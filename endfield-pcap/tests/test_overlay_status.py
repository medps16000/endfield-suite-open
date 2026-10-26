from __future__ import annotations

from copy import deepcopy
import json
import os
from types import SimpleNamespace

import pytest

os.environ["ENDFIELD_OVERLAY_ALLOW_MULTIPLE"] = "1"

import overlay


def test_overlay_uses_official_character_catalog_for_new_characters() -> None:
    assert overlay.friendly_name("chr_0035_liino") == "梨诺"
    assert overlay.friendly_name("liino") == "梨诺"
    assert overlay._character_element("liino") == "pulse"


def test_live_overlay_status_displays_official_dungeon_instead_of_boss() -> None:
    class TailerStub:
        @staticmethod
        def _live_core_rows():
            return (
                {},
                {},
                {
                    "battle": {
                        "dungeon_name": "斧柄纪年·残酷",
                        "dungeon_key": "indie_battletower004_ex",
                        "dungeon_context_id": "indie_battletower004_ex",
                        "boss_name": "精锐行刑人",
                        "boss_key": "eny_0000_example",
                        "duration_ms": 12_345,
                        "rdps_available": True,
                    }
                },
            )

    status = overlay.LogTailer.get_status_snapshot(TailerStub())

    assert status == {"dungeonName": "斧柄纪年[残酷]", "elapsed": 12.345}
    assert "精锐行刑人" not in status.values()


def test_overlay_difficulty_label_uses_official_dungeon_id() -> None:
    cases = [
        ("dung01_takestwo007", "什么！还有动物！", "什么！还有动物！[普通]"),
        (
            "dung01_takestwo007_hard",
            "什么！还有动物！全功率！",
            "什么！还有动物！[全功率]",
        ),
        ("indie_hard008_s", "怨憎雾海·苦难", "怨憎雾海[苦难]"),
        ("dung01_bossrush01_04", "危境再现·罗丹", "危境再现·罗丹[四级]"),
        ("dung_ss04", "高危材料研究所", "高危材料研究所"),
    ]

    for dungeon_id, dungeon_name, expected in cases:
        assert overlay.format_overlay_dungeon_name(
            {
                "dungeon_context_id": dungeon_id,
                "dungeon_name": dungeon_name,
            }
        ) == expected


def test_live_overlay_exposes_official_stage_before_first_hit() -> None:
    parser = overlay.LiveOverlayBattleParser()
    parser.feed_line(
        "[10:00:00.000] DUNGEON_CONTEXT "
        "dungeonId=indie_battletower004_ex source=SC_SELF_SCENE_INFO"
    )

    snapshot = parser.snapshot()

    assert snapshot is not None
    assert snapshot["battle"]["dungeon_name"] == "斧柄纪年·残酷"
    assert snapshot["participants"] == []


def test_stale_fatal_service_status_remains_visible(monkeypatch, tmp_path) -> None:
    status_file = tmp_path / "trace.log.status.json"
    payload = {
        "state": "live",
        "fatal_error": {
            "task": "packet-loop",
            "type": "AttributeError",
            "message": "FieldDescriptor.label is unavailable",
        },
    }
    status_file.write_text(json.dumps(payload), encoding="utf-8")
    os.utime(status_file, (100.0, 100.0))
    monkeypatch.setattr(overlay, "STATUS_FILE", str(status_file))
    monkeypatch.setattr(overlay.time, "time", lambda: 200.0)

    status = overlay.read_service_status()

    assert status == payload
    assert overlay.service_status_text(status) == "状态：采集异常（AttributeError）"
    assert overlay.service_metrics_text(status) == "错误：FieldDescriptor.label is unavailable"


def test_stale_healthy_service_status_is_unavailable(monkeypatch, tmp_path) -> None:
    status_file = tmp_path / "trace.log.status.json"
    status_file.write_text(json.dumps({"state": "live"}), encoding="utf-8")
    os.utime(status_file, (100.0, 100.0))
    monkeypatch.setattr(overlay, "STATUS_FILE", str(status_file))
    monkeypatch.setattr(overlay.time, "time", lambda: 200.0)

    assert overlay.read_service_status() is None


def _meter_update_stub(monkeypatch, failures, *, hide_raises=False):
    class RootStub:
        def __init__(self):
            self.scheduled = []

        def after(self, delay, callback):
            self.scheduled.append((delay, callback))

    class LabelStub:
        def __init__(self):
            self.texts = []

        def config(self, *, text):
            self.texts.append(text)

    class TailerStub:
        def __init__(self):
            self.duration_ms = 10_000
            self.clock_enabled_calls = []
            self.reset_calls = self.stop_calls = 0
            self._live_core = SimpleNamespace(reset=self.reset)

        def set_live_clock_enabled(self, enabled):
            self.clock_enabled_calls.append(enabled)

        def _live_core_rows(self):
            return {}, {}, {"battle": {"duration_ms": self.duration_ms,
                "dungeon_name": "受缚之影·苦难", "dungeon_key": "indie_hard030_s", "rdps_available": True}}

        def reset(self):
            self.reset_calls += 1

        def stop(self):
            self.stop_calls += 1

    class GuideStub:
        def __init__(self):
            self.failures = iter(failures)
            self.hide_calls = 0
            self.update_calls = []
            self.progress = {"elapsedMs": 6250, "completedIds": ["confirmed-step"]}
            self._shown = True
            self.window = SimpleNamespace(hide=self.hide)

        def hide(self):
            self.hide_calls += 1
            if hide_raises:
                raise RuntimeError("underlying Qt window already deleted")

        def update(self, **kwargs):
            self.update_calls.append(kwargs)
            failure = next(self.failures)
            if failure is not None:
                raise failure

    meter = overlay.OverlayApp.__new__(overlay.OverlayApp)
    meter.root, meter.tailer, meter.rotation_module = RootStub(), TailerStub(), GuideStub()
    meter.stage_label, meter.time_label = LabelStub(), LabelStub()
    meter.mode, meter.row_widgets = "DPS", []
    meter.win_w, meter.win_h = 360, 154
    meter._fatal_shutdown_scheduled = False
    meter._ensure_rows = lambda count: None
    monkeypatch.setattr(overlay, "STATUS_FILE", "owned-status-stub")
    monkeypatch.setattr(overlay, "read_service_status", lambda: {"state": "live", "game_exe": "Endfield.exe"})
    monkeypatch.setattr(overlay, "_write_rows_debug", lambda *args: None)
    return meter


@pytest.mark.parametrize("hide_raises", [False, True])
def test_guide_update_error_does_not_stop_real_meter_refresh_or_capture(monkeypatch, hide_raises):
    meter = _meter_update_stub(monkeypatch, [RuntimeError("guide image failure"), RuntimeError("guide image failure")],
        hide_raises=hide_raises)
    before_progress = deepcopy(meter.rotation_module.progress)
    for duration_ms in (10_000, 11_000):
        meter.tailer.duration_ms = duration_ms
        meter._update()

    assert meter.time_label.texts[0].endswith("10.000")
    assert meter.time_label.texts[1].endswith("11.000")
    assert [delay for delay, _ in meter.root.scheduled] == [overlay.POLL_MS, overlay.POLL_MS]
    assert all(callback.__self__ is meter for _, callback in meter.root.scheduled)
    assert meter.tailer.clock_enabled_calls == [True, True]
    assert meter.tailer.reset_calls == meter.tailer.stop_calls == 0
    assert meter.rotation_module.progress == before_progress
    assert meter.rotation_module.hide_calls == 2
    assert all(call["available"] is True for call in meter.rotation_module.update_calls)


def test_guide_error_diagnostics_distinguish_type_and_message_and_recovery_keeps_progress(monkeypatch, capsys):
    meter = _meter_update_stub(monkeypatch, [RuntimeError("image A"), RuntimeError("image A"),
        RuntimeError("image B"), ValueError("image B"), None])
    progress = deepcopy(meter.rotation_module.progress)
    meter._update()
    first = capsys.readouterr().err
    assert "RuntimeError" in first and "image A" in first
    meter.tailer.duration_ms = 11_000
    meter._update()
    assert capsys.readouterr().err == ""
    meter._update()
    changed_message = capsys.readouterr().err
    assert "RuntimeError" in changed_message and "image B" in changed_message
    meter._update()
    changed_type = capsys.readouterr().err
    assert "ValueError" in changed_type and "image B" in changed_type
    meter.tailer.duration_ms = 12_000
    meter._update()
    assert capsys.readouterr().err == ""
    assert meter.time_label.texts[-1].endswith("12.000")
    assert meter.rotation_module.progress == progress
    assert len(meter.root.scheduled) == 5
    assert meter.tailer.clock_enabled_calls == [True] * 5
    assert meter.tailer.reset_calls == meter.tailer.stop_calls == 0
