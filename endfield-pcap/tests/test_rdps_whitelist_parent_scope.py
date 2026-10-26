"""Inventory must not claim a two-element declaration from one child branch."""
from copy import deepcopy
import json
from pathlib import Path
import runpy
import sys

import pytest

from parser_core import battle_log_parser as parser


@pytest.mark.parametrize('change', ['none', 'disable_pulse', 'retarget_pulse_to_unapproved_natural'])
def test_antal_inventory_uses_explicit_alias_and_unions_only_proven_branch_scope(tmp_path, monkeypatch, change):
    key = 'buff_chr_0023_antal_ultimate_enhance'
    actual_parent = 'buff_chr_0023_antal_utimate_skill'
    actual_roots = parser._load_current_buff_roots()
    roots = dict(actual_roots)
    roots[actual_parent] = deepcopy(actual_roots[actual_parent])
    actions = roots[actual_parent]['buffEventAction'][0]['actions'][0]['actionData']
    assert actions[0]['serverActionIndex'] == 0 and actions[0]['subType'] == 'Pulse'
    if change == 'disable_pulse':
        actions[0]['isEnable'] = False
    elif change == 'retarget_pulse_to_unapproved_natural':
        actions[0]['subType'] = 'Natural'
    monkeypatch.setattr(parser, '_load_current_buff_roots', lambda: roots)
    root = Path(__file__).resolve().parents[1]
    script = root / 'infra/scripts/audit_rdps_whitelist.py'
    monkeypatch.setattr(sys, 'argv', [str(script), '--output-dir', str(tmp_path)])
    runpy.run_path(str(script), run_name='__main__')
    report = json.loads((tmp_path / 'whitelist_audit.json').read_text(encoding='utf-8'))
    entry = next(row for row in report['inventory'] if row['whitelistKey'] == key)
    probe = entry['structureProbe']
    assert report['fullGameplayCoverageVerified'] is False
    assert probe['synthetic'] is True
    assert {tuple(sorted(row.items())) for row in probe['parentKeywordRequiredScope']} == {
        tuple(sorted({'zone': 'amp', 'element': element}.items())) for element in ['pulse', 'fire']}
    paths = probe['keywordChildPaths']
    assert all(row['canonicalApprovalKey'] == key and row['actualParentTemplate'] == actual_parent for row in paths)
    assert all(row['childWhitelistKeys'] == [key] for row in paths)
    assert all(row['requiresObservedCreateOrigin'] is True and row['runtimeCoverageVerified'] is False for row in paths)
    assert all(row['coversFullEntryScope'] is False and len(row['declaredScopeCovered']) == 1 for row in paths)
    if change == 'none':
        assert probe['status'] == 'keyword_child_path'
        assert {row['actionId']: row['declaredScopeCovered'] for row in paths} == {
            0: [{'zone': 'amp', 'element': 'pulse'}], 1: [{'zone': 'amp', 'element': 'fire'}]}
        assert probe['parentKeywordCoveredScope'] == probe['parentKeywordRequiredScope']
        assert report['structureProbeCounts']['keyword_child_path'] == 8
    else:
        assert probe['status'] == 'needs_review'
        assert probe['parentKeywordCoveredScope'] == [{'zone': 'amp', 'element': 'fire'}]
        assert 'incomplete_parent_keyword_scope' in probe['reasons']
        assert probe['typedNonRdpsEvidence'] is None
        assert report['structureProbeCounts']['keyword_child_path'] == 7
    assert report['structureProbeCounts']['visual_main_path'] == 6
    new_parent = next(row for row in report['inventory'] if row['whitelistKey'] == 'buff_chr_0036_jsspsi_atk_buff_2')
    assert new_parent['structureProbe']['status'] == 'keyword_child_path'
    assert {row['actionId']: row['declaredScopeCovered'] for row in new_parent['structureProbe']['keywordChildPaths']} == {
        1: [{'zone': 'amp', 'element': 'fire'}], 2: [{'zone': 'amp', 'element': 'cryst'}]}
    hp = next(row for row in report['inventory'] if row['whitelistKey'] == 'buff_wpn_funnel_0014_hp')
    assert hp['structureProbe']['status'] == 'typed_non_rdps'
