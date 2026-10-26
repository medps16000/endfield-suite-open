from copy import deepcopy

import pytest

from parser_core import battle_log_parser as parser


CHAR = 'chr_0016_laevat'
TARGET = 'eny_0051_rodin'


@pytest.mark.parametrize('actual,expected', [(None, 'condition_unobservable'), (0, 'condition_unsatisfied'), (128, None)])
def test_child_damage_does_not_inherit_original_skill_category(monkeypatch, actual, expected):
    monkeypatch.setattr(parser, '_canonical_num_table_skill_number', lambda *args: 'original_normal_attack')
    monkeypatch.setattr(parser, '_resolve_skill_profile', lambda *args: {'skill_specification': 'CharacterNormalAttack'})
    effect = {'condition': {'type': 'damage_decorate_mask', 'mask': 0x20484, 'check': 'HasAny'}}
    hit = {'character_key': CHAR, 'skill_key': 'separate_trigger_damage',
           'original_template_int_id': 1021, 'damage_decorate_mask': actual}
    failure = parser._effect_condition_failure(effect, hit)
    assert (failure or {}).get('reason_group') == expected


def test_matching_element_does_not_replace_missing_runtime_damage_mask():
    effect = {'condition': {'type': 'damage_decorate_mask', 'mask': 0x80, 'check': 'HasAny'},
              'attr_type': 51, 'element': 'fire'}
    hit = {'damage_element': 'fire', 'damage_decorate_mask': None}
    failure = parser._effect_condition_failure(effect, hit)
    assert (failure or {}).get('reason_group') == 'condition_unobservable'


@pytest.mark.parametrize('passed', [False, True])
@pytest.mark.parametrize('loadout', [
    {'suit_effects': [{'suit_id': 'unreviewed_suit', 'value': {'dmg_up': 0.3}}]},
    {'suit_effects': [{'suit_id': 'suit_fire_natr01', 'value': {'new_trigger_key': 0.3}}]},
    {'weapon_template': 'wpn_sword_0006', 'weapon_source_skills': [
        {'skill_id': '2237', 'bb': {'unreviewed_trigger_key': 0.3}}]},
])
def test_unreviewed_effect_is_not_waived_by_a_source_name(monkeypatch, loadout, passed):
    monkeypatch.setattr(parser, '_select_loadout_snapshot_for_battle', lambda *args, **kwargs: {CHAR: deepcopy(loadout)})
    parsed = parser.parse_raw_battle_log_text(
        '[10:00:01.000] HP_V2 #1 hit=100 eHP=1000 skill="chr_0016_laevat_attack1" '
        f'src={CHAR} atk={CHAR} tgt={TARGET} actionId=6 damageUnitIndex=0'
        + ('\n[10:00:02.000] OFFICIAL_TIMER_END isPass=1 passTime=1000' if passed else ''),
        include_rdps_debug=True,
    )
    assert parsed['debug_hits'][0]['rdps_unresolved_static_multiplier_sources']
    assert parsed['rdps_preflight']['ok'] is False


def test_same_blackboard_name_does_not_define_an_unknown_weapon():
    row = {'weapon_template': 'unknown_weapon',
           'weapon_source_skills': [{'skill_id': 'unknown', 'bb': {'atk_up': 0.3}}]}
    parser._derive_static_self_multiplier_entries(row)
    assert row['rdps_unresolved_static_multiplier_sources']


def test_observed_exact_weapon_trigger_retires_static_trigger_source(monkeypatch):
    monkeypatch.setattr(parser, '_select_loadout_snapshot_for_battle', lambda *args, **kwargs: {
        CHAR: {'character_key': CHAR, 'weapon_template': 'wpn_funnel_0017',
               'weapon_source_skills': [{'skill_id': '2913', 'level': 9,
                                         'bb': {'pulse_dmg_up2': 0.154}}],
               'suit_effects': [], 'equips': [], 'equipment_snapshot_complete': True},
    })
    parsed = parser.parse_raw_battle_log_text('\n'.join([
        f'[10:00:00.000] BUFF_START #1 id="buff_wpn_funnel_0017_layer" uid=77 '
        f'owner={CHAR} src={CHAR} ownerId=100 srcId=100 dur=30 lifeT=30 passed=0 enh=1',
        '[10:00:00.000]   BB[77]: pulse_dmg_up2=0.154 duration=30 max_stack=3',
        f'[10:00:01.000] HP_V2 #1 hit=100 eHP=1000 skill="{CHAR}_attack1" '
        f'src={CHAR} atk={CHAR} tgt={TARGET} actionId=0 damageUnitIndex=0',
    ]), include_rdps_debug=True)
    assert not any(row.get('reason') == 'unmodeled_weapon_static_semantics'
                   for row in parsed['debug_hits'][0]['rdps_unresolved_static_multiplier_sources'])


@pytest.mark.parametrize('baseline', [{51: 0.756}, {}])
def test_basic_weapon_bonus_is_counted_once_with_or_without_hit_baseline(baseline):
    row = {'weapon_template': 'wpn_sword_0006',
           'weapon_source_skills': [{'skill_id': '2237', 'bb': {'fire_dmg_up': 0.256}}]}
    entries = parser._derive_static_self_multiplier_entries(row)
    hit = {'character_key': CHAR, 'target_enemy_key': TARGET, 'ts_ms': 500,
           'hit_value': 175.6, 'skill_key': 'explicit', 'damage_element': 'fire',
           'damage_school': 'spell', 'baseline': baseline, 'static_multiplier_entries': entries}
    window = {'uid': '1', 'event_key': 'explicit', 'source_character_key': 'chr_0025_ardelia',
              'target_character_key': CHAR, 'start_ts_ms': 0, 'end_ts_ms': 1000,
              'zone_effects': [{'zone': 'dmg_inc', 'element': 'fire', 'attr_type': 51, 'rate': 0.5}]}
    result = parser._allocate_rdps_for_hit(hit, [window])
    assert result['contributions_map'] == pytest.approx({CHAR: 125.6, 'chr_0025_ardelia': 50.0})


def test_same_blackboard_key_from_wrong_weapon_skill_is_not_basic_bonus():
    row = {'weapon_template': 'wpn_sword_0006',
           'weapon_source_skills': [{'skill_id': '1247', 'bb': {'fire_dmg_up': 0.256}}]}
    assert parser._derive_static_self_multiplier_entries(row) == []


def test_explicit_buff_end_is_not_shortened_by_nominal_game_duration():
    # Packet timestamps include pauses; the explicit end is later than seven
    # seconds of wall time, as in the unconsumed-corrosion capture.
    assert parser._normalize_buff_duration_ms(
        1000, event_key='buff_common_natural_natural_corrupt_do',
        end_ts_ms=11000, raw_duration_ms=7000, battle_end_ms=15000,
    ) == 10000


@pytest.mark.parametrize('duplicate', [False, True])
def test_dynamic_action_keeps_destination_separate_from_operand(monkeypatch, duplicate):
    action = {'$type': 'Beyond.Gameplay.Core.ModifyDynamicBlackboard.Data',
              'serverActionIndex': 5, 'key': 'def_decrease', 'operation': 'Add',
              'value': {'useBlackboardKey': True, 'blackboardKey': 'def_decrease_tick'}}
    root = {'actions': [action] * (2 if duplicate else 1)}
    monkeypatch.setattr(parser, '_load_current_buff_roots', lambda: {'buff_test_dynamic': root})
    parsed = parser.parse_raw_battle_log_text(
        '[10:00:00.100] BB_MODIFY_DYNAMIC seq=1 tick=10 inst=111 ownerId=200 '
        'owner=eny_0051_rodin skill=buff_test_dynamic actionId=5 value=-4.44\n'
        '[10:00:00.200] HP_V2 #1 hit=100 eHP=1000 skill="chr_0016_laevat_attack1" '
        f'src={CHAR} tgt={TARGET} actionId=6 damageUnitIndex=0',
    )
    event = parsed['dynamic_bb_events'][0]
    if duplicate:
        assert event['blackboard_key'] is None
        assert event['mapping_error'] == 'ambiguous_dynamic_action'
    else:
        assert event['blackboard_key'] == 'def_decrease'
        assert event['operand_blackboard_key'] == 'def_decrease_tick'
        assert event['operation'] == 'Add'
        assert event['value'] == -4.44


@pytest.mark.parametrize('instance,owner,stamp', [('111', '200', '00.500'),
    ('999', '200', '00.500'), ('111', '201', '00.500'), ('111', '200', '01.500')])
def test_dynamic_buff_observation_binds_owner_uid_and_lifetime(instance, owner, stamp):
    buff = 'buff_common_natural_natural_corrupt_do'
    parsed = parser.parse_raw_battle_log_text('\n'.join([
        f'[10:00:00.000] BUFF_START #1 id="{buff}" uid=111 owner={TARGET} src={CHAR} ownerId=200 srcId=100',
        '[10:00:00.000] BB[111]: def_decrease=-3.6 start_def_decrease=-3.6 def_decrease_tick=-0.84 max_def_decrease=-12 additional_def_decrease=0 duration=7',
        f'[10:00:00.400] HP_V2 #1 hit=100 eHP=1100 skill="chr_0016_laevat_attack1" src={CHAR} tgt={TARGET} actionId=6 damageUnitIndex=0',
        f'[10:00:{stamp}] BB_MODIFY_DYNAMIC seq=1 tick=10 inst={instance} ownerId={owner} owner={TARGET} skill={buff} actionId=5 value=-4.44',
        f'[10:00:01.000] BUFF_END #1 id="{buff}" uid=111',
        f'[10:00:02.000] HP_V2 #2 hit=100 eHP=1000 skill="chr_0016_laevat_attack1" src={CHAR} tgt={TARGET} actionId=6 damageUnitIndex=0',
    ]))
    event = next(e for e in parsed['buff_events'] if e.get('uid') == '111')
    bound = event['dynamic_bb_events']
    assert len(bound) == (1 if instance == '111' and owner == '200' and stamp == '00.500' else 0)


def test_a_numeric_dynamic_update_is_not_itself_a_resistance_multiplier(monkeypatch):
    monkeypatch.setattr(parser, '_rdps_registry_verified_effect_entry', lambda record, root_lookup_cache=None: {'dynamic': True})
    record = {'uid': '111', 'target_actor_id': '200', 'event_key': 'buff_unknown_dynamic',
              'packet_mapping': {'dynamic_effects': [{'zone': 'res', 'element': 'all',
                  'base_bb_key': 'base', 'tick_bb_key': 'step'}]},
              'bb_values': {'base': -3.6, 'step': -0.84},
              'dynamic_bb_events': [{'blackboard_key': 'step', 'ts_ms': 500, 'value': -4.44}]}
    assert parser._extract_dynamic_effect_specs(record) == []
    assert record['rdps_unresolved_dynamic_effect'] is True


def test_unproven_dynamic_points_cannot_bypass_window_evaluation():
    window = {'uid': '111', 'start_ts_ms': 0, 'dynamic_effects': [
        {'zone': 'res', 'element': 'all', 'base_rate': 0, 'observed_points': [(0, 3.6)]}]}
    assert parser._window_effects_at_ts(window, 500) == []
    assert window['rdps_invalid_dynamic_evaluation'] is True


@pytest.mark.parametrize('unit_index', [None, 0])
def test_static_mask_is_diagnostic_and_does_not_fill_missing_unit(monkeypatch, unit_index):
    monkeypatch.setattr(parser, '_load_skill_action_damage_units', lambda key: {
        6: [{'index': 0, 'damage_attribute_type': 'hp', 'element': 'fire',
             'damage_decorate_mask': 128,
             'damage_decorate_mask_source': 'current_typed_buff_exact_unit_generation_only'}]})
    suffix = '' if unit_index is None else ' damageUnitIndex=0'
    parsed = parser.parse_raw_battle_log_text(
        '[10:00:01.000] HP_V2 #1 hit=100 eHP=1000 skill="chr_0016_laevat_attack1" '
        f'src={CHAR} atk={CHAR} tgt={TARGET} actionId=6' + suffix,
        include_rdps_debug=True)
    hit = parsed['debug_hits'][0]
    assert hit['damage_unit_index'] == unit_index
    assert hit['damage_decorate_mask'] is None
    assert hit['static_damage_decorate_mask'] == (128 if unit_index == 0 else None)


@pytest.mark.parametrize('direct_flag', [False, True])
def test_defensive_or_direct_damage_label_cannot_hide_other_modifiers(monkeypatch, direct_flag):
    root = {'attributeModifier': {'attributeModifiers': [
        {'attributeType': 'DamageTakenScalar'}]},
        'damageModifier': [{'enableSide': 'Attacker', 'damageProcessors': [
            {'zoneName': 'BaseAdditionCalcZone'}]}],
        'globalModifier': [], 'healModifier': []}
    monkeypatch.setattr(parser, '_load_current_buff_roots', lambda: {'buff_mixed': root})
    assert parser._rdps_registry_known_non_rdps_entry({
        'event_key': 'buff_mixed', 'target_character_key': CHAR,
        'typed_direct_damage_event': direct_flag}) is None


def test_typed_incoming_defense_is_structurally_non_rdps(monkeypatch):
    root = {'attributeModifier': {'attributeModifiers': [
        {'attributeType': 'PhysicalDamageTakenScalar', 'modifyAttributeType': 'Specific'},
        {'attributeType': 'HealTakenIncrease', 'modifyAttributeType': 'Specific'}]},
        'damageModifier': [], 'globalModifier': [], 'healModifier': [], 'actions': []}
    monkeypatch.setattr(parser, '_load_current_buff_roots', lambda: {'buff_defense': root})
    entry = parser._rdps_registry_known_non_rdps_entry({
        'event_key': 'buff_defense', 'target_character_key': CHAR})
    assert entry and entry['proof_kind'] == 'typed_root_structural'
    # Incoming damage on an enemy can affect player damage. The same root
    # must not be suppressed merely because it uses damage-taken attributes.
    assert parser._rdps_registry_known_non_rdps_entry({
        'event_key': 'buff_defense', 'target_enemy_key': TARGET}) is None
    root['actions'] = [{'$type': 'Beyond.Gameplay.Core.DamageAction.DamageActionData'}]
    assert parser._rdps_registry_known_non_rdps_entry({
        'event_key': 'buff_defense', 'target_character_key': CHAR}) is None


def test_registry_proof_label_without_current_root_cannot_suppress(monkeypatch):
    monkeypatch.setattr(parser, '_load_current_buff_roots', lambda: {})
    monkeypatch.setattr(parser, '_load_rdps_semantics_registry', lambda: {
        'known_non_rdps': {'exact_buff_ids': {'buff_unproven': {
            'proof_kind': 'typed_root_structural', 'suppress_zone_effects': True}}}})
    assert parser._rdps_registry_known_non_rdps_entry({'event_key': 'buff_unproven'}) is None


@pytest.mark.parametrize('action_type', [
    'Beyond.Gameplay.Core.ModifyDynamicBlackboard.Data',
    'Beyond.Gameplay.Core.InstantModifyAttribute.Data',
    'Beyond.Gameplay.Core.ModifyDynamicBlackboard+Data',
])
def test_nested_data_type_does_not_hide_unmodeled_attribute_action(monkeypatch, action_type):
    root = {'attributeModifier': {'attributeModifiers': []}, 'damageModifier': [],
            'globalModifier': [], 'healModifier': [], 'actions': [{'$type': action_type}]}
    monkeypatch.setattr(parser, '_load_current_buff_roots', lambda: {'buff_action': root})
    assert parser._rdps_registry_known_non_rdps_entry({'event_key': 'buff_action'}) is None


def test_hit_retains_exact_packet_instance_without_inventing_it():
    prefix = ('[10:00:01.000] HP_V2 #1 hit=100 eHP=1000 skill="chr_0016_laevat_attack1" '
              f'src={CHAR} atk={CHAR} tgt={TARGET} actionId=6 damageUnitIndex=0')
    for suffix, expected in [('', (None, None, None, None)),
        (' eventInstId=2305843009213695858 eventOwnerId=100573 packetSeq=2377 clientTick=35597',
         ('2305843009213695858', '100573', 2377, 35597))]:
        hit = parser.parse_raw_battle_log_text(prefix + suffix, include_rdps_debug=True)['debug_hits'][0]
        assert tuple(hit[k] for k in ('event_inst_id', 'event_owner_id', 'packet_seq_id', 'client_tick_ms')) == expected


def test_bound_dynamic_value_is_not_automatically_a_resistance_rate(monkeypatch):
    monkeypatch.setattr(parser, '_rdps_registry_verified_effect_entry', lambda record, root_lookup_cache=None: {
        'dynamic': True, 'dynamic_effects': [{'zone': 'res', 'element': 'all',
            'base_bb_key': 'base', 'tick_bb_key': 'step'}]})
    record = {'packet_mapping': {}, 'bb_values': {'base': -3.6, 'step': -0.84},
              'dynamic_bb_events': [{'blackboard_key_source': 'typed_current_buff_action',
                  'blackboard_key': 'base', 'operand_blackboard_key': 'step',
                  'operation': 'Add', 'value': -4.44, 'ts_ms': 1000}]}
    assert parser._extract_dynamic_effect_specs(record) == []
    assert record['rdps_unresolved_dynamic_effect'] is True


def test_arts_strength_basic_definition_requires_the_reviewed_source():
    assert parser._derive_static_arts_strength_entries({
        'suit_effects': [{'suit_id': 'unknown_suit', 'active': True, 'value': {'phy_spell_up': 30}}],
        'weapon_template': 'unknown_weapon',
        'weapon_source_skills': [{'skill_id': 'unknown', 'bb': {'phy_spell_up': 25}}],
    }) == []
    result = parser._derive_static_arts_strength_entries({
        'suit_effects': [{'suit_id': 'suit_fire_natr01', 'active': True, 'value': {'phy_spell_up': 30}}],
        'weapon_template': 'wpn_sword_0007',
        'weapon_source_skills': [{'skill_id': '2226', 'bb': {'phy_spell_up': 25}}],
    })
    assert [entry['rate'] for entry in result] == [30, 25]
