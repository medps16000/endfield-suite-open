from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType
from types import SimpleNamespace
import pytest

import launcher
from endfield_pcap import runner


def test_uploader_dispatch_does_not_enter_capture_or_update(monkeypatch):
    calls = []
    app = ModuleType("app")
    app_main = ModuleType("app.main")
    app_main.main = lambda: calls.append(list(sys.argv)) or 7
    monkeypatch.setitem(sys.modules, "app", app)
    monkeypatch.setitem(sys.modules, "app.main", app_main)
    monkeypatch.setattr(sys, "argv", ["EndfieldLogsClient.exe", "--uploader", "--verify-log", "retained.log"])
    monkeypatch.setattr(launcher, "_prepare_default_launch", lambda: (_ for _ in ()).throw(AssertionError("capture started")))
    monkeypatch.setattr(launcher, "main", lambda: (_ for _ in ()).throw(AssertionError("CLI entered")))
    monkeypatch.delenv("ENDFIELD_UPLOADER_SKIP_UPDATE_CHECK", raising=False)
    monkeypatch.delenv("ENDFIELD_LOGS_MANAGED_BY_CLIENT", raising=False)
    assert launcher.run() == 7
    assert calls == [["EndfieldLogsClient.exe", "--verify-log", "retained.log"]]
    assert launcher.os.environ["ENDFIELD_UPLOADER_SKIP_UPDATE_CHECK"] == "1"
    assert launcher.os.environ["ENDFIELD_LOGS_MANAGED_BY_CLIENT"] == "1"


def test_detector_dispatch_keeps_arguments_and_skips_capture(monkeypatch):
    diagnostic = ModuleType("endfield_pcap.diagnostic")
    diagnostic.run_detector_app = lambda: 0 if sys.argv == ["client", "--help"] else 9
    monkeypatch.setitem(sys.modules, "endfield_pcap.diagnostic", diagnostic)
    monkeypatch.setattr(sys, "argv", ["client", "--detector", "--help"])
    monkeypatch.setattr(launcher, "_prepare_default_launch", lambda: (_ for _ in ()).throw(AssertionError("capture started")))
    assert launcher.run() == 0


@pytest.mark.parametrize("name", ["EndfieldLogsClient.exe", "renamed-client.exe"])
def test_managed_worker_uses_same_frozen_executable(monkeypatch, tmp_path, name):
    executable = tmp_path / name
    executable.write_bytes(b"client")
    calls = []
    monkeypatch.setattr(runner.sys, "frozen", True, raising=False)
    monkeypatch.setattr(runner.sys, "executable", str(executable))
    monkeypatch.setattr(runner, "bundle_root", lambda: tmp_path / "_internal")
    monkeypatch.delenv("ENDFIELD_LOGS_DISABLE_UPLOADER", raising=False)
    monkeypatch.setattr(runner.subprocess, "Popen", lambda args, **kwargs: calls.append((args, kwargs)) or "worker")
    assert runner._launch_managed_uploader(tmp_path / "trace.log", tmp_path / "Endfield.exe") == "worker"
    assert calls[0][0] == [str(executable.resolve()), "--uploader", "--watch-log", str(tmp_path / "trace.log")]
    assert calls[0][1]["env"]["ENDFIELD_LOGS_MANAGED_BY_CLIENT"] == "1"


def test_control_capability_is_private_per_launch_and_parent_environment_is_restored(monkeypatch, tmp_path):
    observed = {}
    class Service:
        def __init__(self, *args, **kwargs): pass
        def request_stop(self): pass
    class Worker:
        def __init__(self, **kwargs): pass
        def start(self): pass
        def join(self, **kwargs): pass
    monkeypatch.setattr(runner, "DamageLogService", Service)
    monkeypatch.setattr(runner.threading, "Thread", Worker)
    monkeypatch.setattr(runner, "_wait_for_service_start", lambda *args: None)
    monkeypatch.setattr(runner, "bundle_root", lambda: tmp_path)
    monkeypatch.setenv("ENDFIELD_OVERLAY_CONTROL_NAME", "previous-parent")
    monkeypatch.setenv("ENDFIELD_OVERLAY_CONTROL_TOKEN", "previous-secret")
    def launch(*args):
        observed.update({key: runner.os.environ[key] for key in (
            "ENDFIELD_OVERLAY_CONTROL_NAME", "ENDFIELD_OVERLAY_CONTROL_TOKEN")})
    monkeypatch.setattr(runner, "_launch_managed_uploader", launch)
    monkeypatch.setattr(runner.runpy, "run_path", lambda *args, **kwargs: None)
    config = SimpleNamespace(trace_file=tmp_path/"trace.log", status_file=None, log_dir=tmp_path,
                             dll_dir=tmp_path, game_exe="Endfield.exe")
    assert runner.run_with_overlay(config) == 0
    assert observed["ENDFIELD_OVERLAY_CONTROL_NAME"].startswith("EndfieldOverlay-")
    assert len(observed["ENDFIELD_OVERLAY_CONTROL_TOKEN"]) == 64
    assert set(observed["ENDFIELD_OVERLAY_CONTROL_TOKEN"]) <= set("0123456789abcdef")
    assert runner.os.environ["ENDFIELD_OVERLAY_CONTROL_NAME"] == "previous-parent"
    assert runner.os.environ["ENDFIELD_OVERLAY_CONTROL_TOKEN"] == "previous-secret"
