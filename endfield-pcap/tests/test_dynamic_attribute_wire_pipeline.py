"""Synthetic protobuf-to-parser evidence; HP lines only retain a battle window."""
import json

from endfield_pcap.proto_generated.CS_BATTLE_OP_pb2 import CS_BATTLE_OP
from endfield_pcap.service import SessionPipeline
from endfield_pcap.trace_bridge import TraceBridge
from parser_core import battle_log_parser as parser


UID = 9223372036854775901
TARGET = 9223372036854775902
SOURCE = 9223372036854775903
ATTACKER = 5001
BUFF = "buff_common_natural_natural_corrupt_do"
SESSION = "synthetic-dynamic-wire-session"


def packet(kind, sequence, *, conflicting_assigned=False):
    message = CS_BATTLE_OP()
    op = message.client_data.op_list.add()
    op.owner_id = SOURCE if kind == "create" else TARGET
    op.generation = 91 if kind == "create" else 7
    op.seq_id = sequence
    op.client_tick_tms = sequence * 100
    if kind == "end":
        op.op_type = 2
        op.finish_buff_op_data.buff_inst_id = UID
        return message
    op.op_type = 8
    trigger = op.trigger_action_op_data
    trigger.owner_id = op.owner_id
    trigger.owner_type = 1
    trigger.inst_id = 1010 if kind == "create" else UID
    trigger.trigger_type = 0
    trigger.template_id.type = 1
    trigger.template_id.int_id = 331 if kind == "create" else 1984
    action = trigger.action
    if kind == "create":
        action.action_type = 2
        action.action_id = 7
        detail = action.create_buff_action.details.add()
        detail.source_id = SOURCE
        detail.target_id = TARGET
        detail.buff_inst_id = UID
        detail.buff_num_id = 1984
        detail.buff_id = BUFF
        values = [("def_decrease", -10.0), ("additional_def_decrease", -5.0)]
        if conflicting_assigned:
            values.append(("additional_def_decrease", -6.0))
        for key, value in values:
            assigned = detail.assigned_items.add()
            assigned.target_key = key
            assigned.use_direct_value = True
            assigned.numeric_value = value
    elif kind == "update":
        action.action_type = 7
        action.action_id = 5
        action.modify_dynamic_blackboard_action.client_value = -12.0
    else:
        assert kind == "refresh"
        action.action_type = 31
        action.action_id = 10
        action.refresh_buff_attr_modifier_value_action.SetInParent()
    return message


def capture(tmp_path):
    bridge = TraceBridge(tmp_path / "dynamic-wire.log")
    # Explicit test actor identities; no roster or name inference.
    bridge.id_to_name.update({SOURCE: "chr_0025_ardelia", TARGET: "eny_0051_rodin", ATTACKER: "chr_0016_laevat"})
    pipeline = SessionPipeline.__new__(SessionPipeline)
    pipeline.session_id = SESSION
    pipeline.skill_levels_by_battle_inst = {}
    return bridge, pipeline


def emit(bridge, pipeline, message, timestamp):
    decoded = CS_BATTLE_OP.FromString(message.SerializeToString())
    events = []
    pipeline._emit_event = events.append
    pipeline._emit_battle_events(decoded, timestamp)
    rows = [event.as_dict() for event in events]
    for row in rows:
        bridge.handle_event(row)
    bridge._flush_pending_add_buffs()
    bridge._flush_pending_create_buff_triggers()
    bridge._flush_pending_buff_starts()
    return rows


def synthetic_hit(bridge, timestamp, sequence):
    # Deliberately synthetic HP text, not a claim to decode a DamageAction.
    bridge._line(timestamp, f'HP_V2 #{sequence} hit=120 cum=120 raw=120 packetFinalValue=120 '
                 f'packetSeq={sequence} packetSessionId="{SESSION}" packetOwnerId={ATTACKER} packetGeneration=12 '
                 f'pHP=100 eHP=1000 skill="fixture" hits=1 src=chr_0016_laevat tgt=eny_0051_rodin '
                 f'atk=chr_0016_laevat atkId={ATTACKER} tgtId={TARGET}')


def parse(bridge):
    text = bridge.trace_file.read_text(encoding="utf-8")
    return text, parser.parse_raw_battle_log_text(text, include_rdps_debug=True)


def test_wire_skill_setting_exports_all_computed_rows_and_exact_identity(tmp_path):
    bridge, pipeline = capture(tmp_path)
    message = packet("refresh", 7)
    trigger = message.client_data.op_list[0].trigger_action_op_data
    trigger.template_id.int_id = 1067
    action = trigger.action
    action.Clear()
    action.action_type = 35
    action.action_id = 3
    for layers, value, enhancement, actor in [(3, 0.4, 70.8, SOURCE), (0, 0.0, 0.0, 0)]:
        detail = action.read_skill_setting_to_bb_action.runtime_details.add()
        detail.client_layer_cnt = layers
        detail.client_value = value
        detail.enhance_attr_value = enhancement
        detail.attacker_battle_inst_id = actor
    decoded = emit(bridge, pipeline, message, 1000)[0]
    line = next(line for line in bridge.trace_file.read_text(encoding="utf-8").splitlines()
                if " BB_READ_SKILL_SETTING " in line)
    observed = json.loads(line.split(" BB_READ_SKILL_SETTING ", 1)[1])
    assert observed["runtime"] == decoded["action"]["read_skill_setting_to_bb_action"]
    assert len(observed["runtime"]["runtime_details"]) == 2
    assert observed["runtime"]["runtime_details"][0]["attacker_battle_inst_id"] == str(SOURCE)
    assert observed["runtime"]["runtime_details"][1] == {
        "client_layer_cnt": 0, "client_value": 0.0, "enhance_attr_value": 0.0,
        "attacker_battle_inst_id": "0",
    }
    assert (observed["operationOwnerId"], observed["ownerId"], observed["buffUid"]) == (
        str(TARGET), str(TARGET), str(UID))
    assert (observed["sessionId"], observed["operationSeq"], observed["generation"],
            observed["clientTickMs"], observed["templateIntId"], observed["actionId"],
            observed["actionType"]) == (SESSION, 7, 7, "700", 1067, 3, 35)
    assert "validationErrors" not in observed


def test_wire_skill_setting_does_not_relabel_skill_environment_as_buff(tmp_path):
    bridge, pipeline = capture(tmp_path)
    message = packet("refresh", 8)
    trigger = message.client_data.op_list[0].trigger_action_op_data
    trigger.owner_type = 0
    trigger.template_id.type = 0
    trigger.template_id.str_id = "explicit_skill_fixture"
    trigger.action.Clear()
    trigger.action.action_type = 35
    trigger.action.read_skill_setting_to_bb_action.SetInParent()
    emit(bridge, pipeline, message, 1000)
    line = next(line for line in bridge.trace_file.read_text(encoding="utf-8").splitlines()
                if " BB_READ_SKILL_SETTING " in line)
    observed = json.loads(line.split(" BB_READ_SKILL_SETTING ", 1)[1])
    assert observed["environmentInstId"] == str(UID)
    assert observed["buffUid"] is None
    assert observed["templateStrId"] == "explicit_skill_fixture"
    assert observed["runtime"]["runtime_details"] == []


def test_skill_setting_export_keeps_unverified_runtime_presence_explicit(tmp_path):
    bridge, pipeline = capture(tmp_path)
    message = packet("refresh", 9)
    trigger = message.client_data.op_list[0].trigger_action_op_data
    trigger.action.Clear()
    trigger.action.action_type = 35
    trigger.action.read_skill_setting_to_bb_action.runtime_details.add().client_value = 0.25
    events = []
    pipeline._emit_event = events.append
    pipeline._emit_battle_events(CS_BATTLE_OP.FromString(message.SerializeToString()), 1000)
    event = events[0].as_dict()
    event.pop("action_runtime_case")
    bridge.handle_event(event)
    line = next(line for line in bridge.trace_file.read_text(encoding="utf-8").splitlines()
                if " BB_READ_SKILL_SETTING_UNRESOLVED " in line)
    observed = json.loads(line.split(" BB_READ_SKILL_SETTING_UNRESOLVED ", 1)[1])
    assert "skill_setting_runtime_presence_unverified" in observed["validationErrors"]
    assert observed["runtime"] == event["action"]["read_skill_setting_to_bb_action"]
    assert observed["actionRuntimeCase"] is None


def test_wire_distinguishes_absent_report_from_present_empty_instant_modifiers(tmp_path):
    for present in (False, True):
        bridge, pipeline = capture(tmp_path / str(present))
        message = CS_BATTLE_OP()
        op = message.client_data.op_list.add(op_type=8, seq_id=10, owner_id=ATTACKER, generation=1, client_tick_tms=1000)
        trigger = op.trigger_action_op_data
        trigger.owner_id = ATTACKER
        trigger.owner_type = 3
        trigger.inst_id = 10001
        trigger.template_id.type = 0
        trigger.template_id.int_id = 4807
        trigger.action.action_type = 1
        trigger.action.action_id = 2
        damage = trigger.action.damage_action
        damage.attacker_id = ATTACKER
        damage.original_source_template_id.type = 0
        damage.original_source_template_id.int_id = 2959
        detail = damage.details.add(target_id=TARGET, damage_unit_index=0, value=130, cur_hp=870)
        if present:
            detail.battle_report_info.SetInParent()
        rows = emit(bridge, pipeline, message, 1000)
        assert rows[0]['damage_detail_presence'][0]['has_battle_report_info'] is present
        text, result = parse(bridge)
        assert ' PKT_ATTR_INFO ' in text
        assert result['debug_hits'][0]['packet_attribute_report_present'] is present


def test_wire_create_update_refresh_preserves_parameter_provenance_without_rdps(tmp_path):
    bridge, pipeline = capture(tmp_path)
    created = emit(bridge, pipeline, packet("create", 1), 1000)
    updated = emit(bridge, pipeline, packet("update", 2), 1500)
    refreshed = emit(bridge, pipeline, packet("refresh", 3), 2000)
    synthetic_hit(bridge, 3000, 100)
    text, result = parse(bridge)
    assert created[0]["action_runtime_case"] == "create_buff_action"
    assert updated[0]["action_runtime_case"] == "modify_dynamic_blackboard_action"
    assert refreshed[0]["action_runtime_case"] == "refresh_buff_attr_modifier_value_action"
    assert refreshed[0]["action"]["refresh_buff_attr_modifier_value_action"] == {}
    assert text.index(" BUFF_START ") < text.index(" BUFF_CREATE_OBSERVED ") < text.index(" BB_MODIFY_DYNAMIC ") < text.index(" BUFF_ATTR_REFRESH ")
    # The Buff's duration is unknown, so it need not overlap the final HP
    # window. The complete input observation lives on the refresh event.
    anchor = json.loads(next(line.split(" BUFF_CREATE_OBSERVED ", 1)[1]
                             for line in text.splitlines() if " BUFF_CREATE_OBSERVED " in line))
    assert anchor["buffNumId"] == 1984 and anchor["buffId"] == BUFF
    assert anchor["operationOwnerId"] == str(SOURCE) and anchor["operationGeneration"] == 91
    assert len(anchor["assignedItems"]) == 2
    assert {row["target_key"]: row["numeric_value"] for row in anchor["assignedItems"]} == {
        "def_decrease": -10.0, "additional_def_decrease": -5.0,
    }
    assert all(row["use_direct_value"] is True for row in anchor["assignedItems"])
    origin = json.loads(next(line.split(" BUFF_ORIGIN ", 1)[1]
                             for line in text.splitlines() if " BUFF_ORIGIN " in line))
    assert origin["templateIntId"] == 331 and origin["actionId"] == 7
    assert (origin["sourceActorId"], origin["targetActorId"]) == (str(SOURCE), str(TARGET))
    observed = [row["snapshot"] for row in result["buff_attribute_refresh_events"] if "snapshot" in row]
    assert len(observed) == 1
    snapshot = observed[0]
    assert snapshot["operation_seq"] == 3
    assert snapshot["bb_values"] == {"def_decrease": -12.0, "additional_def_decrease": -5.0}
    assert snapshot["status"] == "attribute_parameters_observed"
    assert snapshot["missing_parameters"] == snapshot["unverified_parameters"] == []
    assert snapshot["enhance_multiplier"] is None and snapshot["final_modifiers_verified"] is False
    assert snapshot["instance_start_line_no"] == next(i for i, line in enumerate(text.splitlines(), 1)
                                                     if " BUFF_START " in line)
    assert snapshot["ts_ms"] == result["dynamic_bb_events"][0]["ts_ms"] + 500
    assert snapshot["line_no"] > result["dynamic_bb_events"][0]["line_no"]
    assert (snapshot["identity"]["sessionId"], snapshot["identity"]["operationOwnerId"], snapshot["identity"]["generation"]) == (SESSION, str(TARGET), 7)
    assert snapshot["identity"]["environmentInstId"] == str(UID)
    assert {row["bb_key"]: row["operand_source"]["kind"] for row in snapshot["attribute_parameters"]} == {
        "def_decrease": "typed_runtime_post_assignment", "additional_def_decrease": "typed_post_create_runtime_bb",
    }
    assert len(snapshot["attribute_parameters"]) == 10
    for record in result["buff_events"]:
        if record["uid"] == str(UID):
            assert record["attribute_refresh_snapshots"] == [snapshot]
    assert result["debug_hits"][0]["rdps_contributions"] == [
        {"character_key": "chr_0016_laevat", "character_name": "莱万汀", "value": 120.0},
    ]


def test_wire_conflicting_assigned_key_is_not_resolved_by_last_value(tmp_path):
    bridge, pipeline = capture(tmp_path)
    emit(bridge, pipeline, packet("create", 1, conflicting_assigned=True), 1000)
    emit(bridge, pipeline, packet("update", 2), 1500)
    emit(bridge, pipeline, packet("refresh", 3), 2000)
    synthetic_hit(bridge, 3000, 100)
    text, result = parse(bridge)
    anchor = json.loads(next(line.split(" BUFF_CREATE_OBSERVED ", 1)[1]
                             for line in text.splitlines() if " BUFF_CREATE_OBSERVED " in line))
    assert [row["numeric_value"] for row in anchor["assignedItems"] if row["target_key"] == "additional_def_decrease"] == [-5.0, -6.0]
    observed = [row["snapshot"] for row in result["buff_attribute_refresh_events"] if "snapshot" in row]
    assert len(observed) == 1
    snapshot = observed[0]
    assert snapshot["status"] == "missing_attribute_parameters"
    assert snapshot["missing_parameters"] == ["additional_def_decrease"]
    assert "additional_def_decrease" not in snapshot["bb_values"]
    assert all(row["operand"] is None for row in snapshot["attribute_parameters"] if row["bb_key"] == "additional_def_decrease")
    assert result["debug_hits"][0]["rdps_contributions"] == [
        {"character_key": "chr_0016_laevat", "character_name": "莱万汀", "value": 120.0},
    ]


def test_wire_end_then_duplicate_old_create_does_not_revive_instance(tmp_path):
    bridge, pipeline = capture(tmp_path)
    original = packet("create", 1)
    emit(bridge, pipeline, original, 1000)
    emit(bridge, pipeline, packet("update", 2), 1500)
    emit(bridge, pipeline, packet("refresh", 3), 2000)
    synthetic_hit(bridge, 2100, 50)
    emit(bridge, pipeline, packet("end", 4), 2200)
    # Retransmit the same protobuf operation after END; receive time changes
    # but its exact session/owner/generation/sequence and payload do not.
    emit(bridge, pipeline, original, 2300)
    emit(bridge, pipeline, packet("refresh", 5), 2400)
    synthetic_hit(bridge, 3000, 100)
    _, result = parse(bridge)
    records = [row for row in result["buff_events"] if row["uid"] == str(UID)]
    assert len(records) == 1
    assert records[0]["raw_end_ts_ms"] is not None
    assert records[0]["raw_end_ts_ms"] == records[0]["start_ts_ms"] + 1200
    snapshots = records[0]["attribute_refresh_snapshots"]
    assert [row["operation_seq"] for row in snapshots] == [3]
    assert [row["snapshot"] for row in result["buff_attribute_refresh_events"] if "snapshot" in row] == snapshots
    late = next(row for row in result["buff_attribute_refresh_events"] if row["identity"]["operationSeq"] == 5)
    assert late.get("state_error") == "unbound_dynamic_buff_instance"
    assert "snapshot" not in late
