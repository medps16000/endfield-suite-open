import json

import pytest

from parser_core import battle_log_parser as parser


ATTACKER = 'chr_0004_pelica'
PROVIDER = 'chr_0035_liino'
KEY = 'buff_chr_0035_liino_atkup'


def buff(uid, rate, start=0, owner='1001', source=PROVIDER):
    record = {'event_key': KEY, 'source_character_key': source,
              'target_character_key': ATTACKER, 'bb_values': {'atk_up': rate}}
    return {**record, 'uid': str(uid), 'target_actor_id': owner, 'start_ts_ms': start,
            'end_ts_ms': 5000, 'zone_effects': parser._collect_zone_effects(record),
            'enable_observations': []}


def observation(enabled, time, seq):
    return {'enabled': enabled, 'ts_ms': time, 'operationSeq': seq, 'line_no': seq}


def damage(time=1000, **extra):
    return {'character_key': ATTACKER, 'source_actor_id': '1001', 'target_enemy_key': 'eny_0051_rodin',
            'target_actor_id': '3001', 'ts_ms': time, 'hit_value': 130, 'damage_element': 'physical',
            'damage_school': 'physical', **extra}


def test_old_enabled_priority_buff_survives_new_disabled_instance():
    old, new = buff(1, 0.3), buff(2, 0.1, start=500)
    old['enable_observations'] = [observation(True, 0, 1)]
    new['enable_observations'] = [observation(False, 500, 2)]
    windows = parser._apply_buff_stack_limits([old, new])
    assert [row['end_ts_ms'] for row in windows] == [5000, 5000]
    result = parser._allocate_rdps_for_hit(damage(), windows, include_debug=True)
    assert result['contributions_map'] == pytest.approx({ATTACKER: 100, PROVIDER: 30})
    assert result['zones'][0]['contributors'][0]['uid'] == '1'


def test_disabled_instance_can_be_reenabled_after_the_other_instance_ends():
    old, new = buff(1, 0.1), buff(2, 0.3, start=500)
    old['enable_observations'] = [observation(True, 0, 1), observation(False, 500, 2),
                                  observation(True, 1500, 4)]
    new['end_ts_ms'] = 1499
    new['enable_observations'] = [observation(True, 500, 2)]
    windows = parser._apply_buff_stack_limits([old, new])
    first = parser._allocate_rdps_for_hit(damage(), windows, include_debug=True)
    second = parser._allocate_rdps_for_hit(damage(2000, hit_value=110), windows, include_debug=True)
    assert first['contributions_map'] == pytest.approx({ATTACKER: 100, PROVIDER: 30})
    assert second['contributions_map'] == pytest.approx({ATTACKER: 100, PROVIDER: 10})


@pytest.mark.parametrize('enabled', [None, True])
def test_competing_instances_without_a_unique_enabled_set_do_not_pick_by_age(enabled):
    windows = [buff(1, 0.3), buff(2, 0.1, start=500)]
    if enabled is not None:
        for window in windows:
            window['enable_observations'] = [observation(enabled, window['start_ts_ms'], 2)]
    windows = parser._apply_buff_stack_limits(windows)
    hit = damage()
    result = parser._allocate_rdps_for_hit(hit, windows, include_debug=True)
    assert result['contributions_map'] == {ATTACKER: 130}
    assert hit['rdps_unresolved_effect_conditions'] is True
    assert any(row['reason'] == 'unresolved_buff_stack_selection' for row in result['ignored_effects'])


@pytest.mark.parametrize('packet_uid_present', [False, True])
def test_same_character_different_actor_does_not_receive_the_buff(packet_uid_present):
    window = buff(1, 0.3, owner='1002')
    hit = damage(packet_modifier_uids={'attacker': ['1']} if packet_uid_present else {})
    result = parser._allocate_rdps_for_hit(hit, [window], include_debug=True)
    assert result['contributions_map'] == {ATTACKER: 130}
    assert any(row['reason'] == 'buff_owner_instance_mismatch' for row in result['ignored_effects'])
    assert bool(hit.get('rdps_unresolved_target_binding')) is packet_uid_present


def test_same_template_on_another_owner_does_not_displace_this_owner():
    windows = parser._apply_buff_stack_limits([buff(1, 0.3), buff(2, 0.1, 500, owner='1002')])
    result = parser._allocate_rdps_for_hit(damage(), windows)
    assert result['contributions_map'] == pytest.approx({ATTACKER: 100, PROVIDER: 30})


def test_priority_group_does_not_split_by_provider_or_skill():
    windows = [buff(1, 0.3), buff(2, 0.1, 500, source='chr_0023_antal')]
    windows[1]['skill_family_key'] = 'different_skill'
    assert parser._buff_window_stack_group_key(windows[0]) == parser._buff_window_stack_group_key(windows[1])
    hit = damage()
    assert parser._allocate_rdps_for_hit(hit, parser._apply_buff_stack_limits(windows))['contributions_map'] == {ATTACKER: 130}


def test_different_templates_use_the_official_shared_stacking_key():
    team, own = buff(1, 0.3), buff(2, 0.1, source=ATTACKER)
    own['event_key'] = 'buff_chr_0035_liino_atkup_owner'
    assert parser._buff_window_stack_group_key(team) == parser._buff_window_stack_group_key(own)


@pytest.mark.parametrize('state_problem', ['conflict', 'missing_initial'])
def test_unknown_self_state_cannot_be_replaced_by_zero_baseline(state_problem):
    own = buff(1, 0.3, source=ATTACKER)
    if state_problem == 'conflict':
        own['enable_observations'] = [observation(True, 0, 1), observation(False, 0, 1)]
    else:
        own['requires_enable_observation'] = True
    external = buff(2, 0.2)
    external['event_key'] = 'buff_wpn_funnel_0005_atk_up'
    external['zone_effects'] = parser._collect_zone_effects(external)
    assert external['zone_effects']
    hit = damage(hit_value=156)
    result = parser._allocate_rdps_for_hit(hit, [own, external], include_debug=True)
    assert result['contributions_map'] == {ATTACKER: 156}
    assert hit['rdps_unresolved_effect_conditions'] is True
    assert any(row['reason'] == 'unresolved_buff_state_zone_baseline' for row in result['ignored_effects'])


def test_disabled_self_priority_candidate_is_not_assumed_absent_from_counterfactual():
    own, external = buff(1, 0.1, source=ATTACKER), buff(2, 0.3)
    own['enable_observations'] = [observation(False, 0, 1)]
    external['enable_observations'] = [observation(True, 0, 1)]
    result = parser._allocate_rdps_for_hit(damage(), [own, external], include_debug=True)
    assert result['contributions_map'] == {ATTACKER: 130}
    assert any(row['reason'] == 'unresolved_priority_self_baseline' for row in result['ignored_effects'])


def test_later_enable_observation_does_not_prove_the_initial_state():
    window = buff(1, 0.3)
    window['requires_enable_observation'] = True
    window['enable_observations'] = [observation(True, 1500, 3)]
    before = parser._allocate_rdps_for_hit(damage(1000), [window], include_debug=True)
    after = parser._allocate_rdps_for_hit(damage(2000), [window], include_debug=True)
    assert before['contributions_map'] == {ATTACKER: 130}
    assert after['contributions_map'] == pytest.approx({ATTACKER: 100, PROVIDER: 30})


@pytest.mark.parametrize('include_enable', [True, False])
def test_synthetic_trace_grammar_requires_observed_priority_enable(include_enable):
    lines = []
    for stamp, uid, rate, enabled, seq in [('10:00:00.000', '1', 0.3, True, 1),
                                         ('10:00:00.500', '2', 0.1, False, 2)]:
        lines += [f'[{stamp}] BUFF_START #{seq} id="{KEY}" uid={uid} owner={ATTACKER} src={PROVIDER} ownerId=1001 srcId=2001 dur=5',
                  f'[{stamp}] BB[{uid}]: atk_up={rate}',
                  f'[{stamp}] BUFF_ENABLE ' + json.dumps({'schemaVersion': 1, 'buffUid': uid,
                      'ownerId': '1001', 'enabled': enabled, 'operationSeq': seq})]
    lines.append(f'[10:00:01.000] HP_V2 #3 hit=130 cum=130 raw=130 packetFinalValue=130 pHP=100 eHP=1000 skill="chr_0004_pelica_combo_skill_projhit" actionId=11 damageUnitIndex=0 src={ATTACKER} tgt=eny_0051_rodin atk={ATTACKER} atkId=1001 tgtId=3001 hits=1')
    if not include_enable:
        lines = [line for line in lines if ' BUFF_ENABLE ' not in line]
    result = parser.parse_raw_battle_log_text('\n'.join(lines), include_rdps_debug=True)
    expected = {ATTACKER: 100, PROVIDER: 30} if include_enable else {ATTACKER: 130}
    assert {row['character_key']: row['value'] for row in result['debug_hits'][0]['rdps_contributions']} == pytest.approx(expected)
    if not include_enable:
        assert any(row['reason'] == 'unobserved_initial_buff_enable_state'
                   for row in result['debug_hits'][0]['ignored_effects'])
