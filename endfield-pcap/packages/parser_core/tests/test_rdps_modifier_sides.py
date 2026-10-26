from copy import deepcopy

import pytest

from parser_core import battle_log_parser as parser


@pytest.mark.parametrize('attacker,frozen,applies', [
    ('chr_0003_endminf', True, True), ('chr_0003_endminf', False, False),
    ('chr_0004_pelica', True, False),
])
def test_admin_modifier_is_carried_by_attacker_but_writes_defender_zone(attacker, frozen, applies):
    record = {'event_key': 'buff_chr_0003_endminf_talent_0', 'source_character_key': 'chr_0003_endminf',
              'target_character_key': 'chr_0003_endminf', 'bb_values': {'dmg': 0.2}}
    effects = parser._collect_zone_effects(record)
    assert len(effects) == 1
    assert effects[0]['zone'] == 'vuln_taken'
    assert effects[0]['recipient_side'] == 'attacker'
    window = {**record, 'uid': '1', 'start_ts_ms': 0, 'end_ts_ms': 1000, 'zone_effects': effects}
    hit = {'character_key': attacker, 'source_actor_id': '1001', 'target_enemy_key': 'eny_0051_rodin',
           'target_actor_id': '2001', 'hit_value': 120, 'ts_ms': 100, 'damage_element': 'physical',
           'damage_school': 'physical', 'target_buff_state_complete': True}
    states = [{'uid': '2', 'event_key': 'buff_common_originum_frozen', 'target_enemy_key': 'eny_0051_rodin',
               'target_actor_id': '2001', 'ts_ms': 0, 'end_ts_ms': 1000}] if frozen else []
    result = parser._allocate_rdps_for_hit(hit, [window], include_debug=True, condition_buff_records=states)
    assert result['contributions_map'] == {attacker: 120}
    assert bool(result['zones']) is applies
    if applies:
        assert result['zones'][0]['self_rate'] == 0.2


@pytest.mark.parametrize('enabled_side,attacker_hp,expected', [
    ('Attacker', 20, None), ('Defender', 20, 'condition_unsatisfied'),
    ('Defender', 80, None), ('Defender', None, 'condition_unobservable'),
])
def test_hp_condition_uses_opposite_participant_of_modifier_entry(monkeypatch, enabled_side, attacker_hp, expected):
    root = {'damageModifier': [{'enableSide': enabled_side,
        'condition': {'onlyExecuteWhenSourceIsGuard': False, 'onlyExecuteWhenSourceIsMainChar': False,
                      'actionData': [{'$type': 'Beyond.Gameplay.Core.Conditions.CheckHp.Data', 'isEnable': True,
                          'hpOwner': {'targetSource': 'Target'}, 'compare': 'GE', 'isRatio': True,
                          'value': {'useBlackboardKey': False, 'value': 0.5}}]},
        'damageProcessors': [{'$type': 'Beyond.Gameplay.Core.DamageScaleProcessor', 'side': 'Defender',
                              'zoneName': 'NormalCalcZone', 'addition': {'useBlackboardKey': True, 'blackboardKey': 'rate'}}]}],
        'attributeModifier': {'isConvertedAttribute': False, 'attributeModifiers': []}}
    monkeypatch.setattr(parser, '_load_current_buff_roots', lambda: {'buff_scope_fixture': root})
    parser._decoded_damage_modifier_condition_rows.cache_clear()
    try:
        effects = parser._collect_semantic_condition_zone_effects({'event_key': 'buff_scope_fixture', 'bb_values': {'rate': 0.2}})
        assert len(effects) == 1
        condition = effects[0]['condition']
        assert condition['subject_side'] == ('defender' if enabled_side == 'Attacker' else 'attacker')
        hit = {'enemy_hp_before': 80, 'target_enemy_max_hp': 100, 'attacker_max_hp': 100}
        if attacker_hp is not None:
            hit['attacker_hp_before'] = attacker_hp
        failure = parser._effect_condition_failure(effects[0], hit)
        assert (failure['reason_group'] if failure else None) == expected
    finally:
        parser._decoded_damage_modifier_condition_rows.cache_clear()


def test_attacker_buff_condition_cannot_use_enemy_state():
    effect = {'condition': {'type': 'target_has_buff', 'subject_side': 'attacker', 'buff_ids': ['buff_required']}}
    hit = {'_active_target_buff_ids': ['buff_required'], 'target_buff_state_complete': True}
    assert parser._effect_condition_failure(effect, hit)['reason_group'] == 'condition_unobservable'
    hit.update(_active_attacker_buff_ids=[], attacker_buff_state_complete=True)
    assert parser._effect_condition_failure(effect, hit)['reason_group'] == 'condition_unsatisfied'
    hit['_active_attacker_buff_ids'] = ['buff_required']
    assert parser._effect_condition_failure(effect, hit) is None


def test_converted_arc_bonus_uses_runtime_value_without_reconstructing_conversion():
    record = {'event_key': 'buff_chr_0007_ikut_atk_buff_talent', 'source_character_key': 'chr_0007_ikut',
              'target_character_key': 'chr_0004_pelica', 'bb_values': {'pulse_up': 0.2}}
    effects = parser._collect_zone_effects(record)
    assert len(effects) == 1 and effects[0]['converted_attribute'] is True
    window = {**record, 'uid': '1', 'start_ts_ms': 0, 'end_ts_ms': 1000, 'zone_effects': effects}
    hit = {'character_key': 'chr_0004_pelica', 'target_enemy_key': 'eny_0051_rodin', 'hit_value': 120,
           'ts_ms': 100, 'damage_element': 'pulse', 'damage_school': 'spell'}
    assert parser._allocate_rdps_for_hit(hit, [window])['contributions_map'] == pytest.approx({
        'chr_0004_pelica': 100, 'chr_0007_ikut': 20})
    record['bb_values'] = {}
    assert parser._collect_zone_effects(record) == []


@pytest.mark.parametrize('converted', [None, 'true', 1])
def test_malformed_converted_flag_is_not_assumed_valid(monkeypatch, converted):
    key = 'buff_chr_0007_ikut_atk_buff_talent'
    root = deepcopy(parser._load_current_buff_roots()[key])
    root['attributeModifier']['isConvertedAttribute'] = converted
    monkeypatch.setattr(parser, '_load_current_buff_roots', lambda: {key: root})
    record = {'event_key': key, 'bb_values': {'pulse_up': 0.2}}
    assert parser._collect_semantic_condition_zone_effects(record) == []
    assert record['rdps_unresolved_semantic_condition'] == ['unsupported_conditional_attribute_formula']
