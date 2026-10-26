from copy import deepcopy
import hashlib
import json
from pathlib import Path
import struct

import pytest

from infra.runtime_probe import validate_native_formula as validator

FIXTURE = Path(__file__).with_name("fixtures") / "native_formula_20260913.jsonl"
K = struct.unpack("<f", struct.pack("<f", 0.01))[0]


def capture():
    aa, da = [0.0] * 101, [0.0] * 101
    aa[10], aa[25], aa[49], aa[62] = 0.5, 1.0, 1.0, 1.0
    da[3] = 100.0
    for index in (4, 5, 6, 7, 48, 60):
        da[index] = 1.0
    snap = {"dpd": "000000000013D750", "damageType": 3, "damageDecorateMask": 0,
            "calcResult": 200.0, "atkScale": 1.0, "isBlocked": False,
            "attackerAttributes": aa, "defenderAttributes": da,
            "attackerZones": [1.0] * 7, "defenderZones": [1.0] * 7}
    return [
        {"kind": "probe_ready", "module": "GameAssembly.dll"},
        {"kind": "calculate_enter", "seq": 10, "snapshot": deepcopy(snap)},
        {"kind": "get_final_damage_scale", "calculationSeq": 10, "result": 1.0, "snapshot": deepcopy(snap)},
        {"kind": "get_final_attack_value", "calculationSeq": 10, "result": 200.0, "snapshot": deepcopy(snap)},
        {"kind": "calculate_leave", "seq": 10, "result": 200.0 / (1 + K * 100),
         "isCritical": False, "isBlocked": False, "snapshot": deepcopy(snap)},
    ]


def write_capture(tmp_path, rows):
    path = tmp_path / "capture.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def test_frozen_real_sample_is_reproduced_without_native_returns_as_inputs():
    manifest = json.loads(FIXTURE.with_suffix(".manifest.json").read_text(encoding="utf-8"))
    raw = FIXTURE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == manifest["fixtureSha256"]
    assert len(raw.splitlines()) == manifest["rows"]
    report = validator.validate(FIXTURE)
    assert report["boundCalculationCount"] == 131
    assert report["formulaMatchCount"] == 131
    assert report["formulaMismatchCount"] == report["blockedCount"] == 0
    assert report["maxAbsoluteError"] == 0.0
    assert report["conditionalFormulaOk"] is True
    assert report["predictionUsesNativeScalarReturns"] is False
    assert report["strictFormulaOk"] is report["strictRdpsOk"] is False
    assert "capture_completion_missing_or_duplicated" in report["evidenceGaps"]


@pytest.mark.parametrize("text", ["", "\n", '{"kind":"unrelated"}\n'])
def test_empty_or_missing_marker_cannot_pass(tmp_path, text):
    path = tmp_path / "empty.jsonl"
    path.write_text(text, encoding="utf-8")
    report = validator.validate(path)
    assert report["conditionalFormulaOk"] is False
    assert report["blockedCount"] > 0


@pytest.mark.parametrize("suffix", ['{"kind":', 'null\n', '[]\n', '{"kind":"x","kind":"y"}\n'])
def test_bad_record_is_not_silently_dropped(tmp_path, suffix):
    path = write_capture(tmp_path, capture())
    with path.open("a", encoding="utf-8") as stream:
        stream.write(suffix)
    report = validator.validate(path)
    assert report["conditionalFormulaOk"] is False
    assert any(b["reason"] == "invalid_json_record" for b in report["blocked"])


def test_correlated_return_corruption_does_not_validate_itself(tmp_path):
    rows = capture()
    rows[2]["result"] *= 2
    rows[3]["result"] *= 2
    rows[4]["result"] *= 2
    report = validator.validate(write_capture(tmp_path, rows))
    assert report["formulaMismatchCount"] == 1
    assert report["rows"][0]["predictedAttack"] == 200
    assert report["conditionalFormulaOk"] is False


@pytest.mark.parametrize("index", [1, 2, 3, 4])
def test_all_four_nodes_require_the_same_dpd(tmp_path, index):
    rows = capture()
    rows[index]["snapshot"]["dpd"] = "14D750"
    report = validator.validate(write_capture(tmp_path, rows))
    assert report["formulaMatchCount"] == 0
    assert any(b["reason"] == "formula_node_dpd_mismatch" for b in report["blocked"])


@pytest.mark.parametrize("pointer", [None, "", "None", "0", "0000000000000000", -1, "xyz"])
def test_null_or_malformed_pointer_blocks(tmp_path, pointer):
    rows = capture()
    rows[3]["snapshot"]["dpd"] = pointer
    report = validator.validate(write_capture(tmp_path, rows))
    assert report["conditionalFormulaOk"] is False


@pytest.mark.parametrize("value", [None, True, "10", 0, -1])
def test_bad_call_id_blocks_instead_of_disappearing(tmp_path, value):
    rows = capture()
    rows[2]["calculationSeq"] = value
    report = validator.validate(write_capture(tmp_path, rows))
    assert report["formulaMatchCount"] == 0
    assert any(b["reason"] == "invalid_calculation_sequence" for b in report["blocked"])


@pytest.mark.parametrize("operation", ["missing", "duplicate", "order"])
def test_call_cardinality_and_order(tmp_path, operation):
    rows = capture()
    if operation == "missing":
        rows.pop(2)
    elif operation == "duplicate":
        rows.insert(3, deepcopy(rows[2]))
    else:
        rows[2], rows[3] = rows[3], rows[2]
    report = validator.validate(write_capture(tmp_path, rows))
    assert report["formulaMatchCount"] == 0
    assert report["conditionalFormulaOk"] is False


@pytest.mark.parametrize("mask", [None, False, -1, 2**64, "0"])
def test_missing_or_wrong_typed_mask_is_not_zero(tmp_path, mask):
    rows = capture()
    for row in rows[1:]:
        row["snapshot"]["damageDecorateMask"] = mask
    report = validator.validate(write_capture(tmp_path, rows))
    assert report["formulaMatchCount"] == 0


@pytest.mark.parametrize("flag", [None, "false", 0, 1])
def test_crit_flag_must_be_present_and_boolean(tmp_path, flag):
    rows = capture()
    rows[-1]["isCritical"] = flag
    report = validator.validate(write_capture(tmp_path, rows))
    assert report["formulaMatchCount"] == 0


@pytest.mark.parametrize("field,value", [
    ("attackerAttributes", [1.0] * 100), ("defenderAttributes", None),
    ("attackerZones", [1.0] * 6), ("defenderZones", [1.0] * 8),
    ("calcResult", float("nan")), ("atkScale", float("inf")),
    ("calcResult", True), ("calcResult", 10**400),
    ("attackerAttributes", [1.0] * 100 + [float("nan")]),
])
def test_missing_nonfinite_or_incomplete_input_blocks(tmp_path, field, value):
    rows = capture()
    rows[-1]["snapshot"][field] = value
    report = validator.validate(write_capture(tmp_path, rows))
    assert report["formulaMatchCount"] == 0
    assert report["conditionalFormulaOk"] is False


def test_nonunit_weakness_crit_shelter_and_anomaly_factors(tmp_path):
    rows = capture()
    for row in rows[1:]:
        s = row["snapshot"]
        s["attackerAttributes"][62] = 0.8
        s["attackerAttributes"][49] = 1.25
        s["attackerAttributes"][25] = 1.2
        s["defenderAttributes"][63] = 0.1
        s["defenderAttributes"][97] = 20.0
        s["defenderAttributes"][6] = 0.9
        s["damageDecorateMask"] = 0x1000000 | 0x4000
    rows[-1]["isCritical"] = True
    rows[-1]["result"] = 200 * 0.8 * 1.5 / (1 + K * 100) * 0.9 * 0.72 * 1.25 * 1.2
    report = validator.validate(write_capture(tmp_path, rows))
    assert report["formulaMatchCount"] == 1


@pytest.mark.parametrize("damage_type", [1, 5, 8])
def test_unreviewed_damage_branches_stay_blocked(tmp_path, damage_type):
    rows = capture()
    for row in rows[1:]:
        row["snapshot"]["damageType"] = damage_type
    report = validator.validate(write_capture(tmp_path, rows))
    assert any(b["reason"] == "unsupported_damage_type" for b in report["blocked"])


def test_zone_list_position_is_not_server_index(tmp_path):
    rows = capture()
    for row in rows[1:]:
        row["snapshot"]["attackerZones"][0] = 1.2
        row["snapshot"]["defenderZones"][6] = 1.3
    rows[2]["result"] = 1.56
    rows[3]["result"] = 312.0
    rows[-1]["result"] = 312.0 / (1 + K * 100)
    report = validator.validate(write_capture(tmp_path, rows))
    assert report["formulaMatchCount"] == 1
    assert report["rows"][0]["predictedScale"] == pytest.approx(1.56)


def test_combined_additive_zone_uses_explicit_flag():
    # Exercise the reviewed operation independently; this synthetic config
    # is not presented to the validator as an official current game asset.
    product, values = validator._aggregate_zones(
        [{"isMultiplyZone": 0, "mergeAttackerAndDefender": 1}], [1.2], [1.1])
    assert product == pytest.approx(1.3)
    assert values == pytest.approx([1.3])
    product, _ = validator._aggregate_zones(
        [{"isMultiplyZone": 1, "mergeAttackerAndDefender": 1}], [1.2], [1.1])
    assert product == pytest.approx(1.32)


@pytest.mark.parametrize("change", ["version", "consumption", "duplicate", "flag"])
def test_unproven_config_blocks(tmp_path, change):
    config = json.loads(validator.DEFAULT_ZONE_CONFIG.read_text(encoding="utf-8"))
    if change == "version":
        config["decoder"]["metadataSha256"] = "0" * 64
    elif change == "consumption":
        config["proof"]["consumedObjectBytes"] -= 1
    elif change == "duplicate":
        config["zones"][1]["name"] = config["zones"][0]["name"]
    else:
        config["zones"][0]["isMultiplyZone"] = "1"
    cfg = tmp_path / "zones.json"
    cfg.write_text(json.dumps(config), encoding="utf-8")
    report = validator.validate(write_capture(tmp_path, capture()), cfg)
    assert report["conditionalFormulaOk"] is False


def test_native_patch_cannot_pass_by_matching_return_values(tmp_path):
    rows = capture() + [{"kind": "ifix_is_patched", "methodId": 48131, "result": True}]
    report = validator.validate(write_capture(tmp_path, rows))
    assert report["conditionalFormulaOk"] is False
    assert any(b["reason"] == "native_formula_overridden_by_ifix" for b in report["blocked"])


@pytest.mark.parametrize("method_id", [None, True, "48131", [48131], {"id": 48131}])
def test_invalid_ifix_identity_blocks_without_crashing(tmp_path, method_id):
    rows = capture() + [{"kind": "ifix_is_patched", "methodId": method_id, "result": False}]
    report = validator.validate(write_capture(tmp_path, rows))
    assert report["conditionalFormulaOk"] is False
    assert any(b["reason"] == "invalid_ifix_method_id" for b in report["blocked"])


@pytest.mark.parametrize("field,value", [
    ("calculationCount", 0), ("calculationCount", 2), ("calculationCount", True),
    ("droppedRecords", 1), ("droppedRecords", None),
    ("writeErrors", 1), ("openCalls", 1), ("sessionId", "different"),
])
def test_incorrect_capture_completion_counters_block(tmp_path, field, value):
    rows = capture()
    rows[0]["sessionId"] = "fixture"
    end = {"kind": "probe_end", "sessionId": "fixture", "calculationCount": 1,
           "droppedRecords": 0, "writeErrors": 0, "openCalls": 0}
    end[field] = value
    rows.append(end)
    report = validator.validate(write_capture(tmp_path, rows))
    assert report["conditionalFormulaOk"] is False
    assert any(b["reason"] == "capture_completion_invalid" for b in report["blocked"])


def test_records_after_completion_cannot_extend_a_closed_capture(tmp_path):
    rows = capture()
    rows[0]["sessionId"] = "fixture"
    rows.append({"kind": "probe_end", "sessionId": "fixture", "calculationCount": 1,
                 "droppedRecords": 0, "writeErrors": 0, "openCalls": 0})
    rows.append({"kind": "generate_mask", "seq": 100, "result": 0})
    report = validator.validate(write_capture(tmp_path, rows))
    assert report["conditionalFormulaOk"] is False
    assert any(b["reason"] == "records_after_probe_end" for b in report["blocked"])


@pytest.mark.parametrize("field", ["gameAssemblySha256", "metadataSha256", "probeSha256"])
def test_numerical_agreement_cannot_hide_a_different_binary(tmp_path, field):
    rows = capture()
    rows[0][field] = "0" * 64
    report = validator.validate(write_capture(tmp_path, rows))
    assert report["formulaMatchCount"] == 1
    assert report["conditionalFormulaOk"] is report["strictPostZoneFormulaOk"] is False


def test_new_failed_start_does_not_reuse_previous_success(tmp_path):
    rows = capture() + [{"kind": "hook_failed", "name": "example"}]
    report = validator.validate(write_capture(tmp_path, rows))
    assert report["conditionalFormulaOk"] is False
    rows = capture() + [{"kind": "probe_begin", "sessionId": "new"}, {"kind": "hook_failed"}]
    report = validator.validate(write_capture(tmp_path, rows))
    assert report["formulaMatchCount"] == 0
    rows = capture() + [{"kind": "hook_ready", "name": "new_start"}]
    report = validator.validate(write_capture(tmp_path, rows))
    assert report["conditionalFormulaOk"] is False


def test_setup_hook_records_before_ready_are_valid_capture_setup(tmp_path):
    rows = capture()
    rows[0]["sessionId"] = "fixture"
    rows[:0] = [{"kind": "probe_begin", "sessionId": "fixture"},
                {"kind": "hook_ready", "name": "calculate"}]
    report = validator.validate(write_capture(tmp_path, rows))
    assert report["conditionalFormulaOk"] is True
    assert report["strictFormulaOk"] is False


def test_observation_changes_between_final_nodes_are_not_ignored(tmp_path):
    rows = capture()
    rows[-1]["snapshot"]["defenderAttributes"][3] = 120
    report = validator.validate(write_capture(tmp_path, rows))
    assert any(b["reason"] == "formula_input_changed_between_nodes" for b in report["blocked"])


def test_numeric_match_is_not_rdps_or_full_formula_acceptance(tmp_path, monkeypatch):
    rows = capture()
    rows[0].update(sessionId="fixture", gameAssemblySha256=validator.EXPECTED_GAME_ASSEMBLY_SHA256,
                   metadataSha256=validator.EXPECTED_METADATA_SHA256,
                   probeSha256=validator.REVIEWED_PROBE_SHA256, captureSchemaVersion=2)
    rows.insert(0, {"kind": "probe_begin", "sessionId": "fixture"})
    rows.extend({"kind": "ifix_is_patched", "methodId": method, "result": False}
                for method in validator.FORMULA_IFIX_IDS)
    rows.append({"kind": "probe_end", "sessionId": "fixture", "droppedRecords": 0,
                 "writeErrors": 0, "openCalls": 0, "calculationCount": 1})
    path = write_capture(tmp_path, rows)
    report = validator.validate(path)
    assert report["conditionalFormulaOk"] is report["strictPostZoneFormulaOk"] is True
    assert report["strictFormulaOk"] is report["strictRdpsOk"] is False
    monkeypatch.setattr("sys.argv", ["validate_native_formula.py", str(path)])
    assert validator.main() == 2
