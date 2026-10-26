from __future__ import annotations

import json
import math
import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from .packet_resolver import PacketResolveContext, PacketResolver
from .runtime_paths import bundle_root

_UNKNOWN_ENEMY_KEY = "eny_0000_unknown"
_ENEMY_KEY_RE = re.compile(r"(eny_\d{4}_[a-z0-9]+)")
_DUNGEON_ENEMY_HINT_ALIASES: dict[str, str] = {
    "indie_hard011_s": "eny_0090_wgabyss",
    "indie_hard016": "eny_0046_lbshamman",
    "indie_hard016_s": "eny_0046_lbshamman",
    "indie_hard017": "eny_0095_ethillu",
    "indie_hard017_s": "eny_0095_ethillu",
    "indie_hard018": "eny_0082_hsbear",
    "indie_hard018_s": "eny_0082_hsbear",
    "indie_hard019": "eny_0088_wgthorns",
    "indie_hard019_s": "eny_0088_wgthorns",
    "indie_hard020": "eny_0107_wgshoal2",
    "indie_hard020_s": "eny_0107_wgshoal2",
    "indie_hard021": "eny_0102_hstiger2",
    "indie_hard021_s": "eny_0102_hstiger2",
    "dung01_bossrush01_01": "eny_0051_rodin",
    "dung01_bossrush01_02": "eny_0051_rodin",
    "dung01_bossrush01_03": "eny_0051_rodin",
    "dung01_bossrush01_04": "eny_0051_rodin",
    "dung01_bossrush03_01": "eny_0052_palesent",
    "dung01_bossrush03_02": "eny_0052_palesent",
    "dung01_bossrush03_03": "eny_0052_palesent",
    "dung02_bossrush02_01": "eny_0079_nefarp2",
    "dung02_bossrush02_02": "eny_0079_nefarp2",
    "dung02_bossrush02_03": "eny_0079_nefarp2",
}


def default_trace_file() -> Path:
    import os

    return Path(os.environ.get("TEMP", ".")) / "dxg_trace.dat"


def make_archive_trace_file(log_dir: Path, *, now: datetime | None = None) -> Path:
    timestamp = now or datetime.now()
    day_dir = Path(log_dir) / timestamp.strftime("%Y%m%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    stem = f"trace_{timestamp.strftime('%Y%m%d-%H%M%S')}"
    candidate = day_dir / f"{stem}.log"
    index = 1
    while candidate.exists():
        candidate = day_dir / f"{stem}_{index:02d}.log"
        index += 1
    return candidate


def _intish(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _floatish(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _trace_string(value: Any) -> str:
    text = str(value or "")
    return json.dumps(text, ensure_ascii=False)


def _template_id(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    if not isinstance(value, dict):
        return None
    str_id = value.get("str_id")
    if isinstance(str_id, str) and str_id:
        return str_id
    int_id = _intish(value.get("int_id"))
    if int_id:
        typ = str(value.get("type") or "template").lower()
        return f"{typ}_{int_id}"
    return None


def _character_family_from_any(text: str | None) -> str:
    match = re.search(r"(chr_\d{4}_[a-z0-9]+)", str(text or ""))
    return match.group(1) if match else ""


def _enemy_family_from_any(text: str | None) -> str:
    match = _ENEMY_KEY_RE.search(str(text or ""))
    if not match:
        return ""
    enemy_key = match.group(1)
    return "" if enemy_key == _UNKNOWN_ENEMY_KEY else enemy_key


def _resource_dungeon_enemy_hints(root: Path) -> dict[str, str]:
    """Extract unambiguous dungeon -> enemy families from shipped game data."""
    mapping: dict[str, str] = {}
    item_root = root / "data" / "local_tables" / "dungeon" / "items"
    if not item_root.exists():
        return mapping
    for item_path in item_root.glob("*.json"):
        try:
            payload = json.loads(item_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        pending = [payload]
        while pending:
            node = pending.pop()
            if isinstance(node, dict):
                dungeon_id = str(node.get("dungeonId") or "").strip()
                enemy_ids = node.get("enemyIds")
                if dungeon_id and isinstance(enemy_ids, list):
                    enemy_keys = {
                        enemy_key
                        for raw_enemy_id in enemy_ids
                        if (enemy_key := _enemy_family_from_any(str(raw_enemy_id)))
                    }
                    if len(enemy_keys) == 1:
                        mapping.setdefault(dungeon_id, next(iter(enemy_keys)))
                pending.extend(node.values())
            elif isinstance(node, list):
                pending.extend(node)
    return mapping


@lru_cache(maxsize=1)
def _load_dungeon_enemy_hints() -> dict[str, str]:
    mapping = dict(_DUNGEON_ENEMY_HINT_ALIASES)
    root = bundle_root()
    for dungeon_id, enemy_key in _resource_dungeon_enemy_hints(root).items():
        mapping.setdefault(dungeon_id, enemy_key)
    path = root / "jsondata" / "Dungeon.json"
    if not path.exists():
        return mapping
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return mapping
    if not isinstance(payload, dict):
        return mapping
    for dungeon_id, raw_enemy_key in payload.items():
        enemy_key = _enemy_family_from_any(str(raw_enemy_key))
        if dungeon_id and enemy_key:
            mapping[str(dungeon_id)] = enemy_key
    return mapping


def _parse_equip_suit_counts(raw: Any) -> set[str]:
    if not isinstance(raw, str):
        return set()
    return {
        suit_id
        for suit_id, count in re.findall(r"\[([^\]]+)\]=(\d+)", raw)
        if int(count) >= 3
    }


def _blackboard_pairs(payload: Any) -> list[tuple[str, float]]:
    if not isinstance(payload, dict):
        return []
    blackboard = payload.get("blackboard")
    if not isinstance(blackboard, dict):
        return []
    pairs: list[tuple[str, float]] = []
    for key, item in blackboard.items():
        if not isinstance(item, dict):
            continue
        value = _floatish(item.get("float_value"))
        if value is None:
            value = _floatish(item.get("numeric_value"))
        if value is None:
            value = _floatish(item.get("value"))
        if value is not None:
            pairs.append((str(key), value))
    return pairs


def _modifier_uid_tokens(modifier_args: Any, field_name: str) -> list[str]:
    if not isinstance(modifier_args, dict):
        return []
    values: list[str] = []
    for item in modifier_args.get(field_name) or []:
        if not isinstance(item, dict):
            continue
        uid = str(item.get("buff_inst_id") or "")
        if uid:
            values.append(uid)
    return values


def _attr_info_tokens(battle_report_info: Any, field_name: str) -> list[str]:
    if not isinstance(battle_report_info, dict):
        return []
    values: list[str] = []
    for item in battle_report_info.get(field_name) or []:
        if not isinstance(item, dict):
            continue
        attr_type = _intish(item.get("attr_type"))
        attrs = item.get("attrs") if isinstance(item.get("attrs"), list) else []
        if attr_type is None:
            continue
        payload = ",".join(str(_floatish(value) if _floatish(value) is not None else value) for value in attrs)
        values.append(f"{attr_type}:{payload}")
    return values


def _entity_attr_tokens(attrs: Any) -> list[str]:
    if not isinstance(attrs, list):
        return []
    values: list[str] = []
    for item in attrs:
        if not isinstance(item, dict):
            continue
        attr_type = _intish(item.get("attr_type"))
        value = _floatish(item.get("value"))
        basic_value = _floatish(item.get("basic_value"))
        if attr_type is None:
            continue
        if value is None:
            value = 0.0
        if basic_value is None:
            basic_value = value
        values.append(f"{attr_type}:{value:.6g}/{basic_value:.6g}")
    return values


class TraceBridge:
    """Translate decoded packet events into the legacy dxg_trace.dat line format.

    The current tkinter overlay and exporters already consume this text format,
    so the packet pipeline writes compatible lines instead of reimplementing the
    whole overlay UI.
    """

    def __init__(self, trace_file: Path | None = None) -> None:
        self.trace_file = trace_file or default_trace_file()
        self.id_to_name: dict[int, str] = {}
        self.skill_id_by_inst: dict[int, str] = {}
        self.skill_source_by_inst: dict[int, str] = {}
        self._char_skills_emitted: set[tuple[str, tuple[str, ...]]] = set()
        self.skill_owner_by_int: dict[int, str] = {}
        self.buff_id_by_uid: dict[int, str] = {}
        self.buff_actor_ids_by_uid: dict[int, tuple[int | None, int | None]] = {}
        self.buff_values_by_uid: dict[int, dict[str, float]] = {}
        self.buff_keys_by_uid: dict[int, set[str]] = {}
        self.actor_alias_by_raw_id: dict[int, str] = {}
        self.learned_buff_mapping_by_signature: dict[tuple[int, str, tuple[str, ...]], str] = {}
        self.learned_buff_mapping_conflicts: set[tuple[int, str, tuple[str, ...]]] = set()
        self.resolver = PacketResolver()
        self.active_suits_by_char: dict[str, set[str]] = {}
        self.active_weapons_by_char: dict[str, str] = {}
        self.loadout_actor_order: list[str] = []
        self._next_loadout_actor_index = 0
        self.recent_dynamic_bb_by_inst: dict[int, list[tuple[int, float]]] = {}
        self._keyword_value_serials: dict[tuple[str, str, str], tuple[int, float | None]] = {}
        self.recent_calc_bb_by_inst: dict[int, list[tuple[int, str, float]]] = {}
        self.hit_seq = 0
        self.buff_seq = 0
        self._last_ts_ms: int | None = None
        self._pending_add_buff_ts_ms: int | None = None
        self._pending_add_buff_events: list[dict[str, Any]] = []
        self._pending_create_buff_ts_ms: int | None = None
        self._pending_create_buff_events: list[dict[str, Any]] = []
        self._pending_buff_start_ts_ms: int | None = None
        self._pending_buff_start_order: list[int] = []
        self._pending_buff_start_rows: dict[int, tuple[str, str, str, Any]] = {}
        self._battle_active = False
        self._battle_start_ts_ms: int | None = None
        self._battle_start_client_tick_ms: int | None = None
        self._official_timer_active = False
        self._official_timer_start_ts_ms: int | None = None
        self._official_timer_observed_start_ts_ms: int | None = None
        self._official_timer_game_id = ""
        self._official_timer_expected = False
        self._challenge_pass_confirmed = False
        self._current_dungeon_context: dict[str, Any] | None = None
        self._battle_dungeon_context_emitted = False
        self._current_enemy_hint = ""
        self._current_enemy_hint_source = ""
        self._has_scene_squad = False
        self.trace_file.parent.mkdir(parents=True, exist_ok=True)
        self.trace_file.write_text("", encoding="utf-8")

    def handle_event(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("type") or "")
        if event_type in {
            "SC_EFFECT_SNAPSHOT", "SC_OBJECT_LEAVE_VIEW",
            "SC_CHAR_BAG_SYNC_PERSISTENT_BUFFS", "SC_SCENE_RESET_ENTITY",
            "SC_SYNC_RACING_DUNGEON_BUFF_MODIFY", "SC_SYNC_RACING_DUNGEON_RECONNECT",
            "CAPTURE_SESSION_START", "CAPTURE_SESSION_END",
        }:
            # Overlay evidence is isolated from legacy statistics. It must not
            # advance gameplay timestamps or partition pending Buff groups.
            return
        ts_ms = _intish(event.get("timestamp_ms"))
        if event_type in {
            "GAME_CLOCK_TICK", "CS_PING", "SC_PING",
            "CS_GAME_TIME_FREEZE_START_REQ", "CS_GAME_TIME_FREEZE_END_REQ",
            "SC_GAME_TIME_FREEZE_START_RSP", "SC_GAME_TIME_FREEZE_END_RSP",
        }:
            # Clock observations are a side channel. They cannot become the
            # last gameplay timestamp or flush/partition pending Buff evidence.
            # Missing receipt timestamps remain absent instead of becoming now.
            if ts_ms is None:
                return
            if event_type == "GAME_CLOCK_TICK":
                tick = _intish(event.get("tick_ms"))
                seq = _intish(event.get("seq_id"))
                if event.get("source") == "CS_BATTLE_OP" and tick is not None and tick > 0:
                    self._append_line(ts_ms, f"GAME_CLOCK_TICK source=CS_BATTLE_OP tickMs={tick} seq={seq if seq is not None else 'unknown'}")
            elif event_type in {"CS_PING", "SC_PING"}:
                parts = [f"GAME_CLOCK_SYNC source={event_type}", f"msgId={event.get('msg_id', 'unknown')}", f"captureMs={ts_ms}"]
                for key, output_key in (("client_ts", "clientTs"), ("server_ts", "serverTs"), ("logical_ts", "logicalTs")):
                    value = _intish(event.get(key))
                    if value is not None and value >= 0:
                        parts.append(f"{output_key}={value}")
                if isinstance(event.get("raw_body_hex"), str):
                    parts.append(f"rawBodyHex={event['raw_body_hex'] or 'empty'}")
                self._append_line(ts_ms, " ".join(parts))
            elif event_type in {"CS_GAME_TIME_FREEZE_START_REQ", "CS_GAME_TIME_FREEZE_END_REQ"}:
                reason = _intish(event.get("reason"))
                raw_body = event.get("raw_body_hex")
                raw_token = (raw_body or "empty") if isinstance(raw_body, str) else "unknown"
                self._append_line(ts_ms, f"GAME_TIME_FREEZE_REQUEST source={event_type} reason={reason if reason is not None else 'unknown'} msgId={event.get('msg_id', 'unknown')} rawBodyHex={raw_token}")
            else:
                reason = _intish(event.get("reason"))
                frozen = event.get("in_time_freeze")
                raw_body = event.get("raw_body_hex")
                raw_token = (raw_body or "empty") if isinstance(raw_body, str) else "unknown"
                self._append_line(ts_ms, f"GAME_TIME_FREEZE_STATE source={event_type} inTimeFreeze={int(frozen) if type(frozen) is bool else 'unknown'} reason={reason if reason is not None else 'unknown'} msgId={event.get('msg_id', 'unknown')} rawBodyHex={raw_token}")
            return
        if ts_ms is None:
            ts_ms = int(datetime.now().timestamp() * 1000)
        self._last_ts_ms = ts_ms
        action = event.get("action") if event_type == "BattleOpTriggerAction" else None
        action_type = str(action.get("action_type") or "") if isinstance(action, dict) else ""
        is_create_buff_trigger = bool(
            event_type == "BattleOpTriggerAction"
            and isinstance(action, dict)
            and (action_type == "BattleActionCreateBuff" or "create_buff_action" in action)
        )
        if event_type != "BattleOpAddBuff":
            self._flush_pending_add_buffs()
        if not is_create_buff_trigger:
            self._flush_pending_create_buff_triggers()
        if event_type == "DUNGEON_CONTEXT":
            self._handle_dungeon_context(event, ts_ms)
        elif event_type == "CS_ENTER_DUNGEON":
            self._handle_scene_load_boundary(event_type, event, ts_ms)
        elif event_type == "CS_SCENE_LOAD_FINISH":
            self._handle_scene_load_boundary(event_type, event, ts_ms)
        elif event_type in {"CS_SCENE_SET_BATTLE", "SC_SCENE_SET_BATTLE"}:
            self._handle_scene_battle_state(event_type, event, ts_ms)
        elif event_type == "CONTRACT_TAGS":
            self._handle_contract_tags(event, ts_ms)
        elif event_type == "SC_SELF_SCENE_INFO":
            self._handle_scene_info(event, ts_ms)
        elif event_type == "SC_SELF_SCENE_INFO_SKILLS":
            self._handle_scene_info_skills(event, ts_ms)
        elif event_type == "SC_OBJECT_ENTER_VIEW":
            self._handle_object_enter(event, ts_ms)
        elif event_type == "SC_SYNC_ATTR":
            self._handle_sync_attr(event, ts_ms)
        elif event_type == "BattleOpSkillAttach":
            self._handle_skill_attach(event, ts_ms)
        elif event_type == "BattleOpSkillDetach":
            self._handle_skill_detach(event)
        elif event_type == "BattleOpSkillStartCast":
            self._handle_skill_start_cast(event, ts_ms)
        elif event_type == "BattleOpSkillEndCast":
            self._handle_skill_end_cast(event, ts_ms)
        elif event_type == "BattleOpUpdateAtbInfo":
            self._handle_update_atb_info(event, ts_ms)
        elif event_type == "BattleOpModifyPoiseValue":
            self._handle_modify_poise_value(event, ts_ms)
        elif event_type == "BattleOpModifyBattleState":
            self._handle_battle_state(event, ts_ms)
        elif event_type == "SC_RESET_BATTLE_STATUS":
            self._handle_reset_battle_status(event, ts_ms)
        elif event_type == "SC_SYNC_WEEK_RAID_SETTLEMENT":
            self._handle_week_raid_settlement(event, ts_ms)
        elif event_type in {
            "CS_GAME_MECHANICS_NTF_INST_PREPARE_FINISH",
            "CS_GAME_MECHANICS_REQ_START",
            "CS_GAME_MECHANICS_REQ_STOP",
            "SC_GAME_MECHANICS_MODIFY_INST_TIME_FREEZE",
            "SC_GAME_MECHANICS_SYNC_CHALLENGE_COMPLETE",
            "SC_GAME_MECHANICS_SYNC_CHALLENGE_START",
            "SC_GAME_MECHANICS_SYNC_COMPLETION_REWARD",
            "SC_GAME_MECHANICS_SYNC_ENTER_GAME_INST",
            "SC_GAME_MECHANICS_SYNC_LEAVE_GAME_INST",
            "SC_GAME_MECHANICS_SYNC_RESTART_GAME_INST",
        }:
            self._handle_game_mechanics_event(event, ts_ms)
        elif event_type == "BattleOpAddBuff":
            if self._pending_add_buff_ts_ms is None or self._pending_add_buff_ts_ms == ts_ms:
                self._pending_add_buff_ts_ms = ts_ms
                self._pending_add_buff_events.append(event)
            else:
                self._flush_pending_add_buffs()
                self._pending_add_buff_ts_ms = ts_ms
                self._pending_add_buff_events.append(event)
        elif event_type == "BattleOpFinishBuff":
            self._handle_finish_buff_op(event, ts_ms)
        elif event_type == "BattleOpTriggerAction":
            if is_create_buff_trigger:
                if self._pending_create_buff_ts_ms is None or self._pending_create_buff_ts_ms == ts_ms:
                    self._pending_create_buff_ts_ms = ts_ms
                    self._pending_create_buff_events.append(event)
                else:
                    self._flush_pending_create_buff_triggers()
                    self._pending_create_buff_ts_ms = ts_ms
                    self._pending_create_buff_events.append(event)
            else:
                self._handle_trigger_action(event, ts_ms)
        elif event_type == 'BattleOpTriggerKeyword':
            updates = []
            errors = []
            raw_updates = event.get('modify')
            if not isinstance(raw_updates, list):
                errors.append('missing_keyword_modify_list')
            for row in raw_updates if isinstance(raw_updates, list) else []:
                value = None
                if isinstance(row, dict) and type(row.get('modify_value')) in (int, float):
                    try:
                        value = float(row['modify_value'])
                    except (OverflowError, ValueError):
                        pass
                if (not isinstance(row, dict) or type(row.get('config_index')) is not int
                        or row['config_index'] < 0 or value is None or not math.isfinite(value)):
                    errors.append('invalid_keyword_modify_row')
                    continue
                updates.append({'configIndex': row['config_index'], 'rateAfter': value})
            observation = {
                'schemaVersion': 1, 'sessionId': event.get('session_id'),
                'ownerId': str(event['owner_id']) if type(event.get('owner_id')) in (int, str) else None,
                'generation': event.get('generation'), 'operationSeq': event.get('seq_id'),
                'clientTickMs': str(event['client_tick_tms']) if type(event.get('client_tick_tms')) in (int, str) else None,
                'buffUid': str(event['buff_inst_id']) if type(event.get('buff_inst_id')) in (int, str) else None,
                'addedBuffNumId': event.get('add_buff_num_id'), 'modify': updates,
                'operationPayloadCase': event.get('operation_payload_case'),
            }
            if observation['operationPayloadCase'] != 'trigger_keyword_op_data':
                errors.append('keyword_operation_presence_unverified')
            for field in ('ownerId', 'buffUid'):
                value = observation[field]
                if (not isinstance(value, str) or not re.fullmatch(r'[1-9][0-9]*', value)
                        or int(value) >= 2**64):
                    errors.append('invalid_' + field)
            for field in ('generation', 'operationSeq', 'addedBuffNumId'):
                if type(observation[field]) is not int or observation[field] < 0:
                    errors.append('invalid_' + field)
            if not isinstance(observation['sessionId'], str) or not observation['sessionId']:
                errors.append('missing_sessionId')
            if errors:
                observation['validationErrors'] = errors
            elif updates:
                # The native list contains consecutive absolute rate results.
                # Keep the ordinary create-row de-duplication cache current,
                # without emitting a fabricated new Buff or lifetime.
                uid = int(observation['buffUid'])
                owner = int(observation['ownerId'])
                indices = [row['configIndex'] for row in updates]
                cache_key = (observation['sessionId'], observation['ownerId'], observation['buffUid'])
                previous = self._keyword_value_serials.get(cache_key)
                seq = observation['operationSeq']
                if (uid in self.buff_values_by_uid and self.buff_actor_ids_by_uid.get(uid, (None, None))[0] == owner
                        and indices == sorted(set(indices))):
                    final_rate = updates[-1]['rateAfter']
                    if previous is None or seq > previous[0]:
                        self._keyword_value_serials[cache_key] = (seq, final_rate)
                        self.buff_values_by_uid[uid]['rate'] = final_rate
                        self.buff_keys_by_uid.setdefault(uid, set()).add('rate')
                    elif seq == previous[0] and previous[1] != final_rate:
                        self._keyword_value_serials[cache_key] = (seq, None)
                        self.buff_values_by_uid[uid].pop('rate', None)
            self._line(ts_ms, ('BUFF_KEYWORD_UPDATE_UNRESOLVED ' if errors else 'BUFF_KEYWORD_UPDATE ')
                       + json.dumps(observation, ensure_ascii=False, separators=(',', ':'), allow_nan=False))
        elif event_type == 'BattleOpSetBuffEnableState':
            states = event.get('states')
            if isinstance(states, dict):
                for uid, enabled in states.items():
                    self._line(ts_ms, 'BUFF_ENABLE ' + json.dumps({
                        'schemaVersion': 1, 'buffUid': str(uid), 'enabled': enabled,
                        'ownerId': str(event.get('owner_id')) if event.get('owner_id') is not None else None,
                        'operationSeq': event.get('seq_id'), 'clientTickMs': event.get('client_tick_tms'),
                        'generation': event.get('generation'),
                    }, separators=(',', ':')))
        elif event_type == "BattleOpEntityDie":
            self._line(ts_ms, f"ENTITY_DIE id={event.get('entity_inst_id')}")
        elif event_type == "BattleOpEntityValueModify":
            entity_inst_id = _intish(event.get("entity_inst_id"))
            hp = _floatish(event.get("hp"))
            ultimatesp = _floatish(event.get("ultimatesp"))
            delta_value = _floatish(event.get("delta_value"))
            template_int_id = _intish(event.get("template_int_id"))
            orig_template_int_id = _intish(event.get("orig_template_int_id"))
            self._line(
                ts_ms,
                (
                    f"ENTITY_VALUE seq={event.get('seq_id')} "
                    f"id={entity_inst_id if entity_inst_id is not None else 'unknown'} "
                    f"scene={event.get('scene_num_id') if event.get('scene_num_id') is not None else 'unknown'} "
                    f"hp={hp if hp is not None else 'unknown'} "
                    f"ult={ultimatesp if ultimatesp is not None else 'unknown'} "
                    f"delta={delta_value if delta_value is not None else 'unknown'} "
                    f"srcType={event.get('template_type') or 'unknown'} "
                    f"srcInt={template_int_id if template_int_id is not None else 'unknown'} "
                    f"origSrcType={event.get('orig_template_type') or 'unknown'} "
                    f"origSrcInt={orig_template_int_id if orig_template_int_id is not None else 'unknown'}"
                ),
            )
        elif event_type == "SESSION_WARNING":
            self._line(
                ts_ms,
                (
                    f"SESSION_WARNING kind={event.get('kind') or 'unknown'} "
                    f"direction={event.get('direction') or 'unknown'} "
                    f"missingBytes={event.get('missing_bytes') if event.get('missing_bytes') is not None else 'unknown'} "
                    f"gapCount={event.get('startup_tcp_gap_count') if event.get('startup_tcp_gap_count') is not None else 'unknown'}"
                ),
            )
        elif event_type == "LOADOUT":
            self._handle_loadout(event, ts_ms)

    def end_capture_session(self, *, timestamp_ms: int | None = None) -> bool:
        """Handle an active battle when its packet capture session disappears.

        Use the last observed packet timestamp by default.  Advancing to wall
        clock time here would count loading/disconnect time that was never
        observed in the game stream. War Echo is official-only: a missing
        challenge-complete packet is reported, never synthesized as an end.
        """
        ts_ms = timestamp_ms if timestamp_ms is not None else self._last_ts_ms
        if ts_ms is None:
            ts_ms = int(datetime.now().timestamp() * 1000)
        self._last_ts_ms = ts_ms
        self._flush_pending_add_buffs()
        self._flush_pending_create_buff_triggers()
        self._flush_pending_buff_starts()
        self._record_missing_official_timer(ts_ms, boundary="CAPTURE_SESSION_LOST")
        return False

    def begin_capture_session(self, *, timestamp_ms: int | None = None) -> None:
        """Reset trace state before replaying packets from a new TCP flow."""
        ts_ms = timestamp_ms if timestamp_ms is not None else int(datetime.now().timestamp() * 1000)
        self._last_ts_ms = ts_ms
        self._emit_timer_reset(ts_ms, source="CAPTURE_SESSION_START")

        self.id_to_name.clear()
        self.skill_id_by_inst.clear()
        self.skill_source_by_inst.clear()
        self._char_skills_emitted.clear()
        self.skill_owner_by_int.clear()
        self.buff_id_by_uid.clear()
        self.buff_actor_ids_by_uid.clear()
        self.buff_values_by_uid.clear()
        self.buff_keys_by_uid.clear()
        self.actor_alias_by_raw_id.clear()
        self.learned_buff_mapping_by_signature.clear()
        self.learned_buff_mapping_conflicts.clear()
        self.resolver = PacketResolver()
        self.active_suits_by_char.clear()
        self.active_weapons_by_char.clear()
        self.loadout_actor_order.clear()
        self._next_loadout_actor_index = 0
        self.recent_dynamic_bb_by_inst.clear()
        self.recent_calc_bb_by_inst.clear()
        self.hit_seq = 0
        self.buff_seq = 0
        self._pending_add_buff_ts_ms = None
        self._pending_add_buff_events.clear()
        self._pending_create_buff_ts_ms = None
        self._pending_create_buff_events.clear()
        self._pending_buff_start_ts_ms = None
        self._pending_buff_start_order.clear()
        self._pending_buff_start_rows.clear()
        self._current_dungeon_context = None
        self._current_enemy_hint = ""
        self._current_enemy_hint_source = ""
        self._has_scene_squad = False

    def _handle_dungeon_context(self, event: dict[str, Any], ts_ms: int) -> None:
        dungeon_id = str(event.get("dungeon_id") or event.get("dungeonId") or "").strip()
        if not dungeon_id:
            return
        self._current_dungeon_context = dict(event)
        self._current_dungeon_context["dungeon_id"] = dungeon_id
        self._battle_dungeon_context_emitted = False
        self._set_enemy_hint_from_dungeon_id(dungeon_id, "dungeon_context", clear_on_missing=True)
        is_calc = bool(event.get("is_calc"))
        is_pass = bool(event.get("is_pass"))
        if is_calc and is_pass:
            self._challenge_pass_confirmed = True
            self._line(
                ts_ms,
                (
                    f"BATTLE_RESULT source={event.get('source') or 'DUNGEON_CONTEXT'} "
                    f"dungeonId={dungeon_id} isCalc=1 isPass=1"
                ),
            )
        elif not self._battle_active and not self._official_timer_active:
            self._challenge_pass_confirmed = False
        self._write_dungeon_context(ts_ms, self._current_dungeon_context)

    def _write_dungeon_context(self, ts_ms: int, context: dict[str, Any]) -> None:
        dungeon_id = str(context.get("dungeon_id") or context.get("dungeonId") or "").strip()
        if not dungeon_id:
            return
        parts = [
            "DUNGEON_CONTEXT",
            f"dungeonId={dungeon_id}",
            f"source={context.get('source') or 'packet'}",
        ]
        optional_fields = (
            ("scene_num_id", "scene"),
            ("scene_id", "sceneId"),
            ("challenge_expire_ts", "challengeExpireTs"),
            ("leave_dungeon_ts", "leaveDungeonTs"),
            ("char_team_count", "charTeamCount"),
        )
        for source_key, output_key in optional_fields:
            value = context.get(source_key)
            if value is not None:
                parts.append(f"{output_key}={value}")
        for source_key, output_key in (
            ("is_reward", "isReward"),
            ("is_calc", "isCalc"),
            ("is_pass", "isPass"),
        ):
            value = context.get(source_key)
            if value is not None:
                parts.append(f"{output_key}={1 if bool(value) else 0}")
        self._line(ts_ms, " ".join(parts))

    def _handle_contract_tags(self, event: dict[str, Any], ts_ms: int) -> None:
        dungeon_id = str(event.get("dungeon_id") or event.get("dungeonId") or "").strip()
        raw_tag_ids = event.get("tag_ids") or event.get("tagIds") or event.get("display_tag_ids") or []
        tag_ids = [int(tag_id) for tag_id in raw_tag_ids if _intish(tag_id)]
        if not dungeon_id and not tag_ids:
            return
        parts = [
            "CONTRACT_TAGS",
            f"dungeonId={dungeon_id or 'unknown'}",
            f"source={event.get('source') or 'packet'}",
            f"tagIds=[{','.join(str(tag_id) for tag_id in tag_ids)}]",
        ]
        score = _intish(event.get("score"))
        if score is not None:
            parts.append(f"score={score}")
        direction = str(event.get("direction") or "")
        if direction:
            parts.append(f"direction={direction}")
        msg_id = _intish(event.get("msg_id") or event.get("msgId"))
        if msg_id is not None:
            parts.append(f"msgId={msg_id}")
        self._line(ts_ms, " ".join(parts))

    def _handle_reset_battle_status(self, event: dict[str, Any], ts_ms: int) -> None:
        reason = event.get("reason")
        inst_id = event.get("inst_id")
        battle_inst_id = event.get("battle_inst_id")
        parts = ["BATTLE_RESET_STATUS"]
        if reason is not None:
            parts.append(f"reason={reason}")
        if inst_id is not None:
            parts.append(f"inst={inst_id}")
        if battle_inst_id is not None:
            parts.append(f"battleInst={battle_inst_id}")
        self._line(ts_ms, " ".join(parts))

    def _handle_game_mechanics_event(self, event: dict[str, Any], ts_ms: int) -> None:
        event_type = str(event.get("type") or "")
        game_id = str(event.get("game_id") or event.get("cur_game_id") or "")
        self._set_enemy_hint_from_dungeon_id(game_id, "game_mechanics")
        if event_type == "SC_GAME_MECHANICS_SYNC_CHALLENGE_START":
            self._challenge_pass_confirmed = False
            self._official_timer_expected = True
            self._official_timer_active = True
            self._official_timer_observed_start_ts_ms = ts_ms
            self._official_timer_game_id = game_id
            raw_start_ts_ms = _intish(event.get("challenge_start_ts"))
            self._official_timer_start_ts_ms = raw_start_ts_ms if raw_start_ts_ms and raw_start_ts_ms > 0 else ts_ms
            parts = [
                "OFFICIAL_TIMER_START",
                "source=SC_GAME_MECHANICS_SYNC_CHALLENGE_START",
            ]
            if game_id:
                parts.append(f"gameId={game_id}")
            for source_key, output_key in (
                ("challenge_start_ts", "challengeStartTs"),
                ("challenge_expire_ts", "challengeExpireTs"),
                ("prepare_challenge_seconds", "prepareSeconds"),
            ):
                value = event.get(source_key)
                if value is not None:
                    parts.append(f"{output_key}={value}")
            self._line(ts_ms, " ".join(parts))
            return
        if event_type == "SC_GAME_MECHANICS_SYNC_CHALLENGE_COMPLETE":
            self._challenge_pass_confirmed = bool(event.get("is_pass"))
            parts = [
                "OFFICIAL_TIMER_END",
                "source=SC_GAME_MECHANICS_SYNC_CHALLENGE_COMPLETE",
            ]
            if game_id:
                parts.append(f"gameId={game_id}")
            for source_key, output_key in (
                ("is_pass", "isPass"),
                ("pass_time", "passTime"),
                ("force_leave_ts", "forceLeaveTs"),
            ):
                value = event.get(source_key)
                if value is not None:
                    if source_key == "is_pass":
                        parts.append(f"{output_key}={1 if bool(value) else 0}")
                    else:
                        parts.append(f"{output_key}={value}")
            self._line(ts_ms, " ".join(parts))
            self._clear_packet_battle_window()
            self._official_timer_expected = False
            self._official_timer_active = False
            self._official_timer_start_ts_ms = None
            self._official_timer_observed_start_ts_ms = None
            self._official_timer_game_id = ""
            return
        labels = {
            "CS_GAME_MECHANICS_NTF_INST_PREPARE_FINISH": "GAME_MECHANICS_PREPARE_FINISH",
            "CS_GAME_MECHANICS_REQ_START": "GAME_MECHANICS_REQ_START",
            "CS_GAME_MECHANICS_REQ_STOP": "GAME_MECHANICS_REQ_STOP",
            "SC_GAME_MECHANICS_MODIFY_INST_TIME_FREEZE": "GAME_MECHANICS_TIME_FREEZE",
            "SC_GAME_MECHANICS_SYNC_ENTER_GAME_INST": "GAME_INST_ENTER",
            "SC_GAME_MECHANICS_SYNC_LEAVE_GAME_INST": "GAME_INST_LEAVE",
            "SC_GAME_MECHANICS_SYNC_RESTART_GAME_INST": "GAME_INST_RESTART",
        }
        label = labels.get(event_type, event_type)
        parts = [label, f"source={event_type}"]
        if game_id:
            parts.append(f"gameId={game_id}")
        for source_key, output_key in (
            ("game_inst_id", "gameInstId"),
            ("game_unique_id", "gameUniqueId"),
            ("is_hunter_mode", "isHunterMode"),
            ("interactive_obj_id", "interactiveObjId"),
            ("npc_proxy_id", "npcProxyId"),
            ("npc_obj_id", "npcObjId"),
        ):
            value = event.get(source_key)
            if value is not None and value != "":
                if source_key == "is_hunter_mode":
                    parts.append(f"{output_key}={1 if bool(value) else 0}")
                else:
                    parts.append(f"{output_key}={value}")
        if event_type == "SC_GAME_MECHANICS_MODIFY_INST_TIME_FREEZE":
            freezes = event.get("time_freeze_infos")
            if isinstance(freezes, list):
                parts.append(f"count={len(freezes)}")
                freeze_tokens: list[str] = []
                total_freeze_ms = 0
                for freeze in freezes:
                    if not isinstance(freeze, dict):
                        continue
                    time_key = _intish(freeze.get("time_key")) or 0
                    freeze_ms = _intish(freeze.get("total_freeze_time_in_ms")) or 0
                    total_freeze_ms = max(total_freeze_ms, freeze_ms)
                    freeze_tokens.append(f"{time_key}:{freeze_ms}")
                if freeze_tokens:
                    parts.append(f"freezeInfos=[{','.join(freeze_tokens)}]")
                    parts.append(f"totalFreezeMs={total_freeze_ms}")
        self._line(ts_ms, " ".join(parts))

        if event_type == "SC_GAME_MECHANICS_SYNC_RESTART_GAME_INST":
            self._record_missing_official_timer(ts_ms, boundary=event_type)
            self._emit_timer_reset(ts_ms, source=event_type)
        elif event_type == "SC_GAME_MECHANICS_SYNC_LEAVE_GAME_INST":
            self._record_missing_official_timer(ts_ms, boundary=event_type)
            self._emit_timer_reset(ts_ms, source=event_type)

    def _handle_week_raid_settlement(self, event: dict[str, Any], ts_ms: int) -> None:
        game_id = str(event.get("game_id") or "").strip()
        if not game_id.startswith("indie_battletower"):
            return
        has_active_run = (
            self._battle_active
            or self._official_timer_expected
            or self._official_timer_active
        )
        if not has_active_run:
            return
        total_playtime = _intish(event.get("total_playtime"))
        parts = [
            "OFFICIAL_TIMER_END",
            "source=SC_SYNC_WEEK_RAID_SETTLEMENT",
            f"gameId={game_id}",
            "isPass=1",
        ]
        if total_playtime is not None and total_playtime >= 0:
            parts.append(f"passTime={total_playtime}")
        for source_key, output_key in (
            ("bp_score", "bpScore"),
            ("danger_meter", "dangerMeter"),
        ):
            value = _intish(event.get(source_key))
            if value is not None:
                parts.append(f"{output_key}={value}")
        self._line(ts_ms, " ".join(parts))
        self._challenge_pass_confirmed = True
        self._clear_packet_battle_window()
        self._official_timer_expected = False
        self._official_timer_active = False
        self._official_timer_start_ts_ms = None
        self._official_timer_observed_start_ts_ms = None
        self._official_timer_game_id = ""

    def _activity_id(self) -> str:
        dungeon_id = ""
        if self._current_dungeon_context is not None:
            dungeon_id = str(
                self._current_dungeon_context.get("dungeon_id")
                or self._current_dungeon_context.get("dungeonId")
                or ""
            )
        return self._official_timer_game_id or dungeon_id

    def _official_timer_required(self) -> bool:
        return self._activity_id().startswith("indie_battletower")

    def _clear_packet_battle_window(self) -> None:
        self._battle_active = False
        self._battle_start_ts_ms = None
        self._battle_start_client_tick_ms = None
        self._battle_dungeon_context_emitted = False

    def _record_missing_official_timer(self, ts_ms: int, *, boundary: str) -> bool:
        if not self._battle_active and not self._official_timer_expected and not self._official_timer_active:
            return False
        activity_id = self._activity_id()
        label = (
            "OFFICIAL_TIMER_MISSING"
            if self._official_timer_expected or self._official_timer_active
            else "BATTLE_TIMER_MISSING"
        )
        parts = [
            label,
            f"boundary={boundary}",
            f"packetBattleActive={1 if self._battle_active else 0}",
            f"officialExpected={1 if self._official_timer_expected else 0}",
            f"officialStartSeen={1 if self._official_timer_active else 0}",
        ]
        if activity_id:
            parts.append(f"gameId={activity_id}")
        self._line(ts_ms, " ".join(parts))
        self._clear_packet_battle_window()
        self._official_timer_expected = False
        self._official_timer_active = False
        self._official_timer_start_ts_ms = None
        self._official_timer_observed_start_ts_ms = None
        self._official_timer_game_id = ""
        self._challenge_pass_confirmed = False
        return True

    def _emit_timer_reset(self, ts_ms: int, *, source: str, scene_num_id: int | None = None) -> None:
        parts = ["GAME_TIMER_RESET", f"source={source}"]
        if scene_num_id is not None:
            parts.append(f"scene={scene_num_id}")
        self._line(ts_ms, " ".join(parts))
        self._battle_active = False
        self._battle_start_ts_ms = None
        self._battle_start_client_tick_ms = None
        self._battle_dungeon_context_emitted = False
        self._official_timer_active = False
        self._official_timer_start_ts_ms = None
        self._official_timer_observed_start_ts_ms = None
        self._official_timer_game_id = ""
        self._official_timer_expected = False
        self._challenge_pass_confirmed = False

    def _handle_scene_load_boundary(
        self,
        event_type: str,
        event: dict[str, Any],
        ts_ms: int,
    ) -> None:
        self._record_missing_official_timer(ts_ms, boundary=event_type)
        self._emit_timer_reset(
            ts_ms,
            source=event_type,
            scene_num_id=_intish(event.get("scene_num_id")),
        )

    def _handle_scene_battle_state(
        self,
        event_type: str,
        event: dict[str, Any],
        ts_ms: int,
    ) -> None:
        in_battle = bool(event.get("in_battle"))
        self._line(
            ts_ms,
            f"SCENE_BATTLE_STATE source={event_type} inBattle={1 if in_battle else 0}",
        )
        if not in_battle and self._official_timer_required() and self._battle_active:
            self._line(
                ts_ms,
                (
                    "OFFICIAL_TIMER_AWAIT "
                    f"source={event_type} gameId={self._activity_id()} "
                    f"officialStartSeen={1 if self._official_timer_active else 0}"
                ),
            )
            self._clear_packet_battle_window()

    def _handle_scene_info_skills(self, event: dict[str, Any], ts_ms: int) -> None:
        # SC_SELF_SCENE_INFO_SKILLS 携带每个角色的技能清单（skill_inst_id → template id）。
        # 施放包（SkillStartCast）只带 inst id，没有这份映射时零伤害技能（辅助大招等）
        # 在日志里只能落成 chr_xxx_skill_100849 这种运行时兜底名，下游无法归类。
        owner_template = str(event.get("owner_templateid") or "")
        owner_hint = self._character_from_template(owner_template) or ""
        skill_level_tokens: list[str] = []
        for skill in event.get("skills") or []:
            if not isinstance(skill, dict):
                continue
            skill_inst_id = _intish(skill.get("skill_inst_id"))
            if not skill_inst_id:
                continue
            resolved = self.skill_id_by_inst.get(skill_inst_id)
            if resolved is None:
                resolved = self._resolve_skill_template(
                    skill.get("template_str_id"),
                    skill.get("template_int_id"),
                    owner_hint=owner_hint,
                )
                if resolved:
                    self.skill_id_by_inst[skill_inst_id] = resolved
            skill_source = str(skill.get("skill_source") or "")
            if skill_source and skill_inst_id not in self.skill_source_by_inst:
                self.skill_source_by_inst[skill_inst_id] = skill_source
            # 技能等级落盘（供 parser v33 导出排轴 API 消费）：解析出真名用真名，
            # 否则用 int id 兜底，level 缺失的条目跳过。
            level = skill.get("level")
            if level is None:
                continue
            token_id = resolved or (
                str(skill.get("template_int_id")) if skill.get("template_int_id") is not None else ""
            )
            if token_id:
                skill_level_tokens.append(f"{token_id}:{level}")
        if owner_template and skill_level_tokens:
            signature = (owner_template, tuple(skill_level_tokens))
            if signature not in self._char_skills_emitted:
                self._char_skills_emitted.add(signature)
                self._line(
                    ts_ms,
                    (
                        f"CHAR_SKILLS owner={owner_template} "
                        f"ownerId={_intish(event.get('owner_inst_id')) or 'unknown'} "
                        f"count={len(skill_level_tokens)} skills=[{' '.join(skill_level_tokens)}]"
                    ),
                )

    def _handle_scene_info(self, event: dict[str, Any], ts_ms: int) -> None:
        members = []
        for item in event.get("char_list") or []:
            if not isinstance(item, dict):
                continue
            battle_id = _intish(item.get("battle_inst_id"))
            obj_id = _intish(item.get("id"))
            template = str(item.get("templateid") or "")
            if battle_id and template:
                self.id_to_name[battle_id] = template
            if obj_id and template:
                self.id_to_name[obj_id] = template
                members.append(f"{template}_{battle_id}")
                self.actor_alias_by_raw_id[obj_id] = template
            if battle_id and template:
                self.actor_alias_by_raw_id[battle_id] = template
                attrs = _entity_attr_tokens(item.get("attrs"))
                if attrs or item.get("level") is not None or item.get("hp") is not None:
                    self._line(
                        ts_ms,
                        (
                            f"ENTITY_STATS id={battle_id} template={template} kind=character "
                            f"level={item.get('level') if item.get('level') is not None else 'unknown'} "
                            f"hp={item.get('hp') if item.get('hp') is not None else 'unknown'} "
                            f"attrs=[{' '.join(attrs)}]"
                        ),
                    )
        if members:
            self._has_scene_squad = True
            self._line(ts_ms, f"SQUAD size={len(members)} members=[{' '.join(members)}]")

    def _handle_loadout(self, event: dict[str, Any], ts_ms: int) -> None:
        rows = event.get("rows")
        if not isinstance(rows, list) or not rows:
            return
        members = [
            f"{row.get('char')}_{row.get('char_inst_id')}"
            for row in rows
            if isinstance(row, dict) and row.get("char") and row.get("char_inst_id")
        ]
        if members and not self._has_scene_squad:
            self._line(ts_ms, f"SQUAD size={len(members)} members=[{' '.join(members)}]")
        self._refresh_loadout_skill_owners(rows)
        reason = str(event.get("reason") or "packet")
        if reason == "SC_SELF_SCENE_INFO":
            self.loadout_actor_order = [
                str(row.get("char") or "")
                for row in rows
                if isinstance(row, dict) and row.get("char")
            ]
            self._next_loadout_actor_index = 0
        else:
            self.loadout_actor_order = []
            self._next_loadout_actor_index = 0
        roster = [
            str(row.get("char_inst_id") or "")
            for row in rows
            if isinstance(row, dict) and row.get("char_inst_id")
        ]
        self._line(ts_ms, f"LOADOUT reason={reason} slotCount={len(rows)} memberCount={len(rows)} roster=[{' '.join(roster)}]")
        for row in rows:
            if not isinstance(row, dict):
                continue
            slot = int(_intish(row.get("slot")) or 0)
            char = str(row.get("char") or "")
            char_inst_id = int(_intish(row.get("char_inst_id")) or 0)
            char_level = int(_intish(row.get("char_level")) or 0)
            if char and char_inst_id and self.id_to_name.get(char_inst_id) is None:
                self.id_to_name[char_inst_id] = char
            if char and char_inst_id:
                self.actor_alias_by_raw_id[char_inst_id] = char
            potential = int(_intish(row.get("potential")) or 0)
            weapon_inst_id = int(_intish(row.get("weapon_inst_id")) or 0)
            weapon_template = str(row.get("weapon_template") or "unknown_weapon")
            weapon_sync_source = str(row.get("weapon_sync_source") or "missing")
            weapon_lv = int(_intish(row.get("weapon_lv")) or 0)
            refine = _intish(row.get("refine"))
            breakthrough = int(_intish(row.get("breakthrough")) or 0)
            weapon_base_atk = row.get("weapon_base_atk")
            weapon_base_atk_lv1 = row.get("weapon_base_atk_lv1")
            weapon_base_atk_max = row.get("weapon_base_atk_max")
            weapon_refine_stats = str(row.get("weapon_refine_stats") or "")
            weapon_refine_stats_source = str(row.get("weapon_refine_stats_source") or "unknown")
            attached_gem = int(_intish(row.get("attached_gem")) or 0)
            gem_weapon_id = int(_intish(row.get("gem_weapon_id")) or 0)
            gem_template = int(_intish(row.get("gem_template")) or 0)
            gem_terms = str(row.get("gem_terms") or "")
            weapon_source_skills = str(row.get("weapon_source_skills") or "")
            equip_inst_ids = str(row.get("equip_inst_ids") or "{}")
            equips = str(row.get("equips") or "{}")
            equip_complete = (f'equipsComplete={int(row["equipment_snapshot_complete"])} '
                              if type(row.get('equipment_snapshot_complete')) is bool else '')
            unplaced_equips = ('unplacedEquipInstIds=[' + ','.join(str(value) for value in row['unresolved_equip_inst_ids']) + '] '
                               if isinstance(row.get('unresolved_equip_inst_ids'), list) else '')
            equip_suit = str(row.get("equip_suit") or "{}")
            skill_int_ids = str(row.get("skill_int_ids") or "")
            self._line(
                ts_ms,
                (
                    f"LOADOUT slot={slot} char={char} slotCharInstId={char_inst_id} "
                    f"resolvedCharInstId={char_inst_id} character=packet charInfo.instId={char_inst_id} "
                    f"template={char} charLv={char_level} potential={potential} weaponInstId={weapon_inst_id} "
                    f"weaponTemplate={weapon_template} weaponLv={weapon_lv} refine={refine if refine is not None else 'unknown'} break={breakthrough} "
                    f"attachedGem={attached_gem} equipInsts={equip_inst_ids} equips={equips} "
                    f"{equip_complete}"
                    f"{unplaced_equips}"
                    f"equipSuit={equip_suit} skillIntIds=[{skill_int_ids}]"
                ),
            )
            self._line(
                ts_ms,
                (
                    f"LOADOUT_STATS slot={slot} char={char} weaponInstId={weapon_inst_id} "
                    f"weaponTemplate={weapon_template} weaponSync={weapon_sync_source} "
                    f"weaponBaseAtk={weapon_base_atk if weapon_base_atk is not None else 'unknown'} "
                    f"weaponBaseAtkLv1={weapon_base_atk_lv1 if weapon_base_atk_lv1 is not None else 'unknown'} "
                    f"weaponBaseAtkMax={weapon_base_atk_max if weapon_base_atk_max is not None else 'unknown'} "
                    f"weaponRefineStatsSource={weapon_refine_stats_source} "
                    f"weaponRefineStats={{{weapon_refine_stats}}} "
                    f"weaponSourceSkills={{{weapon_source_skills}}} "
                    f"gemInstId={attached_gem} gemWeaponId={gem_weapon_id} gemTemplate={gem_template} "
                    f"gemTerms={{{gem_terms}}}"
                ),
            )

    def _refresh_loadout_skill_owners(self, rows: list[Any]) -> None:
        owners_by_skill: dict[int, set[str]] = {}
        active_suits_by_char: dict[str, set[str]] = {}
        active_weapons_by_char: dict[str, str] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            char = str(row.get("char") or "")
            if not char:
                continue
            weapon_template = str(row.get("weapon_template") or "")
            if weapon_template:
                active_weapons_by_char[char] = weapon_template
            active_suits_by_char[char] = _parse_equip_suit_counts(row.get("equip_suit"))
            for skill_int_id in self._parse_skill_int_ids(row.get("skill_int_ids")):
                owners_by_skill.setdefault(skill_int_id, set()).add(char)
        if not owners_by_skill:
            self.active_suits_by_char = active_suits_by_char
            self.active_weapons_by_char = active_weapons_by_char
            return
        self.skill_owner_by_int = {
            skill_int_id: next(iter(owners))
            for skill_int_id, owners in owners_by_skill.items()
            if len(owners) == 1
        }
        self.active_suits_by_char = active_suits_by_char
        self.active_weapons_by_char = active_weapons_by_char

    @staticmethod
    def _parse_skill_int_ids(value: Any) -> list[int]:
        if isinstance(value, (list, tuple, set)):
            result = []
            for item in value:
                number = _intish(item)
                if number is not None:
                    result.append(number)
            return result
        if not isinstance(value, str):
            return []
        text = value.strip().strip("[]")
        if not text:
            return []
        result = []
        for part in text.replace(";", ",").replace(" ", ",").split(","):
            number = _intish(part.strip())
            if number is not None:
                result.append(number)
        return result

    def _handle_object_enter(self, event: dict[str, Any], ts_ms: int) -> None:
        for item in event.get("objects") or []:
            if not isinstance(item, dict):
                continue
            battle_id = _intish(item.get("battle_inst_id"))
            obj_id = _intish(item.get("obj_id"))
            template = str(item.get("templateid") or "")
            if battle_id and template:
                self.id_to_name[battle_id] = template
            if obj_id and template:
                self.id_to_name[obj_id] = template
            attrs = _entity_attr_tokens(item.get("attrs"))
            if battle_id and template and (attrs or item.get("level") is not None or item.get("hp") is not None):
                kind = str(item.get("kind") or "object")
                self._line(
                    ts_ms,
                    (
                        f"ENTITY_STATS id={battle_id} template={template} kind={kind} "
                        f"level={item.get('level') if item.get('level') is not None else 'unknown'} "
                        f"hp={item.get('hp') if item.get('hp') is not None else 'unknown'} "
                        f"attrs=[{' '.join(attrs)}]"
                    ),
                )

    def _handle_sync_attr(self, event: dict[str, Any], ts_ms: int) -> None:
        battle_id = _intish(event.get("battle_inst_id"))
        obj_id = _intish(event.get("obj_id"))
        entity_id = battle_id or obj_id
        if not entity_id:
            return
        template = str(event.get("templateid") or "")
        if not template:
            template = self.id_to_name.get(entity_id) or (self.id_to_name.get(obj_id) if obj_id else "") or "unknown"
        if battle_id and template and template != "unknown":
            self.id_to_name[battle_id] = template
            self.actor_alias_by_raw_id[battle_id] = template
        if obj_id and template and template != "unknown":
            self.id_to_name[obj_id] = template
        attrs = _entity_attr_tokens(event.get("attrs"))
        if not attrs:
            return
        self._line(
            ts_ms,
            (
                f"ENTITY_ATTR_UPDATE id={entity_id} "
                f"obj={obj_id if obj_id is not None else 'unknown'} "
                f"template={template} attrs=[{' '.join(attrs)}]"
            ),
        )

    def _handle_skill_attach(self, event: dict[str, Any], ts_ms: int) -> None:
        skill_inst_id = _intish(event.get("skill_inst_id"))
        src_inst_id = _intish(event.get("src_inst_id"))
        src_template = self.id_to_name.get(src_inst_id) if src_inst_id else None
        skill_template = self._resolve_skill_template(
            event.get("template_str_id"),
            event.get("template_int_id"),
            owner_hint=self._character_from_template(src_template) or "",
        )
        char_template = self._character_from_template(skill_template)
        if not char_template and src_inst_id:
            char_template = self._character_from_template(src_template)
            if char_template and skill_template and skill_template.startswith("skill_"):
                skill_template = f"{char_template}_{skill_template}"
        if skill_inst_id and skill_template:
            self.skill_id_by_inst[skill_inst_id] = skill_template
        skill_source = str(event.get("skill_source") or "")
        if skill_inst_id and skill_source:
            self.skill_source_by_inst[skill_inst_id] = skill_source
        if src_inst_id and char_template:
            if self.id_to_name.get(src_inst_id) is None:
                self.id_to_name[src_inst_id] = char_template
                self._line(ts_ms, f"ACTOR_MAP id={src_inst_id} template={char_template} source=skill_attach skill={skill_template}")

    def _handle_skill_detach(self, event: dict[str, Any]) -> None:
        skill_inst_id = _intish(event.get("skill_inst_id"))
        if skill_inst_id:
            self.skill_id_by_inst.pop(skill_inst_id, None)
            self.skill_source_by_inst.pop(skill_inst_id, None)

    def _handle_skill_start_cast(self, event: dict[str, Any], ts_ms: int) -> None:
        skill_inst_id = _intish(event.get("skill_inst_id"))
        if not skill_inst_id:
            return
        skill = self.skill_id_by_inst.get(skill_inst_id)
        owner_id = _intish(event.get("owner_id"))
        owner = self._name_for_id(owner_id)
        if not skill:
            skill = self._resolve_skill_template(None, skill_inst_id, owner_hint=self._character_from_template(owner) or "")
        char_template = self._character_from_template(skill) or self._character_from_template(owner)
        if owner_id and char_template and self.id_to_name.get(owner_id) is None:
            self.id_to_name[owner_id] = char_template
            owner = char_template
        seq = _intish(event.get("seq_id")) or 0
        client_tick = _intish(event.get("client_tick_tms")) or 0
        self._line(
            ts_ms,
            (
                f"SKILL_CAST_START seq={seq} startMs={client_tick} inst={skill_inst_id} "
                f"owner={owner} ownerId={owner_id if owner_id is not None else 'unknown'} "
                f"skill={skill or 'unknown_skill'} "
                f"skillSource={self.skill_source_by_inst.get(skill_inst_id) or 'unknown'} captureMs={ts_ms}"
            ),
        )

    def _handle_skill_end_cast(self, event: dict[str, Any], ts_ms: int) -> None:
        skill_inst_id = _intish(event.get("skill_inst_id"))
        if not skill_inst_id:
            return
        owner = self._name_for_id(event.get("owner_id"))
        skill = self.skill_id_by_inst.get(skill_inst_id) or self._resolve_skill_template(
            None,
            skill_inst_id,
            owner_hint=self._character_from_template(owner) or "",
        )
        seq = _intish(event.get("seq_id")) or 0
        client_tick = _intish(event.get("client_tick_tms")) or 0
        self._line(
            ts_ms,
            (
                f"SKILL_CAST_END seq={seq} endMs={client_tick} inst={skill_inst_id} "
                f"owner={owner} ownerId={event.get('owner_id') if event.get('owner_id') is not None else 'unknown'} "
                f"skill={skill or 'unknown_skill'} "
                f"skillSource={self.skill_source_by_inst.get(skill_inst_id) or 'unknown'}"
            ),
        )

    def _handle_update_atb_info(self, event: dict[str, Any], ts_ms: int) -> None:
        # 技力(atb)更新：主控角色重击(连招末段)回技力 = reason=AddValue 的正 delta。
        # 排轴据此区分主控重击 vs 非主控 AI 重击。owner 归到角色，source 归到触发技能。
        owner_id = _intish(event.get("owner_id"))
        owner = self._name_for_id(owner_id)
        owner_hint = self._character_from_template(owner) or ""
        source_skill = self._resolve_skill_template(
            event.get("template_str_id"),
            event.get("template_int_id"),
            owner_hint=owner_hint,
        )
        orig_source_skill = self._resolve_skill_template(
            event.get("orig_template_str_id"),
            event.get("orig_template_int_id"),
            owner_hint=owner_hint,
        )
        seq = _intish(event.get("seq_id")) or 0
        client_tick = _intish(event.get("client_tick_tms")) or 0
        atb_value = _floatish(event.get("atb_value"))
        delta = _floatish(event.get("delta_value"))
        parts = [
            "ATB_UPDATE",
            f"seq={seq}",
            f"tick={client_tick}",
            f"owner={owner}",
            f"ownerId={owner_id if owner_id is not None else 'unknown'}",
            f"reason={event.get('reason') or 'unknown'}",
            f"atb={atb_value:.6g}" if atb_value is not None else "atb=unknown",
            f"delta={delta:.6g}" if delta is not None else "delta=unknown",
            f"source={source_skill or event.get('template_str_id') or 'unknown'}",
            f"origSource={orig_source_skill or event.get('orig_template_str_id') or 'unknown'}",
        ]
        self._line(ts_ms, " ".join(parts))

    def _handle_modify_poise_value(self, event: dict[str, Any], ts_ms: int) -> None:
        attacker_id = _intish(event.get("attacker_inst_id"))
        attacker = self._name_for_id(attacker_id)
        owner_hint = self._character_from_template(attacker) or ""
        source_skill = self._resolve_skill_template(
            event.get("template_str_id"),
            event.get("template_int_id"),
            owner_hint=owner_hint,
        )
        original_source_skill = self._resolve_skill_template(
            event.get("orig_template_str_id"),
            event.get("orig_template_int_id"),
            owner_hint=owner_hint,
        )
        seq = _intish(event.get("seq_id")) or 0
        client_tick = _intish(event.get("client_tick_tms")) or 0
        template_int_id = _intish(event.get("template_int_id"))
        orig_template_int_id = _intish(event.get("orig_template_int_id"))
        action_id = _intish(event.get("action_id"))
        value = _floatish(event.get("value"))
        cur_poise_value = _floatish(event.get("cur_poise_value"))
        parts = [
            "POISE_V1",
            f"seq={seq}",
            f"tick={client_tick}",
            f"type={event.get('modify_type') or 'unknown'}",
            f"value={value:.6g}" if value is not None else "value=unknown",
            f"cur={cur_poise_value:.6g}" if cur_poise_value is not None else "cur=unknown",
            f"attacker={attacker}",
            f"attackerId={attacker_id if attacker_id is not None else 'unknown'}",
            f"ownerType={event.get('owner_type') or 'unknown'}",
            f"sourceType={event.get('template_type') or 'unknown'}",
            f"source={source_skill or event.get('template_str_id') or 'unknown'}",
            f"sourceInt={template_int_id if template_int_id is not None else 'unknown'}",
            f"origSourceType={event.get('orig_template_type') or 'unknown'}",
            f"origSource={original_source_skill or event.get('orig_template_str_id') or 'unknown'}",
            f"origSourceInt={orig_template_int_id if orig_template_int_id is not None else 'unknown'}",
            f"actionId={action_id if action_id is not None else 'unknown'}",
        ]
        self._line(ts_ms, " ".join(parts))

    def _handle_battle_state(self, event: dict[str, Any], ts_ms: int) -> None:
        is_in_battle = bool(event.get("is_in_battle"))
        if is_in_battle and not self._battle_active:
            is_war_echo_continuation = (
                self._official_timer_required() and self._official_timer_expected
            )
            self._challenge_pass_confirmed = False
            self._battle_active = True
            if self._official_timer_required():
                self._official_timer_expected = True
                self._official_timer_game_id = self._activity_id()
                if self._official_timer_observed_start_ts_ms is None:
                    self._official_timer_observed_start_ts_ms = ts_ms
            self._battle_start_ts_ms = ts_ms
            seq = _intish(event.get("seq_id")) or 0
            raw_client_tick = _intish(event.get("client_tick_tms"))
            client_tick = raw_client_tick or 0
            self._battle_start_client_tick_ms = raw_client_tick
            if self._current_dungeon_context is not None and not self._battle_dungeon_context_emitted:
                self._write_dungeon_context(ts_ms, self._current_dungeon_context)
                self._battle_dungeon_context_emitted = True
            if is_war_echo_continuation:
                self._line(
                    ts_ms,
                    (
                        "BATTLE_PHASE_START "
                        f"seq={seq} source=BattleOpModifyBattleState "
                        f"gameId={self._activity_id()}"
                    ),
                )
                return
            timer_source = "PacketBattleState" if raw_client_tick is not None else "BattleOpModifyBattleState"
            official = 1 if raw_client_tick is not None else 0
            self._line(
                ts_ms,
                f"GAME_TIMER_START seq={seq} source={timer_source} startMs={client_tick} expireMs=0 official={official}",
            )
        elif not is_in_battle and self._battle_active and self._official_timer_required():
            self._line(
                ts_ms,
                (
                    "OFFICIAL_TIMER_AWAIT source=BattleOpModifyBattleState "
                    f"gameId={self._activity_id()} "
                    f"officialStartSeen={1 if self._official_timer_active else 0}"
                ),
            )
            self._clear_packet_battle_window()
        elif not is_in_battle and self._battle_active:
            self._battle_active = False
            seq = _intish(event.get("seq_id")) or 0
            raw_client_tick = _intish(event.get("client_tick_tms"))
            client_tick = raw_client_tick or 0
            start_tick = self._battle_start_client_tick_ms if self._battle_start_client_tick_ms is not None else client_tick
            packet_elapsed_ms = (
                client_tick - start_tick
                if raw_client_tick is not None and self._battle_start_client_tick_ms is not None
                else None
            )
            wall_elapsed_ms = max(ts_ms - self._battle_start_ts_ms, 0) if self._battle_start_ts_ms is not None else 0
            if packet_elapsed_ms is not None and packet_elapsed_ms >= 0:
                timer_source = "PacketBattleState"
                official = 1
                elapsed_ms = wall_elapsed_ms
            else:
                timer_source = "BattleOpModifyBattleState"
                official = 0
                elapsed_ms = wall_elapsed_ms
            packet_elapsed_token = (
                f" packetElapsedMs={packet_elapsed_ms}"
                if packet_elapsed_ms is not None and packet_elapsed_ms >= 0
                else ""
            )
            wall_elapsed_token = (
                f" wallElapsedMs={wall_elapsed_ms}"
                if wall_elapsed_ms > 0 and wall_elapsed_ms != elapsed_ms
                else ""
            )
            self._line(
                ts_ms,
                (
                    f"GAME_TIMER_END seq={seq} source={timer_source} "
                    f"elapsedMs={elapsed_ms} startMs={start_tick} endMs={client_tick} "
                    f"expireMs=0 sane={1 if elapsed_ms > 0 else 0} official={official}"
                    f"{packet_elapsed_token}{wall_elapsed_token}"
                ),
            )
            self._battle_start_ts_ms = None
            self._battle_start_client_tick_ms = None
            self._battle_dungeon_context_emitted = False

    def _handle_add_buff_op(self, event: dict[str, Any], ts_ms: int) -> None:
        buff_uid = _intish(event.get("buff_inst_id"))
        if not buff_uid:
            return
        buff_id, owner, src, src_id, target_id = self._resolve_add_buff_event(event, ts_ms)
        existing = self.buff_id_by_uid.get(buff_uid, "")
        if existing and existing != "unknown_buff" and not existing.isdigit():
            self._write_buff_start(ts_ms, existing, buff_uid, owner, src, event.get("assigned_items"), target_id=target_id, src_id=src_id, creation_event=event)
            return
        self._remember_actor_alias_for_buff(src_id, target_id, _intish(event.get("int_id")), buff_id)
        self._write_buff_start(ts_ms, buff_id, buff_uid, owner, src, event.get("assigned_items"), target_id=target_id, src_id=src_id, creation_event=event)

    def _resolve_add_buff_event(self, event: dict[str, Any], ts_ms: int) -> tuple[str, str, str, int | None, int | None]:
        src_id = _intish(event.get("src_inst_id"))
        target_id = _intish(event.get("target_inst_id"))
        if src_id and target_id and src_id == target_id:
            self._maybe_map_next_loadout_actor(src_id, ts_ms, source="bootstrap_buff")
        owner = self._name_for_event_id(target_id)
        src = self._name_for_event_id(src_id)
        buff_id = self._resolve_buff_template(
            event.get("str_id"),
            event.get("int_id"),
            owner=owner,
            src=src,
            assigned_items=event.get("assigned_items"),
        )
        if not buff_id or buff_id == "unknown_buff" or buff_id.isdigit():
            learned = self._resolve_learned_buff_mapping(
                _intish(event.get("int_id")),
                owner,
                src,
                event.get("assigned_items"),
                target_id=target_id,
                src_id=src_id,
            )
            if learned:
                buff_id = learned
        if (
            (not buff_id or buff_id == "unknown_buff" or buff_id.isdigit())
            and (src_id or target_id)
        ):
            alias_owner = self._actor_alias_by_id(target_id)
            alias_src = self._actor_alias_by_id(src_id)
            if alias_owner != owner or alias_src != src:
                retry = self._resolve_buff_template(
                    event.get("str_id"),
                    event.get("int_id"),
                    owner=alias_owner,
                    src=alias_src,
                    assigned_items=event.get("assigned_items"),
                )
                if (
                    retry
                    and retry != buff_id
                    and retry != "unknown_buff"
                    and (not retry.isdigit() or buff_id.isdigit())
                ):
                    buff_id = retry
                    owner = alias_owner
                    src = alias_src
        self._remember_learned_buff_mapping(
            _intish(event.get("int_id")),
            owner,
            src,
            event.get("assigned_items"),
            buff_id,
            target_id=target_id,
            src_id=src_id,
        )
        return buff_id, owner, src, src_id, target_id

    def _flush_pending_add_buffs(self) -> None:
        if not self._pending_add_buff_events:
            self._pending_add_buff_ts_ms = None
            return
        ts_ms = self._pending_add_buff_ts_ms
        if ts_ms is None:
            ts_ms = self._last_ts_ms
        if ts_ms is None:
            ts_ms = int(datetime.now().timestamp() * 1000)
        resolved_rows: list[dict[str, Any]] = []
        for event in self._pending_add_buff_events:
            buff_uid = _intish(event.get("buff_inst_id"))
            if not buff_uid:
                continue
            buff_id, owner, src, src_id, target_id = self._resolve_add_buff_event(event, ts_ms)
            resolved_rows.append(
                {
                    "event": event,
                    "buff_uid": buff_uid,
                    "src_id": src_id,
                    "target_id": target_id,
                    "owner": owner,
                    "src": src,
                    "buff_id": buff_id,
                }
            )

        self._apply_group_companion_inference(resolved_rows)

        for row in resolved_rows:
            event = row["event"]
            buff_id = str(row["buff_id"])
            owner = str(row["owner"])
            src = str(row["src"])
            self._remember_learned_buff_mapping(
                _intish(event.get("int_id")),
                owner,
                src,
                event.get("assigned_items"),
                buff_id,
                target_id=row.get("target_id"),
                src_id=row.get("src_id"),
            )
            self._remember_actor_alias_for_buff(
                row.get("src_id"),
                row.get("target_id"),
                _intish(event.get("int_id")),
                buff_id,
            )
            self._write_buff_start(ts_ms, buff_id, int(row["buff_uid"]), owner, src, event.get("assigned_items"), target_id=row.get("target_id"), src_id=row.get("src_id"), creation_event=event)

        self._pending_add_buff_events.clear()
        self._pending_add_buff_ts_ms = None

    def _flush_pending_create_buff_triggers(self) -> None:
        if self._pending_create_buff_events:
            ts_ms = self._pending_create_buff_ts_ms
            if ts_ms is None:
                ts_ms = self._last_ts_ms
            if ts_ms is None:
                raise ValueError("pending buff actions have no timestamp")
            self._handle_grouped_create_buff_events(self._pending_create_buff_events, ts_ms)
        self._pending_create_buff_events.clear()
        self._pending_create_buff_ts_ms = None

    def _handle_grouped_create_buff_events(self, events: list[dict[str, Any]], ts_ms: int) -> None:
        # Preserve each operation's phase and order, including Execute/End
        # pairs for the same instance in one timestamp group.
        for event in events:
            action = event.get("action")
            if isinstance(action, dict):
                self._write_create_buffs(ts_ms, event, action.get("create_buff_action"))

    def _apply_group_companion_inference(self, rows: list[dict[str, Any]]) -> None:
        return

    def _handle_finish_buff_op(self, event: dict[str, Any], ts_ms: int) -> None:
        buff_uid = _intish(event.get("buff_inst_id"))
        if not buff_uid:
            return
        buff_id = self.buff_id_by_uid.get(buff_uid, "")
        self.buff_seq += 1
        self._line(ts_ms, f'BUFF_END #{self.buff_seq} id="{buff_id}" uid={buff_uid}')
        self.buff_id_by_uid.pop(buff_uid, None)
        self.buff_actor_ids_by_uid.pop(buff_uid, None)
        self.buff_values_by_uid.pop(buff_uid, None)
        self.buff_keys_by_uid.pop(buff_uid, None)

    def _handle_trigger_action(self, event: dict[str, Any], ts_ms: int) -> None:
        action = event.get("action")
        if not isinstance(action, dict):
            return
        action_type = str(action.get("action_type") or "")
        if action_type in {"BattleActionModifyDynamicBlackboard", "BattleActionRefreshBuffAttrModifierValue",
                           "BattleActionReadSkillActionToBb"}:
            template = event.get("template_id")
            template = template if isinstance(template, dict) else {}
            template_cases = [key for key in ("int_id", "str_id") if key in template]
            template_case = template_cases[0] if len(template_cases) == 1 else None
            identity = {
                "schemaVersion": 1, "sessionId": event.get("session_id"),
                "operationSeq": event.get("seq_id"),
                "operationOwnerId": event.get("operation_owner_id"),
                "ownerType": event.get("owner_type"),
                "ownerId": event.get("trigger_owner_id"),
                "environmentInstId": event.get("trigger_inst_id"),
                "generation": event.get("generation"),
                "clientTickMs": (str(event["client_tick_tms"])
                                 if event.get("client_tick_tms") is not None else None),
                "templateType": template.get("type"), "templateIdCase": template_case,
                "templateIntId": template.get("int_id"), "templateStrId": template.get("str_id"),
                "actionId": action.get("action_id"), "triggerType": event.get("trigger_type"),
                "actionType": {"BattleActionModifyDynamicBlackboard": 7,
                               "BattleActionRefreshBuffAttrModifierValue": 31,
                               "BattleActionReadSkillActionToBb": 35}[action_type],
                "actionRuntimeCase": event.get("action_runtime_case"),
                "operationPayloadCase": event.get("operation_payload_case"),
            }
            # Older decoded NDJSON preserved these fields but not the later
            # envelope. Keep original names without inventing missing metadata.
            if all(event.get(key) is None for key in ('operation_owner_id', 'trigger_owner_id', 'trigger_inst_id', 'generation', 'template_id')):
                identity['eventContext'] = {key: event[key] for key in (
                    'owner_id', 'inst_id', 'owner_type', 'template_type',
                    'template_int_id', 'template_str_id', 'seq_id') if key in event}
            for field in ("operationOwnerId", "ownerId", "environmentInstId"):
                value = identity[field]
                if type(value) in (int, str):
                    identity[field] = str(value)
            if action_type == "BattleActionReadSkillActionToBb":
                # Preserve the packet's computed values and all detail rows.
                # The matching typed action defines their meaning (e.g. combo
                # layers/rate); this event alone does not authorize rDPS.
                runtime_field = "read_skill_setting_to_bb_action"
                runtime = action.get(runtime_field)
                errors = []
                if ({key for key in action if key.endswith("_action")} != {runtime_field}
                        or not isinstance(runtime, dict)
                        or not isinstance(runtime.get("runtime_details"), list)):
                    errors.append("invalid_skill_setting_runtime")
                if event.get("action_runtime_case") != runtime_field:
                    errors.append("skill_setting_runtime_presence_unverified")
                if event.get("operation_payload_case") != "trigger_action_op_data":
                    errors.append("trigger_operation_presence_unverified")
                if template_case is None or event.get("template_id_case") != template_case:
                    errors.append("invalid_template_oneof")
                identity["runtime"] = runtime
                identity["actionSerialization"] = event.get("action_serialization")
                identity["buffUid"] = (identity["environmentInstId"]
                    if type(identity["ownerType"]) in (int, str) and str(identity["ownerType"]) == "1"
                    and template.get("type") == "Buff"
                    and identity["environmentInstId"] not in (None, "0") else None)
                if errors:
                    identity["validationErrors"] = errors
                self._line(ts_ms, ("BB_READ_SKILL_SETTING_UNRESOLVED " if errors else "BB_READ_SKILL_SETTING ")
                           + json.dumps(identity, ensure_ascii=False, separators=(",", ":"), allow_nan=False))
                return
            if action_type == "BattleActionRefreshBuffAttrModifierValue":
                errors = []
                runtime_field = "refresh_buff_attr_modifier_value_action"
                runtime_fields = {key for key in action if key.endswith("_action")}
                if runtime_fields != {runtime_field} or action.get(runtime_field) != {}:
                    errors.append("invalid_refresh_runtime_oneof")
                if event.get("action_runtime_case") != runtime_field:
                    errors.append("refresh_runtime_presence_unverified")
                if event.get("operation_payload_case") != "trigger_action_op_data":
                    errors.append("trigger_operation_presence_unverified")
                if template_case is None or event.get("template_id_case") != template_case:
                    errors.append("invalid_template_oneof")
                if template_case == "int_id" and type(template.get("int_id")) is not int:
                    errors.append("invalid_template_int_id")
                if template_case == "str_id" and not isinstance(template.get("str_id"), str):
                    errors.append("invalid_template_str_id")
                if type(identity["ownerType"]) not in (int, str) or str(identity["ownerType"]) not in {"0", "1", "2", "3"}:
                    errors.append("invalid_ownerType")
                for field in ("operationOwnerId", "ownerId", "environmentInstId"):
                    value = identity[field]
                    if (not isinstance(value, str) or not value.isascii() or not value.isdecimal()
                            or not 0 <= int(value) < 2**64):
                        errors.append("invalid_" + field)
                for field in ("operationSeq", "generation", "actionId", "triggerType"):
                    if type(identity[field]) is not int:
                        errors.append("invalid_" + field)
                tick = event.get("client_tick_tms")
                if (type(tick) not in (int, str)
                        or not re.fullmatch(r"-?[0-9]+", str(tick))
                        or not -(2**63) <= int(tick) < 2**63):
                    errors.append("invalid_clientTickMs")
                if not isinstance(identity["sessionId"], str) or not identity["sessionId"]:
                    errors.append("missing_sessionId")
                # A skill or standalone environment can report this action too;
                # its environment ID must never be relabeled as a Buff UID.
                identity["buffUid"] = (identity["environmentInstId"]
                    if type(identity["ownerType"]) in (int, str) and str(identity["ownerType"]) == "1"
                    and template.get("type") == "Buff"
                    and identity["environmentInstId"] not in (None, "0") else None)
                if errors:
                    identity["validationErrors"] = errors
                self._line(ts_ms, ("BUFF_ATTR_REFRESH_UNRESOLVED " if errors else "BUFF_ATTR_REFRESH ")
                           + json.dumps(identity, ensure_ascii=False, separators=(",", ":"), allow_nan=False))
                return
        if action_type == "BattleActionModifyDynamicBlackboard":
            runtime_field = "modify_dynamic_blackboard_action"
            if ({key for key in action if key.endswith("_action")} != {runtime_field}
                    or event.get("action_runtime_case") not in (None, runtime_field)):
                return
            inst_id = _intish(event.get("inst_id"))
            action_id = int(_intish(action.get("action_id")) or 0)
            modify_action = action.get("modify_dynamic_blackboard_action")
            if inst_id and isinstance(modify_action, dict):
                raw_value = modify_action.get("client_value")
                value = None
                if type(raw_value) in (int, float):
                    try:
                        value = float(raw_value)
                    except (OverflowError, ValueError):
                        pass
                if value is not None and math.isfinite(value):
                    rows = self.recent_dynamic_bb_by_inst.setdefault(inst_id, [])
                    rows.append((action_id, value))
                    del rows[:-8]
                    self._write_blackboard_trace(
                        ts_ms,
                        "BB_MODIFY_DYNAMIC",
                        event,
                        action_id,
                        f"value={value!r} identity=" + json.dumps(
                            identity, ensure_ascii=False, separators=(",", ":"), allow_nan=False),
                    )
        if action_type == "BattleActionSimpleCalcBb":
            inst_id = _intish(event.get("inst_id"))
            action_id = int(_intish(action.get("action_id")) or 0)
            calc_action = action.get("simple_calc_bb_action")
            if inst_id and isinstance(calc_action, dict):
                target_key = str(calc_action.get("client_target_key") or "")
                final_value = _floatish(calc_action.get("client_final_value"))
                if target_key and final_value is not None:
                    rows = self.recent_calc_bb_by_inst.setdefault(inst_id, [])
                    rows.append((action_id, target_key, final_value))
                    del rows[:-12]
                    value_a = _floatish(calc_action.get("client_value_a"))
                    value_b = _floatish(calc_action.get("client_value_b"))
                    parts = [
                        f"key={target_key}",
                        f"final={final_value:.6g}",
                    ]
                    if value_a is not None:
                        parts.append(f"valueA={value_a:.6g}")
                    if value_b is not None:
                        parts.append(f"valueB={value_b:.6g}")
                    self._write_blackboard_trace(
                        ts_ms,
                        "BB_SIMPLE_CALC",
                        event,
                        action_id,
                        " ".join(parts),
                    )
        if action_type == "BattleActionMultiModifyBb" or "multi_modify_bb_action" in action:
            action_id = int(_intish(action.get("action_id")) or 0)
            modify_action = action.get("multi_modify_bb_action")
            if isinstance(modify_action, dict):
                key = str(modify_action.get("key") or "")
                value = _floatish(modify_action.get("value"))
                if key or value is not None:
                    parts = []
                    if key:
                        parts.append(f"key={key}")
                    if value is not None:
                        parts.append(f"value={value:.6g}")
                    self._write_blackboard_trace(
                        ts_ms,
                        "BB_MULTI_MODIFY",
                        event,
                        action_id,
                        " ".join(parts),
                    )
        if action_type == "BattleActionSetBlackboardFromPreset" or "set_blackboard_from_preset_action" in action:
            action_id = int(_intish(action.get("action_id")) or 0)
            preset_action = action.get("set_blackboard_from_preset_action")
            if isinstance(preset_action, dict):
                modified_value = preset_action.get("modified_value")
                if modified_value:
                    self._write_blackboard_trace(
                        ts_ms,
                        "BB_SET_PRESET",
                        event,
                        action_id,
                        f"modifiedValue={_trace_string(modified_value)}",
                    )
        if action_type == "BattleActionLaunchProjectile" or "launch_projectile_action" in action:
            self._handle_launch_projectile(ts_ms, event, action.get("launch_projectile_action") or {})
        if action_type == "BattleActionSpawnAbilityEntity" or "spawn_ability_entity_action" in action:
            self._handle_spawn_ability_entity(ts_ms, event, action.get("spawn_ability_entity_action") or {})
        if action_type == "BattleActionDamage" or "damage_action" in action:
            self._write_damage(ts_ms, event, action.get("damage_action") or {})
        if action_type == "BattleActionFinishBuff" or "finish_buff_action" in action:
            self._write_finish_buffs(ts_ms, action.get("finish_buff_action") or {})

    def _trigger_skill_template(self, event: dict[str, Any], owner_name: str = "") -> str:
        skill = str(event.get("template_str_id") or "")
        if skill:
            return skill
        number = _intish(event.get("template_int_id"))
        if number is None:
            return "unknown_skill"
        namespace = str(event.get("template_type") or "").lower()
        if namespace == "buff":
            return self.resolver.resolve_buff(int_id=number)
        if namespace == "skill":
            return self.resolver.resolve_skill(int_id=number) or f"skill_{number}"
        return f"skill_{number}"

    def _write_blackboard_trace(
        self,
        ts_ms: int,
        label: str,
        event: dict[str, Any],
        action_id: int,
        extra: str,
    ) -> None:
        inst_id = _intish(event.get("inst_id"))
        owner_id = _intish(event.get("owner_id"))
        owner = self._name_for_id(owner_id)
        skill = self._trigger_skill_template(event, owner)
        seq = _intish(event.get("seq_id")) or 0
        client_tick = _intish(event.get("client_tick_tms")) or 0
        self._line(
            ts_ms,
            (
                f"{label} seq={seq} tick={client_tick} inst={inst_id if inst_id is not None else 'unknown'} "
                f"ownerId={owner_id if owner_id is not None else 'unknown'} owner={owner} "
                f"skill={skill} actionId={action_id} {extra}"
            ),
        )

    def _handle_spawn_ability_entity(self, ts_ms: int, event: dict[str, Any], spawn_action: Any) -> None:
        if not isinstance(spawn_action, dict):
            return
        owner_id = _intish(event.get("owner_id"))
        owner = self._name_for_id(owner_id)
        skill = self._trigger_skill_template(event, owner)
        action_payload = event.get("action") if isinstance(event.get("action"), dict) else {}
        action_id = int(_intish(action_payload.get("action_id")) or 0)
        seq = _intish(event.get("seq_id")) or 0
        client_tick = _intish(event.get("client_tick_tms")) or 0
        inst_id = _intish(event.get("inst_id"))
        template_int_id = _intish(event.get("template_int_id"))
        details = spawn_action.get("details") or []
        if not isinstance(details, list):
            return
        for detail in details:
            if not isinstance(detail, dict):
                continue
            entity_id = _intish(detail.get("client_ability_entity_id"))
            source_id = _intish(detail.get("source_id")) or owner_id
            source = self._name_for_id(source_id)
            pos = detail.get("init_pos") if isinstance(detail.get("init_pos"), dict) else {}
            rot = detail.get("rotation") if isinstance(detail.get("rotation"), dict) else {}
            pos_text = ",".join(
                str(_floatish(pos.get(axis)) if _floatish(pos.get(axis)) is not None else "unknown")
                for axis in ("x", "y", "z")
            )
            rot_text = ",".join(
                str(_floatish(rot.get(axis)) if _floatish(rot.get(axis)) is not None else "unknown")
                for axis in ("x", "y", "z")
            )
            self._line(
                ts_ms,
                (
                    f"ABILITY_ENTITY_SPAWN seq={seq} tick={client_tick} actionId={action_id} "
                    f"inst={inst_id if inst_id is not None else 'unknown'} skill={skill} "
                    f"templateIntId={template_int_id if template_int_id is not None else 'unknown'} "
                    f"ownerId={owner_id if owner_id is not None else 'unknown'} owner={owner} "
                    f"sourceId={source_id if source_id is not None else 'unknown'} source={source} "
                    f"entityId={entity_id if entity_id is not None else 'unknown'} "
                    f"pos={pos_text} rot={rot_text}"
                ),
            )

    def _handle_launch_projectile(self, ts_ms: int, event: dict[str, Any], launch_action: Any) -> None:
        if not isinstance(launch_action, dict):
            return
        source_id = _intish(launch_action.get("source_id")) or _intish(event.get("owner_id"))
        source_name = self.id_to_name.get(source_id) if source_id else ""
        skill = self._resolve_skill_template(
            event.get("template_str_id"),
            event.get("template_int_id"),
            owner_hint=self._character_from_template(source_name) or "",
        )
        char_template = self._character_from_template(skill)
        if not char_template and source_id:
            char_template = self._character_from_template(source_name)
        if not char_template:
            return
        if source_id and self.id_to_name.get(source_id) is None:
            self.id_to_name[source_id] = char_template
            self._line(ts_ms, f"ACTOR_MAP id={source_id} template={char_template} source=launch_source skill={skill}")
        for detail in launch_action.get("details") or []:
            if not isinstance(detail, dict):
                continue
            projectile_id = (
                _intish(detail.get("client_projectile_id"))
                or _intish(detail.get("projectile_id"))
                or _intish(detail.get("inst_id"))
            )
            if projectile_id and self.id_to_name.get(projectile_id) is None:
                self.id_to_name[projectile_id] = char_template
                self._line(ts_ms, f"ACTOR_MAP id={projectile_id} template={char_template} source=launch_projectile skill={skill}")

    def _write_damage(self, ts_ms: int, event: dict[str, Any], damage_action: Any) -> None:
        if not isinstance(damage_action, dict):
            return
        attacker_id = _intish(damage_action.get("attacker_id")) or _intish(event.get("owner_id"))
        attacker = self._name_for_id(attacker_id)
        skill = self._trigger_skill_template(event, attacker)
        skill_level = int(_intish(event.get("level")) or 0)
        template_int_id = int(_intish(event.get("template_int_id")) or 0)
        action_payload = event.get("action") if isinstance(event.get("action"), dict) else {}
        action_id = _intish(action_payload.get("action_id"))
        original_template = damage_action.get("original_source_template_id")
        original_template_int_id = int(_intish((original_template or {}).get("int_id")) or 0) if isinstance(original_template, dict) else 0
        original_source_skill = self._resolve_skill_template(
            (original_template or {}).get("str_id") if isinstance(original_template, dict) else None,
            original_template_int_id,
        )
        # Only the explicit original-source template is identity evidence for
        # an otherwise unknown attacker ID. The event's display skill alone
        # must not fabricate an actor mapping.
        actor_template = self._character_from_template(original_source_skill)
        if attacker_id and actor_template and self.id_to_name.get(attacker_id) is None:
            self.id_to_name[attacker_id] = actor_template
            self._line(
                ts_ms,
                f"ACTOR_MAP id={attacker_id} template={actor_template} "
                f"source=damage_original_skill skill={original_source_skill or skill}",
            )
        attacker = self._name_for_id(attacker_id)
        event_inst_id = int(_intish(event.get("inst_id")) or 0)
        dynamic_bb_rows = list(self.recent_dynamic_bb_by_inst.get(event_inst_id, [])) if event_inst_id else []
        dynamic_bb_text = ",".join(f"{bb_action}:{bb_value:.6g}" for bb_action, bb_value in dynamic_bb_rows)
        calc_bb_rows = list(self.recent_calc_bb_by_inst.get(event_inst_id, [])) if event_inst_id else []
        calc_bb_text = ",".join(
            f"{bb_action}:{bb_key}={bb_value:.6g}"
            for bb_action, bb_key, bb_value in calc_bb_rows
        )
        details = damage_action.get("details") or []
        if not isinstance(details, list):
            return
        detail_presence_rows = event.get("damage_detail_presence") if isinstance(event.get("damage_detail_presence"), list) else []
        for detail_index, detail in enumerate(details):
            if not isinstance(detail, dict):
                continue
            raw_value = abs(_floatish(detail.get("value")) or 0.0)
            hit = int(raw_value + 0.5)
            if hit <= 0:
                continue
            target_id = _intish(detail.get("target_id"))
            target = self._name_for_damage_target(target_id, attacker)
            cur_hp = _floatish(detail.get("cur_hp")) or 0.0
            is_crit = bool(detail.get("is_crit"))
            damage_unit_index = _intish(detail.get("damage_unit_index"))
            part_inst_id = int(_intish(detail.get("part_inst_id")) or 0)
            detail_presence = (
                detail_presence_rows[detail_index]
                if detail_index < len(detail_presence_rows) and isinstance(detail_presence_rows[detail_index], dict)
                else {}
            )
            calc_args = detail.get("calculate_damage_check_args")
            calc_tokens: list[str] = []
            if isinstance(calc_args, dict):
                calc_field_names = {
                    "client_calc_result_value": "clientCalc",
                    "client_final_damage_scale": "clientFinalScale",
                    "client_attack_value": "clientAttack",
                    "client_crit_add_scale": "clientCritAdd",
                    "client_def_attr_defender_def": "clientDef",
                    "client_def_resistance": "clientDefResistance",
                    "client_dmg_type_resistance": "clientDmgTypeResistance",
                    "client_defender_poise_factor": "clientPoiseFactor",
                    "client_ignite_damage_scalar": "clientIgniteScalar",
                    "client_final_value": "clientFinalValue",
                    "client_weak_damage_scalar": "clientWeakScalar",
                    "client_shelter_damage_scalar": "clientShelterScalar",
                    "client_physical_infliction_damage_scalar": "clientPhysicalInflictionScalar",
                }
                for field_name, token_name in calc_field_names.items():
                    value = _floatish(calc_args.get(field_name))
                    if value is not None:
                        calc_tokens.append(f"{token_name}={value:.6g}")
            shared = 3 if is_crit else 2
            packet_identity_tokens = []
            if isinstance(event.get('session_id'), str) and event['session_id']:
                packet_identity_tokens.append('packetSessionId=' + _trace_string(event['session_id']))
            packet_owner = event.get('operation_owner_id')
            if (type(packet_owner) in (int, str) and re.fullmatch(r'[0-9]+', str(packet_owner))
                    and 0 <= int(packet_owner) < 2**64):
                packet_identity_tokens.append('packetOwnerId=' + str(packet_owner))
            if type(event.get('generation')) is int and 0 <= event['generation'] < 2**32:
                packet_identity_tokens.append('packetGeneration=' + str(event['generation']))
            self.hit_seq += 1
            self._line(
                ts_ms,
                (
                    f'HP_V2 #{self.hit_seq} hit={hit} cum={hit} raw={raw_value:.2f} '
                    f"packetFinalValue={raw_value:.17g} "
                    f'pHP=0 eHP={cur_hp:.0f} skill="{skill}" hits=1 '
                    f"skillLv={skill_level if skill_level > 0 else 'unknown'} "
                    f"templateIntId={template_int_id if template_int_id > 0 else 'unknown'} "
                    f"templateType={event.get('template_type') or 'unknown'} "
                    f"actionId={action_id if action_id is not None else 'unknown'} "
                    f"origTemplateIntId={original_template_int_id if original_template_int_id > 0 else 'unknown'} "
                    f"damageUnitIndex={damage_unit_index if damage_unit_index is not None else 'unknown'} "
                    f"partInstId={part_inst_id if part_inst_id > 0 else 'unknown'} "
                    f"dynBB={dynamic_bb_text if dynamic_bb_text else 'unknown'} "
                    f"calcBB={calc_bb_text if calc_bb_text else 'unknown'} "
                    f"src={attacker} tgt={target} atk={attacker} "
                    f"atkId={attacker_id if attacker_id else 'unknown'} "
                    f"tgtId={target_id if target_id else 'unknown'} seg=0 "
                    f"shared={shared} critFlag={1 if is_crit else 0} critDmg=-1.0000"
                    " "
                    f"eventInstId={event_inst_id if event_inst_id > 0 else 'unknown'} "
                    f"eventOwnerId={_intish(event.get('owner_id')) or 'unknown'} "
                    f"packetSeq={event.get('seq_id') if type(event.get('seq_id')) is int else 'unknown'} "
                    f"clientTick={event.get('client_tick_tms') if type(event.get('client_tick_tms')) is int else 'unknown'} "
                    f"{(' '.join(packet_identity_tokens) + ' ') if packet_identity_tokens else ''}"
                    f"{(' ' + ' '.join(calc_tokens)) if calc_tokens else ''}"
                ),
            )
            modifier_args = detail.get("modifier_args") if isinstance(detail.get("modifier_args"), dict) else {}
            atk_mods = _modifier_uid_tokens(modifier_args, "attacker_modifiers")
            def_mods = _modifier_uid_tokens(modifier_args, "defender_modifiers")
            if atk_mods or def_mods:
                self._line(
                    ts_ms,
                    f"PKT_MOD #{self.hit_seq} atk=[{' '.join(atk_mods)}] def=[{' '.join(def_mods)}]",
                )
                # Keep each handle's processor index and debug arguments;
                # UID-only display tokens cannot identify the applied entry.
                self._line(ts_ms, f"PKT_MOD_DETAIL #{self.hit_seq} " + json.dumps({
                    'attacker': modifier_args.get('attacker_modifiers') or [],
                    'defender': modifier_args.get('defender_modifiers') or [],
                    'packetSessionId': event.get('session_id'),
                    'packetSeq': event.get('seq_id'),
                    'damageUnitIndex': detail.get('damage_unit_index'),
                    'partInstId': detail.get('part_inst_id'),
                }, ensure_ascii=False, separators=(',', ':'), allow_nan=False))
            battle_report_info = detail.get("battle_report_info") if isinstance(detail.get("battle_report_info"), dict) else {}
            report_present = (detail_presence.get('has_battle_report_info')
                              if type(detail_presence.get('has_battle_report_info')) is bool
                              else True if isinstance(detail.get('battle_report_info'), dict) else None)
            if type(report_present) is bool:
                self._line(ts_ms, f"PKT_ATTR_INFO #{self.hit_seq} " + json.dumps({
                    'reportPresent': report_present, 'packetSeq': event.get('seq_id'),
                    'packetSessionId': event.get('session_id'),
                }, ensure_ascii=False, separators=(',', ':')))
            atk_attrs = _attr_info_tokens(battle_report_info, "attacker_attr_modify_info")
            def_attrs = _attr_info_tokens(battle_report_info, "defender_attr_modify_info")
            if atk_attrs or def_attrs:
                self._line(
                    ts_ms,
                    f"PKT_ATTR #{self.hit_seq} atk=[{' '.join(atk_attrs)}] def=[{' '.join(def_attrs)}]",
                )
            if detail_presence:
                has_calc_check = bool(detail_presence.get("has_calculation_check_args"))
                has_calc = bool(detail_presence.get("has_calculate_damage_check_args"))
                proc_count = int(_intish(detail_presence.get("processor_debug_args_count")) or 0)
                has_att_dbg = bool(detail_presence.get("has_attacker_attr_modifier_debug_data"))
                has_def_dbg = bool(detail_presence.get("has_defender_attr_modifier_debug_data"))
                atk_mod_dbg_handles = int(_intish(detail_presence.get("attacker_modifier_debug_handle_count")) or 0)
                def_mod_dbg_handles = int(_intish(detail_presence.get("defender_modifier_debug_handle_count")) or 0)
                atk_mod_dbg_args = int(_intish(detail_presence.get("attacker_modifier_debug_arg_entry_count")) or 0)
                def_mod_dbg_args = int(_intish(detail_presence.get("defender_modifier_debug_arg_entry_count")) or 0)
                if (
                    has_calc_check
                    or has_calc
                    or proc_count
                    or has_att_dbg
                    or has_def_dbg
                    or atk_mod_dbg_handles
                    or def_mod_dbg_handles
                    or atk_mod_dbg_args
                    or def_mod_dbg_args
                ):
                    self._line(
                        ts_ms,
                        (
                            f"PKT_DBG #{self.hit_seq} "
                            f"calcCheck={1 if has_calc_check else 0} "
                            f"calcArgs={1 if has_calc else 0} "
                            f"procArgs={proc_count} "
                            f"attDbg={1 if has_att_dbg else 0} "
                            f"defDbg={1 if has_def_dbg else 0} "
                            f"atkModDbgHandles={atk_mod_dbg_handles} "
                            f"defModDbgHandles={def_mod_dbg_handles} "
                            f"atkModDbgArgs={atk_mod_dbg_args} "
                            f"defModDbgArgs={def_mod_dbg_args}"
                        ),
                    )
        if event_inst_id:
            self.recent_dynamic_bb_by_inst.pop(event_inst_id, None)
            self.recent_calc_bb_by_inst.pop(event_inst_id, None)

    def _write_create_buffs(self, ts_ms: int, event: dict[str, Any], create_buff_action: Any) -> None:
        if not isinstance(create_buff_action, dict):
            return
        details = create_buff_action.get("details") or []
        if not isinstance(details, list):
            return
        trigger_type = event.get('trigger_type')
        if type(trigger_type) is int and trigger_type == 2:
            # Current native CreateBuffAction.OnEnd emits these same detail
            # rows as an explicit finish list (BATTLE_TRIGGER_TYPE.End).
            self._write_finish_buffs(ts_ms, {'finish_buffs': details})
            return
        if type(trigger_type) is not int or trigger_type != 0:
            for detail in details:
                if not isinstance(detail, dict) or not _intish(detail.get('buff_inst_id')):
                    continue
                self._line(ts_ms, 'BUFF_ACTION_UNRESOLVED '
                           f'uid={_intish(detail.get("buff_inst_id"))} '
                           f'seq={event.get("seq_id", "unknown")} '
                           f'triggerType={trigger_type if type(trigger_type) is int else "unknown"} '
                           f'actionId={_intish((event.get("action") or {}).get("action_id"))} '
                           f'ownerId={_intish(detail.get("target_id"))} '
                           f'srcId={_intish(detail.get("source_id"))}')
            return
        inferred_parent_id, inferred_children = self._infer_unresolved_parent_graph(event, details)
        if inferred_parent_id:
            parent_uid = _intish(event.get("inst_id"))
            if parent_uid:
                self.buff_id_by_uid[parent_uid] = inferred_parent_id
                owner = self._name_for_id((details[0].get("target_id") if details and isinstance(details[0], dict) else None) or event.get("owner_id"))
                src = self._name_for_id((details[0].get("source_id") if details and isinstance(details[0], dict) else None) or event.get("owner_id"))
                self._write_buff_start(ts_ms, inferred_parent_id, parent_uid, owner, src, {})
        for detail in details:
            if not isinstance(detail, dict):
                continue
            buff_uid = _intish(detail.get("buff_inst_id"))
            if not buff_uid:
                continue
            owner = self._name_for_id(detail.get("target_id"))
            src = self._name_for_id(detail.get("source_id"))
            has_explicit_template = bool(_template_id(detail.get("buff_id"))) or _intish(detail.get("buff_num_id")) is not None
            buff_id = (
                self._resolve_created_buff_template(event, detail, buff_uid, owner=owner, src=src)
                if has_explicit_template
                else inferred_children.get(buff_uid)
                or self._resolve_created_buff_template(event, detail, buff_uid, owner=owner, src=src)
            )
            assigned = {"blackboard": self._assigned_list_to_blackboard(detail.get("assigned_items"))}
            self._write_buff_start(ts_ms, buff_id, buff_uid, owner, src, assigned, target_id=_intish(detail.get("target_id")), src_id=_intish(detail.get("source_id")), creation_event=event, creation_detail=detail)
            # Preserve the create row's explicit environment/action relation.
            # No inferred parent graph or neighboring event supplies fields.
            origin = {
                'schemaVersion': 1, 'buffUid': str(buff_uid), 'buffTemplate': buff_id,
                'sourceActorId': str(detail.get('source_id')) if detail.get('source_id') is not None else None,
                'targetActorId': str(detail.get('target_id')) if detail.get('target_id') is not None else None,
                'environmentType': event.get('template_type'), 'ownerType': event.get('owner_type'),
                'environmentInstId': str(event.get('inst_id')) if event.get('inst_id') is not None else None,
                'templateStrId': event.get('template_str_id'), 'templateIntId': event.get('template_int_id'),
                'actionId': (event.get('action') or {}).get('action_id'),
                'operationSeq': event.get('seq_id'), 'triggerType': trigger_type,
            }
            self._line(ts_ms, 'BUFF_ORIGIN ' + json.dumps(origin, ensure_ascii=False, separators=(',', ':')))

    def _infer_unresolved_parent_graph(self, event: dict[str, Any], details: list[Any]) -> tuple[str | None, dict[int, str]]:
        return None, {}

    def _write_finish_buffs(self, ts_ms: int, finish_buff_action: Any) -> None:
        if not isinstance(finish_buff_action, dict):
            return
        details = finish_buff_action.get("finish_buffs") or []
        if not isinstance(details, list):
            return
        for detail in details:
            if not isinstance(detail, dict):
                continue
            buff_uid = _intish(detail.get("buff_inst_id"))
            if not buff_uid:
                continue
            buff_id = self.buff_id_by_uid.get(buff_uid, "")
            self.buff_seq += 1
            self._line(ts_ms, f'BUFF_END #{self.buff_seq} id="{buff_id}" uid={buff_uid}')
            self.buff_id_by_uid.pop(buff_uid, None)
            self.buff_actor_ids_by_uid.pop(buff_uid, None)
            self.buff_values_by_uid.pop(buff_uid, None)
            self.buff_keys_by_uid.pop(buff_uid, None)

    @staticmethod
    def _assigned_list_to_blackboard(items: Any) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        if not isinstance(items, list):
            return out
        for item in items:
            if not isinstance(item, dict):
                continue
            key = str(item.get("target_key") or item.get("input_value_key") or "")
            value = _floatish(item.get("numeric_value"))
            if key and value is not None:
                out[key] = {"float_value": value}
        return out

    @staticmethod
    def _assigned_list_to_values(items: Any) -> dict[str, float]:
        out: dict[str, float] = {}
        if isinstance(items, dict):
            blackboard = items.get("blackboard")
            if isinstance(blackboard, dict):
                for key, raw in blackboard.items():
                    if not isinstance(raw, dict):
                        continue
                    value = _floatish(raw.get("float_value"))
                    if value is None:
                        value = _floatish(raw.get("numeric_value"))
                    if value is None:
                        value = _floatish(raw.get("value"))
                    if key and value is not None:
                        out[str(key)] = value
            return out
        if not isinstance(items, list):
            return out
        for item in items:
            if not isinstance(item, dict):
                continue
            key = str(item.get("target_key") or item.get("input_value_key") or item.get("targetKey") or item.get("inputValueKey") or "")
            value = _floatish(item.get("numeric_value"))
            if key and value is not None:
                out[key] = value
        return out

    def _assigned_items_to_values(self, items: Any) -> dict[str, float]:
        return self._assigned_list_to_values(items)

    @staticmethod
    def _assigned_items_to_keys(items: Any) -> set[str]:
        keys: set[str] = set()
        if isinstance(items, dict):
            blackboard = items.get("blackboard")
            if isinstance(blackboard, dict):
                keys.update(str(key) for key in blackboard.keys() if str(key))
            return keys
        if not isinstance(items, list):
            return keys
        for item in items:
            if not isinstance(item, dict):
                continue
            key = str(item.get("target_key") or item.get("input_value_key") or item.get("targetKey") or item.get("inputValueKey") or "")
            if key:
                keys.add(key)
        return keys

    @staticmethod
    def _buff_context_signature(
        owner: str,
        src: str,
        *,
        target_id: int | None = None,
        src_id: int | None = None,
    ) -> str:
        if target_id and src_id and target_id == src_id:
            return "self"
        if owner.startswith("eny_") and src.startswith("chr_"):
            return "player_to_enemy"
        if owner.startswith("chr_") and src.startswith("chr_"):
            return "ally" if owner != src else "self"
        if owner.startswith("eny_") and src.startswith("eny_"):
            return "enemy_self"
        return "other"

    def _learned_buff_signature(
        self,
        numeric_id: int,
        owner: str,
        src: str,
        assigned_items: Any,
        *,
        target_id: int | None = None,
        src_id: int | None = None,
    ) -> tuple[int, str, tuple[str, ...]]:
        keys = tuple(sorted(self._assigned_items_to_keys(assigned_items)))
        return (
            int(numeric_id),
            self._buff_context_signature(owner, src, target_id=target_id, src_id=src_id),
            keys,
        )

    def _learned_buff_signature_from_keys(
        self,
        numeric_id: int,
        owner: str,
        src: str,
        keys: set[str] | None,
        *,
        target_id: int | None = None,
        src_id: int | None = None,
    ) -> tuple[int, str, tuple[str, ...]]:
        return (
            int(numeric_id),
            self._buff_context_signature(owner, src, target_id=target_id, src_id=src_id),
            tuple(sorted(str(key) for key in (keys or set()) if str(key))),
        )

    def _remember_learned_buff_mapping(
        self,
        numeric_id: int | None,
        owner: str,
        src: str,
        assigned_items: Any,
        canonical_id: str,
        *,
        target_id: int | None = None,
        src_id: int | None = None,
    ) -> None:
        return

    def _remember_learned_buff_mapping_from_keys(
        self,
        numeric_id: int | None,
        owner: str,
        src: str,
        keys: set[str] | None,
        canonical_id: str,
        *,
        target_id: int | None = None,
        src_id: int | None = None,
    ) -> None:
        return

    def _resolve_learned_buff_mapping(
        self,
        numeric_id: int | None,
        owner: str,
        src: str,
        assigned_items: Any,
        *,
        target_id: int | None = None,
        src_id: int | None = None,
    ) -> str | None:
        return None

    def _assigned_direct_buff_id(self, items: Any) -> str | None:
        if not isinstance(items, list):
            return None
        candidates: list[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            key = str(item.get("target_key") or item.get("targetKey") or "").lower()
            if "buff_id" not in key and "child_buff_id" not in key:
                continue
            value = str(item.get("string_value") or item.get("stringValue") or "")
            if value.startswith("buff_") and self.resolver.buff_content(value):
                candidates.append(value)
        if len(candidates) == 1:
            return candidates[0]
        return None

    def _write_buff_start(
        self,
        ts_ms: int,
        buff_id: str,
        buff_uid: int,
        owner: str,
        src: str,
        assigned_items: Any,
        *,
        target_id: int | None = None,
        src_id: int | None = None,
        creation_event: dict[str, Any] | None = None,
        creation_detail: dict[str, Any] | None = None,
    ) -> None:
        if target_id is not None or src_id is not None:
            self.buff_actor_ids_by_uid[buff_uid] = (target_id, src_id)
        previous_id = self.buff_id_by_uid.get(buff_uid, "")
        previous_values = self.buff_values_by_uid.get(buff_uid, {})
        previous_keys = self.buff_keys_by_uid.get(buff_uid, set())
        if previous_id and previous_id != buff_id and not self._prefer_buff_start(previous_id, buff_id):
            buff_id = previous_id
        new_values = self._assigned_items_to_values(assigned_items)
        new_keys = self._assigned_items_to_keys(assigned_items)
        identity_unchanged = bool(previous_id and previous_id == buff_id)
        values_add_information = any(previous_values.get(key) != value for key, value in new_values.items())
        if identity_unchanged:
            merged_values = dict(previous_values)
            merged_values.update(new_values)
            merged_keys = set(previous_keys) | set(new_keys)
        else:
            merged_values = new_values
            merged_keys = new_keys
        self.buff_id_by_uid[buff_uid] = buff_id
        self.buff_values_by_uid[buff_uid] = merged_values
        self.buff_keys_by_uid[buff_uid] = merged_keys
        if previous_id and previous_id != buff_id and previous_id.isdigit() and buff_id and buff_id != "unknown_buff" and not buff_id.isdigit():
            self._remember_learned_buff_mapping_from_keys(
                _intish(previous_id),
                owner,
                src,
                previous_keys or self._assigned_items_to_keys(assigned_items),
                buff_id,
            )
            self._remember_learned_buff_mapping(
                _intish(previous_id),
                owner,
                src,
                {"blackboard": {key: {"float_value": value} for key, value in previous_values.items()}},
                buff_id,
            )
        if not identity_unchanged or values_add_information:
            # Repeated create callbacks need not be a newly allocated Buff.
            # Preserve the observation without inventing another start row.
            if self._pending_buff_start_ts_ms is not None and self._pending_buff_start_ts_ms != ts_ms:
                self._flush_pending_buff_starts()
            self._pending_buff_start_ts_ms = ts_ms
            existing = self._pending_buff_start_rows.get(buff_uid)
            if existing is None:
                self._pending_buff_start_order.append(buff_uid)
                self._pending_buff_start_rows[buff_uid] = (buff_id, owner, src, assigned_items)
            else:
                prev_buff_id, prev_owner, prev_src, prev_assigned_items = existing
                if self._prefer_buff_start(prev_buff_id, buff_id):
                    self._pending_buff_start_rows[buff_uid] = (buff_id, owner, src, assigned_items)
        if creation_event is not None:
            # generation belongs to the operation owner entity. Neither
            # client create schema contains a generation for the new Buff.
            observation = {
                'schemaVersion': 1, 'buffUid': str(buff_uid), 'buffTemplate': buff_id,
                'sourceActorId': str(src_id) if src_id is not None else None,
                'targetActorId': str(target_id) if target_id is not None else None,
                'sessionId': creation_event.get('session_id'),
                'operationSeq': creation_event.get('seq_id'),
                'operationOwnerId': creation_event.get('operation_owner_id'),
                'operationGeneration': creation_event.get('generation'),
                'generationScope': 'operation_owner', 'sourceEvent': creation_event.get('type'),
                'buffNumId': ((creation_detail or {}).get('buff_num_id')
                              if creation_detail is not None else creation_event.get('int_id')),
                'buffId': ((creation_detail or {}).get('buff_id')
                           if creation_detail is not None else creation_event.get('str_id')),
                'buffTemplateId': creation_event.get('buff_template_id'),
                'assignedItems': (creation_detail.get('assigned_items') if creation_detail is not None
                                  else assigned_items if isinstance(assigned_items, list) else None),
                'isNewInstance': None, 'priorUidObserved': bool(previous_id),
                'observationKind': 'creation_or_refresh', 'enhanceMultiplier': None,
            }
            if creation_detail is not None:
                observation['actionId'] = (creation_event.get('action') or {}).get('action_id')
                observation['triggerType'] = creation_event.get('trigger_type')
                observation['creationActionType'] = (creation_event.get('action') or {}).get('action_type')
                observation['creationActionRuntimeCase'] = creation_event.get('action_runtime_case')
            if type(observation['operationOwnerId']) in (int, str):
                observation['operationOwnerId'] = str(observation['operationOwnerId'])
            self._line(ts_ms, 'BUFF_CREATE_OBSERVED ' + json.dumps(
                observation, ensure_ascii=False, separators=(',', ':'), allow_nan=False))

    def _packet_buff_mapping_applies(self, mapping: dict[str, Any], owner: str, src: str) -> bool:
        return self.resolver._packet_buff_mapping_applies(
            mapping,
            owner,
            src,
            self.active_suits_by_char,
            self.active_weapons_by_char,
        )

    def _resolve_buff_template(
        self,
        str_id: Any = None,
        int_id: Any = None,
        *,
        owner: str = "",
        src: str = "",
        assigned_items: Any = None,
    ) -> str:
        return self.resolver.resolve_buff(
            str_id,
            _intish(int_id),
            context=PacketResolveContext(
                owner=owner,
                src=src,
                active_suits_by_char=self.active_suits_by_char,
                active_weapons_by_char=self.active_weapons_by_char,
                blackboard_values=self._assigned_list_to_values(assigned_items),
                blackboard_keys=self._assigned_items_to_keys(assigned_items),
            ),
        )

    def _resolve_skill_template(self, str_id: Any = None, int_id: Any = None, *, owner_hint: str = "") -> str | None:
        number = _intish(int_id)
        return self.resolver.resolve_skill(
            str_id,
            number,
            context=PacketResolveContext(
                owner_hint=owner_hint or (self.skill_owner_by_int.get(number) if number is not None else "") or "",
            ),
        )

    @staticmethod
    def _character_from_template(template: str | None) -> str | None:
        if not template:
            return None
        parts = template.split("_")
        if len(parts) >= 3 and parts[0] == "chr" and parts[1].isdigit():
            return "_".join(parts[:3])
        return None

    def _resolve_created_buff_template(self, event: dict[str, Any], detail: dict[str, Any], buff_uid: int, *, owner: str = "", src: str = "") -> str:
        raw_template = _template_id(detail.get("buff_id"))
        if raw_template:
            return raw_template
        buff_num_id = _intish(detail.get("buff_num_id"))
        if buff_num_id is not None:
            resolved_numeric = self._resolve_buff_template(
                None,
                buff_num_id,
                owner=owner,
                src=src,
                assigned_items=detail.get("assigned_items"),
            )
            # The create row's numeric template is more authoritative than a
            # cached UID or inferred parent graph, even without trial loadout.
            if resolved_numeric:
                return resolved_numeric
        existing = self.buff_id_by_uid.get(buff_uid)
        if existing and existing != "unknown_buff":
            return existing
        parent_type = str(event.get("template_type") or "").lower()
        if parent_type == "skill":
            parent_id = self._resolve_skill_template(
                event.get("template_str_id"),
                event.get("template_int_id"),
                owner_hint=self._character_from_template(src) or self._character_from_template(owner) or "",
            ) or ""
        else:
            parent_uid = _intish(event.get("inst_id"))
            existing_parent = self.buff_id_by_uid.get(parent_uid) if parent_uid else None
            if existing_parent and existing_parent != "unknown_buff" and not existing_parent.isdigit():
                parent_id = existing_parent
            else:
                parent_id = self._resolve_buff_template(
                    event.get("template_str_id"),
                    event.get("template_int_id"),
                    owner=owner,
                    src=src,
                )

        resolved_created = self.resolver.resolve_created_buff(
            parent_type=parent_type,
            parent_canonical_id=parent_id,
            assigned_items=detail.get("assigned_items"),
        )
        if resolved_created:
            return resolved_created

        return "unknown_buff"

    def _name_for_id(self, value: Any) -> str:
        battle_id = _intish(value)
        if not battle_id:
            return "?"
        existing = self.id_to_name.get(battle_id)
        if existing and existing != _UNKNOWN_ENEMY_KEY:
            return existing
        alias = self.actor_alias_by_raw_id.get(battle_id)
        if alias:
            return alias
        if existing:
            return existing
        return f"id_{battle_id}"

    def _actor_alias_by_id(self, battle_id: int | None) -> str:
        if not battle_id:
            return "?"
        existing = self.id_to_name.get(battle_id)
        if existing and existing != _UNKNOWN_ENEMY_KEY:
            return existing
        alias = self.actor_alias_by_raw_id.get(battle_id)
        if alias:
            return alias
        if existing:
            return existing
        return f"id_{battle_id}"

    def _name_for_event_id(self, battle_id: int | None) -> str:
        return self._name_for_id(battle_id)

    def _infer_actor_from_mapping(self, buff_num_id: int | None) -> str:
        return ""

    def _remember_actor_alias_for_buff(self, src_id: int | None, target_id: int | None, buff_num_id: int | None, buff_id: str) -> None:
        # Buff names and nearby actor IDs do not prove entity identity.
        return

    def _set_enemy_hint_from_dungeon_id(
        self,
        dungeon_id: str,
        source: str,
        *,
        clear_on_missing: bool = False,
    ) -> None:
        if not dungeon_id:
            return
        enemy_key = _load_dungeon_enemy_hints().get(dungeon_id)
        if enemy_key:
            self._set_enemy_hint(enemy_key, source)
        elif clear_on_missing:
            self._current_enemy_hint = ""
            self._current_enemy_hint_source = ""

    def _set_enemy_hint(self, enemy_key: str, source: str) -> None:
        if not enemy_key or enemy_key == _UNKNOWN_ENEMY_KEY:
            return
        priority = {"buff": 1, "game_mechanics": 2, "dungeon_context": 2}.get(source, 1)
        current_priority = {"buff": 1, "game_mechanics": 2, "dungeon_context": 2}.get(
            self._current_enemy_hint_source,
            0,
        )
        if self._current_enemy_hint and priority < current_priority:
            return
        self._current_enemy_hint = enemy_key
        self._current_enemy_hint_source = source
        self._upgrade_single_unknown_enemy_alias(enemy_key, source=source)

    def _enemy_hint_can_accept_buff_alias(self, enemy_key: str) -> bool:
        if not self._current_enemy_hint:
            return True
        if self._current_enemy_hint == enemy_key:
            return True
        return self._current_enemy_hint_source == "buff"

    def _upgrade_single_unknown_enemy_alias(self, enemy_key: str, *, source: str) -> None:
        return

    def _record_identity_inference(self, raw_id: int, enemy_key: str, *, source: str) -> None:
        if self._last_ts_ms is None:
            return
        self._line(
            self._last_ts_ms,
            f"IDENTITY_INFERENCE actorId={raw_id} template={enemy_key} source={source}",
        )

    def _prefer_buff_start(self, previous_id: str, new_id: str) -> bool:
        if previous_id == new_id:
            return False
        if previous_id.isdigit():
            static_previous = self.resolver.static_buff_by_numeric.get(previous_id)
            if static_previous and new_id != static_previous:
                return False
        prev_canonical = previous_id and previous_id != "unknown_buff" and not previous_id.isdigit()
        new_canonical = new_id and new_id != "unknown_buff" and not new_id.isdigit()
        if new_canonical and not prev_canonical:
            return True
        if prev_canonical and not new_canonical:
            return False
        if new_id == "unknown_buff" and previous_id != "unknown_buff":
            return False
        if previous_id == "unknown_buff" and new_id != "unknown_buff":
            return True
        return new_id != previous_id

    def _flush_pending_buff_starts(self) -> None:
        if not self._pending_buff_start_rows:
            self._pending_buff_start_ts_ms = None
            return
        if self._pending_buff_start_ts_ms is None:
            raise ValueError("missing buff start timestamp")
        ts_ms = self._pending_buff_start_ts_ms
        for buff_uid in self._pending_buff_start_order:
            row = self._pending_buff_start_rows.get(buff_uid)
            if row is None:
                continue
            buff_id, owner, src, assigned_items = row
            owner_id, source_id = self.buff_actor_ids_by_uid.get(buff_uid, (None, None))
            self.buff_seq += 1
            self._append_line(
                ts_ms,
                (
                    f'BUFF_START #{self.buff_seq} id="{buff_id}" uid={buff_uid} '
                    f"owner={owner} src={src} ownerId={owner_id if owner_id is not None else 'unknown'} "
                    f"srcId={source_id if source_id is not None else 'unknown'} "
                    f"dur=unknown lifeT=unknown passed=unknown enh=unknown"
                ),
            )
            pairs = _blackboard_pairs(assigned_items)
            if pairs:
                body = " ".join(f"{key}={value:.6g}" for key, value in pairs)
                self._append_line(ts_ms, f"BB[{buff_uid}]: {body}")
        self._pending_buff_start_rows.clear()
        self._pending_buff_start_order.clear()
        self._pending_buff_start_ts_ms = None

    def _maybe_map_next_loadout_actor(self, actor_id: int, ts_ms: int, *, source: str) -> None:
        return

    def _name_for_damage_target(self, value: Any, attacker_name: str) -> str:
        battle_id = _intish(value)
        if battle_id is None:
            return "?"
        return self.id_to_name.get(battle_id) or self.actor_alias_by_raw_id.get(battle_id) or f"id_{battle_id}"

    def _line(self, ts_ms: int, body: str) -> None:
        self._flush_pending_buff_starts()
        self._append_line(ts_ms, body)

    def _append_line(self, ts_ms: int, body: str) -> None:
        stamp = datetime.fromtimestamp(ts_ms / 1000).strftime("%H:%M:%S.%f")[:-3]
        with self.trace_file.open("a", encoding="utf-8", newline="") as handle:
            handle.write(f"[{stamp}] {body}\n")
