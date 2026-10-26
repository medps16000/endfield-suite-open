"""Static arts points retain source identity and full precision."""
from copy import deepcopy

import pytest

from parser_core import battle_log_parser as parser


def equipped_arts_items():
    row = {'equipment_snapshot_complete': True,
           'equips': [{'slot': slot, 'item_id': 'item_equip_t4_suit_fire_natr01_edc_01',
                       'enhance_levels': [{'index': 3, 'level': 3}]} for slot in (2, 3)]}
    parser._derive_static_self_multiplier_entries(row)
    return row


def test_two_real_equipment_slots_preserve_equal_full_precision_arts_values():
    row = equipped_arts_items()
    entries = parser._derive_static_arts_strength_entries(row)
    assert len(entries) == 2
    assert {entry['source_slot'] for entry in entries} == {2, 3}
    assert all(entry['rate'] == 53.82 for entry in entries)
    assert sum(entry['rate'] for entry in entries) == pytest.approx(107.64)


def test_duplicate_same_equipment_property_is_one_source_and_conflict_is_reported():
    row = equipped_arts_items()
    original = next(item for item in row['equipment_baseline_attributes'] if item.get('attr_type') == 87)
    row['equipment_baseline_attributes'].append(deepcopy(original))
    assert len(parser._derive_static_arts_strength_entries(row)) == 2
    row['equipment_baseline_attributes'].append({**original, 'value': 50.0})
    entries = parser._derive_static_arts_strength_entries(row)
    assert len(entries) == 1
    assert entries[0]['source_slot'] != original['source_slot']
    assert any(item['reason'] == 'conflicting_static_arts_strength_source'
               for item in row['rdps_unresolved_static_multiplier_sources'])


@pytest.mark.parametrize('weapon,skill,key', [
    ('wpn_claym_0014', '2288', 'phy_spell_up'),
    ('wpn_funnel_0004', '2268', 'phy_spell_up'),
    ('wpn_funnel_0007', '2224', 'phy_spell_up'),
    ('wpn_funnel_0020', '4023', 'phy_spell_up'),
    ('wpn_sword_0007', '2226', 'phy_spell_up'),
    ('wpn_sword_0021', '2222', 'phy_spell_up'),
    ('wpn_funnel_0008', '2253', 'physpell'),
    ('wpn_claym_0016', '2253', 'physpell'),
    ('wpn_sword_0014', '2253', 'physpell'),
    ('wpn_sword_0026', '2253', 'physpell'),
    ('wpn_claym_0014', '2454', 'physpell'),
    ('wpn_funnel_0014', '2454', 'physpell'),
])
def test_every_verified_weapon_card_binding_uses_actual_bb_value(weapon, skill, key):
    row = {'weapon_template': weapon, 'weapon_source_skills': [{'skill_id': skill, 'bb': {key: 28.125}}]}
    entries = parser._derive_static_arts_strength_entries(row)
    assert len(entries) == 1
    assert entries[0]['rate'] == 28.125
    assert entries[0]['item_id'] == weapon
    assert entries[0]['source_id'] == parser._load_num_id_str_skill_map()[skill]
    assert entries[0]['evidence_kind'] == 'current_official_item_skill_card_attribute'


def test_two_distinct_weapon_card_properties_with_equal_points_are_both_counted():
    row = {'weapon_template': 'wpn_claym_0014', 'weapon_source_skills': [
        {'skill_id': '2288', 'bb': {'phy_spell_up': 20.0}},
        {'skill_id': '2454', 'bb': {'physpell': 20.0}},
    ]}
    entries = parser._derive_static_arts_strength_entries(row)
    assert len(entries) == 2
    assert sum(entry['rate'] for entry in entries) == 40.0
    row['weapon_source_skills'].append(deepcopy(row['weapon_source_skills'][0]))
    assert len(parser._derive_static_arts_strength_entries(row)) == 2


@pytest.mark.parametrize('row', [
    {'weapon_template': 'wpn_sword_0006', 'weapon_source_skills': [{'skill_id': '2226', 'bb': {'phy_spell_up': 50}}]},
    {'weapon_template': 'wpn_sword_0007', 'weapon_source_skills': [{'skill_id': '2226', 'bb': {'another_key': 50}}]},
    {'weapon_template': 'unknown_weapon', 'weapon_source_skills': [{'skill_id': '2253', 'bb': {'physpell': 50}}]},
    {'weapon_template': 'wpn_sword_0007', 'weapon_source_skills': [{'skill_id': 'unknown_skill', 'bb': {'phy_spell_up': 50}}]},
])
def test_unknown_weapon_or_same_named_unrelated_bb_is_not_static_strength(row):
    assert parser._derive_static_arts_strength_entries(row) == []


@pytest.mark.parametrize('suit', ['suit_fire_natr01', 'suit_pulse_cryst01'])
def test_both_current_arts_suits_are_one_source_each_and_require_activation(suit):
    effect = {'suit_id': suit, 'active': True, 'value': {'phy_spell_up': 30.0}}
    row = {'suit_effects': [effect, deepcopy(effect)],
           'equipment_baseline_attributes': [{'source_type': 'equip_suit_baseline',
               'source_id': suit, 'field': 'phy_spell_up', 'value': 30.0, 'evidence_kind': 'official_EquipSuit_text'}]}
    entries = parser._derive_static_arts_strength_entries(row)
    assert len(entries) == 1 and entries[0]['rate'] == 30.0
    row['suit_effects'] = [{**effect, 'active': False}]
    assert parser._derive_static_arts_strength_entries(row) == []


def test_known_self_equipment_strength_dilutes_external_strength_correctly():
    static = parser._derive_static_arts_strength_entries(equipped_arts_items())
    hit = {'character_key': 'chr_0016_laevat', 'source_actor_id': '1001',
           'target_enemy_key': 'eny_0051_rodin', 'target_actor_id': '2001',
           'hit_value': 237.64, 'ts_ms': 1000, 'line_no': 10,
           'damage_element': 'physical', 'damage_school': 'physical',
           'damage_decorate_mask': 0x8000000, 'static_arts_strength_entries': static}
    buff = {'event_key': 'buff_chr_0029_pograni_talent1', 'source_character_key': 'chr_0029_pograni',
            'target_character_key': 'chr_0016_laevat', 'target_actor_id': '1001',
            'bb_values': {'atk_up': 0.0, 'physpell_up': 30.0}}
    window = {**buff, 'uid': '4001', 'start_ts_ms': 0, 'end_ts_ms': 2000,
              'zone_effects': parser._collect_zone_effects(buff)}
    result = parser._allocate_rdps_for_hit(hit, [window], include_debug=True)
    assert result['contributions_map'] == pytest.approx({'chr_0016_laevat': 207.64, 'chr_0029_pograni': 30.0})
