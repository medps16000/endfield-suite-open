from itertools import permutations
from copy import deepcopy
import json
from math import log

import pytest

from parser_core import battle_log_parser as parser
from parser_core.hit_context import build_hit_context, rdps_contribution_map
from parser_core.unified import rdps_totals_from_hit_debug


ATTACKER = 'chr_0004_pelica'


@pytest.mark.parametrize('explicit_self', [False, True])
def test_unapproved_effect_stays_in_observed_baseline_without_receiving_credit(explicit_self):
    hit = {'character_key': ATTACKER, 'source_actor_id': '1001', 'target_enemy_key': 'eny_0051_rodin',
           'target_actor_id': '2001', 'hit_value': 180, 'ts_ms': 500, 'damage_element': 'fire',
           'damage_school': 'spell', 'baseline': {51: 0.8}}
    inputs = [('chr_0023_antal', 0.2, True), ('chr_0025_ardelia', 0.2, False)]
    if explicit_self:
        inputs.append((ATTACKER, 0.4, False))
    windows = [{
        'uid': str(i), 'event_key': f'baseline_fixture_{i}', 'source_character_key': source,
        'target_character_key': ATTACKER, 'target_actor_id': '1001', 'start_ts_ms': 0, 'end_ts_ms': 1000,
        'zone_effects': [{'zone': 'dmg_inc', 'element': 'fire', 'rate': rate, 'attr_type': 51,
                         'external_rdps_allowed': approved, 'rdps_whitelist_keys': ['approved_fixture'] if approved else []}],
    } for i, (source, rate, approved) in enumerate(inputs)]
    for ordered in permutations(windows):
        result = parser._allocate_rdps_for_hit(dict(hit), list(ordered), include_debug=True)
        assert result['contributions_map'] == pytest.approx({ATTACKER: 160, 'chr_0023_antal': 20})
        assert result['zones'][0]['self_rate'] == pytest.approx(0.6)
        assert any(row['reason'] == 'external_effect_not_whitelisted' for row in result['ignored_effects'])


@pytest.mark.parametrize('rates,zones', [
    ([float('nan')], ['atk']), ([float('inf')], ['atk']),
    ([1e308, 1e308], ['atk', 'atk']),
    ([1e200, 1e200], ['atk', 'dmg_inc']),
    ([1e200, 1e200], ['prod_dmg_inc', 'prod_dmg_inc']),
])
def test_invalid_or_overflowing_coefficients_cannot_erase_observed_damage(rates, zones):
    hit = {'character_key': ATTACKER, 'target_enemy_key': 'eny_0051_rodin', 'hit_value': 120,
           'ts_ms': 500, 'damage_element': 'fire', 'damage_school': 'spell'}
    windows = [{
        'uid': str(i), 'event_key': f'numeric_fixture_{i}', 'source_character_key': 'chr_0023_antal',
        'target_character_key': ATTACKER, 'start_ts_ms': 0, 'end_ts_ms': 1000,
        'zone_effects': [{'zone': zone, 'element': 'all', 'rate': rate, 'external_rdps_allowed': True}],
    } for i, (rate, zone) in enumerate(zip(rates, zones))]
    result = parser._allocate_rdps_for_hit(hit, windows, include_debug=True)
    assert result['contributions_map'] == {ATTACKER: 120}
    assert hit['rdps_invalid_numeric_allocation'] is True
    assert result['zones'] == []
    assert any(row['reason'] == 'invalid_numeric_allocation' for row in result['ignored_effects'])
    json.dumps(result, allow_nan=False)


def test_exact_multipliers_conserve_and_ignore_input_order():
    hit = {'character_key': ATTACKER, 'target_enemy_key': 'eny_0051_rodin',
           'hit_value': 156, 'ts_ms': 500, 'skill_key': 'explicit_skill',
           'damage_element': 'pulse', 'damage_school': 'spell'}
    windows = [
        {'uid': 'a', 'event_key': 'explicit_a', 'source_character_key': 'chr_0023_antal',
         'target_character_key': ATTACKER, 'start_ts_ms': 0, 'end_ts_ms': 1000,
         'zone_effects': [{'zone': 'atk', 'element': 'all', 'rate': 0.2}]},
        {'uid': 'b', 'event_key': 'explicit_b', 'source_character_key': 'chr_0025_ardelia',
         'target_character_key': ATTACKER, 'start_ts_ms': 0, 'end_ts_ms': 1000,
         'zone_effects': [{'zone': 'dmg_inc', 'element': 'all', 'rate': 0.3}]},
    ]
    for ordered in permutations(windows):
        values = parser._allocate_rdps_for_hit(dict(hit), list(ordered))['contributions_map']
        assert values[ATTACKER] == pytest.approx(100)
        assert values['chr_0023_antal'] == pytest.approx(56 * log(1.2) / log(1.56))
        assert values['chr_0025_ardelia'] == pytest.approx(56 * log(1.3) / log(1.56))
        assert sum(values.values()) == pytest.approx(156)


def test_self_multiplier_is_not_external_credit():
    hit = {'character_key': ATTACKER, 'target_enemy_key': 'eny_0051_rodin',
           'hit_value': 175, 'ts_ms': 500, 'skill_key': 'explicit_skill',
           'damage_element': 'pulse', 'damage_school': 'spell'}
    windows = [
        {'uid': str(i), 'event_key': 'explicit_' + str(i), 'source_character_key': source,
         'target_character_key': ATTACKER, 'start_ts_ms': 0, 'end_ts_ms': 1000,
         'zone_effects': [{'zone': 'atk', 'element': 'all', 'rate': rate}]}
        for i, (source, rate) in enumerate([(ATTACKER, 0.5), ('chr_0023_antal', 0.25)])
    ]
    values = parser._allocate_rdps_for_hit(hit, windows)['contributions_map']
    assert values == pytest.approx({ATTACKER: 150, 'chr_0023_antal': 25})


def test_unexplained_dpd_residual_is_a_blocker_not_self_evidence():
    hit = {'character_key': ATTACKER, 'target_enemy_key': 'eny_0051_rodin',
           'hit_value': 100, 'packet_hit_value': 100, 'ts_ms': 500, 'skill_key': 'explicit_skill',
           'damage_element': 'pulse', 'damage_school': 'spell', 'damage_element_source': 'official_action_unit',
           'dpd_raw': {'atk_zones': [1, 1.5, 1, 1, 1, 1]}}
    allocation = parser._allocate_rdps_for_hit(hit, [], include_debug=True)
    assert hit['rdps_unresolved_dpd_residual'] is True
    assert not any(item.get('source_type') == 'dpd_self_residual'
                   for zone in allocation.get('zones', []) for item in zone.get('contributors', []))
    gate = parser._build_rdps_preflight([], [], hits=[hit], battle_start_ms=0, battle_end_ms=1000)
    assert gate['ok'] is False
    assert any(row['reason'] == 'DPD residual has no authoritative attribution' for row in gate['blockers'])


def test_unexplained_dpd_bucket_cannot_calibrate_known_external_credit():
    hit = {'character_key': ATTACKER, 'target_enemy_key': 'eny_0051_rodin',
           'hit_value': 120, 'ts_ms': 500, 'skill_key': 'explicit_skill',
           'damage_element': 'pulse', 'damage_school': 'spell'}
    window = {'uid': 'a', 'event_key': 'explicit_a', 'source_character_key': 'chr_0023_antal',
              'target_character_key': ATTACKER, 'start_ts_ms': 0, 'end_ts_ms': 1000,
              'zone_effects': [{'zone': 'dmg_inc', 'element': 'all', 'rate': 0.2}]}
    known = parser._allocate_rdps_for_hit(dict(hit), [window])
    with_unexplained_bucket = dict(hit, dpd_raw={'atk_zones': [1, 2.0, 1, 1, 1, 1]})
    diagnosed = parser._allocate_rdps_for_hit(with_unexplained_bucket, [window])
    assert diagnosed['contributions_map'] == known['contributions_map']
    assert with_unexplained_bucket['rdps_unresolved_dpd_residual'] is True
    assert diagnosed['allocation_policy_basis'] == 'accounting_convention'


def test_tiny_credit_remains_in_totals_even_when_display_rounds_to_zero():
    hit = {'character_key': ATTACKER, 'target_enemy_key': 'eny_0051_rodin',
           'hit_value': 1, 'ts_ms': 500, 'skill_key': 'explicit_skill',
           'damage_element': 'pulse', 'damage_school': 'spell'}
    window = {'uid': 'a', 'event_key': 'explicit_a', 'source_character_key': 'chr_0023_antal',
              'target_character_key': ATTACKER, 'start_ts_ms': 0, 'end_ts_ms': 1000,
              'zone_effects': [{'zone': 'dmg_inc', 'element': 'all', 'rate': 0.00001}]}
    context = build_hit_context(hit, [window])
    values = rdps_contribution_map(context)
    assert 0 < values['chr_0023_antal'] < 0.0001
    assert sum(values.values()) == pytest.approx(1, abs=1e-14)
    assert values['chr_0023_antal'] == pytest.approx(1 - 1 / 1.00001, abs=1e-14)
    totals = rdps_totals_from_hit_debug({'hits': [context] * 2000})
    assert totals['chr_0023_antal'] == pytest.approx(values['chr_0023_antal'] * 2000, abs=1e-12)


@pytest.mark.parametrize('zone', ['prod_dmg_inc', 'prod_vuln_taken'])
@pytest.mark.parametrize('self_rates,external,damage,expected,total_multiplier', [
    ([0.1], [('chr_0023_antal', 0.2)], 132,
     {ATTACKER: 110, 'chr_0023_antal': 22}, 1.32),
    ([0.1, 0.2], [('chr_0023_antal', 0.2), ('chr_0025_ardelia', 0.3)], 205.92,
     {ATTACKER: 132, 'chr_0023_antal': 29.568, 'chr_0025_ardelia': 44.352}, 2.0592),
    ([0.1, 0.2], [], 132, {ATTACKER: 132}, 1.32),
    ([], [('chr_0023_antal', 0.2), ('chr_0023_antal', 0.3)], 156,
     {ATTACKER: 100, 'chr_0023_antal': 56}, 1.56),
])
def test_prod_zone_uses_product_of_external_factors(zone, self_rates, external, damage, expected, total_multiplier):
    hit = {'character_key': ATTACKER, 'target_enemy_key': 'eny_0051_rodin', 'target_actor_id': 'e1',
           'hit_value': damage, 'ts_ms': 500, 'skill_key': 'explicit',
           'damage_element': 'pulse', 'damage_school': 'spell'}
    windows = [
        {'uid': str(i), 'event_key': 'explicit_' + str(i), 'source_character_key': source,
         'target_character_key': ATTACKER if zone == 'prod_dmg_inc' else 'eny_0051_rodin',
         'target_actor_id': 'e1', 'start_ts_ms': 0, 'end_ts_ms': 1000,
         'zone_effects': [{'zone': zone, 'element': 'all', 'rate': rate}]}
        for i, (source, rate) in enumerate([(ATTACKER, rate) for rate in self_rates] + external)
    ]
    for ordered in permutations(windows):
        result = parser._allocate_rdps_for_hit(dict(hit), list(ordered), include_debug=True)
        assert result['contributions_map'] == pytest.approx(expected)
        assert sum(result['contributions_map'].values()) == pytest.approx(damage)
        assert len(result['zones']) == 1
        assert result['zones'][0]['total_multiplier'] == pytest.approx(total_multiplier)
        assert result['zones'][0]['total_rate'] == pytest.approx(total_multiplier - 1)


def test_resistance_effect_requires_counterfactual_evidence():
    hit = {'character_key': ATTACKER, 'target_enemy_key': 'eny_0051_rodin', 'target_actor_id': 'e1',
           'hit_value': 120, 'ts_ms': 500, 'skill_key': 'explicit',
           'damage_element': 'pulse', 'damage_school': 'spell'}
    window = {'uid': 'x', 'event_key': 'explicit', 'source_character_key': 'chr_0023_antal',
              'target_character_key': 'eny_0051_rodin', 'target_actor_id': 'e1',
              'start_ts_ms': 0, 'end_ts_ms': 1000,
              'zone_effects': [{'zone': 'res', 'element': 'pulse', 'rate': 0.2}]}
    result = parser._allocate_rdps_for_hit(hit, [window], include_debug=True)
    assert result['contributions_map'] == {ATTACKER: 120}
    assert hit['rdps_unresolved_effect_conditions'] is True
    assert result['ignored_effects'][0]['reason'] == 'resistance_counterfactual_unresolved'


def test_observed_final_resistance_does_not_prove_unbuffed_denominator():
    hit = {'character_key': ATTACKER, 'target_enemy_key': 'eny_0051_rodin', 'target_actor_id': 'e1',
           'hit_value': 120, 'ts_ms': 500, 'skill_key': 'explicit',
           'damage_element': 'pulse', 'damage_school': 'spell',
           'target_resistance_points': {'pulse': 20.0}}
    window = {'uid': 'x', 'event_key': 'explicit', 'source_character_key': 'chr_0023_antal',
              'target_character_key': 'eny_0051_rodin', 'target_actor_id': 'e1',
              'start_ts_ms': 0, 'end_ts_ms': 1000,
              'zone_effects': [{'zone': 'res', 'element': 'pulse', 'rate': 10.0}]}
    # If current R=20 already includes a 10-point reduction, removing that
    # reduction gives R=30: 120 * (1 - 0.7 / 0.8) = 15, not 13.3333.
    # An ENTITY_STATS observation alone doesn't identify that counterfactual.
    assert 120 * (1 - 0.7 / 0.8) == pytest.approx(15)
    result = parser._allocate_rdps_for_hit(hit, [window], include_debug=True)
    assert result['contributions_map'] == {ATTACKER: 120}
    assert hit.get('rdps_unresolved_effect_conditions') is True
    assert result['ignored_effects'][0]['reason'] == 'resistance_counterfactual_unresolved'


def test_resistance_named_bb_and_snapshot_do_not_prove_a_counterfactual():
    hit = {'character_key': ATTACKER, 'target_enemy_key': 'eny_0051_rodin', 'target_actor_id': 'e1',
           'hit_value': 120, 'ts_ms': 500, 'skill_key': 'explicit',
           'damage_element': 'pulse', 'damage_school': 'spell',
           'target_resistance_scalars': {'pulse': 0.8}}
    window = {'uid': 'x', 'event_key': 'explicit', 'source_character_key': 'chr_0023_antal',
              'target_character_key': 'eny_0051_rodin', 'target_actor_id': 'e1',
              'start_ts_ms': 0, 'end_ts_ms': 1000,
              'zone_effects': [{'zone': 'res', 'element': 'pulse', 'rate': 0.2,
                                'bb_key': 'spell_resistance_decrease'}]}
    result = parser._allocate_rdps_for_hit(hit, [window], include_debug=True)
    assert result['contributions_map'] == {ATTACKER: 120}
    assert hit['rdps_unresolved_effect_conditions'] is True
    assert result['ignored_effects'][0]['reason'] == 'resistance_counterfactual_unresolved'


@pytest.mark.parametrize('element,target', [('fire', 'e1'), ('pulse', 'other_enemy')])
def test_inapplicable_resistance_effect_does_not_create_counterfactual_blocker(element, target):
    hit = {'character_key': ATTACKER, 'target_enemy_key': 'eny_0051_rodin',
           'target_actor_id': 'e1', 'hit_value': 120, 'ts_ms': 500,
           'skill_key': 'explicit', 'damage_element': 'pulse', 'damage_school': 'spell'}
    window = {'uid': 'x', 'event_key': 'explicit', 'source_character_key': 'chr_0023_antal',
              'target_character_key': 'eny_0051_rodin', 'target_actor_id': target,
              'start_ts_ms': 0, 'end_ts_ms': 1000,
              'zone_effects': [{'zone': 'res', 'element': element, 'rate': 10.0}]}
    result = parser._allocate_rdps_for_hit(hit, [window])
    assert result['contributions_map'] == {ATTACKER: 120}
    assert not hit.get('rdps_unresolved_effect_conditions')


@pytest.mark.parametrize('scalar', [None, 0.8, 1.2])
def test_current_typed_conduct_is_taken_damage_even_with_resistance_named_bb(scalar):
    effect_record = {'event_key': 'buff_common_pulse_pulse_conduct_triggered_do',
                     'target_enemy_key': 'eny_0051_rodin',
                     'bb_values': {'final_spell_resistance_decrease': 0.2}}
    effects = parser._collect_zone_effects(effect_record)
    assert effects and all(e['zone'] == 'vuln_taken' for e in effects)
    assert all(e['semantic_source'] == 'typed_current_damageModifier' for e in effects)
    hit = {'character_key': ATTACKER, 'target_enemy_key': 'eny_0051_rodin',
           'target_actor_id': '2001', 'hit_value': 120, 'ts_ms': 500,
           'skill_key': 'explicit', 'damage_element': 'pulse', 'damage_school': 'spell',
           'target_resistance_scalars': {'pulse': scalar}}
    window = {'uid': 'conduct1', 'event_key': effect_record['event_key'],
              'source_character_key': 'chr_0023_antal',
              'target_character_key': 'eny_0051_rodin', 'target_actor_id': '2001',
              'start_ts_ms': 0, 'end_ts_ms': 1000, 'zone_effects': effects}
    result = parser._allocate_rdps_for_hit(hit, [window])
    assert result['contributions_map'] == pytest.approx({ATTACKER: 100, 'chr_0023_antal': 20})
    assert not hit.get('rdps_unresolved_effect_conditions')


@pytest.mark.parametrize('target_id,mask', [('enemy-1', 33554432), ('enemy-2', 33554432), ('enemy-1', None)])
def test_hit_context_and_allocation_share_instance_and_condition_gates(target_id, mask):
    hit = {'character_key': ATTACKER, 'target_enemy_key': 'eny_0051_rodin',
           'target_actor_id': target_id, 'hit_value': 120, 'ts_ms': 500, 'skill_key': 'explicit_skill',
           'damage_element': 'natural', 'damage_school': 'spell', 'damage_decorate_mask': mask}
    window = {'uid': 'a', 'event_key': 'explicit_a', 'source_character_key': 'chr_0023_antal',
              'target_character_key': 'eny_0051_rodin', 'target_actor_id': 'enemy-1',
              'start_ts_ms': 0, 'end_ts_ms': 1000,
              'zone_effects': [{'zone': 'vuln_taken', 'element': 'all', 'rate': 0.2,
                  'condition': {'type': 'damage_decorate_mask', 'mask': 33554432, 'check': 'HasAll'}}]}
    direct_hit, context_hit = dict(hit), dict(hit)
    direct = parser._allocate_rdps_for_hit(direct_hit, [window], include_debug=True)
    context = build_hit_context(context_hit, [window])
    assert context['contributions_map'] == direct['contributions_map']
    assert context['ignored_effects'] == direct['ignored_effects']
    assert context_hit.get('rdps_unresolved_effect_conditions') == direct_hit.get('rdps_unresolved_effect_conditions')
    if target_id != 'enemy-1' or mask is None:
        assert context['contributions_map'] == {ATTACKER: 120}
    else:
        assert context['contributions_map'] == pytest.approx({ATTACKER: 100, 'chr_0023_antal': 20})


def test_equal_effects_from_distinct_instances_or_times_are_not_merged():
    first = {'uid': 'a', 'source_character_key': 'chr_0023_antal',
             'target_character_key': ATTACKER, 'event_key': 'explicit_buff',
             'start_ts_ms': 0, 'end_ts_ms': 1000,
             'zone_effects': [{'zone': 'atk', 'element': 'all', 'rate': 0.2}]}
    second = dict(first, uid='b')
    adjacent = dict(first, start_ts_ms=1001, end_ts_ms=2000)
    rows = parser._merge_buff_windows([first, second, adjacent])
    assert len(rows) == 3
    assert len(parser._dedupe_mirrored_buff_windows(rows)) == 3
    assert len(parser._dedupe_mirrored_buff_windows([first, dict(first)])) == 1


@pytest.mark.parametrize('zone,recipient', [('vuln_taken', ATTACKER), ('atk', 'eny_0051_rodin')])
def test_incoming_player_modifier_and_enemy_attack_do_not_boost_player_damage(zone, recipient):
    hit = {'character_key': ATTACKER, 'target_enemy_key': 'eny_0051_rodin', 'target_actor_id': 'e1',
           'hit_value': 120, 'ts_ms': 500, 'skill_key': 'explicit', 'damage_element': 'pulse', 'damage_school': 'spell'}
    window = {'uid': 'x', 'event_key': 'explicit', 'source_character_key': 'chr_0023_antal',
              'target_character_key': recipient, 'target_actor_id': 'e1',
              'start_ts_ms': 0, 'end_ts_ms': 1000,
              'zone_effects': [{'zone': zone, 'element': 'all', 'rate': 0.2}]}
    result = parser._allocate_rdps_for_hit(hit, [window], include_debug=True)
    assert result['contributions_map'] == {ATTACKER: 120}
    assert result['ignored_effects'][0]['reason'] == 'effect_recipient_side_mismatch'


@pytest.mark.parametrize('element,attr_type', [
    ('physical', 94), ('natural', 95), ('cryst', 96),
    ('pulse', 97), ('fire', 98), ('ether', 99),
])
def test_resistance_uses_this_hit_effective_points_for_each_element(element, attr_type):
    hit = {'character_key': ATTACKER, 'target_enemy_key': 'eny_0051_rodin', 'target_actor_id': 'e1',
           'hit_value': 120, 'ts_ms': 500, 'skill_key': 'explicit', 'damage_element': element,
           'damage_school': 'physical' if element == 'physical' else 'spell',
           'packet_attr_details': {'attacker': [], 'defender': [f'{attr_type}:20']}}
    window = {'uid': 'r1', 'event_key': 'resistance_points_fixture', 'source_character_key': 'chr_0023_antal',
              'target_character_key': 'eny_0051_rodin', 'target_actor_id': 'e1',
              'start_ts_ms': 0, 'end_ts_ms': 1000,
              'zone_effects': [{'zone': 'res', 'element': element, 'rate': 10.0,
                                'external_rdps_allowed': True, 'rdps_whitelist_keys': ['approved_resistance_fixture']}]}
    result = parser._allocate_rdps_for_hit(hit, [window], include_debug=True)
    # R_eff=20 already includes the applied ten-point reduction. Removing it
    # gives 30, so m_res=80/70 and the attacker's retained damage is 105.
    assert result['contributions_map'] == pytest.approx({ATTACKER: 105, 'chr_0023_antal': 15})
    assert sum(result['contributions_map'].values()) == pytest.approx(120)
    assert result['allocation_policy'] == 'log_marginal_v1'


@pytest.mark.parametrize('tokens,reduction,damage,retained,credit', [
    (['98:-23.6'], 23.6, 123.6, 100.0, 23.6),
    (['98:0'], 10.0, 120.0, 108.0, 12.0),
    (['98:20', '98:20'], 10.0, 120.0, 105.0, 15.0),
])
def test_resistance_retains_negative_points_and_does_not_reapply_attacker_ignore(tokens, reduction, damage, retained, credit):
    hit = {'character_key': ATTACKER, 'target_enemy_key': 'eny_0051_rodin', 'target_actor_id': 'e1',
           'hit_value': damage, 'ts_ms': 500, 'skill_key': 'explicit', 'damage_element': 'fire',
           'damage_school': 'spell', 'target_resistance_points': {'fire': 20.0},
           'target_resistance_scalars': {'fire': 0.8},
           'packet_attr_details': {'attacker': [], 'defender': tokens}}
    window = {'uid': 'r1', 'event_key': 'resistance_points_fixture', 'source_character_key': 'chr_0023_antal',
              'target_character_key': 'eny_0051_rodin', 'target_actor_id': 'e1',
              'start_ts_ms': 0, 'end_ts_ms': 1000,
              'zone_effects': [{'zone': 'res', 'element': 'fire', 'rate': reduction,
                                'external_rdps_allowed': True, 'rdps_whitelist_keys': ['approved_resistance_fixture']}]}
    result = parser._allocate_rdps_for_hit(hit, [window], include_debug=True)
    # The per-hit zero already contains any attacker's ignore. Do not reuse
    # scene R=20, multiply negative points by 100, or add ignore a second time.
    assert result['contributions_map'] == pytest.approx({ATTACKER: retained, 'chr_0023_antal': credit})
    assert sum(result['contributions_map'].values()) == pytest.approx(damage)


@pytest.mark.parametrize('same_provider', [False, True])
def test_resistance_multiple_external_sources_keep_own_reduction_in_retained_damage(same_provider):
    second = 'chr_0023_antal' if same_provider else 'chr_0025_ardelia'
    hit = {'character_key': ATTACKER, 'target_enemy_key': 'eny_0051_rodin', 'target_actor_id': 'e1',
           'hit_value': 160.0, 'ts_ms': 500, 'skill_key': 'explicit', 'damage_element': 'fire',
           'damage_school': 'spell', 'packet_attr_details': {'attacker': [], 'defender': ['98:20']}}
    windows = [{'uid': str(i), 'event_key': f'resistance_source_fixture_{i}', 'source_character_key': source,
                'target_character_key': 'eny_0051_rodin', 'target_actor_id': 'e1',
                'start_ts_ms': 0, 'end_ts_ms': 1000,
                'zone_effects': [{'zone': 'res', 'element': 'fire', 'rate': points,
                                  'external_rdps_allowed': True, 'rdps_whitelist_keys': ['approved_resistance_fixture']}]}
               for i, (source, points) in enumerate([(ATTACKER, 5.0), ('chr_0023_antal', 5.0), (second, 10.0)])]
    expected = {ATTACKER: 130.0, 'chr_0023_antal': 30.0} if same_provider else {
        ATTACKER: 130.0, 'chr_0023_antal': 10.0, second: 20.0,
    }
    for order in permutations(windows):
        result = parser._allocate_rdps_for_hit(deepcopy(hit), deepcopy(list(order)), include_debug=True)
        # Only external d=15 is removed from N=80; the attacker's own d=5
        # stays present. Intra-zone credit follows the existing 5:10 weights.
        assert result['contributions_map'] == pytest.approx(expected)
        assert sum(result['contributions_map'].values()) == pytest.approx(160.0)
        json.dumps(result, allow_nan=False)


def test_resistance_and_attack_still_share_external_pool_by_log_marginal_policy():
    hit = {'character_key': ATTACKER, 'source_actor_id': 'a1', 'target_enemy_key': 'eny_0051_rodin',
           'target_actor_id': 'e1', 'hit_value': 150.0, 'ts_ms': 500, 'skill_key': 'explicit',
           'damage_element': 'fire', 'damage_school': 'spell',
           'packet_attr_details': {'attacker': [], 'defender': ['98:0']}}
    windows = [{'uid': 'r1', 'event_key': 'resistance_cross_zone_fixture', 'source_character_key': 'chr_0023_antal',
                'target_character_key': 'eny_0051_rodin', 'target_actor_id': 'e1',
                'start_ts_ms': 0, 'end_ts_ms': 1000,
                'zone_effects': [{'zone': 'res', 'element': 'fire', 'rate': 20.0,
                                  'external_rdps_allowed': True, 'rdps_whitelist_keys': ['approved_resistance_fixture']}]},
               {'uid': 'a1', 'event_key': 'attack_cross_zone_fixture', 'source_character_key': 'chr_0025_ardelia',
                'target_character_key': ATTACKER, 'target_actor_id': 'a1',
                'start_ts_ms': 0, 'end_ts_ms': 1000,
                'zone_effects': [{'zone': 'atk', 'element': 'all', 'rate': 0.2,
                                  'external_rdps_allowed': True, 'rdps_whitelist_keys': ['approved_attack_fixture']}]}]
    expected = {ATTACKER: 100.0, 'chr_0023_antal': 50.0 * log(1.25) / log(1.5),
                'chr_0025_ardelia': 50.0 * log(1.2) / log(1.5)}
    for order in permutations(windows):
        result = parser._allocate_rdps_for_hit(deepcopy(hit), deepcopy(list(order)), include_debug=True)
        assert result['contributions_map'] == pytest.approx(expected)
        assert sum(result['contributions_map'].values()) == pytest.approx(150.0)
        assert result['attacker_share'] == pytest.approx(100.0)
        assert result['external_pool'] == pytest.approx(50.0)
        # Independently assigning 150*20/100=30 and 150*0.2/1.2=25
        # would bypass the policy and overdraw the actual external pool.
        assert result['contributions_map']['chr_0023_antal'] != pytest.approx(30.0)


@pytest.mark.parametrize('defender_tokens', [
    [], ['97:20'], ['98:invalid'], ['98:nan'], ['98:inf'], ['98:-inf'],
    ['98:20', '98:21'], ['98:20', '98:20', '98:21'], ['98:20', '98:nan'],
])
def test_resistance_rejects_missing_or_conflicting_hit_values_without_scene_fallback(defender_tokens):
    hit = {'character_key': ATTACKER, 'target_enemy_key': 'eny_0051_rodin', 'target_actor_id': 'e1',
           'hit_value': 120.0, 'ts_ms': 500, 'skill_key': 'explicit', 'damage_element': 'fire',
           'damage_school': 'spell', 'target_resistance_points': {'fire': 20.0},
           'target_resistance_scalars': {'fire': 0.8},
           'packet_attr_details': {'attacker': ['98:20'], 'defender': defender_tokens}}
    window = {'uid': 'r1', 'event_key': 'resistance_missing_fixture', 'source_character_key': 'chr_0023_antal',
              'target_character_key': 'eny_0051_rodin', 'target_actor_id': 'e1',
              'start_ts_ms': 0, 'end_ts_ms': 1000,
              'zone_effects': [{'zone': 'res', 'element': 'fire', 'rate': 10.0,
                                'external_rdps_allowed': True, 'rdps_whitelist_keys': ['approved_resistance_fixture']}]}
    result = parser._allocate_rdps_for_hit(hit, [window], include_debug=True)
    assert result['contributions_map'] == {ATTACKER: 120.0}
    assert hit.get('rdps_unresolved_effect_conditions') is True
    assert result['ignored_effects']
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize('resistance,reduction', [(80, 20), (90, 20), (100, 10), (120, 10)])
def test_resistance_nonpositive_observed_or_removed_denominator_keeps_damage(resistance, reduction):
    hit = {'character_key': ATTACKER, 'target_enemy_key': 'eny_0051_rodin', 'target_actor_id': 'e1',
           'hit_value': 120.0, 'ts_ms': 500, 'skill_key': 'explicit', 'damage_element': 'fire',
           'damage_school': 'spell', 'packet_attr_details': {'attacker': [], 'defender': [f'98:{resistance}']}}
    window = {'uid': 'r1', 'event_key': 'resistance_boundary_fixture', 'source_character_key': 'chr_0023_antal',
              'target_character_key': 'eny_0051_rodin', 'target_actor_id': 'e1',
              'start_ts_ms': 0, 'end_ts_ms': 1000,
              'zone_effects': [{'zone': 'res', 'element': 'fire', 'rate': reduction,
                                'external_rdps_allowed': True, 'rdps_whitelist_keys': ['approved_resistance_fixture']}]}
    result = parser._allocate_rdps_for_hit(hit, [window], include_debug=True)
    assert result['contributions_map'] == {ATTACKER: 120.0}
    assert hit.get('rdps_unresolved_effect_conditions') is True
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize('effect_element,target', [('pulse', 'e1'), ('fire', 'e2')])
def test_inapplicable_resistance_does_not_consume_correct_hit_packet_value(effect_element, target):
    hit = {'character_key': ATTACKER, 'target_enemy_key': 'eny_0051_rodin', 'target_actor_id': 'e1',
           'hit_value': 120.0, 'ts_ms': 500, 'skill_key': 'explicit', 'damage_element': 'fire',
           'damage_school': 'spell', 'packet_attr_details': {'attacker': [], 'defender': ['98:20']}}
    window = {'uid': 'r1', 'event_key': 'resistance_inapplicable_fixture', 'source_character_key': 'chr_0023_antal',
              'target_character_key': 'eny_0051_rodin', 'target_actor_id': target,
              'start_ts_ms': 0, 'end_ts_ms': 1000,
              'zone_effects': [{'zone': 'res', 'element': effect_element, 'rate': 10.0,
                                'external_rdps_allowed': True, 'rdps_whitelist_keys': ['approved_resistance_fixture']}]}
    result = parser._allocate_rdps_for_hit(hit, [window], include_debug=True)
    assert result['contributions_map'] == {ATTACKER: 120.0}
    assert not hit.get('rdps_unresolved_effect_conditions')
