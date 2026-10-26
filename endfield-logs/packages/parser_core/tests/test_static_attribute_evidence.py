from copy import deepcopy

import pytest

from parser_core import battle_log_parser as parser


@pytest.fixture
def equipment(monkeypatch):
    meta = {'item_name': 'display only', 'attribute_source_sha256': 'a' * 64,
            'attribute_modifiers': [{'attrIndex': 3, 'attrType': 52, 'modifierType': 5,
                                     'modifyAttributeType': 0, 'attrValues': [0.1, 0.2, 0.3, 0.4]}]}
    monkeypatch.setattr(parser, '_load_equip_item_catalog', lambda: {'item_known': meta})
    return {'slot': 2, 'item_id': 'item_known', 'enhance_levels': [{'index': 3, 'level': 3}],
            'stats': [{'slot': 'sub3', 'name': 'unrelated healing text', 'value': 999}]}


def test_equipment_uses_exact_attribute_row_and_keeps_two_equal_pieces(equipment):
    row = {'equips': [equipment, dict(equipment, slot=3)]}
    entries = parser._derive_static_self_multiplier_entries(row)
    assert len(entries) == 2
    assert {entry['source_slot'] for entry in entries} == {2, 3}
    assert sum(entry['rate'] for entry in entries) == pytest.approx(0.8)
    assert all(entry['element'] == 'pulse' and entry['attr_type'] == 52 for entry in entries)
    assert not row['rdps_unresolved_static_multiplier_sources']


@pytest.mark.parametrize('levels', [[], [{'index': 3, 'level': 99}], [{'index': 3, 'level': -1}],
    [{'index': 3, 'level': 2}, {'index': 3, 'level': 3}]])
def test_missing_invalid_or_conflicting_enhancement_is_not_clamped(equipment, levels):
    row = {'equips': [dict(equipment, enhance_levels=levels)]}
    assert parser._derive_static_self_multiplier_entries(row) == []
    assert row['rdps_unresolved_static_multiplier_sources'][0]['reason'] == 'missing_or_conflicting_equipment_enhancement'
    gate = parser._build_rdps_preflight([], [], hits=[{
        'rdps_unresolved_static_multiplier_sources': row['rdps_unresolved_static_multiplier_sources']}],
        battle_start_ms=0, battle_end_ms=1000)
    assert gate['ok'] is False
    assert any(entry['reason'] == 'unresolved_static_multiplier_source' for entry in gate['blockers'])


def test_display_text_cannot_supply_an_unknown_item_attribute(equipment):
    row = {'equips': [dict(equipment, item_id='missing_item', stats=[{'name': '电磁伤害提升', 'value': 0.4}])]}
    assert parser._derive_static_self_multiplier_entries(row) == []
    assert row['rdps_unresolved_static_multiplier_sources']


def test_duplicate_equipment_slot_is_not_counted_twice(equipment):
    row = {'equips': [equipment, deepcopy(equipment)]}
    assert parser._derive_static_self_multiplier_entries(row) == []
    assert len(row['rdps_unresolved_static_multiplier_sources']) == 2


def test_unplaced_equipment_survives_snapshot_and_blocks_static_evidence():
    row = parser._parse_loadout_slot_snapshot(
        '[10:00:00.000] LOADOUT slot=0 char=chr_0004_pelica unplacedEquipInstIds=[101,102]')
    assert row['unresolved_equip_inst_ids'] == ['101', '102']
    parser._derive_static_self_multiplier_entries(row)
    assert row['rdps_unresolved_static_multiplier_sources'] == [
        {'reason': 'unresolved_equipment_slot', 'equip_inst_ids': ['101', '102']}]


def test_final_attribute_snapshot_already_contains_static_equipment_bonus(equipment):
    row = {'equips': [equipment]}
    entries = parser._derive_static_self_multiplier_entries(row)
    hit = {'character_key': 'chr_0004_pelica', 'target_enemy_key': 'eny_0051_rodin',
           'ts_ms': 500, 'hit_value': 180, 'skill_key': 'explicit', 'damage_element': 'pulse',
           'damage_school': 'spell', 'baseline': {52: 0.8}, 'static_multiplier_entries': entries}
    window = {'uid': 'x', 'event_key': 'explicit', 'source_character_key': 'chr_0023_antal',
              'target_character_key': 'chr_0004_pelica', 'start_ts_ms': 0, 'end_ts_ms': 1000,
              'zone_effects': [{'zone': 'dmg_inc', 'element': 'pulse', 'attr_type': 52, 'rate': 0.4}]}
    result = parser._allocate_rdps_for_hit(hit, [window])
    assert result['contributions_map'] == pytest.approx({'chr_0004_pelica': 140, 'chr_0023_antal': 40})


def test_complete_empty_enhance_map_is_exact_zero_level_baseline(monkeypatch):
    meta = {'item_name': 'zero level', 'attribute_source_sha256': 'b' * 64,
            'attribute_modifiers': [
                {'attrIndex': 1, 'attrType': 41, 'modifierType': 5,
                 'modifyAttributeType': 0, 'attrValues': [65, 71, 78, 84]},
                {'attrIndex': 2, 'attrType': 87, 'modifierType': 5,
                 'modifyAttributeType': 0, 'attrValues': [30, 35, 40, 45]},
            ]}
    monkeypatch.setattr(parser, '_load_equip_item_catalog', lambda: {'item_zero': meta})
    row = {'equipment_snapshot_complete': True,
           'equips': [{'slot': 0, 'item_id': 'item_zero', 'enhance_levels': [],
                       'stats': [{'slot': 'main', 'value': 42.0},
                                 {'slot': 'sub1', 'value': 'unknown'},
                                 {'slot': 'sub2', 'value': 'unknown'}]}]}
    assert parser._derive_static_self_multiplier_entries(row) == []
    assert row['equipment_baseline_attributes'] == [
        {'source_type': 'equip_baseline', 'source_id': 'item_zero', 'source_slot': 0,
         'attr_type': 41, 'attr_index': 1, 'value': 65.0, 'enhance_level': 0,
         'evidence_kind': 'official_EquipTable_attribute_zero_or_exact', 'source_table_sha256': 'b' * 64},
        {'source_type': 'equip_baseline', 'source_id': 'item_zero', 'source_slot': 0,
         'attr_type': 87, 'attr_index': 2, 'value': 30.0, 'enhance_level': 0,
         'evidence_kind': 'official_EquipTable_attribute_zero_or_exact', 'source_table_sha256': 'b' * 64},
    ]
    assert row['equips'][0]['stats'][1]['value'] == 65.0
    assert row['equips'][0]['stats'][1]['value_source'] == 'official_EquipTable_attribute_zero_or_exact'
    assert row['equips'][0]['stats'][2]['value'] == 30.0


def test_complete_partial_enhance_map_defaults_each_missing_index_to_zero(monkeypatch):
    meta = {'item_name': 'partial', 'attribute_source_sha256': 'c' * 64,
            'attribute_modifiers': [
                {'attrIndex': 1, 'attrType': 41, 'modifierType': 5,
                 'modifyAttributeType': 0, 'attrValues': [65, 71, 78, 84]},
                {'attrIndex': 2, 'attrType': 40, 'modifierType': 5,
                 'modifyAttributeType': 0, 'attrValues': [43, 47, 51, 55]},
            ]}
    monkeypatch.setattr(parser, '_load_equip_item_catalog', lambda: {'item_partial': meta})
    row = {'equipment_snapshot_complete': True,
           'equips': [{'slot': 0, 'item_id': 'item_partial',
                       'enhance_levels': [{'index': 1, 'level': 3}],
                       'stats': [{'slot': 'main', 'value': 42.0},
                                 {'slot': 'sub1', 'value': 84.0},
                                 {'slot': 'sub2', 'value': 'unknown'}]}]}
    parser._derive_static_self_multiplier_entries(row)
    assert row['equips'][0]['stats'][2]['value'] == 43.0
    assert row['equipment_baseline_attributes'][-1]['enhance_level'] == 0


def test_current_composite_equipment_uses_expanded_full_precision_values():
    row = {'equips': [{'slot': 0, 'item_id': 'item_equip_t4_suit_expend_spell01_hand_02',
                      'enhance_levels': [{'index': i, 'level': 3} for i in (1, 2, 3)]}]}
    entries = parser._derive_static_self_multiplier_entries(row)
    assert {(e['attr_type'], e['element']) for e in entries} == {(52, 'pulse'), (53, 'cryst')}
    assert all(e['rate'] == 0.2491666666666667 for e in entries)
    # Its ability attributes still require the attack-base formula audit.
    assert {e['attr_type'] for e in row['rdps_unresolved_static_multiplier_sources']} == {41, 42}


def test_invalid_snapshot_does_not_override_proven_static_attribute(equipment):
    entries = parser._derive_static_self_multiplier_entries({'equips': [equipment]})
    hit = {'character_key': 'chr_0004_pelica', 'target_enemy_key': 'eny_0051_rodin',
           'ts_ms': 500, 'hit_value': 180, 'skill_key': 'explicit', 'damage_element': 'pulse',
           'damage_school': 'spell', 'baseline': {52: None}, 'static_multiplier_entries': entries}
    result = parser._allocate_rdps_for_hit(hit, [], include_debug=True)
    assert hit['rdps_unresolved_effect_conditions'] is True
    assert result['zones'][0]['self_rate'] == 0.4


def test_small_positive_static_baseline_is_not_dropped_by_a_threshold():
    hit = {'character_key': 'chr_0004_pelica', 'target_enemy_key': 'eny_0051_rodin',
           'ts_ms': 500, 'hit_value': 140.05, 'skill_key': 'explicit', 'damage_element': 'pulse',
           'damage_school': 'spell', 'baseline': {52: 0.0005}}
    window = {'uid': 'x', 'event_key': 'explicit', 'source_character_key': 'chr_0023_antal',
              'target_character_key': 'chr_0004_pelica', 'start_ts_ms': 0, 'end_ts_ms': 1000,
              'zone_effects': [{'zone': 'dmg_inc', 'element': 'pulse', 'rate': 0.4}]}
    result = parser._allocate_rdps_for_hit(hit, [window])
    assert result['contributions_map'] == pytest.approx({'chr_0004_pelica': 100.05, 'chr_0023_antal': 40})


def test_character_base_stats_never_use_maximum_or_neighbor_level(monkeypatch):
    monkeypatch.setattr(parser, '_load_character_base_stat_catalog', lambda: {'chr_fixture': {
        1: {'level': 1, 'atk': 10}, 90: {'level': 90, 'atk': 100}}})
    assert parser._character_base_stats('chr_fixture', 90)['atk'] == 100
    for value in (None, 0, 2, 89, 91, True, 90.0):
        assert parser._character_base_stats('chr_fixture', value) is None


def test_loadout_boundary_excludes_future_update_and_future_fallback():
    char = 'chr_0004_pelica'
    groups = [
        {'ts_ms': 100, 'reason': 'SC_SELF_SCENE_INFO', 'index': 0,
         'rows': {char: {'character_key': char, 'weapon_template': 'old', 'weapon_refine': 0}}},
        {'ts_ms': 1100, 'reason': 'BATTLE_START', 'index': 1,
         'rows': {char: {'character_key': char, 'weapon_template': 'new', 'weapon_refine': 5}}},
    ]
    result = parser._select_loadout_snapshot_for_battle(groups,
        {char: {'weapon_template': 'new', 'weapon_refine': 5}}, anchor_ms=1000)
    assert result[char]['weapon_template'] == 'old'
    assert result[char]['weapon_refine'] == 0
    assert parser._select_loadout_snapshot_for_battle(groups, {}, anchor_ms=0) == {}


def test_same_time_loadout_uses_packet_order_without_reason_ranking():
    char = 'chr_0004_pelica'
    groups = [
        {'ts_ms': 100, 'reason': 'BATTLE_START', 'rows': {char: {'weapon_template': 'old'}}},
        {'ts_ms': 100, 'reason': 'SC_ITEM_BAG_SCOPE_MODIFY', 'rows': {char: {'weapon_template': 'new'}}},
    ]
    assert parser._select_loadout_snapshot_for_battle(groups, {}, anchor_ms=100)[char]['weapon_template'] == 'new'


def test_explicit_empty_equipment_snapshot_clears_old_slots():
    base = {'equips': [{'slot': 2, 'item_id': 'old'}], 'suit_effects': [{'suit_id': 'old_suit'}]}
    empty = parser._parse_loadout_slot_snapshot('[10:00:00.000] LOADOUT char=chr_0004_pelica equips={} equipsComplete=1 equipSuit={}')
    result = parser._merge_loadout_snapshot(base, empty)
    assert result['equips'] == [] and result['suit_effects'] == []
    partial = parser._parse_loadout_stats_snapshot('[10:00:00.000] LOADOUT_STATS char=chr_0004_pelica weaponBaseAtk=100')
    assert parser._merge_loadout_snapshot(base, partial)['equips'] == base['equips']


def test_equipment_replace_never_borrows_old_stats_or_invents_second_slot():
    base = [{'slot': 2, 'item_id': 'old_edc_item', 'suit_id': 'suit_a',
             'stats': [{'value': 999}], 'enhance_levels': [{'index': 1, 'level': 3}]}]
    update = [{'slot': 2, 'item_id': 'new_edc_item', 'suit_id': 'suit_b'}]
    result = parser._merge_loadout_equips_by_slot(base, update, suit_counts={'suit_a': 1})
    assert result == update
