import hashlib
import json
from unittest.mock import Mock

import pytest

from app import main as uploader_main


def test_verify_log_hashes_retained_payloads_without_gui_or_account_access(monkeypatch, tmp_path):
    source = tmp_path / "trace.log"
    source.write_bytes(b"archived trace\n")
    output = tmp_path / "verification.json"
    payloads = [
        {"battle": {"totalDamage": 100, "clearFlag": True, "timerEndSeen": True,
                    "officialTimerEndSeen": True, "parserVersion": "v51", "rulesVersion": "v44"},
         "timelineEvents": [{"laneType": "skill", "value": 100, "eventName": "灼热"}]},
        {"battle": {"totalDamage": 20, "clearFlag": False, "timerEndSeen": False,
                    "officialTimerEndSeen": False, "parserVersion": "v51", "rulesVersion": "v44"},
         "timelineEvents": [{"laneType": "buff", "value": 20}]},
    ]
    builder = Mock(return_value=payloads)
    monkeypatch.setattr(uploader_main, "build_battle_upload_payloads_from_log", builder)
    for name in ("QApplication", "MainWindow", "_configure_logging"):
        monkeypatch.setattr(uploader_main, name, Mock(side_effect=AssertionError(f"unexpected {name}")))
    monkeypatch.setattr("app.auth.session_store.SessionStore.load_session", Mock(side_effect=AssertionError("account read")))
    monkeypatch.setattr(uploader_main.sys, "argv", ["uploader", "--verify-log", str(source), "--verification-output", str(output)])

    assert uploader_main.main() == 0
    builder.assert_called_once_with(str(source.resolve()), fast_unverified=True, include_source_metadata=True)
    summary = json.loads(output.read_text(encoding="utf8"))
    canonical = ('[{"battle":{"clearFlag":true,"officialTimerEndSeen":true,"parserVersion":"v51",'
                 '"rulesVersion":"v44","timerEndSeen":true,"totalDamage":100},'
                 '"timelineEvents":[{"eventName":"灼热","laneType":"skill","value":100}]},'
                 '{"battle":{"clearFlag":false,"officialTimerEndSeen":false,"parserVersion":"v51",'
                 '"rulesVersion":"v44","timerEndSeen":false,"totalDamage":20},'
                 '"timelineEvents":[{"laneType":"buff","value":20}]}]').encode("utf8")
    assert json.loads(canonical) == payloads
    assert summary["payloadsSha256"] == hashlib.sha256(canonical).hexdigest()
    assert summary["sourceSha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert summary["battleCount"] == 2
    assert summary["totalDamage"] == 120
    assert summary["clearedBattleCount"] == summary["timerEndBattleCount"] == summary["officialTimerEndBattleCount"] == 1
    assert summary["timelineEventCount"] == 2
    assert summary["damageEventCount"] == 1
    assert summary["rawLogIntegrityVerified"] is False
    assert summary["uploaded"] is False


@pytest.mark.parametrize("mode", ["missing_output", "same_file", "existing_output", "output_without_input", "upload_flag"])
def test_verify_log_rejects_unsafe_or_incomplete_arguments(monkeypatch, tmp_path, mode):
    source = tmp_path / "trace.log"
    source.write_text("preserved trace", encoding="utf8")
    output = tmp_path / "verification.json"
    argv = ["uploader", "--verify-log", str(source)]
    if mode == "same_file":
        argv += ["--verification-output", str(source)]
    elif mode == "existing_output":
        output.write_text("preserved output", encoding="utf8")
        argv += ["--verification-output", str(output)]
    elif mode == "output_without_input":
        argv = ["uploader", "--verification-output", str(output)]
    elif mode == "upload_flag":
        argv += ["--verification-output", str(output), "--auto-upload-all"]
    monkeypatch.setattr(uploader_main.sys, "argv", argv)
    for name in ("build_battle_upload_payloads_from_log", "QApplication", "MainWindow", "_configure_logging"):
        monkeypatch.setattr(uploader_main, name, Mock(side_effect=AssertionError(f"unexpected {name}")))
    with pytest.raises(SystemExit) as exc:
        uploader_main.main()
    assert exc.value.code == 2
    assert source.read_text(encoding="utf8") == "preserved trace"
    if mode == "existing_output":
        assert output.read_text(encoding="utf8") == "preserved output"
    else:
        assert not output.exists()
