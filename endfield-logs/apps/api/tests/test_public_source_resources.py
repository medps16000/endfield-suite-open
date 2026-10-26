"""The public source checkout must not need private generated data to import."""
import json

import pytest

from app.services import public_data


def test_missing_optional_character_resources_return_an_empty_catalog(tmp_path, monkeypatch):
    monkeypatch.setattr(public_data, "CHARACTER_MANIFEST_PATH", tmp_path / "missing-character.json")
    monkeypatch.setattr(public_data, "LOCAL_CHARACTER_MANIFEST_PATH", tmp_path / "missing-local.json")
    monkeypatch.setattr(public_data, "WEB_PUBLIC_ROOT", tmp_path)
    assert public_data._load_character_catalog() == ({}, {})


def test_present_malformed_character_resource_is_not_silently_ignored(tmp_path, monkeypatch):
    path = tmp_path / "character.json"
    path.write_text("{", encoding="utf-8")
    monkeypatch.setattr(public_data, "CHARACTER_MANIFEST_PATH", path)
    with pytest.raises(json.JSONDecodeError):
        public_data._load_character_catalog()
