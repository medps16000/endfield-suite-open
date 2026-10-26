from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .runtime_paths import bundle_root


@dataclass(slots=True)
class PacketResolveContext:
    owner_hint: str = ""
    owner: str = ""
    src: str = ""
    active_suits_by_char: dict[str, set[str]] | None = None
    active_weapons_by_char: dict[str, str] | None = None
    blackboard_values: dict[str, float] | None = None
    blackboard_keys: set[str] | None = None


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_legacy_maps(root: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    packet_root = root / "data" / "packet_semantics"

    def load_map(name: str) -> dict[str, Any]:
        path = packet_root / name
        if not path.exists():
            return {}
        try:
            payload = _read_json(path)
        except (OSError, json.JSONDecodeError):
            return {}
        mappings = payload.get("mappings")
        if isinstance(mappings, dict):
            return mappings
        characters = payload.get("characters")
        if isinstance(characters, dict):
            return characters
        return {}

    return load_map("skill_numeric_map.json"), load_map("buff_numeric_map.json"), {}


def _load_static_buff_numeric_map(root: Path, table_name: str = "buff_id") -> dict[str, str]:
    path = root / "data" / "local_tables" / "NumIdStrTable.json"
    if not path.exists():
        return {}
    try:
        payload = _read_json(path)
    except (OSError, json.JSONDecodeError):
        return {}
    buff_table = payload.get(table_name) if isinstance(payload, dict) else None
    mapping = buff_table.get("dic") if isinstance(buff_table, dict) else None
    if not isinstance(mapping, dict):
        return {}
    return {
        str(raw_id): str(canonical_id)
        for raw_id, canonical_id in mapping.items()
        if str(raw_id) and isinstance(canonical_id, str) and canonical_id
    }


def _char_prefix(template: str) -> str:
    parts = str(template or "").split("_")
    if len(parts) >= 3 and parts[0] == "chr" and parts[1].isdigit():
        return "_".join(parts[:3])
    return ""


def _character_family_from_id(text: str) -> str:
    match = re.search(r"(chr_\d{4}_[a-z0-9]+)", str(text or ""))
    return match.group(1) if match else ""


def _is_unknown_actor_name(value: str) -> bool:
    text = str(value or "").strip()
    return not text or text == "?" or text.startswith("id_")


class PacketResolver:
    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or bundle_root()).resolve()
        self.skill_by_owner_numeric: dict[str, str] = {}
        self.skill_by_numeric_unique: dict[str, str] = {}
        self.buff_by_numeric: dict[str, dict[str, Any]] = {}
        self.static_buff_by_numeric: dict[str, str] = {}
        self.static_skill_by_numeric = _load_static_buff_numeric_map(self.root, "skill_id")
        self.buff_content_by_id: dict[str, dict[str, Any]] = {}
        self.skill_content_by_id: dict[str, dict[str, Any]] = {}
        self.attribute_type_content_by_id: dict[str, dict[str, Any]] = {}
        self.attribute_type_by_numeric: dict[str, dict[str, Any]] = {}
        self.semantic_indexes: dict[str, Any] = {}
        self.bundle_path = self.root / "data" / "packet_semantics" / "packet_resolver_bundle.json"
        self._load()

    def _load(self) -> None:
        if self.bundle_path.exists():
            try:
                payload = _read_json(self.bundle_path)
            except (OSError, json.JSONDecodeError):
                payload = {}
            skills = payload.get("skills") if isinstance(payload, dict) else {}
            buffs = payload.get("buffs") if isinstance(payload, dict) else {}
            content = payload.get("content") if isinstance(payload, dict) else {}
            self.skill_by_owner_numeric = dict(skills.get("by_owner_numeric") or {})
            self.skill_by_numeric_unique = dict(skills.get("by_numeric_unique") or {})
            self.buff_by_numeric = dict(buffs.get("by_numeric") or {})
            if isinstance(content, dict):
                self.buff_content_by_id = dict(content.get("buffs") or {})
                self.skill_content_by_id = dict(content.get("skills") or {})
                self.attribute_type_content_by_id = dict(content.get("attribute_types") or {})
            indexes = payload.get("indexes") if isinstance(payload, dict) else {}
            if isinstance(indexes, dict):
                self.semantic_indexes = indexes
                attr_index = indexes.get("attribute_types")
                if isinstance(attr_index, dict):
                    self.attribute_type_by_numeric = dict(attr_index.get("by_attribute_type") or {})
            # The generated resolver bundle can lag behind a same-day resource
            # sync. Overlay the curated packet map and retain the authoritative
            # Buff NumIdStrTable as the final numeric identity source.
            _, legacy_buff_map, _ = _load_legacy_maps(self.root)
            self.buff_by_numeric.update(
                {
                    str(raw_key): value
                    for raw_key, value in legacy_buff_map.items()
                    if isinstance(value, dict)
                }
            )
            self.static_buff_by_numeric = _load_static_buff_numeric_map(self.root)
            return

        skill_numeric_map, buff_numeric_map, _ = _load_legacy_maps(self.root)

        by_int: dict[str, set[str]] = {}
        by_owner_numeric: dict[str, str] = {}
        for raw_key, mapping in skill_numeric_map.items():
            if not isinstance(raw_key, str) or not isinstance(mapping, dict):
                continue
            canonical = str(mapping.get("canonical_skill_id") or "")
            if not canonical:
                continue
            by_owner_numeric[raw_key] = canonical
            match = re.search(r"_skill_(\d+)$", raw_key)
            if match is not None:
                by_int.setdefault(match.group(1), set()).add(canonical)
        self.skill_by_owner_numeric = by_owner_numeric
        self.skill_by_numeric_unique = {
            int_id: next(iter(values))
            for int_id, values in by_int.items()
            if len(values) == 1
        }

        self.buff_by_numeric = {
            str(raw_key): mapping
            for raw_key, mapping in buff_numeric_map.items()
            if isinstance(mapping, dict)
        }
        self.static_buff_by_numeric = _load_static_buff_numeric_map(self.root)

    @staticmethod
    def _packet_buff_mapping_applies(
        mapping: dict[str, Any],
        owner: str,
        src: str,
        active_suits_by_char: dict[str, set[str]] | None,
        active_weapons_by_char: dict[str, str] | None,
    ) -> bool:
        unknown_owner = _is_unknown_actor_name(owner)
        unknown_src = _is_unknown_actor_name(src)
        character_id = str(mapping.get("character_id") or "")
        if character_id:
            if character_id not in {owner, src}:
                if not (unknown_owner and unknown_src):
                    return False
                active_chars = set((active_weapons_by_char or {}).keys()) | set((active_suits_by_char or {}).keys())
                if character_id not in active_chars:
                    return False
        suit_id = str(mapping.get("suit_id") or "")
        if suit_id:
            suit_holders = {
                char
                for char, suits in (active_suits_by_char or {}).items()
                if suit_id in suits
            }
            if unknown_owner and unknown_src:
                if len(suit_holders) != 1:
                    return False
            elif not any(suit_id in (active_suits_by_char or {}).get(char, set()) for char in (owner, src)):
                return False
        weapon_id = str(mapping.get("weapon_id") or "")
        if weapon_id:
            weapon_holders = {
                char
                for char, active_weapon in (active_weapons_by_char or {}).items()
                if active_weapon == weapon_id
            }
            if unknown_owner and unknown_src:
                if len(weapon_holders) != 1:
                    return False
            else:
                weapon_candidates = {
                    (active_weapons_by_char or {}).get(owner),
                    (active_weapons_by_char or {}).get(src),
                }
                if weapon_id not in weapon_candidates:
                    return False
        return True

    def resolve_skill(self, str_id: Any = None, int_id: Any = None, *, context: PacketResolveContext | None = None) -> str | None:
        """Resolve only explicit string IDs or unique numeric table entries."""
        if isinstance(str_id, str) and str_id:
            return str_id
        if int_id is None or isinstance(int_id, bool) or not re.fullmatch(r"\d+", str(int_id)):
            return None
        number = str(int(int_id))
        return self.static_skill_by_numeric.get(number)

    def resolve_buff(self, str_id: Any = None, int_id: Any = None, *, context: PacketResolveContext | None = None) -> str:
        if isinstance(str_id, str) and str_id:
            return str_id
        if int_id is None or isinstance(int_id, bool) or not re.fullmatch(r"\d+", str(int_id)):
            return "unknown_buff"
        number = str(int(int_id))
        return self.static_buff_by_numeric.get(number, number)

    def buff_content(self, canonical_id: str | None) -> dict[str, Any]:
        if not canonical_id:
            return {}
        payload = self.buff_content_by_id.get(canonical_id)
        return payload if isinstance(payload, dict) else {}

    def skill_content(self, canonical_id: str | None) -> dict[str, Any]:
        if not canonical_id:
            return {}
        payload = self.skill_content_by_id.get(canonical_id)
        return payload if isinstance(payload, dict) else {}

    def attribute_type_content(self, canonical_id: str | None) -> dict[str, Any]:
        if not canonical_id:
            return {}
        payload = self.attribute_type_content_by_id.get(canonical_id)
        return payload if isinstance(payload, dict) else {}

    def attribute_type_info(self, numeric_id: Any = None) -> dict[str, Any]:
        if numeric_id is None:
            return {}
        key = str(int(numeric_id))
        payload = self.attribute_type_by_numeric.get(key)
        return payload if isinstance(payload, dict) else {}

    def parent_rules(self, parent_type: str, canonical_id: str) -> dict[str, Any]:
        if not canonical_id:
            return {}
        indexes = self.semantic_indexes.get("parent_rules") if isinstance(self.semantic_indexes, dict) else {}
        if not isinstance(indexes, dict):
            return {}
        if parent_type == "skill":
            payload = indexes.get("skills")
        elif parent_type == "buff":
            payload = indexes.get("buffs")
        else:
            return {}
        if not isinstance(payload, dict):
            return {}
        row = payload.get(canonical_id)
        return row if isinstance(row, dict) else {}

    @staticmethod
    def _is_canonical_buff_id(value: str | None) -> bool:
        text = str(value or "")
        return bool(text) and text != "unknown_buff" and not text.isdigit()


    @staticmethod
    def _blackboard_signature(values: dict[str, float] | None) -> str:
        if not isinstance(values, dict) or not values:
            return ""
        keys = sorted(str(key) for key in values.keys() if str(key))
        return "|".join(keys)




    def _resolve_buff_from_semantic_indexes(self, context: PacketResolveContext | None) -> str | None:
        # Equal blackboard keys are not a template identity proof.
        return None



    @staticmethod
    def _assigned_item_keys(assigned_items: Any) -> tuple[set[str], set[str]]:
        target_keys: set[str] = set()
        input_keys: set[str] = set()
        if not isinstance(assigned_items, list):
            return target_keys, input_keys
        for item in assigned_items:
            if not isinstance(item, dict):
                continue
            target = str(item.get("target_key") or item.get("targetKey") or "")
            input_key = str(item.get("input_value_key") or item.get("inputValueKey") or "")
            if target:
                target_keys.add(target)
            if input_key:
                input_keys.add(input_key)
        return target_keys, input_keys

    def resolve_created_buff(
        self,
        *,
        parent_type: str,
        parent_canonical_id: str,
        assigned_items: Any,
    ) -> str | None:
        # This API has no packet child-template or action-instance identity.
        # Callers must retain an unknown child instead of choosing a candidate.
        return None
