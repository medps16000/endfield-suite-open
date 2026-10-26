"""Synthetic packet identity observations; no claim of fresh allocation or rDPS."""
import json

import pytest

from endfield_pcap.proto_generated.CS_BATTLE_OP_pb2 import CS_BATTLE_OP
from endfield_pcap.service import SessionPipeline
from endfield_pcap.trace_bridge import TraceBridge


UID = 18446744073709551612
SOURCE = 18446744073709551611
TARGET = 18446744073709551610
OP_OWNER = 18446744073709551609
BUFF = 'buff_common_natural_natural_corrupt_do'


def packets(kind='create', phase=0, generation=41, sequence=11):
    message = CS_BATTLE_OP()
    op = message.client_data.op_list.add()
    op.owner_id = OP_OWNER
    op.generation = generation
    op.seq_id = sequence
    op.client_tick_tms = 1000 + sequence
    if kind == 'add':
        op.op_type = 1
        add = op.add_buff_op_data
        add.src_inst_id = SOURCE
        add.target_inst_id = TARGET
        add.buff_inst_id = UID
        add.buff_id.type = 1
        add.buff_id.int_id = 1984
    else:
        op.op_type = 8
        trigger = op.trigger_action_op_data
        trigger.owner_id = OP_OWNER
        trigger.inst_id = 5001
        trigger.owner_type = 1
        trigger.trigger_type = phase
        trigger.template_id.type = 1
        trigger.template_id.int_id = 331
        trigger.action.action_type = 2
        trigger.action.action_id = 7
        detail = trigger.action.create_buff_action.details.add()
        detail.source_id = SOURCE
        detail.target_id = TARGET
        detail.buff_inst_id = UID
        detail.buff_num_id = 1984
        detail.buff_id = BUFF
    return message


def events(message):
    pipeline = SessionPipeline.__new__(SessionPipeline)
    pipeline.session_id = 'synthetic-creation-session'
    pipeline.skill_levels_by_battle_inst = {}
    out = []
    pipeline._emit_event = out.append
    pipeline._emit_battle_events(CS_BATTLE_OP.FromString(message.SerializeToString()), 2000)
    return [event.as_dict() for event in out]


def emit(bridge, message):
    for event in events(message):
        bridge.handle_event(event)
    bridge._flush_pending_add_buffs()
    bridge._flush_pending_create_buff_triggers()
    bridge._flush_pending_buff_starts()


def observations(bridge):
    text = bridge.trace_file.read_text(encoding='utf-8')
    rows = [json.loads(line.split(' BUFF_CREATE_OBSERVED ', 1)[1])
            for line in text.splitlines() if ' BUFF_CREATE_OBSERVED ' in line]
    return text, rows


@pytest.mark.parametrize('kind', ['add', 'create'])
def test_creation_observation_keeps_owner_context_without_inventing_buff_generation(tmp_path, kind):
    bridge = TraceBridge(tmp_path / 'trace.log')
    emit(bridge, packets(kind))
    text, rows = observations(bridge)
    assert len(rows) == 1
    row = rows[0]
    assert row['buffUid'] == str(UID)
    assert row['buffTemplate'] == BUFF
    assert row['sourceActorId'] == str(SOURCE)
    assert row['targetActorId'] == str(TARGET)
    assert row['sessionId'] == 'synthetic-creation-session'
    assert row['operationSeq'] == 11
    assert row['operationOwnerId'] == str(OP_OWNER)
    assert row['operationGeneration'] == 41
    assert row['generationScope'] == 'operation_owner'
    assert 'generation' not in row and 'buffGeneration' not in row
    assert row['isNewInstance'] is None
    assert row['priorUidObserved'] is False
    assert row['observationKind'] == 'creation_or_refresh'
    assert row['enhanceMultiplier'] is None
    assert ' enh=unknown' in text and ' enh=1' not in text
    assert text.index(' BUFF_START ') < text.index(' BUFF_CREATE_OBSERVED ')
    if kind == 'add':
        assert row['sourceEvent'] == 'BattleOpAddBuff'
        assert row['buffTemplateId'] == {'int_id': 1984, 'type': 'Buff'}
        assert ' BUFF_ORIGIN ' not in text
    else:
        assert row['sourceEvent'] == 'BattleOpTriggerAction'
        assert row['buffNumId'] == 1984 and row['buffId'] == BUFF
        assert row['actionId'] == 7 and row['triggerType'] == 0


@pytest.mark.parametrize('phase', [1, 2])
def test_non_begin_create_action_does_not_invent_creation_anchor(tmp_path, phase):
    bridge = TraceBridge(tmp_path / 'trace.log')
    emit(bridge, packets(phase=phase))
    text, rows = observations(bridge)
    assert rows == []
    assert ' BUFF_START ' not in text


@pytest.mark.parametrize('kind', ['add', 'create'])
def test_repeated_uid_and_changed_operation_generation_are_not_new_instance_proof(tmp_path, kind):
    bridge = TraceBridge(tmp_path / 'trace.log')
    emit(bridge, packets(kind, generation=41))
    emit(bridge, packets(kind, generation=42, sequence=12))
    text, rows = observations(bridge)
    assert len(rows) == 2
    assert [row['operationGeneration'] for row in rows] == [41, 42]
    assert [row['priorUidObserved'] for row in rows] == [False, True]
    assert all(row['isNewInstance'] is None for row in rows)
    assert text.count(' BUFF_START ') == 1


def test_explicit_end_closes_observation_before_uid_reappears(tmp_path):
    bridge = TraceBridge(tmp_path / 'trace.log')
    emit(bridge, packets())
    emit(bridge, packets(phase=2, sequence=12))
    emit(bridge, packets(sequence=13))
    text, rows = observations(bridge)
    assert len(rows) == 2
    assert [row['operationSeq'] for row in rows] == [11, 13]
    assert all(row['isNewInstance'] is None for row in rows)
    assert text.count(' BUFF_END ') == 1
    assert text.count(' BUFF_START ') == 2
    assert text.index(' BUFF_END ') < text.rindex(' BUFF_CREATE_OBSERVED ')


def test_add_buff_preserves_zero_template_oneof_and_zero_operation_generation(tmp_path):
    packet = packets('add', generation=0)
    packet.client_data.op_list[0].add_buff_op_data.buff_id.int_id = 0
    event = events(packet)[0]
    assert event['buff_template_id_case'] == 'int_id'
    assert event['buff_template_id'] == {'int_id': 0, 'type': 'Buff'}
    bridge = TraceBridge(tmp_path / 'trace.log')
    bridge.handle_event(event)
    bridge._flush_pending_add_buffs()
    _, rows = observations(bridge)
    assert rows[0]['buffTemplateId']['int_id'] == 0
    assert rows[0]['operationGeneration'] == 0


def test_damage_keeps_exact_packet_session_owner_and_generation(tmp_path):
    packet = packets()
    trigger = packet.client_data.op_list[0].trigger_action_op_data
    trigger.action.action_type = 1
    damage = trigger.action.damage_action
    damage.attacker_id = SOURCE
    detail = damage.details.add(target_id=TARGET, value=-123.5, cur_hp=500)
    event = events(packet)[0]
    bridge = TraceBridge(tmp_path / 'trace.log')
    bridge.handle_event(event)
    text = bridge.trace_file.read_text(encoding='utf-8')
    line = next(line for line in text.splitlines() if ' HP_V2 ' in line)
    assert 'packetSessionId="synthetic-creation-session"' in line
    assert f'packetOwnerId={OP_OWNER}' in line
    assert 'packetGeneration=41' in line
    assert f'packetOwnerId={TARGET}' not in line


def test_unknown_damage_packet_context_is_omitted_instead_of_filled_from_legacy_owner(tmp_path):
    packet = packets()
    trigger = packet.client_data.op_list[0].trigger_action_op_data
    trigger.action.action_type = 1
    trigger.action.damage_action.attacker_id = SOURCE
    trigger.action.damage_action.details.add(target_id=TARGET, value=-123.5, cur_hp=500)
    event = events(packet)[0]
    for field in ('session_id', 'operation_owner_id', 'generation'):
        event.pop(field)
    bridge = TraceBridge(tmp_path / 'trace.log')
    bridge.handle_event(event)
    text = bridge.trace_file.read_text(encoding='utf-8')
    assert ' HP_V2 ' in text
    assert 'packetSessionId=' not in text
    assert 'packetOwnerId=' not in text
    assert 'packetGeneration=' not in text
