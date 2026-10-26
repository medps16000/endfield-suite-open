"""Exact current damage-unit categories supplement absent runtime masks positively."""
from pathlib import Path

import pytest

from parser_core import battle_log_parser as parser


SOURCE = 'current_typed_skill_or_buff_exact_unit_generation_only'
CATEGORIES = [(17, 0x4), (17, 0x80), (17, 0x400), (17, 0x20000),
              (32, 0x100), (28, 0x200), (33, 0x2000)]


@pytest.mark.parametrize('attr,mask', CATEGORIES)
def test_positive_category_applies_without_changing_runtime_evidence(attr, mask):
    assert parser._attr_type_applies_to_skill(attr, 'unrelated_name',
        static_damage_decorate_mask=mask, static_damage_decorate_mask_source=SOURCE) is True
    assert parser._attr_type_applies_to_skill(attr, 'normal_skill_combo_ultimate_attack',
        static_damage_decorate_mask=0, static_damage_decorate_mask_source=SOURCE) is None


@pytest.mark.parametrize('actual,expected', [(0, False), (0x100, False), (0x80, True),
                                           (False, None), (-1, None), ('128', None), (2**64, None)])
def test_runtime_counterevidence_and_invalid_values_never_use_static_fallback(actual, expected):
    assert parser._attr_type_applies_to_skill(17, None, damage_decorate_mask=actual,
        static_damage_decorate_mask=0x80, static_damage_decorate_mask_source=SOURCE) is expected


@pytest.mark.parametrize('mask,source', [(0x80, None), (0x80, 'name_guess'),
                                       (True, SOURCE), (-1, SOURCE), (2**64, SOURCE), ('128', SOURCE)])
def test_nonexact_or_invalid_static_category_remains_unknown(mask, source):
    assert parser._attr_type_applies_to_skill(17, None, static_damage_decorate_mask=mask,
        static_damage_decorate_mask_source=source) is None


@pytest.mark.parametrize('check,required,static,passed', [
    ('HasAny', 0x20484, 0x80, True), ('HasAny', 0x300, 0x100, True),
    ('HasAll', 0x300, 0x300, True), ('HasAll', 0x300, 0x100, False),
    ('HasAny', 0x80000080, 0x80, True), ('HasAll', 0x80000080, 0x80000080, False),
    ('HasAny', 0x80000000, 0x80000000, False), ('HasAny', 0x1000, 0x1000, False),
    ('HasAny', 0x80, 0x100, False), ('HasAll', 0, 0, True),
])
def test_conditions_accept_only_proven_positive_category_bits(check, required, static, passed):
    hit = {'damage_decorate_mask': None, 'static_damage_decorate_mask': static,
           'static_damage_decorate_mask_source': SOURCE}
    condition = {'condition': {'type': 'damage_decorate_mask', 'check': check, 'mask': required}}
    failure = parser._effect_condition_failure(condition, hit)
    assert (failure is None) is passed
    if not passed:
        assert failure['reason_group'] == 'condition_unobservable'
    assert hit['damage_decorate_mask'] is None


@pytest.mark.parametrize('actual,group', [(0, 'condition_unsatisfied'), (0x100, 'condition_unsatisfied'),
                                        (False, 'condition_unobservable'), (-1, 'condition_unobservable'),
                                        ('128', 'condition_unobservable'), (2**64, 'condition_unobservable')])
def test_mask_condition_runtime_value_takes_priority(actual, group):
    condition = {'condition': {'type': 'damage_decorate_mask', 'check': 'HasAny', 'mask': 0x80}}
    hit = {'damage_decorate_mask': actual, 'static_damage_decorate_mask': 0x80,
           'static_damage_decorate_mask_source': SOURCE}
    assert parser._effect_condition_failure(condition, hit)['reason_group'] == group


@pytest.mark.parametrize('attr,group,mask', [(17, 'normal', 0x80), (28, 'ultimate', 0x200),
                                         (32, 'skill', 0x100), (33, 'combo', 0x2000)])
def test_equipment_category_and_skill_group_accept_exact_positive_evidence(attr, group, mask):
    entry = {'zone': 'dmg_inc', 'element': 'all', 'rate': 0.2, 'attr_type': attr,
             'evidence_kind': 'official_EquipTable_attribute', 'skill_groups': [group]}
    hit = {'damage_element': 'fire', 'damage_school': 'spell', 'static_damage_decorate_mask': mask,
           'static_damage_decorate_mask_source': SOURCE}
    assert parser._static_entry_applies_to_hit(entry, hit) is True
    assert hit.get('damage_decorate_mask') is None
    missing = {**hit, 'static_damage_decorate_mask': 0}
    assert parser._static_entry_applies_to_hit(entry, missing) is False
    assert missing['rdps_unresolved_effect_conditions'] is True
    # An explicitly captured total includes this static attribute already.
    assert parser._static_entry_applies_to_hit(entry, {**hit, 'baseline': {attr: 0.5}}) is False
    assert parser._static_entry_applies_to_hit(entry, {**hit, 'baseline': {str(attr): 0.0}}) is False


@pytest.mark.parametrize('attr,mask', [(17, 0x80), (28, 0x200), (32, 0x100), (33, 0x2000)])
def test_dynamic_category_and_observed_final_attribute_share_one_denominator(attr, mask):
    # This fixture isolates allocation math, as existing condition tests do.
    # Actual approval extraction is separately exercised for Melted Flame below.
    hit = {'character_key': 'chr_0006_wolfgd', 'target_enemy_key': 'eny_0051_rodin',
           'hit_value': 150, 'ts_ms': 500, 'damage_element': 'fire', 'damage_school': 'spell',
           'static_damage_decorate_mask': mask, 'static_damage_decorate_mask_source': SOURCE,
           'baseline': {attr: 0.5},
           'static_multiplier_entries': [{'zone': 'dmg_inc', 'element': 'all', 'rate': 0.3,
               'attr_type': attr, 'evidence_kind': 'official_EquipTable_attribute'}]}
    effect = {'zone': 'dmg_inc', 'element': 'all', 'rate': 0.2, 'attr_type': attr}
    window = {'uid': '1', 'event_key': 'explicit_math_fixture', 'source_character_key': 'chr_0023_antal',
              'target_character_key': hit['character_key'], 'start_ts_ms': 0, 'end_ts_ms': 1000,
              'zone_effects': [effect]}
    result = parser._allocate_rdps_for_hit(hit, [window], include_debug=True)
    assert result['contributions_map'] == pytest.approx({'chr_0006_wolfgd': 130, 'chr_0023_antal': 20})


@pytest.mark.parametrize('template,action,attr', [
    ('chr_0006_wolfgd_attack1_projhit01', 0, 17),
    ('chr_0006_wolfgd_normal_skill_projhit', 1, 32),
    ('chr_0006_wolfgd_ultimate_skill', 35, 28),
    ('chr_0004_pelica_combo_skill_projhit', 11, 33),
    # The exact unit is NormalAttack despite this resource name.
    ('chr_0016_laevat_normal_skill_projhit', 1, 17),
])
def test_full_hp_protobuf_service_bridge_pipeline_keeps_static_category(tmp_path, template, action, attr):
    service = pytest.importorskip('endfield_pcap.service')
    bridge_module = pytest.importorskip('endfield_pcap.trace_bridge')
    proto = pytest.importorskip('endfield_pcap.proto_generated.CS_BATTLE_OP_pb2')
    ids = [int(key) for key, value in parser._load_num_id_str_skill_map().items() if value == template]
    assert len(ids) == 1
    numeric = ids[0]
    packet = proto.CS_BATTLE_OP()
    op = packet.client_data.op_list.add(op_type=8, seq_id=20, owner_id=200, generation=1, client_tick_tms=1000)
    trigger = op.trigger_action_op_data
    trigger.owner_id = 200
    trigger.owner_type = 3
    trigger.inst_id = 20001
    trigger.template_id.type = 0
    trigger.template_id.int_id = numeric
    trigger.action.action_type = 1
    trigger.action.action_id = action
    damage = trigger.action.damage_action
    damage.attacker_id = 200
    damage.original_source_template_id.CopyFrom(trigger.template_id)
    damage.details.add(target_id=300, damage_unit_index=0, value=150.0, cur_hp=850.0)
    pipeline = service.SessionPipeline.__new__(service.SessionPipeline)
    pipeline.session_id = 'synthetic-category-proof'
    pipeline.skill_levels_by_battle_inst = {200: {pipeline._template_key('Skill', numeric, None): 1}}
    emitted = []
    pipeline._emit_event = emitted.append
    pipeline._emit_battle_events(proto.CS_BATTLE_OP.FromString(packet.SerializeToString()), 1000)
    path = tmp_path / 'hp-category.log'
    bridge = bridge_module.TraceBridge(path)
    bridge.id_to_name.update({200: 'chr_0016_laevat', 300: 'eny_0051_rodin'})
    for event in emitted:
        bridge.handle_event(event.as_dict())
    parsed = parser.parse_raw_battle_log_text(path.read_text(encoding='utf8'), include_rdps_debug=True)
    assert len(parsed['debug_hits']) == 1
    hit = parsed['debug_hits'][0]
    assert hit['damage_decorate_mask'] is None
    assert hit['static_damage_decorate_mask_source'] == SOURCE
    assert parser._attr_type_applies_to_skill(attr, None,
        static_damage_decorate_mask=hit['static_damage_decorate_mask'],
        static_damage_decorate_mask_source=hit['static_damage_decorate_mask_source']) is True
    if attr == 17:
        effects = parser._collect_zone_effects({'event_key': 'buff_wpn_sword_0006_valid',
            'bb_values': {'normal_atk_up_valid': 1.2}, 'source_character_key': 'chr_0016_laevat',
            'target_character_key': 'chr_0016_laevat', 'source_weapon_id': 'wpn_sword_0006'})
        assert len(effects) == 1 and effects[0]['attr_type'] == 17
        assert effects[0]['rdps_whitelist_keys'] == ['buff_wpn_sword_0006_valid']
        assert parser._effect_condition_failure(effects[0], hit) is None
        # Approval's self-recipient guard remains intact; a different character
        # cannot borrow this weapon effect as external contribution.
        foreign = parser._collect_zone_effects({'event_key': 'buff_wpn_sword_0006_valid',
            'bb_values': {'normal_atk_up_valid': 1.2}, 'source_character_key': 'chr_0016_laevat',
            'target_character_key': 'chr_0006_wolfgd', 'source_weapon_id': 'wpn_sword_0006'})
        assert foreign[0]['external_rdps_allowed'] is False
        window = {'uid': '1', 'event_key': 'buff_wpn_sword_0006_valid',
                  'source_character_key': hit['character_key'], 'target_character_key': hit['character_key'],
                  'start_ts_ms': hit['ts_ms'] - 1, 'end_ts_ms': hit['ts_ms'] + 1, 'zone_effects': effects}
        allocation = parser._allocate_rdps_for_hit(hit, [window], include_debug=True)
        assert allocation['external_pool'] == 0
        assert any(row['zone'] == 'dmg_inc' and row['self_rate'] == pytest.approx(1.2)
                   for row in allocation['zones'])


def test_live_capture_categories_prove_only_positive_scope():
    fixtures = Path(__file__).with_name('fixtures')
    counts = {17: 0, 28: 0, 32: 0, 33: 0}
    for name in ['combo_live_skill_20260914.log', 'combo_live_ultimate_20260914.log']:
        parsed = parser.parse_raw_battle_log_text((fixtures / name).read_text(encoding='utf8'), include_rdps_debug=True)
        for hit in parsed['debug_hits']:
            assert hit['damage_decorate_mask'] is None
            for attr in counts:
                applies = parser._attr_type_applies_to_skill(attr, hit['skill_key'],
                    static_damage_decorate_mask=hit['static_damage_decorate_mask'],
                    static_damage_decorate_mask_source=hit['static_damage_decorate_mask_source'])
                assert applies is not False  # Static absence never proves a runtime negative.
                counts[attr] += applies is True
    assert counts == {17: 1, 28: 5, 32: 3, 33: 0}


@pytest.mark.parametrize('mask', [0x4000, 0x8000, 0x10000, 0x40000000,
                                0x8, 0x10, 0x20, 0x100000, 0x4000000, 0x8000000,
                                0x400000, 0x800000, 0x1000000, 0x2000000])
def test_verified_anomaly_flags_are_consistent_between_eligibility_and_conditions(mask):
    hit = {'damage_decorate_mask': None, 'static_damage_decorate_mask': mask,
           'static_damage_decorate_mask_source': SOURCE}
    assert parser._is_arts_strength_damage_hit(hit) is True
    for check in ['HasAny', 'HasAll']:
        condition = {'condition': {'type': 'damage_decorate_mask', 'check': check, 'mask': mask}}
        assert parser._effect_condition_failure(condition, hit) is None
        assert parser._effect_condition_failure(condition, {**hit, 'static_damage_decorate_mask': 0})[
            'reason_group'] == 'condition_unobservable'
        assert parser._effect_condition_failure(condition, {**hit, 'damage_decorate_mask': 0})[
            'reason_group'] == 'condition_unsatisfied'
    assert hit['damage_decorate_mask'] is None


@pytest.mark.parametrize('template,action,credit', [
    ('buff_common_natural_natural_triggered', 5, 20),
    ('chr_0034_typhoea_attack1_01_projhit', 1, 0),
    ('buff_common_fire_fire_triggered', 5, 0),
])
def test_typhoea_current_arrow_field_buff_requires_natural_burst(template, action, credit):
    # Actual current templates/approval, with explicitly synthetic HP and Buff values.
    # A normal natural hit and a different element's burst cannot borrow this effect.
    text = (f'[00:00:01.000] HP_V2 #1 hit=120 cum=120 raw=120 packetFinalValue=120 '
            f'pHP=100 eHP=1000 skill="{template}" hits=1 actionId={action} damageUnitIndex=0 '
            'src=chr_0006_wolfgd tgt=eny_0051_rodin atk=chr_0006_wolfgd atkId=1001 tgtId=2001 seg=0 critFlag=0')
    hit = parser.parse_raw_battle_log_text(text, include_rdps_debug=True)['debug_hits'][0]
    record = {'event_key': 'buff_chr_0034_typhoea_combo_enemy_debuff',
              'source_character_key': 'chr_0034_typhoea', 'source_actor_id': '3001',
              'target_character_key': 'eny_0051_rodin',
              'target_enemy_key': 'eny_0051_rodin', 'target_actor_id': '2001',
              'bb_values': {'damage_up': 0.2}}
    effects = parser._collect_zone_effects(record)
    assert len(effects) == 1
    assert effects[0]['external_rdps_allowed'] is True
    assert effects[0]['rdps_whitelist_keys'] == ['buff_chr_0034_typhoea_combo_enemy_debuff']
    failure = parser._effect_condition_failure(effects[0], hit)
    assert (failure is None) is bool(credit)
    window = {**record, 'uid': '4001', 'start_ts_ms': 0, 'end_ts_ms': 2000, 'zone_effects': effects}
    result = parser._allocate_rdps_for_hit(hit, [window], include_debug=True)
    assert result['external_pool'] == pytest.approx(credit)
    assert result['attacker_share'] == pytest.approx(120 - credit)
    assert hit['damage_decorate_mask'] is None
    assert hit['static_damage_decorate_mask_source'] == SOURCE
    if credit:
        assert hit['damage_element'] == 'natural'
        assert hit['static_damage_decorate_mask'] == 0x2000000
        assert result['contributions_map']['chr_0034_typhoea'] == pytest.approx(20)
        counterexample = {**hit, 'damage_decorate_mask': 0x80}
        assert parser._effect_condition_failure(effects[0], counterexample)['reason_group'] == 'condition_unsatisfied'
