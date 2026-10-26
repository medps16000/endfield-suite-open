"""Generate or verify the offline PNG assets used by the rotation guide.

Only the two official web manifests enumerate inputs. QImage performs local,
headless decoding; no QApplication, network fetch, or optional Pillow is used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from PySide6.QtCore import QBuffer, QIODevice, qVersion
from PySide6.QtGui import QImage

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PUBLIC = ROOT.parent / "endfield-logs/apps/web/public"
OUTPUT_PUBLIC_ROOTS = (ROOT / "data/public", ROOT.parent / "endfield-logs/data/public")
SOURCES = (
    ("skills", "icons", "/images/character/game-skills/manifest.json"),
    ("avatars", "avatars", "/images/character/game-avatars/manifest.json"),
)
_ID = re.compile(r"[A-Za-z0-9_]+\Z")
_FILENAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*\Z")


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_json(path: Path):
    return json.loads(path.read_text("utf-8-sig"), object_pairs_hook=_unique_object)


def _canonical_sha(payload) -> str:
    return _sha(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _source_file(public: Path, url: str, prefix: str) -> Path:
    if not isinstance(url, str) or not url.startswith(prefix):
        raise ValueError(f"Official image URL has the wrong prefix: {url!r}")
    parsed = urlsplit(url)
    filename = url[len(prefix):]
    if (parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or parsed.path != url
            or not _FILENAME.fullmatch(filename) or ".." in filename
            or Path(filename).suffix not in {".webp", ".png"}):
        raise ValueError(f"Invalid official image URL: {url!r}")
    path = public / url.lstrip("/")
    if not path.is_file() or not path.resolve().is_relative_to(public.resolve()):
        raise ValueError(f"Official image is missing or outside web public: {url}")
    return path


def _image(payload: bytes, description: str) -> QImage:
    image = QImage.fromData(payload)
    if image.isNull():
        raise ValueError(f"QImage cannot decode {description}")
    return image


def _pixels(image: QImage) -> str:
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    raw = rgba.constBits()
    stride, row_size = rgba.bytesPerLine(), rgba.width() * 4
    return _sha(b"".join(bytes(raw[y * stride:y * stride + row_size]) for y in range(rgba.height())))


def build_rotation_assets(source_public: Path = SOURCE_PUBLIC) -> tuple[dict, dict[str, bytes]]:
    """Build expected PNG bytes and their exact source relationships in memory."""
    source_public = Path(source_public)
    manifest_sources, images, payloads = {}, {}, {}
    for kind, key, manifest_url in SOURCES:
        source_manifest = _load_json(source_public / manifest_url.lstrip("/"))
        if (not isinstance(source_manifest, dict)
                or ("schemaVersion" in source_manifest and
                    (type(source_manifest["schemaVersion"]) is not int or source_manifest["schemaVersion"] != 1))
                or not isinstance(source_manifest.get(key), dict) or not source_manifest[key]):
            raise ValueError(f"Invalid official {kind} manifest")
        source_sha = _canonical_sha(source_manifest)
        manifest_sources[kind] = {"url": manifest_url, "canonicalJsonSha256": source_sha,
                                  "count": len(source_manifest[key])}
        prefix = manifest_url.rsplit("/", 1)[0] + "/"
        for identity, row in sorted(source_manifest[key].items()):
            if not isinstance(identity, str) or not _ID.fullmatch(identity) or not isinstance(row, dict):
                raise ValueError(f"Invalid official {kind} identity: {identity!r}")
            url = row.get("url")
            source = _source_file(source_public, url, prefix)
            if url in images:
                raise ValueError(f"Duplicate official image URL: {url}")
            source_bytes = source.read_bytes()
            image = _image(source_bytes, url)
            width, height = image.width(), image.height()
            if (type(row.get("width")) is not int or type(row.get("height")) is not int
                    or (row["width"], row["height"]) != (width, height)):
                raise ValueError(f"Official image dimensions disagree: {url}")
            pixel_sha = _pixels(image)
            if source.suffix == ".png":
                if not source_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
                    raise ValueError(f"PNG extension does not identify PNG bytes: {url}")
                png = source_bytes
            else:
                buffer = QBuffer()
                buffer.open(QIODevice.OpenModeFlag.WriteOnly)
                if not image.convertToFormat(QImage.Format.Format_RGBA8888).save(buffer, "PNG"):
                    raise ValueError(f"QImage cannot encode PNG: {url}")
                png = bytes(buffer.data())
                buffer.close()
            decoded = _image(png, f"PNG conversion of {url}")
            if (decoded.width(), decoded.height()) != (width, height) or _pixels(decoded) != pixel_sha:
                raise ValueError(f"PNG conversion changed decoded RGBA pixels: {url}")
            relative = f"{kind}/{identity}.png"
            entry = {"path": relative, "sha256": _sha(png), "width": width, "height": height,
                     "sourceImageSha256": _sha(source_bytes), "sourceManifestSha256": source_sha,
                     "pixelSha256": pixel_sha}
            if "viewBox" in row:
                entry["viewBox"] = row["viewBox"]
            images[url], payloads[relative] = entry, png
    return {"schemaVersion": 1, "generator": {"name": "sync_rotation_assets.py", "qtVersion": qVersion()},
            "sourceManifests": manifest_sources, "images": images}, payloads


def _target(public: Path, relative: str) -> Path:
    public = Path(public).absolute()
    target = public / relative
    if not target.resolve().is_relative_to(public.resolve()):
        raise ValueError(f"Output escapes data/public: {target}")
    for parent in (target, *target.parents):
        if parent.is_symlink() or parent.is_junction():
            raise ValueError(f"Indirect output path is not allowed: {parent}")
    return target


def _existing_assets(public: Path) -> set[str]:
    public = Path(public).absolute()
    asset_root = _target(public, "rotation-assets")
    if not asset_root.exists():
        return set()
    if not asset_root.is_dir():
        raise ValueError(f"Asset root is not a directory: {asset_root}")
    result = set()
    for group in asset_root.iterdir():
        if group.name not in {"skills", "avatars"} or not group.is_dir():
            raise ValueError(f"Unexpected asset directory: {group}")
        _target(public, group.relative_to(public).as_posix())
        for path in group.iterdir():
            _target(public, path.relative_to(public).as_posix())
            if not path.is_file():
                raise ValueError(f"Unexpected non-file asset: {path}")
            result.add(path.relative_to(asset_root).as_posix())
    return result


def _index_bytes(index: dict) -> bytes:
    return (json.dumps(index, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _write(path: Path, payload: bytes) -> None:
    if path.is_file() and path.read_bytes() == payload:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".rotation-assets-", delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(payload)
    try:
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def generate_rotation_assets(source_public: Path = SOURCE_PUBLIC,
                             output_public_roots: tuple[Path, ...] = OUTPUT_PUBLIC_ROOTS) -> dict:
    index, payloads = build_rotation_assets(source_public)
    if not output_public_roots:
        raise ValueError("At least one explicit output root is required")
    for public in output_public_roots:
        _target(public, "rotation-assets.json")
        extras = _existing_assets(public) - payloads.keys()
        if extras:
            raise ValueError(f"Unexpected or stale assets; no automatic deletion: {sorted(extras)}")
    for public in output_public_roots:
        for relative, payload in payloads.items():
            _write(_target(public, "rotation-assets/" + relative), payload)
        _write(_target(public, "rotation-assets.json"), _index_bytes(index))
    validate_rotation_assets(source_public, output_public_roots)
    return index


def validate_rotation_assets(source_public: Path = SOURCE_PUBLIC,
                             output_public_roots: tuple[Path, ...] = OUTPUT_PUBLIC_ROOTS) -> dict:
    """Read-only validation; never creates directories, repairs files or caches."""
    index, payloads = build_rotation_assets(source_public)
    if not output_public_roots:
        raise ValueError("At least one explicit output root is required")
    expected_index = _index_bytes(index)
    entries_by_path = {row["path"]: row for row in index["images"].values()}
    for public in output_public_roots:
        path = _target(public, "rotation-assets.json")
        if not path.is_file() or path.read_bytes() != expected_index:
            raise ValueError(f"Asset manifest differs from current official sources: {path}")
        if _existing_assets(public) != set(payloads):
            raise ValueError(f"Asset file set differs from current official sources: {public}")
        for relative, payload in payloads.items():
            path = _target(public, "rotation-assets/" + relative)
            actual = path.read_bytes()
            entry = entries_by_path[relative]
            if actual != payload or _sha(actual) != entry["sha256"]:
                raise ValueError(f"PNG bytes or source relationship differ: {path}")
            if _pixels(_image(actual, str(path))) != entry["pixelSha256"]:
                raise ValueError(f"PNG decoded RGBA pixels differ: {path}")
    return index


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("generate", "validate"))
    args = parser.parse_args()
    try:
        index = generate_rotation_assets() if args.command == "generate" else validate_rotation_assets()
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    counts = {kind: row["count"] for kind, row in index["sourceManifests"].items()}
    print(json.dumps({"command": args.command, "images": len(index["images"]), "counts": counts}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
