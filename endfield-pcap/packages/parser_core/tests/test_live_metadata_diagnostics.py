"""Observed control fields must not be reported as additional rDPS effects."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from parser_core import battle_log_parser as parser


RECORDS = json.loads((Path(__file__).with_name('fixtures') / 'live_metadata_records_20260914.json')
                     .read_text(encoding='utf8'))['records']


@pytest.mark.parametrize('record', RECORDS, ids=lambda record: record['event_key'])
def test_observed_known_metadata_does_not_fail_approved_effect_validation(record):
    entry = parser._rdps_registry_verified_effect_entry(deepcopy(record))
    assert entry is not None
    assert parser._rdps_registry_validation_error(entry, deepcopy(record)) is None
    unknown = deepcopy(record)
    unknown['bb_values']['unreviewed_damage_rate_fixture'] = .75
    error = parser._rdps_registry_validation_error(entry, unknown)
    assert error['unknown_bb_keys'] == ['unreviewed_damage_rate_fixture']


def test_corrosion_carrier_retains_known_forwarded_fields_without_allocating_them():
    record = deepcopy(next(row for row in RECORDS if row['event_key'].endswith('corrupt_triggered')))
    entry = parser._rdps_registry_verified_effect_entry(record)
    assert entry['_rdps_lifecycle_only'] is True
    assert entry['effects'] == []
    assert {'def_decrease', 'additional_def_decrease', 'def_decrease_tick_final',
            'max_def_decrease_final'} <= set(entry['ignored_bb_keys'])
    assert parser._collect_zone_effects(record) == []


def test_reviewed_metadata_names_exist_in_current_typed_roots():
    for record in RECORDS:
        root = parser._load_current_buff_roots()[record['event_key']]
        declared = {row['key'] for row in root['blackboard']}
        expected = ({'child_buff_id'} if record['event_key'].startswith('buff_common_affixes_') else
                    {'lv'} if record['event_key'].startswith('buff_wpn_') else
                    {'def_decrease_tick_final', 'max_def_decrease_final'})
        assert expected <= declared
