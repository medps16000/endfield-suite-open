import argparse
import hashlib
import json
import logging
import os
import sys
import traceback
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from app.ui.assets import app_icon
from app.ui.main_window import MainWindow
from uploader_core.battle_payload_builder import build_battle_upload_payloads_from_log


def _configure_logging() -> None:
    appdata_root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".endfield-pcap")
    log_dir = appdata_root / "EndfieldPCAP" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=log_dir / "uploader.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        encoding="utf-8",
    )
    previous_hook = sys.excepthook

    def _log_unhandled_exception(exc_type, exc_value, exc_traceback) -> None:
        logging.getLogger(__name__).critical(
            "unhandled uploader exception",
            exc_info=(exc_type, exc_value, exc_traceback),
        )
        previous_hook(exc_type, exc_value, exc_traceback)

    sys.excepthook = _log_unhandled_exception


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="EndfieldLogsUploader", add_help=True)
    parser.add_argument("--open-log", dest="open_log", help="启动后直接载入指定日志文件。")
    parser.add_argument("--watch-log", dest="watch_log", help="启动后持续监听统一客户端自动归档的日志文件。")
    parser.add_argument("--auto-parse", dest="auto_parse", action="store_true", help="载入日志后自动开始解析。")
    parser.add_argument("--verify-log", help="仅本地解析日志并输出验收摘要；不验证原始签名、不登录、不上传。")
    parser.add_argument("--verification-output", help="验收摘要 JSON 路径；必须是尚不存在的新文件。")
    parser.add_argument(
        "--auto-upload-all",
        dest="auto_upload_all",
        action="store_true",
        help="解析完成后自动上传所有当前可上传的候选 battle。",
    )
    return parser


def main() -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(sys.argv[1:])

    if args.verify_log:
        if not args.verification_output:
            parser.error("--verify-log 必须同时指定 --verification-output。")
        if args.open_log or args.watch_log or args.auto_parse or args.auto_upload_all:
            parser.error("--verify-log 不能与界面载入、监听或自动上传参数同时使用。")
        source = Path(args.verify_log).expanduser().resolve()
        output = Path(args.verification_output).expanduser()
        if output.exists() or output.is_symlink():
            parser.error("验收输出文件已存在，拒绝覆盖。")
        output = output.resolve()
        if output == source:
            parser.error("验收输出不能与输入日志是同一文件。")
        if not source.is_file():
            parser.error("待验收的日志文件不存在。")
        if not output.parent.is_dir():
            parser.error("验收输出目录不存在。")
        try:
            source_signature = (source.stat().st_size, source.stat().st_mtime_ns)
            source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
            payloads = build_battle_upload_payloads_from_log(
                str(source), fast_unverified=True, include_source_metadata=True,
            )
            if source_signature != (source.stat().st_size, source.stat().st_mtime_ns):
                parser.error("日志在验收期间发生变化，请使用已完成的归档日志。")
            canonical = json.dumps(
                payloads, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
            ).encode("utf-8")
            battles = [payload["battle"] for payload in payloads]
            summary = {
                "schemaVersion": 1,
                "mode": "local_parse_only",
                "rawLogIntegrityVerified": False,
                "uploaded": False,
                "sourceFile": str(source),
                "sourceSha256": source_sha256,
                "payloadsSha256": hashlib.sha256(canonical).hexdigest(),
                "battleCount": len(payloads),
                "totalDamage": sum(int(battle.get("totalDamage") or 0) for battle in battles),
                "clearedBattleCount": sum(battle.get("clearFlag") is True for battle in battles),
                "timerEndBattleCount": sum(battle.get("timerEndSeen") is True for battle in battles),
                "officialTimerEndBattleCount": sum(battle.get("officialTimerEndSeen") is True for battle in battles),
                "timelineEventCount": sum(len(payload.get("timelineEvents") or []) for payload in payloads),
                "damageEventCount": sum(
                    event.get("laneType") == "skill" and (event.get("value") or 0) > 0
                    for payload in payloads for event in payload.get("timelineEvents") or []
                ),
                "parserVersions": sorted({str(battle["parserVersion"]) for battle in battles if battle.get("parserVersion")}),
                "rulesVersions": sorted({str(battle["rulesVersion"]) for battle in battles if battle.get("rulesVersion")}),
            }
            # Exclusive creation also prevents a concurrent writer from being overwritten.
            with output.open("x", encoding="utf-8") as handle:
                json.dump(summary, handle, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
                handle.write("\n")
        except (OSError, ValueError, TypeError) as exc:
            traceback.print_exc()
            parser.error(f"日志验收失败：{exc}")
        return 0
    if args.verification_output:
        parser.error("--verification-output 必须与 --verify-log 同时使用。")

    _configure_logging()
    app = QApplication(sys.argv[:1])
    app.setWindowIcon(app_icon())
    window = MainWindow()
    # The trusted settings panel controls the capture process over a private
    # per-launch pipe. HTML documents retain only the read-only data interface.
    if not getattr(sys, "frozen", False):
        source = Path(__file__).resolve().parents[4] / "endfield-pcap" / "src"
        if source.is_dir() and str(source) not in sys.path:
            sys.path.insert(0, str(source))
    from endfield_pcap.overlay_control import OverlayControlClient
    from endfield_pcap.overlay_window_settings import OverlaySettingsPage

    window.overlay_control = OverlayControlClient(parent=window)
    window.set_overlay_settings_view(OverlaySettingsPage(window.overlay_control, parent=window))

    def show_overlay_settings():
        window._restore_from_tray()
        window.show_workspace_page("overlays")

    window.overlay_control.openSettingsRequested.connect(show_overlay_settings)
    window.overlay_control.connectionChanged.connect(
        lambda _status: window.sidebar.set_overlay_status(
            "悬浮窗已连接" if window.overlay_control.snapshot.get("online") else
            ("离线设置" if window.overlay_control.snapshot.get("capabilities", {}).get("configuration") else "等待悬浮窗连接"),
        ),
    )
    window.sidebar.set_overlay_status(
        "离线设置" if window.overlay_control.snapshot.get("capabilities", {}).get("configuration")
        and not window.overlay_control.snapshot.get("online") else "等待悬浮窗连接",
    )
    app.aboutToQuit.connect(window.overlay_control.close)
    window.show()
    if args.watch_log:
        watched_log = str(Path(args.watch_log).expanduser())
        QTimer.singleShot(0, lambda: window.run_managed_log_workflow(watched_log))
    elif args.open_log:
        startup_log = str(Path(args.open_log).expanduser())
        QTimer.singleShot(
            0,
            lambda: window.run_startup_log_workflow(
                startup_log,
                auto_parse=args.auto_parse or args.auto_upload_all,
                auto_upload=args.auto_upload_all,
            ),
        )
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
