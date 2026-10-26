import pytest

from endfield_pcap.service import SessionPipeline
from endfield_pcap.trace_bridge import TraceBridge
from endfield_pcap.proto_generated.CS_BATTLE_OP_pb2 import CS_BATTLE_OP
from parser_core import battle_log_parser as parser


def test_service_preserves_false_states_and_large_uid():
    message = CS_BATTLE_OP()
    op = message.client_data.op_list.add()
    op.op_type = 20
    op.owner_id = 1001
    op.seq_id = 12
    op.client_tick_tms = 123
    op.generation = 4
    op.set_buff_enable_state_op_data.buff_inst_id_to_enable_state[9223372036854775901] = False
    pipeline = SessionPipeline.__new__(SessionPipeline)
    pipeline.session_id = 'fixture'
    events = []
    pipeline._emit_event = events.append
    pipeline._emit_battle_events(message, 1000)
    assert len(events) == 1
    assert events[0].event_type == 'BattleOpSetBuffEnableState'
    assert events[0].payload['states'] == {'9223372036854775901': False}
    assert events[0].payload['generation'] == 4


@pytest.mark.parametrize('sequence,enabled', [(11, True), (21, False), (31, True)])
def test_state_machine_uses_packet_operation_order_at_same_timestamp(sequence, enabled):
    effects = [{'zone': 'atk', 'element': 'all', 'rate': 0.2}]
    window = {'zone_effects': effects, 'enable_observations': [
        {'ts_ms': 1000, 'line_no': 1, 'operationSeq': 10, 'enabled': True},
        {'ts_ms': 1000, 'line_no': 2, 'operationSeq': 20, 'enabled': False},
        {'ts_ms': 1000, 'line_no': 3, 'operationSeq': 30, 'enabled': True},
    ]}
    assert bool(parser._window_effects_at_ts(window, 1000, packet_seq_id=sequence)) is enabled


@pytest.mark.parametrize('state_owner,expected_credit', [('1001', 0), ('9999', 20), ('conflict', 0)])
def test_disabled_buff_is_bound_to_its_uid_and_owner(tmp_path, state_owner, expected_credit):
    trace = tmp_path / 'trace.log'
    bridge = TraceBridge(trace)
    bridge._line(1000, 'BUFF_START #1 id="buff_wpn_lance_0014_damageup" uid=9223372036854775901 owner=chr_0004_pelica src=chr_0035_liino ownerId=1001 srcId=2001 dur=10')
    bridge._line(1000, 'BB[9223372036854775901]: atk_up=0.2')
    bridge.handle_event({'type': 'BattleOpSetBuffEnableState', 'timestamp_ms': 1050, 'owner_id': '1001',
                         'seq_id': 10, 'client_tick_tms': 1050, 'generation': 4,
                         'states': {'9223372036854775901': True}})
    bridge.handle_event({'type': 'BattleOpSetBuffEnableState', 'timestamp_ms': 1100,
                         'owner_id': '1001' if state_owner == 'conflict' else state_owner,
                         'seq_id': 10 if state_owner == 'conflict' else 20, 'client_tick_tms': 1100, 'generation': 4,
                         'states': {'9223372036854775901': False}})
    bridge._line(1200, 'HP_V2 #1 hit=120 raw=120 packetFinalValue=120 cum=120 pHP=100 eHP=1000 skill="chr_0004_pelica_combo_skill_projhit" actionId=11 damageUnitIndex=0 src=chr_0004_pelica tgt=eny_0051_rodin atk=chr_0004_pelica atkId=1001 tgtId=3001 hits=1')
    parsed = parser.parse_raw_battle_log_text(trace.read_text(encoding='utf-8'), include_rdps_debug=True)
    hit = parsed['debug_hits'][0]
    credit = sum(row['value'] for row in hit['rdps_contributions'] if row['character_key'] == 'chr_0035_liino')
    assert credit == pytest.approx(expected_credit)
    buff = next(row for row in parsed['buff_events'] if row['uid'] == '9223372036854775901')
    if state_owner == '1001':
        assert [row['enabled'] for row in buff['enable_observations']] == [True, False]
    elif state_owner == 'conflict':
        assert any(row['reason'] == 'conflicting_buff_enable_state' for row in parsed['unresolved_buff_actions'])
    else:
        assert [row['enabled'] for row in buff['enable_observations']] == [True]
        assert any(row['reason'] == 'unbound_buff_enable_state' for row in parsed['unresolved_buff_actions'])
