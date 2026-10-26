"""Source API/cache/display roundtrip, isolated before importing the API app.

The clock portion uses the sealed native policy and explicit synthetic protocol
events. It is not gameplay, capture, GUI, or frozen-package acceptance.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4


PCAP_ROOT = Path(__file__).resolve().parents[1]
LOGS_ROOT = PCAP_ROOT.parent / "endfield-logs"


def test_real_api_sync_cache_offline_restart_and_account_isolation(tmp_path):
    # A fresh interpreter prevents an already-imported app/session singleton in
    # another pytest file from accidentally selecting a development/preview DB.
    work = (tmp_path / f"roundtrip_{uuid4().hex}").resolve()
    work.mkdir()
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    api_python = LOGS_ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    assert api_python.is_file(), "This source integration test requires the API project's existing environment."
    with (work / "worker.stdout").open("xb") as out, (work / "worker.stderr").open("xb") as err:
        result = subprocess.run([str(api_python), str(Path(__file__).resolve()), "--worker", str(work)],
            cwd=work, env=environment, stdin=subprocess.DEVNULL, stdout=out, stderr=err, timeout=120,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert result.returncode == 0, (work / "worker.stderr").read_text("utf-8", errors="replace")
    report = json.loads((work / "result.json").read_text("utf-8"))
    assert report["status"] == "passed"
    assert report["assetCount"] == 132
    assert report["postUsedRealApi"] and report["apiClientUsedRealAsgiTransport"]
    assert report["offlineRestartUsesSameAccountCache"] and report["accountSwitchDoesNotMixPlans"]
    assert report["syntheticClockProtocol"]["completedCount"] == 3
    assert report["syntheticClockProtocol"]["allNonDisplayFieldsEqualRawEngine"]
    assert report["databasePath"].startswith(str(work))


def _worker(work: Path) -> None:
    # No API, SQLAlchemy session, real user SessionStore or settings object may
    # be imported before selecting this worker's private filesystem namespace.
    assert not any(name == "app" or name.startswith("app.") for name in sys.modules)
    work = work.resolve()
    profile = work / "profile"
    profile.mkdir()
    database = work / "api.sqlite"
    assert not database.exists()
    os.environ.update(DATABASE_URL="sqlite+pysqlite:///" + database.as_posix(), APP_ENV="test",
        ALLOWED_HOSTS="testserver,localhost,127.0.0.1", INTEGRITY_SECRET="roundtrip-test-only",
        CORS_ORIGINS="http://testserver", CSRF_TRUSTED_ORIGINS="http://testserver",
        LOCALAPPDATA=str(profile), APPDATA=str(profile), PYTHONDONTWRITEBYTECODE="1", ROTATIONS_ENABLED="true")
    for key in list(os.environ):
        if key.lower() in {"http_proxy", "https_proxy", "all_proxy", "pythonhome"}:
            os.environ.pop(key, None)
    os.environ["NO_PROXY"] = "testserver"
    sys.dont_write_bytecode = True
    sys.path[:0] = [str(LOGS_ROOT / "apps/api"), str(LOGS_ROOT / "packages/parser_core"),
                   str(LOGS_ROOT / "packages/upload_domain"), str(LOGS_ROOT / "packages/uploader_core"),
                   str(PCAP_ROOT / "src")]

    import copy
    from datetime import UTC, datetime, timedelta
    import hashlib
    import shutil
    from unittest.mock import patch

    import httpx
    from fastapi.testclient import TestClient
    from app.db.models import AuthSessionRecord, AuthUserRecord
    from app.db.session import SessionLocal, engine
    from app.main import create_app
    from app.services.auth import DatabaseAuthService
    from endfield_pcap.rotation_client import RotationCache, RotationGuideController
    from endfield_pcap import rotation_client
    # Historical enabled-mode regression only; normal product builds stay off.
    rotation_client.ROTATION_GUIDANCE_ENABLED = True
    from endfield_pcap.rotation_clock_proof import NATIVE_CLOCK_PROOF
    from uploader_core.api_client import ApiClient
    from uploader_core.session_store import SessionStore
    from uploader_core.settings_store import SettingsStore, UploaderSettings

    assert Path(engine.url.database).resolve() == database
    os.environ["ENDFIELD_ROTATION_GAME_ASSEMBLY_SHA256"] = NATIVE_CLOCK_PROOF["gameAssemblySha256"]
    os.environ["ENDFIELD_ROTATION_METADATA_SHA256"] = NATIVE_CLOCK_PROOF["metadataSha256"]
    assets = work / "assets"
    assets.mkdir()
    shutil.copy2(PCAP_ROOT / "data/public/rotation-assets.json", assets / "rotation-assets.json")
    shutil.copytree(PCAP_ROOT / "data/public/rotation-assets", assets / "rotation-assets")
    manifest = json.loads((assets / "rotation-assets.json").read_text("utf-8"))
    assert manifest["schemaVersion"] == 1 and len(manifest["images"]) == 132
    for row in manifest["images"].values():
        path = (assets / "rotation-assets" / row["path"]).resolve()
        assert path.is_relative_to(assets.resolve()) and path.suffix == ".png"
        raw = path.read_bytes()
        assert raw.startswith(b"\x89PNG\r\n\x1a\n")
        assert hashlib.sha256(raw).hexdigest() == row["sha256"]

    sessions = SessionStore()
    settings = SettingsStore()
    base_url = "http://testserver"
    settings.save_settings(UploaderSettings(api_base_url=base_url, web_base_url=base_url,
                                           last_log_dir=str(work)))
    assert sessions._session_path.is_relative_to(work)
    assert settings._settings_path.is_relative_to(work)
    cache = RotationCache(work / "cache/cache.json")
    trace = work / "synthetic-protocol.log"
    trace.write_text("", encoding="utf-8")
    state = {"online": True, "requests": [], "offlineAttempts": 0}
    evidence = {"schemaVersion": 1, "status": "started", "workRoot": str(work),
        "sourcePythonExecutable": sys.executable, "sourcePythonVersion": sys.version,
        "databasePath": str(database), "assetCount": len(manifest["images"]),
        "scope": "Source real FastAPI/ApiClient/cache/display roundtrip plus synthetic protocol unit using the sealed native clock spec; no gameplay capture, GUI, service or frozen-package claim.",
        "networkBoundary": "All HTTP uses the in-process TestClient ASGI transport; no preview port, external host, socket client or real user session.",
        "postUsedRealApi": False, "apiClientUsedRealAsgiTransport": True}

    with TestClient(create_app()) as api:
        class BorrowedAsgiTransport(httpx.BaseTransport):
            def handle_request(self, request):
                assert request.url.scheme == "http" and request.url.host == "testserver"
                if not state["online"]:
                    state["offlineAttempts"] += 1
                    raise httpx.ConnectError("isolated offline transport", request=request)
                state["requests"].append((request.method, request.url.path))
                # This is the actual FastAPI ASGI application, not canned JSON.
                return api._transport.handle_request(request)

            def close(self):
                # ApiClient owns its wrapper; TestClient owns the ASGI lifespan.
                pass

        class AsgiApiClient(ApiClient):
            @staticmethod
            def _new_client(url):
                assert url == base_url
                client = ApiClient._new_client(url)
                client._transport.close()
                for mounted in client._mounts.values():
                    if mounted is not None:
                        mounted.close()
                client._mounts.clear()
                client._transport = BorrowedAsgiTransport()
                return client

        now = datetime.now(UTC)
        accounts = []
        with SessionLocal() as db:
            for _ in range(2):
                identity, token = uuid4().hex, "roundtrip-" + uuid4().hex
                email = identity + "@example.com"
                db.add(AuthUserRecord(id=identity, email=email, nickname=identity,
                    created_at=now, is_disabled=False, is_admin=False))
                db.add(AuthSessionRecord(token=DatabaseAuthService._hash_secret(token), user_id=identity,
                    email=email, nickname=identity, client_type="web", created_at=now,
                    expires_at=now + timedelta(days=1)))
                accounts.append({"userId": identity, "sessionToken": token})
            db.commit()
        account_a, account_b = accounts
        auth_a = {"Authorization": "Bearer " + account_a["sessionToken"]}
        auth_b = {"Authorization": "Bearer " + account_b["sessionToken"]}
        response = api.get("/api/rotations/catalog")
        assert response.status_code == 200
        catalog = response.json()
        game = "indie_hard026_s"
        assert game in {item["id"] for item in catalog["maps"]}
        members = ["chr_0030_zhuangfy", "chr_0035_liino", "chr_0013_aglina", "chr_0019_karin"]
        characters = {item["id"]: item for item in catalog["characters"]}
        assert set(members) <= characters.keys()
        owner = members[0]
        actions = {item["kind"]: item for item in characters[owner]["actions"]}
        assert actions["ultimate_skill"]["rootSkillIds"] == ["chr_0030_zhuangfy_ultimate_skill"]
        assert set(actions["battle_skill"]["rootSkillIds"]) == {
            "chr_0030_zhuangfy_normal_skill", "chr_0030_zhuangfy_normal_skill_ult"}
        body = {"name": "独立测试·R-Q-Q", "dungeonId": game, "characterIds": members,
            "enabled": True, "displayNotes": "测试数据，不参与真实账号", "steps": [
                {"id": "r", "atMs": 1000, "characterId": owner, "actionKind": "ultimate_skill", "phase": "开场", "note": "终结技"},
                {"id": "q1", "atMs": 2000, "characterId": owner, "actionKind": "battle_skill", "phase": "强化段", "note": "第一次战技"},
                {"id": "q2", "atMs": 3000, "characterId": owner, "actionKind": "battle_skill", "phase": "强化段", "note": "第二次战技"},
            ]}
        posted = api.post("/api/rotations", headers=auth_a, json=body)
        assert posted.status_code == 201, posted.text
        saved = posted.json()
        assert saved["steps"] == body["steps"] and all("skillId" not in s for s in saved["steps"])
        evidence["postUsedRealApi"] = True
        sessions.save_session(account_a)

        def make_controller():
            return RotationGuideController(trace, cache=cache, session_store=sessions, settings_store=settings,
                                           client_factory=AsgiApiClient, asset_root=assets)

        online = make_controller()
        online._sync_once(online._reconcile_session(force=True))
        cached = cache.load(account_a["userId"], base_url)
        assert cached["rotations"] == [saved]
        assert online._catalog == catalog
        assert set(state["requests"]) == {("GET", "/api/rotations"), ("GET", "/api/rotations/catalog")}
        assert account_a["sessionToken"] not in cache.path.read_text("utf-8")
        assert online._clock_spec is not None
        assert online._clock_spec.game_assembly_sha256 == NATIVE_CLOCK_PROOF["gameAssemblySha256"]
        online.stop()

        state["online"] = False
        offline = make_controller()
        context = offline._reconcile_session(force=True)
        assert offline._envelope == cached and offline._catalog == catalog
        successful = len(state["requests"])
        offline._sync_once(context)
        assert len(state["requests"]) == successful and state["offlineAttempts"] == ApiClient._REQUEST_RETRY_COUNT
        assert offline.sync_state()["offline"] is True
        assert offline._envelope == cached
        for url in manifest["images"]:
            resolved = Path(offline._asset_path(url))
            assert resolved.is_file() and resolved.is_relative_to(assets)
        evidence["offlineRestartUsesSameAccountCache"] = True

        start = 1791811352908
        actors = ["102140", "102171", "102201", "102231"]
        display_fields = {"characterName", "skillName", "actionName", "iconUrl", "avatarUrl", "iconPath", "avatarPath"}

        def strip_display(value):
            if isinstance(value, dict):
                return {k: strip_display(v) for k, v in value.items() if k not in display_fields}
            if isinstance(value, list):
                return [strip_display(v) for v in value]
            return value

        def feed(*lines):
            with trace.open("a", encoding="utf-8") as stream:
                for line in lines:
                    text = "[21:22:34.800] " + line
                    stream.write(text + "\n")
                    offline._engine.feed_line(text)

        def check_view(expected_completed):
            raw = offline._engine.snapshot()
            shown = offline.snapshot()
            assert shown is not None
            assert strip_display(raw) == strip_display(shown)
            assert shown["completedCount"] == expected_completed
            assert shown["clockSource"] == "official_algorithm_observer"
            assert [{k: s[k] for k in body["steps"][i]} for i, s in enumerate(shown["steps"])] == body["steps"]
            assert all(Path(s["iconPath"]).is_file() and Path(s["avatarPath"]).is_file() for s in shown["steps"])
            return shown

        def cast(sequence, instance, root, capture):
            return (f"SKILL_CAST_START seq={sequence} startMs={30000 + sequence} inst={instance} owner={owner} "
                    f"ownerId={actors[0]} skill={root} skillSource=Player captureMs={capture}")

        offline.set_available(True)
        with patch("parser_core.rotation_clock.utc_now_ms", return_value=start + 100) as utc:
            feed(f"DUNGEON_CONTEXT dungeonId={game} sceneId=roundtrip-scene",
                 "SQUAD size=4 members=[" + " ".join(f"{c}_{a}" for c, a in zip(members, actors)) + "]",
                 f"GAME_INST_ENTER gameId={game} gameInstId=roundtrip-instance gameUniqueId=123456789",
                 f"GAME_CLOCK_SYNC source=CS_PING clientTs={start - 1040} logicalTs=30091 captureMs={start - 1039}",
                 f"GAME_CLOCK_SYNC source=SC_PING clientTs={start - 1040} serverTs={start - 1070} captureMs={start - 1000}",
                 f"OFFICIAL_TIMER_START source=SC_GAME_MECHANICS_SYNC_CHALLENGE_START gameId={game} challengeStartTs={start}")
            check_view(0)
            feed(cast(1, 900, "chr_0030_zhuangfy_normal_skill", start + 500))
            check_view(0)  # A Q packet cannot complete the authored R.
            utc.return_value = start + 1200
            feed(cast(2, 901, "chr_0030_zhuangfy_ultimate_skill", start + 1100))
            check_view(1)
            utc.return_value = start + 2200
            q1 = cast(3, 902, "chr_0030_zhuangfy_normal_skill_ult", start + 2100)
            feed(q1, q1)
            check_view(2)  # Duplicate Q must not consume the second Q.
            feed("GAME_TIME_FREEZE_STATE source=SC_GAME_TIME_FREEZE_START_RSP inTimeFreeze=1 reason=1")
            utc.return_value = start + 5000
            feed(cast(4, 903, "chr_0030_zhuangfy_normal_skill", start + 5000))
            paused = check_view(2)
            assert paused["paused"] is True and paused["elapsedMs"] == 2200
            feed(f"GAME_MECHANICS_TIME_FREEZE source=SC_GAME_MECHANICS_MODIFY_INST_TIME_FREEZE gameId={game} count=1 freezeInfos=[0:2000]",
                 "GAME_TIME_FREEZE_STATE source=SC_GAME_TIME_FREEZE_END_RSP inTimeFreeze=0 reason=1")
            utc.return_value = start + 7200
            feed(cast(5, 904, "chr_0030_zhuangfy_normal_skill", start + 7100))
            completed = check_view(3)
            assert completed["elapsedMs"] == 5200 and completed["allCompleted"] is True
            assert completed["steps"][2]["completedAtMs"] == 5100
        evidence["syntheticClockProtocol"] = {"completedCount": 3, "wrongActionRefused": True,
            "duplicateCastDeduplicated": True, "pauseDidNotAdvance": True,
            "resumeElapsedMs": 5200, "allNonDisplayFieldsEqualRawEngine": True,
            "nativeGameAssemblySha256": NATIVE_CLOCK_PROOF["gameAssemblySha256"],
            "boundary": "Explicit synthetic protocol inputs, not a real capture replay."}

        sessions.save_session(account_b)
        offline._reconcile_session(force=True)
        assert offline._envelope is None and offline.snapshot() is None
        assert cache.load(account_b["userId"], base_url) is None
        state["online"] = True
        offline._sync_once(offline._reconcile_session(force=True))
        own_b = cache.load(account_b["userId"], base_url)
        assert own_b["accountId"] == account_b["userId"] and own_b["rotations"] == []
        assert cache.load(account_a["userId"], base_url) is None
        assert api.get("/api/rotations", headers=auth_a).json()["rotations"] == [saved]
        assert api.get("/api/rotations", headers=auth_b).json()["rotations"] == []
        evidence["accountSwitchDoesNotMixPlans"] = True
        evidence["realAsgiRequestPaths"] = [list(item) for item in state["requests"]]
        evidence["cacheAndCatalogPaths"] = [str(cache.path), str(offline._catalog_path)]
        evidence["status"] = "passed"
        offline.stop()
    engine.dispose()
    (work / "result.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    assert len(sys.argv) == 3 and sys.argv[1] == "--worker"
    _worker(Path(sys.argv[2]))
