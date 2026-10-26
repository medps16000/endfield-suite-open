from copy import deepcopy

import pytest

from parser_core import battle_log_parser as parser


@pytest.mark.parametrize('source,expected', [
    ('chr_0023_antal', {'chr_0004_pelica': 175}),
    ('chr_0004_pelica', {'chr_0004_pelica': 175}),
])
def test_typed_effect_outside_whitelist_cannot_transfer_credit(monkeypatch, source, expected):
    monkeypatch.setattr(parser, '_load_rdps_semantics_registry', lambda: {})
    monkeypatch.setattr(parser, '_rdps_registry_known_non_rdps_entry', lambda record: None)
    monkeypatch.setattr(parser, '_collect_semantic_condition_zone_effects', lambda record, root_lookup_cache=None: [
        {'zone': 'atk', 'element': 'all', 'rate': 0.5, 'bb_key': 'rate'}])
    record = {'event_key': 'buff_not_approved', 'source_character_key': source,
              'target_character_key': 'chr_0004_pelica', 'bb_values': {'rate': 0.5}}
    effects = parser._collect_zone_effects(record)
    assert effects[0]['external_rdps_allowed'] is False
    window = {**record, 'uid': 'one', 'start_ts_ms': 0, 'end_ts_ms': 1000, 'zone_effects': effects}
    hit = {'character_key': 'chr_0004_pelica', 'target_enemy_key': 'enemy',
           'hit_value': 175, 'ts_ms': 100, 'damage_element': 'fire', 'damage_school': 'spell'}
    allocation = parser._allocate_rdps_for_hit(hit, [window], include_debug=True)
    assert allocation['contributions_map'] == expected
    if source != hit['character_key']:
        assert allocation['ignored_effects'][0]['reason'] == 'external_effect_not_whitelisted'
        assert hit['rdps_unresolved_whitelist_effect'] is True
    else:
        assert allocation['zones'][0]['self_rate'] == 0.5
        assert not hit.get('rdps_unresolved_whitelist_effect')


@pytest.mark.parametrize('elements,hit_element,credit', [
    (['fire'], 'physical', 0), (['fire'], 'fire', 20),
    (['fire', 'physical'], 'physical', 20), (['fire', 'physical'], 'fire', 20),
    (['spell'], 'physical', 0), (['spell'], 'natural', 20),
])
def test_typed_branch_keeps_whitelist_element_union_without_duplicate_credit(monkeypatch, elements, hit_element, credit):
    registry = {'verified_effects': {'buff_approved': {'effects': [
        {'zone': 'dmg_inc', 'element': element, 'bb_key': 'rate'} for element in elements]}}}
    monkeypatch.setattr(parser, '_load_rdps_semantics_registry', lambda: registry)
    monkeypatch.setattr(parser, '_collect_semantic_condition_zone_effects', lambda record, root_lookup_cache=None: [
        {'zone': 'dmg_inc', 'element': 'all', 'rate': 0.2, 'bb_key': 'rate'}])
    record = {'event_key': 'buff_approved', 'source_character_key': 'chr_0023_antal',
              'target_character_key': 'chr_0004_pelica', 'bb_values': {'rate': 0.2}}
    effects = parser._collect_zone_effects(record)
    assert len(effects) == 1
    assert effects[0]['rdps_whitelist_keys'] == ['buff_approved']
    window = {**record, 'uid': 'one', 'start_ts_ms': 0, 'end_ts_ms': 1000, 'zone_effects': effects}
    hit = {'character_key': 'chr_0004_pelica', 'target_enemy_key': 'enemy', 'hit_value': 120,
           'ts_ms': 100, 'damage_element': hit_element,
           'damage_school': 'physical' if hit_element == 'physical' else 'spell'}
    allocation = parser._allocate_rdps_for_hit(hit, [window], include_debug=True)
    assert allocation['contributions_map'].get('chr_0023_antal', 0) == pytest.approx(credit)
    assert sum(allocation['contributions_map'].values()) == pytest.approx(120)


@pytest.mark.parametrize('mask,credit', [(64, 0), (128, 0), (192, 20)])
def test_whitelist_condition_and_typed_condition_both_apply(monkeypatch, mask, credit):
    registry = {'verified_effects': {'buff_approved': {'effects': [
        {'zone': 'atk', 'element': 'all', 'bb_key': 'rate',
         'condition': {'type': 'damage_decorate_mask', 'mask': 128, 'check': 'HasAll'}}]}}}
    monkeypatch.setattr(parser, '_load_rdps_semantics_registry', lambda: registry)
    monkeypatch.setattr(parser, '_collect_semantic_condition_zone_effects', lambda record, root_lookup_cache=None: [
        {'zone': 'atk', 'element': 'all', 'rate': 0.2, 'bb_key': 'rate',
         'condition': {'type': 'damage_decorate_mask', 'mask': 64, 'check': 'HasAll'}}])
    record = {'event_key': 'buff_approved', 'source_character_key': 'chr_0023_antal',
              'target_character_key': 'chr_0004_pelica', 'bb_values': {'rate': 0.2}}
    window = {**record, 'uid': 'one', 'start_ts_ms': 0, 'end_ts_ms': 1000,
              'zone_effects': parser._collect_zone_effects(record)}
    hit = {'character_key': 'chr_0004_pelica', 'target_enemy_key': 'enemy', 'hit_value': 120,
           'ts_ms': 100, 'damage_element': 'fire', 'damage_school': 'spell', 'damage_decorate_mask': mask}
    values = parser._allocate_rdps_for_hit(hit, [window])['contributions_map']
    assert values.get('chr_0023_antal', 0) == pytest.approx(credit)


@pytest.mark.parametrize('zone,operand,allowed', [('atk', 'base', True), ('dmg_inc', 'base', False), ('atk', 'different_base', False)])
def test_packet_dynamic_hint_cannot_expand_whitelist_zone(monkeypatch, zone, operand, allowed):
    registry = {'verified_effects': {'buff_dynamic': {'dynamic': True,
        'dynamic_effects': [{'zone': 'atk', 'element': 'all', 'base_bb_key': 'base'}]}}}
    monkeypatch.setattr(parser, '_load_rdps_semantics_registry', lambda: registry)
    record = {'event_key': 'buff_dynamic', 'bb_values': {operand: 0.2},
              'packet_mapping': {'dynamic_effects': [{'zone': zone, 'element': 'all', 'base_bb_key': operand}]}}
    effects = parser._extract_dynamic_effect_specs(record)
    if not allowed:
        assert effects == []
        assert record['rdps_whitelist_rejection'] in ('dynamic_effect_not_declared', 'dynamic_operand_not_declared')
    else:
        evaluated = parser._window_effects_at_ts({'dynamic_effects': effects}, 100)
        assert evaluated[0]['rdps_whitelist_keys'] == ['buff_dynamic']
        assert evaluated[0]['external_rdps_allowed'] is True


def test_equal_numeric_effects_do_not_merge_away_whitelist_status():
    effect = {'zone': 'atk', 'element': 'all', 'rate': 0.2,
              'external_rdps_allowed': True, 'rdps_whitelist_keys': ['buff_approved']}
    unapproved = {**deepcopy(effect), 'external_rdps_allowed': False, 'rdps_whitelist_keys': []}
    assert len(parser._merge_effect_lists([effect], [unapproved])) == 2


@pytest.mark.parametrize('approved', [False, True])
def test_derived_arts_credit_requires_whitelisted_strength_source(approved):
    effect_window = {'event_key': 'buff_damage', 'source_character_key': 'chr_0023_antal', 'start_ts_ms': 100}
    effect = {'zone': 'vuln_taken', 'element': 'all', 'rate': 0.2, 'arts_strength_provenance': True}
    strength_window = {'target_character_key': 'chr_0023_antal', 'source_character_key': 'chr_0004_pelica',
                       'start_ts_ms': 0, 'end_ts_ms': 1000, 'zone_effects': [
                           {'zone': 'arts_strength', 'element': 'all', 'rate': 100,
                            'external_rdps_allowed': approved, 'rdps_whitelist_keys': ['buff_strength'] if approved else []}]}
    rows = parser._arts_strength_effect_contributors(effect_window=effect_window, effect=effect, buff_windows=[strength_window])
    if approved:
        assert len(rows) == 2
        assert rows[1][0] == 'chr_0004_pelica'
        assert rows[1][2]['arts_strength_whitelist_keys'] == ['buff_strength']
    else:
        assert rows == [('chr_0023_antal', 0.2, None)]
