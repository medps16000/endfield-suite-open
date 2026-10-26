from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_rdps_effect_catalog.py"
SPEC = importlib.util.spec_from_file_location("build_rdps_effect_catalog", SCRIPT)
assert SPEC and SPEC.loader
catalog_builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(catalog_builder)


def _record(source_id: str, name: str, description: str, *, kind: str = "character") -> dict[str, object]:
    return {
        "kind": kind,
        "source_id": source_id,
        "source_name": name,
        "section": "skill",
        "title": "测试条目",
        "description": description,
        "runtime_ids": [],
        "bb_keys": [],
    }


def _shapes(candidate: dict[str, object]) -> set[tuple[str, str]]:
    return {
        (str(effect["zone"]), str(effect["element"]))
        for effect in candidate["inferred_effects"]  # type: ignore[index]
    }


def test_extracts_external_fragile_and_elemental_team_damage() -> None:
    rows = [
        _record(
            "chr_0031_mifu",
            "弭弗",
            "使用上勾拳痛击前方的敌人，对其造成<@ba.pd>物理伤害</>，同时施加一定时间的<#ba.physicalvul>物理脆弱</>。",
        ),
        _record(
            "wpn_lance_0007",
            "灯火使命",
            "装备者施加灼热脆弱时，全队物理和灼热伤害<@ba.vup>+{dmg_up2:0%}</>。",
            kind="weapon",
        ),
    ]

    candidates = catalog_builder.extract_text_candidates(rows)

    assert _shapes(candidates[0]) == {("fragile", "physical")}
    assert _shapes(candidates[1]) == {("dmg_inc", "physical"), ("dmg_inc", "fire")}


def test_damage_taken_uses_vuln_zone_and_keeps_both_elements() -> None:
    rows = [
        _record(
            "chr_0028_wulfa",
            "洛茜",
            "爪印斫痕持续期间，目标每秒受到洛茜攻击力20%的物理伤害，且受到的物理伤害和灼热伤害+10%。",
        )
    ]

    candidates = catalog_builder.extract_text_candidates(rows)

    assert len(candidates) == 1
    assert _shapes(candidates[0]) == {("vuln_taken", "physical"), ("vuln_taken", "fire")}


def test_fracture_common_mechanism_is_physical_vulnerability_not_fragile() -> None:
    rows = [
        _record(
            "chr_0029_pograni",
            "骏卫",
            "对前方范围的敌人进行两段斩击，造成物理伤害并施加碎甲。",
        )
    ]

    candidates = catalog_builder.extract_text_candidates(rows)

    assert len(candidates) == 1
    assert _shapes(candidates[0]) == {("vuln_taken", "physical")}


def test_team_arts_strength_is_a_separate_effect_from_damage_increase() -> None:
    rows = [
        _record(
            "wpn_sword_0026",
            "遥望",
            "全队获得憧憬，造成的伤害+10%，源石技艺强度+20，防御力+10%。",
            kind="weapon",
        )
    ]

    candidates = catalog_builder.extract_text_candidates(rows)

    assert len(candidates) == 1
    assert _shapes(candidates[0]) == {("dmg_inc", "all"), ("arts_strength", "all")}


def test_shared_named_effect_inherits_attack_and_arts_strength() -> None:
    definition = _record(
        "chr_0029_pograni",
        "骏卫",
        "获得<@ba.key>士气激昂</>。<@ba.key>士气激昂</>效果：攻击力+10%，源石技艺强度+15。",
    )
    definition["title"] = "活着的旗帜"
    shared_effect = _record(
        "chr_0029_pograni",
        "骏卫",
        "当任意干员触发后续效果后，也会获得<@ba.key>士气激昂</>。",
    )
    shared_effect["title"] = "战术教导"
    rows = [definition, shared_effect]

    candidates = catalog_builder.extract_text_candidates(rows)
    shared = next(candidate for candidate in candidates if "任意干员" in str(candidate["text"]))

    assert _shapes(shared) == {("atk", "all"), ("arts_strength", "all")}


def test_triggering_on_fragile_does_not_claim_the_fragile_source() -> None:
    rows = [
        _record(
            "wpn_funnel_0016",
            "四二式·肃阵",
            "装备者通过自身技能施加法术脆弱时，使目标敌人受到的法术伤害+10%。",
            kind="weapon",
        )
    ]

    candidates = catalog_builder.extract_text_candidates(rows)

    assert len(candidates) == 1
    assert _shapes(candidates[0]) == {("vuln_taken", "spell")}


def test_combo_support_and_corresponding_element_damage_are_distinct() -> None:
    rows = [
        _record(
            "suit_atk02",
            "50式应龙",
            "当小队内任意干员施放战技时，使得该干员下一次连携技伤害+16%。",
            kind="equip",
        ),
        _record(
            "wpn_pistol_0009",
            "同类相食",
            "装备者消耗法术异常后，使目标敌人受到对应属性的伤害+12%。",
            kind="weapon",
        ),
    ]

    candidates = catalog_builder.extract_text_candidates(rows)

    assert _shapes(candidates[0]) == {("combo", "all")}
    assert _shapes(candidates[1]) == {("vuln_taken", "corresponding")}


def test_self_only_weapon_damage_is_not_external_candidate() -> None:
    rows = [
        _record(
            "wpn_sword_0022",
            "狼之绯",
            "装备者造成暴击伤害后获得狼血，物理和灼热伤害+10%，最多叠加5层。",
            kind="weapon",
        )
    ]

    assert catalog_builder.extract_text_candidates(rows) == []


def test_generated_review_catalog_has_unique_stable_ids_and_expected_proposals() -> None:
    payload = json.loads(
        (ROOT / "data" / "packet_semantics" / "rdps_effect_catalog_review.json").read_text(encoding="utf-8-sig")
    )
    effects = payload["effect_catalog"]
    ids = [row["rdps_effect_id"] for row in effects]

    assert len(ids) == len(set(ids))
    assert all(effect_id.startswith("RDPS-") for effect_id in ids)
    by_member = {
        member["canonical"]: row
        for row in effects
        for member in row["runtime_members"]
    }
    by_runtime_field = {
        (member["canonical"], member["bb_key"]): row
        for row in effects
        for member in row["runtime_members"]
    }
    assert by_member["buff_chr_0031_mifu_vulnerablephysic_comboskill"]["status"] == "proposed_addition"
    assert "buff_equipsuit_atk_02_addcombodamage_buff" not in by_member
    assert "buff_wpn_sword_0022_layer" not in by_member
    assert by_runtime_field[("buff_chr_0029_pograni_talent1", "physpell_up")]["zone"] == "arts_strength"
    assert by_runtime_field[("buff_wpn_sword_0026_celebration", "phy_spell_up")]["zone"] == "arts_strength"
    assert by_runtime_field[("buff_chr_0035_liino_atkup", "atk_up")]["rdps_effect_id"] == "RDPS-0080"
    assert by_runtime_field[("buff_chr_0035_liino_spellenhance", "spellenhance_rate")]["status"] == "proposed_addition"
    assert by_runtime_field[("buff_wpn_lance_0014_damageup", "atk_up")]["rdps_effect_id"] == "RDPS-0083"
    assert payload["text_source"]["provider"] == "AKEData Wiki"
    assert "@" in payload["text_source"]["version_id"]
    assert payload["summary"]["effects_overlapping_known_non_rdps"] >= 3
    suit_candidate = next(row for row in payload["text_candidates"] if row["source_group_id"] == "suit_atk02")
    assert suit_candidate["match_status"] == "excluded_by_review"


def test_active_registry_contains_approved_rescreen_and_suppresses_removed_fallbacks() -> None:
    registry = json.loads(
        (ROOT / "data" / "packet_semantics" / "rdps_semantics_registry.json").read_text(encoding="utf-8")
    )
    verified = registry["verified_effects"]
    known_non = registry["known_non_rdps"]["exact_buff_ids"]

    assert verified["buff_physical_do_fracture"]["effects"] == [
        {"zone": "vuln_taken", "element": "physical", "bb_key": "physical_res_down"}
    ]
    assert {effect["element"] for effect in verified["buff_wpn_lance_0007_dmgup2"]["effects"]} == {
        "physical",
        "fire",
    }
    assert verified["buff_chr_0031_mifu_vulnerablephysic_comboskill"]["effects"][0]["zone"] == "fragile"
    assert verified["buff_chr_0029_pograni_talent1"]["effects"][1]["zone"] == "arts_strength"
    assert verified["buff_wpn_sword_0026_celebration"]["effects"][1]["zone"] == "arts_strength"
    assert verified["buff_chr_0035_liino_atkup"]["effects"] == [
        {"zone": "atk", "element": "all", "bb_key": "atk_up"}
    ]
    assert "buff_chr_0035_liino_spellenhance" not in verified
    for element in ("pulse", "natural"):
        assert verified[f"buff_common_affixes_enhance_{element}"]["effects"] == [
            {"zone": "amp", "element": element, "bb_key": "rate"}]
    assert verified["buff_wpn_lance_0014_damageup"]["stack_limit"] == 4
    assert "buff_chr_0035_liino_ultskill_spellvulnerable" not in verified
    for key in (
        "buff_chr_0035_liino_normalskill_buff_atkup",
        "buff_chr_0035_liino_ultskill_buff_atkup",
        "buff_wpn_lance_0014_sign",
    ):
        assert known_non[key]["suppress_zone_effects"] is True
    assert "buff_chr_0035_liino_ultskill_spellvulnerable" not in known_non

    for key in (
        "buff_common_enemy_spell_status_do_frozen",
        "buff_common_enemy_spell_cryst_triggered_fx",
        "buff_wpn_sword_0022_layer",
    ):
        assert key not in verified
        assert known_non[key]["suppress_zone_effects"] is True
    assert "buff_rpg_equip_all_up_when_healed_01" not in verified
