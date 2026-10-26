from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "data" / "local_tables" / "character" / "manifest.json"
NAME_INDEX_PATH = ROOT / "jsondata" / "CharacterNameIndex.json"
CHARACTER_ID_PATTERN = re.compile(r"^chr_\d+_[a-z0-9]+$")
SUPPORTED_CHARACTER_TYPES = {"Physical", "Fire", "Pulse", "Cryst", "Natural"}


def _load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def build_name_index(manifest_path: Path = MANIFEST_PATH) -> dict[str, str]:
    payload = _load_json(manifest_path)
    if not isinstance(payload, dict):
        raise ValueError(f"invalid character manifest: {manifest_path}")
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise ValueError(f"character manifest has no entries: {manifest_path}")

    result: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        char_id = str(entry.get("charId") or entry.get("id") or "").strip()
        name = str(entry.get("name") or "").strip()
        if char_id and name:
            result[char_id] = name
    return result


def validate_character_catalog(
    manifest_path: Path = MANIFEST_PATH,
    name_index_path: Path = NAME_INDEX_PATH,
) -> list[str]:
    errors: list[str] = []
    payload = _load_json(manifest_path)
    entries = payload.get("entries") if isinstance(payload, dict) else None
    if not isinstance(entries, list) or not entries:
        return [f"character manifest has no entries: {manifest_path}"]

    expected_count = payload.get("count")
    if expected_count != len(entries):
        errors.append(f"manifest count mismatch: count={expected_count!r} entries={len(entries)}")

    seen: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            errors.append(f"entry {index} is not an object")
            continue
        char_id = str(entry.get("charId") or entry.get("id") or "").strip()
        name = str(entry.get("name") or "").strip()
        char_type = str(entry.get("charTypeId") or "").strip()
        if not CHARACTER_ID_PATTERN.fullmatch(char_id):
            errors.append(f"entry {index} has invalid charId: {char_id!r}")
        elif char_id in seen:
            errors.append(f"duplicate charId: {char_id}")
        seen.add(char_id)
        if not name:
            errors.append(f"{char_id or f'entry {index}'} has no Chinese display name")
        if char_type not in SUPPORTED_CHARACTER_TYPES:
            errors.append(f"{char_id or f'entry {index}'} has unsupported charTypeId: {char_type!r}")

    name_index_payload = _load_json(name_index_path)
    if not isinstance(name_index_payload, dict):
        errors.append(f"invalid name index: {name_index_path}")
        return errors
    expected_index = build_name_index(manifest_path)
    actual_index = {
        str(key): str(value)
        for key, value in name_index_payload.items()
        if isinstance(value, str) and value.strip()
    }
    if actual_index != expected_index:
        missing = sorted(set(expected_index) - set(actual_index))
        stale = sorted(set(actual_index) - set(expected_index))
        mismatched = sorted(
            key
            for key in set(expected_index) & set(actual_index)
            if expected_index[key] != actual_index[key]
        )
        errors.append(
            "CharacterNameIndex is not generated from the current official manifest: "
            f"missing={missing} stale={stale} mismatched={mismatched}"
        )
    return errors


def generate_character_name_index(
    manifest_path: Path = MANIFEST_PATH,
    name_index_path: Path = NAME_INDEX_PATH,
) -> None:
    payload = build_name_index(manifest_path)
    name_index_path.parent.mkdir(parents=True, exist_ok=True)
    name_index_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate and validate the client character catalog from local game tables.")
    parser.add_argument("command", choices=("generate", "validate"))
    args = parser.parse_args()

    if args.command == "generate":
        generate_character_name_index()
        print(f"generated {NAME_INDEX_PATH} ({len(build_name_index())} characters)")
        return 0

    errors = validate_character_catalog()
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"character catalog valid ({len(build_name_index())} characters)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
