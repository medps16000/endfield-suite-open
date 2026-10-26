from __future__ import annotations

import json
import math
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from .runtime_paths import bundle_root


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None


def _catalog_roots(base: Path, kind: str) -> tuple[Path, ...]:
    """Return the legacy AKEData and current local-table item roots.

    AKEData is still the richest source for older items, while the local table
    is the authoritative source immediately after a game update.  Keeping the
    two sources here lets a newly added weapon/equipment piece be displayed
    before the next AKEData mirror is available.
    """
    roots: list[Path] = [base / "data" / "akedata" / kind / "items"]
    local_root = base / "data" / "local_tables" / kind / "items"
    if local_root not in roots:
        roots.append(local_root)
    return tuple(roots)


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _value_options(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _select_option(options: list[Any], level: Any) -> Any:
    if not options or level is None or isinstance(level, bool) or not isinstance(level, (int, str)):
        return None
    try:
        level_int = int(level)
    except (TypeError, ValueError):
        return None
    if 0 <= level_int < len(options):
        return options[level_int]
    return None


def _sub_key_order(raw_key: Any) -> tuple[int, str]:
    match = re.search(r"(\d+)", str(raw_key))
    return (int(match.group(1)) if match else 99, str(raw_key))


def _build_affix_rows(item: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    main_attr = item.get("主词条")
    if isinstance(main_attr, dict):
        rows.append(
            {
                "kind": "main",
                "index": 0,
                "desc": _clean_text(main_attr.get("desc")),
                "value": main_attr.get("value"),
                "value_options": _value_options(main_attr.get("value")),
            }
        )
    sub_attrs = item.get("副词条")
    if isinstance(sub_attrs, dict):
        for index, key in enumerate(sorted(sub_attrs.keys(), key=_sub_key_order)):
            attr = sub_attrs.get(key)
            if not isinstance(attr, dict):
                continue
            rows.append(
                {
                    "kind": "sub",
                    "index": index,
                    "desc": _clean_text(attr.get("desc")),
                    "value": attr.get("value"),
                    "value_options": _value_options(attr.get("value")),
                }
            )
    return rows


@lru_cache(maxsize=1)
def weapon_catalog(root: str | None = None) -> dict[str, dict[str, Any]]:
    base = Path(root) if root else bundle_root()
    result: dict[str, dict[str, Any]] = {}
    for items_dir in _catalog_roots(base, "weapon"):
        if not items_dir.exists():
            continue
        for path in sorted(items_dir.glob("*.json")):
            payload = _read_json(path)
            if not isinstance(payload, dict):
                continue
            weapon_id = str(payload.get("weaponId") or path.stem)
            raw = payload.get("raw") if isinstance(payload.get("raw"), dict) else {}
            current = result.setdefault(weapon_id, {"weapon_id": weapon_id})
            current.update(
                {
                    "name": _clean_text(payload.get("title") or payload.get("name") or weapon_id),
                    "rarity": payload.get("rarity"),
                    "base_atk": payload.get("baseAtk") if isinstance(payload.get("baseAtk"), list) else current.get("base_atk", []),
                    "skilllist": payload.get("skilllist") if isinstance(payload.get("skilllist"), list) else current.get("skilllist", []),
                    "max_lv": raw.get("maxLv") if raw.get("maxLv") is not None else current.get("max_lv"),
                    "weapon_skill_ids": raw.get("weaponSkillList") if isinstance(raw.get("weaponSkillList"), list) else current.get("weapon_skill_ids", []),
                }
            )
    return result


@lru_cache(maxsize=1)
def equip_piece_catalog(root: str | None = None) -> dict[str, dict[str, Any]]:
    base = Path(root) if root else bundle_root()
    result: dict[str, dict[str, Any]] = {}
    for items_dir in _catalog_roots(base, "equip"):
        if not items_dir.exists():
            continue
        for path in sorted(items_dir.glob("*.json")):
            payload = _read_json(path)
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
                # Local-table entries are newer and should replace an older
                # AKEData copy; older AKEData remains a fallback for pieces not
                # present in the latest table export.
                result[item_key] = {
                    "item_id": item_key,
                    "name": _clean_text(item.get("name")) or item_key,
                    "suit_id": suit_id,
                    "suit_name": suit_name,
                    "part": _clean_text(item.get("部位")),
                    "affixes": _build_affix_rows(item),
                }
    # The local-table generator stores the current piece records in one
    # pieces.json file rather than repeating them inside each suit file.  Add
    # those records after AKEData so new suits/pieces are immediately usable.
    pieces_path = base / "data" / "local_tables" / "equip" / "pieces.json"
    pieces_payload = _read_json(pieces_path)
    if isinstance(pieces_payload, dict) and isinstance(pieces_payload.get("entries"), list):
        suit_names: dict[str, str] = {}
        for suit_path in (base / "data" / "local_tables" / "equip" / "items").glob("*.json"):
            suit_payload = _read_json(suit_path)
            if isinstance(suit_payload, dict):
                suit_id = str(suit_payload.get("suitID") or suit_path.stem)
                suit_names[suit_id] = _clean_text(suit_payload.get("name") or suit_payload.get("displayName"))
        for item in pieces_payload["entries"]:
            if not isinstance(item, dict):
                continue
            item_key = str(item.get("itemId") or item.get("id") or "")
            if not item_key:
                continue
            slot_parts = pieces_payload.get('slotPartTypes')
            valid_slots = [slot for slot, part in enumerate(slot_parts) if part == item.get('partType')] if (
                isinstance(slot_parts, list) and all(type(part) is int for part in slot_parts)
                and type(item.get('partType')) is int
                and re.fullmatch(r'[0-9a-f]{64}', str(pieces_payload.get('sourceGlobalConstSha256') or ''))) else []
            if item_key in result:
                # Preserve the richer AKEData affix descriptions for existing
                # pieces; local tables are a fallback for newly added pieces.
                result[item_key]['valid_slots'] = valid_slots
                continue
            affixes: list[dict[str, Any]] = []
            main = item.get("displayBaseAttrModifier")
            if isinstance(main, dict):
                attr_type = main.get("attrType")
                affixes.append({
                    "kind": "main", "index": 0,
                    "desc": f"attribute_type_{attr_type}",
                    "value": main.get("attrValue"),
                    "value_options": [main.get("attrValue")],
                })
            for index, raw in enumerate(item.get("displayAttrModifiers") or []):
                if not isinstance(raw, dict):
                    continue
                attr_type = raw.get("attrType")
                values = [raw.get("attrValue")] + list(raw.get("enhancedAttrValues") or [])
                affixes.append({
                    "kind": "sub", "index": index,
                    "desc": f"attribute_type_{attr_type}",
                    "value": values[0] if values else None,
                    "value_options": values,
                })
            suit_id = str(item.get("suitID") or "suit_none")
            result[item_key] = {
                "item_id": item_key,
                "name": _clean_text(item.get("name")) or item_key,
                "suit_id": suit_id,
                "suit_name": suit_names.get(suit_id, suit_id),
                "part": _clean_text(item.get("partName")),
                "affixes": affixes,
                "valid_slots": valid_slots,
            }
    return result


@lru_cache(maxsize=1)
def attribute_type_catalog(root: str | None = None) -> dict[int, dict[str, Any]]:
    base = Path(root) if root else bundle_root()
    items_dir = base / "data" / "akedata" / "attribute_type" / "items"
    result: dict[int, dict[str, Any]] = {}
    if not items_dir.exists():
        return result
    for path in sorted(items_dir.glob("*.json")):
        payload = _read_json(path)
        if not isinstance(payload, dict):
            continue
        try:
            attr_type = int(payload.get("attributeType") if payload.get("attributeType") is not None else path.stem)
        except (TypeError, ValueError):
            continue
        result[attr_type] = {
            "attribute_type": attr_type,
            "name": _clean_text(payload.get("name")) or f"AttributeType{attr_type}",
            "slug": _clean_text(payload.get("slug")) or f"attribute_type_{attr_type}",
            "default_value": payload.get("defaultValue"),
        }
    return result


def weapon_base_atk(weapon_template: str, weapon_level: int) -> int | float | None:
    values = weapon_catalog().get(str(weapon_template or ""), {}).get("base_atk")
    if not isinstance(values, list) or not values:
        return None
    if weapon_level <= 0:
        return None
    if weapon_level > len(values):
        return None
    value = values[weapon_level - 1]
    return value if isinstance(value, (int, float)) else None


def weapon_base_atk_bounds(weapon_template: str) -> dict[str, int | float | None]:
    values = weapon_catalog().get(str(weapon_template or ""), {}).get("base_atk")
    if not isinstance(values, list) or not values:
        return {"lv1": None, "max": None}
    first = values[0]
    last = values[-1]
    return {
        "lv1": first if isinstance(first, (int, float)) else None,
        "max": last if isinstance(last, (int, float)) else None,
    }


def selected_weapon_blackboard(weapon_template: str, refine: int) -> list[dict[str, Any]]:
    meta = weapon_catalog().get(str(weapon_template or ""), {})
    rows: list[dict[str, Any]] = []
    for skill in meta.get("skilllist") or []:
        if not isinstance(skill, dict):
            continue
        skill_name = _clean_text(skill.get("skillName")) or "weapon_skill"
        for raw in skill.get("blackboard") or []:
            if not isinstance(raw, dict):
                continue
            key = _clean_text(raw.get("key"))
            values = raw.get("value")
            selected = _select_option(values if isinstance(values, list) else [values], refine)
            rows.append(
                {
                    "skill_name": skill_name,
                    "key": key,
                    "refine": refine,
                    "value": selected,
                    "values": values,
                }
            )
    return rows








def _coerce_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@lru_cache(maxsize=1)
def _weapon_refine_catalog() -> dict[str, Any]:
    data = _read_json(bundle_root() / 'data' / 'local_tables' / 'weapon' / 'refine_catalog.json')
    return data.get('entries', {}) if isinstance(data, dict) else {}


def infer_verified_weapon_refine_from_levels(weapon_template: str, source_skills: Any, *, gem_terms: Any = None) -> int | None:
    """Use only the weapon's third (potential) skill, never attribute skills."""
    entry = _weapon_refine_catalog().get(weapon_template, {})
    main_id = str(entry.get('mainSkillId') or '')
    accepted_ids = {main_id, str(entry.get('mainSkillNumId') or '')} - {''}
    gem_cost = None
    if gem_terms is not None:
        # Callers must bind this complete gem snapshot to the equipped weapon.
        if not isinstance(gem_terms, list) or not gem_terms:
            return None
        terms = {}
        for term in gem_terms:
            if (not isinstance(term, dict) or type(term.get('term_num_id')) is not int
                    or type(term.get('cost')) is not int or term['term_num_id'] <= 0
                    or not 0 <= term['cost'] <= 6 or term['term_num_id'] in terms):
                return None
            terms[term['term_num_id']] = term['cost']
        term_id = entry.get('mainSkillGemTermNumId')
        if type(term_id) is not int or not entry.get('mainSkillBounds') or not entry.get('refineBonusBounds'):
            return None
        gem_cost = terms.get(term_id, 0)
    values = set()
    for skill in source_skills or []:
        if not isinstance(skill, dict):
            continue
        ids = {str(skill[key]) for key in ('skill_str_id', 'skill_int_id', 'skill_id') if skill.get(key) not in (None, '', 0)}
        if not ids.intersection(accepted_ids):
            continue
        if not ids.issubset(accepted_ids):
            return None
        level = skill.get('level')
        if isinstance(level, bool) or not str(level).isdigit():
            return None
        value = entry.get('skillLevelToRefine', {}).get(str(int(level)))
        if gem_cost is not None:
            if int(level) not in entry.get('supportedSkillLevels', []):
                return None
            # WeaponUtil: add refinement to both bounds, then the matching
            # gem's cost to the lower bound, capped at the upper bound.
            candidates = {int(refine) for refine, bonus in entry['refineBonusBounds'].items()
                          for bound in entry['mainSkillBounds']
                          if min(bound['lowerBound'] + bonus['lowerBound'] + gem_cost,
                                 bound['upperBound'] + bonus['upperBound']) == int(level)}
            value = next(iter(candidates)) if len(candidates) == 1 else None
        if value is None:
            return None
        values.add(int(value))
    return next(iter(values)) if len(values) == 1 else None


def infer_weapon_refine_from_source_skills(
    weapon_template: str,
    source_skills: list[dict[str, Any]] | None,
    *, gem_terms: Any = None,
) -> int | None:
    # Only the explicitly identified third weapon potential skill is valid
    # evidence. Gem arithmetic additionally requires the explicit bound gem
    # identity and the official skill tag/bounds; blackboard similarity is not evidence.
    return infer_verified_weapon_refine_from_levels(weapon_template, source_skills, gem_terms=gem_terms)


def format_weapon_refine_stats(weapon_template: str, refine: int) -> str:
    parts: list[str] = []
    for row in selected_weapon_blackboard(weapon_template, refine):
        key = _clean_text(row.get("key"))
        if not key:
            continue
        value = row.get("value")
        if isinstance(value, float):
            value_text = f"{value:.6g}"
        else:
            value_text = str(value)
        skill_name = _clean_text(row.get("skill_name"))
        parts.append(f"{skill_name}.{key}={value_text}@refine{refine}")
    return ";".join(parts)


def selected_equip_affixes(item_id: str, enhance: dict[int, int] | dict[Any, Any] | None) -> list[dict[str, Any]]:
    meta = equip_piece_catalog().get(str(item_id or ""), {})
    levels: dict[int, int | None] = {}
    for key, value in dict(enhance or {}).items():
        try:
            if isinstance(key, bool) or not isinstance(key, (int, str)):
                continue
            index = int(key)
            level = int(value) if isinstance(value, (int, str)) and not isinstance(value, bool) else None
            if index in levels and levels[index] != level:
                levels[index] = None
            else:
                levels[index] = level
        except (TypeError, ValueError):
            continue
    rows: list[dict[str, Any]] = []
    for raw in meta.get("affixes") or []:
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        if row.get("kind") == "sub":
            index = int(row.get("index") or 0)
            level = levels.get(index + 1)
            row["level"] = level
            selected = _select_option(row.get("value_options") or [], level)
            row["value"] = selected
            row["selected_value"] = selected
        rows.append(row)
    return rows


def format_equip_stats(item_id: str, enhance: dict[int, int] | dict[Any, Any] | None) -> str:
    parts: list[str] = []
    for row in selected_equip_affixes(item_id, enhance):
        desc = _clean_text(row.get("desc"))
        if not desc:
            continue
        value = row.get("value")
        if value is None:
            value_text = 'unknown'
        elif isinstance(value, float):
            value_text = f"{value:.6g}"
        else:
            value_text = str(value)
        kind = "main" if row.get("kind") == "main" else f"sub{int(row.get('index') or 0) + 1}"
        level = row.get("level")
        level_text = f"@{level}" if level is not None else ""
        parts.append(f"{kind}:{desc}={value_text}{level_text}")
    return ";".join(parts)


def normalize_gem_payload(gem: Any, inst_id: int, template_string: str = "") -> dict[str, Any]:
    terms: list[dict[str, int]] = []
    for term in getattr(gem, "terms", []) or []:
        terms.append(
            {
                "term_num_id": int(getattr(term, "term_num_id", 0) or 0),
                "cost": int(getattr(term, "cost", 0) or 0),
            }
        )
    gem_id = int(getattr(gem, "gem_id", 0) or 0) or int(inst_id or 0)
    return {
        "inst_id": int(inst_id or 0),
        "gem_id": gem_id,
        "template_id": int(getattr(gem, "template_id", 0) or 0),
        "template_string": template_string,
        "total_cost": int(getattr(gem, "total_cost", 0) or 0),
        "terms": terms,
        "weapon_id": int(getattr(gem, "weapon_id", 0) or 0),
        "domain_id": int(getattr(gem, "domain_id", 0) or 0),
    }


def format_gem_terms(gem_payload: dict[str, Any] | None) -> str:
    if not gem_payload:
        return ""
    attrs = attribute_type_catalog()
    terms = gem_payload.get("terms")
    if not isinstance(terms, list):
        return ""
    parts: list[str] = []
    for term in terms:
        if not isinstance(term, dict):
            continue
        try:
            term_id = int(term.get("term_num_id") or 0)
            cost = int(term.get("cost") or 0)
        except (TypeError, ValueError):
            continue
        attr = attrs.get(term_id, {})
        label = attr.get("slug") or attr.get("name") or f"attr_{term_id}"
        parts.append(f"{term_id}:{label}@cost{cost}")
    return ",".join(parts)
