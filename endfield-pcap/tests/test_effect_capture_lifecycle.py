from collections import defaultdict
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from google.protobuf.json_format import MessageToDict

from endfield_pcap.message_registry import MessageRegistry
from endfield_pcap.models import BattleLogEvent, Endpoint, FlowKey, RuntimeMetrics
from endfield_pcap.proto_generated.BATTLE_ACTION_INST_pb2 import BATTLE_ACTION_INST
from endfield_pcap.service import DamageLogService, SessionPipeline
from endfield_pcap.trace_bridge import TraceBridge


DATA = Path(__file__).resolve().parents[1] / "data"
LARGE_UID = 9223372036854775901


def pipeline():
    events = []
    registry = MessageRegistry(DATA)
    value = SessionPipeline(
        flow=FlowKey(client=Endpoint("127.0.0.1", 50100), server=Endpoint("127.0.0.2", 30000)),
        session_id="effects-capture-test", registry=registry, private_key=None,
        srsa_bridge=None, name_index={}, multi_phase_dungeon_map={},
        merge_multi_phase_enemy_battles=False, on_event=events.append,
        on_debug_message=None, on_debug_record=None, metrics=RuntimeMetrics(),
    )
    return value, events, registry


def deliver(value, registry, class_name, message, timestamp=1000, *, direction="sc"):
    # Use the real generated protobuf serializer and registry decoder, rather
    # than substituting dicts for proto3 presence/default behavior.
    message_id = next(number for number, name in registry._maps[direction].items() if name == class_name)
    assert registry.should_decode_message(direction, message_id)
    decoded = registry.decode_messages(direction, {"msgid": message_id}, message.SerializeToString())
    assert len(decoded) == 1
    value._handle_message(decoded[0], timestamp)


def add_buff(battle):
    battle.battle_inst_id = 8001
    battle.msg_generation = 0
    buff = battle.buff_list.add(inst_id=LARGE_UID)
    buff.buff_id.str_id = "buff_exact_fixture"
    group = battle.stacking_group_list.add(stacking_key="fixture-key", stacking_type=0, max_stack_cnt=0)
    group.buff_inst_ids.append(LARGE_UID)


@pytest.mark.parametrize("source", ["SC_SELF_SCENE_INFO", "SC_OBJECT_ENTER_VIEW"])
@pytest.mark.parametrize("kind", ["char_list", "monster_list", "npc_list", "summon_list", "interactive_list"])
def test_scene_snapshots_cover_every_battle_owner_and_default_fields(source, kind):
    value, events, registry = pipeline()
    value.name_index = {"eny_fixture": "测试怪物", "chr_fixture": "测试角色"}
    message = registry.load_message_class(source)()
    actor = getattr(message.detail, kind).add()
    actor.common_info.id = LARGE_UID - 1
    actor.common_info.templateid = "eny_fixture" if kind == "monster_list" else "chr_fixture"
    # Noncombat interactives have no BATTLE_MGR_INFO and cannot provide an
    # invented target or effect list.
    if "battle_info" not in actor.DESCRIPTOR.fields_by_name:
        deliver(value, registry, source, message)
        assert events[-1].payload["owners"] == []
        return
    add_buff(actor.battle_info)
    deliver(value, registry, source, message)
    snapshot = events[-1].as_dict()
    assert snapshot["type"] == "SC_EFFECT_SNAPSHOT"
    assert snapshot["session_id"] == value.session_id
    owner = snapshot["owners"][0]
    assert owner["identity_scope"] == "server_buff"
    assert owner["owner_obj_id"] == str(LARGE_UID - 1)
    assert owner["owner_battle_inst_id"] == "8001"
    assert owner["generation"] == 0
    assert owner["owner_kind"] == kind.removesuffix("_list")
    assert owner["owner_display_name"] == value.name_index[owner["owner_templateid"]]
    assert owner["owner_entity_type"] == 0
    buff = owner["buffs"][0]
    assert buff["inst_id"] == str(LARGE_UID)
    assert buff["is_enable"] is False
    assert buff["is_server_init"] is False
    assert buff["duration"] == "0"
    assert buff["enhance_multi"] == 0
    assert "stackCount" not in buff and "sourceId" not in buff
    assert owner["stacking_groups"][0]["buff_inst_ids"] == [str(LARGE_UID)]


def test_bag_initial_effects_and_explicit_empty_owner_snapshot_are_preserved():
    value, events, registry = pipeline()
    message = registry.load_message_class("SC_SYNC_CHAR_BAG_INFO")()
    char = message.char_info.add(objid=71, templateid="chr_fixture")
    add_buff(char.battle_mgr_info)
    deliver(value, registry, "SC_SYNC_CHAR_BAG_INFO", message)
    assert events[-1].payload["owners"][0]["owner_obj_id"] == "71"
    message.ClearField("char_info")
    char = message.char_info.add(objid=71, templateid="chr_fixture")
    char.battle_mgr_info.battle_inst_id = 8001
    deliver(value, registry, "SC_SYNC_CHAR_BAG_INFO", message)
    assert events[-1].payload["owners"][0]["buffs"] == []


def test_snapshot_does_not_discard_observed_buff_when_target_identity_is_missing():
    value, events, registry = pipeline()
    message = registry.load_message_class("SC_OBJECT_ENTER_VIEW")()
    monster = message.detail.monster_list.add()
    monster.common_info.id = 71
    monster.battle_info.buff_list.add(inst_id=31).buff_id.str_id = "buff_exact_fixture"
    deliver(value, registry, "SC_OBJECT_ENTER_VIEW", message)
    owner = events[-1].payload["owners"][0]
    assert owner["owner_obj_id"] == "71"
    assert owner["owner_battle_inst_id"] is None
    assert owner["buffs"][0]["inst_id"] == "31"


@pytest.mark.parametrize("source", ["SC_RESET_BATTLE_STATUS", "SC_SCENE_RESET_ENTITY"])
def test_reset_payload_precedes_snapshot_without_guessing_object_namespace(source):
    value, events, registry = pipeline()
    message = registry.load_message_class(source)()
    add_buff(message.battle_info)
    deliver(value, registry, source, message)
    assert [event.event_type for event in events] == [source, "SC_EFFECT_SNAPSHOT"]
    assert events[-1].payload["owners"][0]["owner_obj_id"] is None
    assert events[-1].payload["owners"][0]["owner_battle_inst_id"] == "8001"


def test_server_delta_preserves_disabled_zero_duration_and_owner_generation():
    value, events, registry = pipeline()
    message = registry.load_message_class("SC_ADD_SERVER_BUFF")(inst_id=LARGE_UID)
    message.buffs.add(inst_id=31).buff_id.int_id = 0
    deliver(value, registry, "SC_ADD_SERVER_BUFF", message)
    payload = events[-1].payload
    assert payload["inst_id"] == str(LARGE_UID)
    assert payload["generation"] == 0
    assert payload["buffs"][0]["duration"] == "0"
    assert payload["buffs"][0]["is_enable"] is False
    message = registry.load_message_class("SC_REMOVE_SERVER_BUFF")(inst_id=LARGE_UID, del_inst_ids=[31])
    deliver(value, registry, "SC_REMOVE_SERVER_BUFF", message)
    assert events[-1].payload["del_inst_ids"] == ["31"]
    assert events[-1].payload["generation"] == 0


@pytest.mark.parametrize("source", [
    "SC_CHAR_BAG_SYNC_PERSISTENT_BUFFS", "SC_SYNC_RACING_DUNGEON_BUFF_MODIFY",
    "SC_SYNC_RACING_DUNGEON_RECONNECT", "SC_BATTLE_ADD_GLOBAL_BUFF",
    "SC_BATTLE_REMOVE_GLOBAL_BUFF", "SC_BATTLE_SYNC_GLOBAL_BUFF_INFO",
])
def test_all_effect_message_families_are_decoded_and_exposed_without_namespace_mapping(source):
    value, events, registry = pipeline()
    message = registry.load_message_class(source)()
    deliver(value, registry, source, message)
    assert events[-1].event_type == source
    assert "source_id" not in events[-1].payload
    assert "stack_count" not in events[-1].payload


def test_object_leave_retains_exact_known_target_and_unknown_object():
    value, events, registry = pipeline()
    entered = registry.load_message_class("SC_OBJECT_ENTER_VIEW")()
    monster = entered.detail.monster_list.add()
    monster.common_info.id = LARGE_UID
    monster.common_info.templateid = "eny_fixture"
    monster.battle_info.battle_inst_id = 8001
    deliver(value, registry, "SC_OBJECT_ENTER_VIEW", entered)
    left = registry.load_message_class("SC_OBJECT_LEAVE_VIEW")()
    left.obj_list.add(obj_id=LARGE_UID, obj_type=0)
    left.obj_list.add(obj_id=99, obj_type=0)
    deliver(value, registry, "SC_OBJECT_LEAVE_VIEW", left)
    rows = events[-1].payload["objects"]
    assert rows[0]["obj_id"] == str(LARGE_UID)
    assert rows[0]["battle_inst_id"] == "8001"
    assert rows[0]["templateid"] == "eny_fixture"
    assert rows[1]["battle_inst_id"] is None
    assert 8001 not in value.entity_index and LARGE_UID not in value.obj_to_battle


def test_client_finish_raw_fields_keep_reduce_layer_distinct_from_actual_remove():
    value, events, registry = pipeline()
    message = registry.load_message_class("CS_BATTLE_OP")()
    op = message.client_data.op_list.add(owner_id=LARGE_UID, generation=0)
    op.op_type = op.DESCRIPTOR.fields_by_name["op_type"].enum_type.values_by_name["BattleOpFinishBuff"].number
    op.finish_buff_op_data.buff_inst_id = LARGE_UID - 1
    op.finish_buff_op_data.reduce_layer = 2
    op.finish_buff_op_data.force = False
    value._emit_battle_events(message, 1000)
    payload = events[-1].payload
    assert payload["operation_owner_id"] == str(LARGE_UID)
    assert payload["generation"] == 0
    assert payload["reduce_layer"] == 2
    assert payload["force"] is False
    assert payload["raw_data"]["buff_inst_id"] == str(LARGE_UID - 1)
    assert "removed" not in payload


def test_client_add_keeps_source_data_and_uninterpreted_client_time():
    value, events, registry = pipeline()
    message = registry.load_message_class("CS_BATTLE_OP")()
    op = message.client_data.op_list.add(owner_id=71, generation=0)
    op.op_type = op.DESCRIPTOR.fields_by_name["op_type"].enum_type.values_by_name["BattleOpAddBuff"].number
    data = op.add_buff_op_data
    data.buff_inst_id = LARGE_UID
    data.buff_id.str_id = "buff_exact_fixture"
    data.src_inst_id = 91
    data.target_inst_id = 92
    data.client_add_ts = 0
    data.buff_src_type = 0
    data.buff_src_args = "typed-source"
    value._emit_battle_events(message, 1000)
    payload = events[-1].payload
    assert payload["src_inst_id"] == 91 and payload["target_inst_id"] == 92
    assert payload["raw_data"]["client_add_ts"] == "0"
    assert payload["raw_data"]["buff_src_type"] == 0
    assert payload["raw_data"]["buff_src_args"] == "typed-source"
    assert "duration_ms" not in payload


@pytest.mark.parametrize("source", ["SC_ATTACH_SERVER_SKILL", "SC_UPDATE_SERVER_SKILL"])
def test_server_skill_roundtrip_retains_typed_blackboard_and_zero_properties(source):
    value, events, registry = pipeline()
    message = registry.load_message_class(source)(inst_id=LARGE_UID, generation=0)
    skill = message.skills.add(inst_id=LARGE_UID - 1, level=0, potential_lv=0)
    skill.skill_id.int_id = 0
    skill.talent_node_ids.extend(["", "exact-node"])
    skill.blackboard.blackboard["literal"].str_value = "0.75"
    skill.blackboard.blackboard["literal"].float_value = 0.0
    skill.blackboard.blackboard["literal"].is_dynamic = False
    skill.blackboard.blackboard["dynamic"].float_value = 1.25
    skill.blackboard.blackboard["dynamic"].is_dynamic = True
    expected = MessageToDict(skill, preserving_proto_field_name=True,
                             always_print_fields_with_no_presence=True)
    deliver(value, registry, source, message)
    row = events[-1].payload["skills"][0]
    assert events[-1].event_type == source and events[-1].payload["generation"] == 0
    assert row["level"] == 0 and row["potential_lv"] == 0
    assert row["is_enable"] is False and row["is_passive"] is False
    assert row["talent_node_ids"] == ["", "exact-node"]
    assert row["raw_data"] == expected
    assert row["raw_data"]["inst_id"] == str(LARGE_UID - 1)
    assert row["skill_id"]["int_id"] == 0
    assert row["blackboard"] == expected["blackboard"]
    assert row["blackboard"]["blackboard"]["literal"] == {
        "str_value": "0.75", "float_value": 0.0, "is_dynamic": False}
    assert value.server_skill_state_by_owner_inst[LARGE_UID][LARGE_UID - 1]["raw_data"] == expected
    skill.blackboard.blackboard["literal"].str_value = "later"
    assert row["blackboard"]["blackboard"]["literal"]["str_value"] == "0.75"


def test_server_skill_absent_blackboard_and_detach_generation_are_not_invented():
    value, events, registry = pipeline()
    attached = registry.load_message_class("SC_ATTACH_SERVER_SKILL")(inst_id=71)
    attached.skills.add(inst_id=LARGE_UID)
    deliver(value, registry, "SC_ATTACH_SERVER_SKILL", attached)
    row = events[-1].payload["skills"][0]
    assert row["blackboard"] is None and "blackboard" not in row["raw_data"]
    assert row["talent_node_ids"] == []
    detached = registry.load_message_class("SC_DETACH_SERVER_SKILL")(inst_id=71, del_inst_ids=[LARGE_UID])
    deliver(value, registry, "SC_DETACH_SERVER_SKILL", detached)
    assert events[-1].payload["generation"] == 0
    assert LARGE_UID not in value.server_skill_state_by_owner_inst.get(71, {})


@pytest.mark.parametrize("source", [
    "SC_CHAR_BAG_SET_CURR_TEAM_INDEX", "SC_CHAR_BAG_SET_CURR_TEAM_TYPE",
    "SC_CHAR_BAG_SET_MAX_TEAM_MEMBER_COUNT", "SC_CHAR_SKILL_INFOS",
    "SC_EQUIP_MEDICINE_MODIFY", "SC_EQUIP_PRODUCE",
    "SC_SYNC_CHAR_BAG_INFO", "SC_CHAR_BAG_SET_TEAM", "SC_CHAR_BAG_SET_TEAM_LEADER",
])
def test_newly_retained_messages_keep_all_typed_fields(source):
    value, events, registry = pipeline()
    message = registry.load_message_class(source)()
    if source == "SC_CHAR_SKILL_INFOS":
        message.char_obj_id = LARGE_UID
        message.level_infos.add(skill_id="skill-exact", skill_level=0, skill_max_level=0, skill_enhanced_level=0)
    elif source == "SC_EQUIP_MEDICINE_MODIFY":
        message.char_id = LARGE_UID
    elif source == "SC_EQUIP_PRODUCE":
        message.equip_instid = LARGE_UID
    expected = MessageToDict(message, preserving_proto_field_name=True,
                             always_print_fields_with_no_presence=True)
    deliver(value, registry, source, message)
    assert events[0].event_type == source and events[0].payload == expected
    assert value.char_bag_by_objid == {} and value.equip_by_inst_id == {}
    assert value.current_team_char_ids == [] and not value.current_team_selection_known


def test_team_selectors_use_observed_scope_and_index_without_first_team_fallback():
    value, _, registry = pipeline()
    snapshot = registry.load_message_class("SC_SYNC_CHAR_BAG_INFO")(scope_name=3, curr_team_index=0)
    snapshot.team_info.add().char_team.extend([71])
    snapshot.team_info.add().char_team.extend([92])
    deliver(value, registry, "SC_SYNC_CHAR_BAG_INFO", snapshot)
    assert value.current_team_char_ids == [71] and value.current_team_selection_known
    change = registry.load_message_class("SC_CHAR_BAG_SET_TEAM")(
        scope_name=3, team_type=0, team_index=1, char_team=[93])
    deliver(value, registry, "SC_CHAR_BAG_SET_TEAM", change)
    assert value.current_team_char_ids == [71]
    select = registry.load_message_class("SC_CHAR_BAG_SET_CURR_TEAM_INDEX")(scope_name=3, curr_team_index=1)
    deliver(value, registry, "SC_CHAR_BAG_SET_CURR_TEAM_INDEX", select)
    assert value.current_team_char_ids == [93]
    select.scope_name = 4
    select.curr_team_index = 0
    deliver(value, registry, "SC_CHAR_BAG_SET_CURR_TEAM_INDEX", select)
    assert value.current_team_char_ids == [93] and value.current_team_scope_name == 3
    select.scope_name = 3
    select.curr_team_index = 99
    deliver(value, registry, "SC_CHAR_BAG_SET_CURR_TEAM_INDEX", select)
    assert value.current_team_char_ids == [] and not value.current_team_selection_known
    # A selector can resolve again when the actual selected team's data arrives.
    change.team_index = 99
    change.char_team[:] = [94]
    deliver(value, registry, "SC_CHAR_BAG_SET_TEAM", change)
    assert value.current_team_char_ids == [94] and value.current_team_selection_known


def test_temp_team_and_empty_team_have_explicit_knownness():
    value, _, registry = pipeline()
    snapshot = registry.load_message_class("SC_SYNC_CHAR_BAG_INFO")(scope_name=0)
    snapshot.team_info.add().char_team.extend([71])
    deliver(value, registry, "SC_SYNC_CHAR_BAG_INFO", snapshot)
    select = registry.load_message_class("SC_CHAR_BAG_SET_CURR_TEAM_TYPE")(team_type=1)
    deliver(value, registry, "SC_CHAR_BAG_SET_CURR_TEAM_TYPE", select)
    assert value.current_team_char_ids == [] and not value.current_team_selection_known
    snapshot.team_type = 1
    snapshot.temp_team_info.SetInParent()
    deliver(value, registry, "SC_SYNC_CHAR_BAG_INFO", snapshot)
    assert value.current_team_char_ids == [] and value.current_team_selection_known
    snapshot.temp_team_info.char_team.append(92)
    deliver(value, registry, "SC_SYNC_CHAR_BAG_INFO", snapshot)
    assert value.current_team_char_ids == [92]
    deleted = registry.load_message_class("SC_SYNC_CHAR_BAG_INFO")(is_scope_delete=True)
    deliver(value, registry, "SC_SYNC_CHAR_BAG_INFO", deleted)
    assert value.current_team_char_ids == [] and not value.current_team_selection_known


def test_skill_and_medicine_deltas_update_only_explicit_known_character():
    value, events, registry = pipeline()
    snapshot = registry.load_message_class("SC_SYNC_CHAR_BAG_INFO")()
    snapshot.char_info.add(objid=LARGE_UID, templateid="chr_fixture", equip_medicine_id=5)
    snapshot.team_info.add().char_team.append(LARGE_UID)
    deliver(value, registry, "SC_SYNC_CHAR_BAG_INFO", snapshot)
    value.obj_to_battle[LARGE_UID] = 8001
    value.skill_levels_by_battle_inst[8001] = {"skill:str:unchanged": 7}
    message = registry.load_message_class("SC_CHAR_SKILL_INFOS")(char_obj_id=LARGE_UID)
    message.level_infos.add(skill_id="skill-exact", skill_level=0, skill_max_level=9, skill_enhanced_level=0)
    deliver(value, registry, "SC_CHAR_SKILL_INFOS", message)
    assert value.skill_levels_by_battle_inst[8001] == {"skill:str:unchanged": 7}
    assert value.global_skill_levels == {}
    assert value.char_bag_by_objid[LARGE_UID]["skill_level_infos"][0]["skill_max_level"] == 9
    reported = next(event.payload for event in reversed(events) if event.event_type == "SC_CHAR_SKILL_INFOS")
    assert reported["level_infos"][0] == {
        "skill_id": "skill-exact", "skill_level": 0, "skill_max_level": 9, "skill_enhanced_level": 0}
    medicine = registry.load_message_class("SC_EQUIP_MEDICINE_MODIFY")(char_id=LARGE_UID, equip_medicine_id=0)
    deliver(value, registry, "SC_EQUIP_MEDICINE_MODIFY", medicine)
    assert value.char_bag_by_objid[LARGE_UID]["equip_medicine_id"] == 0
    medicine.char_id = 999
    deliver(value, registry, "SC_EQUIP_MEDICINE_MODIFY", medicine)
    assert 999 not in value.char_bag_by_objid
    loadout = next(event.payload for event in reversed(events) if event.event_type == "LOADOUT")
    assert loadout["rows"][0]["equip_medicine_id"] == 0
    assert loadout["rows"][0]["reported_skill_level_infos"][0]["skill_level"] == 0


@pytest.mark.parametrize("op_name,case", [
    ("BattleOpSwitchMode", "skill_switch_mode_op_data"),
    ("BattleOpResetBattleData", "reset_battle_op_data"),
    ("BattleOpUpdateDataVersion", "update_data_version_op_data"),
    ("BattleOpShieldValueModify", "shield_value_modify_op_data"),
])
def test_additional_operation_roundtrip_keeps_raw_body_outer_identity_and_zero(op_name, case):
    value, events, registry = pipeline()
    value.rotation_clock_trace = False
    observed = []
    value.on_battle_operation = observed.append
    message = registry.load_message_class("CS_BATTLE_OP")()
    op = message.client_data.op_list.add(owner_id=LARGE_UID, generation=0, seq_id=0,
                                        client_tick_tms=9223372036854775807)
    op.op_type = op.DESCRIPTOR.fields_by_name["op_type"].enum_type.values_by_name[op_name].number
    data = getattr(op, case)
    data.SetInParent()
    if op_name == "BattleOpShieldValueModify":
        data.buff_inst_id = LARGE_UID - 1
    expected = MessageToDict(data, preserving_proto_field_name=True,
                             always_print_fields_with_no_presence=True)
    deliver(value, registry, "CS_BATTLE_OP", message, direction="cs")
    assert len(events) == 1 and events[0].event_type == op_name
    payload = events[0].payload
    assert payload["raw_data"] == expected
    assert all(payload[key] == val for key, val in expected.items())
    assert payload["seq_id"] == 0 and payload["generation"] == 0
    assert payload["owner_id"] == payload["operation_owner_id"] == str(LARGE_UID)
    assert payload["client_tick_tms"] == "9223372036854775807"
    assert payload["operation_payload_case"] == case
    assert observed[0]["operation_type"] == op_name and observed[0]["source_index"] == 1
    assert value.entity_index == {} and value.server_skill_state_by_owner_inst == {}


def test_mismatched_operation_oneof_is_not_labeled_as_a_complete_typed_body():
    value, events, registry = pipeline()
    message = registry.load_message_class("CS_BATTLE_OP")()
    op = message.client_data.op_list.add(seq_id=17)
    op.op_type = 16  # BattleOpShieldValueModify
    op.reset_battle_op_data.SetInParent()
    deliver(value, registry, "CS_BATTLE_OP", message, direction="cs")
    assert events == []


@pytest.mark.parametrize("case", [field.name for field in BATTLE_ACTION_INST.DESCRIPTOR.oneofs_by_name["inst_impl"].fields])
def test_every_declared_action_runtime_survives_real_protobuf_roundtrip(case):
    value, events, registry = pipeline()
    message = registry.load_message_class("CS_BATTLE_OP")()
    op = message.client_data.op_list.add(seq_id=7)
    op.op_type = 8  # BattleOpTriggerAction
    action = op.trigger_action_op_data.action
    runtime = getattr(action, case)
    runtime.SetInParent()
    if case == "create_buff_action":
        detail = runtime.details.add(buff_inst_id=LARGE_UID, buff_input_index=0)
        detail.assigned_items.add(target_key="literal", use_direct_value=False,
                                  numeric_value=0.0, string_value="0.75")
    elif case == "launch_projectile_action":
        runtime.source_id = LARGE_UID
    elif case == "read_skill_setting_to_bb_action":
        runtime.runtime_details.add(client_layer_cnt=0, client_value=0.0, enhance_attr_value=0.0,
                                    attacker_battle_inst_id=LARGE_UID)
    expected = MessageToDict(action, preserving_proto_field_name=True,
                             always_print_fields_with_no_presence=True)
    deliver(value, registry, "CS_BATTLE_OP", message, direction="cs")
    payload = events[-1].payload
    assert payload["action_runtime_case"] == case
    assert payload["action"] == expected and payload["action"]["action_id"] == 0
    if case == "create_buff_action":
        detail = payload["action"][case]["details"][0]
        assert detail["buff_inst_id"] == str(LARGE_UID)
        assert detail["assigned_items"][0]["use_direct_value"] is False
        assert detail["assigned_items"][0]["numeric_value"] == 0.0


def test_new_service_evidence_reaches_log_and_observer_without_touching_legacy_trace(tmp_path):
    bridge = TraceBridge(tmp_path / "unchanged.log")
    bridge._last_ts_ms = 1000
    bridge._pending_add_buff_ts_ms = 1000
    pending = {"fixture": "pending"}
    bridge._pending_add_buff_events.append(pending)
    observed = []
    service = DamageLogService.__new__(DamageLogService)
    service.trace_bridge = bridge
    service.batch = []
    service.observer = SimpleNamespace(on_event=observed.append)
    service.log_file = io.StringIO()
    service.loop = None
    kinds = ["SC_CHAR_BAG_SET_CURR_TEAM_INDEX", "SC_CHAR_BAG_SET_CURR_TEAM_TYPE",
             "SC_CHAR_BAG_SET_MAX_TEAM_MEMBER_COUNT", "SC_CHAR_SKILL_INFOS", "SC_EQUIP_MEDICINE_MODIFY",
             "SC_EQUIP_PRODUCE", "BattleOpSwitchMode", "BattleOpResetBattleData",
             "BattleOpUpdateDataVersion", "BattleOpShieldValueModify", "SC_SYNC_CHAR_BAG_INFO",
             "SC_CHAR_BAG_SET_TEAM", "SC_CHAR_BAG_SET_TEAM_LEADER", "LOADOUT"]
    for kind in kinds:
        service._handle_outbound_event(BattleLogEvent(
            session_id="typed-observation", timestamp_ms=9000, event_type=kind,
            payload={"reason": "SC_CHAR_SKILL_INFOS"} if kind == "LOADOUT" else {}))
    assert len(observed) == len(service.batch) == len(kinds)
    assert [json.loads(line)["type"] for line in service.log_file.getvalue().splitlines()] == kinds
    assert bridge._last_ts_ms == 1000 and bridge._pending_add_buff_events == [pending]
    assert bridge.trace_file.read_text(encoding="utf-8") == ""
    # The pre-existing roster LOADOUT route still reaches the old bridge.
    routed = []
    service.trace_bridge = SimpleNamespace(handle_event=routed.append)
    service._handle_outbound_event(BattleLogEvent(
        session_id="typed-observation", timestamp_ms=1001, event_type="LOADOUT",
        payload={"reason": "SC_SYNC_CHAR_BAG_INFO", "rows": []}))
    assert len(routed) == 1 and routed[0]["type"] == "LOADOUT"


def test_new_overlay_evidence_cannot_advance_or_flush_legacy_trace(tmp_path):
    bridge = TraceBridge(tmp_path / "isolated.log")
    bridge._last_ts_ms = 1000
    bridge._pending_add_buff_ts_ms = 1000
    pending = {"fixture": "pending"}
    bridge._pending_add_buff_events.append(pending)
    for kind in (
        "SC_EFFECT_SNAPSHOT", "SC_OBJECT_LEAVE_VIEW", "SC_CHAR_BAG_SYNC_PERSISTENT_BUFFS",
        "SC_SCENE_RESET_ENTITY", "SC_SYNC_RACING_DUNGEON_BUFF_MODIFY",
        "SC_SYNC_RACING_DUNGEON_RECONNECT", "CAPTURE_SESSION_START", "CAPTURE_SESSION_END",
    ):
        bridge.handle_event({"type": kind, "timestamp_ms": 9000})
    assert bridge._last_ts_ms == 1000
    assert bridge._pending_add_buff_events == [pending]
    assert bridge.trace_file.read_text(encoding="utf-8") == ""


def test_session_start_end_side_channel_invalidates_once_without_changing_trace():
    observed = []
    service = DamageLogService.__new__(DamageLogService)
    service.observer = SimpleNamespace(on_event=observed.append)
    service.active_session = SimpleNamespace(session_id="first", flush_debug_state=lambda: None)
    service.active_flow = object()
    service.trace_bridge = None
    service.capture_manager = SimpleNamespace(restore_default_filters=lambda: None)
    service.log_file = None
    service.debug_counters = defaultdict(int)
    service._notify_capture_lifecycle("CAPTURE_SESSION_START")
    service._reset_session()
    service._reset_session()
    assert [event["type"] for event in observed] == ["CAPTURE_SESSION_START", "CAPTURE_SESSION_END"]
    assert all(event["session_id"] == "first" for event in observed)
    assert all(type(event["timestamp_ms"]) is int for event in observed)


def test_raw_operation_header_is_once_even_when_one_op_has_derived_events():
    value, events, registry = pipeline()
    observed = []
    value.on_battle_operation = observed.append
    value.merge_multi_phase_enemy_battles = True
    value.merged_battle_active = True
    value.tracked_enemy_inst_ids = {81}
    message = registry.load_message_class("CS_BATTLE_OP")()
    op = message.client_data.op_list.add(seq_id=0)
    op.op_type = op.DESCRIPTOR.fields_by_name["op_type"].enum_type.values_by_name["BattleOpEntityDie"].number
    op.entity_die_op_data.entity_inst_id = 81
    value._emit_battle_events(message, 1500)
    assert len(observed) == 1 and observed[0]["seq_id"] == 0
    assert observed[0]["type"] == "CAPTURE_BATTLE_OPERATION"
    assert observed[0]["operation_type"] == "BattleOpEntityDie"
    assert observed[0]["source_index"] == 1
    assert observed[0]["operation_sha256"] == hashlib.sha256(op.SerializeToString(deterministic=True)).hexdigest()
    assert set(observed[0]["capture_integrity"]) == {
        "packetsDroppedQueue", "decompressionErrors", "protobufDecodeErrors", "framingErrors",
        "pendingMultipacks", "pendingTransportSegments", "currentFrameErrors"}
    assert all(number == 0 for number in observed[0]["capture_integrity"].values())
    assert [event.event_type for event in events] == ["BattleOpEntityDie", "BattleOpModifyBattleState"]
    assert value.metrics.outbound_events_emitted == 2
    assert all(event.event_type != "CAPTURE_BATTLE_OPERATION" for event in events)


def test_unknown_raw_operation_keeps_header_without_changing_legacy_output():
    value, events, _ = pipeline()
    observed = []
    value.on_battle_operation = observed.append
    value._field_enum_name = lambda *_: None
    message = SimpleNamespace(client_data=SimpleNamespace(op_list=[SimpleNamespace(seq_id=41, op_type=999999)]))
    value._emit_battle_events(message, 1500)
    assert len(observed) == 1
    assert {key: observed[0][key] for key in ("type", "session_id", "timestamp_ms", "seq_id", "op_type", "operation_type")} == {
        "type": "CAPTURE_BATTLE_OPERATION", "session_id": value.session_id,
        "timestamp_ms": 1500, "seq_id": 41, "op_type": 999999, "operation_type": "UnknownBattleOperation"}
    assert observed[0]["source_index"] == 1 and observed[0]["operation_sha256"] is None
    assert events == [] and value.metrics.outbound_events_emitted == 0


def test_failing_readonly_operation_observer_cannot_stop_capture(caplog):
    value, events, registry = pipeline()
    def fail(_):
        raise RuntimeError("observer fixture")
    value.on_battle_operation = fail
    message = registry.load_message_class("CS_BATTLE_OP")()
    op = message.client_data.op_list.add(seq_id=14)
    op.op_type = op.DESCRIPTOR.fields_by_name["op_type"].enum_type.values_by_name["BattleOpEntityDie"].number
    op.entity_die_op_data.entity_inst_id = 81
    value._emit_battle_events(message, 1500)
    assert len(events) == 1 and events[0].event_type == "BattleOpEntityDie"
    assert "read-only battle operation observer failed" in caplog.text
    observed = []
    value.on_battle_operation = observed.append
    op.seq_id = 15
    value._emit_battle_events(message, 1600)
    assert observed[0]["source_index"] == 2


def test_top_level_head_error_reaches_observer_quality_without_changing_legacy_metrics():
    value, _, _ = pipeline()
    value.client_cipher = SimpleNamespace(process=lambda data: data)
    value._decode_session_frame("cs", 1, b"\x80", b"", 1000)
    assert value._observer_framing_errors == 1
    assert value.metrics.protobuf_decode_errors == 0 and value.metrics.decompression_errors == 0


@pytest.mark.parametrize("bad_tail", [b"\x02\xff", b"\x01\x00\x00\x80"])
def test_merged_framing_loss_is_counted_without_debug(bad_tail):
    _, _, registry = pipeline()
    merge = registry.load_message_class("CS_MERGE_MSG")()
    merge.msg = bad_tail
    merge_id = next(number for number, name in registry._maps["cs"].items() if name == "CS_MERGE_MSG")
    decoded = registry.decode_messages("cs", {"msgid": merge_id}, merge.SerializeToString())
    assert decoded[0].class_name == "CS_MERGE_MSG"
    assert registry.observer_framing_errors >= 1


def test_multipart_failures_and_abandoned_parts_are_visible_to_quality():
    value, _, _ = pipeline()
    def part(index, sequence, body):
        return value._assemble_multi_pack_message("cs", {"total_pack_count": 2, "current_pack_index": index,
            "up_seqid": sequence, "is_compress": False, "msgid": 999}, body, 1000)
    assert part(0, 0, b"A") is None
    assert value._observer_framing_errors == 1
    part(1, 11, b"first")
    part(2, 12, b"second")
    assert value._observer_framing_errors >= 2
    part(0, 20, b"A")
    before = value._observer_framing_errors
    part(0, 20, b"B")
    assert value._observer_framing_errors == before + 1
    before = value._observer_framing_errors
    value.flush_debug_state()
    assert value._observer_framing_errors > before and value.pending_multi_frames == {}


def test_transport_conflict_stays_visible_after_pending_segments_drain():
    value, _, _ = pipeline()
    stream = value.client_reassembler
    stream.accept(100, b"abcd")
    stream.accept(108, b"AAAA")
    stream.accept(108, b"BBBBB")
    stream.accept(104, b"efgh")
    assert stream._segments == {} and stream.gap_state() is None
    assert stream.observer_overlap_conflicts == 1


def test_actual_raw_observer_uses_capture_order_when_game_numbers_skip():
    import json
    from endfield_pcap.overlay_topics import OverlayTopicBroker
    contract = json.loads((DATA / "overlay/native.json").read_text(encoding="utf-8"))
    broker = OverlayTopicBroker(native_contract=contract, installed_hashes=contract["hashes"], publish_interval_ms=0)
    value, _, registry = pipeline()
    broker.on_state_change("live", value.session_id)
    value.on_battle_operation = broker.on_event
    message = registry.load_message_class("CS_BATTLE_OP")()
    for number in (10, 13, 18):
        op = message.client_data.op_list.add(seq_id=number)
        op.op_type = op.DESCRIPTOR.fields_by_name["op_type"].enum_type.values_by_name["BattleOpEntityDie"].number
        op.entity_die_op_data.entity_inst_id = 81
    value._emit_battle_events(message, 1000)
    data = broker.inspect("effects.operations.v1")["data"]
    assert data["continuity"]["basis"] == "source-index-and-capture-integrity"
    assert data["continuity"]["state"] == "contiguous" and data["gapRevision"] == 0
    assert [row["sourceIndex"] for row in data["operations"]] == [1, 2, 3]
    assert [row["sequenceRelation"] for row in data["operations"]] == ["first_observation", "forward_skip", "forward_skip"]
    broker.close()


def test_every_operation_in_a_damaged_compound_frame_stays_uncertain():
    from google.protobuf.internal.encoder import _VarintBytes
    value, _, registry = pipeline()
    observed = []
    value.on_battle_operation = observed.append
    value.client_cipher = SimpleNamespace(process=lambda data: data)
    battle_id = next(number for number, name in registry._maps["cs"].items() if name == "CS_BATTLE_OP")
    merge_id = next(number for number, name in registry._maps["cs"].items() if name == "CS_MERGE_MSG")
    battle = registry.load_message_class("CS_BATTLE_OP")()
    for sequence in (1, 2):
        op = battle.client_data.op_list.add(seq_id=sequence)
        op.op_type = op.DESCRIPTOR.fields_by_name["op_type"].enum_type.values_by_name["BattleOpEntityDie"].number
        op.entity_die_op_data.entity_inst_id = 81
    inner_head = b"\x08" + _VarintBytes(battle_id)
    body = battle.SerializeToString()
    wire = bytes([len(inner_head)]) + len(body).to_bytes(2, "little") + inner_head + body
    merge = registry.load_message_class("CS_MERGE_MSG")(msg=wire + b"\x02\xff")
    outer_head = b"\x08" + _VarintBytes(merge_id)
    value._decode_session_frame("cs", len(outer_head), outer_head, merge.SerializeToString(), 1000)
    assert len(observed) == 2
    assert all(row["capture_integrity"]["currentFrameErrors"] == 1 for row in observed)
    assert not value._observer_frame_invalid


def test_pending_quality_ignores_only_exactly_consumed_overlap_bytes():
    value, _, registry = pipeline()
    observed = []
    value.on_battle_operation = observed.append
    stream = value.client_reassembler
    stream.accept(100, b"abcd")
    stream.accept(108, b"ijkl")
    stream.accept(104, b"efghij")
    stream.accept(112, b"mnop")
    assert stream.gap_state() is None and stream._next_seq == 110
    message = registry.load_message_class("CS_BATTLE_OP")()
    op = message.client_data.op_list.add(seq_id=1)
    op.op_type = op.DESCRIPTOR.fields_by_name["op_type"].enum_type.values_by_name["BattleOpEntityDie"].number
    op.entity_die_op_data.entity_inst_id = 81
    value._emit_battle_events(message, 1000)
    assert observed[-1]["capture_integrity"]["pendingTransportSegments"] == 2
    assert b"".join(stream.accept(110, b"kl")) == b"klmnop"
    assert stream._segments and stream._next_seq == 116
    op.seq_id = 2
    value._emit_battle_events(message, 1100)
    assert observed[-1]["capture_integrity"]["pendingTransportSegments"] == 0
