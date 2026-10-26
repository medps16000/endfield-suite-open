from __future__ import annotations

from copy import deepcopy
import json
import threading
import hashlib

import pytest

from endfield_pcap.models import ServiceState
from endfield_pcap.overlay_topics import OverlayTopicBroker


def test_all_current_typed_action_runtime_cases_preserve_generated_payload(broker):
    from google.protobuf.json_format import MessageToDict
    from endfield_pcap.proto_generated.BATTLE_ACTION_INST_pb2 import BATTLE_ACTION_INST

    runtime_fields = BATTLE_ACTION_INST.DESCRIPTOR.oneofs_by_name["inst_impl"].fields
    assert len(runtime_fields) == 20
    for seq, field in enumerate(runtime_fields):
        action = BATTLE_ACTION_INST()
        getattr(action, field.name).SetInParent()
        payload = MessageToDict(action, preserving_proto_field_name=True,
                                always_print_fields_with_no_presence=True)
        emit(broker, "BattleOpTriggerAction", seq_id=seq, action_runtime_case=field.name, action=payload)
        row = broker.inspect("effects.operations.v1")["data"]["operations"][-1]
        assert row["payloadState"] == "whitelisted-fields", field.name
        assert row["payload"]["action"] == payload, field.name


@pytest.mark.parametrize("case,body", [
    ("modify_dynamic_blackboard_action", {"client_value": 0.0}),
    ("simple_calc_bb_action", {"client_value_a": 0.0, "client_value_b": 4.0,
                               "client_target_key": "token", "client_final_value": 0.0}),
    ("store_attribute_value_action", {"value": 0.0}),
    ("read_skill_setting_to_bb_action", {"runtime_details": [{"value": 0.0}]}),
    ("multi_modify_bb_action", {"key": "account", "value": 0.0}),
])
def test_typed_dynamic_action_values_remain_raw_observations(broker, case, body):
    # Match the current detail descriptor rather than assuming a semantic
    # name for its numerical output.
    if case == "read_skill_setting_to_bb_action":
        from endfield_pcap.overlay_topics import _proto_descriptor
        detail = _proto_descriptor("BATTLE_READ_SKILL_SETTING_TO_BB_ACTION_RUNTIME_DETAIL")
        body = {"runtime_details": [{field.name: 0.0 if field.type in (1, 2) else 0
                                     for field in detail.fields if field.type in (1, 2, 5, 13)}]}
    emit(broker, "BattleOpTriggerAction", seq_id=1, action_runtime_case=case,
         action={"action_type": 0, "action_id": 0, case: body})
    row = broker.inspect("effects.operations.v1")["data"]["operations"][0]
    assert row["payloadState"] == "whitelisted-fields"
    assert row["payload"]["action"][case] == body
    assert broker.inspect("effects.actual.v1")["data"] is None


@pytest.mark.parametrize("kind,case,body", [
    ("BattleOpSwitchMode", "switch_mode_op_data", {"mode_inst_id": 0, "mode_id": "",
                                                   "mode_change_type": 0, "is_enable": False}),
    ("BattleOpResetBattleData", "reset_battle_data_op_data", {}),
    ("BattleOpUpdateDataVersion", "update_data_version_op_data", {"client_data_version": "", "proto_version": 0}),
    ("BattleOpShieldValueModify", "shield_value_modify_op_data", {"buff_inst_id": "18446744073709551615",
                                                                 "shield_value": 0.0}),
])
def test_new_typed_operation_payloads_preserve_outer_metadata_and_zero(broker, kind, case, body):
    emit(broker, "CAPTURE_BATTLE_OPERATION", seq_id=0, operation_type=kind)
    assert broker.inspect("effects.operations.v1")["data"]["operations"][0]["payloadState"] == "pending"
    emit(broker, kind, seq_id=0, owner_id="18446744073709551615", operation_owner_id="18446744073709551615",
         generation=0, client_tick_tms="9223372036854775807", op_type=0,
         operation_payload_case=case, operation_data_serialization="protobuf_json_implicit_defaults_v1",
         raw_data=body, **body)
    row = broker.inspect("effects.operations.v1")["data"]["operations"][0]
    assert row["payloadState"] == "whitelisted-fields" and row["payloadRevision"] == 1
    assert row["payload"]["raw_data"] == body
    assert all(row["payload"][key] == value for key, value in body.items())
    assert row["payload"]["seq_id"] == 0 and row["payload"]["generation"] == 0
    assert row["payload"]["op_type"] == 0
    assert row["payload"]["client_tick_tms"] == "9223372036854775807"
    assert row["payload"]["operation_owner_id"] == "18446744073709551615"


def test_runtime_descriptor_payload_limits_omit_whole_payload(broker):
    emit(broker, "BattleOpTriggerAction", seq_id=1,
         action={"action_id": 0, "read_skill_setting_to_bb_action": {"runtime_details": [{}] * 257}})
    row = broker.inspect("effects.operations.v1")["data"]["operations"][0]
    assert row["payloadState"] == "omitted" and row["payload"] is None
    assert broker.inspect("effects.operations.v1")["data"]["payloadOmittedCount"] == 1


def test_game_events_are_allowlisted_observations_with_exact_parameter_maps(broker):
    blackboard = {"blackboard": {key: {"float_value": 0.0, "str_value": "", "is_dynamic": False}
                                 for key in ("token", "account", "hex", "bytes", "type")}}
    event = {"skills": [{"skill_inst_id": 2 ** 63 + 13, "level": 0, "is_enable": False,
                          "blackboard": blackboard, "raw_data": {"blackboard": blackboard,
                                                                   "inst_id": str(2 ** 63 + 13)}}],
             "generation": 0, "raw_body_hex": "PRIVATEHEX", "secret": "PRIVATESECRET"}
    emit(broker, "SC_ATTACH_SERVER_SKILL", **event)
    emit(broker, "CAPTURE_BATTLE_OPERATION", seq_id=1, operation_type="UnknownBattleOperation")
    emit(broker, "SC_BATTLE_DEBUG_INFO", raw_data="PRIVATEDEBUG")
    emit(broker, "SC_UNKNOWN_FUTURE", secret="UNKNOWNPRIVATE")
    data = broker.inspect("game.events.v1")["data"]
    assert len(data["events"]) == 1 and data["lastObservedIndex"] == 1
    row = data["events"][0]
    assert row["type"] == "SC_ATTACH_SERVER_SKILL" and row["payloadState"] == "complete"
    assert row["payload"]["skills"][0]["blackboard"] == blackboard
    assert row["payload"]["skills"][0]["raw_data"]["blackboard"] == blackboard
    assert row["payload"]["skills"][0]["skill_inst_id"] == str(2 ** 63 + 13)
    assert row["payload"]["skills"][0]["level"] == 0
    assert row["payload"]["skills"][0]["is_enable"] is False
    assert row["excludedDiagnosticFieldCount"] == 2
    assert "PRIVATE" not in json.dumps(data)
    assert "not-full-current-state" in data["valueSemantics"]


def test_game_event_filtering_capacity_omission_and_copy_isolation():
    instance = OverlayTopicBroker(publish_interval_ms=0, max_events=3)
    instance.on_state_change("live", "capture-1")
    try:
        callbacks = [[], []]
        for output in callbacks:
            instance.subscribe("game.events.v1", {"filter": {"eventTypes": ["SC_SYNC_ATTR"], "limit": 1}}, output.append)
        for index in range(5):
            emit(instance, "SC_SYNC_ATTR" if index % 2 == 0 else "LOADOUT", value=index, is_enable=False)
        data = instance.inspect("game.events.v1")["data"]
        assert data["retainedCount"] == 3 and data["bufferDroppedCount"] == 2
        assert [row["index"] for row in data["events"]] == [3, 4, 5]
        one = callbacks[0][-1]["data"]
        assert one["matchingEventCount"] == 2 and one["projectionOmittedEventCount"] == 1
        assert one["projectionReason"] == "limit" and one["events"][0]["index"] == 5
        one["events"][0]["payload"]["value"] = "changed"
        assert callbacks[1][-1]["data"]["events"][0]["payload"]["value"] == 4
        assert instance.inspect("game.events.v1")["data"]["events"][-1]["payload"]["value"] == 4
        instance.on_state_change("waiting_connection", None)
        assert callbacks[1][-1]["data"] is None
        instance.on_state_change("live", "capture-2")
        assert instance.inspect("game.events.v1")["data"] is None
        instance.on_event({"type": "LOADOUT", "session_id": "capture-2", "rows": [], "timestamp_ms": 0})
        assert instance.inspect("game.events.v1")["data"]["events"][0]["index"] == 1
    finally:
        instance.close()


@pytest.mark.parametrize("payload", [{"rows": [0] * 1025}, {"rows": ["x" * 16000] * 64}, {"value": b"raw bytes"}])
def test_game_event_over_limit_retains_an_explicit_omitted_row(broker, payload):
    emit(broker, "LOADOUT", **payload)
    data = broker.inspect("game.events.v1")["data"]
    assert data["payloadOmittedCount"] == 1
    assert data["events"][0]["payloadState"] == "omitted"
    assert data["events"][0]["payload"] is None
    assert data["events"][0]["payloadReason"] == "event_payload_unavailable_or_exceeds_limit"


@pytest.mark.parametrize("options", [{"eventTypes": ["SC_BATTLE_DEBUG_INFO"]}, {"eventTypes": ["SC_ANYTHING"]},
                                     {"eventTypes": "LOADOUT"}, {"limit": 257}, {"limit": False}, {"query": "x"}])
def test_game_event_filters_reject_unknown_types_and_invalid_bounds(broker, options):
    with pytest.raises(ValueError):
        broker.inspect("game.events.v1", {"filter": options})


def test_game_events_strip_clock_packet_bytes_without_dropping_false_or_zero(broker):
    emit(broker, "SC_GAME_TIME_FREEZE_END_RSP", in_time_freeze=False, tick_ms=0,
         raw_body_hex="PRIVATE", raw_bytes=b"PRIVATE", session_key="PRIVATE")
    row = broker.inspect("game.events.v1")["data"]["events"][0]
    assert row["payload"] == {"in_time_freeze": False, "tick_ms": 0}
    assert row["excludedDiagnosticFieldCount"] == 3


def test_game_event_depth_limits_do_not_return_a_truncated_parameter_tree(broker):
    value = {"zero": 0, "enabled": False}
    for _ in range(26):
        value = {"child": value}
    emit(broker, "SC_ATTACH_SERVER_SKILL", skills=[value])
    row = broker.inspect("game.events.v1")["data"]["events"][0]
    assert row["payloadState"] == "omitted" and row["payload"] is None


def test_unsubscribed_game_events_do_not_build_or_broadcast_large_projections(broker, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("no unsubscribed projection should be built")
    monkeypatch.setattr(broker, "_build", forbidden)
    emit(broker, "LOADOUT", rows=[{"char": "x" * 8000}] * 8)
    assert len(broker._game_events) == 1


def test_representative_typed_char_bag_with_many_skill_blackboards_reaches_game_events(broker):
    from google.protobuf.json_format import MessageToDict
    from endfield_pcap.proto_generated.SC_SYNC_CHAR_BAG_INFO_pb2 import SC_SYNC_CHAR_BAG_INFO
    message = SC_SYNC_CHAR_BAG_INFO()
    # Controlled capacity fixture, not a claim about a captured player roster:
    # 24 characters, 12 typed skills each, 16 original parameter names/skill.
    for character_index in range(24):
        character = message.char_info.add(objid=2 ** 63 + character_index, templateid=f"char_fixture_{character_index}")
        for skill_index in range(12):
            skill = character.battle_mgr_info.skill_list.add(inst_id=2 ** 63 + 1000 + character_index * 12 + skill_index)
            skill.skill_id.str_id = f"skill_fixture_{skill_index}"
            for parameter_index in range(16):
                skill.blackboard.blackboard[f"parameter_{parameter_index}"].is_dynamic = False
                skill.blackboard.blackboard[f"parameter_{parameter_index}"].float_value = 0.0
    payload = MessageToDict(message, preserving_proto_field_name=True, always_print_fields_with_no_presence=True)
    payload_size = len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    assert 128_000 < payload_size < 1_000_000
    emit(broker, "SC_SYNC_CHAR_BAG_INFO", **payload)
    envelope = broker.inspect("game.events.v1")
    row = envelope["data"]["events"][0]
    assert row["payloadState"] == "complete"
    assert row["payload"] == payload
    assert row["payload"]["char_info"][-1]["battle_mgr_info"]["skill_list"][-1]["blackboard"]["blackboard"]["parameter_15"]["is_dynamic"] is False
    assert len(json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) < 1_500_000


def test_global_buff_assigned_items_are_raw_initialization_and_never_stack_or_timer(broker):
    assigned = {"blackboard": {"max_stack": {"float_value": 16, "str_value": "", "is_dynamic": False},
                                "duration": {"float_value": 20, "str_value": "", "is_dynamic": False}}}
    emit(broker, "SC_BATTLE_ADD_GLOBAL_BUFF", global_buff_info={"global_buff_num_id": 700, "assigned_items": assigned})
    row = effects(broker)[0]
    assert row["raw"]["assignedItems"] == assigned
    assert row["raw"]["assignedItemsSource"] == "GLOBAL_BUFF_INFO.assigned_items"
    assert row["initializationParameters"]["assignedItems"] == assigned
    assert row["stackCount"] is None and row["remainingMs"] is None


def test_game_events_include_real_damage_model_and_team_observations(broker):
    from endfield_pcap.models import ActorRefPayload, DamageDetailPayload, DamageEvent
    event = DamageEvent(session_id="capture-1", timestamp_ms=0, seq_id=0, client_tick_tms=0,
                        skill_template_id=0, attacker=ActorRefPayload(100, None), target=ActorRefPayload(200, None),
                        details=[DamageDetailPayload(200, False, 0.0, 0.0, 0.0)]).as_dict()
    assert event["type"] == "damage"
    broker.on_event(event)
    emit(broker, "SC_SYNC_CHAR_BAG_INFO", char_team=[], curr_team_index=0)
    data = broker.inspect("game.events.v1")["data"]
    assert [row["type"] for row in data["events"]] == ["damage", "SC_SYNC_CHAR_BAG_INFO"]
    assert data["events"][0]["payload"]["details"][0]["value"] == 0
    assert data["events"][0]["payload"]["details"][0]["is_crit"] is False
    assert data["events"][1]["payload"]["curr_team_index"] == 0


@pytest.fixture
def broker():
    instance = OverlayTopicBroker(publish_interval_ms=0, catalog=[
        {"effectId": "buff_exact", "canonicalId": "buff_exact", "numericIds": ["700"],
         "label": "准确效果", "classification": "buff", "hasIcon": False},
        {"effectId": "buff_unknown", "canonicalId": "buff_unknown", "numericIds": ["701"], "label": None},
    ])
    instance.on_state_change(ServiceState.LIVE, "capture-1")
    yield instance
    instance.close()


def emit(broker, kind, **fields):
    broker.on_event({"type": kind, "session_id": "capture-1", "timestamp_ms": 10_000, **fields})


def add(broker, uid=1, effect="buff_exact", target=100, **fields):
    emit(broker, "BattleOpAddBuff", buff_inst_id=uid, str_id=effect,
         target_inst_id=target, src_inst_id=200, **fields)


def effects(broker, options=None):
    return broker.inspect("effects.actual.v1", options)["data"]["effects"]


def finish_native_contract():
    return {"schemaVersion": 1, "hashes": {"gameAssemblySha256": "a" * 64, "metadataSha256": "b" * 64},
            "defaultBranchOnly": True, "capabilities": {"clientFinishExact": {
                "supported": True, "namespace": "client_buff", "uidRequiredMask": "0x2000000000000000",
                "reduceLayer": 1, "force": True, "targetField": "operation_owner_id", "generationField": "generation",
                "matchScope": "same_capture_scene_target_uid", "closureReason": "client-finish-confirmed",
                "proofSha256": "c" * 64}}}


def make_finish_broker(*, installed_hashes=None):
    contract = finish_native_contract()
    instance = OverlayTopicBroker(publish_interval_ms=0, native_contract=contract,
                                   installed_hashes=contract["hashes"] if installed_hashes is None else installed_hashes)
    instance.on_state_change("live", "capture-1")
    return instance


def test_late_subscriber_gets_current_snapshot_and_unsubscribes(broker):
    add(broker)
    received = []
    unsubscribe = broker.subscribe("effects.actual.v1", {}, received.append)
    assert received[0]["data"]["effects"][0]["effectId"] == "buff_exact"
    assert len(received) == 1
    add(broker, uid=2)
    assert len(received) == 2
    assert len(received[-1]["data"]["effects"]) == 2
    assert received[-1]["sequence"] > received[0]["sequence"]
    unsubscribe()
    unsubscribe()
    add(broker, uid=3)
    assert len(received) == 2


def test_callback_mutation_does_not_change_broker_or_other_windows(broker):
    add(broker)
    first, second = [], []
    one = broker.subscribe("effects.actual.v1", {}, first.append)
    two = broker.subscribe("effects.actual.v1", {}, second.append)
    first[0]["data"]["effects"][0]["effectId"] = "changed"
    assert second[0]["data"]["effects"][0]["effectId"] == "buff_exact"
    assert effects(broker)[0]["effectId"] == "buff_exact"
    one()
    two()


def test_disconnect_immediate_null_and_reconnect_waits_fresh_data(broker):
    received = []
    broker.subscribe("effects.actual.v1", {}, received.append)
    add(broker)
    broker.on_state_change(ServiceState.WAITING_CONNECTION, None, object())
    assert received[-1]["state"] == "unavailable"
    assert received[-1]["data"] is None
    assert received[-1]["reason"] == "capture_disconnected"
    broker.on_state_change(ServiceState.LIVE, "capture-2")
    assert received[-1]["data"] is None
    assert received[-1]["reason"] == "awaiting_fresh_capture_data"
    # A delayed event from the old service session must not revive it.
    add(broker, uid=2)
    assert broker.inspect("effects.actual.v1")["data"] is None
    broker.on_event({"type": "BattleOpAddBuff", "session_id": "capture-2", "timestamp_ms": 20_000,
                     "buff_inst_id": 3, "str_id": "buff_exact", "target_inst_id": 100})
    assert [row["instanceId"] for row in received[-1]["data"]["effects"]] == ["3"]


def test_live_state_without_events_never_replays_old_effects(broker):
    add(broker)
    emit(broker, "CAPTURE_SESSION_END")
    assert broker.inspect("effects.actual.v1")["data"] is None
    emit(broker, "CAPTURE_SESSION_START")
    assert broker.inspect("effects.actual.v1")["data"] is None
    emit(broker, "SC_EFFECT_SNAPSHOT", owners=[])
    assert effects(broker) == []


@pytest.mark.parametrize("kind", ["CAPTURE_SESSION_END", "SC_RESET_BATTLE_STATUS",
                                  "SC_BATTLE_GENERATION_CHANGE", "SC_ENTER_DUNGEON",
                                  "SC_GAME_MECHANICS_SYNC_RESTART_GAME_INST",
                                  "SC_GAME_MECHANICS_SYNC_LEAVE_GAME_INST"])
def test_old_session_end_and_resets_cannot_clear_new_capture(broker, kind):
    add(broker)
    broker.on_event({"type": "CAPTURE_SESSION_START", "session_id": "capture-2"})
    broker.on_event({"type": "BattleOpAddBuff", "session_id": "capture-2", "timestamp_ms": 20_000,
                     "buff_inst_id": 2, "str_id": "buff_exact", "target_inst_id": 100})
    before = broker.inspect("effects.actual.v1")
    broker.on_event({"type": kind, "session_id": "capture-1", "timestamp_ms": 30_000})
    after = broker.inspect("effects.actual.v1")
    assert after["data"] == before["data"]
    assert after["sequence"] == before["sequence"]
    assert after["state"] == "ready"


def test_explicit_start_is_authoritative_even_with_an_older_capture_timestamp(broker):
    add(broker)
    before_generation = broker.inspect("effects.actual.v1")["data"]["context"]["generation"]
    # Observation wall time cannot decide which lifecycle handoff is real.
    broker.on_event({"type": "CAPTURE_SESSION_START", "session_id": "capture-2", "timestamp_ms": 1})
    assert broker.inspect("effects.actual.v1")["data"] is None
    broker.on_event({"type": "BattleOpAddBuff", "session_id": "capture-2", "timestamp_ms": 2,
                     "buff_inst_id": 2, "str_id": "buff_exact", "target_inst_id": 100})
    data = broker.inspect("effects.actual.v1")["data"]
    assert data["context"]["captureSessionId"] == "capture-2"
    assert data["context"]["generation"] > before_generation
    assert [row["instanceId"] for row in data["effects"]] == ["2"]


def test_filters_exact_effect_target_and_character(broker):
    emit(broker, "SC_SELF_SCENE_INFO", char_list=[
        {"id": 1000, "battle_inst_id": 100, "templateid": "chr_a", "display_name": "角色甲", "is_leader": True},
        {"id": 1001, "battle_inst_id": 101, "templateid": "chr_b"},
    ])
    add(broker, effect="buff_exact", target=100)
    add(broker, uid=2, effect="buff_unknown", target=101)
    add(broker, uid=3, effect="buff_unknown_extra", target=900)
    selected = effects(broker, {"filter": {"effectIds": ["buff_unknown"]}})
    assert [row["instanceId"] for row in selected] == ["2"]
    assert len(effects(broker, {"filter": {"target": "party"}})) == 2
    assert len(effects(broker, {"filter": {"targetIds": ["900"]}})) == 1
    assert effects(broker, {"filter": {"target": "character", "characterIds": ["chr_a"]}})[0]["targetId"] == "100"
    assert len(effects(broker, {"filter": {"hideUnknown": True}})) == 2
    # A team leader is not evidence of the currently controlled character.
    control = broker.inspect("effects.actual.v1", {"filter": {"target": "controlled"}})["data"]
    assert control["effects"] == []
    assert control["selectionReason"] == "controlled_target_unverified"


def test_hide_unknown_keeps_identified_effects_without_official_names(broker):
    add(broker, uid=1, effect="buff_unknown")
    add(broker, uid=2, effect="buff_unknown_extra")
    emit(broker, "BattleOpAddBuff", buff_inst_id=3, target_inst_id=100,
         buff_template_id={"type": "Buff", "int_id": 701})
    emit(broker, "BattleOpAddBuff", buff_inst_id=4, target_inst_id=100,
         buff_template_id={"type": "Skill", "int_id": 701})
    emit(broker, "BattleOpAddBuff", buff_inst_id=5, target_inst_id=100, int_id=701)
    selected = broker.inspect("effects.actual.v1", {"hideUnknown": True})["data"]
    assert [row["instanceId"] for row in selected["effects"]] == ["1", "3"]
    assert all(row["canonicalId"] == "buff_unknown" and row["label"] is None for row in selected["effects"])
    assert all(row["fieldAvailability"]["canonicalId"]["state"] == "known" for row in selected["effects"])
    assert all(row["fieldAvailability"]["label"]["state"] == "unknown" for row in selected["effects"])
    assert selected["scopeEffectCount"] == 5 and selected["deliveredEffectCount"] == 2
    assert len(effects(broker)) == 5


def test_actual_pages_respect_total_message_budget_prioritize_open_and_preserve_all_rows():
    icon = "data:image/webp;base64," + "A" * 80_000
    contract = finish_native_contract()
    instance = OverlayTopicBroker(publish_interval_ms=60_000, catalog=[
        {"canonicalId": "buff_exact", "label": "官方配置摘要", "icon": icon, "hasIcon": True}],
        native_contract=contract, installed_hashes=contract["hashes"], max_effects=1024)
    instance.on_state_change("live", "capture-1")
    base_uid = 1 << 61
    try:
        for index in range(630):
            add(instance, uid=base_uid + index + 1,
                assigned_items={"blackboard": {"rate": {"float_value": 0.125},
                                                "duration": {"str_value": "input_duration", "is_dynamic": True}}})
            if index < 90:
                emit(instance, "BattleOpFinishBuff", operation_owner_id=100, buff_inst_id=base_uid + index + 1,
                     reduce_layer=1, force=True)
        delivered = []
        offset = 0
        sequence = None
        while True:
            envelope = instance.inspect("effects.actual.v1", {"offset": offset})
            data = envelope["data"]
            serialized = json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            assert len(serialized) <= data["projectionByteLimit"] == 1_500_000
            assert data["matchingEffectCount"] == 630
            assert data["offset"] == offset and data["limit"] is None
            assert data["deliveredEffectCount"] == len(data["effects"])
            assert data["projectionOmittedOpenCount"] == 540 - sum(row["state"] == "observed-open" for row in data["effects"])
            assert data["projectionOmittedClosedCount"] == 90 - sum(row["state"] != "observed-open" for row in data["effects"])
            assert data["storageOmittedOpenCount"] == data["storageOmittedClosedCount"] == 0
            assert data["omittedOpenCount"] == data["projectionOmittedOpenCount"]
            assert data["omittedClosedCount"] == data["projectionOmittedClosedCount"]
            if offset == 0:
                assert len(data["effects"]) < 540
                assert data["projectionReason"] == "payload_budget"
                assert all(row["state"] == "observed-open" for row in data["effects"])
                assert len(data["effects"]) * len(icon) > 2 * 1024 * 1024
                assert all(row.get("icon") is None for row in data["effects"])
            delivered.extend(row["instanceId"] for row in data["effects"])
            assert sequence is None or envelope["sequence"] == sequence
            sequence = envelope["sequence"]
            if not data["hasMore"]:
                assert data["nextOffset"] is None
                break
            assert data["nextOffset"] == offset + len(data["effects"]) > offset
            offset = data["nextOffset"]
        expected = [str(base_uid + index + 1) for index in (*range(90, 630), *range(90))]
        assert delivered == expected
    finally:
        instance.close()


def test_actual_auxiliary_lists_share_the_message_budget_and_report_every_omission(broker):
    add(broker)
    for index in range(30):
        broker._stacking_groups[str(index)] = {"targetId": "100", "proof": "g" * 80_000}
        broker._unresolved[str(index)] = {"targetId": "100", "proof": "u" * 80_000}
    envelope = broker.inspect("effects.actual.v1")
    data = envelope["data"]
    assert len(json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) <= 1_500_000
    assert data["deliveredEffectCount"] == 1 and data["projectionReason"] == "payload_budget"
    assert len(data["stackingGroups"]) + data["omittedStackingGroupCount"] == 30
    assert len(data["unresolvedObservations"]) + data["omittedUnresolvedObservationCount"] == 30
    assert data["omittedStackingGroupCount"] + data["omittedUnresolvedObservationCount"] > 0


def test_actual_projection_cache_distinguishes_pages_and_shares_equal_normalized_filters(monkeypatch):
    instance = OverlayTopicBroker(publish_interval_ms=60_000)
    instance.on_state_change("live", "capture-1")
    outputs = [[] for _ in range(4)]
    try:
        add(instance, uid=1)
        add(instance, uid=2)
        for output, options in zip(outputs, ({"limit": 1}, {"filter": {"offset": 0, "limit": 1}},
                                             {"offset": 1, "limit": 1}, {"limit": 2})):
            instance.subscribe("effects.actual.v1", options, output.append)
        builds = []
        original = instance._build

        def count_build(topic, options):
            builds.append(topic)
            return original(topic, options)

        monkeypatch.setattr(instance, "_build", count_build)
        add(instance, uid=3)
        instance.flush()
        assert len(builds) == 3
        assert [row["instanceId"] for row in outputs[0][-1]["data"]["effects"]] == ["1"]
        assert [row["instanceId"] for row in outputs[2][-1]["data"]["effects"]] == ["2"]
        assert [row["instanceId"] for row in outputs[3][-1]["data"]["effects"]] == ["1", "2"]
        outputs[0][-1]["data"]["effects"][0]["raw"]["mutation"] = True
        assert "mutation" not in outputs[1][-1]["data"]["effects"][0]["raw"]
        assert instance.inspect("effects.definitions.v1")["state"] == "unavailable"
    finally:
        instance.close()


@pytest.mark.parametrize("limit", [None, 0, True, 4097])
def test_actual_pagination_rejects_invalid_explicit_limits(broker, limit):
    with pytest.raises(ValueError):
        broker.inspect("effects.actual.v1", {"limit": limit})


def test_actual_limit_default_is_independent_of_definition_topic_limit(broker):
    capabilities = next(row for row in broker.get_topics() if row["topic"] == "effects.actual.v1")["capabilities"]
    assert capabilities["pagination"] is True
    assert {"limit", "offset"}.issubset(capabilities["filters"])
    for index in range(30):
        add(broker, uid=index + 1)
    assert len(effects(broker)) == 30
    assert len(effects(broker, {"limit": 4096})) == 30
    assert len(effects(broker, {"offset": 20, "limit": 5})) == 5
    broker.configure_catalog([], definitions=[{"effectId": f"buff_{index}"} for index in range(30)])
    assert len(broker.inspect("effects.definitions.v1")["data"]["definitions"]) == 25
    with pytest.raises(ValueError):
        broker.inspect("effects.definitions.v1", {"limit": 101})


def test_actual_configuration_reports_lazy_definition_availability_without_loading():
    started, release = threading.Event(), threading.Event()
    calls = []

    def loader():
        calls.append(True)
        started.set()
        assert release.wait(2)
        return [{"effectId": "buff_exact"}]

    instance = OverlayTopicBroker(publish_interval_ms=0, catalog=[{"canonicalId": "buff_exact"}], definitions_loader=loader)
    instance.on_state_change("live", "capture-1")
    try:
        add(instance)
        assert effects(instance)[0]["configuration"]["reason"] == "definitions_not_loaded"
        assert not calls
        instance.subscribe("effects.definitions.v1", {}, lambda _envelope: None)
        assert started.wait(1)
        assert effects(instance)[0]["configuration"]["reason"] == "definitions_loading"
        release.set()
        instance.inspect("effects.definitions.v1")
        assert effects(instance)[0]["configuration"]["state"] == "known"
        assert effects(instance)[0]["configuration"]["reason"] is None
        instance.configure_catalog([{"canonicalId": "buff_exact"}], definitions=[])
        assert effects(instance)[0]["configuration"]["reason"] == "official_definition_unavailable"
    finally:
        release.set()
        instance.close()


def test_monster_type_does_not_invent_enemy_or_boss_classification(broker):
    emit(broker, "SC_OBJECT_ENTER_VIEW", objects=[{"battle_inst_id": 900, "obj_id": 9000,
                                                 "templateid": "monster_boss_named", "kind": "monster"}])
    add(broker, target=900)
    for target in ("enemy", "boss"):
        data = broker.inspect("effects.actual.v1", {"filter": {"target": target}})["data"]
        assert data["effects"] == []
        assert data["selectionState"] == "unavailable"
    assert len(effects(broker, {"filter": {"targetIds": ["900"]}})) == 1


def test_proven_official_target_metadata_can_enable_enemy_and_boss_filter():
    instance = OverlayTopicBroker(publish_interval_ms=0, target_catalog={
        "enemy_exact": {"label": "准确首领", "isEnemy": True, "isBoss": True}})
    instance.on_state_change("live", "capture-1")
    emit(instance, "SC_OBJECT_ENTER_VIEW", objects=[{"battle_inst_id": 900, "templateid": "enemy_exact"}])
    add(instance, target=900)
    assert len(effects(instance, {"filter": {"target": "boss"}})) == 1
    instance.close()


def test_snapshot_unknown_time_layer_group_size_and_namespace_separation(broker):
    emit(broker, "SC_EFFECT_SNAPSHOT", owners=[{"owner_obj_id": "999", "owner_battle_inst_id": "100",
         "owner_templateid": "chr_a", "owner_display_name": "角色甲", "generation": 0,
         "buffs": [{"inst_id": "1", "buff_id": {"str_id": "buff_exact", "type": "Buff"},
                    "duration": "10000", "is_enable": False, "enhance_multi": 4,
                    "stacking_group_id": "stack-key"}],
         "stacking_groups": [{"buff_inst_ids": [1, 2, 3], "max_stack_cnt": 20}]}])
    add(broker, uid=1)
    rows = effects(broker)
    assert len(rows) == 2
    assert {row["namespace"] for row in rows} == {"server_buff", "client_buff"}
    server = next(row for row in rows if row["namespace"] == "server_buff")
    for field in ("durationMs", "expiresAtMs", "remainingMs", "stackCount"):
        assert server[field] is None
    assert server["enabled"] is False
    assert server["observationKind"] == "initialSnapshot"
    assert server["raw"]["durationUnit"] == "unverified"
    assert server["raw"]["duration"] == "10000"
    assert server["sourceId"] is None
    data = broker.inspect("effects.actual.v1")["data"]
    assert (data["coverage"], data["completeness"], data["absence"]) == ("observed-only", "partial", "unknown")


def test_server_wire_owner_does_not_guess_scene_target_mapping(broker):
    emit(broker, "SC_OBJECT_ENTER_VIEW", objects=[{"battle_inst_id": 100, "obj_id": 999}])
    emit(broker, "SC_ADD_SERVER_BUFF", inst_id="100", generation=0,
         buffs=[{"inst_id": "99", "buff_id": {"type": "Buff", "int_id": 700}}])
    row = effects(broker)[0]
    assert row["targetId"] is None
    assert row["canonicalId"] == "buff_exact"
    emit(broker, "SC_REMOVE_SERVER_BUFF", inst_id="100", generation=1, del_inst_ids=["99"])
    assert effects(broker)[0]["state"] == "observed-open"
    emit(broker, "SC_REMOVE_SERVER_BUFF", inst_id="100", generation=0, del_inst_ids=["99"])
    assert effects(broker)[0]["state"] == "observed-closed"
    assert effects(broker)[0]["closureReason"] == "server-remove"


def test_finish_reduce_layer_and_force_do_not_fabricate_actual_removal(broker):
    add(broker)
    emit(broker, "BattleOpFinishBuff", buff_inst_id=1, reduce_layer=2, force=True)
    row = effects(broker)[0]
    assert row["state"] == "observed-open"
    assert row["removedAtMs"] is None
    assert row["stackCount"] is None
    assert row["observationKind"] != "finish-request"
    observation = broker.inspect("effects.actual.v1")["data"]["unresolvedObservations"][0]
    assert observation["raw"]["finishReduceLayer"] == 2
    assert observation["raw"]["finishForce"] is True
    assert observation["targetId"] is None


def test_same_version_exact_client_mark_finish_closes_exact_target_uid():
    instance = make_finish_broker()
    uid = (1 << 61) + 1
    add(instance, uid=uid, target=100, generation=3)
    emit(instance, "BattleOpFinishBuff", buff_inst_id=str(uid), operation_owner_id="100",
         reduce_layer=1, force=True, generation=9)
    row = effects(instance)[0]
    assert row["state"] == "observed-closed"
    assert row["closureReason"] == "client-finish-confirmed"
    assert row["enabled"] is False
    assert row["finishedAtMs"] == 10000
    assert row["removedAtMs"] is None
    assert row["raw"]["finishTargetGeneration"] == 9
    assert row["raw"]["finishSourceContext"] == "native-client-mark-finish"
    assert instance.inspect("effects.actual.v1")["data"]["lifecycleEvidence"]["clientFinishExact"]["state"] == "ready"
    instance.close()


@pytest.mark.parametrize("uid,reduce_layer,force,owner", [
    (1, 1, True, "100"), ((1 << 61) + 1, 0, True, "100"),
    ((1 << 61) + 1, 2, True, "100"), ((1 << 61) + 1, True, True, "100"),
    ((1 << 61) + 1, 1, False, "100"), ((1 << 61) + 1, 1, True, None),
])
def test_native_finish_rejects_other_shapes_without_guessing(uid, reduce_layer, force, owner):
    instance = make_finish_broker()
    add(instance, uid=uid, target=100)
    emit(instance, "BattleOpFinishBuff", buff_inst_id=str(uid), operation_owner_id=owner,
         reduce_layer=reduce_layer, force=force, generation=0)
    assert effects(instance)[0]["state"] == "observed-open"
    instance.close()


@pytest.mark.parametrize("hashes", [{}, {"gameAssemblySha256": "x" * 64, "metadataSha256": "b" * 64},
                                    {"gameAssemblySha256": "a" * 64, "metadataSha256": "d" * 64}])
def test_finish_from_unknown_or_other_game_version_stays_unresolved(hashes):
    instance = make_finish_broker(installed_hashes=hashes)
    uid = (1 << 61) + 1
    add(instance, uid=uid)
    emit(instance, "BattleOpFinishBuff", buff_inst_id=str(uid), operation_owner_id="100", reduce_layer=1, force=True)
    assert effects(instance)[0]["state"] == "observed-open"
    evidence = instance.inspect("effects.actual.v1")["data"]["lifecycleEvidence"]["clientFinishExact"]
    assert evidence == {"state": "unavailable", "reason": "native_version_unverified"}
    instance.close()


def test_exact_finish_does_not_choose_a_unique_uid_row_on_the_wrong_target():
    instance = make_finish_broker()
    uid = (1 << 61) + 1
    add(instance, uid=uid, target=100)
    emit(instance, "BattleOpFinishBuff", buff_inst_id=str(uid), operation_owner_id="101", reduce_layer=1, force=True)
    assert effects(instance)[0]["state"] == "observed-open"
    unmatched = instance.inspect("effects.actual.v1")["data"]["unresolvedObservations"][0]
    assert unmatched["observationKind"] == "finish-confirmed"
    assert unmatched["targetId"] == "101"
    assert unmatched["reason"] == "creation_not_observed"
    instance.close()


def test_client_mark_finish_does_not_close_equal_uid_server_buff():
    instance = make_finish_broker()
    uid = (1 << 61) + 1
    add(instance, uid=uid, target=100)
    emit(instance, "SC_EFFECT_SNAPSHOT", owners=[{"owner_battle_inst_id": "100", "generation": 0,
         "buffs": [{"inst_id": str(uid), "buff_id": {"str_id": "buff_exact", "type": "Buff"}}]}])
    emit(instance, "BattleOpFinishBuff", buff_inst_id=str(uid), operation_owner_id="100", reduce_layer=1, force=True)
    rows = {row["namespace"]: row for row in effects(instance)}
    assert rows["client_buff"]["state"] == "observed-closed"
    assert rows["server_buff"]["state"] == "observed-open"
    instance.close()


def test_catalog_keeps_short_labels_and_runtime_keeps_bounded_description(broker):
    broker.configure_catalog([{"canonicalId": "buff_exact", "label": "攻击提升",
                              "labelSource": "official-config-summary", "description": "来自准确官方配置的说明。",
                              "descriptionSource": "official-config-summary"}])
    add(broker)
    row = effects(broker)[0]
    assert row["label"] == "攻击提升"
    assert row["description"] == "来自准确官方配置的说明。"
    assert row["labelSource"] == row["descriptionSource"] == "official-config-summary"
    catalog_row = broker.inspect("effects.catalog.v1")["data"]["effects"][0]
    assert catalog_row["label"] == "攻击提升"
    assert catalog_row["labelSource"] == "official-config-summary"
    assert "description" not in catalog_row
    broker.configure_catalog([{"canonicalId": "buff_exact", "description": "x" * 801}])
    assert effects(broker)[0]["description"] is None


def test_finish_uid_shared_by_two_targets_does_not_choose_first(broker):
    add(broker, uid=1, target=100)
    add(broker, uid=1, target=101)
    emit(broker, "BattleOpFinishBuff", buff_inst_id=1, reduce_layer=0)
    assert all(row["observationKind"] != "finish-request" for row in effects(broker))


def test_finish_action_with_explicit_target_can_attach_observation(broker):
    add(broker, uid=1, target=100)
    add(broker, uid=1, target=101)
    emit(broker, "BattleOpTriggerAction", trigger_type=0,
         action={"finish_buff_action": {"finish_buffs": [{"target_id": 101, "buff_inst_id": 1}]}})
    assert effects(broker)[0]["observationKind"] != "finish-request"
    assert effects(broker)[1]["observationKind"] == "finish-request"
    assert all(row["removedAtMs"] is None for row in effects(broker))


def test_target_leave_and_death_are_context_unknown_not_remove(broker):
    add(broker)
    emit(broker, "SC_OBJECT_LEAVE_VIEW", objects=[{"battle_inst_id": "100", "obj_id": "999"}])
    row = effects(broker)[0]
    assert (row["state"], row["closureReason"], row["removedAtMs"]) == ("context-unknown", "target-left-view", None)
    add(broker, uid=2, target=101)
    emit(broker, "BattleOpEntityDie", entity_inst_id=101)
    assert effects(broker)[1]["closureReason"] == "target-died"


def test_instance_overwrite_is_distinct_from_actual_remove(broker):
    add(broker)
    add(broker, effect="buff_unknown")
    rows = effects(broker)
    assert len(rows) == 2
    replaced = next(row for row in rows if row["closureReason"] == "instance-overwritten")
    assert replaced["removedAtMs"] is None
    assert replaced["effectId"] == "buff_exact"
    assert len({row["key"] for row in rows}) == 2


def test_client_keyword_updates_and_enabled_do_not_guess_operation_owner_target(broker):
    add(broker)
    emit(broker, "BattleOpSetBuffEnableState", owner_id=100, states={"1": False})
    emit(broker, "BattleOpTriggerKeyword", owner_id=100, buff_inst_id=1,
         modify=[{"config_index": 0, "modify_value": 0.75}])
    row = effects(broker)[0]
    assert row["enabled"] is None
    assert "keywordUpdates" not in row["raw"]
    assert row["stackCount"] is None
    observations = broker.inspect("effects.actual.v1")["data"]["unresolvedObservations"]
    assert observations[0]["raw"]["enabled"] is False
    assert observations[1]["raw"]["keywordUpdates"] == [{"configIndex": 0, "value": 0.75}]
    assert all(item["targetId"] is None and item["operationOwnerId"] == "100" for item in observations)
    emit(broker, "BattleOpSetBuffEnableState", owner_id=101, states={"1": True})
    assert effects(broker)[0]["enabled"] is None


def test_create_action_only_uses_explicit_execute_phase(broker):
    detail = {"buff_inst_id": "88", "buff_id": "buff_exact", "source_id": "200", "target_id": "100"}
    emit(broker, "BattleOpTriggerAction", trigger_type=1, action={"create_buff_action": {"details": [detail]}})
    assert broker.inspect("effects.actual.v1")["data"] is None
    emit(broker, "BattleOpTriggerAction", trigger_type=0, action={"create_buff_action": {"details": [detail]}})
    assert effects(broker)[0]["instanceId"] == "88"
    emit(broker, "BattleOpTriggerAction", trigger_type=2, action={"create_buff_action": {"details": [detail]}})
    assert effects(broker)[0]["state"] == "observed-open"


def test_global_persistent_and_racing_have_no_invented_runtime_instance(broker):
    emit(broker, "SC_BATTLE_ADD_GLOBAL_BUFF", global_buff_info={"global_buff_num_id": 700})
    emit(broker, "SC_CHAR_BAG_SYNC_PERSISTENT_BUFFS", buffs=[{"item_num_id": 700, "duration": 60.0,
                                                           "char_inst_ids": ["100"], "stacking_key": "group"}])
    emit(broker, "SC_SYNC_RACING_DUNGEON_BUFF_MODIFY", buff_list=["buff_exact"], add_buff_id="race2")
    rows = effects(broker)
    assert len(rows) == 4
    assert all(row["instanceId"] is None and row["slotId"] is not None for row in rows)
    assert all(row["targetId"] is None and row["canonicalId"] is None for row in rows)
    assert all(row["durationMs"] is None and row["stackCount"] is None for row in rows)
    emit(broker, "SC_BATTLE_REMOVE_GLOBAL_BUFF", global_buff_num_id=700)
    assert next(row for row in effects(broker) if row["namespace"] == "global_buff")["closureReason"] == "global-remove"


def test_expiration_is_not_synthesized_from_unverified_duration(broker):
    emit(broker, "SC_ADD_SERVER_BUFF", inst_id=100, generation=0,
         buffs=[{"inst_id": 1, "buff_id": {"str_id": "buff_exact"}, "duration": 1}])
    broker._clock = lambda: 999_999_999.0
    assert effects(broker)[0]["state"] == "observed-open"
    assert effects(broker)[0]["remainingMs"] is None


def test_context_and_scene_changes_clear_stale_runtime_data(broker):
    emit(broker, "SC_SELF_SCENE_INFO", scene_id="scene-a", char_list=[])
    add(broker)
    emit(broker, "SC_SELF_SCENE_INFO", scene_id="scene-b", char_list=[])
    assert effects(broker) == []
    add(broker)
    generation = broker.inspect("effects.actual.v1")["data"]["context"]["generation"]
    emit(broker, "SC_GAME_MECHANICS_SYNC_RESTART_GAME_INST")
    assert broker.inspect("effects.actual.v1")["data"] is None
    add(broker, uid=2)
    data = broker.inspect("effects.actual.v1")["data"]
    assert data["context"]["generation"] > generation
    assert [row["instanceId"] for row in data["effects"]] == ["2"]


def test_catalog_ready_offline_full_and_safe_icon_validation(broker):
    broker.on_state_change("waiting_game")
    catalog = broker.inspect("effects.catalog.v1")
    assert catalog["state"] == "ready"
    assert len(catalog["data"]["effects"]) == 2
    broker.configure_catalog([
        {"canonicalId": "safe", "icon": "data:image/png;base64,AAAA"},
        {"canonicalId": "unsafe", "icon": "file:///secret.txt"},
    ])
    data = broker.inspect("effects.catalog.v1")["data"]
    rows = data["effects"]
    assert rows[0]["icon"] is None
    assert data["icons"][rows[0]["iconKey"]] == "data:image/png;base64,AAAA"
    assert rows[1]["icon"] is None
    assert rows[1]["iconKey"] is None


def test_projection_never_exposes_private_or_full_internal_payload(broker):
    add(broker, account_token="secret", capture_path="D:/private/log", flow={"server": "1.2.3.4"})
    payload = str(broker.inspect())
    assert "secret" not in payload
    assert "D:/private" not in payload
    assert "1.2.3.4" not in payload
    assert "account_token" not in payload
    emit(broker, "damage", attacker={"display_name": "private-player-name"})
    # The original purpose-specific topics do not acquire damage records.
    # The explicitly subscribed game-event stream now carries this existing
    # local observer payload, including its observed actor display name.
    old_topics = {name: value for name, value in broker.inspect().items() if name != "game.events.v1"}
    assert "private-player-name" not in str(old_topics)
    assert broker.inspect("game.events.v1")["data"]["events"][-1]["type"] == "damage"


def test_invalid_filters_and_unknown_topics_are_rejected(broker):
    for options in ({"filter": {"target": "guess-self"}}, {"filter": {"effectIds": "all"}},
                    {"filter": {"targetIds": [True]}}, {"filter": "bad"}):
        with pytest.raises(ValueError):
            broker.inspect("effects.actual.v1", options)
    with pytest.raises(ValueError):
        broker.subscribe("admin.quit", {}, lambda _: None)
    with pytest.raises(TypeError):
        broker.subscribe("effects.actual.v1", {})


def test_shared_producer_batches_updates_for_multiple_subscribers():
    instance = OverlayTopicBroker(publish_interval_ms=60_000)
    instance.on_state_change("live", "capture-1")
    first, second = [], []
    instance.subscribe("effects.actual.v1", {}, first.append)
    instance.subscribe("effects.actual.v1", {}, second.append)
    for uid in range(1, 101):
        add(instance, uid=uid)
    assert len(first) == len(second) == 1
    assert len(effects(instance)) == 100
    instance.flush()
    assert len(first) == len(second) == 2
    assert first[-1] == second[-1]
    instance.on_state_change("waiting_connection")
    assert first[-1]["data"] is None
    instance.close()


def test_equal_normalized_filters_build_one_projection_for_four_windows(monkeypatch):
    instance = OverlayTopicBroker(publish_interval_ms=60_000)
    instance.on_state_change("live", "capture-1")
    outputs = [[] for _ in range(4)]
    options = [{}, {"filter": {"target": "all"}},
               {"filter": {"targetIds": [], "characterIds": [], "effectIds": []}},
               {"filter": {"hideUnknown": False, "target": "all"}}]
    for option, output in zip(options, outputs):
        instance.subscribe("effects.actual.v1", option, output.append)
    original_build = instance._build
    built_topics = []

    def count_build(topic, normalized):
        built_topics.append(topic)
        return original_build(topic, normalized)

    monkeypatch.setattr(instance, "_build", count_build)
    add(instance)
    instance.flush()
    assert built_topics == ["effects.actual.v1"]
    assert all(len(output) == 2 for output in outputs)
    assert all(output[-1] == outputs[0][-1] for output in outputs)
    assert len({id(output[-1]["data"]["effects"][0]["raw"]) for output in outputs}) == 4
    instance.close()


def test_equal_identity_filters_ignore_input_order_when_sharing_projection(monkeypatch):
    instance = OverlayTopicBroker(publish_interval_ms=60_000)
    instance.on_state_change("live", "capture-1")
    instance.subscribe("effects.actual.v1", {"filter": {"effectIds": ["b", "a"]}}, lambda _: None)
    instance.subscribe("effects.actual.v1", {"filter": {"effectIds": ["a", "b", "a"]}}, lambda _: None)
    original_build = instance._build
    calls = []

    def count_build(topic, normalized):
        calls.append(topic)
        return original_build(topic, normalized)

    monkeypatch.setattr(instance, "_build", count_build)
    add(instance, effect="a")
    instance.flush()
    assert calls == ["effects.actual.v1"]
    instance.close()


def test_callback_mutation_during_shared_publish_cannot_affect_any_window_or_producer():
    instance = OverlayTopicBroker(publish_interval_ms=60_000)
    instance.on_state_change("live", "capture-1")
    others = []

    def mutate(payload):
        if payload["data"] is not None:
            payload["data"]["context"]["generation"] = -999
            payload["data"]["effects"][0]["raw"]["templateType"] = "mutation"
            payload["data"]["effects"].append({"fake": True})
            payload["data"]["stackingGroups"].append({"fake": True})

    instance.subscribe("effects.actual.v1", {}, mutate)
    instance.subscribe("effects.actual.v1", {}, others.append)
    add(instance)
    instance.flush()
    data = others[-1]["data"]
    assert len(data["effects"]) == 1
    assert data["effects"][0]["raw"]["templateType"] is None
    assert data["stackingGroups"] == []
    assert data["context"]["generation"] >= 0
    assert instance.inspect("effects.actual.v1")["data"] == data
    instance.close()


def test_inspect_result_is_fully_detached_without_second_whole_snapshot_copy(broker):
    add(broker)
    view = broker.inspect()
    view["effects.actual.v1"]["data"]["effects"][0]["raw"]["templateType"] = "mutation"
    view["effects.actual.v1"]["data"]["context"]["generation"] = -1
    view["effects.catalog.v1"]["data"]["effects"][0]["numericIds"].append("forged")
    next_view = broker.inspect()
    assert next_view["effects.actual.v1"]["data"]["effects"][0]["raw"]["templateType"] is None
    assert next_view["effects.actual.v1"]["data"]["context"]["generation"] >= 0
    assert "forged" not in next_view["effects.catalog.v1"]["data"]["effects"][0]["numericIds"]


def test_disconnect_is_immediate_for_four_windows_while_runtime_flush_is_pending():
    instance = OverlayTopicBroker(publish_interval_ms=60_000)
    instance.on_state_change("live", "capture-1")
    outputs = [[] for _ in range(4)]
    for output in outputs:
        instance.subscribe("effects.actual.v1", {}, output.append)
    add(instance)
    assert all(len(output) == 1 for output in outputs)
    instance.on_state_change("waiting_connection")
    assert all(len(output) == 2 and output[-1]["state"] == "unavailable" and output[-1]["data"] is None
               for output in outputs)
    instance.close()


def test_callback_runs_without_broker_state_lock(broker):
    done = threading.Event()
    threads = []
    completed_without_lock = []

    def callback(_):
        worker = threading.Thread(target=lambda: (broker.inspect(), done.set()))
        threads.append(worker)
        worker.start()
        completed_without_lock.append(done.wait(2))

    unsubscribe = broker.subscribe("effects.actual.v1", {}, callback)
    unsubscribe()
    for thread in threads:
        thread.join(2)
    assert completed_without_lock == [True]


def test_bounded_observations_report_omissions():
    instance = OverlayTopicBroker(publish_interval_ms=0, max_effects=2)
    instance.on_state_change("live", "capture-1")
    for uid in range(1, 5):
        add(instance, uid=uid)
    data = instance.inspect("effects.actual.v1")["data"]
    assert len(data["effects"]) == 2
    assert data["omittedOpenCount"] == 2
    assert data["completeness"] == "partial"
    instance.close()


def test_subscriber_failure_does_not_break_other_windows(broker):
    def fail(_):
        raise RuntimeError("demo failure")
    broker.subscribe("effects.actual.v1", {}, fail)
    received = []
    broker.subscribe("effects.actual.v1", {}, received.append)
    add(broker)
    assert received[-1]["data"]["effects"][0]["effectId"] == "buff_exact"


def test_uint64_ids_survive_json_projection_exactly(broker):
    large = 2 ** 64 - 1
    add(broker, uid=large, target=large - 1)
    row = effects(broker)[0]
    assert row["instanceId"] == str(large)
    assert row["targetId"] == str(large - 1)


def test_ambiguous_numeric_catalog_alias_is_not_assigned():
    instance = OverlayTopicBroker(publish_interval_ms=0, catalog=[
        {"canonicalId": "a", "numericIds": ["700"]}, {"canonicalId": "b", "numericIds": ["700"]}])
    instance.on_state_change("live", "capture-1")
    emit(instance, "BattleOpAddBuff", buff_inst_id=1, int_id=700, target_inst_id=100)
    assert effects(instance)[0]["canonicalId"] is None
    instance.close()


def test_three_or_more_conflicting_numeric_aliases_do_not_crash_or_resolve():
    instance = OverlayTopicBroker(publish_interval_ms=0, catalog=[
        {"canonicalId": "a", "numericIds": ["700"]}, {"canonicalId": "b", "numericIds": ["700"]},
        {"canonicalId": "c", "numericIds": ["700"]}, {"canonicalId": "a", "numericIds": ["700"]}])
    instance.on_state_change("live", "capture-1")
    emit(instance, "BattleOpAddBuff", buff_inst_id=1, int_id=700, target_inst_id=100)
    assert effects(instance)[0]["canonicalId"] is None
    assert len(instance.inspect("effects.catalog.v1")["data"]["effects"]) == 4
    instance.close()


@pytest.mark.parametrize("source", ["client", "server"])
@pytest.mark.parametrize("template_type,expected", [("Buff", "buff_exact"), (1, "buff_exact"),
                                                    (None, None), ("Skill", None), (2, None),
                                                    (0, None), ("BATTLE_ACTION_OWNER_TYPE_None", None)])
def test_numeric_catalog_lookup_requires_explicit_typed_buff(broker, source, template_type, expected):
    template = {"int_id": 700}
    if template_type is not None:
        template["type"] = template_type
    if source == "client":
        emit(broker, "BattleOpAddBuff", buff_inst_id=1, target_inst_id=100, buff_template_id=template)
    else:
        emit(broker, "SC_ADD_SERVER_BUFF", inst_id="999", generation=0,
             buffs=[{"inst_id": "1", "buff_id": template}])
    row = effects(broker)[0]
    assert row["effectId"] == "700"
    assert row["canonicalId"] == expected


def test_numeric_string_template_is_not_the_numeric_oneof(broker):
    emit(broker, "BattleOpAddBuff", buff_inst_id=1, target_inst_id=100,
         buff_template_id={"type": "Buff", "str_id": "700"})
    assert effects(broker)[0]["canonicalId"] is None
    assert effects(broker)[0]["effectId"] == "700"


def test_untyped_legacy_numeric_template_never_uses_outer_client_scope_as_proof(broker):
    emit(broker, "BattleOpAddBuff", buff_inst_id=1, target_inst_id=100, int_id=700)
    assert effects(broker)[0]["canonicalId"] is None
    broker.configure_catalog([{"canonicalId": "buff_exact", "numericIds": ["700"], "label": "准确效果"}])
    assert effects(broker)[0]["canonicalId"] is None


def test_catalog_reconfigure_preserves_explicit_type_boundary(broker):
    emit(broker, "BattleOpAddBuff", buff_inst_id=1, target_inst_id=100,
         buff_template_id={"type": "Skill", "int_id": 700})
    emit(broker, "BattleOpAddBuff", buff_inst_id=2, target_inst_id=100,
         buff_template_id={"type": "Buff", "int_id": 700})
    broker.configure_catalog([{"canonicalId": "buff_exact", "numericIds": ["700"], "label": "准确效果"}])
    assert [row["canonicalId"] for row in effects(broker)] == [None, "buff_exact"]


def test_catalog_metadata_is_not_a_runtime_template_relation(broker):
    broker.configure_catalog([{"_meta": {"skill_id": {"dic": {"700": "skill_separate"}}}},
                              {"canonicalId": "buff_exact", "numericIds": ["700"]}])
    emit(broker, "BattleOpAddBuff", buff_inst_id=1, target_inst_id=100,
         buff_template_id={"type": "Skill", "int_id": 700})
    assert effects(broker)[0]["canonicalId"] is None
    assert len(broker.inspect("effects.catalog.v1")["data"]["effects"]) == 1


def test_create_buff_detail_numeric_uses_its_explicit_protobuf_type_context(broker):
    emit(broker, "BattleOpTriggerAction", trigger_type=0, action_runtime_case="create_buff_action",
         action={"action_type": "BattleActionCreateBuff", "create_buff_action": {"details": [
             {"buff_inst_id": "91", "buff_num_id": 700, "target_id": "100", "source_id": "200"}]}})
    row = effects(broker)[0]
    assert row["canonicalId"] == "buff_exact"
    assert row["effectId"] == "700"
    assert row["raw"]["templateType"] is None
    assert row["raw"]["templateIdCase"] == "direct_numeric"
    assert row["raw"]["identitySourceContext"] == "protobuf-create-buff-detail"
    assert row["raw"]["identitySourceField"] == "BATTLE_CREATE_BUFF_ACTION_DETAIL.buff_num_id"
    assert row["state"] == "observed-open"
    broker.configure_catalog([{"canonicalId": "buff_exact", "numericIds": ["700"], "label": "准确效果"}])
    assert effects(broker)[0]["canonicalId"] == "buff_exact"


@pytest.mark.parametrize("action_type,runtime_case,numeric", [
    ("BattleActionCreateBuff", None, 700), ("BattleActionDamage", "create_buff_action", 700),
    ("BattleActionCreateBuff", "damage_action", 700), (None, "create_buff_action", 700),
    ("BattleActionCreateBuff", "create_buff_action", "700"),
])
def test_create_numeric_mapping_requires_the_complete_typed_action_context(broker, action_type, runtime_case, numeric):
    emit(broker, "BattleOpTriggerAction", trigger_type=0, action_runtime_case=runtime_case,
         action={"action_type": action_type, "create_buff_action": {"details": [
             {"buff_inst_id": "91", "buff_num_id": numeric, "target_id": "100"}]}})
    assert effects(broker)[0]["canonicalId"] is None


def test_create_buff_enum_value_two_is_the_same_explicit_action_type(broker):
    emit(broker, "BattleOpTriggerAction", trigger_type=0, action_runtime_case="create_buff_action",
         action={"action_type": 2, "create_buff_action": {"details": [
             {"buff_inst_id": "91", "buff_num_id": 700, "target_id": "100"}]}})
    assert effects(broker)[0]["canonicalId"] == "buff_exact"


def test_create_specific_context_does_not_open_generic_client_server_or_global_aliases(broker):
    emit(broker, "BattleOpAddBuff", buff_inst_id=1, int_id=700, target_inst_id=100,
         identitySourceContext="protobuf-create-buff-detail")
    emit(broker, "SC_ADD_SERVER_BUFF", inst_id="999", generation=0, buffs=[{"inst_id": "2", "buff_id": {"int_id": 700}}])
    emit(broker, "SC_BATTLE_ADD_GLOBAL_BUFF", global_buff_info={"global_buff_num_id": 700})
    assert all(row["canonicalId"] is None for row in effects(broker))


def test_official_display_group_source_and_icon_fields_survive_catalog_and_runtime(broker):
    broker.configure_catalog([{"canonicalId": "buff_exact", "numericIds": ["700"], "hasIcon": True,
                              "classification": "buff", "uiGroup": "Buff", "uiGroupUsesNumericPriority": False,
                              "classificationSource": "official-icon-priority-group"}])
    emit(broker, "BattleOpAddBuff", buff_inst_id=1, target_inst_id=100,
         buff_template_id={"type": "Buff", "int_id": 700})
    row = effects(broker)[0]
    catalog_row = broker.inspect("effects.catalog.v1")["data"]["effects"][0]
    for value in (row, catalog_row):
        assert value["hasIcon"] is True
        assert value["uiGroup"] == "Buff"
        assert value["uiGroupUsesNumericPriority"] is False
        assert value["classificationSource"] == "official-icon-priority-group"
        assert value["classification"] == "buff"
    assert row["stackCount"] is None
    assert row["durationMs"] is None


def test_unmatched_runtime_icon_and_group_remain_unknown_not_false(broker):
    add(broker, effect="not_in_the_official_directory")
    row = effects(broker)[0]
    assert row["hasIcon"] is None
    assert row["uiGroup"] is None
    assert row["uiGroupUsesNumericPriority"] is None
    assert row["classificationSource"] is None


def test_catalog_deduplicates_repeated_icons_below_bridge_limit(broker):
    icon = "data:image/png;base64," + "AAAA" * 2048
    broker.configure_catalog([{"canonicalId": f"buff_{index}", "icon": icon} for index in range(512)])
    envelope = broker.inspect("effects.catalog.v1")
    data = envelope["data"]
    assert len(data["icons"]) == 1
    keys = {row["iconKey"] for row in data["effects"]}
    assert len(keys) == 1
    assert all(row["icon"] is None for row in data["effects"])
    assert data["icons"][next(iter(keys))] == icon
    payload = json.dumps({"kind": "snapshot", "envelope": envelope}, ensure_ascii=False, separators=(",", ":"))
    assert len(payload) < 2 * 1024 * 1024
    # The previous repeated-icon projection would trip the bridge's limit.
    assert len(icon) * len(data["effects"]) > 2 * 1024 * 1024
    data["icons"][next(iter(keys))] = "mutated"
    assert next(iter(broker.inspect("effects.catalog.v1")["data"]["icons"].values())) == icon
    broker.configure_catalog([{"canonicalId": "without-image"}])
    assert broker.inspect("effects.catalog.v1")["data"]["icons"] == {}


@pytest.mark.parametrize("value,expected", [("skill-button-progress", "skill-button-progress"),
                                            ("image", "image"), ("missing-asset", "missing-asset"),
                                            ("guessed-progress", None), (None, None), (True, None), ({}, None)])
def test_icon_presentation_accepts_only_exact_display_metadata(broker, value, expected):
    broker.configure_catalog([{"canonicalId": "buff_exact", "iconPresentation": value}])
    add(broker)
    assert effects(broker)[0]["iconPresentation"] == expected
    assert broker.inspect("effects.catalog.v1")["data"]["effects"][0]["iconPresentation"] == expected
    broker.configure_catalog([{"canonicalId": "buff_exact", "iconPresentation": "skill-button-progress"}])
    assert effects(broker)[0]["iconPresentation"] == "skill-button-progress"


def generation_native_contract():
    contract = finish_native_contract()
    contract["capabilities"]["entityGenerationExact"] = {
        "supported": True, "namespace": "scene_object", "targetField": "inst_id", "generationField": "generation",
        "matchScope": "same_capture_scene_explicit_object_battle_relation", "proofSha256": "d" * 64}
    return contract


def make_generation_broker(*, installed_hashes=None):
    contract = generation_native_contract()
    instance = OverlayTopicBroker(publish_interval_ms=0, native_contract=contract,
                                   installed_hashes=contract["hashes"] if installed_hashes is None else installed_hashes)
    instance.on_state_change("live", "capture-1")
    emit(instance, "SC_OBJECT_ENTER_VIEW", objects=[
        {"obj_id": 1000, "battle_inst_id": 100}, {"obj_id": 1001, "battle_inst_id": 101}])
    return instance


def test_entity_reset_invalidates_only_explicit_target_and_keeps_capture_context(broker):
    add(broker, uid=1, target=100)
    add(broker, uid=2, target=101)
    before_context = broker.inspect("effects.actual.v1")["data"]["context"]
    emit(broker, "SC_RESET_BATTLE_STATUS", inst_id=999, battle_inst_id=100,
         battle_info={"battle_inst_id": 100, "msg_generation": 3})
    data = broker.inspect("effects.actual.v1")["data"]
    assert data["context"] == before_context
    rows = {row["targetId"]: row for row in data["effects"]}
    assert rows["100"]["state"] == "context-unknown"
    assert rows["100"]["closureReason"] == "target-battle-reset"
    assert rows["100"]["removedAtMs"] is None
    assert rows["101"]["state"] == "observed-open"
    emit(broker, "SC_EFFECT_SNAPSHOT", owners=[{"owner_battle_inst_id": 100, "generation": 3,
         "buffs": [{"inst_id": 8, "buff_id": {"str_id": "buff_exact"}}]}])
    assert next(row for row in effects(broker) if row["instanceId"] == "8")["state"] == "observed-open"
    assert next(row for row in effects(broker) if row["instanceId"] == "2")["state"] == "observed-open"


@pytest.mark.parametrize("fields", [{}, {"inst_id": 100},
                                    {"battle_inst_id": 100, "battle_info": {"battle_inst_id": 101}}])
def test_missing_or_conflicting_reset_target_never_clears_any_target(broker, fields):
    add(broker, target=100)
    add(broker, uid=2, target=101)
    emit(broker, "SC_RESET_BATTLE_STATUS", **fields)
    data = broker.inspect("effects.actual.v1")["data"]
    assert all(row["state"] == "observed-open" for row in data["effects"])
    assert len(data["unresolvedObservations"]) == 1
    assert data["unresolvedObservations"][0]["namespace"] == "scene_object"


def test_nested_explicit_reset_target_is_usable_without_guessing_inst_id(broker):
    add(broker, target=100)
    add(broker, uid=2, target=101)
    emit(broker, "SC_RESET_BATTLE_STATUS", inst_id=101, battle_info={"battle_inst_id": 100})
    rows = {row["targetId"]: row for row in effects(broker)}
    assert rows["100"]["state"] == "context-unknown"
    assert rows["101"]["state"] == "observed-open"


def test_generation_change_uses_version_qualified_object_mapping_and_only_changed_target():
    instance = make_generation_broker()
    try:
        add(instance, target=100)
        add(instance, uid=2, target=101)
        emit(instance, "SC_EFFECT_SNAPSHOT", owners=[{"owner_battle_inst_id": 100, "generation": 2, "buffs": []}])
        emit(instance, "SC_BATTLE_GENERATION_CHANGE", inst_id=1000, generation=2)
        assert all(row["state"] == "observed-open" for row in effects(instance))
        emit(instance, "SC_BATTLE_GENERATION_CHANGE", inst_id=1000, generation=3)
        rows = {row["targetId"]: row for row in effects(instance)}
        assert rows["100"]["state"] == "context-unknown"
        assert rows["100"]["closureReason"] == "target-generation-changed"
        assert rows["101"]["state"] == "observed-open"
    finally:
        instance.close()


def test_first_observed_generation_does_not_invent_old_observation_generation():
    instance = make_generation_broker()
    try:
        add(instance, target=100)
        emit(instance, "SC_BATTLE_GENERATION_CHANGE", inst_id=1000, generation=2)
        assert effects(instance)[0]["state"] == "observed-open"
        unresolved = instance.inspect("effects.actual.v1")["data"]["unresolvedObservations"]
        assert unresolved[0]["reason"] == "previous_generation_unobserved"
        emit(instance, "SC_BATTLE_GENERATION_CHANGE", inst_id=1000, generation=3)
        assert effects(instance)[0]["state"] == "context-unknown"
    finally:
        instance.close()


@pytest.mark.parametrize("installed", [{}, {"gameAssemblySha256": "a" * 64, "metadataSha256": "e" * 64}])
def test_unverified_native_generation_relation_cannot_map_even_known_object(installed):
    instance = make_generation_broker(installed_hashes=installed)
    try:
        add(instance, target=100)
        emit(instance, "SC_EFFECT_SNAPSHOT", owners=[{"owner_battle_inst_id": 100, "generation": 2, "buffs": []}])
        emit(instance, "SC_BATTLE_GENERATION_CHANGE", inst_id=1000, generation=3)
        assert effects(instance)[0]["state"] == "observed-open"
        assert instance.inspect("capture.context.v1")["lifecycleEvidence"]["entityGenerationExact"]["state"] == "unavailable"
    finally:
        instance.close()


def test_generation_inst_id_equal_to_battle_id_is_not_an_object_relation():
    instance = make_generation_broker()
    try:
        add(instance, target=100)
        emit(instance, "SC_EFFECT_SNAPSHOT", owners=[{"owner_battle_inst_id": 100, "generation": 2, "buffs": []}])
        emit(instance, "SC_BATTLE_GENERATION_CHANGE", inst_id=100, generation=3)
        assert effects(instance)[0]["state"] == "observed-open"
        assert instance.inspect("effects.actual.v1")["data"]["unresolvedObservations"][0]["reason"] == "object_battle_relation_unverified"
    finally:
        instance.close()


def test_capture_context_exports_capabilities_even_before_fresh_data_and_when_disconnected():
    instance = make_generation_broker()
    try:
        instance.on_state_change("waiting_game")
        envelope = instance.inspect("capture.context.v1")
        assert envelope["data"] is None
        assert envelope["lifecycleEvidence"]["clientFinishExact"]["state"] == "ready"
        assert envelope["lifecycleEvidence"]["entityGenerationExact"]["state"] == "ready"
        assert "hashes" not in envelope["lifecycleEvidence"]
    finally:
        instance.close()


def enable_native_contract():
    contract = generation_native_contract()
    contract["capabilities"]["clientEnableStateExact"] = {
        "supported": True, "namespace": "client_buff", "uidRequiredMask": "0x2000000000000000",
        "targetField": "owner_id", "statesField": "states", "generationField": "generation",
        "matchScope": "same_capture_scene_target_uid", "proofSha256": "e" * 64}
    return contract


def make_enable_broker(*, installed_hashes=None, contract=None):
    contract = enable_native_contract() if contract is None else contract
    instance = OverlayTopicBroker(publish_interval_ms=0, native_contract=contract,
                                   installed_hashes=contract["hashes"] if installed_hashes is None else installed_hashes)
    instance.on_state_change("live", "capture-1")
    return instance


def test_exact_client_enable_state_changes_only_matching_target_uid_and_can_reenable():
    instance = make_enable_broker()
    uid = (1 << 61) + 55
    try:
        add(instance, uid=uid, target=100, generation=8)
        add(instance, uid=uid, target=101, generation=8)
        emit(instance, "BattleOpSetBuffEnableState", owner_id=100, generation=9, seq_id=1, states={str(uid): False})
        rows = {row["targetId"]: row for row in effects(instance)}
        assert rows["100"]["enabled"] is False
        assert rows["100"]["state"] == "observed-open"
        assert rows["100"]["removedAtMs"] is None
        assert rows["100"]["finishedAtMs"] is None
        assert rows["100"]["raw"]["operationGeneration"] == 9
        assert rows["100"]["raw"]["enableSourceContext"] == "native-client-buff-enable-setter"
        assert rows["101"]["enabled"] is None
        emit(instance, "BattleOpSetBuffEnableState", owner_id=100, generation=9, states={str(uid): True})
        row = next(row for row in effects(instance) if row["targetId"] == "100")
        assert row["enabled"] is True
        assert row["state"] == "observed-open"
        assert instance.inspect("effects.actual.v1")["data"]["unresolvedObservations"] == []
        assert instance.inspect("capture.context.v1")["lifecycleEvidence"]["clientEnableStateExact"] == {"state": "ready", "reason": None}
    finally:
        instance.close()


@pytest.mark.parametrize("boundary", ["finish", "reset", "left-view"])
def test_late_enable_cannot_revive_finished_or_context_unknown_effect(boundary):
    instance = make_enable_broker()
    uid = (1 << 61) + 55
    try:
        add(instance, uid=uid, target=100)
        if boundary == "finish":
            emit(instance, "BattleOpFinishBuff", operation_owner_id=100, buff_inst_id=uid, reduce_layer=1, force=True)
        elif boundary == "reset":
            emit(instance, "SC_RESET_BATTLE_STATUS", battle_inst_id=100)
        else:
            emit(instance, "SC_OBJECT_LEAVE_VIEW", objects=[{"battle_inst_id": 100}])
        before = effects(instance)[0]
        emit(instance, "BattleOpSetBuffEnableState", owner_id=100, generation=1, states={str(uid): True})
        assert effects(instance)[0] == before
        unresolved = instance.inspect("effects.actual.v1")["data"]["unresolvedObservations"]
        assert unresolved[-1]["reason"] == "instance_context_unavailable"
    finally:
        instance.close()


@pytest.mark.parametrize("fields", [{}, {"owner_id": 101}, {"operation_owner_id": 100}, {"owner_id": "not-battle-id"}])
def test_enable_state_needs_exact_wire_owner_and_observed_target_uid(fields):
    instance = make_enable_broker()
    uid = (1 << 61) + 55
    try:
        add(instance, uid=uid, target=100)
        emit(instance, "BattleOpSetBuffEnableState", generation=1, states={str(uid): True}, **fields)
        assert effects(instance)[0]["enabled"] is None
        assert instance.inspect("effects.actual.v1")["data"]["unresolvedObservations"]
    finally:
        instance.close()


@pytest.mark.parametrize("uid", [1, (1 << 60) + 55, "not-a-client-uid"])
def test_enable_state_does_not_claim_unproved_uid_namespace(uid):
    instance = make_enable_broker()
    try:
        add(instance, uid=uid, target=100)
        emit(instance, "BattleOpSetBuffEnableState", owner_id=100, generation=1, states={str(uid): False})
        assert effects(instance)[0]["enabled"] is None
        assert instance.inspect("effects.actual.v1")["data"]["unresolvedObservations"]
    finally:
        instance.close()


@pytest.mark.parametrize("invalid", ["hashes", "supported", "targetField", "uidRequiredMask", "proofSha256"])
def test_enable_state_requires_its_own_exact_version_bound_capability(invalid):
    contract = enable_native_contract()
    if invalid == "hashes":
        installed = {}
    else:
        installed = None
        contract["capabilities"]["clientEnableStateExact"][invalid] = "invalid"
    instance = make_enable_broker(installed_hashes=installed, contract=contract)
    uid = (1 << 61) + 55
    try:
        add(instance, uid=uid, target=100)
        emit(instance, "BattleOpSetBuffEnableState", owner_id=100, generation=1, states={str(uid): False})
        assert effects(instance)[0]["enabled"] is None
        gate = instance.inspect("capture.context.v1")["lifecycleEvidence"]["clientEnableStateExact"]
        assert gate["state"] == "unavailable"
        assert gate["reason"]
        assert instance.inspect("effects.actual.v1")["data"]["unresolvedObservations"]
    finally:
        instance.close()


def test_enable_state_missing_creation_is_unresolved_and_does_not_create_an_effect():
    instance = make_enable_broker()
    try:
        emit(instance, "BattleOpSetBuffEnableState", owner_id=100, generation=1, states={str((1 << 61) + 55): True})
        data = instance.inspect("effects.actual.v1")["data"]
        assert data["effects"] == []
        assert data["unresolvedObservations"][0]["reason"] == "creation_not_observed"
    finally:
        instance.close()


def test_only_game_icons_filters_on_confirmed_metadata_not_names_or_image_files(broker):
    broker.configure_catalog([{"canonicalId": "visible", "numericIds": ["700"], "hasIcon": True},
                              {"canonicalId": "not_visible", "hasIcon": False,
                               "icon": "data:image/png;base64,AAAA", "label": "有名字但不是游戏图标"}])
    add(broker, uid=1, effect="visible")
    add(broker, uid=2, effect="not_visible")
    add(broker, uid=3, effect="not_in_catalog")
    data = broker.inspect("effects.actual.v1", {"filter": {"onlyGameIcons": True}})["data"]
    assert [row["effectId"] for row in data["effects"]] == ["visible"]
    assert data["scopeEffectCount"] == 3
    assert data["gameIconFilteredCount"] == 2
    assert data["deliveredEffectCount"] == 1
    assert len(effects(broker)) == 3


def test_game_icon_projection_is_after_exact_target_and_id_scope(broker):
    broker.configure_catalog([{"canonicalId": "visible", "hasIcon": True},
                              {"canonicalId": "internal", "hasIcon": False}])
    add(broker, uid=1, effect="visible", target=100)
    add(broker, uid=2, effect="internal", target=100)
    add(broker, uid=3, effect="internal", target=101)
    data = broker.inspect("effects.actual.v1", {"filter": {"targetIds": ["100"], "onlyGameIcons": True}})["data"]
    assert data["scopeEffectCount"] == 2
    assert data["gameIconFilteredCount"] == 1
    assert data["deliveredEffectCount"] == 1
    assert data["effects"][0]["targetId"] == "100"


def test_only_game_icons_is_part_of_the_shared_projection_cache_key(monkeypatch):
    instance = OverlayTopicBroker(publish_interval_ms=60_000, catalog=[
        {"canonicalId": "visible", "hasIcon": True}, {"canonicalId": "internal", "hasIcon": False}])
    instance.on_state_change("live", "capture-1")
    all_rows, icon_rows = [], []
    instance.subscribe("effects.actual.v1", {}, all_rows.append)
    instance.subscribe("effects.actual.v1", {"filter": {"onlyGameIcons": True}}, icon_rows.append)
    original_build = instance._build
    calls = []

    def count_build(topic, normalized):
        calls.append(normalized.get("onlyGameIcons", False))
        return original_build(topic, normalized)

    monkeypatch.setattr(instance, "_build", count_build)
    add(instance, uid=1, effect="visible")
    add(instance, uid=2, effect="internal")
    instance.flush()
    assert calls == [False, True]
    assert len(all_rows[-1]["data"]["effects"]) == 2
    assert len(icon_rows[-1]["data"]["effects"]) == 1
    instance.close()


@pytest.mark.parametrize("next_event", ["SC_OBJECT_ENTER_VIEW", "SC_EFFECT_SNAPSHOT", "SC_SELF_SCENE_INFO"])
def test_exact_object_reassignment_invalidates_old_battle_target(broker, next_event):
    emit(broker, "SC_OBJECT_ENTER_VIEW", objects=[{"obj_id": "999", "battle_inst_id": "100"}])
    add(broker, uid=1, target=100)
    if next_event == "SC_OBJECT_ENTER_VIEW":
        emit(broker, next_event, objects=[{"obj_id": "999", "battle_inst_id": "101"}])
    elif next_event == "SC_EFFECT_SNAPSHOT":
        emit(broker, next_event, owners=[{"owner_obj_id": "999", "owner_battle_inst_id": "101", "generation": 0, "buffs": []}])
    else:
        emit(broker, next_event, char_list=[{"id": "999", "battle_inst_id": "101"}])
    targets = {row["id"]: row for row in broker.inspect("targets.current.v1")["data"]["targets"]}
    assert targets["100"]["present"] is False
    assert targets["100"]["absenceReason"] == "target-instance-reassigned"
    assert targets["101"]["present"] is True
    row = effects(broker)[0]
    assert row["state"] == "context-unknown"
    assert row["closureReason"] == "target-instance-reassigned"
    assert row["removedAtMs"] is None


def test_raw_snapshot_originals_are_not_modified(broker):
    event = {"type": "SC_EFFECT_SNAPSHOT", "session_id": "capture-1", "timestamp_ms": 10_000,
             "owners": [{"owner_battle_inst_id": 100, "generation": 0,
                         "buffs": [{"inst_id": 1, "buff_id": {"str_id": "buff_exact"}, "duration": 123}]}]}
    original = deepcopy(event)
    broker.on_event(event)
    assert event == original


def test_singleton_loads_official_target_labels_without_inventing_alignment(monkeypatch):
    from endfield_pcap import overlay_effect_catalog, overlay_topics

    monkeypatch.setattr(overlay_topics, "_singleton", None)
    monkeypatch.setattr(overlay_effect_catalog, "load_effect_catalog", lambda: [])
    monkeypatch.setattr(overlay_effect_catalog, "load_target_catalog", lambda: {"enemy_exact": {"label": "准确名称"}})
    instance = overlay_topics.get_overlay_broker()
    assert instance is overlay_topics.get_overlay_broker()
    instance.on_state_change("live", "capture-1")
    emit(instance, "SC_OBJECT_ENTER_VIEW", objects=[{"battle_inst_id": 100, "templateid": "enemy_exact", "kind": "monster"}])
    target = instance.inspect("targets.current.v1")["data"]["targets"][0]
    assert target["label"] == "准确名称"
    assert target["isEnemy"] is target["isBoss"] is None
    instance.close()


def test_snapshot_unknown_targets_do_not_merge_separate_object_owners(broker):
    emit(broker, "SC_EFFECT_SNAPSHOT", owners=[
        {"owner_obj_id": "901", "owner_battle_inst_id": None, "generation": 0,
         "buffs": [{"inst_id": "1", "buff_id": {"str_id": "buff_exact"}}]},
        {"owner_obj_id": "902", "owner_battle_inst_id": None, "generation": 0,
         "buffs": [{"inst_id": "1", "buff_id": {"str_id": "buff_exact"}}]},
    ])
    rows = effects(broker)
    assert len(rows) == 2
    assert all(row["targetId"] is None for row in rows)
    assert len({row["key"] for row in rows}) == 2


def test_snapshot_without_any_owner_identity_does_not_merge_uids(broker):
    emit(broker, "SC_EFFECT_SNAPSHOT", owners=[
        {"generation": 0, "buffs": [{"inst_id": "1", "buff_id": {"str_id": "buff_exact"}}]},
        {"generation": 0, "buffs": [{"inst_id": "1", "buff_id": {"str_id": "buff_exact"}}]},
    ])
    assert len(effects(broker)) == 2


def test_signed_numeric_buff_template_is_preserved(broker):
    emit(broker, "BattleOpAddBuff", buff_inst_id=1, int_id=-123, target_inst_id=100)
    assert effects(broker)[0]["effectId"] == "-123"


def test_definitions_are_available_disconnected_and_keep_legacy_catalog_separate():
    instance = OverlayTopicBroker(catalog=[{"canonicalId": "one", "label": "旧目录"}], definitions=[
        {"effectId": "one", "canonicalId": "one", "label": "准确标签", "description": "完整文案",
         "definitionOnly": True, "staticConfig": {"duration": {"value": 12}},
         "details": {"sourcePaths": ["exact.official.path"]}, "sourceRelations": []}])
    try:
        result = instance.inspect("effects.definitions.v1")
        assert result["state"] == "ready"
        assert result["data"]["scope"] == "static-configuration"
        assert result["data"]["runtimeValues"] == "not_provided"
        assert "details" not in result["data"]["definitions"][0]
        exact = instance.inspect("effects.definitions.v1", {"filter": {"effectIds": ["one"]}})
        assert exact["data"]["definitions"][0]["details"]["sourcePaths"] == ["exact.official.path"]
        assert instance.inspect("effects.catalog.v1")["data"]["effects"][0]["label"] == "旧目录"
        assert instance.inspect("effects.actual.v1")["data"] is None
        topic = next(row for row in instance.get_topics() if row["topic"] == "effects.definitions.v1")
        assert topic["readOnly"] is True and topic["capabilities"]["editing"] is False
        assert set(topic["capabilities"]["filters"]) == {"query", "sourceTypes", "sourceIds", "effectIds", "limit", "offset"}
        exact["data"]["definitions"][0]["details"]["sourcePaths"].clear()
        assert instance.inspect("effects.definitions.v1", {"effectIds": ["one"]})["data"]["definitions"][0]["details"]["sourcePaths"]
    finally:
        instance.close()


def test_definition_filters_match_explicit_source_relation_and_return_pages():
    definitions = [
        {"effectId": "one", "label": "中文准确标签", "sourceRelations": [
            {"sourceType": "weapon", "sourceId": "weapon_a", "sourceName": "Exact Weapon"},
            {"sourceType": "character", "sourceId": "chr_a"}]},
        {"effectId": "two", "label": "第二项", "sourceRelations": [{"sourceType": "weapon", "sourceId": "weapon_b"}]},
        {"effectId": "three", "label": "第三项", "sourceRelations": []},
    ]
    instance = OverlayTopicBroker(definitions=definitions)
    try:
        first = instance.inspect("effects.definitions.v1", {"limit": 1})["data"]
        assert [row["effectId"] for row in first["definitions"]] == ["one"]
        assert (first["offset"], first["total"], first["nextOffset"], first["hasMore"]) == (0, 3, 1, True)
        last = instance.inspect("effects.definitions.v1", {"offset": 2, "limit": 1})["data"]
        assert [row["effectId"] for row in last["definitions"]] == ["three"]
        assert last["nextOffset"] is None and last["hasMore"] is False
        assert instance.inspect("effects.definitions.v1", {"offset": 100})["data"]["definitions"] == []
        assert [row["effectId"] for row in instance.inspect("effects.definitions.v1", {"query": "  EXACT wEaPoN  "})["data"]["definitions"]] == ["one"]
        assert instance.inspect("effects.definitions.v1", {"sourceTypes": ["weapon"], "sourceIds": ["chr_a"]})["data"]["definitions"] == []
        assert [row["effectId"] for row in instance.inspect("effects.definitions.v1", {"sourceTypes": ["weapon"], "sourceIds": ["weapon_b"]})["data"]["definitions"]] == ["two"]
    finally:
        instance.close()


@pytest.mark.parametrize("options", [
    {"limit": 0}, {"limit": 101}, {"limit": True}, {"offset": -1}, {"offset": 1_000_001},
    {"query": "x" * 257}, {"query": []}, {"sourceTypes": "weapon"},
    {"sourceIds": ["x"] * 257}, {"sourceTypes": [None]},
])
def test_definition_query_options_are_bounded(options):
    instance = OverlayTopicBroker()
    try:
        with pytest.raises(ValueError):
            instance.inspect("effects.definitions.v1", options)
    finally:
        instance.close()


def test_definition_subscription_projection_key_includes_every_query_and_page_option(monkeypatch):
    instance = OverlayTopicBroker(publish_interval_ms=60_000, definitions=[
        {"effectId": "one", "label": "alpha", "sourceRelations": [{"sourceType": "weapon", "sourceId": "weapon_a"}]},
        {"effectId": "two", "label": "beta", "sourceRelations": [{"sourceType": "character", "sourceId": "chr_a"}]},
    ])
    outputs = [[] for _ in range(7)]
    options = [{"query": " ALPHA "}, {"query": "alpha"}, {"sourceTypes": ["character"]},
               {"sourceIds": ["weapon_a"]}, {"effectIds": ["two"]}, {"limit": 1}, {"offset": 1}]
    try:
        for output, option in zip(outputs, options):
            instance.subscribe("effects.definitions.v1", option, output.append)
        builds = []
        original = instance._build

        def count_build(topic, normalized):
            builds.append(topic)
            return original(topic, normalized)

        monkeypatch.setattr(instance, "_build", count_build)
        instance.configure_catalog([], definitions=instance._definitions)
        assert len(builds) == 6
        assert [row["effectId"] for row in outputs[0][-1]["data"]["definitions"]] == ["one"]
        assert [row["effectId"] for row in outputs[2][-1]["data"]["definitions"]] == ["two"]
        outputs[0][-1]["data"]["definitions"][0]["label"] = "mutation"
        assert outputs[1][-1]["data"]["definitions"][0]["label"] == "alpha"
    finally:
        instance.close()


def test_definition_payload_budget_and_page_icons_are_bounded():
    icon = "data:image/webp;base64," + "A" * 60_000
    instance = OverlayTopicBroker(catalog=[{"canonicalId": f"buff_{index}", "icon": icon} for index in range(30)],
                                  definitions=[{"effectId": f"buff_{index}", "label": "x" * 80_000,
                                                "sourceRelations": [], "details": {"long": "d" * 100_000}}
                                               for index in range(30)])
    try:
        envelope = instance.inspect("effects.definitions.v1", {"limit": 100})
        page = envelope["data"]
        assert 0 < len(page["definitions"]) < 30
        assert page["paginationReason"] == "payload_budget"
        assert page["nextOffset"] == len(page["definitions"])
        assert len(page["icons"]) == 1
        assert len(json.dumps(envelope, ensure_ascii=False).encode("utf-8")) < 2 * 1024 * 1024
        assert all("details" not in row for row in page["definitions"])
        exact = instance.inspect("effects.definitions.v1", {"effectIds": ["buff_0"]})["data"]["definitions"][0]
        assert len(exact["details"]["long"]) == 100_000
    finally:
        instance.close()


def test_definitions_load_once_only_after_the_first_definition_query():
    loaded = []

    def loader():
        loaded.append(True)
        return [{"effectId": "one", "label": "first", "sourceRelations": []}]

    instance = OverlayTopicBroker(definitions_loader=loader)
    try:
        instance.get_topics()
        instance.on_state_change("live", "capture-1")
        add(instance)
        instance.inspect("effects.actual.v1")
        instance.inspect("effects.catalog.v1")
        assert loaded == []
        first = instance.inspect("effects.definitions.v1")
        assert len(loaded) == 1
        first["data"]["definitions"][0]["label"] = "mutated"
        output = []
        instance.subscribe("effects.definitions.v1", {}, output.append)
        assert len(loaded) == 1
        assert output[0]["data"]["definitions"][0]["label"] == "first"
        instance.on_state_change("waiting_connection")
        assert instance.inspect("effects.definitions.v1")["state"] == "ready"
        assert len(loaded) == 1
    finally:
        instance.close()


def test_missing_definitions_do_not_break_capture_or_repeat_the_loader():
    calls = []

    def missing():
        calls.append(True)
        raise OSError("fixture missing")

    instance = OverlayTopicBroker(definitions_loader=missing)
    try:
        assert instance.inspect("effects.definitions.v1")["reason"] == "definition_catalog_unavailable"
        instance.on_state_change("live", "capture-1")
        add(instance)
        assert len(effects(instance)) == 1
        instance.inspect("effects.definitions.v1")
        assert len(calls) == 1
    finally:
        instance.close()


@pytest.mark.parametrize("access", ["inspect", "subscribe"])
def test_slow_definition_loader_does_not_hold_capture_lock_or_block_subscription(access):
    started, release, returned, event_processed, ready = (threading.Event() for _ in range(5))
    output = []

    def slow_loader():
        started.set()
        assert release.wait(3)
        return [{"effectId": "one", "sourceRelations": []}]

    instance = OverlayTopicBroker(definitions_loader=slow_loader, publish_interval_ms=0)
    instance.on_state_change("live", "capture-1")

    def observe(envelope):
        output.append(envelope)
        if envelope["state"] == "ready":
            ready.set()

    def request():
        if access == "inspect":
            observe(instance.inspect("effects.definitions.v1"))
        else:
            instance.subscribe("effects.definitions.v1", {}, observe)
        returned.set()

    def capture():
        add(instance)
        event_processed.set()

    requester = threading.Thread(target=request)
    capture_thread = threading.Thread(target=capture)
    try:
        requester.start()
        assert started.wait(1)
        if access == "subscribe":
            assert returned.wait(1)
            assert output[0]["reason"] == "definitions_loading"
        capture_thread.start()
        assert event_processed.wait(1), "definition parsing held the capture state lock"
        assert len(effects(instance)) == 1
        assert not ready.is_set()
        release.set()
        assert ready.wait(2)
        assert output[-1]["data"]["definitions"][0]["effectId"] == "one"
    finally:
        release.set()
        requester.join(3)
        if capture_thread.ident is not None:
            capture_thread.join(3)
        instance.close()


def test_actual_distinguishes_unknown_and_unsupported_values_from_static_configuration(broker):
    add(broker)
    row = effects(broker)[0]
    assert all(row[name] is None for name in ("stackCount", "durationMs", "expiresAtMs", "remainingMs", "enabled"))
    assert row["fieldAvailability"]["label"]["state"] == "known"
    assert row["fieldAvailability"]["stackCount"] == {
        "state": "unsupported", "source": None, "reason": "stack_count_semantics_unverified"}
    assert row["fieldAvailability"]["remainingMs"] == {
        "state": "unsupported", "source": None, "reason": "duration_clock_anchor_unverified"}
    assert row["configuration"]["scope"] == "static-configuration"
    assert row["configuration"]["definitionId"] == "buff_exact"
    assert row["runtimeValues"]["variables"] is None
    assert row["initializationParameters"]["assignedItems"] is None
    assert row["initializationParameters"]["fieldAvailability"]["assignedItems"]["state"] == "unknown"
    assert row["lifecycle"]["state"] == "observed-open"


def test_create_initial_assignments_retain_wire_fields_without_synthesizing_current_values(broker):
    assigned = [{"target_key": "duration", "input_value_key": "incoming_duration"},
                {"target_key": "rate", "use_direct_value": True, "numeric_value": 0},
                {"target_key": "element", "string_value": "Fire"}]
    emit(broker, "BattleOpTriggerAction", trigger_type=0, action_runtime_case="create_buff_action",
         action={"action_type": "BattleActionCreateBuff", "create_buff_action": {"details": [
             {"buff_inst_id": "91", "buff_num_id": 700, "target_id": "100", "source_id": "200",
              "buff_input_index": 0, "assigned_items": assigned}]}})
    assigned[1]["numeric_value"] = 999
    row = effects(broker)[0]
    initial = row["initializationParameters"]
    assert "numeric_value" not in initial["assignedItems"][0]
    assert initial["assignedItems"][1]["numeric_value"] == 0
    assert initial["buffInputIndex"] == 0
    assert initial["currentValues"] is False
    assert initial["assignedItemFieldSources"][0]["fields"]["target_key"] == "BATTLE_CREATE_BUFF_ACTION_DETAIL.assigned_items[0].target_key"
    assert initial["assignedItemFieldSources"][1]["numericValueUnit"] is None
    assert row["durationMs"] is None and row["runtimeValues"]["variables"] is None
    assert row["raw"]["assignedItems"] == initial["assignedItems"]


def test_add_buff_initial_assignments_are_distinct_from_unobserved_initialization(broker):
    add(broker, assigned_items=[])
    initial = effects(broker)[0]["initializationParameters"]
    assert initial["assignedItems"] == []
    assert initial["fieldAvailability"]["assignedItems"] == {
        "state": "known", "source": "BATTLE_ADD_BUFF_OP_DATA.assigned_items", "reason": None}


def generic_native_contract():
    contract = enable_native_contract()
    contract["capabilities"]["clientActionFinishExact"] = {
        "supported": True, "namespace": "client_buff", "uidRequiredMask": "0x2000000000000000",
        "actionType": 3, "actionRuntimeCase": "finish_buff_action", "triggerType": 0,
        "targetField": "target_id", "uidField": "buff_inst_id", "matchScope": "same_capture_scene_target_uid",
        "closureReason": "client-action-finish-confirmed", "proofSha256": "f" * 64}
    contract["capabilities"]["clientKeywordVariablesExact"] = {
        "supported": True, "namespace": "client_buff", "uidRequiredMask": "0x2000000000000000",
        "targetField": "owner_id", "uidField": "buff_inst_id", "updatesField": "modify",
        "configIndexField": "config_index", "valueField": "modify_value", "variableKey": "rate",
        "valueSemantics": "absolute_after_assignment", "indexSemantics": "zero_based_keyword_enhance_list",
        "matchScope": "same_capture_scene_target_uid", "proofSha256": "1" * 64}
    contract["capabilities"]["clientOperationSequenceExact"] = {
        "supported": True, "namespace": "client_battle_operation", "sequenceField": "seq_id", "sequenceType": "int32",
        "sequenceScope": "same_gameplay_network_all_owners", "matchScope": "same_capture_scene_context",
        "increment": 1, "wireSequenceDensity": "sparse", "proofSha256": "2" * 64}
    return contract


def indexed_header_fields(seq, source_index, *, health=None):
    """Controlled observer metadata, not an original captured Proto digest."""
    return {"seq_id": seq, "source_index": source_index,
            "operation_sha256": hashlib.sha256(f"controlled-header:{seq}:{source_index}".encode()).hexdigest(),
            "capture_integrity": ({"packetsDroppedQueue": 0, "decompressionErrors": 0,
                                   "protobufDecodeErrors": 0, "framingErrors": 0,
                                   "pendingMultipacks": 0, "pendingTransportSegments": 0, "currentFrameErrors": 0} if health is None else health),
            "operation_type": "BattleOpUpdateAtbInfo", "op_type": 4}


def test_indexed_source_forward_game_numbering_skips_are_diagnostic_not_capture_gaps():
    instance = make_enable_broker(contract=generic_native_contract())
    health = {"packetsDroppedQueue": 3, "decompressionErrors": 2, "protobufDecodeErrors": 1,
              "framingErrors": 4, "pendingMultipacks": 0, "pendingTransportSegments": 0, "currentFrameErrors": 0}
    try:
        for source_index, seq in enumerate([0, 1, 5, 8, 12, 13], 501):
            emit(instance, "CAPTURE_BATTLE_OPERATION", **indexed_header_fields(seq, source_index, health=health))
        data = instance.inspect("effects.operations.v1")["data"]
        assert data["continuity"]["basis"] == "source-index-and-capture-integrity"
        assert data["continuity"]["state"] == "contiguous" and data["gapRevision"] == 0
        assert data["continuity"]["lastSourceIndex"] == 506
        assert [row["sequenceRelation"] for row in data["operations"]] == ["first_observation", "next", "forward_skip", "forward_skip", "forward_skip", "next"]
        assert [row["captureSequenceRelation"] for row in data["operations"]] == ["first_observation", *(["next"] * 5)]
        assert data["captureIntegrity"]["state"] == "stable"
        assert data["captureIntegrity"]["baseline"] == data["captureIntegrity"]["current"] == health
        assert data["captureIntegrity"]["revision"] == 0 and data["captureIntegrity"]["afterIndex"] is None
    finally:
        instance.close()


@pytest.mark.parametrize("second_index,relation", [(12, "gap"), (10, "repeated"), (9, "backward")])
def test_indexed_source_index_discontinuity_is_not_waived_for_valid_sparse_game_numbers(second_index, relation):
    instance = make_enable_broker(contract=generic_native_contract())
    try:
        emit(instance, "CAPTURE_BATTLE_OPERATION", **indexed_header_fields(124, 10))
        emit(instance, "CAPTURE_BATTLE_OPERATION", **indexed_header_fields(129, second_index))
        data = instance.inspect("effects.operations.v1")["data"]
        assert data["operations"][-1]["sequenceRelation"] == "forward_skip"
        assert data["operations"][-1]["captureSequenceRelation"] == relation
        assert data["gapRevision"] == 1 and data["continuity"]["lastGapIndex"] == 2
        assert data["continuity"]["state"] == "gap"
    finally:
        instance.close()


@pytest.mark.parametrize("second_seq,relation", [(124, "repeated"), (123, "backward")])
def test_indexed_source_does_not_deduplicate_repeated_game_seq_even_with_equal_digest(second_seq, relation):
    instance = make_enable_broker(contract=generic_native_contract())
    try:
        first = indexed_header_fields(124, 10)
        emit(instance, "CAPTURE_BATTLE_OPERATION", **first)
        second = indexed_header_fields(second_seq, 11)
        second["operation_sha256"] = first["operation_sha256"]
        emit(instance, "CAPTURE_BATTLE_OPERATION", **second)
        data = instance.inspect("effects.operations.v1")["data"]
        assert data["operations"][-1]["captureSequenceRelation"] == "next"
        assert data["operations"][-1]["sequenceRelation"] == relation
        assert data["lastObservedIndex"] == 2 and data["gapRevision"] == 1
    finally:
        instance.close()


@pytest.mark.parametrize("field,value", [("source_index", None), ("source_index", 0), ("source_index", True),
                                         ("source_index", 1.0), ("operation_sha256", "x" * 64),
                                         ("operation_sha256", None), ("capture_integrity", None)])
def test_indexed_source_invalid_metadata_cannot_fall_back_to_dense_legacy(field, value):
    instance = make_enable_broker(contract=generic_native_contract())
    try:
        emit(instance, "CAPTURE_BATTLE_OPERATION", **indexed_header_fields(100, 10))
        bad = indexed_header_fields(105, 11)
        bad[field] = value
        emit(instance, "CAPTURE_BATTLE_OPERATION", **bad)
        data = instance.inspect("effects.operations.v1")["data"]
        assert data["continuity"]["basis"] == "source-index-and-capture-integrity"
        assert data["operations"][-1]["captureSequenceRelation"] == "missing_metadata"
        assert data["continuity"]["state"] == "unavailable"
        assert data["gapRevision"] == 1 and data["continuity"]["lastGapIndex"] == 2
    finally:
        instance.close()


@pytest.mark.parametrize("field", ["packetsDroppedQueue", "decompressionErrors", "protobufDecodeErrors", "framingErrors",
                                  "pendingMultipacks", "pendingTransportSegments", "currentFrameErrors"])
def test_indexed_source_all_seven_capture_health_fields_are_required(field):
    instance = make_enable_broker(contract=generic_native_contract())
    try:
        bad = indexed_header_fields(100, 1)
        bad["capture_integrity"].pop(field)
        emit(instance, "CAPTURE_BATTLE_OPERATION", **bad)
        data = instance.inspect("effects.operations.v1")["data"]
        assert data["captureIntegrity"]["state"] == "unavailable"
        assert data["captureIntegrity"]["current"] is None
        assert data["continuity"]["state"] == "unavailable" and data["gapRevision"] == 1
    finally:
        instance.close()


@pytest.mark.parametrize("field", ["packetsDroppedQueue", "decompressionErrors", "protobufDecodeErrors", "framingErrors"])
def test_indexed_source_counter_change_closes_old_segment_but_allows_new_stable_segment(field):
    instance = make_enable_broker(contract=generic_native_contract())
    try:
        initial = indexed_header_fields(100, 500)
        emit(instance, "CAPTURE_BATTLE_OPERATION", **initial)
        changed = indexed_header_fields(105, 501)
        changed["capture_integrity"][field] = 1
        emit(instance, "CAPTURE_BATTLE_OPERATION", **changed)
        data = instance.inspect("effects.operations.v1")["data"]
        assert data["captureIntegrity"]["state"] == "changed"
        assert data["captureIntegrity"]["changedFields"] == [field]
        assert data["captureIntegrity"]["revision"] == 1 and data["captureIntegrity"]["afterIndex"] == 2
        assert data["operations"][-1]["captureSequenceRelation"] == "integrity_changed"
        assert data["gapRevision"] == 1 and data["continuity"]["lastGapIndex"] == 2
        healthy = indexed_header_fields(110, 502, health=changed["capture_integrity"])
        emit(instance, "CAPTURE_BATTLE_OPERATION", **healthy)
        stable = instance.inspect("effects.operations.v1")["data"]
        assert stable["captureIntegrity"]["state"] == "stable"
        assert stable["captureIntegrity"]["baseline"][field] == 1
        assert stable["captureIntegrity"]["revision"] == 1 and stable["captureIntegrity"]["afterIndex"] == 2
        assert stable["gapRevision"] == 1 and stable["continuity"]["lastGapIndex"] == 2
        assert stable["operations"][-1]["index"] > stable["continuity"]["lastGapIndex"]
    finally:
        instance.close()


@pytest.mark.parametrize("field", ["pendingMultipacks", "pendingTransportSegments", "currentFrameErrors"])
def test_indexed_source_pending_gauges_require_zero_and_do_not_mistake_recovery_for_counter_loss(field):
    instance = make_enable_broker(contract=generic_native_contract())
    try:
        for source_index, pending in enumerate([0, 1, 1, 0], 1):
            header = indexed_header_fields(source_index * 3, source_index)
            header["capture_integrity"][field] = pending
            emit(instance, "CAPTURE_BATTLE_OPERATION", **header)
        data = instance.inspect("effects.operations.v1")["data"]
        assert data["captureIntegrity"]["state"] == "stable"
        assert data["captureIntegrity"]["baseline"][field] == 0
        assert data["captureIntegrity"]["revision"] == 2 and data["captureIntegrity"]["afterIndex"] == 3
        assert data["gapRevision"] == 2 and data["continuity"]["lastGapIndex"] == 3
        assert [row["captureSequenceRelation"] for row in data["operations"]] == ["first_observation", "pending", "pending", "next"]
    finally:
        instance.close()


def test_damaged_compound_frame_cannot_recalibrate_after_the_counter_baseline_has_rolled():
    instance = make_enable_broker(contract=generic_native_contract())
    try:
        emit(instance, "CAPTURE_BATTLE_OPERATION", **indexed_header_fields(10, 1))
        bad_health = indexed_header_fields(11, 2)["capture_integrity"]
        bad_health.update(framingErrors=1, currentFrameErrors=1)
        emit(instance, "CAPTURE_BATTLE_OPERATION", **indexed_header_fields(11, 2, health=bad_health))
        clear = indexed_header_fields(20, 3, health=bad_health)
        clear.update(operation_type="BattleOpTriggerAction", op_type=8)
        emit(instance, "CAPTURE_BATTLE_OPERATION", **clear)
        emit(instance, "BattleOpTriggerAction", seq_id=20, trigger_type=0, action_runtime_case="finish_buff_action",
             operation_owner_id=100, inst_id=123, template_type="Buff", template_int_id=700,
             action={"action_type": 3, "action_id": 1, "finish_buff_action": {"finish_layer_cnt": 0,
                       "finish_buffs": [{"target_id": "100", "buff_inst_id": "999"}]}})
        damaged = instance.inspect("effects.operations.v1")["data"]
        clear_record = damaged["operations"][-1]
        assert clear_record["payloadState"] == "whitelisted-fields"
        assert clear_record["captureIntegrityState"] == "pending"
        assert clear_record["captureSequenceRelation"] == "pending"
        assert clear_record["index"] <= damaged["continuity"]["lastGapIndex"] == 3
        assert damaged["captureIntegrity"]["state"] == "pending"
        assert damaged["captureIntegrity"]["baseline"]["framingErrors"] == 1
        assert damaged["captureIntegrity"]["revision"] == 2
        healthy = dict(bad_health, currentFrameErrors=0)
        emit(instance, "CAPTURE_BATTLE_OPERATION", **indexed_header_fields(21, 4, health=healthy))
        recovered = instance.inspect("effects.operations.v1")["data"]
        assert recovered["captureIntegrity"]["state"] == "stable"
        assert recovered["captureIntegrity"]["revision"] == 2
        assert recovered["captureIntegrity"]["afterIndex"] == recovered["continuity"]["lastGapIndex"] == 3
        assert recovered["operations"][-1]["index"] > recovered["continuity"]["lastGapIndex"]
    finally:
        instance.close()


def test_legacy_raw_and_normalized_bases_remain_explicit_and_conservative():
    for raw, basis in [(True, "legacy-dense-game-sequence"), (False, "normalized-dense-game-sequence")]:
        instance = make_enable_broker(contract=generic_native_contract())
        try:
            if raw:
                emit(instance, "CAPTURE_BATTLE_OPERATION", seq_id=124, operation_type="BattleOpUpdateAtbInfo", op_type=4)
                emit(instance, "CAPTURE_BATTLE_OPERATION", seq_id=129, operation_type="BattleOpUpdateAtbInfo", op_type=4)
            else:
                emit(instance, "BattleOpUpdateAtbInfo", seq_id=124)
                emit(instance, "BattleOpUpdateAtbInfo", seq_id=129)
            data = instance.inspect("effects.operations.v1")["data"]
            assert data["continuity"]["basis"] == basis
            assert data["gapRevision"] == 1 and data["operations"][-1]["sequenceRelation"] == "gap"
        finally:
            instance.close()


def test_indexed_source_missing_every_new_field_does_not_downgrade_and_upgrade_starts_new_domain():
    instance = make_enable_broker(contract=generic_native_contract())
    try:
        emit(instance, "CAPTURE_BATTLE_OPERATION", seq_id=1, operation_type="BattleOpUpdateAtbInfo", op_type=4)
        old_stream = instance.inspect("effects.operations.v1")["data"]["streamRevision"]
        emit(instance, "CAPTURE_BATTLE_OPERATION", **indexed_header_fields(2, 100))
        upgraded = instance.inspect("effects.operations.v1")["data"]
        assert upgraded["streamRevision"] == old_stream + 1 and upgraded["lastObservedIndex"] == 1
        assert upgraded["operations"][0]["captureSequenceRelation"] == "source_mode_changed"
        emit(instance, "CAPTURE_BATTLE_OPERATION", seq_id=3, operation_type="BattleOpUpdateAtbInfo", op_type=4)
        missing = instance.inspect("effects.operations.v1")["data"]
        assert missing["streamRevision"] == upgraded["streamRevision"]
        assert missing["continuity"]["basis"] == "source-index-and-capture-integrity"
        assert missing["operations"][-1]["captureSequenceRelation"] == "missing_metadata"
        assert missing["continuity"]["state"] == "unavailable"
    finally:
        instance.close()


def test_operation_headers_publish_when_actual_state_has_not_changed():
    instance = make_enable_broker(contract=generic_native_contract())
    received = []
    try:
        instance.subscribe("effects.operations.v1", {}, received.append)
        assert received[-1]["state"] == "unavailable"
        emit(instance, "BattleOpUpdateAtbInfo", seq_id=100, account_email="developer@example.com", raw_body_hex="SECRET",
             owner_id=999, network={"address": "PRIVATE"})
        data = received[-1]["data"]
        assert data["continuity"]["state"] == "contiguous"
        assert data["sourceMode"] == "normalized-only" and data["streamRevision"] == 1
        assert data["operations"] == [{"index": 1, "seqId": 100, "type": "BattleOpUpdateAtbInfo", "atMs": 10_000,
                                       "sequenceRelation": "first_observation", "payloadState": "header-only",
                                       "captureSequenceRelation": "first_observation", "sourceIndex": None,
                                       "operationSha256": None, "captureIntegrity": None,
                                       "captureIntegrityState": "unavailable", "captureIntegrityRevision": 0,
                                       "payloadReason": None, "payload": None, "payloadRevision": 0, "opType": None}]
        assert data["coverage"] == "observed-only" and data["continuity"]["initialGameState"] == "unknown"
        assert instance.inspect("effects.actual.v1")["state"] == "unavailable"
        assert instance._observed_at is None
        assert "PRIVATE" not in json.dumps(data) and "SECRET" not in json.dumps(data)
        assert len(received) == 2
    finally:
        instance.close()


def test_operations_native_gate_is_independent_of_other_lifecycle_capabilities():
    instance = make_enable_broker()
    try:
        emit(instance, "BattleOpSkillAttach", seq_id=1)
        emit(instance, "BattleOpSkillDetach", seq_id=2)
        data = instance.inspect("effects.operations.v1")["data"]
        assert data["continuity"]["state"] == "unavailable"
        assert data["continuity"]["reason"] == "native_operation_sequence_unverified"
        assert data["gapRevision"] == 0
    finally:
        instance.close()


def test_public_native_binding_reports_actual_installed_hashes_and_never_contract_expectations():
    contract = generic_native_contract()
    actual = {"gameAssemblySha256": "C" * 64, "metadataSha256": "D" * 64, "privatePath": "SECRET"}
    instance = make_enable_broker(contract=contract, installed_hashes=actual)
    try:
        add(instance, seq_id=1)
        context = instance.inspect("capture.context.v1")
        expected = {"gameAssemblySha256": "c" * 64, "metadataSha256": "d" * 64}
        assert context["data"]["nativeBinding"] == expected
        assert instance.inspect("effects.operations.v1")["data"]["nativeBinding"] == expected
        assert set(context["data"]["context"]) == {"captureSessionId", "generation"}
        assert context["lifecycleEvidence"]["clientOperationSequenceExact"]["state"] == "unavailable"
        assert "SECRET" not in json.dumps(instance.inspect())
        context["data"]["nativeBinding"]["gameAssemblySha256"] = "mutated"
        assert instance.inspect("capture.context.v1")["data"]["nativeBinding"] == expected
    finally:
        instance.close()


@pytest.mark.parametrize("value", [None, "", "a" * 63, "a" * 65, "g" * 64, True, 123, {"secret": "PRIVATE"}])
def test_public_native_binding_filters_invalid_actual_hashes(value):
    instance = make_enable_broker(contract=generic_native_contract(), installed_hashes={
        "gameAssemblySha256": value, "metadataSha256": "B" * 64, "secret": "PRIVATE"})
    try:
        add(instance, seq_id=1)
        binding = instance.inspect("capture.context.v1")["data"]["nativeBinding"]
        assert binding == {"gameAssemblySha256": None, "metadataSha256": "b" * 64}
        assert "PRIVATE" not in json.dumps(instance.inspect("effects.operations.v1"))
    finally:
        instance.close()


@pytest.mark.parametrize("case", ["version", "proof", "scope", "boolean_increment", "density"])
def test_operation_sequence_gate_rejects_unverified_contracts(case):
    contract = generic_native_contract()
    if case == "proof":
        contract["capabilities"]["clientOperationSequenceExact"]["proofSha256"] = "invalid"
    elif case == "scope":
        contract["capabilities"]["clientOperationSequenceExact"]["sequenceScope"] = "one_owner"
    elif case == "boolean_increment":
        contract["capabilities"]["clientOperationSequenceExact"]["increment"] = True
    elif case == "density":
        contract["capabilities"]["clientOperationSequenceExact"].pop("wireSequenceDensity")
    instance = make_enable_broker(contract=contract, installed_hashes={} if case == "version" else None)
    try:
        emit(instance, "BattleOpSkillAttach", seq_id=1)
        assert instance.inspect("effects.operations.v1")["data"]["continuity"]["state"] == "unavailable"
    finally:
        instance.close()


def test_operation_sequence_gaps_backwards_and_duplicates_are_sticky_and_not_owner_scoped():
    instance = make_enable_broker(contract=generic_native_contract())
    try:
        for index, seq in enumerate([10, 11, 13, 13, 12, 14]):
            emit(instance, "BattleOpSkillAttach", seq_id=seq, owner_id=100 + index)
        data = instance.inspect("effects.operations.v1")["data"]
        assert [row["sequenceRelation"] for row in data["operations"]] == ["first_observation", "next", "gap", "repeated", "backward", "gap"]
        assert data["gapRevision"] == 4 and data["continuity"]["lastGapIndex"] == 6
        assert data["continuity"]["state"] == "gap"
        emit(instance, "BattleOpSkillAttach", seq_id=15, owner_id=999)
        assert instance.inspect("effects.operations.v1")["data"]["gapRevision"] == 4
    finally:
        instance.close()


@pytest.mark.parametrize("seq", [None, True, 1.0, "1", -(2 ** 31) - 1, 2 ** 31])
def test_operation_headers_reject_invalid_sequence_values(broker, seq):
    emit(broker, "BattleOpSkillAttach", seq_id=seq)
    assert broker.inspect("effects.operations.v1")["state"] == "unavailable"


def test_operations_keep_explicit_zero_int32_seq_and_ignore_non_operation_clock_events():
    instance = make_enable_broker(contract=generic_native_contract())
    try:
        emit(instance, "GAME_CLOCK_TICK", seq_id=0, tick_ms=987)
        emit(instance, "CS_PING", seq_id=0)
        assert instance.inspect("effects.operations.v1")["state"] == "unavailable"
        emit(instance, "BattleOpSkillAttach", seq_id=0)
        emit(instance, "BattleOpSkillAttach", seq_id=1)
        data = instance.inspect("effects.operations.v1")["data"]
        assert [row["seqId"] for row in data["operations"]] == [0, 1]
        assert data["gapRevision"] == 0
    finally:
        instance.close()


def test_operation_buffer_and_limit_report_every_missing_local_index():
    contract = generic_native_contract()
    instance = OverlayTopicBroker(publish_interval_ms=0, native_contract=contract, installed_hashes=contract["hashes"], max_operations=3)
    instance.on_state_change("live", "capture-1")
    try:
        for seq in range(1, 11):
            emit(instance, "BattleOpSkillAttach", seq_id=seq)
        data = instance.inspect("effects.operations.v1", {"limit": 2})["data"]
        assert [row["index"] for row in data["operations"]] == [9, 10]
        assert (data["bufferFirstIndex"], data["bufferLastIndex"], data["retainedCount"]) == (8, 10, 3)
        assert (data["firstIndex"], data["lastIndex"], data["lastObservedIndex"]) == (9, 10, 10)
        assert data["bufferDroppedCount"] == 7 and data["projectionOmittedOperationCount"] == 1
        assert data["omittedBeforeCount"] == 8 and data["projectionReason"] == "limit"
        assert data["gapRevision"] == 0
    finally:
        instance.close()


@pytest.mark.parametrize("options", [{"target": "all"}, {"effectIds": []}, {"offset": 0}, {"query": ""}, {"limit": 0}, {"limit": 257}, {"limit": True}])
def test_operations_do_not_allow_sequence_breaking_filters(broker, options):
    with pytest.raises(ValueError):
        broker.inspect("effects.operations.v1", options)


def test_raw_headers_enrich_exact_normalized_payload_without_derived_sequence_duplicates():
    instance = make_enable_broker(contract=generic_native_contract())
    try:
        emit(instance, "CAPTURE_BATTLE_OPERATION", seq_id=0, operation_type="BattleOpEntityDie", op_type=7)
        emit(instance, "BattleOpEntityDie", seq_id=0, entity_inst_id=100)
        emit(instance, "BattleOpModifyBattleState", seq_id=0, state="synthetic-derived")
        emit(instance, "CAPTURE_BATTLE_OPERATION", seq_id=1, operation_type="BattleOpTriggerAction", op_type=8)
        before = instance.inspect("effects.operations.v1")["data"]
        assert before["operations"][-1]["payloadState"] == "pending"
        assert before["pendingPayloadCount"] == 1
        emit(instance, "BattleOpTriggerAction", seq_id=1, owner_id=100, operation_owner_id="100", trigger_owner_id="100",
             inst_id=90, trigger_type=0, template_type="Buff", template_int_id=700, action_runtime_case="create_buff_action",
             action={"action_type": 2, "action_id": 0, "create_buff_action": {"details": [
                 {"buff_inst_id": "91", "buff_num_id": 700, "target_id": "100", "source_id": "200",
                  "assigned_items": [{"target_key": "rate", "numeric_value": 0.0, "use_direct_value": True}],
                  "private_nickname": "SECRET"}]}, "raw_log": "PRIVATE"}, account_email="SECRET")
        data = instance.inspect("effects.operations.v1")["data"]
        assert data["sourceMode"] == "raw-operation-header" and data["lastObservedIndex"] == 2
        assert data["gapRevision"] == 0 and data["continuity"]["state"] == "contiguous"
        assert data["pendingPayloadCount"] == 0
        operation = data["operations"][-1]
        assert operation["payloadState"] == "whitelisted-fields" and operation["payloadRevision"] == 1
        assert operation["payload"]["action"]["create_buff_action"]["details"][0]["assigned_items"][0]["numeric_value"] == 0
        assert "SECRET" not in json.dumps(data) and "PRIVATE" not in json.dumps(data)
        assert [row["seqId"] for row in data["operations"]] == [0, 1]
        assert data["contentRevision"] > before["contentRevision"]
        operation["payload"]["owner_id"] = "mutated"
        assert instance.inspect("effects.operations.v1")["data"]["operations"][-1]["payload"]["owner_id"] == "100"
    finally:
        instance.close()


def test_operation_source_mode_transition_starts_a_new_explicit_stream_domain():
    instance = make_enable_broker(contract=generic_native_contract())
    try:
        emit(instance, "BattleOpSkillAttach", seq_id=99)
        initial = instance.inspect("effects.operations.v1")["data"]
        emit(instance, "CAPTURE_BATTLE_OPERATION", seq_id=100, operation_type="UnknownBattleOperation", op_type=12345)
        data = instance.inspect("effects.operations.v1")["data"]
        assert data["streamRevision"] == initial["streamRevision"] + 1
        assert data["sourceMode"] == "raw-operation-header" and data["firstIndex"] == data["lastIndex"] == 1
        assert data["gapRevision"] == 1 and data["continuity"]["state"] == "gap"
        assert data["operations"][0]["sequenceRelation"] == "source_mode_changed"
        assert data["operations"][0]["type"] == "UnknownBattleOperation"
        emit(instance, "BattleOpSkillAttach", seq_id=101)
        assert instance.inspect("effects.operations.v1")["data"]["lastObservedIndex"] == 1
    finally:
        instance.close()


def test_conflicting_normalized_payload_is_unavailable_without_fabricating_raw_sequence_gap():
    instance = make_enable_broker(contract=generic_native_contract())
    try:
        emit(instance, "CAPTURE_BATTLE_OPERATION", seq_id=1, operation_type="BattleOpFinishBuff", op_type=2)
        emit(instance, "BattleOpFinishBuff", seq_id=1, operation_owner_id=100, buff_inst_id=123, force=True, reduce_layer=1)
        emit(instance, "BattleOpFinishBuff", seq_id=1, operation_owner_id=100, buff_inst_id=123, force=False, reduce_layer=1)
        data = instance.inspect("effects.operations.v1")["data"]
        assert data["gapRevision"] == 0 and data["lastObservedIndex"] == 1
        assert data["payloadOmittedCount"] == 1 and data["lastPayloadOmissionIndex"] == 1
        assert data["operations"][0]["payloadState"] == "omitted"
        assert data["operations"][0]["payloadReason"] == "conflicting_normalized_payload"
    finally:
        instance.close()


def test_operation_context_invalidations_clear_indices_and_cannot_mix_late_capture_events():
    instance = make_enable_broker(contract=generic_native_contract())
    try:
        emit(instance, "CAPTURE_BATTLE_OPERATION", seq_id=1, operation_type="BattleOpSkillAttach", op_type=3)
        original_context = instance.inspect("effects.operations.v1")["data"]["context"]
        instance.on_state_change("waiting_game")
        assert instance.inspect("effects.operations.v1")["state"] == "unavailable"
        instance.on_state_change("live", "capture-2")
        instance.on_event({"type": "CAPTURE_BATTLE_OPERATION", "session_id": "capture-1", "seq_id": 2, "operation_type": "BattleOpSkillAttach"})
        assert instance.inspect("effects.operations.v1")["state"] == "unavailable"
        instance.on_event({"type": "CAPTURE_BATTLE_OPERATION", "session_id": "capture-2", "seq_id": 50, "operation_type": "BattleOpSkillAttach"})
        data = instance.inspect("effects.operations.v1")["data"]
        assert data["context"] != original_context and data["firstIndex"] == data["lastIndex"] == 1
        assert data["gapRevision"] == 0
        instance.on_event({"type": "SC_ENTER_DUNGEON", "session_id": "capture-2"})
        assert instance.inspect("effects.operations.v1")["state"] == "unavailable"
    finally:
        instance.close()


def test_target_context_revisions_invalidate_only_explicit_target_and_ignore_server_snapshot_or_uid_finish():
    instance = make_enable_broker(contract=generic_native_contract())
    uid = (1 << 61) + 123
    try:
        emit(instance, "CAPTURE_BATTLE_OPERATION", seq_id=1, operation_type="BattleOpSkillAttach", op_type=3)
        add(instance, uid=uid, target=100)
        add(instance, uid=uid + 1, target=101)
        emit(instance, "SC_RESET_BATTLE_STATUS", battle_inst_id=100, battle_info={"msg_generation": 2})
        data = instance.inspect("effects.operations.v1")["data"]
        assert data["targetContextRevisions"] == {"100": {"revision": 1, "afterIndex": 1, "streamRevision": 1}}
        assert data["targetContextRevisionOverflow"] == 0 and data["targetContextRevisionMapComplete"] is True
        actual = {row["targetId"]: row for row in effects(instance)}
        assert actual["100"]["state"] == "context-unknown" and actual["101"]["state"] == "observed-open"
        emit(instance, "SC_EFFECT_SNAPSHOT", owners=[{"owner_battle_inst_id": 100, "generation": 2, "buffs": []}])
        emit(instance, "CAPTURE_BATTLE_OPERATION", seq_id=2, operation_type="BattleOpFinishBuff", op_type=2)
        emit(instance, "BattleOpFinishBuff", seq_id=2, operation_owner_id=101, buff_inst_id=uid + 1, reduce_layer=1, force=True)
        assert instance.inspect("effects.operations.v1")["data"]["targetContextRevisions"] == data["targetContextRevisions"]
        emit(instance, "BattleOpEntityDie", entity_inst_id=101)
        revised = instance.inspect("effects.operations.v1")["data"]["targetContextRevisions"]
        assert revised["100"] == data["targetContextRevisions"]["100"]
        assert revised["101"] == {"revision": 2, "afterIndex": 2, "streamRevision": 1}
        revised["100"]["revision"] = -1
        assert instance.inspect("effects.operations.v1")["data"]["targetContextRevisions"]["100"]["revision"] == 1
    finally:
        instance.close()


def test_empty_target_reset_is_an_invalidation_and_never_a_zero_count_anchor():
    instance = make_enable_broker(contract=generic_native_contract())
    try:
        emit(instance, "CAPTURE_BATTLE_OPERATION", seq_id=1, operation_type="BattleOpSkillAttach", op_type=3)
        before = instance.inspect("effects.operations.v1")["data"]["contentRevision"]
        emit(instance, "SC_RESET_BATTLE_STATUS", battle_inst_id=100, battle_info={"msg_generation": 2})
        data = instance.inspect("effects.operations.v1")["data"]
        assert data["contentRevision"] > before and data["lastObservedIndex"] == 1
        assert data["targetContextRevisions"]["100"]["afterIndex"] == 1
        assert data["operations"][0]["type"] == "BattleOpSkillAttach"
        actual = instance.inspect("effects.actual.v1")["data"]
        assert actual["effects"] == [] and actual["absence"] == "unknown"
    finally:
        instance.close()


def test_same_frame_clear_before_target_reset_cannot_be_used_as_a_later_anchor():
    contract = generic_native_contract()
    instance = OverlayTopicBroker(publish_interval_ms=60_000, native_contract=contract, installed_hashes=contract["hashes"])
    instance.on_state_change("live", "capture-1")
    received = []
    try:
        instance.subscribe("effects.operations.v1", {}, received.append)
        emit(instance, "CAPTURE_BATTLE_OPERATION", seq_id=1, operation_type="BattleOpTriggerAction", op_type=8)
        emit(instance, "BattleOpTriggerAction", seq_id=1, trigger_type=0, action_runtime_case="finish_buff_action",
             template_type="Buff", template_int_id=700, inst_id=123, operation_owner_id=100,
             action={"action_type": 3, "action_id": 1, "finish_buff_action": {"finish_layer_cnt": 0,
                       "finish_buffs": [{"target_id": "100", "buff_inst_id": "999"}]}})
        emit(instance, "SC_RESET_BATTLE_STATUS", battle_inst_id=100, battle_info={"msg_generation": 2})
        instance.flush()
        data = received[-1]["data"]
        clear_index = data["operations"][0]["index"]
        target_boundary = data["targetContextRevisions"]["100"]
        assert clear_index <= target_boundary["afterIndex"] == 1
        assert target_boundary["streamRevision"] == data["streamRevision"]
        assert data["operations"][0]["payload"]["action"]["finish_buff_action"]["finish_layer_cnt"] == 0
        assert data["continuity"]["state"] == "contiguous"
    finally:
        instance.close()


def test_target_context_revision_map_eviction_never_reuses_serial_and_reports_loss_boundary():
    instance = make_enable_broker(contract=generic_native_contract())
    try:
        emit(instance, "CAPTURE_BATTLE_OPERATION", seq_id=1, operation_type="BattleOpSkillAttach", op_type=3)
        for target in range(1000, 3050):
            emit(instance, "SC_RESET_BATTLE_STATUS", battle_inst_id=target, battle_info={"msg_generation": 2})
        data = instance.inspect("effects.operations.v1")["data"]
        assert len(data["targetContextRevisions"]) == 2048
        assert "1000" not in data["targetContextRevisions"] and "1001" not in data["targetContextRevisions"]
        assert data["targetContextRevisionOverflow"] == 2 and data["targetContextRevisionMapComplete"] is False
        assert data["targetContextRevisionOverflowAfterIndex"] == 1
        assert data["targetContextRevisionOverflowStreamRevision"] == data["streamRevision"] == 1
        emit(instance, "SC_RESET_BATTLE_STATUS", battle_inst_id=1000, battle_info={"msg_generation": 3})
        replaced = instance.inspect("effects.operations.v1")["data"]
        assert replaced["targetContextRevisions"]["1000"]["revision"] == 2051
        assert replaced["targetContextRevisionOverflow"] == 3
    finally:
        instance.close()


def test_target_revision_stream_switch_clears_map_but_preserves_context_serial():
    instance = make_enable_broker(contract=generic_native_contract())
    try:
        emit(instance, "BattleOpSkillAttach", seq_id=1)
        emit(instance, "SC_RESET_BATTLE_STATUS", battle_inst_id=100, battle_info={"msg_generation": 2})
        before = instance.inspect("effects.operations.v1")["data"]
        emit(instance, "CAPTURE_BATTLE_OPERATION", seq_id=2, operation_type="BattleOpSkillAttach", op_type=3)
        switched = instance.inspect("effects.operations.v1")["data"]
        assert switched["streamRevision"] == before["streamRevision"] + 1
        assert switched["targetContextRevisions"] == {}
        emit(instance, "SC_RESET_BATTLE_STATUS", battle_inst_id=100, battle_info={"msg_generation": 3})
        after = instance.inspect("effects.operations.v1")["data"]
        assert after["targetContextRevisions"]["100"] == {"revision": 2, "afterIndex": 1, "streamRevision": 2}
        emit(instance, "SC_ENTER_DUNGEON")
        emit(instance, "CAPTURE_BATTLE_OPERATION", seq_id=3, operation_type="BattleOpSkillAttach", op_type=3)
        emit(instance, "SC_RESET_BATTLE_STATUS", battle_inst_id=100, battle_info={"msg_generation": 4})
        restarted = instance.inspect("effects.operations.v1")["data"]
        assert restarted["context"] != before["context"]
        assert restarted["targetContextRevisions"]["100"]["revision"] == 1
    finally:
        instance.close()


def test_operation_large_payload_is_declared_omitted_but_sequence_header_is_retained(broker):
    assigned = [{"target_key": f"key_{index}", "string_value": "x" * 2000, "use_direct_value": True} for index in range(20)]
    emit(broker, "BattleOpTriggerAction", seq_id=1, trigger_type=0, action_runtime_case="create_buff_action",
         action={"action_type": 2, "create_buff_action": {"details": [
             {"buff_inst_id": "1", "buff_num_id": 700, "target_id": "100", "assigned_items": assigned}]}})
    data = broker.inspect("effects.operations.v1")["data"]
    assert data["lastObservedIndex"] == 1
    assert data["payloadOmittedCount"] == 1 and data["lastPayloadOmissionIndex"] == 1
    assert data["operations"][0]["payloadState"] == "omitted"
    assert data["operations"][0]["payload"] is None
    assert data["operations"][0]["seqId"] == 1


def test_operation_payload_budget_reports_the_exact_delivered_suffix():
    instance = OverlayTopicBroker(publish_interval_ms=60_000)
    instance.on_state_change("live", "capture-1")
    assigned = [{"target_key": f"key_{index}", "string_value": "x" * 1800, "use_direct_value": True} for index in range(14)]
    try:
        for seq in range(1, 261):
            emit(instance, "BattleOpTriggerAction", seq_id=seq, trigger_type=0, action_runtime_case="create_buff_action",
                 action={"action_type": 2, "create_buff_action": {"details": [
                     {"buff_inst_id": "1", "buff_num_id": 700, "target_id": "100", "assigned_items": assigned}]}})
        envelope = instance.inspect("effects.operations.v1")
        data = envelope["data"]
        assert len(json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) <= 1_500_000
        assert 0 < len(data["operations"]) < 256
        assert data["projectionReason"] == "payload_budget" and data["payloadOmittedCount"] == 0
        assert data["bufferFirstIndex"] == 5 and data["bufferLastIndex"] == 260
        assert data["bufferDroppedCount"] == 4
        assert data["projectionOmittedOperationCount"] + data["deliveredOperationCount"] == data["retainedCount"] == 256
        assert data["omittedBeforeCount"] == data["firstIndex"] - 1
        assert [row["index"] for row in data["operations"]] == list(range(data["firstIndex"], 261))
        assert data["lastIndex"] == data["lastObservedIndex"] == 260
    finally:
        instance.close()


def test_operation_subscribers_share_projections_only_when_limit_matches(monkeypatch):
    instance = OverlayTopicBroker(publish_interval_ms=60_000)
    instance.on_state_change("live", "capture-1")
    outputs = [[] for _ in range(4)]
    try:
        for output, options in zip(outputs, (None, {}, {"limit": 256}, {"limit": 1})):
            instance.subscribe("effects.operations.v1", options, output.append)
        builds = []
        original = instance._build

        def count_build(topic, options):
            builds.append(topic)
            return original(topic, options)

        monkeypatch.setattr(instance, "_build", count_build)
        emit(instance, "BattleOpFinishBuff", seq_id=1, buff_inst_id=123, operation_owner_id=100, reduce_layer=1, force=True)
        instance.flush()
        assert builds.count("effects.operations.v1") == 2
        outputs[0][-1]["data"]["operations"][0]["payload"]["buff_inst_id"] = "mutated"
        assert outputs[1][-1]["data"]["operations"][0]["payload"]["buff_inst_id"] == "123"
    finally:
        instance.close()


def test_905_contiguous_raw_headers_are_consumed_once_across_overlapping_buffer_snapshots():
    contract = generic_native_contract()
    instance = OverlayTopicBroker(publish_interval_ms=60_000, native_contract=contract, installed_hashes=contract["hashes"])
    instance.on_state_change("live", "capture-1")
    seen = []
    last_index = 0

    def consume(envelope):
        nonlocal last_index
        if envelope["state"] != "ready":
            return
        data = envelope["data"]
        assert data["sourceMode"] == "raw-operation-header" and data["gapRevision"] == 0
        assert data["continuity"]["state"] == "contiguous"
        assert data["firstIndex"] <= last_index + 1
        for row in data["operations"]:
            if row["index"] > last_index:
                assert row["index"] == last_index + 1
                seen.append(row["seqId"])
                last_index = row["index"]

    try:
        instance.subscribe("effects.operations.v1", {}, consume)
        for index, seq in enumerate(range(4450, 5355), 1):
            emit(instance, "CAPTURE_BATTLE_OPERATION", seq_id=seq, operation_type="BattleOpUpdateAtbInfo", op_type=4)
            if index % 64 == 0:
                instance.flush()
        instance.flush()
        assert seen == list(range(4450, 5355))
        assert last_index == 905
        assert instance.inspect("effects.operations.v1")["data"]["bufferDroppedCount"] == 649
        assert instance.inspect("effects.actual.v1")["state"] == "unavailable"
    finally:
        instance.close()


@pytest.mark.parametrize("kind", ["BattleOpSkillAttach", "BattleOpSkillDetach"])
def test_skill_lifecycle_payloads_are_pending_until_exact_raw_header_enrichment(kind):
    instance = make_enable_broker(contract=generic_native_contract())
    skill_uid = 2 ** 64 - 12
    try:
        emit(instance, "CAPTURE_BATTLE_OPERATION", seq_id=1, operation_type=kind, op_type=3)
        before = instance.inspect("effects.operations.v1")["data"]
        assert before["operations"][0]["payloadState"] == "pending"
        assert before["pendingPayloadCount"] == 1
        fields = {"skill_inst_id": skill_uid, "private_account": "SECRET"}
        if kind == "BattleOpSkillAttach":
            fields.update(src_inst_id=100, skill_lv=0, skill_source="Equipment", template_type="Skill",
                          template_int_id=700, template_str_id="skill_exact")
        emit(instance, kind, seq_id=1, **fields)
        data = instance.inspect("effects.operations.v1")["data"]
        operation = data["operations"][0]
        assert data["lastObservedIndex"] == 1 and data["gapRevision"] == 0
        assert data["pendingPayloadCount"] == 0
        assert operation["payloadState"] == "whitelisted-fields" and operation["payloadRevision"] == 1
        assert operation["payload"]["skill_inst_id"] == str(skill_uid)
        assert "SECRET" not in json.dumps(data)
        if kind == "BattleOpSkillAttach":
            assert operation["payload"] == {"src_inst_id": "100", "skill_inst_id": str(skill_uid), "skill_lv": 0,
                                             "skill_source": "Equipment", "template_type": "Skill", "template_int_id": 700,
                                             "template_str_id": "skill_exact"}
        else:
            assert operation["payload"] == {"skill_inst_id": str(skill_uid)}
    finally:
        instance.close()


def test_skill_parent_identity_survives_attach_create_owner_and_detach_without_aliases():
    instance = make_enable_broker(contract=generic_native_contract())
    skill_uid = 2 ** 64 - 12
    try:
        emit(instance, "CAPTURE_BATTLE_OPERATION", seq_id=1, operation_type="BattleOpSkillAttach", op_type=3)
        emit(instance, "BattleOpSkillAttach", seq_id=1, src_inst_id=100, skill_inst_id=skill_uid,
             template_type="Skill", template_str_id="skill_exact")
        emit(instance, "CAPTURE_BATTLE_OPERATION", seq_id=2, operation_type="BattleOpTriggerAction", op_type=8)
        emit(instance, "BattleOpTriggerAction", seq_id=2, owner_id=100, operation_owner_id=100,
             inst_id=skill_uid, trigger_inst_id=str(skill_uid), template_type="Skill", template_str_id="skill_exact",
             trigger_type=0, action_runtime_case="create_buff_action",
             action={"action_type": 2, "create_buff_action": {"details": []}})
        emit(instance, "CAPTURE_BATTLE_OPERATION", seq_id=3, operation_type="BattleOpSkillDetach", op_type=4)
        emit(instance, "BattleOpSkillDetach", seq_id=3, skill_inst_id=skill_uid)
        data = instance.inspect("effects.operations.v1")["data"]
        attach, create, detach = data["operations"]
        assert attach["payload"]["skill_inst_id"] == create["payload"]["inst_id"] == detach["payload"]["skill_inst_id"] == str(skill_uid)
        assert create["payload"]["trigger_inst_id"] == str(skill_uid)
        assert data["gapRevision"] == 0 and data["pendingPayloadCount"] == 0
    finally:
        instance.close()


def test_exact_action_finish_closes_only_explicit_open_client_instance():
    instance = make_enable_broker(contract=generic_native_contract())
    uid = (1 << 61) + 123
    try:
        add(instance, uid=uid, target=100)
        add(instance, uid=uid, target=101)
        emit(instance, "SC_EFFECT_SNAPSHOT", owners=[{"owner_battle_inst_id": 100, "generation": 2,
             "buffs": [{"inst_id": str(uid), "buff_id": {"str_id": "buff_exact"}}]}])
        emit(instance, "BattleOpTriggerAction", trigger_type=0, action_runtime_case="finish_buff_action",
             action={"action_type": "BattleActionFinishBuff", "finish_buff_action": {"finish_buffs": [
                 {"target_id": "100", "buff_inst_id": str(uid)}]}})
        rows = effects(instance)
        matched = next(row for row in rows if row["namespace"] == "client_buff" and row["targetId"] == "100")
        assert matched["state"] == "observed-closed"
        assert matched["closureReason"] == "client-action-finish-confirmed"
        assert matched["finishedAtMs"] == 10_000 and matched["removedAtMs"] is None
        assert all(row["state"] == "observed-open" for row in rows if row is not matched)
    finally:
        instance.close()


@pytest.mark.parametrize("case", ["untyped", "wrong_action", "wrong_trigger", "boolean_trigger", "wrong_owner", "uid_without_mask", "version_mismatch", "missing_create"])
def test_action_finish_never_uses_a_candidate_fallback_or_unverified_case(case):
    instance = make_enable_broker(contract=generic_native_contract(),
                                  installed_hashes={} if case == "version_mismatch" else None)
    uid = 123 if case == "uid_without_mask" else (1 << 61) + 123
    try:
        if case != "missing_create":
            add(instance, uid=uid, target=100)
        emit(instance, "BattleOpTriggerAction", trigger_type=2 if case == "wrong_trigger" else False if case == "boolean_trigger" else 0,
             action_runtime_case=None if case == "untyped" else "finish_buff_action",
             action={"action_type": "BattleActionDamage" if case == "wrong_action" else 3,
                     "finish_buff_action": {"finish_buffs": [
                         {"target_id": "101" if case == "wrong_owner" else "100", "buff_inst_id": str(uid)}]}})
        assert all(row["state"] == "observed-open" for row in effects(instance))
        if case == "missing_create":
            unresolved = instance.inspect("effects.actual.v1")["data"]["unresolvedObservations"]
            assert len(unresolved) == 1 and unresolved[0]["reason"] == "creation_not_observed"
    finally:
        instance.close()


def test_create_end_phase_remains_request_with_native_action_finish_capability():
    instance = make_enable_broker(contract=generic_native_contract())
    uid = (1 << 61) + 123
    try:
        add(instance, uid=uid)
        emit(instance, "BattleOpTriggerAction", trigger_type=2, action_runtime_case="create_buff_action",
             action={"action_type": 2, "create_buff_action": {"details": [{"target_id": "100", "buff_inst_id": str(uid)}]}})
        row = effects(instance)[0]
        assert row["state"] == "observed-open"
        assert row["raw"]["finishRequestReason"] == "create_action_end_does_not_prove_finished"
    finally:
        instance.close()


def test_keyword_reports_absolute_rate_in_array_order_without_creating_or_stacking_instances():
    instance = make_enable_broker(contract=generic_native_contract())
    uid = (1 << 61) + 123
    try:
        add(instance, uid=uid)
        emit(instance, "BattleOpTriggerKeyword", owner_id=100, buff_inst_id=uid, add_buff_num_id=999,
             modify=[{"config_index": 7, "modify_value": 0.75}, {"config_index": 1, "modify_value": 0.125}])
        row = effects(instance)[0]
        assert row["runtimeValues"]["variables"]["rate"]["value"] == 0.125
        assert row["runtimeValues"]["variables"]["rate"]["configIndex"] == 1
        assert row["fieldAvailability"]["variables"]["state"] == "known"
        assert row["stackCount"] is None
        assert len(effects(instance)) == 1
        emit(instance, "BattleOpTriggerKeyword", owner_id=100, buff_inst_id=uid + 1,
             modify=[{"config_index": 0, "modify_value": 9}])
        assert len(effects(instance)) == 1
        assert instance.inspect("effects.actual.v1")["data"]["unresolvedObservations"][-1]["reason"] == "creation_not_observed"
    finally:
        instance.close()


@pytest.mark.parametrize("case", ["wrong_owner", "missing_owner", "uid_without_mask", "invalid_last_value", "negative_index", "version_mismatch"])
def test_keyword_requires_native_contract_exact_owner_uid_and_complete_values(case):
    instance = make_enable_broker(contract=generic_native_contract(),
                                  installed_hashes={} if case == "version_mismatch" else None)
    uid = 123 if case == "uid_without_mask" else (1 << 61) + 123
    try:
        add(instance, uid=uid)
        updates = [{"config_index": 0, "modify_value": 0.4}]
        if case == "invalid_last_value":
            updates.append({"config_index": 1, "modify_value": "missing"})
        if case == "negative_index":
            updates[0]["config_index"] = -1
        emit(instance, "BattleOpTriggerKeyword", owner_id=None if case == "missing_owner" else 101 if case == "wrong_owner" else 100,
             buff_inst_id=uid, modify=updates)
        assert effects(instance)[0]["runtimeValues"]["variables"] is None
        assert len(effects(instance)) == 1
    finally:
        instance.close()


def test_refresh_preserves_observed_enabled_variables_and_initialization_without_reviving_ended_uid():
    instance = make_enable_broker(contract=generic_native_contract())
    uid = (1 << 61) + 123
    try:
        add(instance, uid=uid, assigned_items={"blackboard": {"duration": {"str_value": "incoming", "is_dynamic": True}}})
        emit(instance, "BattleOpSetBuffEnableState", owner_id=100, states={str(uid): False})
        emit(instance, "BattleOpTriggerKeyword", owner_id=100, buff_inst_id=uid,
             modify=[{"config_index": 0, "modify_value": 0.4}])
        add(instance, uid=uid)
        row = effects(instance)[0]
        assert row["enabled"] is False
        assert row["runtimeValues"]["variables"]["rate"]["value"] == 0.4
        assert row["initializationParameters"]["assignedItems"]["blackboard"]["duration"]["is_dynamic"] is True
        emit(instance, "BattleOpFinishBuff", operation_owner_id=100, buff_inst_id=uid, reduce_layer=1, force=True)
        before = effects(instance)[0]
        add(instance, uid=uid)
        emit(instance, "BattleOpTriggerKeyword", owner_id=100, buff_inst_id=uid,
             modify=[{"config_index": 0, "modify_value": 9}])
        assert effects(instance)[0] == before
        assert len(instance.inspect("effects.actual.v1")["data"]["unresolvedObservations"]) == 2
    finally:
        instance.close()


@pytest.mark.parametrize("kind", ["create", "add"])
def test_protobuf_initialization_keeps_real_assign_list_and_blackboard_shapes(broker, kind):
    from endfield_pcap.proto_generated.CS_BATTLE_OP_pb2 import CS_BATTLE_OP
    from endfield_pcap.service import SessionPipeline

    message = CS_BATTLE_OP()
    op = message.client_data.op_list.add(owner_id=200)
    if kind == "create":
        op.op_type = 8
        trigger = op.trigger_action_op_data
        trigger.owner_id = 200
        trigger.action.action_type = 2
        detail = trigger.action.create_buff_action.details.add(buff_inst_id=123, buff_num_id=700, target_id=100, source_id=200)
        detail.assigned_items.add(target_key="duration", input_value_key="incoming")
        detail.assigned_items.add(target_key="rate", use_direct_value=True, numeric_value=0.125)
    else:
        op.op_type = 1
        detail = op.add_buff_op_data
        detail.buff_inst_id, detail.target_inst_id, detail.src_inst_id = 123, 100, 200
        detail.buff_id.type, detail.buff_id.int_id = 1, 700
        detail.assigned_items.blackboard["duration"].str_value = "incoming"
        detail.assigned_items.blackboard["duration"].is_dynamic = True
        detail.assigned_items.blackboard["rate"].float_value = 0.125
    pipeline = SessionPipeline.__new__(SessionPipeline)
    pipeline.session_id, pipeline.skill_levels_by_battle_inst = "capture-1", {}
    observed = []
    pipeline._emit_event = observed.append
    pipeline._emit_battle_events(CS_BATTLE_OP.FromString(message.SerializeToString()), 10_000)
    for event in observed:
        broker.on_event(event.as_dict())
    row = effects(broker)[0]
    assigned = row["initializationParameters"]["assignedItems"]
    if kind == "create":
        assert isinstance(assigned, list)
        assert "numeric_value" not in assigned[0]
        assert assigned[1]["numeric_value"] == 0.125
    else:
        assert isinstance(assigned, dict)
        assert assigned["blackboard"]["duration"] == {"str_value": "incoming", "is_dynamic": True}
        assert assigned["blackboard"]["rate"]["float_value"] == 0.125
    assert row["runtimeValues"]["variables"] is None and row["durationMs"] is None


def test_groups_are_raw_metadata_never_current_layer_count(broker):
    emit(broker, "SC_EFFECT_SNAPSHOT", owners=[{
        "owner_battle_inst_id": 100, "generation": 0, "buffs": [],
        "stacking_groups": [{"stacking_key": "g", "stacking_type": 0,
                             "max_stack_cnt": 6, "buff_inst_ids": [1, 2], "is_server_init": False}],
    }])
    data = broker.inspect("effects.actual.v1")["data"]
    assert data["effects"] == []
    assert data["stackingGroups"][0]["instanceIds"] == ["1", "2"]
    assert data["stackingGroups"][0]["maxStackCount"] == 6
    assert "stackCount" not in data["stackingGroups"][0]
