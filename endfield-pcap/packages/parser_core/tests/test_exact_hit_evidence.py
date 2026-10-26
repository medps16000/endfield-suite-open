import pytest
import json
from pathlib import Path

from parser_core import battle_log_parser as parser
from parser_core import damage_core
from parser_core.hit_debug import _skill_mapping_info
from parser_core.hit_debug import parse_hit_debug_log_text


CHAR = 'chr_0004_pelica'
SKILL = f'{CHAR}_proof_action'
HIT = ('[10:00:00.100] HP_V2 #1 hit=100 eHP=1000 skill="' + SKILL + '" '
       'src=' + CHAR + ' tgt=eny_0051_rodin actionId=0 damageUnitIndex=0')


@pytest.fixture
def exact_units(monkeypatch):
    actions = {0: [
        {'index': 0, 'damage_attribute_type': 'hp', 'element': 'pulse'},
        {'index': 1, 'damage_attribute_type': 'poise', 'element': 'physical'},
    ]}
    monkeypatch.setattr(parser, '_load_skill_action_damage_units', lambda key: actions if key == SKILL else {})
    return actions


@pytest.mark.parametrize('action,unit', [(99, 0), (None, 0), (0, None), (0, 1),
                                     (0, 99), (0, -1), (False, 0), (0, False),
                                     (0.5, 0), (0, 0.5), ('bad', 0), (0, 'bad')])
def test_incomplete_or_wrong_hp_unit_is_unknown(exact_units, action, unit):
    assert parser._infer_skill_action_damage_element(SKILL, action, unit) is None
    assert damage_core.infer_damage_element(SKILL, action_id=action, damage_unit_index=unit,
                                           dpd={'damageType': 4}) is None


def test_public_damage_core_has_no_dpd_element_fallback():
    assert damage_core.damage_element_from_dpd({'damageType': 4}) is None
    assert damage_core.infer_damage_element('unknown_skill', dpd={'damageType': 4}) is None


def test_exact_zero_indices_and_duplicate_records(exact_units):
    assert parser._infer_skill_action_damage_element(SKILL, 0, 0) == 'pulse'
    exact_units[0].append(dict(exact_units[0][0]))
    assert parser._infer_skill_action_damage_element(SKILL, 0, 0) is None


def test_unresolved_attribute_does_not_become_hp(exact_units):
    exact_units[0][0]['damage_attribute_type'] = ''
    assert parser._infer_skill_action_damage_element(SKILL, 0, 0) is None


def test_official_profile_lookup_does_not_expand_prefixes(monkeypatch):
    profile = {'resolved': True, 'elements': ['pulse']}
    monkeypatch.setattr(parser, '_load_official_skill_damage_catalog', lambda: {SKILL: profile})
    assert parser._resolve_official_skill_damage_profile(SKILL) is profile
    assert parser._resolve_official_skill_damage_profile(SKILL + '_invented_child') is None


def test_current_full_object_catalog_resolves_reported_damage_units():
    assert parser._infer_skill_action_damage_element('chr_0027_tangtang_normal_skill_water_projhit_1', 13, 0) == 'cryst'
    assert parser._infer_skill_action_damage_element('chr_0027_tangtang_normal_skill_water_projhit_1', 13, 1) is None
    assert parser._infer_skill_action_damage_element('buff_common_burning_status', 0, 0) == 'fire'
    unit = parser._load_skill_action_damage_units('chr_0030_zhuangfy_attack5_abilityrange')[11][0]
    assert unit['damage_decorate_mask'] == 2097280
    assert unit['damage_decorate_mask_source'] == 'current_typed_skill_or_buff_exact_unit_generation_only'


def test_current_skill_mask_is_diagnostic_not_current_hit_evidence():
    units = parser._load_skill_action_damage_units('chr_0016_laevat_attack1')[6]
    unit = next(row for row in units if row['index'] == 0)
    assert unit['damage_decorate_mask'] == 128
    assert unit['damage_decorate_mask_source'] == 'current_typed_skill_or_buff_exact_unit_generation_only'


def test_current_typed_buff_mask_only_proves_generation():
    units = parser._load_skill_action_damage_units('buff_common_pulse_pulse_triggered')[5]
    assert units[0]['damage_decorate_mask'] == 16777216
    assert units[0]['damage_decorate_mask_source'] == 'current_typed_skill_or_buff_exact_unit_generation_only'


def test_current_action_unit_mask_is_bound_to_exact_action_and_unit():
    units = parser._load_skill_action_damage_units('chr_0016_laevat_normal_skill')[36]
    unit = next(row for row in units if row['index'] == 0)
    assert unit['damage_decorate_mask'] == 4352



def test_scanned_or_partial_catalog_cannot_supply_strict_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(parser, '_repo_root', lambda: tmp_path)
    path = tmp_path / 'data/local_static/skill/damage_element_catalog.json'
    path.parent.mkdir(parents=True)
    payload = {'schemaVersion': 1, 'source': 'official_local_skilldata_memorypack',
               'skills': {SKILL: {'actions': {}, 'elements': ['pulse']}}}
    try:
        path.write_text(json.dumps(payload))
        parser._load_official_skill_damage_catalog.cache_clear()
        assert parser._load_official_skill_damage_catalog() == {}
        payload.update(schemaVersion=2, decoder={'kind': 'typed_root_deserialization'},
                       summary={'inputResourceCount': 1, 'completeRootCount': 1})
        payload['skills'][SKILL]['proof'] = {'actionBoundaries': 'typed_root_deserialization',
                                             'sourceBytes': 20, 'consumedBytes': 19}
        path.write_text(json.dumps(payload))
        parser._load_official_skill_damage_catalog.cache_clear()
        assert parser._load_official_skill_damage_catalog() == {}
    finally:
        parser._load_official_skill_damage_catalog.cache_clear()


def test_exact_hit_passes_evidence_gate_and_missing_index_fails(exact_units):
    known = parser.parse_raw_battle_log_text(HIT, include_rdps_debug=True)
    assert known['debug_hits'][0]['damage_element_source'] == 'official_action_unit'
    assert known['rdps_damage_basis']['rdps_strict_ok'] is True
    unknown = parser.parse_raw_battle_log_text(HIT.replace(' damageUnitIndex=0', ''), include_rdps_debug=True)
    assert unknown['debug_hits'][0]['damage_unit_index'] is None
    assert unknown['debug_hits'][0]['damage_element'] is None
    assert unknown['rdps_damage_basis']['rdps_strict_ok'] is False
    assert any(row['reason'] == 'unresolved_hit_evidence' for row in unknown['rdps_preflight']['blockers'])


def test_skipped_actor_remains_visible_and_blocks_partial_success(exact_units):
    unknown = HIT.replace('src=' + CHAR, 'src=id_999').replace('#1', '#2')
    parsed = parser.parse_raw_battle_log_text(HIT + '\n' + unknown)
    assert parsed['unresolved_hits'][0]['source_actor'] == 'id_999'
    assert parsed['unresolved_hits'][0]['packet_hit_value'] == '100'
    assert parsed['rdps_damage_basis']['rdps_strict_ok'] is False
    assert parsed['rdps_damage_basis']['unresolved_damage_actor_count'] == 1
    empty = parser.parse_raw_battle_log_text(unknown)
    assert empty['participants'] == []
    assert empty['rdps_damage_basis']['rdps_strict_ok'] is False


def test_buff_name_cannot_supply_source_actor(exact_units):
    buff = '[10:00:00.000] BUFF_START #0 id="buff_chr_0004_pelica_unproven" uid=x owner=eny_0051_rodin src=id_999 dur=2'
    parsed = parser.parse_raw_battle_log_text(buff + '\n' + HIT)
    record = next(row for row in parsed['buff_events'] if row['uid'] == 'x')
    assert record['source_character_key'] is None
    assert parsed['rdps_damage_basis']['rdps_strict_ok'] is False


def test_viewer_uses_explicit_mapping_but_ignores_recent_cast(monkeypatch):
    monkeypatch.setattr(parser, '_canonical_num_table_skill_id', lambda key, char: SKILL if key == 'skill_7' else None)
    monkeypatch.setattr(parser, '_canonical_num_table_buff_number', lambda *args: None)
    common = dict(character_key=CHAR, target_enemy_key='eny_0051_rodin', damage_element=None,
                  original_template_int_id=None, ts_ms=100,
                  recent_actor_maps_by_char={}, recent_numeric_buffs_by_source={},
                  recent_skill_casts_by_char={CHAR: [{'skill': SKILL, 'ts_ms': 100}]},
                  loadout_skill_ids_by_char={})
    assert _skill_mapping_info('skill_7', **common)['canonical_skill_id'] == SKILL
    assert _skill_mapping_info('skill_8', **common)['canonical_skill_id'] is None


def test_namespace_collision_is_unresolved(monkeypatch):
    monkeypatch.setattr(parser, '_canonical_num_table_skill_id', lambda *args: SKILL)
    monkeypatch.setattr(parser, '_canonical_num_table_buff_number', lambda *args: 'buff_common_other')
    assert parser._canonical_hit_skill_id('skill_7', CHAR) == 'skill_7'
    assert parser._canonical_runtime_buff_skill_id('skill_7', CHAR, action_id=0, damage_unit_index=0) is None
    assert parser._canonical_hit_skill_id_from_cast_context(
        'skill_7', CHAR, 0, target_enemy_key=None, recent_skill_casts_by_char={},
        recent_numeric_buffs_by_source={}, template_type='buff',
    ) == 'buff_common_other'


def test_highest_seen_hp_is_not_maximum_hp():
    hit = {'target_enemy_key': 'eny_0051_rodin', 'enemy_hp_after': 500, 'hit_value': 100}
    parser._annotate_hit_enemy_hp_state([hit])
    assert 'target_enemy_hp_ratio_before' not in hit
    hit['target_enemy_max_hp'] = 1200
    parser._annotate_hit_enemy_hp_state([hit])
    assert hit['target_enemy_hp_ratio_before'] == 0.5


def test_enemy_hp_state_isolated_by_target_actor_id():
    content = "\n".join([
        '[10:00:00.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=5000 eHP=0 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang tgtId=2001',
        '[10:00:01.000] HP_V2 #2 hit=500 cum=500 raw=500.00 pHP=5000 eHP=0 skill="chr_0004_pelica_attack1" hits=1 src=chr_0004_pelica tgt=eny_0051_rodin atk=chr_0004_pelica tgtId=2002',
    ])
    parsed = parser.parse_raw_battle_log_text(content, include_rdps_debug=True)
    assert [hit['hit_value'] for hit in parsed['debug_hits']] == [100, 500]


def test_enemy_hp_state_reuses_same_target_actor_id_for_overkill_cap():
    content = "\n".join([
        '[10:00:00.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=5000 eHP=200 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang tgtId=2001',
        '[10:00:01.000] HP_V2 #2 hit=500 cum=500 raw=500.00 pHP=5000 eHP=0 skill="chr_0004_pelica_attack1" hits=1 src=chr_0004_pelica tgt=eny_0051_rodin atk=chr_0004_pelica tgtId=2001',
    ])
    parsed = parser.parse_raw_battle_log_text(content, include_rdps_debug=True)
    assert [hit['hit_value'] for hit in parsed['debug_hits']] == [100, 200]


def test_entity_stats_are_bound_to_hit_target_without_defaults():
    content = "\n".join([
        '[10:00:00.000] ENTITY_STATS id=2001 template=eny_0051_rodin kind=object level=90 hp=1000 attrs=[3:100/100 4:0.6/0.6 5:1/1 6:0.8/0.8 7:1/1 48:1/1 60:1/1 80:123/123 81:123/123 82:123/123 83:123/123 84:123/123 85:123/123 94:0/0 95:0/0 96:0/0 97:20/20 98:0/0 99:0/0]',
        '[10:00:01.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=5000 eHP=900 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang tgtId=2001',
    ])
    parsed = parser.parse_raw_battle_log_text(content, include_rdps_debug=True)
    hit = parsed['debug_hits'][0]
    assert hit['target_defense'] == 100.0
    assert hit['target_defense_source'] == 'ENTITY_STATS.attr3'
    assert hit['target_resistance_scalars']['pulse'] == 0.8
    assert hit['target_resistance_scalars'].get('physical') == 0.6
    assert 83 not in hit['target_resistance_scalars']
    assert hit['target_resistance_points']['pulse'] == 20.0


def test_native_resistance_inputs_are_distinct_from_resistance_points():
    # Native BattleFormula uses DamageTakenScalar (4-7, 48, 60) multiplied by
    # the resistance-point factor (94-99); the similarly named 80-85 values
    # are not the hit-time scalar inputs.
    assert (1.0 - 20.0 / 100.0) * 0.8 == pytest.approx(0.64)


def test_all_snapshot_attributes_do_not_prove_current_hit_baseline():
    attrs = " ".join(
        f"{attr_type}:{0.2 if attr_type in (17, 51) else 123 if attr_type in (2, 87) else 0}/0"
        for attr_type in range(1, 101)
    )
    content = "\n".join([
        '[10:00:00.000] LOADOUT reason=SC_SELF_SCENE_INFO slotCount=1 memberCount=1 roster=[1001]',
        '[10:00:00.000] LOADOUT slot=0 char=chr_0027_tangtang weaponTemplate=wpn_pistol_0011 weaponLv=1 refine=0 break=0 attachedGem=0 equipInsts={} equips={} equipsComplete=1 unplacedEquipInstIds=[] equipSuit={[suit_usp02]=3}',
        f'[10:00:00.100] ENTITY_STATS id=1001 template=chr_0027_tangtang kind=character level=90 hp=5000 attrs=[{attrs}]',
        '[10:00:01.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=5000 eHP=900 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang atkId=1001 tgtId=2001',
    ])
    parsed = parser.parse_raw_battle_log_text(content, include_rdps_debug=True)
    hit = parsed['debug_hits'][0]
    assert hit['attacker_stats_attrs'][17] == 0.2
    assert hit['attacker_stats_source'] == 'ENTITY_STATS'
    assert hit.get('attacker_stats_complete') is not True
    assert hit.get('baseline') is None
    assert not any(row.get('evidence_kind') == 'prebattle_ENTITY_STATS'
                   for row in hit.get('static_multiplier_entries', []))
    assert not any(row.get('evidence_kind') == 'prebattle_ENTITY_STATS'
                   for row in hit.get('static_arts_strength_entries', []))
    assert hit['attacker_stats_snapshot_ts_ms'] == 36_000_100
    assert hit['attacker_stats_snapshot_line_no'] == 3
    # This abbreviated log does not establish capture coverage or a complete
    # model for the suit. A snapshot alone cannot waive those requirements.
    assert not any(row['reason'] == 'unmodeled_suit_static_semantics'
                   for row in hit['rdps_unresolved_static_multiplier_sources'])


def test_entity_snapshot_never_uses_future_or_wrong_entity():
    early = {'attrs': {2: 100}}
    late = {'attrs': {2: 900}}
    rows = {'1001': [(100, early), (200, late)]}
    assert parser._entity_stats_snapshot_at(rows, '1001', 99) is None
    assert parser._entity_stats_snapshot_at(rows, '1002', 200) is None
    assert parser._entity_stats_snapshot_at(rows, '1001', 100) is early
    assert parser._entity_stats_snapshot_at(rows, '1001', 199) is early
    assert parser._entity_stats_snapshot_at(rows, '1001', 200) is late


@pytest.mark.parametrize('side', ['attacker', 'target'])
def test_snapshot_template_conflict_does_not_supply_hit_attributes(side):
    hit = {'character_key': CHAR, 'target_enemy_key': 'eny_0051_rodin'}
    snapshot = {'template': 'chr_9999_other', 'attrs': {2: 100, 3: 100, 83: 0.8}}
    if side == 'attacker':
        parser._attach_attacker_entity_stats(hit, snapshot)
        assert 'attacker_stats_attrs' not in hit
        assert hit['rdps_unresolved_attacker_binding'] is True
    else:
        parser._attach_target_entity_stats(hit, snapshot)
        assert 'target_defense' not in hit
        assert hit['rdps_unresolved_target_binding'] is True


@pytest.mark.parametrize('tokens', ['2:100/10 2:900/10', '2:100/10 2:unknown/10', '2:nan/10'])
def test_invalid_snapshot_attribute_is_not_replaced_or_silently_dropped(tokens):
    _, snapshot = parser._parse_entity_stats_snapshot(
        '[10:00:00.000] ENTITY_STATS id=1001 template=' + CHAR + ' kind=character attrs=[' + tokens + ']')
    assert 2 not in snapshot['attrs']
    assert snapshot['invalid_attr_types'] == [2]


def test_complete_enemy_variant_identity_survives_hit_parsing(exact_units):
    first = HIT.replace('eny_0051_rodin', 'eny_0051_rodin_tower010')
    second = HIT.replace('eny_0051_rodin', 'eny_0051_rodin_race007').replace('#1', '#2')
    parsed = parser.parse_raw_battle_log_text(first + '\n' + second, include_rdps_debug=True)
    assert [hit['target_enemy_key'] for hit in parsed['debug_hits']] == [
        'eny_0051_rodin_tower010', 'eny_0051_rodin_race007']


def test_unknown_refinement_is_not_refinement_zero():
    base = '[10:00:00.000] LOADOUT slot=0 char=chr_0004_pelica weaponTemplate=wpn_funnel_0005'
    assert parser._parse_loadout_slot_snapshot(base)['weapon_refine'] is None
    assert parser._parse_loadout_slot_snapshot(base + ' refine=unknown')['weapon_refine'] is None
    assert parser._parse_loadout_slot_snapshot(base + ' refine=0')['weapon_refine'] == 0


def test_viewer_does_not_read_historical_battles_for_current_hit(exact_units, monkeypatch):
    original_glob = Path.glob

    def guarded_glob(path, pattern, *args, **kwargs):
        assert pattern != '*trace*.dat', 'current hit must not be estimated from historical battles'
        return original_glob(path, pattern, *args, **kwargs)

    monkeypatch.setattr(Path, 'glob', guarded_glob)
    first = parse_hit_debug_log_text(HIT, file_name='current.log', _allow_history=True)
    second = parse_hit_debug_log_text(HIT, file_name='current.log', _allow_history=False)
    assert first['hits'] == second['hits']
