from __future__ import annotations

from pathlib import Path

import pytest


DATA_SENTINEL = Path(__file__).resolve().parents[3] / "data" / "akedata" / "character" / "manifest.json"

HEALTH_TESTS_REQUIRING_GAME_DATA = {
    "test_high_difficulty_plain_stage_slug_does_not_enter_hard_ranking",
    "test_crisis_fragment_aliases_filter_by_official_stage_slug_only",
    "test_war_echo_highest_stages_have_separate_rankings_and_lower_difficulty_is_excluded",
    "test_contract_tags_are_exposed_on_public_records",
    "test_local_game_catalog_endpoints",
    "test_local_game_semantic_endpoints",
    "test_local_game_semantic_hint_endpoints",
    "test_uploader_battle_upload_endpoints",
    "test_high_difficulty_upload_uses_explicit_hard_stage_slug",
    "test_reviewed_gambling_upload_keeps_detail_without_opening_rankings",
    "test_new_si_bossrush_upload_uses_the_official_series_ranking",
    "test_new_war_echo_rankings_have_official_names",
    "test_new_war_echo_uploads_keep_hard_and_cruel_separate",
}

MODULES_REQUIRING_GAME_DATA = {
    "test_public_data_assets.py",
    "test_character_boss_statistics.py",
    "test_character_profile.py",
    "test_character_statistics_query.py",
    "test_weapon_refine_normalization.py",
}

RESOURCE_FREE_ROTATION_TESTS = {
    "test_private_unexpected_failures_are_not_cached_or_exposed",
    "test_catalog_receipt_verifies_bytes_with_release_newline_convention",
    "test_cors_preflight_allows_expected_rotation_account_header",
    "test_rotations_are_disabled_by_default_and_can_be_explicitly_restored",
    "test_disabled_rotation_namespace_rejects_before_validation_or_storage",
    "test_rotation_disable_does_not_change_other_api_routes",
}


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    if DATA_SENTINEL.is_file():
        return
    reason = "generated game-data resources are not distributed in the public repository"
    marker = pytest.mark.skip(reason=reason)
    for item in items:
        name = getattr(item, "originalname", None) or item.name
        if (item.path.name in MODULES_REQUIRING_GAME_DATA
                or name in HEALTH_TESTS_REQUIRING_GAME_DATA
                or item.path.name == "test_rotations.py" and name not in RESOURCE_FREE_ROTATION_TESTS):
            item.add_marker(marker)

