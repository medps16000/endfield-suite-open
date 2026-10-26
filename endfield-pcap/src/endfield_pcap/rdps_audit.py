from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from html import escape
from pathlib import Path
from typing import Any

from .runtime_paths import bundle_root
from .trace_bridge import default_trace_file


_LOADOUT_SLOT_RE = re.compile(
    r"\bLOADOUT\s+slot=(?P<slot>\d+)\s+char=(?P<char>\S+).*?"
    r"weaponTemplate=(?P<weapon>\S+)\s+weaponLv=(?P<weapon_lv>-?\d+)\s+"
    r"refine=(?P<refine>-?\d+|unknown)\s+break=(?P<breakthrough>-?\d+)\s+"
    r"attachedGem=(?P<attached_gem>-?\d+)\s+equipInsts=(?P<equip_inst>\{.*?\})\s+"
    r"equips=(?P<equips>\{.*?\})\s+"
    r"(?:(?:(?:equipsComplete=\S+)|(?:unplacedEquipInstIds=\[[^\]]*\]))\s+)*"
    r"equipSuit=(?P<equip_suit>\{.*\})"
)
_SQUAD_RE = re.compile(r"\bSQUAD\s+size=(?P<size>\d+)\s+members=\[(?P<members>[^\]]*)\]")
_BUFF_START_RE = re.compile(
    r'\bBUFF_START\s+#(?P<seq>\d+)\b(?=.*?\bid="(?P<buff_id>[^"]*)")(?=.*?\buid=(?P<uid>\S+))'
    r"(?=.*?\bowner=(?P<owner>\S+))(?=.*?\bsrc=(?P<src>\S+))"
)
_BUFF_END_RE = re.compile(r'\bBUFF_END\s+#(?P<seq>\d+)\b(?=.*?\bid="(?P<buff_id>[^"]*)")(?=.*?\buid=(?P<uid>\S+))')
_BB_RE = re.compile(r"\bBB\[(?P<uid>[^\]]+)\]:\s*(?P<body>.*)")
_HP_RE = re.compile(
    r'\bHP_V2\s+#(?P<seq>\d+)\b(?=.*?\bhit=(?P<hit>\d+)\b)(?=.*?\bskill="(?P<skill>[^"]*)")'
    r"(?=.*?\bsrc=(?P<src>\S+))(?=.*?\btgt=(?P<tgt>\S+))(?=.*?\batk=(?P<atk>\S+))"
)
_CHAR_PREFIX_RE = re.compile(r"^(chr_\d{4})_")
_CHAR_KEY_RE = re.compile(r"chr_\d{4}_[a-z0-9]+")
_ENEMY_KEY_RE = re.compile(r"eny_\d{4}_[a-z0-9]+")


@dataclass(slots=True)
class BuffSeen:
    buff_id: str
    raw_buff_id: str
    uid: str
    owner: str
    src: str
    runtime_truth_canonical: str = ""
    bb_keys: set[str] = field(default_factory=set)
    bb_values: dict[str, Any] = field(default_factory=dict)
    ended: bool = False


@dataclass(slots=True)
class EquipSemantic:
    suit_id: str
    passive_skill_id: str
    values: dict[str, Any]
    piece_ids: set[str]


def _load_buff_semantics(root: Path) -> dict[str, dict[str, Any]]:
    path = root / "data" / "local_semantics" / "buff" / "details.json"
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    entries = payload.get("entries")
    return entries if isinstance(entries, dict) else {}


def _load_equipment_semantics(root: Path) -> dict[str, EquipSemantic]:
    items_dir = root / "data" / "local_tables" / "equip" / "items"
    if not items_dir.exists():
        return {}
    results: dict[str, EquipSemantic] = {}
    for path in items_dir.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        suit_id = payload.get("suitID") or payload.get("name") or path.stem
        values = payload.get("value")
        if not isinstance(suit_id, str) or not isinstance(values, dict):
            continue
        piece_ids = {
            item
            for item in payload.get("pieceIds") or []
            if isinstance(item, str) and item
        }
        results[suit_id] = EquipSemantic(
            suit_id=suit_id,
            passive_skill_id=str(payload.get("passiveSkillId") or ""),
            values=values,
            piece_ids=piece_ids,
        )
    return results


def _load_packet_mappings(root: Path) -> dict[str, dict[str, Any]]:
    path = root / "data" / "packet_semantics" / "buff_numeric_map.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    mappings = payload.get("mappings")
    if not isinstance(mappings, dict):
        return {}
    return {
        str(buff_id): mapping
        for buff_id, mapping in mappings.items()
        if isinstance(mapping, dict)
    }


_STATIC_PACKET_BUFF_ALIASES = {
    "buff_equipsuit_usp_02_dmgup": "buff_equipsuit_usp_02_AddAttack",
}


def _load_packet_mappings_by_canonical(packet_mappings: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_canonical: dict[str, dict[str, Any]] = {}
    for mapping in packet_mappings.values():
        canonical = str(mapping.get("canonical_buff_id") or "")
        if canonical and canonical not in by_canonical:
            by_canonical[canonical] = mapping
    return by_canonical


def _load_num_id_str_buff_map(root: Path) -> dict[str, str]:
    candidate_paths = (
        root.resolve().parent / "endfield_tables" / "Data" / "TableCfg" / "NumIdStrTable.json",
        root / "data" / "local_tables" / "NumIdStrTable.json",
    )
    for path in candidate_paths:
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        buff_section = payload.get("buff_id") if isinstance(payload, dict) else None
        mapping = buff_section.get("dic") if isinstance(buff_section, dict) else None
        if not isinstance(mapping, dict):
            continue
        return {
            str(buff_id): str(static_id)
            for buff_id, static_id in mapping.items()
            if static_id is not None
        }
    return {}


def _load_mechanism_mappings_by_buff_id(root: Path) -> dict[str, dict[str, Any]]:
    path = root / "data" / "packet_semantics" / "mechanism_registry.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    rows_by_buff = payload.get("by_buff_id") if isinstance(payload, dict) else None
    if not isinstance(rows_by_buff, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for buff_id, rows in rows_by_buff.items():
        if not isinstance(rows, list):
            continue
        normalized = [row for row in rows if isinstance(row, dict)]
        if len(normalized) != 1:
            continue
        row = dict(normalized[0])
        row["canonical_buff_id"] = str(row.get("canonical_buff_id") or buff_id)
        if row.get("source_kind") == "weapon" and row.get("source_id") and not row.get("weapon_id"):
            row["weapon_id"] = row.get("source_id")
        if row.get("source_kind") == "suit" and row.get("source_id") and not row.get("suit_id"):
            row["suit_id"] = row.get("source_id")
        out[str(buff_id)] = row
    return out


def _load_packet_bundle_buff_ids(root: Path) -> set[str]:
    known: set[str] = set()

    bundle_path = root / "data" / "packet_semantics" / "packet_resolver_bundle.json"
    if bundle_path.exists():
        try:
            payload = json.loads(bundle_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        content = payload.get("content") if isinstance(payload, dict) else {}
        buffs = content.get("buffs") if isinstance(content, dict) else {}
        if isinstance(buffs, dict):
            for buff_id in buffs.keys():
                if isinstance(buff_id, str) and buff_id:
                    known.add(buff_id)

    for mapping in _load_packet_mappings(root).values():
        if not isinstance(mapping, dict):
            continue
        canonical = str(mapping.get("canonical_buff_id") or "")
        if canonical:
            known.add(canonical)

    return known


def _default_runtime_truth_db_file() -> Path:
    return bundle_root().resolve().parent / "endfield-dump" / "database" / "runtime_truth_db.json"


def _load_runtime_truth_db(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _runtime_truth_known_buff_ids(payload: dict[str, Any]) -> set[str]:
    known: set[str] = set()
    buffs = payload.get("buffs")
    if isinstance(buffs, dict):
        for buff_id in buffs:
            if isinstance(buff_id, str) and buff_id:
                known.add(buff_id)
    registry_items = payload.get("registry_items")
    if isinstance(registry_items, dict):
        for buff_id in registry_items:
            if isinstance(buff_id, str) and buff_id.startswith("buff_"):
                known.add(buff_id)
    return known


def _default_truth_jsonl_file() -> Path:
    return Path(os.environ.get("TEMP", ".")) / "endfield_truth_dump.jsonl"


def _load_runtime_truth_buff_uid_map(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    mapping: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        if str(row.get("type") or "") != "TRUTH_BUFF":
            continue
        uid = row.get("instUid")
        canonical = row.get("canonical")
        if uid is None:
            continue
        uid_key = str(uid)
        canonical_text = str(canonical or "")
        if uid_key and canonical_text:
            mapping[uid_key] = canonical_text
    return mapping


def _parse_scalar(raw: str) -> Any:
    raw = raw.strip()
    if not raw:
        return raw
    try:
        if re.search(r"[.eE]", raw):
            return float(raw)
        return int(raw)
    except ValueError:
        return raw


def _bb_values(body: str) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for token in body.split():
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        key = key.strip()
        if key:
            values[key] = _parse_scalar(value)
    return values


def _bb_keys(body: str) -> set[str]:
    return set(_bb_values(body))


def _has_rdps_semantics(entry: dict[str, Any] | None) -> bool:
    if not entry:
        return False
    flags = entry.get("semanticFlags") if isinstance(entry.get("semanticFlags"), dict) else {}
    counts = entry.get("modifierCounts") if isinstance(entry.get("modifierCounts"), dict) else {}
    probe = entry.get("binaryProbe") if isinstance(entry.get("binaryProbe"), dict) else {}
    return bool(
        flags.get("hasTemplateModifiers")
        or flags.get("hasAttributeModifier")
        or flags.get("hasDamageModifier")
        or counts.get("attribute")
        or counts.get("damage")
        or probe.get("rdpsCandidate")
    )


def _semantic_bb_keys(entry: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for item in entry.get("blackboard") or []:
        if not isinstance(item, dict):
            continue
        key = item.get("key")
        if isinstance(key, str) and key:
            keys.add(key)
    return keys


def _char_prefix(value: str) -> str | None:
    match = _CHAR_PREFIX_RE.match(value or "")
    return match.group(1) if match else None


def _static_domain_matches_actor(actor: str, static_id: str) -> bool:
    actor_text = str(actor or "")
    static_text = str(static_id or "")
    if not actor_text or not static_text:
        return True
    if actor_text.startswith("chr_") and _ENEMY_KEY_RE.search(static_text):
        return False
    if actor_text.startswith("eny_") and _CHAR_KEY_RE.search(static_text):
        return False
    actor_prefix = _char_prefix(actor_text)
    static_prefix = _char_prefix(static_text)
    return not (actor_prefix and static_prefix and actor_prefix != static_prefix)


def _canonical_static_buff_id(
    raw_buff_id: str,
    *,
    owner: str,
    src: str,
    num_id_str_buff_map: dict[str, str],
) -> str:
    alias = _STATIC_PACKET_BUFF_ALIASES.get(raw_buff_id)
    if alias:
        return alias
    static_id = num_id_str_buff_map.get(str(raw_buff_id or ""))
    if not static_id:
        return ""
    if not any(_static_domain_matches_actor(actor, static_id) for actor in (src, owner)):
        return ""
    return static_id


def _packet_mapping_for_seen(
    seen: BuffSeen,
    *,
    packet_mappings: dict[str, dict[str, Any]],
    packet_mappings_by_canonical: dict[str, dict[str, Any]],
    mechanism_mappings_by_buff_id: dict[str, dict[str, Any]],
    active_suits: dict[str, list[dict[str, Any]]],
    active_weapons: dict[str, str],
) -> tuple[dict[str, Any] | None, str]:
    candidates = [
        str(seen.raw_buff_id or ""),
        str(seen.buff_id or ""),
    ]
    candidates.extend(
        str(value)
        for value in (
            _STATIC_PACKET_BUFF_ALIASES.get(str(seen.raw_buff_id or "")),
            _STATIC_PACKET_BUFF_ALIASES.get(str(seen.buff_id or "")),
        )
        if value
    )
    for key in dict.fromkeys(value for value in candidates if value):
        mapping = packet_mappings.get(key) or packet_mappings_by_canonical.get(key) or mechanism_mappings_by_buff_id.get(key)
        if not isinstance(mapping, dict):
            continue
        if not _packet_mapping_applies(mapping, seen, active_suits, active_weapons):
            continue
        return mapping, key
    return None, ""


_RDPS_EQUIP_KEYS = {
    "atk_up",
    "crit_up",
    "crit_up2",
    "cryst_dmg_up",
    "dmg_up",
    "fire_dmg_up",
    "nature_dmg_up",
    "phy_dmg_up",
    "phy_dmg_up2",
    "pulse_dmg_up",
    "skill_dmg_up",
    "spell_dmg_up",
    "spell_up",
}
_RDPS_EFFECT_BB_KEYS = {
    "atk_up",
    "crit_up",
    "crit_up2",
    "crit_dmg_up",
    "def_decrease",
    "def_decrease_tick",
    "def_decrease_tick_final",
    "dmg_up",
    "fire_dmg_up",
    "ignore_fire_resist",
    "max_def_decrease",
    "max_def_decrease_final",
    "normal_atk_up_valid",
    "phy_dmg_up",
    "phy_dmg_up2",
    "spell_dmg_up",
    "spell_taken_up",
    "spell_up",
    "start_def_decrease",
}
_NON_RDPS_BB_KEYS = {
    "atb",
    "cd",
    "count",
    "duration",
    "heal_max_hp",
    "heal_scale",
    "heal_value",
    "healvalue",
    "hp_up",
    "lv",
    "max_stack",
    "poise",
    "posie",
    "probability",
    "ratio",
    "shelter",
    "skill_bg_type",
    "speed",
}


def _is_rdps_effect_bb_key(key: str) -> bool:
    lowered = key.lower()
    if lowered in _RDPS_EFFECT_BB_KEYS:
        return True
    if any(token in lowered for token in ("def_decrease", "resist", "taken_up", "vulnerable", "fragile")):
        return True
    if any(token in lowered for token in ("dmg_up", "damage_up", "spell_up", "atk_up", "crit")):
        return True
    return lowered.startswith("ignore_") and ("res" in lowered or "def" in lowered)


def _could_be_external_rdps_buff(seen: BuffSeen) -> bool:
    owner = str(seen.owner or "")
    src = str(seen.src or "")
    if not _CHAR_KEY_RE.search(src):
        return False
    if _ENEMY_KEY_RE.search(owner):
        return True
    owner_char = _CHAR_KEY_RE.search(owner)
    src_char = _CHAR_KEY_RE.search(src)
    return bool(owner_char and src_char and owner_char.group(0) != src_char.group(0))


def _classify_unresolved_packet_buff(seen: BuffSeen) -> tuple[str, list[str]]:
    return ("unverified", [])


def _values_equal(left: Any, right: Any) -> bool:
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return abs(float(left) - float(right)) <= 0.0001
    return str(left) == str(right)


def _parse_explicit_suit_counts(raw: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for suit_id, count in re.findall(r"\[([^\]]+)\]=(\d+)", raw or ""):
        counts[suit_id] = max(counts.get(suit_id, 0), int(count))
    return counts


def _infer_suit_counts(raw_equips: str, equipment: dict[str, EquipSemantic]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for equip_id in re.findall(r"=(item_equip_[^\s|}]+)", raw_equips or ""):
        for suit_id, semantic in equipment.items():
            if equip_id in semantic.piece_ids:
                counts[suit_id] += 1
                break
    return dict(counts)


def _active_suits_by_char(
    loadout_rows: list[dict[str, Any]],
    equipment: dict[str, EquipSemantic],
) -> dict[str, list[dict[str, Any]]]:
    active: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in loadout_rows[-4:]:
        explicit = _parse_explicit_suit_counts(row.get("equip_suit") or "")
        inferred = _infer_suit_counts(row.get("equips") or "", equipment)
        suit_ids = set(explicit) | set(inferred)
        for suit_id in sorted(suit_ids):
            count = max(explicit.get(suit_id, 0), inferred.get(suit_id, 0))
            if count < 3 or suit_id not in equipment:
                continue
            active[str(row.get("char") or "")].append(
                {
                    "suit_id": suit_id,
                    "count": count,
                    "source": "equipSuit" if explicit.get(suit_id, 0) >= 3 else "equips",
                }
            )
    return active


def _active_weapons_by_char(loadout_rows: list[dict[str, Any]]) -> dict[str, str]:
    return {
        str(row.get("char") or ""): str(row.get("weapon_template") or "")
        for row in loadout_rows[-4:]
        if row.get("char") and row.get("weapon_template")
    }


def _packet_mapping_applies(
    mapping: dict[str, Any] | None,
    seen: BuffSeen,
    active_suits: dict[str, list[dict[str, Any]]],
    active_weapons: dict[str, str],
) -> bool:
    if not isinstance(mapping, dict):
        return False
    character_id = str(mapping.get("character_id") or "")
    if character_id and character_id not in {seen.owner, seen.src}:
        return False
    suit_id = str(mapping.get("suit_id") or "")
    if suit_id:
        suit_chars = [char for char in (seen.owner, seen.src) if char in active_suits]
        if suit_chars and not any(
            suit_id == item.get("suit_id")
            for char in suit_chars
            for item in active_suits.get(char, [])
        ):
            return False
    weapon_id = str(mapping.get("weapon_id") or "")
    if weapon_id:
        known_weapons = {
            active_weapons.get(char)
            for char in (seen.owner, seen.src)
            if char in active_weapons
        }
        if known_weapons and weapon_id not in known_weapons:
            return False
    return True


def _equipment_candidates(
    seen: BuffSeen,
    active_suits: dict[str, list[dict[str, Any]]],
    equipment: dict[str, EquipSemantic],
    *,
    limit: int = 5,
) -> list[dict[str, Any]]:
    return []


def _candidate_semantics(
    seen: BuffSeen,
    buff_semantics: dict[str, dict[str, Any]],
    *,
    limit: int = 5,
) -> list[dict[str, Any]]:
    return []


def _line_payload(line: str) -> str:
    if "] " not in line:
        return line.strip()
    return line.split("] ", 1)[1].strip()


def _load_rdps_registry(root: Path) -> dict[str, dict[str, Any]]:
    path = root / "data" / "packet_semantics" / "rdps_semantics_registry.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    verified = payload.get("verified_effects") if isinstance(payload, dict) else None
    return verified if isinstance(verified, dict) else {}


def _load_rdps_known_non_rdps_registry(root: Path) -> dict[str, Any]:
    path = root / "data" / "packet_semantics" / "rdps_semantics_registry.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    known = payload.get("known_non_rdps") if isinstance(payload, dict) else None
    return known if isinstance(known, dict) else {}


def _registry_bb_keys_allowed(entry: dict[str, Any], keys: set[str]) -> bool:
    allowed_values = entry.get("allowed_bb_keys")
    if not isinstance(allowed_values, list):
        return True
    allowed = {str(key).lower() for key in allowed_values}
    if "*" in allowed:
        return True
    observed = {str(key).lower() for key in keys if str(key)}
    return observed.issubset(allowed)


def _known_non_rdps_seen(seen: BuffSeen, known: dict[str, Any]) -> bool:
    exact = known.get("exact_buff_ids") if isinstance(known.get("exact_buff_ids"), dict) else {}
    keys = {str(seen.raw_buff_id or ""), str(seen.buff_id or "")}
    keys.discard("")
    observed = set(str(key) for key in seen.bb_keys if str(key))
    for key in keys:
        entry = exact.get(key)
        if isinstance(entry, dict) and _registry_bb_keys_allowed(entry, observed):
            return True

    prefixes = known.get("prefixes") if isinstance(known.get("prefixes"), list) else []
    for key in keys:
        for entry in prefixes:
            if not isinstance(entry, dict):
                continue
            prefix = str(entry.get("prefix") or "")
            if prefix and key.startswith(prefix) and _registry_bb_keys_allowed(entry, observed):
                return True
    return False


def _rdps_registry_by_event_key(root: Path) -> dict[str, dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}
    for event_key, entry in _load_rdps_registry(root).items():
        if not isinstance(entry, dict):
            continue
        by_key[str(event_key)] = entry
        for alias in entry.get("aliases") or []:
            if isinstance(alias, str) and alias:
                by_key.setdefault(alias, entry)
    return by_key


def _registry_runtime_bb_keys(entry: dict[str, Any] | None) -> list[str]:
    if not isinstance(entry, dict):
        return []
    keys: set[str] = set()
    for effect in entry.get("effects") or []:
        if not isinstance(effect, dict):
            continue
        for field in (
            "bb_key",
            "add_bb_key",
            "mult_bb_key",
            "consume_bb_key",
            "formula_bb_key",
            "rate_bb_key",
        ):
            value = effect.get(field)
            if isinstance(value, str) and value:
                keys.add(value)
        for field in ("bb_keys", "required_bb_keys"):
            for value in effect.get(field) or []:
                if isinstance(value, str) and value:
                    keys.add(value)
    return sorted(keys)


def _md_cell(value: Any) -> str:
    text = str(value if value is not None else "")
    text = text.replace("\n", " ").replace("|", "\\|")
    return text


def _percent_text(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value if value is not None else "")
    return f"{number * 100:.2f}%"


_ZONE_CN = {
    "atk": "攻击力",
    "dmg_inc": "增伤",
    "amp": "增幅",
    "fragile": "承伤易伤",
    "vuln_taken": "承伤易伤",
    "res": "减抗",
    "res_down": "减抗",
    "def_down": "减防",
    "combo": "连击增伤",
}

_ELEMENT_CN = {
    "all": "全属性",
    "physical": "物理",
    "physic": "物理",
    "fire": "灼热",
    "pulse": "电磁",
    "cryst": "寒冷",
    "spell": "法术",
    "nature": "自然",
}


def _effect_text(effect: dict[str, Any]) -> str:
    zone = _ZONE_CN.get(str(effect.get("zone") or ""), str(effect.get("zone") or ""))
    element = _ELEMENT_CN.get(str(effect.get("element") or "all"), str(effect.get("element") or "all"))
    rate_value = effect.get("rate")
    if rate_value is None and effect.get("base_rate") is not None:
        rate = _percent_text(effect.get("base_rate"))
        max_rate = effect.get("max_rate")
        delayed_add = effect.get("delayed_add_rate")
        if max_rate:
            rate = f"{rate}->{_percent_text(max_rate)}"
        elif delayed_add:
            try:
                rate = f"{rate}->{_percent_text(float(effect.get('base_rate') or 0.0) + float(delayed_add))}"
            except (TypeError, ValueError):
                pass
    else:
        rate = _percent_text(rate_value)
    bb_key = str(effect.get("bb_key") or effect.get("_registry_bb_key") or "")
    suffix = f" BB={bb_key}" if bb_key else ""
    return f"{zone}/{element} {rate}{suffix}".strip()


def _effects_text(event: dict[str, Any]) -> str:
    effects: list[str] = []
    for effect in event.get("zone_effects") or []:
        if isinstance(effect, dict):
            effects.append(_effect_text(effect))
    for effect in event.get("dynamic_effects") or []:
        if isinstance(effect, dict):
            effects.append(_effect_text(effect))
    if effects:
        return "; ".join(effects)
    summary = event.get("effect_summary")
    if isinstance(summary, list) and summary:
        return "; ".join(str(item) for item in summary[:4])
    return ""


def _runtime_bb_text(bb_values: dict[str, Any], keys: list[str]) -> str:
    if not isinstance(bb_values, dict):
        return ""
    if keys:
        parts = [f"{key}={bb_values.get(key)}" for key in keys if key in bb_values]
    else:
        parts = [f"{key}={bb_values.get(key)}" for key in sorted(bb_values)[:8]]
    return ", ".join(parts)


def _external_rdps_event(event: dict[str, Any]) -> bool:
    source_key = str(event.get("source_character_key") or "")
    if not source_key.startswith("chr_"):
        return False
    target_enemy_key = str(event.get("target_enemy_key") or "")
    if target_enemy_key.startswith("eny_"):
        return True
    target_character_key = str(event.get("target_character_key") or "")
    return bool(target_character_key.startswith("chr_") and target_character_key != source_key)


def _display_actor(name: Any, key: Any) -> str:
    name_text = str(name or "")
    key_text = str(key or "")
    if name_text and key_text and name_text != key_text:
        return f"{name_text}({key_text})"
    return name_text or key_text


def _block_reason_cn(reason: Any) -> str:
    text = str(reason or "")
    if "not in rdps_semantics_registry.verified_effects" in text:
        return "效果进入了外部 rDPS 窗口，但没有命中白名单"
    if "carried effect-like BB keys not declared" in text:
        return "白名单命中，但带了未声明的效果型 BB"
    if "did not expose required runtime BB value" in text:
        return "白名单命中，但缺少 effects 声明需要的运行时 BB 数值"
    if "packet mapping" in text and "rejected" in text:
        return "包映射被 guard 拒绝"
    return text or "未知原因"


def _build_rdps_trust_audit(report: dict[str, Any], *, root: Path, proof: dict[str, Any] | None = None) -> dict[str, Any]:
    registry_by_key = _rdps_registry_by_event_key(root)
    preflight = report.get("rdps_preflight") or {}
    proof = proof or {}
    basis = report.get("rdps_damage_basis") or (report.get("battle") or {}).get("rdps_damage_basis") or {}
    if not proof and isinstance(basis, dict):
        proof = basis.get("rdps_proof") or {}
    buff_events = [event for event in report.get("buff_events") or [] if isinstance(event, dict)]
    events_by_uid = {str(event.get("uid") or ""): event for event in buff_events if event.get("uid") is not None}
    credit_by_buff: dict[tuple[str, str, str], float] = defaultdict(float)
    for row in proof.get("external_credit_by_buff") or []:
        if not isinstance(row, dict):
            continue
        key = (
            str(row.get("source_character_key") or ""),
            str(row.get("event_key") or ""),
            str(row.get("zone") or ""),
        )
        try:
            credit_by_buff[key] += float(row.get("value") or 0.0)
        except (TypeError, ValueError):
            continue

    blocked_uids = {
        str(blocker.get("uid") or "")
        for blocker in preflight.get("blockers") or []
        if isinstance(blocker, dict) and blocker.get("uid") is not None
    }
    accepted_rows: list[dict[str, Any]] = []
    for event in buff_events:
        if not _external_rdps_event(event):
            continue
        if not (event.get("zone_effects") or event.get("dynamic_effects")):
            continue
        if str(event.get("status") or "") != "included":
            continue
        if str(event.get("uid") or "") in blocked_uids:
            continue
        event_key = str(event.get("event_key") or "")
        entry = registry_by_key.get(event_key)
        if entry is None:
            continue
        runtime_keys = _registry_runtime_bb_keys(entry)
        zones = [str(effect.get("zone") or "") for effect in (event.get("zone_effects") or []) if isinstance(effect, dict)]
        credit = sum(
            credit_by_buff.get((str(event.get("source_character_key") or ""), event_key, zone), 0.0)
            for zone in zones
        )
        accepted_rows.append(
            {
                "event_key": event_key,
                "name": (entry or {}).get("cn_name") or event.get("event_name") or event_key,
                "source": _display_actor(event.get("source_character_name"), event.get("source_character_key")),
                "target": _display_actor(event.get("target_character_name"), event.get("target_character_key"))
                or _display_actor(event.get("target_enemy_key"), event.get("target_enemy_key")),
                "source_skill": event.get("source_skill_family_key") or event.get("source_skill_key") or "",
                "effects": _effects_text(event),
                "runtime_bb": _runtime_bb_text(event.get("bb_values") or {}, runtime_keys),
                "status": event.get("status_label") or event.get("status"),
                "external_credit": credit,
            }
        )

    blocker_rows: list[dict[str, Any]] = []
    for blocker in preflight.get("blockers") or []:
        if not isinstance(blocker, dict):
            continue
        event = events_by_uid.get(str(blocker.get("uid") or "")) or {}
        event_key = str(blocker.get("event_key") or event.get("event_key") or "")
        entry = registry_by_key.get(event_key)
        runtime_keys = _registry_runtime_bb_keys(entry)
        bb_values = event.get("bb_values") if isinstance(event.get("bb_values"), dict) else {}
        required = blocker.get("required_bb_keys") or []
        unknown = blocker.get("unknown_bb_keys") or []
        blocker_rows.append(
            {
                "event_key": event_key,
                "name": (entry or {}).get("cn_name") or event.get("event_name") or event_key,
                "source": _display_actor(event.get("source_character_name"), blocker.get("source_character_key")),
                "target": _display_actor(event.get("target_character_name"), blocker.get("target_character_key"))
                or _display_actor(blocker.get("target_enemy_key"), blocker.get("target_enemy_key")),
                "source_skill": blocker.get("source_skill_family_key") or blocker.get("source_skill_key") or "",
                "reason": _block_reason_cn(blocker.get("reason")),
                "required_bb": ", ".join(str(item) for item in required),
                "unknown_bb": ", ".join(str(item) for item in unknown),
                "bb_keys": ", ".join(str(item) for item in blocker.get("bb_keys") or []),
                "runtime_bb": _runtime_bb_text(bb_values, runtime_keys),
            }
        )

    credit_rows = [
        {
            "source": row.get("source_character_key"),
            "event_key": row.get("event_key"),
            "zone": _ZONE_CN.get(str(row.get("zone") or ""), str(row.get("zone") or "")),
            "value": float(row.get("value") or 0.0),
        }
        for row in proof.get("external_credit_by_buff") or []
        if isinstance(row, dict)
    ]
    return {
        "ok": preflight.get("ok") is True and basis.get("rdps_strict_ok") is True and proof.get("external_credit_evidence_ok") is True,
        "preflight_ok": bool(preflight.get("ok")),
        "checked_external_buff_count": int(preflight.get("checked_external_buff_count") or 0),
        "accepted_effect_buff_count": int(preflight.get("accepted_effect_buff_count") or 0),
        "accepted_non_rdps_buff_count": int(preflight.get("accepted_non_rdps_buff_count") or 0),
        "blocker_count": int(preflight.get("blocker_count") or 0),
        "accepted_rows": accepted_rows[:100],
        "accepted_row_count": len(accepted_rows),
        "blockers": blocker_rows[:100],
        "credit_rows": credit_rows[:100],
        "credit_row_count": len(credit_rows),
    }


def _parse_truth_jsonl_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _parse_truth_squad_roster(value: Any) -> list[str]:
    if not isinstance(value, str):
        return []
    return [item for item in value.split() if item]


def _per_hit_rdps_audit(text: str, *, root: Path, expected_hit_count: int) -> dict[str, Any]:
    parser_core_path = root / "packages" / "parser_core"
    if parser_core_path.exists():
        parser_core_path_text = str(parser_core_path)
        if parser_core_path_text not in sys.path:
            sys.path.insert(0, parser_core_path_text)
    try:
        from parser_core.battle_log_parser import parse_raw_battle_log_text
        from parser_core.unified import rdps_totals_from_raw_report
    except Exception as exc:  # noqa: BLE001 - audit should report optional parser failures.
        return {
            "available": False,
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
        }

    try:
        report = parse_raw_battle_log_text(
            text,
            file_name="rdps_audit_window.log",
            include_rdps_debug=True,
        )
    except Exception as exc:  # noqa: BLE001 - keep the rest of the audit usable.
        return {
            "available": True,
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
        }

    hits = [
        event
        for event in report.get("timeline_events") or []
        if isinstance(event, dict)
        and event.get("lane_type") == "skill"
        and event.get("value") is not None
    ]
    hits_without_contrib: list[int] = []
    total_contribution_by_char = Counter(rdps_totals_from_raw_report(report))
    basis = report.get("rdps_damage_basis") or (report.get("battle") or {}).get("rdps_damage_basis") or {}
    preflight = report.get("rdps_preflight") or basis.get("rdps_preflight") or {}
    packet_grounded_hit_count = int(basis.get("packet_grounded_hit_count") or 0) if basis else 0
    packet_final_value_count = int(basis.get("packet_final_value_count") or 0) if basis else 0
    packet_grounded_ok = bool(hits) and bool(basis) and packet_grounded_hit_count == len(hits)
    conservation_ok = basis.get("rdps_conservation_ok") is True
    preflight_ok = preflight.get("ok") is True and basis.get("rdps_strict_ok") is True
    sample_hits: list[dict[str, Any]] = []
    for index, hit in enumerate(hits, start=1):
        contributions = hit.get("rdps_contributions") or []
        try:
            hit_value = float(hit.get("value") or 0.0)
        except (TypeError, ValueError):
            hit_value = 0.0
        if not contributions and hit_value > 0:
            hits_without_contrib.append(index)
        if len(sample_hits) < 8:
            sample_hits.append(
                {
                    "index": index,
                    "ts_ms_from_start": hit.get("ts_ms_from_start"),
                    "attacker": hit.get("source_character_key"),
                    "target": hit.get("target_character_key"),
                    "skill": hit.get("event_key"),
                    "hit_value": hit.get("value"),
                    "value_source": hit.get("value_source"),
                    "packet_final_value": hit.get("packet_final_value"),
                    "rdps_contributions": contributions,
                }
            )

    parsed_hit_count = len(hits)
    # The static audit's player->enemy hit counter is a conservative regex pass
    # and can undercount older traces whose actors are resolved by parser_core.
    # Treat parser under-counting as fatal; parser over-counting is validated by
    # packet grounding and conservation below.
    mismatch = expected_hit_count > 0 and parsed_hit_count < expected_hit_count
    debug_hits = [hit for hit in report.get("debug_hits") or [] if isinstance(hit, dict)]
    external_evidence_missing: list[dict[str, Any]] = []
    external_credit_by_source: Counter[str] = Counter()
    external_credit_by_buff: Counter[tuple[str, str, str]] = Counter()
    external_credit_by_zone: Counter[str] = Counter()
    external_contribution_rows = 0
    external_credit_evidence_hits = 0
    if len(debug_hits) != parsed_hit_count:
        external_evidence_missing.append(
            {
                "reason": "debug_hit_count_mismatch",
                "debug_hit_count": len(debug_hits),
                "parsed_hit_count": parsed_hit_count,
            }
        )
    for index, debug_hit in enumerate(debug_hits, start=1):
        attacker = str(debug_hit.get("character_key") or "")
        external_rows = [
            item
            for item in debug_hit.get("rdps_contributions") or []
            if str(item.get("character_key") or "") and str(item.get("character_key") or "") != attacker
        ]
        if not external_rows:
            continue
        external_contribution_rows += len(external_rows)
        seq = debug_hit.get("seq")
        sources = {
            str(item.get("character_key") or ""): float(item.get("rdps_credit") or 0.0)
            for item in debug_hit.get("external_sources") or []
            if str(item.get("character_key") or "")
        }
        missing_sources = [
            str(item.get("character_key") or "")
            for item in external_rows
            if sources.get(str(item.get("character_key") or ""), 0.0) <= 0
        ]
        if missing_sources:
            external_evidence_missing.append(
                {
                    "index": index,
                    "seq": seq,
                    "reason": "missing_external_source_credit",
                    "sources": missing_sources,
                }
            )
            continue
        external_credit_evidence_hits += 1
        for character_key, credit in sources.items():
            external_credit_by_source[character_key] += credit
        for zone in debug_hit.get("zones") or []:
            zone_key = str(zone.get("zone") or "")
            try:
                zone_share = float(zone.get("zone_external_share") or 0.0)
            except (TypeError, ValueError):
                zone_share = 0.0
            if zone_key and zone_share > 0:
                external_credit_by_zone[zone_key] += zone_share
            for contributor in zone.get("contributors") or []:
                if contributor.get("scope") != "external":
                    continue
                try:
                    credit = float(contributor.get("rdps_credit") or 0.0)
                except (TypeError, ValueError):
                    credit = 0.0
                if credit <= 0:
                    continue
                source_key = str(contributor.get("source_character_key") or "")
                event_key = str(contributor.get("event_key") or "")
                if not source_key or not event_key or not zone_key:
                    external_evidence_missing.append(
                        {
                            "index": index,
                            "seq": seq,
                            "reason": "incomplete_external_buff_evidence",
                            "source": source_key,
                            "event_key": event_key,
                            "zone": zone_key,
                        }
                    )
                    continue
                external_credit_by_buff[(source_key, event_key, zone_key)] += credit
    external_credit_evidence_ok = bool(hits) and len(debug_hits) == len(hits) and not external_evidence_missing
    rdps_proof = {
        "definition": "packet HP-loss conserved attribution",
        "preflight_ok": preflight_ok,
        "packet_damage_basis_ok": packet_grounded_ok,
        "per_hit_conservation_ok": conservation_ok,
        "external_credit_evidence_ok": external_credit_evidence_ok,
        "debug_hit_count": len(debug_hits),
        "external_contribution_rows": external_contribution_rows,
        "external_credit_evidence_hits": external_credit_evidence_hits,
        "external_credit_by_source": external_credit_by_source.most_common(),
        "external_credit_by_zone": external_credit_by_zone.most_common(),
        "external_credit_by_buff": [
            {
                "source_character_key": source,
                "event_key": event_key,
                "zone": zone,
                "value": value,
            }
            for (source, event_key, zone), value in external_credit_by_buff.most_common(30)
        ],
        "external_evidence_missing": external_evidence_missing[:50],
    }
    return {
        "available": True,
        "ok": not mismatch and not hits_without_contrib and preflight_ok and packet_grounded_ok and conservation_ok and external_credit_evidence_ok,
        "expected_hit_count": expected_hit_count,
        "parsed_hit_count": parsed_hit_count,
        "hit_count_mismatch": mismatch,
        "hits_without_rdps_contributions": hits_without_contrib[:50],
        "external_buff_hit_count": sum(1 for hit in hits if len(hit.get("rdps_contributions") or []) > 1),
        "dpd_count": 0,
        "baseline_count": 0,
        "packet_grounded_hit_count": packet_grounded_hit_count,
        "packet_final_value_count": packet_final_value_count,
        "rdps_conservation_ok": conservation_ok,
        "rdps_conservation_delta": basis.get("rdps_conservation_delta") if basis else None,
        "hit_conservation_mismatch_count": basis.get("hit_conservation_mismatch_count") if basis else None,
        "rdps_damage_basis": basis,
        "rdps_preflight": preflight,
        "rdps_proof": rdps_proof,
        "rdps_trust_audit": _build_rdps_trust_audit(report, root=root, proof=rdps_proof),
        "total_contribution_by_char": total_contribution_by_char.most_common(),
        "sample_hits": sample_hits,
    }


def _latest_timer_window(lines: list[str]) -> tuple[int | None, int | None]:
    start_idx: int | None = None
    end_idx: int | None = None
    for idx, raw_line in enumerate(lines):
        line = _line_payload(raw_line)
        if "GAME_TIMER_START" in line:
            start_idx = idx
            end_idx = None
        elif "GAME_TIMER_END" in line and start_idx is not None:
            end_idx = idx
    return start_idx, end_idx


_PARSER_CONTEXT_PRELUDE_LINES = 5000
_PARSER_CONTEXT_MARKERS = (
    "LOADOUT reason=",
    "LOADOUT_STATS ",
    "LOADOUT slot=",
    "DUNGEON_CONTEXT ",
    "OFFICIAL_TIMER_START",
    "OFFICIAL_TIMER_END",
    " SQUAD ",
    " ENTITY_STATS ",
)


def _focused_parser_context_text(
    raw_lines: list[str],
    *,
    event_start_idx: int,
    event_end_idx: int,
) -> str:
    if not raw_lines:
        return ""
    max_idx = len(raw_lines) - 1
    event_start_idx = max(0, min(event_start_idx, max_idx))
    event_end_idx = max(event_start_idx, min(event_end_idx, max_idx))
    if event_start_idx == 0:
        return "\n".join(raw_lines[: event_end_idx + 1])

    keep_indices: set[int] = set(range(event_start_idx, event_end_idx + 1))
    prelude_start = max(0, event_start_idx - _PARSER_CONTEXT_PRELUDE_LINES)
    keep_indices.update(range(prelude_start, event_start_idx))
    for idx, raw_line in enumerate(raw_lines[:event_start_idx]):
        if any(marker in raw_line for marker in _PARSER_CONTEXT_MARKERS):
            keep_indices.add(idx)
    # ChallengeComplete is normally emitted shortly after GAME_TIMER_END. Keep
    # that authoritative settlement marker in the parser context so its hit
    # window and the static audit cover the same completed battle.
    for idx in range(event_end_idx + 1, len(raw_lines)):
        raw_line = raw_lines[idx]
        if "OFFICIAL_TIMER_START" in raw_line or "GAME_TIMER_START" in raw_line:
            break
        if "OFFICIAL_TIMER_END" in raw_line:
            keep_indices.add(idx)
            break
    return "\n".join(raw_lines[idx] for idx in sorted(keep_indices))


def audit_trace(trace_file: Path | None = None, *, root: Path | None = None) -> dict[str, Any]:
    root = root or bundle_root()
    trace_file = trace_file or default_trace_file()
    buff_semantics = _load_buff_semantics(root)
    equipment_semantics = _load_equipment_semantics(root)
    packet_mappings = _load_packet_mappings(root)
    packet_mappings_by_canonical = _load_packet_mappings_by_canonical(packet_mappings)
    num_id_str_buff_map = _load_num_id_str_buff_map(root)
    mechanism_mappings_by_buff_id = _load_mechanism_mappings_by_buff_id(root)
    packet_bundle_buff_ids = _load_packet_bundle_buff_ids(root)
    known_non_rdps_registry = _load_rdps_known_non_rdps_registry(root)
    runtime_truth_jsonl_path = _default_truth_jsonl_file()
    # A global sidecar and nearby modification time do not bind it to this trace.
    runtime_truth_buff_uid_map = {}
    runtime_truth_db_path = _default_runtime_truth_db_file()
    runtime_truth_db = {}
    runtime_truth_known_buff_ids = set()
    runtime_truth_session_delta_sec: float | None = None
    runtime_truth_session_mismatch = False

    loadout_rows: list[dict[str, Any]] = []
    squad_members: list[str] = []
    buffs_by_uid: dict[str, BuffSeen] = {}
    buff_start_count = 0
    buff_end_count = 0
    damage_count = 0
    player_enemy_damage_count = 0
    damage_by_src: Counter[str] = Counter()
    unresolved_damage_refs: Counter[str] = Counter()
    line_type_counts: Counter[str] = Counter()

    if not trace_file.exists():
        return {
            "ok": False,
            "trace_file": str(trace_file),
            "error": "trace file does not exist",
        }

    if runtime_truth_jsonl_path.exists():
        runtime_truth_session_delta_sec = runtime_truth_jsonl_path.stat().st_mtime - trace_file.stat().st_mtime
        runtime_truth_session_mismatch = abs(runtime_truth_session_delta_sec) > 300

    raw_lines = trace_file.read_text(encoding="utf-8", errors="replace").splitlines()
    timer_start_idx, timer_end_idx = _latest_timer_window(raw_lines)
    event_start_idx = timer_start_idx if timer_start_idx is not None else 0
    event_end_idx = timer_end_idx if timer_end_idx is not None else len(raw_lines) - 1

    for idx, raw_line in enumerate(raw_lines[: event_end_idx + 1]):
        line = _line_payload(raw_line)
        in_event_window = event_start_idx <= idx <= event_end_idx
        if "LOADOUT slot=" in line:
            match = _LOADOUT_SLOT_RE.search(line)
            if match:
                groups = match.groupdict()
                loadout_rows.append(
                    {
                        "slot": int(groups["slot"]),
                        "char": groups["char"],
                        "weapon_template": groups["weapon"],
                        "weapon_lv": int(groups["weapon_lv"]),
                        "refine": int(groups["refine"]) if groups["refine"] != "unknown" else None,
                        "breakthrough": int(groups["breakthrough"]),
                        "attached_gem": int(groups["attached_gem"]),
                        "equip_count": groups["equip_inst"].count("["),
                        "equips": groups["equips"],
                        "equip_suit": groups["equip_suit"],
                    }
                )
                line_type_counts["loadout_slot"] += 1
            continue

        squad_match = _SQUAD_RE.search(line)
        if squad_match:
            squad_members = [item for item in squad_match.group("members").split() if item]
            line_type_counts["squad"] += 1
            continue

        if "GAME_TIMER_START" in line:
            if in_event_window:
                line_type_counts["timer_start"] += 1
            continue
        if "GAME_TIMER_END" in line:
            if in_event_window:
                line_type_counts["timer_end"] += 1
            continue

        if not in_event_window:
            continue

        buff_match = _BUFF_START_RE.search(line)
        if buff_match:
            buff_start_count += 1
            data = buff_match.groupdict()
            raw_buff_id = data["buff_id"]
            uid = data["uid"]
            runtime_truth_canonical = runtime_truth_buff_uid_map.get(uid, "")
            static_canonical = _canonical_static_buff_id(
                raw_buff_id,
                owner=data["owner"],
                src=data["src"],
                num_id_str_buff_map=num_id_str_buff_map,
            )
            buffs_by_uid[data["uid"]] = BuffSeen(
                buff_id=runtime_truth_canonical or static_canonical or raw_buff_id,
                raw_buff_id=raw_buff_id,
                uid=uid,
                owner=data["owner"],
                src=data["src"],
                runtime_truth_canonical=runtime_truth_canonical,
            )
            line_type_counts["buff_start"] += 1
            continue

        bb_match = _BB_RE.search(line)
        if bb_match:
            uid = bb_match.group("uid")
            seen = buffs_by_uid.get(uid)
            if seen is not None:
                values = _bb_values(bb_match.group("body"))
                seen.bb_keys.update(values)
                seen.bb_values.update(values)
            line_type_counts["bb"] += 1
            continue

        end_match = _BUFF_END_RE.search(line)
        if end_match:
            buff_end_count += 1
            uid = end_match.group("uid")
            seen = buffs_by_uid.get(uid)
            if seen is not None:
                seen.ended = True
            line_type_counts["buff_end"] += 1
            continue

        hp_match = _HP_RE.search(line)
        if hp_match:
            damage_count += 1
            data = hp_match.groupdict()
            damage_by_src[data["src"]] += int(data["hit"])
            is_player_enemy_damage = (
                _CHAR_KEY_RE.search(data["src"])
                or _CHAR_KEY_RE.search(data["atk"])
            ) and _ENEMY_KEY_RE.search(data["tgt"])
            if is_player_enemy_damage:
                player_enemy_damage_count += 1
                for field in ("src", "tgt", "atk"):
                    if data[field] in {"?", "id_0"} or data[field].startswith("id_"):
                        unresolved_damage_refs[field] += 1
            line_type_counts["hp_v2"] += 1
            continue

    unique_buff_ids = Counter(seen.buff_id for seen in buffs_by_uid.values())
    active_suits = _active_suits_by_char(loadout_rows, equipment_semantics)
    active_weapons = _active_weapons_by_char(loadout_rows)
    unresolved_buff_ids: Counter[str] = Counter()
    unresolved_rdps_effect_ids: Counter[str] = Counter()
    unresolved_potential_rdps_effect_ids: Counter[str] = Counter()
    unresolved_utility_or_marker_ids: Counter[str] = Counter()
    unresolved_unknown_blackboard_ids: Counter[str] = Counter()
    unresolved_no_blackboard_ids: Counter[str] = Counter()
    unresolved_orphan_actor_ids: Counter[str] = Counter()
    rdps_candidate_buff_ids: Counter[str] = Counter()
    equipment_candidate_buff_ids: Counter[str] = Counter()
    accepted_packet_mapped_buff_ids: Counter[str] = Counter()
    accepted_packet_mapping_examples: dict[str, dict[str, Any]] = {}
    accepted_known_non_rdps_buff_ids: Counter[str] = Counter()
    accepted_runtime_truth_buff_ids: Counter[str] = Counter()
    accepted_static_table_buff_ids: Counter[str] = Counter()
    buff_ids_with_bb: Counter[str] = Counter()
    unresolved_examples: dict[str, dict[str, Any]] = {}
    equipment_matches: dict[str, dict[str, Any]] = {}
    for seen in buffs_by_uid.values():
        buff_id = seen.buff_id
        raw_buff_id = seen.raw_buff_id
        entry = buff_semantics.get(buff_id)
        runtime_truth_known = bool(
            seen.runtime_truth_canonical
            or (buff_id and buff_id in runtime_truth_known_buff_ids)
        )
        packet_bundle_known = bool(buff_id and buff_id in packet_bundle_buff_ids)
        if seen.bb_keys:
            buff_ids_with_bb[buff_id] += 1
        if raw_buff_id != buff_id and buff_id in buff_semantics:
            accepted_static_table_buff_ids[raw_buff_id] += 1
        packet_mapping, _packet_mapping_key = _packet_mapping_for_seen(
            seen,
            packet_mappings=packet_mappings,
            packet_mappings_by_canonical=packet_mappings_by_canonical,
            mechanism_mappings_by_buff_id=mechanism_mappings_by_buff_id,
            active_suits=active_suits,
            active_weapons=active_weapons,
        )
        if packet_mapping is not None:
            accepted_packet_mapped_buff_ids[raw_buff_id] += 1
            accepted_packet_mapping_examples.setdefault(raw_buff_id, packet_mapping)
        if _known_non_rdps_seen(seen, known_non_rdps_registry):
            accepted_known_non_rdps_buff_ids[raw_buff_id or buff_id] += 1
        if runtime_truth_known:
            accepted_runtime_truth_buff_ids[buff_id or raw_buff_id] += 1
        equipment_candidates = _equipment_candidates(seen, active_suits, equipment_semantics)
        if equipment_candidates:
            equipment_candidate_buff_ids[raw_buff_id or buff_id] += 1
            equipment_matches.setdefault(
                raw_buff_id or buff_id or "<empty>",
                {
                    "raw_buff_id": raw_buff_id,
                    "runtime_truth_canonical": seen.runtime_truth_canonical,
                    "owner": seen.owner,
                    "src": seen.src,
                    "bb_values": seen.bb_values,
                    "candidates": equipment_candidates,
                },
            )
        if (
            not buff_id
            or buff_id == "unknown_buff"
            or (buff_id not in buff_semantics and packet_mapping is None and not runtime_truth_known and not packet_bundle_known)
        ):
            unresolved_key = raw_buff_id or seen.buff_id or "<empty>"
            classification, rdps_keys = _classify_unresolved_packet_buff(seen)
            unresolved_buff_ids[unresolved_key] += 1
            if classification == "rdps_effect":
                unresolved_rdps_effect_ids[unresolved_key] += 1
            elif classification == "potential_rdps_effect":
                unresolved_potential_rdps_effect_ids[unresolved_key] += 1
            elif classification == "utility_or_marker":
                unresolved_utility_or_marker_ids[unresolved_key] += 1
            elif classification == "unknown_blackboard":
                unresolved_unknown_blackboard_ids[unresolved_key] += 1
            elif classification == "orphan_actor":
                unresolved_orphan_actor_ids[unresolved_key] += 1
            else:
                unresolved_no_blackboard_ids[unresolved_key] += 1
            unresolved_examples.setdefault(
                unresolved_key,
                {
                    "raw_buff_id": raw_buff_id,
                    "resolved_buff_id": buff_id,
                    "owner": seen.owner,
                    "src": seen.src,
                    "classification": classification,
                    "rdps_relevant_bb_keys": rdps_keys,
                    "bb_keys": sorted(seen.bb_keys),
                    "bb_values": seen.bb_values,
                    "packet_mapping": packet_mapping,
                    "runtime_truth_known": runtime_truth_known,
                    "packet_bundle_known": packet_bundle_known,
                    "candidates": _candidate_semantics(seen, buff_semantics),
                    "equipment_candidates": equipment_candidates,
                },
            )
        if _has_rdps_semantics(entry):
            rdps_candidate_buff_ids[buff_id] += 1

    loadout_missing = []
    for row in loadout_rows[-4:]:
        missing = []
        if not row["weapon_template"] or row["weapon_template"] in {"unknown_weapon", "0"}:
            missing.append("weapon_template")
        if row["equip_count"] < 4:
            missing.append("equip_slots")
        if row["weapon_lv"] == 0:
            missing.append("weapon_lv")
        if missing:
            loadout_missing.append({"slot": row["slot"], "char": row["char"], "missing": missing})

    hard_blockers = []
    if damage_count == 0:
        hard_blockers.append("no_damage")
    if not squad_members:
        hard_blockers.append("no_squad")
    if len(loadout_rows[-4:]) < 4:
        hard_blockers.append("incomplete_loadout")
    if unresolved_damage_refs:
        hard_blockers.append("unresolved_damage_actor")
    if unresolved_buff_ids:
        hard_blockers.append("unresolved_buff_id")
    rdps_relevant_unresolved = dict(unresolved_rdps_effect_ids)
    rdps_potential_unresolved = dict(unresolved_potential_rdps_effect_ids)
    rdps_relevant_after_equipment = {
        key: count
        for key, count in rdps_relevant_unresolved.items()
        if key not in equipment_candidate_buff_ids
        and key not in accepted_packet_mapped_buff_ids
        and key not in accepted_known_non_rdps_buff_ids
    }
    rdps_potential_after_equipment = {
        key: count
        for key, count in rdps_potential_unresolved.items()
        if key not in equipment_candidate_buff_ids
        and key not in accepted_packet_mapped_buff_ids
        and key not in accepted_known_non_rdps_buff_ids
    }
    if rdps_relevant_after_equipment:
        hard_blockers.append("unresolved_rdps_effect_buff")
    if rdps_potential_after_equipment:
        hard_blockers.append("unresolved_potential_rdps_buff")
    parser_context_text = _focused_parser_context_text(
        raw_lines,
        event_start_idx=event_start_idx,
        event_end_idx=event_end_idx,
    )
    per_hit = _per_hit_rdps_audit(parser_context_text, root=root, expected_hit_count=player_enemy_damage_count)
    if per_hit.get("ok") is not True:
        hard_blockers.append("per_hit_rdps_parse")

    return {
        "ok": not hard_blockers,
        "trace_file": str(trace_file),
        "audit_window": {
            "timer_start_idx": timer_start_idx,
            "timer_end_idx": timer_end_idx,
            "event_start_idx": event_start_idx,
            "event_end_idx": event_end_idx,
        },
        "line_type_counts": dict(line_type_counts),
        "hard_blockers": hard_blockers,
        "coverage": {
            "damage_events": damage_count,
            "player_enemy_damage_events": player_enemy_damage_count,
            "buff_starts": buff_start_count,
            "buff_ends": buff_end_count,
            "unique_buff_instances": len(buffs_by_uid),
            "unique_buff_ids": len(unique_buff_ids),
            "squad_members": len(squad_members),
            "loadout_rows": len(loadout_rows[-4:]),
            "rdps_semantic_buff_ids": len(rdps_candidate_buff_ids),
            "equipment_semantic_buff_ids": len(equipment_candidate_buff_ids),
            "accepted_packet_mapped_buff_ids": len(accepted_packet_mapped_buff_ids),
            "accepted_runtime_truth_buff_ids": len(accepted_runtime_truth_buff_ids),
            "accepted_static_table_buff_ids": len(accepted_static_table_buff_ids),
        },
        "loadout": {
            "latest_rows": loadout_rows[-4:],
            "missing": loadout_missing,
            "active_suits": dict(active_suits),
            "active_weapons": active_weapons,
        },
        "runtime_truth": {
            "db_path": str(runtime_truth_db_path),
            "truth_jsonl_path": str(runtime_truth_jsonl_path),
            "known_buff_ids": len(runtime_truth_known_buff_ids),
            "buff_uid_mappings": len(runtime_truth_buff_uid_map),
            "session_delta_sec": runtime_truth_session_delta_sec,
            "session_mismatch": runtime_truth_session_mismatch,
        },
        "buffs": {
            "top_ids": unique_buff_ids.most_common(30),
            "with_blackboard": buff_ids_with_bb.most_common(30),
            "rdps_semantic_ids": rdps_candidate_buff_ids.most_common(30),
            "equipment_semantic_ids": equipment_candidate_buff_ids.most_common(30),
            "equipment_matches": equipment_matches,
            "accepted_packet_mapped_ids": [
                [
                    buff_id,
                    count,
                    accepted_packet_mapping_examples.get(buff_id, {}).get("canonical_buff_id"),
                    accepted_packet_mapping_examples.get(buff_id, {}).get("role"),
                    accepted_packet_mapping_examples.get(buff_id, {}).get("confidence"),
                ]
                for buff_id, count in accepted_packet_mapped_buff_ids.most_common()
            ],
            "accepted_known_non_rdps_ids": accepted_known_non_rdps_buff_ids.most_common(50),
            "accepted_runtime_truth_ids": accepted_runtime_truth_buff_ids.most_common(50),
            "accepted_static_table_ids": accepted_static_table_buff_ids.most_common(50),
            "unresolved_ids": unresolved_buff_ids.most_common(50),
            "unresolved_rdps_effect_ids": unresolved_rdps_effect_ids.most_common(50),
            "unresolved_potential_rdps_effect_ids": unresolved_potential_rdps_effect_ids.most_common(50),
            "unresolved_utility_or_marker_ids": unresolved_utility_or_marker_ids.most_common(50),
            "unresolved_unknown_blackboard_ids": unresolved_unknown_blackboard_ids.most_common(50),
            "unresolved_no_blackboard_ids": unresolved_no_blackboard_ids.most_common(50),
            "unresolved_orphan_actor_ids": unresolved_orphan_actor_ids.most_common(50),
            "unresolved_examples": unresolved_examples,
            "rdps_relevant_unresolved_ids": sorted(rdps_relevant_unresolved.items(), key=lambda item: (-item[1], item[0]))[:50],
            "rdps_potential_unresolved_ids": sorted(rdps_potential_unresolved.items(), key=lambda item: (-item[1], item[0]))[:50],
            "rdps_relevant_unresolved_after_equipment": sorted(
                rdps_relevant_after_equipment.items(),
                key=lambda item: (-item[1], item[0]),
            )[:50],
            "rdps_potential_unresolved_after_equipment": sorted(
                rdps_potential_after_equipment.items(),
                key=lambda item: (-item[1], item[0]),
            )[:50],
            "open_instance_count": sum(1 for seen in buffs_by_uid.values() if not seen.ended),
        },
        "damage": {
            "by_src": damage_by_src.most_common(20),
            "unresolved_refs": dict(unresolved_damage_refs),
            "per_hit_rdps": per_hit,
        },
        "notes": [
            "ok=true requires packet hit coverage, per-hit rDPS conservation, and no unresolved rDPS-effect buff after accepted static/equipment/packet/runtime mappings.",
            "equipment_semantic_ids are packet numeric buff ids matched by active LOADOUT suit plus exact blackboard value overlap.",
            "accepted_static_table_ids are runtime numeric buff ids resolved through NumIdStrTable before static semantics and mechanism mappings are checked.",
            "weapon_lv/refine/breakthrough may remain zero until full WEAPON_DATA sync is decoded; weapon/equip templates are the rDPS-critical part for static buff lookup.",
            "accepted_runtime_truth_buff_ids are canonical buff ids already confirmed by the endfield-dump runtime truth database; they are direct runtime truth, not numeric packet guesses.",
            "If runtime truth jsonl and dxg_trace.dat come from different sessions, uid->canonical joins will correctly load but still not match anything.",
        ],
    }


def _batch_trace_label(path_text: str) -> str:
    try:
        return Path(path_text).name or path_text
    except (TypeError, ValueError):
        return str(path_text)


def audit_trace_batch(trace_files: list[Path], *, root: Path | None = None) -> dict[str, Any]:
    root = root or bundle_root()
    unique_files: list[Path] = []
    seen_paths: set[str] = set()
    for raw_path in trace_files:
        path = Path(raw_path).resolve()
        key = str(path).lower()
        if key in seen_paths:
            continue
        seen_paths.add(key)
        unique_files.append(path)

    rows: list[dict[str, Any]] = []
    hard_blockers: Counter[str] = Counter()
    strict_blockers: Counter[tuple[str, str]] = Counter()
    strict_blocker_examples: dict[str, dict[str, Any]] = {}
    accepted_effects: Counter[str] = Counter()
    external_credit_by_buff: Counter[str] = Counter()
    external_credit_by_source: Counter[str] = Counter()
    errors: list[dict[str, Any]] = []

    for path in unique_files:
        row: dict[str, Any] = {
            "trace_file": str(path),
            "label": _batch_trace_label(str(path)),
            "ok": False,
        }
        try:
            audit = audit_trace(path, root=root)
        except Exception as exc:  # noqa: BLE001 - batch audit must keep processing other files.
            error = f"{type(exc).__name__}: {exc}"
            row["error"] = error
            errors.append({"trace_file": str(path), "error": error})
            rows.append(row)
            continue

        per_hit = (audit.get("damage") or {}).get("per_hit_rdps") or {}
        trust = per_hit.get("rdps_trust_audit") or {}
        coverage = audit.get("coverage") or {}
        row.update(
            {
                "ok": bool(audit.get("ok")) and bool(trust.get("ok", True)),
                "audit_ok": bool(audit.get("ok")),
                "trust_ok": bool(trust.get("ok", True)),
                "hard_blockers": list(audit.get("hard_blockers") or []),
                "damage_events": int(coverage.get("damage_events") or 0),
                "player_enemy_damage_events": int(coverage.get("player_enemy_damage_events") or 0),
                "buff_starts": int(coverage.get("buff_starts") or 0),
                "checked_external_buff_count": int(trust.get("checked_external_buff_count") or 0),
                "accepted_effect_buff_count": int(trust.get("accepted_effect_buff_count") or 0),
                "accepted_row_count": int(trust.get("accepted_row_count") or 0),
                "accepted_non_rdps_buff_count": int(trust.get("accepted_non_rdps_buff_count") or 0),
                "strict_blocker_count": int(trust.get("blocker_count") or 0),
                "external_credit_row_count": int(trust.get("credit_row_count") or 0),
            }
        )
        for blocker in row["hard_blockers"]:
            hard_blockers[str(blocker)] += 1
        for blocker in trust.get("blockers") or []:
            if not isinstance(blocker, dict):
                continue
            event_key = str(blocker.get("event_key") or "<unknown>")
            reason = str(blocker.get("reason") or "")
            strict_blockers[(event_key, reason)] += 1
            strict_blocker_examples.setdefault(
                f"{event_key}\n{reason}",
                {
                    "event_key": event_key,
                    "name": blocker.get("name"),
                    "reason": reason,
                    "source": blocker.get("source"),
                    "target": blocker.get("target"),
                    "source_skill": blocker.get("source_skill"),
                    "required_bb": blocker.get("required_bb"),
                    "unknown_bb": blocker.get("unknown_bb"),
                    "bb_keys": blocker.get("bb_keys"),
                    "trace_file": str(path),
                },
            )
        for accepted in trust.get("accepted_rows") or []:
            if isinstance(accepted, dict):
                event_key = str(accepted.get("event_key") or "<unknown>")
                accepted_effects[event_key] += 1
        for credit in trust.get("credit_rows") or []:
            if not isinstance(credit, dict):
                continue
            event_key = str(credit.get("event_key") or "<unknown>")
            source = str(credit.get("source") or "<unknown>")
            try:
                value = float(credit.get("value") or 0.0)
            except (TypeError, ValueError):
                value = 0.0
            external_credit_by_buff[event_key] += value
            external_credit_by_source[source] += value
        rows.append(row)

    ok_rows = sum(1 for row in rows if row.get("ok"))
    failed_rows = len(rows) - ok_rows
    return {
        "ok": failed_rows == 0,
        "root": str(root),
        "summary": {
            "trace_count": len(rows),
            "ok_count": ok_rows,
            "failed_count": failed_rows,
            "error_count": len(errors),
            "hard_blocker_file_count": sum(1 for row in rows if row.get("hard_blockers")),
            "strict_blocker_file_count": sum(1 for row in rows if int(row.get("strict_blocker_count") or 0) > 0),
            "strict_blocker_count": sum(int(row.get("strict_blocker_count") or 0) for row in rows),
            "checked_external_buff_count": sum(int(row.get("checked_external_buff_count") or 0) for row in rows),
            "accepted_effect_buff_count": sum(int(row.get("accepted_effect_buff_count") or 0) for row in rows),
            "accepted_non_rdps_buff_count": sum(int(row.get("accepted_non_rdps_buff_count") or 0) for row in rows),
        },
        "files": rows,
        "errors": errors,
        "hard_blockers": hard_blockers.most_common(),
        "strict_blockers": [
            {
                "event_key": event_key,
                "reason": reason,
                "count": count,
                "example": strict_blocker_examples.get(f"{event_key}\n{reason}") or {},
            }
            for (event_key, reason), count in strict_blockers.most_common()
        ],
        "accepted_effects": accepted_effects.most_common(100),
        "external_credit_by_buff": external_credit_by_buff.most_common(100),
        "external_credit_by_source": external_credit_by_source.most_common(100),
    }


def audit_truth_jsonl(truth_jsonl: Path | None = None, *, root: Path | None = None) -> dict[str, Any]:
    root = root or bundle_root()
    truth_jsonl = truth_jsonl or _default_truth_jsonl_file()
    buff_semantics = _load_buff_semantics(root)
    equipment_semantics = _load_equipment_semantics(root)

    if not truth_jsonl.exists():
        return {
            "ok": False,
            "truth_jsonl": str(truth_jsonl),
            "error": "truth jsonl does not exist",
        }

    rows = _parse_truth_jsonl_rows(truth_jsonl)
    row_type_counts: Counter[str] = Counter()
    latest_loadout_by_slot: dict[int, dict[str, Any]] = {}
    latest_squad_members: list[str] = []
    actor_by_key: dict[str, str] = {}
    actor_by_inst: dict[str, str] = {}
    skill_by_key: dict[str, str] = {}
    buffs_by_uid: dict[str, BuffSeen] = {}
    damage_by_src: Counter[str] = Counter()
    damage_by_skill: Counter[str] = Counter()
    unresolved_damage_refs: Counter[str] = Counter()
    damage_count = 0
    player_enemy_damage_count = 0

    for row in rows:
        row_type = str(row.get("type") or "")
        row_type_counts[row_type] += 1

        if row_type == "TRUTH_SQUAD":
            latest_squad_members = _parse_truth_squad_roster(row.get("roster"))
            continue

        if row_type == "TRUTH_LOADOUT":
            slot = int(row.get("slot") or 0)
            canonical = str(row.get("canonical") or "")
            latest_loadout_by_slot[slot] = {
                "slot": slot,
                "char": canonical,
                "weapon_template": str(row.get("weaponTemplate") or ""),
                "weapon_lv": 0,
                "refine": 0,
                "breakthrough": 0,
                "attached_gem": 0,
                "equip_count": str(row.get("equipInsts") or "").count("["),
                "equips": str(row.get("equips") or ""),
                "equip_suit": str(row.get("equipSuit") or ""),
                "potential": int(row.get("potential") or 0),
            }
            actor_inst = row.get("actorInstId")
            if actor_inst is not None and canonical:
                actor_by_inst[str(actor_inst)] = canonical
            continue

        if row_type == "TRUTH_CONTAINER_ACTOR":
            actor_inst = row.get("actorInstId")
            canonical = str(row.get("canonical") or "")
            if actor_inst is not None and canonical:
                actor_by_inst[str(actor_inst)] = canonical
            continue

        if row_type == "TRUTH_ACTOR":
            actor_key = str(row.get("actorKey") or "")
            canonical = str(row.get("canonical") or "")
            if actor_key and canonical:
                actor_by_key[actor_key] = canonical
            continue

        if row_type == "TRUTH_SKILL":
            skill_key = str(row.get("skillKey") or "")
            skill = str(row.get("canonical") or "")
            owner_key = str(row.get("ownerKey") or "")
            if skill_key and skill:
                skill_by_key[skill_key] = skill
            if owner_key and skill:
                owner = _char_prefix(skill)
                if owner:
                    actor_by_key.setdefault(owner_key, owner)
            continue

        if row_type == "TRUTH_BUFF":
            uid = str(row.get("instUid") or "")
            if not uid:
                continue
            canonical = str(row.get("canonical") or "")
            owner = str(row.get("owner") or "")
            src = str(row.get("src") or "")
            phase = str(row.get("phase") or "")
            seen = buffs_by_uid.get(uid)
            if seen is None:
                seen = BuffSeen(
                    buff_id=canonical or "unknown_buff",
                    raw_buff_id=canonical or "unknown_buff",
                    uid=uid,
                    owner=owner,
                    src=src,
                    runtime_truth_canonical=canonical,
                )
                buffs_by_uid[uid] = seen
            else:
                if canonical:
                    seen.buff_id = canonical
                    seen.raw_buff_id = canonical
                    seen.runtime_truth_canonical = canonical
                if owner:
                    seen.owner = owner
                if src:
                    seen.src = src
            if phase == "BUFF_END":
                seen.ended = True
            continue

        if row_type == "TRUTH_DAMAGE":
            damage_count += 1
            atk_key = str(row.get("atkKey") or "")
            tgt_key = str(row.get("tgtKey") or "")
            skill = str(row.get("skill") or "")
            atk = actor_by_key.get(atk_key, "")
            tgt = actor_by_key.get(tgt_key, "")
            if not atk and skill:
                atk = _char_prefix(skill) or ""
            raw = float(row.get("raw") or 0.0)
            damage_by_src[atk or atk_key or "?"] += int(raw + 0.5)
            damage_by_skill[skill or "<empty>"] += int(raw + 0.5)
            if (atk.startswith("chr_") or skill.startswith("chr_")) and tgt.startswith("eny_"):
                player_enemy_damage_count += 1
            if not atk:
                unresolved_damage_refs["atk"] += 1
            if not tgt:
                unresolved_damage_refs["tgt"] += 1
            continue

    loadout_rows = [latest_loadout_by_slot[index] for index in sorted(latest_loadout_by_slot)]
    active_suits = _active_suits_by_char(loadout_rows, equipment_semantics)
    active_weapons = _active_weapons_by_char(loadout_rows)

    unique_buff_ids = Counter(seen.buff_id for seen in buffs_by_uid.values())
    unresolved_buff_ids: Counter[str] = Counter()
    rdps_candidate_buff_ids: Counter[str] = Counter()
    equipment_truth_buff_ids: Counter[str] = Counter()
    buff_examples: dict[str, dict[str, Any]] = {}

    for seen in buffs_by_uid.values():
        buff_id = seen.buff_id
        entry = buff_semantics.get(buff_id)
        if _has_rdps_semantics(entry):
            rdps_candidate_buff_ids[buff_id] += 1
        if buff_id.startswith("buff_equipsuit_"):
            equipment_truth_buff_ids[buff_id] += 1
        if not buff_id or buff_id == "unknown_buff":
            key = buff_id or "<empty>"
            unresolved_buff_ids[key] += 1
            buff_examples.setdefault(
                key,
                {
                    "owner": seen.owner,
                    "src": seen.src,
                    "runtime_truth_canonical": seen.runtime_truth_canonical,
                },
            )

    hard_blockers = []
    if damage_count == 0:
        hard_blockers.append("no_truth_damage")
    if not loadout_rows:
        hard_blockers.append("no_truth_loadout")
    if not latest_squad_members:
        hard_blockers.append("no_truth_squad")

    return {
        "ok": not hard_blockers,
        "truth_jsonl": str(truth_jsonl),
        "row_type_counts": dict(row_type_counts),
        "hard_blockers": hard_blockers,
        "coverage": {
            "damage_events": damage_count,
            "player_enemy_damage_events": player_enemy_damage_count,
            "buff_events": row_type_counts.get("TRUTH_BUFF", 0),
            "unique_buff_instances": len(buffs_by_uid),
            "unique_buff_ids": len(unique_buff_ids),
            "squad_members": len(latest_squad_members),
            "loadout_rows": len(loadout_rows),
            "unique_skills": len({value for value in skill_by_key.values() if value}),
            "unique_actors": len({value for value in actor_by_key.values() if value} | {value for value in actor_by_inst.values() if value}),
            "rdps_semantic_buff_ids": len(rdps_candidate_buff_ids),
            "equipment_truth_buff_ids": len(equipment_truth_buff_ids),
            "unresolved_buff_ids": len(unresolved_buff_ids),
        },
        "runtime_truth": {
            "truth_jsonl_path": str(truth_jsonl),
            "row_count": len(rows),
        },
        "loadout": {
            "latest_rows": loadout_rows,
            "active_suits": dict(active_suits),
            "active_weapons": active_weapons,
        },
        "buffs": {
            "top_ids": unique_buff_ids.most_common(40),
            "rdps_semantic_ids": rdps_candidate_buff_ids.most_common(40),
            "equipment_truth_ids": equipment_truth_buff_ids.most_common(40),
            "unresolved_ids": unresolved_buff_ids.most_common(40),
            "examples": buff_examples,
        },
        "damage": {
            "by_src": damage_by_src.most_common(20),
            "by_skill": damage_by_skill.most_common(20),
            "unresolved_refs": dict(unresolved_damage_refs),
        },
        "notes": [
            "truth-jsonl audit reads canonical runtime truth rows directly and does not depend on dxg_trace.dat.",
            "BUFF canonical ids come from runtime_hook_truth, so numeric packet ids are bypassed entirely on this path.",
            "This path is the safest way to evaluate rDPS-relevant truth coverage while loader method probes remain disabled.",
        ],
    }


def format_batch_audit_markdown(batch: dict[str, Any]) -> str:
    summary = batch.get("summary") or {}
    lines = [
        "# rDPS Batch Audit",
        "",
        f"- ok: `{str(batch.get('ok')).lower()}`",
        f"- root: `{batch.get('root', '')}`",
        f"- traces: {summary.get('trace_count', 0)}",
        f"- trusted / blocked: {summary.get('ok_count', 0)} / {summary.get('failed_count', 0)}",
        f"- strict blocker files: {summary.get('strict_blocker_file_count', 0)}",
        f"- strict blocker rows: {summary.get('strict_blocker_count', 0)}",
        f"- checked external buffs: {summary.get('checked_external_buff_count', 0)}",
        f"- accepted rDPS effects: {summary.get('accepted_effect_buff_count', 0)}",
        f"- accepted non-rDPS buffs: {summary.get('accepted_non_rdps_buff_count', 0)}",
    ]

    errors = batch.get("errors") or []
    if errors:
        lines.extend(["", "## Errors"])
        for item in errors[:30]:
            lines.append(f"- `{item.get('trace_file')}`: {item.get('error')}")

    files = batch.get("files") or []
    if files:
        lines.extend(
            [
                "",
                "## Files",
                "| 状态 | 文件 | 伤害行 | 外部 BUFF | 白名单 | 非 rDPS | strict block | hard blockers |",
                "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
            ]
        )
        for row in files[:120]:
            status = "可信" if row.get("ok") else "待审核"
            if row.get("error"):
                status = "错误"
            hard = ", ".join(str(item) for item in row.get("hard_blockers") or [])
            lines.append(
                "| "
                + " | ".join(
                    [
                        _md_cell(status),
                        _md_cell(row.get("label") or row.get("trace_file")),
                        _md_cell(row.get("player_enemy_damage_events", row.get("damage_events", 0))),
                        _md_cell(row.get("checked_external_buff_count", 0)),
                        _md_cell(row.get("accepted_effect_buff_count", 0)),
                        _md_cell(row.get("accepted_non_rdps_buff_count", 0)),
                        _md_cell(row.get("strict_blocker_count", 0)),
                        _md_cell(hard),
                    ]
                )
                + " |"
            )
        if len(files) > 120:
            lines.append(f"- 其余文件省略：{len(files) - 120}")

    strict = batch.get("strict_blockers") or []
    if strict:
        lines.extend(
            [
                "",
                "## Strict Block 聚合",
                "| key | 次数 | 原因 | 示例来源 | 示例目标 | required BB | unknown BB | 示例文件 |",
                "| --- | ---: | --- | --- | --- | --- | --- | --- |",
            ]
        )
        for item in strict[:80]:
            example = item.get("example") or {}
            lines.append(
                "| "
                + " | ".join(
                    [
                        _md_cell(item.get("event_key")),
                        _md_cell(item.get("count")),
                        _md_cell(item.get("reason")),
                        _md_cell(example.get("source")),
                        _md_cell(example.get("target")),
                        _md_cell(example.get("required_bb")),
                        _md_cell(example.get("unknown_bb")),
                        _md_cell(_batch_trace_label(str(example.get("trace_file") or ""))),
                    ]
                )
                + " |"
            )
    else:
        lines.extend(["", "## Strict Block 聚合", "- strict block: 0"])

    hard = batch.get("hard_blockers") or []
    if hard:
        lines.extend(["", "## Hard Blockers"])
        for blocker, count in hard:
            lines.append(f"- `{blocker}`: {count}")

    accepted = batch.get("accepted_effects") or []
    if accepted:
        lines.extend(["", "## 白名单命中 Top"])
        for event_key, count in accepted[:40]:
            lines.append(f"- `{event_key}`: {count}")

    credit_by_buff = batch.get("external_credit_by_buff") or []
    if credit_by_buff:
        lines.extend(["", "## 外部 rDPS 归因 Top"])
        for event_key, value in credit_by_buff[:40]:
            lines.append(f"- `{event_key}`: {float(value):.1f}")

    credit_by_source = batch.get("external_credit_by_source") or []
    if credit_by_source:
        lines.extend(["", "## 来源归因 Top"])
        for source, value in credit_by_source[:20]:
            lines.append(f"- `{source}`: {float(value):.1f}")

    return "\n".join(lines) + "\n"


def format_audit_markdown(audit: dict[str, Any]) -> str:
    lines = [
        f"# rDPS Audit",
        f"",
        f"- trace: `{audit.get('trace_file')}`",
        f"- ok: `{str(audit.get('ok')).lower()}`",
    ]
    blockers = audit.get("hard_blockers") or []
    if blockers:
        lines.append(f"- hard blockers: `{', '.join(blockers)}`")
    coverage = audit.get("coverage") or {}
    lines.extend(
        [
            "",
            "## Coverage",
            f"- damage events: {coverage.get('damage_events', 0)}",
            f"- player->enemy damage events: {coverage.get('player_enemy_damage_events', 0)}",
            f"- buff starts / ends: {coverage.get('buff_starts', 0)} / {coverage.get('buff_ends', 0)}",
            f"- unique buff instances / ids: {coverage.get('unique_buff_instances', 0)} / {coverage.get('unique_buff_ids', 0)}",
            f"- squad members: {coverage.get('squad_members', 0)}",
            f"- loadout rows: {coverage.get('loadout_rows', 0)}",
            f"- static rDPS semantic buff ids seen: {coverage.get('rdps_semantic_buff_ids', 0)}",
            f"- equipment semantic buff ids seen: {coverage.get('equipment_semantic_buff_ids', 0)}",
            f"- accepted packet mapped buff ids seen: {coverage.get('accepted_packet_mapped_buff_ids', 0)}",
            f"- accepted runtime truth buff ids seen: {coverage.get('accepted_runtime_truth_buff_ids', 0)}",
            f"- accepted static table buff ids seen: {coverage.get('accepted_static_table_buff_ids', 0)}",
        ]
    )
    runtime_truth = audit.get("runtime_truth") or {}
    if runtime_truth:
        lines.extend(
            [
                "",
                "## Runtime Truth",
                f"- db: `{runtime_truth.get('db_path', '')}`",
                f"- truth_jsonl: `{runtime_truth.get('truth_jsonl_path', '')}`",
                f"- known canonical buff ids: {runtime_truth.get('known_buff_ids', 0)}",
                f"- uid->canonical mappings loaded: {runtime_truth.get('buff_uid_mappings', 0)}",
            ]
        )
        if runtime_truth.get("session_delta_sec") is not None:
            lines.append(f"- trace/truth session delta sec: {runtime_truth.get('session_delta_sec'):.1f}")
        if runtime_truth.get("session_mismatch"):
            lines.append("- session mismatch: `true`")
    per_hit = (audit.get("damage") or {}).get("per_hit_rdps") or {}
    if per_hit:
        lines.append("")
        lines.append("## Per-Hit rDPS Parse")
        if per_hit.get("available") is False:
            lines.append(f"- parser: unavailable ({per_hit.get('error')})")
        else:
            lines.extend(
                [
                    f"- ok: `{str(per_hit.get('ok')).lower()}`",
                    f"- parsed hits / player->enemy hits: {per_hit.get('parsed_hit_count', 0)} / {per_hit.get('expected_hit_count', 0)}",
                    f"- packet-grounded rDPS hits: {per_hit.get('packet_grounded_hit_count', 0)} / {per_hit.get('parsed_hit_count', 0)}",
                    f"- packet final-value hits: {per_hit.get('packet_final_value_count', 0)} / {per_hit.get('parsed_hit_count', 0)}",
                    f"- rDPS conservation: `{str(per_hit.get('rdps_conservation_ok')).lower()}` (delta={per_hit.get('rdps_conservation_delta')})",
                    f"- hits without rDPS contribution rows: {len(per_hit.get('hits_without_rdps_contributions') or [])}",
                    f"- external-buff hits: {per_hit.get('external_buff_hit_count', 0)}",
                    f"- DPD / baseline rows: {per_hit.get('dpd_count', 0)} / {per_hit.get('baseline_count', 0)}",
                ]
            )
            totals = per_hit.get("total_contribution_by_char") or []
            if totals:
                preview = ", ".join(f"{char}={value:.1f}" for char, value in totals[:8])
                lines.append(f"- contribution totals: {preview}")
            proof = per_hit.get("rdps_proof") or {}
            if proof:
                lines.extend(
                    [
                        f"- proof preflight: `{str(proof.get('preflight_ok')).lower()}`",
                        f"- proof packet basis: `{str(proof.get('packet_damage_basis_ok')).lower()}`",
                        f"- proof per-hit conservation: `{str(proof.get('per_hit_conservation_ok')).lower()}`",
                        f"- proof external credit evidence: `{str(proof.get('external_credit_evidence_ok')).lower()}`",
                        f"- external contribution rows: {proof.get('external_contribution_rows', 0)}",
                    ]
                )
                preflight = per_hit.get("rdps_preflight") or {}
                if preflight:
                    lines.append(
                        f"- preflight checked external buffs: {preflight.get('checked_external_buff_count', 0)} "
                        f"(blockers={preflight.get('blocker_count', 0)})"
                    )
                missing = proof.get("external_evidence_missing") or []
                if missing:
                    lines.append(f"- missing external evidence rows: {len(missing)}")
                credit_by_buff = proof.get("external_credit_by_buff") or []
                if credit_by_buff:
                    preview = ", ".join(
                        f"{row.get('source_character_key')}:{row.get('event_key')}:{row.get('zone')}={float(row.get('value') or 0.0):.1f}"
                        for row in credit_by_buff[:6]
                    )
                    lines.append(f"- top external proof rows: {preview}")
            trust = per_hit.get("rdps_trust_audit") or {}
            if trust:
                lines.append("")
                lines.append("## rDPS 可信度审计")
                lines.extend(
                    [
                        f"- 可信状态: `{'可信' if trust.get('ok') else '不可信/待审核'}`",
                        f"- 检查外部 BUFF: {trust.get('checked_external_buff_count', 0)}",
                        f"- 白名单效果通过(preflight): {trust.get('accepted_effect_buff_count', 0)}",
                        f"- 白名单明细行: {trust.get('accepted_row_count', 0)}",
                        f"- 已识别非 rDPS: {trust.get('accepted_non_rdps_buff_count', 0)}",
                        f"- strict block: {trust.get('blocker_count', 0)}",
                        f"- 实际产生外部归因的 buff 行: {trust.get('credit_row_count', 0)}",
                    ]
                )
                accepted_rows = trust.get("accepted_rows") or []
                if accepted_rows:
                    lines.extend(
                        [
                            "",
                            "### 白名单命中并进入外部 rDPS 窗口",
                            "| 名称 | key | 来源 | 目标 | 效果 | 运行时 BB | 外部归因 |",
                            "| --- | --- | --- | --- | --- | --- | ---: |",
                        ]
                    )
                    for row in accepted_rows[:30]:
                        lines.append(
                            "| "
                            + " | ".join(
                                [
                                    _md_cell(row.get("name")),
                                    _md_cell(row.get("event_key")),
                                    _md_cell(row.get("source")),
                                    _md_cell(row.get("target")),
                                    _md_cell(row.get("effects")),
                                    _md_cell(row.get("runtime_bb")),
                                    _md_cell(f"{float(row.get('external_credit') or 0.0):.1f}"),
                                ]
                            )
                            + " |"
                        )
                    if len(accepted_rows) > 30:
                        lines.append(f"- 其余白名单命中行省略：{len(accepted_rows) - 30}")
                blocker_rows = trust.get("blockers") or []
                if blocker_rows:
                    lines.extend(
                        [
                            "",
                            "### Strict Block",
                            "| 名称 | key | 来源 | 目标 | 父技能/来源技能 | 原因 | required BB | unknown BB | 实包 BB |",
                            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
                        ]
                    )
                    for row in blocker_rows[:40]:
                        lines.append(
                            "| "
                            + " | ".join(
                                [
                                    _md_cell(row.get("name")),
                                    _md_cell(row.get("event_key")),
                                    _md_cell(row.get("source")),
                                    _md_cell(row.get("target")),
                                    _md_cell(row.get("source_skill")),
                                    _md_cell(row.get("reason")),
                                    _md_cell(row.get("required_bb")),
                                    _md_cell(row.get("unknown_bb")),
                                    _md_cell(row.get("runtime_bb") or row.get("bb_keys")),
                                ]
                            )
                            + " |"
                        )
                credit_rows = trust.get("credit_rows") or []
                if credit_rows:
                    lines.extend(
                        [
                            "",
                            "### 已产生外部 rDPS 归因",
                            "| 来源 | key | 乘区 | 归因伤害 |",
                            "| --- | --- | --- | ---: |",
                        ]
                    )
                    for row in credit_rows[:30]:
                        lines.append(
                            "| "
                            + " | ".join(
                                [
                                    _md_cell(row.get("source")),
                                    _md_cell(row.get("event_key")),
                                    _md_cell(row.get("zone")),
                                    _md_cell(f"{float(row.get('value') or 0.0):.1f}"),
                                ]
                            )
                            + " |"
                        )
    active_suits = (audit.get("loadout") or {}).get("active_suits") or {}
    active_weapons = (audit.get("loadout") or {}).get("active_weapons") or {}
    if active_weapons:
        lines.append("")
        lines.append("## Active Weapons")
        for char, weapon in active_weapons.items():
            lines.append(f"- {char}: {weapon}")
    if active_suits:
        lines.append("")
        lines.append("## Active Equip Suits")
        for char, suits in active_suits.items():
            labels = [
                f"{item.get('suit_id')}x{item.get('count')}({item.get('source')})"
                for item in suits
            ]
            lines.append(f"- {char}: {', '.join(labels)}")
    loadout_missing = (audit.get("loadout") or {}).get("missing") or []
    if loadout_missing:
        lines.append("")
        lines.append("## Loadout Gaps")
        for item in loadout_missing:
            lines.append(f"- slot {item.get('slot')} {item.get('char')}: {', '.join(item.get('missing') or [])}")
    equipment_ids = (audit.get("buffs") or {}).get("equipment_semantic_ids") or []
    if equipment_ids:
        lines.append("")
        lines.append("## Equipment Semantic Matches")
        matches = (audit.get("buffs") or {}).get("equipment_matches") or {}
        for buff_id, count in equipment_ids[:20]:
            example = matches.get(buff_id) or {}
            candidates = example.get("candidates") or []
            if candidates:
                best = candidates[0]
                lines.append(
                    f"- `{buff_id}`: {count} -> `{best.get('id')}` "
                    f"char={best.get('char')} keys={','.join(best.get('matched_keys') or [])} "
                    f"confidence={best.get('confidence')}"
                )
            else:
                lines.append(f"- `{buff_id}`: {count}")
    accepted = (audit.get("buffs") or {}).get("accepted_packet_mapped_ids") or []
    if accepted:
        lines.append("")
        lines.append("## Accepted Packet Mappings")
        for row in accepted[:30]:
            buff_id, count, canonical, role, *rest = row
            confidence = rest[0] if rest else None
            confidence_text = f" confidence={confidence}" if confidence else ""
            lines.append(f"- `{buff_id}`: {count} -> `{canonical}` role={role}{confidence_text}")
    accepted_runtime_truth = (audit.get("buffs") or {}).get("accepted_runtime_truth_ids") or []
    if accepted_runtime_truth:
        lines.append("")
        lines.append("## Accepted Runtime Truth IDs")
        for buff_id, count in accepted_runtime_truth[:30]:
            lines.append(f"- `{buff_id}`: {count}")
    accepted_static_table = (audit.get("buffs") or {}).get("accepted_static_table_ids") or []
    if accepted_static_table:
        lines.append("")
        lines.append("## Accepted Static Table IDs")
        for buff_id, count in accepted_static_table[:30]:
            lines.append(f"- `{buff_id}`: {count}")
    rdps_unresolved = (audit.get("buffs") or {}).get("unresolved_rdps_effect_ids") or []
    if rdps_unresolved:
        lines.append("")
        lines.append("## rDPS-Relevant Unresolved Buff IDs")
        examples = (audit.get("buffs") or {}).get("unresolved_examples") or {}
        for buff_id, count in rdps_unresolved[:20]:
            example = examples.get(buff_id) or {}
            keys = ",".join(example.get("rdps_relevant_bb_keys") or [])
            lines.append(f"- `{buff_id}`: {count} keys={keys}")
            candidates = example.get("candidates") or []
            if candidates:
                best = candidates[0]
                lines.append(
                    f"  candidate: `{best.get('id')}` score={best.get('score')} "
                    f"keys={','.join(best.get('overlap_keys') or [])}"
                )
    rdps_potential = (audit.get("buffs") or {}).get("unresolved_potential_rdps_effect_ids") or []
    if rdps_potential:
        lines.append("")
        lines.append("## Potential rDPS Unresolved Buff IDs")
        examples = (audit.get("buffs") or {}).get("unresolved_examples") or {}
        for buff_id, count in rdps_potential[:20]:
            example = examples.get(buff_id) or {}
            lines.append(
                f"- `{buff_id}`: {count} owner={example.get('owner')} src={example.get('src')} "
                f"keys={','.join(example.get('bb_keys') or [])}"
            )
    utility_unresolved = (audit.get("buffs") or {}).get("unresolved_utility_or_marker_ids") or []
    unknown_blackboard = (audit.get("buffs") or {}).get("unresolved_unknown_blackboard_ids") or []
    no_blackboard = (audit.get("buffs") or {}).get("unresolved_no_blackboard_ids") or []
    orphan_actor = (audit.get("buffs") or {}).get("unresolved_orphan_actor_ids") or []
    if utility_unresolved or unknown_blackboard or no_blackboard or orphan_actor:
        lines.append("")
        lines.append("## Non-rDPS / Low-Evidence Unresolved Buff IDs")
        for label, items in (
            ("utility_or_marker", utility_unresolved),
            ("unknown_blackboard", unknown_blackboard),
            ("no_blackboard", no_blackboard),
            ("orphan_actor", orphan_actor),
        ):
            if not items:
                continue
            preview = ", ".join(f"`{buff_id}`:{count}" for buff_id, count in items[:12])
            lines.append(f"- {label}: {preview}")
    unresolved = (audit.get("buffs") or {}).get("unresolved_ids") or []
    if unresolved:
        lines.append("")
        lines.append("## All Unresolved Buff IDs")
        examples = (audit.get("buffs") or {}).get("unresolved_examples") or {}
        for buff_id, count in unresolved[:20]:
            example = examples.get(buff_id) or {}
            classification = example.get("classification") or "unknown"
            lines.append(f"- `{buff_id}`: {count} class={classification}")
            candidates = (example or {}).get("candidates") or []
            if candidates:
                best = candidates[0]
                lines.append(
                    f"  candidate: `{best.get('id')}` score={best.get('score')} "
                    f"keys={','.join(best.get('overlap_keys') or [])}"
                )
            equipment_candidates = (example or {}).get("equipment_candidates") or []
            if equipment_candidates:
                best = equipment_candidates[0]
                lines.append(
                    f"  equip: `{best.get('id')}` score={best.get('score')} "
                    f"keys={','.join(best.get('matched_keys') or [])}"
                )
    damage_refs = (audit.get("damage") or {}).get("unresolved_refs") or {}
    if damage_refs:
        lines.append("")
        lines.append("## Damage Actor Gaps")
        for key, count in damage_refs.items():
            lines.append(f"- {key}: {count}")
    return "\n".join(lines) + "\n"


def format_truth_audit_markdown(audit: dict[str, Any]) -> str:
    lines = [
        "# rDPS Truth Audit",
        "",
        f"- truth_jsonl: `{audit.get('truth_jsonl')}`",
        f"- ok: `{str(audit.get('ok')).lower()}`",
    ]
    blockers = audit.get("hard_blockers") or []
    if blockers:
        lines.append(f"- hard blockers: `{', '.join(blockers)}`")
    coverage = audit.get("coverage") or {}
    lines.extend(
        [
            "",
            "## Coverage",
            f"- damage events: {coverage.get('damage_events', 0)}",
            f"- player->enemy damage events: {coverage.get('player_enemy_damage_events', 0)}",
            f"- buff events: {coverage.get('buff_events', 0)}",
            f"- unique buff instances / ids: {coverage.get('unique_buff_instances', 0)} / {coverage.get('unique_buff_ids', 0)}",
            f"- squad members: {coverage.get('squad_members', 0)}",
            f"- loadout rows: {coverage.get('loadout_rows', 0)}",
            f"- unique skills / actors: {coverage.get('unique_skills', 0)} / {coverage.get('unique_actors', 0)}",
            f"- static rDPS semantic buff ids seen: {coverage.get('rdps_semantic_buff_ids', 0)}",
            f"- equipment truth buff ids seen: {coverage.get('equipment_truth_buff_ids', 0)}",
            f"- unresolved buff ids: {coverage.get('unresolved_buff_ids', 0)}",
        ]
    )
    runtime_truth = audit.get("runtime_truth") or {}
    if runtime_truth:
        lines.extend(
            [
                "",
                "## Runtime Truth",
                f"- truth_jsonl: `{runtime_truth.get('truth_jsonl_path', '')}`",
                f"- row_count: {runtime_truth.get('row_count', 0)}",
            ]
        )
    active_weapons = (audit.get("loadout") or {}).get("active_weapons") or {}
    if active_weapons:
        lines.append("")
        lines.append("## Active Weapons")
        for char, weapon in active_weapons.items():
            lines.append(f"- {char}: {weapon}")
    active_suits = (audit.get("loadout") or {}).get("active_suits") or {}
    if active_suits:
        lines.append("")
        lines.append("## Active Equip Suits")
        for char, suits in active_suits.items():
            labels = [f"{item.get('suit_id')}x{item.get('count')}({item.get('source')})" for item in suits]
            lines.append(f"- {char}: {', '.join(labels)}")
    top_ids = (audit.get("buffs") or {}).get("top_ids") or []
    if top_ids:
        lines.append("")
        lines.append("## Top Buff IDs")
        for buff_id, count in top_ids[:30]:
            lines.append(f"- `{buff_id}`: {count}")
    rdps_ids = (audit.get("buffs") or {}).get("rdps_semantic_ids") or []
    if rdps_ids:
        lines.append("")
        lines.append("## rDPS Semantic Buff IDs")
        for buff_id, count in rdps_ids[:30]:
            lines.append(f"- `{buff_id}`: {count}")
    equip_ids = (audit.get("buffs") or {}).get("equipment_truth_ids") or []
    if equip_ids:
        lines.append("")
        lines.append("## Equipment Truth Buff IDs")
        for buff_id, count in equip_ids[:30]:
            lines.append(f"- `{buff_id}`: {count}")
    damage_by_src = (audit.get("damage") or {}).get("by_src") or []
    if damage_by_src:
        lines.append("")
        lines.append("## Damage By Source")
        for source, total in damage_by_src[:20]:
            lines.append(f"- `{source}`: {total}")
    unresolved = (audit.get("buffs") or {}).get("unresolved_ids") or []
    if unresolved:
        lines.append("")
        lines.append("## Unresolved Buff IDs")
        for buff_id, count in unresolved[:20]:
            lines.append(f"- `{buff_id}`: {count}")
    return "\n".join(lines) + "\n"


def format_truth_audit_html(audit: dict[str, Any], *, title: str = "Endfield rDPS Truth Audit") -> str:
    def esc(value: Any) -> str:
        return escape(str(value if value is not None else ""))

    def json_text(value: Any) -> str:
        return esc(json.dumps(value, ensure_ascii=False, indent=2))

    coverage = audit.get("coverage") or {}
    runtime_truth = audit.get("runtime_truth") or {}
    loadout = (audit.get("loadout") or {}).get("latest_rows") or []
    active_suits = (audit.get("loadout") or {}).get("active_suits") or {}
    active_weapons = (audit.get("loadout") or {}).get("active_weapons") or {}
    top_ids = (audit.get("buffs") or {}).get("top_ids") or []
    rdps_ids = (audit.get("buffs") or {}).get("rdps_semantic_ids") or []
    equipment_ids = (audit.get("buffs") or {}).get("equipment_truth_ids") or []
    unresolved_ids = (audit.get("buffs") or {}).get("unresolved_ids") or []
    damage_by_src = (audit.get("damage") or {}).get("by_src") or []
    damage_by_skill = (audit.get("damage") or {}).get("by_skill") or []

    metrics = [
        ("Damage Events", coverage.get("damage_events", 0)),
        ("Player->Enemy", coverage.get("player_enemy_damage_events", 0)),
        ("Buff Events", coverage.get("buff_events", 0)),
        ("Unique Buff Ids", coverage.get("unique_buff_ids", 0)),
        ("rDPS Semantic Buffs", coverage.get("rdps_semantic_buff_ids", 0)),
        ("Equipment Truth Buffs", coverage.get("equipment_truth_buff_ids", 0)),
        ("Unresolved Buffs", coverage.get("unresolved_buff_ids", 0)),
        ("Truth Rows", runtime_truth.get("row_count", 0)),
    ]

    def rows_table(rows: list[tuple[Any, Any]], headers: tuple[str, str]) -> str:
        body = "".join(
            f"<tr><td>{esc(left)}</td><td>{esc(right)}</td></tr>"
            for left, right in rows
        ) or '<tr><td colspan="2" class="muted">No data</td></tr>'
        return (
            '<table><thead><tr>'
            f"<th>{esc(headers[0])}</th><th>{esc(headers[1])}</th>"
            '</tr></thead><tbody>'
            f"{body}</tbody></table>"
        )

    loadout_cards = []
    for row in loadout:
        char = str(row.get("char") or "")
        suits = active_suits.get(char) or []
        suit_tags = " ".join(
            f'<span class="tag">{esc(item.get("suit_id"))} x{esc(item.get("count"))}</span>'
            for item in suits
        ) or '<span class="tag muted">No active suits</span>'
        loadout_cards.append(
            f"""
            <article class="card">
              <div class="card-title">{esc(char)}</div>
              <div class="muted">Weapon</div>
              <div>{esc(active_weapons.get(char, row.get('weapon_template', '')))}</div>
              <div class="muted" style="margin-top:8px">Suits</div>
              <div>{suit_tags}</div>
              <details>
                <summary>Raw</summary>
                <pre>{json_text(row)}</pre>
              </details>
            </article>
            """
        )
    loadout_html = "".join(loadout_cards) or '<div class="empty">No loadout rows.</div>'

    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<style>
:root {{
  color-scheme: dark;
  --bg: #15131f;
  --panel: #211d30;
  --panel2: #28233a;
  --line: #3a334d;
  --text: #ece7ff;
  --muted: #a79fbe;
  --accent: #46d6b5;
  --warn: #f5bf4f;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
  font-size: 14px;
}}
header {{
  padding: 18px 22px 10px;
  border-bottom: 1px solid var(--line);
  background: #1b1828;
}}
h1 {{ margin: 0 0 8px; font-size: 20px; }}
h2 {{ margin: 22px 0 10px; font-size: 16px; }}
.muted {{ color: var(--muted); }}
.wrap {{ padding: 16px 22px 32px; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; }}
.metric, .card, .table-wrap {{
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
}}
.metric {{ padding: 10px 12px; min-height: 64px; }}
.metric .label {{ color: var(--muted); font-size: 12px; margin-bottom: 6px; }}
.metric .value {{ font-size: 18px; font-weight: 700; }}
.card-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 10px; }}
.card {{ padding: 12px; }}
.card-title {{ font-weight: 700; margin-bottom: 8px; }}
.tag {{
  display: inline-flex;
  align-items: center;
  min-height: 20px;
  border-radius: 5px;
  padding: 2px 6px;
  margin: 1px 3px 1px 0;
  font-size: 12px;
  background: var(--panel2);
}}
table {{ width: 100%; border-collapse: collapse; table-layout: fixed; }}
th, td {{ border-bottom: 1px solid var(--line); padding: 8px 7px; text-align: left; vertical-align: top; overflow-wrap: anywhere; }}
th {{ color: var(--muted); background: #1a1725; font-weight: 600; }}
.table-wrap {{ overflow: auto; }}
details {{ margin-top: 8px; }}
summary {{ color: var(--accent); cursor: pointer; }}
pre {{ white-space: pre-wrap; word-break: break-word; margin: 8px 0 0; padding: 8px; background: #14111d; border: 1px solid var(--line); border-radius: 6px; color: #d8d1ef; max-height: 260px; overflow: auto; }}
.empty {{ padding: 16px; color: var(--muted); border: 1px dashed var(--line); border-radius: 8px; }}
@media (max-width: 820px) {{
  th, td {{ font-size: 12px; padding: 7px 5px; }}
}}
</style>
</head>
<body>
<header>
  <h1>{esc(title)}</h1>
  <div class="muted">{esc(audit.get('truth_jsonl'))}</div>
</header>
<main class="wrap">
  <section class="grid">
    {"".join(f'<div class="metric"><div class="label">{esc(label)}</div><div class="value">{esc(value)}</div></div>' for label, value in metrics)}
  </section>

  <section>
    <h2>Runtime Truth</h2>
    <div class="card">
      <div><span class="muted">truth_jsonl</span> {esc(runtime_truth.get('truth_jsonl_path', ''))}</div>
      <div><span class="muted">row_count</span> {esc(runtime_truth.get('row_count', 0))}</div>
    </div>
  </section>

  <section>
    <h2>Current Loadout</h2>
    <div class="card-grid">{loadout_html}</div>
  </section>

  <section>
    <h2>Top Buff IDs</h2>
    <div class="table-wrap">{rows_table(top_ids[:30], ("Buff ID", "Count"))}</div>
  </section>

  <section>
    <h2>rDPS Semantic Buff IDs</h2>
    <div class="table-wrap">{rows_table(rdps_ids[:30], ("Buff ID", "Count"))}</div>
  </section>

  <section>
    <h2>Equipment Truth Buff IDs</h2>
    <div class="table-wrap">{rows_table(equipment_ids[:30], ("Buff ID", "Count"))}</div>
  </section>

  <section>
    <h2>Damage By Source</h2>
    <div class="table-wrap">{rows_table(damage_by_src[:30], ("Source", "Total"))}</div>
  </section>

  <section>
    <h2>Damage By Skill</h2>
    <div class="table-wrap">{rows_table(damage_by_skill[:30], ("Skill", "Total"))}</div>
  </section>

  <section>
    <h2>Unresolved Buff IDs</h2>
    <div class="table-wrap">{rows_table(unresolved_ids[:30], ("Buff ID", "Count"))}</div>
  </section>
</main>
</body>
</html>
"""
    return html
