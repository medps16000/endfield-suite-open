from __future__ import annotations

import json
import gzip
from pathlib import Path
import pytest

from endfield_pcap.overlay_effect_catalog import load_effect_catalog, classify_official_icon_group


def test_bundled_catalog_has_exact_official_ids_and_no_inferred_fields():
    root = Path(__file__).resolve().parents[1]
    actual = load_effect_catalog(root)
    numbers = json.loads((root / "data/local_tables/NumIdStrTable.json").read_text(encoding="utf-8-sig"))["buff_id"]["dic"]
    lookup = {number: entry["canonicalId"] for entry in actual for number in entry["numericIds"]}
    assert actual and lookup == numbers
    typed = json.loads(gzip.decompress((root / "data/local_static/buff/typed_roots.json.gz").read_bytes()))["entries"]
    for entry in actual:
        assert entry["label"] is None or entry["labelSource"] == "official-config-summary"
        assert entry["description"] is None or entry["descriptionSource"] == "official-config-summary"
        config = typed.get(entry["canonicalId"], {}).get("root", {})
        if entry["classification"] != "unclassified":
            order = config["iconConfig"]["_orderPriorityConfig"]
            assert config["hasIcon"] is True and order["useDirectoryValue"] is False
            assert entry["uiGroup"] == order["priorityEnum"]
            assert entry["classificationSource"] == "official-icon-priority-group"
    assert all(not any(key in entry for key in ("stackCount", "remainingMs", "durationMs")) for entry in actual)


def test_missing_or_malformed_catalog_does_not_break_existing_client(tmp_path):
    assert load_effect_catalog(tmp_path) == []
    path = tmp_path / "data/overlay/effects.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"schemaVersion": 1, "entries": [{"canonicalId": "effect", "numericIds": [True]}]}))
    assert load_effect_catalog(tmp_path) == []


@pytest.mark.parametrize("group,expected", [
    ("CommonCharBuff", "buff"), ("AttentionBuff", "buff"), ("KeywordBuff", "buff"),
    ("CommonCharDebuff", "debuff"), ("AttentionDebuff", "debuff"), ("KeywordDebuff", "debuff"),
    ("AttachedAndAbnormal", "unclassified"), ("AttentionBuffExtra", "unclassified"), (0, "unclassified"),
])
def test_only_exact_official_active_icon_group_is_a_display_classification(group, expected):
    assert classify_official_icon_group(True, group, False) == expected
    for has_icon, direct in ((False, False), (True, True), (True, None), (True, 0)):
        assert classify_official_icon_group(has_icon, group, direct) == "unclassified"


def test_catalog_rejects_ambiguous_identity_and_never_exposes_source_paths(tmp_path):
    path = tmp_path / "data/overlay/effects.json"
    path.parent.mkdir(parents=True)
    row = {"canonicalId": "effect", "numericIds": ["55"], "sourcePath": "private-path", "label": "unverified", "classification": "debuff"}
    path.write_text(json.dumps({"schemaVersion": 1, "entries": [row]}))
    result = load_effect_catalog(tmp_path)
    assert result[0]["label"] is None and result[0]["classification"] == "unclassified"
    assert "sourcePath" not in result[0]
    path.write_text(json.dumps({"schemaVersion": 1, "entries": [row, row]}))
    assert load_effect_catalog(tmp_path) == []


def test_target_generation_uses_exact_enemy_template_relation_only(tmp_path, monkeypatch):
    import importlib.util
    import sys

    script = Path(__file__).resolve().parents[1] / "scripts/build_overlay_effect_catalog.py"
    spec = importlib.util.spec_from_file_location("overlay_catalog_generator", script)
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    root = tmp_path / "client"
    for kind, entries in (("character", [{"charId": "char", "name": "Character"}]),
                          ("enemy", [{"templateId": "enemy_base", "name": "Enemy"},
                                     {"templateId": "other_base", "name": "Other"}])):
        manifest = root / "data/local_tables" / kind / "manifest.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(json.dumps({"sourceTablePath": "tables/EnemyTemplateDisplayInfoTable.json",
                                        "entries": entries}), encoding="utf-8")
    tables = tmp_path / "tables"
    tables.mkdir()
    (tables / "EnemyTable.json").write_text(json.dumps({
        "enemy_variant": {"enemyId": "enemy_variant", "templateId": "enemy_base"},
        "enemy_base_suffix": {"enemyId": "enemy_base_suffix", "templateId": "other_base"},
        "enemy_base_unlinked": {"enemyId": "enemy_base_unlinked", "templateId": "missing"},
        "invalid_identity": {"enemyId": "different_identity", "templateId": "enemy_base"},
    }), encoding="utf-8")
    overlay = root / "data/overlay"
    overlay.mkdir(parents=True)
    (overlay / "effects.json").write_text("existing effects", encoding="utf-8")
    (overlay / "descriptions.json").write_text("existing descriptions", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [str(script), "--root", str(root), "--targets-only"])
    generator.main()
    targets = json.loads((overlay / "targets.json").read_text(encoding="utf-8"))
    assert targets["entries"]["enemy_variant"]["label"] == "Enemy"
    assert targets["entries"]["enemy_base_suffix"]["label"] == "Other"
    assert "enemy_base_unlinked" not in targets["entries"]
    assert "invalid_identity" not in targets["entries"]
    assert len(targets["source"]["enemyTableSha256"]) == 64
    assert (overlay / "effects.json").read_text(encoding="utf-8") == "existing effects"
    assert (overlay / "descriptions.json").read_text(encoding="utf-8") == "existing descriptions"


def test_bundled_target_labels_include_exact_tower_variant():
    from endfield_pcap.overlay_effect_catalog import load_target_catalog

    root = Path(__file__).resolve().parents[1]
    targets = load_target_catalog(root)
    assert targets["eny_0120_klbear_tower015"]["label"] == "\u8680\u5f71\u566a\u96f7"
    assert targets["eny_0120_klbear_tower015"] == targets["eny_0120_klbear"]


def test_generation_loads_exact_packaged_sprite_and_distinguishes_button_progress(tmp_path):
    import base64
    import importlib.util

    script = Path(__file__).resolve().parents[1] / "scripts/build_overlay_effect_catalog.py"
    spec = importlib.util.spec_from_file_location("overlay_catalog_icon_generator", script)
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    table_path = tmp_path / "data/local_tables/NumIdStrTable.json"
    table_path.parent.mkdir(parents=True)
    table_path.write_text(json.dumps({"buff_id": {"dic": {}}}), encoding="utf-8")
    typed_path = tmp_path / "data/local_static/buff/typed_roots.json.gz"
    typed_path.parent.mkdir(parents=True)
    typed_path.write_bytes(gzip.compress(json.dumps({"entries": {
        "exact": {"root": {"hasIcon": True, "iconConfig": {"_spritePath": "exact_icon"}}},
        "missing": {"root": {"hasIcon": True, "iconConfig": {"_spritePath": "unavailable_icon"}}},
        "progress": {"root": {"hasIcon": True, "iconConfig": {"_spritePath": "", "showProgressInNormalSkillButton": True}}},
        "invalid": {"root": {"hasIcon": True, "iconConfig": {"_spritePath": "../exact_icon"}}},
        "inactive": {"root": {"hasIcon": False, "iconConfig": {"_spritePath": "exact_icon"}}},
    }}).encode("utf-8")))
    assets = tmp_path / "src/endfield_pcap/overlay_assets/effects"
    assets.mkdir(parents=True)
    (assets / "exact_icon.webp").write_bytes(b"packaged-exact-data")
    (assets / "unavailable_icon_variant.webp").write_bytes(b"never-use-a-similar-name")
    entries = {row["canonicalId"]: row for row in generator.build_catalog(tmp_path)["entries"]}
    assert base64.b64decode(entries["exact"]["icon"].split(",", 1)[1]) == b"packaged-exact-data"
    assert entries["exact"]["iconPresentation"] == "image"
    assert entries["missing"]["icon"] is None and entries["missing"]["iconPresentation"] == "missing-asset"
    assert entries["progress"]["icon"] is None and entries["progress"]["iconPresentation"] == "skill-button-progress"
    assert entries["invalid"]["icon"] is None
    assert entries["inactive"]["icon"] is None and entries["inactive"]["iconPresentation"] is None
    web_icons = tmp_path / "web-icons"
    web_icons.mkdir()
    (web_icons / "exact_icon.webp").write_bytes(b"web-exact-data")
    row = next(row for row in generator.build_catalog(tmp_path, web_icons)["entries"] if row["canonicalId"] == "exact")
    assert base64.b64decode(row["icon"].split(",", 1)[1]) == b"web-exact-data"
