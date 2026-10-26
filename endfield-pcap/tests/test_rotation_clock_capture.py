from pathlib import Path
from collections import Counter

from endfield_pcap.message_registry import MessageRegistry
from endfield_pcap.models import RuntimeMetrics
from endfield_pcap.service import SessionPipeline
from endfield_pcap.trace_bridge import TraceBridge


def pipeline():
    value = SessionPipeline.__new__(SessionPipeline)
    value.session_id = "clock-test"
    value.metrics = RuntimeMetrics()
    events = []
    value.on_event = events.append
    value.decoded_class_counts = Counter()
    value.on_debug_message = None
    value.rotation_clock_trace = True
    return value, events


def test_clock_messages_decode_and_preserve_explicit_default_fields(tmp_path):
    registry = MessageRegistry(Path(__file__).resolve().parents[1] / "data")
    cases = [
        ("cs", 5, "CS_PING", {"client_ts": 123, "logical_ts": 456}),
        ("sc", 5, "SC_PING", {"client_ts": 123, "server_ts": 1790000000123}),
        ("cs", 1452, "CS_GAME_TIME_FREEZE_START_REQ", {"reason": 0}),
        ("cs", 1453, "CS_GAME_TIME_FREEZE_END_REQ", {"reason": 0}),
        ("sc", 1642, "SC_GAME_TIME_FREEZE_START_RSP", {"reason": 0, "in_time_freeze": True}),
        ("sc", 1643, "SC_GAME_TIME_FREEZE_END_RSP", {"reason": 0, "in_time_freeze": False}),
    ]
    value, events = pipeline()
    bridge = TraceBridge(tmp_path / "clock.log")
    for direction, message_id, class_name, fields in cases:
        assert registry.should_decode_message(direction, message_id)
        message_class = registry.load_message_class(class_name)
        assert message_class is not None
        decoded = registry.decode_messages(direction, {"msgid": message_id}, message_class(**fields).SerializeToString())
        value._handle_message(decoded[0], 1000)
        event = events[-1].as_dict()
        for key, field in fields.items():
            assert event[key] == (str(field) if key.endswith("_ts") else field)
        bridge.handle_event(event)
    lines = (tmp_path / "clock.log").read_text(encoding="utf-8")
    assert "source=CS_PING msgId=5 captureMs=1000 clientTs=123 logicalTs=456" in lines
    assert "source=SC_PING msgId=5 captureMs=1000 clientTs=123 serverTs=1790000000123" in lines
    assert "source=SC_GAME_TIME_FREEZE_END_RSP inTimeFreeze=0 reason=0" in lines
    assert "OFFICIAL_TIMER_PROGRESS" not in lines


def test_duplicate_operation_ticks_emit_one_observation_per_packet():
    from endfield_pcap.proto_generated.CS_BATTLE_OP_pb2 import CS_BATTLE_OP
    message = CS_BATTLE_OP()
    enum = message.client_data.op_list.add().DESCRIPTOR.fields_by_name["op_type"].enum_type
    end_cast_value = enum.values_by_name["BattleOpSkillEndCast"].number
    del message.client_data.op_list[:]
    for seq, tick in ((1, 12345), (2, 12345), (3, 12400)):
        op = message.client_data.op_list.add(seq_id=seq, client_tick_tms=tick, op_type=end_cast_value)
        op.skill_end_cast_op_data.inst_id = seq + 100
    value, events = pipeline()
    value._emit_battle_events(message, 2000)
    ticks = [event.payload for event in events if event.event_type == "GAME_CLOCK_TICK"]
    assert ticks == [{"source": "CS_BATTLE_OP", "tick_ms": 12345, "seq_id": 1},
                     {"source": "CS_BATTLE_OP", "tick_ms": 12400, "seq_id": 3}]
    assert len([event for event in events if event.event_type == "BattleOpSkillEndCast"]) == 3
