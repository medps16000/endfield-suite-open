from timer_samples import official_timer_evidence
from copy import deepcopy
import pytest
from fastapi.testclient import TestClient
from uuid import uuid4

from app.core.config import get_settings
from app.db.models import UploadedBattleRecord
from app.db.session import SessionLocal
from app.main import app, create_app
from app.services.public_data import BOSS_SEEDS, _load_contract_tag_catalog
from app.services.raw_log_integrity import build_raw_log_proof
from app.services.auth import DatabaseAuthService

UPLOADER_CLIENT_HEADERS = {
    "X-Endfield-Uploader-Name": "EndfieldLogsUploader",
    "X-Endfield-Uploader-Version": "2026.10.15.1",
    "X-Endfield-Parser-Version": "raw-log-parser-v59",
    "X-Endfield-Rules-Version": "raw-log-parser-v44",
}


def _uploader_auth_headers(session_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {session_token}",
        **UPLOADER_CLIENT_HEADERS,
    }


def _unique_email(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:10]}@example.com"


def _unique_nickname(prefix: str) -> str:
    return f"{prefix}{uuid4().hex[:8]}"


def _send_debug_code(client: TestClient, email: str, purpose: str) -> str:
    response = client.post(
        "/api/auth/send-code",
        json={"email": email, "purpose": purpose},
    )
    assert response.status_code == 200
    debug_code = response.json()["debugCode"]
    assert debug_code
    return debug_code


def _register_password_user(
    client: TestClient,
    *,
    email: str,
    nickname: str,
    purpose: str = "uploader_login",
    password: str = "hunter2pass",
) -> str | None:
    debug_code = _send_debug_code(client, email, purpose)
    response = client.post(
        "/api/auth/register",
        json={
            "email": email,
            "purpose": purpose,
            "password": password,
            "nickname": nickname,
            "code": debug_code,
        },
    )
    assert response.status_code == 200
    return response.json()["sessionToken"]


def _upload_real_battle(
    client: TestClient,
    *,
    boss_slug: str = "dung01_group_bossrush01",
    boss_key: str = "eny_0051_rodin",
    boss_name: str = "危境再现·罗丹",
    dungeon_name: str = "危境再现",
    character_key: str = "chr_0028_wulfa",
    character_name: str = "洛茜",
    duration_ms: int = 60000,
    total_damage: int = 4000000,
    contract_tag_score: int | None = None,
    contract_tags: list[dict] | None = None,
    parser_version: str = "raw-log-parser-v59",
    rules_version: str = "raw-log-parser-v44",
    official_timer_end_seen: bool = True,
    expected_status: int = 200,
) -> str:
    email = _unique_email("public-upload")
    nickname = _unique_nickname("Public")
    session_token = _register_password_user(
        client,
        email=email,
        nickname=nickname,
        purpose="uploader_login",
        password="upload-pass-123",
    )
    assert session_token

    total_dps = round(total_damage / (duration_ms / 1000), 2)
    fingerprint = f"fp-{boss_slug}-{uuid4().hex}"
    payload = {
        "battle": {
            "dungeonKey": boss_slug,
            "dungeonName": dungeon_name,
            "dungeonContextId": boss_slug,
            "dungeonIdentitySource": "dungeon_context",
            "bossKey": boss_key,
            "bossName": boss_name,
            "battleStartAt": "2026-04-24T18:24:58+08:00",
            "battleEndAt": "2026-04-24T18:25:58+08:00",
            "durationMs": duration_ms,
            "clearFlag": True,
            "totalDamage": total_damage,
            "totalDps": total_dps,
            "roster": [
                {
                    "slot": 1,
                    "characterKey": character_key,
                    "characterName": character_name,
                    "accountDisplayName": "ignored-by-server",
                }
            ],
            "battleFingerprint": fingerprint,
            "parserVersion": parser_version,
            "rulesVersion": rules_version,
            "timeSource": "game_timer",
            "officialTimerStartSeen": True,
            "officialTimerEndSeen": official_timer_end_seen,
            "officialTimerObservedElapsedMs": duration_ms + 2_000,
            "packetTimerElapsedMs": max(duration_ms - 2_000, 1),
            "timerIntegrityOk": True,
            "timerIntegrityReasons": [],
            "timerEvidence": official_timer_evidence(duration_ms, boss_slug),
            "timerWindowValid": True,
            "rdpsPreflightOk": True,
            "rdpsStrictOk": True,
            "rdpsPreflightBlockerCount": 0,
        },
        "participants": [
            {
                "characterKey": character_key,
                "characterName": character_name,
                "accountDisplayName": "ignored-by-server",
                "totalDamage": total_damage,
                "dps": total_dps,
                "rdps": total_dps,
                "maxHit": total_damage,
                "critRate": 0.25,
            }
        ],
        "timelineEvents": [],
        "roleSkillStats": [],
    }
    if contract_tag_score is not None:
        payload["battle"]["contractTagScore"] = contract_tag_score
    if contract_tags is not None:
        payload["battle"]["contractTags"] = contract_tags

    upload_response = client.post(
        "/api/uploader/battles",
        json=payload,
        headers=_uploader_auth_headers(session_token),
    )
    assert upload_response.status_code == expected_status
    if expected_status != 200:
        return upload_response.json()["error"]["code"]
    return upload_response.json()["battleId"]


def test_old_uploader_client_version_is_rejected_for_duplicate_check_and_upload() -> None:
    client = TestClient(app)
    session_token = _register_password_user(
        client,
        email=_unique_email("old-uploader"),
        nickname=_unique_nickname("OldUploader"),
        purpose="uploader_login",
        password="old-uploader-pass-123",
    )
    assert session_token

    old_headers = {
        "Authorization": f"Bearer {session_token}",
        "X-Endfield-Uploader-Name": "EndfieldLogsUploader",
        "X-Endfield-Uploader-Version": "2026.09.02.1",
    }
    duplicate_response = client.post(
        "/api/uploader/battles/check-duplicate",
        json={
            "battleFingerprint": f"fp-old-uploader-{uuid4().hex}",
            "bossKey": "eny_0051_rodin",
            "parserVersion": "raw-log-parser-v5",
            "rulesVersion": "raw-log-parser-v5",
        },
        headers=old_headers,
    )
    assert duplicate_response.status_code == 426
    assert duplicate_response.json()["error"]["code"] == "uploader_client_outdated"

    payload = {
        "battle": {
            "dungeonKey": "dung01_group_bossrush01",
            "dungeonName": "危境再现",
            "dungeonContextId": "dung01_group_bossrush01",
            "dungeonIdentitySource": "dungeon_context",
            "bossKey": "eny_0051_rodin",
            "bossName": "危境再现·罗丹",
            "battleStartAt": "2026-04-24T18:24:58+08:00",
            "battleEndAt": "2026-04-24T18:25:58+08:00",
            "durationMs": 60000,
            "clearFlag": True,
            "totalDamage": 4000000,
            "totalDps": 66666.67,
            "roster": [
                {
                    "slot": 1,
                    "characterKey": "chr_0028_wulfa",
                    "characterName": "洛茜",
                    "accountDisplayName": "ignored-by-server",
                }
            ],
            "battleFingerprint": f"fp-missing-uploader-version-{uuid4().hex}",
            "parserVersion": "raw-log-parser-v5",
            "rulesVersion": "raw-log-parser-v5",
        },
        "participants": [
            {
                "characterKey": "chr_0028_wulfa",
                "characterName": "洛茜",
                "accountDisplayName": "ignored-by-server",
                "totalDamage": 4000000,
                "dps": 66666.67,
                "rdps": 66666.67,
                "maxHit": 4000000,
                "critRate": 0.25,
            }
        ],
        "timelineEvents": [],
        "roleSkillStats": [],
    }
    missing_header_response = client.post(
        "/api/uploader/battles",
        json=payload,
        headers={"Authorization": f"Bearer {session_token}"},
    )
    assert missing_header_response.status_code == 426
    assert missing_header_response.json()["error"]["message"] == "当前上传器版本过旧，请下载最新版上传器后重新上传。"


def test_old_or_mismatched_parser_rules_are_rejected_for_duplicate_check_and_upload() -> None:
    client = TestClient(app)
    session_token = _register_password_user(
        client,
        email=_unique_email("old-semantics"),
        nickname=_unique_nickname("OldSemantics"),
        purpose="uploader_login",
        password="old-semantics-pass-123",
    )
    assert session_token

    old_headers = {
        **_uploader_auth_headers(session_token),
        "X-Endfield-Parser-Version": "raw-log-parser-v48",
        "X-Endfield-Rules-Version": "raw-log-parser-v42",
    }
    duplicate_response = client.post(
        "/api/uploader/battles/check-duplicate",
        json={
            "battleFingerprint": f"fp-old-semantics-{uuid4().hex}",
            "bossKey": "eny_0051_rodin",
            "parserVersion": "raw-log-parser-v48",
            "rulesVersion": "raw-log-parser-v42",
        },
        headers=old_headers,
    )
    assert duplicate_response.status_code == 426
    assert duplicate_response.json()["error"]["code"] == "upload_semantics_outdated"

    payload = {
        "battle": {
            "dungeonKey": "dung01_group_bossrush01",
            "dungeonName": "危境再现",
            "bossKey": "eny_0051_rodin",
            "bossName": "危境再现·罗丹",
            "battleStartAt": "2026-07-22T02:00:00+08:00",
            "battleEndAt": "2026-07-22T02:01:00+08:00",
            "durationMs": 60000,
            "clearFlag": True,
            "totalDamage": 4000000,
            "totalDps": 66666.67,
            "roster": [
                {
                    "slot": 1,
                    "characterKey": "chr_0028_wulfa",
                    "characterName": "洛茜",
                    "accountDisplayName": "ignored-by-server",
                }
            ],
            "battleFingerprint": f"fp-old-semantics-upload-{uuid4().hex}",
            "parserVersion": "raw-log-parser-v48",
            "rulesVersion": "raw-log-parser-v42",
        },
        "participants": [
            {
                "characterKey": "chr_0028_wulfa",
                "characterName": "洛茜",
                "accountDisplayName": "ignored-by-server",
                "totalDamage": 4000000,
                "dps": 66666.67,
                "rdps": 66666.67,
                "maxHit": 4000000,
                "critRate": 0.25,
            }
        ],
        "timelineEvents": [],
        "roleSkillStats": [],
    }
    upload_response = client.post(
        "/api/uploader/battles",
        json=payload,
        headers=old_headers,
    )
    assert upload_response.status_code == 426
    assert upload_response.json()["error"]["code"] == "upload_semantics_outdated"

    mismatched_headers = {
        **_uploader_auth_headers(session_token),
        "X-Endfield-Rules-Version": "raw-log-parser-v46",
    }
    mismatch_response = client.post(
        "/api/uploader/battles/check-duplicate",
        json={
            "battleFingerprint": f"fp-mismatched-semantics-{uuid4().hex}",
            "bossKey": "eny_0051_rodin",
            "parserVersion": "raw-log-parser-v59",
            "rulesVersion": "raw-log-parser-v44",
        },
        headers=mismatched_headers,
    )
    assert mismatch_response.status_code == 426
    assert mismatch_response.json()["error"]["code"] == "upload_semantics_outdated"


def test_healthcheck() -> None:
    client = TestClient(app)
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize("header_version,body_version", [("raw-log-parser-v58", "raw-log-parser-v58"),
    ("raw-log-parser-v59", "raw-log-parser-v58"), ("raw-log-parser-v58", "raw-log-parser-v59")])
def test_v58_parser_cannot_submit_new_duplicate_checks(header_version: str, body_version: str) -> None:
    client = TestClient(app)
    session_token = _register_password_user(client, email=_unique_email("parser-boundary"),
        nickname=_unique_nickname("ParserBoundary"), password="parser-boundary-pass-123")
    headers = {**_uploader_auth_headers(session_token), "X-Endfield-Parser-Version": header_version}
    response = client.post("/api/uploader/battles/check-duplicate", headers=headers, json={
        "battleFingerprint": f"fp-parser-boundary-{uuid4().hex}", "bossKey": "eny_0051_rodin",
        "parserVersion": body_version, "rulesVersion": "raw-log-parser-v44"})
    assert response.status_code == 426
    assert response.json()["error"]["code"] == "upload_semantics_outdated"


def test_saved_v58_battle_keeps_existing_dps_and_rdps_ranking_eligibility() -> None:
    client = TestClient(app)
    battle_id = _upload_real_battle(client)
    with SessionLocal() as session:
        record = session.get(UploadedBattleRecord, battle_id)
        assert record is not None
        record.parser_version = "raw-log-parser-v58"
        session.commit()
    detail = client.get(f"/api/battles/{battle_id}")
    assert detail.status_code == 200
    assert detail.json()["battle"]["parserVersion"] == "raw-log-parser-v58"
    for metric in ("dps", "rdps"):
        response = client.get(f"/api/bosses/dung01_group_bossrush01/rankings?metric={metric}")
        assert response.status_code == 200
        assert battle_id in {row["battleId"] for row in response.json()["rows"]}


def test_upload_filters_roster_entries_without_participants() -> None:
    client = TestClient(app)
    session_token = _register_password_user(
        client,
        email=_unique_email("roster-filter"),
        nickname=_unique_nickname("RosterFilter"),
        purpose="uploader_login",
        password="roster-filter-pass-123",
    )
    assert session_token

    payload = {
        "battle": {
            "dungeonKey": "dung01_group_bossrush01",
            "dungeonName": "危境再现",
            "dungeonContextId": "dung01_group_bossrush01",
            "dungeonIdentitySource": "dungeon_context",
            "bossKey": "eny_0051_rodin",
            "bossName": "“碾骨之拳”罗丹",
            "battleStartAt": "2026-04-24T18:24:58+08:00",
            "battleEndAt": "2026-04-24T18:25:18+08:00",
            "durationMs": 20000,
            "clearFlag": True,
            "totalDamage": 1000,
            "totalDps": 50.0,
            "roster": [
                {
                    "slot": 1,
                    "characterKey": "chr_0030_zhuangfy",
                    "characterName": "庄方宜",
                    "accountDisplayName": "ignored-by-server",
                },
                {
                    "slot": 2,
                    "characterKey": "chr_0013_aglina",
                    "characterName": "洁尔佩塔",
                    "accountDisplayName": "ignored-by-server",
                },
            ],
            "battleFingerprint": f"fp-roster-filter-{uuid4().hex}",
            "parserVersion": "raw-log-parser-v59",
            "rulesVersion": "raw-log-parser-v44",
            "timeSource": "game_timer",
            "officialTimerStartSeen": True,
            "officialTimerEndSeen": True,
            "officialTimerObservedElapsedMs": 22_000,
            "packetTimerElapsedMs": 18_000,
            "timerIntegrityOk": True,
            "timerIntegrityReasons": [],
            "timerEvidence": official_timer_evidence(20000, "dung01_group_bossrush01"),
            "timerEndSeen": True,
            "timerWindowValid": True,
            "rdpsPreflightOk": True,
            "rdpsStrictOk": True,
            "rdpsPreflightBlockerCount": 0,
        },
        "participants": [
            {
                "characterKey": "chr_0030_zhuangfy",
                "characterName": "庄方宜",
                "accountDisplayName": "ignored-by-server",
                "totalDamage": 1000,
                "dps": 50.0,
                "rdps": 50.0,
                "maxHit": 1000,
                "critRate": 0.0,
            }
        ],
        "timelineEvents": [
            {
                "tsMsFromStart": 1000,
                "laneType": "buff",
                "sourceCharacterKey": "chr_0013_aglina",
                "sourceCharacterName": "洁尔佩塔",
                "targetCharacterKey": "chr_0030_zhuangfy",
                "targetCharacterName": "庄方宜",
                "targetPlayerKey": "chr_0030_zhuangfy",
                "eventType": "buff",
                "eventKey": "buff_common_affixes_enhance_pulse_default_child",
                "eventName": "增幅",
                "durationMs": 3000,
                "effects": [{"zone": "amp", "element": "pulse", "rate": 0.2}],
            },
            {
                "tsMsFromStart": 2000,
                "laneType": "skill",
                "sourceCharacterKey": "chr_0030_zhuangfy",
                "sourceCharacterName": "庄方宜",
                "targetCharacterKey": "eny_0051_rodin",
                "targetCharacterName": "罗丹",
                "eventType": "damage",
                "eventKey": "chr_0030_zhuangfy_normal_skill_ult",
                "eventName": "惊霆诀",
                "value": 1000,
                "rdpsContributions": [
                    {"characterKey": "chr_0030_zhuangfy", "characterName": "庄方宜", "value": 800.0},
                    {"characterKey": "chr_0013_aglina", "characterName": "洁尔佩塔", "value": 200.0},
                ],
            }
        ],
        "roleSkillStats": [],
    }

    upload_response = client.post(
        "/api/uploader/battles",
        json=payload,
        headers=_uploader_auth_headers(session_token),
    )
    assert upload_response.status_code == 200
    battle_id = upload_response.json()["battleId"]

    detail_response = client.get(f"/api/battles/{battle_id}")
    assert detail_response.status_code == 200
    detail_payload = detail_response.json()
    assert [entry["characterName"] for entry in detail_payload["battle"]["roster"]] == ["庄方宜"]
    assert detail_payload["participants"][0]["rdps"] == 50.0
    assert detail_payload["timelineEvents"][0]["sourceCharacterName"] == "庄方宜"
    assert detail_payload["timelineEvents"][1]["rdpsContributions"] == [
        {"characterKey": "chr_0030_zhuangfy", "characterName": "庄方宜", "value": 1000.0}
    ]


def test_api_security_headers_are_present() -> None:
    client = TestClient(app)
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.headers["content-security-policy"] == "base-uri 'self'; form-action 'self'; frame-ancestors 'none'; object-src 'none'"
    assert response.headers["cross-origin-opener-policy"] == "same-origin"
    assert response.headers["permissions-policy"] == "camera=(), geolocation=(), microphone=()"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"


def test_trusted_host_blocks_unexpected_host() -> None:
    client = TestClient(app)
    response = client.get("/healthz", headers={"host": "evil.example"})
    assert response.status_code == 400


def test_production_auth_hides_debug_code_and_sets_secure_cookie(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AUTH_DEBUG_CODE_ENABLED", "false")
    monkeypatch.setenv("EXPOSE_API_DOCS", "false")
    monkeypatch.setenv("ALLOWED_HOSTS", "testserver")
    monkeypatch.setenv("CORS_ORIGINS", "http://testserver")
    monkeypatch.setenv("CSRF_TRUSTED_ORIGINS", "http://testserver")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "true")
    get_settings.cache_clear()

    try:
        client = TestClient(create_app())
        email = _unique_email("prod-auth")
        nickname = _unique_nickname("ProdUser")

        send_response = client.post(
            "/api/auth/send-code",
            json={"email": email, "purpose": "web_login"},
        )
        assert send_response.status_code == 200
        assert send_response.json()["debugCode"] is None
        debug_code = DatabaseAuthService().send_code(email, "web_login")

        register_response = client.post(
            "/api/auth/register",
            json={
                "email": email,
                "purpose": "web_login",
                "password": "ProdPass123",
                "nickname": nickname,
                "code": debug_code,
            },
        )
        assert register_response.status_code == 200
        assert register_response.json()["sessionToken"] is None
        set_cookie = register_response.headers["set-cookie"].lower()
        assert "httponly" in set_cookie
        assert "max-age=604800" in set_cookie
        assert "samesite=lax" in set_cookie
        assert "secure" in set_cookie

        docs_response = client.get("/openapi.json")
        assert docs_response.status_code == 404
    finally:
        get_settings.cache_clear()


def test_cookie_authenticated_unsafe_request_rejects_untrusted_origin_in_production(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ALLOWED_HOSTS", "testserver")
    monkeypatch.setenv("CORS_ORIGINS", "http://testserver")
    monkeypatch.setenv("CSRF_TRUSTED_ORIGINS", "http://testserver")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")
    get_settings.cache_clear()

    try:
        client = TestClient(create_app())
        email = _unique_email("csrf")
        response = client.post(
            "/api/auth/register",
            json={
                "email": email,
                "purpose": "web_login",
                "password": "csrf-pass-123",
                "nickname": _unique_nickname("Csrf"),
                "code": DatabaseAuthService().send_code(email, "web_login"),
            },
        )
        assert response.status_code == 200

        blocked_logout = client.post("/api/auth/logout", headers={"origin": "http://evil.example"})
        assert blocked_logout.status_code == 403
        assert blocked_logout.json()["error"]["code"] == "csrf_origin_forbidden"

        allowed_logout = client.post("/api/auth/logout", headers={"origin": "http://testserver"})
        assert allowed_logout.status_code == 200
    finally:
        get_settings.cache_clear()


def test_auth_flow_for_new_user() -> None:
    client = TestClient(app)
    email = _unique_email("alice")
    nickname = _unique_nickname("Alice")

    send_response = client.post(
        "/api/auth/send-code",
        json={"email": email, "purpose": "web_login"},
    )
    assert send_response.status_code == 200
    debug_code = send_response.json()["debugCode"]

    verify_response = client.post(
        "/api/auth/verify-code",
        json={"email": email, "purpose": "web_login", "code": debug_code},
    )
    assert verify_response.status_code == 200
    assert verify_response.json()["status"] == "needs_profile"

    complete_response = client.post(
        "/api/auth/complete-profile",
        json={
            "profileSetupToken": verify_response.json()["profileSetupToken"],
            "nickname": nickname,
        },
    )
    assert complete_response.status_code == 200
    assert complete_response.json()["status"] == "authenticated"

    me_response = client.get("/api/auth/me")
    assert me_response.status_code == 200
    assert me_response.json()["authenticated"] is True
    assert me_response.json()["user"]["nickname"] == nickname


def test_auth_flow_for_existing_user() -> None:
    client = TestClient(app)
    email = _unique_email("bob")
    nickname = _unique_nickname("Bob")

    send_response = client.post(
        "/api/auth/send-code",
        json={"email": email, "purpose": "web_login"},
    )
    debug_code = send_response.json()["debugCode"]
    verify_response = client.post(
        "/api/auth/verify-code",
        json={"email": email, "purpose": "web_login", "code": debug_code},
    )
    profile_token = verify_response.json()["profileSetupToken"]
    client.post("/api/auth/complete-profile", json={"profileSetupToken": profile_token, "nickname": nickname})

    second_send = client.post(
        "/api/auth/send-code",
        json={"email": email, "purpose": "web_login"},
    )
    second_code = second_send.json()["debugCode"]
    second_verify = client.post(
        "/api/auth/verify-code",
        json={"email": email, "purpose": "web_login", "code": second_code},
    )
    assert second_verify.status_code == 200
    assert second_verify.json()["status"] == "authenticated"
    assert second_verify.json()["user"]["nickname"] == nickname


def test_uploader_auth_flow_returns_session_token() -> None:
    client = TestClient(app)
    email = _unique_email("uploader")
    nickname = _unique_nickname("Uploader")

    send_response = client.post(
        "/api/auth/send-code",
        json={"email": email, "purpose": "uploader_login"},
    )
    debug_code = send_response.json()["debugCode"]
    verify_response = client.post(
        "/api/auth/verify-code",
        json={"email": email, "purpose": "uploader_login", "code": debug_code},
    )
    assert verify_response.status_code == 200
    assert verify_response.json()["status"] == "needs_profile"

    complete_response = client.post(
        "/api/auth/complete-profile",
        json={
            "profileSetupToken": verify_response.json()["profileSetupToken"],
            "nickname": nickname,
        },
    )
    assert complete_response.status_code == 200
    assert complete_response.json()["status"] == "authenticated"
    assert complete_response.json()["sessionToken"]

    me_response = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {complete_response.json()['sessionToken']}"},
    )
    assert me_response.status_code == 200
    assert me_response.json()["authenticated"] is True

    logout_response = client.post(
        "/api/auth/logout",
        headers={"Authorization": f"Bearer {complete_response.json()['sessionToken']}"},
    )
    assert logout_response.status_code == 200
    assert logout_response.json()["ok"] is True

    me_after_logout = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {complete_response.json()['sessionToken']}"},
    )
    assert me_after_logout.status_code == 200
    assert me_after_logout.json()["authenticated"] is False


def test_password_register_and_login_flow_for_web_user() -> None:
    client = TestClient(app)
    email = f"pw-web-{uuid4().hex[:10]}@example.com"
    nickname = _unique_nickname("Web")

    missing_code_response = client.post(
        "/api/auth/register",
        json={
            "email": email,
            "purpose": "web_login",
            "password": "hunter2pass",
            "nickname": nickname,
        },
    )
    assert missing_code_response.status_code == 400
    assert missing_code_response.json()["error"]["code"] == "code_required"

    debug_code = _send_debug_code(client, email, "web_login")
    register_response = client.post(
        "/api/auth/register",
        json={
            "email": email,
            "purpose": "web_login",
            "password": "hunter2pass",
            "nickname": nickname,
            "code": debug_code,
        },
    )
    assert register_response.status_code == 200
    assert register_response.json()["status"] == "authenticated"
    assert register_response.json()["sessionToken"] is None

    me_response = client.get("/api/auth/me")
    assert me_response.status_code == 200
    assert me_response.json()["authenticated"] is True
    assert me_response.json()["user"]["email"] == email

    logout_response = client.post("/api/auth/logout")
    assert logout_response.status_code == 200

    login_response = client.post(
        "/api/auth/login",
        json={
            "email": email,
            "purpose": "web_login",
            "password": "hunter2pass",
        },
    )
    assert login_response.status_code == 200
    assert login_response.json()["status"] == "authenticated"
    assert login_response.json()["sessionToken"] is None

    me_after_login = client.get("/api/auth/me")
    assert me_after_login.status_code == 200
    assert me_after_login.json()["authenticated"] is True
    assert me_after_login.json()["user"]["email"] == email


def test_password_register_and_login_flow_for_uploader_user() -> None:
    client = TestClient(app)
    email = f"pw-uploader-{uuid4().hex[:10]}@example.com"
    nickname = _unique_nickname("Uploader")

    check_before_response = client.get("/api/auth/check-email", params={"email": email})
    assert check_before_response.status_code == 200
    assert check_before_response.json()["available"] is True

    missing_code_response = client.post(
        "/api/auth/register",
        json={
            "email": email,
            "purpose": "uploader_login",
            "password": "uploader-pass-123",
            "nickname": nickname,
        },
    )
    assert missing_code_response.status_code == 400
    assert missing_code_response.json()["error"]["code"] == "code_required"

    debug_code = _send_debug_code(client, email, "uploader_login")
    register_response = client.post(
        "/api/auth/register",
        json={
            "email": email,
            "purpose": "uploader_login",
            "password": "uploader-pass-123",
            "nickname": nickname,
            "code": debug_code,
        },
    )
    assert register_response.status_code == 200
    session_token = register_response.json()["sessionToken"]
    assert session_token

    check_after_response = client.get("/api/auth/check-email", params={"email": email})
    assert check_after_response.status_code == 200
    assert check_after_response.json()["available"] is False

    me_response = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {session_token}"},
    )
    assert me_response.status_code == 200
    assert me_response.json()["authenticated"] is True
    assert me_response.json()["user"]["email"] == email

    bad_login_response = client.post(
        "/api/auth/login",
        json={
            "email": email,
            "purpose": "uploader_login",
            "password": "wrong-password",
        },
    )
    assert bad_login_response.status_code == 401
    assert bad_login_response.json()["error"]["code"] == "login_failed"

    login_response = client.post(
        "/api/auth/login",
        json={
            "email": email,
            "purpose": "uploader_login",
            "password": "uploader-pass-123",
        },
    )
    assert login_response.status_code == 200
    assert login_response.json()["sessionToken"]


def test_password_account_persists_across_auth_service_instances() -> None:
    client = TestClient(app)
    email = _unique_email("persist")
    nickname = _unique_nickname("Persist")
    password = "persist-pass-123"
    debug_code = _send_debug_code(client, email, "uploader_login")

    register_response = client.post(
        "/api/auth/register",
        json={
            "email": email,
            "purpose": "uploader_login",
            "password": password,
            "nickname": nickname,
            "code": debug_code,
        },
    )
    assert register_response.status_code == 200
    session_token = register_response.json()["sessionToken"]

    reloaded_auth_service = DatabaseAuthService()
    user = reloaded_auth_service.get_user_from_session(session_token)
    assert user is not None
    assert user.email == email

    login_result = reloaded_auth_service.login_with_password(email, "uploader_login", password)
    assert login_result["status"] == "authenticated"
    assert login_result["user"].email == email


def test_public_home_and_boss_endpoints() -> None:
    client = TestClient(app)
    battle_id = _upload_real_battle(client, duration_ms=6000, total_damage=9000000)

    home_response = client.get("/api/home/hot-bosses")
    assert home_response.status_code == 200
    home_payload = home_response.json()
    assert len(home_payload) == len(BOSS_SEEDS)
    rodin_card = next(card for card in home_payload if card["bossSlug"] == "dung01_group_bossrush01")
    assert rodin_card["topSpeedRuns"]
    assert any(
        card["bossSlug"] == "indie_hard008_s"
        and card["bossName"] == "怨憎雾海·苦难"
        and card["dungeonName"] == "影拓丰碑1期 · 灼痛疤痕"
        for card in home_payload
    )
    assert any(
        card["bossSlug"] == "indie_hard013_s"
        and card["bossName"] == "沉寂视界·苦难"
        and card["dungeonName"] == "影拓丰碑2期 · 浊流具现"
        for card in home_payload
    )
    hot_durations = [run["durationMs"] for run in rodin_card["topSpeedRuns"]]
    assert hot_durations == sorted(hot_durations)

    boss_response = client.get("/api/bosses/dung01_group_bossrush01/rankings?metric=dps")
    assert boss_response.status_code == 200
    boss_payload = boss_response.json()
    assert boss_payload["bossSlug"] == "dung01_group_bossrush01"
    assert any(row["battleId"] == battle_id for row in boss_payload["rows"])
    ranking_durations = [row["durationMs"] for row in boss_payload["rows"]]
    assert ranking_durations == sorted(ranking_durations)
    assert boss_payload["rows"][0]["rank"] == 1
    assert boss_payload["rows"][0]["scorePercent"] == 100
    assert boss_payload["rows"][0]["characterProfession"] in {"近卫", "重装", "辅助", "突击", "术士", "先锋", "未归类"}
    assert len(boss_payload["professionGroups"]) == 6


def test_crisis_fragment_aliases_filter_by_official_stage_slug_only() -> None:
    client = TestClient(app)
    stage_battle_id = _upload_real_battle(
        client,
        boss_slug="dung02_group_minibossrush02",
        boss_key="eny_0120_klbear",
        boss_name="eny_0120_klbear",
        dungeon_name="碎片延影·蚀影噪雷",
        duration_ms=260000,
        total_damage=52000000,
    )
    boss_key_battle_id = _upload_real_battle(
        client,
        boss_slug="dung02_group_minibossrush02",
        boss_key="eny_0120_klbear",
        boss_name="eny_0120_klbear",
        dungeon_name="危境碎片·蚀影噪雷",
        duration_ms=270000,
        total_damage=50000000,
    )

    with SessionLocal() as session:
        stage_row = session.get(UploadedBattleRecord, stage_battle_id)
        assert stage_row is not None
        stage_row.boss_slug = "dung02_minibossrush02_02"
        stage_row.boss_name = "eny_0120_klbear"

        boss_key_row = session.get(UploadedBattleRecord, boss_key_battle_id)
        assert boss_key_row is not None
        boss_key_row.boss_slug = "legacy-klbear"
        boss_key_row.boss_name = "eny_0120_klbear"
        session.commit()

    boss_response = client.get("/api/bosses/dung02_group_minibossrush02/rankings?metric=dps")
    assert boss_response.status_code == 200
    boss_payload = boss_response.json()
    assert boss_payload["bossSlug"] == "dung02_group_minibossrush02"
    assert boss_payload["bossName"] == "蚀影噪雷"
    row_ids = {row["battleId"] for row in boss_payload["rows"]}
    assert stage_battle_id in row_ids
    assert boss_key_battle_id not in row_ids


def test_war_echo_highest_stages_have_separate_rankings_and_lower_difficulty_is_excluded() -> None:
    client = TestClient(app)
    cases = (
        ("indie_group_twdg", "eny_0082_hsbear", "legacy_tian_gu"),
        ("indie_battletower007_ex", "eny_0082_hsbear", "tian_gu_ex"),
        ("indie_battletower006_ex", "eny_0018_lbtough_race006", "split_old_wound_ex"),
        ("indie_battletower004_ex", "eny_0068_lbtough2", "axe_annals_ex"),
        ("indie_battletower006_s", "eny_0018_lbtough_race006", "split_old_wound_hard"),
    )
    battle_ids = {}
    for index, (stored_slug, boss_key, label) in enumerate(cases):
        battle_id = _upload_real_battle(
            client,
            boss_slug=stored_slug,
            boss_key=boss_key,
            boss_name="战争回响",
            dungeon_name="战争回响",
            duration_ms=260000 + index * 10000,
            total_damage=52000000 - index * 1000000,
        )
        if label == "legacy_tian_gu":
            with SessionLocal() as session:
                row = session.get(UploadedBattleRecord, battle_id)
                assert row is not None
                row.boss_slug = "indie_group_twdg"
                session.commit()
        battle_ids[label] = battle_id

    tian_gu_response = client.get("/api/bosses/indie_battletower007_ex/rankings?metric=dps")
    assert tian_gu_response.status_code == 200
    tian_gu_rows = {row["battleId"] for row in tian_gu_response.json()["rows"]}
    assert battle_ids["tian_gu_ex"] in tian_gu_rows
    assert battle_ids["legacy_tian_gu"] not in tian_gu_rows
    assert battle_ids["split_old_wound_ex"] not in tian_gu_rows

    split_old_wound_response = client.get("/api/bosses/indie_battletower006_ex/rankings?metric=dps")
    assert split_old_wound_response.status_code == 200
    split_old_wound_payload = split_old_wound_response.json()
    assert split_old_wound_payload["bossSlug"] == "indie_battletower006_ex"
    assert split_old_wound_payload["bossName"] == "裂地旧创·残酷"
    split_old_wound_rows = {row["battleId"] for row in split_old_wound_payload["rows"]}
    assert battle_ids["split_old_wound_ex"] in split_old_wound_rows
    assert battle_ids["legacy_tian_gu"] not in split_old_wound_rows
    assert battle_ids["split_old_wound_hard"] not in split_old_wound_rows

    axe_annals_response = client.get("/api/bosses/indie_battletower004_ex/rankings?metric=dps")
    assert axe_annals_response.status_code == 200
    axe_annals_payload = axe_annals_response.json()
    assert axe_annals_payload["bossName"] == "斧柄纪年·残酷"
    assert battle_ids["axe_annals_ex"] in {
        row["battleId"] for row in axe_annals_payload["rows"]
    }

    white_blade_response = client.get("/api/bosses/indie_battletower001_ex/rankings?metric=dps")
    assert white_blade_response.status_code == 200
    assert white_blade_response.json()["bossName"] == "白刃穿水·残酷"

    wild_history_response = client.get("/api/bosses/indie_battletower002_ex/rankings?metric=dps")
    assert wild_history_response.status_code == 200
    assert wild_history_response.json()["bossName"] == "野性旧事·残酷"
    brief_history_response = client.get("/api/bosses/indie_battletower008_ex/rankings?metric=dps")
    assert brief_history_response.status_code == 200
    assert brief_history_response.json()["bossName"] == "战争简史·残酷"

    legacy_group_response = client.get("/api/bosses/indie_group_twdg/rankings?metric=dps")
    assert legacy_group_response.status_code == 404


@pytest.mark.parametrize("stage,name,group", [
    ("indie_hard008_s", "怨憎雾海·苦难", "影拓丰碑1期 · 灼痛疤痕"),
    ("indie_hard026_s", "兽影生杀·苦难", "影拓丰碑5期 · 幽影刻形"),
    ("indie_hard027_s", "暗曜白霆·苦难", "影拓丰碑5期 · 幽影刻形"),
    ("indie_hard028_s", "沛然轰鸣·苦难", "影拓丰碑5期 · 幽影刻形"),
    ("indie_hard029_s", "三角定位·苦难", "影拓丰碑5期 · 幽影刻形"),
    ("indie_hard030_s", "受缚之影·苦难", "影拓丰碑6期 · 深林呓语"),
    ("indie_hard031_s", "相引之骸·苦难", "影拓丰碑6期 · 深林呓语"),
    ("indie_hard032_s", "怨怒延展·苦难", "影拓丰碑6期 · 深林呓语"),
    ("indie_hard033_s", "虫傀呼应·苦难", "影拓丰碑6期 · 深林呓语"),
])
def test_high_difficulty_upload_uses_explicit_hard_stage_slug(stage: str, name: str, group: str) -> None:
    client = TestClient(app)
    battle_id = _upload_real_battle(
        client,
        boss_slug=stage,
        boss_key="eny_0085_hsrogue_hard",
        boss_name="断云叟",
        dungeon_name=name,
        duration_ms=300000,
        total_damage=733731,
    )

    boss_response = client.get(f"/api/bosses/{stage}/rankings?metric=dps")
    assert boss_response.status_code == 200
    boss_payload = boss_response.json()
    assert boss_payload["bossSlug"] == stage
    assert boss_payload["bossName"] == name
    assert boss_payload["dungeonName"] == group
    assert any(row["battleId"] == battle_id for row in boss_payload["rows"])


@pytest.mark.parametrize("stage", ["indie_hard013", "indie_hard026", "indie_hard027", "indie_hard028", "indie_hard029",
                                    "indie_hard030", "indie_hard031", "indie_hard032", "indie_hard033"])
def test_high_difficulty_plain_stage_slug_does_not_enter_hard_ranking(stage: str) -> None:
    client = TestClient(app)
    battle_id = _upload_real_battle(
        client,
        boss_slug=stage,
        boss_key="eny_0059_erhound",
        boss_name="雾火牙兽",
        dungeon_name="沉寂视界",
        duration_ms=51000,
        total_damage=733731,
    )

    boss_response = client.get(f"/api/bosses/{stage}_s/rankings?metric=dps")
    assert boss_response.status_code == 200
    assert not any(row["battleId"] == battle_id for row in boss_response.json()["rows"])


@pytest.mark.parametrize("stage,name", [
    ("dung01_group_gambling", "百趣演武"),
    ("dungeon_gamblingdungeon01", "百趣演武"),
])
def test_reviewed_gambling_upload_keeps_detail_without_opening_rankings(stage: str, name: str) -> None:
    client = TestClient(app)
    battle_id = _upload_real_battle(client, boss_slug=stage, boss_key="eny_0051_rodin", boss_name=name,
                                  dungeon_name=name, total_damage=733731)
    detail = client.get(f"/api/battles/{battle_id}")
    assert detail.status_code == 200
    assert detail.json()["battle"]["dungeonName"] == name
    assert detail.json()["battle"]["rdpsRankingEligible"] is False
    assert client.get(f"/api/bosses/{stage}/rankings?metric=dps").status_code == 404
    assert client.get(f"/api/bosses/{stage}/rankings?metric=rdps").status_code == 404
    hot = client.get("/api/home/hot-bosses")
    assert hot.status_code == 200
    assert stage not in {row["bossSlug"] for row in hot.json()}


def test_new_si_bossrush_upload_uses_the_official_series_ranking() -> None:
    client = TestClient(app)
    battle_id = _upload_real_battle(client, boss_slug="dung02_bossrush04_03", boss_key="eny_0134_jsspsi",
                                  boss_name="祀", dungeon_name="危境祸影·祀", total_damage=733731)
    response = client.get("/api/bosses/dung02_group_bossrush04/rankings?metric=dps")
    assert response.status_code == 200
    assert response.json()["bossName"] == "危境再现·祀"
    assert battle_id in {row["battleId"] for row in response.json()["rows"]}


@pytest.mark.parametrize("stage,name", [
    ("indie_battletower015_ex", "蚀影记录：雷声·残酷"),
    ("indie_battletower016_ex", "长刃记录：应龙·残酷"),
    ("indie_battletower017_ex", "复仇记录：碾骨·残酷"),
    ("indie_battletower018_ex", "兽群记录：清波寨·残酷"),
    ("indie_battletower019_ex", "造物记录：傀·残酷"),
    ("indie_battletower020_ex", "防卫记录：盾片·残酷"),
])
def test_new_war_echo_rankings_have_official_names(stage: str, name: str) -> None:
    response = TestClient(app).get(f"/api/bosses/{stage}/rankings?metric=dps")
    assert response.status_code == 200
    assert response.json()["bossName"] == name


def test_new_war_echo_uploads_keep_hard_and_cruel_separate() -> None:
    client = TestClient(app)
    cruel = _upload_real_battle(client, boss_slug="indie_battletower015_ex", boss_key="eny_0120_klbear_tower015",
                               boss_name="蚀影记录：雷声·残酷", dungeon_name="战争回响", total_damage=733731)
    hard = _upload_real_battle(client, boss_slug="indie_battletower015_s", boss_key="eny_0120_klbear_tower015",
                              boss_name="蚀影记录：雷声·困难", dungeon_name="战争回响", total_damage=733731)
    response = client.get("/api/bosses/indie_battletower015_ex/rankings?metric=dps")
    assert response.status_code == 200
    ids = {row["battleId"] for row in response.json()["rows"]}
    assert cruel in ids and hard not in ids
    assert client.get(f"/api/battles/{hard}").status_code == 200


def test_high_difficulty_legacy_enemy_key_without_stage_is_rejected() -> None:
    client = TestClient(app)
    error_code = _upload_real_battle(
        client,
        boss_slug="unknown_dungeon",
        boss_key="eny_0085_hsrogue",
        boss_name="割云翁",
        dungeon_name="未知副本",
        duration_ms=300000,
        total_damage=733731,
        expected_status=422,
    )
    assert error_code == "battle_dungeon_unverified"


def test_high_difficulty_shadow_phase_three_unknown_stage_is_rejected() -> None:
    client = TestClient(app)
    error_code = _upload_real_battle(
        client,
        boss_slug="unknown_dungeon",
        boss_key="eny_0046_lbshamman_hdg016",
        boss_name="eny_0046_lbshamman_hdg016",
        dungeon_name="未知副本",
        duration_ms=300000,
        total_damage=733731,
        expected_status=422,
    )
    assert error_code == "battle_dungeon_unverified"


def test_high_difficulty_group_context_does_not_enter_hard_ranking() -> None:
    client = TestClient(app)
    battle_id = _upload_real_battle(
        client,
        boss_slug="indie_group_h04",
        boss_key="eny_0059_erhound",
        boss_name="雾火牙兽",
        dungeon_name="浊流具现",
        duration_ms=260000,
        total_damage=733731,
    )

    phase_two_response = client.get("/api/bosses/indie_hard013_s/rankings?metric=dps")
    assert phase_two_response.status_code == 200
    assert not any(row["battleId"] == battle_id for row in phase_two_response.json()["rows"])

    phase_one_response = client.get("/api/bosses/indie_hard006_s/rankings?metric=dps")
    assert phase_one_response.status_code == 200
    assert not any(row["battleId"] == battle_id for row in phase_one_response.json()["rows"])

    detail_response = client.get(f"/api/battles/{battle_id}")
    assert detail_response.status_code == 200
    detail_payload = detail_response.json()
    assert detail_payload["battle"]["bossName"] == "雾火牙兽"


def test_high_difficulty_explicit_hard_stage_raw_enemy_name_displays_catalog_name() -> None:
    client = TestClient(app)
    battle_id = _upload_real_battle(
        client,
        boss_slug="indie_hard007_s",
        boss_key="eny_0054_hsmino_hdg007",
        boss_name="eny_0054_hsmino_hdg007",
        dungeon_name="呼吼炽焰 · 苦难",
        duration_ms=83945,
        total_damage=1349557,
    )

    boss_response = client.get("/api/bosses/indie_hard007_s/rankings?metric=dps")
    assert boss_response.status_code == 200
    assert any(row["battleId"] == battle_id for row in boss_response.json()["rows"])

    detail_response = client.get(f"/api/battles/{battle_id}")
    assert detail_response.status_code == 200
    detail_payload = detail_response.json()
    assert detail_payload["battle"]["bossName"] == "呼吼炽焰·苦难"
    assert detail_payload["battle"]["bossName"] != "eny_0054_hsmino_hdg007"

    share_response = client.get(f"/api/battles/{battle_id}/share-summary")
    assert share_response.status_code == 200
    assert share_response.json()["bossName"] == "呼吼炽焰·苦难"


def test_high_difficulty_legacy_raw_enemy_name_without_stage_is_rejected() -> None:
    client = TestClient(app)
    error_code = _upload_real_battle(
        client,
        boss_slug="unknown_dungeon",
        boss_key="eny_0054_hsmino_hdg007",
        boss_name="eny_0054_hsmino_hdg007",
        dungeon_name="呼吼炽焰 · 苦难",
        duration_ms=83945,
        total_damage=1349557,
        expected_status=422,
    )
    assert error_code == "battle_dungeon_unverified"


def test_public_battle_detail_and_share_summary() -> None:
    client = TestClient(app)
    battle_id = _upload_real_battle(client, duration_ms=7000, total_damage=8500000)

    detail_response = client.get(f"/api/battles/{battle_id}")
    assert detail_response.status_code == 200
    detail_payload = detail_response.json()
    assert detail_payload["battle"]["bossName"] == "危境再现·罗丹"
    assert len(detail_payload["participants"]) == 1
    assert detail_payload["integrity"]["version"] == "battle-struct-v1"
    assert detail_payload["integrity"]["verified"] is True
    assert detail_payload["integrity"]["canonicalSha256"]
    assert detail_payload["integrity"]["serverSeal"]

    share_response = client.get(f"/api/battles/{battle_id}/share-summary")
    assert share_response.status_code == 200
    assert share_response.json()["battleId"] == battle_id


def test_contract_tags_are_exposed_on_public_records() -> None:
    client = TestClient(app)
    catalog = _load_contract_tag_catalog()
    preferred_tag_ids = [102802, 900103]
    contract_tags = [
        {
            "tagId": 102802,
            "score": 2,
            "name": "主属性降低 II",
            "buffId": "global_buff_cc_chr_main_attribute_down",
            "groupId": 1028,
            "conflictId": "c1028",
            "values": {"attr": 0.8},
        },
        {
            "tagId": 900103,
            "score": 3,
            "name": "敌方生命提升 III",
            "buffId": "buff_cc_enemy_common_hp_up",
            "groupId": 9001,
            "conflictId": "c9001",
            "values": {"hp_up": 3},
        },
    ]
    contract_tags.extend(
        {"tagId": tag_id, "score": catalog[tag_id]["score"], "name": catalog[tag_id]["name"]}
        for tag_id in sorted(catalog)
        if tag_id not in preferred_tag_ids
    )
    expected_score = sum(int(tag["score"]) for tag in contract_tags)
    battle_id = _upload_real_battle(
        client,
        boss_slug="indie_group_ccdg",
        boss_key="eny_0090_wgabyss",
        boss_name="破潮之像",
        dungeon_name="危机合约",
        duration_ms=1,
        total_damage=8500000,
        contract_tag_score=10000,
        contract_tags=contract_tags,
    )

    detail_response = client.get(f"/api/battles/{battle_id}")
    assert detail_response.status_code == 200
    detail_battle = detail_response.json()["battle"]
    assert detail_battle["contractTagScore"] == expected_score
    assert [tag["tagId"] for tag in detail_battle["contractTags"][:2]] == preferred_tag_ids
    assert detail_battle["contractTags"][0]["iconId"] == "icon_activity_contract_tag_303"
    assert detail_battle["contractTags"][0]["iconUrl"] == "/images/contract-tag/icon_activity_contract_tag_303.png"

    ranking_response = client.get("/api/bosses/indie_group_ccdg/rankings?metric=dps")
    assert ranking_response.status_code == 200
    ranking_row = next(row for row in ranking_response.json()["rows"] if row["battleId"] == battle_id)
    assert ranking_row["contractTagScore"] == expected_score
    assert ranking_row["contractTags"][1]["score"] == 3
    assert ranking_row["contractTags"][1]["iconId"] == "icon_activity_contract_tag_116"

    account_response = client.get(f"/api/battles/users/{ranking_row['accountId']}/rankings")
    assert account_response.status_code == 200
    account_row = next(row for row in account_response.json()["rankings"] if row["battleId"] == battle_id)
    assert account_row["contractTagScore"] == expected_score
    assert account_row["contractTags"][0]["buffId"] == "global_buff_cc_chr_main_attribute_down"

    home_response = client.get("/api/home/hot-bosses")
    assert home_response.status_code == 200
    contract_card = next(card for card in home_response.json() if card["bossSlug"] == "indie_group_ccdg")
    assert any(
        run["contractTags"]
        and run["contractTags"][0]["tagId"] == 102802
        for run in contract_card["topSpeedRuns"]
    )

    share_response = client.get(f"/api/battles/{battle_id}/share-summary")
    assert share_response.status_code == 200
    assert share_response.json()["contractTags"][0]["tagId"] == 102802


def test_contract_tags_are_hidden_for_non_contract_bosses() -> None:
    client = TestClient(app)
    contract_tags = [
        {
            "tagId": 102803,
            "score": 1,
            "name": "队列：萎缩",
            "buffId": "global_buff_cc_chr_main_attribute_down",
            "groupId": 1028,
            "conflictId": "c1028",
            "values": {"attr": 0.9},
        }
    ]
    battle_id = _upload_real_battle(
        client,
        duration_ms=37398,
        total_damage=2641058,
        contract_tag_score=3,
        contract_tags=contract_tags,
    )

    with SessionLocal() as session:
        row = session.get(UploadedBattleRecord, battle_id)
        assert row is not None
        assert row.contract_tag_score is None
        assert row.contract_tags_json is None
        row.contract_tag_score = 3
        row.contract_tags_json = (
            '[{"tagId":102803,"score":1,"name":"队列：萎缩",'
            '"iconId":"icon_activity_contract_tag_304",'
            '"iconUrl":"/images/contract-tag/icon_activity_contract_tag_304.png"}]'
        )
        session.commit()

    detail_response = client.get(f"/api/battles/{battle_id}")
    assert detail_response.status_code == 200
    detail_battle = detail_response.json()["battle"]
    assert detail_battle["contractTagScore"] is None
    assert detail_battle["contractTags"] == []

    ranking_response = client.get("/api/bosses/dung01_group_bossrush01/rankings?metric=dps")
    assert ranking_response.status_code == 200
    ranking_row = next(row for row in ranking_response.json()["rows"] if row["battleId"] == battle_id)
    assert ranking_row["contractTagScore"] is None
    assert ranking_row["contractTags"] == []

    share_response = client.get(f"/api/battles/{battle_id}/share-summary")
    assert share_response.status_code == 200
    assert share_response.json()["contractTagScore"] is None
    assert share_response.json()["contractTags"] == []


def test_local_game_catalog_endpoints() -> None:
    client = TestClient(app)

    summary_response = client.get("/api/game-data/catalog")
    assert summary_response.status_code == 200
    summary_payload = summary_response.json()
    assert summary_payload["modules"]["character"]["count"] > 0
    assert summary_payload["modules"]["dungeon"]["count"] > 0
    assert summary_payload["modules"]["buff"]["count"] > 0
    assert summary_payload["modules"]["skill"]["count"] > 0
    assert summary_payload["modules"]["attribute_type"]["count"] > 0
    assert summary_payload["modules"]["character"]["source"]["primary"] == "local_table"
    assert summary_payload["modules"]["weapon"]["source"]["primary"] == "local_table"
    assert summary_payload["modules"]["enemy"]["source"]["primary"] == "local_table"
    assert summary_payload["modules"]["equip"]["source"]["primary"] == "local_table"
    assert summary_payload["modules"]["dungeon"]["source"]["primary"] == "local_table"
    assert summary_payload["modules"]["buff"]["source"]["primary"] == "local_static"
    assert summary_payload["modules"]["skill"]["source"]["primary"] == "local_static"
    assert summary_payload["modules"]["attribute_type"]["source"]["primary"] == "local_static"
    assert summary_payload["modules"]["dungeon"]["akedataSupplementCount"] == 1
    for kind in ("buff", "skill"):
        module = summary_payload["modules"][kind]
        assert module["count"] == module["localCount"] + module["akedataSupplementCount"]
        assert module["akedataSupplementCount"] == module["decodedCoverage"]["decodedOnlyCount"]

    list_response = client.get("/api/game-data/character")
    assert list_response.status_code == 200
    list_payload = list_response.json()
    assert list_payload["kind"] == "character"
    assert list_payload["count"] > 0

    detail_response = client.get("/api/game-data/character/chr_0002_endminm")
    assert detail_response.status_code == 200
    detail_payload = detail_response.json()
    assert detail_payload["source"]["primary"] == "local_table"
    assert detail_payload["detail"]["charId"] == "chr_0002_endminm"

    equip_detail_response = client.get("/api/game-data/equip/suit_atb01")
    assert equip_detail_response.status_code == 200
    equip_detail_payload = equip_detail_response.json()
    assert equip_detail_payload["source"]["primary"] == "local_table"
    assert equip_detail_payload["detail"]["suitID"] == "suit_atb01"
    assert equip_detail_payload["detail"]["passiveSkillId"] == "passive_equipsuit_combosuit_01"
    assert equip_detail_payload["detail"]["value"]["dmg_up"] == 0.16

    dungeon_fallback_response = client.get("/api/game-data/dungeon/dungeon_null")
    assert dungeon_fallback_response.status_code == 200
    dungeon_fallback_payload = dungeon_fallback_response.json()
    assert dungeon_fallback_payload["source"]["primary"] == "akedata_mirror"
    assert dungeon_fallback_payload["detail"]["templateId"] == "dungeon_null"

    buff_detail_response = client.get("/api/game-data/buff/buff_common_affixes_enhance_crystal")
    assert buff_detail_response.status_code == 200
    buff_detail_payload = buff_detail_response.json()
    assert buff_detail_payload["id"] == "buff_common_affixes_enhance_crystal"
    assert buff_detail_payload["source"]["primary"] == "local_static"
    assert buff_detail_payload["localBinary"]["projectPath"]
    assert buff_detail_payload["detail"]["id"] == "buff_common_affixes_enhance_crystal"

    buff_fallback_response = client.get("/api/game-data/buff/buff_wpn_passive_break_01")
    assert buff_fallback_response.status_code == 200
    buff_fallback_payload = buff_fallback_response.json()
    assert buff_fallback_payload["source"]["primary"] == "akedata_mirror"
    assert buff_fallback_payload["detail"]["decodedAvailable"] is False

    local_undecoded_buff_response = client.get("/api/game-data/buff/buff_common_cryst_fire_triggered")
    assert local_undecoded_buff_response.status_code == 200
    local_undecoded_buff_payload = local_undecoded_buff_response.json()
    assert local_undecoded_buff_payload["source"]["primary"] == "local_static"
    assert local_undecoded_buff_payload["detail"]["decodedAvailable"] is False
    binary_probe = local_undecoded_buff_payload["binaryProbe"]
    assert isinstance(binary_probe["firstByte"], int)
    assert binary_probe["formatHeaderHex"].startswith(f"{binary_probe['firstByte']:02x}")
    assert binary_probe["source"] == "official_typed_memorypack_fields"
    assert binary_probe["schemaVersion"] == 3
    assert binary_probe["auditEvidenceRef"]["assetId"] == "buff_common_cryst_fire_triggered"
    assert "typedSemanticFields" not in binary_probe
    assert binary_probe["rdpsKeyHints"] == []
    native_fields = client.get("/api/game-semantics/buff/buff_common_cryst_fire_triggered").json()["detail"]["blackboard"]
    assert any(
        item["key"] == "duration" and item["defaultDouble"] == 5.0 and item["isDynamic"] is True
        for item in native_fields
    )
    assert any(
        item["key"] == "phy_dmg_up" and item["value"] == 0.2
        for item in local_undecoded_buff_payload["binaryProbe"]["blackboardDoubleHints"]
    )
    assert local_undecoded_buff_payload["binaryProbe"]["createdBuffIds"] == [
        "buff_common_cryst_triggered_fx",
        "buff_common_cryst_triggered_start",
        "buff_common_frozen",
    ]
    assert any(
        item["value"] == "buff_common_frozen"
        and item["source"] == "official_typed_memorypack_fields" and item["field"] == "buffId"
        for item in local_undecoded_buff_payload["binaryProbe"]["actionReferenceContexts"]
    )

    local_direct_assign_buff_response = client.get("/api/game-data/buff/buff_common_cryst_triggered")
    assert local_direct_assign_buff_response.status_code == 200
    local_direct_assign_buff_payload = local_direct_assign_buff_response.json()
    assert local_direct_assign_buff_payload["binaryProbe"]["createdBuffIds"] == ["buff_common_frozen"]
    assert any(
        item["value"] == "buff_common_frozen"
        and item["source"] == "official_typed_memorypack_fields" and item["field"] == "buffId"
        for item in local_direct_assign_buff_payload["binaryProbe"]["actionReferenceContexts"]
    )

    skill_detail_response = client.get("/api/game-data/skill/chr_0011_seraph_ultimate_skill")
    assert skill_detail_response.status_code == 200
    skill_detail_payload = skill_detail_response.json()
    assert skill_detail_payload["id"] == "chr_0011_seraph_ultimate_skill"
    assert skill_detail_payload["source"]["primary"] == "local_static"
    assert skill_detail_payload["localBinary"]["projectPath"]
    assert skill_detail_payload["detail"].get("skillId", skill_detail_payload["detail"].get("id")) == "chr_0011_seraph_ultimate_skill"

    skill_fallback_response = client.get("/api/game-data/skill/wpn_passive_break_01")
    assert skill_fallback_response.status_code == 200
    skill_fallback_payload = skill_fallback_response.json()
    assert skill_fallback_payload["source"]["primary"] == "akedata_mirror"
    assert skill_fallback_payload["detail"]["decodedAvailable"] is False

    local_undecoded_skill_response = client.get("/api/game-data/skill/eny_0092_slbomb_skill03")
    assert local_undecoded_skill_response.status_code == 200
    local_undecoded_skill_payload = local_undecoded_skill_response.json()
    assert local_undecoded_skill_payload["source"]["primary"] == "local_static"
    assert local_undecoded_skill_payload["detail"]["decodedAvailable"] is False
    skill_binary_probe = local_undecoded_skill_payload["binaryProbe"]
    assert isinstance(skill_binary_probe["firstByte"], int)
    assert skill_binary_probe["formatHeaderHex"].startswith(f"{skill_binary_probe['firstByte']:02x}")
    assert skill_binary_probe["source"] == "official_typed_memorypack_fields"
    assert skill_binary_probe["rdpsKeyHints"] == []
    assert any(
        item["key"] == "damage_scale" and item["value"] == 3.1
        for item in local_undecoded_skill_payload["binaryProbe"]["blackboardDoubleHints"]
    )
    assert local_undecoded_skill_payload["binaryProbe"]["createdBuffIds"] == [
        "buff_eny_0092_slbomb_damage_bomb"
    ]
    assert any(
        item["value"] == "buff_eny_0092_slbomb_damage_bomb"
        and item["source"] == "official_typed_memorypack_fields" and item["field"] == "buffId"
        for item in local_undecoded_skill_payload["binaryProbe"]["actionReferenceContexts"]
    )

    local_weapon_skill_response = client.get("/api/game-data/skill/sk_wpn_pistol_0009")
    assert local_weapon_skill_response.status_code == 200
    local_weapon_skill_payload = local_weapon_skill_response.json()
    assert local_weapon_skill_payload["binaryProbe"]["rdpsKeyHints"] == []
    assert local_weapon_skill_payload["binaryProbe"]["auditEvidenceRef"]["assetId"] == "sk_wpn_pistol_0009"
    assert "typedSemanticFields" not in local_weapon_skill_payload["binaryProbe"]
    assert any(
        item["value"] == "buff_wpn_pistol_0009_dmg_taken_up_f"
        for item in local_weapon_skill_payload["binaryProbe"]["actionReferenceContexts"]
    )

    attribute_detail_response = client.get("/api/game-data/attribute_type/67")
    assert attribute_detail_response.status_code == 200
    attribute_detail_payload = attribute_detail_response.json()
    assert attribute_detail_payload["source"]["primary"] == "local_static"
    assert attribute_detail_payload["detail"]["attributeType"] == 67


def test_local_game_semantic_endpoints() -> None:
    client = TestClient(app)

    summary_response = client.get("/api/game-semantics/catalog")
    assert summary_response.status_code == 200
    summary_payload = summary_response.json()
    assert summary_payload["modules"]["buff"]["count"] > 0
    assert summary_payload["modules"]["skill"]["count"] > 0
    assert summary_payload["modules"]["attribute_type"]["count"] > 0
    assert summary_payload["modules"]["buff"]["source"]["primary"] == "local_semantics"

    buff_detail_response = client.get("/api/game-semantics/buff/buff_common_affixes_enhance_crystal")
    assert buff_detail_response.status_code == 200
    buff_detail_payload = buff_detail_response.json()
    assert buff_detail_payload["source"]["primary"] == "local_semantics"
    assert buff_detail_payload["detail"]["attributeEffects"][0]["attributeTypeName"] == "CrystEnhancedDmgIncrease"
    assert buff_detail_payload["detail"]["blackboard"][0]["key"] == "rate"
    assert "buff_common_affixes_enhance_crystal_default_child" in buff_detail_payload["detail"]["referencedBuffIds"]

    binary_buff_detail_response = client.get("/api/game-semantics/buff/buff_common_cryst_fire_triggered")
    assert binary_buff_detail_response.status_code == 200
    binary_buff_detail_payload = binary_buff_detail_response.json()
    assert binary_buff_detail_payload["detail"]["semanticFlags"]["decodedDetailMissing"] is False
    assert any(
        item["key"] == "phy_dmg_up" and item["value"] == 0.2
        for item in binary_buff_detail_payload["detail"]["binaryProbe"]["blackboardDoubleHints"]
    )
    assert binary_buff_detail_payload["detail"]["createdBuffSource"] == "official_typed_memorypack_fields"
    assert binary_buff_detail_payload["detail"]["createdBuffIds"] == [
        "buff_common_cryst_triggered_fx",
        "buff_common_cryst_triggered_start",
        "buff_common_frozen",
    ]
    assert "buff_common_frozen" in binary_buff_detail_payload["detail"]["referencedBuffIds"]

    direct_assign_buff_detail_response = client.get("/api/game-semantics/buff/buff_common_cryst_triggered")
    assert direct_assign_buff_detail_response.status_code == 200
    direct_assign_buff_detail_payload = direct_assign_buff_detail_response.json()
    assert direct_assign_buff_detail_payload["detail"]["createdBuffDirectAssignValues"] == [
        {
            "serverActionIndex": 0,
            "buffId": "buff_common_frozen",
            "targetKey": "duration",
            "inputValueKey": "",
            "numericValue": 5.0,
            "encoding": None,
            "source": "official_typed_memorypack_fields",
        }
    ]
    assert "buff_common_frozen" in direct_assign_buff_detail_payload["detail"]["referencedBuffIds"]

    skill_detail_response = client.get("/api/game-semantics/skill/chr_0011_seraph_ultimate_skill")
    assert skill_detail_response.status_code == 200
    skill_detail_payload = skill_detail_response.json()
    assert skill_detail_payload["detail"]["castType"] == "Active"
    assert skill_detail_payload["detail"]["timelineActionCount"] > 0
    assert any(action_type.startswith("PlayAnimationAction") for action_type in skill_detail_payload["detail"]["actionTypes"])

    binary_skill_detail_response = client.get("/api/game-semantics/skill/sk_wpn_pistol_0009")
    assert binary_skill_detail_response.status_code == 200
    binary_skill_detail_payload = binary_skill_detail_response.json()
    assert binary_skill_detail_payload["detail"]["createdBuffSource"] == "official_typed_memorypack_fields"
    assert binary_skill_detail_payload["detail"]["createdBuffIds"] == [
        "buff_wpn_passive_spirit_01",
        "buff_wpn_pistol_0009_cd",
        "buff_wpn_pistol_0009_dmg_taken_up_c",
        "buff_wpn_pistol_0009_dmg_taken_up_f",
        "buff_wpn_pistol_0009_dmg_taken_up_n",
        "buff_wpn_pistol_0009_dmg_taken_up_p",
    ]
    assert "buff_wpn_pistol_0009_cd" in binary_skill_detail_payload["detail"]["referencedBuffIds"]
    assert "buff_wpn_passive_spirit_01" in binary_skill_detail_payload["detail"]["referencedBuffIds"]
    assert any(item["buffId"] == "buff_wpn_passive_spirit_01" and item["path"] == "$.toggleBuffs[0].buffs[0].buffId"
               for item in binary_skill_detail_payload["detail"]["createdBuffActions"])
    assert binary_skill_detail_payload["detail"]["binaryProbe"]["source"] == "official_typed_memorypack_fields"

    attribute_detail_response = client.get("/api/game-semantics/attribute_type/67")
    assert attribute_detail_response.status_code == 200
    attribute_detail_payload = attribute_detail_response.json()
    assert attribute_detail_payload["detail"]["slug"] == "cryst_enhanced_dmg_increase_alt"


def test_local_game_semantic_hint_endpoints() -> None:
    client = TestClient(app)
    response = client.get("/api/game-semantics/hints/catalog")
    assert response.status_code == 200
    payload = response.json()
    assert payload["modules"]["buff"]["count"] > 0
    assert payload["modules"]["skill"]["count"] > 0
    assert payload["modules"]["attribute_type"]["count"] == 0
    for identity in ("buff_common_affixes_enhance_crystal", "buff_common_cryst_fire_triggered"):
        response = client.get(f"/api/game-semantics/hints/buff/{identity}")
        assert response.status_code == 200
        detail = response.json()["detail"]
        assert detail["source"] == "official_typed_memorypack_fields"
        assert "classification" not in detail
        assert detail["effectHints"] == detail["resolvedEffectHints"] == []
        assert detail["referenceSource"] == "official_typed_memorypack_fields"
    assert client.get("/api/game-semantics/hints/attribute_type/67").status_code == 404
    response = client.get("/api/game-semantics/hints/skill/chr_0011_seraph_ultimate_skill")
    assert response.status_code == 200
    assert response.json()["detail"]["source"] == "official_typed_memorypack_fields"


def test_verify_raw_log_integrity_endpoint() -> None:
    client = TestClient(app)
    content = "HP_V2 #1 hit=17\nBUFF_START demo\n"
    proof = build_raw_log_proof(content.encode("utf-8"), file_name="sample.log", meta={"hit_count": 1})

    ok_response = client.post(
        "/api/integrity/verify-raw-log",
        json={"content": content, "proof": proof},
    )
    assert ok_response.status_code == 200
    ok_payload = ok_response.json()
    assert ok_payload["verified"] is True
    assert ok_payload["issues"] == []

    bad_response = client.post(
        "/api/integrity/verify-raw-log",
        json={"content": content + "HP_V2 #2 hit=19\n", "proof": proof},
    )
    assert bad_response.status_code == 200
    bad_payload = bad_response.json()
    assert bad_payload["verified"] is False
    assert "proof.sha256 mismatch" in bad_payload["issues"]


def test_preflight_raw_log_upload_rejects_tampered_document() -> None:
    client = TestClient(app)
    content = "HP_V2 #1 hit=17\nBUFF_START demo\n"
    proof = build_raw_log_proof(content.encode("utf-8"), file_name="sample.log", meta={"hit_count": 1})

    ok_response = client.post(
        "/api/integrity/preflight-raw-log-upload",
        json={
            "file_name": "sample.log",
            "content": content,
            "proof": proof,
            "integrity_gate": {
                "tamper_suspected": False,
                "integrity_proof_present": True,
                "reasons": [],
            },
        },
    )
    assert ok_response.status_code == 200
    assert ok_response.json()["accepted"] is True

    reject_response = client.post(
        "/api/integrity/preflight-raw-log-upload",
        json={
            "file_name": "sample.log",
            "content": content,
            "proof": proof,
            "integrity_gate": {
                "tamper_suspected": True,
                "integrity_proof_present": True,
                "reasons": ["proof.sha256 mismatch"],
            },
        },
    )
    assert reject_response.status_code == 422
    assert reject_response.json()["error"]["code"] == "raw_log_rejected"


def test_uploader_battle_upload_endpoints() -> None:
    client = TestClient(app)
    fingerprint = f"fp-battle-rodin-{uuid4().hex}"
    email = _unique_email("battle-uploader")
    nickname = _unique_nickname("BattleUploader")

    send_response = client.post(
        "/api/auth/send-code",
        json={"email": email, "purpose": "uploader_login"},
    )
    debug_code = send_response.json()["debugCode"]
    verify_response = client.post(
        "/api/auth/verify-code",
        json={"email": email, "purpose": "uploader_login", "code": debug_code},
    )
    profile_token = verify_response.json()["profileSetupToken"]
    complete_response = client.post(
        "/api/auth/complete-profile",
        json={"profileSetupToken": profile_token, "nickname": nickname},
    )
    session_token = complete_response.json()["sessionToken"]
    assert session_token

    headers = _uploader_auth_headers(session_token)
    payload = {
        "battle": {
            "dungeonKey": "dung01_group_bossrush01",
            "dungeonName": "危境再现",
            "dungeonContextId": "dung01_group_bossrush01",
            "dungeonIdentitySource": "dungeon_context",
            "bossKey": "eny_0051_rodin",
            "bossName": "“碾骨之拳”罗丹",
            "battleStartAt": "2026-04-22T10:24:58+08:00",
            "battleEndAt": "2026-04-22T10:26:15+08:00",
            "durationMs": 77000,
            "clearFlag": True,
            "totalDamage": 2345678,
            "totalDps": 30463.35,
            "roster": [
                {
                    "slot": 1,
                    "characterKey": "chr_0027_tangtang",
                    "characterName": "唐糖",
                    "accountDisplayName": "ignored-by-server",
                    "weapon": {
                        "weaponTemplate": "wpn_pistol_0011",
                        "weaponName": "落草",
                        "weaponLevel": 90,
                        "weaponRefine": 0,
                        "iconUrl": "/public/charpack/weapon/wpn_pistol_0011.png",
                    },
                },
                {
                    "slot": 2,
                    "characterKey": "chr_0004_pelica",
                    "characterName": "佩丽卡",
                    "accountDisplayName": "ignored-by-server",
                },
            ],
            "battleFingerprint": fingerprint,
            "parserVersion": "raw-log-parser-v59",
            "rulesVersion": "raw-log-parser-v44",
            "timeSource": "game_timer",
            "timelineZeroSource": "official_timer_start",
            "timerStartSeen": True,
            "timerEndSeen": True,
            "officialTimerStartSeen": True,
            "officialTimerEndSeen": True,
            "officialTimerObservedElapsedMs": 79_000,
            "packetTimerElapsedMs": 70_000,
            "timerIntegrityOk": True,
            "timerIntegrityReasons": [],
            "timerEvidence": official_timer_evidence(77000, "dung01_group_bossrush01"),
            "timerStartInferred": False,
            "timerWindowValid": True,
            "rdpsPreflightOk": True,
            "rdpsStrictOk": True,
            "rdpsPreflightBlockerCount": 0,
        },
        "participants": [
            {
                "characterKey": "chr_0027_tangtang",
                "characterName": "唐糖",
                "accountDisplayName": "ignored-by-server",
                "totalDamage": 1600000,
                "dps": 10389.61,
                "rdps": 11123.45,
                "maxHit": 55123,
                "critRate": 0.24,
            },
            {
                "characterKey": "chr_0004_pelica",
                "characterName": "佩丽卡",
                "accountDisplayName": "ignored-by-server",
                "totalDamage": 745678,
                "dps": 5643.73,
                "rdps": 6201.11,
                "maxHit": 33110,
                "critRate": 0.19,
            },
        ],
        "timelineEvents": [
            {
                "tsMsFromStart": 2500,
                "laneType": "skill",
                "sourceCharacterKey": "chr_0027_tangtang",
                "sourceCharacterName": "唐糖",
                "targetCharacterKey": "eny_0051_rodin",
                "targetCharacterName": "罗丁",
                "eventType": "damage",
                "eventKey": "chr_0027_tangtang_attack5",
                "eventGroupKey": "chr_0027_tangtang_attack5::eny_0051_rodin::1",
                "eventName": "普攻五段",
                "value": 5121,
                "rdpsContributions": [
                    {
                        "characterKey": "chr_0027_tangtang",
                        "characterName": "唐糖",
                        "value": 5121,
                    },
                    {
                        "characterKey": "chr_0004_pelica",
                        "characterName": "佩丽卡",
                        "value": 384.5,
                    },
                ],
                "durationMs": None,
                "important": True,
            }
        ],
        "roleSkillStats": [
            {
                "characterKey": "chr_0027_tangtang",
                "characterName": "唐糖",
                "accountDisplayName": "ignored-by-server",
                "skillKey": "chr_0027_tangtang_attack5",
                "skillName": "普攻五段",
                "castCount": 3,
                "totalDamage": 15600,
                "avgDamage": 5200,
                "maxDamage": 6123,
            }
        ],
    }

    duplicate_before = client.post(
        "/api/uploader/battles/check-duplicate",
        json={
            "battleFingerprint": payload["battle"]["battleFingerprint"],
            "bossKey": payload["battle"]["bossKey"],
            "parserVersion": payload["battle"]["parserVersion"],
            "rulesVersion": payload["battle"]["rulesVersion"],
        },
        headers=headers,
    )
    assert duplicate_before.status_code == 200
    assert duplicate_before.json()["duplicate"] is False

    unfinished_payload = deepcopy(payload)
    unfinished_payload["battle"]["battleFingerprint"] = f"fp-unfinished-{uuid4().hex}"
    unfinished_payload["battle"]["clearFlag"] = False
    unfinished_response = client.post("/api/uploader/battles", json=unfinished_payload, headers=headers)
    assert unfinished_response.status_code == 200
    unfinished_battle_id = unfinished_response.json()["battleId"]
    assert client.get(f"/api/battles/{unfinished_battle_id}").status_code == 404
    unfinished_authed_detail = client.get(f"/api/battles/{unfinished_battle_id}", headers=headers)
    assert unfinished_authed_detail.status_code == 200
    assert unfinished_authed_detail.json()["battle"]["clearFlag"] is False

    no_completion_signal_payload = deepcopy(payload)
    no_completion_signal_payload["battle"]["battleFingerprint"] = f"fp-no-completion-signal-{uuid4().hex}"
    no_completion_signal_payload["battle"]["clearFlag"] = True
    no_completion_signal_payload["battle"]["timerEndSeen"] = False
    no_completion_signal_payload["battle"]["officialTimerEndSeen"] = False
    no_completion_signal_response = client.post("/api/uploader/battles", json=no_completion_signal_payload, headers=headers)
    assert no_completion_signal_response.status_code == 200
    no_completion_signal_battle_id = no_completion_signal_response.json()["battleId"]
    assert client.get(f"/api/battles/{no_completion_signal_battle_id}").status_code == 404
    no_completion_signal_authed_detail = client.get(f"/api/battles/{no_completion_signal_battle_id}", headers=headers)
    assert no_completion_signal_authed_detail.status_code == 200
    assert no_completion_signal_authed_detail.json()["battle"]["clearFlag"] is False

    short_damage_payload = deepcopy(payload)
    short_damage_payload["battle"]["battleFingerprint"] = f"fp-short-damage-{uuid4().hex}"
    short_damage_payload["battle"]["dungeonKey"] = "dung01_group_bossrush02"
    short_damage_payload["battle"]["bossKey"] = "eny_0045_agtrinit"
    short_damage_payload["battle"]["bossName"] = "三位一体"
    short_damage_payload["battle"]["totalDamage"] = 938073
    short_damage_payload["battle"]["totalDps"] = 44813
    short_damage_response = client.post("/api/uploader/battles", json=short_damage_payload, headers=headers)
    assert short_damage_response.status_code == 200
    short_damage_battle_id = short_damage_response.json()["battleId"]
    assert client.get(f"/api/battles/{short_damage_battle_id}").status_code == 200
    short_damage_authed_detail = client.get(f"/api/battles/{short_damage_battle_id}", headers=headers)
    assert short_damage_authed_detail.status_code == 200
    assert short_damage_authed_detail.json()["battle"]["clearFlag"] is True

    upload_response = client.post("/api/uploader/battles", json=payload, headers=headers)
    assert upload_response.status_code == 200
    battle_id = upload_response.json()["battleId"]
    assert upload_response.json()["battleUrl"] == f"/battle/{battle_id}"

    with SessionLocal() as session:
        saved = session.get(UploadedBattleRecord, battle_id)
        import json
        assert json.loads(saved.timer_evidence_json) == payload['battle']['timerEvidence']

    changed_clock_payload = deepcopy(payload)
    changed_clock_payload['battle']['battleFingerprint'] = f'fp-changed-clock-{uuid4().hex}'
    changed_clock_payload['battle']['durationMs'] = 1000
    changed_clock_payload['battle']['timerIntegrityOk'] = True
    changed_clock_response = client.post('/api/uploader/battles', json=changed_clock_payload, headers=headers)
    assert changed_clock_response.status_code == 422
    assert changed_clock_response.json()['error']['code'] == 'timer_integrity_failed'

    lower_rank_payload = deepcopy(payload)
    lower_rank_payload["battle"]["battleFingerprint"] = f"fp-lower-rank-{uuid4().hex}"
    lower_rank_payload["battle"]["durationMs"] = 99000
    lower_rank_payload["battle"]["timerEvidence"] = official_timer_evidence(99000, lower_rank_payload["battle"]["dungeonContextId"])
    lower_rank_payload["battle"]["totalDamage"] = 2100000
    lower_rank_payload["battle"]["totalDps"] = 21212.12
    lower_rank_payload["participants"][0]["totalDamage"] = 1400000
    lower_rank_payload["participants"][0]["dps"] = 14141.41
    lower_rank_payload["participants"][0]["rdps"] = 14141.41
    lower_rank_payload["participants"][1]["totalDamage"] = 700000
    lower_rank_payload["participants"][1]["dps"] = 7070.71
    lower_rank_payload["participants"][1]["rdps"] = 7070.71
    lower_rank_response = client.post("/api/uploader/battles", json=lower_rank_payload, headers=headers)
    assert lower_rank_response.status_code == 200
    lower_rank_battle_id = lower_rank_response.json()["battleId"]
    # 有链接即可查看：非最佳但已完成的公开战斗匿名也能看（不再要求上榜 best-per-account）。
    lower_rank_anon_detail = client.get(f"/api/battles/{lower_rank_battle_id}")
    assert lower_rank_anon_detail.status_code == 200
    assert lower_rank_anon_detail.json()["battle"]["clearFlag"] is True
    lower_rank_authed_detail = client.get(f"/api/battles/{lower_rank_battle_id}", headers=headers)
    assert lower_rank_authed_detail.status_code == 200
    assert lower_rank_authed_detail.json()["battle"]["clearFlag"] is True

    ranking_response = client.get("/api/bosses/dung01_group_bossrush01/rankings?metric=dps")
    assert ranking_response.status_code == 200
    ranking_battle_ids = [row["battleId"] for row in ranking_response.json()["rows"]]
    assert battle_id in ranking_battle_ids
    assert lower_rank_battle_id not in ranking_battle_ids
    assert unfinished_battle_id not in ranking_battle_ids
    assert no_completion_signal_battle_id not in ranking_battle_ids

    hot_bosses_response = client.get("/api/home/hot-bosses")
    assert hot_bosses_response.status_code == 200
    rodin_card = next(
        card for card in hot_bosses_response.json() if card["bossSlug"] == "dung01_group_bossrush01"
    )
    hot_battle_ids = [run["battleId"] for run in rodin_card["topSpeedRuns"]]
    assert unfinished_battle_id not in hot_battle_ids
    assert no_completion_signal_battle_id not in hot_battle_ids
    hot_durations = [run["durationMs"] for run in rodin_card["topSpeedRuns"]]
    assert hot_durations == sorted(hot_durations)

    duplicate_after = client.post(
        "/api/uploader/battles/check-duplicate",
        json={
            "battleFingerprint": payload["battle"]["battleFingerprint"],
            "bossKey": payload["battle"]["bossKey"],
            "parserVersion": payload["battle"]["parserVersion"],
            "rulesVersion": payload["battle"]["rulesVersion"],
        },
        headers=headers,
    )
    assert duplicate_after.status_code == 200
    assert duplicate_after.json()["duplicate"] is True
    assert duplicate_after.json()["battleId"] == battle_id

    refreshed_payload = deepcopy(payload)
    refreshed_payload["participants"][0]["rdps"] = 12345.67
    refresh_response = client.post("/api/uploader/battles", json=refreshed_payload, headers=headers)
    assert refresh_response.status_code == 200
    assert refresh_response.json()["battleId"] == battle_id

    detail_response = client.get(f"/api/battles/{battle_id}")
    assert detail_response.status_code == 200
    detail_payload = detail_response.json()
    assert detail_payload["battle"]["bossName"] == "“碾骨之拳”罗丹"
    assert detail_payload["participants"][0]["accountDisplayName"] == nickname
    assert detail_payload["participants"][0]["rdps"] == 12345.67
    assert detail_payload["battle"]["battleFingerprint"] == fingerprint
    assert detail_payload["battle"]["timeSource"] == "game_timer"
    assert detail_payload["battle"]["timelineZeroSource"] == "official_timer_start"
    assert detail_payload["battle"]["officialTimerStartSeen"] is True
    assert detail_payload["battle"]["timerStartInferred"] is False
    assert detail_payload["battle"]["roster"][0]["weapon"]["weaponRefine"] == 1
    assert detail_payload["timelineEvents"][0]["eventGroupKey"] == "chr_0027_tangtang_attack5::eny_0051_rodin::1"
    assert detail_payload["timelineEvents"][0]["rdpsContributions"][0]["characterName"] == "汤汤"
    assert detail_payload["timelineEvents"][0]["rdpsContributions"][1]["value"] == 384.5

    authed_detail_response = client.get(f"/api/battles/{battle_id}", headers=headers)
    assert authed_detail_response.status_code == 200
    assert authed_detail_response.json()["viewerCapabilities"]["isUploader"] is True
    assert authed_detail_response.json()["viewerCapabilities"]["canDelete"] is True

    new_nickname = _unique_nickname("BattleRenamed")
    update_nickname_response = client.patch(
        "/api/auth/me/nickname",
        json={"nickname": new_nickname},
        headers=headers,
    )
    assert update_nickname_response.status_code == 200
    assert update_nickname_response.json()["user"]["nickname"] == new_nickname

    renamed_detail_response = client.get(f"/api/battles/{battle_id}", headers=headers)
    assert renamed_detail_response.status_code == 200
    renamed_detail_payload = renamed_detail_response.json()
    assert renamed_detail_payload["participants"][0]["accountDisplayName"] == new_nickname
    assert renamed_detail_payload["battle"]["roster"][0]["accountDisplayName"] == new_nickname

    my_battles_response = client.get("/api/battles/mine", headers=headers)
    assert my_battles_response.status_code == 200
    my_battles_payload = my_battles_response.json()
    assert len(my_battles_payload["battles"]) == 5
    ranking_summaries = {ranking["battleId"]: ranking for ranking in my_battles_payload["rankings"]}
    assert battle_id in ranking_summaries
    assert no_completion_signal_battle_id not in ranking_summaries
    assert ranking_summaries[battle_id]["bossName"] == payload["battle"]["bossName"]
    assert ranking_summaries[battle_id]["rank"] >= 1
    assert 0 <= ranking_summaries[battle_id]["scorePercent"] <= 100
    assert ranking_summaries[battle_id]["rosterSummary"] == ["汤汤", "佩丽卡"]
    battle_summaries = {battle["id"]: battle for battle in my_battles_payload["battles"]}
    assert battle_summaries[battle_id]["status"] == "valid"
    assert battle_summaries[battle_id]["rosterSummary"] == ["汤汤", "佩丽卡"]
    assert battle_summaries[unfinished_battle_id]["status"] == "valid"
    assert battle_summaries[no_completion_signal_battle_id]["status"] == "valid"
    assert battle_summaries[short_damage_battle_id]["status"] == "valid"
    assert battle_summaries[lower_rank_battle_id]["status"] == "valid"

    delete_response = client.delete(f"/api/battles/{battle_id}", headers=headers)
    assert delete_response.status_code == 200
    assert delete_response.json()["ok"] is True

    my_battles_after_delete = client.get("/api/battles/mine", headers=headers)
    assert my_battles_after_delete.status_code == 200
    battle_summaries_after_delete = {
        battle["id"]: battle for battle in my_battles_after_delete.json()["battles"]
    }
    assert battle_summaries_after_delete[battle_id]["status"] == "deleted"
    ranking_ids_after_delete = {
        ranking["battleId"] for ranking in my_battles_after_delete.json()["rankings"]
    }
    assert battle_id not in ranking_ids_after_delete

    duplicate_after_delete = client.post(
        "/api/uploader/battles/check-duplicate",
        json={
            "battleFingerprint": payload["battle"]["battleFingerprint"],
            "bossKey": payload["battle"]["bossKey"],
            "parserVersion": payload["battle"]["parserVersion"],
            "rulesVersion": payload["battle"]["rulesVersion"],
        },
        headers=headers,
    )
    assert duplicate_after_delete.status_code == 200
    assert duplicate_after_delete.json()["duplicate"] is False

    detail_after_delete = client.get(f"/api/battles/{battle_id}")
    assert detail_after_delete.status_code == 404


def test_admin_dashboard_and_cross_account_battle_management(monkeypatch) -> None:
    client = TestClient(app)
    admin_email = _unique_email("admin")
    monkeypatch.setenv("ADMIN_EMAILS", admin_email)
    get_settings.cache_clear()

    try:
        user_email = _unique_email("managed-user")
        user_nickname = _unique_nickname("Managed")
        user_session = _register_password_user(
            client,
            email=user_email,
            nickname=user_nickname,
            purpose="uploader_login",
            password="managed-pass-123",
        )
        assert user_session

        fingerprint = f"fp-admin-dashboard-{uuid4().hex}"
        upload_headers = _uploader_auth_headers(user_session)
        payload = {
            "battle": {
                "dungeonKey": "dung02_group_bossrush02",
                "dungeonName": "危境再现",
                "dungeonContextId": "dung02_group_bossrush02",
                "dungeonIdentitySource": "dungeon_context",
                "bossKey": "eny_0079_nefarp2",
                "bossName": "聂菲斯",
                "battleStartAt": "2026-04-24T18:24:58+08:00",
                "battleEndAt": "2026-04-24T18:27:37+08:00",
                "durationMs": 159000,
                "clearFlag": True,
                "totalDamage": 5123456,
                "totalDps": 32223.0,
                "roster": [
                    {
                        "slot": 1,
                        "characterKey": "chr_0027_tangtang",
                        "characterName": "汤汤",
                        "accountDisplayName": "ignored-by-server",
                    }
                ],
                "battleFingerprint": fingerprint,
                "parserVersion": "raw-log-parser-v59",
                "rulesVersion": "raw-log-parser-v44",
            },
            "participants": [
                {
                    "characterKey": "chr_0027_tangtang",
                    "characterName": "汤汤",
                    "accountDisplayName": "ignored-by-server",
                    "totalDamage": 5123456,
                    "dps": 32223.0,
                    "rdps": 33888.0,
                    "maxHit": 188000,
                    "critRate": 0.26,
                }
            ],
            "timelineEvents": [],
            "roleSkillStats": [],
        }
        upload_response = client.post("/api/uploader/battles", json=payload, headers=upload_headers)
        assert upload_response.status_code == 200
        battle_id = upload_response.json()["battleId"]

        regular_email = _unique_email("regular-viewer")
        regular_nickname = _unique_nickname("Regular")
        regular_session = _register_password_user(
            client,
            email=regular_email,
            nickname=regular_nickname,
            purpose="uploader_login",
            password="regular-pass-123",
        )
        forbidden_response = client.get(
            "/api/admin/dashboard",
            headers={"Authorization": f"Bearer {regular_session}"},
        )
        assert forbidden_response.status_code == 403

        admin_session = _register_password_user(
            client,
            email=admin_email,
            nickname=_unique_nickname("Admin"),
            purpose="uploader_login",
            password="admin-pass-123",
        )
        assert admin_session

        me_response = client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {admin_session}"},
        )
        assert me_response.status_code == 200
        assert me_response.json()["user"]["isAdmin"] is True

        dashboard_response = client.get(
            "/api/admin/dashboard",
            headers={"Authorization": f"Bearer {admin_session}"},
        )
        assert dashboard_response.status_code == 200
        dashboard_payload = dashboard_response.json()
        assert dashboard_payload["overview"]["adminUsers"] >= 1
        managed_user_summary = next(user for user in dashboard_payload["users"] if user["email"] == user_email)
        assert managed_user_summary["createdAt"]
        assert managed_user_summary["totalBattles"] == 1
        assert managed_user_summary["validBattles"] == 1
        assert managed_user_summary["lastBattleAt"] == "2026-04-24T18:27:37+08:00"
        assert any(battle["id"] == battle_id for battle in dashboard_payload["battles"])

        disable_response = client.patch(
            f"/api/admin/users/{managed_user_summary['id']}/disabled",
            json={"disabled": True},
            headers={"Authorization": f"Bearer {admin_session}"},
        )
        assert disable_response.status_code == 200
        assert disable_response.json()["ok"] is True

        managed_me_after_disable = client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {user_session}"},
        )
        assert managed_me_after_disable.status_code == 200
        assert managed_me_after_disable.json()["authenticated"] is False

        disabled_login_response = client.post(
            "/api/auth/login",
            json={
                "email": user_email,
                "purpose": "uploader_login",
                "password": "managed-pass-123",
            },
        )
        assert disabled_login_response.status_code == 403
        assert disabled_login_response.json()["error"]["code"] == "account_disabled"

        enable_response = client.patch(
            f"/api/admin/users/{managed_user_summary['id']}/disabled",
            json={"disabled": False},
            headers={"Authorization": f"Bearer {admin_session}"},
        )
        assert enable_response.status_code == 200

        old_password_login_response = client.post(
            "/api/auth/login",
            json={
                "email": user_email,
                "purpose": "uploader_login",
                "password": "managed-pass-123",
            },
        )
        assert old_password_login_response.status_code == 200
        refreshed_user_session = old_password_login_response.json()["sessionToken"]
        assert refreshed_user_session

        reset_password_response = client.post(
            f"/api/admin/users/{managed_user_summary['id']}/reset-password",
            json={"newPassword": "managed-pass-456"},
            headers={"Authorization": f"Bearer {admin_session}"},
        )
        assert reset_password_response.status_code == 200

        stale_session_after_reset = client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {refreshed_user_session}"},
        )
        assert stale_session_after_reset.status_code == 200
        assert stale_session_after_reset.json()["authenticated"] is False

        old_password_after_reset = client.post(
            "/api/auth/login",
            json={
                "email": user_email,
                "purpose": "uploader_login",
                "password": "managed-pass-123",
            },
        )
        assert old_password_after_reset.status_code == 401

        new_password_login = client.post(
            "/api/auth/login",
            json={
                "email": user_email,
                "purpose": "uploader_login",
                "password": "managed-pass-456",
            },
        )
        assert new_password_login.status_code == 200
        promoted_session = new_password_login.json()["sessionToken"]
        assert promoted_session

        promote_response = client.patch(
            f"/api/admin/users/{managed_user_summary['id']}/admin",
            json={"isAdmin": True},
            headers={"Authorization": f"Bearer {admin_session}"},
        )
        assert promote_response.status_code == 200

        promoted_me = client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {promoted_session}"},
        )
        assert promoted_me.status_code == 200
        assert promoted_me.json()["authenticated"] is True
        assert promoted_me.json()["user"]["isAdmin"] is True

        demote_response = client.patch(
            f"/api/admin/users/{managed_user_summary['id']}/admin",
            json={"isAdmin": False},
            headers={"Authorization": f"Bearer {admin_session}"},
        )
        assert demote_response.status_code == 200

        demoted_me = client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {promoted_session}"},
        )
        assert demoted_me.status_code == 200
        assert demoted_me.json()["authenticated"] is True
        assert demoted_me.json()["user"]["isAdmin"] is False

        admin_detail_response = client.get(
            f"/api/battles/{battle_id}",
            headers={"Authorization": f"Bearer {admin_session}"},
        )
        assert admin_detail_response.status_code == 200
        assert admin_detail_response.json()["viewerCapabilities"]["isAdmin"] is True
        assert admin_detail_response.json()["viewerCapabilities"]["canDelete"] is True

        delete_response = client.delete(
            f"/api/admin/battles/{battle_id}",
            headers={"Authorization": f"Bearer {admin_session}"},
        )
        assert delete_response.status_code == 200
        assert delete_response.json()["ok"] is True

        user_delete_response = client.delete(
            f"/api/admin/users/{managed_user_summary['id']}",
            headers={"Authorization": f"Bearer {admin_session}"},
        )
        assert user_delete_response.status_code == 200

        deleted_detail_response = client.get(f"/api/battles/{battle_id}")
        assert deleted_detail_response.status_code == 404

        deleted_user_login = client.post(
            "/api/auth/login",
            json={
                "email": user_email,
                "purpose": "uploader_login",
                "password": "managed-pass-456",
            },
        )
        assert deleted_user_login.status_code == 401

        deleted_user_dashboard = client.get(
            "/api/admin/dashboard",
            headers={"Authorization": f"Bearer {admin_session}"},
        )
        assert deleted_user_dashboard.status_code == 200
        assert all(user["email"] != user_email for user in deleted_user_dashboard.json()["users"])
    finally:
        get_settings.cache_clear()
