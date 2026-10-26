from __future__ import annotations

import json
import re
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from functools import lru_cache
from math import log
from pathlib import Path
from statistics import median
from typing import Any

from parser_core.battle_log_parser import (
    UNKNOWN_DUNGEON_KEY,
    UNKNOWN_DUNGEON_NAME,
    _BUFF_EFFECT_SKILL_FILTER,
    _BUFF_SKILL_FILTER,
    _CHR_BORROW_WINDOW_MS,
    _GENERIC_BUFF_PREFIXES,
    _ATTR_TYPE_BUFF_LABELS,
    _ATTR_TYPE_TO_EFFECT,
    _WEAPON_BUFF_RE,
    _apply_buff_stack_limits,
    _apply_enemy_status_bb_overrides,
    _annotate_hit_enemy_hp_state,
    _attr_type_applies_to_skill,
    _bb_duration_ms_for_enemy_defense_effect,
    _buff_stack_limit,
    _equip_buff_matches_active_suits,
    _build_special_combo_windows,
    _cap_enemy_overkill_damage,
    _enemy_hp_state_key,
    _allocate_rdps_for_hit,
    _coerce_float,
    _coerce_int,
    _coerce_optional_float,
    _parse_zone_values as _parse_runtime_zone_values,
    _parse_dpd_raw_line as _parse_runtime_dpd_raw_line,
    _collect_buff_labels,
    _collect_zone_effects,
    _canonical_num_table_skill_id,
    _canonical_hit_skill_id,
    _canonical_hit_skill_id_from_cast_context,
    _character_base_stats,
    _dedupe_mirrored_buff_windows,
    _derive_static_self_multiplier_entries,
    _damage_school_from_element,
    _damage_element_from_dpd_raw,
    _duration_ms_from_seconds,
    _effect_applies_to_damage_element,
    _extract_character_key,
    _extract_dynamic_effect_specs,
    _extract_enemy_key,
    _extract_fields,
    _extend_buff_records_from_packet_modifiers,
    _infer_skill_damage_element,
    _infer_skill_action_damage_element,
    _infer_related_buff_end_times,
    _is_noise_buff,
    _merge_buff_windows,
    _normalize_buff_duration_ms,
    _normalize_buff_id,
    _normalize_effect_element,
    _packet_mapping_applies,
    _packet_mapping_stack_limit,
    _packet_buff_semantic_candidates,
    _packet_numeric_skill_hint,
    _packet_numeric_buff_hint,
    _canonical_packet_buff_id,
    _preserve_raw_numeric_internal_trigger_buff_id,
    _classify_packet_buff_record,
    _parse_loadout_suits,
    _merge_loadout_snapshot,
    _parse_hint_timestamp_ms,
    _parse_loadout_slot_snapshot,
    _parse_loadout_stats_snapshot,
    _parse_prefixed_timestamp_ms,
    _prefer_packet_buff_record,
    _resolve_character_name,
    _resolve_dungeon_context,
    _resolve_enemy_name,
    _resolve_skill_family_key,
    _resolve_skill_name,
    _resolve_skill_profile,
    _infer_skill_damage_school,
    _is_runtime_numeric_skill_id,
    _apply_same_frame_trigger_skill_mappings,
    _infer_missing_hit_damage_schools,
    _RDPS_ALLOCATABLE_ZONES,
    _canonical_runtime_buff_skill_id,
    _same_frame_trigger_buff_for_runtime_skill,
    _runtime_skill_number,
    _runtime_skill_trigger_chain_mapping,
    _trigger_damage_identity_from_buff,
    _collect_party_actor_ids,
    _infer_context_enemy_hint,
    _recover_enemy_target_from_mislabeled_actor,
    _record_has_enemy_defense_effect,
    _resolve_weapon_buff_sources,
    _safe_positive_rate,
    _should_ignore_rate_buff,
    _weapon_buff_matches_active_weapon,
    _window_matches_packet_uids,
    _window_effects_at_ts,
)


ZONE_LABELS = {
    "atk": "攻击",
    "dmg_inc": "增伤",
    "fragile": "脆弱",
    "vuln_taken": "易伤",
    "amp": "增幅",
    "res": "减抗",
    "combo": "连击",
    "crit": "暴击",
}
ZONE_ORDER = {
    "atk": 0,
    "dmg_inc": 1,
    "fragile": 2,
    "vuln_taken": 3,
    "amp": 4,
    "res": 5,
    "combo": 6,
    "crit": 7,
}
ELEMENT_LABELS = {
    "physical": "物理",
    "fire": "灼热",
    "pulse": "电磁",
    "cryst": "寒冷",
    "natural": "自然",
    "spell": "法术",
}
DPD_ZONE_BUCKETS = {
    "dmg_inc": ("atk", 1),
    "amp": ("atk", 3),
    "combo": ("atk", 4),
    "vuln_taken": ("def", 1),
    "fragile": ("def", 5),
}

_HIT_SEQ_RE = re.compile(r"\bHP_V2\s+#(\d+)\b")
_ACTOR_MAP_RE = re.compile(r"\bACTOR_MAP\b")
_DPD_RAW_RE = re.compile(
    r'DPD_RAW\s+#(?P<seq>\d+).*?\bcalc=(?P<calc>[-\d.eE+]+)\s+'
    r'atkScale=(?P<atk_scale>[-\d.eE+]+)\s+blocked=(?P<blocked>\d+)\s+'
    r'damageType=(?P<damage_type>0x[0-9A-Fa-f]+|\S+)\s+'
    r'decorateMask=(?P<decorate_mask>0x[0-9A-Fa-f]+|\S+)\s+'
    r'collider="(?P<collider>[^"]*)"\s+'
    r'atkZones=\[(?P<atk_zones>[^\]]*)\]\s+defZones=\[(?P<def_zones>[^\]]*)\]'
)
_BASELINE_RE = re.compile(r"\bBASELINE\s+#(?P<seq>\d+)\s+(?P<body>.*)")
_PKT_MOD_RE = re.compile(r"\bPKT_MOD\s+#(?P<seq>\d+)\s+atk=\[(?P<atk>[^\]]*)\]\s+def=\[(?P<def>[^\]]*)\]")
_PKT_ATTR_RE = re.compile(r"\bPKT_ATTR\s+#(?P<seq>\d+)\s+atk=\[(?P<atk>[^\]]*)\]\s+def=\[(?P<def>[^\]]*)\]")
_BASELINE_KV_RE = re.compile(r"(-?\d+)=([-\d.eE+]+)")
_RAW_LOG_PROOF_BEGIN_PREFIX = "## ENDFIELD_RAW_LOG_INTEGRITY_BEGIN sep="
_RAW_LOG_PROOF_END = "## ENDFIELD_RAW_LOG_INTEGRITY_END"
_REPO_ROOT = Path(__file__).resolve().parents[3]
_XLSX_NS = {
    "a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}


def _read_json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _normalize_value_options(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _selected_option_for_level(options: list[Any], level: Any) -> Any:
    if not options or level is None or isinstance(level, bool) or not isinstance(level, (int, str)):
        return None
    try:
        level_int = int(level)
    except (TypeError, ValueError):
        return None
    if 0 <= level_int < len(options):
        return options[level_int]
    return None


def _sub_affix_key_order(raw_key: Any) -> tuple[int, str]:
    match = re.search(r"(\d+)", str(raw_key))
    return (int(match.group(1)) if match else 99, str(raw_key))


def _build_affix_rows(item: dict[str, Any], levels: list[int] | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    levels = levels or []
    main_attr = item.get("主词条")
    if isinstance(main_attr, dict):
        rows.append(
            {
                "kind": "main",
                "index": 0,
                "desc": _clean_text(main_attr.get("desc")),
                "value": main_attr.get("value"),
                "value_options": _normalize_value_options(main_attr.get("value")),
                "level": None,
                "selected_value": main_attr.get("value"),
            }
        )
    sub_attrs = item.get("副词条")
    if isinstance(sub_attrs, dict):
        for index, key in enumerate(sorted(sub_attrs.keys(), key=_sub_affix_key_order)):
            attr = sub_attrs.get(key)
            if not isinstance(attr, dict):
                continue
            options = _normalize_value_options(attr.get("value"))
            level = levels[index] if index < len(levels) else None
            selected = _selected_option_for_level(options, level)
            rows.append(
                {
                    "kind": "sub",
                    "index": index,
                    "desc": _clean_text(attr.get("desc")),
                    "value": selected,
                    "value_options": options,
                    "level": level,
                    "selected_value": selected,
                }
            )
    return rows


@lru_cache(maxsize=1)
def _load_equip_affix_catalog() -> dict[str, dict[str, Any]]:
    catalog: dict[str, dict[str, Any]] = {}
    equip_root = _REPO_ROOT / "data" / "akedata" / "equip" / "items"
    if not equip_root.exists():
        return catalog
    for path in sorted(equip_root.glob("*.json")):
        payload = _read_json_file(path)
        if not isinstance(payload, dict):
            continue
        suit_id = str(payload.get("suitID") or path.stem)
        suit_name = _clean_text(payload.get("套组名称") or payload.get("displayName") or payload.get("name"))
        equip_items = payload.get("equip")
        if not isinstance(equip_items, dict):
            continue
        for item_id, item in equip_items.items():
            if not isinstance(item, dict):
                continue
            item_key = str(item.get("itemId") or item_id)
            catalog[item_key] = {
                "item_id": item_key,
                "piece_name": _clean_text(item.get("name")),
                "suit_id": suit_id,
                "suit_name": suit_name,
                "part_name": _clean_text(item.get("部位")),
                "affixes": _build_affix_rows(item),
            }
    return catalog


@lru_cache(maxsize=1)
def _load_weapon_affix_catalog() -> dict[str, dict[str, Any]]:
    catalog: dict[str, dict[str, Any]] = {}
    weapon_root = _REPO_ROOT / "data" / "akedata" / "weapon" / "items"
    if not weapon_root.exists():
        return catalog
    for path in sorted(weapon_root.glob("*.json")):
        payload = _read_json_file(path)
        if not isinstance(payload, dict):
            continue
        weapon_id = str(payload.get("weaponId") or path.stem)
        catalog[weapon_id] = {
            "weapon_template": weapon_id,
            "weapon_name": _clean_text(payload.get("title") or payload.get("name") or weapon_id),
            "weapon_base_atk_values": payload.get("baseAtk") if isinstance(payload.get("baseAtk"), list) else [],
            "weapon_skilllist": payload.get("skilllist") if isinstance(payload.get("skilllist"), list) else [],
        }
    return catalog


def _extract_embedded_raw_log_proof(text: str) -> dict[str, Any] | None:
    begin_idx = text.rfind(_RAW_LOG_PROOF_BEGIN_PREFIX)
    if begin_idx < 0:
        return None
    begin_line_end = text.find("\n", begin_idx)
    if begin_line_end < 0:
        return None
    end_marker = "\n" + _RAW_LOG_PROOF_END
    end_idx = text.find(end_marker, begin_line_end + 1)
    if end_idx < 0:
        return None
    try:
        proof = json.loads(text[begin_line_end + 1:end_idx])
    except json.JSONDecodeError:
        return None
    return proof if isinstance(proof, dict) else None


def _enrich_equip_row(equip: dict[str, Any]) -> dict[str, Any]:
    item_id = str(equip.get("item_id") or equip.get("itemId") or "")
    meta = _load_equip_affix_catalog().get(item_id, {})
    row = dict(equip)
    row.setdefault("item_id", item_id)
    row.setdefault("piece_name", meta.get("piece_name") or item_id)
    row.setdefault("suit_id", meta.get("suit_id") or "")
    row.setdefault("suit_name", meta.get("suit_name") or "")
    row.setdefault("part_name", meta.get("part_name") or "")
    levels = row.get("enhance_levels")
    if not isinstance(levels, list):
        levels = []
    levels = [int(value) for value in levels if isinstance(value, int) or str(value).lstrip("-").isdigit()]
    raw_affixes = row.get("affixes") if isinstance(row.get("affixes"), list) else meta.get("affixes") or []
    affixes: list[dict[str, Any]] = []
    main_attr: dict[str, Any] | None = None
    sub_attrs: list[dict[str, Any]] = []
    for raw_attr in raw_affixes:
        if not isinstance(raw_attr, dict):
            continue
        attr = dict(raw_attr)
        if attr.get("kind") == "sub":
            index = int(attr.get("index") or 0)
            level = levels[index] if index < len(levels) else attr.get("level")
            options = attr.get("value_options") if isinstance(attr.get("value_options"), list) else []
            selected = _selected_option_for_level(options, level)
            attr["level"] = level
            attr["selected_value"] = selected
            if selected is not None:
                attr["value"] = selected
            sub_attrs.append(attr)
        elif main_attr is None:
            main_attr = attr
        affixes.append(attr)
    row["enhance_levels"] = levels
    row["affixes"] = affixes
    row["main_attr"] = row.get("main_attr") or main_attr
    row["sub_attrs"] = row.get("sub_attrs") if isinstance(row.get("sub_attrs"), list) and row.get("sub_attrs") else sub_attrs
    try:
        failed_times = int(row.get("enhance_failed_times"))
    except (TypeError, ValueError):
        failed_times = None
    row["enhance_failed_times"] = failed_times if failed_times and 0 < failed_times <= 10000 else None
    return row


def _is_valid_equip_row(equip: dict[str, Any]) -> bool:
    item_id = str(equip.get("item_id") or equip.get("itemId") or "")
    return item_id.startswith("item_")


_ATTR_NAME_TO_TYPE = {
    "力量": 39,
    "敏捷": 40,
    "智识": 41,
    "意志": 42,
}


@lru_cache(maxsize=1)
def _load_character_attr_type_catalog() -> dict[str, dict[str, int | None]]:
    root = _REPO_ROOT / "data" / "local_tables" / "character" / "items"
    catalog: dict[str, dict[str, int | None]] = {}
    if not root.is_dir():
        return catalog
    for path in root.glob("chr_*.json"):
        payload = _read_json_file(path)
        raw = payload.get("raw") if isinstance(payload, dict) and isinstance(payload.get("raw"), dict) else {}
        main_attr_type = raw.get("mainAttrType")
        sub_attr_type = raw.get("subAttrType")
        try:
            main_attr_int = int(main_attr_type) if main_attr_type is not None else None
        except (TypeError, ValueError):
            main_attr_int = None
        try:
            sub_attr_int = int(sub_attr_type) if sub_attr_type is not None else None
        except (TypeError, ValueError):
            sub_attr_int = None
        catalog[path.stem] = {
            "main_attr_type": main_attr_int,
            "sub_attr_type": sub_attr_int,
        }
    return catalog


def _estimate_visible_panel_attack(
    character_key: str,
    loadout_row: dict[str, Any],
    base_stats: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(base_stats, dict):
        return None
    attr_types = _load_character_attr_type_catalog().get(character_key, {})
    main_attr_type = attr_types.get("main_attr_type")
    sub_attr_type = attr_types.get("sub_attr_type")
    if main_attr_type is None or sub_attr_type is None:
        return None

    ability_totals = {
        39: float(base_stats.get("attr_39") or 0.0),
        40: float(base_stats.get("attr_40") or 0.0),
        41: float(base_stats.get("attr_41") or 0.0),
        42: float(base_stats.get("attr_42") or 0.0),
    }
    atk_pct_bonus = 0.0
    flat_atk_bonus = 0.0
    sub_attr_pct_bonus = 0.0

    for skill in loadout_row.get("weapon_source_skills") or []:
        if not isinstance(skill, dict):
            continue
        bb = skill.get("bb") if isinstance(skill.get("bb"), dict) else {}
        for key, raw_value in bb.items():
            try:
                value = float(raw_value)
            except (TypeError, ValueError):
                continue
            lowered = str(key).lower()
            if lowered == "atk":
                atk_pct_bonus += value
            elif lowered == "mainattr":
                ability_totals[main_attr_type] = ability_totals.get(main_attr_type, 0.0) + value
            elif lowered == "second_attr_up":
                sub_attr_pct_bonus += value
            elif lowered in {"str", "agi", "wisd", "will"}:
                attr_type = _ATTR_NAME_TO_TYPE.get({"str": "力量", "agi": "敏捷", "wisd": "智识", "will": "意志"}[lowered])
                if attr_type is not None:
                    ability_totals[attr_type] = ability_totals.get(attr_type, 0.0) + value

    for equip in loadout_row.get("equips") or []:
        if not isinstance(equip, dict):
            continue
        for stat in equip.get("stats") or []:
            if not isinstance(stat, dict):
                continue
            name = str(stat.get("name") or "")
            try:
                value = float(stat.get("value") or 0.0)
            except (TypeError, ValueError):
                continue
            attr_type = _ATTR_NAME_TO_TYPE.get(name)
            if attr_type is not None:
                ability_totals[attr_type] = ability_totals.get(attr_type, 0.0) + value
            elif name == "攻击力":
                flat_atk_bonus += value

    if sub_attr_pct_bonus:
        ability_totals[sub_attr_type] = ability_totals.get(sub_attr_type, 0.0) * (1.0 + sub_attr_pct_bonus)

    try:
        character_base_atk = float(base_stats.get("atk") or 0.0)
    except (TypeError, ValueError):
        character_base_atk = 0.0
    try:
        weapon_base_atk = float(loadout_row.get("weapon_base_atk") or 0.0)
    except (TypeError, ValueError):
        weapon_base_atk = 0.0
    if character_base_atk <= 0 or weapon_base_atk <= 0:
        return None

    main_attr_total = ability_totals.get(main_attr_type, 0.0)
    sub_attr_total = ability_totals.get(sub_attr_type, 0.0)
    ability_bonus_multiplier = 1.0 + int(main_attr_total) * 0.005 + int(sub_attr_total) * 0.002
    panel_attack_estimate = ((character_base_atk + weapon_base_atk) * (1.0 + atk_pct_bonus) + flat_atk_bonus) * ability_bonus_multiplier
    return {
        "main_attr_type": main_attr_type,
        "sub_attr_type": sub_attr_type,
        "main_attr_total": main_attr_total,
        "sub_attr_total": sub_attr_total,
        "atk_pct_bonus": atk_pct_bonus,
        "flat_atk_bonus": flat_atk_bonus,
        "sub_attr_pct_bonus": sub_attr_pct_bonus,
        "ability_bonus_multiplier": ability_bonus_multiplier,
        "panel_attack_estimate": panel_attack_estimate,
    }


def _damage_doc_workbook_path() -> Path:
    return _REPO_ROOT.parent / "endfield_docs" / "干员属性.xlsx"


@lru_cache(maxsize=1)
def _load_damage_doc_sheet_rows() -> dict[str, list[list[str | None]]]:
    path = _damage_doc_workbook_path()
    if not path.exists():
        return {}
    sheets: dict[str, list[list[str | None]]] = {}
    try:
        with zipfile.ZipFile(path) as zf:
            shared_strings: list[str] = []
            if "xl/sharedStrings.xml" in zf.namelist():
                root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
                for si in root.findall("a:si", _XLSX_NS):
                    texts = [node.text or "" for node in si.iterfind(".//a:t", _XLSX_NS)]
                    shared_strings.append("".join(texts))

            workbook = ET.fromstring(zf.read("xl/workbook.xml"))
            rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
            rel_map: dict[str, str] = {}
            for rel in rels:
                rid = rel.attrib.get("Id")
                target = rel.attrib.get("Target")
                if rid and target:
                    rel_map[rid] = target

            def cell_value(cell: ET.Element) -> str | None:
                cell_type = cell.attrib.get("t")
                if cell_type == "inlineStr":
                    texts = [node.text or "" for node in cell.iterfind(".//a:t", _XLSX_NS)]
                    return "".join(texts)
                value = cell.find("a:v", _XLSX_NS)
                if value is None:
                    return None
                raw = value.text
                if cell_type == "s":
                    try:
                        return shared_strings[int(raw)]
                    except Exception:
                        return raw
                return raw

            sheet_root = workbook.find("a:sheets", _XLSX_NS)
            if sheet_root is None:
                return {}
            for sheet in sheet_root:
                name = sheet.attrib.get("name")
                rid = sheet.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
                target = rel_map.get(rid or "")
                if not name or not target:
                    continue
                sheet_path = "xl/" + target if not target.startswith("xl/") else target
                if sheet_path not in zf.namelist():
                    continue
                xml_root = ET.fromstring(zf.read(sheet_path))
                rows: list[list[str | None]] = []
                for row in xml_root.findall(".//a:sheetData/a:row", _XLSX_NS):
                    values = [cell_value(cell) for cell in row.findall("a:c", _XLSX_NS)]
                    rows.append(values)
                sheets[name] = rows
    except Exception:
        return {}
    return sheets


def _parse_doc_scalar(value: str | None) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("%"):
        text = text[:-1]
        try:
            return float(text) / 100.0
        except ValueError:
            return None
    try:
        return float(text)
    except ValueError:
        return None


@lru_cache(maxsize=1)
def _load_damage_doc_skill_scalar_catalog() -> dict[str, dict[str, dict[int, float]]]:
    rows = _load_damage_doc_sheet_rows().get("干员技能属性") or []
    catalog: dict[str, dict[str, dict[int, float]]] = defaultdict(lambda: defaultdict(dict))
    if not rows:
        return {}
    for row in rows[1:]:
        if len(row) < 5:
            continue
        skill_id = str(row[2] or "").strip()
        key = str(row[3] or "").strip()
        if not skill_id or not key:
            continue
        for index, raw_value in enumerate(row[4:16], start=1):
            scalar = _parse_doc_scalar(raw_value)
            if scalar is None:
                continue
            catalog[skill_id][key][index] = scalar
    return {
        skill_id: {key: dict(levels) for key, levels in values.items()}
        for skill_id, values in catalog.items()
    }


def _skill_doc_scalar(skill_key: str | None, skill_level: int | None) -> float | None:
    if not skill_key or not skill_level or skill_level <= 0:
        return None
    catalog = _load_damage_doc_skill_scalar_catalog()
    candidate_keys = [str(skill_key)]
    text = str(skill_key)
    for candidate in (
        text.removesuffix("_blocked"),
        text.removesuffix("_projhit"),
        text.removesuffix("_projhit_blocked"),
    ):
        if candidate and candidate not in candidate_keys:
            candidate_keys.append(candidate)
    if text.endswith("_projhit"):
        base = text.removesuffix("_projhit")
        if base and base not in candidate_keys:
            candidate_keys.append(base)
    for candidate_key in candidate_keys:
        rows = catalog.get(candidate_key, {})
        if not rows:
            continue
        for key_name in ("atk_scale", "display_atk_scale"):
            values = rows.get(key_name)
            if isinstance(values, dict) and skill_level in values:
                return values[skill_level]
    return None


def _extract_loadout_from_text(text: str) -> list[dict[str, Any]]:
    def _loadout_from_raw_lines() -> list[dict[str, Any]]:
        self_scene_loadout_by_char: dict[str, dict[str, Any]] = {}
        sync_char_bag_loadout_by_char: dict[str, dict[str, Any]] = {}
        orphan_loadout_by_char: dict[str, dict[str, Any]] = {}
        current_reason: str | None = None
        for raw_line in text.splitlines():
            reason_match = re.search(r"\bLOADOUT\s+reason=([^\s]+)", raw_line)
            if reason_match:
                current_reason = reason_match.group(1)
                if current_reason == "SC_SELF_SCENE_INFO":
                    self_scene_loadout_by_char = {}
                elif current_reason == "SC_SYNC_CHAR_BAG_INFO":
                    sync_char_bag_loadout_by_char = {}
                elif current_reason == "BATTLE_START":
                    orphan_loadout_by_char = {}
                continue
            snapshot: dict[str, Any] | None = None
            if " LOADOUT_STATS " in raw_line or "LOADOUT_STATS " in raw_line:
                snapshot = _parse_loadout_stats_snapshot(raw_line)
            elif " LOADOUT slot=" in raw_line or "LOADOUT slot=" in raw_line:
                snapshot = _parse_loadout_slot_snapshot(raw_line)
            if snapshot is None:
                continue
            char_key = str(snapshot.get("character_key") or "")
            if not char_key:
                continue
            if current_reason == "SC_SELF_SCENE_INFO":
                self_scene_loadout_by_char[char_key] = _merge_loadout_snapshot(
                    self_scene_loadout_by_char.get(char_key, {}),
                    snapshot,
                )
            elif current_reason == "SC_SYNC_CHAR_BAG_INFO":
                sync_char_bag_loadout_by_char[char_key] = _merge_loadout_snapshot(
                    sync_char_bag_loadout_by_char.get(char_key, {}),
                    snapshot,
                )
            elif current_reason is None:
                orphan_loadout_by_char[char_key] = _merge_loadout_snapshot(
                    orphan_loadout_by_char.get(char_key, {}),
                    snapshot,
                )
            else:
                orphan_loadout_by_char[char_key] = _merge_loadout_snapshot(
                    orphan_loadout_by_char.get(char_key, {}),
                    snapshot,
                )
        loadout_by_char = self_scene_loadout_by_char or sync_char_bag_loadout_by_char or orphan_loadout_by_char
        return sorted(loadout_by_char.values(), key=lambda row: int(row.get("slot") or 0))

    proof = _extract_embedded_raw_log_proof(text)
    meta = proof.get("meta") if isinstance(proof, dict) else None
    loadout = meta.get("loadout") if isinstance(meta, dict) else None
    line_loadout = _loadout_from_raw_lines()
    if isinstance(loadout, list):
        line_by_char = {
            str(row.get("character_key") or row.get("char_key") or row.get("char") or ""): row
            for row in line_loadout
            if str(row.get("character_key") or row.get("char_key") or row.get("char") or "")
        }
        merged_loadout: list[dict[str, Any]] = []
        for entry in loadout:
            if not isinstance(entry, dict):
                continue
            char_key = str(entry.get("character_key") or entry.get("char_key") or entry.get("char") or "")
            base = line_by_char.get(char_key, {})
            merged_loadout.append(_merge_loadout_snapshot(base, entry))
        loadout = merged_loadout
    else:
        loadout = line_loadout
    weapon_catalog = _load_weapon_affix_catalog()
    enriched: list[dict[str, Any]] = []
    for entry in loadout:
        if not isinstance(entry, dict):
            continue
        row = dict(entry)
        row["character_key"] = str(row.get("character_key") or row.get("char_key") or row.get("char") or "")
        row["char_key"] = str(row.get("char_key") or row["character_key"])
        row["character_name"] = str(row.get("character_name") or row.get("char_name") or row["character_key"])
        row["char_name"] = str(row.get("char_name") or row["character_name"])
        weapon_template = str(row.get("weapon_template") or "")
        weapon_meta = weapon_catalog.get(weapon_template, {})
        row.setdefault("weapon_name", weapon_meta.get("weapon_name") or weapon_template)
        base_values = weapon_meta.get("weapon_base_atk_values") if isinstance(weapon_meta, dict) else []
        weapon_level = row.get("weapon_level")
        if row.get("weapon_base_atk") is None and isinstance(base_values, list):
            try:
                level_int = int(weapon_level)
            except (TypeError, ValueError):
                level_int = 0
            if 0 < level_int <= len(base_values):
                row["weapon_base_atk"] = base_values[level_int - 1]
        row.setdefault("weapon_skilllist", weapon_meta.get("weapon_skilllist") or [])
        row["equips"] = [
            _enrich_equip_row(equip)
            for equip in row.get("equips") or []
            if isinstance(equip, dict) and _is_valid_equip_row(equip)
        ]
        enriched.append(row)
    return enriched


def _loadout_skill_ids_by_char(loadout: list[dict[str, Any]]) -> dict[str, set[str]]:
    ids_by_char: dict[str, set[str]] = {}
    for row in loadout:
        char_key = str(row.get("character_key") or row.get("char_key") or row.get("char") or "")
        if not char_key:
            continue
        ids_by_char[char_key] = {
            str(skill_id)
            for skill_id in row.get("skill_int_ids") or []
            if str(skill_id)
        }
    return ids_by_char


@lru_cache(maxsize=None)
def _load_exported_character_skill_groups(character_key: str) -> list[dict[str, Any]]:
    path = _REPO_ROOT / "data" / "akedata" / "character" / "items" / f"{character_key}.json"
    payload = _read_json_file(path)
    if not isinstance(payload, dict):
        return []
    labels = {0: "普攻", 1: "战技", 2: "终结", 3: "连携"}
    groups: list[dict[str, Any]] = []
    for skill in payload.get("skills") or []:
        if not isinstance(skill, dict):
            continue
        try:
            group_type = int(skill.get("groupType") or 0)
        except (TypeError, ValueError):
            group_type = 0
        skill_ids = [str(skill_id) for skill_id in skill.get("skillIds") or [] if skill_id]
        if not skill_ids:
            continue
        groups.append(
            {
                "group_type": group_type,
                "group_label": labels.get(group_type, str(group_type)),
                "name": _clean_text(skill.get("name")) or labels.get(group_type, ""),
                "skill_ids": skill_ids,
            }
        )
    return groups


def _active_loadout_maps_from_rows(
    loadout: list[dict[str, Any]],
) -> tuple[dict[str, set[str]], dict[str, str]]:
    active_suits_by_char: dict[str, set[str]] = {}
    active_weapons_by_char: dict[str, str] = {}
    for row in loadout:
        if not isinstance(row, dict):
            continue
        char_key = str(row.get("character_key") or row.get("char_key") or row.get("char") or "")
        if not char_key:
            continue
        weapon_template = str(row.get("weapon_template") or "")
        if weapon_template:
            active_weapons_by_char[char_key] = weapon_template
        suit_counter: dict[str, int] = defaultdict(int)
        for equip in row.get("equips") or []:
            if not isinstance(equip, dict):
                continue
            suit_id = str(equip.get("suit_id") or equip.get("suitID") or "")
            if suit_id:
                suit_counter[suit_id] += 1
        for suit_effect in row.get("suit_effects") or []:
            if not isinstance(suit_effect, dict):
                continue
            suit_id = str(suit_effect.get("suit_id") or "")
            if suit_id and suit_effect.get("active", True):
                suit_counter[suit_id] = max(suit_counter.get(suit_id, 0), int(suit_effect.get("piece_count") or 3))
        suit_blob = str(row.get("equip_suit") or row.get("equipSuit") or row.get("equip_suit_raw") or "")
        for suit_id in _parse_loadout_suits(suit_blob):
            suit_counter[suit_id] = max(suit_counter.get(suit_id, 0), 3)
        active_suits_by_char[char_key] = {
            suit_id for suit_id, count in suit_counter.items() if count >= 3
        }
    return active_suits_by_char, active_weapons_by_char


@lru_cache(maxsize=1)
def _load_skill_action_graph_candidate_map() -> dict[str, list[dict[str, Any]]]:
    return {}

    path = _REPO_ROOT / "data" / "packet_semantics" / "skill_action_graph_candidates.json"
    payload = _read_json_file(path)
    if not isinstance(payload, dict):
        return {}
    candidates = payload.get("candidates")
    if not isinstance(candidates, dict):
        return {}
    result: dict[str, list[dict[str, Any]]] = {}
    for skill_key, rows in candidates.items():
        if not isinstance(rows, list):
            continue
        normalized_rows: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            candidate_skill_id = str(row.get("candidate_skill_id") or "")
            if not candidate_skill_id:
                continue
            normalized_rows.append(
                {
                    "candidate_skill_id": candidate_skill_id,
                    "display_name": str(row.get("display_name") or candidate_skill_id),
                    "confidence": str(row.get("confidence") or "action_graph_candidate"),
                    "source": str(row.get("source") or "akedata_action_graph"),
                    "reason": str(row.get("reason") or ""),
                    "evidence": [str(item) for item in row.get("evidence") or []],
                }
            )
        if normalized_rows:
            result[str(skill_key)] = normalized_rows
    return result


def _action_graph_skill_candidates(
    skill_key: str,
    *,
    character_key: str | None,
) -> list[dict[str, Any]]:
    return []

    rows = _load_skill_action_graph_candidate_map().get(str(skill_key or ""), [])
    if not rows:
        return []
    if not character_key:
        return rows
    character_prefix = f"{character_key}_"
    return [
        row
        for row in rows
        if str(row.get("candidate_skill_id") or "").startswith(character_prefix)
    ]


def _skill_mapping_info(
    skill_key: str,
    *,
    character_key: str | None,
    target_enemy_key: str | None,
    damage_element: str | None,
    original_template_int_id: int | None,
    ts_ms: int,
    recent_actor_maps_by_char: dict[str, list[dict[str, Any]]],
    recent_skill_casts_by_char: dict[str, list[dict[str, Any]]],
    recent_numeric_buffs_by_source: dict[str, list[dict[str, Any]]],
    loadout_skill_ids_by_char: dict[str, set[str]],
    action_id: int | None = None,
    damage_unit_index: int | None = None,
    template_type: str | None = None,
) -> dict[str, Any]:
    canonical = _canonical_hit_skill_id_from_cast_context(
        skill_key, character_key, ts_ms, target_enemy_key=target_enemy_key,
        recent_skill_casts_by_char={}, recent_numeric_buffs_by_source={},
        action_id=action_id, damage_unit_index=damage_unit_index, template_type=template_type,
    ) or _canonical_hit_skill_id(skill_key, character_key)
    mapped = bool(canonical and canonical != skill_key)
    explicit = bool(canonical and canonical not in {"unknown_skill", "?"}
                    and not _runtime_skill_number(canonical))
    return {
        "status": "mapped" if mapped else "static" if explicit else "unmapped",
        "status_label": "已映射" if mapped else "静态 ID" if explicit else "未知",
        "raw_skill_key": skill_key,
        "canonical_skill_id": canonical if mapped or explicit else None,
        "evidence_kind": "NumIdStrTable" if mapped else "packet_string_id" if explicit else None,
        "candidates": [],
        "action_graph_candidates": [],
    }


def _parse_zone_values(blob: str | None) -> list[float | None]:
    return _parse_runtime_zone_values(blob)


def _parse_hex_int(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(value, 16) if value.lower().startswith("0x") else int(value)
    except ValueError:
        return None


def _parse_dpd_raw_line(line: str) -> dict[str, Any] | None:
    parsed = _parse_runtime_dpd_raw_line(line)
    if parsed is None:
        return None
    return {
        **parsed,
        "damage_type": _parse_hex_int(parsed['damage_type_raw']),
        "decorate_mask": _parse_hex_int(parsed['decorate_mask_raw']),
    }


def _parse_baseline_line(line: str) -> tuple[int, dict[int, float]] | None:
    match = _BASELINE_RE.search(line)
    if not match:
        return None
    values = {
        int(key): _coerce_float(value)
        for key, value in _BASELINE_KV_RE.findall(match.group("body"))
    }
    return int(match.group("seq")), values


def _parse_packet_modifier_line(line: str) -> tuple[int, list[str], list[str]] | None:
    match = _PKT_MOD_RE.search(line)
    if not match:
        return None
    atk = [item for item in match.group("atk").split() if item]
    defender = [item for item in match.group("def").split() if item]
    return int(match.group("seq")), atk, defender


def _parse_packet_attr_line(line: str) -> tuple[int, list[str], list[str]] | None:
    match = _PKT_ATTR_RE.search(line)
    if not match:
        return None
    atk = [item for item in match.group("atk").split() if item]
    defender = [item for item in match.group("def").split() if item]
    return int(match.group("seq")), atk, defender


def _packet_attr_allowed_effects(tokens: list[str] | None) -> set[tuple[str, str]]:
    allowed: set[tuple[str, str]] = set()
    for token in tokens or []:
        head = str(token).split(":", 1)[0]
        try:
            attr_type = int(head)
        except (TypeError, ValueError):
            continue
        mapping = _ATTR_TYPE_TO_EFFECT.get(attr_type)
        if mapping is None:
            continue
        zone, element = mapping
        allowed.add((str(zone), str(element or "all")))
    return allowed


def _format_ts_ms(ts_ms: int) -> str:
    millis = ts_ms % 1000
    total_seconds = ts_ms // 1000
    seconds = total_seconds % 60
    total_minutes = total_seconds // 60
    minutes = total_minutes % 60
    hours = total_minutes // 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


def _round4(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 4)


def _dpd_bucket_for_zone(zone: str, dpd_raw: dict[str, Any] | None) -> dict[str, Any] | None:
    if not dpd_raw:
        return None
    bucket = DPD_ZONE_BUCKETS.get(zone)
    if bucket is None:
        return None
    side, index = bucket
    values = dpd_raw.get("atk_zones") if side == "atk" else dpd_raw.get("def_zones")
    values = values or []
    if len(values) > index:
        return {"side": side, "index": index, "value": values[index]}
    return None


def _sort_zones(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(items, key=lambda item: (ZONE_ORDER.get(str(item.get("zone")), 99), str(item.get("zone"))))


def _apply_enemy_target_bb_duration_override(record: dict[str, Any]) -> None:
    _apply_enemy_status_bb_overrides(record, context_enemy_hint=None)


def _build_buff_windows(
    hits: list[dict[str, Any]],
    buff_starts: list[dict[str, Any]],
    *,
    battle_end_ms: int,
    active_suits_by_char: dict[str, set[str]] | None = None,
    active_weapons_by_char: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    _resolve_weapon_buff_sources(buff_starts)

    buff_windows: list[dict[str, Any]] = []
    for buff_record in buff_starts:
        labels = _collect_buff_labels(buff_record)
        zone_effects = _collect_zone_effects(buff_record)
        dynamic_effects = _extract_dynamic_effect_specs(buff_record)
        if not zone_effects and not dynamic_effects:
            continue
        if not _equip_buff_matches_active_suits(
            buff_record.get("event_key"),
            buff_record.get("source_character_key"),
            active_suits_by_char,
        ):
            continue
        if not _weapon_buff_matches_active_weapon(
            buff_record.get("event_key"),
            buff_record.get("source_character_key"),
            active_weapons_by_char,
        ):
            continue

        start_ts_ms = int(buff_record["ts_ms"])
        buff_duration_ms = _normalize_buff_duration_ms(
            start_ts_ms,
            event_key=buff_record.get("event_key"),
            end_ts_ms=buff_record.get("end_ts_ms"),
            raw_duration_ms=buff_record.get("raw_duration_ms"),
            battle_end_ms=battle_end_ms,
        )
        buff_windows.append(
            {
                "start_ts_ms": start_ts_ms,
                "start_line_no": buff_record.get("line_no"),
                "start_time": _format_ts_ms(start_ts_ms),
                "end_ts_ms": start_ts_ms + buff_duration_ms,
                "end_time": _format_ts_ms(start_ts_ms + buff_duration_ms),
                "duration_ms": buff_duration_ms,
                "uid": buff_record.get("uid"),
                "source_character_key": buff_record.get("source_character_key"),
                "source_character_name": buff_record.get("source_character_name"),
                "raw_source_character_key": buff_record.get("raw_source_character_key"),
                "raw_source_character_name": buff_record.get("raw_source_character_name"),
                "raw_source": buff_record.get("raw_source"),
                "target_character_key": buff_record.get("target_character_key")
                or buff_record.get("target_enemy_key"),
                "target_character_name": buff_record.get("target_character_name")
                or buff_record.get("target_enemy_name"),
                "target_player_key": buff_record.get("target_character_key"),
                "target_enemy_key": buff_record.get("target_enemy_key"),
                "owner_raw": buff_record.get("owner_raw"),
                "target_actor_id": buff_record.get("target_actor_id"),
                "source_actor_id": buff_record.get("source_actor_id"),
                "event_key": buff_record.get("event_key"),
                "event_name": " / ".join(labels) if labels else str(buff_record.get("event_key") or ""),
                "zone_effects": zone_effects,
                "dynamic_effects": dynamic_effects,
                "modifier_count": len(buff_record.get("attr_mods") or []),
                "skill_family_key": None,
                "stack_limit": _packet_mapping_stack_limit(buff_record) or _buff_stack_limit(buff_record.get("event_key")),
            }
        )

    buff_windows = _dedupe_mirrored_buff_windows(_merge_buff_windows(buff_windows))
    buff_windows.extend(_build_special_combo_windows(hits, buff_starts, battle_end_ms=battle_end_ms))
    merged = _apply_buff_stack_limits(_dedupe_mirrored_buff_windows(_merge_buff_windows(buff_windows)))
    for window in merged:
        window["start_time"] = _format_ts_ms(int(window["start_ts_ms"]))
        window["end_time"] = _format_ts_ms(int(window["end_ts_ms"]))
    return merged


def _build_buff_record_index(buff_starts: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    from parser_core.battle_log_parser import _build_buff_record_index as build_index
    return build_index(buff_starts)


def _format_effect_summary(effects: list[dict[str, Any]]) -> list[str]:
    rows: list[str] = []
    seen: set[str] = set()
    for effect in effects:
        zone = str(effect.get("zone") or "")
        zone_label = ZONE_LABELS.get(zone, zone)
        element = str(effect.get("element") or "all")
        rate = effect.get("rate")
        if rate is None and effect.get("base_rate") is not None:
            rate = effect.get("base_rate")
        try:
            base_rate = float(rate)
        except (TypeError, ValueError):
            base_rate = None
        element_text = "" if element == "all" else f"/{element}"
        if base_rate is None:
            row_text = f"{zone_label}{element_text} -"
        else:
            delayed_add_rate = float(effect.get("delayed_add_rate") or 0.0)
            delay_sec = float(effect.get("delay_sec") or 0.0)
            tick_rate = float(effect.get("tick_rate") or 0.0)
            max_rate = float(effect.get("max_rate") or 0.0)
            if delayed_add_rate > 0 and delay_sec > 0:
                row_text = (
                    f"{zone_label}{element_text} {base_rate * 100:.2f}%"
                    f" -> {(base_rate + delayed_add_rate) * 100:.2f}% @ {delay_sec:g}s"
                )
            elif tick_rate > 0 and max_rate > base_rate:
                row_text = (
                    f"{zone_label}{element_text} {base_rate * 100:.2f}%"
                    f" -> {max_rate * 100:.2f}% over time"
                )
            else:
                row_text = f"{zone_label}{element_text} {base_rate * 100:.2f}%"
        condition = effect.get("condition")
        if isinstance(condition, dict) and str(condition.get("type") or "") == "target_hp_ratio_lte":
            threshold = _safe_positive_rate(condition.get("threshold"))
            if threshold is not None:
                row_text = f"{row_text} (target HP <= {threshold * 100:.2f}%)"
        elif isinstance(condition, dict) and str(condition.get("type") or "") == "damage_type_in":
            elements = [str(item) for item in condition.get("elements") or [] if str(item or "")]
            if elements:
                row_text = f"{row_text} (damage type: {', '.join(elements)})"
        if row_text in seen:
            continue
        seen.add(row_text)
        rows.append(row_text)
    return rows


def _build_buff_audit_rows(
    buff_starts: list[dict[str, Any]],
    *,
    battle_end_ms: int,
    included_windows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    _resolve_weapon_buff_sources(buff_starts)
    included_uids = {
        str(window.get("uid") or "")
        for window in included_windows or []
        if window.get("uid")
    }
    for record in buff_starts:
        buff_id = _normalize_buff_id(record.get("event_key"))
        raw_buff_id = _normalize_buff_id(record.get("raw_event_key") or buff_id)
        packet_classification = _classify_packet_buff_record(record)
        semantic_candidates = _packet_buff_semantic_candidates(record)
        packet_mapping = record.get("packet_mapping") if isinstance(record.get("packet_mapping"), dict) else None
        labels = _collect_buff_labels(record)
        zone_effects = _collect_zone_effects(record)
        dynamic_effects = _extract_dynamic_effect_specs(record)
        effective_duration_ms = _normalize_buff_duration_ms(
            int(record.get("ts_ms") or 0),
            event_key=record.get("event_key"),
            end_ts_ms=record.get("end_ts_ms"),
            raw_duration_ms=record.get("raw_duration_ms"),
            battle_end_ms=battle_end_ms,
        )
        reasons: list[str] = []
        has_effects = bool(zone_effects or dynamic_effects)
        included = has_effects and (not included_uids or str(record.get("uid") or "") in included_uids)
        merged = has_effects and not included
        if _is_noise_buff(buff_id):
            reasons.append("配置为噪声/载体 buff")
        if _should_ignore_rate_buff(buff_id):
            reasons.append("规则忽略此类 rate wrapper")
        if not zone_effects and not dynamic_effects:
            reasons.append("无可识别正数效果")
        if not labels and (zone_effects or dynamic_effects):
            reasons.append("有效果但缺少展示标签")
        if merged:
            reasons.append("已被同一来源/目标/buff 的刷新链合并，不单独生效")
        if raw_buff_id.isdigit():
            if packet_mapping:
                reasons.append("numeric packet id 已通过上下文守卫映射")
            else:
                reasons.append(f"numeric packet id 未采纳：{packet_classification.get('label')}")
        status = "included" if included else "merged" if merged else "filtered"
        rows.append(
            {
                "status": status,
                "status_label": "纳入" if included else "合并" if merged else "过滤",
                "reasons": reasons,
                "line_no": record.get("line_no"),
                "uid": record.get("uid"),
                "event_key": buff_id,
                "raw_event_key": raw_buff_id,
                "event_name": " / ".join(labels) if labels else buff_id,
                "packet_mapping": packet_mapping,
                "packet_classification": packet_classification,
                "semantic_candidates": semantic_candidates,
                "source_character_key": record.get("source_character_key"),
                "source_character_name": record.get("source_character_name"),
                "raw_source": record.get("raw_source"),
                "target_character_key": record.get("target_character_key") or record.get("target_enemy_key"),
                "target_character_name": record.get("target_character_name") or record.get("target_enemy_name"),
                "owner_raw": record.get("owner_raw"),
                "start_ts_ms": record.get("ts_ms"),
                "start_time": record.get("time") or _format_ts_ms(int(record.get("ts_ms") or 0)),
                "end_ts_ms": int(record.get("ts_ms") or 0) + effective_duration_ms,
                "end_time": _format_ts_ms(int(record.get("ts_ms") or 0) + effective_duration_ms),
                "raw_end_time": _format_ts_ms(int(record["end_ts_ms"])) if record.get("end_ts_ms") is not None else None,
                "raw_duration_ms": record.get("raw_duration_ms"),
                "effective_duration_ms": effective_duration_ms,
                "bb_keys": sorted(set(str(key) for key in record.get("bb_keys") or [])),
                "bb_raw": dict(record.get("bb_raw") or {}),
                "attr_mods": list(record.get("attr_mods") or []),
                "zone_effects": zone_effects,
                "dynamic_effects": dynamic_effects,
                "effect_summary": _format_effect_summary(zone_effects + dynamic_effects),
            }
        )
    return sorted(rows, key=lambda item: (int(item.get("start_ts_ms") or 0), int(item.get("line_no") or 0)))


def _record_applicable_effect(
    *,
    window: dict[str, Any],
    effect: dict[str, Any],
    scope: str,
    rdps_credit: float | None = None,
) -> dict[str, Any]:
    source_key = window.get("source_character_key")
    raw_source_key = window.get("raw_source_character_key")
    return {
        "scope": scope,
        "source_character_key": source_key,
        "source_character_name": window.get("source_character_name")
        or _resolve_character_name(source_key)
        or source_key,
        "raw_source_character_key": raw_source_key,
        "raw_source_character_name": window.get("raw_source_character_name")
        or _resolve_character_name(raw_source_key)
        or raw_source_key,
        "raw_source": window.get("raw_source"),
        "target_character_key": window.get("target_character_key"),
        "target_character_name": window.get("target_character_name"),
        "owner_raw": window.get("owner_raw"),
        "event_key": window.get("event_key"),
        "event_name": window.get("event_name"),
        "uid": window.get("uid"),
        "uid_aliases": list(window.get("uid_aliases") or []),
        "start_ts_ms": window.get("start_ts_ms"),
        "start_time": window.get("start_time"),
        "end_ts_ms": window.get("end_ts_ms"),
        "end_time": window.get("end_time"),
        "zone": str(effect.get("zone") or ""),
        "zone_label": ZONE_LABELS.get(str(effect.get("zone") or ""), str(effect.get("zone") or "")),
        "element": str(effect.get("element") or "all"),
        "rate": _round4(float(effect.get("rate") or 0.0)),
        "rdps_credit": _round4(rdps_credit),
    }


def _record_dpd_self_residual(
    *,
    hit: dict[str, Any],
    zone: str,
    rate: float,
) -> dict[str, Any]:
    attacker_key = str(hit.get("character_key") or "")
    attacker_name = str(hit.get("character_name") or _resolve_character_name(attacker_key) or attacker_key)
    return {
        "scope": "self",
        "source_character_key": attacker_key,
        "source_character_name": attacker_name,
        "raw_source_character_key": attacker_key,
        "raw_source_character_name": attacker_name,
        "raw_source": "DPD_RAW",
        "target_character_key": attacker_key,
        "target_character_name": attacker_name,
        "owner_raw": "DPD_RAW",
        "event_key": "__dpd_self_residual__",
        "event_name": "自身基线/未归因（DPD残差）",
        "uid": None,
        "start_ts_ms": None,
        "start_time": None,
        "end_ts_ms": None,
        "end_time": None,
        "zone": zone,
        "zone_label": ZONE_LABELS.get(zone, zone),
        "element": str(hit.get("damage_element") or "all"),
        "rate": _round4(rate),
        "rdps_credit": None,
    }


def _record_baseline_self(
    *,
    hit: dict[str, Any],
    attr_type: int,
    zone: str,
    element: str,
    rate: float,
    final_value: float,
    captured: float,
) -> dict[str, Any]:
    attacker_key = str(hit.get("character_key") or "")
    attacker_name = str(hit.get("character_name") or _resolve_character_name(attacker_key) or attacker_key)
    label = _ATTR_TYPE_BUFF_LABELS.get(attr_type) or ZONE_LABELS.get(zone, zone)
    return {
        "scope": "self",
        "source_character_key": attacker_key,
        "source_character_name": attacker_name,
        "raw_source_character_key": attacker_key,
        "raw_source_character_name": attacker_name,
        "raw_source": "BASELINE",
        "target_character_key": attacker_key,
        "target_character_name": attacker_name,
        "owner_raw": "BASELINE",
        "event_key": f"__baseline_attr_{attr_type}__",
        "event_name": f"自身属性基线（{label} / attrType={attr_type}）",
        "uid": None,
        "start_ts_ms": None,
        "start_time": None,
        "end_ts_ms": None,
        "end_time": None,
        "zone": zone,
        "zone_label": ZONE_LABELS.get(zone, zone),
        "element": element,
        "rate": _round4(rate),
        "rdps_credit": None,
        "baseline_final": _round4(final_value),
        "baseline_captured": _round4(captured),
    }










def _explain_hit(
    hit: dict[str, Any],
    buff_windows: list[dict[str, Any]],
    buff_records_by_uid: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    allocation = _allocate_rdps_for_hit(
        hit,
        buff_windows,
        include_debug=True,
        buff_records_by_uid=buff_records_by_uid,
    )
    hit["zones"] = allocation.get("zones") or []
    hit["ignored_effects"] = allocation.get("ignored_effects") or []
    hit["product_external_multiplier"] = allocation.get("product_external_multiplier")
    hit["attacker_share"] = allocation.get("attacker_share")
    hit["external_pool"] = allocation.get("external_pool")
    hit["rdps_contributions"] = allocation.get("contributions_list") or []
    hit["external_sources"] = allocation.get("external_sources") or []
    hit["zone_summary"] = str(allocation.get("zone_summary") or "")
    hit["buff_source_summary"] = str(allocation.get("buff_source_summary") or "")
    hit["packet_modifier_uids"] = allocation.get("packet_modifier_uids") or {"attacker": [], "defender": []}
    hit["packet_modifier_details"] = allocation.get("packet_modifier_details") or []
    return hit


def parse_hit_debug_log_text(
    text: str,
    *,
    file_name: str | None = None,
    first_hit_hint: str | None = None,
    last_hit_hint: str | None = None,
    _allow_history: bool = True,
) -> dict[str, Any]:
    """Present the canonical parser's results without a second allocation."""
    from parser_core.battle_log_parser import (parse_raw_battle_log_text, _is_generic_runtime_skill_id,
                                               _RUNTIME_SKILL_NAME_OVERRIDES)

    proof = _extract_embedded_raw_log_proof(text)
    metadata = proof.get('meta') if isinstance(proof, dict) else None
    supplied = metadata.get('loadout') if isinstance(metadata, dict) else None
    display_loadout = []
    if isinstance(supplied, list):
        declared = Counter(str(row.get('character_key') or row.get('char_key') or row.get('char') or '')
                           for row in supplied if isinstance(row, dict))
        display_loadout = [row for row in _extract_loadout_from_text(text)
                           if declared.get(row['character_key']) == 1]
    raw = parse_raw_battle_log_text(text, file_name=file_name, first_hit_hint=first_hit_hint,
                                    last_hit_hint=last_hit_hint, include_rdps_debug=True)
    battle = raw['battle']
    context = raw.get('debug_context') or {}
    lines = text.splitlines()
    hits = []
    for item in raw.get('debug_hits') or []:
        hit = dict(item)
        number = hit.get('line_no')
        raw_line = lines[number - 1] if type(number) is int and 0 < number <= len(lines) else ''
        fields = _extract_fields(raw_line)
        original = str(hit.get('raw_skill_key') or hit.get('skill_key') or '')
        canonical = str(hit.get('skill_key') or '')
        unresolved = _is_generic_runtime_skill_id(canonical) or not canonical or canonical == 'unknown_skill'
        status = 'unresolved' if unresolved else 'mapped' if original != canonical else 'static'
        if original in _RUNTIME_SKILL_NAME_OVERRIDES:
            hit['skill_name'] = _RUNTIME_SKILL_NAME_OVERRIDES[original]
        hit.update({
            'raw_line': raw_line, 'time': _format_ts_ms(int(hit.get('ts_ms') or 0)),
            'src_raw': fields.get('src'), 'tgt_raw': fields.get('tgt'), 'atk_raw': fields.get('atk'),
            'raw_damage': _coerce_optional_float(fields.get('raw')), 'cum_damage': _coerce_optional_float(fields.get('cum')),
            'crit_dmg': _coerce_optional_float(fields.get('critDmg')), 'shared': _coerce_int(fields.get('shared'), default=0),
            'skill_mapping': {'status': status, 'status_label': {'unresolved': '未解析', 'mapped': '已映射', 'static': '静态 ID'}[status],
                              'raw_skill_key': original, 'canonical_skill_id': canonical,
                              'evidence_kind': 'canonical_parser_identity', 'candidates': [], 'action_graph_candidates': []},
        })
        hits.append(hit)
    windows = context.get('buff_windows') or []
    audit = []
    for item in raw.get('buff_events') or []:
        row = dict(item)
        row['effective_duration_ms'] = row.get('duration_ms')
        row['bb_raw'] = ' '.join(f'{key}={value}' for key, value in sorted((row.get('bb_values') or {}).items()))
        row['reasons'] = sorted({str(reason.get('reason')) for reason in (raw.get('rdps_preflight') or {}).get('blockers', [])
                                if reason.get('uid') == row.get('uid') and reason.get('reason')})
        if not row.get('zone_effects') and not row.get('dynamic_effects'):
            row['reasons'].append('无可识别正数效果')
        audit.append(row)
    start = context.get('start_ts_ms')
    end = context.get('end_ts_ms')
    return {
        'summary': {
            'file_name': file_name, 'hit_count': len(hits), 'buff_window_count': len(windows),
            'buff_audit_count': len(audit),
            'buff_audit_included_count': sum(row.get('status') == 'included' for row in audit),
            'buff_audit_filtered_count': sum(row.get('status') == 'filtered' for row in audit),
            'buff_audit_merged_count': sum(row.get('status') == 'merged' for row in audit),
            'dpd_count': sum(bool(hit.get('dpd_raw')) for hit in hits),
            'baseline_count': sum(bool(hit.get('baseline')) for hit in hits),
            'external_buff_hit_count': sum(bool(hit.get('external_sources')) for hit in hits),
            'first_hit_ms': start, 'first_hit_time': _format_ts_ms(start) if start is not None else None,
            'last_hit_ms': end, 'last_hit_time': _format_ts_ms(end) if end is not None else None,
            'duration_ms': battle.get('duration_ms'), 'total_damage': battle.get('total_damage'),
            'total_dps': battle.get('total_dps'), 'boss_key': battle.get('boss_key'), 'boss_name': battle.get('boss_name'),
            'dungeon_key': battle.get('dungeon_key'), 'dungeon_name': battle.get('dungeon_name'),
            'dungeon_context_id': battle.get('dungeon_context_id'), 'dungeon_identity_source': battle.get('dungeon_identity_source'),
        },
        'participants': raw.get('participants') or [], 'loadout': raw.get('loadout') or display_loadout,
        'loadout_presentation_source': 'canonical_parser' if raw.get('loadout') else 'embedded_metadata_display_only',
        'hits': hits, 'buff_windows': windows, 'buff_audit': audit,
        **{key: raw[key] for key in ('rdps_preflight','rdps_damage_basis','unresolved_hits','unresolved_buff_actions') if key in raw},
    }
