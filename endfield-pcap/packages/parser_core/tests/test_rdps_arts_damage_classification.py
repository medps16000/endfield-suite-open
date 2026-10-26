"""Anomaly eligibility is tied to exact damage units, not names or elements."""
from copy import deepcopy
from itertools import permutations

import pytest

from parser_core import battle_log_parser as parser


STATIC_SOURCE = 'current_typed_skill_or_buff_exact_unit_generation_only'


def damage_log(template, action, unit=0, damage=130):
    return (f'[00:00:01.000] HP_V2 #1 hit={damage} cum={damage} raw={damage} '
            f'packetFinalValue={damage} pHP=100 eHP=1000 skill="{template}" hits=1 '
            f'actionId={action} damageUnitIndex={unit} src=chr_0016_laevat '
            'tgt=eny_0051_rodin atk=chr_0016_laevat atkId=1001 tgtId=2001 seg=0 critFlag=0')


@pytest.mark.parametrize('mask,kind', [
    (0x4000, 'physical_anomaly'), (0x8000, 'physical_anomaly'),
    (0x10000, 'physical_anomaly'), (0x40000000, 'physical_anomaly'),
    (0x8, 'spell_anomaly'), (0x10, 'spell_anomaly'), (0x20, 'spell_anomaly'),
    (0x100000, 'spell_anomaly'), (0x4000000, 'spell_anomaly'), (0x8000000, 'spell_anomaly'),
    (0x400000, 'spell_burst'), (0x800000, 'spell_burst'),
    (0x1000000, 'spell_burst'), (0x2000000, 'spell_burst'),
])
def test_official_positive_anomaly_flags_enable_arts_strength(mask, kind):
    hit = {'static_damage_decorate_mask': mask, 'static_damage_decorate_mask_source': STATIC_SOURCE}
    assert parser._is_arts_strength_damage_hit(hit) is True
    assert hit['arts_strength_damage_kind'] == kind
    assert hit['arts_strength_damage_source'] == STATIC_SOURCE
    assert hit['arts_strength_damage_factor_count'] == 1
    assert hit.get('damage_decorate_mask') is None


@pytest.mark.parametrize('mask,count', [(0x4000 | 0x8000000, 2), (0x40000000 | 0x400000, 2),
                                       (0x8 | 0x400000, 1)])
def test_independent_physical_and_spell_flags_count_native_factors(mask, count):
    hit = {'damage_decorate_mask': mask}
    assert parser._is_arts_strength_damage_hit(hit) is True
    assert hit['arts_strength_damage_kind'] == 'mixed_anomaly'
    assert hit['arts_strength_damage_factor_count'] == count


@pytest.mark.parametrize('mask', [0, 0x4, 0x40, 0x80, 0x100, 0x200, 0x1000, 0x10000000])
def test_normal_fire_dot_or_break_tags_do_not_prove_anomaly(mask):
    hit = {'skill_key': 'burning_fire_shatter_name_is_not_evidence', 'damage_element': 'fire',
           'damage_school': 'spell', 'static_damage_decorate_mask': mask,
           'static_damage_decorate_mask_source': STATIC_SOURCE}
    assert parser._is_arts_strength_damage_hit(hit) is False
    assert hit['arts_strength_damage_kind'] is None


@pytest.mark.parametrize('mask', [0, 0x100, False, -1, '67108864', 2**64])
def test_explicit_runtime_mask_or_invalid_runtime_input_overrides_static_guess(mask):
    hit = {'damage_decorate_mask': mask, 'static_damage_decorate_mask': 0x4000000,
           'static_damage_decorate_mask_source': STATIC_SOURCE}
    assert parser._is_arts_strength_damage_hit(hit) is False


def test_unverified_static_source_is_not_arts_strength_evidence():
    hit = {'static_damage_decorate_mask': 0x4000000, 'static_damage_decorate_mask_source': 'name_guess'}
    assert parser._is_arts_strength_damage_hit(hit) is False


def test_explicit_math_fixture_classification_remains_supported():
    hit = {'arts_strength_damage_observed': True, 'skill_key': 'arbitrary_name'}
    assert parser._is_arts_strength_damage_hit(hit) is True
    assert parser._is_arts_strength_damage_hit(hit) is True
    assert hit['arts_strength_damage_source'] == 'caller_supplied_classification'


@pytest.mark.parametrize('template,action,element,kind', [
    ('buff_common_cryst_triggered_physical_break', 0, 'physical', 'spell_anomaly'),
    ('buff_physical_do_fracture', 10, 'physical', 'physical_anomaly'),
    ('buff_common_burning_status', 0, 'fire', 'spell_anomaly'),
    ('buff_common_fire_fire_triggered', 5, 'fire', 'spell_burst'),
])
def test_raw_hp_constructor_sets_arts_classification_from_exact_current_unit(template, action, element, kind):
    parsed = parser.parse_raw_battle_log_text(damage_log(template, action), include_rdps_debug=True)
    hit = parsed['debug_hits'][0]
    assert hit['damage_element'] == element
    assert hit['damage_school'] == ('physical' if element == 'physical' else 'spell')
    assert hit['arts_strength_damage_observed'] is True
    assert hit['arts_strength_damage_kind'] == kind
    assert hit['arts_strength_damage_source'] == STATIC_SOURCE
    assert hit['damage_decorate_mask'] is None
    assert hit['damage_calculation']['source'] == 'current_typed_damage_unit'
    assert hit['damage_calculation']['sourceProof']['actionId'] == action
    assert hit['damage_calculation']['sourceProof']['damageUnitIndex'] == 0


@pytest.mark.parametrize('template,action,unit', [
    ('chr_0006_wolfgd_normal_skill_projhit', 1, 0),
    ('buff_common_burning_status', 999, 0),
    ('buff_common_burning_status', 0, 99),
    ('buff_common_burning_status_fake_suffix', 0, 0),
])
def test_regular_fire_and_wrong_or_missing_action_unit_never_become_burning(template, action, unit):
    hit = parser.parse_raw_battle_log_text(damage_log(template, action, unit), include_rdps_debug=True)['debug_hits'][0]
    assert hit['arts_strength_damage_observed'] is False


def test_duplicate_unit_identity_does_not_select_first_anomaly_or_calculation(monkeypatch):
    units = parser._load_skill_action_damage_units('buff_common_burning_status')
    modified = deepcopy(units)
    modified[0].append(deepcopy(modified[0][0]))
    monkeypatch.setattr(parser, '_load_skill_action_damage_units', lambda key: modified)
    hit = parser.parse_raw_battle_log_text(damage_log('buff_common_burning_status', 0), include_rdps_debug=True)['debug_hits'][0]
    assert hit['arts_strength_damage_observed'] is False
    assert hit['static_damage_decorate_mask'] is None
    assert hit['damage_calculation'] is None


@pytest.mark.parametrize('template,action,expected', [
    ('buff_common_cryst_triggered_physical_break', 0, {'chr_0016_laevat': 100, 'chr_0029_pograni': 30}),
    ('buff_common_fire_fire_triggered', 5, {'chr_0016_laevat': 100, 'chr_0029_pograni': 30}),
    ('chr_0006_wolfgd_normal_skill_projhit', 1, {'chr_0016_laevat': 130}),
])
def test_real_whitelisted_strength_buff_allocates_only_eligible_damage(template, action, expected):
    hit = parser.parse_raw_battle_log_text(damage_log(template, action), include_rdps_debug=True)['debug_hits'][0]
    buff = {'event_key': 'buff_chr_0029_pograni_talent1', 'source_character_key': 'chr_0029_pograni',
            'source_actor_id': '3001', 'target_character_key': hit['character_key'], 'target_actor_id': '1001',
            'bb_values': {'atk_up': 0.0, 'physpell_up': 30.0}}
    effects = parser._collect_zone_effects(buff)
    assert any(effect['zone'] == 'arts_strength' and effect['external_rdps_allowed'] is True for effect in effects)
    window = {**buff, 'uid': '4001', 'start_ts_ms': 0, 'end_ts_ms': 2000, 'zone_effects': effects}
    result = parser._allocate_rdps_for_hit(hit, [window], include_debug=True)
    assert result['contributions_map'] == pytest.approx(expected)


def test_full_calculation_fields_keep_the_active_branch_and_dormant_object_separate():
    hp_unit = parser._load_skill_action_damage_units('abilityentity_interact_explosion')[1][0]
    hp = hp_unit['damage_calculation']
    assert hp['simpleCalculation'] is False
    assert hp['atkCalculation']['$type'] == 'Beyond.Gameplay.Core.MultiplyAttributeCalculation'
    assert hp['atkCalculation']['attributeType'] == 'MaxHp'
    assert hp['atkCalculation']['valueSource'] == 'Target'
    assert hp['atkCalculation']['multiplier']['value'] == pytest.approx(0.3)
    dormant = parser._load_skill_action_damage_units('chr_0034_typhoea_archery_projectile_bounce_projhit')[0][0]['damage_calculation']
    assert dormant['simpleCalculation'] is True
    assert dormant['atkScale']['useBlackboardKey'] is False and dormant['atkScale']['value'] == 1.0
    assert dormant['atkCalculation']['addition']['value'] == 1250.0
    # 1250 is retained as data; this test does not assert that the dormant
    # calculation is selected when simpleCalculation is true.


@pytest.mark.parametrize('mask,power,damage,attacker_share', [
    (0x8000000, 1, 200.0, 150.0),
    (0x4000 | 0x8000000, 2, 400.0, 225.0),
])
def test_arts_factor_power_preserves_sources_order_and_total(mask, power, damage, attacker_share):
    # Explicit mathematical inputs: S_self=50, provider A=30, provider B=20.
    # M(S_total)=2 and M(S_self)=1.5, independently on each proven anomaly factor.
    # There is currently no captured mixed-mask damage unit; this tests the
    # formula for that explicit input without claiming real-battle coverage.
    attacker, provider_a, provider_b = 'chr_0016_laevat', 'chr_0029_pograni', 'chr_0033_camille'
    hit = {'character_key': attacker, 'target_enemy_key': 'eny_0051_rodin',
           'hit_value': damage, 'ts_ms': 500, 'damage_element': 'physical', 'damage_school': 'physical',
           'damage_decorate_mask': mask,
           'static_arts_strength_entries': [{'zone': 'arts_strength', 'rate': 50.0,
                'source_type': 'explicit_math_fixture', 'source_name': 'retained strength'}]}
    windows = []
    for source, points in [(provider_a, [10.0, 20.0]), (provider_b, [20.0])]:
        windows.append({'uid': source, 'event_key': 'explicit_math_fixture_' + source,
            'source_character_key': source, 'target_character_key': attacker,
            'start_ts_ms': 0, 'end_ts_ms': 1000,
            'zone_effects': [{'zone': 'arts_strength', 'element': 'all', 'rate': value,
                 'external_rdps_allowed': True, 'rdps_whitelist_keys': ['explicit_approved_math_fixture']}
                for value in points]})
    pool = damage - attacker_share
    for source_order in permutations(windows):
        for reverse_effects in [False, True]:
            ordered = deepcopy(list(source_order))
            if reverse_effects:
                for window in ordered:
                    window['zone_effects'].reverse()
            sample = deepcopy(hit)
            result = parser._allocate_rdps_for_hit(sample, ordered, include_debug=True)
            assert sample['arts_strength_damage_factor_count'] == power
            assert result['contributions_map'] == pytest.approx({attacker: attacker_share,
                provider_a: pool * 0.6, provider_b: pool * 0.4})
            assert sum(result['contributions_map'].values()) == pytest.approx(damage)
            zone = next(row for row in result['zones'] if row['zone'] == 'arts_strength')
            assert zone['total_multiplier'] == pytest.approx(2.0 ** power)
            assert zone['external_multiplier'] == pytest.approx(round((2 / 1.5) ** power, 4))
