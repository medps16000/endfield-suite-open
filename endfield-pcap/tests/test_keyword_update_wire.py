"""Synthetic wire-to-parser coverage for the approved Antal keyword update."""
import json

import pytest

from endfield_pcap.proto_generated.CS_BATTLE_OP_pb2 import CS_BATTLE_OP
from endfield_pcap.service import SessionPipeline
from endfield_pcap.trace_bridge import TraceBridge
from parser_core import battle_log_parser as parser


SESSION = 'synthetic-keyword-wire'
UID = 9223372036854776111
OWNER = 9223372036854776112
SOURCE = 9223372036854776113
MARKER = 'buff_chr_0023_antal_talent_1_combotrigger'


def number(name):
    matches = [int(key) for key, value in parser._load_num_id_str_buff_map().items() if value == name]
    assert len(matches) == 1
    return matches[0]


def packet(kind, seq=30, value=0.3, element='Fire'):
    message = CS_BATTLE_OP()
    op = message.client_data.op_list.add()
    op.owner_id = OWNER
    op.seq_id = seq
    op.client_tick_tms = 9223372036854770000 + seq
    op.generation = 3
    if kind == 'create':
        op.op_type = 8
        trigger = op.trigger_action_op_data
        trigger.owner_id = OWNER
        trigger.inst_id = 9223372036854776114
        trigger.owner_type = 1
        trigger.template_id.type = 1
        trigger.template_id.int_id = 912
        trigger.action.action_type = 2
        trigger.action.action_id = 1 if element == 'Fire' else 0
        detail = trigger.action.create_buff_action.details.add()
        detail.buff_inst_id = UID
        detail.source_id = SOURCE
        detail.target_id = OWNER
        main = parser._load_rdps_semantics_registry()['_keyword_buff_map']['Vulnerable'][element]
        detail.buff_num_id = number(main)
        row = detail.assigned_items.add()
        row.target_key = 'rate'
        row.use_direct_value = True
        row.numeric_value = 0.2
        row = detail.assigned_items.add()
        row.target_key = 'duration'
        row.use_direct_value = True
        row.numeric_value = 20.0
    elif kind == 'enable':
        op.op_type = 20
        op.set_buff_enable_state_op_data.buff_inst_id_to_enable_state[UID] = True
    elif kind == 'end':
        op.op_type = 2
        op.finish_buff_op_data.buff_inst_id = UID
    else:
        op.op_type = 15
        op.trigger_keyword_op_data.buff_inst_id = UID
        op.trigger_keyword_op_data.add_buff_num_id = number(MARKER)
        update = op.trigger_keyword_op_data.modify.add()
        update.config_index = 0
        update.modify_value = value
    return message


def service(message, timestamp):
    pipeline = SessionPipeline.__new__(SessionPipeline)
    pipeline.session_id = SESSION
    pipeline.skill_levels_by_battle_inst = {}
    emitted = []
    pipeline._emit_event = emitted.append
    pipeline._emit_battle_events(CS_BATTLE_OP.FromString(message.SerializeToString()), timestamp)
    return [event.as_dict() for event in emitted]


def replay(tmp_path, operations, element='Fire'):
    bridge = TraceBridge(tmp_path / 'trace.log')
    bridge.id_to_name[OWNER] = 'eny_0051_rodin'
    bridge.id_to_name[SOURCE] = 'chr_0023_antal'
    all_ops = [(packet('create', seq=10, element=element), 1000), (packet('enable', seq=11), 1000)] + operations
    for message, timestamp in all_ops:
        for event in service(message, timestamp):
            bridge.handle_event(event)
    bridge._flush_pending_add_buffs()
    bridge._flush_pending_create_buff_triggers()
    bridge._line(5000, f'HP_V2 #1 hit=120 raw=120 packetFinalValue=120 cum=120 packetSeq=100 packetSessionId="{SESSION}" pHP=100 eHP=1000 skill="fixture" src=chr_0016_laevat tgt=eny_0051_rodin atk=chr_0016_laevat atkId=1001 tgtId={OWNER}')
    text = bridge.trace_file.read_text(encoding='utf-8')
    return text, parser.parse_raw_battle_log_text(text, include_rdps_debug=True)


@pytest.mark.parametrize('element', ['Fire', 'Pulse'])
def test_keyword_packet_exports_final_rate_and_updates_exact_main(tmp_path, element):
    value = 0.30000000000000004
    text, result = replay(tmp_path, [(packet('keyword', value=value), 2000)], element)
    line = next(line for line in text.splitlines() if ' BUFF_KEYWORD_UPDATE ' in line)
    row = json.loads(line.split(' BUFF_KEYWORD_UPDATE ', 1)[1])
    assert row['buffUid'] == str(UID)
    assert row['ownerId'] == str(OWNER)
    assert row['generation'] == 3
    assert row['sessionId'] == SESSION
    assert row['clientTickMs'] == str(9223372036854770030)
    assert row['modify'] == [{'configIndex': 0, 'rateAfter': value}]
    record = next(row for row in result['buff_events'] if row['uid'] == str(UID))
    effects = parser._window_effects_at_ts(record, 8 * 3600 * 1000 + 2500, packet_seq_id=31,
                                          packet_session_id=SESSION)
    assert [(e['element'], e['rate']) for e in effects] == [(element.lower(), value)]
    assert effects[0]['rdps_whitelist_keys'] == ['buff_chr_0023_antal_normal_fragile']


def test_recycled_uid_does_not_keep_previous_keyword_rate(tmp_path):
    text, result = replay(tmp_path, [(packet('keyword'), 2000), (packet('end', 40), 3000),
                                    (packet('create', 50), 3500), (packet('enable', 51), 3500)])
    records = [row for row in result['buff_events'] if row['uid'] == str(UID)]
    # Only the later instance overlaps the fixture's damage window.
    assert len(records) == 1
    assert text.count(' BUFF_START ') == 2 and text.count(' BUFF_END ') == 1
    assert not records[0]['dynamic_effects']
    assert records[0]['zone_effects'][0]['rate'] == pytest.approx(0.2)


def test_later_explicit_same_uid_creation_can_replace_keyword_rate_with_old_base(tmp_path):
    text, result = replay(tmp_path, [(packet('keyword'), 2000), (packet('create', 40), 3000)])
    assert text.count(' BUFF_START ') == 2
    records = [row for row in result['buff_events'] if row['uid'] == str(UID) and row.get('snapshot_update')]
    assert len(records) == 1
    record = records[0]
    assert not record['dynamic_effects']
    assert record['zone_effects'][0]['rate'] == pytest.approx(0.2)


@pytest.mark.parametrize('value', [float('nan'), float('inf'), float('-inf')])
def test_nonfinite_keyword_wire_value_is_observed_as_unresolved(tmp_path, value):
    text, result = replay(tmp_path, [(packet('keyword', value=value), 2000)])
    assert ' BUFF_KEYWORD_UPDATE_UNRESOLVED ' in text
    assert ' BUFF_KEYWORD_UPDATE ' not in text
    assert any(row['reason'] == 'unresolved_keyword_packet_fields' for row in result['unresolved_buff_actions'])


def test_op15_requires_its_actual_oneof_payload():
    message = packet('keyword')
    message.client_data.op_list[0].add_buff_op_data.buff_inst_id = UID
    assert service(message, 1000) == []


def test_keyword_update_is_not_inferred_from_a_payload_on_another_operation(tmp_path):
    message = packet('keyword')
    message.client_data.op_list[0].op_type = 999
    events = service(message, 1000)
    assert not any(event['type'] == 'BattleOpTriggerKeyword' for event in events)
