"""Small, read-only projections for custom overlay pages.

One process-wide broker consumes the existing service observer.  It never reads
capture files, starts a collector, or exposes the service's diagnostic payload.
An observed list is not a complete list of effects present in the game.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from functools import lru_cache
import hashlib
import importlib
import json
import logging
import math
import os
import re
import threading
import time
from typing import Any, Callable

LOGGER = logging.getLogger(__name__)
_RUNTIME_TOPICS = ("effects.actual.v1", "targets.current.v1", "capture.context.v1", "effects.operations.v1", "game.events.v1")
_TOPICS = (*_RUNTIME_TOPICS, "effects.catalog.v1", "effects.definitions.v1")
_RESET_EVENTS = {"SC_ENTER_DUNGEON",
                 "SC_GAME_MECHANICS_SYNC_RESTART_GAME_INST", "SC_GAME_MECHANICS_SYNC_LEAVE_GAME_INST"}

# These are existing service observer events. Prefixes, packet wrappers,
# authentication events and diagnostic events are deliberately not a contract.
_GAME_EVENT_TYPES = frozenset({
    "LOADOUT", "DUNGEON_CONTEXT", "CONTRACT_TAGS", "GAME_CLOCK_TICK", "squad_update", "damage",
    "SC_SYNC_CHAR_BAG_INFO", "CS_ENTER_DUNGEON", "CS_SCENE_LOAD_FINISH", "CS_SCENE_SET_BATTLE",
    "CS_CONDITIONAL_MULTI_STAGE_ACTIVITY_GAIN_REWARD", "CS_GAME_MECHANICS_NTF_INST_PREPARE_FINISH",
    "CS_GAME_MECHANICS_REQ_START", "CS_GAME_MECHANICS_REQ_STOP",
    "SC_SELF_SCENE_INFO", "SC_SELF_SCENE_INFO_SKILLS", "SC_OBJECT_ENTER_VIEW", "SC_OBJECT_LEAVE_VIEW",
    "SC_SYNC_ATTR", "SC_EFFECT_SNAPSHOT", "SC_ATTACH_SERVER_SKILL", "SC_UPDATE_SERVER_SKILL",
    "SC_DETACH_SERVER_SKILL", "SC_CHAR_BAG_SET_TEAM", "SC_CHAR_BAG_SET_CURR_TEAM_INDEX",
    "SC_CHAR_BAG_SET_TEAM_LEADER",
    "SC_CHAR_BAG_SET_CURR_TEAM_TYPE", "SC_CHAR_BAG_SET_MAX_TEAM_MEMBER_COUNT", "SC_CHAR_SKILL_INFOS",
    "SC_EQUIP_MEDICINE_MODIFY", "SC_EQUIP_PRODUCE", "SC_ADD_SERVER_BUFF", "SC_REMOVE_SERVER_BUFF",
    "SC_BATTLE_ADD_GLOBAL_BUFF", "SC_BATTLE_REMOVE_GLOBAL_BUFF", "SC_BATTLE_SYNC_GLOBAL_BUFF_INFO",
    "SC_CHAR_BAG_SYNC_PERSISTENT_BUFFS", "SC_BATTLE_GENERATION_CHANGE", "SC_BATTLE_SYNC_SEQ_ID",
    "SC_ENTER_DUNGEON", "SC_RESET_BATTLE_STATUS", "SC_SCENE_RESET_ENTITY", "SC_SCENE_SET_BATTLE",
    "SC_SYNC_RACING_DUNGEON_BUFF_MODIFY", "SC_SYNC_RACING_DUNGEON_RECONNECT", "SC_SYNC_FULL_DUNGEON_STATUS",
    "SC_SYNC_WEEK_RAID_SETTLEMENT", "SC_GAME_MECHANICS_MODIFY_INST_TIME_FREEZE",
    "SC_GAME_MECHANICS_SYNC_ENTER_GAME_INST", "SC_GAME_MECHANICS_SYNC_LEAVE_GAME_INST",
    "SC_GAME_MECHANICS_SYNC_RESTART_GAME_INST", "SC_GAME_MECHANICS_SYNC_CHALLENGE_START",
    "SC_GAME_MECHANICS_SYNC_CHALLENGE_COMPLETE", "SC_GAME_MECHANICS_SYNC_COMPLETION_REWARD",
    "SC_ACTIVITY_CONDITIONAL_MULTI_STAGE_BASE_CHANGE", "SC_ACTIVITY_CONDITIONAL_STAGE_PROGRESS_CHANGE",
    "SC_ACTIVITY_PROGRESS_CHANGE", "SC_CONDITIONAL_MULTI_STAGE_ACTIVITY_GAIN_REWARD",
    "CS_PING", "SC_PING", "CS_GAME_TIME_FREEZE_START_REQ", "CS_GAME_TIME_FREEZE_END_REQ",
    "SC_GAME_TIME_FREEZE_START_RSP", "SC_GAME_TIME_FREEZE_END_RSP",
    "BattleOpSkillAttach", "BattleOpSkillDetach", "BattleOpSkillStartCast", "BattleOpSkillEndCast",
    "BattleOpTriggerAction", "BattleOpTriggerKeyword", "BattleOpSetBuffEnableState", "BattleOpFinishBuff",
    "BattleOpAddBuff", "BattleOpEnablePassiveSkill", "BattleOpEntityDie", "BattleOpEntityValueModify",
    "BattleOpModifyBattleState", "BattleOpModifyPoiseValue", "BattleOpUpdateAtbInfo", "BattleOpSwitchMode",
    "BattleOpResetBattleData", "BattleOpUpdateDataVersion", "BattleOpShieldValueModify",
})
_PRIVATE_EVENT_FIELDS = frozenset({
    "raw_body_hex", "raw_body", "body_hex", "packet_hex",
    "raw_packet", "raw_bytes", "bytes", "hex", "session_key", "session_keys", "private_key", "public_key",
    "password", "passwd", "token", "access_token", "refresh_token", "authorization", "cookie",
    "account", "account_id", "account_email", "account_token", "email", "login", "login_token", "secret", "secrets",
    "capture_path", "flow", "raw_log", "private_nickname", "diagnostic", "diagnostics", "raw_body_len",
})


def _public_json(value: Any, *, depth: int = 0, budget: list[int] | None = None,
                 removed: list[int] | None = None) -> Any:
    """Bounded copy of an allowlisted observer event, without packet bytes."""
    budget = [131_072] if budget is None else budget
    budget[0] -= 1
    if depth > 24 or budget[0] < 0:
        raise ValueError("event payload depth or node limit")
    if value is None or type(value) is bool:
        return value
    if type(value) is int:
        return str(value) if abs(value) > 2 ** 53 - 1 else value
    if type(value) is float and math.isfinite(value):
        return value
    if isinstance(value, str) and len(value) <= 16_384:
        return value
    if isinstance(value, list) and len(value) <= 1024:
        return [_public_json(item, depth=depth + 1, budget=budget, removed=removed) for item in value]
    if isinstance(value, dict) and len(value) <= 1024:
        result = {}
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > 256:
                raise ValueError("event payload map key invalid")
            # Only observer envelope additions are redacted. A nested game
            # blackboard is a map of literal parameter names; e.g. "token"
            # is a valid variable key, not evidence of authentication data.
            if depth == 0 and key.casefold() in _PRIVATE_EVENT_FIELDS:
                if removed is not None:
                    removed[0] += 1
                continue
            result[key] = _public_json(item, depth=depth + 1, budget=budget, removed=removed)
        return result
    raise ValueError("event payload value unavailable or exceeds limit")


@lru_cache(maxsize=None)
def _proto_descriptor(name: str) -> Any:
    # Generated descriptor identity is authoritative; imported once, never a
    # per-operation MessageToDict/descriptor graph reconstruction.
    module = importlib.import_module(f".proto_generated.{name}_pb2", __package__)
    return getattr(module, name).DESCRIPTOR


def _copy_proto_message(value: Any, descriptor: Any, *, depth: int = 0,
                        budget: list[int] | None = None) -> Any:
    budget = [8192] if budget is None else budget
    budget[0] -= 1
    if depth > 24 or budget[0] < 0 or not isinstance(value, dict):
        raise ValueError("typed operation depth or node limit")
    result = {}
    for name, item in value.items():
        field = descriptor.fields_by_name.get(name)
        if field is None:
            # The current generated descriptor is the explicit field
            # allowlist. Observer/log additions are not gameplay fields.
            continue
        def scalar(raw: Any, field: Any) -> Any:
            budget[0] -= 1
            if budget[0] < 0:
                raise ValueError("typed operation node limit")
            if field.type == 11:
                return _copy_proto_message(raw, field.message_type, depth=depth + 1, budget=budget)
            if field.type == 8 and type(raw) is bool:
                return raw
            if field.type == 9 and isinstance(raw, str) and len(raw) <= 8192:
                return raw
            if field.type == 14 and (type(raw) is int or isinstance(raw, str)
                                     and raw in field.enum_type.values_by_name):
                return raw
            if field.type in (1, 2) and _number(raw) is not None:
                return raw
            if field.type in (3, 4, 6, 16, 18):
                if type(raw) is int or isinstance(raw, str) and re.fullmatch(r"-?[0-9]{1,20}", raw):
                    number = int(raw)
                    if (0 <= number < 2 ** 64 if field.type in (4, 6) else -(2 ** 63) <= number < 2 ** 63):
                        return str(number)
            if field.type in (5, 7, 13, 15, 17) and type(raw) is int:
                if (0 <= raw < 2 ** 32 if field.type in (7, 13) else -(2 ** 31) <= raw < 2 ** 31):
                    return raw
            raise ValueError("typed operation scalar invalid or bytes excluded")
        if field.is_repeated and field.message_type is not None and field.message_type.GetOptions().map_entry:
            if not isinstance(item, dict) or len(item) > 256:
                raise ValueError("typed operation map exceeds limit")
            if any(not isinstance(key, str) or len(key) > 256 for key in item):
                raise ValueError("typed operation map key invalid")
            result[name] = {key: scalar(child, field.message_type.fields_by_name["value"])
                            for key, child in item.items()}
        elif field.is_repeated:
            if not isinstance(item, list) or len(item) > 256:
                raise ValueError("typed operation array exceeds limit")
            result[name] = [scalar(child, field) for child in item]
        else:
            result[name] = scalar(item, field)
    return result



@lru_cache(maxsize=1)
def _operation_schemas() -> dict[str, Any]:
    scalar = {"target_key": "text", "input_value_key": "text", "use_direct_value": "bool",
              "numeric_value": "number", "string_value": "text"}
    blackboard = {"blackboard": ("map", {"str_value": "text", "float_value": "number", "is_dynamic": "bool"})}
    template = {"type": "enum", "str_id": "text", "int_id": "number"}
    schemas = {
        "BattleOpSkillAttach": {"src_inst_id": "id", "skill_inst_id": "id", "skill_lv": "number",
                                "skill_source": "enum", "template_type": "enum", "template_int_id": "number",
                                "template_str_id": "text"},
        "BattleOpSkillDetach": {"skill_inst_id": "id"},
        "BattleOpTriggerAction": {
            **{name: "id" for name in ("operation_owner_id", "trigger_owner_id", "owner_id", "trigger_inst_id", "inst_id")},
            **{name: "number" for name in ("generation", "trigger_type", "template_int_id")},
            **{name: "enum" for name in ("owner_type", "template_type")},
            **{name: "text" for name in ("template_str_id", "template_id_case",
                                       "action_runtime_case", "action_serialization")},
            "template_id": template,
            "action": {"action_type": "enum", "action_id": "number",
                       "create_buff_action": {"details": [{"source_id": "id", "target_id": "id", "buff_inst_id": "id",
                                                             "buff_input_index": "number", "buff_num_id": "number", "buff_id": "text",
                                                             "assigned_items": [scalar]}]},
                       "finish_buff_action": {"finish_buffs": [{"target_id": "id", "buff_inst_id": "id"}],
                                              "finish_layer_cnt": "number"}}},
        "BattleOpAddBuff": {"buff_inst_id": "id", "target_inst_id": "id", "src_inst_id": "id",
                            "operation_owner_id": "id", "generation": "number", "int_id": "number", "str_id": "text",
                            "buff_template_id": template, "buff_template_id_case": "text", "assigned_items": blackboard},
        "BattleOpFinishBuff": {"buff_inst_id": "id", "operation_owner_id": "id", "generation": "number",
                               "reduce_layer": "number", "force": "bool"},
        "BattleOpSetBuffEnableState": {"owner_id": "id", "generation": "number", "states": ("map", "bool")},
        "BattleOpTriggerKeyword": {"owner_id": "id", "generation": "number", "buff_inst_id": "id", "add_buff_num_id": "number",
                                   "modify": [{"config_index": "number", "modify_value": "number"}]},
    }
    schemas["BattleOpTriggerAction"]["action"] = ("proto", "BATTLE_ACTION_INST")
    schemas["BattleOpTriggerAction"]["operation_payload_case"] = "text"
    schemas["BattleOpTriggerAction"]["client_tick_tms"] = "wire_number"
    for kind, message in {
        "BattleOpSwitchMode": "BATTLE_SWITCH_MODE_OP_DATA",
        "BattleOpResetBattleData": "BATTLE_RESET_BATTLE_DATA_OP_DATA",
        "BattleOpUpdateDataVersion": "BATTLE_UPDATE_DATA_VERSION_OP_DATA",
        "BattleOpShieldValueModify": "BATTLE_SHIELD_VALUE_MODIFY_OP_DATA",
    }.items():
        descriptor = _proto_descriptor(message)
        schemas[kind] = {"owner_id": "id", "operation_owner_id": "id", "generation": "number",
                         "seq_id": "number", "client_tick_tms": "wire_number", "operation_payload_case": "text",
                         "op_type": "enum", "operation_data_serialization": "text",
                         "raw_data": ("proto", message),
                         **{field.name: ("proto_field", message, field.name) for field in descriptor.fields}}
    return schemas


def _identity(value: Any) -> str | None:
    """Keep uint64 identities as strings; do not turn bools/floats into IDs."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value) if 0 < value < 2 ** 64 else None
    if isinstance(value, str) and value and len(value) <= 256:
        if value.isdecimal():
            try:
                return str(int(value)) if 0 < int(value) < 2 ** 64 else None
            except ValueError:
                return None
        return value
    return None


def _number(value: Any) -> int | float | None:
    return value if type(value) in (int, float) and math.isfinite(value) else None


def _wire_number(value: Any) -> int | float | str | None:
    # protobuf JSON uses decimal strings for int64, including duration. Keep
    # its exact representation without claiming any unit or clock meaning.
    if isinstance(value, str) and re.fullmatch(r"-?[0-9]{1,20}", value):
        return value
    return _number(value)


def _template(value: Any) -> str | None:
    if isinstance(value, dict):
        # TEMPLATE_ID is a oneof: these are exact wire identities, not names.
        for key in ("str_id", "int_id"):
            if key in value:
                return _template(value[key])
        return None
    if type(value) is int and -(2 ** 31) <= value < 0:
        return str(value)
    return _identity(value)


@dataclass
class _Subscription:
    topic: str
    options: dict[str, Any]
    callback: Callable[[dict[str, Any]], None]
    lock: threading.RLock = field(default_factory=threading.RLock)
    active: bool = True
    last_sequence: int = -1

    def deliver(self, envelope: dict[str, Any], *, isolated: bool = False) -> None:
        with self.lock:
            if not self.active:
                return
            if envelope["sequence"] < self.last_sequence:
                return
            self.last_sequence = envelope["sequence"]
            try:
                self.callback(envelope if isolated else deepcopy(envelope))
            except Exception:
                LOGGER.warning("overlay subscriber callback failed", exc_info=True)


class OverlayTopicBroker:
    """Thread-safe bounded observed state, shared by every overlay window."""

    def __init__(self, *, catalog: list[dict[str, Any]] | None = None,
                 definitions: list[dict[str, Any]] | None = None,
                 definitions_loader: Callable[[], list[dict[str, Any]]] | None = None,
                 target_catalog: dict[str, dict[str, Any]] | None = None,
                 native_contract: dict[str, Any] | None = None,
                 installed_hashes: dict[str, str] | None = None,
                 publish_interval_ms: int = 100, max_effects: int = 4096,
                 max_closed: int = 256, max_operations: int = 256, max_events: int = 256,
                 clock: Callable[[], float] = time.time) -> None:
        self._lock = threading.RLock()
        self._subscriptions: list[_Subscription] = []
        self._clock = clock
        self._interval = max(0, publish_interval_ms) / 1000
        self._timer: threading.Timer | None = None
        self._dirty: set[str] = set()
        self._seq = 0
        self._generation = 0
        self._session: str | None = None
        self._live = False
        self._fresh = False
        self._reason = "capture_not_connected"
        self._observed_at: int | None = None
        self._effects: dict[str, dict[str, Any]] = {}
        self._client_uid_keys: dict[str, set[str]] = {}
        self._needs_closed_trim = False
        self._targets: dict[str, dict[str, Any]] = {}
        self._target_generations: dict[str, int] = {}
        self._object_to_battle: dict[str, str] = {}
        self._controlled: str | None = None
        self._scene_identity: tuple[Any, Any] | None = None
        self._max_effects = max(1, max_effects)
        self._max_closed = max(0, max_closed)
        self._omitted_closed = 0
        self._omitted_open = 0
        self._unresolved_owner_serial = 0
        self._stacking_groups: dict[str, dict[str, Any]] = {}
        self._unresolved: dict[str, dict[str, Any]] = {}
        self._operations: list[dict[str, Any]] = []
        self._game_events: list[dict[str, Any]] = []
        self._max_game_events = max(1, min(256, max_events))
        self._game_event_index = 0
        self._game_event_dropped = 0
        self._game_event_payload_omitted = 0
        self._game_event_bytes = 0
        self._max_operations = max(1, min(256, max_operations))
        self._operation_index = 0
        self._operation_previous_seq: int | None = None
        self._operation_gap_revision = 0
        self._operation_last_gap_index: int | None = None
        self._operation_dropped = 0
        self._operation_payload_omitted = 0
        self._operation_last_payload_omission_index: int | None = None
        self._operation_source_mode: str | None = None
        self._operation_stream_revision = 0
        self._operation_content_revision = 0
        self._operation_sequence_basis: str | None = None
        self._operation_source_previous_index: int | None = None
        self._operation_raw_metadata_valid = False
        self._operation_integrity_baseline: dict[str, int] | None = None
        self._operation_integrity_current: dict[str, int] | None = None
        self._operation_integrity_state = "unavailable"
        self._operation_integrity_revision = 0
        self._operation_integrity_after_index: int | None = None
        self._operation_integrity_changed_fields: list[str] = []
        self._target_context_revisions: dict[str, dict[str, int]] = {}
        self._target_context_revision_serial = 0
        self._target_context_revision_overflow = 0
        self._target_context_revision_overflow_after_index: int | None = None
        self._target_context_revision_overflow_stream_revision: int | None = None
        self._catalog: list[dict[str, Any]] = []
        self._catalog_icons: dict[str, str] = {}
        self._definitions: list[dict[str, Any]] = []
        self._definition_ids: set[str] = set()
        self._definitions_loader = definitions_loader
        self._definitions_state = "unloaded" if definitions_loader is not None else "unavailable"
        self._definitions_reason: str | None = "definition_catalog_unavailable"
        self._definitions_complete = threading.Event()
        self._metadata: dict[str, dict[str, Any]] = {}
        self._target_catalog = deepcopy(target_catalog or {})
        self._native_binding = {
            name: (value.lower() if isinstance(value := (installed_hashes or {}).get(name), str)
                   and re.fullmatch(r"[a-fA-F0-9]{64}", value) else None)
            for name in ("gameAssemblySha256", "metadataSha256")}
        self._client_finish_exact, self._client_finish_reason = self._qualify_lifecycle_capability(
            native_contract, installed_hashes or {}, "clientFinishExact")
        self._client_enable_exact, self._client_enable_reason = self._qualify_lifecycle_capability(
            native_contract, installed_hashes or {}, "clientEnableStateExact")
        self._client_action_finish_exact, self._client_action_finish_reason = self._qualify_lifecycle_capability(
            native_contract, installed_hashes or {}, "clientActionFinishExact")
        self._client_keyword_variables_exact, self._client_keyword_variables_reason = self._qualify_lifecycle_capability(
            native_contract, installed_hashes or {}, "clientKeywordVariablesExact")
        self._entity_generation_exact, self._entity_generation_reason = self._qualify_lifecycle_capability(
            native_contract, installed_hashes or {}, "entityGenerationExact")
        self._client_operation_sequence_exact, self._client_operation_sequence_reason = self._qualify_lifecycle_capability(
            native_contract, installed_hashes or {}, "clientOperationSequenceExact")
        self._set_catalog(catalog or [], definitions=definitions)

    @staticmethod
    def _qualify_lifecycle_capability(contract: Any, installed_hashes: dict[str, str],
                                      name: str) -> tuple[bool, str | None]:
        if not isinstance(contract, dict) or type(contract.get("schemaVersion")) is not int or contract["schemaVersion"] != 1:
            return False, "native_contract_unavailable"
        hashes = contract.get("hashes")
        if not isinstance(hashes, dict):
            return False, "native_version_unverified"
        for key in ("gameAssemblySha256", "metadataSha256"):
            expected, actual = hashes.get(key), installed_hashes.get(key)
            if (not isinstance(expected, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", expected)
                    or not isinstance(actual, str) or actual.lower() != expected.lower()):
                return False, "native_version_unverified"
        capabilities = contract.get("capabilities")
        capability = capabilities.get(name) if isinstance(capabilities, dict) else None
        required = {
            "clientFinishExact": {"namespace": "client_buff", "uidRequiredMask": "0x2000000000000000",
                                  "targetField": "operation_owner_id", "generationField": "generation",
                                  "matchScope": "same_capture_scene_target_uid", "closureReason": "client-finish-confirmed"},
            "clientEnableStateExact": {"namespace": "client_buff", "uidRequiredMask": "0x2000000000000000",
                                       "targetField": "owner_id", "statesField": "states", "generationField": "generation",
                                       "matchScope": "same_capture_scene_target_uid"},
            "clientActionFinishExact": {"namespace": "client_buff", "uidRequiredMask": "0x2000000000000000",
                                        "actionType": 3, "actionRuntimeCase": "finish_buff_action", "triggerType": 0,
                                        "targetField": "target_id", "uidField": "buff_inst_id",
                                        "matchScope": "same_capture_scene_target_uid",
                                        "closureReason": "client-action-finish-confirmed"},
            "clientKeywordVariablesExact": {"namespace": "client_buff", "uidRequiredMask": "0x2000000000000000",
                                            "targetField": "owner_id", "uidField": "buff_inst_id", "updatesField": "modify",
                                            "configIndexField": "config_index", "valueField": "modify_value", "variableKey": "rate",
                                            "valueSemantics": "absolute_after_assignment", "indexSemantics": "zero_based_keyword_enhance_list",
                                            "matchScope": "same_capture_scene_target_uid"},
            "clientOperationSequenceExact": {"namespace": "client_battle_operation", "sequenceField": "seq_id",
                                             "sequenceType": "int32", "sequenceScope": "same_gameplay_network_all_owners",
                                             "matchScope": "same_capture_scene_context", "increment": 1,
                                             "wireSequenceDensity": "sparse"},
            "entityGenerationExact": {"namespace": "scene_object", "targetField": "inst_id", "generationField": "generation",
                                      "matchScope": "same_capture_scene_explicit_object_battle_relation"}}[name]
        if (not isinstance(capability, dict) or capability.get("supported") is not True
                or any(capability.get(key) != value for key, value in required.items())
                or (name == "clientFinishExact" and (type(capability.get("reduceLayer")) is not int
                    or capability["reduceLayer"] != 1 or capability.get("force") is not True))
                or (name == "clientActionFinishExact" and any(type(capability.get(key)) is not int for key in ("actionType", "triggerType")))
                or (name == "clientOperationSequenceExact" and type(capability.get("increment")) is not int)
                or not isinstance(capability.get("proofSha256"), str)
                or not re.fullmatch(r"[a-fA-F0-9]{64}", capability["proofSha256"])):
            return False, {"clientFinishExact": "native_finish_case_unverified",
                           "clientEnableStateExact": "native_enable_state_relation_unverified",
                           "clientActionFinishExact": "native_action_finish_case_unverified",
                           "clientKeywordVariablesExact": "native_keyword_variable_relation_unverified",
                           "clientOperationSequenceExact": "native_operation_sequence_unverified",
                           "entityGenerationExact": "native_generation_relation_unverified"}[name]
        # The contract's legacy defaultBranchOnly flag concerns server-owner
        # mapping. It is not the independent, exact client-finish capability.
        return True, None

    def configure_catalog(self, catalog: list[dict[str, Any]],
                          target_catalog: dict[str, dict[str, Any]] | None = None, *,
                          definitions: list[dict[str, Any]] | None = None) -> None:
        """Trusted host metadata injection, never part of the page bridge."""
        with self._lock:
            self._catalog, self._metadata, self._catalog_icons = [], {}, {}
            self._set_catalog(catalog, definitions=definitions)
            if target_catalog is not None:
                self._target_catalog = deepcopy(target_catalog)
            for row in self._effects.values():
                metadata = self._effect_metadata(row["effectId"], row["namespace"],
                                                  row["raw"].get("templateType"),
                                                  row["raw"].get("templateIdCase"),
                                                  row["raw"].get("identitySourceContext"))
                row.update(canonicalId=metadata.get("canonicalId"), label=metadata.get("label"),
                           description=metadata.get("description"), labelSource=metadata.get("labelSource"),
                           descriptionSource=metadata.get("descriptionSource"),
                           classification=metadata.get("classification", "unclassified"),
                           hasIcon=metadata.get("hasIcon"), uiGroup=metadata.get("uiGroup"),
                            iconPresentation=metadata.get("iconPresentation"),
                           uiGroupUsesNumericPriority=metadata.get("uiGroupUsesNumericPriority"),
                           classificationSource=metadata.get("classificationSource"))
            self._dirty.update(("effects.catalog.v1", "effects.definitions.v1", "effects.actual.v1"))
        self.flush()

    def _set_catalog(self, rows: list[dict[str, Any]], *,
                     definitions: list[dict[str, Any]] | None = None, definitions_owned: bool = False) -> None:
        if definitions is not None:
            # Definitions are trusted, packaged static metadata, never page
            # input. Their full descriptions are delivered only in pages.
            valid_definitions = [row for row in definitions if isinstance(row, dict)
                                 and _identity(row.get("effectId") or row.get("canonicalId"))]
            self._definitions = valid_definitions if definitions_owned else deepcopy(valid_definitions)
            self._definition_ids = {row.get("canonicalId") or row.get("effectId") for row in self._definitions}
            self._definitions_loader = None
            self._definitions_state, self._definitions_reason = "ready", None
            self._definitions_complete.set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            canonical = _identity(row.get("canonicalId") or row.get("effectId") or row.get("id"))
            if canonical is None:
                continue
            label = row.get("label") if isinstance(row.get("label"), str) and len(row["label"]) <= 256 else None
            classification = row.get("classification")
            if classification not in {"buff", "debuff"}:
                classification = "unclassified"
            safe = {"effectId": canonical, "canonicalId": canonical, "label": label,
                    "labelSource": "official-config-summary" if row.get("labelSource") == "official-config-summary" else None,
                    "classification": classification, "hasIcon": row.get("hasIcon") is True,
                    "iconPresentation": (row.get("iconPresentation")
                                         if isinstance(row.get("iconPresentation"), str)
                                         and row["iconPresentation"] in {"image", "missing-asset", "skill-button-progress"} else None),
                    "uiGroup": row.get("uiGroup") if isinstance(row.get("uiGroup"), str) and len(row["uiGroup"]) <= 64 else None,
                    "uiGroupUsesNumericPriority": (row.get("uiGroupUsesNumericPriority")
                                                   if type(row.get("uiGroupUsesNumericPriority")) is bool else None),
                    "classificationSource": ("official-icon-priority-group"
                                             if row.get("classificationSource") == "official-icon-priority-group" else None),
                    "numericIds": [identity for raw in (row.get("numericIds") or [])
                                   if (identity := _identity(raw)) is not None]}
            icon = row.get("icon")
            icon = (icon if isinstance(icon, str) and len(icon) <= 100_000
                    and re.fullmatch(r"data:image/(?:png|webp);base64,[A-Za-z0-9+/=]+", icon) else None)
            icon_key = hashlib.sha256(icon.encode("ascii")).hexdigest() if icon else None
            if icon_key:
                self._catalog_icons[icon_key] = icon
            # v1 keeps the legacy nullable icon field, while new pages resolve
            # iconKey in the shared data.icons dictionary. Repeated data URIs
            # must not exhaust the 2 MiB per-window bridge limit.
            safe.update(icon=None, iconKey=icon_key)
            self._catalog.append(safe)
            # The all-effects directory carries short labels only. Description
            # is projected for matched runtime effects, never repeated across
            # the complete static catalog sent to every page.
            detail = {**safe, "icon": icon, "description": (row.get("description") if isinstance(row.get("description"), str)
                                             and len(row["description"]) <= 800 else None),
                      "descriptionSource": ("official-config-summary"
                                            if row.get("descriptionSource") == "official-config-summary" else None)}
            for identity in (canonical, *safe["numericIds"]):
                # An ambiguous numeric relation is not usable as an alias.
                previous = self._metadata.get(identity)
                if previous == {}:
                    continue
                if previous is not None and previous["canonicalId"] != canonical:
                    self._metadata[identity] = {}
                else:
                    self._metadata[identity] = detail
        icon_keys = {row["canonicalId"]: row["iconKey"] for row in self._catalog}
        for definition in self._definitions:
            canonical = definition.get("canonicalId") or definition.get("effectId")
            if canonical in icon_keys:
                definition["iconKey"] = icon_keys[canonical]

    def _effect_metadata(self, effect_id: str, namespace: str, template_type: Any,
                         id_case: str | None, source_context: str | None = None) -> dict[str, Any]:
        if namespace not in {"client_buff", "server_buff"}:
            return {}
        typed_buff = template_type == "Buff" or (type(template_type) is int and template_type == 1)
        if template_type is not None and not typed_buff:
            return {}
        metadata = self._metadata.get(effect_id, {})
        if (namespace == "client_buff" and id_case == "direct_numeric"
                and source_context == "protobuf-create-buff-detail"):
            # BATTLE_ACTION_INST.create_buff_action -> RUNTIME.details ->
            # DETAIL.buff_num_id is an explicitly typed Buff field. Its source
            # is retained separately; do not invent a wire TEMPLATE_ID.type.
            return metadata
        if id_case == "int_id":
            # Exact enum Buff=1 plus current official buff_id dictionary is
            # the typed numeric relation; outer namespaces alone do not prove it.
            return metadata if typed_buff else {}
        if id_case in {"str_id", "direct_string"} and metadata.get("canonicalId") == effect_id:
            return metadata
        return {}

    def get_topics(self) -> list[dict[str, Any]]:
        return [{"topic": topic, "schemaVersion": 1, "readOnly": True,
                 "description": {"effects.actual.v1": "准确观测到的效果实例；缺失不代表不存在",
                                 "targets.current.v1": "准确观测到的场景目标",
                                 "capture.context.v1": "采集连接与上下文",
                                 "effects.operations.v1": "有界规范化操作观测流；连续序号不保证全游戏完整性",
                                 "game.events.v1": "白名单游戏观察事件流；事件不代表完整当前状态",
                                 "effects.catalog.v1": "本机官方资源中的全部效果目录",
                                 "effects.definitions.v1": "可检索分页的官方静态效果定义；配置不代表当前实例值"}[topic],
                 "capabilities": {"readOnly": True, "editing": False,
                                  "pagination": topic in {"effects.definitions.v1", "effects.actual.v1"},
                                  "fieldAvailability": topic == "effects.actual.v1",
                                  "runtimeTimeValues": "unsupported-until-clock-relation-verified" if topic == "effects.actual.v1" else None,
                                  "filters": (["eventTypes", "limit"] if topic == "game.events.v1" else
                                              ["limit"] if topic == "effects.operations.v1" else
                                              ["query", "sourceTypes", "sourceIds", "effectIds", "limit", "offset"]
                                              if topic == "effects.definitions.v1" else
                                              ["target", "targetIds", "characterIds", "effectIds", "hideUnknown", "onlyGameIcons",
                                               *(["limit", "offset"] if topic == "effects.actual.v1" else [])])}}
                for topic in _TOPICS]

    def subscribe(self, topic: str, options: dict[str, Any] | None = None,
                  callback: Callable[[dict[str, Any]], None] | None = None) -> Callable[[], None]:
        if topic not in _TOPICS:
            raise ValueError("unsupported overlay topic")
        if not callable(callback):
            raise TypeError("callback is required")
        subscription = _Subscription(topic, self._options(options, topic=topic), callback)
        if topic == "effects.definitions.v1":
            self._ensure_definitions(wait=False)
        # Reserve this delivery lock before registering so a producer cannot
        # deliver a change ahead of the subscriber's initial snapshot.
        with subscription.lock:
            with self._lock:
                self._subscriptions.append(subscription)
                initial = self._build(topic, subscription.options)
            subscription.deliver(initial, isolated=True)

        def unsubscribe() -> None:
            with subscription.lock:
                subscription.active = False
            with self._lock:
                if subscription in self._subscriptions:
                    self._subscriptions.remove(subscription)
        return unsubscribe

    def inspect(self, topic: str | None = None, options: dict[str, Any] | None = None) -> Any:
        normalized = self._options(options, topic=topic)
        if topic is not None and topic not in _TOPICS:
            raise ValueError("unsupported overlay topic")
        if topic in {None, "effects.definitions.v1"}:
            self._ensure_definitions(wait=True)
        with self._lock:
            # _build owns its complete result: each mutable state row has
            # already been detached, so another whole-state copy is redundant.
            return (self._build(topic, normalized) if topic else
                    {name: self._build(name, normalized) for name in _TOPICS})

    def _ensure_definitions(self, *, wait: bool) -> None:
        """Coordinate one shared load without holding the capture state lock."""
        with self._lock:
            if self._definitions_state in {"ready", "unavailable"}:
                return
            loader = self._definitions_loader
            self._definitions_loader = None
            complete = self._definitions_complete
            if loader is not None:
                self._definitions_state, self._definitions_reason = "loading", "definitions_loading"
                complete.clear()
        if loader is not None:
            if wait:
                self._load_definitions(loader, complete)
            else:
                threading.Thread(target=self._load_definitions, args=(loader, complete),
                                 name="overlay-effect-definitions", daemon=True).start()
        elif wait:
            complete.wait()

    def _load_definitions(self, loader: Callable[[], list[dict[str, Any]]], complete: threading.Event) -> None:
        try:
            definitions = loader()
        except (OSError, ValueError, TypeError, RecursionError):
            LOGGER.warning("overlay effect definitions unavailable", exc_info=True)
            definitions = []
        with self._lock:
            # An explicit host reconfiguration supersedes an outstanding load.
            if self._definitions_state != "loading" or complete is not self._definitions_complete:
                complete.set()
                return
            if definitions:
                self._set_catalog([], definitions=definitions, definitions_owned=True)
            else:
                self._definitions_state, self._definitions_reason = "unavailable", "definition_catalog_unavailable"
            complete.set()
            self._dirty.update(("effects.definitions.v1", "effects.actual.v1"))
        self.flush()

    @staticmethod
    def _options(options: Any, *, topic: str | None = None) -> dict[str, Any]:
        if options is None:
            options = {}
        if not isinstance(options, dict):
            raise ValueError("subscription options must be an object")
        raw = options.get("filter", options)
        if not isinstance(raw, dict):
            raise ValueError("filter must be an object")
        if topic == "game.events.v1":
            if set(raw) - {"eventTypes", "limit"}:
                raise ValueError("unsupported game event filter")
            kinds = raw.get("eventTypes", [])
            if (not isinstance(kinds, list) or len(kinds) > 256
                    or any(not isinstance(kind, str) or kind not in _GAME_EVENT_TYPES for kind in kinds)):
                raise ValueError("unsupported game event type")
            limit = raw.get("limit", 64)
            if type(limit) is not int or not 1 <= limit <= 256:
                raise ValueError("invalid game event limit")
            return {"eventTypes": frozenset(kinds), "limit": limit}
        if topic == "effects.operations.v1":
            if set(raw) - {"limit"}:
                raise ValueError("operation observations must not be filtered")
            limit = raw.get("limit", 256)
            if type(limit) is not int or not 1 <= limit <= 256:
                raise ValueError("invalid operation observation limit")
            return {"limit": limit}
        selection = raw.get("target", "all")
        if selection not in {"all", "party", "controlled", "enemy", "boss", "character"}:
            raise ValueError("unsupported target filter")
        out: dict[str, Any] = {"target": selection, "hideUnknown": raw.get("hideUnknown") is True,
                               "onlyGameIcons": raw.get("onlyGameIcons") is True}
        for name in ("targetIds", "characterIds", "effectIds", "sourceTypes", "sourceIds"):
            items = raw.get(name, [])
            if not isinstance(items, list) or len(items) > (256 if name in {"sourceTypes", "sourceIds"} else 4096):
                raise ValueError("invalid identity filter")
            identities = [_identity(item) for item in items]
            if any(item is None for item in identities):
                raise ValueError("invalid identity filter")
            out[name] = frozenset(identities)
        query = raw.get("query", "")
        if not isinstance(query, str) or len(query) > 256:
            raise ValueError("invalid definition query")
        out["query"] = query.strip().casefold()
        for name, default, lower, upper in (("limit", 25 if topic == "effects.definitions.v1" else None,
                                             1, 4096 if topic == "effects.actual.v1" else 100),
                                            ("offset", 0, 0, 1_000_000)):
            value = raw.get(name, default)
            if name == "limit" and name not in raw and value is None:
                out[name] = None
                continue
            if type(value) is not int or not lower <= value <= upper:
                raise ValueError("invalid definition pagination")
            out[name] = value
        return out

    def _context(self) -> dict[str, Any]:
        return {"captureSessionId": self._session, "generation": self._generation}

    def _selection(self, options: dict[str, Any]) -> tuple[set[str] | None, str | None]:
        explicit = options.get("targetIds")
        if explicit:
            return set(explicit), None
        selection = options.get("target", "all")
        if selection == "all":
            return None, None
        if selection == "controlled":
            return ({self._controlled}, None) if self._controlled else (set(), "controlled_target_unverified")
        if selection in {"enemy", "boss"}:
            field = "isEnemy" if selection == "enemy" else "isBoss"
            known = [row for row in self._targets.values() if row.get(field) is not None]
            if not known:
                return set(), "enemy_identity_unverified" if selection == "enemy" else "boss_identity_unverified"
            return {row["id"] for row in known if row[field] is True}, None
        if selection == "party":
            return {row["id"] for row in self._targets.values() if row["kind"] == "party"}, None
        chars = options.get("characterIds", ())
        return {row["id"] for row in self._targets.values() if row.get("templateId") in chars}, None

    def _build(self, topic: str, options: dict[str, Any]) -> dict[str, Any]:
        envelope = {"topic": topic, "schemaVersion": 1, "sequence": self._seq,
                    "receivedAtMs": int(self._clock() * 1000), "state": "ready", "reason": None, "data": None}
        lifecycle_evidence = {
            "clientFinishExact": {"state": "ready" if self._client_finish_exact else "unavailable",
                                  "reason": self._client_finish_reason},
            "clientEnableStateExact": {"state": "ready" if self._client_enable_exact else "unavailable",
                                       "reason": self._client_enable_reason},
            "clientActionFinishExact": {"state": "ready" if self._client_action_finish_exact else "unavailable",
                                        "reason": self._client_action_finish_reason},
            "clientKeywordVariablesExact": {"state": "ready" if self._client_keyword_variables_exact else "unavailable",
                                            "reason": self._client_keyword_variables_reason},
            "clientOperationSequenceExact": {"state": "ready" if self._client_operation_sequence_exact else "unavailable",
                                             "reason": self._client_operation_sequence_reason},
            "entityGenerationExact": {"state": "ready" if self._entity_generation_exact else "unavailable",
                                      "reason": self._entity_generation_reason}}
        if topic == "capture.context.v1":
            envelope["lifecycleEvidence"] = lifecycle_evidence
        if topic == "game.events.v1":
            if not self._live or not self._game_events:
                envelope.update(state="unavailable", reason=self._reason or "awaiting_game_events")
                return envelope
            selected = options.get("eventTypes", ())
            matching = [row for row in self._game_events if not selected or row["type"] in selected]
            rows = []
            data = {"context": self._context(), "coverage": "observed-only", "completeness": "partial",
                    "scope": "allowlisted-service-observer-events", "absence": "unknown",
                    "valueSemantics": "normalized-observations-not-full-current-state",
                    "integerEncoding": "decimal-string-for-integers-outside-javascript-safe-range",
                    "payloadEncoding": "existing-observer-json-no-protobuf-reserialization",
                    "excludedFields": "packet-bytes-authentication-and-diagnostic-fields",
                    "excludedFieldScope": "observer-payload-top-level-only-not-game-parameter-map-keys",
                    "allowedEventTypes": sorted(_GAME_EVENT_TYPES), "events": rows,
                    "firstIndex": None, "lastIndex": None,
                    "bufferFirstIndex": self._game_events[0]["index"], "bufferLastIndex": self._game_events[-1]["index"],
                    "lastObservedIndex": self._game_event_index, "retainedCount": len(self._game_events),
                    "matchingEventCount": len(matching), "deliveredEventCount": 0,
                    "bufferDroppedCount": self._game_event_dropped,
                    "payloadOmittedCount": self._game_event_payload_omitted,
                    "projectionOmittedEventCount": len(matching), "projectionReason": None,
                    "projectionByteLimit": 1_500_000, "eventPayloadByteLimit": 1_000_000,
                    "payloadLimits": {"maxDepth": 24, "maxNodes": 131_072, "maxArrayOrMapEntries": 1024,
                                      "maxStringCharacters": 16_384, "maxMapKeyCharacters": 256},
                    "eventIndexScope": "current-capture-context-all-allowlisted-event-types"}
            envelope["data"] = data
            used = len(json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) + 1024
            budget_limited = False
            for row in reversed(matching[-(options.get("limit") or 64):]):
                addition = len(json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) + bool(rows)
                if used + addition > 1_500_000:
                    budget_limited = True
                    break
                rows.append(deepcopy(row))
                used += addition
            rows.reverse()
            data.update(firstIndex=rows[0]["index"] if rows else None,
                        lastIndex=rows[-1]["index"] if rows else None,
                        deliveredEventCount=len(rows), projectionOmittedEventCount=len(matching) - len(rows),
                        projectionReason="payload_budget" if budget_limited else
                                         "limit" if len(rows) < len(matching) else None)
            return envelope
        if topic == "effects.operations.v1":
            if not self._live or not self._operations:
                envelope.update(state="unavailable", reason=self._reason or "awaiting_operation_observations")
                return envelope
            limit = options.get("limit") or 256
            operations = []
            data = {"context": self._context(), "coverage": "observed-only", "completeness": "partial",
                    "nativeBinding": dict(self._native_binding),
                    "scope": "observed-client-operations", "absence": "unknown",
                    "sourceMode": self._operation_source_mode, "streamRevision": self._operation_stream_revision,
                    "contentRevision": self._operation_content_revision,
                    "targetContextRevisions": deepcopy(self._target_context_revisions),
                    "targetContextRevisionOverflow": self._target_context_revision_overflow,
                    "targetContextRevisionOverflowAfterIndex": self._target_context_revision_overflow_after_index,
                    "targetContextRevisionOverflowStreamRevision": self._target_context_revision_overflow_stream_revision,
                    "targetContextRevisionMapComplete": self._target_context_revision_overflow == 0,
                    "targetContextRevisionScope": "current-operation-stream",
                    "operations": operations, "firstIndex": None, "lastIndex": None,
                    "bufferFirstIndex": self._operations[0]["index"], "bufferLastIndex": self._operations[-1]["index"],
                    "lastObservedIndex": self._operation_index, "retainedCount": len(self._operations),
                    "deliveredOperationCount": 0, "bufferDroppedCount": self._operation_dropped,
                    "projectionOmittedOperationCount": len(self._operations), "omittedBeforeCount": self._operation_index,
                    "payloadOmittedCount": self._operation_payload_omitted,
                    "pendingPayloadCount": sum(row["payloadState"] == "pending" for row in self._operations),
                    "lastPayloadOmissionIndex": self._operation_last_payload_omission_index,
                    "projectionReason": None, "projectionByteLimit": 1_500_000,
                    "operationPayloadByteLimit": 32_000,
                    "payloadSchema": "current-generated-descriptor-and-explicit-observer-fields",
                    "unlistedFieldPolicy": "exclude-fields-absent-from-current-descriptor-or-explicit-schema",
                    "gapRevision": self._operation_gap_revision,
                    "captureIntegrity": {"state": self._operation_integrity_state,
                                         "baseline": deepcopy(self._operation_integrity_baseline),
                                         "current": deepcopy(self._operation_integrity_current),
                                         "changedFields": list(self._operation_integrity_changed_fields),
                                         "revision": self._operation_integrity_revision,
                                         "afterIndex": self._operation_integrity_after_index,
                                         "scope": "tracked-capture-counters-not-complete-game-state"},
                    "continuity": {"state": ("unavailable" if not self._client_operation_sequence_exact else
                                              "unavailable" if self._operation_sequence_basis == "source-index-and-capture-integrity"
                                              and (not self._operation_raw_metadata_valid or self._operation_integrity_state != "stable") else
                                              "gap" if self._operation_gap_revision else "contiguous"),
                                   "reason": (self._client_operation_sequence_reason if not self._client_operation_sequence_exact else
                                              "source_operation_metadata_or_integrity_unavailable" if self._operation_sequence_basis == "source-index-and-capture-integrity"
                                              and (not self._operation_raw_metadata_valid or self._operation_integrity_state != "stable") else
                                              "observed_sequence_discontinuity" if self._operation_gap_revision else None),
                                   "basis": self._operation_sequence_basis,
                                   "payloadState": ("pending" if any(row["payloadState"] == "pending" for row in self._operations) else
                                                    "omitted" if any(row["payloadState"] == "omitted" for row in self._operations) else "complete"),
                                   "scope": "observed-operation-sequence-since-first-record",
                                   "lastGapIndex": self._operation_last_gap_index,
                                   "lastSeqId": self._operation_previous_seq,
                                   "lastSourceIndex": self._operation_source_previous_index,
                                   "initialGameState": "unknown"},
                    "lifecycleEvidence": {"clientOperationSequenceExact": lifecycle_evidence["clientOperationSequenceExact"]}}
            envelope["data"] = data
            payload_bytes = len(json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) + 1024
            budget_limited = False
            for operation in reversed(self._operations[-limit:]):
                addition = len(json.dumps(operation, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) + bool(operations)
                if payload_bytes + addition > 1_500_000:
                    budget_limited = True
                    break
                operations.append(deepcopy(operation))
                payload_bytes += addition
            operations.reverse()
            data.update(firstIndex=operations[0]["index"] if operations else None,
                        lastIndex=operations[-1]["index"] if operations else None,
                        deliveredOperationCount=len(operations),
                        projectionOmittedOperationCount=len(self._operations) - len(operations),
                        omittedBeforeCount=(operations[0]["index"] - 1 if operations else self._operation_index),
                        projectionReason="payload_budget" if budget_limited else
                                         "limit" if len(operations) < len(self._operations) else None)
            return envelope
        if topic == "effects.definitions.v1":
            if self._definitions_state != "ready":
                envelope.update(state="unavailable", reason=self._definitions_reason)
                return envelope
            effect_ids, source_types, source_ids = (options.get(name, ()) for name in ("effectIds", "sourceTypes", "sourceIds"))
            query = options.get("query", "")
            matched = []
            for row in self._definitions:
                if effect_ids and row.get("effectId", row.get("canonicalId")) not in effect_ids and row.get("canonicalId") not in effect_ids:
                    continue
                relations = row.get("sourceRelations", [])
                relations = [relation for relation in relations if isinstance(relation, dict)] if isinstance(relations, list) else []
                # Both filters must name the same explicit source relation.
                if (source_types or source_ids) and not any(
                        (not source_types or relation.get("sourceType") in source_types)
                        and (not source_ids or relation.get("sourceId") in source_ids) for relation in relations):
                    continue
                if query:
                    searchable = [row.get(name) for name in ("effectId", "canonicalId", "label", "description")]
                    searchable.extend(relation.get(name) for relation in relations
                                      for name in ("sourceId", "sourceName", "skillId", "skillName", "skillDescription"))
                    if query not in "\n".join(value for value in searchable if isinstance(value, str)).casefold():
                        continue
                matched.append(row)
            offset, limit = options.get("offset", 0), options.get("limit") or 25
            delivered, icons = [], {}
            # Leave space for the bridge envelope and messages queued by the
            # host. A page may be shorter than limit because of large assets.
            payload_bytes = 0
            for row in matched[offset:offset + limit]:
                detail = dict(row)
                if len(effect_ids) != 1:
                    detail.pop("details", None)
                    relations = detail.get("sourceRelations")
                    if isinstance(relations, list) and len(relations) > 32:
                        detail["sourceRelations"] = relations[:32]
                        detail.update(sourceRelationsTotal=len(relations), sourceRelationsTruncated=True)
                icon_key = detail.get("iconKey")
                icon = self._catalog_icons.get(icon_key)
                addition = len(json.dumps(detail, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
                if icon and icon_key not in icons:
                    addition += len(icon.encode("utf-8")) + len(icon_key) + 6
                if payload_bytes + addition > 1_500_000:
                    break
                delivered.append(deepcopy(detail))
                if icon:
                    icons[icon_key] = icon
                payload_bytes += addition
            next_offset = offset + len(delivered)
            envelope["data"] = {"definitions": delivered, "icons": icons, "total": len(matched),
                                "offset": offset, "limit": limit,
                                "nextOffset": next_offset if next_offset < len(matched) and delivered else None,
                                "hasMore": next_offset < len(matched), "scope": "static-configuration",
                                "runtimeValues": "not_provided", "paginationReason": (
                                    "payload_budget" if len(delivered) < min(limit, max(0, len(matched) - offset)) else None)}
            return envelope
        if topic == "effects.catalog.v1":
            envelope["data"] = {"effects": deepcopy(self._catalog), "icons": dict(self._catalog_icons),
                                "classificationCoverage": "unclassified_when_unverified"}
            return envelope
        if not self._live or not self._fresh:
            envelope.update(state="unavailable", reason=self._reason)
            return envelope
        selected, error = self._selection(options)
        common = {"context": self._context(), "observedThroughAtMs": self._observed_at,
                  "selectionState": "unavailable" if error else "ready", "selectionReason": error}
        if topic == "capture.context.v1":
            envelope["data"] = {"context": self._context(), "connection": "live",
                                "observedThroughAtMs": self._observed_at, "nativeBinding": dict(self._native_binding)}
        elif topic == "targets.current.v1":
            envelope["data"] = {**common, "controlledTargetId": self._controlled,
                                "targets": [deepcopy(row) for row in self._targets.values()
                                            if selected is None or row["id"] in selected]}
        else:
            effect_ids = options.get("effectIds", ())
            effects = []
            matching = []
            scope_count = game_icon_filtered_count = 0
            for row in self._effects.values():
                if selected is not None and row["targetId"] not in selected:
                    continue
                if effect_ids and row["effectId"] not in effect_ids and row["canonicalId"] not in effect_ids:
                    continue
                scope_count += 1
                if options.get("onlyGameIcons") and row.get("hasIcon") is not True:
                    game_icon_filtered_count += 1
                    continue
                if options.get("hideUnknown") and row["canonicalId"] is None:
                    continue
                matching.append(row)
            # Pagination is deterministic within this snapshot: retain
            # insertion order inside the open and historical partitions.
            matching = ([row for row in matching if row["state"] == "observed-open"]
                        + [row for row in matching if row["state"] != "observed-open"])
            offset, limit = options.get("offset", 0), options.get("limit")
            matching_open = sum(row["state"] == "observed-open" for row in matching)
            envelope["data"] = {**common, "coverage": "observed-only", "completeness": "partial",
                                "absence": "unknown", "clock": "capture-unix-ms", "effects": effects,
                                "scopeEffectCount": scope_count, "gameIconFilteredCount": game_icon_filtered_count,
                                "matchingEffectCount": len(matching), "deliveredEffectCount": 0,
                                "offset": offset, "limit": limit, "hasMore": False, "nextOffset": None,
                                "projectionReason": None, "projectionByteLimit": 1_500_000,
                                "projectionOmittedOpenCount": matching_open,
                                "projectionOmittedClosedCount": len(matching) - matching_open,
                                "storageOmittedOpenCount": self._omitted_open,
                                "storageOmittedClosedCount": self._omitted_closed,
                                "omittedOpenCount": self._omitted_open + matching_open,
                                "omittedClosedCount": self._omitted_closed + len(matching) - matching_open,
                                "lifecycleEvidence": lifecycle_evidence, "stackingGroups": [], "unresolvedObservations": [],
                                "omittedStackingGroupCount": 0, "omittedUnresolvedObservationCount": 0}
            # Serialize each candidate once, with a fixed reserve for final
            # counters and the host's outer snapshot wrapper. Never serialize
            # the growing list on each append or copy rows outside this page.
            payload_bytes = len(json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) + 1024
            budget_limited = False
            page = matching[offset:offset + limit if limit is not None else None]
            delivered_open = 0
            for row in page:
                projected = deepcopy(row)
                availability = {}
                for name in ("canonicalId", "label", "description", "labelSource", "descriptionSource", "sourceId", "targetId",
                             "hasIcon", "uiGroup", "iconPresentation", "uiGroupUsesNumericPriority", "classificationSource"):
                    value = row.get(name)
                    availability[name] = {"state": "known" if value is not None else "unknown",
                                          "source": ("official-static-definition" if name not in {"sourceId", "targetId"} else "observed-packet")
                                                    if value is not None else None,
                                          "reason": None if value is not None else (
                                              "explicit_relation_not_observed" if name in {"sourceId", "targetId"} else "official_definition_unavailable")}
                for name in ("instanceId", "slotId"):
                    supported = ((row["namespace"] in {"client_buff", "server_buff"}) == (name == "instanceId"))
                    availability[name] = {"state": "known" if row.get(name) is not None else "unknown" if supported else "unsupported",
                                          "source": "observed-packet" if row.get(name) is not None else None,
                                          "reason": None if row.get(name) is not None else "identity_not_observed" if supported else "namespace_uses_other_identity_field"}
                availability["classification"] = {"state": "known" if row.get("classificationSource") else "unknown",
                                                  "source": row.get("classificationSource"),
                                                  "reason": None if row.get("classificationSource") else "official_ui_classification_unverified"}
                for name in ("finishedAtMs", "removedAtMs", "closureReason"):
                    availability[name] = {"state": "known" if row.get(name) is not None else "unknown",
                                          "source": ("observed-lifecycle-relation" if name == "closureReason" else "capture-event-timestamp") if row.get(name) is not None else None,
                                          "reason": None if row.get(name) is not None else "lifecycle_transition_not_observed"}
                for name in ("stackCount", "durationMs", "expiresAtMs", "remainingMs", "enabled"):
                    value = row.get(name)
                    state = "known" if value is not None else "unsupported" if name in {"stackCount", "durationMs", "expiresAtMs", "remainingMs"} else "unknown"
                    availability[name] = {"state": state, "source": "observed-packet" if value is not None else None,
                                          "reason": None if value is not None else {
                                              "stackCount": "stack_count_semantics_unverified",
                                              "durationMs": "wire_duration_unit_unverified",
                                              "expiresAtMs": "duration_clock_anchor_unverified",
                                              "remainingMs": "duration_clock_anchor_unverified",
                                              "enabled": "enable_state_not_observed"}[name]}
                variables = row.get("runtimeVariables")
                availability["variables"] = {"state": "known" if variables is not None else "unknown", "source": "native-client-keyword-variable" if variables is not None else None,
                                              "reason": None if variables is not None else "runtime_variable_update_not_observed"}
                assigned = row["raw"].get("assignedItems")
                assigned_source = row["raw"].get("assignedItemsSource")
                initial_assignments = deepcopy(assigned)
                if isinstance(initial_assignments, list):
                    for item in initial_assignments:
                        if isinstance(item, dict) and item.get("use_direct_value") is not True and item.get("input_value_key"):
                            # The decoder includes proto3 implicit defaults.
                            # An indirect assignment names an input; its unused
                            # literal zero/empty fields are not observed values.
                            item.pop("numeric_value", None)
                            item.pop("string_value", None)
                assigned_field_sources = [
                    {"index": index, "fields": {name: f"{assigned_source}[{index}].{name}" for name in item},
                     "numericValueUnit": None, "numericValueUnitReason": "wire_unit_unverified",
                     "resolvedValueState": "unknown" if item.get("use_direct_value") is not True and item.get("input_value_key") else "not_provided",
                     "resolvedValueReason": "assignment_input_value_not_observed" if item.get("use_direct_value") is not True and item.get("input_value_key") else "initial_instruction_is_not_current_value"}
                    for index, item in enumerate(initial_assignments or []) if isinstance(item, dict)] if isinstance(initial_assignments, list) else [
                    {"key": key, "fields": {name: f"{assigned_source}.blackboard[{json.dumps(key)}].{name}" for name in item},
                     "numericValueUnit": None, "numericValueUnitReason": "wire_unit_unverified"}
                    for key, item in (assigned.get("blackboard", {}) if isinstance(assigned, dict)
                                      and isinstance(assigned.get("blackboard"), dict) else {}).items() if isinstance(item, dict)]
                projected["initializationParameters"] = {
                    "assignedItems": initial_assignments, "rawSource": "raw.assignedItems",
                    "buffInputIndex": row["raw"].get("buffInputIndex"),
                    "assignedItemsFormat": ("BATTLE_BLACKBOARD_ASSIGN_ITEM[]" if isinstance(assigned, list) else
                                            "BATTLE_BLACKBOARD" if isinstance(assigned, dict) else None),
                    "assignedItemFieldSources": assigned_field_sources,
                    "fieldAvailability": {
                        "assignedItems": {"state": "known" if assigned is not None else "unknown",
                                          "source": assigned_source if assigned is not None else None,
                                          "reason": None if assigned is not None else "initialization_parameters_not_observed"},
                        "buffInputIndex": {"state": "known" if row["raw"].get("buffInputIndex") is not None else "unknown",
                                           "source": row["raw"].get("buffInputIndexSource"),
                                           "reason": None if row["raw"].get("buffInputIndex") is not None else "initialization_parameter_not_observed"}},
                    "semantics": "initial-assignment-instructions", "currentValues": False}
                projected["fieldAvailability"] = availability
                projected["runtimeValues"] = {name: row.get(name) for name in ("stackCount", "durationMs", "expiresAtMs", "remainingMs", "enabled")}
                projected["runtimeValues"]["variables"] = deepcopy(variables)
                projected["wireValues"] = {
                    "duration": {"value": row["raw"].get("duration"), "unit": None,
                                 "source": row["raw"].get("durationSource"),
                                 "state": "known" if row["raw"].get("duration") is not None else "unknown",
                                 "reason": "wire_unit_unverified" if row["raw"].get("duration") is not None else "wire_value_not_observed"},
                    "blackboard": {"value": deepcopy(row["raw"].get("blackboard")),
                                   "source": row["raw"].get("blackboardSource"),
                                   "state": "known" if row["raw"].get("blackboard") is not None else "unknown",
                                   "reason": None if row["raw"].get("blackboard") is not None else "wire_value_not_observed"}}
                projected["lifecycle"] = {name: row.get(name) for name in ("state", "observationKind", "closureReason", "finishedAtMs", "removedAtMs")}
                projected["configuration"] = {"definitionId": row.get("canonicalId"), "topic": "effects.definitions.v1",
                                                "scope": "static-configuration",
                                                "state": "known" if row.get("canonicalId") in self._definition_ids else "unknown",
                                                "reason": (None if row.get("canonicalId") in self._definition_ids else
                                                           "definitions_not_loaded" if self._definitions_state == "unloaded" else
                                                           "definitions_loading" if self._definitions_state == "loading" else
                                                           "definition_catalog_unavailable" if self._definitions_state == "unavailable" else
                                                           "official_definition_unavailable")}
                addition = len(json.dumps(projected, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) + bool(effects)
                if payload_bytes + addition > 1_500_000:
                    budget_limited = True
                    break
                effects.append(projected)
                payload_bytes += addition
                delivered_open += row["state"] == "observed-open"
            data = envelope["data"]
            for name, rows, omitted_name in (("stackingGroups", self._stacking_groups.values(), "omittedStackingGroupCount"),
                                             ("unresolvedObservations", self._unresolved.values(), "omittedUnresolvedObservationCount")):
                for row in rows:
                    if selected is not None and row["targetId"] not in selected:
                        continue
                    addition = len(json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) + bool(data[name])
                    if payload_bytes + addition > 1_500_000:
                        data[omitted_name] += 1
                        budget_limited = True
                        continue
                    data[name].append(deepcopy(row))
                    payload_bytes += addition
            omitted_open = matching_open - delivered_open
            omitted_closed = len(matching) - matching_open - (len(effects) - delivered_open)
            next_offset = offset + len(effects)
            data.update(deliveredEffectCount=len(effects), projectionOmittedOpenCount=omitted_open,
                        projectionOmittedClosedCount=omitted_closed,
                        omittedOpenCount=self._omitted_open + omitted_open,
                        omittedClosedCount=self._omitted_closed + omitted_closed,
                        hasMore=next_offset < len(matching),
                        nextOffset=next_offset if next_offset < len(matching) and effects else None,
                        projectionReason="payload_budget" if budget_limited else
                                         "pagination" if omitted_open or omitted_closed else None)
            # A final linear verification also covers future additions to the
            # envelope. The byte reserve above keeps this a single check.
            if len(json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > 1_500_000:
                raise ValueError("overlay actual projection exceeded its declared byte budget")
        return envelope

    def _reset(self, reason: str, *, session: str | None = None) -> None:
        self._generation += 1
        self._session = session
        self._fresh = False
        self._reason = reason
        self._observed_at = None
        self._effects.clear()
        self._client_uid_keys.clear()
        self._needs_closed_trim = False
        self._targets.clear()
        self._target_generations.clear()
        self._object_to_battle.clear()
        self._stacking_groups.clear()
        self._unresolved.clear()
        self._operations.clear()
        self._game_events.clear()
        self._game_event_index = 0
        self._game_event_dropped = 0
        self._game_event_payload_omitted = 0
        self._game_event_bytes = 0
        self._operation_index = 0
        self._operation_previous_seq = None
        self._operation_gap_revision = 0
        self._operation_last_gap_index = None
        self._operation_dropped = 0
        self._operation_payload_omitted = 0
        self._operation_last_payload_omission_index = None
        self._operation_source_mode = None
        self._operation_stream_revision = 0
        self._operation_content_revision = 0
        self._operation_sequence_basis = None
        self._operation_source_previous_index = None
        self._operation_raw_metadata_valid = False
        self._operation_integrity_baseline = None
        self._operation_integrity_current = None
        self._operation_integrity_state = "unavailable"
        self._operation_integrity_revision = 0
        self._operation_integrity_after_index = None
        self._operation_integrity_changed_fields = []
        self._target_context_revisions.clear()
        self._target_context_revision_serial = 0
        self._target_context_revision_overflow = 0
        self._target_context_revision_overflow_after_index = None
        self._target_context_revision_overflow_stream_revision = None
        self._controlled = None
        self._scene_identity = None
        self._omitted_closed = self._omitted_open = 0
        self._dirty.update(_RUNTIME_TOPICS)

    def on_state_change(self, state: Any, session_id: str | None = None, flow: Any = None) -> None:
        del flow  # Never expose IP addresses, ports or capture internals.
        value = getattr(state, "value", state)
        live = str(value).lower() == "live"
        with self._lock:
            if not live or not self._live or session_id != self._session:
                self._reset("awaiting_fresh_capture_data" if live else "capture_disconnected", session=session_id)
            self._live = live
        self.flush()

    def on_event(self, event: dict[str, Any]) -> None:
        if not isinstance(event, dict):
            return
        kind = event.get("type")
        force = False
        with self._lock:
            session = event.get("session_id")
            session = session if isinstance(session, str) else self._session
            if (kind != "CAPTURE_SESSION_START" and self._session is not None
                    and session is not None and session != self._session):
                # Session END and scene/reset events need the same ownership
                # gate as ordinary observations. Only an explicit START is
                # authoritative evidence of a handoff to a different session.
                return
            if kind == "CAPTURE_SESSION_END":
                self._live = False
                self._reset("capture_disconnected", session=session)
                force = True
            elif kind == "CAPTURE_SESSION_START":
                self._reset("awaiting_fresh_capture_data", session=session)
                self._live = True
                force = True
            elif kind in _RESET_EVENTS:
                self._reset("awaiting_fresh_context_data", session=session)
                if self._live:
                    at = event.get("timestamp_ms")
                    at = int(at) if type(at) in (int, float) and math.isfinite(at) else int(self._clock() * 1000)
                    self._record_game_event(event, at)
                force = True
            else:
                if not self._live:
                    return
                if self._session is None:
                    self._session = session
                elif session is not None and session != self._session:
                    # Late delivery from a closed capture must not revive it.
                    return
                at = event.get("timestamp_ms")
                at = int(at) if type(at) in (int, float) and math.isfinite(at) else int(self._clock() * 1000)
                if (isinstance(kind, str) and (kind.startswith("BattleOp") or kind == "CAPTURE_BATTLE_OPERATION") and len(kind) <= 128
                        and type(event.get("seq_id")) is int and -(2 ** 31) <= event["seq_id"] < 2 ** 31):
                    self._record_operation(event, at)
                try:
                    changed = self._apply(event, at)
                except (TypeError, ValueError, OverflowError):
                    LOGGER.warning("ignored malformed overlay observation type=%r", kind)
                    changed = False
                self._record_game_event(event, at)
                if changed:
                    self._fresh = True
                    self._reason = None
                    self._observed_at = max(at, self._observed_at or at)
                    self._trim()
                    self._dirty.update(_RUNTIME_TOPICS)
        if force or self._interval == 0:
            self.flush()
        else:
            self._schedule()

    def _record_operation(self, event: dict[str, Any], at: int) -> None:
        """Retain only public game-operation fields, never the event/log body."""
        schemas = _operation_schemas()

        def copy_fields(value: Any, schema: Any) -> Any:
            if value is None:
                return None
            if isinstance(schema, dict):
                if not isinstance(value, dict):
                    raise ValueError("operation payload object required")
                return {name: copy_fields(value[name], child) for name, child in schema.items() if name in value}
            if isinstance(schema, list):
                if not isinstance(value, list) or len(value) > 256:
                    raise ValueError("operation payload list exceeds limit")
                return [copy_fields(item, schema[0]) for item in value]
            if isinstance(schema, tuple):
                if schema[0] == "proto":
                    return _copy_proto_message(value, _proto_descriptor(schema[1]))
                if schema[0] == "proto_field":
                    return _copy_proto_message({schema[2]: value}, _proto_descriptor(schema[1]))[schema[2]]
                if not isinstance(value, dict) or len(value) > 256:
                    raise ValueError("operation payload map exceeds limit")
                if any(not isinstance(key, str) or len(key) > 256 for key in value):
                    raise ValueError("operation payload map key invalid")
                return {key: copy_fields(item, schema[1]) for key, item in value.items()}
            if schema == "id":
                if type(value) is int and 0 <= value < 2 ** 64:
                    return str(value)
                if isinstance(value, str) and value.isascii() and value.isdecimal() and len(value) <= 20 and 0 <= int(value) < 2 ** 64:
                    return value
            elif schema == "number" and _number(value) is not None:
                return str(value) if type(value) is int and abs(value) > 2 ** 53 - 1 else value
            elif schema == "wire_number" and _wire_number(value) is not None:
                return str(value) if type(value) is int and abs(value) > 2 ** 53 - 1 else value
            elif schema == "bool" and type(value) is bool:
                return value
            elif schema in {"text", "enum"} and isinstance(value, str) and len(value) <= 2048:
                return value
            elif schema == "enum" and type(value) is int:
                return value
            raise ValueError("operation payload scalar invalid")

        raw_header = event["type"] == "CAPTURE_BATTLE_OPERATION"
        kind = event.get("operation_type") if raw_header else event["type"]
        kind = kind if isinstance(kind, str) and 0 < len(kind) <= 128 else "UnknownBattleOperation"
        seq_id = event["seq_id"]
        mode_changed = False
        if raw_header:
            indexed_metadata = (self._operation_sequence_basis == "source-index-and-capture-integrity"
                                or any(name in event for name in ("source_index", "operation_sha256", "capture_integrity")))
            basis = "source-index-and-capture-integrity" if indexed_metadata else "legacy-dense-game-sequence"
            if self._operation_source_mode != "raw-operation-header" or self._operation_sequence_basis != basis:
                mode_changed = self._operation_source_mode is not None
                self._operation_source_mode = "raw-operation-header"
                self._operation_sequence_basis = basis
                self._operation_stream_revision += 1
                self._target_context_revisions.clear()
                self._operation_source_previous_index = None
                self._operation_raw_metadata_valid = False
                self._operation_integrity_baseline = None
                self._operation_integrity_current = None
                self._operation_integrity_state = "unavailable"
                self._operation_integrity_revision = 0
                self._operation_integrity_after_index = None
                self._operation_integrity_changed_fields = []
                if mode_changed:
                    # A raw per-operation source is a new observation domain;
                    # no old normalized index or continuity claim crosses it.
                    self._operations.clear()
                    self._operation_index = 0
                    self._operation_previous_seq = None
                    self._operation_gap_revision = 1
                    self._operation_last_gap_index = 1
                    self._operation_dropped = 0
                    self._operation_payload_omitted = 0
                    self._operation_last_payload_omission_index = None
        elif self._operation_source_mode == "raw-operation-header":
            # A normalized event may enrich only its already observed raw
            # header. Synthetic derived event types cannot consume SeqId twice.
            operation = self._operations[-1] if self._operations else None
            if operation is None or operation["seqId"] != seq_id or operation["type"] != kind:
                return
            if kind not in schemas:
                return
            try:
                payload = copy_fields(event, schemas[kind])
                if len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > 32_000:
                    raise ValueError("operation payload exceeds byte limit")
                state, reason = "whitelisted-fields", None
                if operation["payloadState"] == "whitelisted-fields" and operation["payload"] != payload:
                    payload, state, reason = None, "omitted", "conflicting_normalized_payload"
            except (TypeError, ValueError, OverflowError):
                payload, state, reason = None, "omitted", "operation_payload_unavailable_or_exceeds_limit"
            if operation["payloadState"] == "omitted":
                return
            if operation["payloadState"] == state and operation["payload"] == payload:
                return
            operation.update(payload=payload, payloadState=state, payloadReason=reason,
                             payloadRevision=operation["payloadRevision"] + 1)
            if state == "omitted":
                self._operation_payload_omitted += 1
                self._operation_last_payload_omission_index = operation["index"]
            self._operation_content_revision += 1
            self._dirty.add("effects.operations.v1")
            return
        elif self._operation_source_mode is None:
            self._operation_source_mode = "normalized-only"
            self._operation_sequence_basis = "normalized-dense-game-sequence"
            self._operation_stream_revision += 1
            self._target_context_revisions.clear()
        self._operation_index += 1
        previous = self._operation_previous_seq
        indexed = self._operation_sequence_basis == "source-index-and-capture-integrity"
        relation = ("first_observation" if previous is None else "next" if seq_id == previous + 1 else
                    "repeated" if seq_id == previous else "backward" if seq_id < previous else
                    "forward_skip" if indexed else "gap")
        capture_relation = relation
        source_index = None
        operation_hash = None
        integrity = None
        discontinuity = previous is not None and (seq_id <= previous if indexed else seq_id != previous + 1)
        if indexed:
            raw_source_index = event.get("source_index")
            source_index = (raw_source_index if type(raw_source_index) is int and 0 < raw_source_index < 2 ** 53 else None)
            raw_hash = event.get("operation_sha256")
            operation_hash = (raw_hash.lower() if isinstance(raw_hash, str) and re.fullmatch(r"[a-fA-F0-9]{64}", raw_hash) else None)
            health_names = ("packetsDroppedQueue", "decompressionErrors", "protobufDecodeErrors", "framingErrors",
                            "pendingMultipacks", "pendingTransportSegments", "currentFrameErrors")
            raw_integrity = event.get("capture_integrity")
            if (isinstance(raw_integrity, dict)
                    and all(type(raw_integrity.get(name)) is int and raw_integrity[name] >= 0 for name in health_names)):
                integrity = {name: raw_integrity[name] for name in health_names}
            self._operation_raw_metadata_valid = source_index is not None and operation_hash is not None and integrity is not None
            previous_source_index = self._operation_source_previous_index
            capture_relation = ("missing_metadata" if source_index is None else "first_observation" if previous_source_index is None else
                                "next" if source_index == previous_source_index + 1 else "repeated" if source_index == previous_source_index else
                                "backward" if source_index < previous_source_index else "gap")
            if source_index is not None:
                self._operation_source_previous_index = source_index
            reference_health = self._operation_integrity_current or self._operation_integrity_baseline
            changed_fields = ([name for name in health_names[:4] if integrity[name] != reference_health[name]]
                              if integrity is not None and reference_health is not None else [])
            if integrity is None:
                self._operation_integrity_state = "unavailable"
                self._operation_integrity_changed_fields = []
            else:
                if (self._operation_integrity_baseline is None or changed_fields
                        or reference_health is not None and any(reference_health[name] > 0 for name in health_names[4:])
                        and not any(integrity[name] > 0 for name in health_names[4:])):
                    # A loss closes the old stable segment. Subsequent healthy
                    # records may support a NEW explicit semantic anchor; the
                    # old gap revision and boundary remain sticky.
                    self._operation_integrity_baseline = dict(integrity)
                self._operation_integrity_state = ("pending" if any(integrity[name] > 0 for name in health_names[4:]) else
                                                   "changed" if changed_fields else "stable")
                self._operation_integrity_changed_fields = changed_fields + [name for name in health_names[4:] if integrity[name] > 0]
            self._operation_integrity_current = deepcopy(integrity)
            health_bad = self._operation_integrity_state != "stable"
            if health_bad:
                self._operation_integrity_revision += 1
                self._operation_integrity_after_index = self._operation_index
            if not self._operation_raw_metadata_valid:
                capture_relation = "missing_metadata"
            elif capture_relation in {"first_observation", "next"} and health_bad:
                capture_relation = "pending" if self._operation_integrity_state == "pending" else "integrity_changed"
            discontinuity = discontinuity or not self._operation_raw_metadata_valid or capture_relation not in {"first_observation", "next"}
        if mode_changed:
            relation = "source_mode_changed"
            capture_relation = "source_mode_changed"
        if discontinuity and not mode_changed:
            self._operation_gap_revision += 1
            self._operation_last_gap_index = self._operation_index
        self._operation_previous_seq = seq_id
        payload, payload_state, payload_reason = None, "header-only", None
        if raw_header and kind in schemas:
            payload_state = "pending"
        elif kind in schemas:
            try:
                payload = copy_fields(event, schemas[kind])
                if len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > 32_000:
                    raise ValueError("operation payload exceeds byte limit")
                payload_state = "whitelisted-fields"
            except (TypeError, ValueError, OverflowError):
                payload, payload_state, payload_reason = None, "omitted", "operation_payload_unavailable_or_exceeds_limit"
                self._operation_payload_omitted += 1
                self._operation_last_payload_omission_index = self._operation_index
        self._operations.append({"index": self._operation_index, "seqId": seq_id, "type": kind, "atMs": at,
                                 "sequenceRelation": relation, "payloadState": payload_state,
                                 "captureSequenceRelation": capture_relation, "sourceIndex": source_index,
                                 "operationSha256": operation_hash, "captureIntegrity": deepcopy(integrity),
                                 "captureIntegrityState": self._operation_integrity_state,
                                 "captureIntegrityRevision": self._operation_integrity_revision,
                                 "payloadReason": payload_reason, "payload": payload, "payloadRevision": 0,
                                 "opType": event.get("op_type") if raw_header and type(event.get("op_type")) is int else None})
        self._operation_content_revision += 1
        if len(self._operations) > self._max_operations:
            self._operations.pop(0)
            self._operation_dropped += 1
        self._dirty.add("effects.operations.v1")

    def _record_game_event(self, event: dict[str, Any], at: int) -> None:
        kind = event.get("type")
        if not isinstance(kind, str) or kind not in _GAME_EVENT_TYPES:
            return
        self._game_event_index += 1
        removed = [0]
        try:
            source = {key: value for key, value in event.items() if key not in {"type", "session_id", "timestamp_ms"}}
            payload = _public_json(source, removed=removed)
            if len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > 1_000_000:
                raise ValueError("game event payload exceeds byte limit")
            state, reason = "complete", None
        except (ValueError, TypeError, OverflowError, RecursionError):
            payload, state, reason = None, "omitted", "event_payload_unavailable_or_exceeds_limit"
            self._game_event_payload_omitted += 1
        row = {"index": self._game_event_index, "type": kind, "atMs": at, "payload": payload,
               "payloadState": state, "payloadReason": reason, "excludedDiagnosticFieldCount": removed[0]}
        self._game_events.append(row)
        self._game_event_bytes += len(json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        while len(self._game_events) > self._max_game_events or self._game_event_bytes > 1_250_000:
            old = self._game_events.pop(0)
            self._game_event_bytes -= len(json.dumps(old, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            self._game_event_dropped += 1
        self._dirty.add("game.events.v1")

    def _schedule(self) -> None:
        with self._lock:
            if not self._dirty or self._timer is not None:
                return
            self._timer = threading.Timer(self._interval, self.flush)
            self._timer.daemon = True
            self._timer.start()

    @staticmethod
    def _projection_key(subscription: _Subscription) -> tuple[Any, ...]:
        options = subscription.options
        return (subscription.topic, options.get("target", "all"), options.get("hideUnknown", False),
                options.get("onlyGameIcons", False),
                options.get("query", ""), options.get("limit"), options.get("offset", 0),
                *(tuple(sorted(options.get(name, ()))) for name in ("targetIds", "characterIds", "effectIds", "sourceTypes", "sourceIds", "eventTypes")))

    def flush(self) -> None:
        groups: dict[tuple[Any, ...], tuple[dict[str, Any], list[_Subscription]]] = {}
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            dirty, self._dirty = self._dirty, set()
            if not dirty:
                return
            self._seq += 1
            received_at = int(self._clock() * 1000)
            for subscription in self._subscriptions:
                if subscription.topic in dirty:
                    key = self._projection_key(subscription)
                    if key not in groups:
                        envelope = self._build(subscription.topic, subscription.options)
                        envelope["receivedAtMs"] = received_at
                        groups[key] = (envelope, [])
                    groups[key][1].append(subscription)
        # Projection rows are detached while holding the state lock. Prepare
        # each callback's independently owned payload outside that lock, and
        # before any callback can mutate the first group's original snapshot.
        # Equal filters share one production pass, never one mutable delivery.
        deliveries: list[tuple[_Subscription, dict[str, Any]]] = []
        for envelope, subscriptions in groups.values():
            for index, subscription in enumerate(subscriptions):
                deliveries.append((subscription, envelope if index == 0 else deepcopy(envelope)))
        for subscription, envelope in deliveries:
            subscription.deliver(envelope, isolated=True)

    def close(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            subscriptions, self._subscriptions = self._subscriptions, []
        for subscription in subscriptions:
            with subscription.lock:
                subscription.active = False

    def _target(self, battle: Any, *, obj: Any = None, template: Any = None,
                kind: str = "object", at: int | None = None) -> str | None:
        identity = _identity(battle)
        if identity is None:
            return None
        old = self._targets.get(identity)
        template_id = _identity(template) or (old.get("templateId") if old else None)
        metadata = self._target_catalog.get(template_id, {})
        row = {"id": identity, "battleInstId": identity, "objectId": _identity(obj),
               "templateId": template_id, "label": metadata.get("label") if isinstance(metadata.get("label"), str) else None,
               "kind": kind, "isEnemy": metadata.get("isEnemy") if type(metadata.get("isEnemy")) is bool else None,
               "isBoss": metadata.get("isBoss") if type(metadata.get("isBoss")) is bool else None,
               "isControlled": None, "present": True}
        if old:
            row["objectId"] = row["objectId"] or old["objectId"]
            if old["kind"] == "party":
                row["kind"] = "party"
            row["label"] = row["label"] or old["label"]
        if row["objectId"]:
            previous_battle = self._object_to_battle.get(row["objectId"])
            if previous_battle is not None and previous_battle != identity:
                previous_target = self._targets.get(previous_battle)
                if previous_target is not None:
                    previous_target.update(present=False, absenceReason="target-instance-reassigned")
                self._mark(target=previous_battle, at=at if at is not None else int(self._clock() * 1000),
                           reason="target-instance-reassigned")
                self._target_generations.pop(previous_battle, None)
            self._object_to_battle[row["objectId"]] = identity
        self._targets[identity] = row
        if len(self._targets) > 2048:
            oldest = next(iter(self._targets))
            removed = self._targets.pop(oldest)
            self._target_generations.pop(oldest, None)
            if removed["objectId"] and self._object_to_battle.get(removed["objectId"]) == oldest:
                self._object_to_battle.pop(removed["objectId"], None)
        return identity

    def _observe(self, namespace: str, uid: Any, effect: Any, target: Any, source: Any,
                 at: int, *, observation: str, raw: dict[str, Any] | None = None,
                 enabled: bool | None = None, owner: Any = None, generation: Any = None,
                 identity_source_context: str | None = None) -> None:
        instance, effect_id, target_id = _identity(uid), _template(effect), _identity(target)
        if instance is None or effect_id is None:
            return
        # Server owner/generation and client UID are separate identity spaces.
        owner_id = _identity(owner) or target_id or "unknown"
        server_generation = str(generation) if type(generation) is int and generation >= 0 else "unknown"
        key = f"{namespace}:{owner_id}:{server_generation if namespace == 'server_buff' else '-'}:{instance}"
        template_type = effect.get("type") if isinstance(effect, dict) else None
        if not (isinstance(template_type, str) or type(template_type) is int):
            template_type = None
        id_case = ("str_id" if "str_id" in effect and "int_id" not in effect else
                   "int_id" if "int_id" in effect and "str_id" not in effect else None) if isinstance(effect, dict) else (
                   "direct_string" if isinstance(effect, str) else "direct_numeric")
        metadata = self._effect_metadata(effect_id, namespace, template_type, id_case, identity_source_context)
        safe_raw = deepcopy(raw or {})
        safe_raw.update(templateType=template_type, templateIdCase=id_case)
        if identity_source_context == "protobuf-create-buff-detail":
            safe_raw.update(identitySourceContext=identity_source_context,
                            identitySourceField="BATTLE_CREATE_BUFF_ACTION_DETAIL.buff_num_id")
        previous = self._effects.get(key)
        if previous and namespace == "client_buff" and previous["state"] != "observed-open":
            # Repeated creation/refresh observations do not establish a new
            # lifetime for an already closed or invalidated client UID.
            self._unresolved_observation("application-after-context-ended", instance, at, owner_id,
                                         {"effectId": effect_id, "previousState": previous["state"],
                                          "previousClosureReason": previous.get("closureReason")},
                                         target=target_id, reason="instance_context_unavailable")
            return
        same_open_instance = bool(previous and previous["state"] == "observed-open" and previous["effectId"] == effect_id)
        if same_open_instance:
            safe_raw = {**previous["raw"], **{name: value for name, value in safe_raw.items() if value is not None}}
            metadata = self._effect_metadata(effect_id, namespace, safe_raw.get("templateType"),
                                             safe_raw.get("templateIdCase"), safe_raw.get("identitySourceContext"))
            enabled = previous["enabled"] if enabled is None else enabled
            source = previous["sourceId"] if _identity(source) is None else source
        first = previous["firstObservedAtMs"] if previous and previous["effectId"] == effect_id else at
        if previous and previous["effectId"] != effect_id:
            replaced = deepcopy(previous)
            replaced["state"], replaced["closureReason"] = "observed-closed", "instance-overwritten"
            replacement_key = f"{key}:replaced:{self._seq}:{at}"
            replaced["key"] = replacement_key
            self._effects[replacement_key] = replaced
            self._needs_closed_trim = True
        self._effects[key] = {"key": key, "namespace": namespace, "effectId": effect_id,
                              "canonicalId": metadata.get("canonicalId"),
                              "instanceId": instance if namespace in {"client_buff", "server_buff"} else None,
                              "slotId": instance if namespace not in {"client_buff", "server_buff"} else None,
                              "targetId": target_id, "sourceId": _identity(source),
                              "label": metadata.get("label"), "classification": metadata.get("classification", "unclassified"),
                              "description": metadata.get("description"), "labelSource": metadata.get("labelSource"),
                              "descriptionSource": metadata.get("descriptionSource"),
                              "hasIcon": metadata.get("hasIcon"), "uiGroup": metadata.get("uiGroup"),
                              "iconPresentation": metadata.get("iconPresentation"),
                              "uiGroupUsesNumericPriority": metadata.get("uiGroupUsesNumericPriority"),
                              "classificationSource": metadata.get("classificationSource"),
                              "state": "observed-open", "observationKind": observation,
                              "firstObservedAtMs": first, "lastObservedAtMs": at,
                              "stackCount": None, "durationMs": None, "expiresAtMs": None, "remainingMs": None,
                              "enabled": enabled, "removedAtMs": None, "finishedAtMs": None, "closureReason": None,
                              "raw": safe_raw}
        if namespace == "client_buff":
            self._client_uid_keys.setdefault(instance, set()).add(key)
        if same_open_instance:
            for name in ("stackCount", "durationMs", "expiresAtMs", "remainingMs", "runtimeVariables"):
                if name in previous:
                    self._effects[key][name] = deepcopy(previous[name])

    def _mark(self, *, namespace: str | None = None, uid: Any = None, target: Any = None,
              at: int, reason: str, actual_remove: bool = False, raw: dict[str, Any] | None = None) -> None:
        uid_id, target_id = _identity(uid), _identity(target)
        if namespace is None and uid is None and target_id is not None:
            # Target lifecycle invalidation is independent of retained effect
            # rows. Never infer zero from an empty row set, and never reuse a
            # revision after a bounded-map eviction in this global context.
            self._target_context_revision_serial += 1
            self._target_context_revisions.pop(target_id, None)
            self._target_context_revisions[target_id] = {
                "revision": self._target_context_revision_serial, "afterIndex": self._operation_index,
                "streamRevision": self._operation_stream_revision}
            if len(self._target_context_revisions) > 2048:
                self._target_context_revisions.pop(next(iter(self._target_context_revisions)))
                self._target_context_revision_overflow += 1
                self._target_context_revision_overflow_after_index = self._operation_index
                self._target_context_revision_overflow_stream_revision = self._operation_stream_revision
            self._operation_content_revision += 1
            self._dirty.add("effects.operations.v1")
        candidates = ([self._effects[key] for key in self._client_uid_keys.get(uid_id, ()) if key in self._effects]
                      if namespace == "client_buff" and uid_id is not None else self._effects.values())
        matches = [row for row in candidates
                   if row["state"] == "observed-open"
                   and (namespace is None or row["namespace"] == namespace)
                   and (uid_id is None or row["instanceId"] == uid_id or row.get("slotId") == uid_id)
                   and (target_id is None or row["targetId"] == target_id)]
        # A single observed candidate cannot prove ownership in a partial
        # capture. Client lifecycle changes require their explicit target.
        if namespace == "client_buff" and uid_id is not None and target_id is None:
            return
        for row in matches:
            row["lastObservedAtMs"] = at
            if raw:
                row["raw"].update(raw)
            if reason == "finish-request":
                row["observationKind"] = reason
                continue
            row["state"] = "observed-closed" if actual_remove else "context-unknown"
            self._needs_closed_trim = True
            row["closureReason"] = reason
            row["removedAtMs"] = at if actual_remove else None

    def _apply(self, event: dict[str, Any], at: int) -> bool:
        kind = event.get("type")
        if kind == "SC_RESET_BATTLE_STATUS":
            info = event.get("battle_info")
            info = info if isinstance(info, dict) else {}
            explicit = _identity(event.get("battle_inst_id"))
            nested = _identity(info.get("battle_inst_id"))
            # Both fields name the battle-instance namespace directly. A
            # disagreement cannot be resolved using inst_id's equal number,
            # roster membership, or any observed candidate count.
            target = explicit or nested
            if target is None or (explicit and nested and explicit != nested):
                self._unresolved_observation("target-reset", event.get("inst_id") or "missing-target", at,
                                             event.get("inst_id"), {
                                                 "battleInstId": explicit, "snapshotBattleInstId": nested,
                                                 "generation": _number(info.get("msg_generation"))},
                                             namespace="scene_object",
                                             reason="reset_target_conflict" if explicit and nested else "reset_target_unverified")
                return True
            self._mark(target=target, at=at, reason="target-battle-reset")
            self._stacking_groups = {key: row for key, row in self._stacking_groups.items()
                                     if row["targetId"] != target}
            generation = info.get("msg_generation")
            if type(generation) is int and 0 <= generation < 2 ** 32:
                self._target_generations[target] = generation
                while len(self._target_generations) > 2048:
                    self._target_generations.pop(next(iter(self._target_generations)))
            return True
        if kind == "SC_BATTLE_GENERATION_CHANGE":
            obj = _identity(event.get("inst_id"))
            generation = event.get("generation")
            valid_generation = type(generation) is int and 0 <= generation < 2 ** 32
            # Same-version native handler looks up InstId as a scene object
            # (CharBag.GetCharInfo / GameWorld entity dictionary), never as a
            # battle instance. Only the explicit observed mapping may join it.
            target = self._object_to_battle.get(obj) if self._entity_generation_exact and obj else None
            if target is None or not valid_generation:
                self._unresolved_observation("target-generation", obj or "missing-target", at, obj,
                                             {"generation": generation if valid_generation else None},
                                             namespace="scene_object",
                                             reason=(self._entity_generation_reason if not self._entity_generation_exact else
                                                     "generation_value_unverified" if not valid_generation else
                                                     "object_battle_relation_unverified"))
                return True
            previous = self._target_generations.get(target)
            self._target_generations[target] = generation
            if previous is not None and previous != generation:
                self._mark(target=target, at=at, reason="target-generation-changed")
                self._stacking_groups = {key: row for key, row in self._stacking_groups.items()
                                         if row["targetId"] != target}
            elif previous is None:
                # A first observed generation establishes the namespace for
                # subsequent comparisons; it does not date existing effects.
                self._unresolved_observation("target-generation", obj, at, obj,
                                             {"generation": generation}, target=target,
                                             namespace="scene_object", reason="previous_generation_unobserved")
            return True
        if kind in {"SC_SELF_SCENE_INFO", "squad_update"}:
            scene = (event.get("scene_id"), event.get("scene_num_id"))
            if kind == "SC_SELF_SCENE_INFO" and any(value is not None for value in scene):
                old_scene = self._scene_identity
                if old_scene is not None and any(old is not None and new is not None and old != new
                                                 for old, new in zip(old_scene, scene)):
                    self._reset("awaiting_fresh_context_data", session=self._session)
                self._scene_identity = tuple(new if new is not None else old
                                             for old, new in zip(old_scene or (None, None), scene))
            members = event.get("char_list", event.get("members", []))
            if not isinstance(members, list):
                return False
            for row in self._targets.values():
                if row["kind"] == "party":
                    row["kind"] = "object"
            for member in members:
                if isinstance(member, dict):
                    target = self._target(member.get("battle_inst_id"), obj=member.get("id"),
                                          template=member.get("templateid"), kind="party", at=at)
                    if target and isinstance(member.get("display_name"), str):
                        self._targets[target]["label"] = member["display_name"]
            return True
        if kind == "SC_OBJECT_ENTER_VIEW":
            for row in event.get("objects", []):
                if isinstance(row, dict):
                    self._target(row.get("battle_inst_id"), obj=row.get("obj_id"),
                                 template=row.get("templateid"), kind=str(row.get("kind") or "object"), at=at)
            return True
        if kind == "SC_OBJECT_LEAVE_VIEW":
            for row in event.get("objects", []):
                if not isinstance(row, dict):
                    continue
                target = _identity(row.get("battle_inst_id")) or self._object_to_battle.get(_identity(row.get("obj_id")))
                if target:
                    self._mark(target=target, at=at, reason="target-left-view")
                    self._targets.pop(target, None)
                    self._target_generations.pop(target, None)
                obj = _identity(row.get("obj_id"))
                if obj:
                    self._object_to_battle.pop(obj, None)
            return True
        if kind == "BattleOpEntityDie":
            target = _identity(event.get("entity_inst_id"))
            if target:
                self._mark(target=target, at=at, reason="target-died")
                if target in self._targets:
                    self._targets[target]["present"] = False
            return True
        if kind == "SC_EFFECT_SNAPSHOT":
            for owner in event.get("owners", []):
                if not isinstance(owner, dict):
                    continue
                target = self._target(owner.get("owner_battle_inst_id"), obj=owner.get("owner_obj_id"),
                                      template=owner.get("owner_templateid"),
                                      kind=str(owner.get("owner_kind") or "object"), at=at)
                if target and isinstance(owner.get("owner_display_name"), str):
                    self._targets[target]["label"] = owner["owner_display_name"]
                if target:
                    # Full owner snapshot can supersede prior observations,
                    # but absence is not an explicit server remove event.
                    self._mark(namespace="server_buff", target=target, at=at, reason="owner-snapshot-replaced")
                    generation = owner.get("generation")
                    if type(generation) is int and 0 <= generation < 2 ** 32:
                        self._target_generations[target] = generation
                obj = _identity(owner.get("owner_obj_id"))
                self._unresolved_owner_serial += 1
                owner_key = (f"battle_owner:{target}" if target else f"object_owner:{obj}" if obj else
                             f"unresolved_owner:{self._unresolved_owner_serial}")
                for group in owner.get("stacking_groups", []):
                    if not isinstance(group, dict):
                        continue
                    stacking_key = group.get("stacking_key")
                    if not isinstance(stacking_key, str) or len(stacking_key) > 256:
                        continue
                    self._stacking_groups[f"{owner_key}:{stacking_key}"] = {
                        "targetId": target, "ownerObjectId": obj, "stackingKey": stacking_key,
                        "stackingType": _number(group.get("stacking_type")),
                        "maxStackCount": _number(group.get("max_stack_cnt")),
                        "isServerInit": group.get("is_server_init") if type(group.get("is_server_init")) is bool else None,
                        "instanceIds": [uid for raw in group.get("buff_inst_ids", [])
                                        if (uid := _identity(raw)) is not None],
                        "generation": owner.get("generation") if type(owner.get("generation")) is int else None,
                        "observedAtMs": at,
                    }
                self._server_buffs(owner.get("buffs", []), target, owner.get("generation"), at,
                                   observation="initialSnapshot", owner=owner_key)
            return True
        if kind == "SC_ADD_SERVER_BUFF":
            # The wire inst_id's relation to scene obj/battle identities is
            # not proven. Equal integers across these namespaces do not join.
            owner = _identity(event.get("inst_id"))
            self._server_buffs(event.get("buffs", []), None, event.get("generation"), at,
                               observation="application_or_refresh", owner=f"wire_owner:{owner}" if owner else None)
            return True
        if kind == "SC_REMOVE_SERVER_BUFF":
            owner = _identity(event.get("inst_id"))
            generation = event.get("generation")
            for uid in event.get("del_inst_ids", []):
                uid = _identity(uid)
                if not uid or owner is None or type(generation) is not int:
                    continue
                key = f"server_buff:wire_owner:{owner}:{generation}:{uid}"
                row = self._effects.get(key)
                if row:
                    row.update(state="observed-closed", closureReason="server-remove", removedAtMs=at, lastObservedAtMs=at)
                    self._needs_closed_trim = True
            return True
        if kind == "BattleOpAddBuff":
            effect = event.get("buff_template_id") or event.get("str_id") or event.get("int_id")
            initialization = event.get("raw_data")
            initialization = initialization if isinstance(initialization, dict) else {}
            self._observe("client_buff", event.get("buff_inst_id"), effect, event.get("target_inst_id"),
                          event.get("src_inst_id"), at, observation="application_or_refresh",
                          raw={"operationGeneration": event.get("generation") if type(event.get("generation")) is int else None,
                               "assignedItems": deepcopy(event["assigned_items"]) if isinstance(event.get("assigned_items"), (dict, list)) else None,
                               "assignedItemsSource": "BATTLE_ADD_BUFF_OP_DATA.assigned_items" if isinstance(event.get("assigned_items"), (dict, list)) else None,
                               "clientAddTs": _wire_number(initialization.get("client_add_ts")), "clientAddTsUnit": "unverified",
                               "clientAddTsSource": "BATTLE_ADD_BUFF_OP_DATA.client_add_ts" if _wire_number(initialization.get("client_add_ts")) is not None else None,
                               "buffSrcType": _number(initialization.get("buff_src_type")),
                               "buffSrcArgs": initialization.get("buff_src_args") if isinstance(initialization.get("buff_src_args"), str) else None})
            return True
        if kind == "BattleOpSetBuffEnableState":
            states = event.get("states")
            target = _identity(event.get("owner_id"))
            for uid, enabled in states.items() if isinstance(states, dict) else ():
                if type(enabled) is not bool:
                    continue
                uid = _identity(uid)
                raw_enable = {"enabled": enabled, "operationGeneration": _number(event.get("generation")),
                              "operationSeq": _number(event.get("seq_id"))}
                confirmed = (self._client_enable_exact and uid is not None and uid.isdecimal()
                             and bool(int(uid) & (1 << 61)) and target is not None and target.isdecimal())
                row = self._effects.get(f"client_buff:{target}:-:{uid}") if confirmed else None
                if row is not None and row["state"] == "observed-open":
                    # The setter reports the existing Buff's state. Disabled
                    # is neither finished nor removed and may become enabled
                    # again. Never revive a closed/invalidated observation.
                    row.update(enabled=enabled, lastObservedAtMs=at, observationKind="enable-state-confirmed")
                    row["raw"].update(raw_enable, enableSourceContext="native-client-buff-enable-setter")
                else:
                    self._unresolved_observation("enable_state", uid, at, target, raw_enable,
                                                 target=target if confirmed else None,
                                                 reason=(self._client_enable_reason if not self._client_enable_exact else
                                                         "instance_context_unavailable" if row is not None else
                                                         "creation_not_observed" if confirmed else "target_relation_unverified"))
            return True
        if kind == "BattleOpFinishBuff":
            uid, target = _identity(event.get("buff_inst_id")), _identity(event.get("operation_owner_id"))
            raw_finish = {"finishReduceLayer": _number(event.get("reduce_layer")),
                          "finishForce": event.get("force") if type(event.get("force")) is bool else None,
                          "finishTargetGeneration": _number(event.get("generation"))}
            confirmed = (self._client_finish_exact and uid is not None and uid.isdecimal()
                         and bool(int(uid) & (1 << 61)) and target is not None and target.isdecimal()
                         and type(event.get("reduce_layer")) is int and event["reduce_layer"] == 1
                         and event.get("force") is True)
            if confirmed:
                row = self._effects.get(f"client_buff:{target}:-:{uid}")
                if row is not None and row["state"] == "observed-open" and row["targetId"] == target and row["instanceId"] == uid:
                    row.update(state="observed-closed", closureReason="client-finish-confirmed", enabled=False,
                               lastObservedAtMs=at, finishedAtMs=at, observationKind="finish-confirmed")
                    row["raw"].update(raw_finish, finishSourceContext="native-client-mark-finish")
                    self._needs_closed_trim = True
                else:
                    self._unresolved_observation("finish-confirmed", uid, at, target, raw_finish,
                                                 target=target, reason="instance_context_unavailable" if row is not None else "creation_not_observed")
            elif uid:
                self._unresolved_observation("finish-request", uid, at, event.get("operation_owner_id"), raw_finish)
            return True
        if kind == "BattleOpTriggerKeyword":
            updates = [{"configIndex": row["config_index"], "value": row["modify_value"]}
                       for row in event.get("modify", []) if isinstance(row, dict)
                       and type(row.get("config_index")) is int and _number(row.get("modify_value")) is not None]
            uid, target = _identity(event.get("buff_inst_id")), _identity(event.get("owner_id"))
            raw_keyword = {"keywordUpdates": updates,
                           "keywordUpdateCount": len(event["modify"]) if isinstance(event.get("modify"), list) else None,
                           "addedBuffNumericId": _number(event.get("add_buff_num_id")),
                           "operationGeneration": _number(event.get("generation")),
                           "operationSeq": _number(event.get("seq_id"))}
            modify = event.get("modify")
            valid_updates = (isinstance(modify, list) and bool(modify) and len(updates) == len(modify)
                             and all(row["configIndex"] >= 0 for row in updates))
            confirmed = (self._client_keyword_variables_exact and uid is not None and uid.isdecimal()
                         and bool(int(uid) & (1 << 61)) and target is not None and target.isdecimal() and valid_updates)
            row = self._effects.get(f"client_buff:{target}:-:{uid}") if confirmed else None
            if row is not None and row["state"] == "observed-open":
                # Each array element reports the value after that assignment.
                # config_index selects an enhancement entry, not a variable
                # slot or stack. Preserve order and expose the final rate.
                row["runtimeVariables"] = {"rate": {"value": updates[-1]["value"], "unit": None,
                                                   "source": "BATTLE_TRIGGER_KEYWORD_OP_DATA.modify.modify_value",
                                                   "valueSemantics": "absolute_after_assignment",
                                                   "configIndex": updates[-1]["configIndex"],
                                                   "indexSemantics": "zero_based_keyword_enhance_list", "observedAtMs": at}}
                row.update(lastObservedAtMs=at, observationKind="keyword-variable-confirmed")
                row["raw"].update(raw_keyword, keywordSourceContext="native-client-keyword-variable")
            else:
                self._unresolved_observation("keyword_update", uid, at, target, raw_keyword,
                                             target=target if confirmed else None,
                                             reason=(self._client_keyword_variables_reason if not self._client_keyword_variables_exact else
                                                     "keyword_update_payload_invalid" if not valid_updates else
                                                     "instance_context_unavailable" if row is not None else
                                                     "creation_not_observed" if confirmed else "target_relation_unverified"))
            return True
        if kind == "BattleOpTriggerAction":
            action = event.get("action")
            if not isinstance(action, dict):
                return False
            create = action.get("create_buff_action")
            if isinstance(create, dict) and event.get("trigger_type") == 0:
                action_type = action.get("action_type")
                typed_create = (event.get("action_runtime_case") == "create_buff_action"
                                and (action_type == "BattleActionCreateBuff" or
                                     (type(action_type) is int and action_type == 2)))
                for row in create.get("details", []):
                    if isinstance(row, dict):
                        numeric = row.get("buff_num_id")
                        numeric_context = ("protobuf-create-buff-detail" if typed_create and not row.get("buff_id")
                                           and type(numeric) is int and -(2 ** 31) <= numeric < 2 ** 31 and numeric != 0 else None)
                        self._observe("client_buff", row.get("buff_inst_id"), row.get("buff_id") or row.get("buff_num_id"),
                                      row.get("target_id"), row.get("source_id"), at, observation="application_or_refresh",
                                      identity_source_context=numeric_context,
                                      raw={"assignedItems": deepcopy(row["assigned_items"]) if isinstance(row.get("assigned_items"), list) else None,
                                           "assignedItemsSource": "BATTLE_CREATE_BUFF_ACTION_DETAIL.assigned_items" if isinstance(row.get("assigned_items"), list) else None,
                                           "buffInputIndex": row.get("buff_input_index") if type(row.get("buff_input_index")) is int else None,
                                           "buffInputIndexSource": "BATTLE_CREATE_BUFF_ACTION_DETAIL.buff_input_index" if type(row.get("buff_input_index")) is int else None})
                return True
            finish = action.get("finish_buff_action")
            rows = (finish.get("finish_buffs", []) if isinstance(finish, dict) else
                    create.get("details", []) if isinstance(create, dict) and event.get("trigger_type") == 2 else [])
            for row in rows:
                if isinstance(row, dict) and _identity(row.get("buff_inst_id")):
                    uid, target = _identity(row.get("buff_inst_id")), _identity(row.get("target_id"))
                    typed_finish = (isinstance(finish, dict) and event.get("action_runtime_case") == "finish_buff_action"
                                    and (action.get("action_type") == "BattleActionFinishBuff" or
                                         (type(action.get("action_type")) is int and action["action_type"] == 3))
                                    and type(event.get("trigger_type")) is int and event["trigger_type"] == 0)
                    confirmed = (self._client_action_finish_exact and typed_finish and uid.isdecimal()
                                 and bool(int(uid) & (1 << 61)) and target is not None and target.isdecimal())
                    observed = self._effects.get(f"client_buff:{target}:-:{uid}") if target else None
                    if confirmed and observed is not None and observed["state"] == "observed-open":
                        observed.update(state="observed-closed", closureReason="client-action-finish-confirmed", enabled=False,
                                        lastObservedAtMs=at, finishedAtMs=at, observationKind="finish-confirmed")
                        observed["raw"].update(finishSourceContext="native-client-action-mark-finish",
                                               finishSourceField="BATTLE_FINISH_BUFF_ACTION_RUNTIME.finish_buffs",
                                               finishActionTriggerType=event["trigger_type"])
                        self._needs_closed_trim = True
                    else:
                        reason = ("instance_context_unavailable" if confirmed and observed is not None else
                                  "creation_not_observed" if confirmed else
                                  "create_action_end_does_not_prove_finished" if isinstance(create, dict) and event.get("trigger_type") == 2 else
                                  self._client_action_finish_reason if not self._client_action_finish_exact else "finish_action_case_unverified")
                        if not confirmed and target and observed is not None and observed["state"] == "observed-open":
                            self._mark(namespace="client_buff", uid=uid, target=target, at=at, reason="finish-request",
                                       raw={"finishRequestReason": reason})
                        else:
                            self._unresolved_observation("finish-request", uid, at, event.get("operation_owner_id"),
                                                         {"finishRequestReason": reason}, target=target, reason=reason)
            return bool(rows)
        if kind in {"SC_BATTLE_ADD_GLOBAL_BUFF", "SC_BATTLE_SYNC_GLOBAL_BUFF_INFO"}:
            rows = event.get("global_buff_info") or event.get("global_buff_infos") or event.get("global_buff_list") or []
            if isinstance(rows, dict):
                rows = [rows]
            for row in rows if isinstance(rows, list) else ():
                if isinstance(row, dict) and _identity(row.get("global_buff_num_id")):
                    # No instance identity exists in GLOBAL_BUFF_INFO. The ID
                    # is a slot in its own namespace, never a client Buff UID.
                    num = row["global_buff_num_id"]
                    assigned = row.get("assigned_items")
                    self._observe("global_buff", num, num, None, None, at, observation="global_observation",
                                  raw={"assignedItems": deepcopy(assigned) if isinstance(assigned, (dict, list)) else None,
                                       "assignedItemsSource": "GLOBAL_BUFF_INFO.assigned_items" if isinstance(assigned, (dict, list)) else None})
            return True
        if kind == "SC_BATTLE_REMOVE_GLOBAL_BUFF":
            values = event.get("global_buff_num_ids", [])
            if "global_buff_num_id" in event:
                values = [event["global_buff_num_id"]]
            for uid in values:
                self._mark(namespace="global_buff", uid=uid, at=at, reason="global-remove", actual_remove=True)
            return True
        if kind == "SC_CHAR_BAG_SYNC_PERSISTENT_BUFFS":
            for row in event.get("buffs", []):
                if not isinstance(row, dict) or _identity(row.get("item_num_id")) is None:
                    continue
                item = row["item_num_id"]
                self._observe("persistent_item_effect", item, item, None, None, at,
                              observation="persistent_item_snapshot", raw={
                                  "itemNumId": _identity(item), "duration": _number(row.get("duration")),
                                  "durationUnit": "unverified", "targetCharacterObjectIds": [v for raw in row.get("char_inst_ids", [])
                                                                                          if (v := _identity(raw)) is not None],
                                  "stackingKey": row.get("stacking_key") if isinstance(row.get("stacking_key"), str) else None})
            return True
        if kind in {"SC_SYNC_RACING_DUNGEON_BUFF_MODIFY", "SC_SYNC_RACING_DUNGEON_RECONNECT"}:
            values = list(event.get("buff_list", []))
            if _identity(event.get("add_buff_id")):
                values.append(event["add_buff_id"])
            for value in values:
                if _identity(value):
                    self._observe("racing_effect", value, value, None, None, at, observation="racing_effect_observation")
            return True
        return False

    def _unresolved_observation(self, kind: str, uid: Any, at: int, owner: Any,
                                raw: dict[str, Any], *, target: str | None = None,
                                namespace: str = "client_buff",
                                reason: str = "target_relation_unverified") -> None:
        instance, owner_id = _identity(uid), _identity(owner)
        if instance is None:
            return
        key = f"{kind}:{owner_id}:{instance}"
        self._unresolved[key] = {"namespace": namespace, "instanceId": instance,
                                 "targetId": target, "operationOwnerId": owner_id,
                                 "observationKind": kind, "lastObservedAtMs": at,
                                 "reason": reason, "raw": raw}
        while len(self._unresolved) > 256:
            self._unresolved.pop(next(iter(self._unresolved)))

    def _server_buffs(self, rows: Any, target: str | None, generation: Any, at: int, *,
                      observation: str, owner: str | None = None) -> None:
        for row in rows if isinstance(rows, list) else ():
            if not isinstance(row, dict):
                continue
            raw = {"duration": _wire_number(row.get("duration")), "durationUnit": "unverified",
                   "durationSource": "SERVER_BUFF.duration" if _wire_number(row.get("duration")) is not None else None,
                   "blackboard": deepcopy(row["blackboard"]) if isinstance(row.get("blackboard"), dict) else None,
                   "blackboardSource": "SERVER_BUFF.blackboard" if isinstance(row.get("blackboard"), dict) else None,
                   "priority": _number(row.get("priority")), "enhanceMulti": _number(row.get("enhance_multi")),
                   "stackingGroupId": row.get("stacking_group_id") if isinstance(row.get("stacking_group_id"), str) else None,
                   "isServerInit": row.get("is_server_init") if type(row.get("is_server_init")) is bool else None}
            self._observe("server_buff", row.get("inst_id"), row.get("buff_id"), target, None, at,
                          observation=observation, owner=owner or target, generation=generation, raw=raw,
                          enabled=row.get("is_enable") if type(row.get("is_enable")) is bool else None)

    def _trim(self) -> None:
        while len(self._stacking_groups) > self._max_effects:
            self._stacking_groups.pop(next(iter(self._stacking_groups)))
        if self._needs_closed_trim:
            self._needs_closed_trim = False
            closed = [key for key, row in self._effects.items() if row["state"] != "observed-open"]
            for key in closed[:max(0, len(closed) - self._max_closed)]:
                self._pop_effect(key)
                self._omitted_closed += 1
        overflow = len(self._effects) - self._max_effects
        if overflow > 0:
            for key in list(self._effects)[:overflow]:
                row = self._pop_effect(key)
                if row["state"] == "observed-open":
                    self._omitted_open += 1
                else:
                    self._omitted_closed += 1

    def _pop_effect(self, key: str) -> dict[str, Any]:
        row = self._effects.pop(key)
        if row["namespace"] == "client_buff":
            keys = self._client_uid_keys.get(row["instanceId"])
            if keys is not None:
                keys.discard(key)
                if not keys:
                    self._client_uid_keys.pop(row["instanceId"], None)
        return row


_singleton: OverlayTopicBroker | None = None
_singleton_lock = threading.Lock()


def get_overlay_broker() -> OverlayTopicBroker:
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            try:
                from .overlay_effect_catalog import load_effect_catalog, load_target_catalog
                catalog = load_effect_catalog()
                target_catalog = load_target_catalog()
            except (ImportError, OSError, ValueError, TypeError):
                LOGGER.warning("overlay effect catalog unavailable", exc_info=True)
                catalog = []
                target_catalog = {}
            try:
                from .overlay_effect_catalog import load_effect_definitions
            except (ImportError, OSError, ValueError, TypeError):
                LOGGER.warning("overlay effect definitions unavailable", exc_info=True)
                load_effect_definitions = None
            try:
                from .runtime_paths import bundle_root
                native_path = bundle_root() / "data/overlay/native.json"
                native_contract = (json.loads(native_path.read_text(encoding="utf-8"))
                                   if native_path.stat().st_size <= 64_000 else None)
            except (ImportError, OSError, ValueError, TypeError):
                native_contract = None
            installed_hashes = {
                "gameAssemblySha256": os.environ.get("ENDFIELD_ROTATION_GAME_ASSEMBLY_SHA256", ""),
                "metadataSha256": os.environ.get("ENDFIELD_ROTATION_METADATA_SHA256", ""),
            }
            _singleton = OverlayTopicBroker(catalog=catalog, definitions_loader=load_effect_definitions, target_catalog=target_catalog,
                                             native_contract=native_contract, installed_hashes=installed_hashes)
        return _singleton
