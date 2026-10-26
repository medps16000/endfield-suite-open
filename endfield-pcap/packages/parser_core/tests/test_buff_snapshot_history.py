import json

import pytest

from parser_core import battle_log_parser as parser
from parser_core.hit_debug import parse_hit_debug_log_text
from parser_core.unified import parse_hit_viewer_log_text


UID = '9007199254740993'
ATTACKER = 'chr_0004_pelica'
PROVIDER = 'chr_0035_liino'


def start(stamp, source=PROVIDER, actor='2001'):
    return f'[{stamp}] BUFF_START #1 id="buff_wpn_lance_0014_damageup" uid={UID} owner={ATTACKER} src={source} ownerId=1001 srcId={actor} dur=10'


def state(stamp, enabled, seq):
    return f'[{stamp}] BUFF_ENABLE ' + json.dumps({'schemaVersion': 1, 'buffUid': UID, 'ownerId': '1001',
                                                'enabled': enabled, 'operationSeq': seq})


def hit(stamp, seq, damage):
    return f'[{stamp}] HP_V2 #{seq} hit={damage} cum={damage} raw={damage} packetFinalValue={damage} pHP=100 eHP=1000 skill="chr_0004_pelica_combo_skill_projhit" actionId=11 damageUnitIndex=0 src={ATTACKER} tgt=eny_0051_rodin atk={ATTACKER} atkId=1001 tgtId=3001 hits=1'


@pytest.mark.parametrize('mode', ['later', 'same_stamp', 'disabled', 'reenabled', 'partial'])
def test_snapshot_rates_and_enable_history_are_shared_by_all_public_views(mode):
    boundary = '10:00:00.000' if mode == 'same_stamp' else '10:00:01.000'
    first = '10:00:00.000' if mode == 'same_stamp' else '10:00:00.500'
    second = '10:00:00.000' if mode == 'same_stamp' else '10:00:01.500'
    lines = [start('10:00:00.000'), f'[10:00:00.000] BB[{UID}]: atk_up=0.2',
             state('10:00:00.000', True, 1), hit(first, 1, 120)]
    if mode in ('disabled', 'reenabled'):
        lines.append(state('10:00:00.750', False, 2))
    lines += [start(boundary), f'[{boundary}] BB[{UID}]: ' + ('duration=10' if mode == 'partial' else 'atk_up=0.4')]
    if mode == 'reenabled':
        lines.append(state('10:00:01.250', True, 3))
    damage = 120 if mode == 'partial' else 140
    lines.append(hit(second, 2, damage))
    text = '\n'.join(lines)
    primary = parser.parse_raw_battle_log_text(text, include_rdps_debug=True)
    expected_second = {ATTACKER: 140} if mode == 'disabled' else {ATTACKER: 100, PROVIDER: damage - 100}
    for report_hits in [primary['debug_hits'], parse_hit_debug_log_text(text)['hits'], parse_hit_viewer_log_text(text)['hits']]:
        assert len(report_hits) == 2
        maps = [{row['character_key']: row['value'] for row in h['rdps_contributions']} for h in report_hits]
        assert maps[0] == pytest.approx({ATTACKER: 100, PROVIDER: 20})
        assert maps[1] == pytest.approx(expected_second)
        assert all(sum(row['value'] for row in h['rdps_contributions']) == pytest.approx(h['hit_value']) for h in report_hits)
    snapshots = [row for row in primary['buff_events'] if row['uid'] == UID]
    assert len(snapshots) == 2
    assert snapshots[0]['bb_values']['atk_up'] == 0.2
    assert snapshots[0]['raw_end_ts_ms'] is None
    assert snapshots[0]['snapshot_end_line_no'] == snapshots[1]['line_no']


def test_end_and_new_lifetime_do_not_share_disabled_state():
    text = '\n'.join([start('10:00:00.000'), f'[10:00:00.000] BB[{UID}]: atk_up=0.2',
        state('10:00:00.000', True, 1), state('10:00:00.500', False, 2),
        f'[10:00:01.000] BUFF_END #2 uid={UID}',
        start('10:00:02.000'), f'[10:00:02.000] BB[{UID}]: atk_up=0.4',
        state('10:00:02.000', True, 3), hit('10:00:02.500', 1, 140)])
    parsed = parser.parse_raw_battle_log_text(text, include_rdps_debug=True)
    snapshots = [row for row in parsed['buff_events'] if row['uid'] == UID]
    assert snapshots[0]['instance_start_line_no'] != snapshots[1]['instance_start_line_no']
    assert [state['enabled'] for state in snapshots[1]['enable_observations']] == [True]
    values = {row['character_key']: row['value'] for row in parsed['debug_hits'][0]['rdps_contributions']}
    assert values == pytest.approx({ATTACKER: 100, PROVIDER: 40})


def test_conflicting_live_owner_does_not_reassign_credit():
    text = '\n'.join([start('10:00:00.000'), f'[10:00:00.000] BB[{UID}]: atk_up=0.2',
        hit('10:00:00.500', 1, 120), start('10:00:01.000', 'chr_0023_antal', '4001'),
        f'[10:00:01.000] BB[{UID}]: atk_up=0.4', hit('10:00:01.500', 2, 140)])
    parsed = parser.parse_raw_battle_log_text(text, include_rdps_debug=True)
    assert parsed['debug_hits'][1]['rdps_contributions'] == [
        {'character_key': ATTACKER, 'character_name': '佩丽卡', 'value': 140.0}]
    assert any('conflicting_live_buff_identity' in str(row) for row in parsed['rdps_preflight']['blockers'])


def test_stack_limit_counts_instances_not_snapshot_versions():
    windows = [
        {'uid': UID, 'event_key': 'fixture', 'source_character_key': PROVIDER, 'target_character_key': ATTACKER,
         'start_ts_ms': 0, 'start_line_no': 1, 'end_ts_ms': 1000, 'end_line_no': 20, 'stack_limit': 1,
         'snapshot_end_ts_ms': 1000, 'snapshot_end_line_no': 20, 'zone_effects': [{'zone': 'atk', 'element': 'all', 'rate': 0.2}]},
        {'uid': UID, 'event_key': 'fixture', 'source_character_key': PROVIDER, 'target_character_key': ATTACKER,
         'start_ts_ms': 1000, 'start_line_no': 20, 'end_ts_ms': 2000, 'stack_limit': 1, 'snapshot_update': True,
         'zone_effects': [{'zone': 'atk', 'element': 'all', 'rate': 0.4}]},
    ]
    result = parser._apply_buff_stack_limits(windows)
    assert parser._window_effects_at_ts(result[0], 1000, line_no=15)[0]['rate'] == 0.2
    assert parser._window_effects_at_ts(result[1], 1000, line_no=25)[0]['rate'] == 0.4
    assert parser._window_effects_at_ts(result[0], 1000) == []
    assert parser._window_effects_at_ts(result[1], 1000) == []
    ambiguous = {'character_key': ATTACKER, 'target_enemy_key': 'enemy', 'ts_ms': 1000, 'hit_value': 140}
    allocation = parser._allocate_rdps_for_hit(ambiguous, result, include_debug=True)
    assert ambiguous['rdps_unresolved_effect_conditions'] is True
    assert any(row['reason'] == 'unresolved_buff_snapshot_order' for row in allocation['ignored_effects'])


@pytest.mark.parametrize('new_hit_present', [True, False])
def test_viewer_does_not_merge_reused_hit_sequence_across_challenges(new_hit_present):
    lines = [
        '[10:00:00.000] OFFICIAL_TIMER_START source=SC_GAME_MECHANICS_SYNC_CHALLENGE_START gameId=dung01_bossrush01_04 challengeStartTs=1000',
        hit('10:00:00.500', 1, 1000),
        '[10:00:02.000] OFFICIAL_TIMER_START source=SC_GAME_MECHANICS_SYNC_CHALLENGE_START gameId=dung01_bossrush01_04 challengeStartTs=3000',
    ]
    if new_hit_present:
        lines.append(hit('10:00:02.500', 1, 120))
    text = '\n'.join(lines)
    raw = parser.parse_raw_battle_log_text(text, include_rdps_debug=True)
    viewer = parse_hit_viewer_log_text(text)
    assert len(raw['debug_hits']) == len(viewer['hits']) == int(new_hit_present)
    assert viewer['summary']['total_damage'] == raw['battle']['total_damage'] == (120 if new_hit_present else 0)
    if new_hit_present:
        assert viewer['hits'][0]['line_no'] == raw['debug_hits'][0]['line_no'] == 4


def test_packet_modifier_details_use_snapshot_at_hit_not_latest_values():
    records = [
        {'uid': UID, 'ts_ms': 0, 'line_no': 1, 'snapshot_end_ts_ms': 1000, 'snapshot_end_line_no': 10,
         'event_key': 'fixture', 'bb_values': {'rate': 0.2}},
        {'uid': UID, 'ts_ms': 1000, 'line_no': 10, 'event_key': 'fixture', 'bb_values': {'rate': 0.4}},
    ]
    index = parser._build_buff_record_index(records)
    for timestamp, line, rate in [(500, 5, 0.2), (1000, 9, 0.2), (1000, 11, 0.4), (1000, 0, None)]:
        value = parser._allocate_rdps_for_hit({'character_key': ATTACKER, 'target_enemy_key': 'enemy',
            'ts_ms': timestamp, 'line_no': line, 'hit_value': 100, 'packet_modifier_uids': {'attacker': [UID]}},
            [], include_debug=True, buff_records_by_uid=index)
        if rate is None:
            assert value['packet_modifier_details'][0]['bb_values'] == {}
        else:
            assert value['packet_modifier_details'][0]['bb_values']['rate'] == rate


def test_conflicting_state_does_not_erase_prior_or_later_known_state():
    window = {'zone_effects': [{'zone': 'atk', 'element': 'all', 'rate': 0.2}],
              'rdps_conflicting_enable_state': True, 'enable_observations': [
                  {'operationSeq': 10, 'ts_ms': 0, 'line_no': 1, 'enabled': True},
                  {'operationSeq': 20, 'ts_ms': 500, 'line_no': 2, 'enabled': True},
                  {'operationSeq': 20, 'ts_ms': 600, 'line_no': 3, 'enabled': False},
                  {'operationSeq': 30, 'ts_ms': 1000, 'line_no': 4, 'enabled': True},
              ]}
    assert parser._window_effects_at_ts(window, 200, packet_seq_id=15)
    assert parser._window_effects_at_ts(window, 800, packet_seq_id=25) == []
    assert parser._window_effects_at_ts(window, 1200, packet_seq_id=35)
