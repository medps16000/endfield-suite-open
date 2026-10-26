from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

import launcher
from endfield_pcap import runner


def _game_dir(tmp_path: Path) -> Path:
    game_dir = tmp_path / "Endfield Game"
    game_dir.mkdir()
    (game_dir / "Endfield.exe").write_bytes(b"")
    (game_dir / "GameAssembly.dll").write_bytes(b"")
    return game_dir


def test_default_launch_resolves_game_path_before_entering_cli(monkeypatch, tmp_path: Path) -> None:
    game_dir = _game_dir(tmp_path)
    trace_file = tmp_path / "logs" / "trace.log"
    calls: list[str] = []

    monkeypatch.setattr(launcher.sys, "argv", ["EndfieldLogsClient.exe"])
    monkeypatch.setattr(launcher, "check_and_maybe_start_update", lambda: False)
    monkeypatch.setattr(launcher, "_ensure_desktop_shortcut_once", lambda: None)
    monkeypatch.setattr(
        launcher,
        "ensure_configured_game_dir_interactive",
        lambda: calls.append("game_path") or game_dir,
    )
    monkeypatch.setattr(launcher, "_default_log_dir", lambda: tmp_path / "logs")
    monkeypatch.setattr(launcher, "make_archive_trace_file", lambda _path: trace_file)

    def fake_main() -> int:
        calls.append("main")
        assert launcher.sys.argv[-2:] == ["--dll-dir", str(game_dir.resolve())]
        assert "--trace-file" in launcher.sys.argv
        return 0

    monkeypatch.setattr(launcher, "main", fake_main)

    assert launcher.run() == 0
    assert calls == ["game_path", "main"]


def test_cancelled_game_path_does_not_enter_cli_or_start_any_child(monkeypatch, tmp_path: Path) -> None:
    messages: list[tuple[str, str, bool]] = []
    logs: list[str] = []

    monkeypatch.setattr(launcher.sys, "argv", ["EndfieldLogsClient.exe"])
    monkeypatch.setattr(launcher, "check_and_maybe_start_update", lambda: False)
    monkeypatch.setattr(launcher, "_ensure_desktop_shortcut_once", lambda: None)
    monkeypatch.setattr(launcher, "ensure_configured_game_dir_interactive", lambda: None)
    monkeypatch.setattr(
        launcher,
        "_write_startup_log",
        lambda message: logs.append(message) or (tmp_path / "startup.log"),
    )
    monkeypatch.setattr(
        launcher,
        "_show_startup_message",
        lambda title, message, error=False: messages.append((title, message, error)),
    )
    monkeypatch.setattr(launcher, "main", lambda: pytest.fail("CLI must not run after path cancellation"))

    assert launcher.run() == 1
    assert logs == ["game path setup cancelled; startup aborted before uploader launch"]
    assert len(messages) == 1
    assert "客户端和上传器均未启动" in messages[0][1]


def test_default_launch_surfaces_invisible_startup_failure(monkeypatch, tmp_path: Path) -> None:
    game_dir = _game_dir(tmp_path)
    messages: list[tuple[str, str, bool]] = []
    logs: list[str] = []

    monkeypatch.setattr(launcher.sys, "argv", ["EndfieldLogsClient.exe"])
    monkeypatch.setattr(launcher, "check_and_maybe_start_update", lambda: False)
    monkeypatch.setattr(launcher, "_ensure_desktop_shortcut_once", lambda: None)
    monkeypatch.setattr(launcher, "ensure_configured_game_dir_interactive", lambda: game_dir)
    monkeypatch.setattr(launcher, "_default_log_dir", lambda: tmp_path / "logs")
    monkeypatch.setattr(launcher, "make_archive_trace_file", lambda _path: tmp_path / "trace.log")
    monkeypatch.setattr(launcher, "main", lambda: (_ for _ in ()).throw(RuntimeError("capture failed")))
    monkeypatch.setattr(
        launcher,
        "_write_startup_log",
        lambda message: logs.append(message) or (tmp_path / "startup.log"),
    )
    monkeypatch.setattr(
        launcher,
        "_show_startup_message",
        lambda title, message, error=False: messages.append((title, message, error)),
    )

    assert launcher.run() == 1
    assert "RuntimeError: capture failed" in logs[0]
    assert messages[0][2] is True
    assert "capture failed" in messages[0][1]


def test_runner_launches_uploader_only_after_service_start_and_stops_it(monkeypatch, tmp_path: Path) -> None:
    import hashlib
    import json

    events: list[str] = []

    class ServiceSpy:
        def __init__(self, config, observer=None) -> None:
            self.stop_requested = False
            assert callable(observer.on_event) and callable(observer.on_state_change)

        async def run(self) -> None:
            events.append("service_run")
            while not self.stop_requested:
                await asyncio.sleep(0.001)

        def wait_started(self, timeout=None) -> bool:
            events.append("service_started")
            return True

        def request_stop(self) -> None:
            events.append("service_stop")
            self.stop_requested = True

    class ProcessSpy:
        def __init__(self) -> None:
            self.running = True

        def poll(self):
            return None if self.running else 0

        def terminate(self) -> None:
            events.append("uploader_terminate")
            self.running = False

        def wait(self, timeout=None) -> int:
            return 0

        def kill(self) -> None:
            self.running = False

    trace_file = tmp_path / "trace.log"
    config = SimpleNamespace(
        trace_file=trace_file,
        status_file=None,
        log_dir=tmp_path,
        dll_dir=tmp_path / "Endfield Game",
        game_exe="Endfield.exe",
    )
    process = ProcessSpy()
    config.dll_dir.mkdir()
    (config.dll_dir / "GameAssembly.dll").write_bytes(b"actual-game-dll")
    metadata = config.dll_dir / "Endfield_Data/il2cpp_data/Metadata/global-metadata.dat"
    metadata.parent.mkdir(parents=True)
    metadata.write_bytes(b"actual-game-metadata")

    monkeypatch.setattr(runner, "DamageLogService", ServiceSpy)
    monkeypatch.setattr(runner, "bundle_root", lambda: tmp_path)
    monkeypatch.setattr(
        runner,
        "_launch_managed_uploader",
        lambda _trace, _game: events.append("uploader_launch") or process,
    )
    monkeypatch.setattr(
        runner.runpy,
        "run_path",
        lambda *_args, **_kwargs: events.append("overlay_run"),
    )

    assert runner.run_with_overlay(config) == 0
    assert events.index("service_started") < events.index("uploader_launch")
    assert events.index("uploader_launch") < events.index("overlay_run")
    assert "uploader_terminate" in events
    assert "service_stop" in events
    diagnostics = json.loads(trace_file.with_suffix(".log.overlay-startup.json").read_text(encoding="utf-8"))
    assert diagnostics["gameHashes"] == {
        "gameAssemblySha256": hashlib.sha256(b"actual-game-dll").hexdigest(),
        "metadataSha256": hashlib.sha256(b"actual-game-metadata").hexdigest(),
    }
    assert len(diagnostics["implementationSha256"]) == 64
    assert diagnostics["lifecycleEvidence"]["clientFinishExact"]["state"] == "unavailable"
    assert all(value is None for value in diagnostics["resourceHashes"].values())
    assert not any(key in diagnostics for key in ("session_id", "flow", "account", "packet"))


def test_runner_does_not_launch_uploader_when_service_start_fails(monkeypatch, tmp_path: Path) -> None:
    launched: list[bool] = []

    class FailingService:
        def __init__(self, config, observer=None) -> None:
            pass

        async def run(self) -> None:
            raise RuntimeError("npcap open failed")

        def wait_started(self, timeout=None) -> bool:
            return False

        def request_stop(self) -> None:
            pass

    config = SimpleNamespace(
        trace_file=tmp_path / "trace.log",
        status_file=None,
        log_dir=tmp_path,
        dll_dir=tmp_path / "Endfield Game",
        game_exe="Endfield.exe",
    )
    monkeypatch.setattr(runner, "DamageLogService", FailingService)
    monkeypatch.setattr(runner, "bundle_root", lambda: tmp_path)
    monkeypatch.setattr(
        runner,
        "_launch_managed_uploader",
        lambda *_args: launched.append(True),
    )

    with pytest.raises(RuntimeError, match="npcap open failed"):
        runner.run_with_overlay(config)
    assert launched == []


def test_optional_overlay_observer_failure_cannot_stop_original_capture(monkeypatch, tmp_path):
    import threading
    observed = threading.Event()
    calls = []

    class FaultyBroker:
        def on_state_change(self, *args):
            raise RuntimeError("optional context failed")

        def on_event(self, payload):
            raise RuntimeError("optional projection failed")

    class Capture:
        def __init__(self, config, observer=None):
            self.observer = observer
            self.stopped = False

        async def run(self):
            self.observer.on_event({"type": "test"})
            observed.set()
            while not self.stopped:
                await asyncio.sleep(.001)

        def wait_started(self, timeout=None):
            return observed.wait(timeout)

        def request_stop(self):
            self.stopped = True

    config = SimpleNamespace(trace_file=tmp_path / "trace.log", status_file=None, log_dir=tmp_path,
                             dll_dir=tmp_path, game_exe="Endfield.exe")
    monkeypatch.setattr(runner, "get_overlay_broker", lambda: FaultyBroker())
    monkeypatch.setattr(runner, "DamageLogService", Capture)
    monkeypatch.setattr(runner, "bundle_root", lambda: tmp_path)
    monkeypatch.setattr(runner, "_launch_managed_uploader", lambda *args: None)
    monkeypatch.setattr(runner.runpy, "run_path", lambda *args, **kwargs: calls.append("original_overlay"))
    assert runner.run_with_overlay(config) == 0
    assert calls == ["original_overlay"] and observed.is_set()
