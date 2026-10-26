"""Synthetic packet contracts for current Antal and shared Ardelia corrosion."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from parser_core import battle_log_parser as parser


APPROVAL = 'buff_chr_0023_antal_normal_fragile'
PARENT = 'buff_chr_0023_antal_tageffect'
MARKER = 'buff_chr_0023_antal_talent_1_combotrigger'
ARD = 'buff_chr_0025_ardelia_dynamic_def_decrease'
CORROSION = 'buff_common_natural_natural_corrupt_do'
SESSION = 'synthetic-keyword-session'
UID = '9223372036854776111'
OWNER = '9223372036854776112'
SOURCE = '9223372036854776113'


def main_record(element='fire', rate=0.2):
    subtype, action = ('Fire', 1) if element == 'fire' else ('Pulse', 0)
    key = parser._load_rdps_semantics_registry()['_keyword_buff_map']['Vulnerable'][subtype]
    return {'event_key': key, 'raw_event_key': key, 'uid': UID,
            'source_character_key': 'chr_0023_antal', 'source_actor_id': SOURCE,
            'target_character_key': 'eny_0051_rodin', 'target_actor_id': OWNER,
            'bb_values': {'rate': rate},
            'origin_observations': [{
                'schemaVersion': 1, '_boundCreateRecord': True, 'buffUid': UID, 'buffTemplate': key,
                'sourceActorId': SOURCE, 'targetActorId': OWNER, 'environmentType': 'Buff',
                'ownerType': '1', 'environmentInstId': '9223372036854776114',
                'templateIntId': 912, 'templateStrId': None, 'actionId': action,
                'operationSeq': 10, 'triggerType': 0}]}


def create_lines(element='fire'):
    r = main_record(element)
    origin = {key: value for key, value in r['origin_observations'][0].items() if not key.startswith('_')}
    anchor = {'schemaVersion': 1, 'buffUid': UID, 'buffTemplate': r['event_key'],
              'sourceActorId': SOURCE, 'targetActorId': OWNER, 'sessionId': SESSION,
              'operationSeq': 10, 'operationOwnerId': OWNER, 'operationGeneration': 2,
              'sourceEvent': 'BattleOpTriggerAction', 'generationScope': 'operation_owner',
              'buffId': r['event_key'], 'buffNumId': None, 'buffTemplateId': None,
              'isNewInstance': None, 'enhanceMultiplier': None,
              'creationActionType': 'BattleActionCreateBuff', 'creationActionRuntimeCase': 'create_buff_action',
              'assignedItems': [{'target_key': 'rate', 'numeric_value': 0.2, 'use_direct_value': True}]}
    return [
        f'[10:00:00.000] BUFF_START #1 id="{r["event_key"]}" uid={UID} owner=eny_0051_rodin src=chr_0023_antal ownerId={OWNER} srcId={SOURCE} dur=20 enh=unknown',
        f'[10:00:00.000] BB[{UID}]: rate=0.2 duration=20',
        '[10:00:00.000] BUFF_CREATE_OBSERVED ' + json.dumps(anchor),
        '[10:00:00.000] BUFF_ORIGIN ' + json.dumps(origin),
        '[10:00:00.000] BUFF_ENABLE ' + json.dumps({'schemaVersion': 1, 'buffUid': UID,
            'ownerId': OWNER, 'enabled': True, 'operationSeq': 11})]


def update(seq=30, rate=0.3, **changes):
    matches = [int(key) for key, value in parser._load_num_id_str_buff_map().items() if value == MARKER]
    assert len(matches) == 1
    data = {'schemaVersion': 1, 'sessionId': SESSION, 'ownerId': OWNER, 'generation': 2,
            'operationSeq': seq, 'clientTickMs': str(seq * 10), 'buffUid': UID,
            'addedBuffNumId': matches[0], 'modify': [{'configIndex': 0, 'rateAfter': rate}],
            'operationPayloadCase': 'trigger_keyword_op_data'}
    data.update(changes)
    return '[10:00:01.000] BUFF_KEYWORD_UPDATE ' + json.dumps(data)


def report(extra=(), element='fire'):
    return parser.parse_raw_battle_log_text('\n'.join(create_lines(element) + list(extra) + [
        f'[10:00:10.000] HP_V2 #1 hit=120 raw=120 packetFinalValue=120 cum=120 packetSeq=100 packetSessionId="{SESSION}" pHP=100 eHP=1000 skill="fixture" src=chr_0016_laevat tgt=eny_0051_rodin atk=chr_0016_laevat atkId=1001 tgtId={OWNER}']), include_rdps_debug=True)


def observed_rate(result, seq, *, session=SESSION, element='fire'):
    record = next(row for row in result['buff_events'] if row['uid'] == UID)
    effects = parser._window_effects_at_ts(record, 36_001_000, line_no=999,
                                           packet_seq_id=seq, packet_session_id=session)
    return [effect['rate'] for effect in effects if effect['element'] == element]


def test_each_parse_rebuilds_keyword_root_lookup_after_in_place_or_replacement_changes(monkeypatch):
    roots = dict(parser._load_current_buff_roots())
    roots[PARENT] = deepcopy(roots[PARENT])
    monkeypatch.setattr(parser, '_load_current_buff_roots', lambda: roots)
    pending = [roots[PARENT]]
    actions = []
    while pending:
        node = pending.pop()
        if isinstance(node, dict):
            if (node.get('$type') == 'Beyond.Gameplay.Core.VulnerableAction.Data'
                    and type(node.get('serverActionIndex')) is int and node['serverActionIndex'] == 1):
                actions.append(node)
            pending.extend(node.values())
        elif isinstance(node, list):
            pending.extend(node)
    assert len(actions) == 1
    first = report([update()])
    first_before_hit_lookup = deepcopy(first)
    assert observed_rate(first, 30) == [0.3]
    actions[0]['isEnable'] = False
    # Rejecting the new keyword operation preserves the separately observed
    # initial main-Buff value; it must not reuse the earlier accepted 0.3.
    assert observed_rate(report([update()]), 30) == [0.2]
    actions[0]['isEnable'] = True
    assert report([update()]) == first_before_hit_lookup
    old_root = roots[PARENT]
    roots[PARENT] = deepcopy(old_root)
    roots[PARENT].update({'serverActionIndex': 999999, 'isEnable': False})
    assert roots[PARENT] is not old_root
    assert observed_rate(report([update()]), 30) == [0.2]


def test_current_numeric_and_alias_routes_use_real_parent_and_main_ids():
    assert parser._canonical_num_table_buff_number('912', 'chr_0023_antal') == PARENT
    assert parser._canonical_num_table_buff_number('331', 'chr_0025_ardelia') == 'buff_common_natural_natural_corrupt_triggered'
    assert parser._canonical_num_table_buff_number('1984', 'chr_0025_ardelia') == CORROSION


@pytest.mark.parametrize('element', ['pulse', 'fire'])
def test_actual_main_rate_is_approved_without_readding_parent_potential(element):
    record = main_record(element, 0.3)
    effects = parser._collect_zone_effects(record)
    assert [(row['zone'], row['element'], row['rate']) for row in effects] == [('fragile', element, 0.3)]
    assert effects[0]['rdps_whitelist_keys'] == [APPROVAL]
    parent = {**record, 'event_key': PARENT, 'raw_event_key': '912', 'origin_observations': [],
              'bb_values': {'rate': 0.2, 'potential_5_rate': 0.1, 'delay_time': 5}}
    assert parser._collect_zone_effects(parent) == []
    assert parser._extract_dynamic_effect_specs(parent) == []


@pytest.mark.parametrize('element', ['pulse', 'fire'])
def test_packet_new_rate_applies_only_from_its_explicit_operation(element):
    result = report([update()], element)
    assert observed_rate(result, 29, element=element) == [0.2]
    assert observed_rate(result, 30, element=element) == [0.3]
    assert observed_rate(result, 31, element=element) == [0.3]
    record = next(row for row in result['buff_events'] if row['uid'] == UID)
    assert record['bb_values']['rate'] == 0.2  # Do not rewrite earlier state.
    assert record['start_ts_ms'] == 36_000_000


def test_live_keyword_update_display_matches_per_hit_rate():
    fixture = json.loads((Path(__file__).parent / 'fixtures' / 'antal_keyword_live_20260914.json').read_text(encoding='utf-8'))
    result = parser.parse_raw_battle_log_text('\n'.join(fixture['lines']), include_rdps_debug=True)
    event = next(row for row in result['buff_events'] if row['uid'] == '2305843009213695144')
    segments = event['effect_segments']
    assert len(segments) == 2
    assert [row['rate'] for row in segments] == pytest.approx([0.1, 0.14])
    assert segments[0]['end_ts_ms'] == segments[1]['start_ts_ms'] == 46_224_481
    assert segments[0]['end_operation_seq'] == segments[1]['operation_seq'] == 1965
    assert segments[0]['end_line_no'] == segments[1]['line_no'] == 6
    assert segments[1]['session_id'] == fixture['source_session']
    assert any('10.00% -> 14.00%' in row for row in event['effect_summary'])
    for hit in result['debug_hits']:
        if hit['ts_ms'] >= event['end_ts_ms']:
            continue  # The final raw hit only supplies the excerpt's observation boundary.
        effects = parser._window_effects_at_ts(event, hit['ts_ms'], line_no=hit['line_no'],
                                              packet_seq_id=hit['packet_seq_id'],
                                              packet_session_id=hit['packet_session_id'])
        assert [effect['rate'] for effect in effects] == pytest.approx(
            [0.1 if hit['packet_seq_id'] < 1965 else 0.14])


def test_keyword_display_retains_same_timestamp_operation_boundaries_and_unknown_state():
    result = report([update(30, 0.3), update(30, 0.35), update(40, 0.4)])
    event = next(row for row in result['buff_events'] if row['uid'] == UID)
    assert [row['rate'] for row in event['effect_segments']] == [0.2, None, 0.4]
    unknown = event['effect_segments'][1]
    assert unknown['start_ts_ms'] == unknown['end_ts_ms'] == 36_001_000
    assert (unknown['operation_seq'], unknown['end_operation_seq']) == (30, 40)
    assert unknown['line_no'] < unknown['end_line_no']
    assert any('20.00% -> 未确认 -> 40.00%' in row for row in event['effect_summary'])


def test_elapsed_delay_or_marker_lifetime_does_not_invent_extra_rate():
    result = report()
    assert observed_rate(result, 99) == [0.2]


def test_repeated_packet_does_not_add_or_roll_back_the_absolute_rate():
    result = report([update(30, 0.3), update(40, 0.4), update(30, 0.3)])
    assert observed_rate(result, 35) == [0.3]
    assert observed_rate(result, 45) == [0.4]


def test_conflicting_operation_blocks_its_interval_and_later_known_value_recovers():
    result = report([update(30, 0.3), update(30, 0.35), update(40, 0.4)])
    assert observed_rate(result, 35) == []
    assert observed_rate(result, 45) == [0.4]
    assert any(row['reason'] == 'conflicting_keyword_update' for row in result['unresolved_buff_actions'])


@pytest.mark.parametrize('changes', [
    {'sessionId': 'foreign'}, {'ownerId': '9999'}, {'buffUid': '9999'},
    {'operationSeq': 9}, {'generation': True}, {'operationPayloadCase': 'trigger_action_op_data'}])
def test_foreign_or_unbound_update_does_not_poison_existing_main(changes):
    result = report([update(**changes)])
    assert observed_rate(result, 40) == [0.2]
    assert result['unresolved_buff_actions']


@pytest.mark.parametrize('changes', [
    {'addedBuffNumId': 912}, {'modify': [{'configIndex': 1, 'rateAfter': 0.3}]},
    {'modify': [{'configIndex': True, 'rateAfter': 0.3}]},
    {'modify': [{'configIndex': 0, 'rateAfter': True}]},
    {'modify': [{'configIndex': 0, 'rateAfter': -0.2}]},
    {'modify': []}, {'modify': [{'configIndex': 0, 'rateAfter': 0.3}] * 2}])
def test_unusable_current_instance_update_does_not_keep_a_guessed_final_rate(changes):
    result = report([update(**changes)])
    assert observed_rate(result, 29) == [0.2]
    assert observed_rate(result, 40) == []


def test_keyword_snapshot_cannot_credit_a_different_packet_session():
    result = report([update()])
    assert observed_rate(result, 40, session='foreign') == []


def test_repeated_operation_with_conflicting_config_cannot_recover_by_picking_later_row():
    result = report([update(modify=[{'configIndex': 1, 'rateAfter': 0.3}]), update()])
    assert observed_rate(result, 40) == []
    assert any(row['reason'] == 'conflicting_keyword_update' for row in result['unresolved_buff_actions'])


def test_provider_guard_and_bound_origin_are_required():
    record = main_record()
    record['source_character_key'] = 'chr_0035_liino'
    assert parser._rdps_registry_verified_effect_entry(record) is None
    record = main_record()
    record['origin_observations'][0]['_boundCreateRecord'] = False
    assert parser._rdps_registry_verified_effect_entry(record) is None


def test_ardelia_and_generic_approvals_reference_one_actual_corrosion_effect():
    record = {'event_key': CORROSION, 'raw_event_key': '1984', 'uid': UID,
              'source_character_key': 'chr_0025_ardelia', 'source_actor_id': SOURCE,
              'target_character_key': 'eny_0051_rodin', 'target_actor_id': OWNER,
              'bb_values': {'def_decrease': -10, 'additional_def_decrease': 0},
              'resistance_parameter_snapshots': [{'status': 'attribute_parameters_observed',
                  'operation_seq': 20, 'line_no': 2, 'ts_ms': 0, 'session_id': SESSION,
                  'bb_values': {'def_decrease': -10, 'additional_def_decrease': 0}}]}
    entry = parser._rdps_registry_verified_effect_entry(record)
    assert entry['_rdps_whitelist_keys'] == sorted([ARD, CORROSION])
    parent = {**deepcopy(record), 'event_key': 'buff_common_natural_natural_corrupt_triggered', 'raw_event_key': '331'}
    assert parser._collect_zone_effects(parent) == []
    assert parser._extract_dynamic_effect_specs(parent) == []
    window = {**record, 'start_ts_ms': 0, 'end_ts_ms': 1000,
              'zone_effects': [], 'dynamic_effects': parser._extract_dynamic_effect_specs(record)}
    hit = {'character_key': 'chr_0016_laevat', 'source_actor_id': '1001',
           'target_enemy_key': 'eny_0051_rodin', 'target_actor_id': OWNER,
           'ts_ms': 500, 'packet_seq_id': 30, 'packet_session_id': SESSION,
           'hit_value': 110, 'damage_element': 'fire', 'damage_school': 'spell',
           'packet_attr_details': {'defender': ['98:-10']}}
    result = parser._allocate_rdps_for_hit(hit, [window], include_debug=True)
    assert result['contributions_map'] == pytest.approx({'chr_0016_laevat': 100, 'chr_0025_ardelia': 10})
    contributors = [row for zone in result['zones'] for row in zone['contributors'] if row['scope'] == 'external']
    assert len(contributors) == 1
    assert contributors[0]['rdps_whitelist_keys'] == sorted([ARD, CORROSION])
    record['source_character_key'] = 'chr_0004_pelica'
    assert parser._rdps_registry_verified_effect_entry(record)['_rdps_whitelist_keys'] == [CORROSION]
