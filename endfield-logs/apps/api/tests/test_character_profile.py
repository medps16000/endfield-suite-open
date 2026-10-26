import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.core.errors import AppError
from app.db.models import Base, UploadedBattleRecord
from app.services import character_profile
from app.services.public_data import BOSS_SEEDS


CHARACTER = "chr_0034_typhoea"
TEAMMATE = "chr_0035_liino"


def record(identity, account="a", duration=20000, character=CHARACTER, potential=0, refine=None, **overrides):
    ended = datetime.now(UTC).isoformat()
    own = {"slot": 1, "characterKey": character, "characterName": "提弗洛斯", "accountDisplayName": account,
           "characterPotential": potential}
    if refine is not None:
        own["weapon"] = {"weaponTemplate": "wpn-test", "weaponName": "测试武器", "weaponRefine": refine}
        own["equips"] = [{"slot": slot, "itemId": item, "pieceName": item} for slot, item in enumerate(["armor", "glove", "ring", "ring"])]
    boss = BOSS_SEEDS[0]
    data = dict(id=identity, uploader_user_id=account, uploader_nickname=account, boss_key=boss.key,
        boss_slug=boss.slug, boss_name=boss.name, dungeon_name=boss.dungeon_name,
        duration_ms=duration, battle_start_at=ended, battle_end_at=ended, total_damage=1000000,
        total_dps=1000000 / (duration / 1000), clear_flag=True,
        roster_json=json.dumps([own, {"slot": 2, "characterKey": TEAMMATE, "characterName": "梨诺", "accountDisplayName": account}]),
        participants_json="[]", timeline_events_json="[]", role_skill_stats_json="[]", parser_version="raw-log-parser-v57",
        rules_version="raw-log-parser-v44", battle_fingerprint=identity, time_source="game_timer",
        official_timer_end_seen=True, timer_integrity_ok=True, loadout_fallback_used=False,
        visibility="public", status="valid", created_at=ended)
    data.update(overrides)
    return UploadedBattleRecord(**data)


@pytest.fixture
def store(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(character_profile, "SessionLocal", factory)
    return factory, character_profile.CharacterProfileService(), engine


def test_shared_cohort_filters_character_before_dedupe_and_keeps_fast_missing_loadout(store):
    factory, service, engine = store
    second = BOSS_SEEDS[1]
    with factory() as session:
        session.add_all([
            record("fast", duration=20000), record("slow-complete", duration=30000, potential=5, refine=5),
            record("faster-without-character", duration=10000, character="chr_0027_tangtang"),
            record("second-account", account="b", duration=22000, potential=5, refine=5),
            record("second-dungeon", boss_slug=second.slug, boss_key=second.key, potential=5, refine=5, loadout_fallback_used=True),
            record("private", account="c", visibility="private"),
            record("invalid", account="d", status="invalid"),
            record("uncleared", account="e", clear_flag=False),
            record("no-official-end", account="f", official_timer_end_seen=False),
            record("bad-clock", account="g", timer_integrity_ok=False),
            record("old-parser", account="h", parser_version="raw-log-parser-v24"),
        ])
        session.commit()
    statements = []
    event.listen(engine, "before_cursor_execute", lambda conn, cursor, statement, params, context, many: statements.append(statement))
    result = service.get_profile(CHARACTER)
    assert (result.sampleCount, result.accountCount, result.bossCount) == (3, 2, 2)
    assert {row.key: row.count for row in result.potentials if row.count} == {"0": 1, "5": 1, "unknown": 1}
    assert {row.key: row.count for row in result.refinements if row.count} == {"6": 1, "unknown": 2}
    assert sum(row.count for row in result.combinations) == 3
    assert result.teammates[0].count == 3 and result.teammates[0].percent == 100
    assert {row.key: row.count for row in result.weapons} == {"unknown": 2, "wpn-test": 1}
    assert {row.key: row.count for row in result.equipment} == {"armor": 1, "glove": 1, "ring": 1}
    assert result.equipmentUnknownSamples == 2
    first = next(boss for boss in result.bosses if boss.bossSlug == BOSS_SEEDS[0].slug)
    assert [(row.battleId, row.rank) for row in first.rows] == [("fast", 1), ("second-account", 2)]
    assert not any("timeline_events_json" in query or "casts_json" in query or "participants_json" in query for query in statements)
    selected = service.get_profile(CHARACTER, boss_slug=second.slug)
    assert selected.sampleCount == 1 and selected.potentials[-1].count == 1


def test_time_window_precedes_dedupe_and_damage_does_not_guess_validity(store):
    factory, service, _ = store
    old = (datetime.now(UTC) - timedelta(days=40)).isoformat()
    with factory() as session:
        session.add_all([record("old-fast", duration=10000, battle_end_at=old), record("recent-slow", duration=25000)])
        session.add_all(record(f"normal-{i}", account=f"account-{i}") for i in range(16))
        session.add(record("small-damage", account="low", total_damage=1))
        session.commit()
    result = service.get_profile(CHARACTER, "7d")
    assert result.sampleCount == 18
    ids = {row.battleId for boss in result.bosses for row in boss.rows}
    assert "old-fast" not in ids and {"recent-slow", "small-damage"} <= ids


def test_admin_aliases_four_star_empty_and_invalid_filters(store):
    factory, service, _ = store
    with factory() as session:
        session.add_all([record("male", character="chr_0002_endminm", duration=25000), record("female", character="chr_0003_endminf", duration=20000)])
        session.commit()
    result = service.get_profile("chr_9000_endmin")
    assert result.sampleCount == 1 and result.bosses[0].rows[0].battleId == "female"
    empty = service.get_profile("chr_0023_antal")
    assert empty.sampleCount == 0 and len(empty.potentials) == 7
    assert all(item.percent == 0 for item in empty.potentials)
    with pytest.raises(AppError):
        service.get_profile("not-a-character")
    with pytest.raises(AppError):
        service.get_profile(CHARACTER, boss_slug="not-a-dungeon")


def test_exact_equipment_localization_and_invalid_refinement_stays_unknown(store):
    factory, service, _ = store
    item_id = "item_equip_t4_suit_spellburst_body_01"
    battle = record("raw-id", refine=99)
    roster = json.loads(battle.roster_json)
    roster[0]["equips"][0].update(itemId=item_id, pieceName=item_id)
    battle.roster_json = json.dumps(roster)
    with factory() as session:
        session.add(battle)
        session.commit()
    result = service.get_profile(CHARACTER)
    assert next(item for item in result.equipment if item.key == item_id).name == "险关装甲"
    assert result.refinements[-1].count == 1
    assert result.refinements[5].count == 0


def test_duplicate_or_missing_character_ids_do_not_become_guessed_samples(store):
    factory, service, _ = store
    duplicate = record("duplicate")
    roster = json.loads(duplicate.roster_json)
    roster.append(roster[0])
    duplicate.roster_json = json.dumps(roster)
    missing = record("missing")
    roster = json.loads(missing.roster_json)
    roster[0]["characterKey"] = None
    missing.roster_json = json.dumps(roster)
    with factory() as session:
        session.add_all([duplicate, missing])
        session.commit()
    assert service.get_profile(CHARACTER).sampleCount == 0


def test_character_speed_rank_compares_every_character_with_shared_times_and_ties(store):
    factory, service, _ = store
    old = (datetime.now(UTC) - timedelta(days=40)).isoformat()
    with factory() as session:
        session.add_all([
            record("shared-fast", duration=20000), record("same-account-slow", duration=30000),
            record("tangtang-fast", account="b", character="chr_0027_tangtang", duration=15000),
            record("antal-tie", account="c", character="chr_0023_antal", duration=20000),
            record("old-antal", account="d", character="chr_0023_antal", duration=10000, battle_end_at=old),
            record("private-fast", account="e", duration=1000, visibility="private"),
            record("invalid-clock", account="f", duration=2000, timer_integrity_ok=False),
        ])
        session.commit()
    result = service.get_profile(CHARACTER, "7d")
    boss = result.bosses[0]
    assert result.sampleCount == 1
    assert (boss.characterRank, boss.rankedCharacterCount) == (3, 4)
    rows = {row.characterKey: row for row in boss.characterRows}
    assert rows[TEAMMATE].rank == rows["chr_0027_tangtang"].rank == 1
    assert rows[CHARACTER].rank == rows["chr_0023_antal"].rank == 3
    assert rows[TEAMMATE].battleId == rows["chr_0027_tangtang"].battleId == "tangtang-fast"
    assert rows[CHARACTER].sampleCount == 1
    assert rows[TEAMMATE].sampleCount == 3
    assert "chr_0038_purrche" not in rows
    # Changing the time range affects the comparison set, not just this character.
    all_time = service.get_profile(CHARACTER)
    assert all_time.bosses[0].characterRank == 4


def test_character_speed_comparison_keeps_dungeons_separate(store):
    factory, service, _ = store
    second = BOSS_SEEDS[1]
    with factory() as session:
        session.add_all([
            record("first", duration=10000),
            record("second-own", duration=30000, boss_slug=second.slug, boss_key=second.key),
            record("second-other", account="b", character="chr_0027_tangtang", duration=15000,
                   boss_slug=second.slug, boss_key=second.key),
        ])
        session.commit()
    first = service.get_profile(CHARACTER, boss_slug=BOSS_SEEDS[0].slug).bosses[0]
    second_result = service.get_profile(CHARACTER, boss_slug=second.slug).bosses[0]
    assert (first.characterRank, first.rankedCharacterCount) == (1, 2)
    assert (second_result.characterRank, second_result.rankedCharacterCount) == (3, 3)
    assert next(row for row in second_result.characterRows if row.characterKey == CHARACTER).bestDurationMs == 30000


def test_profiles_share_one_scan_and_fetch_only_selected_loadouts(store):
    factory, service, engine = store
    with factory() as session:
        session.add_all([record("one"), record("two", account="b")])
        session.commit()
    statements = []
    event.listen(engine, "before_cursor_execute", lambda conn, cursor, sql, params, context, many: statements.append(sql))
    service.get_profile(CHARACTER)
    service.get_profile(TEAMMATE)
    service.get_profile(CHARACTER, boss_slug=BOSS_SEEDS[0].slug)
    reads = [sql for sql in statements if "FROM uploaded_battles" in sql]
    full_scans = [sql for sql in reads if "uploaded_battles.id IN" not in sql]
    assert len(full_scans) == 1
    assert len(reads) == 4
    assert all("roster_json" not in vars(row) for row, members, digest in service._snapshot[3])


def test_late_cache_entries_do_not_extend_snapshot_age(store, monkeypatch):
    factory, service, _ = store
    clock = [100.0]
    monkeypatch.setattr(character_profile, "monotonic", lambda: clock[0])
    with factory() as session:
        session.add(record("one"))
        session.commit()
    service.get_profile(CHARACTER)
    clock[0] = 159.0
    service.get_profile(CHARACTER, boss_slug=BOSS_SEEDS[0].slug)
    assert service._cache[(CHARACTER, "all", BOSS_SEEDS[0].slug)][0] == 160.0


def test_snapshot_retries_exactly_when_loadout_changes_and_invalidation_removes_private_rows(store, monkeypatch):
    factory, service, _ = store
    with factory() as session:
        session.add(record("changing"))
        session.commit()
    service.get_profile(CHARACTER)
    with factory() as session:
        row = session.get(UploadedBattleRecord, "changing")
        roster = json.loads(row.roster_json)
        roster[0]["characterPotential"] = 4
        row.roster_json = json.dumps(roster)
        session.commit()
    updated = service.get_profile(CHARACTER, boss_slug=BOSS_SEEDS[0].slug)
    assert updated.potentials[4].count == 1
    with factory() as session:
        session.get(UploadedBattleRecord, "changing").visibility = "private"
        session.commit()
    monkeypatch.setattr(character_profile, "character_profile_service", service)
    character_profile.public_data_service._invalidate_public_caches()
    assert service.get_profile(CHARACTER).sampleCount == 0


def test_warm_result_does_not_wait_for_another_cold_character(store, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    factory, service, _ = store
    with factory() as session:
        session.add(record("one"))
        session.commit()
    service.get_profile(CHARACTER)
    entered, release = Event(), Event()
    original = service._compute_profile
    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(service, "_compute_profile", blocked)
    with ThreadPoolExecutor(max_workers=2) as pool:
        cold = pool.submit(service.get_profile, "chr_0023_antal")
        assert entered.wait(2)
        try:
            assert pool.submit(service.get_profile, CHARACTER).result(timeout=1).sampleCount == 1
        finally:
            release.set()
        assert cold.result(timeout=2).sampleCount == 0


def test_summary_keeps_ranks_and_fastest_time_without_mutating_full_response(store, monkeypatch):
    from app.api.routes import characters
    factory, service, _ = store
    with factory() as session:
        session.add(record("one"))
        session.commit()
    monkeypatch.setattr(characters, "character_profile_service", service)
    summary = characters.get_character_profile(CHARACTER, "all", None, False)
    assert summary.bosses[0].bestDurationMs == 20000
    assert summary.bosses[0].characterRank == 1
    assert summary.bosses[0].rows == [] and summary.bosses[0].characterRows == []
    full = characters.get_character_profile(CHARACTER, "all", None, True)
    assert len(full.bosses[0].rows) == 1 and len(full.bosses[0].characterRows) == 2
