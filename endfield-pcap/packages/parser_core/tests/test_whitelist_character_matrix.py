"""Isolated contracts for all current character/common exact approvals.

These fixtures supply final observed rates and exact origins, not inferred
skill levels. Text and current typed paths are documented in the matching
per-entry audit. Real packet corrosion coverage remains a separate test.
"""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from parser_core import battle_log_parser as parser


CASES = json.loads((Path(__file__).with_name('fixtures') / 'whitelist_character_matrix.json').read_text(encoding='utf8'))['rows']
ELEMENTS = ('physical', 'fire', 'pulse', 'cryst', 'natural', 'ether')
SPELL = {'fire', 'pulse', 'cryst', 'natural'}
MAIN_ELEMENTS = {
    'buff_common_affixes_enhance_crystal': {'cryst'},
    'buff_common_affixes_enhance_natural': {'natural'},
    'buff_common_affixes_enhance_pulse': {'pulse'},
    'buff_common_affixes_enhance_fire': {'fire'},
    'buff_common_affixes_enhance_spell': SPELL,
    'buff_common_affixes_vulnerable_crystal': {'cryst'},
    'buff_common_affixes_vulnerable_natural': {'natural'},
    'buff_common_affixes_vulnerable_pulse': {'pulse'},
    'buff_common_affixes_vulnerable_fire': {'fire'},
    'buff_common_affixes_vulnerable_physical': {'physical'},
    'buff_common_affixes_vulnerable_spell': SPELL,
}
ADMIN = 'buff_chr_0003_endminf_talent_0'
ANTAL = 'buff_chr_0023_antal_normal_fragile'
TYPHOEA = 'buff_chr_0034_typhoea_combo_enemy_debuff'


@pytest.mark.parametrize('case', CASES, ids=[row['key'] for row in CASES])
def test_each_character_approval_has_effective_allocation_and_exclusion_contract(monkeypatch, case):
    key = case['key']
    registry = deepcopy(parser._load_rdps_semantics_registry())
    entry = registry['verified_effects'][key]
    assert entry['effects'] == case['declaredEffects']
    registry['verified_effects'] = {key: entry}
    registry['verified_prefixes'] = []
    monkeypatch.setattr(parser, '_load_rdps_semantics_registry', lambda: registry)
    positive_cases = 0
    for original in case['records']:
        record = deepcopy(original)
        if case['dynamic'] and key != ANTAL:
            record['attribute_refresh_snapshots'] = [{
                'operation_seq': 2, 'line_no': 20, 'ts_ms': 200, 'session_id': 'fixture',
                'status': 'attribute_parameters_observed',
                'bb_values': {'def_decrease': -10., 'additional_def_decrease': 0.}}]
            specs = parser._extract_dynamic_effect_specs(record)
            # Current roots modify the five ordinary resistance attributes;
            # the broad approval does not invent an EtherResistance modifier.
            assert {spec['element'] for spec in specs} == set(ELEMENTS) - {'ether'}
            assert all(spec['external_rdps_allowed'] and spec['rdps_whitelist_keys'] == [key] for spec in specs)
            window = {**record, 'start_ts_ms': 100, 'start_line_no': 10, 'end_ts_ms': 1000,
                      'end_line_no': 100, 'zone_effects': [], 'dynamic_effects': specs}
            base_hit = {'character_key': 'chr_0016_laevat', 'source_actor_id': '1001',
                        'target_enemy_key': 'eny_0051_rodin', 'target_actor_id': '3001',
                        'hit_value': 130, 'ts_ms': 500, 'line_no': 50, 'packet_seq_id': 5,
                        'packet_session_id': 'fixture', 'damage_element': 'fire', 'damage_school': 'spell',
                        'packet_attr_details': {'attacker': [], 'defender': ['98:-30']}}
            for changes, expected in [({},10), ({'target_actor_id':'9999'},0), ({'ts_ms':1001},0),
                                      ({'packet_seq_id':1},0), ({'packet_session_id':'other'},0),
                                      ({'packet_attr_details':{'attacker':[],'defender':[]}},0)]:
                result = parser._allocate_rdps_for_hit({**base_hit, **changes}, [deepcopy(window)], include_debug=True)
                credit = sum(value for who,value in result['contributions_map'].items() if who != base_hit['character_key'])
                assert credit == pytest.approx(expected)
                assert sum(result['contributions_map'].values()) == pytest.approx(130)
                positive_cases += expected > 0
            continue

        enemy_side = all(effect['zone'] in {'fragile', 'vuln_taken', 'res'} for effect in case['declaredEffects']) and key != ADMIN
        owner = record['target_actor_id']
        record['target_character_key'] = 'eny_0051_rodin' if enemy_side else 'chr_0016_laevat'
        effects = parser._collect_zone_effects(record)
        assert effects
        assert all(effect['external_rdps_allowed'] and effect['rdps_whitelist_keys'] == [key] for effect in effects)
        assert {effect['zone'] for effect in effects} == {effect['zone'] for effect in case['declaredEffects']}
        window = {**record, 'start_ts_ms':0, 'end_ts_ms':1000,'start_line_no':1,'zone_effects':effects,
                  'enable_observations':[{'operationSeq':1,'ts_ms':0,'line_no':1,'enabled':True}]}
        rate = record['bb_values'].get('rate', .2)
        approved = {effect['element'] for effect in case['declaredEffects'] if effect['zone'] != 'arts_strength'}
        expected_elements = set(ELEMENTS) if 'all' in approved else (approved - {'spell'}) | (SPELL if 'spell' in approved else set())
        if record.get('origin_observations'):
            expected_elements &= MAIN_ELEMENTS[record['event_key']]
        if key == TYPHOEA:
            expected_elements = {'natural'}
        for element in ELEMENTS:
            attacker = record['target_character_key'] if not enemy_side else 'chr_0016_laevat'
            target_actor = owner if enemy_side else '3001'
            hit = {'character_key':attacker,'source_actor_id':'1001' if enemy_side else owner,
                   'target_enemy_key':'eny_0051_rodin','target_actor_id':target_actor,
                   'hit_value':100*(1+rate),'ts_ms':500,'line_no':10,'damage_element':element,
                   'damage_school':'physical' if element=='physical' else 'spell',
                   'target_buff_state_complete':True,
                   'damage_decorate_mask':33554432 if key==TYPHOEA and element=='natural' else 0x100}
            states = [{'uid':'7002','event_key':'buff_common_originum_frozen','target_enemy_key':'eny_0051_rodin',
                       'target_actor_id':target_actor,'ts_ms':0,'end_ts_ms':1000}] if key==ADMIN else []
            result = parser._allocate_rdps_for_hit(deepcopy(hit),[deepcopy(window)],include_debug=True,condition_buff_records=states)
            credit = sum(value for who,value in result['contributions_map'].items() if who != attacker)
            expected = 100*rate if element in expected_elements else 0
            assert credit == pytest.approx(expected)
            assert sum(result['contributions_map'].values()) == pytest.approx(hit['hit_value'])
            if key==ADMIN and element=='physical':
                own_window = {**deepcopy(window), 'source_character_key':attacker, 'source_actor_id':hit['source_actor_id']}
                own = parser._allocate_rdps_for_hit(deepcopy(hit),[own_window],include_debug=True,condition_buff_records=states)
                assert sum(zone.get('self_rate',0) for zone in own['zones']) == pytest.approx(.2)
                assert own['contributions_map'] == pytest.approx({attacker:hit['hit_value']})
                positive_cases += 1
                missing = parser._allocate_rdps_for_hit(hit,[deepcopy(window)],include_debug=True,condition_buff_records=[])
                assert not missing['zones']
            if expected > 0:
                positive_cases += 1
                for changes in [{'ts_ms':1001},{'target_actor_id' if enemy_side else 'source_actor_id':'9999'}]:
                    other = parser._allocate_rdps_for_hit({**hit,**changes},[deepcopy(window)],include_debug=True,condition_buff_records=states)
                    assert other['contributions_map'] == pytest.approx({attacker:hit['hit_value']})
        if record.get('origin_observations'):
            unbound = deepcopy(record)
            if record['event_key'] in (entry.get('aliases') or []):
                # Avywen also has an explicit direct-main alias; that path
                # retains both source-character and ultimate-family guards.
                unbound['source_skill_family_key'] = 'unrelated_explicit_family'
            else:
                unbound['origin_observations'][0]['buffUid'] = '9999'
            unbound_effects = parser._collect_zone_effects(unbound)
            assert not any(effect['external_rdps_allowed'] for effect in unbound_effects)
        if key == 'buff_chr_0029_pograni_talent1':
            anomaly_hit = {**hit, 'hit_value':156., 'damage_element':'physical',
                           'damage_school':'physical', 'damage_decorate_mask':0x4000}
            anomaly = parser._allocate_rdps_for_hit(anomaly_hit,[deepcopy(window)],include_debug=True)
            assert anomaly['contributions_map'] == pytest.approx({'chr_0016_laevat':100.,'chr_0029_pograni':56.})
            assert {zone['zone'] for zone in anomaly['zones']} == {'atk','arts_strength'}
        missing = {**deepcopy(record),'bb_values':{}}
        assert not parser._collect_zone_effects(missing)
    assert positive_cases > 0


def test_matrix_covers_every_current_character_and_common_exact_approval():
    registry = parser._load_rdps_semantics_registry()['verified_effects']
    expected = {key for key in registry if not key.startswith(('buff_wpn_','buff_equipsuit_'))}
    assert {case['key'] for case in CASES} == expected
    assert len(CASES) == len(expected)


@pytest.mark.parametrize('change', ['missing_origin', 'unbound_create', 'wrong_source', 'wrong_uid', 'disabled_physical'])
def test_jsspsi_new_parent_approval_requires_exact_enabled_main_origin(monkeypatch, change):
    key = 'buff_chr_0036_jsspsi_atk_buff_2'
    case = next(row for row in CASES if row['key'] == key)
    registry = deepcopy(parser._load_rdps_semantics_registry())
    registry['verified_effects'] = {key: registry['verified_effects'][key]}
    registry['verified_prefixes'] = []
    monkeypatch.setattr(parser, '_load_rdps_semantics_registry', lambda: registry)
    record = deepcopy(case['records'][0])
    assert parser._collect_zone_effects(record)
    if change == 'missing_origin':
        record['origin_observations'] = []
    elif change == 'unbound_create':
        record['origin_observations'][0]['_boundCreateRecord'] = False
    elif change == 'wrong_source':
        record['source_character_key'] = 'chr_0023_antal'
    elif change == 'wrong_uid':
        record['origin_observations'][0]['buffUid'] = '9999'
    else:
        main = 'buff_common_affixes_enhance_physical'
        record['event_key'] = record['raw_event_key'] = main
        record['origin_observations'][0].update(buffTemplate=main, actionId=0)
    assert not any(effect.get('external_rdps_allowed') for effect in parser._collect_zone_effects(record))
