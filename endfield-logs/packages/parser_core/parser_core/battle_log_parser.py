from __future__ import annotations

import bisect
import copy
import gzip
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from math import isclose, isfinite, log
from pathlib import Path
from typing import Any

from parser_core.integrity import build_canonical_sha256
from parser_core.critical_context import build_critical_context, critical_buff_profile, update_critical_observation
from parser_core.timer_evidence import collect_timer_evidence, validate_timer_evidence

PARSER_VERSION = "raw-log-parser-v59"
RULES_VERSION = "raw-log-parser-v44"
UNKNOWN_DUNGEON_KEY = "unknown_dungeon"
UNKNOWN_DUNGEON_NAME = "未知副本"
UNKNOWN_ENEMY_KEY = "eny_0000_unknown"

_TIMESTAMP_RE = re.compile(r"^\[(\d{2}):(\d{2}):(\d{2})\.(\d{3})\]\s+")
_GAME_TIMER_START_RE = re.compile(r"\bGAME_TIMER_START\b")
_GAME_TIMER_END_RE = re.compile(r"\bGAME_TIMER_END\b")
_OFFICIAL_TIMER_START_RE = re.compile(r"\bOFFICIAL_TIMER_START\b")
_OFFICIAL_TIMER_END_RE = re.compile(r"\bOFFICIAL_TIMER_END\b")
_LIVE_TIMER_TICK_RE = re.compile(r"\bLIVE_TIMER_TICK\b")
_KV_RE = re.compile(r'(\w+)=(".*?"|\S+)')
_CHAR_KEY_RE = re.compile(r"(chr_\d{4}_[a-z0-9]+)")
_SQUAD_MEMBER_RE = re.compile(r"(chr_\d{4}_[a-z0-9]+)_(\d+)")
_ENEMY_KEY_RE = re.compile(r"(eny_\d{4}_[a-z0-9]+)")
_FULL_ENEMY_KEY_RE = re.compile(r"^(eny_\d{4}_[a-z0-9]+(?:_[a-z0-9]+)*)$")
_HIT_SEQ_RE = re.compile(r"\bHP_V2\s+#(\d+)\b")
_POISE_RE = re.compile(r"\bPOISE_V1\b")
_SHORT_ID_RE = re.compile(r"^id_(\d+)$")
_RUNTIME_SKILL_ID_RE = re.compile(r"^(?:skill_)?(\d+)$")
_COMPOSITE_RUNTIME_SKILL_RE = re.compile(r"^(chr_\d{4}_[a-z0-9]+)_skill_(\d+)$")
_ENDMIN_VARIANTS = {"chr_0002_endminm", "chr_0003_endminf"}
_STATIC_TRIGGER_DAMAGE_BUFF_RE = re.compile(r"^buff_[a-z0-9_]+(?:triggered|damage)[a-z0-9_]*$", re.IGNORECASE)
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
_LOADOUT_SLOT_RE = re.compile(
    r"\bLOADOUT\s+slot=(?P<slot>\d+)\s+char=(?P<char>\S+).*?"
    r"weaponTemplate=(?P<weapon>\S+).*?"
    r"equipSuit=(?P<equip_suit>\{.*\})"
)
_LOADOUT_FIELD_RE_TEMPLATE = r"\b{key}=([^\s]+)"

_BUFF_LABEL_PRIORITY = {
    "攻击提升": 0,
    "增伤": 1,
    "增幅": 2,
    "导电": 2,
    "腐蚀": 2,
    "燃烧": 2,
    "冻结": 2,
    "碎冰": 2,
    "法术爆发": 2,
    "破防": 2,
    "倒地": 2,
    "击飞": 2,
    "猛击": 2,
    "脆弱": 2,
    "物理脆弱": 2,
    "物理脆弱 / 碎甲": 2,
    "碎甲": 2,
    "易伤": 3,
    "承伤易伤": 3,
    "减抗": 4,
    "连击增伤": 5,
    "暴击": 6,
    "暴击伤害": 7,
}
_BUFF_NOISE_PREFIXES = (
    "buff_common_vfx_",
    "buff_common_normal_skill_fx_",
    "buff_common_combo_attack_fx",
    "buff_common_dash",
    "buff_common_dash_",
    "buff_common_full_immune_weak",
)
_BUFF_NOISE_IDS = {
    "buff_chr_0011_seraph_mainchr_heal",
    "buff_chr_0023_antal_ultimate_icon",
    "buff_chr_0023_antal_ultimate_icon_2",
    "buff_common_obtain_ultimate_sp",
    "buff_common_dash_behit_listener",
    "buff_common_poise_break_damage_taken_scale",
}
_BUFF_NOISE_BB_KEYS = {
    "atb",
    "atk_duration",
    "cd",
    "comboskill_cooldown",
    "common_character_perfect_dodge",
    "count",
    "damage_interval",
    "def_decrease_tick",
    "def_decrease_tick_final",
    "duration",
    "dodgeSkillId",
    "end_early",
    "heal_value",
    "healvalue",
    "hit_spellduration",
    "hp_up",
    "imbue_scale",
    "infliction_num",
    "max_stack",
    "max_def_decrease",
    "max_def_decrease_final",
    "phy_spell_up",
    "poise",
    "posie",
    "potential_1",
    "potential_3",
    "potential_3_atb",
    "rate_add",
    "shatter_dmg",
    "skill_bg_type",
    "speed",
    "stack_cond",
    "start_def_decrease",
    "time_warning",
    "usp_everyone",
    "usp_self",
    "vfx_buff_name",
}
_BUFF_EFFECT_BB_KEY_IGNORE_BY_ID = {
    # Seraph's ultimate uses atk_up as an internal base for elemental EnhancedAction.
    # The actual buffs are emitted separately as common_affixes_enhance_* children.
    "buff_chr_0011_seraph_atk_buff": {"atk_up"},
    # Seraph's heal-side potential wrapper passes atk_up into a child action. The
    # actual attack buff is emitted separately by the weapon buff.
    "buff_chr_0011_seraph_potential_1_atkup": {"atk_up"},
    # suit_atk02 stores child trigger parameters on the persistent wearer buff.
    # Only atk_up is a direct stat effect here; dmg_up belongs to the later
    # add-combo-damage child.
    "buff_equipsuit_atk_02": {"dmg_up", "max_stack"},
    # Fracture carries trigger damage parameters next to the actual debuff.
    # The rDPS effect is physical_res_down; atk_scale belongs to the triggered hit.
    "buff_physical_do_fracture": {"atk_scale"},
    # Mixed weapon buffs carry owner-side parameters next to external rDPS keys.
    "buff_wpn_funnel_0006_valid": {"atk_up"},
    "buff_wpn_funnel_0011_valid": {"nature_dmg_up"},
    "buff_wpn_sword_0012_atk_up": {"atk_up"},
    "buff_wpn_sword_0013_atk_up": {"atk_up", "atk_up_add", "atk_up_mult"},
    "buff_wpn_sword_0016_team": {"phy_dmg_up", "phy_dmg_up2"},
    "buff_wpn_sword_0016_valid": {"phy_dmg_up", "phy_dmg_up2"},
    "buff_wpn_sword_0020_cryst": {"atk_up"},
}
_ATTR_TYPE_BUFF_LABELS = {
    2: "攻击提升",
    9: "暴击",
    10: "暴击伤害",
    17: "增伤",
    28: "增伤",
    32: "增伤",
    33: "增伤",
    50: "增伤",
    51: "增伤",
    53: "增伤",
    65: "增幅",
    66: "增幅",
    67: "增幅",
    68: "增幅",
    70: "易伤",
}
_FRAGILE_ATTR_TYPES = set(range(70, 86))
_BUFF_MERGE_GAP_MS = 250
_BUFF_MIRROR_DEDUPE_WINDOW_MS = 500
_TIMELINE_SKILL_GROUP_GAP_MS = 1200
_TIMELINE_ULTIMATE_GROUP_GAP_MS = 5000
_ZONE_BY_LABEL = {
    "攻击提升": "atk",
    "增伤": "dmg_inc",
    "增幅": "amp",
    "脆弱": "fragile",
    "物理脆弱": "fragile",
    "物理脆弱 / 碎甲": "vuln_taken",
    "碎甲": "vuln_taken",
    "易伤": "vuln_taken",
    "承伤易伤": "vuln_taken",
    "减抗": "res",
    "连击增伤": "combo",
    "源石技艺强度": "arts_strength",
    "加速": "speedup",
    "缓速": "slow",
}
_EFFECT_ZONE_LABELS = {
    "atk": "攻击提升",
    "dmg_inc": "增伤",
    "amp": "增幅",
    "fragile": "脆弱",
    "vuln_taken": "易伤",
    "res": "减抗",
    "combo": "连击增伤",
    "arts_strength": "源石技艺强度",
    "crit": "暴击",
    "speedup": "加速",
    "slow": "缓速",
}
_RDPS_DEBUG_ZONE_LABELS = {
    "atk": "攻击",
    "dmg_inc": "增伤",
    "fragile": "脆弱",
    "vuln_taken": "易伤",
    "amp": "增幅",
    "res": "减抗",
    "combo": "连击",
    "crit": "暴击",
    "arts_strength": "源石技艺强度",
    "prod_dmg_inc": "独立增伤",
    "prod_vuln_taken": "独立承伤易伤",
}
_RDPS_DEBUG_ZONE_ORDER = {
    "atk": 0,
    "dmg_inc": 1,
    "fragile": 2,
    "vuln_taken": 3,
    "amp": 4,
    "res": 5,
    "combo": 6,
    "crit": 7,
    "arts_strength": 8,
    "prod_dmg_inc": 9,
    "prod_vuln_taken": 10,
}
_RDPS_ALLOCATABLE_ZONES = {"atk", "dmg_inc", "amp", "fragile", "vuln_taken", "res", "combo", "arts_strength",
                           "prod_dmg_inc", "prod_vuln_taken"}
_DPD_ZONE_BUCKETS = {
    "dmg_inc": ("atk", 1),
    "amp": ("atk", 3),
    "combo": ("atk", 4),
    "vuln_taken": ("def", 1),
    "fragile": ("def", 5),
}
_ELEMENTAL_ELEMENTS = {"fire", "pulse", "cryst", "natural", "ether"}
_ACTION_DAMAGE_TYPE_TO_ELEMENT = {
    "physical": "physical",
    "real": "real",
    "fire": "fire",
    "pulse": "pulse",
    "cryst": "cryst",
    "crystal": "cryst",
    "lifedrain": "lifedrain",
    "natural": "natural",
    "ether": "ether",
}
_CHAR_TYPE_TO_ELEMENT = {
    "物理": "physical",
    "灼热": "fire",
    "寒冷": "cryst",
    "电磁": "pulse",
    "自然": "natural",
    "Physical": "physical",
    "Fire": "fire",
    "Cryst": "cryst",
    "Pulse": "pulse",
    "Natural": "natural",
    "Ether": "ether",
}
_ENHANCED_ACTION_SUBTYPE_TO_ELEMENT = {
    "crystal": "cryst",
    "cryst": "cryst",
    "natural": "natural",
    "fire": "fire",
    "pulse": "pulse",
    "physical": "physical",
    "spell": "spell",
    "all": "all",
}
_ATTR_TYPE_TO_LABEL = {
    39: "力量",
    40: "敏捷",
    41: "智识",
    42: "意志",
}
_ATTR_LABEL_TO_TYPE = {value: key for key, value in _ATTR_TYPE_TO_LABEL.items()}
_DAMAGE_SCHOOL_BY_SPEC = {
    "CharacterNormalAttack": "physical",
    "CharacterPlungingAttack": "physical",
    "CharacterPowerAttack": "physical",
    "CharacterDashAttack": "physical",
    "CharacterNormalSkill": "spell",
    "CharacterUltimateSkill": "spell",
    "CharacterComboSkill": "spell",
}
_RATE_IGNORE_BUFF_PATTERNS = (
    re.compile(r"^buff_equipsuit_(?:usp_02|combo_cd01)$", re.IGNORECASE),
    re.compile(r"buff_common_affixes_slow", re.IGNORECASE),
    re.compile(r"^buff_chr_0023_antal_(?:normal_skill|tageffect|utimate_skill)$", re.IGNORECASE),
    re.compile(r"^buff_common_affixes_shelter_default_child$", re.IGNORECASE),
    re.compile(r"buff_common_affixes_vulnerable_spell", re.IGNORECASE),
    re.compile(
        r"^buff_common_(?:physical|fire|pulse|cryst|natural|spell)_"
        r"(?:physical|fire|pulse|cryst|natural|spell)_corrupt_triggered$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^buff_common_(?:try_)?(?:physical|fire|pulse|cryst|natural|spell)_triggered(?:_wrapper)?$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^buff_common_try_(?:physical|fire|pulse|cryst|natural|spell)"
        r"_(?:physical|fire|pulse|cryst|natural|spell)_triggered$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^buff_common_(?:physical|fire|pulse|cryst|natural|spell)"
        r"_(?:physical|fire|pulse|cryst|natural|spell)_triggered_wrapper$",
        re.IGNORECASE,
    ),
)
_BB_KEY_ELEMENT_RE = re.compile(r"(fire|pulse|crystal|cryst|natural|physical|physic|spell)")
_ATTR_TYPE_TO_EFFECT = {
    2: ("atk", "all"),
    9: ("crit", "all"),
    10: ("crit", "all"),
    17: ("dmg_inc", "all"),
    28: ("dmg_inc", "all"),
    32: ("dmg_inc", "all"),
    33: ("dmg_inc", "all"),
    50: ("dmg_inc", "physical"),
    51: ("dmg_inc", "fire"),
    # 52/54 依据 AttributeMetaTable 官方语义补齐（icon_pulse/natural_damage_increase），
    # 此前缺失导致电磁/自然系角色的属性快照增伤丢失。
    52: ("dmg_inc", "pulse"),
    53: ("dmg_inc", "cryst"),
    54: ("dmg_inc", "natural"),
    55: ("dmg_inc", "ether"),
    65: ("amp", "fire"),
    66: ("amp", "pulse"),
    67: ("amp", "cryst"),
    68: ("amp", "natural"),
    70: ("fragile", "physical"),
    71: ("fragile", "fire"),
    72: ("fragile", "pulse"),
    73: ("fragile", "cryst"),
    74: ("fragile", "natural"),
    75: ("fragile", "ether"),
}
_DEF_DECREASE_ATTR_TYPE_TO_EFFECT = {
    80: ("res", "physical"),
    81: ("res", "natural"),
    82: ("res", "cryst"),
    83: ("res", "pulse"),
    84: ("res", "fire"),
    85: ("res", "ether"),
}
_DAMAGE_TAG_TO_ELEMENT = {
    "pd": "physical",
    "fire": "fire",
    "cryst": "cryst",
    "pulse": "pulse",
    "natur": "natural",
    "natural": "natural",
}
_DPD_DAMAGE_TYPE_TO_ELEMENT = {
    0: "physical",
    1: "real",
    2: "fire",
    3: "pulse",
    4: "cryst",
    5: "lifedrain",
    6: "natural",
    7: "ether",
}
_ELEMENT_LABELS = {
    "physical": "物理",
    "fire": "灼热",
    "pulse": "电磁",
    "cryst": "寒冷",
    "natural": "自然",
    "ether": "源石技艺",
    "real": "真实",
    "lifedrain": "生命汲取",
    "spell": "法术",
}
# Compatibility exports for old callers. Name-based effect filters are retired;
# current typed conditions and observed hit masks determine applicability.
_BUFF_SKILL_FILTER = {}
_BUFF_EFFECT_SKILL_FILTER = {}
_ATTR_TYPE_DAMAGE_MASK = {17: 0x20484, 28: 0x200, 32: 0x100, 33: 0x2000}
_NATIVE_DAMAGE_SEMANTICS_BINDING = {
    'gameAssemblySha256': 'c24495e51b406f03b03890c4788ee618ae022c991405be5d5b8b787cb775ae89',
    'metadataSha256': '0076743397acadf03d3b0064343a963c7c88863b8160526d397e4b3efb96f02e',
}
# Complete typed resources have an independent layout provenance. This does
# not renew the separate native zone/keyword/equipment semantics proofs above.
_CURRENT_TYPED_RESOURCE_BINDING = {
    'gameAssemblySha256': 'ab00f1c41e13edf9816f142b166e3c2536d5bc7707992c39e5ed52d1cc888e2c',
    'metadataSha256': '461321d9dca6a832bce3bd2390402efc7c83c145554e97a8058bb68fbec1c8ca',
    'schemaSha256': '42bd4483f3f6f5937fd4a1f8bd43f3152ee62d33e259c67260a4be25a8a4bcf2',
}
# Independently re-proved GetKeywordBuffName switches and metadata literals.
# This does not renew the unrelated legacy zone/mask/equipment native proof.
_CURRENT_KEYWORD_BINDING = {
    'gameAssemblySha256': 'ab00f1c41e13edf9816f142b166e3c2536d5bc7707992c39e5ed52d1cc888e2c',
    'metadataSha256': '461321d9dca6a832bce3bd2390402efc7c83c145554e97a8058bb68fbec1c8ca',
}
_SINGLETON_REFRESH_EVENT_PATTERNS = (
    re.compile(r"^buff_equipsuit_", re.IGNORECASE),
    re.compile(r"^buff_chr_\d{4}_[a-z0-9]+_potential_\d+(?:_|$)", re.IGNORECASE),
)
_STACKABLE_EVENT_NAME_RE = re.compile(r"(?:^|_)(?:layer|stack)(?:_|$)", re.IGNORECASE)
_WEAPON_BUFF_RE = re.compile(r"^buff_wpn_[a-z]+_\d+_")
_WEAPON_ID_FROM_BUFF_RE = re.compile(r"^buff_(wpn_[a-z]+_\d+)_")
_FOOD_POTION_BUFF_RE = re.compile(r"^buff_common_.*_potion(?:_\d+)?$", re.IGNORECASE)
_GENERIC_BUFF_PREFIXES = (
    "buff_common_affixes_enhance_",
    "buff_common_affixes_vulnerable_",
    "buff_common_pulse_",
    "buff_common_fire_",
    "buff_common_cryst_",
    "buff_common_natural_",
)
_PLAYER_TARGET_GENERIC_BUFF_PREFIXES = (
    "buff_common_affixes_enhance_",
)
_CHR_BORROW_WINDOW_MS = 1000
_DEF_DECREASE_BASE_KEYS = {"def_decrease", "additional_def_decrease"}
_DEF_DECREASE_TICKS_PER_SEC = 3.5


def _attr_type_applies_to_skill(
    attr_type: int | None, skill_key: str | None, *, damage_decorate_mask: int | None = None,
    static_damage_decorate_mask: int | None = None,
    static_damage_decorate_mask_source: str | None = None,
) -> bool | None:
    if attr_type is None:
        return True
    mask = _ATTR_TYPE_DAMAGE_MASK.get(attr_type)
    if mask is None:
        return True
    # Current DamagePackData._GetAttackerDamageScaleIncreaseFromSkillType
    # tests these bits independently. Names and damage school are not inputs.
    if damage_decorate_mask is not None:
        if type(damage_decorate_mask) is not int or not 0 <= damage_decorate_mask < 2**64:
            return None
        return bool(damage_decorate_mask & mask)
    # An exact current unit proves its positive skill-category bits only.
    # Runtime-added bits can be absent from the template, so absence is unknown.
    if (static_damage_decorate_mask_source == 'current_typed_skill_or_buff_exact_unit_generation_only'
            and type(static_damage_decorate_mask) is int
            and 0 <= static_damage_decorate_mask < 2**64
            and static_damage_decorate_mask & mask):
        return True
    return None
_HIDDEN_COMBO_RULES = (
    {
        "rule_key": "karin_hidden_combo",
        "buff_patterns": (
            re.compile(r"buff_chr_0019_karin_talent_2_combo", re.IGNORECASE),
            re.compile(r"buff_chr_0019_karin_potential_5_combo", re.IGNORECASE),
        ),
        "skill_group_rates": {
            1: 0.30,
            2: 0.20,
        },
        "cast_window_ms": 5000,
    },
)
_GENERIC_COMBO_TRIGGER_BUFF_ID = "buff_common_affixes_combo_trigger"
_GENERIC_COMBO_IMBUE_BUFF_IDS = {
    "buff_common_affixes_skillimbue",
    "buff_common_affixes_skillimbue_atk",
}
_COMBO_TOTAL_RATES_BY_GROUP_TYPE = {
    1: (0.30, 0.45, 0.60, 0.75),
    2: (0.20, 0.30, 0.40, 0.50),
}
_COMBO_TRIGGER_GROUP_WINDOW_MS = 20
_COMBO_CONSUME_MAX_WINDOW_MS = 5000
_COMBO_CONSUME_SKILL_ALIASES: dict[str, dict[str, Any]] = {
    "sk_wpn_lance_0010": {
        "family_skill_key": "chr_0015_lifeng_ultimate_skill",
        "group_type": 2,
    },
}
_DUNGEON_CONTEXT_ALIASES: dict[str, tuple[str, str]] = {
    "indie_contract001": ("indie_group_ccdg", "危机合约"),
    "indie_group_ccdg": ("indie_group_ccdg", "危机合约"),
    "indie_ccdg001": ("indie_group_ccdg", "危机合约"),
    "indie_hard016": ("indie_hard016", "仪式旋流"),
    "indie_hard016_s": ("indie_hard016_s", "仪式旋流·苦难"),
    "indie_hard017": ("indie_hard017", "死寂表象"),
    "indie_hard017_s": ("indie_hard017_s", "死寂表象·苦难"),
    "indie_hard018": ("indie_hard018", "忿鼓咆声"),
    "indie_hard018_s": ("indie_hard018_s", "忿鼓咆声·苦难"),
    "indie_hard019": ("indie_hard019", "刺痛盾阵"),
    "indie_hard019_s": ("indie_hard019_s", "刺痛盾阵·苦难"),
    "indie_hard020": ("indie_hard020", "溶解窥看"),
    "indie_hard020_s": ("indie_hard020_s", "溶解窥看·苦难"),
    "indie_hard021": ("indie_hard021", "冯河断水"),
    "indie_hard021_s": ("indie_hard021_s", "冯河断水·苦难"),
}
_VISUAL_HIT_GROUP_WINDOW_MS = 120


def _timer_is_authoritative(fields: dict[str, Any]) -> bool:
    if _coerce_int(fields.get("fallback"), default=0) != 0:
        return False
    if str(fields.get("source") or "").endswith("Fallback"):
        return False
    if "official" in fields:
        return _coerce_int(fields.get("official"), default=1) != 0
    return str(fields.get("source") or "") != "BattleOpModifyBattleState"


def _is_generic_runtime_skill_id(skill_key: str | None) -> bool:
    return bool(re.fullmatch(r"skill_\d+", str(skill_key or "")))


@lru_cache(maxsize=1)
def _load_classifier_hints() -> dict[str, Any]:
    candidates = [
        _repo_root() / "data" / "local_semantics" / "classifier_hints.json",
    ]
    for path in candidates:
        try:
            if not path.is_file():
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            return data
    return {}


def _buff_classifier_hint(buff_id: str) -> dict[str, Any] | None:
    hints = _load_classifier_hints().get("buffHints")
    if not isinstance(hints, dict):
        return None
    hint = hints.get(buff_id)
    return hint if isinstance(hint, dict) else None


def _positive_int(value: Any) -> int | None:
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


def _iter_json_entries(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    entries = payload.get("entries") if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, dict)]


@lru_cache(maxsize=1)
def _load_nonstacking_weapon_ids() -> set[str]:
    roots = [
        _repo_root() / "data" / "akedata" / "weapon" / "items",
    ]
    markers = ("无法叠加", "不能叠加", "不可叠加")
    weapon_ids: set[str] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            weapon_id = str(payload.get("weaponId") or path.stem)
            text_parts: list[str] = []
            for skill in payload.get("skilllist") or []:
                if isinstance(skill, dict):
                    text_parts.append(str(skill.get("description") or ""))
            text_parts.append(str(payload.get("description") or ""))
            if any(marker in "\n".join(text_parts) for marker in markers):
                weapon_ids.add(weapon_id)
        if weapon_ids:
            break
    return weapon_ids


def _is_nonstacking_refresh_event(event_key: str | None) -> bool:
    event_key = str(event_key or "")
    root = _load_current_buff_roots().get(event_key)
    settings = root.get('stackingSettings') if isinstance(root, dict) else None
    return isinstance(settings, dict) and settings.get('stackingType') == 'Unique'


def _is_food_potion_buff(event_key: str | None) -> bool:
    return bool(_FOOD_POTION_BUFF_RE.match(str(event_key or "")))


def _blackboard_max_stack_from_weapon_skill(skill: dict[str, Any]) -> int | None:
    description = str(skill.get("description") or "")
    if "叠加" not in description and "max_stack" not in description:
        return None
    for item in skill.get("blackboard") or []:
        if not isinstance(item, dict) or item.get("key") != "max_stack":
            continue
        raw_value = item.get("value")
        values = raw_value if isinstance(raw_value, list) else [raw_value]
        stack_values = [value for value in (_positive_int(value) for value in values) if value is not None]
        if stack_values:
            return min(stack_values)
    return None


@lru_cache(maxsize=1)
def _load_weapon_id_stack_limits() -> dict[str, int]:
    roots = [
        _repo_root() / "data" / "akedata" / "weapon" / "items",
    ]
    limits: dict[str, int] = {}
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            weapon_id = str(payload.get("weaponId") or path.stem)
            for skill in payload.get("skilllist") or []:
                if not isinstance(skill, dict):
                    continue
                max_stack = _blackboard_max_stack_from_weapon_skill(skill)
                if max_stack is not None:
                    limits[weapon_id] = max_stack
                    break
        if limits:
            break
    return limits


@lru_cache(maxsize=1)
def _load_weapon_buff_stack_limits() -> dict[str, int]:
    paths = [
        _repo_root() / "data" / "local_static" / "skill" / "manifest.json",
    ]
    limits: dict[str, int] = {}
    for path in paths:
        if not path.is_file():
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
            created_buff_ids = binary_probe.get("highConfidenceCreatedBuffIds") or binary_probe.get("createdBuffIds") or []
            if not isinstance(created_buff_ids, list):
                continue
            for buff_id in created_buff_ids:
                buff_key = str(buff_id or "")
                if buff_key.startswith("buff_wpn_"):
                    limits[buff_key] = max_stack
        if limits:
            break
    return limits


def _buff_stack_limit(event_key: str | None, bb_values: dict[str, Any] | None = None) -> int | None:
    event_key = str(event_key or "")
    if not event_key:
        return None
    root = _load_current_buff_roots().get(event_key)
    settings = root.get('stackingSettings') if isinstance(root, dict) else None
    if isinstance(settings, dict):
        if settings.get('stackingType') == 'HighPriority':
            return 1
        if settings.get('stackingType') == 'Unique' and settings.get('useMaxStackCntKey') is False:
            return _positive_int(settings.get('maxStackCnt')) or 1
        if settings.get('useMaxStackCntKey') is True:
            value = _positive_int((bb_values or {}).get(str(settings.get('maxStackCntKey') or '')))
            return value
        value = _positive_int(settings.get('maxStackCnt'))
        if value is not None:
            return value
        return None
    semantic_entry = _load_local_buff_semantic_entries().get(event_key)
    if isinstance(semantic_entry, dict):
        stacking = semantic_entry.get("stacking")
        if isinstance(stacking, dict):
            semantic_limit = _positive_int(stacking.get("maxStackCnt"))
            if semantic_limit is not None:
                return semantic_limit
    exact_limit = _load_weapon_buff_stack_limits().get(event_key)
    if exact_limit is not None:
        return exact_limit
    match = _WEAPON_ID_FROM_BUFF_RE.match(event_key)
    return None


def _buff_effect_hint_candidates(buff_id: str) -> list[Any]:
    hint = _buff_classifier_hint(buff_id)
    if not hint:
        return []
    resolved = hint.get("resolvedEffectHints")
    if isinstance(resolved, list) and resolved:
        return resolved
    direct = hint.get("effectHints")
    return direct if isinstance(direct, list) else []


_BUFF_DAMAGE_NAME_OVERRIDES = {
    "buff_physical_airborne": "物理浮空伤害",
    "buff_physical_crushed": "猛击",
    "buff_physical_knockdown": "倒地",
    "buff_physical_no_guard": "破防",
    "buff_chr_0028_wulfa_normal_bleed": "爪印斫痕",
    "buff_chr_0028_wulfa_normal_bleed_effect": "爪印斫痕",
    "buff_chr_0028_wulfa_normal_bleed_crit_extra_damage": "沸血",
    "buff_chr_0028_wulfa_combo_2_damage": "燎影时刻",
    "buff_common_cryst_triggered_physical_break": "猛击",
    "buff_common_heal_moss_1": "治疗苔藓",
    "buff_common_heal_moss_2": "治疗苔藓",
    "buff_common_originum_frozen": "源石冻结",
}
_RUNTIME_SKILL_NAME_OVERRIDES = {
    "chr_0017_yvonne_skill_162": "伊冯连携 / 机器人持续伤害",
    "chr_0017_yvonne_skill_163": "伊冯连携 / 机器人终结爆炸",
}
_ELEMENT_TOKEN_TO_NAME = {
    "physical": "物理",
    "fire": "灼热",
    "pulse": "电磁",
    "cryst": "寒冷",
    "crystal": "寒冷",
    "natural": "自然",
    "spell": "法术",
}
_ATTACK_INDEX_RE = re.compile(r"_attack(\d+)")
_ATTACK_VARIANT_RE = re.compile(
    r"^chr_\d{4}_[a-z0-9]+_attack_?(\d+)(?:_(\d+))?(?:_projhit)?(?:_blocked)?$",
    re.IGNORECASE,
)
_DASH_ATTACK_RE = re.compile(r"^chr_\d{4}_[a-z0-9]+_dash_attack(?:_projhit(?:\d+)?)?$", re.IGNORECASE)
_PLUNGING_ATTACK_RE = re.compile(
    r"^chr_\d{4}_[a-z0-9]+_plunging_attack(?:_(start|end|projhit))?$",
    re.IGNORECASE,
)
_TALENT_RE = re.compile(r"^chr_\d{4}_[a-z0-9]+_talent_?(\d+)(?:_(\d+))?$", re.IGNORECASE)
_POTENTIAL_RE = re.compile(r"^chr_\d{4}_[a-z0-9]+_potential_?(\d+)(?:_(\d+))?$", re.IGNORECASE)
_PASSIVE_RE = re.compile(r"^chr_\d{4}_[a-z0-9]+_passive(?:_(.+))?$", re.IGNORECASE)
_COMBO_SKILL_RE = re.compile(r"_combo(?:_\d+)?_skill(?:$|_)")
_COMBO_DAMAGE_SKILL_KEY_RE = re.compile(
    r"^buff_(chr_\d{4}_[a-z0-9]+_combo(?:_\d+)?_)damage(?:wait)?$",
    re.IGNORECASE,
)


def _repo_root() -> Path:
    override = os.environ.get("ENDFIELD_LOGS_DATA_ROOT")
    if override:
        return Path(override)
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        return Path(frozen_root)
    return Path(__file__).resolve().parents[3]


def _apply_dungeon_catalog_overrides() -> None:
    """Merge dungeon alias tables from data/local_tables/dungeon_catalog.json.

    The catalog file is the single maintained source for dungeon aliases shared
    by parser_core, the API service, and the web frontend. Entries in the file
    win over the built-in tables above; the built-ins remain as a fallback for
    environments where the data directory is absent.
    """
    catalog_path = _repo_root() / "data" / "local_tables" / "dungeon_catalog.json"
    if not catalog_path.exists():
        return
    try:
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    parser_tables = catalog.get("parser") or {}
    for key, value in (parser_tables.get("dungeon_context_aliases") or {}).items():
        if isinstance(value, list) and len(value) == 2:
            _DUNGEON_CONTEXT_ALIASES[str(key)] = (str(value[0]), str(value[1]))


_apply_dungeon_catalog_overrides()


def _token_field(line: str, key: str) -> str | None:
    match = re.search(_LOADOUT_FIELD_RE_TEMPLATE.format(key=re.escape(key)), line)
    return match.group(1) if match else None


def _braced_field(line: str, key: str) -> str | None:
    marker = f"{key}="
    start = line.find(marker)
    if start < 0:
        return None
    brace_start = line.find("{", start + len(marker))
    if brace_start < 0:
        return None
    depth = 0
    for index in range(brace_start, len(line)):
        char = line[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return line[brace_start + 1:index]
    return None


def _split_top_level(text: str, sep: str) -> list[str]:
    parts: list[str] = []
    start = 0
    depth = 0
    for index, char in enumerate(text):
        if char == "{":
            depth += 1
        elif char == "}":
            depth = max(0, depth - 1)
        elif char == sep and depth == 0:
            parts.append(text[start:index])
            start = index + 1
    parts.append(text[start:])
    return [part.strip() for part in parts if part.strip()]


def _parse_bb_assignments(blob: str | None) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for item in _split_top_level(str(blob or ""), ","):
        if "=" not in item:
            continue
        key, raw_value = item.split("=", 1)
        value = raw_value.strip()
        # Blackboard values can be signed (for example resistance reductions
        # use -20). Preserve the numeric value here; individual effect
        # semantics decide whether a positive rate is required.
        rate = _coerce_optional_float(value)
        values[key.strip()] = rate if rate is not None else value
    return values


def _ms_time_text(timestamp_ms: int | None) -> str | None:
    if timestamp_ms is None:
        return None
    value = int(timestamp_ms) % (24 * 60 * 60 * 1000)
    hours, rem = divmod(value, 60 * 60 * 1000)
    minutes, rem = divmod(rem, 60 * 1000)
    seconds, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


@lru_cache(maxsize=1)
def _load_packet_numeric_buff_map() -> dict[str, dict[str, Any]]:
    map_path = _repo_root() / "data" / "packet_semantics" / "buff_numeric_map.json"
    if not map_path.exists():
        return {}
    try:
        payload = json.loads(map_path.read_text(encoding="utf-8"))
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


def _merge_packet_buff_mapping_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}
    base = dict(rows[0])
    effect_keys: set[str] = set()
    merged_effects: list[dict[str, Any]] = []
    for field in ("effects", "dynamic_effects"):
        merged: list[dict[str, Any]] = []
        for row in rows:
            values = row.get(field)
            if not isinstance(values, list):
                continue
            for item in values:
                if not isinstance(item, dict):
                    continue
                marker = json.dumps(item, ensure_ascii=False, sort_keys=True)
                unique_key = f"{field}:{marker}"
                if unique_key in effect_keys:
                    continue
                effect_keys.add(unique_key)
                merged.append(item)
        if merged:
            merged_effects.extend(merged)
            base[field] = merged
    if "effects" not in base:
        base["effects"] = []
    if "dynamic_effects" not in base:
        base["dynamic_effects"] = []
    for row in rows[1:]:
        if not base.get("reason") and row.get("reason"):
            base["reason"] = row.get("reason")
        if not base.get("confidence") and row.get("confidence"):
            base["confidence"] = row.get("confidence")
        if base.get("stack_limit") in (None, "", 0) and row.get("stack_limit") not in (None, "", 0):
            base["stack_limit"] = row.get("stack_limit")
    return base


@lru_cache(maxsize=1)
def _load_packet_canonical_buff_map() -> dict[str, dict[str, Any]]:
    by_canonical: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in _load_packet_numeric_buff_map().values():
        canonical_buff_id = str(row.get("canonical_buff_id") or "")
        if not canonical_buff_id:
            continue
        by_canonical[canonical_buff_id].append(row)
    return {
        canonical_buff_id: _merge_packet_buff_mapping_rows(rows)
        for canonical_buff_id, rows in by_canonical.items()
        if rows
    }


@lru_cache(maxsize=1)
def _load_mechanism_registry_by_buff_id() -> dict[str, list[dict[str, Any]]]:
    path = _repo_root() / "data" / "packet_semantics" / "mechanism_registry.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    rows_by_buff = payload.get("by_buff_id")
    if not isinstance(rows_by_buff, dict):
        return {}
    out: dict[str, list[dict[str, Any]]] = {}
    for buff_id, rows in rows_by_buff.items():
        if not isinstance(rows, list):
            continue
        normalized_rows = [row for row in rows if isinstance(row, dict)]
        if normalized_rows:
            out[str(buff_id)] = normalized_rows
    return out


def _mechanism_registry_buff_hint(buff_id: str | None) -> dict[str, Any]:
    key = str(buff_id or "")
    if not key:
        return {}
    canonical_key = _load_num_id_str_buff_map().get(key, key) if key.isdigit() else key
    rows = _load_mechanism_registry_by_buff_id().get(canonical_key) or []
    if len(rows) != 1:
        return {}
    row = dict(rows[0])
    row["canonical_buff_id"] = str(row.get("canonical_buff_id") or canonical_key)
    if row.get("source_kind") == "weapon" and row.get("source_id") and not row.get("weapon_id"):
        row["weapon_id"] = row.get("source_id")
    if row.get("source_kind") == "suit" and row.get("source_id") and not row.get("suit_id"):
        row["suit_id"] = row.get("source_id")
    row.setdefault("scope", "loadout_guarded")
    row.setdefault("confidence", "compiled_from_unpack")
    return row


def _packet_numeric_buff_hint(buff_id: str | None) -> dict[str, Any]:
    key = str(buff_id or "")
    direct = _load_packet_numeric_buff_map().get(key)
    if isinstance(direct, dict):
        return direct
    canonical = _load_packet_canonical_buff_map().get(key)
    if isinstance(canonical, dict):
        return canonical
    return _mechanism_registry_buff_hint(key)


# Derived indexes belong to the exact immutable resources loaded below. Test
# overrides and replacement catalogs fall back to their own complete scans.
_RDPS_VERIFIED_INDEX: tuple[dict, dict, dict, dict, dict] | None = None
_BUFF_ACTION_TYPES_INDEX: tuple[dict, dict] | None = None


@lru_cache(maxsize=1)
def _load_rdps_semantics_registry() -> dict[str, Any]:
    global _RDPS_VERIFIED_INDEX
    _RDPS_VERIFIED_INDEX = None
    path = _repo_root() / "data" / "packet_semantics" / "rdps_semantics_registry.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    try:
        keyword = json.loads((_repo_root() / 'data/local_static/buff/keyword_buff_map.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        keyword = {}
    if (isinstance(keyword, dict) and type(keyword.get('schemaVersion')) is int and keyword['schemaVersion'] == 1
            and keyword.get('source') == 'current_native_keyword_switch_and_metadata_literals'
            and keyword.get('decoder') == _CURRENT_KEYWORD_BINDING):
        mapping = keyword.get('mapping')
        subtypes = {'All', 'Spell', 'Physical', 'Natural', 'Fire', 'Crystal', 'Pulse'}
        if (isinstance(mapping, dict) and set(mapping) == {'Vulnerable', 'Enhanced'}
                and all(isinstance(rows, dict) and set(rows) == subtypes
                        and all(isinstance(value, str) and value for value in rows.values()) for rows in mapping.values())):
            payload['_keyword_buff_map'] = mapping
    verified = payload.get('verified_effects')
    if isinstance(verified, dict):
        exact_index: dict[str, set[str]] = defaultdict(set)
        runtime_index: dict[str, set[str]] = defaultdict(set)
        for key, entry in verified.items():
            if not isinstance(entry, dict):
                continue
            for identity in [key, *(entry.get('aliases') or []), *(entry.get('numeric_ids') or [])]:
                if str(identity):
                    exact_index[str(identity)].add(key)
            for binding in entry.get('runtime_bindings') or []:
                if isinstance(binding, dict) and isinstance(binding.get('template'), str):
                    runtime_index[binding['template']].add(key)
        _RDPS_VERIFIED_INDEX = (payload, verified, dict(exact_index), dict(runtime_index),
                                {key: order for order, key in enumerate(verified)})
    return payload


def _record_bb_key_set(record: dict[str, Any]) -> set[str]:
    keys = {str(key) for key in record.get("bb_keys") or [] if str(key)}
    keys.update(str(key) for key in (record.get("bb_values") or {}).keys() if str(key))
    return keys


def _rdps_registry_bb_keys_allowed(entry: dict[str, Any], record: dict[str, Any]) -> bool:
    allowed_values = entry.get("allowed_bb_keys")
    if not isinstance(allowed_values, list):
        return True
    allowed = {str(key).lower() for key in allowed_values}
    if "*" in allowed:
        return True
    observed = {key.lower() for key in _record_bb_key_set(record)}
    return observed.issubset(allowed)


_RDPS_REGISTRY_CONTROL_BB_KEYS = {
    "",
    "=",
    "duration",
    "duration2",
    "duration_spellvulnerable",
    "life_time",
    "lifetime",
    "max_stack",
    "max_stack_count",
    "maxstack",
    "stack_limit",
    "stacklimit",
    "cntmax",
    "count",
    "max_count",
}


def _rdps_registry_entry_matches_guard(entry: dict[str, Any], record: dict[str, Any]) -> bool:
    guard = entry.get("guard")
    if not isinstance(guard, dict):
        return True

    source_key = str(record.get("source_character_key") or "")
    target_key = str(record.get("target_character_key") or "")
    packet_mapping = record.get("packet_mapping") if isinstance(record.get("packet_mapping"), dict) else {}
    if guard.get('requires_actor_identity') is True:
        if (not source_key or any(type(record.get(key)) is not str
                or not re.fullmatch(r'[1-9]\d*', record[key]) or int(record[key]) >= 2**64
                for key in ('uid', 'source_actor_id', 'target_actor_id'))
                or record.get('uid') == '4294967295'):
            return False
    if guard.get('target_kind') == 'enemy':
        enemy = record.get('target_enemy_key')
        if not enemy or target_key not in ('', enemy) or record.get('target_player_key'):
            return False
    if guard.get('target_kind') == 'character' and (not target_key or record.get('target_enemy_key')):
        return False
    if guard.get('recipient') == 'self':
        if not source_key or target_key != source_key:
            return False
        source_actor, target_actor = record.get('source_actor_id'), record.get('target_actor_id')
        if (source_actor or target_actor) and (not source_actor or not target_actor or str(source_actor) != str(target_actor)):
            return False
    if guard.get('recipient') == 'other_character':
        if not source_key or not target_key or source_key == target_key:
            return False
        source_actor, target_actor = record.get('source_actor_id'), record.get('target_actor_id')
        if (source_actor or target_actor) and (not source_actor or not target_actor or str(source_actor) == str(target_actor)):
            return False

    source_guard = str(guard.get("source_character_key") or guard.get("source_character_id") or "")
    if source_guard and source_key != source_guard:
        return False

    character_guard = str(guard.get("character_id") or "")
    if character_guard and character_guard not in {source_key, target_key}:
        return False

    source_skill = str(record.get("source_skill_key") or "")
    source_skill_family = str(record.get("source_skill_family_key") or source_skill)
    skill_guard = str(guard.get("source_skill_key") or "")
    if skill_guard and source_skill != skill_guard:
        return False
    family_guard = str(guard.get("source_skill_family_key") or "")
    if family_guard and source_skill_family != family_guard:
        return False

    weapon_guard = str(guard.get("weapon_id") or "")
    if weapon_guard:
        # New equipment approvals require the loadout observed at this Buff
        # instance's creation. A template name or later roster cannot supply it.
        if (guard.get('requires_source_loadout') is True
                and record.get('source_weapon_template') != weapon_guard):
            return False
        mapping_weapon = str(packet_mapping.get("weapon_id") or "")
        event_key = _normalize_buff_id(record.get("event_key"))
        raw_event_key = _normalize_buff_id(record.get("raw_event_key"))
        event_matches_weapon = event_key.startswith(f"buff_{weapon_guard}") or raw_event_key.startswith(f"buff_{weapon_guard}")
        if mapping_weapon and mapping_weapon != weapon_guard:
            return False
        if not mapping_weapon and not event_matches_weapon:
            return False

    suit_guard = str(guard.get("suit_id") or "")
    if suit_guard:
        mapping_suit = str(packet_mapping.get("suit_id") or "")
        if mapping_suit and mapping_suit != suit_guard:
            return False

    return True


def _rdps_registry_matching_effect_entry(record: dict[str, Any], *, require_guard: bool,
                                         root_lookup_cache: dict | None = None) -> dict[str, Any] | None:
    registry = _load_rdps_semantics_registry()
    verified = registry.get("verified_effects") if isinstance(registry.get("verified_effects"), dict) else {}
    candidates = {
        _normalize_buff_id(record.get("event_key")),
        _normalize_buff_id(record.get("raw_event_key")),
    }
    candidates.discard("")
    raw_numeric = _normalize_buff_id(record.get("raw_event_key"))

    # A current creation action may forward an approved field under a different
    # child key. The reviewed action/recipient contract, not the child name,
    # grants that existing approval to the actual packet instance.
    actual_candidates = {_load_num_id_str_buff_map().get(key, key) if key.isdigit() else key for key in candidates}
    index = _RDPS_VERIFIED_INDEX
    indexed = index is not None and index[0] is registry and index[1] is verified
    runtime_keys = set().union(*(index[3].get(key, set()) for key in actual_candidates)) if indexed else None
    runtime_entries = verified.items() if runtime_keys is None else (
        (key, verified[key]) for key in sorted(runtime_keys, key=index[4].__getitem__)
    )
    for approval_key, entry in runtime_entries:
        if not isinstance(entry, dict):
            continue
        for binding in entry.get('runtime_bindings') or []:
            if binding.get('template') not in actual_candidates:
                continue
            if (binding.get('decoder') != _NATIVE_DAMAGE_SEMANTICS_BINDING
                    or not re.fullmatch(r'[0-9a-f]{64}', str(binding.get('sourceRootSha256') or ''))
                    or require_guard and not _rdps_registry_entry_matches_guard(entry, record)):
                continue
            origins = record.get('origin_observations') or []
            valid = bool(origins)
            for origin in origins:
                parent = origin.get('templateStrId')
                if parent in (None, '') and type(origin.get('templateIntId')) is int:
                    parent = (_load_num_id_str_skill_map() if binding.get('parent_kind') == 'Skill'
                              else _load_num_id_str_buff_map()).get(str(origin['templateIntId']))
                elif not isinstance(parent, str) or not parent or origin.get('templateIntId') is not None:
                    parent = None
                if (origin.get('_boundCreateRecord') is not True
                        or str(origin.get('environmentType') or '').lower() != str(binding.get('parent_kind') or '').lower()
                        or parent != binding.get('parent_template')
                        or type(origin.get('actionId')) is not int or origin['actionId'] not in binding.get('create_actions', [])
                        or origin.get('triggerType') != 0
                        or origin.get('buffUid') != record.get('uid')
                        or origin.get('buffTemplate') not in candidates | actual_candidates
                        or origin.get('sourceActorId') != record.get('source_actor_id')
                        or origin.get('targetActorId') != record.get('target_actor_id')
                        or any(not re.fullmatch(r'[1-9]\d*', str(origin.get(key) or ''))
                               or int(origin[key]) >= 2**64 for key in ('buffUid', 'sourceActorId', 'targetActorId'))
                        or binding.get('recipient') == 'other_character' and origin['sourceActorId'] == origin['targetActorId']):
                    valid = False
                    break
            if valid:
                remap = binding.get('bb_key_remap') or {}
                effects = [{**effect, 'bb_key': remap.get(effect.get('bb_key'), effect.get('bb_key'))}
                           for effect in entry.get('effects') or []]
                return {**entry, 'effects': effects, '_rdps_whitelist_keys': [approval_key],
                        '_runtime_binding': copy.deepcopy(binding)}

    exact_matches: list[dict[str, Any]] = []
    exact_keys: list[str] = []
    indexed_exact_keys = set().union(*(index[2].get(key, set()) for key in candidates)) if indexed else None
    exact_entries = verified.items() if indexed_exact_keys is None else (
        (key, verified[key]) for key in sorted(indexed_exact_keys, key=index[4].__getitem__)
    )
    for canonical_buff_id, entry in exact_entries:
        if not isinstance(entry, dict):
            continue
        aliases = {str(value) for value in entry.get("aliases") or [] if str(value)}
        numeric_ids = {str(value) for value in entry.get("numeric_ids") or [] if str(value)}
        if not (
            str(canonical_buff_id) in candidates
            or bool(candidates & aliases)
            or (raw_numeric and raw_numeric in numeric_ids)
        ):
            continue
        if require_guard and not _rdps_registry_entry_matches_guard(entry, record):
            continue
        exact_matches.append(entry)
        exact_keys.append(str(canonical_buff_id))
    if exact_matches:
        actual = _normalize_buff_id(record.get('event_key'))
        actual = _load_num_id_str_buff_map().get(actual, actual) if actual.isdigit() else actual
        lifecycle_approvals = {
            'buff_chr_0023_antal_tageffect': 'buff_chr_0023_antal_normal_fragile',
            'buff_common_natural_natural_corrupt_triggered': 'buff_chr_0025_ardelia_dynamic_def_decrease',
        }
        lifecycle_key = lifecycle_approvals.get(actual)
        if (lifecycle_key is None and len(exact_keys) == 1
                and verified[exact_keys[0]].get('lifecycle_only') is True):
            lifecycle_key = exact_keys[0]
        if lifecycle_key in exact_keys:
            root = _load_current_buff_roots().get(actual)
            if (isinstance(root, dict) and (root.get('attributeModifier') or {}).get('attributeModifiers') == []
                    and root.get('damageModifier') == [] and root.get('globalModifier') == []):
                entry = verified[lifecycle_key]
                return {**entry, 'dynamic': False, 'effects': [], 'dynamic_effects': [],
                        'ignored_bb_keys': sorted(set(entry.get('ignored_bb_keys') or [])
                                                  | _rdps_registry_effect_bb_keys(entry)),
                        '_rdps_whitelist_keys': [lifecycle_key], '_rdps_lifecycle_only': True}
        # The character approval and the generic corrosion approval describe
        # the same current Buff, not two independently stacked contributions.
        corrosion_key = 'buff_common_natural_natural_corrupt_do'
        ardelia_key = 'buff_chr_0025_ardelia_dynamic_def_decrease'
        if (actual == corrosion_key and set(exact_keys) == {corrosion_key, ardelia_key}
                and verified[corrosion_key].get('effects') == verified[ardelia_key].get('effects')
                and corrosion_key in (verified[ardelia_key].get('aliases') or [])):
            return {**verified[corrosion_key], '_rdps_whitelist_keys': sorted(exact_keys)}
        signatures = {
            json.dumps(entry, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
            for entry in exact_matches
        }
        return {**exact_matches[0], '_rdps_whitelist_keys': sorted(exact_keys)} if len(signatures) == 1 else None

    prefixes = registry.get("verified_prefixes") if isinstance(registry.get("verified_prefixes"), list) else []
    prefix_matches: list[dict[str, Any]] = []
    for key in candidates:
        if not key:
            continue
        for entry in prefixes:
            if not isinstance(entry, dict):
                continue
            prefix = str(entry.get("prefix") or "")
            if not prefix or not key.startswith(prefix):
                continue
            if require_guard and not _rdps_registry_entry_matches_guard(entry, record):
                continue
            if _rdps_registry_bb_keys_allowed(entry, record):
                prefix_matches.append(entry)
    if prefix_matches:
        signatures = {
            json.dumps(entry, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
            for entry in prefix_matches
        }
        return {**prefix_matches[0], '_rdps_whitelist_keys': sorted({str(row['prefix']) for row in prefix_matches})} if len(signatures) == 1 else None
    origins = record.get('origin_observations')
    keyword_map = registry.get('_keyword_buff_map')
    if isinstance(origins, list) and origins and isinstance(keyword_map, dict):
        projected = []
        for origin in origins:
            if (not isinstance(origin, dict) or origin.get('_boundCreateRecord') is not True
                    or origin.get('buffTemplate') not in {record.get('event_key'), record.get('raw_event_key')}
                    or any(type(origin.get(key)) is not str or not re.fullmatch(r'[1-9]\d*', origin[key])
                           or int(origin[key]) >= 2**64
                           for key in ('buffUid', 'sourceActorId', 'targetActorId', 'environmentInstId'))
                    or origin.get('buffUid') != record.get('uid')
                    or origin.get('sourceActorId') != record.get('source_actor_id')
                    or origin.get('targetActorId') != record.get('target_actor_id')
                    or str(origin.get('environmentType') or '').lower() != 'buff'
                    or str(origin.get('ownerType') or '') != '1'
                    or origin.get('environmentInstId') in (None, '', '0', '4294967295')
                    or type(origin.get('actionId')) is not int or origin['actionId'] < 0):
                return None
            actual_main = _normalize_buff_id(record.get('event_key'))
            for identity in (record.get('raw_event_key'), origin.get('buffTemplate')):
                if identity in (None, ''):
                    continue
                template = _normalize_buff_id(identity)
                if template.isdigit():
                    template = _load_num_id_str_buff_map().get(template)
                if template and template != actual_main:
                    return None
                if identity and not template:
                    return None
            parent = origin.get('templateStrId')
            numeric = origin.get('templateIntId')
            if isinstance(parent, str) and parent and numeric is None:
                parent = _normalize_buff_id(parent)
            elif parent in (None, '') and type(numeric) is int and numeric >= 0:
                parent = _load_num_id_str_buff_map().get(str(numeric))
            else:
                return None
            parent_entries = [(key, value) for key, value in verified.items()
                              if isinstance(value, dict) and (key == parent or parent in (value.get('aliases') or []))
                              and (not require_guard or _rdps_registry_entry_matches_guard(value, record))]
            parent_signatures = {json.dumps(value, sort_keys=True, ensure_ascii=False) for _, value in parent_entries}
            if len(parent_signatures) > 1:
                return None
            entry = parent_entries[0][1] if parent_entries else None
            parent_approval_keys = sorted(key for key, _ in parent_entries)
            root = _load_current_buff_roots().get(parent)
            if not isinstance(root, dict):
                return None
            if root_lookup_cache is None:
                actions = []; pending = [(root, True)]
                while pending:
                    value, ancestors_enabled = pending.pop()
                    if isinstance(value, dict):
                        enabled = ancestors_enabled and ('serverActionIndex' not in value or value.get('isEnable') is True)
                        if (value.get('$type') in {'Beyond.Gameplay.Core.VulnerableAction.Data', 'Beyond.Gameplay.Core.EnhancedAction.Data'}
                                and type(value.get('serverActionIndex')) is int
                                and value['serverActionIndex'] == origin['actionId']):
                            actions.append((value, enabled))
                        pending.extend((item, enabled) for item in value.values())
                    elif isinstance(value, list):
                        pending.extend((item, ancestors_enabled) for item in value)
            else:
                cached_lookup = root_lookup_cache.get(id(root))
                if cached_lookup is None or cached_lookup[0] is not root:
                    root_actions = {}
                    create_actions = []
                    pending = [(root, True)]
                    while pending:
                        value, ancestors_enabled = pending.pop()
                        if isinstance(value, dict):
                            enabled = ancestors_enabled and ('serverActionIndex' not in value or value.get('isEnable') is True)
                            if type(value.get('serverActionIndex')) is int:
                                root_actions.setdefault(value['serverActionIndex'], []).append((value, enabled))
                            if value.get('$type') == 'Beyond.Gameplay.Core.CreateBuffAction.Data':
                                create_actions.append((value, enabled))
                            pending.extend((item, enabled) for item in value.values())
                        elif isinstance(value, list):
                            pending.extend((item, ancestors_enabled) for item in value)
                    cached_lookup = (root, root_actions, create_actions)
                    root_lookup_cache[id(root)] = cached_lookup
                actions = [(value, enabled) for value, enabled in cached_lookup[1].get(origin['actionId'], [])
                           if value.get('$type') in {'Beyond.Gameplay.Core.VulnerableAction.Data',
                                                     'Beyond.Gameplay.Core.EnhancedAction.Data'}]
            if len(actions) != 1 or actions[0][1] is not True:
                return None
            action = actions[0][0]
            kind = {'Beyond.Gameplay.Core.VulnerableAction.Data': 'Vulnerable',
                    'Beyond.Gameplay.Core.EnhancedAction.Data': 'Enhanced'}[action['$type']]
            child = (keyword_map.get(kind) or {}).get(action.get('subType'))
            if not child or child != _normalize_buff_id(record.get('event_key')):
                return None
            rate_config = action.get('rate') or {}
            if (rate_config.get('useBlackboardKey') is not True
                    or not isinstance(rate_config.get('blackboardKey'), str) or not rate_config['blackboardKey']):
                return None
            approval_key = parent_approval_keys[0] if parent_approval_keys else parent
            parent_approval = isinstance(entry, dict)
            if not isinstance(entry, dict):
                # Some historical approvals name the visual child. Resolve
                # the native keyword main -> explicitly selected child path;
                # neither naming conventions nor a visual rate create credit.
                main_root = _load_current_buff_roots().get(child)
                if not isinstance(main_root, dict):
                    return None
                child_config = action.get('childBuffId') or {}
                if (action.get('overrideChildBuffId') is True
                        and child_config.get('useBlackboardKey') is False):
                    visual_child = child_config.get('value')
                elif action.get('overrideChildBuffId') is False:
                    defaults = [row for row in main_root.get('blackboard') or []
                                if isinstance(row, dict) and row.get('key') == 'child_buff_id']
                    if len(defaults) != 1 or defaults[0].get('isDynamic') is not False:
                        return None
                    visual_child = defaults[0].get('valueStr')
                else:
                    return None
                if not isinstance(visual_child, str) or not visual_child:
                    return None
                entry = verified.get(visual_child)
                visual_root = _load_current_buff_roots().get(visual_child)
                if not isinstance(entry, dict) or not isinstance(visual_root, dict):
                    return None
                attributes = visual_root.get('attributeModifier')
                if (not isinstance(attributes, dict) or attributes.get('attributeModifiers') != []
                        or visual_root.get('damageModifier') != [] or visual_root.get('globalModifier') != []):
                    return None
                forwards = []
                if root_lookup_cache is None:
                    pending = [(main_root, True)]
                    while pending:
                        value, ancestors_enabled = pending.pop()
                        if isinstance(value, dict):
                            enabled = ancestors_enabled and ('serverActionIndex' not in value or value.get('isEnable') is True)
                            if value.get('$type') == 'Beyond.Gameplay.Core.CreateBuffAction.Data':
                                for created in value.get('buffs') or []:
                                    if (created.get('readIdFromBlackboard') is True
                                            and created.get('buffIdKey') == 'child_buff_id'):
                                        forwards.append((created, enabled))
                            pending.extend((item, enabled) for item in value.values())
                        elif isinstance(value, list):
                            pending.extend((item, ancestors_enabled) for item in value)
                else:
                    cached_lookup = root_lookup_cache.get(id(main_root))
                    if cached_lookup is None or cached_lookup[0] is not main_root:
                        root_actions = {}
                        create_actions = []
                        pending = [(main_root, True)]
                        while pending:
                            value, ancestors_enabled = pending.pop()
                            if isinstance(value, dict):
                                enabled = ancestors_enabled and ('serverActionIndex' not in value or value.get('isEnable') is True)
                                if type(value.get('serverActionIndex')) is int:
                                    root_actions.setdefault(value['serverActionIndex'], []).append((value, enabled))
                                if value.get('$type') == 'Beyond.Gameplay.Core.CreateBuffAction.Data':
                                    create_actions.append((value, enabled))
                                pending.extend((item, enabled) for item in value.values())
                            elif isinstance(value, list):
                                pending.extend((item, ancestors_enabled) for item in value)
                        cached_lookup = (main_root, root_actions, create_actions)
                        root_lookup_cache[id(main_root)] = cached_lookup
                    for value, enabled in cached_lookup[2]:
                        for created in value.get('buffs') or []:
                            if (created.get('readIdFromBlackboard') is True
                                    and created.get('buffIdKey') == 'child_buff_id'):
                                forwards.append((created, enabled))
                if len(forwards) != 1 or forwards[0][1] is not True or forwards[0][0].get('assignBlackboard') is not True:
                    return None
                assignments = forwards[0][0].get('assignItems') or []
                for key in ('rate', 'duration'):
                    assigned = [row for row in assignments if isinstance(row, dict) and row.get('targetKey') == key]
                    if (len(assigned) != 1 or assigned[0].get('useDirectValue') is not False
                            or assigned[0].get('inputValueKey') != key):
                        return None
                approval_key = visual_child
                expected_zone = {'Vulnerable': 'fragile', 'Enhanced': 'amp'}[kind]
                expected_element = {'All': 'all', 'Spell': 'spell', 'Physical': 'physical', 'Natural': 'natural',
                                    'Fire': 'fire', 'Crystal': 'cryst', 'Pulse': 'pulse'}.get(action.get('subType'))
                if any(spec.get('zone') != expected_zone or _normalize_effect_element(spec.get('element')) != expected_element
                       for spec in entry.get('effects') or []):
                    return None
            if require_guard and not _rdps_registry_entry_matches_guard(entry, record):
                return None
            if (parent == 'buff_chr_0023_antal_tageffect'
                    and parent_approval_keys == ['buff_chr_0023_antal_normal_fragile']):
                # The actual keyword Buff reports its current rate. It already
                # includes any enhancement applied during creation; later op15
                # observations update that same rate, never a guessed timer.
                entry = {**entry, 'dynamic': bool(record.get('keyword_rate_snapshots')),
                         'effects': [{'zone': 'fragile', 'element': element, 'bb_key': 'rate'}
                                     for element in ('pulse', 'fire')]}
            specs = entry.get('effects') or []
            # A reviewed parent input alias only authorizes the exact typed
            # origin; it must never become a fallback rate on the parent.
            # The actual keyword main Buff still must expose its own rate.
            if not specs or any((parent_approval and rate_config.get('blackboardKey') not in {spec.get('bb_key'), *(spec.get('bb_keys') or []), *(spec.get('origin_bb_keys') or [])})
                                or 'rate' not in {spec.get('bb_key'), *(spec.get('bb_keys') or [])}
                                for spec in specs):
                return None
            projected.extend((key, entry) for key in (parent_approval_keys if parent_approval else [approval_key]))
        signatures = {json.dumps(entry, sort_keys=True, ensure_ascii=False) for _, entry in projected}
        if len(signatures) == 1:
            return {**projected[0][1], '_rdps_whitelist_keys': sorted({key for key, _ in projected}),
                    '_keyword_origin': copy.deepcopy(origins)}
    return None


def _rdps_registry_known_non_rdps_entry(record: dict[str, Any]) -> dict[str, Any] | None:
    candidates = [
        _normalize_buff_id(record.get("event_key")),
        _normalize_buff_id(record.get("raw_event_key")),
    ]
    # A registry label is not a structural proof. Check the current typed root.
    roots = _load_current_buff_roots()
    type_index = _BUFF_ACTION_TYPES_INDEX
    for key in candidates:
        root = roots.get(key)
        if not isinstance(root, dict):
            continue
        attribute_block = root.get("attributeModifier")
        attribute_rows = (attribute_block.get("attributeModifiers")
                          if isinstance(attribute_block, dict) else None)
        action_types = type_index[1].get(key) if type_index is not None and type_index[0] is roots else None
        if action_types is None:
            action_types = set()
            pending = [root]
            while pending:
                value = pending.pop()
                if isinstance(value, dict):
                    type_name = str(value.get("$type") or "")
                    if type_name:
                        action_types.add(type_name.replace("+", "."))
                    pending.extend(value.values())
                elif isinstance(value, list):
                    pending.extend(value)
        unsafe_action_tokens = (
            "DamageScaleProcessor", "InstantModifyAttribute",
            "ModifyDynamicBlackboard", "DamageActionData",
        )
        safe_attribute_types = {
            "MoveSpeedScalar", "InAirMoveSpeedScalar", "TurnRateScalar",
            "NormalAttackRange", "PoiseRecTime", "PoiseRecTimeScalar",
            "ComboSkillCooldownScalar", "ComboSkillCooldownFinalAddition",
            "ComboSkillCooldownRecoveryScalar", "NormalSkillCooldownAddition",
            "SkillCooldownScalar", "AtbCostAddition", "MaxUltimateSp",
            "UltimateSpGainScalar", "MaxHp", "HealTakenIncrease",
            "HealOutputIncrease", "HpRecoveryPerSecByMaxHpRatio",
            "SlowActionSpeedScalar", "KeywordSpeedUpScalar",
            "KnockDownTimeAddition",
        }
        attribute_types_safe = (
            isinstance(attribute_rows, list)
            and all(isinstance(item, dict) and item.get("attributeType") in safe_attribute_types
                    for item in attribute_rows)
        )
        damage_modifiers_empty = (
            isinstance(root.get("damageModifier"), list)
            and all(isinstance(item, dict) and not item.get("damageProcessors")
                    for item in root.get("damageModifier") or [])
        )
        defensive_attribute_names = {
            "PhysicalDamageTakenScalar", "FireDamageTakenScalar",
            "PulseDamageTakenScalar", "CrystDamageTakenScalar",
            "EtherDamageTakenScalar", "NaturalDamageTakenScalar",
            "HealTakenIncrease",
        }
        defensive_attributes_only = (
            isinstance(attribute_rows, list)
            and bool(attribute_rows)
            and all(
                isinstance(item, dict)
                and item.get("attributeType") in defensive_attribute_names
                and item.get("modifyAttributeType") == "Specific"
                for item in attribute_rows
            )
        )
        defensive_damage_only = (
            isinstance(root.get("damageModifier"), list)
            and bool(root.get("damageModifier"))
            and all(
                isinstance(item, dict)
                and item.get("enableSide") == "Defender"
                and all(
                    isinstance(proc, dict)
                    and str(proc.get("$type") or "").endswith("DamageScaleProcessor")
                    and proc.get("side") == "Defender"
                    and proc.get("zoneName") == "ProdCalcZone"
                    for proc in item.get("damageProcessors") or []
                )
                for item in root.get("damageModifier") or []
            )
        )
        if (str(record.get("target_character_key") or "").startswith("chr_")
                and not record.get("target_enemy_key")
                and not any(any(token in item for token in (
                    "InstantModifyAttribute", "ModifyDynamicBlackboard", "DamageActionData"))
                    for item in action_types)
                and not root.get("globalModifier") and not root.get("healModifier")
                and ((defensive_attributes_only and damage_modifiers_empty)
                     or (defensive_attributes_only and defensive_damage_only))):
            return {
                "kind": "typed_defensive_only_effect",
                "proof_kind": "typed_root_structural",
                "reason": "current typed root only modifies incoming damage/healing on an identified player",
                "suppress_zone_effects": True,
            }
        non_allocating_types = {
            "Beyond.Blackboard.AssignPair", "Beyond.Blackboard.BlackboardDouble",
            "Beyond.Blackboard.BlackboardInt", "Beyond.Blackboard.DataPair",
            "Beyond.Gameplay.AttributeModifierData", "Beyond.Gameplay.Core.AbilityActionMap",
            "Beyond.Gameplay.Core.AttackClickListenerAction.Data",
            "Beyond.Gameplay.Core.BuffActionMap", "Beyond.Gameplay.Core.BuffData",
            "Beyond.Gameplay.Core.BuffIconConfig", "Beyond.Gameplay.Core.BuffIconDurationSourceSetting",
            "Beyond.Gameplay.Core.BuffStackingSettings", "Beyond.Gameplay.Core.CompareFloat.Data",
            "Beyond.Gameplay.Core.Conditions.CheckEntityNum.Data",
            "Beyond.Gameplay.Core.Conditions.CheckSkillType.Data",
            "Beyond.Gameplay.Core.CreateBuffAction.Data", "Beyond.Gameplay.Core.CreateBuffActionInput",
            "Beyond.Gameplay.Core.DirectionSettings", "Beyond.Gameplay.Core.FindTargetAction.FindTargetActionData",
            "Beyond.Gameplay.Core.GameplayTagQuery", "Beyond.Gameplay.Core.IfElseAction.IfElseActionData",
            "Beyond.Gameplay.Core.ModifyDynamicBlackboard.Data",
            "Beyond.Gameplay.Core.NotifyCharPassiveUIAction.Data",
            "Beyond.Gameplay.Core.PlaySoundAction.PlaySoundActionData",
            "Beyond.Gameplay.Core.ReadSkillSettingData.Data", "Beyond.Gameplay.Core.ReadSkillSettingData.ReadData",
            "Beyond.Gameplay.Core.Selector.DistanceValidator.Data",
            "Beyond.Gameplay.Core.Selector.OwnerSpawnedEntityFinder.Data",
            "Beyond.Gameplay.Core.Selector.SelectorData", "Beyond.Gameplay.Core.Selector.TagValidator.Data",
            "Beyond.Gameplay.Core.SequenceActionData", "Beyond.Gameplay.Core.SimpleCalcBBAction.Data",
            "Beyond.Gameplay.Core.TargetSettings", "Beyond.Gameplay.Core.TriggerLiinoUIEvent.Data",
        }
        no_allocatable_payload = (
            root.get("$type") == "Beyond.Gameplay.Core.BuffData"
            and isinstance(attribute_rows, list) and not attribute_rows
            and isinstance(root.get("damageModifier"), list) and not root.get("damageModifier")
            and not root.get("globalModifier") and not root.get("healModifier")
            and not root.get("poiseModifier") and not root.get("shieldConfigs")
            and action_types and action_types <= non_allocating_types
        )
        if no_allocatable_payload:
            return {
                "kind": "typed_root_no_allocatable_effect",
                "proof_kind": "typed_root_structural",
                "reason": "current typed root has no attribute, damage, global, heal, poise, or shield effect",
                "suppress_zone_effects": True,
            }
        if (isinstance(attribute_rows, list)
                and not any(any(token in item for token in unsafe_action_tokens) for item in action_types)
                and (not attribute_rows or attribute_types_safe)
                and damage_modifiers_empty
                and not root.get("globalModifier")
                and not root.get("healModifier")):
            return {
                "kind": "typed_root_no_direct_damage_execution",
                "proof_kind": "typed_root_structural",
                "reason": "version-bound BuffData root has no direct damage processor or modifier",
                "suppress_zone_effects": True,
            }
    return None


def _rdps_registry_suppresses_zone_effects(record: dict[str, Any]) -> bool:
    entry = _rdps_registry_known_non_rdps_entry(record)
    return bool(entry and entry.get("suppress_zone_effects"))


def _rdps_registry_verified_effect_entry(record: dict[str, Any], *, root_lookup_cache: dict | None = None) -> dict[str, Any] | None:
    return _rdps_registry_matching_effect_entry(record, require_guard=True, root_lookup_cache=root_lookup_cache)


def _rdps_registry_stack_limit(record: dict[str, Any]) -> int | None:
    entry = _rdps_registry_verified_effect_entry(record)
    if not isinstance(entry, dict):
        return None
    return _positive_int(entry.get("stack_limit"))


def _rdps_registry_candidate_effect_entry(record: dict[str, Any], *, root_lookup_cache: dict | None = None) -> dict[str, Any] | None:
    return _rdps_registry_matching_effect_entry(record, require_guard=False, root_lookup_cache=root_lookup_cache)


def _rdps_registry_effect_bb_keys(entry: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for effect in entry.get("effects") or []:
        if not isinstance(effect, dict):
            continue
        for field in ("bb_key", "add_bb_key", "delay_bb_key", "max_bb_key"):
            value = str(effect.get(field) or "")
            if value:
                keys.add(value)
        keys.update(str(value) for value in effect.get("bb_keys") or [] if str(value))
        keys.update(str(value) for value in effect.get("required_bb_keys") or [] if str(value))
    return keys


def _rdps_registry_ignored_bb_keys(entry: dict[str, Any]) -> set[str]:
    ignored = set(_RDPS_REGISTRY_CONTROL_BB_KEYS)
    ignored.update(_rdps_registry_effect_bb_keys(entry))
    ignored.update(str(key) for key in entry.get("ignored_bb_keys") or [] if str(key))
    ignored.update(str(key) for key in entry.get("self_bb_keys") or [] if str(key))
    ignored.update(str(key) for key in entry.get("allowed_bb_keys") or [] if str(key) != "*")
    return ignored


def _rdps_registry_unrecognized_effect_bb_keys(entry: dict[str, Any], record: dict[str, Any]) -> list[str]:
    ignored = _rdps_registry_ignored_bb_keys(entry)
    keys: set[str] = set(_record_bb_key_set(record))
    keys.update(str(key) for key in (record.get("bb_values") or {}).keys() if str(key))
    keys.update(str(mod.get("bb_key") or "") for mod in record.get("attr_mods") or [] if str(mod.get("bb_key") or ""))
    return sorted(
        key
        for key in keys
        if key not in ignored
    )


def _rdps_registry_effect_runtime_rate(
    spec: dict[str, Any],
    record: dict[str, Any],
) -> tuple[float | None, str | None]:
    bb_values = record.get("bb_values") or {}
    keys = list(dict.fromkeys([str(key) for key in spec.get("bb_keys") or [] if str(key)]))
    primary = str(spec.get("bb_key") or "")
    if primary and primary not in keys:
        keys.insert(0, primary)
    present = [(key, _coerce_optional_float(bb_values[key])) for key in keys if key in bb_values]
    if present:
        if any(value is None or value < 0 for _, value in present):
            record["rdps_invalid_runtime_rate"] = True
            return None, None
        if len({value for _, value in present}) != 1:
            record["rdps_conflicting_runtime_rate"] = True
            return None, None
        return present[0][1], present[0][0]
    formula = str(spec.get("formula") or "")
    if formula == "atk_add_plus_mult_consume_half_for_other_targets":
        add = _coerce_optional_float(bb_values.get(str(spec.get("add_bb_key") or "atk_up_add")))
        mult = _coerce_optional_float(bb_values.get(str(spec.get("mult_bb_key") or "atk_up_mult")))
        consumed = _coerce_optional_float(bb_values.get(str(spec.get("consume_bb_key") or "consume_layer")))
        if add is None or mult is None or consumed is None:
            return None, None
        rate = add + mult * consumed
        source = str(record.get("source_character_key") or "")
        target = str(record.get("target_character_key") or "")
        if target and source and target != source:
            factor = _coerce_optional_float(spec.get("other_target_multiplier", 0.5))
            if factor is None:
                return None, None
            rate *= factor
        return (rate, primary or str(spec.get("add_bb_key") or "atk_up_add")) if isfinite(rate) and rate >= 0 else (None, None)
    if str(spec.get("value_source") or "").lower() in {"constant", "static_literal"}:
        rate = _coerce_optional_float(spec.get("rate"))
        return (rate, primary or None) if rate is not None and rate >= 0 else (None, None)
    return None, None


def _rdps_registry_zone_effects(record: dict[str, Any], entry: dict[str, Any]) -> list[dict[str, Any]]:
    if entry.get("dynamic"):
        return []
    effects: list[dict[str, Any]] = []
    for spec in entry.get("effects") or []:
        if not isinstance(spec, dict):
            continue
        zone = str(spec.get("zone") or "")
        if zone not in _RDPS_ALLOCATABLE_ZONES and zone != "crit":
            continue
        rate, used_bb_key = _rdps_registry_effect_runtime_rate(spec, record)
        if rate is None:
            continue
        effect = {
            "zone": zone,
            "element": _normalize_effect_element(str(spec.get("element") or "all")),
            "rate": rate,
        }
        if used_bb_key:
            effect["_registry_bb_key"] = used_bb_key
            effect['_registry_bb_keys'] = sorted({used_bb_key, str(spec.get('bb_key') or ''),
                                                  *(str(key) for key in spec.get('bb_keys') or [])} - {''})
            if spec.get("expose_bb_key"):
                effect["bb_key"] = used_bb_key
        if "condition" in spec:
            effect["condition"] = copy.deepcopy(spec["condition"])
        effects.append(effect)
    return effects


def _rdps_registry_validation_error(entry: dict[str, Any], record: dict[str, Any]) -> dict[str, Any] | None:
    record.pop("rdps_invalid_runtime_rate", None)
    record.pop("rdps_conflicting_runtime_rate", None)
    if record.get("rdps_unresolved_semantic_condition"):
        return {"reason": "unresolved_semantic_condition", "condition_errors": record["rdps_unresolved_semantic_condition"]}
    if record.get("rdps_unresolved_dynamic_effect"):
        return {"reason": "unresolved_dynamic_effect_parameters"}
    if not _rdps_registry_bb_keys_allowed(entry, record):
        return {"reason": "registry allowed_bb_keys check failed"}
    unknown_bb_keys = _rdps_registry_unrecognized_effect_bb_keys(entry, record)
    if unknown_bb_keys:
        return {
            "reason": "verified rDPS buff carried BB keys not declared by rdps_semantics_registry",
            "unknown_bb_keys": unknown_bb_keys,
        }
    if entry.get("dynamic"):
        return None
    resolved = _rdps_registry_zone_effects(record, entry)
    if record.get("rdps_conflicting_runtime_rate"):
        return {"reason": "conflicting_runtime_rate"}
    if record.get("rdps_invalid_runtime_rate"):
        return {"reason": "invalid_runtime_rate"}
    if entry.get("effects") and len(resolved) != len(entry["effects"]):
        return {
            "reason": "verified rDPS buff did not expose required runtime BB value declared by rdps_semantics_registry",
            "required_bb_keys": sorted(_rdps_registry_effect_bb_keys(entry)),
        }
    return None



def _parse_loadout_suits(raw: str) -> set[str]:
    return {
        suit_id
        for suit_id, count in re.findall(r"\[([^\]]+)\]=(\d+)", raw or "")
        if int(count) >= 3
    }


@lru_cache(maxsize=1)
def _load_weapon_catalog() -> dict[str, dict[str, Any]]:
    root = _repo_root() / "data" / "akedata" / "weapon" / "items"
    catalog: dict[str, dict[str, Any]] = {}
    try:
        attack_catalog = json.loads((_repo_root() / 'data/packet_semantics/weapon_static_attack_catalog.json').read_text(encoding='utf8'))
    except (OSError, json.JSONDecodeError):
        attack_catalog = {}
    attack_fields = (attack_catalog.get('entries') or []) if (
        attack_catalog.get('schemaVersion') == 1
        and attack_catalog.get('source') == 'current_WeaponSkill_cardAttributeModifier'
        and attack_catalog.get('decoder') == _NATIVE_DAMAGE_SEMANTICS_BINDING) else []
    for path in root.glob("*.json") if root.is_dir() else []:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        weapon_id = str(payload.get("weaponId") or path.stem)
        catalog[weapon_id] = {
            "weapon_template": weapon_id,
            "weapon_name": str(payload.get("title") or payload.get("name") or weapon_id),
            "weapon_type": payload.get("weapontype"),
            "rarity": payload.get("rarity"),
            "base_atk_values": payload.get("baseAtk") if isinstance(payload.get("baseAtk"), list) else [],
            "skilllist": payload.get("skilllist") if isinstance(payload.get("skilllist"), list) else [],
        }
    local_manifest = _repo_root() / "data" / "local_tables" / "weapon" / "manifest.json"
    try:
        local_payload = json.loads(local_manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        local_payload = {}
    for item in local_payload.get("entries", []) if isinstance(local_payload, dict) else []:
        if not isinstance(item, dict):
            continue
        weapon_id = str(item.get("weaponId") or item.get("id") or "")
        if not weapon_id:
            continue
        current = catalog.setdefault(weapon_id, {"weapon_template": weapon_id, "base_atk_values": [], "skilllist": []})
        current["weapon_name"] = str(item.get("name") or current.get("weapon_name") or weapon_id)
        current["weapon_type"] = item.get("weaponType") or current.get("weapon_type")
        current["rarity"] = item.get("rarity") or current.get("rarity")
        definition_path = _repo_root() / 'data/local_tables/weapon/items' / f'{weapon_id}.json'
        try:
            definition_bytes = definition_path.read_bytes()
            definition = json.loads(definition_bytes)
        except (OSError, json.JSONDecodeError):
            continue
        skills = (definition.get('raw') or {}).get('weaponSkillList')
        if isinstance(skills, list) and all(isinstance(skill, str) for skill in skills):
            current['weapon_skill_ids'] = list(skills)
            current['weapon_definition_sha256'] = hashlib.sha256(definition_bytes).hexdigest()
            current['static_attack_fields'] = [dict(field) for field in attack_fields
                if field.get('weaponId') == weapon_id and field.get('skillId') in skills
                and field.get('modifyAttributeType') == 'Specific'
                and field.get('formulaItem') in {'BaseMultiplier', 'BaseFinalAddition'}
                and isinstance(field.get('bbKey'), str)
                and isinstance(field.get('sourceRootSha256'), str)
                and re.fullmatch(r'[0-9a-f]{64}', field['sourceRootSha256'])]
    return catalog


@lru_cache(maxsize=1)
def _load_character_base_stat_catalog() -> dict[str, dict[int, dict[str, Any]]]:
    root = _repo_root() / "data" / "local_tables" / "character" / "items"
    catalog: dict[str, dict[int, dict[str, Any]]] = {}
    if not root.is_dir():
        return catalog
    for path in root.glob("chr_*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        raw = payload.get("raw") if isinstance(payload.get("raw"), dict) else {}
        attributes = raw.get("attributes") if isinstance(raw, dict) else []
        if not isinstance(attributes, list):
            continue
        level_rows: dict[int, dict[str, Any]] = {}
        ambiguous_levels: set[int] = set()
        for row in attributes:
            if not isinstance(row, dict):
                continue
            attr_blob = row.get("Attribute") if isinstance(row.get("Attribute"), dict) else {}
            attrs = attr_blob.get("attrs") if isinstance(attr_blob, dict) else []
            if not isinstance(attrs, list):
                continue
            values: dict[int, float] = {}
            level = None
            for item in attrs:
                if not isinstance(item, dict):
                    continue
                try:
                    attr_type = int(item.get("attrType"))
                    attr_value = float(item.get("attrValue"))
                except (TypeError, ValueError):
                    continue
                values[attr_type] = attr_value
                if attr_type == 0 and isfinite(attr_value) and attr_value.is_integer():
                    level = int(attr_value)
            if level is None or level in ambiguous_levels:
                continue
            break_stage = _coerce_optional_int(row.get("breakStage"))
            previous = level_rows.get(level)
            current = {
                "level": level,
                "main_attr_type": raw.get('mainAttrType'),
                "sub_attr_type": raw.get('subAttrType'),
                "break_stage": break_stage,
                "hp": values.get(1),
                "atk": values.get(2),
                "def": values.get(3),
                "attr_39": values.get(39),
                "attr_40": values.get(40),
                "attr_41": values.get(41),
                "attr_42": values.get(42),
            }
            if previous is not None and previous != current:
                level_rows.pop(level)
                ambiguous_levels.add(level)
                continue
            level_rows[level] = current
        if level_rows:
            catalog[path.stem] = level_rows
    return catalog


def _character_base_stats(character_key: str | None, level: int | None) -> dict[str, Any] | None:
    key = str(character_key or "")
    if not key:
        return None
    rows = _load_character_base_stat_catalog().get(key)
    if not rows:
        return None
    if type(level) is not int or level <= 0:
        return None
    exact = rows.get(int(level))
    if exact is not None:
        return {
            **exact,
            "level_source": "loadout",
        }
    return None


@lru_cache(maxsize=1)
def _load_suit_catalog() -> dict[str, dict[str, Any]]:
    root = _repo_root() / "data" / "akedata" / "equip" / "items"
    catalog: dict[str, dict[str, Any]] = {}
    for path in root.glob("*.json") if root.is_dir() else []:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        suit_id = str(payload.get("suitID") or path.stem)
        catalog[suit_id] = {
            "suit_id": suit_id,
            "suit_name": str(payload.get("套组名称") or payload.get("name") or suit_id),
            "description": str(payload.get("技能描述") or ""),
            "value": payload.get("value") if isinstance(payload.get("value"), dict) else {},
        }
    local_root = _repo_root() / "data" / "local_tables" / "equip" / "items"
    for path in local_root.glob("*.json") if local_root.is_dir() else []:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        suit_id = str(payload.get("suitID") or path.stem)
        current = catalog.setdefault(suit_id, {"suit_id": suit_id})
        current.update({
            "suit_name": str(payload.get("name") or payload.get("displayName") or current.get("suit_name") or suit_id),
            "description": str(payload.get("skillDescription") or current.get("description") or ""),
            "value": payload.get("value") if isinstance(payload.get("value"), dict) else current.get("value", {}),
            "required_piece_count": payload.get("requiredPieceCount") if type(payload.get("requiredPieceCount")) is int else None,
        })
    return catalog


@lru_cache(maxsize=1)
def _load_weapon_refine_catalog() -> dict[str, Any]:
    path = _repo_root() / 'data' / 'local_tables' / 'weapon' / 'refine_catalog.json'
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return {}
    return data.get('entries', {}) if isinstance(data, dict) else {}


def _infer_verified_weapon_refine_from_levels(weapon_template: str, source_skills: Any, *, gem_terms: Any = None) -> int | None:
    entry = _load_weapon_refine_catalog().get(weapon_template, {})
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
        ids = {str(skill[key]) for key in ('skill_id', 'skillKey', 'skill_str_id', 'skill_int_id') if skill.get(key) not in (None, '', 0)}
        if not ids.intersection(accepted_ids):
            continue
        if not ids.issubset(accepted_ids):
            return None
        level = skill.get('level')
        if isinstance(level, bool) or not str(level).isdigit():
            return None
        # Base breakthrough/gem levels and refinement bonuses overlap.
        # Accept only totals with one possible refinement in the official tables.
        value = entry.get('skillLevelToRefine', {}).get(str(int(level)))
        if gem_cost is not None:
            if int(level) not in entry.get('supportedSkillLevels', []):
                return None
            candidates = {int(refine) for refine, bonus in entry['refineBonusBounds'].items()
                          for bound in entry['mainSkillBounds']
                          if min(bound['lowerBound'] + bonus['lowerBound'] + gem_cost,
                                 bound['upperBound'] + bonus['upperBound']) == int(level)}
            value = next(iter(candidates)) if len(candidates) == 1 else None
        if value is None:
            return None
        values.add(int(value))
    return next(iter(values)) if len(values) == 1 else None


@lru_cache(maxsize=1)
def _load_equip_item_catalog() -> dict[str, dict[str, Any]]:
    root = _repo_root() / "data" / "akedata" / "equip" / "items"
    catalog: dict[str, dict[str, Any]] = {}
    for path in root.glob("*.json") if root.is_dir() else []:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        suit_id = str(payload.get("suitID") or path.stem)
        suit_name = str(payload.get("套组名称") or suit_id)
        equip_items = payload.get("equip")
        if not isinstance(equip_items, dict):
            continue
        for item_id, item in equip_items.items():
            if not isinstance(item, dict):
                continue
            key = str(item.get("itemId") or item_id)
            catalog[key] = {
                "item_id": key,
                "item_name": str(item.get("name") or key),
                "suit_id": suit_id,
                "suit_name": suit_name,
                "part_name": str(item.get("部位") or ""),
                "rarity": item.get("rarity"),
                "main_attr": item.get("主词条") if isinstance(item.get("主词条"), dict) else None,
                "sub_attrs": item.get("副词条") if isinstance(item.get("副词条"), dict) else {},
            }
    local_root = _repo_root() / "data" / "local_tables" / "equip"
    suit_names: dict[str, str] = {}
    for suit_path in (local_root / "items").glob("*.json") if (local_root / "items").is_dir() else []:
        try:
            suit_payload = json.loads(suit_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(suit_payload, dict):
            suit_id = str(suit_payload.get("suitID") or suit_path.stem)
            suit_names[suit_id] = str(suit_payload.get("name") or suit_payload.get("displayName") or suit_id)
    pieces_path = local_root / "pieces.json"
    try:
        pieces_payload = json.loads(pieces_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pieces_payload = {}
    for item in pieces_payload.get("entries", []) if isinstance(pieces_payload, dict) else []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("itemId") or item.get("id") or "")
        if not key:
            continue
        typed_attributes = item.get('equipAttrModifiers')
        typed_proof = pieces_payload.get('sourceTableSha256')
        if key in catalog:
            catalog[key]['attribute_modifiers'] = typed_attributes
            catalog[key]['attribute_source_sha256'] = typed_proof
            continue
        main = item.get("displayBaseAttrModifier")
        sub_rows = item.get("displayAttrModifiers") if isinstance(item.get("displayAttrModifiers"), list) else []
        main_attr = None
        if isinstance(main, dict):
            main_attr = {"desc": f"attribute_type_{main.get('attrType')}", "value": main.get("attrValue")}
        sub_attrs: dict[str, dict[str, Any]] = {}
        for index, row in enumerate(sub_rows, start=1):
            if not isinstance(row, dict):
                continue
            values = [row.get("attrValue"), *(row.get("enhancedAttrValues") or [])]
            sub_attrs[f"词条{index}"] = {"desc": f"attribute_type_{row.get('attrType')}", "value": values}
        suit_id = str(item.get("suitID") or "suit_none")
        catalog[key] = {
            "item_id": key,
            "item_name": str(item.get("name") or key),
            "suit_id": suit_id,
            "suit_name": suit_names.get(suit_id, suit_id),
            "part_name": str(item.get("partName") or ""),
            "rarity": item.get("rarity"),
            "main_attr": main_attr,
            "sub_attrs": sub_attrs,
            "attribute_modifiers": typed_attributes,
            "attribute_source_sha256": typed_proof,
        }
    return catalog


def _parse_skill_int_ids(raw_line: str) -> list[str]:
    match = re.search(r"skillIntIds=\[([^\]]*)\]", raw_line)
    if not match:
        return []
    return re.findall(r"\d+", match.group(1))


def _parse_equip_levels(raw: str | None) -> list[dict[str, int]]:
    levels: list[dict[str, int]] = []
    for index, level in re.findall(r"(\d+):(\d+)", str(raw or "")):
        levels.append({"index": int(index), "level": int(level)})
    return levels


def _parse_inline_equip_stats(raw: str | None) -> list[dict[str, Any]]:
    stats: list[dict[str, Any]] = []
    for item in str(raw or "").split(";"):
        if ":" not in item or "=" not in item:
            continue
        slot, rest = item.split(":", 1)
        name, raw_value = rest.split("=", 1)
        level = None
        value_text = raw_value
        if "@" in raw_value:
            value_text, raw_level = raw_value.rsplit("@", 1)
            try:
                level = int(raw_level)
            except ValueError:
                level = None
        number = _safe_positive_rate(value_text)
        stats.append(
            {
                "slot": slot,
                "name": name,
                "value": number if number is not None else value_text,
                "level": level,
            }
        )
    return stats


def _parse_inline_equips(raw_line: str) -> list[dict[str, Any]]:
    equips_blob = _braced_field(raw_line, "equips")
    if not equips_blob:
        return []
    matches = list(re.finditer(r"\[(\d+)\]=", equips_blob))
    equips: list[dict[str, Any]] = []
    catalog = _load_equip_item_catalog()
    for index, match in enumerate(matches):
        slot = int(match.group(1))
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(equips_blob)
        entry = equips_blob[start:end].strip()
        item_id = entry.split("|", 1)[0].strip()
        meta = catalog.get(item_id, {})
        level_match = re.search(r"\|lv=([^|]+)", entry)
        stats_match = re.search(r"\|stats=(.*)$", entry)
        equips.append(
            {
                "slot": slot,
                "item_id": item_id,
                "item_name": meta.get("item_name") or item_id,
                "suit_id": meta.get("suit_id") or "",
                "suit_name": meta.get("suit_name") or "",
                "part_name": meta.get("part_name") or "",
                "rarity": meta.get("rarity"),
                "enhance_levels": _parse_equip_levels(level_match.group(1) if level_match else None),
                "stats": _parse_inline_equip_stats(stats_match.group(1) if stats_match else None),
                "catalog_main_attr": meta.get("main_attr"),
                "catalog_sub_attrs": meta.get("sub_attrs") or {},
            }
        )
    return equips


def _parse_suit_counts(raw: str | None) -> list[dict[str, Any]]:
    suits: list[dict[str, Any]] = []
    catalog = _load_suit_catalog()
    for suit_id, count_text in re.findall(r"\[([^\]]+)\]=(\d+)", str(raw or "")):
        count = int(count_text)
        meta = catalog.get(suit_id, {})
        suits.append(
            {
                "suit_id": suit_id,
                "suit_name": meta.get("suit_name") or suit_id,
                "piece_count": count,
                "required_piece_count": meta.get("required_piece_count"),
                "active": (type(meta.get("required_piece_count")) is int
                           and count >= int(meta["required_piece_count"])),
                "description": meta.get("description") or "",
                "value": meta.get("value") or {},
            }
        )
    return suits


def _parse_weapon_source_skills(blob: str | None) -> list[dict[str, Any]]:
    if not blob:
        return []
    skills: list[dict[str, Any]] = []
    for entry in _split_top_level(blob, ";"):
        if ":" not in entry:
            continue
        skill_id, rest = entry.split(":", 1)
        level_match = re.search(r"(?:^|:)level=([^:]+)", rest)
        potential_match = re.search(r"(?:^|:)potentialLv=([^:]+)", rest)
        bb_blob = _braced_field(f"bb={{{rest.split('bb={', 1)[1]}" if "bb={" in rest else "", "bb")
        skills.append(
            {
                "skill_id": skill_id.strip(),
                "level": _coerce_int(level_match.group(1), default=0) if level_match else None,
                "potential_level": _coerce_int(potential_match.group(1), default=0) if potential_match else None,
                "bb": _parse_bb_assignments(bb_blob),
            }
        )
    return skills














def _infer_weapon_refine_from_source_skills(
    weapon_template: str,
    source_skills: list[dict[str, Any]] | None,
) -> int | None:
    return _infer_verified_weapon_refine_from_levels(weapon_template, source_skills)


def _selected_weapon_base_atk(weapon_template: str, weapon_level: int | None) -> Any:
    values = _load_weapon_catalog().get(weapon_template, {}).get("base_atk_values")
    if not isinstance(values, list) or weapon_level is None:
        return None
    if 0 < weapon_level <= len(values):
        return values[weapon_level - 1]
    return None


def _parse_loadout_slot_snapshot(raw_line: str) -> dict[str, Any] | None:
    char_key = _token_field(raw_line, "char")
    if not char_key or not char_key.startswith("chr_"):
        return None
    weapon_template = _token_field(raw_line, "weaponTemplate") or ""
    # The wire format uses weaponLv=0 when the instance level is not present
    # (for example template_only syncs). Preserve that distinction instead of
    # exposing zero as a real level; static attack curves require a positive
    # level and must remain unknown otherwise.
    raw_weapon_level = _coerce_int(_token_field(raw_line, "weaponLv"), default=0)
    weapon_level = raw_weapon_level if raw_weapon_level > 0 else None
    weapon_meta = _load_weapon_catalog().get(weapon_template, {})
    equip_suit_blob = _braced_field(raw_line, "equipSuit") or ""
    raw_refine = _token_field(raw_line, "refine") or ""
    weapon_refine = int(raw_refine) if re.fullmatch(r"[0-5]", raw_refine) else None
    row = {
        "slot": _coerce_int(_token_field(raw_line, "slot"), default=0),
        "character_key": char_key,
        "character_name": _resolve_character_name(char_key) or char_key,
        "character_level": _coerce_int(_token_field(raw_line, "charLv"), default=0) or None,
        "potential": _coerce_int(_token_field(raw_line, "potential"), default=0),
        "weapon_inst_id": _token_field(raw_line, "weaponInstId"),
        "weapon_template": weapon_template,
        "weapon_name": weapon_meta.get("weapon_name") or weapon_template,
        "weapon_level": weapon_level,
        "weapon_refine": weapon_refine,
        "weapon_refine_source": "loadout" if weapon_refine is not None else None,
        "weapon_break": _coerce_int(_token_field(raw_line, "break"), default=0),
        "weapon_base_atk": _selected_weapon_base_atk(weapon_template, weapon_level),
        "weapon_catalog_skilllist": weapon_meta.get("skilllist") or [],
        "equip_suit_raw": "{" + equip_suit_blob + "}" if equip_suit_blob else "{}",
        "suit_effects": _parse_suit_counts(equip_suit_blob),
        "equips": _parse_inline_equips(raw_line),
        "skill_int_ids": _parse_skill_int_ids(raw_line),
        "weapon_source_skills": [],
        "weapon_refine_stats": [],
        "gem_template": None,
        "gem_terms": {},
    }
    unplaced = re.search(r'\bunplacedEquipInstIds=\[([^\]]*)\]', raw_line)
    if unplaced:
        row['unresolved_equip_inst_ids'] = re.findall(r'\d+', unplaced.group(1))
    complete = _token_field(raw_line, 'equipsComplete')
    if complete in {'0', '1'}:
        row['equipment_snapshot_complete'] = complete == '1'
    return row


def _parse_loadout_stats_snapshot(
    raw_line: str,
    *,
    loadout_context: dict[str, Any] | None = None,
    squad_actor_keys: dict[str, str] | None = None,
    allow_bound_gem: bool = False,
) -> dict[str, Any] | None:
    if " CHAR_SKILLS " in raw_line:
        parsed = _parse_char_skills_line(raw_line)
        if parsed is None or not loadout_context or not squad_actor_keys:
            return None
        char_key, levels = parsed
        owner_id = str(_token_field(raw_line, "ownerId") or "")
        if (squad_actor_keys.get(owner_id) != char_key
                or list(squad_actor_keys.values()).count(char_key) != 1
                or loadout_context.get("character_key") != char_key
                or not re.fullmatch(r"[1-9]\d*", str(loadout_context.get("weapon_inst_id") or ""))
                or loadout_context.get("weapon_refine") is not None):
            return None
        weapon_template = str(loadout_context.get("weapon_template") or "")
        main_skill = _load_weapon_refine_catalog().get(weapon_template, {}).get("mainSkillId")
        if not main_skill or main_skill not in levels:
            return None
        refine = _infer_verified_weapon_refine_from_levels(
            weapon_template, [{"skill_id": main_skill, "level": levels[main_skill]}]
        )
        if refine is None:
            return None
        return {**copy.deepcopy(loadout_context), "weapon_refine": refine,
                "weapon_refine_source": "source_skill",
                "weapon_refine_evidence": {"source": "CHAR_SKILLS", "owner_id": owner_id,
                    "weapon_inst_id": str(loadout_context["weapon_inst_id"]),
                    "skill_id": main_skill, "level": levels[main_skill]}}
    char_key = _token_field(raw_line, "char")
    if not char_key or not char_key.startswith("chr_"):
        return None
    weapon_template = _token_field(raw_line, "weaponTemplate") or ""
    raw_weapon_level = _coerce_int(_token_field(raw_line, "weaponLv"), default=0)
    weapon_level = raw_weapon_level if raw_weapon_level > 0 else None
    weapon_refine_stats = _parse_weapon_source_skills(_braced_field(raw_line, "weaponRefineStats"))
    weapon_source_skills = _parse_weapon_source_skills(_braced_field(raw_line, "weaponSourceSkills"))
    gem_terms = None
    weapon_inst = str(_token_field(raw_line, "weaponInstId") or "")
    gem_inst = str(_token_field(raw_line, "gemInstId") or "")
    raw_terms = _braced_field(raw_line, "gemTerms")
    if (allow_bound_gem and re.fullmatch(r"[1-9]\d*", weapon_inst) and re.fullmatch(r"[1-9]\d*", gem_inst)
            and _token_field(raw_line, "gemWeaponId") == weapon_inst and raw_terms):
        matches = [re.fullmatch(r"(\d+):[^,@{}]*@cost(\d+)", part.strip()) for part in raw_terms.split(',')]
        if all(matches):
            gem_terms = [{"term_num_id": int(match[1]), "cost": int(match[2])} for match in matches]
    inferred_refine = _infer_verified_weapon_refine_from_levels(
        weapon_template,
        weapon_source_skills or weapon_refine_stats,
        gem_terms=gem_terms,
    )
    return {
        "character_key": char_key,
        "weapon_template": weapon_template,
        "weapon_refine": inferred_refine,
        "weapon_refine_source": "source_skill" if inferred_refine is not None else None,
        "weapon_base_atk": _coerce_int(_token_field(raw_line, "weaponBaseAtk"), default=0)
        or _selected_weapon_base_atk(weapon_template, weapon_level),
        "weapon_base_atk_lv1": _coerce_int(_token_field(raw_line, "weaponBaseAtkLv1"), default=0),
        "weapon_base_atk_max": _coerce_int(_token_field(raw_line, "weaponBaseAtkMax"), default=0),
        "weapon_refine_stats": weapon_refine_stats,
        "weapon_source_skills": weapon_source_skills,
        "gem_template": _token_field(raw_line, "gemTemplate"),
        "gem_terms": _parse_bb_assignments((_braced_field(raw_line, "gemTerms") or "").replace(":", "=")),
    }


def _merge_loadout_snapshot(base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    current_refine_source = merged.get("weapon_refine_source")
    update_refine_source = update.get("weapon_refine_source")
    for key, value in update.items():
        if update.get('equipment_snapshot_complete') is True and key in {
                'equips', 'suit_effects', 'equip_suit_raw', 'unresolved_equip_inst_ids'}:
            merged[key] = copy.deepcopy(value)
            continue
        if value in (None, "", [], {}):
            continue
        if key == "equips" and isinstance(value, list):
            current = merged.get("equips")
            if isinstance(current, list) and current:
                merged["equips"] = _merge_loadout_equips_by_slot(
                    current,
                    value,
                    suit_counts=_suit_effect_count_map(update.get("suit_effects")),
                )
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


def _select_loadout_snapshot_for_battle(
    loadout_groups: list[dict[str, Any]],
    fallback_by_char: dict[str, dict[str, Any]],
    *,
    anchor_ms: int | None,
    anchor_line_no: int | None = None,
) -> dict[str, dict[str, Any]]:
    """Apply observed loadout updates in source order up to the battle boundary."""
    groups = [group for group in loadout_groups if isinstance(group.get('rows'), dict) and group['rows']]
    if not groups:
        return {key: dict(value) for key, value in fallback_by_char.items()}
    eligible = [group for group in groups if anchor_ms is None
                or (type(group.get('ts_ms')) is int and (group['ts_ms'] < anchor_ms
                    or (group['ts_ms'] == anchor_ms and (anchor_line_no is None
                        or int(group.get('line_no') or 0) <= anchor_line_no))))]
    if not eligible:
        return {}
    state: dict[str, dict[str, Any]] = {}
    selected_rows: dict[str, Any] = {}
    for group in eligible:
        selected_rows = group['rows']
        for key, value in selected_rows.items():
            if not isinstance(value, dict):
                continue
            character_key = str(key)
            state[character_key] = _merge_tracked_loadout_state(state.get(character_key, {}), value)
    # Restrict output to the observed snapshot's character keys. Do not merge
    # future fallback state into the historical battle or rank packet reasons.
    return {str(key): state[str(key)] for key in selected_rows if str(key) in state}


_WEAPON_LOADOUT_STATE_FIELDS = {
    "weapon_refine_evidence",
    "weapon_inst_id",
    "weapon_template",
    "weapon_name",
    "weapon_level",
    "weapon_refine",
    "weapon_refine_source",
    "weapon_break",
    "weapon_base_atk",
    "weapon_base_atk_lv1",
    "weapon_base_atk_max",
    "weapon_catalog_skilllist",
    "weapon_source_skills",
    "weapon_refine_stats",
    "gem_template",
    "gem_terms",
}


def _loadout_weapon_source_skill_ids(row: dict[str, Any] | None) -> set[int]:
    if not isinstance(row, dict):
        return set()
    result: set[int] = set()
    for item in row.get("weapon_source_skills") or row.get("weapon_refine_stats") or []:
        if not isinstance(item, dict):
            continue
        skill_int_id = _coerce_int(item.get("skill_id"), default=0)
        if skill_int_id:
            result.add(skill_int_id)
    return result


def _loadout_with_weapon_state(
    destination: dict[str, Any],
    source: dict[str, Any],
) -> dict[str, Any]:
    merged = copy.deepcopy(destination)
    previous_source_ids = _loadout_weapon_source_skill_ids(destination)
    for key in _WEAPON_LOADOUT_STATE_FIELDS:
        if key in source:
            merged[key] = copy.deepcopy(source[key])
        else:
            merged.pop(key, None)

    destination_potential = int(destination.get("potential") or 0)
    for field in ("weapon_source_skills", "weapon_refine_stats"):
        for item in merged.get(field) or []:
            if isinstance(item, dict):
                item["potential_level"] = destination_potential

    next_source_ids = _loadout_weapon_source_skill_ids(merged)
    skill_int_ids = [
        int(skill_int_id)
        for skill_int_id in destination.get("skill_int_ids") or []
        if int(skill_int_id) and int(skill_int_id) not in previous_source_ids
    ]
    for skill_int_id in next_source_ids:
        if skill_int_id not in skill_int_ids:
            skill_int_ids.append(skill_int_id)
    merged["skill_int_ids"] = skill_int_ids
    return merged


def _loadout_without_mismatched_weapon_skills(row: dict[str, Any]) -> dict[str, Any]:
    weapon_template = str(row.get("weapon_template") or "")
    if not weapon_template.startswith("wpn_"):
        return row
    expected_main_skill = f"sk_{weapon_template}"
    skill_id_map = _load_num_id_str_skill_map()
    main_skills = {
        skill_id_map.get(str(item.get("skill_id") or ""), str(item.get("skill_id") or ""))
        for item in row.get("weapon_source_skills") or row.get("weapon_refine_stats") or []
        if isinstance(item, dict)
    }
    main_skills = {skill_id for skill_id in main_skills if skill_id.startswith("sk_wpn_")}
    if not main_skills or expected_main_skill in main_skills:
        return row

    sanitized = copy.deepcopy(row)
    stale_source_ids = _loadout_weapon_source_skill_ids(sanitized)
    sanitized["weapon_source_skills"] = []
    sanitized["weapon_refine_stats"] = []
    if sanitized.get("weapon_refine_source") == "source_skill":
        sanitized["weapon_refine"] = None
        sanitized["weapon_refine_source"] = None
    sanitized["skill_int_ids"] = [
        int(skill_int_id)
        for skill_int_id in sanitized.get("skill_int_ids") or []
        if int(skill_int_id) and int(skill_int_id) not in stale_source_ids
    ]
    return sanitized


def _merge_tracked_loadout_state(
    previous: dict[str, Any] | None,
    row: dict[str, Any],
) -> dict[str, Any]:
    """Merge adjacent partial snapshots without retaining the old weapon.

    `_merge_loadout_snapshot` intentionally protects a source-skill-derived
    refine from weaker updates.  That protection must not cross a weapon
    instance change, where the new weapon's whole state is authoritative.
    """

    previous = previous or {}
    merged = _merge_loadout_snapshot(previous, row)
    previous_inst_id = str(previous.get("weapon_inst_id") or "")
    next_inst_id = str(row.get("weapon_inst_id") or "")
    if previous_inst_id and next_inst_id and previous_inst_id != next_inst_id:
        merged = _loadout_with_weapon_state(merged, row)
    return merged


def _repair_weapon_puton_loadout_groups(
    loadout_groups: list[dict[str, Any]],
    fallback_by_char: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Repair the partial state emitted by the old SC_WEAPON_PUTON handler.

    The legacy client updated only the receiver's weapon instance.  When a
    weapon was moved from another character, the previous owner retained the
    same instance and both characters retained their pre-swap weapon skills.
    The surrounding loadout groups contain enough information to reconstruct
    the atomic swap deterministically.
    """

    raw_state_by_char: dict[str, dict[str, Any]] = {}
    corrected_state_by_char: dict[str, dict[str, Any]] = {}

    for group in loadout_groups:
        rows = group.get("rows")
        if not isinstance(rows, dict):
            continue
        raw_rows = {
            str(char_key): copy.deepcopy(row)
            for char_key, row in rows.items()
            if isinstance(row, dict)
        }
        corrected_rows = copy.deepcopy(raw_rows)
        reason = str(group.get("reason") or "")

        if reason != "SC_SYNC_CHAR_BAG_INFO":
            # Equipment/scene/battle snapshots repeat the cached weapon state.
            # Carry an earlier correction forward while the raw instance for
            # that character has not changed.
            for char_key, raw_row in raw_rows.items():
                previous_raw = raw_state_by_char.get(char_key)
                previous_corrected = corrected_state_by_char.get(char_key)
                if not previous_raw or not previous_corrected:
                    continue
                if all(
                    previous_raw.get(field) == previous_corrected.get(field)
                    for field in _WEAPON_LOADOUT_STATE_FIELDS
                ):
                    continue
                if str(raw_row.get("weapon_inst_id") or "") == str(
                    previous_raw.get("weapon_inst_id") or ""
                ):
                    corrected_rows[char_key] = _loadout_with_weapon_state(
                        raw_row,
                        previous_corrected,
                    )

        state_updates: dict[str, dict[str, Any]] = {}
        if reason == "SC_WEAPON_PUTON":
            for receiver_key, raw_receiver in raw_rows.items():
                previous_raw_receiver = raw_state_by_char.get(receiver_key)
                previous_corrected_receiver = corrected_state_by_char.get(receiver_key)
                if not previous_raw_receiver or not previous_corrected_receiver:
                    continue
                new_weapon_inst_id = str(raw_receiver.get("weapon_inst_id") or "")
                if not new_weapon_inst_id or new_weapon_inst_id == str(
                    previous_raw_receiver.get("weapon_inst_id") or ""
                ):
                    continue

                previous_owner_key = next(
                    (
                        char_key
                        for char_key, previous_row in corrected_state_by_char.items()
                        if char_key != receiver_key
                        and str(previous_row.get("weapon_inst_id") or "") == new_weapon_inst_id
                    ),
                    None,
                )
                if previous_owner_key is None:
                    corrected_rows[receiver_key] = _loadout_without_mismatched_weapon_skills(
                        corrected_rows[receiver_key]
                    )
                    continue

                previous_owner_state = corrected_state_by_char[previous_owner_key]
                corrected_receiver = _loadout_with_weapon_state(
                    raw_receiver,
                    previous_owner_state,
                )
                owner_destination = corrected_rows.get(
                    previous_owner_key,
                    corrected_state_by_char[previous_owner_key],
                )
                corrected_previous_owner = _loadout_with_weapon_state(
                    owner_destination,
                    previous_corrected_receiver,
                )
                corrected_rows[receiver_key] = corrected_receiver
                if previous_owner_key in corrected_rows:
                    corrected_rows[previous_owner_key] = corrected_previous_owner
                else:
                    state_updates[previous_owner_key] = corrected_previous_owner

        for char_key, row in list(corrected_rows.items()):
            corrected_rows[char_key] = _loadout_without_mismatched_weapon_skills(row)
        group["rows"] = corrected_rows
        # A logical snapshot can arrive as adjacent LOADOUT / LOADOUT_STATS
        # records (and therefore as separate groups).  Preserve the fields
        # from both halves while advancing the raw/corrected state machines.
        for char_key, row in raw_rows.items():
            raw_state_by_char[char_key] = _merge_tracked_loadout_state(
                raw_state_by_char.get(char_key), row
            )
        for char_key, row in corrected_rows.items():
            corrected_state_by_char[char_key] = _merge_tracked_loadout_state(
                corrected_state_by_char.get(char_key), row
            )
        for char_key, row in state_updates.items():
            corrected_state_by_char[char_key] = _merge_tracked_loadout_state(
                corrected_state_by_char.get(char_key), row
            )

    repaired_fallback = {
        str(char_key): copy.deepcopy(row)
        for char_key, row in fallback_by_char.items()
        if isinstance(row, dict)
    }
    for char_key, row in corrected_state_by_char.items():
        repaired_fallback[char_key] = _merge_tracked_loadout_state(
            repaired_fallback.get(char_key), row
        )
    return repaired_fallback






def _suit_effect_count_map(value: object) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in value or []:
        if not isinstance(item, dict):
            continue
        suit_id = str(item.get("suit_id") or "")
        if not suit_id:
            continue
        counts[suit_id] = int(item.get("piece_count") or 0)
    return counts




def _merge_loadout_equips_by_slot(
    base: list[dict[str, Any]],
    update: list[dict[str, Any]],
    *,
    suit_counts: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = [dict(equip) for equip in base if isinstance(equip, dict)]
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
            # A different item replaces the whole old row. Its absent stats
            # cannot be borrowed from the previous item in this slot.
            merged[slot_index[slot]] = (dict(equip) if existing.get('item_id') != equip.get('item_id') else {
                **existing, **equip})
        else:
            merged.append(dict(equip))
            if slot is not None:
                slot_index[slot] = len(merged) - 1

    return sorted(
        merged,
        key=lambda equip: int(equip.get("slot")) if str(equip.get("slot", "")).lstrip("-").isdigit() else 99,
    )


def _packet_mapping_applies(
    mapping: dict[str, Any],
    *,
    owner_key: str | None,
    source_key: str | None,
    active_suits_by_char: dict[str, set[str]] | None = None,
    active_weapons_by_char: dict[str, str] | None = None,
) -> bool:
    character_id = str(mapping.get("character_id") or "")
    if character_id and character_id not in {owner_key, source_key}:
        return False

    suit_id = str(mapping.get("suit_id") or "")
    if suit_id and active_suits_by_char:
        guard_key = str(source_key or owner_key or "")
        has_actor_loadout = bool(guard_key and guard_key in active_suits_by_char)
        active_suits = active_suits_by_char.get(guard_key, set()) if has_actor_loadout else set()
        if has_actor_loadout and suit_id not in active_suits:
            return False

    weapon_id = str(mapping.get("weapon_id") or "")
    if weapon_id and active_weapons_by_char:
        guard_key = str(source_key or owner_key or "")
        active_weapon = active_weapons_by_char.get(guard_key) if guard_key else None
        if active_weapon and weapon_id != active_weapon:
            return False

    return True


_STATIC_PACKET_BUFF_ALIASES = {
    # Early packet traces used this synthetic alias for suit_usp02's actual
    # damage-up child. Normalize it to the static buff id so suit gating and
    # semantic lookup see the real equipment buff.
    "buff_equipsuit_usp_02_dmgup": "buff_equipsuit_usp_02_AddAttack",
}


def _canonical_packet_buff_id(
    buff_id: str | None,
    *,
    owner_key: str | None = None,
    source_key: str | None = None,
    active_suits_by_char: dict[str, set[str]] | None = None,
    active_weapons_by_char: dict[str, str] | None = None,
) -> str:
    raw_buff_id = _normalize_buff_id(buff_id)
    if raw_buff_id in _STATIC_PACKET_BUFF_ALIASES:
        return _STATIC_PACKET_BUFF_ALIASES[raw_buff_id]
    hint = _packet_numeric_buff_hint(raw_buff_id)
    if hint and not _packet_mapping_applies(
        hint,
        owner_key=owner_key,
        source_key=source_key,
        active_suits_by_char=active_suits_by_char,
        active_weapons_by_char=active_weapons_by_char,
    ):
        return raw_buff_id
    canonical = hint.get("canonical_buff_id")
    if canonical:
        return str(canonical)
    fallback_ids = {
        fallback
        for candidate_key in (source_key, owner_key, None)
        if (fallback := _canonical_num_table_buff_number(raw_buff_id, candidate_key))
    }
    if len(fallback_ids) == 1:
        return next(iter(fallback_ids))
    return raw_buff_id


def _preserve_raw_numeric_internal_trigger_buff_id(record: dict[str, Any]) -> None:
    raw_buff_id = _normalize_buff_id(record.get("raw_event_key") or record.get("event_key"))
    if not raw_buff_id.isdigit():
        return
    if isinstance(record.get("packet_mapping"), dict):
        return
    if _is_internal_trigger_damage_record(record):
        record["event_key"] = raw_buff_id


def _is_packet_effectless_wrapper(record_or_buff_id: dict[str, Any] | str | None) -> bool:
    if isinstance(record_or_buff_id, dict):
        hint = record_or_buff_id.get("packet_mapping")
        if not isinstance(hint, dict):
            raw_buff_id = _normalize_buff_id(record_or_buff_id.get("raw_event_key") or record_or_buff_id.get("event_key"))
            hint = _packet_numeric_buff_hint(raw_buff_id)
    else:
        hint = _packet_numeric_buff_hint(record_or_buff_id)
    role = str(hint.get("role") or "").lower()
    has_effect_specs = bool(hint.get("effects") or hint.get("dynamic_effects"))
    return role in {"wrapper", "marker", "utility"} and not has_effect_specs


def _packet_mapping_allows_static_rate(hint: dict[str, Any]) -> bool:
    return bool(
        hint.get("allow_static_rate")
        or hint.get("allow_static_rates")
        or str(hint.get("value_source") or "").lower() in {"static_literal", "constant"}
    )


def _packet_numeric_effects(record: dict[str, Any]) -> list[dict[str, Any]]:
    raw_buff_id = _normalize_buff_id(record.get("raw_event_key") or record.get("event_key"))
    hint = record.get("packet_mapping")
    if not isinstance(hint, dict):
        hint = _packet_numeric_buff_hint(raw_buff_id)
    specs = hint.get("effects")
    bb_values = {str(key): value for key, value in (record.get("bb_values") or {}).items()}
    if not isinstance(specs, list):
        specs = _enhanced_action_child_effect_specs(str(record.get("event_key") or ""))
        if not specs:
            return []
    allow_static_rate = _packet_mapping_allows_static_rate(hint)
    effects: list[dict[str, Any]] = []
    for spec in specs:
        if not isinstance(spec, dict):
            continue
        zone = str(spec.get("zone") or "")
        if zone not in _DPD_ZONE_BUCKETS and zone != "crit":
            continue
        element = _normalize_effect_element(str(spec.get("element") or "all"))
        bb_key = str(spec.get("bb_key") or "")
        rate = _safe_positive_rate(bb_values.get(bb_key)) if bb_key else None
        if rate is None and str(spec.get("prefer_runtime_rate_key") or ""):
            rate = _safe_positive_rate(bb_values.get(str(spec.get("prefer_runtime_rate_key") or "")))
        if rate is None and allow_static_rate:
            rate = _safe_positive_rate(spec.get("rate"))
        if rate is None:
            continue
        effect = {"zone": zone, "element": element, "rate": rate}
        if bb_key:
            effect["bb_key"] = bb_key
            _attach_bb_key_damage_type_condition(effect, bb_key)
        effects.append(effect)
    return effects


def _packet_numeric_dynamic_effects(record: dict[str, Any]) -> list[dict[str, Any]]:
    raw_buff_id = _normalize_buff_id(record.get("raw_event_key") or record.get("event_key"))
    hint = record.get("packet_mapping")
    if not isinstance(hint, dict):
        hint = _packet_numeric_buff_hint(raw_buff_id)
    specs = hint.get("dynamic_effects")
    if not isinstance(specs, list):
        return []
    bb_values = {str(key): value for key, value in (record.get("bb_values") or {}).items()}
    allow_static_rate = _packet_mapping_allows_static_rate(hint)
    effects: list[dict[str, Any]] = []
    for spec in specs:
        if not isinstance(spec, dict):
            continue
        zone = str(spec.get("zone") or "")
        if zone not in _DPD_ZONE_BUCKETS and zone != "crit":
            continue
        element = _normalize_effect_element(str(spec.get("element") or "all"))
        base_bb_keys = [str(key) for key in spec.get("base_bb_keys") or [] if str(key)]
        base_bb_key = str(spec.get("base_bb_key") or spec.get("bb_key") or "")
        if not base_bb_keys and base_bb_key:
            base_bb_keys = [base_bb_key]
        base_rate = None
        for candidate_key in base_bb_keys:
            base_rate = _safe_positive_rate(bb_values.get(candidate_key))
            if base_rate is not None:
                base_bb_key = candidate_key
                break
        if base_rate is None:
            base_rate = _safe_positive_rate(spec.get("base_rate")) if allow_static_rate else None
        if base_rate is None:
            base_rate = 0.0
        add_bb_key = str(spec.get("add_bb_key") or "")
        delayed_add_rate = _safe_positive_rate(bb_values.get(add_bb_key)) if add_bb_key else None
        if delayed_add_rate is None:
            delayed_add_rate = _safe_positive_rate(spec.get("delayed_add_rate")) if allow_static_rate else None
        if delayed_add_rate is None:
            delayed_add_rate = 0.0
        delay_bb_key = str(spec.get("delay_bb_key") or "")
        delay_sec = _safe_positive_rate(bb_values.get(delay_bb_key)) if delay_bb_key else None
        if delay_sec is None and allow_static_rate:
            delay_sec = _safe_positive_rate(spec.get("delay_sec")) or 0.0
        if delay_sec is None:
            delay_sec = 0.0
        tick_rate = (_safe_positive_rate(spec.get("tick_rate")) or 0.0) if allow_static_rate else 0.0
        tick_bb_key = ""
        for candidate_key in [str(key) for key in spec.get("tick_bb_keys") or [] if str(key)]:
            runtime_tick_rate = _safe_positive_rate(bb_values.get(candidate_key))
            if runtime_tick_rate is not None:
                tick_bb_key = candidate_key
                tick_rate = runtime_tick_rate * (float(spec.get("tick_multiplier") or 1.0))
                break
        max_bb_key = str(spec.get("max_bb_key") or "")
        max_rate = _safe_positive_rate(bb_values.get(max_bb_key)) if max_bb_key else None
        if max_rate is None:
            max_rate = (_safe_positive_rate(spec.get("max_rate")) or 0.0) if allow_static_rate else 0.0
        if base_rate <= 0 and delayed_add_rate <= 0 and tick_rate <= 0:
            continue
        effect = {
            "zone": zone,
            "element": element,
            "base_rate": base_rate,
            "tick_rate": tick_rate,
            "max_rate": max_rate,
            "delayed_add_rate": delayed_add_rate,
            "delay_sec": delay_sec,
        }
        if base_bb_key:
            effect["bb_key"] = base_bb_key
        if add_bb_key:
            effect["add_bb_key"] = add_bb_key
        if delay_bb_key:
            effect["delay_bb_key"] = delay_bb_key
        if tick_bb_key:
            effect["tick_bb_key"] = tick_bb_key
        if max_bb_key:
            effect["max_bb_key"] = max_bb_key
        if spec.get("dynamic_key"):
            effect["dynamic_key"] = spec.get("dynamic_key")
        effects.append(effect)
    return effects


def _packet_mapping_stack_limit(record: dict[str, Any]) -> int | None:
    bb_values = record.get("bb_values") if isinstance(record.get("bb_values"), dict) else {}
    for key in ("max_stack", "stack_limit", "maxStack", "stackLimit"):
        limit = _positive_int(bb_values.get(key))
        if limit is not None:
            return limit
    hint = record.get("packet_mapping")
    if not isinstance(hint, dict):
        raw_buff_id = _normalize_buff_id(record.get("raw_event_key") or record.get("event_key"))
        hint = _packet_numeric_buff_hint(raw_buff_id)
    return _positive_int(hint.get("stack_limit"))


@lru_cache(maxsize=1)
def _load_packet_numeric_skill_map() -> dict[str, dict[str, Any]]:
    map_path = _repo_root() / "data" / "packet_semantics" / "skill_numeric_map.json"
    if not map_path.exists():
        return {}
    try:
        payload = json.loads(map_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    mappings = payload.get("mappings")
    if not isinstance(mappings, dict):
        return {}
    return {
        str(skill_id): mapping
        for skill_id, mapping in mappings.items()
        if isinstance(mapping, dict)
    }


def _packet_numeric_skill_hint(skill_key: str | None) -> dict[str, Any]:
    return _load_packet_numeric_skill_map().get(str(skill_key or ""), {})


@lru_cache(maxsize=1)
def _load_num_id_str_skill_map() -> dict[str, str]:
    candidate_paths = (
        _repo_root().parent / "endfield_tables" / "Data" / "TableCfg" / "NumIdStrTable.json",
        _repo_root() / "data" / "local_tables" / "NumIdStrTable.json",
    )
    for path in candidate_paths:
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        skill_section = payload.get("skill_id") if isinstance(payload, dict) else None
        if not isinstance(skill_section, dict):
            continue
        mapping = skill_section.get("dic")
        if not isinstance(mapping, dict):
            continue
        return {
            str(skill_id): str(static_id)
            for skill_id, static_id in mapping.items()
            if static_id is not None
        }
    return {}


@lru_cache(maxsize=1)
def _load_num_id_str_buff_map() -> dict[str, str]:
    candidate_paths = (
        _repo_root().parent / "endfield_tables" / "Data" / "TableCfg" / "NumIdStrTable.json",
        _repo_root() / "data" / "local_tables" / "NumIdStrTable.json",
    )
    for path in candidate_paths:
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        buff_section = payload.get("buff_id") if isinstance(payload, dict) else None
        if not isinstance(buff_section, dict):
            continue
        mapping = buff_section.get("dic")
        if not isinstance(mapping, dict):
            continue
        return {
            str(buff_id): str(static_id)
            for buff_id, static_id in mapping.items()
            if static_id is not None
        }
    return {}


def _canonical_num_table_skill_number(skill_number: str | None, character_key: str | None) -> str | None:
    if not skill_number:
        return None
    static_id = _load_num_id_str_skill_map().get(str(skill_number))
    if not static_id:
        return None
    if not _runtime_owner_matches_static_domain(character_key, static_id):
        return None
    if not character_key:
        return static_id
    match = _CHAR_KEY_RE.search(static_id)
    if match and not _character_key_matches_static_family(str(character_key), match.group(1)):
        return None
    return static_id


def _canonical_num_table_skill_id(skill_key: str | None, character_key: str | None) -> str | None:
    skill_number = _runtime_skill_number(skill_key)
    return _canonical_num_table_skill_number(skill_number, character_key)


def _parse_char_skills_line(raw_line: str) -> tuple[str, dict[str, int]] | None:
    """解析 bridge 的 CHAR_SKILLS 行 → (角色key, {技能key: 等级})。
    运行时兜底 id（纯数字 / chr_xxx_skill_766）经 num 表解析，失败自弃。"""
    fields = _extract_fields(raw_line)
    owner_key = str(fields.get("owner") or "")
    tokens_match = re.search(r"skills=\[([^\]]*)\]", raw_line)
    if not owner_key or not tokens_match:
        return None
    levels: dict[str, int] = {}
    conflicted: set[str] = set()
    for token in tokens_match.group(1).split():
        skill_id, _, level_text = token.rpartition(":")
        level = _coerce_int(level_text, default=0)
        if not skill_id or level <= 0:
            continue
        if _is_generic_runtime_skill_id(skill_id) or re.fullmatch(r".*_skill_\d+", skill_id) or skill_id.isdigit():
            resolved = _canonical_num_table_skill_id(skill_id, owner_key)
            if not resolved:
                continue
            skill_id = resolved
        if skill_id in conflicted:
            continue
        if skill_id in levels and levels[skill_id] != level:
            levels.pop(skill_id)
            conflicted.add(skill_id)
        else:
            levels[skill_id] = level
    if not levels:
        return None
    return owner_key, levels


def extract_char_skill_levels_from_text(text: str) -> dict[str, dict[str, int]]:
    """全文提取 CHAR_SKILLS（bridge 在战前落盘，可能在 battle 分段之外——
    与 _raw_text_loadout_entries 同理由 uploader 对整个 trace 调用）。同角色取最后一次。"""
    result: dict[str, dict[str, int]] = {}
    for raw_line in text.splitlines():
        if " CHAR_SKILLS " not in raw_line:
            continue
        parsed = _parse_char_skills_line(raw_line)
        if parsed is not None:
            result[parsed[0]] = parsed[1]
    return result


def _canonical_num_table_buff_number(buff_number: str | None, character_key: str | None) -> str | None:
    if not buff_number:
        return None
    static_id = _load_num_id_str_buff_map().get(str(buff_number))
    if not static_id:
        return None
    if not _runtime_owner_matches_static_domain(character_key, static_id):
        return None
    if not character_key:
        return static_id
    match = _CHAR_KEY_RE.search(static_id)
    if match and not _character_key_matches_static_family(str(character_key), match.group(1)):
        return None
    return static_id


def _character_key_matches_static_family(character_key: str | None, static_character_key: str | None) -> bool:
    character = str(character_key or "")
    static_character = str(static_character_key or "")
    if not character or not static_character:
        return True
    if character == static_character:
        return True
    if character == "chr_9000_endmin" and static_character in _ENDMIN_VARIANTS:
        return True
    return False


def _runtime_owner_matches_static_domain(character_key: str | None, static_id: str | None) -> bool:
    owner = str(character_key or "")
    static = str(static_id or "")
    if not owner or not static:
        return True
    if owner.startswith("chr_") and _ENEMY_KEY_RE.search(static):
        return False
    if owner.startswith("eny_") and _CHAR_KEY_RE.search(static):
        return False
    return True


@lru_cache(maxsize=1)
def _load_canonical_skill_display_names() -> dict[str, str]:
    display_names: dict[str, str] = {}
    skill_map = _load_packet_numeric_skill_map()
    map_path = _repo_root() / "data" / "packet_semantics" / "skill_numeric_map.json"
    if map_path.exists():
        try:
            payload = json.loads(map_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        preserved = payload.get("canonical_display_names") if isinstance(payload, dict) else {}
        if isinstance(preserved, dict):
            for canonical_skill_id, display_name in preserved.items():
                canonical_text = str(canonical_skill_id or "").strip()
                display_text = str(display_name or "").strip()
                if not canonical_text or not display_text:
                    continue
                existing = display_names.get(canonical_text, "")
                if not existing or len(display_text) > len(existing):
                    display_names[canonical_text] = display_text
    for mapping in skill_map.values():
        canonical_skill_id = str(mapping.get("canonical_skill_id") or "")
        display_name = str(mapping.get("display_name") or "").strip()
        if not canonical_skill_id or not display_name:
            continue
        existing = display_names.get(canonical_skill_id, "")
        if not existing or len(display_name) > len(existing):
            display_names[canonical_skill_id] = display_name
    return display_names


def _runtime_skill_number(skill_key: str | None) -> str | None:
    raw_skill_key = str(skill_key or "")
    match = _RUNTIME_SKILL_ID_RE.match(raw_skill_key)
    if match:
        return match.group(1)
    match = _COMPOSITE_RUNTIME_SKILL_RE.match(raw_skill_key)
    if match:
        return match.group(2)
    return None


def _is_runtime_numeric_skill_id(skill_key: str | None) -> bool:
    text = str(skill_key or "")
    return bool(re.fullmatch(r"skill_\d+", text) or re.search(r"_skill_\d+$", text))


def _canonical_packet_skill_id(skill_key: str | None) -> str:
    raw_skill_key = str(skill_key or "")
    number = _runtime_skill_number(raw_skill_key)
    if not number:
        return raw_skill_key
    return _canonical_num_table_skill_number(number, None) or raw_skill_key


def _canonical_hit_skill_id(skill_key: str | None, character_key: str | None) -> str:
    raw_skill_key = str(skill_key or "")
    number = _runtime_skill_number(raw_skill_key)
    if number and _canonical_num_table_skill_id(raw_skill_key, character_key) and _canonical_num_table_buff_number(number, character_key):
        return raw_skill_key
    num_table_skill = _canonical_num_table_skill_id(raw_skill_key, character_key)
    if num_table_skill:
        return num_table_skill
    return raw_skill_key


def _canonical_runtime_buff_skill_id(
    skill_key: str | None,
    character_key: str | None,
    *,
    action_id: int | None = None,
    damage_unit_index: int | None = None,
) -> str | None:
    skill_number = _runtime_skill_number(skill_key)
    canonical_buff_id = _canonical_num_table_buff_number(skill_number, character_key)
    if not canonical_buff_id:
        return None
    # Skill and Buff number spaces can overlap. An action/unit number alone
    # cannot establish the namespace of a conflicting numeric template.
    if _canonical_num_table_skill_id(skill_key, character_key):
        return None
    if action_id is not None:
        profile = _load_official_skill_damage_catalog().get(canonical_buff_id, {})
        units = (profile.get("actions") or {}).get(str(action_id), [])
        exact_units = [
            unit for unit in units
            if isinstance(unit, dict)
            and unit.get("damageAttribute") == "hp"
            and unit.get("element")
            and unit.get("index") == damage_unit_index
        ]
        if len(exact_units) == 1:
            return canonical_buff_id
    return None


@lru_cache(maxsize=1)
def _load_local_skill_semantic_entries() -> dict[str, dict[str, Any]]:
    details_path = _repo_root() / "data" / "local_semantics" / "skill" / "details.json"
    if not details_path.exists():
        return {}
    try:
        payload = json.loads(details_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    entries = payload.get("entries")
    if not isinstance(entries, dict):
        return {}
    return {
        str(skill_id): entry
        for skill_id, entry in entries.items()
        if isinstance(entry, dict)
    }


@lru_cache(maxsize=4096)
def _related_child_buff_ids(buff_id: str) -> set[str]:
    ids: set[str] = set()
    entry = _load_local_buff_semantic_entries().get(str(buff_id or ""))
    if isinstance(entry, dict):
        ids.update(str(value) for value in entry.get("createdBuffIds") or [] if str(value or ""))
        ids.update(str(value) for value in entry.get("referencedBuffIds") or [] if str(value or ""))
    hint = _buff_classifier_hint(str(buff_id or "")) or {}
    ids.update(str(value) for value in hint.get("createdBuffIds") or [] if str(value or ""))
    ids.update(str(value) for value in hint.get("referencedBuffIds") or [] if str(value or ""))
    return ids


@lru_cache(maxsize=4096)
def _skill_related_buff_ids(skill_id: str) -> set[str]:
    ids: set[str] = set()
    entry = _load_local_skill_semantic_entries().get(str(skill_id or ""))
    if isinstance(entry, dict):
        ids.update(str(value) for value in entry.get("createdBuffIds") or [] if str(value or ""))
        ids.update(str(value) for value in entry.get("referencedBuffIds") or [] if str(value or ""))
    return ids






def _derived_skill_id_from_origin_skill(origin_skill_id: str | None) -> str | None:
    origin = str(origin_skill_id or "")
    if not origin:
        return None
    if origin.endswith("_projhit"):
        return origin
    return f"{origin}_projhit"












def _apply_same_frame_trigger_skill_mappings(
    hits: list[dict[str, Any]],
    buff_starts: list[dict[str, Any]],
) -> None:
    return


def _canonical_hit_skill_id_from_cast_context(
    skill_key: str | None,
    character_key: str | None,
    ts_ms: int,
    *,
    original_template_int_id: int | None = None,
    target_enemy_key: str | None,
    recent_skill_casts_by_char: dict[str, list[dict[str, Any]]],
    recent_numeric_buffs_by_source: dict[str, list[dict[str, Any]]],
    action_id: int | None = None,
    damage_unit_index: int | None = None,
    template_type: str | None = None,
) -> str | None:
    number = _runtime_skill_number(skill_key)
    if number and template_type == "buff":
        return _canonical_num_table_buff_number(number, character_key) or str(skill_key)
    if number and template_type == "skill":
        return _canonical_num_table_skill_id(skill_key, character_key) or str(skill_key)
    # Runtime proximity/cast context is not identity evidence.
    return _canonical_runtime_buff_skill_id(
        skill_key, character_key, action_id=action_id, damage_unit_index=damage_unit_index,
    )


def _recent_source_skill_context_for_buff(
    source_key: str | None,
    ts_ms: int,
    recent_skill_casts_by_char: dict[str, list[dict[str, Any]]],
) -> dict[str, str] | None:
    return None


@lru_cache(maxsize=1)
def _load_current_buff_roots() -> dict[str, dict[str, Any]]:
    global _BUFF_ACTION_TYPES_INDEX
    _BUFF_ACTION_TYPES_INDEX = None
    path = _repo_root() / 'data/local_static/buff/typed_roots.json.gz'
    damage_path = _repo_root() / 'data/local_static/skill/damage_element_catalog.json'
    try:
        payload = json.loads(gzip.decompress(path.read_bytes()))
        damage_catalog = json.loads(damage_path.read_text(encoding='utf-8'))
    except (OSError, EOFError, ValueError):
        return {}
    if not isinstance(payload, dict) or not isinstance(damage_catalog, dict):
        return {}
    decoder = payload.get('decoder')
    if not isinstance(decoder, dict):
        return {}
    if (payload.get('schemaVersion') != 1
            or payload.get('source') != 'official_local_buffdata_memorypack'
            or decoder.get('kind') != 'typed_root_deserialization'
            or any(decoder.get(key) != value for key, value in _CURRENT_TYPED_RESOURCE_BINDING.items())
            or payload.get('decoder') != damage_catalog.get('decoder')
            or damage_catalog.get('schemaVersion') != 2
            or damage_catalog.get('source') != 'official_local_skilldata_memorypack'):
        return {}
    summary = payload.get('summary') or {}
    entries = payload.get('entries')
    if (not isinstance(entries, dict) or summary.get('completeRootCount') != len(entries)
            or summary.get('inputResourceCount') != len(entries)):
        return {}
    roots = {}
    for key, entry in entries.items():
        if not isinstance(entry, dict):
            continue
        root, proof = entry.get('root'), entry.get('proof') or {}
        size = proof.get('sourceBytes')
        if (not isinstance(root, dict) or root.get('id') != key
                or root.get('$type') != 'Beyond.Gameplay.Core.BuffData'
                or root.get('$offset') != 0
                or type(size) is not int or size <= 0
                or proof.get('consumedBytes') != size or root.get('$end') != size):
            continue
        roots[key] = root
    action_type_index = {}
    for key, root in roots.items():
        types = set()
        pending = [root]
        while pending:
            value = pending.pop()
            if isinstance(value, dict):
                type_name = str(value.get('$type') or '')
                if type_name:
                    types.add(type_name.replace('+', '.'))
                pending.extend(value.values())
            elif isinstance(value, list):
                pending.extend(value)
        action_type_index[key] = frozenset(types)
    _BUFF_ACTION_TYPES_INDEX = (roots, action_type_index)
    return roots


@lru_cache(maxsize=1)
def _load_damage_zone_config() -> dict[str, dict[str, Any]]:
    path = _repo_root() / 'data/local_static/damage_zone_config.json'
    try:
        payload = json.loads(path.read_text(encoding='utf8'))
    except (OSError, ValueError):
        return {}
    decoder = payload.get('decoder') if isinstance(payload, dict) else None
    zones = payload.get('zones') if isinstance(payload, dict) else None
    if (payload.get('schemaVersion') != 1
            or payload.get('source') != 'official_unity_typetree_damage_zone_config'
            or not isinstance(decoder, dict)
            or any(decoder.get(k) != v for k, v in _NATIVE_DAMAGE_SEMANTICS_BINDING.items())
            or not isinstance(zones, list) or not zones):
        return {}
    result = {}
    for index, zone in enumerate(zones):
        if (not isinstance(zone, dict) or type(zone.get('name')) is not str
                or type(zone.get('isMultiplyZone')) is not int
                or type(zone.get('serverIndex')) is not int):
            return {}
        result[zone['name']] = {**zone, 'clientIndex': index}
    return result


@lru_cache(maxsize=1)
def _load_local_buff_semantic_entries() -> dict[str, dict[str, Any]]:
    details_path = _repo_root() / "data" / "local_semantics" / "buff" / "details.json"
    if not details_path.exists():
        return {}
    try:
        payload = json.loads(details_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    entries = payload.get("entries")
    if not isinstance(entries, dict):
        return {}
    return {
        str(buff_id): entry
        for buff_id, entry in entries.items()
        if isinstance(entry, dict)
    }


@lru_cache(maxsize=16384)
def _buff_child_family_ids(buff_id: str) -> set[str]:
    seen: set[str] = set()
    frontier = [str(buff_id or "")]
    entries = _load_local_buff_semantic_entries()
    while frontier:
        current = frontier.pop()
        if not current or current in seen:
            continue
        seen.add(current)
        entry = entries.get(current)
        if not isinstance(entry, dict):
            continue
        for field in ("createdBuffIds", "referencedBuffIds", "binaryCreatedBuffIds", "binaryReferencedBuffIds"):
            for child in entry.get(field) or []:
                child_id = str(child or "")
                if child_id and child_id not in seen:
                    frontier.append(child_id)
    return seen


@lru_cache(maxsize=1)
def _load_equip_suit_allowed_buffs() -> dict[str, set[str]]:
    skill_entries = _load_local_skill_semantic_entries()
    equip_root = _repo_root() / "data" / "local_tables" / "equip" / "items"
    result: dict[str, set[str]] = {}
    if not equip_root.is_dir():
        return result
    for path in sorted(equip_root.glob("suit_*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        suit_id = str(payload.get("suitID") or "")
        passive_skill_id = str(payload.get("passiveSkillId") or "")
        if not suit_id or not passive_skill_id:
            continue
        entry = skill_entries.get(passive_skill_id)
        if not isinstance(entry, dict):
            continue
        root_buffs = {
            str(buff_id or "")
            for field in ("createdBuffIds", "referencedBuffIds", "binaryCreatedBuffIds", "binaryReferencedBuffIds")
            for buff_id in (entry.get(field) or [])
            if str(buff_id or "").startswith("buff_equipsuit_")
        }
        allowed: set[str] = set()
        for buff_id in root_buffs:
            allowed.update(_buff_child_family_ids(buff_id))
        if allowed:
            result[suit_id] = allowed
    return result


def _equip_buff_matches_active_suits(
    event_key: str | None,
    source_key: str | None,
    active_suits_by_char: dict[str, set[str]] | None,
) -> bool:
    buff_id = str(event_key or "")
    if not buff_id.startswith("buff_equipsuit_"):
        return True
    source = str(source_key or "")
    if not source or not isinstance(active_suits_by_char, dict):
        return True
    if not active_suits_by_char:
        return True
    if source not in active_suits_by_char:
        # Character trials and squad swaps can emit equipment buff packets
        # without a matching LOADOUT row for the replacement character.
        return True
    active_suits = set(active_suits_by_char.get(source) or set())
    if not active_suits:
        return False
    allowed_by_suit = _load_equip_suit_allowed_buffs()
    for suit_id in active_suits:
        if buff_id in allowed_by_suit.get(str(suit_id), set()):
            return True
    return False


def _weapon_buff_matches_active_weapon(
    event_key: str | None,
    source_key: str | None,
    active_weapons_by_char: dict[str, str] | None,
) -> bool:
    buff_id = str(event_key or "")
    if not _WEAPON_BUFF_RE.match(buff_id):
        return True
    source = str(source_key or "")
    if not source or not isinstance(active_weapons_by_char, dict):
        return True
    if not active_weapons_by_char:
        return True
    active_weapon = active_weapons_by_char.get(source)
    if not active_weapon:
        # Character trials and squad swaps can emit weapon buff packets before a
        # matching LOADOUT_STATS row exists for the new source character.
        return True
    hint = _packet_numeric_buff_hint(buff_id)
    weapon_id = str(hint.get("weapon_id") or "")
    if not weapon_id:
        match = _WEAPON_ID_FROM_BUFF_RE.match(buff_id)
        weapon_id = match.group(1) if match else ""
    return not weapon_id or weapon_id == active_weapon


def _buff_decoded_detail_path(buff_id: str) -> Path | None:
    entry = _load_local_buff_semantic_entries().get(str(buff_id or ""))
    if not isinstance(entry, dict):
        return None
    source = entry.get("source") if isinstance(entry.get("source"), dict) else {}
    path_text = str(source.get("decodedDetailPath") or "")
    if path_text:
        path = _repo_root() / path_text
        if path.is_file():
            return path
    fallback = _repo_root() / "data" / "akedata" / "buff" / "items" / f"{buff_id}.json"
    return fallback if fallback.is_file() else None


def _normalize_condition_damage_type(value: Any) -> str | None:
    lowered = str(value or "").strip().lower()
    if lowered in {"physical", "physic"}:
        return "physical"
    if lowered in {"fire", "pulse", "cryst", "crystal", "natural"}:
        return "cryst" if lowered == "crystal" else lowered
    return None


@lru_cache(maxsize=4096)
def _decoded_damage_modifier_condition_rows(buff_id: str) -> dict[int, dict[str, Any]]:
    payload = _load_current_buff_roots().get(buff_id)
    if not isinstance(payload, dict):
        return {}
    modifiers = payload.get("damageModifier")
    if not isinstance(modifiers, list):
        return {}
    rows = {}
    for index, modifier in enumerate(modifiers):
        conditions = []
        errors = []
        container = modifier.get("condition") if isinstance(modifier, dict) else None
        actions = container.get("actionData") if isinstance(container, dict) else None
        if not isinstance(actions, list):
            rows[index] = {"conditions": [], "errors": ["missing_condition_actions"]}
            continue
        if any(container.get(key) is not False for key in ("onlyExecuteWhenSourceIsMainChar", "onlyExecuteWhenSourceIsGuard")):
            errors.append("unresolved_source_guard")
        disabled = 0
        for action in actions:
            if not isinstance(action, dict) or not isinstance(action.get("isEnable"), bool):
                errors.append("missing_condition_enabled_state")
                continue
            if action["isEnable"] is False:
                disabled += 1
                continue
            # C# nested-type notation differs between JSON.NET's old display
            # JSON (+Data) and the fully qualified current metadata (.Data).
            symbol = str(action.get('$type') or '').split(',', 1)[0].replace('+', '.')
            action_type = {
                'Beyond.Gameplay.Core.Conditions.CheckHp.Data': 'CheckHp',
                'Beyond.Gameplay.Core.Conditions.CheckDamageType.Data': 'CheckDamageType',
                'Beyond.Gameplay.Core.Conditions.CheckDamageTypeMask.Data': 'CheckDamageTypeMask',
                'Beyond.Gameplay.Core.Conditions.CheckDamageDecorateMask.Data': 'CheckDamageDecorateMask',
                'Beyond.Gameplay.Core.CheckBuffStackNumAdvanced.Data': 'CheckBuffStackNumAdvanced',
            }.get(symbol, symbol)
            if action_type not in {"CheckHp", "CheckDamageType", "CheckDamageTypeMask", "CheckDamageDecorateMask", "CheckBuffStackNumAdvanced"}:
                errors.append("unsupported_action:" + action_type)
                continue
            if action_type in {"CheckHp", "CheckBuffStackNumAdvanced"}:
                selector = action.get("hpOwner" if action_type == "CheckHp" else "checkTarget")
                if not isinstance(selector, dict) or selector.get("targetSource") != "Target":
                    errors.append("unsupported_condition_target")
                    continue
                selection = selector.get("selectorData") or {}
                if (not isinstance(selection, dict) or selection.get("validatorData") or selection.get("postProcessorData")
                        or any(selector.get(key) for key in ("targetGroupKey", "ownerContextKey", "targetContextKey"))):
                    errors.append("unsupported_condition_selector")
                    continue
            if action_type == "CheckHp":
                value = action.get("value")
                compare = action.get("compare")
                if (compare not in {"LT", "LE", "GT", "GE", "EQ", "NE"}
                        or not isinstance(action.get("isRatio"), bool) or not isinstance(value, dict)
                        or not isinstance(value.get("useBlackboardKey"), bool)
                        or (value["useBlackboardKey"] and not value.get("blackboardKey"))):
                    errors.append("unsupported_hp_comparison")
                    continue
                conditions.append({"type": "target_hp_compare", "source": action_type,
                                   "compare": compare, "is_ratio": action["isRatio"], "value_config": dict(value)})
            elif action_type == "CheckDamageType":
                element = _normalize_condition_damage_type(action.get("damageType"))
                if element is None:
                    errors.append("unresolved_damage_type")
                    continue
                conditions.append({"type": "damage_type_in", "source": action_type, "elements": [element]})
            elif action_type == 'CheckDamageTypeMask':
                mask = action.get('damageTypeMask')
                # A defined enum value is serialized as its exact name;
                # combinations are integers. These names are version-bound
                # DamageTypeMask declarations, not inferred from a Buff ID.
                if type(mask) is str:
                    mask = {'Physical': 1, 'Real': 2, 'Fire': 4, 'Pulse': 8,
                            'Cryst': 16, 'LifeDrain': 32, 'Natural': 64, 'Ether': 128}.get(mask)
                # Exact DamageTypeMask enum bits. Representing a condition
                # does not grant an external effect whitelist approval.
                bits = {1: 'physical', 2: 'real', 4: 'fire', 8: 'pulse', 16: 'cryst',
                        32: 'lifedrain', 64: 'natural', 128: 'ether'}
                if type(mask) is not int or mask <= 0 or mask & ~sum(bits):
                    errors.append('unsupported_damage_type_mask')
                    continue
                conditions.append({'type': 'damage_type_in', 'source': action_type,
                                   'elements': [element for bit, element in bits.items() if mask & bit]})
            elif action_type == 'CheckDamageDecorateMask':
                mask, check = action.get('mask'), action.get('checkType')
                if type(mask) is not int or mask < 0 or check not in {'HasAll', 'HasAny'}:
                    errors.append('unsupported_damage_decorate_check')
                    continue
                conditions.append({'type': 'damage_decorate_mask', 'source': action_type,
                                   'mask': mask, 'check': check})
            else:
                settings = action.get("buffSettings") or {}
                value = action.get("value") or {}
                ids = settings.get("buffIdList") if isinstance(settings, dict) else None
                if (not isinstance(ids, list) or not ids or any(not isinstance(key, str) or not key for key in ids)
                        or settings.get("checkType") != "Id" or action.get("buffStackNumType") != "BuffCount"
                        or action.get("compareType") != "GE" or action.get("limitSkillCastId") is not False
                        or not isinstance(value, dict) or value.get("useBlackboardKey") is not False
                        or _coerce_optional_float(value.get("value")) != 1.0):
                    errors.append("unsupported_buff_count_comparison")
                    continue
                conditions.append({"type": "target_has_buff", "source": action_type, "buff_ids": sorted(set(ids))})
        rows[index] = {"conditions": conditions, "errors": errors, "action_count": len(actions),
                       "all_disabled": bool(actions) and disabled == len(actions)}
    return rows


def _walk_enhanced_action_child_specs(payload: Any, *, target_child_buff_id: str) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            type_name = str(node.get("$type") or "")
            if "EnhancedAction+Data" in type_name:
                child_buff = node.get("childBuffId") if isinstance(node.get("childBuffId"), dict) else {}
                child_buff_id = str(child_buff.get("value") or "")
                subtype = str(node.get("subType") or "").lower()
                element = _ENHANCED_ACTION_SUBTYPE_TO_ELEMENT.get(subtype)
                if (
                    node.get("asChildBuff")
                    and child_buff_id == target_child_buff_id
                    and element
                ):
                    rate_cfg = node.get("rate") if isinstance(node.get("rate"), dict) else {}
                    specs.append(
                        {
                            "zone": "amp",
                            "element": element,
                            "bb_key": str(rate_cfg.get("blackboardKey") or ""),
                            # Child buff instances often carry the copied rate under a plain runtime `rate` key.
                            "prefer_runtime_rate_key": "rate",
                        }
                    )
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(payload)
    return specs


@lru_cache(maxsize=4096)
def _enhanced_action_child_effect_specs(buff_id: str) -> list[dict[str, Any]]:
    target_child_buff_id = str(buff_id or "")
    if not target_child_buff_id:
        return []
    reverse_parents: set[str] = set()
    for parent_buff_id, entry in _load_local_buff_semantic_entries().items():
        if not isinstance(entry, dict):
            continue
        referenced = {
            str(value)
            for value in entry.get("referencedBuffIds") or []
            if str(value or "")
        }
        if target_child_buff_id in referenced:
            reverse_parents.add(str(parent_buff_id))
    specs: list[dict[str, Any]] = []
    for parent_buff_id in sorted(reverse_parents):
        path = _buff_decoded_detail_path(parent_buff_id)
        if path is None:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        specs.extend(_walk_enhanced_action_child_specs(payload, target_child_buff_id=target_child_buff_id))
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for spec in specs:
        key = (
            str(spec.get("zone") or ""),
            str(spec.get("element") or ""),
            str(spec.get("bb_key") or ""),
            str(spec.get("prefer_runtime_rate_key") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(spec)
    return deduped


def _semantic_entry_bb_keys(entry: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for item in entry.get("blackboard") or []:
        if not isinstance(item, dict):
            continue
        key = item.get("key")
        if isinstance(key, str) and key:
            keys.add(key)
    return keys


def _semantic_entry_has_rdps(entry: dict[str, Any]) -> bool:
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


def _char_prefix(value: Any) -> str | None:
    match = re.match(r"^(chr_\d{4})_", str(value or ""))
    return match.group(1) if match else None


_PACKET_RDPS_BB_KEY_HINTS = {
    "atk_up",
    "atk_up2",
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
    "pd_up",
    "phy_dmg_up",
    "phy_dmg_up2",
    "spell_damage_taken_up",
    "spell_dmg_up",
    "spell_taken_up",
    "spell_up",
    "start_def_decrease",
}
_PACKET_UTILITY_BB_KEY_HINTS = {
    "atb",
    "atk_duration",
    "cd",
    "comboskill_cooldown",
    "common_character_perfect_dodge",
    "count",
    "damage_interval",
    "duration",
    "dodgeSkillId",
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
    "vfx_buff_name",
}


def _packet_bb_key_has_rdps_shape(key: str) -> bool:
    lowered = key.lower()
    if lowered in _PACKET_RDPS_BB_KEY_HINTS:
        return True
    if any(token in lowered for token in ("def_decrease", "resist", "taken_up", "vulnerable", "fragile")):
        return True
    if any(token in lowered for token in ("dmg_up", "damage_up", "spell_up", "atk_up", "crit")):
        return True
    return lowered.startswith("ignore_") and ("res" in lowered or "def" in lowered)


def _is_internal_trigger_damage_record(record: dict[str, Any]) -> bool:
    raw_buff_id = _normalize_buff_id(record.get("raw_event_key") or record.get("event_key"))
    if not raw_buff_id.isdigit():
        return False
    bb_keys = {str(key).lower() for key in record.get("bb_keys") or []}
    bb_keys.update(str(key).lower() for key in (record.get("bb_values") or {}).keys())
    trigger_keys = {"phy_dmg_up", "final_phy_dmg_up", "shatter_dmg", "consumed_layer"}
    if not trigger_keys.issubset(bb_keys):
        return False
    owner = str(record.get("owner_raw") or record.get("target_character_key") or record.get("target_enemy_key") or "")
    source = str(record.get("raw_source") or record.get("source_character_key") or "")
    return owner.startswith("eny_") and source.startswith("chr_")


def _classify_packet_buff_record(record: dict[str, Any]) -> dict[str, Any]:
    raw_buff_id = _normalize_buff_id(record.get("raw_event_key") or record.get("event_key"))
    mapping = record.get("packet_mapping")
    if isinstance(mapping, dict):
        return {
            "status": "mapped",
            "class": str(mapping.get("role") or "mapped"),
            "label": "已接受映射",
            "reason": str(mapping.get("reason") or ""),
        }
    if not raw_buff_id.isdigit():
        return {
            "status": "static_id",
            "class": "static",
            "label": "静态 ID",
            "reason": "buff id is already a static string id",
        }
    owner = str(record.get("owner_raw") or record.get("target_character_key") or record.get("target_enemy_key") or "")
    source = str(record.get("raw_source") or record.get("source_character_key") or "")
    if not any(value.startswith(("chr_", "eny_")) for value in (owner, source)):
        return {
            "status": "unmapped",
            "class": "orphan_actor",
            "label": "孤立 actor",
            "reason": "numeric buff has no character/enemy owner or source context",
        }
    bb_keys = {str(key) for key in record.get("bb_keys") or []}
    bb_keys.update(str(key) for key in (record.get("bb_values") or {}).keys())
    if _is_internal_trigger_damage_record(record):
        return {
            "status": "unmapped",
            "class": "utility_or_marker",
            "label": "内部触发窗",
            "reason": "numeric buff matches common cryst-triggered helper keys and should not be treated as an external damage buff",
        }
    rdps_keys = sorted(key for key in bb_keys if _packet_bb_key_has_rdps_shape(key))
    if rdps_keys:
        return {
            "status": "unmapped",
            "class": "rdps_effect",
            "label": "疑似 rDPS 效果",
            "reason": "numeric buff has rDPS-shaped blackboard keys: " + ", ".join(rdps_keys),
        }
    if bb_keys:
        utility_keys = {key for key in bb_keys if key.lower() in _PACKET_UTILITY_BB_KEY_HINTS}
        if utility_keys == bb_keys:
            return {
                "status": "unmapped",
                "class": "utility_or_marker",
                "label": "工具/标记",
                "reason": "only utility blackboard keys were observed",
            }
        return {
            "status": "unmapped",
            "class": "unknown_blackboard",
            "label": "未知黑板",
            "reason": "blackboard keys exist but do not match accepted rDPS patterns",
        }
    return {
        "status": "unmapped",
        "class": "no_blackboard",
        "label": "无黑板",
        "reason": "numeric buff carried no blackboard values in this trace",
    }


_CLASSIFIER_ZONE_TO_INTERNAL = {
    "ATK": "atk",
    "CRIT": "crit",
    "CRIT_RATE": "crit",
    "DMG_INC": "dmg_inc",
    "AMP": "amp",
    "FRAGILE": "fragile",
    "VULN_TAKEN": "vuln_taken",
    "RES": "res",
    "COMBO": "combo",
    "UTILITY": "utility",
}


def _classifier_zone_to_internal(zone: Any) -> str | None:
    return _CLASSIFIER_ZONE_TO_INTERNAL.get(str(zone or "").strip().upper())


def _packet_record_effect_pairs(record: dict[str, Any]) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for effect in (record.get("zone_effects") or []):
        if not isinstance(effect, dict):
            continue
        zone = str(effect.get("zone") or "").strip()
        if not zone:
            continue
        pairs.add((zone, _normalize_effect_element(effect.get("element"))))
    for effect in (record.get("dynamic_effects") or []):
        if not isinstance(effect, dict):
            continue
        zone = str(effect.get("zone") or "").strip()
        if not zone:
            continue
        pairs.add((zone, _normalize_effect_element(effect.get("element"))))
    return pairs


def _classifier_hint_effect_pairs(hint: dict[str, Any]) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for effect in hint.get("resolvedEffectHints") or hint.get("effectHints") or []:
        if not isinstance(effect, dict):
            continue
        zone = _classifier_zone_to_internal(effect.get("zone"))
        if zone in (None, "utility"):
            continue
        pairs.add((zone, _normalize_effect_element(effect.get("element"))))
    return pairs


def _packet_buff_semantic_candidates(record: dict[str, Any], *, limit: int = 5) -> list[dict[str, Any]]:
    return []


@lru_cache(maxsize=1)
def _load_character_names() -> dict[str, str]:
    names = {}
    for source in ('akedata', 'local_tables'):
        manifest_path = _repo_root() / 'data' / source / 'character/manifest.json'
        try:
            manifest = json.loads(manifest_path.read_text(encoding='utf8'))
        except (OSError, ValueError):
            continue
        entries = manifest.get('entries', []) if isinstance(manifest, dict) else manifest
        if isinstance(entries, list):
            names.update({str(entry['charId']): str(entry['name']) for entry in entries
                          if isinstance(entry, dict) and entry.get('charId') and entry.get('name')})
    return names


@lru_cache(maxsize=1)
def _load_enemy_names() -> dict[str, str]:
    manifest_path = _repo_root() / "data" / "akedata" / "enemy" / "manifest.json"
    if not manifest_path.exists():
        return {}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return {
        str(entry.get("templateId")): str(entry.get("name"))
        for entry in manifest
        if entry.get("templateId") and entry.get("name")
    }


def _high_difficulty_stage_key(dungeon_id: str) -> str:
    return dungeon_id.removesuffix("_s")


def _dungeon_table_sources() -> list[tuple[Path, Path]]:
    root = _repo_root()
    return [
        (root / "data" / "akedata" / "dungeon" / "manifest.json", root / "data" / "akedata" / "dungeon" / "items"),
        (root / "data" / "local_tables" / "dungeon" / "manifest.json", root / "data" / "local_tables" / "dungeon" / "items"),
    ]


def _iter_dungeon_manifest_entries(manifest: Any) -> list[dict[str, Any]]:
    if isinstance(manifest, list):
        return [entry for entry in manifest if isinstance(entry, dict)]
    if isinstance(manifest, dict):
        entries = manifest.get("entries")
        if isinstance(entries, list):
            return [entry for entry in entries if isinstance(entry, dict)]
    return []


def _dungeon_item_path(entry: dict[str, Any], dungeon_root: Path) -> Path | None:
    content_file = str(entry.get("contentFile") or "")
    detail_path = str(entry.get("detailPath") or "")
    if detail_path:
        candidate = _repo_root() / detail_path
        if candidate.exists():
            return candidate
    if content_file:
        return dungeon_root / Path(content_file).name
    if detail_path:
        return dungeon_root / Path(detail_path).name
    return None


def _iter_dungeon_table_payloads() -> list[tuple[dict[str, Any], dict[str, Any]]]:
    payloads: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for manifest_path, dungeon_root in _dungeon_table_sources():
        if not manifest_path.exists() or not dungeon_root.exists():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for entry in _iter_dungeon_manifest_entries(manifest):
            item_path = _dungeon_item_path(entry, dungeon_root)
            if item_path is None or not item_path.exists():
                continue
            try:
                data = json.loads(item_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict):
                payloads.append((entry, data))
    return payloads


@lru_cache(maxsize=1)
def _load_dungeon_by_id() -> dict[str, tuple[str, str]]:
    mapping: dict[str, tuple[str, str]] = {}
    for entry, data in _iter_dungeon_table_payloads():
        template_id = str(entry.get("templateId") or "")
        if not template_id:
            continue
        series_key = str(data.get("dungeonSeriesId") or template_id)
        series_name = str(data.get("dungeonSeriesname") or entry.get("name") or UNKNOWN_DUNGEON_NAME)
        mapping.setdefault(series_key, (series_key, series_name))
        child_dungeons = data.get("dungeons") or data.get("childDungeons") or {}
        for dungeon_id, dungeon in child_dungeons.items():
            dungeon_key = str(dungeon_id)
            dungeon_name = str(dungeon.get("name") or series_name)
            if dungeon_key.startswith("indie_hard"):
                mapping[dungeon_key] = (dungeon_key, dungeon_name)
            elif series_name == UNKNOWN_DUNGEON_NAME and dungeon_name != UNKNOWN_DUNGEON_NAME:
                # Activity groups such as dungeon_takestwo have no official
                # series name. Preserve their official child gameId and the
                # resolved DungeonTable name instead of reporting an unknown
                # group. Ranked groups with a real series name keep their
                # existing canonical grouping behavior.
                mapping[dungeon_key] = (dungeon_key, dungeon_name)
            else:
                mapping[dungeon_key] = (series_key, series_name)
        for dungeon_id in data.get("includeDungeonIds") or []:
            dungeon_key = str(dungeon_id or "")
            if not dungeon_key:
                continue
            if dungeon_key.startswith("indie_hard"):
                mapping.setdefault(dungeon_key, (dungeon_key, series_name))
            else:
                mapping.setdefault(dungeon_key, (series_key, series_name))
    return mapping


@lru_cache(maxsize=1)
def _load_skill_name_prefixes() -> list[tuple[str, str]]:
    character_root = _repo_root() / "data" / "akedata" / "character" / "items"
    if not character_root.exists():
        return []

    prefixes: list[tuple[str, str]] = []
    for path in character_root.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for skill in data.get("skills") or []:
            skill_name = str(skill.get("name") or "")
            skill_ids = skill.get("skillIds") or []
            if not skill_name:
                continue
            for skill_id in skill_ids:
                skill_key = str(skill_id or "")
                if skill_key:
                    prefixes.append((skill_key, skill_name))
    prefixes.sort(key=lambda item: len(item[0]), reverse=True)
    return prefixes


@lru_cache(maxsize=1)
def _load_skill_exact_names() -> dict[str, str]:
    names = {
        skill_key: skill_name
        for skill_key, skill_name in _load_skill_name_prefixes()
    }
    # Display labels have their own exact, version-bound official relation.
    # They must not change skill family, damage classification or attribution.
    try:
        catalog = json.loads((_repo_root() / 'data/local_static/skill/display_name_catalog.json')
                             .read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return names
    if (not isinstance(catalog, dict) or catalog.get('schemaVersion') != 1
            or catalog.get('source') != 'official_CharGrowth_I18n_typed_skill_display'
            or not isinstance(catalog.get('decoder'), dict)
            or any(catalog['decoder'].get(key) != value
                   for key, value in _CURRENT_TYPED_RESOURCE_BINDING.items())):
        return names
    entries = catalog.get('entries')
    conflicts = catalog.get('conflicts', {})
    if not isinstance(entries, dict) or not isinstance(conflicts, dict):
        return names
    for skill_key, entry in entries.items():
        if (not isinstance(skill_key, str) or not skill_key or not isinstance(entry, dict)
                or skill_key in conflicts
                or entry.get('proofKind') not in {'official_char_growth_skill_list', 'typed_enabled_creation_path'}):
            continue
        display_name = entry.get('displayName')
        if isinstance(display_name, str) and display_name.strip():
            names[skill_key] = display_name.strip()
    # An explicit official ambiguity must not select an older cached label.
    for skill_key in conflicts:
        names[skill_key] = ''
    return names


@lru_cache(maxsize=1)
def _load_character_profiles() -> dict[str, dict[str, str]]:
    manifest_path = _repo_root() / "data" / "local_tables" / "character" / "manifest.json"
    if not manifest_path.exists():
        return {}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = manifest.get("entries") if isinstance(manifest, dict) else manifest
    return {
        str(entry.get("charId")): {
            "name": str(entry.get("name") or ""),
            "char_type": str(entry.get("charTypeId") or entry.get("charTypeName") or ""),
            "profession": str(entry.get("professionName") or entry.get("profession") or ""),
            "weapon_type": str(entry.get("weaponTypeName") or ""),
        }
        for entry in (entries or [])
        if entry.get("charId")
    }


@lru_cache(maxsize=1)
def _load_official_skill_damage_catalog() -> dict[str, dict[str, Any]]:
    path = _repo_root() / "data" / "local_static" / "skill" / "damage_element_catalog.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict) or payload.get("source") != "official_local_skilldata_memorypack":
        return {}
    decoder = payload.get('decoder')
    if not isinstance(decoder, dict):
        return {}
    if (payload.get('schemaVersion') != 2
            or decoder.get('kind') != 'typed_root_deserialization'
            or any(decoder.get(key) != value for key, value in _CURRENT_TYPED_RESOURCE_BINDING.items())
            or payload.get('decodeErrors')):
        return {}
    summary = payload.get('summary') or {}
    count = summary.get('inputResourceCount')
    if type(count) is not int or count <= 0 or summary.get('completeRootCount') != count:
        return {}
    skills = payload.get("skills")
    if not isinstance(skills, dict):
        return {}
    verified = {}
    for key, profile in skills.items():
        if not isinstance(profile, dict) or profile.get('derivedFrom'):
            continue
        proof = profile.get('proof') or {}
        if (proof.get('actionBoundaries') != 'typed_root_deserialization'
                or type(proof.get('sourceBytes')) is not int
                or proof['sourceBytes'] <= 0
                or proof.get('consumedBytes') != proof['sourceBytes']):
            continue
        actions = profile.get('actions')
        if not isinstance(actions, dict) or any(
            not isinstance(units, list) or any(
                not isinstance(unit, dict) or unit.get('source') != 'typed_memorypack_full_object'
                for unit in units
            ) for units in actions.values()
        ):
            continue
        verified[key] = profile
    return verified


@lru_cache(maxsize=1)
def _load_official_skill_damage_prefixes() -> list[tuple[str, dict[str, Any]]]:
    return sorted(_load_official_skill_damage_catalog().items(), key=lambda item: len(item[0]), reverse=True)


def _resolve_official_skill_damage_profile(skill_key: str | None) -> dict[str, Any] | None:
    if not skill_key:
        return None
    canonical = _canonical_packet_skill_id(skill_key)
    exact = _load_official_skill_damage_catalog().get(canonical)
    return exact


@lru_cache(maxsize=1)
def _load_skill_profiles() -> dict[str, dict[str, Any]]:
    character_root = _repo_root() / "data" / "akedata" / "character" / "items"
    skill_item_root = _repo_root() / "data" / "akedata" / "skill" / "items"
    if not character_root.exists():
        return {}

    profiles = _load_character_profiles()
    mapping: dict[str, dict[str, Any]] = {}
    damage_tag_re = re.compile(r"<@ba\.([a-z_]+)>")
    for path in character_root.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        char_key = str(data.get("charId") or "")
        if not char_key:
            continue
        char_profile = profiles.get(char_key, {})
        char_type = char_profile.get("char_type") or str(data.get("charType") or "")
        profession = char_profile.get("profession") or str(data.get("profession") or "")
        weapon_type = char_profile.get("weapon_type") or str(data.get("weapontype") or "")
        for skill in data.get("skills") or []:
            try:
                group_type = int(skill.get("groupType") or 0)
            except (TypeError, ValueError):
                group_type = 0
            skill_ids = [str(skill_id or "") for skill_id in (skill.get("skillIds") or []) if skill_id]
            description = str(skill.get("description") or "")
            tags = {match.group(1) for match in damage_tag_re.finditer(description)}
            elements = {
                _DAMAGE_TAG_TO_ELEMENT[tag]
                for tag in tags
                if tag in _DAMAGE_TAG_TO_ELEMENT
            }
            family_skill_key = skill_ids[0] if group_type == 3 and skill_ids else ""
            for skill_key in skill_ids:
                skill_item_spec = ""
                skill_item_path = skill_item_root / f"{skill_key}.json"
                if skill_item_path.exists():
                    try:
                        skill_item = json.loads(skill_item_path.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        skill_item = {}
                    skill_item_spec = str(skill_item.get("skillSpecification") or "")
                mapping[skill_key] = {
                    "char_key": char_key,
                    "char_type": char_type,
                    "profession": profession,
                    "weapon_type": weapon_type,
                    "group_type": group_type,
                    "skill_specification": skill_item_spec,
                    "elements": sorted(elements),
                    # combo => 连携，同一条连携的多个分段应共享展示家族
                    "family_skill_key": family_skill_key or skill_key,
                }
    return mapping


@lru_cache(maxsize=4096)
def _load_skill_item_metadata(skill_key: str) -> dict[str, Any]:
    if not skill_key:
        return {}
    path = _repo_root() / "data" / "akedata" / "skill" / "items" / f"{skill_key}.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    dynamic_action_keys: dict[int, str] = {}
    pending_nodes: list[Any] = [payload]
    while pending_nodes:
        node = pending_nodes.pop()
        if isinstance(node, dict):
            node_type = str(node.get("$type") or "").replace("+", ".")
            if ("ModifyDynamicBlackboard.Data" in node_type
                    or "SimpleCalcBBAction.Data" in node_type):
                action_index = _coerce_optional_int(node.get("serverActionIndex"))
                key = str(node.get("key") or "")
                if action_index is not None and key:
                    dynamic_action_keys[action_index] = key
            pending_nodes.extend(node.values())
        elif isinstance(node, list):
            pending_nodes.extend(node)
    return {
        "skill_specification": str(payload.get("skillSpecification") or ""),
        "cast_type": str(payload.get("castType") or ""),
        "attack_range_type": str(payload.get("attackRangeType") or ""),
        "dynamic_action_keys": dynamic_action_keys,
    }


def _damage_school_from_element(element: str | None) -> str | None:
    if element == "physical":
        return "physical"
    if element in _ELEMENTAL_ELEMENTS:
        return "spell"
    return None


def _normalize_action_damage_type(value: Any) -> str | None:
    return _ACTION_DAMAGE_TYPE_TO_ELEMENT.get(str(value or "").strip().lower())


@lru_cache(maxsize=4096)
def _load_skill_action_damage_units(skill_key: str) -> dict[int, list[dict[str, Any]]]:
    if not skill_key:
        return {}
    canonical = _canonical_packet_skill_id(skill_key)
    profile = _load_official_skill_damage_catalog().get(canonical)
    if not isinstance(profile, dict):
        return {}
    actions: dict[int, list[dict[str, Any]]] = {}
    # Diagnostic masks come only from the version-bound current Buff root.
    # They must not be promoted to observed hit masks until end-to-end native
    # propagation through damage processors is established.
    static_masks: dict[tuple[int, int], list[tuple[int, str, str]]] = {}
    static_payload = _load_current_buff_roots().get(canonical)
    if isinstance(static_payload, dict):
        pending_nodes: list[Any] = [static_payload]
        while pending_nodes:
            node = pending_nodes.pop()
            if isinstance(node, dict):
                raw_units = node.get("damageUnits")
                if isinstance(raw_units, list):
                    action_index = _coerce_optional_int(node.get("serverActionIndex"))
                    if action_index is not None:
                        for index, unit in enumerate(raw_units):
                            if not isinstance(unit, dict) or unit.get("ignoreDamageImmuneLevel") != "None":
                                continue
                            mask = _coerce_optional_int(unit.get("damageDecorateMask"))
                            if mask is not None and mask >= 0:
                                static_masks.setdefault((action_index, index), []).append((
                                    mask,
                                    str(unit.get("damageType") or "").strip().lower(),
                                    str(unit.get("damageAttributeType") or "").strip().lower(),
                                ))
                pending_nodes.extend(node.values())
            elif isinstance(node, list):
                pending_nodes.extend(node)
    for raw_action_id, raw_units in (profile.get("actions") or {}).items():
        try:
            action_id = int(raw_action_id)
        except (TypeError, ValueError):
            continue
        units = []
        for unit in raw_units:
            unit_index = unit.get("index") if isinstance(unit, dict) else None
            mask_candidates = static_masks.get((action_id, unit_index), []) if type(unit_index) is int else []
            if (isinstance(unit, dict)
                    and type(unit.get('damageDecorateMask')) is int
                    and unit.get('ignoreDamageImmuneLevel') == 'None'):
                mask_candidates = list(mask_candidates) + [(
                    int(unit['damageDecorateMask']),
                    str(unit.get('element') or '').lower(),
                    str(unit.get('damageAttribute') or '').lower(),
                )]
            mask_candidates = list(dict.fromkeys(mask_candidates))
            direct_mask = mask_candidates[0] if len(mask_candidates) == 1 else None
            mask_source = None
            if (direct_mask is not None
                    and direct_mask[1] == str(unit.get("element") or "").lower()
                    and direct_mask[2] == str(unit.get("damageAttribute") or "").lower()):
                mask_value = direct_mask[0]
                mask_source = "current_typed_skill_or_buff_exact_unit_generation_only"
            else:
                # An action/unit index mismatch has no protocol proof.
                mask_value = None
            if not isinstance(unit, dict):
                continue
            units.append({
                "index": unit.get("index"),
                "damage_type": str(unit.get("element") or ""),
                "damage_attribute_type": str(unit.get("damageAttribute") or ""),
                "element": str(unit.get("element") or ""),
                "damage_decorate_mask": mask_value,
                "damage_decorate_mask_source": mask_source,
                "damage_calculation": ({**copy.deepcopy(unit['damageCalculation']),
                    'source': 'current_typed_damage_unit',
                    'sourceProof': {**_CURRENT_TYPED_RESOURCE_BINDING,
                        'scope': 'typed_layout_and_exact_resource_only',
                        'resourceId': canonical,
                        'sourceSha256': (profile.get('proof') or {}).get('sourceSha256'),
                        'sourceAsset': (profile.get('proof') or {}).get('sourceAsset'),
                        'sourceBytes': (profile.get('proof') or {}).get('sourceBytes'),
                        'consumedBytes': (profile.get('proof') or {}).get('consumedBytes'),
                        'actionId': action_id, 'damageUnitIndex': unit.get('index'),
                        'byteOffset': unit.get('byteOffset'), 'byteEnd': unit.get('byteEnd')}}
                    if isinstance(unit.get('damageCalculation'), dict) else None),
            })
        if units:
            actions[action_id] = units
    return actions


def _infer_skill_action_damage_element(
    skill_key: str | None,
    action_id: int | str | None,
    damage_unit_index: int | str | None = None,
) -> str | None:
    if not skill_key:
        return None
    # Missing, fractional, negative or boolean indices are not unit zero.
    if any(isinstance(value, bool) or not re.fullmatch(r"\d+", str(value))
           for value in (action_id, damage_unit_index)):
        return None
    units = _load_skill_action_damage_units(str(skill_key)).get(int(action_id), [])
    matches = [unit for unit in units if type(unit.get("index")) is int
               and unit["index"] == int(damage_unit_index)]
    if len(matches) != 1 or matches[0].get("damage_attribute_type") != "hp":
        return None
    element = matches[0].get("element")
    return element if element in {"physical", "fire", "pulse", "cryst", "natural"} else None


def _resolve_skill_profile(skill_key: str | None) -> dict[str, Any] | None:
    if not skill_key:
        return None
    skill_key = _canonical_packet_skill_id(skill_key)
    mapping = _load_skill_profiles()
    return mapping.get(skill_key)


def _resolve_combo_consume_context(hit: dict[str, Any]) -> tuple[int, str] | None:
    skill_key = str(hit.get("skill_key") or "")
    alias = _COMBO_CONSUME_SKILL_ALIASES.get(skill_key)
    if alias is not None:
        return int(alias["group_type"]), str(alias["family_skill_key"])

    try:
        group_type = int(hit.get("skill_group_type"))
    except (TypeError, ValueError):
        group_type = None
    family_key = str(hit.get("skill_family_key") or skill_key)
    if group_type in {1, 2} and family_key:
        return group_type, family_key

    return None


def _combo_marginal_layer_rates(group_type: int, stack_count: int) -> list[float]:
    total_rates = _COMBO_TOTAL_RATES_BY_GROUP_TYPE.get(group_type)
    if not total_rates or stack_count <= 0:
        return []
    capped_count = min(int(stack_count), len(total_rates))
    marginal_rates: list[float] = []
    previous_total = 0.0
    for total_rate in total_rates[:capped_count]:
        marginal_rates.append(max(0.0, float(total_rate) - previous_total))
        previous_total = float(total_rate)
    return marginal_rates


def _resolve_skill_family_key(skill_key: str | None, skill_profile: dict[str, Any] | None = None) -> str:
    if not skill_key:
        return ""
    skill_key = _canonical_packet_skill_id(skill_key)
    if skill_profile is not None:
        family_skill_key = str(skill_profile.get("family_skill_key") or skill_key)
        if family_skill_key:
            return family_skill_key

    return skill_key


_CAST_CHILD_ENTITY_RE = re.compile(
    r"(?:projhit|abilityrange|indicator|blocked|_bomb|_field|_sheep)",
    re.IGNORECASE,
)


def _cast_skill_family_key(cast: dict[str, Any]) -> str:
    cast_skill = str(cast.get("skill") or "")
    if not cast_skill:
        return ""
    if str(cast.get("skill_source") or "").lower() == "summon":
        return ""
    if _CAST_CHILD_ENTITY_RE.search(cast_skill):
        return ""

    cast_character_key = str(cast.get("source_character_key") or "")
    canonical_skill = _canonical_packet_skill_id(cast_skill)
    if _is_generic_runtime_skill_id(canonical_skill) or re.fullmatch(r".*_skill_\d+", canonical_skill):
        canonical_skill = _canonical_num_table_skill_id(canonical_skill, cast_character_key) or canonical_skill

    skill_profile = _resolve_skill_profile(canonical_skill)
    family_key = _resolve_skill_family_key(canonical_skill, skill_profile)
    return family_key


def _build_cast_start_index(skill_casts: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    index: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for cast in sorted(skill_casts, key=lambda item: int(item.get("ts_ms") or 0)):
        character_key = str(cast.get("source_character_key") or "")
        family_key = _cast_skill_family_key(cast)
        if not character_key or not family_key:
            continue
        index[(character_key, family_key)].append(cast)
    return index


def _matching_cast_for_hit(
    hit: dict[str, Any],
    cast_start_index: dict[tuple[str, str], list[dict[str, Any]]],
) -> dict[str, Any] | None:
    character_key = str(hit.get("character_key") or "")
    family_key = str(hit.get("skill_family_key") or hit.get("skill_key") or "").lower()
    if not character_key or not family_key:
        return None

    hit_ts = int(hit.get("ts_ms") or 0)
    for cast in reversed(cast_start_index.get((character_key, family_key), [])):
        cast_ts = int(cast.get("ts_ms") or 0)
        delta_ms = hit_ts - cast_ts
        if delta_ms < -50:
            continue
        if delta_ms > 12000:
            break
        return cast
    return None


def _timeline_skill_group_gap_ms(hit: dict[str, Any]) -> int:
    skill_key = str(hit.get("skill_key") or "").lower()
    family_key = str(hit.get("skill_family_key") or "").lower()
    if "_ultimate_skill" in family_key or "_ultimate_skill" in skill_key or "_ult_attack" in skill_key:
        return _TIMELINE_ULTIMATE_GROUP_GAP_MS
    return _TIMELINE_SKILL_GROUP_GAP_MS


def _resolve_character_name(character_key: str | None) -> str | None:
    if not character_key:
        return None
    return _load_character_names().get(character_key, character_key)


def _resolve_enemy_name(enemy_key: str | None) -> str | None:
    if not enemy_key:
        return None
    return _load_enemy_names().get(enemy_key, enemy_key)


def _resolve_dungeon_context(dungeon_id: str | None) -> tuple[str, str] | None:
    if not dungeon_id:
        return None
    return _DUNGEON_CONTEXT_ALIASES.get(dungeon_id) or _load_dungeon_by_id().get(dungeon_id)


@lru_cache(maxsize=1)
def _load_buff_manifest_names() -> dict[str, str]:
    manifest_path = _repo_root() / "data" / "akedata" / "buff" / "manifest.json"
    if not manifest_path.exists():
        return {}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return {
        str(entry.get("id")): str(entry.get("name"))
        for entry in manifest
        if entry.get("id") and entry.get("name")
    }


def _humanize_buff_damage_name(skill_key: str) -> str | None:
    override = _BUFF_DAMAGE_NAME_OVERRIDES.get(skill_key)
    if override is not None:
        return override

    manifest_name = _load_buff_manifest_names().get(skill_key, skill_key.removeprefix("buff_"))
    normalized = manifest_name.removeprefix("buff_")

    anomaly_do_match = re.match(
        r"^common_(fire|pulse|cryst|natural)_(?:fire|pulse|cryst|natural)_(conduct_triggered_do|corrupt_do)$",
        normalized,
    )
    if anomaly_do_match:
        action = anomaly_do_match.group(2)
        if action == "conduct_triggered_do":
            return "导电"
        if action == "corrupt_do":
            return "腐蚀"

    same_element_burst_match = re.match(
        r"^common_(fire|pulse|cryst|natural|spell)_\1_triggered$",
        normalized,
    )
    if same_element_burst_match:
        element = _ELEMENT_TOKEN_TO_NAME.get(same_element_burst_match.group(1), same_element_burst_match.group(1))
        return f"{element}爆发"

    cross_element_trigger_match = re.match(
        r"^common_(fire|pulse|cryst|natural)_(fire|pulse|cryst|natural)_triggered$",
        normalized,
    )
    if cross_element_trigger_match:
        applied = cross_element_trigger_match.group(1)
        anomaly_name = {
            "fire": "燃烧",
            "pulse": "导电",
            "cryst": "冻结",
            "natural": "腐蚀",
        }.get(applied)
        if anomaly_name:
            return anomaly_name

    if normalized.startswith("common_") and normalized.endswith("_triggered"):
        parts = normalized.split("_")
        if len(parts) >= 4:
            left = _ELEMENT_TOKEN_TO_NAME.get(parts[1], parts[1])
            right = _ELEMENT_TOKEN_TO_NAME.get(parts[2], parts[2])
            return f"{left}·{right}触发"

    if normalized.startswith("enemy_spell_") and "_triggered_" in normalized:
        parts = normalized.split("_")
        if len(parts) >= 5:
            element = _ELEMENT_TOKEN_TO_NAME.get(parts[2], parts[2])
            return f"敌方法术{element}触发"

    if normalized.endswith("_airborne"):
        prefix = normalized.removesuffix("_airborne")
        prefix_name = _ELEMENT_TOKEN_TO_NAME.get(prefix, prefix)
        return f"{prefix_name}浮空伤害"

    if normalized.startswith("common_heal_moss_"):
        return "治疗苔藓"

    if normalized.startswith("chr_"):
        character_skill_name = _humanize_character_skill_name(normalized)
        if character_skill_name is not None:
            return character_skill_name

    if normalized.startswith("common_"):
        readable = normalized.removeprefix("common_").replace("_", " ")
        return readable

    return None


def _humanize_skill_suffix_text(suffix: str) -> str | None:
    text = suffix.strip("_").lower()
    if not text:
        return None
    explicit = {
        "abilityentity": "实体",
        "abilityentitymove": "实体移动",
        "abilityrange": "范围",
        "air_attack_abilityrange": "空中攻击范围",
        "air_attack_projhit": "空中攻击派生",
        "air_attack_sheep": "空中攻击 / 绵羊",
        "blocked": "格挡",
        "during_ult": "终结技期间",
        "gene_sheep": "召唤绵羊",
        "hit": "命中",
        "hit_self": "自身命中",
        "indicator": "指示器",
        "listen_combo": "连携监听",
        "projhit": "派生",
        "remain_loop_sheep": "绵羊持续伤害",
        "sheep": "绵羊派生",
        "shockwave": "冲击波",
        "start": "起手",
    }
    if text in explicit:
        return explicit[text]

    absorb_match = re.match(r"absorb(?:_(\d+))?_projhit$", text)
    if absorb_match:
        slot = absorb_match.group(1)
        return f"吸收{slot}派生" if slot else "吸收派生"

    attack_match = re.match(r"attack_?(\d+)(?:_(\d+))?_projhit(?:_blocked)?$", text)
    if attack_match:
        attack_index = attack_match.group(1)
        sub_index = attack_match.group(2)
        base = f"A{attack_index}"
        if sub_index:
            base += f"-{sub_index}"
        if text.endswith("_blocked"):
            return f"{base} 派生（格挡）"
        return f"{base} 派生"

    tokens = [token for token in text.split("_") if token]
    if not tokens:
        return None
    token_map = {
        "absorb": "吸收",
        "air": "空中",
        "airborne": "浮空",
        "attack": "攻击",
        "blocked": "格挡",
        "bleed": "流血",
        "break": "击破",
        "combo": "连携",
        "damage": "伤害",
        "drone": "无人机",
        "effect": "效果",
        "entity": "实体",
        "extra": "额外",
        "fx": "特效",
        "loop": "循环",
        "move": "移动",
        "normal": "普攻",
        "projhit": "派生",
        "range": "范围",
        "remain": "持续",
        "self": "自身",
        "sheep": "绵羊",
        "shockwave": "冲击波",
        "spawn": "召唤",
        "talent": "天赋",
    }
    pretty = [token_map.get(token, token.upper() if token.isdigit() else token) for token in tokens]
    return " / ".join(pretty)


def _humanize_character_skill_name(skill_key: str) -> str | None:
    exact_name = _load_skill_exact_names().get(skill_key)
    if exact_name:
        return exact_name

    lowered = skill_key.lower()

    if lowered.endswith("_dodge"):
        return "闪避"

    if _DASH_ATTACK_RE.match(lowered):
        return "闪避攻击 派生" if "projhit" in lowered else "闪避攻击"

    if lowered.endswith("_power_attack"):
        return "重击"

    plunging_match = _PLUNGING_ATTACK_RE.match(lowered)
    if plunging_match:
        phase = plunging_match.group(1)
        if phase == "projhit":
            return "下落攻击 派生"
        if phase == "start":
            return "下落攻击 起手"
        return "下落攻击"

    attack_match = _ATTACK_VARIANT_RE.match(lowered)
    if attack_match:
        attack_index = attack_match.group(1)
        sub_index = attack_match.group(2)
        base = f"A{attack_index}"
        if sub_index:
            base += f"-{sub_index}"
        if "projhit" in lowered and "blocked" in lowered:
            return f"{base} 派生（格挡）"
        if "projhit" in lowered:
            return f"{base} 派生"
        return base

    talent_match = _TALENT_RE.match(lowered)
    if talent_match:
        tier = talent_match.group(1)
        branch = talent_match.group(2)
        return f"天赋{tier}-{branch}" if branch else f"天赋{tier}"

    potential_match = _POTENTIAL_RE.match(lowered)
    if potential_match:
        tier = potential_match.group(1)
        branch = potential_match.group(2)
        return f"潜能{tier}-{branch}" if branch else f"潜能{tier}"

    passive_match = _PASSIVE_RE.match(lowered)
    if passive_match:
        suffix = _humanize_skill_suffix_text(passive_match.group(1) or "")
        return f"被动 / {suffix}" if suffix else "被动"
    if lowered.endswith("_passive"):
        suffix_name = _humanize_skill_suffix_text(lowered.split("_", 3)[-1].removesuffix("_passive"))
        return f"被动 / {suffix_name}" if suffix_name else "被动"

    talent_suffix_index = lowered.find("_talent_")
    if talent_suffix_index >= 0:
        suffix_name = _humanize_skill_suffix_text(lowered[talent_suffix_index + len("_talent_"):])
        return f"天赋 / {suffix_name}" if suffix_name else "天赋"

    if lowered.endswith("_tactic_skill"):
        return "战术技"

    if lowered.endswith("_extra_attack_projhit"):
        return "追加攻击 派生"
    if lowered.endswith("_extra_attack"):
        return "追加攻击"

    for base_suffix in ("_normal_skill", "_combo_skill", "_ultimate_skill"):
        if base_suffix not in lowered:
            continue
        base_index = lowered.index(base_suffix) + len(base_suffix)
        base_key = skill_key[:base_index]
        suffix = skill_key[base_index:]
        base_name = _load_skill_exact_names().get(base_key)
        suffix_name = _humanize_skill_suffix_text(suffix)
        if base_name and suffix_name:
            return f"{base_name} / {suffix_name}"
        if base_name:
            return base_name
        break

    for base_key, base_name in _load_skill_name_prefixes():
        prefix = f"{base_key}_"
        if not skill_key.startswith(prefix):
            continue
        suffix_name = _humanize_skill_suffix_text(skill_key[len(base_key):])
        if suffix_name:
            return f"{base_name} / {suffix_name}"
        break

    suffix_patterns = (
        "remain_loop",
        "air_attack",
        "absorb",
        "abilityentity",
        "airborne",
        "bleed",
        "break",
        "damage",
        "effect",
        "projhit",
        "blocked",
        "sheep",
    )
    if any(pattern in lowered for pattern in suffix_patterns):
        if "_remain_loop_" in lowered:
            suffix_name = _humanize_skill_suffix_text(lowered.split("_", 3)[-1])
            if suffix_name:
                return suffix_name
        tail = lowered.split("_", 3)[-1]
        suffix_name = _humanize_skill_suffix_text(tail)
        if suffix_name:
            return suffix_name

    return None


def _humanize_generic_skill_tokens(text: str) -> str | None:
    value = text.strip("_").lower()
    if not value:
        return None
    explicit = {
        "abilityrange": "范围",
        "activityspecial": "活动特化",
        "battleact": "战斗动作",
        "bornexit": "出场结束",
        "bornstart": "出场开始",
        "death": "死亡",
        "doodad": "装置",
        "dungeon": "副本",
        "endinggame": "终局",
        "explosion": "爆炸",
        "ex": "EX",
        "hard": "高难",
        "interact": "交互",
        "passive": "被动",
        "projhit": "派生",
        "settlement": "结算",
        "store": "存储",
    }
    if value in explicit:
        return explicit[value]

    skill_match = re.fullmatch(r"skill0?(\d+)", value)
    if skill_match:
        return f"技能{int(skill_match.group(1))}"

    dash_match = re.fullmatch(r"dash_([bflr])", value)
    if dash_match:
        direction = {"b": "后", "f": "前", "l": "左", "r": "右"}.get(dash_match.group(1), dash_match.group(1))
        return f"冲刺（{direction}）"

    tokens = [token for token in value.split("_") if token]
    token_map = {
        "abilityentity": "实体",
        "activityspecial": "活动特化",
        "anchor": "锚点",
        "attack": "攻击",
        "attackcore": "核心攻击",
        "attackplayer": "玩家攻击",
        "battleact": "战斗动作",
        "boom": "爆破",
        "bomb": "炸弹",
        "carpet": "地毯",
        "container": "容器",
        "core": "核心",
        "dash": "冲刺",
        "death": "死亡",
        "doodad": "装置",
        "endinggame": "终局",
        "entity": "实体",
        "explosion": "爆炸",
        "fire": "火焰",
        "flammable": "可燃",
        "interact": "交互",
        "matrix": "矩阵",
        "mud": "泥沼",
        "oil": "油桶",
        "passive": "被动",
        "plus": "强化",
        "projhit": "派生",
        "range": "范围",
        "settlement": "结算",
        "special": "特殊",
        "store": "存储",
        "wall": "墙",
        "wave": "波动",
        "weekraid": "周常",
    }
    pretty: list[str] = []
    for token in tokens:
        skill_match = re.fullmatch(r"skill0?(\d+)", token)
        if skill_match:
            pretty.append(f"技能{int(skill_match.group(1))}")
            continue
        dash_match = re.fullmatch(r"dash_([bflr])", token)
        if dash_match:
            direction = {"b": "后", "f": "前", "l": "左", "r": "右"}.get(dash_match.group(1), dash_match.group(1))
            pretty.append(f"冲刺（{direction}）")
            continue
        pretty.append(token_map.get(token, token.upper() if token.isdigit() else token))
    return " / ".join(pretty) if pretty else None


def _humanize_generic_skill_name(skill_key: str) -> str | None:
    lowered = skill_key.lower()
    if lowered.startswith("eny_"):
        parts = lowered.split("_", 3)
        suffix = parts[3] if len(parts) >= 4 else ""
        suffix_name = _humanize_generic_skill_tokens(suffix)
        return f"敌方 / {suffix_name}" if suffix_name else "敌方技能"
    if lowered.startswith("abilityentity_"):
        suffix_name = _humanize_generic_skill_tokens(lowered.removeprefix("abilityentity_"))
        return f"实体 / {suffix_name}" if suffix_name else "实体技能"
    if lowered.startswith("common_"):
        suffix_name = _humanize_generic_skill_tokens(lowered.removeprefix("common_"))
        return f"通用 / {suffix_name}" if suffix_name else "通用技能"
    return None


def _resolve_skill_name(skill_key: str | None) -> str | None:
    if not skill_key:
        return None
    exact_names = _load_skill_exact_names()
    if str(skill_key) in exact_names and not exact_names[str(skill_key)]:
        return None
    override = _RUNTIME_SKILL_NAME_OVERRIDES.get(str(skill_key))
    if override:
        return override
    hint = _packet_numeric_skill_hint(skill_key)
    if hint.get("display_name"):
        return str(hint["display_name"])
    exact = exact_names.get(str(skill_key))
    if exact:
        return exact
    canonical = _canonical_packet_skill_id(skill_key)
    if canonical in exact_names:
        return exact_names[canonical] or None
    return _load_canonical_skill_display_names().get(canonical)


def _element_from_text(value: str | None) -> str | None:
    if not value:
        return None
    match = _BB_KEY_ELEMENT_RE.search(value.lower())
    if not match:
        return None
    token = match.group(1)
    if token == "crystal":
        return "cryst"
    if token == "physic":
        return "physical"
    return token


def _damage_increase_element_from_bb_key(bb_key: str | None) -> str | None:
    lowered = (bb_key or "").lower()
    if lowered == "pd_up":
        return "physical"
    return _element_from_text(lowered)


def _is_damage_increase_bb_key(bb_key: str | None) -> bool:
    lowered = (bb_key or "").lower()
    return lowered in {"dmg_up", "pd_up", "spell_up"} or lowered.endswith("_dmg_up") or lowered.endswith("_dmg_up2")


def _normalize_effect_element(element: str | None) -> str:
    return element or "all"


def _effect_applies_to_damage_element(
    effect_element: str,
    damage_element: str | None,
    damage_school: str | None = None,
) -> bool:
    normalized = _normalize_effect_element(effect_element)
    if normalized == "all":
        return True
    if normalized in {"physical", "spell"}:
        if damage_school is not None:
            return normalized == damage_school
        if damage_element is None:
            return False
        if normalized == "physical":
            return damage_element == "physical"
        return damage_element in _ELEMENTAL_ELEMENTS
    if damage_element is None:
        return False
    return normalized == damage_element


def _effect_damage_type_condition_elements(effect: dict[str, Any]) -> set[str]:
    condition = effect.get("condition")
    return _condition_damage_type_elements(condition if isinstance(condition, dict) else None)


def _condition_damage_type_elements(condition: dict[str, Any] | None) -> set[str]:
    if not isinstance(condition, dict):
        return set()
    if str(condition.get("type") or "") == "all":
        elements: set[str] = set()
        for child in condition.get("conditions") or []:
            if isinstance(child, dict):
                elements.update(_condition_damage_type_elements(child))
        return elements
    if str(condition.get("type") or "") != "damage_type_in":
        return set()
    return {
        str(element)
        for element in condition.get("elements") or []
        if str(element or "")
    }


def _effect_element_applies_to_hit(
    effect: dict[str, Any],
    damage_element: str | None,
    damage_school: str | None = None,
) -> bool:
    effect_element = _normalize_effect_element(effect.get("element"))
    condition_elements = _effect_damage_type_condition_elements(effect)
    if condition_elements and (effect_element == "all" or effect_element in condition_elements):
        return True
    return _effect_applies_to_damage_element(effect_element, damage_element, damage_school)


def _element_filter_debug_fields(
    effect_element: str,
    damage_element: str | None,
    damage_school: str | None = None,
) -> dict[str, Any]:
    normalized = _normalize_effect_element(effect_element)
    reason = "damage_school_filtered" if normalized in {"physical", "spell"} else "damage_element_filtered"
    return {
        "reason": reason,
        "reason_group": "element_mismatch",
        "filter_kind": reason,
        "effect_element": normalized,
        "hit_damage_element": damage_element,
        "hit_damage_school": damage_school,
    }


def _derive_static_arts_strength_entries(row: dict[str, Any]) -> list[dict[str, Any]]:
    # Baseline points belong to exact equipment slots or exact card properties,
    # never to a matching number or an arbitrary BB field name.
    candidates: defaultdict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for attribute in row.get('equipment_baseline_attributes') or []:
        if (not isinstance(attribute, dict) or attribute.get('source_type') != 'equip_baseline'
                or attribute.get('attr_type') != 87
                or attribute.get('evidence_kind') != 'official_EquipTable_attribute_zero_or_exact'
                or not isinstance(attribute.get('source_id'), str) or not attribute['source_id']
                or any(type(attribute.get(key)) is not int or attribute[key] < 0
                       for key in ('source_slot', 'attr_index', 'enhance_level'))
                or not re.fullmatch(r'[0-9a-f]{64}', str(attribute.get('source_table_sha256') or ''))):
            continue
        value = _coerce_optional_float(attribute.get('value'))
        if value is None or value < 0:
            continue
        identity = ('equip', attribute['source_slot'], attribute['source_id'], attribute['attr_index'])
        candidates[identity].append({
            'source_type': 'equip', 'source_name': attribute['source_id'], 'source_id': attribute['source_id'],
            'source_slot': attribute['source_slot'], 'attr_index': attribute['attr_index'],
            'enhance_level': attribute['enhance_level'], 'rate': value,
            'source_table_sha256': attribute['source_table_sha256'],
            'evidence_kind': attribute['evidence_kind'],
        })
    try:
        catalog = json.loads((_repo_root() / 'data/local_static/skill/item_arts_strength_catalog.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        catalog = {}
    decoder = catalog.get('decoder') if isinstance(catalog, dict) else None
    if (not isinstance(catalog, dict) or catalog.get('schemaVersion') != 1
            or catalog.get('source') != 'current_official_item_skill_card_attribute'
            or not isinstance(decoder, dict)
            or any(decoder.get(key) != value for key, value in _NATIVE_DAMAGE_SEMANTICS_BINDING.items())):
        catalog = {}
    weapon_id = str(row.get('weapon_template') or '')
    declared_skills = (_load_weapon_catalog().get(weapon_id) or {}).get('weapon_skill_ids') or []
    for category, definitions in (('suit', catalog.get('suitEntries') or []),
                                  ('weapon', catalog.get('weaponEntries') or [])):
        for definition in definitions:
            if not isinstance(definition, dict):
                continue
            modifier = definition.get('modifier') or {}
            param = modifier.get('param') or {}
            if (modifier.get('$type') != 'Beyond.Gameplay.AttributeModifierData.AttributeModifier'
                    or modifier.get('attributeType') != 'PhysicalAndSpellInflictionEnhance'
                    or modifier.get('formulaItem') != 'BaseAddition' or modifier.get('modifyAttributeType') != 'Specific'
                    or param.get('useBlackboardKey') is not True
                    or param.get('blackboardKey') != definition.get('bbKey')
                    or not isinstance(definition.get('bbKey'), str) or not definition['bbKey']
                    or type(definition.get('attributeIndex')) is not int or definition['attributeIndex'] < 0
                    or type(definition.get('sourceBytes')) is not int or definition['sourceBytes'] <= 0
                    or definition.get('consumedBytes') != definition['sourceBytes']
                    or not re.fullmatch(r'[0-9a-f]{64}', str(definition.get('sourceSha256') or ''))):
                continue
            source_rows = []
            if category == 'weapon':
                bindings = [binding for binding in definition.get('weaponBindings') or []
                            if isinstance(binding, dict) and binding.get('weaponId') == weapon_id
                            and type(binding.get('skillSlot')) is int and 0 <= binding['skillSlot'] < len(declared_skills)
                            and declared_skills[binding['skillSlot']] == definition.get('skillId')]
                if (len(bindings) != 1 or _load_num_id_str_skill_map().get(str(definition.get('numericSkillId')))
                        != definition.get('skillId')):
                    continue
                for skill in row.get('weapon_source_skills') or row.get('weapon_refine_stats') or []:
                    if not isinstance(skill, dict):
                        continue
                    skill_id = str(skill.get('skill_id') or '')
                    canonical = _load_num_id_str_skill_map().get(skill_id) if skill_id.isdigit() else skill_id
                    if canonical == definition['skillId'] and isinstance(skill.get('bb'), dict):
                        source_rows.append((weapon_id, 'weapon_skill', weapon_id, skill['bb']))
            else:
                for effect in row.get('suit_effects') or []:
                    if (isinstance(effect, dict) and effect.get('suit_id') == definition.get('suitId')
                            and effect.get('active') is True and isinstance(effect.get('value'), dict)):
                        source_rows.append((effect['suit_id'], 'equip_suit',
                                            str(effect.get('suit_name') or effect['suit_id']), effect['value']))
            for item_id, source_type, source_name, bb in source_rows:
                value = _coerce_optional_float(bb.get(definition['bbKey']))
                if value is None or value < 0:
                    continue
                identity = (category, item_id, definition['skillId'], definition['attributeIndex'])
                candidates[identity].append({
                    'source_type': source_type, 'source_name': source_name,
                    'source_id': definition['skillId'], 'item_id': item_id,
                    'attr_index': definition['attributeIndex'], 'bb_key': definition['bbKey'], 'rate': value,
                    'source_resource_sha256': definition['sourceSha256'],
                    'evidence_kind': 'current_official_item_skill_card_attribute',
                })
    entries = []
    for identity, values in candidates.items():
        signatures = {json.dumps({key: field for key, field in value.items() if key != 'source_name'},
                                 sort_keys=True, ensure_ascii=False) for value in values}
        if len(signatures) != 1:
            row.setdefault('rdps_unresolved_static_multiplier_sources', []).append({
                'reason': 'conflicting_static_arts_strength_source', 'identity': list(identity)})
            continue
        entry = dict(values[0])  # One proven source with identical numeric/provenance observations.
        if len({value['source_name'] for value in values}) != 1:
            entry['source_name'] = str(entry.get('item_id') or entry['source_id'])
        if entry['rate'] > 0:
            entries.append({**entry, 'attr_type': 87, 'zone': 'arts_strength',
                            'zone_label': _RDPS_DEBUG_ZONE_LABELS['arts_strength'], 'element': 'all',
                            'condition_text': ''})
    return entries


def _derive_static_self_multiplier_entries(row: dict[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []

    row['rdps_unresolved_static_multiplier_sources'] = []
    if row.get('unresolved_equip_inst_ids'):
        row['rdps_unresolved_static_multiplier_sources'].append({
            'reason': 'unresolved_equipment_slot', 'equip_inst_ids': list(row['unresolved_equip_inst_ids'])})
    equip_slots = Counter(equip.get('slot') for equip in row.get('equips') or [] if isinstance(equip, dict))
    baseline_attributes: list[dict[str, Any]] = []
    allocatable_attr_types = {2, 17, 28, 32, 33, 50, 51, 52, 53, 54}
    for equip in row.get("equips") or []:
        if not isinstance(equip, dict):
            continue
        item_id, slot = str(equip.get('item_id') or ''), equip.get('slot')
        meta = _load_equip_item_catalog().get(item_id, {})
        modifiers, proof = meta.get('attribute_modifiers'), meta.get('attribute_source_sha256')
        if (type(slot) is not int or equip_slots[slot] != 1 or not isinstance(modifiers, list)
                or not isinstance(proof, str) or not re.fullmatch(r'[0-9a-f]{64}', proof)):
            row['rdps_unresolved_static_multiplier_sources'].append({'item_id': item_id, 'slot': slot,
                'reason': 'missing_or_conflicting_equipment_attribute_evidence'})
            continue
        levels = defaultdict(set)
        for level in equip.get('enhance_levels') or []:
            if (isinstance(level, dict) and type(level.get('index')) is int
                    and type(level.get('level')) is int):
                levels[level['index']].add(level['level'])
        if row.get('equipment_snapshot_complete') is True:
            for modifier in modifiers:
                if (isinstance(modifier, dict) and type(modifier.get('attrIndex')) is int
                        and not levels.get(modifier['attrIndex'])):
                    # A protobuf map omits only the entries at their default
                    # value; each absent attribute index is therefore level 0.
                    levels[modifier['attrIndex']].add(0)
        for modifier in modifiers:
            if not isinstance(modifier, dict):
                row['rdps_unresolved_static_multiplier_sources'].append({'item_id': item_id, 'reason': 'invalid_attribute_row'})
                continue
            attr_type, formula, target = modifier.get('attrType'), modifier.get('modifierType'), modifier.get('modifyAttributeType')
            if any(type(value) is not int for value in (attr_type, formula, target)):
                row['rdps_unresolved_static_multiplier_sources'].append({'item_id': item_id, 'reason': 'invalid_attribute_enum'})
                continue
            if (row.get('equipment_snapshot_complete') is True
                    and attr_type not in allocatable_attr_types
                    and attr_type not in {39, 40, 41, 42, 87}):
                index = modifier.get('attrIndex')
                raw_values = modifier.get('attrValues')
                values = [_coerce_optional_float(value) for value in raw_values] if isinstance(raw_values, list) else []
                candidates = levels.get(index, set())
                level = next(iter(candidates)) if len(candidates) == 1 else None
                value = (values[level] if level is not None and 0 <= level < len(values)
                         and all(v is not None for v in values) else None)
                if value is not None and value >= 0:
                    baseline_attributes.append({
                        'source_type': 'equip_baseline', 'source_id': item_id,
                        'source_slot': slot, 'attr_type': attr_type,
                        'attr_index': index, 'value': value, 'enhance_level': level,
                        'modifier_type': formula, 'modify_attribute_type': target,
                        'evidence_kind': 'official_EquipTable_attribute_zero_or_exact',
                        'source_table_sha256': proof,
                    })
                    stats = equip.get('stats')
                    if isinstance(stats, list) and type(index) is int and 0 <= index < len(stats):
                        stat = stats[index]
                        if isinstance(stat, dict) and stat.get('value') == 'unknown':
                            stat['value'] = value
                            stat['level'] = level
                            stat['value_source'] = 'official_EquipTable_attribute_zero_or_exact'
                    continue
            # This path handles percentage attack and additive damage bonuses.
            # Main/sub ability, flat stats and other mechanisms are separate.
            if (row.get('equipment_snapshot_complete') is True
                    and target == 0 and attr_type in {39, 40, 41, 42, 87} and formula == 5):
                index = modifier.get('attrIndex')
                raw_values = modifier.get('attrValues')
                values = [_coerce_optional_float(value) for value in raw_values] if isinstance(raw_values, list) else []
                candidates = levels.get(index, set())
                level = next(iter(candidates)) if len(candidates) == 1 else None
                value = values[level] if (level is not None and 0 <= level < len(values)
                                          and all(v is not None for v in values)) else None
                if value is not None and value >= 0:
                    baseline_attributes.append({
                        'source_type': 'equip_baseline', 'source_id': item_id,
                        'source_slot': slot, 'attr_type': attr_type,
                        'attr_index': index, 'value': value, 'enhance_level': level,
                        'evidence_kind': 'official_EquipTable_attribute_zero_or_exact',
                        'source_table_sha256': proof,
                    })
                    stats = equip.get('stats')
                    if isinstance(stats, list) and type(index) is int and 0 <= index < len(stats):
                        stat = stats[index]
                        if isinstance(stat, dict) and stat.get('value') == 'unknown':
                            stat['value'] = value
                            stat['level'] = level
                            stat['value_source'] = 'official_EquipTable_attribute_zero_or_exact'
                    continue
            if target != 0 or attr_type in {39, 40, 41, 42, 61, 87} or (attr_type == 2 and formula not in {6, 7}):
                row['rdps_unresolved_static_multiplier_sources'].append({'item_id': item_id, 'slot': slot,
                    'attr_type': attr_type, 'modifier_type': formula, 'modify_attribute_type': target,
                    'reason': 'unmodeled_static_attribute_mechanism'})
                continue
            if target != 0 or attr_type not in {2, 17, 28, 32, 33, 50, 51, 52, 53, 54}:
                continue
            if (attr_type == 2 and formula not in {6, 7}) or (attr_type != 2 and formula != 5):
                continue
            index = modifier.get('attrIndex')
            raw_values = modifier.get('attrValues')
            values = [_coerce_optional_float(value) for value in raw_values] if isinstance(raw_values, list) else []
            candidates = levels.get(index, set())
            level = next(iter(candidates)) if len(candidates) == 1 else None
            value = None
            if values and all(v is not None for v in values) and type(index) is int:
                if len(candidates) == 1 and 0 <= level < len(values):
                    value = values[level]
                elif not candidates and len(set(values)) == 1:
                    # No level is invented: every legal level has this value.
                    value = values[0]
            if value is None or value < 0:
                row['rdps_unresolved_static_multiplier_sources'].append({'item_id': item_id,
                    'slot': slot, 'attr_index': index, 'attr_type': attr_type,
                    'reason': 'missing_or_conflicting_equipment_enhancement'})
                continue
            stats = equip.get('stats')
            if isinstance(stats, list) and type(index) is int and 0 <= index < len(stats):
                stat = stats[index]
                if isinstance(stat, dict) and stat.get('value') == 'unknown':
                    stat['value'] = value
                    stat['level'] = level
                    stat['value_source'] = 'official_EquipTable_attribute_zero_or_exact'
            if attr_type == 2 and formula == 7:
                baseline_attributes.append({'source_type': 'equip_baseline', 'source_id': item_id,
                    'source_slot': slot, 'attr_type': 2, 'attr_index': index, 'value': value,
                    'modifier_type': 7, 'modify_attribute_type': target, 'enhance_level': level,
                    'evidence_kind': 'official_EquipTable_flat_attack', 'source_table_sha256': proof})
                continue
            zone, element = _ATTR_TYPE_TO_EFFECT[attr_type]
            entries.append({'source_type': 'equip', 'source_name': str(meta.get('item_name') or item_id),
                'source_id': item_id, 'source_slot': slot, 'label': f'attribute_type_{attr_type}',
                'zone': zone, 'zone_label': _RDPS_DEBUG_ZONE_LABELS[zone], 'element': element,
                'rate': value, 'attr_type': attr_type, 'modifier_type': formula, 'attr_index': index,
                'enhance_level': level, 'skill_groups': [], 'condition_text': '',
                'evidence_kind': 'official_EquipTable_attribute', 'source_table_sha256': proof})

    row['equipment_baseline_attributes'] = baseline_attributes
    weapon_skills = row.get('weapon_source_skills') or row.get('weapon_refine_stats') or []
    weapon_meta = _load_weapon_catalog().get(str(row.get('weapon_template') or ''), {})
    declared_weapon_skills = set(weapon_meta.get('weapon_skill_ids') or [])
    basic_attack_skills = {'wpn_sp_attr_atk_high', 'wpn_sp_attr_atk_mid', 'wpn_sp_attr_atk_low'}
    attack_specs = {(key, 'atk'): 'BaseMultiplier' for key in basic_attack_skills & declared_weapon_skills}
    attack_specs.update({(field['skillId'], field['bbKey']): field['formulaItem']
                         for field in weapon_meta.get('static_attack_fields') or []})
    weapon_attack_values: dict[tuple[str, str], set[float | None]] = defaultdict(set)
    for skill in weapon_skills:
        if not isinstance(skill, dict):
            continue
        canonical_skill = _canonical_num_table_skill_number(str(skill.get('skill_id') or ''), None)
        if canonical_skill is None and skill.get('skill_id') in declared_weapon_skills:
            canonical_skill = skill['skill_id']
        for (skill_id, key), formula in attack_specs.items():
            if canonical_skill == skill_id:
                weapon_attack_values[(skill_id, key)].add(_coerce_optional_float((skill.get('bb') or {}).get(key)))
    for (skill_id, key), values in weapon_attack_values.items():
        value = next(iter(values)) if len(values) == 1 else None
        if value is None or value < 0:
            row['rdps_unresolved_static_multiplier_sources'].append({
                'reason': 'missing_or_conflicting_weapon_basic_attack', 'source_id': skill_id})
            continue
        if attack_specs[(skill_id, key)] == 'BaseFinalAddition':
            baseline_attributes.append({'source_type': 'weapon_skill', 'source_id': skill_id,
                'attr_type': 2, 'modifier_type': 7, 'modify_attribute_type': 0, 'value': value,
                'bb_key': key, 'evidence_kind': 'official_WeaponSkill_card_flat_attack'})
            continue
        entries.append({'source_type': 'weapon_skill', 'source_name': str(row.get('weapon_template') or ''),
            'source_id': skill_id, 'zone': 'atk', 'zone_label': _RDPS_DEBUG_ZONE_LABELS['atk'],
            'element': 'all', 'attr_type': 2, 'modifier_type': 6, 'bb_key': key, 'rate': value,
            'evidence_kind': 'official_WeaponSkill_text_basic_atk',
            'source_table_sha256': weapon_meta.get('weapon_definition_sha256'), 'condition_text': ''})
    try:
        damage_catalog = json.loads((_repo_root() / 'data/local_static/skill/item_damage_baseline_catalog.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        damage_catalog = {}
    decoder = damage_catalog.get('decoder') if isinstance(damage_catalog, dict) else None
    if (not isinstance(damage_catalog, dict) or damage_catalog.get('schemaVersion') != 1
            or damage_catalog.get('source') != 'current_official_item_skill_card_attribute'
            or not isinstance(decoder, dict)
            or any(decoder.get(key) != value for key, value in _CURRENT_TYPED_RESOURCE_BINDING.items())):
        damage_catalog = {}
    damage_attributes = {
        'NormalAttackDamageIncrease': 17, 'UltimateSkillDamageIncrease': 28,
        'NormalSkillDamageIncrease': 32, 'ComboSkillDamageIncrease': 33,
        'PhysicalDamageIncrease': 50, 'FireDamageIncrease': 51,
        'PulseDamageIncrease': 52, 'CrystDamageIncrease': 53,
        'NaturalDamageIncrease': 54, 'EtherDamageIncrease': 55,
    }
    damage_static_keys: dict[str, set[str]] = defaultdict(set)
    reviewed_damage_suits: set[str] = set()
    for category, definitions in (('weapon', damage_catalog.get('weaponEntries') or []),
                                  ('suit', damage_catalog.get('suitEntries') or [])):
        for definition in definitions:
            if not isinstance(definition, dict):
                continue
            modifier = definition.get('modifier') or {}
            param = modifier.get('param') or {}
            attr_type = definition.get('attributeType')
            if (modifier.get('$type') != 'Beyond.Gameplay.AttributeModifierData.AttributeModifier'
                    or damage_attributes.get(modifier.get('attributeType')) != attr_type
                    or attr_type not in _ATTR_TYPE_TO_EFFECT
                    or modifier.get('modifyAttributeType') != 'Specific'
                    or param.get('useBlackboardKey') is not True
                    or param.get('blackboardKey') != definition.get('bbKey')
                    or not isinstance(definition.get('bbKey'), str) or not definition['bbKey']
                    or type(definition.get('attributeIndex')) is not int or definition['attributeIndex'] < 0
                    or type(definition.get('sourceBytes')) is not int or definition['sourceBytes'] <= 0
                    or definition.get('consumedBytes') != definition['sourceBytes']
                    or not re.fullmatch(r'[0-9a-f]{64}', str(definition.get('sourceSha256') or ''))
                    or _load_num_id_str_skill_map().get(str(definition.get('numericSkillId'))) != definition.get('skillId')):
                continue
            observations = []
            if category == 'weapon':
                declared_list = weapon_meta.get('weapon_skill_ids') or []
                bindings = [binding for binding in definition.get('weaponBindings') or []
                            if isinstance(binding, dict) and binding.get('weaponId') == row.get('weapon_template')
                            and type(binding.get('skillSlot')) is int and 0 <= binding['skillSlot'] < len(declared_list)
                            and declared_list[binding['skillSlot']] == definition['skillId']]
                if len(bindings) != 1:
                    continue
                damage_static_keys[definition['skillId']].add(definition['bbKey'])
                for skill in weapon_skills:
                    if not isinstance(skill, dict):
                        continue
                    skill_id = str(skill.get('skill_id') or '')
                    canonical = _load_num_id_str_skill_map().get(skill_id) if skill_id.isdigit() else skill_id
                    if canonical == definition['skillId'] and isinstance(skill.get('bb'), dict):
                        observations.append((str(row['weapon_template']), 'weapon_skill', skill['bb']))
            else:
                for effect in row.get('suit_effects') or []:
                    if (isinstance(effect, dict) and effect.get('suit_id') == definition.get('suitId')
                            and effect.get('active') is True and isinstance(effect.get('value'), dict)
                            and definition.get('requiredPieces') == 3 and definition.get('skillLevel') == 1):
                        observations.append((effect['suit_id'], 'equip_suit', effect['value']))
                        reviewed_damage_suits.add(effect['suit_id'])
            for item_id, source_type, bb in observations:
                value = _coerce_optional_float(bb.get(definition['bbKey']))
                if value is None or value < 0 or modifier.get('formulaItem') not in {'BaseAddition', 'Addition'}:
                    row['rdps_unresolved_static_multiplier_sources'].append({
                        'reason': ('missing_item_damage_baseline_value' if value is None or value < 0
                                   else 'unmodeled_item_damage_attribute_stage'),
                        'source_id': definition['skillId'], 'bb_key': definition['bbKey'],
                        'formula_item': modifier.get('formulaItem')})
                    continue
                zone, element = _ATTR_TYPE_TO_EFFECT[attr_type]
                entries.append({'source_type': source_type, 'source_name': item_id, 'item_id': item_id,
                    'source_id': definition['skillId'], 'attr_index': definition['attributeIndex'],
                    'zone': zone, 'zone_label': _RDPS_DEBUG_ZONE_LABELS[zone], 'element': element,
                    'attr_type': attr_type, 'modifier_type': {'BaseAddition': 5, 'Addition': 0}[modifier['formulaItem']],
                    'formula_item': modifier['formulaItem'], 'bb_key': definition['bbKey'], 'rate': value,
                    'source_resource_sha256': definition['sourceSha256'],
                    'evidence_kind': 'current_official_item_skill_card_attribute', 'condition_text': ''})

    suit_definitions = {
        'suit_expend_spell01': ({'atk_up'}, {'spell_dmg_up', 'duration', 'max_stack'}),
        'suit_atb01': ({'comboskill_cooldown'}, {'dmg_up', 'duration'}),
        'suit_heal01': ({'heal_up'}, {'dmg_taken_down', 'dmg_taken_down2', 'duration'}),
        'suit_combo_cd01': ({'comboskill_cooldown'}, {'spell_up', 'duration', 'max_stack'}),
        'suit_fire_natr01': ({'phy_spell_up'}, {'fire_dmg_up', 'nature_dmg_up', 'duration', 'duration2'}),
        'suit_usp02': ({'hp_up'}, {'dmg_up', 'duration'}),
    }
    suit_ids = {
        str(effect.get('suit_id') or '') for effect in row.get('suit_effects') or []
        if isinstance(effect, dict) and effect.get('suit_id')
    }
    for effect in row.get('suit_effects') or []:
        if not isinstance(effect, dict):
            continue
        suit_id = str(effect.get('suit_id') or '')
        values = effect.get('value')
        if suit_id not in suit_definitions:
            continue
        base_fields, trigger_fields = suit_definitions[suit_id]
        if not isinstance(values, dict) or not set(values).issubset(base_fields | trigger_fields):
            row['rdps_unresolved_static_multiplier_sources'].append({
                'reason': 'unmodeled_suit_static_semantics', 'suit_ids': [suit_id],
                'unknown_fields': sorted(set(values or {}) - base_fields - trigger_fields)
                if isinstance(values, dict) else [],
            })
            continue
        for field in base_fields:
            value = _coerce_optional_float(values.get(field))
            if value is not None and value >= 0:
                baseline_attributes.append({
                    'source_type': 'equip_suit_baseline', 'source_id': suit_id,
                    'field': field, 'value': value,
                    'evidence_kind': 'official_EquipSuit_text',
                })
    if suit_ids - set(suit_definitions) - reviewed_damage_suits:
        row['rdps_unresolved_static_multiplier_sources'].append({
            'reason': 'unmodeled_suit_static_semantics',
            'suit_ids': sorted(suit_ids - set(suit_definitions) - reviewed_damage_suits),
        })
    # Reviewed fields include card baselines and separately implemented trigger
    # parameters. Knowing a trigger's semantics does not make it a static bonus:
    # its Buff UID/lifetime still decides allocation. Scope every field to the
    # current weapon's declared skill identity, never to a shared BB name.
    basic_fields = {
        'wpn_sword_0006': {
            'wpn_attr_wisd_high': {'wisd'}, 'wpn_sp_attr_atk_high': {'atk'},
            'sk_wpn_sword_0006': {'fire_dmg_up', 'duration', 'normal_atk_up'},
        },
        'wpn_sword_0007': {
            'wpn_attr_wisd_mid': {'wisd'}, 'wpn_sp_attr_usgs_mid': {'usgs'},
            'sk_wpn_sword_0007': {'atk_up', 'phy_spell_up'},
        },
        'wpn_pistol_0012': {
            'wpn_attr_agi_mid': {'agi'}, 'wpn_sp_attr_usgs_mid': {'usgs'},
            'sk_wpn_pistol_0012': {'atk_up', 'duration', 'count', 'nature_dmg_up', 'nature_dmg_up_mult'},
        },
        'wpn_funnel_0008': {
            'wpn_attr_main_high': {'mainattr'},
            'wpn_sp_attr_phy_spell_high': {'physpell'},
            'sk_wpn_funnel_0008': {'second_attr_up', 'duration', 'lv', 'spell_damage_taken_up'},
        },
        'wpn_funnel_0013': {
            'wpn_attr_wisd_high': {'wisd'}, 'wpn_sp_attr_heal_high': {'heal'},
            'sk_wpn_funnel_0013': {'second_attr_up', 'duration', 'lv', 'spell_dmg_taken_up'},
        },
        'wpn_funnel_0017': {
            'wpn_attr_will_high': {'will'}, 'wpn_sp_attr_electrondam_high': {'electrondam'},
            'sk_wpn_funnel_0017': {'atk_up', 'duration', 'max_stack', 'pulse_dmg_up2'},
        },
        'wpn_sword_0012': {
            'wpn_attr_will_high': {'will'}, 'wpn_sp_attr_atk_high': {'atk'},
            'sk_wpn_sword_0012': {'atk_up', 'atk_up2', 'duration', 'max_stack', 'lv'},
        },
        'wpn_lance_0014': {
            'wpn_attr_will_high': {'will'}, 'wpn_sp_attr_heal_high': {'heal'},
            'sk_wpn_lance_0014': {'atk_up', 'duration', 'max_stack', 'primary_attr_up'},
        },
        'wpn_funnel_0004': {
            'wpn_attr_wisd_mid': {'wisd'}, 'wpn_sp_attr_electrondam_mid': {'electrondam'},
            'sk_wpn_funnel_0004': {'phy_spell_up', 'dmg_up', 'duration'},
        },
    }.get(str(row.get('weapon_template') or ''), {})
    weapon_trigger_keys = []
    for skill in weapon_skills:
        if not isinstance(skill, dict):
            continue
        canonical_skill = _canonical_num_table_skill_number(str(skill.get('skill_id') or ''), None)
        reviewed_fields = basic_fields.get(canonical_skill)
        if canonical_skill is None and skill.get('skill_id') in declared_weapon_skills:
            canonical_skill = skill['skill_id']
            reviewed_fields = basic_fields.get(canonical_skill)
        if canonical_skill not in declared_weapon_skills:
            reviewed_fields = None
        if (str(row.get('weapon_template') or '') == 'wpn_funnel_0008'
                and canonical_skill == 'wpn_sp_attr_phy_spell_high'
                and canonical_skill in declared_weapon_skills
                and _coerce_optional_float((skill.get('bb') or {}).get('atk')) == 0.0):
            # Current typed card modifies arts strength only; its exported zero
            # atk placeholder is inert. A nonzero/new field stays unresolved.
            reviewed_fields = set(reviewed_fields or []) | {'atk'}
        if canonical_skill in basic_attack_skills and canonical_skill in declared_weapon_skills:
            reviewed_fields = {'atk'}
        static_attack_keys = {key for skill_id, key in attack_specs if skill_id == canonical_skill}
        if static_attack_keys or damage_static_keys.get(canonical_skill):
            reviewed_fields = set(reviewed_fields or []) | static_attack_keys | damage_static_keys.get(canonical_skill, set())
        if reviewed_fields is None:
            weapon_trigger_keys.append('unreviewed_skill:' + str(skill.get('skill_id') or 'unknown'))
        for key in (skill.get('bb') or {}):
            if reviewed_fields is not None and key in reviewed_fields:
                continue
            weapon_trigger_keys.append(str(key))
    if weapon_trigger_keys:
        row['rdps_unresolved_static_multiplier_sources'].append({
            'reason': 'unmodeled_weapon_static_semantics',
            'weapon_template': row.get('weapon_template'),
            'weapon_source_skill_ids': [
                skill.get('skill_id') for skill in weapon_skills
                if isinstance(skill, dict) and skill.get('skill_id') is not None
            ],
            'trigger_blackboard_keys': sorted(set(weapon_trigger_keys)),
        })

    deduped: list[dict[str, Any]] = []
    seen: dict[tuple[Any, ...], dict[str, Any]] = {}
    conflicting_sources: set[tuple[Any, ...]] = set()
    for entry in entries:
        if entry.get('evidence_kind') == 'official_EquipTable_attribute':
            deduped.append(entry)
            continue
        # Conditional entries should be represented by live packet buff windows;
        # adding them here would double count transient weapon/suit effects.
        if entry.get("condition_text"):
            continue
        source_id = str(entry.get('source_id') or '')
        source_id = _canonical_num_table_skill_number(source_id, None) or source_id
        key = (
            str(entry.get("source_type") or ""),
            str(entry.get("item_id") or ""),
            source_id,
            str(entry.get("attr_index") or ""),
            str(entry.get('bb_key') or ''),
            str(entry.get('attr_type') or ''),
            str(entry.get('modifier_type') or ''),
            str(entry.get("element") or ""),
            ",".join(entry.get("skill_groups") or []),
        )
        if key in conflicting_sources:
            continue
        if key in seen:
            if seen[key].get('rate') != entry.get('rate'):
                prior = seen.pop(key)
                deduped = [existing for existing in deduped if existing is not prior]
                conflicting_sources.add(key)
                row['rdps_unresolved_static_multiplier_sources'].append({
                    'reason': 'conflicting_static_multiplier_source', 'source_id': source_id,
                    'bb_key': entry.get('bb_key')})
            continue
        seen[key] = entry
        deduped.append(entry)
    return deduped


@lru_cache(maxsize=None)
def _load_character_potential_rows(character_key: str) -> list[dict[str, Any]]:
    path = _repo_root() / "data" / "akedata" / "character" / "items" / f"{character_key}.json"
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [row for row in payload.get("potentials") or [] if isinstance(row, dict)]


def _arts_strength_entry(
    *,
    source_type: str,
    source_name: str,
    label: str,
    value: Any,
    note: str = "",
) -> dict[str, Any] | None:
    points = _safe_positive_rate(value)
    if points is None:
        return None
    return {
        "source_type": source_type,
        "source_name": source_name,
        "label": label,
        "zone": "arts_strength",
        "zone_label": _RDPS_DEBUG_ZONE_LABELS["arts_strength"],
        "element": "all",
        "rate": points,
        "note": note,
    }


def _is_arts_strength_damage_hit(hit: dict[str, Any]) -> bool:
    # Current native DamageDecorateMask values, matched to the version-bound
    # exact HP action/unit. Shatter is physical damage but a spell anomaly;
    # damage_element and damage_school must not be repurposed for this lane.
    mask = hit.get('damage_decorate_mask')
    source = 'observed_damage_decorate_mask'
    runtime_mask_present = mask is not None
    if not runtime_mask_present:
        mask = hit.get('static_damage_decorate_mask')
        source = hit.get('static_damage_decorate_mask_source')
        if source != 'current_typed_skill_or_buff_exact_unit_generation_only':
            mask = None
    if type(mask) is int and 0 <= mask < 2**64:
        kinds = []
        if mask & 0x4001C000:  # Crush, Airborne, KnockDown, Fracture.
            kinds.append('physical_anomaly')
        if mask & 0x0C100038:  # Four initial anomalies, Burning and Shatter.
            kinds.append('spell_anomaly')
        if mask & 0x03C00000:  # Fire/Crystal/Pulse/Natural Burst.
            kinds.append('spell_burst')
        hit['arts_strength_damage_observed'] = bool(kinds)
        hit['arts_strength_damage_kind'] = kinds[0] if len(kinds) == 1 else 'mixed_anomaly' if kinds else None
        hit['arts_strength_damage_source'] = source
        # The native formula independently multiplies spell-scaling attr49
        # and physical-scaling attr25. Several spell bits still mean one
        # factor, while an explicit physical+spell mask uses both factors.
        hit['arts_strength_damage_factor_count'] = int(bool(mask & 0x4001C000)) + int(bool(mask & 0x0FD00038))
        return bool(kinds)
    # Existing explicit mathematical fixtures can supply their classification.
    # A real but invalid mask, or a stale derived flag, is not that contract.
    explicit = (not runtime_mask_present and hit.get('arts_strength_damage_observed') is True
                and hit.get('arts_strength_damage_source') in (None, 'caller_supplied_classification'))
    hit['arts_strength_damage_observed'] = explicit
    hit['arts_strength_damage_kind'] = 'caller_classified_anomaly' if explicit else None
    hit['arts_strength_damage_source'] = 'caller_supplied_classification' if explicit else None
    hit['arts_strength_damage_factor_count'] = 1 if explicit else 0
    return explicit


def _arts_strength_damage_multiplier(points: float) -> float:
    return 1.0 + max(float(points), 0.0) / 100.0


def _arts_strength_effect_multiplier(points: float) -> float:
    normalized = max(float(points), 0.0)
    return 1.0 + (2.0 * normalized / (normalized + 300.0))


def _infer_skill_damage_element(skill_key: str | None, character_key: str | None) -> str | None:
    """Use only an exact official skill profile with one element."""
    if not skill_key:
        return None
    profile = _resolve_official_skill_damage_profile(skill_key)
    if profile is None or not profile.get("resolved"):
        return None
    elements = [str(value) for value in (profile.get("elements") or []) if value]
    return elements[0] if len(set(elements)) == 1 else None


def _infer_skill_damage_school(
    skill_key: str | None,
    character_key: str | None,
    *,
    raw_skill_key: str | None = None,
) -> str | None:
    candidate_keys = [str(skill_key or "")]
    raw_key = str(raw_skill_key or "")
    if raw_key and raw_key not in candidate_keys:
        candidate_keys.append(raw_key)

    for candidate in candidate_keys:
        if not candidate:
            continue
        canonical = _canonical_packet_skill_id(candidate)
        profile = _resolve_skill_profile(canonical)
        spec = str(profile.get("skill_specification") or "") if profile is not None else ""
        if not spec:
            item_meta = _load_skill_item_metadata(canonical)
            spec = str(item_meta.get("skill_specification") or "")
        school = _DAMAGE_SCHOOL_BY_SPEC.get(spec)
        inferred_element = _infer_skill_damage_element(canonical, character_key)
        if school:
            return school
        if inferred_element:
            return _damage_school_from_element(inferred_element)

    return None


def _parse_prefixed_timestamp_ms(line: str) -> int | None:
    match = _TIMESTAMP_RE.match(line)
    if not match:
        return None
    hours, minutes, seconds, millis = (int(part) for part in match.groups())
    return (((hours * 60) + minutes) * 60 + seconds) * 1000 + millis


def _parse_hint_timestamp_ms(text: str | None) -> int | None:
    if not text:
        return None
    parts = text.split("-")
    if len(parts) != 4:
        return None
    hours, minutes, seconds, millis = (int(part) for part in parts)
    return (((hours * 60) + minutes) * 60 + seconds) * 1000 + millis


def _extract_fields(line: str) -> dict[str, str]:
    return {
        key: value[1:-1] if value.startswith('"') and value.endswith('"') else value
        for key, value in _KV_RE.findall(line)
    }


def _parse_poise_damage_event(raw_line: str, ts_ms: int, line_no: int) -> dict[str, Any] | None:
    if not _POISE_RE.search(raw_line):
        return None
    fields = _extract_fields(raw_line)
    if str(fields.get("type") or "") != "PoiseDamage":
        return None
    source = str(fields.get("source") or "").strip()
    orig_source = str(fields.get("origSource") or "").strip()
    if source in {"", "unknown"}:
        source = orig_source
    if source in {"", "unknown"}:
        return None
    return {
        "line_no": line_no,
        "ts_ms": ts_ms,
        "type": "PoiseDamage",
        "value": _coerce_optional_float(fields.get("value")),
        "current_value": _coerce_optional_float(fields.get("cur")),
        "source": source,
        "source_int": _coerce_int(fields.get("sourceInt"), default=0) or None,
        "orig_source": orig_source if orig_source not in {"", "unknown"} else None,
        "orig_source_int": _coerce_int(fields.get("origSourceInt"), default=0) or None,
    }


def _apply_poise_damage_events_to_hits(
    hits: list[dict[str, Any]],
    poise_damage_events: list[dict[str, Any]],
) -> None:
    if not hits or not poise_damage_events:
        return

    for event in poise_damage_events:
        source = str(event.get("source") or "")
        source_int = event.get("source_int")
        orig_source_int = event.get("orig_source_int")
        event_ts_ms = int(event.get("ts_ms") or 0)
        matches = [
            hit
            for hit in hits
            if int(hit.get("ts_ms") or 0) == event_ts_ms
            and (
                source in {str(hit.get("skill_key") or ""), str(hit.get("skill_family_key") or "")}
                or (source_int is not None and source_int == hit.get("template_int_id"))
                or (orig_source_int is not None and orig_source_int == hit.get("original_template_int_id"))
            )
        ]
        if not matches:
            continue
        for hit in matches:
            hit["poise_damage"] = {
                "type": event.get("type") or "PoiseDamage",
                "value": event.get("value"),
                "current_value": event.get("current_value"),
                "source": source,
                "source_int": source_int,
                "orig_source": event.get("orig_source"),
                "orig_source_int": orig_source_int,
            }


@lru_cache(maxsize=1)
def _load_contract_tag_texts() -> dict[str, str]:
    candidates = [
        _repo_root().parent / "endfield_tables" / "Data" / "TableCfg" / "I18nTextTable_CN.json",
        _repo_root() / "data" / "local_tables" / "I18nTextTable_CN.json",
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            result: dict[str, str] = {}
            for key, value in payload.items():
                if isinstance(value, str):
                    result[str(key)] = value
                elif isinstance(value, dict):
                    text = value.get("text") or value.get("value")
                    if isinstance(text, str):
                        result[str(key)] = text
            return result
    return {}


def _contract_tag_text(value: Any, texts: dict[str, str]) -> str | None:
    if isinstance(value, dict):
        text = value.get("text")
        if isinstance(text, str) and text:
            return text
        value = value.get("id")
    if value is None:
        return None
    return texts.get(str(value))


@lru_cache(maxsize=1)
def _load_contract_tag_catalog() -> dict[int, dict[str, Any]]:
    catalog: dict[int, dict[str, Any]] = {}
    public_path = _repo_root() / "data" / "public" / "contract_tags.json"
    if public_path.exists():
        try:
            payload = json.loads(public_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = None
        tags = payload.get("tags") if isinstance(payload, dict) else payload
        entries = tags.values() if isinstance(tags, dict) else tags if isinstance(tags, list) else []
        for item in entries:
            if not isinstance(item, dict):
                continue
            tag_id = _coerce_int(str(item.get("tagId") or item.get("tag_id") or ""), default=0)
            if tag_id > 0:
                catalog[tag_id] = dict(item)

    table_candidates = [
        _repo_root().parent / "endfield_tables" / "Data" / "TableCfg" / "CcTagTable.json",
        _repo_root() / "data" / "local_tables" / "CcTagTable.json",
    ]
    for path in table_candidates:
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        entries = payload.values() if isinstance(payload, dict) else payload if isinstance(payload, list) else []
        for item in entries:
            if not isinstance(item, dict):
                continue
            tag_id = _coerce_int(str(item.get("tagId") or item.get("tag_id") or ""), default=0)
            if tag_id <= 0:
                continue
            merged = dict(catalog.get(tag_id) or {})
            merged.update(item)
            catalog[tag_id] = merged
        break

    texts = _load_contract_tag_texts()
    for tag_id, item in list(catalog.items()):
        enriched = dict(item)
        name = enriched.get("name")
        desc = enriched.get("desc") if "desc" in enriched else enriched.get("description")
        name_text = _contract_tag_text(name, texts) or _contract_tag_text(enriched.get("nameTextId"), texts)
        desc_text = _contract_tag_text(desc, texts) or _contract_tag_text(enriched.get("descTextId"), texts)
        if name_text:
            enriched["name"] = name_text
        if desc_text:
            enriched["description"] = desc_text
        catalog[tag_id] = enriched
    return catalog


def _parse_contract_tag_ids(value: str | None) -> list[int]:
    if not value:
        return []
    return [int(item) for item in re.findall(r"\d+", value)]


def _contract_tag_payload(tag_id: int) -> dict[str, Any]:
    catalog_entry = _load_contract_tag_catalog().get(tag_id, {})
    tag_terms = catalog_entry.get("tagTerms") if isinstance(catalog_entry.get("tagTerms"), list) else []
    values: dict[str, Any] = {}
    for term in tag_terms:
        if not isinstance(term, dict):
            continue
        for row in term.get("blackboard") or []:
            if isinstance(row, dict) and row.get("key") is not None:
                values[str(row.get("key"))] = row.get("value")
    icon_id = catalog_entry.get("iconId") or catalog_entry.get("icon")
    payload: dict[str, Any] = {
        "tag_id": tag_id,
        "score": _coerce_int(str(catalog_entry.get("score") or ""), default=0),
    }
    if catalog_entry.get("name"):
        payload["name"] = catalog_entry.get("name")
    if catalog_entry.get("description"):
        payload["description"] = catalog_entry.get("description")
    if icon_id:
        payload["icon"] = icon_id
    icon_url = catalog_entry.get("iconUrl") or catalog_entry.get("icon_url")
    if icon_url:
        payload["icon_url"] = icon_url
    if catalog_entry.get("buffId"):
        payload["buff_id"] = catalog_entry.get("buffId")
    if catalog_entry.get("groupId") is not None:
        payload["group_id"] = catalog_entry.get("groupId")
    if catalog_entry.get("conflictId") is not None:
        payload["conflict_id"] = catalog_entry.get("conflictId")
    if tag_terms:
        payload["terms"] = tag_terms
    if values:
        payload["values"] = values
    return payload


def _short_actor_number(value: str | None) -> str | None:
    if not value:
        return None
    match = _SHORT_ID_RE.match(value)
    return match.group(1) if match else None


@lru_cache(maxsize=1)


def _parse_loadout_skill_ids(raw: str) -> set[str]:
    match = re.search(r"skillIntIds=\[([^\]]*)\]", raw)
    if not match:
        return set()
    return set(re.findall(r"\d+", match.group(1)))
















def _apply_short_actor_aliases(text: str) -> str:
    return text


def _extract_character_key(*values: str | None) -> str | None:
    for value in values:
        if not value:
            continue
        match = _CHAR_KEY_RE.search(value)
        if match:
            return match.group(1)
    return None


def _extract_enemy_key(*values: str | None) -> str | None:
    for value in values:
        if not value:
            continue
        full_match = _FULL_ENEMY_KEY_RE.match(value)
        if full_match:
            return full_match.group(1)
        match = _ENEMY_KEY_RE.search(value)
        if match:
            return match.group(1)
    return None


def _collect_party_actor_ids(raw_line: str) -> set[str]:
    actor_ids: set[str] = set()
    if " SQUAD " in raw_line:
        actor_ids.update(match.group(2) for match in _SQUAD_MEMBER_RE.finditer(raw_line))
    if " ENTITY_STATS " in raw_line:
        fields = _extract_fields(raw_line)
        if fields.get("kind") == "character" and _extract_character_key(fields.get("template")):
            actor_id = fields.get("id")
            if actor_id:
                actor_ids.add(actor_id)
    return actor_ids


def _parse_entity_stats_snapshot(raw_line: str) -> tuple[str, dict[str, Any]] | None:
    if " ENTITY_STATS " not in raw_line:
        return None
    fields = _extract_fields(raw_line)
    entity_id = str(fields.get("id") or "")
    if not re.fullmatch(r"[1-9]\d*", entity_id):
        return None
    attrs: dict[int, float] = {}
    basic_attrs: dict[int, float] = {}
    seen_attr_types: set[int] = set()
    invalid_attr_types: set[int] = set()
    invalid_attr_tokens: list[str] = []
    attrs_match = re.search(r"\battrs=\[(?P<attrs>[^\]]*)\]", raw_line)
    attrs_blob = attrs_match.group("attrs") if attrs_match else ""
    for token in attrs_blob.split():
        attr_type_text, separator, raw_values = token.partition(":")
        if not separator:
            invalid_attr_tokens.append(token)
            continue
        attr_type = _coerce_optional_int(attr_type_text)
        raw_value, basic_separator, raw_basic = raw_values.partition('/')
        value = _coerce_optional_float(raw_value)
        if attr_type is None:
            invalid_attr_tokens.append(token)
            continue
        if attr_type in seen_attr_types or value is None:
            invalid_attr_types.add(attr_type)
            attrs.pop(attr_type, None)
            basic_attrs.pop(attr_type, None)
        elif attr_type not in invalid_attr_types:
            attrs[attr_type] = value
            basic_value = _coerce_optional_float(raw_basic) if basic_separator else None
            if basic_value is not None:
                basic_attrs[attr_type] = basic_value
        seen_attr_types.add(attr_type)
    return entity_id, {
        "template": fields.get("template"),
        "kind": fields.get("kind"),
        "level": _coerce_optional_int(fields.get("level")),
        "attrs": attrs,
        "basic_attrs": basic_attrs,
        "ts_ms": _parse_prefixed_timestamp_ms(raw_line),
        "invalid_attr_types": sorted(invalid_attr_types),
        "invalid_attr_tokens": invalid_attr_tokens,
        "attribute_list_present": attrs_match is not None,
    }


def _entity_stats_snapshot_at(
    timelines: dict[str, list[tuple[int, dict[str, Any]]]],
    entity_id: str | None,
    ts_ms: int,
) -> dict[str, Any] | None:
    rows = timelines.get(str(entity_id or ""))
    if not rows:
        return None
    chosen: dict[str, Any] | None = None
    chosen_ts: int | None = None
    for row_ts, row in rows:
        if row_ts <= ts_ms and (chosen_ts is None or row_ts >= chosen_ts):
            chosen, chosen_ts = row, row_ts
    return chosen


def _attach_target_entity_stats(hit: dict[str, Any], snapshot: dict[str, Any] | None) -> None:
    if not isinstance(snapshot, dict):
        return
    if not hit.get("target_enemy_key") or snapshot.get("template") != hit.get("target_enemy_key"):
        hit["rdps_unresolved_target_binding"] = True
        return
    attrs = snapshot.get("attrs") if isinstance(snapshot.get("attrs"), dict) else {}
    hit["target_stats_snapshot_ts_ms"] = snapshot.get("ts_ms")
    hit["target_stats_snapshot_line_no"] = snapshot.get("line_no")
    hit["target_stats_invalid_attr_types"] = list(snapshot.get("invalid_attr_types") or [])
    defense = attrs.get(3)
    if defense is not None:
        hit["target_defense"] = defense
        hit["target_defense_source"] = "ENTITY_STATS.attr3"
    resistance_scalar_attrs = {
        # Native BattleFormula._GetDamageTypeResistanceValue multiplies the
        # resistance-point factor by DamageTakenScalar (enum 4-7, 48, 60).
        "physical": 4,
        "fire": 5,
        "pulse": 6,
        "cryst": 7,
        "natural": 48,
        "ether": 60,
    }
    resistance_point_attrs = {
        "physical": 94,
        "natural": 95,
        "cryst": 96,
        "pulse": 97,
        "fire": 98,
        "ether": 99,
    }
    scalars = {element: attrs[attr_type] for element, attr_type in resistance_scalar_attrs.items() if attrs.get(attr_type) is not None}
    points = {element: attrs[attr_type] for element, attr_type in resistance_point_attrs.items() if attrs.get(attr_type) is not None}
    if scalars:
        hit["target_resistance_scalars"] = scalars
        hit["target_resistance_source"] = "ENTITY_STATS.attr4_7_48_60"
    if points:
        hit["target_resistance_points"] = points
        hit["target_resistance_points_source"] = "ENTITY_STATS.attr94_99"


def _attach_attacker_entity_stats(hit: dict[str, Any], snapshot: dict[str, Any] | None) -> None:
    if not isinstance(snapshot, dict):
        return
    if snapshot.get("template") != hit.get("character_key") or snapshot.get("kind") != "character":
        hit["rdps_unresolved_attacker_binding"] = True
        return
    attrs = snapshot.get("attrs") if isinstance(snapshot.get("attrs"), dict) else {}
    if not attrs:
        return
    hit["attacker_stats_attrs"] = dict(attrs)
    hit['attacker_stats_basic_attrs'] = dict(snapshot.get('basic_attrs') or {})
    hit['attacker_stats_level'] = snapshot.get('level')
    hit["attacker_stats_source"] = "ENTITY_STATS"
    hit["attacker_stats_snapshot_ts_ms"] = snapshot.get("ts_ms")
    hit["attacker_stats_snapshot_line_no"] = snapshot.get("line_no")
    hit["attacker_stats_observed_attr_count"] = len(attrs)
    hit["attacker_stats_invalid_attr_types"] = list(snapshot.get("invalid_attr_types") or [])


def _recover_enemy_target_from_mislabeled_actor(
    fields: dict[str, str],
    *,
    context_enemy_hint: str | None,
    party_actor_ids: set[str],
) -> str | None:
    return None


_ENEMY_DEFENSE_EFFECT_BB_KEYS = {
    "additional_def_decrease",
    "all_resistance_decrease",
    "def_decrease",
    "def_decrease_tick",
    "def_decrease_tick_final",
    "final_spell_resistance_decrease",
    "max_def_decrease",
    "physical_res_down",
    "physical_vulnerable_dmg_increase",
    "phy_resist_down",
    "spell_damage_taken_up",
    "spell_resistance_decrease",
    "spell_taken_up",
    "start_def_decrease",
}

_PHYSICAL_DAMAGE_ELEMENT_BB_KEYS = {
    "physical_res_down",
    "physical_vulnerable_dmg_increase",
    "phy_resist_down",
}

_FINAL_BB_KEY_OVERRIDES = {
    "spell_resistance_decrease": "final_spell_resistance_decrease",
}


def _bb_key_damage_type_condition(bb_key: str | None) -> dict[str, Any] | None:
    if str(bb_key or "").lower() not in _PHYSICAL_DAMAGE_ELEMENT_BB_KEYS:
        return None
    return {
        "type": "damage_type_in",
        "source": "blackboard_key",
        "elements": ["physical"],
    }


def _attach_bb_key_damage_type_condition(effect: dict[str, Any], bb_key: str | None) -> None:
    if effect.get("condition"):
        return
    condition = _bb_key_damage_type_condition(bb_key)
    if condition is not None:
        effect["condition"] = condition


def _record_has_enemy_defense_effect(record: dict[str, Any], bb_values: dict[str, Any]) -> bool:
    bb_keys = {str(key).lower() for key in bb_values}
    if bb_keys & _ENEMY_DEFENSE_EFFECT_BB_KEYS:
        return True

    event_key = _normalize_buff_id(record.get("event_key")).lower()
    if "conduct_triggered" in event_key:
        return True
    if "corrupt_triggered" in event_key or "corrupt_do" in event_key:
        return True
    if re.match(r"^buff_common_(?:try_)?natural_(?:fire|pulse|cryst|natural)_triggered(?:_wrapper)?$", event_key):
        return True
    return False


def _bb_duration_ms_for_enemy_defense_effect(bb_values: dict[str, Any]) -> int | None:
    for key in ("duration", "corrupt_duration", "duration_corrupt", "duration_corrupt_final"):
        duration = _safe_positive_rate(bb_values.get(key))
        if duration is not None:
            return int(duration * 1000)
    return None


def _apply_enemy_status_bb_overrides(
    record: dict[str, Any],
    *,
    context_enemy_hint: str | None,
) -> None:
    root = _load_current_buff_roots().get(_normalize_buff_id(record.get('event_key')))
    if not isinstance(root, dict) or root.get('lifeType') not in {'Limited', 'Infinity'}:
        return
    record['lifetime_mode'] = root['lifeType']
    record['lifetime_source'] = 'typed_current_BuffData'
    if record.get("raw_duration_ms") is not None:
        return
    # Infinity ignores the duration configuration. It remains an open interval
    # until a concrete finish event; battle-end clipping is only for display.
    if root['lifeType'] == 'Infinity':
        return
    duration = root.get('duration')
    if not isinstance(duration, dict):
        return
    use_key = duration.get('useBlackboardKey')
    key = str(duration.get("blackboardKey") or "")
    values = record.get("bb_values") or {}
    seconds = (_coerce_optional_float(values.get(key)) if use_key is True and key else
               _coerce_optional_float(duration.get('value')) if use_key is False else None)
    if seconds is None or seconds < 0:
        return
    record["raw_duration_ms"] = int(round(seconds * 1000))
    record["raw_duration_source"] = "official_duration_blackboard" if use_key is True else "official_duration_constant"


def _coerce_int(value: str | None, default: int = 0) -> int:
    parsed = _coerce_optional_int(value)
    return default if parsed is None else parsed


def _cap_enemy_overkill_damage(
    hit_value: int,
    *,
    enemy_hp_after: int | None,
    previous_enemy_hp: int | None,
) -> int:
    if hit_value <= 0 or enemy_hp_after is None or previous_enemy_hp is None:
        return hit_value
    if previous_enemy_hp <= 0 and enemy_hp_after <= 0:
        return 0
    if enemy_hp_after <= 0 and previous_enemy_hp > 0:
        return min(hit_value, previous_enemy_hp)
    return hit_value


def _enemy_hp_state_key(target_enemy_key: Any, target_actor_id: Any = None) -> str:
    """Use the packet entity identity when present; template keys are only a fallback."""
    actor_id = str(target_actor_id or "")
    if re.fullmatch(r"[1-9]\d*", actor_id):
        return f"actor:{actor_id}"
    return f"template:{str(target_enemy_key or '')}"


def _coerce_optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if isfinite(value) and value.is_integer() and abs(value) <= 2**53 else None
    if not isinstance(value, str):
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        exact = Decimal(value)
        return int(exact) if exact.is_finite() and exact == exact.to_integral_value() else None
    except (ValueError, InvalidOperation, OverflowError):
        return None


def _annotate_hit_enemy_hp_state(hits: list[dict[str, Any]]) -> None:
    previous_enemy_hp_by_target: dict[str, int] = {}
    observed_max_hp_by_target: dict[str, int] = {}

    for hit in hits:
        target_enemy_key = str(hit.get("target_enemy_key") or "")
        hp_state_key = _enemy_hp_state_key(target_enemy_key, hit.get("target_actor_id"))
        enemy_hp_after = _coerce_optional_int(hit.get("enemy_hp_after"))
        if not target_enemy_key or enemy_hp_after is None:
            continue

        previous_enemy_hp = previous_enemy_hp_by_target.get(hp_state_key)
        hit_value = max(0, _coerce_optional_int(hit.get("hit_value")) or 0)
        if previous_enemy_hp is not None and previous_enemy_hp >= enemy_hp_after:
            enemy_hp_before = previous_enemy_hp
            source = "previous_enemy_hp"
        else:
            enemy_hp_before = enemy_hp_after + hit_value
            source = "enemy_hp_after_plus_hit"

        hit["enemy_hp_before"] = enemy_hp_before
        hit["enemy_hp_before_source"] = source
        observed_max_hp_by_target[hp_state_key] = max(
            observed_max_hp_by_target.get(hp_state_key, 0),
            enemy_hp_before,
            enemy_hp_after,
        )
        previous_enemy_hp_by_target[hp_state_key] = enemy_hp_after

    for hit in hits:
        target_enemy_key = str(hit.get("target_enemy_key") or "")
        hp_state_key = _enemy_hp_state_key(target_enemy_key, hit.get("target_actor_id"))
        observed_max_hp = observed_max_hp_by_target.get(hp_state_key)
        enemy_hp_before = _coerce_optional_int(hit.get("enemy_hp_before"))
        if observed_max_hp is None or observed_max_hp <= 0:
            continue
        hit["target_enemy_observed_max_hp"] = observed_max_hp
        # The highest observed HP may be an already-damaged enemy, so it is
        # not the maximum HP required by CheckHp. Only an explicit maximum
        # from the packet can establish a percentage condition.
        explicit_max_hp = _coerce_optional_int(hit.get("target_enemy_max_hp"))
        if enemy_hp_before is not None and explicit_max_hp is not None and explicit_max_hp > 0:
            hit["target_enemy_hp_ratio_before"] = enemy_hp_before / explicit_max_hp


def _new_participant_totals() -> defaultdict[str, dict[str, Any]]:
    return defaultdict(
        lambda: {
            "character_key": None,
            "character_name": None,
            "total_damage": 0,
            "max_hit": 0,
            "crit_hits": 0,
            "hit_count": 0,
        }
    )


def _recompute_participant_visual_max_hits(
    hits: list[dict[str, Any]],
    participant_totals: defaultdict[str, dict[str, Any]],
) -> None:
    grouped_max: dict[str, int] = defaultdict(int)
    last_group: dict[tuple[str, str, str], dict[str, int]] = {}

    ordered_hits = sorted(
        hits,
        key=lambda item: (
            int(item.get("ts_ms") or 0),
            str(item.get("character_key") or ""),
            str(item.get("skill_key") or ""),
        ),
    )
    for hit in ordered_hits:
        character_key = str(hit.get("character_key") or "")
        if not character_key:
            continue
        target_key = str(hit.get("target_enemy_key") or "")
        skill_key = str(hit.get("skill_key") or "")
        ts_ms = int(hit.get("ts_ms") or 0)
        hit_value = int(hit.get("hit_value") or 0)
        group_key = (character_key, target_key, skill_key)
        current = last_group.get(group_key)
        if current is None or ts_ms - current["last_ts_ms"] > _VISUAL_HIT_GROUP_WINDOW_MS:
            current = {"sum": hit_value, "last_ts_ms": ts_ms}
            last_group[group_key] = current
        else:
            current["sum"] += hit_value
            current["last_ts_ms"] = ts_ms
        grouped_max[character_key] = max(grouped_max[character_key], current["sum"])

    for character_key, participant in participant_totals.items():
        participant["max_hit"] = max(int(participant.get("max_hit") or 0), int(grouped_max.get(character_key) or 0))


def _new_skill_totals() -> defaultdict[tuple[str, str], dict[str, Any]]:
    return defaultdict(
        lambda: {
            "character_key": None,
            "character_name": None,
            "skill_key": None,
            "skill_name": None,
            "total_damage": 0,
            "max_damage": 0,
            "hit_count": 0,
            "cast_count": 0,
        }
    )


def _rebuild_hit_totals(
    hits: list[dict[str, Any]],
    buff_starts: list[dict[str, Any]],
) -> tuple[
    defaultdict[str, dict[str, Any]],
    defaultdict[tuple[str, str], dict[str, Any]],
    list[str],
    set[str],
    Counter[str],
]:
    participant_totals = _new_participant_totals()
    skill_totals = _new_skill_totals()
    roster_seen: set[str] = set()
    roster_order: list[str] = []
    enemy_counter: Counter[str] = Counter()

    for hit in hits:
        character_key = str(hit.get("character_key") or "")
        if not character_key:
            continue
        if character_key not in roster_seen:
            roster_seen.add(character_key)
            roster_order.append(character_key)
        enemy_key = str(hit.get("target_enemy_key") or "")
        if enemy_key:
            enemy_counter[enemy_key] += 1

        hit_value = int(hit.get("hit_value") or 0)
        crit_flag = int(hit.get("crit_flag") or 0)
        participant = participant_totals[character_key]
        participant["character_key"] = character_key
        participant["character_name"] = hit.get("character_name") or _resolve_character_name(character_key) or character_key
        participant["total_damage"] += hit_value
        participant["max_hit"] = max(participant["max_hit"], hit_value)
        participant["crit_hits"] += 1 if crit_flag else 0
        participant["hit_count"] += 1

        skill_key = str(hit.get("skill_key") or "unknown_skill")
        skill_entry = skill_totals[(character_key, skill_key)]
        skill_entry["character_key"] = character_key
        skill_entry["character_name"] = participant["character_name"]
        skill_entry["skill_key"] = skill_key
        skill_entry["skill_name"] = hit.get("skill_name") or _resolve_skill_name(skill_key) or skill_key
        skill_entry["total_damage"] += hit_value
        skill_entry["max_damage"] = max(skill_entry["max_damage"], hit_value)
        skill_entry["hit_count"] += 1
        if int(hit.get("hit_index") or 1) == 1:
            skill_entry["cast_count"] += 1

    for buff_record in buff_starts:
        for character_key in (buff_record.get("source_character_key"), buff_record.get("target_character_key")):
            if not character_key:
                continue
            character_key = str(character_key)
            if character_key not in roster_seen:
                roster_seen.add(character_key)
                roster_order.append(character_key)
            participant = participant_totals[character_key]
            participant["character_key"] = character_key
            participant["character_name"] = _resolve_character_name(character_key) or character_key

    _recompute_participant_visual_max_hits(hits, participant_totals)

    return participant_totals, skill_totals, roster_order, roster_seen, enemy_counter


def _retarget_unknown_enemy_hits(
    hits: list[dict[str, Any]],
    buff_starts: list[dict[str, Any]],
    enemy_key: str | None,
) -> bool:
    return False


def _filter_buff_starts_for_timer_window(
    buff_starts: list[dict[str, Any]],
    *,
    battle_start_ms: int,
) -> list[dict[str, Any]]:
    def overlaps_start(buff_record: dict[str, Any]) -> bool:
        start_ts_ms = int(buff_record.get("ts_ms") or 0)
        if start_ts_ms >= battle_start_ms:
            return True
        end_ts_ms = buff_record.get("end_ts_ms")
        if end_ts_ms is not None:
            return int(end_ts_ms) >= battle_start_ms
        raw_duration_ms = buff_record.get("raw_duration_ms")
        if raw_duration_ms is not None and int(raw_duration_ms or 0) > 0:
            return start_ts_ms + int(raw_duration_ms) >= battle_start_ms
        packet_seen_ts_ms = buff_record.get("packet_modifier_last_seen_ts_ms")
        if packet_seen_ts_ms is not None:
            return int(packet_seen_ts_ms) >= battle_start_ms
        return False

    return [
        buff_record
        for buff_record in buff_starts
        if not (
            buff_record.get("target_enemy_key")
            and int(buff_record.get("ts_ms") or 0) < battle_start_ms
            and not overlaps_start(buff_record)
        )
    ]


def _coerce_float(value: str | None, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _coerce_optional_float(value: str | None) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _parse_zone_values(blob: str | None) -> list[float | None]:
    if blob is None or not blob.strip():
        return []
    # Array position is the client-side zone identity. Preserve a missing
    # value in place; dropping it would relabel every subsequent zone.
    return [_coerce_optional_float(part.strip()) for part in blob.split(',')]


def _parse_dpd_raw_line(line: str) -> dict[str, Any] | None:
    match = _DPD_RAW_RE.search(line)
    if not match:
        return None
    return {
        "seq": int(match.group("seq")),
        "calc": _coerce_optional_float(match.group("calc")),
        "atk_scale": _coerce_optional_float(match.group("atk_scale")),
        "blocked": _coerce_optional_int(match.group("blocked")),
        "damage_type_raw": match.group("damage_type"),
        "decorate_mask_raw": match.group("decorate_mask"),
        "collider": match.group("collider"),
        "atk_zones": _parse_zone_values(match.group("atk_zones")),
        "def_zones": _parse_zone_values(match.group("def_zones")),
    }


def _damage_element_from_dpd_raw(dpd_raw: dict[str, Any] | None) -> str | None:
    return None


def _parse_baseline_line(line: str) -> tuple[int, dict[int, float]] | None:
    match = _BASELINE_RE.search(line)
    if not match:
        return None
    return (
        int(match.group("seq")),
        {
            int(key): _coerce_float(value)
            for key, value in _BASELINE_KV_RE.findall(match.group("body"))
        },
    )


def _parse_packet_modifier_line(line: str) -> tuple[int, list[str], list[str]] | None:
    match = _PKT_MOD_RE.search(line)
    if not match:
        return None
    attacker = [item for item in match.group("atk").split() if item]
    defender = [item for item in match.group("def").split() if item]
    return int(match.group("seq")), attacker, defender


def _parse_packet_attr_line(line: str) -> tuple[int, list[str], list[str]] | None:
    match = _PKT_ATTR_RE.search(line)
    if not match:
        return None
    attacker = [item for item in match.group("atk").split() if item]
    defender = [item for item in match.group("def").split() if item]
    return int(match.group("seq")), attacker, defender


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


def _dpd_bucket_value_for_zone(zone: str, dpd_raw: dict[str, Any] | None) -> float | None:
    if not dpd_raw:
        return None
    bucket = _DPD_ZONE_BUCKETS.get(zone)
    if bucket is None:
        return None
    side, index = bucket
    values = dpd_raw.get("atk_zones") if side == "atk" else dpd_raw.get("def_zones")
    if not values or index >= len(values):
        return None
    try:
        return float(values[index])
    except (TypeError, ValueError):
        return None


def _round4(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return None


def _sort_rdps_debug_zones(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        items,
        key=lambda item: (
            _RDPS_DEBUG_ZONE_ORDER.get(str(item.get("zone") or ""), 99),
            str(item.get("zone") or ""),
        ),
    )


def _rdps_debug_record_applicable_effect(
    *,
    window: dict[str, Any],
    effect: dict[str, Any],
    scope: str,
    rdps_credit: float | None = None,
) -> dict[str, Any]:
    source_key = window.get("source_character_key")
    raw_source_key = window.get("raw_source_character_key")
    zone = str(effect.get("zone") or "")
    row = {
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
        "zone": zone,
        "zone_label": _RDPS_DEBUG_ZONE_LABELS.get(zone, zone),
        "element": str(effect.get("element") or "all"),
        "rate": _round4(effect.get("rate") or 0.0),
        "rdps_credit": _round4(rdps_credit),
    }
    condition = effect.get("condition")
    if isinstance(condition, dict):
        row["condition"] = dict(condition)
    for key in ('rdps_whitelist_keys', 'external_rdps_allowed', 'keyword_origin', 'recipient_side', 'converted_attribute'):
        if key in effect:
            row[key] = copy.deepcopy(effect[key])
    return row


def _rdps_debug_record_dpd_self_residual(
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
        "zone_label": _RDPS_DEBUG_ZONE_LABELS.get(zone, zone),
        "element": str(hit.get("damage_element") or "all"),
        "rate": _round4(rate),
        "rdps_credit": None,
    }


def _rdps_debug_record_baseline_self(
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
    label = _ATTR_TYPE_BUFF_LABELS.get(attr_type) or _RDPS_DEBUG_ZONE_LABELS.get(zone, zone)
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
        "zone_label": _RDPS_DEBUG_ZONE_LABELS.get(zone, zone),
        "element": element,
        "rate": _round4(rate),
        "rdps_credit": None,
        "baseline_final": _round4(final_value),
        "baseline_captured": _round4(captured),
    }


def _rdps_debug_record_static_self(
    *,
    hit: dict[str, Any],
    entry: dict[str, Any],
) -> dict[str, Any]:
    attacker_key = str(hit.get("character_key") or "")
    attacker_name = str(hit.get("character_name") or _resolve_character_name(attacker_key) or attacker_key)
    zone = str(entry.get("zone") or "")
    source_type = str(entry.get("source_type") or "static")
    source_name = str(entry.get("source_name") or "静态来源")
    return {
        "scope": "self",
        "source_character_key": attacker_key,
        "source_character_name": attacker_name,
        "raw_source_character_key": attacker_key,
        "raw_source_character_name": attacker_name,
        "raw_source": source_type,
        "target_character_key": attacker_key,
        "target_character_name": attacker_name,
        "owner_raw": source_type,
        "event_key": f"__static_self_{source_type}__",
        "event_name": f"自身静态基线（{source_name} / {entry.get('label') or zone}）",
        "uid": None,
        "start_ts_ms": None,
        "start_time": None,
        "end_ts_ms": None,
        "end_time": None,
        "zone": zone,
        "zone_label": _RDPS_DEBUG_ZONE_LABELS.get(zone, zone),
        "element": str(entry.get("element") or "all"),
        "rate": _round4(entry.get("rate") or 0.0),
        "rdps_credit": None,
        "note": entry.get("note"),
    }


def _effect_condition_failure(effect: dict[str, Any], hit: dict[str, Any]) -> dict[str, Any] | None:
    if "condition" not in effect:
        return None
    condition = effect.get("condition")
    missing = {"reason": "condition_state_missing", "reason_group": "condition_unobservable"}
    if not isinstance(condition, dict):
        return {**missing, "detail": "malformed_condition"}
    condition_type = str(condition.get("type") or "")
    missing.update({"condition_type": condition_type, "condition_source": condition.get("source")})
    if condition_type == "always":
        return None
    if condition_type == 'damage_decorate_mask':
        required, check = condition.get('mask'), condition.get('check')
        actual = hit.get('damage_decorate_mask')
        if type(required) is not int or not 0 <= required < 2**64 or check not in {'HasAll', 'HasAny'}:
            return {**missing, 'detail': 'missing_or_invalid_damage_decorate_mask'}
        if actual is None:
            static = hit.get('static_damage_decorate_mask')
            if (hit.get('static_damage_decorate_mask_source') == 'current_typed_skill_or_buff_exact_unit_generation_only'
                    and type(static) is int and 0 <= static < 2**64):
                # The same exact unit proves positive skill categories and
                # physical/spell anomaly bits used by arts-strength eligibility.
                # It does not prove absent bits, crit or other runtime state.
                positive = static & (0x22784 | 0x4001C000 | 0x0FD00038)
                overlap = positive & required
                if (check == 'HasAny' and overlap) or (check == 'HasAll' and overlap == required):
                    return None
            return {**missing, 'detail': 'missing_or_invalid_damage_decorate_mask'}
        if type(actual) is not int or not 0 <= actual < 2**64:
            return {**missing, 'detail': 'missing_or_invalid_damage_decorate_mask'}
        overlap = actual & required
        passed = overlap == required if check == 'HasAll' else overlap != 0
        return None if passed else {'reason': 'damage_decorate_mask_filtered', 'reason_group': 'condition_unsatisfied'}
    if condition_type in {"all", "any"}:
        children = condition.get("conditions")
        if not isinstance(children, list) or not children or any(not isinstance(child, dict) for child in children):
            return {**missing, "detail": "malformed_compound_condition"}
        failures = [_effect_condition_failure({"condition": child}, hit) for child in children]
        if condition_type == 'any' and any(failure is None for failure in failures):
            return None
        failures = [failure for failure in failures if failure is not None]
        if not failures:
            return None
        return next((failure for failure in failures if failure.get("reason_group") == "condition_unobservable"), failures[0])
    if condition_type in {"target_hp_compare", "target_hp_ratio_lte"}:
        compare = condition.get("compare") if condition_type == "target_hp_compare" else "LE"
        is_ratio = condition.get("is_ratio") if condition_type == "target_hp_compare" else True
        threshold = _coerce_optional_float(condition.get("threshold"))
        subject = condition.get('subject_side', 'defender')
        if subject not in {'attacker', 'defender'}:
            return {**missing, 'detail': 'invalid_condition_subject'}
        hp_before = _coerce_optional_float(hit.get('attacker_hp_before' if subject == 'attacker' else 'enemy_hp_before'))
        maximum = _coerce_optional_float(hit.get('attacker_max_hp' if subject == 'attacker' else 'target_enemy_max_hp'))
        if (compare not in {"LT", "LE", "GT", "GE", "EQ", "NE"} or not isinstance(is_ratio, bool)
                or threshold is None or threshold < 0 or hp_before is None or hp_before < 0):
            return {**missing, "detail": "invalid_hp_comparison"}
        if is_ratio and (maximum is None or maximum <= 0 or hp_before > maximum or threshold > 1):
            return {**missing, "detail": "missing_explicit_maximum_hp"}
        value = hp_before / maximum if is_ratio else hp_before
        passed = {"LT": value < threshold, "LE": value <= threshold, "GT": value > threshold,
                  "GE": value >= threshold, "EQ": value == threshold, "NE": value != threshold}[compare]
        if passed:
            return None
        return {"reason": "target_hp_condition_unsatisfied", "reason_group": "condition_unsatisfied",
                "condition_type": condition_type, "condition_compare": compare, "condition_threshold": threshold,
                "target_hp_before": hp_before, "target_max_hp": maximum, "compared_value": value}
    if condition_type == "target_has_buff":
        ids = condition.get("buff_ids")
        if not isinstance(ids, list) or not ids or any(not isinstance(key, str) or not key for key in ids):
            return {**missing, "detail": "invalid_required_buff_ids"}
        subject = condition.get('subject_side', 'defender')
        if subject not in {'attacker', 'defender'}:
            return {**missing, 'detail': 'invalid_condition_subject'}
        active = hit.get('_active_attacker_buff_ids' if subject == 'attacker' else '_active_target_buff_ids')
        if not isinstance(active, (list, set, tuple)):
            return {**missing, "detail": "missing_target_buff_state"}
        if set(ids).intersection(active):
            return None
        if hit.get('attacker_buff_state_complete' if subject == 'attacker' else 'target_buff_state_complete') is not True:
            return {**missing, "detail": "incomplete_target_buff_state", "condition_buff_ids": ids}
        return {"reason": "target_buff_missing", "reason_group": "condition_unsatisfied", "condition_buff_ids": ids}
    if condition_type == "damage_type_in":
        elements = condition.get("elements")
        valid = {"physical", "fire", "pulse", "cryst", "natural"}
        actual = hit.get("damage_element")
        if not isinstance(elements, list) or not elements or any(value not in valid for value in elements) or actual not in valid:
            return {**missing, "detail": "unresolved_damage_type"}
        if actual in elements:
            return None
        return {"reason": "damage_type_filtered", "reason_group": "condition_unsatisfied",
                "condition_type": condition_type, "condition_elements": elements, "damage_element": actual}
    return {**missing, "reason": "condition_type_unsupported", "detail": "unsupported_condition"}


def _rdps_contribution_rows(
    contributions_map: dict[str, float],
    *,
    sort_by_value: bool,
) -> list[dict[str, Any]]:
    items = sorted(
        contributions_map.items(),
        key=(lambda item: (-item[1], item[0])) if sort_by_value else (lambda item: item[0]),
    )
    return [
        {
            "character_key": character_key,
            "character_name": _resolve_character_name(character_key) or character_key,
            "value": value,
        }
        for character_key, value in items
        if value > 0.0
    ]


def _duration_ms_from_seconds(value: str | None) -> int | None:
    seconds = _coerce_float(value, default=-1.0)
    if seconds < 0:
        return None
    if seconds > 1_000_000:
        return None
    return int(round(seconds * 1000))


def _build_iso_datetime(reference_date: date, timestamp_ms: int, rollover_days: int = 0) -> str:
    midnight = datetime.combine(reference_date, time.min)
    result = midnight + timedelta(days=rollover_days, milliseconds=timestamp_ms)
    return result.astimezone().isoformat(timespec="seconds")


def _normalize_buff_id(buff_id: str | None) -> str:
    return str(buff_id or "unknown_buff")


def _is_canonical_packet_buff_key(buff_id: str | None) -> bool:
    text = _normalize_buff_id(buff_id)
    return bool(text) and text != "unknown_buff" and not text.isdigit()


def _prefer_packet_buff_record(previous: dict[str, Any], current: dict[str, Any]) -> bool:
    previous_key = _normalize_buff_id(previous.get("event_key"))
    current_key = _normalize_buff_id(current.get("event_key"))
    if previous_key == current_key:
        return False
    previous_canonical = _is_canonical_packet_buff_key(previous_key)
    current_canonical = _is_canonical_packet_buff_key(current_key)
    if current_canonical and not previous_canonical:
        return True
    if previous_canonical and not current_canonical:
        return False
    if current_key == "unknown_buff" and previous_key != "unknown_buff":
        return False
    if previous_key == "unknown_buff" and current_key != "unknown_buff":
        return True
    return False


def _is_noise_buff(buff_id: str) -> bool:
    return buff_id in _BUFF_NOISE_IDS or buff_id.startswith(_BUFF_NOISE_PREFIXES)


def _should_ignore_rate_buff(buff_id: str) -> bool:
    return any(pattern.search(buff_id) for pattern in _RATE_IGNORE_BUFF_PATTERNS)


def _should_ignore_record_rate_effects(record: dict[str, Any]) -> bool:
    buff_id = _normalize_buff_id(record.get("event_key"))
    if buff_id == "buff_equipsuit_atk_02_aruadetect":
        return True
    return False


def _is_effectless_wrapper_record(record: dict[str, Any]) -> bool:
    buff_id = _normalize_buff_id(record.get("event_key"))
    packet_mapping = record.get("packet_mapping")
    if isinstance(packet_mapping, dict) and str(packet_mapping.get("role") or "").lower() == "effect":
        return False
    entry = _load_local_buff_semantic_entries().get(buff_id)
    if isinstance(entry, dict):
        action_types = {str(item or "") for item in entry.get("actionTypes") or []}
        if "VulnerableAction" in action_types:
            return False
    if _is_internal_trigger_damage_record(record):
        return True
    if record.get("attr_mods"):
        return False
    if _is_packet_effectless_wrapper(record):
        return True
    hint = _buff_classifier_hint(buff_id) or {}
    classification = str(hint.get("classification") or "")
    if classification not in {"wrapper", "marker_or_utility"}:
        return False
    if hint.get("resolvedEffectHints") or int(hint.get("resolvedEffectCount") or 0) > 0:
        return False
    semantic_flags = hint.get("semanticFlags") if isinstance(hint.get("semanticFlags"), dict) else {}
    created_or_referenced_buff_ids = [
        str(child_id)
        for field in ("createdBuffIds", "referencedBuffIds", "binaryCreatedBuffIds", "binaryReferencedBuffIds")
        for child_id in hint.get(field) or []
        if str(child_id or "")
    ]
    if semantic_flags.get("hasDynamicBlackboard"):
        entry = _load_local_buff_semantic_entries().get(buff_id)
        if isinstance(entry, dict):
            action_types = {str(item or "") for item in entry.get("actionTypes") or []}
            created_or_referenced = {
                str(child_id)
                for field in ("createdBuffIds", "referencedBuffIds", "binaryCreatedBuffIds", "binaryReferencedBuffIds")
                for child_id in entry.get(field) or []
                if str(child_id or "")
            }
            if "OnSpellAbnormalStartFinish" in action_types and created_or_referenced:
                return True
        return False
    if classification == "wrapper" and created_or_referenced_buff_ids:
        return True
    created_buff_ids = [
        str(buff_id)
        for buff_id in hint.get("createdBuffIds") or []
        if str(buff_id or "")
    ]
    return bool(created_buff_ids) and all(_is_noise_buff(created_buff_id) for created_buff_id in created_buff_ids)


def _add_label(labels: set[str], label: str | None) -> None:
    if label:
        labels.add(label)


def _anomaly_status_labels(
    buff_id: str,
    *,
    source_key: str,
    target_is_enemy: bool,
    bb_values: dict[str, Any],
) -> set[str]:
    labels: set[str] = set()
    lowered = buff_id.lower()

    if lowered == "buff_physical_no_guard":
        labels.add("破防")
    elif lowered == "buff_physical_knockdown":
        labels.add("倒地")
    elif lowered == "buff_physical_airborne":
        labels.add("击飞")
    elif lowered == "buff_physical_crushed":
        labels.add("猛击")
    elif lowered == "buff_common_cryst_triggered_physical_break":
        labels.add("猛击")

    if "conduct_triggered" in lowered:
        labels.add("导电")
    if (
        "corrupt_triggered" in lowered
        or "corrupt_do" in lowered
        or re.match(r"^buff_common_(?:try_)?natural_(?:fire|pulse|cryst|natural)_triggered(?:_wrapper)?$", lowered)
    ):
        labels.add("腐蚀")
    if "fire_fire_triggered" in lowered or "burn" in lowered:
        labels.add("燃烧")

    same_element_burst = re.match(
        r"^buff_common_(fire|pulse|cryst|natural|spell)_\1_triggered(?:_wrapper|_do)?$",
        lowered,
    )
    if same_element_burst:
        labels.add("法术爆发")

    if (
        "frozen" in lowered
        and "physical_res_down" not in bb_values
        and "phy_resist_down" not in bb_values
    ):
        labels.add("冻结")

    if target_is_enemy and source_key == "chr_0029_pograni" and "physical_res_down" in bb_values:
        labels.add("碎甲")

    return labels


def _label_from_bb_key(
    bb_key: str,
    *,
    buff_id: str,
    target_is_enemy: bool,
) -> str | None:
    lowered = bb_key.lower()
    if not lowered or lowered in _BUFF_NOISE_BB_KEYS:
        return None
    if _buff_effect_bb_key_ignored(buff_id, lowered):
        return None
    if lowered in {"atk", "atk_up", "atk_up2"}:
        return "攻击提升"
    if lowered in {"phy_resist_down", "physical_res_down", "physical_vulnerable_dmg_increase"}:
        return "物理脆弱 / 碎甲"
    if lowered == "ratio_speed":
        return "加速"
    if lowered == "ratio_speedreduction":
        return "缓速"
    if lowered in {"crit_up", "crit_up2", "critical_rate"}:
        return "暴击"
    if lowered in {"crit_dmg", "critical_damage_inc"}:
        return "暴击伤害"
    if lowered in {"fire_up", "pulse_up", "cryst_up", "crystal_up", "natural_up", "physical_up", "physic_up"}:
        return "脆弱" if target_is_enemy else "增幅"
    if _is_damage_increase_bb_key(lowered) or lowered.endswith("_up_valid"):
        return "增伤"
    if lowered.endswith("_taken_up"):
        return "承伤易伤"
    if lowered == "def_down_damage":
        return "脆弱"
    if lowered in {"def_decrease", "additional_def_decrease"}:
        return "减抗"
    if lowered.endswith("_vul"):
        return "脆弱"
    if lowered.startswith("rate_") and "vulnerable" in lowered:
        return "脆弱"
    if lowered.startswith("enhance_"):
        return "增幅"
    if re.match(r"^(?:.+_)?taken_up_(?:physical|physic|fire|pulse|cryst|crystal|natural|spell)$", lowered):
        return "承伤易伤"
    if re.match(r"^damage_taken_up_(?:physical|physic|fire|pulse|cryst|crystal|natural|spell)$", lowered):
        return "承伤易伤"
    if lowered.endswith("_resistance_decrease") or lowered.endswith("_resistance_down"):
        return "承伤易伤"
    if lowered.endswith("_res_down") or (lowered.startswith("ignore_") and lowered.endswith("_resist")):
        return "减抗"
    if lowered == "combo_damageup":
        return "连击增伤"
    if lowered in {"atk_scale", "extra_atk_scale", "burning_atk_scale"} and target_is_enemy:
        buff_lower = buff_id.lower()
        if any(token in buff_lower for token in ("vulnerable", "fragile", "fracture")):
            return "脆弱"
        return None
    if lowered == "rate":
        buff_lower = buff_id.lower()
        if buff_lower in {
            "buff_chr_0015_lifeng_purify",
            "buff_chr_0015_lifeng_normal_skill_debuff",
            "buff_chr_0015_lifeng_normal_skill_debuff_icon",
        }:
            return "物理脆弱"
        if any(token in buff_lower for token in ("vulnerable", "fragile", "waterdebuff")):
            return "脆弱"
        if "enhance_" in buff_lower:
            return "增幅"
        if any(token in buff_lower for token in ("spellup", "dmg", "damage_increase")):
            return "增伤"
        if "criticalrate" in buff_lower:
            return "暴击"
        if "combo_damage" in buff_lower:
            return "连击增伤"
    return None


def _buff_effect_bb_key_ignored(buff_id: str, bb_key: str) -> bool:
    ignored_keys = _BUFF_EFFECT_BB_KEY_IGNORE_BY_ID.get(str(buff_id or "").lower())
    return ignored_keys is not None and str(bb_key or "").lower() in ignored_keys


def _collect_buff_labels(record: dict[str, Any], *, root_lookup_cache: dict | None = None) -> list[str]:
    buff_id = _normalize_buff_id(record.get("event_key"))
    target_is_enemy = bool(record.get("target_enemy_key"))
    source_key = str(record.get("source_character_key") or "")
    bb_values = {str(key).lower(): value for key, value in (record.get("bb_values") or {}).items()}
    semantic_condition_effects = _collect_semantic_condition_zone_effects(record, root_lookup_cache=root_lookup_cache)
    anomaly_labels = _anomaly_status_labels(
        buff_id,
        source_key=source_key,
        target_is_enemy=target_is_enemy,
        bb_values=bb_values,
    )
    if _should_ignore_record_rate_effects(record):
        return sorted(anomaly_labels, key=lambda item: (_BUFF_LABEL_PRIORITY.get(item, 99), item))
    if (_is_noise_buff(buff_id) or _should_ignore_rate_buff(buff_id)) and not semantic_condition_effects:
        return sorted(anomaly_labels, key=lambda item: (_BUFF_LABEL_PRIORITY.get(item, 99), item))
    if _is_effectless_wrapper_record(record) and anomaly_labels and not semantic_condition_effects:
        return sorted(anomaly_labels, key=lambda item: (_BUFF_LABEL_PRIORITY.get(item, 99), item))
    if _is_effectless_wrapper_record(record) and not semantic_condition_effects:
        return []

    labels: set[str] = set(anomaly_labels)
    special_bb_labels: dict[str, str] = {}

    if buff_id == _GENERIC_COMBO_TRIGGER_BUFF_ID:
        labels.add("连击增伤")
    if buff_id in {
        "buff_chr_0015_lifeng_purify",
        "buff_chr_0015_lifeng_normal_skill_debuff",
        "buff_chr_0015_lifeng_normal_skill_debuff_icon",
    }:
        labels.add("物理脆弱")
    if buff_id == "buff_chr_0029_pograni_talent2":
        labels.add("铁卫标记")
    if "physical_res_down" in bb_values:
        if source_key == "chr_0029_pograni":
            special_bb_labels["physical_res_down"] = "碎甲"
            labels.add("碎甲")
        else:
            special_bb_labels["physical_res_down"] = "物理脆弱"
            labels.add("物理脆弱")
    if "phy_resist_down" in bb_values:
        special_bb_labels["phy_resist_down"] = "物理脆弱"

    semantic_bb_keys = {
        str(effect.get("bb_key") or "")
        for effect in semantic_condition_effects
        if str(effect.get("bb_key") or "")
    }
    semantic_bb_keys_lower = {key.lower() for key in semantic_bb_keys}
    packet_label_effects = [
        effect
        for effect in _packet_numeric_effects(record)
        if not (str(effect.get("bb_key") or "") and str(effect.get("bb_key") or "") in semantic_bb_keys)
    ]
    for effect in packet_label_effects + _packet_numeric_dynamic_effects(record):
        zone = str(effect.get("zone") or "")
        label = _EFFECT_ZONE_LABELS.get(zone)
        if label:
            labels.add(label)
    for effect in semantic_condition_effects:
        zone = str(effect.get("zone") or "")
        label = '承伤易伤' if zone == 'vuln_taken' else _EFFECT_ZONE_LABELS.get(zone)
        if label:
            labels.add(label)
    for bb_key in record.get("bb_keys") or []:
        lowered = str(bb_key).lower()
        if lowered in semantic_bb_keys_lower:
            continue
        if lowered in special_bb_labels:
            labels.add(special_bb_labels[lowered])
            continue
        _add_label(
            labels,
            _label_from_bb_key(
                str(bb_key),
                buff_id=buff_id,
                target_is_enemy=target_is_enemy,
            ),
        )

    attr_label_rows = record.get("attr_mods") or [
        {"attr_type": attr_type, "bb_key": ""}
        for attr_type in record.get("attr_types") or []
    ]
    for attr_mod in attr_label_rows:
        try:
            attr_type_int = int(attr_mod.get("attr_type"))
        except (TypeError, ValueError):
            continue
        bb_key = str(attr_mod.get("bb_key") or "").lower()
        if _buff_effect_bb_key_ignored(buff_id, bb_key):
            continue
        if bb_key in special_bb_labels:
            labels.add(special_bb_labels[bb_key])
            continue
        attr_effect = _ATTR_TYPE_TO_EFFECT.get(attr_type_int)
        if (
            attr_effect is not None
            and attr_effect[0] == "amp"
            and _is_damage_increase_bb_key(str(attr_mod.get("bb_key") or ""))
        ):
            continue
        if str(attr_mod.get("bb_key") or "") in _DEF_DECREASE_BASE_KEYS:
            labels.add("减抗")
            continue
        if attr_type_int in _FRAGILE_ATTR_TYPES:
            labels.add("脆弱")
            continue
        _add_label(labels, _ATTR_TYPE_BUFF_LABELS.get(attr_type_int))

    return sorted(labels, key=lambda item: (_BUFF_LABEL_PRIORITY.get(item, 99), item))


def _normalize_buff_duration_ms(
    start_ts_ms: int,
    *,
    event_key: str | None = None,
    end_ts_ms: int | None,
    raw_duration_ms: int | None,
    battle_end_ms: int,
) -> int:
    remaining = max(battle_end_ms - start_ts_ms, 0)
    if end_ts_ms is not None and end_ts_ms >= start_ts_ms:
        # Packet end timestamps include pauses. A nominal game-time duration
        # must not shorten the explicitly observed lifetime.
        return min(remaining, end_ts_ms - start_ts_ms)
    if raw_duration_ms is not None and raw_duration_ms >= 0:
        return min(remaining, raw_duration_ms)
    root = _load_current_buff_roots().get(_normalize_buff_id(event_key), {})
    return remaining if root.get('lifeType') == 'Infinity' else 0


def _extend_buff_records_from_packet_modifiers(
    buff_starts: list[dict[str, Any]],
    packet_modifier_last_seen_by_uid: dict[str, int],
) -> None:
    # UID occurrence proves participation in that hit, not an interval end.
    for record in buff_starts:
        uid = str(record.get("uid") or "")
        if uid in packet_modifier_last_seen_by_uid:
            record["packet_modifier_last_seen_ts_ms"] = packet_modifier_last_seen_by_uid[uid]


_RELATED_BUFF_LIFECYCLE_START_TOLERANCE_MS = 250


def _buff_record_lifecycle_target_key(record: dict[str, Any]) -> str:
    return str(
        record.get("target_character_key")
        or record.get("target_enemy_key")
        or record.get("owner_raw")
        or ""
    )


def _buff_record_lifecycle_lane_matches(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if str(left.get("source_character_key") or "") != str(right.get("source_character_key") or ""):
        return False
    if _buff_record_lifecycle_target_key(left) != _buff_record_lifecycle_target_key(right):
        return False
    return (
        abs(int(left.get("ts_ms") or 0) - int(right.get("ts_ms") or 0))
        <= _RELATED_BUFF_LIFECYCLE_START_TOLERANCE_MS
    )


def _buff_record_static_effect_pairs(record: dict[str, Any]) -> set[tuple[str, str, float]]:
    pairs: set[tuple[str, str, float]] = set()
    for effect in _collect_zone_effects(record):
        try:
            rate = round(float(effect.get("rate") or 0.0), 6)
        except (TypeError, ValueError):
            continue
        if rate <= 0:
            continue
        pairs.add(
            (
                str(effect.get("zone") or ""),
                _normalize_effect_element(effect.get("element")),
                rate,
            )
        )
    return pairs


def _buff_records_have_matching_lifecycle_effects(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_key = str(left.get("event_key") or "")
    right_key = str(right.get("event_key") or "")
    if not (left_key.endswith("_default_child") or right_key.endswith("_default_child")):
        return False
    left_pairs = _buff_record_static_effect_pairs(left)
    right_pairs = _buff_record_static_effect_pairs(right)
    if left_pairs & right_pairs:
        return True
    child_key = left_key if left_key.endswith("_default_child") else right_key
    parent_pairs = right_pairs if left_key.endswith("_default_child") else left_pairs
    if child_key.startswith("buff_common_affixes_vulnerable"):
        return any(zone in {"fragile", "vuln_taken", "res"} for zone, _element, _rate in parent_pairs)
    return False


def _buff_records_lifecycle_related(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_key = str(left.get("event_key") or "")
    right_key = str(right.get("event_key") or "")
    if not left_key or not right_key or left_key == right_key:
        return False
    return (
        _buff_semantically_reaches_buff(left_key, right_key)
        or _buff_semantically_reaches_buff(right_key, left_key)
        or _buff_records_have_matching_lifecycle_effects(left, right)
    )


def _record_has_authoritative_bb_duration(record: dict[str, Any]) -> bool:
    return str(record.get("raw_duration_source") or "") == "bb"


def _infer_related_buff_end_times(
    buff_starts: list[dict[str, Any]],
    skill_casts: list[dict[str, Any]],
) -> None:
    return


def _merge_buff_windows(buff_windows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Temporal proximity does not prove refresh or shared instance identity.
    return sorted(buff_windows, key=lambda row: (
        int(row.get("start_ts_ms") or 0), int(row.get("line_no") or 0),
    ))


def _window_uid_aliases(window: dict[str, Any]) -> set[str]:
    aliases: set[str] = set()
    uid = str(window.get("uid") or "")
    if uid:
        aliases.add(uid)
    for alias in window.get("uid_aliases") or []:
        text = str(alias or "")
        if text:
            aliases.add(text)
    return aliases


def _merge_window_uid_aliases(target: dict[str, Any], other: dict[str, Any]) -> None:
    aliases = _window_uid_aliases(target) | _window_uid_aliases(other)
    primary_uid = str(target.get("uid") or "")
    if primary_uid:
        aliases.discard(primary_uid)
    target["uid_aliases"] = sorted(aliases)


def _effect_condition_signature(effect: dict[str, Any]) -> str:
    condition = effect.get("condition")
    if not isinstance(condition, dict):
        return ""
    return json.dumps(condition, ensure_ascii=False, sort_keys=True)


def _effect_row_signature(effect: dict[str, Any]) -> tuple[Any, ...]:
    if "rate" in effect:
        return (
            "static",
            str(effect.get("zone") or ""),
            _normalize_effect_element(effect.get("element")),
            round(float(effect.get("rate") or 0.0), 6),
            str(effect.get("bb_key") or ""),
            int(effect.get("attr_type")) if effect.get("attr_type") is not None else None,
            _effect_condition_signature(effect),
            effect.get('recipient_side'),
            effect.get('external_rdps_allowed'),
            tuple(sorted(effect.get('rdps_whitelist_keys') or [])),
        )
    return (
        "dynamic",
        str(effect.get("zone") or ""),
        _normalize_effect_element(effect.get("element")),
        round(float(effect.get("base_rate") or 0.0), 6),
        round(float(effect.get("tick_rate") or 0.0), 6),
        round(float(effect.get("max_rate") or 0.0), 6),
        round(float(effect.get("delayed_add_rate") or 0.0), 6),
        round(float(effect.get("delay_sec") or 0.0), 6),
        str(effect.get("bb_key") or ""),
        str(effect.get("add_bb_key") or ""),
        str(effect.get("delay_bb_key") or ""),
        _effect_condition_signature(effect),
        effect.get('recipient_side'),
        effect.get('external_rdps_allowed'),
        tuple(sorted(effect.get('rdps_whitelist_keys') or [])),
    )


def _merge_effect_lists(primary: list[dict[str, Any]] | None, secondary: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for effect in [*(primary or []), *(secondary or [])]:
        if not isinstance(effect, dict):
            continue
        signature = _effect_row_signature(effect)
        if signature in seen:
            continue
        seen.add(signature)
        merged.append(dict(effect))
    return merged


def _prefer_window_event_name(primary: dict[str, Any], secondary: dict[str, Any]) -> str:
    primary_name = str(primary.get("event_name") or "")
    secondary_name = str(secondary.get("event_name") or "")
    event_key = str(primary.get("event_key") or secondary.get("event_key") or "")
    if primary_name and primary_name != event_key and secondary_name == event_key:
        return primary_name
    if secondary_name and secondary_name != event_key and primary_name == event_key:
        return secondary_name
    if len(secondary_name) > len(primary_name):
        return secondary_name
    return primary_name or secondary_name or event_key


def _merge_window_content(target: dict[str, Any], other: dict[str, Any]) -> None:
    target_start = int(target.get("start_ts_ms") or 0)
    other_start = int(other.get("start_ts_ms") or 0)
    target_line = int(target.get("start_line_no") or target.get("line_no") or 0)
    other_line = int(other.get("start_line_no") or other.get("line_no") or 0)
    if other_start < target_start or (other_start == target_start and other_line and (not target_line or other_line < target_line)):
        target["start_line_no"] = other_line
    _merge_window_uid_aliases(target, other)
    target["end_ts_ms"] = max(int(target.get("end_ts_ms") or 0), int(other.get("end_ts_ms") or 0))
    target["duration_ms"] = max(int(target["end_ts_ms"]) - int(target.get("start_ts_ms") or 0), 1)
    target["zone_effects"] = _merge_effect_lists(target.get("zone_effects"), other.get("zone_effects"))
    target["dynamic_effects"] = _merge_effect_lists(target.get("dynamic_effects"), other.get("dynamic_effects"))
    target["event_name"] = _prefer_window_event_name(target, other)
    if not target.get("stack_limit") and other.get("stack_limit"):
        target["stack_limit"] = other.get("stack_limit")


def _window_matches_packet_uids(window: dict[str, Any], packet_uids: set[str]) -> bool:
    if not packet_uids:
        return True
    aliases = _window_uid_aliases(window)
    if not aliases:
        return True
    return bool(aliases & packet_uids)


def _buff_window_stack_group_key(window: dict[str, Any]) -> tuple[str, str, str, str]:
    event_key = str(window.get('event_key') or '')
    root = _load_current_buff_roots().get(event_key)
    settings = root.get('stackingSettings') if isinstance(root, dict) else None
    key = event_key
    if isinstance(settings, dict) and settings.get('identifierType') == 'StackingKey':
        key = str(settings.get('stackingKey') or '')
    # BuffContainer owns the dictionary; BuffData.get_stackingKey supplies its
    # key. Source and skill family are not part of the native grouping rule.
    actor_id = str(window.get('target_actor_id') or '')
    return (
        'actor' if actor_id else 'unresolved_actor',
        actor_id or str(window.get('target_character_key') or ''),
        key,
        '',
    )


def _apply_buff_stack_limits(buff_windows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # A stack limit constrains the enabled set, not instance lifetime. A lower
    # priority instance can remain alive while disabled and later be enabled.
    # Never invent BUFF_END by dropping the oldest UID. Resolve competing
    # instances at each hit, after applying the observed enable timeline.
    limited: list[dict[str, Any]] = []
    for window in buff_windows:
        window = dict(window)
        root = _load_current_buff_roots().get(str(window.get('event_key') or ''))
        stacking = root.get('stackingSettings') if isinstance(root, dict) else None
        if isinstance(stacking, dict):
            window['stacking_type'] = stacking.get('stackingType')
            limit = _buff_stack_limit(str(window.get("event_key") or ""), window.get('bb_values'))
            if window['stacking_type'] == 'Unlimited':
                limit = None
            elif limit is None:
                window['rdps_unresolved_stack_evidence'] = True
        else:
            limit = _positive_int(window.get('stack_limit'))
        window['stack_limit'] = limit
        if int(window.get('end_ts_ms') or 0) < int(window.get('start_ts_ms') or 0):
            continue
        window["duration_ms"] = max(int(window["end_ts_ms"]) - int(window["start_ts_ms"]), 1)
        limited.append(window)
    return sorted(limited, key=lambda item: (int(item.get("start_ts_ms") or 0), str(item.get("event_key") or "")))


def _static_effect_signature(window: dict[str, Any]) -> tuple[tuple[Any, ...], ...] | None:
    if window.get("dynamic_effects"):
        return None
    effects = window.get("zone_effects") or []
    if not effects:
        return None
    signature: list[tuple[Any, ...]] = []
    for effect in effects:
        try:
            rate = round(float(effect.get("rate") or 0.0), 4)
        except (TypeError, ValueError):
            return None
        if rate <= 0:
            return None
        signature.append(
            (
                str(effect.get("zone") or ""),
                _normalize_effect_element(effect.get("element")),
                rate,
                _effect_condition_signature(effect),
            )
        )
    return tuple(sorted(signature))


def _dynamic_effect_signature(window: dict[str, Any]) -> tuple[tuple[str, str, float, float, float, float, float], ...] | None:
    effects = window.get("dynamic_effects") or []
    if not effects:
        return None
    signature: list[tuple[str, str, float, float, float, float, float]] = []
    for effect in effects:
        try:
            signature.append(
                (
                    str(effect.get("zone") or ""),
                    _normalize_effect_element(effect.get("element")),
                    round(float(effect.get("base_rate") or 0.0), 6),
                    round(float(effect.get("tick_rate") or 0.0), 6),
                    round(float(effect.get("max_rate") or 0.0), 6),
                    round(float(effect.get("delayed_add_rate") or 0.0), 6),
                    round(float(effect.get("delay_sec") or 0.0), 6),
                )
            )
        except (TypeError, ValueError):
            return None
    return tuple(sorted(signature))


def _window_detail_count(window: dict[str, Any]) -> int:
    return len(window.get("zone_effects") or []) + len(window.get("dynamic_effects") or [])


def _same_event_window_signature_matches(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_static = set(_static_effect_signature(left) or [])
    right_static = set(_static_effect_signature(right) or [])
    left_dynamic = _dynamic_effect_signature(left)
    right_dynamic = _dynamic_effect_signature(right)
    if left_dynamic != right_dynamic:
        return False
    if left_static == right_static:
        return True
    if not left_static or not right_static:
        return True
    return left_static.issuperset(right_static) or right_static.issuperset(left_static)


def _mirror_preference_score(window: dict[str, Any]) -> int:
    event_key = str(window.get("event_key") or "")
    hint = _buff_classifier_hint(event_key) or {}
    classification = hint.get("classification")
    score = 0
    if int(window.get("modifier_count") or 0) > 0:
        score += 4
    if classification == "effect_buff":
        score += 3
    elif classification == "wrapper":
        score -= 2
    elif classification == "marker_or_utility":
        score -= 3
    lowered = event_key.lower()
    if "icon" in lowered or "vfx" in lowered:
        score -= 1
    return score


def _event_key_parent_of_default_child(event_key: str | None) -> str | None:
    text = str(event_key or "")
    suffix = "_default_child"
    if not text.endswith(suffix):
        return None
    return text[: -len(suffix)]


def _effect_elements_compatible(parent_element: str, child_element: str) -> bool:
    if parent_element == child_element:
        return True
    if child_element == "all":
        return True
    if child_element == "spell":
        return parent_element in _ELEMENTAL_ELEMENTS or parent_element == "spell"
    return False


def _parent_window_covers_default_child(parent: dict[str, Any], child: dict[str, Any]) -> bool:
    parent_effects = parent.get("zone_effects") or []
    child_effects = child.get("zone_effects") or []
    if not parent_effects or not child_effects:
        return False

    parent_elements_by_lane: defaultdict[tuple[str, float], set[str]] = defaultdict(set)
    for effect in parent_effects:
        try:
            rate = round(float(effect.get("rate") or 0.0), 6)
        except (TypeError, ValueError):
            continue
        if rate <= 0:
            continue
        lane = (str(effect.get("zone") or ""), rate)
        parent_elements_by_lane[lane].add(_normalize_effect_element(effect.get("element")))

    for child_effect in child_effects:
        try:
            child_rate = round(float(child_effect.get("rate") or 0.0), 6)
        except (TypeError, ValueError):
            return False
        child_lane = (str(child_effect.get("zone") or ""), child_rate)
        parent_elements = parent_elements_by_lane.get(child_lane)
        if not parent_elements:
            return False
        child_element = _normalize_effect_element(child_effect.get("element"))
        if child_element in {"spell", "all"} and len(parent_elements & _ELEMENTAL_ELEMENTS) < 2:
            if child_element not in parent_elements and "all" not in parent_elements:
                return False
        elif not any(_effect_elements_compatible(parent_element, child_element) for parent_element in parent_elements):
            return False
    return True


@lru_cache(maxsize=4096)
def _created_child_buff_ids(buff_id: str) -> set[str]:
    ids: set[str] = set()
    entry = _load_local_buff_semantic_entries().get(str(buff_id or ""))
    if isinstance(entry, dict):
        ids.update(str(value) for value in entry.get("createdBuffIds") or [] if str(value or ""))
    hint = _buff_classifier_hint(str(buff_id or "")) or {}
    ids.update(str(value) for value in hint.get("createdBuffIds") or [] if str(value or ""))
    return ids


def _window_static_effect_pairs(window: dict[str, Any]) -> set[tuple[str, str, float]]:
    pairs: set[tuple[str, str, float]] = set()
    for effect in window.get("zone_effects") or []:
        try:
            rate = round(float(effect.get("rate") or 0.0), 6)
        except (TypeError, ValueError):
            continue
        if rate <= 0:
            continue
        pairs.add(
            (
                str(effect.get("zone") or ""),
                _normalize_effect_element(effect.get("element")),
                rate,
            )
        )
    return pairs


def _window_matches_same_lane(parent: dict[str, Any], child: dict[str, Any]) -> bool:
    return (
        parent.get("source_character_key") == child.get("source_character_key")
        and parent.get("target_character_key") == child.get("target_character_key")
        and abs(int(parent.get("start_ts_ms") or 0) - int(child.get("start_ts_ms") or 0)) <= _BUFF_MIRROR_DEDUPE_WINDOW_MS
    )


def _parent_window_covers_created_children(parent: dict[str, Any], siblings: list[dict[str, Any]]) -> bool:
    child_ids = _created_child_buff_ids(str(parent.get("event_key") or ""))
    if not child_ids:
        return False
    parent_pairs = _window_static_effect_pairs(parent)
    if not parent_pairs:
        return False
    child_pairs: set[tuple[str, str, float]] = set()
    for other in siblings:
        if other is parent:
            continue
        if str(other.get("event_key") or "") not in child_ids:
            continue
        if not _window_matches_same_lane(parent, other):
            continue
        child_pairs.update(_window_static_effect_pairs(other))
    if not child_pairs:
        return False
    for zone, element, rate in parent_pairs:
        if (zone, element, rate) in child_pairs:
            continue
        if element == "spell":
            spell_children = {
                child_element
                for child_zone, child_element, child_rate in child_pairs
                if child_zone == zone and child_rate == rate and child_element in _ELEMENTAL_ELEMENTS
            }
            if spell_children:
                continue
        if element == "all":
            matching_children = [
                child_element
                for child_zone, child_element, child_rate in child_pairs
                if child_zone == zone and child_rate == rate
            ]
            if matching_children:
                continue
        return False
    return True


def _parent_window_covers_specific_siblings(parent: dict[str, Any], siblings: list[dict[str, Any]]) -> bool:
    parent_pairs = _window_static_effect_pairs(parent)
    if not parent_pairs:
        return False
    child_pairs: set[tuple[str, str, float]] = set()
    child_event_keys: set[str] = set()
    for other in siblings:
        if other is parent:
            continue
        if str(other.get("event_key") or "") == str(parent.get("event_key") or ""):
            continue
        if not _window_matches_same_lane(parent, other):
            continue
        pairs = _window_static_effect_pairs(other)
        if not pairs:
            continue
        child_pairs.update(pairs)
        child_event_keys.add(str(other.get("event_key") or ""))
    if not child_pairs or not child_event_keys:
        return False
    for zone, element, rate in parent_pairs:
        if (zone, element, rate) in child_pairs:
            continue
        if element == "spell":
            spell_children = {
                child_element
                for child_zone, child_element, child_rate in child_pairs
                if child_zone == zone and child_rate == rate and child_element in _ELEMENTAL_ELEMENTS
            }
            if spell_children:
                continue
        if element == "all":
            matching_children = [
                child_element
                for child_zone, child_element, child_rate in child_pairs
                if child_zone == zone and child_rate == rate
            ]
            if matching_children:
                continue
        return False
    return True


def _dedupe_mirrored_buff_windows(buff_windows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Remove only repeated observations of the same instance with identical
    # boundaries and effect data. Never select a richer-looking sibling.
    seen = set()
    result = []
    for window in buff_windows:
        uid = str(window.get("uid") or "")
        if not uid:
            result.append(window)
            continue
        identity = json.dumps({key: window.get(key) for key in (
            "uid", "uid_aliases", "source_character_key", "target_character_key", "event_key", "target_actor_id", "source_actor_id",
            "start_ts_ms", "end_ts_ms", "zone_effects", "dynamic_effects", "effect_segments",
            "start_line_no", "end_line_no", "snapshot_end_ts_ms", "snapshot_end_line_no",
            "enable_observations", "snapshot_update",
        )}, sort_keys=True, ensure_ascii=False)
        if identity not in seen:
            seen.add(identity)
            result.append(window)
    return result


def _assign_skill_timeline_group_keys(hits: list[dict[str, Any]]) -> None:
    latest_group_state: dict[tuple[str, str, str], dict[str, int]] = {}
    next_group_index: defaultdict[tuple[str, str, str], int] = defaultdict(int)

    for order_index, hit in sorted(
        enumerate(hits),
        key=lambda item: (int(item[1].get("ts_ms") or 0), item[0]),
    ):
        _ = order_index
        base_event_key = str(hit.get("skill_family_key") or hit.get("skill_key") or "")
        if not base_event_key:
            hit["timeline_group_key"] = None
            continue

        state_key = (
            str(hit.get("character_key") or ""),
            base_event_key,
            str(hit.get("target_enemy_key") or ""),
        )
        hit_ts_ms = int(hit.get("ts_ms") or 0)
        state = latest_group_state.get(state_key)
        if state is None or hit_ts_ms - state["last_ts_ms"] > _timeline_skill_group_gap_ms(hit):
            next_group_index[state_key] += 1

        group_index = next_group_index[state_key]
        latest_group_state[state_key] = {"last_ts_ms": hit_ts_ms}
        hit["timeline_group_key"] = f"{base_event_key}::{state_key[2] or 'unknown-target'}::{group_index}"


def _infer_missing_hit_damage_schools(
    hits: list[dict[str, Any]],
    buff_windows: list[dict[str, Any]],
) -> None:
        return


def _build_role_skill_stats_from_timeline_groups(
    hits: list[dict[str, Any]],
    roster_order: list[str],
) -> list[dict[str, Any]]:
    cast_totals: defaultdict[tuple[str, str, str], dict[str, Any]] = defaultdict(
        lambda: {
            "character_key": None,
            "character_name": None,
            "skill_key": None,
            "skill_name": None,
            "total_damage": 0,
        }
    )

    for index, hit in enumerate(hits):
        character_key = str(hit.get("character_key") or "")
        if not character_key:
            continue
        skill_key = str(hit.get("skill_family_key") or hit.get("skill_key") or "unknown_skill")
        cast_key = str(hit.get("timeline_group_key") or f"{skill_key}::hit-{index}")
        cast_entry = cast_totals[(character_key, skill_key, cast_key)]
        cast_entry["character_key"] = character_key
        cast_entry["character_name"] = hit.get("character_name") or _resolve_character_name(character_key) or character_key
        cast_entry["skill_key"] = skill_key
        cast_entry["skill_name"] = hit.get("skill_name") or _resolve_skill_name(skill_key) or skill_key
        cast_entry["total_damage"] += int(hit.get("hit_value") or 0)

    skill_totals: defaultdict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {
            "character_key": None,
            "character_name": None,
            "skill_key": None,
            "skill_name": None,
            "total_damage": 0,
            "max_damage": 0,
            "cast_count": 0,
        }
    )

    for (character_key, skill_key, _), cast_entry in cast_totals.items():
        cast_damage = int(cast_entry["total_damage"] or 0)
        skill_entry = skill_totals[(character_key, skill_key)]
        skill_entry["character_key"] = character_key
        skill_entry["character_name"] = cast_entry["character_name"]
        skill_entry["skill_key"] = skill_key
        skill_entry["skill_name"] = cast_entry["skill_name"]
        skill_entry["total_damage"] += cast_damage
        skill_entry["max_damage"] = max(int(skill_entry["max_damage"] or 0), cast_damage)
        skill_entry["cast_count"] += 1

    role_skill_stats = []
    for character_key in roster_order:
        character_skills = [
            entry
            for (entry_character_key, _), entry in skill_totals.items()
            if entry_character_key == character_key
        ]
        character_skills.sort(key=lambda item: (-item["total_damage"], item["skill_key"]))
        for entry in character_skills:
            cast_count = max(int(entry["cast_count"] or 0), 1)
            role_skill_stats.append(
                {
                    "character_key": character_key,
                    "character_name": entry["character_name"],
                    "skill_key": entry["skill_key"],
                    "skill_name": entry["skill_name"],
                    "cast_count": cast_count,
                    "total_damage": entry["total_damage"],
                    "avg_damage": round(entry["total_damage"] / cast_count, 2),
                    "max_damage": entry["max_damage"],
                }
            )

    return role_skill_stats


def _zone_from_label(label: str) -> str | None:
    return _ZONE_BY_LABEL.get(label)


def _safe_positive_rate(value: Any) -> float | None:
    rate = _coerce_optional_float(value)
    return rate if rate is not None and rate > 0 else None


def _semantic_blackboard_defaults(entry: dict[str, Any]) -> dict[str, Any]:
    defaults: dict[str, Any] = {}
    for row in entry.get("blackboard") or []:
        if not isinstance(row, dict):
            continue
        key = str(row.get("key") or "")
        if not key:
            continue
        if row.get("defaultDouble") is not None:
            defaults[key] = row.get("defaultDouble")
        elif row.get("defaultString") not in (None, ""):
            defaults[key] = row.get("defaultString")
    return defaults


def _semantic_damage_effect_zone(effect: dict[str, Any]) -> tuple[str, str] | None:
    if str(effect.get("processorType") or "") != "DamageScaleProcessor":
        return None
    zone_name = str(effect.get('zoneName') or '')
    config = _load_damage_zone_config().get(zone_name)
    if not isinstance(config, dict):
        return None
    side = str(effect.get("side") or effect.get("enableSide") or "")
    if zone_name == 'ProdCalcZone':
        if side == 'Attacker':
            return 'prod_dmg_inc', 'all'
        if side == 'Defender':
            return 'prod_vuln_taken', 'all'
        return None
    if zone_name != 'NormalCalcZone':
        return None
    if side == "Attacker":
        return "dmg_inc", "all"
    if side == "Defender":
        return "vuln_taken", "all"
    return None


def _semantic_damage_effect_condition(
    buff_id: str,
    effect: dict[str, Any],
    *,
    defaults: dict[str, Any],
    bb_values: dict[str, Any],
) -> dict[str, Any] | None:
    group_index = effect.get("groupIndex")
    if isinstance(group_index, bool) or not re.fullmatch(r"\d+", str(group_index)):
        return {"type": "unresolved", "errors": ["missing_condition_group_index"]}
    decoded = _decoded_damage_modifier_condition_rows(buff_id).get(int(group_index))
    if not decoded:
        return {"type": "unresolved", "errors": ["missing_decoded_condition"]}
    if decoded.get("errors"):
        return {"type": "unresolved", "errors": list(decoded["errors"])}
    if decoded.get("all_disabled"):
        return {"type": "always", "source": "decoded_disabled_conditions"}
    if decoded.get("action_count") == 0:
        return {"type": "always", "source": "decoded_empty_conditions"}
    conditions = []
    for raw in decoded.get("conditions") or []:
        condition = dict(raw)
        if condition.get('type') in {'target_hp_compare', 'target_has_buff'} and effect.get('enableSide') in {'Attacker', 'Defender'}:
            # ApplyModifier sets TargetHandle to the opposite participant
            # of the modifier's enabled/owning side, independently of the
            # processor's destination damage zone.
            condition['subject_side'] = 'defender' if effect['enableSide'] == 'Attacker' else 'attacker'
        if condition.get("type") == "target_hp_compare":
            config = condition.pop("value_config")
            key = str(config.get("blackboardKey") or "")
            value = bb_values.get(key) if config["useBlackboardKey"] else config.get("value")
            threshold = _coerce_optional_float(value)
            if threshold is None or threshold < 0 or (condition["is_ratio"] and threshold > 1):
                return {"type": "unresolved", "errors": ["missing_or_invalid_hp_threshold"], "bb_key": key}
            condition["threshold"] = threshold
            if config["useBlackboardKey"]:
                condition["threshold_key"] = key
        conditions.append(condition)
    if not conditions:
        return {"type": "unresolved", "errors": ["empty_decoded_condition"]}
    if len(conditions) == 1:
        return conditions[0]
    return {"type": "all", "source": "decoded_conditions", "conditions": conditions}


def _collect_semantic_condition_zone_effects(record: dict[str, Any], *, root_lookup_cache: dict | None = None) -> list[dict[str, Any]]:
    buff_id = _normalize_buff_id(record.get("event_key"))
    root = _load_current_buff_roots().get(buff_id)
    record.pop('rdps_unresolved_semantic_condition', None)
    record.pop('rdps_unapproved_typed_branches', None)
    if not isinstance(root, dict):
        record['rdps_unresolved_semantic_condition'] = ['missing_current_buff_root']
        return []
    bb_values = {str(key): value for key, value in (record.get("bb_values") or {}).items()}
    effects: list[dict[str, Any]] = []
    modifiers = root.get('damageModifier')
    if not isinstance(modifiers, list):
        record['rdps_unresolved_semantic_condition'] = ['unspecified_damage_modifiers']
        return []
    declared = []
    for group_index, modifier in enumerate(modifiers):
        if not isinstance(modifier, dict) or not isinstance(modifier.get('damageProcessors'), list):
            record['rdps_unresolved_semantic_condition'] = ['unspecified_damage_processors']
            return []
        if modifier.get('enableSide') not in {'Attacker', 'Defender'}:
            record['rdps_unresolved_semantic_condition'] = ['unsupported_modifier_entry_side']
            return []
        for processor in modifier['damageProcessors']:
            if not isinstance(processor, dict):
                record['rdps_unresolved_semantic_condition'] = ['unsupported_damage_processor_or_side']
                return []
            processor_type = processor.get('$type')
            if processor_type == 'Beyond.Gameplay.Core.DamageScaleProcessor':
                if processor.get('side') not in {'Attacker', 'Defender'}:
                    record['rdps_unresolved_semantic_condition'] = ['unsupported_damage_processor_or_side']
                    return []
                declared.append({**processor, 'groupIndex': group_index, 'processorType': 'DamageScaleProcessor',
                                 'enableSide': modifier['enableSide']})
                continue
            # InstantModifyAttribute is the native processor used by
            # resistance-ignore windows. Only accept the fully typed fire
            # resistance form; other attributes remain unknown.
            if processor_type == 'Beyond.Gameplay.Core.InstantModifyAttribute':
                typed_modifier = processor.get('modifier')
                if (not isinstance(typed_modifier, dict)
                        or typed_modifier.get('attributeType') != 'FireResistance'
                        or typed_modifier.get('formulaItem') != 'BaseAddition'
                        or typed_modifier.get('modifyAttributeType') != 'Specific'
                        or processor.get('modifyTargetSide') != 'Defender'):
                    record['rdps_unresolved_semantic_condition'] = ['unsupported_damage_processor_or_side']
                    return []
                declared.append({
                    'groupIndex': group_index,
                    'processorType': 'InstantModifyAttribute',
                    'side': 'Defender',
                    'enableSide': modifier['enableSide'],
                    'addition': typed_modifier.get('param') or {},
                    'attributeType': 'FireResistance',
                })
                continue
            record['rdps_unresolved_semantic_condition'] = ['unsupported_damage_processor_or_side']
            return []
    approval = _rdps_registry_candidate_effect_entry(record, root_lookup_cache=root_lookup_cache)
    approved_specs = (approval.get('effects') or []) if isinstance(approval, dict) else []
    for damage_effect in declared:
        # A newly added typed branch cannot expand an existing element-specific
        # whitelist. An enabled AND condition with disjoint damage types proves
        # it is outside that approval, even when another action in the branch
        # is not supported. Preserve the boundary without guessing that action.
        decoded = _decoded_damage_modifier_condition_rows(buff_id).get(damage_effect['groupIndex']) or {}
        addition = damage_effect.get('addition') if isinstance(damage_effect.get('addition'), dict) else {}
        bb_key = addition.get('blackboardKey') if addition.get('useBlackboardKey') is True else None
        scopes = [spec.get('element', 'all') for spec in approved_specs if isinstance(spec, dict)
                  and bb_key and bb_key in {spec.get('bb_key'), *(spec.get('bb_keys') or [])}]
        if (scopes and all(scope in {'physical', 'spell', 'fire', 'pulse', 'cryst', 'natural',
                                     'real', 'lifedrain', 'ether'} for scope in scopes)
                and 'unresolved_source_guard' not in decoded.get('errors', [])):
            allowed = set().union(*({'fire', 'pulse', 'cryst', 'natural'} if scope == 'spell' else {scope}
                                    for scope in scopes))
            restrictions = [set(condition['elements']) for condition in decoded.get('conditions') or []
                            if condition.get('type') == 'damage_type_in' and condition.get('elements')]
            if any(not allowed.intersection(elements) for elements in restrictions):
                record.setdefault('rdps_unapproved_typed_branches', []).append({
                    'group_index': damage_effect['groupIndex'], 'bb_key': bb_key,
                    'reason': 'typed_damage_types_disjoint_from_whitelist',
                    'whitelist_elements': sorted(allowed),
                    'typed_damage_types': sorted(set.union(*restrictions)),
                    'unsupported_conditions': list(decoded.get('errors') or []),
                })
                continue
        condition = _semantic_damage_effect_condition(
            buff_id,
            damage_effect,
            defaults={},
            bb_values=bb_values,
        )
        if condition is None:
            continue
        if condition.get("type") == "unresolved":
            record["rdps_unresolved_semantic_condition"] = condition.get("errors") or ["unresolved_condition"]
            continue
        if damage_effect.get('processorType') == 'InstantModifyAttribute':
            zone_element = ('res', 'fire')
        else:
            zone_element = _semantic_damage_effect_zone(damage_effect)
        if zone_element is None:
            record['rdps_unresolved_semantic_condition'] = ['unsupported_damage_zone']
            continue
        zone, element = zone_element
        addition = damage_effect.get("addition") if isinstance(damage_effect.get("addition"), dict) else {}
        bb_key = str(addition.get("blackboardKey") or "")
        use_key = addition.get('useBlackboardKey')
        rate = (_coerce_optional_float(bb_values.get(bb_key)) if use_key is True and bb_key else
                _coerce_optional_float(addition.get('value')) if use_key is False else None)
        if damage_effect.get('processorType') == 'InstantModifyAttribute' and rate is not None:
            # FireResistance BaseAddition is encoded as a signed decrease
            # (e.g. -20); the rDPS res lane stores the corresponding positive
            # amount removed from resistance.
            rate = -rate
        if rate is None or rate < 0:
            record["rdps_unresolved_semantic_condition"] = ["missing_or_invalid_effect_rate"]
            continue
        condition_elements = _condition_damage_type_elements(condition)
        if element == "all" and len(condition_elements) == 1:
            element = next(iter(condition_elements))
        effect = {
            "zone": zone,
            "element": _normalize_effect_element(element),
            "rate": rate,
            "condition": condition,
            "semantic_source": "typed_current_damageModifier",
            'recipient_side': damage_effect['enableSide'].lower(),
        }
        if zone.startswith('prod_'):
            effect['multiplicative'] = True
        if bb_key:
            effect["bb_key"] = bb_key
        effects.append(effect)
    attributes = root.get('attributeModifier')
    if isinstance(attributes, dict):
        typed_attribute_types = {
            'Atk': (2, 'BaseMultiplier'),
            'NormalAttackDamageIncrease': (17, 'BaseAddition'),
            'UltimateSkillDamageIncrease': (28, 'BaseAddition'),
            'NormalSkillDamageIncrease': (32, 'BaseAddition'),
            'ComboSkillDamageIncrease': (33, 'BaseAddition'),
            'FireDamageIncrease': (51, 'BaseAddition'),
            'PhysicalDamageIncrease': (50, 'BaseAddition'),
            'PulseDamageIncrease': (52, 'BaseAddition'),
            'CrystDamageIncrease': (53, 'BaseAddition'),
            'NaturalDamageIncrease': (54, 'BaseAddition'),
            'PulseEnhancedDmgIncrease': (66, 'BaseAddition'),
            'FireEnhancedDmgIncrease': (65, 'BaseAddition'),
            'CrystEnhancedDmgIncrease': (67, 'BaseAddition'),
            'NaturalEnhancedDmgIncrease': (68, 'BaseAddition'),
            'PhysicalAndSpellInflictionEnhance': (87, 'BaseAddition'),
        }
        typed_vulnerability_types = {
            'PhysicalVulnerableDmgIncrease': ('fragile', 'physical'),
            'FireVulnerableDmgIncrease': ('fragile', 'fire'),
            'PulseVulnerableDmgIncrease': ('fragile', 'pulse'),
            'CrystVulnerableDmgIncrease': ('fragile', 'cryst'),
            'NaturalVulnerableDmgIncrease': ('fragile', 'natural'),
        }
        for modifier in attributes.get('attributeModifiers') or []:
            attr_spec = typed_attribute_types.get(modifier.get('attributeType')) if isinstance(modifier, dict) else None
            vulnerability_zone = (typed_vulnerability_types.get(modifier.get('attributeType'))
                                  if isinstance(modifier, dict) else None)
            if attr_spec is None:
                if vulnerability_zone is None:
                    continue
                attr_type, required_formula = None, 'BaseAddition'
            else:
                attr_type, required_formula = attr_spec
            if (type(attributes.get('isConvertedAttribute')) is not bool
                    or modifier.get('formulaItem') != required_formula
                    or modifier.get('modifyAttributeType') != 'Specific'):
                record['rdps_unresolved_semantic_condition'] = ['unsupported_conditional_attribute_formula']
                return []
            param = modifier.get('param') or {}
            bb_key = str(param.get('blackboardKey') or '')
            use_key = param.get('useBlackboardKey')
            rate = (_coerce_optional_float(bb_values.get(bb_key)) if use_key is True and bb_key else
                    _coerce_optional_float(param.get('value')) if use_key is False else None)
            if rate is None or rate < 0:
                record['rdps_unresolved_semantic_condition'] = ['missing_or_invalid_attribute_rate']
                return []
            zone, element = vulnerability_zone or (('arts_strength', 'all') if attr_type == 87 else _ATTR_TYPE_TO_EFFECT[attr_type])
            condition = ({'type': 'damage_decorate_mask', 'check': 'HasAny',
                          'mask': _ATTR_TYPE_DAMAGE_MASK[attr_type],
                          'source': 'DamagePackData.GetAttackerDamageScaleIncreaseFromSkillType'}
                         if attr_type in _ATTR_TYPE_DAMAGE_MASK else None)
            effects.append({'zone': zone, 'element': element, 'rate': rate, 'attr_type': attr_type,
                            'bb_key': bb_key if use_key is True else '',
                            **({'condition': condition} if condition is not None else {}),
                            'semantic_source': 'typed_current_attributeModifier'})
            if attributes['isConvertedAttribute']:
                # Full Attributes.GetValue uses FilerTypeMask.All (-1),
                # which includes Converted. Read the observed BB operand;
                # do not reconstruct the conversion that generated it.
                effects[-1]['converted_attribute'] = True
    return effects


def _collect_zone_effects(record: dict[str, Any], *, root_lookup_cache: dict | None = None) -> list[dict[str, Any]]:
    if record.get('rdps_live_identity_conflict'):
        record['rdps_unresolved_semantic_condition'] = ['conflicting_live_buff_identity']
        return []
    buff_id = _normalize_buff_id(record.get("event_key"))
    verified_entry = _rdps_registry_verified_effect_entry(record, root_lookup_cache=root_lookup_cache)
    if verified_entry is None and _rdps_registry_known_non_rdps_entry(record) is not None:
        return []
    if verified_entry is None and _rdps_registry_suppresses_zone_effects(record):
        record.pop('rdps_unresolved_semantic_condition', None)
        return []
    if verified_entry is None:
        # Current typed effects can describe the attacker's own baseline;
        # they do not authorize external rDPS outside the approved whitelist.
        effects = _collect_semantic_condition_zone_effects(record, root_lookup_cache=root_lookup_cache)
        for effect in effects:
            effect['external_rdps_allowed'] = False
            effect['rdps_whitelist_keys'] = []
        if not effects and not record.get('rdps_unresolved_semantic_condition'):
            record['rdps_unresolved_semantic_condition'] = ['missing_verified_semantics']
        return effects
    effects = _rdps_registry_zone_effects(record, verified_entry)
    semantic_effects = _collect_semantic_condition_zone_effects(record, root_lookup_cache=root_lookup_cache)
    if record.get("rdps_unresolved_semantic_condition"):
        return []
    if effects and not semantic_effects:
        # A legacy registry effect without a corresponding current typed
        # modifier is not evidence that the effect exists in this build.
        record['rdps_unresolved_semantic_condition'] = ['missing_current_semantic_effect']
        return []
    verified_effects = []
    registry_specs_by_current_index: dict[int, list[dict[str, Any]]] = {}
    for effect in effects:
        matched = [(index, semantic) for index, semantic in enumerate(semantic_effects)
                   if effect.get('zone') == semantic.get('zone')
                   and str(semantic.get('bb_key') or '') in (effect.get('_registry_bb_keys')
                        or [str(effect.get('_registry_bb_key') or '')])]
        if matched:
            if any(semantic.get('rate') != effect.get('rate') for _, semantic in matched):
                record['rdps_unresolved_semantic_condition'] = ['conflicting_declared_rates']
                return []
            # The registry authorizes the effect; the current complete root
            # defines the actual branches/conditions, not a legacy name rule.
            for index, semantic in matched:
                registry_specs_by_current_index.setdefault(index, []).append(effect)
        else:
            record['rdps_unresolved_semantic_condition'] = ['registry_effect_not_in_current_typed_root']
            return []
    for index, declared_specs in registry_specs_by_current_index.items():
        approved = dict(semantic_effects[index])
        approved['external_rdps_allowed'] = True
        approved['rdps_whitelist_keys'] = list(verified_entry.get('_rdps_whitelist_keys') or [])
        if verified_entry.get('_keyword_origin'):
            approved['keyword_origin'] = copy.deepcopy(verified_entry['_keyword_origin'])
        alternatives = []
        for spec in declared_specs:
            terms = [copy.deepcopy(spec['condition'])] if 'condition' in spec else []
            element = spec.get('element', 'all')
            if element != 'all':
                terms.append({'type': 'damage_type_in', 'source': 'rdps_whitelist',
                              'elements': ['fire', 'pulse', 'cryst', 'natural'] if element == 'spell' else [element]})
            alternatives.append({'type': 'all', 'conditions': terms} if terms else {'type': 'always'})
        # A typed branch may match several approved element rows. Preserve
        # their union without crediting the branch more than once.
        if not any(branch.get('type') == 'always' for branch in alternatives):
            restriction = {'type': 'any', 'source': 'rdps_whitelist', 'conditions': alternatives}
            approved['condition'] = ({'type': 'all', 'source': 'typed_and_whitelist',
                                      'conditions': [approved['condition'], restriction]}
                                     if 'condition' in approved else restriction)
        verified_effects.append(approved)
    effects = verified_effects
    for effect in effects:
        effect.pop("_registry_bb_key", None)
    if verified_entry.get("dynamic"):
        return []
    return effects


def _extract_dynamic_effect_specs(record: dict[str, Any], *, root_lookup_cache: dict | None = None) -> list[dict[str, Any]]:
    record.pop("rdps_unresolved_dynamic_effect", None)
    if record.get('rdps_live_identity_conflict'):
        record['rdps_unresolved_dynamic_effect'] = True
        return []
    entry = _rdps_registry_verified_effect_entry(record, root_lookup_cache=root_lookup_cache)
    if entry is None or not entry.get("dynamic") or record.get("packet_mapping_rejected"):
        return []
    if 'buff_chr_0023_antal_normal_fragile' in (entry.get('_rdps_whitelist_keys') or []):
        snapshots = record.get('keyword_rate_snapshots') or []
        if snapshots and entry.get('_keyword_origin'):
            base_record = dict(record)
            base_record.pop('keyword_rate_snapshots', None)
            effects = _collect_zone_effects(base_record, root_lookup_cache=root_lookup_cache)
            sessions = {row.get('session_id') for row in snapshots}
            if len(sessions) == 1 and None not in sessions and effects:
                return [{**effect, 'base_rate': effect['rate'],
                         'keyword_rate_snapshots': copy.deepcopy(snapshots),
                         'keyword_session_id': next(iter(sessions))}
                        for effect in effects]
        # A legacy parent label is not the live keyword effect. Current main
        # Buffs are authorized by their exact creation origin and observed rate.
        record['rdps_unresolved_dynamic_effect'] = True
        return []
    if record.get('event_key') in {'buff_common_natural_natural_corrupt_do', 'buff_common_natural_cryst_triggered'}:
        snapshots = record.get('attribute_refresh_snapshots') or record.get('resistance_parameter_snapshots') or []
        if snapshots:
            root = _load_current_buff_roots().get(record['event_key']) or {}
            attributes = root.get('attributeModifier') or {}
            resistance_types = {'PhysicalResistance': 'physical', 'NaturalResistance': 'natural',
                                'CrystResistance': 'cryst', 'PulseResistance': 'pulse',
                                'FireResistance': 'fire', 'EtherResistance': 'ether'}
            keys_by_element: dict[str, list[str]] = defaultdict(list)
            approved_keys = {item.get('bb_key') for item in entry.get('effects') or [] if item.get('zone') == 'res'}
            for modifier in attributes.get('attributeModifiers') or []:
                element = resistance_types.get(modifier.get('attributeType'))
                if element is None:
                    continue
                param = modifier.get('param') or {}
                key = param.get('blackboardKey')
                if (modifier.get('formulaItem') != 'BaseAddition' or modifier.get('modifyAttributeType') != 'Specific'
                        or param.get('useBlackboardKey') is not True or key not in approved_keys):
                    record['rdps_unresolved_dynamic_effect'] = True
                    return []
                keys_by_element[element].append(key)
            if keys_by_element:
                return [{'zone': 'res', 'element': element, 'base_rate': 0.0,
                         'parameter_keys': keys, 'parameter_snapshots': copy.deepcopy(snapshots),
                         'external_rdps_allowed': True,
                         'rdps_whitelist_keys': list(entry.get('_rdps_whitelist_keys') or [])}
                        for element, keys in keys_by_element.items()]
    hint = record.get("packet_mapping")
    if not isinstance(hint, dict):
        hint = _packet_numeric_buff_hint(record.get("raw_event_key") or record.get("event_key"))
    specs = hint.get("dynamic_effects")
    if not isinstance(specs, list) or not specs:
        specs = entry.get("dynamic_effects")
    if not isinstance(specs, list) or not specs:
        record["rdps_unresolved_dynamic_effect"] = True
        return []
    bb_values = record.get("bb_values") or {}
    allow_constant = _packet_mapping_allows_static_rate(hint)
    effects = []
    for spec in specs:
        if not isinstance(spec, dict) or spec.get("zone") not in _RDPS_ALLOCATABLE_ZONES:
            record["rdps_unresolved_dynamic_effect"] = True
            return []
        element = spec.get("element")
        if element not in {"all", "physical", "spell", "fire", "pulse", "cryst", "natural"}:
            record["rdps_unresolved_dynamic_effect"] = True
            return []
        declared = [item for item in (entry.get('dynamic_effects') or entry.get('effects') or [])
                    if item.get('zone') == spec['zone'] and item.get('element', 'all') == element]
        if not declared:
            record['rdps_unresolved_dynamic_effect'] = True
            record['rdps_whitelist_rejection'] = 'dynamic_effect_not_declared'
            return []
        effect = {"zone": spec["zone"], "element": element}
        parameters = (("base_rate", "base_bb_key", "base_bb_keys", "bb_key"),
                      ("tick_rate", "tick_bb_key", "tick_bb_keys", "tick_bb_key"),
                      ("delayed_add_rate", "add_bb_key", "", "add_bb_key"),
                      ("delay_sec", "delay_bb_key", "", "delay_bb_key"),
                      ("max_rate", "max_bb_key", "", "max_bb_key"))
        for parameter, key_field, aliases_field, provenance in parameters:
            aliases = spec.get(aliases_field) if aliases_field else []
            if aliases is not None and not isinstance(aliases, list):
                record["rdps_unresolved_dynamic_effect"] = True
                return []
            keys = list(dict.fromkeys([str(key) for key in aliases or [] if key]))
            primary = spec.get(key_field) or (spec.get("bb_key") if parameter == "base_rate" else None)
            if primary and str(primary) not in keys:
                keys.insert(0, str(primary))
            values = [(key, _coerce_optional_float(bb_values[key])) for key in keys if key in bb_values]
            if str(spec.get("zone") or "") == "res":
                values = [(key, -value if value is not None and value < 0 else value)
                          for key, value in values]
            if values:
                if any(value is None or value < 0 for _, value in values) or len({value for _, value in values}) != 1:
                    record["rdps_unresolved_dynamic_effect"] = True
                    return []
                effect[parameter] = values[0][1]
                effect[provenance] = values[0][0]
            elif parameter in spec and allow_constant:
                value = _coerce_optional_float(spec[parameter])
                if value is None or value < 0:
                    record["rdps_unresolved_dynamic_effect"] = True
                    return []
                effect[parameter] = value
            elif keys or parameter in spec:
                record["rdps_unresolved_dynamic_effect"] = True
                return []
            else:
                # The declared formula has no such term: its additive identity is zero.
                effect[parameter] = 0.0
        multiplier = _coerce_optional_float(spec.get("tick_multiplier", 1))
        if multiplier is None or multiplier < 0 or not isfinite(effect["tick_rate"] * multiplier):
            record["rdps_unresolved_dynamic_effect"] = True
            return []
        effect["tick_rate"] *= multiplier
        declared_keys = {str(item[field]) for item in declared
                         for field in ('bb_key', 'base_bb_key', 'tick_bb_key', 'add_bb_key', 'delay_bb_key', 'max_bb_key')
                         if item.get(field)}
        declared_keys.update(str(key) for item in declared
                             for field in ('bb_keys', 'base_bb_keys', 'tick_bb_keys', 'required_bb_keys')
                             for key in item.get(field) or [])
        used_keys = {str(effect[field]) for field in ('bb_key', 'tick_bb_key', 'add_bb_key', 'delay_bb_key', 'max_bb_key')
                     if effect.get(field)}
        if not used_keys <= declared_keys:
            record['rdps_unresolved_dynamic_effect'] = True
            record['rdps_whitelist_rejection'] = 'dynamic_operand_not_declared'
            return []
        for parameter, _, _, provenance in parameters:
            if parameter in spec and not effect.get(provenance):
                if not any(_coerce_optional_float(item.get(parameter)) == effect[parameter]
                           for item in declared if parameter in item):
                    record['rdps_unresolved_dynamic_effect'] = True
                    record['rdps_whitelist_rejection'] = 'dynamic_literal_not_declared'
                    return []
        # Native ModifyDynamicBlackboard reports the destination key's value
        # after assignment. That result alone does not prove the attribute
        # modifier was refreshed; bind the typed key and explicit refresh
        # before using it. Never integrate the configured tick over time.
        if effect["tick_rate"] != 0.0 or effect["delayed_add_rate"] != 0.0:
            record["rdps_unresolved_dynamic_effect"] = True
            return []
        if spec.get("dynamic_key"):
            effect["dynamic_key"] = spec["dynamic_key"]
        if "condition" in spec:
            effect["condition"] = copy.deepcopy(spec["condition"])
        if all('condition' in item for item in declared):
            restriction = {'type': 'any', 'source': 'rdps_whitelist',
                           'conditions': [copy.deepcopy(item['condition']) for item in declared]}
            effect['condition'] = ({'type': 'all', 'conditions': [effect['condition'], restriction]}
                                   if 'condition' in effect else restriction)
        effect['external_rdps_allowed'] = True
        effect['rdps_whitelist_keys'] = list(entry.get('_rdps_whitelist_keys') or [])
        effects.append(effect)
    return effects


def _window_effects_at_ts(window: dict[str, Any], hit_ts_ms: int, *,
                          line_no: int | None = None, packet_seq_id: int | None = None,
                          packet_session_id: str | None = None) -> list[dict[str, Any]]:
    window['_enable_state_unobserved_at_hit'] = False
    window['_effect_state_unresolved_at_hit'] = False
    window['_buff_enabled_at_hit'] = None
    start = window.get('start_ts_ms')
    end = window.get('end_ts_ms')
    if type(start) is int and hit_ts_ms < start or type(end) is int and hit_ts_ms > end:
        return []
    if line_no:
        if start == hit_ts_ms and int(window.get('start_line_no') or 0) > line_no:
            return []
        if end == hit_ts_ms and window.get('end_line_no') and line_no >= int(window['end_line_no']):
            return []
    elif (window.get('snapshot_update') and start == hit_ts_ms
          or window.get('snapshot_end_ts_ms') == hit_ts_ms):
        window['rdps_snapshot_order_unresolved'] = True
        window['_effect_state_unresolved_at_hit'] = True
        return []
    observations = window.get('enable_observations') or []
    if window.get('rdps_conflicting_enable_state') and not observations:
        window['_effect_state_unresolved_at_hit'] = True
        return []
    prior = []
    if observations:
        if type(packet_seq_id) is int:
            prior = [row for row in observations if row['operationSeq'] <= packet_seq_id]
            prior.sort(key=lambda row: (row['operationSeq'], row['line_no']))
        else:
            prior = [row for row in observations if row['ts_ms'] < hit_ts_ms or
                     row['ts_ms'] == hit_ts_ms and (not line_no or row['line_no'] <= line_no)]
            prior.sort(key=lambda row: (row['ts_ms'], row['line_no']))
        if prior:
            latest = prior[-1]
            values = {row['enabled'] for row in observations if row['operationSeq'] == latest['operationSeq']}
            if len(values) != 1:
                window['_effect_state_unresolved_at_hit'] = True
                return []
            if latest['enabled'] is False:
                window['_buff_enabled_at_hit'] = False
                return []
    if window.get('requires_enable_observation') and not prior:
        window['_enable_state_unobserved_at_hit'] = True
        window['_effect_state_unresolved_at_hit'] = True
        window['rdps_unresolved_enable_evidence'] = True
        return []
    window['_buff_enabled_at_hit'] = True
    dynamic_specs = list(window.get("dynamic_effects") or [])
    if not dynamic_specs:
        return list(window.get("zone_effects") or [])

    effects: list[dict[str, Any]] = []
    for spec in dynamic_specs:
        if 'keyword_rate_snapshots' in spec:
            if ((packet_session_id is not None and packet_session_id != spec.get('keyword_session_id'))
                    or type(packet_seq_id) is int and packet_session_id is None):
                window['_effect_state_unresolved_at_hit'] = True
                return []
            prior = [row for row in spec['keyword_rate_snapshots']
                     if row.get('session_id') == spec.get('keyword_session_id')
                     and ((type(packet_seq_id) is int and row['operation_seq'] <= packet_seq_id)
                          or packet_seq_id is None and (row['ts_ms'] < hit_ts_ms or
                              row['ts_ms'] == hit_ts_ms and line_no and row['line_no'] <= line_no))]
            rate = _coerce_optional_float(spec.get('base_rate'))
            if prior:
                latest = max(prior, key=lambda row: (row['operation_seq'], row['line_no']))
                rate = (_coerce_optional_float(latest.get('rate_after'))
                        if latest.get('status') == 'observed' else None)
            if rate is None or rate < 0:
                window['_effect_state_unresolved_at_hit'] = True
                return []
            if rate > 0:
                effect = {key: copy.deepcopy(spec[key]) for key in
                          ('zone', 'element', 'condition', 'external_rdps_allowed', 'rdps_whitelist_keys', 'keyword_origin')
                          if key in spec}
                effect['rate'] = rate
                effects.append(effect)
            continue
        if 'parameter_snapshots' in spec:
            prior = [row for row in spec['parameter_snapshots']
                     if (type(packet_seq_id) is int and type(row.get('operation_seq')) is int
                         and row['operation_seq'] <= packet_seq_id)
                     or (packet_seq_id is None and (row['ts_ms'] < hit_ts_ms or
                         row['ts_ms'] == hit_ts_ms and line_no and row['line_no'] <= line_no))]
            if not prior:
                window['_effect_state_unresolved_at_hit'] = True
                return []
            snapshot = max(prior, key=lambda row: (row['operation_seq'], row['line_no']))
            snapshot_session = snapshot.get('session_id') or (snapshot.get('identity') or {}).get('sessionId')
            if packet_session_id is not None and snapshot_session is not None and packet_session_id != snapshot_session:
                window['_effect_state_unresolved_at_hit'] = True
                return []
            values = [_coerce_optional_float((snapshot.get('bb_values') or {}).get(key))
                      for key in spec['parameter_keys']]
            if (snapshot.get('status') != 'attribute_parameters_observed'
                    or not values or any(value is None or value > 0 for value in values)):
                window['_effect_state_unresolved_at_hit'] = True
                return []
            rate = -sum(values)
            if not isfinite(rate):
                window['_effect_state_unresolved_at_hit'] = True
                return []
            if rate > 0:
                effects.append({'zone': 'res', 'element': spec['element'], 'rate': rate,
                                'external_rdps_allowed': spec['external_rdps_allowed'],
                                'rdps_whitelist_keys': list(spec['rdps_whitelist_keys']),
                                'resistance_point_source': 'packet_bb_update_and_refresh',
                                'resistance_snapshot_seq': snapshot['operation_seq']})
            continue
        if (spec.get("observed_points")
                and spec.get("observed_points_source") != "typed_runtime_dynamic_action"):
            window["rdps_invalid_dynamic_evaluation"] = True
            return []
        if float(spec.get("tick_rate") or 0.0) != 0.0:
            window["rdps_invalid_dynamic_evaluation"] = True
            return []
        if float(spec.get("delayed_add_rate") or 0.0) != 0.0:
            window["rdps_invalid_dynamic_evaluation"] = True
            return []
        rate = float(spec.get("base_rate") or 0.0)
        if spec.get("observed_points"):
            earlier = [point for point in spec["observed_points"]
                       if isinstance(point, (list, tuple)) and len(point) == 2
                       and int(point[0]) <= hit_ts_ms]
            if not earlier:
                continue
            rate = max(0.0, float(earlier[-1][1]))
        if not isfinite(rate):
            window["rdps_invalid_dynamic_evaluation"] = True
            return []
        if rate <= 0:
            continue
        effect = {
                "zone": str(spec["zone"]),
                "element": _normalize_effect_element(spec.get("element")),
                "rate": rate,
            }
        if "condition" in spec:
            effect["condition"] = copy.deepcopy(spec["condition"])
        for key in ('external_rdps_allowed', 'rdps_whitelist_keys'):
            if key in spec:
                effect[key] = copy.deepcopy(spec[key])
        effects.append(effect)
    return effects


def _effect_summary_rows(effects: list[dict[str, Any]]) -> list[str]:
    rows: list[str] = []
    seen: set[str] = set()
    for effect in effects:
        zone = str(effect.get("zone") or "")
        element = _normalize_effect_element(effect.get("element"))
        rate = effect.get("rate")
        if rate is None:
            rate = effect.get("base_rate")
        try:
            base_rate = float(rate)
        except (TypeError, ValueError):
            base_rate = None
        label = _EFFECT_ZONE_LABELS.get(zone, zone)
        if 'keyword_rate_snapshots' in effect:
            rates = [base_rate]
            for snapshot in sorted(effect['keyword_rate_snapshots'],
                                   key=lambda row: (row['operation_seq'], row['line_no'])):
                if snapshot.get('session_id') != effect.get('keyword_session_id'):
                    continue
                snapshot_rate = (_coerce_optional_float(snapshot.get('rate_after'))
                                 if snapshot.get('status') == 'observed' else None)
                if snapshot_rate != rates[-1]:
                    rates.append(snapshot_rate)
            rate_text = ' -> '.join(f'{value * 100:.2f}%' if value is not None else '未确认'
                                    for value in rates)
            row_text = f"{label}/{element} {rate_text}"
        elif zone == 'res' and 'parameter_snapshots' in effect:
            row_text = f"{label}/{element} 按包内实际减抗点数更新"
        elif zone == 'res' and base_rate is not None:
            row_text = f"{label}/{element} {base_rate:g} 点"
        elif base_rate is None:
            row_text = f"{label}/{element} -"
        else:
            delayed_add_rate = _safe_positive_rate(effect.get("delayed_add_rate")) or 0.0
            delay_sec = float(effect.get("delay_sec") or 0.0)
            tick_rate = float(effect.get("tick_rate") or 0.0)
            max_rate = float(effect.get("max_rate") or 0.0)
            if delayed_add_rate > 0 and delay_sec > 0:
                row_text = (
                    f"{label}/{element} {base_rate * 100:.2f}%"
                    f" -> {(base_rate + delayed_add_rate) * 100:.2f}% @ {delay_sec:g}s"
                )
            elif tick_rate > 0 and max_rate > base_rate:
                row_text = (
                    f"{label}/{element} {base_rate * 100:.2f}%"
                    f" -> {max_rate * 100:.2f}% over time"
                )
            else:
                row_text = f"{label}/{element} {base_rate * 100:.2f}%"
        condition = effect.get("condition")
        if isinstance(condition, dict) and str(condition.get("type") or "") in {"target_hp_ratio_lte", "target_hp_compare"}:
            threshold = _coerce_optional_float(condition.get("threshold"))
            if threshold is not None:
                legacy = condition.get("type") == "target_hp_ratio_lte"
                compare = "LE" if legacy else condition.get("compare")
                operator = {"LT": "<", "LE": "<=", "GT": ">", "GE": ">=", "EQ": "=", "NE": "!="}.get(compare, "?")
                value = f"{threshold * 100:.2f}%" if legacy or condition.get("is_ratio") is True else str(threshold)
                row_text = f"{row_text} (target HP {operator} {value})"
        elif isinstance(condition, dict) and str(condition.get("type") or "") == "damage_type_in":
            elements = [str(item) for item in condition.get("elements") or [] if str(item or "")]
            if elements:
                row_text = f"{row_text} (damage type: {', '.join(elements)})"
        if row_text in seen:
            continue
        seen.add(row_text)
        rows.append(row_text)
    return rows


def _effect_segments_for_event(
    *,
    start_ts_ms: int,
    end_ts_ms: int,
    zone_effects: list[dict[str, Any]],
    dynamic_effects: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    for effect in zone_effects:
        segments.append(
            {
                "start_ts_ms": start_ts_ms,
                "start_time": _ms_time_text(start_ts_ms),
                "end_ts_ms": end_ts_ms,
                "end_time": _ms_time_text(end_ts_ms),
                "zone": str(effect.get("zone") or ""),
                "element": _normalize_effect_element(effect.get("element")),
                "rate": effect.get("rate"),
                "mode": "static",
            }
        )
    for effect in dynamic_effects:
        if 'keyword_rate_snapshots' in effect:
            states = [{'ts_ms': start_ts_ms, 'rate_after': effect.get('base_rate'),
                       'status': 'observed', 'operation_seq': None, 'line_no': None}]
            latest_seq = -1
            for snapshot in sorted(effect['keyword_rate_snapshots'],
                                   key=lambda row: (row['ts_ms'], row['line_no'], row['operation_seq'])):
                if (snapshot.get('session_id') != effect.get('keyword_session_id')
                        or snapshot['ts_ms'] > end_ts_ms
                        or snapshot['operation_seq'] < latest_seq):
                    continue
                latest_seq = snapshot['operation_seq']
                states.append(snapshot)
            for index, state in enumerate(states):
                following = states[index + 1] if index + 1 < len(states) else None
                segment_start = max(start_ts_ms, state['ts_ms'])
                segment_end = min(end_ts_ms, following['ts_ms'] if following else end_ts_ms)
                if segment_end < segment_start:
                    continue
                rate = (_coerce_optional_float(state.get('rate_after'))
                        if state.get('status') == 'observed' else None)
                # Equal timestamps still have an explicit packet/line boundary;
                # retain it instead of making the earlier operation disappear.
                segments.append({'start_ts_ms': segment_start, 'start_time': _ms_time_text(segment_start),
                                 'end_ts_ms': segment_end, 'end_time': _ms_time_text(segment_end),
                                 'zone': str(effect.get('zone') or ''),
                                 'element': _normalize_effect_element(effect.get('element')),
                                 'rate': rate, 'mode': 'packet_keyword_update',
                                 'operation_seq': state['operation_seq'], 'line_no': state['line_no'],
                                 'end_operation_seq': following['operation_seq'] if following else None,
                                 'end_line_no': following['line_no'] if following else None,
                                 'session_id': effect.get('keyword_session_id')})
            continue
        if 'parameter_snapshots' in effect:
            snapshots = effect['parameter_snapshots']
            for index, snapshot in enumerate(snapshots):
                values = [_coerce_optional_float((snapshot.get('bb_values') or {}).get(key))
                          for key in effect['parameter_keys']]
                resolved = (snapshot.get('status') == 'attribute_parameters_observed'
                            and values and all(value is not None and value <= 0 for value in values))
                start = max(start_ts_ms, snapshot['ts_ms'])
                end = min(end_ts_ms, snapshots[index + 1]['ts_ms'] if index + 1 < len(snapshots) else end_ts_ms)
                if end >= start:
                    segments.append({'start_ts_ms': start, 'start_time': _ms_time_text(start),
                                     'end_ts_ms': end, 'end_time': _ms_time_text(end),
                                     'zone': 'res', 'element': effect['element'],
                                     'rate': -sum(values) if resolved else None,
                                     'rate_unit': 'resistance_points', 'mode': 'packet_refresh',
                                     'operation_seq': snapshot['operation_seq']})
            continue
        base_rate = float(effect.get("base_rate") or 0.0)
        delayed_add_rate = float(effect.get("delayed_add_rate") or 0.0)
        delay_ms = int(max(0.0, float(effect.get("delay_sec") or 0.0)) * 1000)
        tick_rate = float(effect.get("tick_rate") or 0.0)
        max_rate = float(effect.get("max_rate") or 0.0)
        zone = str(effect.get("zone") or "")
        element = _normalize_effect_element(effect.get("element"))
        observed_points = [
            (int(point[0]), float(point[1]))
            for point in (effect.get("observed_points") or [])
            if isinstance(point, (list, tuple)) and len(point) == 2
        ]
        if observed_points:
            for index, (point_ts, point_rate) in enumerate(observed_points):
                segment_start = max(start_ts_ms, point_ts)
                segment_end = (observed_points[index + 1][0]
                               if index + 1 < len(observed_points)
                               else end_ts_ms)
                if segment_end <= segment_start:
                    continue
                segments.append({
                    "start_ts_ms": segment_start,
                    "start_time": _ms_time_text(segment_start),
                    "end_ts_ms": min(segment_end, end_ts_ms),
                    "end_time": _ms_time_text(min(segment_end, end_ts_ms)),
                    "zone": zone,
                    "element": element,
                    "rate": point_rate,
                    "mode": "dynamic_observed",
                })
            continue
        if delayed_add_rate > 0 and delay_ms > 0 and start_ts_ms + delay_ms < end_ts_ms:
            segments.append(
                {
                    "start_ts_ms": start_ts_ms,
                    "start_time": _ms_time_text(start_ts_ms),
                    "end_ts_ms": start_ts_ms + delay_ms,
                    "end_time": _ms_time_text(start_ts_ms + delay_ms),
                    "zone": zone,
                    "element": element,
                    "rate": base_rate,
                    "mode": "dynamic_before_delay",
                }
            )
            segments.append(
                {
                    "start_ts_ms": start_ts_ms + delay_ms,
                    "start_time": _ms_time_text(start_ts_ms + delay_ms),
                    "end_ts_ms": end_ts_ms,
                    "end_time": _ms_time_text(end_ts_ms),
                    "zone": zone,
                    "element": element,
                    "rate": base_rate + delayed_add_rate,
                    "mode": "dynamic_after_delay",
                }
            )
            continue
        segments.append(
            {
                "start_ts_ms": start_ts_ms,
                "start_time": _ms_time_text(start_ts_ms),
                "end_ts_ms": end_ts_ms,
                "end_time": _ms_time_text(end_ts_ms),
                "zone": zone,
                "element": element,
                "rate": base_rate,
                "tick_rate_per_sec": tick_rate,
                "max_rate": max_rate,
                "mode": "dynamic",
            }
        )
    return segments


def _buff_window_identity(window: dict[str, Any]) -> tuple[Any, ...]:
    uid = str(window.get("uid") or "")
    if uid:
        return ("uid", uid)
    return (
        "tuple",
        window.get("event_key"),
        window.get("source_character_key"),
        window.get("target_character_key"),
        int(window.get("start_ts_ms") or 0),
    )


def _build_buff_events(
    buff_starts: list[dict[str, Any]],
    buff_windows: list[dict[str, Any]],
    *,
    battle_start_ms: int,
    battle_end_ms: int,
    root_lookup_cache: dict | None = None,
) -> list[dict[str, Any]]:
    included_identities = {_buff_window_identity(window) for window in buff_windows}
    events: list[dict[str, Any]] = []
    for record in buff_starts:
        start_ts_ms = int(record.get("ts_ms") or 0)
        end_ts_ms = _buff_record_end_ts_ms(record, battle_end_ms)
        duration_ms = max(0, end_ts_ms - start_ts_ms)
        labels = _collect_buff_labels(record, root_lookup_cache=root_lookup_cache)
        zone_effects = _collect_zone_effects(record, root_lookup_cache=root_lookup_cache)
        dynamic_effects = _extract_dynamic_effect_specs(record, root_lookup_cache=root_lookup_cache)
        has_effects = bool(zone_effects or dynamic_effects)
        identity = _buff_window_identity(
            {
                "uid": record.get("uid"),
                "event_key": record.get("event_key"),
                "source_character_key": record.get("source_character_key"),
                "target_character_key": record.get("target_character_key") or record.get("target_enemy_key"),
                "start_ts_ms": start_ts_ms,
            }
        )
        included = has_effects and identity in included_identities
        status = "included" if included else "merged" if has_effects else "filtered"
        packet_classification = _classify_packet_buff_record(record)
        events.append(
            {
                "status": status,
                "status_label": "纳入" if included else "合并/未参与窗口" if has_effects else "过滤",
                "line_no": record.get("line_no"),
                "uid": record.get("uid"),
                "event_key": record.get("event_key"),
                "raw_event_key": record.get("raw_event_key"),
                "event_name": " / ".join(labels) if labels else record.get("event_key"),
                "packet_mapping": record.get("packet_mapping"),
                "origin_observations": copy.deepcopy(record.get('origin_observations') or []),
                "enable_observations": copy.deepcopy(record.get('enable_observations') or []),
                "creation_observations": copy.deepcopy(record.get('creation_observations') or []),
                "repeated_creation_observations": copy.deepcopy(record.get('repeated_creation_observations') or []),
                "attribute_refresh_snapshots": copy.deepcopy(record.get('attribute_refresh_snapshots') or []),
                "packet_classification": packet_classification,
                "semantic_candidates": _packet_buff_semantic_candidates(record),
                "source_character_key": record.get("source_character_key"),
                "source_character_name": record.get("source_character_name"),
                "source_skill_key": record.get("source_skill_key"),
                "source_skill_family_key": record.get("source_skill_family_key"),
                "raw_source": record.get("raw_source"),
                "target_character_key": record.get("target_character_key") or record.get("target_enemy_key"),
                "target_character_name": record.get("target_character_name") or record.get("target_enemy_name"),
                "target_player_key": record.get("target_character_key"),
                "target_enemy_key": record.get("target_enemy_key"),
                "owner_raw": record.get("owner_raw"),
                "target_actor_id": record.get("target_actor_id"),
                "source_actor_id": record.get("source_actor_id"),
                "source_weapon_template": record.get("source_weapon_template"),
                "start_ts_ms": start_ts_ms,
                "start_time": _ms_time_text(start_ts_ms),
                "end_ts_ms": end_ts_ms,
                "end_time": _ms_time_text(end_ts_ms),
                "start_ms_from_battle": start_ts_ms - battle_start_ms,
                "end_ms_from_battle": end_ts_ms - battle_start_ms,
                "duration_ms": duration_ms,
                "raw_end_ts_ms": record.get("end_ts_ms"),
                "snapshot_end_ts_ms": record.get('snapshot_end_ts_ms'),
                "snapshot_end_line_no": record.get('snapshot_end_line_no'),
                "snapshot_update": bool(record.get('snapshot_update')),
                "instance_start_line_no": record.get('instance_start_line_no'),
                "raw_end_time": _ms_time_text(record.get("end_ts_ms")) if record.get("end_ts_ms") is not None else None,
                "raw_duration_ms": record.get("raw_duration_ms"),
                "lifetime_mode": record.get('lifetime_mode'),
                "lifetime_source": record.get('lifetime_source'),
                "end_is_observation_boundary": record.get('end_ts_ms') is None and record.get('snapshot_end_ts_ms') is None and record.get('lifetime_mode') == 'Infinity',
                "end_source": ('snapshot_update' if record.get('snapshot_end_ts_ms') == end_ts_ms else
                               'buff_end' if record.get('end_ts_ms') == end_ts_ms else 'duration_or_capture_boundary'),
                "overlaps_battle": end_ts_ms >= battle_start_ms and start_ts_ms <= battle_end_ms,
                "dynamic_bb_events": list(record.get("dynamic_bb_events") or []),
                "bb_keys": sorted(set(str(key) for key in record.get("bb_keys") or [])),
                "bb_values": dict(record.get("bb_values") or {}),
                "attr_types": list(record.get("attr_types") or []),
                "attr_mods": list(record.get("attr_mods") or []),
                "zone_effects": zone_effects,
                "dynamic_effects": dynamic_effects,
                "effect_segments": _effect_segments_for_event(
                    start_ts_ms=start_ts_ms,
                    end_ts_ms=end_ts_ms,
                    zone_effects=zone_effects,
                    dynamic_effects=dynamic_effects,
                ),
                "effect_summary": _effect_summary_rows(zone_effects + dynamic_effects),
                "is_weapon_buff": bool(record.get("is_weapon_buff")),
            }
        )
    return sorted(events, key=lambda item: (int(item.get("start_ts_ms") or 0), int(item.get("line_no") or 0)))


def _build_buff_record_index(buff_starts: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = {}
    for record in buff_starts:
        uid = str(record.get("uid") or "")
        if uid:
            index.setdefault(uid, []).append(record)
    return index


def _record_could_affect_external_rdps(record: dict[str, Any]) -> bool:
    source_key = str(record.get("source_character_key") or "")
    if not source_key.startswith("chr_"):
        return bool(record.get("target_enemy_key") or record.get("target_character_key"))
    target_enemy_key = str(record.get("target_enemy_key") or "")
    if target_enemy_key.startswith("eny_"):
        return True
    target_character_key = str(record.get("target_character_key") or "")
    return bool(target_character_key.startswith("chr_") and target_character_key != source_key)


def _record_overlaps_window(record: dict[str, Any], *, battle_start_ms: int, battle_end_ms: int) -> bool:
    start_ts_ms = int(record.get("ts_ms") or 0)
    if record.get('snapshot_end_ts_ms') is not None and int(record['snapshot_end_ts_ms']) < battle_start_ms:
        return False
    if record.get("end_ts_ms") is None and record.get("raw_duration_ms") is None:
        return start_ts_ms <= battle_end_ms
    duration_ms = _normalize_buff_duration_ms(
        start_ts_ms,
        event_key=record.get("event_key"),
        end_ts_ms=record.get("end_ts_ms"),
        raw_duration_ms=record.get("raw_duration_ms"),
        battle_end_ms=battle_end_ms,
    )
    end_ts_ms = start_ts_ms + duration_ms
    return end_ts_ms >= battle_start_ms and start_ts_ms <= battle_end_ms


def _known_non_rdps_buff_record(record: dict[str, Any]) -> bool:
    # A name, classifier label, or lack of decoded effects is not proof.
    return _rdps_registry_known_non_rdps_entry(record) is not None


def _build_rdps_preflight(
    buff_starts: list[dict[str, Any]],
    buff_windows: list[dict[str, Any]],
    *,
    hits: list[dict[str, Any]] | None = None,
    unresolved_hits: list[dict[str, Any]] | None = None,
    unresolved_buff_actions: list[dict[str, Any]] | None = None,
    battle_start_ms: int,
    battle_end_ms: int,
    active_character_keys: set[str] | None = None,
    root_lookup_cache: dict | None = None,
) -> dict[str, Any]:
    included_uids: set[str] = set()
    for window in buff_windows:
        included_uids.update(_window_uid_aliases(window))
    blockers: list[dict[str, Any]] = []
    for row in unresolved_buff_actions or []:
        if row.get("uid_observation_only"):
            continue
        if _record_overlaps_window(row, battle_start_ms=battle_start_ms, battle_end_ms=battle_end_ms):
            blockers.append(dict(row))
    for window in buff_windows:
        if window.get("rdps_invalid_dynamic_evaluation"):
            blockers.append({"uid": window.get("uid"), "reason": "invalid_dynamic_evaluation"})
        if window.get('rdps_unresolved_stack_evidence'):
            blockers.append({'uid': window.get('uid'), 'event_key': window.get('event_key'),
                             'reason': 'unresolved_stack_evidence'})
        if window.get('rdps_unresolved_enable_evidence'):
            blockers.append({'uid': window.get('uid'), 'event_key': window.get('event_key'),
                             'reason': 'unobserved_initial_buff_enable_state'})
        if window.get('rdps_snapshot_order_unresolved'):
            blockers.append({'uid': window.get('uid'), 'reason': 'unresolved_buff_snapshot_order'})
    for row in unresolved_hits or []:
        if battle_start_ms <= int(row.get("ts_ms") or 0) <= battle_end_ms:
            blockers.append(dict(row))
    for hit in hits or []:
        missing = []
        dpd = hit.get('dpd_raw')
        if isinstance(dpd, dict) and any(
                value is None for side in ('atk_zones', 'def_zones') for value in dpd.get(side) or []):
            blockers.append({'seq': hit.get('seq'), 'reason': 'invalid_dpd_bucket_evidence'})
        if not hit.get("damage_element") or hit.get("damage_element_source") != "official_action_unit":
            missing.append("damage_action_unit")
        if not hit.get("damage_school"):
            missing.append("damage_school")
        if not hit.get("target_enemy_key") or hit.get("target_enemy_key") == UNKNOWN_ENEMY_KEY:
            missing.append("target_identity")
        if hit.get("packet_hit_value") is None:
            missing.append("packet_hp_loss")
        if missing:
            blockers.append({"seq": hit.get("seq"), "line_no": hit.get("line_no"),
                             "reason": "unresolved_hit_evidence", "missing": missing})
        if hit.get("rdps_unresolved_effect_conditions"):
            blockers.append({"seq": hit.get("seq"), "line_no": hit.get("line_no"),
                             "reason": "unresolved_effect_condition"})
        if hit.get('rdps_unresolved_whitelist_effect'):
            blockers.append({'seq': hit.get('seq'), 'reason': 'external_effect_not_whitelisted'})
        if hit.get('rdps_unresolved_static_multiplier_sources'):
            blockers.append({'seq': hit.get('seq'), 'reason': 'unresolved_static_multiplier_source',
                             'sources': hit['rdps_unresolved_static_multiplier_sources']})
        if hit.get("rdps_unresolved_target_binding"):
            blockers.append({"seq": hit.get("seq"), "reason": "unresolved_effect_target_instance"})
        if hit.get("rdps_unresolved_attacker_binding"):
            blockers.append({"seq": hit.get("seq"), "reason": "unresolved_attacker_stats_binding"})
        if hit.get("rdps_unresolved_dpd_residual"):
            blockers.append({
                "seq": hit.get("seq"),
                "line_no": hit.get("line_no"),
                "reason": "DPD residual has no authoritative attribution",
            })
    checked_external = 0
    accepted_effects = 0
    accepted_non_rdps = 0
    for record in buff_starts:
        if record.get("uid_observation_only"):
            continue
        if not _record_overlaps_window(record, battle_start_ms=battle_start_ms, battle_end_ms=battle_end_ms):
            continue
        proven_non_rdps = _rdps_registry_known_non_rdps_entry(record)
        if (isinstance(proven_non_rdps, dict)
                and proven_non_rdps.get("proof_kind") == "typed_root_structural"):
            record.pop("rdps_unresolved_semantic_condition", None)
            accepted_non_rdps += 1
            continue
        # The gate must evaluate semantics itself. UI label/window building
        # is not a prerequisite for detecting unresolved own multipliers.
        zone_effects = _collect_zone_effects(record, root_lookup_cache=root_lookup_cache)
        dynamic_effects = _extract_dynamic_effect_specs(record, root_lookup_cache=root_lookup_cache)
        if (record.get("end_ts_ms") is None and record.get("raw_duration_ms") is None
                and record.get('lifetime_mode') != 'Infinity'
                and _rdps_registry_verified_effect_entry(record, root_lookup_cache=root_lookup_cache) is not None):
            blockers.append({"uid": record.get("uid"), "event_key": record.get("event_key"),
                             "reason": "unresolved_buff_lifecycle"})
            continue
        if record.get("rdps_unresolved_semantic_condition"):
            blockers.append({"uid": record.get("uid"), "event_key": record.get("event_key"),
                             "reason": "unresolved_semantic_condition",
                             "condition_errors": record["rdps_unresolved_semantic_condition"]})
            continue
        if not _record_could_affect_external_rdps(record):
            continue
        source_key = str(record.get("source_character_key") or "")
        checked_external += 1
        uid = str(record.get("uid") or "")
        has_effect = bool(zone_effects or dynamic_effects)
        included = bool(uid and uid in included_uids)
        classification = _classify_packet_buff_record(record)
        verified_entry = _rdps_registry_verified_effect_entry(record, root_lookup_cache=root_lookup_cache)
        if verified_entry is not None:
            validation_error = _rdps_registry_validation_error(verified_entry, record)
            if validation_error is not None:
                blockers.append(
                    {
                        "uid": uid,
                        "event_key": record.get("event_key"),
                        "raw_event_key": record.get("raw_event_key"),
                        "source_character_key": record.get("source_character_key"),
                        "source_skill_key": record.get("source_skill_key"),
                        "source_skill_family_key": record.get("source_skill_family_key"),
                        "target_character_key": record.get("target_character_key"),
                        "target_enemy_key": record.get("target_enemy_key"),
                        "class": classification.get("class"),
                        "reason": validation_error.get("reason"),
                        "bb_keys": sorted(set(str(key) for key in record.get("bb_keys") or [])),
                        "unknown_bb_keys": validation_error.get("unknown_bb_keys"),
                        "required_bb_keys": validation_error.get("required_bb_keys"),
                        "has_effect": has_effect,
                        "included_effect_window": included,
                        "packet_mapping_rejected": bool(record.get("packet_mapping_rejected")),
                    }
                )
                continue
            accepted_effects += 1
            continue
        if _known_non_rdps_buff_record(record):
            accepted_non_rdps += 1
            continue
        if included and has_effect:
            if verified_entry is None:
                blockers.append(
                    {
                        "uid": uid,
                        "event_key": record.get("event_key"),
                        "raw_event_key": record.get("raw_event_key"),
                        "source_character_key": record.get("source_character_key"),
                        "source_skill_key": record.get("source_skill_key"),
                        "source_skill_family_key": record.get("source_skill_family_key"),
                        "target_character_key": record.get("target_character_key"),
                        "target_enemy_key": record.get("target_enemy_key"),
                        "class": classification.get("class"),
                        "reason": "included external rDPS effect is not in rdps_semantics_registry.verified_effects",
                        "bb_keys": sorted(set(str(key) for key in record.get("bb_keys") or [])),
                        "has_effect": has_effect,
                        "included_effect_window": included,
                        "packet_mapping_rejected": bool(record.get("packet_mapping_rejected")),
                    }
                )
                continue
            accepted_effects += 1
            continue
        if included and classification.get("class") == "effect":
            if verified_entry is None:
                blockers.append(
                    {
                        "uid": uid,
                        "event_key": record.get("event_key"),
                        "raw_event_key": record.get("raw_event_key"),
                        "source_character_key": record.get("source_character_key"),
                        "source_skill_key": record.get("source_skill_key"),
                        "source_skill_family_key": record.get("source_skill_family_key"),
                        "target_character_key": record.get("target_character_key"),
                        "target_enemy_key": record.get("target_enemy_key"),
                        "class": classification.get("class"),
                        "reason": "included external rDPS effect is not in rdps_semantics_registry.verified_effects",
                        "bb_keys": sorted(set(str(key) for key in record.get("bb_keys") or [])),
                        "has_effect": has_effect,
                        "included_effect_window": included,
                        "packet_mapping_rejected": bool(record.get("packet_mapping_rejected")),
                    }
                )
                continue
            accepted_effects += 1
            continue
        blockers.append(
            {
                "uid": uid,
                "event_key": record.get("event_key"),
                "raw_event_key": record.get("raw_event_key"),
                "source_character_key": record.get("source_character_key"),
                "source_skill_key": record.get("source_skill_key"),
                "source_skill_family_key": record.get("source_skill_family_key"),
                "target_character_key": record.get("target_character_key"),
                "target_enemy_key": record.get("target_enemy_key"),
                "class": classification.get("class"),
                "reason": classification.get("reason"),
                "bb_keys": sorted(set(str(key) for key in record.get("bb_keys") or [])),
                "has_effect": has_effect,
                "included_effect_window": included,
                "packet_mapping_rejected": bool(record.get("packet_mapping_rejected")),
            }
        )

    return {
        "mode": "pre_allocation_semantic_gate",
        "ok": not blockers,
        "checked_external_buff_count": checked_external,
        "accepted_effect_buff_count": accepted_effects,
        "accepted_non_rdps_buff_count": accepted_non_rdps,
        "blocker_count": len(blockers),
        "blocker_reason_counts": dict(Counter(str(row.get('reason') or 'unknown') for row in blockers)),
        # Keep the complete blocker list for audit and replay tooling. The
        # reason counts remain available for compact UI summaries, while
        # truncating here hid the concrete records needed to close a gate.
        "blockers": blockers,
    }


def _static_entry_applies_to_hit(entry: dict[str, Any], hit: dict[str, Any]) -> bool:
    zone = str(entry.get("zone") or "")
    if zone not in _RDPS_ALLOCATABLE_ZONES:
        return False
    rate = _safe_positive_rate(entry.get("rate"))
    if rate is None:
        return False
    if entry.get('evidence_kind') in {'official_EquipTable_attribute', 'official_WeaponSkill_text_basic',
                                    'current_official_item_skill_card_attribute'}:
        # Captured final attributes already contain equipment additions.
        # Preserve the typed evidence for display; do not add it twice.
        attr_type = entry.get('attr_type')
        baseline = hit.get('baseline') or {}
        if attr_type != 2 and (attr_type in baseline or str(attr_type) in baseline):
            observed = _coerce_optional_float(baseline.get(attr_type, baseline.get(str(attr_type))))
            if observed is not None:
                return False
            hit['rdps_unresolved_effect_conditions'] = True
        applies = _attr_type_applies_to_skill(attr_type, None,
            damage_decorate_mask=hit.get('damage_decorate_mask'),
            static_damage_decorate_mask=hit.get('static_damage_decorate_mask'),
            static_damage_decorate_mask_source=hit.get('static_damage_decorate_mask_source'))
        if applies is None:
            hit['rdps_unresolved_effect_conditions'] = True
        if applies is not True:
            return False
    if not _effect_applies_to_damage_element(
        str(entry.get("element") or "all"),
        hit.get("damage_element"),
        hit.get("damage_school"),
    ):
        return False

    skill_groups = {str(group or "") for group in entry.get("skill_groups") or [] if str(group or "")}
    if not skill_groups:
        return True
    masks = {'normal': 0x20484, 'skill': 0x100, 'combo': 0x2000, 'ultimate': 0x200}
    if not skill_groups.issubset(masks):
        hit['rdps_unresolved_effect_conditions'] = True
        return False
    failure = _effect_condition_failure({'condition': {'type': 'damage_decorate_mask',
        'check': 'HasAny', 'mask': sum(masks[group] for group in skill_groups)}}, hit)
    if failure is not None and failure.get('reason_group') == 'condition_unobservable':
        hit['rdps_unresolved_effect_conditions'] = True
    return failure is None


def _packet_modifier_guard_signature(effect: dict[str, Any]) -> tuple[Any, ...]:
    zone = str(effect.get("zone") or "")
    element = _normalize_effect_element(effect.get("element"))
    bb_keys = tuple(
        str(effect.get(key_name) or "")
        for key_name in ("bb_key", "add_bb_key", "delay_bb_key", "tick_bb_key", "max_bb_key")
    )
    try:
        attr_type = int(effect.get("attr_type")) if effect.get("attr_type") is not None else None
    except (TypeError, ValueError):
        attr_type = None
    condition_signature = _effect_condition_signature(effect)
    if any(bb_keys) or attr_type is not None or condition_signature:
        return ("keyed", zone, element, bb_keys, attr_type, condition_signature)
    return _effect_row_signature(effect)


def _matched_packet_defender_effect_signatures(
    *,
    hit: dict[str, Any],
    buff_windows: list[dict[str, Any]],
    defender_modifier_uids: set[str],
) -> set[tuple[Any, ...]]:
    if not defender_modifier_uids:
        return set()

    target_enemy_key = str(hit.get("target_enemy_key") or "")
    hit_ts_ms = int(hit.get("ts_ms") or 0)
    hit_line_no = int(hit.get("line_no") or 0)
    skill_key = str(hit.get("skill_key") or "")
    signatures: set[tuple[Any, ...]] = set()
    for window in buff_windows:
        if not target_enemy_key or str(window.get("target_character_key") or "") != target_enemy_key:
            continue
        if not (_window_uid_aliases(window) & defender_modifier_uids):
            continue
        window_effects = _window_effects_at_ts(window, hit_ts_ms, line_no=int(hit.get('line_no') or 0),
                                               packet_seq_id=hit.get('packet_seq_id'))
        if not window_effects:
            continue
        window_start_ts_ms = int(window.get("start_ts_ms") or 0)
        if not (window_start_ts_ms <= hit_ts_ms <= int(window.get("end_ts_ms") or 0)):
            continue
        window_line_no = int(window.get("start_line_no") or window.get("line_no") or 0)
        if window_start_ts_ms == hit_ts_ms and hit_line_no and window_line_no and window_line_no > hit_line_no:
            continue
        if window_start_ts_ms == hit_ts_ms and str(window.get("event_key") or "") == skill_key:
            continue
        skill_family_key = window.get("skill_family_key")
        if skill_family_key and hit.get("skill_family_key") != skill_family_key:
            continue
        skill_filter = _BUFF_SKILL_FILTER.get(str(window.get("event_key") or ""))
        if skill_filter and not skill_filter.search(skill_key):
            continue

        for effect in window_effects:
            if str(effect.get("zone") or "") == "vuln_taken":
                signatures.add(_packet_modifier_guard_signature(effect))
    return signatures


def _active_target_buff_ids_for_hit(
    hit: dict[str, Any],
    condition_buff_records: list[dict[str, Any]] | None,
    *, side: str = 'defender',
) -> set[str]:
    target = str(hit.get('character_key' if side == 'attacker' else 'target_enemy_key') or '')
    target_id = str(hit.get('source_actor_id' if side == 'attacker' else 'target_actor_id') or '')
    stamp = int(hit.get("ts_ms") or 0)
    line_no = int(hit.get("line_no") or 0)
    packet_uids = {str(uid) for uid in (hit.get("packet_modifier_uids") or {}).get(side, [])}
    active = set()
    for record in condition_buff_records or []:
        if str(record.get("target_enemy_key") or record.get("target_character_key") or "") != target:
            continue
        owner_id = str(record.get("target_actor_id") or "")
        uid_seen = str(record.get("uid") or "") in packet_uids
        if target_id and owner_id and target_id != owner_id:
            continue
        if not (target_id and owner_id) and not uid_seen:
            continue
        start = _coerce_optional_int(record.get("ts_ms"))
        if start is None or start > stamp:
            continue
        start_line = int(record.get("line_no") or 0)
        if start == stamp and line_no and start_line and start_line > line_no:
            continue
        end = _coerce_optional_int(record.get("end_ts_ms"))
        end_line = int(record.get("end_line_no") or 0)
        if end is not None and (stamp > end or (stamp == end and (not end_line or not line_no or line_no >= end_line))):
            continue
        snapshot_end = record.get('snapshot_end_ts_ms')
        if snapshot_end is not None and (stamp > int(snapshot_end) or stamp == int(snapshot_end)
                and (not line_no or line_no >= int(record.get('snapshot_end_line_no') or 0))):
            continue
        duration = _coerce_optional_int(record.get("raw_duration_ms"))
        if duration is not None and (duration <= 0 or stamp >= start + duration):
            continue
        if end is None and duration is None and not uid_seen and record.get('lifetime_mode') != 'Infinity':
            continue
        for key in (record.get("event_key"), record.get("raw_event_key")):
            if key:
                active.add(_normalize_buff_id(key))
    return active


def _arts_strength_effect_contributors(
    *,
    effect_window: dict[str, Any],
    effect: dict[str, Any],
    buff_windows: list[dict[str, Any]],
) -> list[tuple[str, float, dict[str, Any] | None]]:
    """Require a typed source-strength provenance before allocating it."""

    source_key = str(effect_window.get("source_character_key") or "")
    final_rate = float(effect.get("rate") or 0.0)
    if effect.get('arts_strength_provenance') is not True:
        # This helper is also used by ordinary enemy debuffs. Their explicit
        # provider remains the provider; no secondary Arts redistribution is
        # inferred from their identifier.
        return [(source_key, final_rate, None)]

    applied_at_ms = int(effect_window.get("start_ts_ms") or 0)
    applied_line_no = int(effect_window.get("start_line_no") or effect_window.get("line_no") or 0)
    self_points = max(float(effect_window.get("source_static_arts_strength") or 0.0), 0.0)
    external_points: dict[str, float] = defaultdict(float)
    external_approval_keys: dict[str, set[str]] = defaultdict(set)

    for candidate in buff_windows:
        if str(candidate.get("target_character_key") or "") != source_key:
            continue
        start_ms = int(candidate.get("start_ts_ms") or 0)
        end_ms = int(candidate.get("end_ts_ms") or 0)
        if not (start_ms <= applied_at_ms <= end_ms):
            continue
        start_line_no = int(candidate.get("start_line_no") or candidate.get("line_no") or 0)
        if start_ms == applied_at_ms and applied_line_no and start_line_no and start_line_no > applied_line_no:
            continue
        provider_key = str(candidate.get("source_character_key") or "")
        if not provider_key:
            continue
        for candidate_effect in _window_effects_at_ts(candidate, applied_at_ms, line_no=applied_line_no):
            if str(candidate_effect.get("zone") or "") != "arts_strength":
                continue
            points = float(candidate_effect.get("rate") or 0.0)
            if points <= 0:
                continue
            if provider_key == source_key:
                self_points += points
            else:
                if candidate_effect.get('external_rdps_allowed') is False:
                    continue
                external_points[provider_key] += points
                external_approval_keys[provider_key].update(candidate_effect.get('rdps_whitelist_keys') or [])

    external_total = sum(external_points.values())
    if external_total <= 0:
        return [(source_key, final_rate, None)]

    without_external = final_rate * (
        _arts_strength_effect_multiplier(self_points)
        / _arts_strength_effect_multiplier(self_points + external_total)
    )
    external_rate = max(final_rate - without_external, 0.0)
    contributors: list[tuple[str, float, dict[str, Any] | None]] = [(source_key, without_external, None)]
    for provider_key, points in external_points.items():
        provider_rate = external_rate * (points / external_total)
        provenance = {
            "derived_from_zone": "arts_strength",
            "effect_event_key": effect_window.get("event_key"),
            "effect_source_character_key": source_key,
            "arts_strength_points": points,
            "arts_strength_self_points": self_points,
            "arts_strength_external_points": external_total,
            "arts_strength_whitelist_keys": sorted(external_approval_keys[provider_key]),
        }
        contributors.append((provider_key, provider_rate, provenance))
    return contributors


def _rdps_debug_record_arts_effect_provenance(
    *,
    effect_window: dict[str, Any],
    effect: dict[str, Any],
    source_key: str,
    rate: float,
    provenance: dict[str, Any] | None,
    scope: str,
) -> dict[str, Any]:
    debug_effect = dict(effect)
    debug_effect["rate"] = rate
    debug_window = dict(effect_window)
    debug_window["source_character_key"] = source_key
    debug_window["source_character_name"] = _resolve_character_name(source_key) or source_key
    record = _rdps_debug_record_applicable_effect(window=debug_window, effect=debug_effect, scope=scope)
    if provenance:
        record.update(
            {
                "derived_from_zone": "arts_strength",
                "effect_source_character_key": provenance.get("effect_source_character_key"),
                "arts_strength_points": _round4(provenance.get("arts_strength_points") or 0.0),
                "arts_strength_self_points": _round4(provenance.get("arts_strength_self_points") or 0.0),
                "arts_strength_external_points": _round4(provenance.get("arts_strength_external_points") or 0.0),
                "effect_rdps_whitelist_keys": list(effect.get('rdps_whitelist_keys') or []),
                "rdps_whitelist_keys": list(provenance.get('arts_strength_whitelist_keys') or []),
            }
        )
    return record


def _allocate_rdps_for_hit(
    hit: dict[str, Any],
    buff_windows: list[dict[str, Any]],
    *,
    include_debug: bool = False,
    buff_records_by_uid: dict[str, list[dict[str, Any]]] | None = None,
    condition_buff_records: list[dict[str, Any]] | None = None,
    condition_records_by_target: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    attacker_key = hit["character_key"]
    target_enemy_key = hit["target_enemy_key"]
    hit_value = float(hit["hit_value"])
    hit_ts_ms = hit["ts_ms"]
    damage_element = hit.get("damage_element")
    damage_school = hit.get("damage_school")
    arts_strength_damage_hit = _is_arts_strength_damage_hit(hit)
    # Attack percentage bonuses affect only attack-scaled damage. A dormant
    # custom calculation on a simple unit is not its active damage formula.
    calculation = hit.get('damage_calculation')
    calculations = [calculation] if isinstance(calculation, dict) else []
    if not calculations and 'damage_calculation' in hit:
        calculations = [unit['damage_calculation'] for units in
                        _load_skill_action_damage_units(str(hit.get('skill_key') or '')).values()
                        for unit in units if unit.get('damage_attribute_type') == 'hp'
                        and isinstance(unit.get('damage_calculation'), dict)]
    attack_kinds = []
    for candidate in calculations:
        if candidate.get('source') != 'current_typed_damage_unit':
            attack_kinds.append('unknown')
            continue
        custom = candidate.get('atkCalculation') or {}
        kind = custom.get('$type')
        if candidate.get('simpleCalculation') is True:
            attack_kinds.append('linear')
        elif candidate.get('simpleCalculation') is not False:
            attack_kinds.append('unknown')
        elif kind in {'Beyond.Gameplay.Core.AtkScaleCalculation', 'Beyond.Gameplay.Core.BreakingAttackCalculation'}:
            attack_kinds.append('linear')
        elif kind in {'Beyond.Gameplay.Core.DefiniteValueCalculation', 'Beyond.Gameplay.Core.PrimaryAttrCalculation'}:
            attack_kinds.append('independent')
        elif kind == 'Beyond.Gameplay.Core.MultiplyAttributeCalculation':
            if custom.get('attributeType') != 'Atk' or custom.get('valueSource') != 'AttackerOrHealer':
                attack_kinds.append('independent')
            else:
                addition = custom.get('addition') or {}
                attack_kinds.append('linear' if addition.get('useBlackboardKey') is False
                                    and _coerce_optional_float(addition.get('value')) == 0.0 else 'affine')
        else:
            attack_kinds.append('unknown')
    attack_kind = (attack_kinds[0] if attack_kinds and len(set(attack_kinds)) == 1 else
                   'linear' if 'damage_calculation' not in hit else 'unknown')
    if attack_kind == 'affine' and not isinstance(calculation, dict):
        attack_kind = 'unknown'  # Different units cannot borrow one set of BB parameters.
    hit['attack_damage_dependency'] = attack_kind
    # The official text defines arts-strength as anomaly-only. A hit that
    # is not observed as anomaly damage does not consume this entry.
    packet_modifier_uids = hit.get("packet_modifier_uids") if isinstance(hit.get("packet_modifier_uids"), dict) else {}
    attacker_modifier_uids = {str(item) for item in packet_modifier_uids.get("attacker") or [] if str(item)}
    defender_modifier_uids = {str(item) for item in packet_modifier_uids.get("defender") or [] if str(item)}
    packet_attr_details = hit.get("packet_attr_details") if isinstance(hit.get("packet_attr_details"), dict) else {}
    defender_allowed_effects = _packet_attr_allowed_effects(packet_attr_details.get("defender") if isinstance(packet_attr_details, dict) else [])
    hit_line_no = int(hit.get("line_no") or 0)

    external_by_zone: dict[str, list[tuple[str, float, dict[str, Any] | None]]] = defaultdict(list)
    self_by_zone: dict[str, float] = defaultdict(float)
    self_factor_by_zone: dict[str, float] = defaultdict(lambda: 1.0)
    captured_by_attr_type: dict[int, float] = defaultdict(float)
    contributors_by_zone: dict[str, list[dict[str, Any]]] = defaultdict(list) if include_debug else {}
    ignored_effects: list[dict[str, Any]] = []
    invalid_numeric_allocation = False
    resistance_attr_type = {'physical': 94, 'natural': 95, 'cryst': 96,
                            'pulse': 97, 'fire': 98, 'ether': 99}.get(damage_element)
    resistance_rows = [str(token).partition(':')[2]
                       for token in (packet_attr_details.get('defender') or [])
                       if resistance_attr_type is not None
                       and str(token).partition(':')[0] == str(resistance_attr_type)]
    # This is the resistance observed in this damage operation, including
    # temporary modifications such as the attacker's resistance ignore.
    # ENTITY_STATS may predate the debuff and is not a replacement for it.
    resistance_values = [_coerce_optional_float(value) for row in resistance_rows for value in row.split(',')]
    effective_resistance = (resistance_values[0] if resistance_values and not hit.get('packet_attr_conflict')
                            and all(value is not None for value in resistance_values)
                            and len(set(resistance_values)) == 1 else None)
    resistance_numerator = 100.0 - effective_resistance if effective_resistance is not None else None
    resistance_source = f'PKT_ATTR.defender.attr{resistance_attr_type}'
    matched_packet_defender_effect_signatures = _matched_packet_defender_effect_signatures(
        hit=hit,
        buff_windows=buff_windows,
        defender_modifier_uids=defender_modifier_uids,
    ) if bool(hit.get("packet_modifier_seen")) else set()
    defender_records = (condition_records_by_target.get(str(hit.get('target_enemy_key') or ''), [])
                        if condition_records_by_target is not None else condition_buff_records)
    active_target_buff_ids = _active_target_buff_ids_for_hit(hit, defender_records)
    hit["_active_target_buff_ids"] = sorted(active_target_buff_ids)
    if hit.get('source_actor_id'):
        attacker_records = (condition_records_by_target.get(str(hit.get('character_key') or ''), [])
                            if condition_records_by_target is not None else condition_buff_records)
        hit['_active_attacker_buff_ids'] = sorted(_active_target_buff_ids_for_hit(hit, attacker_records, side='attacker'))

    effects_at_hit = [_window_effects_at_ts(window, hit_ts_ms, line_no=hit_line_no,
                                           packet_seq_id=hit.get('packet_seq_id'),
                                           packet_session_id=hit.get('packet_session_id')) for window in buff_windows]
    stack_groups: defaultdict[tuple[str, str, str, str], list[int]] = defaultdict(list)
    disabled_self_groups: defaultdict[tuple[str, str, str, str], list[int]] = defaultdict(list)
    unresolved_stack_zones: set[str] = set()
    for index, (window, effects) in enumerate(zip(buff_windows, effects_at_hit)):
        owner = window.get('target_character_key')
        actor_id = str(hit.get('source_actor_id' if owner == attacker_key else 'target_actor_id') or '')
        window_actor_id = str(window.get('target_actor_id') or '')
        if owner not in {attacker_key, target_enemy_key} or (actor_id and window_actor_id and actor_id != window_actor_id):
            continue
        if window.get('_effect_state_unresolved_at_hit'):
            potential_effects = list(window.get('zone_effects') or []) + list(window.get('dynamic_effects') or [])
            zones = {str(effect['zone']) for effect in potential_effects
                     if _effect_element_applies_to_hit(effect, damage_element, damage_school)}
            if zones:
                unresolved_stack_zones.update(zones)
                hit['rdps_unresolved_effect_conditions'] = True
                if include_debug:
                    ignored_effects.append({'uid': window.get('uid'), 'event_key': window.get('event_key'),
                        'reason': 'unresolved_buff_state_zone_baseline', 'reason_group': 'condition_unobservable'})
        if window.get('_buff_enabled_at_hit') is False and window.get('source_character_key') == attacker_key:
            disabled_self_groups[_buff_window_stack_group_key(window)].append(index)
        if not effects:
            continue
        stack_groups[_buff_window_stack_group_key(window)].append(index)
    for group, indices in stack_groups.items():
        policies, limits = set(), set()
        for index in indices:
            window = buff_windows[index]
            root = _load_current_buff_roots().get(str(window.get('event_key') or ''))
            settings = root.get('stackingSettings') if isinstance(root, dict) else None
            policies.add(settings.get('stackingType') if isinstance(settings, dict) else window.get('stacking_type'))
            limits.add(_buff_stack_limit(window.get('event_key'), window.get('bb_values'))
                       if isinstance(settings, dict) else _positive_int(window.get('stack_limit')))
        if policies == {'Unlimited'}:
            continue
        if (policies & {'HighPriority', 'HighPriorityWithMaxStack'} and disabled_self_groups.get(group)
                and any(buff_windows[i].get('source_character_key') != attacker_key for i in indices)):
            # Removing an external priority winner may reactivate a suppressed
            # self buff. Its counterfactual selection has not been proved.
            for index in indices + disabled_self_groups[group]:
                window = buff_windows[index]
                potential_effects = list(window.get('zone_effects') or []) + list(window.get('dynamic_effects') or [])
                zones = {str(effect['zone']) for effect in potential_effects
                         if _effect_element_applies_to_hit(effect, damage_element, damage_school)}
                if zones:
                    unresolved_stack_zones.update(zones)
                    hit['rdps_unresolved_effect_conditions'] = True
                    window['rdps_unresolved_stack_evidence'] = True
                    if include_debug:
                        ignored_effects.append({'uid': window.get('uid'), 'event_key': window.get('event_key'),
                            'reason': 'unresolved_priority_self_baseline', 'reason_group': 'condition_unobservable'})
        uids = {str(buff_windows[i].get('uid') or f'unknown:{i}') for i in indices}
        if len(uids) <= 1:
            continue
        limit = next(iter(limits)) if len(limits) == 1 else None
        if len(policies) == 1 and limit is not None and len(uids) <= limit:
            continue
        # No enabled winner is implied by age, UID ordering, or BB magnitude.
        # Omit this zone too when a competing self buff makes its baseline
        # unknown; allocating another external effect against zero would lie.
        for index in indices:
            window = buff_windows[index]
            zones = {str(effect['zone']) for effect in effects_at_hit[index]
                     if _effect_element_applies_to_hit(effect, damage_element, damage_school)}
            if not zones:
                continue
            unresolved_stack_zones.update(zones)
            window['rdps_unresolved_stack_evidence'] = True
            hit['rdps_unresolved_effect_conditions'] = True
            if include_debug:
                ignored_effects.append({'uid': window.get('uid'), 'event_key': window.get('event_key'),
                    'reason': 'unresolved_buff_stack_selection', 'reason_group': 'condition_unobservable',
                    'stack_group': list(group), 'candidate_uids': sorted(uids)})

    # A present, complete report with no entry for this resistance means no
    # instant modifier touched it. A known pre-debuff snapshot plus explicit
    # persistent updates can then supply the same resistance factor.
    if (effective_resistance is None and not resistance_rows and resistance_attr_type is not None
            and hit.get('packet_attribute_report_present') is True
            and not hit.get('packet_attribute_report_unresolved') and 'res' not in unresolved_stack_zones):
        initial = _coerce_optional_float((hit.get('target_resistance_points') or {}).get(damage_element))
        snapshot_ts = hit.get('target_stats_snapshot_ts_ms')
        snapshot_line = hit.get('target_stats_snapshot_line_no')
        target_id = str(hit.get('target_actor_id') or '')
        if (initial is not None and type(snapshot_ts) is int and type(snapshot_line) is int
                and target_id and not hit.get('rdps_unresolved_target_binding')):
            baseline_stamp = (snapshot_ts, snapshot_line)
            current_stamp = (hit_ts_ms, hit_line_no)
            known = baseline_stamp <= current_stamp
            persistent_points = 0.0
            observed_persistent = False
            covered_instances = set()
            for candidate, effects in zip(buff_windows, effects_at_hit):
                if (str(candidate.get('target_actor_id') or '') != target_id
                        or candidate.get('target_character_key') != target_enemy_key):
                    continue
                specs = [spec for spec in candidate.get('dynamic_effects') or []
                         if 'parameter_snapshots' in spec and spec.get('zone') == 'res'
                         and _effect_element_applies_to_hit(spec, damage_element, damage_school)]
                if not specs:
                    continue
                covered_instances.add((str(candidate.get('uid') or ''), candidate.get('start_ts_ms'), candidate.get('start_line_no')))
                start_stamp = (int(candidate.get('start_ts_ms') or 0), int(candidate.get('start_line_no') or 0))
                end_stamp = (int(candidate.get('end_ts_ms') or 0), int(candidate.get('end_line_no') or 2**63 - 1))
                if start_stamp <= baseline_stamp < end_stamp:
                    known = False  # The snapshot may already include this Buff.
                if candidate.get('_effect_state_unresolved_at_hit'):
                    known = False
                if start_stamp <= current_stamp <= end_stamp:
                    observed_persistent = True
                    persistent_points += sum(effect['rate'] for effect in effects
                        if effect.get('resistance_point_source') == 'packet_bb_update_and_refresh'
                        and _effect_element_applies_to_hit(effect, damage_element, damage_school))
            attribute_name = {'physical': 'PhysicalResistance', 'natural': 'NaturalResistance',
                              'cryst': 'CrystResistance', 'pulse': 'PulseResistance',
                              'fire': 'FireResistance', 'ether': 'EtherResistance'}[damage_element]
            for candidate in condition_buff_records or []:
                if (str(candidate.get('target_actor_id') or '') != target_id
                        or (str(candidate.get('uid') or ''), candidate.get('ts_ms'), candidate.get('line_no')) in covered_instances):
                    continue
                start_stamp = (int(candidate.get('ts_ms') or 0), int(candidate.get('line_no') or 0))
                end_stamp = (int(candidate.get('end_ts_ms') or hit_ts_ms), int(candidate.get('end_line_no') or 2**63 - 1))
                if not (start_stamp <= baseline_stamp < end_stamp or start_stamp <= current_stamp < end_stamp):
                    continue
                root = _load_current_buff_roots().get(str(candidate.get('event_key') or '')) or {}
                if any(modifier.get('attributeType') == attribute_name
                       for modifier in (root.get('attributeModifier') or {}).get('attributeModifiers') or []):
                    known = False  # Another observed persistent change is not modeled here.
            if known and observed_persistent and isfinite(persistent_points):
                effective_resistance = initial - persistent_points
                resistance_numerator = 100.0 - effective_resistance
                resistance_source = 'ENTITY_STATS+packet_bb_refresh+complete_instant_report'

    for window, window_effects in zip(buff_windows, effects_at_hit):
        if not window_effects:
            if window.get('_enable_state_unobserved_at_hit'):
                hit['rdps_unresolved_effect_conditions'] = True
                if include_debug:
                    ignored_effects.append({'uid': window.get('uid'), 'event_key': window.get('event_key'),
                        'reason': 'unobserved_initial_buff_enable_state', 'reason_group': 'condition_unobservable'})
            if (not hit_line_no and (window.get('snapshot_update') and window.get('start_ts_ms') == hit_ts_ms
                                    or window.get('snapshot_end_ts_ms') == hit_ts_ms)):
                hit['rdps_unresolved_effect_conditions'] = True
                if include_debug:
                    ignored_effects.append({'uid': window.get('uid'), 'event_key': window.get('event_key'),
                                            'reason': 'unresolved_buff_snapshot_order', 'reason_group': 'condition_unobservable'})
            continue
        window_start_ts_ms = int(window.get("start_ts_ms") or 0)
        if not (window_start_ts_ms <= hit_ts_ms <= int(window.get("end_ts_ms") or 0)):
            continue
        window_line_no = int(window.get("start_line_no") or window.get("line_no") or 0)
        if window_start_ts_ms == hit_ts_ms and hit_line_no and window_line_no and window_line_no > hit_line_no:
            continue
        if window_start_ts_ms == hit_ts_ms and str(window.get("event_key") or "") == str(hit.get("skill_key") or ""):
            continue
        skill_family_key = window.get("skill_family_key")
        if skill_family_key and hit.get("skill_family_key") != skill_family_key:
            continue
        skill_filter = _BUFF_SKILL_FILTER.get(window["event_key"])
        if skill_filter and not skill_filter.search(str(hit.get("skill_key") or "")):
            continue

        applies_to_attacker = window["target_character_key"] == attacker_key
        applies_to_enemy = bool(target_enemy_key) and window["target_character_key"] == target_enemy_key
        if not applies_to_attacker and not applies_to_enemy:
            continue

        window_uid_aliases = _window_uid_aliases(window)
        packet_uid_match = bool(window_uid_aliases & defender_modifier_uids)
        if applies_to_attacker:
            attacker_instance = str(hit.get("source_actor_id") or "")
            window_instance = str(window.get("target_actor_id") or "")
            if attacker_instance and window_instance and attacker_instance != window_instance:
                if window_uid_aliases & attacker_modifier_uids:
                    hit["rdps_unresolved_target_binding"] = True
                if include_debug:
                    ignored_effects.append({'uid': window.get('uid'), 'event_key': window.get('event_key'),
                                            'reason': 'buff_owner_instance_mismatch'})
                continue
        if applies_to_enemy:
            target_instance = str(hit.get("target_actor_id") or "")
            window_instance = str(window.get("target_actor_id") or "")
            if target_instance and window_instance and target_instance != window_instance:
                if packet_uid_match:
                    hit["rdps_unresolved_target_binding"] = True
                continue
            if not (target_instance and window_instance) and not packet_uid_match:
                hit["rdps_unresolved_target_binding"] = True
                continue

        source_key = window["source_character_key"]
        if not source_key:
            continue

        for effect in window_effects:
            zone = effect["zone"]
            if zone == 'atk' and attack_kind in {'independent', 'unknown'}:
                if attack_kind == 'unknown':
                    hit['rdps_unresolved_effect_conditions'] = True
                if include_debug:
                    ignored_effects.append({'uid': window.get('uid'), 'event_key': window.get('event_key'),
                        'zone': 'atk', 'reason': 'damage_not_scaled_by_attack' if attack_kind == 'independent'
                        else 'attack_damage_calculation_unresolved'})
                continue
            if zone in unresolved_stack_zones:
                if include_debug:
                    ignored_effects.append({'uid': window.get('uid'), 'event_key': window.get('event_key'),
                        'zone': zone, 'reason': 'unresolved_stack_zone_baseline', 'reason_group': 'condition_unobservable'})
                continue
            rate = _coerce_optional_float(effect.get('rate'))
            if rate is None:
                invalid_numeric_allocation = True
                if include_debug:
                    ignored_effects.append({'uid': window.get('uid'), 'event_key': window.get('event_key'),
                                            'reason': 'invalid_effect_rate'})
                continue
            element = effect.get("element")
            required_side = ('defender' if zone in {'vuln_taken', 'fragile', 'res', 'prod_vuln_taken'} else
                             'attacker' if zone in {'atk', 'dmg_inc', 'amp', 'combo', 'arts_strength', 'crit', 'prod_dmg_inc'} else None)
            if 'recipient_side' in effect:
                if effect['recipient_side'] not in {'attacker', 'defender'}:
                    hit['rdps_unresolved_effect_conditions'] = True
                    continue
                required_side = effect['recipient_side']
            if ((required_side == 'defender' and not applies_to_enemy)
                    or (required_side == 'attacker' and not applies_to_attacker)):
                if include_debug:
                    ignored_effects.append({
                        **_rdps_debug_record_applicable_effect(window=window, effect=effect, scope='ignored'),
                        'reason': 'effect_recipient_side_mismatch', 'required_side': required_side,
                    })
                continue
            packet_uid_restricted = (
                bool(hit.get("packet_modifier_seen"))
                and applies_to_enemy
                and bool(window_uid_aliases)
                and zone == "vuln_taken"
                and _packet_modifier_guard_signature(effect) in matched_packet_defender_effect_signatures
            )
            if packet_uid_restricted and not packet_uid_match:
                if include_debug:
                    ignored_effects.append(
                        {
                            **_rdps_debug_record_applicable_effect(window=window, effect=effect, scope="ignored"),
                            "reason": "packet_defender_uid_suppressed",
                            "reason_group": "packet_modifier_uid_mismatch",
                            "packet_modifier_guard": "defender_uid_selection",
                            "packet_modifier_seen": True,
                            "packet_modifier_uids": {
                                "attacker": sorted(attacker_modifier_uids),
                                "defender": sorted(defender_modifier_uids),
                            },
                            "candidate_uids": sorted(window_uid_aliases),
                        }
                    )
                continue
            effect_skill_filter = _BUFF_EFFECT_SKILL_FILTER.get((window["event_key"], zone))
            if effect_skill_filter and not effect_skill_filter.search(str(hit.get("skill_key") or "")):
                continue
            attr_type = effect.get("attr_type")
            if isinstance(attr_type, int):
                attr_applies = _attr_type_applies_to_skill(
                    attr_type, None, damage_decorate_mask=hit.get('damage_decorate_mask'),
                    static_damage_decorate_mask=hit.get('static_damage_decorate_mask'),
                    static_damage_decorate_mask_source=hit.get('static_damage_decorate_mask_source'))
                if attr_applies is None:
                    hit['rdps_unresolved_effect_conditions'] = True
                if attr_applies is not True:
                    if include_debug:
                        ignored_effects.append({
                            **_rdps_debug_record_applicable_effect(window=window, effect=effect, scope='ignored'),
                            'reason': 'attribute_damage_mask_missing' if attr_applies is None else 'attribute_damage_mask_filtered',
                            'reason_group': 'condition_unobservable' if attr_applies is None else 'condition_unsatisfied',
                        })
                    continue
            # The typed processor defines the zone. A BB name containing
            # "resistance" does not turn a NormalCalcZone effect into resistance.
            if zone == "crit":
                if include_debug:
                    ignored_effects.append(
                        {
                            **_rdps_debug_record_applicable_effect(window=window, effect=effect, scope="ignored"),
                            "reason": "crit_not_allocated",
                        }
                    )
                continue
            if zone not in _RDPS_ALLOCATABLE_ZONES:
                if include_debug:
                    ignored_effects.append(
                        {
                            **_rdps_debug_record_applicable_effect(window=window, effect=effect, scope="ignored"),
                            "reason": "resistance_counterfactual_unresolved" if zone == 'res' else "utility_not_allocated",
                        }
                    )
                continue
            if zone == "arts_strength" and not arts_strength_damage_hit:
                if include_debug:
                    ignored_effects.append(
                        {
                            **_rdps_debug_record_applicable_effect(window=window, effect=effect, scope="ignored"),
                            "reason": "arts_strength_only_affects_anomaly_damage",
                        }
                    )
                continue
            if rate <= 0:
                continue
            if not _effect_element_applies_to_hit(effect, damage_element, damage_school):
                if include_debug:
                    ignored_effects.append(
                        {
                            **_rdps_debug_record_applicable_effect(window=window, effect=effect, scope="ignored"),
                            **_element_filter_debug_fields(effect["element"], damage_element, damage_school),
                        }
                    )
                continue
            condition_failure = _effect_condition_failure(effect, hit)
            if condition_failure:
                if condition_failure.get("reason_group") == "condition_unobservable":
                    hit["rdps_unresolved_effect_conditions"] = True
                if include_debug:
                    ignored_effects.append(
                        {
                            **_rdps_debug_record_applicable_effect(window=window, effect=effect, scope="ignored"),
                            **condition_failure,
                        }
                    )
                continue
            if (
                source_key != attacker_key
                and applies_to_enemy
                and defender_allowed_effects
                and not defender_modifier_uids
                and (zone, _normalize_effect_element(effect["element"])) not in defender_allowed_effects
            ):
                if include_debug:
                    ignored_effects.append(
                        {
                            **_rdps_debug_record_applicable_effect(window=window, effect=effect, scope="ignored"),
                            "reason": "packet_attr_mismatch",
                        }
                    )
                continue
            if zone == 'res':
                if damage_element in {'real', 'lifedrain'}:
                    continue
                if (resistance_numerator is None or not isfinite(resistance_numerator)
                        or resistance_numerator <= 0):
                    hit['rdps_unresolved_effect_conditions'] = True
                    if include_debug:
                        ignored_effects.append({
                            **_rdps_debug_record_applicable_effect(window=window, effect=effect, scope='ignored'),
                            'reason': 'resistance_counterfactual_unresolved',
                            'detail': 'missing_or_conflicting_hit_resistance',
                        })
                    continue
            effect_contributors = (
                _arts_strength_effect_contributors(
                    effect_window=window,
                    effect=effect,
                    buff_windows=buff_windows,
                )
                if applies_to_enemy
                else [(source_key, rate, None)]
            )
            for contributor_key, contributor_rate, provenance in effect_contributors:
                contributor_rate = _coerce_optional_float(contributor_rate)
                if contributor_rate is None:
                    invalid_numeric_allocation = True
                    continue
                if contributor_rate <= 0:
                    continue
                if contributor_key != attacker_key and effect.get('external_rdps_allowed') is False:
                    hit['rdps_unresolved_whitelist_effect'] = True
                    if include_debug:
                        ignored_effects.append({
                            **_rdps_debug_record_applicable_effect(window=window, effect=effect, scope='ignored'),
                            'reason': 'external_effect_not_whitelisted',
                        })
                    continue
                # Subtract only effects actually retained in the model from
                # the observed final attribute. Rejected external effects
                # remain in the attacker's baseline; they get no credit.
                if applies_to_attacker and isinstance(attr_type, int):
                    captured_by_attr_type[attr_type] += contributor_rate
                scope = "self" if contributor_key == attacker_key else "external"
                record = (
                    _rdps_debug_record_arts_effect_provenance(
                        effect_window=window,
                        effect=effect,
                        source_key=contributor_key,
                        rate=contributor_rate,
                        provenance=provenance,
                        scope=scope,
                    )
                    if include_debug
                    else None
                )
                if contributor_key == attacker_key:
                    if zone.startswith('prod_'):
                        self_factor_by_zone[zone] *= 1.0 + contributor_rate
                    else:
                        self_by_zone[zone] += contributor_rate
                else:
                    external_by_zone[zone].append((contributor_key, contributor_rate, record))
                if include_debug and record is not None:
                    contributors_by_zone[zone].append(record)

    observed_final_attr_types: set[int] = set()
    for raw_attr_type, final_value in (hit.get("baseline") or {}).items():
        try:
            attr_type = int(raw_attr_type)
        except (TypeError, ValueError):
            continue
        if attr_type == 2:
            continue
        mapping = _ATTR_TYPE_TO_EFFECT.get(attr_type)
        if mapping is None:
            continue
        final_rate = _coerce_optional_float(final_value)
        if final_rate is None or final_rate < 0:
            hit['rdps_unresolved_effect_conditions'] = True
            continue
        zone, element = mapping
        if zone == "crit":
            continue
        observed_final_attr_types.add(attr_type)
        if final_rate == 0:
            continue
        attr_applies = _attr_type_applies_to_skill(
            attr_type, None, damage_decorate_mask=hit.get('damage_decorate_mask'),
            static_damage_decorate_mask=hit.get('static_damage_decorate_mask'),
            static_damage_decorate_mask_source=hit.get('static_damage_decorate_mask_source'))
        if attr_applies is None and element == damage_element:
            # An element-specific attribute has an exact element match; the
            # decorate mask is only needed for broader skill-category rules.
            attr_applies = True
        if attr_applies is None:
            hit['rdps_unresolved_effect_conditions'] = True
        if attr_applies is not True:
            continue
        if not _effect_applies_to_damage_element(element, damage_element, damage_school):
            continue
        baseline_rate = final_rate - captured_by_attr_type.get(attr_type, 0.0)
        if baseline_rate < 0:
            hit['rdps_unresolved_effect_conditions'] = True
            continue
        if baseline_rate == 0:
            continue
        self_by_zone[zone] += baseline_rate
        if include_debug:
            contributors_by_zone[zone].append(
                _rdps_debug_record_baseline_self(
                    hit=hit,
                    attr_type=attr_type,
                    zone=zone,
                    element=str(element or "all"),
                    rate=baseline_rate,
                    final_value=final_rate,
                    captured=captured_by_attr_type.get(attr_type, 0.0),
                )
            )

    for entry in hit.get("static_multiplier_entries") or []:
        if not isinstance(entry, dict) or not _static_entry_applies_to_hit(entry, hit):
            continue
        if entry.get('zone') == 'atk' and attack_kind == 'independent':
            continue
        if entry.get('attr_type') in observed_final_attr_types:
            # The per-hit final attribute already contains this static source.
            continue
        zone = str(entry.get("zone") or "")
        rate = _coerce_optional_float(entry.get('rate'))
        if rate is None:
            invalid_numeric_allocation = True
            continue
        self_by_zone[zone] += rate
        if include_debug:
            contributors_by_zone[zone].append(_rdps_debug_record_static_self(hit=hit, entry=entry))

    attack_extra_baseline = 0.0
    attack_baseline_error = None
    flat_attack = _coerce_optional_float(hit.get('static_flat_attack', 0.0))
    base_attack = _coerce_optional_float(hit.get('base_attack_for_percent'))
    if flat_attack is None or flat_attack < 0:
        attack_baseline_error = 'invalid_flat_attack_baseline'
    elif flat_attack > 0:
        if base_attack is None or base_attack <= 0:
            attack_baseline_error = 'missing_base_attack_for_flat_addition'
        else:
            attack_extra_baseline += flat_attack / base_attack
    if attack_kind == 'affine':
        custom = calculation['atkCalculation']
        params = {}
        resource = (calculation.get('sourceProof') or {}).get('resourceId')
        owner_uid = str(hit.get('event_inst_id') or '')
        stamp = (hit_ts_ms, hit_line_no)
        records = [record for record in condition_buff_records or []
                   if str(record.get('uid') or '') == owner_uid and record.get('event_key') == resource
                   and (int(record.get('ts_ms') or 0), int(record.get('line_no') or 0)) <= stamp
                   and (record.get('end_ts_ms') is None or stamp < (int(record['end_ts_ms']), int(record.get('end_line_no') or 2**63-1)))
                   and (record.get('snapshot_end_ts_ms') is None or stamp < (int(record['snapshot_end_ts_ms']), int(record.get('snapshot_end_line_no') or 2**63-1)))]
        for key in ('multiplier', 'addition'):
            parameter = custom.get(key) or {}
            raw = parameter.get('value') if parameter.get('useBlackboardKey') is False else (
                (records[0].get('bb_values') or {}).get(parameter.get('blackboardKey'))
                if parameter.get('useBlackboardKey') is True and len(records) == 1 else None)
            params[key] = _coerce_optional_float(raw)
        if params['addition'] is None:
            attack_baseline_error = 'missing_damage_base_addition'
        elif params['addition'] != 0:
            ability_factor = _coerce_optional_float(hit.get('attack_ability_factor'))
            coefficient = params['multiplier']
            if (base_attack is None or base_attack <= 0 or ability_factor is None or ability_factor <= 0
                    or coefficient is None or coefficient <= 0):
                attack_baseline_error = 'missing_attack_scale_for_fixed_damage'
            else:
                scale = base_attack * ability_factor * coefficient
                if not isfinite(scale) or scale <= 0:
                    attack_baseline_error = 'invalid_attack_scale_for_fixed_damage'
                else:
                    attack_extra_baseline += params['addition'] / scale
    if not isfinite(attack_extra_baseline):
        attack_baseline_error = 'invalid_attack_addition_ratio'
    if attack_baseline_error:
        if external_by_zone.get('atk'):
            hit['rdps_unresolved_effect_conditions'] = True
            if include_debug:
                ignored_effects.append({'zone': 'atk', 'reason': attack_baseline_error})
    elif attack_extra_baseline != 0:
        self_by_zone['atk'] += attack_extra_baseline
        if include_debug:
            contributors_by_zone['atk'].append(_rdps_debug_record_static_self(hit=hit, entry={
                'zone': 'atk', 'element': 'all', 'rate': attack_extra_baseline,
                'source_type': 'fixed_attack_basis', 'source_name': '固定攻击与基础伤害项',
                'evidence_kind': 'explicit_fixed_terms_normalization', 'base_attack': base_attack,
                'flat_attack': flat_attack}))

    if arts_strength_damage_hit:
        for entry in hit.get("static_arts_strength_entries") or []:
            if not isinstance(entry, dict):
                continue
            points = _coerce_optional_float(entry.get('rate'))
            if points is None:
                invalid_numeric_allocation = True
                continue
            if points <= 0:
                continue
            self_by_zone["arts_strength"] += points
            if include_debug:
                contributors_by_zone["arts_strength"].append(
                    _rdps_debug_record_static_self(hit=hit, entry=entry)
                )

    for zone in _DPD_ZONE_BUCKETS:
        bucket_value = _dpd_bucket_value_for_zone(zone, hit.get("dpd_raw"))
        if bucket_value is None:
            continue
        recognized_total = self_by_zone.get(zone, 0.0) + sum(
            rate for _, rate, _ in external_by_zone.get(zone, [])
        )
        residual = (bucket_value - 1.0) - recognized_total
        if abs(residual) > 0.003:
            hit["rdps_unresolved_dpd_residual"] = True

    m_values: dict[str, float] = {}
    external_sum_by_zone: dict[str, float] = {}
    combined_multiplier = 1.0
    if any(not isfinite(value) for value in (*self_by_zone.values(), *self_factor_by_zone.values(), *captured_by_attr_type.values())):
        invalid_numeric_allocation = True
    for zone, contributors in external_by_zone.items():
        if zone == 'atk' and attack_baseline_error:
            continue
        external_sum = sum(rate for _, rate, _ in contributors)
        if not isfinite(external_sum):
            invalid_numeric_allocation = True
            break
        if external_sum <= 0:
            continue
        self_sum = self_by_zone.get(zone, 0.0)
        if not isfinite(self_sum) or (100.0 if zone == 'arts_strength' else 1.0) + self_sum <= 0:
            invalid_numeric_allocation = True
            break
        if zone.startswith('prod_'):
            m_z = 1.0
            for _, rate, _ in contributors:
                m_z *= 1.0 + rate
        elif zone == "arts_strength":
            m_z = (
                _arts_strength_damage_multiplier(self_sum + external_sum)
                / _arts_strength_damage_multiplier(self_sum)
            ) ** (hit.get('arts_strength_damage_factor_count') or 1)
        elif zone == 'res':
            # Rates in this lane are positive resistance points. Remove only
            # approved external points from the observed resistance factor;
            # own reductions/ignore and rejected sources stay in the baseline.
            denominator = resistance_numerator - external_sum
            if not isfinite(denominator) or denominator <= 0:
                hit['rdps_unresolved_effect_conditions'] = True
                if include_debug:
                    ignored_effects.append({'zone': 'res', 'reason': 'resistance_counterfactual_unresolved',
                                            'detail': 'nonpositive_without_external_resistance_factor'})
                continue
            m_z = resistance_numerator / denominator
        else:
            m_z = (1.0 + self_sum + external_sum) / (1.0 + self_sum)
        if not isfinite(m_z):
            invalid_numeric_allocation = True
            break
        # DPD buckets remain a cross-check. An unexplained remainder cannot
        # become a self multiplier or calibrate the externally proven rate.
        if m_z <= 1.0:
            continue
        external_sum_by_zone[zone] = external_sum
        m_values[zone] = m_z
        combined_multiplier *= m_z
        if not isfinite(combined_multiplier):
            invalid_numeric_allocation = True
            break

    if invalid_numeric_allocation:
        # Invalid or overflowing coefficients cannot erase observed damage.
        # Keep the hit with its attacker and expose the failed allocation.
        hit['rdps_invalid_numeric_allocation'] = True
        hit['rdps_unresolved_effect_conditions'] = True
        m_values.clear()
        external_sum_by_zone.clear()
        external_by_zone.clear()
        self_by_zone.clear()
        self_factor_by_zone.clear()
        contributors_by_zone.clear()
        if include_debug:
            ignored_effects.append({'reason': 'invalid_numeric_allocation', 'reason_group': 'condition_unobservable'})

    hit_rdps_contributions: dict[str, float] = defaultdict(float)
    if not m_values:
        product_external = 1.0
        attacker_share = hit_value
        external_pool = 0.0
        hit_rdps_contributions[attacker_key] += hit_value
    else:
        product_external = 1.0
        for value in m_values.values():
            product_external *= value

        if product_external <= 1.0:
            attacker_share = hit_value
            external_pool = 0.0
            hit_rdps_contributions[attacker_key] += hit_value
        else:
            attacker_share = hit_value / product_external
            external_pool = hit_value - attacker_share
            hit_rdps_contributions[attacker_key] += attacker_share
            log_total = sum(log(value) for value in m_values.values())
            if log_total <= 0:
                hit_rdps_contributions[attacker_key] += external_pool
            else:
                for zone, contributors in external_by_zone.items():
                    if zone not in m_values:
                        continue
                    zone_share = external_pool * (log(m_values[zone]) / log_total)
                    zone_external_sum = external_sum_by_zone[zone]
                    if zone_external_sum <= 0:
                        hit_rdps_contributions[attacker_key] += zone_share
                        continue
                    for source_key, rate, record in contributors:
                        credit = zone_share * (rate / zone_external_sum)
                        if include_debug and record is not None:
                            record["rdps_credit"] = _round4(credit)
                        hit_rdps_contributions[source_key] += credit

    normalized_contributions = {
        character_key: value
        for character_key, value in hit_rdps_contributions.items()
        if value > 0.0
    }
    result: dict[str, Any] = {
        "allocation_policy": "log_marginal_v1",
        "allocation_policy_basis": "accounting_convention",
        "contributions_map": normalized_contributions,
        "contributions_list": _rdps_contribution_rows(normalized_contributions, sort_by_value=True),
    }

    if not include_debug:
        return result

    zone_external_share: dict[str, float] = defaultdict(float)
    log_total = sum(log(value) for value in m_values.values()) if product_external > 1.0 else 0.0
    if log_total > 0 and external_pool > 0:
        for zone in external_by_zone:
            if zone not in m_values:
                continue
            zone_external_share[zone] = external_pool * (log(m_values[zone]) / log_total)

    zones: list[dict[str, Any]] = []
    for zone in sorted(
        set(self_by_zone) | set(self_factor_by_zone) | set(external_by_zone),
        key=lambda key: (_RDPS_DEBUG_ZONE_ORDER.get(key, 99), key),
    ):
        self_rate = (self_factor_by_zone.get(zone, 1.0) - 1.0
                     if zone.startswith('prod_') else self_by_zone.get(zone, 0.0))
        external_rate = external_sum_by_zone.get(
            zone,
            sum(rate for _, rate, _ in external_by_zone.get(zone, [])),
        )
        total_rate = self_rate + external_rate
        total_multiplier = (
            self_factor_by_zone.get(zone, 1.0) * m_values.get(zone, 1.0)
            if zone.startswith('prod_')
            else
            resistance_numerator / 100.0
            if zone == 'res' and resistance_numerator is not None
            else
            _arts_strength_damage_multiplier(total_rate) ** (hit.get('arts_strength_damage_factor_count') or 1)
            if zone == "arts_strength"
            else 1.0 + total_rate
        )
        if zone.startswith('prod_'):
            total_rate = total_multiplier - 1.0
        bucket_value = _dpd_bucket_value_for_zone(zone, hit.get("dpd_raw"))
        zones.append(
            {
                "zone": zone,
                "zone_label": _RDPS_DEBUG_ZONE_LABELS.get(zone, zone),
                "self_rate": _round4(self_rate),
                "external_rate": _round4(external_rate),
                "total_rate": _round4(total_rate),
                "total_multiplier": _round4(total_multiplier),
                "external_multiplier": _round4(m_values.get(zone, 1.0)),
                **({'rate_unit': 'resistance_points', 'effective_resistance': effective_resistance,
                    'effective_resistance_source': resistance_source,
                    'without_external_resistance': effective_resistance + external_rate}
                   if zone == 'res' and effective_resistance is not None else {}),
                "zone_external_share": _round4(zone_external_share.get(zone, 0.0)),
                "dpd_bucket": (
                    {
                        "side": _DPD_ZONE_BUCKETS[zone][0],
                        "index": _DPD_ZONE_BUCKETS[zone][1],
                        "value": _round4(bucket_value),
                    }
                    if zone in _DPD_ZONE_BUCKETS and bucket_value is not None
                    else None
                ),
                "contributors": sorted(
                    contributors_by_zone.get(zone, []),
                    key=lambda item: (
                        0 if item.get("scope") == "external" else 1,
                        -float(item.get("rate") or 0.0),
                        str(item.get("source_character_name") or ""),
                        str(item.get("event_key") or ""),
                    ),
                ),
            }
        )

    external_sources: dict[str, dict[str, Any]] = {}
    for contributors in external_by_zone.values():
        for source_key, _rate, record in contributors:
            current = external_sources.setdefault(
                source_key,
                {
                    "character_key": source_key,
                    "character_name": (record or {}).get("source_character_name") or _resolve_character_name(source_key) or source_key,
                    "effect_count": 0,
                    "rdps_credit": 0.0,
                },
            )
            current["effect_count"] += 1
            if record is not None:
                current["rdps_credit"] += float(record.get("rdps_credit") or 0.0)

    modifier_details: list[dict[str, Any]] = []
    record_index = buff_records_by_uid if isinstance(buff_records_by_uid, dict) else {}
    for side, uids in (("attacker", sorted(attacker_modifier_uids)), ("defender", sorted(defender_modifier_uids))):
        for uid in uids:
            candidates = record_index.get(uid) or []
            if isinstance(candidates, dict):
                candidates = [candidates]
            stamp = (hit_ts_ms, hit_line_no)
            matched_records = [row for row in candidates
                if stamp >= (int(row.get('ts_ms') or 0), int(row.get('line_no') or 0))
                and (row.get('end_ts_ms') is None or stamp < (int(row['end_ts_ms']), int(row.get('end_line_no') or 0)))
                and (row.get('snapshot_end_ts_ms') is None or stamp < (int(row['snapshot_end_ts_ms']), int(row.get('snapshot_end_line_no') or 0)))
                and (hit_line_no or hit_ts_ms not in (row.get('ts_ms'), row.get('end_ts_ms'), row.get('snapshot_end_ts_ms')))]
            record = matched_records[0] if len(matched_records) == 1 else None
            if not isinstance(record, dict):
                modifier_details.append(
                    {
                        "side": side,
                        "uid": uid,
                        "event_key": "",
                        "event_name": "",
                        "source_character_name": "",
                        "target_name": "",
                        "bb_values": {},
                    }
                )
                continue
            target_name = (
                record.get("target_character_name")
                or record.get("target_enemy_name")
                or record.get("owner_raw")
                or ""
            )
            modifier_details.append(
                {
                    "side": side,
                    "uid": uid,
                    "event_key": record.get("event_key"),
                    "event_name": " / ".join(_collect_buff_labels(record)) or record.get("event_key") or "",
                    "source_character_key": record.get("source_character_key"),
                    "source_character_name": record.get("source_character_name"),
                    "target_key": record.get("target_character_key") or record.get("target_enemy_key"),
                    "target_name": target_name,
                    "bb_values": dict(record.get("bb_values") or {}),
                    "bb_keys": sorted(set(str(key) for key in record.get("bb_keys") or [])),
                }
            )

    result.update(
        {
            "zones": _sort_rdps_debug_zones(zones),
            "ignored_effects": ignored_effects,
            "product_external_multiplier": _round4(product_external),
            "attacker_share": _round4(attacker_share),
            "external_pool": _round4(external_pool),
            "external_sources": sorted(
                (
                    {
                        **value,
                        "rdps_credit": _round4(value["rdps_credit"]),
                    }
                    for value in external_sources.values()
                ),
                key=lambda item: (-float(item.get("rdps_credit") or 0.0), str(item.get("character_name") or "")),
            ),
            "zone_summary": " / ".join(
                f"{zone['zone_label']} x{float(zone['external_multiplier']):.4f}"
                for zone in _sort_rdps_debug_zones(zones)
                if float(zone.get("external_multiplier") or 1.0) > 1.0001
            ),
            "buff_source_summary": " / ".join(
                f"{source['character_name']} +{float(source['rdps_credit']):.1f}"
                for source in sorted(
                    (
                        {
                            **value,
                            "rdps_credit": _round4(value["rdps_credit"]),
                        }
                        for value in external_sources.values()
                    ),
                    key=lambda item: (-float(item.get("rdps_credit") or 0.0), str(item.get("character_name") or "")),
                )
                if float(source.get("rdps_credit") or 0.0) > 0.0001
            ),
            "packet_modifier_uids": {
                "attacker": sorted(attacker_modifier_uids),
                "defender": sorted(defender_modifier_uids),
            },
            "packet_modifier_details": modifier_details,
        }
    )
    return result


def _compute_rdps_damage_shares(
    hits: list[dict[str, Any]],
    buff_windows: list[dict[str, Any]],
    condition_buff_records: list[dict[str, Any]] | None = None,
) -> dict[str, float]:
    rdps_damage: dict[str, float] = defaultdict(float)
    condition_records_by_target: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in condition_buff_records or []:
        target = str(record.get('target_enemy_key') or record.get('target_character_key') or '')
        condition_records_by_target[target].append(record)

    for hit in hits:
        allocation = _allocate_rdps_for_hit(
            hit,
            buff_windows,
            include_debug=False,
            condition_buff_records=condition_buff_records,
            condition_records_by_target=condition_records_by_target,
        )
        normalized_contributions = dict(allocation.get("contributions_map") or {})
        hit["rdps_contributions"] = normalized_contributions
        for character_key, value in normalized_contributions.items():
            rdps_damage[character_key] += value

    return dict(rdps_damage)


def _build_rdps_damage_basis(
    hits: list[dict[str, Any]],
    rdps_damage: dict[str, float],
    total_damage: int,
) -> dict[str, Any]:
    total_rdps_contribution = sum(float(value or 0.0) for value in rdps_damage.values())
    hit_conservation_deltas: list[float] = []
    for hit in hits:
        contribution_total = sum(
            float(value or 0.0)
            for value in (hit.get("rdps_contributions") or {}).values()
        )
        hit_conservation_deltas.append(contribution_total - float(hit.get("hit_value") or 0.0))

    packet_final_values = [
        float(hit["packet_final_value"])
        for hit in hits
        if hit.get("packet_final_value") is not None
    ]
    packet_raw_values = [
        float(hit["packet_raw_value"])
        for hit in hits
        if hit.get("packet_raw_value") is not None
    ]
    conservation_delta = total_rdps_contribution - float(total_damage)
    max_hit_delta = max((abs(delta) for delta in hit_conservation_deltas), default=0.0)
    hit_mismatch_count = sum(1 for delta in hit_conservation_deltas if abs(delta) > 0.01)
    return {
        "mode": "packet_hp_loss",
        "rdps_mode": "packet_hp_loss",
        "allocation_policy": "log_marginal_v1",
        "allocation_policy_basis": "accounting_convention",
        "source": "HP_V2.hit with packet eHP overkill cap",
        "hit_count": len(hits),
        "packet_grounded_hit_count": sum(1 for hit in hits if hit.get("packet_hit_value") is not None),
        "packet_hit_count": sum(1 for hit in hits if hit.get("packet_hit_value") is not None),
        "packet_final_value_count": len(packet_final_values),
        "packet_raw_value_count": len(packet_raw_values),
        "formula_grounded_hit_count": 0,
        "missing_packet_hit_count": sum(1 for hit in hits if hit.get("packet_hit_value") is None),
        "overkill_capped_hit_count": sum(1 for hit in hits if int(hit.get("overkill_damage") or 0) > 0),
        "total_damage": total_damage,
        "total_packet_final_value": _round4(sum(packet_final_values)) if packet_final_values else None,
        "total_packet_raw_value": _round4(sum(packet_raw_values)) if packet_raw_values else None,
        "total_rdps_contribution": _round4(total_rdps_contribution),
        "rdps_conservation_delta": _round4(conservation_delta),
        "rdps_conservation_ok": abs(conservation_delta) <= 0.01 and hit_mismatch_count == 0,
        "hit_conservation_mismatch_count": hit_mismatch_count,
        "max_hit_conservation_delta": _round4(max_hit_delta),
    }


def _resolve_weapon_buff_sources(buff_records: list[dict[str, Any]]) -> None:
    return


def _buff_record_end_ts_ms(record: dict[str, Any], battle_end_ms: int) -> int:
    start_ts_ms = int(record["ts_ms"])
    duration_ms = _normalize_buff_duration_ms(
        start_ts_ms,
        event_key=record.get("event_key"),
        end_ts_ms=record.get("end_ts_ms"),
        raw_duration_ms=record.get("raw_duration_ms"),
        battle_end_ms=battle_end_ms,
    )
    end = start_ts_ms + duration_ms
    return min(end, int(record['snapshot_end_ts_ms'])) if record.get('snapshot_end_ts_ms') is not None else end


def _matches_hidden_combo_rule(buff_id: str) -> dict[str, Any] | None:
    for rule in _HIDDEN_COMBO_RULES:
        if any(pattern.search(buff_id) for pattern in rule["buff_patterns"]):
            return rule
    return None


def _build_hidden_combo_windows(
    hits: list[dict[str, Any]],
    buff_starts: list[dict[str, Any]],
    *,
    battle_end_ms: int,
) -> list[dict[str, Any]]:
    combo_windows: list[dict[str, Any]] = []
    ordered_hits = sorted(hits, key=lambda item: item["ts_ms"])

    for buff_record in buff_starts:
        source_key = buff_record.get("source_character_key")
        target_key = buff_record.get("target_character_key")
        buff_id = _normalize_buff_id(buff_record.get("event_key"))
        rule = _matches_hidden_combo_rule(buff_id)
        if not source_key or not target_key or rule is None:
            continue

        buff_start_ms = int(buff_record["ts_ms"])
        buff_end_ms = _buff_record_end_ts_ms(buff_record, battle_end_ms)

        for hit in ordered_hits:
            if hit["character_key"] != target_key:
                continue
            if not (buff_start_ms <= hit["ts_ms"] <= buff_end_ms):
                continue

            combo_context = _resolve_combo_consume_context(hit)
            if combo_context is None:
                continue
            combo_group_type, skill_family_key = combo_context
            rate = rule["skill_group_rates"].get(combo_group_type)
            if rate is None or rate <= 0:
                continue

            combo_windows.append(
                {
                    "start_ts_ms": hit["ts_ms"],
                    "end_ts_ms": min(hit["ts_ms"] + int(rule["cast_window_ms"]), battle_end_ms),
                    "duration_ms": min(int(rule["cast_window_ms"]), max(battle_end_ms - hit["ts_ms"], 1)),
                    "source_character_key": source_key,
                    "source_character_name": buff_record.get("source_character_name"),
                    "target_character_key": target_key,
                    "target_character_name": buff_record.get("target_character_name"),
                    "target_player_key": target_key,
                    "target_enemy_key": None,
                    "event_key": f"{buff_id}::{skill_family_key}",
                    "event_name": "连击增伤",
                    "zone_effects": [{"zone": "combo", "element": "all", "rate": rate}],
                    "skill_family_key": skill_family_key,
                }
            )
            break

    return combo_windows


def _build_combo_consume_windows(
    hits: list[dict[str, Any]],
    buff_starts: list[dict[str, Any]],
    *,
    battle_end_ms: int,
) -> list[dict[str, Any]]:
    combo_windows: list[dict[str, Any]] = []
    ordered_hits = sorted(hits, key=lambda item: item["ts_ms"])
    trigger_records: list[dict[str, Any]] = []
    consume_records: list[dict[str, Any]] = []

    for buff_record in buff_starts:
        buff_id = _normalize_buff_id(buff_record.get("event_key"))
        source_key = buff_record.get("source_character_key")
        target_key = buff_record.get("target_character_key")
        if not source_key or not target_key:
            continue

        if buff_id in _GENERIC_COMBO_IMBUE_BUFF_IDS:
            consume_records.append(
                {
                    "buff_record": buff_record,
                    "source_key": source_key,
                    "target_key": target_key,
                    "start_ms": int(buff_record["ts_ms"]),
                }
            )
            continue

        if buff_id != _GENERIC_COMBO_TRIGGER_BUFF_ID:
            continue

        buff_start_ms = int(buff_record["ts_ms"])
        buff_end_ms = _buff_record_end_ts_ms(buff_record, battle_end_ms)
        trigger_records.append(
            {
                "buff_record": buff_record,
                "source_key": source_key,
                "target_key": target_key,
                "start_ms": buff_start_ms,
                "end_ms": buff_end_ms,
            }
        )

    trigger_records.sort(
        key=lambda record: (
            int(record["start_ms"]),
            str(record["source_key"] or ""),
            str(record["target_key"] or ""),
        )
    )
    trigger_groups: list[dict[str, Any]] = []
    for record in trigger_records:
        group = next(
            (
                candidate
                for candidate in reversed(trigger_groups)
                if candidate["source_key"] == record["source_key"]
                and abs(int(record["start_ms"]) - int(candidate["start_ms"])) <= _COMBO_TRIGGER_GROUP_WINDOW_MS
            ),
            None,
        )
        if group is None:
            group = {
                "source_key": record["source_key"],
                "start_ms": record["start_ms"],
                "records": [],
            }
            trigger_groups.append(group)
        group["records"].append(record)

    indexed_hits = list(enumerate(ordered_hits))
    matched_layers: list[dict[str, Any]] = []
    for group in trigger_groups:
        group_start_ms = min(int(record["start_ms"]) for record in group["records"])
        group_end_ms = max(int(record["end_ms"]) for record in group["records"])
        group_source_key = group["source_key"]

        consume_candidates = [
            record
            for record in consume_records
            if record["source_key"] == group_source_key
            and group_start_ms < int(record["start_ms"]) <= group_end_ms + _COMBO_TRIGGER_GROUP_WINDOW_MS
        ]
        if not consume_candidates:
            continue

        consume_record = min(
            consume_candidates,
            key=lambda record: (int(record["start_ms"]), str(record["target_key"] or "")),
        )
        consume_ts_ms = int(consume_record["start_ms"])
        target_key = consume_record["target_key"]
        source_key = consume_record["source_key"]

        hit_candidate: tuple[int, dict[str, Any], tuple[int, str]] | None = None
        for hit_index, hit in indexed_hits:
            if hit["character_key"] != target_key:
                continue
            if not (consume_ts_ms <= hit["ts_ms"] <= consume_ts_ms + _COMBO_CONSUME_MAX_WINDOW_MS):
                continue
            combo_context = _resolve_combo_consume_context(hit)
            if combo_context is None:
                continue
            hit_candidate = (hit_index, hit, combo_context)
            break

        if hit_candidate is None:
            continue

        hit_index, _hit, combo_context = hit_candidate
        combo_group_type, skill_family_key = combo_context
        matched_layers.append(
            {
                "consume_record": consume_record,
                "consume_ts_ms": consume_ts_ms,
                "target_key": target_key,
                "source_key": source_key,
                "source_record": group["records"][0]["buff_record"],
                "group_start_ms": group_start_ms,
                "hit_index": hit_index,
                "combo_group_type": combo_group_type,
                "skill_family_key": skill_family_key,
            }
        )

    consume_groups: dict[tuple[int, str, int, int, str], list[dict[str, Any]]] = {}
    for layer in matched_layers:
        group_key = (
            int(layer["consume_ts_ms"]),
            str(layer["target_key"] or ""),
            int(layer["hit_index"]),
            int(layer["combo_group_type"]),
            str(layer["skill_family_key"] or ""),
        )
        consume_groups.setdefault(group_key, []).append(layer)

    for layers in consume_groups.values():
        layers.sort(key=lambda layer: (int(layer["group_start_ms"]), str(layer["source_key"] or "")))
        marginal_rates = _combo_marginal_layer_rates(int(layers[0]["combo_group_type"]), len(layers))
        if not marginal_rates:
            continue

        capped_layers = layers[: len(marginal_rates)]
        total_rate = round(sum(marginal_rates), 6)
        rates_by_source: dict[str, dict[str, Any]] = {}
        for layer, rate in zip(capped_layers, marginal_rates):
            if rate <= 0:
                continue
            source_key = str(layer["source_key"] or "")
            if not source_key:
                continue
            bucket = rates_by_source.setdefault(
                source_key,
                {
                    "rate": 0.0,
                    "layers": [],
                    "first_layer": layer,
                },
            )
            bucket["rate"] = float(bucket["rate"]) + float(rate)
            bucket["layers"].append(layer)

        for source_key, bucket in rates_by_source.items():
            rate = round(float(bucket["rate"]), 6)
            if rate <= 0:
                continue
            first_layer = bucket["first_layer"]
            consume_record = first_layer["consume_record"]
            consume_buff_record = consume_record["buff_record"]
            source_buff_record = first_layer["source_record"]
            consume_ts_ms = int(first_layer["consume_ts_ms"])
            target_key = str(first_layer["target_key"] or "")
            skill_family_key = str(first_layer["skill_family_key"] or "")
            window_end_ms = min(consume_ts_ms + _COMBO_CONSUME_MAX_WINDOW_MS, battle_end_ms)
            projection_key = f"{_GENERIC_COMBO_TRIGGER_BUFF_ID}::{skill_family_key}"
            approval = _rdps_registry_verified_effect_entry({
                'event_key': projection_key, 'source_character_key': source_key, 'target_character_key': target_key})
            if not approval or not any(spec.get('zone') == 'combo' and spec.get('element') == 'all'
                                       for spec in approval.get('effects') or []):
                continue
            combo_windows.append(
                {
                    "start_ts_ms": consume_ts_ms,
                    "end_ts_ms": window_end_ms,
                    "duration_ms": max(window_end_ms - consume_ts_ms, 1),
                    "source_character_key": source_key,
                    "source_character_name": source_buff_record.get("source_character_name"),
                    "target_character_key": target_key,
                    "target_character_name": consume_buff_record.get("target_character_name"),
                    "target_player_key": target_key,
                    "target_enemy_key": None,
                    "event_key": projection_key,
                    "event_name": "连击增伤",
                    "zone_effects": [{"zone": "combo", "element": "all", "rate": rate,
                                      "external_rdps_allowed": True,
                                      "rdps_whitelist_keys": list(approval.get('_rdps_whitelist_keys') or [])}],
                    "skill_family_key": skill_family_key,
                    "combo_stack_count": len(capped_layers),
                    "combo_layer_count": len(bucket["layers"]),
                    "combo_total_rate": total_rate,
                    "combo_rate_model": "ordered_marginal_stack",
                }
            )

    return combo_windows


def _build_special_combo_windows(
    hits: list[dict[str, Any]],
    buff_starts: list[dict[str, Any]],
    *,
    battle_end_ms: int,
) -> list[dict[str, Any]]:
    # The packet names the modifier on this exact hit. The two explicit
    # creation origins identify its provider; no nearby cast is selected.
    trigger_id = 'buff_common_affixes_combo_trigger'
    imbue_id = 'buff_common_affixes_skillimbue'
    modifier_id = 'buff_common_affixes_skillimbue_atk'
    roots = _load_current_buff_roots()
    if not all(isinstance(roots.get(key), dict) for key in (trigger_id, imbue_id, modifier_id)):
        return []
    for parent_id, action_id, child_id in ((trigger_id, 4, imbue_id), (imbue_id, 0, modifier_id)):
        actions = []; pending = [(roots[parent_id], True)]
        while pending:
            node, enabled = pending.pop()
            if isinstance(node, dict):
                enabled = enabled and ('serverActionIndex' not in node or node.get('isEnable') is True)
                if (node.get('$type') == 'Beyond.Gameplay.Core.CreateBuffAction.Data'
                        and type(node.get('serverActionIndex')) is int and node['serverActionIndex'] == action_id):
                    actions.append((node, enabled))
                pending.extend((value, enabled) for value in node.values())
            elif isinstance(node, list):
                pending.extend((value, enabled) for value in node)
        if len(actions) != 1 or actions[0][1] is not True:
            return []
        action = actions[0][0]
        created = action.get('buffs') or []
        if (len(created) != 1 or created[0].get('readIdFromBlackboard') is not False
                or created[0].get('buffId') != child_id or created[0].get('assignBlackboard') is not True
                or action.get('buffSource') != 'ActionSource'
                or (action.get('targetSettings') or {}).get('targetSource') != 'Owner'):
            return []
        assigned = [row for row in created[0].get('assignItems') or []
                    if isinstance(row, dict) and row.get('targetKey') == 'imbue_scale']
        if (len(assigned) != 1 or assigned[0].get('useDirectValue') is not False
                or assigned[0].get('inputValueKey') != 'imbue_scale'):
            return []
    groups = roots[modifier_id].get('damageModifier') or []
    if len(groups) != 1 or groups[0].get('enableSide') != 'Attacker':
        return []
    processors = groups[0].get('damageProcessors') or []
    if (len(processors) != 1 or processors[0].get('$type') != 'Beyond.Gameplay.Core.DamageScaleProcessor'
            or processors[0].get('side') != 'Attacker' or processors[0].get('zoneName') != 'ComboCalcZone'
            or (processors[0].get('addition') or {}).get('blackboardKey') != 'real_imbue_scale'
            or (processors[0].get('addition') or {}).get('useBlackboardKey') is not True):
        return []
    conditions = (groups[0].get('condition') or {}).get('actionData') or []
    if (len(conditions) != 3 or any(row.get('isEnable') is not True for row in conditions)
            or conditions[0].get('$type') != 'Beyond.Gameplay.Core.Conditions.CheckSkillCastId.Data'
            or conditions[1].get('$type') != 'Beyond.Gameplay.Core.Conditions.CheckDamageDecorateMask.Data'
            or conditions[1].get('checkType') != 'HasAny' or conditions[1].get('mask') != 0x300
            or conditions[2].get('$type') != 'Beyond.Gameplay.Core.IfElseAction.IfElseActionData'):
        return []
    branch = conditions[2]
    checks = (branch.get('conditionAction') or {}).get('actionData') or []
    succeeds = (branch.get('succeedActions') or {}).get('actionData') or []
    failures = (branch.get('failActions') or {}).get('actionData') or []
    if (len(checks) != 1 or len(succeeds) != 1 or len(failures) != 1
            or any(row.get('isEnable') is not True for row in (*checks, *succeeds, *failures))
            or checks[0].get('$type') != 'Beyond.Gameplay.Core.Conditions.CheckDamageDecorateMask.Data'
            or checks[0].get('checkType') != 'HasAny' or checks[0].get('mask') != 0x100
            or succeeds[0].get('$type') != 'Beyond.Gameplay.Core.SimpleCalcBBAction.Data'
            or succeeds[0].get('operation') != 'Multiply' or succeeds[0].get('key') != 'real_imbue_scale'
            or (succeeds[0].get('value1') or {}).get('useBlackboardKey') is not True
            or (succeeds[0].get('value1') or {}).get('blackboardKey') != 'imbue_scale'
            or (succeeds[0].get('value2') or {}).get('useBlackboardKey') is not False
            or failures[0].get('$type') != 'Beyond.Gameplay.Core.ModifyDynamicBlackboard.Data'
            or failures[0].get('operation') != 'Assign' or failures[0].get('directValue') is not True
            or failures[0].get('key') != 'real_imbue_scale'
            or (failures[0].get('value') or {}).get('useBlackboardKey') is not True
            or (failures[0].get('value') or {}).get('blackboardKey') != 'imbue_scale'):
        return []
    skill_factor = _coerce_optional_float(succeeds[0]['value2'].get('value'))
    if skill_factor is None or skill_factor <= 0:
        return []
    by_uid: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in buff_starts:
        if record.get('event_key') in {trigger_id, imbue_id, modifier_id}:
            by_uid[str(record.get('uid') or '')].append(record)
    windows = []
    for hit in hits:
        raw_handles = (hit.get('packet_modifier_handles') or {}).get('attacker') or []
        grouped_handles: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
        for handle in raw_handles:
            if isinstance(handle, dict) and str(handle.get('buff_inst_id') or '') in by_uid:
                grouped_handles[str(handle['buff_inst_id'])].append(handle)
        for uid, handles in grouped_handles.items():
            if not any(row.get('event_key') == modifier_id for row in by_uid[uid]):
                continue
            rejection = None
            session = hit.get('packet_session_id')
            seq = hit.get('packet_seq_id')
            stamp = (hit.get('ts_ms'), hit.get('line_no'))
            if (not isinstance(session, str) or not session or type(seq) is not int or seq < 0
                    or any(type(value) is not int for value in stamp) or stamp[1] <= 0):
                rejection = 'missing_combo_hit_identity'
            elif (any(type(row.get('index')) is not int or row['index'] != 0 for row in handles)
                  or len({json.dumps(row, sort_keys=True, ensure_ascii=False) for row in handles}) != 1):
                rejection = 'conflicting_combo_modifier_handle'
            mask = hit.get('damage_decorate_mask')
            scope_source = 'observed_damage_decorate_mask'
            if mask is None:
                mask = hit.get('static_damage_decorate_mask')
                scope_source = hit.get('static_damage_decorate_mask_source')
                if scope_source != 'current_typed_skill_or_buff_exact_unit_generation_only':
                    mask = None
            if type(mask) is not int or mask < 0 or not mask & 0x300:
                rejection = rejection or 'unresolved_or_inapplicable_combo_skill_scope'
            selected = []; chain = []; origins_used = []; anchors_used = []
            current_uid, expected_id, point = uid, modifier_id, stamp
            for parent_id, expected_action in ((imbue_id, 0), (trigger_id, 4), (None, None)):
                if rejection:
                    break
                candidates = []
                for record in by_uid.get(current_uid, []):
                    if record.get('event_key') != expected_id:
                        continue
                    start = (record.get('ts_ms', record.get('start_ts_ms')), record.get('line_no', 0))
                    if any(type(value) is not int for value in start) or point < start:
                        continue
                    cutoffs = [(record.get(name), record.get(line_name)) for name, line_name in (
                        ('end_ts_ms', 'end_line_no'), ('snapshot_end_ts_ms', 'snapshot_end_line_no'))]
                    if any(type(end) is int and (point[0] > end or point[0] == end
                           and type(line) is int and point[1] >= line) for end, line in cutoffs):
                        continue
                    candidates.append(record)
                if len(candidates) != 1:
                    rejection = 'ambiguous_or_missing_combo_instance'
                    break
                record = candidates[0]
                if (record.get('rdps_live_identity_conflict') or record.get('rdps_dynamic_identity_conflict')
                        or not record.get('source_character_key')
                        or record.get('target_actor_id') != hit.get('source_actor_id')
                        or record.get('target_character_key') != hit.get('character_key')
                        or any(type(record.get(key)) is not str or not re.fullmatch(r'[1-9]\d*', record[key])
                               or int(record[key]) >= 2**64 for key in ('uid', 'source_actor_id', 'target_actor_id'))
                        or chain and (record['source_actor_id'] != chain[0]['source_actor_id']
                                      or record['source_character_key'] != chain[0]['source_character_key'])):
                    rejection = 'conflicting_combo_provider_or_recipient'
                    break
                chain.append(record)
                if parent_id is None:
                    break
                origins = record.get('origin_observations') or []
                normalized_origins = []
                for origin in origins:
                    parent = origin.get('templateStrId')
                    if parent in (None, '') and type(origin.get('templateIntId')) is int:
                        parent = _load_num_id_str_buff_map().get(str(origin['templateIntId']))
                    elif not isinstance(parent, str) or not parent or origin.get('templateIntId') is not None:
                        parent = None
                    if (origin.get('_boundCreateRecord') is not True or parent != parent_id
                            or origin.get('buffUid') != current_uid or origin.get('buffTemplate') != expected_id
                            or origin.get('sourceActorId') != record['source_actor_id']
                            or origin.get('targetActorId') != record['target_actor_id']
                            or str(origin.get('environmentType') or '').lower() != 'buff'
                            or str(origin.get('ownerType')) != '1'
                            or type(origin.get('actionId')) is not int or origin['actionId'] != expected_action
                            or origin.get('triggerType') != 0 or type(origin.get('operationSeq')) is not int
                            or type(origin.get('environmentInstId')) is not str
                            or not re.fullmatch(r'[1-9]\d*', origin['environmentInstId'])):
                        rejection = 'invalid_combo_creation_origin'
                        break
                    normalized_origins.append(origin)
                parent_uids = {row['environmentInstId'] for row in normalized_origins}
                if rejection or not normalized_origins or len(parent_uids) != 1:
                    rejection = rejection or 'missing_or_conflicting_combo_creation_origin'
                    break
                anchors = [row for row in record.get('creation_observations') or []
                           if row.get('sessionId') == session and row.get('buffUid') == current_uid
                           and row.get('buffTemplate') == expected_id and row.get('sourceActorId') == record['source_actor_id']
                           and row.get('targetActorId') == record['target_actor_id']
                           and row.get('sourceEvent') == 'BattleOpTriggerAction'
                           and type(row.get('operationSeq')) is int and row['operationSeq'] <= seq
                           and any(row['operationSeq'] == origin['operationSeq'] for origin in normalized_origins)]
                if not anchors or any(not isinstance(row.get('assignedItems'), list) for row in anchors):
                    rejection = 'missing_combo_creation_parameters'
                    break
                rates = set()
                for anchor in anchors:
                    values = [row for row in anchor['assignedItems'] if isinstance(row, dict) and row.get('target_key') == 'imbue_scale']
                    if (not values or any(row.get('use_direct_value') is not True or row.get('string_value')
                                          or _coerce_optional_float(row.get('numeric_value')) is None for row in values)
                            or 'imbue_scale' in (anchor.get('bb_conflicts') or []) or '*' in (anchor.get('bb_conflicts') or [])):
                        rejection = 'invalid_combo_creation_rate'
                        break
                    rates.update(float(row['numeric_value']) for row in values)
                if rejection or len(rates) != 1 or next(iter(rates)) <= 0:
                    rejection = rejection or 'conflicting_combo_creation_rate'
                    break
                selected.append(next(iter(rates)))
                points = {(row.get('ts_ms'), row.get('line_no')) for row in anchors}
                if len(points) != 1 or any(type(value) is not int for value in next(iter(points))):
                    rejection = 'ambiguous_combo_creation_boundary'
                    break
                point = next(iter(points))
                current_uid, expected_id = next(iter(parent_uids)), parent_id
                origins_used.extend(normalized_origins)
                anchors_used.extend(anchors)
            if not rejection and (len(chain) != 3 or len(selected) != 2 or selected[0] != selected[1]):
                rejection = 'combo_forwarded_rate_conflict'
            if not rejection:
                states = chain[0].get('enable_observations') or []
                prior = [row for row in states if type(row.get('operationSeq')) is int and row['operationSeq'] <= seq]
                if prior:
                    latest = max(row['operationSeq'] for row in prior)
                    enabled = {row.get('enabled') for row in prior if row['operationSeq'] == latest}
                    if enabled != {True}:
                        rejection = 'disabled_or_conflicting_combo_modifier'
                elif chain[0].get('rdps_conflicting_enable_state'):
                    rejection = 'disabled_or_conflicting_combo_modifier'
            if rejection:
                hit['rdps_unresolved_effect_conditions'] = True
                hit.setdefault('combo_unresolved_reasons', []).append({'uid': uid, 'reason': rejection})
                continue
            projection_key = f'{trigger_id}::packet/{session}/{seq}/{stamp[1]}'
            approval = _rdps_registry_verified_effect_entry({'event_key': projection_key,
                'source_character_key': chain[0]['source_character_key'], 'target_character_key': hit['character_key']})
            if (not approval or not approval.get('_rdps_whitelist_keys')
                    or not any(row.get('zone') == 'combo' and row.get('element') == 'all' for row in approval.get('effects') or [])):
                continue
            rate = selected[0] * (skill_factor if mask & 0x100 else 1.0)
            if not isfinite(rate) or rate <= 0:
                continue
            windows.append({'uid': uid, 'start_ts_ms': stamp[0], 'end_ts_ms': stamp[0],
                'start_line_no': stamp[1], 'end_line_no': stamp[1] + 1, 'line_no': stamp[1],
                'snapshot_update': True, 'snapshot_end_ts_ms': stamp[0], 'snapshot_end_line_no': stamp[1] + 1,
                'duration_ms': 1, 'stacking_type': (roots[modifier_id].get('stackingSettings') or {}).get('stackingType'),
                'stack_limit': None, 'source_character_key': chain[0]['source_character_key'],
                'source_character_name': chain[0].get('source_character_name'), 'source_actor_id': chain[0]['source_actor_id'],
                'target_character_key': hit['character_key'], 'target_character_name': hit.get('character_name') or hit['character_key'],
                'target_actor_id': hit['source_actor_id'],
                'target_player_key': hit['character_key'], 'target_enemy_key': None,
                'event_key': projection_key, 'event_name': '连击增伤', 'skill_family_key': None,
                'zone_effects': [{'zone': 'combo', 'element': 'all', 'rate': rate,
                                  'external_rdps_allowed': True, 'rdps_whitelist_keys': list(approval['_rdps_whitelist_keys'])}],
                'combo_rate_model': 'exact_packet_modifier_and_creation_chain', 'combo_imbue_scale': selected[0],
                'combo_skill_scope_mask': mask & 0x300, 'combo_skill_scope_source': scope_source,
                'combo_source_uids': [row['uid'] for row in reversed(chain)],
                'combo_origins': copy.deepcopy(origins_used), 'combo_creation_observations': copy.deepcopy(anchors_used),
                'combo_hit_identity': {'sessionId': session, 'operationSeq': seq, 'lineNo': stamp[1],
                                       'targetActorId': hit.get('target_actor_id'), 'damageUnitIndex': hit.get('damage_unit_index')}})
    return windows


def parse_raw_battle_log_text(
    text: str,
    *,
    file_name: str | None = None,
    first_hit_hint: str | None = None,
    last_hit_hint: str | None = None,
    reference_date: date | None = None,
    include_rdps_debug: bool = False,
    loadout_override_by_char: dict[str, dict[str, Any]] | None = None,
    critical_attributes_override_by_actor: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    reference_date = reference_date or datetime.now().astimezone().date()
    text = _apply_short_actor_aliases(text)
    context_enemy_hint = _infer_context_enemy_hint(text)
    trace_identity_inference_seen = " IDENTITY_INFERENCE " in text
    first_hit_hint_ms = _parse_hint_timestamp_ms(first_hit_hint)
    last_hit_hint_ms = _parse_hint_timestamp_ms(last_hit_hint)

    hits: list[dict[str, Any]] = []
    unresolved_hits: list[dict[str, Any]] = []
    unresolved_buff_actions: list[dict[str, Any]] = []
    hits_by_seq: dict[int, dict[str, Any]] = {}
    critical_stats_by_actor = copy.deepcopy(critical_attributes_override_by_actor or {})
    baseline_by_character: dict[str, dict[int, float]] = {}
    buff_starts: list[dict[str, Any]] = []
    roster_order: list[str] = []
    roster_seen: set[str] = set()
    participant_totals = _new_participant_totals()
    skill_totals = _new_skill_totals()
    enemy_counter: Counter[str] = Counter()
    last_enemy_hp_by_target: dict[str, int] = {}
    active_buff_starts_by_uid: dict[str, dict[str, Any]] = {}
    packet_modifier_last_seen_by_uid: dict[str, int] = {}
    pending_attr_mods: dict[str, list[dict[str, str]]] = defaultdict(list)
    pending_buff_record: dict[str, Any] | None = None
    last_chr_src_on_owner: dict[str, tuple[str, int]] = {}
    recent_skill_casts_by_char: dict[str, list[dict[str, Any]]] = defaultdict(list)
    skill_casts: list[dict[str, Any]] = []
    active_skill_casts_by_inst: dict[str, dict[str, Any]] = {}
    # ATB_UPDATE（技力更新，v34+ 客户端 BattleOpUpdateAtbInfo）：主控角色重击回技力
    # = reason=AddValue 正 delta。排轴导出据此标记「回能量重击」区分主控 vs AI。
    # 按角色收集技力增加时刻。
    atb_gain_ms_by_char: dict[str, list[int]] = defaultdict(list)
    # CHAR_SKILLS（bridge 自 SC_SELF_SCENE_INFO_SKILLS 落盘）：角色技能等级清单，
    # 供排轴导出 API 消费；同角色多次同步取最后一次。
    char_skill_levels: dict[str, dict[str, int]] = {}
    squad_actor_keys: dict[str, str] = {}
    first_party_action_ms: int | None = None
    recent_numeric_buffs_by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    dungeon_context: tuple[str, str] | None = None
    dungeon_context_id: str | None = None
    party_actor_ids: set[str] = set()
    entity_stats_timelines: dict[str, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    dynamic_bb_events: list[dict[str, Any]] = []
    buff_attribute_refresh_events: list[dict[str, Any]] = []
    buff_creation_operation_history: dict[tuple[Any, ...], dict[str, Any]] = {}
    # A typed root is immutable during a parse. Keep its exact object alive and
    # index every integer action identity once; duplicate identities and the
    # inherited enabled state remain part of each lookup's existing guards.
    # This cache is local so another parse/root/version cannot reuse its proof.
    buff_root_action_indices: dict[int, tuple[Any, dict[int, list[tuple[dict[str, Any], bool]]],
                                                   list[tuple[dict[str, Any], bool]]]] = {}
    game_timer_start_ms: int | None = None
    game_timer_end_ms: int | None = None
    game_timer_elapsed_ms: int | None = None
    game_timer_window_elapsed_ms: int | None = None
    official_timer_start_ms: int | None = None
    official_timer_elapsed_ms: int | None = None
    official_timer_end_ms: int | None = None
    live_timer_elapsed_ms: int | None = None
    packet_timer_elapsed_ms: int | None = None
    timer_start_seen = False
    timer_end_seen = False
    timer_window_valid = True
    challenge_pass_confirmed = False
    official_pass_confirmed = False
    completion_source = ""
    active_suits_by_char: dict[str, set[str]] = {}
    weapon_override_by_char = {
        str(char_key): copy.deepcopy(row)
        for char_key, row in (loadout_override_by_char or {}).items()
        if isinstance(row, dict)
    }
    active_weapons_by_char: dict[str, str] = {
        char_key: str(row.get("weapon_template") or "")
        for char_key, row in weapon_override_by_char.items()
        if str(row.get("weapon_template") or "")
    }
    loadout_by_char: dict[str, dict[str, Any]] = copy.deepcopy(weapon_override_by_char)
    loadout_groups: list[dict[str, Any]] = []
    current_loadout_group: dict[str, Any] | None = None
    first_context_ms: int | None = None
    last_context_ms: int | None = None
    contract_tag_ids: list[int] = []
    contract_tag_score: int | None = None
    poise_damage_events: list[dict[str, Any]] = []

    def set_dungeon_context(raw_dungeon_id: str | None) -> None:
        nonlocal dungeon_context, dungeon_context_id
        context_id = str(raw_dungeon_id or "").strip()
        if not context_id:
            return
        dungeon_context_id = context_id
        # The official stage id is authoritative. If the newest official id
        # is not mapped, clear any older resolved context instead of silently
        # keeping or guessing a leaderboard from an enemy target.
        dungeon_context = _resolve_dungeon_context(context_id)

    timestamp_clock_origin: int | None = None
    timer_observed_times: dict[int, int] = {}
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        if "GAME_TIMER_RESET source=CAPTURE_SESSION_START" in raw_line:
            critical_stats_by_actor.clear()
        ts_ms = _parse_prefixed_timestamp_ms(raw_line)
        if ts_ms is None:
            continue
        # Legacy trace timestamps are time-of-day, not dated instants. Express
        # them in one fixed modulo-24h coordinate system for this segment.
        # Never increment a day on a decreasing observation: late/replayed
        # events must keep the same coordinate. This is not server elapsed
        # time and is never used to validate or reconstruct passTime.
        if timestamp_clock_origin is None:
            timestamp_clock_origin = ts_ms
        ts_ms = timestamp_clock_origin + (ts_ms - timestamp_clock_origin) % 86_400_000
        if 'TIMER_' in raw_line or 'GAME_INST_' in raw_line or 'GAME_MECHANICS_TIME_FREEZE' in raw_line:
            timer_observed_times[line_no] = ts_ms
        if first_context_ms is None:
            first_context_ms = ts_ms
        last_context_ms = ts_ms
        party_actor_ids.update(_collect_party_actor_ids(raw_line))
        if " SQUAD " in raw_line:
            members = [(match.group(2), match.group(1)) for match in _SQUAD_MEMBER_RE.finditer(raw_line)]
            squad_actor_keys = dict(members) if len({pair[0] for pair in members}) == len(members) else {}

        entity_stats_snapshot = _parse_entity_stats_snapshot(raw_line)
        if entity_stats_snapshot is not None:
            entity_id, snapshot = entity_stats_snapshot
            snapshot["ts_ms"] = ts_ms
            snapshot["line_no"] = line_no
            entity_stats_timelines[entity_id].append((ts_ms, snapshot))
            update_critical_observation(critical_stats_by_actor, entity_id, snapshot, line_no)
            continue

        if " ENTITY_ATTR_UPDATE " in raw_line:
            update = _parse_entity_stats_snapshot(raw_line.replace(" ENTITY_ATTR_UPDATE ", " ENTITY_STATS ", 1))
            if update is not None:
                entity_id, snapshot = update
                snapshot["ts_ms"] = ts_ms
                update_critical_observation(critical_stats_by_actor, entity_id, snapshot, line_no, partial=True)
            continue

        if " BUFF_ACTION_UNRESOLVED " in raw_line:
            fields = _extract_fields(raw_line)
            unresolved_buff_actions.append({
                'uid': fields.get('uid'), 'seq': fields.get('seq'),
                'ts_ms': ts_ms, 'line_no': line_no,
                'trigger_type': fields.get('triggerType'), 'action_id': fields.get('actionId'),
                'target_actor_id': fields.get('ownerId'), 'source_actor_id': fields.get('srcId'),
                'reason': 'unresolved_buff_action_phase',
            })
            continue

        if " BUFF_KEYWORD_UPDATE " in raw_line or " BUFF_KEYWORD_UPDATE_UNRESOLVED " in raw_line:
            keyword_record = None
            keyword_bound = False
            keyword_observation = {}
            try:
                tag = (' BUFF_KEYWORD_UPDATE_UNRESOLVED ' if ' BUFF_KEYWORD_UPDATE_UNRESOLVED ' in raw_line
                       else ' BUFF_KEYWORD_UPDATE ')
                encoded = raw_line.split(tag, 1)[1]
                pairs = json.loads(encoded, object_pairs_hook=list)
                if (not encoded.startswith('{') or not isinstance(pairs, list)
                        or any(not isinstance(pair, tuple) or len(pair) != 2 for pair in pairs)):
                    raise ValueError('invalid_keyword_observation')
                keyword_observation = dict(pairs)
                if len(keyword_observation) != len(pairs):
                    raise ValueError('duplicate_keyword_fields')
                uid = keyword_observation.get('buffUid')
                owner = keyword_observation.get('ownerId')
                session = keyword_observation.get('sessionId')
                seq = keyword_observation.get('operationSeq')
                generation = keyword_observation.get('generation')
                added = keyword_observation.get('addedBuffNumId')
                keyword_record = active_buff_starts_by_uid.get(uid) if isinstance(uid, str) else None
                if (keyword_observation.get('schemaVersion') != 1
                        or type(keyword_observation.get('schemaVersion')) is not int
                        or keyword_observation.get('operationPayloadCase') != 'trigger_keyword_op_data'
                        or keyword_record is None or keyword_record.get('rdps_live_identity_conflict')
                        or not isinstance(session, str) or not session
                        or type(seq) is not int or seq < 0
                        or type(generation) is not int or not 0 <= generation < 2**32
                        or type(added) is not int or added < 0
                        or any(not isinstance(value, str) or not re.fullmatch(r'[1-9][0-9]*', value)
                               or int(value) >= 2**64 for value in (uid, owner))
                        or keyword_record.get('target_actor_id') != owner
                        or (ts_ms, line_no) < (keyword_record['ts_ms'], keyword_record['line_no'])):
                    raise ValueError('unbound_keyword_identity')
                anchors = keyword_record.get('creation_observations') or []
                if (not any(anchor.get('sessionId') == session for anchor in anchors)
                        or any(anchor.get('sessionId') not in (None, session) for anchor in anchors)):
                    raise ValueError('keyword_creation_session_mismatch')
                if any(anchor.get('sessionId') == session and anchor.get('operationSeq', -1) >= seq
                       for anchor in anchors if anchor.get('line_no', 0) >= keyword_record['line_no']):
                    raise ValueError('keyword_update_before_creation')
                approved = _rdps_registry_matching_effect_entry(keyword_record, require_guard=True,
                                                                  root_lookup_cache=buff_root_action_indices)
                if (not approved or approved.get('_rdps_whitelist_keys') != ['buff_chr_0023_antal_normal_fragile']
                        or not approved.get('_keyword_origin')):
                    raise ValueError('keyword_update_without_approved_origin')
                keyword_bound = True
                raw_rows = keyword_observation.get('modify')
                normalized_rows = []
                for raw_row in raw_rows if isinstance(raw_rows, list) else []:
                    normalized_rows.append(dict(raw_row) if isinstance(raw_row, list)
                                           and all(isinstance(pair, tuple) and len(pair) == 2 for pair in raw_row)
                                           else raw_row)
                signature = (generation, added, json.dumps(normalized_rows if isinstance(raw_rows, list) else raw_rows,
                                                          sort_keys=True, ensure_ascii=False))
                seen = keyword_record.setdefault('_keyword_seen_updates', {})
                snapshots = keyword_record.setdefault('keyword_rate_snapshots', [])
                if seq in seen:
                    if seen[seq] != signature:
                        for previous in snapshots:
                            if previous['operation_seq'] == seq:
                                previous['status'] = 'conflicting_keyword_update'
                        raise ValueError('conflicting_keyword_update')
                    continue
                seen[seq] = signature
                if keyword_observation.get('validationErrors'):
                    raise ValueError('unresolved_keyword_packet_fields')
                if not isinstance(raw_rows, list) or len(raw_rows) != 1:
                    raise ValueError('invalid_keyword_modify_rows')
                row_pairs = raw_rows[0]
                if (not isinstance(row_pairs, list)
                        or any(not isinstance(pair, tuple) or len(pair) != 2 for pair in row_pairs)):
                    raise ValueError('invalid_keyword_modify_row')
                row = dict(row_pairs)
                if len(row) != len(row_pairs) or type(row.get('configIndex')) is not int or row['configIndex'] != 0:
                    raise ValueError('invalid_keyword_config_index')
                rate = row.get('rateAfter')
                if type(rate) not in (int, float) or _coerce_optional_float(rate) is None or rate < 0:
                    raise ValueError('invalid_keyword_rate')
                added_id = _load_num_id_str_buff_map().get(str(added))
                parent_root = _load_current_buff_roots().get('buff_chr_0023_antal_tageffect') or {}
                for origin in approved['_keyword_origin']:
                    selected = []; pending = [parent_root]
                    while pending:
                        node = pending.pop()
                        if isinstance(node, dict):
                            if (node.get('$type') == 'Beyond.Gameplay.Core.VulnerableAction.Data'
                                    and node.get('serverActionIndex') == origin['actionId']):
                                selected.append(node)
                            pending.extend(node.values())
                        elif isinstance(node, list):
                            pending.extend(node)
                    enhancing = selected[0].get('enhancingList') if len(selected) == 1 else None
                    if (not isinstance(enhancing, list) or len(enhancing) != 1
                            or enhancing[0].get('operationType') != 'Add'
                            or (enhancing[0].get('value') or {}).get('useBlackboardKey') is not True
                            or (enhancing[0].get('value') or {}).get('blackboardKey') != 'potential_5_rate'
                            or added_id not in (enhancing[0].get('buffIds') or [])):
                        raise ValueError('keyword_marker_not_in_current_config')
                snapshots.append({'operation_seq': seq, 'line_no': line_no, 'ts_ms': ts_ms,
                                  'session_id': session, 'generation': generation,
                                  'rate_after': float(rate), 'status': 'observed',
                                  'added_buff_id': added_id, 'config_index': row['configIndex']})
            except (ValueError, TypeError, KeyError, IndexError) as error:
                if keyword_bound and keyword_record is not None:
                    snapshots = keyword_record.setdefault('keyword_rate_snapshots', [])
                    if not any(row['operation_seq'] == keyword_observation['operationSeq'] for row in snapshots):
                        snapshots.append({'operation_seq': keyword_observation['operationSeq'], 'line_no': line_no,
                                          'ts_ms': ts_ms, 'session_id': keyword_observation['sessionId'],
                                          'generation': keyword_observation['generation'], 'rate_after': None,
                                          'status': 'unresolved_keyword_update'})
                unresolved_buff_actions.append({'ts_ms': ts_ms, 'line_no': line_no,
                                                'reason': str(error), 'source': 'BUFF_KEYWORD_UPDATE'})
            continue

        if " BUFF_ENABLE " in raw_line:
            try:
                state_text = raw_line.split(' BUFF_ENABLE ', 1)[1]
                pairs = json.loads(state_text, object_pairs_hook=list)
                if not state_text.startswith('{') or any(not isinstance(pair, tuple) or len(pair) != 2 for pair in pairs):
                    raise ValueError('state_not_object')
                state = dict(pairs)
                if len(state) != len(pairs):
                    raise ValueError('duplicate_state_fields')
                uid = state.get('buffUid')
                record = active_buff_starts_by_uid.get(uid) if isinstance(uid, str) else None
                if (type(state.get('schemaVersion')) is not int or state['schemaVersion'] != 1
                        or type(state.get('enabled')) is not bool or record is None
                        or type(state.get('ownerId')) is not str or state['ownerId'] != record.get('target_actor_id')
                        or type(state.get('operationSeq')) is not int or state['operationSeq'] < 0):
                    raise ValueError('unbound_buff_enable_state')
                previous = record.setdefault('enable_observations', [])
                if any(row['operationSeq'] == state['operationSeq'] and row['enabled'] != state['enabled'] for row in previous):
                    record['rdps_conflicting_enable_state'] = True
                    unresolved_buff_actions.append({'uid': uid, 'ts_ms': ts_ms, 'line_no': line_no,
                                                   'reason': 'conflicting_buff_enable_state'})
                previous.append({**state, 'ts_ms': ts_ms, 'line_no': line_no})
            except (ValueError, TypeError, KeyError):
                unresolved_buff_actions.append({'ts_ms': ts_ms, 'line_no': line_no, 'reason': 'unbound_buff_enable_state'})
            continue

        if " BUFF_CREATE_OBSERVED " in raw_line:
            try:
                blob = raw_line.split(' BUFF_CREATE_OBSERVED ', 1)[1]
                pairs = json.loads(blob, object_pairs_hook=list)
                if not blob.startswith('{') or any(not isinstance(pair, tuple) or len(pair) != 2 for pair in pairs):
                    raise ValueError('creation_not_object')
                observation = dict(pairs)
                if len(observation) != len(pairs):
                    raise ValueError('duplicate_creation_fields')
                uid = observation.get('buffUid')
                record = active_buff_starts_by_uid.get(uid) if isinstance(uid, str) else None
                template = observation.get('buffTemplate')
                if isinstance(template, str) and template.isdigit():
                    template = _load_num_id_str_buff_map().get(template)
                reported_templates = []
                number = observation.get('buffNumId')
                if number not in (None, 0):
                    if type(number) is not int or number < 0:
                        raise ValueError('invalid_creation_numeric_template')
                    reported_templates.append(_load_num_id_str_buff_map().get(str(number)))
                elif type(number) is bool:
                    raise ValueError('invalid_creation_numeric_template')
                literal = observation.get('buffId')
                if literal not in (None, ''):
                    if not isinstance(literal, str):
                        raise ValueError('invalid_creation_string_template')
                    reported_templates.append(literal)
                template_pairs = observation.get('buffTemplateId')
                if template_pairs is not None:
                    if (not isinstance(template_pairs, list)
                            or any(not isinstance(pair, tuple) or len(pair) != 2 for pair in template_pairs)):
                        raise ValueError('invalid_creation_template_object')
                    template_object = dict(template_pairs)
                    if len(template_object) != len(template_pairs):
                        raise ValueError('duplicate_creation_template_fields')
                    observation['buffTemplateId'] = template_object
                    choices = [key for key in ('int_id', 'str_id') if key in template_object]
                    if (len(choices) != 1 or type(template_object.get('type')) not in (int, str)
                            or template_object['type'] not in (1, 'Buff')):
                        raise ValueError('invalid_creation_template_oneof')
                    if choices[0] == 'int_id':
                        number = template_object['int_id']
                        if type(number) is not int or number < 0:
                            raise ValueError('invalid_creation_numeric_template')
                        reported_templates.append(_load_num_id_str_buff_map().get(str(number)))
                    else:
                        literal = template_object['str_id']
                        if not isinstance(literal, str) or not literal:
                            raise ValueError('invalid_creation_string_template')
                        reported_templates.append(literal)
                if not reported_templates or any(value != template for value in reported_templates):
                    raise ValueError('creation_template_evidence_conflict')
                if (record is not None and template == record.get('event_key')
                        and observation.get('sourceEvent') == 'BattleOpTriggerAction'
                        and observation.get('sourceActorId') == record.get('source_actor_id')
                        and observation.get('targetActorId') == record.get('target_actor_id')
                        and isinstance(observation.get('sessionId'), str) and observation['sessionId']
                        and type(observation.get('operationSeq')) is int and observation['operationSeq'] >= 0):
                    context = {'session_id': observation['sessionId'], 'operation_seq': observation['operationSeq']}
                    contexts = record.setdefault('packet_creation_contexts', [])
                    if context not in contexts:
                        contexts.append(context)
                if (type(observation.get('schemaVersion')) is not int or observation['schemaVersion'] != 1
                        or record is None or template != record.get('event_key')
                        or observation.get('sourceEvent') not in {'BattleOpTriggerAction', 'BattleOpAddBuff'}
                        or not isinstance(observation.get('sessionId'), str) or not observation['sessionId']
                        or type(observation.get('operationSeq')) is not int or observation['operationSeq'] < 0
                        or observation.get('generationScope') != 'operation_owner'
                        or any(type(observation.get(key)) is not str or not re.fullmatch(r'[1-9]\d*', observation[key])
                               or int(observation[key]) >= 2**64 for key in ('buffUid', 'targetActorId', 'operationOwnerId'))
                        or uid in {'0', '4294967295'}
                        or observation['targetActorId'] != record.get('target_actor_id')
                        or (record.get('source_actor_id') and observation.get('sourceActorId') != record['source_actor_id'])):
                    raise ValueError('unbound_buff_creation_observation')
                previous = record.setdefault('creation_observations', [])
                observed_values = dict(record.get('_observed_bb_values') or {})
                bb_conflicts = set()
                assigned = observation.get('assignedItems')
                if isinstance(assigned, list):
                    decoded_items = []
                    observed_values = {}
                    for item_pairs in assigned:
                        if (not isinstance(item_pairs, list)
                                or any(not isinstance(pair, tuple) or len(pair) != 2 for pair in item_pairs)):
                            bb_conflicts.add('*')
                            continue
                        item = dict(item_pairs)
                        if len(item) != len(item_pairs):
                            bb_conflicts.add('*')
                            continue
                        decoded_items.append(item)
                        key = item.get('target_key')
                        operand = item.get('numeric_value')
                        if not isinstance(key, str) or not key:
                            bb_conflicts.add('*')
                            continue
                        if (item.get('use_direct_value') is not True or type(operand) not in (int, float)
                                or _coerce_optional_float(operand) is None):
                            bb_conflicts.add(key)
                            continue
                        operand = float(operand)
                        if key in observed_values and observed_values[key].hex() != operand.hex():
                            bb_conflicts.add(key)
                        observed_values[key] = operand
                    observation['assignedItems'] = decoded_items
                    for key in bb_conflicts:
                        observed_values.pop(key, None)
                    if '*' in bb_conflicts:
                        observed_values = {}
                creation_key = (observation['sessionId'], observation['operationOwnerId'],
                                observation.get('operationGeneration'), observation['operationSeq'], uid)
                historical_creation = buff_creation_operation_history.get(creation_key)
                same_operations = [historical_creation['observation']] if historical_creation is not None else []
                pending_state = record.get('_dynamic_pending_state')
                comparable_anchors = [row for row in previous if
                    (row['sessionId'], row['operationOwnerId'], row.get('operationGeneration')) == creation_key[:3]]
                stale = any(row['operationSeq'] > observation['operationSeq'] for row in comparable_anchors)
                if (pending_state is not None and pending_state['context'] == creation_key[:3]
                        and observation['operationSeq'] < pending_state['last_seq']):
                    stale = True
                conflicting = any(observed_values and row.get('bb_values') and row['bb_values'] != observed_values for row in same_operations)
                if same_operations or stale:
                    prior_record = record.get('_previous_snapshot_record')
                    incoming_record = record
                    if prior_record is not None:
                        for key, old_value in record['_previous_snapshot_boundary'].items():
                            if old_value is None:
                                prior_record.pop(key, None)
                            else:
                                prior_record[key] = old_value
                        active_buff_starts_by_uid[uid] = prior_record
                        pending_buff_record = prior_record
                        record = prior_record
                    elif historical_creation is not None and historical_creation['instance_start_line_no'] != record['instance_start_line_no']:
                        active_buff_starts_by_uid.pop(uid, None)
                        pending_buff_record = None
                        record = historical_creation['record']
                    if record is not incoming_record:
                        buff_starts = [row for row in buff_starts if row is not incoming_record]
                    record.setdefault('repeated_creation_observations', []).append({
                        **observation, 'ts_ms': ts_ms, 'line_no': line_no, 'bb_values': observed_values,
                        'status': 'conflicting' if conflicting else 'duplicate' if same_operations else 'out_of_order'})
                    if conflicting:
                        affected_record = historical_creation['record'] if historical_creation is not None else record
                        affected_record.setdefault('_dynamic_unresolved_keys', set()).add('*')
                        if affected_record.get('_dynamic_pending_state') is not None:
                            affected_record['_dynamic_pending_state']['invalid_keys'].add('*')
                        for snapshot in affected_record.get('attribute_refresh_snapshots') or []:
                            if snapshot['operation_seq'] >= observation['operationSeq']:
                                snapshot['status'] = 'conflicting_creation_parameters'
                    continue
                if any(row['sessionId'] != observation['sessionId'] for row in previous):
                    record['rdps_dynamic_identity_conflict'] = True
                    raise ValueError('conflicting_buff_creation_session')
                accepted = {**observation, 'ts_ms': ts_ms, 'line_no': line_no, 'bb_values': observed_values,
                            'bb_conflicts': sorted(bb_conflicts), 'bb_observation_source': 'unverified_creation_bb'}
                previous.append(accepted)
                buff_creation_operation_history[creation_key] = {
                    'observation': accepted, 'record': record, 'instance_start_line_no': record['instance_start_line_no']}
                record['_observed_bb_values'] = {}
                if pending_state is not None:
                    context = pending_state['context']
                    same_owner_wrong_generation = (observation['operationOwnerId'] == context[1]
                                                    and observation.get('operationGeneration') != context[2])
                    if not same_owner_wrong_generation:
                        pending_state['values'].update(observed_values)
                        pending_state['invalid_keys'].difference_update(observed_values)
                        record.setdefault('_dynamic_unresolved_keys', set()).difference_update(observed_values)
                        pending_state['invalid_keys'].update(bb_conflicts)
                        for key in observed_values:
                            pending_state['value_sources'][key] = {'kind': 'creation_observation', 'creation_key': creation_key}
            except (ValueError, TypeError, KeyError):
                unresolved_buff_actions.append({'ts_ms': ts_ms, 'line_no': line_no,
                                               'reason': 'unbound_buff_creation_observation'})
            continue

        if " BUFF_ORIGIN " in raw_line:
            try:
                origin_text = raw_line.split(' BUFF_ORIGIN ', 1)[1]
                pairs = json.loads(origin_text, object_pairs_hook=list)
                if (not origin_text.startswith('{') or not isinstance(pairs, list)
                        or any(not isinstance(pair, tuple) or len(pair) != 2 for pair in pairs)):
                    raise ValueError('origin_not_object')
                origin = dict(pairs)
                if len(origin) != len(pairs):
                    raise ValueError('duplicate_origin_fields')
                uid = origin.get('buffUid')
                record = active_buff_starts_by_uid.get(uid) if isinstance(uid, str) else None
                if (type(origin.get('schemaVersion')) is not int or origin['schemaVersion'] != 1
                        or record is None or origin.get('buffTemplate') not in {record.get('event_key'), record.get('raw_event_key')}
                        or any(type(origin.get(key)) is not str or not re.fullmatch(r'[1-9]\d*', origin[key])
                               or int(origin[key]) >= 2**64 for key in ('buffUid','sourceActorId','targetActorId','environmentInstId'))
                        or origin['sourceActorId'] != record.get('source_actor_id')
                        or origin['targetActorId'] != record.get('target_actor_id')
                        or type(origin.get('actionId')) is not int or origin['actionId'] < 0
                        or type(origin.get('operationSeq')) is not int or origin['operationSeq'] < 0
                        or type(origin.get('triggerType')) is not int or origin['triggerType'] != 0):
                    raise ValueError('unbound_origin_identity')
                record.setdefault('origin_observations', []).append({**origin, 'lineNo': line_no, '_boundCreateRecord': True})
            except (ValueError, TypeError, KeyError):
                unresolved_buff_actions.append({'ts_ms': ts_ms, 'line_no': line_no,
                                               'reason': 'unbound_buff_origin'})
            continue

        if " LOADOUT reason=" in raw_line or "LOADOUT reason=" in raw_line:
            fields = _extract_fields(raw_line)
            current_loadout_group = {
                "ts_ms": ts_ms,
                "line_no": line_no,
                "reason": str(fields.get("reason") or ""),
                "index": len(loadout_groups),
                "rows": {},
            }
            loadout_groups.append(current_loadout_group)
            continue

        if " DUNGEON_CONTEXT " in raw_line:
            fields = _extract_fields(raw_line)
            set_dungeon_context(fields.get("dungeonId") or fields.get("dungeon_id"))
            continue

        if " BATTLE_RESULT " in raw_line:
            fields = _extract_fields(raw_line)
            set_dungeon_context(fields.get("dungeonId") or fields.get("dungeon_id"))
            if _coerce_int(fields.get("isPass"), default=0) == 1:
                challenge_pass_confirmed = True
                completion_source = str(fields.get("source") or "battle_result")
            continue

        if " CONTRACT_TAGS " in raw_line:
            fields = _extract_fields(raw_line)
            set_dungeon_context(fields.get("dungeonId") or fields.get("dungeon_id"))
            parsed_tag_ids = _parse_contract_tag_ids(fields.get("tagIds") or fields.get("tag_ids"))
            if parsed_tag_ids:
                contract_tag_ids = parsed_tag_ids
            if fields.get("score") is not None:
                contract_tag_score = _coerce_int(fields.get("score"), default=0)
            continue

        if " LOADOUT_STATS " in raw_line or "LOADOUT_STATS " in raw_line:
            stats_snapshot = _parse_loadout_stats_snapshot(raw_line,
                allow_bound_gem=bool(current_loadout_group and current_loadout_group.get("ts_ms") == ts_ms
                    and current_loadout_group.get("reason") in {"SC_SYNC_CHAR_BAG_INFO", "SC_SELF_SCENE_INFO"}))
            if stats_snapshot is not None:
                char_key = str(stats_snapshot["character_key"])
                loadout_by_char[char_key] = _merge_tracked_loadout_state(
                    loadout_by_char.get(char_key, {"character_key": char_key}),
                    stats_snapshot,
                )
                if char_key in weapon_override_by_char:
                    loadout_by_char[char_key] = _loadout_with_weapon_state(
                        loadout_by_char[char_key],
                        weapon_override_by_char[char_key],
                    )
                if current_loadout_group is None or current_loadout_group.get("ts_ms") != ts_ms:
                    current_loadout_group = {
                        "ts_ms": ts_ms,
                        "line_no": line_no,
                        "reason": "",
                        "index": len(loadout_groups),
                        "rows": {},
                    }
                    loadout_groups.append(current_loadout_group)
                group_rows = current_loadout_group["rows"]
                group_rows[char_key] = _merge_loadout_snapshot(
                    group_rows.get(char_key, {"character_key": char_key}),
                    stats_snapshot,
                )
                if char_key in weapon_override_by_char:
                    group_rows[char_key] = _loadout_with_weapon_state(
                        group_rows[char_key],
                        weapon_override_by_char[char_key],
                    )
            continue

        if " LOADOUT slot=" in raw_line or "LOADOUT slot=" in raw_line:
            loadout_snapshot = _parse_loadout_slot_snapshot(raw_line)
            if loadout_snapshot is not None:
                char_key = str(loadout_snapshot["character_key"])
                loadout_by_char[char_key] = _merge_tracked_loadout_state(
                    loadout_by_char.get(char_key, {}),
                    loadout_snapshot,
                )
                if char_key in weapon_override_by_char:
                    loadout_by_char[char_key] = _loadout_with_weapon_state(
                        loadout_by_char[char_key],
                        weapon_override_by_char[char_key],
                    )
                if current_loadout_group is None or current_loadout_group.get("ts_ms") != ts_ms:
                    current_loadout_group = {
                        "ts_ms": ts_ms,
                        "line_no": line_no,
                        "reason": "",
                        "index": len(loadout_groups),
                        "rows": {},
                    }
                    loadout_groups.append(current_loadout_group)
                group_rows = current_loadout_group["rows"]
                group_rows[char_key] = _merge_loadout_snapshot(
                    group_rows.get(char_key, {}),
                    loadout_snapshot,
                )
                if char_key in weapon_override_by_char:
                    group_rows[char_key] = _loadout_with_weapon_state(
                        group_rows[char_key],
                        weapon_override_by_char[char_key],
                    )
            loadout_match = _LOADOUT_SLOT_RE.search(raw_line)
            if loadout_match:
                char_key = loadout_match.group("char")
                active_weapons_by_char[char_key] = str(
                    loadout_by_char.get(char_key, {}).get("weapon_template")
                    or loadout_match.group("weapon")
                )
                active_suits_by_char[char_key] = _parse_loadout_suits(
                    loadout_match.group("equip_suit")
                )
            continue

        if _OFFICIAL_TIMER_START_RE.search(raw_line):
            fields = _extract_fields(raw_line)
            set_dungeon_context(fields.get("gameId") or fields.get("game_id"))
            # A new explicit challenge start opens a new timer scope. A prior
            # attempt's completion and elapsed time cannot certify this one.
            official_timer_elapsed_ms = None
            official_timer_end_ms = None
            live_timer_elapsed_ms = None
            game_timer_end_ms = None
            game_timer_elapsed_ms = None
            game_timer_window_elapsed_ms = None
            packet_timer_elapsed_ms = None
            timer_end_seen = False
            timer_window_valid = True
            challenge_pass_confirmed = False
            official_pass_confirmed = False
            completion_source = ""
            official_timer_start_ms = ts_ms
            game_timer_start_ms = ts_ms
            timer_start_seen = True
            continue

        if _LIVE_TIMER_TICK_RE.search(raw_line):
            fields = _extract_fields(raw_line)
            elapsed_ms = _coerce_int(fields.get("elapsedMs"), default=0)
            if elapsed_ms > 0:
                live_timer_elapsed_ms = elapsed_ms
                game_timer_end_ms = ts_ms
                game_timer_elapsed_ms = elapsed_ms
                game_timer_window_elapsed_ms = elapsed_ms
            continue

        if _OFFICIAL_TIMER_END_RE.search(raw_line):
            fields = _extract_fields(raw_line)
            set_dungeon_context(fields.get("gameId") or fields.get("game_id"))
            official_timer_end_ms = ts_ms
            if _coerce_int(fields.get("isPass"), default=0) == 1:
                challenge_pass_confirmed = True
                official_pass_confirmed = True
                completion_source = "official_timer_end"
            pass_time_ms = _coerce_int(fields.get("passTime"), default=0)
            if pass_time_ms > 0:
                game_timer_end_ms = ts_ms
                game_timer_elapsed_ms = pass_time_ms
                game_timer_window_elapsed_ms = pass_time_ms
                official_timer_elapsed_ms = pass_time_ms
                timer_end_seen = True
            continue

        if _GAME_TIMER_START_RE.search(raw_line):
            fields = _extract_fields(raw_line)
            start_ms = _coerce_int(fields.get("startMs"), default=0)
            if official_timer_start_ms is None and _timer_is_authoritative(fields) and start_ms >= 0:
                game_timer_start_ms = ts_ms
                timer_start_seen = True
            continue

        if _GAME_TIMER_END_RE.search(raw_line):
            fields = _extract_fields(raw_line)
            timer_is_authoritative = _timer_is_authoritative(fields)
            if timer_is_authoritative and _coerce_int(fields.get("isPass"), default=0) == 1:
                challenge_pass_confirmed = True
                completion_source = str(fields.get("source") or "game_timer_end")
            elapsed_ms = _coerce_int(fields.get("elapsedMs"), default=0)
            wall_elapsed_ms = _coerce_int(fields.get("wallElapsedMs"), default=0)
            packet_elapsed_ms = _coerce_int(fields.get("packetElapsedMs"), default=0)
            if packet_elapsed_ms > 0:
                packet_timer_elapsed_ms = packet_elapsed_ms
            sane = _coerce_int(fields.get("sane"), default=1)
            if (
                official_timer_elapsed_ms is None
                and timer_is_authoritative
                and sane
                and elapsed_ms > 0
            ):
                game_timer_end_ms = ts_ms
                game_timer_elapsed_ms = wall_elapsed_ms if wall_elapsed_ms > 0 else elapsed_ms
                game_timer_window_elapsed_ms = game_timer_elapsed_ms
                timer_end_seen = True
            continue

        if " ATB_UPDATE " in raw_line:
            fields = _extract_fields(raw_line)
            if str(fields.get("reason") or "") == "AddValue" and _safe_positive_rate(fields.get("delta")) is not None:
                atb_char = _extract_character_key(fields.get("owner"), "")
                if atb_char:
                    atb_gain_ms_by_char[atb_char].append(ts_ms)
            continue

        if " SKILL_CAST_START " in raw_line:
            fields = _extract_fields(raw_line)
            cast_skill = str(fields.get("skill") or "")
            cast_character_key = _extract_character_key(fields.get("owner"), cast_skill)
            cast_record = {
                "ts_ms": ts_ms,
                "line_no": line_no,
                "skill": cast_skill,
                "owner": fields.get("owner"),
                "owner_id": fields.get("ownerId"),
                "skill_inst_id": fields.get("inst"),
                "skill_source": fields.get("skillSource") or fields.get("skill_source"),
                "source_character_key": cast_character_key,
                "end_ts_ms": None,
            }
            skill_casts.append(cast_record)
            inst_key = str(cast_record.get("skill_inst_id") or "")
            if inst_key:
                active_skill_casts_by_inst[inst_key] = cast_record
            if cast_character_key:
                if first_party_action_ms is None and not _is_generic_runtime_skill_id(cast_skill):
                    first_party_action_ms = ts_ms
                rows = recent_skill_casts_by_char[cast_character_key]
                rows.append(
                    {
                        "ts_ms": ts_ms,
                        "skill": cast_skill,
                        "owner": fields.get("owner"),
                        "skill_inst_id": fields.get("inst"),
                        "skill_source": fields.get("skillSource") or fields.get("skill_source"),
                    }
                )
                del rows[:-16]
            continue

        if " SKILL_CAST_END " in raw_line:
            fields = _extract_fields(raw_line)
            inst_key = str(fields.get("inst") or "")
            cast_record = active_skill_casts_by_inst.pop(inst_key, None) if inst_key else None
            if cast_record is not None:
                cast_record["end_ts_ms"] = ts_ms
                cast_record["end_line_no"] = line_no
            continue

        if " CHAR_SKILLS " in raw_line:
            parsed_char_skills = _parse_char_skills_line(raw_line)
            if parsed_char_skills is not None:
                char_skill_levels[parsed_char_skills[0]] = parsed_char_skills[1]
                char_key = parsed_char_skills[0]
                stats_snapshot = _parse_loadout_stats_snapshot(
                    raw_line, loadout_context=loadout_by_char.get(char_key), squad_actor_keys=squad_actor_keys
                )
                if stats_snapshot is not None:
                    loadout_by_char[char_key] = stats_snapshot
                    current_loadout_group = {
                        "ts_ms": ts_ms, "line_no": line_no, "reason": "CHAR_SKILLS", "index": len(loadout_groups),
                        "rows": {key: copy.deepcopy(loadout_by_char[key])
                                 for key in set(squad_actor_keys.values()) if key in loadout_by_char},
                    }
                    loadout_groups.append(current_loadout_group)
            continue

        if (" BB_MODIFY_DYNAMIC " in raw_line or " BUFF_ATTR_REFRESH " in raw_line
                or " BUFF_ATTR_REFRESH_UNRESOLVED " in raw_line):
            refresh_tag = (' BUFF_ATTR_REFRESH_UNRESOLVED ' if ' BUFF_ATTR_REFRESH_UNRESOLVED ' in raw_line
                           else ' BUFF_ATTR_REFRESH ')
            is_refresh = refresh_tag in raw_line
            fields = _extract_fields(raw_line.split(' identity=', 1)[0]) if not is_refresh else {}
            value = _coerce_optional_float(fields.get("value"))
            blackboard_key = None
            skill_key = fields.get("skill")
            action_id = _coerce_optional_int(fields.get("actionId"))
            root = _load_current_buff_roots().get(str(skill_key or ""))
            action_metadata: dict[str, Any] = {}
            if isinstance(root, dict) and action_id is not None:
                cached_actions = buff_root_action_indices.get(id(root))
                if cached_actions is None or cached_actions[0] is not root:
                    action_index: dict[int, list[tuple[dict[str, Any], bool]]] = {}
                    create_actions = []
                    pending_nodes = [(root, True)]
                    while pending_nodes:
                        node, enabled = pending_nodes.pop()
                        if isinstance(node, dict):
                            enabled = enabled and ('serverActionIndex' not in node or node.get('isEnable') is True)
                            if type(node.get('serverActionIndex')) is int:
                                action_index.setdefault(node['serverActionIndex'], []).append((node, enabled))
                            if node.get('$type') == 'Beyond.Gameplay.Core.CreateBuffAction.Data':
                                create_actions.append((node, enabled))
                            pending_nodes.extend((item, enabled) for item in node.values())
                        elif isinstance(node, list):
                            pending_nodes.extend((item, enabled) for item in node)
                    buff_root_action_indices[id(root)] = (root, action_index, create_actions)
                else:
                    action_index = cached_actions[1]
                matches = [node for node, _enabled in action_index.get(action_id, [])
                           if str(node.get('$type') or '').replace('+', '.')
                           == 'Beyond.Gameplay.Core.ModifyDynamicBlackboard.Data']
                if len(matches) == 1:
                    node = matches[0]
                    blackboard_key = str(node.get("key") or "") or None
                    operand = node.get("value") or {}
                    action_metadata = {
                        "blackboard_key_source": "typed_current_buff_action",
                        "operand_blackboard_key": (operand.get("blackboardKey")
                                                   if operand.get("useBlackboardKey") is True else None),
                        "operation": node.get("operation"),
                    }
                elif len(matches) > 1:
                    action_metadata["mapping_error"] = "ambiguous_dynamic_action"
                else:
                    action_metadata["mapping_error"] = "missing_dynamic_action"
            elif action_id is not None:
                skill_metadata = _load_skill_item_metadata(str(skill_key or ""))
                action_keys = skill_metadata.get("dynamic_action_keys") if isinstance(skill_metadata, dict) else None
                if isinstance(action_keys, dict):
                    blackboard_key = action_keys.get(action_id)
                    if blackboard_key is not None:
                        action_metadata["blackboard_key_source"] = "unversioned_skill_observation"
            event = {
                "line_no": line_no,
                "ts_ms": ts_ms,
                "seq": _coerce_optional_int(fields.get("seq")),
                "tick": _coerce_optional_int(fields.get("tick")),
                "instance_id": fields.get("inst"),
                "owner_id": fields.get("ownerId"),
                "owner": fields.get("owner"),
                "skill_key": skill_key,
                "action_id": action_id,
                "blackboard_key": blackboard_key,
                "value": value,
                **action_metadata,
            }
            (buff_attribute_refresh_events if is_refresh else dynamic_bb_events).append(event)
            # Legacy decoded packets kept exact Buff/owner/template/operation
            # identities before the later generation/oneof envelope existed.
            # Preserve that narrower evidence separately; do not fabricate the
            # missing envelope or let an invalid modern identity fall back to it.
            try:
                observation = json.loads(raw_line.split(refresh_tag if is_refresh else ' identity=', 1)[1])
                context = observation.get('eventContext')
                legacy = (isinstance(context, dict)
                          and all(observation.get(key) is None for key in
                                  ('operationOwnerId', 'ownerId', 'environmentInstId', 'generation',
                                   'templateType', 'templateIdCase', 'actionRuntimeCase', 'operationPayloadCase')))
                if legacy and str(context.get('owner_type')) == '1' and context.get('template_type') == 'Buff':
                    legacy_uid = str(context.get('inst_id'))
                    legacy_owner = str(context.get('owner_id'))
                    legacy_seq = context.get('seq_id')
                    legacy_action = observation.get('actionId')
                    template_int = context.get('template_int_id')
                    template_str = context.get('template_str_id')
                    legacy_template = (_load_num_id_str_buff_map().get(str(template_int))
                                       if type(template_int) is int and not template_str else template_str)
                    legacy_record = active_buff_starts_by_uid.get(legacy_uid)
                    creation_contexts = (legacy_record or {}).get('packet_creation_contexts') or []
                    creation_sessions = {row['session_id'] for row in creation_contexts}
                    if (legacy_template in {'buff_common_natural_natural_corrupt_do', 'buff_common_natural_cryst_triggered'}
                            and type(observation.get('schemaVersion')) is int and observation['schemaVersion'] == 1
                            and type(legacy_seq) is int and legacy_seq >= 0
                            and type(legacy_action) is int and legacy_record is not None
                            and legacy_record.get('event_key') == legacy_template
                            and legacy_record.get('target_actor_id') == legacy_owner
                            and legacy_record.get('source_actor_id')
                            and all(re.fullmatch(r'[1-9]\d*', str(value)) and int(value) < 2**64
                                    for value in (legacy_uid, legacy_owner, legacy_record.get('source_actor_id')))
                            and legacy_record.get('source_character_key')
                            and not legacy_record.get('rdps_live_identity_conflict')
                            and creation_sessions == {observation.get('sessionId')}
                            and all(legacy_seq > row['operation_seq'] for row in creation_contexts)
                            and observation.get('operationSeq') == legacy_seq
                            and observation.get('actionType') == (31 if is_refresh else 7)):
                        candidates = []; nodes = [(_load_current_buff_roots().get(legacy_template), True)]
                        while nodes:
                            item, enabled = nodes.pop()
                            if isinstance(item, dict):
                                enabled = enabled and ('serverActionIndex' not in item or item.get('isEnable') is True)
                                if item.get('serverActionIndex') == legacy_action:
                                    candidates.append((item, enabled))
                                nodes.extend((value, enabled) for value in item.values())
                            elif isinstance(item, list):
                                nodes.extend((value, enabled) for value in item)
                        expected = ('Beyond.Gameplay.Core.RefreshBuffAttrModifierValue.Data' if is_refresh else
                                    'Beyond.Gameplay.Core.ModifyDynamicBlackboard.Data')
                        if (len(candidates) == 1 and candidates[0][1] is True
                                and candidates[0][0].get('$type') == expected):
                            keys = ('def_decrease', 'additional_def_decrease')
                            state = legacy_record.setdefault('_resistance_parameter_state', {
                                'values': {key: _coerce_optional_float((legacy_record.get('bb_values') or {}).get(key)) for key in keys},
                                'seen': {}, 'last_seq': -1, 'invalid': set()})
                            key = candidates[0][0].get('key')
                            if not is_refresh and key in keys:
                                if (fields.get('inst') == legacy_uid and fields.get('ownerId') == legacy_owner
                                        and skill_key == legacy_template and action_id == legacy_action
                                        and _coerce_optional_int(fields.get('seq')) == legacy_seq):
                                    signature = (legacy_action, key, value)
                                    if legacy_seq in state['seen']:
                                        if state['seen'][legacy_seq] != signature:
                                            state['invalid'].add(key)
                                            for saved in legacy_record.get('resistance_parameter_snapshots') or []:
                                                if saved['operation_seq'] >= legacy_seq:
                                                    saved['status'] = 'conflicting_dynamic_operation'
                                    elif legacy_seq < state['last_seq']:
                                        state['invalid'].add(key)
                                    else:
                                        state['seen'][legacy_seq] = signature
                                        state['last_seq'] = legacy_seq
                                        state['values'][key] = value
                                        if value is None or value > 0:
                                            state['invalid'].add(key)
                                        else:
                                            state['invalid'].discard(key)
                            elif is_refresh:
                                complete = (not state['invalid'] and legacy_seq > state['last_seq']
                                            and all(state['values'].get(key) is not None and state['values'][key] <= 0 for key in keys))
                                saved = {'operation_seq': legacy_seq, 'line_no': line_no, 'ts_ms': ts_ms,
                                         'bb_values': dict(state['values']),
                                         'status': 'attribute_parameters_observed' if complete else 'missing_attribute_parameters',
                                         'identity_source': 'legacy_packet_uid_owner_template_sequence',
                                         'buff_uid': legacy_uid, 'owner_id': legacy_owner,
                                         'action_id': legacy_action, 'session_id': observation.get('sessionId')}
                                prior = legacy_record.setdefault('resistance_parameter_snapshots', [])
                                duplicate = [row for row in prior if row['operation_seq'] == legacy_seq]
                                if not duplicate:
                                    prior.append(saved)
                                elif any(row['bb_values'] != saved['bb_values'] for row in duplicate):
                                    for row in duplicate:
                                        row['status'] = 'conflicting_dynamic_operation'
            except (ValueError, TypeError, KeyError, IndexError):
                pass
            record = None
            identity = None
            identity_bound = False
            try:
                if is_refresh:
                    identity_text = raw_line.split(refresh_tag, 1)[1]
                elif ' identity=' in raw_line:
                    identity_text = raw_line.split(' identity=', 1)[1]
                else:
                    raise ValueError('missing_dynamic_identity')
                pairs = json.loads(identity_text, object_pairs_hook=list)
                if not identity_text.startswith('{') or any(not isinstance(pair, tuple) or len(pair) != 2 for pair in pairs):
                    raise ValueError('dynamic_identity_not_object')
                identity = dict(pairs)
                if len(identity) != len(pairs):
                    raise ValueError('duplicate_dynamic_identity_fields')
                event['identity'] = identity
                expected_type = 31 if is_refresh else 7
                runtime_case = 'refresh_buff_attr_modifier_value_action' if is_refresh else 'modify_dynamic_blackboard_action'
                uid = identity.get('environmentInstId')
                if (type(identity.get('schemaVersion')) is not int or identity['schemaVersion'] != 1
                        or type(identity.get('actionType')) is not int or identity['actionType'] != expected_type
                        or identity.get('actionRuntimeCase') != runtime_case
                        or identity.get('operationPayloadCase') != 'trigger_action_op_data'
                        or str(identity.get('ownerType')) != '1' or type(identity.get('ownerType')) not in (int, str)
                        or identity.get('templateType') != 'Buff'
                        or type(identity.get('triggerType')) is not int or identity['triggerType'] != 0
                        or not isinstance(identity.get('sessionId'), str) or not identity['sessionId']
                        or any(type(identity.get(key)) is not int or identity[key] < 0
                               for key in ('operationSeq', 'generation', 'actionId'))
                        or any(type(identity.get(key)) is not str or not re.fullmatch(r'[1-9]\d*', identity[key])
                               or int(identity[key]) >= 2**64 for key in ('environmentInstId', 'ownerId', 'operationOwnerId'))
                        or uid in {'0', '4294967295'} or identity['ownerId'] != identity['operationOwnerId']
                        or (is_refresh and identity.get('buffUid') != uid)):
                    raise ValueError('invalid_dynamic_buff_identity')
                if (identity.get('templateIdCase') == 'int_id' and type(identity.get('templateIntId')) is int
                        and identity['templateIntId'] >= 0 and identity.get('templateStrId') is None):
                    template = _load_num_id_str_buff_map().get(str(identity['templateIntId']))
                elif (identity.get('templateIdCase') == 'str_id' and isinstance(identity.get('templateStrId'), str)
                        and identity['templateStrId'] and identity.get('templateIntId') is None):
                    template = identity['templateStrId']
                else:
                    raise ValueError('invalid_dynamic_template_oneof')
                record = active_buff_starts_by_uid.get(uid)
                if (record is None or record.get('rdps_live_identity_conflict') or record.get('rdps_dynamic_identity_conflict')
                        or template != record.get('event_key') or identity['ownerId'] != record.get('target_actor_id')):
                    raise ValueError('unbound_dynamic_buff_instance')
                anchors = record.get('creation_observations') or []
                if (not anchors or any(row['sessionId'] != identity['sessionId'] for row in anchors)
                        or not any(row['operationSeq'] <= identity['operationSeq'] for row in anchors)):
                    raise ValueError('missing_compatible_buff_creation_observation')
                if not is_refresh and (fields.get('inst') != uid or fields.get('ownerId') != identity['ownerId']
                        or action_id != identity['actionId'] or skill_key != template
                        or _coerce_optional_int(fields.get('seq')) != identity['operationSeq'] or value is None):
                    raise ValueError('dynamic_text_identity_conflict')
                identity_bound = True
                root = _load_current_buff_roots().get(template)
                expected_action = ('Beyond.Gameplay.Core.RefreshBuffAttrModifierValue.Data' if is_refresh else
                                   'Beyond.Gameplay.Core.ModifyDynamicBlackboard.Data')
                cached_actions = buff_root_action_indices.get(id(root))
                if cached_actions is None or cached_actions[0] is not root:
                    action_index = {}
                    create_actions = []
                    pending_nodes = [(root, True)]
                    while pending_nodes:
                        node, enabled = pending_nodes.pop()
                        if isinstance(node, dict):
                            enabled = enabled and ('serverActionIndex' not in node or node.get('isEnable') is True)
                            if type(node.get('serverActionIndex')) is int:
                                action_index.setdefault(node['serverActionIndex'], []).append((node, enabled))
                            if node.get('$type') == 'Beyond.Gameplay.Core.CreateBuffAction.Data':
                                create_actions.append((node, enabled))
                            pending_nodes.extend((item, enabled) for item in node.values())
                        elif isinstance(node, list):
                            pending_nodes.extend((item, enabled) for item in node)
                    buff_root_action_indices[id(root)] = (root, action_index, create_actions)
                else:
                    action_index = cached_actions[1]
                matches = action_index.get(identity['actionId'], [])
                if len(matches) != 1 or matches[0][1] is not True or matches[0][0].get('$type') != expected_action:
                    raise ValueError('dynamic_action_not_in_current_template')
                node = matches[0][0]
                for anchor in anchors:
                    anchor['bb_observation_source'] = 'unverified_creation_bb'
                    if (template not in {'buff_common_natural_natural_corrupt_do', 'buff_common_natural_cryst_triggered'}
                            or (root.get('stackingSettings') or {}).get('stackingType') != 'Stack'
                            or anchor.get('sourceEvent') != 'BattleOpTriggerAction'
                            or anchor.get('creationActionType') != 'BattleActionCreateBuff'
                            or anchor.get('creationActionRuntimeCase') != 'create_buff_action'
                            or not isinstance(anchor.get('assignedItems'), list)):
                        continue
                    origins = [origin for origin in record.get('origin_observations') or []
                               if origin.get('_boundCreateRecord') is True and origin.get('operationSeq') == anchor['operationSeq']
                               and origin.get('buffUid') == uid and origin.get('sourceActorId') == anchor.get('sourceActorId')
                               and origin.get('targetActorId') == anchor['targetActorId']]
                    creator_signatures = set()
                    valid_origins = bool(origins)
                    for origin in origins:
                        if (str(origin.get('environmentType') or '').lower() != 'buff' or str(origin.get('ownerType')) != '1'
                                or origin.get('environmentInstId') in (None, '', '0', '4294967295')):
                            valid_origins = False
                            break
                        parent = origin.get('templateStrId')
                        if isinstance(parent, str) and parent and origin.get('templateIntId') is None:
                            pass
                        elif parent in (None, '') and type(origin.get('templateIntId')) is int and origin['templateIntId'] >= 0:
                            parent = _load_num_id_str_buff_map().get(str(origin['templateIntId']))
                        else:
                            valid_origins = False
                            break
                        creator_root = _load_current_buff_roots().get(parent)
                        cached_actions = buff_root_action_indices.get(id(creator_root))
                        if cached_actions is None or cached_actions[0] is not creator_root:
                            action_index = {}
                            create_actions = []
                            creator_nodes = [(creator_root, True)]
                            while creator_nodes:
                                creator, enabled = creator_nodes.pop()
                                if isinstance(creator, dict):
                                    enabled = enabled and ('serverActionIndex' not in creator or creator.get('isEnable') is True)
                                    if type(creator.get('serverActionIndex')) is int:
                                        action_index.setdefault(creator['serverActionIndex'], []).append((creator, enabled))
                                    if creator.get('$type') == 'Beyond.Gameplay.Core.CreateBuffAction.Data':
                                        create_actions.append((creator, enabled))
                                    creator_nodes.extend((item, enabled) for item in creator.values())
                                elif isinstance(creator, list):
                                    creator_nodes.extend((item, enabled) for item in creator)
                            buff_root_action_indices[id(creator_root)] = (creator_root, action_index, create_actions)
                        else:
                            action_index = cached_actions[1]
                        try:
                            creator_actions = action_index.get(origin.get('actionId'), [])
                        except TypeError:
                            creator_actions = []
                        if (len(creator_actions) != 1 or creator_actions[0][1] is not True
                                or creator_actions[0][0].get('$type') != 'Beyond.Gameplay.Core.CreateBuffAction.Data'):
                            valid_origins = False
                            break
                        creator_signatures.add((parent, origin['actionId'], origin.get('environmentInstId')))
                    if valid_origins and len(creator_signatures) == 1:
                        anchor['bb_observation_source'] = 'typed_post_create_runtime_bb'
                        anchor['bb_observation_scope'] = 'current_default_corrosion_stack_path'
                        anchor_key = (anchor['sessionId'], anchor['operationOwnerId'], anchor.get('operationGeneration'), anchor['operationSeq'], uid)
                        if anchor_key in buff_creation_operation_history:
                            buff_creation_operation_history[anchor_key]['observation']['bb_observation_source'] = anchor['bb_observation_source']
                            buff_creation_operation_history[anchor_key]['observation']['bb_observation_scope'] = anchor['bb_observation_scope']
                context = (identity['sessionId'], identity['operationOwnerId'], identity['generation'])
                state = record.get('_dynamic_pending_state')
                seen_contexts = record.setdefault('_dynamic_seen_contexts', set())
                if state is None:
                    seeds = [row for row in anchors if row['operationSeq'] <= identity['operationSeq'] and row.get('bb_values')]
                    newest_seq = max((row['operationSeq'] for row in seeds), default=-1)
                    seeds = [row for row in seeds if row['operationSeq'] == newest_seq]
                    seed_values = {}
                    seed_sources = {}
                    if (seeds and len({json.dumps(row['bb_values'], sort_keys=True) for row in seeds}) == 1
                            and all(row['operationOwnerId'] != context[1] or row.get('operationGeneration') == context[2] for row in seeds)):
                        seed_values = dict(seeds[0]['bb_values'])
                        seed = seeds[0]
                        seed_key = (seed['sessionId'], seed['operationOwnerId'], seed.get('operationGeneration'), seed['operationSeq'], uid)
                        seed_sources = {key: {'kind': 'creation_observation', 'creation_key': seed_key} for key in seed_values}
                    state = {'context': context, 'values': seed_values,
                             'value_sources': seed_sources, 'last_seq': -1, 'operations': {},
                             'invalid_keys': set(record.get('_dynamic_unresolved_keys') or []) | {key for seed in seeds for key in seed.get('bb_conflicts') or []}}
                    record['_dynamic_pending_state'] = state
                elif state['context'] != context:
                    if context in seen_contexts:
                        raise ValueError('dynamic_context_reused_out_of_order')
                    # generation belongs to the operation owner. A changed
                    # message context is not a new Buff, nor permission to
                    # combine updates from incompatible operation streams.
                    state = {'context': context, 'values': {}, 'value_sources': {}, 'last_seq': -1,
                             'operations': {}, 'invalid_keys': set()}
                    record['_dynamic_pending_state'] = state
                seen_contexts.add(context)
                seq = identity['operationSeq']
                key = str(node.get('key') or '') if not is_refresh else ''
                signature = (expected_type, identity['actionId'], key,
                             value.hex() if value is not None else None,
                             json.dumps(identity, sort_keys=True, ensure_ascii=False))
                if seq in state['operations'] and state['operations'][seq] == signature:
                    event['state_status'] = 'duplicate_dynamic_observation'
                    continue
                if seq < state['last_seq'] or (seq in state['operations'] and state['operations'][seq] != signature):
                    if key:
                        state['values'].pop(key, None)
                        state['invalid_keys'].add(key)
                    else:
                        state['invalid_keys'].add('*')
                    for snapshot in record.get('attribute_refresh_snapshots') or []:
                        snapshot_identity = snapshot['identity']
                        if (snapshot_identity['sessionId'], snapshot_identity['operationOwnerId'], snapshot_identity['generation']) == context and snapshot['operation_seq'] >= seq:
                            snapshot['status'] = 'conflicting_dynamic_operation'
                            snapshot.setdefault('conflicting_operation_seqs', []).append(seq)
                    raise ValueError('conflicting_dynamic_operation_order')
                duplicate = seq in state['operations']
                state['operations'][seq] = signature
                state['last_seq'] = seq
                if not is_refresh:
                    if not key:
                        raise ValueError('missing_dynamic_target_key')
                    # client_value is the result after AssignDynamic, including
                    # Add/Sub operations. Assign it exactly once; never add it.
                    state['values'][key] = value
                    state['value_sources'][key] = {'kind': 'typed_runtime_post_assignment', 'operation_seq': seq,
                                                  'session_id': context[0], 'operation_owner_id': context[1], 'generation': context[2]}
                    state['invalid_keys'].discard(key)
                    record.setdefault('_dynamic_unresolved_keys', set()).discard(key)
                    event.update(blackboard_key=key, blackboard_key_source='typed_current_buff_action',
                                 operation=node.get('operation'), state_status='pending_attribute_refresh',
                                 instance_start_line_no=record['instance_start_line_no'])
                else:
                    event.update(instance_id=uid, owner_id=identity['ownerId'], skill_key=template,
                                 action_id=identity['actionId'], seq=seq, state_status='attribute_refresh_observed')
                    parameters = []; missing = []; unverified = []
                    attributes = (root.get('attributeModifier') or {}).get('attributeModifiers')
                    if not isinstance(attributes, list):
                        raise ValueError('missing_current_attribute_modifiers')
                    for index, modifier in enumerate(attributes):
                        param = modifier.get('param') or {}
                        bb_key = param.get('blackboardKey') if param.get('useBlackboardKey') is True else None
                        raw_value = (state['values'].get(bb_key) if bb_key is not None else param.get('value'))
                        operand = _coerce_optional_float(raw_value)
                        operand_source = state['value_sources'].get(bb_key, {'kind': 'unknown'}) if bb_key is not None else {'kind': 'typed_static_literal'}
                        if operand_source.get('kind') == 'creation_observation':
                            source_anchor = buff_creation_operation_history.get(tuple(operand_source['creation_key']), {}).get('observation')
                            operand_source = {**operand_source, 'kind': source_anchor.get('bb_observation_source', 'unverified_creation_bb') if source_anchor else 'unverified_creation_bb'}
                        if operand is None or bb_key in state['invalid_keys'] or '*' in state['invalid_keys']:
                            missing.append(bb_key or f'attribute:{index}')
                        elif operand_source['kind'] not in {'typed_runtime_post_assignment', 'typed_post_create_runtime_bb', 'typed_static_literal'}:
                            unverified.append(bb_key or f'attribute:{index}')
                        parameters.append({'index': index, 'attribute_type': modifier.get('attributeType'),
                            'formula': modifier.get('formulaItem'), 'bb_key': bb_key, 'operand': operand, 'operand_source': operand_source})
                    snapshot = {'ts_ms': ts_ms, 'line_no': line_no, 'operation_seq': seq,
                        'identity': copy.deepcopy(identity), 'bb_values': dict(state['values']),
                        'attribute_parameters': parameters, 'missing_parameters': sorted(set(missing)),
                        'unverified_parameters': sorted(set(unverified)),
                        'status': ('missing_attribute_parameters' if missing else 'unverified_attribute_parameters' if unverified else 'attribute_parameters_observed'),
                        'enhance_multiplier': None, 'final_modifiers_verified': False,
                        'instance_start_line_no': record['instance_start_line_no']}
                    if not duplicate:
                        record.setdefault('attribute_refresh_snapshots', []).append(snapshot)
                    event['snapshot_status'] = snapshot['status']
                    # Keep the complete observation even if this Buff falls
                    # outside the selected battle window. Share the object
                    # so later conflicting evidence invalidates both views.
                    event['snapshot'] = snapshot
            except (ValueError, TypeError, KeyError) as error:
                event['state_error'] = str(error)
                if not is_refresh and (identity_bound or identity is None):
                    affected = active_buff_starts_by_uid.get(str(fields.get('inst') or ''))
                    if (affected is not None and fields.get('ownerId') == affected.get('target_actor_id')
                            and skill_key == affected.get('event_key')):
                        unknown_key = blackboard_key or '*'
                        affected.setdefault('_dynamic_unresolved_keys', set()).add(unknown_key)
                        affected_state = affected.get('_dynamic_pending_state')
                        if affected_state is not None:
                            affected_state['invalid_keys'].add(unknown_key)
                if is_refresh:
                    unresolved_buff_actions.append({'ts_ms': ts_ms, 'line_no': line_no,
                        'uid': event.get('instance_id'), 'reason': 'unbound_buff_attribute_refresh', 'detail': str(error)})
            continue

        if " BB[" in raw_line:
            if pending_buff_record is not None:
                parsed_bb_fields = _extract_fields(raw_line)
                pending_buff_record["bb_keys"].extend(parsed_bb_fields.keys())
                for key, raw_value in parsed_bb_fields.items():
                    # Do not discard signed BB values. Negative values are
                    # meaningful for typed resistance reductions.
                    rate = _coerce_optional_float(raw_value)
                    if rate is not None:
                        pending_buff_record["bb_values"][key] = rate
                        pending_buff_record.setdefault('_observed_bb_values', {})[key] = rate
                _apply_enemy_status_bb_overrides(
                    pending_buff_record,
                    context_enemy_hint=context_enemy_hint,
                )
                _preserve_raw_numeric_internal_trigger_buff_id(pending_buff_record)
            continue

        dpd_raw = _parse_dpd_raw_line(raw_line)
        if dpd_raw is not None:
            hit = hits_by_seq.get(int(dpd_raw["seq"]))
            if hit is not None:
                hit["dpd_raw"] = {key: value for key, value in dpd_raw.items() if key != "seq"}
            continue

        baseline = _parse_baseline_line(raw_line)
        if baseline is not None:
            seq, values = baseline
            hit = hits_by_seq.get(seq)
            if hit is not None:
                hit["baseline"] = values
                baseline_by_character[str(hit.get("character_key") or "")] = values
            continue

        if ' PKT_ATTR_INFO #' in raw_line:
            info_match = re.search(r'\bPKT_ATTR_INFO #(\d+) (\{.*\})$', raw_line)
            if info_match:
                hit = hits_by_seq.get(int(info_match.group(1)))
                if hit is not None:
                    try:
                        payload = json.loads(info_match.group(2))
                        if (type(payload.get('reportPresent')) is not bool
                                or payload.get('packetSeq') not in (None, hit.get('packet_seq_id'))
                                or payload.get('packetSessionId') not in (None, hit.get('packet_session_id'))):
                            raise ValueError('attribute_report_context_mismatch')
                        if ('packet_attribute_report_present' in hit
                                and hit['packet_attribute_report_present'] != payload['reportPresent']):
                            raise ValueError('conflicting_attribute_report_presence')
                        if not hit.get('packet_attribute_report_unresolved'):
                            hit['packet_attribute_report_present'] = payload['reportPresent']
                    except (ValueError, TypeError, AttributeError):
                        hit.pop('packet_attribute_report_present', None)
                        hit['packet_attribute_report_unresolved'] = True
            continue

        if ' PKT_MOD_DETAIL #' in raw_line:
            detail_match = re.search(r'\bPKT_MOD_DETAIL #(\d+) (\{.*\})$', raw_line)
            if detail_match:
                hit = hits_by_seq.get(int(detail_match.group(1)))
                if hit is not None:
                    try:
                        payload = json.loads(detail_match.group(2))
                        if (not isinstance(payload, dict)
                                or any(not isinstance(payload.get(side), list) for side in ('attacker', 'defender'))
                                or payload.get('packetSeq') not in (None, hit.get('packet_seq_id'))
                                or payload.get('packetSessionId') not in (None, hit.get('packet_session_id'))):
                            raise ValueError('modifier_handle_context_mismatch')
                        handles = {side: [] for side in ('attacker', 'defender')}
                        for side in handles:
                            for item in payload[side]:
                                if (not isinstance(item, dict) or type(item.get('index')) is not int
                                        or item['index'] < 0 or type(item.get('buff_inst_id')) not in (int, str)
                                        or not re.fullmatch(r'[1-9]\d*', str(item['buff_inst_id']))
                                        or int(item['buff_inst_id']) >= 2**64):
                                    raise ValueError('invalid_modifier_handle')
                                handles[side].append({**item, 'buff_inst_id': str(item['buff_inst_id'])})
                        if 'packet_modifier_handles' in hit and hit['packet_modifier_handles'] != handles:
                            raise ValueError('conflicting_modifier_handles')
                        if not hit.get('packet_modifier_handles_unresolved'):
                            hit['packet_modifier_handles'] = handles
                    except (ValueError, TypeError, KeyError):
                        hit.pop('packet_modifier_handles', None)
                        hit['packet_modifier_handles_unresolved'] = True
            continue

        packet_modifiers = _parse_packet_modifier_line(raw_line)
        if packet_modifiers is not None:
            seq, attacker_modifier_uids, defender_modifier_uids = packet_modifiers
            for uid in attacker_modifier_uids + defender_modifier_uids:
                if uid:
                    packet_modifier_last_seen_by_uid[str(uid)] = max(
                        packet_modifier_last_seen_by_uid.get(str(uid), ts_ms),
                        ts_ms,
                    )
            hit = hits_by_seq.get(seq)
            if hit is not None:
                hit["packet_modifier_seen"] = True
                hit["packet_modifier_uids"] = {
                    "attacker": attacker_modifier_uids,
                    "defender": defender_modifier_uids,
                }
            continue

        packet_attrs = _parse_packet_attr_line(raw_line)
        if packet_attrs is not None:
            seq, attacker_attr_rows, defender_attr_rows = packet_attrs
            hit = hits_by_seq.get(seq)
            if hit is not None:
                observed_attrs = {
                    "attacker": attacker_attr_rows,
                    "defender": defender_attr_rows,
                }
                if hit.get('packet_attr_seen') and hit['packet_attr_details'] != observed_attrs:
                    hit['packet_attr_conflict'] = True
                    hit['packet_attribute_report_unresolved'] = True
                    hit['rdps_unresolved_effect_conditions'] = True
                elif not hit.get('packet_attr_conflict'):
                    hit['packet_attr_details'] = observed_attrs
                hit['packet_attr_seen'] = True
            continue

        poise_damage_event = _parse_poise_damage_event(raw_line, ts_ms, line_no)
        if poise_damage_event is not None:
            poise_damage_events.append(poise_damage_event)
            continue

        current_buff_record: dict[str, Any] | None = None

        if " ATTR_MOD " in raw_line or " DMG_MOD " in raw_line:
            fields = _extract_fields(raw_line)
            buff_key = _normalize_buff_id(fields.get("buff"))
            pending_attr_mods[buff_key].append(
                {
                    "attr_type": str(fields.get("attrType") or ""),
                    "bb_key": str(fields.get("bbKey") or ""),
                    "use_key": str(fields.get("useKey") or "0"),
                    "value": _safe_positive_rate(fields.get("val")),
                }
            )

        if " HP_V2 " in raw_line:
            fields = _extract_fields(raw_line)
            source_actor = _extract_character_key(fields.get("src"))
            attacker_actor = _extract_character_key(fields.get("atk"))
            if not source_actor and not attacker_actor and _extract_enemy_key(fields.get("src"), fields.get("atk")):
                # An explicitly identified enemy hit is outside player outgoing damage.
                continue
            character_key = source_actor or attacker_actor
            target_enemy_key = _extract_enemy_key(fields.get("tgt"))
            conflicting_actor = bool(source_actor and attacker_actor and source_actor != attacker_actor)
            if character_key is None or target_enemy_key is None or conflicting_actor:
                unresolved_hits.append({
                    "line_no": line_no, "ts_ms": ts_ms,
                    "source_actor": fields.get("src"), "attacker_actor": fields.get("atk"),
                    "target_actor": fields.get("tgt"), "raw_skill_key": fields.get("skill"),
                    "packet_hit_value": fields.get("hit"),
                    "reason": "conflicting_damage_actor" if conflicting_actor else "unresolved_damage_actor",
                })
                continue
            enemy_counter[target_enemy_key] += 1

            character_name = _resolve_character_name(character_key) or character_key
            raw_hit_value = _coerce_int(fields.get("hit"))
            packet_hit_value = raw_hit_value if fields.get("hit") is not None else None
            packet_raw_value = _coerce_optional_float(fields.get("raw"))
            packet_final_value = _coerce_optional_float(fields.get("packetFinalValue"))
            has_enemy_hp_after = "eHP" in fields
            enemy_hp_after = _coerce_int(fields.get("eHP"))
            target_actor_id = fields.get("tgtId") if re.fullmatch(r"[1-9]\d*", fields.get("tgtId", "")) else None
            source_actor_id = fields.get("atkId") if re.fullmatch(r"[1-9]\d*", fields.get("atkId", "")) else None
            hp_state_key = _enemy_hp_state_key(target_enemy_key, target_actor_id)
            target_entity_stats = _entity_stats_snapshot_at(entity_stats_timelines, target_actor_id, ts_ms)
            source_entity_stats = _entity_stats_snapshot_at(entity_stats_timelines, source_actor_id, ts_ms)
            hit_value = _cap_enemy_overkill_damage(
                raw_hit_value,
                enemy_hp_after=enemy_hp_after if has_enemy_hp_after else None,
                previous_enemy_hp=last_enemy_hp_by_target.get(hp_state_key),
            )
            damage_value_source = "packet_hit"
            if packet_hit_value is None:
                damage_value_source = "missing_packet_hit"
            elif hit_value != raw_hit_value:
                damage_value_source = "packet_hit_overkill_capped"
            if has_enemy_hp_after:
                last_enemy_hp_by_target[hp_state_key] = enemy_hp_after
            crit_flag = _coerce_int(fields.get("critFlag"))
            raw_skill_key = fields.get("skill") or "unknown_skill"
            original_template_int_id = _coerce_int(fields.get("origTemplateIntId"), default=0) or None
            action_id = int(fields["actionId"]) if re.fullmatch(r"\d+", fields.get("actionId", "")) else None
            damage_unit_index = int(fields["damageUnitIndex"]) if re.fullmatch(r"\d+", fields.get("damageUnitIndex", "")) else None
            damage_unit_index_source = None
            skill_key = (
                _canonical_hit_skill_id_from_cast_context(
                    raw_skill_key,
                    character_key,
                    ts_ms,
                    original_template_int_id=original_template_int_id,
                    target_enemy_key=target_enemy_key,
                    recent_skill_casts_by_char=recent_skill_casts_by_char,
                    recent_numeric_buffs_by_source=recent_numeric_buffs_by_source,
                    action_id=action_id,
                    damage_unit_index=damage_unit_index,
                    template_type=fields.get("templateType"),
                )
                or _canonical_hit_skill_id(raw_skill_key, character_key)
            )
            skill_name = _resolve_skill_name(skill_key) or skill_key
            hit_index = _coerce_int(fields.get("hits"), default=1)
            skill_profile = _resolve_skill_profile(skill_key)
            skill_group_type = None
            skill_family_key = _resolve_skill_family_key(skill_key, skill_profile)
            if skill_profile is not None:
                try:
                    skill_group_type = int(skill_profile.get("group_type"))
                except (TypeError, ValueError):
                    skill_group_type = None
            alias_combo_consume = _COMBO_CONSUME_SKILL_ALIASES.get(skill_key)
            if alias_combo_consume is not None:
                skill_group_type = int(alias_combo_consume["group_type"])
                skill_family_key = str(alias_combo_consume["family_skill_key"])

            seq_match = _HIT_SEQ_RE.search(raw_line)
            seq = int(seq_match.group(1)) if seq_match else len(hits) + 1
            dynamic_bb_values: dict[int, float] = {}
            for token in re.split(r"[,;]", str(fields.get("dynBB") or "")):
                key_text, separator, value_text = token.partition(":")
                key = _coerce_optional_int(key_text)
                value = _coerce_optional_float(value_text) if separator else None
                if key is not None and value is not None:
                    dynamic_bb_values[key] = value
            calc_bb_values: dict[int, dict[str, float | str]] = {}
            for token in re.split(r"[,;]", str(fields.get("calcBB") or "")):
                group_text, separator, assignment = token.partition(":")
                group = _coerce_optional_int(group_text)
                key_text, assignment_separator, value_text = assignment.partition("=")
                if group is None or not separator or not assignment_separator or not key_text:
                    continue
                numeric_value = _coerce_optional_float(value_text)
                calc_bb_values.setdefault(group, {})[key_text] = (
                    numeric_value if numeric_value is not None else value_text
                )
            action_damage_element = _infer_skill_action_damage_element(
                skill_key,
                action_id,
                damage_unit_index,
            )
            static_damage_decorate_mask = None
            static_damage_decorate_mask_source = None
            damage_calculation = None
            if action_id is not None and damage_unit_index is not None:
                exact_units = [unit for unit in _load_skill_action_damage_units(str(skill_key)).get(action_id, [])
                               if type(unit.get('index')) is int and unit['index'] == damage_unit_index
                               and unit.get('damage_attribute_type') == 'hp']
                if len(exact_units) == 1 and exact_units[0].get('damage_attribute_type') == 'hp':
                    unit = exact_units[0]
                    if type(unit.get('damage_decorate_mask')) is int:
                        static_damage_decorate_mask = unit['damage_decorate_mask']
                        static_damage_decorate_mask_source = unit.get('damage_decorate_mask_source')
                    damage_calculation = copy.deepcopy(unit.get('damage_calculation'))
            damage_element = action_damage_element
            damage_school = _damage_school_from_element(damage_element)
            hit = {
                "seq": seq,
                "line_no": line_no,
                "ts_ms": ts_ms,
                "character_key": character_key,
                "character_name": character_name,
                "target_enemy_key": target_enemy_key,
                "target_enemy_name": _resolve_enemy_name(target_enemy_key) or target_enemy_key,
                "raw_skill_key": raw_skill_key,
                "target_actor_id": target_actor_id,
                "source_actor_id": source_actor_id,
                "skill_key": skill_key,
                "skill_name": skill_name,
                "skill_level": _coerce_int(fields.get("skillLv"), default=0) or None,
                "template_int_id": _coerce_int(fields.get("templateIntId"), default=0) or None,
                "event_inst_id": fields.get("eventInstId") if re.fullmatch(r"[1-9]\d*", fields.get("eventInstId", "")) else None,
                "event_owner_id": fields.get("eventOwnerId") if re.fullmatch(r"[1-9]\d*", fields.get("eventOwnerId", "")) else None,
                "packet_seq_id": int(fields["packetSeq"]) if re.fullmatch(r"\d+", fields.get("packetSeq", "")) else None,
                "packet_session_id": fields.get('packetSessionId') or None,
                "packet_owner_id": (fields.get('packetOwnerId') if re.fullmatch(r'[1-9]\d*', fields.get('packetOwnerId', ''))
                                    and int(fields['packetOwnerId']) < 2**64 else None),
                "packet_generation": (int(fields['packetGeneration']) if re.fullmatch(r'\d+', fields.get('packetGeneration', '')) else None),
                "client_tick_ms": int(fields["clientTick"]) if re.fullmatch(r"\d+", fields.get("clientTick", "")) else None,
                "action_id": action_id,
                "damage_unit_index": damage_unit_index,
                "damage_unit_index_source": damage_unit_index_source,
                "original_template_int_id": original_template_int_id,
                "dynamic_bb_signature": fields.get("dynBB"),
                "dynamic_bb_values": dynamic_bb_values,
                "calc_bb_signature": fields.get("calcBB"),
                "calc_bb_values": calc_bb_values,
                "damage_element": damage_element,
                "damage_school": damage_school,
                "damage_element_source": "official_action_unit" if damage_element else None,
                "damage_decorate_mask": None,
                "damage_decorate_mask_source": None,
                "static_damage_decorate_mask": static_damage_decorate_mask,
                "static_damage_decorate_mask_source": static_damage_decorate_mask_source,
                "damage_calculation": damage_calculation,
                "skill_group_type": skill_group_type,
                "skill_family_key": skill_family_key,
                "hit_value": hit_value,
                "raw_hit_value": raw_hit_value,
                "packet_hit_value": packet_hit_value,
                "packet_raw_value": packet_raw_value,
                "packet_final_value": packet_final_value,
                "damage_value_source": damage_value_source,
                "rdps_basis_value": hit_value,
                "rdps_basis_source": damage_value_source,
                "overkill_damage": max(0, raw_hit_value - hit_value),
                "enemy_hp_after": enemy_hp_after,
                "enemy_hp_after_observed": has_enemy_hp_after,
                "crit_flag": crit_flag,
                "critical_flag_observed": fields.get("critFlag") in {"0", "1"},
                "critical_observation": copy.deepcopy(critical_stats_by_actor.get(source_actor_id)),
                "part_inst_id": _coerce_optional_int(fields.get("partInstId")),
                "hit_index": hit_index,
                "dpd_raw": None,
                "baseline": baseline_by_character.get(character_key),
                "packet_modifier_seen": False,
                "packet_modifier_uids": {"attacker": [], "defender": []},
                "packet_attr_details": {"attacker": [], "defender": []},
            }
            _is_arts_strength_damage_hit(hit)
            _attach_target_entity_stats(hit, target_entity_stats)
            _attach_attacker_entity_stats(hit, source_entity_stats)
            # Scene snapshots are observations at their own timestamp. They do
            # not establish current-hit modifier state or passive coverage.
            hits.append(hit)
            hits_by_seq[seq] = hit
            if loadout_by_char and character_key not in loadout_by_char:
                # A battle actor outside the frozen loadout proves that the
                # client carried an old bag team across an empty dungeon-team
                # packet. Stop using that loadout for subsequent inference.
                loadout_by_char.clear()
                active_suits_by_char.clear()
                active_weapons_by_char.clear()
            if character_key not in roster_seen:
                roster_seen.add(character_key)
                roster_order.append(character_key)

            participant = participant_totals[character_key]
            participant["character_key"] = character_key
            participant["character_name"] = character_name
            participant["total_damage"] += hit_value
            participant["max_hit"] = max(participant["max_hit"], hit_value)
            participant["crit_hits"] += 1 if crit_flag else 0
            participant["hit_count"] += 1

            skill_entry = skill_totals[(character_key, skill_key)]
            skill_entry["character_key"] = character_key
            skill_entry["character_name"] = character_name
            skill_entry["skill_key"] = skill_key
            skill_entry["skill_name"] = skill_name
            skill_entry["total_damage"] += hit_value
            skill_entry["max_damage"] = max(skill_entry["max_damage"], hit_value)
            skill_entry["hit_count"] += 1
            if hit_index == 1:
                skill_entry["cast_count"] += 1

        elif " BUFF_START " in raw_line:
            fields = _extract_fields(raw_line)
            owner_value = str(fields.get("owner") or "")
            owner_key = _extract_character_key(owner_value)
            target_enemy_key = _extract_enemy_key(fields.get("owner"))
            raw_buff_key = _normalize_buff_id(fields.get("id"))
            raw_source_key = _extract_character_key(fields.get("src"))
            source_key = raw_source_key
            mapping_hint = _packet_numeric_buff_hint(raw_buff_key)
            mapping_rejected = False
            if mapping_hint and not _packet_mapping_applies(
                mapping_hint,
                owner_key=owner_key,
                source_key=source_key,
                active_suits_by_char=active_suits_by_char,
                active_weapons_by_char=active_weapons_by_char,
            ):
                mapping_rejected = True
                mapping_hint = {}
            buff_key = _canonical_packet_buff_id(
                raw_buff_key,
                owner_key=owner_key,
                source_key=source_key,
                active_suits_by_char=active_suits_by_char,
                active_weapons_by_char=active_weapons_by_char,
            )
            is_weapon_buff = bool(_WEAPON_BUFF_RE.match(buff_key))
            player_keys = [key for key in (source_key, owner_key) if key]
            if not player_keys and not target_enemy_key:
                pending_buff_record = None
                continue
            for player_key in player_keys:
                if player_key not in roster_seen:
                    roster_seen.add(player_key)
                    roster_order.append(player_key)
            if source_key:
                source_participant = participant_totals[source_key]
                source_participant["character_key"] = source_key
                source_participant["character_name"] = _resolve_character_name(source_key) or source_key
            if owner_key:
                owner_participant = participant_totals[owner_key]
                owner_participant["character_key"] = owner_key
                owner_participant["character_name"] = _resolve_character_name(owner_key) or owner_key
            attr_mods = pending_attr_mods.pop(buff_key, [])
            if not attr_mods and raw_buff_key != buff_key:
                attr_mods = pending_attr_mods.pop(raw_buff_key, [])
            source_skill_context = _recent_source_skill_context_for_buff(
                source_key,
                ts_ms,
                recent_skill_casts_by_char,
            ) or {}
            raw_uid = str(fields.get("uid") or "")
            # uint32 max is the protocol's invalid/sentinel instance ID. Keep
            # these rows for diagnostics, but never create a live window from
            # them or evaluate them as a semantic buff instance.
            invalid_uid = raw_uid in {"0", "4294967295"}
            buff_record = {
                "uid": "" if invalid_uid else raw_uid,
                "uid_observation_only": invalid_uid,
                "line_no": line_no,
                "ts_ms": ts_ms,
                "source_character_key": source_key,
                "source_character_name": _resolve_character_name(source_key) or source_key,
                "source_weapon_template": active_weapons_by_char.get(source_key),
                "source_skill_key": source_skill_context.get("source_skill_key"),
                "source_skill_family_key": source_skill_context.get("source_skill_family_key"),
                "raw_source_character_key": raw_source_key,
                "raw_source_character_name": _resolve_character_name(raw_source_key) or raw_source_key,
                "raw_source": fields.get("src"),
                "target_character_key": owner_key,
                "target_character_name": _resolve_character_name(owner_key) or owner_key,
                "target_enemy_key": target_enemy_key,
                "target_enemy_name": _resolve_enemy_name(target_enemy_key) or target_enemy_key,
                "owner_raw": owner_value,
                "target_actor_id": fields.get("ownerId") if re.fullmatch(r"[1-9]\d*", fields.get("ownerId", "")) else None,
                "source_actor_id": fields.get("srcId") if re.fullmatch(r"[1-9]\d*", fields.get("srcId", "")) else None,
                "event_key": buff_key,
                "raw_event_key": raw_buff_key,
                "packet_mapping": mapping_hint if mapping_hint else None,
                "packet_mapping_rejected": mapping_rejected,
                "raw_duration_ms": _duration_ms_from_seconds(fields.get("dur")),
                "end_ts_ms": None,
                "bb_keys": [row["bb_key"] for row in attr_mods if row["bb_key"]],
                "bb_values": {},
                "attr_types": [row["attr_type"] for row in attr_mods if row["attr_type"]],
                "attr_mods": attr_mods,
                "is_weapon_buff": is_weapon_buff,
                "instance_start_line_no": line_no,
                "enable_observations": [],
                "creation_observations": [],
                "attribute_refresh_snapshots": [],
                "_observed_bb_values": {},
            }
            _apply_enemy_status_bb_overrides(buff_record, context_enemy_hint=None)
            uid_key = str(buff_record.get("uid") or "")
            if uid_key:
                existing = active_buff_starts_by_uid.get(uid_key)
                if existing is not None:
                    # A new observation of a live UID changes its snapshot,
                    # not the number of simultaneous Buff instances.
                    buff_record['_previous_snapshot_record'] = existing
                    buff_record['_previous_snapshot_boundary'] = {
                        'snapshot_end_ts_ms': existing.get('snapshot_end_ts_ms'),
                        'snapshot_end_line_no': existing.get('snapshot_end_line_no')}
                    existing['snapshot_end_ts_ms'] = ts_ms
                    existing['snapshot_end_line_no'] = line_no
                    buff_record['snapshot_update'] = True
                    buff_record['instance_start_line_no'] = existing.get('instance_start_line_no', existing['line_no'])
                    buff_record['source_weapon_template'] = existing.get('source_weapon_template')
                    identity_fields = ('source_actor_id', 'target_actor_id', 'source_character_key',
                                       'target_character_key', 'target_enemy_key')
                    conflict = bool(existing.get('rdps_live_identity_conflict')) or any(
                        existing.get(key) and buff_record.get(key) and existing[key] != buff_record[key]
                        for key in identity_fields)
                    known_roots = _load_current_buff_roots()
                    if existing.get('event_key') in known_roots and buff_record['event_key'] in known_roots:
                        conflict = conflict or existing['event_key'] != buff_record['event_key']
                    if conflict:
                        buff_record['rdps_live_identity_conflict'] = True
                    else:
                        for key in (*identity_fields, 'source_skill_key', 'source_skill_family_key'):
                            if not buff_record.get(key) and existing.get(key):
                                buff_record[key] = existing[key]
                        # State history belongs to this live UID. Sharing it
                        # retains later observations while evaluation uses
                        # the hit's own operation/time boundary.
                        buff_record['enable_observations'] = existing['enable_observations']
                        buff_record['creation_observations'] = copy.deepcopy(existing.get('creation_observations') or [])
                        buff_record['attribute_refresh_snapshots'] = existing.get('attribute_refresh_snapshots', [])
                        if existing.get('_dynamic_pending_state') is not None:
                            buff_record['_dynamic_pending_state'] = copy.deepcopy(existing['_dynamic_pending_state'])
                            buff_record['_dynamic_seen_contexts'] = set(existing.get('_dynamic_seen_contexts') or [])
                        buff_record['_dynamic_unresolved_keys'] = set(existing.get('_dynamic_unresolved_keys') or [])
                        if existing.get('rdps_dynamic_identity_conflict'):
                            buff_record['rdps_dynamic_identity_conflict'] = True
                        if existing.get('rdps_conflicting_enable_state'):
                            buff_record['rdps_conflicting_enable_state'] = True
                        if existing.get('event_key') == buff_record['event_key']:
                            buff_record['bb_values'] = dict(existing.get('bb_values') or {})
                            buff_record['bb_keys'] = list(existing.get('bb_keys') or [])
                            buff_record['origin_observations'] = copy.deepcopy(existing.get('origin_observations') or [])
                            if not attr_mods:
                                buff_record['attr_mods'] = copy.deepcopy(existing.get('attr_mods') or [])
                                buff_record['attr_types'] = list(existing.get('attr_types') or [])
            buff_starts.append(buff_record)
            if raw_buff_key.isdigit() and source_key:
                rows = recent_numeric_buffs_by_source[str(source_key)]
                rows.append(buff_record)
                del rows[:-128]
            if uid_key:
                active_buff_starts_by_uid[uid_key] = buff_record
            current_buff_record = buff_record

        elif " BUFF_END " in raw_line:
            fields = _extract_fields(raw_line)
            uid = str(fields.get("uid") or "")
            if uid:
                for unresolved in unresolved_buff_actions:
                    if unresolved.get('uid') == uid and unresolved.get('end_ts_ms') is None:
                        unresolved.update(end_ts_ms=ts_ms, end_line_no=line_no)
                buff_record = active_buff_starts_by_uid.pop(uid, None)
                if buff_record is not None:
                    buff_record["end_ts_ms"] = ts_ms
                    buff_record["end_line_no"] = line_no
                    current_buff_record = buff_record

        pending_buff_record = current_buff_record

    loadout_by_char = _repair_weapon_puton_loadout_groups(loadout_groups, loadout_by_char)
    for event in buff_attribute_refresh_events:
        if isinstance(event.get('snapshot'), dict):
            event['snapshot_status'] = event['snapshot']['status']
    _apply_poise_damage_events_to_hits(hits, poise_damage_events)

    buff_records_by_instance: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in buff_starts:
        record["dynamic_bb_events"] = []
        uid = str(record.get("uid") or "")
        if uid:
            buff_records_by_instance[uid].append(record)
    for event in dynamic_bb_events:
        stamp = (int(event["ts_ms"]), int(event["line_no"]))
        candidates = [
            record for record in buff_records_by_instance.get(str(event.get("instance_id") or ""), [])
            if event.get("owner_id") and record.get("target_actor_id")
            and str(event["owner_id"]) == str(record["target_actor_id"])
            and event.get("skill_key") == record.get("event_key")
            and stamp >= (int(record["ts_ms"]), int(record["line_no"]))
            and (record.get("end_ts_ms") is None or stamp < (
                int(record["end_ts_ms"]), int(record.get("end_line_no") or 0)))
            and (record.get('snapshot_end_ts_ms') is None or stamp < (
                int(record['snapshot_end_ts_ms']), int(record.get('snapshot_end_line_no') or 0)))
        ]
        if len(candidates) == 1:
            candidates[0]["dynamic_bb_events"].append(event)
        elif len(candidates) > 1:
            event["binding_error"] = "ambiguous_buff_instance"

    # Cast observations require matching owner/instance/template. HP_V2 does
    # not carry the cast instance, so proximity cannot bind these to a hit.
    for cast in skill_casts:
        cast["dynamic_bb_events"] = []
    for event in dynamic_bb_events:
        stamp = (int(event["ts_ms"]), int(event["line_no"]))
        candidates = [cast for cast in skill_casts
            if event.get("owner_id") and cast.get("owner_id")
            and str(event["owner_id"]) == str(cast["owner_id"])
            and event.get("instance_id") == cast.get("skill_inst_id")
            and event.get("skill_key") == cast.get("skill")
            and cast.get("end_ts_ms") is not None
            and (int(cast["ts_ms"]), int(cast["line_no"])) <= stamp
            < (int(cast["end_ts_ms"]), int(cast.get("end_line_no") or 0))]
        if len(candidates) == 1:
            candidates[0]["dynamic_bb_events"].append(event)
        elif len(candidates) > 1:
            event["binding_error"] = "ambiguous_skill_instance"
    for hit in hits:
        hit["dynamic_bb_observations"] = []

    timer_evidence = collect_timer_evidence(text, _extract_fields, _parse_prefixed_timestamp_ms, timer_observed_times)
    if timer_evidence is not None:
        start = timer_evidence.get('start') or {}
        complete = timer_evidence.get('complete') or {}
        if start.get('gameId'):
            set_dungeon_context(start['gameId'])
        official_timer_start_ms = start.get('observedMs')
        official_timer_end_ms = complete.get('observedMs')
        official_pass_confirmed = complete.get('isPass') == '1'
        challenge_pass_confirmed = challenge_pass_confirmed or official_pass_confirmed
        official_timer_elapsed_ms = None
        raw_pass_time = complete.get('passTimeMs')
        if isinstance(raw_pass_time, str) and raw_pass_time.isdecimal() and int(raw_pass_time) > 0:
            official_timer_elapsed_ms = int(raw_pass_time)
        game_timer_start_ms = official_timer_start_ms
        game_timer_end_ms = official_timer_end_ms
        game_timer_elapsed_ms = official_timer_elapsed_ms if official_timer_elapsed_ms is not None else live_timer_elapsed_ms
        game_timer_window_elapsed_ms = game_timer_elapsed_ms
        timer_start_seen = official_timer_start_ms is not None
        timer_end_seen = official_timer_end_ms is not None
        completion_source = 'official_timer_end' if official_pass_confirmed else completion_source
    # Packet receipt timestamps are diagnostic only. A negative wall-clock
    # difference may be a date boundary; it does not disprove server passTime.
    official_timer_observed_elapsed_ms = (
        official_timer_end_ms - official_timer_start_ms
        if official_timer_start_ms is not None and official_timer_end_ms is not None
        and official_timer_end_ms >= official_timer_start_ms else None
    )

    official_scope = (timer_evidence or {}).get('start')
    if official_scope is not None:
        # Membership follows explicit lifecycle events, not receipt-clock
        # proximity. A new challenge cannot inherit a previous one's hits.
        scope_end_line = ((timer_evidence or {}).get('complete') or {}).get('line', len(text.splitlines()) + 1)
        scope_end_line = min([scope_end_line] + [
            event['line'] for event in timer_evidence['events']
            if event.get('kind') == 'boundary'
            and event.get('gameId') in (None, '', official_scope.get('gameId'))
        ])
        hits = [hit for hit in hits if official_scope['line'] < hit['line_no'] < scope_end_line]

    if not hits and (loadout_by_char or dungeon_context_id or unresolved_hits or unresolved_buff_actions):
        anchor_ms = (
            official_timer_start_ms
            if official_timer_start_ms is not None
            else first_party_action_ms
            if first_party_action_ms is not None
            else game_timer_start_ms
            if game_timer_start_ms is not None
            else first_context_ms
            if first_context_ms is not None
            else 0
        )
        battle_loadout_by_char = _select_loadout_snapshot_for_battle(
            loadout_groups,
            loadout_by_char,
            anchor_ms=anchor_ms,
        )
        loadout = sorted(
            battle_loadout_by_char.values(),
            key=lambda item: (int(item.get("slot") or 0), str(item.get("character_key") or "")),
        )
        roster = [
            {
                "slot": int(row.get("slot") or index),
                "character_key": str(row.get("character_key") or ""),
                "character_name": str(row.get("character_name") or row.get("character_key") or ""),
            }
            for index, row in enumerate(loadout, start=1)
            if row.get("character_key")
        ]
        end_ms = last_context_ms if last_context_ms is not None else anchor_ms
        dungeon_key, dungeon_name = dungeon_context or (UNKNOWN_DUNGEON_KEY, UNKNOWN_DUNGEON_NAME)
        time_source = "party_action_window" if first_party_action_ms is not None else "battle_ready"
        fingerprint_payload = {
            "rules_version": RULES_VERSION,
            "time_source": time_source,
            "loadout": [row["character_key"] for row in roster],
            "anchor_ms": anchor_ms,
        }
        return {
            "battle": {
                "dungeon_key": dungeon_key,
                "dungeon_name": dungeon_name,
                "boss_key": "unknown_boss",
                "boss_name": "未知对象",
                "boss_identity_source": "missing_damage_target",
                "dungeon_context_id": dungeon_context_id,
                "dungeon_identity_source": (
                    "dungeon_context"
                    if dungeon_context is not None
                    else "unmapped_dungeon_context"
                    if dungeon_context_id
                    else "missing_dungeon_context"
                ),
                "battle_start_at": _build_iso_datetime(reference_date, anchor_ms),
                "battle_end_at": _build_iso_datetime(reference_date, end_ms),
                "duration_ms": 0,
                "time_source": time_source,
                "timeline_zero_source": time_source,
                "timer_start_seen": timer_start_seen,
                "timer_end_seen": timer_end_seen,
                "official_timer_start_seen": official_timer_start_ms is not None,
                "official_timer_end_seen": official_timer_end_ms is not None,
                "official_timer_observed_elapsed_ms": official_timer_observed_elapsed_ms,
                "packet_timer_elapsed_ms": packet_timer_elapsed_ms,
                "timer_integrity_ok": False if official_pass_confirmed else None,
                "timer_integrity_reasons": (
                    ["missing_damage_events"] if official_pass_confirmed else []
                ),
                "timer_start_inferred": False,
                "clear_flag": False,
                "total_damage": 0,
                "total_dps": 0.0,
                "roster": roster,
                "battle_fingerprint": build_canonical_sha256(fingerprint_payload),
                "parser_version": PARSER_VERSION,
                "rules_version": RULES_VERSION,
                "source_file_name": file_name,
            },
            "participants": [],
            "loadout": loadout,
            "buff_events": [],
            "timeline_events": [],
            "role_skill_stats": [],
            "debug_hits": [],
            "unresolved_hits": unresolved_hits,
            "unresolved_buff_actions": unresolved_buff_actions,
            "rdps_preflight": {
                "ok": False, "blocker_count": max(1, len(unresolved_hits) + len(unresolved_buff_actions)),
                "blocker_reason_counts": dict(Counter(row['reason'] for row in
                    (unresolved_hits + unresolved_buff_actions or [{'reason': 'missing_damage_events'}]))),
                "blockers": (unresolved_hits + unresolved_buff_actions)[:100] or [{"reason": "missing_damage_events"}],
            },
            "rdps_damage_basis": {
                "rdps_preflight_ok": False, "rdps_strict_ok": False,
                "rdps_conservation_ok": False, "hit_count": 0,
                "unresolved_damage_actor_count": len(unresolved_hits),
            },
            "casts": [],
            "char_skills": {},
        }

    if not hits:
        raise ValueError("未在日志中解析到任何 HP_V2 伤害事件。")

    _extend_buff_records_from_packet_modifiers(buff_starts, packet_modifier_last_seen_by_uid)
    _infer_related_buff_end_times(buff_starts, skill_casts)
    _apply_same_frame_trigger_skill_mappings(hits, buff_starts)
    _resolve_weapon_buff_sources(buff_starts)
    for buff_record in buff_starts:
        source_key = buff_record.get("source_character_key")
        if not source_key:
            continue
        if source_key not in roster_seen:
            roster_seen.add(source_key)
            roster_order.append(source_key)
        source_participant = participant_totals[source_key]
        source_participant["character_key"] = source_key
        source_participant["character_name"] = _resolve_character_name(source_key) or source_key

    known_enemy_keys = {
        enemy_key
        for enemy_key in enemy_counter
        if enemy_key and enemy_key != UNKNOWN_ENEMY_KEY
    }
    should_retarget_unknown = (
        bool(context_enemy_hint)
        and UNKNOWN_ENEMY_KEY in enemy_counter
        and (not known_enemy_keys or known_enemy_keys == {context_enemy_hint})
    )
    identity_retargeted = bool(
        should_retarget_unknown
        and _retarget_unknown_enemy_hits(hits, buff_starts, context_enemy_hint)
    )
    if identity_retargeted:
        (
            participant_totals,
            skill_totals,
            roster_order,
            roster_seen,
            enemy_counter,
        ) = _rebuild_hit_totals(hits, buff_starts)

    initial_boss_key = enemy_counter.most_common(1)[0][0] if enemy_counter else "unknown_boss"
    first_party_hit_ms = next(
        (
            int(hit["ts_ms"])
            for hit in hits
            if str(hit.get("character_key") or "").startswith("chr_")
            and str(hit.get("target_enemy_key") or "").startswith("eny_")
        ),
        None,
    )
    first_party_boss_hit_ms = next(
        (
            int(hit["ts_ms"])
            for hit in hits
            if str(hit.get("character_key") or "").startswith("chr_")
            and str(hit.get("target_enemy_key") or "") == initial_boss_key
        ),
        None,
    )

    def _fallback_hit_window() -> tuple[int, int, str]:
        if first_hit_hint_ms is not None:
            return (
                first_hit_hint_ms,
                last_hit_hint_ms if last_hit_hint_ms is not None else hits[-1]["ts_ms"],
                "hit_hint_window",
            )
        if first_party_action_ms is not None:
            return first_party_action_ms, last_hit_hint_ms if last_hit_hint_ms is not None else hits[-1]["ts_ms"], "party_action_window"
        if first_party_boss_hit_ms is not None:
            return first_party_boss_hit_ms, last_hit_hint_ms if last_hit_hint_ms is not None else hits[-1]["ts_ms"], "party_boss_hit_window"
        if first_party_hit_ms is not None:
            return first_party_hit_ms, last_hit_hint_ms if last_hit_hint_ms is not None else hits[-1]["ts_ms"], "party_hit_window"
        return (
            first_hit_hint_ms if first_hit_hint_ms is not None else hits[0]["ts_ms"],
            last_hit_hint_ms if last_hit_hint_ms is not None else hits[-1]["ts_ms"],
            "hit_window",
        )

    time_source = "observed_challenge_window" if timer_evidence is not None and official_timer_elapsed_ms is None else "game_timer"
    timeline_zero_source = "game_timer"
    if game_timer_elapsed_ms is not None:
        timer_window_elapsed_ms = game_timer_window_elapsed_ms or game_timer_elapsed_ms
        if (
            official_timer_elapsed_ms is not None
            and game_timer_start_ms is not None
            and game_timer_end_ms is not None
            and game_timer_end_ms >= game_timer_start_ms
        ):
            first_hit_ms = game_timer_start_ms
            last_hit_ms = game_timer_end_ms
            timeline_zero_source = (
                "official_timer_start"
                if official_timer_start_ms is not None and official_timer_start_ms == game_timer_start_ms
                else "game_timer_start"
            )
        elif game_timer_start_ms is not None:
            first_hit_ms = game_timer_start_ms
            last_hit_ms = first_hit_ms + timer_window_elapsed_ms
            timeline_zero_source = (
                "official_timer_start"
                if official_timer_start_ms is not None and official_timer_start_ms == game_timer_start_ms
                else "game_timer_start"
            )
        elif game_timer_end_ms is not None:
            last_hit_ms = game_timer_end_ms
            first_hit_ms = last_hit_ms - timer_window_elapsed_ms
            timeline_zero_source = "timer_end_inferred"
        else:
            first_hit_ms = first_hit_hint_ms if first_hit_hint_ms is not None else hits[0]["ts_ms"]
            last_hit_ms = first_hit_ms + timer_window_elapsed_ms
            timeline_zero_source = "timer_elapsed_first_hit_inferred"
    elif game_timer_start_ms is not None:
        _, fallback_last_hit_ms, _ = _fallback_hit_window()
        first_hit_ms = game_timer_start_ms
        last_hit_ms = max(fallback_last_hit_ms, hits[-1]["ts_ms"])
        timeline_zero_source = (
            "official_timer_start"
            if official_timer_start_ms is not None and official_timer_start_ms == game_timer_start_ms
            else "game_timer_start"
        )
    else:
        first_hit_ms, last_hit_ms, time_source = _fallback_hit_window()
        timeline_zero_source = time_source
    if last_hit_ms < first_hit_ms:
        last_hit_ms += 24 * 60 * 60 * 1000

    has_game_timer_window = game_timer_start_ms is not None or game_timer_elapsed_ms is not None
    if has_game_timer_window:
        windowed_hits = hits if official_scope is not None else [
            hit for hit in hits if first_hit_ms <= int(hit["ts_ms"]) <= last_hit_ms]
        if windowed_hits:
            buff_starts = _filter_buff_starts_for_timer_window(
                buff_starts,
                battle_start_ms=first_hit_ms,
            )
            hits = windowed_hits
            (
                participant_totals,
                skill_totals,
                roster_order,
                roster_seen,
                enemy_counter,
            ) = _rebuild_hit_totals(hits, buff_starts)
        else:
            timer_window_valid = False
            game_timer_start_ms = None
            game_timer_end_ms = None
            game_timer_elapsed_ms = None
            game_timer_window_elapsed_ms = None
            has_game_timer_window = False
            first_hit_ms, last_hit_ms, time_source = _fallback_hit_window()
            time_source = "invalid_timer_window"
            timeline_zero_source = "invalid_timer_window"
            if last_hit_ms < first_hit_ms:
                last_hit_ms += 24 * 60 * 60 * 1000
            if time_source in {"party_boss_hit_window", "party_hit_window"}:
                buff_starts = _filter_buff_starts_for_timer_window(
                    buff_starts,
                    battle_start_ms=first_hit_ms,
                )
                windowed_hits = [hit for hit in hits if int(hit["ts_ms"]) >= first_hit_ms]
                if windowed_hits:
                    hits = windowed_hits
                    (
                        participant_totals,
                        skill_totals,
                        roster_order,
                        roster_seen,
                        enemy_counter,
                    ) = _rebuild_hit_totals(hits, buff_starts)
    elif time_source in {"party_boss_hit_window", "party_hit_window"}:
        buff_starts = _filter_buff_starts_for_timer_window(
            buff_starts,
            battle_start_ms=first_hit_ms,
        )
        windowed_hits = [hit for hit in hits if int(hit["ts_ms"]) >= first_hit_ms]
        if windowed_hits:
            hits = windowed_hits
            (
                participant_totals,
                skill_totals,
                roster_order,
                roster_seen,
                enemy_counter,
            ) = _rebuild_hit_totals(hits, buff_starts)

    boss_key = enemy_counter.most_common(1)[0][0] if enemy_counter else "unknown_boss"
    boss_name = _resolve_enemy_name(boss_key) or boss_key
    dungeon_key, dungeon_name = dungeon_context or (UNKNOWN_DUNGEON_KEY, UNKNOWN_DUNGEON_NAME)
    if timer_evidence is not None or dungeon_key.startswith("indie_battletower"):
        # War Echo records are leaderboard data. Only the server's official
        # challenge-complete message may mark them cleared; HP zero, scene
        # exits and late dungeon status are retained as diagnostics only.
        clear_flag = official_pass_confirmed
    else:
        clear_flag = challenge_pass_confirmed or any(
            hit["target_enemy_key"] == boss_key and hit.get("enemy_hp_after", 1) <= 0
            for hit in hits
        )
    if not timer_window_valid:
        clear_flag = False
    duration_ms = max(
        game_timer_elapsed_ms if game_timer_elapsed_ms is not None else last_hit_ms - first_hit_ms,
        1,
    )
    timer_integrity_reasons: list[str] = []
    if official_pass_confirmed:
        timer_integrity_reasons = validate_timer_evidence(
            timer_evidence, duration_ms=duration_ms, game_id=dungeon_context_id)
    timer_integrity_ok = not timer_integrity_reasons if official_pass_confirmed else None
    wall_window_ms = max(last_hit_ms - first_hit_ms, 1)
    timeline_scale = duration_ms / wall_window_ms if has_game_timer_window else 1.0
    timer_start_inferred = timeline_zero_source in {
        "timer_end_inferred",
        "timer_elapsed_first_hit_inferred",
    }

    def battle_offset_ms(ts_ms: int) -> int:
        return max(int(round((int(ts_ms) - first_hit_ms) * timeline_scale)), 0)

    def battle_offset_ms_unclamped(ts_ms: int) -> int:
        return int(round((int(ts_ms) - first_hit_ms) * timeline_scale))

    total_damage = sum(hit["hit_value"] for hit in hits)
    total_dps = round(total_damage / (duration_ms / 1000), 2)
    battle_loadout_by_char = _select_loadout_snapshot_for_battle(
        loadout_groups,
        loadout_by_char,
        anchor_ms=int(hits[0]["ts_ms"]) if hits else first_hit_ms,
        anchor_line_no=int(hits[0].get("line_no") or 0) or None if hits else None,
    )
    observed_damage_characters = {
        str(hit.get("character_key") or "")
        for hit in hits
        if str(hit.get("character_key") or "").startswith("chr_")
    }
    selected_loadout_characters = set(battle_loadout_by_char)
    loadout_stale = bool(
        selected_loadout_characters
        and observed_damage_characters - selected_loadout_characters
    )
    if loadout_stale:
        battle_loadout_by_char = {}
        active_suits_by_char.clear()
    active_weapons_by_char = {
        str(character_key): str(row.get("weapon_template") or "")
        for character_key, row in battle_loadout_by_char.items()
        if isinstance(row, dict) and row.get("weapon_template")
    }
    for loadout_row in battle_loadout_by_char.values():
        if isinstance(loadout_row, dict):
            loadout_row["static_multiplier_entries"] = _derive_static_self_multiplier_entries(loadout_row)
            loadout_row["static_arts_strength_entries"] = _derive_static_arts_strength_entries(loadout_row)
    static_entries_by_char = {
        str(character_key): list(row.get("static_multiplier_entries") or [])
        for character_key, row in battle_loadout_by_char.items()
        if isinstance(row, dict)
    }
    static_arts_strength_entries_by_char = {
        str(character_key): list(row.get("static_arts_strength_entries") or [])
        for character_key, row in battle_loadout_by_char.items()
        if isinstance(row, dict)
    }
    static_arts_strength_by_char = {
        character_key: sum(float(entry.get("rate") or 0.0) for entry in entries)
        for character_key, entries in static_arts_strength_entries_by_char.items()
    }
    # Static loadout effects remain unresolved unless their exact packet-level
    # application and hit-time contribution are modeled.  Repeated snapshots,
    # matching totals, or the presence of a runtime BB are observations only;
    # they cannot retire a blocker or retroactively alter the rDPS baseline.
    for hit in hits:
        character_key = str(hit.get("character_key") or "")
        hit["static_multiplier_entries"] = list(static_entries_by_char.get(character_key, []))
        loadout_row = battle_loadout_by_char.get(character_key) or {}
        static_sources = list(loadout_row.get(
            'rdps_unresolved_static_multiplier_sources', []))
        # Field count and an equipment marker do not prove that each passive
        # has been modeled or is represented by a hit-time baseline.
        hit['rdps_unresolved_static_multiplier_sources'] = static_sources
        hit["static_arts_strength_entries"] = static_arts_strength_entries_by_char.get(character_key, [])
        flat_entries = [entry for entry in loadout_row.get('equipment_baseline_attributes') or []
                        if entry.get('attr_type') == 2 and entry.get('modifier_type') == 7]
        hit['static_flat_attack'] = sum(float(entry['value']) for entry in flat_entries)
        hit['static_flat_attack_entries'] = copy.deepcopy(flat_entries)
        level = hit.get('attacker_stats_level') or loadout_row.get('character_level')
        base_stats = _character_base_stats(character_key, level) or {}
        basic_attack = _coerce_optional_float((hit.get('attacker_stats_basic_attrs') or {}).get(2))
        if basic_attack is None:
            basic_attack = _coerce_optional_float(base_stats.get('atk'))
        weapon_attack = _coerce_optional_float(loadout_row.get('weapon_base_atk'))
        if basic_attack is not None and basic_attack > 0 and weapon_attack is not None and weapon_attack > 0:
            hit['base_attack_for_percent'] = basic_attack + weapon_attack
        attrs = hit.get('attacker_stats_attrs') or {}
        main_value = _coerce_optional_float(attrs.get(base_stats.get('main_attr_type')))
        sub_value = _coerce_optional_float(attrs.get(base_stats.get('sub_attr_type')))
        main_scale = _coerce_optional_float(attrs.get(78))
        sub_scale = _coerce_optional_float(attrs.get(79))
        if all(value is not None and value >= 0 for value in (main_value, sub_value, main_scale, sub_scale)):
            hit['attack_ability_factor'] = 1.0 + int(main_value) * main_scale + int(sub_value) * sub_scale
    _annotate_hit_enemy_hp_state(hits)

    roster = []

    buff_windows = []
    for buff_record in buff_starts:
        if buff_record.get("uid_observation_only"):
            continue
        labels = _collect_buff_labels(buff_record, root_lookup_cache=buff_root_action_indices)
        zone_effects = _collect_zone_effects(buff_record, root_lookup_cache=buff_root_action_indices)
        dynamic_effects = _extract_dynamic_effect_specs(buff_record, root_lookup_cache=buff_root_action_indices)
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
        start_ts_ms = buff_record["ts_ms"]
        window_end_ms = _buff_record_end_ts_ms(buff_record, last_hit_ms)
        end_line_no = None
        if buff_record.get('snapshot_end_ts_ms') == window_end_ms:
            end_line_no = buff_record.get('snapshot_end_line_no')
        elif buff_record.get('end_ts_ms') == window_end_ms:
            end_line_no = buff_record.get('end_line_no')
        buff_windows.append(
            {
                "start_ts_ms": start_ts_ms,
                "start_line_no": buff_record.get("line_no"),
                "end_ts_ms": window_end_ms,
                "end_line_no": end_line_no,
                "duration_ms": max(0, window_end_ms - start_ts_ms),
                "snapshot_end_ts_ms": buff_record.get('snapshot_end_ts_ms'),
                "snapshot_end_line_no": buff_record.get('snapshot_end_line_no'),
                "snapshot_update": bool(buff_record.get('snapshot_update')),
                "lifetime_mode": buff_record.get('lifetime_mode'),
                "lifetime_source": buff_record.get('lifetime_source'),
                "end_is_observation_boundary": buff_record.get('end_ts_ms') is None and buff_record.get('snapshot_end_ts_ms') is None and buff_record.get('lifetime_mode') == 'Infinity',
                "uid": buff_record.get("uid"),
                "uid_aliases": [],
                "source_character_key": buff_record["source_character_key"],
                "source_character_name": buff_record["source_character_name"],
                "source_static_arts_strength": static_arts_strength_by_char.get(
                    str(buff_record.get("source_character_key") or ""),
                    0.0,
                ),
                "source_skill_key": buff_record.get("source_skill_key"),
                "source_skill_family_key": buff_record.get("source_skill_family_key"),
                "raw_source_character_key": buff_record.get("raw_source_character_key"),
                "raw_source_character_name": buff_record.get("raw_source_character_name"),
                "raw_source": buff_record.get("raw_source"),
                "target_character_key": buff_record["target_character_key"] or buff_record["target_enemy_key"],
                "target_character_name": buff_record["target_character_name"] or buff_record["target_enemy_name"],
                "target_player_key": buff_record["target_character_key"],
                "target_enemy_key": buff_record["target_enemy_key"],
                "owner_raw": buff_record.get("owner_raw"),
                "target_actor_id": buff_record.get("target_actor_id"),
                "source_actor_id": buff_record.get("source_actor_id"),
                "source_weapon_template": buff_record.get("source_weapon_template"),
                "event_key": buff_record["event_key"],
                "event_name": " / ".join(labels) if labels else buff_record["event_key"],
                "bb_values": copy.deepcopy(buff_record.get('bb_values') or {}),
                "zone_effects": zone_effects,
                "dynamic_effects": dynamic_effects,
                "requires_enable_observation": (
                    ((_load_current_buff_roots().get(buff_record['event_key']) or {}).get('stackingSettings') or {})
                    .get('stackingType') in {'HighPriority', 'HighPriorityWithMaxStack'}
                ),
                "modifier_count": len(buff_record.get("attr_mods") or []),
                "enable_observations": copy.deepcopy(buff_record.get('enable_observations') or []),
                "rdps_conflicting_enable_state": bool(buff_record.get('rdps_conflicting_enable_state')),
                "skill_family_key": None,
                "stack_limit": (
                    _packet_mapping_stack_limit(buff_record)
                    or _rdps_registry_stack_limit(buff_record)
                    or _buff_stack_limit(buff_record["event_key"], buff_record.get('bb_values'))
                ),
            }
        )
    buff_windows = _dedupe_mirrored_buff_windows(_merge_buff_windows(buff_windows))
    buff_windows.extend(_build_special_combo_windows(hits, buff_starts, battle_end_ms=last_hit_ms))
    buff_windows = _dedupe_mirrored_buff_windows(_merge_buff_windows(buff_windows))
    buff_windows = _apply_buff_stack_limits(buff_windows)

    # Static audit classifies each exact weapon skill field above. Observing
    # one known trigger never waives other unknown fields from that weapon.
    # Trigger capture coverage and allocation remain separate per-Buff checks.

    _infer_missing_hit_damage_schools(hits, buff_windows)
    buff_events = _build_buff_events(
        buff_starts,
        buff_windows,
        battle_start_ms=first_hit_ms,
        battle_end_ms=last_hit_ms,
        root_lookup_cache=buff_root_action_indices,
    )
    rdps_damage = _compute_rdps_damage_shares(hits, buff_windows, condition_buff_records=buff_starts)
    critical_ids = {str(record.get("event_key") or "") for record in buff_starts}
    for hit in hits:
        critical_ids.update(hit.get("_active_attacker_buff_ids") or [])
        critical_ids.update(hit.get("_active_target_buff_ids") or [])
    critical_roots = _load_current_buff_roots()
    critical_numeric_map = _load_num_id_str_buff_map()
    critical_profiles = {
        key: critical_buff_profile(critical_roots.get(critical_numeric_map.get(key, key)))
        for key in critical_ids
    }
    critical_changes: dict[tuple[str, str, str], set[int]] = defaultdict(set)
    for record in buff_starts:
        resource = str(record.get("event_key") or "")
        if not critical_profiles.get(resource, {}).get("attributes"):
            continue
        identity = (str(record.get("uid") or ""), str(record.get("target_actor_id") or ""), resource)
        for key in ("dynamic_bb_events", "enable_observations", "repeated_creation_observations"):
            for observation in record.get(key) or []:
                if (isinstance(observation, dict) and type(observation.get("line_no")) is int
                        and observation.get("status") != "duplicate"):
                    critical_changes[identity].add(observation["line_no"])
    for window in buff_windows:
        identities = {str(window.get("uid") or ""), *[str(uid) for uid in window.get("uid_aliases") or []]}
        changes = set()
        for uid in identities:
            changes.update(critical_changes.get((uid, str(window.get("target_actor_id") or ""), str(window.get("event_key") or "")), set()))
        if changes:
            window["critical_state_change_lines"] = sorted(changes)
    for hit in hits:
        hit["critical_context"] = build_critical_context(hit, hit.get("critical_observation"), critical_profiles, buff_windows)
    rdps_preflight = _build_rdps_preflight(
        buff_starts,
        buff_windows,
        hits=hits,
        unresolved_hits=unresolved_hits,
        unresolved_buff_actions=unresolved_buff_actions,
        battle_start_ms=first_hit_ms,
        battle_end_ms=last_hit_ms,
        active_character_keys=set(battle_loadout_by_char),
        root_lookup_cache=buff_root_action_indices,
    )
    rdps_damage_basis = _build_rdps_damage_basis(hits, rdps_damage, int(total_damage))
    rdps_damage_basis["rdps_preflight_ok"] = bool(rdps_preflight.get("ok"))
    rdps_damage_basis["rdps_strict_ok"] = bool(rdps_preflight.get("ok")) and bool(rdps_damage_basis.get("rdps_conservation_ok"))
    rdps_damage_basis["preflight_blocker_count"] = int(rdps_preflight.get("blocker_count") or 0)
    rdps_damage_basis["unresolved_damage_actor_count"] = sum(
        first_hit_ms <= int(row.get("ts_ms") or 0) <= last_hit_ms for row in unresolved_hits
    )
    _assign_skill_timeline_group_keys(hits)

    participants = []
    for character_key in roster_order:
        participant = participant_totals.get(character_key)
        if participant is None or not participant["character_name"]:
            continue
        if battle_loadout_by_char and character_key not in battle_loadout_by_char and participant["total_damage"] <= 0:
            continue
        total_rd = rdps_damage.get(character_key, float(participant["total_damage"]))
        if participant["total_damage"] <= 0 and total_rd <= 0:
            continue
        dps = round(participant["total_damage"] / (duration_ms / 1000), 2)
        rdps = round(total_rd / (duration_ms / 1000), 2)
        hit_count = participant["hit_count"]
        participants.append(
            {
                "character_key": character_key,
                "character_name": participant["character_name"],
                "total_damage": participant["total_damage"],
                "total_rd": total_rd,
                "dps": dps,
                "rdps": rdps,
                "max_hit": participant["max_hit"] or None,
                "hit_count": hit_count or None,
                "crit_hits": participant["crit_hits"] or None,
                "crit_rate": round(participant["crit_hits"] / hit_count, 4) if hit_count else None,
            }
        )
    participants.sort(key=lambda item: (-item["dps"], -item["rdps"], item["character_name"]))
    roster = [
        {
            "slot": index,
            "character_key": participant["character_key"],
            "character_name": participant["character_name"],
        }
        for index, participant in enumerate(participants, start=1)
    ]

    cast_start_index = _build_cast_start_index(skill_casts)

    timeline_events = []
    for hit in hits:
        matching_cast = _matching_cast_for_hit(hit, cast_start_index)
        actual_start_ms = int(matching_cast["ts_ms"]) if matching_cast is not None else None
        actual_end_ms = (
            int(matching_cast["end_ts_ms"])
            if matching_cast is not None and matching_cast.get("end_ts_ms") is not None
            else None
        )
        timeline_events.append(
            {
                "ts_ms_from_start": battle_offset_ms(hit["ts_ms"]),
                "lane_type": "skill",
                "source_character_key": hit["character_key"],
                "source_character_name": hit["character_name"],
                "target_character_key": hit["target_enemy_key"],
                "target_character_name": hit["target_enemy_name"],
                "event_type": "damage",
                "event_key": hit.get("skill_family_key") or hit["skill_key"],
                "event_group_key": hit.get("timeline_group_key"),
                "event_name": hit["skill_name"],
                "value": hit["hit_value"],
                "damage_element": hit.get("damage_element"),
                "damage_school": hit.get("damage_school"),
                "value_source": hit.get("damage_value_source"),
                "packet_hit_value": hit.get("packet_hit_value"),
                "packet_raw_value": hit.get("packet_raw_value"),
                "packet_final_value": hit.get("packet_final_value"),
                "overkill_damage": hit.get("overkill_damage"),
                "rdps_basis_value": hit.get("rdps_basis_value"),
                "rdps_basis_source": hit.get("rdps_basis_source"),
                "rdps_contributions": [
                    {
                        "character_key": character_key,
                        "character_name": _resolve_character_name(character_key) or character_key,
                        "value": round(value, 4),
                    }
                    for character_key, value in sorted((hit.get("rdps_contributions") or {}).items())
                ],
                "packet_modifier_uids": hit.get("packet_modifier_uids") or {"attacker": [], "defender": []},
                "packet_attr_details": hit.get("packet_attr_details") or {"attacker": [], "defender": []},
                "poise_damage": hit.get("poise_damage"),
                "hit_context": {"critical": hit["critical_context"]},
                "duration_ms": None,
                "actual_start_ms_from_start": (
                    battle_offset_ms_unclamped(actual_start_ms) if actual_start_ms is not None else None
                ),
                "actual_end_ms_from_start": (
                    battle_offset_ms_unclamped(actual_end_ms) if actual_end_ms is not None else None
                ),
                "actual_duration_ms": (
                    max(actual_end_ms - actual_start_ms, 1)
                    if actual_start_ms is not None and actual_end_ms is not None
                    else None
                ),
                "important": True,
            }
        )

    # 零伤害施放补录（例如辅助干员的终结技）：这些施放没有任何伤害命中，
    # 时间轴上会完全缺失。仅当施放技能名可解析为真实模板（非运行时兜底 id）、
    # 属于战技/连携/终结技形态、且该角色同技能在本场没有伤害命中时补一个 cast 事件；
    # 解析不出真名的老日志自动不生效，不会产生歧义数据。
    damaging_skill_keys = {
        (hit["character_key"], str(key))
        for hit in hits
        for key in (hit.get("skill_family_key"), hit.get("skill_key"))
        if key
    }
    participant_keys = {participant["character_key"] for participant in participants}
    last_cast_emit_ms: dict[tuple[str, str], int] = {}
    for cast in sorted(skill_casts, key=lambda item: int(item["ts_ms"])):
        cast_skill = str(cast.get("skill") or "")
        cast_character_key = cast.get("source_character_key")
        if not cast_character_key or cast_character_key not in participant_keys or not cast_skill:
            continue
        if _is_generic_runtime_skill_id(cast_skill) or re.fullmatch(r".*_skill_\d+", cast_skill):
            # 运行时兜底 id（chr_xxx_skill_100901）：尝试 num 表反解析成真实技能，失败则放弃。
            canonical_skill = _canonical_num_table_skill_id(cast_skill, cast_character_key)
            if not canonical_skill:
                continue
            cast_skill = canonical_skill
        # 归一到技能族：施放会连带范围/位移/派生等子实体（xxx_ultimate_skill_abilityrange），
        # 统一收敛为族名后再判定，避免子实体刷屏、也保证与伤害命中的 family key 对得上。
        family_match = re.match(r"^(.*?_(?:ultimate_skill|normal_skill|combo_skill))", cast_skill)
        if not family_match:
            continue
        cast_skill = family_match.group(1)
        if (cast_character_key, cast_skill) in damaging_skill_keys:
            continue
        cast_ts_ms = int(cast["ts_ms"])
        if cast_ts_ms < first_hit_ms or cast_ts_ms > last_hit_ms:
            continue
        emit_key = (str(cast_character_key), cast_skill)
        last_emit_ms = last_cast_emit_ms.get(emit_key)
        if last_emit_ms is not None and cast_ts_ms - last_emit_ms < 3000:
            continue
        last_cast_emit_ms[emit_key] = cast_ts_ms
        timeline_events.append(
            {
                "ts_ms_from_start": battle_offset_ms(cast_ts_ms),
                "lane_type": "skill",
                "source_character_key": cast_character_key,
                "source_character_name": _resolve_character_name(cast_character_key) or cast_character_key,
                "target_character_key": None,
                "target_character_name": None,
                "event_type": "cast",
                "event_key": cast_skill,
                "event_group_key": None,
                "event_name": _resolve_skill_name(cast_skill) or cast_skill,
                "value": None,
                "damage_element": None,
                "damage_school": None,
                "value_source": None,
                "packet_hit_value": None,
                "packet_raw_value": None,
                "packet_final_value": None,
                "overkill_damage": None,
                "rdps_basis_value": None,
                "rdps_basis_source": None,
                "rdps_contributions": [],
                "packet_modifier_uids": {"attacker": [], "defender": []},
                "packet_attr_details": {"attacker": [], "defender": []},
                "poise_damage": None,
                "duration_ms": None,
                "important": True,
            }
        )

    # 完整施法序列（v33，排轴导出 API 专用通道，不进 timeline_events 不影响展示）：
    # 参与者的每一次 SKILL_CAST_START 原样保留分段（attack1~5/重击/战技/终结…），
    # 运行时 id 经 num 表解析、失败自弃；子实体派生施放（projhit/abilityrange 等）
    # 不是玩家操作，过滤掉。窗口放宽到战斗零点前 10s（开怪第一个施放先于首刀落地）。
    casts_export: list[dict[str, Any]] = []
    _CAST_EXPORT_NOISE_RE = re.compile(
        r"(_projhit|_abilityrange|_abilityentity|_absorb|_indicator|_blocked|_listener|_aura|_marker)"
    )
    # 技力归属（v34+）：一次技力增加(ATB_UPDATE AddValue)归属到它「之前最近」的同角色施放
    # = 真正触发回技力的那一次(主控重击=连招末段)。若用前向窗口从每个施放往后看，连招
    # 前几段(A1~A4)会把末段(A5)的回技力抢标。先收集(施放起点ms, casts_export下标)，全部
    # 建完后按「gain 归最近前驱 cast」一次性回填 recovers_energy。
    _ATB_RECOVER_WINDOW_MS = 1500
    cast_starts_by_char: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for cast in sorted(skill_casts, key=lambda item: int(item["ts_ms"])):
        cast_skill = str(cast.get("skill") or "")
        cast_character_key = cast.get("source_character_key")
        if not cast_character_key or cast_character_key not in participant_keys or not cast_skill:
            continue
        if _is_generic_runtime_skill_id(cast_skill) or re.fullmatch(r".*_skill_\d+", cast_skill):
            canonical_skill = _canonical_num_table_skill_id(cast_skill, cast_character_key)
            if not canonical_skill:
                continue
            cast_skill = canonical_skill
        if _CAST_EXPORT_NOISE_RE.search(cast_skill):
            continue
        cast_ts_ms = int(cast["ts_ms"])
        if cast_ts_ms < first_hit_ms - 10_000 or cast_ts_ms > last_hit_ms:
            continue
        cast_end_ts_ms = cast.get("end_ts_ms")
        casts_export.append(
            {
                "ts_ms_from_start": battle_offset_ms_unclamped(cast_ts_ms),
                "end_ms_from_start": (
                    battle_offset_ms_unclamped(int(cast_end_ts_ms)) if cast_end_ts_ms is not None else None
                ),
                "character_key": cast_character_key,
                "skill_key": cast_skill,
                "skill_name": _resolve_skill_name(cast_skill) or cast_skill,
                "skill_source": str(cast.get("skill_source") or "") or None,
                "dynamic_bb_events": list(cast.get("dynamic_bb_events") or []),
                # 技力增加归属回填（下方），默认 False；老客户端无 ATB_UPDATE 恒 False。
                "recovers_energy": False,
            }
        )
        cast_starts_by_char[cast_character_key].append((cast_ts_ms, len(casts_export) - 1))

    # 回填：每次技力增加 → 归属到 (gain-窗口, gain] 内最近的前驱同角色施放。
    for atb_char, gains in atb_gain_ms_by_char.items():
        starts = cast_starts_by_char.get(atb_char)
        if not starts:
            continue
        start_ms_list = [s for s, _ in starts]
        for gain_ms in gains:
            pos = bisect.bisect_right(start_ms_list, gain_ms) - 1
            if pos < 0:
                continue
            cast_start_ms, cast_idx = starts[pos]
            if 0 <= gain_ms - cast_start_ms <= _ATB_RECOVER_WINDOW_MS:
                casts_export[cast_idx]["recovers_energy"] = True

    for event in buff_windows:
        actual_start_ms = int(event["start_ts_ms"])
        actual_end_ms = int(event["end_ts_ms"])
        visible_start_ms = max(int(event["start_ts_ms"]), first_hit_ms)
        visible_end_ms = min(int(event["end_ts_ms"]), last_hit_ms)
        if visible_end_ms < visible_start_ms:
            continue
        visible_duration_ms = max(
            battle_offset_ms_unclamped(visible_end_ms) - battle_offset_ms_unclamped(visible_start_ms),
            1,
        )
        timeline_events.append(
            {
                "ts_ms_from_start": battle_offset_ms(visible_start_ms),
                "lane_type": "buff",
                "source_character_key": event["source_character_key"],
                "source_character_name": event["source_character_name"],
                "target_character_key": event["target_character_key"],
                "target_character_name": event["target_character_name"],
                "target_player_key": event.get("target_player_key"),
                "target_enemy_key": event.get("target_enemy_key"),
                "event_type": "buff",
                "event_key": event["event_key"],
                "event_name": event["event_name"],
                "value": None,
                "rdps_contributions": [],
                "duration_ms": visible_duration_ms,
                "actual_start_ms_from_start": battle_offset_ms_unclamped(actual_start_ms),
                "actual_end_ms_from_start": battle_offset_ms_unclamped(actual_end_ms),
                "actual_duration_ms": max(
                    battle_offset_ms_unclamped(actual_end_ms) - battle_offset_ms_unclamped(actual_start_ms),
                    1,
                ),
                "effects": _window_effects_at_ts(event, visible_start_ms),
                "dynamic_effects": list(event.get("dynamic_effects") or []),
                "important": True,
            }
        )
    timeline_events.sort(key=lambda item: (item["ts_ms_from_start"], item["lane_type"], item["event_name"]))

    role_skill_stats = _build_role_skill_stats_from_timeline_groups(hits, roster_order)
    loadout = sorted(
        battle_loadout_by_char.values(),
        key=lambda item: (int(item.get("slot") or 0), str(item.get("character_key") or "")),
    )
    debug_hits: list[dict[str, Any]] = []
    if include_rdps_debug:
        buff_records_by_uid = _build_buff_record_index(buff_starts)
        for hit in hits:
            allocation = _allocate_rdps_for_hit(
                hit,
                buff_windows,
                include_debug=True,
                buff_records_by_uid=buff_records_by_uid,
                condition_buff_records=buff_starts,
            )
            debug_hit = dict(hit)
            debug_hit["rdps_contributions"] = allocation.get("contributions_list") or []
            for key in (
                "zones",
                "ignored_effects",
                "product_external_multiplier",
                "attacker_share",
                "external_pool",
                "external_sources",
                "zone_summary",
                "buff_source_summary",
                "packet_modifier_uids",
                "packet_modifier_details",
            ):
                debug_hit[key] = allocation.get(key)
            debug_hits.append(debug_hit)

    fingerprint_payload = {
        "rules_version": RULES_VERSION,
        "boss_key": boss_key,
        "first_hit_ms": first_hit_ms,
        "last_hit_ms": last_hit_ms,
        "hits": [
            {
                "ts_ms": hit["ts_ms"],
                "character_key": hit["character_key"],
                "skill_key": hit["skill_key"],
                "hit_value": hit["hit_value"],
                "crit_flag": hit["crit_flag"],
                "target_enemy_key": hit["target_enemy_key"],
            }
            for hit in hits
        ],
    }
    contract_tags = [_contract_tag_payload(tag_id) for tag_id in contract_tag_ids]
    if contract_tags:
        tag_scores = [_coerce_int(str(tag.get("score") or ""), default=0) for tag in contract_tags]
        computed_contract_tag_score = sum(tag_scores)
        if computed_contract_tag_score > 0:
            contract_tag_score = computed_contract_tag_score
        elif contract_tag_score is None:
            contract_tag_score = computed_contract_tag_score

    return {
        "battle": {
            "dungeon_key": dungeon_key,
            "dungeon_name": dungeon_name,
            "boss_key": boss_key,
            "boss_name": boss_name,
            "battle_start_at": _build_iso_datetime(reference_date, first_hit_ms),
            "battle_end_at": _build_iso_datetime(reference_date, last_hit_ms),
            "duration_ms": duration_ms,
            "time_source": "game_timer" if has_game_timer_window else time_source,
            "timeline_zero_source": timeline_zero_source,
            "timer_start_seen": timer_start_seen,
            "timer_end_seen": timer_end_seen,
            "official_timer_start_seen": official_timer_start_ms is not None,
            "official_timer_end_seen": official_timer_end_ms is not None,
            "official_timer_observed_elapsed_ms": official_timer_observed_elapsed_ms,
            "packet_timer_elapsed_ms": packet_timer_elapsed_ms,
            "timer_integrity_ok": timer_integrity_ok,
            "timer_integrity_reasons": timer_integrity_reasons,
            "timer_evidence": timer_evidence,
            "timer_start_inferred": timer_start_inferred,
            "timer_window_valid": timer_window_valid,
            "clear_flag": clear_flag,
            "challenge_pass_confirmed": challenge_pass_confirmed,
            "official_pass_confirmed": official_pass_confirmed,
            "completion_source": completion_source,
            "boss_identity_source": (
                "trace_inference"
                if trace_identity_inference_seen
                else "inferred_dungeon_context"
                if identity_retargeted
                else "damage_target"
            ),
            "dungeon_context_id": dungeon_context_id,
            "dungeon_identity_source": (
                "dungeon_context"
                if dungeon_context is not None
                else "unmapped_dungeon_context"
                if dungeon_context_id
                else "missing_dungeon_context"
            ),
            "total_damage": total_damage,
            "total_dps": total_dps,
            "roster": roster,
            "battle_fingerprint": build_canonical_sha256(fingerprint_payload),
            "parser_version": PARSER_VERSION,
            "rules_version": RULES_VERSION,
            "source_file_name": file_name,
            "loadout_stale": loadout_stale,
            "rdps_damage_basis": rdps_damage_basis,
            "contract_tag_score": contract_tag_score,
            "contract_tags": contract_tags,
        },
        "participants": participants,
        "loadout": loadout,
        "buff_events": buff_events,
        "timeline_events": timeline_events,
        "dynamic_bb_events": dynamic_bb_events,
        "buff_attribute_refresh_events": buff_attribute_refresh_events,
        "role_skill_stats": role_skill_stats,
        "debug_hits": debug_hits,
        **({'debug_context': {'start_ts_ms': first_hit_ms, 'end_ts_ms': last_hit_ms,
                              'buff_windows': buff_windows}} if include_rdps_debug else {}),
        "rdps_damage_basis": rdps_damage_basis,
        "rdps_preflight": rdps_preflight,
        "unresolved_hits": unresolved_hits,
        "unresolved_buff_actions": unresolved_buff_actions,
        "casts": casts_export,
        "char_skills": {
            character_key: [
                {"skill_key": skill_key, "level": level}
                for skill_key, level in sorted(levels.items())
            ]
            for character_key, levels in char_skill_levels.items()
        },
    }
# Compatibility stubs: heuristic identity inference was removed. Callers must
# treat a missing result as unresolved evidence.
def _same_frame_trigger_buff_for_runtime_skill(*args: Any, **kwargs: Any) -> None:
    return None


def _runtime_skill_trigger_chain_mapping(*args: Any, **kwargs: Any) -> None:
    return None


def _trigger_damage_identity_from_buff(*args: Any, **kwargs: Any) -> tuple[str, str]:
    return ("unknown_skill", "unknown_skill")


def _infer_context_enemy_hint(*args: Any, **kwargs: Any) -> None:
    return None


def _buff_semantically_reaches_buff(*args: Any, **kwargs: Any) -> bool:
    return False


def _skill_semantically_reaches_buff(*args: Any, **kwargs: Any) -> bool:
    return False
