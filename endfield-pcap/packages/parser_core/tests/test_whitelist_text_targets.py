"""Current text limits and exact current child-field forwarding."""
from copy import deepcopy

import pytest

from parser_core import battle_log_parser as parser


@pytest.mark.parametrize('key', [
    'buff_equipsuit_fninflict_01_firedamageadd',
    'buff_equipsuit_fninflict_01_poisedamageadd',
    'buff_wpn_sword_0006_valid',
    'buff_wpn_pistol_0012_natural',
    'buff_wpn_funnel_0017_layer',
])
def test_wearer_only_text_never_authorizes_external_transfer(key):
    entry = parser._load_rdps_semantics_registry()['verified_effects'][key]
    values = {field: .2 for field in parser._rdps_registry_effect_bb_keys(entry)}
    record = {'event_key': key, 'raw_event_key': key, 'source_character_key': 'chr_0016_laevat',
              'target_character_key': 'chr_0016_laevat', 'source_actor_id': '10', 'target_actor_id': '10',
              'bb_values': values}
    own = parser._collect_zone_effects(deepcopy(record))
    assert own and all(effect['external_rdps_allowed'] for effect in own)
    for target_key, target_id in [('chr_0004_pelica', '20'), ('chr_0016_laevat', '20')]:
        other = {**record, 'target_character_key': target_key, 'target_actor_id': target_id}
        assert parser._rdps_registry_verified_effect_entry(other) is None
        assert all(effect.get('external_rdps_allowed') is False for effect in parser._collect_zone_effects(other))


def link_record():
    template = 'buff_wpn_funnel_0018_layer'
    return {'uid': '30', 'event_key': template, 'raw_event_key': '3605',
            'source_character_key': 'chr_0004_pelica', 'source_actor_id': '10',
            'target_character_key': 'chr_0016_laevat', 'target_actor_id': '20',
            'bb_values': {'atk_up': .024, 'duration': 20, 'max_stack': 2},
            'origin_observations': [{'_boundCreateRecord': True, 'buffUid': '30', 'buffTemplate': template,
                'sourceActorId': '10', 'targetActorId': '20', 'environmentType': 'Skill', 'ownerType': '0',
                'environmentInstId': '40', 'templateStrId': 'sk_wpn_funnel_0018', 'templateIntId': None,
                'actionId': 4, 'operationSeq': 5, 'triggerType': 0}]}


@pytest.mark.parametrize('action', [4, 9, 14, 19, 23])
def test_link_current_teammate_child_uses_existing_approval_and_actual_field(action):
    record = link_record()
    record['origin_observations'][0]['actionId'] = action
    effects = parser._collect_zone_effects(record)
    assert len(effects) == 1
    assert effects[0]['bb_key'] == 'atk_up'
    assert effects[0]['rate'] == .024
    assert effects[0]['rdps_whitelist_keys'] == ['buff_wpn_funnel_0018_layer_teammates']
    hit = {'character_key': 'chr_0016_laevat', 'source_actor_id': '20', 'target_enemy_key': 'eny_0051_rodin',
           'target_actor_id': '90', 'hit_value': 102.4, 'ts_ms': 100, 'damage_element': 'fire', 'damage_school': 'spell'}
    window = {**record, 'source_character_key': record['source_character_key'], 'zone_effects': effects,
              'start_ts_ms': 0, 'end_ts_ms': 200}
    result = parser._allocate_rdps_for_hit(hit, [window])
    assert result['contributions_map'] == pytest.approx({'chr_0016_laevat': 100, 'chr_0004_pelica': 2.4})


@pytest.mark.parametrize('change', ['no_origin', 'wrong_parent', 'own_branch', 'cleanup', 'wrong_recipient', 'unbound'])
def test_link_rejects_unrelated_or_self_creation(change):
    record = link_record()
    if change == 'no_origin':
        record['origin_observations'] = []
    elif change == 'wrong_parent':
        record['origin_observations'][0]['templateStrId'] = 'sk_wpn_funnel_0017'
    elif change == 'own_branch':
        record['origin_observations'][0]['actionId'] = 3
    elif change == 'cleanup':
        record['origin_observations'][0]['triggerType'] = 2
    elif change == 'wrong_recipient':
        record['origin_observations'][0]['targetActorId'] = '99'
    else:
        record['origin_observations'][0]['_boundCreateRecord'] = False
    assert parser._rdps_registry_verified_effect_entry(record) is None
    assert all(effect.get('external_rdps_allowed') is False for effect in parser._collect_zone_effects(record))
