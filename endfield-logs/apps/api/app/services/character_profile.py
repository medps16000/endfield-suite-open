"""One shared, character-filtered cohort for every character page statistic.

Membership is resolved by exact character IDs. Select the fastest eligible run
per uploader and canonical dungeon before inspecting its loadout completeness.
Do not replace missing values with another run or infer validity from damage.
"""
from __future__ import annotations

import json
from hashlib import sha256
from functools import lru_cache
from types import SimpleNamespace
from collections import Counter, OrderedDict
from datetime import UTC, datetime, timedelta
from threading import Lock
from time import monotonic

from pydantic import ValidationError
from sqlalchemy import select

from app.core.errors import AppError
from app.db.models import UploadedBattleRecord
from app.db.session import SessionLocal
from app.schemas.character_profile import CharacterProfileResponse, ProfileBoss, ProfileCharacterRanking, ProfileCount, ProfileRanking
from app.schemas.public import BattleRosterEntryResponse
from app.services.public_data import (
    ADMIN_CHARACTER_KEYS, ADMIN_STATISTICS_CHARACTER_KEY, BOSS_SEEDS,
    CHARACTER_STATISTICS_RANGE_DAYS, CRISIS_CONTRACT_BOSS_SLUG,
    RANKING_MIN_PARSER_VERSION, REPO_ROOT, CharacterStatisticsRange,
    _is_inactive_season_tower_stage, _normalize_roster_weapon_refine,
    _parser_version_number, public_data_service,
)


_record = UploadedBattleRecord
_PROFILE_QUERY = select(
    _record.id, _record.uploader_user_id, _record.uploader_nickname,
    _record.boss_slug, _record.boss_key, _record.duration_ms, _record.total_dps,
    _record.total_damage, _record.battle_end_at, _record.parser_version,
    _record.timer_integrity_ok, _record.roster_json, _record.loadout_fallback_used,
).where(_record.status == "valid", _record.visibility == "public", _record.clear_flag.is_(True),
        _record.official_timer_end_seen.is_(True), _record.time_source == "game_timer",
        _record.duration_ms > 0).execution_options(yield_per=250)


@lru_cache(maxsize=1)
def _reference_data():
    characters = json.loads((REPO_ROOT / "apps/web/public/images/character/game-art/manifest.json").read_text(encoding="utf-8"))
    equips = json.loads((REPO_ROOT / "data/local_tables/equip/pieces.json").read_text(encoding="utf-8"))
    weapons = json.loads((REPO_ROOT / "data/local_tables/weapon/manifest.json").read_text(encoding="utf-8"))
    return ({item["id"]: item for item in characters["entries"]},
            {item["itemId"]: item for item in equips["entries"]},
            {item["weaponId"]: item for item in weapons["entries"]})


class _SnapshotChanged(Exception):
    pass


class CharacterProfileService:
    def __init__(self) -> None:
        self._cache: OrderedDict[tuple, tuple[float, CharacterProfileResponse]] = OrderedDict()
        self._lock = Lock()
        self._compute_lock = Lock()
        self._snapshot = None
        self._cohorts = {}
        self._generation = 0

    def invalidate(self) -> None:
        with self._lock:
            self._generation += 1
            self._snapshot = None
            self._cohorts.clear()
            self._cache.clear()

    def _get_snapshot(self):
        # Called under the cold-computation lock. Keep only small metadata and
        # exact membership, not the large inventory/stat dictionaries.
        with self._lock:
            if self._snapshot and self._snapshot[0] > monotonic():
                return self._snapshot
            generation = self._generation
        captured_at = datetime.now(UTC)
        records = []
        with SessionLocal() as session:
            for row in session.execute(_PROFILE_QUERY):
                try:
                    roster = json.loads(row.roster_json)
                    if not isinstance(roster, list):
                        continue
                except (TypeError, ValueError):
                    continue
                members = Counter()
                for item in roster:
                    if isinstance(item, dict) and isinstance(item.get("characterKey"), str):
                        key = item["characterKey"]
                        members[ADMIN_STATISTICS_CHARACTER_KEY if key in ADMIN_CHARACTER_KEYS else key] += 1
                metadata = dict(row._mapping)
                raw = metadata.pop("roster_json")
                records.append((SimpleNamespace(**metadata), tuple(members.items()), sha256(raw.encode()).digest()))
        snapshot = (monotonic() + 60, captured_at, generation, records)
        with self._lock:
            if generation == self._generation:
                self._snapshot = snapshot
                self._cohorts.clear()
        return snapshot

    def _selected_rosters(self, samples):
        expected = {item[1].id: (vars(item[1]), item[3]) for item in samples}
        identities = list(expected)
        result = {}
        if not identities:
            return result
        with SessionLocal() as session:
            for offset in range(0, len(identities), 400):
                statement = _PROFILE_QUERY.where(_record.id.in_(identities[offset:offset + 400]))
                for row in session.execute(statement):
                    metadata = dict(row._mapping)
                    raw = metadata.pop("roster_json")
                    # Backfills or visibility changes must not mix old ranking
                    # metadata with a newer loadout for the same battle ID.
                    if (metadata, sha256(raw.encode()).digest()) != expected[row.id]:
                        raise _SnapshotChanged()
                    result[row.id] = json.loads(raw)
        if result.keys() != expected.keys():
            raise _SnapshotChanged()
        return result

    def get_profile(self, character_key: str, time_range: CharacterStatisticsRange = "all",
                    boss_slug: str | None = None) -> CharacterProfileResponse:
        if character_key in ADMIN_CHARACTER_KEYS:
            character_key = ADMIN_STATISTICS_CHARACTER_KEY
        characters, _, _ = _reference_data()
        if character_key not in characters:
            raise AppError(status_code=404, code="character_not_found", message="未找到对应角色。")
        bosses = {boss.slug: boss for boss in BOSS_SEEDS
                  if boss.slug != CRISIS_CONTRACT_BOSS_SLUG and not _is_inactive_season_tower_stage(boss.slug)}
        if boss_slug and boss_slug not in bosses:
            raise AppError(status_code=404, code="boss_not_found", message="未找到对应竞速副本。")
        key = (character_key, time_range, boss_slug)
        with self._lock:
            cached = self._cache.get(key)
            if cached and cached[0] > monotonic():
                self._cache.move_to_end(key)
                return cached[1]
        # A cached response never queues behind another character's cold scan.
        with self._compute_lock:
            with self._lock:
                cached = self._cache.get(key)
                if cached and cached[0] > monotonic():
                    return cached[1]
            for attempt in range(2):
                try:
                    result, expires_at, generation = self._compute_profile(character_key, time_range, boss_slug, characters, bosses)
                    break
                except _SnapshotChanged:
                    self.invalidate()
                    if attempt:
                        raise AppError(status_code=503, code="profile_updating", message="战斗记录正在更新，请稍后重试。") from None
            with self._lock:
                if generation == self._generation:
                    # Tie response expiry to its shared snapshot, never extend
                    # an almost-expired snapshot for another 60 seconds.
                    self._cache[key] = (expires_at, result)
                    self._cache.move_to_end(key)
                    while len(self._cache) > 64:
                        self._cache.popitem(last=False)
            return result

    def _compute_profile(self, character_key, time_range, boss_slug, characters, bosses):
        expires_at, now, generation, records = self._get_snapshot()
        days = CHARACTER_STATISTICS_RANGE_DAYS[time_range]
        cutoff = now - timedelta(days=days) if days else None
        _, equip_catalog, weapon_catalog = _reference_data()
        cached_cohort = self._cohorts.get(time_range)
        if cached_cohort and cached_cohort[0] is records:
            best_by_character, comparison_by_boss = cached_cohort[1:]
        else:
            best_by_character = {identity: {} for identity in characters}
            comparison_best = {}
            for row, member_counts, digest in records:
                slug = public_data_service._canonical_uploaded_boss_slug(row.boss_slug, row.boss_key)
                if slug not in bosses or not row.uploader_user_id:
                    continue
                parser_version = _parser_version_number(row.parser_version)
                if parser_version < RANKING_MIN_PARSER_VERSION or (parser_version >= 47 and row.timer_integrity_ok is not True):
                    continue
                try:
                    ended = datetime.fromisoformat(row.battle_end_at)
                    if ended.tzinfo is None:
                        ended = ended.replace(tzinfo=UTC)
                    if cutoff and ended < cutoff:
                        continue
                except (TypeError, ValueError):
                    continue
                members = dict(member_counts)
                sort_key = (row.duration_ms, -row.total_dps, -row.total_damage, ended, row.id)
                for identity, count in member_counts:
                    if identity not in characters or count != 1:
                        continue
                    comparison_key = (slug, identity, row.uploader_user_id)
                    if comparison_key not in comparison_best or sort_key < comparison_best[comparison_key][0]:
                        comparison_best[comparison_key] = (sort_key, row.id, row.uploader_nickname)
                    best = best_by_character[identity]
                    cohort_key = (row.uploader_user_id, slug)
                    if cohort_key not in best or sort_key < best[cohort_key][0]:
                        best[cohort_key] = (sort_key, row, members, digest, ended)

            comparison_counts = Counter((slug, identity) for slug, identity, _ in comparison_best)
            comparison_fastest = {}
            for (slug, identity, _), value in comparison_best.items():
                key = (slug, identity)
                if key not in comparison_fastest or value[0] < comparison_fastest[key][0]:
                    comparison_fastest[key] = value
            comparison_by_boss = {}
            for (slug, identity), (sort_key, battle_id, nickname) in comparison_fastest.items():
                comparison_by_boss.setdefault(slug, []).append(ProfileCharacterRanking(
                    rank=0, characterKey=identity, characterName=characters[identity]["name"],
                    sampleCount=comparison_counts[(slug, identity)], bestDurationMs=sort_key[0],
                    battleId=battle_id, accountDisplayName=nickname,
                ))
            for rows in comparison_by_boss.values():
                rows.sort(key=lambda item: (item.bestDurationMs, item.characterKey))
                previous_duration, rank = None, 0
                for index, item in enumerate(rows, start=1):
                    if item.bestDurationMs != previous_duration:
                        rank = index
                        previous_duration = item.bestDurationMs
                    item.rank = rank
            with self._lock:
                if generation == self._generation:
                    self._cohorts[time_range] = (records, best_by_character, comparison_by_boss)
        best = best_by_character[character_key]
        options_count = Counter(slug for _, slug in best)
        samples = [value for (_, slug), value in best.items() if not boss_slug or slug == boss_slug]
        samples.sort(key=lambda value: value[0])
        total = len(samples)
        roster_payloads = self._selected_rosters(samples)
        potential_counts, refine_counts, combos = Counter(), Counter(), Counter()
        teammate_counts, weapon_counts, equip_counts = Counter(), Counter(), Counter()
        weapon_labels, equip_labels = {}, {}
        rankings = {}
        unknown_equipment = 0
        for _, row, roster, _, ended in samples:
            own = next(item for item in roster_payloads[row.id] if isinstance(item, dict) and isinstance(item.get("characterKey"), str)
                       and (ADMIN_STATISTICS_CHARACTER_KEY if item.get("characterKey") in ADMIN_CHARACTER_KEYS
                            else item.get("characterKey")) == character_key)
            potential = own.get("characterPotential")
            if type(potential) is not int or not 0 <= potential <= 5 or row.loadout_fallback_used:
                potential = None
            refinement = None
            weapon = None
            equips = []
            if not row.loadout_fallback_used:
                try:
                    parsed = _normalize_roster_weapon_refine([BattleRosterEntryResponse.model_validate(own)])[0]
                    weapon, equips = parsed.weapon, parsed.equips
                    raw = own.get("weapon", {}).get("weaponRefine") if isinstance(own.get("weapon"), dict) else None
                    if weapon and (raw is None or type(raw) is int and 0 <= raw <= 5):
                        refinement = weapon.weaponRefine
                except (ValidationError, TypeError, ValueError):
                    pass
            potential_counts[potential] += 1
            refine_counts[refinement] += 1
            combos[(potential, refinement)] += 1
            for teammate, entries in roster.items():
                if teammate != character_key and teammate in characters and entries == 1:
                    teammate_counts[teammate] += 1
            weapon_key = weapon.weaponTemplate if weapon and weapon.weaponTemplate else "unknown"
            weapon_counts[weapon_key] += 1
            weapon_labels[weapon_key] = (weapon.weaponName, weapon.iconUrl) if weapon_key != "unknown" else ("未知武器", None)
            if weapon_key in weapon_catalog:
                official_weapon = weapon_catalog[weapon_key]
                weapon_labels[weapon_key] = (official_weapon["name"], official_weapon.get("icon"))
            # Count presence per sample; duplicate accessories never count twice.
            equip_ids = set()
            for equip in equips:
                if equip.itemId:
                    equip_ids.add(equip.itemId)
                    equip_labels[equip.itemId] = (equip.pieceName, equip.iconUrl)
                    if equip.itemId in equip_catalog:
                        official_equip = equip_catalog[equip.itemId]
                        equip_labels[equip.itemId] = (official_equip["name"], official_equip.get("icon"))
            equip_counts.update(equip_ids)
            if len({equip.slot for equip in equips}) < 4 or any(not equip.itemId for equip in equips):
                unknown_equipment += 1
            slug = public_data_service._canonical_uploaded_boss_slug(row.boss_slug, row.boss_key)
            rows = rankings.setdefault(slug, [])
            rows.append(ProfileRanking(rank=len(rows) + 1, battleId=row.id, accountId=row.uploader_user_id,
                accountDisplayName=row.uploader_nickname, durationMs=row.duration_ms,
                battleEndAt=ended.isoformat(), potential=potential, refinement=refinement))

        def counts(counter, labels, keys=None):
            ordered = keys if keys is not None else sorted(counter, key=lambda item: (-counter[item], str(item)))
            return [ProfileCount(key="unknown" if item is None else str(item), name=labels[item][0],
                iconUrl=labels[item][1], count=counter[item], percent=round(counter[item] * 100 / total, 2) if total else 0)
                for item in ordered]

        potential_labels = {value: (f"{value} 潜", None) for value in range(6)} | {None: ("未知", None)}
        refine_labels = {value: (f"精炼 {value}", None) for value in range(1, 7)} | {None: ("未知", None)}
        combo_labels = {(p, r): (f"{p if p is not None else '未知'} + {r if r is not None else '未知'}", None) for p, r in combos}
        result_bosses = [ProfileBoss(bossSlug=slug, bossName=bosses[slug].name, dungeonName=bosses[slug].dungeon_name,
            sampleCount=len(rows), bestDurationMs=rows[0].durationMs, rows=rows[:20], characterRows=comparison_by_boss[slug],
            rankedCharacterCount=len(comparison_by_boss[slug]),
            characterRank=next(item.rank for item in comparison_by_boss[slug] if item.characterKey == character_key),
        ) for slug, rows in rankings.items()]
        result_bosses.sort(key=lambda item: (-item.sampleCount, item.bossSlug))
        result = CharacterProfileResponse(characterKey=character_key, range=time_range, bossSlug=boss_slug,
            generatedAt=now.isoformat(), latestBattleAt=max((value[4] for value in samples), default=None).isoformat() if samples else None,
            sampleCount=total, accountCount=len({value[1].uploader_user_id for value in samples}), bossCount=len(rankings),
            potentials=counts(potential_counts, potential_labels, list(range(6)) + [None]),
            refinements=counts(refine_counts, refine_labels, list(range(1, 7)) + [None]),
            combinations=counts(combos, combo_labels),
            teammates=counts(teammate_counts, {key: (characters[key]["name"], None) for key in teammate_counts}),
            weapons=counts(weapon_counts, weapon_labels), equipment=counts(equip_counts, equip_labels),
            equipmentUnknownSamples=unknown_equipment, bosses=result_bosses,
            bossOptions=[ProfileBoss(bossSlug=slug, bossName=bosses[slug].name, dungeonName=bosses[slug].dungeon_name,
                sampleCount=options_count[slug]) for slug in bosses])
        return result, expires_at, generation


character_profile_service = CharacterProfileService()
