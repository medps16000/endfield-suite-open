from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
from types import SimpleNamespace

import pytest


# These tests render only their own widgets into images, never desktop windows.
os.environ["QT_QPA_PLATFORM"] = "offscreen"
pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QEvent, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QFontMetrics, QImage, QMouseEvent, QPainter
from PySide6.QtWidgets import QApplication

from endfield_pcap import rotation_overlay as overlay

@pytest.fixture(autouse=True)
def enable_retained_guide_rendering_for_unit_tests(monkeypatch):
    monkeypatch.setattr("endfield_pcap.rotation_client.ROTATION_GUIDANCE_ENABLED", True)


def step(index: int, *, completed: bool = False, at_ms: int | None = None) -> dict:
    return {
        "id": f"step-{index}", "atMs": index * 1000 if at_ms is None else at_ms,
        "characterId": f"chr_internal_{index}", "skillId": f"internal_root_{index}",
        "characterName": "庄方宜", "actionName": "战技", "skillName": "惊霆诀",
        "phase": "爆发轴", "note": "重击后释放", "completed": completed,
        "iconPath": "", "avatarPath": "",
    }


def report(steps: list[dict]) -> dict:
    return {
        "name": "庄方宜测试排轴", "elapsedMs": 1500, "paused": False,
        "steps": steps, "allCompleted": bool(steps) and all(s["completed"] for s in steps),
    }


@pytest.fixture(scope="module")
def application():
    app = QApplication.instance() or QApplication(["rotation-component-tests", "-platform", "offscreen"])
    assert app.platformName() == "offscreen"
    app.setQuitOnLastWindowClosed(False)
    for filename in ("msyh.ttc", "msyhbd.ttc"):
        assert QFontDatabase.addApplicationFont(str(Path("C:/Windows/Fonts") / filename)) >= 0
    assert QFontMetrics(QFont("Microsoft YaHei")).inFont("庄")
    yield app


@pytest.fixture
def strip(application):
    widget = overlay.GuideStrip()
    yield widget
    widget.close()
    widget.deleteLater()
    application.processEvents()


@pytest.fixture
def painted_text(monkeypatch):
    calls = []

    class RecordingPainter(QPainter):
        def drawText(self, *args):
            text = next((item for item in reversed(args) if isinstance(item, str)), "")
            rectangle = QRectF(args[0]) if args and isinstance(args[0], QRectF) else None
            ink = self.boundingRect(rectangle, int(args[1]), text) if rectangle is not None else None
            calls.append({"text": text, "fontSize": self.font().pixelSize(), "rect": rectangle, "inkBounds": ink})
            return super().drawText(*args)

        def drawPolyline(self, *args):
            points = list(args[0])
            half_stroke = self.pen().widthF() / 2
            rectangle = QRectF(min(p.x() for p in points), min(p.y() for p in points),
                max(p.x() for p in points) - min(p.x() for p in points),
                max(p.y() for p in points) - min(p.y() for p in points))
            calls.append({"text": "", "fontSize": self.font().pixelSize(), "rect": rectangle,
                "inkBounds": rectangle.adjusted(-half_stroke, -half_stroke, half_stroke, half_stroke), "shape": "chevron"})
            return super().drawPolyline(*args)

    monkeypatch.setattr(overlay, "QPainter", RecordingPainter)
    return calls


def render(widget, output: Path | None = None) -> QImage:
    image = QImage(widget.size(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    widget.render(image)
    if output is not None:
        assert image.save(str(output), "PNG")
    return image


def test_projection_is_current_pending_and_next_three_pending_without_mutation():
    value = report([step(i, completed=i in {0, 2, 4}) for i in range(9)])
    before = deepcopy(value)
    assert [item["id"] for item in overlay.guide_steps(value)] == ["step-1", "step-3", "step-5", "step-6"]
    assert value == before


def test_projection_preserves_same_second_order_and_does_not_invent_future_steps():
    value = report([step(0, completed=True), step(1, at_ms=3000), step(2, at_ms=3000)])
    assert [item["id"] for item in overlay.guide_steps(value)] == ["step-1", "step-2"]
    assert [item["atMs"] for item in overlay.guide_steps(value)] == [3000, 3000]


@pytest.mark.parametrize("value", [{}, report([]), report([step(0, completed=True)])])
def test_projection_has_no_completed_or_placeholder_cards(value):
    assert overlay.guide_steps(value) == []


@pytest.mark.parametrize("bounds,size,settings,expected", [
    ((0, 0, 1920, 1080), (600, 200), {}, (660, 140)),
    ((2560, 120, 1920, 1080), (600, 200), {}, (3220, 260)),
    ((-1920, 0, 1920, 1080), (600, 200), {}, (-1260, 140)),
    ((100, 200, 1920, 1080), (600, 200), {"guideAnchor": [0, 0]}, (100, 200)),
    ((100, 200, 1920, 1080), (600, 200), {"guideAnchor": [1, 1]}, (1420, 1080)),
    ((300, 200, 200, 100), (600, 200), {}, (300, 200)),
    ((100, 200, 1920, 1080), (600, 200),
        {"guidePositionMode": "absolute", "positions": {"guide": [500, 600]}}, (500, 600)),
    ((100, 200, 1920, 1080), (600, 200),
        {"guidePositionMode": "absolute", "positions": {"guide": [-50, -20]}}, (100, 200)),
    ((100, 200, 1920, 1080), (600, 200),
        {"guidePositionMode": "absolute", "positions": {"guide": [9000, 9000]}}, (1420, 1080)),
    ((0, 0, 1920, 1080), (600, 200),
        {"guidePositionMode": "game", "positions": {"guide": [9000, 9000]}}, (660, 140)),
])
def test_placement_uses_game_client_origin_and_keeps_legacy_absolute_positions(bounds, size, settings, expected):
    before = deepcopy(settings)
    assert overlay.place_guide(bounds, size, settings) == expected
    assert settings == before


def test_placement_keeps_horizontal_center_when_content_width_changes():
    bounds = (200, 100, 1920, 1080)
    narrow = overlay.place_guide(bounds, (250, 200), {})
    wide = overlay.place_guide(bounds, (650, 200), {})
    assert narrow[0] + 125 == wide[0] + 325 == 1160
    assert narrow[1] == wide[1]


def test_real_painter_draws_local_skill_and_avatar_pngs(strip, tmp_path):
    icon_color, avatar_color = QColor(245, 25, 235), QColor(20, 240, 45)
    paths = []
    for name, color in [("skill.png", icon_color), ("avatar.png", avatar_color)]:
        image = QImage(24, 24, QImage.Format.Format_RGBA8888)
        image.fill(color)
        path = tmp_path / name
        assert image.save(str(path), "PNG")
        paths.append(str(path))
    item = step(0)
    item.update(iconPath=paths[0], avatarPath=paths[1])
    strip.set_snapshot(report([item]))
    drawn = render(strip, tmp_path / "guide-local-images.png")
    colors = {drawn.pixelColor(x, y).getRgb()[:3] for y in range(drawn.height()) for x in range(drawn.width())}
    assert icon_color.getRgb()[:3] in colors
    assert avatar_color.getRgb()[:3] in colors
    assert len(strip.visible_steps) == 1
    assert not strip.isVisible()


@pytest.mark.parametrize("broken_file", [False, True])
def test_missing_or_unreadable_images_use_chinese_fallback_without_internal_ids(
    strip, tmp_path, painted_text, broken_file,
):
    image_path = tmp_path / "unreadable.png"
    if broken_file:
        image_path.write_bytes(b"not an image")
    item = step(0)
    item.update(iconPath=str(image_path), avatarPath=str(image_path))
    item.pop("characterName")
    item.pop("actionName")
    strip.set_snapshot(report([item]))
    render(strip)
    text = "\n".join(c["text"] for c in painted_text)
    assert "技" in text
    assert "角色资料待更新" in text
    assert item["characterId"] not in text
    assert item["skillId"] not in text


def test_long_chinese_note_grows_height_and_is_drawn_without_elision(strip, painted_text, tmp_path):
    value = report([step(0)])
    strip.set_snapshot(value)
    short_height = strip.sizeHint().height()
    note = "重击命中后再释放战技，切换角色并确认连携提示。" * 16
    value["steps"][0]["note"] = note
    strip.set_snapshot(value)
    assert strip.sizeHint().height() > short_height
    render(strip, tmp_path / "guide-long-note.png")
    rendered_note = [c for c in painted_text if c["text"] == note]
    assert rendered_note
    assert all(c["rect"].bottom() <= strip.height() for c in rendered_note)


def test_scaling_changes_actual_text_size_and_widget_geometry(strip, painted_text):
    value = report([step(0), step(1)])
    strip.set_snapshot(value, unit=1.0)
    initial = strip.sizeHint()
    render(strip)
    normal_font = max(c["fontSize"] for c in painted_text if c["text"] == "庄方宜")
    painted_text.clear()
    strip.set_snapshot(value, unit=1.5)
    render(strip)
    larger_font = max(c["fontSize"] for c in painted_text if c["text"] == "庄方宜")
    assert larger_font > normal_font
    assert abs(strip.sizeHint().width() - initial.width() * 1.5) <= 2
    assert strip.sizeHint().height() > initial.height()


def test_header_current_action_label_and_chevrons_do_not_overlap(strip, painted_text, tmp_path):
    value = report([step(i) for i in range(4)])
    strip.set_snapshot(value)
    render(strip, tmp_path / "guide-header-spacing.png")
    headers = [c for c in painted_text if value["name"] in c["text"] or c["text"] == "0:01.500"]
    actions = [c for c in painted_text if c["text"] == "战技"]
    assert headers and actions
    overlaps = [(header["text"], header["inkBounds"], action["inkBounds"])
        for header in headers for action in actions if header["inkBounds"].intersects(action["inkBounds"])]
    assert overlaps == []
    chevrons = [c for c in painted_text if c.get("shape") == "chevron"]
    assert chevrons
    assert all(not action["inkBounds"].intersects(c["inkBounds"]) for action in actions for c in chevrons)


def test_grey_band_is_translucent_and_fades_at_both_edges(strip):
    strip.set_snapshot(report([step(i) for i in range(4)]))
    image = render(strip)
    y = image.height() - 2
    middle = image.pixelColor(image.width() // 2, y)
    assert 0 < middle.alpha() < 255
    assert image.pixelColor(0, y).alpha() < middle.alpha() / 4
    assert image.pixelColor(image.width() - 1, y).alpha() < middle.alpha() / 4


def test_partial_tail_shrinks_and_all_complete_has_no_pending_card(strip, painted_text):
    value = report([step(i) for i in range(6)])
    strip.set_snapshot(value)
    full_width = strip.sizeHint().width()
    value = report([step(i, completed=i < 5) for i in range(6)])
    strip.set_snapshot(value)
    assert [s["id"] for s in strip.visible_steps] == ["step-5"]
    assert strip.sizeHint().width() < full_width
    value = report([step(i, completed=True) for i in range(6)])
    strip.set_snapshot(value)
    render(strip)
    assert strip.visible_steps == []
    assert any(c["text"] == "本轴已完成" for c in painted_text)


def test_paused_keeps_last_confirmed_steps_and_time_and_explains_frozen_progress(strip, painted_text):
    value = report([step(0, completed=True), step(1), step(2)])
    value.update(elapsedMs=6250, paused=True, pauseDisplaySource="last_confirmed_snapshot")
    before = deepcopy(value)
    strip.set_snapshot(value, offline=True)
    render(strip)
    assert value == before
    assert [s["id"] for s in strip.visible_steps] == ["step-1", "step-2"]
    texts = [c["text"] for c in painted_text]
    assert "0:06.250" in texts
    assert any("已暂停" in text and "保留最后确认进度" in text for text in texts)


def test_offline_status_is_visible_without_changing_projected_steps(strip, painted_text):
    value = report([step(0), step(1)])
    strip.set_snapshot(value, offline=True)
    render(strip)
    assert any(c["text"] == "离线缓存" for c in painted_text)
    assert strip.visible_steps == value["steps"]


@pytest.mark.parametrize("paused", [False, True])
def test_long_note_and_paused_or_offline_footer_keep_separate_rows(strip, painted_text, paused):
    value = report([step(0)])
    note = "切到前台再接战技，" * 40
    value["steps"][0]["note"] = note
    value.update(paused=paused, pauseDisplaySource="last_confirmed_snapshot" if paused else None)
    strip.set_snapshot(value, offline=True)
    render(strip)
    notes = [c for c in painted_text if c["text"] == note]
    footer = [c for c in painted_text if "保留最后确认进度" in c["text"] or c["text"] == "离线缓存"]
    assert notes and footer
    assert all(not n["inkBounds"].intersects(f["inkBounds"]) for n in notes for f in footer)
    assert all(f["rect"].bottom() <= strip.height() for f in footer)


def test_drag_is_disabled_until_settings_editing_and_callback_runs_on_release(application):
    finished = []
    widget = overlay.GuideStrip(on_drag_finished=lambda: finished.append(True))
    try:
        press = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(5, 5), QPointF(105, 105),
            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        release = QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(5, 5), QPointF(105, 105),
            Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
        widget.mousePressEvent(press)
        widget.mouseReleaseEvent(release)
        assert not widget.dragging and finished == []
        assert widget.windowFlags() & Qt.WindowType.WindowTransparentForInput
        widget.set_editing(True)
        assert widget.editing
        assert not widget.windowFlags() & Qt.WindowType.WindowTransparentForInput
        widget.mousePressEvent(press)
        assert widget.dragging
        widget.mouseReleaseEvent(release)
        assert not widget.dragging and finished == [True]
        widget.set_editing(False)
        assert widget.windowFlags() & Qt.WindowType.WindowTransparentForInput
        assert not widget.isVisible()
    finally:
        widget.close()
        widget.deleteLater()
        application.processEvents()


class ControllerStub:
    def __init__(self, value):
        self.value = deepcopy(value)
        self.available_calls = []

    def set_available(self, available):
        self.available_calls.append(available)
        if not available:
            self.value = None

    def snapshot(self):
        return deepcopy(self.value)

    def sync_state(self):
        return {"offline": False, "message": "测试状态"}


class WindowStub:
    def __init__(self):
        self.dragging = self.editing = self.shown = False
        self.show_calls = self.hide_calls = 0
        self.units = []
        self._width, self._height = 500, 200

    def set_editing(self, value):
        self.editing = value

    def set_snapshot(self, value, *, unit, offline):
        self.units.append(unit)
        self._width, self._height = round(500 * unit), round(200 * unit)

    def width(self):
        return self._width

    def height(self):
        return self._height

    def hide(self):
        self.shown = False
        self.hide_calls += 1

    def show(self):
        self.shown = True
        self.show_calls += 1

    def move(self, x, y):
        self.position = (x, y)

    def winId(self):
        return 777


def module_stub(value):
    module = overlay.RotationOverlayModule.__new__(overlay.RotationOverlayModule)
    module.controller = ControllerStub(value)
    module.window = WindowStub()
    module.settings = {"guideEnabled": True, "guideScale": 1, "guidePositionMode": "game", "guideAnchor": [.5, .13], "positions": {}}
    module.settings_window = module._sync_label = module._game_context = None
    module._shown = False
    return module


def test_foreground_loss_hides_only_the_strip_without_resetting_clock_or_steps(monkeypatch):
    value = report([step(0, completed=True), step(1), step(2)])
    module = module_stub(value)
    context = {"rect": (100, 200, 1920, 1080), "dpi": 96, "hwnd": 123}
    contexts = iter([context, None, context])
    monkeypatch.setattr(overlay, "game_window_context", lambda *args, **kwargs: next(contexts))
    monkeypatch.setattr(overlay, "_windows_api", lambda: None)
    module.update(available=True)
    assert module.window.shown
    module.update(available=True)
    assert not module.window.shown
    assert module.controller.value == value
    module.update(available=True)
    assert module.window.shown
    assert module.controller.available_calls == [True, True, True]
    assert module.controller.value == value


def test_native_placement_failure_hides_the_strip_and_restores_thread_dpi(monkeypatch):
    module = module_stub(report([step(0)]))
    module._shown = module.window.shown = True
    dpi_calls, placement_calls = [], []

    def dpi_context(value):
        dpi_calls.append(value)
        return 456

    def place(*args):
        placement_calls.append(args)
        return False

    api = SimpleNamespace(SetThreadDpiAwarenessContext=dpi_context, SetWindowPos=place)
    monkeypatch.setattr(overlay, "_windows_api", lambda: api)
    module.update(available=True, game_bounds=(100, 200, 1920, 1080))
    assert placement_calls
    assert dpi_calls[-1] == 456
    assert not module._shown and not module.window.shown
    assert module.window.show_calls == 0
    assert module.controller.available_calls == [True]


def test_settings_enable_drag_without_treating_focus_as_capture_unavailability(monkeypatch):
    module = module_stub(report([step(0)]))
    module.settings_window = SimpleNamespace(winfo_exists=lambda: True)
    contexts = []

    def observe(game_exe, previous, *, editing):
        contexts.append((game_exe, editing))
        return {"rect": (0, 0, 1920, 1080), "dpi": 144, "hwnd": 123}

    monkeypatch.setattr(overlay, "game_window_context", observe)
    monkeypatch.setattr(overlay, "_windows_api", lambda: None)
    module.update(available=True)
    assert contexts == [("Endfield.exe", True)]
    assert module.window.editing and module.window.shown
    assert module.window.units == [pytest.approx(2 / 3)]
    assert module.controller.available_calls == [True]


def test_real_qt_pump_recovers_after_event_exception_and_keeps_capture_progress():
    module = module_stub(report([step(0, completed=True), step(1)]))
    progress = deepcopy(module.controller.value)
    events = iter([RuntimeError("isolated Qt paint failure"), None])
    scheduled, calls = [], []

    def process_events():
        calls.append(True)
        error = next(events)
        if error is not None:
            raise error

    module._closed = False
    module._app = SimpleNamespace(processEvents=process_events)
    module.parent = SimpleNamespace(after=lambda delay, callback: scheduled.append((delay, callback)) or len(scheduled))
    module._shown = module.window.shown = True
    module._pump()
    assert not module.window.shown
    assert [delay for delay, _ in scheduled] == [16]
    module._pump()
    assert len(calls) == 2
    assert [delay for delay, _ in scheduled] == [16, 16]
    assert module.controller.value == progress
    assert module.controller.available_calls == []


def test_closed_qt_pump_neither_dispatches_events_nor_schedules_callbacks():
    module = module_stub(report([step(0)]))
    calls = []
    module._closed = True
    module._app = SimpleNamespace(processEvents=lambda: calls.append("events"))
    module.parent = SimpleNamespace(after=lambda *args: calls.append("after"))
    module._pump()
    assert calls == []


def test_qt_pump_closed_by_dispatched_event_does_not_schedule_another_callback():
    module = module_stub(report([step(0)]))
    calls = []
    module._closed = False

    def close_during_event():
        calls.append("events")
        module._closed = True

    module._app = SimpleNamespace(processEvents=close_during_event)
    module.parent = SimpleNamespace(after=lambda *args: calls.append("after"))
    module._pump()
    assert calls == ["events"]
