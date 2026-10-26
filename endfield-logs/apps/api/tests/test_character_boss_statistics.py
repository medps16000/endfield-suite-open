import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Lock
from time import sleep

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.routes import characters as character_routes
from app.core.errors import AppError
from app.db.models import Base, UploadedBattleRecord
from app.main import app
from app.schemas.public import CharacterBossStatisticsResponse
from app.services import public_data
from app.services.public_data import BOSS_SEEDS, DemoPublicDataService


def _record(index: int, *, potential: int | None = 0) -> UploadedBattleRecord:
    boss = BOSS_SEEDS[0]
    end_at = datetime(2026, 8, 20, 12, index % 60, tzinfo=UTC).isoformat()
    return UploadedBattleRecord(
        id=f"character-boss-statistics-{index}",
        uploader_user_id=f"user-{index}",
        uploader_nickname=f"账号 {index}",
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
                    "accountDisplayName": f"账号 {index}",
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
                    "accountDisplayName": f"账号 {index}",
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
        battle_fingerprint=f"character-boss-statistics-fingerprint-{index}",
        time_source="game_timer",
        official_timer_end_seen=True,
        rdps_strict_ok=True,
        loadout_fallback_used=False,
        visibility="public",
        status="valid",
        created_at=end_at,
    )


def _service() -> DemoPublicDataService:
    service = DemoPublicDataService.__new__(DemoPublicDataService)
    service.bosses_by_slug = {boss.slug: boss for boss in BOSS_SEEDS}
    service._character_statistics_cache = {}
    service._character_statistics_cache_lock = Lock()
    service._character_boss_statistics_cache = {}
    service._character_boss_statistics_cache_lock = Lock()
    return service


def test_character_boss_rows_equal_existing_boss_statistics_rows(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    test_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    with test_session() as session:
        session.add_all([_record(index) for index in range(6)])
        session.add(_record(100, potential=None))
        session.commit()

    monkeypatch.setattr(public_data, "SessionLocal", test_session)
    service = _service()

    transposed = service.get_character_boss_statistics(
        "chr_0027_tangtang",
        "dps",
        "all",
        "all",
    )
    existing = service.get_boss_character_statistics(
        BOSS_SEEDS[0].slug,
        "dps",
        "all",
        "all",
    )
    existing_row = next(
        row for row in existing.rows if row.characterKey == "chr_0027_tangtang"
    )
    transposed_row = next(
        row for row in transposed.rows if row.bossSlug == BOSS_SEEDS[0].slug
    )

    for field in (
        "rank",
        "sampleCount",
        "normalSampleCount",
        "outlierCount",
        "insufficientSamples",
        "lowerWhisker",
        "p10",
        "p25",
        "median",
        "p75",
        "p90",
        "upperWhisker",
        "maximum",
        "outliers",
    ):
        assert getattr(transposed_row, field) == getattr(existing_row, field)
    assert transposed_row.rankedCharacterCount == sum(
        row.rank is not None for row in existing.rows
    )
    assert transposed.totalSampleCount == sum(row.sampleCount for row in transposed.rows)
    assert transposed.totalOutlierCount == sum(row.outlierCount for row in transposed.rows)


def test_character_boss_statistics_include_every_eligible_boss_and_zero_rows(
    monkeypatch,
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    test_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(public_data, "SessionLocal", test_session)
    service = _service()

    response = service.get_character_boss_statistics(
        "chr_0027_tangtang",
        "dps",
        "all",
        "all",
    )

    expected_bosses = service._character_statistics_bosses()
    assert response.includedBossCount == len(expected_bosses) == 63
    assert [row.bossSlug for row in response.rows] == [boss.slug for boss in expected_bosses]
    assert all(row.sampleCount == 0 for row in response.rows)
    assert all(row.rank is None and row.insufficientSamples for row in response.rows)
    assert all(row.median is None and row.maximum is None for row in response.rows)


def test_character_boss_statistics_reject_unknown_character() -> None:
    with pytest.raises(AppError) as error:
        _service().get_character_boss_statistics("chr_unknown", "dps", "all", "all")

    assert error.value.status_code == 404
    assert error.value.code == "character_not_found"


def test_character_boss_statistics_cold_cache_is_single_flight(monkeypatch) -> None:
    service = _service()
    catalog_entry = public_data.SIX_STAR_STATISTICS_CATALOG[0]
    expected = CharacterBossStatisticsResponse(
        characterKey=catalog_entry.char_id,
        characterName=catalog_entry.name,
        characterProfession=catalog_entry.profession,
        characterAvatarUrl=catalog_entry.avatar_url,
        metric="dps",
        range="all",
        potential="all",
        minimumSampleCount=5,
        includedBossCount=41,
        totalSampleCount=0,
        totalOutlierCount=0,
        rows=[],
    )
    calls = 0
    calls_lock = Lock()

    def compute(**kwargs) -> dict[str, CharacterBossStatisticsResponse]:
        nonlocal calls
        with calls_lock:
            calls += 1
        sleep(0.03)
        return {entry.char_id: expected for entry in public_data.SIX_STAR_STATISTICS_CATALOG}

    monkeypatch.setattr(service, "_compute_all_character_boss_statistics", compute)
    with ThreadPoolExecutor(max_workers=8) as executor:
        responses = list(
            executor.map(
                lambda _: service.get_character_boss_statistics(
                    catalog_entry.char_id,
                    "dps",
                    "all",
                    "all",
                ),
                range(16),
            )
        )

    assert calls == 1
    assert all(response == expected for response in responses)


def test_character_boss_statistics_route_contract_etag_and_validation(
    monkeypatch,
) -> None:
    catalog_entry = public_data.SIX_STAR_STATISTICS_CATALOG[0]
    expected = CharacterBossStatisticsResponse(
        characterKey=catalog_entry.char_id,
        characterName=catalog_entry.name,
        characterProfession=catalog_entry.profession,
        characterAvatarUrl=catalog_entry.avatar_url,
        metric="dps",
        range="30d",
        potential="0",
        minimumSampleCount=5,
        includedBossCount=41,
        totalSampleCount=0,
        totalOutlierCount=0,
        rows=[],
    )
    monkeypatch.setattr(
        character_routes.public_data_service,
        "get_character_boss_statistics",
        lambda character_key, metric, time_range, potential: expected,
    )
    client = TestClient(app)
    url = (
        f"/api/characters/{catalog_entry.char_id}/boss-statistics"
        "?metric=dps&range=30d&potential=0"
    )

    response = client.get(url)

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json; charset=utf-8"
    assert response.headers["access-control-allow-origin"] == "*"
    assert response.headers["cache-control"] == "public, max-age=30"
    etag = response.headers["etag"]
    assert response.json() == expected.model_dump(mode="json")
    cached = client.get(url, headers={"If-None-Match": etag})
    assert cached.status_code == 304
    assert cached.content == b""
    assert client.get(f"{url}&metric=invalid").status_code == 422
    assert client.get(f"{url}&range=invalid").status_code == 422
    assert client.get(f"{url}&potential=invalid").status_code == 422
