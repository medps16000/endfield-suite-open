from __future__ import annotations

from typing import Any

from parser_core.battle_log_parser import (
    _allocate_rdps_for_hit,
    _ATTR_TYPE_BUFF_LABELS,
    _ATTR_TYPE_TO_EFFECT,
    _resolve_character_name,
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


def _round4(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 4)


def build_hit_context(
    hit: dict[str, Any], buff_windows: list[dict[str, Any]], *,
    condition_buff_records: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    allocation = _allocate_rdps_for_hit(
        hit, buff_windows, include_debug=True, condition_buff_records=condition_buff_records,
    )
    baseline = hit.get("baseline") if isinstance(hit.get("baseline"), dict) else {}
    return {
        **allocation,
        "baseline": {"attrs": _baseline_attr_rows(baseline)},
        "rdps_contributions": allocation["contributions_list"],
    }


def explain_hit_context(hit: dict[str, Any], buff_windows: list[dict[str, Any]]) -> dict[str, Any]:
    raw_baseline = hit.get("baseline")
    context = build_hit_context(hit, buff_windows)
    for key, value in context.items():
        if key == "baseline":
            continue
        hit[key] = value
    if raw_baseline is not None:
        hit["baseline"] = raw_baseline
    hit["hit_context"] = context
    return hit


def rdps_contribution_map(hit_context: dict[str, Any]) -> dict[str, float]:
    if isinstance(hit_context.get('contributions_map'), dict):
        return dict(hit_context['contributions_map'])
    return {
        str(item.get("character_key") or ""): float(item.get("value") or 0.0)
        for item in hit_context.get("rdps_contributions") or []
        if item.get("character_key") and float(item.get("value") or 0.0) > 0.0
    }


def _baseline_attr_row(attr_type: int, value: float) -> dict[str, Any]:
    zone = None
    element = None
    mapping = _ATTR_TYPE_TO_EFFECT.get(attr_type)
    if mapping is not None:
        zone, element = mapping
    return {
        "attr_type": attr_type,
        "label": _ATTR_TYPE_BUFF_LABELS.get(attr_type) or ZONE_LABELS.get(str(zone or ""), ""),
        "zone": zone,
        "zone_label": ZONE_LABELS.get(str(zone or ""), str(zone or "")) if zone else None,
        "element": element,
        "value": _round4(value),
    }


def _baseline_attr_rows(baseline: dict[Any, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw_attr_type, raw_value in sorted(baseline.items(), key=lambda item: str(item[0])):
        try:
            attr_type = int(raw_attr_type)
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        rows.append(_baseline_attr_row(attr_type, value))
    rows.sort(key=lambda item: int(item["attr_type"]))
    return rows


def _state_window_row(window: dict[str, Any], *, battle_start_ms: int) -> dict[str, Any]:
    start_ts_ms = int(window.get("start_ts_ms") or 0)
    end_ts_ms = int(window.get("end_ts_ms") or start_ts_ms)
    return {
        "event_key": window.get("event_key"),
        "event_name": window.get("event_name"),
        "source_character_key": window.get("source_character_key"),
        "source_character_name": window.get("source_character_name"),
        "target_character_key": window.get("target_character_key"),
        "target_character_name": window.get("target_character_name"),
        "target_player_key": window.get("target_player_key"),
        "target_enemy_key": window.get("target_enemy_key"),
        "target_enemy_name": window.get("target_enemy_name"),
        "owner_raw": window.get("owner_raw"),
        "start_ts_ms_from_start": max(start_ts_ms - battle_start_ms, 0),
        "end_ts_ms_from_start": max(end_ts_ms - battle_start_ms, 0),
        "duration_ms": max(end_ts_ms - start_ts_ms, 1),
        "effects": [
            {
                "zone": effect.get("zone"),
                "zone_label": ZONE_LABELS.get(str(effect.get("zone") or ""), str(effect.get("zone") or "")),
                "element": effect.get("element"),
                "rate": _round4(float(effect.get("rate") or 0.0)),
                "attr_type": effect.get("attr_type"),
                "bb_key": effect.get("bb_key"),
            }
            for effect in window.get("zone_effects") or []
        ],
        "dynamic_effects": [
            {
                "zone": effect.get("zone"),
                "zone_label": ZONE_LABELS.get(str(effect.get("zone") or ""), str(effect.get("zone") or "")),
                "element": effect.get("element"),
                "base_rate": _round4(float(effect.get("base_rate") or 0.0)),
                "tick_rate": _round4(float(effect.get("tick_rate") or 0.0)),
                "max_rate": _round4(float(effect.get("max_rate") or 0.0)),
            }
            for effect in window.get("dynamic_effects") or []
        ],
    }


def build_character_states(
    *,
    roster: list[dict[str, Any]],
    hits: list[dict[str, Any]],
    buff_windows: list[dict[str, Any]],
    battle_start_ms: int,
) -> list[dict[str, Any]]:
    first_baseline_by_character: dict[str, tuple[int, dict[int, float]]] = {}
    for hit in hits:
        character_key = str(hit.get("character_key") or "")
        baseline = hit.get("baseline")
        if not character_key or character_key in first_baseline_by_character or not isinstance(baseline, dict):
            continue
        first_baseline_by_character[character_key] = (int(hit.get("ts_ms") or 0), baseline)

    states: list[dict[str, Any]] = []
    for roster_entry in roster:
        character_key = str(roster_entry.get("character_key") or "")
        if not character_key:
            continue
        baseline_ts_ms, baseline = first_baseline_by_character.get(character_key, (None, {}))
        buffs_received = [
            _state_window_row(window, battle_start_ms=battle_start_ms)
            for window in buff_windows
            if window.get("target_player_key") == character_key
        ]
        buffs_given = [
            _state_window_row(window, battle_start_ms=battle_start_ms)
            for window in buff_windows
            if window.get("source_character_key") == character_key
            and window.get("target_player_key")
            and window.get("target_player_key") != character_key
        ]
        debuffs_applied = [
            _state_window_row(window, battle_start_ms=battle_start_ms)
            for window in buff_windows
            if window.get("source_character_key") == character_key and window.get("target_enemy_key")
        ]
        states.append(
            {
                "character_key": character_key,
                "character_name": roster_entry.get("character_name") or _resolve_character_name(character_key) or character_key,
                "initial_baseline": {
                    "ts_ms_from_start": (
                        max(int(baseline_ts_ms) - battle_start_ms, 0)
                        if baseline_ts_ms is not None
                        else None
                    ),
                    "attrs": _baseline_attr_rows(baseline),
                },
                "buffs_received": buffs_received,
                "buffs_given": buffs_given,
                "debuffs_applied": debuffs_applied,
            }
        )
    return states
