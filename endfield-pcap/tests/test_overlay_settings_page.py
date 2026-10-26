from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QTimer, Qt, Signal
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from endfield_pcap.overlay_window_config import OverlayWindowConfig
from endfield_pcap.overlay_window_settings import OverlaySettingsDialog, OverlaySettingsPage


class ControlFixture(QObject):
    snapshotChanged = Signal(dict)
    connectionChanged = Signal(str)
    operationFinished = Signal(str, dict)
    operationFailed = Signal(str, str)

    def __init__(self, root, *, live=True):
        super().__init__()
        self.root = root
        self.html = root / "my-overlay.html"
        self.html.write_text("<html><body>my own HTML</body></html>", encoding="utf-8")
        self.config = OverlayWindowConfig(root / "windows.json")
        self.entry = self.config.add_entry("效果监控", "file", str(self.html))
        self.requests = []
        self.complete_automatically = True
        self.snapshot = {"entries": self.config.list_entries(), "meter": {"meterEnabled": True, "meterOpacity": .9, "opacity": .9, "guideScale": 1.0},
                         "online": live, "status": "ready", "capabilities": {"configuration": True, "liveActions": live}}

    def list_entries(self):
        return copy.deepcopy(self.snapshot["entries"])

    def get_entry(self, identifier):
        return next(copy.deepcopy(entry) for entry in self.snapshot["entries"] if entry["id"] == identifier)

    def submit(self, operation, arguments):
        identifier = str(len(self.requests) + 1)
        self.requests.append((identifier, operation, copy.deepcopy(arguments)))
        if self.complete_automatically:
            QTimer.singleShot(0, lambda: self.complete(identifier, operation, arguments))
        return identifier

    def complete(self, identifier, operation, arguments):
        value = None
        if operation == "update":
            patch = copy.deepcopy(arguments["patch"])
            if "geometry" in patch:
                patch["geometry"] = {**self.config.get_entry(arguments["id"])["geometry"], **patch["geometry"]}
            value = self.config.update_entry(arguments["id"], **patch)
        elif operation == "add":
            value = self.config.add_entry(arguments["name"], arguments["sourceType"], arguments["sourceValue"])
        elif operation == "remove":
            self.config.remove_entry(arguments["id"])
        elif operation == "meter":
            self.snapshot["meter"].update(meterEnabled=arguments["enabled"], meterOpacity=arguments["opacity"])
        elif operation == "diagnostics":
            value = "仅此窗口的脱敏诊断"
        self.snapshot["entries"] = self.config.list_entries()
        self.snapshotChanged.emit(copy.deepcopy(self.snapshot))
        self.operationFinished.emit(identifier, {"snapshot": self.snapshot, "value": value, "offlineSaved": not self.snapshot["online"]})


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


@pytest.fixture
def form(app, tmp_path):
    client = ControlFixture(tmp_path)
    parent = QWidget()
    page = OverlaySettingsPage(client, parent=parent)
    page.refresh_list(client.entry["id"])
    yield page, client
    if page._diagnostic_dialog:
        page._diagnostic_dialog.close()
    parent.close()
    parent.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def flush(app):
    for _ in range(3):
        app.processEvents()


def test_settings_page_is_embedded_and_guidance_absent(form):
    page, client = form
    assert not page.isWindow()
    assert not bool(page.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
    assert page.items.count() == 2
    assert [page.items.item(i).text() for i in range(2)] == ["基础 DPS", "效果监控"]
    assert page.legacy_scale.isHidden() and page.legacy_scale_label.isHidden()
    assert not any("排轴" in page.items.item(i).text() for i in range(page.items.count()))


def test_remote_save_is_async_and_merges_only_edited_dimensions(form, app):
    page, client = form
    page.name.setText("我的效果监控")
    page.geometry_inputs["width"].setValue(570)
    client.config.update_entry(client.entry["id"], geometry={"x": 377, "y": 244, "width": 440, "height": 320})
    client.snapshot["entries"] = client.config.list_entries()
    client.snapshotChanged.emit(copy.deepcopy(client.snapshot))
    assert page.name.text() == "我的效果监控" and page._dirty
    page._save_entry()
    assert page._pending is not None
    assert client.config.get_entry(client.entry["id"])["name"] == "效果监控"
    assert client.requests[-1][2]["patch"]["geometry"] == {"width": 570}
    flush(app)
    saved = client.config.get_entry(client.entry["id"])
    assert saved["name"] == "我的效果监控"
    assert saved["geometry"] == {"x": 377, "y": 244, "width": 570, "height": 320}
    assert page._pending is None and not page._dirty


def test_failed_remote_save_does_not_report_success_or_drop_draft(form):
    page, client = form
    client.complete_automatically = False
    page.name.setText("未保存的名字")
    page._save_entry()
    identifier = page._pending[0]
    assert not page.items.isEnabled()
    client.operationFailed.emit(identifier, "连接中断，未确认设置生效。")
    assert page._pending is None and page._dirty
    assert page.name.text() == "未保存的名字"
    assert "连接中断" in page.feedback.text()
    assert "已保存" not in page.feedback.text()
    assert client.config.get_entry(client.entry["id"])["name"] == "效果监控"


def test_offline_save_explains_next_start_and_live_buttons_are_disabled(form, app):
    page, client = form
    client.snapshot.update(online=False, capabilities={"configuration": True, "liveActions": False})
    client.connectionChanged.emit("离线设置")
    assert "下次启动" in page.connection_badge.text()
    assert not any(button.isEnabled() for button in page._live_buttons)
    page.flags["clickThrough"].setChecked(True)
    page.opacity.setValue(75)
    page._save_entry()
    flush(app)
    saved = client.config.get_entry(client.entry["id"])
    assert saved["clickThrough"] and saved["opacity"] == .75
    assert "下次启动" in page.feedback.text()


def test_managed_disconnect_prevents_mutations(form):
    page, client = form
    client.snapshot.update(online=False, capabilities={"configuration": False, "liveActions": False})
    client.connectionChanged.emit("服务未连接")
    page.name.setText("连接恢复后再保存")
    page._save_entry()
    assert not client.requests
    assert "尚未连接" in page.feedback.text()
    assert not any(button.isEnabled() for button in page._configuration_buttons)


def test_native_logs_and_remove_keep_original_html(form, app, monkeypatch):
    page, client = form
    page._show_diagnostics()
    flush(app)
    assert page._diagnostic_dialog is not None
    from PySide6.QtWidgets import QPlainTextEdit
    log = page._diagnostic_dialog.findChild(QPlainTextEdit)
    assert log.isReadOnly() and "脱敏诊断" in log.toPlainText()
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    page._delete()
    flush(app)
    assert not client.config.list_entries()
    assert client.html.read_text(encoding="utf-8") == "<html><body>my own HTML</body></html>"
    assert page._selected_id == "meter"


def test_meter_settings_use_same_async_control(form, app):
    page, client = form
    page.refresh_list("meter")
    page.legacy_enabled.setChecked(False)
    page.legacy_opacity.setValue(64)
    page._save_legacy()
    assert client.requests[-1][1:] == ("meter", {"enabled": False, "opacity": .64})
    flush(app)
    assert client.snapshot["meter"]["meterEnabled"] is False
    assert client.snapshot["meter"]["meterOpacity"] == .64


def test_selection_change_keeps_dirty_inputs_when_discard_rejected(form, monkeypatch):
    page, client = form
    page.name.setText("当前草稿")
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.No)
    page.items.setCurrentRow(0)
    assert page._selected_id == client.entry["id"]
    assert page.items.currentRow() == 1
    assert page.name.text() == "当前草稿"
    page.on_activated()
    assert page.name.text() == "当前草稿"


def test_local_preview_dialog_delegates_legacy_fields_and_preserves_edit_state(app, tmp_path):
    client = ControlFixture(tmp_path)
    config = client.config
    calls = []
    rotation = SimpleNamespace(settings={"meterEnabled": True, "opacity": .9, "guideScale": 1.0},
                               external_settings_open=False, window=SimpleNamespace(set_editing=lambda value: calls.append(value)))
    dialog = OverlaySettingsDialog(config, rotation_module=rotation)
    try:
        dialog.show()
        app.processEvents()
        assert rotation.external_settings_open
        dialog.refresh_list(client.entry["id"])
        dialog.name.setText("改名而不覆盖刚拖动的位置")
        config.update_entry(client.entry["id"], geometry={"x": 319, "y": 211, "width": 560, "height": 320})
        dialog._save_entry()
        assert config.get_entry(client.entry["id"])["geometry"]["x"] == 319
        dialog.hide()
        assert rotation.external_settings_open is False and calls[-1] is False
    finally:
        dialog.close()
        dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
