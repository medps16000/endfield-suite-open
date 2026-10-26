"""Actual isolated renderer death/recovery and native diagnostic UI."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def test_renderer_failure_is_bounded_and_does_not_duplicate_subscriptions(tmp_path):
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--runtime", str(tmp_path)],
                            capture_output=True, text=True, timeout=70,
                            env={**os.environ, "QT_QPA_PLATFORM": "offscreen", "QTWEBENGINE_CHROMIUM_FLAGS": "--disable-gpu"})
    assert result.returncode == 0, (result.stdout + result.stderr)[-10000:]
    assert "OVERLAY_RECOVERY_OK" in result.stdout


def _runtime(root):
    import json
    import time
    import psutil
    from endfield_pcap import overlay_windows as host
    from endfield_pcap.overlay_window_settings import OverlaySettingsDialog
    from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication, QDialog, QPlainTextEdit

    host.RENDER_RECOVERY_DELAYS_MS = (20, 30, 40)
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    html = root / "custom.html"
    html.write_text("""<!doctype html><html><body>Recovery test<script>
window.last=null; window.cancel=window.endfieldOverlay.subscribe({topic:'test'}, x=>window.last=x);
console.error('ReferenceError missingName is not defined; https://example.com/?token=privateToken token=privateToken');
window.evalBlocked=false;try{eval('window.evalBlocked=false');}catch(error){window.evalBlocked=true;}
</script></body></html>""", encoding="utf-8")

    class Broker:
        def __init__(self):
            self.callbacks = []
            self.added = 0
        def get_topics(self):
            return [{"topic": "test"}]
        def subscribe(self, _topic, _options, callback):
            self.callbacks.append(callback)
            self.added += 1
            callback({"state": "unavailable", "data": None, "reason": "disconnected"})
            def stop():
                if callback in self.callbacks: self.callbacks.remove(callback)
            return stop

    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    def wait(predicate, seconds=12):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            app.processEvents()
            if predicate(): return
            time.sleep(.01)
        raise AssertionError("recovery condition timed out")
    def evaluate(page, script):
        loop = QEventLoop(); values = []
        page.runJavaScript(script, lambda value:(values.append(value),loop.quit()))
        QTimer.singleShot(1000, loop.quit);loop.exec()
        return values[0] if values else None

    broker = Broker()
    manager = host.OverlayWindowManager(broker, root / "settings.json", root / "profiles")
    first = manager.add_entry("first", "file", str(html))
    other = manager.add_entry("other", "file", str(html))
    window = manager.windows[first["id"]]
    wait(lambda: len(broker.callbacks) == 2 and bool(evaluate(window.page, "Boolean(window.last)")))
    diagnostic = manager.diagnostics.text(first["id"])
    assert "ReferenceError missingName" in diagnostic
    assert "privateToken" not in diagnostic and "example.com" not in diagnostic
    assert "Unrecognized Content-Security-Policy" not in diagnostic
    assert evaluate(window.page, "window.evalBlocked === true")
    # The killed process must be a renderer owned by THIS isolated test process.
    renderer_pid = window.page.renderProcessPid()
    assert renderer_pid > 0 and renderer_pid != os.getpid()
    renderer = psutil.Process(renderer_pid)
    assert os.getpid() in {parent.pid for parent in renderer.parents()}
    renderer.kill()
    wait(lambda: window._recovery_attempts == 1 and not window._failure and window.page.renderProcessPid() != renderer_pid)
    wait(lambda: len(broker.callbacks) == 2)
    wait(lambda:evaluate(window.page, "Boolean(window.last && window.last.state==='unavailable' && window.last.data===null)"))
    assert "renderer_terminated" in manager.diagnostics.text(first["id"])

    # Repeat explicit renderer exit signals to exercise bounded scheduling,
    # retaining a real Qt page and real broker lifecycle for every reload.
    status = host.QWebEnginePage.RenderProcessTerminationStatus.CrashedTerminationStatus
    for attempt in (2, 3):
        window._renderer_terminated(status, 7)
        assert not window.bridge.subscriptions and not window.view.isVisible()
        wait(lambda: window._recovery_attempts == attempt and not window._failure)
        wait(lambda: len(broker.callbacks) == 2)
    window._renderer_terminated(status, 7)
    assert window._failure and not window._recovery_timer.isActive()
    assert "停止自动重试" in window._failure
    assert not window.bridge.subscriptions and len(broker.callbacks) == 1
    manager.reload_entry(first["id"])
    wait(lambda: not window._failure and len(broker.callbacks) == 2)
    assert window._recovery_attempts == 0

    settings = OverlaySettingsDialog(manager)
    settings.refresh_list(first["id"])
    def close_diagnostic():
        dialogs = [item for item in settings.findChildren(QDialog) if item.windowTitle() == "悬浮窗错误日志"]
        assert len(dialogs) == 1
        assert "renderer_terminated" in dialogs[0].findChild(QPlainTextEdit).toPlainText()
        dialogs[0].accept()
    QTimer.singleShot(30, close_diagnostic)
    settings._show_diagnostics()

    window._renderer_terminated(status, 7)
    manager.update_entry(first["id"], visible=False)
    assert not window._recovery_timer.isActive()
    manager.update_entry(first["id"], visible=True)
    window.reload(); wait(lambda:not window._failure and len(broker.callbacks)==2)
    # Disable must stop pending recovery; enabling constructs one new isolated
    # page. Changing HTML source likewise disposes old timers/subscriptions.
    window._renderer_terminated(status, 7)
    manager.update_entry(first["id"], enabled=False)
    assert window._shutting_down and not window._recovery_timer.isActive()
    manager.update_entry(first["id"], enabled=True)
    wait(lambda:len(broker.callbacks)==2)
    current=manager.windows[first["id"]]
    current._renderer_terminated(status,7)
    replacement=root/'replacement.html';replacement.write_text(html.read_text(encoding='utf-8'),encoding='utf-8')
    manager.update_entry(first["id"],sourceValue=str(replacement))
    assert current._shutting_down and not current._recovery_timer.isActive()
    wait(lambda:len(broker.callbacks)==2)
    active = manager.windows[first["id"]]
    replacement.unlink()
    manager.reload_entry(first["id"])
    wait(lambda:active._failure is not None)
    assert not active.view.isVisible() and not active.bridge.subscriptions
    assert not active._recovery_timer.isActive()
    assert "load_failed" in manager.diagnostics.text(first["id"])
    replacement.write_text(html.read_text(encoding='utf-8'),encoding='utf-8')
    manager.reload_entry(first["id"])
    wait(lambda:not active._failure and len(broker.callbacks)==2)
    manager.close();settings.close()
    app.processEvents();QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
    assert not broker.callbacks
    print('OVERLAY_RECOVERY_OK',json.dumps({'realRendererKilled':True,'subscriptions':broker.added,'remaining':len(broker.callbacks)}))


if __name__ == "__main__" and "--runtime" in sys.argv:
    _runtime(sys.argv[-1])
