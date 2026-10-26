from __future__ import annotations

import logging
import time
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from app.auth.session_store import SessionStore
from app.services.api_client import ApiClient, ApiClientError
from app.services.battle_payload_builder import build_battle_upload_payloads_from_log
from app.services.browser import open_url
from app.services.log_integrity import load_raw_log_integrity
from app.services.settings_store import SettingsStore
from app.services.updater import (
    UpdateError,
    UpdateManifest,
    download_update_package,
    fetch_update_manifest,
    launch_updater,
    should_check_for_updates,
)
from app.state.store import AuthSession, BattleCandidate, UploaderStore
from app.ui.assets import app_icon
from app.ui.navigation_sidebar import NavigationSidebar
from app.ui.theme import MAIN_WINDOW_STYLE
from app.ui.views.battle_upload_view import BattleUploadView
from app.ui.views.login_view import LoginView
from app.ui.views.settings_dialog import SettingsDialog
from app.ui.views.trace_import_view import TraceImportView


_MANAGED_PARSE_MARKERS = (
    b"OFFICIAL_TIMER_END",
    b"BATTLE_RESULT",
    b"OFFICIAL_TIMER_MISSING",
    b"BATTLE_TIMER_MISSING",
)

logger = logging.getLogger(__name__)


def _battle_payload_is_completed(payload: dict) -> bool:
    battle = payload.get("battle") if isinstance(payload, dict) else None
    if not isinstance(battle, dict):
        return False
    if battle.get("clearFlag") is not True:
        return False
    if battle.get("timerWindowValid") is False:
        return False
    has_timer_fields = "timerEndSeen" in battle or "officialTimerEndSeen" in battle
    if not has_timer_fields:
        return True
    return battle.get("timerEndSeen") is True or battle.get("officialTimerEndSeen") is True


class ParseTraceWorker(QObject):
    progress = Signal(str, object, object, str)
    completed = Signal(list)
    failed = Signal(str, object)

    def __init__(
        self,
        *,
        log_path: str,
        known_fingerprints: set[str] | None = None,
        known_battle_index: int | None = None,
        include_source_metadata: bool = False,
        fast_unverified: bool = False,
    ) -> None:
        super().__init__()
        self.log_path = log_path
        self.known_fingerprints = known_fingerprints or set()
        self.known_battle_index = known_battle_index
        self.include_source_metadata = include_source_metadata
        self.fast_unverified = fast_unverified

    @Slot()
    def run(self) -> None:
        try:
            candidates: list[BattleCandidate] = []

            self.progress.emit("正在本地拆分 trace 并解析 battle…", None, None, "正在解析 battle")
            if self.known_fingerprints or self.include_source_metadata:
                payloads = build_battle_upload_payloads_from_log(
                    self.log_path,
                    known_fingerprints=self.known_fingerprints or None,
                    known_battle_index=self.known_battle_index,
                    include_source_metadata=self.include_source_metadata,
                    fast_unverified=self.fast_unverified,
                )
            else:
                payloads = build_battle_upload_payloads_from_log(self.log_path)

            total = max(1, len(payloads))
            self.progress.emit(f"已拆出 {len(payloads)} 场 battle，正在整理候选列表…", 0, total, "正在整理候选")
            for index, payload in enumerate(payloads, start=1):
                source_battle_index = int(payload.pop("_sourceBattleIndex", index) or index)
                fingerprint = str(payload.get("battle", {}).get("battleFingerprint") or "")
                self.progress.emit(
                    f"正在整理第 {index}/{len(payloads)} 场 battle…",
                    index - 1,
                    len(payloads),
                    f"正在整理候选（{index}/{len(payloads)}）",
                )
                candidates.append(
                    BattleCandidate(
                        candidate_id=f"candidate-{fingerprint}" if fingerprint else f"candidate-{index}",
                        source_battle_index=source_battle_index,
                        source_log_path=self.log_path,
                        file_name=Path(self.log_path).name,
                        boss_name=str(payload["battle"]["bossName"]),
                        dungeon_name=str(payload["battle"]["dungeonName"]),
                        duration_ms=int(payload["battle"]["durationMs"]),
                        roster_names=[entry["characterName"] for entry in payload["battle"]["roster"]],
                        payload=payload,
                        selected=_battle_payload_is_completed(payload),
                    )
                )
            self.completed.emit(candidates)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"解析失败：{exc}", None)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("mainWindow")
        self.setWindowTitle("Endfield Logs 客户端")
        self.setWindowIcon(app_icon())
        self.resize(1260, 800)
        self.setMinimumSize(1100, 700)
        self.setStyleSheet(MAIN_WINDOW_STYLE)

        self.session_store = SessionStore()
        self.settings_store = SettingsStore()
        self.settings = self.settings_store.load_settings()
        self.api_client = ApiClient(base_url=self.settings.api_base_url)
        self.store = UploaderStore()
        self._parse_thread: QThread | None = None
        self._parse_worker: ParseTraceWorker | None = None
        self._parse_context = "manual"
        self._auto_upload_after_parse = False
        self._managed_log_path: str | None = None
        self._paused_managed_log_path: str | None = None
        self._managed_log_last_signature: tuple[int, int] | None = None
        self._managed_log_had_candidates = False
        self._managed_log_scan_offset = 0
        self._managed_log_scan_buffer = b""
        self._managed_log_completion_count = 0
        self._managed_log_last_requested_completion_count = 0
        self._managed_log_result_refresh_pending = False
        self._parse_incremental = False
        self._tray_icon: QSystemTrayIcon | None = None
        self._tray_hint_shown = False

        self.stack = QStackedWidget()
        self.login_view = LoginView()
        self.trace_import_view = TraceImportView()
        self.battle_upload_view = BattleUploadView()

        self.stack.addWidget(self.login_view)
        self.stack.addWidget(self.trace_import_view)
        self.stack.addWidget(self.battle_upload_view)
        self.page_stack = QStackedWidget()
        self.page_stack.setObjectName("workspacePages")
        self.page_stack.addWidget(self.stack)
        self.overlay_settings_host = QWidget()
        self.overlay_settings_host.setObjectName("overlaySettingsHost")
        self._overlay_settings_layout = QVBoxLayout(self.overlay_settings_host)
        self._overlay_settings_layout.setContentsMargins(0, 0, 0, 0)
        self.overlay_settings_view: QWidget | None = None
        self.page_stack.addWidget(self.overlay_settings_host)
        shell = QWidget()
        shell.setObjectName("desktopWorkspace")
        shell_layout = QHBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)
        self.sidebar = NavigationSidebar()
        self.sidebar.page_requested.connect(self.show_workspace_page)
        shell_layout.addWidget(self.sidebar)
        shell_layout.addWidget(self.page_stack, 1)
        self.setCentralWidget(shell)

        self._status_user_label = QLabel("账号：未登录")
        self._status_file_label = QLabel("文件：未选择")
        self._status_phase_label = QLabel("阶段：等待登录")
        self._status_server_label = QLabel("服务器：待检查")
        for label in [self._status_user_label, self._status_file_label, self._status_phase_label]:
            label.setObjectName("statusText")
        self._status_server_label.setObjectName("statusServer")
        status_bar = self.statusBar()
        status_bar.setSizeGripEnabled(False)
        status_bar.addWidget(self._status_user_label, 1)
        status_bar.addWidget(self._status_file_label, 1)
        status_bar.addPermanentWidget(self._status_phase_label, 1)
        status_bar.addPermanentWidget(self._status_server_label, 1)

        self._connect_signals()
        self._setup_tray_icon()
        self._managed_log_timer = QTimer(self)
        self._managed_log_timer.setInterval(5000)
        self._managed_log_timer.timeout.connect(self._maybe_refresh_managed_log)
        api_available = self._run_healthcheck(startup=True)
        self._restore_session(api_available=api_available)
        if should_check_for_updates():
            QTimer.singleShot(1500, self._check_for_updates)

    @staticmethod
    def _is_managed_archive_path(log_path: str) -> bool:
        path = Path(log_path)
        return (
            path.suffix.lower() == ".log"
            and path.name.startswith("trace_")
            and path.with_suffix(path.suffix + ".status.json").is_file()
        )

    @staticmethod
    def _is_managed_archive_trace(log_path: str, integrity: dict) -> bool:
        issues = [str(issue) for issue in (integrity.get("issues") or [])]
        if issues != ["missing integrity proof"]:
            return False
        return MainWindow._is_managed_archive_path(log_path)

    def run_managed_log_workflow(self, file_path: str) -> None:
        resolved_path = str(Path(file_path).expanduser())
        self._managed_log_path = resolved_path
        self._paused_managed_log_path = None
        self._managed_log_last_signature = None
        self._managed_log_had_candidates = False
        self._reset_managed_log_scan_state()
        self.store.current_trace_file_name = Path(resolved_path).name
        self.store.current_trace_path = resolved_path
        self.store.current_integrity_label = "自动归档日志：统一客户端正在持续写入"
        self.store.current_trace_integrity_verified = True
        self.trace_import_view.set_selected_file(Path(resolved_path).name, self.store.current_integrity_label)
        self.trace_import_view.set_parse_allowed(True)
        self.battle_upload_view.set_candidates([])
        self.battle_upload_view.set_message("正在监听自动归档日志，打完一场战斗后会自动出现记录。")
        self._remember_trace_directory(resolved_path)
        self._managed_log_timer.start()
        if self.store.session is None:
            self._show_login()
            self.login_view.set_message("请先登录，登录后会自动显示本机归档的战斗记录。")
        else:
            self._show_battle_upload()
            self._refresh_status_bar("正在监听自动归档日志")
        QTimer.singleShot(0, self._maybe_refresh_managed_log)

    def _reset_managed_log_scan_state(self) -> None:
        self._managed_log_scan_offset = 0
        self._managed_log_scan_buffer = b""
        self._managed_log_completion_count = 0
        self._managed_log_last_requested_completion_count = 0
        self._managed_log_result_refresh_pending = False

    def _scan_managed_log_completion_count(self, path: Path, *, file_size: int) -> int:
        if file_size < self._managed_log_scan_offset:
            self._reset_managed_log_scan_state()

        try:
            with path.open("rb") as handle:
                handle.seek(self._managed_log_scan_offset)
                chunk = handle.read()
        except OSError:
            return self._managed_log_completion_count

        self._managed_log_scan_offset += len(chunk)
        if not chunk:
            return self._managed_log_completion_count

        combined = self._managed_log_scan_buffer + chunk
        lines = combined.splitlines(keepends=True)
        if lines and not lines[-1].endswith((b"\n", b"\r")):
            self._managed_log_scan_buffer = lines.pop()
        else:
            self._managed_log_scan_buffer = b""

        for line in lines:
            if any(marker in line for marker in _MANAGED_PARSE_MARKERS):
                self._managed_log_completion_count += 1
                self._managed_log_result_refresh_pending = True
        return self._managed_log_completion_count

    def run_startup_log_workflow(self, file_path: str, *, auto_parse: bool = False, auto_upload: bool = False) -> None:
        def _run() -> None:
            if self.store.session is None:
                self._show_login()
                self.login_view.set_message("当前还未登录，无法直接导入启动日志。", error=True)
                return

            resolved_path = str(Path(file_path).expanduser())
            if not Path(resolved_path).exists():
                self.trace_import_view.set_message(f"启动日志不存在：{resolved_path}", error=True)
                self._refresh_status_bar("启动日志不存在")
                self._show_trace_import()
                return

            self._show_trace_import()
            self._load_trace_file(resolved_path)
            if not auto_parse or not self.store.current_trace_integrity_verified:
                return

            self._auto_upload_after_parse = bool(auto_upload)
            self._handle_parse_trace()
            if auto_upload and self.store.candidates:
                self._auto_upload_after_parse = False
                uploadable_candidate_ids = [
                    candidate.candidate_id
                    for candidate in self.store.candidates
                    if candidate.selected and not candidate.duplicate
                ]
                if uploadable_candidate_ids:
                    self._handle_upload_candidates(uploadable_candidate_ids)

        QTimer.singleShot(0, _run)

    def _connect_signals(self) -> None:
        self.login_view.login_requested.connect(self._handle_login)
        self.login_view.register_requested.connect(self._handle_register)
        self.login_view.send_register_code_requested.connect(self._handle_send_register_code)

        self.trace_import_view.choose_file_requested.connect(self._handle_choose_file)
        self.trace_import_view.parse_requested.connect(self._handle_parse_trace)
        self.trace_import_view.resume_monitoring_requested.connect(self._handle_resume_monitoring)
        self.trace_import_view.file_dropped.connect(self._handle_file_dropped)
        self.trace_import_view.logout_requested.connect(self._handle_logout_requested)

        self.battle_upload_view.upload_requested.connect(self._handle_upload_candidates)
        self.battle_upload_view.reupload_requested.connect(self._handle_reupload_candidate)
        self.battle_upload_view.retry_failed_requested.connect(self._handle_upload_candidates)
        self.battle_upload_view.open_record_requested.connect(self._handle_open_record)
        self.battle_upload_view.back_requested.connect(self._handle_back_from_upload)
        self.battle_upload_view.logout_requested.connect(self._handle_logout_requested)
        self.battle_upload_view.start_game_requested.connect(self._handle_start_game_requested)

    def _setup_tray_icon(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        tray = QSystemTrayIcon(app_icon(), self)
        tray.setToolTip("Endfield Logs 上传器")
        menu = QMenu(self)

        show_action = QAction("打开上传器", self)
        show_action.triggered.connect(self._restore_from_tray)
        menu.addAction(show_action)

        quit_action = QAction("退出上传器", self)
        quit_action.triggered.connect(QApplication.quit)
        menu.addAction(quit_action)

        tray.setContextMenu(menu)
        tray.activated.connect(self._handle_tray_activated)
        tray.show()
        self._tray_icon = tray

    def _handle_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._restore_from_tray()

    def _minimize_to_tray(self) -> None:
        if self._tray_icon is None:
            return
        self.hide()
        if not self._tray_hint_shown:
            self._tray_icon.showMessage(
                "Endfield Logs 上传器",
                "上传器已最小化到系统托盘。",
                QSystemTrayIcon.MessageIcon.Information,
                1800,
            )
            self._tray_hint_shown = True

    def _restore_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt override.
        if event.type() == QEvent.Type.WindowStateChange and self.isMinimized():
            QTimer.singleShot(0, self._minimize_to_tray)
        super().changeEvent(event)

    def _restore_session(self, *, api_available: bool) -> None:
        session_payload = self.session_store.load_session()
        if not session_payload:
            self._show_login()
            return

        session_token = str(session_payload.get("sessionToken") or "")
        if not session_token:
            self.session_store.clear()
            self._show_login()
            return

        self.api_client.set_session_token(session_token)
        if not api_available:
            self._show_login()
            self.login_view.set_message("当前无法连接 API，暂时不能验证本地登录状态。", error=True)
            return

        try:
            response = self.api_client.auth_me()
        except ApiClientError as exc:
            self.api_client.set_session_token(None)
            self._show_login()
            self.login_view.set_message(f"无法验证本地登录状态：{exc}", error=True)
            return

        if not response.get("authenticated"):
            self.session_store.clear()
            self.api_client.set_session_token(None)
            self._show_login()
            self.login_view.set_message("登录已失效，请重新登录。", error=True)
            return

        user = response.get("user") or {}
        self.store.session = AuthSession(
            session_token=session_token,
            user_id=str(user.get("id") or session_payload.get("userId") or ""),
            email=str(user.get("email") or session_payload.get("email") or ""),
            nickname=str(user.get("nickname") or session_payload.get("nickname") or ""),
        )
        self.session_store.save_session(
            {
                "sessionToken": session_token,
                "userId": self.store.session.user_id,
                "email": self.store.session.email,
                "nickname": self.store.session.nickname,
            }
        )
        self._show_trace_import()
        self.trace_import_view.set_message(f"已登录：{self.store.session.nickname}")
        self._refresh_status_bar("等待导入日志")
        if self._managed_log_path:
            self._show_battle_upload()
            self.battle_upload_view.set_message("已登录，正在监听自动归档日志。")
            QTimer.singleShot(0, self._maybe_refresh_managed_log)

    def _show_login(self) -> None:
        self.stack.setCurrentWidget(self.login_view)
        self._refresh_status_bar("等待登录")

    def _show_trace_import(self) -> None:
        self.trace_import_view.set_resume_monitoring_available(bool(self._paused_managed_log_path))
        self.stack.setCurrentWidget(self.trace_import_view)
        self._refresh_status_bar("等待导入日志")

    def _show_battle_upload(self) -> None:
        self.stack.setCurrentWidget(self.battle_upload_view)
        self._refresh_status_bar("等待选择并上传 battle")

    def _refresh_status_bar(self, phase: str) -> None:
        nickname = self.store.session.nickname if self.store.session else "未登录"
        file_name = self.store.current_trace_file_name or "未选择"
        self._status_user_label.setText(f"账号：{nickname}")
        self._status_file_label.setText(f"文件：{file_name}")
        self._status_phase_label.setText(f"阶段：{phase}")
        self.sidebar.set_monitoring(bool(self._managed_log_path))

    def set_overlay_settings_view(self, page: QWidget) -> None:
        """Install native settings without replacing the current battle workflow."""
        if page is self.overlay_settings_view:
            return
        if self.overlay_settings_view is not None:
            self._overlay_settings_layout.removeWidget(self.overlay_settings_view)
            self.overlay_settings_view.hide()
            self.overlay_settings_view.setParent(None)
        self.overlay_settings_view = page
        self._overlay_settings_layout.addWidget(page)

    def show_workspace_page(self, page: str) -> None:
        if page not in {"battle", "overlays"}:
            raise ValueError("Unsupported workspace page")
        self.page_stack.setCurrentWidget(self.stack if page == "battle" else self.overlay_settings_host)
        self.sidebar.set_current_page(page)
        if page == "overlays" and self.overlay_settings_view is not None:
            activated = getattr(self.overlay_settings_view, "on_activated", None)
            if callable(activated):
                activated()

    def _set_server_status(self, message: str, *, error: bool = False) -> None:
        if error:
            self._status_server_label.setStyleSheet(
                "QLabel#statusServer {"
                " background: #fff1f1;"
                " border: 1px solid #f2c6c6;"
                " color: #b53131;"
                "}"
            )
        else:
            self._status_server_label.setStyleSheet(
                "QLabel#statusServer {"
                " background: #eef7ff;"
                " border: 1px solid #c8def3;"
                " color: #24507d;"
                "}"
            )
        self._status_server_label.setText(f"服务器：{message}")

    def _run_healthcheck(self, *, startup: bool = False) -> bool:
        try:
            self.api_client.healthcheck()
        except Exception as exc:  # noqa: BLE001
            self._set_server_status("连接失败", error=True)
            current_widget = self.stack.currentWidget()
            message = f"当前无法连接 API：{exc}"
            if current_widget is self.login_view:
                self.login_view.set_message(message, error=True)
            elif current_widget is self.trace_import_view:
                self.trace_import_view.set_message(message, error=True)
            elif current_widget is self.battle_upload_view:
                self.battle_upload_view.set_message(message, error=True)
            if startup:
                self._refresh_status_bar("等待连接服务器")
            return False

        self._set_server_status("已连接")
        if startup:
            self._refresh_status_bar("等待登录" if self.store.session is None else "等待导入日志")
        return True

    def _check_for_updates(self) -> None:
        manifest = fetch_update_manifest(self.settings.api_base_url)
        if manifest is None:
            return

        notes = "\n".join(f"- {note}" for note in manifest.notes[:4])
        detail = f"发现新版上传器 {manifest.version}。"
        if notes:
            detail = f"{detail}\n\n更新内容：\n{notes}"
        detail = f"{detail}\n\n是否立即更新？上传器会自动关闭、安装并重启。"
        result = QMessageBox.question(
            self,
            "发现新版上传器",
            detail,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes if manifest.required else QMessageBox.StandardButton.No,
        )
        if result != QMessageBox.StandardButton.Yes:
            if manifest.required:
                QMessageBox.warning(self, "需要更新", "当前版本已不再支持上传，请更新后继续使用。")
            return
        self._download_and_launch_update(manifest)

    def _download_and_launch_update(self, manifest: UpdateManifest) -> None:
        progress_dialog = QProgressDialog("正在下载更新包…", "取消", 0, 100, self)
        progress_dialog.setWindowTitle("更新上传器")
        progress_dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        progress_dialog.setMinimumDuration(0)
        progress_dialog.setValue(0)

        def _on_progress(downloaded: int, total: int | None) -> None:
            if total:
                progress_dialog.setRange(0, 100)
                progress_dialog.setValue(min(100, int(downloaded * 100 / total)))
                progress_dialog.setLabelText(
                    f"正在下载更新包… {downloaded // 1024 // 1024} / {total // 1024 // 1024} MB"
                )
            else:
                progress_dialog.setRange(0, 0)
                progress_dialog.setLabelText(f"正在下载更新包… {downloaded // 1024 // 1024} MB")
            QApplication.processEvents()
            if progress_dialog.wasCanceled():
                raise UpdateError("更新已取消")

        self._set_busy(True)
        try:
            package_path = download_update_package(manifest, progress=_on_progress)
            progress_dialog.setValue(100)
            launch_updater(package_path)
        except UpdateError as exc:
            QMessageBox.warning(self, "更新失败", str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "更新失败", f"下载或启动更新器失败：{exc}")
            return
        finally:
            progress_dialog.close()
            self._set_busy(False)

        QMessageBox.information(self, "开始更新", "更新包已下载，上传器将关闭并由更新器完成替换。")
        QApplication.quit()

    def _set_busy(self, busy: bool) -> None:
        self.login_view.set_busy(busy)
        self.trace_import_view.set_busy(busy)
        self.battle_upload_view.set_busy(busy)

    @staticmethod
    def _is_valid_email(email: str) -> bool:
        return bool(email) and "@" in email and "." in email

    def _persist_authenticated_session(self, response: dict) -> str | None:
        user = response.get("user") or {}
        session_token = str(response.get("sessionToken") or "")
        if not session_token:
            raise ApiClientError("登录接口没有返回会话令牌，请重试；若仍然出现请提交 uploader.log。")
        session = AuthSession(
            session_token=session_token,
            user_id=str(user.get("id") or ""),
            email=str(user.get("email") or ""),
            nickname=str(user.get("nickname") or ""),
        )
        self.store.session = session
        self.api_client.set_session_token(session.session_token)
        try:
            self.session_store.save_session(
                {
                    "sessionToken": session.session_token,
                    "userId": session.user_id,
                    "email": session.email,
                    "nickname": session.nickname,
                }
            )
        except OSError as exc:
            # The server session is already valid. Keep it active in memory so a
            # local DPAPI/disk failure cannot strand the UI on the login screen,
            # but make the degraded state visible and preserve the traceback.
            logger.exception("failed to persist authenticated uploader session")
            return f"登录成功，但本地登录状态未能保存；本次可以继续使用，重启后需重新登录。错误：{exc}"
        return None

    def _show_authenticated_landing(self, *, persistence_warning: str | None = None) -> None:
        nickname = self.store.session.nickname if self.store.session else ""
        if self._managed_log_path:
            self._show_battle_upload()
            message = f"已登录：{nickname}，正在监听自动归档日志。"
            if persistence_warning:
                message = f"{message} {persistence_warning}"
            self.battle_upload_view.set_message(message)
            QTimer.singleShot(0, self._maybe_refresh_managed_log)
            return

        self._show_trace_import()
        message = f"已登录：{nickname}"
        if persistence_warning:
            message = f"{message}。{persistence_warning}"
        self.trace_import_view.set_message(message)

    def _handle_login(self, email: str, password: str) -> None:
        if not self._is_valid_email(email):
            self.login_view.set_message("邮箱格式不正确。", error=True)
            return
        if not password:
            self.login_view.set_message("请输入密码。", error=True)
            return

        self._set_busy(True)
        try:
            self._refresh_status_bar("正在登录")
            response = self.api_client.login_with_password(email, password)
        except ApiClientError as exc:
            self.login_view.set_message(str(exc), error=True)
            return
        finally:
            self._set_busy(False)

        try:
            persistence_warning = self._persist_authenticated_session(response)
        except ApiClientError as exc:
            logger.exception("invalid uploader login response")
            self.login_view.set_message(str(exc), error=True)
            self._refresh_status_bar("登录响应无效")
            return

        self.login_view.clear_profile_setup()
        self._show_authenticated_landing(persistence_warning=persistence_warning)

    def _handle_send_register_code(self, email: str) -> None:
        if not self._is_valid_email(email):
            self.login_view.set_message("邮箱格式不正确。", error=True)
            return

        self._set_busy(True)
        try:
            self._refresh_status_bar("正在检查邮箱")
            email_check = self.api_client.check_email(email)
            if email_check.get("available") is False:
                self.login_view.switch_to_login_with_email(email)
                self.login_view.set_message("这个邮箱已经注册过了，请直接输入密码登录。")
                self._refresh_status_bar("等待密码登录")
                return
            self._refresh_status_bar("正在发送邮箱验证码")
            response = self.api_client.send_code(email, purpose="uploader_login")
        except ApiClientError as exc:
            self.login_view.set_message(str(exc), error=True)
            return
        finally:
            self._set_busy(False)

        cooldown_seconds = int(response.get("cooldownSeconds") or 60)
        self.login_view.start_send_code_cooldown(cooldown_seconds)
        debug_code = str(response.get("debugCode") or "")
        if debug_code:
            self.login_view.set_message(f"验证码已发送。调试验证码：{debug_code}")
        else:
            self.login_view.set_message("验证码已发送，请查看邮箱。")
        self._refresh_status_bar("等待输入邮箱验证码")

    def _handle_register(self, email: str, password: str, nickname: str, code: str) -> None:
        if not self._is_valid_email(email):
            self.login_view.set_message("邮箱格式不正确。", error=True)
            return
        if len(code) != 6 or not code.isdigit():
            self.login_view.set_message("请输入 6 位邮箱验证码。", error=True)
            return
        if len(password) < 8:
            self.login_view.set_message("密码至少需要 8 位。", error=True)
            return
        if len(nickname) < 2:
            self.login_view.set_message("昵称至少 2 个字符。", error=True)
            return

        self._set_busy(True)
        try:
            self._refresh_status_bar("正在创建账号")
            response = self.api_client.register_with_password(email, password, nickname, code)
        except ApiClientError as exc:
            self.login_view.set_message(str(exc), error=True)
            return
        finally:
            self._set_busy(False)

        try:
            persistence_warning = self._persist_authenticated_session(response)
        except ApiClientError as exc:
            logger.exception("invalid uploader registration response")
            self.login_view.set_message(str(exc), error=True)
            self._refresh_status_bar("注册响应无效")
            return

        self.login_view.clear_profile_setup()
        self._show_authenticated_landing(persistence_warning=persistence_warning)

    def _handle_back_from_upload(self) -> None:
        # 从上传页返回选择日志：必须彻底暂停自动归档监听，否则 5 秒定时器会在
        # 用户手动挑选日志时把视图抢回上传页并触发解析（2026-07-05 用户踩中）。
        # 暂停的路径记入 _paused_managed_log_path，导入页显示“恢复自动监听”按钮。
        self._pause_managed_monitoring()
        self._show_trace_import()

    def _pause_managed_monitoring(self) -> None:
        if self._managed_log_path:
            self._paused_managed_log_path = self._managed_log_path
            self._managed_log_path = None
            self._managed_log_timer.stop()

    def _handle_resume_monitoring(self) -> None:
        paused_path = self._paused_managed_log_path
        if not paused_path:
            return
        self._paused_managed_log_path = None
        self.run_managed_log_workflow(paused_path)

    def _handle_choose_file(self) -> None:
        initial_dir = Path(self.settings.last_log_dir) if self.settings.last_log_dir else Path.cwd()
        if not initial_dir.exists():
            initial_dir = Path.cwd()
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择战斗日志",
            str(initial_dir),
            "Log Files (*.log);;All Files (*)",
        )
        if not file_path:
            return
        self._load_trace_file(file_path)

    def _handle_file_dropped(self, file_path: str) -> None:
        self._load_trace_file(file_path)

    def _load_trace_file(self, file_path: str) -> None:
        if self.store.candidates:
            result = QMessageBox.question(
                self,
                "重新导入日志",
                "当前解析结果会被新的日志替换，是否继续？",
            )
            if result != QMessageBox.StandardButton.Yes:
                return

        # 手动导入必须暂停自动归档监听：否则 5 秒定时器会立刻把当前文件
        # 抢占回归档日志并触发解析（2026-07-05 用户重传旧日志时踩中）。
        # 通过导入页的“恢复自动监听”按钮可恢复。
        self._pause_managed_monitoring()

        integrity = load_raw_log_integrity(file_path)
        archive_trace_allowed = self._is_managed_archive_trace(file_path, integrity)
        parse_allowed = bool(integrity.get("verified")) or archive_trace_allowed
        if integrity.get("verified"):
            integrity_label = f"完整性：已通过（{integrity.get('proof_source') or 'unknown'}）"
            message = "日志已读取，可以开始解析。"
        elif archive_trace_allowed:
            integrity_label = "自动归档日志：可解析（无导出 proof）"
            message = "已识别为统一客户端自动归档日志，可以开始解析。"
        else:
            reasons = "；".join(integrity.get("issues") or ["完整性未通过"])
            integrity_label = "完整性：未通过"
            message = reasons

        self.store.current_trace_file_name = Path(file_path).name
        self.store.current_trace_path = file_path
        self.store.current_integrity_label = integrity_label
        self.store.current_trace_integrity_verified = parse_allowed
        self.store.candidates = []
        self.store.last_uploaded_battle_urls = []
        self._remember_trace_directory(file_path)

        self.trace_import_view.set_selected_file(Path(file_path).name, integrity_label)
        self.trace_import_view.set_parse_allowed(parse_allowed)
        self.trace_import_view.set_message(message, error=not parse_allowed)
        self.trace_import_view.clear_progress()
        self.battle_upload_view.set_candidates([])
        self.battle_upload_view.clear_progress()
        self.battle_upload_view.set_message("")
        # 关键：手动加载后一定回到导入页——只有导入页有“开始解析”按钮，
        # 否则用户会被卡在没有解析入口的上传页（2026-07-05 用户报“也没开始解析”）。
        self._show_trace_import()
        self._refresh_status_bar("日志已加载，等待解析")

    def _remember_trace_directory(self, file_path: str) -> None:
        log_dir = str(Path(file_path).expanduser().resolve().parent)
        if self.settings.last_log_dir == log_dir:
            return
        self.settings.last_log_dir = log_dir
        try:
            self.settings_store.save_settings(self.settings)
        except OSError:
            pass

    def _handle_parse_trace(self) -> None:
        log_path = self.store.current_trace_path
        if not log_path:
            self.trace_import_view.set_message("请先选择日志文件。", error=True)
            return
        if self._parse_thread is not None:
            self.trace_import_view.set_message("正在解析当前日志，请稍等。")
            return

        integrity = load_raw_log_integrity(log_path)
        if not integrity.get("verified") and not self._is_managed_archive_trace(log_path, integrity):
            reasons = "；".join(integrity.get("issues") or ["日志完整性校验未通过"])
            self.trace_import_view.set_message(reasons, error=True)
            return

        self._start_parse_trace(log_path, context="manual")

    def _start_parse_trace(self, log_path: str, *, context: str, force_full: bool = False) -> None:
        if self._parse_thread is not None:
            if context == "managed":
                self.battle_upload_view.set_message("正在解析当前归档日志，请稍等。")
            else:
                self.trace_import_view.set_message("正在解析当前日志，请稍等。")
            return
        known_fingerprints: set[str] = set()
        known_battle_index: int | None = None
        if context == "managed":
            known_fingerprints = {
                self._payload_fingerprint(candidate.payload)
                for candidate in self.store.candidates
                if self._payload_fingerprint(candidate.payload)
            }
            known_indexes = [candidate.source_battle_index for candidate in self.store.candidates]
            known_battle_index = max(known_indexes) if known_indexes else None
        fast_unverified = context == "managed" or (
            context == "manual" and self._is_managed_archive_path(log_path)
        )
        force_result_refresh = context == "managed" and (force_full or self._managed_log_result_refresh_pending)
        self._parse_incremental = context == "managed" and bool(known_fingerprints) and not force_result_refresh
        parser_known_fingerprints = known_fingerprints if self._parse_incremental else set()
        if context == "managed":
            self._managed_log_result_refresh_pending = False
        self._parse_context = context
        self._set_busy(True)
        if context == "managed":
            self._show_battle_upload()
            self._refresh_status_bar("正在自动解析归档日志")
            if self._parse_incremental:
                self.battle_upload_view.set_progress("正在解析新增战斗记录…")
            else:
                self.battle_upload_view.set_progress("正在自动解析归档日志…")
        else:
            self._refresh_status_bar("正在本地解析")
            self.trace_import_view.set_progress("正在本地解析 battle…")
        self._parse_thread = QThread(self)
        self._parse_worker = ParseTraceWorker(
            log_path=log_path,
            known_fingerprints=parser_known_fingerprints,
            known_battle_index=known_battle_index if self._parse_incremental else None,
            include_source_metadata=True,
            fast_unverified=fast_unverified,
        )
        self._parse_worker.moveToThread(self._parse_thread)
        self._parse_thread.started.connect(self._parse_worker.run)
        self._parse_worker.progress.connect(self._handle_parse_progress)
        self._parse_worker.completed.connect(self._handle_parse_completed)
        self._parse_worker.failed.connect(self._handle_parse_failed)
        self._parse_worker.completed.connect(self._parse_thread.quit)
        self._parse_worker.failed.connect(self._parse_thread.quit)
        self._parse_thread.finished.connect(self._cleanup_parse_worker)
        self._parse_thread.start()

    @Slot(str, object, object, str)
    def _handle_parse_progress(
        self,
        message: str,
        current: object = None,
        total: object = None,
        status_message: str = "",
    ) -> None:
        self._refresh_status_bar(status_message or message)
        current_value = int(current) if isinstance(current, int) else None
        total_value = int(total) if isinstance(total, int) else None
        if self._parse_context == "managed":
            self.battle_upload_view.set_progress(message, current=current_value, total=total_value)
        else:
            self.trace_import_view.set_progress(message, current=current_value, total=total_value)

    @Slot(list)
    def _handle_parse_completed(self, candidates: list[BattleCandidate]) -> None:
        context = self._parse_context
        self._set_busy(False)
        if context == "managed":
            self.battle_upload_view.clear_progress()
        else:
            self.trace_import_view.clear_progress()
        if not candidates:
            if context == "managed" and self._parse_incremental and self.store.candidates:
                self.battle_upload_view.set_message("自动更新完成，暂时没有新增可显示的战斗记录。")
                self._refresh_status_bar(f"监听中，已显示 {len(self.store.candidates)} 场")
                return
            if context == "managed":
                self.battle_upload_view.set_message("正在监听自动归档日志，暂时还没有可显示的战斗记录。")
            else:
                self.trace_import_view.set_message("未在日志中识别到可上传的 battle。", error=True)
            self._refresh_status_bar("等待战斗记录" if context == "managed" else "解析失败")
            return

        if context == "managed":
            candidates = self._merge_managed_candidates(candidates)
        self.store.candidates = candidates
        self.store.last_uploaded_battle_urls = []
        self.battle_upload_view.set_candidates(
            self.store.candidates,
            reset_uncleared_filter=context != "managed" or not self._managed_log_had_candidates,
        )
        if context == "managed":
            self._managed_log_had_candidates = True
        duplicate_count = sum(1 for candidate in candidates if candidate.duplicate)
        uploadable_count = sum(
            1
            for candidate in candidates
            if not candidate.duplicate and not candidate.upload_url
        )
        uncleared_count = sum(
            1
            for candidate in candidates
            if not _battle_payload_is_completed(candidate.payload)
        )
        uncleared_hint = (
            f" 其中 {uncleared_count} 场未完成，已显示但默认未勾选。"
            if uncleared_count
            else ""
        )
        if uploadable_count and duplicate_count:
            self.battle_upload_view.set_message(
                f"{'自动更新完成' if context == 'managed' else '解析完成'}，共拆出 {len(candidates)} 场 battle，"
                f"可上传 {uploadable_count} 场，重复 {duplicate_count} 场。"
                f"{uncleared_hint}"
            )
        elif uploadable_count:
            self.battle_upload_view.set_message(
                f"{'自动更新完成' if context == 'managed' else '解析完成'}，共拆出 {len(candidates)} 场 battle，"
                "请勾选并上传。上传时会重新检查重复记录。"
                f"{uncleared_hint}"
            )
        else:
            self.battle_upload_view.set_message(
                f"{'自动更新完成' if context == 'managed' else '解析完成'}，共拆出 {len(candidates)} 场 battle，"
                "但都已存在，可直接打开已有记录。"
                f"{uncleared_hint}"
            )
        self._show_battle_upload()
        self._refresh_status_bar(f"解析完成，共 {len(candidates)} 场")
        if self._auto_upload_after_parse:
            self._auto_upload_after_parse = False
            uploadable_candidate_ids = [
                candidate.candidate_id
                for candidate in self.store.candidates
                if candidate.selected and not candidate.duplicate and not candidate.upload_url
            ]
            if uploadable_candidate_ids:
                QTimer.singleShot(0, lambda: self._handle_upload_candidates(uploadable_candidate_ids))

    @Slot(str, object)
    def _handle_parse_failed(self, message: str, status_code: object = None) -> None:
        context = self._parse_context
        self._auto_upload_after_parse = False
        self._set_busy(False)
        if context == "managed":
            self.battle_upload_view.clear_progress()
        else:
            self.trace_import_view.clear_progress()
        if status_code == 401:
            self._handle_session_invalid()
            return
        if context == "managed":
            if "未在日志中识别" in message or "未在日志中识别到可用 battle" in message:
                self.battle_upload_view.set_message("正在监听自动归档日志，暂时还没有可显示的战斗记录。")
                self._refresh_status_bar("等待战斗记录")
                return
            self.battle_upload_view.set_message(message, error=True)
            return
        self.trace_import_view.set_message(message, error=True)

    @Slot()
    def _cleanup_parse_worker(self) -> None:
        if self._parse_worker is not None:
            self._parse_worker.deleteLater()
        if self._parse_thread is not None:
            self._parse_thread.deleteLater()
        self._parse_worker = None
        self._parse_thread = None

    @staticmethod
    def _payload_fingerprint(payload: dict) -> str:
        battle = payload.get("battle") if isinstance(payload, dict) else None
        if not isinstance(battle, dict):
            return ""
        return str(battle.get("battleFingerprint") or "")

    @staticmethod
    def _apply_payload_to_candidate(candidate: BattleCandidate, payload: dict) -> None:
        battle = payload.get("battle") if isinstance(payload, dict) else None
        if not isinstance(battle, dict):
            raise ValueError("上传前重新解析失败：payload 缺少 battle 信息。")
        roster = battle.get("roster") if isinstance(battle.get("roster"), list) else []
        candidate.payload = payload
        candidate.boss_name = str(battle.get("bossName") or candidate.boss_name)
        candidate.dungeon_name = str(battle.get("dungeonName") or candidate.dungeon_name)
        candidate.duration_ms = int(battle.get("durationMs") or candidate.duration_ms)
        candidate.roster_names = [str(entry.get("characterName") or "") for entry in roster if isinstance(entry, dict)]

    def _merge_managed_candidates(self, candidates: list[BattleCandidate]) -> list[BattleCandidate]:
        previous_by_fingerprint = {
            self._payload_fingerprint(candidate.payload): candidate
            for candidate in self.store.candidates
            if self._payload_fingerprint(candidate.payload)
        }
        if self._parse_incremental:
            merged = list(self.store.candidates)
            next_source_index = max(
                (candidate.source_battle_index for candidate in merged),
                default=0,
            ) + 1
            seen = {
                self._payload_fingerprint(candidate.payload)
                for candidate in merged
                if self._payload_fingerprint(candidate.payload)
            }
            for candidate in candidates:
                fingerprint = self._payload_fingerprint(candidate.payload)
                if fingerprint and fingerprint in seen:
                    continue
                candidate.source_battle_index = next_source_index
                next_source_index += 1
                merged.append(candidate)
                if fingerprint:
                    seen.add(fingerprint)
            return merged

        for candidate in candidates:
            previous = previous_by_fingerprint.get(self._payload_fingerprint(candidate.payload))
            if previous is None:
                continue
            candidate.selected = previous.selected
            candidate.duplicate = previous.duplicate
            candidate.duplicate_url = previous.duplicate_url
            candidate.upload_url = previous.upload_url
            candidate.upload_error = previous.upload_error
        return candidates

    def _maybe_refresh_managed_log(self) -> None:
        if not self._managed_log_path:
            return
        if self.store.session is None:
            self._show_login()
            self.login_view.set_message("请先登录，登录后会自动显示本机归档的战斗记录。")
            return
        if self._parse_thread is not None:
            return
        path = Path(self._managed_log_path)
        self.store.current_trace_file_name = path.name
        self.store.current_trace_path = str(path)
        if not path.exists():
            self.battle_upload_view.set_message("正在等待自动归档日志创建。")
            self._show_battle_upload()
            self._refresh_status_bar("等待自动归档日志")
            return
        try:
            stat = path.stat()
        except OSError as exc:
            self.battle_upload_view.set_message(f"读取自动归档日志失败：{exc}", error=True)
            return
        if stat.st_size <= 0:
            self.battle_upload_view.set_message("正在监听自动归档日志，暂时还没有写入内容。")
            self._show_battle_upload()
            self._refresh_status_bar("等待战斗记录")
            return
        if time.time() - stat.st_mtime < 0.75:
            return
        signature = (int(stat.st_size), int(stat.st_mtime_ns))
        completion_count = self._scan_managed_log_completion_count(path, file_size=int(stat.st_size))
        needs_initial_backfill = (
            completion_count > 0
            and not self.store.candidates
            and self._managed_log_last_signature is None
        )
        if completion_count <= self._managed_log_last_requested_completion_count and not needs_initial_backfill:
            return
        if signature == self._managed_log_last_signature and not needs_initial_backfill:
            return
        self._managed_log_last_signature = signature
        self._managed_log_last_requested_completion_count = completion_count
        self._start_parse_trace(
            str(path),
            context="managed",
            force_full=self._managed_log_result_refresh_pending,
        )

    def _refresh_candidate_payload_before_upload(
        self,
        candidate: BattleCandidate,
        payload_cache: dict[str, list[dict]],
    ) -> None:
        source_log_path = candidate.source_log_path
        if self._managed_log_path and str(Path(source_log_path)) == str(Path(self._managed_log_path)):
            managed_path = Path(source_log_path)
            try:
                managed_size = int(managed_path.stat().st_size)
            except OSError:
                managed_size = 0
            if managed_size > 0:
                self._scan_managed_log_completion_count(managed_path, file_size=managed_size)
            if self._payload_fingerprint(candidate.payload) and not self._managed_log_result_refresh_pending:
                return
        if source_log_path not in payload_cache:
            if self._is_managed_archive_path(source_log_path):
                payload_cache[source_log_path] = build_battle_upload_payloads_from_log(
                    source_log_path,
                    fast_unverified=True,
                )
            else:
                payload_cache[source_log_path] = build_battle_upload_payloads_from_log(source_log_path)

        payloads = payload_cache[source_log_path]
        old_fingerprint = self._payload_fingerprint(candidate.payload)
        refreshed_payload = next(
            (payload for payload in payloads if self._payload_fingerprint(payload) == old_fingerprint),
            None,
        )
        if refreshed_payload is None and 1 <= candidate.source_battle_index <= len(payloads):
            refreshed_payload = payloads[candidate.source_battle_index - 1]
        if refreshed_payload is None:
            raise ValueError(
                f"上传前重新解析失败：找不到第 {candidate.source_battle_index} 场 {candidate.boss_name}。"
            )
        self._apply_payload_to_candidate(candidate, refreshed_payload)

    def _handle_reupload_candidate(self, candidate_id: str) -> None:
        """重传已存在的战斗：服务器对同账号同指纹会复用同一条记录并覆盖，
        用于新版客户端补全数据字段（施法序列/武器装备详情等）。"""
        self._handle_upload_candidates([candidate_id], force_reupload=True)

    def _handle_upload_candidates(self, candidate_ids: list, force_reupload: bool = False) -> None:
        if self.store.session is None:
            self._show_login()
            self.login_view.set_message("请先登录后再上传战斗记录。", error=True)
            return
        if not candidate_ids:
            self.battle_upload_view.set_message("请先勾选要上传的 battle。", error=True)
            return

        requested_candidate_ids = set(candidate_ids)
        requested_candidates = [
            candidate
            for candidate in self.store.candidates
            if candidate.candidate_id in requested_candidate_ids
            and (force_reupload or not candidate.duplicate)
        ]
        if not requested_candidates:
            self.battle_upload_view.set_message("当前没有可上传的 battle。", error=True)
            return

        self._set_busy(True)
        uploaded_urls: list[str] = []
        blocked_duplicates: list[str] = []
        failed_messages: list[str] = []
        requested_count = len(requested_candidates)
        refreshed_payloads_by_log: dict[str, list[dict]] = {}
        try:
            self._refresh_status_bar(f"正在上传（0/{requested_count}）")
            self.battle_upload_view.set_progress("准备上传 battle…", current=0, total=requested_count)
            self.battle_upload_view.set_message("")
            for index, candidate in enumerate(requested_candidates, start=1):
                candidate.upload_error = None
                self.battle_upload_view.set_progress(
                    f"正在上传第 {index}/{requested_count} 场：{candidate.boss_name}",
                    current=index - 1,
                    total=requested_count,
                )
                self._refresh_status_bar(f"正在上传（{index}/{requested_count}）")
                QApplication.processEvents()

                try:
                    self._refresh_candidate_payload_before_upload(candidate, refreshed_payloads_by_log)
                except Exception as exc:  # noqa: BLE001
                    candidate.upload_error = str(exc)
                    candidate.selected = True
                    failed_messages.append(f"第 {candidate.source_battle_index} 场 {candidate.boss_name}：{exc}")
                    continue

                duplicate_request = {
                    "battleFingerprint": candidate.payload["battle"]["battleFingerprint"],
                    "bossKey": candidate.payload["battle"]["bossKey"],
                    "parserVersion": candidate.payload["battle"]["parserVersion"],
                    "rulesVersion": candidate.payload["battle"]["rulesVersion"],
                }
                try:
                    duplicate_result = self.api_client.check_duplicate_battle(duplicate_request)
                except ApiClientError as exc:
                    if exc.status_code == 401:
                        self._handle_session_invalid()
                        return
                    candidate.upload_error = str(exc)
                    candidate.selected = True
                    failed_messages.append(f"第 {candidate.source_battle_index} 场 {candidate.boss_name}：{exc}")
                    continue
                if duplicate_result.get("duplicate") and not force_reupload:
                    candidate.duplicate = True
                    candidate.selected = False
                    candidate.duplicate_url = str(duplicate_result.get("battleUrl") or "") or candidate.duplicate_url
                    blocked_duplicates.append(f"第 {candidate.source_battle_index} 场 {candidate.boss_name}")
                    continue

                try:
                    result = self.api_client.upload_battle(candidate.payload)
                except ApiClientError as exc:
                    if exc.status_code == 401:
                        self._handle_session_invalid()
                        return
                    if exc.status_code == 409:
                        candidate.duplicate = True
                        candidate.selected = False
                        candidate.upload_error = None
                        try:
                            duplicate_result = self.api_client.check_duplicate_battle(duplicate_request)
                        except ApiClientError:
                            duplicate_result = {}
                        candidate.duplicate_url = str(duplicate_result.get("battleUrl") or "") or candidate.duplicate_url
                        blocked_duplicates.append(f"第 {candidate.source_battle_index} 场 {candidate.boss_name}")
                        continue
                    candidate.upload_error = str(exc)
                    candidate.selected = True
                    failed_messages.append(f"第 {candidate.source_battle_index} 场 {candidate.boss_name}：{exc}")
                    continue
                candidate.upload_url = str(result.get("battleUrl") or "")
                candidate.upload_error = None
                candidate.selected = False
                # 重传成功后清掉"已存在"标记，状态显示为"上传成功"
                candidate.duplicate = False
                uploaded_urls.append(candidate.upload_url)
        finally:
            self._set_busy(False)
            self.battle_upload_view.clear_progress()

        self.store.last_uploaded_battle_urls = uploaded_urls
        self.battle_upload_view.set_candidates(self.store.candidates)

        if uploaded_urls and len(uploaded_urls) == 1 and not failed_messages and requested_count == 1:
            self.battle_upload_view.set_message("上传成功，已为你打开 battle 详情页。")
            self._refresh_status_bar("上传完成")
            if not open_url(self._web_url(uploaded_urls[0])):
                self.battle_upload_view.set_message(
                    f"上传成功，但浏览器打开失败，请手动访问：{self._web_url(uploaded_urls[0])}",
                    error=True,
                )
            return

        if uploaded_urls and not failed_messages and not blocked_duplicates:
            self.battle_upload_view.set_message(
                f"上传完成：成功 {len(uploaded_urls)} 场。你可以在列表中选中记录后直接打开。"
            )
            self._refresh_status_bar(f"上传完成，成功 {len(uploaded_urls)} 场")
            return

        if blocked_duplicates or failed_messages:
            status_bits: list[str] = []
            if uploaded_urls:
                status_bits.append(f"成功 {len(uploaded_urls)} 场")
            if blocked_duplicates:
                status_bits.append(f"已存在 {len(blocked_duplicates)} 场")
            if failed_messages:
                status_bits.append(f"失败 {len(failed_messages)} 场")
            detail_parts: list[str] = []
            if blocked_duplicates:
                detail_parts.append("重复项已自动标记为已存在")
            if failed_messages:
                detail_parts.append(f"失败原因：{'；'.join(failed_messages)}")
            self.battle_upload_view.set_message(
                f"上传完成：{'，'.join(status_bits)}。"
                + (" ".join(detail_parts) if detail_parts else ""),
                error=bool(failed_messages),
            )
            self._refresh_status_bar(
                f"上传完成，成功 {len(uploaded_urls)} 场 / 已存在 {len(blocked_duplicates)} 场 / 失败 {len(failed_messages)} 场"
            )
            return

        self.battle_upload_view.set_message("没有 battle 被上传。", error=True)
        self._refresh_status_bar("上传未执行")

    def _handle_open_record(self, battle_url: str) -> None:
        target_url = self._web_url(battle_url)
        if not open_url(target_url):
            self.battle_upload_view.set_message(f"浏览器打开失败，请手动访问：{target_url}", error=True)

    def _handle_open_settings(self) -> None:
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec() != SettingsDialog.DialogCode.Accepted:
            return

        self.settings = dialog.current_settings()
        self.settings_store.save_settings(self.settings)
        self.api_client.update_base_url(self.settings.api_base_url)
        if self.store.session is not None:
            self.api_client.set_session_token(self.store.session.session_token)
        connected = self._run_healthcheck()

        if connected:
            message = f"设置已保存并已连接。API：{self.settings.api_base_url} | 网站：{self.settings.web_base_url}"
            error = False
        else:
            message = f"设置已保存，但当前无法连接 API：{self.settings.api_base_url}"
            error = True
        current_widget = self.stack.currentWidget()
        if current_widget is self.login_view:
            self.login_view.set_message(message, error=error)
        elif current_widget is self.trace_import_view:
            self.trace_import_view.set_message(message, error=error)
        elif current_widget is self.battle_upload_view:
            self.battle_upload_view.set_message(message, error=error)

    def _handle_session_invalid(self) -> None:
        self.session_store.clear()
        self.api_client.set_session_token(None)
        self.store.session = None
        self.store.candidates = []
        self.store.last_uploaded_battle_urls = []
        self.store.current_trace_file_name = None
        self.store.current_trace_path = None
        self.store.current_integrity_label = None
        self.store.current_trace_integrity_verified = False
        self.trace_import_view.set_selected_file(None)
        self.trace_import_view.clear_progress()
        self.login_view.set_message("登录已失效，请重新登录。", error=True)
        self._show_login()

    def _handle_start_game_requested(self) -> None:
        from app.services import game_launcher

        game_exe = game_launcher.resolve_game_exe()
        if game_exe is None:
            picked, _ = QFileDialog.getOpenFileName(
                self,
                "选择 Endfield.exe",
                "",
                "Endfield.exe (Endfield.exe);;可执行文件 (*.exe);;所有文件 (*)",
            )
            if not picked:
                self.battle_upload_view.set_message("已取消启动游戏。")
                return
            candidate = Path(picked)
            if candidate.name.lower() != game_launcher.GAME_EXE_NAME.lower() or not (
                candidate.parent / game_launcher.GAME_DLL_NAME
            ).exists():
                self.battle_upload_view.set_message(
                    "所选文件不是有效的 Endfield.exe（同目录需存在 GameAssembly.dll）。", error=True
                )
                return
            game_exe = candidate
            game_launcher.remember_game_exe(game_exe)

        try:
            game_launcher.launch_game(game_exe)
        except OSError as exc:
            self.battle_upload_view.set_message(f"启动游戏失败：{exc}", error=True)
            return
        self.battle_upload_view.set_message(f"已启动游戏：{game_exe.parent.name}")

    def _handle_logout_requested(self) -> None:
        result = QMessageBox.question(
            self,
            "退出登录",
            "确定要退出当前上传器账号吗？本地登录状态会被清除。",
        )
        if result != QMessageBox.StandardButton.Yes:
            return

        self._set_busy(True)
        try:
            if self.store.session is not None:
                try:
                    self.api_client.logout()
                except ApiClientError:
                    pass
        finally:
            self._set_busy(False)

        self.session_store.clear()
        self.api_client.set_session_token(None)
        self.store.session = None
        self.store.candidates = []
        self.store.last_uploaded_battle_urls = []
        self.store.current_trace_file_name = None
        self.store.current_trace_path = None
        self.store.current_integrity_label = None
        self.store.current_trace_integrity_verified = False
        self.trace_import_view.set_selected_file(None)
        self.trace_import_view.set_message("")
        self.trace_import_view.clear_progress()
        self.battle_upload_view.set_candidates([])
        self.battle_upload_view.clear_progress()
        self.battle_upload_view.set_message("")
        self.login_view.clear_profile_setup()
        self.login_view.set_message("你已退出登录。")
        self._show_login()

    def _web_url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return f"{self.settings.web_base_url}{path}"
