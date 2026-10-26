import pytest

from endfield_pcap.loadout_static import infer_weapon_refine_from_source_skills
from endfield_pcap.loadout_static import weapon_base_atk
from parser_core.battle_log_parser import _parse_loadout_slot_snapshot
from parser_core.battle_log_parser import _infer_verified_weapon_refine_from_levels


def test_missing_weapon_level_does_not_use_max_level_attack_value():
    assert weapon_base_atk("wpn_sword_0006", 0) is None


def test_loadout_snapshot_exposes_zero_weapon_level_as_unknown():
    row = _parse_loadout_slot_snapshot(
        "[10:00:00.000] LOADOUT slot=0 char=chr_0016_laevat "
        "weaponTemplate=wpn_sword_0006 weaponLv=0 refine=unknown break=0 "
        "equips={} equipSuit={}"
    )
    assert row is not None
    assert row["weapon_level"] is None
    assert row["weapon_base_atk"] is None


@pytest.mark.parametrize('weapon', ['wpn_funnel_0019', 'wpn_lance_0014'])
def test_new_weapon_potential_skill_level_identifies_full_refine(weapon):
    rows = [
        {'skill_str_id': f'sk_{weapon}', 'level': 9, 'potential_lv': 5, 'blackboard': {}},
        {'skill_str_id': 'wpn_attr_agi_high', 'level': 9, 'potential_lv': 5, 'blackboard': {}},
    ]
    assert infer_weapon_refine_from_source_skills(weapon, rows) == 5


def test_character_potential_and_attribute_level_do_not_imply_full_weapon():
    rows = [
        {'skill_str_id': 'sk_wpn_funnel_0019', 'level': 4, 'potential_lv': 5, 'blackboard': {}},
        {'skill_str_id': 'wpn_attr_agi_high', 'level': 9, 'potential_lv': 5, 'blackboard': {}},
    ]
    assert infer_weapon_refine_from_source_skills('wpn_funnel_0019', rows) is None


def test_different_weapon_skill_is_not_used():
    assert infer_weapon_refine_from_source_skills('wpn_funnel_0019', [
        {'skill_str_id': 'sk_wpn_funnel_0010', 'level': 9, 'blackboard': {}}
    ]) is None


@pytest.mark.parametrize('infer', [infer_weapon_refine_from_source_skills, _infer_verified_weapon_refine_from_levels])
@pytest.mark.parametrize('skill_level,gem_id,gem_cost,expected', [
    (4, 66, 3, 0),  # Farsight: base 1 + the matching force gem 3, no refinement.
    (5, 66, 3, 1), (9, 66, 3, 5), (4, 66, 1, 2),
    (4, 72, 3, 3),  # A burst gem does not increase Farsight's force skill.
    (3, 66, 3, None),  # Contradicts the observed bound gem.
])
def test_bound_gem_disambiguates_main_skill(infer, skill_level, gem_id, gem_cost, expected):
    assert infer('wpn_sword_0026', [{'skill_id': '3759', 'level': skill_level, 'potential_lv': 5}],
                 gem_terms=[{'term_num_id': gem_id, 'cost': gem_cost}]) == expected


@pytest.mark.parametrize('terms', [[], [{'term_num_id': 66, 'cost': -1}],
    [{'term_num_id': 66, 'cost': True}], [{'term_num_id': 66, 'cost': 3}, {'term_num_id': 66, 'cost': 1}]])
@pytest.mark.parametrize('infer', [infer_weapon_refine_from_source_skills, _infer_verified_weapon_refine_from_levels])
def test_incomplete_or_conflicting_gem_evidence_stays_unknown(infer, terms):
    assert infer('wpn_sword_0026', [{'skill_id': '3759', 'level': 4}], gem_terms=terms) is None
