from __future__ import annotations

from datetime import datetime
from pathlib import Path
import os
import subprocess
import sys
import traceback


def _bootstrap_paths() -> None:
    base_dir = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    candidates = [
        base_dir / "src",
        base_dir / "packages" / "parser_core",
        base_dir / "packages" / "uploader_core",
        base_dir.parent / "endfield-logs" / "apps" / "uploader",
    ]
    for candidate in candidates:
        candidate_str = str(candidate)
        if candidate.exists() and candidate_str not in sys.path:
            sys.path.insert(0, candidate_str)


_bootstrap_paths()

from endfield_pcap.cli import main
from endfield_pcap.game_path import ensure_configured_game_dir_interactive
from endfield_pcap.trace_bridge import make_archive_trace_file
from endfield_pcap.update import check_and_maybe_start_update


def _default_log_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "logs"
    return Path(__file__).resolve().parent / "logs"


def _ensure_desktop_shortcut_once() -> None:
    """First packaged run: drop a desktop shortcut to the client, then never again.

    Guarded by a marker file so it runs exactly once and never recreates a
    shortcut the user deliberately deleted. Best-effort — any failure is silent.
    """
    if not getattr(sys, "frozen", False) or not sys.platform.startswith("win"):
        return
    appdata_root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    marker = appdata_root / "EndfieldPCAP" / ".desktop_shortcut_done"
    if marker.exists():
        return
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        exe_path = Path(sys.executable).resolve()
        desktop = Path(os.environ.get("USERPROFILE") or Path.home()) / "Desktop"
        if not desktop.exists():
            marker.write_text("no-desktop", encoding="utf-8")
            return
        shortcut_path = desktop / "Endfield Logs 客户端.lnk"
        if not shortcut_path.exists():
            ps_script = (
                "$w = New-Object -ComObject WScript.Shell; "
                f"$s = $w.CreateShortcut('{shortcut_path}'); "
                f"$s.TargetPath = '{exe_path}'; "
                f"$s.WorkingDirectory = '{exe_path.parent}'; "
                f"$s.IconLocation = '{exe_path}'; "
                "$s.Save()"
            )
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                timeout=15,
                check=False,
            )
        marker.write_text("done", encoding="utf-8")
    except Exception:
        return


def _startup_log_path() -> Path:
    appdata_root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    return appdata_root / "EndfieldPCAP" / "logs" / "startup.log"


def _write_startup_log(message: str) -> Path:
    path = _startup_log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"[{stamp}] pid={os.getpid()} {message.rstrip()}\n")
    except OSError:
        pass
    return path


def _show_startup_message(title: str, message: str, *, error: bool = False) -> None:
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes

        flags = 0x10 if error else 0x30
        ctypes.windll.user32.MessageBoxW(None, message, title, flags)
    except Exception:
        return


def _prepare_default_launch() -> int | None:
    if check_and_maybe_start_update():
        return 0
    _ensure_desktop_shortcut_once()
    try:
        game_dir = ensure_configured_game_dir_interactive()
    except Exception as exc:  # noqa: BLE001 - frozen startup must surface the failure.
        details = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        log_path = _write_startup_log(f"game path setup failed\n{details}")
        _show_startup_message(
            "Endfield Logs 客户端启动失败",
            f"游戏路径初始化失败，客户端和上传器均未启动。\n\n错误：{type(exc).__name__}: {exc}\n\n日志：{log_path}",
            error=True,
        )
        return 1
    if game_dir is None:
        log_path = _write_startup_log("game path setup cancelled; startup aborted before uploader launch")
        _show_startup_message(
            "Endfield Logs 客户端未启动",
            "尚未选择有效的终末地游戏目录，客户端和上传器均未启动。\n\n"
            "请重新运行 EndfieldLogsClient.exe，并选择包含 Endfield.exe 和 GameAssembly.dll 的 Endfield Game 目录。\n\n"
            f"日志：{log_path}",
        )
        return 1

    log_dir = _default_log_dir().resolve()
    trace_file = make_archive_trace_file(log_dir)
    sys.argv.extend(
        [
            "serve",
            "--log-dir",
            str(log_dir),
            "--trace-file",
            str(trace_file),
            "--dll-dir",
            str(game_dir.resolve()),
        ]
    )
    return None


def run() -> int:
    # All desktop roles share this frozen executable and one dependency tree.
    # Dispatch before the default game/capture/update path: an offline verifier
    # or uploader must never start capture as a side effect.
    if len(sys.argv) > 1 and sys.argv[1] in {"--uploader", "--detector"}:
        role = sys.argv.pop(1)
        if role == "--uploader":
            os.environ["ENDFIELD_UPLOADER_SKIP_UPDATE_CHECK"] = "1"
            os.environ["ENDFIELD_LOGS_MANAGED_BY_CLIENT"] = "1"
            from app.main import main as uploader_main

            return int(uploader_main() or 0)
        from endfield_pcap.diagnostic import run_detector_app

        return int(run_detector_app() or 0)
    default_launch = len(sys.argv) == 1
    if default_launch:
        early_exit = _prepare_default_launch()
        if early_exit is not None:
            return early_exit
    try:
        return int(main() or 0)
    except SystemExit as exc:
        code = int(exc.code) if isinstance(exc.code, int) else 1
        if not default_launch or code == 0:
            return code
        message = str(exc.code or "客户端启动已中止。")
        log_path = _write_startup_log(f"startup exited code={code}: {message}")
        _show_startup_message(
            "Endfield Logs 客户端启动失败",
            f"{message}\n\n日志：{log_path}",
            error=True,
        )
        return code
    except BaseException as exc:  # noqa: BLE001 - frozen app has no console for startup errors.
        if not default_launch:
            raise
        details = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        log_path = _write_startup_log(f"unexpected startup failure\n{details}")
        _show_startup_message(
            "Endfield Logs 客户端启动失败",
            f"客户端启动失败，已停止本次启动且不会遗留上传器。\n\n"
            f"错误：{type(exc).__name__}: {exc}\n\n日志：{log_path}",
            error=True,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(run())
