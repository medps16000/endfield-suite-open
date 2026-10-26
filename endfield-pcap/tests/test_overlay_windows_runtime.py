"""Actual QtWebEngine/isolated bridge runtime, separate process and no capture."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path


def test_source_qt_runtime_bridge_storage_and_controls(tmp_path):
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--runtime", str(tmp_path)], capture_output=True, text=True, timeout=80, env={**os.environ, "QT_QPA_PLATFORM": "offscreen", "QTWEBENGINE_CHROMIUM_FLAGS": "--disable-gpu"})
    assert result.returncode == 0, (result.stdout + result.stderr)[-12000:]
    assert "OVERLAY_RUNTIME_OK" in result.stdout


def _runtime(root):
    # Import/register custom scheme BEFORE QApplication.
    from endfield_pcap.overlay_windows import OverlayWindowManager
    from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication
    import threading
    import time

    class Broker:
        def __init__(self):
            self.callbacks = []
            self.subscribed = 0
            self.unsubscribed = 0

        def get_topics(self):
            return [{"topic": "effects.actual.v1", "schemaVersion": 1}]

        def subscribe(self, topic, options, callback):
            assert topic == "effects.actual.v1"
            self.subscribed += 1
            self.callbacks.append(callback)
            callback({"topic": topic, "schemaVersion": 1, "sequence": 1, "receivedAtMs": 1, "state": "ready", "reason": None, "data": {"effects": [{"effectId": "buff_test", "stackCount": None}]}})

            def unsubscribe():
                self.unsubscribed += 1
                if callback in self.callbacks:
                    self.callbacks.remove(callback)
            return unsubscribe

    def evaluate(page, source):
        loop = QEventLoop()
        result = []
        page.runJavaScript(source, lambda value: (result.append(value), loop.quit()))
        QTimer.singleShot(3000, loop.quit)
        loop.exec()
        assert result, "JavaScript callback timed out"
        return result[0]

    def wait_for(predicate, seconds=15):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            app.processEvents()
            if predicate():
                return
            time.sleep(0.01)
        raise AssertionError("runtime condition timed out")

    root = Path(root)
    page = root / "test.html"
    (root / "style.css").write_text("body { --loaded-style: passed; }", encoding="utf-8")
    page.write_text("""<!doctype html><html><head><link rel="stylesheet" href="style.css"></head><body><h1>Runtime</h1><script>
window.snapshots=[];window.topicResult=null;
window.endfieldOverlay.getTopics().then(x=>window.topicResult=x).catch(e=>window.error=String(e));
window.stopSubscription=window.endfieldOverlay.subscribe({topic:'effects.actual.v1',filter:{target:'all'}},x=>window.snapshots.push(x));
localStorage.setItem('boots', String(Number(localStorage.getItem('boots')||0)+1));
</script></body></html>""", encoding="utf-8")
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    broker = Broker()
    manager = OverlayWindowManager(broker, root / "config.json", root / "profiles")
    first = manager.add_entry("first", "file", str(page))
    second = manager.add_entry("second", "file", str(page))
    a, b = manager.windows[first["id"]], manager.windows[second["id"]]
    wait_for(lambda: evaluate(a.page, "Boolean(window.topicResult && window.snapshots.length)") and evaluate(b.page, "Boolean(window.topicResult && window.snapshots.length)"))
    assert evaluate(a.page, "JSON.stringify(Object.keys(window.endfieldOverlay).sort())") == '["getTopics","subscribe"]'
    assert evaluate(a.page, "typeof qt") == "undefined"
    assert evaluate(a.page, "window.snapshots[0].data.effects[0].stackCount === null")
    assert evaluate(a.page, "getComputedStyle(document.body).getPropertyValue('--loaded-style').trim()") == "passed"
    assert evaluate(a.page, "localStorage.getItem('boots')") == "1"
    assert evaluate(b.page, "localStorage.getItem('boots')") == "1"
    evaluate(a.page, "localStorage.setItem('private','one')")
    assert evaluate(b.page, "localStorage.getItem('private') === null")
    subscribed_before_limit = broker.subscribed
    evaluate(a.page, "window.limitResult=null;window.stopLimit=window.endfieldOverlay.subscribe({topic:'effects.actual.v1',filter:{effectIds:['x'.repeat(40000)]}},x=>window.limitResult=x);true")
    wait_for(lambda: evaluate(a.page, "window.limitResult?.reason") == "overlay-request-limit", seconds=2)
    assert evaluate(a.page, "window.limitResult.state==='unavailable'&&window.limitResult.data===null")
    assert broker.subscribed == subscribed_before_limit
    evaluate(a.page, "window.stopLimit();true")
    # Optional live public HTTPS fetch exercises the same isolated page API.
    if os.environ.get("ENDFIELD_OVERLAY_TEST_PUBLIC_URL"):
        remote = manager.add_entry("public", "url", os.environ["ENDFIELD_OVERLAY_TEST_PUBLIC_URL"])
        remote_window = manager.windows[remote["id"]]
        wait_for(lambda: evaluate(remote_window.page, "document.title === 'Example Domain' && typeof window.endfieldOverlay === 'object'"), seconds=25)
        evaluate(remote_window.page, "window.remoteTopics=null;window.endfieldOverlay.getTopics().then(x=>window.remoteTopics=x)")
        wait_for(lambda: evaluate(remote_window.page, "Boolean(window.remoteTopics && window.remoteTopics.length)"))
        manager.remove_entry(remote["id"])
    # No traffic may reach a local backend/STUN target, including paths outside
    # normal fetch interception. This tests one actual WebRTC UDP attempt.
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp.bind(("127.0.0.1", 0))
    udp.settimeout(0.05)
    udp_port = udp.getsockname()[1]
    evaluate(a.page, f"window.rtc=new RTCPeerConnection({{iceServers:[{{urls:'stun:127.0.0.1:{udp_port}'}}]}});window.rtc.createDataChannel('test');window.rtc.createOffer().then(x=>window.rtc.setLocalDescription(x));true")
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        app.processEvents()
        try:
            packet = udp.recv(4096)
        except TimeoutError:
            continue
        raise AssertionError(f"WebRTC bypassed the isolated transport ({len(packet)} bytes)")
    udp.close()
    evaluate(a.page, "window.rtc.close()")
    before = broker.subscribed
    manager.reload_entry(first["id"])
    wait_for(lambda: broker.subscribed > before and evaluate(a.page, "Boolean(window.snapshots && window.snapshots.length)"))
    assert evaluate(a.page, "localStorage.getItem('private')") == "one"
    assert evaluate(a.page, "localStorage.getItem('boots')") == "2"
    # Producer callback from another thread must reach Qt through its queue.
    event = {"topic": "effects.actual.v1", "schemaVersion": 1, "sequence": 2, "receivedAtMs": 2, "state": "unavailable", "reason": "disconnected", "data": None}
    thread = threading.Thread(target=lambda: [callback(event) for callback in list(broker.callbacks)])
    thread.start(); thread.join()
    wait_for(lambda: evaluate(a.page, "window.snapshots[window.snapshots.length-1].state") == "unavailable")
    assert evaluate(a.page, "window.snapshots[window.snapshots.length-1].data === null")
    # Bursts replace the pending full snapshot instead of replaying an old queue.
    snapshot_count = evaluate(a.page, "window.snapshots.length")
    def burst():
        for number in range(3, 1003):
            latest = {**event, "sequence": number}
            for callback in list(broker.callbacks):
                callback(latest)
    thread = threading.Thread(target=burst)
    thread.start(); thread.join()
    wait_for(lambda: evaluate(a.page, "window.snapshots[window.snapshots.length-1].sequence") == 1002)
    assert evaluate(a.page, "window.snapshots.length") == snapshot_count + 1
    evaluate(a.page, "window.stopSubscription()")
    wait_for(lambda: len(broker.callbacks) == 1)
    # Same-document hash is allowed, document replacement/new navigation is not.
    evaluate(a.page, "location.href='https://127.0.0.1/'")
    app.processEvents()
    assert a.policy.exact_entry(a.page.url().toString())
    manager.update_entry(first["id"], visible=False, locked=True, clickThrough=True, opacity=0.3, geometry={"x": -9999, "y": -9999, "width": 500, "height": 300})
    assert not a.isVisible()
    recovered = manager.recover_entry(first["id"])
    assert recovered["visible"] and not recovered["locked"] and not recovered["clickThrough"] and recovered["opacity"] == 1
    assert recovered["geometry"]["x"] >= 0
    assert a.isVisible()
    # Old generation queued envelopes are discarded after reset.
    count = evaluate(a.page, "window.snapshots.length")
    a.bridge.incoming.emit(a.bridge.generation - 1, "stale", event)
    app.processEvents()
    assert evaluate(a.page, "window.snapshots.length") == count
    manager.remove_entry(second["id"])
    assert page.exists()
    manager.close()
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    manager = OverlayWindowManager(broker, root / "config.json", root / "profiles")
    restored = manager.windows[first["id"]]
    wait_for(lambda: evaluate(restored.page, "Boolean(window.snapshots && window.snapshots.length)"))
    assert evaluate(restored.page, "localStorage.getItem('private')") == "one"
    assert evaluate(restored.page, "localStorage.getItem('boots')") == "3"
    manager.close()
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    print("OVERLAY_RUNTIME_OK", json.dumps({"subscriptions": broker.subscribed, "unsubscribed": broker.unsubscribed}))


if __name__ == "__main__" and "--runtime" in sys.argv:
    _runtime(sys.argv[-1])
