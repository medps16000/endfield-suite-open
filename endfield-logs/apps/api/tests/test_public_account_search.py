from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Lock
from time import sleep

import pytest
from fastapi.testclient import TestClient

from app.api.routes import battles as battle_routes
from app.core.errors import AppError
from app.main import app
from app.schemas.public import (
    PublicAccountSearchEntryResponse,
    PublicAccountSearchResponse,
)
from app.services.public_data import BOSS_SEEDS, DemoBattle, DemoPublicDataService


def _battle(
    battle_id: str,
    account_id: str,
    nickname: str,
    *,
    boss_index: int = 0,
    duration_ms: int = 60_000,
    end_offset_minutes: int = 0,
    parser_version: str = "raw-log-parser-v46",
) -> DemoBattle:
    boss = BOSS_SEEDS[boss_index]
    battle_end_at = datetime(2026, 8, 20, 12, tzinfo=UTC) + timedelta(
        minutes=end_offset_minutes
    )
    return DemoBattle(
        battle_id=battle_id,
        uploader_user_id=account_id,
        uploader_nickname=nickname,
        boss_key=boss.key,
        boss_slug=boss.slug,
        boss_name=boss.name,
        dungeon_name=boss.dungeon_name,
        duration_ms=duration_ms,
        battle_start_at=battle_end_at - timedelta(milliseconds=duration_ms),
        battle_end_at=battle_end_at,
        total_damage=1_000_000,
        total_dps=20_000,
        roster=[],
        participants=[],
        timeline_events=[],
        role_skill_stats=[],
        parser_version=parser_version,
        rules_version="raw-log-parser-v40",
        battle_fingerprint=f"fingerprint-{battle_id}",
        time_source="game_timer",
        official_timer_end_seen=True,
        clear_flag=True,
        visibility="public",
        status="valid",
    )


def _service(metadata: list[DemoBattle]) -> DemoPublicDataService:
    service = DemoPublicDataService.__new__(DemoPublicDataService)
    service.bosses_by_slug = {boss.slug: boss for boss in BOSS_SEEDS}
    service._public_account_directory_cache = None
    service._public_account_directory_cache_lock = Lock()
    service._query_public_ranking_metadata = lambda *, boss_slug: metadata
    return service


def test_search_uses_only_ranked_public_metadata_and_latest_ranked_nickname() -> None:
    service = _service(
        [
            _battle("a-slow", "user-a", "CPU Old", duration_ms=70_000),
            _battle("a-fast", "user-a", "CPU 0", duration_ms=50_000),
            _battle(
                "a-other-boss",
                "user-a",
                "CPU New",
                boss_index=1,
                end_offset_minutes=10,
            ),
            _battle("b", "user-b", "CPU_0"),
            _battle("old-parser", "user-old", "CPU Old Parser", parser_version="raw-log-parser-v1"),
            _battle("empty-account", "", "CPU Empty"),
        ]
    )

    response = service.search_public_accounts(query="cpu", limit=10)

    assert response == PublicAccountSearchResponse(
        query="cpu",
        hasMore=False,
        accounts=[
            PublicAccountSearchEntryResponse(
                accountId="user-a",
                accountDisplayName="CPU New",
            ),
            PublicAccountSearchEntryResponse(
                accountId="user-b",
                accountDisplayName="CPU_0",
            ),
        ],
    )


def test_search_normalizes_nfkc_and_orders_exact_prefix_before_contains() -> None:
    service = _service(
        [
            _battle("contains", "user-c", "A CPU Team"),
            _battle("prefix", "user-b", "CPU 0"),
            _battle("exact", "user-a", "CPU"),
        ]
    )

    response = service.search_public_accounts(query="  ＣＰＵ  ", limit=2)

    assert response.query == "CPU"
    assert response.hasMore is True
    assert [entry.accountId for entry in response.accounts] == ["user-a", "user-b"]


@pytest.mark.parametrize("query", [" A ", "x" * 65])
def test_search_rejects_query_outside_trimmed_length_contract(query: str) -> None:
    with pytest.raises(AppError) as error:
        _service([]).search_public_accounts(query=query, limit=10)

    assert error.value.status_code == 422
    assert error.value.code == "invalid_account_search_query"


@pytest.mark.parametrize("limit", [0, 21])
def test_search_rejects_limit_outside_contract(limit: int) -> None:
    with pytest.raises(AppError) as error:
        _service([]).search_public_accounts(query="CPU", limit=limit)

    assert error.value.status_code == 422
    assert error.value.code == "invalid_account_search_limit"


def test_public_account_directory_cold_cache_is_single_flight() -> None:
    service = _service([])
    calls = 0
    calls_lock = Lock()

    def build() -> tuple[PublicAccountSearchEntryResponse, ...]:
        nonlocal calls
        with calls_lock:
            calls += 1
        sleep(0.03)
        return (
            PublicAccountSearchEntryResponse(
                accountId="user-a",
                accountDisplayName="CPU 0",
            ),
        )

    service._build_public_account_directory = build
    with ThreadPoolExecutor(max_workers=8) as executor:
        responses = list(
            executor.map(
                lambda _: service.search_public_accounts(query="CPU", limit=10),
                range(16),
            )
        )

    assert calls == 1
    assert all(response.accounts[0].accountId == "user-a" for response in responses)


def test_rankings_display_name_uses_same_public_directory_contract() -> None:
    service = _service([_battle("ranked", "user-a", "CPU Public")])
    service._list_user_boss_rankings = lambda account_id: []

    response = service.get_public_user_rankings("user-a")

    assert response.accountId == "user-a"
    assert response.accountDisplayName == "CPU Public"


def test_search_route_contract_etag_and_not_modified(monkeypatch) -> None:
    expected = PublicAccountSearchResponse(
        query="CPU",
        hasMore=False,
        accounts=[
            PublicAccountSearchEntryResponse(
                accountId="user-a",
                accountDisplayName="CPU 0",
            )
        ],
    )
    monkeypatch.setattr(
        battle_routes.public_data_service,
        "search_public_accounts",
        lambda *, query, limit: expected,
    )
    client = TestClient(app)

    response = client.get("/api/battles/users/search?query=CPU&limit=10")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json; charset=utf-8"
    assert response.headers["access-control-allow-origin"] == "*"
    assert response.headers["cache-control"] == "public, max-age=30"
    etag = response.headers["etag"]
    assert response.json() == expected.model_dump(mode="json")

    cached = client.get(
        "/api/battles/users/search?query=CPU&limit=10",
        headers={"If-None-Match": etag},
    )
    assert cached.status_code == 304
    assert cached.content == b""
    assert client.get("/api/battles/users/search").status_code == 422
    assert client.get("/api/battles/users/search?query=CPU&limit=21").status_code == 422
