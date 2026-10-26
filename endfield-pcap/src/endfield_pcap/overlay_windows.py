"""Sandboxed static-page windows with a small, read-only subscription bridge.

No legacy overlay_host import: that module disables the Chromium sandbox. Both
local and HTTPS documents use a host-owned scheme. HTTPS fetches pin a verified
public address and validate TLS/SNI, so Chromium never resolves remote hosts.
"""
from __future__ import annotations

import hashlib
import atexit
import http.client
import ipaddress
import json
import mimetypes
import os
import socket
import socketserver
import ssl
import threading
import time
import uuid
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from PySide6.QtCore import QByteArray, QBuffer, QCoreApplication, QFile, QIODevice, QObject, QRunnable, QThreadPool, QTimer, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineScript, QWebEngineSettings, QWebEngineUrlRequestInfo, QWebEngineUrlRequestInterceptor, QWebEngineUrlRequestJob, QWebEngineUrlScheme, QWebEngineUrlSchemeHandler
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QPushButton, QSizeGrip, QVBoxLayout, QWidget

from .overlay_window_config import OverlayWindowConfig, validate_source
from .overlay_diagnostics import OverlayDiagnostics

SCHEME = "endfield-overlay"
MAX_RESOURCE_BYTES = 8 * 1024 * 1024
MAX_SUBSCRIPTIONS = 32
RENDER_RECOVERY_DELAYS_MS = (2000, 5000, 10000)
RENDER_STABLE_RESET_MS = 60000
_network_pool = None
_network_slots = threading.BoundedSemaphore(64)


class _DenyProxy(socketserver.TCPServer):
    """An application-only proxy which never forwards or reads any request."""
    allow_reuse_address = False


class _DenyProxyRequest(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.close()


_deny_proxy = None
_network_flags = ()


def _close_deny_proxy():
    if _deny_proxy is not None:
        # Stop the select loop before closing its socket (Windows otherwise
        # reports WinError 10038 from the daemon during interpreter shutdown).
        _deny_proxy.shutdown()
        _deny_proxy.server_close()


if QCoreApplication.instance() is None:
    # URL interceptors do not cover WebRTC's own UDP path. Chromium's forced
    # policy disables non-proxied UDP; its remaining network paths use a closed
    # forwarding proxy, including loopback (Chromium normally bypasses it).
    # Python's pinned HTTPS loader is independent of this engine-only setting.
    _deny_proxy = _DenyProxy(("127.0.0.1", 0), _DenyProxyRequest)
    _network_flags = (
        f"--proxy-server=http://127.0.0.1:{_deny_proxy.server_address[1]}",
        "--proxy-bypass-list=<-loopback>",
        "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
    )
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = " ".join((os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", ""), *_network_flags)).strip()
    threading.Thread(target=_deny_proxy.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True, name="overlay-deny-proxy").start()
    atexit.register(_close_deny_proxy)
_registered = bool(QWebEngineUrlScheme.schemeByName(SCHEME.encode()).name())
if not _registered and QCoreApplication.instance() is None:
    _scheme = QWebEngineUrlScheme(SCHEME.encode())
    _scheme.setSyntax(QWebEngineUrlScheme.Syntax.Host)
    _scheme.setFlags(QWebEngineUrlScheme.Flag.SecureScheme | QWebEngineUrlScheme.Flag.CorsEnabled)
    QWebEngineUrlScheme.registerScheme(_scheme)
    _registered = True


def sandbox_enabled() -> bool:
    disabled = os.environ.get("QTWEBENGINE_DISABLE_SANDBOX", "").strip().lower()
    flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "").lower()
    return disabled in {"", "0", "false"} and not any(flag in flags for flag in ("--no-sandbox", "--disable-web-security", "--allow-file-access-from-files")) and not os.environ.get("QTWEBENGINE_REMOTE_DEBUGGING")


def public_addresses(hostname: str) -> list[str]:
    """Reject mixed public/private DNS responses, not only a chosen address."""
    if hostname.lower().rstrip(".") in {"localhost", "localhost.localdomain"}:
        raise ValueError("网页不能访问本机或私网。")
    rows = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
    addresses = list(dict.fromkeys(row[4][0] for row in rows))
    if not addresses or any(not ipaddress.ip_address(address).is_global or ipaddress.ip_address(address).is_multicast for address in addresses):
        raise ValueError("网页不能访问本机、私网或保留网络。")
    return addresses


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, hostname: str, address: str):
        super().__init__(hostname, port=443, timeout=12, context=ssl.create_default_context())
        self._address = address

    def connect(self):
        connection = socket.create_connection((self._address, 443), timeout=self.timeout)
        try:
            self.sock = self._context.wrap_socket(connection, server_hostname=self.host)
        except BaseException:
            connection.close()
            raise


def fetch_public_https(url: str) -> tuple[bytes, str]:
    _, canonical = validate_source("url", url)
    parsed = urlsplit(canonical)
    addresses = public_addresses(parsed.hostname)
    connection = _PinnedHTTPSConnection(parsed.hostname, addresses[0])
    def abort():
        transport = connection.sock
        if transport is not None:
            try:
                transport.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        connection.close()
    deadline = threading.Timer(18, abort)
    deadline.daemon = True
    deadline.start()
    try:
        connection.request("GET", urlunsplit(("", "", parsed.path, parsed.query, "")), headers={"User-Agent": "EndfieldOverlayPreview/1", "Accept-Encoding": "identity", "Connection": "close"})
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError("网页返回错误或重定向，已拒绝加载。")
        content_encoding = response.getheader("Content-Encoding", "identity").strip().lower()
        if content_encoding not in {"", "identity"}:
            raise ValueError("网页压缩格式不受支持。")
        length = response.getheader("Content-Length")
        if length is not None and (not length.isdigit() or int(length) > MAX_RESOURCE_BYTES):
            raise ValueError("页面资源过大。")
        body = response.read(MAX_RESOURCE_BYTES + 1)
        if len(body) > MAX_RESOURCE_BYTES:
            raise ValueError("页面资源过大。")
        mime = response.getheader("Content-Type", "application/octet-stream").split(";", 1)[0].strip()
        if not mime or len(mime) > 100 or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/-+." for c in mime):
            mime = "application/octet-stream"
        return body, mime
    finally:
        deadline.cancel()
        connection.close()


class EntryPolicy:
    """Pure policy shared by the engine interceptor and resource handler."""
    def __init__(self, entry: dict):
        self.kind, self.value = validate_source(entry["sourceType"], entry["sourceValue"], must_exist=False)
        self.host = "w" + entry["id"].lower()
        self.root = Path(self.value).parent.resolve() if self.kind == "file" else None
        self.remote = urlsplit(self.value) if self.kind == "url" else None
        path = "/" + quote(Path(self.value).name, safe="") if self.kind == "file" else self.remote.path
        query = "" if self.kind == "file" else self.remote.query
        self.entry_url = urlunsplit((SCHEME, self.host, path, query, ""))

    def exact_entry(self, url: str) -> bool:
        parsed = urlsplit(url)
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, "")) == self.entry_url

    def local_path(self, url: str) -> Path:
        parsed = urlsplit(url)
        if self.kind != "file" or parsed.scheme != SCHEME or parsed.netloc != self.host:
            raise ValueError("资源来源不匹配。")
        decoded = unquote(parsed.path)
        parts = PurePosixPath(decoded).parts
        if not decoded.startswith("/") or any(part in {"..", "."} or "\\" in part or ":" in part or "\x00" in part for part in parts):
            raise ValueError("本地资源越界。")
        candidate = (self.root / decoded.lstrip("/")).resolve(strict=True)
        if not candidate.is_relative_to(self.root) or not candidate.is_file():
            raise ValueError("本地资源越界或不存在。")
        return candidate

    def remote_url(self, url: str) -> str:
        parsed = urlsplit(url)
        if self.kind != "url" or parsed.scheme != SCHEME or parsed.netloc != self.host:
            raise ValueError("网页资源来源不匹配。")
        return urlunsplit(("https", self.remote.netloc, parsed.path or "/", parsed.query, ""))

    def request_action(self, url: str, *, main_frame=False) -> tuple[str, str | None]:
        parsed = urlsplit(url)
        if main_frame:
            return ("allow", None) if self.exact_entry(url) else ("deny", None)
        if parsed.scheme == SCHEME and parsed.netloc == self.host:
            return "allow", None
        if parsed.scheme in {"data", "blob"}:
            return "allow", None
        if self.remote and parsed.scheme == "https" and parsed.netloc == self.remote.netloc and not parsed.username and not parsed.password:
            return "redirect", urlunsplit((SCHEME, self.host, parsed.path or "/", parsed.query, ""))
        return "deny", None


class _Interceptor(QWebEngineUrlRequestInterceptor):
    def __init__(self, policy, parent):
        super().__init__(parent)
        self.policy = policy

    def interceptRequest(self, info):
        if info.requestMethod() != b"GET" or info.resourceType() in {QWebEngineUrlRequestInfo.ResourceType.ResourceTypeSubFrame, QWebEngineUrlRequestInfo.ResourceType.ResourceTypeWebSocket, QWebEngineUrlRequestInfo.ResourceType.ResourceTypeWorker, QWebEngineUrlRequestInfo.ResourceType.ResourceTypeSharedWorker, QWebEngineUrlRequestInfo.ResourceType.ResourceTypeServiceWorker}:
            info.block(True)
            return
        action, destination = self.policy.request_action(info.requestUrl().toString(), main_frame=info.resourceType() == QWebEngineUrlRequestInfo.ResourceType.ResourceTypeMainFrame)
        if action == "deny":
            info.block(True)
        elif action == "redirect":
            info.redirect(QUrl(destination))


class _FetchSignals(QObject):
    complete = Signal(str, object, str)


class _Fetch(QRunnable):
    def __init__(self, identifier, url, signals):
        super().__init__()
        self.identifier, self.url, self.signals = identifier, url, signals
        self.cancelled = threading.Event()

    def run(self):
        try:
            if self.cancelled.is_set():
                return
            body, mime = fetch_public_https(self.url)
            if not self.cancelled.is_set():
                self.signals.complete.emit(self.identifier, body, mime)
        except Exception:
            if not self.cancelled.is_set():
                try:
                    self.signals.complete.emit(self.identifier, None, "")
                except RuntimeError:
                    pass
        finally:
            _network_slots.release()


class _SchemeHandler(QWebEngineUrlSchemeHandler):
    def __init__(self, policy, parent):
        super().__init__(parent)
        self.policy = policy
        self.jobs = {}
        self.tasks = {}
        self.signals = _FetchSignals(self)
        self.signals.complete.connect(self._complete, Qt.ConnectionType.QueuedConnection)
        global _network_pool
        if _network_pool is None:
            _network_pool = QThreadPool(QCoreApplication.instance())
            _network_pool.setMaxThreadCount(4)
        self.pool = _network_pool

    def requestStarted(self, job):
        if job.requestMethod() != b"GET" or len(self.jobs) >= 32:
            job.fail(QWebEngineUrlRequestJob.Error.RequestDenied)
            return
        url = job.requestUrl().toString()
        try:
            if self.policy.kind == "file":
                path = self.policy.local_path(url)
                if path.stat().st_size > MAX_RESOURCE_BYTES:
                    raise ValueError("资源过大。")
                body = path.read_bytes()
                self._reply(job, body, mimetypes.guess_type(path.name)[0] or "application/octet-stream")
            else:
                remote = self.policy.remote_url(url)
                if not _network_slots.acquire(blocking=False):
                    raise ValueError("页面资源请求过多。")
                identifier = uuid.uuid4().hex
                self.jobs[identifier] = job
                task = _Fetch(identifier, remote, self.signals)
                self.tasks[identifier] = task
                job.destroyed.connect(lambda _=None, key=identifier: self._cancel(key))
                self.pool.start(task)
        except (OSError, ValueError):
            job.fail(QWebEngineUrlRequestJob.Error.RequestDenied)

    @Slot(str, object, str)
    def _complete(self, identifier, body, mime):
        job = self.jobs.pop(identifier, None)
        self.tasks.pop(identifier, None)
        if job is None:
            return
        if body is None:
            job.fail(QWebEngineUrlRequestJob.Error.RequestFailed)
        else:
            self._reply(job, body, mime)

    def _cancel(self, identifier):
        self.jobs.pop(identifier, None)
        task = self.tasks.pop(identifier, None)
        if task:
            task.cancelled.set()

    def close(self):
        for task in self.tasks.values():
            task.cancelled.set()
        self.tasks.clear()
        self.jobs.clear()

    def _reply(self, job, body, mime):
        # No network scheme is allowed by CSP; same-origin HTTPS resources are
        # translated before loading, and fetched only by the pinned transport.
        origin = f"https://{self.policy.remote.netloc}" if self.policy.remote else ""
        policy = (f"default-src {SCHEME}: {origin} data: blob:; script-src {SCHEME}: {origin} 'unsafe-inline'; style-src {SCHEME}: {origin} 'unsafe-inline'; img-src {SCHEME}: {origin} data: blob:; connect-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'self'; form-action 'none'; media-src 'none'").encode()
        # Qt's API takes a QMultiMap: the Python value is a LIST of header
        # values, despite the 6.11 stub declaring a single QByteArray. A bare
        # QByteArray is iterable and gets split into one header per character.
        job.setAdditionalResponseHeaders({QByteArray(b"Content-Security-Policy"): [QByteArray(policy)], QByteArray(b"X-Content-Type-Options"): [QByteArray(b"nosniff")]})
        buffer = QBuffer(job)
        buffer.setData(QByteArray(body))
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        job.reply(mime.encode(), buffer)


class _Page(QWebEnginePage):
    def __init__(self, profile, policy, parent):
        super().__init__(profile, parent)
        self.policy = policy
        self.permissionRequested.connect(lambda permission: permission.deny())
        self.fullScreenRequested.connect(lambda request: request.reject())
        self.fileSystemAccessRequested.connect(lambda request: request.reject())
        self.registerProtocolHandlerRequested.connect(lambda request: request.reject())
        self.newWindowRequested.connect(lambda request: None)

    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
        return bool(is_main_frame and self.policy.exact_entry(url.toString()))

    def createWindow(self, _kind):
        return None

    def chooseFiles(self, _mode, _old_files, _mime_types):
        return []

    def javaScriptConsoleMessage(self, level, message, line, _source):
        owner = self.parent().parent()
        if getattr(owner, "_shutting_down", True):
            return
        severity = "error" if level == QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel else "warning"
        if level != QWebEnginePage.JavaScriptConsoleMessageLevel.InfoMessageLevel:
            owner.manager.diagnostics.record(owner.entry["id"], "javascript", message, level=severity, line=line)


_PUBLIC_API = r"""(() => {
 'use strict'; if (window !== window.top) return;
 const tag = '__ENDFIELD_READONLY_OVERLAY_V1__', pending = new Map(), subscriptions = new Map();
 let next = 0;
 const send = payload => window.postMessage({tag, direction:'request', payload}, '*');
 const request = payload => new Promise((resolve,reject) => {
   const requestId = 'r' + (++next), message = {...payload,requestId};
   if(JSON.stringify(message).length>32768){reject(new Error('overlay-request-limit'));return;}
   const timer = setTimeout(() => {pending.delete(requestId);reject(new Error('悬浮窗连接尚未就绪'));},15000);
   pending.set(requestId,{resolve,reject,timer}); send(message);
 });
 window.addEventListener('message', event => {
   if(event.source !== window || !event.data || event.data.tag !== tag || event.data.direction !== 'response') return;
   const msg = event.data.payload;
   if(!msg || typeof msg !== 'object') return;
   if(msg.kind === 'response') { const p=pending.get(msg.requestId);if(p){pending.delete(msg.requestId);clearTimeout(p.timer);msg.ok?p.resolve(msg.value):p.reject(new Error(msg.error || '请求失败'));} }
   if(msg.kind === 'snapshot') { const cb=subscriptions.get(msg.subscriptionId);if(cb){try{cb(msg.envelope);}catch(error){console.error('悬浮窗订阅回调失败：'+String(error));}} }
 });
 Object.defineProperty(window,'endfieldOverlay',{configurable:false,writable:false,value:Object.freeze({
   getTopics: () => request({op:'getTopics'}),
   subscribe: (spec, callback) => {
     if(!spec || typeof spec.topic !== 'string' || typeof callback !== 'function') throw new TypeError('subscribe 需要主题和回调');
     const subscriptionId='s'+(++next);subscriptions.set(subscriptionId,callback);
     request({op:'subscribe',subscriptionId,topic:spec.topic,options:{filter:spec.filter || {}}}).catch(error => {
       if(subscriptions.has(subscriptionId)) callback({topic:spec.topic,schemaVersion:1,sequence:0,receivedAtMs:Date.now(),state:'unavailable',reason:String(error.message),data:null});
     });
     return () => {if(subscriptions.delete(subscriptionId)) request({op:'unsubscribe',subscriptionId}).catch(()=>{});};
   }
 })});
})();"""

_ISOLATED_API = r"""(() => {
 'use strict'; if(window !== window.top) return;
 const tag='__ENDFIELD_READONLY_OVERLAY_V1__';let bridge=null;const queue=[];
 window.addEventListener('message',event=>{
   if(event.source!==window || !event.data || event.data.tag!==tag || event.data.direction!=='request') return;
   try {const encoded=JSON.stringify(event.data.payload);if(encoded.length>32768)return;if(bridge)bridge.request(encoded);else if(queue.length<64)queue.push(encoded);}catch(_){}
 });
 new QWebChannel(qt.webChannelTransport, channel=>{
   bridge=channel.objects.subscription;
   bridge.outbound.connect(encoded=>{try{window.postMessage({tag,direction:'response',payload:JSON.parse(encoded)},'*');}catch(_){}});
   for(const encoded of queue)bridge.request(encoded);queue.length=0;
 });
})();"""


class _Bridge(QObject):
    outbound = Signal(str)
    incoming = Signal(int, str, object)
    wake = Signal()

    def __init__(self, broker, page, policy):
        super().__init__(page)
        self.broker, self.page, self.policy = broker, page, policy
        self.generation = 0
        self.subscriptions = {}
        self._recent_requests = []
        self._pending_lock = threading.Lock()
        self._pending = {}
        self._wake_queued = False
        self.incoming.connect(self._deliver, Qt.ConnectionType.QueuedConnection)
        self.wake.connect(self._flush_pending, Qt.ConnectionType.QueuedConnection)

    def reset(self):
        with self._pending_lock:
            self.generation += 1
            self._pending.clear()
        subscriptions, self.subscriptions = self.subscriptions, {}
        for unsubscribe in subscriptions.values():
            try:
                unsubscribe()
            except Exception:
                pass

    def _send(self, value):
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        if len(encoded) > 2 * 1024 * 1024 and value.get("kind") == "snapshot":
            envelope = value["envelope"]
            value = {**value, "envelope": {"topic": envelope.get("topic"), "schemaVersion": envelope.get("schemaVersion", 1), "sequence": envelope.get("sequence", 0), "receivedAtMs": envelope.get("receivedAtMs"), "state": "unavailable", "reason": "window-payload-limit", "data": None}}
            encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        if len(encoded) <= 2 * 1024 * 1024:
            self.outbound.emit(encoded)

    def _queue_snapshot(self, generation, identifier, envelope):
        with self._pending_lock:
            if generation != self.generation:
                return
            self._pending[identifier] = (generation, envelope)
            if self._wake_queued:
                return
            self._wake_queued = True
        self.wake.emit()

    @Slot()
    def _flush_pending(self):
        with self._pending_lock:
            pending, self._pending = self._pending, {}
            self._wake_queued = False
        for identifier, (generation, envelope) in pending.items():
            self._deliver(generation, identifier, envelope)

    @Slot(str)
    def request(self, encoded):
        if not self.policy.exact_entry(self.page.url().toString()) or len(encoded) > 32768:
            return
        request_id = None
        try:
            request = json.loads(encoded)
            if not isinstance(request, dict):
                raise ValueError("请求无效。")
            request_id = request.get("requestId")
            if not isinstance(request_id, str) or len(request_id) > 80:
                return
            now = time.monotonic()
            self._recent_requests = [stamp for stamp in self._recent_requests if now - stamp < 1]
            if len(self._recent_requests) >= 100:
                raise ValueError("请求过于频繁。")
            self._recent_requests.append(now)
            operation = request.get("op")
            if operation == "getTopics":
                result = self.broker.get_topics()
            elif operation == "subscribe":
                identifier = request.get("subscriptionId")
                if not isinstance(identifier, str) or len(identifier) > 80 or identifier in self.subscriptions:
                    raise ValueError("订阅标识无效。")
                if len(self.subscriptions) >= MAX_SUBSCRIPTIONS:
                    raise ValueError("窗口订阅数超出限制。")
                topic, options = request.get("topic"), request.get("options", {})
                topics = self.broker.get_topics()
                names = {row.get("topic", row.get("name", row.get("id"))) if isinstance(row, dict) else row for row in topics}
                if topic not in names or not isinstance(options, dict):
                    raise ValueError("主题或筛选无效。")
                generation = self.generation
                # Reserve before subscribe: producers deliver the initial snapshot
                # synchronously, while Qt delivery is always queued.
                self.subscriptions[identifier] = lambda: None
                try:
                    unsubscribe = self.broker.subscribe(topic, options, lambda envelope: self._queue_snapshot(generation, identifier, envelope))
                    self.subscriptions[identifier] = unsubscribe
                except BaseException:
                    self.subscriptions.pop(identifier, None)
                    raise
                result = True
            elif operation == "unsubscribe":
                unsubscribe = self.subscriptions.pop(request.get("subscriptionId"), None)
                if unsubscribe:
                    unsubscribe()
                result = True
            else:
                raise ValueError("页面仅能读取主题、订阅或取消订阅。")
            self._send({"kind": "response", "requestId": request_id, "ok": True, "value": result})
        except Exception as exc:
            # Do not disclose internal paths/diagnostics through unexpected
            # producer errors. Validation failures alone are page-facing.
            message = str(exc)[:200] if isinstance(exc, (ValueError, TypeError)) else "订阅请求暂时无法完成。"
            self._send({"kind": "response", "requestId": request_id, "ok": False, "error": message})

    @Slot(int, str, object)
    def _deliver(self, generation, identifier, envelope):
        if generation != self.generation or identifier not in self.subscriptions or not self.policy.exact_entry(self.page.url().toString()):
            return
        self._send({"kind": "snapshot", "subscriptionId": identifier, "envelope": envelope})


class _TitleBar(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self.owner = window
        layout = QHBoxLayout(self)
        layout.setContentsMargins(9, 3, 4, 3)
        self.label = QLabel()
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.label, 1)
        hide = QPushButton("−")
        hide.setFixedSize(24, 22)
        hide.setToolTip("隐藏窗口，可在设置中重新显示")
        hide.clicked.connect(lambda: self.owner.manager.update_entry(self.owner.entry["id"], visible=False))
        layout.addWidget(hide)
        self._origin = None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and not self.owner.entry["locked"]:
            self._origin = event.globalPosition().toPoint() - self.owner.pos()

    def mouseMoveEvent(self, event):
        if self._origin is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.owner.move(event.globalPosition().toPoint() - self._origin)

    def mouseReleaseEvent(self, event):
        self._origin = None
        self.owner.schedule_geometry()


class _Window(QWidget):
    def __init__(self, manager, entry):
        super().__init__()
        self.manager, self.entry = manager, entry
        self._shutting_down = False
        self._recovery_attempts = 0
        self._failure = None
        self.policy = EntryPolicy(entry)
        self._applying = True
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet("QWidget{background:#14212be8;color:#e8eef2;} QPushButton{background:#273b49;border:0;}")
        self.setMinimumSize(160, 100)
        self._geometry_timer = QTimer(self)
        self._geometry_timer.setSingleShot(True)
        self._geometry_timer.setInterval(250)
        self._geometry_timer.timeout.connect(self._persist_geometry)
        self._recovery_timer = QTimer(self)
        self._recovery_timer.setSingleShot(True)
        self._recovery_timer.timeout.connect(self._retry_renderer)
        self._stable_timer = QTimer(self)
        self._stable_timer.setSingleShot(True)
        self._stable_timer.setInterval(RENDER_STABLE_RESET_MS)
        self._stable_timer.timeout.connect(self._reset_recovery_budget)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)
        self.title = _TitleBar(self)
        layout.addWidget(self.title)
        self.failure_notice = QWidget(self)
        failure_layout = QHBoxLayout(self.failure_notice)
        self.failure_label = QLabel()
        self.failure_label.setWordWrap(True)
        self.failure_label.setTextFormat(Qt.TextFormat.PlainText)
        failure_layout.addWidget(self.failure_label, 1)
        retry = QPushButton("重载页面")
        retry.clicked.connect(self.reload)
        failure_layout.addWidget(retry)
        self.failure_notice.hide()
        layout.addWidget(self.failure_notice)
        self.view = QWebEngineView(self)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        layout.addWidget(self.view, 1)
        self.grip = QSizeGrip(self)
        self.grip.setFixedHeight(10)
        layout.addWidget(self.grip, 0, Qt.AlignmentFlag.AlignRight)
        partition = hashlib.sha256((entry["sourceType"] + "\0" + entry["sourceValue"]).encode()).hexdigest()[:24]
        profile_root = self.manager.storage_root / entry["id"] / partition
        profile_root.mkdir(parents=True, exist_ok=True)
        self.profile = QWebEngineProfile("overlay-" + entry["id"] + "-" + partition, self)
        self.profile.setPersistentStoragePath(str(profile_root / "storage"))
        self.profile.setCachePath(str(profile_root / "cache"))
        self.profile.setHttpCacheMaximumSize(4 * 1024 * 1024)
        self.profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)
        self.profile.downloadRequested.connect(lambda download: download.cancel())
        self.interceptor = _Interceptor(self.policy, self.profile)
        self.profile.setUrlRequestInterceptor(self.interceptor)
        self.handler = _SchemeHandler(self.policy, self.profile)
        self.profile.installUrlSchemeHandler(SCHEME.encode(), self.handler)
        self.page = _Page(self.profile, self.policy, self.view)
        self.page.setAudioMuted(True)
        self.page.setBackgroundColor(Qt.GlobalColor.transparent)
        settings = self.page.settings()
        for attribute in (QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, QWebEngineSettings.WebAttribute.JavascriptCanPaste, QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, QWebEngineSettings.WebAttribute.AllowRunningInsecureContent, QWebEngineSettings.WebAttribute.PluginsEnabled, QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, QWebEngineSettings.WebAttribute.ScreenCaptureEnabled, QWebEngineSettings.WebAttribute.NavigateOnDropEnabled, QWebEngineSettings.WebAttribute.HyperlinkAuditingEnabled, QWebEngineSettings.WebAttribute.DnsPrefetchEnabled, QWebEngineSettings.WebAttribute.WebGLEnabled, QWebEngineSettings.WebAttribute.PdfViewerEnabled, QWebEngineSettings.WebAttribute.AutoLoadIconsForPage):
            settings.setAttribute(attribute, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, True)
        self.view.setPage(self.page)
        self.bridge = _Bridge(manager.broker, self.page, self.policy)
        self.channel = QWebChannel(self.page)
        self.channel.registerObject("subscription", self.bridge)
        self.page.setWebChannel(self.channel, QWebEngineScript.ScriptWorldId.ApplicationWorld)
        channel_file = QFile(":/qtwebchannel/qwebchannel.js")
        if not channel_file.open(QIODevice.OpenModeFlag.ReadOnly):
            raise RuntimeError("未找到安全页面桥接组件。")
        channel_source = bytes(channel_file.readAll()).decode("utf-8")
        channel_file.close()
        for name, source, world in (("endfield-public-api", _PUBLIC_API, QWebEngineScript.ScriptWorldId.MainWorld), ("endfield-isolated-bridge", channel_source + "\n" + _ISOLATED_API, QWebEngineScript.ScriptWorldId.ApplicationWorld)):
            script = QWebEngineScript()
            script.setName(name)
            script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
            script.setWorldId(world)
            script.setRunsOnSubFrames(False)
            script.setSourceCode(source)
            self.page.scripts().insert(script)
        self.page.loadStarted.connect(self._load_started)
        self.page.loadFinished.connect(self._loaded)
        self.page.renderProcessTerminated.connect(self._renderer_terminated)
        self.apply(entry)
        self.page.load(QUrl(self.policy.entry_url))

    def _loaded(self, success):
        if self._shutting_down:
            return
        if success:
            self._failure = None
            self.failure_notice.hide()
            self.view.show()
            self.title.label.setText(self.entry["name"])
            self._stable_timer.start()
        elif not self._recovery_timer.isActive():
            self._show_failure("页面加载失败，请检查 HTML 文件或网页连接后重载。")
            self.manager.diagnostics.record(self.entry["id"], "load_failed", "页面加载失败；未记录入口地址或页面内容。")

    def _load_started(self):
        if self._shutting_down:
            return
        self.bridge.reset()
        self._stable_timer.stop()
        self.view.hide()

    def _show_failure(self, message):
        self._failure = message
        self.bridge.reset()
        self.view.hide()
        self.failure_label.setText(message)
        self.failure_notice.show()
        self.title.label.setText(self.entry["name"] + " · 页面异常")

    def _renderer_terminated(self, status, exit_code):
        if self._shutting_down or self.manager._closing:
            return
        self._recovery_timer.stop()
        self._stable_timer.stop()
        self.manager.diagnostics.record(self.entry["id"], "renderer_terminated",
                                        f"渲染进程退出：{status.name}，退出码 {int(exit_code)}。")
        if not self.entry["visible"]:
            self._show_failure("隐藏期间页面渲染异常，已暂停自动恢复；重新显示后可手动重载。")
        elif self._recovery_attempts < len(RENDER_RECOVERY_DELAYS_MS):
            delay = RENDER_RECOVERY_DELAYS_MS[self._recovery_attempts]
            self._recovery_attempts += 1
            self._show_failure(f"页面渲染异常，{delay // 1000} 秒后尝试恢复（{self._recovery_attempts}/{len(RENDER_RECOVERY_DELAYS_MS)}）。")
            self._recovery_timer.start(delay)
        else:
            self._show_failure("页面连续异常，已停止自动重试。请查看错误日志，修正页面后手动重载。")
            self.manager.diagnostics.record(self.entry["id"], "recovery_limit", "连续渲染异常，已停止自动重试。")

    def _retry_renderer(self):
        if not self._shutting_down and not self.manager._closing and self.entry["enabled"] and self.entry["visible"]:
            self.manager.diagnostics.record(self.entry["id"], "renderer_retry", "开始恢复页面。", level="info")
            self.page.load(QUrl(self.policy.entry_url))

    def _reset_recovery_budget(self):
        self._recovery_attempts = 0

    def reload(self):
        if self._shutting_down:
            return
        self._recovery_timer.stop()
        self._stable_timer.stop()
        self._recovery_attempts = 0
        self._failure = None
        self.bridge.reset()
        self.manager.diagnostics.record(self.entry["id"], "manual_reload", "手动重载页面。", level="info")
        self.page.triggerAction(QWebEnginePage.WebAction.ReloadAndBypassCache)

    def apply(self, entry):
        self._applying = True
        self.entry = entry
        self.setWindowTitle(entry["name"])
        self.title.label.setText(entry["name"] + (" · 页面异常" if self._failure else ""))
        dimensions = entry["geometry"]
        self.setGeometry(dimensions["x"], dimensions["y"], dimensions["width"], dimensions["height"])
        self.setWindowOpacity(entry["opacity"])
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, entry["clickThrough"])
        self.title.setVisible(not entry["locked"])
        self.grip.setVisible(not entry["locked"])
        if entry["enabled"] and entry["visible"]:
            self.show()
        else:
            self._recovery_timer.stop()
            self._stable_timer.stop()
            self.hide()
        self._applying = False

    def schedule_geometry(self):
        if not self._applying and not self.entry["locked"]:
            self._geometry_timer.start()

    def moveEvent(self, event):
        super().moveEvent(event)
        if hasattr(self, "_geometry_timer"):
            self.schedule_geometry()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_geometry_timer"):
            self.schedule_geometry()

    def _persist_geometry(self):
        rect = self.geometry()
        self.manager._save_geometry(self.entry["id"], {"x": rect.x(), "y": rect.y(), "width": rect.width(), "height": rect.height()})

    def shutdown(self):
        self._shutting_down = True
        self._geometry_timer.stop()
        self._recovery_timer.stop()
        self._stable_timer.stop()
        self.bridge.reset()
        self.handler.close()
        self.page.triggerAction(QWebEnginePage.WebAction.Stop)
        self.hide()
        self.page.deleteLater()
        self.deleteLater()

    def closeEvent(self, event):
        if not self.manager._closing:
            event.ignore()
            self.manager.update_entry(self.entry["id"], visible=False)
        else:
            event.accept()


class OverlayWindowManager:
    def __init__(self, broker, config_path, storage_root, on_changed=None):
        if not sandbox_enabled():
            raise RuntimeError("安全悬浮窗模块未启动：当前环境关闭了网页沙箱，请正常重启客户端。")
        if not _registered:
            raise RuntimeError("安全悬浮窗模块需在 Qt 初始化前导入，请重启客户端。")
        if not _deny_proxy or not all(flag in os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "") for flag in _network_flags):
            raise RuntimeError("安全悬浮窗网络隔离未就绪，请正常重启客户端。")
        if QApplication.instance() is None:
            raise RuntimeError("悬浮窗需要已初始化的 Qt 应用。")
        self.broker, self.storage_root, self.on_changed = broker, Path(storage_root), on_changed
        self.diagnostics = OverlayDiagnostics(self.storage_root / "diagnostics")
        self.config = OverlayWindowConfig(config_path)
        self.windows = {}
        self._closing = False
        for entry in self.config.list_entries():
            if entry["enabled"]:
                self.windows[entry["id"]] = _Window(self, entry)

    def list_entries(self):
        return self.config.list_entries()

    def get_entry(self, identifier):
        return self.config.get_entry(identifier)

    def _changed(self):
        if self.on_changed:
            self.on_changed()

    def add_entry(self, name, source_type, source_value):
        entry = self.config.add_entry(name, source_type, source_value)
        try:
            self.windows[entry["id"]] = _Window(self, entry)
        except Exception:
            self.config.remove_entry(entry["id"])
            raise
        self._changed()
        return entry

    def update_entry(self, identifier, **patch):
        before = self.config.get_entry(identifier)
        entry = self.config.update_entry(identifier, **patch)
        window = self.windows.get(identifier)
        source_changed = (before["sourceType"], before["sourceValue"]) != (entry["sourceType"], entry["sourceValue"])
        if window and (source_changed or not entry["enabled"]):
            window.shutdown()
            del self.windows[identifier]
            window = None
        if entry["enabled"]:
            if window:
                window.apply(entry)
            else:
                self.windows[identifier] = _Window(self, entry)
        self._changed()
        return entry

    def _save_geometry(self, identifier, geometry):
        if self._closing or identifier not in self.windows:
            return
        entry = self.config.update_entry(identifier, geometry=geometry)
        self.windows[identifier].entry = entry
        self._changed()

    def remove_entry(self, identifier):
        self.config.remove_entry(identifier)
        window = self.windows.pop(identifier, None)
        if window:
            window.shutdown()
        self.diagnostics.forget(identifier)
        # Keep original HTML and isolated browser state; no file deletion API.
        self._changed()

    def reload_entry(self, identifier):
        self.config.get_entry(identifier)
        window = self.windows.get(identifier)
        if window:
            window.reload()

    def recover_entry(self, identifier):
        entry = self.config.get_entry(identifier)
        screen = QGuiApplication.primaryScreen()
        if screen:
            area = screen.availableGeometry()
            dimensions = dict(entry["geometry"])
            dimensions["width"] = min(dimensions["width"], area.width())
            dimensions["height"] = min(dimensions["height"], area.height())
            dimensions["x"] = area.x() + max(0, (area.width() - dimensions["width"]) // 2)
            dimensions["y"] = area.y() + max(0, (area.height() - dimensions["height"]) // 2)
        else:
            dimensions = {"x": 100, "y": 100, "width": 440, "height": 320}
        return self.update_entry(identifier, geometry=dimensions, enabled=True, visible=True, locked=False, clickThrough=False, opacity=1.0)

    def close(self):
        for window in self.windows.values():
            if window._geometry_timer.isActive():
                window._persist_geometry()
        self._closing = True
        for window in self.windows.values():
            window.shutdown()
        self.windows.clear()
