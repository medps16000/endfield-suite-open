from parser_core.battle_log_parser import _infer_skill_action_damage_element, parse_raw_battle_log_text


def test_resolved_hp_unit_keeps_its_own_element_separate_from_poise():
    # Full current-root decoding resolves HP unit 0 as cold. Physical belongs
    # to the distinct Poise unit 1 and is never a replacement HP attribute.
    assert _infer_skill_action_damage_element('chr_0027_tangtang_normal_skill_water_projhit_1', 13, 0) == 'cryst'
    assert _infer_skill_action_damage_element('chr_0027_tangtang_normal_skill_water_projhit_1', 13, 1) is None


def test_real_trace_damage_elements_use_hp_and_buff_action_identity():
    # Minimal fields from trace_20260909-185105, last successful battle.
    text = '\n'.join([
        '[19:58:21.000] SKILL_CAST_START owner=chr_0026_lastrite skill=chr_0026_lastrite_normal_skill_projhit inst=1 skillSource=Summon',
        '[19:58:22.698] HP_V2 #6621 hit=16583 eHP=3802384 skill="chr_0026_lastrite_skill_1352" templateIntId=1352 actionId=7 origTemplateIntId=1746 damageUnitIndex=0 src=chr_0026_lastrite tgt=eny_0082_hsbear_race007',
        '[19:58:22.698] HP_V2 #6622 hit=16583 eHP=3785802 skill="chr_0026_lastrite_skill_1352" templateIntId=1352 actionId=10 origTemplateIntId=1746 damageUnitIndex=0 src=chr_0026_lastrite tgt=eny_0082_hsbear_race007',
        '[19:58:43.264] HP_V2 #6795 hit=14382 eHP=1296412 skill="chr_0027_tangtang_normal_skill_water_projhit_1" templateIntId=2426 actionId=13 origTemplateIntId=2383 damageUnitIndex=0 src=chr_0027_tangtang tgt=eny_0082_hsbear_race007',
    ])
    parsed = parse_raw_battle_log_text(text, include_rdps_debug=True)
    hits = parsed['debug_hits']
    assert [hit['damage_element'] for hit in hits] == ['cryst', 'cryst', 'cryst']
    assert [hit['skill_key'] for hit in hits[:2]] == ['buff_chr_0026_lastrite_normal_skill_phantom'] * 2
    assert sum(hit['hit_value'] for hit in hits) == 47548
    # This isolated hit-only fixture now has complete damage-unit evidence.
    assert parsed['rdps_damage_basis']['rdps_strict_ok'] is True
    missing_action = parse_raw_battle_log_text(text.replace('actionId=13 ', 'actionId=unknown '))
    assert missing_action['rdps_damage_basis']['rdps_strict_ok'] is False


def test_explicit_missing_or_poise_unit_is_not_a_damage_fallback():
    key = 'chr_0027_tangtang_normal_skill'
    assert _infer_skill_action_damage_element(key, 19, 99) is None
    assert _infer_skill_action_damage_element(key, 19, 1) is None


def test_phantom_requires_matching_action_unit_and_unambiguous_namespace(monkeypatch):
    import parser_core.battle_log_parser as parser
    key = 'chr_0026_lastrite_skill_1352'
    char = 'chr_0026_lastrite'
    assert parser._canonical_runtime_buff_skill_id(key, char, action_id=99, damage_unit_index=0) is None
    assert parser._canonical_runtime_buff_skill_id(key, char, action_id=7, damage_unit_index=1) is None
    assert parser._canonical_runtime_buff_skill_id(key, char, action_id=7, damage_unit_index=0) == 'buff_chr_0026_lastrite_normal_skill_phantom'
    monkeypatch.setattr(parser, '_canonical_num_table_skill_id', lambda *args: 'some_real_skill')
    assert parser._canonical_runtime_buff_skill_id(key, char, action_id=7, damage_unit_index=0) is None


def test_action_without_unit_index_does_not_choose_arbitrary_element(monkeypatch):
    import parser_core.battle_log_parser as parser
    monkeypatch.setattr(parser, '_load_skill_action_damage_units', lambda key: {
        0: [
            {'index': 0, 'damage_attribute_type': 'hp', 'element': 'physical'},
            {'index': 1, 'damage_attribute_type': 'hp', 'element': 'cryst'},
        ],
    })
    assert parser._infer_skill_action_damage_element('mixed_skill', 0) is None
    assert parser._infer_skill_action_damage_element('mixed_skill', 0, 1) == 'cryst'
