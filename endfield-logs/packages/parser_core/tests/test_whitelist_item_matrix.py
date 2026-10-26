"""Every approved weapon/set row: text, current packet field and allocation.

Fixtures declare observed final BB values and explicit instance/lifetime facts;
they do not infer triggers from casts or multiply already-final values again.
"""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from parser_core import battle_log_parser as parser


ALL = ('physical', 'fire', 'pulse', 'cryst', 'natural', 'ether')
SPELL = ('fire', 'pulse', 'cryst', 'natural')
ATTACKER, PROVIDER, OTHER = 'chr_0016_laevat', 'chr_0023_antal', 'chr_0025_ardelia'
RATE = .2375

# key, exact item, actual packet field, zone, approved elements, recipient.
# Link uses its current 3605 parent-creation route, retaining approval 3606's key.
CASES = [
    ('buff_wpn_funnel_0005_atk_up', 'wpn_funnel_0005', 'atk_up', 'atk', ALL, 'attacker'),
    ('buff_equipsuit_usp_02_AddAttack', 'suit_usp02', 'dmg_up', 'dmg_inc', ALL, 'attacker'),
    ('buff_wpn_pistol_0011_valid2', 'wpn_pistol_0011', 'spell_damage_taken_up', 'vuln_taken', SPELL, 'enemy'),
    ('buff_wpn_funnel_0004_up', 'wpn_funnel_0004', 'dmg_up', 'dmg_inc', ('physical', 'pulse'), 'attacker'),
    ('buff_wpn_funnel_0006_valid', 'wpn_funnel_0006', 'spell_dmg_up', 'dmg_inc', SPELL, 'attacker'),
    ('buff_wpn_funnel_0008_magic_damage_taken_up', 'wpn_funnel_0008', 'spell_damage_taken_up', 'vuln_taken', SPELL, 'enemy'),
    ('buff_wpn_funnel_0010_atk_up', 'wpn_funnel_0010', 'atk_up', 'atk', ALL, 'attacker'),
    ('buff_wpn_funnel_0011_valid', 'wpn_funnel_0011', 'spell_dmg_up', 'dmg_inc', SPELL, 'attacker'),
    ('buff_wpn_funnel_0013_valid', 'wpn_funnel_0013', 'spell_taken_up', 'vuln_taken', SPELL, 'enemy'),
    ('buff_wpn_funnel_0014_hp', 'wpn_funnel_0014', 'hp_up', 'none', (), 'non_rdps'),
    ('buff_wpn_funnel_0014_up', 'wpn_funnel_0014', 'dmg_up', 'dmg_inc', ('fire', 'pulse'), 'attacker'),
    ('buff_wpn_pistol_0009_dmg_taken_up_c', 'wpn_pistol_0009', 'damage_taken_up_cryst', 'vuln_taken', ('cryst',), 'enemy'),
    ('buff_wpn_pistol_0009_dmg_taken_up_f', 'wpn_pistol_0009', 'damage_taken_up_fire', 'vuln_taken', ('fire',), 'enemy'),
    ('buff_wpn_pistol_0009_dmg_taken_up_n', 'wpn_pistol_0009', 'damage_taken_up_natural', 'vuln_taken', ('natural',), 'enemy'),
    ('buff_wpn_pistol_0009_dmg_taken_up_p', 'wpn_pistol_0009', 'damage_taken_up_pulse', 'vuln_taken', ('pulse',), 'enemy'),
    ('buff_wpn_sword_0012_atk_up', 'wpn_sword_0012', 'atk_up2', 'atk', ALL, 'attacker'),
    ('buff_wpn_sword_0013_atk_up', 'wpn_sword_0013', 'atk_up_ex', 'atk', ALL, 'attacker'),
    ('buff_wpn_lance_0007_dmgup2', 'wpn_lance_0007', 'dmg_up2', 'dmg_inc', ('physical', 'fire'), 'attacker'),
    ('buff_wpn_sword_0016_team', 'wpn_sword_0016', 'pd_up_valid', 'dmg_inc', ('physical',), 'attacker'),
    ('buff_wpn_sword_0016_valid', 'wpn_sword_0016', 'pd_up', 'dmg_inc', ('physical',), 'attacker'),
    ('buff_wpn_sword_0019_up', 'wpn_sword_0019', 'dmg_up', 'dmg_inc', ('fire', 'pulse'), 'attacker'),
    ('buff_wpn_sword_0020_cryst', 'wpn_sword_0020', 'cryst_dmg_up', 'vuln_taken', ('cryst',), 'enemy'),
    ('buff_equipsuit_combosuit_01_adddamage', 'suit_atb01', 'dmg_up', 'dmg_inc', ALL, 'attacker'),
    ('buff_wpn_lance_0015_atkup', 'wpn_lance_0015', 'atk_up', 'atk', ALL, 'attacker'),
    ('buff_wpn_lance_0015_dmgup', 'wpn_lance_0015', 'dmg_up', 'dmg_inc', ('fire',), 'attacker'),
    ('buff_wpn_funnel_0016_will_atk', 'wpn_funnel_0016', 'spell_dmg_taken_up', 'vuln_taken', SPELL, 'enemy'),
    ('buff_wpn_funnel_0016_will_dmg', 'wpn_funnel_0016', 'spell_dmg_taken_up2', 'vuln_taken', SPELL, 'enemy'),
    ('buff_wpn_funnel_0018_layer_teammates', 'wpn_funnel_0018', 'atk_up', 'atk', ALL, 'attacker'),
    ('buff_wpn_lance_0016_dmgup', 'wpn_lance_0016', 'spell_dmg_up', 'dmg_inc', SPELL, 'attacker'),
    ('buff_wpn_sword_0026_celebration', 'wpn_sword_0026', 'dmg_up', 'dmg_inc', ALL[:-1], 'attacker'),
    ('buff_wpn_lance_0014_damageup', 'wpn_lance_0014', 'atk_up', 'atk', ALL, 'attacker'),
    ('buff_equipsuit_fninflict_01_firedamageadd', 'suit_fire_natr01', 'fire_dmg_up', 'dmg_inc', ('fire',), 'self'),
    ('buff_equipsuit_fninflict_01_poisedamageadd', 'suit_fire_natr01', 'nature_dmg_up', 'dmg_inc', ('natural',), 'self'),
    ('buff_wpn_sword_0006_valid', 'wpn_sword_0006', 'normal_atk_up_valid', 'dmg_inc', ALL, 'self'),
    ('buff_wpn_pistol_0012_natural', 'wpn_pistol_0012', 'nature_dmg_up_mult', 'dmg_inc', ('natural',), 'self'),
    ('buff_wpn_funnel_0017_layer', 'wpn_funnel_0017', 'pulse_dmg_up2', 'dmg_inc', ('pulse',), 'self'),
    ('buff_wpn_claym_0018_enemyvul', 'wpn_claym_0018', 'dmg_taken_up', 'vuln_taken', ALL, 'enemy'),
    ('buff_wpn_claym_0019_teamatk', 'wpn_claym_0019', 'spell_dmg_up', 'dmg_inc', SPELL, 'attacker'),
]
TEXT_ANCHORS = {
    'wpn_funnel_0005': '和自身属性不同的干员', 'suit_usp02': '其他队友造成的伤害',
    'wpn_pistol_0011': '通过战技或终结技施加', 'wpn_funnel_0004': '全队获得物理和电磁伤害',
    'wpn_funnel_0006': '通过战技施加', 'wpn_funnel_0008': '法术爆发',
    'wpn_funnel_0010': '通过自身技能治疗后', 'wpn_funnel_0011': '通过自身连携技造成',
    'wpn_funnel_0013': '腐蚀', 'wpn_funnel_0014': '通过自身连携技造成',
    'wpn_pistol_0009': '受到对应属性的伤害', 'wpn_sword_0012': '通过自身技能恢复技力或获得',
    'wpn_sword_0013': '小队内其他干员获得一半的效果', 'wpn_lance_0007': '全队物理和灼热伤害',
    'wpn_sword_0016': '小队内其他干员造成的物理伤害', 'wpn_sword_0019': '全队造成的灼热和电磁伤害',
    'wpn_sword_0020': '受到的寒冷伤害', 'suit_atb01': '通过技能恢复技力后',
    'wpn_lance_0015': '全队造成的灼热伤害', 'wpn_funnel_0016': '装备者意志值>智识值',
    'wpn_funnel_0018': '通过连携技施加', 'wpn_lance_0016': '通过连携技治疗小队内其他干员',
    'wpn_sword_0026': '当队伍进入战斗时', 'wpn_lance_0014': '受到装备者治疗的干员',
    'suit_fire_natr01': '当装备者施加', 'wpn_sword_0006': '施放终结技时，获得普通攻击伤害',
    'wpn_pistol_0012': '消耗层数', 'wpn_funnel_0017': '电磁增幅',
    'wpn_claym_0018': '使目标敌人受到的伤害', 'wpn_claym_0019': '后，全队法术伤害',
}


def record_for(case, rate=RATE):
    key, item, field, zone, elements, recipient = case
    own = recipient == 'self'
    target_enemy = recipient == 'enemy'
    record = {'uid': '7001', 'event_key': key, 'raw_event_key': key,
              'source_character_key': ATTACKER if own else PROVIDER,
              'source_actor_id': '1001' if own else '2001',
              'target_character_key': 'eny_0051_rodin' if target_enemy else ATTACKER,
              'target_enemy_key': 'eny_0051_rodin' if target_enemy else None,
              'target_actor_id': '3001' if target_enemy else '1001',
              'bb_values': {field: rate, 'duration': 10, 'max_stack': 4},
              'packet_mapping': {'weapon_id' if item.startswith('wpn_') else 'suit_id': item}}
    if key == 'buff_wpn_sword_0026_celebration':
        record['bb_values']['phy_spell_up'] = 30.0
    if item in {'wpn_claym_0018', 'wpn_claym_0019'}:
        # Explicit fixture of the loadout observed at this Buff's creation.
        record['source_weapon_template'] = item
    if key == 'buff_wpn_funnel_0018_layer_teammates':
        template = 'buff_wpn_funnel_0018_layer'
        record.update(event_key=template, raw_event_key='3605')
        record['origin_observations'] = [{'_boundCreateRecord': True, 'buffUid': '7001', 'buffTemplate': template,
            'sourceActorId': '2001', 'targetActorId': '1001', 'environmentType': 'Skill', 'ownerType': '0',
            'environmentInstId': '6001', 'templateStrId': 'sk_wpn_funnel_0018', 'templateIntId': None,
            'actionId': 4, 'operationSeq': 5, 'triggerType': 0}]
    return record


def window_for(record):
    effects = parser._collect_zone_effects(record)
    return {**record, 'zone_effects': effects, 'start_ts_ms': 0, 'end_ts_ms': 1000,
            'start_line_no': 1, 'requires_enable_observation': True,
            'enable_observations': [{'operationSeq': 6, 'ts_ms': 0, 'line_no': 2, 'enabled': True}]}


def hit_for(case, element='fire', damage=123.75):
    return {'character_key': ATTACKER, 'source_actor_id': '1001', 'target_enemy_key': 'eny_0051_rodin',
            'target_actor_id': '3001', 'hit_value': damage, 'ts_ms': 500, 'line_no': 10,
            'damage_element': element, 'damage_school': 'physical' if element == 'physical' else 'spell',
            'damage_decorate_mask': 0x80 if case[0] == 'buff_wpn_sword_0006_valid' else 0x100}


def test_matrix_covers_every_current_weapon_and_suit_approval_exactly_once():
    registry = parser._load_rdps_semantics_registry()['verified_effects']
    expected = {key for key in registry if key.startswith(('buff_wpn_', 'buff_equipsuit_'))}
    assert len(CASES) == len(expected)
    assert {case[0] for case in CASES} == expected


@pytest.mark.parametrize('case', [row for row in CASES if row[1] in {'wpn_claym_0018', 'wpn_claym_0019'}], ids=lambda case: case[0])
@pytest.mark.parametrize('change', ['missing_creation_weapon', 'different_creation_weapon', 'missing_source_actor', 'missing_target_actor', 'invalid_uid'])
def test_new_weapons_require_observed_creation_loadout_and_actor_identity(case, change):
    record = record_for(case)
    assert parser._rdps_registry_verified_effect_entry(record) is not None
    if change == 'missing_creation_weapon':
        record.pop('source_weapon_template')
    elif change == 'different_creation_weapon':
        record['source_weapon_template'] = 'wpn_claym_0003'
    elif change == 'missing_source_actor':
        record['source_actor_id'] = None
    elif change == 'missing_target_actor':
        record['target_actor_id'] = None
    else:
        record['uid'] = '4294967295'
    assert parser._rdps_registry_verified_effect_entry(record) is None
    assert not any(effect.get('external_rdps_allowed') for effect in parser._collect_zone_effects(record))


@pytest.mark.parametrize('case', CASES, ids=lambda case: case[0])
def test_each_item_has_matching_text_current_field_and_approval(case):
    key, item, field, zone, elements, recipient = case
    if item in {'wpn_claym_0018', 'wpn_claym_0019'}:
        # These new items have official text rather than an old AKE snapshot.
        text_proof = json.loads((Path(__file__).parent / 'fixtures' /
            'whitelist_new_item_official_text_20261016.json').read_text(encoding='utf8'))
        catalog = json.loads((Path(__file__).resolve().parents[3] /
            'data/local_static/skill/item_damage_baseline_catalog.json').read_text(encoding='utf8'))
        assert all(catalog['sourceTables'][table] == digest for table, digest in text_proof['sourceTables'].items())
        text = text_proof['rows'][item]
        assert parser._load_weapon_catalog()[item]['weapon_skill_ids'][text['skillSlot']] == text['skillId']
        assert TEXT_ANCHORS[item] in text['description']
    else:
        texts = json.loads((Path(__file__).resolve().parents[3] /
                            'data/packet_semantics/akedata_rdps_text_snapshot.json').read_text(encoding='utf8'))
        records = [row for row in texts['records'] if row.get('source_id') == item]
        assert any(TEXT_ANCHORS[item] in row.get('description', '') for row in records)
    record = record_for(case)
    root = parser._load_current_buff_roots()[record['event_key']]
    assert root['$type'] == 'Beyond.Gameplay.Core.BuffData'
    window = window_for(record)
    effects = window['zone_effects']
    if recipient == 'non_rdps':
        assert parser._rdps_registry_known_non_rdps_entry(record)
        assert effects == []
        assert {row['attributeType'] for row in root['attributeModifier']['attributeModifiers']} == {'MaxHp'}
    else:
        assert effects
        assert all(effect['external_rdps_allowed'] for effect in effects)
        assert all(effect['rdps_whitelist_keys'] == [key] for effect in effects)
        matching = [effect for effect in effects if effect['zone'] == zone]
        assert matching and all(effect['bb_key'] == field and effect['rate'] == RATE for effect in matching)


@pytest.mark.parametrize('case', CASES, ids=lambda case: case[0])
@pytest.mark.parametrize('element', ALL)
def test_each_item_allocates_only_its_approved_damage_elements(case, element):
    window = window_for(record_for(case))
    hit = hit_for(case, element)
    result = parser._allocate_rdps_for_hit(hit, [window], include_debug=True)
    externally_applicable = case[5] not in {'self', 'non_rdps'} and element in case[4]
    expected = {ATTACKER: 100.0, PROVIDER: 23.75} if externally_applicable else {ATTACKER: 123.75}
    assert result['contributions_map'] == pytest.approx(expected)
    assert sum(result['contributions_map'].values()) == pytest.approx(123.75)
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize('case', CASES, ids=lambda case: case[0])
@pytest.mark.parametrize('boundary', ['expired', 'disabled', 'other_target_instance', 'missing_source', 'missing_value'])
def test_each_item_requires_its_observed_source_target_value_and_lifetime(case, boundary):
    record = record_for(case)
    if boundary == 'missing_source':
        record['source_character_key'] = None
    if boundary == 'missing_value':
        record['bb_values'].pop(case[2])
    window = window_for(record)
    hit = hit_for(case, next(iter(case[4]), 'fire'))
    if boundary == 'expired':
        hit['ts_ms'] = 1001
    if boundary == 'disabled':
        window['enable_observations'][0]['enabled'] = False
    if boundary == 'other_target_instance':
        hit['target_actor_id' if case[5] == 'enemy' else 'source_actor_id'] = '9999'
    result = parser._allocate_rdps_for_hit(hit, [window])
    assert result['contributions_map'] == {ATTACKER: 123.75}


@pytest.mark.parametrize('case', CASES, ids=lambda case: case[0])
def test_conflicting_item_identity_does_not_borrow_another_items_approval(case):
    record = record_for(case)
    record['packet_mapping'] = {'weapon_id' if case[1].startswith('wpn_') else 'suit_id': 'different_exact_item'}
    assert parser._rdps_registry_verified_effect_entry(record) is None
    assert all(effect['external_rdps_allowed'] is False for effect in parser._collect_zone_effects(record))


@pytest.mark.parametrize('case', [row for row in CASES if row[5] == 'self'], ids=lambda case: case[0])
def test_wearer_only_items_retain_their_numeric_self_baseline(case):
    own = window_for(record_for(case))
    element = case[4][0]
    external = {'uid': '8001', 'event_key': 'independent_approved_fixture', 'source_character_key': OTHER,
                'target_character_key': ATTACKER, 'target_actor_id': '1001', 'start_ts_ms': 0, 'end_ts_ms': 1000,
                'zone_effects': [{'zone': 'dmg_inc', 'element': element, 'rate': .2,
                                  'external_rdps_allowed': True, 'rdps_whitelist_keys': ['independent_approved_fixture']}]}
    result = parser._allocate_rdps_for_hit(hit_for(case, element, 143.75), [own, external])
    assert result['contributions_map'] == pytest.approx({ATTACKER: 123.75, OTHER: 20.0})
    other = record_for(case)
    other.update(source_character_key=PROVIDER, source_actor_id='2001')
    assert parser._rdps_registry_verified_effect_entry(other) is None
    assert all(effect['external_rdps_allowed'] is False for effect in parser._collect_zone_effects(other))


def test_normal_attack_weapon_does_not_apply_to_other_skill_categories():
    case = next(row for row in CASES if row[0] == 'buff_wpn_sword_0006_valid')
    own = window_for(record_for(case))
    external = {'uid': '8001', 'event_key': 'independent_approved_fixture', 'source_character_key': OTHER,
                'target_character_key': ATTACKER, 'target_actor_id': '1001', 'start_ts_ms': 0, 'end_ts_ms': 1000,
                'zone_effects': [{'zone': 'dmg_inc', 'element': 'fire', 'rate': .2,
                                  'external_rdps_allowed': True, 'rdps_whitelist_keys': ['independent_approved_fixture']}]}
    for mask in (0x100, 0x200, 0x2000):
        hit = hit_for(case, 'fire', 120)
        hit['damage_decorate_mask'] = mask
        result = parser._allocate_rdps_for_hit(hit, [own, external])
        assert result['contributions_map'] == pytest.approx({ATTACKER: 100.0, OTHER: 20.0})


def test_celebration_arts_and_damage_share_the_existing_cross_zone_contract():
    case = next(row for row in CASES if row[0] == 'buff_wpn_sword_0026_celebration')
    # Explicit physical abnormal mask activates its 30 arts points. Ordinary
    # skill hits above only receive the five-element damage property.
    hit = hit_for(case, 'physical', 160.875)
    hit['damage_decorate_mask'] = 0x8000000
    result = parser._allocate_rdps_for_hit(hit, [window_for(record_for(case))], include_debug=True)
    assert result['contributions_map'] == pytest.approx({ATTACKER: 100.0, PROVIDER: 60.875})
    assert {zone['zone'] for zone in result['zones']} == {'dmg_inc', 'arts_strength'}
