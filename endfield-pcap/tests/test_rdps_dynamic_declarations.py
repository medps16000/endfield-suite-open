"""Current corrosion operands and historical HP approval do not grant new credit."""
import copy
import json

import pytest

from parser_core import battle_log_parser as parser


CORROSION_KEYS = (
    "buff_common_natural_natural_corrupt_do",
    "buff_common_natural_cryst_triggered",
)


@pytest.mark.parametrize("key", CORROSION_KEYS)
def test_corrosion_declaration_matches_actual_resistance_attribute_operands(key):
    registry = json.loads((parser._repo_root() / "data/packet_semantics/rdps_semantics_registry.json").read_text(encoding="utf-8"))
    entry = registry["verified_effects"][key]
    root = parser._load_current_buff_roots()[key]
    modifiers = root["attributeModifier"]["attributeModifiers"]
    resistance_types = {
        "PhysicalResistance", "FireResistance", "PulseResistance",
        "CrystResistance", "NaturalResistance",
    }
    assert len(modifiers) == 10
    assert {(row["attributeType"], row["param"]["blackboardKey"]) for row in modifiers} == {
        (attribute, operand)
        for attribute in resistance_types
        for operand in ("def_decrease", "additional_def_decrease")
    }
    assert all(row["formulaItem"] == "BaseAddition" and row["modifyAttributeType"] == "Specific"
               and row["param"]["useBlackboardKey"] is True for row in modifiers)
    assert entry["effects"] == [
        {"zone": "res", "element": "all", "bb_key": "def_decrease"},
        {"zone": "res", "element": "all", "bb_key": "additional_def_decrease"},
    ]
    assert entry["dynamic"] is True
    assert not entry.get("dynamic_effects")
    controls = {"start_def_decrease", "def_decrease_tick", "max_def_decrease"}
    assert controls <= set(entry["ignored_bb_keys"])
    assert not controls.intersection(effect["bb_key"] for effect in entry["effects"])
    assert not {"def_decrease", "additional_def_decrease"}.intersection(entry["ignored_bb_keys"])


@pytest.mark.parametrize("key", CORROSION_KEYS)
def test_declared_corrosion_operands_do_not_enable_resistance_allocation(key):
    record = {
        "event_key": key, "raw_event_key": key,
        "source_character_key": "chr_0004_pelica", "source_actor_id": "2001",
        "target_enemy_key": "eny_0051_rodin", "target_actor_id": "3001",
        "bb_values": {"def_decrease": -20.0, "additional_def_decrease": -5.0,
                      "start_def_decrease": -10.0, "def_decrease_tick": -1.0,
                      "max_def_decrease": -30.0},
    }
    # Signed captured fields are retained; declaring them must not revive
    # a time-integrated rate or an independent contribution for each control.
    original_values = dict(record["bb_values"])
    assert parser._collect_zone_effects(record) == []
    assert parser._extract_dynamic_effect_specs(record) == []
    assert record["rdps_unresolved_dynamic_effect"] is True
    assert record["bb_values"] == original_values

    attacker = "chr_0016_laevat"
    hit = {"character_key": attacker, "target_enemy_key": "eny_0051_rodin", "target_actor_id": "3001",
           "hit_value": 120.0, "ts_ms": 500, "skill_key": "explicit_fixture",
           "damage_element": "pulse", "damage_school": "spell",
           "target_resistance_points": {"pulse": 20.0}}
    # These supplied magnitudes are not yet bound to the hit through an
    # observed attribute refresh. This checks the current evidence guard;
    # it is not a derivation of the signed-field counterfactual formula.
    window = {"uid": "7", "event_key": key, "source_character_key": "chr_0004_pelica",
              "target_character_key": "eny_0051_rodin", "target_actor_id": "3001",
              "start_ts_ms": 0, "end_ts_ms": 1000,
              "zone_effects": [
                  {"zone": "res", "element": "pulse", "rate": amount, "bb_key": operand,
                   "external_rdps_allowed": True, "rdps_whitelist_keys": [key]}
                  for operand, amount in (("def_decrease", 20.0), ("additional_def_decrease", 5.0))
              ]}
    result = parser._allocate_rdps_for_hit(hit, [window], include_debug=True)
    assert result["contributions_map"] == {attacker: 120.0}
    assert hit["rdps_unresolved_effect_conditions"] is True
    reasons = [row for row in result["ignored_effects"] if row["reason"] == "resistance_counterfactual_unresolved"]
    assert len(reasons) == 2
    assert {row["rate"] for row in reasons} == {20.0, 5.0}
    assert all(row["event_key"] == key and row["rdps_whitelist_keys"] == [key] for row in reasons)


def test_historical_hp_approval_does_not_alias_to_current_damage_buff(monkeypatch):
    hp_key = "buff_wpn_funnel_0014_hp"
    damage_key = "buff_wpn_funnel_0014_up"
    registry = json.loads((parser._repo_root() / "data/packet_semantics/rdps_semantics_registry.json").read_text(encoding="utf-8"))
    roots = parser._load_current_buff_roots()
    hp_root = roots[hp_key]
    hp_modifiers = hp_root["attributeModifier"]["attributeModifiers"]
    assert [(row["attributeType"], row["formulaItem"], row["param"]["blackboardKey"])
            for row in hp_modifiers] == [("MaxHp", "BaseFinalAddition", "hp_up")]
    assert damage_key not in registry["verified_effects"][hp_key].get("aliases", [])
    assert hp_key not in registry["verified_effects"][damage_key].get("aliases", [])
    record = {"event_key": hp_key, "raw_event_key": hp_key, "source_character_key": "chr_0004_pelica",
              "target_character_key": "chr_0023_antal", "packet_mapping": {"weapon_id": "wpn_funnel_0014"},
              "bb_values": {"hp_up": 0.2, "dmg_up": 0.3}}
    non_rdps = parser._rdps_registry_known_non_rdps_entry(record)
    assert non_rdps["proof_kind"] == "typed_root_structural"
    assert non_rdps["suppress_zone_effects"] is True
    assert parser._collect_zone_effects(record) == []
    damage_record = {**record, "event_key": damage_key, "raw_event_key": damage_key}
    damage_record.pop("rdps_unresolved_semantic_condition", None)
    damage_effects = parser._collect_zone_effects(damage_record)
    assert {(row["zone"], row["element"], row["rate"]) for row in damage_effects} == {
        ("dmg_inc", "fire", 0.3), ("dmg_inc", "pulse", 0.3),
    }
    assert all(row["rdps_whitelist_keys"] == [damage_key] for row in damage_effects)

    # Structural suppression must cease if this same ID gains a direct
    # damage-relevant property; the historical name is not an exemption.
    changed_root = copy.deepcopy(hp_root)
    changed_root["attributeModifier"]["attributeModifiers"][0]["attributeType"] = "FireDamageIncrease"
    monkeypatch.setattr(parser, "_load_current_buff_roots", lambda: {**roots, hp_key: changed_root})
    assert parser._rdps_registry_known_non_rdps_entry(record) is None
