"""Independent approval-boundary review for the current Antal binding."""
from copy import deepcopy

import pytest

from parser_core import battle_log_parser as parser

APPROVAL = 'buff_chr_0023_antal_ultimate_enhance'
CURRENT_PARENT = 'buff_chr_0023_antal_utimate_skill'
PULSE_MAIN = 'buff_common_affixes_enhance_pulse'


def antal_main_record(element, parent_form='string'):
    main = PULSE_MAIN if element == 'pulse' else 'buff_common_affixes_enhance_fire'
    uid, source, target = '9223372036854777011', '9223372036854777012', '9223372036854777013'
    return {
        'uid': uid, 'event_key': main, 'raw_event_key': main,
        'source_character_key': 'chr_0023_antal', 'target_character_key': 'chr_0004_pelica',
        'source_actor_id': source, 'target_actor_id': target, 'bb_values': {'rate': 0.25},
        'origin_observations': [{
            'schemaVersion': 1, '_boundCreateRecord': True, 'buffUid': uid, 'buffTemplate': main,
            'sourceActorId': source, 'targetActorId': target, 'environmentType': 'Buff', 'ownerType': '1',
            'environmentInstId': '9223372036854777010',
            'templateStrId': CURRENT_PARENT if parent_form == 'string' else None,
            'templateIntId': None if parent_form == 'string' else 1074,
            'actionId': 0 if element == 'pulse' else 1, 'operationSeq': 21, 'triggerType': 0,
        }],
    }


@pytest.mark.parametrize('element', ['pulse', 'fire'])
@pytest.mark.parametrize('change', ['missing_origin', 'unapproved_source'])
def test_full_registry_does_not_bypass_antal_origin_or_source_guard(element, change):
    # Use the full current registry: unrelated public-main declarations must
    # not silently grant Antal's approved identity to these records.
    record = antal_main_record(element)
    if change == 'missing_origin':
        record['origin_observations'] = []
    else:
        record['source_character_key'] = 'chr_0004_pelica'
    assert parser._rdps_registry_matching_effect_entry(record, require_guard=True) is None
    assert not any(effect.get('external_rdps_allowed') is True
                   for effect in parser._collect_zone_effects(record))


def test_independent_liino_main_approval_is_not_reported_as_antal():
    record = antal_main_record('pulse')
    record['source_character_key'] = 'chr_0035_liino'
    entry = parser._rdps_registry_matching_effect_entry(record, require_guard=True)
    assert entry is not None
    assert entry['_rdps_whitelist_keys'] == [PULSE_MAIN]
    assert APPROVAL not in entry['_rdps_whitelist_keys']


@pytest.mark.parametrize('element', ['pulse', 'fire'])
@pytest.mark.parametrize('change', ['zone', 'element', 'rate_operand'])
def test_conflicting_parent_alias_declarations_are_not_chosen_by_order(monkeypatch, element, change):
    registry = deepcopy(parser._load_rdps_semantics_registry())
    original = registry['verified_effects'][APPROVAL]
    competing = deepcopy(original)
    spec = next(row for row in competing['effects'] if row['element'] == element)
    if change == 'zone':
        spec['zone'] = 'atk'
    elif change == 'element':
        spec['element'] = 'natural'
    else:
        spec['bb_key'] = 'a_different_operand'
    # Both declarations match this same source and exact official alias.
    # Either insertion order must remain ambiguous and reject projection.
    for first in [True, False]:
        pairs = [('competing_approval_fixture', competing), (APPROVAL, original)]
        if not first:
            pairs.reverse()
        isolated = {**registry, 'verified_effects': dict(pairs), 'verified_prefixes': []}
        monkeypatch.setattr(parser, '_load_rdps_semantics_registry', lambda: isolated)
        assert parser._rdps_registry_matching_effect_entry(antal_main_record(element), require_guard=True) is None


def test_current_official_numeric_parent_relation_is_required(monkeypatch):
    record = antal_main_record('pulse', 'numeric')
    # A stale internal canonical hint must not rescue an absent official
    # numeric relation, even though 1074 remains listed in the approval.
    monkeypatch.setattr(parser, '_load_num_id_str_buff_map', lambda: {})
    monkeypatch.setattr(parser, '_canonical_num_table_buff_number', lambda *args, **kwargs: CURRENT_PARENT)
    assert parser._rdps_registry_matching_effect_entry(record, require_guard=True) is None
