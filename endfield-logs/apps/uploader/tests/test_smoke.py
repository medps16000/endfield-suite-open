import json
import os
import time

from PySide6.QtCore import QMimeData, QUrl
from PySide6.QtWidgets import QApplication, QLabel

from app.auth.session_store import SessionStore
from app.services.api_client import SAFE_ACCEPT_ENCODING, ApiClient, ApiClientError
from app.services.settings_store import SettingsStore, UploaderSettings
from app.state.store import AuthSession, BattleCandidate, UploaderStore
from app.ui.assets import asset_path
from app.ui.main_window import MainWindow, ParseTraceWorker, _battle_payload_is_completed
from app.ui.views.battle_upload_view import BattleUploadView
from app.ui.views.login_view import LoginView
from app.ui.views.settings_dialog import SettingsDialog
from app.ui.views.trace_import_view import TraceImportView


def test_store_defaults() -> None:
    store = UploaderStore()
    assert store.current_trace_file_name is None
    assert store.current_trace_path is None
    assert store.current_integrity_label is None
    assert store.current_trace_integrity_verified is False
    assert store.session is None
    assert store.candidates == []
    assert store.last_uploaded_battle_urls == []
    assert store.upload_running is False


def test_api_client_never_advertises_zstd() -> None:
    client = ApiClient(base_url="https://zmdlogs.com")
    try:
        assert client.client.headers["Accept-Encoding"] == SAFE_ACCEPT_ENCODING
        assert "zstd" not in client.client.headers["Accept-Encoding"]
    finally:
        client.client.close()


def test_completion_check_fails_closed_for_missing_or_invalid_timer_evidence() -> None:
    assert _battle_payload_is_completed({"battle": {}}) is False
    assert _battle_payload_is_completed({"battle": {"clearFlag": True, "timerWindowValid": False}}) is False
    assert _battle_payload_is_completed(
        {
            "battle": {
                "clearFlag": True,
                "timerWindowValid": True,
                "officialTimerEndSeen": True,
            }
        }
    ) is True


def test_upload_view_requires_official_dungeon_identity() -> None:
    assert BattleUploadView._dungeon_identity_is_unverified(
        {
            "dungeonKey": "dung01_group_bossrush01",
            "dungeonIdentitySource": "dungeon_context",
            "dungeonContextId": "dung01_group_bossrush01",
        }
    ) is False
    assert BattleUploadView._dungeon_identity_is_unverified(
        {
            "dungeonKey": "dung01_group_bossrush01",
            "dungeonIdentitySource": "inferred_from_boss",
            "bossIdentitySource": "trace_inference",
        }
    ) is True


def test_uploader_logo_assets_exist() -> None:
    assert asset_path("logo.svg").exists()
    assert asset_path("logo.png").exists()
    assert asset_path("logo.ico").exists()


def test_battle_upload_view_exposes_retry_and_open_actions() -> None:
    app = QApplication.instance() or QApplication([])
    view = BattleUploadView()
    assert all(button.text() != "设置" for button in view.findChildren(type(view.upload_button)))
    view.set_candidates(
        [
            BattleCandidate(
                candidate_id="candidate-1",
                source_battle_index=1,
                source_log_path="sample.log",
                file_name="sample.log",
                boss_name="首领甲",
                dungeon_name="副本甲",
                duration_ms=12345,
                roster_names=["甲", "乙"],
                payload={
                    "battle": {
                        "totalDamage": 123456,
                        "totalDps": 9999.99,
                        "battleFingerprint": "fingerprint-existing-1",
                        "rulesVersion": "rules-v1",
                        "parserVersion": "parser-v1",
                        "battleStartAt": "2026-04-22T10:00:00+08:00",
                        "battleEndAt": "2026-04-22T10:00:12+08:00",
                    }
                },
                duplicate=True,
                duplicate_url="/battle/existing-1",
            ),
            BattleCandidate(
                candidate_id="candidate-2",
                source_battle_index=2,
                source_log_path="sample.log",
                file_name="sample.log",
                boss_name="首领乙",
                dungeon_name="副本乙",
                duration_ms=23456,
                roster_names=["丙", "丁"],
                payload={
                    "battle": {
                        "totalDamage": 234567,
                        "totalDps": 8888.88,
                        "battleFingerprint": "fingerprint-failed-2",
                        "rulesVersion": "rules-v1",
                        "parserVersion": "parser-v1",
                        "battleStartAt": "2026-04-22T10:01:00+08:00",
                        "battleEndAt": "2026-04-22T10:01:23+08:00",
                        "clearFlag": True,
                        "timerEndSeen": True,
                        "timerWindowValid": False,
                        "rdpsStrictOk": False,
                        "rdpsPreflightBlockerCount": 2,
                        "loadoutFallbackUsed": True,
                        "bossIdentitySource": "trace_inference",
                        "dungeonIdentitySource": "dungeon_context",
                        "dungeonContextId": "dung01_group_bossrush01",
                        "dungeonKey": "dung01_group_bossrush01",
                    }
                },
                selected=True,
                upload_error="网络错误",
            ),
            BattleCandidate(
                candidate_id="candidate-3",
                source_battle_index=3,
                source_log_path="sample.log",
                file_name="sample.log",
                boss_name="首领丙",
                dungeon_name="副本丙",
                duration_ms=34567,
                roster_names=["戊", "己"],
                payload={
                    "battle": {
                        "totalDamage": 345678,
                        "totalDps": 7777.77,
                        "battleFingerprint": "fingerprint-uploaded-3",
                        "rulesVersion": "rules-v1",
                        "parserVersion": "parser-v1",
                        "battleStartAt": "2026-04-22T10:02:00+08:00",
                        "battleEndAt": "2026-04-22T10:02:34+08:00",
                    }
                },
                upload_url="/battle/uploaded-3",
            ),
        ]
    )

    assert view.failed_candidate_ids() == ["candidate-2"]
    assert view.selected_candidate_ids() == ["candidate-2"]

    view.list_widget.setCurrentRow(0)
    assert view.open_record_button.isEnabled() is True

    view.list_widget.setCurrentRow(2)
    assert view.open_record_button.isEnabled() is True
    assert "总伤：" in view.list_widget.item(0).text()
    assert "规则：" in view.list_widget.item(0).text()
    assert "完整指纹" in view.list_widget.item(0).toolTip()
    assert "计时窗口无效" in view.list_widget.item(1).text()
    assert "rDPS审计失败(2项)" in view.list_widget.item(1).text()
    assert "配装使用兜底" in view.list_widget.item(1).text()
    assert "官方场地 ID：dung01_group_bossrush01" in view.list_widget.item(1).toolTip()

    view.status_filter.setCurrentText("仅失败项")
    assert view.list_widget.count() == 1
    assert "首领乙" in view.list_widget.item(0).text()

    view.status_filter.setCurrentText("全部状态")
    view.sort_order.setCurrentText("按时长（长到短）")
    assert "首领丙" in view.list_widget.item(0).text()

    view.search_input.setText("首领乙")
    assert view.list_widget.count() == 1
    assert "首领乙" in view.list_widget.item(0).text()

    view.search_input.clear()
    view.status_filter.setCurrentText("仅可上传")
    view.select_all_button.click()
    assert view.selected_candidate_ids() == ["candidate-2"]
    view.clear_selection_button.click()
    assert view.selected_candidate_ids() == []

    view.close()
    if QApplication.instance() is app and not QApplication.topLevelWidgets():
        app.quit()


def test_trace_import_view_extracts_dropped_log_path() -> None:
    mime_data = QMimeData()
    mime_data.setUrls(
        [
            QUrl.fromLocalFile("C:/path/to/endfield-suite/logs/endfield_battle3.txt"),
            QUrl.fromLocalFile("C:/path/to/endfield-suite/logs/endfield_battle3.log"),
        ]
    )
    assert TraceImportView.extract_log_file_path(mime_data) == "C:/path/to/endfield-suite/logs/endfield_battle3.log"


def test_trace_import_view_progress_roundtrip() -> None:
    app = QApplication.instance() or QApplication([])
    view = TraceImportView()
    assert all(button.text() != "设置" for button in view.findChildren(type(view.parse_button)))
    view.set_selected_file("sample.log", "完整性：已通过")
    view.set_parse_allowed(True)
    assert view.parse_button.isEnabled() is True
    view.set_parse_allowed(False)
    assert view.parse_button.isEnabled() is False
    view.set_progress("正在解析…", current=1, total=3)
    assert view.progress_label.text() == "正在解析…"
    assert view.progress_bar.maximum() == 3
    assert view.progress_bar.value() == 1
    view.clear_progress()
    assert view.progress_label.text() == ""
    assert view.progress_bar.isHidden() is True
    view.close()
    if QApplication.instance() is app and not QApplication.topLevelWidgets():
        app.quit()


def test_login_view_switches_between_login_and_register_rules() -> None:
    app = QApplication.instance() or QApplication([])
    view = LoginView()
    brand_logo = view.findChild(QLabel, "brandLogo")
    assert brand_logo is not None
    assert brand_logo.pixmap() is not None
    assert view.login_button.isEnabled() is False
    assert view.register_button.isHidden() is True
    assert view.login_button.isEnabled() is False

    view.email_input.setText("tester@example.com")
    assert view.login_button.isEnabled() is False

    view.password_input.setText("hunter2pass")
    assert view.login_button.isEnabled() is True

    view.set_mode("register")
    assert view.register_button.isHidden() is False
    assert view.login_button.isHidden() is True
    assert view.register_button.isEnabled() is False
    assert view.register_code_input.isHidden() is False
    assert all(button.text() not in {"连接设置", "设置"} for button in view.findChildren(type(view.login_button)))

    view.confirm_password_input.setText("hunter2pass")
    view.nickname_input.setText("测试昵称")
    assert view.register_button.isEnabled() is False
    view.register_code_input.setText("123456")
    assert view.register_button.isEnabled() is True
    view.resize(455, 623)
    view.show()
    app.processEvents()
    assert view.email_input.height() == 48
    assert view.password_input.height() == 48
    assert view.confirm_password_input.height() == 48
    assert view.nickname_input.height() == 48

    view.confirm_password_input.setText("mismatch")
    assert view.register_button.isEnabled() is False

    view.clear_profile_setup()
    assert view.login_button.isHidden() is False
    assert view.register_button.isHidden() is True

    view.close()
    if QApplication.instance() is app and not QApplication.topLevelWidgets():
        app.quit()


def test_battle_upload_view_keeps_selection_across_filters() -> None:
    app = QApplication.instance() or QApplication([])
    view = BattleUploadView()
    candidates = [
        BattleCandidate(
            candidate_id="candidate-1",
            source_battle_index=1,
            source_log_path="sample.log",
            file_name="sample.log",
            boss_name="首领甲",
            dungeon_name="副本甲",
            duration_ms=12345,
            roster_names=["甲"],
            payload={
                "battle": {
                    "battleFingerprint": "fp-1",
                    "rulesVersion": "rules-v1",
                    "dungeonKey": "dung01_group_bossrush01",
                    "dungeonIdentitySource": "dungeon_context",
                    "dungeonContextId": "dung01_group_bossrush01",
                }
            },
        ),
        BattleCandidate(
            candidate_id="candidate-2",
            source_battle_index=2,
            source_log_path="sample.log",
            file_name="sample.log",
            boss_name="首领乙",
            dungeon_name="副本乙",
            duration_ms=23456,
            roster_names=["乙"],
            payload={
                "battle": {
                    "battleFingerprint": "fp-2",
                    "rulesVersion": "rules-v1",
                    "dungeonKey": "dung01_group_bossrush01",
                    "dungeonIdentitySource": "dungeon_context",
                    "dungeonContextId": "dung01_group_bossrush01",
                }
            },
            selected=True,
        ),
    ]
    view.set_candidates(candidates)
    assert view.selected_candidate_ids() == ["candidate-2"]
    view.search_input.setText("首领甲")
    assert view.list_widget.count() == 1
    assert view.selected_candidate_ids() == ["candidate-2"]
    assert "已勾选：1" in view.summary_label.text()

    view.close()
    if QApplication.instance() is app and not QApplication.topLevelWidgets():
        app.quit()


def test_battle_upload_view_shows_uncleared_battles_by_default() -> None:
    app = QApplication.instance() or QApplication([])
    view = BattleUploadView()
    candidates = [
        BattleCandidate(
            candidate_id="candidate-1",
            source_battle_index=1,
            source_log_path="sample.log",
            file_name="sample.log",
            boss_name="首领甲",
            dungeon_name="副本甲",
            duration_ms=12345,
            roster_names=["甲"],
            payload={
                "battle": {
                    "battleFingerprint": "fp-1",
                    "rulesVersion": "rules-v1",
                    "clearFlag": True,
                    "dungeonKey": "dung01_group_bossrush01",
                    "dungeonIdentitySource": "dungeon_context",
                    "dungeonContextId": "dung01_group_bossrush01",
                }
            },
            selected=True,
        ),
        BattleCandidate(
            candidate_id="candidate-2",
            source_battle_index=2,
            source_log_path="sample.log",
            file_name="sample.log",
            boss_name="首领乙",
            dungeon_name="副本乙",
            duration_ms=23456,
            roster_names=["乙"],
            payload={
                "battle": {
                    "battleFingerprint": "fp-2",
                    "rulesVersion": "rules-v1",
                    "clearFlag": False,
                    "dungeonKey": "dung01_group_bossrush01",
                    "dungeonIdentitySource": "dungeon_context",
                    "dungeonContextId": "dung01_group_bossrush01",
                }
            },
            selected=True,
        ),
    ]

    view.set_candidates(candidates, reset_uncleared_filter=True)

    assert view.show_uncleared_checkbox.isChecked() is True
    assert view.list_widget.count() == 2
    assert "首领甲" in view.list_widget.item(0).text()
    assert view.selected_candidate_ids() == ["candidate-1", "candidate-2"]
    assert "未完成：1" in view.summary_label.text()
    assert "未完成" in view.list_widget.item(1).text()

    view.show_uncleared_checkbox.setChecked(False)
    assert view.list_widget.count() == 1
    assert view.selected_candidate_ids() == ["candidate-1"]
    assert "已隐藏未完成：1" in view.summary_label.text()

    view.show_uncleared_checkbox.setChecked(True)
    view.select_all_button.click()
    assert view.selected_candidate_ids() == ["candidate-1", "candidate-2"]

    view.show_uncleared_checkbox.setChecked(False)
    assert view.list_widget.count() == 1
    assert view.selected_candidate_ids() == ["candidate-1"]

    view.close()
    if QApplication.instance() is app and not QApplication.topLevelWidgets():
        app.quit()


def test_battle_upload_view_keeps_action_panel_height_during_upload_state() -> None:
    app = QApplication.instance() or QApplication([])
    view = BattleUploadView()
    view.resize(980, 660)
    view.set_candidates(
        [
            BattleCandidate(
                candidate_id="candidate-1",
                source_battle_index=1,
                source_log_path="sample.log",
                file_name="sample.log",
                boss_name="首领甲",
                dungeon_name="副本甲",
                duration_ms=12345,
                roster_names=["甲"],
                payload={"battle": {"battleFingerprint": "fp-1", "rulesVersion": "rules-v1"}},
                selected=True,
            )
        ]
    )
    view.show()
    view.set_progress("正在上传第 1/6 场：硬骨之拳 罗丹", current=0, total=6)
    view.set_message("上传状态提示")
    view.set_busy(True)
    app.processEvents()

    assert view.action_panel.height() >= view.action_panel.minimumHeight()
    assert view.back_button.height() >= view.back_button.minimumHeight()
    assert view.logout_button.height() >= view.logout_button.minimumHeight()

    view.close()
    if QApplication.instance() is app and not QApplication.topLevelWidgets():
        app.quit()


def test_session_store_roundtrip_hides_plaintext_token(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    store = SessionStore()
    payload = {
        "sessionToken": "ses_test_secret_token",
        "userId": "usr_test",
        "email": "test@example.com",
        "nickname": "Tester",
    }

    store.save_session(payload)
    loaded = store.load_session()

    assert loaded is not None
    assert loaded["sessionToken"] == "ses_test_secret_token"
    assert loaded["nickname"] == "Tester"

    raw_path = tmp_path / "EndfieldPCAP" / "session.json"
    raw_payload = json.loads(raw_path.read_text(encoding="utf-8"))
    assert "sessionToken" not in raw_payload
    assert raw_payload["sessionTokenProtected"]
    assert "ses_test_secret_token" not in raw_path.read_text(encoding="utf-8")


def test_settings_store_roundtrip(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    store = SettingsStore()
    store.save_settings(
        UploaderSettings(
            api_base_url="http://127.0.0.1:8100",
            web_base_url="http://127.0.0.1:3100",
            last_log_dir="D:/logs/endfield",
        )
    )
    loaded = store.load_settings()
    assert loaded.api_base_url == "http://127.0.0.1:8100"
    assert loaded.web_base_url == "http://127.0.0.1:3100"
    assert loaded.last_log_dir == "D:/logs/endfield"


def test_settings_store_migrates_frozen_defaults_to_public_domain(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr("uploader_core.settings_store.sys.frozen", True, raising=False)

    store = SettingsStore()
    loaded = store.load_settings()
    assert loaded.api_base_url == "https://zmdlogs.com"
    assert loaded.web_base_url == "https://zmdlogs.com"

    store.save_settings(UploaderSettings(api_base_url="http://192.0.2.10", web_base_url="http://192.0.2.10"))
    migrated = store.load_settings()
    assert migrated.api_base_url == "https://zmdlogs.com"
    assert migrated.web_base_url == "https://zmdlogs.com"


def test_settings_dialog_roundtrip() -> None:
    app = QApplication.instance() or QApplication([])
    dialog = SettingsDialog(UploaderSettings(last_log_dir="D:/logs/endfield"))
    dialog.api_base_url_input.setText("http://127.0.0.1:9000")
    dialog.web_base_url_input.setText("http://127.0.0.1:4000")
    settings = dialog.current_settings()
    assert settings.api_base_url == "http://127.0.0.1:9000"
    assert settings.web_base_url == "http://127.0.0.1:4000"
    assert settings.last_log_dir == "D:/logs/endfield"
    dialog.close()
    if QApplication.instance() is app and not QApplication.topLevelWidgets():
        app.quit()


def test_manual_load_lands_on_import_view_and_pauses_monitoring(monkeypatch, tmp_path) -> None:
    """手动加载日志后必须停在导入页（有“开始解析”按钮）并暂停自动监听——
    2026-07-05 回归：用户被卡在没有解析入口的上传页、监听定时器抢占视图。"""
    monkeypatch.setattr("app.ui.main_window.SettingsStore.load_settings", lambda self: UploaderSettings())
    monkeypatch.setattr("app.ui.main_window.SessionStore.load_session", lambda self: None)
    monkeypatch.setattr("app.ui.main_window.ApiClient.healthcheck", lambda self: {"status": "ok"})
    monkeypatch.setattr(
        "app.ui.main_window.load_raw_log_integrity",
        lambda path: {"verified": True, "proof_source": "test", "issues": []},
    )
    app = QApplication.instance() or QApplication([])
    window = MainWindow()

    log_path = tmp_path / "manual.log"
    log_path.write_text("dummy", encoding="utf-8")

    # 进入监听模式，再切到上传页
    window._managed_log_path = str(tmp_path / "trace_managed.log")
    window._managed_log_timer.start()
    window._show_battle_upload()

    # 从上传页返回：暂停监听 + 回导入页 + 显示“恢复自动监听”
    window._handle_back_from_upload()
    assert window._managed_log_path is None
    assert window._paused_managed_log_path == str(tmp_path / "trace_managed.log")
    assert window._managed_log_timer.isActive() is False
    assert window.stack.currentWidget() is window.trace_import_view
    assert window.trace_import_view.resume_monitoring_button.isHidden() is False

    # 手动加载：仍停在导入页（解析入口可达），监听保持暂停
    window._load_trace_file(str(log_path))
    assert window.stack.currentWidget() is window.trace_import_view
    assert window._managed_log_path is None

    # 恢复监听：回到监听态
    window._handle_resume_monitoring()
    assert window._managed_log_path == str(tmp_path / "trace_managed.log")
    assert window._paused_managed_log_path is None

    window.close()
    if QApplication.instance() is app and not QApplication.topLevelWidgets():
        app.quit()


def test_main_window_status_bar_boots(monkeypatch) -> None:
    monkeypatch.setattr("app.ui.main_window.SettingsStore.load_settings", lambda self: UploaderSettings())
    monkeypatch.setattr("app.ui.main_window.SessionStore.load_session", lambda self: None)
    monkeypatch.setattr("app.ui.main_window.ApiClient.healthcheck", lambda self: {"status": "ok"})
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    assert window.windowTitle() == "Endfield Logs 客户端"
    assert window.windowIcon().isNull() is False
    assert window._status_user_label.text().startswith("账号：")
    assert window._status_file_label.text().startswith("文件：")
    assert window._status_phase_label.text().startswith("阶段：")
    assert window._status_server_label.text() == "服务器：已连接"
    window.close()
    if QApplication.instance() is app and not QApplication.topLevelWidgets():
        app.quit()


def test_main_window_remembers_trace_directory(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("app.ui.main_window.SettingsStore.load_settings", lambda self: UploaderSettings())
    monkeypatch.setattr("app.ui.main_window.SessionStore.load_session", lambda self: None)
    monkeypatch.setattr("app.ui.main_window.ApiClient.healthcheck", lambda self: {"status": "ok"})
    saved_dirs: list[str] = []
    monkeypatch.setattr(
        "app.ui.main_window.SettingsStore.save_settings",
        lambda self, settings: saved_dirs.append(settings.last_log_dir),
    )
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    log_path = log_dir / "sample.log"
    log_path.write_text("dummy", encoding="utf-8")

    window._remember_trace_directory(str(log_path))

    assert window.settings.last_log_dir == str(log_dir.resolve())
    assert saved_dirs == [str(log_dir.resolve())]
    window.close()
    if QApplication.instance() is app and not QApplication.topLevelWidgets():
        app.quit()


def test_main_window_existing_email_switches_back_to_login(monkeypatch) -> None:
    monkeypatch.setattr("app.ui.main_window.SettingsStore.load_settings", lambda self: UploaderSettings())
    monkeypatch.setattr("app.ui.main_window.SessionStore.load_session", lambda self: None)
    monkeypatch.setattr("app.ui.main_window.ApiClient.healthcheck", lambda self: {"status": "ok"})
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.login_view.set_mode("register")
    send_called = {"value": False}
    monkeypatch.setattr(window.api_client, "check_email", lambda email: {"available": False})
    monkeypatch.setattr(
        window.api_client,
        "send_code",
        lambda email, purpose="uploader_login": send_called.__setitem__("value", True),
    )

    window._handle_send_register_code("registered@example.com")

    assert send_called["value"] is False
    assert window.login_view.login_button.isHidden() is False
    assert window.login_view.register_button.isHidden() is True
    assert window.login_view.email_input.text() == "registered@example.com"
    assert "已经注册过了" in window.login_view.message_label.text()

    window.close()
    if QApplication.instance() is app and not QApplication.topLevelWidgets():
        app.quit()


def test_main_window_password_login_enters_managed_archive_view(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("app.ui.main_window.SettingsStore.load_settings", lambda self: UploaderSettings())
    monkeypatch.setattr("app.ui.main_window.SessionStore.load_session", lambda self: None)
    monkeypatch.setattr("app.ui.main_window.ApiClient.healthcheck", lambda self: {"status": "ok"})
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    managed_log = tmp_path / "trace_managed.log"
    window._managed_log_path = str(managed_log)
    saved_payloads: list[dict] = []
    monkeypatch.setattr(
        window.api_client,
        "login_with_password",
        lambda email, password: {
            "sessionToken": "ses_login_test",
            "user": {"id": "usr_login", "email": email, "nickname": "LoginTester"},
        },
    )
    monkeypatch.setattr(window.session_store, "save_session", lambda payload: saved_payloads.append(payload))

    window._handle_login("login@example.com", "password123")

    assert window.store.session is not None
    assert window.store.session.session_token == "ses_login_test"
    assert saved_payloads[0]["sessionToken"] == "ses_login_test"
    assert window.stack.currentWidget() is window.battle_upload_view
    assert "正在监听自动归档日志" in window.battle_upload_view.message_label.text()
    assert window._status_user_label.text() == "账号：LoginTester"
    window.close()
    if QApplication.instance() is app and not QApplication.topLevelWidgets():
        app.quit()


def test_main_window_password_login_survives_session_persistence_failure(monkeypatch) -> None:
    monkeypatch.setattr("app.ui.main_window.SettingsStore.load_settings", lambda self: UploaderSettings())
    monkeypatch.setattr("app.ui.main_window.SessionStore.load_session", lambda self: None)
    monkeypatch.setattr("app.ui.main_window.ApiClient.healthcheck", lambda self: {"status": "ok"})
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    monkeypatch.setattr(
        window.api_client,
        "login_with_password",
        lambda email, password: {
            "sessionToken": "ses_memory_only",
            "user": {"id": "usr_login", "email": email, "nickname": "LoginTester"},
        },
    )

    def fail_to_save(payload: dict) -> None:
        raise OSError("DPAPI test failure")

    monkeypatch.setattr(window.session_store, "save_session", fail_to_save)

    window._handle_login("login@example.com", "password123")

    assert window.store.session is not None
    assert window.api_client.session_token == "ses_memory_only"
    assert window.stack.currentWidget() is window.trace_import_view
    assert "本地登录状态未能保存" in window.trace_import_view.message_label.text()
    assert "重启后需重新登录" in window.trace_import_view.message_label.text()
    window.close()
    if QApplication.instance() is app and not QApplication.topLevelWidgets():
        app.quit()


def test_main_window_password_login_rejects_missing_session_token(monkeypatch) -> None:
    monkeypatch.setattr("app.ui.main_window.SettingsStore.load_settings", lambda self: UploaderSettings())
    monkeypatch.setattr("app.ui.main_window.SessionStore.load_session", lambda self: None)
    monkeypatch.setattr("app.ui.main_window.ApiClient.healthcheck", lambda self: {"status": "ok"})
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    monkeypatch.setattr(
        window.api_client,
        "login_with_password",
        lambda email, password: {
            "sessionToken": None,
            "user": {"id": "usr_login", "email": email, "nickname": "LoginTester"},
        },
    )

    window._handle_login("login@example.com", "password123")

    assert window.store.session is None
    assert window.stack.currentWidget() is window.login_view
    assert "没有返回会话令牌" in window.login_view.message_label.text()
    assert window._status_phase_label.text() == "阶段：登录响应无效"
    window.close()
    if QApplication.instance() is app and not QApplication.topLevelWidgets():
        app.quit()


def test_main_window_restores_session_only_after_server_validation(monkeypatch) -> None:
    monkeypatch.setattr("app.ui.main_window.SettingsStore.load_settings", lambda self: UploaderSettings())
    monkeypatch.setattr(
        "app.ui.main_window.SessionStore.load_session",
        lambda self: {
            "sessionToken": "ses_test",
            "userId": "usr_old",
            "email": "saved@example.com",
            "nickname": "SavedName",
        },
    )
    monkeypatch.setattr("app.ui.main_window.ApiClient.healthcheck", lambda self: {"status": "ok"})
    monkeypatch.setattr(
        "app.ui.main_window.ApiClient.auth_me",
        lambda self: {
            "authenticated": True,
            "user": {
                "id": "usr_live",
                "email": "live@example.com",
                "nickname": "LiveName",
            },
        },
    )
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    assert window.stack.currentWidget() is window.trace_import_view
    assert window.store.session is not None
    assert window.store.session.user_id == "usr_live"
    assert window.store.session.nickname == "LiveName"
    assert window._status_user_label.text() == "账号：LiveName"
    window.close()
    if QApplication.instance() is app and not QApplication.topLevelWidgets():
        app.quit()


def test_main_window_rechecks_duplicates_before_upload(monkeypatch) -> None:
    monkeypatch.setattr("app.ui.main_window.SettingsStore.load_settings", lambda self: UploaderSettings())
    monkeypatch.setattr("app.ui.main_window.SessionStore.load_session", lambda self: None)
    monkeypatch.setattr("app.ui.main_window.ApiClient.healthcheck", lambda self: {"status": "ok"})
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.store.session = AuthSession(
        session_token="ses_test",
        user_id="usr_test",
        email="test@example.com",
        nickname="Tester",
    )
    candidate = BattleCandidate(
        candidate_id="candidate-1",
        source_battle_index=1,
        source_log_path="sample.log",
        file_name="sample.log",
        boss_name="首领甲",
        dungeon_name="副本甲",
        duration_ms=12345,
        roster_names=["甲"],
        payload={
            "battle": {
                "battleFingerprint": "fp-existing",
                "bossKey": "eny_001",
                "parserVersion": "parser-v1",
                "rulesVersion": "rules-v1",
                "clearFlag": True,
                "officialTimerEndSeen": True,
                "timerWindowValid": True,
            }
        },
        selected=True,
    )
    window.store.candidates = [candidate]
    window.battle_upload_view.set_candidates(window.store.candidates)

    monkeypatch.setattr(
        "app.ui.main_window.build_battle_upload_payloads_from_log",
        lambda path: [candidate.payload],
    )
    upload_called = {"value": False}
    monkeypatch.setattr(
        window.api_client,
        "check_duplicate_battle",
        lambda payload: {"duplicate": True, "battleUrl": "/battle/existing-1"},
    )

    def fake_upload_battle(payload: dict) -> dict:
        upload_called["value"] = True
        raise ApiClientError("这条战斗记录已存在。", status_code=409)

    monkeypatch.setattr(window.api_client, "upload_battle", fake_upload_battle)

    window._handle_upload_candidates(["candidate-1"])

    assert upload_called["value"] is False
    assert candidate.duplicate is True
    assert candidate.duplicate_url == "/battle/existing-1"
    assert candidate.selected is False
    assert "已存在 1 场" in window.battle_upload_view.message_label.text()

    window.close()
    if QApplication.instance() is app and not QApplication.topLevelWidgets():
        app.quit()


def test_main_window_reparses_candidate_before_upload(monkeypatch) -> None:
    monkeypatch.setattr("app.ui.main_window.SettingsStore.load_settings", lambda self: UploaderSettings())
    monkeypatch.setattr("app.ui.main_window.SessionStore.load_session", lambda self: None)
    monkeypatch.setattr("app.ui.main_window.ApiClient.healthcheck", lambda self: {"status": "ok"})
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.store.session = AuthSession(
        session_token="ses_test",
        user_id="usr_test",
        email="test@example.com",
        nickname="Tester",
    )
    candidate = BattleCandidate(
        candidate_id="candidate-1",
        source_battle_index=1,
        source_log_path="sample.log",
        file_name="sample.log",
        boss_name="旧首领",
        dungeon_name="旧副本",
        duration_ms=1000,
        roster_names=["旧角色"],
        payload={
            "battle": {
                "battleFingerprint": "fp-stale",
                "bossKey": "eny_old",
                "parserVersion": "parser-old",
                "rulesVersion": "rules-old",
            }
        },
        selected=True,
    )
    refreshed_payload = {
        "battle": {
            "battleFingerprint": "fp-fresh",
            "bossKey": "eny_new",
            "bossName": "新首领",
            "dungeonName": "新副本",
            "durationMs": 2345,
            "parserVersion": "parser-new",
            "rulesVersion": "rules-new",
            "roster": [{"characterName": "新角色"}],
        }
    }
    window.store.candidates = [candidate]
    window.battle_upload_view.set_candidates(window.store.candidates)

    monkeypatch.setattr(
        "app.ui.main_window.build_battle_upload_payloads_from_log",
        lambda path: [refreshed_payload],
    )
    duplicate_requests: list[dict] = []
    upload_payloads: list[dict] = []
    monkeypatch.setattr(
        window.api_client,
        "check_duplicate_battle",
        lambda payload: duplicate_requests.append(payload) or {"duplicate": False},
    )
    monkeypatch.setattr(
        window.api_client,
        "upload_battle",
        lambda payload: upload_payloads.append(payload) or {"battleUrl": "/battle/new-1"},
    )

    window._handle_upload_candidates(["candidate-1"])

    assert duplicate_requests[0]["battleFingerprint"] == "fp-fresh"
    assert upload_payloads == [refreshed_payload]
    assert candidate.payload is refreshed_payload
    assert candidate.boss_name == "新首领"
    assert candidate.dungeon_name == "新副本"
    assert candidate.duration_ms == 2345
    assert candidate.roster_names == ["新角色"]

    window.close()
    if QApplication.instance() is app and not QApplication.topLevelWidgets():
        app.quit()


def test_parse_trace_worker_builds_candidates_without_network(monkeypatch, tmp_path) -> None:
    log_path = tmp_path / "sample.log"
    log_path.write_text("dummy", encoding="utf-8")
    payload = {
        "battle": {
            "bossName": "首领甲",
            "dungeonName": "副本甲",
            "durationMs": 12345,
            "roster": [{"characterName": "甲"}],
            "battleFingerprint": "fp-local-only",
            "bossKey": "eny_001",
            "parserVersion": "parser-v1",
            "rulesVersion": "rules-v1",
            "clearFlag": True,
            "officialTimerEndSeen": True,
            "timerWindowValid": True,
        }
    }

    def fake_build_payloads(path: str) -> list[dict]:
        assert path == str(log_path)
        return [payload]

    def forbidden_api_client(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("开始解析不应该访问网络")

    monkeypatch.setattr("app.ui.main_window.build_battle_upload_payloads_from_log", fake_build_payloads)
    monkeypatch.setattr("app.ui.main_window.ApiClient", forbidden_api_client)

    completed: list[BattleCandidate] = []
    failures: list[tuple[str, object]] = []
    worker = ParseTraceWorker(log_path=str(log_path))
    worker.completed.connect(lambda candidates: completed.extend(candidates))
    worker.failed.connect(lambda message, status_code: failures.append((message, status_code)))

    worker.run()

    assert failures == []
    assert len(completed) == 1
    candidate = completed[0]
    assert candidate.selected is True
    assert candidate.duplicate is False
    assert candidate.duplicate_url is None
    assert candidate.boss_name == "首领甲"
    assert candidate.payload is payload


def test_parse_trace_worker_leaves_uncleared_candidates_unselected(monkeypatch, tmp_path) -> None:
    log_path = tmp_path / "sample.log"
    log_path.write_text("dummy", encoding="utf-8")
    payloads = [
        {
            "battle": {
                "bossName": "首领甲",
                "dungeonName": "副本甲",
                "durationMs": 12345,
                "roster": [{"characterName": "甲"}],
                "battleFingerprint": "fp-clear",
                "bossKey": "eny_001",
                "parserVersion": "parser-v1",
                "rulesVersion": "rules-v1",
                "clearFlag": True,
                "timerEndSeen": True,
            }
        },
        {
            "battle": {
                "bossName": "首领乙",
                "dungeonName": "副本乙",
                "durationMs": 23456,
                "roster": [{"characterName": "乙"}],
                "battleFingerprint": "fp-uncleared",
                "bossKey": "eny_002",
                "parserVersion": "parser-v1",
                "rulesVersion": "rules-v1",
                "clearFlag": False,
            }
        },
        {
            "battle": {
                "bossName": "首领丙",
                "dungeonName": "副本丙",
                "durationMs": 34567,
                "roster": [{"characterName": "丙"}],
                "battleFingerprint": "fp-no-timer-end",
                "bossKey": "eny_003",
                "parserVersion": "parser-v1",
                "rulesVersion": "rules-v1",
                "clearFlag": True,
                "timerEndSeen": False,
                "officialTimerEndSeen": False,
            }
        },
    ]

    monkeypatch.setattr(
        "app.ui.main_window.build_battle_upload_payloads_from_log",
        lambda path: payloads,
    )

    completed: list[BattleCandidate] = []
    failures: list[tuple[str, object]] = []
    worker = ParseTraceWorker(log_path=str(log_path))
    worker.completed.connect(lambda candidates: completed.extend(candidates))
    worker.failed.connect(lambda message, status_code: failures.append((message, status_code)))

    worker.run()

    assert failures == []
    assert [candidate.selected for candidate in completed] == [True, False, False]


def test_main_window_allows_managed_archive_trace_without_integrity_proof(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("app.ui.main_window.SettingsStore.load_settings", lambda self: UploaderSettings())
    monkeypatch.setattr("app.ui.main_window.SessionStore.load_session", lambda self: None)
    monkeypatch.setattr("app.ui.main_window.ApiClient._new_client", lambda self, base_url: object())
    monkeypatch.setattr("app.ui.main_window.ApiClient.healthcheck", lambda self: {"status": "ok"})
    app = QApplication.instance() or QApplication([])
    window = MainWindow()

    log_path = tmp_path / "trace_20260625-120000.log"
    log_path.write_text("HP_V2 #1 hit=17\n", encoding="utf-8")
    log_path.with_suffix(log_path.suffix + ".status.json").write_text('{"type":"status"}', encoding="utf-8")

    window._load_trace_file(str(log_path))

    assert window.store.current_trace_integrity_verified is True
    assert window.trace_import_view.parse_button.isEnabled() is True
    assert window.store.current_integrity_label == "自动归档日志：可解析（无导出 proof）"

    started: list[str] = []
    monkeypatch.setattr(window, "_start_parse_trace", lambda path, *, context: started.append(f"{context}:{path}"))
    window._handle_parse_trace()

    assert started == [f"manual:{log_path}"]

    window.close()
    if QApplication.instance() is app and not QApplication.topLevelWidgets():
        app.quit()


def test_managed_log_refresh_waits_for_completion_marker(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("app.ui.main_window.SettingsStore.load_settings", lambda self: UploaderSettings())
    monkeypatch.setattr("app.ui.main_window.SessionStore.load_session", lambda self: None)
    monkeypatch.setattr("app.ui.main_window.ApiClient._new_client", lambda self, base_url: object())
    monkeypatch.setattr("app.ui.main_window.ApiClient.healthcheck", lambda self: {"status": "ok"})
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.store.session = AuthSession(
        session_token="ses_test",
        user_id="usr_test",
        email="test@example.com",
        nickname="Tester",
    )

    log_path = tmp_path / "trace_20260625-120000.log"
    log_path.write_text(
        "\n".join(
            [
                "[10:00:00.000] GAME_TIMER_START seq=1 source=PacketBattleState startMs=0 expireMs=0 official=1",
                '[10:00:01.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=5000 eHP=900000 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            ]
        ),
        encoding="utf-8",
    )
    old_time = time.time() - 2
    os.utime(log_path, (old_time, old_time))

    started: list[tuple[str, bool]] = []
    window._managed_log_path = str(log_path)
    window._reset_managed_log_scan_state()
    monkeypatch.setattr(
        window,
        "_start_parse_trace",
        lambda path, *, context, force_full=False: started.append((f"{context}:{path}", force_full)),
    )

    window._maybe_refresh_managed_log()
    assert started == []

    with log_path.open("a", encoding="utf-8") as handle:
        handle.write("\n[10:00:05.000] GAME_TIMER_END seq=1 source=PacketBattleState elapsedMs=5000 startMs=0 endMs=5000 expireMs=0 sane=1 official=1\n")
    os.utime(log_path, (old_time, old_time))

    window._maybe_refresh_managed_log()
    assert started == []

    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(
            "[10:00:05.100] OFFICIAL_TIMER_END "
            "source=SC_GAME_MECHANICS_SYNC_CHALLENGE_COMPLETE "
            "gameId=indie_battletower007_ex isPass=1 passTime=5100\n"
        )
    os.utime(log_path, (old_time, old_time))

    window._maybe_refresh_managed_log()
    assert started == [(f"managed:{log_path}", True)]
    assert window._managed_log_result_refresh_pending is True

    window.close()
    if QApplication.instance() is app and not QApplication.topLevelWidgets():
        app.quit()


def test_force_full_managed_refresh_does_not_stop_at_existing_fingerprint(monkeypatch) -> None:
    monkeypatch.setattr("app.ui.main_window.SettingsStore.load_settings", lambda self: UploaderSettings())
    monkeypatch.setattr("app.ui.main_window.SessionStore.load_session", lambda self: None)
    monkeypatch.setattr("app.ui.main_window.ApiClient._new_client", lambda self, base_url: object())
    monkeypatch.setattr("app.ui.main_window.ApiClient.healthcheck", lambda self: {"status": "ok"})
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.store.candidates = [
        BattleCandidate(
            candidate_id="candidate-old",
            source_battle_index=1,
            source_log_path="trace.log",
            file_name="trace.log",
            boss_name="旧首领",
            dungeon_name="旧副本",
            duration_ms=1000,
            roster_names=["甲"],
            payload={"battle": {"battleFingerprint": "fp-old"}},
            selected=True,
        )
    ]
    captured: dict[str, object] = {}

    class FakeSignal:
        def connect(self, callback) -> None:
            pass

    class FakeThread:
        def __init__(self, *args) -> None:
            self.started = FakeSignal()
            self.finished = FakeSignal()

        def start(self) -> None:
            pass

        def quit(self) -> None:
            pass

        def deleteLater(self) -> None:
            pass

    class FakeWorker:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)
            self.progress = FakeSignal()
            self.completed = FakeSignal()
            self.failed = FakeSignal()

        def moveToThread(self, thread) -> None:
            pass

        def run(self) -> None:
            pass

        def deleteLater(self) -> None:
            pass

    monkeypatch.setattr("app.ui.main_window.QThread", FakeThread)
    monkeypatch.setattr("app.ui.main_window.ParseTraceWorker", FakeWorker)

    window._managed_log_result_refresh_pending = True
    window._start_parse_trace("trace.log", context="managed", force_full=True)

    assert window._parse_incremental is False
    assert captured["known_fingerprints"] == set()
    assert captured["known_battle_index"] is None

    window._parse_worker = None
    window._parse_thread = None
    window.close()
    if QApplication.instance() is app and not QApplication.topLevelWidgets():
        app.quit()


def test_merge_managed_candidates_appends_incremental_results(monkeypatch) -> None:
    monkeypatch.setattr("app.ui.main_window.SettingsStore.load_settings", lambda self: UploaderSettings())
    monkeypatch.setattr("app.ui.main_window.SessionStore.load_session", lambda self: None)
    monkeypatch.setattr("app.ui.main_window.ApiClient._new_client", lambda self, base_url: object())
    monkeypatch.setattr("app.ui.main_window.ApiClient.healthcheck", lambda self: {"status": "ok"})
    app = QApplication.instance() or QApplication([])
    window = MainWindow()

    old_candidate = BattleCandidate(
        candidate_id="candidate-old",
        source_battle_index=1,
        source_log_path="trace.log",
        file_name="trace.log",
        boss_name="旧首领",
        dungeon_name="旧副本",
        duration_ms=1000,
        roster_names=["甲"],
        payload={"battle": {"battleFingerprint": "fp-old"}},
        selected=True,
        upload_url="/battle/old",
    )
    new_candidate = BattleCandidate(
        candidate_id="candidate-new",
        source_battle_index=1,
        source_log_path="trace.log",
        file_name="trace.log",
        boss_name="新首领",
        dungeon_name="新副本",
        duration_ms=2000,
        roster_names=["乙"],
        payload={"battle": {"battleFingerprint": "fp-new"}},
        selected=True,
    )
    window.store.candidates = [old_candidate]
    window._parse_incremental = True

    merged = window._merge_managed_candidates([new_candidate])

    assert merged == [old_candidate, new_candidate]
    assert merged[0].upload_url == "/battle/old"
    assert merged[1].source_battle_index == 2

    window.close()
    if QApplication.instance() is app and not QApplication.topLevelWidgets():
        app.quit()


def test_managed_upload_reuses_candidate_payload_without_reparse(monkeypatch) -> None:
    monkeypatch.setattr("app.ui.main_window.SettingsStore.load_settings", lambda self: UploaderSettings())
    monkeypatch.setattr("app.ui.main_window.SessionStore.load_session", lambda self: None)
    monkeypatch.setattr("app.ui.main_window.ApiClient._new_client", lambda self, base_url: object())
    monkeypatch.setattr("app.ui.main_window.ApiClient.healthcheck", lambda self: {"status": "ok"})
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window._managed_log_path = "trace.log"
    candidate = BattleCandidate(
        candidate_id="candidate-fp",
        source_battle_index=1,
        source_log_path="trace.log",
        file_name="trace.log",
        boss_name="首领",
        dungeon_name="副本",
        duration_ms=1000,
        roster_names=["甲"],
        payload={"battle": {"battleFingerprint": "fp-managed"}},
        selected=True,
    )

    monkeypatch.setattr(
        "app.ui.main_window.build_battle_upload_payloads_from_log",
        lambda path: (_ for _ in ()).throw(AssertionError("managed upload should not reparse")),
    )

    window._refresh_candidate_payload_before_upload(candidate, {})

    assert candidate.payload["battle"]["battleFingerprint"] == "fp-managed"

    window.close()
    if QApplication.instance() is app and not QApplication.topLevelWidgets():
        app.quit()


def test_managed_upload_reparses_when_late_result_is_pending(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("app.ui.main_window.SettingsStore.load_settings", lambda self: UploaderSettings())
    monkeypatch.setattr("app.ui.main_window.SessionStore.load_session", lambda self: None)
    monkeypatch.setattr("app.ui.main_window.ApiClient._new_client", lambda self, base_url: object())
    monkeypatch.setattr("app.ui.main_window.ApiClient.healthcheck", lambda self: {"status": "ok"})
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    log_path = tmp_path / "trace_20260718-120000.log"
    log_path.write_text(
        "[10:00:04.100] BATTLE_RESULT source=SC_SELF_SCENE_INFO "
        "dungeonId=indie_battletower007_ex isCalc=1 isPass=1\n",
        encoding="utf-8",
    )
    window._managed_log_path = str(log_path)
    window._managed_log_result_refresh_pending = True
    candidate = BattleCandidate(
        candidate_id="candidate-fp",
        source_battle_index=1,
        source_log_path=str(log_path),
        file_name=log_path.name,
        boss_name="死兽鸣吼·残酷",
        dungeon_name="战争回响",
        duration_ms=4000,
        roster_names=["甲"],
        payload={"battle": {"battleFingerprint": "fp-managed", "clearFlag": False}},
        selected=True,
    )
    refreshed_payload = {
        "battle": {
            "battleFingerprint": "fp-managed",
            "bossName": "死兽鸣吼·残酷",
            "dungeonName": "战争回响",
            "durationMs": 4000,
            "clearFlag": True,
            "roster": [],
        }
    }
    monkeypatch.setattr(
        "app.ui.main_window.build_battle_upload_payloads_from_log",
        lambda path, **kwargs: [refreshed_payload],
    )

    window._refresh_candidate_payload_before_upload(candidate, {})

    assert candidate.payload["battle"]["clearFlag"] is True

    window.close()
    if QApplication.instance() is app and not QApplication.topLevelWidgets():
        app.quit()


def test_main_window_still_blocks_plain_log_without_integrity_proof(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("app.ui.main_window.SettingsStore.load_settings", lambda self: UploaderSettings())
    monkeypatch.setattr("app.ui.main_window.SessionStore.load_session", lambda self: None)
    monkeypatch.setattr("app.ui.main_window.ApiClient._new_client", lambda self, base_url: object())
    monkeypatch.setattr("app.ui.main_window.ApiClient.healthcheck", lambda self: {"status": "ok"})
    app = QApplication.instance() or QApplication([])
    window = MainWindow()

    log_path = tmp_path / "manual.log"
    log_path.write_text("HP_V2 #1 hit=17\n", encoding="utf-8")

    window._load_trace_file(str(log_path))

    assert window.store.current_trace_integrity_verified is False
    assert window.trace_import_view.parse_button.isEnabled() is False
    assert window.store.current_integrity_label == "完整性：未通过"

    window.close()
    if QApplication.instance() is app and not QApplication.topLevelWidgets():
        app.quit()


def test_main_window_startup_log_workflow_runs_parse_and_upload(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("app.ui.main_window.SettingsStore.load_settings", lambda self: UploaderSettings())
    monkeypatch.setattr("app.ui.main_window.SessionStore.load_session", lambda self: None)
    monkeypatch.setattr("app.ui.main_window.ApiClient.healthcheck", lambda self: {"status": "ok"})
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.store.session = AuthSession(
        session_token="ses_test",
        user_id="usr_test",
        email="test@example.com",
        nickname="Tester",
    )

    log_path = tmp_path / "sample.log"
    log_path.write_text("dummy", encoding="utf-8")

    call_log: list[object] = []

    def fake_load_trace_file(path: str) -> None:
        call_log.append(("load", path))
        window.store.current_trace_integrity_verified = True

    def fake_handle_parse_trace() -> None:
        call_log.append("parse")
        window.store.candidates = [
            BattleCandidate(
                candidate_id="candidate-1",
                source_battle_index=1,
                source_log_path=str(log_path),
                file_name=log_path.name,
                boss_name="首领甲",
                dungeon_name="副本甲",
                duration_ms=12345,
                roster_names=["甲"],
                payload={"battle": {}},
                selected=True,
            )
        ]

    def fake_handle_upload_candidates(candidate_ids: list[str]) -> None:
        call_log.append(("upload", list(candidate_ids)))

    monkeypatch.setattr(window, "_load_trace_file", fake_load_trace_file)
    monkeypatch.setattr(window, "_handle_parse_trace", fake_handle_parse_trace)
    monkeypatch.setattr(window, "_handle_upload_candidates", fake_handle_upload_candidates)

    window.run_startup_log_workflow(str(log_path), auto_parse=True, auto_upload=True)
    app.processEvents()
    app.processEvents()

    assert call_log == [("load", str(log_path)), "parse", ("upload", ["candidate-1"])]

    window.close()
    if QApplication.instance() is app and not QApplication.topLevelWidgets():
        app.quit()
