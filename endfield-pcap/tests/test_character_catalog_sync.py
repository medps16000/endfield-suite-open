from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from endfield_pcap.game_data import load_name_index


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "sync_character_catalog.py"
SPEC = importlib.util.spec_from_file_location("sync_character_catalog", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
sync_character_catalog = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync_character_catalog)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_current_catalog_generates_every_official_character_name() -> None:
    expected = sync_character_catalog.build_name_index()

    assert expected["chr_0035_liino"] == "梨诺"
    assert sync_character_catalog.validate_character_catalog() == []


def test_validation_rejects_missing_name_index_entry(tmp_path: Path) -> None:
    manifest = tmp_path / "data" / "local_tables" / "character" / "manifest.json"
    name_index = tmp_path / "jsondata" / "CharacterNameIndex.json"
    _write_json(
        manifest,
        {
            "count": 1,
            "entries": [
                {
                    "charId": "chr_0035_liino",
                    "name": "梨诺",
                    "charTypeId": "Pulse",
                }
            ],
        },
    )
    _write_json(name_index, {})

    errors = sync_character_catalog.validate_character_catalog(manifest, name_index)

    assert len(errors) == 1
    assert "chr_0035_liino" in errors[0]


def test_runtime_name_index_merges_official_manifest(tmp_path: Path) -> None:
    name_index = tmp_path / "jsondata" / "CharacterNameIndex.json"
    manifest = tmp_path / "data" / "local_tables" / "character" / "manifest.json"
    _write_json(name_index, {"chr_0004_pelica": "佩丽卡"})
    _write_json(
        manifest,
        {
            "entries": [
                {"charId": "chr_0035_liino", "name": "梨诺", "charTypeId": "Pulse"}
            ]
        },
    )

    assert load_name_index(name_index)["chr_0035_liino"] == "梨诺"
