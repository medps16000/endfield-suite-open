from __future__ import annotations

import json
import logging
import re
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from re import sub
from time import monotonic
from typing import Any, Literal
from unicodedata import normalize
from urllib.parse import urlsplit
from uuid import uuid4

from sqlalchemy import or_, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import load_only
from parser_core.timer_evidence import validate_timer_evidence

from app.core.errors import AppError
from app.db.models import UploadedBattleRecord
from app.db.session import SessionLocal
from app.schemas.public import (
    BattleDetailResponse,
    BattleRosterEquipResponse,
    BattleIntegrityResponse,
    BattleParticipantResponse,
    BattleResponse,
    BattleRosterEntryResponse,
    BattleRosterSkillResponse,
    BattleRosterWeaponResponse,
    BattleRosterWeaponSkillResponse,
    BossCharacterStatisticsResponse,
    BossCharacterStatisticsOutlier,
    BossCharacterStatisticsRow,
    BossProfessionGroup,
    BossProfessionUsageEntry,
    BossRankingResponse,
    BossRankingRosterEntry,
    BossRankingRow,
    CharacterBossStatisticsResponse,
    CharacterBossStatisticsRow,
    ContractTagResponse,
    HotBossCard,
    HotBossRun,
    PublicAccountSearchEntryResponse,
    PublicAccountSearchResponse,
    PublicUserRankingsResponse,
    RoleSkillStatResponse,
    ShareSummaryResponse,
    TimelineEventResponse,
    UserBattlesResponse,
    UserBattleSummaryResponse,
    UserBossRankingResponse,
    ViewerCapabilitiesResponse,
)
from app.schemas.uploader import UploadBattleRequest
from app.services.integrity import build_battle_integrity
from parser_core.battle_log_parser import (
    _CURRENT_TYPED_RESOURCE_BINDING,
    _canonical_num_table_buff_number,
    _canonical_num_table_skill_id,
    _extract_character_key,
    _humanize_buff_damage_name,
    _infer_skill_damage_element,
    _infer_skill_damage_school,
    _infer_verified_weapon_refine_from_levels,
    _resolve_skill_name,
    _runtime_skill_number,
)

Metric = Literal["dps", "rdps"]
CharacterStatisticsRange = Literal["7d", "14d", "30d", "all"]
CharacterStatisticsPotential = Literal["0", "1-5", "all"]
PROFESSION_ORDER = ("近卫", "重装", "辅助", "突击", "术士", "先锋")
REPO_ROOT = Path(__file__).resolve().parents[4]
CHARACTER_MANIFEST_PATH = REPO_ROOT / "data" / "akedata" / "character" / "manifest.json"
LOCAL_CHARACTER_MANIFEST_PATH = REPO_ROOT / "data" / "local_tables" / "character" / "manifest.json"
CONTRACT_TAG_CATALOG_PATH = REPO_ROOT / "data" / "public" / "contract_tags.json"
WEB_PUBLIC_ROOT = REPO_ROOT / "apps" / "web" / "public"
AKEDATA_ASSET_HOSTS = {"www.akedata.top", "akedata.top"}
HOT_BOSSES_CACHE_TTL_SECONDS = 30.0
# TTL backstop for the uploaded-battle list cache: writers are expected to call
# _invalidate_public_caches(), the TTL only covers writers outside this service.
BATTLE_LIST_CACHE_TTL_SECONDS = 30.0
CHARACTER_STATISTICS_CACHE_TTL_SECONDS = 300.0
CHARACTER_BOSS_STATISTICS_CACHE_TTL_SECONDS = 60.0
PUBLIC_ACCOUNT_DIRECTORY_CACHE_TTL_SECONDS = 60.0
PHYSICAL_CRUSH_TRIGGER_DAMAGE_BUFF = "buff_common_cryst_triggered_physical_break"

# 榜单准入的最低解析器版本：v25 起 rDPS 过白名单，数值才可信（docs/rdps_correctness_plan.md）。
# 更早版本的记录保留可查，但不参与排名。
RANKING_MIN_PARSER_VERSION = 25
# These versions were accepted against effect text, packet identities and the
# whitelist algorithm suite. They do not assert complete native reconstruction.
RDPS_ALGORITHM_MIN_PARSER_VERSION = 51
RDPS_ALGORITHM_MIN_RULES_VERSION = 44
_PARSER_VERSION_RE = re.compile(r"v(\d+)\s*$", re.IGNORECASE)

# Boss 实际击杀校验（第三道榜单准入）。clear_flag（boss HP 归零）与 official_timer_end_seen
# （游戏 isPass=1）在 boss rush 场景都可能误判：只杀部分 boss、中途异常结算会以极低伤害“通关”
# （实测 dung01_group_bossrush01 有 8.16s/157k 伤害=boss 血量 6.9% 的假通关排到榜首）。
# 判据：固定血量 boss 的正常通关伤害紧密聚集在血量附近（p25/median≈1）；伤害显著低于中位数=没真打死。
# 只对样本充足(n>=15)且伤害紧密聚集(p25/median>=0.95=固定血量特征)的 boss 套用伤害下限，
# 剔除伤害不足中位数 60% 的记录；伤害离散的 boss(unknown-dungeon 混桶/多目标)不套用，避免误伤。
RANKING_DAMAGE_GATE_MIN_SAMPLES = 15
RANKING_DAMAGE_GATE_MIN_CLUSTERING = 0.95
RANKING_CLEAR_DAMAGE_MIN_FRACTION = 0.60
CHARACTER_STATISTICS_MIN_SAMPLES = 5
CHARACTER_STATISTICS_LOCAL_OUTLIER_IQR_MULTIPLIER = 1.5
CHARACTER_STATISTICS_GLOBAL_OUTLIER_IQR_MULTIPLIER = 3.0
CHARACTER_STATISTICS_RANGE_DAYS: dict[CharacterStatisticsRange, int | None] = {
    "7d": 7,
    "14d": 14,
    "30d": 30,
    "all": None,
}
ADMIN_CHARACTER_KEYS = frozenset({"chr_0002_endminm", "chr_0003_endminf", "chr_9000_endmin"})
ADMIN_STATISTICS_CHARACTER_KEY = "chr_9000_endmin"


def _parser_version_number(parser_version: str | None) -> int:
    match = _PARSER_VERSION_RE.search(str(parser_version or ""))
    return int(match.group(1)) if match else 0


def _rdps_ranking_eligible(
    parser_version: str | None, rules_version: str | None, strict_ok: bool | None,
) -> bool:
    """rDPS algorithm eligibility; normal battle ranking gates still apply."""
    parser_number = _parser_version_number(parser_version)
    if parser_number < RANKING_MIN_PARSER_VERSION:
        return False
    return strict_ok is True or (
        parser_number >= RDPS_ALGORITHM_MIN_PARSER_VERSION
        and _parser_version_number(rules_version) >= RDPS_ALGORITHM_MIN_RULES_VERSION
    )


def _linear_percentile(sorted_values: list[float], q: float) -> float:
    """线性插值分位数，语义对齐 postgres percentile_cont。sorted_values 需已升序且非空。"""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = q * (len(sorted_values) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def _iqr_bounds(sorted_values: list[float], multiplier: float) -> tuple[float, float] | None:
    """返回 Tukey IQR 边界；样本太少或 IQR 为 0 时不武断制造离群点。"""
    if len(sorted_values) < 4:
        return None
    q1 = _linear_percentile(sorted_values, 0.25)
    q3 = _linear_percentile(sorted_values, 0.75)
    iqr = q3 - q1
    if iqr <= 0:
        return None
    return q1 - multiplier * iqr, q3 + multiplier * iqr


@dataclass(frozen=True)
class CharacterCatalogEntry:
    char_id: str
    name: str
    profession: str
    element: str | None
    avatar_url: str | None
    rarity: int | None = None


def _normalize_skill_display_name(skill_name: str, skill_key: str | None = None) -> str:
    if skill_key == PHYSICAL_CRUSH_TRIGGER_DAMAGE_BUFF:
        return "猛击"
    candidate = skill_name or skill_key or ""
    # Repair missing labels at read time, without replacing an existing specific
    # display name or inferring a skill family from an internal ID's spelling.
    generic_labels = {"普攻", "普通攻击", "战技", "连携", "连携技", "终结技", "技能"}
    if candidate.strip() not in generic_labels and not candidate.startswith(
        ("buff_", "chr_", "eny_", "skill_", "common_", "abilityentity_")
    ):
        return candidate
    if candidate.startswith("buff_"):
        # Some damage events are BuffData roots with an explicitly reviewed
        # creation path to a named skill. Resolve that exact display identity.
        resolved = _resolve_skill_name(candidate)
        if resolved and resolved != candidate:
            return resolved
        return _humanize_buff_damage_name(candidate) or candidate
    if candidate.startswith(("chr_", "eny_", "skill_")):
        resolved = _resolve_skill_name(candidate)
        if resolved and resolved != candidate:
            return resolved
        character_key = _extract_character_key(candidate)
        buff_candidate = _canonical_num_table_buff_number(_runtime_skill_number(candidate), character_key)
        if buff_candidate:
            buff_name = _humanize_buff_damage_name(buff_candidate)
            if buff_name:
                return buff_name
        canonical_skill = _canonical_num_table_skill_id(candidate, character_key)
        if canonical_skill:
            resolved_canonical = _resolve_skill_name(canonical_skill)
            if resolved_canonical and resolved_canonical != canonical_skill:
                return resolved_canonical
    if skill_key:
        resolved_key = _resolve_skill_name(skill_key)
        if resolved_key and resolved_key != skill_key:
            return resolved_key
    return candidate


def _local_public_image_url(candidate: str) -> str | None:
    if candidate.startswith("/public/images/"):
        local_candidate = candidate.removeprefix("/public")
    elif candidate.startswith("/images/"):
        local_candidate = candidate
    else:
        return None
    if (WEB_PUBLIC_ROOT / local_candidate.removeprefix("/")).exists():
        return local_candidate
    return None


def _normalize_public_asset_url(asset_url: str | None) -> str | None:
    if not asset_url:
        return None
    candidate = str(asset_url).strip()
    if not candidate:
        return None
    if candidate.startswith(("/images/", "/public/images/")):
        return _local_public_image_url(candidate)
    if candidate.startswith("http://") or candidate.startswith("https://"):
        parsed = urlsplit(candidate)
        if parsed.netloc.lower() in AKEDATA_ASSET_HOSTS and parsed.path.startswith("/public/images/"):
            return _local_public_image_url(parsed.path)
    return candidate


def _weapon_icon_url(asset_url: str | None, weapon_template: str | None) -> str | None:
    normalized = _normalize_public_asset_url(asset_url)
    if normalized:
        return normalized
    template = str(weapon_template or "").strip()
    if not template:
        return None
    return _local_public_image_url(f"/images/weapon/icon/{template}.png")


def _looks_like_raw_enemy_key(value: str | None) -> bool:
    return bool(value and str(value).strip().startswith("eny_"))


logger = logging.getLogger(__name__)

# Mirrors parser_core.battle_log_parser UNKNOWN_DUNGEON_KEY/NAME — the fallback
# the parser emits when a battle's dungeon could not be identified.
UNKNOWN_DUNGEON_KEY = "unknown_dungeon"
UNKNOWN_DUNGEON_NAME = "未知副本"


def _is_registrable_boss_identity(boss_slug: str, boss_name: str, dungeon_name: str) -> bool:
    """Reject catalog auto-registration for entries the parser failed to identify.

    A mis-parsed upload would otherwise permanently create a garbage boss entry
    (raw enemy key / 未知副本) in the public catalog. The battle itself is still
    stored and viewable; it just does not create a rankings category.
    """
    slug = (boss_slug or "").strip()
    if not slug or slug == UNKNOWN_DUNGEON_KEY:
        return False
    boss_display = (boss_name or "").strip()
    dungeon_display = (dungeon_name or "").strip()
    boss_usable = bool(boss_display) and not _looks_like_raw_enemy_key(boss_display) and boss_display != UNKNOWN_DUNGEON_NAME
    dungeon_usable = bool(dungeon_display) and dungeon_display != UNKNOWN_DUNGEON_NAME
    return boss_usable or dungeon_usable


def _build_rdps_contribution_responses(
    contributions: object,
) -> list[TimelineEventResponse.RdpsContributionResponse]:
    result: list[TimelineEventResponse.RdpsContributionResponse] = []
    if not isinstance(contributions, list):
        return result
    for contribution in contributions:
        if hasattr(contribution, "model_dump"):
            payload = contribution.model_dump(mode="json")
        else:
            payload = contribution
        result.append(TimelineEventResponse.RdpsContributionResponse.model_validate(payload))
    return result


def _build_poise_damage_response(poise_damage: object) -> TimelineEventResponse.PoiseDamageResponse | None:
    payload = _model_payload(poise_damage)
    if not payload:
        return None
    return TimelineEventResponse.PoiseDamageResponse.model_validate(payload)


def _model_payload(value: object) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value if isinstance(value, dict) else {}


def _build_character_states_from_detail(
    roster: list[BattleRosterEntryResponse],
    timeline_events: list[TimelineEventResponse],
) -> list[dict[str, Any]]:
    first_baseline_by_character: dict[str, dict[str, Any]] = {}
    for event in timeline_events:
        if event.laneType != "skill":
            continue
        character_key = str(event.sourceCharacterKey or "")
        if not character_key or character_key in first_baseline_by_character:
            continue
        hit_context = event.hitContext if isinstance(event.hitContext, dict) else {}
        baseline = hit_context.get("baseline") if isinstance(hit_context.get("baseline"), dict) else {}
        attrs = baseline.get("attrs") if isinstance(baseline.get("attrs"), list) else []
        if attrs:
            first_baseline_by_character[character_key] = {
                "tsMsFromStart": event.tsMsFromStart,
                "attrs": attrs,
            }

    states: list[dict[str, Any]] = []
    for roster_entry in roster:
        character_key = str(roster_entry.characterKey or "")
        buffs_received: list[dict[str, Any]] = []
        buffs_given: list[dict[str, Any]] = []
        debuffs_applied: list[dict[str, Any]] = []
        for event in timeline_events:
            if event.laneType != "buff":
                continue
            row = {
                "eventKey": event.eventKey,
                "eventName": event.eventName,
                "sourceCharacterKey": event.sourceCharacterKey,
                "sourceCharacterName": event.sourceCharacterName,
                "targetCharacterKey": event.targetCharacterKey,
                "targetCharacterName": event.targetCharacterName,
                "targetPlayerKey": event.targetPlayerKey,
                "targetEnemyKey": event.targetEnemyKey,
                "startTsMsFromStart": event.tsMsFromStart,
                "durationMs": event.durationMs,
                "effects": [_model_payload(effect) for effect in event.effects],
                "dynamicEffects": [_model_payload(effect) for effect in event.dynamicEffects],
            }
            if event.targetPlayerKey == character_key:
                buffs_received.append(row)
            if event.sourceCharacterKey == character_key and event.targetPlayerKey and event.targetPlayerKey != character_key:
                buffs_given.append(row)
            if event.sourceCharacterKey == character_key and event.targetEnemyKey:
                debuffs_applied.append(row)

        states.append(
            {
                "characterKey": character_key,
                "characterName": roster_entry.characterName,
                "loadout": {
                    "characterLevel": roster_entry.characterLevel,
                    "characterPotential": roster_entry.characterPotential,
                    "weapon": _model_payload(roster_entry.weapon) if roster_entry.weapon else None,
                    "equips": [_model_payload(equip) for equip in roster_entry.equips],
                },
                "initialBaseline": first_baseline_by_character.get(
                    character_key,
                    {"tsMsFromStart": None, "attrs": []},
                ),
                "buffsReceived": buffs_received,
                "buffsGiven": buffs_given,
                "debuffsApplied": debuffs_applied,
            }
        )
    return states


def _first_string_value(payload: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _enrich_timeline_event_damage_identity(event: TimelineEventResponse) -> TimelineEventResponse:
    if event.laneType != "skill":
        return event

    hit_context = event.hitContext if isinstance(event.hitContext, dict) else {}
    inferred_character_key = event.sourceCharacterKey or _extract_character_key(event.eventKey)
    if event.eventKey == PHYSICAL_CRUSH_TRIGGER_DAMAGE_BUFF:
        return event.model_copy(
            update={
                "eventName": "猛击",
                "damageElement": "physical",
                "damageSchool": event.damageSchool or "physical",
            }
        )
    event_name = _normalize_skill_display_name(event.eventName, event.eventKey)
    damage_element = (
        event.damageElement
        or _first_string_value(hit_context, ("damage_element", "damageElement", "element"))
        or _infer_skill_damage_element(event.eventKey, inferred_character_key)
    )
    damage_school = (
        event.damageSchool
        or _first_string_value(hit_context, ("damage_school", "damageSchool", "school"))
        or _infer_skill_damage_school(event.eventKey, inferred_character_key)
    )

    if (
        event_name == event.eventName
        and damage_element == event.damageElement
        and damage_school == event.damageSchool
    ):
        return event
    return event.model_copy(
        update={"eventName": event_name, "damageElement": damage_element, "damageSchool": damage_school}
    )


def _enrich_timeline_event_damage_identities(
    timeline_events: list[TimelineEventResponse],
) -> list[TimelineEventResponse]:
    return [_enrich_timeline_event_damage_identity(event) for event in timeline_events]


def _dump_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _load_contract_tag_catalog() -> dict[int, dict[str, Any]]:
    try:
        payload = json.loads(CONTRACT_TAG_CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    tags = payload.get("tags") if isinstance(payload, dict) else None
    if not isinstance(tags, dict):
        return {}
    catalog: dict[int, dict[str, Any]] = {}
    for key, item in tags.items():
        if not isinstance(item, dict):
            continue
        try:
            tag_id = int(item.get("tagId") or key)
        except (TypeError, ValueError):
            continue
        catalog[tag_id] = item
    return catalog


def _contract_tag_catalog_entry(tag_id: int) -> dict[str, Any]:
    if not hasattr(_contract_tag_catalog_entry, "_cache"):
        _contract_tag_catalog_entry._cache = _load_contract_tag_catalog()  # type: ignore[attr-defined]
    cache = _contract_tag_catalog_entry._cache  # type: ignore[attr-defined]
    return cache.get(tag_id, {}) if isinstance(cache, dict) else {}


def _enrich_contract_tag(tag: ContractTagResponse) -> ContractTagResponse:
    catalog_entry = _contract_tag_catalog_entry(tag.tagId)
    icon_id = tag.iconId or str(catalog_entry.get("iconId") or "").strip() or None
    if "iconUrl" in catalog_entry:
        icon_url = _normalize_public_asset_url(str(catalog_entry.get("iconUrl") or "").strip() or None)
    else:
        icon_url = _normalize_public_asset_url(tag.iconUrl)
    name = tag.name or str(catalog_entry.get("name") or "").strip() or None
    description = tag.description or str(catalog_entry.get("description") or "").strip() or None
    score = tag.score
    if score <= 0:
        try:
            score = max(int(catalog_entry.get("score") or 0), 0)
        except (TypeError, ValueError):
            score = tag.score
    if (
        icon_id == tag.iconId
        and icon_url == tag.iconUrl
        and name == tag.name
        and description == tag.description
        and score == tag.score
    ):
        return tag
    return tag.model_copy(
        update={
            "iconId": icon_id,
            "iconUrl": icon_url,
            "name": name,
            "description": description,
            "score": score,
        }
    )


def _load_contract_tags(raw_json: str | None) -> list[ContractTagResponse]:
    if not raw_json:
        return []
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    tags: list[ContractTagResponse] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        try:
            tags.append(_enrich_contract_tag(ContractTagResponse.model_validate(item)))
        except ValueError:
            continue
    return tags


def _normalize_contract_tags(tags: object) -> list[ContractTagResponse]:
    if not isinstance(tags, list):
        return []
    normalized: list[ContractTagResponse] = []
    for item in tags:
        try:
            payload = item.model_dump(mode="json") if hasattr(item, "model_dump") else item
            normalized.append(_enrich_contract_tag(ContractTagResponse.model_validate(payload)))
        except ValueError:
            continue
    return normalized


def _contract_tag_score(score: int | None, tags: list[ContractTagResponse]) -> int | None:
    if tags:
        return sum(max(int(tag.score), 0) for tag in tags)
    if score is not None:
        return max(int(score), 0)
    return None


def _dump_contract_tags(tags: list[ContractTagResponse]) -> str | None:
    if not tags:
        return None
    return _dump_json([tag.model_dump(mode="json") for tag in tags])


def _contract_fields_for_boss(
    boss_slug: str | None,
    score: int | None,
    tags: list[ContractTagResponse],
) -> tuple[int | None, list[ContractTagResponse]]:
    if boss_slug != CRISIS_CONTRACT_BOSS_SLUG:
        return None, []
    return score, tags


def _visible_contract_tag_score(battle: DemoBattle) -> int | None:
    return battle.contract_tag_score if battle.boss_slug == CRISIS_CONTRACT_BOSS_SLUG else None


def _visible_contract_tags(battle: DemoBattle) -> list[ContractTagResponse]:
    return battle.contract_tags if battle.boss_slug == CRISIS_CONTRACT_BOSS_SLUG else []


def _normalize_profession_name(profession: str | None) -> str:
    if not profession:
        return "未归类"
    if profession == "术师":
        return "术士"
    return profession


def _normalize_character_element(value: str | None) -> str | None:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return None
    aliases = {
        "物理": "physical",
        "physical": "physical",
        "灼热": "fire",
        "火": "fire",
        "fire": "fire",
        "寒冷": "cryst",
        "冰": "cryst",
        "cryst": "cryst",
        "crystal": "cryst",
        "电磁": "pulse",
        "电弧": "pulse",
        "pulse": "pulse",
        "自然": "natural",
        "natural": "natural",
        "法术": "spell",
        "spell": "spell",
    }
    return aliases.get(normalized)


def _load_character_catalog() -> tuple[dict[str, CharacterCatalogEntry], dict[str, CharacterCatalogEntry]]:
    # The public source tree does not redistribute generated game resources.
    # An empty catalog keeps authentication and synthetic API workflows usable;
    # when the resource is supplied its ordinary parsing/validation still runs.
    manifest = json.loads(CHARACTER_MANIFEST_PATH.read_text(encoding="utf-8")) if CHARACTER_MANIFEST_PATH.is_file() else []
    avatar_manifest_path = WEB_PUBLIC_ROOT / "images/character/game-avatars/manifest.json"
    avatars: dict = {}
    try:
        avatar_manifest = json.loads(avatar_manifest_path.read_text("utf-8"))
        if isinstance(avatar_manifest, dict) and isinstance(avatar_manifest.get("avatars"), dict):
            avatars = avatar_manifest["avatars"]
    except (OSError, ValueError):
        pass
    by_id: dict[str, CharacterCatalogEntry] = {}
    by_name: dict[str, CharacterCatalogEntry] = {}
    for item in manifest:
        char_id = str(item.get("charId"))
        avatar_key = ADMIN_STATISTICS_CHARACTER_KEY if char_id in ADMIN_CHARACTER_KEYS else char_id
        official_avatar = avatars.get(avatar_key) or {}
        entry = CharacterCatalogEntry(
            char_id=char_id,
            name=str(item.get("name")),
            profession=_normalize_profession_name(item.get("profession")),
            element=_normalize_character_element(item.get("charType")),
            avatar_url=_normalize_public_asset_url(official_avatar.get("url")) or _normalize_public_asset_url(item.get("icon")),
            rarity=int(item["rarity"]) if item.get("rarity") is not None else None,
        )
        by_id[entry.char_id] = entry
        by_name[entry.name] = entry

    if LOCAL_CHARACTER_MANIFEST_PATH.exists():
        local_manifest = json.loads(LOCAL_CHARACTER_MANIFEST_PATH.read_text(encoding="utf-8"))
        for item in local_manifest.get("entries", []):
            if not isinstance(item, dict):
                continue
            char_id = str(item.get("charId") or item.get("id") or "")
            name = str(item.get("name") or "")
            if not char_id or not name:
                continue
            old_entry = by_id.get(char_id)
            avatar_key = ADMIN_STATISTICS_CHARACTER_KEY if char_id in ADMIN_CHARACTER_KEYS else char_id
            official_avatar = avatars.get(avatar_key) or {}
            entry = CharacterCatalogEntry(
                char_id=char_id,
                name=name,
                profession=_normalize_profession_name(item.get("professionName"))
                or (old_entry.profession if old_entry else None),
                element=_normalize_character_element(item.get("charTypeName") or item.get("charTypeId"))
                or (old_entry.element if old_entry else None),
                avatar_url=_normalize_public_asset_url(official_avatar.get("url")) or _normalize_public_asset_url(item.get("icon"))
                or (old_entry.avatar_url if old_entry else None),
                rarity=(
                    int(item["rarity"])
                    if item.get("rarity") is not None
                    else (old_entry.rarity if old_entry else None)
                ),
            )
            if old_entry is not None:
                by_name.pop(old_entry.name, None)
            by_id[entry.char_id] = entry
            by_name[entry.name] = entry
    return by_id, by_name


CHARACTER_CATALOG_BY_ID, CHARACTER_CATALOG_BY_NAME = _load_character_catalog()


def _statistics_character_key(entry: CharacterCatalogEntry) -> str:
    if entry.char_id in ADMIN_CHARACTER_KEYS or entry.name == "管理员":
        return ADMIN_STATISTICS_CHARACTER_KEY
    return entry.char_id


def _load_six_star_statistics_catalog() -> tuple[CharacterCatalogEntry, ...]:
    collapsed: dict[str, CharacterCatalogEntry] = {}
    for entry in CHARACTER_CATALOG_BY_ID.values():
        if entry.rarity != 6:
            continue
        character_key = _statistics_character_key(entry)
        current = collapsed.get(character_key)
        if character_key == ADMIN_STATISTICS_CHARACTER_KEY:
            # 三个管理员资源 ID 合并为一个统计角色；优先使用带头像的男管理员资源。
            if current is not None and entry.char_id != "chr_0002_endminm":
                continue
            entry = CharacterCatalogEntry(
                char_id=character_key,
                name="管理员",
                profession=entry.profession,
                element=entry.element,
                avatar_url=entry.avatar_url,
                rarity=6,
            )
        collapsed[character_key] = entry
    profession_order = {name: index for index, name in enumerate(PROFESSION_ORDER)}
    return tuple(
        sorted(
            collapsed.values(),
            key=lambda item: (profession_order.get(item.profession, len(PROFESSION_ORDER)), item.name, item.char_id),
        )
    )


SIX_STAR_STATISTICS_CATALOG = _load_six_star_statistics_catalog()


def _get_character_entry(character_name: str | None = None, character_key: str | None = None) -> CharacterCatalogEntry | None:
    if character_key:
        entry = CHARACTER_CATALOG_BY_ID.get(character_key)
        if entry is not None:
            return entry
    if character_name:
        normalized_name = str(character_name).strip()
        return CHARACTER_CATALOG_BY_NAME.get(normalized_name) or CHARACTER_CATALOG_BY_ID.get(normalized_name)
    return None


def _get_character_display_name(character_name: str | None = None, character_key: str | None = None) -> str:
    entry = _get_character_entry(character_name, character_key)
    if entry is not None:
        return entry.name
    return str(character_name or character_key or "")


def _get_character_profession(character_name: str | None = None, character_key: str | None = None) -> str:
    entry = _get_character_entry(character_name, character_key)
    return entry.profession if entry else "未归类"


def _get_enriched_character_profession(
    current_profession: str | None,
    character_name: str | None = None,
    character_key: str | None = None,
) -> str:
    if current_profession and current_profession != "未归类":
        return current_profession
    return _get_character_profession(character_name, character_key)


def _get_character_avatar_url(character_name: str | None = None, character_key: str | None = None) -> str | None:
    entry = _get_character_entry(character_name, character_key)
    return entry.avatar_url if entry else None


def _get_character_element(character_name: str | None = None, character_key: str | None = None) -> str | None:
    entry = _get_character_entry(character_name, character_key)
    return entry.element if entry else None


def _enrich_roster_entry_character_identity(entry: BattleRosterEntryResponse) -> BattleRosterEntryResponse:
    display_name = _get_character_display_name(entry.characterName, entry.characterKey)
    profession = _get_enriched_character_profession(entry.characterProfession, display_name, entry.characterKey)
    avatar_url = entry.characterAvatarUrl or _get_character_avatar_url(display_name, entry.characterKey)
    element = entry.characterElement or _get_character_element(display_name, entry.characterKey)
    updates: dict[str, Any] = {}
    if entry.weapon is not None:
        weapon_icon_url = _weapon_icon_url(entry.weapon.iconUrl, entry.weapon.weaponTemplate)
        if weapon_icon_url != entry.weapon.iconUrl:
            updates["weapon"] = entry.weapon.model_copy(update={"iconUrl": weapon_icon_url})
    if display_name and display_name != entry.characterName:
        updates["characterName"] = display_name
    if profession != entry.characterProfession:
        updates["characterProfession"] = profession
    if avatar_url != entry.characterAvatarUrl:
        updates["characterAvatarUrl"] = avatar_url
    if element != entry.characterElement:
        updates["characterElement"] = element
    if not updates:
        return entry
    return entry.model_copy(update=updates)


def _enrich_participant_character_identity(entry: BattleParticipantResponse) -> BattleParticipantResponse:
    display_name = _get_character_display_name(entry.characterName, entry.characterKey)
    profession = _get_enriched_character_profession(entry.characterProfession, display_name, entry.characterKey)
    avatar_url = entry.characterAvatarUrl or _get_character_avatar_url(display_name, entry.characterKey)
    element = entry.characterElement or _get_character_element(display_name, entry.characterKey)
    updates: dict[str, Any] = {}
    if display_name and display_name != entry.characterName:
        updates["characterName"] = display_name
    if profession != entry.characterProfession:
        updates["characterProfession"] = profession
    if avatar_url != entry.characterAvatarUrl:
        updates["characterAvatarUrl"] = avatar_url
    if element != entry.characterElement:
        updates["characterElement"] = element
    if not updates:
        return entry
    return entry.model_copy(update=updates)


def _normalize_role_skill_stat_character_identity(entry: RoleSkillStatResponse) -> RoleSkillStatResponse:
    display_name = _get_character_display_name(entry.characterName)
    if display_name == entry.characterName:
        return entry
    return entry.model_copy(update={"characterName": display_name})


def _normalize_timeline_event_character_identity(event: TimelineEventResponse) -> TimelineEventResponse:
    updates: dict[str, Any] = {}
    if event.sourceCharacterKey or event.sourceCharacterName:
        source_name = _get_character_display_name(event.sourceCharacterName, event.sourceCharacterKey)
        if source_name and source_name != event.sourceCharacterName:
            updates["sourceCharacterName"] = source_name
    target_key = event.targetPlayerKey or (
        event.targetCharacterKey if str(event.targetCharacterKey or "").startswith("chr_") else None
    )
    if target_key or event.targetCharacterName:
        target_name = _get_character_display_name(event.targetCharacterName, target_key)
        if target_name and target_name != event.targetCharacterName:
            updates["targetCharacterName"] = target_name
    if event.rdpsContributions:
        contributions: list[TimelineEventResponse.RdpsContributionResponse] = []
        changed = False
        for contribution in event.rdpsContributions:
            contribution_name = _get_character_display_name(contribution.characterName, contribution.characterKey)
            if contribution_name != contribution.characterName:
                changed = True
                contributions.append(contribution.model_copy(update={"characterName": contribution_name}))
            else:
                contributions.append(contribution)
        if changed:
            updates["rdpsContributions"] = contributions
    if not updates:
        return event
    return event.model_copy(update=updates)


def _get_character_name(character_key: str) -> str:
    entry = _get_character_entry(character_key=character_key)
    return entry.name if entry else character_key


def _display_weapon_refine(value: int | None) -> int | None:
    if value is None:
        return None
    return max(1, min(6, value + 1))


def _normalize_roster_weapon_refine(
    roster: list[BattleRosterEntryResponse],
) -> list[BattleRosterEntryResponse]:
    normalized: list[BattleRosterEntryResponse] = []
    for entry in roster:
        if entry.weapon is None:
            normalized.append(entry)
            continue
        raw_refine = entry.weapon.weaponRefine
        if raw_refine in (None, 0):
            verified = _infer_verified_weapon_refine_from_levels(
                entry.weapon.weaponTemplate or '',
                [skill.model_dump() for skill in entry.weapon.skills],
            )
            if verified is not None:
                raw_refine = verified
        weapon = entry.weapon.model_copy(update={"weaponRefine": _display_weapon_refine(raw_refine)})
        normalized.append(entry.model_copy(update={"weapon": weapon}))
    return normalized


def _filter_roster_to_participants(
    roster: list[BattleRosterEntryResponse],
    participants: list[BattleParticipantResponse],
) -> list[BattleRosterEntryResponse]:
    participant_keys = {
        str(entry.characterKey or "").strip()
        for entry in participants
        if str(entry.characterKey or "").strip()
    }
    participant_names = {
        str(entry.characterName or "").strip()
        for entry in participants
        if str(entry.characterName or "").strip()
    }
    if not participant_keys and not participant_names:
        return roster

    filtered = [
        entry
        for entry in roster
        if (
            str(entry.characterKey or "").strip()
            and str(entry.characterKey or "").strip() in participant_keys
        )
        or (
            not str(entry.characterKey or "").strip()
            and str(entry.characterName or "").strip() in participant_names
        )
    ]
    if not filtered:
        return roster
    return [entry.model_copy(update={"slot": index}) for index, entry in enumerate(filtered, start=1)]


def _normalize_off_participant_timeline_contributions(
    timeline_events: list[TimelineEventResponse],
    participants: list[BattleParticipantResponse],
    *,
    duration_ms: int,
) -> tuple[list[TimelineEventResponse], list[BattleParticipantResponse]]:
    participant_by_key = {
        str(entry.characterKey or "").strip(): entry
        for entry in participants
        if str(entry.characterKey or "").strip()
    }
    if not participant_by_key:
        return timeline_events, participants

    normalized_events: list[TimelineEventResponse] = []
    changed_contributions = False
    for event in timeline_events:
        updates: dict[str, Any] = {}
        target_player_key = str(event.targetPlayerKey or "").strip()
        if (
            event.laneType == "buff"
            and str(event.eventKey or "").startswith("buff_common_affixes_enhance_")
            and str(event.sourceCharacterKey or "").strip() not in participant_by_key
            and target_player_key in participant_by_key
        ):
            target_participant = participant_by_key[target_player_key]
            updates["sourceCharacterKey"] = target_participant.characterKey
            updates["sourceCharacterName"] = target_participant.characterName

        if event.rdpsContributions:
            combined: dict[str, float] = {}
            names: dict[str, str] = {}
            order: list[str] = []
            fallback_key = str(event.sourceCharacterKey or updates.get("sourceCharacterKey") or "").strip()
            if fallback_key not in participant_by_key:
                fallback_key = target_player_key if target_player_key in participant_by_key else ""
            for contribution in event.rdpsContributions:
                key = str(contribution.characterKey or "").strip()
                if key not in participant_by_key:
                    changed_contributions = True
                    if fallback_key:
                        key = fallback_key
                if key not in participant_by_key:
                    continue
                if key not in combined:
                    combined[key] = 0.0
                    names[key] = participant_by_key[key].characterName
                    order.append(key)
                combined[key] += contribution.value
            updates["rdpsContributions"] = [
                TimelineEventResponse.RdpsContributionResponse(
                    characterKey=key,
                    characterName=names[key],
                    value=combined[key],
                )
                for key in order
            ]

        normalized_events.append(event.model_copy(update=updates) if updates else event)

    totals = {key: 0.0 for key in participant_by_key}
    has_contributions = False
    for event in normalized_events:
        for contribution in event.rdpsContributions:
            key = str(contribution.characterKey or "").strip()
            if key in totals:
                totals[key] += contribution.value
                has_contributions = True
    if not changed_contributions or not has_contributions:
        return normalized_events, participants

    duration_seconds = max(duration_ms / 1000, 1e-9)
    normalized_participants = [
        participant.model_copy(update={"rdps": round(totals[str(participant.characterKey or "").strip()] / duration_seconds, 2)})
        if str(participant.characterKey or "").strip() in totals
        else participant
        for participant in participants
    ]
    return normalized_events, normalized_participants


@dataclass(frozen=True)
class BossSeed:
    key: str
    slug: str
    name: str
    dungeon_name: str
    roster: tuple[str, str, str, str]
    uploader_names: tuple[str, str, str]
    base_duration_ms: int
    base_dps: int
    base_rdps: int


@dataclass
class DemoBattle:
    battle_id: str
    uploader_user_id: str
    uploader_nickname: str
    boss_key: str
    boss_slug: str
    boss_name: str
    dungeon_name: str
    duration_ms: int
    battle_start_at: datetime
    battle_end_at: datetime
    total_damage: int
    total_dps: float
    roster: list[BattleRosterEntryResponse]
    participants: list[BattleParticipantResponse]
    timeline_events: list[TimelineEventResponse]
    role_skill_stats: list[RoleSkillStatResponse]
    parser_version: str
    rules_version: str
    battle_fingerprint: str
    time_source: str | None = None
    timeline_zero_source: str | None = None
    timer_start_seen: bool | None = None
    timer_end_seen: bool | None = None
    official_timer_start_seen: bool | None = None
    official_timer_end_seen: bool | None = None
    official_timer_observed_elapsed_ms: int | None = None
    packet_timer_elapsed_ms: int | None = None
    timer_integrity_ok: bool | None = None
    timer_integrity_reasons: list[str] = field(default_factory=list)
    timer_evidence: dict[str, Any] | None = None
    timer_start_inferred: bool | None = None
    timer_window_valid: bool | None = None
    rdps_preflight_ok: bool | None = None
    rdps_strict_ok: bool | None = None
    rdps_preflight_blocker_count: int | None = None
    boss_identity_source: str | None = None
    dungeon_context_id: str | None = None
    dungeon_identity_source: str | None = None
    loadout_fallback_used: bool | None = None
    contract_tag_score: int | None = None
    contract_tags: list[ContractTagResponse] = field(default_factory=list)
    clear_flag: bool = True
    visibility: str = "public"
    status: str = "valid"
    # v33+：完整施法序列（排轴导出 API 专用，原样 dict 存取，不做响应建模）；
    # None = 老 payload 不支持导出
    casts: list[dict] | None = None


@dataclass(frozen=True)
class CharacterStatisticsBattleRow:
    battle_id: str
    boss_slug: str
    battle_end_at: datetime
    duration_ms: int
    total_damage: int
    parser_version: str
    rdps_strict_ok: bool | None
    rules_version: str | None = None
    timer_integrity_ok: bool | None = None
    roster_json: str | None = None
    participants_json: str | None = None


BOSS_SEEDS: tuple[BossSeed, ...] = (
    BossSeed(
        key="dung01_group_bossrush01",
        slug="dung01_group_bossrush01",
        name="危境再现·罗丹",
        dungeon_name="危境再现",
        roster=("chr_0028_wulfa", "chr_0027_tangtang", "chr_0019_karin", "chr_0004_pelica"),
        uploader_names=("锻炉房", "铁砧回声", "钢铁余烬"),
        base_duration_ms=321000,
        base_dps=184200,
        base_rdps=197600,
    ),
    BossSeed(
        key="dung01_group_bossrush02",
        slug="dung01_group_bossrush02",
        name="危境再现·三位一体",
        dungeon_name="危境再现",
        roster=("chr_0015_lifeng", "chr_0009_azrila", "chr_0013_aglina", "chr_0024_deepfin"),
        uploader_names=("信号尘", "蓝继电", "铬梦"),
        base_duration_ms=298000,
        base_dps=191800,
        base_rdps=204100,
    ),
    BossSeed(
        key="dung01_group_bossrush03",
        slug="dung01_group_bossrush03",
        name="危境再现·白垩界卫",
        dungeon_name="危境再现",
        roster=("chr_0005_chen", "chr_0014_aurora", "chr_0011_seraph", "chr_0016_laevat"),
        uploader_names=("夜弧", "空行军", "影栅"),
        base_duration_ms=287000,
        base_dps=196500,
        base_rdps=208900,
    ),
    BossSeed(
        key="dung02_group_bossrush01",
        slug="dung02_group_bossrush01",
        name="危境再现·阮一",
        dungeon_name="危境再现",
        roster=("chr_0021_whiten", "chr_0020_meurs", "chr_0023_antal", "chr_0007_ikut"),
        uploader_names=("煤语", "焰线", "火结"),
        base_duration_ms=309000,
        base_dps=188000,
        base_rdps=201200,
    ),
    BossSeed(
        key="dung02_group_bossrush02",
        slug="dung02_group_bossrush02",
        name="危境再现·聂菲斯",
        dungeon_name="危境再现",
        roster=("chr_0026_lastrite", "chr_0017_yvonne", "chr_0025_ardelia", "chr_0030_zhuangfy"),
        uploader_names=("澄折", "光环架", "镜歌"),
        base_duration_ms=276000,
        base_dps=199800,
        base_rdps=214300,
    ),
    BossSeed(
        key="dung02_group_minibossrush01",
        slug="dung02_group_minibossrush01",
        name="巨山犼兽",
        dungeon_name="危境碎片",
        roster=("chr_0026_lastrite", "chr_0017_yvonne", "chr_0025_ardelia", "chr_0030_zhuangfy"),
        uploader_names=("碎片回声", "危境边界", "山犼记录"),
        base_duration_ms=300000,
        base_dps=190000,
        base_rdps=203000,
    ),
    BossSeed(
        key="eny_0125_fdcentur",
        slug="dung02_group_bossrush03",
        name="危境再现·阿莱克琉斯",
        dungeon_name="危境再现",
        roster=("chr_0032_lizhiyan", "chr_0026_lastrite", "chr_0017_yvonne", "chr_0025_ardelia"),
        uploader_names=("千夫长记录", "归途道标", "危境锋刃"),
        base_duration_ms=284000,
        base_dps=201600,
        base_rdps=216800,
    ),
    BossSeed(
        key="eny_0120_klbear",
        slug="dung02_group_minibossrush02",
        name="蚀影噪雷",
        dungeon_name="危境碎片",
        roster=("chr_0032_lizhiyan", "chr_0030_zhuangfy", "chr_0026_lastrite", "chr_0019_karin"),
        uploader_names=("噪雷碎片", "蚀影回响", "碎片边界"),
        base_duration_ms=296000,
        base_dps=193500,
        base_rdps=207400,
    ),
    BossSeed(
        key="eny_0127_bigents",
        slug="dung02_group_minibossrush03",
        name="幽林之怒",
        dungeon_name="危境碎片",
        roster=("chr_0034_typhoea", "chr_0032_lizhiyan", "chr_0033_camille", "chr_0026_lastrite"),
        uploader_names=("幽林碎片", "林怒回响", "碎片边界"),
        base_duration_ms=296000,
        base_dps=193500,
        base_rdps=207400,
    ),
    BossSeed(
        key="eny_0090_wgabyss",
        slug="indie_group_ccdg",
        name="破潮之像",
        dungeon_name="危机合约",
        roster=("chr_0030_zhuangfy", "chr_0026_lastrite", "chr_0019_karin", "chr_0009_azrila"),
        uploader_names=("合约记录", "破潮合约", "危机档案"),
        base_duration_ms=300000,
        base_dps=190000,
        base_rdps=203000,
    ),
    BossSeed(
        key="indie_hard008_s",
        slug="indie_hard008_s",
        name="怨憎雾海·苦难",
        dungeon_name="影拓丰碑1期 · 灼痛疤痕",
        roster=("chr_0028_wulfa", "chr_0017_yvonne", "chr_0027_tangtang", "chr_0004_pelica"),
        uploader_names=("赤痕", "烬线", "热障"),
        base_duration_ms=330000,
        base_dps=182400,
        base_rdps=195600,
    ),
    BossSeed(
        key="indie_hard009_s",
        slug="indie_hard009_s",
        name="血肉熔点·苦难",
        dungeon_name="影拓丰碑1期 · 灼痛疤痕",
        roster=("chr_0028_wulfa", "chr_0017_yvonne", "chr_0027_tangtang", "chr_0004_pelica"),
        uploader_names=("熔线", "铁水", "热障"),
        base_duration_ms=318000,
        base_dps=186300,
        base_rdps=199400,
    ),
    BossSeed(
        key="indie_hard007_s",
        slug="indie_hard007_s",
        name="呼吼炽焰·苦难",
        dungeon_name="影拓丰碑1期 · 灼痛疤痕",
        roster=("chr_0028_wulfa", "chr_0017_yvonne", "chr_0027_tangtang", "chr_0004_pelica"),
        uploader_names=("炽焰", "火幕", "怒层"),
        base_duration_ms=342000,
        base_dps=179800,
        base_rdps=193100,
    ),
    BossSeed(
        key="indie_hard002_s",
        slug="indie_hard002_s",
        name="矢影环伺·苦难",
        dungeon_name="影拓丰碑1期 · 无机造物",
        roster=("chr_0021_whiten", "chr_0015_lifeng", "chr_0013_aglina", "chr_0024_deepfin"),
        uploader_names=("白构", "环矢", "晶步"),
        base_duration_ms=321000,
        base_dps=184500,
        base_rdps=198300,
    ),
    BossSeed(
        key="indie_hard001_s",
        slug="indie_hard001_s",
        name="狂冲盾止·苦难",
        dungeon_name="影拓丰碑1期 · 无机造物",
        roster=("chr_0021_whiten", "chr_0015_lifeng", "chr_0013_aglina", "chr_0024_deepfin"),
        uploader_names=("盾止", "刚角", "白构"),
        base_duration_ms=309000,
        base_dps=188700,
        base_rdps=201600,
    ),
    BossSeed(
        key="indie_hard003_s",
        slug="indie_hard003_s",
        name="安于磐石·苦难",
        dungeon_name="影拓丰碑1期 · 无机造物",
        roster=("chr_0021_whiten", "chr_0015_lifeng", "chr_0013_aglina", "chr_0024_deepfin"),
        uploader_names=("磐石", "晶城", "稳步"),
        base_duration_ms=336000,
        base_dps=181900,
        base_rdps=195100,
    ),
    BossSeed(
        key="indie_hard006_s",
        slug="indie_hard006_s",
        name="毒雾求生·苦难",
        dungeon_name="影拓丰碑1期 · 大地的弃子",
        roster=("chr_0030_zhuangfy", "chr_0026_lastrite", "chr_0019_karin", "chr_0009_azrila"),
        uploader_names=("弃土", "毒雾", "荒门"),
        base_duration_ms=354000,
        base_dps=175200,
        base_rdps=188600,
    ),
    BossSeed(
        key="indie_hard005_s",
        slug="indie_hard005_s",
        name="旁门外道·苦难",
        dungeon_name="影拓丰碑1期 · 大地的弃子",
        roster=("chr_0030_zhuangfy", "chr_0026_lastrite", "chr_0019_karin", "chr_0009_azrila"),
        uploader_names=("旁门", "虬影", "荒门"),
        base_duration_ms=333000,
        base_dps=181100,
        base_rdps=194800,
    ),
    BossSeed(
        key="indie_hard004_s",
        slug="indie_hard004_s",
        name="弩斧相应·苦难",
        dungeon_name="影拓丰碑1期 · 大地的弃子",
        roster=("chr_0030_zhuangfy", "chr_0026_lastrite", "chr_0019_karin", "chr_0009_azrila"),
        uploader_names=("弩斧", "行刑", "弃土"),
        base_duration_ms=324000,
        base_dps=185600,
        base_rdps=199000,
    ),
    BossSeed(
        key="indie_hard013_s",
        slug="indie_hard013_s",
        name="沉寂视界·苦难",
        dungeon_name="影拓丰碑2期 · 浊流具现",
        roster=("chr_0028_wulfa", "chr_0017_yvonne", "chr_0027_tangtang", "chr_0004_pelica"),
        uploader_names=("沉视", "雾牙", "浊潮"),
        base_duration_ms=347000,
        base_dps=178600,
        base_rdps=191400,
    ),
    BossSeed(
        key="indie_hard014_s",
        slug="indie_hard014_s",
        name="枯竭海退·苦难",
        dungeon_name="影拓丰碑2期 · 浊流具现",
        roster=("chr_0028_wulfa", "chr_0017_yvonne", "chr_0027_tangtang", "chr_0004_pelica"),
        uploader_names=("海退", "酸雾", "浊潮"),
        base_duration_ms=335000,
        base_dps=182900,
        base_rdps=196200,
    ),
    BossSeed(
        key="indie_hard010_s",
        slug="indie_hard010_s",
        name="潮涌遗恨·苦难",
        dungeon_name="影拓丰碑2期 · 浊流具现",
        roster=("chr_0021_whiten", "chr_0015_lifeng", "chr_0013_aglina", "chr_0024_deepfin"),
        uploader_names=("潮涌", "浊天", "回声"),
        base_duration_ms=326000,
        base_dps=185800,
        base_rdps=199100,
    ),
    BossSeed(
        key="indie_hard012_s",
        slug="indie_hard012_s",
        name="污浊血脉·苦难",
        dungeon_name="影拓丰碑2期 · 浊流具现",
        roster=("chr_0021_whiten", "chr_0015_lifeng", "chr_0013_aglina", "chr_0024_deepfin"),
        uploader_names=("血脉", "球刺", "回声"),
        base_duration_ms=338000,
        base_dps=181700,
        base_rdps=194600,
    ),
    BossSeed(
        key="indie_hard015_s",
        slug="indie_hard015_s",
        name="潜流绝窟·苦难",
        dungeon_name="影拓丰碑2期 · 浊流具现",
        roster=("chr_0030_zhuangfy", "chr_0026_lastrite", "chr_0019_karin", "chr_0009_azrila"),
        uploader_names=("潜流", "肖像", "绝窟"),
        base_duration_ms=329000,
        base_dps=184400,
        base_rdps=197700,
    ),
    BossSeed(
        key="indie_hard011_s",
        slug="indie_hard011_s",
        name="霜冻联结·苦难",
        dungeon_name="影拓丰碑2期 · 浊流具现",
        roster=("chr_0030_zhuangfy", "chr_0026_lastrite", "chr_0019_karin", "chr_0009_azrila"),
        uploader_names=("霜联", "破潮", "绝窟"),
        base_duration_ms=341000,
        base_dps=180900,
        base_rdps=193800,
    ),
    BossSeed(
        key="indie_hard016_s",
        slug="indie_hard016_s",
        name="仪式旋流·苦难",
        dungeon_name="影拓丰碑3期 · 死寂争鸣",
        roster=("chr_0033_camille", "chr_0028_wulfa", "chr_0027_tangtang", "chr_0004_pelica"),
        uploader_names=("旋流", "焰术", "争鸣"),
        base_duration_ms=336000,
        base_dps=183200,
        base_rdps=196500,
    ),
    BossSeed(
        key="indie_hard017_s",
        slug="indie_hard017_s",
        name="死寂表象·苦难",
        dungeon_name="影拓丰碑3期 · 死寂争鸣",
        roster=("chr_0033_camille", "chr_0028_wulfa", "chr_0027_tangtang", "chr_0004_pelica"),
        uploader_names=("表象", "死寂", "回声"),
        base_duration_ms=348000,
        base_dps=179400,
        base_rdps=192700,
    ),
    BossSeed(
        key="indie_hard018_s",
        slug="indie_hard018_s",
        name="忿鼓咆声·苦难",
        dungeon_name="影拓丰碑3期 · 死寂争鸣",
        roster=("chr_0033_camille", "chr_0028_wulfa", "chr_0027_tangtang", "chr_0004_pelica"),
        uploader_names=("忿鼓", "天鼓", "鼓声"),
        base_duration_ms=329000,
        base_dps=186100,
        base_rdps=199400,
    ),
    BossSeed(
        key="indie_hard019_s",
        slug="indie_hard019_s",
        name="刺痛盾阵·苦难",
        dungeon_name="影拓丰碑3期 · 死寂争鸣",
        roster=("chr_0033_camille", "chr_0030_zhuangfy", "chr_0026_lastrite", "chr_0019_karin"),
        uploader_names=("盾阵", "晶锥", "棱镜"),
        base_duration_ms=342000,
        base_dps=181600,
        base_rdps=194900,
    ),
    BossSeed(
        key="indie_hard020_s",
        slug="indie_hard020_s",
        name="溶解窥看·苦难",
        dungeon_name="影拓丰碑3期 · 死寂争鸣",
        roster=("chr_0033_camille", "chr_0030_zhuangfy", "chr_0026_lastrite", "chr_0019_karin"),
        uploader_names=("窥看", "潮行", "溶解"),
        base_duration_ms=331000,
        base_dps=185300,
        base_rdps=198600,
    ),
    BossSeed(
        key="indie_hard021_s",
        slug="indie_hard021_s",
        name="冯河断水·苦难",
        dungeon_name="影拓丰碑3期 · 死寂争鸣",
        roster=("chr_0033_camille", "chr_0030_zhuangfy", "chr_0026_lastrite", "chr_0019_karin"),
        uploader_names=("断水", "怒彪", "河声"),
        base_duration_ms=325000,
        base_dps=187800,
        base_rdps=201100,
    ),
    BossSeed(
        key="indie_hard022_s",
        slug="indie_hard022_s",
        name="撼山雾火·苦难",
        dungeon_name="影拓丰碑4期 · 山中见犼",
        roster=("chr_0032_lizhiyan", "chr_0028_wulfa", "chr_0027_tangtang", "chr_0004_pelica"),
        uploader_names=("雾火", "撼山", "山中见犼"),
        base_duration_ms=338000,
        base_dps=183900,
        base_rdps=197200,
    ),
    BossSeed(
        key="indie_hard023_s",
        slug="indie_hard023_s",
        name="兽群把戏·苦难",
        dungeon_name="影拓丰碑4期 · 山中见犼",
        roster=("chr_0032_lizhiyan", "chr_0028_wulfa", "chr_0027_tangtang", "chr_0004_pelica"),
        uploader_names=("兽群", "把戏", "香蕉皮"),
        base_duration_ms=332000,
        base_dps=185600,
        base_rdps=198900,
    ),
    BossSeed(
        key="indie_hard024_s",
        slug="indie_hard024_s",
        name="山犼争王·苦难",
        dungeon_name="影拓丰碑4期 · 山中见犼",
        roster=("chr_0032_lizhiyan", "chr_0030_zhuangfy", "chr_0026_lastrite", "chr_0019_karin"),
        uploader_names=("争王", "新王", "群山"),
        base_duration_ms=327000,
        base_dps=188200,
        base_rdps=201900,
    ),
    BossSeed(
        key="indie_hard025_s",
        slug="indie_hard025_s",
        name="清波访客·苦难",
        dungeon_name="影拓丰碑4期 · 山中见犼",
        roster=("chr_0032_lizhiyan", "chr_0030_zhuangfy", "chr_0026_lastrite", "chr_0019_karin"),
        uploader_names=("清波", "访客", "刀剑声"),
        base_duration_ms=344000,
        base_dps=181700,
        base_rdps=194800,
    ),
    BossSeed(
        key="indie_battletower001_ex",
        slug="indie_battletower001_ex",
        name="白刃穿水·残酷",
        dungeon_name="战争回响",
        roster=("chr_0032_lizhiyan", "chr_0033_camille", "chr_0030_zhuangfy", "chr_0026_lastrite"),
        uploader_names=("白刃穿水", "追忆记录", "回响档案"),
        base_duration_ms=300000,
        base_dps=190000,
        base_rdps=203000,
    ),
    BossSeed(
        key="indie_battletower002_ex",
        slug="indie_battletower002_ex",
        name="野性旧事·残酷",
        dungeon_name="战争回响",
        roster=("chr_0032_lizhiyan", "chr_0033_camille", "chr_0030_zhuangfy", "chr_0026_lastrite"),
        uploader_names=("野性旧事", "追忆记录", "回响档案"),
        base_duration_ms=300000,
        base_dps=190000,
        base_rdps=203000,
    ),
    BossSeed(
        key="indie_battletower003_ex",
        slug="indie_battletower003_ex",
        name="弓弩表象·残酷",
        dungeon_name="战争回响",
        roster=("chr_0032_lizhiyan", "chr_0033_camille", "chr_0030_zhuangfy", "chr_0026_lastrite"),
        uploader_names=("弓弩表象", "追忆记录", "回响档案"),
        base_duration_ms=300000,
        base_dps=190000,
        base_rdps=203000,
    ),
    BossSeed(
        key="indie_battletower004_ex",
        slug="indie_battletower004_ex",
        name="斧柄纪年·残酷",
        dungeon_name="战争回响",
        roster=("chr_0032_lizhiyan", "chr_0033_camille", "chr_0030_zhuangfy", "chr_0026_lastrite"),
        uploader_names=("斧柄纪年", "追忆记录", "回响档案"),
        base_duration_ms=300000,
        base_dps=190000,
        base_rdps=203000,
    ),
    BossSeed(
        key="indie_battletower005_ex",
        slug="indie_battletower005_ex",
        name="铳弹砺石·残酷",
        dungeon_name="战争回响",
        roster=("chr_0032_lizhiyan", "chr_0033_camille", "chr_0030_zhuangfy", "chr_0026_lastrite"),
        uploader_names=("铳弹砺石", "追忆记录", "回响档案"),
        base_duration_ms=300000,
        base_dps=190000,
        base_rdps=203000,
    ),
    BossSeed(
        key="indie_battletower006_ex",
        slug="indie_battletower006_ex",
        name="裂地旧创·残酷",
        dungeon_name="战争回响",
        roster=("chr_0032_lizhiyan", "chr_0033_camille", "chr_0030_zhuangfy", "chr_0026_lastrite"),
        uploader_names=("裂地旧创", "追忆记录", "回响档案"),
        base_duration_ms=300000,
        base_dps=190000,
        base_rdps=203000,
    ),
    BossSeed(
        key="indie_battletower007_ex",
        slug="indie_battletower007_ex",
        name="死兽鸣吼·残酷",
        dungeon_name="战争回响",
        roster=("chr_0032_lizhiyan", "chr_0033_camille", "chr_0030_zhuangfy", "chr_0026_lastrite"),
        uploader_names=("死兽鸣吼", "追忆记录", "回响档案"),
        base_duration_ms=300000,
        base_dps=190000,
        base_rdps=203000,
    ),
    BossSeed(
        key="indie_battletower008_ex",
        slug="indie_battletower008_ex",
        name="战争简史·残酷",
        dungeon_name="战争回响",
        roster=("chr_0032_lizhiyan", "chr_0033_camille", "chr_0030_zhuangfy", "chr_0026_lastrite"),
        uploader_names=("战争简史", "追忆记录", "回响档案"),
        base_duration_ms=300000,
        base_dps=190000,
        base_rdps=203000,
    ),
    BossSeed(
        key="indie_battletower009_ex",
        slug="indie_battletower009_ex",
        name="掩埋阵线·残酷",
        dungeon_name="战争回响",
        roster=("chr_0034_typhoea", "chr_0032_lizhiyan", "chr_0033_camille", "chr_0026_lastrite"),
        uploader_names=("掩埋阵线", "追忆记录", "回响档案"),
        base_duration_ms=300000,
        base_dps=190000,
        base_rdps=203000,
    ),
    BossSeed(
        key="indie_battletower010_ex",
        slug="indie_battletower010_ex",
        name="方阵庇护·残酷",
        dungeon_name="战争回响",
        roster=("chr_0034_typhoea", "chr_0032_lizhiyan", "chr_0033_camille", "chr_0026_lastrite"),
        uploader_names=("方阵庇护", "追忆记录", "回响档案"),
        base_duration_ms=300000,
        base_dps=190000,
        base_rdps=203000,
    ),
    BossSeed(
        key="indie_battletower011_ex",
        slug="indie_battletower011_ex",
        name="重伤之围·残酷",
        dungeon_name="战争回响",
        roster=("chr_0034_typhoea", "chr_0032_lizhiyan", "chr_0033_camille", "chr_0026_lastrite"),
        uploader_names=("重伤之围", "追忆记录", "回响档案"),
        base_duration_ms=300000,
        base_dps=190000,
        base_rdps=203000,
    ),
    BossSeed(
        key="indie_battletower012_ex",
        slug="indie_battletower012_ex",
        name="无机狂热·残酷",
        dungeon_name="战争回响",
        roster=("chr_0034_typhoea", "chr_0032_lizhiyan", "chr_0033_camille", "chr_0026_lastrite"),
        uploader_names=("无机狂热", "追忆记录", "回响档案"),
        base_duration_ms=300000,
        base_dps=190000,
        base_rdps=203000,
    ),
    BossSeed(
        key="indie_battletower013_ex",
        slug="indie_battletower013_ex",
        name="斩首蓄势·残酷",
        dungeon_name="战争回响",
        roster=("chr_0034_typhoea", "chr_0032_lizhiyan", "chr_0033_camille", "chr_0026_lastrite"),
        uploader_names=("斩首蓄势", "追忆记录", "回响档案"),
        base_duration_ms=300000,
        base_dps=190000,
        base_rdps=203000,
    ),
    BossSeed(
        key="indie_battletower014_ex",
        slug="indie_battletower014_ex",
        name="野兽诡计·残酷",
        dungeon_name="战争回响",
        roster=("chr_0034_typhoea", "chr_0032_lizhiyan", "chr_0033_camille", "chr_0026_lastrite"),
        uploader_names=("野兽诡计", "追忆记录", "回响档案"),
        base_duration_ms=300000,
        base_dps=190000,
        base_rdps=203000,
    ),
)

HIGH_DIFFICULTY_STAGE_SLUG_ALIASES: dict[str, str] = {}

HIGH_DIFFICULTY_BOSS_KEY_SLUG_ALIASES: dict[str, str] = {
    "eny_0021_agmelee": "indie_hard002_s",
    "eny_0023_aghornb": "indie_hard001_s",
    "eny_0023_aghornb_hdg001": "indie_hard001_s",
    "eny_0023_aghornb_hdg001_assult": "indie_hard001_s",
    "eny_0027_agscorp": "indie_hard002_s",
    "eny_0027_agscorp_hdg002": "indie_hard002_s",
    "eny_0027_agscorp_hdg002_assult": "indie_hard002_s",
    "eny_0064_agrange2": "indie_hard002_s",
    "eny_0064_agrange2_hdg002_assult": "indie_hard002_s",
    "eny_0076_agfly": "indie_hard002_s",
    "eny_0077_agshield": "indie_hard003_s",
    "eny_0077_agshield_hdg003": "indie_hard003_s",
    "eny_0007_mimicw": "indie_hard005_s",
    "eny_0007_mimicw_hdg005": "indie_hard005_s",
    "eny_0018_lbtough": "indie_hard004_s",
    "eny_0018_lbtough_hdg004": "indie_hard004_s",
    "eny_0018_lbtough_hdg004_assult": "indie_hard004_s",
    "eny_0033_lbhunt": "indie_hard005_s",
    "eny_0033_lbhunt_hdg005": "indie_hard005_s",
    "eny_0048_hvybow": "indie_hard004_s",
    "eny_0048_hvybow_hdg004": "indie_hard004_s",
    "eny_0048_hvybow_hdg004_assult": "indie_hard004_s",
    "eny_0049_rogue": "indie_hard006_s",
    "eny_0050_hound": "indie_hard006_s",
    "eny_0059_erhound": "indie_hard006_s",
    "eny_0066_lbhunt2": "indie_hard006_s",
    "eny_0066_lbhunt2_hdg005": "indie_hard005_s",
    "eny_0067_hound2": "indie_hard006_s",
    "eny_0074_lbshield": "indie_hard006_s",
    "eny_0075_lbroshan": "indie_hard006_s",
    "eny_0047_firebat": "indie_hard009_s",
    "eny_0047_firebat_hdg009": "indie_hard009_s",
    "eny_0054_hsmino": "indie_hard007_s",
    "eny_0054_hsmino_hdg007": "indie_hard007_s",
    "eny_0055_hscrane": "indie_hard008_s",
    "eny_0055_hscrane_hard": "indie_hard008_s",
    "eny_0085_hsrogue": "indie_hard008_s",
    "eny_0085_hsrogue_hard": "indie_hard008_s",
    "eny_0060_lbmad": "indie_hard013_s",
    "eny_0099_slimeml2": "indie_hard014_s",
    "eny_0100_slimerg2": "indie_hard014_s",
    "eny_0108_slbomb2_hdg014": "indie_hard014_s",
    "eny_0098_sandb2": "indie_hard014_s",
    "eny_0087_wgslime_hdg010": "indie_hard010_s",
    "eny_0105_wgslime2_hdg010": "indie_hard010_s",
    "eny_0093_hshog_hdg": "indie_hard012_s",
    "eny_0109_hshog2_hdg": "indie_hard012_s",
    "eny_0058_agdisk": "indie_hard015_s",
    "eny_0090_wgabyss_hdg011": "indie_hard011_s",
    "eny_0046_lbshamman": "indie_hard016_s",
    "eny_0046_lbshamman_hdg016": "indie_hard016_s",
    "eny_0046_lbshamman_maineny": "indie_hard016_s",
    "eny_0046_lbshamman_teleport": "indie_hard016_s",
    "eny_0095_ethillu": "indie_hard017_s",
    "eny_0095_ethillu_hdg017": "indie_hard017_s",
    "eny_0082_hsbear": "indie_hard018_s",
    "eny_0082_hsbear_hdg018": "indie_hard018_s",
    "eny_0088_wgthorns": "indie_hard019_s",
    "eny_0088_wgthorns_hdg019": "indie_hard019_s",
    "eny_0089_wgreflec": "indie_hard019_s",
    "eny_0089_wgreflec_hdg019": "indie_hard019_s",
    "eny_0107_wgshoal2": "indie_hard020_s",
    "eny_0107_wgshoal2_hdg020": "indie_hard020_s",
    "eny_0102_hstiger2": "indie_hard021_s",
    "eny_0102_hstiger2_hdg021": "indie_hard021_s",
}

HIGH_DIFFICULTY_GROUP_BOSS_KEY_SLUG_ALIASES: dict[str, dict[str, str]] = {
    "indie_group_h04": {
        "eny_0059_erhound": "indie_hard013_s",
        "eny_0060_lbmad": "indie_hard013_s",
        "eny_0099_slimeml2": "indie_hard014_s",
        "eny_0100_slimerg2": "indie_hard014_s",
        "eny_0108_slbomb2_hdg014": "indie_hard014_s",
        "eny_0098_sandb2": "indie_hard014_s",
        "eny_0087_wgslime_hdg010": "indie_hard010_s",
        "eny_0105_wgslime2_hdg010": "indie_hard010_s",
        "eny_0093_hshog_hdg": "indie_hard012_s",
        "eny_0109_hshog2_hdg": "indie_hard012_s",
        "eny_0058_agdisk": "indie_hard015_s",
        "eny_0090_wgabyss_hdg011": "indie_hard011_s",
    },
    "indie_group_h05": {
        "eny_0046_lbshamman": "indie_hard016_s",
        "eny_0046_lbshamman_hdg016": "indie_hard016_s",
        "eny_0046_lbshamman_maineny": "indie_hard016_s",
        "eny_0046_lbshamman_teleport": "indie_hard016_s",
        "eny_0095_ethillu": "indie_hard017_s",
        "eny_0095_ethillu_hdg017": "indie_hard017_s",
        "eny_0082_hsbear": "indie_hard018_s",
        "eny_0082_hsbear_hdg018": "indie_hard018_s",
        "eny_0088_wgthorns": "indie_hard019_s",
        "eny_0088_wgthorns_hdg019": "indie_hard019_s",
        "eny_0089_wgreflec": "indie_hard019_s",
        "eny_0089_wgreflec_hdg019": "indie_hard019_s",
        "eny_0107_wgshoal2": "indie_hard020_s",
        "eny_0107_wgshoal2_hdg020": "indie_hard020_s",
        "eny_0102_hstiger2": "indie_hard021_s",
        "eny_0102_hstiger2_hdg021": "indie_hard021_s",
    },
}
CRISIS_FRAGMENT_BOSS_SLUG = "dung02_group_minibossrush01"
CRISIS_CONTRACT_BOSS_SLUG = "indie_group_ccdg"
CRISIS_FRAGMENT_STAGE_SLUG_ALIASES: dict[str, str] = {
    "dung02_minibossrush01": CRISIS_FRAGMENT_BOSS_SLUG,
    "dung02_minibossrush01_01": CRISIS_FRAGMENT_BOSS_SLUG,
    "dung02_minibossrush01_02": CRISIS_FRAGMENT_BOSS_SLUG,
}
CRISIS_FRAGMENT_BOSS_KEY_SLUG_ALIASES: dict[str, str] = {
    "eny_0114_jzmking": CRISIS_FRAGMENT_BOSS_SLUG,
}
ACTIVITY_STAGE_SLUG_ALIASES: dict[str, str] = {}
ACTIVITY_GROUP_BOSS_KEY_SLUG_ALIASES: dict[str, dict[str, str]] = {}
LEGACY_ACTIVITY_GROUP_SLUGS = frozenset({"indie_group_twdg"})
REVIEWED_NON_RANKING_DUNGEON_SLUGS: frozenset[str] = frozenset()


def _apply_dungeon_catalog_overrides() -> None:
    """Merge dungeon alias tables from data/local_tables/dungeon_catalog.json.

    Same single-source catalog consumed by parser_core and the web frontend.
    File entries win over the built-in tables; built-ins remain as fallback.
    """
    global BOSS_SEEDS, REVIEWED_NON_RANKING_DUNGEON_SLUGS
    catalog_path = REPO_ROOT / "data" / "local_tables" / "dungeon_catalog.json"
    if not catalog_path.exists():
        return
    try:
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    non_ranking: set[str] = set()
    for series_id, decision in ((catalog.get("discovery") or {}).get("manual_series_decisions") or {}).items():
        if decision.get("decision") != "reviewed_not_ranked":
            continue
        detail = json.loads((REPO_ROOT / "data/local_tables/dungeon/items" / f"{series_id}.json").read_text("utf-8"))
        signature_fields = {
            "templateId": detail.get("templateId") or detail.get("id") or "",
            "name": detail.get("name") or "", "gameCategory": detail.get("gameCategory") or "",
            "includeDungeonIds": list(detail.get("includeDungeonIds") or []),
            "dungeonPicPaths": list(detail.get("dungeonPicPaths") or []),
            "dungeonRoleImg": detail.get("dungeonRoleImg") or "",
        }
        signature = sha256(json.dumps(signature_fields, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if signature_fields["templateId"] != series_id or signature != decision.get("resource_signature"):
            raise ValueError(f"Manual dungeon decision differs from the official resources: {series_id}")
        for identity in (series_id, *signature_fields["includeDungeonIds"]):
            non_ranking.add(identity)
            # Include only the existing URL spelling of these exact IDs for
            # historical uploads; no category/name/prefix inference is used.
            non_ranking.add(sub(r"[^a-z0-9]+", "-", identity.lower()).strip("-"))
    REVIEWED_NON_RANKING_DUNGEON_SLUGS = frozenset(non_ranking)
    BOSS_SEEDS = tuple(boss for boss in BOSS_SEEDS if boss.slug not in non_ranking)
    # New rankings come from the same official catalog as parser and web.
    # Empty boards need metadata only; no fabricated participants or results.
    seed_slugs = {boss.slug for boss in BOSS_SEEDS}
    for group in (catalog.get("web") or {}).get("ranking_groups") or []:
        for boss in group.get("bosses") or []:
            slug = str(boss["slug"])
            if slug in non_ranking:
                raise ValueError(f"A manually excluded dungeon cannot have a ranking seed: {slug}")
            if slug not in seed_slugs:
                BOSS_SEEDS += (BossSeed(
                    key=slug, slug=slug, name=str(boss["name"]),
                    dungeon_name=str(boss["dungeonName"]),
                    roster=("unknown",) * 4, uploader_names=("",) * 3,
                    base_duration_ms=0, base_dps=0, base_rdps=0,
                ),)
                seed_slugs.add(slug)
    api_tables = catalog.get("api") or {}
    for key, value in (api_tables.get("high_difficulty_stage_slug_aliases") or {}).items():
        HIGH_DIFFICULTY_STAGE_SLUG_ALIASES[str(key)] = str(value)
    for key, value in (api_tables.get("high_difficulty_boss_key_slug_aliases") or {}).items():
        HIGH_DIFFICULTY_BOSS_KEY_SLUG_ALIASES[str(key)] = str(value)
    for group, mapping in (api_tables.get("high_difficulty_group_boss_key_slug_aliases") or {}).items():
        if isinstance(mapping, dict):
            HIGH_DIFFICULTY_GROUP_BOSS_KEY_SLUG_ALIASES.setdefault(str(group), {}).update(
                {str(key): str(value) for key, value in mapping.items()}
            )
    for key, value in (api_tables.get("crisis_fragment_stage_slug_aliases") or {}).items():
        CRISIS_FRAGMENT_STAGE_SLUG_ALIASES[str(key)] = str(value)
    for key, value in (api_tables.get("crisis_fragment_boss_key_slug_aliases") or {}).items():
        CRISIS_FRAGMENT_BOSS_KEY_SLUG_ALIASES[str(key)] = str(value)
    for key, value in (api_tables.get("activity_stage_slug_aliases") or {}).items():
        ACTIVITY_STAGE_SLUG_ALIASES[str(key)] = str(value)
    for group, mapping in (api_tables.get("activity_group_boss_key_slug_aliases") or {}).items():
        if isinstance(mapping, dict):
            ACTIVITY_GROUP_BOSS_KEY_SLUG_ALIASES.setdefault(str(group), {}).update(
                {str(key): str(value) for key, value in mapping.items()}
            )


_apply_dungeon_catalog_overrides()
HIGH_DIFFICULTY_CANONICAL_STAGE_SLUGS = frozenset(HIGH_DIFFICULTY_STAGE_SLUG_ALIASES.values())
ACTIVITY_CANONICAL_STAGE_SLUGS = frozenset(ACTIVITY_STAGE_SLUG_ALIASES.values())


def _is_inactive_season_tower_stage(boss_slug: str) -> bool:
    return boss_slug.startswith("indie_battletower") and boss_slug not in ACTIVITY_CANONICAL_STAGE_SLUGS

NEPHIS_BOSS_SLUG = "dung02_group_bossrush02"
NEPHIS_PHASE_ONE_KEYS = frozenset({"eny_0078_nefarp1"})
NEPHIS_PHASE_TWO_KEYS = frozenset({"eny_0079_nefarp2"})
NEPHIS_FULL_CLEAR_TOTALS = frozenset(
    {
        727_197 + 812_749,
        1_012_106 + 1_131_178,
        1_559_178 + 1_742_611,
    }
)
NEPHIS_FULL_CLEAR_TOTAL_TOLERANCE = 5_000


class DemoPublicDataService:
    def __init__(self) -> None:
        self.bosses_by_slug = {boss.slug: boss for boss in BOSS_SEEDS}
        self._hot_bosses_cache: tuple[float, list[HotBossCard]] | None = None
        self._battle_list_cache: dict[tuple[str | None, bool], tuple[float, list[DemoBattle]]] = {}
        self._battle_list_cache_lock = threading.Lock()
        self._public_ranked_cache: dict[
            tuple[str | None, Metric], tuple[float, list[DemoBattle]]
        ] = {}
        self._public_ranked_cache_lock = threading.Lock()
        self._public_account_directory_cache: tuple[
            float, tuple[PublicAccountSearchEntryResponse, ...]
        ] | None = None
        # 账号索引冷构建复用轻量榜单元数据；锁覆盖构建过程，避免匿名并发搜索
        # 重复扫描历史记录。
        self._public_account_directory_cache_lock = threading.Lock()
        self._character_statistics_cache: dict[
            tuple[str, str, str, str], tuple[float, BossCharacterStatisticsResponse]
        ] = {}
        # FastAPI 的同步 handler 会在线程池并发运行。统计冷缓存必须单航班，
        # 否则多个请求会同时扫描/解码全部历史样本，成倍放大 CPU 与内存。
        self._character_statistics_cache_lock = threading.Lock()
        self._character_boss_statistics_cache: dict[
            tuple[str, str, str],
            tuple[float, dict[str, CharacterBossStatisticsResponse]],
        ] = {}
        self._character_boss_statistics_cache_lock = threading.Lock()
        self._sync_uploaded_boss_catalog()

    def list_hot_bosses(self) -> list[HotBossCard]:
        now = monotonic()
        if self._hot_bosses_cache is not None:
            expires_at, cards = self._hot_bosses_cache
            if expires_at > now:
                return cards

        ranked_battles_by_boss = self._list_public_ranked_battles_by_boss()
        cards: list[HotBossCard] = []
        for boss in BOSS_SEEDS:
            ranked_boss_battles = sorted(
                ranked_battles_by_boss.get(boss.slug, []),
                key=self._public_battle_sort_key,
            )
            boss_battles = ranked_boss_battles[:3]
            top_runs: list[HotBossRun] = []
            for index, battle in enumerate(boss_battles):
                lead = self._get_lead_participant(battle, "dps")
                lead_entries = [entry for entry in battle.roster if lead.characterKey and entry.characterKey == lead.characterKey]
                lead_entry = lead_entries[0] if len(lead_entries) == 1 else None
                top_runs.append(HotBossRun(
                    battleId=battle.battle_id,
                    durationMs=battle.duration_ms,
                    uploaderNickname=battle.uploader_nickname,
                    characterKey=lead.characterKey,
                    characterName=lead.characterName,
                    characterProfession=lead.characterProfession,
                    characterAvatarUrl=lead.characterAvatarUrl,
                    characterPotential=lead_entry.characterPotential if lead_entry else None,
                    weaponRefine=lead_entry.weapon.weaponRefine if lead_entry and lead_entry.weapon else None,
                    scorePercent=self._rank_score_percent(index + 1, len(ranked_boss_battles)),
                    contractTagScore=_visible_contract_tag_score(battle),
                    contractTags=_visible_contract_tags(battle),
                ))
            cards.append(
                HotBossCard(
                    bossSlug=boss.slug,
                    bossKey=boss.key,
                    bossName=boss.name,
                    dungeonName=boss.dungeon_name,
                    topSpeedRuns=top_runs,
                )
            )
        self._hot_bosses_cache = (now + HOT_BOSSES_CACHE_TTL_SECONDS, cards)
        return cards

    def _invalidate_public_caches(self) -> None:
        # Import lazily: profile aggregation depends on this service's catalog.
        from app.services.character_profile import character_profile_service
        character_profile_service.invalidate()
        self._hot_bosses_cache = None
        with self._battle_list_cache_lock:
            self._battle_list_cache.clear()
        ranked_cache_lock = getattr(self, "_public_ranked_cache_lock", None)
        if ranked_cache_lock is not None:
            with ranked_cache_lock:
                self._public_ranked_cache.clear()
        directory_cache_lock = getattr(self, "_public_account_directory_cache_lock", None)
        if directory_cache_lock is not None:
            with directory_cache_lock:
                self._public_account_directory_cache = None
        cache_lock = getattr(self, "_character_statistics_cache_lock", None)
        if cache_lock is not None:
            with cache_lock:
                self._character_statistics_cache.clear()
        character_boss_cache_lock = getattr(
            self,
            "_character_boss_statistics_cache_lock",
            None,
        )
        if character_boss_cache_lock is not None:
            with character_boss_cache_lock:
                self._character_boss_statistics_cache.clear()

    def get_boss_rankings(self, boss_slug: str, metric: Metric) -> BossRankingResponse:
        if boss_slug in REVIEWED_NON_RANKING_DUNGEON_SLUGS or _is_inactive_season_tower_stage(boss_slug):
            raise AppError(status_code=404, code="boss_not_found", message="未找到对应首领。")
        boss = self.bosses_by_slug.get(boss_slug)
        if boss is None:
            self._sync_uploaded_boss_catalog()
            boss = self.bosses_by_slug.get(boss_slug)
        if boss is None:
            raise AppError(status_code=404, code="boss_not_found", message="未找到对应首领。")

        rows: list[BossRankingRow] = []
        ranked_battles = [
            battle
            for battle in sorted(
                self._iter_boss_battles(boss_slug, metric=metric),
                key=lambda item: self._boss_ranking_sort_key(item, metric),
            )
        ]
        total_ranked_battles = len(ranked_battles)

        for index, battle in enumerate(ranked_battles, start=1):
            lead = self._get_lead_participant(battle, metric)
            # The roster is already normalized to the battle-detail display
            # scale (refinement 1..6). Bind by exact character identity only.
            lead_entries = [entry for entry in battle.roster if lead.characterKey and entry.characterKey == lead.characterKey]
            lead_entry = lead_entries[0] if len(lead_entries) == 1 else None
            rows.append(
                BossRankingRow(
                    rank=index,
                    scorePercent=self._rank_score_percent(index, total_ranked_battles),
                    battleId=battle.battle_id,
                    battleEndAt=battle.battle_end_at.isoformat(),
                    characterKey=lead.characterKey,
                    characterName=lead.characterName,
                    characterProfession=lead.characterProfession or _get_character_profession(lead.characterName),
                    characterAvatarUrl=lead.characterAvatarUrl,
                    characterPotential=lead_entry.characterPotential if lead_entry else None,
                    weaponRefine=lead_entry.weapon.weaponRefine if lead_entry and lead_entry.weapon else None,
                    accountId=battle.uploader_user_id,
                    accountDisplayName=lead.accountDisplayName,
                    dps=battle.total_dps,
                    rdps=self._battle_metric_value(battle, "rdps"),
                    durationMs=battle.duration_ms,
                    rosterSummary=[entry.characterName for entry in battle.roster],
                    rosterEntries=[
                        BossRankingRosterEntry(
                            characterKey=entry.characterKey,
                            characterName=entry.characterName,
                            profession=entry.characterProfession or _get_character_profession(entry.characterName),
                            avatarUrl=entry.characterAvatarUrl,
                            characterPotential=entry.characterPotential,
                            weaponRefine=entry.weapon.weaponRefine if entry.weapon else None,
                        )
                        for entry in battle.roster
                    ],
                    contractTagScore=_visible_contract_tag_score(battle),
                    contractTags=_visible_contract_tags(battle),
                )
            )

        return BossRankingResponse(
            bossSlug=boss.slug,
            bossName=boss.name,
            dungeonName=boss.dungeon_name,
            metric=metric,
            professionGroups=self._build_profession_groups(rows),
            rows=rows,
        )

    def get_boss_character_statistics(
        self,
        boss_slug: str,
        metric: Metric,
        time_range: CharacterStatisticsRange,
        potential_range: CharacterStatisticsPotential = "all",
    ) -> BossCharacterStatisticsResponse:
        if boss_slug == CRISIS_CONTRACT_BOSS_SLUG:
            raise AppError(
                status_code=404,
                code="character_statistics_not_available",
                message="危机合约不提供角色统计。",
            )
        if boss_slug in REVIEWED_NON_RANKING_DUNGEON_SLUGS or _is_inactive_season_tower_stage(boss_slug):
            raise AppError(status_code=404, code="boss_not_found", message="未找到对应首领。")
        boss = self.bosses_by_slug.get(boss_slug)
        if boss is None:
            self._sync_uploaded_boss_catalog()
            boss = self.bosses_by_slug.get(boss_slug)
        if boss is None:
            raise AppError(status_code=404, code="boss_not_found", message="未找到对应首领。")

        return self._get_character_statistics_response(
            scope="boss",
            boss_slug=boss.slug,
            boss_name=boss.name,
            dungeon_name=boss.dungeon_name,
            metric=metric,
            time_range=time_range,
            potential_range=potential_range,
        )

    def get_all_character_statistics(
        self,
        metric: Metric,
        time_range: CharacterStatisticsRange,
        potential_range: CharacterStatisticsPotential = "all",
    ) -> BossCharacterStatisticsResponse:
        return self._get_character_statistics_response(
            scope="all",
            boss_slug="all",
            boss_name="全部副本",
            dungeon_name="角色统计总榜",
            metric=metric,
            time_range=time_range,
            potential_range=potential_range,
        )

    @staticmethod
    def _character_statistics_bosses() -> tuple[BossSeed, ...]:
        return tuple(
            boss
            for boss in BOSS_SEEDS
            if boss.slug != CRISIS_CONTRACT_BOSS_SLUG
            and not _is_inactive_season_tower_stage(boss.slug)
        )

    def _ensure_character_boss_statistics_cache(self) -> None:
        if not hasattr(self, "_character_boss_statistics_cache"):
            self._character_boss_statistics_cache = {}
        if not hasattr(self, "_character_boss_statistics_cache_lock"):
            self._character_boss_statistics_cache_lock = threading.Lock()

    def get_character_boss_statistics(
        self,
        character_key: str,
        metric: Metric,
        time_range: CharacterStatisticsRange,
        potential_range: CharacterStatisticsPotential = "all",
    ) -> CharacterBossStatisticsResponse:
        catalog_entry = next(
            (
                entry
                for entry in SIX_STAR_STATISTICS_CATALOG
                if entry.char_id == character_key
            ),
            None,
        )
        if catalog_entry is None:
            raise AppError(
                status_code=404,
                code="character_not_found",
                message="未找到对应的六星角色。",
            )

        self._ensure_character_boss_statistics_cache()
        cache_key = (metric, time_range, potential_range)
        now = monotonic()
        with self._character_boss_statistics_cache_lock:
            cached = self._character_boss_statistics_cache.get(cache_key)
            if cached is not None and cached[0] > now:
                return cached[1][character_key]
            responses = self._compute_all_character_boss_statistics(
                metric=metric,
                time_range=time_range,
                potential_range=potential_range,
            )
            self._character_boss_statistics_cache[cache_key] = (
                monotonic() + CHARACTER_BOSS_STATISTICS_CACHE_TTL_SECONDS,
                responses,
            )
            return responses[character_key]

    def _compute_all_character_boss_statistics(
        self,
        *,
        metric: Metric,
        time_range: CharacterStatisticsRange,
        potential_range: CharacterStatisticsPotential,
    ) -> dict[str, CharacterBossStatisticsResponse]:
        bosses = self._character_statistics_bosses()
        samples_by_boss, _ = self._collect_character_statistics_samples_by_boss(
            boss_slug=None,
            metric=metric,
            time_range=time_range,
            potential_range=potential_range,
        )
        rows_by_character: dict[str, list[CharacterBossStatisticsRow]] = {
            entry.char_id: [] for entry in SIX_STAR_STATISTICS_CATALOG
        }
        for boss in bosses:
            boss_samples = samples_by_boss.get(boss.slug) or {
                entry.char_id: [] for entry in SIX_STAR_STATISTICS_CATALOG
            }
            character_rows = self._build_character_statistics_rows(boss_samples)
            ranked_character_count = sum(
                row.rank is not None for row in character_rows
            )
            for row in character_rows:
                rows_by_character[row.characterKey].append(
                    CharacterBossStatisticsRow(
                        bossSlug=boss.slug,
                        bossName=boss.name,
                        dungeonName=boss.dungeon_name,
                        rank=row.rank,
                        rankedCharacterCount=ranked_character_count,
                        sampleCount=row.sampleCount,
                        normalSampleCount=row.normalSampleCount,
                        outlierCount=row.outlierCount,
                        insufficientSamples=row.insufficientSamples,
                        lowerWhisker=row.lowerWhisker,
                        p10=row.p10,
                        p25=row.p25,
                        median=row.median,
                        p75=row.p75,
                        p90=row.p90,
                        upperWhisker=row.upperWhisker,
                        maximum=row.maximum,
                        outliers=row.outliers,
                    )
                )

        return {
            entry.char_id: CharacterBossStatisticsResponse(
                characterKey=entry.char_id,
                characterName=entry.name,
                characterProfession=entry.profession,
                characterAvatarUrl=entry.avatar_url,
                metric=metric,
                range=time_range,
                potential=potential_range,
                minimumSampleCount=CHARACTER_STATISTICS_MIN_SAMPLES,
                includedBossCount=len(bosses),
                totalSampleCount=sum(
                    row.sampleCount for row in rows_by_character[entry.char_id]
                ),
                totalOutlierCount=sum(
                    row.outlierCount for row in rows_by_character[entry.char_id]
                ),
                rows=rows_by_character[entry.char_id],
            )
            for entry in SIX_STAR_STATISTICS_CATALOG
        }

    def _ensure_character_statistics_cache(self) -> None:
        # 一些单元测试用 __new__ 构造轻量 service；运行时正常走 __init__。
        if not hasattr(self, "_character_statistics_cache"):
            self._character_statistics_cache = {}
        if not hasattr(self, "_character_statistics_cache_lock"):
            self._character_statistics_cache_lock = threading.Lock()

    def _get_character_statistics_response(
        self,
        *,
        scope: Literal["boss", "all"],
        boss_slug: str,
        boss_name: str,
        dungeon_name: str,
        metric: Metric,
        time_range: CharacterStatisticsRange,
        potential_range: CharacterStatisticsPotential,
    ) -> BossCharacterStatisticsResponse:
        self._ensure_character_statistics_cache()
        cache_key = (scope, boss_slug, metric, f"{time_range}:{potential_range}")
        now = monotonic()
        with self._character_statistics_cache_lock:
            cached = self._character_statistics_cache.get(cache_key)
            if cached is not None and cached[0] > now:
                return cached[1]
            # 锁覆盖整个冷缓存构建：同一进程内其它统计请求等待本次结果，
            # 不会并发扫描/解析 5k+ 场记录形成内存雪崩。
            response = self._compute_character_statistics_response(
                scope=scope,
                boss_slug=boss_slug,
                boss_name=boss_name,
                dungeon_name=dungeon_name,
                metric=metric,
                time_range=time_range,
                potential_range=potential_range,
            )
            self._character_statistics_cache[cache_key] = (
                monotonic() + CHARACTER_STATISTICS_CACHE_TTL_SECONDS,
                response,
            )
            return response

    def _query_character_statistics_metadata(
        self,
        *,
        boss_slug: str | None,
    ) -> list[CharacterStatisticsBattleRow]:
        statement = select(
            UploadedBattleRecord.id,
            UploadedBattleRecord.boss_key,
            UploadedBattleRecord.boss_slug,
            UploadedBattleRecord.battle_end_at,
            UploadedBattleRecord.duration_ms,
            UploadedBattleRecord.total_damage,
            UploadedBattleRecord.parser_version,
            UploadedBattleRecord.rules_version,
            UploadedBattleRecord.rdps_strict_ok,
            UploadedBattleRecord.timer_integrity_ok,
        ).where(
            UploadedBattleRecord.status == "valid",
            UploadedBattleRecord.visibility == "public",
            UploadedBattleRecord.clear_flag.is_(True),
            UploadedBattleRecord.official_timer_end_seen.is_(True),
            UploadedBattleRecord.time_source == "game_timer",
            UploadedBattleRecord.loadout_fallback_used.is_not(True),
        )
        if boss_slug is not None:
            statement = statement.where(or_(*self._uploaded_battle_boss_filter_conditions(boss_slug)))
        try:
            with SessionLocal() as session:
                rows = session.execute(statement).all()
        except OperationalError:
            return []
        result: list[CharacterStatisticsBattleRow] = []
        for row in rows:
            try:
                battle_end_at = datetime.fromisoformat(row.battle_end_at)
            except (TypeError, ValueError):
                continue
            result.append(
                CharacterStatisticsBattleRow(
                    battle_id=row.id,
                    boss_slug=self._canonical_uploaded_boss_slug(row.boss_slug, row.boss_key),
                    battle_end_at=battle_end_at,
                    duration_ms=row.duration_ms,
                    total_damage=row.total_damage,
                    parser_version=row.parser_version,
                    rules_version=row.rules_version,
                    rdps_strict_ok=row.rdps_strict_ok,
                    timer_integrity_ok=row.timer_integrity_ok,
                )
            )
        return result

    def _iter_character_statistics_payloads(
        self,
        battle_ids: set[str],
    ) -> Iterator[tuple[str, str, str]]:
        if not battle_ids:
            return
        statement = (
            select(
                UploadedBattleRecord.id,
                UploadedBattleRecord.roster_json,
                UploadedBattleRecord.participants_json,
            )
            .where(UploadedBattleRecord.id.in_(battle_ids))
            .execution_options(stream_results=True, yield_per=250)
        )
        try:
            with SessionLocal() as session:
                for row in session.execute(statement):
                    yield row.id, row.roster_json, row.participants_json
        except OperationalError:
            return

    def _collect_character_statistics_samples_by_boss(
        self,
        *,
        boss_slug: str | None,
        metric: Metric,
        time_range: CharacterStatisticsRange,
        potential_range: CharacterStatisticsPotential,
    ) -> tuple[
        dict[str, dict[str, list[float]]],
        dict[str, set[str]],
    ]:
        included_slugs = {
            boss.slug
            for boss in BOSS_SEEDS
            if boss.slug != CRISIS_CONTRACT_BOSS_SLUG
            and not _is_inactive_season_tower_stage(boss.slug)
        }
        metadata = self._query_character_statistics_metadata(boss_slug=boss_slug)
        range_days = CHARACTER_STATISTICS_RANGE_DAYS.get(time_range)
        cutoff = datetime.now(UTC) - timedelta(days=range_days) if range_days is not None else None
        candidates: list[CharacterStatisticsBattleRow] = []
        for battle in metadata:
            battle_end_at = battle.battle_end_at
            if battle_end_at.tzinfo is None:
                battle_end_at = battle_end_at.replace(tzinfo=UTC)
            if boss_slug is None and battle.boss_slug not in included_slugs:
                continue
            if cutoff is not None and battle_end_at < cutoff:
                continue
            if metric == "rdps" and not _rdps_ranking_eligible(
                battle.parser_version, battle.rules_version, battle.rdps_strict_ok,
            ):
                continue
            if _parser_version_number(battle.parser_version) >= 47 and battle.timer_integrity_ok is not True:
                continue
            candidates.append(battle)

        incomplete_ids = self._incomplete_clear_battle_ids(candidates)
        eligible_ids = {
            battle.battle_id for battle in candidates if battle.battle_id not in incomplete_ids
        }
        boss_slug_by_id = {
            battle.battle_id: battle.boss_slug
            for battle in candidates
            if battle.battle_id in eligible_ids
        }
        samples_by_boss: dict[str, dict[str, list[float]]] = {}
        sampled_battle_ids_by_boss: dict[str, set[str]] = {}

        for battle_id, roster_json, participants_json in self._iter_character_statistics_payloads(eligible_ids):
            resolved_boss_slug = boss_slug_by_id.get(battle_id)
            if resolved_boss_slug is None:
                continue
            try:
                roster_items = json.loads(roster_json)
                participant_items = json.loads(participants_json)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(roster_items, list) or not isinstance(participant_items, list):
                continue
            potential_by_character: dict[str, int | None] = {}
            for item in roster_items:
                if not isinstance(item, dict):
                    continue
                entry = _get_character_entry(item.get("characterName"), item.get("characterKey"))
                if entry is None:
                    continue
                character_key = _statistics_character_key(entry)
                potential = item.get("characterPotential")
                if not isinstance(potential, int) or isinstance(potential, bool):
                    potential = None
                if (
                    character_key not in potential_by_character
                    or potential_by_character[character_key] is None
                ):
                    potential_by_character[character_key] = potential
            for item in participant_items:
                if not isinstance(item, dict):
                    continue
                entry = _get_character_entry(item.get("characterName"), item.get("characterKey"))
                if entry is None or entry.rarity != 6:
                    continue
                try:
                    dps = float(item.get("dps") or 0)
                    rdps = float(item.get("rdps") or 0)
                except (TypeError, ValueError):
                    continue
                if dps <= 0 and rdps <= 0:
                    continue
                character_key = _statistics_character_key(entry)
                potential = potential_by_character.get(character_key)
                if potential is None or not 0 <= potential <= 5:
                    continue
                if potential_range == "0" and potential != 0:
                    continue
                if potential_range == "1-5" and not 1 <= potential <= 5:
                    continue
                samples_by_character = samples_by_boss.setdefault(
                    resolved_boss_slug,
                    {catalog_entry.char_id: [] for catalog_entry in SIX_STAR_STATISTICS_CATALOG},
                )
                if character_key not in samples_by_character:
                    continue
                samples_by_character[character_key].append(dps if metric == "dps" else rdps)
                sampled_battle_ids_by_boss.setdefault(resolved_boss_slug, set()).add(battle_id)

        return samples_by_boss, sampled_battle_ids_by_boss

    def _compute_character_statistics_response(
        self,
        *,
        scope: Literal["boss", "all"],
        boss_slug: str,
        boss_name: str,
        dungeon_name: str,
        metric: Metric,
        time_range: CharacterStatisticsRange,
        potential_range: CharacterStatisticsPotential,
    ) -> BossCharacterStatisticsResponse:
        # 保留轻量内存 service 的测试/调用兼容；生产实例不在 __dict__ 覆盖该方法。
        if "_list_uploaded_battle_summaries" in self.__dict__:
            included_slugs = {
                boss.slug
                for boss in BOSS_SEEDS
                if boss.slug != CRISIS_CONTRACT_BOSS_SLUG
                and not _is_inactive_season_tower_stage(boss.slug)
            }
            completed_battles = [
                battle
                for battle in self._list_uploaded_battle_summaries(
                    boss_slug=boss_slug if scope == "boss" else None
                )
                if (scope == "boss" or battle.boss_slug in included_slugs)
                and battle.status == "valid"
                and battle.visibility == "public"
                and battle.clear_flag
                and battle.official_timer_end_seen is True
                and battle.time_source == "game_timer"
            ]
            return self._build_character_statistics_response(
                scope=scope,
                boss_slug=boss_slug,
                boss_name=boss_name,
                dungeon_name=dungeon_name,
                metric=metric,
                time_range=time_range,
                potential_range=potential_range,
                completed_battles=completed_battles,
                included_boss_count=(
                    1 if scope == "boss" else len({battle.boss_slug for battle in completed_battles})
                ),
            )
        samples_by_boss, sampled_battle_ids_by_boss = (
            self._collect_character_statistics_samples_by_boss(
                boss_slug=boss_slug if scope == "boss" else None,
                metric=metric,
                time_range=time_range,
                potential_range=potential_range,
            )
        )
        samples_by_character: dict[str, list[float]] = {
            entry.char_id: [] for entry in SIX_STAR_STATISTICS_CATALOG
        }
        if scope == "boss":
            selected_samples = samples_by_boss.get(boss_slug)
            if selected_samples is not None:
                samples_by_character = selected_samples
            sampled_battle_ids = set(sampled_battle_ids_by_boss.get(boss_slug) or set())
            sampled_boss_slugs = {boss_slug} if sampled_battle_ids else set()
        else:
            sampled_battle_ids = set()
            sampled_boss_slugs = set(samples_by_boss)
            for sampled_boss_slug, boss_samples in samples_by_boss.items():
                sampled_battle_ids.update(
                    sampled_battle_ids_by_boss.get(sampled_boss_slug) or set()
                )
                for character_key, values in boss_samples.items():
                    samples_by_character[character_key].extend(values)

        return self._build_character_statistics_rows_response(
            scope=scope,
            boss_slug=boss_slug,
            boss_name=boss_name,
            dungeon_name=dungeon_name,
            metric=metric,
            time_range=time_range,
            potential_range=potential_range,
            samples_by_character=samples_by_character,
            sampled_battle_ids=sampled_battle_ids,
            sampled_boss_slugs=sampled_boss_slugs,
            included_boss_count=1 if scope == "boss" else 0,
        )

    def _build_character_statistics_response(
        self,
        *,
        scope: Literal["boss", "all"],
        boss_slug: str,
        boss_name: str,
        dungeon_name: str,
        metric: Metric,
        time_range: CharacterStatisticsRange,
        potential_range: CharacterStatisticsPotential,
        completed_battles: list[DemoBattle],
        included_boss_count: int,
    ) -> BossCharacterStatisticsResponse:
        range_days = CHARACTER_STATISTICS_RANGE_DAYS.get(time_range)
        cutoff = datetime.now(UTC) - timedelta(days=range_days) if range_days is not None else None
        incomplete_ids = self._incomplete_clear_battle_ids(completed_battles)
        eligible_battles: list[DemoBattle] = []
        for battle in completed_battles:
            battle_end_at = battle.battle_end_at
            if battle_end_at.tzinfo is None:
                battle_end_at = battle_end_at.replace(tzinfo=UTC)
            if battle.battle_id in incomplete_ids:
                continue
            if cutoff is not None and battle_end_at < cutoff:
                continue
            if metric == "rdps" and not _rdps_ranking_eligible(
                battle.parser_version, battle.rules_version, battle.rdps_strict_ok,
            ):
                continue
            if battle.loadout_fallback_used is True:
                continue
            if (
                _parser_version_number(battle.parser_version) >= 47
                and battle.timer_integrity_ok is not True
            ):
                continue
            eligible_battles.append(battle)

        samples_by_character: dict[str, list[float]] = {
            entry.char_id: [] for entry in SIX_STAR_STATISTICS_CATALOG
        }
        sampled_battle_ids: set[str] = set()
        sampled_boss_slugs: set[str] = set()
        for battle in eligible_battles:
            potential_by_character: dict[str, int | None] = {}
            for roster_entry in battle.roster:
                roster_catalog_entry = _get_character_entry(
                    roster_entry.characterName,
                    roster_entry.characterKey,
                )
                if roster_catalog_entry is None:
                    continue
                roster_character_key = _statistics_character_key(roster_catalog_entry)
                if (
                    roster_character_key not in potential_by_character
                    or potential_by_character[roster_character_key] is None
                ):
                    potential_by_character[roster_character_key] = roster_entry.characterPotential
            for participant in battle.participants:
                entry = _get_character_entry(participant.characterName, participant.characterKey)
                if entry is None or entry.rarity != 6:
                    continue
                if float(participant.dps) <= 0 and float(participant.rdps) <= 0:
                    continue
                character_key = _statistics_character_key(entry)
                if character_key not in samples_by_character:
                    continue
                character_potential = potential_by_character.get(character_key)
                if character_potential is None or not 0 <= character_potential <= 5:
                    continue
                if potential_range == "0" and character_potential != 0:
                    continue
                if potential_range == "1-5" and not 1 <= character_potential <= 5:
                    continue
                value = participant.dps if metric == "dps" else participant.rdps
                samples_by_character[character_key].append(float(value))
                sampled_battle_ids.add(battle.battle_id)
                sampled_boss_slugs.add(battle.boss_slug)

        return self._build_character_statistics_rows_response(
            scope=scope,
            boss_slug=boss_slug,
            boss_name=boss_name,
            dungeon_name=dungeon_name,
            metric=metric,
            time_range=time_range,
            potential_range=potential_range,
            samples_by_character=samples_by_character,
            sampled_battle_ids=sampled_battle_ids,
            sampled_boss_slugs=sampled_boss_slugs,
            included_boss_count=included_boss_count,
        )

    def _build_character_statistics_rows_response(
        self,
        *,
        scope: Literal["boss", "all"],
        boss_slug: str,
        boss_name: str,
        dungeon_name: str,
        metric: Metric,
        time_range: CharacterStatisticsRange,
        potential_range: CharacterStatisticsPotential,
        samples_by_character: dict[str, list[float]],
        sampled_battle_ids: set[str],
        sampled_boss_slugs: set[str],
        included_boss_count: int,
    ) -> BossCharacterStatisticsResponse:
        ranked_rows = self._build_character_statistics_rows(samples_by_character)
        return BossCharacterStatisticsResponse(
            scope=scope,
            bossSlug=boss_slug,
            bossName=boss_name,
            dungeonName=dungeon_name,
            metric=metric,
            range=time_range,
            potential=potential_range,
            includedBossCount=(
                len(sampled_boss_slugs)
                if scope == "all"
                else included_boss_count
            ),
            minimumSampleCount=CHARACTER_STATISTICS_MIN_SAMPLES,
            eligibleBattleCount=len(sampled_battle_ids),
            totalSampleCount=sum(len(values) for values in samples_by_character.values()),
            totalOutlierCount=sum(row.outlierCount for row in ranked_rows),
            rows=ranked_rows,
        )

    def _build_character_statistics_rows(
        self,
        samples_by_character: dict[str, list[float]],
    ) -> list[BossCharacterStatisticsRow]:
        pooled_values = sorted(value for values in samples_by_character.values() for value in values)
        global_outlier_bounds = _iqr_bounds(
            pooled_values,
            CHARACTER_STATISTICS_GLOBAL_OUTLIER_IQR_MULTIPLIER,
        )

        rows: list[BossCharacterStatisticsRow] = []
        for entry in SIX_STAR_STATISTICS_CATALOG:
            values = sorted(samples_by_character.get(entry.char_id) or [])
            sample_count = len(values)
            globally_normal_values = values
            if global_outlier_bounds is not None:
                global_lower, global_upper = global_outlier_bounds
                globally_normal_values = [value for value in values if global_lower <= value <= global_upper]
            local_outlier_bounds = _iqr_bounds(
                globally_normal_values,
                CHARACTER_STATISTICS_LOCAL_OUTLIER_IQR_MULTIPLIER,
            )
            normal_values: list[float] = []
            outlier_values: list[float] = []
            for value in values:
                is_outlier = False
                if global_outlier_bounds is not None:
                    global_lower, global_upper = global_outlier_bounds
                    is_outlier = value < global_lower or value > global_upper
                if not is_outlier and local_outlier_bounds is not None:
                    local_lower, local_upper = local_outlier_bounds
                    is_outlier = value < local_lower or value > local_upper
                (outlier_values if is_outlier else normal_values).append(value)
            # 极端情况下不能把整名角色清空；保留原始样本并取消离群拆分。
            if values and not normal_values:
                normal_values = values
                outlier_values = []
            outlier_counts: dict[float, int] = {}
            for value in outlier_values:
                rounded_value = round(value, 2)
                outlier_counts[rounded_value] = outlier_counts.get(rounded_value, 0) + 1
            normal_sample_count = len(normal_values)
            rows.append(
                BossCharacterStatisticsRow(
                    characterKey=entry.char_id,
                    characterName=entry.name,
                    characterProfession=entry.profession,
                    characterAvatarUrl=entry.avatar_url,
                    sampleCount=sample_count,
                    normalSampleCount=normal_sample_count,
                    outlierCount=len(outlier_values),
                    insufficientSamples=normal_sample_count < CHARACTER_STATISTICS_MIN_SAMPLES,
                    lowerWhisker=round(normal_values[0], 2) if normal_values else None,
                    p10=round(_linear_percentile(normal_values, 0.10), 2) if normal_values else None,
                    p25=round(_linear_percentile(normal_values, 0.25), 2) if normal_values else None,
                    median=round(_linear_percentile(normal_values, 0.50), 2) if normal_values else None,
                    p75=round(_linear_percentile(normal_values, 0.75), 2) if normal_values else None,
                    p90=round(_linear_percentile(normal_values, 0.90), 2) if normal_values else None,
                    upperWhisker=round(normal_values[-1], 2) if normal_values else None,
                    maximum=round(values[-1], 2) if values else None,
                    outliers=[
                        BossCharacterStatisticsOutlier(value=value, count=count)
                        for value, count in sorted(outlier_counts.items())
                    ],
                )
            )

        rows.sort(
            key=lambda row: (
                row.insufficientSamples,
                -(row.median if row.median is not None else -1.0),
                -(row.maximum if row.maximum is not None else -1.0),
                row.characterName,
            )
        )
        rank = 0
        ranked_rows: list[BossCharacterStatisticsRow] = []
        for row in rows:
            if not row.insufficientSamples:
                rank += 1
                row = row.model_copy(update={"rank": rank})
            ranked_rows.append(row)
        return ranked_rows

    def get_battle_detail(
        self,
        battle_id: str,
        viewer_user_id: str | None,
        *,
        viewer_is_admin: bool = False,
    ) -> BattleDetailResponse:
        battle = self._get_uploaded_battle(battle_id, include_uncleared=True)
        if battle is None:
            raise AppError(status_code=404, code="battle_not_found", message="未找到这条战斗记录。")
        is_uploader = viewer_user_id == battle.uploader_user_id
        # 有链接即可查看：公开且已完成的战斗人人可看，不再要求上榜（best-per-account）。
        # 未完成战斗仍仅上传者/管理员可见。
        if not (battle.visibility == "public" and battle.clear_flag) and not (is_uploader or viewer_is_admin):
            raise AppError(status_code=404, code="battle_not_found", message="未找到这条战斗记录。")
        normalized_role_skill_stats = [
            RoleSkillStatResponse(
                characterName=stat.characterName,
                skillKey=stat.skillKey,
                skillName=_normalize_skill_display_name(stat.skillName, stat.skillKey),
                castCount=stat.castCount,
                totalDamage=stat.totalDamage,
                avgDamage=stat.avgDamage,
                maxDamage=stat.maxDamage,
            )
            for stat in battle.role_skill_stats
        ]
        timeline_events = _enrich_timeline_event_damage_identities(battle.timeline_events)
        # Read-time display metadata only. Official ownership paths define groups;
        # equal translated names do not establish that two skills belong together.
        skill_display_entries: dict[str, Any] = {}
        skill_display_conflicts: dict[str, Any] = {}
        try:
            skill_display_catalog = json.loads(
                (REPO_ROOT / "data" / "local_static" / "skill" / "display_name_catalog.json").read_text(
                    encoding="utf-8"
                )
            )
            decoder = skill_display_catalog.get("decoder") if isinstance(skill_display_catalog, dict) else None
            if (
                isinstance(skill_display_catalog, dict)
                and skill_display_catalog.get("schemaVersion") == 1
                and skill_display_catalog.get("source") == "official_CharGrowth_I18n_typed_skill_display"
                and isinstance(decoder, dict)
                and all(decoder.get(key) == value for key, value in _CURRENT_TYPED_RESOURCE_BINDING.items())
                and isinstance(skill_display_catalog.get("entries"), dict)
                and isinstance(skill_display_catalog.get("conflicts"), dict)
            ):
                skill_display_entries = skill_display_catalog["entries"]
                skill_display_conflicts = skill_display_catalog["conflicts"]
        except (OSError, ValueError):
            pass

        roster_keys_by_name: dict[str, set[str]] = {}
        roster_names_by_key: dict[str, set[str]] = {}
        for entry in battle.roster:
            if entry.characterKey and entry.characterName:
                roster_keys_by_name.setdefault(entry.characterName, set()).add(entry.characterKey)
                roster_names_by_key.setdefault(entry.characterKey, set()).add(entry.characterName)
        stat_identity_counts: dict[tuple[str, str | None], int] = {}
        for stat in normalized_role_skill_stats:
            identity = (stat.characterName, stat.skillKey)
            stat_identity_counts[identity] = stat_identity_counts.get(identity, 0) + 1

        hit_values_by_identity: dict[tuple[str, str], list[int]] = {}
        conflicting_hit_identities: set[tuple[str, str]] = set()
        for event in battle.timeline_events:
            if event.laneType != "skill" or not event.eventKey or not event.value or event.value <= 0:
                continue
            source_name = event.sourceCharacterName
            source_key = event.sourceCharacterKey
            key_names = roster_names_by_key.get(source_key, set()) if source_key else set()
            if source_name:
                name_keys = roster_keys_by_name.get(source_name, set())
                if len(name_keys) != 1 or (source_key and name_keys != {source_key}):
                    conflicting_hit_identities.add((source_name, event.eventKey))
                    for key_name in key_names:
                        conflicting_hit_identities.add((key_name, event.eventKey))
                    continue
            elif len(key_names) == 1:
                source_name = next(iter(key_names))
            else:
                continue
            hit_values_by_identity.setdefault((source_name, event.eventKey), []).append(event.value)

        for stat in normalized_role_skill_stats:
            character_keys = roster_keys_by_name.get(stat.characterName, set())
            character_key = next(iter(character_keys)) if len(character_keys) == 1 else None
            entry = skill_display_entries.get(stat.skillKey) if stat.skillKey else None
            if (
                character_key
                and isinstance(entry, dict)
                and stat.skillKey not in skill_display_conflicts
                and entry.get("proofKind") in {"official_char_growth_skill_list", "typed_enabled_creation_path"}
                and isinstance(entry.get("paths"), list)
                and isinstance(entry.get("displayName"), str)
                and entry["displayName"].strip()
            ):
                group_ids = {
                    path["skillGroupId"]
                    for path in entry["paths"]
                    if isinstance(path, dict)
                    and path.get("characterId") == character_key
                    and isinstance(path.get("skillGroupId"), str)
                    and path["skillGroupId"]
                }
                if len(group_ids) == 1:
                    stat.displayGroupKey = f"official:{character_key}:{next(iter(group_ids))}"
                    stat.displayGroupName = entry["displayName"]
            identity = (stat.characterName, stat.skillKey)
            hit_values = hit_values_by_identity.get(identity, [])
            if (
                character_key
                and stat_identity_counts[identity] == 1
                and identity not in conflicting_hit_identities
                and hit_values
                and sum(hit_values) == stat.totalDamage
            ):
                # These are individual positive damage events, never inferred casts.
                stat.hitCount = len(hit_values)
                stat.maxHit = max(hit_values)
        battle_response = BattleResponse(
            id=battle.battle_id,
            uploaderUserId=battle.uploader_user_id,
            visibility="public",
            status="valid",
            dungeonName=battle.dungeon_name,
            bossKey=battle.boss_key,
            bossName=battle.boss_name,
            battleStartAt=battle.battle_start_at.isoformat(),
            battleEndAt=battle.battle_end_at.isoformat(),
            durationMs=battle.duration_ms,
            clearFlag=battle.clear_flag,
            totalDamage=battle.total_damage,
            totalDps=battle.total_dps,
            roster=battle.roster,
            parserVersion=battle.parser_version,
            rulesVersion=battle.rules_version,
            battleFingerprint=battle.battle_fingerprint,
            timeSource=battle.time_source,
            timelineZeroSource=battle.timeline_zero_source,
            timerStartSeen=battle.timer_start_seen,
            timerEndSeen=battle.timer_end_seen,
            officialTimerStartSeen=battle.official_timer_start_seen,
            officialTimerEndSeen=battle.official_timer_end_seen,
            officialTimerObservedElapsedMs=battle.official_timer_observed_elapsed_ms,
            packetTimerElapsedMs=battle.packet_timer_elapsed_ms,
            timerIntegrityOk=battle.timer_integrity_ok,
            timerIntegrityReasons=battle.timer_integrity_reasons,
            timerStartInferred=battle.timer_start_inferred,
            timerWindowValid=battle.timer_window_valid,
            rdpsPreflightOk=battle.rdps_preflight_ok,
            rdpsStrictOk=battle.rdps_strict_ok,
            rdpsRankingEligible=battle.boss_slug not in REVIEWED_NON_RANKING_DUNGEON_SLUGS and _rdps_ranking_eligible(
                battle.parser_version, battle.rules_version, battle.rdps_strict_ok,
            ),
            rdpsPreflightBlockerCount=battle.rdps_preflight_blocker_count,
            bossIdentitySource=battle.boss_identity_source,
            dungeonContextId=battle.dungeon_context_id,
            dungeonIdentitySource=battle.dungeon_identity_source,
            loadoutFallbackUsed=battle.loadout_fallback_used,
            contractTagScore=_visible_contract_tag_score(battle),
            contractTags=_visible_contract_tags(battle),
        )
        integrity_payload = {
            "battle": battle_response.model_dump(mode="json"),
            "participants": [participant.model_dump(mode="json") for participant in battle.participants],
            "characterStates": _build_character_states_from_detail(battle.roster, timeline_events),
            "timelineEvents": [event.model_dump(mode="json") for event in timeline_events],
            "roleSkillStats": [stat.model_dump(mode="json") for stat in normalized_role_skill_stats],
        }
        return BattleDetailResponse(
            battle=battle_response,
            participants=battle.participants,
            characterStates=integrity_payload["characterStates"],
            timelineEvents=timeline_events,
            roleSkillStats=normalized_role_skill_stats,
            integrity=BattleIntegrityResponse(**build_battle_integrity(integrity_payload)),
            viewerCapabilities=ViewerCapabilitiesResponse(
                isUploader=is_uploader,
                isAdmin=viewer_is_admin,
                canDelete=is_uploader or viewer_is_admin,
                canViewVersions=is_uploader or viewer_is_admin,
            ),
        )

    def get_share_summary(self, battle_id: str) -> ShareSummaryResponse:
        battle = self._get_battle_or_raise(battle_id)
        return ShareSummaryResponse(
            battleId=battle.battle_id,
            bossName=battle.boss_name,
            dungeonName=battle.dungeon_name,
            durationMs=battle.duration_ms,
            uploaderNickname=battle.uploader_nickname,
            rosterSummary=[entry.characterName for entry in battle.roster],
            contractTagScore=_visible_contract_tag_score(battle),
            contractTags=_visible_contract_tags(battle),
        )

    def list_user_battles(self, viewer_user_id: str | None) -> UserBattlesResponse:
        if viewer_user_id is None:
            raise AppError(status_code=401, code="session_invalid", message="请先登录。")

        with SessionLocal() as session:
            # load_only：排除 timeline_events_json 等大 JSON 列。整行加载时高产用户一次
            # /mine 就是数百 MB 分配，glibc 高水位不还 OS——2026-07-05 OOM 事故修正之一。
            rows = session.execute(
                select(UploadedBattleRecord)
                .options(
                    load_only(
                        UploadedBattleRecord.id,
                        UploadedBattleRecord.boss_slug,
                        UploadedBattleRecord.boss_key,
                        UploadedBattleRecord.boss_name,
                        UploadedBattleRecord.dungeon_name,
                        UploadedBattleRecord.battle_end_at,
                        UploadedBattleRecord.created_at,
                        UploadedBattleRecord.duration_ms,
                        UploadedBattleRecord.total_damage,
                        UploadedBattleRecord.total_dps,
                        UploadedBattleRecord.status,
                        UploadedBattleRecord.parser_version,
                        UploadedBattleRecord.rules_version,
                        UploadedBattleRecord.roster_json,
                        UploadedBattleRecord.contract_tag_score,
                        UploadedBattleRecord.contract_tags_json,
                    )
                )
                .where(UploadedBattleRecord.uploader_user_id == viewer_user_id)
                .order_by(UploadedBattleRecord.battle_end_at.desc(), UploadedBattleRecord.created_at.desc())
            ).scalars()

            battles = []
            for row in rows:
                contract_tag_score, contract_tags = _contract_fields_for_boss(
                    row.boss_slug,
                    row.contract_tag_score,
                    _load_contract_tags(row.contract_tags_json),
                )
                battles.append(
                    UserBattleSummaryResponse(
                        id=row.id,
                        bossName=self._display_boss_name(
                            boss_slug=row.boss_slug,
                            boss_key=row.boss_key,
                            boss_name=row.boss_name,
                            dungeon_name=row.dungeon_name,
                        ),
                        dungeonName=row.dungeon_name,
                        battleEndAt=row.battle_end_at,
                        createdAt=row.created_at,
                        durationMs=row.duration_ms,
                        totalDamage=row.total_damage,
                        totalDps=row.total_dps,
                        status=row.status,
                        parserVersion=row.parser_version,
                        rulesVersion=row.rules_version,
                        rosterSummary=[
                            str(item.get("characterName"))
                            for item in json.loads(row.roster_json)
                            if isinstance(item, dict) and item.get("characterName")
                        ],
                        contractTagScore=contract_tag_score,
                        contractTags=contract_tags,
                    )
                )

        return UserBattlesResponse(
            battles=battles,
            rankings=self._list_user_boss_rankings(viewer_user_id),
        )

    @staticmethod
    def _normalize_public_account_search_text(value: str) -> str:
        return normalize("NFKC", value).strip()

    def _ensure_public_account_directory_cache(self) -> None:
        # 兼容用 __new__ 构造的轻量测试 service。
        if not hasattr(self, "_public_account_directory_cache"):
            self._public_account_directory_cache = None
        if not hasattr(self, "_public_account_directory_cache_lock"):
            self._public_account_directory_cache_lock = threading.Lock()

    def _get_public_account_directory(self) -> tuple[PublicAccountSearchEntryResponse, ...]:
        self._ensure_public_account_directory_cache()
        now = monotonic()
        with self._public_account_directory_cache_lock:
            cached = self._public_account_directory_cache
            if cached is not None and cached[0] > now:
                return cached[1]
            directory = self._build_public_account_directory()
            self._public_account_directory_cache = (
                monotonic() + PUBLIC_ACCOUNT_DIRECTORY_CACHE_TTL_SECONDS,
                directory,
            )
            return directory

    def _build_public_account_directory(self) -> tuple[PublicAccountSearchEntryResponse, ...]:
        """从正式公开榜单的轻量元数据生成可搜索账号索引。

        不读取 auth_users，也不加载 roster/participants JSON；昵称只来自最终可在
        公开榜单出现的上传记录，因此不会泄露私有记录或尚未公开的新昵称。
        """
        metadata_by_boss: dict[str, list[DemoBattle]] = {}
        for battle in self._query_public_ranking_metadata(boss_slug=None):
            if not battle.uploader_user_id.strip():
                continue
            if _is_inactive_season_tower_stage(battle.boss_slug):
                continue
            if battle.boss_slug not in self.bosses_by_slug:
                continue
            metadata_by_boss.setdefault(battle.boss_slug, []).append(battle)

        latest_ranked_by_account: dict[str, DemoBattle] = {}
        for group in metadata_by_boss.values():
            for battle in self._best_public_battles_by_account(group):
                account_id = battle.uploader_user_id.strip()
                display_name = battle.uploader_nickname.strip()
                if not account_id or not display_name:
                    continue
                current = latest_ranked_by_account.get(account_id)
                if current is None or battle.battle_end_at > current.battle_end_at:
                    latest_ranked_by_account[account_id] = battle

        entries = [
            PublicAccountSearchEntryResponse(
                accountId=account_id,
                accountDisplayName=battle.uploader_nickname.strip(),
            )
            for account_id, battle in latest_ranked_by_account.items()
        ]
        return tuple(
            sorted(
                entries,
                key=lambda entry: (
                    self._normalize_public_account_search_text(entry.accountDisplayName).casefold(),
                    entry.accountId,
                ),
            )
        )

    def search_public_accounts(self, *, query: str, limit: int = 10) -> PublicAccountSearchResponse:
        display_query = self._normalize_public_account_search_text(query)
        if not 2 <= len(display_query) <= 64:
            raise AppError(
                status_code=422,
                code="invalid_account_search_query",
                message="账号搜索关键词去除首尾空格后长度必须为 2 到 64 个字符。",
            )
        if not 1 <= limit <= 20:
            raise AppError(
                status_code=422,
                code="invalid_account_search_limit",
                message="账号搜索结果数量必须为 1 到 20。",
            )

        folded_query = display_query.casefold()
        matches: list[tuple[int, str, PublicAccountSearchEntryResponse]] = []
        for entry in self._get_public_account_directory():
            normalized_name = self._normalize_public_account_search_text(entry.accountDisplayName)
            folded_name = normalized_name.casefold()
            if folded_query not in folded_name:
                continue
            if folded_name == folded_query:
                match_rank = 0
            elif folded_name.startswith(folded_query):
                match_rank = 1
            else:
                match_rank = 2
            matches.append((match_rank, folded_name, entry))
        matches.sort(key=lambda item: (item[0], item[1], item[2].accountId))

        accounts = [item[2] for item in matches]
        return PublicAccountSearchResponse(
            query=display_query,
            hasMore=len(accounts) > limit,
            accounts=accounts[:limit],
        )

    def get_public_user_rankings(self, account_id: str) -> PublicUserRankingsResponse:
        normalized_account_id = account_id.strip()
        if not normalized_account_id:
            raise AppError(status_code=404, code="account_not_found", message="未找到这个账号。")

        display_name = self._get_public_user_display_name(normalized_account_id)
        rankings = self._list_user_boss_rankings(normalized_account_id)
        if display_name is None and not rankings:
            raise AppError(status_code=404, code="account_not_found", message="未找到这个账号。")

        return PublicUserRankingsResponse(
            accountId=normalized_account_id,
            accountDisplayName=display_name or normalized_account_id,
            rankings=rankings,
        )

    def _get_public_user_display_name(self, account_id: str) -> str | None:
        return next(
            (
                entry.accountDisplayName
                for entry in self._get_public_account_directory()
                if entry.accountId == account_id
            ),
            None,
        )

    def _list_user_boss_rankings(self, viewer_user_id: str) -> list[UserBossRankingResponse]:
        self._sync_uploaded_boss_catalog()
        ranked_battles_by_boss = self._list_public_ranked_battles_by_boss()
        rows: list[UserBossRankingResponse] = []
        for boss in sorted(self.bosses_by_slug.values(), key=lambda item: (item.dungeon_name, item.name, item.slug)):
            ranked_battles = ranked_battles_by_boss.get(boss.slug) or []
            if not ranked_battles:
                continue
            total_ranked_battles = len(ranked_battles)
            for index, battle in enumerate(ranked_battles, start=1):
                if battle.uploader_user_id != viewer_user_id:
                    continue
                rows.append(
                    UserBossRankingResponse(
                        bossSlug=boss.slug,
                        bossName=battle.boss_name,
                        dungeonName=battle.dungeon_name,
                        battleId=battle.battle_id,
                        rank=index,
                        scorePercent=self._rank_score_percent(index, total_ranked_battles),
                        durationMs=battle.duration_ms,
                        totalDps=battle.total_dps,
                        battleEndAt=battle.battle_end_at.isoformat(),
                        rosterSummary=[entry.characterName for entry in battle.roster],
                        rosterEntries=[
                            BossRankingRosterEntry(
                                characterKey=entry.characterKey,
                                characterName=entry.characterName,
                                profession=entry.characterProfession or _get_character_profession(entry.characterName),
                                avatarUrl=entry.characterAvatarUrl,
                                characterPotential=entry.characterPotential,
                                weaponRefine=entry.weapon.weaponRefine if entry.weapon else None,
                            )
                            for entry in battle.roster
                        ],
                        contractTagScore=_visible_contract_tag_score(battle),
                        contractTags=_visible_contract_tags(battle),
                    )
                )
                break
        return sorted(rows, key=lambda row: (row.rank, row.durationMs, row.bossName))

    def delete_battle(
        self,
        battle_id: str,
        viewer_user_id: str | None,
        *,
        viewer_is_admin: bool = False,
    ) -> None:
        if viewer_user_id is None:
            raise AppError(status_code=401, code="session_invalid", message="请先登录。")

        with SessionLocal() as session:
            row = session.get(UploadedBattleRecord, battle_id)
            if row is None or row.status == "deleted":
                raise AppError(status_code=404, code="battle_not_found", message="未找到这条战斗记录。")
            if viewer_user_id != row.uploader_user_id and not viewer_is_admin:
                raise AppError(status_code=403, code="forbidden", message="你没有权限删除这条记录。")
            row.status = "deleted"
            session.commit()
        self._invalidate_public_caches()

    def find_duplicate_battle(self, battle_fingerprint: str) -> DemoBattle | None:
        with SessionLocal() as session:
            row = session.execute(
                select(UploadedBattleRecord).where(
                    UploadedBattleRecord.battle_fingerprint == battle_fingerprint,
                    UploadedBattleRecord.status == "valid",
                )
            ).scalar_one_or_none()
        if row is None:
            return None
        return self._record_to_uploaded_battle(row)

    def create_uploaded_battle(
        self,
        payload: UploadBattleRequest,
        *,
        uploader_user_id: str,
        uploader_nickname: str,
    ) -> DemoBattle:
        if not payload.battle.roster:
            raise AppError(status_code=422, code="battle_payload_invalid", message="战斗阵容不能为空。")
        if not payload.participants:
            raise AppError(status_code=422, code="battle_payload_invalid", message="参与角色列表不能为空。")

        if (
            payload.battle.dungeonIdentitySource != "dungeon_context"
            or not str(payload.battle.dungeonContextId or "").strip()
            or payload.battle.dungeonKey in {"", UNKNOWN_DUNGEON_KEY}
        ):
            raise AppError(
                status_code=422,
                code="battle_dungeon_unverified",
                message="缺少可映射的官方场地 ID，已停止归属副本；请保留调试日志检查 dungeonId/gameId 资源映射。",
            )

        duplicate = self.find_duplicate_battle(payload.battle.battleFingerprint)
        if duplicate is not None and duplicate.uploader_user_id != uploader_user_id:
            raise AppError(status_code=409, code="battle_duplicate", message="这条战斗记录已存在。")

        boss_slug = self._resolve_boss_slug(payload.battle.dungeonKey)
        display_boss_name = self._display_boss_name(
            boss_slug=boss_slug,
            boss_key=payload.battle.bossKey,
            boss_name=payload.battle.bossName,
            dungeon_name=payload.battle.dungeonName,
        )
        self._ensure_boss_catalog_entry(
            boss_key=payload.battle.bossKey,
            boss_slug=boss_slug,
            boss_name=display_boss_name,
            dungeon_name=payload.battle.dungeonName,
            roster_keys=[entry.characterKey for entry in payload.participants],
            uploader_nickname=uploader_nickname,
            duration_ms=payload.battle.durationMs,
            lead_dps=max((entry.dps for entry in payload.participants), default=0.0),
            lead_rdps=max((entry.rdps for entry in payload.participants), default=0.0),
        )

        battle_id = duplicate.battle_id if duplicate is not None else f"btl_upload_{uuid4().hex[:12]}"
        roster = [
            BattleRosterEntryResponse(
                slot=entry.slot,
                characterKey=entry.characterKey,
                characterName=entry.characterName,
                characterProfession=_get_character_profession(entry.characterName, entry.characterKey),
                characterAvatarUrl=_get_character_avatar_url(entry.characterName, entry.characterKey),
                characterElement=_get_character_element(entry.characterName, entry.characterKey),
                accountDisplayName=uploader_nickname,
                characterLevel=entry.characterLevel,
                characterPotential=entry.characterPotential,
                weapon=BattleRosterWeaponResponse(
                    weaponTemplate=entry.weapon.weaponTemplate,
                    weaponName=entry.weapon.weaponName,
                    weaponLevel=entry.weapon.weaponLevel,
                    weaponRefine=entry.weapon.weaponRefine,
                    iconUrl=_weapon_icon_url(entry.weapon.iconUrl, entry.weapon.weaponTemplate),
                    skills=[
                        BattleRosterWeaponSkillResponse(
                            skillKey=skill.skillKey,
                            level=skill.level,
                            potentialLevel=skill.potentialLevel,
                        )
                        for skill in entry.weapon.skills
                    ],
                )
                if entry.weapon is not None
                else None,
                equips=[
                    BattleRosterEquipResponse(
                        slot=equip.slot,
                        itemId=equip.itemId,
                        pieceName=equip.pieceName,
                        suitName=equip.suitName,
                        partName=equip.partName,
                        iconUrl=_normalize_public_asset_url(equip.iconUrl),
                        enhanceLevels=equip.enhanceLevels,
                        stats=equip.stats,
                    )
                    for equip in entry.equips
                ],
                skills=[
                    BattleRosterSkillResponse(skillKey=skill.skillKey, level=skill.level)
                    for skill in entry.skills
                ],
            )
            for entry in payload.battle.roster
        ]
        roster = [_enrich_roster_entry_character_identity(entry) for entry in roster]
        participants = [
            BattleParticipantResponse(
                characterKey=entry.characterKey,
                characterName=entry.characterName,
                characterProfession=_get_character_profession(entry.characterName, entry.characterKey),
                characterAvatarUrl=_get_character_avatar_url(entry.characterName, entry.characterKey),
                characterElement=_get_character_element(entry.characterName, entry.characterKey),
                accountDisplayName=uploader_nickname,
                totalDamage=entry.totalDamage,
                dps=entry.dps,
                rdps=entry.rdps,
                maxHit=entry.maxHit,
                critRate=entry.critRate,
            )
            for entry in payload.participants
        ]
        participants = [_enrich_participant_character_identity(entry) for entry in participants]
        roster = _filter_roster_to_participants(roster, participants)
        timeline_events = [
            TimelineEventResponse(
                tsMsFromStart=event.tsMsFromStart,
                laneType=event.laneType,
                sourceCharacterKey=event.sourceCharacterKey,
                sourceCharacterName=event.sourceCharacterName,
                targetCharacterKey=event.targetCharacterKey,
                targetCharacterName=event.targetCharacterName,
                targetPlayerKey=event.targetPlayerKey,
                targetEnemyKey=event.targetEnemyKey,
                eventKey=event.eventKey,
                eventGroupKey=event.eventGroupKey,
                eventName=event.eventName,
                value=event.value,
                damageElement=event.damageElement,
                damageSchool=event.damageSchool,
                poiseDamage=_build_poise_damage_response(event.poiseDamage),
                rdpsContributions=_build_rdps_contribution_responses(event.rdpsContributions),
                hitContext=event.hitContext,
                durationMs=event.durationMs,
                actualStartMsFromStart=event.actualStartMsFromStart,
                actualEndMsFromStart=event.actualEndMsFromStart,
                actualDurationMs=event.actualDurationMs,
                effects=[
                    TimelineEventResponse.BuffEffectResponse.model_validate(item.model_dump())
                    for item in event.effects
                ],
                dynamicEffects=[
                    TimelineEventResponse.BuffEffectResponse.model_validate(item.model_dump())
                    for item in event.dynamicEffects
                ],
                important=event.important,
            )
            for event in payload.timelineEvents
        ]
        timeline_events = [_normalize_timeline_event_character_identity(event) for event in timeline_events]
        timeline_events = _enrich_timeline_event_damage_identities(timeline_events)
        timeline_events, participants = _normalize_off_participant_timeline_contributions(
            timeline_events,
            participants,
            duration_ms=payload.battle.durationMs,
        )
        role_skill_stats = [
            RoleSkillStatResponse(
                characterName=stat.characterName,
                skillKey=stat.skillKey,
                skillName=_normalize_skill_display_name(stat.skillName, stat.skillKey),
                castCount=stat.castCount,
                totalDamage=stat.totalDamage,
                avgDamage=stat.avgDamage,
                maxDamage=stat.maxDamage,
            )
            for stat in payload.roleSkillStats
        ]
        role_skill_stats = [_normalize_role_skill_stat_character_identity(stat) for stat in role_skill_stats]
        clear_flag = self._validate_upload_clear_flag(
            requested_clear_flag=payload.battle.clearFlag,
            dungeon_key=payload.battle.dungeonKey,
            boss_slug=boss_slug,
            total_damage=payload.battle.totalDamage,
            timeline_events=timeline_events,
            timer_end_seen=payload.battle.timerEndSeen,
            official_timer_end_seen=payload.battle.officialTimerEndSeen,
            time_source=payload.battle.timeSource,
            timer_window_valid=payload.battle.timerWindowValid,
        )
        self._validate_upload_timer_integrity(
            boss_slug=boss_slug,
            parser_version=payload.battle.parserVersion,
            clear_flag=clear_flag,
            duration_ms=payload.battle.durationMs,
            official_observed_elapsed_ms=payload.battle.officialTimerObservedElapsedMs,
            packet_elapsed_ms=payload.battle.packetTimerElapsedMs,
            timer_integrity_ok=payload.battle.timerIntegrityOk,
            timer_integrity_reasons=payload.battle.timerIntegrityReasons,
            timer_evidence=payload.battle.timerEvidence,
            dungeon_context_id=payload.battle.dungeonContextId,
        )
        contract_tags = _normalize_contract_tags(payload.battle.contractTags)
        contract_tag_score = _contract_tag_score(payload.battle.contractTagScore, contract_tags)
        contract_tag_score, contract_tags = _contract_fields_for_boss(boss_slug, contract_tag_score, contract_tags)
        created = DemoBattle(
            battle_id=battle_id,
            uploader_user_id=uploader_user_id,
            uploader_nickname=uploader_nickname,
            boss_key=payload.battle.bossKey,
            boss_slug=boss_slug,
            boss_name=display_boss_name,
            dungeon_name=payload.battle.dungeonName,
            duration_ms=payload.battle.durationMs,
            battle_start_at=payload.battle.battleStartAt,
            battle_end_at=payload.battle.battleEndAt,
            total_damage=payload.battle.totalDamage,
            total_dps=payload.battle.totalDps,
            roster=roster,
            participants=participants,
            timeline_events=timeline_events,
            role_skill_stats=role_skill_stats,
            parser_version=payload.battle.parserVersion,
            rules_version=payload.battle.rulesVersion,
            battle_fingerprint=payload.battle.battleFingerprint,
            time_source=payload.battle.timeSource,
            timeline_zero_source=payload.battle.timelineZeroSource,
            timer_start_seen=payload.battle.timerStartSeen,
            timer_end_seen=payload.battle.timerEndSeen,
            official_timer_start_seen=payload.battle.officialTimerStartSeen,
            official_timer_end_seen=payload.battle.officialTimerEndSeen,
            official_timer_observed_elapsed_ms=payload.battle.officialTimerObservedElapsedMs,
            packet_timer_elapsed_ms=payload.battle.packetTimerElapsedMs,
            timer_integrity_ok=payload.battle.timerIntegrityOk,
            timer_integrity_reasons=list(payload.battle.timerIntegrityReasons),
            timer_evidence=payload.battle.timerEvidence,
            timer_start_inferred=payload.battle.timerStartInferred,
            timer_window_valid=payload.battle.timerWindowValid,
            rdps_preflight_ok=payload.battle.rdpsPreflightOk,
            rdps_strict_ok=payload.battle.rdpsStrictOk,
            rdps_preflight_blocker_count=payload.battle.rdpsPreflightBlockerCount,
            boss_identity_source=payload.battle.bossIdentitySource,
            dungeon_context_id=payload.battle.dungeonContextId,
            dungeon_identity_source=payload.battle.dungeonIdentitySource,
            loadout_fallback_used=payload.battle.loadoutFallbackUsed,
            contract_tag_score=contract_tag_score,
            contract_tags=contract_tags,
            clear_flag=clear_flag,
            visibility=duplicate.visibility if duplicate is not None else "public",
            casts=(
                [cast.model_dump(mode="json") for cast in payload.casts]
                if payload.casts
                else None
            ),
        )
        self._persist_uploaded_battle(created)
        self._invalidate_public_caches()
        return created

    def _iter_boss_battles(self, boss_slug: str, *, metric: Metric = "dps") -> list[DemoBattle]:
        return self._list_public_ranked_battles(boss_slug=boss_slug, metric=metric)

    def _participant_metric_value(
        self,
        participant: BattleParticipantResponse,
        metric: Metric,
    ) -> float:
        return participant.dps if metric == "dps" else participant.rdps

    def _battle_metric_value(self, battle: DemoBattle, metric: Metric) -> float:
        if metric == "dps":
            return battle.total_dps
        return sum(participant.rdps for participant in battle.participants)

    def _validate_upload_clear_flag(
        self,
        *,
        requested_clear_flag: bool,
        dungeon_key: str = "",
        boss_slug: str,
        total_damage: int,
        timeline_events: list[TimelineEventResponse],
        timer_end_seen: bool | None = None,
        official_timer_end_seen: bool | None = None,
        time_source: str | None = None,
        timer_window_valid: bool | None = None,
    ) -> bool:
        if not requested_clear_flag:
            return False
        if dungeon_key.startswith("indie_battletower") and official_timer_end_seen is not True:
            return False
        if timer_window_valid is False:
            return False
        if official_timer_end_seen is True and time_source != "game_timer":
            return False
        if not self._has_clear_completion_signal(
            timer_end_seen=timer_end_seen,
            official_timer_end_seen=official_timer_end_seen,
        ):
            return False
        if boss_slug == NEPHIS_BOSS_SLUG and not self._nephis_timeline_has_full_clear_damage(
            total_damage=total_damage,
            timeline_events=timeline_events,
        ):
            return False
        return True

    def _validate_upload_timer_integrity(
        self,
        *,
        boss_slug: str,
        parser_version: str,
        clear_flag: bool,
        duration_ms: int,
        official_observed_elapsed_ms: int | None,
        packet_elapsed_ms: int | None,
        timer_integrity_ok: bool | None,
        timer_integrity_reasons: list[str],
        timer_evidence: dict[str, Any] | None = None,
        dungeon_context_id: str | None = None,
    ) -> None:
        if not clear_flag:
            return
        competitive_slugs = {
            boss.slug
            for boss in BOSS_SEEDS
            if boss.slug != CRISIS_CONTRACT_BOSS_SLUG
        }
        if boss_slug not in competitive_slugs:
            return
        reasons = list(timer_integrity_reasons)
        reasons.extend(validate_timer_evidence(
            timer_evidence, duration_ms=duration_ms, game_id=dungeon_context_id))
        if timer_integrity_ok is not True:
            reasons.append("timer_integrity_not_confirmed")
        if reasons:
            raise AppError(
                status_code=422,
                code="timer_integrity_failed",
                message="官方挑战计时证据缺失或存在冲突，请更新客户端后重新解析并上传。",
            )

    @staticmethod
    def _has_clear_completion_signal(
        *,
        timer_end_seen: bool | None,
        official_timer_end_seen: bool | None,
    ) -> bool:
        if official_timer_end_seen is True or timer_end_seen is True:
            return True
        if official_timer_end_seen is False or timer_end_seen is False:
            return False
        return False

    @staticmethod
    def _nephis_timeline_has_full_clear_damage(
        *,
        total_damage: int,
        timeline_events: list[TimelineEventResponse],
    ) -> bool:
        damage_by_target: dict[str, int] = {}
        for event in timeline_events:
            if event.laneType != "skill" or event.value is None or event.value <= 0:
                continue
            target_key = event.targetEnemyKey or event.targetCharacterKey
            if not target_key:
                continue
            damage_by_target[target_key] = damage_by_target.get(target_key, 0) + event.value

        has_phase_one = any(damage_by_target.get(key, 0) > 0 for key in NEPHIS_PHASE_ONE_KEYS)
        has_phase_two = any(damage_by_target.get(key, 0) > 0 for key in NEPHIS_PHASE_TWO_KEYS)
        if has_phase_one and has_phase_two:
            return True

        return any(
            abs(total_damage - expected_total) <= NEPHIS_FULL_CLEAR_TOTAL_TOLERANCE
            for expected_total in NEPHIS_FULL_CLEAR_TOTALS
        )

    def _get_lead_participant(
        self,
        battle: DemoBattle,
        metric: Metric,
    ) -> BattleParticipantResponse:
        return max(
            battle.participants,
            key=lambda participant: (
                self._participant_metric_value(participant, metric),
                participant.totalDamage,
                participant.characterName,
            ),
        )

    def _get_battle_or_raise(self, battle_id: str) -> DemoBattle:
        # 有链接即可查看/分享：公开且已完成即可，不再要求上榜（此前非最佳公开战斗无法分享）。
        uploaded_battle = self._get_uploaded_battle(battle_id, include_uncleared=True)
        if uploaded_battle is not None and uploaded_battle.visibility == "public" and uploaded_battle.clear_flag:
            return uploaded_battle
        raise AppError(status_code=404, code="battle_not_found", message="未找到这条战斗记录。")

    def get_battle_export(self, battle_id: str) -> dict:
        """排轴导出（外部工具消费的公共只读契约，schema v1）。
        门禁 = valid + visibility=public（"知道链接即可导"，与玩家把战斗链接
        粘进排轴器的工作流一致）；不要求上榜——排轴要导的是玩家指定的那一把，
        不是账号历史最佳（2026-07-05 用户新战斗被 best-per-account gate 挡住后放宽）。
        老 payload（无 casts）明确拒绝，不让消费端处理两种数据形态。"""
        battle = self._get_uploaded_battle(battle_id, include_uncleared=True)
        if battle is None or battle.visibility != "public":
            raise AppError(status_code=404, code="battle_not_found", message="未找到这条战斗记录。")
        if not battle.casts:
            raise AppError(
                status_code=422,
                code="battle_export_unsupported",
                message="这条战斗由旧版客户端上传，不包含完整施法序列，无法导出。",
            )
        roster_export = []
        for entry in battle.roster:
            weapon = entry.weapon
            roster_export.append(
                {
                    "slot": entry.slot,
                    "characterKey": entry.characterKey,
                    "characterName": entry.characterName,
                    "characterLevel": entry.characterLevel,
                    "characterPotential": entry.characterPotential,
                    "weapon": (
                        {
                            "weaponTemplate": weapon.weaponTemplate,
                            "weaponName": weapon.weaponName,
                            "weaponLevel": weapon.weaponLevel,
                            "weaponRefine": weapon.weaponRefine,
                            "skills": [
                                {
                                    "skillKey": skill.skillKey,
                                    "level": skill.level,
                                    "potentialLevel": skill.potentialLevel,
                                }
                                for skill in weapon.skills
                            ],
                        }
                        if weapon is not None
                        else None
                    ),
                    "equips": [
                        {
                            "slot": equip.slot,
                            "itemId": equip.itemId,
                            "pieceName": equip.pieceName,
                            "suitName": equip.suitName,
                            "partName": equip.partName,
                            "enhanceLevels": equip.enhanceLevels,
                            "stats": equip.stats,
                        }
                        for equip in entry.equips
                    ],
                    "skills": [
                        {"skillKey": skill.skillKey, "level": skill.level}
                        for skill in entry.skills
                    ],
                }
            )
        return {
            "schemaVersion": 1,
            "battleId": battle.battle_id,
            "parserVersion": battle.parser_version,
            "rulesVersion": battle.rules_version,
            "dungeon": {
                "dungeonSlug": battle.boss_slug,
                "dungeonName": battle.dungeon_name,
                "bossKey": battle.boss_key,
                "bossName": battle.boss_name,
            },
            "durationMs": battle.duration_ms,
            "battleStartAt": battle.battle_start_at.isoformat(),
            "battleEndAt": battle.battle_end_at.isoformat(),
            "roster": roster_export,
            "casts": battle.casts,
        }

    @staticmethod
    def _score_percent(value: float, top_value: float) -> int:
        if top_value <= 0:
            return 0
        return max(0, min(100, int(round((value / top_value) * 100))))

    @staticmethod
    def _speed_score_percent(duration_ms: int, fastest_duration_ms: int) -> int:
        if duration_ms <= 0 or fastest_duration_ms <= 0:
            return 0
        return max(0, min(100, int(round((fastest_duration_ms / duration_ms) * 100))))

    @staticmethod
    def _rank_score_percent(rank: int, total_count: int) -> int:
        if rank <= 0 or total_count <= 0:
            return 0
        clamped_rank = min(rank, total_count)
        percentile = ((total_count - clamped_rank + 1) / total_count) * 100
        return max(1, min(100, int(round(percentile))))

    @staticmethod
    def _build_profession_groups(rows: list[BossRankingRow]) -> list[BossProfessionGroup]:
        counts_by_profession: dict[str, dict[str, int]] = {profession: {} for profession in PROFESSION_ORDER}
        for row in rows:
            for entry in row.rosterEntries:
                if entry.profession not in counts_by_profession:
                    continue
                counts_by_profession[entry.profession][entry.characterName] = (
                    counts_by_profession[entry.profession].get(entry.characterName, 0) + 1
                )

        groups: list[BossProfessionGroup] = []
        for profession in PROFESSION_ORDER:
            profession_counts = counts_by_profession[profession]
            total = sum(profession_counts.values())
            entries = [
                BossProfessionUsageEntry(
                    characterKey=_get_character_entry(character_name=character_name).char_id
                    if _get_character_entry(character_name=character_name)
                    else None,
                    characterName=character_name,
                    avatarUrl=_get_character_avatar_url(character_name=character_name),
                    usagePercent=round((count / total) * 100, 1) if total else 0.0,
                )
                for character_name, count in sorted(
                    profession_counts.items(),
                    key=lambda item: (-item[1], item[0]),
                )
            ]
            groups.append(BossProfessionGroup(profession=profession, entries=entries))
        return groups

    def _build_demo_battles(self) -> dict[str, DemoBattle]:
        battles: dict[str, DemoBattle] = {}
        duration_offsets = (-18000, -7000, 9000)
        damage_multipliers = (1.04, 1.0, 0.96)

        for boss in BOSS_SEEDS:
            for index in range(3):
                battle_id = f"btl_{boss.slug}_{index + 1}"
                uploader_nickname = boss.uploader_names[index]
                uploader_user_id = f"usr_{boss.slug}_{index + 1}"
                duration_ms = boss.base_duration_ms + duration_offsets[index]
                battle_start_at = datetime(2026, 4, 20, 20, 0, tzinfo=UTC) + timedelta(minutes=index * 11)
                battle_end_at = battle_start_at + timedelta(milliseconds=duration_ms)
                participants = self._build_participants(
                    roster=boss.roster,
                    uploader_nickname=uploader_nickname,
                    duration_ms=duration_ms,
                    base_dps=boss.base_dps,
                    base_rdps=boss.base_rdps,
                    multiplier=damage_multipliers[index],
                )
                total_damage = sum(participant.totalDamage for participant in participants)
                total_dps = round(total_damage / (duration_ms / 1000), 2)
                roster = [
                    BattleRosterEntryResponse(
                        slot=slot,
                        characterKey=character_key,
                        characterName=_get_character_name(character_key),
                        characterProfession=_get_character_profession(character_key=character_key),
                        characterAvatarUrl=_get_character_avatar_url(character_key=character_key),
                        characterElement=_get_character_element(character_key=character_key),
                        accountDisplayName=uploader_nickname,
                    )
                    for slot, character_key in enumerate(boss.roster, start=1)
                ]
                battles[battle_id] = DemoBattle(
                    battle_id=battle_id,
                    uploader_user_id=uploader_user_id,
                    uploader_nickname=uploader_nickname,
                    boss_key=boss.key,
                    boss_slug=boss.slug,
                    boss_name=boss.name,
                    dungeon_name=boss.dungeon_name,
                    duration_ms=duration_ms,
                    battle_start_at=battle_start_at,
                    battle_end_at=battle_end_at,
                    total_damage=total_damage,
                    total_dps=total_dps,
                    roster=roster,
                    participants=participants,
                    timeline_events=self._build_timeline_events(boss.roster),
                    role_skill_stats=self._build_skill_stats(participants),
                    parser_version="0.1.0-dev",
                    rules_version="0.1.0-dev",
                    battle_fingerprint=sha256(f"{battle_id}:{boss.slug}:{uploader_nickname}".encode("utf-8")).hexdigest(),
                )
        return battles

    def _resolve_boss_slug(
        self,
        dungeon_key: str,
        boss_key: str | None = None,
        boss_name: str | None = None,
    ) -> str:
        # Leaderboard routing is intentionally stage-only. boss_key/boss_name
        # remain optional compatibility parameters for callers and are used
        # nowhere in this decision.
        if dungeon_key in REVIEWED_NON_RANKING_DUNGEON_SLUGS:
            return dungeon_key
        if dungeon_key in HIGH_DIFFICULTY_STAGE_SLUG_ALIASES:
            return HIGH_DIFFICULTY_STAGE_SLUG_ALIASES[dungeon_key]
        if dungeon_key in ACTIVITY_STAGE_SLUG_ALIASES:
            return ACTIVITY_STAGE_SLUG_ALIASES[dungeon_key]
        if dungeon_key in CRISIS_FRAGMENT_STAGE_SLUG_ALIASES:
            return CRISIS_FRAGMENT_STAGE_SLUG_ALIASES[dungeon_key]
        if dungeon_key.startswith("indie_battletower"):
            return dungeon_key
        if dungeon_key and dungeon_key.endswith("_s") and dungeon_key in self.bosses_by_slug:
            return dungeon_key
        if dungeon_key and dungeon_key.startswith(("indie_hard", "indie_group_")):
            return dungeon_key
        if dungeon_key and dungeon_key in self.bosses_by_slug:
            return dungeon_key
        slug = sub(r"[^a-z0-9]+", "-", dungeon_key.lower()).strip("-")
        return slug or f"boss-{uuid4().hex[:8]}"

    def _canonical_uploaded_boss_slug(self, boss_slug: str, boss_key: str) -> str:
        # Canonicalization is stage-only. boss_key remains an argument because
        # callers also use it for display, never for leaderboard attribution.
        if boss_slug in ACTIVITY_STAGE_SLUG_ALIASES:
            return ACTIVITY_STAGE_SLUG_ALIASES[boss_slug]
        if boss_slug in CRISIS_FRAGMENT_STAGE_SLUG_ALIASES:
            return CRISIS_FRAGMENT_STAGE_SLUG_ALIASES[boss_slug]
        if boss_slug in self.bosses_by_slug:
            return boss_slug
        return boss_slug

    def _display_boss_name(
        self,
        *,
        boss_slug: str,
        boss_key: str,
        boss_name: str,
        dungeon_name: str,
    ) -> str:
        canonical_slug = self._canonical_uploaded_boss_slug(boss_slug, boss_key)
        boss = self.bosses_by_slug.get(canonical_slug)
        is_high_difficulty = canonical_slug.startswith("indie_hard") and canonical_slug.endswith("_s")
        is_activity_stage = canonical_slug in ACTIVITY_CANONICAL_STAGE_SLUGS
        if boss is not None and (is_high_difficulty or is_activity_stage or _looks_like_raw_enemy_key(boss_name)):
            return boss.name
        if _looks_like_raw_enemy_key(boss_name) and dungeon_name:
            return dungeon_name
        return boss_name

    def _uploaded_battle_boss_filter_conditions(self, boss_slug: str):
        stage_aliases = [
            stage_slug
            for aliases in (CRISIS_FRAGMENT_STAGE_SLUG_ALIASES, ACTIVITY_STAGE_SLUG_ALIASES)
            for stage_slug, canonical_slug in aliases.items()
            if canonical_slug == boss_slug
        ]
        if stage_aliases:
            return [UploadedBattleRecord.boss_slug.in_([boss_slug, *stage_aliases])]
        return [UploadedBattleRecord.boss_slug == boss_slug]

    def _ensure_boss_catalog_entry(
        self,
        *,
        boss_key: str,
        boss_slug: str,
        boss_name: str,
        dungeon_name: str,
        roster_keys: list[str],
        uploader_nickname: str,
        duration_ms: int,
        lead_dps: float,
        lead_rdps: float,
    ) -> None:
        if boss_slug in REVIEWED_NON_RANKING_DUNGEON_SLUGS or _is_inactive_season_tower_stage(boss_slug):
            return
        if boss_slug in self.bosses_by_slug:
            return
        if boss_slug in LEGACY_ACTIVITY_GROUP_SLUGS:
            return
        if not _is_registrable_boss_identity(boss_slug, boss_name, dungeon_name):
            logger.warning(
                "skip boss catalog auto-registration for unidentified upload: slug=%r boss_name=%r dungeon_name=%r boss_key=%r",
                boss_slug,
                boss_name,
                dungeon_name,
                boss_key,
            )
            return
        padded_roster = tuple((roster_keys + ["unknown", "unknown", "unknown", "unknown"])[:4])
        self.bosses_by_slug[boss_slug] = BossSeed(
            key=boss_key,
            slug=boss_slug,
            name=boss_name,
            dungeon_name=dungeon_name,
            roster=padded_roster,
            uploader_names=(uploader_nickname, uploader_nickname, uploader_nickname),
            base_duration_ms=duration_ms,
            base_dps=int(lead_dps),
            base_rdps=int(lead_rdps),
        )

    def _sync_uploaded_boss_catalog(self) -> None:
        # 启动时只同步目录元数据。旧实现为了找动态场地，会读取并反序列化全部
        # roster/participants，导致空闲 API 进程也常驻数百 MB 战斗对象。
        statement = select(
            UploadedBattleRecord.boss_key,
            UploadedBattleRecord.boss_slug,
            UploadedBattleRecord.boss_name,
            UploadedBattleRecord.dungeon_name,
            UploadedBattleRecord.uploader_nickname,
            UploadedBattleRecord.duration_ms,
            UploadedBattleRecord.total_dps,
        ).where(
            UploadedBattleRecord.status == "valid",
            UploadedBattleRecord.clear_flag.is_(True),
        )
        try:
            with SessionLocal() as session:
                rows = session.execute(statement).all()
        except OperationalError:
            return

        registered: set[str] = set()
        for row in rows:
            boss_slug = self._canonical_uploaded_boss_slug(row.boss_slug, row.boss_key)
            if boss_slug in registered or boss_slug in self.bosses_by_slug:
                continue
            registered.add(boss_slug)
            self._ensure_boss_catalog_entry(
                boss_key=row.boss_key,
                boss_slug=boss_slug,
                boss_name=self._display_boss_name(
                    boss_slug=boss_slug,
                    boss_key=row.boss_key,
                    boss_name=row.boss_name,
                    dungeon_name=row.dungeon_name,
                ),
                dungeon_name=row.dungeon_name,
                roster_keys=[],
                uploader_nickname=row.uploader_nickname,
                duration_ms=row.duration_ms,
                lead_dps=row.total_dps,
                lead_rdps=0.0,
            )

    def _register_boss_from_battle(self, battle: DemoBattle) -> None:
        self._ensure_boss_catalog_entry(
            boss_key=battle.boss_key,
            boss_slug=battle.boss_slug,
            boss_name=battle.boss_name,
            dungeon_name=battle.dungeon_name,
            roster_keys=[entry.characterKey or "unknown" for entry in battle.roster],
            uploader_nickname=battle.uploader_nickname,
            duration_ms=battle.duration_ms,
            lead_dps=max((entry.dps for entry in battle.participants), default=0.0),
            lead_rdps=max((entry.rdps for entry in battle.participants), default=0.0),
        )

    def _list_uploaded_battle_summaries(self, boss_slug: str | None = None) -> list[DemoBattle]:
        try:
            return self._list_uploaded_battles(boss_slug=boss_slug, summary_only=True)
        except TypeError as exc:
            if "summary_only" not in str(exc):
                raise
            return self._list_uploaded_battles(boss_slug=boss_slug)

    def _list_uploaded_battles(self, boss_slug: str | None = None, *, summary_only: bool = False) -> list[DemoBattle]:
        cache_key = (boss_slug, summary_only)
        now = monotonic()
        with self._battle_list_cache_lock:
            cached = self._battle_list_cache.get(cache_key)
            if cached is not None and cached[0] > now:
                return cached[1]
            self._battle_list_cache.pop(cache_key, None)
            # 缓存锁同时作为单航班锁。旧实现会在冷缓存并发时让每个请求都构建
            # 一份全量 DemoBattle/Pydantic 图，短时间把 RSS 推到 MemoryMax。
            battles = self._query_uploaded_battles(boss_slug=boss_slug, summary_only=summary_only)
            self._battle_list_cache[cache_key] = (now + BATTLE_LIST_CACHE_TTL_SECONDS, battles)
            return battles

    def _query_uploaded_battles(self, boss_slug: str | None = None, *, summary_only: bool = False) -> list[DemoBattle]:
        try:
            with SessionLocal() as session:
                statement = select(UploadedBattleRecord).where(
                    UploadedBattleRecord.status == "valid",
                    UploadedBattleRecord.clear_flag.is_(True),
                )
                if summary_only:
                    statement = statement.options(
                        load_only(
                            UploadedBattleRecord.id,
                            UploadedBattleRecord.uploader_user_id,
                            UploadedBattleRecord.uploader_nickname,
                            UploadedBattleRecord.boss_key,
                            UploadedBattleRecord.boss_slug,
                            UploadedBattleRecord.boss_name,
                            UploadedBattleRecord.dungeon_name,
                            UploadedBattleRecord.duration_ms,
                            UploadedBattleRecord.battle_start_at,
                            UploadedBattleRecord.battle_end_at,
                            UploadedBattleRecord.total_damage,
                            UploadedBattleRecord.total_dps,
                            UploadedBattleRecord.clear_flag,
                            UploadedBattleRecord.roster_json,
                            UploadedBattleRecord.participants_json,
                            UploadedBattleRecord.parser_version,
                            UploadedBattleRecord.rules_version,
                            UploadedBattleRecord.battle_fingerprint,
                            UploadedBattleRecord.time_source,
                            UploadedBattleRecord.timeline_zero_source,
                            UploadedBattleRecord.timer_start_seen,
                            UploadedBattleRecord.timer_end_seen,
                            UploadedBattleRecord.official_timer_start_seen,
                            UploadedBattleRecord.official_timer_end_seen,
                            UploadedBattleRecord.official_timer_observed_elapsed_ms,
                            UploadedBattleRecord.packet_timer_elapsed_ms,
                            UploadedBattleRecord.timer_integrity_ok,
                            UploadedBattleRecord.timer_integrity_reasons_json,
                            UploadedBattleRecord.timer_start_inferred,
                            UploadedBattleRecord.timer_window_valid,
                            UploadedBattleRecord.rdps_preflight_ok,
                            UploadedBattleRecord.rdps_strict_ok,
                            UploadedBattleRecord.rdps_preflight_blocker_count,
                            UploadedBattleRecord.boss_identity_source,
                            UploadedBattleRecord.dungeon_context_id,
                            UploadedBattleRecord.dungeon_identity_source,
                            UploadedBattleRecord.loadout_fallback_used,
                            UploadedBattleRecord.contract_tag_score,
                            UploadedBattleRecord.contract_tags_json,
                            UploadedBattleRecord.visibility,
                            UploadedBattleRecord.status,
                        )
                    )
                if boss_slug is not None:
                    statement = statement.where(or_(*self._uploaded_battle_boss_filter_conditions(boss_slug)))
                rows = session.execute(statement).scalars().all()
        except OperationalError:
            return []
        return [self._record_to_uploaded_battle(row, summary_only=summary_only) for row in rows]

    def _list_public_ranked_battles(
        self,
        boss_slug: str | None = None,
        *,
        metric: Metric = "dps",
    ) -> list[DemoBattle]:
        # 轻量测试 service 会覆盖底层列表方法，继续走原有内存口径。
        if "_list_uploaded_battles" in self.__dict__:
            battles = self._list_uploaded_battle_summaries(boss_slug=boss_slug)
            if metric == "rdps":
                battles = [battle for battle in battles if _rdps_ranking_eligible(
                    battle.parser_version, battle.rules_version, battle.rdps_strict_ok,
                )]
            return self._best_public_battles_by_account(battles)

        if not hasattr(self, "_public_ranked_cache"):
            self._public_ranked_cache = {}
        if not hasattr(self, "_public_ranked_cache_lock"):
            self._public_ranked_cache_lock = threading.Lock()
        cache_key = (boss_slug, metric)
        now = monotonic()
        with self._public_ranked_cache_lock:
            cached = self._public_ranked_cache.get(cache_key)
            if cached is not None and cached[0] > now:
                return cached[1]
            ranked = self._compute_public_ranked_battles(boss_slug=boss_slug, metric=metric)
            self._public_ranked_cache[cache_key] = (
                monotonic() + BATTLE_LIST_CACHE_TTL_SECONDS,
                ranked,
            )
            return ranked

    def _compute_public_ranked_battles(
        self,
        *,
        boss_slug: str | None,
        metric: Metric,
    ) -> list[DemoBattle]:
        battles = self._query_public_ranking_metadata(boss_slug=boss_slug)
        if metric == "rdps":
            battles = [battle for battle in battles if _rdps_ranking_eligible(
                battle.parser_version, battle.rules_version, battle.rdps_strict_ok,
            )]
        if boss_slug is not None:
            ranked_metadata = self._best_public_battles_by_account(battles)
        else:
            metadata_by_boss: dict[str, list[DemoBattle]] = {}
            for battle in battles:
                metadata_by_boss.setdefault(battle.boss_slug, []).append(battle)
            ranked_metadata = [
                battle
                for group in metadata_by_boss.values()
                for battle in self._best_public_battles_by_account(group)
            ]
        hydrated = self._query_uploaded_battle_summaries_by_ids(
            {battle.battle_id for battle in ranked_metadata}
        )
        hydrated_by_id = {battle.battle_id: battle for battle in hydrated}
        return [
            hydrated_by_id[battle.battle_id]
            for battle in ranked_metadata
            if battle.battle_id in hydrated_by_id
        ]

    def _query_public_ranking_metadata(self, *, boss_slug: str | None) -> list[DemoBattle]:
        statement = select(
            UploadedBattleRecord.id,
            UploadedBattleRecord.uploader_user_id,
            UploadedBattleRecord.uploader_nickname,
            UploadedBattleRecord.boss_key,
            UploadedBattleRecord.boss_slug,
            UploadedBattleRecord.boss_name,
            UploadedBattleRecord.dungeon_name,
            UploadedBattleRecord.duration_ms,
            UploadedBattleRecord.battle_start_at,
            UploadedBattleRecord.battle_end_at,
            UploadedBattleRecord.total_damage,
            UploadedBattleRecord.total_dps,
            UploadedBattleRecord.parser_version,
            UploadedBattleRecord.rules_version,
            UploadedBattleRecord.battle_fingerprint,
            UploadedBattleRecord.time_source,
            UploadedBattleRecord.official_timer_end_seen,
            UploadedBattleRecord.official_timer_observed_elapsed_ms,
            UploadedBattleRecord.packet_timer_elapsed_ms,
            UploadedBattleRecord.timer_integrity_ok,
            UploadedBattleRecord.timer_integrity_reasons_json,
            UploadedBattleRecord.rdps_strict_ok,
            UploadedBattleRecord.contract_tag_score,
            UploadedBattleRecord.contract_tags_json,
            UploadedBattleRecord.visibility,
            UploadedBattleRecord.status,
            UploadedBattleRecord.clear_flag,
        ).where(
            UploadedBattleRecord.status == "valid",
            UploadedBattleRecord.visibility == "public",
            UploadedBattleRecord.clear_flag.is_(True),
            UploadedBattleRecord.official_timer_end_seen.is_(True),
            UploadedBattleRecord.time_source == "game_timer",
        )
        if boss_slug is not None:
            statement = statement.where(or_(*self._uploaded_battle_boss_filter_conditions(boss_slug)))
        try:
            with SessionLocal() as session:
                rows = session.execute(statement).all()
        except OperationalError:
            return []

        battles: list[DemoBattle] = []
        for row in rows:
            try:
                battle_start_at = datetime.fromisoformat(row.battle_start_at)
                battle_end_at = datetime.fromisoformat(row.battle_end_at)
            except (TypeError, ValueError):
                continue
            canonical_slug = self._canonical_uploaded_boss_slug(row.boss_slug, row.boss_key)
            contract_tag_score, contract_tags = _contract_fields_for_boss(
                canonical_slug,
                row.contract_tag_score,
                _load_contract_tags(row.contract_tags_json),
            )
            battles.append(
                DemoBattle(
                    battle_id=row.id,
                    uploader_user_id=row.uploader_user_id,
                    uploader_nickname=row.uploader_nickname,
                    boss_key=row.boss_key,
                    boss_slug=canonical_slug,
                    boss_name=row.boss_name,
                    dungeon_name=row.dungeon_name,
                    duration_ms=row.duration_ms,
                    battle_start_at=battle_start_at,
                    battle_end_at=battle_end_at,
                    total_damage=row.total_damage,
                    total_dps=row.total_dps,
                    roster=[],
                    participants=[],
                    timeline_events=[],
                    role_skill_stats=[],
                    parser_version=row.parser_version,
                    rules_version=row.rules_version,
                    battle_fingerprint=row.battle_fingerprint,
                    time_source=row.time_source,
                    official_timer_end_seen=row.official_timer_end_seen,
                    official_timer_observed_elapsed_ms=row.official_timer_observed_elapsed_ms,
                    packet_timer_elapsed_ms=row.packet_timer_elapsed_ms,
                    timer_integrity_ok=row.timer_integrity_ok,
                    timer_integrity_reasons=(
                        json.loads(row.timer_integrity_reasons_json)
                        if row.timer_integrity_reasons_json
                        else []
                    ),
                    rdps_strict_ok=row.rdps_strict_ok,
                    contract_tag_score=contract_tag_score,
                    contract_tags=contract_tags,
                    clear_flag=row.clear_flag,
                    visibility=row.visibility,
                    status=row.status,
                )
            )
        return battles

    def _query_uploaded_battle_summaries_by_ids(self, battle_ids: set[str]) -> list[DemoBattle]:
        if not battle_ids:
            return []
        try:
            with SessionLocal() as session:
                statement = (
                    select(UploadedBattleRecord)
                    .where(UploadedBattleRecord.id.in_(battle_ids))
                    .options(
                        load_only(
                            UploadedBattleRecord.id,
                            UploadedBattleRecord.uploader_user_id,
                            UploadedBattleRecord.uploader_nickname,
                            UploadedBattleRecord.boss_key,
                            UploadedBattleRecord.boss_slug,
                            UploadedBattleRecord.boss_name,
                            UploadedBattleRecord.dungeon_name,
                            UploadedBattleRecord.duration_ms,
                            UploadedBattleRecord.battle_start_at,
                            UploadedBattleRecord.battle_end_at,
                            UploadedBattleRecord.total_damage,
                            UploadedBattleRecord.total_dps,
                            UploadedBattleRecord.clear_flag,
                            UploadedBattleRecord.roster_json,
                            UploadedBattleRecord.participants_json,
                            UploadedBattleRecord.parser_version,
                            UploadedBattleRecord.rules_version,
                            UploadedBattleRecord.battle_fingerprint,
                            UploadedBattleRecord.time_source,
                            UploadedBattleRecord.timeline_zero_source,
                            UploadedBattleRecord.timer_start_seen,
                            UploadedBattleRecord.timer_end_seen,
                            UploadedBattleRecord.official_timer_start_seen,
                            UploadedBattleRecord.official_timer_end_seen,
                            UploadedBattleRecord.official_timer_observed_elapsed_ms,
                            UploadedBattleRecord.packet_timer_elapsed_ms,
                            UploadedBattleRecord.timer_integrity_ok,
                            UploadedBattleRecord.timer_integrity_reasons_json,
                            UploadedBattleRecord.timer_start_inferred,
                            UploadedBattleRecord.timer_window_valid,
                            UploadedBattleRecord.rdps_preflight_ok,
                            UploadedBattleRecord.rdps_strict_ok,
                            UploadedBattleRecord.rdps_preflight_blocker_count,
                            UploadedBattleRecord.boss_identity_source,
                            UploadedBattleRecord.dungeon_context_id,
                            UploadedBattleRecord.dungeon_identity_source,
                            UploadedBattleRecord.loadout_fallback_used,
                            UploadedBattleRecord.contract_tag_score,
                            UploadedBattleRecord.contract_tags_json,
                            UploadedBattleRecord.visibility,
                            UploadedBattleRecord.status,
                        )
                    )
                )
                rows = session.execute(statement).scalars().all()
        except OperationalError:
            return []
        return [self._record_to_uploaded_battle(row, summary_only=True) for row in rows]

    def _list_public_ranked_battles_by_boss(self) -> dict[str, list[DemoBattle]]:
        if "_list_uploaded_battles" not in self.__dict__:
            battles_by_boss: dict[str, list[DemoBattle]] = {}
            for battle in self._list_public_ranked_battles(metric="dps"):
                battles_by_boss.setdefault(battle.boss_slug, []).append(battle)
            return battles_by_boss
        battles_by_boss: dict[str, list[DemoBattle]] = {}
        for battle in self._list_uploaded_battle_summaries():
            battles_by_boss.setdefault(battle.boss_slug, []).append(battle)
        return {
            boss_slug: self._best_public_battles_by_account(battles)
            for boss_slug, battles in battles_by_boss.items()
        }

    def _is_public_ranked_battle(self, battle: DemoBattle) -> bool:
        if not battle.clear_flag:
            return False
        public_battle_ids = {
            public_battle.battle_id
            for public_battle in self._list_public_ranked_battles(boss_slug=battle.boss_slug)
        }
        return battle.battle_id in public_battle_ids

    @staticmethod
    def _contract_tag_score_value(battle: DemoBattle) -> int:
        return battle.contract_tag_score or 0

    @classmethod
    def _public_battle_sort_key(cls, battle: DemoBattle) -> tuple[object, ...]:
        if battle.boss_slug == CRISIS_CONTRACT_BOSS_SLUG:
            return (
                -cls._contract_tag_score_value(battle),
                battle.duration_ms,
                -battle.total_dps,
                -battle.total_damage,
                battle.battle_end_at.isoformat(),
            )
        return (
            battle.duration_ms,
            -battle.total_dps,
            -battle.total_damage,
            battle.battle_end_at.isoformat(),
        )

    def _boss_ranking_sort_key(self, battle: DemoBattle, metric: Metric) -> tuple[object, ...]:
        if battle.boss_slug == CRISIS_CONTRACT_BOSS_SLUG:
            return (
                -self._contract_tag_score_value(battle),
                battle.duration_ms,
                -self._battle_metric_value(battle, metric),
                -battle.total_damage,
                battle.battle_end_at.isoformat(),
            )
        return (
            battle.duration_ms,
            -self._battle_metric_value(battle, metric),
            -battle.total_damage,
            battle.battle_end_at.isoformat(),
        )

    def _best_public_battles_by_account(self, battles: list[DemoBattle]) -> list[DemoBattle]:
        # 榜单准入（仅收紧榜单，记录本身保留、详情/分享不受影响）：
        # (1) 官方通关：clear_flag 只按 boss HP 归零判定，会把 boss rush 只杀一个 boss、
        #     或多阶段 boss 单阶段血条归零误判成通关（时长虚短，60s 假通关排在 75s 真
        #     通关前）；只收 official_timer_end_seen=游戏 isPass=1 的真通关。
        # (2) rDPS 白名单：v25 前 rDPS 未过白名单，数值不可信，不参与排名。
        eligible = [
            battle
            for battle in battles
            if battle.official_timer_end_seen is True
            and battle.boss_slug not in REVIEWED_NON_RANKING_DUNGEON_SLUGS
            and battle.time_source == "game_timer"
            and _parser_version_number(battle.parser_version) >= RANKING_MIN_PARSER_VERSION
            and self._timer_integrity_eligible_for_ranking(battle)
        ]
        # (3) Boss 实际击杀校验：剔除伤害远低于同 boss 正常通关水平的“假通关”（boss 没真打死）。
        incomplete_ids = self._incomplete_clear_battle_ids(eligible)
        best_by_account: dict[str, DemoBattle] = {}
        for battle in eligible:
            if battle.battle_id in incomplete_ids:
                continue
            account_key = battle.uploader_user_id
            current = best_by_account.get(account_key)
            if current is None or self._public_battle_sort_key(battle) < self._public_battle_sort_key(current):
                best_by_account[account_key] = battle
        return sorted(best_by_account.values(), key=self._public_battle_sort_key)

    @staticmethod
    def _timer_integrity_eligible_for_ranking(battle: DemoBattle) -> bool:
        if battle.boss_slug == CRISIS_CONTRACT_BOSS_SLUG:
            return True
        if _parser_version_number(battle.parser_version) >= 47:
            return battle.timer_integrity_ok is True
        return True

    @staticmethod
    def _incomplete_clear_battle_ids(
        eligible: list[DemoBattle] | list[CharacterStatisticsBattleRow],
    ) -> set[str]:
        """按 boss 分组，对固定血量 boss（伤害紧密聚集）剔除伤害不足中位数 60% 的假通关。

        判据见 RANKING_* 常量注释：固定血量 boss 正常通关伤害≈血量、紧密聚集（p25/median≥0.95），
        伤害显著偏低=没真打死；伤害离散的 boss（混桶/多目标）不套用，避免误伤正常低伤记录。
        """
        by_boss: dict[str, list[DemoBattle | CharacterStatisticsBattleRow]] = {}
        for battle in eligible:
            by_boss.setdefault(battle.boss_slug, []).append(battle)
        incomplete: set[str] = set()
        for group in by_boss.values():
            if len(group) < RANKING_DAMAGE_GATE_MIN_SAMPLES:
                continue
            damages = sorted(float(battle.total_damage or 0) for battle in group)
            median = _linear_percentile(damages, 0.5)
            if median <= 0:
                continue
            p25 = _linear_percentile(damages, 0.25)
            if (p25 / median) < RANKING_DAMAGE_GATE_MIN_CLUSTERING:
                continue
            floor = RANKING_CLEAR_DAMAGE_MIN_FRACTION * median
            for battle in group:
                if float(battle.total_damage or 0) < floor:
                    incomplete.add(battle.battle_id)
        return incomplete

    def _get_uploaded_battle(self, battle_id: str, *, include_uncleared: bool = False) -> DemoBattle | None:
        with SessionLocal() as session:
            row = session.get(UploadedBattleRecord, battle_id)
        if row is None or row.status != "valid":
            return None
        if not include_uncleared and not row.clear_flag:
            return None
        return self._record_to_uploaded_battle(row)

    def _record_to_uploaded_battle(self, row: UploadedBattleRecord, *, summary_only: bool = False) -> DemoBattle:
        boss_slug = self._canonical_uploaded_boss_slug(row.boss_slug, row.boss_key)
        dungeon_name = row.dungeon_name
        if boss_slug in ACTIVITY_CANONICAL_STAGE_SLUGS and boss_slug in self.bosses_by_slug:
            dungeon_name = self.bosses_by_slug[boss_slug].dungeon_name
        boss_name = self._display_boss_name(
            boss_slug=boss_slug,
            boss_key=row.boss_key,
            boss_name=row.boss_name,
            dungeon_name=dungeon_name,
        )
        participants = [
            _enrich_participant_character_identity(BattleParticipantResponse.model_validate(item))
            for item in json.loads(row.participants_json)
        ]
        roster = [
            _enrich_roster_entry_character_identity(entry)
            for entry in _normalize_roster_weapon_refine(
                [BattleRosterEntryResponse.model_validate(item) for item in json.loads(row.roster_json)]
            )
        ]
        roster = _filter_roster_to_participants(roster, participants)
        timeline_events = (
            [
                _normalize_timeline_event_character_identity(TimelineEventResponse.model_validate(item))
                for item in json.loads(row.timeline_events_json)
            ]
            if not summary_only
            else []
        )
        timeline_events, participants = _normalize_off_participant_timeline_contributions(
            timeline_events,
            participants,
            duration_ms=row.duration_ms,
        )
        contract_tag_score, contract_tags = _contract_fields_for_boss(
            boss_slug,
            row.contract_tag_score,
            _load_contract_tags(row.contract_tags_json),
        )
        battle = DemoBattle(
            battle_id=row.id,
            uploader_user_id=row.uploader_user_id,
            uploader_nickname=row.uploader_nickname,
            boss_key=row.boss_key,
            boss_slug=boss_slug,
            boss_name=boss_name,
            dungeon_name=dungeon_name,
            duration_ms=row.duration_ms,
            battle_start_at=datetime.fromisoformat(row.battle_start_at),
            battle_end_at=datetime.fromisoformat(row.battle_end_at),
            total_damage=row.total_damage,
            total_dps=row.total_dps,
            roster=roster,
            participants=participants,
            timeline_events=timeline_events,
            role_skill_stats=(
                [
                    _normalize_role_skill_stat_character_identity(RoleSkillStatResponse.model_validate(item))
                    for item in json.loads(row.role_skill_stats_json)
                ]
                if not summary_only
                else []
            ),
            parser_version=row.parser_version,
            rules_version=row.rules_version,
            battle_fingerprint=row.battle_fingerprint,
            time_source=row.time_source,
            timeline_zero_source=row.timeline_zero_source,
            timer_start_seen=row.timer_start_seen,
            timer_end_seen=row.timer_end_seen,
            official_timer_start_seen=row.official_timer_start_seen,
            official_timer_end_seen=row.official_timer_end_seen,
            official_timer_observed_elapsed_ms=row.official_timer_observed_elapsed_ms,
            packet_timer_elapsed_ms=row.packet_timer_elapsed_ms,
            timer_integrity_ok=row.timer_integrity_ok,
            timer_integrity_reasons=(
                json.loads(row.timer_integrity_reasons_json)
                if row.timer_integrity_reasons_json
                else []
            ),
            timer_evidence=(json.loads(row.timer_evidence_json) if not summary_only and row.timer_evidence_json else None),
            timer_start_inferred=row.timer_start_inferred,
            timer_window_valid=row.timer_window_valid,
            rdps_preflight_ok=row.rdps_preflight_ok,
            rdps_strict_ok=row.rdps_strict_ok,
            rdps_preflight_blocker_count=row.rdps_preflight_blocker_count,
            boss_identity_source=row.boss_identity_source,
            dungeon_context_id=row.dungeon_context_id,
            dungeon_identity_source=row.dungeon_identity_source,
            loadout_fallback_used=row.loadout_fallback_used,
            contract_tag_score=contract_tag_score,
            contract_tags=contract_tags,
            clear_flag=row.clear_flag,
            visibility=row.visibility,
            status=row.status,
            casts=(
                json.loads(row.casts_json)
                if not summary_only and getattr(row, "casts_json", None)
                else None
            ),
        )
        self._register_boss_from_battle(battle)
        return battle

    def _persist_uploaded_battle(self, battle: DemoBattle) -> None:
        contract_tag_score, contract_tags = _contract_fields_for_boss(
            battle.boss_slug,
            battle.contract_tag_score,
            battle.contract_tags,
        )
        record = UploadedBattleRecord(
            id=battle.battle_id,
            uploader_user_id=battle.uploader_user_id,
            uploader_nickname=battle.uploader_nickname,
            boss_key=battle.boss_key,
            boss_slug=battle.boss_slug,
            boss_name=battle.boss_name,
            dungeon_name=battle.dungeon_name,
            duration_ms=battle.duration_ms,
            battle_start_at=battle.battle_start_at.isoformat(),
            battle_end_at=battle.battle_end_at.isoformat(),
            total_damage=battle.total_damage,
            total_dps=battle.total_dps,
            clear_flag=battle.clear_flag,
            roster_json=_dump_json([entry.model_dump(mode="json") for entry in battle.roster]),
            participants_json=_dump_json([entry.model_dump(mode="json") for entry in battle.participants]),
            timeline_events_json=_dump_json([entry.model_dump(mode="json") for entry in battle.timeline_events]),
            role_skill_stats_json=_dump_json([entry.model_dump(mode="json") for entry in battle.role_skill_stats]),
            parser_version=battle.parser_version,
            rules_version=battle.rules_version,
            battle_fingerprint=battle.battle_fingerprint,
            time_source=battle.time_source,
            timeline_zero_source=battle.timeline_zero_source,
            timer_start_seen=battle.timer_start_seen,
            timer_end_seen=battle.timer_end_seen,
            official_timer_start_seen=battle.official_timer_start_seen,
            official_timer_end_seen=battle.official_timer_end_seen,
            official_timer_observed_elapsed_ms=battle.official_timer_observed_elapsed_ms,
            packet_timer_elapsed_ms=battle.packet_timer_elapsed_ms,
            timer_integrity_ok=battle.timer_integrity_ok,
            timer_integrity_reasons_json=_dump_json(battle.timer_integrity_reasons),
            timer_evidence_json=_dump_json(battle.timer_evidence) if battle.timer_evidence is not None else None,
            timer_start_inferred=battle.timer_start_inferred,
            timer_window_valid=battle.timer_window_valid,
            rdps_preflight_ok=battle.rdps_preflight_ok,
            rdps_strict_ok=battle.rdps_strict_ok,
            rdps_preflight_blocker_count=battle.rdps_preflight_blocker_count,
            boss_identity_source=battle.boss_identity_source,
            dungeon_context_id=battle.dungeon_context_id,
            dungeon_identity_source=battle.dungeon_identity_source,
            loadout_fallback_used=battle.loadout_fallback_used,
            contract_tag_score=contract_tag_score,
            contract_tags_json=_dump_contract_tags(contract_tags),
            casts_json=_dump_json(battle.casts) if battle.casts else None,
            visibility=battle.visibility,
            status=battle.status,
            created_at=datetime.now(UTC).isoformat(),
        )
        with SessionLocal() as session:
            session.merge(record)
            session.commit()

    @staticmethod
    def _build_participants(
        roster: tuple[str, str, str, str],
        uploader_nickname: str,
        duration_ms: int,
        base_dps: int,
        base_rdps: int,
        multiplier: float,
    ) -> list[BattleParticipantResponse]:
        participants: list[BattleParticipantResponse] = []
        for lane, character_key in enumerate(roster):
            character_name = _get_character_name(character_key)
            dps = round((base_dps - lane * 16800) * multiplier, 2)
            rdps = round((base_rdps - lane * 15400) * multiplier, 2)
            participants.append(
                BattleParticipantResponse(
                    characterKey=character_key,
                    characterName=character_name,
                    characterProfession=_get_character_profession(character_name, character_key),
                    characterAvatarUrl=_get_character_avatar_url(character_name, character_key),
                    characterElement=_get_character_element(character_name, character_key),
                    accountDisplayName=uploader_nickname,
                    totalDamage=int(dps * (duration_ms / 1000)),
                    dps=dps,
                    rdps=rdps,
                    maxHit=275000 - lane * 26000,
                    critRate=round(0.18 + lane * 0.035, 3),
                )
            )
        return participants

    @staticmethod
    def _build_timeline_events(roster: tuple[str, str, str, str]) -> list[TimelineEventResponse]:
        events: list[TimelineEventResponse] = []
        for lane, character_key in enumerate(roster):
            character_name = _get_character_name(character_key)
            start = 2500 + lane * 1400
            events.append(
                TimelineEventResponse(
                    tsMsFromStart=start,
                    laneType="skill",
                    sourceCharacterKey=character_key,
                    sourceCharacterName=character_name,
                    targetCharacterKey=None,
                    targetCharacterName="首领",
                    eventName="起手连段",
                    value=125000 + lane * 8000,
                    important=True,
                )
            )
            events.append(
                TimelineEventResponse(
                    tsMsFromStart=start + 5200,
                    laneType="buff",
                    sourceCharacterKey=character_key,
                    sourceCharacterName=character_name,
                    targetCharacterKey=character_key,
                    targetCharacterName=character_name,
                    eventName="爆发窗口",
                    durationMs=12000,
                    important=True,
                )
            )
            events.append(
                TimelineEventResponse(
                    tsMsFromStart=start + 14300,
                    laneType="skill",
                    sourceCharacterKey=character_key,
                    sourceCharacterName=character_name,
                    targetCharacterKey=None,
                    targetCharacterName="首领",
                    eventName="终结技",
                    value=198000 + lane * 11000,
                    important=lane == 0,
                )
            )
        return events

    @staticmethod
    def _build_skill_stats(participants: list[BattleParticipantResponse]) -> list[RoleSkillStatResponse]:
        skill_stats: list[RoleSkillStatResponse] = []
        for participant in participants:
            skill_stats.append(
                RoleSkillStatResponse(
                    characterName=participant.characterName,
                    skillKey=None,
                    skillName="起手连段",
                    castCount=11,
                    totalDamage=int(participant.totalDamage * 0.37),
                    avgDamage=round(participant.totalDamage * 0.37 / 11, 2),
                    maxDamage=int((participant.maxHit or 0) * 0.78),
                )
            )
            skill_stats.append(
                RoleSkillStatResponse(
                    characterName=participant.characterName,
                    skillKey=None,
                    skillName="终结技",
                    castCount=7,
                    totalDamage=int(participant.totalDamage * 0.28),
                    avgDamage=round(participant.totalDamage * 0.28 / 7, 2),
                    maxDamage=int(participant.maxHit or 0),
                )
            )
        return skill_stats


public_data_service = DemoPublicDataService()
