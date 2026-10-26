import json
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize('key,zone,element,expected', [
    ('buff_chr_0035_liino_atkup', 'atk', 'fire', 0),
    ('buff_outside', 'atk', 'fire', 1),
    ('buff_chr_0035_liino_atkup', 'vuln_taken', 'fire', 1),
    ('buff_chr_0011_seraph_talent_1_crystup', 'vuln_taken', 'fire', 1),
])
def test_audit_matches_each_exported_credit_to_declared_scope(tmp_path, key, zone, element, expected):
    root = Path(__file__).resolve().parents[1]
    parsed = tmp_path / 'parsed.json'
    parsed.write_text(json.dumps({'debug_hits': [{'seq': 1, 'damage_element': element, 'damage_school': 'spell',
        'zones': [{'contributors': [{'scope': 'external', 'event_key': key, 'zone': zone,
             'external_rdps_allowed': True, 'rdps_whitelist_keys': [key], 'rdps_credit': 2}]}]}]}), encoding='utf-8')
    completed = subprocess.run([sys.executable, str(root / 'infra/scripts/audit_rdps_whitelist.py'),
                               '--parsed', str(parsed), '--output-dir', str(tmp_path / 'audit')],
                              cwd=root, capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert completed.returncode == 0, completed.stderr
    result = json.loads((tmp_path / 'audit/whitelist_audit.json').read_text(encoding='utf-8'))
    assert len(result['attribution']['unmatchedRows']) == expected
    assert result['exactEntryCount'] == len(result['inventory'])
    assert result['fullGameplayCoverageVerified'] is False
