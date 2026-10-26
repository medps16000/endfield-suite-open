from timer_samples import official_timer_evidence
"""对外排轴导出 API（/api/v1/battles/{id}/export）契约测试。"""
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.db.models import UploadedBattleRecord
from app.db.session import SessionLocal

UPLOADER_CLIENT_HEADERS = {
    "X-Endfield-Uploader-Name": "EndfieldLogsUploader",
    "X-Endfield-Uploader-Version": "2026.10.15.1",
    "X-Endfield-Parser-Version": "raw-log-parser-v59",
    "X-Endfield-Rules-Version": "raw-log-parser-v44",
}


def _register_uploader(client: TestClient) -> str:
    email = f"export-{uuid4().hex[:10]}@example.com"
    send = client.post("/api/auth/send-code", json={"email": email, "purpose": "uploader_login"})
    assert send.status_code == 200
    register = client.post(
        "/api/auth/register",
        json={
            "email": email,
            "purpose": "uploader_login",
            "password": "export-pass-123",
            "nickname": f"Export{uuid4().hex[:8]}",
            "code": send.json()["debugCode"],
        },
    )
    assert register.status_code == 200
    return register.json()["sessionToken"]


def _upload_battle(client: TestClient, *, with_casts: bool) -> str:
    session_token = _register_uploader(client)
    payload = {
        "battle": {
            "dungeonKey": "dung01_group_bossrush01",
            "dungeonName": "危境再现",
            "dungeonContextId": "dung01_group_bossrush01",
            "dungeonIdentitySource": "dungeon_context",
            "bossKey": "eny_0051_rodin",
            "bossName": "危境再现·罗丹",
            "battleStartAt": "2026-07-05T01:33:35+08:00",
            "battleEndAt": "2026-07-05T01:34:57+08:00",
            "durationMs": 82601,
            "clearFlag": True,
            "totalDamage": 4000000,
            "totalDps": 48425.08,
            "roster": [
                {
                    "slot": 1,
                    "characterKey": "chr_0016_laevat",
                    "characterName": "莱万汀",
                    "accountDisplayName": "ignored",
                    "characterLevel": 90,
                    "characterPotential": 0,
                    "weapon": {
                        "weaponTemplate": "wpn_sword_0006",
                        "weaponName": "夜幕",
                        "weaponLevel": 90,
                        "weaponRefine": 4,
                        "skills": [
                            {"skillKey": "wpn_attr_wisd_high", "level": 9, "potentialLevel": 0},
                            {"skillKey": "sk_wpn_sword_0006", "level": 4, "potentialLevel": 0},
                        ],
                    },
                    "equips": [
                        {
                            "slot": 0,
                            "itemId": "item_equip_t4_suit_atk02_hand_01",
                            "pieceName": "应龙之锐·手甲",
                            "suitName": "应龙之锐",
                            "enhanceLevels": [{"index": 1, "level": 3}],
                            "stats": [
                                {"slot": "main", "name": "防御力", "value": 42.0, "level": None},
                                {"slot": "sub1", "name": "敏捷", "value": 84.0, "level": 3},
                            ],
                        }
                    ],
                    "skills": [
                        {"skillKey": "chr_0016_laevat_attack1", "level": 12},
                        {"skillKey": "chr_0016_laevat_ultimate_skill", "level": 12},
                    ],
                }
            ],
            "battleFingerprint": f"fp-export-{uuid4().hex}",
            "parserVersion": "raw-log-parser-v59",
            "rulesVersion": "raw-log-parser-v44",
            "timeSource": "game_timer",
            "officialTimerStartSeen": True,
            "officialTimerEndSeen": True,
            "officialTimerObservedElapsedMs": 84_601,
            "packetTimerElapsedMs": 80_000,
            "timerIntegrityOk": True,
            "timerIntegrityReasons": [],
            "timerEvidence": official_timer_evidence(82601, "dung01_group_bossrush01"),
        },
        "participants": [
            {
                "characterKey": "chr_0016_laevat",
                "characterName": "莱万汀",
                "accountDisplayName": "ignored",
                "totalDamage": 4000000,
                "dps": 48425.08,
                "rdps": 48425.08,
                "maxHit": 19304,
                "critRate": 0.25,
            }
        ],
        "timelineEvents": [],
        "roleSkillStats": [],
    }
    if with_casts:
        payload["casts"] = [
            {
                "tsMsFromStart": 4030,
                "endMsFromStart": 4269,
                "characterKey": "chr_0016_laevat",
                "skillKey": "chr_0016_laevat_attack1",
                "skillName": "燃烬",
                "skillSource": "unknown",
            },
            {
                "tsMsFromStart": 30190,
                "endMsFromStart": 30857,
                "characterKey": "chr_0016_laevat",
                "skillKey": "chr_0016_laevat_ult_attack1",
                "skillName": "黄昏",
                "skillSource": "unknown",
            },
        ]
    headers = {"Authorization": f"Bearer {session_token}", **UPLOADER_CLIENT_HEADERS}
    upload = client.post("/api/uploader/battles", json=payload, headers=headers)
    assert upload.status_code == 200
    return upload.json()["battleId"]


def test_export_returns_schema_v1_with_casts_and_skills() -> None:
    client = TestClient(app)
    battle_id = _upload_battle(client, with_casts=True)

    response = client.get(f"/api/v1/battles/{battle_id}/export")
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"
    assert response.headers["cache-control"] == "public, max-age=60"
    etag = response.headers["etag"]
    # ETag = 内容哈希（重传覆盖后必须变化，不能用 battleId+版本这类固定值）
    assert etag.startswith('W/"') and len(etag) > 20

    payload = response.json()
    assert payload["schemaVersion"] == 1
    assert payload["battleId"] == battle_id
    assert payload["parserVersion"] == "raw-log-parser-v59"
    assert payload["dungeon"]["bossKey"] == "eny_0051_rodin"
    assert payload["dungeon"]["dungeonSlug"] == "dung01_group_bossrush01"
    assert payload["durationMs"] == 82601

    roster_entry = payload["roster"][0]
    assert roster_entry["characterKey"] == "chr_0016_laevat"
    assert roster_entry["characterLevel"] == 90
    assert roster_entry["weapon"]["weaponTemplate"] == "wpn_sword_0006"
    # weaponRefine = 显示精炼数（包内 0 基 +1，夹 1..6），与游戏 UI 一致——导出契约沿用
    assert roster_entry["weapon"]["weaponRefine"] == 5
    assert roster_entry["weapon"]["weaponLevel"] == 90
    assert {"skillKey": "sk_wpn_sword_0006", "level": 4, "potentialLevel": 0} in roster_entry["weapon"]["skills"]
    assert roster_entry["equips"][0]["itemId"] == "item_equip_t4_suit_atk02_hand_01"
    assert roster_entry["equips"][0]["enhanceLevels"] == [{"index": 1, "level": 3}]
    assert roster_entry["equips"][0]["stats"][1]["level"] == 3
    assert {"skillKey": "chr_0016_laevat_attack1", "level": 12} in roster_entry["skills"]
    # 展示层字段（头像/职业/icon）不进导出契约
    assert "characterAvatarUrl" not in roster_entry
    assert "iconUrl" not in roster_entry["weapon"]

    assert len(payload["casts"]) == 2
    assert payload["casts"][0] == {
        "tsMsFromStart": 4030,
        "endMsFromStart": 4269,
        "characterKey": "chr_0016_laevat",
        "skillKey": "chr_0016_laevat_attack1",
        "skillName": "燃烬",
        "skillSource": "unknown",
        "recoversEnergy": False,
    }

    cached = client.get(
        f"/api/v1/battles/{battle_id}/export",
        headers={"If-None-Match": etag},
    )
    assert cached.status_code == 304


def test_export_rejects_legacy_battle_without_casts() -> None:
    client = TestClient(app)
    battle_id = _upload_battle(client, with_casts=False)

    response = client.get(f"/api/v1/battles/{battle_id}/export")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "battle_export_unsupported"


def test_export_unknown_battle_returns_404() -> None:
    client = TestClient(app)
    response = client.get("/api/v1/battles/btl_upload_nonexistent00/export")
    assert response.status_code == 404


def test_saved_v58_battle_remains_readable_and_exports_its_original_version() -> None:
    client = TestClient(app)
    battle_id = _upload_battle(client, with_casts=True)
    # Seed a historical stored record. The current upload endpoint must not be
    # used to manufacture acceptance of an outdated parser version.
    with SessionLocal() as session:
        record = session.get(UploadedBattleRecord, battle_id)
        assert record is not None
        record.parser_version = "raw-log-parser-v58"
        session.commit()
    response = client.get(f"/api/v1/battles/{battle_id}/export")
    assert response.status_code == 200
    assert response.json()["parserVersion"] == "raw-log-parser-v58"
    detail = client.get(f"/api/battles/{battle_id}")
    assert detail.status_code == 200
    assert detail.json()["battle"]["parserVersion"] == "raw-log-parser-v58"


def test_export_allows_non_best_public_battle() -> None:
    """排轴导出不要求上榜：同账号同 Boss 的非最佳战绩也可导出。"""
    client = TestClient(app)
    best_id = _upload_battle(client, with_casts=True)
    weaker_id = _upload_battle(client, with_casts=True)

    # 两把都能导出（第二把大概率不是排行榜上的账号最佳）
    for battle_id in (best_id, weaker_id):
        response = client.get(f"/api/v1/battles/{battle_id}/export")
        assert response.status_code == 200, battle_id
        assert response.json()["battleId"] == battle_id
