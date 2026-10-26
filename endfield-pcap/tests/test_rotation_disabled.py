"""Product-default retirement keeps local data and unrelated overlays alive."""
from __future__ import annotations

import ast
import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import QApplication, QPushButton

from endfield_pcap import rotation_client, rotation_overlay
from endfield_pcap.overlay_window_settings import OverlaySettingsDialog


def forbidden(*_args, **_kwargs):
    raise AssertionError("retired guide must not perform this action")


class Parent:
    def __init__(self):
        self.scheduled, self.cancelled, self.attributes_set = [], [], []

    def after(self, delay, callback):
        self.scheduled.append((delay, callback))
        return f"timer-{len(self.scheduled)}"

    def after_cancel(self, identifier):
        self.cancelled.append(identifier)

    def attributes(self, *values):
        self.attributes_set.append(values)


@pytest.fixture(scope="module")
def application():
    app = QApplication.instance() or QApplication([])
    yield app


def test_disabled_controller_never_loads_deletes_syncs_or_matches_cached_plans(tmp_path, monkeypatch):
    assert rotation_client.ROTATION_GUIDANCE_ENABLED is False
    cache_path, catalog_path = tmp_path / "cache.json", tmp_path / "catalog.json"
    saved_cache, saved_catalog = b'{"savedPlans":"keep-original"}', b'{"savedCatalog":"keep-original"}'
    cache_path.write_bytes(saved_cache)
    catalog_path.write_bytes(saved_catalog)
    cache = SimpleNamespace(path=cache_path, load=forbidden, save=forbidden, clear=forbidden)
    monkeypatch.setattr(rotation_client, "load_rotation_clock_spec", forbidden)
    controller = rotation_client.RotationGuideController(
        tmp_path / "trace.log", cache=cache,
        session_store=SimpleNamespace(load_session=forbidden),
        settings_store=SimpleNamespace(load_settings=forbidden), client_factory=forbidden)
    monkeypatch.setattr(controller._engine, "snapshot", forbidden)
    monkeypatch.setattr(controller._engine, "replace_plans", forbidden)
    monkeypatch.setattr(controller._engine, "feed_line", forbidden)
    controller.start()
    controller.request_sync()
    controller.set_available(True)
    assert controller.snapshot() is None
    assert controller._reconcile_session(force=True) is None
    assert controller._session_context() is None
    controller._sync_once((("test-account", "https://example.test"), "unused"))
    controller._install({"rotations": []}, "unused")
    controller._sync_loop()
    controller._tail_loop()
    assert controller._threads == [] and not controller._sync_requested.is_set()
    assert not controller._available and controller.sync_state()["disabled"] is True
    assert cache_path.read_bytes() == saved_cache and catalog_path.read_bytes() == saved_catalog
    controller.stop()


def test_disabled_guide_keeps_qt_pump_meter_settings_and_html_list(application, tmp_path, monkeypatch):
    assert rotation_client.ROTATION_GUIDANCE_ENABLED is False
    store = rotation_client.OverlaySettingsStore(tmp_path / "overlay-settings.json")
    original = {"meterEnabled": True, "guideEnabled": True, "opacity": .9,
                "meterOpacity": .8, "guideOpacity": .7, "guideScale": 1.3,
                "guideAnchor": [.4, .2], "guidePositionMode": "game",
                "positions": {"meter": [50, 60], "guide": [70, 80]}}
    store.save(original)
    monkeypatch.setattr(rotation_overlay, "OverlaySettingsStore", lambda: store)
    monkeypatch.setattr(rotation_overlay.webbrowser, "open", forbidden)
    parent, meter_changes = Parent(), []
    controller = SimpleNamespace(set_available=forbidden, snapshot=forbidden,
                                 sync_state=forbidden, request_sync=forbidden)
    module = rotation_overlay.RotationOverlayModule(parent, controller, on_meter_change=meter_changes.append)
    module.window.show()
    module.update(available=True, game_bounds=(0, 0, 1920, 1080))
    assert not module.window.isVisible() and not module._shown
    deleted = []
    pending = QObject()
    pending.destroyed.connect(lambda: deleted.append(True))
    pending.deleteLater()
    module._pump()
    assert deleted == [True] and len(parent.scheduled) == 2
    assert parent.scheduled[-1][0] == 16
    module.apply_module_settings("meter", enabled=False, opacity=.6)
    assert meter_changes == [False] and parent.attributes_set == [("-alpha", .6)]
    saved = json.loads(store.path.read_text(encoding="utf-8"))
    assert saved["guideEnabled"] is True and saved["positions"] == original["positions"]
    assert saved["guideOpacity"] == .7 and saved["guideScale"] == 1.3
    with pytest.raises(ValueError, match="已停用"):
        module.apply_module_settings("guide", enabled=True, opacity=.7)
    module.open_editor()
    entries = [{"id": "html-example", "name": "本地 HTML"}]
    dialog = OverlaySettingsDialog(SimpleNamespace(list_entries=lambda: copy.deepcopy(entries)), rotation_module=module)
    assert [dialog.items.item(i).data(Qt.ItemDataRole.UserRole) for i in range(dialog.items.count())] == ["meter", "html-example"]
    assert not {"网站编辑", "立即同步", "恢复指导条位置"}.intersection(
        button.text() for button in dialog.findChildren(QPushButton))
    assert dialog.legacy_scale.isHidden() and dialog.legacy_buttons.isHidden()
    before = store.path.read_bytes()
    dialog._open_editor()
    dialog._sync()
    dialog._recover_guide()
    assert store.path.read_bytes() == before
    dialog.close()
    dialog.deleteLater()
    module.close()
    assert parent.cancelled and module._closed


def test_overlay_run_starts_meter_tailer_and_ui_without_guide_controller():
    source = Path(__file__).resolve().parents[1] / "overlay.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    run = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "run"
               and any(isinstance(child, ast.Attribute) and child.attr == "rotation_controller" for child in ast.walk(node)))
    namespace = {"_rotation_client": rotation_client, "POLL_MS": 123}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[run], type_ignores=[])), str(source), "exec"), namespace)
    calls = []
    root = SimpleNamespace(after=lambda *args: calls.append(("after", args)), mainloop=lambda: calls.append(("mainloop",)))
    value = SimpleNamespace(tailer=SimpleNamespace(start=lambda: calls.append(("tailer",))),
                            rotation_controller=SimpleNamespace(start=forbidden), root=root, _update=forbidden)
    namespace["run"](value)
    assert [call[0] for call in calls] == ["tailer", "after", "mainloop"]
