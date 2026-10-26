from __future__ import annotations

import pytest

from infra.scripts.build_deterministic_damage_audit import (
    _compute_defense_scalar,
    _is_true_damage,
    _official_static_enemy_stats_by_key,
)


def test_defense_scalar_matches_official_nonnegative_formula() -> None:
    assert _compute_defense_scalar(100.0, 0.01) == 0.5


def test_negative_defense_branch_is_explicit_native_formula() -> None:
    assert _compute_defense_scalar(-100.0, 0.01) > 1.0


def test_true_damage_bypasses_defense_zone() -> None:
    assert _is_true_damage({"damage_type": "Real"}) is True
    assert _is_true_damage({"damage_type": "Fire"}) is False


def test_lifedrain_bypasses_main_damage_formula() -> None:
    from infra.scripts.build_deterministic_damage_audit import _can_compute_defense_scalar

    assert _can_compute_defense_scalar({"damage_type": "Ether"}) is True
    assert _can_compute_defense_scalar({"damage_type": "LifeDrain"}) is False


@pytest.mark.parametrize('element,point_attr', [('physical',94),('fire',98),('pulse',97),('cryst',96),('natural',95),('ether',99)])
def test_native_resistance_needs_both_points_and_taken_scalar(element, point_attr):
    from infra.scripts.build_deterministic_damage_audit import _compute_resistance_scalar

    args = dict(debug_fields={}, defender_stats={'attrs': {str(point_attr): {'value': 20}}},
                element=element, packet_damage_taken_scalar=0.9, defender_damage_taken_scalar=None)
    assert _compute_resistance_scalar(**args)[0] == pytest.approx(0.72)
    args['defender_stats']['attrs'][str(point_attr)]['value'] = -20
    assert _compute_resistance_scalar(**args)[0] == pytest.approx(1.08)
    args['defender_stats']['attrs'][str(point_attr)]['value'] = 120
    assert _compute_resistance_scalar(**args)[0] == 0.0
    args['defender_stats'] = None
    assert _compute_resistance_scalar(**args)[0] is None


def test_composite_spell_label_does_not_select_a_native_resistance_type():
    from infra.scripts.build_deterministic_damage_audit import _compute_resistance_scalar

    value, _ = _compute_resistance_scalar(debug_fields={},
        defender_stats={'attrs': {str(k): {'value': 0.8} for k in (81,82,83,84)}},
        element='spell', packet_damage_taken_scalar=None, defender_damage_taken_scalar=0.8)
    assert value is None


def test_audit_does_not_report_formula_match_from_reconstructed_inputs(tmp_path) -> None:
    from infra.scripts.build_deterministic_damage_audit import build_audit

    trace = tmp_path / "minimal.dat"
    trace.write_text(
        '[10:00:01.000] HP_V2 #1 hit=100 cum=100 raw=100.00 '
        'packetFinalValue=100.0 eHP=900 skill="chr_0027_tangtang_attack1" '
        'actionId=0 damageUnitIndex=0 src=chr_0027_tangtang '
        'atk=chr_0027_tangtang tgt=eny_0051_rodin\n',
        encoding="utf-8",
    )
    audit = build_audit(trace)
    assert audit["summary"]["formula_final_hit_count"] == 0
    assert audit["summary"]["formula_match_hit_count"] == 0
    assert {row["formula_status"] for row in audit["hits"]} == {"blocked_unproven_inputs"}


def test_packet_only_validation_does_not_publish_candidate_fits(tmp_path) -> None:
    from infra.scripts.build_packet_only_model_validation import build_validation

    trace = tmp_path / "minimal.dat"
    trace.write_text(
        '[10:00:01.000] HP_V2 #1 hit=100 eHP=900 skill="chr_0027_tangtang_attack1" '
        'actionId=0 damageUnitIndex=0 src=chr_0027_tangtang '
        'atk=chr_0027_tangtang tgt=eny_0051_rodin\n',
        encoding="utf-8",
    )
    report = build_validation(trace)
    assert report["summary"]["formula_audit"] == {
        "status": "blocked_unproven_inputs",
        "reason": "No complete native calculation field and processor-order proof is available.",
        "candidate_fitting_used": False,
    }
    assert report["summary"]["stable_hidden_scalar_skills"] == []


def test_enemy_static_stats_do_not_infer_level_from_hit_hp() -> None:
    templates = {
        "eny_test": {
            "levelIndependentAttributes": {
                "attrs": [
                    {"attrType": 80, "attrValue": 0.8},
                    {"attrType": 94, "attrValue": 20},
                ]
            },
            "levelDependentAttributes": [
                {"attrs": [{"attrType": 0, "attrValue": 1}, {"attrType": 1, "attrValue": 1000}, {"attrType": 3, "attrValue": 100}]},
                {"attrs": [{"attrType": 0, "attrValue": 90}, {"attrType": 1, "attrValue": 999999}, {"attrType": 3, "attrValue": 100}]},
            ],
        }
    }
    rows = _official_static_enemy_stats_by_key(
        [{"target_enemy_key": "eny_test", "enemy_hp_after": 1, "raw_hit_value": 999999}],
        templates,
    )
    assert rows["eny_test"]["source"] == "enemy_static_table_official_def_100"
    assert rows["eny_test"]["level"] is None
    assert rows["eny_test"]["hp"] is None
    assert "observed_initial_hp" not in rows["eny_test"]
