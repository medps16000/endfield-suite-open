"""Verify frozen local preview code and assets against the selected source."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import types

from PyInstaller.archive.readers import CArchiveReader


def normalized(code):
    if not isinstance(code, types.CodeType):
        return code
    return code.replace(co_filename="", co_consts=tuple(normalized(value) for value in code.co_consts))


def verify(source: Path, application: Path) -> dict:
    executable = application / "EndfieldOverlayPreview.exe"
    archive = CArchiveReader(str(executable)).open_embedded_archive("PYZ.pyz")
    modules = {}
    for name in ("overlay_windows", "overlay_window_config", "overlay_topics", "overlay_window_settings", "overlay_effect_catalog", "overlay_preview"):
        path = source / "src/endfield_pcap" / (name + ".py")
        actual = archive.extract("endfield_pcap." + name)
        expected = compile(path.read_bytes(), str(path), "exec", dont_inherit=True, optimize=0)
        modules[name] = {"matches": normalized(actual) == normalized(expected), "sourceSha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    assets = {}
    for name in ("effects-monitor.html", "topic-inspector.html", "data-api-minimal.html", "wolfblood-monitor.html"):
        original = source / "src/endfield_pcap/overlay_examples" / name
        frozen = application / "_internal/endfield_pcap/overlay_examples" / name
        assets[name] = original.read_bytes() == frozen.read_bytes()
    for name in ("effects.json", "targets.json", "native.json", "descriptions.json", "definitions.json"):
        original = source / "data/overlay" / name
        frozen = application / "_internal/data/overlay" / name
        assets[name] = original.read_bytes() == frozen.read_bytes()
    assets["game_resource_snapshot.json"] = ((source / "data/game_resource_snapshot.json").read_bytes()
                                              == (application / "_internal/data/game_resource_snapshot.json").read_bytes())
    files = list(application.rglob("*"))
    runtime = {
        "webEngineProcess": any(path.name == "QtWebEngineProcess.exe" for path in files),
        "chromiumResources": any(path.suffix == ".pak" for path in files),
        "noForeignIcu": not any(path.name.lower() in {"icuuc.dll", "icudt78.dll"} for path in files),
        "noCaptureSecretsOrUserRecords": not any(path.suffix.lower() in {".pem", ".ndjson", ".dump"} for path in files),
    }
    return {"ok": all(row["matches"] for row in modules.values()) and all(assets.values()) and all(runtime.values()),
            "version": "0.1.0-preview.6", "modules": modules, "assets": assets, "runtime": runtime,
            "exeSha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
            "fileCount": sum(path.is_file() for path in files),
            "totalBytes": sum(path.stat().st_size for path in files if path.is_file())}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--application", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    result = verify(args.source, args.application)
    args.result.parent.mkdir(parents=True, exist_ok=True)
    args.result.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("ok", "version", "exeSha256", "fileCount", "totalBytes")}))
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
