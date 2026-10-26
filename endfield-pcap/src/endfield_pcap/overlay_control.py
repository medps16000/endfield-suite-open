"""Private native settings control between the client and its uploader window.

This is a same-user local pipe, not an HTML or external-browser API. Only the
trusted native settings page holds the per-launch capability inherited from its
parent. Game data continues to use the existing read-only topic interface.
"""
from __future__ import annotations

import copy
import hmac
import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid

from PySide6.QtCore import QLockFile, QObject, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from .overlay_diagnostics import safe_text
from .overlay_window_config import OverlayWindowConfig
from .rotation_client import OverlaySettingsStore

NAME_ENV = "ENDFIELD_OVERLAY_CONTROL_NAME"
TOKEN_ENV = "ENDFIELD_OVERLAY_CONTROL_TOKEN"
MAX_REQUEST_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 512 * 1024
MAX_CONNECTIONS = 4
MAX_PENDING = 4
REQUEST_TIMEOUT = 5.0
_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_TOKEN = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[0-9a-f]{32}$")
_OPERATIONS = {"snapshot", "add", "update", "remove", "reload", "recover", "meter", "diagnostics"}


def settings_root() -> Path:
    return Path(os.environ.get("ENDFIELD_OVERLAY_SETTINGS_ROOT") or
                (Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "EndfieldPCAP" / "web-overlays"))


class OverlayConfigLease:
    """One live host or one offline writer may own the shared settings at once."""
    def __init__(self, root: Path):
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        self.lock = QLockFile(str(root / "control-host.lock"))
        # A healthy long-running host must never lose its lease solely because
        # its lock file is old. QLockFile still checks a recorded dead owner.
        self.lock.setStaleLockTime(0)
        self.held = self.lock.tryLock(0)

    def close(self):
        if self.held:
            self.lock.unlock()
            self.held = False


def _meter_view(settings: dict) -> dict:
    return {"meterEnabled": bool(settings["meterEnabled"]),
            "meterOpacity": float(settings.get("meterOpacity", settings["opacity"])),
            "opacity": float(settings["opacity"]), "guideScale": 1.0}


def _meter_arguments(arguments: dict) -> tuple[bool, float]:
    if set(arguments) != {"enabled", "opacity"} or type(arguments["enabled"]) is not bool:
        raise ValueError("基础 DPS 设置无效。")
    opacity = arguments["opacity"]
    if type(opacity) not in (float, int) or not .35 <= opacity <= 1:
        raise ValueError("基础 DPS 透明度应为 35% 至 100%。")
    return arguments["enabled"], float(opacity)


def _arguments(operation: str, arguments: object) -> dict:
    if operation not in _OPERATIONS or not isinstance(arguments, dict):
        raise ValueError("不支持的悬浮窗操作。")
    expected = {"snapshot": set(), "add": {"name", "sourceType", "sourceValue"},
                "update": {"id", "patch"}, "remove": {"id"}, "reload": {"id"},
                "recover": {"id"}, "meter": {"enabled", "opacity"}, "diagnostics": {"id"}}[operation]
    if set(arguments) != expected:
        raise ValueError("悬浮窗操作参数无效。")
    if "id" in arguments and (not isinstance(arguments["id"], str) or len(arguments["id"]) > 64):
        raise ValueError("窗口标识无效。")
    if operation == "update" and not isinstance(arguments["patch"], dict):
        raise ValueError("窗口设置无效。")
    return arguments


class OverlayControlServer(QObject):
    """All commands run on the host's existing Qt/Tk main-thread pump."""
    def __init__(self, manager, rotation, *, name: str, token: str, lease=None, parent=None):
        super().__init__(parent)
        if not _SAFE_NAME.fullmatch(name) or not _TOKEN.fullmatch(token):
            raise ValueError("悬浮窗控制会话无效。")
        self.manager, self.rotation = manager, rotation
        self._owns_lease = lease is None
        self.lease = lease or OverlayConfigLease(Path(manager.config.path).parent)
        if not self.lease.held:
            raise RuntimeError("悬浮窗配置正由另一个客户端使用。")
        self._token = token
        self._peers = {}
        self._revision = 0
        self._snapshot_identity = None
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self.server.setMaxPendingConnections(MAX_CONNECTIONS)
        self.server.newConnection.connect(self._accept)
        if not self.server.listen(name):
            if self._owns_lease: self.lease.close()
            raise RuntimeError("悬浮窗控制会话无法启动。")

    def snapshot(self) -> dict:
        entries, meter = self.manager.list_entries(), _meter_view(self.rotation.settings)
        identity = hashlib.sha256(json.dumps([entries, meter], sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        if identity != self._snapshot_identity:
            self._snapshot_identity = identity
            self._revision += 1
        return {"entries": entries, "meter": meter, "revision": self._revision,
                "online": True, "status": "已连接悬浮窗服务，保存后立即生效。",
                "capabilities": {"configuration": True, "liveActions": True}}

    def _accept(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            if len(self._peers) >= MAX_CONNECTIONS:
                socket.abort(); socket.deleteLater(); continue
            timer = QTimer(socket)
            timer.setSingleShot(True)
            timer.timeout.connect(socket.abort)
            timer.start(8000)
            self._peers[socket] = {"buffer": bytearray(), "times": [], "timer": timer, "authorized": False}
            socket.readyRead.connect(lambda socket=socket: self._read(socket))
            socket.disconnected.connect(lambda socket=socket: self._drop(socket))

    def _drop(self, socket):
        self._peers.pop(socket, None)
        socket.deleteLater()

    def _reply(self, socket, payload):
        encoded = (json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
        if len(encoded) > MAX_RESPONSE_BYTES or socket.bytesToWrite() > MAX_RESPONSE_BYTES:
            socket.abort(); return
        socket.write(encoded)

    def _read(self, socket):
        peer = self._peers.get(socket)
        if peer is None: return
        peer["buffer"].extend(bytes(socket.readAll()))
        if len(peer["buffer"]) > MAX_REQUEST_BYTES:
            socket.abort(); return
        while b"\n" in peer["buffer"]:
            raw, _, rest = peer["buffer"].partition(b"\n")
            peer["buffer"] = bytearray(rest)
            now = time.monotonic()
            peer["times"] = [stamp for stamp in peer["times"] if now - stamp < 1]
            if len(peer["times"]) >= 16:
                socket.abort(); return
            peer["times"].append(now)
            identifier = None
            try:
                request = json.loads(raw)
                if not isinstance(request, dict) or set(request) != {"id", "token", "operation", "arguments"}:
                    raise ValueError("控制请求无效。")
                identifier = request["id"]
                if not isinstance(identifier, str) or not _ID.fullmatch(identifier):
                    raise ValueError("控制请求标识无效。")
                supplied = request["token"]
                if not isinstance(supplied, str) or not hmac.compare_digest(supplied, self._token):
                    socket.abort(); return
                peer["authorized"] = True
                peer["timer"].start(8000)
                operation = request["operation"]
                arguments = _arguments(operation, request["arguments"])
                value = self._perform(operation, arguments)
                self._reply(socket, {"id": identifier, "ok": True, "result": {
                    "snapshot": self.snapshot(), "value": value, "offlineSaved": False}})
            except (ValueError, TypeError, KeyError, RecursionError, OSError, RuntimeError) as exc:
                if identifier is not None:
                    self._reply(socket, {"id": identifier, "ok": False, "error": safe_text(exc)})
                else:
                    socket.abort(); return

    def _perform(self, operation, arguments):
        if operation == "snapshot": return None
        if operation == "add":
            return self.manager.add_entry(arguments["name"], arguments["sourceType"], arguments["sourceValue"])
        if operation == "meter":
            enabled, opacity = _meter_arguments(arguments)
            self.rotation.apply_module_settings("meter", enabled=enabled, opacity=opacity)
            return _meter_view(self.rotation.settings)
        identifier = arguments["id"]
        self.manager.get_entry(identifier)
        if operation == "update":
            patch = copy.deepcopy(arguments["patch"])
            if "geometry" in patch:
                if not isinstance(patch["geometry"], dict): raise ValueError("窗口尺寸无效。")
                patch["geometry"] = {**self.manager.get_entry(identifier)["geometry"], **patch["geometry"]}
            return self.manager.update_entry(identifier, **patch)
        if operation == "remove": return self.manager.remove_entry(identifier)
        if operation == "reload": return self.manager.reload_entry(identifier)
        if operation == "recover": return self.manager.recover_entry(identifier)
        if operation == "diagnostics": return self.manager.diagnostics.text(identifier)
        raise ValueError("不支持的悬浮窗操作。")

    def close(self):
        for socket in list(self._peers): socket.abort()
        self.server.close()
        if self._owns_lease: self.lease.close()

    def request_settings(self) -> bool:
        """A trusted host button may focus its already-authenticated settings UI."""
        recipients = [socket for socket, peer in self._peers.items() if peer["authorized"]]
        for socket in recipients: self._reply(socket, {"event": "open_settings"})
        return bool(recipients)


def attach_control_server(manager, rotation, *, lease=None):
    """No endpoint is created for standalone/source tests without a parent grant."""
    name, token = os.environ.get(NAME_ENV), os.environ.get(TOKEN_ENV)
    if not name and not token: return None
    if not name or not token: raise ValueError("悬浮窗控制会话不完整。")
    return OverlayControlServer(manager, rotation, name=name, token=token, lease=lease)


class OverlayControlClient(QObject):
    """Asynchronous native UI adapter; never waits for the host on the UI thread."""
    snapshotChanged = Signal(dict)
    connectionChanged = Signal(str)
    operationFinished = Signal(str, object)
    operationFailed = Signal(str, str)
    openSettingsRequested = Signal()

    def __init__(self, *, name=None, token=None, root: Path | None = None, meter_store=None, parent=None):
        super().__init__(parent)
        self.root = Path(root) if root is not None else settings_root()
        self.storage_root = self.root / "profiles"
        self._name = os.environ.get(NAME_ENV, "") if name is None else name
        self._token = os.environ.get(TOKEN_ENV, "") if token is None else token
        self._managed = bool(self._name or self._token)
        self._meter_store = meter_store or OverlaySettingsStore()
        self._pending = {}
        self._buffer = bytearray()
        self._closed = False
        self._offline_revision = 0
        self.snapshot = self._offline_snapshot()
        self.socket = QLocalSocket(self)
        self.socket.connected.connect(self._connected)
        self.socket.disconnected.connect(self._disconnected)
        self.socket.readyRead.connect(self._read)
        self.socket.errorOccurred.connect(lambda _error: self._disconnected())
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        if self._managed:
            self._set_status("正在连接悬浮窗服务…", live=False)
        QTimer.singleShot(0, self._tick)

    def _offline_snapshot(self):
        try:
            entries = OverlayWindowConfig(self.root / "windows.json").list_entries()
            meter = _meter_view(self._meter_store.load())
            error = None
        except (ValueError, OSError) as exc:
            entries, meter, error = [], {"meterEnabled": True, "meterOpacity": .9, "opacity": .9, "guideScale": 1.0}, safe_text(exc)
        available = False
        if error is None:
            try:
                lease = OverlayConfigLease(self.root)
                available = lease.held
                lease.close()
            except OSError as exc:
                error = safe_text(exc)
        status = error or ("采集客户端未启动，设置保存后将在下次启动时生效。" if available else
                           "悬浮窗服务正在其他客户端运行；请从正在运行的统一客户端进入设置。")
        return {"entries": entries, "meter": meter, "online": False,
                "revision": self._offline_revision,
                "status": status,
                "capabilities": {"configuration": available, "liveActions": False}}

    def _set_status(self, text, *, live):
        self.snapshot = {**self.snapshot, "online": live, "status": text,
                         "capabilities": {"configuration": live or not self._managed, "liveActions": live}}
        self.connectionChanged.emit(text)
        self.snapshotChanged.emit(copy.deepcopy(self.snapshot))

    def list_entries(self): return copy.deepcopy(self.snapshot["entries"])

    def get_entry(self, identifier):
        for entry in self.snapshot["entries"]:
            if entry["id"] == identifier: return copy.deepcopy(entry)
        raise ValueError("未找到此悬浮窗。")

    def submit(self, operation: str, arguments: dict | None = None) -> str:
        arguments = {} if arguments is None else arguments
        _arguments(operation, arguments)
        identifier = uuid.uuid4().hex
        if self._closed: raise RuntimeError("悬浮窗设置已关闭。")
        if len(self._pending) >= MAX_PENDING: raise RuntimeError("操作正在处理，请稍候。")
        if not self._managed:
            # Queue callbacks so the UI can register the returned request ID.
            self._pending[identifier] = time.monotonic() + REQUEST_TIMEOUT
            QTimer.singleShot(0, lambda: self._offline_perform(identifier, operation, copy.deepcopy(arguments)))
            return identifier
        if self.socket.state() != QLocalSocket.LocalSocketState.ConnectedState:
            QTimer.singleShot(0, lambda: self.operationFailed.emit(identifier, "悬浮窗服务尚未连接，未修改设置。"))
            return identifier
        message = {"id": identifier, "token": self._token, "operation": operation, "arguments": arguments}
        encoded = (json.dumps(message, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
        if len(encoded) > MAX_REQUEST_BYTES: raise ValueError("操作参数过大。")
        self._pending[identifier] = time.monotonic() + REQUEST_TIMEOUT
        self.socket.write(encoded)
        return identifier

    def _tick(self):
        if self._closed: return
        now = time.monotonic()
        for identifier, deadline in list(self._pending.items()):
            if now > deadline:
                self._pending.pop(identifier, None)
                self.operationFailed.emit(identifier, "悬浮窗服务响应超时，未确认设置已生效。")
        if not self._managed:
            current = self._offline_snapshot()
            if current != self.snapshot:
                changed_status = current["status"] != self.snapshot["status"]
                self.snapshot = current
                if changed_status: self.connectionChanged.emit(current["status"])
                self.snapshotChanged.emit(copy.deepcopy(current))
            return
        if not _SAFE_NAME.fullmatch(self._name) or not _TOKEN.fullmatch(self._token):
            self._set_status("悬浮窗控制会话无效，请重新启动统一客户端。", live=False)
            return
        if self.socket.state() == QLocalSocket.LocalSocketState.UnconnectedState:
            self.socket.connectToServer(self._name)
        elif self.socket.state() == QLocalSocket.LocalSocketState.ConnectedState and not self._pending:
            self.submit("snapshot")

    def _connected(self):
        self._buffer.clear()
        self.submit("snapshot")

    def _disconnected(self):
        if self._closed: return
        for identifier in list(self._pending):
            self._pending.pop(identifier, None)
            self.operationFailed.emit(identifier, "悬浮窗连接已中断，未确认设置已生效。")
        self._buffer.clear()
        if self._managed:
            self._set_status("悬浮窗服务未连接，正在重试；尚未连接时不修改运行中的设置。", live=False)

    def _read(self):
        self._buffer.extend(bytes(self.socket.readAll()))
        if len(self._buffer) > MAX_RESPONSE_BYTES:
            self.socket.abort(); return
        while b"\n" in self._buffer:
            raw, _, rest = self._buffer.partition(b"\n")
            self._buffer = bytearray(rest)
            try:
                message = json.loads(raw)
                if message == {"event": "open_settings"}:
                    self.openSettingsRequested.emit()
                    continue
                identifier = message["id"]
                if identifier not in self._pending: continue
                self._pending.pop(identifier)
                if message.get("ok") is not True:
                    self.operationFailed.emit(identifier, safe_text(message.get("error", "操作未完成。")))
                    continue
                result = message["result"]
                snapshot = result["snapshot"]
                if not isinstance(snapshot, dict) or not isinstance(snapshot.get("entries"), list) or len(snapshot["entries"]) > 24:
                    raise ValueError("Invalid control response")
                prior_status = self.snapshot["status"]
                self.snapshot = snapshot
                if snapshot["status"] != prior_status: self.connectionChanged.emit(snapshot["status"])
                self.snapshotChanged.emit(copy.deepcopy(snapshot))
                self.operationFinished.emit(identifier, result)
            except (ValueError, TypeError, KeyError, RecursionError):
                self.socket.abort(); return

    def _offline_perform(self, identifier, operation, arguments):
        if self._closed: return
        self._pending.pop(identifier, None)
        lease = None
        try:
            if operation not in {"snapshot", "diagnostics"}:
                lease = OverlayConfigLease(self.root)
                if not lease.held:
                    raise RuntimeError("悬浮窗服务正在其他客户端运行，未修改设置；请从该客户端进入设置。")
            config = OverlayWindowConfig(self.root / "windows.json")
            if operation == "snapshot": value = None
            elif operation == "add": value = config.add_entry(arguments["name"], arguments["sourceType"], arguments["sourceValue"])
            elif operation == "update":
                patch = copy.deepcopy(arguments["patch"])
                if "geometry" in patch:
                    if not isinstance(patch["geometry"], dict): raise ValueError("窗口尺寸无效。")
                    patch["geometry"] = {**config.get_entry(arguments["id"])["geometry"], **patch["geometry"]}
                value = config.update_entry(arguments["id"], **patch)
            elif operation == "remove": value = config.remove_entry(arguments["id"])
            elif operation == "meter":
                enabled, opacity = _meter_arguments(arguments)
                settings = self._meter_store.load()
                settings.update(meterEnabled=enabled, meterOpacity=opacity)
                self._meter_store.save(settings)
                value = _meter_view(settings)
            elif operation == "diagnostics":
                config.get_entry(arguments["id"])
                value = "悬浮窗服务未启动，暂无本次运行日志。启动统一客户端后可查看实时诊断。"
            else:
                raise RuntimeError("此操作需要运行中的悬浮窗服务，请先启动统一客户端。")
            if lease is not None:
                lease.close(); lease = None
            self._offline_revision += 1
            self.snapshot = self._offline_snapshot()
            self.snapshotChanged.emit(copy.deepcopy(self.snapshot))
            self.operationFinished.emit(identifier, {"snapshot": copy.deepcopy(self.snapshot), "value": value, "offlineSaved": True})
        except (ValueError, TypeError, OSError, RuntimeError) as exc:
            if lease is not None:
                lease.close(); lease = None
            self.operationFailed.emit(identifier, safe_text(exc))
            self.snapshot = self._offline_snapshot()
            self.snapshotChanged.emit(copy.deepcopy(self.snapshot))
        finally:
            if lease is not None: lease.close()

    def close(self):
        self._closed = True
        self.timer.stop()
        self._pending.clear()
        self.socket.abort()
