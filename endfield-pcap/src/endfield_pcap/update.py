from __future__ import annotations

import hashlib
import http.client
import json
import logging
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from typing import Callable
import urllib.error
import urllib.parse
import urllib.request
import zipfile

from .runtime_paths import app_root, bundle_root
from .component_updates import prepare_component_package


LOGGER = logging.getLogger(__name__)
DEFAULT_MANIFEST_URL = "https://zmdlogs.com/client/latest.json"
UPDATE_DIR_NAME = "updates"
UPDATER_EXE_NAME = "EndfieldLogsUpdater.exe"
ProgressCallback = Callable[[int, int | None], None]


def _appdata_root() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".endfield-pcap") / "EndfieldPCAP"


def _updates_root() -> Path:
    root = _appdata_root() / UPDATE_DIR_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def _read_version_payload() -> dict[str, object]:
    version_path = bundle_root() / "version.json"
    if version_path.is_dir():
        version_path = version_path / "version.json"
    if not version_path.exists():
        return {}
    try:
        return json.loads(version_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        LOGGER.warning("failed to read version metadata from %s", version_path, exc_info=True)
        return {}


def _version_key(value: object) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in str(value or "").replace("-", ".").replace("_", ".").split("."):
        digits = "".join(char for char in chunk if char.isdigit())
        if digits:
            parts.append(int(digits))
    return tuple(parts or [0])


def _is_remote_newer(local_version: object, local_build: object, remote: dict[str, object]) -> bool:
    remote_version = remote.get("version")
    remote_build = remote.get("build")
    if _version_key(remote_version) != _version_key(local_version):
        return _version_key(remote_version) > _version_key(local_version)
    return _version_key(remote_build) > _version_key(local_build)


def _version_matches(value: object, expected: object) -> bool:
    return _version_key(value) == _version_key(expected)


def _is_patch_compatible(patch: dict[str, object], *, local_version: str, local_build: str) -> bool:
    if not str(patch.get("url") or "").strip() or not str(patch.get("sha256") or "").strip():
        return False
    from_version = patch.get("fromVersion")
    if from_version not in (None, "") and not _version_matches(local_version, from_version):
        return False
    from_build = patch.get("fromBuild")
    if from_build not in (None, "") and not _version_matches(local_build, from_build):
        return False
    min_version = patch.get("minVersion")
    if min_version not in (None, "") and _version_key(local_version) < _version_key(min_version):
        return False
    max_version = patch.get("maxVersion")
    if max_version not in (None, "") and _version_key(local_version) > _version_key(max_version):
        return False
    min_build = patch.get("minBuild")
    if min_build not in (None, "") and _version_key(local_build) < _version_key(min_build):
        return False
    max_build = patch.get("maxBuild")
    if max_build not in (None, "") and _version_key(local_build) > _version_key(max_build):
        return False
    return True


def _select_patch_manifest(
    manifest: dict[str, object],
    *,
    local_version: str,
    local_build: str,
) -> dict[str, object] | None:
    candidates: list[object] = []
    patches = manifest.get("patches")
    if isinstance(patches, list):
        candidates.extend(patches)
    patch = manifest.get("patch")
    if isinstance(patch, dict):
        candidates.insert(0, patch)
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        if _is_patch_compatible(candidate, local_version=local_version, local_build=local_build):
            return candidate
    return None


def _manifest_url() -> str:
    return str(os.environ.get("ENDFIELD_LOGS_UPDATE_URL") or DEFAULT_MANIFEST_URL)


def _patch_blocked_by_marker(remote_version: str, remote_build: str) -> bool:
    """True when a previous patch attempt for this remote version failed.

    The updater writes the marker on any patch-apply failure (base drift,
    locked files, corrupt archive) so the retry goes straight to the full
    package instead of looping on the same broken patch.
    """
    marker = _updates_root() / "patch_fallback.json"
    if not marker.exists():
        return False
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    marked_version = str(payload.get("version") or "*")
    marked_build = str(payload.get("build") or "*")
    version_matches = marked_version == "*" or _version_matches(marked_version, remote_version)
    build_matches = marked_build == "*" or _version_matches(marked_build, remote_build)
    if version_matches and build_matches:
        LOGGER.info("patch skipped due to fallback marker (reason=%s)", payload.get("reason"))
        return True
    # Marker is for an older release; a new patch may work again.
    try:
        marker.unlink(missing_ok=True)
    except OSError:
        pass
    return False


def _fetch_json(url: str, *, timeout: float = 5.0) -> dict[str, object]:
    parsed = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query.append(("_", str(int(time.time() * 1000))))
    fresh_url = urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(query), parsed.fragment)
    )
    request = urllib.request.Request(
        fresh_url,
        headers={
            "User-Agent": "EndfieldLogsClient-Updater/1",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - URL is configured by app/env.
        data = response.read(512 * 1024)
    payload = json.loads(data.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("manifest root must be an object")
    return payload


def _format_bytes(value: int | float | None) -> str:
    size = float(value or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024.0 or unit == "GB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} GB"


class _DownloadProgressDialog:
    def __init__(self, *, remote_version: str, remote_build: str) -> None:
        import tkinter as tk
        from tkinter import ttk

        self._tk = tk
        self._root = tk.Tk()
        self._root.title("正在更新")
        self._root.resizable(False, False)
        try:
            self._root.attributes("-topmost", True)
        except tk.TclError:
            pass

        frame = ttk.Frame(self._root, padding=18)
        frame.grid(row=0, column=0, sticky="nsew")
        ttk.Label(frame, text=f"正在下载更新 {remote_version} ({remote_build})").grid(
            row=0,
            column=0,
            sticky="w",
        )
        self._status = ttk.Label(frame, text="正在连接服务器...")
        self._status.grid(row=1, column=0, sticky="w", pady=(8, 0))
        self._bar = ttk.Progressbar(frame, orient="horizontal", length=360, mode="determinate", maximum=100)
        self._bar.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        self._detail = ttk.Label(frame, text="")
        self._detail.grid(row=3, column=0, sticky="w", pady=(8, 0))
        self._indeterminate = False
        self._closed = False
        self._root.update_idletasks()
        self._root.update()

    def update(self, downloaded: int, total: int | None) -> None:
        if self._closed:
            return
        try:
            if total and total > 0:
                if self._indeterminate:
                    self._bar.stop()
                    self._bar.configure(mode="determinate")
                    self._indeterminate = False
                percent = min(100.0, max(0.0, downloaded / total * 100.0))
                self._bar["value"] = percent
                self._status.configure(text=f"正在下载更新包... {percent:.0f}%")
                self._detail.configure(text=f"{_format_bytes(downloaded)} / {_format_bytes(total)}")
            else:
                if not self._indeterminate:
                    self._bar.configure(mode="indeterminate")
                    self._bar.start(12)
                    self._indeterminate = True
                self._status.configure(text="正在下载更新包...")
                self._detail.configure(text=f"已下载 {_format_bytes(downloaded)}")
            self._root.update_idletasks()
            self._root.update()
        except Exception:  # noqa: BLE001 - progress UI must not break updates.
            LOGGER.debug("failed to update download progress UI", exc_info=True)

    def set_status(self, status: str, detail: str = "") -> None:
        if self._closed:
            return
        try:
            if self._indeterminate:
                self._bar.stop()
                self._bar.configure(mode="determinate")
                self._indeterminate = False
            self._bar["value"] = 100
            self._status.configure(text=status)
            self._detail.configure(text=detail)
            self._root.update_idletasks()
            self._root.update()
        except Exception:  # noqa: BLE001
            LOGGER.debug("failed to update download progress UI", exc_info=True)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self._indeterminate:
                self._bar.stop()
            self._root.destroy()
        except Exception:  # noqa: BLE001
            LOGGER.debug("failed to close download progress UI", exc_info=True)


_DOWNLOAD_RETRY_EXCEPTIONS = (
    OSError,
    TimeoutError,
    urllib.error.URLError,
    http.client.HTTPException,
    socket.timeout,
)


def _download_file_once(
    url: str,
    target: Path,
    *,
    timeout: float = 30.0,
    progress: ProgressCallback | None = None,
) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "EndfieldLogsClient-Updater/1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - URL is configured by manifest.
        total: int | None = None
        try:
            content_length = response.headers.get("Content-Length")
            total = int(content_length) if content_length else None
        except (TypeError, ValueError):
            total = None
        downloaded = 0
        if progress is not None:
            progress(downloaded, total)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb") as file:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                file.write(chunk)
                downloaded += len(chunk)
                if progress is not None:
                    progress(downloaded, total)


def _download_file(
    url: str,
    target: Path,
    *,
    timeout: float = 30.0,
    attempts: int = 3,
    retry_delay_sec: float = 1.5,
    progress: ProgressCallback | None = None,
) -> None:
    last_exc: BaseException | None = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            target.unlink(missing_ok=True)
            _download_file_once(url, target, timeout=timeout, progress=progress)
            return
        except _DOWNLOAD_RETRY_EXCEPTIONS as exc:
            last_exc = exc
            LOGGER.warning("download failed attempt=%d/%d url=%s", attempt, attempts, url, exc_info=True)
            try:
                target.unlink(missing_ok=True)
            except OSError:
                LOGGER.debug("failed to remove partial download %s", target, exc_info=True)
            if attempt < attempts:
                time.sleep(retry_delay_sec * attempt)
    if last_exc is not None:
        raise last_exc


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _updater_source_dir() -> Path | None:
    for root in (bundle_root(), app_root()):
        candidate = root / "updater" / "EndfieldLogsUpdater"
        if (candidate / UPDATER_EXE_NAME).exists():
            return candidate
    return None


def _prepare_updater() -> Path | None:
    source_dir = _updater_source_dir()
    if source_dir is None:
        LOGGER.warning("updater executable is missing from packaged client")
        return None
    target_parent = _updates_root() / "updater"
    target_parent.mkdir(parents=True, exist_ok=True)
    for stale_dir in target_parent.glob("EndfieldLogsUpdater*"):
        try:
            shutil.rmtree(stale_dir)
        except OSError:
            LOGGER.debug("failed to remove stale updater cache %s", stale_dir, exc_info=True)
    target_dir = target_parent / f"EndfieldLogsUpdater-{os.getpid()}-{int(time.time() * 1000)}"
    try:
        shutil.copytree(source_dir, target_dir)
    except OSError:
        LOGGER.warning("failed to prepare updater in %s", target_dir, exc_info=True)
        return None
    updater_exe = target_dir / UPDATER_EXE_NAME
    return updater_exe if updater_exe.exists() else None


def _show_update_prompt(manifest: dict[str, object], *, local_version: str, local_build: str) -> bool | None:
    import tkinter as tk
    from tkinter import messagebox

    remote_version = str(manifest.get("version") or "")
    remote_build = str(manifest.get("build") or "")
    notes = str(manifest.get("notes") or "").strip()
    force = bool(manifest.get("force"))
    lines = [
        f"当前版本：{local_version} ({local_build})",
        f"最新版本：{remote_version} ({remote_build})",
    ]
    if notes:
        lines.extend(["", notes])
    lines.extend(["", "现在更新会关闭客户端，更新完成后自动重启。"])

    root = tk.Tk()
    root.withdraw()
    try:
        try:
            root.attributes("-topmost", True)
        except tk.TclError:
            pass
        if force:
            accepted = messagebox.askokcancel("需要更新", "\n".join(lines), parent=root)
            return True if accepted else None
        return messagebox.askyesno("发现新版本", "\n".join(lines), parent=root)
    finally:
        root.destroy()


def _show_error(message: str) -> None:
    import tkinter as tk
    from tkinter import messagebox

    root = tk.Tk()
    root.withdraw()
    try:
        messagebox.showerror("更新失败", message, parent=root)
    finally:
        root.destroy()


def _download_update_artifact(
    artifact: dict[str, object],
    *,
    remote_version: str,
    remote_build: str,
) -> tuple[Path, str, str]:
    package_url = str(artifact.get("url") or "").strip()
    expected_sha256 = str(artifact.get("sha256") or "").strip().lower()
    if not package_url or not expected_sha256:
        raise ValueError("更新清单缺少下载地址或 sha256 校验值。")

    package_path = _updates_root() / Path(package_url.split("?")[0]).name
    progress_dialog: _DownloadProgressDialog | None = None
    try:
        if not package_path.exists() or _sha256_file(package_path) != expected_sha256:
            progress_dialog = _DownloadProgressDialog(remote_version=remote_version, remote_build=remote_build)
            _download_file(package_url, package_path, progress=progress_dialog.update)
            progress_dialog.set_status("正在校验更新包...", package_path.name)
        actual_sha256 = _sha256_file(package_path)
    finally:
        if progress_dialog is not None:
            progress_dialog.close()
    return package_path, expected_sha256, actual_sha256


def check_and_maybe_start_update() -> bool:
    """Return True when an updater was launched or startup should stop."""
    if os.environ.get("ENDFIELD_LOGS_DISABLE_UPDATE") == "1":
        return False
    if not getattr(sys, "frozen", False) and os.environ.get("ENDFIELD_LOGS_ENABLE_DEV_UPDATE") != "1":
        return False

    version_payload = _read_version_payload()
    local_version = str(version_payload.get("version") or "0.0.0")
    local_build = str(version_payload.get("build") or "0")

    try:
        manifest = _fetch_json(_manifest_url())
    except (OSError, urllib.error.URLError, json.JSONDecodeError, ValueError):
        LOGGER.info("update check skipped because manifest fetch failed", exc_info=True)
        return False

    if not _is_remote_newer(local_version, local_build, manifest):
        return False

    decision = _show_update_prompt(manifest, local_version=local_version, local_build=local_build)
    if decision is None:
        return True
    if not decision:
        return False

    remote_version = str(manifest.get("version") or "")
    remote_build = str(manifest.get("build") or "")
    patch_manifest = _select_patch_manifest(manifest, local_version=local_version, local_build=local_build)
    if patch_manifest is not None and _patch_blocked_by_marker(remote_version, remote_build):
        patch_manifest = None
    update_artifact = patch_manifest or manifest
    update_arg = "--patch" if patch_manifest is not None else "--package"
    package_path: Path | None = None
    expected_sha256 = ""
    actual_sha256 = ""
    component_descriptor = manifest.get("components")
    if isinstance(component_descriptor, dict):
        try:
            def fetch_component(artifact: dict) -> Path:
                downloaded, expected, actual = _download_update_artifact(
                    artifact, remote_version=remote_version, remote_build=remote_build,
                )
                if actual != expected:
                    raise ValueError("组件下载校验失败。")
                return downloaded

            package_path, actual_sha256, component_report = prepare_component_package(
                component_descriptor, app_root(), _updates_root() / "components",
                fetch_component, expected_version=remote_version, expected_build=remote_build,
            )
            expected_sha256 = actual_sha256
            update_arg = "--package"
            LOGGER.info("component update staged: reused=%s downloaded=%s repaired=%s",
                        component_report["reused"], component_report["downloaded"], component_report["repaired"])
        except (OSError, ValueError, zipfile.BadZipFile, urllib.error.URLError, http.client.HTTPException) as exc:
            # Never change an active component in place. Any unsupported ABI,
            # missing baseline or corrupt payload takes the proven full path.
            LOGGER.warning("component update unavailable; using full package (%s)", type(exc).__name__)
            package_path = None
            update_artifact = manifest
            patch_manifest = None
            update_arg = "--package"
    try:
        if package_path is None:
            package_path, expected_sha256, actual_sha256 = _download_update_artifact(
                update_artifact,
                remote_version=remote_version,
                remote_build=remote_build,
            )
    except (OSError, urllib.error.URLError, ValueError, http.client.HTTPException) as exc:
        LOGGER.warning("failed to download update package", exc_info=True)
        if patch_manifest is not None:
            LOGGER.warning("patch download failed; falling back to full package")
            update_artifact = manifest
            update_arg = "--package"
            try:
                package_path, expected_sha256, actual_sha256 = _download_update_artifact(
                    update_artifact,
                    remote_version=remote_version,
                    remote_build=remote_build,
                )
            except (OSError, urllib.error.URLError, ValueError, http.client.HTTPException) as fallback_exc:
                LOGGER.warning("failed to download full update package", exc_info=True)
                _show_error(f"下载更新失败：{fallback_exc}")
                return bool(manifest.get("force"))
        else:
            _show_error(f"下载更新失败：{exc}")
            return bool(manifest.get("force"))

    if package_path is None:
        _show_error("下载更新失败：未能获取更新包路径。")
        return bool(manifest.get("force"))

    if actual_sha256 != expected_sha256:
        try:
            package_path.unlink(missing_ok=True)
        except OSError:
            pass
        if update_arg == "--patch":
            LOGGER.warning("patch checksum mismatch; falling back to full package")
            update_arg = "--package"
            try:
                package_path, expected_sha256, actual_sha256 = _download_update_artifact(
                    manifest,
                    remote_version=remote_version,
                    remote_build=remote_build,
                )
            except (OSError, urllib.error.URLError, ValueError, http.client.HTTPException) as exc:
                LOGGER.warning("failed to download full update package", exc_info=True)
                _show_error(f"下载更新失败：{exc}")
                return bool(manifest.get("force"))
            if actual_sha256 != expected_sha256:
                try:
                    package_path.unlink(missing_ok=True)
                except OSError:
                    pass
                _show_error("更新包校验失败，已取消更新。")
                return bool(manifest.get("force"))
        else:
            _show_error("更新包校验失败，已取消更新。")
            return bool(manifest.get("force"))

    updater_exe = _prepare_updater()
    if updater_exe is None:
        _show_error("更新器缺失，请重新下载完整客户端。")
        return bool(manifest.get("force"))

    command = [
        str(updater_exe),
        update_arg,
        str(package_path),
        "--target-dir",
        str(app_root()),
        "--exe-name",
        Path(sys.executable).name,
        "--pid",
        str(os.getpid()),
    ]
    try:
        subprocess.Popen(command, cwd=str(updater_exe.parent))
    except OSError as exc:
        LOGGER.warning("failed to launch updater", exc_info=True)
        _show_error(f"无法启动更新器：{exc}")
        return bool(manifest.get("force"))
    return True
