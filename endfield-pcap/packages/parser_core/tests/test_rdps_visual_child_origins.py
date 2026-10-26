"""Synthetic origin contracts against current official roots and old approvals.

These tests isolate one existing whitelist entry so unrelated direct main-Buff
approvals cannot conceal a missing projection. No game rates are reconstructed.
"""
from copy import deepcopy

import pytest

from parser_core import battle_log_parser as parser


CASES = [
    ('buff_chr_0011_seraph_ultimate_effect', 'buff_chr_0011_seraph_atk_buff', 6,
     'buff_common_affixes_enhance_crystal', 'chr_0011_seraph', 'amp', ('cryst',)),
    ('buff_chr_0011_seraph_ultimate_effect_2', 'buff_chr_0011_seraph_atk_buff', 7,
     'buff_common_affixes_enhance_natural', 'chr_0011_seraph', 'amp', ('natural',)),
    ('buff_common_affixes_enhance_spell_default_child', 'buff_chr_0011_seraph_potential_1_atkup', 0,
     'buff_common_affixes_enhance_spell', 'chr_0011_seraph', 'amp', ('fire', 'pulse', 'cryst', 'natural')),
    ('buff_common_affixes_vulnerable_crystal_lizhiyan_child', 'buff_chr_0032_lizhiyan_combo_skill_spell_vulnerable', 2,
     'buff_common_affixes_vulnerable_crystal', 'chr_0032_lizhiyan', 'fragile', ('cryst',)),
    ('buff_common_affixes_vulnerable_natural_lizhiyan_child', 'buff_chr_0032_lizhiyan_combo_skill_spell_vulnerable', 1,
     'buff_common_affixes_vulnerable_natural', 'chr_0032_lizhiyan', 'fragile', ('natural',)),
    ('buff_common_affixes_vulnerable_pulse_default_child', 'buff_chr_0012_avywen_ultimate_skill_debuff', 0,
     'buff_common_affixes_vulnerable_pulse', 'chr_0012_avywen', 'fragile', ('pulse',)),
]


@pytest.fixture
def isolate_approval(monkeypatch):
    actual = deepcopy(parser._load_rdps_semantics_registry())

    def apply(key, *, remove_direct_alias=False):
        registry = deepcopy(actual)
        registry['verified_effects'] = {key: deepcopy(actual['verified_effects'][key])}
        registry['verified_prefixes'] = []
        if remove_direct_alias:
            # Ayvwen already approves its main Buff alias. Removing that alias
            # only in this test isolates the new fallback without expanding
            # the remaining declaration, its effect or either source guard.
            registry['verified_effects'][key].pop('aliases', None)
        monkeypatch.setattr(parser, '_load_rdps_semantics_registry', lambda: registry)
        return registry

    return apply


def record_for(case):
    _, parent, action, main, source, _, _ = case
    uid = '9223372036854775901'
    source_actor = '18446744073709550001'
    target_actor = '18446744073709550002'
    record = {
        'uid': uid, 'event_key': main, 'raw_event_key': main,
        'source_actor_id': source_actor, 'target_actor_id': target_actor,
        'source_character_key': source, 'target_character_key': 'chr_0004_pelica',
        'bb_values': {'rate': 0.37},
        'origin_observations': [{
            'schemaVersion': 1, '_boundCreateRecord': True, 'buffUid': uid,
            'buffTemplate': main, 'sourceActorId': source_actor, 'targetActorId': target_actor,
            'environmentType': 'Buff', 'ownerType': '1',
            'environmentInstId': '9223372036854775900',
            'templateStrId': parent, 'templateIntId': None,
            'actionId': action, 'operationSeq': 7, 'triggerType': 0,
        }],
    }
    if source == 'chr_0012_avywen':
        record['source_skill_family_key'] = 'chr_0012_avywen_ultimate_skill'
    return record


@pytest.mark.parametrize('case', CASES, ids=[case[0] for case in CASES])
@pytest.mark.parametrize('use_parse_cache', [False, True])
def test_visual_child_approval_projects_to_exact_current_main(isolate_approval, case, use_parse_cache):
    isolate_approval(case[0], remove_direct_alias=True)
    record = record_for(case)
    effects = parser._collect_zone_effects(record, root_lookup_cache={} if use_parse_cache else None)
    assert {(effect['zone'], effect['element'], effect['rate']) for effect in effects} == {
        (case[5], element, 0.37) for element in case[6]}
    assert all(effect['external_rdps_allowed'] is True for effect in effects)
    assert all(effect['rdps_whitelist_keys'] == [case[0]] for effect in effects)
    assert all(effect.get('keyword_origin') == record['origin_observations'] for effect in effects)


@pytest.mark.parametrize('parent', [
    'buff_chr_0032_lizhiyan_combo_skill_spell_vulnerable_pre',
    'buff_chr_0032_lizhiyan_talent1_vulnerable',
])
@pytest.mark.parametrize('case', CASES[3:5], ids=['crystal', 'natural'])
def test_lizhiyan_exact_alternative_parents_preserve_old_approval(isolate_approval, case, parent):
    isolate_approval(case[0])
    record = record_for(case)
    record['origin_observations'][0]['templateStrId'] = parent
    entry = parser._rdps_registry_matching_effect_entry(record, require_guard=True)
    assert entry is not None
    assert entry['_rdps_whitelist_keys'] == [case[0]]


@pytest.mark.parametrize('mutation', [
    'missing_origin', 'unbound_origin', 'wrong_uid', 'wrong_source_actor', 'wrong_target_actor',
    'wrong_parent', 'wrong_main_template', 'wrong_record_main', 'wrong_action',
    'wrong_environment_type', 'wrong_owner_type', 'conflicting_origins',
    'ambiguous_template_oneof', 'empty_template', 'boolean_action', 'float_action', 'string_action',
])
def test_visual_projection_rejects_missing_or_conflicting_identity(isolate_approval, mutation):
    case = CASES[0]
    isolate_approval(case[0])
    record = record_for(case)
    origin = record['origin_observations'][0]
    if mutation == 'missing_origin':
        record['origin_observations'] = []
    elif mutation == 'unbound_origin':
        origin['_boundCreateRecord'] = False
    elif mutation == 'wrong_uid':
        origin['buffUid'] = '9223372036854775902'
    elif mutation == 'wrong_source_actor':
        origin['sourceActorId'] = '18446744073709550003'
    elif mutation == 'wrong_target_actor':
        origin['targetActorId'] = '18446744073709550003'
    elif mutation == 'wrong_parent':
        origin['templateStrId'] = 'buff_chr_0011_seraph_potential_1_atkup'
    elif mutation == 'wrong_main_template':
        origin['buffTemplate'] = 'buff_common_affixes_enhance_natural'
    elif mutation == 'wrong_record_main':
        record['event_key'] = record['raw_event_key'] = 'buff_common_affixes_enhance_natural'
        origin['buffTemplate'] = record['event_key']
    elif mutation == 'wrong_action':
        origin['actionId'] = 7
    elif mutation == 'wrong_environment_type':
        origin['environmentType'] = 'Skill'
    elif mutation == 'wrong_owner_type':
        origin['ownerType'] = '2'
    elif mutation == 'conflicting_origins':
        record['origin_observations'].append({**origin, 'actionId': 7})
    elif mutation == 'ambiguous_template_oneof':
        origin['templateIntId'] = 1
    elif mutation == 'empty_template':
        origin['templateStrId'] = ''
    elif mutation == 'boolean_action':
        origin['actionId'] = True
    elif mutation == 'float_action':
        origin['actionId'] = 6.0
    elif mutation == 'string_action':
        origin['actionId'] = '6'
    assert parser._rdps_registry_matching_effect_entry(record, require_guard=True) is None


@pytest.mark.parametrize('value', ['', '0', '4294967295', '-1', 'not-an-id', True, 1.5])
def test_visual_projection_rejects_invalid_parent_instance(isolate_approval, value):
    isolate_approval(CASES[0][0])
    record = record_for(CASES[0])
    record['origin_observations'][0]['environmentInstId'] = value
    assert parser._rdps_registry_matching_effect_entry(record, require_guard=True) is None


def test_raw_template_disagreement_cannot_validate_a_different_canonical_main(isolate_approval):
    isolate_approval(CASES[0][0])
    record = record_for(CASES[0])
    record['raw_event_key'] = 'buff_common_affixes_enhance_natural'
    record['origin_observations'][0]['buffTemplate'] = record['raw_event_key']
    assert parser._rdps_registry_matching_effect_entry(record, require_guard=True) is None


@pytest.mark.parametrize('record_key,origin_key', [
    ('uid', 'buffUid'), ('source_actor_id', 'sourceActorId'), ('target_actor_id', 'targetActorId'),
])
def test_visual_projection_does_not_treat_matching_empty_identities_as_proof(isolate_approval, record_key, origin_key):
    isolate_approval(CASES[0][0])
    record = record_for(CASES[0])
    record[record_key] = record['origin_observations'][0][origin_key] = ''
    assert parser._rdps_registry_matching_effect_entry(record, require_guard=True) is None


def test_repeated_identical_origin_does_not_duplicate_projected_effect(isolate_approval):
    isolate_approval(CASES[0][0])
    record = record_for(CASES[0])
    record['origin_observations'].append(deepcopy(record['origin_observations'][0]))
    effects = parser._collect_zone_effects(record)
    assert len(effects) == 1
    assert effects[0]['rate'] == 0.37
    assert effects[0]['rdps_whitelist_keys'] == [CASES[0][0]]
    assert effects[0]['external_rdps_allowed'] is True


@pytest.mark.parametrize('source,parent', [
    ('chr_0032_lizhiyan', 'buff_chr_0032_lizhiyan_talent1_enhance'),
    ('chr_0035_liino', 'buff_chr_0035_liino_atkup_finishtime'),
])
def test_shared_default_child_does_not_authorize_another_character(isolate_approval, source, parent):
    isolate_approval(CASES[2][0])
    record = record_for(CASES[2])
    record['source_character_key'] = source
    record['origin_observations'][0]['templateStrId'] = parent
    assert parser._rdps_registry_matching_effect_entry(record, require_guard=True) is None


@pytest.mark.parametrize('source,family', [
    ('chr_0012_avywen', None),
    ('chr_0012_avywen', 'chr_0012_avywen_normal_skill'),
    ('chr_0011_seraph', 'chr_0012_avywen_ultimate_skill'),
])
def test_avywen_projection_keeps_both_original_guards(isolate_approval, source, family):
    isolate_approval(CASES[5][0], remove_direct_alias=True)
    record = record_for(CASES[5])
    record['source_character_key'] = source
    record['source_skill_family_key'] = family
    assert parser._rdps_registry_matching_effect_entry(record, require_guard=True) is None


def test_existing_direct_main_alias_does_not_require_new_projection_origin(isolate_approval):
    isolate_approval(CASES[5][0])
    record = record_for(CASES[5])
    record['origin_observations'] = []
    entry = parser._rdps_registry_matching_effect_entry(record, require_guard=True)
    assert entry is not None
    assert entry['_rdps_whitelist_keys'] == [CASES[5][0]]


@pytest.mark.parametrize('change', ['disabled_action', 'disabled_ancestor', 'duplicate_action_id'])
@pytest.mark.parametrize('use_parse_cache', [False, True])
def test_projection_requires_unique_enabled_action_and_ancestors(isolate_approval, monkeypatch, change, use_parse_cache):
    case = CASES[0]
    isolate_approval(case[0])
    actual_roots = parser._load_current_buff_roots()
    roots = dict(actual_roots)
    roots[case[1]] = deepcopy(actual_roots[case[1]])
    actions = roots[case[1]]['buffEventAction'][0]['actions'][0]['actionData']
    action = actions[3]
    assert action['serverActionIndex'] == 6
    if change == 'disabled_action':
        action['isEnable'] = False
    elif change == 'disabled_ancestor':
        # Synthetic ancestor tests traversal context, not a new game relation.
        actions[3] = {'$type': 'Beyond.Gameplay.Core.IfElseAction.IfElseActionData',
                      'isEnable': False, 'serverActionIndex': 999,
                      'succeedActions': {'actionData': [action]}}
    else:
        actions.append(deepcopy(action))
    monkeypatch.setattr(parser, '_load_current_buff_roots', lambda: roots)
    assert parser._rdps_registry_matching_effect_entry(record_for(case), require_guard=True,
                root_lookup_cache={} if use_parse_cache else None) is None


@pytest.mark.parametrize('change', ['wrong_visual_child', 'dynamic_override_id', 'ambiguous_default'])
def test_projection_requires_exact_unambiguous_visual_child_reference(isolate_approval, monkeypatch, change):
    case = CASES[2] if change == 'ambiguous_default' else CASES[0]
    isolate_approval(case[0])
    actual_roots = parser._load_current_buff_roots()
    roots = dict(actual_roots)
    roots[case[1]] = deepcopy(actual_roots[case[1]])
    if change == 'ambiguous_default':
        roots[case[3]] = deepcopy(actual_roots[case[3]])
        roots[case[3]]['blackboard'].append({
            '$type': 'Beyond.Blackboard.DataPair', 'key': 'child_buff_id',
            'valueStr': 'buff_not_approved', 'valueDouble': 0.0, 'isDynamic': False})
    else:
        action = roots[case[1]]['buffEventAction'][0]['actions'][0]['actionData'][3]
        if change == 'wrong_visual_child':
            action['childBuffId']['value'] = CASES[1][0]
        else:
            action['childBuffId']['useBlackboardKey'] = True
            action['childBuffId']['blackboardKey'] = 'unknown_runtime_child'
    monkeypatch.setattr(parser, '_load_current_buff_roots', lambda: roots)
    assert parser._rdps_registry_matching_effect_entry(record_for(case), require_guard=True) is None


def test_visual_child_itself_does_not_gain_a_second_numeric_modifier(isolate_approval):
    case = CASES[0]
    isolate_approval(case[0])
    record = record_for(case)
    record['event_key'] = record['raw_event_key'] = case[0]
    record['origin_observations'] = []
    assert parser._collect_zone_effects(record) == []


def test_projected_approval_allocates_once_from_observed_main_rate(isolate_approval):
    case = CASES[0]
    isolate_approval(case[0])
    main = record_for(case)
    visual = {**deepcopy(main), 'uid': '9223372036854775902',
              'event_key': case[0], 'raw_event_key': case[0], 'origin_observations': []}
    # Explicitly supplied active windows exercise the allocation convention;
    # they are not evidence that a protocol create implies initial enable.
    windows = [{**row, 'start_ts_ms': 0, 'end_ts_ms': 1000,
                'zone_effects': parser._collect_zone_effects(row)} for row in [main, visual]]
    hit = {'character_key': 'chr_0004_pelica', 'source_actor_id': main['target_actor_id'],
           'target_enemy_key': 'eny_0051_rodin', 'target_actor_id': '2001',
           'hit_value': 137, 'ts_ms': 100, 'damage_element': 'cryst', 'damage_school': 'spell'}
    result = parser._allocate_rdps_for_hit(hit, windows, include_debug=True)
    assert result['contributions_map'] == pytest.approx({'chr_0004_pelica': 100, 'chr_0011_seraph': 37})
    external = [row for zone in result['zones'] for row in zone['contributors'] if row.get('scope') == 'external']
    assert len(external) == 1
    assert external[0]['rdps_whitelist_keys'] == [case[0]]


def test_projection_does_not_replace_missing_main_rate_with_parent_key(isolate_approval):
    isolate_approval(CASES[0][0])
    record = record_for(CASES[0])
    record['bb_values'] = {'final_final_atkup': 0.37}
    effects = parser._collect_zone_effects(record)
    assert not any(effect.get('external_rdps_allowed') is True and effect.get('rate', 0) > 0 for effect in effects)


@pytest.mark.parametrize('field,value', [('zone', 'atk'), ('element', 'natural')])
def test_projected_approval_cannot_expand_declared_zone_or_element(isolate_approval, field, value):
    # The altered declaration models a conflicting approval boundary, not a
    # proposed registry change. Official main-Buff semantics remain intact.
    registry = isolate_approval(CASES[0][0])
    registry['verified_effects'][CASES[0][0]]['effects'][0][field] = value
    effects = parser._collect_zone_effects(record_for(CASES[0]))
    assert not any(effect.get('external_rdps_allowed') is True for effect in effects)


@pytest.mark.parametrize('change', [
    'disabled_forward', 'missing_rate_assignment', 'duplicate_rate_assignment',
    'literal_rate_assignment', 'different_rate_input', 'missing_forward',
])
@pytest.mark.parametrize('use_parse_cache', [False, True])
def test_visual_projection_requires_valid_main_to_child_forwarding(isolate_approval, monkeypatch, change, use_parse_cache):
    case = CASES[0]
    isolate_approval(case[0])
    actual_roots = parser._load_current_buff_roots()
    roots = dict(actual_roots)
    roots[case[3]] = deepcopy(actual_roots[case[3]])
    actions = roots[case[3]]['buffEventAction'][0]['actions'][0]['actionData']
    action = actions[0]
    assert action['$type'] == 'Beyond.Gameplay.Core.CreateBuffAction.Data'
    created = action['buffs'][0]
    assert created['buffIdKey'] == 'child_buff_id'
    assignments = created['assignItems']
    rate_assignment = next(row for row in assignments if row['targetKey'] == 'rate')
    if change == 'disabled_forward':
        action['isEnable'] = False
    elif change == 'missing_rate_assignment':
        assignments.remove(rate_assignment)
    elif change == 'duplicate_rate_assignment':
        assignments.append(deepcopy(rate_assignment))
    elif change == 'literal_rate_assignment':
        rate_assignment['useDirectValue'] = True
        rate_assignment['numericValue'] = 0.37
    elif change == 'different_rate_input':
        rate_assignment['inputValueKey'] = 'another_rate'
    elif change == 'missing_forward':
        actions.clear()
    monkeypatch.setattr(parser, '_load_current_buff_roots', lambda: roots)
    assert parser._rdps_registry_matching_effect_entry(record_for(case), require_guard=True,
                root_lookup_cache={} if use_parse_cache else None) is None


def test_forward_cache_keeps_create_nodes_without_an_integer_action_index(isolate_approval, monkeypatch):
    case = CASES[0]
    isolate_approval(case[0])
    roots = dict(parser._load_current_buff_roots())
    roots[case[3]] = deepcopy(roots[case[3]])
    action = roots[case[3]]['buffEventAction'][0]['actions'][0]['actionData'][0]
    assert action['$type'] == 'Beyond.Gameplay.Core.CreateBuffAction.Data'
    action.pop('serverActionIndex')
    monkeypatch.setattr(parser, '_load_current_buff_roots', lambda: roots)
    record = record_for(case)
    expected = parser._rdps_registry_matching_effect_entry(record, require_guard=True)
    assert expected is not None
    assert parser._rdps_registry_matching_effect_entry(record, require_guard=True, root_lookup_cache={}) == expected
