from __future__ import annotations

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent
DATA_SENTINEL = ROOT / "data" / "local_tables" / "NumIdStrTable.json"

# These integration modules perform lookups in externally supplied game tables,
# native evidence or release receipts. Keep their source public, but do not claim
# they ran in a source-only checkout without those inputs.
RESOURCE_TEST_FILES = {
    'test_buff_enable_state.py',
    'test_buff_origin.py',
    'test_character_catalog_sync.py',
    'test_dynamic_attribute_wire_pipeline.py',
    'test_dynamic_buff_identity_anchor.py',
    'test_effect_capture_lifecycle.py',
    'test_exact_packet_identity.py',
    'test_game_data_bridge.py',
    'test_keyword_update_wire.py',
    'test_overlay_effect_catalog.py',
    'test_overlay_effect_definitions.py',
    'test_pack_release.py',
    'test_rdps_20261015_effects.py',
    'test_rdps_dynamic_declarations.py',
    'test_rdps_whitelist_audit.py',
    'test_rdps_whitelist_parent_scope.py',
    'test_rotation_clock_capture.py',
    'test_rotation_clock_proof.py',
    'test_rotation_roundtrip.py',
    'test_service_loadout.py',
    'test_weapon_refine_regression.py',

    "test_loadout_static.py",
    "test_message_registry_activity.py",
    "test_overlay_status.py",
    "test_packet_resolver_bundle.py",
    "test_packet_resolver_wulfa.py",
    "test_rdps_audit.py",
    "test_rdps_effect_catalog.py",
    "test_trace_bridge_timer.py",
}


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    if DATA_SENTINEL.is_file():
        return
    reason = "generated parser resources are not distributed in the public repository"
    marker = pytest.mark.skip(reason=reason)
    parser_tests = ROOT / "packages" / "parser_core" / "tests"
    for item in items:
        path = Path(str(item.path)).resolve()
        if parser_tests in path.parents or path.name in RESOURCE_TEST_FILES:
            item.add_marker(marker)



PRIVATE_SUPPORT_TESTS = {
    "test_compile_semantics_from_akedata.py", "test_deterministic_damage_formula.py",
    "test_native_evidence_addressing.py", "test_native_formula_validation.py",
    "test_observation_extensions.py", "test_source_binding.py", "test_rdps_ignored_effect_inventory.py",
}


def pytest_ignore_collect(collection_path, config):
    path = Path(str(collection_path))
    if path.name in PRIVATE_SUPPORT_TESTS and not (ROOT / "infra").is_dir():
        return True
    if path.name == "test_protocol_selected_coverage.py" and not (ROOT / "data/message_ids_cs.json").is_file():
        return True
    return False
