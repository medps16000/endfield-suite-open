from __future__ import annotations

from datetime import datetime
import copy
from functools import lru_cache
import json
import os
from pathlib import Path
import re
import sys
from typing import Any

try:
    from parser_core import parse_upload_battle_log_text
    from parser_core.battle_log_parser import (
        _SQUAD_MEMBER_RE,
        _extract_fields,
        _parse_char_skills_line,
        _parse_entity_stats_snapshot,
        _load_num_id_str_skill_map,
        _merge_loadout_snapshot as _parser_merge_loadout_snapshot,
        _merge_tracked_loadout_state as _parser_merge_tracked_loadout_state,
        _parse_loadout_slot_snapshot,
        _parse_loadout_stats_snapshot,
        _repair_weapon_puton_loadout_groups,
        extract_char_skill_levels_from_text,
    )
except ModuleNotFoundError:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "packages" / "parser_core"
        if candidate.exists():
            sys.path.insert(0, str(candidate))
            break
    from parser_core import parse_upload_battle_log_text
    from parser_core.battle_log_parser import (
        _SQUAD_MEMBER_RE,
        _extract_fields,
        _parse_char_skills_line,
        _parse_entity_stats_snapshot,
        _load_num_id_str_skill_map,
        _merge_loadout_snapshot as _parser_merge_loadout_snapshot,
        _merge_tracked_loadout_state as _parser_merge_tracked_loadout_state,
        _parse_loadout_slot_snapshot,
        _parse_loadout_stats_snapshot,
        _repair_weapon_puton_loadout_groups,
        extract_char_skill_levels_from_text,
    )

from uploader_core.log_integrity import load_raw_log_integrity
from parser_core.critical_context import update_critical_observation

_TIMESTAMP_RE = re.compile(r"^\[(\d{2}):(\d{2}):(\d{2})\.(\d{3})\]")
_GAME_TIMER_END_ELAPSED_RE = re.compile(r"\bGAME_TIMER_END\b.*?\belapsedMs=(\d+).*?\bsane=(\d+)")
_OFFICIAL_TIMER_END_ELAPSED_RE = re.compile(r"\bOFFICIAL_TIMER_END\b.*?\bpassTime=(\d+)")
_DAY_MS = 24 * 60 * 60 * 1000
_IDLE_SPLIT_MS = 30_000
_POST_TIMER_TAIL_MS = 10_000
PAYLOAD_BUILDER_VERSION = "payload-builder-2026.10.15.1"
_ADMIN_CANONICAL_KEY = "chr_9000_endmin"
_ENDMIN_VARIANTS = {"chr_0002_endminm", "chr_0003_endminf"}
_MANAGED_INCREMENTAL_TAIL_BYTES = 8 * 1024 * 1024


def payload_builder_diagnostics() -> dict[str, str]:
    from parser_core.battle_log_parser import PARSER_VERSION, RULES_VERSION

    return {
        "payloadBuilderVersion": PAYLOAD_BUILDER_VERSION,
        "parserVersion": PARSER_VERSION,
        "rulesVersion": RULES_VERSION,
    }


def _repo_root() -> Path:
    override = os.environ.get("ENDFIELD_LOGS_DATA_ROOT")
    if override:
        return Path(override)
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        return Path(frozen_root)
    for parent in Path(__file__).resolve().parents:
        if (parent / "data").exists() and (parent / "packages").exists():
            return parent
    return Path(__file__).resolve().parents[3]


def _normalize_asset_url(icon_path: str | None) -> str | None:
    if not icon_path:
        return None
    if icon_path.startswith("http://") or icon_path.startswith("https://"):
        return icon_path
    if icon_path.startswith("/"):
        return icon_path
    return icon_path


def _is_character_key(value: object) -> bool:
    return isinstance(value, str) and value.startswith("chr_")


def _loadout_lookup_keys(character_key: str | None) -> list[str]:
    key = str(character_key or "")
    if not key:
        return []
    if key == _ADMIN_CANONICAL_KEY:
        return [key, *_ENDMIN_VARIANTS]
    if key in _ENDMIN_VARIANTS:
        return [key, _ADMIN_CANONICAL_KEY]
    return [key]


def _resolve_loadout_character_key(character_key: str | None, active_keys: set[str]) -> str | None:
    key = str(character_key or "")
    if not key:
        return None
    if key == _ADMIN_CANONICAL_KEY:
        matches = [candidate for candidate in sorted(_ENDMIN_VARIANTS) if candidate in active_keys]
        if len(matches) == 1:
            return matches[0]
    return key


def _lookup_loadout_payload(loadout_by_key: dict[str, dict], character_key: str | None) -> dict[str, Any]:
    for key in _loadout_lookup_keys(character_key):
        payload = loadout_by_key.get(key)
        if isinstance(payload, dict):
            return payload
    return {}


def _lookup_loadout_name(loadout_name_by_key: dict[str, str], character_key: str | None) -> str | None:
    for key in _loadout_lookup_keys(character_key):
        name = str(loadout_name_by_key.get(key) or "")
        if name:
            return name
    return None


def _load_loadout_entries(proof: dict | None) -> list[dict]:
    meta = proof.get("meta") if isinstance(proof, dict) else None
    loadout_meta = None
    if isinstance(meta, dict):
        if isinstance(meta.get("loadout"), list):
            loadout_meta = meta.get("loadout")
        elif isinstance(meta.get("roster"), list):
            loadout_meta = meta.get("roster")
    if not isinstance(loadout_meta, list):
        return []
    return [_normalize_loadout_entry(entry) for entry in loadout_meta if isinstance(entry, dict)]


def _normalize_equip_entry(entry: dict) -> dict:
    normalized = dict(entry)
    if normalized.get("item_id") is None:
        normalized["item_id"] = normalized.get("itemId")
    if normalized.get("piece_name") is None:
        normalized["piece_name"] = normalized.get("pieceName") or normalized.get("item_name")
    if normalized.get("suit_name") is None:
        normalized["suit_name"] = normalized.get("suitName")
    if normalized.get("part_name") is None:
        normalized["part_name"] = normalized.get("partName")
    return normalized


def _normalize_loadout_entry(entry: dict) -> dict:
    normalized = dict(entry)
    if normalized.get("char_key") is None:
        normalized["char_key"] = normalized.get("character_key") or normalized.get("characterKey")
    if normalized.get("char_name") is None:
        normalized["char_name"] = normalized.get("character_name") or normalized.get("characterName")
    if normalized.get("character_level") is None:
        normalized["character_level"] = (
            normalized.get("characterLevel")
            if normalized.get("characterLevel") is not None
            else normalized.get("char_level")
        )
    if normalized.get("potential") is None:
        normalized["potential"] = (
            normalized.get("character_potential")
            if normalized.get("character_potential") is not None
            else normalized.get("characterPotential")
        )
    if normalized.get("weapon_template") is None:
        normalized["weapon_template"] = normalized.get("weaponTemplate")
    if normalized.get("weapon_name") is None:
        normalized["weapon_name"] = normalized.get("weaponName")
    if normalized.get("weapon_level") is None:
        normalized["weapon_level"] = normalized.get("weaponLevel")
    if normalized.get("weapon_refine") is None:
        normalized["weapon_refine"] = normalized.get("weaponRefine")
    equips = normalized.get("equips")
    if isinstance(equips, list):
        normalized["equips"] = [_normalize_equip_entry(equip) for equip in equips if isinstance(equip, dict)]
    return normalized


def _merge_equip_entries(base: list[dict], update: list[dict]) -> list[dict]:
    merged: list[dict] = [dict(equip) for equip in base if isinstance(equip, dict)]
    slot_index: dict[int, int] = {}
    for index, equip in enumerate(merged):
        try:
            slot_index[int(equip.get("slot"))] = index
        except (TypeError, ValueError):
            continue

    for equip in update:
        if not isinstance(equip, dict):
            continue
        try:
            slot = int(equip.get("slot"))
        except (TypeError, ValueError):
            slot = None
        if slot is not None and slot in slot_index:
            existing = merged[slot_index[slot]]
            merged[slot_index[slot]] = {**existing, **{key: value for key, value in equip.items() if value not in (None, "", [], {})}}
        else:
            merged.append(dict(equip))
            if slot is not None:
                slot_index[slot] = len(merged) - 1
    return merged


def _merge_loadout_entry(base: dict | None, update: dict) -> dict:
    if base is None:
        return dict(update)
    merged = dict(base)
    current_refine_source = merged.get("weapon_refine_source")
    update_refine_source = update.get("weapon_refine_source")
    for key, value in update.items():
        if value in (None, "", [], {}):
            continue
        if key == "equips" and isinstance(value, list):
            current_equips = merged.get("equips")
            if isinstance(current_equips, list) and current_equips:
                merged["equips"] = _merge_equip_entries(current_equips, value)
            else:
                merged["equips"] = value
            continue
        if key == "weapon_refine" and current_refine_source == "source_skill" and update_refine_source != "source_skill":
            continue
        if (
            key == "weapon_refine_source"
            and current_refine_source == "source_skill"
            and value != "source_skill"
        ):
            continue
        merged[key] = value
    return merged


def _raw_text_loadout_state(raw_content: str) -> tuple[list[dict], dict[str, dict]]:
    groups: list[dict] = []
    entries_by_key: dict[str, dict] = {}
    current_group: dict | None = None
    squad_actor_keys: dict[str, str] = {}
    for line_index, raw_line in enumerate(raw_content.splitlines()):
        if " SQUAD " in raw_line:
            members = [(match.group(2), match.group(1)) for match in _SQUAD_MEMBER_RE.finditer(raw_line)]
            squad_actor_keys = dict(members) if len({pair[0] for pair in members}) == len(members) else {}
        reason_match = re.search(r"\bLOADOUT reason=([^\s]+)", raw_line)
        if reason_match:
            current_group = {
                "ts_ms": line_index,
                "line_index": line_index,
                "timestamp": raw_line[:13],
                "reason": reason_match.group(1),
                "index": len(groups),
                "rows": {},
            }
            groups.append(current_group)
            continue

        snapshot: dict[str, Any] | None = None
        if " LOADOUT_STATS " in raw_line or "LOADOUT_STATS " in raw_line:
            snapshot = _parse_loadout_stats_snapshot(raw_line,
                allow_bound_gem=bool(current_group and current_group.get("timestamp") == raw_line[:13]
                    and current_group.get("reason") in {"SC_SYNC_CHAR_BAG_INFO", "SC_SELF_SCENE_INFO"}))
        elif " LOADOUT slot=" in raw_line or "LOADOUT slot=" in raw_line:
            snapshot = _parse_loadout_slot_snapshot(raw_line)
        elif " CHAR_SKILLS " in raw_line:
            parsed_skills = _parse_char_skills_line(raw_line)
            if parsed_skills is not None:
                snapshot = _parse_loadout_stats_snapshot(
                    raw_line, loadout_context=entries_by_key.get(parsed_skills[0]),
                    squad_actor_keys=squad_actor_keys,
                )
            if snapshot is not None:
                current_group = {
                    "ts_ms": line_index, "line_index": line_index, "timestamp": raw_line[:13],
                    "reason": "CHAR_SKILLS", "index": len(groups),
                    "rows": {key: dict(entries_by_key[key]) for key in set(squad_actor_keys.values()) if key in entries_by_key},
                }
                groups.append(current_group)
        if snapshot is None:
            continue
        char_key = str(snapshot.get("character_key") or snapshot.get("char_key") or "")
        if not char_key:
            continue
        timestamp = raw_line[:13]
        if current_group is None or current_group.get("timestamp") != timestamp:
            current_group = {
                "ts_ms": line_index,
                "line_index": line_index,
                "timestamp": timestamp,
                "reason": "",
                "index": len(groups),
                "rows": {},
            }
            groups.append(current_group)
        group_rows = current_group["rows"]
        group_rows[char_key] = _parser_merge_loadout_snapshot(
            group_rows.get(char_key, {"character_key": char_key}),
            snapshot,
        )
        entries_by_key[char_key] = _parser_merge_tracked_loadout_state(
            entries_by_key.get(char_key, {}),
            snapshot,
        )

    repaired_fallback = _repair_weapon_puton_loadout_groups(groups, entries_by_key)
    return groups, repaired_fallback


def _sorted_loadout_entries(entries_by_key: dict[str, dict]) -> list[dict]:
    return sorted(
        (dict(entry) for entry in entries_by_key.values() if isinstance(entry, dict)),
        key=lambda entry: (int(entry.get("slot") or 0), str(entry.get("character_key") or "")),
    )


def _raw_text_loadout_entries(raw_content: str) -> list[dict]:
    _groups, repaired_fallback = _raw_text_loadout_state(raw_content)
    return _sorted_loadout_entries(repaired_fallback)


def _raw_loadout_entries_for_segment(
    loadout_groups: list[dict],
    fallback_by_char: dict[str, dict],
    segment: dict,
) -> list[dict]:
    first_hit_line_index = segment.get("first_hit_line_index")
    if first_hit_line_index is None:
        return _sorted_loadout_entries(fallback_by_char)
    eligible = [
        group
        for group in loadout_groups
        if group.get("rows")
        and int(group.get("line_index") or 0) <= int(first_hit_line_index)
    ]
    if not eligible:
        return []
    selected = max(
        eligible,
        key=lambda group: (int(group.get("line_index") or 0), int(group.get("index") or 0)),
    )
    rows = selected.get("rows")
    if not isinstance(rows, dict):
        return []
    # A battle-start packet may be a delta (for example, one changed equip
    # slot). Rebuild only the characters present in the selected battle group
    # from preceding raw-log snapshots; do not carry previous teammates.
    merged_rows: dict[str, dict] = {}
    selected_keys = {str(key) for key in rows}
    for group in sorted(
        eligible,
        key=lambda item: (int(item.get("line_index") or 0), int(item.get("index") or 0)),
    ):
        group_rows = group.get("rows")
        if not isinstance(group_rows, dict):
            continue
        for char_key in selected_keys:
            snapshot = group_rows.get(char_key)
            if isinstance(snapshot, dict):
                merged_rows[char_key] = _parser_merge_tracked_loadout_state(
                    merged_rows.get(char_key, {"character_key": char_key}),
                    snapshot,
                )
    return _sorted_loadout_entries(merged_rows)


def _loadout_entries_with_fallback(
    proof: dict | None,
    parsed_loadout: object,
    *,
    raw_loadout_entries: list[dict] | None = None,
) -> list[dict]:
    entries_by_key: dict[str, dict] = {}
    order: list[str] = []

    entry_sources = []
    if raw_loadout_entries:
        entry_sources.append(raw_loadout_entries)
    entry_sources.append(parsed_loadout if isinstance(parsed_loadout, list) else [])

    for raw_entry in [entry for source in entry_sources for entry in source]:
        if not isinstance(raw_entry, dict):
            continue
        entry = _normalize_loadout_entry(raw_entry)
        char_key = str(entry.get("char_key") or "")
        if not char_key:
            continue
        if char_key not in entries_by_key:
            order.append(char_key)
        entries_by_key[char_key] = _merge_loadout_entry(entries_by_key.get(char_key), entry)

    # Integrity-proof metadata is not battle-time evidence. It may only fill
    # fields that the raw/parsed battle record did not provide; it must never
    # overwrite an observed value. Every such fill is marked for diagnostics.
    for raw_entry in _load_loadout_entries(proof):
        entry = _normalize_loadout_entry(raw_entry)
        char_key = str(entry.get("char_key") or "")
        if not char_key:
            continue
        if char_key not in entries_by_key:
            order.append(char_key)
            entries_by_key[char_key] = dict(entry)
            entries_by_key[char_key]["_loadout_fallback_used"] = True
            continue
        current = entries_by_key[char_key]
        filled = False
        critical_fallback_used = False
        for key, value in entry.items():
            if value in (None, "", [], {}):
                continue
            if key == "equips" and isinstance(value, list):
                existing_equips = current.get("equips")
                existing_equips = existing_equips if isinstance(existing_equips, list) else []
                by_slot = {
                    int(equip.get("slot")): equip
                    for equip in existing_equips
                    if isinstance(equip, dict) and equip.get("slot") is not None
                }
                for proof_equip in value:
                    if not isinstance(proof_equip, dict) or proof_equip.get("slot") is None:
                        continue
                    slot = int(proof_equip["slot"])
                    if slot not in by_slot:
                        existing_equips.append(dict(proof_equip))
                        by_slot[slot] = existing_equips[-1]
                        filled = True
                        critical_fallback_used = True
                        continue
                    target = by_slot[slot]
                    for equip_key, equip_value in proof_equip.items():
                        if equip_value not in (None, "", [], {}) and target.get(equip_key) in (None, "", [], {}):
                            target[equip_key] = equip_value
                            filled = True
                            critical_fallback_used = True
                current["equips"] = existing_equips
                continue
            if current.get(key) in (None, "", [], {}):
                current[key] = value
                filled = True
                if key != "char_name":
                    critical_fallback_used = True
        if filled and critical_fallback_used:
            current["_loadout_fallback_used"] = True

    return [entries_by_key[char_key] for char_key in order]


def _normalize_payload_equip_slots(equips: list[dict]) -> list[dict]:
    ordered = sorted(equips, key=lambda item: int(item.get("slot") or 0))
    raw_slots: list[int] = []
    for equip in ordered:
        try:
            raw_slots.append(int(equip.get("slot")))
        except (TypeError, ValueError):
            raw_slots.append(-1)
    looks_one_based = (
        len(raw_slots) == 4
        and 0 not in raw_slots
        and all(1 <= slot <= 4 for slot in raw_slots)
    )

    slots: list[dict | None] = [None, None, None, None]
    pending: list[dict] = []
    for equip in ordered:
        try:
            slot = int(equip.get("slot"))
        except (TypeError, ValueError):
            slot = -1
        normalized_slot = slot - 1 if looks_one_based else slot
        payload = {**equip, "slot": normalized_slot}
        if 0 <= normalized_slot < len(slots) and slots[normalized_slot] is None:
            slots[normalized_slot] = payload
        else:
            pending.append(payload)

    for equip in pending:
        try:
            original_slot = int(equip.get("slot"))
        except (TypeError, ValueError):
            original_slot = -1
        empty_index = next((index for index, value in enumerate(slots) if value is None), None)
        if empty_index is None:
            continue
        slots[empty_index] = {**equip, "slot": empty_index if original_slot < 0 or original_slot >= len(slots) else original_slot}

    return [equip for equip in slots if equip is not None]


@lru_cache(maxsize=1)
def _load_weapon_catalog() -> dict[str, dict]:
    weapon_root = _repo_root() / "data" / "akedata" / "weapon" / "items"
    catalog: dict[str, dict] = {}
    if weapon_root.exists():
        for path in weapon_root.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            weapon_id = str(data.get("weaponId") or "")
            if not weapon_id:
                continue
            catalog[weapon_id] = {
                "weaponName": str(data.get("title") or weapon_id),
                "iconUrl": _normalize_asset_url(data.get("icon")),
                # 等级 1..N 的基础攻击阶梯：包内 weaponLv 恒为 0，武器等级用 baseAtk 反查
                "baseAtk": data.get("baseAtk") if isinstance(data.get("baseAtk"), list) else None,
            }

    local_manifest = _repo_root() / "data" / "local_tables" / "weapon" / "manifest.json"
    if local_manifest.exists():
        try:
            local_data = json.loads(local_manifest.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            local_data = {}
        for item in local_data.get("entries", []):
            if not isinstance(item, dict):
                continue
            weapon_id = str(item.get("weaponId") or item.get("id") or "")
            if not weapon_id:
                continue
            existing = catalog.get(weapon_id, {})
            catalog[weapon_id] = {
                "weaponName": str(item.get("name") or existing.get("weaponName") or weapon_id),
                "iconUrl": _normalize_asset_url(item.get("icon")) or existing.get("iconUrl"),
                "baseAtk": existing.get("baseAtk"),
            }
    return catalog


def _infer_weapon_level_from_base_atk(weapon_catalog_entry: dict | None, base_atk: object) -> int | None:
    """weaponLv 服务器同步恒为 0；baseAtk 是每级唯一的阶梯值，反查即等级（1 基）。"""
    try:
        base_atk_value = int(base_atk)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if base_atk_value <= 0:
        return None
    ladder = (weapon_catalog_entry or {}).get("baseAtk")
    if not isinstance(ladder, list):
        return None
    level = None
    for index, value in enumerate(ladder):
        if value == base_atk_value:
            level = index + 1
    return level


@lru_cache(maxsize=1)
def _load_equip_catalog() -> dict[str, dict]:
    equip_root = _repo_root() / "data" / "akedata" / "equip" / "items"
    catalog: dict[str, dict] = {}
    if equip_root.exists():
        for path in equip_root.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            equip_items = data.get("equip")
            if not isinstance(equip_items, dict):
                continue
            for item_id, item in equip_items.items():
                catalog[str(item_id)] = {
                    "pieceName": str(item.get("name") or item_id),
                    "partName": str(item.get("部位") or ""),
                    "iconUrl": _normalize_asset_url(item.get("icon")),
                }

    local_manifest = _repo_root() / "data" / "local_tables" / "equip" / "pieces.json"
    if local_manifest.exists():
        try:
            local_data = json.loads(local_manifest.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            local_data = {}
        for item in local_data.get("entries", []):
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("itemId") or item.get("id") or "")
            if not item_id:
                continue
            existing = catalog.get(item_id, {})
            catalog[item_id] = {
                "pieceName": str(item.get("name") or existing.get("pieceName") or item_id),
                "partName": str(item.get("partName") or existing.get("partName") or ""),
                "iconUrl": _normalize_asset_url(item.get("icon")) or existing.get("iconUrl"),
            }
    return catalog


def _build_loadout_by_character_key(
    proof: dict | None,
    *,
    loadout_entries: list[dict] | None = None,
) -> dict[str, dict]:
    weapon_catalog = _load_weapon_catalog()
    equip_catalog = _load_equip_catalog()
    loadouts: dict[str, dict] = {}

    for entry in (loadout_entries if loadout_entries is not None else _load_loadout_entries(proof)):
        char_key = str(entry.get("char_key") or "")
        if not char_key:
            continue

        character_level_raw = (
            entry.get("character_level")
            if entry.get("character_level") is not None
            else entry.get("char_level")
            if entry.get("char_level") is not None
            else entry.get("level")
        )

        weapon_template = str(entry.get("weapon_template") or "") or None
        weapon_catalog_entry = weapon_catalog.get(weapon_template or "")
        raw_weapon_name = str(entry.get("weapon_name") or "")
        if raw_weapon_name == weapon_template:
            raw_weapon_name = ""
        weapon_name = str(
            raw_weapon_name
            or (weapon_catalog_entry or {}).get("weaponName")
            or weapon_template
            or "未知武器"
        )

        equips: list[dict] = []
        for equip in sorted(entry.get("equips") or [], key=lambda item: int(item.get("slot") or 0)):
            if not isinstance(equip, dict):
                continue
            item_id = str(equip.get("item_id") or "") or None
            equip_catalog_entry = equip_catalog.get(item_id or "")
            equips.append(
                {
                    "slot": int(equip.get("slot") or 0),
                    "itemId": item_id,
                    "pieceName": str(
                        equip.get("piece_name")
                        or (equip_catalog_entry or {}).get("pieceName")
                        or item_id
                        or "未知装备"
                    ),
                    "suitName": str(equip.get("suit_name") or "") or None,
                    "partName": str(
                        equip.get("part_name")
                        or (equip_catalog_entry or {}).get("partName")
                        or ""
                    )
                    or None,
                    "iconUrl": (equip_catalog_entry or {}).get("iconUrl"),
                    # 排轴导出补全（2026-07-05 排轴器开发者需求）：精锻等级对 + 副词条实值/等级
                    "enhanceLevels": [
                        item for item in (equip.get("enhance_levels") or []) if isinstance(item, dict)
                    ],
                    "stats": [
                        item for item in (equip.get("stats") or []) if isinstance(item, dict)
                    ],
                }
            )
        equips = _normalize_payload_equip_slots(equips)

        # 武器三条词条技能各自的等级（同一把武器三词条可不同级，单一 refine 表达不了）；
        # 数字技能 id 尽量解析为字符串 id，失败保留数字原样。
        skill_id_map = _load_num_id_str_skill_map()
        weapon_skills: list[dict] = []
        for skill in entry.get("weapon_source_skills") or entry.get("weapon_refine_stats") or []:
            if not isinstance(skill, dict):
                continue
            raw_skill_id = str(skill.get("skill_id") or "")
            if not raw_skill_id:
                continue
            weapon_skills.append(
                {
                    "skillKey": skill_id_map.get(raw_skill_id, raw_skill_id),
                    "level": int(skill.get("level")) if skill.get("level") is not None else None,
                    "potentialLevel": (
                        int(skill.get("potential_level")) if skill.get("potential_level") is not None else None
                    ),
                }
            )

        # Never publish a hybrid loadout (for example, 爆破单元's template
        # carrying 四二式·肃阵's source skills).  The parser repairs weapon
        # moves when the surrounding snapshots contain the previous owner;
        # this is the final fail-closed guard for incomplete legacy traces.
        expected_weapon_main_skill = f"sk_{weapon_template}" if weapon_template else None
        weapon_main_skills = {
            str(skill.get("skillKey") or "")
            for skill in weapon_skills
            if str(skill.get("skillKey") or "").startswith("sk_wpn_")
        }
        weapon_skill_mismatch = bool(
            expected_weapon_main_skill
            and weapon_main_skills
            and expected_weapon_main_skill not in weapon_main_skills
        )
        if weapon_skill_mismatch:
            weapon_skills = []

        weapon_refine = (
            int(entry.get("weapon_refine"))
            if entry.get("weapon_refine") is not None
            else None
        )
        if weapon_skill_mismatch and entry.get("weapon_refine_source") == "source_skill":
            weapon_refine = None

        weapon_level_raw = int(entry.get("weapon_level")) if entry.get("weapon_level") is not None else None
        if not weapon_level_raw:
            # 包内 weaponLv 恒为 0：用 baseAtk 阶梯反查真实等级
            weapon_level_raw = _infer_weapon_level_from_base_atk(
                weapon_catalog_entry,
                entry.get("weapon_base_atk") or entry.get("weaponBaseAtk"),
            )

        loadouts[char_key] = {
            "characterLevel": int(character_level_raw) if character_level_raw is not None else None,
            "characterPotential": int(entry.get("potential")) if entry.get("potential") is not None else None,
            "weapon": {
                "weaponTemplate": weapon_template,
                "weaponName": weapon_name,
                "weaponLevel": weapon_level_raw,
                "weaponRefine": weapon_refine,
                "iconUrl": (weapon_catalog_entry or {}).get("iconUrl"),
                "skills": weapon_skills,
            }
            if weapon_name and weapon_name != "?"
            else None,
            "equips": equips,
        }

    return loadouts


def _canonical_character_keys(
    parsed: dict,
    *,
    loadout_entries: list[dict],
) -> list[str]:
    battle_duration_ms = max(int(parsed["battle"]["duration_ms"]), 0)
    participant_by_key = {str(entry["character_key"]): entry for entry in parsed["participants"]}
    role_skill_keys = {str(entry["character_key"]) for entry in parsed["role_skill_stats"]}
    active_keys = set(participant_by_key)
    active_keys.update(role_skill_keys)

    for event in parsed["timeline_events"]:
        if event.get("lane_type") != "skill":
            continue
        try:
            ts_ms_from_start = int(event.get("ts_ms_from_start") or 0)
        except (TypeError, ValueError):
            ts_ms_from_start = 0
        if ts_ms_from_start > battle_duration_ms:
            continue
        source_key = event.get("source_character_key")
        target_key = event.get("target_character_key")
        source_character_key = str(source_key) if _is_character_key(source_key) else None
        target_character_key = str(target_key) if _is_character_key(target_key) else None

        if source_character_key and target_character_key and source_character_key == target_character_key:
            continue
        if source_character_key:
            active_keys.add(source_character_key)
        if target_character_key:
            active_keys.add(target_character_key)

    if not loadout_entries:
        return [
            str(entry["character_key"])
            for entry in parsed["battle"]["roster"]
            if str(entry["character_key"]) in active_keys
        ]

    loadout_order: list[str] = []
    for entry in sorted(loadout_entries, key=lambda item: int(item.get("slot") or 0)):
        char_key = _resolve_loadout_character_key(entry.get("char_key"), active_keys) or ""
        if char_key and char_key in active_keys and char_key not in loadout_order:
            loadout_order.append(char_key)

    extra_keys: list[str] = []
    for entry in parsed["battle"]["roster"]:
        character_key = str(entry["character_key"])
        if character_key in loadout_order or character_key in extra_keys:
            continue
        if character_key in active_keys:
            extra_keys.append(character_key)

    return loadout_order + extra_keys


def _parse_line_timestamp_ms(line: str) -> tuple[int, str] | None:
    match = _TIMESTAMP_RE.match(line)
    if not match:
        return None
    hours, minutes, seconds, millis = (int(part) for part in match.groups())
    relative_ms = (((hours * 60) + minutes) * 60 + seconds) * 1000 + millis
    hint = f"{hours:02d}-{minutes:02d}-{seconds:02d}-{millis:03d}"
    return relative_ms, hint


def _find_line_index_for_abs(records: list[dict], target_abs_ms: int) -> int:
    for record in records:
        if record["abs_ts_ms"] >= target_abs_ms:
            return int(record["line_index"])
    return int(records[-1]["line_index"]) if records else 0


def _parse_game_timer_end_elapsed_ms(line: str) -> int | None:
    match = _GAME_TIMER_END_ELAPSED_RE.search(line)
    if not match:
        return None
    try:
        elapsed_ms = int(match.group(1))
        sane = int(match.group(2))
    except ValueError:
        return None
    if not sane or elapsed_ms <= 0:
        return None
    return elapsed_ms


def _parse_official_timer_end_elapsed_ms(line: str) -> int | None:
    match = _OFFICIAL_TIMER_END_ELAPSED_RE.search(line)
    if not match:
        return None
    try:
        elapsed_ms = int(match.group(1))
    except ValueError:
        return None
    return elapsed_ms if elapsed_ms > 0 else None


def _parse_timer_end_elapsed_ms(line: str) -> int | None:
    return _parse_official_timer_end_elapsed_ms(line) or _parse_game_timer_end_elapsed_ms(line)


def _is_loadout_context_line(line: str) -> bool:
    return " LOADOUT slot=" in line or "LOADOUT slot=" in line or " LOADOUT_STATS " in line or "LOADOUT_STATS " in line


def _find_loadout_context_start_line(lines: list[str], *, start_line: int, min_line: int) -> int | None:
    last_loadout_line: int | None = None
    for index in range(start_line, min_line - 1, -1):
        if _is_loadout_context_line(lines[index]):
            last_loadout_line = index
            break
    if last_loadout_line is None:
        return None

    block_start = last_loadout_line
    for index in range(last_loadout_line - 1, min_line - 1, -1):
        if _is_loadout_context_line(lines[index]):
            block_start = index
            continue
        if lines[index].strip() == "":
            continue
        break
    return block_start


def _find_context_start_line(records: list[dict], *, start_line: int, min_line: int, lines: list[str] | None = None) -> int:
    context_records = [
        record
        for record in records
        if record["is_dungeon_context"] and min_line <= int(record["line_index"]) <= start_line
    ]
    context_start = int(context_records[-1]["line_index"]) if context_records else start_line
    if lines is None:
        return context_start
    loadout_start = _find_loadout_context_start_line(lines, start_line=context_start, min_line=min_line)
    return min(context_start, loadout_start) if loadout_start is not None else context_start


def _extend_timer_tail_end_line(
    records: list[dict],
    *,
    timer_end_record: dict,
    next_start_line: int,
    tail_ms: int = _POST_TIMER_TAIL_MS,
) -> int:
    timer_end_line = int(timer_end_record["line_index"])
    timer_end_abs_ms = int(timer_end_record["abs_ts_ms"])
    end_line = timer_end_line
    for record in records:
        line_index = int(record["line_index"])
        if line_index <= timer_end_line:
            continue
        if line_index >= next_start_line:
            break
        if int(record["abs_ts_ms"]) - timer_end_abs_ms > tail_ms:
            break
        if (
            record["is_hit"]
            or record["is_dungeon_context"]
            or record["is_official_timer_start"]
            or record["is_game_timer_start"]
        ):
            break
        end_line = line_index
    return end_line


def _merged_timer_start_records(records: list[dict]) -> list[dict]:
    # Packet battle-state cycles are phases while an explicit official
    # challenge remains open. Never merge starts by elapsed-time proximity.
    merged = []
    active_official = None
    active_packet = None
    for record in records:
        if record.get('is_boundary') and (
                active_official is None or record.get('game_id') in (None, '', active_official[0])):
            active_official = None
            active_packet = None
        if record['is_official_timer_start']:
            identity = (record.get('game_id'), record.get('challenge_start_ts'))
            if (active_official is not None and identity[1] not in (None, '', '0')
                    and identity == active_official):
                continue
            active_official = identity
            active_packet = None
            merged.append(record)
        elif record['is_official_timer_end']:
            if active_official is not None and record.get('game_id') == active_official[0]:
                active_official = None
                active_packet = None
        elif record['is_game_timer_start']:
            if active_official is not None:
                continue
            identity = (record.get('timer_source'), record.get('timer_start_tick'))
            if identity[1] is not None and identity == active_packet:
                continue
            active_packet = identity
            merged.append(record)
        elif record['is_game_timer_end']:
            active_packet = None
    return merged


def _split_trace_into_battles(raw_content: str, *, idle_split_ms: int = _IDLE_SPLIT_MS) -> list[dict]:
    lines = raw_content.splitlines(keepends=True)
    timestamped_records: list[dict] = []
    hit_records: list[dict] = []
    clock_origin_ms: int | None = None

    for index, line in enumerate(lines):
        parsed = _parse_line_timestamp_ms(line)
        if parsed is None:
            continue
        relative_ms, hint = parsed
        # Fixed time-of-day coordinates; a late observation is not a new day.
        # Official challenge segmentation below uses explicit event order.
        if clock_origin_ms is None:
            clock_origin_ms = relative_ms
        absolute_ms = clock_origin_ms + (relative_ms - clock_origin_ms) % _DAY_MS
        timer_fields = _extract_fields(line) if any(token in line for token in ("TIMER", "GAME_INST_")) else {}
        record = {
            "line_index": index,
            "game_id": timer_fields.get("gameId"),
            "challenge_start_ts": timer_fields.get("challengeStartTs"),
            "timer_source": timer_fields.get("source"),
            "timer_start_tick": timer_fields.get("startMs"),
            "is_boundary": any(token in line for token in ("GAME_TIMER_RESET ", "GAME_INST_RESTART ", "GAME_INST_LEAVE ")),
            "abs_ts_ms": absolute_ms,
            "hint": hint,
            "is_hit": " HP_V2 " in line,
            "is_dungeon_context": "DUNGEON_CONTEXT" in line,
            "is_official_timer_start": "OFFICIAL_TIMER_START" in line,
            "is_game_timer_start": "GAME_TIMER_START" in line,
            "is_official_timer_end": "OFFICIAL_TIMER_END" in line,
            "is_game_timer_end": "GAME_TIMER_END" in line,
            "is_timer_end": "OFFICIAL_TIMER_END" in line or "GAME_TIMER_END" in line,
            "timer_elapsed_ms": _parse_timer_end_elapsed_ms(line),
        }
        timestamped_records.append(record)
        if record["is_hit"]:
            hit_records.append(record)

    if not hit_records:
        raise ValueError("未在日志中识别到可用 battle。")

    timer_start_records = _merged_timer_start_records(timestamped_records)
    if timer_start_records:
        segments: list[dict] = []
        for timer_index, start_record in enumerate(timer_start_records):
            marker_start_line = int(start_record["line_index"])
            min_start_line = (
                int(timer_start_records[timer_index - 1]["line_index"]) + 1
                if timer_index > 0
                else 0
            )
            previous_timer_end_line = max(
                (
                    int(record["line_index"])
                    for record in timestamped_records
                    if record["is_timer_end"] and int(record["line_index"]) < marker_start_line
                ),
                default=-1,
            )
            min_start_line = max(min_start_line, previous_timer_end_line + 1)
            start_line = _find_context_start_line(
                timestamped_records,
                start_line=marker_start_line,
                min_line=min_start_line,
                lines=lines,
            )
            next_start_line = (
                int(timer_start_records[timer_index + 1]["line_index"])
                if timer_index + 1 < len(timer_start_records)
                else len(lines)
            )
            if start_record["is_official_timer_start"]:
                timer_end_record = next(
                    (
                        record
                        for record in timestamped_records
                        if record["is_official_timer_end"]
                        and record.get("game_id") == start_record.get("game_id")
                        and marker_start_line <= int(record["line_index"]) < next_start_line
                    ),
                    None,
                )
                if timer_end_record is None:
                    timer_end_record = next(
                        (record for record in timestamped_records
                         if record.get('is_boundary')
                         and record.get('game_id') in (None, '', start_record.get('game_id'))
                         and marker_start_line < int(record['line_index']) < next_start_line),
                        None,
                    )
            else:
                timer_end_record = next(
                    (
                        record
                        for record in timestamped_records
                        if record["is_timer_end"]
                        and marker_start_line <= int(record["line_index"]) < next_start_line
                    ),
                    None,
                )
            if timer_end_record is not None:
                hit_end_line = int(timer_end_record["line_index"])
                end_line = _extend_timer_tail_end_line(
                    timestamped_records,
                    timer_end_record=timer_end_record,
                    next_start_line=next_start_line,
                )
            else:
                end_line = next_start_line - 1
                hit_end_line = end_line
            battle_hits = [
                record
                for record in hit_records
                if marker_start_line <= int(record["line_index"]) <= hit_end_line
            ]
            if not battle_hits:
                continue
            content = "".join(lines[start_line : end_line + 1]).strip()
            if not content:
                continue
            segments.append(
                {
                    "battle_index": len(segments) + 1,
                    "start_line_index": start_line,
                    "content": content,
                    "first_hit_hint": str(battle_hits[0]["hint"]),
                    "last_hit_hint": str(battle_hits[-1]["hint"]),
                    "first_hit_line_index": int(battle_hits[0]["line_index"]),
                }
            )
        if segments:
            return segments

    timer_end_records = [
        record
        for record in timestamped_records
        if record["is_timer_end"] and record.get("timer_elapsed_ms")
    ]
    if timer_end_records:
        segments = []
        previous_end_line = -1
        for timer_end_record in timer_end_records:
            if int(timer_end_record["line_index"]) <= previous_end_line:
                continue
            elapsed_ms = int(timer_end_record["timer_elapsed_ms"])
            end_abs_ms = int(timer_end_record["abs_ts_ms"])
            start_abs_ms = max(end_abs_ms - elapsed_ms, 0)
            marker_start_line = max(_find_line_index_for_abs(timestamped_records, start_abs_ms), previous_end_line + 1)
            start_line = _find_context_start_line(
                timestamped_records,
                start_line=marker_start_line,
                min_line=previous_end_line + 1,
                lines=lines,
            )
            end_line = _extend_timer_tail_end_line(
                timestamped_records,
                timer_end_record=timer_end_record,
                next_start_line=len(lines),
            )
            battle_hits = [
                record
                for record in hit_records
                if start_line <= int(record["line_index"]) <= end_line
                and start_abs_ms <= int(record["abs_ts_ms"]) <= end_abs_ms
            ]
            if not battle_hits:
                previous_end_line = max(previous_end_line, end_line)
                continue
            content = "".join(lines[start_line : end_line + 1]).strip()
            if not content:
                previous_end_line = max(previous_end_line, end_line)
                continue
            segments.append(
                {
                    "battle_index": len(segments) + 1,
                    "start_line_index": start_line,
                    "content": content,
                    "first_hit_hint": str(battle_hits[0]["hint"]),
                    "last_hit_hint": str(battle_hits[-1]["hint"]),
                    "first_hit_line_index": int(battle_hits[0]["line_index"]),
                }
            )
            previous_end_line = max(previous_end_line, end_line)
        if segments:
            return segments

    battle_hit_spans: list[tuple[int, int]] = []
    span_start = 0
    for index in range(1, len(hit_records)):
        if hit_records[index]["abs_ts_ms"] - hit_records[index - 1]["abs_ts_ms"] > idle_split_ms:
            battle_hit_spans.append((span_start, index - 1))
            span_start = index
    battle_hit_spans.append((span_start, len(hit_records) - 1))

    segment_start_lines: list[int] = []
    for hit_span_start, _ in battle_hit_spans:
        first_hit = hit_records[hit_span_start]
        start_abs_ms = max(int(first_hit["abs_ts_ms"]) - idle_split_ms, 0)
        segment_start_lines.append(_find_line_index_for_abs(timestamped_records, start_abs_ms))

    segments: list[dict] = []
    for segment_index, (hit_span_start, hit_span_end) in enumerate(battle_hit_spans):
        first_hit = hit_records[hit_span_start]
        last_hit = hit_records[hit_span_end]
        start_line = segment_start_lines[segment_index]
        if segment_index + 1 < len(segment_start_lines):
            end_line = max(segment_start_lines[segment_index + 1] - 1, start_line)
        else:
            end_line = len(lines) - 1
        content = "".join(lines[start_line : end_line + 1]).strip()
        if not content:
            continue
        segments.append(
            {
                "battle_index": segment_index + 1,
                "start_line_index": start_line,
                "content": content,
                "first_hit_hint": str(first_hit["hint"]),
                "last_hit_hint": str(last_hit["hint"]),
                "first_hit_line_index": int(first_hit["line_index"]),
            }
        )
    return segments


def _timeline_effect_payload(effect: dict) -> dict:
    payload: dict = {
        "zone": effect.get("zone"),
        "element": effect.get("element"),
    }
    for source_key, target_key in (
        ("rate", "rate"),
        ("base_rate", "baseRate"),
        ("tick_rate", "tickRate"),
        ("max_rate", "maxRate"),
    ):
        value = effect.get(source_key)
        if value is None:
            continue
        payload[target_key] = value
    return payload


def _camelize_state_key(key: str) -> str:
    parts = key.split("_")
    return parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:])


def _character_state_payload(value):
    if isinstance(value, list):
        return [_character_state_payload(item) for item in value]
    if isinstance(value, dict):
        return {
            _camelize_state_key(str(key)): _character_state_payload(item)
            for key, item in value.items()
        }
    return value


def _contract_tag_payload(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    tag_id = value.get("tagId", value.get("tag_id"))
    score = value.get("score")
    try:
        tag_id_int = int(tag_id)
        score_int = int(score or 0)
    except (TypeError, ValueError):
        return None
    raw_group_id = value.get("groupId") if value.get("groupId") is not None else value.get("group_id")
    try:
        group_id_int = int(raw_group_id) if raw_group_id is not None else None
    except (TypeError, ValueError):
        group_id_int = None
    icon_id = value.get("iconId") or value.get("icon_id") or value.get("icon")
    icon_url = value.get("iconUrl") or value.get("icon_url") or value.get("iconPath") or value.get("icon_path")
    if not icon_url and isinstance(icon_id, str) and icon_id.startswith("icon_activity_contract_tag_"):
        icon_url = f"/images/contract-tag/{icon_id}.png"
    payload: dict[str, Any] = {
        "tagId": tag_id_int,
        "score": score_int,
        "name": value.get("name") or value.get("tagName") or value.get("tag_name"),
        "description": value.get("description"),
        "iconId": icon_id,
        "iconUrl": icon_url,
        "buffId": value.get("buffId") or value.get("buff_id"),
        "groupId": group_id_int,
        "conflictId": value.get("conflictId") or value.get("conflict_id"),
        "terms": value.get("terms") if isinstance(value.get("terms"), list) else [],
        "values": value.get("values") if isinstance(value.get("values"), dict) else {},
    }
    return payload


def _contract_tags_payload(values: object) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        return []
    return [payload for value in values if (payload := _contract_tag_payload(value)) is not None]


def _build_payload_from_segment(
    *,
    segment: dict,
    source_file_name: str,
    reference_date,
    proof: dict | None,
    raw_loadout_entries: list[dict] | None = None,
    raw_char_skill_levels: dict[str, dict[str, int]] | None = None,
    critical_attributes_override_by_actor: dict[str, dict] | None = None,
) -> dict:
    loadout_override_by_char = {
        str(entry.get("char_key") or entry.get("character_key") or ""): entry
        for entry in (raw_loadout_entries or [])
        if isinstance(entry, dict)
        and str(entry.get("char_key") or entry.get("character_key") or "")
    }
    parsed = parse_upload_battle_log_text(
        str(segment["content"]),
        file_name=source_file_name,
        first_hit_hint=segment.get("first_hit_hint"),
        last_hit_hint=segment.get("last_hit_hint"),
        reference_date=reference_date,
        loadout_override_by_char=loadout_override_by_char,
        critical_attributes_override_by_actor=critical_attributes_override_by_actor,
    )
    loadout_entries = _loadout_entries_with_fallback(
        proof,
        parsed.get("loadout"),
        raw_loadout_entries=raw_loadout_entries,
    )
    loadout_by_key = _build_loadout_by_character_key(proof, loadout_entries=loadout_entries)
    canonical_keys = _canonical_character_keys(parsed, loadout_entries=loadout_entries)
    canonical_key_set = set(canonical_keys)
    parsed_roster_by_key = {
        str(entry["character_key"]): entry
        for entry in parsed["battle"]["roster"]
    }
    parsed_participants_by_key = {
        str(entry["character_key"]): entry
        for entry in parsed["participants"]
    }
    loadout_name_by_key = {
        str(entry.get("char_key") or ""): str(entry.get("char_name") or "")
        for entry in loadout_entries
        if str(entry.get("char_key") or "")
    }

    # 技能等级（v33 排轴导出）：优先本段 CHAR_SKILLS，缺则用整份 trace 的全局提取
    # （bridge 在战前落盘，常在 battle 分段之外）；只保留本角色前缀的技能
    # （武器词条技能由 weapon.refine 表达，不重复导出）。
    char_skill_levels: dict[str, dict[str, int]] = dict(raw_char_skill_levels or {})
    for owner_key, entries in (parsed.get("char_skills") or {}).items():
        if isinstance(entries, list):
            char_skill_levels[str(owner_key)] = {
                str(item.get("skill_key")): int(item.get("level") or 0)
                for item in entries
                if item.get("skill_key") and int(item.get("level") or 0) > 0
            }

    roster: list[dict] = []
    for index, character_key in enumerate(canonical_keys, start=1):
        parsed_entry = parsed_roster_by_key.get(character_key, {})
        loadout_payload = _lookup_loadout_payload(loadout_by_key, character_key)
        skills: list[dict] = []
        for lookup_key in _loadout_lookup_keys(character_key):
            levels_for_char = char_skill_levels.get(lookup_key)
            if levels_for_char:
                skills = [
                    {"skillKey": skill_key, "level": level}
                    for skill_key, level in sorted(levels_for_char.items())
                    if skill_key.startswith(lookup_key)
                ]
                break
        roster.append(
            {
                "slot": index,
                "characterKey": character_key,
                "characterName": str(
                    parsed_entry.get("character_name")
                    or _lookup_loadout_name(loadout_name_by_key, character_key)
                    or character_key
                ),
                "accountDisplayName": None,
                "characterLevel": loadout_payload.get("characterLevel"),
                "characterPotential": loadout_payload.get("characterPotential"),
                "weapon": loadout_payload.get("weapon"),
                "equips": loadout_payload.get("equips", []),
                "skills": skills,
            }
        )

    participants = [
        {
            "characterKey": entry["character_key"],
            "characterName": entry["character_name"],
            "accountDisplayName": None,
            "totalDamage": entry["total_damage"],
            "dps": entry["dps"],
            "rdps": entry["rdps"],
            "maxHit": entry["max_hit"],
            "critRate": entry["crit_rate"],
        }
        for entry in parsed["participants"]
        if str(entry["character_key"]) in canonical_key_set
    ]

    timeline_events = [
        {
            "tsMsFromStart": event["ts_ms_from_start"],
            "laneType": event["lane_type"],
            "sourceCharacterKey": event["source_character_key"],
            "sourceCharacterName": event["source_character_name"],
            "targetCharacterKey": event["target_character_key"],
            "targetCharacterName": event["target_character_name"],
            "targetPlayerKey": event.get("target_player_key"),
            "targetEnemyKey": event.get("target_enemy_key"),
            "eventType": event["event_type"],
            "eventKey": event["event_key"],
            "eventGroupKey": event.get("event_group_key"),
            "eventName": event["event_name"],
            "value": event["value"],
            "damageElement": event.get("damage_element"),
            "damageSchool": event.get("damage_school"),
            "poiseDamage": event.get("poise_damage"),
            "rdpsContributions": [
                {
                    "characterKey": item["character_key"],
                    "characterName": item["character_name"],
                    "value": item["value"],
                }
                for item in event.get("rdps_contributions") or []
                if str(item["character_key"]) in canonical_key_set
            ],
            "hitContext": event.get("hit_context"),
            "durationMs": event["duration_ms"],
            "actualStartMsFromStart": event.get("actual_start_ms_from_start"),
            "actualEndMsFromStart": event.get("actual_end_ms_from_start"),
            "actualDurationMs": event.get("actual_duration_ms"),
            "effects": [_timeline_effect_payload(item) for item in event.get("effects") or []],
            "dynamicEffects": [_timeline_effect_payload(item) for item in event.get("dynamic_effects") or []],
            "important": event["important"],
        }
        for event in parsed["timeline_events"]
        if int(event["ts_ms_from_start"]) <= int(parsed["battle"]["duration_ms"])
        if (
            not _is_character_key(event.get("source_character_key"))
            or str(event.get("source_character_key")) in canonical_key_set
        )
        and (
            not _is_character_key(event.get("target_character_key"))
            or str(event.get("target_character_key")) in canonical_key_set
        )
    ]

    role_skill_stats = [
        {
            "characterKey": entry["character_key"],
            "characterName": entry["character_name"],
            "accountDisplayName": None,
            "skillKey": entry["skill_key"],
            "skillName": entry["skill_name"],
            "castCount": entry["cast_count"],
            "totalDamage": entry["total_damage"],
            "avgDamage": entry["avg_damage"],
            "maxDamage": entry["max_damage"],
        }
        for entry in parsed["role_skill_stats"]
        if str(entry["character_key"]) in canonical_key_set
    ]

    contract_tags = _contract_tags_payload(parsed["battle"].get("contract_tags") or parsed["battle"].get("contractTags"))
    contract_tag_score = parsed["battle"].get("contract_tag_score")
    if contract_tag_score is None and contract_tags:
        contract_tag_score = sum(int(tag.get("score") or 0) for tag in contract_tags)

    timer_end_seen = bool(
        parsed["battle"].get("timer_end_seen")
        or parsed["battle"].get("official_timer_end_seen")
    )
    clear_flag = bool(parsed["battle"].get("clear_flag")) and timer_end_seen
    rdps_damage_basis = parsed.get("rdps_damage_basis") or parsed["battle"].get("rdps_damage_basis") or {}
    loadout_fallback_used = any(bool(entry.get("_loadout_fallback_used")) for entry in loadout_entries)

    return {
        "battle": {
            "dungeonKey": parsed["battle"]["dungeon_key"],
            "dungeonName": parsed["battle"]["dungeon_name"],
            "bossKey": parsed["battle"]["boss_key"],
            "bossName": parsed["battle"]["boss_name"],
            "battleStartAt": parsed["battle"]["battle_start_at"],
            "battleEndAt": parsed["battle"]["battle_end_at"],
            "durationMs": parsed["battle"]["duration_ms"],
            "clearFlag": clear_flag,
            "totalDamage": parsed["battle"]["total_damage"],
            "totalDps": parsed["battle"]["total_dps"],
            "roster": roster,
            "battleFingerprint": parsed["battle"]["battle_fingerprint"],
            "parserVersion": parsed["battle"]["parser_version"],
            "rulesVersion": parsed["battle"]["rules_version"],
            "timeSource": parsed["battle"].get("time_source"),
            "timelineZeroSource": parsed["battle"].get("timeline_zero_source"),
            "timerStartSeen": parsed["battle"].get("timer_start_seen"),
            "timerEndSeen": parsed["battle"].get("timer_end_seen"),
            "officialTimerStartSeen": parsed["battle"].get("official_timer_start_seen"),
            "officialTimerEndSeen": parsed["battle"].get("official_timer_end_seen"),
            "officialTimerObservedElapsedMs": parsed["battle"].get(
                "official_timer_observed_elapsed_ms"
            ),
            "packetTimerElapsedMs": parsed["battle"].get("packet_timer_elapsed_ms"),
            "timerIntegrityOk": parsed["battle"].get("timer_integrity_ok"),
            "timerIntegrityReasons": list(parsed["battle"].get("timer_integrity_reasons") or []),
            "timerEvidence": parsed["battle"].get("timer_evidence"),
            "timerStartInferred": parsed["battle"].get("timer_start_inferred"),
            "timerWindowValid": parsed["battle"].get("timer_window_valid"),
            "rdpsPreflightOk": rdps_damage_basis.get("rdps_preflight_ok"),
            "rdpsStrictOk": rdps_damage_basis.get("rdps_strict_ok"),
            "rdpsPreflightBlockerCount": rdps_damage_basis.get("preflight_blocker_count"),
            "bossIdentitySource": parsed["battle"].get("boss_identity_source"),
            "dungeonContextId": parsed["battle"].get("dungeon_context_id"),
            "dungeonIdentitySource": parsed["battle"].get("dungeon_identity_source"),
            "loadoutFallbackUsed": loadout_fallback_used,
            "contractTagScore": contract_tag_score,
            "contractTags": contract_tags,
        },
        "participants": participants,
        "characterStates": [
            _character_state_payload(state)
            for state in parsed.get("character_states") or []
            if str(state.get("character_key") or "") in canonical_key_set
        ],
        "timelineEvents": timeline_events,
        "roleSkillStats": role_skill_stats,
        "casts": [
            {
                "tsMsFromStart": cast["ts_ms_from_start"],
                "endMsFromStart": cast.get("end_ms_from_start"),
                "characterKey": cast["character_key"],
                "skillKey": cast["skill_key"],
                "skillName": cast.get("skill_name"),
                "skillSource": cast.get("skill_source"),
                "recoversEnergy": bool(cast.get("recovers_energy")),
            }
            for cast in parsed.get("casts") or []
            if str(cast.get("character_key") or "") in canonical_key_set
        ],
    }


def _is_uploadable_battle_payload(payload: dict) -> bool:
    battle = payload.get("battle") if isinstance(payload, dict) else None
    if not isinstance(battle, dict):
        return False
    try:
        total_damage = int(battle.get("totalDamage") or 0)
    except (TypeError, ValueError):
        total_damage = 0
    if total_damage <= 0:
        return False
    if not payload.get("participants"):
        return False
    return any(
        event.get("laneType") == "skill" and (event.get("value") or 0)
        for event in payload.get("timelineEvents") or []
        if isinstance(event, dict)
    )


def _payload_fingerprint(payload: dict) -> str:
    battle = payload.get("battle") if isinstance(payload, dict) else None
    if not isinstance(battle, dict):
        return ""
    return str(battle.get("battleFingerprint") or "")


def _read_incremental_tail_text(path: Path) -> str:
    try:
        tail_bytes = int(os.environ.get("ENDFIELD_MANAGED_INCREMENTAL_TAIL_BYTES") or _MANAGED_INCREMENTAL_TAIL_BYTES)
    except ValueError:
        tail_bytes = _MANAGED_INCREMENTAL_TAIL_BYTES
    tail_bytes = max(1024 * 1024, tail_bytes)
    size = path.stat().st_size
    if size <= tail_bytes:
        return path.read_text(encoding="utf-8", errors="replace")
    with path.open("rb") as handle:
        handle.seek(max(0, size - tail_bytes))
        raw = handle.read()
    text = raw.decode("utf-8", errors="replace")
    newline_index = text.find("\n")
    if newline_index >= 0:
        text = text[newline_index + 1 :]
    return text


def build_battle_upload_payloads_from_log(
    log_path: str,
    *,
    known_fingerprints: set[str] | None = None,
    known_battle_index: int | None = None,
    include_source_metadata: bool = False,
    fast_unverified: bool = False,
) -> list[dict]:
    log_file = Path(log_path)
    use_incremental_tail = fast_unverified and known_battle_index is not None
    if fast_unverified:
        raw_content = (
            _read_incremental_tail_text(log_file)
            if use_incremental_tail
            else log_file.read_text(encoding="utf-8", errors="replace")
        )
        proof = None
    else:
        integrity = load_raw_log_integrity(log_path)
        raw_content = str(integrity.get("raw_content") or "")
        proof = integrity.get("proof") if isinstance(integrity.get("proof"), dict) else None
    reference_date = datetime.fromtimestamp(log_file.stat().st_mtime).astimezone().date()
    segments = _split_trace_into_battles(raw_content)
    source_file_name = log_file.name
    known = {str(item) for item in (known_fingerprints or set()) if str(item)}
    if known_battle_index is not None and not use_incremental_tail:
        segments = [
            segment
            for segment in segments
            if int(segment.get("battle_index") or 0) > int(known_battle_index)
        ]
    if not segments:
        return []
    raw_loadout_groups, raw_loadout_fallback = _raw_text_loadout_state(raw_content)
    raw_char_skill_levels = extract_char_skill_levels_from_text(raw_content)
    critical_overrides: dict[int, dict] = {}
    critical_states: dict[str, dict] = {}
    segment_starts: dict[int, list[int]] = {}
    for segment in segments:
        segment_starts.setdefault(int(segment["start_line_index"]), []).append(int(segment["battle_index"]))
    for line_index, raw_line in enumerate(raw_content.splitlines()):
        if line_index in segment_starts:
            snapshot = copy.deepcopy(critical_states)
            for observation in snapshot.values():
                observation["lines"] = {attr: line - line_index for attr, line in observation["lines"].items()}
            for index in segment_starts[line_index]:
                critical_overrides[index] = snapshot
        if "GAME_TIMER_RESET source=CAPTURE_SESSION_START" in raw_line:
            critical_states.clear()
        partial = " ENTITY_ATTR_UPDATE " in raw_line
        parsed = _parse_entity_stats_snapshot(raw_line.replace(" ENTITY_ATTR_UPDATE ", " ENTITY_STATS ", 1) if partial else raw_line)
        if parsed is not None and parsed[1].get("ts_ms") is not None:
            entity_id, observation = parsed
            update_critical_observation(critical_states, entity_id, observation, line_index + 1, partial=partial)

    payloads: list[dict] = []
    reverse_until_known = bool(known) and (known_battle_index is None or use_incremental_tail)
    iterable = reversed(segments) if reverse_until_known else segments
    for segment in iterable:
        raw_loadout_entries = _raw_loadout_entries_for_segment(
            raw_loadout_groups,
            raw_loadout_fallback,
            segment,
        )
        try:
            payload = _build_payload_from_segment(
                segment=segment,
                source_file_name=f"{Path(source_file_name).stem}#battle{segment['battle_index']}{Path(source_file_name).suffix}",
                reference_date=reference_date,
                proof=proof,
                raw_loadout_entries=raw_loadout_entries,
                raw_char_skill_levels=raw_char_skill_levels,
                critical_attributes_override_by_actor=critical_overrides.get(int(segment["battle_index"])),
            )
        except ValueError as exc:
            if "HP_V2" in str(exc):
                continue
            raise
        if include_source_metadata:
            payload["_sourceBattleIndex"] = int(segment.get("battle_index") or 0)
        if known and _payload_fingerprint(payload) in known:
            break
        if _is_uploadable_battle_payload(payload):
            payloads.append(payload)
    if reverse_until_known:
        payloads.reverse()
    return payloads


def build_battle_upload_payload_from_log(log_path: str) -> dict:
    payloads = build_battle_upload_payloads_from_log(log_path)
    if not payloads:
        raise ValueError("未在日志中识别到可上传的 battle。")
    return payloads[0]
