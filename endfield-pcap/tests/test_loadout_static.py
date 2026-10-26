from endfield_pcap.loadout_static import equip_piece_catalog, infer_weapon_refine_from_source_skills, weapon_catalog
from endfield_pcap import loadout_static
from parser_core import hit_debug
import pytest


def test_equipment_missing_level_is_not_maximum_or_neighbor_level(monkeypatch):
    monkeypatch.setattr(loadout_static, 'equip_piece_catalog', lambda: {'fixture': {'affixes': [
        {'kind': 'sub', 'index': 0, 'desc': 'first', 'value_options': [10, 20, 30, 40]},
        {'kind': 'sub', 'index': 1, 'desc': 'second', 'value_options': [1, 2, 3, 4]},
    ]}})
    rows = loadout_static.selected_equip_affixes('fixture', {1: 3})
    assert rows[0]['value'] == 40
    assert rows[1]['level'] is None and rows[1]['value'] is None
    assert all(r['value'] is None for r in loadout_static.selected_equip_affixes('fixture', {}))
    assert 'sub2:second=unknown' in loadout_static.format_equip_stats('fixture', {1: 3})


@pytest.mark.parametrize('selector', [loadout_static._select_option, hit_debug._selected_option_for_level])
def test_attribute_option_index_is_exact_and_never_reinterpreted_as_one_based(selector):
    assert selector([10, 20, 30], 0) == 10
    assert selector([10, 20, 30], 2) == 30
    for value in (3, -1, None, True, 2.5):
        assert selector([10, 20, 30], value) is None


def test_weapon_refine_keeps_ambiguous_third_level_unknown() -> None:
    source_skills = [
        {
            "skill_int_id": 1249,
            "level": 9,
            "blackboard": {"atk": 0.39},
        },
        {
            "skill_int_id": 1241,
            "level": 9,
            "blackboard": {"agi": 156},
        },
        {
            "skill_int_id": 2668,
            "level": 4,
            "blackboard": {
                "cd": 0.1,
                "cryst_dmg_up": 0.256,
                "cryst_dmg_up2": 0.32,
                "duration": 20,
                "duration2": 20,
                "spell_damage_taken_up": 0.096,
                "spell_dmg_taken_up": 0.12,
            },
        },
    ]

    # Total level 4 permits base/refine pairs 4+0, 3+1, 2+2, 1+3.
    # Blackboard magnitudes and character potential cannot disambiguate them.
    assert infer_weapon_refine_from_source_skills("wpn_pistol_0011", source_skills) is None
    source_skills[2]['level'] = 9
    assert infer_weapon_refine_from_source_skills("wpn_pistol_0011", source_skills) == 5


def test_infer_weapon_refine_from_source_skills_uses_passive_level_hint_when_values_overlap() -> None:
    source_skills = [
        {
            "skill_int_id": 2443,
            "level": 1,
            "blackboard": {"wisd": 16},
        },
        {
            "skill_int_id": 2444,
            "level": 2,
            "blackboard": {"atk": 0.072},
        },
        {
            "skill_int_id": 2217,
            "level": 9,
            "blackboard": {
                "atk_up": 0.224,
                "duration": 20,
                "hp_up": 0.28,
                "lv": 9,
                "ower_char_type": "#",
                "team_char_type": "#",
            },
        },
    ]

    assert infer_weapon_refine_from_source_skills("wpn_funnel_0005", source_skills) == 5


def test_weapon_refine_does_not_use_character_potential_to_disambiguate() -> None:
    source_skills = [
        {
            "skill_int_id": 1391,
            "level": 5,
            "potential_lv": 5,
            "blackboard": {"mainattr": 71},
        },
        {
            "skill_int_id": 2253,
            "level": 9,
            "potential_lv": 5,
            "blackboard": {"atk": 0, "physpell": 78},
        },
        {
            "skill_int_id": 2228,
            "level": 4,
            "potential_lv": 5,
            "blackboard": {
                "duration": 15,
                "lv": 4,
                "second_attr_up": 0.16,
                "spell_damage_taken_up": 0.144,
            },
        },
    ]

    assert infer_weapon_refine_from_source_skills("wpn_funnel_0008", source_skills) is None
    source_skills[2]['level'] = 9
    assert infer_weapon_refine_from_source_skills("wpn_funnel_0008", source_skills) == 5


def test_current_local_tables_fill_items_missing_from_akedata() -> None:
    # The 2026-09-02 update added these records to local tables before the
    # external AKEData mirror had corresponding item files.
    weapon = weapon_catalog()["wpn_funnel_0019"]
    assert weapon["name"] == "寒夜幽影"
    assert weapon["max_lv"] == 90

    equip = equip_piece_catalog()["item_equip_t4_suit_spellburst_body_01"]
    assert equip["suit_id"] == "suit_spellburst"
    assert equip["suit_name"] == "险关"
    assert equip["affixes"]
