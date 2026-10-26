"""
Endfield DPS Overlay — transparent topmost window that tails dxg_trace.dat
Usage: python overlay.py
"""

import os, re, sys, time, threading, json, math
import importlib.util
import tkinter as tk
from tkinter import filedialog, messagebox
from collections import OrderedDict
from functools import lru_cache
import ctypes as ct

from log_integrity import write_embedded_raw_log

TRACE_FILE = os.environ.get(
    "ENDFIELD_PCAP_TRACE_FILE",
    os.path.join(os.environ.get("TEMP", ""), "dxg_trace.dat"),
)
STATUS_FILE = os.environ.get("ENDFIELD_PCAP_STATUS_FILE", "")
POLL_MS = 200          # UI refresh interval
TAIL_INTERVAL = 0.15   # log tail poll (seconds)
APP_DIR = os.path.dirname(os.path.abspath(__file__))
OVERLAY_BUILD_LABEL = "CORE-rDPS 20260809-1320 official_character_catalog"
OVERLAY_DEBUG = os.environ.get("ENDFIELD_OVERLAY_DEBUG") == "1"
_OVERLAY_MUTEX = None


def _acquire_single_instance_mutex() -> bool:
    global _OVERLAY_MUTEX
    if os.environ.get("ENDFIELD_OVERLAY_ALLOW_MULTIPLE") == "1":
        return True
    if os.name != "nt":
        return True
    kernel32 = ct.windll.kernel32
    mutex = kernel32.CreateMutexW(None, False, "Global\\EndfieldPCAPOverlaySingleton")
    if not mutex:
        return True
    _OVERLAY_MUTEX = mutex
    return kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


if not _acquire_single_instance_mutex():
    raise SystemExit(0)

if not os.environ.get("ENDFIELD_LOGS_DATA_ROOT"):
    _data_root = APP_DIR if os.path.isdir(os.path.join(APP_DIR, "data")) else ""
    if _data_root:
        os.environ["ENDFIELD_LOGS_DATA_ROOT"] = _data_root

_parser_core_paths = (
    os.environ.get("ENDFIELD_LOGS_PARSER_CORE"),
    os.path.join(APP_DIR, "packages", "parser_core"),
    os.path.join(os.path.dirname(APP_DIR), "newproject", "endfield-logs", "packages", "parser_core"),
)
for _parser_core_path in _parser_core_paths:
    if _parser_core_path and os.path.isdir(_parser_core_path) and _parser_core_path not in sys.path:
        sys.path.insert(0, _parser_core_path)

for _parser_core_path in _parser_core_paths:
    _pkg_init = os.path.join(_parser_core_path or "", "parser_core", "__init__.py")
    if not os.path.isfile(_pkg_init):
        continue
    _pkg_dir = os.path.join(_parser_core_path, "parser_core")
    _spec = importlib.util.spec_from_file_location(
        "parser_core",
        _pkg_init,
        submodule_search_locations=[_pkg_dir],
    )
    if _spec:
        # Execute the real package initializer. A hand-made empty ModuleType
        # lets submodules import but drops package exports and can leave a
        # partial parser package in sys.modules for later consumers.
        _module = importlib.util.module_from_spec(_spec)
        sys.modules["parser_core"] = _module
        try:
            assert _spec.loader is not None
            _spec.loader.exec_module(_module)
        except Exception:
            sys.modules.pop("parser_core", None)
            raise
        break

from parser_core.damage_core import (
    attr_type_applies_to_skill as core_attr_type_applies_to_skill,
    buff_applies_to_skill as core_buff_applies_to_skill,
    buff_effect_applies_to_skill as core_buff_effect_applies_to_skill,
    damage_element_from_dpd as core_damage_element_from_dpd,
    effect_applies_to_damage_element as core_effect_applies_to_damage_element,
    infer_damage_element as core_infer_damage_element,
)
from parser_core.live import LiveOverlayBattleParser
from pathlib import Path

_capture_source = os.path.join(APP_DIR, "src")
if os.path.isdir(_capture_source) and _capture_source not in sys.path:
    sys.path.insert(0, _capture_source)
_uploader_paths = (
    os.path.join(APP_DIR, "packages", "uploader_core"),
    os.path.join(os.path.dirname(APP_DIR), "endfield-logs", "packages", "uploader_core"),
)
for _uploader_path in _uploader_paths:
    if os.path.isdir(_uploader_path) and _uploader_path not in sys.path:
        sys.path.insert(0, _uploader_path)
from endfield_pcap.rotation_client import RotationGuideController
from endfield_pcap import rotation_client as _rotation_client
from endfield_pcap.rotation_overlay import RotationOverlayModule
from endfield_pcap.overlay_topics import get_overlay_broker
from endfield_pcap.overlay_windows import OverlayWindowManager
from endfield_pcap.overlay_control import OverlayConfigLease, attach_control_server
from endfield_pcap.overlay_window_settings import OverlaySettingsDialog

def _write_runtime_debug(extra=""):
    try:
        import parser_core as _parser_core_pkg
        import parser_core.unified as _parser_core_unified

        _debug_path = os.path.join(os.environ.get("TEMP", ""), "endfield_overlay_runtime.txt")
        with open(_debug_path, "w", encoding="utf-8") as _f:
            _f.write(f"build={OVERLAY_BUILD_LABEL}\n")
            _f.write(f"app_dir={APP_DIR}\n")
            _f.write(f"data_root={os.environ.get('ENDFIELD_LOGS_DATA_ROOT', '')}\n")
            _f.write(f"parser_core={getattr(_parser_core_pkg, '__file__', '')}\n")
            _f.write(f"unified={getattr(_parser_core_unified, '__file__', '')}\n")
            if extra:
                _f.write(f"extra={extra}\n")
    except Exception as _exc:
        try:
            _debug_path = os.path.join(os.environ.get("TEMP", ""), "endfield_overlay_runtime.txt")
            with open(_debug_path, "w", encoding="utf-8") as _f:
                _f.write(f"build={OVERLAY_BUILD_LABEL}\n")
                _f.write(f"app_dir={APP_DIR}\n")
                _f.write(f"data_root={os.environ.get('ENDFIELD_LOGS_DATA_ROOT', '')}\n")
                _f.write(f"runtime_debug_error={type(_exc).__name__}: {_exc}\n")
        except Exception:
            pass


if OVERLAY_DEBUG:
    _write_runtime_debug()


def _write_rows_debug(mode, status, rows):
    if not OVERLAY_DEBUG:
        return
    try:
        path = os.path.join(os.environ.get("TEMP", ""), "endfield_overlay_rows.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"build={OVERLAY_BUILD_LABEL}\n")
            f.write(f"mode={mode}\n")
            f.write(f"dungeon={status.get('dungeonName')}\n")
            f.write(f"elapsed={status.get('elapsed')}\n")
            for row in rows:
                f.write(
                    "row="
                    f"name={getattr(row, 'name', '')},"
                    f"dps={getattr(row, 'dps', '')},"
                    f"rdps={getattr(row, 'rdps', '')},"
                    f"total_dmg={getattr(row, 'total_dmg', '')},"
                    f"total_rd={getattr(row, 'total_rd', '')},"
                    f"max_hit={getattr(row, 'max_hit', '')},"
                    f"max_rd={getattr(row, 'max_rd', '')}\n"
                )
    except Exception:
        pass

# ── colours ──
BG        = "#1a1a2e"
FG_HEAD   = "#8888aa"
FG_NAME   = "#e0e0e0"
FG_DPS    = "#ffcc00"
FG_MAX    = "#ff6666"
FG_CRIT   = "#ff9944"
FG_TOTAL  = "#66ccff"
FG_PCT    = "#cc99ff"
BAR_COLORS = [
    "#c0392b",  "#e67e22",  "#2ecc71",  "#3498db",
    "#9b59b6",  "#1abc9c",  "#f39c12",  "#e84393",
]
ALPHA     = 0.85

# ── name → friendly label ──
CHAR_LABELS = {
    "chen":      "陈千语",
    "endminm":   "管理员",
    "endminf":   "管理员",
    "endmin":    "管理员",
    "wulfa":     "洛茜",
    "tangtang":  "汤汤",
    "pelica":    "佩丽卡",
    "karin":     "秋栗",
    "laevat":    "莱万汀",
    "ardelia":   "艾尔黛拉",
    "antal":     "安塔尔",
    "wolfgd":    "狼卫",
    "lastrite":  "别礼",
    "azrila":    "余烬",
    "pograni":   "骏卫",
    "seraph":    "赛希",
    "ikut":      "弧光",
    "avywen":    "艾维文娜",
    "deepfin":   "阿列什",
    "aurora":    "昼雪",
    "whiten":    "埃特拉",
    "dapan":     "大潘",
    "bounda":    "萤石",
    "meurs":     "卡契尔",
    "lifeng":    "黎风",
    "zhuangfy":  "庄方宜",
    "yvonne":    "伊冯",
    "aglina":    "洁尔佩塔",
    "mifu":      "弭弗",
    "camille":   "卡缪",
    "lizhiyan":  "诀",
}

# ── character → damage element ──
CHAR_ELEMENTS = {
    "chen": "physical", "endminf": "physical", "endminm": "physical",
    "endmin": "physical", "tangtang": "cryst",
    "pelica": "pulse", "karin": "fire", "laevat": "fire",
    "ardelia": "natural", "antal": "pulse", "wolfgd": "fire",
    "lastrite": "cryst", "pograni": "physical", "seraph": "cryst",
    "ikut": "pulse", "avywen": "pulse", "deepfin": "cryst",
    "aurora": "cryst", "whiten": "cryst", "dapan": "physical",
    "bounda": "natural", "meurs": "physical", "lifeng": "physical",
    "zhuangfy": "pulse", "yvonne": "cryst", "aglina": "natural",
    "mifu": "physical", "camille": "fire", "lizhiyan": "natural",
}

CHAR_TYPE_ELEMENTS = {
    "Physical": "physical",
    "Fire": "fire",
    "Pulse": "pulse",
    "Cryst": "cryst",
    "Natural": "natural",
}

MIXED_ELEMENTS = {
    "wulfa":  {"attack": "physical", "normal_skill": "fire", "combo": "physical", "ultimate": "fire"},
    "azrila": {"attack": "physical", "normal_skill": "fire", "combo": "physical", "ultimate": "fire"},
    "karin":  {"attack": "physical", "normal_skill": "fire", "combo": "physical", "ultimate": "fire"},
}

RE_CHAR_KEY = re.compile(r"(chr_\d+_[a-z]+)")
RE_ENEMY_KEY = re.compile(r"(eny_\d+_[a-z0-9]+)")
RE_COMMON_SKILL_ELEM = re.compile(r"^buff(?:_common)?_(physical|fire|pulse|cryst|natural|spell)_")
COMMON_DAMAGE_ELEM_KEYWORDS = {
    "burning_status": "fire",
}


def extract_char_key(text):
    if not text:
        return None
    m = RE_CHAR_KEY.search(text)
    if not m:
        return None
    parts = m.group(1).split("_")
    return "_".join(parts[:3]) if len(parts) >= 3 else m.group(1)


def infer_common_damage_element(skill_name):
    if not skill_name:
        return None
    skill = skill_name.lower()
    m = RE_COMMON_SKILL_ELEM.match(skill)
    if m:
        return m.group(1)
    for token, elem in COMMON_DAMAGE_ELEM_KEYWORDS.items():
        if token in skill:
            return elem
    return None


def extract_enemy_key(text):
    if not text:
        return None
    if text.startswith("eny_"):
        parts = text.split("_")
        return "_".join(parts[:3]) if len(parts) >= 3 else text
    m = RE_ENEMY_KEY.search(text)
    if not m:
        return None
    parts = m.group(1).split("_")
    return "_".join(parts[:3]) if len(parts) >= 3 else m.group(1)


def _load_enemy_catalog():
    candidates = [
        os.path.join(APP_DIR, "enemy_catalog.json"),
        os.path.join(os.path.dirname(APP_DIR), "newproject", "endfield-logs", "data", "akedata", "enemy"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
            except Exception:
                continue
        manifest_path = os.path.join(path, "manifest.json")
        items_dir = os.path.join(path, "items")
        if not os.path.isfile(manifest_path) or not os.path.isdir(items_dir):
            continue
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except Exception:
            continue
        catalog = {}
        for row in manifest:
            key = str(row.get("templateId") or "")
            if not key:
                continue
            detail = {}
            detail_path = os.path.join(items_dir, f"{key}.json")
            if os.path.isfile(detail_path):
                try:
                    with open(detail_path, "r", encoding="utf-8") as f:
                        detail = json.load(f)
                except Exception:
                    detail = {}
            enemy_tag = detail.get("enemyTag") or ""
            rarity = detail.get("rarity", row.get("rarity", 0))
            catalog[key] = {
                "name": detail.get("name") or row.get("name") or key,
                "enemyTag": enemy_tag,
                "rarity": rarity,
                "isBoss": ("领袖" in enemy_tag) or (rarity is not None and rarity >= 6),
            }
        if catalog:
            return catalog
    return {}


ENEMY_CATALOG = _load_enemy_catalog()


def _load_classifier_hints():
    candidates = [
        os.path.join(APP_DIR, "classifier_hints.json"),
        os.path.join(os.path.dirname(APP_DIR), "newproject", "endfield-logs", "data", "local_semantics", "classifier_hints.json"),
    ]
    for path in candidates:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception:
            continue
    return {}


CLASSIFIER_HINTS = _load_classifier_hints()
ATTRIBUTE_TYPE_HINTS = CLASSIFIER_HINTS.get("attributeTypeHints", {}) if isinstance(CLASSIFIER_HINTS, dict) else {}
BUFF_CLASSIFIER_HINTS = CLASSIFIER_HINTS.get("buffHints", {}) if isinstance(CLASSIFIER_HINTS, dict) else {}

RE_LOADOUT_SLOT = re.compile(
    r'LOADOUT\s+slot=(?P<slot>\d+)\s+char=(?P<char>\S+)'
    r'.*?\stemplate=(?P<template>\S+)'
    r'.*?\spotential=(?P<potential>-?\d+)'
    r'.*?\sweaponTemplate=(?P<weapon>\S+)'
    r'.*?\sweaponLv=(?P<weapon_lv>-?\d+)'
    r'\srefine=(?P<refine>-?\d+)'
    r'.*?\sequips=\{(?P<equips>[^}]*)\}'
)
RE_LOADOUT_MAP_ENTRY = re.compile(r'\[(\d+)\]=')
_LOADOUT_CATALOGS = None
_CHARACTER_CATALOG_MAPS = None


def _clean_catalog_text(value):
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _data_root_candidates():
    drive, _tail = os.path.splitdrive(APP_DIR)
    drive_root = (drive + os.sep) if drive else os.sep
    roots = [
        os.path.join(APP_DIR, "data"),
        os.path.join(APP_DIR, "endfield-logs", "data"),
        os.path.join(os.path.dirname(APP_DIR), "newproject", "endfield-logs", "data"),
        os.path.join(drive_root, "newproject", "endfield-logs", "data"),
    ]
    deduped = []
    seen = set()
    for root in roots:
        norm = os.path.normpath(root)
        if norm in seen:
            continue
        seen.add(norm)
        deduped.append(norm)
    return deduped


def _data_path_candidates(*parts):
    return [os.path.join(root, *parts) for root in _data_root_candidates()]


def _load_json_file(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _iter_json_entries(path):
    payload = _load_json_file(path)
    entries = payload.get("entries") if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, dict)]


def _load_character_catalog_maps():
    names = {}
    names_by_short = {}
    elements_by_short = {}
    for path in _data_path_candidates("local_tables", "character", "manifest.json"):
        payload = _load_json_file(path)
        if not isinstance(payload, dict):
            continue
        for entry in payload.get("entries", []):
            if not isinstance(entry, dict):
                continue
            char_id = str(entry.get("charId") or entry.get("id") or "")
            name = _clean_catalog_text(entry.get("name"))
            if char_id and name:
                names[char_id] = name
                short = char_id.rsplit("_", 1)[-1].lower()
                names_by_short[short] = name
                element = CHAR_TYPE_ELEMENTS.get(str(entry.get("charTypeId") or ""))
                if element:
                    elements_by_short[short] = element
        if names:
            break
    return names, names_by_short, elements_by_short


def _get_character_catalog_maps():
    global _CHARACTER_CATALOG_MAPS
    if _CHARACTER_CATALOG_MAPS is None:
        _CHARACTER_CATALOG_MAPS = _load_character_catalog_maps()
    return _CHARACTER_CATALOG_MAPS


def _load_character_name_map():
    return _get_character_catalog_maps()[0]


def _character_element(raw):
    if not raw:
        return None
    char_key = extract_char_key(str(raw))
    short = (char_key or str(raw)).rsplit("_", 1)[-1].lower()
    dynamic = _get_character_catalog_maps()[2].get(short)
    return dynamic or CHAR_ELEMENTS.get(short)


def _load_weapon_name_map():
    mapping = {}
    for items_dir in _data_path_candidates("akedata", "weapon", "items"):
        if not os.path.isdir(items_dir):
            continue
        for file_name in sorted(os.listdir(items_dir)):
            if not file_name.endswith(".json"):
                continue
            payload = _load_json_file(os.path.join(items_dir, file_name))
            if not isinstance(payload, dict):
                continue
            weapon_id = str(payload.get("weaponId") or "")
            title = _clean_catalog_text(payload.get("title") or payload.get("name"))
            if weapon_id and title:
                mapping[weapon_id] = title
        if mapping:
            break

    for path in _data_path_candidates("local_tables", "weapon", "manifest.json"):
        payload = _load_json_file(path)
        if not isinstance(payload, dict):
            continue
        for entry in payload.get("entries", []):
            if not isinstance(entry, dict):
                continue
            weapon_id = str(entry.get("weaponId") or entry.get("id") or "")
            name = _clean_catalog_text(entry.get("name"))
            if weapon_id and name and weapon_id not in mapping:
                mapping[weapon_id] = name
        if payload.get("entries"):
            break
    return mapping


def _normalize_value_options(value):
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _selected_option_for_level(options, level):
    if not options or level is None:
        return None
    try:
        level_int = int(level)
    except (TypeError, ValueError):
        return None
    if 0 <= level_int < len(options):
        return options[level_int]
    if 1 <= level_int <= len(options):
        return options[level_int - 1]
    return None


def _build_affix_rows(item, levels=None):
    rows = []
    levels = levels or []
    main_attr = item.get("主词条")
    if isinstance(main_attr, dict):
        rows.append(
            {
                "kind": "main",
                "index": 0,
                "desc": _clean_catalog_text(main_attr.get("desc")),
                "value": main_attr.get("value"),
                "value_options": _normalize_value_options(main_attr.get("value")),
                "level": None,
                "selected_value": main_attr.get("value"),
            }
        )
    sub_attrs = item.get("副词条")
    if isinstance(sub_attrs, dict):
        def _sub_key_order(raw_key):
            match = re.search(r"(\d+)", str(raw_key))
            return (int(match.group(1)) if match else 99, str(raw_key))

        for index, key in enumerate(sorted(sub_attrs.keys(), key=_sub_key_order)):
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
                    "desc": _clean_catalog_text(attr.get("desc")),
                    "value": selected if selected is not None else attr.get("value"),
                    "value_options": options,
                    "level": level,
                    "selected_value": selected,
                }
            )
    return rows


def _load_weapon_detail_map():
    mapping = {}
    for items_dir in _data_path_candidates("akedata", "weapon", "items"):
        if not os.path.isdir(items_dir):
            continue
        for file_name in sorted(os.listdir(items_dir)):
            if not file_name.endswith(".json"):
                continue
            payload = _load_json_file(os.path.join(items_dir, file_name))
            if not isinstance(payload, dict):
                continue
            weapon_id = str(payload.get("weaponId") or file_name[:-5])
            if not weapon_id:
                continue
            mapping[weapon_id] = {
                "weapon_id": weapon_id,
                "name": _clean_catalog_text(payload.get("title") or payload.get("name") or weapon_id),
                "rarity": payload.get("rarity"),
                "base_atk": payload.get("baseAtk") if isinstance(payload.get("baseAtk"), list) else [],
                "skilllist": payload.get("skilllist") if isinstance(payload.get("skilllist"), list) else [],
            }
        if mapping:
            break
    return mapping


def _load_equip_piece_map():
    piece_map = {}
    for items_dir in _data_path_candidates("akedata", "equip", "items"):
        if not os.path.isdir(items_dir):
            continue
        for file_name in sorted(os.listdir(items_dir)):
            if not file_name.endswith(".json"):
                continue
            payload = _load_json_file(os.path.join(items_dir, file_name))
            if not isinstance(payload, dict):
                continue
            suit_id = str(payload.get("suitID") or file_name[:-5])
            suit_name = _clean_catalog_text(
                payload.get("套组名称")
                or payload.get("displayName")
                or payload.get("name")
            )
            equip_items = payload.get("equip")
            if not isinstance(equip_items, dict):
                continue
            for item_id, item in equip_items.items():
                if not isinstance(item, dict):
                    continue
                item_key = str(item.get("itemId") or item_id or "")
                if not item_key:
                    continue
                piece_map[item_key] = {
                    "item_id": item_key,
                    "name": _clean_catalog_text(item.get("name")),
                    "part": _clean_catalog_text(item.get("部位")),
                    "suit_id": suit_id,
                    "suit_name": suit_name,
                    "main_attr": _build_affix_rows(item)[:1],
                    "sub_attrs": [row for row in _build_affix_rows(item) if row.get("kind") == "sub"],
                    "affixes": _build_affix_rows(item),
                }
        if piece_map:
            break
    return piece_map


def _get_loadout_catalogs():
    global _LOADOUT_CATALOGS
    if _LOADOUT_CATALOGS is None:
        _LOADOUT_CATALOGS = {
            "characters": _load_character_name_map(),
            "weapons": _load_weapon_name_map(),
            "weapon_details": _load_weapon_detail_map(),
            "equip_pieces": _load_equip_piece_map(),
        }
    return _LOADOUT_CATALOGS


def _lookup_character_name(char_key):
    catalogs = _get_loadout_catalogs()
    return catalogs["characters"].get(char_key) or friendly_name(char_key) or char_key


def _lookup_weapon_name(weapon_id):
    catalogs = _get_loadout_catalogs()
    return catalogs["weapons"].get(weapon_id) or weapon_id


def _lookup_weapon_meta(weapon_id):
    catalogs = _get_loadout_catalogs()
    return catalogs["weapon_details"].get(weapon_id) or {}


def _lookup_equip_piece_meta(item_id):
    catalogs = _get_loadout_catalogs()
    return catalogs["equip_pieces"].get(item_id) or {}


def _format_named_id(name, raw_id):
    clean_name = _clean_catalog_text(name)
    clean_id = _clean_catalog_text(raw_id)
    if not clean_name:
        return clean_id
    if not clean_id or clean_name == clean_id:
        return clean_name
    return f"{clean_name} ({clean_id})"


def _format_equip_piece_display(item_id):
    item_key = _clean_catalog_text(item_id)
    if not item_key:
        return "?"
    meta = _lookup_equip_piece_meta(item_key)
    piece_name = _clean_catalog_text(meta.get("name"))
    suit_name = _clean_catalog_text(meta.get("suit_name"))
    part_name = _clean_catalog_text(meta.get("part"))
    label_parts = []
    if piece_name:
        label_parts.append(piece_name)
    if suit_name and suit_name not in label_parts:
        label_parts.append(suit_name)
    if part_name and part_name not in label_parts:
        label_parts.append(part_name)
    label = " / ".join(label_parts) if label_parts else item_key
    return f"{label} ({item_key})"


def _parse_indexed_blob(blob):
    values = {}
    text = str(blob or "")
    matches = list(RE_LOADOUT_MAP_ENTRY.finditer(text))
    top_level_matches = [
        match
        for match in matches
        if text[match.end():].startswith("item_")
    ]
    for pos, match in enumerate(top_level_matches):
        try:
            idx = int(match.group(1))
        except ValueError:
            continue
        next_match = top_level_matches[pos + 1] if pos + 1 < len(top_level_matches) else None
        end = next_match.start() if next_match else len(text)
        raw_value = text[match.end():end].strip()
        if raw_value:
            values[idx] = raw_value
    return values


def _parse_int_csv(value):
    if not value:
        return []
    levels = []
    for part in str(value).split(","):
        part = part.strip()
        if not part:
            continue
        try:
            levels.append(int(part))
        except ValueError:
            continue
    return levels


def _parse_level_map(value):
    text = str(value or "")
    pairs = []
    for pattern in (r'(\d+)\s*:\s*(-?\d+)', r'\[(\d+)\]\s*=\s*(-?\d+)'):
        pairs = [(int(k), int(v)) for k, v in re.findall(pattern, text)]
        if pairs:
            break
    if not pairs:
        return []
    by_key = {}
    for key, level in pairs:
        by_key[key] = level
    return [by_key[key] for key in sorted(by_key)]


def _parse_enhance_levels(value):
    mapped = _parse_level_map(value)
    if mapped:
        return mapped
    return _parse_int_csv(value)


def _parse_enhance_failed_times(value):
    try:
        parsed = int(str(value).strip())
    except ValueError:
        return None
    if parsed <= 0 or parsed > 10000:
        return None
    return parsed


def _parse_equip_ref(raw_value):
    parts = str(raw_value or "").split("|")
    item_id = parts[0].strip()
    result = {
        "item_id": item_id,
        "enhance_levels": [],
        "enhance_failed_times": None,
        "raw": str(raw_value or ""),
    }
    for part in parts[1:]:
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        key = key.strip().lower()
        value = value.strip()
        if key in {"lv", "levels", "enhance"}:
            result["enhance_levels"] = _parse_enhance_levels(value)
        elif key in {"fail", "failed"}:
            result["enhance_failed_times"] = _parse_enhance_failed_times(value)
    return result


def _parse_indexed_equip_blob(blob):
    return {slot: _parse_equip_ref(raw_value) for slot, raw_value in _parse_indexed_blob(blob).items()}


def _parse_loadout_slot_line(line):
    match = RE_LOADOUT_SLOT.search(line or "")
    if not match:
        return None
    try:
        slot = int(match.group("slot"))
        potential = int(match.group("potential"))
        weapon_lv = int(match.group("weapon_lv"))
        refine = int(match.group("refine"))
    except ValueError:
        return None
    equip_refs = _parse_indexed_equip_blob(match.group("equips"))
    return {
        "slot": slot,
        "char_key": match.group("char"),
        "template_id": match.group("template"),
        "potential": potential,
        "weapon_template": match.group("weapon"),
        "weapon_lv": weapon_lv,
        "refine": refine,
        "equip_ids": {equip_slot: ref["item_id"] for equip_slot, ref in equip_refs.items()},
        "equip_refs": equip_refs,
    }


def _extract_observed_player_roster(lines):
    roster = set()
    for line in lines or []:
        match = RE_HIT.search(line or "")
        if not match:
            continue
        atk_key = extract_char_key(match.group(7))
        src_key = extract_char_key(match.group(5))
        for char_key in (atk_key, src_key):
            if char_key and char_key.startswith("chr_"):
                roster.add(char_key)
    return roster


def _extract_loadout_groups(lines):
    groups = []
    current_group = None
    for idx, line in enumerate(lines or []):
        if "LOADOUT reason=" in line:
            if current_group and current_group["entries"]:
                groups.append(current_group)
            current_group = {
                "reason": line.rstrip("\r\n"),
                "entries": OrderedDict(),
                "raw_lines": [line],
                "last_idx": idx,
            }
            continue
        if "LOADOUT_STATS slot=" in line:
            if current_group is not None:
                current_group["raw_lines"].append(line)
                current_group["last_idx"] = idx
            continue
        if "LOADOUT slot=" not in line:
            continue
        entry = _parse_loadout_slot_line(line)
        if not entry:
            continue
        if current_group is None:
            current_group = {
                "reason": "",
                "entries": OrderedDict(),
                "raw_lines": [],
                "last_idx": idx,
            }
        current_group["raw_lines"].append(line)
        current_group["entries"][entry["slot"]] = entry
        current_group["last_idx"] = idx
    if current_group and current_group["entries"]:
        groups.append(current_group)
    return groups


def _loadout_min_overlap(observed_roster):
    size = len(observed_roster or ())
    if size <= 0:
        return 0
    return min(size, 3)


def _extract_latest_loadout_entries(lines, observed_roster=None, min_overlap=0):
    groups = _extract_loadout_groups(lines)
    if not groups:
        return []
    observed_roster = set(observed_roster or _extract_observed_player_roster(lines))
    best_group = None
    best_key = None
    for ordinal, group in enumerate(groups):
        entries = [group["entries"][idx] for idx in sorted(group["entries"])]
        group_roster = {
            entry["char_key"]
            for entry in entries
            if entry.get("char_key", "").startswith("chr_")
        }
        overlap = len(group_roster & observed_roster) if observed_roster else 0
        if observed_roster and min_overlap > 0 and overlap < min_overlap:
            continue
        exact = 1 if observed_roster and group_roster == observed_roster else 0
        size = len(entries)
        latest = group.get("last_idx", ordinal)
        if observed_roster:
            score = (overlap, exact, size, latest)
        else:
            score = (size, latest)
        if best_key is None or score > best_key:
            best_key = score
            best_group = entries
    return best_group or []


def _extract_latest_loadout_context_lines(lines, observed_roster=None, min_overlap=0):
    groups = _extract_loadout_groups(lines)
    if not groups:
        return []
    observed_roster = set(observed_roster or _extract_observed_player_roster(lines))
    best_group = None
    best_key = None
    for ordinal, group in enumerate(groups):
        entries = [group["entries"][idx] for idx in sorted(group["entries"])]
        group_roster = {
            entry["char_key"]
            for entry in entries
            if entry.get("char_key", "").startswith("chr_")
        }
        overlap = len(group_roster & observed_roster) if observed_roster else 0
        if observed_roster and min_overlap > 0 and overlap < min_overlap:
            continue
        exact = 1 if observed_roster and group_roster == observed_roster else 0
        size = len(entries)
        latest = group.get("last_idx", ordinal)
        raw_lines = list(group.get("raw_lines") or [])
        stats_count = sum(1 for row in raw_lines if "LOADOUT_STATS slot=" in row)
        if observed_roster:
            score = (overlap, exact, stats_count, size, latest)
        else:
            score = (stats_count, size, latest)
        if best_key is None or score > best_key:
            best_key = score
            best_group = raw_lines
    return best_group or []


def _read_trace_lines(trace_path=TRACE_FILE):
    if not os.path.exists(trace_path):
        return []
    try:
        with open(trace_path, encoding="utf-8", errors="replace") as f:
            return f.readlines()
    except OSError:
        return []


def read_all_battle_log_export(trace_path=None):
    """Return the current trace contents plus a lightweight all-battles summary.

    This export path intentionally keeps every battle in the same log instead
    of slicing only the latest one.
    """
    trace_path = trace_path or TRACE_FILE
    lines = _read_trace_lines(trace_path)
    if not lines:
        return [], None

    line_times = []
    last_clock = None
    day_offset = 0.0
    hit_count = 0
    battle_count = 0
    first_hit_idx = None
    last_hit_idx = None
    in_battle = False
    last_hit_t = None

    for idx, line in enumerate(lines):
        ts_match = RE_TS.search(line)
        t_abs, last_clock, day_offset = _trace_time_from_match(
            ts_match, last_clock, day_offset
        )
        line_times.append(t_abs)

        if (
            RE_GAME_TIMER_START.search(line)
            or RE_OFFICIAL_TIMER_START.search(line)
            or RE_GAME_TIMER_RESET.search(line)
        ):
            in_battle = False
            last_hit_t = None

        if not RE_HIT.search(line) or t_abs is None:
            continue

        hit_count += 1
        if first_hit_idx is None:
            first_hit_idx = idx
        last_hit_idx = idx

        is_new_battle = (
            not in_battle
            or last_hit_t is None
            or t_abs - last_hit_t > IDLE_RESET_SEC
        )
        if is_new_battle:
            battle_count += 1
            in_battle = True
        last_hit_t = t_abs

    if first_hit_idx is None or last_hit_idx is None or hit_count <= 0:
        return [], None

    meta = {
        "battle_count": battle_count,
        "hit_count": hit_count,
        "line_count": len(lines),
        "first_hit_ts": _trace_ts_text(lines[first_hit_idx]),
        "last_hit_ts": _trace_ts_text(lines[last_hit_idx]),
    }
    return lines, meta


def build_export_loadout_summary(trace_path=TRACE_FILE, preferred_lines=None):
    lines = preferred_lines if preferred_lines is not None else []
    observed_roster = _extract_observed_player_roster(lines)
    min_overlap = _loadout_min_overlap(observed_roster)
    entries = _extract_latest_loadout_entries(
        lines,
        observed_roster=observed_roster,
        min_overlap=min_overlap,
    )
    if not entries:
        entries = _extract_latest_loadout_entries(
            _read_trace_lines(trace_path),
            observed_roster=observed_roster,
            min_overlap=min_overlap,
        )
    if not entries:
        return [], []

    summary_lines = []
    summary_rows = []
    for entry in entries:
        char_key = entry["char_key"]
        weapon_template = entry["weapon_template"]
        char_name = _lookup_character_name(char_key)
        weapon_name = _lookup_weapon_name(weapon_template)
        weapon_meta = _lookup_weapon_meta(weapon_template)
        weapon_base_atk = None
        base_atk_values = weapon_meta.get("base_atk") if isinstance(weapon_meta, dict) else []
        if isinstance(base_atk_values, list) and entry["weapon_lv"] > 0 and entry["weapon_lv"] <= len(base_atk_values):
            weapon_base_atk = base_atk_values[entry["weapon_lv"] - 1]
        equip_rows = []
        equip_texts = []
        equip_refs = entry.get("equip_refs") or {}
        for equip_slot, item_id in sorted(entry["equip_ids"].items()):
            meta = _lookup_equip_piece_meta(item_id)
            equip_ref = equip_refs.get(equip_slot) or {}
            enhance_levels = equip_ref.get("enhance_levels") or []
            affixes = []
            main_attr = []
            sub_attrs = []
            raw_affixes = meta.get("affixes") or []
            for raw_attr in raw_affixes:
                attr = dict(raw_attr)
                if attr.get("kind") == "sub":
                    idx = int(attr.get("index") or 0)
                    level = enhance_levels[idx] if idx < len(enhance_levels) else attr.get("level")
                    options = attr.get("value_options") if isinstance(attr.get("value_options"), list) else []
                    selected = _selected_option_for_level(options, level)
                    attr["level"] = level
                    attr["selected_value"] = selected
                    if selected is not None:
                        attr["value"] = selected
                    sub_attrs.append(attr)
                else:
                    main_attr.append(attr)
                affixes.append(attr)
            equip_rows.append(
                {
                    "slot": equip_slot,
                    "item_id": item_id,
                    "piece_name": _clean_catalog_text(meta.get("name")),
                    "suit_name": _clean_catalog_text(meta.get("suit_name")),
                    "part_name": _clean_catalog_text(meta.get("part")),
                    "enhance_levels": enhance_levels,
                    "enhance_failed_times": equip_ref.get("enhance_failed_times"),
                    "main_attr": main_attr[0] if main_attr else None,
                    "sub_attrs": sub_attrs,
                    "affixes": affixes,
                }
            )
            equip_texts.append(_format_equip_piece_display(item_id))

        summary_lines.append(
            f"{char_name} {char_key}：角色潜能 {entry['potential']}，"
            f"武器 {_format_named_id(weapon_name, weapon_template)}，"
            f"武器等级 {entry['weapon_lv']}，武器潜能/精炼 {entry['refine']}。"
            f"装备是 {'、'.join(equip_texts) if equip_texts else '无'}。"
        )
        summary_rows.append(
            {
                "slot": entry["slot"],
                "char_key": char_key,
                "char_name": char_name,
                "potential": entry["potential"],
                "weapon_template": weapon_template,
                "weapon_name": weapon_name,
                "weapon_level": entry["weapon_lv"],
                "weapon_refine": entry["refine"],
                "weapon_base_atk": weapon_base_atk,
                "weapon_skilllist": weapon_meta.get("skilllist") if isinstance(weapon_meta, dict) else [],
                "equips": equip_rows,
            }
        )
    return summary_lines, summary_rows


def build_export_loadout_context(trace_path=TRACE_FILE, preferred_lines=None):
    lines = preferred_lines if preferred_lines is not None else []
    observed_roster = _extract_observed_player_roster(lines)
    min_overlap = _loadout_min_overlap(observed_roster)
    context_lines = _extract_latest_loadout_context_lines(
        lines,
        observed_roster=observed_roster,
        min_overlap=min_overlap,
    )
    if not context_lines:
        context_lines = _extract_latest_loadout_context_lines(
            _read_trace_lines(trace_path),
            observed_roster=observed_roster,
            min_overlap=min_overlap,
        )
    return context_lines


def _prepend_export_context(lines, context_lines):
    if not context_lines:
        return list(lines or [])
    body_lines = list(lines or [])
    prefix_len = min(len(context_lines), len(body_lines))
    if prefix_len and body_lines[:prefix_len] == context_lines[:prefix_len]:
        return body_lines
    merged = list(context_lines)
    if merged and merged[-1].strip():
        merged.append("\n")
    merged.extend(body_lines)
    return merged


def _buff_hint_meta(buff_id):
    hint = BUFF_CLASSIFIER_HINTS.get(buff_id)
    return hint if isinstance(hint, dict) else None


def _buff_hint_effect_candidates(buff_id):
    hint = _buff_hint_meta(buff_id)
    if not hint:
        return []
    resolved = hint.get("resolvedEffectHints")
    if isinstance(resolved, list) and resolved:
        return resolved
    direct = hint.get("effectHints")
    if isinstance(direct, list):
        return direct
    return []


def _bb_pairs_have_effect_signal(bb_pairs):
    for key, val in bb_pairs:
        if val <= 0 or val >= 10:
            continue
        if _classify_effect_from_bb_key(key) is not None:
            return True
        if key == "rate":
            return True
    return False


def _buff_hint_is_non_effect(buff_id, bb_pairs):
    hint = _buff_hint_meta(buff_id)
    if not hint:
        return False
    flags = hint.get("semanticFlags") or {}
    if flags.get("decodedDetailMissing"):
        return False
    if _buff_hint_effect_candidates(buff_id):
        return False
    if _bb_pairs_have_effect_signal(bb_pairs):
        return False
    return hint.get("classification") in {"wrapper", "marker_or_utility"}


def friendly_enemy_name(raw):
    key = extract_enemy_key(raw)
    if key:
        meta = ENEMY_CATALOG.get(key)
        if meta and meta.get("name"):
            return meta["name"]
        return key
    return raw or "-"


def is_boss_enemy(raw):
    key = extract_enemy_key(raw)
    if not key:
        return False
    meta = ENEMY_CATALOG.get(key) or {}
    return bool(meta.get("isBoss"))


def stable_bar_color(raw):
    return BAR_COLORS[stable_bar_color_index(raw)]


def stable_bar_color_index(raw):
    if not raw:
        return 0
    total = 0
    for i, ch in enumerate(raw):
        total += (i + 1) * ord(ch)
    return total % len(BAR_COLORS)


def assign_distinct_bar_colors(names):
    unique_names = sorted({name for name in names if name})
    if not unique_names:
        return {}

    assigned = {}
    used_indexes = set()
    palette_size = len(BAR_COLORS)
    for name in unique_names:
        idx = stable_bar_color_index(name)
        for _ in range(palette_size):
            if idx not in used_indexes:
                break
            idx = (idx + 1) % palette_size
        used_indexes.add(idx)
        assigned[name] = BAR_COLORS[idx]
    return assigned


def format_elapsed(seconds):
    if seconds is None or seconds <= 0:
        return "--:--.---"
    minutes = int(seconds // 60)
    remain = seconds - minutes * 60
    sec_int = int(remain)
    millis = int(round((remain - sec_int) * 1000))
    if millis >= 1000:
        sec_int += 1
        millis -= 1000
    if sec_int >= 60:
        minutes += sec_int // 60
        sec_int %= 60
    return f"{minutes:02d}:{sec_int:02d}.{millis:03d}"


_BOSSRUSH_DIFFICULTY_RE = re.compile(r"^dung\d+_(?:mini)?bossrush\d+_(\d+)$")
_BOSSRUSH_DIFFICULTY_LABELS = {
    1: "一级",
    2: "二级",
    3: "三级",
    4: "四级",
}


def format_overlay_dungeon_name(battle):
    """Append a display-only difficulty label using the official stage id."""
    dungeon_name = str((battle or {}).get("dungeon_name") or "未知场地")
    dungeon_id = str(
        (battle or {}).get("dungeon_context_id")
        or (battle or {}).get("dungeon_key")
        or ""
    ).strip()
    if dungeon_name in {"", "-", "未知场地", "未知副本"}:
        return dungeon_name or "未知场地"

    label = ""
    if "takestwo" in dungeon_id:
        label = "全功率" if dungeon_id.endswith("_hard") else "普通"
    elif dungeon_id.startswith("indie_battletower"):
        if dungeon_id.endswith("_ex"):
            label = "残酷"
        elif dungeon_id.endswith("_s"):
            label = "困难"
        else:
            label = "普通"
    elif dungeon_id.startswith("indie_hard"):
        label = "苦难" if dungeon_id.endswith("_s") else "普通"
    else:
        bossrush_match = _BOSSRUSH_DIFFICULTY_RE.fullmatch(dungeon_id)
        if bossrush_match:
            label = _BOSSRUSH_DIFFICULTY_LABELS.get(int(bossrush_match.group(1)), "")

    if not label:
        for candidate in ("普通", "困难", "残酷", "苦难"):
            if dungeon_name.endswith(f"·{candidate}"):
                label = candidate
                break
    if not label or dungeon_name.endswith(f"[{label}]"):
        return dungeon_name

    display_name = dungeon_name
    if label in {"普通", "困难", "残酷", "苦难"}:
        display_name = display_name.removesuffix(f"·{label}")
    elif label == "全功率":
        display_name = display_name.removesuffix("全功率！")
    return f"{display_name}[{label}]"


SERVICE_STATE_LABELS = {
    "waiting_restart": "状态：请重启游戏",
    "waiting_game": "状态：等待游戏启动",
    "waiting_connection": "状态：等待连接",
    "waiting_handshake": "状态：等待登录握手",
    "live": "状态：已连接",
}


def read_service_status():
    if not STATUS_FILE or not os.path.exists(STATUS_FILE):
        return None
    try:
        stale = time.time() - os.path.getmtime(STATUS_FILE) > 15
        with open(STATUS_FILE, encoding="utf-8") as f:
            payload = json.load(f)
        if not isinstance(payload, dict):
            return None
        # A fatal status is intentionally the final heartbeat. Keep it
        # readable until the next client start overwrites the status file.
        if stale and not isinstance(payload.get("fatal_error"), dict):
            return None
        return payload
    except (OSError, json.JSONDecodeError):
        return None


def service_status_text(payload):
    fatal_error = payload.get("fatal_error")
    if isinstance(fatal_error, dict):
        error_type = str(fatal_error.get("type") or "未知错误")
        return f"状态：采集异常（{error_type}）"
    state = str(payload.get("state") or "")
    return SERVICE_STATE_LABELS.get(state, f"状态：{state or '-'}")


def service_metrics_text(payload):
    fatal_error = payload.get("fatal_error")
    if isinstance(fatal_error, dict):
        message = " ".join(str(fatal_error.get("message") or "").split())
        return f"错误：{message[:72] or '采集服务意外退出'}"
    metrics = payload.get("metrics") if isinstance(payload, dict) else None
    if not isinstance(metrics, dict):
        return "包:0 解:0"
    packets = int(metrics.get("packets_seen") or 0)
    messages = int(metrics.get("messages_decoded") or 0)
    events = int(metrics.get("outbound_events_emitted") or 0)
    suffix = ""
    devices = payload.get("capture_devices")
    if isinstance(devices, list) and len(devices) == 1 and isinstance(devices[0], dict):
        desc = str(devices[0].get("description") or "")
        if "Ethernet" in desc:
            suffix = " Eth"
        elif "Tunnel" in desc:
            suffix = " Tun"
        elif "Wi-Fi" in desc or "Wireless" in desc:
            suffix = " WiFi"
    handshake = ""
    session = payload.get("session")
    if isinstance(session, dict):
        client_ok = "C+" if session.get("client_login_done") else "C-"
        server_ok = "S+" if session.get("server_login_done") else "S-"
        handshake = f" {client_ok}{server_ok}"
    return f"包:{packets} 解:{messages} 事:{events}{suffix}{handshake}"


def get_damage_element(skill_name):
    if core_infer_damage_element is not None:
        return core_infer_damage_element(skill_name, extract_char_key(skill_name))
    char_key = extract_char_key(skill_name)
    if char_key:
        char_name = char_key.split("_")[-1].lower()
        if char_name in MIXED_ELEMENTS:
            s = skill_name.lower()
            if "ultimate" in s:
                return MIXED_ELEMENTS[char_name]["ultimate"]
            if "combo" in s:
                return MIXED_ELEMENTS[char_name]["combo"]
            if "normal_skill" in s:
                return MIXED_ELEMENTS[char_name]["normal_skill"]
            return MIXED_ELEMENTS[char_name]["attack"]
        elem = _character_element(char_name)
        if elem is not None:
            return elem
    return infer_common_damage_element(skill_name)


DAMAGE_TYPE_ELEMENTS = {
    0: "physical",
    2: "fire",
    3: "pulse",
    4: "cryst",
    6: "natural",
}


def _damage_element_from_dpd(dpd):
    if core_damage_element_from_dpd is not None:
        return core_damage_element_from_dpd(dpd)
    if not dpd:
        return None
    return DAMAGE_TYPE_ELEMENTS.get(dpd.get("damageType"))


def _effect_matches_damage_element(effect_elem, damage_elem):
    if core_effect_applies_to_damage_element is not None:
        return core_effect_applies_to_damage_element(effect_elem, damage_elem)
    elem = effect_elem or "all"
    if elem == "crystal":
        elem = "cryst"
    if elem == "all":
        return True
    if not damage_elem:
        return False
    if elem == "spell":
        return damage_elem in {"fire", "pulse", "cryst", "natural"}
    if elem == "physical":
        return damage_elem == "physical"
    return elem == damage_elem

# Buffs triggered by game mechanics, not attributable to any player
NEUTRAL_BUFFS = {
    "buff_common_poise_break_damage_taken_scale",
}

# Zones classified but not consumed by rDPS.
# - CRIT_RATE: kept for future crit-allocation work, not current rDPS
# - UTILITY: non-damage / non-rDPS functional stats (HP, wisdom, etc.)
NON_RDPS_ZONES = {"CRIT_RATE", "UTILITY"}

# ── Phase 18: BuffData modifier → zone mapping (from DLL ATTR_MOD/DMG_MOD) ──
# Reverse-engineered 2026-04-19 from sample buffs; extend as new attrTypes appear.
#   attrType: Beyond.Gameplay.AttributeType enum
#   formula:  5 = multiplicative zone, 6 = ATK pool
#   Unknown attrTypes are logged to unmatched.log with `unknown_attrType=N`.
# Per-buff hardcoded overrides (zone+elem only — NOT rate). Used when the
# auto-classifier puts a buff in the wrong zone/element. Rate still comes
# from BB or template so it adapts to weapon/talent upgrades.
# Format: buff_id → [(zone, elem)] — rate filled in by caller from BB value.
BUFF_ZONE_OVERRIDE = {}

# Ally rate multiplier: keep this only for buffs whose BB value does not
# already encode the teammate rate. Wolfgd talent writes 0.30 on self and
# 0.15 on teammates in current logs, so it needs no extra scaling here.
# The BB rate stays authoritative (adapts to talent level); this multiplier
# only scales the rate WHEN the attacker is NOT the buff source.
# Format: buff_id → multiplier (1.0 = no halving, 0.5 = team gets half).
# elem/zone left alone.
BUFF_ALLY_RATE_MULT = {}

# Buff-level skill-name filter. Buff's effects only apply when attacker's
# skill name matches the regex. Used for conditional effects (e.g. "only
# during normal attacks"). Checked in _handle_hit alongside element filter.
# sword_0006 "大招后普通攻击 +120%": in-game "普通攻击" covers laevat's
# normal_skill AND her ult-stance's attack combos (ult_attack1/2/3/4 are
# all 普通攻击 variants of the transformed stance).
BUFF_SKILL_FILTER = {
    "buff_wpn_sword_0006_valid": re.compile(r"normal_skill|ult_attack"),
    "buff_equipsuit_combo_cd01_spellup": re.compile(r"normal_skill|combo|ultimate"),
}

BUFF_EFFECT_SKILL_FILTER = {
    ("buff_equipsuit_attrisuit_01", "DMG_INC"): re.compile(r"normal_skill.*_1"),
    ("buff_equipsuit_attrisuitup_01", "DMG_INC"): re.compile(r"normal_skill.*_1"),
}

ATTRIBUTE_TYPE_SKILL_FILTER = {
    28: re.compile(r"ultimate"),
    32: re.compile(r"normal_skill"),
    33: re.compile(r"combo"),
}


def _buff_effect_applies_to_skill(buff_id, zone, skill):
    if core_buff_effect_applies_to_skill is not None:
        return core_buff_effect_applies_to_skill(buff_id, zone, skill)
    flt = BUFF_EFFECT_SKILL_FILTER.get((str(buff_id or ""), str(zone or "")))
    return not flt or flt.search(str(skill or ""))


def _buff_applies_to_skill(buff_id, skill):
    if core_buff_applies_to_skill is not None:
        return core_buff_applies_to_skill(buff_id, skill)
    flt = BUFF_SKILL_FILTER.get(str(buff_id or ""))
    return not flt or flt.search(str(skill or ""))


def _attribute_type_applies_to_skill(attr_type, skill):
    if core_attr_type_applies_to_skill is not None:
        return core_attr_type_applies_to_skill(attr_type, skill)
    try:
        attr_type = int(attr_type)
    except (TypeError, ValueError):
        return True
    flt = ATTRIBUTE_TYPE_SKILL_FILTER.get(attr_type)
    return not flt or flt.search(str(skill or ""))


# Game's 8 damage multiplicative zones (user-confirmed):
#   ATK         攻击力       —  atk_up, atk
#   DMG_INC     增伤         —  普通攻击伤害提高, 火属性伤害提高, 终结技伤害提高 …
#                              (all "伤害提高" types additive within this zone)
#   FRAGILE     脆弱         —  affixes_vulnerable_*, fragile, weakness_damage
#   VULN_TAKEN  易伤         —  spell_damage_taken_up, *_taken_up, *_resistance_decrease
#   AMP         增幅         —  affixes_enhance_*
#   COMBO       连击         —  combo_damageup
#   CRIT_RATE   暴击         —  crit_up (NON_RDPS — not a damage multiplier)
#   RES         抗性         —  ignore_*_resist (attacker reduces enemy elem res)
# Intra-zone: additive.  Inter-zone: multiplicative.
ATTRIBUTE_TYPE_MAP = {
    2:  ("ATK",       "all"),      # atk_up / atk
    9:  ("CRIT_RATE", "all"),      # critical_rate / crit_up2
    10: ("CRIT_RATE", "all"),      # critical_damage_inc
    # Elemental AMP (game's "增幅" zone — observed in antal's affixes_enhance_fire/pulse,
    # pattern: 65=fire, 66=pulse, 67=cryst, 68=natural). User confirmed antal
    # ult "20火增幅 + 20电增幅" = 增幅 zone.
    53: ("DMG_INC",   "cryst"),    # cryst_dmg / cryst_dmg_up2 = 增伤, not 增幅
    65: ("AMP",       "fire"),
    66: ("AMP",       "pulse"),
    67: ("AMP",       "cryst"),
    68: ("AMP",       "natural"),
    # Elemental FRAGILE (game's "脆弱" zone — ardelia 战技 applies
    # affixes_vulnerable_physical + _spell, emitting 70 / 71-74 respectively).
    70: ("FRAGILE",   "physical"),
    71: ("FRAGILE",   "fire"),
    72: ("FRAGILE",   "pulse"),
    73: ("FRAGILE",   "cryst"),
    74: ("FRAGILE",   "natural"),
    # AttributeMetaTable 80-85 = *_DamageTakenScalar by default. Corrosion's
    # `def_decrease` keys are a separate RES lane handled below.
    80: ("FRAGILE",   "physical"),
    81: ("FRAGILE",   "natural"),
    82: ("FRAGILE",   "cryst"),
    83: ("FRAGILE",   "pulse"),
    84: ("FRAGILE",   "fire"),
    85: ("FRAGILE",   "spell"),
    # Equipsuit DMG_INC variants — go to 增伤 zone.
    17: ("DMG_INC",   "all"),      # normal_atk_up (laevat sword_0006)
    28: ("DMG_INC",   "all"),      # spell_up variant (equipsuit)
    32: ("DMG_INC",   "all"),      # spell_up variant
    33: ("DMG_INC",   "all"),      # spell_up variant
    50: ("DMG_INC",   "physical"), # physical dmg_up (paired with attrType 51 fire on sword_0022)
    51: ("DMG_INC",   "fire"),     # FireDamageIncrease (wolfgd talent_0)
    # non-damage → skip
    14: None,                      # MoveSpeed
    86: None,                      # slow_rate
    92: None,                      # speedup
}

DEF_DECREASE_ATTR_TYPE_MAP = {
    80: ("RES", "physical"),
    81: ("RES", "natural"),
    82: ("RES", "cryst"),
    83: ("RES", "pulse"),
    84: ("RES", "fire"),
    85: ("RES", "spell"),
}


def _attribute_type_mapping(attr_type):
    if attr_type in ATTRIBUTE_TYPE_MAP:
        return ATTRIBUTE_TYPE_MAP[attr_type]
    hint = ATTRIBUTE_TYPE_HINTS.get(str(attr_type), {})
    cls_hint = hint.get("classificationHint") if isinstance(hint, dict) else None
    if not isinstance(cls_hint, dict):
        return None
    zone = cls_hint.get("zone")
    elem = cls_hint.get("element", "all")
    if not zone:
        return None
    return (zone, elem)

# Template-path marker buffs whose effect is already counted elsewhere.
# `affixes_vulnerable_spell` emits VULN/{fire,pulse,cryst,natural} via
# attrType 71-74 but a skill-side `normalskill_spellvulnerable` buff with
# `rate_spellvulnerable=0.05` also fires the same 5% → double-count without
# this ignore. Observed on tangtang.
TEMPLATE_IGNORE_BUFF_PATTERNS = [
    re.compile(r"^buff_common_affixes_vulnerable_spell"),
]

# Generic buffs whose `src` is unreliable (engine records the triggering
# entity, not the originator). For these, we borrow ownership from a
# `buff_chr_XXXX_*` buff applied to the same owner within a narrow window —
# the heuristic being "a character's ultimate triggers chr-specific + generic
# side-effect buffs as a burst". A lone pulse/fire reaction with no
# chr-specific partner stays unassigned (engine quirk → accept neutral).
GENERIC_BUFF_PREFIXES = (
    "buff_common_affixes_enhance_",       # antal fire/pulse enhance
    "buff_common_affixes_vulnerable_",    # antal fire/pulse vuln
    "buff_common_pulse_",                 # pulse conduct
    "buff_common_fire_",                  # fire burning etc.
    "buff_common_cryst_",
    "buff_common_natural_",
)
CHR_BORROW_WINDOW_SEC = 1.0

# Weapon ownership: buff_wpn_<weapon_id>_*  → owner chr_key.
# Most weapons can now be learned dynamically from direct src/owner evidence,
# self-attached weapon buffs, or prior observed weapon-owner pairs. Static map
# is only a last-resort fallback for traces with no usable runtime signal.
WEAPON_OWNER_MAP = {
    "funnel_0008": "chr_0023_antal",    # antal's funnel (spell_damage_taken_up 19.8%)
    "funnel_0013": "chr_0025_ardelia",  # ardelia's funnel (spell_taken_up 16%)
    "sword_0006":  "chr_0016_laevat",   # laevat's sword (夜幕·嘶鸣烈火 normal_atk +120%)
}
_RE_WEAPON_ID = re.compile(r"^buff_wpn_([a-z]+_\d+)_")
WEAPON_TEAM_REPLICATE_WINDOW_SEC = 0.05


def _weapon_id_from_buff_id(buff_id):
    m = _RE_WEAPON_ID.search(buff_id or "")
    return m.group(1) if m else None


def _positive_int(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    rounded = round(number)
    if abs(number - rounded) > 0.0001:
        return None
    return int(rounded)


def _blackboard_max_stack_from_weapon_skill(skill):
    description = str(skill.get("description") or "")
    if "叠加" not in description and "max_stack" not in description:
        return None
    values = []
    for item in skill.get("blackboard") or []:
        if not isinstance(item, dict) or item.get("key") != "max_stack":
            continue
        raw_value = item.get("value")
        raw_values = raw_value if isinstance(raw_value, list) else [raw_value]
        values.extend(v for v in (_positive_int(v) for v in raw_values) if v is not None)
    return min(values) if values else None


@lru_cache(maxsize=1)
def _load_weapon_id_stack_limits():
    limits = {}
    for items_dir in _data_path_candidates("akedata", "weapon", "items"):
        if not os.path.isdir(items_dir):
            continue
        for file_name in os.listdir(items_dir):
            if not file_name.endswith(".json"):
                continue
            payload = _load_json_file(os.path.join(items_dir, file_name))
            if not isinstance(payload, dict):
                continue
            weapon_id = str(payload.get("weaponId") or file_name[:-5])
            for skill in payload.get("skilllist") or []:
                if not isinstance(skill, dict):
                    continue
                max_stack = _blackboard_max_stack_from_weapon_skill(skill)
                if max_stack is None:
                    continue
                limits[weapon_id] = max_stack
                if weapon_id.startswith("wpn_"):
                    limits[weapon_id[4:]] = max_stack
                break
        if limits:
            break
    return limits


@lru_cache(maxsize=1)
def _load_weapon_buff_stack_limits():
    limits = {}
    for path in _data_path_candidates("local_static", "skill", "manifest.json"):
        if not os.path.isfile(path):
            continue
        for entry in _iter_json_entries(path):
            skill_id = str(entry.get("id") or "")
            if not skill_id.startswith("sk_wpn_"):
                continue
            binary_probe = entry.get("binaryProbe")
            if not isinstance(binary_probe, dict):
                continue
            max_stack = None
            for hint in binary_probe.get("blackboardDoubleHints") or []:
                if not isinstance(hint, dict) or hint.get("key") != "max_stack":
                    continue
                max_stack = _positive_int(hint.get("value"))
                if max_stack is not None:
                    break
            if max_stack is None:
                continue
            created_buff_ids = (
                binary_probe.get("highConfidenceCreatedBuffIds")
                or binary_probe.get("createdBuffIds")
                or []
            )
            if not isinstance(created_buff_ids, list):
                continue
            for buff_id in created_buff_ids:
                buff_key = str(buff_id or "")
                if buff_key.startswith("buff_wpn_"):
                    limits[buff_key] = max_stack
        if limits:
            break
    return limits


def _weapon_stack_limit_for_buff(buff_id):
    buff_id = str(buff_id or "")
    if not buff_id:
        return None
    exact_limit = _load_weapon_buff_stack_limits().get(buff_id)
    if exact_limit is not None:
        return exact_limit

    weapon_id = _weapon_id_from_buff_id(buff_id)
    if not weapon_id:
        return None
    # akedata/weapon/items exposes the stack count by weapon, while the live
    # buff id only carries the runtime effect suffix. The current known
    # fallback is the weapon's atk_up stack buff, including karin sword_0012.
    if buff_id == f"buff_wpn_{weapon_id}_atk_up" or buff_id.startswith(f"buff_wpn_{weapon_id}_atk_up_"):
        limits = _load_weapon_id_stack_limits()
        return limits.get(weapon_id) or limits.get(f"wpn_{weapon_id}")
    return None


@lru_cache(maxsize=1)
def _load_nonstacking_weapon_ids():
    markers = ("无法叠加", "不能叠加", "不可叠加")
    weapon_ids = set()
    for items_dir in _data_path_candidates("akedata", "weapon", "items"):
        if not os.path.isdir(items_dir):
            continue
        for file_name in os.listdir(items_dir):
            if not file_name.endswith(".json"):
                continue
            payload = _load_json_file(os.path.join(items_dir, file_name))
            if not isinstance(payload, dict):
                continue
            weapon_id = str(payload.get("weaponId") or file_name[:-5])
            text_parts = []
            for skill in payload.get("skilllist") or []:
                if isinstance(skill, dict):
                    text_parts.append(str(skill.get("description") or ""))
            text_parts.append(str(payload.get("description") or ""))
            if any(marker in "\n".join(text_parts) for marker in markers):
                weapon_ids.add(weapon_id)
        if weapon_ids:
            break
    return weapon_ids


def _is_nonstacking_refresh_buff(buff_id):
    weapon_id = _weapon_id_from_buff_id(buff_id)
    return bool(weapon_id and f"wpn_{weapon_id}" in _load_nonstacking_weapon_ids())


FOOD_POTION_BUFF_RE = re.compile(r"^buff_common_.*_potion(?:_\d+)?$", re.IGNORECASE)
SINGLETON_REFRESH_BUFF_PATTERNS = [
    re.compile(r"^buff_equipsuit_", re.IGNORECASE),
    re.compile(r"^buff_chr_\d{4}_[a-z0-9]+_potential_\d+(?:_|$)", re.IGNORECASE),
]
STACKABLE_BUFF_NAME_RE = re.compile(r"(?:^|_)(?:layer|stack)(?:_|$)", re.IGNORECASE)


def _is_singleton_refresh_buff(buff_id):
    buff_id = str(buff_id or "")
    if not buff_id:
        return False
    if FOOD_POTION_BUFF_RE.match(buff_id):
        return True
    if _weapon_stack_limit_for_buff(buff_id) is not None:
        return False
    if STACKABLE_BUFF_NAME_RE.search(buff_id):
        return False
    if _is_nonstacking_refresh_buff(buff_id):
        return True
    return any(pattern.search(buff_id) for pattern in SINGLETON_REFRESH_BUFF_PATTERNS)

# DamageProcessor zoneName + side → (zone, element)
#   side 0 = attacker-side (outgoing) → DMG_INC zone (增伤)
#   side 1 = defender-side (incoming). Defender's zone depends on bbKey —
#            bbKey="spell_taken_up" / "*_taken_up" → VULN_TAKEN (易伤);
#            unknown bbKey defaults to VULN_TAKEN since "side 1" in game
#            calc generally means "enemy takes more damage" zone.
# ProdCalcZone = separate multiplicative zone; side=1 likely AMP (增幅)
# since it's the defender's "amplification" layer stacked multiplicatively
# with NormalCalcZone's defender side (VULN_TAKEN).
ZONE_SIDE_MAP = {
    ("NormalCalcZone", 0): ("DMG_INC",    "all"),
    ("NormalCalcZone", 1): ("VULN_TAKEN", "all"),
    ("ComboCalcZone",  0): ("COMBO",      "all"),
    ("ComboCalcZone",  1): ("COMBO",      "all"),
    ("ProdCalcZone",   0): ("DMG_INC",    "all"),
    ("ProdCalcZone",   1): ("AMP",        "all"),
}

# ── BB key → zone mapping (classify by BB key, not buff name) ──
BB_ZONE_MAP = {
    "atk":                    ("ATK",        "all"),  # karin ult gives atk=0.1 to party
    "atk_up":                 ("ATK",        "all"),
    "dmg_up":                 ("DMG_INC",    "all"),
    "spell_up":               ("DMG_INC",    "spell"),
    # Non-damage attribute buffs. We classify them explicitly so the broad
    # fallback rules can't accidentally turn "*_up" into DMG_INC.
    "hp_up":                  ("UTILITY",    "all"),
    "wisd_up":                ("UTILITY",    "all"),
    # "taken_up" family → 易伤 (VULN_TAKEN) zone
    "spell_damage_taken_up":  ("VULN_TAKEN", "spell"),
    "spell_taken_up":         ("VULN_TAKEN", "spell"),
    # "ignore_*_resist" → 抗性 zone (RES) - attacker reduces enemy elem res
    "ignore_fire_resist":     ("RES",        "fire"),
    "ignore_pulse_resist":    ("RES",        "pulse"),
    "ignore_cryst_resist":    ("RES",        "cryst"),
    "ignore_natural_resist":  ("RES",        "natural"),
    "ignore_physical_resist": ("RES",        "physical"),
    "ignore_spell_resist":    ("RES",        "spell"),
    "fire_res_down":          ("RES",        "fire"),
    "pulse_res_down":         ("RES",        "pulse"),
    "cryst_res_down":         ("RES",        "cryst"),
    "natural_res_down":       ("RES",        "natural"),
    "physical_res_down":      ("RES",        "physical"),
    "spell_res_down":         ("RES",        "spell"),
    "crit_up":                ("CRIT_RATE",  "all"),
    "crit_up2":               ("CRIT_RATE",  "all"),
}

# BB key pattern rules (checked when key not in BB_ZONE_MAP).
# Order matters: specific patterns first, generic "_up$" catch-all last.
BB_KEY_PATTERNS = [
    (re.compile(r"^(fire|pulse|cryst|natural|physical|spell)_dmg_up2?$"), "DMG_INC"),
    (re.compile(r"^rate_(.*)vulnerable$"), "FRAGILE"),
    (re.compile(r"^normal_(.+)_up_valid$"), "DMG_INC"),
    # res_down / ignore_resist are RES; resistance_decrease is incoming damage taken.
    (re.compile(r"^(fire|pulse|cryst|natural|physical|spell)_res_down$"), "RES"),
    (re.compile(r"^(fire|pulse|cryst|natural|physical|spell)_resistance_decrease$"), "VULN_TAKEN"),
    # "*_taken_up" → 易伤乘区 (VULN_TAKEN)
    (re.compile(r"^(.+)_taken_up$"), "VULN_TAKEN"),
    (re.compile(r"^(?:.+_)?taken_up_(fire|pulse|cryst|natural|physical|physic|spell)$"), "VULN_TAKEN"),
    (re.compile(r"^damage_taken_up_(fire|pulse|cryst|natural|physical|physic|spell)$"), "VULN_TAKEN"),
    # 无视抗性 → 抗性乘区
    (re.compile(r"^ignore_(.+)_resist$"), "RES"),
    (re.compile(r"^(.+)_up$"), "DMG_INC"),
]

ELEM_KEYWORDS = {"fire", "pulse", "cryst", "crystal", "natural", "physical", "physic", "spell"}

def _match_bb_key(key):
    """Try pattern-matching a BB key → (zone, element) or None."""
    for pat, zone in BB_KEY_PATTERNS:
        m = pat.match(key)
        if m:
            prefix = m.group(1).lower()
            elem = "all"
            for e in ELEM_KEYWORDS:
                if e in prefix:
                    elem = "physical" if e == "physic" else ("cryst" if e == "crystal" else e)
                    break
            return (zone, elem)
    return None


def _classify_effect_from_bb_key(key):
    """Return (zone, elem) from a concrete BB key, or None.

    This is the shared source of truth for both fallback BB parsing and the
    template path. Some DamageProcessor templates only tell us
    `NormalCalcZone/side=1`, which is too generic; the concrete `bbKey`
    (`spell_taken_up`, `physical_res_down`, ...) decides the real zone."""
    if not key:
        return None
    if key.startswith("final_"):
        key = key[6:]
    direct = BB_ZONE_MAP.get(key)
    if direct:
        return direct
    return _match_bb_key(key)


def _buff_hint_effects(buff_id, bb_pairs):
    hint = _buff_hint_meta(buff_id)
    if not isinstance(hint, dict):
        return None

    effect_hints = _buff_hint_effect_candidates(buff_id)
    bb = dict(bb_pairs)
    rate = bb.get("rate")

    if rate is None or rate <= 0 or rate >= 10:
        return None

    effects = []
    for eff in effect_hints:
        if not isinstance(eff, dict):
            continue
        if eff.get("confidence") not in {"high", "medium"}:
            continue
        zone = eff.get("zone")
        elem = eff.get("element", "all")
        if not zone or zone in NON_RDPS_ZONES:
            continue
        effects.append((zone, elem, rate))

    if effects:
        seen = set()
        deduped = []
        for e in effects:
            key = (e[0], e[1], round(e[2], 4))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(e)
        return deduped

    return None

# For BB key "rate", fall back to buff name to determine zone
NAME_RATE_RULES = [
    # ── 脆弱乘区 (FRAGILE) — "vulnerable" = 脆弱 ──
    # Element-specific before generic. Matches ardelia's affixes_vulnerable_*
    # _child variants that fall through to NAME_RATE path (no template).
    (re.compile(r"vulnerable_physic"), "FRAGILE", "physical"),
    (re.compile(r"vulnerable_spell"),  "FRAGILE", "spell"),
    (re.compile(r"vulnerable_fire"),   "FRAGILE", "fire"),
    (re.compile(r"vulnerable_pulse"),  "FRAGILE", "pulse"),
    (re.compile(r"vulnerable_crystal"),"FRAGILE", "cryst"),
    (re.compile(r"vulnerable_cryst"),  "FRAGILE", "cryst"),
    (re.compile(r"vulnerable_natural"),"FRAGILE", "natural"),
    (re.compile(r"vulnerable"),        "FRAGILE", "all"),
    (re.compile(r"fragile|weakness_damage|def_down_damage"), "FRAGILE", "all"),
    # ── 连击乘区 (COMBO) ──
    (re.compile(r"combo_damageup"), "COMBO", "all"),
    # ── 增幅乘区 (AMP) — "enhance" = 增幅 ──
    (re.compile(r"enhance_physical"), "AMP", "physical"),
    (re.compile(r"enhance_fire"),     "AMP", "fire"),
    (re.compile(r"enhance_pulse"),    "AMP", "pulse"),
    (re.compile(r"enhance_crystal"),  "AMP", "cryst"),
    (re.compile(r"enhance_cryst"),    "AMP", "cryst"),
    (re.compile(r"enhance_natural"),  "AMP", "natural"),
    (re.compile(r"enhance_spell"),    "AMP", "spell"),
    # ── 增伤乘区 (DMG_INC) — 所有"伤害提升" ──
    (re.compile(r"dmgup|damage_increase"), "DMG_INC", "all"),
]

# ── Unmatched BB-key observer (writes to %TEMP%/endfield_unmatched_bb.log) ──
# Mechanism/VFX keys that are not buff effects; excluded to reduce noise.
BB_IGNORE_KEYS = {
    "duration", "poise", "posie", "count", "atk_scale", "damage_interval",
    "vfx_buff_name", "dodgeSkillId", "atb", "hpper",
    "atk_duration", "cd", "comboskill_cooldown", "heal_value",
    "healvalue", "hp_up", "infliction_num", "phy_spell_up",
    "potential_1", "potential_3", "potential_3_atb", "rate_add",
    "shatter_dmg", "skill_bg_type", "stack_cond",
    "common_character_perfect_dodge", "buff_common_dash_perfect_vfx_common",
    # mechanism params observed 2026-04-19 (wulfa / tangtang / laevat):
    "time_warning", "maxcount", "max_stack", "cntmax",
    "poise_value", "water_num", "water_stack",
    "burning_atk_scale", "def_decrease_tick", "def_decrease_tick_final",
    "max_def_decrease", "max_def_decrease_final",
    "triggerheal", "End_Early", "speed", "trigger",
    "hp_threshold", "heal_max_hp", "shelter", "distance",
    "hit_spellduration", "usp_self", "usp_everyone",
    "imbue_scale",
}

# Ignored key prefixes (any key starting with these is a config param, not an effect).
BB_IGNORE_PREFIXES = (
    "duration_",           # duration_waterdebuff, duration_spellvulnerable, ...
    "usp_",                # usp_stage_*, usp_1, usp_2, ultimate_sp related
    "normalskill_atk_scale",  # tangtang passive scale table
    "ignore_fire_resist_duration",  # duration of VULN, not the effect
)
_UNMATCHED_SEEN = set()
_UNMATCHED_LOG_PATH = os.path.join(os.environ.get("TEMP", ""), "endfield_unmatched_bb.log")
_unmatched_fp = None

def _log_unmatched(buff_id, key, val, reason):
    global _unmatched_fp
    if key in BB_IGNORE_KEYS:
        return
    for p in BB_IGNORE_PREFIXES:
        if key.startswith(p):
            return
    sig = (buff_id, key, reason)
    if sig in _UNMATCHED_SEEN:
        return
    _UNMATCHED_SEEN.add(sig)
    try:
        if _unmatched_fp is None:
            _unmatched_fp = open(_UNMATCHED_LOG_PATH, "a", encoding="utf-8")
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        _unmatched_fp.write(f"[{ts}] {reason:<18} buff={buff_id} key={key} val={val}\n")
        _unmatched_fp.flush()
    except Exception:
        pass


RATE_IGNORE_BUFF_PATTERNS = [
    re.compile(r"^buff_common_affixes_slow"),  # movement slow, not a dmg rate
    re.compile(r"^buff_chr_0023_antal_(?:normal_skill|tageffect|utimate_skill)$"),
    re.compile(r"^buff_common_affixes_shelter_default_child$"),
    re.compile(
        r"^buff_common_(?:physical|fire|pulse|cryst|natural|spell)_"
        r"(?:physical|fire|pulse|cryst|natural|spell)_corrupt_triggered$"
    ),
    # 'affixes' here is the engine's generic status-buff namespace, not a dungeon
    # modifier. The real effect is on the skill-side buff (e.g. normalskill_*),
    # this one is a marker that would double-count:
    re.compile(r"^buff_common_affixes_vulnerable_spell"),  # + _default_child variant
]

EFFECT_IGNORE_BUFF_PATTERNS = [
    # Listener/config shells. Their BB carries the configured value, but the
    # actual damage modifier is emitted by the created child buff.
    re.compile(r"^buff_equipsuit_(?:usp_02|combo_cd01)$"),
    # Seraph ultimate wrapper. Its `atk_up` BB is an internal base used to
    # spawn elemental enhance children, not an attack-percent buff.
    re.compile(r"^buff_chr_0011_seraph_atk_buff$"),
    # Seraph heal-side potential wrapper passes atk_up into the spawned action;
    # the real attack buff is emitted separately as wpn_funnel_0010_atk_up.
    re.compile(r"^buff_chr_0011_seraph_potential_1_atkup$"),
    re.compile(r"^buff_chr_0011_seraph_mainchr_heal$"),
]

# Dynamic BB effects: some buffs expose a *current value* that ramps during the
# buff lifetime instead of a fixed modifier at apply time. We only see the
# start snapshot and the final snapshot on BUFF_END, so in-flight values are
# estimated from the observed internal tick cadence (~3.5 updates / sec).
DEF_DECREASE_BASE_KEYS = {"def_decrease", "additional_def_decrease"}
DEF_DECREASE_TICKS_PER_SEC = 3.5


# Phase 18: buff_id → [modifier specs] extracted from ATTR_MOD/DMG_MOD lines.
# Populated by LogTailer as log is tailed.
# Structure: BUFF_TEMPLATES[buff_id] = {
#     'attrs': [(i, attrType, formula, useKey, val, bbKey), ...],
#     'dmgs':  [(d, p, className, side, zoneName, useKey, val, bbKey), ...],
# }
BUFF_TEMPLATES: dict = {}


def _template_effects(buff_id, bb_pairs):
    """Classify via BuffData modifier template (Phase 18). Returns effects list
    or None if no template or template fully unclassified."""
    if any(p.search(buff_id) for p in EFFECT_IGNORE_BUFF_PATTERNS):
        return []
    tmpl = BUFF_TEMPLATES.get(buff_id)
    if not tmpl:
        return None
    if any(p.search(buff_id) for p in RATE_IGNORE_BUFF_PATTERNS):
        return []
    # Template-level ignore (partnered marker buffs → skip, don't fallback)
    for pat in TEMPLATE_IGNORE_BUFF_PATTERNS:
        if pat.match(buff_id):
            return []
    bb = dict(bb_pairs)

    def _resolve(val_default, useKey, bbKey):
        if useKey and bbKey:
            if bbKey in bb and bb[bbKey] != 0:
                return bb[bbKey]
            # Game quirk: template often references `final_<something>` which
            # is 0 at buff-apply (computed later), while the real value lives
            # under the un-prefixed key. Observed in pulse_conduct:
            # `final_spell_resistance_decrease=0` but `spell_resistance_decrease=0.2`.
            if bbKey.startswith("final_"):
                base = bbKey[6:]
                if base in bb and bb[base] != 0:
                    return bb[base]
        return val_default

    effects = []
    # Whether we *understood* at least one template entry. Unknown attrTypes /
    # unknown zones should not suppress fallback BB/name rules, otherwise every
    # newly-seen enum becomes a silent drop instead of a recoverable fallback.
    saw_understood = False
    for (_i, attrTy, form, useKey, val, bbKey) in tmpl.get('attrs', []):
        if attrTy in ATTRIBUTE_TYPE_MAP or str(attrTy) in ATTRIBUTE_TYPE_HINTS:
            saw_understood = True
        if bbKey in DEF_DECREASE_BASE_KEYS:
            mapping = DEF_DECREASE_ATTR_TYPE_MAP.get(attrTy)
        else:
            mapping = _attribute_type_mapping(attrTy)
        if mapping is None:
            if attrTy not in ATTRIBUTE_TYPE_MAP and str(attrTy) not in ATTRIBUTE_TYPE_HINTS:
                _log_unmatched(buff_id, f"attrType={attrTy}", val, "unknown_attrType")
            # mapping=None explicitly means "known non-rDPS" — skip silently
            continue
        zone, elem = mapping
        key_mapping = _classify_effect_from_bb_key((bbKey or "").lower())
        if bbKey in DEF_DECREASE_BASE_KEYS:
            key_mapping = None
        if key_mapping is not None and key_mapping[0] == "DMG_INC" and (elem == "all" or key_mapping[1] != "all"):
            zone, elem = key_mapping
        if zone in NON_RDPS_ZONES:
            continue
        rate = _resolve(val, useKey, bbKey)
        if rate <= 0 or rate >= 10:
            continue
        effects.append((zone, elem, rate))
    for (_d, _p, cls, side, zone_name, useKey, val, bbKey) in tmpl.get('dmgs', []):
        mapping = ZONE_SIDE_MAP.get((zone_name, side))
        if mapping is None:
            if zone_name:
                _log_unmatched(buff_id, f"zone={zone_name}/side={side}", val, "unknown_zone")
            continue
        saw_understood = True
        zone, elem = mapping
        # Concrete bbKey beats generic zone+side defaults. Example:
        # `NormalCalcZone/side=1` is only "defender-side incoming scaler"; the
        # real zone can be VULN_TAKEN (`spell_taken_up`) or RES
        # (`physical_res_down`) depending on bbKey.
        key_mapping = None
        if bbKey:
            bk = bbKey.lower()
            if side == 1 and bk in {"fire_up", "pulse_up", "cryst_up", "natural_up", "physical_up"}:
                elem = bk[:-3]
                key_mapping = ("VULN_TAKEN", elem)
            else:
                key_mapping = _classify_effect_from_bb_key(bk)
        if key_mapping is not None:
            zone, elem = key_mapping
        elif elem == "all" and bbKey:
            # Fallback: at least refine the element when bbKey mentions one.
            bk = bbKey.lower()
            for el in ("physical", "spell", "fire", "pulse", "cryst", "natural"):
                if el in bk:
                    elem = el
                    break
        if zone in NON_RDPS_ZONES:
            continue
        rate = _resolve(val, useKey, bbKey)
        if rate <= 0 or rate >= 10:
            continue
        effects.append((zone, elem, rate))
    # Dedupe: same buff template often has multiple modifier/processor entries
    # that produce identical (zone, elem, rate) — e.g. pulse_conduct emits
    # 4 identical DamageScaleProcessor copies. Keep one.
    if effects:
        seen = set()
        deduped = []
        for e in effects:
            key = (e[0], e[1], round(e[2], 4))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(e)
        effects = deduped
    # If we understood at least one template entry, trust the template path
    # (possibly yielding an intentionally empty list for non-rDPS buffs).
    # If every template entry was unknown to us, allow fallback BB/name rules
    # so newly-seen enums can still be classified heuristically.
    return effects if saw_understood else None


def _compute_attrType_rates(buff_id, bb_pairs):
    """Per-buff {attrType: resolved_rate} for template-path buffs. Used by
    baseline injection in _handle_hit to subtract only the captured portion
    that actually feeds a given attrType (not every DMG_INC across zone)."""
    if any(p.search(buff_id) for p in EFFECT_IGNORE_BUFF_PATTERNS):
        return {}
    tmpl = BUFF_TEMPLATES.get(buff_id)
    if not tmpl:
        return {}
    result = {}
    bb = dict(bb_pairs)
    for (_i, attrType, _form, useKey, val, bbKey) in tmpl.get('attrs', []):
        rate = val
        if useKey and bbKey:
            if bbKey in bb and bb[bbKey] != 0:
                rate = bb[bbKey]
            elif bbKey.startswith("final_"):
                base = bbKey[6:]
                if base in bb and bb[base] != 0:
                    rate = bb[base]
        if rate > 0 and rate < 10:
            result[attrType] = result.get(attrType, 0.0) + rate
    return result


def _extract_dynamic_effect_specs(buff_id, bb_pairs):
    """Return dynamic effect specs for buffs whose rate ramps over time.

    Current use: ramping `def_decrease` style buffs write into attrTypes
    80/82/83/84, so we keep the buff alive even when apply-time current value
    is 0 and estimate the live value at hit time. Marker corrosion wrappers
    proven absent from DPD buckets are filtered before this point."""
    if any(p.search(buff_id) for p in EFFECT_IGNORE_BUFF_PATTERNS):
        return []
    if any(p.search(buff_id) for p in RATE_IGNORE_BUFF_PATTERNS):
        return []

    tmpl = BUFF_TEMPLATES.get(buff_id)
    if not tmpl:
        return []

    bb = dict(bb_pairs)
    tick_val = bb.get("def_decrease_tick_final", 0.0) or bb.get("def_decrease_tick", 0.0)
    max_val = max(0.0, bb.get("max_def_decrease", 0.0))
    dyn = {}

    for (_i, attrType, _form, _useKey, _val, bbKey) in tmpl.get('attrs', []):
        if bbKey not in DEF_DECREASE_BASE_KEYS:
            continue
        if bbKey in DEF_DECREASE_BASE_KEYS:
            mapping = DEF_DECREASE_ATTR_TYPE_MAP.get(attrType)
        else:
            mapping = _attribute_type_mapping(attrType)
        if mapping is None:
            continue
        zone, elem = mapping
        if zone in NON_RDPS_ZONES:
            continue
        spec = dyn.setdefault((zone, elem), {
            "kind": "def_decrease",
            "zone": zone,
            "elem": elem,
            "base_rate": 0.0,
            "tick_rate": max(0.0, tick_val) * DEF_DECREASE_TICKS_PER_SEC,
            "max_rate": max_val,
        })
        cur = bb.get(bbKey, 0.0)
        if cur > 0:
            spec["base_rate"] += cur

    return list(dyn.values())


def classify_bb_effects(buff_id, bb_pairs):
    """Return list of (zone, element, rate) from BB key-value pairs.
    Zones in NON_RDPS_ZONES are recognized but dropped so rDPS can't consume
    them as if they were DMG_INC multipliers.

    Phase 18 ordering: BuffData modifier template (zone from attrType, rate
    from BB) → fall back to BB-key pattern matching when no template is known.
    Zone override in BUFF_ZONE_OVERRIDE is applied AFTER classification to
    relocate misclassified buffs without touching their rate."""
    if any(p.search(buff_id) for p in EFFECT_IGNORE_BUFF_PATTERNS):
        return []
    tmpl_effects = _template_effects(buff_id, bb_pairs)
    if tmpl_effects is not None:
        # Apply zone+elem override (rate unchanged → adapts to weapon upgrades)
        if buff_id in BUFF_ZONE_OVERRIDE:
            override = BUFF_ZONE_OVERRIDE[buff_id]
            if len(override) == len(tmpl_effects):
                tmpl_effects = [(override[i][0], override[i][1], e[2])
                                for i, e in enumerate(tmpl_effects)]
        return tmpl_effects

    hinted_effects = _buff_hint_effects(buff_id, bb_pairs)
    if hinted_effects is not None:
        return hinted_effects

    if _buff_hint_is_non_effect(buff_id, bb_pairs):
        return []

    effects = []
    for key, val in bb_pairs:
        if val <= 0 or val >= 10:
            _log_unmatched(buff_id, key, val, "val_out_of_range")
            continue
        key_mapping = _classify_effect_from_bb_key(key)
        if key_mapping is not None:
            zone, elem = key_mapping
            if zone in NON_RDPS_ZONES:
                continue
            effects.append((zone, elem, val))
        elif key == "rate":
            if any(p.search(buff_id) for p in RATE_IGNORE_BUFF_PATTERNS):
                continue
            matched = False
            for pat, zone, elem in NAME_RATE_RULES:
                if pat.search(buff_id):
                    effects.append((zone, elem, val))
                    matched = True
                    break
            if not matched:
                _log_unmatched(buff_id, key, val, "rate_no_name_rule")
        else:
            _log_unmatched(buff_id, key, val, "key_unmatched")
    return effects


def friendly_name(raw):
    if not raw or raw == "?":
        return raw
    parts = raw.split("_")
    short = parts[-1] if parts else raw
    char_key = extract_char_key(raw)
    names, names_by_short, _elements_by_short = _get_character_catalog_maps()
    label = names.get(char_key or "") or names_by_short.get(short.lower()) or CHAR_LABELS.get(short.lower())
    if label:
        return label
    return short


def crit_text(n, c):
    total = n + c
    if total <= 0:
        return "-"
    return f"{c/total:.0%}"

RE_ATTACK_N = re.compile(r'attack(\d+)')

def classify_skill(skill_raw):
    s = skill_raw.lower()
    if "combo" in s:
        return "连携技"
    if "ultimate" in s:
        return "终结技"
    if "normal_skill" in s:
        return "战技"
    m = RE_ATTACK_N.search(s)
    if m:
        return f"普攻{m.group(1)}"
    return None


class CharStats:
    __slots__ = ("name", "dps", "total_dmg", "max_hit", "hits", "elapsed", "idle",
                 "last_update", "first_hit_t", "last_hit_t",
                 "crit_n", "crit_c")
    def __init__(self, name):
        self.name = name
        self.dps = 0.0
        self.total_dmg = 0.0
        self.max_hit = 0
        self.hits = 0
        self.elapsed = 0.0
        self.idle = 0.0
        self.last_update = time.time()
        self.first_hit_t = 0.0
        self.last_hit_t = 0.0
        self.crit_n = 0
        self.crit_c = 0


class CharRdpsStats:
    __slots__ = ("name", "rdps", "total_rd", "max_rd", "first_t", "last_t")
    def __init__(self, name):
        self.name = name
        self.rdps = 0.0
        self.total_rd = 0.0  # total rDPS damage (base + buff credit - buff given away)
        self.max_rd = 0.0
        self.first_t = 0.0
        self.last_t = 0.0


# Elemental proc buffs whose `src` is sometimes recorded as the triggered
# enemy instead of the originating player. Re-attribute to the first roster
# player matching the element.
ELEMENT_PROC_PATTERNS = [
    (re.compile(r"^buff_common_pulse_"), "pulse"),
    (re.compile(r"^buff_common_fire_"), "fire"),
    (re.compile(r"^buff_common_cryst_"), "cryst"),
    (re.compile(r"^buff_common_natural_"), "natural"),
]


# Karin combo buff — BB has no rate, values live in skill formula.
# Known: 全队下次战技+30% / 终结技+20%, 触发后清空, 独立乘区.
# Effect applies to the *entire skill cast* (all segments/projhits), not just
# the first hit. Consume happens on first trigger; window covers following hits
# of the same attacker + same kind.
KARIN_COMBO_BUFF_RE = re.compile(
    r"^buff_chr_0019_karin_(?:talent_2_combo|potential_5_combo)$"
)
KARIN_KEY = "chr_0019_karin"
KARIN_COMBO_TRIGGER_BUFF_ID = "buff_common_affixes_combo_trigger"
KARIN_COMBO_IMBUE_BUFF_IDS = {
    "buff_common_affixes_skillimbue",
    "buff_common_affixes_skillimbue_atk",
}
COMBO_RATE_NORMAL_SKILL = 0.30
COMBO_RATE_ULTIMATE = 0.20
COMBO_WINDOW_SEC = 5.0  # a single skill cast rarely spans more than this
IDLE_RESET_SEC = 30.0   # no hit from anyone for this long → next hit = new battle


# Buff IDs matching these indicators are "child" effect buffs (not containers).
# Containers are typically the bundle UI/timer buff with no effect word.
CHILD_BUFF_INDICATORS = re.compile(
    r"(enhance_|vulnerable|_taken_up|_dmg_up|ignore_\w+_resist)")
CONTAINER_WINDOW_SEC = 0.5


def _parent_buff_id_of_default_child(buff_id):
    suffix = "_default_child"
    text = str(buff_id or "")
    if not text.endswith(suffix):
        return None
    return text[:-len(suffix)]


def _effect_elements_overlap(parent_elem, child_elem):
    parent_elem = (parent_elem or "all").lower()
    child_elem = (child_elem or "all").lower()
    if parent_elem == child_elem or child_elem == "all":
        return True
    if child_elem == "spell":
        return parent_elem in {"fire", "pulse", "cryst", "natural"} or parent_elem == "spell"
    return False


def _effect_sets_overlap(parent_effects, child_effects):
    for child_zone, child_elem, child_rate in child_effects or []:
        for parent_zone, parent_elem, parent_rate in parent_effects or []:
            if parent_zone != child_zone:
                continue
            if round(float(parent_rate), 4) != round(float(child_rate), 4):
                continue
            if _effect_elements_overlap(parent_elem, child_elem):
                return True
    return False


RE_HIT = re.compile(
    r'HP_V\d+\s+#(\d+)\s+hit=(\d+)\s+cum=\d+\s+raw=([\d.]+)\s+.*?'
    r'skill="([^"]*)".*?'
    r'src=(\S+)\s+tgt=(\S+)\s+atk=(\S+)'
    r'(?:\s+seg=\S+)?'
    r'(?:\s+shared=(-?\d+))?'
    r'(?:\s+critFlag=(-?\d+))?'
    r'(?:\s+critDmg=([-\d.eE+]+))?'
)
RE_DPD_RAW = re.compile(
    r'DPD_RAW\s+#(\d+)\s+probe=\d+\s+calc=([-\d.eE+]+)\s+'
    r'atkScale=([-\d.eE+]+)\s+blocked=(\d+)\s+'
    r'damageType=(0x[0-9A-Fa-f]+)\s+decorateMask=(0x[0-9A-Fa-f]+)\s+'
    r'collider="([^"]*)"\s+atkZones=\[([^\]]*)\]\s+defZones=\[([^\]]*)\]'
)
RE_TS = re.compile(r"\[(\d+):(\d+):(\d+)\.(\d+)\]")


DPD_ZONE_BUCKETS = {
    "DMG_INC": ("atk", 1),
    "AMP": ("atk", 3),
    "COMBO": ("atk", 4),
    "VULN_TAKEN": ("def", 1),
    "FRAGILE": ("def", 5),
}


def _parse_zone_values(blob):
    vals = []
    for part in (blob or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            vals.append(float(part))
        except ValueError:
            vals.append(1.0)
    return vals

# Phase 20 E-plan v2: BASELINE line emitted by DLL after each hit, carries
# attacker's Attributes.GetValue(attrType) for each rDPS-relevant attrType.
# Value includes gear/talent/active buffs all additive within that attrType.
# Overlay subtracts detected buff rates → remainder = attacker's gear/talent
# baseline, injected as a self-buff so the additive zone math stays correct.
RE_BASELINE = re.compile(r'BASELINE\s+#(\d+)\s+(.*)')
RE_BASELINE_KV = re.compile(r'(-?\d+)=([\d.eE+-]+)')


def _baseline_snapshot_values_and_time(snapshot):
    if not snapshot:
        return {}, None
    if isinstance(snapshot, dict) and isinstance(snapshot.get("values"), dict):
        return snapshot.get("values") or {}, snapshot.get("trace_t")
    if isinstance(snapshot, dict):
        return snapshot, None
    return {}, None

RE_BUFF_START = re.compile(
    r'BUFF_START\s+#\d+\s+id="([^"]*)".*?uid=(\d+).*?owner=(\S+).*?src=(\S+)'
    r'(?:.*?\sdur=([\d.eE+-]+))?'
)
RE_BUFF_END = re.compile(r'BUFF_END\s+#\d+\s+id="[^"]*".*?uid=(\d+)')
RE_BB = re.compile(r'BB\[(\d+)\]:\s+(.*)')
RE_BB_KV = re.compile(r'(\w+)=([\d.eE+-]+)')
# DLL-emitted party snapshot (from SquadManager.Tick hook in dxg.c)
RE_SQUAD = re.compile(r'SQUAD\s+size=\d+\s+members=\[([^\]]*)\]')
RE_GAME_TIMER_START = re.compile(r'\bGAME_TIMER_START\b')
RE_GAME_TIMER_END = re.compile(r'\bGAME_TIMER_END\b.*?\belapsedMs=(\d+)')
RE_OFFICIAL_TIMER_START = re.compile(r'\bOFFICIAL_TIMER_START\b')
RE_OFFICIAL_TIMER_END = re.compile(r'\bOFFICIAL_TIMER_END\b.*?\bpassTime=(\d+)')
RE_GAME_TIMER_RESET = re.compile(r'\bGAME_TIMER_RESET\b')
RE_TIMER_OFFICIAL = re.compile(r'\bofficial=(\d+)')
RE_TIMER_SOURCE = re.compile(r'\bsource=(\S+)')


def _timer_line_is_authoritative(line):
    m = RE_TIMER_OFFICIAL.search(line or "")
    if m:
        try:
            return int(m.group(1)) != 0
        except ValueError:
            return False
    src = RE_TIMER_SOURCE.search(line or "")
    return (src.group(1) if src else "") != "BattleOpModifyBattleState"


def _timer_line_is_window_boundary(line):
    src = RE_TIMER_SOURCE.search(line or "")
    return _timer_line_is_authoritative(line) or (src.group(1) if src else "") == "BattleOpModifyBattleState"

# Hit skill names that explicitly belong to non-player entities. For these we
# must NOT fall back to `atk=` / entity-owner parsing even if the attacker
# field looks player-shaped, because bosses can report player-like strings.
# Observed: rodin/endminm enemy skills, plus `buff_eny_*` container hits.
NON_PLAYER_SKILL_PREFIXES = ("eny_", "buff_eny_")

# Phase 18: ATTR_MOD / DMG_MOD modifier-signature lines emitted by DLL
RE_ATTR_MOD = re.compile(
    r'ATTR_MOD buff="([^"]+)" i=(\d+) attrType=(-?\d+) modType=-?\d+ '
    r'formula=(-?\d+) useKey=(\d+) val=([\d.eE+-]+) bbKey="([^"]*)"')
RE_DMG_MOD = re.compile(
    r'DMG_MOD buff="([^"]+)" d=(\d+) p=(\d+) enableSide=-?\d+ '
    r'class="([^"]+)" side=(-?\d+) zone="([^"]*)" useKey=(\d+) '
    r'val=([\d.eE+-]+) bbKey="([^"]*)"')
RE_IDLE_TICK = re.compile(r'^\[[^\]]+\]\s+tick\s+\d+\b')


def _trace_time_from_match(ts_match, last_clock=None, day_offset=0.0):
    if not ts_match:
        return None, last_clock, day_offset
    clock = (
        int(ts_match.group(1)) * 3600
        + int(ts_match.group(2)) * 60
        + int(ts_match.group(3))
        + int(ts_match.group(4)) / 1000.0
    )
    if last_clock is not None and clock + 1.0 < last_clock:
        day_offset += 86400.0
    return day_offset + clock, clock, day_offset


def _trace_ts_text(line):
    m = RE_TS.search(line)
    if not m:
        return "unknown"
    return (
        f"{int(m.group(1)):02d}-"
        f"{int(m.group(2)):02d}-"
        f"{int(m.group(3)):02d}-"
        f"{m.group(4)[:3]}"
    )


def _has_player_squad(members_blob):
    for member in members_blob.split():
        ck = extract_char_key(member)
        if ck:
            return True
    return False


def read_latest_battle_log_slice(trace_path=None, *, trace_bytes=None):
    """Return the latest battle's raw trace lines using the overlay's idle-reset
    boundary. Includes pre-hit lines after the idle gap and trims long post-hit
    idle tail (> IDLE_RESET_SEC) when present."""
    trace_path = trace_path or TRACE_FILE
    if trace_bytes is not None:
        lines = trace_bytes.decode("utf-8", errors="replace").splitlines(keepends=True)
    else:
        if not os.path.exists(trace_path):
            return [], None
        try:
            with open(trace_path, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError:
            return [], None
    if not lines:
        return [], None

    line_times = []
    last_clock = None
    day_offset = 0.0
    last_valid_squad_idx = None
    battle_start_idx = None
    first_hit_idx = None
    last_hit_idx = None
    last_hit_t = None
    prev_hit_idx = None
    timer_end_idx = None
    timer_end_t = None
    timer_elapsed_s = None

    for idx, line in enumerate(lines):
        ts_match = RE_TS.search(line)
        t_abs, last_clock, day_offset = _trace_time_from_match(
            ts_match, last_clock, day_offset
        )
        line_times.append(t_abs)

        squad_m = RE_SQUAD.search(line)
        if squad_m and _has_player_squad(squad_m.group(1)):
            last_valid_squad_idx = idx

        if RE_GAME_TIMER_RESET.search(line) and t_abs is not None:
            battle_start_idx = idx
            first_hit_idx = None
            last_hit_idx = None
            last_hit_t = None
            prev_hit_idx = None
            timer_end_idx = None
            timer_end_t = None
            timer_elapsed_s = None
            continue

        if (
            (
                (RE_GAME_TIMER_START.search(line) and _timer_line_is_window_boundary(line))
                or RE_OFFICIAL_TIMER_START.search(line)
            )
            and t_abs is not None
        ):
            battle_start_idx = idx
            first_hit_idx = None
            last_hit_idx = None
            last_hit_t = None
            prev_hit_idx = None
            timer_end_idx = None
            continue

        game_timer_end_match = RE_GAME_TIMER_END.search(line)
        official_timer_end_match = RE_OFFICIAL_TIMER_END.search(line)
        timer_end_match = official_timer_end_match or game_timer_end_match
        if (
            timer_end_match
            and (official_timer_end_match or _timer_line_is_window_boundary(line))
            and first_hit_idx is not None
        ):
            timer_end_idx = idx
            timer_end_t = t_abs
            try:
                timer_elapsed_s = int(timer_end_match.group(1)) / 1000.0
            except (TypeError, ValueError):
                timer_elapsed_s = None
            continue

        if not RE_HIT.search(line) or t_abs is None:
            continue

        if first_hit_idx is None:
            if battle_start_idx is None:
                battle_start_idx = 0
            first_hit_idx = idx
        elif last_hit_t is not None and t_abs - last_hit_t > IDLE_RESET_SEC:
            gap_cutoff = last_hit_t + IDLE_RESET_SEC
            battle_start_idx = idx
            while battle_start_idx > 0:
                prev_t = line_times[battle_start_idx - 1]
                if prev_t is None or prev_t <= gap_cutoff:
                    break
                battle_start_idx -= 1
            first_hit_idx = idx

        last_hit_idx = idx
        prev_hit_idx = idx
        last_hit_t = t_abs
        timer_end_idx = None

    if first_hit_idx is None or last_hit_idx is None:
        return [], None

    while battle_start_idx < first_hit_idx and RE_IDLE_TICK.search(lines[battle_start_idx]):
        battle_start_idx += 1

    if timer_end_idx is not None:
        end_idx = timer_end_idx + 1
        if timer_end_t is not None and timer_elapsed_s and timer_elapsed_s > 0:
            official_start_t = timer_end_t - timer_elapsed_s
            for idx in range(0, timer_end_idx + 1):
                t_abs = line_times[idx]
                if t_abs is not None and t_abs >= official_start_t:
                    battle_start_idx = idx
                    break
    else:
        end_idx = len(lines)
        idle_cutoff = last_hit_t + IDLE_RESET_SEC if last_hit_t is not None else None
        if idle_cutoff is not None:
            for idx in range(last_hit_idx + 1, len(lines)):
                t_abs = line_times[idx]
                if t_abs is not None and t_abs > idle_cutoff:
                    end_idx = idx
                    break
        while end_idx > last_hit_idx + 1 and RE_IDLE_TICK.search(lines[end_idx - 1]):
            end_idx -= 1

    hit_count = 0
    window_first_hit_idx = None
    window_last_hit_idx = None
    for idx in range(battle_start_idx, end_idx):
        if RE_HIT.search(lines[idx]):
            hit_count += 1
            if window_first_hit_idx is None:
                window_first_hit_idx = idx
            window_last_hit_idx = idx

    if window_first_hit_idx is not None:
        first_hit_idx = window_first_hit_idx
    if window_last_hit_idx is not None:
        last_hit_idx = window_last_hit_idx

    meta = {
        "start_idx": battle_start_idx,
        "end_idx": end_idx,
        "hit_count": hit_count,
        "first_hit_ts": _trace_ts_text(lines[first_hit_idx]),
        "last_hit_ts": _trace_ts_text(lines[last_hit_idx]),
    }
    return lines[battle_start_idx:end_idx], meta


class LogTailer(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.stats = OrderedDict()
        self.rdps_stats = OrderedDict()
        self.active_buffs = {}
        self.hit_events = []
        self.entity_owner = {}  # non-chr entity → player chr_key (summon tracking)
        self.weapon_owner = {}  # weapon_id → (player chr_key, last_seen_ts)
        # karin combo buff: BB has no rate (values live in skill-formula),
        # but we know from the game: "全队下一次战技+30% / 终结技+20%, 触发后清空"
        # Track uids here and inject a virtual effect at hit time.
        self._karin_combo_uids = set()
        self._karin_combo_trigger_uids = set()
        self._karin_combo_trigger_sources = {}
        # Consume window: release markers decide the consuming attacker; the
        # first qualifying hit decides normal-skill vs ultimate rate, and later
        # hits from the same attacker+kind keep the same rate.
        self._karin_combo_window_end = 0.0
        self._karin_combo_window_kind = None   # "normal_skill" or "ultimate"
        self._karin_combo_window_rate = 0.0
        self._karin_combo_window_attacker = None
        self._karin_combo_window_source = None
        # Current party from DLL SquadManager hook — authoritative roster.
        # Empty means "roster unknown", so strict rDPS gating skips allocation
        # instead of guessing from hit history.
        self.current_squad = set()
        self._bootstrap_current_squad()
        self.observed_roster = set()
        # owner_string → (chr_key, timestamp) — most recent chr-specific buff
        # applied to that owner. Used by generic-buff chr-borrow heuristic to
        # resolve ownership of affixes/proc-type buffs whose src is garbled.
        self._last_chr_src_on_owner = {}
        self.lock = threading.Lock()
        self._stop = threading.Event()
        self._gen = 0
        self._pending_buff = None
        self._pending_buff_is_end = False
        self._current_trace_t = None
        self._trace_day_offset = 0.0
        self._last_trace_clock = None
        # Buffer hits until we collect any same-seq metadata emitted
        # immediately after HP_V2 (BASELINE, DPD_RAW, ...). Older logs without
        # those lines are flushed on the next hit / end-of-batch.
        self._pending_hits = {}  # seq -> {"match","gen","baseline","trace_t","dpd"}
        # Per-attacker baseline cache. Keyed by char_key (chr_XXXX_name).
        # Gear baseline only changes on loadout/upgrade (not combat), so
        # cached value stays valid for entire battle unless player re-equips.
        self._baseline_cache = {}  # char_key -> {"values": {attrType: value}, "trace_t": float}
        self.enemy_damage = {}
        self.enemy_hits = {}
        self.enemy_last_t = {}
        self.current_enemy_key = None
        self._battle_start_t = 0.0
        self._battle_last_t = 0.0
        self._live_core = LiveOverlayBattleParser(
            idle_split_ms=int(IDLE_RESET_SEC * 1000),
            min_reparse_interval_ms=500,
            align_to_data_watermark=True,
        )
        self._tail_position = 0
        self._tail_file_identity = None
        self._bootstrap_recent_battle()

    def _bootstrap_current_squad(self):
        """Seed current_squad from the latest valid SQUAD line already
        present in dxg_trace.dat so the overlay isn't blank until the next
        10s heartbeat arrives."""
        if not os.path.exists(TRACE_FILE):
            return
        try:
            with open(TRACE_FILE, "rb") as f:
                raw = f.read()
            lines = raw.decode("utf-8", errors="replace").splitlines()
        except Exception:
            return
        last_reset_index = max(
            (index for index, line in enumerate(lines) if RE_GAME_TIMER_RESET.search(line)),
            default=-1,
        )
        for line in reversed(lines[last_reset_index + 1:]):
            m = RE_SQUAD.search(line)
            if not m:
                continue
            sq = self._parse_squad_members(m.group(1))
            if sq:
                self.current_squad = sq
                return

    def _parse_squad_members(self, members_blob):
        sq = set()
        for n in members_blob.split():
            k = self._char_key(n)
            if k and self._is_player(k):
                sq.add(k)
        return sq

    def _effective_roster(self):
        squad = set(self.current_squad)
        if squad:
            return squad
        return set(self.observed_roster)

    def _recompute_display_rates_locked(self):
        elapsed = self._battle_last_t - self._battle_start_t
        for s in self.stats.values():
            s.elapsed = elapsed if elapsed > 0 else 0.0
            s.dps = s.total_dmg / elapsed if elapsed > 0.5 else 0.0
        for rs in self.rdps_stats.values():
            rs.rdps = rs.total_rd / elapsed if elapsed > 0.5 else 0.0

    def _live_core_rows(self):
        report = self._live_core.snapshot(refresh=False)
        if not report:
            return None
        participants = report.get("participants") or []
        with self.lock:
            old_stats = dict(self.stats)
            old_rdps = dict(self.rdps_stats)
        stats = OrderedDict()
        rdps = OrderedDict()
        duration_sec = max(float((report.get("battle") or {}).get("duration_ms") or 0) / 1000.0, 0.0)
        rdps_available = bool((report.get("battle") or {}).get("rdps_available", True))
        for entry in participants:
            key = str(entry.get("character_key") or "")
            if not key:
                continue
            old = old_stats.get(key)
            s = CharStats(key)
            s.total_dmg = float(entry.get("total_damage") or 0.0)
            s.dps = float(entry.get("dps") or 0.0)
            s.max_hit = int(entry.get("max_hit") or 0)
            s.hits = int(entry.get("hit_count") or (old.hits if old else 0))
            s.elapsed = duration_sec
            crit_rate = entry.get("crit_rate")
            if crit_rate is not None and s.hits > 0:
                try:
                    crit_count = max(0, min(s.hits, int(round(float(crit_rate) * s.hits))))
                except (TypeError, ValueError):
                    crit_count = None
                if crit_count is not None:
                    s.crit_c = crit_count
                    s.crit_n = max(0, s.hits - s.crit_c)
            if old:
                if s.crit_n == 0 and s.crit_c == 0:
                    s.crit_n = old.crit_n
                    s.crit_c = old.crit_c
                s.first_hit_t = old.first_hit_t
                s.last_hit_t = old.last_hit_t
            else:
                hit_count = int(entry.get("hit_count") or 0)
                crit_hits = int(entry.get("crit_hits") or 0)
                if s.crit_n == 0 and s.crit_c == 0 and hit_count > 0:
                    s.crit_c = max(0, min(hit_count, crit_hits))
                    s.crit_n = max(0, hit_count - s.crit_c)
            stats[key] = s

            if rdps_available:
                old_r = old_rdps.get(key)
                rs = CharRdpsStats(key)
                rs.total_rd = float(entry.get("total_rd") or 0.0)
                rs.rdps = float(entry.get("rdps") or 0.0)
                rs.max_rd = float(entry.get("max_rd") or (old_r.max_rd if old_r else 0.0))
                if old_r:
                    rs.first_t = old_r.first_t
                    rs.last_t = old_r.last_t
                rdps[key] = rs
        if not rdps_available:
            for key, s in stats.items():
                rs = CharRdpsStats(key)
                rs.total_rd = s.total_dmg
                rs.rdps = s.dps
                rs.max_rd = float(s.max_hit)
                rs.first_t = s.first_hit_t
                rs.last_t = s.last_hit_t
                rdps[key] = rs
        return stats, rdps, report

    def set_live_clock_enabled(self, enabled):
        self._live_core.set_live_clock_enabled(enabled)

    def live_rdps_available(self):
        live_rows = self._live_core_rows()
        if live_rows is None:
            return True
        _stats, _rdps, report = live_rows
        return bool((report.get("battle") or {}).get("rdps_available", True))

    def _pick_current_enemy_locked(self):
        if not self.enemy_damage:
            return None
        boss_keys = [k for k in self.enemy_damage if is_boss_enemy(k)]
        pool = boss_keys or list(self.enemy_damage.keys())
        return max(
            pool,
            key=lambda k: (
                self.enemy_damage.get(k, 0.0),
                self.enemy_hits.get(k, 0),
                self.enemy_last_t.get(k, 0.0),
            ),
        )

    def _record_enemy_hit_locked(self, enemy, hit_dmg, now):
        enemy_key = extract_enemy_key(enemy)
        if not enemy_key:
            return
        self.enemy_damage[enemy_key] = self.enemy_damage.get(enemy_key, 0.0) + hit_dmg
        self.enemy_hits[enemy_key] = self.enemy_hits.get(enemy_key, 0) + 1
        self.enemy_last_t[enemy_key] = now
        self.current_enemy_key = self._pick_current_enemy_locked()

    def get_status_snapshot(self):
        live_rows = self._live_core_rows()
        if live_rows is not None:
            _stats, _rdps, report = live_rows
            battle = report.get("battle") or {}
            elapsed = max(float(battle.get("duration_ms") or 0) / 1000.0, 0.0)
            dungeon_name = format_overlay_dungeon_name(battle)
            if battle.get("rdps_available") is False:
                dungeon_name = f"{dungeon_name} / rDPS未确认"
            return {
                "dungeonName": dungeon_name,
                "elapsed": elapsed,
            }
        return {
            "dungeonName": "-",
            "elapsed": 0.0,
        }

    def _bootstrap_recent_battle(self):
        """Replay the latest battle slice already present in dxg_trace.dat so
        a fresh overlay instance can immediately show the most recent fight
        even when no new lines have arrived yet."""
        try:
            with open(TRACE_FILE, "rb") as source:
                source_stat = os.fstat(source.fileno())
                source_identity = (source_stat.st_dev, source_stat.st_ino)
                raw = source.read()
        except OSError:
            return
        # The cursor and the bootstrapped data must come from the same read.
        # Rechecking EOF later would skip a restart appended during bootstrap.
        complete_end = raw.rfind(b"\n") + 1
        complete = raw[:complete_end]
        lines, meta = read_latest_battle_log_slice(trace_bytes=complete)
        if not lines:
            return
        # Retain explicit lifecycle/context lines after a completed battle too.
        trailing = complete.decode("utf-8", errors="replace").splitlines(keepends=True)[meta["end_idx"]:]
        batch_gen = self._gen
        for raw_line in [*lines, *trailing]:
            line = raw_line.rstrip("\r\n")
            if line:
                self._parse(line, batch_gen)
        if complete_end == len(raw):
            self._finalize_pending_end()
            self._flush_pending_hits(gen=batch_gen)
        self._tail_position = complete_end
        self._tail_file_identity = source_identity

    def _line_time(self, line):
        m = RE_TS.search(line)
        if not m:
            return None
        clock = (
            int(m.group(1)) * 3600
            + int(m.group(2)) * 60
            + int(m.group(3))
            + float(f"0.{m.group(4)}")
        )
        if self._last_trace_clock is not None and clock + 300 < self._last_trace_clock:
            self._trace_day_offset += 86400.0
        self._last_trace_clock = clock
        return self._trace_day_offset + clock

    def _event_now(self):
        return self._current_trace_t if self._current_trace_t is not None else time.time()

    def _flush_pending_hits(self, gen=None, before_seq=None, before_trace_t=None):
        if not self._pending_hits:
            return
        pending_items = []
        for seq, entry in sorted(self._pending_hits.items()):
            hit_m = entry["match"]
            hit_gen = entry["gen"]
            baseline = entry.get("baseline")
            hit_trace_t = entry.get("trace_t")
            hit_dpd = entry.get("dpd")
            if gen is not None and hit_gen != gen:
                continue
            if before_seq is not None and seq >= before_seq:
                continue
            if before_trace_t is not None:
                if hit_trace_t is None or hit_trace_t >= before_trace_t:
                    continue
            pending_items.append((seq, hit_m, hit_gen, baseline, hit_trace_t, hit_dpd))
        if not pending_items:
            return
        for seq, hit_m, hit_gen, baseline, hit_trace_t, hit_dpd in pending_items:
            self._pending_hits.pop(seq, None)
            prev_trace_t = self._current_trace_t
            self._current_trace_t = hit_trace_t
            try:
                self._handle_hit(hit_m, hit_gen, baseline=baseline, dpd=hit_dpd)
            finally:
                self._current_trace_t = prev_trace_t

    def _finalize_pending_end(self):
        if self._pending_buff is None or not self._pending_buff_is_end:
            return
        uid = self._pending_buff
        with self.lock:
            self.active_buffs.pop(uid, None)
            self._karin_combo_uids.discard(uid)
            self._karin_combo_trigger_uids.discard(uid)
            self._karin_combo_trigger_sources.pop(uid, None)
        self._pending_buff = None
        self._pending_buff_is_end = False

    def _dynamic_effects(self, buff, now=None):
        specs = buff.get("dynamic_effects") or ()
        if not specs:
            return []
        ref_t = now if now is not None else self._event_now()
        end_t = buff.get("t_end")
        if end_t is not None and ref_t is not None and ref_t > end_t + 1e-6:
            return []
        start_t = buff.get("t_start", ref_t)
        out = []
        for spec in specs:
            if spec.get("kind") != "def_decrease":
                continue
            rate = max(0.0, spec.get("base_rate", 0.0))
            tick_rate = max(0.0, spec.get("tick_rate", 0.0))
            if tick_rate > 0 and ref_t is not None and start_t is not None and ref_t > start_t:
                rate += (ref_t - start_t) * tick_rate
            max_rate = max(0.0, spec.get("max_rate", 0.0))
            if max_rate > 0:
                rate = min(rate, max_rate)
            if rate > 0.001:
                out.append((spec["zone"], spec["elem"], rate))
        return out

    def _active_effects(self, buff, now=None):
        ref_t = now if now is not None else self._event_now()
        end_t = buff.get("t_end")
        if end_t is not None and ref_t is not None and ref_t > end_t + 1e-6:
            return []
        if buff.get("dynamic_effects"):
            return self._dynamic_effects(buff, now=ref_t)
        return list(buff.get("effects") or [])

    def run(self):
        pos = self._tail_position
        partial = b""
        snapshot_worker = None

        try:
            while not self._stop.is_set():
                try:
                    with open(TRACE_FILE, "rb") as f:
                        source_stat = os.fstat(f.fileno())
                        identity = (source_stat.st_dev, source_stat.st_ino)
                        if (self._tail_file_identity is not None and identity != self._tail_file_identity) or source_stat.st_size < pos:
                            # A replacement/truncation is an explicit input boundary.
                            # Never join a partial old line to the new capture.
                            pos, partial = 0, b""
                            self._live_core.reset()
                            with self.lock:
                                self._gen += 1
                                for collection in (self.stats, self.rdps_stats, self.hit_events, self.active_buffs,
                                        self.entity_owner, self.weapon_owner, self.current_squad, self.observed_roster,
                                        self.enemy_damage, self.enemy_hits, self.enemy_last_t, self._pending_hits,
                                        self._baseline_cache, self._karin_combo_uids, self._karin_combo_trigger_uids,
                                        self._karin_combo_trigger_sources, self._last_chr_src_on_owner):
                                    collection.clear()
                                self.current_enemy_key = self._pending_buff = self._current_trace_t = None
                                self._pending_buff_is_end = False
                                self._battle_start_t = self._battle_last_t = self._trace_day_offset = 0.0
                                self._last_trace_clock = None
                                self._karin_combo_window_end = 0.0
                                self._karin_combo_window_kind = self._karin_combo_window_attacker = self._karin_combo_window_source = None
                                self._karin_combo_window_rate = 0.0
                        self._tail_file_identity = identity
                        f.seek(pos)
                        raw = f.read(source_stat.st_size - pos)
                        pos = f.tell()
                    if not raw:
                        if (self._live_core._dirty or self._live_core._snapshot_from_explicit_clock) and (snapshot_worker is None or not snapshot_worker.is_alive()):
                            snapshot_worker = threading.Thread(target=self._live_core.snapshot, name="endfield-live-stats", daemon=True)
                            snapshot_worker.start()
                        time.sleep(TAIL_INTERVAL)
                        continue
                    joined = partial + raw
                    complete_end = joined.rfind(b"\n") + 1
                    partial = joined[complete_end:]
                    lines = joined[:complete_end].decode("utf-8", errors="replace").splitlines()
                    with self.lock:
                        batch_gen = self._gen
                    for line in lines:
                        with self.lock:
                            if self._gen != batch_gen:
                                break
                        self._parse(line, batch_gen)
                    if not partial:
                        self._finalize_pending_end()
                    # Flush hits still waiting on a same-seq BASELINE line. This
                    # covers the first hit of a new attacker and old logs that
                    # never emitted baseline data.
                    if not partial:
                        self._flush_pending_hits(gen=batch_gen)
                    # One worker computes the shared report while the reader
                    # keeps consuming official boundaries and new damage. No
                    # old batch jobs are queued; snapshot's generation guard
                    # rejects a completed report from before a reset/new start.
                    if (self._live_core._dirty or self._live_core._snapshot_from_explicit_clock) and (snapshot_worker is None or not snapshot_worker.is_alive()):
                        snapshot_worker = threading.Thread(target=self._live_core.snapshot, name="endfield-live-stats", daemon=True)
                        snapshot_worker.start()
                    if os.path.getsize(TRACE_FILE) > pos:
                        continue
                except Exception:
                    pass
                time.sleep(TAIL_INTERVAL)
        finally:
            # stop() only signals the reader; closing the UI never waits here.
            # Direct run() callers/tests still leave no parser work behind.
            if snapshot_worker is not None:
                snapshot_worker.join()

    def _parse(self, line, gen):
        with self.lock:
            if self._gen != gen:
                return
            if self._live_core is not None:
                self._live_core.feed_line(line)
        self._current_trace_t = self._line_time(line)
        bb_match = RE_BB.search(line)
        baseline_match = RE_BASELINE.search(line)
        dpd_match = RE_DPD_RAW.search(line)
        game_timer_start_match = RE_GAME_TIMER_START.search(line) if _timer_line_is_window_boundary(line) else None
        official_timer_start_match = RE_OFFICIAL_TIMER_START.search(line)
        timer_start_match = official_timer_start_match or game_timer_start_match
        timer_reset_match = RE_GAME_TIMER_RESET.search(line)
        game_timer_end_match = RE_GAME_TIMER_END.search(line) if _timer_line_is_window_boundary(line) else None
        official_timer_end_match = RE_OFFICIAL_TIMER_END.search(line)
        timer_end_match = official_timer_end_match or game_timer_end_match
        is_pending_hit_metadata = False
        if baseline_match:
            try:
                is_pending_hit_metadata = int(baseline_match.group(1)) in self._pending_hits
            except ValueError:
                is_pending_hit_metadata = False
        if not is_pending_hit_metadata and dpd_match:
            try:
                is_pending_hit_metadata = int(dpd_match.group(1)) in self._pending_hits
            except ValueError:
                is_pending_hit_metadata = False
        if not is_pending_hit_metadata and self._current_trace_t is not None:
            # Only BASELINE/DPD_RAW lines are same-hit metadata. If time has
            # truly advanced, settle older hits. Events that share the same
            # displayed millisecond can still belong to that damage frame in
            # viewer output, so keep those hits pending until the next tick.
            self._flush_pending_hits(gen=gen, before_trace_t=self._current_trace_t - 1e-6)
        if self._pending_buff_is_end and not bb_match:
            self._finalize_pending_end()
        if timer_start_match or timer_reset_match:
            now = self._event_now()
            with self.lock:
                if self._gen != gen:
                    return
                self.stats.clear()
                self.rdps_stats.clear()
                self.hit_events.clear()
                self.observed_roster.clear()
                self.enemy_damage.clear()
                self.enemy_hits.clear()
                self.enemy_last_t.clear()
                self.current_enemy_key = None
                self._battle_start_t = now
                self._battle_last_t = now
                self._pending_hits.clear()
                self._karin_combo_window_end = 0.0
                self._karin_combo_window_kind = None
                self._karin_combo_window_rate = 0.0
                self._karin_combo_window_attacker = None
                self._karin_combo_window_source = None
                if timer_reset_match:
                    # Scene/capture resets invalidate a squad cached before
                    # entering the dungeon. A fresh SQUAD line will repopulate
                    # this after the reset when the packet is authoritative.
                    self.current_squad.clear()
            return
        if timer_end_match:
            try:
                elapsed_s = int(timer_end_match.group(1)) / 1000.0
            except (TypeError, ValueError):
                elapsed_s = 0.0
            if elapsed_s > 0:
                now = self._event_now()
                with self.lock:
                    if self._gen != gen:
                        return
                    if self._battle_start_t == 0.0:
                        self._battle_start_t = max(now - elapsed_s, 0.0)
                    self._battle_last_t = self._battle_start_t + elapsed_s
            return
        m = RE_BUFF_START.search(line)
        if m:
            self._handle_buff_start(m)
            return
        if bb_match:
            self._handle_bb(bb_match)
            return
        m = RE_BUFF_END.search(line)
        if m:
            self._handle_buff_end(m)
            return
        m = RE_HIT.search(line)
        if m:
            # Delay hit processing until we have collected any same-seq
            # metadata lines that immediately follow HP_V2 (BASELINE, DPD_RAW,
            # ...). Older logs or disabled probes still flush safely on the
            # next hit / end-of-batch.
            try:
                seq = int(m.group(1))
            except (ValueError, IndexError):
                seq = None
            if seq is None:
                self._handle_hit(m, gen)
            else:
                # Once a newer hit arrives, any older pending hit will not get
                # any more same-seq metadata. Handle it now while active buff
                # state is still close to the original hit, instead of waiting
                # until the end of the whole replay batch.
                self._flush_pending_hits(
                    gen=gen,
                    before_seq=seq,
                    before_trace_t=self._current_trace_t - 1e-6 if self._current_trace_t is not None else None,
                )
                skill = m.group(4)
                atk_raw = m.group(7)
                atk_char = self._infer_attacker_char(skill, atk_raw)
                cached = self._baseline_cache.get(atk_char) if atk_char else None
                self._pending_hits[seq] = {
                    "match": m,
                    "gen": gen,
                    "baseline": cached,
                    "trace_t": self._current_trace_t,
                    "dpd": None,
                }
            return
        m = baseline_match
        if m:
            seq = int(m.group(1))
            baseline = {}
            for mv in RE_BASELINE_KV.finditer(m.group(2)):
                try:
                    baseline[int(mv.group(1))] = float(mv.group(2))
                except ValueError:
                    pass
            pending = self._pending_hits.get(seq)
            if pending is None:
                return
            hit_m = pending["match"]
            # Cache baseline by attacker char_key for subsequent hits.
            skill = hit_m.group(4)
            atk_raw = hit_m.group(7)
            atk_char = self._infer_attacker_char(skill, atk_raw)
            snapshot = {"values": baseline, "trace_t": self._current_trace_t, "seq": seq}
            if atk_char:
                self._baseline_cache[atk_char] = snapshot
            pending["baseline"] = snapshot
            return
        m = dpd_match
        if m:
            seq = int(m.group(1))
            pending = self._pending_hits.get(seq)
            if pending is None:
                return
            try:
                damage_type = int(m.group(5), 16)
            except ValueError:
                damage_type = 0
            try:
                decorate_mask = int(m.group(6), 16)
            except ValueError:
                decorate_mask = 0
            pending["dpd"] = {
                "calc": float(m.group(2)),
                "atkScale": float(m.group(3)),
                "blocked": int(m.group(4)),
                "damageType": damage_type,
                "decorateMask": decorate_mask,
                "collider": m.group(7),
                "atkZones": _parse_zone_values(m.group(8)),
                "defZones": _parse_zone_values(m.group(9)),
            }
            return
        # DLL SquadManager snapshot — authoritative party roster
        m = RE_SQUAD.search(line)
        if m:
            sq = self._parse_squad_members(m.group(1))
            with self.lock:
                if sq:
                    self.current_squad = sq
            return
        # Phase 18 template lines — dedup-on-insert by (i, attrType) / (d, p)
        m = RE_ATTR_MOD.search(line)
        if m:
            bid, i, at, form, useKey, val, bbKey = m.groups()
            t = BUFF_TEMPLATES.setdefault(bid, {'attrs': [], 'dmgs': []})
            key = (int(i), int(at))
            if not any((e[0], e[1]) == key for e in t['attrs']):
                t['attrs'].append((int(i), int(at), int(form), int(useKey), float(val), bbKey))
            return
        m = RE_DMG_MOD.search(line)
        if m:
            bid, d, p, cls, side, zone, useKey, val, bbKey = m.groups()
            t = BUFF_TEMPLATES.setdefault(bid, {'attrs': [], 'dmgs': []})
            key = (int(d), int(p))
            if not any((e[0], e[1]) == key for e in t['dmgs']):
                t['dmgs'].append((int(d), int(p), cls, int(side), zone, int(useKey), float(val), bbKey))
            return

    def _handle_buff_start(self, m):
        buff_id, uid_s, owner, src = m.group(1), m.group(2), m.group(3), m.group(4)
        if buff_id in NEUTRAL_BUFFS:
            self._pending_buff = None
            self._pending_buff_is_end = False
            return
        now = self._event_now()
        duration_s = None
        try:
            duration_s = float(m.group(5)) if m.group(5) is not None else None
        except (TypeError, ValueError):
            duration_s = None
        if duration_s is not None and (duration_s < 0 or duration_s > 1_000_000):
            duration_s = None
        end_t = (now + duration_s) if (now is not None and duration_s is not None) else None
        # Learn summon/entity ownership: buff_chr_XXXX_name_* on non-chr owner
        chr_match = self._RE_BUFF_CHR.search(buff_id)
        if chr_match:
            chr_key = self._char_key(chr_match.group(1))
            if chr_key and self._is_player(chr_key):
                # entity_owner is for friendly summons only (abilityentity_*,
                # funnel, etc.) — NOT enemies. A chr-tagged buff *debuff* on an
                # enemy (e.g. ardelia's vuln on eny_0029_lbmob) must not register
                # the enemy as ardelia's owned entity, otherwise every debuff
                # on that enemy gets owner_key=ardelia in _handle_hit, fails
                # the on_enemy test (owner_key is not None), and is skipped —
                # killing rDPS attribution for all attackers' debuffs.
                def _is_summon_target(o):
                    return (not o.startswith("chr_")
                            and not o.startswith("eny_"))
                if _is_summon_target(owner) and owner not in self.entity_owner:
                    self.entity_owner[owner] = chr_key
                if _is_summon_target(src) and src not in self.entity_owner:
                    self.entity_owner[src] = chr_key
                # Mark owner as recently touched by this chr's buff — used by
                # _resolve_buff_source to attribute generic-buff bursts. This
                # is fine to set for enemy owners (chr-borrow window is the
                # intended mechanism).
                self._last_chr_src_on_owner[owner] = (chr_key, now)
        resolved_src = self._resolve_buff_source(buff_id, src, owner, now)
        uid = int(uid_s)
        with self.lock:
            if _is_singleton_refresh_buff(buff_id):
                for old_uid, old in list(self.active_buffs.items()):
                    if old_uid == uid:
                        continue
                    if old.get("buff_id") != buff_id:
                        continue
                    if old.get("raw_owner", old.get("owner")) != owner:
                        continue
                    same_raw_src = old.get("raw_src", old.get("src")) == src
                    same_resolved_src = old.get("src") == (resolved_src or src)
                    if same_raw_src or same_resolved_src:
                        self.active_buffs.pop(old_uid, None)
            self.active_buffs[uid] = {
                "buff_id": buff_id, "owner": owner, "src": resolved_src or src,
                "raw_owner": owner, "raw_src": src,
                "effects": [],
                "dynamic_effects": [],
                "t_start": now,
                "t_end": end_t,
            }
            src_key = self._resolve_player_entity(resolved_src or src)
            owner_key = self._resolve_player_entity(owner)
            if not self.current_squad:
                for key in (src_key, owner_key):
                    if key and self._is_player(key):
                        self.observed_roster.add(key)
            if KARIN_COMBO_BUFF_RE.match(buff_id):
                # Game enforces 1-stack: new apply overrides any stale uids.
                for old_uid in list(self._karin_combo_uids):
                    self.active_buffs.pop(old_uid, None)
                self._karin_combo_uids.clear()
                self._karin_combo_uids.add(uid)
            if buff_id == KARIN_COMBO_TRIGGER_BUFF_ID and src_key:
                self._karin_combo_trigger_uids.add(uid)
                self._karin_combo_trigger_sources[uid] = src_key
            if (buff_id in KARIN_COMBO_IMBUE_BUFF_IDS
                    and src_key and owner_key):
                same_pending_window = (
                    self._karin_combo_window_attacker == owner_key
                    and self._karin_combo_window_source == src_key
                    and self._karin_combo_window_kind is None
                    and now < self._karin_combo_window_end
                )
                source_has_pending_trigger = any(
                    self._karin_combo_trigger_sources.get(trigger_uid) == src_key
                    for trigger_uid in self._karin_combo_trigger_uids
                )
                if source_has_pending_trigger or same_pending_window:
                    self._activate_karin_combo_window_locked(owner_key, src_key, now)
        self._pending_buff = uid
        self._pending_buff_is_end = False

    def _activate_karin_combo_window_locked(self, attacker_key, source_key, now):
        # The combo is consumed when the next skill is released. The later
        # damage hit only tells us whether that release was a normal skill or ult.
        source_trigger_uids = [
            trigger_uid
            for trigger_uid in self._karin_combo_trigger_uids
            if self._karin_combo_trigger_sources.get(trigger_uid) == source_key
        ]
        self._karin_combo_window_end = now + COMBO_WINDOW_SEC
        self._karin_combo_window_kind = None
        self._karin_combo_window_rate = 0.0
        self._karin_combo_window_attacker = attacker_key
        self._karin_combo_window_source = source_key
        for trigger_uid in source_trigger_uids:
            self.active_buffs.pop(trigger_uid, None)
            self._karin_combo_trigger_sources.pop(trigger_uid, None)
            self._karin_combo_trigger_uids.discard(trigger_uid)

    def _handle_bb(self, m):
        if self._pending_buff is None:
            return
        bb_text = m.group(2)
        bb_pairs = []
        for kv in RE_BB_KV.finditer(bb_text):
            try:
                bb_pairs.append((kv.group(1), float(kv.group(2))))
            except ValueError:
                pass
        with self.lock:
            buff = self.active_buffs.get(self._pending_buff)
            if not buff:
                return
            effects = classify_bb_effects(buff["buff_id"], bb_pairs)
            dynamic_effects = _extract_dynamic_effect_specs(buff["buff_id"], bb_pairs)
            buff["effects"] = effects or []
            buff["dynamic_effects"] = dynamic_effects or []
            if effects:
                buff["effects"] = effects
                # Per-attrType rate breakdown (template path only). Used by
                # baseline injection to subtract captured rates correctly.
                buff["attrType_rates"] = _compute_attrType_rates(
                    buff["buff_id"], bb_pairs)
                self._detect_and_drop_containers()
                self._enforce_buff_stack_limit_locked(self._pending_buff)
            elif dynamic_effects:
                buff["attrType_rates"] = _compute_attrType_rates(
                    buff["buff_id"], bb_pairs)
                self._enforce_buff_stack_limit_locked(self._pending_buff)
            else:
                # Keep buff_id-is-chr-specific buffs (e.g. antal_tageffect,
                # antal_ultimate_icon) as attribution markers even if their BB
                # has no rate. Hit-time generic-buff re-attribution looks up
                # chr_ markers on the same owner. rDPS loop skips them anyway
                # (empty effects list → `if not buff["effects"]: continue`).
                if self._RE_BUFF_CHR.search(buff["buff_id"]):
                    buff["effects"] = []  # explicit marker, stays in active_buffs
                else:
                    self.active_buffs.pop(self._pending_buff, None)
        if self._pending_buff_is_end:
            self._finalize_pending_end()

    def _detect_and_drop_containers(self):
        """Two jobs, both fired after a new buff's effects are ready:
        (1) Mirror dedup — same src + same effects set within 0.5s = the same
            game effect expressed via multiple buff triggers (e.g. laevat's
            energy_icon_5 / ignore_fire_resist / passive all RES/fire 20%).
        (2) Container detection — this buff is a child (indicator matched),
            look back for same-src non-child buff in CONTAINER_WINDOW_SEC with
            overlapping rates — that's the container to drop.
        Caller must hold self.lock."""
        uid = self._pending_buff
        buff = self.active_buffs.get(uid)
        if not buff or not buff["effects"]:
            return

        now = self._event_now()
        my_src = buff["src"]
        # effects signature used for mirror comparison (ignoring order)
        def _sig(effs):
            return frozenset((z, e, round(r, 4)) for z, e, r in effs)
        my_sig = _sig(buff["effects"])

        def _mirror_score(candidate):
            bid = candidate.get("buff_id", "")
            hint = _buff_hint_meta(bid) or {}
            classification = hint.get("classification")
            score = 0
            if BUFF_TEMPLATES.get(bid):
                score += 4
            if classification == "effect_buff":
                score += 3
            elif classification == "wrapper":
                score -= 2
            elif classification == "marker_or_utility":
                score -= 3
            low = bid.lower()
            if "icon" in low or "vfx" in low:
                score -= 1
            return score

        my_owner = buff["owner"]

        # (1) mirror dedup: drop older same-src + same-owner + same-effects
        # within 0.5s, but only across different buff ids. Same-id repeats can
        # be legitimate stacks / refresh-like layers (e.g. weapon valid stacks)
        # and should survive as separate active instances.
        for other_uid, other in list(self.active_buffs.items()):
            if other_uid == uid:
                continue
            if other.get("src") != my_src:
                continue
            if other.get("owner") != my_owner:
                continue
            if other.get("buff_id") == buff.get("buff_id"):
                continue
            if not other.get("effects"):
                continue
            if now - other.get("t_start", 0) > 0.5:
                continue
            if _sig(other["effects"]) == my_sig:
                my_score = _mirror_score(buff)
                other_score = _mirror_score(other)
                if my_score > other_score:
                    self.active_buffs.pop(other_uid, None)
                elif other_score > my_score:
                    self.active_buffs.pop(uid, None)
                    return

        # (2) container detection (original behaviour), but keep the same
        # owner guard so sibling applications to different teammates are not
        # mistaken for one another's container shell.
        if not CHILD_BUFF_INDICATORS.search(buff["buff_id"]):
            return
        my_effect_keys = {(e[0], e[1], round(e[2], 3)) for e in buff["effects"]}
        my_src = buff["src"]
        my_owner = buff["owner"]
        now = self._event_now()
        parent_buff_id = _parent_buff_id_of_default_child(buff["buff_id"])
        if parent_buff_id:
            for other_uid, other in list(self.active_buffs.items()):
                if other_uid == uid:
                    continue
                if other.get("buff_id") != parent_buff_id:
                    continue
                if now - other.get("t_start", 0) > CONTAINER_WINDOW_SEC:
                    continue
                if other.get("owner") != my_owner:
                    continue
                if _effect_sets_overlap(other.get("effects"), buff.get("effects")):
                    self.active_buffs.pop(uid, None)
                    return
        to_drop = []
        for other_uid, other in self.active_buffs.items():
            if other_uid == uid:
                continue
            if now - other.get("t_start", 0) > CONTAINER_WINDOW_SEC:
                continue
            if other.get("src") != my_src:
                continue
            if other.get("owner") != my_owner:
                continue
            if CHILD_BUFF_INDICATORS.search(other["buff_id"]):
                continue
            if not other.get("effects"):
                continue
            other_effect_keys = {(e[0], e[1], round(e[2], 3)) for e in other["effects"]}
            if my_effect_keys & other_effect_keys:
                to_drop.append(other_uid)
        for u in to_drop:
            self.active_buffs.pop(u, None)

    def _buff_stack_group_key(self, buff):
        src = buff.get("src") or buff.get("raw_src") or ""
        raw_src = buff.get("raw_src") or src
        src_key = (
            self._resolve_player_entity(src)
            or self._resolve_player_entity(raw_src)
            or src
            or raw_src
        )
        owner = buff.get("raw_owner") or buff.get("owner") or ""
        owner_key = self._resolve_player_entity(owner) or extract_enemy_key(owner) or owner
        return (buff.get("buff_id", ""), src_key, owner_key)

    def _enforce_buff_stack_limit_locked(self, uid):
        """Drop oldest live stack instances once a weapon buff exceeds max_stack.

        Caller must hold self.lock.
        """
        buff = self.active_buffs.get(uid)
        if not buff or not self._active_effects(buff):
            return
        limit = _weapon_stack_limit_for_buff(buff.get("buff_id"))
        if limit is None:
            return

        group_key = self._buff_stack_group_key(buff)
        peers = []
        for other_uid, other in self.active_buffs.items():
            if other.get("buff_id") != buff.get("buff_id"):
                continue
            if self._buff_stack_group_key(other) != group_key:
                continue
            if not self._active_effects(other):
                continue
            peers.append((other_uid, other))
        if len(peers) <= limit:
            return

        peers.sort(key=lambda item: (item[1].get("t_start", 0.0), item[0]))
        for old_uid, _old in peers[:-limit]:
            self.active_buffs.pop(old_uid, None)

    def _handle_buff_end(self, m):
        uid = int(m.group(1))
        with self.lock:
            self._karin_combo_uids.discard(uid)
            self._karin_combo_trigger_uids.discard(uid)
            self._karin_combo_trigger_sources.pop(uid, None)
            if uid not in self.active_buffs:
                self._pending_buff = None
                self._pending_buff_is_end = False
                return
        self._pending_buff = uid
        self._pending_buff_is_end = True

    # Enemy skills should be filtered by skill-name prefix (`eny_*`,
    # `buff_eny_*`), not by reclassifying playable `chr_*` ids as bosses.
    _ENEMY_NAMES = set()

    def _char_key(self, entity_name):
        if entity_name.startswith("chr_"):
            parts = entity_name.split("_")
            return "_".join(parts[:3]) if len(parts) >= 3 else entity_name
        return None

    def _is_player(self, char_key):
        if not char_key or not char_key.startswith("chr_"):
            return False
        short = char_key.split("_")[-1].lower()
        if short in self._ENEMY_NAMES:
            return False
        return True

    # Extract chr_XXXX_name from buff_id like "buff_chr_0027_tangtang_xxx"
    _RE_BUFF_CHR = re.compile(r"buff_(chr_\d+_[a-z]+)")
    # Extract chr_XXXX_name from entity like "abilityentity_chr_0027_tangtang_xxx"
    _RE_ENTITY_CHR = RE_CHAR_KEY

    def _resolve_player_entity(self, entity_name):
        if not entity_name:
            return None
        ck = self._char_key(entity_name)
        if ck and self._is_player(ck):
            return ck
        m = self._RE_ENTITY_CHR.search(entity_name)
        if m:
            ck = self._char_key(m.group(1))
            if ck and self._is_player(ck):
                return ck
        ck = self.entity_owner.get(entity_name)
        if ck and self._is_player(ck):
            return ck
        return None

    def _remember_weapon_owner(self, buff_id, char_key, now=None):
        weapon_id = _weapon_id_from_buff_id(buff_id)
        if not weapon_id or not char_key or not self._is_player(char_key):
            return None
        self.weapon_owner[weapon_id] = (
            char_key,
            now if now is not None else self._event_now(),
        )
        return char_key

    def _find_active_weapon_owner(self, buff_id, now=None, roster=None, exclude_uid=None):
        weapon_id = _weapon_id_from_buff_id(buff_id)
        if not weapon_id:
            return None
        ref_t = now if now is not None else self._event_now()
        best_ck = None
        best_dt = None
        for other_uid, other in self.active_buffs.items():
            if exclude_uid is not None and other_uid == exclude_uid:
                continue
            if _weapon_id_from_buff_id(other.get("buff_id", "")) != weapon_id:
                continue
            ck = self._resolve_player_entity(other.get("owner"))
            if ck is None:
                ck = self._resolve_player_entity(other.get("src"))
            if ck is None:
                continue
            if roster is not None and ck not in roster:
                continue
            dt = abs(other.get("t_start", ref_t) - ref_t)
            if best_dt is None or dt < best_dt:
                best_dt = dt
                best_ck = ck
        if best_ck:
            self._remember_weapon_owner(buff_id, best_ck, now=ref_t)
        return best_ck

    def _resolve_weapon_owner(self, buff_id, owner=None, src=None, now=None,
                              roster=None, exclude_uid=None):
        weapon_id = _weapon_id_from_buff_id(buff_id)
        if not weapon_id:
            return None
        team_source = self._resolve_replicated_weapon_team_source(
            buff_id,
            owner=owner,
            src=src,
            now=now,
            roster=roster,
            exclude_uid=exclude_uid,
        )
        if team_source:
            return team_source
        for entity_name in (owner, src):
            ck = self._resolve_player_entity(entity_name)
            if ck and (roster is None or ck in roster):
                return self._remember_weapon_owner(buff_id, ck, now=now)
        ck = self._find_active_weapon_owner(
            buff_id, now=now, roster=roster, exclude_uid=exclude_uid)
        if ck:
            return ck
        rec = self.weapon_owner.get(weapon_id)
        if rec:
            ck, _seen = rec
            if self._is_player(ck) and (roster is None or ck in roster):
                return ck
        ck = WEAPON_OWNER_MAP.get(weapon_id)
        if ck and self._is_player(ck) and (roster is None or ck in roster):
            return self._remember_weapon_owner(buff_id, ck, now=now)
        return None

    def _resolve_replicated_weapon_team_source(self, buff_id, owner=None, src=None,
                                               now=None, roster=None,
                                               exclude_uid=None):
        weapon_id = _weapon_id_from_buff_id(buff_id)
        if not weapon_id:
            return None
        raw_src_key = self._resolve_player_entity(src)
        owner_key = self._resolve_player_entity(owner)
        if not raw_src_key or not self._is_player(raw_src_key):
            return None
        if roster is not None and raw_src_key not in roster:
            return None
        if owner_key == raw_src_key:
            return None

        ref_t = now if now is not None else self._event_now()
        target_keys = set()
        if owner_key and (roster is None or owner_key in roster):
            target_keys.add(owner_key)

        for other_uid, other in self.active_buffs.items():
            if exclude_uid is not None and other_uid == exclude_uid:
                continue
            other_buff_id = other.get("buff_id", "")
            if other_buff_id != buff_id:
                continue
            if _weapon_id_from_buff_id(other_buff_id) != weapon_id:
                continue
            other_raw_src = other.get("raw_src", other.get("src"))
            other_src_key = self._resolve_player_entity(other_raw_src)
            if other_src_key != raw_src_key:
                continue
            other_t = other.get("t_start", ref_t)
            if abs(other_t - ref_t) > WEAPON_TEAM_REPLICATE_WINDOW_SEC:
                continue
            other_owner = other.get("raw_owner", other.get("owner"))
            other_owner_key = self._resolve_player_entity(other_owner)
            if other_owner_key and (roster is None or other_owner_key in roster):
                target_keys.add(other_owner_key)

        if len(target_keys) >= 2:
            # Once the replicated-team pattern is visible, repair peers that
            # were started a few lines earlier before enough targets existed
            # to identify the shared weapon owner.
            for other_uid, other in self.active_buffs.items():
                if exclude_uid is not None and other_uid == exclude_uid:
                    continue
                other_buff_id = other.get("buff_id", "")
                if other_buff_id != buff_id:
                    continue
                other_raw_src = other.get("raw_src", other.get("src"))
                other_src_key = self._resolve_player_entity(other_raw_src)
                if other_src_key != raw_src_key:
                    continue
                other_t = other.get("t_start", ref_t)
                if abs(other_t - ref_t) > WEAPON_TEAM_REPLICATE_WINDOW_SEC:
                    continue
                other["src"] = raw_src_key
            self._remember_weapon_owner(buff_id, raw_src_key, now=ref_t)
            return raw_src_key
        return None

    def _resolve_buff_source(self, buff_id, src, owner=None, now=None):
        """Resolve actual player source.

        Priority (highest → lowest):
          1. weapon buff → direct/self-side learn or cached owner by weapon_id.
          2. buff_id contains `chr_XXXX_` — strongest signal (id names the owner).
          3. For generic buffs, chr-borrow from the owner's recent chr-specific
             buff (engine sometimes records src as 'current leader' instead
             of the originator — wolfgd leader picking up antal's ult bundle).
          4. src field direct parse (trusted for non-generic buffs).
          5. For non-generic buffs only: entity pattern in src string.
          6. entity_owner summon map."""
        roster = set(self.current_squad) or None
        weapon_owner = self._resolve_weapon_owner(
            buff_id, owner=owner, src=src, now=now, roster=roster)
        if weapon_owner:
            return weapon_owner

        is_generic = any(buff_id.startswith(p) for p in GENERIC_BUFF_PREFIXES)

        # 1. buff_id explicitly names the owner — trust it over any src.
        m = self._RE_BUFF_CHR.search(buff_id)
        if m:
            candidate = m.group(1)
            ck = self._char_key(candidate)
            if ck and self._is_player(ck):
                return ck

        # 2. Generic buffs on *party members*: chr-borrow even if src looks
        # like a player (src may be the 'leader' position, not the originator).
        # Do NOT do this for enemy-owned debuffs like pelica conduct — their
        # direct src is usually the real applier, and cross-borrow would steal
        # credit from the correct owner.
        owner_key = self._char_key(owner) if owner else None
        owner_is_party_member = (
            owner_key is not None and
            self._is_player(owner_key) and
            owner_key in self.current_squad
        )
        if is_generic and owner and now is not None and owner_is_party_member:
            rec = self._last_chr_src_on_owner.get(owner)
            if rec:
                ck, t = rec
                if now - t < CHR_BORROW_WINDOW_SEC:
                    return ck

        # 3. src direct parse (trusted for non-generic buffs).
        src_key = self._char_key(src)
        if src_key and self._is_player(src_key):
            return src_key

        # 4. entity pattern in src — only for non-generic.
        if not is_generic:
            m = self._RE_ENTITY_CHR.search(src)
            if m:
                candidate = m.group(1)
                ck = self._char_key(candidate)
                if ck and self._is_player(ck):
                    return ck

        # 5. Summon map.
        if src in self.entity_owner:
            return self.entity_owner[src]
        return None

    def _resolve_effective_src_for_hit(self, uid, buff, owner_key, roster):
        weapon_owner = self._resolve_weapon_owner(
            buff["buff_id"],
            owner=buff.get("owner"),
            src=buff.get("src"),
            now=buff.get("t_start", 0),
            roster=roster,
            exclude_uid=uid,
        )
        if weapon_owner:
            return weapon_owner

        buff_src_key = self._char_key(buff["src"])
        is_generic = any(buff["buff_id"].startswith(p) for p in GENERIC_BUFF_PREFIXES)
        owner_is_party_member = owner_key is not None and owner_key in roster
        if is_generic and owner_is_party_member:
            owner_of_generic = buff["owner"]
            my_t = buff.get("t_start", 0)
            best_ck = None
            best_dt = 1.0  # 1s window — persistent passives are too far away
            for other_uid, other in self.active_buffs.items():
                if other_uid == uid:
                    continue
                if other.get("owner") != owner_of_generic:
                    continue
                chr_m = self._RE_BUFF_CHR.search(other.get("buff_id", ""))
                if not chr_m:
                    continue
                ck = self._char_key(chr_m.group(1))
                if not ck or not self._is_player(ck) or ck not in roster:
                    continue
                dt = abs(other.get("t_start", 0) - my_t)
                if dt < best_dt:
                    best_dt = dt
                    best_ck = ck
            if best_ck is not None:
                buff_src_key = best_ck

        if not buff_src_key or not self._is_player(buff_src_key):
            for pat, elem in ELEMENT_PROC_PATTERNS:
                if pat.match(buff["buff_id"]):
                    for cand in roster:
                        short = cand.split("_")[-1].lower()
                        if _character_element(short) == elem:
                            return cand
                    break
        return buff_src_key

    def _infer_attacker_char(self, skill, atk_raw):
        """Infer the attacking player char_key for a hit.

        Prefer the skill name whenever it already names a character. Only use
        `atk=` / entity-owner resolution as a fallback for buff/effect damage,
        because explicit enemy skills (`eny_*`, `buff_eny_*`) can still carry
        player-like attacker strings."""
        if skill.startswith(NON_PLAYER_SKILL_PREFIXES):
            return None
        skill_key = extract_char_key(skill)
        if skill_key and self._is_player(skill_key):
            return skill_key
        atk_key = self._resolve_player_entity(atk_raw)
        if atk_key and self._is_player(atk_key):
            return atk_key
        return None

    def _infer_enemy_entity(self, attacker, skill, src, tgt):
        """Infer the victim entity for a hit.

        Prefer the opposite side of the resolved attacker. For player skills,
        if src/tgt no longer directly names the player (projectile/summon
        entities), first prefer the side that does NOT resolve to any player
        entity, then fall back to the trace's usual `tgt` convention."""
        src_key = self._char_key(src)
        tgt_key = self._char_key(tgt)
        if src_key == attacker:
            return tgt
        if tgt_key == attacker:
            return src
        src_owner = self._resolve_player_entity(src)
        tgt_owner = self._resolve_player_entity(tgt)
        if src_owner and not tgt_owner:
            return tgt
        if tgt_owner and not src_owner:
            return src
        if skill.startswith("chr_"):
            return tgt
        return src

    def _owner_applies_to_hit(self, owner, owner_key, attacker, enemy, enemy_key):
        on_attacker = (owner_key == attacker)
        on_enemy = False
        if enemy_key and owner_key == enemy_key:
            on_enemy = True
        elif owner == enemy:
            on_enemy = True
        return on_attacker, on_enemy

    def _handle_hit(self, m, gen, baseline=None, dpd=None):
        # group(1) = seq (unused past dispatch); groups 2..10 = hit fields
        hit_dmg = int(m.group(2))
        raw_dmg = float(m.group(3))
        skill = m.group(4)
        src = m.group(5)
        tgt = m.group(6)
        atk_raw = m.group(7)
        shared_flag = int(m.group(8)) if m.group(8) else -1
        crit_flag = int(m.group(9)) if m.group(9) else -1
        if crit_flag < 0 and shared_flag >= 0:
            crit_flag = shared_flag & 1

        char = self._infer_attacker_char(skill, atk_raw)
        if not char:
            return

        # filter non-player attackers (bosses, enemies, intractables)
        if not self._is_player(char):
            return

        # enemy = whichever of src/tgt is NOT the attacker
        enemy = self._infer_enemy_entity(char, skill, src, tgt)
        enemy_key = extract_enemy_key(enemy) if enemy else None
        if not enemy_key:
            return
        # Match the hit viewer: skill/character classification is authoritative.
        # DPD damageType can describe an internal calculation bucket and may
        # disagree with buff-proc skills (e.g. Wulfa bleed extra damage).
        dmg_elem = get_damage_element(skill) or _damage_element_from_dpd(dpd)
        now = self._event_now()

        with self.lock:
            if self._gen != gen:
                return

            # Auto-reset on long idle → treat this hit as a new battle.
            # Clears battle-scoped aggregates; keeps active_buffs/entity_owner
            # (live game state). rDPS remains strictly gated by current_squad.
            if self._battle_last_t > 0 and now - self._battle_last_t > IDLE_RESET_SEC:
                    self.stats.clear()
                    self.rdps_stats.clear()
                    self.hit_events.clear()
                    self.observed_roster.clear()
                    self.enemy_damage.clear()
                    self.enemy_hits.clear()
                    self.enemy_last_t.clear()
                    self.current_enemy_key = None
                    self._battle_start_t = 0.0
                    self._battle_last_t = 0.0
                    self._karin_combo_window_end = 0.0
                    self._karin_combo_window_kind = None
                    self._karin_combo_window_rate = 0.0
                    self._karin_combo_window_attacker = None
                    self._karin_combo_window_source = None

            if self._battle_start_t == 0.0:
                self._battle_start_t = now
            self._battle_last_t = now

            # DPS stats
            s = self.stats.get(char)
            if not s:
                s = CharStats(char)
                self.stats[char] = s
            s.total_dmg += hit_dmg
            s.hits += 1
            if hit_dmg > s.max_hit:
                s.max_hit = hit_dmg
            if crit_flag == 0:
                s.crit_n += 1
            elif crit_flag == 1:
                s.crit_c += 1
            if s.first_hit_t == 0:
                s.first_hit_t = now
            s.last_hit_t = now
            s.last_update = now
            self.observed_roster.add(char)
            self._record_enemy_hit_locked(enemy, hit_dmg, now)
            self.hit_events.append((now, char, skill, hit_dmg))

            # rDPS: FFLogs-style log-ratio allocation
            # Cross-zone multiplicative, within-zone additive
            # ref: FFLogs Buff Allocation Math (joncho#3796)
            all_effects = []
            char_key_set = char  # attacker's char key
            # Prefer the authoritative SquadManager snapshot, but fall back to
            # player chars we've actually observed hitting in this battle when
            # the trace hasn't produced any SQUAD line.
            roster = self._effective_roster()
            if not roster or char not in roster:
                self._recompute_display_rates_locked()
                return

            for uid, buff in self.active_buffs.items():
                buff_effects = self._active_effects(buff, now=now)
                if not buff_effects:
                    continue
                buff_owner = buff["owner"]
                # Resolve owner to a player key if possible. `abilityentity_chr_*`
                # and known-summon entities belong to a player, not an enemy.
                buff_owner_key = self._resolve_player_entity(buff_owner)

                on_attacker, on_enemy = self._owner_applies_to_hit(
                    buff_owner, buff_owner_key, char_key_set, enemy, enemy_key
                )
                if not on_attacker and not on_enemy:
                    continue  # buff owned by another player's self/entity
                buff_src_key = self._resolve_effective_src_for_hit(
                    uid, buff, buff_owner_key, roster)
                if not buff_src_key or not self._is_player(buff_src_key):
                    continue
                if buff_src_key not in roster:
                    continue  # off-party char, skip (effect falls into attacker self_mult)
                # Buff-level skill-name filter (e.g. sword_0006 normal_atk+120
                # only applies to normal_skill*, not ult_attack*).
                if not _buff_applies_to_skill(buff["buff_id"], skill):
                    continue
                for zone, elem, rate in buff_effects:
                    if not _buff_effect_applies_to_skill(buff["buff_id"], zone, skill):
                        continue
                    if not _effect_matches_damage_element(elem, dmg_elem):
                        continue
                    is_self = (buff_src_key == char)
                    # Apply ally rate multiplier for buffs with "self-full /
                    # team-half" mechanic (e.g. wolfgd talent 30%/15%).
                    if not is_self:
                        mult = BUFF_ALLY_RATE_MULT.get(buff["buff_id"])
                        if mult is not None:
                            rate = rate * mult
                    all_effects.append((zone, rate, buff_src_key, is_self))
            # Combo grant buffs: release marker chooses the consuming attacker.
            # The combo zone is generic: normal skill 30%, ultimate 20%.
            # It is consumed at skill release (`affixes_skillimbue`), not at
            # first damage. The first qualifying hit only identifies skill kind.
            combo_src_key = self._karin_combo_window_source
            if (combo_src_key in roster
                    and now < self._karin_combo_window_end
                    and self._karin_combo_window_attacker == char):
                sk_low = skill.lower()
                is_ult = "ultimate" in sk_low
                is_ns = (not is_ult) and ("normal_skill" in sk_low)
                if is_ult or is_ns:
                    kind = "ultimate" if is_ult else "normal_skill"
                    rate = None
                    if self._karin_combo_window_kind is None:
                        rate = COMBO_RATE_ULTIMATE if is_ult else COMBO_RATE_NORMAL_SKILL
                        self._karin_combo_window_kind = kind
                        self._karin_combo_window_rate = rate
                    elif self._karin_combo_window_kind == kind:
                        # continuation of the same skill cast
                        rate = self._karin_combo_window_rate
                    if rate is not None:
                        is_self = (combo_src_key == char)
                        all_effects.append(("COMBO", rate, combo_src_key, is_self))

            # Phase 20 E-plan v2: inject attacker's gear/talent BASELINE as
            # self-buffs. Attributes.GetValue(attrType) returns final additive
            # total (gear+talent+active buffs). Subtract already-captured buff
            # rates for the same (zone, elem) to isolate the attacker's
            # personal baseline, then append as is_self=True. Ensures additive
            # zone math credits attacker for their gear % (e.g. 护手 24.9%
            # fire, 武器 25% fire) rather than dumping it all into allies'
            # extra pool.
            baseline_values, baseline_trace_t = _baseline_snapshot_values_and_time(baseline)
            if baseline_values:
                # Per-attrType captured sum — each buff's attrType_rates (from
                # its template) tells us which game attrType it feeds. This
                # avoids over-subtracting at the (zone, elem) level when two
                # different attrTypes map to the same zone (e.g. sword writes
                # to attrType 17 with rate 1.20, gear writes to attrType 51
                # with 0.5052 — both DMG_INC/all but independent stats).
                captured_by_at = {}  # attrType → sum rate (element-filtered)
                for uid, buff in self.active_buffs.items():
                    if not self._active_effects(buff, now=now):
                        continue
                    attr_rates = buff.get("attrType_rates") or {}
                    if not attr_rates:
                        continue  # only template-path buffs contribute here
                    buff_start_t = buff.get("t_start")
                    if (
                        baseline_trace_t is not None
                        and buff_start_t is not None
                        and buff_start_t > baseline_trace_t + 1e-6
                    ):
                        continue
                    # Mirror gate checks from the main buff loop.
                    bo = buff["owner"]
                    bok = self._resolve_player_entity(bo)
                    on_att, on_en = self._owner_applies_to_hit(
                        bo, bok, char_key_set, enemy, enemy_key
                    )
                    if not on_att and not on_en:
                        continue
                    if not _buff_applies_to_skill(buff["buff_id"], skill):
                        continue
                    src_k_here = self._resolve_effective_src_for_hit(
                        uid, buff, bok, roster)
                    if not src_k_here or not self._is_player(src_k_here):
                        continue
                    if src_k_here not in roster:
                        continue
                    is_self_here = (src_k_here == char)
                    ally_mult = 1.0
                    if not is_self_here:
                        am = BUFF_ALLY_RATE_MULT.get(buff["buff_id"])
                        if am is not None:
                            ally_mult = am
                    for at, r in attr_rates.items():
                        if not _attribute_type_applies_to_skill(at, skill):
                            continue
                        mp = _attribute_type_mapping(at)
                        if mp is None:
                            continue
                        _, e = mp
                        if not _effect_matches_damage_element(e, dmg_elem):
                            continue
                        captured_by_at[at] = captured_by_at.get(at, 0.0) + r * ally_mult
                # Inject gear baseline per attrType (GetValue - captured_for_that_attrType).
                for attr_ty, final_v in baseline_values.items():
                    # attrType 2 is final absolute attack, not an additive
                    # attack-percent rate. Keep runtime atk_up buffs mapped
                    # through ATTRIBUTE_TYPE_MAP, but never inject GetValue(2)
                    # as a self multiplier.
                    if int(attr_ty) == 2:
                        continue
                    mp = _attribute_type_mapping(attr_ty)
                    if mp is None:
                        continue
                    zone, elem = mp
                    if zone in NON_RDPS_ZONES:
                        continue
                    if not _attribute_type_applies_to_skill(attr_ty, skill):
                        continue
                    if not _effect_matches_damage_element(elem, dmg_elem):
                        continue
                    captured = captured_by_at.get(attr_ty, 0.0)
                    gear = final_v - captured
                    if gear > 0.001:
                        all_effects.append((zone, gear, char, True))

            zones = {}
            for zone, rate, src_k, is_self in all_effects:
                zones.setdefault(zone, []).append((rate, src_k, is_self))

            def _bucket_value(kind, idx, active_only=True):
                if not dpd:
                    return None
                arr = dpd.get("atkZones") if kind == "atk" else dpd.get("defZones")
                if not arr or idx < 0 or idx >= len(arr):
                    return None
                try:
                    val = float(arr[idx])
                except (TypeError, ValueError):
                    return None
                if active_only and val <= 1.0001:
                    return None
                return val

            # DPD is authoritative for the engine's final bucket values. Keep
            # any residual as attacker-self state so same-frame weapon stack
            # ordering quirks do not leak into external rDPS attribution.
            for zone_name, (bucket_kind, bucket_idx) in DPD_ZONE_BUCKETS.items():
                actual_bucket = _bucket_value(bucket_kind, bucket_idx, active_only=False)
                if actual_bucket is None:
                    continue
                modeled_bucket = 1.0 + sum(rate for rate, _src_k, _is_self in zones.get(zone_name, ()))
                residual = actual_bucket - modeled_bucket
                if abs(residual) > 0.003:
                    zones.setdefault(zone_name, []).append((residual, char, True))

            # Per-zone external multiplier: m_z = (1+S_z) / (1+S_z_self)
            # This is still our heuristic base. When DPD_RAW is present, we
            # calibrate the major attacker/defender-side buckets against the
            # engine's live zone arrays and only keep heuristic splitting
            # *within* those calibrated buckets.
            zone_mults = {}
            for zone, zone_effs in zones.items():
                s_all = sum(r for r, _, _ in zone_effs)
                s_self = sum(r for r, _, is_s in zone_effs if is_s)
                mz = (1 + s_all) / (1 + s_self)
                zone_mults[zone] = mz

            groups = []
            covered_zones = set()

            def _append_calibrated_group(name, zone_names, bucket_kind, bucket_idx):
                present = [z for z in zone_names if z in zones]
                actual_bucket = _bucket_value(bucket_kind, bucket_idx)
                if not present or actual_bucket is None:
                    return
                s_ext = sum(
                    rate
                    for zone_name in present
                    for rate, _src_k, is_self in zones[zone_name]
                    if not is_self
                )
                # DPD bucket carries hidden self-side state too (gear baseline,
                # stance/stateful skill modifiers, etc.). We therefore
                # calibrate only the *external* portion we already modeled:
                #   total = hidden_self + modeled_self + modeled_ext
                #   m_ext = total / (total - modeled_ext)
                # This preserves hidden self-side terms on the attacker instead
                # of stealing them into rDPS.
                actual_mult = actual_bucket / max(1.0, actual_bucket - s_ext)
                if actual_mult <= 1.0001:
                    return
                groups.append({
                    "name": name,
                    "zones": present,
                    "mult": actual_mult,
                    "calibrated": True,
                })
                covered_zones.update(present)

            for zone_name, (bucket_kind, bucket_idx) in DPD_ZONE_BUCKETS.items():
                _append_calibrated_group(
                    f"DPD_{bucket_kind.upper()}_Z{bucket_idx}",
                    {zone_name},
                    bucket_kind,
                    bucket_idx,
                )

            for zone in zones:
                if zone in covered_zones:
                    continue
                groups.append({
                    "name": zone,
                    "zones": [zone],
                    "mult": zone_mults.get(zone, 1.0),
                    "calibrated": False,
                })

            # rDPS uses only buff-derived / DPD-calibrated multipliers we've
            # reconstructed from active effects. Skill-inherent scaling stays
            # on the attacker via calc/atkScale, not here.
            m_ext = 1.0
            for group in groups:
                mg = group.get("mult", 1.0)
                if mg > 1.0:
                    m_ext *= mg

            attacker_share = hit_dmg / m_ext if m_ext > 0 else hit_dmg
            external_pool = hit_dmg - attacker_share

            # Log-ratio split across calibrated groups, then heuristic log-ratio
            # split within each group back down to logical zones, and finally
            # linear split among sources inside that logical zone.
            log_m_ext = sum(
                math.log(group["mult"])
                for group in groups
                if group.get("mult", 1.0) > 1.0
            )
            contributions = {}

            if log_m_ext > 0 and external_pool > 0:
                for group in groups:
                    mg = group.get("mult", 1.0)
                    if mg <= 1:
                        continue
                    group_share = external_pool * math.log(mg) / log_m_ext
                    zone_names = [z for z in group["zones"] if z in zones]
                    if not zone_names:
                        continue
                    if len(zone_names) == 1:
                        zone_shares = [(zone_names[0], group_share)]
                    else:
                        zone_log_parts = [
                            (z, math.log(zone_mults.get(z, 1.0)))
                            for z in zone_names
                            if zone_mults.get(z, 1.0) > 1.0
                        ]
                        zone_log_sum = sum(v for _z, v in zone_log_parts)
                        if zone_log_sum > 0:
                            zone_shares = [
                                (z, group_share * v / zone_log_sum)
                                for z, v in zone_log_parts
                            ]
                        else:
                            ext_rate_sum = sum(
                                rate
                                for z in zone_names
                                for rate, _src_k, is_self in zones[z]
                                if not is_self
                            )
                            if ext_rate_sum <= 0:
                                continue
                            zone_shares = []
                            for z in zone_names:
                                z_ext = sum(
                                    rate
                                    for rate, _src_k, is_self in zones[z]
                                    if not is_self
                                )
                                if z_ext > 0:
                                    zone_shares.append((z, group_share * z_ext / ext_rate_sum))
                    for zone_name, zone_share in zone_shares:
                        zone_effs = zones.get(zone_name, [])
                        s_ext = sum(r for r, _, is_s in zone_effs if not is_s)
                        if s_ext <= 0:
                            continue
                        for rate, src_k, is_self in zone_effs:
                            if is_self:
                                continue
                            credit = zone_share * rate / s_ext
                            contributions[src_k] = contributions.get(src_k, 0) + credit

            def _credit(key, amount):
                rs = self.rdps_stats.get(key)
                if not rs:
                    rs = CharRdpsStats(key)
                    self.rdps_stats[key] = rs
                rs.total_rd += amount
                if amount > rs.max_rd:
                    rs.max_rd = amount
                if rs.first_t == 0:
                    rs.first_t = now
                rs.last_t = now

            _credit(char, attacker_share)
            for src_key, credit in contributions.items():
                _credit(src_key, credit)
            self._recompute_display_rates_locked()

    def get_sorted(self):
        """Return DPS stats from parser_core only."""
        live_rows = self._live_core_rows()
        if live_rows is not None:
            stats, _rdps, _report = live_rows
            items = list(stats.values())
            items.sort(
                key=lambda x: (x.dps, x.total_dmg, x.max_hit, x.last_hit_t),
                reverse=True,
            )
            return items
        return []

    def get_sorted_rdps(self):
        live_rows = self._live_core_rows()
        if live_rows is not None:
            _stats, rdps, _report = live_rows
            items = list(rdps.values())
            items.sort(
                key=lambda x: (x.rdps, x.total_rd, x.max_rd, x.last_t),
                reverse=True,
            )
            return items
        return []

    def get_events_snapshot(self):
        with self.lock:
            events = list(self.hit_events)
            squad = self._effective_roster()
        if squad:
            return [e for e in events if e[1] in squad]
        return list(events)

    def stop(self):
        self._stop.set()


BTN_ACTIVE_FG = "#ffffff"
BTN_INACTIVE_FG = "#555555"
MINI_WINDOW_SIZE = 48
MINI_ICON_SIZE = 40
MINI_TRANSPARENT_BG = "#010203"


def _find_uploader_logo_path():
    candidates = [
        os.path.join(APP_DIR, "uploader", "EndfieldLogsUploader", "_internal", "app", "assets", "logo.png"),
        os.path.join(APP_DIR, "uploader", "EndfieldLogsUploader", "app", "assets", "logo.png"),
        os.path.join(APP_DIR, "_internal", "uploader", "EndfieldLogsUploader", "_internal", "app", "assets", "logo.png"),
        os.path.join(os.path.dirname(APP_DIR), "endfield-logs", "apps", "uploader", "app", "assets", "logo.png"),
        os.path.join(os.path.dirname(APP_DIR), "endfield-logs", "apps", "uploader", "dist", "EndfieldLogsUploader", "_internal", "app", "assets", "logo.png"),
        os.path.join(APP_DIR, "dist", "EndfieldLogsClient", "_internal", "uploader", "EndfieldLogsUploader", "_internal", "app", "assets", "logo.png"),
    ]
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    return ""

class OverlayApp:
    def __init__(self):
        self.tailer = LogTailer()
        self.mode = "DPS"
        self._mini_window = None
        self._mini_icon = None
        self._mini_icon_source = None
        self._mini_drag_x = 0
        self._mini_drag_y = 0
        self._mini_drag_start_x = 0
        self._mini_drag_start_y = 0
        self._mini_drag_moved = False

        self.root = tk.Tk()
        self._fatal_shutdown_scheduled = False
        self.root.title("Endfield DPS")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", ALPHA)
        self.root.configure(bg=BG)

        screen_w = self.root.winfo_screenwidth()
        self.win_w = 360
        self.win_h = 270
        x = screen_w - self.win_w - 20
        y = 80
        self.root.geometry(f"{self.win_w}x{self.win_h}+{x}+{y}")

        self._drag_x = 0
        self._drag_y = 0
        self.root.bind("<ButtonPress-1>", self._start_drag)
        self.root.bind("<B1-Motion>", self._on_drag)
        self.root.bind("<ButtonPress-3>", lambda e: self._quit())

        # header
        hdr = tk.Frame(self.root, bg=BG)
        hdr.pack(fill="x", padx=8, pady=(6, 2))
        tk.Label(hdr, text="Endfield DPS Meter", font=("Consolas", 9, "bold"),
                 fg=FG_HEAD, bg=BG, anchor="w").pack(side="left")
        min_btn = tk.Canvas(hdr, width=22, height=16, bg=BG,
                            highlightthickness=0, bd=0, cursor="hand2")
        min_line = min_btn.create_line(4, 8, 18, 8, fill="#888888", width=1)
        min_btn.bind("<Enter>", lambda e, c=min_btn, line=min_line: c.itemconfig(line, fill="#ffffff"))
        min_btn.bind("<Leave>", lambda e, c=min_btn, line=min_line: c.itemconfig(line, fill="#888888"))
        min_btn.bind("<ButtonPress-1>", self._on_minimize_button_press)
        min_btn.pack(side="right", padx=(4, 0))
        reset_btn = tk.Button(hdr, text="\u21bb", font=("Segoe UI Symbol", 12),
                              fg="#888888", bg=BG, bd=0, activebackground=BG,
                              activeforeground="#ffffff", cursor="hand2",
                              command=self._reset)
        reset_btn.pack(side="right")
        tk.Label(hdr, text="[右键关闭]", font=("Consolas", 8),
                 fg="#444444", bg=BG, anchor="e").pack(side="right", padx=(0, 6))

        meta = tk.Frame(self.root, bg=BG)
        meta.pack(fill="x", padx=8, pady=(0, 2))
        self.stage_label = tk.Label(
            meta,
            text="当前场地：-",
            font=("Microsoft YaHei", 9),
            fg=FG_NAME,
            bg=BG,
            anchor="w",
        )
        self.stage_label.pack(side="left")
        self.time_label = tk.Label(
            meta,
            text="战斗时间：--:--.---",
            font=("Consolas", 9),
            fg=FG_HEAD,
            bg=BG,
            anchor="e",
        )
        self.time_label.pack(side="right")

        # column headers
        self.col_cvs = tk.Canvas(self.root, height=20, bg=BG, highlightthickness=0, bd=0)
        self.col_cvs.pack(fill="x", padx=8, pady=(2, 0))
        self.col_texts = {}
        for x_pos, anchor, key, text in [
            (6, "w", "name", "名称"), (155, "e", "val1", "DPS"),
            (220, "e", "val5", "暴击"), (285, "e", "val3", "总伤害"),
            (335, "e", "val4", "占比"),
        ]:
            tid = self.col_cvs.create_text(x_pos, 10, anchor=anchor, text=text,
                                           font=("Microsoft YaHei", 9), fill=FG_HEAD)
            self.col_texts[key] = tid

        tk.Frame(self.root, bg="#333355", height=1).pack(fill="x", padx=8, pady=2)

        self.rows_frame = tk.Frame(self.root, bg=BG)
        self.rows_frame.pack(fill="both", expand=True, padx=8, pady=(0, 2))
        self.row_widgets = []

        # mode buttons
        btn_frame = tk.Frame(self.root, bg=BG)
        btn_frame.pack(fill="x", padx=8, pady=(0, 6))
        self.btn_dps = tk.Button(btn_frame, text="DPS", font=("Consolas", 9, "bold"),
                                 fg=BTN_ACTIVE_FG, bg="#333355", bd=0,
                                 activebackground="#444466", activeforeground="#ffffff",
                                 cursor="hand2", padx=12, pady=2,
                                 command=lambda: self._set_mode("DPS"))
        self.btn_dps.pack(side="left", padx=(0, 4))
        self.btn_rdps = tk.Button(btn_frame, text="RDPS", font=("Consolas", 9),
                                  fg=BTN_INACTIVE_FG, bg=BG, bd=0,
                                  activebackground="#333355", activeforeground="#ffffff",
                                  cursor="hand2", padx=12, pady=2,
                                  command=lambda: self._set_mode("RDPS"))
        self.btn_rdps.pack(side="left")
        self.rotation_controller = RotationGuideController(Path(TRACE_FILE))
        self.rotation_module = RotationOverlayModule(
            self.root, self.rotation_controller, on_meter_change=self._set_meter_enabled,
        )
        self.window_settings = None
        self.web_overlays = None
        self.overlay_control = None
        self.overlay_host_lease = None
        self._web_host_retry = None
        self._web_host_closing = False
        self._web_host_busy = False
        self._web_overlay_error = None
        self._start_web_overlay_host()
        point = self.rotation_module.settings["positions"].get("meter")
        if point:
            x = min(point[0], max(self.root.winfo_screenwidth() - self.win_w, 0))
            y = min(point[1], max(self.root.winfo_screenheight() - self.win_h, 0))
            self.root.geometry(f"+{x}+{y}")
        self.root.attributes("-alpha", self.rotation_module.settings.get("meterOpacity", self.rotation_module.settings["opacity"]))
        self.root.bind("<ButtonRelease-1>", self._finish_meter_drag)
        tk.Button(btn_frame, text="悬浮窗设置", font=("Microsoft YaHei", 8),
                  fg=FG_HEAD, bg=BG, bd=0, cursor="hand2",
                  activebackground="#333355", activeforeground="#ffffff",
                  command=self._open_settings).pack(side="right")
        if not self.rotation_module.settings["meterEnabled"]:
            self.root.after(0, self._minimize)

    def _start_web_overlay_host(self, attempt=0):
        self._web_host_retry = None
        if self._web_host_closing or self.web_overlays is not None:
            return
        window_root = Path(os.environ.get("ENDFIELD_OVERLAY_SETTINGS_ROOT") or
                           (Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "EndfieldPCAP" / "web-overlays"))
        lease = None
        try:
            lease = OverlayConfigLease(window_root)
            if not lease.held:
                self._web_host_busy = True
                delays = (200, 600, 1400)
                if attempt < len(delays):
                    self._web_host_retry = self.root.after(delays[attempt], lambda: self._start_web_overlay_host(attempt + 1))
                    return
                raise RuntimeError("悬浮窗配置正由其他客户端使用，已暂停自定义窗口启动。")
            # An offline settings save may have completed between loading the
            # base DPS module and acquiring this lease. Read its atomic result.
            self.rotation_module.settings = self.rotation_module.store.load()
            self.web_overlays = OverlayWindowManager(
                get_overlay_broker(), window_root / "windows.json", window_root / "profiles",
            )
            self.overlay_host_lease = lease
            lease = None
            self._web_host_busy = False
            self._web_overlay_error = None
            if attempt:
                settings = self.rotation_module.settings
                self.root.attributes("-alpha", settings.get("meterOpacity", settings["opacity"]))
                self._set_meter_enabled(settings["meterEnabled"])
            try:
                self.overlay_control = attach_control_server(self.web_overlays, self.rotation_module,
                                                             lease=self.overlay_host_lease)
            except Exception as exc:
                print(f"悬浮窗主页面控制暂不可用：{type(exc).__name__}", file=sys.stderr)
        except Exception as exc:
            self._web_overlay_error = str(exc)
            print(f"自定义悬浮窗暂不可用：{type(exc).__name__}: {exc}", file=sys.stderr)
        finally:
            if lease is not None:
                lease.close()

    def _set_meter_enabled(self, enabled):
        if enabled:
            self._restore_from_minimized()
        else:
            self._minimize()

    def _open_settings(self):
        if self.overlay_control is not None and self.overlay_control.request_settings():
            return
        if self._web_host_busy:
            messagebox.showinfo("悬浮窗设置", "悬浮窗配置正在其他客户端使用，请从该客户端进入设置。", parent=self.root)
            return
        if self.web_overlays is None:
            if self._web_overlay_error:
                messagebox.showwarning("自定义悬浮窗", "自定义窗口未启用：" + self._web_overlay_error, parent=self.root)
            self.rotation_module.open_settings()
            return
        if self.window_settings is None:
            self.window_settings = OverlaySettingsDialog(self.web_overlays, rotation_module=self.rotation_module)
        self.window_settings.refresh_list()
        self.window_settings.show()
        self.window_settings.raise_()
        self.window_settings.activateWindow()

    def _finish_meter_drag(self, _event):
        self.rotation_module.save_meter_position(self.root.winfo_x(), self.root.winfo_y())

    def _on_minimize_button_press(self, event):
        self._minimize()
        return "break"

    def _set_mode(self, mode):
        self.mode = mode
        if mode == "DPS":
            self.col_cvs.itemconfig(self.col_texts["val1"], text="DPS")
            self.col_cvs.itemconfig(self.col_texts["val3"], text="总伤害")
            self.btn_dps.config(fg=BTN_ACTIVE_FG, bg="#333355", font=("Consolas", 9, "bold"))
            self.btn_rdps.config(fg=BTN_INACTIVE_FG, bg=BG, font=("Consolas", 9))
        else:
            self.col_cvs.itemconfig(self.col_texts["val1"], text="rDPS")
            self.col_cvs.itemconfig(self.col_texts["val3"], text="rD")
            self.btn_rdps.config(fg=BTN_ACTIVE_FG, bg="#333355", font=("Consolas", 9, "bold"))
            self.btn_dps.config(fg=BTN_INACTIVE_FG, bg=BG, font=("Consolas", 9))

    def _start_drag(self, event):
        self._drag_x = event.x
        self._drag_y = event.y

    def _on_drag(self, event):
        x = self.root.winfo_x() + event.x - self._drag_x
        y = self.root.winfo_y() + event.y - self._drag_y
        self.root.geometry(f"+{x}+{y}")

    def _load_mini_icon(self):
        logo_path = _find_uploader_logo_path()
        if not logo_path:
            return None
        try:
            source = tk.PhotoImage(file=logo_path)
            scale = max(1, math.ceil(max(source.width(), source.height()) / MINI_ICON_SIZE))
            icon = source.subsample(scale, scale)
            self._mini_icon_source = source
            return icon
        except tk.TclError:
            return None

    def _minimize(self):
        if self._mini_window is not None and self._mini_window.winfo_exists():
            return
        self.root.update_idletasks()
        x = self.root.winfo_x() + max(self.win_w - MINI_WINDOW_SIZE, 0)
        y = self.root.winfo_y()
        self._mini_icon = self._load_mini_icon()

        mini = tk.Toplevel(self.root)
        mini.overrideredirect(True)
        mini.attributes("-topmost", True)
        mini.configure(bg=MINI_TRANSPARENT_BG)
        try:
            mini.attributes("-transparentcolor", MINI_TRANSPARENT_BG)
        except tk.TclError:
            pass
        mini.geometry(f"{MINI_WINDOW_SIZE}x{MINI_WINDOW_SIZE}+{x}+{y}")

        if self._mini_icon is not None:
            label = tk.Label(
                mini,
                image=self._mini_icon,
                bg=MINI_TRANSPARENT_BG,
                bd=0,
                highlightthickness=0,
                width=MINI_WINDOW_SIZE,
                height=MINI_WINDOW_SIZE,
                cursor="hand2",
            )
        else:
            label = tk.Label(
                mini,
                text="E",
                font=("Consolas", 18, "bold"),
                fg=FG_DPS,
                bg=MINI_TRANSPARENT_BG,
                bd=0,
                highlightthickness=0,
                width=2,
                height=2,
                cursor="hand2",
            )
        label.pack(fill="both", expand=True)

        for widget in (mini, label):
            widget.bind("<ButtonPress-1>", self._start_mini_drag)
            widget.bind("<B1-Motion>", self._on_mini_drag)
            widget.bind("<ButtonRelease-1>", self._finish_mini_click)
            widget.bind("<ButtonPress-3>", lambda e: self._quit())

        self._mini_window = mini
        self.root.withdraw()

    def _start_mini_drag(self, event):
        mini = self._mini_window
        if mini is None:
            return
        self._mini_drag_x = event.x
        self._mini_drag_y = event.y
        self._mini_drag_start_x = mini.winfo_x()
        self._mini_drag_start_y = mini.winfo_y()
        self._mini_drag_moved = False

    def _on_mini_drag(self, event):
        mini = self._mini_window
        if mini is None:
            return
        x = mini.winfo_x() + event.x - self._mini_drag_x
        y = mini.winfo_y() + event.y - self._mini_drag_y
        if abs(x - self._mini_drag_start_x) > 3 or abs(y - self._mini_drag_start_y) > 3:
            self._mini_drag_moved = True
        mini.geometry(f"+{x}+{y}")

    def _finish_mini_click(self, event):
        if not self._mini_drag_moved:
            self._restore_from_minimized()

    def _restore_from_minimized(self):
        if not self.rotation_module.settings["meterEnabled"]:
            self._open_settings()
            return
        mini = self._mini_window
        if mini is not None and mini.winfo_exists():
            x = mini.winfo_x() + MINI_WINDOW_SIZE - self.win_w
            y = mini.winfo_y()
            mini.destroy()
            self.root.geometry(f"{self.win_w}x{self.win_h}+{x}+{y}")
        self._mini_window = None
        self._mini_icon = None
        self._mini_icon_source = None
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)

    def _reset(self):
        with self.tailer.lock:
            self.tailer._gen += 1
            self.tailer.stats.clear()
            self.tailer.rdps_stats.clear()
            self.tailer.hit_events.clear()
            self.tailer.active_buffs.clear()
            self.tailer.entity_owner.clear()
            self.tailer.weapon_owner.clear()
            self.tailer._battle_start_t = 0.0
            self.tailer._battle_last_t = 0.0
            self.tailer.enemy_damage.clear()
            self.tailer.enemy_hits.clear()
            self.tailer.enemy_last_t.clear()
            self.tailer.current_enemy_key = None
            self.tailer._karin_combo_uids.clear()
            self.tailer._karin_combo_trigger_uids.clear()
            self.tailer._karin_combo_trigger_sources.clear()
            self.tailer._karin_combo_window_end = 0.0
            self.tailer._karin_combo_window_kind = None
            self.tailer._karin_combo_window_rate = 0.0
            self.tailer._karin_combo_window_attacker = None
            self.tailer._karin_combo_window_source = None
            self.tailer._pending_buff = None
            if self.tailer._live_core is not None:
                self.tailer._live_core.reset()

    def _quit(self):
        self.tailer.stop()
        self.rotation_controller.stop()
        self._web_host_closing = True
        if self._web_host_retry is not None:
            self.root.after_cancel(self._web_host_retry)
            self._web_host_retry = None
        if self.overlay_control is not None:
            self.overlay_control.close()
        if self.window_settings is not None:
            self.window_settings.close()
        if self.web_overlays is not None:
            self.web_overlays.close()
        if self.overlay_host_lease is not None:
            self.overlay_host_lease.close()
        self.rotation_module.close()
        self.root.destroy()

    def _export_battle_log(self):
        lines, meta = read_all_battle_log_export()
        if not lines or not meta:
            messagebox.showinfo("Endfield DPS", "当前没有可导出的战斗日志。", parent=self.root)
            return

        loadout_context_lines = build_export_loadout_context(preferred_lines=lines)
        loadout_summary_lines, loadout_rows = build_export_loadout_summary(preferred_lines=lines)
        export_lines = _prepend_export_context(lines, loadout_context_lines)
        default_name = (
            f"endfield_battles_{meta['first_hit_ts']}_to_{meta['last_hit_ts']}.log"
        )
        out_path = filedialog.asksaveasfilename(
            parent=self.root,
            title="导出当前 trace 全部战斗原始日志",
            defaultextension=".log",
            initialfile=default_name,
            filetypes=[
                ("Log files", "*.log"),
                ("Text files", "*.txt"),
                ("All files", "*.*"),
            ],
        )
        if not out_path:
            return

        try:
            write_embedded_raw_log(
                out_path,
                text="".join(export_lines),
                meta={
                    "first_hit_ts": meta.get("first_hit_ts"),
                    "last_hit_ts": meta.get("last_hit_ts"),
                    "battle_count": meta.get("battle_count"),
                    "hit_count": meta.get("hit_count"),
                    "line_count": len(export_lines),
                    "loadout": loadout_rows,
                },
                loadout_summary="\n".join(loadout_summary_lines),
            )
        except OSError as exc:
            messagebox.showerror(
                "Endfield DPS",
                f"导出失败：{exc}",
                parent=self.root,
            )
            return

        messagebox.showinfo(
            "Endfield DPS",
            (
                f"已导出 {meta['battle_count']} 场战斗、{meta['hit_count']} 条 hit 对应的原始日志：\n{out_path}\n\n"
                "当前队伍 loadout 摘要与完整性说明书已内嵌到该 `.log` 文件末尾。"
            ),
            parent=self.root,
        )

    def _ensure_rows(self, count):
        while len(self.row_widgets) < count:
            idx = len(self.row_widgets)
            cvs = tk.Canvas(self.rows_frame, height=24, bg=BG,
                            highlightthickness=0, bd=0)
            cvs.pack(fill="x", pady=1)
            bar_id = cvs.create_rectangle(0, 0, 0, 24, fill=BAR_COLORS[idx % len(BAR_COLORS)], outline="")
            name_id  = cvs.create_text(6,   12, anchor="w", text="", font=("Microsoft YaHei", 10), fill=FG_NAME)
            dps_id   = cvs.create_text(155, 12, anchor="e", text="", font=("Consolas", 10, "bold"), fill=FG_DPS)
            crit_id  = cvs.create_text(220, 12, anchor="e", text="", font=("Consolas", 10), fill=FG_CRIT)
            total_id = cvs.create_text(285, 12, anchor="e", text="", font=("Consolas", 10), fill=FG_TOTAL)
            pct_id   = cvs.create_text(335, 12, anchor="e", text="", font=("Consolas", 10), fill=FG_PCT)
            self.row_widgets.append((cvs, bar_id, name_id, dps_id, crit_id, total_id, pct_id))

    def _update(self):
        service_status = read_service_status()
        service_state = str((service_status or {}).get("state") or "")
        fatal_error = (service_status or {}).get("fatal_error")
        has_fatal_error = isinstance(fatal_error, dict)
        try:
            self.rotation_module.update(
                available=bool(STATUS_FILE and service_status is not None and service_state == "live" and not has_fatal_error),
                game_exe=str((service_status or {}).get("game_exe") or "Endfield.exe"),
            )
        except Exception as exc:
            # A guide rendering/geometry failure must never stop the meter's
            # clock update or its next scheduled refresh. Capture remains live.
            error = (type(exc).__name__, str(exc))
            if getattr(self, "_rotation_update_error", None) != error:
                print(f"排轴指导条更新失败，伤害统计继续：{error[0]}: {error[1]}", file=sys.stderr)
            self._rotation_update_error = error
            try:
                self.rotation_module._shown = False
                self.rotation_module.window.hide()
            except Exception:
                pass
        else:
            self._rotation_update_error = None
        if STATUS_FILE:
            self.tailer.set_live_clock_enabled(
                service_status is not None and service_state == "live"
            )
        live_rows = self.tailer._live_core_rows()
        if live_rows is not None:
            live_stats, live_rdps, report = live_rows
            battle = report.get("battle") or {}
            dungeon_name = format_overlay_dungeon_name(battle)
            if battle.get("rdps_available") is False:
                dungeon_name = f"{dungeon_name} / rDPS未确认"
            status = {
                "dungeonName": dungeon_name,
                "elapsed": max(float(battle.get("duration_ms") or 0) / 1000.0, 0.0),
            }
            if self.mode == "DPS":
                stats = list(live_stats.values())
                stats.sort(
                    key=lambda x: (x.dps, x.total_dmg, x.max_hit, x.last_hit_t),
                    reverse=True,
                )
            else:
                stats = list(live_rdps.values())
                stats.sort(
                    key=lambda x: (x.rdps, x.total_rd, x.max_rd, x.last_t),
                    reverse=True,
                )
            # Keep RDPS crit display on the exact same live DPS snapshot.
            crit_lookup = live_stats
            rdps_available = self.mode != "RDPS" or bool(battle.get("rdps_available", True))
        else:
            status = self.tailer.get_status_snapshot()
            if self.mode == "DPS":
                stats = self.tailer.get_sorted()
            else:
                stats = self.tailer.get_sorted_rdps()
            crit_lookup = self.tailer.stats
            rdps_available = self.mode != "RDPS" or self.tailer.live_rdps_available()
        stats = stats[:4]
        if has_fatal_error:
            self.stage_label.config(text=service_status_text(service_status))
            self.time_label.config(text=service_metrics_text(service_status))
            if not self._fatal_shutdown_scheduled:
                self._fatal_shutdown_scheduled = True
                # Give the overlay one render cycle to show the real cause,
                # then let the launcher surface its persistent error dialog.
                self.root.after(1500, self._quit)
        elif (
            service_status is not None
            and not stats
            and status.get("dungeonName") in {None, "", "-", "未知场地"}
        ):
            self.stage_label.config(text=service_status_text(service_status))
            self.time_label.config(text=service_metrics_text(service_status))
        else:
            stage_text = f"当前场地：{status['dungeonName']}"
            if STATUS_FILE and service_state != "live":
                if service_status is None:
                    state_note = "采集状态未更新"
                else:
                    state_note = service_status_text(service_status).removeprefix("状态：")
                stage_text = f"{stage_text} · {state_note}"
            self.stage_label.config(text=stage_text)
            self.time_label.config(text=f"战斗时间：{format_elapsed(status['elapsed'])}")
        _write_rows_debug(self.mode, status, stats)
        self._ensure_rows(len(stats))

        if self.mode == "DPS":
            team_total = sum(s.total_dmg for s in stats) or 1.0
            display_colors = assign_distinct_bar_colors([s.name for s in stats])
            for i, s in enumerate(stats):
                cvs, bar_id, name_id, dps_id, crit_id, total_id, pct_id = self.row_widgets[i]
                cvs.pack(fill="x", pady=1)
                cvs.update_idletasks()
                w = cvs.winfo_width()
                pct = s.total_dmg / team_total * 100.0
                bar_w = max(int(w * pct / 100.0), 4)
                color = display_colors.get(s.name, stable_bar_color(s.name))
                cvs.itemconfig(bar_id, fill=color)
                cvs.coords(bar_id, 0, 0, bar_w, 24)
                cvs.itemconfig(name_id, text=friendly_name(s.name))
                cvs.itemconfig(dps_id, text=f"{s.dps:.1f}")
                cvs.itemconfig(crit_id, text=crit_text(s.crit_n, s.crit_c))
                cvs.itemconfig(total_id, text=f"{s.total_dmg:.0f}" if s.total_dmg > 0 else "0")
                cvs.itemconfig(pct_id, text=f"{pct:.0f}%")
        else:
            team_total = sum(s.total_rd for s in stats) or 1.0
            display_colors = assign_distinct_bar_colors([s.name for s in stats])
            for i, s in enumerate(stats):
                cvs, bar_id, name_id, dps_id, crit_id, total_id, pct_id = self.row_widgets[i]
                cvs.pack(fill="x", pady=1)
                cvs.update_idletasks()
                w = cvs.winfo_width()
                pct = s.total_rd / team_total * 100.0
                bar_w = max(int(w * pct / 100.0), 4)
                color = display_colors.get(s.name, stable_bar_color(s.name))
                cvs.itemconfig(bar_id, fill=color)
                cvs.coords(bar_id, 0, 0, bar_w, 24)
                dps_stats = crit_lookup.get(s.name) if crit_lookup else None
                cn, cc = (dps_stats.crit_n, dps_stats.crit_c) if dps_stats else (0, 0)
                suffix = "" if rdps_available else " *"
                cvs.itemconfig(name_id, text=f"{friendly_name(s.name)}{suffix}")
                cvs.itemconfig(dps_id, text=f"{s.rdps:.1f}")
                cvs.itemconfig(crit_id, text=crit_text(cn, cc))
                cvs.itemconfig(total_id, text=f"{s.total_rd:.0f}" if s.total_rd > 0 else "0")
                cvs.itemconfig(pct_id, text=f"{pct:.0f}%")

        for i in range(len(stats), len(self.row_widgets)):
            self.row_widgets[i][0].pack_forget()

        # Base chrome now includes header, boss/time status row, column header,
        # separator and bottom mode buttons.
        needed = 128 + max(len(stats), 1) * 26
        if needed != self.win_h:
            self.win_h = needed
            x = self.root.winfo_x()
            y = self.root.winfo_y()
            self.root.geometry(f"{self.win_w}x{self.win_h}+{x}+{y}")

        self.root.after(POLL_MS, self._update)

    def run(self):
        self.tailer.start()
        if _rotation_client.ROTATION_GUIDANCE_ENABLED:
            self.rotation_controller.start()
        self.root.after(POLL_MS, self._update)
        self.root.mainloop()


def generate_timeline_html(data):
    data_json = json.dumps(data, ensure_ascii=False)
    return f'''<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>Endfield DPS 时间轴</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ background: #0d0d1a; color: #e0e0e0; font-family: "Microsoft YaHei", sans-serif; padding: 20px; }}
h1 {{ font-size: 18px; color: #8888aa; margin-bottom: 16px; }}
.summary {{ display: flex; gap: 24px; margin-bottom: 20px; flex-wrap: wrap; }}
.summary-card {{ background: #1a1a2e; border-radius: 8px; padding: 12px 18px; min-width: 140px; }}
.summary-card .label {{ font-size: 12px; color: #666; }}
.summary-card .value {{ font-size: 22px; font-weight: bold; color: #ffcc00; }}
.loadout-wrap {{ background: #1a1a2e; border-radius: 8px; padding: 14px 18px; margin-bottom: 18px; }}
.loadout-wrap ul {{ margin: 0; padding-left: 18px; }}
.loadout-wrap li {{ line-height: 1.6; margin: 4px 0; }}
.section-title {{ font-size: 14px; color: #8888aa; margin: 20px 0 8px; }}
.tl-wrap {{ background: #1a1a2e; border-radius: 8px; padding: 16px; overflow-x: auto; }}
canvas {{ display: block; }}
.legend {{ display: flex; gap: 16px; margin: 12px 0; flex-wrap: wrap; }}
.legend-item {{ display: flex; align-items: center; gap: 6px; font-size: 12px; }}
.legend-dot {{ width: 12px; height: 12px; border-radius: 2px; }}
</style>
</head>
<body>
<h1>Endfield DPS 时间轴</h1>
<div class="summary" id="summary"></div>
<div id="loadout-summary"></div>
<div class="legend" id="legend"></div>
<div class="section-title">技能时间轴</div>
<div class="tl-wrap"><canvas id="timeline"></canvas></div>
<div class="section-title">DPS 曲线 (每秒)</div>
<div class="tl-wrap"><canvas id="dps-chart"></canvas></div>
<script>
const DATA = {data_json};
const SKILL_COLORS = {{
  "战技": "#c8a415", "连携技": "#27ae60", "终结技": "#c0392b",
}};
const ATTACK_PALETTE = ["#3b6ec9","#4a80d4","#5b91de","#6da3e8","#80b4f2"];
function skillColor(st) {{
  if (SKILL_COLORS[st]) return SKILL_COLORS[st];
  const m = st.match(/普攻(\\d+)/);
  if (m) return ATTACK_PALETTE[Math.min(parseInt(m[1])-1, ATTACK_PALETTE.length-1)];
  return "#666";
}}
function skillLabel(st) {{
  const m = st.match(/普攻(\\d+)/);
  if (m) return "A" + m[1];
  return st;
}}

const events = DATA.events;
if (!events.length) {{ document.body.innerHTML = "<h1>无数据</h1>"; throw 0; }}
const maxT = events[events.length-1].t;
const chars = [...new Set(events.map(e => e.char))];
const charNames = {{}};
chars.forEach(c => {{ charNames[c] = events.find(e => e.char === c).charName; }});

// summary
const totalDmg = events.reduce((s,e) => s + e.dmg, 0);
const avgDps = maxT > 0.5 ? (totalDmg / maxT).toFixed(1) : "0";
document.getElementById("summary").innerHTML = `
  <div class="summary-card"><div class="label">战斗时长</div><div class="value">${{maxT.toFixed(1)}}s</div></div>
  <div class="summary-card"><div class="label">总伤害</div><div class="value">${{totalDmg.toLocaleString()}}</div></div>
  <div class="summary-card"><div class="label">平均 DPS</div><div class="value">${{avgDps}}</div></div>`;

const loadoutLines = Array.isArray(DATA.loadoutSummaryLines) ? DATA.loadoutSummaryLines.filter(Boolean) : [];
if (loadoutLines.length) {{
  const loadoutEl = document.getElementById("loadout-summary");
  const listHtml = loadoutLines.map(line => `<li>${{line}}</li>`).join("");
  loadoutEl.innerHTML = `
    <div class="section-title">当前队伍 Loadout</div>
    <div class="loadout-wrap"><ul>${{listHtml}}</ul></div>`;
}}

// legend
const stSet = [...new Set(events.map(e => e.skillType))];
stSet.sort((a,b) => {{
  const o = ["普攻1","普攻2","普攻3","普攻4","普攻5","战技","连携技","终结技"];
  return (o.indexOf(a) < 0 ? 99 : o.indexOf(a)) - (o.indexOf(b) < 0 ? 99 : o.indexOf(b));
}});
const legEl = document.getElementById("legend");
stSet.forEach(st => {{
  legEl.innerHTML += `<div class="legend-item"><div class="legend-dot" style="background:${{skillColor(st)}}"></div>${{skillLabel(st)}} ${{st}}</div>`;
}});

// ── group hits into skill casts ──
const CAST_GAP = 1.5;
const casts = [];
const curCast = {{}};
events.forEach(e => {{
  const key = e.char + "|" + e.skillType;
  const cc = curCast[key];
  if (cc && (e.t - cc.end) < CAST_GAP) {{
    cc.end = e.t;
    cc.hits.push(e);
    cc.dmg += e.dmg;
  }} else {{
    if (cc) casts.push(cc);
    curCast[key] = {{ char: e.char, skillType: e.skillType, start: e.t, end: e.t, hits: [e], dmg: e.dmg }};
  }}
}});
Object.values(curCast).forEach(c => casts.push(c));

// ── timeline canvas ──
const PPS = 40;
const ROW_H = 50;
const BLOCK_H = 28;
const LABEL_W = 80;
const PAD = 16;
const timeW = Math.max(maxT * PPS, 800);
const tlC = document.getElementById("timeline");
tlC.width = LABEL_W + timeW + PAD * 2;
tlC.height = chars.length * ROW_H + 28;
const ctx = tlC.getContext("2d");

function t2x(t) {{ return LABEL_W + PAD + (maxT > 0 ? (t / maxT) * timeW : 0); }}

// background
ctx.fillStyle = "#111122";
ctx.fillRect(0, 0, tlC.width, tlC.height);

// grid + time labels — drawn BEFORE row backgrounds so lines appear behind blocks
const tStep = 5;
const gridBottom = chars.length * ROW_H;

// row backgrounds + labels
chars.forEach((ch, ci) => {{
  const y = ci * ROW_H;
  ctx.fillStyle = ci % 2 === 0 ? "#141425" : "#181830";
  ctx.fillRect(LABEL_W + PAD, y, timeW, ROW_H);
  ctx.strokeStyle = "#222244";
  ctx.beginPath(); ctx.moveTo(LABEL_W + PAD, y + ROW_H); ctx.lineTo(LABEL_W + PAD + timeW, y + ROW_H); ctx.stroke();
  ctx.fillStyle = "#ccc";
  ctx.font = "13px Microsoft YaHei";
  ctx.textAlign = "right";
  ctx.fillText(charNames[ch], LABEL_W + 6, y + ROW_H / 2 + 5);
}});

// grid lines (drawn after row bg, before blocks — so lines are behind skill blocks)
ctx.font = "11px Consolas";
ctx.textAlign = "center";
for (let t = 0; t <= maxT; t += tStep) {{
  const x = t2x(t);
  ctx.strokeStyle = "#333355";
  ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, gridBottom); ctx.stroke();
  ctx.fillStyle = "#555";
  ctx.fillText(t.toFixed(0) + "s", x, gridBottom + 18);
}}

// draw casts as blocks
const MIN_W = 28;
casts.forEach(c => {{
  const ci = chars.indexOf(c.char);
  const y = ci * ROW_H + (ROW_H - BLOCK_H) / 2;
  let x1 = t2x(c.start);
  let x2 = t2x(c.end);
  let w = Math.max(x2 - x1, MIN_W);
  const color = skillColor(c.skillType);

  // block background
  ctx.fillStyle = color;
  ctx.globalAlpha = 0.25;
  ctx.fillRect(x1, y, w, BLOCK_H);
  ctx.globalAlpha = 1;

  // block border
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.5;
  ctx.strokeRect(x1, y, w, BLOCK_H);

  // hit markers (small diamonds)
  c.hits.forEach(h => {{
    const hx = t2x(h.t);
    const hy = y + BLOCK_H - 5;
    ctx.fillStyle = "#ff4444";
    ctx.beginPath();
    ctx.moveTo(hx, hy - 3);
    ctx.lineTo(hx + 3, hy);
    ctx.lineTo(hx, hy + 3);
    ctx.lineTo(hx - 3, hy);
    ctx.closePath();
    ctx.fill();
  }});

  // label
  const lbl = skillLabel(c.skillType);
  ctx.font = "bold 11px Microsoft YaHei";
  ctx.textAlign = "center";
  ctx.fillStyle = "#fff";
  const lblX = x1 + w / 2;
  const lblY = y + BLOCK_H / 2 + 4;
  if (w > 20) {{
    ctx.fillText(lbl, lblX, lblY);
  }}
}});

// ── DPS chart ──
const DPS_H = 200;
const dC = document.getElementById("dps-chart");
dC.width = LABEL_W + timeW + PAD * 2;
dC.height = DPS_H + 40;
const dCtx = dC.getContext("2d");
dCtx.fillStyle = "#111122";
dCtx.fillRect(0, 0, dC.width, dC.height);

const bucketSize = 1;
const nB = Math.ceil(maxT / bucketSize) + 1;
const cDps = {{}};
const cColors = ["#c0392b","#e67e22","#2ecc71","#3498db","#9b59b6","#1abc9c","#f39c12","#e84393"];
chars.forEach(ch => {{ cDps[ch] = new Float64Array(nB); }});
events.forEach(e => {{
  const bi = Math.floor(e.t / bucketSize);
  if (bi < nB) cDps[e.char][bi] += e.dmg;
}});

const cumDps = {{}};
chars.forEach(ch => {{
  cumDps[ch] = new Float64Array(nB);
  let cd = 0;
  for (let i = 0; i < nB; i++) {{ cd += cDps[ch][i]; cumDps[ch][i] = cd / ((i+1)*bucketSize); }}
}});
const teamD = new Float64Array(nB);
let tc = 0;
for (let i = 0; i < nB; i++) {{
  chars.forEach(ch => {{ tc += cDps[ch][i]; }});
  teamD[i] = tc / ((i+1)*bucketSize);
}}
const mD = Math.max(...teamD, 1);

// grid
dCtx.font = "11px Consolas";
dCtx.textAlign = "center";
for (let t = 0; t <= maxT; t += tStep) {{
  const x = t2x(t);
  dCtx.fillStyle = "#444";
  dCtx.fillText(t.toFixed(0) + "s", x, DPS_H + 28);
  dCtx.strokeStyle = "#1a1a33";
  dCtx.beginPath(); dCtx.moveTo(x, 0); dCtx.lineTo(x, DPS_H); dCtx.stroke();
}}
dCtx.textAlign = "right";
dCtx.fillStyle = "#555";
dCtx.font = "10px Consolas";
for (let i = 0; i <= 4; i++) {{
  const v = mD * i / 4, y = DPS_H - (i/4)*DPS_H;
  dCtx.fillText(v >= 1000 ? (v/1000).toFixed(1)+"k" : v.toFixed(0), LABEL_W + PAD - 6, y + 4);
  dCtx.strokeStyle = "#1a1a33";
  dCtx.beginPath(); dCtx.moveTo(LABEL_W+PAD, y); dCtx.lineTo(LABEL_W+PAD+timeW, y); dCtx.stroke();
}}

function drawLine(arr, color, width, alpha) {{
  dCtx.strokeStyle = color; dCtx.lineWidth = width; dCtx.globalAlpha = alpha;
  dCtx.beginPath();
  for (let i = 0; i < nB; i++) {{
    const x = t2x((i+0.5)*bucketSize), y = DPS_H - (arr[i]/mD)*DPS_H;
    i === 0 ? dCtx.moveTo(x, y) : dCtx.lineTo(x, y);
  }}
  dCtx.stroke(); dCtx.globalAlpha = 1;
}}
chars.forEach((ch, ci) => drawLine(cumDps[ch], cColors[ci%cColors.length], 1.5, 0.7));
drawLine(teamD, "#ffffff", 2, 1);

// DPS legend
dCtx.font = "12px Microsoft YaHei";
dCtx.textAlign = "left";
let lx = LABEL_W + PAD;
dCtx.fillStyle = "#fff"; dCtx.fillText("● 全队", lx, DPS_H + 14); lx += 70;
chars.forEach((ch, ci) => {{
  dCtx.fillStyle = cColors[ci%cColors.length];
  dCtx.fillText("● " + charNames[ch], lx, DPS_H + 14);
  lx += Math.max(dCtx.measureText("● " + charNames[ch]).width + 16, 70);
}});
</script>
</body>
</html>'''


if __name__ == "__main__":
    app = OverlayApp()
    app.run()
