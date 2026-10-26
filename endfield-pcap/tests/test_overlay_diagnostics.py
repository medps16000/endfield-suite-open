from __future__ import annotations

import json
from pathlib import Path
import pytest

from endfield_pcap import overlay_diagnostics as diag


def test_diagnostics_redacts_paths_credentials_and_limits_messages(tmp_path):
    store = diag.OverlayDiagnostics(tmp_path)
    store.record("12345678-1234-1234-1234-123456789012", "javascript",
                 "ReferenceError missing is not defined; https://user:pw@example.com/?token=abc "
                 r"C:\Users\private\secret.html token=SECRET456 Bearer abc123 " + "A" * 300, line=12)
    content = store.text("12345678-1234-1234-1234-123456789012")
    assert "ReferenceError missing is not defined" in content
    for private in ("example.com", "private", "secret.html", "SECRET456", "abc123", "A" * 24):
        assert private not in content
    row = json.loads(store.path.read_text(encoding="utf-8"))
    assert row["windowId"] == "12345678-1234-1234-1234-123456789012"
    assert row["line"] == 12 and len(row["message"]) <= 320


def test_javascript_flood_does_not_hide_lifecycle_failures(tmp_path):
    store = diag.OverlayDiagnostics(tmp_path)
    for i in range(1000):
        store.record("first", "javascript", "TypeError page failed " + str(i))
    store.record("first", "renderer_terminated", "CrashedTerminationStatus")
    assert "970 条高频诊断" in store.text("first")
    assert "renderer_terminated" in store.text("first")
    assert len(store._rows["first"]) == 31
    store.record("second", "load_failed", "another window")
    assert "another window" not in store.text("first")
    store.forget("first")
    assert store.text("first") == "本次运行尚无窗口诊断记录。"


@pytest.mark.parametrize("payload,private", [
    ('{"password":"shortpw"}', "shortpw"),
    ("{'password': 'short pass phrase'}", "short pass phrase"),
    ("{ token: 'tiny token', authorization: 'Bearer abc' }", "tiny token"),
    ('{"api_key": "key one two", "session-id":"sid"}', "key one two"),
    ('{"password":"escape\\\"short pass"}', "short pass"),
    ('{"password":"unterminated short secret', "short secret"),
])
def test_quoted_credential_keys_and_values_are_redacted(payload, private):
    text = diag.safe_text("ReferenceError missingName is not defined " + payload)
    assert "ReferenceError missingName is not defined" in text
    assert private not in text
    assert "[凭据已隐藏]" in text


def test_log_storage_and_window_history_are_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(diag, "MAX_LOG_BYTES", 1200)
    monkeypatch.setattr(diag, "MAX_WINDOWS", 2)
    monkeypatch.setattr(diag, "MAX_ROWS", 5)
    store = diag.OverlayDiagnostics(tmp_path)
    for i in range(300):
        store.record("first", "renderer_retry", "repeat" * 60)
    assert len(store._rows["first"]) == 5
    assert store.path.stat().st_size <= 1200
    assert store.path.with_suffix(".previous.jsonl").stat().st_size <= 1200
    assert len(list(tmp_path.iterdir())) == 2
    for identifier in ("second", "third"):
        store.record(identifier, "load_failed", "page failed")
    assert len(store._rows) == 2 and "first" not in store._rows


def test_diagnostic_write_failure_keeps_memory_and_never_raises(tmp_path):
    invalid = tmp_path / "not-a-directory"
    invalid.write_text("file")
    store = diag.OverlayDiagnostics(invalid)
    store.record("test", "load_failed", "could not load")
    assert "could not load" in store.text("test")
