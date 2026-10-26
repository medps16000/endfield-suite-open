"""Synthetic contracts for the existing Antal approval's current official root.

Current root fields and number-table identity are real static inputs. Rates,
actor IDs, active windows and packet origins below are explicit test fixtures.
"""
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

from parser_core import battle_log_parser as parser


APPROVAL = 'buff_chr_0023_antal_ultimate_enhance'
CURRENT_PARENT = 'buff_chr_0023_antal_utimate_skill'
PULSE_MAIN = 'buff_common_affixes_enhance_pulse'
FIRE_MAIN = 'buff_common_affixes_enhance_fire'


@pytest.fixture
def only_antal_approval(monkeypatch):
    actual = deepcopy(parser._load_rdps_semantics_registry())
    # Public main Buffs may have other legitimate independent approvals.
    # Isolating this one entry tests its own guard and identity boundary,
    # without deleting or narrowing those unrelated production declarations.
    actual['verified_effects'] = {APPROVAL: actual['verified_effects'][APPROVAL]}
    actual['verified_prefixes'] = []
    monkeypatch.setattr(parser, '_load_rdps_semantics_registry', lambda: actual)
    return actual


def antal_main_record(element, parent_form='string'):
    main, action = (PULSE_MAIN, 0) if element == 'pulse' else (FIRE_MAIN, 1)
    return {
        'uid': '9223372036854776011', 'event_key': main, 'raw_event_key': main,
        'source_character_key': 'chr_0023_antal', 'target_character_key': 'chr_0004_pelica',
        'source_actor_id': '9223372036854776012', 'target_actor_id': '9223372036854776013',
        'bb_values': {'rate': 0.25},
        'origin_observations': [{
            'schemaVersion': 1, '_boundCreateRecord': True,
            'buffUid': '9223372036854776011', 'buffTemplate': main,
            'sourceActorId': '9223372036854776012', 'targetActorId': '9223372036854776013',
            'environmentType': 'Buff', 'ownerType': '1', 'environmentInstId': '9223372036854776010',
            'templateStrId': CURRENT_PARENT if parent_form == 'string' else None,
            'templateIntId': None if parent_form == 'string' else 1074,
            'actionId': action, 'operationSeq': 21, 'triggerType': 0,
        }],
    }


def test_antal_binding_preserves_original_approval_and_review_ids():
    registry = parser._load_rdps_semantics_registry()['verified_effects']
    entry = registry[APPROVAL]
    assert CURRENT_PARENT not in registry
    assert entry['aliases'] == [CURRENT_PARENT]
    assert entry['numeric_ids'] == ['1074']
    assert entry['guard'] == {'source_character_key': 'chr_0023_antal'}
    assert entry['value_source'] == 'runtime_packet'
    assert entry['effects'] == [{'zone': 'amp', 'element': 'pulse', 'bb_key': 'rate'},
                                {'zone': 'amp', 'element': 'fire', 'bb_key': 'rate'}]
    assert parser._canonical_num_table_buff_number('1074', 'chr_0023_antal') == CURRENT_PARENT
    catalog_path = Path(__file__).resolve().parents[3] / 'data/packet_semantics/rdps_effect_catalog_review.json'
    catalog = json.loads(catalog_path.read_text(encoding='utf-8'))
    ids = {row['rdps_effect_id'] for row in catalog['effect_catalog']
           if any(member.get('canonical') == APPROVAL for member in row.get('runtime_members', []))}
    assert ids == {'RDPS-0019', 'RDPS-0020'}


@pytest.mark.parametrize('element,action,main', [('pulse', 0, PULSE_MAIN), ('fire', 1, FIRE_MAIN)])
def test_current_antal_root_actions_match_official_keyword_mapping(element, action, main):
    root = parser._load_current_buff_roots()[CURRENT_PARENT]
    assert root['attributeModifier']['attributeModifiers'] == []
    assert root['damageModifier'] == []
    actions = root['buffEventAction'][0]['actions'][0]['actionData']
    selected = [row for row in actions if row.get('serverActionIndex') == action]
    assert len(selected) == 1
    selected = selected[0]
    assert selected['isEnable'] is True
    assert selected['$type'] == 'Beyond.Gameplay.Core.EnhancedAction.Data'
    assert selected['rate']['useBlackboardKey'] is True and selected['rate']['blackboardKey'] == 'rate'
    assert parser._load_rdps_semantics_registry()['_keyword_buff_map']['Enhanced'][selected['subType']] == main
    assert element in {'pulse', 'fire'}


@pytest.mark.parametrize('element', ['pulse', 'fire'])
@pytest.mark.parametrize('parent_form', ['string', 'numeric'])
@pytest.mark.parametrize('use_parse_cache', [False, True])
def test_current_antal_parent_projects_only_observed_main_rate(only_antal_approval, element, parent_form, use_parse_cache):
    record = antal_main_record(element, parent_form)
    effects = parser._collect_zone_effects(record, root_lookup_cache={} if use_parse_cache else None)
    assert len(effects) == 1
    assert effects[0]['zone'] == 'amp'
    assert effects[0]['element'] == element
    assert effects[0]['rate'] == 0.25
    assert effects[0]['external_rdps_allowed'] is True
    assert effects[0]['rdps_whitelist_keys'] == [APPROVAL]
    assert effects[0]['keyword_origin'] == record['origin_observations']


@pytest.mark.parametrize('element', ['pulse', 'fire'])
def test_equivalent_numeric_and_string_parent_observations_keep_one_approval(only_antal_approval, element):
    record = antal_main_record(element)
    record['origin_observations'].append(antal_main_record(element, 'numeric')['origin_observations'][0])
    entry = parser._rdps_registry_matching_effect_entry(record, require_guard=True)
    assert entry is not None and entry['_rdps_whitelist_keys'] == [APPROVAL]


@pytest.mark.parametrize('mutation', [
    'missing_origin', 'wrong_source_character', 'unknown_source_character', 'wrong_source_actor',
    'wrong_target_actor', 'wrong_uid', 'wrong_action', 'wrong_parent',
    'wrong_element', 'conflicting_origins', 'bool_parent_number', 'string_parent_number',
    'ambiguous_parent_oneof', 'unbound_origin',
])
@pytest.mark.parametrize('use_parse_cache', [False, True])
def test_antal_origin_guard_and_identity_fail_closed(only_antal_approval, mutation, use_parse_cache):
    record = antal_main_record('pulse')
    origin = record['origin_observations'][0]
    if mutation == 'missing_origin':
        record['origin_observations'] = []
    elif mutation == 'wrong_source_character':
        record['source_character_key'] = 'chr_0035_liino'
    elif mutation == 'unknown_source_character':
        record['source_character_key'] = None
    elif mutation == 'wrong_source_actor':
        origin['sourceActorId'] = '9223372036854776099'
    elif mutation == 'wrong_target_actor':
        origin['targetActorId'] = '9223372036854776099'
    elif mutation == 'wrong_uid':
        origin['buffUid'] = '9223372036854776099'
    elif mutation == 'wrong_action':
        origin['actionId'] = 1
    elif mutation == 'wrong_parent':
        origin['templateStrId'] = 'buff_chr_0023_antal_tageffect'
    elif mutation == 'wrong_element':
        record['event_key'] = record['raw_event_key'] = 'buff_common_affixes_enhance_natural'
        origin['buffTemplate'] = record['event_key']
    elif mutation == 'conflicting_origins':
        record['origin_observations'].append({**origin, 'actionId': 1})
    elif mutation in {'bool_parent_number', 'string_parent_number'}:
        origin['templateStrId'] = None
        origin['templateIntId'] = True if mutation == 'bool_parent_number' else '1074'
    elif mutation == 'ambiguous_parent_oneof':
        origin['templateIntId'] = 1074
    elif mutation == 'unbound_origin':
        origin['_boundCreateRecord'] = False
    assert parser._rdps_registry_matching_effect_entry(record, require_guard=True,
                root_lookup_cache={} if use_parse_cache else None) is None


@pytest.mark.parametrize('element,icon', [('pulse', 'buff_chr_0023_antal_ultimate_icon_2'),
                                         ('fire', 'buff_chr_0023_antal_ultimate_icon')])
def test_antal_parent_and_icon_do_not_duplicate_main_contribution(only_antal_approval, element, icon):
    main = antal_main_record(element)
    parent = {**deepcopy(main), 'uid': '9223372036854776010',
              'event_key': CURRENT_PARENT, 'raw_event_key': CURRENT_PARENT, 'origin_observations': []}
    visual = {**deepcopy(main), 'uid': '9223372036854776014',
              'event_key': icon, 'raw_event_key': icon, 'origin_observations': []}
    assert parser._collect_zone_effects(parent) == []
    assert parser._collect_zone_effects(visual) == []
    # Active is explicitly given by this allocation fixture; this is not a
    # claim that every protocol create is initially enabled.
    windows = [{**record, 'start_ts_ms': 0, 'end_ts_ms': 1000,
                'zone_effects': parser._collect_zone_effects(record)} for record in [parent, main, visual]]
    hit = {'character_key': 'chr_0004_pelica', 'source_actor_id': main['target_actor_id'],
           'target_enemy_key': 'eny_0051_rodin', 'target_actor_id': '2001',
           'hit_value': 125, 'ts_ms': 100, 'damage_element': element, 'damage_school': 'spell'}
    result = parser._allocate_rdps_for_hit(hit, windows, include_debug=True)
    assert result['contributions_map'] == pytest.approx({'chr_0004_pelica': 100, 'chr_0023_antal': 25})


def test_main_rate_is_not_replaced_with_parent_default(only_antal_approval):
    record = antal_main_record('pulse')
    record['bb_values'] = {}
    assert not any(effect.get('rate', 0) > 0 and effect.get('external_rdps_allowed') is True
                   for effect in parser._collect_zone_effects(record))


def test_existing_liino_public_pulse_approval_keeps_its_independent_guard():
    registry = parser._load_rdps_semantics_registry()['verified_effects']
    entry = registry[PULSE_MAIN]
    assert entry['guard'] == {'source_character_key': 'chr_0035_liino'}
    assert parser._rdps_registry_entry_matches_guard(entry, {'source_character_key': 'chr_0023_antal'}) is False
    record = {'event_key': PULSE_MAIN, 'raw_event_key': PULSE_MAIN,
              'source_character_key': 'chr_0035_liino', 'bb_values': {'rate': 0.25}}
    matched = parser._rdps_registry_matching_effect_entry(record, require_guard=True)
    assert matched is not None and matched['_rdps_whitelist_keys'] == [PULSE_MAIN]


def test_keyword_root_cache_is_explicit_and_never_memoizes_record_guards_or_registry(only_antal_approval, monkeypatch):
    roots = dict(parser._load_current_buff_roots())
    visits = []

    class CountedRoot(dict):
        def values(self):
            if sys._getframe(1).f_code is parser._rdps_registry_matching_effect_entry.__code__:
                visits.append(True)
            return super().values()

    roots[CURRENT_PARENT] = CountedRoot(deepcopy(roots[CURRENT_PARENT]))
    monkeypatch.setattr(parser, '_load_current_buff_roots', lambda: roots)
    record = antal_main_record('pulse')
    expected = parser._rdps_registry_matching_effect_entry(record, require_guard=True)
    assert expected is not None
    assert parser._rdps_registry_matching_effect_entry(record, require_guard=True) == expected
    assert len(visits) == 2  # Independent calls keep the original uncached path.
    cache = {}
    for _ in range(3):
        assert parser._rdps_registry_matching_effect_entry(record, require_guard=True, root_lookup_cache=cache) == expected
    assert len(visits) == 3
    foreign = {**record, 'source_character_key': 'chr_0035_liino'}
    assert parser._rdps_registry_matching_effect_entry(foreign, require_guard=True, root_lookup_cache=cache) is None
    assert parser._rdps_registry_matching_effect_entry(foreign, require_guard=False, root_lookup_cache=cache) == expected
    replacement = deepcopy(only_antal_approval)
    replacement['verified_effects'][APPROVAL]['guard']['source_character_key'] = 'chr_0035_liino'
    monkeypatch.setattr(parser, '_load_rdps_semantics_registry', lambda: replacement)
    assert parser._rdps_registry_matching_effect_entry(record, require_guard=True, root_lookup_cache=cache) is None
    assert len(visits) == 3


@pytest.mark.parametrize('change', ['duplicate_keyword', 'unrelated_same_id', 'disabled_action', 'disabled_ancestor'])
def test_keyword_cache_keeps_its_original_type_filtered_duplicate_rule(only_antal_approval, monkeypatch, change):
    roots = dict(parser._load_current_buff_roots())
    roots[CURRENT_PARENT] = deepcopy(roots[CURRENT_PARENT])
    group = roots[CURRENT_PARENT]['buffEventAction'][0]['actions'][0]
    actions = group['actionData']
    selected = next(row for row in actions if row.get('serverActionIndex') == 0)
    if change == 'duplicate_keyword':
        actions.append(deepcopy(selected))
    elif change == 'unrelated_same_id':
        actions.append({'serverActionIndex': 0, 'isEnable': True, '$type': 'Beyond.Gameplay.Core.Unrelated.Data'})
    elif change == 'disabled_action':
        selected['isEnable'] = False
    else:
        group.update({'serverActionIndex': 999999, 'isEnable': False})
    monkeypatch.setattr(parser, '_load_current_buff_roots', lambda: roots)
    record = antal_main_record('pulse')
    uncached = parser._rdps_registry_matching_effect_entry(record, require_guard=True)
    cached = parser._rdps_registry_matching_effect_entry(record, require_guard=True, root_lookup_cache={})
    assert cached == uncached
    assert (cached is not None) is (change == 'unrelated_same_id')


def test_keyword_cache_rechecks_an_exact_replacement_root(only_antal_approval, monkeypatch):
    roots = dict(parser._load_current_buff_roots())
    monkeypatch.setattr(parser, '_load_current_buff_roots', lambda: roots)
    record = antal_main_record('pulse')
    cache = {}
    assert parser._rdps_registry_matching_effect_entry(record, require_guard=True, root_lookup_cache=cache) is not None
    old_root = roots[CURRENT_PARENT]
    roots[CURRENT_PARENT] = deepcopy(old_root)
    actions = roots[CURRENT_PARENT]['buffEventAction'][0]['actions'][0]['actionData']
    next(row for row in actions if row.get('serverActionIndex') == 0)['isEnable'] = False
    assert roots[CURRENT_PARENT] is not old_root
    assert parser._rdps_registry_matching_effect_entry(record, require_guard=True, root_lookup_cache=cache) is None
    # A new explicit scope and the default path must not reuse the old proof.
    assert parser._rdps_registry_matching_effect_entry(record, require_guard=True, root_lookup_cache={}) is None
    assert parser._rdps_registry_matching_effect_entry(record, require_guard=True) is None
