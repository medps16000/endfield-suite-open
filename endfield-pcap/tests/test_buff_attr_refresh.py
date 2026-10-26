"""Synthetic protobuf capture contracts, not real-game rDPS validation."""
import json
import re

import pytest

from endfield_pcap.proto_generated.CS_BATTLE_OP_pb2 import CS_BATTLE_OP
from endfield_pcap.service import SessionPipeline
from endfield_pcap.trace_bridge import TraceBridge


OWNER = 18446744073709551613
OP_OWNER = 18446744073709551614
INSTANCE = 18446744073709551612
TICK = 9223372036854775806


def message(action_type=31, template_case='int_id', template_value=1984):
    result = CS_BATTLE_OP()
    op = result.client_data.op_list.add()
    op.op_type = 8
    op.owner_id = OP_OWNER
    op.seq_id = 21
    op.client_tick_tms = TICK
    op.generation = 4
    trigger = op.trigger_action_op_data
    trigger.owner_id = OWNER
    trigger.owner_type = 1
    trigger.inst_id = INSTANCE
    trigger.template_id.type = 1
    setattr(trigger.template_id, template_case, template_value)
    trigger.action.action_type = action_type
    trigger.action.action_id = 14
    if action_type == 31:
        trigger.action.refresh_buff_attr_modifier_value_action.SetInParent()
    return result


def service_events(packet):
    # Exercise wire decoding as well as service JSON conversion; an explicit
    # empty oneof message is different from an omitted runtime message.
    decoded = CS_BATTLE_OP.FromString(packet.SerializeToString())
    pipeline = SessionPipeline.__new__(SessionPipeline)
    pipeline.session_id = 'synthetic-refresh-session'
    events = []
    pipeline._emit_event = events.append
    pipeline._emit_battle_events(decoded, 1000)
    return [event.as_dict() for event in events]


def trace_event(tmp_path, event):
    bridge = TraceBridge(tmp_path / 'trace.log')
    bridge.handle_event(event)
    return bridge, bridge.trace_file.read_text(encoding='utf-8')


@pytest.mark.parametrize('case,value', [('int_id', 1984), ('int_id', 0),
                                      ('str_id', 'buff_common_natural_natural_corrupt_do'), ('str_id', '')])
def test_empty_refresh_runtime_keeps_exact_identity_through_wire_service_and_bridge(tmp_path, case, value):
    events = service_events(message(template_case=case, template_value=value))
    assert len(events) == 1
    event = events[0]
    assert event['action_runtime_case'] == 'refresh_buff_attr_modifier_value_action'
    assert event['action']['refresh_buff_attr_modifier_value_action'] == {}
    assert event['template_id_case'] == case
    _, text = trace_event(tmp_path, event)
    rows = [json.loads(line.split(' BUFF_ATTR_REFRESH ', 1)[1])
            for line in text.splitlines() if ' BUFF_ATTR_REFRESH ' in line]
    assert len(rows) == 1
    row = rows[0]
    assert row == {
        'schemaVersion': 1, 'sessionId': 'synthetic-refresh-session', 'operationSeq': 21,
        'operationOwnerId': str(OP_OWNER), 'ownerType': '1', 'ownerId': str(OWNER),
        'environmentInstId': str(INSTANCE), 'buffUid': str(INSTANCE), 'generation': 4,
        'clientTickMs': str(TICK), 'templateType': 'Buff', 'templateIdCase': case,
        'templateIntId': value if case == 'int_id' else None,
        'templateStrId': value if case == 'str_id' else None,
        'actionId': 14, 'triggerType': 0, 'actionType': 31,
        'actionRuntimeCase': 'refresh_buff_attr_modifier_value_action',
        'operationPayloadCase': 'trigger_action_op_data',
    }


def test_refresh_does_not_substitute_operation_owner_for_explicit_zero_trigger_owner(tmp_path):
    packet = message()
    packet.client_data.op_list[0].trigger_action_op_data.owner_id = 0
    event = service_events(packet)[0]
    assert event['owner_id'] == OP_OWNER  # Existing display compatibility field.
    _, text = trace_event(tmp_path, event)
    row = json.loads(text.split(' BUFF_ATTR_REFRESH ', 1)[1])
    assert row['ownerId'] == '0'
    assert row['operationOwnerId'] == str(OP_OWNER)


def test_nonbuff_environment_id_is_not_exported_as_buff_uid(tmp_path):
    packet = message()
    packet.client_data.op_list[0].trigger_action_op_data.owner_type = 3
    _, text = trace_event(tmp_path, service_events(packet)[0])
    row = json.loads(text.split(' BUFF_ATTR_REFRESH ', 1)[1])
    assert row['environmentInstId'] == str(INSTANCE)
    assert row['ownerType'] == '3'
    assert row['buffUid'] is None


@pytest.mark.parametrize('case', ['missing_runtime', 'different_runtime', 'both_runtime_fields',
                                 'both_template_fields', 'missing_template_oneof'])
def test_refresh_rejects_missing_or_conflicting_oneof_evidence(tmp_path, case):
    packet = message()
    action = packet.client_data.op_list[0].trigger_action_op_data.action
    if case == 'missing_runtime':
        action.ClearField('refresh_buff_attr_modifier_value_action')
    elif case == 'different_runtime':
        action.damage_action.SetInParent()
    event = service_events(packet)[0]
    if case == 'both_runtime_fields':
        event['action']['damage_action'] = {}
    elif case == 'both_template_fields':
        event['template_id']['str_id'] = 'conflicting-template'
    elif case == 'missing_template_oneof':
        event['template_id'].pop('int_id')
        event['template_id_case'] = None
    _, text = trace_event(tmp_path, event)
    assert ' BUFF_ATTR_REFRESH ' not in text
    diagnostic = json.loads(text.split(' BUFF_ATTR_REFRESH_UNRESOLVED ', 1)[1])
    assert diagnostic['validationErrors']
    assert diagnostic['operationOwnerId'] == str(OP_OWNER)
    assert diagnostic['ownerId'] == str(OWNER)
    assert ' HP_V2 ' not in text


def test_unknown_action_type_is_not_inferred_from_refresh_runtime_presence(tmp_path):
    packet = message()
    packet.client_data.op_list[0].trigger_action_op_data.action.action_type = 999
    event = service_events(packet)[0]
    assert event['action']['action_type'] == 999
    assert event['action_runtime_case'] == 'refresh_buff_attr_modifier_value_action'
    _, text = trace_event(tmp_path, event)
    assert 'BUFF_ATTR_REFRESH' not in text


@pytest.mark.parametrize('value', [0.0, -0.0, -4.440000057220459, 0.12345679104328156,
                                 1.2345678901234567, 1.0e-100])
def test_dynamic_result_keeps_roundtrip_precision_and_raw_identity(tmp_path, value):
    packet = message(action_type=7)
    packet.client_data.op_list[0].trigger_action_op_data.action.modify_dynamic_blackboard_action.client_value = value
    event = service_events(packet)[0]
    _, text = trace_event(tmp_path, event)
    line = next(line for line in text.splitlines() if ' BB_MODIFY_DYNAMIC ' in line)
    restored = float(re.search(r'\bvalue=(\S+)', line).group(1))
    assert restored.hex() == value.hex()
    identity = json.loads(line.split(' identity=', 1)[1])
    assert identity['ownerId'] == str(OWNER)
    assert identity['operationOwnerId'] == str(OP_OWNER)
    assert identity['environmentInstId'] == str(INSTANCE)
    assert identity['templateIdCase'] == 'int_id'
    assert identity['templateIntId'] == 1984
    assert identity['actionType'] == 7
    assert identity['generation'] == 4
    assert identity['sessionId'] == 'synthetic-refresh-session'
    assert identity['clientTickMs'] == str(TICK)


@pytest.mark.parametrize('value', [True, False, float('inf'), float('-inf'), float('nan'),
                                 '0.5', 'NaN', 10**400])
def test_dynamic_result_rejects_boolean_nonfinite_and_non_numeric_values(tmp_path, value):
    packet = message(action_type=7)
    packet.client_data.op_list[0].trigger_action_op_data.action.modify_dynamic_blackboard_action.client_value = 0.5
    event = service_events(packet)[0]
    event['action']['modify_dynamic_blackboard_action']['client_value'] = value
    bridge, text = trace_event(tmp_path, event)
    assert ' BB_MODIFY_DYNAMIC ' not in text
    assert bridge.recent_dynamic_bb_by_inst == {}


def test_dynamic_result_rejects_conflicting_runtime_oneof(tmp_path):
    packet = message(action_type=7)
    packet.client_data.op_list[0].trigger_action_op_data.action.modify_dynamic_blackboard_action.client_value = 0.5
    event = service_events(packet)[0]
    event['action']['damage_action'] = {}
    bridge, text = trace_event(tmp_path, event)
    assert ' BB_MODIFY_DYNAMIC ' not in text
    assert bridge.recent_dynamic_bb_by_inst == {}
