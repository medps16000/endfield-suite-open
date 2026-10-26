from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "infra" / "scripts"))

action_report = pytest.importorskip("analyze_private_damage_action", reason="private native audit tools are not distributed in the public source tree")
action_fields = pytest.importorskip("analyze_private_damage_action_fields", reason="private native audit tools are not distributed in the public source tree")
unit_fields = pytest.importorskip("analyze_private_damage_unit_fields", reason="private native audit tools are not distributed in the public source tree")


@pytest.mark.parametrize("module", [action_report, action_fields, unit_fields])
@pytest.mark.parametrize("tag_source", ["missing", "inferred", "official_native"])
def test_legacy_damage_reports_refuse_before_resource_scanning(
    module, tag_source: str, tmp_path: Path, monkeypatch,
) -> None:
    report_path = tmp_path / "action-tags.json"
    if tag_source != "missing":
        payload = (
            {"combinedHighConfidenceInferredTags": [{"tag": 2951, "actionType": "DamageAction.DamageActionData"}]}
            if tag_source == "inferred"
            else {"schemaVersion": 2, "source": "official_native_memorypack_union", "actions": []}
        )
        report_path.write_text(json.dumps(payload), encoding="utf-8")

    def reject_scan(*args, **kwargs):
        pytest.fail("The unsupported report must not read or scan current resources")

    monkeypatch.setattr(module, "_analyze_kind", reject_scan)
    with pytest.raises(ValueError, match="current native schema and StringPool") as error:
        module.build_report(report_path)
    assert "build_official_skill_damage_catalog.py validate" in str(error.value)
    assert "typed_audit.jsonl.gz" in str(error.value)


@pytest.mark.parametrize("module", [action_report, action_fields, unit_fields])
def test_legacy_damage_report_cli_refuses_without_writing(
    module, tmp_path: Path, monkeypatch, capsys,
) -> None:
    json_output = tmp_path / "report.json"
    md_output = tmp_path / "report.md"
    monkeypatch.setattr(sys, "argv", [module.__name__, "--json-output", str(json_output), "--md-output", str(md_output)])
    with pytest.raises(SystemExit) as error:
        module.main()
    assert error.value.code == 2
    assert "build_official_skill_damage_catalog.py validate" in capsys.readouterr().err
    assert not json_output.exists()
    assert not md_output.exists()
