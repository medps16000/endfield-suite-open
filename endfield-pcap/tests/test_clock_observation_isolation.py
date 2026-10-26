"""Clock capture must not perturb the existing battle evidence stream."""
from copy import deepcopy

import pytest

from endfield_pcap.trace_bridge import TraceBridge


OBSERVATIONS = [
    {"type": "GAME_CLOCK_TICK", "source": "CS_BATTLE_OP", "tick_ms": 12345, "seq_id": 0},
    {"type": "CS_PING", "msg_id": 5, "client_ts": 123, "logical_ts": 456, "raw_body_hex": "087b"},
    {"type": "SC_PING", "msg_id": 5, "client_ts": 123, "server_ts": 1790000000000, "raw_body_hex": "087b"},
    {"type": "CS_GAME_TIME_FREEZE_START_REQ", "msg_id": 1452, "reason": 0, "raw_body_hex": ""},
    {"type": "CS_GAME_TIME_FREEZE_END_REQ", "msg_id": 1453, "reason": 0, "raw_body_hex": ""},
    {"type": "SC_GAME_TIME_FREEZE_START_RSP", "msg_id": 1642, "reason": 0, "in_time_freeze": True, "raw_body_hex": "1001"},
    {"type": "SC_GAME_TIME_FREEZE_END_RSP", "msg_id": 1643, "reason": 0, "in_time_freeze": False, "raw_body_hex": ""},
]


def _bridge(path):
    bridge = TraceBridge(path)
    bridge.id_to_name[100266] = "chr_0032_lizhiyan"
    bridge.id_to_name[100378] = "eny_0063_agmelee2"
    return bridge


def _buff_event(kind, rate, seq):
    items = [{"target_key": "spell_dmg_taken_up2", "numeric_value": rate}]
    if kind == "add":
        return {"type": "BattleOpAddBuff", "timestamp_ms": 1000, "seq_id": seq,
                "buff_inst_id": 9001, "int_id": 3724, "src_inst_id": 100266,
                "target_inst_id": 100378, "assigned_items": items}
    return {"type": "BattleOpTriggerAction", "timestamp_ms": 1000, "seq_id": seq,
            "owner_id": 100266, "inst_id": 500, "trigger_type": 0,
            "template_type": "Skill", "template_str_id": "chr_0032_lizhiyan_normal_skill",
            "action": {"action_type": "BattleActionCreateBuff", "create_buff_action": {"details": [
                {"source_id": 100266, "target_id": 100378, "buff_inst_id": 9001,
                 "buff_num_id": 3724, "assigned_items": items}]}}}


def _damage_event():
    return {"type": "BattleOpTriggerAction", "timestamp_ms": 2000, "seq_id": 30,
            "client_tick_tms": 13345, "owner_id": 100266, "inst_id": 501,
            "template_str_id": "chr_0032_lizhiyan_normal_skill",
            "action": {"action_type": "BattleActionDamage", "damage_action": {
                "attacker_id": 100266, "details": [{"target_id": 100378, "value": 1234,
                                                     "cur_hp": 5678, "is_crit": False}]}}}


def _battle_lines(path):
    return [line for line in path.read_text(encoding="utf-8").splitlines()
            if not any(marker in line for marker in (" GAME_CLOCK_TICK ", " GAME_CLOCK_SYNC ",
                                                     " GAME_TIME_FREEZE_REQUEST ", " GAME_TIME_FREEZE_STATE "))]


@pytest.mark.parametrize("observation", OBSERVATIONS, ids=lambda event: event["type"])
@pytest.mark.parametrize("kind", ["add", "create"])
def test_clock_interleaving_preserves_buff_creation_blackboard_and_damage(tmp_path, kind, observation):
    plain_path, observed_path = tmp_path / "plain.log", tmp_path / "observed.log"
    plain, observed = _bridge(plain_path), _bridge(observed_path)
    first, update, damage = _buff_event(kind, 0.084, 10), _buff_event(kind, 0.090, 20), _damage_event()
    plain.handle_event(first)
    observed.handle_event(first)
    observed.handle_event({**observation, "timestamp_ms": 50000})
    plain.handle_event(update)
    observed.handle_event(update)
    plain.handle_event(damage)
    observed.handle_event(damage)
    lines = _battle_lines(plain_path)
    assert lines == _battle_lines(observed_path)
    assert any(" BUFF_START " in line and "uid=9001" in line for line in lines)
    if kind == "create":
        assert any("BB[9001]: spell_dmg_taken_up2=0.084" in line for line in lines)
    assert any(" HP_V2 " in line and "packetSeq=30 clientTick=13345" in line for line in lines)
    assert observed.buff_values_by_uid == plain.buff_values_by_uid
    assert observed.buff_actor_ids_by_uid == plain.buff_actor_ids_by_uid
    assert observed.buff_seq == plain.buff_seq
    assert observed._last_ts_ms == plain._last_ts_ms == 2000


@pytest.mark.parametrize("observation", OBSERVATIONS, ids=lambda event: event["type"])
def test_clock_does_not_flush_any_pending_evidence_or_change_capture_end_timestamp(tmp_path, observation):
    bridge = _bridge(tmp_path / "observed.log")
    bridge._last_ts_ms = 1000
    bridge._pending_add_buff_ts_ms = 1000
    bridge._pending_add_buff_events = [_buff_event("add", 0.084, 10)]
    bridge._pending_create_buff_ts_ms = 1000
    bridge._pending_create_buff_events = [_buff_event("create", 0.090, 20)]
    bridge._pending_buff_start_ts_ms = 1000
    bridge._pending_buff_start_order = [9002]
    bridge._pending_buff_start_rows = {9002: ("buff_test", "target", "source", [])}
    before = deepcopy((bridge._pending_add_buff_events, bridge._pending_create_buff_events,
                       bridge._pending_buff_start_rows, bridge._pending_buff_start_order))
    bridge.handle_event({**observation, "timestamp_ms": 50000})
    assert before == (bridge._pending_add_buff_events, bridge._pending_create_buff_events,
                      bridge._pending_buff_start_rows, bridge._pending_buff_start_order)
    assert bridge._pending_add_buff_ts_ms == bridge._pending_create_buff_ts_ms == bridge._pending_buff_start_ts_ms == 1000
    assert bridge._last_ts_ms == 1000
    observed_boundaries = []
    bridge._record_missing_official_timer = lambda ts_ms, **kwargs: observed_boundaries.append(ts_ms)
    bridge.end_capture_session()
    assert observed_boundaries == [1000]


@pytest.mark.parametrize("observation", OBSERVATIONS, ids=lambda event: event["type"])
def test_missing_clock_receipt_timestamp_is_not_filled_from_wall_clock(tmp_path, observation):
    path = tmp_path / "missing.log"
    bridge = _bridge(path)
    bridge._last_ts_ms = 1000
    bridge.handle_event(observation)
    assert bridge._last_ts_ms == 1000
    assert path.read_text(encoding="utf-8") == ""


def test_missing_raw_freeze_body_is_distinct_from_an_empty_protobuf_payload(tmp_path):
    path = tmp_path / "raw.log"
    bridge = _bridge(path)
    bridge.handle_event({"type": "SC_GAME_TIME_FREEZE_END_RSP", "timestamp_ms": 1000,
                         "reason": 0, "in_time_freeze": False})
    bridge.handle_event({"type": "SC_GAME_TIME_FREEZE_END_RSP", "timestamp_ms": 1000,
                         "reason": 0, "in_time_freeze": False, "raw_body_hex": ""})
    lines = path.read_text(encoding="utf-8").splitlines()
    assert "rawBodyHex=unknown" in lines[0]
    assert "rawBodyHex=empty" in lines[1]
    assert all("inTimeFreeze=0 reason=0" in line for line in lines)
