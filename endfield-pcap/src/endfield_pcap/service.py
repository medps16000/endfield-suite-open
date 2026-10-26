from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import json
import logging
import re
import threading
import uuid
from collections import Counter, defaultdict, deque
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import psutil
import websockets
from google.protobuf.json_format import MessageToDict

from .crypto.srsa_bridge import SRSABridge
from .crypto.xxe1 import XXE1
from .flow import TcpStreamReassembler
from .game_data import load_name_index
from .loadout_static import (
    equip_piece_catalog,
    format_equip_stats,
    format_gem_terms,
    format_weapon_refine_stats,
    infer_weapon_refine_from_source_skills,
    normalize_gem_payload,
    weapon_base_atk,
    weapon_base_atk_bounds,
)
from .message_registry import DecodedMessage, MessageDecodeFailure, MessageRegistry
from .models import (
    BattleLogEvent,
    CapturedPacket,
    Endpoint,
    EntityInfo,
    FlowKey,
    OutboundEvent,
    RuntimeMetrics,
    ServiceObserver,
    ServiceState,
    SquadMember,
)
from .npcap import CaptureManager
from .protocol import (
    SessionBodyDecompressionError,
    load_private_key_from_txt,
    maybe_decompress_session_body,
    parse_head,
    parse_sc_login,
    pop_frame,
    rsa_decrypt_session_key,
)
from .runtime_paths import bundle_root
from .trace_bridge import TraceBridge, make_archive_trace_file

LOGGER = logging.getLogger(__name__)

_CONTRACT_TAG_CLASS_NAMES = {
    "CS_CONTINGENCY_CONTRACT_SET_TAGS",
    "SC_CONTINGENCY_CONTRACT_SET_TAGS",
    "SC_CONTINGENCY_CONTRACT_TAGS_SYNC",
    "SC_CONTINGENCY_CONTRACT_BATTLE_RESULT",
}


def _load_multi_phase_dungeon_map(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return {}
    result: dict[str, str] = {}
    for dungeon_id, templateid in payload.items():
        dungeon = str(dungeon_id or "").strip()
        template = str(templateid or "").strip()
        if dungeon and template:
            result[dungeon] = template
    return result


def _attrs_payload(source: Any) -> list[dict[str, float | int]]:
    rows: list[dict[str, float | int]] = []
    attrs = getattr(source, "attrs", None)
    if attrs is None:
        attrs = getattr(source, "attr_list", [])
    for attr in attrs:
        attr_type = int(getattr(attr, "attr_type", 0) or 0)
        if not attr_type:
            continue
        rows.append(
            {
                "attr_type": attr_type,
                "basic_value": float(getattr(attr, "basic_value", 0.0) or 0.0),
                "value": float(getattr(attr, "value", 0.0) or 0.0),
            }
        )
    return rows


def _proto_has_field(message: Any, field_name: str) -> bool:
    try:
        return bool(message.HasField(field_name))
    except (AttributeError, ValueError):
        return getattr(message, field_name, None) is not None


def _optional_int(value: Any) -> int | None:
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return None
    return number or None


@dataclass(slots=True)
class ServiceConfig:
    ws_port: int
    log_dir: Path
    log_level: str
    game_exe: str
    npcap_device: str
    dll_dir: Path
    debug_enabled: bool
    debug_dir: Path
    rsa_key_txt: Path
    name_index_path: Path
    trace_file: Path | None = None
    status_file: Path | None = None
    trace_enabled: bool = True
    merge_multi_phase_enemy_battles: bool = False


class SessionPipeline:
    ENDMIN_VARIANTS = ("chr_0002_endminm", "chr_0003_endminf")

    def __init__(
        self,
        flow: FlowKey,
        session_id: str,
        registry: MessageRegistry,
        private_key: Any,
        srsa_bridge: SRSABridge,
        name_index: dict[str, str],
        multi_phase_dungeon_map: dict[str, str],
        merge_multi_phase_enemy_battles: bool,
        on_event: Callable[[OutboundEvent], None],
        on_debug_message: Callable[[DecodedMessage, int], None] | None,
        on_debug_record: Callable[[dict[str, object]], None] | None,
        metrics: RuntimeMetrics,
        rotation_clock_trace: bool = True,
        on_battle_operation: Callable[[dict[str, object]], None] | None = None,
    ) -> None:
        self.flow = flow
        self.session_id = session_id
        self.registry = registry
        self.private_key = private_key
        self.srsa_bridge = srsa_bridge
        self.name_index = name_index
        self.multi_phase_dungeon_map = multi_phase_dungeon_map
        self.merge_multi_phase_enemy_battles = merge_multi_phase_enemy_battles
        self.on_event = on_event
        self.on_battle_operation = on_battle_operation
        self._observer_operation_index = 0
        self._observer_framing_errors = 0
        self.on_debug_message = on_debug_message
        self.on_debug_record = on_debug_record
        self.metrics = metrics
        self.rotation_clock_trace = bool(rotation_clock_trace)

        self.client_reassembler = TcpStreamReassembler()
        self.server_reassembler = TcpStreamReassembler()
        self.client_buffer = bytearray()
        self.server_buffer = bytearray()
        self.pending_client_session_frames: deque[tuple[int, bytes, bytes, int]] = deque()
        self._last_gap_report: dict[str, tuple[int, int, int]] = {}
        self.pending_multi_frames: dict[tuple[str, int, int], dict[str, object]] = {}
        self.first_packet_ts_ms: int | None = None
        self.startup_tcp_gap_count = 0
        self.startup_tcp_gap_max_missing_bytes = 0
        self.reliability_flags: set[str] = set()
        self._startup_gap_warning_emitted = False

        self.client_login_done = False
        self.server_login_done = False
        self.client_cipher: XXE1 | None = None
        self.server_cipher: XXE1 | None = None

        self.entity_index: dict[int, EntityInfo] = {}
        self.obj_to_battle: dict[int, int] = {}
        self.squad_index: dict[int, SquadMember] = {}
        self.char_potential_levels: dict[str, int] = {}
        self.skill_levels_by_battle_inst: dict[int, dict[str, int]] = {}
        self.global_skill_levels: dict[str, int] = {}
        self.decoded_class_counts: Counter[str] = Counter()
        self.char_bag_by_objid: dict[int, dict[str, object]] = {}
        self.current_team_char_ids: list[int] = []
        self.char_team_state_by_scope: dict[int, dict[str, Any]] = {}
        self.current_team_scope_name: int | None = None
        self.current_team_selection_known = False
        self.scene_team_char_ids: list[int] = []
        # None means CS_ENTER_DUNGEON did not carry an explicit party override.
        # The packet commonly omits char_team for War Echo even though the
        # current bag team is the actual battle roster.
        self.dungeon_team_char_ids: list[int] | None = None
        self.equip_by_inst_id: dict[int, dict[str, object]] = {}
        self.weapon_by_inst_id: dict[int, dict[str, object]] = {}
        self.gem_by_inst_id: dict[int, dict[str, object]] = {}
        self.gem_by_weapon_id: dict[int, dict[str, object]] = {}
        self.server_skill_state_by_owner_inst: dict[int, dict[int, dict[str, object | None]]] = {}
        self.server_skill_owner_by_skill_inst: dict[int, int] = {}
        self._last_loadout_signature: str | None = None
        self.tracked_dungeon_id: str | None = None
        self.tracked_enemy_templateid: str | None = None
        self.tracked_enemy_inst_ids: set[int] = set()
        self.merged_battle_active = False

    @property
    def is_live(self) -> bool:
        return (
            self.client_login_done
            and self.server_login_done
            and self.client_cipher is not None
            and self.server_cipher is not None
        )

    def status_snapshot(self) -> dict[str, object]:
        return {
            "client_login_done": self.client_login_done,
            "server_login_done": self.server_login_done,
            "client_cipher_ready": self.client_cipher is not None,
            "server_cipher_ready": self.server_cipher is not None,
            "client_buffer_len": len(self.client_buffer),
            "server_buffer_len": len(self.server_buffer),
            "pending_client_session_frames": len(self.pending_client_session_frames),
            "decoded_class_counts": dict(self.decoded_class_counts.most_common(30)),
            "reliability_flags": sorted(self.reliability_flags),
            "startup_tcp_gap_count": self.startup_tcp_gap_count,
            "startup_tcp_gap_max_missing_bytes": self.startup_tcp_gap_max_missing_bytes,
            "tcp_gap": {
                direction: {
                    "missing_from_seq": gap[0],
                    "next_seen_seq": gap[1],
                    "missing_bytes": gap[2],
                }
                for direction, gap in self._last_gap_report.items()
            },
        }

    def process_packet(self, packet: CapturedPacket) -> None:
        if self.first_packet_ts_ms is None:
            self.first_packet_ts_ms = packet.timestamp_ms
        direction = "cs" if packet.src == self.flow.client else "sc"
        reassembler = self.client_reassembler if direction == "cs" else self.server_reassembler
        buffer = self.client_buffer if direction == "cs" else self.server_buffer
        flushed_chunks = reassembler.accept(packet.seq, packet.payload)
        if flushed_chunks:
            self._last_gap_report.pop(direction, None)
        else:
            gap = reassembler.gap_state()
            if gap is not None and self._last_gap_report.get(direction) != gap:
                self._last_gap_report[direction] = gap
                missing_from_seq, next_seen_seq, missing_bytes = gap
                self._note_tcp_gap(packet.timestamp_ms, direction, missing_bytes)
                if self.on_debug_record is not None:
                    self.on_debug_record(
                        {
                            "type": "debug_tcp_gap",
                            "session_id": self.session_id,
                            "timestamp_ms": packet.timestamp_ms,
                            "direction": direction,
                            "missing_from_seq": missing_from_seq,
                            "next_seen_seq": next_seen_seq,
                            "missing_bytes": missing_bytes,
                            "packet_seq": packet.seq,
                            "packet_len": len(packet.payload),
                        }
                    )
        for chunk in flushed_chunks:
            buffer.extend(chunk)
            self._drain_direction(direction, packet.timestamp_ms)

    def _note_tcp_gap(self, timestamp_ms: int, direction: str, missing_bytes: int) -> None:
        if self.first_packet_ts_ms is None:
            return
        if timestamp_ms - self.first_packet_ts_ms > 5000:
            return
        self.startup_tcp_gap_count += 1
        self.startup_tcp_gap_max_missing_bytes = max(self.startup_tcp_gap_max_missing_bytes, int(missing_bytes))
        self.reliability_flags.add("startup_tcp_gap")
        if self.startup_tcp_gap_max_missing_bytes >= 4096:
            self.reliability_flags.add("startup_tcp_gap_large")
        if self._startup_gap_warning_emitted:
            return
        self._startup_gap_warning_emitted = True
        self._emit_event(
            BattleLogEvent(
                session_id=self.session_id,
                timestamp_ms=timestamp_ms,
                event_type="SESSION_WARNING",
                payload={
                    "kind": "startup_tcp_gap",
                    "direction": direction,
                    "missing_bytes": int(missing_bytes),
                    "startup_tcp_gap_count": self.startup_tcp_gap_count,
                },
            )
        )

    def _drain_direction(self, direction: str, timestamp_ms: int) -> None:
        buffer = self.client_buffer if direction == "cs" else self.server_buffer
        while True:
            frame = pop_frame(buffer)
            if frame is None:
                return
            head_len, head_bytes, payload = frame
            if direction == "cs" and not self.client_login_done:
                self.client_login_done = True
                continue
            if direction == "sc" and not self.server_login_done:
                plain = self.srsa_bridge.decrypt_login_body(payload)
                login_info = parse_sc_login(plain)
                session_key = rsa_decrypt_session_key(self.private_key, login_info["session_key_encrypted"])
                nonce = login_info["session_nonce"]
                self.client_cipher = XXE1(session_key, nonce, counter=1)
                self.server_cipher = XXE1(session_key, nonce, counter=1)
                self.server_login_done = True
                self._drain_pending_client_session_frames()
                continue
            cipher = self.client_cipher if direction == "cs" else self.server_cipher
            if cipher is None:
                if direction == "cs":
                    self.pending_client_session_frames.append((head_len, head_bytes, payload, timestamp_ms))
                continue
            self._decode_session_frame(direction, head_len, head_bytes, payload, timestamp_ms)

    def _drain_pending_client_session_frames(self) -> None:
        while self.pending_client_session_frames and self.client_cipher is not None:
            head_len, head_bytes, payload, timestamp_ms = self.pending_client_session_frames.popleft()
            self._decode_session_frame("cs", head_len, head_bytes, payload, timestamp_ms)

    def _decode_session_frame(
        self,
        direction: str,
        head_len: int,
        head_bytes: bytes,
        payload: bytes,
        timestamp_ms: int,
    ) -> None:
        observer_errors_before = (getattr(self, "_observer_framing_errors", 0)
                                  + getattr(getattr(self, "registry", None), "observer_framing_errors", 0))
        cipher = self.client_cipher if direction == "cs" else self.server_cipher
        if cipher is None:
            return
        plain = cipher.process(head_bytes + payload)
        plain_head = plain[:head_len]
        plain_body = plain[head_len:]
        head = parse_head(plain_head)
        if self.on_debug_record is not None and "parse_error" in head:
            self.on_debug_record(
                {
                    "type": "debug_frame_error",
                    "session_id": self.session_id,
                    "timestamp_ms": timestamp_ms,
                    "direction": direction,
                    "error": head["parse_error"],
                    "head_len": head_len,
                    "plain_head_hex": plain_head.hex(),
                    "plain_body_prefix_hex": plain_body[:64].hex(),
                }
            )
        if "parse_error" in head:
            self._observer_framing_errors = getattr(self, "_observer_framing_errors", 0) + 1
            return
        if self.on_debug_record is not None:
            msg_id = int(head.get("msgid", 0) or 0)
            self.on_debug_record(
                {
                    "type": "debug_session_head",
                    "session_id": self.session_id,
                    "timestamp_ms": timestamp_ms,
                    "direction": direction,
                    "msg_id": msg_id,
                    "class_name": self.registry.resolve_class_name(direction, msg_id),
                    "head": head,
                    "head_len": head_len,
                    "plain_head_hex": plain_head.hex(),
                    "plain_body_len": len(plain_body),
                    "plain_body_prefix_hex": plain_body[:64].hex(),
                    "plain_body_hex": plain_body.hex() if len(plain_body) <= 4096 else None,
                }
            )
        try:
            assembled = self._assemble_multi_pack_message(direction, head, plain_body, timestamp_ms)
        except SessionBodyDecompressionError as exc:
            self.metrics.decompression_errors += 1
            self.reliability_flags.add("session_body_decompression_failed")
            LOGGER.error("session frame decompression failed", exc_info=True)
            if self.on_debug_record is not None:
                self.on_debug_record(
                    {
                        "type": "debug_decompression_error",
                        "session_id": self.session_id,
                        "timestamp_ms": timestamp_ms,
                        "direction": direction,
                        "msg_id": int(head.get("msgid", 0) or 0),
                        "error": str(exc),
                        "head": head,
                        "body_len": len(plain_body),
                        "body_prefix_hex": plain_body[:128].hex(),
                    }
                )
            return
        if assembled is None:
            return
        head, body, is_already_decompressed = assembled
        msg_id = int(head.get("msgid", 0) or 0)
        if not self.registry.should_decode_message(direction, msg_id):
            if self.on_debug_record is not None:
                try:
                    debug_body = body if is_already_decompressed else maybe_decompress_session_body(head, body)
                except SessionBodyDecompressionError as exc:
                    debug_body = body
                    self.metrics.decompression_errors += 1
                    self.reliability_flags.add("session_body_decompression_failed")
                    LOGGER.error("ignored session frame decompression failed", exc_info=True)
                    self.on_debug_record(
                        {
                            "type": "debug_decompression_error",
                            "session_id": self.session_id,
                            "timestamp_ms": timestamp_ms,
                            "direction": direction,
                            "msg_id": msg_id,
                            "error": str(exc),
                            "head": head,
                            "body_len": len(body),
                            "body_prefix_hex": body[:128].hex(),
                        }
                    )
                self.on_debug_record(
                    {
                        "type": "debug_ignored_frame",
                        "session_id": self.session_id,
                        "timestamp_ms": timestamp_ms,
                        "direction": direction,
                        "msg_id": msg_id,
                        "class_name": self.registry.resolve_class_name(direction, msg_id),
                        "head": head,
                        "body_len": len(debug_body),
                        "body_prefix_hex": debug_body[:128].hex(),
                        "body_hex": debug_body.hex() if len(debug_body) <= 8192 else None,
                    }
                )
            return
        if not is_already_decompressed:
            try:
                body = maybe_decompress_session_body(head, body)
            except SessionBodyDecompressionError as exc:
                self.metrics.decompression_errors += 1
                self.reliability_flags.add("session_body_decompression_failed")
                LOGGER.error("session frame decompression failed", exc_info=True)
                if self.on_debug_record is not None:
                    self.on_debug_record(
                        {
                            "type": "debug_decompression_error",
                            "session_id": self.session_id,
                            "timestamp_ms": timestamp_ms,
                            "direction": direction,
                            "msg_id": msg_id,
                            "error": str(exc),
                            "head": head,
                            "body_len": len(body),
                            "body_prefix_hex": body[:128].hex(),
                        }
                    )
                return
        try:
            decoded_messages = self.registry.decode_messages(direction, head, body)
        except MessageDecodeFailure as exc:
            self.metrics.protobuf_decode_errors += 1
            self.reliability_flags.add("protobuf_decode_failed")
            LOGGER.error("session protobuf decode failed", exc_info=True)
            if self.on_debug_record is not None:
                self.on_debug_record(
                    {
                        "type": "debug_protobuf_error",
                        "session_id": self.session_id,
                        "timestamp_ms": timestamp_ms,
                        "direction": direction,
                        "msg_id": msg_id,
                        "class_name": self.registry.resolve_class_name(direction, msg_id),
                        "error": str(exc),
                        "head": head,
                        "body_len": len(body),
                        "body_prefix_hex": body[:128].hex(),
                    }
                )
            return
        except SessionBodyDecompressionError as exc:
            self.metrics.decompression_errors += 1
            self.reliability_flags.add("session_body_decompression_failed")
            LOGGER.error("merged protobuf payload decompression failed", exc_info=True)
            if self.on_debug_record is not None:
                self.on_debug_record(
                    {
                        "type": "debug_decompression_error",
                        "session_id": self.session_id,
                        "timestamp_ms": timestamp_ms,
                        "direction": direction,
                        "msg_id": msg_id,
                        "class_name": self.registry.resolve_class_name(direction, msg_id),
                        "error": str(exc),
                        "head": head,
                        "body_len": len(body),
                        "body_prefix_hex": body[:128].hex(),
                    }
                )
            return
        self.metrics.frames_decoded += 1
        if self.on_debug_record is not None and not decoded_messages:
            self.on_debug_record(
                {
                    "type": "debug_undecoded_frame",
                    "session_id": self.session_id,
                    "timestamp_ms": timestamp_ms,
                    "direction": direction,
                    "head": head,
                    "head_len": head_len,
                    "plain_head_hex": plain_head.hex(),
                    "plain_body_prefix_hex": body[:128].hex(),
                }
            )
        previous_observer_frame_invalid = getattr(self, "_observer_frame_invalid", False)
        self._observer_frame_invalid = (previous_observer_frame_invalid or
            getattr(self, "_observer_framing_errors", 0) + getattr(self.registry, "observer_framing_errors", 0) > observer_errors_before)
        try:
            for decoded in decoded_messages:
                self.metrics.messages_decoded += 1
                self._handle_message(decoded, timestamp_ms)
        finally:
            self._observer_frame_invalid = previous_observer_frame_invalid

    def _assemble_multi_pack_message(
        self,
        direction: str,
        head: dict[str, Any],
        plain_body: bytes,
        timestamp_ms: int,
    ) -> tuple[dict[str, Any], bytes, bool] | None:
        total_pack_count = int(head.get("total_pack_count", 1) or 1)
        if total_pack_count <= 1:
            return head, plain_body, False

        current_pack_index = int(head.get("current_pack_index", 0) or 0)
        seqid = int(head.get("down_seqid", head.get("up_seqid", 0)) or 0)
        if current_pack_index < 0 or current_pack_index >= total_pack_count:
            self._observer_framing_errors = getattr(self, "_observer_framing_errors", 0) + 1
        if seqid <= 0:
            self._observer_framing_errors = getattr(self, "_observer_framing_errors", 0) + 1
            return None
        base_seqid = seqid - current_pack_index
        key = (direction, int(head.get("msgid", 0) or 0), base_seqid)
        state = self.pending_multi_frames.setdefault(
            key,
            {
                "total_pack_count": total_pack_count,
                "head": dict(head),
                "parts": {},
                "first_timestamp_ms": timestamp_ms,
                "last_timestamp_ms": timestamp_ms,
            },
        )
        parts = state["parts"]
        assert isinstance(parts, dict)
        part_body = maybe_decompress_session_body(head, plain_body)
        if (state["total_pack_count"] != total_pack_count or
                (current_pack_index in parts and parts[current_pack_index] != part_body)):
            self._observer_framing_errors = getattr(self, "_observer_framing_errors", 0) + 1
        parts[current_pack_index] = part_body
        state["last_timestamp_ms"] = timestamp_ms
        if len(parts) < total_pack_count:
            if self.on_debug_record is not None:
                self.on_debug_record(
                    {
                        "type": "debug_multipack_pending",
                        "session_id": self.session_id,
                        "timestamp_ms": timestamp_ms,
                        "direction": direction,
                        "msg_id": int(head.get("msgid", 0) or 0),
                        "seqid": seqid,
                        "base_seqid": base_seqid,
                        "total_pack_count": total_pack_count,
                        "current_pack_index": current_pack_index,
                        "received_indexes": sorted(int(index) for index in parts.keys()),
                    }
                )
            return None

        combined = b"".join(parts[index] for index in range(total_pack_count) if index in parts)
        if len(parts) != total_pack_count:
            return None
        full_head = dict(state["head"])
        full_head["is_compress"] = False
        self.pending_multi_frames.pop(key, None)
        return full_head, combined, True

    def flush_debug_state(self) -> None:
        if self.pending_multi_frames:
            self._observer_framing_errors = getattr(self, "_observer_framing_errors", 0) + len(self.pending_multi_frames)
        if self.on_debug_record is None:
            self.pending_multi_frames.clear()
            return
        for (direction, msg_id, base_seqid), state in list(self.pending_multi_frames.items()):
            parts = state.get("parts", {})
            if not isinstance(parts, dict):
                continue
            self.on_debug_record(
                {
                    "type": "debug_multipack_incomplete",
                    "session_id": self.session_id,
                    "timestamp_ms": int(state.get("last_timestamp_ms", state.get("first_timestamp_ms", 0)) or 0),
                    "direction": direction,
                    "msg_id": msg_id,
                    "base_seqid": base_seqid,
                    "total_pack_count": int(state.get("total_pack_count", 0) or 0),
                    "received_indexes": sorted(int(index) for index in parts.keys()),
                    "first_timestamp_ms": int(state.get("first_timestamp_ms", 0) or 0),
                    "last_timestamp_ms": int(state.get("last_timestamp_ms", 0) or 0),
                    "head": state.get("head", {}),
                }
            )
        self.pending_multi_frames.clear()

    def _handle_message(self, decoded: DecodedMessage, timestamp_ms: int) -> None:
        self.decoded_class_counts[decoded.class_name] += 1
        if self.on_debug_message is not None:
            self.on_debug_message(decoded, timestamp_ms)
        if decoded.class_name in {
            "CS_PING", "SC_PING", "CS_GAME_TIME_FREEZE_START_REQ", "CS_GAME_TIME_FREEZE_END_REQ",
            "SC_GAME_TIME_FREEZE_START_RSP", "SC_GAME_TIME_FREEZE_END_RSP",
        }:
            # Preserve proto3 false/zero defaults: an explicit response with
            # in_time_freeze=false must survive as a state transition.
            payload = self._message_to_payload(decoded.message, include_implicit_defaults=True)
            self._emit_event(BattleLogEvent(
                session_id=self.session_id, timestamp_ms=timestamp_ms,
                event_type=decoded.class_name,
                payload={**payload, "direction": decoded.direction, "msg_id": decoded.msg_id,
                         "raw_body_hex": decoded.raw_body.hex()},
            ))
            return
        if decoded.class_name == "CS_ENTER_DUNGEON":
            self._emit_proto_passthrough_event(
                decoded.class_name,
                decoded.message,
                timestamp_ms,
                extra_payload={
                    "raw_body_len": len(decoded.raw_body),
                    "raw_body_hex": decoded.raw_body.hex(),
                },
            )
            self._handle_enter_dungeon(decoded.message, timestamp_ms)
            return
        if decoded.class_name in _CONTRACT_TAG_CLASS_NAMES:
            self._emit_contract_tags_event(decoded, timestamp_ms)
            return
        if decoded.class_name == "CS_LEAVE_DUNGEON":
            self._handle_leave_dungeon(timestamp_ms)
            return
        if decoded.class_name == "SC_SYNC_CHAR_BAG_INFO":
            self._emit_proto_passthrough_event(decoded.class_name, decoded.message, timestamp_ms)
            self._update_char_bag_info(decoded.message, timestamp_ms)
            self._emit_effect_snapshot(decoded.class_name, decoded.message, timestamp_ms)
            return
        if decoded.class_name == "SC_CHAR_BAG_SET_TEAM":
            self._emit_proto_passthrough_event(decoded.class_name, decoded.message, timestamp_ms)
            self._update_char_team(decoded.message, timestamp_ms)
            return
        if decoded.class_name in {
            "SC_CHAR_BAG_SET_CURR_TEAM_INDEX", "SC_CHAR_BAG_SET_CURR_TEAM_TYPE",
            "SC_CHAR_BAG_SET_MAX_TEAM_MEMBER_COUNT", "SC_CHAR_SKILL_INFOS",
            "SC_EQUIP_MEDICINE_MODIFY", "SC_EQUIP_PRODUCE",
        }:
            self._emit_proto_passthrough_event(decoded.class_name, decoded.message, timestamp_ms)
            if decoded.class_name.startswith("SC_CHAR_BAG_"):
                self._update_char_team(decoded.message, timestamp_ms, event_type=decoded.class_name)
            elif decoded.class_name == "SC_CHAR_SKILL_INFOS":
                char_id = int(decoded.message.char_obj_id)
                char_info = self.char_bag_by_objid.get(char_id)
                levels = [self._message_to_payload(row, include_implicit_defaults=True)
                          for row in decoded.message.level_infos]
                if char_info is not None:
                    char_info["skill_level_infos"] = levels
                # Character skill_level/enhanced_level are reported fields.
                # Their equivalence to SERVER_SKILL.level is not established,
                # so the existing combat calculation cache is left intact.
                if char_info is not None:
                    self._emit_loadout_event(decoded.class_name, timestamp_ms)
            elif decoded.class_name == "SC_EQUIP_MEDICINE_MODIFY":
                char_info = self.char_bag_by_objid.get(int(decoded.message.char_id))
                if char_info is not None:
                    char_info["equip_medicine_id"] = int(decoded.message.equip_medicine_id)
                    self._emit_loadout_event(decoded.class_name, timestamp_ms)
            # EQUIP_PRODUCE identifies a formula and instance, not the new
            # item's complete attributes or its equipped character.
            return
        if decoded.class_name == "SC_CHAR_BAG_SET_TEAM_LEADER":
            self._emit_proto_passthrough_event(decoded.class_name, decoded.message, timestamp_ms)
            self._emit_loadout_event("SC_CHAR_BAG_SET_TEAM_LEADER", timestamp_ms)
            return
        if decoded.class_name == "SC_ITEM_BAG_SCOPE_SYNC":
            self._update_item_bag_scope_sync(decoded.message, timestamp_ms)
            return
        if decoded.class_name == "SC_ITEM_BAG_SCOPE_MODIFY":
            self._update_item_bag_scope_modify(decoded.message, timestamp_ms)
            return
        if decoded.class_name == "SC_EQUIP_PUTON":
            self._update_equip_puton(decoded.message, timestamp_ms)
            return
        if decoded.class_name == "SC_EQUIP_PUTOFF":
            self._update_equip_putoff(decoded.message, timestamp_ms)
            return
        if decoded.class_name == "SC_EQUIP_ENHANCE":
            self._update_equip_enhance(decoded.message, timestamp_ms)
            return
        if decoded.class_name == "SC_WEAPON_PUTON":
            self._update_weapon_puton(decoded.message, timestamp_ms)
            return
        if decoded.class_name in {
            "SC_WEAPON_ADD_EXP",
            "SC_WEAPON_BREAKTHROUGH",
            "SC_WEAPON_REFINE_UPGRADE",
            "SC_WEAPON_ATTACH_GEM",
            "SC_WEAPON_DETACH_GEM",
        }:
            self._update_weapon_delta(decoded.class_name, decoded.message, timestamp_ms)
            return
        if decoded.class_name == "SC_SELF_SCENE_INFO":
            self._update_squad_index(decoded.message, timestamp_ms)
            self._emit_effect_snapshot(decoded.class_name, decoded.message, timestamp_ms)
            return
        if decoded.class_name == "SC_OBJECT_ENTER_VIEW":
            self._update_entity_index(decoded.message, timestamp_ms)
            self._emit_effect_snapshot(decoded.class_name, decoded.message, timestamp_ms)
            return
        if decoded.class_name == "SC_SYNC_ATTR":
            self._sync_entity_attrs(decoded.message, timestamp_ms)
            return
        if decoded.class_name == "SC_OBJECT_LEAVE_VIEW":
            self._remove_entities(decoded.message, timestamp_ms)
            return
        if decoded.class_name in {"SC_ATTACH_SERVER_SKILL", "SC_UPDATE_SERVER_SKILL"}:
            self._update_server_skill_state(decoded.class_name, decoded.message, timestamp_ms)
            return
        if decoded.class_name == "SC_DETACH_SERVER_SKILL":
            self._detach_server_skill_state(decoded.message, timestamp_ms)
            return
        if decoded.class_name == "CS_BATTLE_OP":
            self._emit_battle_events(decoded.message, timestamp_ms)
            return
        if decoded.class_name in {
            "CS_DEV_CLEAR_BATTLE_INFO",
            "CS_CONDITIONAL_MULTI_STAGE_ACTIVITY_GAIN_REWARD",
            "CS_GAME_MECHANICS_NTF_INST_PREPARE_FINISH",
            "CS_GAME_MECHANICS_REQ_START",
            "CS_GAME_MECHANICS_REQ_STOP",
            "CS_SCENE_LOAD_FINISH",
            "CS_SCENE_SET_BATTLE",
            "SC_ACTIVITY_CONDITIONAL_MULTI_STAGE_BASE_CHANGE",
            "SC_ACTIVITY_CONDITIONAL_STAGE_PROGRESS_CHANGE",
            "SC_ACTIVITY_PROGRESS_CHANGE",
            "SC_ADD_SERVER_BUFF",
            "SC_REMOVE_SERVER_BUFF",
            "SC_BATTLE_ADD_GLOBAL_BUFF",
            "SC_BATTLE_DEBUG_INFO",
            "SC_BATTLE_GENERATION_CHANGE",
            "SC_BATTLE_REMOVE_GLOBAL_BUFF",
            "SC_BATTLE_SYNC_GLOBAL_BUFF_INFO",
            "SC_BATTLE_SYNC_SEQ_ID",
            "SC_CHAR_BAG_SYNC_PERSISTENT_BUFFS",
            "SC_ENTER_DUNGEON",
            "SC_GAME_MECHANICS_MODIFY_INST_TIME_FREEZE",
            "SC_GAME_MECHANICS_SYNC_CHALLENGE_COMPLETE",
            "SC_GAME_MECHANICS_SYNC_CHALLENGE_START",
            "SC_GAME_MECHANICS_SYNC_COMPLETION_REWARD",
            "SC_GAME_MECHANICS_SYNC_ENTER_GAME_INST",
            "SC_GAME_MECHANICS_SYNC_LEAVE_GAME_INST",
            "SC_GAME_MECHANICS_SYNC_RESTART_GAME_INST",
            "SC_CONDITIONAL_MULTI_STAGE_ACTIVITY_GAIN_REWARD",
            "SC_RESET_BATTLE_STATUS",
            "SC_SCENE_RESET_ENTITY",
            "SC_SCENE_SET_BATTLE",
            "SC_SYNC_RACING_DUNGEON_BUFF_MODIFY",
            "SC_SYNC_RACING_DUNGEON_RECONNECT",
            "SC_SYNC_FULL_DUNGEON_STATUS",
            "SC_SYNC_WEEK_RAID_SETTLEMENT",
        }:
            self._emit_proto_passthrough_event(decoded.class_name, decoded.message, timestamp_ms)
            if decoded.class_name in {"SC_RESET_BATTLE_STATUS", "SC_SCENE_RESET_ENTITY"}:
                self._emit_effect_snapshot(decoded.class_name, decoded.message, timestamp_ms)

    def _emit_effect_snapshot(self, source: str, message: Any, timestamp_ms: int) -> None:
        """Preserve server Buff snapshots without interpreting timing or layers.

        A server Buff UID belongs to this owner/generation namespace.  It must
        not be joined to an equal client-operation UID merely by its number.
        Empty owner lists are observations, not a claim of complete coverage.
        """
        owners: list[dict[str, object]] = []
        detail = getattr(message, "detail", None)
        if detail is not None:
            rows = [(int(getattr(common, "id", 0) or 0) or None, battle,
                     str(getattr(common, "templateid", "") or "") or None,
                     int(getattr(common, "type", 0)), kind.removesuffix("_list"))
                    for kind, _, common, battle in self._iter_detail_objects(detail)]
        elif source == "SC_SYNC_CHAR_BAG_INFO":
            rows = [(int(getattr(char, "objid", 0) or 0) or None, getattr(char, "battle_mgr_info", None),
                     str(getattr(char, "templateid", "") or "") or None, None, "char")
                    for char in getattr(message, "char_info", [])]
        else:
            rows = [(None, getattr(message, "battle_info", None), None, None, None)]
        for obj_id, battle_info, templateid, entity_type, kind in rows:
            if battle_info is None:
                continue
            battle_inst_id = int(getattr(battle_info, "battle_inst_id", 0) or 0)
            if not battle_inst_id and source == "SC_RESET_BATTLE_STATUS":
                battle_inst_id = int(getattr(message, "battle_inst_id", 0) or 0)
            if (not battle_inst_id and not getattr(battle_info, "buff_list", [])
                    and not getattr(battle_info, "stacking_group_list", [])):
                continue
            entity = self.entity_index.get(battle_inst_id)
            if obj_id is None:
                obj_id = entity.obj_id if entity is not None else None
            if templateid is None and entity is not None:
                templateid = entity.templateid
            if entity_type is None and entity is not None:
                entity_type = entity.entity_type
            owners.append({
                "owner_obj_id": str(obj_id) if obj_id is not None else None,
                "owner_battle_inst_id": str(battle_inst_id) if battle_inst_id else None,
                "owner_templateid": templateid,
                "owner_display_name": self.name_index.get(templateid) if templateid else None,
                "owner_entity_type": entity_type,
                "owner_kind": kind,
                "generation": int(getattr(battle_info, "msg_generation", 0)),
                "identity_scope": "server_buff",
                "buffs": [self._message_to_payload(buff, include_implicit_defaults=True)
                          for buff in getattr(battle_info, "buff_list", [])],
                "stacking_groups": [self._message_to_payload(group, include_implicit_defaults=True)
                                    for group in getattr(battle_info, "stacking_group_list", [])],
            })
        self._emit_event(BattleLogEvent(
            session_id=self.session_id, timestamp_ms=timestamp_ms,
            event_type="SC_EFFECT_SNAPSHOT",
            payload={"source": source, "owners": owners,
                     "serialization": "protobuf_json_implicit_defaults_v1"},
        ))

    def _iter_detail_objects(self, detail: Any):
        for field_desc, value in detail.ListFields():
            # protobuf 7 removed the deprecated ``label`` attribute from the
            # upb FieldDescriptor implementation. ``is_repeated`` is the
            # supported API in both protobuf 6 and 7.
            if not bool(field_desc.is_repeated):
                continue
            for item in value:
                common_info = getattr(item, "common_info", None)
                battle_info = getattr(item, "battle_info", None)
                if common_info is None:
                    continue
                yield field_desc.name, item, common_info, battle_info

    def _update_entity_index(self, message: Any, timestamp_ms: int | None = None) -> None:
        detail = getattr(message, "detail", None)
        if detail is None:
            return
        objects: list[dict[str, object | None]] = []
        for detail_kind, detail_obj, common_info, battle_info in self._iter_detail_objects(detail):
            battle_inst_id = int(getattr(battle_info, "battle_inst_id", 0) or 0) if battle_info is not None else 0
            if not battle_inst_id:
                continue
            obj_id = int(getattr(common_info, "id", 0)) or None
            templateid = str(getattr(common_info, "templateid", "")) or None
            info = EntityInfo(
                battle_inst_id=battle_inst_id,
                obj_id=obj_id,
                templateid=templateid,
                entity_type=int(getattr(common_info, "type", 0)) or None,
            )
            self.entity_index[info.battle_inst_id] = info
            if info.obj_id is not None:
                self.obj_to_battle[info.obj_id] = info.battle_inst_id
            objects.append(
                {
                    "kind": str(detail_kind).removesuffix("_list") or "object",
                    "obj_id": obj_id,
                    "battle_inst_id": battle_inst_id,
                    "templateid": templateid,
                    "entity_type": info.entity_type,
                    "level": int(getattr(detail_obj, "level", 0) or 0) or None,
                    "hp": float(getattr(common_info, "hp", 0.0) or 0.0) if common_info is not None else None,
                    "attrs": _attrs_payload(detail_obj),
                }
            )
            if (
                self.merge_multi_phase_enemy_battles
                and self.tracked_enemy_templateid is not None
                and info.templateid == self.tracked_enemy_templateid
            ):
                self.tracked_enemy_inst_ids.add(info.battle_inst_id)
            self._cache_scene_skill_levels(battle_inst_id, battle_info)
        if objects:
            self._emit_event(
                BattleLogEvent(
                    session_id=self.session_id,
                    timestamp_ms=timestamp_ms or int(datetime.now().timestamp() * 1000),
                    event_type="SC_OBJECT_ENTER_VIEW",
                    payload={"objects": objects},
                )
            )

    def _sync_entity_attrs(self, message: Any, timestamp_ms: int) -> None:
        obj_id = int(getattr(message, "obj_id", 0) or 0)
        attrs = _attrs_payload(message)
        if not obj_id or not attrs:
            return
        battle_inst_id = self.obj_to_battle.get(obj_id)
        if battle_inst_id is None and obj_id in self.entity_index:
            battle_inst_id = obj_id
        entity_info = self.entity_index.get(battle_inst_id) if battle_inst_id is not None else None
        member = self.squad_index.get(battle_inst_id) if battle_inst_id is not None else None
        templateid = entity_info.templateid if entity_info is not None else None
        if templateid is None and member is not None:
            templateid = member.templateid
        if member is not None:
            merged = {int(item.get("attr_type", 0) or 0): dict(item) for item in member.attrs}
            for item in attrs:
                attr_type = int(item.get("attr_type", 0) or 0)
                if attr_type:
                    merged[attr_type] = dict(item)
            member.attrs = [merged[key] for key in sorted(merged)]
        self._emit_event(
            BattleLogEvent(
                session_id=self.session_id,
                timestamp_ms=timestamp_ms,
                event_type="SC_SYNC_ATTR",
                payload={
                    "obj_id": obj_id,
                    "battle_inst_id": battle_inst_id,
                    "templateid": templateid,
                    "attrs": attrs,
                },
            )
        )

    def _handle_enter_dungeon(self, message: Any, timestamp_ms: int) -> None:
        self.scene_team_char_ids = []
        char_team = list(getattr(message, "char_team", []) or [])
        explicit_team_ids = [
            obj_id
            for item in char_team
            if (obj_id := int(getattr(item, "obj_id", 0) or 0))
        ]
        self.dungeon_team_char_ids = explicit_team_ids or None
        dungeon_id = str(getattr(message, "dungeon_id", "") or "").strip()
        if dungeon_id:
            self._emit_dungeon_context(
                timestamp_ms,
                source="CS_ENTER_DUNGEON",
                dungeon_id=dungeon_id,
                char_team_count=len(char_team),
            )
        if not self.merge_multi_phase_enemy_battles:
            return
        templateid = self.multi_phase_dungeon_map.get(dungeon_id)
        if not templateid:
            self.tracked_dungeon_id = None
            self.tracked_enemy_templateid = None
            self.tracked_enemy_inst_ids.clear()
            self.merged_battle_active = False
            return
        self.tracked_dungeon_id = dungeon_id
        self.tracked_enemy_templateid = templateid
        self.tracked_enemy_inst_ids.clear()
        for info in self.entity_index.values():
            if info.templateid == templateid:
                self.tracked_enemy_inst_ids.add(info.battle_inst_id)

    def _scene_dungeon_context_payload(self, message: Any) -> dict[str, object | None] | None:
        if not _proto_has_field(message, "dungeon"):
            return None
        dungeon = getattr(message, "dungeon", None)
        if dungeon is None:
            return None
        dungeon_id = str(getattr(dungeon, "dungeon_id", "") or "").strip()
        if not dungeon_id:
            return None
        scene_id = str(getattr(message, "scene_id", "") or "")
        return {
            "dungeon_id": dungeon_id,
            "source": "SC_SELF_SCENE_INFO",
            "scene_num_id": _optional_int(getattr(message, "scene_num_id", None)),
            "scene_id": scene_id or None,
            "challenge_expire_ts": _optional_int(getattr(dungeon, "challenge_expire_ts", None)),
            "leave_dungeon_ts": _optional_int(getattr(dungeon, "leave_dungeon_ts", None)),
            "is_reward": bool(getattr(dungeon, "is_reward", False)),
            "is_calc": bool(getattr(dungeon, "is_calc", False)),
            "is_pass": bool(getattr(dungeon, "is_pass", False)),
        }

    def _emit_dungeon_context(
        self,
        timestamp_ms: int,
        *,
        source: str,
        dungeon_id: str,
        **extra: object | None,
    ) -> None:
        payload: dict[str, object | None] = {
            "source": source,
            "dungeon_id": dungeon_id,
            **extra,
        }
        self._emit_event(
            BattleLogEvent(
                session_id=self.session_id,
                timestamp_ms=timestamp_ms,
                event_type="DUNGEON_CONTEXT",
                payload=payload,
            )
        )

    def _handle_leave_dungeon(self, timestamp_ms: int) -> None:
        self.scene_team_char_ids = []
        self.dungeon_team_char_ids = None
        if self.merge_multi_phase_enemy_battles and self.merged_battle_active:
            self._emit_event(
                BattleLogEvent(
                    session_id=self.session_id,
                    timestamp_ms=timestamp_ms,
                    event_type="BattleOpModifyBattleState",
                    payload={
                        "seq_id": None,
                        "client_tick_tms": None,
                        "is_in_battle": False,
                    },
                )
            )
        self.tracked_dungeon_id = None
        self.tracked_enemy_templateid = None
        self.tracked_enemy_inst_ids.clear()
        self.merged_battle_active = False

    def _update_squad_index(self, message: Any, timestamp_ms: int) -> None:
        dungeon_context = self._scene_dungeon_context_payload(message)
        if dungeon_context is not None:
            self._emit_dungeon_context(timestamp_ms, **dungeon_context)
        detail = getattr(message, "detail", None)
        team_info = getattr(message, "team_info", None)
        if detail is None:
            return
        char_list = list(getattr(detail, "char_list", []))
        if not char_list:
            LOGGER.info(
                "ignoring SC_SELF_SCENE_INFO without char_list self_info_reason=%s",
                getattr(message, "self_info_reason", None),
            )
            return
        current_leader_id = int(getattr(team_info, "cur_leader_id", 0)) if team_info is not None else 0
        previous_squad_ids = set(self.squad_index)
        self.squad_index.clear()
        self.skill_levels_by_battle_inst.clear()
        self.global_skill_levels.clear()
        scene_team_char_ids: list[int] = []
        for squad_index, char_info in enumerate(char_list):
            common_info = getattr(char_info, "common_info", None)
            battle_info = getattr(char_info, "battle_info", None)
            if common_info is None or battle_info is None:
                continue
            battle_inst_id = int(getattr(battle_info, "battle_inst_id", 0))
            if not battle_inst_id:
                continue
            obj_id = int(getattr(common_info, "id", 0)) or None
            templateid = self._resolve_scene_templateid(common_info, battle_info)
            display_name = self.name_index.get(templateid or "", templateid)
            member = SquadMember(
                battle_inst_id=battle_inst_id,
                obj_id=obj_id,
                templateid=templateid,
                display_name=display_name,
                is_leader=obj_id == current_leader_id,
                squad_index=squad_index,
                level=int(getattr(char_info, "level", 0) or 0) or None,
                hp=float(getattr(common_info, "hp", 0.0) or 0.0) or None,
                attrs=_attrs_payload(char_info),
            )
            self.squad_index[battle_inst_id] = member
            self.entity_index[battle_inst_id] = EntityInfo(
                battle_inst_id=battle_inst_id,
                obj_id=obj_id,
                templateid=templateid,
                entity_type=int(getattr(common_info, "type", 0)) or None,
            )
            if obj_id is not None:
                self.obj_to_battle[obj_id] = battle_inst_id
                scene_team_char_ids.append(obj_id)
            self._cache_scene_skill_levels(battle_inst_id, battle_info)
            self._replace_server_skill_rows(
                battle_inst_id,
                [self._server_skill_row(battle_inst_id, skill) for skill in getattr(battle_info, "skill_list", [])],
            )
        for owner_inst_id in previous_squad_ids - set(self.squad_index):
            detached = self.server_skill_state_by_owner_inst.pop(owner_inst_id, {})
            for skill_inst_id in detached:
                self.server_skill_owner_by_skill_inst.pop(skill_inst_id, None)
        self.scene_team_char_ids = scene_team_char_ids
        self._emit_scene_info_event(timestamp_ms, dungeon_context=dungeon_context, message=message)
        for member in self.squad_index.values():
            owner_skills = list(self.server_skill_state_by_owner_inst.get(member.battle_inst_id, {}).values())
            if not owner_skills:
                continue
            self._emit_server_skill_rows_event(
                event_type="SC_SELF_SCENE_INFO_SKILLS",
                owner_inst_id=member.battle_inst_id,
                generation=None,
                skill_rows=owner_skills,
                timestamp_ms=timestamp_ms,
            )
        self._emit_loadout_event("SC_SELF_SCENE_INFO", timestamp_ms)

    def _update_char_bag_info(self, message: Any, timestamp_ms: int | None = None) -> None:
        for char_info in getattr(message, "char_info", []):
            templateid = str(getattr(char_info, "templateid", "")) or None
            objid = int(getattr(char_info, "objid", 0) or 0)
            if not templateid:
                continue
            level = int(getattr(char_info, "potential_level", 0) or 0)
            self.char_potential_levels[templateid] = level
            equip_col = {int(key): int(value) for key, value in dict(getattr(char_info, "equip_col", {})).items()}
            equip_suit = {str(key): int(value) for key, value in dict(getattr(char_info, "equip_suit", {})).items()}
            self.char_bag_by_objid[objid] = {
                "objid": objid,
                "templateid": templateid,
                "level": int(getattr(char_info, "level", 0) or 0),
                "potential": level,
                "weapon_id": int(getattr(char_info, "weapon_id", 0) or 0),
                "equip_medicine_id": int(getattr(char_info, "equip_medicine_id", 0)),
                "equip_col": equip_col,
                "equipment_snapshot_complete": hasattr(char_info, 'equip_col'),
                "equip_suit": equip_suit,
                "skill_int_ids": self._battle_skill_int_ids(getattr(char_info, "battle_mgr_info", None)),
                "weapon_source_skills": self._weapon_source_skills(getattr(char_info, "battle_mgr_info", None)),
            }
        LOGGER.info('检测到潜能：%s',self.char_potential_levels)
        self._update_char_team(message, timestamp_ms, event_type="SC_SYNC_CHAR_BAG_INFO")

    def _update_char_team(self, message: Any, timestamp_ms: int | None, *,
                          event_type: str = "SC_CHAR_BAG_SET_TEAM") -> None:
        # A current index/type is only a selector. Resolve it against a team
        # actually received in the same scope, never against roster order or
        # another scope's cached party.
        scopes = getattr(self, "char_team_state_by_scope", None)
        if scopes is None:
            scopes = self.char_team_state_by_scope = {}
        scope = int(getattr(message, "scope_name", 0))
        active_scope = getattr(self, "current_team_scope_name", None)
        if event_type == "SC_SYNC_CHAR_BAG_INFO" and bool(getattr(message, "is_scope_delete", False)):
            scopes.pop(scope, None)
            if active_scope == scope:
                self.current_team_char_ids = []
                self.current_team_selection_known = False
            return
        state = scopes.setdefault(scope, {"teams": {}, "temp_team": None,
                                          "curr_team_index": None, "team_type": None})
        if event_type == "SC_SYNC_CHAR_BAG_INFO":
            state["teams"] = {index: self._message_to_payload(team, include_implicit_defaults=True)
                              for index, team in enumerate(getattr(message, "team_info", []))}
            state["temp_team"] = (self._message_to_payload(message.temp_team_info, include_implicit_defaults=True)
                                  if message.HasField("temp_team_info") else None)
            state["curr_team_index"] = int(message.curr_team_index)
            state["team_type"] = int(message.team_type)
            state["max_char_team_member_count"] = int(message.max_char_team_member_count)
            self.current_team_scope_name = active_scope = scope
        elif event_type == "SC_CHAR_BAG_SET_TEAM":
            team_type, index = int(message.team_type), int(message.team_index)
            team = {"char_team": [str(value) for value in message.char_team],
                    "leaderid": str(message.leader_id)}
            if team_type == 0:
                state["teams"][index] = team
            elif team_type == 1:
                state["temp_team"] = team
        elif event_type == "SC_CHAR_BAG_SET_CURR_TEAM_INDEX":
            state["curr_team_index"] = int(message.curr_team_index)
        elif event_type == "SC_CHAR_BAG_SET_CURR_TEAM_TYPE":
            state["team_type"] = int(message.team_type)
        elif event_type == "SC_CHAR_BAG_SET_MAX_TEAM_MEMBER_COUNT":
            state["max_char_team_member_count"] = int(message.max_char_team_member_count)
        if active_scope == scope:
            selected = (state["teams"].get(state["curr_team_index"]) if state["team_type"] == 0 else
                        state["temp_team"] if state["team_type"] == 1 else None)
            self.current_team_selection_known = selected is not None
            self.current_team_char_ids = ([int(value) for value in selected["char_team"] if int(value)]
                                          if selected is not None else [])
            if timestamp_ms is not None:
                self._emit_loadout_event(event_type, timestamp_ms)

    @staticmethod
    def _int_map(message_map: Any) -> dict[int, int]:
        result: dict[int, int] = {}
        try:
            items = dict(message_map).items()
        except Exception:
            return result
        for key, value in items:
            try:
                result[int(key)] = int(value)
            except (TypeError, ValueError):
                continue
        return result

    @staticmethod
    def _str_int_map(message_map: Any) -> dict[str, int]:
        result: dict[str, int] = {}
        try:
            items = dict(message_map).items()
        except Exception:
            return result
        for key, value in items:
            try:
                result[str(key)] = int(value)
            except (TypeError, ValueError):
                continue
        return result

    @staticmethod
    def _equip_template(equip: dict[str, object] | None, equip_id: int | None = None) -> str:
        if not equip:
            return str(equip_id or "")
        return str(equip.get("template_string") or equip.get("templateid") or equip_id or "")

    @staticmethod
    def _equip_suit_id(template: str) -> str:
        meta = equip_piece_catalog().get(str(template or ""), {})
        suit_id = str(meta.get("suit_id") or "")
        if suit_id:
            return suit_id
        match = re.search(r"item_equip_t\d+_(suit_[a-z0-9]+)_", str(template or ""), re.IGNORECASE)
        return match.group(1) if match else ""

    @classmethod
    def _equip_suit_counts(cls, equip_col: dict[int, int], equip_by_inst_id: dict[int, dict[str, object]]) -> Counter[str]:
        counts: Counter[str] = Counter()
        for equip_id in set(int(value or 0) for value in equip_col.values()):
            if not equip_id:
                continue
            suit_id = cls._equip_suit_id(cls._equip_template(equip_by_inst_id.get(equip_id), equip_id))
            if suit_id:
                counts[suit_id] += 1
        return counts

    @classmethod
    def _equip_payload(cls, equip: Any, template_string: str | None = None) -> dict[str, object]:
        equip_id = int(getattr(equip, "equipid", 0) or 0)
        return {
            "equipid": equip_id,
            "templateid": int(getattr(equip, "templateid", 0) or 0),
            "template_string": template_string or "",
            "equip_char_id": int(getattr(equip, "equip_char_id", 0) or 0),
            "enhance": cls._int_map(getattr(equip, "enhance", {})),
            "legacy_enhance": cls._int_map(getattr(equip, "legacy_enhance", {})),
        }

    @staticmethod
    def _weapon_payload(weapon: Any, template_string: str | None = None) -> dict[str, object]:
        inst_id = int(getattr(weapon, "inst_id", 0) or 0)
        return {
            "inst_id": inst_id,
            "template_id": int(getattr(weapon, "template_id", 0) or 0),
            "template_string": template_string or "",
            "exp": int(getattr(weapon, "exp", 0) or 0),
            "weapon_lv": int(getattr(weapon, "weapon_lv", 0) or 0),
            "refine_lv": int(getattr(weapon, "refine_lv", 0) or 0),
            "breakthrough_lv": int(getattr(weapon, "breakthrough_lv", 0) or 0),
            "equip_char_id": int(getattr(weapon, "equip_char_id", 0) or 0),
            "attach_gem_id": int(getattr(weapon, "attach_gem_id", 0) or 0),
        }

    @staticmethod
    def _gem_payload(gem: Any, inst_id: int, template_string: str | None = None) -> dict[str, object]:
        payload = normalize_gem_payload(gem, inst_id, template_string or "")
        payload["payload"] = MessageToDict(gem, preserving_proto_field_name=True, use_integers_for_enums=False)
        return payload

    def _index_gem_payload(self, payload: dict[str, object]) -> None:
        inst_id = int(payload.get("inst_id") or 0)
        gem_id = int(payload.get("gem_id") or 0)
        weapon_id = int(payload.get("weapon_id") or 0)
        previous = self.gem_by_inst_id.get(gem_id or inst_id)
        if previous and (previous.get("terms") != payload.get("terms")
                         or int(previous.get("weapon_id") or 0) != weapon_id):
            affected = {weapon_id, int(previous.get("weapon_id") or 0)} - {0}
            for char_info in self.char_bag_by_objid.values():
                if int(char_info.get("weapon_id") or 0) in affected:
                    self._replace_weapon_source_skills(char_info, [])
        if inst_id:
            self.gem_by_inst_id[inst_id] = payload
        if gem_id:
            self.gem_by_inst_id[gem_id] = payload
        if weapon_id:
            self.gem_by_weapon_id[weapon_id] = payload
            weapon = self.weapon_by_inst_id.setdefault(weapon_id, {"inst_id": weapon_id})
            if not int(weapon.get("attach_gem_id") or 0):
                weapon["attach_gem_id"] = gem_id or inst_id

    def _index_item_grid(self, grid: Any) -> None:
        template_string = str(getattr(grid, "id", "") or "")
        inst = getattr(grid, "inst", None)
        if inst is None:
            return
        inst_id = int(getattr(inst, "inst_id", 0) or 0)
        try:
            kind = inst.WhichOneof("inst_impl")
        except Exception:
            kind = None
        if kind == "equip":
            equip = getattr(inst, "equip", None)
            if equip is not None:
                payload = self._equip_payload(equip, template_string)
                equip_id = int(payload.get("equipid") or inst_id or 0)
                if equip_id:
                    payload["equipid"] = equip_id
                    self.equip_by_inst_id[equip_id] = payload
            return
        if kind == "weapon":
            weapon = getattr(inst, "weapon", None)
            if weapon is not None:
                payload = self._weapon_payload(weapon, template_string)
                weapon_id = int(payload.get("inst_id") or inst_id or 0)
                if weapon_id:
                    payload["inst_id"] = weapon_id
                    payload["sync_source"] = "WEAPON_DATA"
                    self.weapon_by_inst_id[weapon_id] = payload
            return
        if kind == "gem" and inst_id:
            gem = getattr(inst, "gem", None)
            if gem is not None:
                self._index_gem_payload(self._gem_payload(gem, inst_id, template_string))
            return
        if inst_id and template_string.startswith("wpn_"):
            weapon = self.weapon_by_inst_id.setdefault(inst_id, {"inst_id": inst_id})
            weapon.setdefault("template_id", 0)
            weapon["template_string"] = template_string
            weapon.setdefault("sync_source", "template_only")
            weapon.setdefault("exp", 0)
            weapon.setdefault("weapon_lv", 0)
            weapon.setdefault("refine_lv", 0)
            weapon.setdefault("breakthrough_lv", 0)
            weapon.setdefault("equip_char_id", 0)
            weapon.setdefault("attach_gem_id", 0)

    def _index_item_bag(self, bag: Any) -> None:
        if bag is None:
            return
        for grid in getattr(bag, "grids", []):
            self._index_item_grid(grid)

    def _index_item_depot(self, depot: Any) -> None:
        if depot is None:
            return
        for grid in getattr(depot, "inst_list", []):
            self._index_item_grid(grid)

    def _update_item_bag_scope_sync(self, message: Any, timestamp_ms: int) -> None:
        self._index_item_bag(getattr(message, "bag", None))
        for depot in dict(getattr(message, "depot", {})).values():
            self._index_item_depot(depot)
        for depot in dict(getattr(message, "factory_depot", {})).values():
            self._index_item_depot(depot)
        self._emit_loadout_event("SC_ITEM_BAG_SCOPE_SYNC", timestamp_ms)

    def _update_item_bag_scope_modify(self, message: Any, timestamp_ms: int) -> None:
        self._index_item_bag(getattr(message, "bag", None))
        for depot in dict(getattr(message, "depot", {})).values():
            self._index_item_depot(depot)
        for depot in dict(getattr(message, "factory_depot", {})).values():
            self._index_item_depot(depot)
        self._emit_loadout_event("SC_ITEM_BAG_SCOPE_MODIFY", timestamp_ms)

    def _update_equip_puton(self, message: Any, timestamp_ms: int) -> None:
        char_id = int(getattr(message, "charid", 0) or 0)
        slot_id = int(getattr(message, "slotid", 0) or 0)
        equip_id = int(getattr(message, "equipid", 0) or 0)
        if char_id and char_id in self.char_bag_by_objid:
            char_info = self.char_bag_by_objid[char_id]
            equip_col = dict(char_info.get("equip_col") or {})
            equip_suit = self._str_int_map(getattr(message, "suitinfo", {}))
            previous_equip_id = int(equip_col.get(slot_id, 0) or 0)
            if previous_equip_id and previous_equip_id != equip_id and previous_equip_id in self.equip_by_inst_id:
                self.equip_by_inst_id[previous_equip_id]["equip_char_id"] = 0
            # The explicit new slot replaces this instance's old slot even
            # when the equipment moves within the same character.
            equip_col = {slot: value for slot, value in equip_col.items()
                         if int(value or 0) != equip_id or slot == slot_id}
            equip_col[slot_id] = equip_id
            char_info["equip_col"] = equip_col
            char_info["equip_suit"] = equip_suit
        for other_char_id, other_char_info in self.char_bag_by_objid.items():
            if other_char_id == char_id:
                continue
            other_equip_col = dict(other_char_info.get("equip_col") or {})
            removed_slots = [
                slot
                for slot, current_equip_id in other_equip_col.items()
                if int(current_equip_id or 0) == equip_id
            ]
            if not removed_slots:
                continue
            for slot in removed_slots:
                other_equip_col.pop(slot, None)
            other_char_info["equip_col"] = other_equip_col
        put_off_char_id = int(getattr(message, "put_off_charid", 0) or 0)
        if put_off_char_id and put_off_char_id in self.char_bag_by_objid:
            old_owner = self.char_bag_by_objid[put_off_char_id]
            old_owner_equip_col = dict(old_owner.get("equip_col") or {})
            removed_slots = [
                slot
                for slot, current_equip_id in old_owner_equip_col.items()
                if int(current_equip_id or 0) == equip_id
            ]
            for slot in removed_slots:
                old_owner_equip_col.pop(slot, None)
            old_owner["equip_col"] = old_owner_equip_col
            old_owner["equip_suit"] = self._str_int_map(getattr(message, "old_owner_suitinfo", {}))
        if equip_id and equip_id in self.equip_by_inst_id:
            self.equip_by_inst_id[equip_id]["equip_char_id"] = char_id
        self._emit_loadout_event("SC_EQUIP_PUTON", timestamp_ms)

    def _update_equip_putoff(self, message: Any, timestamp_ms: int) -> None:
        char_id = int(getattr(message, "charid", 0) or 0)
        slot_id = int(getattr(message, "slotid", 0) or 0)
        if char_id and char_id in self.char_bag_by_objid:
            char_info = self.char_bag_by_objid[char_id]
            equip_col = dict(char_info.get("equip_col") or {})
            equip_id = int(equip_col.pop(slot_id, 0) or 0)
            char_info["equip_col"] = equip_col
            char_info["equip_suit"] = self._str_int_map(getattr(message, "suitinfo", {}))
            if equip_id and equip_id in self.equip_by_inst_id:
                self.equip_by_inst_id[equip_id]["equip_char_id"] = 0
        self._emit_loadout_event("SC_EQUIP_PUTOFF", timestamp_ms)

    def _update_equip_enhance(self, message: Any, timestamp_ms: int) -> None:
        equip = getattr(message, "equip_data", None)
        if equip is not None:
            payload = self._equip_payload(equip)
            equip_id = int(payload.get("equipid") or getattr(message, "equip_inst_id", 0) or 0)
            if equip_id:
                previous = self.equip_by_inst_id.get(equip_id, {})
                if previous.get("template_string") and not payload.get("template_string"):
                    payload["template_string"] = previous["template_string"]
                payload["equipid"] = equip_id
                self.equip_by_inst_id[equip_id] = payload
        self._emit_loadout_event("SC_EQUIP_ENHANCE", timestamp_ms)

    @staticmethod
    def _weapon_source_skill_ids(char_info: dict[str, object] | None) -> set[int]:
        if not isinstance(char_info, dict):
            return set()
        result: set[int] = set()
        for item in char_info.get("weapon_source_skills") or []:
            if not isinstance(item, dict):
                continue
            skill_int_id = int(item.get("skill_int_id") or 0)
            if skill_int_id:
                result.add(skill_int_id)
        return result

    @classmethod
    def _replace_weapon_source_skills(
        cls,
        char_info: dict[str, object],
        source_skills: object,
    ) -> None:
        previous_skill_ids = cls._weapon_source_skill_ids(char_info)
        copied_skills = copy.deepcopy(list(source_skills or []))
        destination_potential = int(char_info.get("potential") or 0)
        next_skill_ids: list[int] = []
        for item in copied_skills:
            if not isinstance(item, dict):
                continue
            # BATTLE_MGR_INFO reports the current character potential on every
            # weapon source skill.  The weapon/refine data moves with the
            # weapon, while this owner-specific field must follow the receiver.
            item["potential_lv"] = destination_potential
            skill_int_id = int(item.get("skill_int_id") or 0)
            if skill_int_id and skill_int_id not in next_skill_ids:
                next_skill_ids.append(skill_int_id)

        current_skill_ids = [
            int(skill_int_id)
            for skill_int_id in char_info.get("skill_int_ids") or []
            if int(skill_int_id) and int(skill_int_id) not in previous_skill_ids
        ]
        for skill_int_id in next_skill_ids:
            if skill_int_id not in current_skill_ids:
                current_skill_ids.append(skill_int_id)
        char_info["skill_int_ids"] = current_skill_ids
        char_info["weapon_source_skills"] = copied_skills

    def _update_weapon_puton(self, message: Any, timestamp_ms: int) -> None:
        char_id = int(getattr(message, "charid", 0) or 0)
        weapon_id = int(getattr(message, "weaponid", 0) or 0)
        off_weapon_id = int(getattr(message, "offweaponid", 0) or 0)
        put_off_char_id = int(getattr(message, "put_off_charid", 0) or 0)

        receiver = self.char_bag_by_objid.get(char_id) if char_id else None
        previous_owner = (
            self.char_bag_by_objid.get(put_off_char_id)
            if put_off_char_id and put_off_char_id != char_id
            else None
        )
        receiver_previous_weapon_id = int(receiver.get("weapon_id") or 0) if receiver else 0
        previous_owner_weapon_id = int(previous_owner.get("weapon_id") or 0) if previous_owner else 0
        receiver_source_skills = copy.deepcopy(receiver.get("weapon_source_skills") or []) if receiver else []
        previous_owner_source_skills = (
            copy.deepcopy(previous_owner.get("weapon_source_skills") or []) if previous_owner else []
        )

        if receiver is not None:
            receiver["weapon_id"] = weapon_id
            if weapon_id != receiver_previous_weapon_id:
                if previous_owner is not None and previous_owner_weapon_id == weapon_id:
                    self._replace_weapon_source_skills(receiver, previous_owner_source_skills)
                else:
                    # Equipping from the bag provides no new BATTLE_MGR_INFO.
                    # Never retain the previous weapon's skills under the new
                    # template; a later full character sync will repopulate it.
                    self._replace_weapon_source_skills(receiver, [])

        if previous_owner is not None:
            previous_owner["weapon_id"] = off_weapon_id
            if off_weapon_id and receiver_previous_weapon_id == off_weapon_id:
                self._replace_weapon_source_skills(previous_owner, receiver_source_skills)
            else:
                self._replace_weapon_source_skills(previous_owner, [])

        if weapon_id:
            weapon = self.weapon_by_inst_id.setdefault(weapon_id, {"inst_id": weapon_id})
            weapon["equip_char_id"] = char_id
        if off_weapon_id:
            off_weapon = self.weapon_by_inst_id.setdefault(off_weapon_id, {"inst_id": off_weapon_id})
            off_weapon["equip_char_id"] = put_off_char_id if previous_owner is not None else 0
        self._emit_loadout_event("SC_WEAPON_PUTON", timestamp_ms)

    def _update_weapon_delta(self, class_name: str, message: Any, timestamp_ms: int) -> None:
        weapon_id = int(getattr(message, "weaponid", 0) or 0)
        if weapon_id:
            weapon = self.weapon_by_inst_id.setdefault(weapon_id, {"inst_id": weapon_id})
            if class_name in {"SC_WEAPON_ATTACH_GEM", "SC_WEAPON_DETACH_GEM"}:
                # Source skill totals from before the gem change cannot be
                # combined with the new gem. Await an explicit skill refresh.
                for char_info in self.char_bag_by_objid.values():
                    if int(char_info.get("weapon_id") or 0) == weapon_id:
                        self._replace_weapon_source_skills(char_info, [])
            if class_name == "SC_WEAPON_ADD_EXP":
                weapon["exp"] = int(getattr(message, "new_exp", 0) or 0)
                weapon["weapon_lv"] = int(getattr(message, "weapon_lv", 0) or 0)
            elif class_name == "SC_WEAPON_BREAKTHROUGH":
                weapon["breakthrough_lv"] = int(getattr(message, "breakthrough_lv", 0) or 0)
            elif class_name == "SC_WEAPON_REFINE_UPGRADE":
                weapon["refine_lv"] = int(getattr(message, "refine_lv", 0) or 0)
            elif class_name == "SC_WEAPON_ATTACH_GEM":
                gem_id = int(getattr(message, "gemid", 0) or 0)
                weapon["attach_gem_id"] = gem_id
                gem = self.gem_by_inst_id.get(gem_id)
                if gem:
                    gem["weapon_id"] = weapon_id
                    self.gem_by_weapon_id[weapon_id] = gem
            elif class_name == "SC_WEAPON_DETACH_GEM":
                gem_id = int(getattr(message, "detach_gemid", 0) or weapon.get("attach_gem_id") or 0)
                weapon["attach_gem_id"] = 0
                self.gem_by_weapon_id.pop(weapon_id, None)
                if gem_id and gem_id in self.gem_by_inst_id:
                    self.gem_by_inst_id[gem_id]["weapon_id"] = 0
        self._emit_loadout_event(class_name, timestamp_ms)

    @staticmethod
    def _format_enhance_map(values: dict[int, int]) -> str:
        if not values:
            return ""
        return ",".join(f"{key}:{values[key]}" for key in sorted(values))

    @staticmethod
    def _format_int_map(values: dict[Any, Any]) -> str:
        if not values:
            return "{}"
        parts = []
        for key in sorted(values, key=lambda item: str(item)):
            parts.append(f"[{key}]={values[key]}")
        return "{" + " ".join(parts) + "}"

    @staticmethod
    def _format_int_list(values: Any) -> str:
        out: list[int] = []
        for value in values or []:
            try:
                out.append(int(value))
            except (TypeError, ValueError):
                continue
        return ",".join(str(value) for value in sorted(set(out)))

    @staticmethod
    def _equip_slot_candidates(template: str) -> list[int]:
        return list(equip_piece_catalog().get(template, {}).get('valid_slots') or [])

    @classmethod
    def _infer_equip_slot(cls, template: str, used_slots: set[int]) -> int | None:
        candidates = cls._equip_slot_candidates(template)
        return candidates[0] if len(candidates) == 1 and candidates[0] not in used_slots else None

    @classmethod
    def _format_loadout_equip(cls, equip: dict[str, object], equip_id: int) -> str:
        template = str(equip.get("template_string") or equip.get("templateid") or equip_id)
        enhance = equip.get("enhance") if isinstance(equip.get("enhance"), dict) else {}
        enhance_text = cls._format_enhance_map({int(k): int(v) for k, v in dict(enhance).items()})
        stat_text = format_equip_stats(template, enhance if isinstance(enhance, dict) else {})
        equip_text = f"{template}|lv={enhance_text}" if enhance_text else template
        return f"{equip_text}|stats={stat_text}" if stat_text else equip_text

    def _loadout_char_ids(self) -> list[int]:
        current_ids = [char_id for char_id in self.current_team_char_ids if char_id in self.char_bag_by_objid]
        scene_ids = [char_id for char_id in self.scene_team_char_ids if char_id in self.char_bag_by_objid]
        dungeon_team = getattr(self, "dungeon_team_char_ids", None)
        dungeon_ids = [
            char_id
            for char_id in (dungeon_team or [])
            if char_id in self.char_bag_by_objid
        ]
        if scene_ids:
            if dungeon_ids and set(dungeon_ids) == set(scene_ids):
                return dungeon_ids
            if current_ids and set(current_ids) == set(scene_ids):
                return current_ids
            return scene_ids
        if dungeon_team is not None:
            return dungeon_ids
        return current_ids

    @classmethod
    def _format_weapon_source_skills(cls, values: Any) -> str:
        parts: list[str] = []
        for item in values or []:
            if not isinstance(item, dict):
                continue
            skill_int_id = int(item.get("skill_int_id") or 0)
            level = int(item.get("level") or 0)
            potential_lv = int(item.get("potential_lv") or 0)
            blackboard = item.get("blackboard") if isinstance(item.get("blackboard"), dict) else {}
            bb_text = cls._format_blackboard_values(blackboard)
            parts.append(f"{skill_int_id}:level={level}:potentialLv={potential_lv}:bb={{{bb_text}}}")
        return ";".join(parts)

    def _loadout_rows(self) -> list[dict[str, object]]:
        char_ids = self._loadout_char_ids()
        if not char_ids:
            return []
        rows: list[dict[str, object]] = []
        for slot, char_id in enumerate(char_ids):
            char_info = self.char_bag_by_objid.get(char_id)
            if not char_info:
                continue
            weapon_id = int(char_info.get("weapon_id") or 0)
            weapon = self.weapon_by_inst_id.get(weapon_id, {})
            equip_col = {int(k): int(v) for k, v in dict(char_info.get("equip_col") or {}).items()}
            equips: dict[int, str] = {}
            equip_inst_ids: dict[int, int] = {}
            used_slots: set[int] = set()
            used_equip_ids: set[int] = set()
            for equip_slot, equip_id in sorted(equip_col.items()):
                equip = self.equip_by_inst_id.get(equip_id, {})
                if not equip_id or equip_id in used_equip_ids:
                    continue
                equips[equip_slot] = self._format_loadout_equip(equip, equip_id)
                equip_inst_ids[equip_slot] = equip_id
                used_slots.add(equip_slot)
                used_equip_ids.add(equip_id)
            equipped_items = [
                (equip_id, equip)
                for equip_id, equip in self.equip_by_inst_id.items()
                if char_info.get('equipment_snapshot_complete') is not True
                and int(equip.get("equip_char_id") or 0) == char_id and int(equip_id) not in used_equip_ids
            ]
            for equip_id, equip in sorted(equipped_items, key=lambda item: str(item[1].get("template_string") or item[0])):
                template = str(equip.get("template_string") or equip.get("templateid") or equip_id)
                equip_slot = self._infer_equip_slot(template, used_slots)
                if equip_slot is None:
                    continue
                equips[equip_slot] = self._format_loadout_equip(equip, int(equip_id))
                equip_inst_ids[equip_slot] = int(equip_id)
                used_slots.add(equip_slot)
                used_equip_ids.add(int(equip_id))
            attached_gem_id = int(weapon.get("attach_gem_id") or 0)
            attached_gem = self.gem_by_inst_id.get(attached_gem_id) if attached_gem_id else None
            if attached_gem is None and weapon_id:
                attached_gem = self.gem_by_weapon_id.get(weapon_id)
                if attached_gem:
                    attached_gem_id = int(attached_gem.get("gem_id") or attached_gem.get("inst_id") or 0)
            weapon_lv = int(weapon.get("weapon_lv") or 0)
            weapon_template = str(weapon.get("template_string") or weapon.get("template_id") or weapon_id or "unknown_weapon")
            base_atk_bounds = weapon_base_atk_bounds(weapon_template)
            weapon_sync_source = str(weapon.get("sync_source") or "missing")
            raw_weapon_source_skills = char_info.get("weapon_source_skills")
            refine = int(weapon.get("refine_lv") or 0)
            if weapon_sync_source in {"template_only", "missing"}:
                refine = None
            inferred_refine = (
                infer_weapon_refine_from_source_skills(weapon_template, raw_weapon_source_skills,
                    gem_terms=attached_gem.get("terms") if attached_gem
                    and attached_gem_id > 0 and int(attached_gem.get("weapon_id") or 0) == weapon_id else None)
                if weapon_sync_source == "template_only"
                else None
            )
            if inferred_refine is not None:
                refine = inferred_refine
                weapon_sync_source = f"{weapon_sync_source}+source_skill_refine"
            weapon_source_skills = self._format_weapon_source_skills(raw_weapon_source_skills)
            weapon_refine_stats = weapon_source_skills or (
                format_weapon_refine_stats(weapon_template, refine) if refine is not None else ""
            )
            weapon_refine_stats_source = "server_weapon_skill" if weapon_source_skills else "weapon_data_refine"
            rows.append(
                {
                    "slot": slot,
                    "char": str(char_info.get("templateid") or ""),
                    "char_inst_id": char_id,
                    "char_level": int(char_info.get("level") or 0),
                    "potential": int(char_info.get("potential") or 0),
                    **({"equip_medicine_id": char_info["equip_medicine_id"]}
                       if "equip_medicine_id" in char_info else {}),
                    **({"reported_skill_level_infos": copy.deepcopy(char_info["skill_level_infos"])}
                       if "skill_level_infos" in char_info else {}),
                    "weapon_inst_id": weapon_id,
                    "weapon_template": weapon_template,
                    "weapon_sync_source": weapon_sync_source,
                    "weapon_lv": weapon_lv,
                    "refine": refine,
                    "breakthrough": int(weapon.get("breakthrough_lv") or 0),
                    "weapon_base_atk": weapon_base_atk(weapon_template, weapon_lv),
                    "weapon_base_atk_lv1": base_atk_bounds.get("lv1"),
                    "weapon_base_atk_max": base_atk_bounds.get("max"),
                    "weapon_refine_stats": weapon_refine_stats,
                    "weapon_refine_stats_source": weapon_refine_stats_source,
                    "attached_gem": attached_gem_id,
                    "gem_weapon_id": int(attached_gem.get("weapon_id") or 0) if attached_gem else 0,
                    "gem_template": int(attached_gem.get("template_id") or 0) if attached_gem else 0,
                    "gem_terms": format_gem_terms(attached_gem),
                    "equip_inst_ids": self._format_int_map(equip_inst_ids),
                    "equips": self._format_int_map(equips),
                    "equipment_snapshot_complete": char_info.get('equipment_snapshot_complete') is True,
                    "unresolved_equip_inst_ids": sorted(int(equip_id) for equip_id, _ in equipped_items
                                                         if int(equip_id) not in used_equip_ids),
                    "equip_suit": self._format_int_map(dict(char_info.get("equip_suit") or {})),
                    "skill_int_ids": self._format_int_list(char_info.get("skill_int_ids")),
                    "weapon_source_skills": weapon_source_skills,
                }
            )
        return rows

    def _emit_loadout_event(self, reason: str, timestamp_ms: int, *, force: bool = False) -> None:
        rows = self._loadout_rows()
        if not rows:
            return
        signature = json.dumps(rows, ensure_ascii=False, sort_keys=True)
        if not force and signature == self._last_loadout_signature:
            return
        self._last_loadout_signature = signature
        self._emit_event(
            BattleLogEvent(
                session_id=self.session_id,
                timestamp_ms=timestamp_ms,
                event_type="LOADOUT",
                payload={"reason": reason, "rows": rows},
            )
        )

    @staticmethod
    def _template_key(template_type: str | None, template_int_id: int | None, template_str_id: str | None) -> str | None:
        if template_str_id:
            prefix = (template_type or "unknown").lower()
            return f"{prefix}:str:{template_str_id}"
        if template_int_id is not None:
            prefix = (template_type or "unknown").lower()
            return f"{prefix}:int:{template_int_id}"
        return None

    def _cache_scene_skill_levels(self, battle_inst_id: int, battle_info: Any) -> None:
        per_battle: dict[str, int] = {}
        for skill in getattr(battle_info, "skill_list", []):
            skill_id = getattr(skill, "skill_id", None)
            template_type, template_int_id, template_str_id = self._template_id_payload(skill_id)
            key = self._template_key(template_type, template_int_id, template_str_id)
            if key is None:
                continue
            level = int(getattr(skill, "level", 1) or 1)
            per_battle[key] = level
            self.global_skill_levels[key] = level
        if per_battle:
            self.skill_levels_by_battle_inst[battle_inst_id] = per_battle
        member = getattr(self, "squad_index", {}).get(battle_inst_id)
        char_info = getattr(self, "char_bag_by_objid", {}).get(member.obj_id) if member and member.obj_id else None
        if char_info and char_info.get("templateid") == member.templateid:
            weapon_id = int(char_info.get("weapon_id") or 0)
            weapon = self.weapon_by_inst_id.get(weapon_id, {})
            weapon_template = str(weapon.get("template_string") or "")
            scene_weapon_skills = self._weapon_source_skills(battle_info)
            gem_id = int(weapon.get("attach_gem_id") or 0)
            gem = getattr(self, "gem_by_inst_id", {}).get(gem_id) if gem_id else None
            if gem is None:
                gem = getattr(self, "gem_by_weapon_id", {}).get(weapon_id)
                gem_id = int(gem.get("gem_id") or gem.get("inst_id") or 0) if gem else 0
            gem_terms = (gem.get("terms") if gem and gem_id > 0
                         and int(gem.get("weapon_id") or 0) == weapon_id else None)
            # A scene refresh contains the new weapon's actual source skills.
            # Bind it through the declared squad owner and equipped instance;
            # another weapon's main skill cannot establish this refinement.
            if (weapon_id
                    and infer_weapon_refine_from_source_skills(weapon_template, char_info.get("weapon_source_skills"), gem_terms=gem_terms) is None
                    and infer_weapon_refine_from_source_skills(weapon_template, scene_weapon_skills, gem_terms=gem_terms) is not None):
                self._replace_weapon_source_skills(char_info, scene_weapon_skills)

    def _remove_entities(self, message: Any, timestamp_ms: int | None = None) -> None:
        removed_objects: list[dict[str, object | None]] = []
        for item in getattr(message, "obj_list", []):
            obj_id = int(getattr(item, "obj_id", 0))
            if not obj_id:
                continue
            battle_inst_id = self.obj_to_battle.pop(obj_id, None)
            entity = self.entity_index.get(battle_inst_id) if battle_inst_id is not None else None
            removed_objects.append({
                "obj_id": str(obj_id),
                "battle_inst_id": str(battle_inst_id) if battle_inst_id is not None else None,
                "templateid": entity.templateid if entity is not None else None,
                "entity_type": entity.entity_type if entity is not None else None,
                "obj_type": int(getattr(item, "obj_type", 0)),
            })
            if battle_inst_id is not None:
                self.entity_index.pop(battle_inst_id, None)
                self.squad_index.pop(battle_inst_id, None)
                detached = self.server_skill_state_by_owner_inst.pop(battle_inst_id, {})
                for skill_inst_id in detached:
                    self.server_skill_owner_by_skill_inst.pop(skill_inst_id, None)
        if timestamp_ms is not None:
            self._emit_event(BattleLogEvent(
                session_id=self.session_id, timestamp_ms=timestamp_ms,
                event_type="SC_OBJECT_LEAVE_VIEW", payload={"objects": removed_objects},
            ))

    def _resolve_display_name(self, templateid: str | None) -> str | None:
        if templateid is None:
            return None
        return self.name_index.get(templateid, templateid)

    @classmethod
    def _extract_endmin_variant(cls, text: str | None) -> str | None:
        if not text:
            return None
        for variant in cls.ENDMIN_VARIANTS:
            if variant in text:
                return variant
        return None

    @classmethod
    def _infer_endmin_variant(cls, battle_info: Any) -> str | None:
        if battle_info is None:
            return None

        for buff in getattr(battle_info, "buff_list", []):
            variant = cls._extract_endmin_variant(str(getattr(buff, "stacking_group_id", "")) or None)
            if variant is not None:
                return variant

        for group in getattr(battle_info, "stacking_group_list", []):
            variant = cls._extract_endmin_variant(str(getattr(group, "stacking_key", "")) or None)
            if variant is not None:
                return variant

        for skill in getattr(battle_info, "skill_list", []):
            for node_id in getattr(skill, "talent_node_ids", []):
                variant = cls._extract_endmin_variant(str(node_id))
                if variant is not None:
                    return variant
            blackboard = getattr(getattr(skill, "blackboard", None), "blackboard", None)
            if blackboard is None:
                continue
            for value in blackboard.values():
                variant = cls._extract_endmin_variant(str(getattr(value, "str_value", "")) or None)
                if variant is not None:
                    return variant

        return None

    def _resolve_scene_templateid(self, common_info: Any, battle_info: Any) -> str | None:
        templateid = str(getattr(common_info, "templateid", "")) or None
        if templateid != "chr_9000_endmin":
            return templateid
        return self._infer_endmin_variant(battle_info) or templateid

    def _emit_scene_info_event(
        self,
        timestamp_ms: int,
        *,
        dungeon_context: dict[str, object | None] | None = None,
        message: Any | None = None,
    ) -> None:
        members = sorted(self.squad_index.values(), key=lambda item: item.squad_index)
        LOGGER.info(
            "squad update members=%s",
            [(member.squad_index, member.display_name, member.battle_inst_id) for member in members],
        )
        payload: dict[str, object | None] = {
            "dungeon": dungeon_context,
            "char_list": [
                {
                    "id": member.obj_id,
                    "templateid": member.templateid,
                    "battle_inst_id": member.battle_inst_id,
                    "display_name": member.display_name or self._resolve_display_name(member.templateid),
                    "potential_level": self._potential_level_for_template(member.templateid),
                    "is_leader": member.is_leader,
                    "squad_index": member.squad_index,
                    "level": member.level,
                    "hp": member.hp,
                    "attrs": member.attrs,
                }
                for member in members
            ],
        }
        if message is not None:
            payload["scene_num_id"] = _optional_int(getattr(message, "scene_num_id", None))
            payload["scene_id"] = str(getattr(message, "scene_id", "") or "") or None
        self._emit_event(
            BattleLogEvent(
                session_id=self.session_id,
                timestamp_ms=timestamp_ms,
                event_type="SC_SELF_SCENE_INFO",
                payload=payload,
            )
        )

    def _potential_level_for_template(self, templateid: str | None) -> int:
        if templateid is None:
            return 0
        level = self.char_potential_levels.get(templateid)
        if level is not None:
            return level
        if templateid in self.ENDMIN_VARIANTS:
            return self.char_potential_levels.get("chr_9000_endmin", 0)
        return 0

    def _skill_level_for_trigger(
        self,
        op: Any,
        trigger_data: Any,
        template_type: str | None,
        template_int_id: int | None,
        template_str_id: str | None,
        action: Any,
    ) -> int | None:
        key = self._template_key(template_type, template_int_id, template_str_id)
        if key is None:
            return None
        owner_ids: list[int] = []
        damage_action = getattr(action, "damage_action", None)
        attacker_id = int(getattr(damage_action, "attacker_id", 0)) if damage_action is not None else 0
        if attacker_id:
            owner_ids.append(attacker_id)
        trigger_owner = int(getattr(trigger_data, "owner_id", 0) or 0)
        if trigger_owner:
            owner_ids.append(trigger_owner)
        op_owner = int(getattr(op, "owner_id", 0) or 0)
        if op_owner:
            owner_ids.append(op_owner)
        distinct_owner_ids = set(owner_ids)
        if len(distinct_owner_ids) > 1:
            return None
        if not distinct_owner_ids:
            return None
        return self.skill_levels_by_battle_inst.get(owner_ids[0], {}).get(key)

    @staticmethod
    def _read_float_presence(detail: Any, field_name: str) -> float | None:
        try:
            if detail.HasField(field_name):
                return float(getattr(detail, field_name))
        except ValueError:
            value = float(getattr(detail, field_name, 0.0))
            if value != 0.0:
                return value
        return None

    @staticmethod
    def _field_enum_name(message: Any, field_name: str, default: str | None = None) -> str | None:
        try:
            field = type(message).DESCRIPTOR.fields_by_name[field_name]
            return field.enum_type.values_by_number[int(getattr(message, field_name))].name
        except Exception:
            return default

    @classmethod
    def _template_id_payload(cls, template_id: Any) -> tuple[str | None, int | None, str | None]:
        if template_id is None:
            return None, None, None
        return (
            cls._field_enum_name(template_id, "type"),
            int(getattr(template_id, "int_id", 0)) or None,
            str(getattr(template_id, "str_id", "")) or None,
        )

    @classmethod
    def _battle_skill_int_ids(cls, battle_mgr_info: Any) -> list[int]:
        if battle_mgr_info is None:
            return []
        out: set[int] = set()
        for skill in getattr(battle_mgr_info, "skill_list", []):
            _, template_int_id, _ = cls._template_id_payload(getattr(skill, "skill_id", None))
            if template_int_id is not None:
                out.add(template_int_id)
        return sorted(out)

    @staticmethod
    def _blackboard_values(blackboard: Any) -> dict[str, object]:
        values = getattr(blackboard, "blackboard", None)
        if values is None:
            return {}
        out: dict[str, object] = {}
        for key, value in dict(values).items():
            str_value = str(getattr(value, "str_value", "") or "")
            is_dynamic = bool(getattr(value, "is_dynamic", False))
            if str_value or is_dynamic:
                out[str(key)] = str_value
                continue
            out[str(key)] = float(getattr(value, "float_value", 0.0) or 0.0)
        return out

    @staticmethod
    def _format_blackboard_values(values: dict[str, object]) -> str:
        parts: list[str] = []
        for key in sorted(values):
            value = values[key]
            if isinstance(value, float):
                parts.append(f"{key}={value:.12g}")
            else:
                parts.append(f"{key}={value}")
        return ",".join(parts)

    @classmethod
    def _weapon_source_skills(cls, battle_mgr_info: Any) -> list[dict[str, object]]:
        if battle_mgr_info is None:
            return []
        out: list[dict[str, object]] = []
        for skill in getattr(battle_mgr_info, "skill_list", []):
            if cls._field_enum_name(skill, "source") != "Weapon":
                continue
            _, template_int_id, template_str_id = cls._template_id_payload(getattr(skill, "skill_id", None))
            blackboard = cls._blackboard_values(getattr(skill, "blackboard", None))
            out.append(
                {
                    "skill_int_id": template_int_id or 0,
                    "skill_str_id": template_str_id or "",
                    "inst_id": int(getattr(skill, "inst_id", 0) or 0),
                    "potential_lv": int(getattr(skill, "potential_lv", 0) or 0),
                    "level": int(getattr(skill, "level", 0) or 0),
                    "blackboard": blackboard,
                }
            )
        return out

    def _owner_meta_payload(self, owner_inst_id: int | None) -> dict[str, object | None]:
        member = self.squad_index.get(owner_inst_id or 0) if owner_inst_id is not None else None
        entity = self.entity_index.get(owner_inst_id or 0) if owner_inst_id is not None else None
        templateid = None
        obj_id = None
        display_name = None
        squad_index = None
        if member is not None:
            templateid = member.templateid
            obj_id = member.obj_id
            display_name = member.display_name or self._resolve_display_name(member.templateid)
            squad_index = member.squad_index
        elif entity is not None:
            templateid = entity.templateid
            obj_id = entity.obj_id
            display_name = self._resolve_display_name(entity.templateid)
        return {
            "owner_inst_id": owner_inst_id,
            "owner_obj_id": obj_id,
            "owner_templateid": templateid,
            "owner_display_name": display_name,
            "owner_squad_index": squad_index,
        }

    def _server_skill_row(self, owner_inst_id: int, skill: Any) -> dict[str, object | None]:
        template_type, template_int_id, template_str_id = self._template_id_payload(getattr(skill, "skill_id", None))
        raw_data = self._message_to_payload(skill, include_implicit_defaults=True)
        row: dict[str, object | None] = {
            "owner_inst_id": owner_inst_id,
            "skill_inst_id": int(getattr(skill, "inst_id", 0)) or None,
            "template_type": template_type,
            "template_int_id": template_int_id,
            "level": int(getattr(skill, "level", 0)),
            "potential_lv": int(getattr(skill, "potential_lv", 0)),
            "source": self._field_enum_name(skill, "source"),
            "is_server_init": bool(getattr(skill, "is_server_init", False)),
            "is_enable": bool(getattr(skill, "is_enable", False)),
            "is_passive": bool(getattr(skill, "is_passive", False)),
            # Keep typed string/float/dynamic fields distinct. An absent
            # blackboard message is unknown, not an invented empty snapshot.
            "skill_id": copy.deepcopy(raw_data.get("skill_id")),
            "blackboard": copy.deepcopy(raw_data.get("blackboard")),
            "talent_node_ids": [str(node_id) for node_id in getattr(skill, "talent_node_ids", [])],
            "raw_data": raw_data,
        }
        if template_str_id:
            row["template_str_id"] = template_str_id
        return row

    def _cache_server_skill_rows(self, owner_inst_id: int, skill_rows: list[dict[str, object | None]]) -> None:
        owner_state = self.server_skill_state_by_owner_inst.setdefault(owner_inst_id, {})
        for row in skill_rows:
            skill_inst_id = row.get("skill_inst_id")
            if skill_inst_id is None:
                continue
            skill_inst_id_int = int(skill_inst_id)
            owner_state[skill_inst_id_int] = dict(row)
            self.server_skill_owner_by_skill_inst[skill_inst_id_int] = owner_inst_id

    def _replace_server_skill_rows(self, owner_inst_id: int, skill_rows: list[dict[str, object | None]]) -> None:
        previous = self.server_skill_state_by_owner_inst.pop(owner_inst_id, {})
        for skill_inst_id in previous:
            self.server_skill_owner_by_skill_inst.pop(skill_inst_id, None)
        self._cache_server_skill_rows(owner_inst_id, skill_rows)

    def _emit_server_skill_rows_event(
        self,
        event_type: str,
        owner_inst_id: int | None,
        generation: int | None,
        skill_rows: list[dict[str, object | None]],
        timestamp_ms: int,
    ) -> None:
        payload: dict[str, object | None] = self._owner_meta_payload(owner_inst_id)
        payload["generation"] = generation
        payload["skills"] = sorted(
            skill_rows,
            key=lambda row: (
                int(row.get("template_int_id") or 0),
                int(row.get("skill_inst_id") or 0),
            ),
        )
        self._emit_event(
            BattleLogEvent(
                session_id=self.session_id,
                timestamp_ms=timestamp_ms,
                event_type=event_type,
                payload=payload,
            )
        )

    def _update_server_skill_state(self, event_type: str, message: Any, timestamp_ms: int) -> None:
        owner_inst_id = int(getattr(message, "inst_id", 0)) or None
        if owner_inst_id is None:
            return
        skill_rows = [self._server_skill_row(owner_inst_id, skill) for skill in getattr(message, "skills", [])]
        self._cache_server_skill_rows(owner_inst_id, skill_rows)
        self._emit_server_skill_rows_event(
            event_type=event_type,
            owner_inst_id=owner_inst_id,
            generation=int(getattr(message, "generation", 0)),
            skill_rows=skill_rows,
            timestamp_ms=timestamp_ms,
        )

    def _detach_server_skill_state(self, message: Any, timestamp_ms: int) -> None:
        owner_inst_id = int(getattr(message, "inst_id", 0)) or None
        generation = int(getattr(message, "generation", 0))
        deleted_skill_inst_ids = [int(inst_id) for inst_id in getattr(message, "del_inst_ids", []) if int(inst_id)]
        removed_rows: list[dict[str, object | None]] = []
        if owner_inst_id is not None:
            owner_state = self.server_skill_state_by_owner_inst.get(owner_inst_id, {})
            for skill_inst_id in deleted_skill_inst_ids:
                removed = owner_state.pop(skill_inst_id, None)
                self.server_skill_owner_by_skill_inst.pop(skill_inst_id, None)
                if removed is not None:
                    removed_rows.append(removed)
            if not owner_state:
                self.server_skill_state_by_owner_inst.pop(owner_inst_id, None)
        payload: dict[str, object | None] = self._owner_meta_payload(owner_inst_id)
        payload["generation"] = generation
        payload["del_skill_inst_ids"] = deleted_skill_inst_ids
        payload["removed_skills"] = removed_rows
        self._emit_event(
            BattleLogEvent(
                session_id=self.session_id,
                timestamp_ms=timestamp_ms,
                event_type="SC_DETACH_SERVER_SKILL",
                payload=payload,
            )
        )

    @staticmethod
    def _message_to_payload(message: Any, *, include_implicit_defaults: bool = False) -> dict[str, object]:
        if message is None:
            return {}
        return MessageToDict(
            message,
            preserving_proto_field_name=True,
            use_integers_for_enums=False,
            always_print_fields_with_no_presence=include_implicit_defaults,
        )

    def _emit_contract_tags_event(self, decoded: DecodedMessage, timestamp_ms: int) -> None:
        message = decoded.message
        dungeon_id = str(getattr(message, "dungeon_id", "") or "").strip()
        tag_ids = [int(tag_id) for tag_id in getattr(message, "tag_ids", []) if int(tag_id) > 0]
        display_tag_ids = [
            int(tag_id)
            for tag_id in getattr(message, "display_tag_ids", [])
            if int(tag_id) > 0
        ]
        if not tag_ids and display_tag_ids:
            tag_ids = list(display_tag_ids)
        score = int(getattr(message, "score", 0) or 0) or None
        if not dungeon_id and not tag_ids:
            return
        self._emit_event(
            BattleLogEvent(
                session_id=self.session_id,
                timestamp_ms=timestamp_ms,
                event_type="CONTRACT_TAGS",
                payload={
                    "dungeon_id": dungeon_id or None,
                    "tag_ids": tag_ids,
                    "display_tag_ids": display_tag_ids,
                    "score": score,
                    "source": decoded.class_name,
                    "direction": decoded.direction,
                    "msg_id": decoded.msg_id,
                },
            )
        )

    def _emit_event(self, event: OutboundEvent) -> None:
        self.metrics.outbound_events_emitted += 1
        self.on_event(event)

    def _emit_battle_events(self, message: Any, timestamp_ms: int) -> None:
        client_data = getattr(message, "client_data", None)
        if client_data is None:
            return
        emitted_clock_ticks: set[int] = set()
        for op in getattr(client_data, "op_list", []):
            self._observer_operation_index = getattr(self, "_observer_operation_index", 0) + 1
            op_type_name = self._field_enum_name(op, "op_type")
            # One header for every raw operation, including unknown enum
            # values. This observer-only stream must not enter the legacy
            # trace, statistics, uploader batch or disk log. Derived events
            # can share a seq_id, so they cannot establish raw continuity.
            operation_callback = getattr(self, "on_battle_operation", None)
            if operation_callback is not None:
                try:
                    try:
                        operation_sha256 = hashlib.sha256(op.SerializeToString(deterministic=True)).hexdigest()
                    except (AttributeError, TypeError, ValueError):
                        operation_sha256 = None
                    operation_callback({
                        "type": "CAPTURE_BATTLE_OPERATION", "session_id": self.session_id,
                        "timestamp_ms": timestamp_ms, "seq_id": int(getattr(op, "seq_id", 0)),
                        "operation_type": op_type_name or "UnknownBattleOperation",
                        "op_type": int(getattr(op, "op_type", 0)),
                        "source_index": self._observer_operation_index,
                        "operation_sha256": operation_sha256,
                        "capture_integrity": {
                            "packetsDroppedQueue": self.metrics.packets_dropped_queue,
                            "decompressionErrors": self.metrics.decompression_errors,
                            "protobufDecodeErrors": self.metrics.protobuf_decode_errors,
                            "framingErrors": (self._observer_framing_errors + getattr(self.registry, "observer_framing_errors", 0)
                                              + getattr(self.client_reassembler, "observer_overlap_conflicts", 0)
                                              + getattr(self.server_reassembler, "observer_overlap_conflicts", 0)),
                            "pendingMultipacks": len(self.pending_multi_frames),
                            "pendingTransportSegments": sum(
                                1 for stream in (self.client_reassembler, self.server_reassembler)
                                for start, pending in stream._segments.items()
                                if stream._next_seq is None or start + len(pending) > stream._next_seq),
                            "currentFrameErrors": int(bool(getattr(self, "_observer_frame_invalid", False)
                                or getattr(self.client_reassembler, "observer_overlap_conflicts", 0)
                                or getattr(self.server_reassembler, "observer_overlap_conflicts", 0))),
                        },
                    })
                except Exception:
                    LOGGER.warning("read-only battle operation observer failed", exc_info=True)
            if op_type_name is None:
                continue
            seq_id = int(getattr(op, "seq_id", 0)) or None
            client_tick_tms = int(getattr(op, "client_tick_tms", 0)) or None

            if (getattr(self, "rotation_clock_trace", False) and client_tick_tms is not None
                    and client_tick_tms > 0 and client_tick_tms not in emitted_clock_ticks):
                emitted_clock_ticks.add(client_tick_tms)
                self._emit_event(BattleLogEvent(
                    session_id=self.session_id, timestamp_ms=timestamp_ms,
                    event_type="GAME_CLOCK_TICK",
                    payload={"source": "CS_BATTLE_OP", "tick_ms": client_tick_tms, "seq_id": seq_id},
                ))

            if op_type_name in {
                "BattleOpSwitchMode", "BattleOpResetBattleData",
                "BattleOpUpdateDataVersion", "BattleOpShieldValueModify",
            }:
                expected_case = {
                    "BattleOpSwitchMode": "skill_switch_mode_op_data",
                    "BattleOpResetBattleData": "reset_battle_op_data",
                    "BattleOpUpdateDataVersion": "update_data_version_op_data",
                    "BattleOpShieldValueModify": "shield_value_modify_op_data",
                }[op_type_name]
                if op.WhichOneof("op_data_impl") != expected_case:
                    continue
                data = self._message_to_payload(getattr(op, expected_case), include_implicit_defaults=True)
                self._emit_event(BattleLogEvent(
                    session_id=self.session_id, timestamp_ms=timestamp_ms, event_type=op_type_name,
                    payload={**data, "seq_id": int(op.seq_id),
                             "client_tick_tms": str(op.client_tick_tms),
                             "owner_id": str(op.owner_id), "operation_owner_id": str(op.owner_id),
                             "generation": int(op.generation), "op_type": int(op.op_type),
                             "operation_payload_case": expected_case,
                             "operation_data_serialization": "protobuf_json_implicit_defaults_v1",
                             "raw_data": data},
                ))
                # These are observations. Reset/mode/shield names alone do
                # not establish a Buff finish, scene reset or timing rule.
                continue
            if op_type_name == "BattleOpSkillAttach":
                attach_data = getattr(op, "skill_attach_op_data", None)
                if attach_data is None:
                    continue
                template_type, template_int_id, template_str_id = self._template_id_payload(
                    getattr(attach_data, "skill_id", None)
                )
                payload: dict[str, object | None] = {
                    "seq_id": seq_id,
                    "client_tick_tms": client_tick_tms,
                    "src_inst_id": int(getattr(attach_data, "src_inst_id", 0)) or None,
                    "skill_inst_id": int(getattr(attach_data, "skill_inst_id", 0)) or None,
                    "skill_lv": int(getattr(attach_data, "skill_lv", 0)) or None,
                    "skill_source": self._field_enum_name(attach_data, "skill_source"),
                    "template_type": template_type,
                    "template_int_id": template_int_id,
                }
                if template_str_id:
                    payload["template_str_id"] = template_str_id
                self._emit_event(
                    BattleLogEvent(
                        session_id=self.session_id,
                        timestamp_ms=timestamp_ms,
                        event_type=op_type_name,
                        payload=payload,
                    )
                )
                continue
            if op_type_name == "BattleOpSkillDetach":
                detach_data = getattr(op, "skill_detach_op_data", None)
                if detach_data is None:
                    continue
                self._emit_event(
                    BattleLogEvent(
                        session_id=self.session_id,
                        timestamp_ms=timestamp_ms,
                        event_type=op_type_name,
                        payload={
                            "seq_id": seq_id,
                            "client_tick_tms": client_tick_tms,
                            "skill_inst_id": int(getattr(detach_data, "skill_inst_id", 0)) or None,
                        },
                    )
                )
                continue
            if op_type_name == "BattleOpSkillStartCast":
                cast_data = getattr(op, "skill_start_cast_op_data", None)
                if cast_data is None:
                    continue
                self._emit_event(
                    BattleLogEvent(
                        session_id=self.session_id,
                        timestamp_ms=timestamp_ms,
                        event_type=op_type_name,
                        payload={
                            "seq_id": seq_id,
                            "client_tick_tms": client_tick_tms,
                            "owner_id": int(getattr(op, "owner_id", 0)) or None,
                            "skill_inst_id": int(getattr(cast_data, "inst_id", 0)) or None,
                        },
                    )
                )
                continue
            if op_type_name == "BattleOpSkillEndCast":
                cast_data = getattr(op, "skill_end_cast_op_data", None)
                if cast_data is None:
                    continue
                self._emit_event(
                    BattleLogEvent(
                        session_id=self.session_id,
                        timestamp_ms=timestamp_ms,
                        event_type=op_type_name,
                        payload={
                            "seq_id": seq_id,
                            "client_tick_tms": client_tick_tms,
                            "owner_id": int(getattr(op, "owner_id", 0)) or None,
                            "skill_inst_id": int(getattr(cast_data, "inst_id", 0)) or None,
                        },
                    )
                )
                continue
            if op_type_name == "BattleOpTriggerAction":
                self._emit_trigger_action_event(op, seq_id, client_tick_tms, timestamp_ms)
                continue
            if op_type_name == 'BattleOpTriggerKeyword':
                if op.WhichOneof('op_data_impl') != 'trigger_keyword_op_data':
                    continue
                keyword = op.trigger_keyword_op_data
                self._emit_event(BattleLogEvent(
                    session_id=self.session_id, timestamp_ms=timestamp_ms, event_type=op_type_name,
                    payload={'seq_id': int(op.seq_id), 'client_tick_tms': int(op.client_tick_tms),
                             'owner_id': str(op.owner_id), 'generation': int(op.generation),
                             'buff_inst_id': str(keyword.buff_inst_id),
                             'add_buff_num_id': int(keyword.add_buff_num_id),
                             'modify': [{'config_index': int(row.config_index),
                                         'modify_value': float(row.modify_value)} for row in keyword.modify],
                             'operation_payload_case': op.WhichOneof('op_data_impl')},
                ))
                continue
            if op_type_name == "BattleOpSetBuffEnableState":
                state_data = getattr(op, 'set_buff_enable_state_op_data', None)
                if state_data is None:
                    continue
                self._emit_event(BattleLogEvent(
                    session_id=self.session_id, timestamp_ms=timestamp_ms, event_type=op_type_name,
                    payload={'seq_id': int(op.seq_id), 'client_tick_tms': int(op.client_tick_tms),
                             'owner_id': int(op.owner_id), 'generation': int(op.generation),
                             'states': {str(uid): enabled for uid, enabled in state_data.buff_inst_id_to_enable_state.items()}},
                ))
                continue
            if op_type_name == "BattleOpFinishBuff":
                finish_data = getattr(op, "finish_buff_op_data", None)
                if finish_data is None:
                    continue
                self._emit_event(
                    BattleLogEvent(
                        session_id=self.session_id,
                        timestamp_ms=timestamp_ms,
                        event_type=op_type_name,
                        payload={
                            "seq_id": seq_id,
                            "client_tick_tms": client_tick_tms,
                            "buff_inst_id": int(getattr(finish_data, "buff_inst_id", 0)) or None,
                            "operation_owner_id": str(op.owner_id),
                            "generation": int(op.generation),
                            "reduce_layer": int(finish_data.reduce_layer),
                            "force": bool(finish_data.force),
                            "raw_data": self._message_to_payload(finish_data, include_implicit_defaults=True),
                        },
                    )
                )
                continue
            if op_type_name == "BattleOpAddBuff":
                add_data = getattr(op, "add_buff_op_data", None)
                if add_data is None:
                    continue
                _, template_int_id, template_str_id = self._template_id_payload(getattr(add_data, "buff_id", None))
                payload: dict[str, object | None] = {
                    "seq_id": seq_id,
                    "client_tick_tms": client_tick_tms,
                    "operation_owner_id": str(op.owner_id) if hasattr(op, "owner_id") else None,
                    "generation": int(op.generation) if hasattr(op, "generation") else None,
                    "int_id": template_int_id,
                    "buff_inst_id": int(getattr(add_data, "buff_inst_id", 0)) or None,
                    "src_inst_id": int(getattr(add_data, "src_inst_id", 0)) or None,
                    "target_inst_id": int(getattr(add_data, "target_inst_id", 0)) or None,
                    "assigned_items": self._message_to_payload(getattr(add_data, "assigned_items", None)),
                    "raw_data": self._message_to_payload(add_data, include_implicit_defaults=True),
                }
                buff_template = getattr(add_data, "buff_id", None)
                if buff_template is not None and hasattr(buff_template, "WhichOneof"):
                    payload["buff_template_id"] = self._message_to_payload(buff_template, include_implicit_defaults=True)
                    payload["buff_template_id_case"] = buff_template.WhichOneof("id_impl")
                if template_str_id:
                    payload["str_id"] = template_str_id
                self._emit_event(
                    BattleLogEvent(
                        session_id=self.session_id,
                        timestamp_ms=timestamp_ms,
                        event_type=op_type_name,
                        payload=payload,
                    )
                )
                continue
            if op_type_name == "BattleOpEnablePassiveSkill":
                enable_data = getattr(op, "skill_enable_op_data", None)
                if enable_data is None:
                    continue
                self._emit_event(
                    BattleLogEvent(
                        session_id=self.session_id,
                        timestamp_ms=timestamp_ms,
                        event_type=op_type_name,
                        payload={
                            "seq_id": seq_id,
                            "client_tick_tms": client_tick_tms,
                            "skill_inst_id": int(getattr(enable_data, "skill_inst_id", 0)) or None,
                        },
                    )
                )
                continue
            if op_type_name == "BattleOpEntityDie":
                die_data = getattr(op, "entity_die_op_data", None)
                if die_data is None:
                    continue
                entity_inst_id = int(getattr(die_data, "entity_inst_id", 0)) or None
                self._emit_event(
                    BattleLogEvent(
                        session_id=self.session_id,
                        timestamp_ms=timestamp_ms,
                        event_type=op_type_name,
                        payload={
                            "seq_id": seq_id,
                            "client_tick_tms": client_tick_tms,
                            "entity_inst_id": entity_inst_id,
                        },
                    )
                )
                if (
                    self.merge_multi_phase_enemy_battles
                    and self.merged_battle_active
                    and entity_inst_id is not None
                    and entity_inst_id in self.tracked_enemy_inst_ids
                ):
                    self._emit_event(
                        BattleLogEvent(
                            session_id=self.session_id,
                            timestamp_ms=timestamp_ms,
                            event_type="BattleOpModifyBattleState",
                            payload={
                                "seq_id": seq_id,
                                "client_tick_tms": client_tick_tms,
                                "is_in_battle": False,
                            },
                        )
                    )
                    self.merged_battle_active = False
                    self.tracked_enemy_inst_ids.clear()
                continue
            if op_type_name == "BattleOpEntityValueModify":
                value_data = getattr(op, "entity_value_modify_data", None)
                if value_data is None:
                    continue
                template_type, template_int_id, template_str_id = self._template_id_payload(getattr(value_data, "template_id", None))
                orig_template_type, orig_template_int_id, orig_template_str_id = self._template_id_payload(
                    getattr(value_data, "original_source_template_id", None)
                )
                battle_info = getattr(value_data, "value", None)
                payload: dict[str, object | None] = {
                    "seq_id": seq_id,
                    "client_tick_tms": client_tick_tms,
                    "owner_id": int(getattr(op, "owner_id", 0)) or None,
                    "entity_inst_id": int(getattr(value_data, "entity_inst_id", 0)) or None,
                    "scene_num_id": int(getattr(value_data, "scene_num_id", 0)) or None,
                    "source": int(getattr(value_data, "source", 0)) or None,
                    "hp": float(getattr(battle_info, "hp", 0.0) or 0.0) if battle_info is not None else None,
                    "ultimatesp": float(getattr(battle_info, "ultimatesp", 0.0) or 0.0) if battle_info is not None else None,
                    "delta_value": self._read_float_presence(value_data, "delta_value"),
                    "template_type": template_type,
                    "template_int_id": template_int_id,
                    "orig_template_type": orig_template_type,
                    "orig_template_int_id": orig_template_int_id,
                }
                if template_str_id:
                    payload["template_str_id"] = template_str_id
                if orig_template_str_id:
                    payload["orig_template_str_id"] = orig_template_str_id
                self._emit_event(
                    BattleLogEvent(
                        session_id=self.session_id,
                        timestamp_ms=timestamp_ms,
                        event_type=op_type_name,
                        payload=payload,
                    )
                )
                continue
            if op_type_name == "BattleOpModifyBattleState":
                battle_state_data = getattr(op, "modify_battle_state_op_data", None)
                if battle_state_data is None:
                    continue
                is_in_battle = bool(getattr(battle_state_data, "is_in_battle", False))
                if self.merge_multi_phase_enemy_battles and self.tracked_enemy_templateid is not None:
                    if is_in_battle and not self.merged_battle_active:
                        self.merged_battle_active = True
                    else:
                        continue
                self._emit_event(
                    BattleLogEvent(
                        session_id=self.session_id,
                        timestamp_ms=timestamp_ms,
                        event_type=op_type_name,
                        payload={
                            "seq_id": seq_id,
                            "client_tick_tms": client_tick_tms,
                            "is_in_battle": is_in_battle,
                        },
                    )
                )
                if is_in_battle:
                    if self.squad_index:
                        self._emit_scene_info_event(timestamp_ms)
                    self._emit_loadout_event("BATTLE_START", timestamp_ms, force=True)
                continue
            if op_type_name == "BattleOpModifyPoiseValue":
                poise_data = getattr(op, "modify_poise_value_op_data", None)
                if poise_data is None:
                    continue
                template_type, template_int_id, template_str_id = self._template_id_payload(
                    getattr(poise_data, "template_id", None)
                )
                orig_template_type, orig_template_int_id, orig_template_str_id = self._template_id_payload(
                    getattr(poise_data, "original_source_template_id", None)
                )
                payload = {
                    "seq_id": seq_id,
                    "client_tick_tms": client_tick_tms,
                    "owner_id": int(getattr(op, "owner_id", 0)) or None,
                    "modify_type": self._field_enum_name(poise_data, "modify_type"),
                    "owner_type": self._field_enum_name(poise_data, "owner_type"),
                    "value": float(getattr(poise_data, "value", 0.0) or 0.0),
                    "cur_poise_value": float(getattr(poise_data, "cur_poise_value", 0.0) or 0.0),
                    "attacker_inst_id": int(getattr(poise_data, "attacker_inst_id", 0)) or None,
                    "action_id": int(getattr(poise_data, "action_id", 0)) or None,
                    "template_type": template_type,
                    "template_int_id": template_int_id,
                    "orig_template_type": orig_template_type,
                    "orig_template_int_id": orig_template_int_id,
                    "battle_report_info": self._message_to_payload(getattr(poise_data, "battle_report_info", None)),
                }
                if template_str_id:
                    payload["template_str_id"] = template_str_id
                if orig_template_str_id:
                    payload["orig_template_str_id"] = orig_template_str_id
                self._emit_event(
                    BattleLogEvent(
                        session_id=self.session_id,
                        timestamp_ms=timestamp_ms,
                        event_type=op_type_name,
                        payload=payload,
                    )
                )
                continue

            if op_type_name == "BattleOpUpdateAtbInfo":
                # 技力(atb)同步：主控角色的重击(连招末段)回技力 → reason=AddValue 的正 delta，
                # 归因到该角色。用于排轴区分主控重击 vs 非主控 AI 重击。
                atb_op = getattr(op, "update_atb_info_op_data", None)
                if atb_op is None:
                    continue
                atb_data = getattr(atb_op, "atb_data", None)
                template_type, template_int_id, template_str_id = self._template_id_payload(
                    getattr(atb_op, "template_id", None)
                )
                orig_template_type, orig_template_int_id, orig_template_str_id = self._template_id_payload(
                    getattr(atb_op, "original_source_template_id", None)
                )
                payload = {
                    "seq_id": seq_id,
                    "client_tick_tms": client_tick_tms,
                    "owner_id": int(getattr(op, "owner_id", 0)) or None,
                    "atb_value": float(getattr(atb_data, "atb_value", 0.0) or 0.0) if atb_data is not None else None,
                    "atb_recovery_speed": (
                        float(getattr(atb_data, "atb_recovery_speed", 0.0) or 0.0) if atb_data is not None else None
                    ),
                    "reason": self._field_enum_name(atb_data, "reason") if atb_data is not None else None,
                    "delta_value": self._read_float_presence(atb_op, "delta_value"),
                    "template_type": template_type,
                    "template_int_id": template_int_id,
                    "orig_template_type": orig_template_type,
                    "orig_template_int_id": orig_template_int_id,
                }
                if template_str_id:
                    payload["template_str_id"] = template_str_id
                if orig_template_str_id:
                    payload["orig_template_str_id"] = orig_template_str_id
                self._emit_event(
                    BattleLogEvent(
                        session_id=self.session_id,
                        timestamp_ms=timestamp_ms,
                        event_type=op_type_name,
                        payload=payload,
                    )
                )
                continue

    def _emit_trigger_action_event(
        self,
        op: Any,
        seq_id: int | None,
        client_tick_tms: int | None,
        timestamp_ms: int,
    ) -> None:
        trigger_data = getattr(op, "trigger_action_op_data", None)
        if trigger_data is None:
            return
        action = getattr(trigger_data, "action", None)
        if action is None:
            return
        template_type, template_int_id, template_str_id = self._template_id_payload(getattr(trigger_data, "template_id", None))
        payload: dict[str, object | None] = {
            "seq_id": seq_id,
            "client_tick_tms": client_tick_tms,
            "owner_id": int(getattr(trigger_data, "owner_id", 0) or getattr(op, "owner_id", 0)) or None,
            # Preserve the distinct protocol identities before the legacy
            # display owner fallback. uint64 values must survive JSON readers.
            "operation_owner_id": str(op.owner_id) if hasattr(op, "owner_id") else None,
            "trigger_owner_id": str(trigger_data.owner_id) if hasattr(trigger_data, "owner_id") else None,
            "trigger_inst_id": str(trigger_data.inst_id) if hasattr(trigger_data, "inst_id") else None,
            "generation": int(op.generation) if hasattr(op, "generation") else None,
            "owner_type": str(getattr(trigger_data, "owner_type", "")) or None,
            "inst_id": int(getattr(trigger_data, "inst_id", 0)) or None,
            # This is outside BATTLE_ACTION_INST. CreateBuff also emits an
            # End-phase payload containing the instances it finishes.
            "trigger_type": int(trigger_data.trigger_type) if hasattr(trigger_data, "trigger_type") else None,
            "template_type": template_type,
            "template_int_id": template_int_id,
            # Proto3 action/unit index zero is a defined value. The default
            # JSON serializer omits it, which is indistinguishable from an
            # incomplete hand-written event once the message is discarded.
            # Emit non-presence scalar defaults here, at the typed boundary.
            # Absent message/oneof fields remain absent.
            "action": self._message_to_payload(action, include_implicit_defaults=True),
            "action_serialization": "protobuf_json_implicit_defaults_v1",
        }
        template_id = getattr(trigger_data, "template_id", None)
        if template_id is not None and hasattr(template_id, "WhichOneof"):
            payload["template_id"] = self._message_to_payload(template_id, include_implicit_defaults=True)
            payload["template_id_case"] = template_id.WhichOneof("id_impl")
        if hasattr(action, "WhichOneof"):
            payload["action_runtime_case"] = action.WhichOneof("inst_impl")
        if hasattr(op, "WhichOneof"):
            payload["operation_payload_case"] = op.WhichOneof("op_data_impl")
        damage_detail_presence = self._damage_detail_presence_payload(action)
        if damage_detail_presence:
            payload["damage_detail_presence"] = damage_detail_presence
        if template_str_id:
            payload["template_str_id"] = template_str_id
        if (template_type or "").lower() == "skill":
            payload["level"] = self._skill_level_for_trigger(
                op,
                trigger_data,
                template_type,
                template_int_id,
                template_str_id,
                action,
            )
        self._emit_event(
            BattleLogEvent(
                session_id=self.session_id,
                timestamp_ms=timestamp_ms,
                event_type="BattleOpTriggerAction",
                payload=payload,
            )
        )

    @classmethod
    def _damage_detail_presence_payload(cls, action: Any) -> list[dict[str, object]] | None:
        damage_action = getattr(action, "damage_action", None)
        if damage_action is None:
            return None
        details = getattr(damage_action, "details", None)
        if details is None:
            return None
        rows: list[dict[str, object]] = []
        for index, detail in enumerate(details):
            processor_debug_args = getattr(detail, "processor_debug_args", None)
            modifier_args = getattr(detail, "modifier_args", None)
            attacker_modifiers = list(getattr(modifier_args, "attacker_modifiers", [])) if modifier_args is not None else []
            defender_modifiers = list(getattr(modifier_args, "defender_modifiers", [])) if modifier_args is not None else []
            row = {
                "detail_index": index,
                "has_battle_report_info": cls._has_message_field(detail, "battle_report_info"),
                "has_calculation_check_args": cls._has_message_field(detail, "calculation_check_args"),
                "has_calculate_damage_check_args": cls._has_message_field(detail, "calculate_damage_check_args"),
                "processor_debug_args_count": len(processor_debug_args) if processor_debug_args is not None else 0,
                "has_attacker_attr_modifier_debug_data": cls._has_message_field(detail, "attacker_attr_modifier_debug_data"),
                "has_defender_attr_modifier_debug_data": cls._has_message_field(detail, "defender_attr_modifier_debug_data"),
                "attacker_modifier_count": len(attacker_modifiers),
                "defender_modifier_count": len(defender_modifiers),
                "attacker_modifier_debug_handle_count": cls._modifier_debug_handle_count(attacker_modifiers),
                "defender_modifier_debug_handle_count": cls._modifier_debug_handle_count(defender_modifiers),
                "attacker_modifier_debug_arg_entry_count": cls._modifier_debug_arg_entry_count(attacker_modifiers),
                "defender_modifier_debug_arg_entry_count": cls._modifier_debug_arg_entry_count(defender_modifiers),
            }
            rows.append(row)
        return rows

    @staticmethod
    def _has_message_field(message: Any, field_name: str) -> bool:
        try:
            return bool(message.HasField(field_name))
        except Exception:
            return False

    @staticmethod
    def _modifier_debug_handle_count(handles: list[Any]) -> int:
        count = 0
        for handle in handles:
            try:
                processor_debug_args = getattr(handle, "processor_debug_args", None)
                if processor_debug_args is not None and len(processor_debug_args) > 0:
                    count += 1
            except Exception:
                continue
        return count

    @staticmethod
    def _modifier_debug_arg_entry_count(handles: list[Any]) -> int:
        total = 0
        for handle in handles:
            try:
                processor_debug_args = getattr(handle, "processor_debug_args", None)
                if processor_debug_args is not None:
                    total += len(processor_debug_args)
            except Exception:
                continue
        return total

    def _emit_proto_passthrough_event(
        self,
        event_type: str,
        message: Any,
        timestamp_ms: int,
        extra_payload: dict[str, object] | None = None,
    ) -> None:
        effect_messages = {
            "SC_ADD_SERVER_BUFF", "SC_REMOVE_SERVER_BUFF",
            "SC_BATTLE_ADD_GLOBAL_BUFF", "SC_BATTLE_REMOVE_GLOBAL_BUFF",
            "SC_BATTLE_SYNC_GLOBAL_BUFF_INFO", "SC_CHAR_BAG_SYNC_PERSISTENT_BUFFS",
            "SC_SYNC_RACING_DUNGEON_BUFF_MODIFY", "SC_SYNC_RACING_DUNGEON_RECONNECT",
            "SC_RESET_BATTLE_STATUS", "SC_SCENE_RESET_ENTITY", "SC_BATTLE_GENERATION_CHANGE",
            "SC_CHAR_BAG_SET_CURR_TEAM_INDEX", "SC_CHAR_BAG_SET_CURR_TEAM_TYPE",
            "SC_CHAR_BAG_SET_MAX_TEAM_MEMBER_COUNT", "SC_CHAR_SKILL_INFOS",
            "SC_EQUIP_MEDICINE_MODIFY", "SC_EQUIP_PRODUCE",
            "SC_SYNC_CHAR_BAG_INFO", "SC_CHAR_BAG_SET_TEAM", "SC_CHAR_BAG_SET_TEAM_LEADER",
        }
        payload = self._message_to_payload(message, include_implicit_defaults=event_type in effect_messages)
        if extra_payload:
            payload.update(extra_payload)
        self._emit_event(
            BattleLogEvent(
                session_id=self.session_id,
                timestamp_ms=timestamp_ms,
                event_type=event_type,
                payload=payload,
            )
        )


class DamageLogService:
    def __init__(self, config: ServiceConfig, observer: ServiceObserver | None = None) -> None:
        self.config = config
        self.observer = observer
        self.loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None
        self._started_event = threading.Event()
        self._fatal_exception: BaseException | None = None
        self._fatal_error: dict[str, str] | None = None
        self.state = ServiceState.WAITING_GAME
        self.metrics = RuntimeMetrics()
        self.packet_queue: asyncio.Queue[CapturedPacket] = asyncio.Queue(maxsize=20000)
        self.clients: set[Any] = set()
        self.registry = MessageRegistry(bundle_root() / "data")
        self.name_index = load_name_index(config.name_index_path)
        self.multi_phase_dungeon_map = _load_multi_phase_dungeon_map(bundle_root() / "jsondata" / "Dungeon.json")
        self.private_key = load_private_key_from_txt(config.rsa_key_txt)
        self.srsa_bridge = SRSABridge(config.dll_dir)
        self.capture_manager = CaptureManager.create(config.npcap_device, self._on_packet_from_thread)
        self.pending_packets: dict[FlowKey, deque[CapturedPacket]] = defaultdict(lambda: deque(maxlen=8192))
        self.active_flow: FlowKey | None = None
        self.active_session: SessionPipeline | None = None
        self._active_flow_capture_locked = False
        self.batch: list[dict[str, object]] = []
        self.log_file = None
        self.current_log_path: Path | None = None
        self.log_write_errors = 0
        self.debug_session_dir: Path | None = None
        self.debug_counters: dict[tuple[str, str], int] = defaultdict(int)
        self.server = None
        if config.trace_enabled:
            if config.trace_file is None:
                config.trace_file = make_archive_trace_file(config.log_dir)
            self.trace_bridge = TraceBridge(config.trace_file)
        else:
            self.trace_bridge = None
        self.status_file = config.status_file
        if self.status_file is not None:
            self.status_file.parent.mkdir(parents=True, exist_ok=True)
            self._write_status()

    async def run(self) -> None:
        self.loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()
        self.server = await websockets.serve(self._ws_handler, "127.0.0.1", self.config.ws_port)
        self.capture_manager.start()
        self._set_state(ServiceState.WAITING_RESTART if self._game_running() else ServiceState.WAITING_GAME)
        tasks = [
            asyncio.create_task(self._process_monitor_loop(), name="process-monitor"),
            asyncio.create_task(self._packet_loop(), name="packet-loop"),
            asyncio.create_task(self._batch_flush_loop(), name="batch-flush"),
            asyncio.create_task(self._stats_loop(), name="stats-loop"),
        ]
        for task in tasks:
            task.add_done_callback(self._on_background_task_done)
        self._started_event.set()
        try:
            await self._stop_event.wait()
        finally:
            self._started_event.clear()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self._reset_session()
            self.capture_manager.stop()
            if self.server is not None:
                self.server.close()
                await self.server.wait_closed()
            if self.log_file is not None:
                self.log_file.close()
                self.log_file = None
            self.server = None
            self.loop = None
            self._stop_event = None
            if self._fatal_exception is not None:
                exc = self._fatal_exception
                self._fatal_exception = None
                raise exc

    def wait_started(self, timeout: float | None = None) -> bool:
        return self._started_event.wait(timeout)

    def request_stop(self) -> None:
        if self.loop is None or self._stop_event is None:
            return
        if self.loop.is_closed():
            return
        try:
            self.loop.call_soon_threadsafe(self._stop_event.set)
        except RuntimeError:
            return

    def _on_background_task_done(self, task: asyncio.Task[None]) -> None:
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            return
        if exc is None:
            return
        LOGGER.exception("background task failed", exc_info=exc)
        self._fatal_exception = exc
        self._fatal_error = {
            "task": task.get_name(),
            "type": type(exc).__name__,
            "message": str(exc),
        }
        # Persist the actual failure before stopping the heartbeat. The
        # overlay can then surface the cause instead of degrading it to the
        # ambiguous "capture disconnected" state.
        self._write_status()
        if self.loop is not None and self._stop_event is not None and not self._stop_event.is_set():
            if self.loop.is_closed():
                return
            try:
                self.loop.call_soon_threadsafe(self._stop_event.set)
            except RuntimeError:
                return

    def _on_packet_from_thread(self, packet: CapturedPacket) -> None:
        if self.loop is None:
            return
        self.loop.call_soon_threadsafe(self._queue_packet, packet)

    def _queue_packet(self, packet: CapturedPacket) -> None:
        self.metrics.packets_seen += 1
        try:
            self.packet_queue.put_nowait(packet)
        except asyncio.QueueFull:
            self.metrics.packets_dropped_queue += 1

    async def _ws_handler(self, websocket):
        self.clients.add(websocket)
        try:
            await websocket.send(json.dumps(self._hello_payload(), ensure_ascii=False))
            await websocket.wait_closed()
        finally:
            self.clients.discard(websocket)

    def _hello_payload(self) -> dict[str, object]:
        return {
            "type": "hello",
            "schema_version": 2,
            "service_version": "0.1.0",
            "state": self.state.value,
            "session_id": self.active_session.session_id if self.active_session else None,
            "metrics": asdict(self.metrics),
        }

    def _status_payload(self, pcap_stats: dict[str, int] | None = None) -> dict[str, object]:
        active_flow = None
        if self.active_flow is not None:
            active_flow = {
                "client": f"{self.active_flow.client.ip}:{self.active_flow.client.port}",
                "server": f"{self.active_flow.server.ip}:{self.active_flow.server.port}",
            }
        log_size = None
        if self.current_log_path is not None:
            try:
                log_size = self.current_log_path.stat().st_size
            except OSError:
                log_size = None
        return {
            "type": "status",
            "schema_version": 1,
            "state": self.state.value,
            "game_exe": self.config.game_exe,
            "session_id": self.active_session.session_id if self.active_session else None,
            "active_flow": active_flow,
            "session": self.active_session.status_snapshot() if self.active_session is not None else None,
            "log": {
                "dir": str(self.config.log_dir),
                "path": str(self.current_log_path) if self.current_log_path is not None else None,
                "size": log_size,
                "write_errors": self.log_write_errors,
            },
            "capture_devices": self.capture_manager.device_snapshot(),
            "metrics": asdict(self.metrics),
            "pcap_stats": pcap_stats or {},
            "fatal_error": self._fatal_error,
            "updated_at_ms": int(datetime.now().timestamp() * 1000),
        }

    def _write_status(self, pcap_stats: dict[str, int] | None = None) -> None:
        if self.status_file is None:
            return
        try:
            tmp_path = self.status_file.with_suffix(self.status_file.suffix + ".tmp")
            tmp_path.write_text(
                json.dumps(self._status_payload(pcap_stats), ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            tmp_path.replace(self.status_file)
        except OSError:
            LOGGER.debug("failed to write status file %s", self.status_file, exc_info=True)

    async def _broadcast(self, payload: dict[str, object]) -> None:
        if not self.clients:
            return
        message = json.dumps(payload, ensure_ascii=False)
        await asyncio.gather(*(client.send(message) for client in tuple(self.clients)), return_exceptions=True)

    def _set_state(self, state: ServiceState) -> None:
        if self.state == state:
            return
        self.state = state
        LOGGER.info("service state -> %s", state.value)
        self._write_status()
        if self.observer is not None and self.observer.on_state_change is not None:
            self.observer.on_state_change(
                self.state,
                self.active_session.session_id if self.active_session else None,
                self.active_flow,
            )
        if self.loop is not None:
            self.loop.create_task(self._broadcast(self._hello_payload()))

    def _game_running(self) -> bool:
        target = self.config.game_exe.lower()
        for process in psutil.process_iter(["name"]):
            if (process.info.get("name") or "").lower() == target:
                return True
        return False

    def _find_game_pids(self) -> set[int]:
        target = self.config.game_exe.lower()
        pids: set[int] = set()
        for process in psutil.process_iter(["name"]):
            if (process.info.get("name") or "").lower() == target:
                pids.add(int(process.pid))
        return pids

    def _find_active_flow(self, game_pids: set[int]) -> FlowKey | None:
        for conn in psutil.net_connections(kind="tcp"):
            if conn.pid not in game_pids:
                continue
            if not conn.laddr or not conn.raddr:
                continue
            if int(conn.raddr.port) != 30000:
                continue
            return FlowKey(
                client=Endpoint(str(conn.laddr.ip), int(conn.laddr.port)),
                server=Endpoint(str(conn.raddr.ip), int(conn.raddr.port)),
            )
        return None

    async def _process_monitor_loop(self) -> None:
        while True:
            await asyncio.sleep(0.5)
            game_pids = self._find_game_pids()
            if self.state == ServiceState.WAITING_RESTART:
                if not game_pids:
                    self._set_state(ServiceState.WAITING_GAME)
                continue
            if not game_pids:
                if self.active_flow is not None:
                    LOGGER.info("game process exited; clearing session")
                self._reset_session()
                self._set_state(ServiceState.WAITING_GAME)
                continue
            flow = self._find_active_flow(game_pids)
            if flow is None:
                if self.active_flow is not None:
                    LOGGER.info("game connection closed; returning to discovery capture")
                    self._reset_session()
                self._set_state(ServiceState.WAITING_CONNECTION)
                continue
            if self.active_flow != flow:
                self._activate_flow(flow, activation_reason=self._flow_activation_reason(flow))
            if self.active_session and self.active_session.is_live:
                self._lock_active_flow_capture_if_ready()
                self._set_state(ServiceState.LIVE)
            else:
                self._set_state(ServiceState.WAITING_HANDSHAKE)

    async def _packet_loop(self) -> None:
        while True:
            packet = await self.packet_queue.get()
            if self.state == ServiceState.WAITING_RESTART:
                continue
            flow = self._normalize_flow(packet)
            if flow is None:
                continue
            self.pending_packets[flow].append(packet)
            if self.active_flow == flow and self.active_session is not None:
                self.active_session.process_packet(packet)
                if self.active_session.is_live:
                    self._lock_active_flow_capture_if_ready()
                    self._set_state(ServiceState.LIVE)

            # Queue.get() does not yield when a backlog is already available.
            # Give status/monitor tasks a turn between complete TCP packets.
            await asyncio.sleep(0)

    @staticmethod
    def _normalize_flow(packet: CapturedPacket) -> FlowKey | None:
        if packet.src.port == 30000:
            return FlowKey(client=packet.dst, server=packet.src)
        if packet.dst.port == 30000:
            return FlowKey(client=packet.src, server=packet.dst)
        return None

    @staticmethod
    def _pending_sort_key(flow: FlowKey, packet: CapturedPacket) -> tuple[int, int, int]:
        direction_rank = 0 if packet.src == flow.client else 1
        return (direction_rank, int(packet.seq), int(packet.timestamp_ms))

    def _sorted_pending_packets(self, flow: FlowKey) -> list[CapturedPacket]:
        return sorted(
            list(self.pending_packets.get(flow, ())),
            key=lambda packet: self._pending_sort_key(flow, packet),
        )

    def _ready_to_activate_flow(self, flow: FlowKey) -> bool:
        pending = self._sorted_pending_packets(flow)
        if not pending:
            return False
        has_client_payload = any(packet.src == flow.client for packet in pending)
        has_server_payload = any(packet.src == flow.server for packet in pending)
        return has_client_payload and has_server_payload

    def _flow_activation_reason(self, flow: FlowKey) -> str:
        if self._ready_to_activate_flow(flow):
            return "buffered_bidirectional_payload"
        return "process_connection_fallback"

    def _lock_active_flow_capture_if_ready(self) -> None:
        if self.active_flow is None or self.active_session is None or not self.active_session.is_live:
            return
        if self._active_flow_capture_locked:
            return
        # Keep the original Npcap handles alive for the lifetime of the TCP
        # session. Reopening them after the encrypted handshake can lose one
        # server segment; TCP reassembly must then stop rather than fabricate
        # the missing bytes, which also hides the authoritative settlement.
        # The discovery filter is already narrow ("tcp port 30000").
        self._active_flow_capture_locked = True

    def _activate_flow(self, flow: FlowKey, *, activation_reason: str = "unknown") -> None:
        LOGGER.info(
            "activating flow %s:%d -> %s:%d reason=%s",
            flow.client.ip,
            flow.client.port,
            flow.server.ip,
            flow.server.port,
            activation_reason,
        )
        pending = self._sorted_pending_packets(flow)
        if self.active_flow is not None or self.active_session is not None:
            self._reset_session()
        self.active_flow = flow
        self._active_flow_capture_locked = False
        self.active_session = SessionPipeline(
            flow=flow,
            session_id=str(uuid.uuid4()),
            registry=self.registry,
            private_key=self.private_key,
            srsa_bridge=self.srsa_bridge,
            name_index=self.name_index,
            multi_phase_dungeon_map=self.multi_phase_dungeon_map,
            merge_multi_phase_enemy_battles=self.config.merge_multi_phase_enemy_battles,
            on_event=self._handle_outbound_event,
            on_battle_operation=getattr(getattr(self, "observer", None), "on_event", None),
            on_debug_message=self._handle_debug_message if self.config.debug_enabled else None,
            on_debug_record=self._handle_debug_record if self.config.debug_enabled else None,
            metrics=self.metrics,
        )
        self._open_log_file(self.active_session.session_id)
        self._open_debug_session_dir(self.active_session.session_id)
        if self.trace_bridge is not None:
            capture_start_ts_ms = min(
                (packet.timestamp_ms for packet in pending),
                default=None,
            )
            self.trace_bridge.begin_capture_session(timestamp_ms=capture_start_ts_ms)
        self._notify_capture_lifecycle("CAPTURE_SESSION_START")
        self._write_status()
        LOGGER.info("replaying %d buffered packets for active flow", len(pending))
        for packet in pending:
            self.active_session.process_packet(packet)
        self._lock_active_flow_capture_if_ready()

    def _open_log_file(self, session_id: str) -> None:
        if self.log_file is not None:
            self.log_file.close()
        timestamp = datetime.now().strftime("%Y%m%d")
        day_dir = self.config.log_dir / timestamp
        day_dir.mkdir(parents=True, exist_ok=True)
        file_name = f"session_{session_id}.ndjson"
        self.current_log_path = day_dir / file_name
        self.log_file = self.current_log_path.open("a", encoding="utf-8")

    def _open_debug_session_dir(self, session_id: str) -> None:
        self.debug_session_dir = None
        self.debug_counters.clear()
        if not self.config.debug_enabled:
            return
        timestamp = datetime.now().strftime("%Y%m%d")
        session_dir = self.config.debug_dir / timestamp / f"session_{session_id}"
        (session_dir / "proto" / "CS_BATTLE_OP").mkdir(parents=True, exist_ok=True)
        (session_dir / "proto" / "SC_SELF_SCENE_INFO").mkdir(parents=True, exist_ok=True)
        (session_dir / "proto" / "SC_SYNC_CHAR_BAG_INFO").mkdir(parents=True, exist_ok=True)
        (session_dir / "issues" / "frame_error").mkdir(parents=True, exist_ok=True)
        (session_dir / "issues" / "decompression_error").mkdir(parents=True, exist_ok=True)
        (session_dir / "issues" / "protobuf_error").mkdir(parents=True, exist_ok=True)
        (session_dir / "issues" / "session_head").mkdir(parents=True, exist_ok=True)
        (session_dir / "issues" / "undecoded_frame").mkdir(parents=True, exist_ok=True)
        (session_dir / "issues" / "tcp_gap").mkdir(parents=True, exist_ok=True)
        (session_dir / "issues" / "multipack_pending").mkdir(parents=True, exist_ok=True)
        (session_dir / "issues" / "multipack_incomplete").mkdir(parents=True, exist_ok=True)
        self.debug_session_dir = session_dir
        LOGGER.info("debug output -> %s", session_dir)

    def _reset_session(self) -> None:
        had_capture_session = self.active_flow is not None or self.active_session is not None
        if had_capture_session:
            self._notify_capture_lifecycle("CAPTURE_SESSION_END")
        if had_capture_session and self.trace_bridge is not None:
            self.trace_bridge.end_capture_session()
        if self.active_session is not None:
            self.active_session.flush_debug_state()
        self.active_flow = None
        self.active_session = None
        self._active_flow_capture_locked = False
        self.capture_manager.restore_default_filters()
        if self.log_file is not None:
            self.log_file.close()
            self.log_file = None
        self.current_log_path = None
        self.debug_session_dir = None
        self.debug_counters.clear()

    def _notify_capture_lifecycle(self, event_type: str) -> None:
        """Invalidate live subscriptions even when service state stays LIVE.

        This is a readonly observer side channel, not gameplay evidence.  It
        deliberately does not enter the legacy trace or alter battle timing.
        """
        observer = getattr(self, "observer", None)
        callback = getattr(observer, "on_event", None)
        if callback is None:
            return
        session = self.active_session
        callback({
            "type": event_type,
            "session_id": getattr(session, "session_id", None),
            "timestamp_ms": int(datetime.now().timestamp() * 1000),
        })

    def _handle_outbound_event(self, event: OutboundEvent) -> None:
        payload = event.as_dict()
        evidence_types = {
            "SC_CHAR_BAG_SET_CURR_TEAM_INDEX", "SC_CHAR_BAG_SET_CURR_TEAM_TYPE",
            "SC_CHAR_BAG_SET_MAX_TEAM_MEMBER_COUNT", "SC_CHAR_SKILL_INFOS",
            "SC_EQUIP_MEDICINE_MODIFY", "SC_EQUIP_PRODUCE",
            "BattleOpSwitchMode", "BattleOpResetBattleData",
            "BattleOpUpdateDataVersion", "BattleOpShieldValueModify",
        }
        if (self.trace_bridge is not None and payload.get("type") not in evidence_types
                and payload.get("type") not in {"SC_SYNC_CHAR_BAG_INFO", "SC_CHAR_BAG_SET_TEAM",
                                               "SC_CHAR_BAG_SET_TEAM_LEADER"}
                and not (payload.get("type") == "LOADOUT" and payload.get("reason") in evidence_types)):
            # Newly retained evidence must not advance the legacy last
            # gameplay timestamp or flush pending Buff/statistics groups.
            self.trace_bridge.handle_event(payload)
        self.batch.append(payload)
        if self.observer is not None and self.observer.on_event is not None:
            self.observer.on_event(payload)
        if self.log_file is not None:
            try:
                self.log_file.write(json.dumps(payload, ensure_ascii=False) + "\n")
                self.log_file.flush()
            except OSError:
                self.log_write_errors += 1
                LOGGER.warning("failed to write combat log %s", self.current_log_path, exc_info=True)
        if len(self.batch) >= 128 and self.loop is not None:
            self.loop.create_task(self._flush_batch())

    def _handle_debug_message(self, decoded: DecodedMessage, timestamp_ms: int) -> None:
        if self.debug_session_dir is None or self.active_session is None:
            return
        schema_files = {}
        pending_descriptors = [decoded.message.DESCRIPTOR.file]
        while pending_descriptors:
            descriptor = pending_descriptors.pop()
            if descriptor.name in schema_files:
                continue
            schema_files[descriptor.name] = hashlib.sha256(descriptor.serialized_pb).hexdigest()
            pending_descriptors.extend(descriptor.dependencies)
        payload = {
            "type": "debug_proto",
            "session_id": self.active_session.session_id,
            "timestamp_ms": timestamp_ms,
            "direction": decoded.direction,
            "class_name": decoded.class_name,
            "msg_id": decoded.msg_id,
            "head": decoded.head,
            # MessageToDict omits fields unknown to this schema. Preserve the
            # exact decoded body as well, so a newer schema can replay it.
            "raw_body_format": "protobuf_body_after_decompression_and_reassembly",
            "raw_body_size": len(decoded.raw_body),
            "raw_body_sha256": hashlib.sha256(decoded.raw_body).hexdigest(),
            "raw_body_base64": base64.b64encode(decoded.raw_body).decode('ascii'),
            "protobuf_schema_files_sha256": dict(sorted(schema_files.items())),
            "message": MessageToDict(
                decoded.message,
                preserving_proto_field_name=True,
                use_integers_for_enums=False,
                always_print_fields_with_no_presence=True,
            ),
            "message_enum_ints": MessageToDict(
                decoded.message,
                preserving_proto_field_name=True,
                use_integers_for_enums=True,
                always_print_fields_with_no_presence=True,
            ),
        }
        self._write_debug_json("proto", decoded.class_name, payload, timestamp_ms, decoded.direction)

    def _handle_debug_record(self, payload: dict[str, object]) -> None:
        if self.debug_session_dir is None:
            return
        record_type = str(payload.get("type", "record"))
        if record_type == "debug_frame_error":
            category = "frame_error"
        elif record_type == "debug_decompression_error":
            category = "decompression_error"
        elif record_type == "debug_protobuf_error":
            category = "protobuf_error"
        elif record_type == "debug_session_head":
            category = "session_head"
        elif record_type == "debug_tcp_gap":
            category = "tcp_gap"
        elif record_type == "debug_multipack_pending":
            category = "multipack_pending"
        elif record_type == "debug_multipack_incomplete":
            category = "multipack_incomplete"
        else:
            category = "undecoded_frame"
        direction = str(payload.get("direction", "na"))
        timestamp_ms = int(payload.get("timestamp_ms", 0))
        self._write_debug_json("issues", category, payload, timestamp_ms, direction)

    def _write_debug_json(
        self,
        top_level: str,
        bucket: str,
        payload: dict[str, object],
        timestamp_ms: int,
        direction: str,
    ) -> None:
        if self.debug_session_dir is None:
            return
        key = (top_level, bucket)
        self.debug_counters[key] += 1
        index = self.debug_counters[key]
        target_dir = self.debug_session_dir / top_level / bucket
        target_dir.mkdir(parents=True, exist_ok=True)
        file_name = f"{index:06d}_{timestamp_ms}_{direction}.json"
        path = target_dir / file_name
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    async def _batch_flush_loop(self) -> None:
        while True:
            await asyncio.sleep(0.1)
            await self._flush_batch()

    async def _flush_batch(self) -> None:
        if not self.batch:
            return
        payload = {
            "type": "event_batch",
            "session_id": self.active_session.session_id if self.active_session else None,
            "sent_at_ms": int(datetime.now().timestamp() * 1000),
            "events": self.batch[:128],
        }
        del self.batch[:128]
        self.metrics.ws_batches_sent += 1
        await self._broadcast(payload)

    async def _stats_loop(self) -> None:
        while True:
            await asyncio.sleep(5)
            stats = self.capture_manager.stats_snapshot()
            self._write_status(dict(stats))
            if self.observer is not None and self.observer.on_runtime_metrics is not None:
                self.observer.on_runtime_metrics(replace(self.metrics), dict(stats), self.active_flow)
            LOGGER.info(
                "metrics packets=%d queue_drop=%d frames=%d messages=%d events=%d batches=%d ps_drop=%d active_flow=%s",
                self.metrics.packets_seen,
                self.metrics.packets_dropped_queue,
                self.metrics.frames_decoded,
                self.metrics.messages_decoded,
                self.metrics.outbound_events_emitted,
                self.metrics.ws_batches_sent,
                stats["ps_drop"],
                self.active_flow,
            )
