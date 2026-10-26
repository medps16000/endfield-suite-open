from math import log

import pytest

from parser_core import battle_log_parser as parser


@pytest.mark.parametrize('key,values,expected', [
    ('buff_chr_0029_pograni_talent1', {'atk_up': 0.1, 'physpell_up': 30},
     [('atk', 'all', 0.1), ('arts_strength', 'all', 30)]),
    ('buff_wpn_sword_0026_celebration', {'dmg_up': 0.2, 'phy_spell_up': 40},
     [('dmg_inc', e, 0.2) for e in ('physical', 'fire', 'pulse', 'cryst', 'natural')] + [('arts_strength', 'all', 40)]),
    ('buff_wpn_funnel_0006_valid', {'spell_dmg_up': 0.15},
     [('dmg_inc', e, 0.15) for e in ('fire', 'pulse', 'cryst', 'natural')]),
    ('buff_wpn_funnel_0011_valid', {'spell_dmg_up': 0.2},
     [('dmg_inc', e, 0.2) for e in ('fire', 'pulse', 'cryst', 'natural')]),
    ('buff_wpn_sword_0016_valid', {'pd_up': 0.25, 'phy_dmg_up3': 0.25}, [('dmg_inc', 'physical', 0.25)]),
    ('buff_wpn_sword_0016_team', {'pd_up_valid': 0.25}, [('dmg_inc', 'all', 0.25)]),
    ('buff_wpn_sword_0020_cryst', {'cryst_dmg_up': 0.15}, [('vuln_taken', 'cryst', 0.15)]),
    ('buff_common_affixes_vulnerable_fire', {'rate': 0.2}, [('fragile', 'fire', 0.2)]),
])
def test_current_whitelist_entries_resolve_actual_declared_fields(key, values, expected):
    entry = parser._load_rdps_semantics_registry()['verified_effects'][key]
    guard = entry.get('guard') or {}
    record = {'event_key': key, 'bb_values': values,
              'source_character_key': guard.get('source_character_key') or 'chr_0023_antal',
              'target_character_key': 'chr_0004_pelica',
              'packet_mapping': {name: value for name, value in guard.items() if name in ('weapon_id', 'suit_id')}}
    effects = parser._collect_zone_effects(record)
    assert sorted((e['zone'], e['element'], e['rate']) for e in effects) == sorted(expected)
    assert all(e['external_rdps_allowed'] and e['rdps_whitelist_keys'] == [key] for e in effects)
    assert not record.get('rdps_unresolved_semantic_condition')
    if key == 'buff_wpn_sword_0016_team':
        # The typed processor is all-damage; the approved physical-only
        # boundary remains an explicit condition on that processor.
        assert parser._effect_condition_failure(effects[0], {'damage_element': 'physical'}) is None
        assert parser._effect_condition_failure(effects[0], {'damage_element': 'fire'})['reason_group'] == 'condition_unsatisfied'


def test_declared_bb_aliases_do_not_hide_conflicting_values():
    record = {'event_key': 'buff_wpn_sword_0016_valid', 'bb_values': {'phy_dmg_up3': 0.2, 'pd_up': 0.3}}
    assert parser._collect_zone_effects(record) == []
    assert record['rdps_conflicting_runtime_rate'] is True


@pytest.mark.parametrize('element,key', [('fire', 'buff_common_affixes_vulnerable_spell'),
                                        ('physical', 'buff_common_affixes_vulnerable_physical')])
def test_fragility_and_normal_taken_damage_use_distinct_multipliers(element, key):
    normal_key = 'buff_common_pulse_pulse_conduct_triggered_do' if element == 'fire' else 'buff_physical_do_fracture'
    normal_bb = 'final_spell_resistance_decrease' if element == 'fire' else 'physical_res_down'
    records = [
        {'event_key': key, 'bb_values': {'rate': 0.2}, 'source_character_key': 'chr_0023_antal'},
        {'event_key': normal_key, 'bb_values': {normal_bb: 0.3}, 'source_character_key': 'chr_0025_ardelia'},
    ]
    windows = [{**row, 'target_character_key': 'eny_0051_rodin', 'target_actor_id': '2001',
                'start_ts_ms': 0, 'end_ts_ms': 1000, 'uid': str(i),
                'zone_effects': parser._collect_zone_effects(row)} for i, row in enumerate(records)]
    hit = {'character_key': 'chr_0004_pelica', 'target_enemy_key': 'eny_0051_rodin', 'target_actor_id': '2001',
           'hit_value': 156, 'ts_ms': 100, 'damage_element': element,
           'damage_school': 'spell' if element == 'fire' else 'physical'}
    allocation = parser._allocate_rdps_for_hit(hit, windows, include_debug=True)
    assert allocation['contributions_map'] == pytest.approx({
        'chr_0004_pelica': 100, 'chr_0023_antal': 56 * log(1.2) / log(1.56),
        'chr_0025_ardelia': 56 * log(1.3) / log(1.56)})
    assert {zone['zone'] for zone in allocation['zones']} == {'fragile', 'vuln_taken'}
