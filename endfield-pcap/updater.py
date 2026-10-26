from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import zipfile


SYNCHRONIZE = 0x00100000
WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102
PATCH_MANIFEST_NAME = "patch_manifest.json"
PATCH_PAYLOAD_PREFIX = "payload/"
PATCH_FALLBACK_MARKER_NAME = "patch_fallback.json"


class PatchBaseMismatchError(RuntimeError):
    """本地安装与增量基线不一致，必须走完整包。"""


def _appdata_root() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".endfield-pcap") / "EndfieldPCAP"


def _log(message: str) -> None:
    log_path = _appdata_root() / "logs" / "updater.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    with log_path.open("a", encoding="utf-8") as file:
        file.write(f"{timestamp} {message}\n")


def _show_message(title: str, message: str, *, error: bool = False) -> None:
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        try:
            if error:
                messagebox.showerror(title, message, parent=root)
            else:
                messagebox.showinfo(title, message, parent=root)
        finally:
            root.destroy()
    except Exception:
        _log(f"{title}: {message}")


class _ApplyProgressDialog:
    def __init__(self) -> None:
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
        ttk.Label(frame, text="正在应用更新").grid(row=0, column=0, sticky="w")
        self._status = ttk.Label(frame, text="正在等待客户端关闭...")
        self._status.grid(row=1, column=0, sticky="w", pady=(8, 0))
        self._bar = ttk.Progressbar(frame, orient="horizontal", length=360, maximum=100, mode="determinate")
        self._bar.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        self._detail = ttk.Label(frame, text="")
        self._detail.grid(row=3, column=0, sticky="w", pady=(8, 0))
        self._closed = False
        self.update("正在等待客户端关闭...", 5)

    def update(self, status: str, percent: float, detail: str = "") -> None:
        if self._closed:
            return
        try:
            self._status.configure(text=status)
            self._bar["value"] = min(100.0, max(0.0, percent))
            self._detail.configure(text=detail)
            self._root.update_idletasks()
            self._root.update()
        except Exception as exc:  # noqa: BLE001 - progress UI must not break updates.
            _log(f"progress update failed: {type(exc).__name__}: {exc}")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._root.destroy()
        except Exception as exc:  # noqa: BLE001
            _log(f"progress close failed: {type(exc).__name__}: {exc}")


def _wait_for_pid(pid: int, timeout_sec: int = 45) -> None:
    if pid <= 0 or sys.platform != "win32":
        time.sleep(1.0)
        return
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(SYNCHRONIZE, False, wintypes.DWORD(pid))
    if not handle:
        time.sleep(1.0)
        return
    try:
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            result = kernel32.WaitForSingleObject(handle, 500)
            if result == WAIT_OBJECT_0:
                return
            if result != WAIT_TIMEOUT:
                return
    finally:
        kernel32.CloseHandle(handle)


def _find_package_root(extract_dir: Path) -> Path:
    direct = extract_dir / "EndfieldLogsClient"
    if (direct / "EndfieldLogsClient.exe").exists():
        return direct
    matches = [path for path in extract_dir.iterdir() if path.is_dir() and (path / "EndfieldLogsClient.exe").exists()]
    if len(matches) == 1:
        return matches[0]
    if (extract_dir / "EndfieldLogsClient.exe").exists():
        return extract_dir
    raise RuntimeError("更新包结构无效，未找到 EndfieldLogsClient.exe。")


def _subprocess_creationflags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def _terminate_processes_under(target_dir: Path) -> None:
    if os.name != "nt":
        return
    target = str(target_dir.resolve())
    script = r"""
$target = [System.IO.Path]::GetFullPath($args[0]).TrimEnd('\')
$targetPrefix = $target + '\'
function Test-UnderTarget([string]$path) {
  if (-not $path) { return $false }
  try {
    $full = [System.IO.Path]::GetFullPath($path)
  } catch {
    return $false
  }
  return $full.Equals($target, [System.StringComparison]::OrdinalIgnoreCase) -or
    $full.StartsWith($targetPrefix, [System.StringComparison]::OrdinalIgnoreCase)
}
Get-CimInstance Win32_Process | Where-Object {
  Test-UnderTarget $_.ExecutablePath
} | ForEach-Object {
  try { Invoke-CimMethod -InputObject $_ -MethodName Terminate | Out-Null } catch {}
}
"""
    try:
        subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
                target,
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=8,
            creationflags=_subprocess_creationflags(),
        )
    except Exception as exc:  # noqa: BLE001 - best effort only.
        _log(f"path-scoped process termination failed: {type(exc).__name__}: {exc}")


def _rename_with_retries(source: Path, target: Path, *, attempts: int = 12, delay_sec: float = 1.0) -> None:
    last_exc: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            source.rename(target)
            return
        except PermissionError as exc:
            last_exc = exc
            _log(f"rename blocked attempt={attempt}/{attempts}: {exc}")
            _terminate_processes_under(source)
            time.sleep(delay_sec)
        except OSError as exc:
            last_exc = exc
            _log(f"rename failed attempt={attempt}/{attempts}: {type(exc).__name__}: {exc}")
            time.sleep(delay_sec)
    raise RuntimeError(
        "无法替换客户端目录：旧客户端、内置上传器或安全软件仍在占用安装目录。"
        "请关闭 EndfieldLogsClient / EndfieldLogsUploader 后重新启动客户端更新。"
    ) from last_exc


def _extract_package(package_path: Path, extract_dir: Path, progress: _ApplyProgressDialog | None = None) -> None:
    extract_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(package_path) as archive:
        members = archive.infolist()
        total_size = sum(max(0, int(member.file_size or 0)) for member in members)
        extracted_size = 0
        if progress is not None:
            progress.update("正在解压更新包...", 12, package_path.name)
        for index, member in enumerate(members, start=1):
            archive.extract(member, extract_dir)
            extracted_size += max(0, int(member.file_size or 0))
            if progress is None:
                continue
            if total_size > 0:
                fraction = extracted_size / total_size
            else:
                fraction = index / max(1, len(members))
            progress.update(
                "正在解压更新包...",
                12 + fraction * 58,
                f"{index}/{len(members)}",
            )


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _patch_fallback_marker_path() -> Path:
    return _appdata_root() / "updates" / PATCH_FALLBACK_MARKER_NAME


def _write_patch_fallback_marker(version: str, build: str, reason: str) -> None:
    marker = _patch_fallback_marker_path()
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            json.dumps({"version": version, "build": build, "reason": reason}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        _log(f"patch fallback marker written: version={version} build={build} reason={reason}")
    except OSError as exc:
        _log(f"failed to write patch fallback marker: {exc}")


def _clear_patch_fallback_marker() -> None:
    try:
        _patch_fallback_marker_path().unlink(missing_ok=True)
    except OSError as exc:
        _log(f"failed to clear patch fallback marker: {exc}")


def _retry_file_op(operation, description: str, *, target_dir: Path, attempts: int = 8, delay_sec: float = 1.0) -> None:
    """Run a file operation, retrying while the install dir is still locked.

    Terminate is asynchronous — the uploader/overlay may hold locks for a few
    seconds after being killed, so a single attempt regularly fails with
    PermissionError. That was the main source of broken partial updates.
    """
    last_exc: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            operation()
            return
        except PermissionError as exc:
            last_exc = exc
            _log(f"file op blocked attempt={attempt}/{attempts} ({description}): {exc}")
            _terminate_processes_under(target_dir)
            time.sleep(delay_sec)
        except OSError as exc:
            last_exc = exc
            _log(f"file op failed attempt={attempt}/{attempts} ({description}): {type(exc).__name__}: {exc}")
            time.sleep(delay_sec)
    raise RuntimeError(f"文件被占用，无法更新：{description}") from last_exc


def _verify_patch_base(
    target_dir: Path,
    manifest: dict[str, object],
    progress: _ApplyProgressDialog | None = None,
) -> None:
    """Verify the install matches the patch's base before touching anything."""
    base_files = manifest.get("baseFiles")
    if not isinstance(base_files, dict) or not base_files:
        return
    items = sorted(base_files.items())
    for index, (raw_path, expected) in enumerate(items, start=1):
        relative_path = _safe_relative_path(raw_path)
        expected_sha256 = str(expected or "").strip().lower()
        if len(expected_sha256) != 64:
            continue
        target = _safe_child_path(target_dir, relative_path)
        if not target.is_file():
            raise PatchBaseMismatchError(f"本地文件缺失，安装与增量基线不一致：{relative_path}")
        if _sha256_file(target) != expected_sha256:
            raise PatchBaseMismatchError(f"本地文件与增量基线不一致：{relative_path}")
        if progress is not None:
            progress.update("正在校验本地基线...", 8 + index / len(items) * 12, relative_path)


def _verify_patch_result(target_dir: Path, manifest: dict[str, object]) -> None:
    files = manifest.get("files") or []
    assert isinstance(files, list)
    for item in files:
        if not isinstance(item, dict):
            continue
        relative_path = _safe_relative_path(item.get("path"))
        expected_sha256 = str(item.get("sha256") or "").strip().lower()
        target = _safe_child_path(target_dir, relative_path)
        if not target.is_file() or _sha256_file(target) != expected_sha256:
            raise RuntimeError(f"增量更新落盘校验失败：{relative_path}")


def _cleanup_old_backups(target_dir: Path, *, keep: int = 1) -> None:
    """Full updates leave <name>.old-<ts> siblings (~1GB each) forever; prune them."""
    try:
        backups = sorted(
            (
                path
                for path in target_dir.parent.iterdir()
                if path.is_dir() and path.name.startswith(f"{target_dir.name}.old-")
            ),
            key=lambda path: path.name,
        )
        for stale in backups[: max(0, len(backups) - keep)]:
            shutil.rmtree(stale, ignore_errors=True)
            _log(f"pruned old backup {stale}")
    except OSError as exc:
        _log(f"backup cleanup failed: {exc}")


def _safe_relative_path(value: object) -> str:
    text = str(value or "").replace("\\", "/").strip()
    if not text or text.startswith("/") or ":" in text:
        raise RuntimeError(f"增量更新包包含非法路径：{value!r}")
    path = Path(text)
    if path.is_absolute() or any(part in ("", ".", "..") for part in text.split("/")):
        raise RuntimeError(f"增量更新包包含非法路径：{value!r}")
    return text


def _safe_child_path(root: Path, relative_path: str) -> Path:
    root = root.resolve()
    target = (root / Path(*relative_path.split("/"))).resolve()
    root_text = str(root)
    target_text = str(target)
    if target_text != root_text and not target_text.startswith(root_text + os.sep):
        raise RuntimeError(f"增量更新包路径越界：{relative_path}")
    return target


def _copy_existing_for_restore(target: Path, backup: Path) -> None:
    backup.parent.mkdir(parents=True, exist_ok=True)
    if target.is_dir():
        shutil.copytree(target, backup)
        return
    shutil.copy2(target, backup)


def _restore_patch_backup(target_dir: Path, backup_dir: Path, touched: list[tuple[str, bool]]) -> None:
    for relative_path, existed in reversed(touched):
        target = _safe_child_path(target_dir, relative_path)
        backup = _safe_child_path(backup_dir, relative_path)
        try:
            if existed and backup.exists():
                if target.is_dir():
                    shutil.rmtree(target, ignore_errors=True)
                elif target.exists():
                    target.unlink()
                target.parent.mkdir(parents=True, exist_ok=True)
                if backup.is_dir():
                    shutil.copytree(backup, target)
                else:
                    shutil.copy2(backup, target)
            elif not existed:
                if target.is_dir():
                    shutil.rmtree(target, ignore_errors=True)
                else:
                    target.unlink(missing_ok=True)
        except Exception as exc:  # noqa: BLE001 - best effort rollback.
            _log(f"patch rollback failed for {relative_path}: {type(exc).__name__}: {exc}")


def _read_patch_identity(patch_path: Path) -> tuple[str, str]:
    """Best-effort (version, build) from a patch archive; '*' matches any."""
    try:
        with zipfile.ZipFile(patch_path) as archive:
            payload = json.loads(archive.read(PATCH_MANIFEST_NAME).decode("utf-8"))
        return str(payload.get("version") or "*"), str(payload.get("build") or "*")
    except Exception:  # noqa: BLE001 - marker must be written even for corrupt archives.
        return "*", "*"


def _load_patch_manifest(archive: zipfile.ZipFile) -> dict[str, object]:
    try:
        data = archive.read(PATCH_MANIFEST_NAME)
    except KeyError as exc:
        raise RuntimeError("增量更新包缺少 patch_manifest.json。") from exc
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("增量更新包清单无效。") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("增量更新包清单无效。")
    if payload.get("kind") != "endfield_partial_update":
        raise RuntimeError("增量更新包类型无效。")
    files = payload.get("files")
    if not isinstance(files, list):
        raise RuntimeError("增量更新包清单缺少 files。")
    delete = payload.get("delete", [])
    if delete is not None and not isinstance(delete, list):
        raise RuntimeError("增量更新包清单 delete 字段无效。")
    return payload


def _stage_patch_files(
    archive: zipfile.ZipFile,
    manifest: dict[str, object],
    stage_dir: Path,
    progress: _ApplyProgressDialog | None = None,
) -> list[tuple[str, Path]]:
    staged: list[tuple[str, Path]] = []
    files = manifest.get("files") or []
    assert isinstance(files, list)
    for index, item in enumerate(files, start=1):
        if not isinstance(item, dict):
            raise RuntimeError("增量更新包文件条目无效。")
        relative_path = _safe_relative_path(item.get("path"))
        expected_sha256 = str(item.get("sha256") or "").strip().lower()
        if len(expected_sha256) != 64:
            raise RuntimeError(f"增量更新包文件缺少 sha256：{relative_path}")
        member_name = PATCH_PAYLOAD_PREFIX + relative_path
        try:
            data = archive.read(member_name)
        except KeyError as exc:
            raise RuntimeError(f"增量更新包缺少文件：{relative_path}") from exc
        if _sha256_bytes(data) != expected_sha256:
            raise RuntimeError(f"增量更新包文件校验失败：{relative_path}")
        target = _safe_child_path(stage_dir, relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        staged.append((relative_path, target))
        if progress is not None:
            progress.update("正在校验增量更新...", 10 + index / max(1, len(files)) * 35, relative_path)
    return staged


def _apply_patch_package(
    patch_path: Path,
    target_dir: Path,
    work_dir: Path,
    progress: _ApplyProgressDialog | None = None,
) -> None:
    target_dir = target_dir.resolve()
    stage_dir = work_dir / "patch-stage"
    backup_dir = work_dir / "patch-backup"
    if stage_dir.exists():
        shutil.rmtree(stage_dir, ignore_errors=True)
    if backup_dir.exists():
        shutil.rmtree(backup_dir, ignore_errors=True)
    stage_dir.mkdir(parents=True, exist_ok=True)
    backup_dir.mkdir(parents=True, exist_ok=True)

    if progress is not None:
        progress.update("正在读取增量更新包...", 8, patch_path.name)
    with zipfile.ZipFile(patch_path) as archive:
        manifest = _load_patch_manifest(archive)
        _verify_patch_base(target_dir, manifest, progress)
        staged_files = _stage_patch_files(archive, manifest, stage_dir, progress)
        delete_entries = [_safe_relative_path(item) for item in (manifest.get("delete") or [])]

    _terminate_processes_under(target_dir)
    touched: list[tuple[str, bool]] = []
    total_ops = len(staged_files) + len(delete_entries)
    completed = 0

    def _remove_target(target: Path, relative_path: str) -> None:
        if target.is_dir():
            _retry_file_op(lambda: shutil.rmtree(target), f"删除 {relative_path}", target_dir=target_dir)
        else:
            _retry_file_op(target.unlink, f"删除 {relative_path}", target_dir=target_dir)

    try:
        for relative_path in delete_entries:
            target = _safe_child_path(target_dir, relative_path)
            existed = target.exists()
            if existed:
                _copy_existing_for_restore(target, _safe_child_path(backup_dir, relative_path))
            touched.append((relative_path, existed))
            if existed:
                _remove_target(target, relative_path)
            completed += 1
            if progress is not None:
                progress.update("正在应用增量更新...", 45 + completed / max(1, total_ops) * 45, relative_path)

        for relative_path, staged_path in staged_files:
            target = _safe_child_path(target_dir, relative_path)
            existed = target.exists()
            if existed:
                _copy_existing_for_restore(target, _safe_child_path(backup_dir, relative_path))
            touched.append((relative_path, existed))
            if existed:
                _remove_target(target, relative_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            _retry_file_op(
                lambda staged=staged_path, dest=target: shutil.copy2(staged, dest),
                f"写入 {relative_path}",
                target_dir=target_dir,
            )
            completed += 1
            if progress is not None:
                progress.update("正在应用增量更新...", 45 + completed / max(1, total_ops) * 45, relative_path)

        if progress is not None:
            progress.update("正在校验更新结果...", 92)
        _verify_patch_result(target_dir, manifest)
    except Exception:
        _restore_patch_backup(target_dir, backup_dir, touched)
        raise


def _replace_installation(package_root: Path, target_dir: Path) -> Path:
    component_manifest = package_root / "components.json"
    if component_manifest.exists():
        # Every component must be present and match one release before any live
        # file is renamed. Legacy full archives without this metadata still work.
        from endfield_pcap.component_updates import verify_installation
        verify_installation(package_root)
    target_dir = target_dir.resolve()
    backup_dir = target_dir.with_name(f"{target_dir.name}.old-{datetime.now():%Y%m%d%H%M%S}")
    if backup_dir.exists():
        shutil.rmtree(backup_dir, ignore_errors=True)
    _terminate_processes_under(target_dir)
    _rename_with_retries(target_dir, backup_dir)
    try:
        shutil.move(str(package_root), str(target_dir))
        if component_manifest.exists() or (target_dir / "components.json").exists():
            verify_installation(target_dir)
    except Exception:
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)
        backup_dir.rename(target_dir)
        raise
    return backup_dir


def _start_client(target_dir: Path, exe_name: str) -> None:
    exe_path = target_dir / exe_name
    if not exe_path.exists():
        exe_path = target_dir / "EndfieldLogsClient.exe"
    subprocess.Popen([str(exe_path)], cwd=str(target_dir))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="EndfieldLogsUpdater")
    package_group = parser.add_mutually_exclusive_group(required=True)
    package_group.add_argument("--package", type=Path)
    package_group.add_argument("--patch", type=Path)
    parser.add_argument("--target-dir", required=True, type=Path)
    parser.add_argument("--exe-name", default="EndfieldLogsClient.exe")
    parser.add_argument("--pid", type=int, default=0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    target_dir = args.target_dir.resolve()
    work_dir = _appdata_root() / "updates" / f"apply-{datetime.now():%Y%m%d%H%M%S}"
    extract_dir = work_dir / "extract"
    progress: _ApplyProgressDialog | None = None

    try:
        progress = _ApplyProgressDialog()
        _log(f"waiting for pid={args.pid}")
        progress.update("正在等待客户端关闭...", 5)
        _wait_for_pid(args.pid)
        if not target_dir.exists():
            raise RuntimeError(f"安装目录不存在：{target_dir}")

        if args.patch is not None:
            patch_path = args.patch.resolve()
            if not patch_path.exists():
                raise RuntimeError(f"增量更新包不存在：{patch_path}")
            _log(f"applying patch={patch_path} target={target_dir}")
            try:
                _apply_patch_package(patch_path, target_dir, work_dir, progress)
            except Exception as exc:
                # Any patch failure (base drift, locked files, bad archive)
                # marks this remote version so the next attempt goes straight
                # to the full package instead of looping on the same patch.
                patch_version, patch_build = _read_patch_identity(patch_path)
                _write_patch_fallback_marker(patch_version, patch_build, f"{type(exc).__name__}: {exc}")
                try:
                    patch_path.unlink(missing_ok=True)
                except OSError:
                    _log(f"failed to remove bad patch {patch_path}")
                raise
        else:
            package_path = args.package.resolve()
            if not package_path.exists():
                raise RuntimeError(f"更新包不存在：{package_path}")
            _log(f"extracting package={package_path}")
            _extract_package(package_path, extract_dir, progress)

            progress.update("正在检查更新包...", 72)
            package_root = _find_package_root(extract_dir)
            _log(f"replacing target={target_dir} package_root={package_root}")
            progress.update("正在替换客户端文件...", 82)
            backup_dir = _replace_installation(package_root, target_dir)
            _log(f"backup_dir={backup_dir}")
        _clear_patch_fallback_marker()
        _cleanup_old_backups(target_dir)
        progress.update("正在重新启动客户端...", 96)
        _start_client(target_dir, args.exe_name)
        if progress is not None:
            progress.update("更新完成", 100)
            progress.close()
            progress = None
        _show_message("更新完成", "客户端已更新完成并重新启动。")
        return 0
    except Exception as exc:  # noqa: BLE001 - updater must surface all failures.
        _log(f"update failed: {type(exc).__name__}: {exc}")
        if progress is not None:
            progress.close()
            progress = None
        _show_message("更新失败", str(exc), error=True)
        return 1
    finally:
        if progress is not None:
            progress.close()
        shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
