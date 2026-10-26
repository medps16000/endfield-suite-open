from __future__ import annotations

import importlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from google.protobuf.message import DecodeError, Message

from .protocol import iter_merged_frames, maybe_decompress_session_body, parse_head

LOGGER = logging.getLogger(__name__)


class MessageDecodeFailure(RuntimeError):
    """An interesting mapped protobuf message could not be decoded."""

_CUSTOM_MESSAGE_IDS: dict[str, dict[int, str]] = {
    "cs": {
        1952: "CS_CONTINGENCY_CONTRACT_SET_TAGS",
    },
    "sc": {
        2201: "SC_CONTINGENCY_CONTRACT_SET_TAGS",
        2202: "SC_CONTINGENCY_CONTRACT_TAGS_SYNC",
        2203: "SC_CONTINGENCY_CONTRACT_BATTLE_RESULT",
    },
}


@dataclass(slots=True)
class DecodedMessage:
    direction: str
    class_name: str
    msg_id: int
    head: dict[str, Any]
    message: Message
    raw_body: bytes


class MessageRegistry:
    def __init__(self, data_dir: Path, package_name: str = "endfield_pcap.proto_generated") -> None:
        self._package_name = package_name
        self._class_cache: dict[str, type[Message]] = {}
        # Read-only observer quality accounting; legacy decoding is unchanged.
        self.observer_framing_errors = 0
        self._maps = {
            "cs": self._load_map(data_dir / "message_ids_cs.json"),
            "sc": self._load_map(data_dir / "message_ids_sc.json"),
        }
        for direction, mapping in _CUSTOM_MESSAGE_IDS.items():
            self._maps.setdefault(direction, {}).update(mapping)
        self._interesting_class_names = {
            "CS_CONTINGENCY_CONTRACT_SET_TAGS",
            "CS_BATTLE_OP",
            "CS_PING",
            "SC_PING",
            "CS_GAME_TIME_FREEZE_START_REQ",
            "CS_GAME_TIME_FREEZE_END_REQ",
            "SC_GAME_TIME_FREEZE_START_RSP",
            "SC_GAME_TIME_FREEZE_END_RSP",
            "CS_DEV_CLEAR_BATTLE_INFO",
            "CS_ENTER_DUNGEON",
            "CS_GAME_MECHANICS_NTF_INST_PREPARE_FINISH",
            "CS_GAME_MECHANICS_REQ_START",
            "CS_GAME_MECHANICS_REQ_STOP",
            "CS_CONDITIONAL_MULTI_STAGE_ACTIVITY_GAIN_REWARD",
            "CS_LEAVE_DUNGEON",
            "CS_SCENE_LOAD_FINISH",
            "CS_SCENE_SET_BATTLE",
            "SC_ACTIVITY_CONDITIONAL_MULTI_STAGE_BASE_CHANGE",
            "SC_ACTIVITY_CONDITIONAL_STAGE_PROGRESS_CHANGE",
            "SC_ACTIVITY_PROGRESS_CHANGE",
            "SC_BATTLE_DEBUG_INFO",
            "SC_BATTLE_ADD_GLOBAL_BUFF",
            "SC_BATTLE_GENERATION_CHANGE",
            "SC_BATTLE_REMOVE_GLOBAL_BUFF",
            "SC_BATTLE_SYNC_GLOBAL_BUFF_INFO",
            "SC_BATTLE_SYNC_SEQ_ID",
            "SC_CHAR_BAG_SET_CURR_TEAM_INDEX",
            "SC_CHAR_BAG_SET_CURR_TEAM_TYPE",
            "SC_CHAR_BAG_SET_MAX_TEAM_MEMBER_COUNT",
            "SC_CHAR_BAG_SET_TEAM",
            "SC_CHAR_BAG_SET_TEAM_LEADER",
            "SC_CHAR_BAG_SYNC_PERSISTENT_BUFFS",
            "SC_CONTINGENCY_CONTRACT_BATTLE_RESULT",
            "SC_CONTINGENCY_CONTRACT_SET_TAGS",
            "SC_CONTINGENCY_CONTRACT_TAGS_SYNC",
            "SC_CHAR_SKILL_INFOS",
            "SC_EQUIP_ENHANCE",
            "SC_EQUIP_MEDICINE_MODIFY",
            "SC_EQUIP_PRODUCE",
            "SC_EQUIP_PUTOFF",
            "SC_EQUIP_PUTON",
            "SC_ITEM_BAG_SCOPE_MODIFY",
            "SC_ITEM_BAG_SCOPE_SYNC",
            "SC_OBJECT_ENTER_VIEW",
            "SC_OBJECT_LEAVE_VIEW",
            "SC_ATTACH_SERVER_SKILL",
            "SC_ADD_SERVER_BUFF",
            "SC_REMOVE_SERVER_BUFF",
            "SC_DETACH_SERVER_SKILL",
            "SC_ENTER_DUNGEON",
            "SC_RESET_BATTLE_STATUS",
            "SC_SCENE_RESET_ENTITY",
            "SC_SCENE_SET_BATTLE",
            "SC_SELF_SCENE_INFO",
            "SC_SYNC_ATTR",
            "SC_SYNC_FULL_DUNGEON_STATUS",
            "SC_SYNC_RACING_DUNGEON_BUFF_MODIFY",
            "SC_SYNC_RACING_DUNGEON_RECONNECT",
            "SC_GAME_MECHANICS_MODIFY_INST_TIME_FREEZE",
            "SC_GAME_MECHANICS_SYNC_CHALLENGE_COMPLETE",
            "SC_GAME_MECHANICS_SYNC_CHALLENGE_START",
            "SC_GAME_MECHANICS_SYNC_COMPLETION_REWARD",
            "SC_GAME_MECHANICS_SYNC_ENTER_GAME_INST",
            "SC_GAME_MECHANICS_SYNC_LEAVE_GAME_INST",
            "SC_GAME_MECHANICS_SYNC_RESTART_GAME_INST",
            "SC_CONDITIONAL_MULTI_STAGE_ACTIVITY_GAIN_REWARD",
            "SC_SYNC_WEEK_RAID_SETTLEMENT",
            "SC_SYNC_CHAR_BAG_INFO",
            "SC_UPDATE_SERVER_SKILL",
            "SC_WEAPON_ADD_EXP",
            "SC_WEAPON_ATTACH_GEM",
            "SC_WEAPON_BREAKTHROUGH",
            "SC_WEAPON_DETACH_GEM",
            "SC_WEAPON_PUTON",
            "SC_WEAPON_REFINE_UPGRADE",
            "CS_MERGE_MSG",
            "SC_MERGE_MSG",
        }
        self._interesting_msg_ids = {
            direction: {
                msg_id
                for msg_id, class_name in mapping.items()
                if class_name in self._interesting_class_names
            }
            for direction, mapping in self._maps.items()
        }

    @staticmethod
    def _load_map(path: Path) -> dict[int, str]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        values = payload.values() if isinstance(payload, dict) else payload
        return {
            int(entry["msg_id"]): str(entry["class_name"])
            for entry in values
            if entry.get("msg_id") is not None and entry.get("class_name")
        }

    def resolve_class_name(self, direction: str, msg_id: int) -> str | None:
        return self._maps.get(direction, {}).get(msg_id)

    def should_decode_message(self, direction: str, msg_id: int) -> bool:
        return msg_id in self._interesting_msg_ids.get(direction, set())

    def load_message_class(self, class_name: str) -> type[Message] | None:
        cached = self._class_cache.get(class_name)
        if cached is not None:
            return cached

        try:
            module = importlib.import_module(f"{self._package_name}.{class_name}_pb2")
        except ModuleNotFoundError:
            LOGGER.error("protobuf module is missing for %s", class_name)
            return None
        except Exception:
            LOGGER.warning("failed to import protobuf module for %s", class_name, exc_info=True)
            return None

        cls = getattr(module, class_name, None)
        if cls is None:
            LOGGER.error("protobuf class %s is missing from its generated module", class_name)
            return None
        self._class_cache[class_name] = cls
        return cls

    def decode_messages(self, direction: str, head: dict[str, Any], body: bytes) -> list[DecodedMessage]:
        msg_id = int(head.get("msgid", 0))
        if not self.should_decode_message(direction, msg_id):
            return []
        class_name = self.resolve_class_name(direction, msg_id)
        if not class_name:
            return []
        message_class = self.load_message_class(class_name)
        if message_class is None:
            raise MessageDecodeFailure(
                f"protobuf class unavailable: direction={direction} msg_id={msg_id} class={class_name}"
            )

        message = message_class()
        try:
            message.ParseFromString(body)
        except DecodeError as exc:
            raise MessageDecodeFailure(
                f"protobuf decode failed: direction={direction} msg_id={msg_id} class={class_name} body_len={len(body)}"
            ) from exc

        decoded = [
            DecodedMessage(
                direction=direction,
                class_name=class_name,
                msg_id=msg_id,
                head=head,
                message=message,
                raw_body=body,
            )
        ]
        if class_name.endswith("MERGE_MSG"):
            merged_bytes = getattr(message, "msg", b"")
            if merged_bytes:
                consumed = 0
                for offset, sub_head_len, sub_head_bytes, sub_body in iter_merged_frames(merged_bytes):
                    consumed = offset + 3 + sub_head_len + len(sub_body)
                    sub_head = parse_head(sub_head_bytes)
                    if "parse_error" in sub_head:
                        self.observer_framing_errors += 1
                    sub_body = maybe_decompress_session_body(sub_head, sub_body)
                    decoded.extend(self.decode_messages(direction, sub_head, sub_body))
                if consumed != len(merged_bytes):
                    self.observer_framing_errors += 1
        return decoded

