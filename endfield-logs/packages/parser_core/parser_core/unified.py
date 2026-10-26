from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

from parser_core.battle_log_parser import parse_raw_battle_log_text
from parser_core.hit_debug import parse_hit_debug_log_text


def rdps_totals_from_hit_debug(report: dict[str, Any]) -> dict[str, float]:
    totals: dict[str, float] = defaultdict(float)
    for hit in report.get("hits") or []:
        for contribution in hit.get("rdps_contributions") or []:
            character_key = str(contribution.get("character_key") or "")
            if not character_key:
                continue
            totals[character_key] += float(contribution.get("value") or 0.0)
    return dict(totals)


def rdps_max_from_hit_debug(report: dict[str, Any]) -> dict[str, float]:
    max_by_character: dict[str, float] = defaultdict(float)
    for hit in report.get("hits") or []:
        for contribution in hit.get("rdps_contributions") or []:
            character_key = str(contribution.get("character_key") or "")
            if not character_key:
                continue
            max_by_character[character_key] = max(
                max_by_character[character_key],
                float(contribution.get("value") or 0.0),
            )
    return dict(max_by_character)


def rdps_totals_from_raw_report(report: dict[str, Any]) -> dict[str, float]:
    duration_sec = max(float((report.get("battle") or {}).get("duration_ms") or 0) / 1000.0, 0.0)
    totals: dict[str, float] = {}
    for participant in report.get("participants") or []:
        character_key = str(participant.get("character_key") or "")
        if not character_key:
            continue
        if participant.get("total_rd") is not None:
            totals[character_key] = float(participant.get("total_rd") or 0.0)
        else:
            totals[character_key] = float(participant.get("rdps") or 0.0) * duration_sec
    return totals


def rdps_max_from_raw_report(report: dict[str, Any]) -> dict[str, float]:
    max_by_character: dict[str, float] = defaultdict(float)
    for event in report.get("timeline_events") or []:
        for contribution in event.get("rdps_contributions") or []:
            character_key = str(contribution.get("character_key") or "")
            if not character_key:
                continue
            max_by_character[character_key] = max(
                max_by_character[character_key],
                float(contribution.get("value") or 0.0),
            )
    return dict(max_by_character)


def parse_upload_battle_log_text(
    text: str,
    *,
    file_name: str | None = None,
    first_hit_hint: str | None = None,
    last_hit_hint: str | None = None,
    reference_date: date | None = None,
    loadout_override_by_char: dict[str, dict] | None = None,
    critical_attributes_override_by_actor: dict[str, dict] | None = None,
) -> dict[str, Any]:
    return parse_raw_battle_log_text(
        text,
        file_name=file_name,
        first_hit_hint=first_hit_hint,
        last_hit_hint=last_hit_hint,
        reference_date=reference_date,
        loadout_override_by_char=loadout_override_by_char,
        critical_attributes_override_by_actor=critical_attributes_override_by_actor,
    )


def parse_hit_viewer_log_text(
    text: str,
    *,
    file_name: str | None = None,
) -> dict[str, Any]:
    return parse_hit_debug_log_text(text, file_name=file_name)


def parse_overlay_battle_snapshot_text(
    text: str,
    *,
    file_name: str | None = None,
    last_hit_hint: str | None = None,
    reference_date: date | None = None,
) -> dict[str, Any]:
    raw_report = parse_upload_battle_log_text(
        text,
        file_name=file_name,
        last_hit_hint=last_hit_hint,
        reference_date=reference_date,
    )
    rdps_totals = rdps_totals_from_raw_report(raw_report)
    rdps_max = rdps_max_from_raw_report(raw_report)
    duration_sec = max(float(raw_report["battle"]["duration_ms"]) / 1000.0, 0.0)

    participants = []
    seen_character_keys: set[str] = set()
    for participant in raw_report.get("participants") or []:
        character_key = str(participant.get("character_key") or "")
        if not character_key:
            continue
        seen_character_keys.add(character_key)
        total_rd = float(rdps_totals.get(character_key, 0.0))
        participants.append(
            {
                **participant,
                "rdps": total_rd / duration_sec if duration_sec > 0.5 else 0.0,
                "total_rd": total_rd,
                "max_rd": float(rdps_max.get(character_key, 0.0)),
            }
        )
    for row in sorted(raw_report.get("loadout") or [], key=lambda item: (int(item.get("slot") or 0), str(item.get("character_key") or ""))):
        character_key = str(row.get("character_key") or "")
        if not character_key or character_key in seen_character_keys:
            continue
        seen_character_keys.add(character_key)
        participants.append(
            {
                "character_key": character_key,
                "character_name": str(row.get("character_name") or character_key),
                "total_damage": 0.0,
                "dps": 0.0,
                "rdps": 0.0,
                "total_rd": 0.0,
                "max_rd": 0.0,
                "max_hit": 0,
                "hit_count": 0,
                "crit_hits": 0,
                "crit_rate": None,
            }
        )

    return {
        "battle": raw_report["battle"],
        "participants": participants,
        "loadout": raw_report.get("loadout") or [],
        "buff_events": raw_report.get("buff_events") or [],
        "timeline_events": raw_report.get("timeline_events") or [],
        "role_skill_stats": raw_report.get("role_skill_stats") or [],
        "rdps_damage_basis": raw_report.get("rdps_damage_basis") or (raw_report.get("battle") or {}).get("rdps_damage_basis"),
        "debug": None,
    }
