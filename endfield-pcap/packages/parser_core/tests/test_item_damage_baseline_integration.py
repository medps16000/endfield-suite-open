"""Exact item-card inputs must retain the carrier's own damage contribution."""
from copy import deepcopy

import pytest

from parser_core import battle_log_parser as parser


ATTACKER = 'chr_0004_pelica'
PROVIDER = 'chr_0023_antal'


def allocate(loadout, damage, element, mask=0x200):
    entries = parser._derive_static_self_multiplier_entries(loadout)
    hit = {'character_key': ATTACKER, 'target_enemy_key': 'eny_0051_rodin',
           'source_actor_id': '1001', 'target_actor_id': '3001', 'ts_ms': 500,
           'hit_value': damage, 'skill_key': 'explicit_damage_baseline_fixture',
           'damage_element': element, 'damage_school': 'physical' if element == 'physical' else 'spell',
           'damage_decorate_mask': mask, 'static_multiplier_entries': entries}
    windows = [{'uid': '200', 'event_key': 'explicit_baseline_buff_fixture', 'source_character_key': PROVIDER, 'target_character_key': ATTACKER,
                'target_actor_id': '1001', 'start_ts_ms': 0, 'end_ts_ms': 1000,
                'zone_effects': [{'zone': 'dmg_inc', 'element': 'all', 'rate': 0.2,
                                  'external_rdps_allowed': True, 'rdps_whitelist_keys': ['fixture']}]}]
    result = parser._allocate_rdps_for_hit(hit, windows, include_debug=True)
    assert sum(result['contributions_map'].values()) == pytest.approx(damage)
    return result, entries


@pytest.mark.parametrize('element', ['fire', 'pulse', 'cryst', 'natural'])
def test_cold_night_shadow_card_spell_bonus_retains_exact_four_elements(element):
    row = {'weapon_template': 'wpn_funnel_0019', 'weapon_source_skills': [
        {'skill_id': 'sk_wpn_funnel_0019', 'bb': {'spell_dmg_up': 0.16, 'spell_dmg_up2': 0.5}}]}
    result, entries = allocate(row, 136, element)
    assert {entry['attr_type'] for entry in entries} == {51, 52, 53, 54}
    assert result['contributions_map'] == pytest.approx({ATTACKER: 116, PROVIDER: 20})
    other, _ = allocate(deepcopy(row), 120, 'ether')
    assert other['contributions_map'] == pytest.approx({ATTACKER: 100, PROVIDER: 20})


@pytest.mark.parametrize('skill_id', ['1254', 'wpn_sp_attr_firedam_high'])
def test_observed_generic_weapon_skill_id_in_current_slot_retains_fire_bonus(skill_id):
    row = {'weapon_template': 'wpn_lance_0015', 'weapon_source_skills': [
        {'skill_id': skill_id, 'bb': {'firedam': 0.5}}]}
    result, entries = allocate(row, 170, 'fire')
    assert len(entries) == 1
    assert result['contributions_map'] == pytest.approx({ATTACKER: 150, PROVIDER: 20})
    row['weapon_template'] = 'wpn_lance_0014'
    _, entries = allocate(row, 120, 'fire')
    assert entries == []


@pytest.mark.parametrize('mask', [0x100, 0x200, 0x2000])
def test_all_skill_suit_card_applies_once_to_each_declared_category(mask):
    row = {'suit_effects': [{'suit_id': 'suit_atk01', 'active': True, 'value': {'dmg_up': 0.24}}]}
    result, entries = allocate(row, 144, 'fire', mask)
    assert {entry['attr_type'] for entry in entries} == {28, 32, 33}
    assert result['contributions_map'] == pytest.approx({ATTACKER: 124, PROVIDER: 20})
    normal, _ = allocate(deepcopy(row), 120, 'fire', 0x4)
    assert normal['contributions_map'] == pytest.approx({ATTACKER: 100, PROVIDER: 20})
    row['suit_effects'][0]['active'] = False
    _, entries = allocate(row, 120, 'fire', mask)
    assert entries == []


@pytest.mark.parametrize('observations', [
    [{'skill_id': 'sk_wpn_funnel_0019', 'bb': {'spell_dmg_up': 0.16}},
     {'skill_id': 'sk_wpn_funnel_0019', 'bb': {'spell_dmg_up': 0.16}}],
    [{'skill_id': 'sk_wpn_funnel_0019', 'bb': {'spell_dmg_up': 0.16}}],
])
def test_identical_observation_of_same_card_source_is_applied_once(observations):
    result, entries = allocate({'weapon_template': 'wpn_funnel_0019', 'weapon_source_skills': observations}, 136, 'fire')
    assert len(entries) == 4
    assert result['contributions_map'] == pytest.approx({ATTACKER: 116, PROVIDER: 20})


def test_conflicting_card_observations_are_not_selected_by_order():
    observations = [{'skill_id': 'sk_wpn_funnel_0019', 'bb': {'spell_dmg_up': 0.16}},
                    {'skill_id': 'sk_wpn_funnel_0019', 'bb': {'spell_dmg_up': 0.32}}]
    for rows in (observations, list(reversed(observations))):
        row = {'weapon_template': 'wpn_funnel_0019', 'weapon_source_skills': rows}
        _, entries = allocate(row, 136, 'fire')
        assert entries == []
        assert any(item['reason'] == 'conflicting_static_multiplier_source'
                   for item in row['rdps_unresolved_static_multiplier_sources'])


def test_mission_deliver_natural_bonus_preserves_its_original_addition_stage():
    # The current text and complete current item/buff roots supply a +16%
    # natural bonus; no current natural-attribute intermediate multiplier exists.
    # Keep formula 0 intact instead of recasting it as BaseAddition 5.
    row = {'weapon_template': 'wpn_funnel_0011', 'weapon_source_skills': [
        {'skill_id': 'sk_wpn_funnel_0011', 'bb': {'nature_dmg_up': 0.16, 'spell_dmg_up': 0.12}}]}
    result, entries = allocate(row, 136, 'natural')
    assert len(entries) == 1
    assert (entries[0]['modifier_type'], entries[0]['formula_item']) == (0, 'Addition')
    assert result['contributions_map'] == pytest.approx({ATTACKER: 116, PROVIDER: 20})


def test_all_type_suit_includes_exact_ether_attribute():
    row = {'suit_effects': [{'suit_id': 'suit_generaltype', 'active': True, 'value': {'dmg_up': 0.16}}]}
    result, entries = allocate(row, 136, 'ether')
    assert {entry['attr_type'] for entry in entries} == {50, 51, 52, 53, 54, 55}
    assert result['contributions_map'] == pytest.approx({ATTACKER: 116, PROVIDER: 20})
