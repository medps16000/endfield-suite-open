import json
from copy import deepcopy
from pathlib import Path

import pytest

from parser_core import battle_log_parser as parser


ROWS = json.loads((Path(__file__).parent / 'fixtures/live_weapon_audit_20260914.json').read_text(
    encoding='utf-8'))['rows']


@pytest.mark.parametrize('sample', ROWS, ids=[row['loadout']['character_name'] for row in ROWS])
def test_live_reviewed_trigger_fields_are_not_missing_static_inputs(sample):
    row = deepcopy(sample['loadout'])
    entries = parser._derive_static_self_multiplier_entries(row)
    assert entries == sample['expected_static_entries']
    assert parser._derive_static_arts_strength_entries(row) == sample['expected_arts_strength_entries']
    assert row['rdps_unresolved_static_multiplier_sources'] == []
    assert row.get('weapon_refine') == sample['loadout'].get('weapon_refine')
    assert 'weapon_level' not in row

    # Audit classification must not change allocation. Both copies use the
    # same captured static entries and an identical, explicit external effect.
    hit = {'character_key': row['character_key'], 'target_enemy_key': 'enemy',
           'ts_ms': 500, 'hit_value': 1000, 'skill_key': 'explicit',
           'damage_element': 'fire', 'damage_school': 'spell',
           'damage_decorate_mask': 128, 'baseline': None,
           'static_multiplier_entries': entries}
    windows = [{'uid': 'external', 'event_key': 'explicit',
                'source_character_key': 'other_character',
                'target_character_key': row['character_key'],
                'start_ts_ms': 0, 'end_ts_ms': 1000,
                'zone_effects': [{'zone': 'atk', 'element': 'all', 'rate': 0.2},
                                 {'zone': 'dmg_inc', 'element': 'fire', 'rate': 0.3}]}]
    before = parser._allocate_rdps_for_hit({**hit,
        'static_multiplier_entries': sample['expected_static_entries'],
        'rdps_unresolved_static_multiplier_sources': sample['original_diagnostics']}, windows)
    after = parser._allocate_rdps_for_hit({**hit,
        'rdps_unresolved_static_multiplier_sources': []}, windows)
    assert before['contributions_map'] == after['contributions_map']


@pytest.mark.parametrize('sample', ROWS, ids=[row['loadout']['character_name'] for row in ROWS])
def test_new_field_on_reviewed_weapon_remains_unresolved(sample):
    row = deepcopy(sample['loadout'])
    row['weapon_source_skills'][0]['bb']['new_unreviewed_field'] = 0.123
    parser._derive_static_self_multiplier_entries(row)
    assert row['rdps_unresolved_static_multiplier_sources'][-1]['trigger_blackboard_keys'] == [
        'new_unreviewed_field']


@pytest.mark.parametrize('sample', ROWS, ids=[row['loadout']['character_name'] for row in ROWS])
def test_trigger_field_on_wrong_declared_skill_is_not_authorized(sample):
    row = deepcopy(sample['loadout'])
    row['weapon_source_skills'] = [{'skill_id': '1247', 'bb': {
        'normal_atk_up': 1.2, 'nature_dmg_up_mult': 0.05, 'spell_dmg_taken_up': 0.16}}]
    parser._derive_static_self_multiplier_entries(row)
    assert row['rdps_unresolved_static_multiplier_sources']


def test_arts_skill_zero_attack_placeholder_cannot_authorize_nonzero_attack():
    row = deepcopy(ROWS[1]['loadout'])
    row['weapon_source_skills'][0]['bb']['atk'] = 0.1
    assert parser._derive_static_self_multiplier_entries(row) == []
    assert row['rdps_unresolved_static_multiplier_sources'][-1]['trigger_blackboard_keys'] == ['atk']


def test_known_trigger_does_not_erase_an_unknown_field_on_same_weapon(monkeypatch):
    char = 'chr_0030_zhuangfy'
    monkeypatch.setattr(parser, '_select_loadout_snapshot_for_battle', lambda *args, **kwargs: {
        char: {'character_key': char, 'weapon_template': 'wpn_funnel_0017',
               'weapon_source_skills': [{'skill_id': '2913', 'level': 9,
                   'bb': {'pulse_dmg_up2': 0.154, 'new_unreviewed_field': 0.4}}],
               'suit_effects': [], 'equips': [], 'equipment_snapshot_complete': True}})
    result = parser.parse_raw_battle_log_text('\n'.join([
        f'[10:00:00.000] BUFF_START #1 id="buff_wpn_funnel_0017_layer" uid=77 '
        f'owner={char} src={char} ownerId=100 srcId=100 dur=30 lifeT=30 passed=0 enh=1',
        '[10:00:00.000]   BB[77]: pulse_dmg_up2=0.154 duration=30 max_stack=3',
        f'[10:00:01.000] HP_V2 #1 hit=100 eHP=1000 skill="{char}_attack1" '
        f'src={char} atk={char} tgt=eny_0051_rodin actionId=0 damageUnitIndex=0',
    ]), include_rdps_debug=True)
    blockers = result['debug_hits'][0]['rdps_unresolved_static_multiplier_sources']
    assert any('new_unreviewed_field' in row.get('trigger_blackboard_keys', []) for row in blockers)
