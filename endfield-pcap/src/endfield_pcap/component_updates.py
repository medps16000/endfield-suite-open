"""Content-addressed release components with whole-installation staging.

Components share the normal installed tree; they are not independently activated.
Only verified files enter a newly assembled full package. The existing updater
then performs its directory backup/replace transaction. Legacy manifests keep
using the full/patch path when this optional protocol is absent or unsupported.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
from typing import Callable
from urllib.parse import urlsplit
import zipfile

MANIFEST_NAME = "components.json"
SCHEMA_VERSION = 1
LAYOUT_VERSION = 2
COMPONENT_IDS = ("client", "runtime", "game-data")
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_FILES = 100_000
MAX_INSTALL_BYTES = 32 * 1024**3
HEX256 = re.compile(r"^[0-9a-f]{64}$")
SAFE_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
RESERVED = {"CON", "PRN", "AUX", "NUL", *{f"COM{i}" for i in range(1, 10)}, *{f"LPT{i}" for i in range(1, 10)}}


class ComponentUpdateError(ValueError):
    """Component update is unusable; caller must use the full package."""


def _sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _relative(value: object) -> str:
    if not isinstance(value, str) or len(value) > 1024 or not value or any(char in value for char in '\\:<>"|?*'):
        raise ComponentUpdateError("Invalid component relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} or part[-1:] in {".", " "}
                                 or any(ord(char) < 32 for char in part)
                                 or part.split(".")[0].upper() in RESERVED for part in value.split("/")):
        raise ComponentUpdateError("Unsafe component relative path")
    return value


def _child(root: Path, relative: str) -> Path:
    root = root.resolve()
    target = root.joinpath(*_relative(relative).split("/"))
    if target.resolve() != target.absolute() or not target.resolve().is_relative_to(root):
        raise ComponentUpdateError("Component path crosses a link or escapes its root")
    return target


def _hash_value(value: object) -> str:
    if not isinstance(value, str) or not HEX256.fullmatch(value):
        raise ComponentUpdateError("Invalid component SHA256")
    return value


def _artifact(value: object) -> dict:
    if not isinstance(value, dict):
        raise ComponentUpdateError("Missing component archive descriptor")
    url = value.get("url")
    if not isinstance(url, str) or len(url) > 4096:
        raise ComponentUpdateError("Invalid component download URL")
    parsed = urlsplit(url)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ComponentUpdateError("Unsupported component download URL")
    if not SAFE_LABEL.fullmatch(PurePosixPath(parsed.path).name):
        raise ComponentUpdateError("Unsafe component download filename")
    _hash_value(value.get("sha256"))
    size = value.get("bytes")
    if size is not None and (type(size) is not int or not 0 <= size <= MAX_INSTALL_BYTES):
        raise ComponentUpdateError("Invalid component archive size")
    return value


def load_manifest(path: Path) -> dict:
    if path.stat().st_size > MAX_MANIFEST_BYTES:
        raise ComponentUpdateError("Component manifest exceeds size limit")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise ComponentUpdateError("Invalid component manifest JSON") from exc
    return validate_manifest(value)


def validate_manifest(value: object) -> dict:
    if (not isinstance(value, dict) or type(value.get("schemaVersion")) is not int or value.get("schemaVersion") != SCHEMA_VERSION
            or type(value.get("layoutVersion")) is not int or value.get("layoutVersion") != LAYOUT_VERSION):
        raise ComponentUpdateError("Unsupported component schema or installation layout")
    for name in ("version", "build", "runtimeAbi"):
        if not isinstance(value.get(name), str) or not SAFE_LABEL.fullmatch(value[name]):
            raise ComponentUpdateError(f"Invalid component {name}")
    components = value.get("components")
    if not isinstance(components, list) or len(components) != len(COMPONENT_IDS):
        raise ComponentUpdateError("Release must contain all required components")
    identities, paths, file_count, byte_count = set(), set(), 0, 0
    for component in components:
        if not isinstance(component, dict) or component.get("id") not in COMPONENT_IDS or component["id"] in identities:
            raise ComponentUpdateError("Unknown or duplicate component identity")
        identities.add(component["id"])
        archive = _artifact(component.get("archive"))
        if type(archive.get("bytes")) is not int:
            raise ComponentUpdateError("Component archive size is required")
        content_id = _hash_value(component.get("contentId"))
        if component.get("version") != content_id:
            raise ComponentUpdateError("Component version is not its content identity")
        files = component.get("files")
        if not isinstance(files, list) or not files:
            raise ComponentUpdateError("Empty component file list")
        normalized = []
        for item in files:
            if not isinstance(item, dict):
                raise ComponentUpdateError("Invalid component file entry")
            relative = _relative(item.get("path"))
            key = relative.casefold()
            if key == MANIFEST_NAME.casefold() or key in paths:
                raise ComponentUpdateError("Duplicate or reserved component file path")
            paths.add(key)
            size = item.get("bytes")
            if type(size) is not int or not 0 <= size <= MAX_INSTALL_BYTES:
                raise ComponentUpdateError("Invalid component file size")
            normalized.append({"path": relative, "sha256": _hash_value(item.get("sha256")), "bytes": size})
            file_count += 1
            byte_count += size
        if file_count > MAX_FILES or byte_count > MAX_INSTALL_BYTES:
            raise ComponentUpdateError("Component release exceeds installation limits")
        normalized.sort(key=lambda item: item["path"])
        if files != normalized or hashlib.sha256(_canonical(normalized)).hexdigest() != content_id:
            raise ComponentUpdateError("Component file list or content identity mismatch")
    if identities != set(COMPONENT_IDS) or "endfieldlogsclient.exe" not in paths or "_internal/version.json" not in paths:
        raise ComponentUpdateError("Required client files missing from component release")
    # A file cannot also be a parent directory of another file, including Windows
    # case aliases. Reject before any extraction or copy touches the staged tree.
    for path in paths:
        ancestors = path.split("/")[:-1]
        for index in range(1, len(ancestors) + 1):
            if "/".join(ancestors[:index]) in paths:
                raise ComponentUpdateError("Component file/directory path collision")
    return value


def _component_for(relative: str) -> str:
    if relative.startswith(("_internal/data/", "_internal/jsondata/")):
        return "game-data"
    if relative == "_internal/updater/EndfieldLogsUpdater/EndfieldLogsUpdater.exe":
        # Updater code is rebuilt with client code. Its independent Python/Tcl
        # runtime below _internal remains reusable across application releases.
        return "client"
    if relative.startswith("_internal/"):
        owned = ("_internal/src/", "_internal/packages/", "_internal/endfield_pcap/", "_internal/secrets/", "_internal/app/assets/")
        if relative.startswith(owned) or relative in {"_internal/version.json", "_internal/shared-layout.json", "_internal/overlay.py", "_internal/log_integrity.py", "_internal/enemy_catalog.json"}:
            return "client"
        return "runtime"
    return "client"


def _archive_member(archive: zipfile.ZipFile, source: Path, name: str) -> None:
    # Identical component contents must produce identical immutable archives,
    # independent of source timestamps or the machine doing the rebuild.
    info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    with source.open("rb") as reader, archive.open(info, "w", force_zip64=True) as writer:
        shutil.copyfileobj(reader, writer, length=1024 * 1024)


def build_component_release(bundle: Path, output_dir: Path, prefix: str, version: str, build: str,
                            runtime_abi: str, urlbase: str) -> dict:
    """Write install metadata, three independent ZIPs and a pinned sidecar.

    Return the optional ``latest.json["components"]`` descriptor. The full ZIP
    remains the compatibility/failure path and must be built AFTER this call so
    it includes ``components.json``.
    """
    for label in (prefix, version, build, runtime_abi):
        if not isinstance(label, str) or not SAFE_LABEL.fullmatch(label):
            raise ComponentUpdateError("Unsafe release component label")
    bundle, output_dir = bundle.resolve(), output_dir.resolve()
    if output_dir.is_relative_to(bundle):
        raise ComponentUpdateError("Component output must be outside the installed tree")
    output_dir.mkdir(parents=True, exist_ok=True)
    groups = {name: [] for name in COMPONENT_IDS}
    for source in sorted(bundle.rglob("*")):
        if source.is_symlink() or source.is_junction():
            raise ComponentUpdateError("Release contains a linked path")
        if not source.is_file():
            continue
        relative = source.relative_to(bundle).as_posix()
        if relative == MANIFEST_NAME:
            continue
        _relative(relative)
        groups[_component_for(relative)].append({"path": relative, "sha256": _sha(source), "bytes": source.stat().st_size})
    manifest = {"schemaVersion": SCHEMA_VERSION, "layoutVersion": LAYOUT_VERSION, "version": version,
                "build": build, "runtimeAbi": runtime_abi, "components": []}
    for identity in COMPONENT_IDS:
        files = sorted(groups[identity], key=lambda item: item["path"])
        content = hashlib.sha256(_canonical(files)).hexdigest()
        # Archive identity is stable across client versions when component bytes
        # do not change; a previously cached component can therefore be reused.
        name = f"component-{identity}-{content}.zip"
        path = output_dir / name
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for item in files:
                _archive_member(archive, bundle / item["path"], item["path"])
        manifest["components"].append({"id": identity, "version": content, "contentId": content, "files": files,
                                        "archive": {"url": urlbase.rstrip("/") + "/" + name,
                                                    "sha256": _sha(path), "bytes": path.stat().st_size}})
    validate_manifest(manifest)
    payload = _canonical(manifest) + b"\n"
    if len(payload) > MAX_MANIFEST_BYTES:
        raise ComponentUpdateError("Generated component manifest exceeds protocol limit")
    sidecar = output_dir / f"{prefix}-components.json"
    sidecar.write_bytes(payload)
    (bundle / MANIFEST_NAME).write_bytes(payload)
    return {"schemaVersion": SCHEMA_VERSION, "url": urlbase.rstrip("/") + "/" + sidecar.name,
            "sha256": _sha(sidecar), "bytes": len(payload)}


def _verify_file(root: Path, item: dict) -> Path:
    path = _child(root, item["path"])
    if not path.is_file() or path.stat().st_size != item["bytes"] or _sha(path) != item["sha256"]:
        raise ComponentUpdateError("Installed component file is missing or changed: " + item["path"])
    return path


def verify_installation(root: Path, manifest: dict | None = None) -> dict:
    """Check all declared component files and exact version before activation."""
    manifest = validate_manifest(manifest) if manifest is not None else load_manifest(root / MANIFEST_NAME)
    for component in manifest["components"]:
        for item in component["files"]:
            _verify_file(root, item)
    try:
        version = json.loads((root / "_internal/version.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ComponentUpdateError("Component installation has invalid version metadata") from exc
    if version.get("version") != manifest["version"] or version.get("build") != manifest["build"]:
        raise ComponentUpdateError("Component release/version metadata mismatch")
    return manifest


def _extract_component(path: Path, component: dict, stage: Path) -> None:
    descriptor = component["archive"]
    if path.stat().st_size != descriptor["bytes"] or _sha(path) != descriptor["sha256"]:
        raise ComponentUpdateError("Downloaded component archive checksum mismatch")
    expected = {item["path"]: item for item in component["files"]}
    with zipfile.ZipFile(path) as archive:
        actual = {}
        for member in archive.infolist():
            if member.is_dir():
                _relative(member.filename.rstrip("/"))
                continue
            relative = _relative(member.filename)
            if relative not in expected or relative in actual or (member.external_attr >> 16) & 0o170000 == 0o120000:
                raise ComponentUpdateError("Component archive contains an unlisted, repeated or linked file")
            if member.file_size != expected[relative]["bytes"]:
                raise ComponentUpdateError("Component archive file size mismatch")
            actual[relative] = member
        if set(actual) != set(expected):
            raise ComponentUpdateError("Component archive is missing declared files")
        for relative, member in actual.items():
            target = _child(stage, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as reader, target.open("xb") as writer:
                shutil.copyfileobj(reader, writer, length=1024 * 1024)
            _verify_file(stage, expected[relative])


def prepare_component_package(descriptor: dict, install_root: Path, cache_root: Path,
                              fetch_artifact: Callable[[dict], Path], *,
                              expected_version: str, expected_build: str) -> tuple[Path, str, dict]:
    """Download changed components and produce a complete verified ZIP off-line.

    ``fetch_artifact`` must only download to a cache, never mutate installation.
    A SHA from the parent release manifest pins the entire detailed manifest.
    """
    descriptor = _artifact(descriptor)
    if type(descriptor.get("schemaVersion")) is not int or descriptor.get("schemaVersion") != SCHEMA_VERSION:
        raise ComponentUpdateError("Unsupported component manifest descriptor")
    remote_path = fetch_artifact(descriptor)
    if _sha(remote_path) != descriptor["sha256"]:
        raise ComponentUpdateError("Component manifest SHA256 mismatch")
    remote = load_manifest(remote_path)
    if remote["version"] != expected_version or remote["build"] != expected_build:
        raise ComponentUpdateError("Component manifest does not match the advertised release")
    install_root, cache_root = install_root.resolve(), cache_root.resolve()
    if cache_root.is_relative_to(install_root) or install_root.is_relative_to(cache_root):
        raise ComponentUpdateError("Component staging must be separate from the active installation")
    local = load_manifest(install_root / MANIFEST_NAME)
    # A different runtime ABI/layout needs a full package, never a mixture of
    # installed DLLs and modules from a new ABI that this client cannot validate.
    if local["runtimeAbi"] != remote["runtimeAbi"]:
        raise ComponentUpdateError("Runtime ABI changed; use a complete release package")
    local_components = {item["id"]: item for item in local["components"]}
    cache_root.mkdir(parents=True, exist_ok=True)
    report = {"reused": [], "downloaded": [], "repaired": []}
    with tempfile.TemporaryDirectory(prefix="components-stage-", dir=cache_root) as temporary:
        stage = Path(temporary) / "EndfieldLogsClient"
        stage.mkdir()
        for component in remote["components"]:
            identity = component["id"]
            old = local_components[identity]
            reusable = old["contentId"] == component["contentId"]
            if reusable:
                try:
                    for item in component["files"]:
                        source = _verify_file(install_root, item)
                        target = _child(stage, item["path"])
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(source, target)
                        _verify_file(stage, item)
                except (OSError, ComponentUpdateError):
                    # The whole component is retrieved when local bytes drift.
                    # Already copied verified files are removed from staging only.
                    for item in component["files"]:
                        target = _child(stage, item["path"])
                        if target.is_file():
                            target.unlink()
                    reusable = False
                    report["repaired"].append(identity)
            if reusable:
                report["reused"].append(identity)
            else:
                _extract_component(fetch_artifact(component["archive"]), component, stage)
                report["downloaded"].append(identity)
        (stage / MANIFEST_NAME).write_bytes(remote_path.read_bytes())
        verify_installation(stage, remote)
        output = cache_root / f"components-assembled-{descriptor['sha256']}.zip"
        pending = output.with_suffix(".zip.partial")
        with zipfile.ZipFile(pending, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
            for path in sorted(stage.rglob("*")):
                if path.is_file():
                    archive.write(path, "EndfieldLogsClient/" + path.relative_to(stage).as_posix())
        pending.replace(output)
    report["version"] = remote["version"]
    report["build"] = remote["build"]
    return output, _sha(output), report
