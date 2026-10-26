from copy import deepcopy

from parser_core import battle_log_parser as parser


def test_missing_duration_does_not_extend_to_battle_end_or_one_second():
    for duration in (None, 0):
        assert parser._normalize_buff_duration_ms(100, end_ts_ms=None, raw_duration_ms=duration, battle_end_ms=5000) == 0
    assert parser._normalize_buff_duration_ms(100, end_ts_ms=1100, raw_duration_ms=None, battle_end_ms=5000) == 1000
    assert parser._normalize_buff_duration_ms(100, end_ts_ms=600, raw_duration_ms=1000, battle_end_ms=5000) == 500


def test_packet_modifier_last_seen_does_not_invent_a_lifetime():
    record = {'uid': 'u', 'ts_ms': 0, 'raw_duration_ms': None, 'end_ts_ms': None}
    parser._extend_buff_records_from_packet_modifiers([record], {'u': 1000})
    assert record['packet_modifier_last_seen_ts_ms'] == 1000
    assert record['raw_duration_ms'] is None
    assert record['end_ts_ms'] is None


def test_unknown_action_phase_blocks_even_without_fabricated_buff_window():
    action = {'uid': '77', 'ts_ms': 100, 'reason': 'unresolved_buff_action_phase'}
    report = parser._build_rdps_preflight([], [], unresolved_buff_actions=[action],
        battle_start_ms=100, battle_end_ms=1000)
    assert report['ok'] is False
    assert report['blocker_reason_counts'] == {'unresolved_buff_action_phase': 1}
    # Explicit later finish bounds the possible effect; it cannot contaminate
    # a battle that starts after that observed finish.
    action['end_ts_ms'] = 200
    report = parser._build_rdps_preflight([], [], unresolved_buff_actions=[action],
        battle_start_ms=300, battle_end_ms=1000)
    assert report['ok'] is True


def test_duration_only_uses_official_exact_assignment(monkeypatch):
    monkeypatch.setattr(parser, '_load_current_buff_roots', lambda: {
        'buff_exact': {'lifeType': 'Limited', 'duration': {'useBlackboardKey': True, 'blackboardKey': 'timer'}},
    })
    record = {'event_key': 'buff_exact', 'target_character_key': 'chr_0004_pelica',
              'bb_values': {'duration': 50, 'timer': 2}, 'raw_duration_ms': None}
    parser._apply_enemy_status_bb_overrides(record, context_enemy_hint='eny_0051_rodin')
    assert record['raw_duration_ms'] == 2000
    assert record['target_character_key'] == 'chr_0004_pelica'
    assert record.get('target_enemy_key') is None
    unknown = {'event_key': 'buff_unknown', 'bb_values': {'duration': 50}, 'raw_duration_ms': None}
    parser._apply_enemy_status_bb_overrides(unknown, context_enemy_hint=None)
    assert unknown['raw_duration_ms'] is None


def test_infinity_uses_an_open_interval_and_ignores_duration_configuration(monkeypatch):
    monkeypatch.setattr(parser, '_load_current_buff_roots', lambda: {
        'buff_infinite': {'lifeType': 'Infinity', 'duration': {'useBlackboardKey': False, 'value': 11}},
    })
    record = {'event_key': 'buff_infinite', 'raw_duration_ms': None, 'end_ts_ms': None,
              'bb_values': {'duration': 3}}
    parser._apply_enemy_status_bb_overrides(record, context_enemy_hint=None)
    assert record['raw_duration_ms'] is None
    assert record['end_ts_ms'] is None
    assert record['lifetime_mode'] == 'Infinity'
    assert parser._normalize_buff_duration_ms(0, event_key='buff_infinite', end_ts_ms=None,
        raw_duration_ms=None, battle_end_ms=20000) == 20000
    assert parser._normalize_buff_duration_ms(0, event_key='buff_infinite', end_ts_ms=15000,
        raw_duration_ms=None, battle_end_ms=20000) == 15000


def test_condition_buff_presence_is_bound_to_instance_and_lifecycle():
    hit = {'target_enemy_key': 'eny_0051_rodin', 'target_actor_id': '2001', 'ts_ms': 500, 'line_no': 10}
    record = {'event_key': 'buff_state', 'target_enemy_key': 'eny_0051_rodin', 'target_actor_id': '2002',
              'ts_ms': 0, 'raw_duration_ms': 1000, 'end_ts_ms': None, 'uid': 'u'}
    assert parser._active_target_buff_ids_for_hit(hit, [record]) == set()
    record['target_actor_id'] = '2001'
    assert parser._active_target_buff_ids_for_hit(hit, [record]) == {'buff_state'}
    assert parser._active_target_buff_ids_for_hit(dict(hit, ts_ms=1000), [record]) == set()
    record['raw_duration_ms'] = None
    assert parser._active_target_buff_ids_for_hit(hit, [record]) == set()
    seen = dict(hit, packet_modifier_uids={'defender': ['u']})
    assert parser._active_target_buff_ids_for_hit(seen, [record]) == {'buff_state'}
    record.update(end_ts_ms=500, end_line_no=9)
    assert parser._active_target_buff_ids_for_hit(hit, [record]) == set()
    record['end_line_no'] = 11
    assert parser._active_target_buff_ids_for_hit(hit, [record]) == {'buff_state'}


def test_enemy_multiplier_does_not_apply_to_another_instance():
    hit = {'character_key': 'chr_0004_pelica', 'target_enemy_key': 'eny_0051_rodin',
           'target_actor_id': '2001', 'hit_value': 120, 'ts_ms': 500,
           'skill_key': 'known', 'damage_element': 'pulse', 'damage_school': 'spell'}
    window = {'uid': 'u', 'event_key': 'buff_effect', 'source_character_key': 'chr_0023_antal',
              'target_character_key': 'eny_0051_rodin', 'target_actor_id': '2002',
              'start_ts_ms': 0, 'end_ts_ms': 1000,
              'zone_effects': [{'zone': 'vuln_taken', 'element': 'all', 'rate': 0.2}]}
    assert parser._allocate_rdps_for_hit(deepcopy(hit), [window])['contributions_map'] == {'chr_0004_pelica': 120}
    window['target_actor_id'] = '2001'
    values = parser._allocate_rdps_for_hit(deepcopy(hit), [window])['contributions_map']
    assert values == {'chr_0004_pelica': 100, 'chr_0023_antal': 20}
    unknown = deepcopy(hit)
    unknown.pop('target_actor_id')
    parser._allocate_rdps_for_hit(unknown, [window])
    assert unknown['rdps_unresolved_target_binding'] is True
