"""Observed BB state and refresh boundaries, not unobserved final modifiers."""
import copy
import json
import sys

import pytest

from parser_core import battle_log_parser as parser


BUFF = 'buff_common_natural_natural_corrupt_do'
UID = '9223372036854775901'
OWNER = '9223372036854775902'
SOURCE = '9223372036854775903'
SESSION = 'synthetic-dynamic-session'


def creation(stamp='10:00:00.000', sequence=1, session=SESSION):
    return [
        f'[{stamp}] BUFF_START #{sequence} id="{BUFF}" uid={UID} owner=eny_0051_rodin src=chr_0025_ardelia ownerId={OWNER} srcId={SOURCE} dur=30 enh=unknown',
        f'[{stamp}] BB[{UID}]: def_decrease=-10 additional_def_decrease=-5',
        f'[{stamp}] BUFF_ORIGIN ' + json.dumps({
            'schemaVersion': 1, 'buffUid': UID, 'buffTemplate': BUFF, 'sourceActorId': SOURCE, 'targetActorId': OWNER,
            'environmentType': 'Buff', 'ownerType': '1', 'environmentInstId': '1010',
            'templateStrId': 'buff_common_natural_natural_corrupt_triggered', 'templateIntId': None,
            'actionId': 7, 'operationSeq': sequence, 'triggerType': 0}),
        f'[{stamp}] BUFF_CREATE_OBSERVED ' + json.dumps({
            'schemaVersion': 1, 'buffUid': UID, 'buffTemplate': BUFF, 'sourceActorId': SOURCE,
            'buffNumId': 1984, 'buffId': BUFF, 'buffTemplateId': None,
            'assignedItems': [
                {'target_key': 'def_decrease', 'use_direct_value': True, 'numeric_value': -10.0},
                {'target_key': 'additional_def_decrease', 'use_direct_value': True, 'numeric_value': -5.0}],
            'targetActorId': OWNER, 'sessionId': session, 'operationSeq': sequence,
            'operationOwnerId': SOURCE, 'operationGeneration': 91, 'generationScope': 'operation_owner',
            'sourceEvent': 'BattleOpTriggerAction', 'isNewInstance': None, 'enhanceMultiplier': None,
            'creationActionType': 'BattleActionCreateBuff', 'creationActionRuntimeCase': 'create_buff_action'}),
    ]


def identity(seq, *, refresh=False, generation=7, session=SESSION, action=None):
    return {'schemaVersion': 1, 'sessionId': session, 'operationSeq': seq, 'operationOwnerId': OWNER,
            'ownerType': '1', 'ownerId': OWNER, 'environmentInstId': UID,
            'generation': generation, 'clientTickMs': str(seq * 100),
            'templateType': 'Buff', 'templateIdCase': 'int_id', 'templateIntId': 1984, 'templateStrId': None,
            'actionId': action if action is not None else (10 if refresh else 5),
            'triggerType': 0, 'actionType': 31 if refresh else 7,
            'actionRuntimeCase': 'refresh_buff_attr_modifier_value_action' if refresh else 'modify_dynamic_blackboard_action',
            'operationPayloadCase': 'trigger_action_op_data', **({'buffUid': UID} if refresh else {})}


def update(seq, value, *, stamp='10:00:00.500', **options):
    data = identity(seq, **options)
    return f'[{stamp}] BB_MODIFY_DYNAMIC skill={BUFF} actionId={data["actionId"]} seq={seq} tick={seq * 100} inst={UID} ownerId={OWNER} value={value!r} identity=' + json.dumps(data)


def refresh(seq, *, stamp='10:00:01.000', **options):
    return f'[{stamp}] BUFF_ATTR_REFRESH ' + json.dumps(identity(seq, refresh=True, **options))


def hit(seq, stamp='10:00:02.000'):
    return f'[{stamp}] HP_V2 #{seq} hit=120 cum=120 raw=120 packetFinalValue=120 packetSeq={seq} packetSessionId="{SESSION}" packetOwnerId={SOURCE} packetGeneration=12 pHP=100 eHP=1000 skill="fixture" hits=1 src=chr_0016_laevat tgt=eny_0051_rodin atk=chr_0016_laevat atkId={SOURCE} tgtId={OWNER}'


def report(lines):
    return parser.parse_raw_battle_log_text('\n'.join(lines + [hit(100)]), include_rdps_debug=True)


def snapshots(result):
    return [row for buff in result['buff_events'] for row in buff.get('attribute_refresh_snapshots') or []]


def test_add_result_replaces_pending_value_and_only_refresh_emits_snapshot():
    before = report(creation() + [update(2, -12.0)])
    assert snapshots(before) == []
    result = report(creation() + [update(2, -12.0), refresh(3)])
    rows = snapshots(result)
    assert len(rows) == 1
    assert rows[0]['bb_values']['def_decrease'] == -12.0
    assert rows[0]['bb_values']['additional_def_decrease'] == -5.0
    assert rows[0]['status'] == 'attribute_parameters_observed'
    assert {row['operand'] for row in rows[0]['attribute_parameters']} == {-12.0, -5.0}
    assert len(rows[0]['attribute_parameters']) == 10
    assert rows[0]['identity']['generation'] == 7
    assert result['buff_events'][0]['creation_observations'][0]['operationGeneration'] == 91
    assert rows[0]['enhance_multiplier'] is None and rows[0]['final_modifiers_verified'] is False
    assert result['buff_events'][0]['bb_values']['def_decrease'] == -10.0
    assert result['debug_hits'][0]['rdps_contributions'] == [
        {'character_key': 'chr_0016_laevat', 'character_name': '莱万汀', 'value': 120.0}]


def test_later_updates_do_not_rewrite_prior_refresh_snapshots():
    result = report(creation() + [update(2, -12.0), refresh(3),
        update(4, -18.0, stamp='10:00:01.100'), refresh(5, stamp='10:00:01.200')])
    assert [row['bb_values']['def_decrease'] for row in snapshots(result)] == [-12.0, -18.0]
    assert [row['operation_seq'] for row in snapshots(result)] == [3, 5]


def test_duplicate_older_update_is_idempotent_and_does_not_restore_old_value():
    result = report(creation() + [update(2, -12.0), refresh(3), update(4, -18.0),
                                 update(2, -12.0), refresh(5), refresh(5)])
    assert [row['bb_values']['def_decrease'] for row in snapshots(result)] == [-12.0, -18.0]
    assert result['dynamic_bb_events'][-1]['state_status'] == 'duplicate_dynamic_observation'


def test_conflicting_duplicate_does_not_choose_one_value():
    result = report(creation() + [update(2, -12.0), update(2, -13.0), refresh(3)])
    assert result['dynamic_bb_events'][1]['state_error'] == 'conflicting_dynamic_operation_order'
    assert snapshots(result)[0]['status'] == 'missing_attribute_parameters'
    assert snapshots(result)[0]['missing_parameters'] == ['def_decrease']


def test_late_conflicting_observation_marks_earlier_snapshot_unresolved():
    result = report(creation() + [update(2, -12.0), refresh(3), update(2, -13.0)])
    assert snapshots(result)[0]['status'] == 'conflicting_dynamic_operation'
    assert snapshots(result)[0]['conflicting_operation_seqs'] == [2]


@pytest.mark.parametrize('field,value', [
    ('ownerId', SOURCE), ('operationOwnerId', SOURCE), ('environmentInstId', '1234'),
    ('ownerType', '3'), ('templateType', 'Skill'), ('sessionId', 'wrong-session'),
    ('actionId', 5), ('actionId', True), ('triggerType', 1), ('templateIntId', 331),
    ('templateStrId', BUFF), ('actionRuntimeCase', 'modify_dynamic_blackboard_action'),
])
def test_refresh_requires_exact_buff_identity_and_typed_refresh_action(field, value):
    raw = identity(3, refresh=True)
    raw[field] = value
    line = '[10:00:01.000] BUFF_ATTR_REFRESH ' + json.dumps(raw)
    result = report(creation() + [update(2, -12.0), line])
    assert snapshots(result) == []
    assert result['buff_attribute_refresh_events'][0].get('state_error')


def test_missing_creation_anchor_does_not_bind_by_uid_alone():
    result = report(creation()[:2] + [update(2, -12.0), refresh(3)])
    assert snapshots(result) == []
    assert result['buff_attribute_refresh_events'][0]['state_error'] == 'missing_compatible_buff_creation_observation'


def test_creation_generation_is_not_the_buff_generation():
    result = report(creation() + [update(2, -12.0, generation=0), refresh(3, generation=0)])
    assert snapshots(result)[0]['status'] == 'attribute_parameters_observed'
    assert snapshots(result)[0]['identity']['generation'] == 0


def test_incompatible_operation_generations_do_not_mix_pending_fields():
    result = report(creation() + [update(2, -12.0), refresh(3, generation=8)])
    assert snapshots(result)[0]['status'] == 'missing_attribute_parameters'
    assert set(snapshots(result)[0]['missing_parameters']) == {'def_decrease', 'additional_def_decrease'}


def test_end_then_same_uid_does_not_inherit_prior_pending_update():
    result = report(creation() + [update(2, -12.0), hit(3, '10:00:00.600'), f'[10:00:00.700] BUFF_END #3 uid={UID}',
        *creation('10:00:00.800', sequence=4), refresh(5)])
    assert snapshots(result)[0]['bb_values']['def_decrease'] == -10.0
    assert len({row['instance_start_line_no'] for row in result['buff_events']}) == 2


def test_unbound_legacy_update_does_not_leave_a_false_known_initial_value():
    old = update(2, -12.0).split(' identity=', 1)[0]
    result = report(creation() + [old, refresh(3)])
    assert result['dynamic_bb_events'][0]['state_error'] == 'missing_dynamic_identity'
    assert snapshots(result)[0]['missing_parameters'] == ['def_decrease']


def test_snapshot_survives_matching_parameter_observation_without_rewriting_it():
    result = report(creation() + [update(2, -12.0), refresh(3),
        *creation('10:00:01.100', sequence=4), update(5, -18.0, stamp='10:00:01.200'),
        refresh(6, stamp='10:00:01.300')])
    unique = {row['operation_seq']: row for row in snapshots(result)}
    assert unique[3]['bb_values']['def_decrease'] == -12.0
    assert unique[6]['bb_values']['def_decrease'] == -18.0
    assert len({row['instance_start_line_no'] for row in result['buff_events']}) == 1


def test_hit_keeps_its_packet_context_without_equating_it_to_buff_context():
    result = report(creation() + [update(2, -12.0), refresh(3)])
    row = result['debug_hits'][0]
    assert (row['packet_session_id'], row['packet_owner_id'], row['packet_generation']) == (SESSION, SOURCE, 12)
    assert snapshots(result)[0]['identity']['operationOwnerId'] == OWNER


def test_replayed_creation_does_not_roll_back_pending_or_lifetime_boundaries():
    result = report(creation() + [update(2, -12.0), refresh(3), update(4, -18.0),
                                 *creation(), refresh(5)])
    assert [row['bb_values']['def_decrease'] for row in snapshots(result)] == [-12.0, -18.0]
    assert len(result['buff_events']) == 1
    assert result['buff_events'][0]['snapshot_end_ts_ms'] is None
    assert result['buff_events'][0]['repeated_creation_observations'][0]['status'] == 'duplicate'


def test_late_conflict_still_invalidates_snapshot_after_parameter_refresh():
    result = report(creation() + [update(2, -12.0), refresh(3),
                                 *creation('10:00:01.100', sequence=4), update(2, -13.0)])
    assert snapshots(result)[0]['status'] == 'conflicting_dynamic_operation'


@pytest.mark.parametrize('field,value', [('sessionId', 'foreign-session'), ('environmentInstId', '1234')])
def test_explicit_foreign_identity_does_not_poison_this_buff(field, value):
    original = update(2, -12.0)
    prefix, blob = original.split(' identity=', 1)
    data = json.loads(blob)
    data[field] = value
    result = report(creation() + [prefix + ' identity=' + json.dumps(data), refresh(3)])
    assert result['dynamic_bb_events'][0].get('state_error')
    assert snapshots(result)[0]['status'] == 'attribute_parameters_observed'
    assert snapshots(result)[0]['bb_values']['def_decrease'] == -10.0


def test_same_operation_owner_other_generation_does_not_seed_old_bb():
    lines = creation()
    prefix, blob = lines[-1].split(' BUFF_CREATE_OBSERVED ', 1)
    data = json.loads(blob)
    data['operationOwnerId'] = OWNER
    lines[-1] = prefix + ' BUFF_CREATE_OBSERVED ' + json.dumps(data)
    result = report(lines + [update(2, -12.0, generation=7), refresh(3, generation=7)])
    assert snapshots(result)[0]['missing_parameters'] == ['additional_def_decrease']


def test_conflicting_creation_raw_template_does_not_bind():
    lines = creation()
    prefix, blob = lines[-1].split(' BUFF_CREATE_OBSERVED ', 1)
    data = json.loads(blob)
    data['buffNumId'] = 331
    lines[-1] = prefix + ' BUFF_CREATE_OBSERVED ' + json.dumps(data)
    result = report(lines + [update(2, -12.0), refresh(3)])
    assert snapshots(result) == []


def test_creation_replay_after_end_does_not_resurrect_the_finished_instance():
    result = report(creation() + [update(2, -12.0), refresh(3), hit(4, '10:00:01.100'),
        f'[10:00:01.200] BUFF_END #5 uid={UID}', *creation(), refresh(6, stamp='10:00:01.500')])
    assert len(result['buff_events']) == 1
    assert [row['operation_seq'] for row in snapshots(result)] == [3]
    assert result['buff_attribute_refresh_events'][-1]['state_error'] == 'unbound_dynamic_buff_instance'
    assert result['buff_events'][0]['raw_end_ts_ms'] == 36001200


def test_old_creation_during_a_new_lifetime_preserves_the_new_start():
    result = report(creation() + [hit(2, '10:00:00.500'), f'[10:00:00.700] BUFF_END #3 uid={UID}',
        *creation('10:00:01.200', sequence=5), *creation(), refresh(6, stamp='10:00:01.500')])
    rows = result['buff_events']
    assert len(rows) == 2
    assert rows[1]['start_ts_ms'] == 36001200
    assert rows[1]['snapshot_end_ts_ms'] is None
    assert rows[1]['end_ts_ms'] >= rows[1]['start_ts_ms']
    assert rows[1]['attribute_refresh_snapshots'][0]['operation_seq'] == 6


def test_conflicting_assigned_items_are_preserved_without_picking_a_value():
    lines = creation()
    prefix, blob = lines[-1].split(' BUFF_CREATE_OBSERVED ', 1)
    data = json.loads(blob)
    data['assignedItems'].append({'target_key': 'additional_def_decrease', 'numeric_value': -6.0, 'use_direct_value': True})
    lines[-1] = prefix + ' BUFF_CREATE_OBSERVED ' + json.dumps(data)
    result = report(lines + [update(2, -12.0), refresh(3)])
    anchor = result['buff_events'][0]['creation_observations'][0]
    assert len(anchor['assignedItems']) == 3
    assert anchor['bb_conflicts'] == ['additional_def_decrease']
    assert 'additional_def_decrease' not in anchor['bb_values']
    assert snapshots(result)[0]['missing_parameters'] == ['additional_def_decrease']


def test_add_buff_observation_does_not_borrow_create_action_native_proof():
    lines = creation()
    prefix, blob = lines[-1].split(' BUFF_CREATE_OBSERVED ', 1)
    data = json.loads(blob)
    data['sourceEvent'] = 'BattleOpAddBuff'
    data['buffTemplateId'] = {'type': 'Buff', 'int_id': 1984}
    lines[-1] = prefix + ' BUFF_CREATE_OBSERVED ' + json.dumps(data)
    result = report(lines + [update(2, -12.0), refresh(3)])
    row = snapshots(result)[0]
    assert row['status'] == 'unverified_attribute_parameters'
    assert row['unverified_parameters'] == ['additional_def_decrease']
    assert result['buff_events'][0]['creation_observations'][0]['buffTemplateId'] == {'type': 'Buff', 'int_id': 1984}


def test_missing_typed_creator_leaves_creation_bb_unverified():
    lines = [line for line in creation() if ' BUFF_ORIGIN ' not in line]
    result = report(lines + [update(2, -12.0), refresh(3)])
    row = snapshots(result)[0]
    assert row['status'] == 'unverified_attribute_parameters'
    assert row['unverified_parameters'] == ['additional_def_decrease']
    assert row['final_modifiers_verified'] is False


@pytest.fixture
def isolated_action_roots(monkeypatch):
    roots = dict(parser._load_current_buff_roots())
    parent_id = 'buff_common_natural_natural_corrupt_triggered'
    locations = {}
    for template, wanted in ((BUFF, (5, 10)), (parent_id, (7,))):
        roots[template] = copy.deepcopy(roots[template])
        pending = [(None, None, roots[template])]
        while pending:
            container, key, node = pending.pop()
            if isinstance(node, dict):
                action_id = node.get('serverActionIndex')
                if type(action_id) is int and action_id in wanted:
                    assert action_id not in locations
                    locations[action_id] = (template, container, key, node)
                pending.extend((node, child_key, child) for child_key, child in node.items())
            elif isinstance(node, list):
                pending.extend((node, index, child) for index, child in enumerate(node))
    assert set(locations) == {5, 7, 10}
    monkeypatch.setattr(parser, '_load_current_buff_roots', lambda: roots)
    return roots, locations


def test_repeated_dynamic_and_creator_lookups_visit_each_exact_root_once_per_parse(isolated_action_roots):
    roots, locations = isolated_action_roots
    visits = {}

    class CountedRoot(dict):
        def values(self):
            if sys._getframe(1).f_code is parser.parse_raw_battle_log_text.__code__:
                visits[id(self)] = visits.get(id(self), 0) + 1
            return super().values()

    for template in (BUFF, locations[7][0]):
        roots[template] = CountedRoot(roots[template])
    lines = creation() + [update(2, -12.0), refresh(3),
                         update(4, -18.0, stamp='10:00:01.100'),
                         refresh(5, stamp='10:00:01.200')]
    first = report(lines)
    assert [row['bb_values']['def_decrease'] for row in snapshots(first)] == [-12.0, -18.0]
    assert visits == {id(root): 1 for root in roots.values() if isinstance(root, CountedRoot)}
    # Even the same objects must be indexed anew in a subsequent parse.
    assert report(lines) == first
    assert visits == {id(root): 2 for root in roots.values() if isinstance(root, CountedRoot)}


@pytest.mark.parametrize('action_id', [5, 10, 7])
@pytest.mark.parametrize('change', ['duplicate_other_type', 'disabled_action', 'disabled_ancestor', 'wrong_type', 'non_integer'])
def test_action_index_preserves_duplicate_type_and_enabled_guards(isolated_action_roots, action_id, change):
    roots, locations = isolated_action_roots
    template, container, key, action = locations[action_id]
    if change == 'duplicate_other_type':
        roots[template]['duplicate_fixture'] = {'serverActionIndex': action_id, 'isEnable': True,
                                                '$type': 'Beyond.Gameplay.Core.Unrelated.Data'}
    elif change == 'disabled_action':
        action['isEnable'] = False
    elif change == 'disabled_ancestor':
        container[key] = {'serverActionIndex': 999999, 'isEnable': False, 'child': action}
    elif change == 'wrong_type':
        action['$type'] = 'Beyond.Gameplay.Core.Unrelated.Data'
    elif change == 'non_integer':
        action['serverActionIndex'] = float(action_id)
    result = report(creation() + [update(2, -12.0), refresh(3)])
    if action_id == 5:
        assert result['dynamic_bb_events'][0]['state_error'] == 'dynamic_action_not_in_current_template'
    elif action_id == 10:
        assert result['buff_attribute_refresh_events'][0]['state_error'] == 'dynamic_action_not_in_current_template'
        assert snapshots(result) == []
    else:
        assert snapshots(result)[0]['status'] == 'unverified_attribute_parameters'
        assert 'additional_def_decrease' in snapshots(result)[0]['unverified_parameters']


def test_action_index_cannot_reuse_an_earlier_parse_or_replacement_root(isolated_action_roots):
    roots, locations = isolated_action_roots
    lines = creation() + [update(2, -12.0), refresh(3)]
    assert snapshots(report(lines))[0]['status'] == 'attribute_parameters_observed'
    # In-place fixture changes also must not inherit a previous parse's index.
    action = locations[5][3]
    action['isEnable'] = False
    assert report(lines)['dynamic_bb_events'][0]['state_error'] == 'dynamic_action_not_in_current_template'
    action['isEnable'] = True
    root_before_replacement = roots[BUFF]
    roots[BUFF] = copy.deepcopy(root_before_replacement)
    assert roots[BUFF] is not root_before_replacement
    assert snapshots(report(lines))[0]['status'] == 'attribute_parameters_observed'
    roots[BUFF]['duplicate_fixture'] = copy.deepcopy(action)
    assert report(lines)['dynamic_bb_events'][0]['state_error'] == 'dynamic_action_not_in_current_template'
