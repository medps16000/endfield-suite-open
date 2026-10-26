"""Own Tk/Qt client lifecycle, with isolated files and no capture/uploader."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def test_integrated_tk_settings_and_qt_window_disposal(tmp_path):
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--runtime", str(tmp_path)],
                            capture_output=True, text=True, timeout=50,
                            env={**os.environ, "QT_QPA_PLATFORM": "offscreen", "QTWEBENGINE_CHROMIUM_FLAGS": "--disable-gpu"})
    assert result.returncode == 0, (result.stdout + result.stderr)[-8000:]
    assert "INTEGRATED_OVERLAY_OK" in result.stdout


def test_host_configuration_lease_retries_without_blocking_tk_or_capture_startup(tmp_path):
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--contention-runtime", str(tmp_path)],
                            capture_output=True, text=True, timeout=50,
                            env={**os.environ, "QT_QPA_PLATFORM": "offscreen", "QTWEBENGINE_CHROMIUM_FLAGS": "--disable-gpu"})
    assert result.returncode == 0, (result.stdout + result.stderr)[-8000:]
    assert "INTEGRATED_OVERLAY_OK" in result.stdout


def _runtime(root, contention=False):
    import json
    import runpy
    import time
    from shiboken6 import isValid

    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    trace = root / "trace.log"
    trace.write_text("", encoding="utf-8")
    status = root / "status.json"
    status.write_text(json.dumps({"state": "waiting_connection"}), encoding="utf-8")
    os.environ.update(ENDFIELD_OVERLAY_ALLOW_MULTIPLE="1", ENDFIELD_PCAP_TRACE_FILE=str(trace),
                      ENDFIELD_PCAP_STATUS_FILE=str(status), ENDFIELD_OVERLAY_SETTINGS_ROOT=str(root / "windows"),
                      LOCALAPPDATA=str(root / "app-data"), ENDFIELD_LOGS_DISABLE_UPLOADER="1")
    source = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(source))
    values = runpy.run_path(str(source / "overlay.py"), run_name="integration_component")
    lease = values["OverlayConfigLease"](root / "windows") if contention else None
    if lease is not None:
        assert lease.held
    meter = values["OverlayApp"]()
    meter.root.withdraw()
    try:
        if lease is not None:
            assert meter.web_overlays is None and meter._web_host_retry is not None
            assert meter.root.winfo_exists()
            lease.close()
            deadline = time.monotonic() + 4
            while meter.web_overlays is None and time.monotonic() < deadline:
                meter.root.update()
                time.sleep(.01)
            assert meter.web_overlays is not None and meter.overlay_host_lease.held
        assert meter.web_overlays is not None, meter._web_overlay_error
        meter._open_settings()
        assert meter.rotation_module.external_settings_open
        meter.window_settings.hide()
        assert not meter.rotation_module.external_settings_open
        entry = meter.web_overlays.add_entry("测试窗口", "file", str(source / "src/endfield_pcap/overlay_examples/effects-monitor.html"))
        window = meter.web_overlays.windows[entry["id"]]
        assert isValid(window)
        meter.window_settings.refresh_list(entry["id"])
        meter.window_settings.name.setText("改名后保留拖动位置")
        meter.web_overlays.update_entry(entry["id"], geometry={"x": 310, "y": 240, "width": 560, "height": 320})
        meter.window_settings._save_entry()
        saved = meter.web_overlays.get_entry(entry["id"])
        assert saved["geometry"]["x"] == 310 and saved["geometry"]["width"] == 560
        meter.web_overlays.remove_entry(entry["id"])
        # The production app does not call QApplication.exec(). Its existing
        # Tk event pump must release deferred WebEngine/profile objects itself.
        meter.rotation_module._pump()
        assert not isValid(window)
        # Rotation guidance is deliberately disabled; independent HTML settings
        # and the base meter remain usable without re-enabling that module.
        try:
            meter.rotation_module.apply_module_settings("guide", enabled=False, opacity=.6, scale=1.2)
            raise AssertionError("disabled guide accepted a settings mutation")
        except ValueError:
            pass
        meter.rotation_module.apply_module_settings("meter", enabled=True, opacity=.8)
        assert meter.rotation_module.settings["meterOpacity"] == .8
        print("INTEGRATED_OVERLAY_OK")
    finally:
        if lease is not None: lease.close()
        meter._quit()


if __name__ == "__main__" and "--runtime" in sys.argv:
    _runtime(sys.argv[-1])
elif __name__ == "__main__" and "--contention-runtime" in sys.argv:
    _runtime(sys.argv[-1], contention=True)
