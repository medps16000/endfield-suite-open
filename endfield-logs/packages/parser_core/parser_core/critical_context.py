"""Observed critical parameters for a fixed recorded hit, independent of rDPS."""
from __future__ import annotations

import math
from typing import Any

CRITICAL_ATTRIBUTES = {"CriticalRate": 9, "CriticalDamageIncrease": 10}


def update_critical_observation(states: dict[str, dict[str, Any]], entity_id: str,
                                snapshot: dict[str, Any], line_no: int, *, partial: bool = False) -> None:
    if not partial:
        if snapshot.get("kind") != "character":
            states.pop(entity_id, None)
            return
        states[entity_id] = {"template": snapshot.get("template"),
                            "values": {attr: value for attr, value in snapshot["attrs"].items() if attr in (9, 10)},
                            "lines": {attr: line_no for attr in (9, 10)},
                            "times": {attr: snapshot.get("ts_ms") for attr in (9, 10)}}
        return
    prior = states.get(entity_id)
    if prior is None:
        return
    if prior.get("template") != snapshot.get("template"):
        states.pop(entity_id, None)
        return
    for attr in (9, 10):
        if attr in snapshot.get("invalid_attr_types", []):
            prior["values"].pop(attr, None)
        elif attr in snapshot["attrs"]:
            prior["values"][attr] = snapshot["attrs"][attr]
            prior["lines"][attr] = line_no
            prior.setdefault("times", {})[attr] = snapshot.get("ts_ms")


def critical_buff_profile(root: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(root, dict):
        return {"missing": True, "attributes": [], "processors": False}
    attributes: set[int] = set()
    processors = False
    pending: list[Any] = [root]
    while pending:
        node = pending.pop()
        if isinstance(node, dict):
            kind = node.get("$type")
            if kind == "Beyond.Gameplay.Core.DamageAction.DamageUnit":
                continue
            if kind in {"Beyond.Gameplay.Core.AttackCriticalRateProcessor", "Beyond.Gameplay.Core.AttackerCriticalDamageProcessor"}:
                processors = True
            if isinstance(node.get("attributeType"), str) and node.get("attributeType") in CRITICAL_ATTRIBUTES:
                attributes.add(CRITICAL_ATTRIBUTES[node["attributeType"]])
                if kind == "Beyond.Gameplay.Core.InstantModifyAttribute":
                    processors = True
            if kind == "Beyond.Gameplay.Core.InstantModifyAttribute":
                modifier = node.get("modifier") or {}
                if isinstance(modifier.get("attributeType"), str) and modifier.get("attributeType") in CRITICAL_ATTRIBUTES:
                    processors = True
            pending.extend(node.values())
        elif isinstance(node, list):
            pending.extend(node)
    return {"missing": False, "attributes": sorted(attributes), "processors": processors}


def build_critical_context(
    hit: dict[str, Any],
    observation: dict[str, Any] | None,
    profiles: dict[str, dict[str, Any]],
    buff_records: list[dict[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {"version": 1, "status": "unavailable"}
    reasons: list[str] = []
    if (hit.get("damage_element") == "lifedrain" and hit.get("critical_flag_observed")
            and hit.get("crit_flag") == 0 and isinstance(hit.get("damage_calculation"), dict)):
        # The verified native LifeDrain branch bypasses the critical draw.
        return {"version": 1, "status": "verified", "isCritical": False, "critRate": 0.0,
                "critDamageBonus": 0.0, "uncappedDamage": hit.get("raw_hit_value", hit["hit_value"]),
                "damageCap": hit["hit_value"] if hit.get("overkill_damage", 0) > 0 else None,
                "source": "exact_lifedrain_no_critical_branch"}
    if not hit.get("critical_flag_observed"):
        reasons.append("missing_critical_flag")
    if (not observation or observation.get("template") != hit.get("character_key")
            or any(attr not in observation.get("values", {}) for attr in (9, 10))):
        reasons.append("missing_critical_attributes")
    calculation = hit.get("damage_calculation")
    if not isinstance(calculation, dict) or not isinstance(calculation.get("damageProcessors"), list):
        reasons.append("missing_exact_damage_unit")
    if int(hit.get("part_inst_id") or 0):
        reasons.append("part_damage_roll_relation_unavailable")
    active = set(hit.get("_active_attacker_buff_ids") or []) | set(hit.get("_active_target_buff_ids") or [])
    for buff_id in active:
        profile = profiles.get(buff_id, {"missing": True})
        if profile.get("missing"):
            reasons.append("unresolved_active_buff")
        elif profile.get("processors"):
            reasons.append("conditional_critical_modifier")
    # A persistent critical buff change invalidates an older observation of
    # that attribute. An observed attribute update re-establishes the value.
    for record in buff_records:
        profile = profiles.get(str(record.get("event_key") or ""), {})
        attrs = profile.get("attributes") or []
        if not attrs or profile.get("processors") or not observation:
            continue
        target = str(record.get("target_actor_id") or record.get("owner_raw") or "")
        target_character = record.get("target_character_key")
        if target != str(hit.get("source_actor_id") or "") and target_character != hit.get("character_key"):
            continue
        changes = [] if record.get("end_is_observation_boundary") else [record.get("end_line_no")]
        changes.extend(record.get("critical_state_change_lines") or [])
        if not record.get("snapshot_update"):
            changes.append(record.get("line_no", record.get("start_line_no")))
        for attr in attrs:
            observed_line = observation.get("lines", {}).get(attr, 0)
            if any(type(line) is int and observed_line < line <= hit["line_no"] for line in changes):
                reasons.append("critical_attribute_changed_after_snapshot")
            observed_time = observation.get("times", {}).get(attr)
            end_time = record.get("end_ts_ms")
            if (record.get("end_line_no") is None and not record.get("end_is_observation_boundary")
                    and type(observed_time) is int and type(end_time) is int
                    and observed_time <= end_time <= hit.get("ts_ms", -1)):
                reasons.append("critical_attribute_changed_after_snapshot")
    if reasons:
        return {**result, "reasons": sorted(set(reasons))}
    values = {attr: float(observation["values"][attr]) for attr in (9, 10)}
    critical_processor_count = 0
    for processor in calculation["damageProcessors"]:
        if not isinstance(processor, dict):
            reasons.append("invalid_damage_processor")
            continue
        kind = processor.get("$type")
        if kind == "Beyond.Gameplay.Core.DamageScaleProcessor":
            continue
        if kind == "Beyond.Gameplay.Core.AttackCriticalRateProcessor":
            attr, operation, param = 9, "Addition", processor.get("addition")
        elif kind == "Beyond.Gameplay.Core.InstantModifyAttribute":
            modifier = processor.get("modifier") or {}
            if processor.get("modifyTargetSide") == "Defender":
                continue
            if processor.get("modifyTargetSide") != "Attacker":
                reasons.append("unresolved_modifier_target")
                continue
            if modifier.get("modifyAttributeType") != "Specific":
                reasons.append("unresolved_modifier_attribute")
                continue
            attr = CRITICAL_ATTRIBUTES.get(modifier.get("attributeType"))
            if attr is None:
                continue
            operation, param = modifier.get("formulaItem"), modifier.get("param")
        else:
            reasons.append("unsupported_damage_processor")
            continue
        critical_processor_count += 1
        value = None
        if isinstance(param, dict) and param.get("useBlackboardKey") is False:
            value = param.get("value")
        elif isinstance(param, dict) and param.get("useBlackboardKey") is True:
            reasons.append("unresolved_critical_blackboard_binding")
        if type(value) not in (int, float) or not math.isfinite(value):
            reasons.append("missing_critical_modifier_value")
        elif operation == "Addition":
            values[attr] += value
        elif operation == "FinalMultiplier":
            values[attr] *= value
        else:
            reasons.append("unsupported_critical_modifier_operation")
    if critical_processor_count > 1:
        reasons.append("combined_critical_processors_unresolved")
    probability = min(1.0, max(0.0, values[9]))
    bonus = values[10]
    critical = hit.get("crit_flag") == 1
    if hit.get("damage_element") == "lifedrain":
        probability = 0.0
        bonus = 0.0
    if not all(math.isfinite(value) for value in (values[9], bonus)) or bonus < 0:
        reasons.append("invalid_critical_attributes")
    if (probability == 0 and critical) or (probability == 1 and not critical):
        reasons.append("critical_outcome_conflicts_with_parameters")
    cap = None
    uncapped = hit.get("raw_hit_value", hit["hit_value"])
    if probability in (0, 1) or bonus == 0:
        # There is no alternative critical outcome to clip in this case.
        uncapped = hit["hit_value"]
    elif hit.get("overkill_damage", 0) > 0:
        cap = hit["hit_value"]
    elif hit.get("enemy_hp_after_observed"):
        cap = hit.get("enemy_hp_before")
        if type(cap) not in (int, float) or not math.isfinite(cap) or cap < hit["hit_value"]:
            reasons.append("inconsistent_pre_hit_hp")
    else:
        reasons.append("missing_pre_hit_hp")
    if reasons:
        return {**result, "reasons": sorted(set(reasons))}
    return {"version": 1, "status": "verified", "isCritical": critical,
            "critRate": probability, "critDamageBonus": bonus,
            "uncappedDamage": uncapped,
            "damageCap": cap,
            "source": "bound_entity_attributes_and_exact_damage_unit"}
