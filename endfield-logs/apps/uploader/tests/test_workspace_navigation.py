from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from app.services.settings_store import UploaderSettings
from app.state.store import AuthSession, BattleCandidate
from app.ui.main_window import MainWindow


@pytest.fixture
def workspace(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr("app.ui.main_window.SettingsStore.load_settings", lambda self: UploaderSettings())
    monkeypatch.setattr("app.ui.main_window.SessionStore.load_session", lambda self: None)
    monkeypatch.setattr(MainWindow, "_run_healthcheck", lambda self, **kwargs: True)
    monkeypatch.setattr(MainWindow, "_setup_tray_icon", lambda self: None)
    monkeypatch.setattr("app.ui.main_window.should_check_for_updates", lambda: False)
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    yield window
    window._managed_log_timer.stop()
    window.api_client.client.close()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_navigation_is_available_before_login_and_does_not_change_auth(workspace):
    assert workspace.page_stack.currentWidget() is workspace.stack
    workspace.sidebar.buttons["overlays"].click()
    assert workspace.page_stack.currentWidget() is workspace.overlay_settings_host
    assert workspace.store.session is None
    assert workspace.sidebar.buttons["overlays"].isChecked()
    assert not workspace.sidebar.buttons["battle"].isChecked()
    workspace.sidebar.buttons["battle"].click()
    assert workspace.stack.currentWidget() is workspace.login_view


def test_switching_page_preserves_battle_selection_and_background_monitor(workspace, tmp_path):
    candidate = BattleCandidate(candidate_id="first", source_battle_index=1, source_log_path="fixture.log",
                                file_name="fixture.log", boss_name="罗丹", dungeon_name="危境再现", duration_ms=12000,
                                roster_names=["管理员"], payload={"battle": {"totalDamage": 1234, "clearFlag": True,
                                "officialTimerEndSeen": True, "timerWindowValid": True,
                                "dungeonIdentitySource": "dungeon_context", "dungeonKey": "dung01_group_bossrush01",
                                "dungeonContextId": "dung01_group_bossrush01"}}, selected=True)
    workspace.store.candidates = [candidate]
    workspace.store.session = AuthSession(session_token="test-only", user_id="user", email="", nickname="测试账号")
    workspace.battle_upload_view.set_candidates([candidate])
    workspace._show_battle_upload()
    workspace._managed_log_path = str(tmp_path / "trace_managed.log")
    workspace._managed_log_timer.start()
    workspace.sidebar.buttons["overlays"].click()
    workspace._refresh_status_bar("正在解析")
    # An automatic completion may update the battle workflow while settings stay visible.
    workspace._show_battle_upload()
    assert workspace.page_stack.currentWidget() is workspace.overlay_settings_host
    assert workspace._managed_log_timer.isActive()
    assert workspace.sidebar.status_label.text() == "正在监听战斗"
    assert workspace.store.candidates == [candidate] and candidate.selected
    workspace.sidebar.buttons["battle"].click()
    assert workspace.stack.currentWidget() is workspace.battle_upload_view
    assert workspace.battle_upload_view._all_candidates[0] is candidate


def test_settings_page_mount_is_stable_and_keeps_form_values(workspace):
    page = QWidget()
    page.setObjectName("testSettings")
    label = QLabel("当前设置", page)
    layout = QVBoxLayout(page)
    layout.addWidget(label)
    workspace.set_overlay_settings_view(page)
    workspace.show_workspace_page("overlays")
    workspace.show_workspace_page("battle")
    workspace.show_workspace_page("overlays")
    assert workspace.overlay_settings_view is page
    assert workspace._overlay_settings_layout.count() == 1
    assert label.text() == "当前设置"
    workspace.set_overlay_settings_view(page)
    assert workspace._overlay_settings_layout.count() == 1
    replacement = QWidget()
    workspace.set_overlay_settings_view(replacement)
    assert page.parent() is None
    assert workspace.overlay_settings_view is replacement
    assert workspace._overlay_settings_layout.count() == 1
    page.deleteLater()


def test_navigation_rejects_unknown_page_and_supports_keyboard_focus(workspace):
    with pytest.raises(ValueError, match="Unsupported"):
        workspace.show_workspace_page("editor")
    for key, button in workspace.sidebar.buttons.items():
        assert button.accessibleName()
        assert button.focusPolicy() != Qt.FocusPolicy.NoFocus
        assert button.minimumHeight() >= 44
    assert workspace.sidebar.width() == 184


def test_overlay_activation_hook_and_connection_status_are_independent_of_battle_phase(workspace):
    class Settings(QWidget):
        calls = 0
        def on_activated(self):
            self.calls += 1
    page = Settings()
    workspace.set_overlay_settings_view(page)
    workspace.show_workspace_page("overlays")
    workspace.sidebar.set_overlay_status("采集已连接")
    workspace._refresh_status_bar("正在解析")
    assert page.calls == 1
    assert workspace.sidebar.overlay_status_label.text() == "采集已连接"
    workspace.show_workspace_page("battle")
    workspace.show_workspace_page("overlays")
    assert page.calls == 2
