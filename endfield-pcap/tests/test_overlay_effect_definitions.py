from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from endfield_pcap.overlay_effect_catalog import load_effect_definitions


def _generator():
    path = Path(__file__).resolve().parents[1] / "scripts/build_overlay_effect_definitions.py"
    spec = importlib.util.spec_from_file_location("overlay_definition_generator", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_exact_typed_creation_edges_skip_disabled_and_unresolved_dynamic_ids():
    generator = _generator()
    root = {"nested": [
        {"$type": "Beyond.Gameplay.Core.CreateBuffAction.Data", "isEnable": True, "asChildBuff": True,
         "serverActionIndex": 4, "buffs": [
             {"$type": "Beyond.Gameplay.Core.CreateBuffActionInput", "buffId": "actual", "readIdFromBlackboard": False},
             {"$type": "Beyond.Gameplay.Core.CreateBuffActionInput", "buffId": "unsafe-default",
              "readIdFromBlackboard": True, "buffIdKey": "selected_buff"}]},
        {"isEnable": False, "buffs": [{"$type": "Beyond.Gameplay.Core.BuffInput", "buffId": "disabled"}]},
        {"buffId": "looks-related-but-untyped"},
    ]}
    edges, unresolved = generator._edges(root, "SkillData", "skill")
    assert [row["toId"] for row in edges] == ["actual"]
    assert edges[0]["asChildBuff"] is True and edges[0]["serverActionIndex"] == 4
    assert unresolved[0]["reason"] == "dynamic-blackboard-id"
    assert unresolved[0]["blackboardKey"] == "selected_buff"
    assert "toId" not in unresolved[0]


def test_skill_patch_overrides_template_before_explicit_buff_assignment():
    generator = _generator()
    skill = {"blackboard": [{"key": "max_stack", "valueDouble": 10.0},
                            {"key": "duration", "valueDouble": 10.0}]}
    patch = [{"level": 1, "blackboard": [{"key": "max_stack", "value": 16.0},
                                         {"key": "duration", "value": 20.0}]}]
    steps = [{"toKind": "BuffData", "assignBlackboard": True, "assignItems": [
        {"targetKey": "max_stack", "inputValueKey": "max_stack", "useDirectValue": False},
        {"targetKey": "duration", "inputValueKey": "duration", "useDirectValue": False}]}]
    buff = {"stackingSettings": {"useMaxStackCntKey": True, "maxStackCntKey": "max_stack", "maxStackCnt": 2},
            "duration": {"useBlackboardKey": True, "blackboardKey": "duration", "value": 0.0}}
    actual = generator._effective_parameters(skill, patch, steps, buff)
    assert actual["templateBlackboard"]["max_stack"]["value"] == 10
    assert actual["effectiveLevels"][0]["maxStack"]["value"] == 16
    assert actual["effectiveLevels"][0]["duration"]["value"] == 20
    assert actual["effectiveLevels"][0]["maxStack"]["valueSource"] == "SkillPatchTable"
    # A referenced key that was never explicitly assigned stays unresolved.
    steps[0]["assignBlackboard"] = False
    assert generator._effective_parameters(skill, patch, steps, buff)["effectiveLevels"][0]["maxStack"] is None


def test_source_roots_use_official_foreign_keys_and_keep_multiple_owners():
    generator = _generator()
    tables = {"I18nTextTable_CN": {"1": "Weapon A", "2": "Weapon B"},
              "ItemTable": {"weaponA": {"name": {"id": 1}}, "weaponB": {"name": {"id": 2}}},
              "WeaponBasicTable": {"weaponA": {"weaponPotentialSkill": "shared"},
                                   "weaponB": {"weaponPotentialSkill": "shared"},
                                   "shared_prefix": {}},
              "CharGrowthTable": {}, "PotentialTalentEffectTable": {}, "EquipSuitTable": {}, "EquipTable": {}}
    roots = generator._source_roots(tables)
    assert [(row["sourceId"], row["skillId"]) for row in roots] == [("weaponA", "shared"), ("weaponB", "shared")]
    assert all(row["field"] == "WeaponBasicTable.weaponPotentialSkill" for row in roots)


def test_enemy_source_roots_use_only_born_buff_foreign_keys_and_exact_display_names():
    generator = _generator()
    tables = {"I18nTextTable_CN": {"1": "Template Name", "2": "Override Name"},
              "EnemyTable": {
                  "enemy_variant": {"enemyId": "enemy_variant", "templateId": "template", "bornBuffs": ["shared_buff"],
                                    "aiTemplateId": "skill-looking-ai"},
                  "enemy_other": {"enemyId": "enemy_other", "templateId": "template", "bornBuffs": ["shared_buff"]},
                  "shared_buff_prefix": {"enemyId": "shared_buff_prefix", "templateId": "missing", "bornBuffs": []}},
              "EnemyDisplayInfoTable": {"enemy_variant": {"enemyId": "enemy_variant", "name": {"id": 2}}},
              "EnemyTemplateDisplayInfoTable": {"template": {"templateId": "template", "name": {"id": 1}}},
              "WeaponBasicTable": {}, "CharGrowthTable": {}, "PotentialTalentEffectTable": {},
              "EquipSuitTable": {}, "EquipTable": {}, "ItemTable": {}}
    roots = generator._source_roots(tables)
    assert len(roots) == 2
    assert {row["sourceId"]: row["sourceName"] for row in roots} == {
        "enemy_variant": "Override Name", "enemy_other": "Template Name"}
    assert all(row["sourceType"] == "enemy" and row["rootId"] == "shared_buff" and row["skillId"] == ""
               and row["field"] == "EnemyTable.bornBuffs" and row["sourceScope"] == "born-buff-configuration" for row in roots)


def _write_bundle(tmp_path, *, definitions=None):
    directory = tmp_path / "data/overlay"
    directory.mkdir(parents=True)
    fingerprint = {"source": {"StreamingAssets/42A8FCA6": {"sha256": "b" * 64}},
                   "extracted": {"TableCfg": {"sha256": "c" * 64}}}
    (directory / "effects.json").write_text(json.dumps({"source": {"typedRootsSha256": "a" * 64}}))
    (tmp_path / "data/game_resource_snapshot.json").write_text(json.dumps(fingerprint))
    row = {"effectId": "exact", "canonicalId": "exact", "numericIds": ["42"], "definitionOnly": True,
           "label": "Weapon · 配置", "labelSource": "official-source-config-summary", "description": None,
           "sourceRelations": [{"sourceType": "weapon", "sourceId": "weapon", "sourceName": "Weapon",
                                "skillId": "skill", "skillName": "Skill", "skillDescription": "description",
                                "field": "WeaponBasicTable.weaponPotentialSkill", "relationKind": "official-static-definition"}],
           "staticConfig": {"valueKind": "static-configuration", "lifeType": "Infinity", "stackingType": "Stack"},
           "details": {"sourcePaths": [], "skillParameters": []}, "sourcePath": "private-local-path"}
    value = {"schemaVersion": 1, "coverage": "official-static-definition-graph",
             "source": {"typedRootsSha256": "a" * 64, "resourceFingerprint": fingerprint}, "entries": [row]}
    if definitions:
        definitions(value)
    (directory / "definitions.json").write_text(json.dumps(value), encoding="utf-8")


def test_definitions_loader_requires_matching_resource_fingerprint_and_strips_paths(tmp_path):
    _write_bundle(tmp_path)
    actual = load_effect_definitions(tmp_path)
    assert len(actual) == 1 and actual[0]["sourceRelations"][0]["sourceId"] == "weapon"
    assert "sourcePath" not in actual[0] and "sourceId" not in actual[0]
    receipt_path = tmp_path / "data/game_resource_snapshot.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["source"]["StreamingAssets/42A8FCA6"]["sha256"] = "f" * 64
    receipt_path.write_text(json.dumps(receipt))
    assert load_effect_definitions(tmp_path) == []


def test_definition_details_bind_only_exact_graph_resources(tmp_path):
    def configure(value):
        value["source"]["skillManifestSha256"] = "e" * 64
        value["source"]["resourcesSha256"] = {
            "BuffData/exact": "b" * 64, "SkillData/skill": "c" * 64,
            "SkillData/invalid": "bad-hash", "SkillData/unrelated": "d" * 64,
        }
        value["entries"][0]["details"]["sourcePaths"] = [{"steps": [
            {"fromKind": "SkillData", "fromId": "skill", "toKind": "BuffData", "toId": "exact"},
            {"fromKind": "SkillData", "fromId": "invalid"},
            {"fromKind": "UnprovenKind", "fromId": "unrelated"},
        ]}]
    _write_bundle(tmp_path, definitions=configure)
    actual = load_effect_definitions(tmp_path)
    assert actual[0]["details"]["sourceResourcesSha256"] == {
        "BuffData/exact": "b" * 64, "SkillData/skill": "c" * 64}
    assert actual[0]["details"]["definitionGraphBinding"] == {
        "typedRootsSha256": "a" * 64, "skillManifestSha256": "e" * 64}


def test_definitions_loader_rejects_ambiguous_identity_and_guessed_source(tmp_path):
    _write_bundle(tmp_path, definitions=lambda value: value["entries"].append(value["entries"][0]))
    assert load_effect_definitions(tmp_path) == []
    path = tmp_path / "data/overlay/definitions.json"
    value = json.loads(path.read_text())
    value["entries"] = value["entries"][:1]
    value["entries"][0]["sourceRelations"][0]["relationKind"] = "name-prefix"
    path.write_text(json.dumps(value))
    assert load_effect_definitions(tmp_path) == []


def test_enemy_definition_loader_requires_explicit_born_scope(tmp_path):
    def enemy(value):
        row = value["entries"][0]["sourceRelations"][0]
        row.update(sourceType="enemy", sourceId="enemy", enemyTemplateId="template",
                   field="EnemyTable.bornBuffs", sourceScope="born-buff-configuration")

    _write_bundle(tmp_path, definitions=enemy)
    assert load_effect_definitions(tmp_path)[0]["sourceRelations"][0]["sourceType"] == "enemy"
    path = tmp_path / "data/overlay/definitions.json"
    value = json.loads(path.read_text())
    value["entries"][0]["sourceRelations"][0]["sourceScope"] = "guessed-all-skills"
    path.write_text(json.dumps(value))
    assert load_effect_definitions(tmp_path) == []


def test_definitions_only_generation_leaves_existing_catalogs_untouched(tmp_path, monkeypatch):
    import sys
    import types

    script = Path(__file__).resolve().parents[1] / "scripts/build_overlay_effect_catalog.py"
    spec = importlib.util.spec_from_file_location("overlay_definitions_cli", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    overlay = tmp_path / "data/overlay"
    overlay.mkdir(parents=True)
    originals = {name: ("preserve " + name).encode() for name in ("effects.json", "targets.json", "descriptions.json")}
    for name, content in originals.items():
        (overlay / name).write_bytes(content)
    expected = {"schemaVersion": 1, "summary": {"entryCount": 0}, "entries": []}
    monkeypatch.setitem(sys.modules, "build_overlay_effect_definitions",
                        types.SimpleNamespace(build_definitions=lambda root, source_root: expected))
    monkeypatch.setattr(sys, "argv", [str(script), "--root", str(tmp_path), "--definitions-only"])
    module.main()
    assert json.loads((overlay / "definitions.json").read_text()) == expected
    assert all((overlay / name).read_bytes() == content for name, content in originals.items())


def test_bundled_wolf_definition_has_source_search_text_and_patched_static_values():
    root = Path(__file__).resolve().parents[1]
    entries = {row["canonicalId"]: row for row in load_effect_definitions(root)}
    layer, final = entries["buff_wpn_sword_0022_layer"], entries["buff_wpn_sword_0022_final"]
    for row in (layer, final):
        source = next(item for item in row["sourceRelations"] if item["sourceId"] == "wpn_sword_0022")
        assert source["sourceName"] == "狼之绯" and "狼血" in source["skillDescription"]
        assert row["label"] != "狼血"
        assert row["definitionOnly"] is True
        assert not any(key in row for key in ("sourceId", "stackCount", "remainingMs", "durationMs"))
    assert layer["staticConfig"]["stackingType"] == "Stack"
    assert layer["staticConfig"]["lifeType"] == "Infinity"
    assert {level["maxStack"]["value"] for params in layer["details"]["skillParameters"]
            for level in params["effectiveLevels"]} == {16}
    assert {level["duration"]["value"] for params in final["details"]["skillParameters"]
            for level in params["effectiveLevels"]} == {20}


def test_bundled_enemy_definition_preserves_exact_variant_born_relation():
    root = Path(__file__).resolve().parents[1]
    entries = {row["canonicalId"]: row for row in load_effect_definitions(root)}
    relations = entries["buff_eny_0120_klbear_born_fx"]["sourceRelations"]
    source = next(row for row in relations if row["sourceType"] == "enemy" and row["sourceId"] == "eny_0120_klbear_tower015")
    assert source["sourceName"] == "蚀影噪雷"
    assert source["enemyTemplateId"] == "eny_0120_klbear"
    assert source["sourceScope"] == "born-buff-configuration"
    assert source["field"] == "EnemyTable.bornBuffs" and source["skillId"] == ""
