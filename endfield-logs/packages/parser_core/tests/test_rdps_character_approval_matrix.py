"""Character approval contracts using explicit synthetic packet observations.

Keep the general spell-vulnerability approval out of these cases so it cannot
conceal a broken Tangtang parent-to-main approval mapping.
"""
from copy import deepcopy

import pytest

from parser_core import battle_log_parser as parser


PARENT = 'buff_chr_0027_tangtang_normalskill_spellvulnerable'
MAIN = 'buff_common_affixes_vulnerable_spell'


@pytest.fixture
def record(monkeypatch):
    registry = deepcopy(parser._load_rdps_semantics_registry())
    registry['verified_effects'] = {PARENT: registry['verified_effects'][PARENT]}
    registry['verified_prefixes'] = []
    monkeypatch.setattr(parser, '_load_rdps_semantics_registry', lambda: registry)
    return {'event_key': MAIN, 'raw_event_key': MAIN, 'uid': '7001',
            'source_character_key': 'chr_0027_tangtang', 'source_actor_id': '2001',
            'target_character_key': 'eny_0051_rodin', 'target_actor_id': '3001',
            'bb_values': {'rate': .2}, 'origin_observations': [{
                'schemaVersion': 1, '_boundCreateRecord': True, 'buffUid': '7001',
                'buffTemplate': MAIN, 'sourceActorId': '2001', 'targetActorId': '3001',
                'environmentType': 'Buff', 'ownerType': '1', 'environmentInstId': '7000',
                'templateStrId': PARENT, 'templateIntId': None, 'actionId': 1,
                'operationSeq': 1, 'triggerType': 0}]}


def allocate(record, **changes):
    effects = parser._collect_zone_effects(record)
    window = {**record, 'start_ts_ms': 0, 'end_ts_ms': 1000, 'start_line_no': 1,
              'zone_effects': effects, 'enable_observations': [
                  {'operationSeq': 1, 'ts_ms': 0, 'line_no': 1, 'enabled': True}]}
    hit = {'character_key': 'chr_0016_laevat', 'source_actor_id': '1001',
           'target_enemy_key': 'eny_0051_rodin', 'target_actor_id': '3001',
           'hit_value': 120, 'ts_ms': 500, 'line_no': 10,
           'damage_element': 'fire', 'damage_school': 'spell', **changes}
    return effects, parser._allocate_rdps_for_hit(hit, [window], include_debug=True)


def test_current_tangtang_action_proves_parent_input_to_actual_main_rate(record):
    root = parser._load_current_buff_roots()[PARENT]
    action = root['buffEventAction'][0]['actions'][0]['actionData'][1]
    assert action['$type'] == 'Beyond.Gameplay.Core.VulnerableAction.Data'
    assert action['isEnable'] is True and action['serverActionIndex'] == 1
    assert action['subType'] == 'Spell'
    assert action['rate']['useBlackboardKey'] is True
    assert action['rate']['blackboardKey'] == 'rate_spellvulnerable'
    assert parser._load_rdps_semantics_registry()['_keyword_buff_map']['Vulnerable']['Spell'] == MAIN
    assert parser._collect_zone_effects({**record, 'event_key': PARENT, 'raw_event_key': PARENT,
                                         'bb_values': {'rate_spellvulnerable': .2}, 'origin_observations': []}) == []


@pytest.mark.parametrize('element,credit', [('physical', 0), ('fire', 20), ('pulse', 20),
                                          ('cryst', 20), ('natural', 20), ('ether', 0)])
def test_tangtang_approval_allocates_actual_main_once_for_only_spell_elements(record, element, credit):
    effects, result = allocate(record, damage_element=element,
                               damage_school='physical' if element == 'physical' else 'spell')
    assert all(effect['external_rdps_allowed'] and effect['rdps_whitelist_keys'] == [PARENT] for effect in effects)
    assert result['contributions_map'].get('chr_0027_tangtang', 0) == pytest.approx(credit)
    assert sum(result['contributions_map'].values()) == pytest.approx(120)


@pytest.mark.parametrize('mutation', ['missing_origin', 'wrong_uid', 'wrong_owner', 'wrong_action', 'missing_rate'])
def test_tangtang_parent_mapping_requires_bound_identity_and_actual_rate(record, mutation):
    if mutation == 'missing_origin':
        record['origin_observations'] = []
    elif mutation == 'missing_rate':
        record['bb_values'] = {}
    else:
        field = {'wrong_uid': 'buffUid', 'wrong_owner': 'targetActorId', 'wrong_action': 'actionId'}[mutation]
        record['origin_observations'][0][field] = 999 if field == 'actionId' else '9999'
    _, result = allocate(record)
    assert result['contributions_map'] == {'chr_0016_laevat': 120}


@pytest.mark.parametrize('changes', [{'target_actor_id': '9999'}, {'ts_ms': 1001}])
def test_tangtang_observed_buff_cannot_apply_outside_owner_or_lifetime(record, changes):
    _, result = allocate(record, **changes)
    assert result['contributions_map'] == {'chr_0016_laevat': 120}
