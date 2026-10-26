from __future__ import annotations

from typing import Any

from parser_core.battle_log_parser import (
    _attr_type_applies_to_skill,
    _BUFF_EFFECT_SKILL_FILTER,
    _BUFF_SKILL_FILTER,
    _effect_applies_to_damage_element,
    _damage_school_from_element,
    _infer_skill_action_damage_element,
    _infer_skill_damage_element,
    _infer_skill_damage_school,
)

def normalize_zone(zone: str | None) -> str:
    return str(zone or "").lower()


def damage_element_from_dpd(dpd: dict[str, Any] | None) -> str | None:
    return None


def infer_damage_element(
    skill_key: str | None,
    character_key: str | None = None,
    *,
    action_id: int | str | None = None,
    damage_unit_index: int | str | None = None,
    dpd: dict[str, Any] | None = None,
) -> str | None:
    return _infer_skill_action_damage_element(skill_key, action_id, damage_unit_index)


def infer_damage_school(
    skill_key: str | None,
    character_key: str | None = None,
    *,
    raw_skill_key: str | None = None,
    action_id: int | str | None = None,
    damage_unit_index: int | str | None = None,
) -> str | None:
    damage_element = infer_damage_element(
        skill_key, character_key, action_id=action_id, damage_unit_index=damage_unit_index,
    )
    return _damage_school_from_element(damage_element)


def effect_applies_to_damage_element(
    effect_element: str | None,
    damage_element: str | None,
    damage_school: str | None = None,
) -> bool:
    return _effect_applies_to_damage_element(effect_element or "all", damage_element, damage_school)


def attr_type_applies_to_skill(
    attr_type: int | str | None, skill_key: str | None, *, damage_decorate_mask: int | None = None,
) -> bool | None:
    try:
        attr_type_int = int(attr_type) if attr_type is not None else None
    except (TypeError, ValueError):
        return None
    return _attr_type_applies_to_skill(attr_type_int, skill_key, damage_decorate_mask=damage_decorate_mask)


def buff_applies_to_skill(buff_id: str | None, skill_key: str | None) -> bool | None:
    # This legacy name-only API has no observed hit condition state.
    return None


def buff_effect_applies_to_skill(buff_id: str | None, zone: str | None, skill_key: str | None) -> bool | None:
    return None
