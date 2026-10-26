from __future__ import annotations

from pathlib import Path

import pytest

from endfield_pcap.message_registry import MessageDecodeFailure, MessageRegistry
from endfield_pcap.protocol import SessionBodyDecompressionError, maybe_decompress_session_body


def test_activity_conditional_messages_are_decodable() -> None:
    registry = MessageRegistry(Path(__file__).resolve().parents[1] / "data")
    expected = {
        "cs": {
            1463: "CS_CONDITIONAL_MULTI_STAGE_ACTIVITY_GAIN_REWARD",
        },
        "sc": {
            1256: "SC_GAME_MECHANICS_SYNC_COMPLETION_REWARD",
            1654: "SC_ACTIVITY_PROGRESS_CHANGE",
            1656: "SC_ACTIVITY_CONDITIONAL_MULTI_STAGE_BASE_CHANGE",
            1657: "SC_CONDITIONAL_MULTI_STAGE_ACTIVITY_GAIN_REWARD",
            1658: "SC_ACTIVITY_CONDITIONAL_STAGE_PROGRESS_CHANGE",
            1906: "SC_SYNC_WEEK_RAID_SETTLEMENT",
        },
    }

    for direction, messages in expected.items():
        for msg_id, class_name in messages.items():
            assert registry.resolve_class_name(direction, msg_id) == class_name
            assert registry.should_decode_message(direction, msg_id)
            assert registry.load_message_class(class_name) is not None


def test_game_mechanics_completion_reward_is_decodable() -> None:
    registry = MessageRegistry(Path(__file__).resolve().parents[1] / "data")
    message_class = registry.load_message_class("SC_GAME_MECHANICS_SYNC_COMPLETION_REWARD")
    assert message_class is not None

    encoded = message_class(
        game_id="indie_battletower004_ex",
        is_pass=True,
        force_leave_ts=1_721_706_000,
        reward_multiplier=1,
    ).SerializeToString()
    decoded = registry.decode_messages("sc", {"msgid": 1256}, encoded)

    assert len(decoded) == 1
    assert decoded[0].class_name == "SC_GAME_MECHANICS_SYNC_COMPLETION_REWARD"
    assert decoded[0].message.game_id == "indie_battletower004_ex"
    assert decoded[0].message.is_pass is True


def test_week_raid_settlement_is_decodable() -> None:
    registry = MessageRegistry(Path(__file__).resolve().parents[1] / "data")
    message_class = registry.load_message_class("SC_SYNC_WEEK_RAID_SETTLEMENT")
    assert message_class is not None

    encoded = message_class(
        game_id="indie_battletower004_ex",
        bp_score=200,
        danger_meter=100,
        total_playtime=651_250,
    ).SerializeToString()
    decoded = registry.decode_messages("sc", {"msgid": 1906}, encoded)

    assert len(decoded) == 1
    assert decoded[0].class_name == "SC_SYNC_WEEK_RAID_SETTLEMENT"
    assert decoded[0].message.game_id == "indie_battletower004_ex"
    assert decoded[0].message.total_playtime == 651_250


def test_contingency_contract_messages_are_decodable() -> None:
    registry = MessageRegistry(Path(__file__).resolve().parents[1] / "data")
    expected = {
        "cs": {
            1952: "CS_CONTINGENCY_CONTRACT_SET_TAGS",
        },
        "sc": {
            2201: "SC_CONTINGENCY_CONTRACT_SET_TAGS",
            2202: "SC_CONTINGENCY_CONTRACT_TAGS_SYNC",
            2203: "SC_CONTINGENCY_CONTRACT_BATTLE_RESULT",
        },
    }

    for direction, messages in expected.items():
        for msg_id, class_name in messages.items():
            assert registry.resolve_class_name(direction, msg_id) == class_name
            assert registry.should_decode_message(direction, msg_id)
            assert registry.load_message_class(class_name) is not None

    decoded = registry.decode_messages(
        "cs",
        {"msgid": 1952},
        bytes.fromhex(
            "0a11696e6469655f636f6e7472616374303031121e959106b5970691a306e98e06"
            "f19c06d59d0685f836899506a59406f99106"
        ),
    )

    assert len(decoded) == 1
    assert decoded[0].class_name == "CS_CONTINGENCY_CONTRACT_SET_TAGS"
    assert decoded[0].message.dungeon_id == "indie_contract001"
    assert list(decoded[0].message.tag_ids) == [
        100501,
        101301,
        102801,
        100201,
        102001,
        102101,
        900101,
        101001,
        100901,
        100601,
    ]


def test_server_buff_messages_are_decodable():
    registry = MessageRegistry(Path(__file__).resolve().parents[1] / "data")
    message_class = registry.load_message_class("SC_ADD_SERVER_BUFF")
    assert message_class is not None
    decoded = registry.decode_messages(
        "sc",
        {"msgid": 1177},
        message_class(inst_id=100290).SerializeToString(),
    )
    assert len(decoded) == 1
    assert decoded[0].class_name == "SC_ADD_SERVER_BUFF"
    assert decoded[0].message.inst_id == 100290


def test_server_buff_remove_messages_are_decodable():
    registry = MessageRegistry(Path(__file__).resolve().parents[1] / "data")
    message_class = registry.load_message_class("SC_REMOVE_SERVER_BUFF")
    assert message_class is not None
    decoded = registry.decode_messages(
        "sc",
        {"msgid": 1178},
        message_class(inst_id=100290, del_inst_ids=[100291]).SerializeToString(),
    )
    assert len(decoded) == 1
    assert decoded[0].class_name == "SC_REMOVE_SERVER_BUFF"
    assert list(decoded[0].message.del_inst_ids) == [100291]


def test_compressed_session_body_failure_is_not_treated_as_plain_protobuf() -> None:
    with pytest.raises(SessionBodyDecompressionError):
        maybe_decompress_session_body({"msgid": 1952, "is_compress": True}, b"not-compressed")


def test_interesting_protobuf_decode_failure_is_explicit() -> None:
    registry = MessageRegistry(Path(__file__).resolve().parents[1] / "data")

    with pytest.raises(MessageDecodeFailure):
        registry.decode_messages("cs", {"msgid": 1952}, b"\x80")
