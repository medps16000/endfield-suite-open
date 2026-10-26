"""Attack basis contracts with explicit active damage calculations and sources."""
from copy import deepcopy
from itertools import permutations
import json

import pytest

from parser_core import battle_log_parser as parser


ATTACKER = 'chr_0016_laevat'
PROVIDER = 'chr_0023_antal'
OTHER_PROVIDER = 'chr_0025_ardelia'


def hit(damage, **extra):
    return {'character_key': ATTACKER, 'source_actor_id': '1001',
            'target_enemy_key': 'eny_0051_rodin', 'target_actor_id': '3001',
            'hit_value': damage, 'ts_ms': 500, 'line_no': 10, 'skill_key': 'explicit_calculation_fixture',
            'damage_element': 'fire', 'damage_school': 'spell', 'damage_decorate_mask': 0x100,
            'damage_calculation': {'source': 'current_typed_damage_unit', 'simpleCalculation': True,
                                   'atkCalculation': None}, **extra}


def window(rate, source=PROVIDER, zone='atk', attr_type=None):
    effect = {'zone': zone, 'element': 'all' if zone == 'atk' else 'fire', 'rate': rate,
              'external_rdps_allowed': True, 'rdps_whitelist_keys': [f'approved_{zone}_fixture']}
    if attr_type is not None:
        effect['attr_type'] = attr_type
    return {'uid': source + zone, 'event_key': source + '_' + zone + '_fixture',
            'source_character_key': source, 'target_character_key': ATTACKER, 'target_actor_id': '1001',
            'start_ts_ms': 0, 'end_ts_ms': 1000, 'zone_effects': [effect]}


@pytest.mark.parametrize('skill_id', ['1249', 'wpn_sp_attr_atk_high'])
def test_basic_weapon_attack_percent_enters_retained_attack_denominator(skill_id):
    loadout = {'weapon_template': 'wpn_sword_0006',
               'weapon_source_skills': [{'skill_id': skill_id, 'bb': {'atk': 0.39}}]}
    entries = parser._derive_static_self_multiplier_entries(loadout)
    attack_entries = [entry for entry in entries if entry['zone'] == 'atk']
    assert len(attack_entries) == 1
    assert attack_entries[0]['source_id'] == 'wpn_sp_attr_atk_high'
    assert attack_entries[0]['rate'] == pytest.approx(0.39)
    result = parser._allocate_rdps_for_hit(hit(159, static_multiplier_entries=entries), [window(0.2)], include_debug=True)
    assert result['contributions_map'] == pytest.approx({ATTACKER: 139, PROVIDER: 20})
    assert result['zones'][0]['self_rate'] == pytest.approx(0.39)


@pytest.mark.parametrize('basic_skill,card_skill', [('1249', '2234'),
                                                ('wpn_sp_attr_atk_high', 'sk_wpn_sword_0012')])
def test_thermal_cutter_basic_and_card_percents_are_both_retained(basic_skill, card_skill):
    loadout = {'weapon_template': 'wpn_sword_0012', 'weapon_source_skills': [
        {'skill_id': basic_skill, 'bb': {'atk': 0.39}},
        {'skill_id': card_skill, 'bb': {'atk_up': 0.16, 'atk_up2': 0.08, 'duration': 15, 'max_stack': 2}},
    ]}
    entries = parser._derive_static_self_multiplier_entries(loadout)
    attack_entries = [entry for entry in entries if entry['zone'] == 'atk']
    assert {(entry['source_id'], entry['bb_key']) for entry in attack_entries} == {
        ('wpn_sp_attr_atk_high', 'atk'), ('sk_wpn_sword_0012', 'atk_up'),
    }
    assert sum(entry['rate'] for entry in attack_entries) == pytest.approx(0.55)
    assert not any(entry.get('bb_key') == 'atk_up2' for entry in entries)
    result = parser._allocate_rdps_for_hit(hit(163, static_multiplier_entries=entries), [window(0.08)])
    assert result['contributions_map'] == pytest.approx({ATTACKER: 155, PROVIDER: 8})


def test_equal_percent_from_distinct_equipped_skill_sources_is_not_deduplicated():
    # Coefficient equality cannot collapse distinct source slots. These are
    # explicit observed values, not selected weapon-level defaults.
    loadout = {'weapon_template': 'wpn_sword_0012', 'weapon_source_skills': [
        {'skill_id': 'wpn_sp_attr_atk_high', 'bb': {'atk': 0.16}},
        {'skill_id': 'sk_wpn_sword_0012', 'bb': {'atk_up': 0.16}},
    ]}
    entries = parser._derive_static_self_multiplier_entries(loadout)
    assert sum(entry['rate'] for entry in entries if entry['zone'] == 'atk') == pytest.approx(0.32)
    result = parser._allocate_rdps_for_hit(hit(140, static_multiplier_entries=entries), [window(0.08)])
    assert result['contributions_map'] == pytest.approx({ATTACKER: 132, PROVIDER: 8})


def test_weapon_basic_skill_not_in_equipped_weapon_list_cannot_supply_attack():
    loadout = {'weapon_template': 'wpn_lance_0014', 'weapon_source_skills': [
        {'skill_id': 'wpn_sp_attr_atk_high', 'bb': {'atk': 0.39}},
        {'skill_id': 'sk_wpn_sword_0012', 'bb': {'atk_up': 0.16}},
    ]}
    entries = parser._derive_static_self_multiplier_entries(loadout)
    assert not [entry for entry in entries if entry['zone'] == 'atk']


@pytest.mark.parametrize('skill_id', ['2200', 'sk_wpn_claym_0010'])
def test_flat_card_attack_stays_flat_and_is_normalized_by_explicit_base_attack(skill_id):
    loadout = {'weapon_template': 'wpn_claym_0010', 'weapon_source_skills': [
        {'skill_id': skill_id, 'bb': {'atk_up': 25.0}},
    ]}
    entries = parser._derive_static_self_multiplier_entries(loadout)
    flat = [entry for entry in loadout['equipment_baseline_attributes']
            if entry.get('attr_type') == 2 and entry.get('modifier_type') == 7]
    assert len(flat) == 1 and flat[0]['value'] == 25.0
    assert flat[0]['source_id'] == 'sk_wpn_claym_0010'
    assert not [entry for entry in entries if entry['zone'] == 'atk']
    sample = hit(200, static_flat_attack=sum(entry['value'] for entry in flat), base_attack_for_percent=100.0)
    windows = [window(0.5, source=ATTACKER), window(0.25)]
    for order in permutations(windows):
        result = parser._allocate_rdps_for_hit(deepcopy(sample), deepcopy(list(order)), include_debug=True)
        assert result['contributions_map'] == pytest.approx({ATTACKER: 175, PROVIDER: 25})
        assert sum(result['contributions_map'].values()) == pytest.approx(200)


def test_missing_flat_attack_scale_only_withholds_attack_zone():
    sample = hit(240, static_flat_attack=25.0)
    result = parser._allocate_rdps_for_hit(sample, [window(0.25), window(0.2, source=OTHER_PROVIDER, zone='dmg_inc')], include_debug=True)
    assert result['contributions_map'] == pytest.approx({ATTACKER: 200, OTHER_PROVIDER: 40})
    assert PROVIDER not in result['contributions_map']
    assert any(row['reason'] == 'missing_base_attack_for_flat_addition' for row in result['ignored_effects'])
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize('custom', [
    {'$type': 'Beyond.Gameplay.Core.MultiplyAttributeCalculation', 'attributeType': 'MaxHp', 'valueSource': 'AttackerOrHealer'},
    {'$type': 'Beyond.Gameplay.Core.MultiplyAttributeCalculation', 'attributeType': 'Atk', 'valueSource': 'Target'},
    {'$type': 'Beyond.Gameplay.Core.DefiniteValueCalculation', 'value': {'useBlackboardKey': False, 'value': 1250.0}},
])
def test_active_non_attack_calculation_has_no_attack_credit(custom):
    sample = hit(120, damage_calculation={'source': 'current_typed_damage_unit', 'simpleCalculation': False,
                                        'atkCalculation': custom})
    result = parser._allocate_rdps_for_hit(sample, [window(0.2)], include_debug=True)
    assert sample['attack_damage_dependency'] == 'independent'
    assert result['contributions_map'] == {ATTACKER: 120.0}
    assert any(row['reason'] == 'damage_not_scaled_by_attack' for row in result['ignored_effects'])


@pytest.mark.parametrize('custom', [
    {'$type': 'Beyond.Gameplay.Core.DefiniteValueCalculation', 'value': {'useBlackboardKey': False, 'value': 1250.0}},
    {'$type': 'Beyond.Gameplay.Core.MultiplyAttributeCalculation', 'attributeType': 'MaxHp', 'valueSource': 'Target',
     'multiplier': {'useBlackboardKey': False, 'value': 2.0}, 'addition': {'useBlackboardKey': False, 'value': 1250.0}},
])
def test_simple_calculation_ignores_dormant_fixed_1250_object(custom):
    sample = hit(120, damage_calculation={'source': 'current_typed_damage_unit', 'simpleCalculation': True,
                                        'atkCalculation': custom})
    result = parser._allocate_rdps_for_hit(sample, [window(0.2)], include_debug=True)
    assert sample['attack_damage_dependency'] == 'linear'
    assert result['contributions_map'] == pytest.approx({ATTACKER: 100, PROVIDER: 20})


def test_active_affine_attack_damage_keeps_fixed_damage_out_of_external_percentage():
    calculation = {'source': 'current_typed_damage_unit', 'simpleCalculation': False,
                   'atkCalculation': {'$type': 'Beyond.Gameplay.Core.MultiplyAttributeCalculation',
                       'attributeType': 'Atk', 'valueSource': 'AttackerOrHealer',
                       'multiplier': {'useBlackboardKey': False, 'value': 2.0},
                       'addition': {'useBlackboardKey': False, 'value': 100.0}}}
    sample = hit(340, damage_calculation=calculation, base_attack_for_percent=100.0, attack_ability_factor=1.0)
    result = parser._allocate_rdps_for_hit(sample, [window(0.2)], include_debug=True)
    assert sample['attack_damage_dependency'] == 'affine'
    assert result['contributions_map'] == pytest.approx({ATTACKER: 300, PROVIDER: 40})
    assert sum(result['contributions_map'].values()) == pytest.approx(340)


@pytest.mark.parametrize('source', [None, 'name_based_damage_fixture'])
def test_unverified_damage_calculation_does_not_authorize_attack_percentage(source):
    sample = hit(120, damage_calculation={'source': source, 'simpleCalculation': True})
    result = parser._allocate_rdps_for_hit(sample, [window(0.2)], include_debug=True)
    assert sample['attack_damage_dependency'] == 'unknown'
    assert result['contributions_map'] == {ATTACKER: 120.0}
    assert any(row['reason'] == 'attack_damage_calculation_unresolved' for row in result['ignored_effects'])


def test_final_attribute_51_already_contains_matching_static_damage_bonus():
    static = {'zone': 'dmg_inc', 'element': 'fire', 'attr_type': 51, 'rate': 0.4,
              'source_type': 'equip', 'source_name': 'explicit equipment fixture',
              'evidence_kind': 'official_EquipTable_attribute'}
    sample = hit(180, baseline={51: 0.8}, static_multiplier_entries=[static])
    result = parser._allocate_rdps_for_hit(sample, [window(0.2, zone='dmg_inc', attr_type=51)], include_debug=True)
    assert result['contributions_map'] == pytest.approx({ATTACKER: 160, PROVIDER: 20})
    zone = next(row for row in result['zones'] if row['zone'] == 'dmg_inc')
    assert zone['self_rate'] == pytest.approx(0.6)
    assert zone['external_rate'] == pytest.approx(0.2)
