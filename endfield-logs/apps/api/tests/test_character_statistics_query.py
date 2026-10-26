import json
from datetime import UTC, datetime
from threading import Lock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, UploadedBattleRecord
from app.services import public_data
from app.services.public_data import BOSS_SEEDS, DemoPublicDataService


def _record(index: int, *, potential: int | None = 0) -> UploadedBattleRecord:
    boss = BOSS_SEEDS[0]
    end_at = datetime(2026, 8, 13, 12, index % 60, tzinfo=UTC).isoformat()
    return UploadedBattleRecord(
        id=f"statistics-query-{index}",
        uploader_user_id="same-user",
        uploader_nickname="测试账号",
        boss_key=boss.key,
        boss_slug=boss.slug,
        boss_name=boss.name,
        dungeon_name=boss.dungeon_name,
        duration_ms=60_000,
        battle_start_at=end_at,
        battle_end_at=end_at,
        total_damage=1_000_000 + index,
        total_dps=10_000 + index,
        clear_flag=True,
        roster_json=json.dumps(
            [
                {
                    "slot": 1,
                    "characterKey": "chr_0027_tangtang",
                    "characterName": "汤汤",
                    "accountDisplayName": "测试账号",
                    "characterPotential": potential,
                }
            ],
            ensure_ascii=False,
        ),
        participants_json=json.dumps(
            [
                {
                    "characterKey": "chr_0027_tangtang",
                    "characterName": "汤汤",
                    "accountDisplayName": "测试账号",
                    "totalDamage": 600_000 + index,
                    "dps": 10_000 + index,
                    "rdps": 11_000 + index,
                    "maxHit": 50_000 + index,
                    "critRate": 0.2,
                }
            ],
            ensure_ascii=False,
        ),
        timeline_events_json="[]",
        role_skill_stats_json="[]",
        parser_version="raw-log-parser-v46",
        rules_version="raw-log-parser-v40",
        battle_fingerprint=f"statistics-query-fingerprint-{index}",
        time_source="game_timer",
        official_timer_end_seen=True,
        rdps_strict_ok=True,
        loadout_fallback_used=False,
        visibility="public",
        status="valid",
        created_at=end_at,
    )


def test_character_statistics_database_path_streams_minimal_payloads(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    test_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    with test_session() as session:
        session.add_all([_record(index) for index in range(6)])
        session.add(_record(100, potential=None))
        session.commit()

    monkeypatch.setattr(public_data, "SessionLocal", test_session)
    service = DemoPublicDataService.__new__(DemoPublicDataService)
    service.bosses_by_slug = {boss.slug: boss for boss in BOSS_SEEDS}
    service._character_statistics_cache = {}
    service._character_statistics_cache_lock = Lock()

    response = service.get_all_character_statistics("dps", "all", "all")
    tangtang = next(row for row in response.rows if row.characterName == "汤汤")

    assert response.eligibleBattleCount == 6
    assert response.totalSampleCount == 6
    assert tangtang.sampleCount == 6
    assert tangtang.median == 10_002.5
    assert response.includedBossCount == 1


def test_public_ranking_database_path_hydrates_only_account_winners(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    test_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    slow = _record(1)
    slow.duration_ms = 70_000
    fast = _record(2)
    fast.duration_ms = 50_000
    other = _record(3)
    other.uploader_user_id = "other-user"
    other.duration_ms = 60_000
    fast_id = fast.id
    other_id = other.id
    with test_session() as session:
        session.add_all([slow, fast, other])
        session.commit()

    monkeypatch.setattr(public_data, "SessionLocal", test_session)
    service = DemoPublicDataService.__new__(DemoPublicDataService)
    service.bosses_by_slug = {boss.slug: boss for boss in BOSS_SEEDS}
    service._public_ranked_cache = {}
    service._public_ranked_cache_lock = Lock()

    ranked = service._list_public_ranked_battles(BOSS_SEEDS[0].slug, metric="dps")

    assert [battle.battle_id for battle in ranked] == [fast_id, other_id]
    assert all(battle.roster and battle.participants for battle in ranked)


@pytest.mark.parametrize("parser,rules,strict,expected", [
    (51, 44, False, True), (50, 44, False, False), (51, 43, False, False),
    (46, 40, True, True), (24, 44, True, False),
])
def test_rdps_algorithm_versions_reach_database_rankings_and_all_statistics(
    monkeypatch, parser, rules, strict, expected,
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    test_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    record = _record(1)
    record.parser_version = f"raw-log-parser-v{parser}"
    record.rules_version = f"raw-log-parser-v{rules}"
    record.rdps_strict_ok = strict
    record.rdps_preflight_ok = False
    record.rdps_preflight_blocker_count = 224
    record.timer_integrity_ok = True
    with test_session() as session:
        session.add(record)
        session.commit()
    monkeypatch.setattr(public_data, "SessionLocal", test_session)
    service = DemoPublicDataService.__new__(DemoPublicDataService)
    service.bosses_by_slug = {boss.slug: boss for boss in BOSS_SEEDS}

    metadata = service._query_character_statistics_metadata(boss_slug=BOSS_SEEDS[0].slug)
    assert metadata[0].rules_version == f"raw-log-parser-v{rules}"
    for slug in (None, BOSS_SEEDS[0].slug):
        assert bool(service._list_public_ranked_battles(slug, metric="rdps")) is expected
    all_stats = service.get_all_character_statistics("rdps", "all", "all")
    boss_stats = service.get_boss_character_statistics(BOSS_SEEDS[0].slug, "rdps", "all", "all")
    character_stats = service.get_character_boss_statistics("chr_0027_tangtang", "rdps", "all", "all")
    assert all_stats.eligibleBattleCount == int(expected)
    assert boss_stats.eligibleBattleCount == int(expected)
    assert sum(row.sampleCount for row in character_stats.rows) == int(expected)


@pytest.mark.parametrize("field,value", [
    ("clear_flag", False), ("official_timer_end_seen", False),
    ("time_source", "damage_span"), ("visibility", "private"),
    ("status", "deleted"), ("timer_integrity_ok", False),
])
def test_rdps_algorithm_acceptance_does_not_bypass_database_battle_gates(monkeypatch, field, value) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    test_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    record = _record(1)
    record.parser_version = "raw-log-parser-v51"
    record.rules_version = "raw-log-parser-v44"
    record.rdps_strict_ok = False
    record.timer_integrity_ok = True
    setattr(record, field, value)
    with test_session() as session:
        session.add(record)
        session.commit()
    monkeypatch.setattr(public_data, "SessionLocal", test_session)
    service = DemoPublicDataService.__new__(DemoPublicDataService)
    service.bosses_by_slug = {boss.slug: boss for boss in BOSS_SEEDS}
    assert service._list_public_ranked_battles(BOSS_SEEDS[0].slug, metric="rdps") == []
    assert service.get_all_character_statistics("rdps", "all", "all").eligibleBattleCount == 0
