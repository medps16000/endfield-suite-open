from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(params=[ROOT, ROOT.parent / "endfield-logs"], ids=["pcap", "logs"])
def inventory(request, monkeypatch):
    scripts = request.param / "infra" / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location("ignored_inventory_under_test", scripts / "build_rdps_ignored_effect_samples.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_unknown_effects_and_missing_fields_remain_unverified(inventory, monkeypatch, tmp_path):
    source = tmp_path / "session_test.ndjson"
    monkeypatch.setattr(inventory, "_iter_sources", lambda *args, **kwargs: [source])
    monkeypatch.setattr(inventory, "_convert_ndjson_to_trace", lambda *args: {
        "trace_text": "fixture", "trace_hp_line_count": 1,
    })
    ignored = {"event_key": "unmapped_123", "reason": "damage_school_filtered", "rate": 0.2}
    monkeypatch.setattr(inventory, "parse_raw_battle_log_text", lambda *args, **kwargs: {
        "debug_hits": [{
            "seq": 42, "hit_value": 1000, "ignored_effects": [ignored],
            "zones": [{"contributors": [{"event_key": "sibling", "rate": 0.2}]}],
        }],
        "rdps_preflight": {"ok": True, "blocker_count": 0, "blockers": []},
    })
    payload = inventory.build_samples(log_root=tmp_path, output_dir=tmp_path / "out")
    group = payload["groups"][0]
    assert group["event_key"] == "unmapped_123"
    assert group["verification_status"] == "unverified"
    assert group["samples"][0]["hit"]["damage_school"] is None
    assert payload["summary"]["verification_status"] == "unverified"
    assert payload["sources"][0]["parser_reported_preflight"]["ok"] is True
    rendered = json.dumps(payload) + inventory.render_html(payload) + inventory.render_markdown(
        payload, json_path=tmp_path / "out.json", html_path=tmp_path / "out.html",
    )
    for field in ("possible_missing_credit", "forced_credit_estimate", "estimated_external_credit", '"confidence"', "expected_filter"):
        assert field not in rendered


def test_counts_include_every_group_and_mark_sample_truncation(inventory, monkeypatch, tmp_path):
    monkeypatch.setattr(inventory, "_iter_sources", lambda *args, **kwargs: [tmp_path / "session_test.ndjson"])
    monkeypatch.setattr(inventory, "_convert_ndjson_to_trace", lambda *args: {
        "trace_text": "fixture", "trace_hp_line_count": 1,
    })
    effects = [{"event_key": f"id_{i}", "reason": "unknown"} for i in range(25)]
    monkeypatch.setattr(inventory, "parse_raw_battle_log_text", lambda *args, **kwargs: {
        "debug_hits": [{"seq": 1, "ignored_effects": effects + effects}],
        "rdps_preflight": {"blocker_count": 105, "blockers": [{}] * 100},
    })
    payload = inventory.build_samples(log_root=tmp_path, output_dir=tmp_path / "out", max_samples_per_key=1)
    assert payload["summary"]["ignored_effect_count"] == 50
    assert len(payload["groups"]) == 25
    assert all(group["samples_truncated"] and group["count"] == 2 for group in payload["groups"])
    assert payload["sources"][0]["preflight_examples_truncated"] is True


def test_empty_input_and_conversion_errors_do_not_become_success(inventory, monkeypatch, tmp_path):
    monkeypatch.setattr(inventory, "_iter_sources", lambda *args, **kwargs: [])
    payload = inventory.build_samples(log_root=tmp_path, output_dir=tmp_path / "empty")
    assert payload["summary"]["verification_status"] == "unverified"
    assert payload["summary"]["total_hits"] == 0
    monkeypatch.setattr(inventory, "_iter_sources", lambda *args, **kwargs: [tmp_path / "session_bad.ndjson"])
    def fail_conversion(*args):
        raise ValueError("invalid capture")
    monkeypatch.setattr(inventory, "_convert_ndjson_to_trace", fail_conversion)
    payload = inventory.build_samples(log_root=tmp_path, output_dir=tmp_path / "bad")
    assert payload["summary"]["parse_error_count"] == 1
    assert payload["sources"][0]["error"] == "ValueError: invalid capture"
