from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import runpy
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path

from .runtime_paths import bundle_root
from .models import ServiceObserver, ServiceState
from .overlay_topics import get_overlay_broker
from .service import DamageLogService, ServiceConfig
from .trace_bridge import make_archive_trace_file


def _launch_managed_uploader(trace_file: Path, game_exe: Path) -> subprocess.Popen | None:
    if os.environ.get("ENDFIELD_LOGS_DISABLE_UPLOADER") == "1":
        return None
    root = bundle_root()
    candidates = [
        Path(sys.executable).resolve() if getattr(sys, "frozen", False) else root / "EndfieldLogsClient.exe",
        root / "uploader" / "EndfieldLogsUploader" / "EndfieldLogsUploader.exe",
        root.parent / "uploader" / "EndfieldLogsUploader" / "EndfieldLogsUploader.exe",
    ]
    for candidate in candidates:
        if not candidate.exists():
            continue
        env = os.environ.copy()
        env["ENDFIELD_UPLOADER_SKIP_UPDATE_CHECK"] = "1"
        env["ENDFIELD_LOGS_MANAGED_BY_CLIENT"] = "1"
        env["ENDFIELD_GAME_EXE"] = str(game_exe)
        try:
            return subprocess.Popen(
                [str(candidate), *(["--uploader"] if getattr(sys, "frozen", False)
                                   and candidate == Path(sys.executable).resolve() else []),
                 "--watch-log", str(trace_file)],
                cwd=str(candidate.parent),
                env=env,
            )
        except OSError:
            return None
    return None


def _stop_managed_uploader(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
        except OSError:
            return


def _wait_for_service_start(
    service: DamageLogService,
    thread: threading.Thread,
    errors: list[BaseException],
    *,
    timeout: float = 8.0,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if service.wait_started(0.05):
            return
        if errors:
            raise errors[0]
        if not thread.is_alive():
            # The worker may have crossed its final instruction just before
            # publishing the captured exception. Join briefly, then prefer
            # the real startup cause over the generic lifecycle message.
            thread.join(timeout=0.05)
            if errors:
                raise errors[0]
            raise RuntimeError("采集服务在启动完成前意外退出。")
    raise TimeoutError("采集服务启动超时，上传器未启动。")


def run_with_overlay(config: ServiceConfig) -> int:
    root = bundle_root()
    trace_file = config.trace_file or make_archive_trace_file(config.log_dir)
    config.trace_file = trace_file
    status_file = config.status_file or trace_file.with_suffix(trace_file.suffix + ".status.json")
    config.status_file = status_file
    os.environ["ENDFIELD_PCAP_TRACE_FILE"] = str(trace_file)
    os.environ["ENDFIELD_PCAP_STATUS_FILE"] = str(status_file)
    os.environ.setdefault("ENDFIELD_LOGS_DATA_ROOT", str(root))
    os.environ.setdefault("ENDFIELD_LOGS_PARSER_CORE", str(root / "packages" / "parser_core"))
    # Only the trusted native uploader inherits this per-launch capability.
    # It is never part of the read-only HTML bridge, files or public service.
    previous_control = {key: os.environ.get(key) for key in (
        "ENDFIELD_OVERLAY_CONTROL_NAME", "ENDFIELD_OVERLAY_CONTROL_TOKEN",
    )}
    os.environ["ENDFIELD_OVERLAY_CONTROL_NAME"] = f"EndfieldOverlay-{os.getpid()}-{secrets.token_hex(12)}"
    os.environ["ENDFIELD_OVERLAY_CONTROL_TOKEN"] = secrets.token_hex(32)

    # Clock semantics must match the actual installed game, including metadata.
    # Missing or changed inputs disable the guide clock, leaving capture intact.
    for name, relative in (
        ("ENDFIELD_ROTATION_GAME_ASSEMBLY_SHA256", "GameAssembly.dll"),
        ("ENDFIELD_ROTATION_METADATA_SHA256", "Endfield_Data/il2cpp_data/Metadata/global-metadata.dat"),
    ):
        os.environ.pop(name, None)
        try:
            with (Path(config.dll_dir) / relative).open("rb") as stream:
                os.environ[name] = hashlib.file_digest(stream, "sha256").hexdigest()
        except OSError:
            pass

    broker = get_overlay_broker()
    operation_diagnostics = None
    operation_diagnostic_bytes = operation_diagnostic_count = 0
    if os.environ.get("ENDFIELD_OVERLAY_OPERATION_DIAGNOSTICS") == "1":
        try:
            trace_file.parent.mkdir(parents=True, exist_ok=True)
            operation_diagnostics = trace_file.with_suffix(trace_file.suffix + ".operation-headers.jsonl").open(
                "x", encoding="utf-8", buffering=65536)
        except OSError:
            logging.getLogger(__name__).warning("Could not open optional operation-header diagnostics", exc_info=True)
    # Persist what this process actually loaded, before capture begins. These
    # local diagnostics contain no account, actor, network or packet data.
    # Failure of an optional display diagnostic must never stop collection.
    try:
        try:
            lifecycle = broker.inspect("capture.context.v1").get("lifecycleEvidence", {})
        except Exception:
            lifecycle = {"state": "unavailable", "reason": "diagnostic_failed"}
        implementation = Path(sys.executable) if getattr(sys, "frozen", False) else Path(
            sys.modules[type(broker).__module__].__file__)
        loaded = {"schemaVersion": 1, "recordedAtMs": int(time.time() * 1000),
                  "frozen": bool(getattr(sys, "frozen", False)), "rawOperationSchemaVersion": 2,
                  "operationHeaderDiagnosticsEnabled": operation_diagnostics is not None,
                  "implementationSha256": hashlib.sha256(implementation.read_bytes()).hexdigest(),
                  "gameHashes": {key: os.environ.get(name) for key, name in (
                      ("gameAssemblySha256", "ENDFIELD_ROTATION_GAME_ASSEMBLY_SHA256"),
                      ("metadataSha256", "ENDFIELD_ROTATION_METADATA_SHA256"))},
                  "lifecycleEvidence": lifecycle, "resourceHashes": {}}
        for filename in ("effects.json", "descriptions.json", "targets.json", "native.json", "definitions.json"):
            path = root / "data" / "overlay" / filename
            if path.is_file():
                with path.open("rb") as stream:
                    loaded["resourceHashes"][filename] = hashlib.file_digest(stream, "sha256").hexdigest()
            else:
                loaded["resourceHashes"][filename] = None
        trace_file.parent.mkdir(parents=True, exist_ok=True)
        trace_file.with_suffix(trace_file.suffix + ".overlay-startup.json").write_text(
            json.dumps(loaded, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except Exception:
        logging.getLogger(__name__).warning("Could not save optional overlay startup diagnostics", exc_info=True)

    def overlay_state(state, session_id, flow) -> None:
        try:
            broker.on_state_change(state, session_id, flow)
        except Exception:
            logging.getLogger(__name__).exception("Read-only overlay context update failed")

    def overlay_event(payload) -> None:
        nonlocal operation_diagnostics, operation_diagnostic_bytes, operation_diagnostic_count
        try:
            # Opt-in, bounded metadata only. Never record the operation body,
            # credentials, account/actor identities or network endpoints here.
            if operation_diagnostics is not None and payload.get("type") == "CAPTURE_BATTLE_OPERATION":
                record = {key: payload.get(key) for key in (
                    "type", "session_id", "timestamp_ms", "seq_id", "operation_type", "op_type",
                    "source_index", "operation_sha256", "capture_integrity")}
                encoded = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
                if operation_diagnostic_bytes + len(encoded.encode("utf-8")) <= 16 * 1024 * 1024:
                    try:
                        operation_diagnostics.write(encoded)
                        operation_diagnostic_bytes += len(encoded.encode("utf-8"))
                        operation_diagnostic_count += 1
                        if operation_diagnostic_count % 64 == 0:
                            operation_diagnostics.flush()
                    except OSError:
                        try:
                            operation_diagnostics.close()
                        except OSError:
                            pass
                        operation_diagnostics = None
                        logging.getLogger(__name__).warning("Optional operation-header diagnostics stopped")
            broker.on_event(payload)
        except Exception:
            # The optional display projection must never stop original capture,
            # trace writing, statistics or the uploader's normal lifecycle.
            logging.getLogger(__name__).exception("Read-only overlay projection failed")
            overlay_state(ServiceState.WAITING_CONNECTION, None, None)

    overlay_state(ServiceState.WAITING_GAME, None, None)
    service = DamageLogService(config, observer=ServiceObserver(
        on_event=overlay_event, on_state_change=overlay_state,
    ))
    error: list[BaseException] = []

    def service_main() -> None:
        try:
            asyncio.run(service.run())
        except BaseException as exc:  # noqa: BLE001 - surface in caller.
            error.append(exc)

    thread = threading.Thread(target=service_main, name="endfield-pcap-service", daemon=True)
    thread.start()
    uploader_process: subprocess.Popen | None = None
    try:
        _wait_for_service_start(service, thread, error)
        game_exe = (Path(config.dll_dir) / config.game_exe).resolve()
        uploader_process = _launch_managed_uploader(trace_file, game_exe)
        runpy.run_path(str(Path(root) / "overlay.py"), run_name="__main__")
    finally:
        _stop_managed_uploader(uploader_process)
        service.request_stop()
        thread.join(timeout=5)
        overlay_state(ServiceState.WAITING_CONNECTION, None, None)
        for key, value in previous_control.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        if operation_diagnostics is not None:
            try:
                operation_diagnostics.close()
            except OSError:
                logging.getLogger(__name__).warning("Optional operation-header diagnostics could not finish")
    if error:
        raise error[0]
    return 0
