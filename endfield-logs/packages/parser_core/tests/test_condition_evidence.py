import json

import pytest

from parser_core import battle_log_parser as parser


@pytest.fixture
def decoded_modifier(tmp_path, monkeypatch):
    path = tmp_path / 'buff.json'
    monkeypatch.setattr(parser, '_load_current_buff_roots', lambda: {
        'buff_fixture': json.loads(path.read_text(encoding='utf-8'))
    } if path.exists() else {})
    parser._decoded_damage_modifier_condition_rows.cache_clear()

    def write(actions, **guards):
        condition = {'actionData': actions, 'onlyExecuteWhenSourceIsMainChar': False,
                     'onlyExecuteWhenSourceIsGuard': False, **guards}
        path.write_text(json.dumps({'damageModifier': [{'condition': condition}]}), encoding='utf-8')
        parser._decoded_damage_modifier_condition_rows.cache_clear()
        return {'groupIndex': 0, 'conditionActionTypes': [
            row.get('$type', '').split('+')[0].rsplit('.', 1)[-1] for row in actions if isinstance(row, dict)
        ]}

    yield write
    parser._decoded_damage_modifier_condition_rows.cache_clear()


def hp_action(**updates):
    return {'$type': 'Beyond.Gameplay.Core.Conditions.CheckHp+Data, Gameplay.Beyond',
            'isEnable': True, 'hpOwner': {'targetSource': 'Target'}, 'compare': 'LT',
            'isRatio': True, 'value': {'useBlackboardKey': True, 'blackboardKey': 'hp_remain', 'value': 0},
            **updates}


def test_decoded_hp_comparison_uses_operator_and_explicit_maximum(decoded_modifier):
    effect = decoded_modifier([hp_action()])
    condition = parser._semantic_damage_effect_condition('buff_fixture', effect, defaults={}, bb_values={'hp_remain': 0.5})
    assert condition['type'] == 'target_hp_compare'
    assert condition['compare'] == 'LT'
    assert condition['is_ratio'] is True
    assert 'target HP < 50.00%' in parser._effect_summary_rows([{'zone': 'dmg_inc', 'rate': 0.2, 'element': 'all', 'condition': condition}])[0]
    at_threshold = {'enemy_hp_before': 500, 'target_enemy_max_hp': 1000}
    assert parser._effect_condition_failure({'condition': condition}, at_threshold)['reason_group'] == 'condition_unsatisfied'
    assert parser._effect_condition_failure({'condition': condition}, dict(at_threshold, enemy_hp_before=499)) is None
    assert parser._effect_condition_failure({'condition': condition}, {
        'enemy_hp_before': 499, 'target_enemy_observed_max_hp': 1000, 'target_enemy_hp_ratio_before': 0.499,
    })['reason_group'] == 'condition_unobservable'


@pytest.mark.parametrize('compare,hp,passes', [('LT', 50, False), ('LE', 50, True), ('GT', 50, False),
                                            ('GE', 50, True), ('EQ', 50, True), ('NE', 50, False),
                                            ('LT', 49, True), ('GE', 49, False)])
def test_exact_hp_operators_have_no_epsilon(compare, hp, passes):
    condition = {'type': 'target_hp_compare', 'compare': compare, 'is_ratio': False, 'threshold': 50}
    result = parser._effect_condition_failure({'condition': condition}, {'enemy_hp_before': hp})
    assert (result is None) is passes


@pytest.mark.parametrize('condition', [None, 'CheckHp', {}, {'type': 'all'},
                                     {'type': 'all', 'conditions': []},
                                     {'type': 'all', 'conditions': [None]},
                                     {'type': 'future_condition'},
                                     {'type': 'skill_key_matches', 'patterns': ['.*']}])
def test_malformed_or_unsupported_condition_cannot_pass(condition):
    failure = parser._effect_condition_failure({'condition': condition}, {'skill_key': 'anything'})
    assert failure is not None
    assert failure['reason_group'] == 'condition_unobservable'


def test_absent_condition_field_remains_unconditional():
    assert parser._effect_condition_failure({}, {}) is None


def test_hp_default_is_not_substituted_for_missing_runtime_key(decoded_modifier):
    effect = decoded_modifier([hp_action()])
    condition = parser._semantic_damage_effect_condition('buff_fixture', effect, defaults={'hp_remain': 0.5}, bb_values={})
    assert condition['type'] == 'unresolved'


@pytest.mark.parametrize('action', [hp_action(compare='Unsupported'), hp_action(hpOwner={'targetSource': 'Source'}),
                                   hp_action(hpOwner={'targetSource': 'Target', 'selectorData': {'validatorData': [{}]}}),
                                   {'$type': 'Beyond.UnknownCondition+Data, Gameplay.Beyond', 'isEnable': True}])
def test_unsupported_decoded_action_is_not_silently_dropped(decoded_modifier, action):
    effect = decoded_modifier([action])
    result = parser._semantic_damage_effect_condition('buff_fixture', effect, defaults={}, bb_values={'hp_remain': 0.5})
    assert result['type'] == 'unresolved'


def test_disabled_condition_and_enabled_source_guard_are_distinct(decoded_modifier):
    effect = decoded_modifier([hp_action(isEnable=False)])
    condition = parser._semantic_damage_effect_condition('buff_fixture', effect, defaults={}, bb_values={})
    assert condition['type'] == 'always'
    assert parser._effect_condition_failure({'condition': condition}, {}) is None
    effect = decoded_modifier([hp_action()], onlyExecuteWhenSourceIsMainChar=True)
    assert parser._semantic_damage_effect_condition('buff_fixture', effect, defaults={}, bb_values={'hp_remain': 0.5})['type'] == 'unresolved'


def test_damage_type_mask_uses_current_enum_bits(decoded_modifier):
    effect = decoded_modifier([{
        '$type': 'Beyond.Gameplay.Core.Conditions.CheckDamageTypeMask.Data',
        'isEnable': True, 'damageTypeMask': 92,
    }])
    condition = parser._semantic_damage_effect_condition('buff_fixture', effect, defaults={}, bb_values={})
    assert condition['elements'] == ['fire', 'pulse', 'cryst', 'natural']
    assert parser._effect_condition_failure({'condition': condition}, {'damage_element': 'fire'}) is None
    assert parser._effect_condition_failure({'condition': condition}, {'damage_element': 'physical'})['reason_group'] == 'condition_unsatisfied'
    assert parser._effect_condition_failure({'condition': condition}, {})['reason_group'] == 'condition_unobservable'


@pytest.mark.parametrize('attr_type,mask', [(17, 4), (17, 128), (17, 1024), (17, 131072),
                                          (32, 256), (28, 512), (33, 8192)])
def test_skill_attribute_uses_native_mask_instead_of_name(attr_type, mask):
    assert parser._attr_type_applies_to_skill(attr_type, 'unrelated_name', damage_decorate_mask=mask) is True
    assert parser._attr_type_applies_to_skill(attr_type, 'normal_skill_combo_ultimate_attack', damage_decorate_mask=0) is False
    assert parser._attr_type_applies_to_skill(attr_type, 'normal_skill_combo_ultimate_attack') is None


def test_current_normal_attack_buff_no_longer_uses_inverted_name_filter():
    record = {'event_key': 'buff_wpn_sword_0006_valid', 'bb_values': {'normal_atk_up_valid': 0.2}}
    effects = parser._collect_zone_effects(record)
    assert len(effects) == 1
    effect = effects[0]
    assert effect['attr_type'] == 17
    assert effect['condition']['mask'] == 0x20484
    assert parser._effect_condition_failure(effect, {'damage_decorate_mask': 128}) is None
    assert parser._effect_condition_failure(effect, {'damage_decorate_mask': 256})['reason_group'] == 'condition_unsatisfied'


def test_skill_damage_attributes_are_element_independent_and_can_combine():
    hit = {'character_key': 'chr_0004_pelica', 'target_enemy_key': 'eny_0051_rodin',
           'hit_value': 130, 'ts_ms': 500, 'skill_key': 'not_a_skill_name_hint',
           'damage_element': 'physical', 'damage_school': 'physical', 'damage_decorate_mask': 256 | 8192}
    effects = [{'zone': 'dmg_inc', 'element': parser._ATTR_TYPE_TO_EFFECT[attr][1],
                'rate': rate, 'attr_type': attr} for attr, rate in [(32, 0.1), (33, 0.2)]]
    window = {'uid': 'a', 'event_key': 'fixture', 'source_character_key': 'chr_0023_antal',
              'target_character_key': 'chr_0004_pelica', 'start_ts_ms': 0, 'end_ts_ms': 1000,
              'zone_effects': effects}
    allocation = parser._allocate_rdps_for_hit(hit, [window])
    assert allocation['contributions_map'] == pytest.approx({'chr_0004_pelica': 100, 'chr_0023_antal': 30})
    missing = dict(hit)
    missing.pop('damage_decorate_mask')
    assert parser._allocate_rdps_for_hit(missing, [window])['contributions_map'] == {'chr_0004_pelica': 130}
    assert missing['rdps_unresolved_effect_conditions'] is True


def test_gate_validates_unresolved_self_attribute_without_ui_preprocessing(monkeypatch):
    monkeypatch.setattr(parser, '_load_current_buff_roots', lambda: {'buff_fixture': {
        'damageModifier': [], 'attributeModifier': {'isConvertedAttribute': False, 'attributeModifiers': [{
            'attributeType': 'NormalAttackDamageIncrease', 'formulaItem': 'FinalMultiplier',
            'modifyAttributeType': 'Specific', 'param': {'useBlackboardKey': False, 'value': 0.2},
        }]},
    }})
    record = {'event_key': 'buff_fixture', 'uid': 'a', 'ts_ms': 0, 'end_ts_ms': 1000,
              'source_character_key': 'chr_0004_pelica', 'target_character_key': 'chr_0004_pelica'}
    gate = parser._build_rdps_preflight([record], [], battle_start_ms=0, battle_end_ms=1000)
    assert gate['ok'] is False
    assert gate['blockers'][0]['condition_errors'] == ['unsupported_conditional_attribute_formula']


def test_legacy_registry_effect_without_current_typed_modifier_is_unknown(monkeypatch):
    monkeypatch.setattr(parser, '_rdps_registry_verified_effect_entry', lambda record, root_lookup_cache=None: {
        'effects': [{'zone': 'dmg_inc', 'element': 'all', 'bb_key': 'legacy_rate'}],
    })
    monkeypatch.setattr(parser, '_rdps_registry_zone_effects', lambda record, entry: [{
        'zone': 'dmg_inc', 'element': 'all', 'rate': 0.2, '_registry_bb_key': 'legacy_rate'}])
    monkeypatch.setattr(parser, '_load_current_buff_roots', lambda: {'buff_fixture': {
        'damageModifier': [], 'attributeModifier': {'isConvertedAttribute': False, 'attributeModifiers': []},
    }})
    record = {'event_key': 'buff_fixture', 'bb_values': {'legacy_rate': 0.2}}
    assert parser._collect_zone_effects(record) == []
    assert record['rdps_unresolved_semantic_condition'] == ['missing_current_semantic_effect']


def test_current_zone_config_distinguishes_product_and_normal_modes():
    assert parser._semantic_damage_effect_zone({
        'processorType': 'DamageScaleProcessor', 'zoneName': 'ProdCalcZone', 'side': 'Attacker',
    }) == ('prod_dmg_inc', 'all')
    assert parser._semantic_damage_effect_zone({
        'processorType': 'DamageScaleProcessor', 'zoneName': 'NormalCalcZone', 'side': 'Defender',
    }) == ('vuln_taken', 'all')


@pytest.mark.parametrize('mask,elements', [
    ('Physical', ['physical']), ('Fire', ['fire']), ('Pulse', ['pulse']),
    ('Cryst', ['cryst']), ('Natural', ['natural']), (20, ['fire', 'cryst']),
    # Current bound native DamageTypeMask: Real=2, LifeDrain=32, Ether=128.
    # Recognizing a condition does not grant an external rDPS approval.
    ('Real', ['real']), ('LifeDrain', ['lifedrain']), ('Ether', ['ether']),
    ('Fixed', None), ('cryst', None), ('Cryst, Fire', None),
])
def test_damage_mask_uses_exact_typed_enum_names_or_numeric_flags(decoded_modifier, mask, elements):
    effect = decoded_modifier([{'$type': 'Beyond.Gameplay.Core.Conditions.CheckDamageTypeMask.Data',
                               'isEnable': True, 'damageTypeMask': mask}])
    condition = parser._semantic_damage_effect_condition('buff_fixture', effect, defaults={}, bb_values={})
    if elements is None:
        assert condition['type'] == 'unresolved'
        assert 'unsupported_damage_type_mask' in condition['errors']
    else:
        assert condition['type'] == 'damage_type_in'
        assert set(condition['elements']) == set(elements)


def test_missing_summary_does_not_hide_a_decoded_condition(decoded_modifier):
    effect = decoded_modifier([hp_action()])
    effect['conditionActionTypes'] = []
    assert parser._semantic_damage_effect_condition('buff_fixture', effect, defaults={}, bb_values={'hp_remain': 0.5})['type'] == 'target_hp_compare'
    effect = decoded_modifier([])
    assert parser._semantic_damage_effect_condition('buff_fixture', effect, defaults={}, bb_values={})['type'] == 'always'


def test_buff_stack_check_does_not_collapse_two_stacks_into_presence(decoded_modifier):
    action = {'$type': 'Beyond.Gameplay.Core.CheckBuffStackNumAdvanced+Data, Gameplay.Beyond', 'isEnable': True,
              'checkTarget': {'targetSource': 'Target'}, 'buffSettings': {'checkType': 'Id', 'buffIdList': ['buff_x']},
              'buffStackNumType': 'BuffCount', 'compareType': 'GE', 'limitSkillCastId': False,
              'value': {'useBlackboardKey': False, 'value': 2}}
    effect = decoded_modifier([action])
    assert parser._semantic_damage_effect_condition('buff_fixture', effect, defaults={}, bb_values={})['type'] == 'unresolved'
    effect = decoded_modifier([dict(action, value={'useBlackboardKey': False, 'value': 1})])
    condition = parser._semantic_damage_effect_condition('buff_fixture', effect, defaults={}, bb_values={})
    assert condition['type'] == 'target_has_buff'
    assert parser._effect_condition_failure({'condition': condition}, {'_active_target_buff_ids': ['buff_x']}) is None
    assert parser._effect_condition_failure({'condition': condition}, {})['reason_group'] == 'condition_unobservable'


@pytest.mark.parametrize('value', [True, False, float('nan'), float('inf'), '-inf', {}, []])
def test_invalid_numeric_evidence_never_becomes_a_rate(value):
    assert parser._coerce_optional_float(value) is None
    assert parser._safe_positive_rate(value) is None
    assert parser._rdps_registry_effect_runtime_rate({'bb_key': 'rate'}, {'bb_values': {'rate': value}})[0] is None


def test_malformed_dpd_bucket_is_missing_not_neutral_multiplier():
    assert parser._parse_zone_values('bad,2') == [None, 2.0]
    assert parser._parse_zone_values('bad') == [None]
    parsed = parser._parse_dpd_raw_line(
        '[10:00:00.000] DPD_RAW #1 calc=10 atkScale=1 blocked=0 damageType=0x8 '
        'decorateMask=0x80 collider="x" atkZones=[1,2] defZones=[bad]')
    assert parsed['calc'] == 10.0 and parsed['atk_scale'] == 1.0 and parsed['blocked'] == 0
    assert parsed['atk_zones'] == [1.0, 2.0] and parsed['def_zones'] == [None]
    assert parser._dpd_bucket_value_for_zone('dmg_inc', {'atk_zones': [1, None, 3, 4]}) is None
    assert parser._dpd_bucket_value_for_zone('amp', {'atk_zones': [1, None, 3, 4]}) == 4
    gate = parser._build_rdps_preflight([], [], hits=[{'dpd_raw': parsed}], battle_start_ms=0, battle_end_ms=1)
    assert any(row['reason'] == 'invalid_dpd_bucket_evidence' for row in gate['blockers'])


def test_integer_identifiers_never_round_through_binary_float():
    for text in ('9007199254740993', '2305843009213695148', '18446744073709551615'):
        assert parser._coerce_optional_int(text) == int(text)
        assert parser._coerce_int(text) == int(text)
    assert parser._coerce_optional_int('1e3') == 1000
    assert parser._coerce_optional_int('3.000') == 3
    for value in ('3.1', True, float('inf'), 'NaN', {}, 2.5):
        assert parser._coerce_optional_int(value) is None


def test_zero_rate_is_valid_but_conflicting_aliases_are_unresolved():
    assert parser._rdps_registry_effect_runtime_rate({'bb_key': 'rate'}, {'bb_values': {'rate': 0}}) == (0.0, 'rate')
    spec = {'bb_keys': ['rate', 'rate2']}
    assert parser._rdps_registry_effect_runtime_rate(spec, {'bb_values': {'rate': 0.2, 'rate2': 0.3}})[0] is None


def test_partial_effect_list_does_not_pass_registry_validation():
    entry = {'effects': [{'zone': 'atk', 'element': 'all', 'bb_key': 'atk'},
                         {'zone': 'dmg_inc', 'element': 'all', 'bb_key': 'dmg'}]}
    error = parser._rdps_registry_validation_error(entry, {'bb_values': {'atk': 0.2}})
    assert error is not None


def test_unrecognized_key_is_not_approved_by_its_name_or_a_wildcard():
    entry = {'effects': [{'zone': 'atk', 'element': 'all', 'bb_key': 'rate'}], 'allowed_bb_keys': ['*']}
    record = {'bb_values': {'rate': 0.2, 'unfamiliar_field': 3}}
    error = parser._rdps_registry_validation_error(entry, record)
    assert error['unknown_bb_keys'] == ['unfamiliar_field']
    entry['allowed_bb_keys'] = ['rate', 'unfamiliar_field']
    assert parser._rdps_registry_validation_error(entry, record) is None
    entry['allowed_bb_keys'] = ['*']
    record = {'bb_values': {'rate': 0.2}, 'bb_keys': ['unparsed_field']}
    assert parser._rdps_registry_validation_error(entry, record)['unknown_bb_keys'] == ['unparsed_field']


def test_conflicting_rate_does_not_override_explicit_constant_and_can_be_revalidated():
    entry = {'effects': [{'zone': 'atk', 'element': 'all', 'bb_keys': ['a', 'b'],
                          'rate': 0.1, 'value_source': 'constant'}]}
    record = {'bb_values': {'a': 0.2, 'b': 0.3}}
    assert parser._rdps_registry_validation_error(entry, record)['reason'] == 'conflicting_runtime_rate'
    record['bb_values'] = {'a': 0.2, 'b': 0.2}
    assert parser._rdps_registry_validation_error(entry, record) is None


def test_dynamic_parameters_require_each_declared_value(monkeypatch):
    monkeypatch.setattr(parser, '_rdps_registry_verified_effect_entry', lambda record, root_lookup_cache=None: {'dynamic': True})
    spec = {'zone': 'vuln_taken', 'element': 'all', 'base_bb_keys': ['base', 'base_alias'],
            'tick_bb_keys': ['tick'], 'max_bb_key': 'cap', 'delay_bb_key': 'delay', 'add_bb_key': 'add',
            'condition': {'type': 'damage_type_in', 'elements': ['pulse']}}
    record = {'event_key': 'buff_dynamic', 'packet_mapping': {'dynamic_effects': [spec]},
              'bb_values': {'base': 0, 'base_alias': 0, 'tick': 0.1, 'cap': 1, 'delay': 2, 'add': 0.2}}
    effects = parser._extract_dynamic_effect_specs(record)
    # A tick value without packet-backed discrete updates is not an observed
    # multiplier and must remain unresolved.
    assert effects == []
    assert record['rdps_unresolved_dynamic_effect'] is True
    record['bb_values'].pop('delay')
    assert parser._extract_dynamic_effect_specs(record) == []
    assert record['rdps_unresolved_dynamic_effect'] is True
    record['bb_values']['delay'] = 2
    record['bb_values']['base_alias'] = 0.5
    assert parser._extract_dynamic_effect_specs(record) == []
    record['bb_values']['base_alias'] = 0
    record['bb_values']['tick'] = -1
    assert parser._extract_dynamic_effect_specs(record) == []


def test_dynamic_literal_requires_declared_constant_source(monkeypatch):
    monkeypatch.setattr(parser, '_rdps_registry_verified_effect_entry', lambda record, root_lookup_cache=None: {
        'dynamic': True, 'dynamic_effects': [{'zone': 'atk', 'element': 'all', 'base_rate': 0.2}]})
    record = {'packet_mapping': {'dynamic_effects': [{'zone': 'atk', 'element': 'all', 'base_rate': 0.2}]}, 'bb_values': {}}
    assert parser._extract_dynamic_effect_specs(record) == []
    record['packet_mapping']['value_source'] = 'constant'
    assert parser._extract_dynamic_effect_specs(record)[0]['base_rate'] == 0.2


def test_dynamic_arithmetic_overflow_is_not_silently_ignored():
    window = {'uid': 'x', 'start_ts_ms': 0, 'dynamic_effects': [{'zone': 'atk', 'element': 'all', 'tick_rate': 1e308}]}
    assert parser._window_effects_at_ts(window, 10000) == []
    gate = parser._build_rdps_preflight([], [window], hits=[], battle_start_ms=0, battle_end_ms=10000)
    assert gate['ok'] is False


def test_unsupported_condition_blocks_strict_allocation():
    hit = {'seq': 1, 'character_key': 'chr_0004_pelica', 'target_enemy_key': 'eny_0051_rodin',
           'hit_value': 100, 'packet_hit_value': 100, 'ts_ms': 500, 'skill_key': 'known',
           'damage_element': 'pulse', 'damage_school': 'spell', 'damage_element_source': 'official_action_unit'}
    window = {'uid': 'w', 'event_key': 'known_buff', 'source_character_key': 'chr_0023_antal',
              'target_character_key': 'chr_0004_pelica', 'start_ts_ms': 0, 'end_ts_ms': 1000,
              'zone_effects': [{'zone': 'atk', 'element': 'all', 'rate': 0.2, 'condition': {'type': 'future_condition'}}]}
    parser._allocate_rdps_for_hit(hit, [window])
    gate = parser._build_rdps_preflight([], [window], hits=[hit], battle_start_ms=0, battle_end_ms=1000)
    assert gate['ok'] is False
    assert any(row['reason'] == 'unresolved_effect_condition' for row in gate['blockers'])


def test_skill_family_and_combo_context_require_explicit_packet_metadata():
    assert parser._resolve_combo_consume_context({
        'skill_key': 'chr_0028_wulfa_normal_skill_1',
    }) is None
    assert parser._resolve_skill_family_key('chr_0028_wulfa_ult_attack') == 'chr_0028_wulfa_ult_attack'
    assert parser._cast_skill_family_key({
        'skill': 'chr_0028_wulfa_ult_attack',
        'source_character_key': 'chr_0028_wulfa',
    }) == 'chr_0028_wulfa_ult_attack'


def test_dungeon_context_never_supplies_enemy_identity():
    assert parser._infer_context_enemy_hint(
        '[10:00:00.000] DUNGEON_CONTEXT dungeonId=dung01_bossrush01_04'
    ) is None


def test_numeric_buff_context_conflict_stays_raw(monkeypatch):
    monkeypatch.setattr(parser, '_packet_numeric_buff_hint', lambda _value: {})
    monkeypatch.setattr(
        parser,
        '_canonical_num_table_buff_number',
        lambda _value, character: {'chr_a': 'buff_a', 'chr_b': 'buff_b', None: None}.get(character),
    )
    assert parser._canonical_packet_buff_id('123', owner_key='chr_a', source_key='chr_b') == '123'
