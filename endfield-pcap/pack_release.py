from __future__ import annotations

import argparse
import hashlib
import gzip
import importlib.metadata
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path, PurePosixPath
import zipfile


ROOT = Path(__file__).resolve().parent
APP_NAME = "EndfieldLogsClient"
CLIENT_VERSION = "3.0.12"
CHANNEL = "stable"
REQUIRED_PROTOBUF_VERSION = "6.33.6"
REQUIRED_PYTHON_VERSION = (3, 14, 7)
DEFAULT_MIN_VERSION = "3.0.9"
DEFAULT_RELEASE_NOTES = (
    "修复上一场统计计算阻塞新场日志读取的问题，重开和新场记录可及时处理，旧结果不会回填。"
    "复用单次解析内的官方效果结构查找，减少长战斗统计耗时；伤害、rDPS和官方计时规则保持不变。"
    "同步当前游戏热更新的官表、技能动作目录与资源来源证明。"
)
DIST = ROOT / "dist" / APP_NAME
VENV_PYTHON = Path(os.environ.get("ENDFIELD_RELEASE_PYTHON") or ROOT / ".venv" / "Scripts" / "python.exe")
BUILD_META_DIR = ROOT / "build_meta"
VERSION_JSON = BUILD_META_DIR / "version.json"
TCL_RUNTIME_DIR = BUILD_META_DIR / "tcl_runtime_3.14.7"
UPDATER_DIST = ROOT / "dist" / "EndfieldLogsUpdater"
DETECTOR_DIST = ROOT / "dist_detector"
DETECTOR_EXE = DETECTOR_DIST / "EndfieldLogsDetector.exe"
ENDFIELD_LOGS_ROOT = ROOT.parent / "endfield-logs"
NUM_ID_STR_SOURCE = ROOT.parent / "endfield_tables" / "Data" / "TableCfg" / "NumIdStrTable.json"
PCAP_NUM_ID_STR_TABLE = ROOT / "data" / "local_tables" / "NumIdStrTable.json"
LOGS_NUM_ID_STR_TABLE = ENDFIELD_LOGS_ROOT / "data" / "local_tables" / "NumIdStrTable.json"
CHARACTER_CATALOG_SCRIPT = ROOT / "scripts" / "sync_character_catalog.py"
OFFICIAL_SKILL_DAMAGE_CATALOG = ROOT / "data" / "local_static" / "skill" / "damage_element_catalog.json"
OFFICIAL_BUFF_ROOTS = ROOT / "data/local_static/buff/typed_roots.json.gz"
UPLOADER_CORE_ROOT = ENDFIELD_LOGS_ROOT / "packages" / "uploader_core"
UPLOADER_ROOT = ENDFIELD_LOGS_ROOT / "apps" / "uploader"
UPLOADER_VENV_PYTHON = Path(os.environ.get("ENDFIELD_RELEASE_PYTHON") or ENDFIELD_LOGS_ROOT / ".venv" / "Scripts" / "python.exe")
UPLOADER_SPEC = UPLOADER_ROOT / "uploader.spec"
UPLOADER_DIST = UPLOADER_ROOT / "dist" / "EndfieldLogsUploader"
UPLOADER_ICON = UPLOADER_ROOT / "app" / "assets" / "logo.ico"


def _python_is_usable(python_executable: Path) -> bool:
    if not python_executable.exists():
        return False
    try:
        probe = subprocess.run(
            [str(python_executable), "-c", "import sys"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return probe.returncode == 0


def _verify_release_dependencies() -> str | None:
    try:
        protobuf_version = importlib.metadata.version("protobuf")
    except importlib.metadata.PackageNotFoundError:
        return "protobuf is not installed"
    if protobuf_version != REQUIRED_PROTOBUF_VERSION:
        return (
            f"protobuf version drift: required={REQUIRED_PROTOBUF_VERSION} "
            f"actual={protobuf_version}. Install requirements.txt before packaging."
        )
    if sys.version_info[:3] != REQUIRED_PYTHON_VERSION:
        return (
            f"release Python version drift: required={'.'.join(map(str, REQUIRED_PYTHON_VERSION))} "
            f"actual={'.'.join(map(str, sys.version_info[:3]))}. "
            "Set ENDFIELD_RELEASE_PYTHON to the validated build interpreter; "
            "Python 3.13 failed retained multi-battle log replay."
        )
    return None


def _prefer_venv_python() -> int | None:
    current = Path(sys.executable).resolve()
    if _python_is_usable(VENV_PYTHON) and current != VENV_PYTHON.resolve():
        cmd = [str(VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]]
        return subprocess.call(cmd, cwd=ROOT)
    return None


def _build_full_uploader() -> int:
    if not UPLOADER_SPEC.exists():
        print(f"Missing uploader spec: {UPLOADER_SPEC}", file=sys.stderr)
        return 1
    uploader_python = UPLOADER_VENV_PYTHON if _python_is_usable(UPLOADER_VENV_PYTHON) else Path(sys.executable)
    if uploader_python != UPLOADER_VENV_PYTHON:
        print(f"Uploader virtualenv unavailable; using current Python: {uploader_python}")
    shutil.rmtree(UPLOADER_ROOT / "build", ignore_errors=True)
    shutil.rmtree(UPLOADER_ROOT / "dist", ignore_errors=True)
    return subprocess.call(
        [str(uploader_python), "-m", "PyInstaller", "--noconfirm", str(UPLOADER_SPEC)],
        cwd=UPLOADER_ROOT,
    )


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _normalize_runtime_archives(dist: Path) -> None:
    """Do not invalidate a reusable runtime because its ZIP order changed.

    PyInstaller's base-library module set is traversed in process-dependent order.
    Preserve every compiled module byte, while giving the container stable names,
    order and metadata. This does not rewrite bytecode or alter imports.
    """
    for path in dist.rglob("base_library.zip"):
        if path.resolve() != path.absolute() or not path.resolve().is_relative_to(dist.resolve()):
            raise ValueError(f"Unsafe runtime archive path: {path}")
        temporary = path.with_suffix(".zip.normalized")
        with zipfile.ZipFile(path) as source, zipfile.ZipFile(temporary, "w") as target:
            entries = source.infolist()
            if len({entry.filename for entry in entries}) != len(entries):
                raise ValueError(f"Duplicate runtime archive member: {path}")
            for entry in sorted(entries, key=lambda item: item.filename):
                info = zipfile.ZipInfo(entry.filename, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = entry.compress_type
                info.create_system = 3
                info.external_attr = (0o40755 if entry.is_dir() else 0o100644) << 16
                target.writestr(info, source.read(entry))
        with zipfile.ZipFile(path) as source, zipfile.ZipFile(temporary) as target:
            if set(source.namelist()) != set(target.namelist()) or any(
                source.read(name) != target.read(name) for name in source.namelist()
            ):
                raise ValueError(f"Runtime archive normalization changed module bytes: {path}")
        temporary.replace(path)


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _sha256_stream(file) -> str:
    hasher = hashlib.sha256()
    for chunk in iter(lambda: file.read(1024 * 1024), b""):
        hasher.update(chunk)
    return hasher.hexdigest()


def _relative_dist_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*") if path.is_file())


def _posix_relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _hash_dist_files(root: Path) -> dict[str, dict[str, object]]:
    files: dict[str, dict[str, object]] = {}
    for path in _relative_dist_files(root):
        files[_posix_relative(path, root)] = {
            "sha256": _sha256_file(path),
            "size": path.stat().st_size,
        }
    return files


def _hash_client_package_files(package_path: Path) -> dict[str, dict[str, object]]:
    files: dict[str, dict[str, object]] = {}
    prefix = f"{APP_NAME}/"
    with zipfile.ZipFile(package_path) as archive:
        for member in archive.infolist():
            if member.is_dir() or not member.filename.startswith(prefix):
                continue
            relative_path = member.filename[len(prefix) :]
            if not relative_path:
                continue
            with archive.open(member) as file:
                files[relative_path] = {
                    "sha256": _sha256_stream(file),
                    "size": member.file_size,
                }
    return files


def _read_package_version(package_path: Path) -> dict[str, object]:
    try:
        with zipfile.ZipFile(package_path) as archive:
            version_members = [
                member.filename
                for member in archive.infolist()
                if not member.is_dir() and Path(member.filename).name == "version.json"
            ]
            version_member = f"{APP_NAME}/version.json"
            if version_member not in version_members:
                version_member = f"{APP_NAME}/_internal/version.json"
            if version_member not in version_members and version_members:
                version_member = version_members[0]
            payload = json.loads(archive.read(version_member).decode("utf-8"))
    except (KeyError, OSError, zipfile.BadZipFile, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _build_patch_archive(
    *,
    base_package: Path,
    patch_archive: Path,
    version: str,
    build: str,
) -> dict[str, object] | None:
    old_files = _hash_client_package_files(base_package)
    new_files = _hash_dist_files(DIST)
    changed_paths = sorted(
        relative_path
        for relative_path, metadata in new_files.items()
        if old_files.get(relative_path, {}).get("sha256") != metadata["sha256"]
    )
    delete_paths = sorted(relative_path for relative_path in old_files if relative_path not in new_files)
    if not changed_paths and not delete_paths:
        return None

    base_version = _read_package_version(base_package)
    # baseFiles lets the updater verify the install actually matches this
    # patch's base before touching anything — a drifted install (earlier
    # partial update, AV quarantine) must fall back to the full package.
    base_files = {
        relative_path: str(old_files[relative_path]["sha256"])
        for relative_path in (*changed_paths, *delete_paths)
        if relative_path in old_files
    }
    patch_manifest = {
        "kind": "endfield_partial_update",
        "version": version,
        "build": build,
        "fromVersion": str(base_version.get("version") or ""),
        "fromBuild": str(base_version.get("build") or ""),
        "basePackage": base_package.name,
        "files": [
            {
                "path": relative_path,
                "sha256": str(new_files[relative_path]["sha256"]),
                "size": int(new_files[relative_path]["size"]),
            }
            for relative_path in changed_paths
        ],
        "delete": delete_paths,
        "baseFiles": base_files,
    }
    patch_archive.unlink(missing_ok=True)
    with zipfile.ZipFile(patch_archive, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("patch_manifest.json", json.dumps(patch_manifest, ensure_ascii=False, indent=2) + "\n")
        for relative_path in changed_paths:
            archive.write(DIST / Path(*relative_path.split("/")), f"payload/{relative_path}")

    return {
        "archive_path": patch_archive,
        "sha256": _sha256_file(patch_archive),
        "fromVersion": patch_manifest["fromVersion"],
        "fromBuild": patch_manifest["fromBuild"],
        "fileCount": len(changed_paths),
        "deleteCount": len(delete_paths),
    }


def _find_default_patch_base(client_version: str) -> Path | None:
    """Pick the newest older-version client zip in ROOT as the patch base."""
    best: tuple[tuple[int, ...], tuple[int, ...], Path] | None = None
    current_key = _version_sort_key(client_version)
    for candidate in ROOT.glob(f"{APP_NAME}_*.zip"):
        payload = _read_package_version(candidate)
        version = str(payload.get("version") or "")
        build = str(payload.get("build") or "")
        if not version:
            continue
        version_key = _version_sort_key(version)
        if version_key >= current_key:
            continue
        entry = (version_key, _version_sort_key(build), candidate)
        if best is None or entry[:2] > best[:2]:
            best = entry
    return best[2] if best is not None else None


def _version_sort_key(value: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in value.replace("-", ".").replace("_", ".").split("."):
        digits = "".join(char for char in chunk if char.isdigit())
        if digits:
            parts.append(int(digits))
    return tuple(parts or [0])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build EndfieldLogsClient release package and manifest")
    parser.add_argument("--version", default=CLIENT_VERSION, help="Release version to embed in version.json/latest.json.")
    parser.add_argument("--min-version", default=DEFAULT_MIN_VERSION, help="Minimum client version accepted by updater.")
    parser.add_argument("--notes", default=DEFAULT_RELEASE_NOTES, help="Release notes written to latest.json.")
    force_group = parser.add_mutually_exclusive_group()
    force_group.add_argument("--force", dest="force", action="store_true", help="Mark this release as a forced update.")
    force_group.add_argument("--no-force", dest="force", action="store_false", help="Allow this release to be skipped.")
    parser.set_defaults(force=True)
    parser.add_argument(
        "--patch-base",
        type=Path,
        default=None,
        help="Previous full package to diff against. Default: newest older-version client zip in the repo root.",
    )
    parser.add_argument(
        "--enable-patch",
        action="store_true",
        help="Generate a partial update archive. Default: full package only.",
    )
    parser.add_argument("--no-patch", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--download-base-url", default="https://zmdlogs.com/downloads")
    return parser


def _build_updater() -> int:
    shutil.rmtree(ROOT / "build" / "EndfieldLogsUpdater", ignore_errors=True)
    shutil.rmtree(UPDATER_DIST, ignore_errors=True)
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--noconsole",
        "--name",
        "EndfieldLogsUpdater",
        "--onedir",
        "--paths",
        str(ROOT / "src"),
        "--add-data",
        f"{TCL_RUNTIME_DIR / '_tcl_data'};_tcl_data",
        "--add-data",
        f"{TCL_RUNTIME_DIR / '_tk_data'};_tk_data",
        "--hidden-import",
        "tkinter",
        "--hidden-import",
        "tkinter.messagebox",
        "--collect-submodules",
        "tkinter",
        "--icon",
        str(UPLOADER_ICON),
        str(ROOT / "updater.py"),
    ]
    return subprocess.call(cmd, cwd=ROOT)


def _sync_required_local_tables() -> int:
    """Keep packaged parser data aligned with the unpacked NumIdStr table."""
    if not NUM_ID_STR_SOURCE.exists():
        missing = [path for path in (PCAP_NUM_ID_STR_TABLE, LOGS_NUM_ID_STR_TABLE) if not path.exists()]
        if missing:
            print(
                f"Missing NumIdStrTable source and packaged copies: {NUM_ID_STR_SOURCE}",
                file=sys.stderr,
            )
            return 1
        return 0
    for target in (PCAP_NUM_ID_STR_TABLE, LOGS_NUM_ID_STR_TABLE):
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and _sha256_file(target) == _sha256_file(NUM_ID_STR_SOURCE):
            continue
        shutil.copy2(NUM_ID_STR_SOURCE, target)
    return 0


def _verify_game_resource_snapshot() -> int:
    return subprocess.call([sys.executable, str(ENDFIELD_LOGS_ROOT / 'infra/scripts/game_resource_snapshot.py'),
                            'verify', '--project-root', str(ROOT.parent)], cwd=ROOT)


def _sync_and_validate_character_catalog() -> int:
    for command in ("generate", "validate"):
        result = subprocess.call(
            [sys.executable, "-X", "utf8", str(CHARACTER_CATALOG_SCRIPT), command],
            cwd=ROOT,
        )
        if result != 0:
            print(f"Character catalog {command} failed.", file=sys.stderr)
            return result
    return 0


def _validate_official_skill_damage_catalog() -> int:
    # Use the complete typed-root validator, not a few aggregate skill labels.
    validator = ENDFIELD_LOGS_ROOT / "infra/scripts/build_official_skill_damage_catalog.py"
    try:
        receipt = json.loads((ROOT / "data/game_resource_snapshot.json").read_text(encoding="utf8"))
        game_assembly = Path(receipt["gameRoot"]) / "GameAssembly.dll"
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Official gameplay source binding is missing: {exc}", file=sys.stderr)
        return 1
    result = subprocess.call([sys.executable, str(validator), "validate", "--output",
                              str(OFFICIAL_SKILL_DAMAGE_CATALOG), "--game-assembly", str(game_assembly)], cwd=ROOT)
    if result:
        return result
    try:
        damage = json.loads(OFFICIAL_SKILL_DAMAGE_CATALOG.read_text(encoding="utf8"))
        buffs = json.loads(gzip.decompress(OFFICIAL_BUFF_ROOTS.read_bytes()))
        if (not isinstance(buffs, dict) or buffs.get('schemaVersion') != 1
                or buffs.get('source') != 'official_local_buffdata_memorypack'
                or buffs.get('decoder') != damage.get('decoder')):
            raise ValueError('Buff roots do not share the validated damage schema/version')
        entries, summary = buffs.get('entries'), buffs.get('summary')
        if (not isinstance(entries, dict) or not entries or not isinstance(summary, dict)
                or summary.get('inputResourceCount') != len(entries)
                or summary.get('completeRootCount') != len(entries)):
            raise ValueError('Buff root coverage mismatch')
        for key, entry in entries.items():
            if not isinstance(entry, dict):
                raise ValueError(f'invalid Buff entry: {key}')
            root, proof = entry.get('root'), entry.get('proof')
            if not isinstance(root, dict) or not isinstance(proof, dict):
                raise ValueError(f'missing Buff root/proof: {key}')
            size = proof.get('sourceBytes')
            if (root.get('$type') != 'Beyond.Gameplay.Core.BuffData' or root.get('id') != key
                    or root.get('$offset') != 0 or type(size) is not int or size <= 0
                    or root.get('$end') != size or proof.get('consumedBytes') != size):
                raise ValueError(f'partial or mismatched Buff root: {key}')
    except (OSError, EOFError, ValueError) as exc:
        print(f'Official gameplay catalog gate failed: {exc}', file=sys.stderr)
        return 1
    return 0


def _build_detector() -> int:
    shutil.rmtree(ROOT / "build" / "EndfieldLogsDetector", ignore_errors=True)
    shutil.rmtree(DETECTOR_DIST, ignore_errors=True)
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--noconsole",
        "--name",
        "EndfieldLogsDetector",
        "--onefile",
        "--distpath",
        str(DETECTOR_DIST),
        "--paths",
        str(ROOT / "src"),
        "--add-data",
        f"{VERSION_JSON};.",
        "--add-data",
        f"{TCL_RUNTIME_DIR / '_tcl_data'};_tcl_data",
        "--add-data",
        f"{TCL_RUNTIME_DIR / '_tk_data'};_tk_data",
        "--collect-binaries",
        "psutil",
        "--icon",
        str(UPLOADER_ICON),
        str(ROOT / "detector_launcher.py"),
    ]
    return subprocess.call(cmd, cwd=ROOT)


def main() -> int:
    redirected = _prefer_venv_python()
    if redirected is not None:
        return redirected
    # Dependency collection must not search unrelated applications' DLL paths.
    # In particular, ambient Codex native-tool paths supplied incompatible
    # ucrtbase/ICU libraries that prevented the frozen Qt uploader from loading.
    runtime_root = Path(sys.base_prefix).resolve()
    windows_root = Path(os.environ["SystemRoot"]).resolve()
    os.environ["PATH"] = os.pathsep.join(str(path) for path in (
        Path(sys.executable).resolve().parent, runtime_root, runtime_root / "DLLs",
        windows_root / "System32", windows_root, windows_root / "System32" / "Wbem",
    ))
    os.environ.pop("PYTHONPATH", None)
    os.environ["PYTHONNOUSERSITE"] = "1"
    args = build_parser().parse_args()
    dependency_error = _verify_release_dependencies()
    if dependency_error is not None:
        print(f"Release dependency check failed: {dependency_error}", file=sys.stderr)
        return 1
    client_version = str(args.version or "").strip()
    if not client_version:
        print("Release version must not be empty.", file=sys.stderr)
        return 1
    if _verify_game_resource_snapshot() != 0:
        return 1
    # Tcl 9 embeds its script libraries in the DLLs, but PyInstaller's Tk
    # runtime hook still requires physical _tcl_data and _tk_data directories.
    try:
        runtime_root = TCL_RUNTIME_DIR.resolve()
        if (runtime_root != TCL_RUNTIME_DIR.absolute()
                or not runtime_root.is_relative_to(BUILD_META_DIR.resolve())):
            raise ValueError("Tcl runtime output must remain in the build metadata directory")
        for dll_name, archive_prefix, target_name, entry_script in (
            ("tcl90.dll", "tcl_library/", "_tcl_data", "init.tcl"),
            ("tcl9tk90.dll", "tk_library/", "_tk_data", "tk.tcl"),
        ):
            dll_path = Path(sys.base_prefix) / "DLLs" / dll_name
            target_root = runtime_root / target_name
            with zipfile.ZipFile(dll_path) as archive:
                if archive.testzip() is not None:
                    raise ValueError(f"Tcl resource ZIP CRC failed: {dll_name}")
                members = []
                relative_names = set()
                for member in archive.infolist():
                    if not member.filename.startswith(archive_prefix):
                        raise ValueError(f"Unexpected Tcl resource prefix: {member.filename}")
                    relative_text = member.filename[len(archive_prefix):]
                    if not relative_text and member.is_dir():
                        continue
                    relative = PurePosixPath(relative_text)
                    if (not relative.parts or relative.is_absolute()
                            or any(part in {".", ".."} for part in relative_text.split("/"))
                            or "\\" in relative_text or ":" in relative_text
                            or (member.external_attr >> 16) & 0o170000 == 0o120000):
                        raise ValueError(f"Unsafe Tcl resource path: {member.filename}")
                    destination = target_root.joinpath(*relative.parts).resolve()
                    if not destination.is_relative_to(target_root):
                        raise ValueError(f"Tcl resource escapes target directory: {member.filename}")
                    if member.is_dir():
                        continue
                    key = relative.as_posix().casefold()
                    if key in relative_names:
                        raise ValueError(f"Duplicate Tcl resource path: {member.filename}")
                    relative_names.add(key)
                    members.append((member, destination))
                if entry_script.casefold() not in relative_names:
                    raise ValueError(f"Missing Tcl entry script: {dll_name}/{entry_script}")
                for member, destination in members:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(archive.read(member))
                actual_names = {
                    path.relative_to(target_root).as_posix().casefold()
                    for path in target_root.rglob("*") if path.is_file()
                }
                if actual_names != relative_names:
                    raise ValueError(f"Unexpected stale Tcl resource files: {target_root}")
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print(f"Tcl runtime resource preparation failed: {exc}", file=sys.stderr)
        return 1
    patch_base: Path | None = None
    if args.enable_patch and not args.no_patch:
        patch_base = args.patch_base.resolve() if args.patch_base is not None else _find_default_patch_base(client_version)
        if args.patch_base is not None and not patch_base.exists():
            print(f"Missing patch base package: {patch_base}", file=sys.stderr)
            return 1
        if patch_base is None:
            print("No older client zip found in repo root; building full package only.")
        else:
            print(f"Patch base: {patch_base.name}")
    sync_exit_code = _sync_required_local_tables()
    if sync_exit_code != 0:
        return sync_exit_code
    character_catalog_exit_code = _sync_and_validate_character_catalog()
    if character_catalog_exit_code != 0:
        return character_catalog_exit_code
    official_damage_catalog_exit_code = _validate_official_skill_damage_catalog()
    if official_damage_catalog_exit_code != 0:
        return official_damage_catalog_exit_code
    rotation_assets_exit_code = subprocess.call(
        [sys.executable, str(ROOT / "scripts" / "sync_rotation_assets.py"), "validate"], cwd=ROOT,
    )
    if rotation_assets_exit_code != 0:
        return rotation_assets_exit_code
    build = datetime.now().strftime("%Y%m%d")
    _write_json(
        VERSION_JSON,
        {
            "name": APP_NAME,
            "version": client_version,
            "build": build,
            "channel": CHANNEL,
        },
    )
    updater_exit_code = _build_updater()
    if updater_exit_code != 0:
        return updater_exit_code
    shutil.rmtree(DIST, ignore_errors=True)
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--noconsole",
        "--name",
        APP_NAME,
        "--onedir",
        "--additional-hooks-dir",
        str(ROOT / "packaging" / "hooks"),
        "--exclude-module",
        "zstandard",
        "--paths",
        str(ROOT / "src"),
        "--paths",
        str(ROOT / "packages" / "parser_core"),
        "--paths",
        str(UPLOADER_CORE_ROOT),
        "--paths",
        str(UPLOADER_ROOT),
        "--add-data",
        f"{ROOT / 'data'};data",
        "--add-data",
        f"{ROOT / 'jsondata'};jsondata",
        "--add-data",
        f"{ROOT / 'src'};src",
        "--add-data",
        f"{ROOT / 'src' / 'endfield_pcap' / 'overlay_examples'};endfield_pcap/overlay_examples",
        "--add-data",
        f"{ROOT / 'secrets'};secrets",
        "--add-data",
        f"{ROOT / 'packages'};packages",
        "--add-data",
        f"{UPLOADER_CORE_ROOT};packages/uploader_core",
        "--add-data",
        f"{UPLOADER_ROOT / 'app' / 'assets'};app/assets",
        "--add-data",
        f"{UPDATER_DIST};updater/EndfieldLogsUpdater",
        "--add-data",
        f"{VERSION_JSON};.",
        "--add-data",
        f"{TCL_RUNTIME_DIR / '_tcl_data'};_tcl_data",
        "--add-data",
        f"{TCL_RUNTIME_DIR / '_tk_data'};_tk_data",
        "--add-data",
        f"{ROOT / 'overlay.py'};.",
        "--add-data",
        f"{ROOT / 'log_integrity.py'};.",
        "--add-data",
        f"{ROOT / 'enemy_catalog.json'};.",
        "--hidden-import",
        "tkinter",
        "--hidden-import",
        "tkinter.filedialog",
        "--hidden-import",
        "tkinter.messagebox",
        "--hidden-import",
        "endfield_pcap.rotation_client",
        "--hidden-import",
        "endfield_pcap.rotation_overlay",
        "--hidden-import",
        "endfield_pcap.overlay_windows",
        "--hidden-import",
        "endfield_pcap.overlay_topics",
        "--hidden-import",
        "endfield_pcap.overlay_window_settings",
        "--collect-submodules",
        "tkinter",
        "--collect-binaries",
        "Crypto",
        "--collect-submodules",
        "parser_core",
        "--collect-submodules",
        "uploader_core",
        "--collect-submodules",
        "app",
        "--collect-submodules",
        "endfield_pcap.proto_generated",
        "--hidden-import",
        "socksio",
        "--icon",
        str(UPLOADER_ICON),
        str(ROOT / "launcher.py"),
    ]
    logs_data = ENDFIELD_LOGS_ROOT / "data"
    optional_data = [
        (logs_data / "akedata", "data/akedata"),
        (logs_data / "local_static" / "skill", "data/local_static/skill"),
        (logs_data / "local_semantics", "data/local_semantics"),
    ]
    for source, target in optional_data:
        if source.exists():
            insert_at = len(cmd) - 1
            cmd[insert_at:insert_at] = ["--add-data", f"{source};{target}"]
    exit_code = subprocess.call(cmd, cwd=ROOT)
    if exit_code != 0:
        return exit_code
    # The shared worker uses the same canonical official tables. Preserve any
    # worker-only data and exact differing packet resources without overwriting
    # the collector's own resolver. There is no precedence guess on conflicts.
    shared_data = DIST / "_internal" / "data"
    worker_conflicts = []
    for source in sorted((ENDFIELD_LOGS_ROOT / "data").rglob("*")):
        if not source.is_file():
            continue
        relative = source.relative_to(ENDFIELD_LOGS_ROOT / "data")
        if relative.parts[0] not in {"akedata", "local_static", "local_semantics", "local_tables", "packet_semantics", "public"}:
            continue
        target = shared_data / relative
        if target.exists():
            if _sha256_file(source) != _sha256_file(target):
                if relative.as_posix() != "packet_semantics/packet_resolver_bundle.json":
                    raise ValueError(f"Unreviewed shared data conflict: {relative}")
                target = shared_data / "uploader_overrides" / relative
                worker_conflicts.append(relative.as_posix())
            else:
                continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    _write_json(DIST / "_internal" / "shared-layout.json", {
        "schemaVersion": 1, "layoutVersion": 2,
        "roles": {"uploader": [APP_NAME + ".exe", "--uploader"],
                  "detector": [APP_NAME + ".exe", "--detector"]},
        "sharedData": "data", "preservedWorkerDataConflicts": worker_conflicts,
        "updaterIndependent": True,
    })
    # PySide6 adds its own directory to the native loader's search path. The Qt
    # plugins and WebEngine helper live there too. Remove only byte-identical Qt
    # DLL aliases collected at the outer _internal root; keep the canonical copy.
    for alias in (DIST / "_internal").glob("Qt6*.dll"):
        canonical = DIST / "_internal" / "PySide6" / alias.name
        if canonical.is_file() and _sha256_file(alias) == _sha256_file(canonical):
            if alias.resolve() != alias.absolute() or not alias.resolve().is_relative_to(DIST.resolve()):
                raise ValueError(f"Unsafe Qt alias path: {alias}")
            alias.unlink()
    for filename, role in (("运行诊断.cmd", "--detector"), ("仅打开上传器.cmd", "--uploader")):
        (DIST / filename).write_bytes((f'@echo off\r\n"%~dp0{APP_NAME}.exe" {role} %*\r\n').encode("utf-8"))
    # These are source companions for dynamic imports, not a development tree.
    # Keep runtime .py files (including every generated protocol module), but
    # exclude development tests and reproducible bytecode/test caches.
    removed_cache_files = 0
    removed_cache_bytes = 0
    for source_root in (DIST / "_internal" / "src", DIST / "_internal" / "packages"):
        if source_root.resolve() != source_root.absolute() or not source_root.resolve().is_relative_to(DIST.resolve()):
            raise ValueError(f"Unsafe packaged source root: {source_root}")
        if not source_root.is_dir():
            continue
        for cached in source_root.rglob("*.pyc"):
            if cached.resolve() != cached.absolute() or not cached.resolve().is_relative_to(source_root.resolve()):
                raise ValueError(f"Unsafe packaged bytecode path: {cached}")
            removed_cache_files += 1
            removed_cache_bytes += cached.stat().st_size
            cached.unlink()
        excluded_dirs = [item for item in source_root.rglob("*") if item.is_dir() and item.name in {"__pycache__", ".pytest_cache", "tests"}]
        for cached_dir in sorted(excluded_dirs, key=lambda item: len(item.parts), reverse=True):
            if cached_dir.resolve() != cached_dir.absolute() or not cached_dir.resolve().is_relative_to(source_root.resolve()):
                raise ValueError(f"Unsafe packaged cache directory: {cached_dir}")
            for item in cached_dir.rglob("*"):
                if item.resolve() != item.absolute() or not item.resolve().is_relative_to(source_root.resolve()):
                    raise ValueError(f"Unsafe packaged development file: {item}")
                if item.is_file():
                    removed_cache_files += 1
                    removed_cache_bytes += item.stat().st_size
            shutil.rmtree(cached_dir)
    print(f"Excluded development tests/caches: {removed_cache_files} files, {removed_cache_bytes} bytes")
    _normalize_runtime_archives(DIST)
    if _verify_game_resource_snapshot() != 0:
        return 1
    if _validate_official_skill_damage_catalog() != 0:
        return 1
    rotation_assets_exit_code = subprocess.call(
        [sys.executable, str(ROOT / "scripts" / "sync_rotation_assets.py"), "validate"], cwd=ROOT,
    )
    if rotation_assets_exit_code != 0:
        return rotation_assets_exit_code
    component_spec = importlib.util.spec_from_file_location(
        "release_components", ROOT / "src" / "endfield_pcap" / "component_updates.py",
    )
    component_builder = importlib.util.module_from_spec(component_spec)
    component_spec.loader.exec_module(component_builder)
    component_descriptor = component_builder.build_component_release(
        bundle=DIST, output_dir=ROOT,
        prefix=f"{APP_NAME}_{client_version}_{build}", version=client_version, build=build,
        runtime_abi="cp3147-win_amd64-qt6110-layout2",
        urlbase=str(args.download_base_url).rstrip("/"),
    )
    archive_base = ROOT / f"{APP_NAME}_{client_version}_{build}"
    archive_path = Path(shutil.make_archive(str(archive_base), "zip", root_dir=DIST.parent, base_dir=DIST.name))
    sha256 = _sha256_file(archive_path)
    download_base_url = str(args.download_base_url).rstrip("/")
    patch_info = None
    if patch_base is not None:
        patch_archive = ROOT / f"{APP_NAME}_{client_version}_{build}_patch_from_{patch_base.stem}.zip"
        patch_info = _build_patch_archive(
            base_package=patch_base,
            patch_archive=patch_archive,
            version=client_version,
            build=build,
        )
    manifest = {
        "channel": CHANNEL,
        "version": client_version,
        "build": build,
        "url": f"{download_base_url}/{archive_path.name}",
        "sha256": sha256,
        "minVersion": str(args.min_version),
        "notes": str(args.notes),
        "force": bool(args.force),
        "components": component_descriptor,
    }
    if patch_info is not None:
        patch_archive_path = patch_info["archive_path"]
        assert isinstance(patch_archive_path, Path)
        manifest["patches"] = [
            {
                "fromVersion": patch_info["fromVersion"],
                "fromBuild": patch_info["fromBuild"],
                "url": f"{download_base_url}/{patch_archive_path.name}",
                "sha256": patch_info["sha256"],
                "fileCount": patch_info["fileCount"],
                "deleteCount": patch_info["deleteCount"],
            }
        ]
    _write_json(ROOT / "latest.json", manifest)
    print(f"Release archive: {archive_path}")
    print(f"SHA256: {sha256}")
    if patch_info is not None:
        print(f"Patch archive: {patch_info['archive_path']}")
        print(f"Patch SHA256: {patch_info['sha256']}")
        print(f"Patch files: {patch_info['fileCount']} changed, {patch_info['deleteCount']} deleted")
    print(f"Manifest: {ROOT / 'latest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
