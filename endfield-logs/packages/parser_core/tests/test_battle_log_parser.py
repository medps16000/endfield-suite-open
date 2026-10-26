import json
from pathlib import Path
import parser_core.battle_log_parser as parser

from parser_core.battle_log_parser import (
    _canonical_num_table_skill_id,
    _collect_buff_labels,
    _collect_zone_effects,
    _effect_condition_failure,
    _is_arts_strength_damage_hit,
    _infer_skill_action_damage_element,
    _infer_skill_damage_element,
    _packet_mapping_stack_limit,
    _packet_numeric_effects,
    _rdps_registry_stack_limit,
    _rdps_registry_known_non_rdps_entry,
    _merge_loadout_snapshot,
    _parse_loadout_slot_snapshot,
    _parse_loadout_stats_snapshot,
    _repair_weapon_puton_loadout_groups,
    parse_raw_battle_log_text,
)


def test_typed_empty_buff_root_is_exact_non_rdps_marker(monkeypatch) -> None:
    monkeypatch.setattr(
        parser,
        "_load_current_buff_roots",
        lambda: {
            "buff_test_marker": {
                "abilityEventAction": [], "buffEventAction": [],
                "attributeModifier": {"attributeModifiers": []},
                "damageModifier": [], "globalModifier": [], "healModifier": [],
            }
        },
    )
    record = {"event_key": "buff_test_marker", "raw_event_key": "buff_test_marker", "bb_keys": [], "bb_values": {}}
    entry = _rdps_registry_known_non_rdps_entry(record)
    assert entry is not None
    assert entry["proof_kind"] == "typed_root_structural"


def test_local_table_catalogs_cover_new_weapon_and_equipment() -> None:
    from parser_core.battle_log_parser import _load_equip_item_catalog, _load_weapon_catalog

    assert _load_weapon_catalog()["wpn_funnel_0019"]["weapon_name"] == "寒夜幽影"
    equip = _load_equip_item_catalog()["item_equip_t4_suit_spellburst_body_01"]
    assert equip["suit_name"] == "险关"
    assert equip["main_attr"]["value"] == 56
    assert _load_equip_item_catalog()["item_equip_t4_suit_usp02_body_03"]["suit_name"] == "长息"
    assert _load_equip_item_catalog()["item_equip_t4_suit_fire_natr01_hand_04"]["suit_name"] == "动火用"


def test_dynamic_blackboard_modify_events_are_preserved_as_observations() -> None:
    parsed = parse_raw_battle_log_text(
        "[10:00:00.100] BB_MODIFY_DYNAMIC seq=7 tick=11 inst=101 ownerId=202 "
        "owner=chr_0016_laevat skill=chr_0016_laevat_attack1 actionId=3 value=-3.6\n"
        "[10:00:00.200] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=5000 "
        "eHP=900000 skill=chr_0016_laevat_attack1 hits=1 src=chr_0016_laevat "
        "tgt=eny_0051_rodin atk=chr_0016_laevat seg=0 shared=2 critFlag=0 critDmg=0.5000 "
        "dynBB=29:1,38:1 calcBB=2:duration_corrupt_final=7",
        include_rdps_debug=True,
    )
    assert parsed["dynamic_bb_events"] == [{
        "line_no": 1,
        "ts_ms": 36000100,
        "seq": 7,
        "tick": 11,
        "instance_id": "101",
        "owner_id": "202",
        "owner": "chr_0016_laevat",
        "skill_key": "chr_0016_laevat_attack1",
        "action_id": 3,
        "blackboard_key": None,
        "value": -3.6,
        "state_error": "missing_dynamic_identity",
    }]
    assert parsed["debug_hits"][0]["dynamic_bb_observations"] == []
    assert parsed["debug_hits"][0]["dynamic_bb_values"] == {29: 1.0, 38: 1.0}
    assert parsed["debug_hits"][0]["calc_bb_values"] == {2: {"duration_corrupt_final": 7.0}}
    assert parser._load_skill_item_metadata("chr_0016_laevat_ult_attack2")["dynamic_action_keys"][29] == "stopped1"
from parser_core.unified import rdps_totals_from_raw_report


def test_official_skilldata_catalog_resolves_only_direct_damage_templates() -> None:
    assert _infer_skill_damage_element("chr_0032_lizhiyan_attack1", "chr_0032_lizhiyan") == "natural"
    assert _infer_skill_damage_element("chr_0032_lizhiyan_attack1_projhit", "chr_0032_lizhiyan") == "natural"
    assert _infer_skill_action_damage_element("chr_0032_lizhiyan_normal_skill_abilityrange2", 17, 0) == "natural"
    assert _infer_skill_damage_element("chr_0035_liino_normal_skill_projhit", "chr_0035_liino") == "pulse"
    # Current official root has DamageAction 3: unit0 is Pulse/Hp, unit1
    # Pulse/Poise. Action2 has no direct DamageAction; no neighboring fallback.
    assert _infer_skill_action_damage_element("chr_0035_liino_normal_skill_projhit", 3, 0) == "pulse"
    assert _infer_skill_action_damage_element("chr_0035_liino_normal_skill_projhit", 2, 0) is None
    assert _infer_skill_action_damage_element("chr_0035_liino_normal_skill_projhit", 3, 1) is None
    assert _infer_skill_action_damage_element("chr_0035_liino_normal_skill_projhit", 3, 2) is None
    assert _infer_skill_damage_element("chr_0035_liino_unknown", "chr_0035_liino") is None
    assert _infer_skill_damage_element("chr_0035_liino_normal_skill", "chr_0035_liino") is None


def test_packaged_num_id_table_contains_pograni_fracture_alias() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    table_path = repo_root / "data" / "local_tables" / "NumIdStrTable.json"
    payload = json.loads(table_path.read_text(encoding="utf-8"))

    assert payload["buff_id"]["dic"]["1211"] == "buff_physical_do_fracture"


def test_packaged_num_id_table_contains_typhoea_combo_debuff() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    table_path = repo_root / "data" / "local_tables" / "NumIdStrTable.json"
    payload = json.loads(table_path.read_text(encoding="utf-8"))

    assert payload["buff_id"]["dic"]["3619"] == "buff_chr_0034_typhoea_combo_enemy_debuff"


def test_typhoea_condition_requires_observed_damage_decorate_mask() -> None:
    effect = {
        "condition": {
            'type': 'damage_decorate_mask', 'source': 'CheckDamageDecorateMask',
            'mask': 33554432, 'check': 'HasAll',
        }
    }

    assert _effect_condition_failure(
        effect,
        {"skill_key": "buff_common_natural_natural_triggered"},
    )['reason_group'] == 'condition_unobservable'
    assert _effect_condition_failure(effect, {'damage_decorate_mask': 33554432}) is None
    failure = _effect_condition_failure(effect, {'damage_decorate_mask': 128})
    assert failure['reason_group'] == 'condition_unsatisfied'


def test_current_typhoea_combo_debuff_exposes_exact_mask_condition() -> None:
    record = {
        "event_key": "buff_chr_0034_typhoea_combo_enemy_debuff",
        "raw_event_key": "3619",
        "source_character_key": "chr_0034_typhoea",
        "target_enemy_key": "eny_test",
        "bb_values": {"damage_up": 0.1, "slow_down": 0.4, "duration": 6},
        "bb_keys": ["damage_up", "slow_down", "duration"],
        "attr_mods": [],
    }

    effects = _collect_zone_effects(record)
    assert len(effects) == 1
    assert (effects[0]['zone'], effects[0]['rate'], effects[0]['bb_key']) == ('vuln_taken', 0.1, 'damage_up')
    assert effects[0]['rdps_whitelist_keys'] == ['buff_chr_0034_typhoea_combo_enemy_debuff']
    assert _effect_condition_failure(effects[0], {'damage_decorate_mask': 33554432}) is None
    assert _effect_condition_failure(effects[0], {'damage_decorate_mask': 128})['reason_group'] == 'condition_unsatisfied'


def test_legacy_registry_rows_require_current_typed_effects() -> None:
    rows = [
        {'event_key': 'buff_chr_0035_liino_spellenhance', 'source_character_key': 'chr_0035_liino', 'target_character_key': 'chr_0027_tangtang', 'bb_values': {'spellenhance_rate': 0.12}, 'bb_keys': ['spellenhance_rate'], 'attr_mods': []},
        {'event_key': 'buff_wpn_lance_0014_damageup', 'source_character_key': 'chr_0035_liino', 'target_character_key': 'chr_0027_tangtang', 'packet_mapping': {'weapon_id': 'wpn_lance_0014'}, 'bb_values': {'atk_up': 0.08}, 'bb_keys': ['atk_up'], 'attr_mods': []},
    ]
    liino, weapon = rows
    assert _collect_zone_effects(liino) == []
    assert not liino.get('rdps_unresolved_semantic_condition')
    assert _rdps_registry_known_non_rdps_entry(liino)['proof_kind'] == 'typed_root_structural'
    # The parent has no direct attribute modifier. Its current child UID
    # carries PulseEnhancedDmgIncrease; do not credit both parent and child.
    child = {'event_key': 'buff_common_affixes_enhance_pulse',
             'source_character_key': 'chr_0035_liino',
             'target_character_key': 'chr_0027_tangtang',
             'bb_values': {'rate': 0.12}, 'bb_keys': ['rate'], 'attr_mods': []}
    effects = _collect_zone_effects(child)
    assert [(e['zone'], e['element'], e['rate'], e['attr_type']) for e in effects] == [
        ('amp', 'pulse', 0.12, 66)]
    assert _collect_zone_effects(weapon) == [{
        'zone': 'atk', 'element': 'all', 'rate': 0.08, 'attr_type': 2,
        'bb_key': 'atk_up', 'semantic_source': 'typed_current_attributeModifier',
        'external_rdps_allowed': True, 'rdps_whitelist_keys': ['buff_wpn_lance_0014_damageup']}]


def test_arts_strength_damage_classifier_covers_all_physical_anomalies_but_not_no_guard() -> None:
    assert _is_arts_strength_damage_hit({"skill_key": "buff_physical_airborne"}) is False
    assert _is_arts_strength_damage_hit({"skill_key": "arbitrary_name", "arts_strength_damage_observed": True}) is True


def test_repair_weapon_puton_swaps_complete_weapon_state_and_propagates_to_battle() -> None:
    seraph = "chr_0011_seraph"
    lizhiyan = "chr_0032_lizhiyan"
    explosion = {
        "weapon_inst_id": "4633292003673374731",
        "weapon_template": "wpn_funnel_0008",
        "weapon_name": "爆破单元",
        "weapon_refine": 5,
        "weapon_refine_source": "source_skill",
        "gem_template": "gem_explosion",
        "weapon_source_skills": [{"skill_id": 2228, "level": 5, "potential_level": 5}],
        "weapon_refine_stats": [{"skill_id": 2228, "level": 5, "potential_level": 5}],
    }
    formation = {
        "weapon_inst_id": "4614961918380474406",
        "weapon_template": "wpn_funnel_0016",
        "weapon_name": "四二式·肃阵",
        "weapon_refine": 0,
        "weapon_refine_source": "source_skill",
        "gem_template": "gem_formation",
        "weapon_source_skills": [{"skill_id": 3629, "level": 0, "potential_level": 0}],
        "weapon_refine_stats": [{"skill_id": 3629, "level": 0, "potential_level": 0}],
    }

    initial_seraph = {"character_key": seraph, "potential": 5, "skill_int_ids": [101, 2228], **explosion}
    initial_lizhiyan = {"character_key": lizhiyan, "potential": 0, "skill_int_ids": [202, 3629], **formation}
    stale_receiver = {**initial_lizhiyan, **explosion, "potential": 0, "skill_int_ids": [202, 3629]}
    stale_receiver["weapon_source_skills"] = formation["weapon_source_skills"]
    stale_receiver["weapon_refine_stats"] = formation["weapon_refine_stats"]
    stale_swap_rows = {
        # This is the legacy client bug: both rows point to the receiver's new
        # instance, while each row still carries its old weapon source skill.
        seraph: dict(initial_seraph),
        lizhiyan: stale_receiver,
    }
    groups = [
        {"ts_ms": 1, "index": 1, "reason": "SC_SYNC_CHAR_BAG_INFO", "rows": {seraph: initial_seraph, lizhiyan: initial_lizhiyan}},
        {"ts_ms": 2, "index": 2, "reason": "SC_WEAPON_PUTON", "rows": stale_swap_rows},
        {"ts_ms": 3, "index": 3, "reason": "BATTLE_START", "rows": stale_swap_rows},
    ]

    repaired = _repair_weapon_puton_loadout_groups(groups, stale_swap_rows)
    battle_rows = groups[-1]["rows"]

    assert battle_rows[seraph]["weapon_inst_id"] == formation["weapon_inst_id"]
    assert battle_rows[seraph]["weapon_template"] == "wpn_funnel_0016"
    assert battle_rows[seraph]["gem_template"] == "gem_formation"
    assert battle_rows[seraph]["weapon_source_skills"][0] == {
        "skill_id": 3629,
        "level": 0,
        "potential_level": 5,
    }
    assert battle_rows[seraph]["skill_int_ids"] == [101, 3629]

    assert battle_rows[lizhiyan]["weapon_inst_id"] == explosion["weapon_inst_id"]
    assert battle_rows[lizhiyan]["weapon_template"] == "wpn_funnel_0008"
    assert battle_rows[lizhiyan]["gem_template"] == "gem_explosion"
    assert battle_rows[lizhiyan]["weapon_source_skills"][0] == {
        "skill_id": 2228,
        "level": 5,
        "potential_level": 0,
    }
    assert battle_rows[lizhiyan]["skill_int_ids"] == [202, 2228]
    assert repaired[seraph]["weapon_template"] == "wpn_funnel_0016"
    assert repaired[lizhiyan]["weapon_template"] == "wpn_funnel_0008"


def test_packet_mapping_effects_require_runtime_rate_value() -> None:
    record = {
        "event_key": "buff_test_static_rate",
        "raw_event_key": "999001",
        "packet_mapping": {
            "role": "effect",
            "effects": [{"zone": "atk", "element": "all", "bb_key": "atk_up", "rate": 0.5}],
        },
        "bb_values": {},
        "bb_keys": [],
    }

    assert _packet_numeric_effects(record) == []


def test_packet_mapping_stack_limit_prefers_runtime_max_stack() -> None:
    record = {
        "event_key": "buff_test_stack",
        "raw_event_key": "999002",
        "packet_mapping": {"role": "effect", "stack_limit": 5},
        "bb_values": {"max_stack": 2},
        "bb_keys": ["max_stack"],
    }

    assert _packet_mapping_stack_limit(record) == 2


def test_rdps_preflight_accepts_version_bound_typed_non_rdps_root() -> None:
    content = "\n".join(
        [
            "[10:00:00.000] GAME_TIMER_START seq=1 source=ChallengeStart startMs=0 expireMs=0",
            '[10:00:00.100] BUFF_START #1 id="buff_chr_0013_aglina_talent_0_effectbuff_Add" uid=1 owner=chr_0028_wulfa src=chr_0013_aglina dur=5.00 lifeT=5.00 passed=0.00 enh=1',
            "[10:00:00.100]   BB[1]: add=0.08",
            '[10:00:00.500] HP_V2 #2 hit=100 cum=100 raw=100.00 packetFinalValue=100.0 pHP=5000 eHP=900000 skill="chr_0028_wulfa_attack1" actionId=6 damageUnitIndex=0 hits=1 src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:01.000] GAME_TIMER_END seq=1 source=ChallengeComplete elapsedMs=1000 startMs=0 endMs=1000 expireMs=0 sane=1",
        ]
    )

    parsed = parse_raw_battle_log_text(content, include_rdps_debug=True, file_name="registry-non-rdps.log")
    preflight = parsed["rdps_preflight"]

    assert preflight["ok"] is True
    assert preflight["accepted_non_rdps_buff_count"] == 1
    assert parsed["rdps_damage_basis"]["rdps_strict_ok"] is True


def test_rdps_preflight_blocks_unknown_external_buff_before_allocation() -> None:
    content = "\n".join(
        [
            "[10:00:00.000] GAME_TIMER_START seq=1 source=ChallengeStart startMs=0 expireMs=0",
            '[10:00:00.100] BUFF_START #1 id="buff_chr_9999_unknown_marker" uid=1 owner=chr_0028_wulfa src=chr_0013_aglina dur=5.00 lifeT=5.00 passed=0.00 enh=1',
            '[10:00:00.500] HP_V2 #2 hit=100 cum=100 raw=100.00 packetFinalValue=100.0 pHP=5000 eHP=900000 skill="chr_0028_wulfa_attack1" actionId=6 damageUnitIndex=0 hits=1 src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:01.000] GAME_TIMER_END seq=1 source=ChallengeComplete elapsedMs=1000 startMs=0 endMs=1000 expireMs=0 sane=1",
        ]
    )

    parsed = parse_raw_battle_log_text(content, include_rdps_debug=True, file_name="unknown-external-preflight.log")
    preflight = parsed["rdps_preflight"]

    assert preflight["ok"] is False
    assert preflight["blocker_count"] == 1
    assert preflight["blockers"][0]["event_key"] == "buff_chr_9999_unknown_marker"
    assert parsed["rdps_damage_basis"]["rdps_strict_ok"] is False


def test_verified_weapon_mapping_without_current_typed_effect_blocks_strict() -> None:
    content = "\n".join([
        '[10:00:00.100] BUFF_START #1 id="1699" uid=1 owner=chr_0004_pelica src=chr_0013_aglina dur=20.00 lifeT=20.00 passed=0.00 enh=1',
        '[10:00:00.500] HP_V2 #2 hit=114 cum=114 raw=114.00 packetFinalValue=114.0 pHP=5000 eHP=899886 skill="chr_0004_pelica_attack1_projhit" actionId=0 damageUnitIndex=0 hits=1 src=chr_0004_pelica tgt=eny_0051_rodin atk=chr_0004_pelica seg=0 shared=2 critFlag=0 critDmg=0.5000',
    ])
    parsed = parse_raw_battle_log_text(content, include_rdps_debug=True, file_name='legacy-typed-effect-missing.log')
    assert parsed['rdps_preflight']['ok'] is False
    assert parsed['rdps_damage_basis']['rdps_strict_ok'] is False
    event = next(event for event in parsed['buff_events'] if event['uid'] == '1')
    assert event['zone_effects'] == []


def test_verified_weapon_mapping_without_runtime_value_blocks_strict() -> None:
    content = "\n".join(
        [
            "[09:59:59.000] SQUAD size=2 members=[chr_0013_aglina chr_0004_pelica]",
            "[09:59:59.000] LOADOUT slot=0 char=chr_0013_aglina weaponTemplate=wpn_sword_0012 weaponLv=90 refine=5 break=0 attachedGem=0 equipInsts={} equips={} equipSuit={}",
            "[09:59:59.000] LOADOUT slot=1 char=chr_0004_pelica weaponTemplate=wpn_funnel_0001 weaponLv=90 refine=5 break=0 attachedGem=0 equipInsts={} equips={} equipSuit={}",
            "[10:00:00.000] GAME_TIMER_START seq=1 source=ChallengeStart startMs=0 expireMs=0",
            '[10:00:00.100] BUFF_START #1 id="1699" uid=1 owner=chr_0004_pelica src=chr_0013_aglina dur=20.00 lifeT=20.00 passed=0.00 enh=1',
            "[10:00:00.100]   BB[1]: atk_up=0.28 duration=20 max_stack=2",
            '[10:00:00.500] HP_V2 #2 hit=100 cum=100 raw=100.00 packetFinalValue=100.0 pHP=5000 eHP=899900 skill="chr_0004_pelica_attack1_projhit" actionId=0 damageUnitIndex=0 hits=1 src=chr_0004_pelica tgt=eny_0051_rodin atk=chr_0004_pelica seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:01.000] GAME_TIMER_END seq=1 source=ChallengeComplete elapsedMs=1000 startMs=0 endMs=1000 expireMs=0 sane=1",
        ]
    )

    parsed = parse_raw_battle_log_text(content, include_rdps_debug=True, file_name="wpn-sword-0012-missing-runtime.log")

    assert parsed["rdps_preflight"]["ok"] is False
    assert parsed["rdps_preflight"]["blockers"][0]["event_key"] == "buff_wpn_sword_0012_atk_up"
    assert parsed["rdps_damage_basis"]["rdps_strict_ok"] is False


def test_unresolved_formula_weapon_mapping_blocks_strict() -> None:
    content = "\n".join(
        [
            "[09:59:59.000] SQUAD size=2 members=[chr_0013_aglina chr_0004_pelica]",
            "[09:59:59.000] LOADOUT slot=0 char=chr_0013_aglina weaponTemplate=wpn_sword_0013 weaponLv=90 refine=5 break=0 attachedGem=0 equipInsts={} equips={} equipSuit={}",
            "[09:59:59.000] LOADOUT slot=1 char=chr_0004_pelica weaponTemplate=wpn_funnel_0001 weaponLv=90 refine=5 break=0 attachedGem=0 equipInsts={} equips={} equipSuit={}",
            "[10:00:00.000] GAME_TIMER_START seq=1 source=ChallengeStart startMs=0 expireMs=0",
            '[10:00:00.100] BUFF_START #1 id="1812" uid=1 owner=chr_0004_pelica src=chr_0013_aglina dur=20.00 lifeT=20.00 passed=0.00 enh=1',
            "[10:00:00.100]   BB[1]: atk_up=0.28 atk_up_add=0.14 atk_up_mult=0.07 duration=20",
            '[10:00:00.500] HP_V2 #2 hit=100 cum=100 raw=100.00 packetFinalValue=100.0 pHP=5000 eHP=899900 skill="chr_0004_pelica_attack1_projhit" actionId=0 damageUnitIndex=0 hits=1 src=chr_0004_pelica tgt=eny_0051_rodin atk=chr_0004_pelica seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:01.000] GAME_TIMER_END seq=1 source=ChallengeComplete elapsedMs=1000 startMs=0 endMs=1000 expireMs=0 sane=1",
        ]
    )

    parsed = parse_raw_battle_log_text(content, include_rdps_debug=True, file_name="wpn-sword-0013-unresolved.log")

    assert parsed["rdps_preflight"]["ok"] is False
    assert parsed["rdps_preflight"]["blockers"][0]["event_key"] == "buff_wpn_sword_0013_atk_up"
    assert parsed["rdps_damage_basis"]["rdps_strict_ok"] is False


def test_legacy_formula_buff_value_requires_current_typed_effect() -> None:
    content = "\n".join([
        '[10:00:00.100] BUFF_START #1 id="1812" uid=1 owner=chr_0004_pelica src=chr_0013_aglina dur=20.00 lifeT=20.00 passed=0.00 enh=1',
        '[10:00:00.100] BB[1]: atk_up=0.28 atk_up_add=0.14 atk_up_mult=0.07 consume_layer=2 duration=20',
        '[10:00:00.500] HP_V2 #2 hit=114 cum=114 raw=114.00 packetFinalValue=114.0 pHP=5000 eHP=899886 skill="chr_0004_pelica_attack1_projhit" actionId=0 damageUnitIndex=0 hits=1 src=chr_0004_pelica tgt=eny_0051_rodin atk=chr_0004_pelica seg=0 shared=2 critFlag=0 critDmg=0.5000',
    ])
    parsed = parse_raw_battle_log_text(content, include_rdps_debug=True, file_name='legacy-formula-effect-missing.log')
    assert parsed['rdps_preflight']['ok'] is False
    assert parsed['rdps_damage_basis']['rdps_strict_ok'] is False
    event = next(event for event in parsed['buff_events'] if event['uid'] == '1')
    assert event['zone_effects'] == []


def test_merge_loadout_snapshot_merges_incremental_equips_by_slot() -> None:
    base = {
        "character_key": "chr_0017_yvonne",
        "equips": [
            {"slot": 0, "item_id": "old_hand"},
            {"slot": 1, "item_id": "old_body"},
            {"slot": 2, "item_id": "old_edc"},
            {"slot": 3, "item_id": "old_tail"},
        ],
    }
    update = {
        "character_key": "chr_0017_yvonne",
        "equips": [
            {"slot": 2, "item_id": "new_edc"},
        ],
    }

    merged = _merge_loadout_snapshot(base, update)

    assert [(item["slot"], item["item_id"]) for item in merged["equips"]] == [
        (0, "old_hand"),
        (1, "old_body"),
        (2, "new_edc"),
        (3, "old_tail"),
    ]


def test_partial_equipment_update_does_not_invent_another_slot_from_suit_count() -> None:
    base = _parse_loadout_slot_snapshot(
        "[10:00:00.000] LOADOUT slot=0 char=chr_0017_yvonne "
        "weaponTemplate=wpn_pistol_0010 weaponLv=0 refine=0 break=0 "
        "equips={[0]=item_equip_t4_suit_criti01_hand_03 "
        "[1]=item_equip_t4_suit_criti01_body_02 "
        "[2]=item_equip_t4_suit_criti01_edc_03} "
        "equipSuit={[suit_criti01]=3}"
    )
    update = _parse_loadout_slot_snapshot(
        "[10:00:01.000] LOADOUT slot=0 char=chr_0017_yvonne "
        "weaponTemplate=wpn_pistol_0010 weaponLv=0 refine=0 break=0 "
        "equips={[2]=item_equip_t4_suit_heal01_edc_03} "
        "equipSuit={[suit_criti01]=3}"
    )

    assert base is not None
    assert update is not None
    merged = _merge_loadout_snapshot(base, update)

    assert [(item["slot"], item["item_id"]) for item in merged["equips"]] == [
        (0, "item_equip_t4_suit_criti01_hand_03"),
        (1, "item_equip_t4_suit_criti01_body_02"),
        (2, "item_equip_t4_suit_heal01_edc_03"),
    ]


def test_ambiguous_third_skill_does_not_override_explicit_loadout_refine() -> None:
    slot = _parse_loadout_slot_snapshot(
        "[10:00:00.000] LOADOUT slot=3 char=chr_0011_seraph "
        "weaponTemplate=wpn_funnel_0008 weaponLv=0 refine=5 break=0 "
        "equips={} equipSuit={}"
    )
    stats = _parse_loadout_stats_snapshot(
        "[10:00:00.000] LOADOUT_STATS slot=3 char=chr_0011_seraph "
        "weaponTemplate=wpn_funnel_0008 weaponBaseAtk=490 weaponBaseAtkLv1=50 weaponBaseAtkMax=490 "
        "weaponRefineStats={1391:level=5:potentialLv=5:bb={mainattr=71};"
        "2253:level=9:potentialLv=5:bb={atk=0,physpell=78};"
        "2228:level=4:potentialLv=5:bb={duration=15,lv=4,second_attr_up=0.16,spell_damage_taken_up=0.144}} "
        "weaponSourceSkills={1391:level=5:potentialLv=5:bb={mainattr=71};"
        "2253:level=9:potentialLv=5:bb={atk=0,physpell=78};"
        "2228:level=4:potentialLv=5:bb={duration=15,lv=4,second_attr_up=0.16,spell_damage_taken_up=0.144}}"
    )

    assert slot is not None
    assert stats is not None
    assert stats["weapon_refine"] is None
    merged = _merge_loadout_snapshot(slot, stats)
    assert merged["weapon_refine"] == 5
    assert merged["weapon_refine_source"] == "loadout"
    assert _merge_loadout_snapshot(merged, slot)["weapon_refine"] == 5


def test_collect_buff_labels_distinguishes_fragile_from_vuln_taken() -> None:
    fragile_record = {
        "event_key": "buff_chr_0013_aglina_ultimate_spell_vulnerable",
        "source_character_key": "chr_0013_aglina",
        "target_enemy_key": "eny_0051_rodin",
        "bb_keys": ["rate"],
        "bb_values": {"rate": 0.33},
        "attr_mods": [{"attr_type": "85", "bb_key": "rate", "use_key": "1", "value": None}],
        "attr_types": ["85"],
    }
    vuln_record = {
        "event_key": "buff_common_pulse_pulse_conduct_triggered_do",
        "source_character_key": "chr_0004_pelica",
        "target_enemy_key": "eny_0051_rodin",
        "bb_keys": ["final_spell_resistance_decrease"],
        "bb_values": {"final_spell_resistance_decrease": 0.31},
        "attr_mods": [],
        "attr_types": [],
    }

    assert "脆弱" in _collect_buff_labels(fragile_record)
    assert "易伤" not in _collect_buff_labels(fragile_record)
    assert "承伤易伤" in _collect_buff_labels(vuln_record)


def test_collect_zone_effects_builds_check_hp_condition_from_semantics() -> None:
    record = {
        "event_key": "buff_chr_0005_chen_potential_1",
        "source_character_key": "chr_0005_chen",
        "target_character_key": "chr_0005_chen",
        "bb_keys": ["hp_remain", "extra_dmg"],
        "bb_values": {"hp_remain": 0.5, "extra_dmg": 0.2},
        "attr_mods": [],
    }

    effects = _collect_zone_effects(record)

    assert effects == [
        {
            "zone": "dmg_inc",
            "element": "all",
            "rate": 0.2,
            "condition": {
                "type": "target_hp_compare",
                "source": "CheckHp",
                "compare": "LT",
                "is_ratio": True,
                "threshold": 0.5,
                "threshold_key": "hp_remain",
                "subject_side": "defender",
            },
            "semantic_source": "typed_current_damageModifier",
            "bb_key": "extra_dmg",
            "recipient_side": "attacker",
            'external_rdps_allowed': False, 'rdps_whitelist_keys': [],
        }
    ]


def test_collect_zone_effects_builds_check_damage_type_condition_from_decoded_detail() -> None:
    record = {
        "event_key": "buff_chr_0011_seraph_talent_1_crystup",
        "source_character_key": "chr_0011_seraph",
        "target_enemy_key": "eny_0051_rodin",
        "bb_keys": ["cryst_up"],
        "bb_values": {"cryst_up": 0.3},
        "attr_mods": [],
    }

    effects = _collect_zone_effects(record)

    assert len(effects) == 1
    assert (effects[0]['zone'], effects[0]['element'], effects[0]['rate']) == ('vuln_taken', 'cryst', 0.3)
    assert effects[0]['rdps_whitelist_keys'] == ['buff_chr_0011_seraph_talent_1_crystup']
    assert _effect_condition_failure(effects[0], {'damage_element': 'cryst'}) is None
    assert _effect_condition_failure(effects[0], {'damage_element': 'fire'})['reason_group'] == 'condition_unsatisfied'


def test_parse_raw_battle_log_text_gates_check_hp_damage_scale() -> None:
    content = "\n".join(
        [
            "[10:00:00.000] GAME_TIMER_START seq=1 source=ChallengeStart startMs=0 expireMs=0 official=1",
            '[10:00:00.000] BUFF_START #1 id="buff_chr_0005_chen_potential_1" uid=1 owner=chr_0005_chen src=chr_0005_chen dur=10.00 lifeT=10.00 passed=0.00 enh=1',
            "[10:00:00.000] BB[1]: hp_remain=0.5 extra_dmg=0.2",
            '[10:00:00.100] HP_V2 #2 hit=100 cum=100 raw=100.00 pHP=5000 eHP=900 skill="chr_0005_chen_attack1" hits=1 src=chr_0005_chen tgt=eny_0051_rodin atk=chr_0005_chen seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:00.200] HP_V2 #3 hit=500 cum=600 raw=500.00 pHP=5000 eHP=400 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:00.300] HP_V2 #4 hit=100 cum=700 raw=100.00 pHP=5000 eHP=300 skill="chr_0005_chen_attack1" hits=2 src=chr_0005_chen tgt=eny_0051_rodin atk=chr_0005_chen seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:01.000] GAME_TIMER_END seq=2 source=ChallengeComplete elapsedMs=1000 startMs=0 expireMs=0 sane=1 official=1",
        ]
    )

    parsed = parse_raw_battle_log_text(content, include_rdps_debug=True, file_name="check-hp-condition.log")
    chen_hits = [hit for hit in parsed["debug_hits"] if hit["character_key"] == "chr_0005_chen"]

    assert all("target_enemy_hp_ratio_before" not in hit for hit in chen_hits)
    assert parsed["rdps_damage_basis"]["rdps_strict_ok"] is False
    assert any(row["reason"] == "unresolved_effect_condition"
               for row in parsed["rdps_preflight"]["blockers"])


def test_parse_raw_battle_log_text_gates_check_damage_type_damage_scale() -> None:
    content = "\n".join(
        [
            "[10:00:00.000] GAME_TIMER_START seq=1 source=ChallengeStart startMs=0 expireMs=0 official=1",
            '[10:00:00.000] BUFF_START #1 id="buff_chr_0011_seraph_talent_1_crystup" uid=1 owner=eny_0051_rodin src=chr_0011_seraph dur=2.00 lifeT=2.00 passed=0.00 enh=1',
            "[10:00:00.000] BB[1]: duration=2 cryst_up=0.3",
            '[10:00:00.100] HP_V2 #2 hit=100 cum=100 raw=100.00 pHP=5000 eHP=900 skill="unknown_proc_damage" hits=1 src=chr_9999_dummy tgt=eny_0051_rodin atk=chr_9999_dummy seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:00.100] DPD_RAW #2 probe=2 calc=100.0000 atkScale=1.0000 blocked=0 damageType=0x2 decorateMask=0x80 collider="BattleShape" atkZones=[1.0000,1.0000,1.0000,1.0000,1.0000,1.0000] defZones=[1.0000,1.0000,1.0000,1.0000,1.0000,1.0000]',
            '[10:00:00.200] HP_V2 #3 hit=130 cum=230 raw=130.00 pHP=5000 eHP=770 skill="unknown_proc_damage" hits=2 src=chr_9999_dummy tgt=eny_0051_rodin atk=chr_9999_dummy seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:00.200] DPD_RAW #3 probe=3 calc=130.0000 atkScale=1.0000 blocked=0 damageType=0x4 decorateMask=0x80 collider="BattleShape" atkZones=[1.0000,1.0000,1.0000,1.0000,1.0000,1.0000] defZones=[1.0000,1.3000,1.0000,1.0000,1.0000,1.0000]',
            "[10:00:01.000] GAME_TIMER_END seq=2 source=ChallengeComplete elapsedMs=1000 startMs=0 expireMs=0 sane=1 official=1",
        ]
    )

    parsed = parse_raw_battle_log_text(content, include_rdps_debug=True, file_name="check-damage-type-condition.log")
    debug_hits = parsed["debug_hits"]

    assert all(hit["damage_element"] is None for hit in debug_hits)
    assert parsed["rdps_damage_basis"]["rdps_strict_ok"] is False
    assert not any(zone.get("external_rate") for hit in debug_hits for zone in hit["zones"])


def test_parse_raw_battle_log_text_uses_dpd_damage_type_as_element_fallback() -> None:
    content = "\n".join(
        [
            "[10:00:00.000] GAME_TIMER_START seq=1 source=ChallengeStart startMs=0 expireMs=0 official=1",
            '[10:00:00.000] BUFF_START #1 id="buff_fire_taken_probe" uid=1 owner=eny_0051_rodin src=chr_0004_pelica dur=2.00 lifeT=2.00 passed=0.00 enh=1',
            "[10:00:00.000]   BB[1]: damage_taken_up_fire=0.2 duration=2",
            '[10:00:00.500] HP_V2 #2 hit=120 cum=120 raw=120.00 pHP=5000 eHP=900000 skill="unknown_proc_damage" hits=1 src=chr_9999_dummy tgt=eny_0051_rodin atk=chr_9999_dummy seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:00.500] DPD_RAW #2 probe=2 calc=120.0000 atkScale=1.0000 blocked=0 damageType=0x2 decorateMask=0x80 collider="BattleShape" atkZones=[1.0000,1.0000,1.0000,1.0000,1.0000,1.0000] defZones=[1.0000,1.2000,1.0000,1.0000,1.0000,1.0000]',
            "[10:00:01.000] GAME_TIMER_END seq=2 source=ChallengeComplete elapsedMs=1000 startMs=0 expireMs=0 sane=1 official=1",
        ]
    )

    parsed = parse_raw_battle_log_text(content, file_name="dpd-damage-type-fallback.log")
    damage_event = next(event for event in parsed["timeline_events"] if event["lane_type"] == "skill")
    rdps_by_key = {
        contribution["character_key"]: contribution["value"]
        for contribution in damage_event["rdps_contributions"]
    }

    assert damage_event["damage_element"] is None
    assert rdps_by_key == {"chr_9999_dummy": 120.0}
    assert parsed["rdps_damage_basis"]["rdps_strict_ok"] is False


def test_parse_raw_battle_log_text_prefers_game_timer_window() -> None:
    content = "\n".join(
        [
            "[09:59:59.000] GAME_TIMER_START seq=1 source=ChallengeStart self=0000000000000000 msg=0000000000000000 startMs=123 expireMs=0 prepareSeconds=0",
            '[10:00:00.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=5000 eHP=900000 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:10.000] HP_V2 #2 hit=200 cum=300 raw=200.00 pHP=5000 eHP=899800 skill="chr_0027_tangtang_attack1" hits=2 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:01:05.000] GAME_TIMER_END seq=1 source=ChallengeComplete self=0000000000000000 msg=0000000000000000 elapsedMs=66000 startMs=123 expireMs=0 forceLeaveTs=0 sane=1",
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        first_hit_hint="10-00-00-000",
        last_hit_hint="10-00-10-000",
    )

    assert parsed["battle"]["duration_ms"] == 66000
    assert parsed["battle"]["time_source"] == "game_timer"
    assert parsed["battle"]["timeline_zero_source"] == "game_timer_start"
    assert parsed["battle"]["timer_start_seen"] is True
    assert parsed["battle"]["timer_end_seen"] is True
    assert parsed["battle"]["timer_start_inferred"] is False
    damage_events = [event for event in parsed["timeline_events"] if event["lane_type"] == "skill"]
    assert damage_events[0]["ts_ms_from_start"] == 1000


def test_parse_raw_battle_log_text_ignores_synthetic_battle_state_timer() -> None:
    content = "\n".join(
        [
            "[10:00:00.000] GAME_TIMER_START seq=1 source=BattleOpModifyBattleState startMs=1 expireMs=0 official=0",
            '[10:00:01.000] SKILL_CAST_START seq=2 startMs=1000 inst=1 owner=chr_0027_tangtang skill=chr_0027_tangtang_skill_2038',
            '[10:00:02.000] HP_V2 #1 hit=50 cum=50 raw=50.00 pHP=5000 eHP=900000 skill="skill_793" hits=1 src=eny_0051_rodin tgt=chr_0004_pelica atk=eny_0051_rodin seg=0 shared=2 critFlag=0 critDmg=-1.0000',
            '[10:00:04.000] HP_V2 #2 hit=100 cum=100 raw=100.00 pHP=5000 eHP=899900 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:09.000] HP_V2 #3 hit=150 cum=250 raw=150.00 pHP=5000 eHP=899750 skill="chr_0027_tangtang_attack1" hits=2 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:10.000] GAME_TIMER_END seq=1 source=BattleOpModifyBattleState elapsedMs=10000 startMs=1 expireMs=0 sane=1 official=0",
        ]
    )

    parsed = parse_raw_battle_log_text(content, file_name="synthetic-timer.log")

    assert parsed["battle"]["time_source"] == "party_action_window"
    assert parsed["battle"]["timeline_zero_source"] == "party_action_window"
    assert parsed["battle"]["timer_start_seen"] is False
    assert parsed["battle"]["timer_end_seen"] is False
    assert parsed["battle"]["duration_ms"] == 8000
    damage_events = [event for event in parsed["timeline_events"] if event["lane_type"] == "skill"]
    assert damage_events[0]["ts_ms_from_start"] == 3000


def test_parse_raw_battle_log_text_accepts_standard_packet_battle_state_timer() -> None:
    content = "\n".join(
        [
            "[10:00:00.000] GAME_TIMER_START seq=1 source=PacketBattleState startMs=1000 expireMs=0 official=1",
            '[10:00:04.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=5000 eHP=899900 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:10.000] GAME_TIMER_END seq=1 source=PacketBattleState elapsedMs=9000 startMs=1000 endMs=10000 expireMs=0 sane=1 official=1",
        ]
    )

    parsed = parse_raw_battle_log_text(content, file_name="packet-timer.log")

    assert parsed["battle"]["time_source"] == "game_timer"
    assert parsed["battle"]["duration_ms"] == 9000
    assert parsed["battle"]["timeline_zero_source"] == "game_timer_start"
    assert parsed["battle"]["timer_start_inferred"] is False
    damage_events = [event for event in parsed["timeline_events"] if event["lane_type"] == "skill"]
    assert damage_events[0]["ts_ms_from_start"] == 4000


def test_parse_raw_battle_log_text_uses_wall_elapsed_for_packet_battle_state_timer() -> None:
    content = "\n".join(
        [
            "[10:00:00.000] GAME_TIMER_START seq=1 source=PacketBattleState startMs=1000 expireMs=0 official=1",
            '[10:00:09.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=5000 eHP=100 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:12.000] HP_V2 #2 hit=200 cum=200 raw=200.00 pHP=5000 eHP=0 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:13.000] GAME_TIMER_END seq=1 source=PacketBattleState elapsedMs=9000 startMs=1000 endMs=10000 expireMs=0 sane=1 official=1 packetElapsedMs=9000 wallElapsedMs=13000",
        ]
    )

    parsed = parse_raw_battle_log_text(content, file_name="packet-wall-window.log")

    assert parsed["battle"]["time_source"] == "game_timer"
    assert parsed["battle"]["duration_ms"] == 13000
    assert parsed["battle"]["clear_flag"] is True
    assert parsed["battle"]["total_damage"] == 200
    damage_events = [event for event in parsed["timeline_events"] if event["lane_type"] == "skill"]
    assert damage_events[-1]["ts_ms_from_start"] == 12000


def test_parse_raw_battle_log_text_prefers_official_timer_pass_time() -> None:
    content = "\n".join(
        [
            "[10:00:00.000] OFFICIAL_TIMER_START source=SC_GAME_MECHANICS_SYNC_CHALLENGE_START gameId=indie_hard007_s challengeStartTs=1000 challengeExpireTs=61000",
            "[10:00:02.000] GAME_TIMER_START seq=1 source=PacketBattleState startMs=1000 expireMs=0 official=1",
            '[10:00:10.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=5000 eHP=100 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:01:23.000] HP_V2 #2 hit=200 cum=200 raw=200.00 pHP=5000 eHP=0 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:01:23.945] OFFICIAL_TIMER_END source=SC_GAME_MECHANICS_SYNC_CHALLENGE_COMPLETE gameId=indie_hard007_s isPass=1 passTime=83945",
            "[10:01:24.000] GAME_TIMER_END seq=1 source=PacketBattleState elapsedMs=82000 startMs=1000 endMs=83000 expireMs=0 sane=1 official=1 packetElapsedMs=64000",
        ]
    )

    parsed = parse_raw_battle_log_text(content, file_name="official-timer.log")

    assert parsed["battle"]["time_source"] == "game_timer"
    assert parsed["battle"]["duration_ms"] == 83945
    assert parsed["battle"]["timeline_zero_source"] == "official_timer_start"
    assert parsed["battle"]["timer_start_seen"] is True
    assert parsed["battle"]["timer_end_seen"] is True
    assert parsed["battle"]["official_timer_start_seen"] is True
    assert parsed["battle"]["official_timer_end_seen"] is True
    assert parsed["battle"]["timer_start_inferred"] is False
    damage_events = [event for event in parsed["timeline_events"] if event["lane_type"] == "skill"]
    assert damage_events[0]["ts_ms_from_start"] == 10000


def test_parse_raw_battle_log_text_uses_official_timer_start_without_timer_end() -> None:
    content = "\n".join(
        [
            "[10:00:00.000] OFFICIAL_TIMER_START source=SC_GAME_MECHANICS_SYNC_CHALLENGE_START gameId=indie_hard007_s challengeStartTs=1000",
            '[10:00:05.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=5000 eHP=100 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_raw_battle_log_text(content, file_name="official-start-only.log")

    assert parsed["battle"]["time_source"] == "game_timer"
    assert parsed["battle"]["timeline_zero_source"] == "official_timer_start"
    assert parsed["battle"]["duration_ms"] == 5000
    assert parsed["battle"]["timer_start_seen"] is True
    assert parsed["battle"]["timer_end_seen"] is False
    assert parsed["battle"]["official_timer_start_seen"] is True
    assert parsed["battle"]["official_timer_end_seen"] is False
    damage_events = [event for event in parsed["timeline_events"] if event["lane_type"] == "skill"]
    assert damage_events[0]["ts_ms_from_start"] == 5000


def test_packet_phase_end_without_official_completion_stays_unfinished() -> None:
    content = "\n".join(
        [
            "[10:00:00.000] OFFICIAL_TIMER_START source=SC_GAME_MECHANICS_SYNC_CHALLENGE_START gameId=indie_hard007_s challengeStartTs=1000",
            "[10:00:00.100] GAME_TIMER_START seq=1 source=PacketBattleState startMs=1000 expireMs=0 official=1",
            '[10:00:02.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=5000 eHP=100 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:04.000] HP_V2 #2 hit=200 cum=300 raw=200.00 pHP=5000 eHP=0 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:05.000] GAME_TIMER_END seq=1 source=PacketBattleState elapsedMs=5000 startMs=1000 endMs=6000 expireMs=0 sane=1 official=1",
        ]
    )

    parsed = parse_raw_battle_log_text(content, file_name="official-start-game-end.log")

    assert parsed["battle"]["duration_ms"] == 4000
    assert parsed["battle"]["timer_start_seen"] is True
    assert parsed["battle"]["timer_end_seen"] is False
    assert parsed["battle"]["official_timer_start_seen"] is True
    assert parsed["battle"]["official_timer_end_seen"] is False
    assert parsed["battle"]["clear_flag"] is False


def test_parse_raw_battle_log_text_keeps_hits_until_official_timer_end_when_pass_time_is_shorter_than_wall_clock() -> None:
    content = "\n".join(
        [
            "[10:00:00.000] OFFICIAL_TIMER_START source=SC_GAME_MECHANICS_SYNC_CHALLENGE_START gameId=dung02_bossrush02_03 challengeStartTs=1000",
            '[10:00:02.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=5000 eHP=0 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0078_nefarp1 atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:03.000] GAME_TIMER_END seq=1 source=PacketBattleState elapsedMs=3000 startMs=0 endMs=3000 expireMs=0 sane=1 official=1",
            "[10:00:04.000] GAME_TIMER_START seq=2 source=PacketBattleState startMs=3000 expireMs=0 official=1",
            '[10:00:07.000] HP_V2 #2 hit=200 cum=200 raw=200.00 pHP=5000 eHP=300 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0079_nefarp2 atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:09.000] HP_V2 #3 hit=300 cum=300 raw=300.00 pHP=5000 eHP=0 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0079_nefarp2 atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:10.000] OFFICIAL_TIMER_END source=SC_GAME_MECHANICS_SYNC_CHALLENGE_COMPLETE gameId=dung02_bossrush02_03 isPass=1 passTime=8000",
        ]
    )

    parsed = parse_raw_battle_log_text(content, file_name="official-multiphase-wall-drift.log")

    assert parsed["battle"]["duration_ms"] == 8000
    assert parsed["battle"]["boss_key"] == "eny_0079_nefarp2"
    assert parsed["battle"]["clear_flag"] is True
    assert parsed["battle"]["total_damage"] == 600
    damage_events = [event for event in parsed["timeline_events"] if event["lane_type"] == "skill"]
    assert [event["value"] for event in damage_events] == [100, 200, 300]


def test_parse_raw_battle_log_text_retargets_unknown_enemy_from_dungeon_context() -> None:
    content = "\n".join(
        [
            "[10:00:00.000] DUNGEON_CONTEXT dungeonId=dung02_bossrush02_03 source=SC_SELF_SCENE_INFO",
            '[10:00:01.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=0 eHP=0 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0000_unknown atk=chr_0027_tangtang atkId=100701 tgtId=100804 seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_raw_battle_log_text(content, file_name="unknown-boss-context.log")

    assert parsed["battle"]["boss_key"] == "eny_0079_nefarp2"
    assert parsed["battle"]["boss_name"] == "聂菲斯，“征服者”"
    assert parsed["battle"]["clear_flag"] is True


def test_parse_raw_battle_log_text_resolves_contingency_contract_context() -> None:
    content = "\n".join(
        [
            "[10:00:00.000] DUNGEON_CONTEXT dungeonId=indie_contract001 source=SC_SELF_SCENE_INFO",
            "[10:00:00.500] CONTRACT_TAGS dungeonId=indie_contract001 source=SC_CONTINGENCY_CONTRACT_TAGS_SYNC tagIds=[100501,101301,102801,100201,102001,102101,900101,101001,100901,100601] score=10",
            '[10:00:01.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=0 eHP=0 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0090_wgabyss atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_raw_battle_log_text(content, file_name="contingency-contract-context.log")

    assert parsed["battle"]["dungeon_key"] == "indie_group_ccdg"
    assert parsed["battle"]["dungeon_name"] == "危机合约"
    assert parsed["battle"]["boss_key"] == "eny_0090_wgabyss"
    assert parsed["battle"]["boss_name"] == "破潮之像"
    assert parsed["battle"]["clear_flag"] is True
    assert parsed["battle"]["contract_tag_score"] == 10
    assert [tag["tag_id"] for tag in parsed["battle"]["contract_tags"]] == [
        100501,
        101301,
        102801,
        100201,
        102001,
        102101,
        900101,
        101001,
        100901,
        100601,
    ]
    assert parsed["battle"]["contract_tags"][0]["score"] == 1
    assert parsed["battle"]["contract_tags"][0]["icon"] == "icon_activity_contract_tag_207"


def test_parse_raw_battle_log_text_keeps_war_echo_highest_stage_identity() -> None:
    content = "\n".join(
        [
            "[10:00:00.000] DUNGEON_CONTEXT dungeonId=indie_battletower008_ex source=SC_SELF_SCENE_INFO",
            '[10:00:01.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=0 eHP=0 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0114_jzmking atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_raw_battle_log_text(content, file_name="war-echo-context.log")

    assert parsed["battle"]["dungeon_key"] == "indie_battletower008_ex"
    assert parsed["battle"]["dungeon_name"] == "战争简史·残酷"


def test_parse_raw_battle_log_text_keeps_war_echo_lower_difficulty_out_of_highest_board() -> None:
    content = "\n".join(
        [
            "[10:00:00.000] DUNGEON_CONTEXT dungeonId=indie_battletower008_s source=SC_SELF_SCENE_INFO",
            '[10:00:01.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=0 eHP=0 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0114_jzmking atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_raw_battle_log_text(content, file_name="war-echo-lower-context.log")

    assert parsed["battle"]["dungeon_key"] == "indie_battletower008_s"
    assert parsed["battle"]["dungeon_name"] == "战争简史·困难"


def test_parse_raw_battle_log_text_uses_selected_contract_tag_score_sum() -> None:
    content = "\n".join(
        [
            "[13:23:28.718] DUNGEON_CONTEXT dungeonId=indie_contract001 source=CS_ENTER_DUNGEON",
            "[13:23:26.638] CONTRACT_TAGS dungeonId=indie_contract001 source=CS_CONTINGENCY_CONTRACT_SET_TAGS tagIds=[102801,100201,102102]",
            "[13:26:40.542] CONTRACT_TAGS dungeonId=indie_contract001 source=SC_CONTINGENCY_CONTRACT_BATTLE_RESULT tagIds=[102801,100201,102102] score=4",
            "[13:26:40.542] CONTRACT_TAGS dungeonId=indie_contract001 source=SC_CONTINGENCY_CONTRACT_TAGS_SYNC tagIds=[102801,100201,102102] score=10",
            '[13:26:41.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=0 eHP=0 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0090_wgabyss atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_raw_battle_log_text(content, file_name="contingency-contract-score.log")

    assert parsed["battle"]["contract_tag_score"] == 4
    assert [(tag["tag_id"], tag["score"]) for tag in parsed["battle"]["contract_tags"]] == [
        (102801, 1),
        (100201, 1),
        (102102, 2),
    ]


def test_parse_raw_battle_log_text_recomputes_contract_score_from_current_catalog() -> None:
    content = "\n".join(
        [
            "[15:42:10.000] DUNGEON_CONTEXT dungeonId=indie_contract001 source=SC_SELF_SCENE_INFO",
            "[15:46:12.000] CONTRACT_TAGS dungeonId=indie_contract001 source=SC_CONTINGENCY_CONTRACT_TAGS_SYNC tagIds=[101101,102201,102302,100003,900101,101501] score=46",
            '[15:46:12.100] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=0 eHP=0 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0090_wgabyss atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_raw_battle_log_text(content, file_name="contingency-contract-catalog-score.log")

    assert parsed["battle"]["contract_tag_score"] == 10
    assert [(tag["tag_id"], tag["name"], tag["score"]) for tag in parsed["battle"]["contract_tags"]] == [
        (101101, "改写：愈合", 1),
        (102201, "环境：再构成", 2),
        (102302, "改写：奔腾", 2),
        (100003, "环境：过速", 3),
        (900101, "改写：活性", 1),
        (101501, "改写：遗毒", 1),
    ]


def test_parse_raw_battle_log_text_uses_current_battle_loadout_snapshot() -> None:
    content = "\n".join(
        [
            "[10:00:00.000] LOADOUT reason=SC_ITEM_BAG_SCOPE_MODIFY slotCount=2 memberCount=2 roster=[1 2]",
            "[10:00:00.000] LOADOUT slot=0 char=chr_0016_laevat weaponTemplate=wpn_sword_0006 equipSuit={}",
            "[10:00:00.000] LOADOUT slot=1 char=chr_0006_wolfgd weaponTemplate=wpn_pistol_0012 equipSuit={}",
            "[10:00:10.000] DUNGEON_CONTEXT dungeonId=dung01_bossrush01_04 source=SC_SELF_SCENE_INFO",
            "[10:00:11.000] LOADOUT reason=BATTLE_START slotCount=4 memberCount=4 roster=[3 4 5 6]",
            "[10:00:11.000] LOADOUT slot=0 char=chr_0026_lastrite weaponTemplate=wpn_claym_0013 equipSuit={}",
            "[10:00:11.000] LOADOUT slot=1 char=chr_0027_tangtang weaponTemplate=wpn_pistol_0011 equipSuit={}",
            "[10:00:11.000] LOADOUT slot=2 char=chr_0011_seraph weaponTemplate=wpn_funnel_0010 equipSuit={}",
            "[10:00:11.000] LOADOUT slot=3 char=chr_0013_aglina weaponTemplate=wpn_funnel_0005 equipSuit={}",
            "[10:00:11.000] GAME_TIMER_START seq=1 source=PacketBattleState startMs=0 expireMs=0 official=1",
            '[10:00:12.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=0 eHP=100 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0000_unknown atk=chr_0027_tangtang atkId=4 tgtId=99 seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:13.000] HP_V2 #2 hit=50 cum=50 raw=50.00 pHP=0 eHP=0 skill="chr_0013_aglina_attack1" hits=1 src=chr_0013_aglina tgt=eny_0000_unknown atk=chr_0013_aglina atkId=6 tgtId=99 seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:15.000] GAME_TIMER_END seq=2 source=PacketBattleState elapsedMs=4000 startMs=0 endMs=4000 expireMs=0 sane=1 official=1",
        ]
    )

    parsed = parse_raw_battle_log_text(content, file_name="current-loadout.log")

    assert parsed["battle"]["boss_key"] == "eny_0051_rodin"
    assert [row["character_key"] for row in parsed["loadout"]] == [
        "chr_0026_lastrite",
        "chr_0027_tangtang",
        "chr_0011_seraph",
        "chr_0013_aglina",
    ]


def test_parse_raw_battle_log_text_retargets_unknown_enemy_from_single_buff_hint() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] BUFF_START #1 id="buff_eny_0078_nefarp1_player_jump" uid=1 owner=eny_0000_unknown src=chr_0027_tangtang dur=10.00 lifeT=10.00 passed=0.00 enh=1',
            '[10:00:01.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=0 eHP=0 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0000_unknown atk=chr_0027_tangtang atkId=100701 tgtId=100804 seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_raw_battle_log_text(content, file_name="unknown-boss-buff.log")

    assert parsed["battle"]["boss_key"] == "eny_0078_nefarp1"
    assert parsed["battle"]["boss_name"] == "聂菲斯，“碾骨”"


def test_parse_raw_battle_log_text_keeps_known_enemy_over_context_hint() -> None:
    content = "\n".join(
        [
            "[10:00:00.000] DUNGEON_CONTEXT dungeonId=dung02_bossrush02_03 source=SC_SELF_SCENE_INFO",
            '[10:00:01.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=0 eHP=0 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_raw_battle_log_text(content, file_name="known-boss-context.log")

    assert parsed["battle"]["boss_key"] == "eny_0051_rodin"
    assert parsed["battle"]["boss_key"] != "eny_0079_nefarp2"


def test_parse_raw_battle_log_text_uses_official_timer_game_id_before_enemy_guess() -> None:
    content = "\n".join(
        [
            "[10:00:00.000] OFFICIAL_TIMER_START source=SC_GAME_MECHANICS_SYNC_CHALLENGE_START gameId=indie_hard013_s challengeStartTs=1000 challengeExpireTs=61000",
            '[10:00:10.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=5000 eHP=100 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0059_erhound atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:20.000] HP_V2 #2 hit=200 cum=200 raw=200.00 pHP=5000 eHP=0 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0059_erhound atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:01:00.000] OFFICIAL_TIMER_END source=SC_GAME_MECHANICS_SYNC_CHALLENGE_COMPLETE gameId=indie_hard013_s isPass=1 passTime=60000",
        ]
    )

    parsed = parse_raw_battle_log_text(content, file_name="official-timer-game-id.log")

    assert parsed["battle"]["dungeon_key"] == "indie_hard013_s"
    assert parsed["battle"]["dungeon_name"] == "沉寂视界·苦难"
    assert parsed["battle"]["duration_ms"] == 60000


def test_parse_raw_battle_log_text_resolves_v1d3_hard_official_timer_game_id() -> None:
    content = "\n".join(
        [
            "[10:00:00.000] OFFICIAL_TIMER_START source=SC_GAME_MECHANICS_SYNC_CHALLENGE_START gameId=indie_hard018_s challengeStartTs=1000 challengeExpireTs=61000",
            '[10:00:10.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=5000 eHP=100 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0082_hsbear_hdg018 atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:20.000] HP_V2 #2 hit=200 cum=200 raw=200.00 pHP=5000 eHP=0 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0082_hsbear_hdg018 atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:40.440] OFFICIAL_TIMER_END source=SC_GAME_MECHANICS_SYNC_CHALLENGE_COMPLETE gameId=indie_hard018_s isPass=1 passTime=39440",
        ]
    )

    parsed = parse_raw_battle_log_text(content, file_name="official-timer-v1d3-hard.log")

    assert parsed["battle"]["dungeon_key"] == "indie_hard018_s"
    assert parsed["battle"]["dungeon_name"] == "忿鼓咆声·苦难"
    assert parsed["battle"]["boss_key"] == "eny_0082_hsbear_hdg018"
    assert parsed["battle"]["duration_ms"] == 39440


def test_parse_raw_battle_log_text_resolves_v1d3_normal_official_timer_game_id() -> None:
    content = "\n".join(
        [
            "[10:00:00.000] OFFICIAL_TIMER_START source=SC_GAME_MECHANICS_SYNC_CHALLENGE_START gameId=indie_hard018 challengeStartTs=1000 challengeExpireTs=61000",
            '[10:00:10.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=5000 eHP=0 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0082_hsbear atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:40.440] OFFICIAL_TIMER_END source=SC_GAME_MECHANICS_SYNC_CHALLENGE_COMPLETE gameId=indie_hard018 isPass=1 passTime=39440",
        ]
    )

    parsed = parse_raw_battle_log_text(content, file_name="official-timer-v1d3-normal.log")

    assert parsed["battle"]["dungeon_key"] == "indie_hard018"
    assert parsed["battle"]["dungeon_name"] == "忿鼓咆声"


def test_parse_raw_battle_log_text_caps_final_boss_overkill_damage() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=5000 eHP=200 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:01.000] HP_V2 #2 hit=500 cum=500 raw=500.00 pHP=5000 eHP=0 skill="chr_0004_pelica_attack1" hits=1 src=chr_0004_pelica tgt=eny_0051_rodin atk=chr_0004_pelica seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_raw_battle_log_text(content)

    assert parsed["battle"]["total_damage"] == 300
    assert parsed["battle"]["clear_flag"] is True
    by_key = {participant["character_key"]: participant for participant in parsed["participants"]}
    assert by_key["chr_0027_tangtang"]["total_damage"] == 100
    assert by_key["chr_0004_pelica"]["total_damage"] == 200
    damage_events = [event for event in parsed["timeline_events"] if event["lane_type"] == "skill"]
    assert damage_events[-1]["value"] == 200


def test_parse_raw_battle_log_text_groups_visual_multi_hit_for_participant_max_hit() -> None:
    content = "\n".join(
        [
            "[10:00:00.000] GAME_TIMER_START seq=1 source=OnSrvStart self=0 startMs=100 expireMs=0",
            '[10:00:01.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=5000 eHP=900000 skill="chr_0016_laevat_ult_attack4" hits=1 src=chr_0016_laevat tgt=eny_0051_rodin atk=chr_0016_laevat seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:01.080] HP_V2 #2 hit=100 cum=100 raw=100.00 pHP=5000 eHP=899900 skill="chr_0016_laevat_ult_attack4" hits=1 src=chr_0016_laevat tgt=eny_0051_rodin atk=chr_0016_laevat seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:02.000] GAME_TIMER_END seq=1 source=OnSrvComplete self=0 elapsedMs=2000 startMs=100 expireMs=0 sane=1",
        ]
    )

    parsed = parse_raw_battle_log_text(content, file_name="visual-max-hit.log")
    participant = next(item for item in parsed["participants"] if item["character_key"] == "chr_0016_laevat")

    assert participant["max_hit"] == 200


def test_parse_raw_battle_log_text_ignores_hits_outside_game_timer_window() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] HP_V2 #1 hit=1000 cum=1000 raw=1000.00 pHP=5000 eHP=900000 skill="chr_0028_wulfa_attack1" hits=1 src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:01.000] HP_V2 #2 hit=2000 cum=2000 raw=2000.00 pHP=5000 eHP=898000 skill="chr_0028_wulfa_attack1" hits=1 src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:10.000] GAME_TIMER_START seq=1 source=OnSrvStart self=0 startMs=100 expireMs=0",
            '[10:00:11.000] HP_V2 #3 hit=4000 cum=4000 raw=4000.00 pHP=5000 eHP=900000 skill="chr_0028_wulfa_attack1" hits=1 src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:13.000] HP_V2 #4 hit=5000 cum=5000 raw=5000.00 pHP=5000 eHP=895000 skill="chr_0028_wulfa_attack1" hits=1 src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:15.000] GAME_TIMER_END seq=1 source=OnSrvComplete self=0 elapsedMs=5000 startMs=100 expireMs=0 sane=1",
        ]
    )

    parsed = parse_raw_battle_log_text(content)

    assert parsed["battle"]["duration_ms"] == 5000
    assert parsed["battle"]["total_damage"] == 9000
    assert parsed["battle"]["total_dps"] == 1800.0
    assert parsed["participants"][0]["total_damage"] == 9000
    damage_events = [event for event in parsed["timeline_events"] if event["lane_type"] == "skill"]
    assert [event["value"] for event in damage_events] == [4000, 5000]


def test_legacy_enemy_buff_without_current_typed_effect_stays_unknown_outside_window() -> None:
    content = "\n".join([
        '[10:00:01.000] BUFF_START #1 id="buff_chr_0013_aglina_ultimate_spell_vulnerable" uid=old owner=eny_0051_rodin src=chr_0013_aglina dur=9999.00 lifeT=9999.00 passed=0.00 enh=1',
        '[10:00:10.000] GAME_TIMER_START seq=1 source=OnSrvStart self=0 startMs=100 expireMs=0',
        '[10:00:11.000] HP_V2 #2 hit=1000 cum=1000 raw=1000.00 pHP=5000 eHP=899000 skill="chr_0028_wulfa_attack1" actionId=0 damageUnitIndex=0 hits=1 src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa seg=0 shared=2 critFlag=0 critDmg=0.5000',
        '[10:00:12.000] BUFF_START #2 id="buff_chr_0013_aglina_ultimate_spell_vulnerable" uid=new owner=eny_0051_rodin src=chr_0013_aglina dur=9999.00 lifeT=9999.00 passed=0.00 enh=1',
        '[10:00:13.000] HP_V2 #3 hit=1000 cum=2000 raw=1000.00 pHP=5000 eHP=898000 skill="chr_0028_wulfa_attack1" actionId=0 damageUnitIndex=0 hits=2 src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa seg=0 shared=2 critFlag=0 critDmg=0.5000',
        '[10:00:15.000] GAME_TIMER_END seq=1 source=OnSrvComplete self=0 elapsedMs=5000 startMs=100 expireMs=0 sane=1',
    ])
    parsed = parse_raw_battle_log_text(content)
    events = [event for event in parsed['buff_events'] if event['event_key'] == 'buff_chr_0013_aglina_ultimate_spell_vulnerable']
    assert len(events) == 2
    assert all(event['zone_effects'] == [] for event in events)
    assert parsed['rdps_damage_basis']['rdps_strict_ok'] is False


def test_parse_raw_battle_log_text_can_infer_timer_start_from_end_elapsed() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=5000 eHP=900000 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:01:06.000] GAME_TIMER_END seq=1 source=ChallengeComplete self=0 elapsedMs=66000 startMs=0 expireMs=0 sane=1",
        ]
    )

    parsed = parse_raw_battle_log_text(content)

    assert parsed["battle"]["duration_ms"] == 66000
    assert parsed["battle"]["time_source"] == "game_timer"
    assert parsed["battle"]["timeline_zero_source"] == "timer_end_inferred"
    assert parsed["battle"]["timer_start_seen"] is False
    assert parsed["battle"]["timer_end_seen"] is True
    assert parsed["battle"]["official_timer_start_seen"] is False
    assert parsed["battle"]["official_timer_end_seen"] is False
    assert parsed["battle"]["timer_start_inferred"] is True
    damage_events = [event for event in parsed["timeline_events"] if event["lane_type"] == "skill"]
    assert damage_events[0]["ts_ms_from_start"] == 0


def test_parse_raw_battle_log_text_builds_structured_payload() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=5000 eHP=900000 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:00.500] ATTR_MOD buff="buff_equipsuit_critsuitatk_01" i=0 attrType=2 modType=0 formula=6 useKey=1 val=0.0500 bbKey="atk_up"',
            '[10:00:00.500] BUFF_START #2 id="buff_equipsuit_critsuitatk_01" uid=1 owner=chr_0027_tangtang src=chr_0027_tangtang dur=5.00 lifeT=5.00 passed=0.00 enh=1',
            '[10:00:00.500]   BB[4]: atk_up=0.05 =0 crit_up2=0.05 =0',
            '[10:00:00.600] ATTR_MOD buff="buff_common_dash" i=0 attrType=14 modType=0 formula=0 useKey=0 val=2.0000 bbKey=""',
            '[10:00:00.600] BUFF_START #3 id="buff_common_dash" uid=2 owner=chr_0027_tangtang src=chr_0027_tangtang dur=0.33 lifeT=0.33 passed=0.00 enh=1',
            '[10:00:00.600]   BB[2]: dodgeSkillId=0 common_character_perfect_dodge=0',
            '[10:00:01.000] HP_V2 #3 hit=250 cum=350 raw=250.00 pHP=5000 eHP=899750 skill="chr_0027_tangtang_attack1" hits=2 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=3 critFlag=1 critDmg=0.5000',
            '[10:00:01.750] BUFF_END #4 id="buff_equipsuit_critsuitatk_01" uid=1 owner=chr_0027_tangtang src=chr_0027_tangtang dur=5.00 lifeT=3.75 passed=1.25 enh=1',
            '[10:00:02.000] HP_V2 #4 hit=300 cum=300 raw=300.00 pHP=4800 eHP=899450 skill="chr_0004_pelica_attack1" hits=1 src=chr_0004_pelica tgt=eny_0051_rodin atk=chr_0004_pelica seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="sample.log",
        first_hit_hint="10-00-00-000",
        last_hit_hint="10-00-02-000",
    )

    assert parsed["battle"]["boss_key"] == "eny_0051_rodin"
    assert parsed["battle"]["boss_name"] == "“碾骨之拳”罗丹"
    assert parsed["battle"]["dungeon_key"] == "unknown_dungeon"
    assert parsed["battle"]["dungeon_name"] == "未知副本"
    assert parsed["battle"]["dungeon_context_id"] is None
    assert parsed["battle"]["dungeon_identity_source"] == "missing_dungeon_context"
    assert parsed["battle"]["duration_ms"] == 2000
    assert parsed["battle"]["total_damage"] == 650
    assert parsed["battle"]["source_file_name"] == "sample.log"

    roster_names = [entry["character_name"] for entry in parsed["battle"]["roster"]]
    assert roster_names == ["汤汤", "佩丽卡"]

    assert parsed["participants"][0]["character_name"] == "汤汤"
    assert parsed["participants"][0]["total_damage"] == 350
    assert parsed["participants"][0]["crit_rate"] == 0.5
    assert parsed["participants"][0]["rdps"] == parsed["participants"][0]["dps"]

    buff_events = [event for event in parsed["timeline_events"] if event["lane_type"] == "buff"]
    assert len(buff_events) == 1
    assert buff_events[0]["event_key"] == "buff_equipsuit_critsuitatk_01"
    assert buff_events[0]["event_name"] == "攻击提升 / 暴击"
    assert buff_events[0]["duration_ms"] == 1250
    assert buff_events[0]["target_player_key"] == "chr_0027_tangtang"
    assert buff_events[0]["target_enemy_key"] is None

    damage_events = [event for event in parsed["timeline_events"] if event["lane_type"] == "skill"]
    assert damage_events[0]["event_name"] == "崩你脑壳！"
    assert damage_events[0]["event_group_key"] is not None
    assert damage_events[0]["event_group_key"] == damage_events[1]["event_group_key"]
    assert damage_events[2]["event_group_key"] != damage_events[0]["event_group_key"]
    assert damage_events[0]["rdps_contributions"] == [
        {
            "character_key": "chr_0027_tangtang",
            "character_name": "汤汤",
            "value": 100.0,
        }
    ]

    skill_stats = parsed["role_skill_stats"]
    tangtang_stats = [item for item in skill_stats if item["character_key"] == "chr_0027_tangtang"]
    assert tangtang_stats[0]["cast_count"] == 1
    assert tangtang_stats[0]["total_damage"] == 350
    assert tangtang_stats[0]["skill_name"] == "崩你脑壳！"


def test_parse_raw_battle_log_text_uses_packet_dungeon_context_for_high_difficulty_stage() -> None:
    for stage, name in [('indie_hard008_s', '怨憎雾海·苦难'), ('indie_hard026_s', '兽影生杀·苦难'), ('indie_hard026', '兽影生杀'), ('indie_hard027_s', '暗曜白霆·苦难'), ('indie_hard027', '暗曜白霆'), ('indie_hard028_s', '沛然轰鸣·苦难'), ('indie_hard028', '沛然轰鸣'), ('indie_hard029_s', '三角定位·苦难'), ('indie_hard029', '三角定位')]:
        content = "\n".join(
            [
                f"[10:00:00.000] DUNGEON_CONTEXT dungeonId={stage} source=SC_SELF_SCENE_INFO scene=210 isReward=1 isCalc=0 isPass=0",
                '[10:00:00.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=5000 eHP=733631 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0085_hsrogue_hard atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            ]
        )

        parsed = parse_raw_battle_log_text(content)

        assert parsed["battle"]["dungeon_key"] == stage
        assert parsed["battle"]["dungeon_name"] == name
        assert parsed["battle"]["dungeon_context_id"] == stage
        assert parsed["battle"]["dungeon_identity_source"] == "dungeon_context"

def test_parse_raw_battle_log_text_uses_official_game_id_even_when_boss_belongs_elsewhere() -> None:
    content = "\n".join(
        [
            "[10:00:00.000] OFFICIAL_TIMER_START source=SC_GAME_MECHANICS_SYNC_CHALLENGE_START gameId=indie_battletower004_ex challengeStartTs=1000",
            '[10:00:01.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=5000 eHP=900000 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_raw_battle_log_text(content)

    assert parsed["battle"]["dungeon_key"] == "indie_battletower004_ex"
    assert parsed["battle"]["dungeon_name"] == "斧柄纪年·残酷"
    assert parsed["battle"]["dungeon_context_id"] == "indie_battletower004_ex"
    assert parsed["battle"]["dungeon_identity_source"] == "dungeon_context"
    assert parsed["battle"]["boss_key"] == "eny_0051_rodin"


def test_parse_raw_battle_log_text_exposes_official_stage_before_first_hit() -> None:
    parsed = parse_raw_battle_log_text(
        "[10:00:00.000] DUNGEON_CONTEXT dungeonId=indie_battletower004_ex source=SC_SELF_SCENE_INFO"
    )

    assert parsed["battle"]["dungeon_key"] == "indie_battletower004_ex"
    assert parsed["battle"]["dungeon_name"] == "斧柄纪年·残酷"
    assert parsed["battle"]["boss_key"] == "unknown_boss"
    assert parsed["participants"] == []


def test_parse_raw_battle_log_text_uses_generated_activity_child_name() -> None:
    parsed = parse_raw_battle_log_text(
        "[10:00:00.000] DUNGEON_CONTEXT dungeonId=dung01_takestwo007 source=SC_SELF_SCENE_INFO"
    )

    assert parsed["battle"]["dungeon_key"] == "dung01_takestwo007"
    assert parsed["battle"]["dungeon_name"] == "什么！还有动物！"
    assert parsed["battle"]["dungeon_context_id"] == "dung01_takestwo007"
    assert parsed["participants"] == []


def test_parse_raw_battle_log_text_does_not_guess_dungeon_for_unmapped_official_id() -> None:
    content = "\n".join(
        [
            "[10:00:00.000] DUNGEON_CONTEXT dungeonId=dung_future_unmapped source=SC_SELF_SCENE_INFO",
            '[10:00:01.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=5000 eHP=900000 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_raw_battle_log_text(content)

    assert parsed["battle"]["dungeon_key"] == "unknown_dungeon"
    assert parsed["battle"]["dungeon_name"] == "未知副本"
    assert parsed["battle"]["dungeon_context_id"] == "dung_future_unmapped"
    assert parsed["battle"]["dungeon_identity_source"] == "unmapped_dungeon_context"
    assert parsed["battle"]["boss_key"] == "eny_0051_rodin"


def test_parse_raw_battle_log_text_clips_buff_timeline_duration_to_battle_window() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] ATTR_MOD buff="buff_common_natural_cryst_triggered" i=0 attrType=82 modType=0 formula=5 useKey=1 val=0.0000 bbKey="def_decrease"',
            '[10:00:00.000] BUFF_START #1 id="buff_common_natural_cryst_triggered" uid=1 owner=eny_0051_rodin src=chr_0013_aglina dur=15.00 lifeT=15.00 passed=0.00 enh=1',
            "[10:00:00.000]   BB[4]: def_decrease=0.08129 =0 max_def_decrease=0.271 =0 def_decrease_tick=0.01897 =0 duration=15",
            '[10:00:10.500] HP_V2 #2 hit=150 cum=150 raw=150.00 pHP=5000 eHP=900000 skill="buff_chr_0026_lastrite_normal_skill_phantom_main" hits=1 src=chr_0026_lastrite tgt=eny_0051_rodin atk=chr_0026_lastrite seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="prebattle-buff.log",
        first_hit_hint="10-00-10-000",
        last_hit_hint="10-00-11-000",
    )

    buff_event = next(event for event in parsed["timeline_events"] if event["lane_type"] == "buff")
    assert buff_event["ts_ms_from_start"] == 0
    assert buff_event["duration_ms"] == 1000
    assert buff_event["target_player_key"] is None
    assert buff_event["target_enemy_key"] == "eny_0051_rodin"


def test_parse_raw_battle_log_text_keeps_potion_duration_when_refresh_end_is_same_tick() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] ATTR_MOD buff="buff_common_atk_buff_potion_1" i=0 attrType=2 modType=0 formula=6 useKey=1 val=0.1000 bbKey="value"',
            '[10:00:00.000] BUFF_START #1 id="buff_common_atk_buff_potion_1" uid=1 owner=chr_0027_tangtang src=chr_0027_tangtang dur=300.00 lifeT=300.00 passed=0.00 enh=1',
            '[10:00:00.000]   BB[1]: value=0.27',
            '[10:00:00.000] BUFF_END #2 id="buff_common_atk_buff_potion_1" uid=1 owner=chr_0027_tangtang src=chr_0027_tangtang dur=300.00 lifeT=245.71 passed=0.00 enh=1',
            '[10:00:00.001] ATTR_MOD buff="buff_common_atk_buff_potion_1" i=0 attrType=2 modType=0 formula=6 useKey=1 val=0.1000 bbKey="value"',
            '[10:00:00.001] BUFF_START #3 id="buff_common_atk_buff_potion_1" uid=2 owner=chr_0027_tangtang src=chr_0027_tangtang dur=300.00 lifeT=300.00 passed=0.00 enh=1',
            '[10:00:00.001]   BB[1]: value=0.27',
            '[10:00:05.000] HP_V2 #4 hit=127 cum=127 raw=127.00 pHP=5000 eHP=900000 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="potion-refresh.log",
        first_hit_hint="10-00-00-000",
        last_hit_hint="10-00-05-000",
    )

    buff_events = [
        event
        for event in parsed["timeline_events"]
        if event["lane_type"] == "buff" and event["event_key"] == "buff_common_atk_buff_potion_1"
    ]
    assert len(buff_events) == 1
    assert buff_events[0]["duration_ms"] == 5000
    assert buff_events[0]["actual_duration_ms"] == 5000


def test_parse_raw_battle_log_text_filters_effectless_wrapper_blackboard_values() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] BUFF_START #1 id="buff_chr_0011_seraph_spawnball" uid=1 owner=chr_0011_seraph src=chr_0011_seraph dur=2.00 lifeT=2.00 passed=0.00 enh=1',
            '[10:00:00.000]   BB[3]: atk_up=0.18 will_up=0.74 atk_scale=0.1',
            '[10:00:01.000] HP_V2 #2 hit=100 cum=100 raw=100.00 pHP=5000 eHP=900000 skill="chr_0011_seraph_attack1" hits=1 src=chr_0011_seraph tgt=eny_0051_rodin atk=chr_0011_seraph seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="effectless-wrapper.log",
        first_hit_hint="10-00-00-000",
        last_hit_hint="10-00-01-000",
    )

    assert [event for event in parsed["timeline_events"] if event["lane_type"] == "buff"] == []


def test_parse_raw_battle_log_text_filters_registry_verified_character_wrappers() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] BUFF_START #1 id="buff_chr_0004_pelica_potential_3" uid=1 owner=chr_0004_pelica src=chr_0004_pelica dur=5.00 lifeT=5.00 passed=0.00 enh=1',
            "[10:00:00.000]   BB[1]: atk_up=0.2 atk_duration=5 max_stack=1",
            '[10:00:00.100] BUFF_START #2 id="buff_chr_0027_tangtang_passive_0" uid=2 owner=chr_0027_tangtang src=chr_0027_tangtang dur=5.00 lifeT=5.00 passed=0.00 enh=1',
            "[10:00:00.100]   BB[2]: rate_spellvulnerable=0.1 rate_spellvulnerable_02=0.05 duration_spellvulnerable=5",
            '[10:00:01.000] HP_V2 #3 hit=100 cum=100 raw=100.00 pHP=5000 eHP=900000 skill="chr_0004_pelica_attack1" hits=1 src=chr_0004_pelica tgt=eny_0051_rodin atk=chr_0004_pelica seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="registry-character-wrapper.log",
        first_hit_hint="10-00-00-000",
        last_hit_hint="10-00-01-000",
    )

    by_key = {event["event_key"]: event for event in parsed["buff_events"]}
    assert by_key["buff_chr_0004_pelica_potential_3"]["zone_effects"] == []
    assert by_key["buff_chr_0027_tangtang_passive_0"]["zone_effects"] == []
    assert [event for event in parsed["timeline_events"] if event["lane_type"] == "buff"] == []


def test_parse_raw_battle_log_text_keeps_registry_leaf_character_effect() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] BUFF_START #1 id="buff_chr_0004_pelica_potential_3_atkup" uid=1 owner=chr_0004_pelica src=chr_0004_pelica dur=5.00 lifeT=5.00 passed=0.00 enh=1',
            "[10:00:00.000]   BB[1]: atk_up=0.2 atk_duration=5",
            '[10:00:01.000] HP_V2 #2 hit=100 cum=100 raw=100.00 pHP=5000 eHP=900000 skill="chr_0004_pelica_attack1" hits=1 src=chr_0004_pelica tgt=eny_0051_rodin atk=chr_0004_pelica seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="registry-character-leaf.log",
        first_hit_hint="10-00-00-000",
        last_hit_hint="10-00-01-000",
    )

    buff_event = next(event for event in parsed["buff_events"] if event["event_key"] == "buff_chr_0004_pelica_potential_3_atkup")
    assert buff_event["zone_effects"] == [{
        "zone": "atk", "element": "all", "rate": 0.2,
        "attr_type": 2, "bb_key": "atk_up",
        "semantic_source": "typed_current_attributeModifier",
        "external_rdps_allowed": False, "rdps_whitelist_keys": [],
    }]


def test_parse_raw_battle_log_text_maps_display_skill_names_to_generic_labels() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=5000 eHP=900000 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:00.100] HP_V2 #2 hit=100 cum=100 raw=100.00 pHP=5000 eHP=899900 skill="chr_0027_tangtang_attack4" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:00.200] HP_V2 #3 hit=100 cum=100 raw=100.00 pHP=5000 eHP=899800 skill="chr_0027_tangtang_attack5" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:00.300] HP_V2 #4 hit=100 cum=100 raw=100.00 pHP=5000 eHP=899700 skill="chr_0027_tangtang_power_attack" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:00.400] HP_V2 #5 hit=100 cum=100 raw=100.00 pHP=5000 eHP=899600 skill="chr_0027_tangtang_normal_skill" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:00.500] HP_V2 #6 hit=100 cum=100 raw=100.00 pHP=5000 eHP=899500 skill="chr_0027_tangtang_combo_2_skill" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:00.600] HP_V2 #7 hit=100 cum=100 raw=100.00 pHP=5000 eHP=899400 skill="chr_0027_tangtang_ultimate_skill_1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:00.700] HP_V2 #8 hit=100 cum=100 raw=100.00 pHP=5000 eHP=899300 skill="chr_0027_tangtang_execute" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:00.800] HP_V2 #9 hit=100 cum=100 raw=100.00 pHP=5000 eHP=899200 skill="buff_common_cryst_triggered_physical_break" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="generic-display-names.log",
        first_hit_hint="10-00-00-000",
        last_hit_hint="10-00-01-000",
    )

    names = [event["event_name"] for event in parsed["timeline_events"] if event["lane_type"] == "skill"]
    assert names[-1] == "猛击"


def test_parse_raw_battle_log_text_ignores_non_enemy_hp_changes() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=5000 eHP=900000 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:00.500] HP_V2 #2 hit=9999 cum=9999 raw=9999.00 pHP=4500 eHP=5000 skill="buff_chr_0023_antal_talent_1_heal_trigger" hits=1 src=chr_0023_antal tgt=chr_0027_tangtang atk=? seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:01.000] HP_V2 #3 hit=200 cum=300 raw=200.00 pHP=5000 eHP=899800 skill="chr_0027_tangtang_attack2" hits=2 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="non-enemy-hp-change.log",
        first_hit_hint="10-00-00-000",
        last_hit_hint="10-00-01-000",
    )

    assert parsed["battle"]["total_damage"] == 300
    assert [entry["character_key"] for entry in parsed["participants"]] == ["chr_0027_tangtang"]
    damage_events = [event for event in parsed["timeline_events"] if event["lane_type"] == "skill"]
    assert len(damage_events) == 2
    assert {event["target_character_key"] for event in damage_events} == {"eny_0051_rodin"}


def test_parse_raw_battle_log_text_merges_combo_skill_family_into_single_timeline_group() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] HP_V2 #1 hit=1670 cum=1670 raw=1669.94 pHP=6085 eHP=2259523 skill="chr_0028_wulfa_combo_2_skill" hits=1 src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:00.376] HP_V2 #2 hit=2505 cum=4175 raw=2504.91 pHP=6085 eHP=2257019 skill="chr_0028_wulfa_combo_2_skill" hits=2 src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa seg=0 shared=3 critFlag=1 critDmg=0.5000',
            '[10:00:00.676] HP_V2 #3 hit=756 cum=756 raw=755.56 pHP=6085 eHP=2251516 skill="buff_chr_0028_wulfa_combo_2_damage" hits=1 src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa seg=0 shared=3 critFlag=1 critDmg=0.5000',
            '[10:00:00.801] HP_V2 #4 hit=796 cum=1552 raw=796.38 pHP=6085 eHP=2250719 skill="buff_chr_0028_wulfa_combo_2_damage" hits=2 src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa seg=0 shared=3 critFlag=1 critDmg=0.5000',
            '[10:00:00.929] HP_V2 #5 hit=838 cum=2390 raw=838.14 pHP=6085 eHP=2237146 skill="buff_chr_0028_wulfa_combo_2_damage" hits=3 src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa seg=0 shared=3 critFlag=1 critDmg=0.5000',
            '[10:00:01.430] HP_V2 #6 hit=19102 cum=31517 raw=19101.81 pHP=6085 eHP=2208019 skill="chr_0028_wulfa_combo_3_skill" hits=5 src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa seg=0 shared=2 critFlag=0 critDmg=1.0000',
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="wulfa-combo-family.log",
        first_hit_hint="10-00-00-000",
        last_hit_hint="10-00-02-000",
    )

    combo_events = [event for event in parsed["timeline_events"] if event["lane_type"] == "skill"]
    assert {event["event_name"] for event in combo_events} == {"燎影时刻"}
    assert {event["event_key"] for event in combo_events} == {"chr_0028_wulfa_combo_2_skill"}
    assert len({event["event_group_key"] for event in combo_events}) == 1


def test_parse_raw_battle_log_text_merges_ultimate_multiphase_hits_into_single_cast() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=5000 eHP=900000 skill="chr_0017_yvonne_ult_attack1_projhit" hits=1 src=chr_0017_yvonne tgt=eny_0051_rodin atk=chr_0017_yvonne seg=0 shared=3 critFlag=1 critDmg=0.5000',
            '[10:00:00.140] HP_V2 #2 hit=200 cum=300 raw=200.00 pHP=5000 eHP=899800 skill="chr_0017_yvonne_ult_attack2_projhit" hits=1 src=chr_0017_yvonne tgt=eny_0051_rodin atk=chr_0017_yvonne seg=0 shared=3 critFlag=1 critDmg=0.5000',
            '[10:00:03.900] HP_V2 #3 hit=300 cum=600 raw=300.00 pHP=5000 eHP=899500 skill="chr_0017_yvonne_ult_attack1_projhit" hits=1 src=chr_0017_yvonne tgt=eny_0051_rodin atk=chr_0017_yvonne seg=0 shared=3 critFlag=1 critDmg=0.5000',
            '[10:00:06.000] HP_V2 #4 hit=400 cum=1000 raw=400.00 pHP=5000 eHP=899100 skill="chr_0017_yvonne_ult_attack_end" hits=1 src=chr_0017_yvonne tgt=eny_0051_rodin atk=chr_0017_yvonne seg=0 shared=3 critFlag=1 critDmg=0.5000',
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="yvonne-ultimate-family.log",
        first_hit_hint="10-00-00-000",
        last_hit_hint="10-00-06-000",
    )

    damage_events = [event for event in parsed["timeline_events"] if event["lane_type"] == "skill"]
    assert {event["event_key"] for event in damage_events} == {"chr_0017_yvonne_ultimate_skill"}
    assert {event["event_group_key"] for event in damage_events} == {
        "chr_0017_yvonne_ultimate_skill::eny_0051_rodin::1"
    }
    assert {event["event_key"] for event in damage_events} == {"chr_0017_yvonne_ultimate_skill"}
    assert len({event["event_group_key"] for event in damage_events}) == 1

    skill_stats = parsed["role_skill_stats"]
    yvonne_ultimate = next(
        item
        for item in skill_stats
        if item["character_key"] == "chr_0017_yvonne" and item["skill_key"] == "chr_0017_yvonne_ultimate_skill"
    )
    assert yvonne_ultimate["cast_count"] == 1
    assert yvonne_ultimate["total_damage"] == 1000
    assert yvonne_ultimate["avg_damage"] == 1000
    assert yvonne_ultimate["max_damage"] == 1000


def test_parse_raw_battle_log_text_allocates_external_rdps_to_support() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] ATTR_MOD buff="buff_pelica_team_atk" i=0 attrType=2 modType=0 formula=6 useKey=1 val=0.5000 bbKey="atk_up"',
            '[10:00:00.000] BUFF_START #1 id="buff_pelica_team_atk" uid=1 owner=chr_0027_tangtang src=chr_0004_pelica dur=2.00 lifeT=2.00 passed=0.00 enh=1',
            '[10:00:00.000]   BB[2]: atk_up=0.5 =0',
            '[10:00:00.500] HP_V2 #2 hit=150 cum=150 raw=150.00 pHP=5000 eHP=900000 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="support.log",
        first_hit_hint="10-00-00-000",
        last_hit_hint="10-00-01-000",
    )

    participants = {entry["character_key"]: entry for entry in parsed["participants"]}
    assert set(participants) == {"chr_0027_tangtang"}
    assert participants["chr_0027_tangtang"]["dps"] == 150.0
    assert participants["chr_0027_tangtang"]["rdps"] == 150.0
    assert "chr_0004_pelica" not in participants

    damage_events = [event for event in parsed["timeline_events"] if event["lane_type"] == "skill"]
    assert damage_events[0]["rdps_contributions"] == [{"character_key": "chr_0027_tangtang", "character_name": "汤汤", "value": 150.0}]


def test_parse_raw_battle_log_text_allocates_endmin_essence_collapse_attack_share() -> None:
    content = "\n".join(
        [
            "[09:59:59.000] LOADOUT reason=SC_SELF_SCENE_INFO",
            "[09:59:59.000] LOADOUT slot=1 char=chr_0003_endminf weaponTemplate=wpn_sword_0021 weaponLv=0 refine=1 break=0 equips={} equipSuit={}",
            "[09:59:59.000] LOADOUT slot=2 char=chr_0028_wulfa weaponTemplate=wpn_sword_0016 weaponLv=0 refine=1 break=0 equips={} equipSuit={}",
            '[10:00:00.000] ATTR_MOD buff="buff_chr_0003_endminf_talent_1_tirgger" i=0 attrType=2 modType=0 formula=6 useKey=1 val=0.1500 bbKey="atk_up"',
            '[10:00:00.100] BUFF_START #1 id="buff_chr_0003_endminf_talent_1_tirgger" uid=adm1 owner=chr_0028_wulfa src=chr_0003_endminf dur=15.00 lifeT=15.00 passed=0.00 enh=1',
            "[10:00:00.100]   BB[1]: atk_up=0.15 duration=15",
            '[10:00:01.000] HP_V2 #2 hit=115 cum=115 raw=115.00 pHP=5000 eHP=899885 skill="chr_0028_wulfa_attack1" hits=1 src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="endmin-essence-collapse-share.log",
        first_hit_hint="10-00-00-000",
        last_hit_hint="10-00-02-000",
    )

    assert parsed["rdps_preflight"]["ok"] is True
    assert parsed["rdps_preflight"]["accepted_effect_buff_count"] == 1

    participants = {entry["character_key"]: entry for entry in parsed["participants"]}
    assert participants["chr_0028_wulfa"]["total_rd"] == 100.00000000000001
    assert round(participants["chr_0003_endminf"]["total_rd"], 6) == 15.0

    damage_events = [event for event in parsed["timeline_events"] if event["lane_type"] == "skill"]
    assert damage_events[0]["rdps_contributions"] == [
        {
            "character_key": "chr_0003_endminf",
            "character_name": "管理员",
            "value": 15.0,
        },
        {
            "character_key": "chr_0028_wulfa",
            "character_name": "洛茜",
            "value": 100.0,
        },
    ]


def test_parse_raw_battle_log_text_allocates_endmin_essence_collapse_parent_attack_share() -> None:
    content = "\n".join(
        [
            "[09:59:59.000] LOADOUT reason=SC_SELF_SCENE_INFO",
            "[09:59:59.000] LOADOUT slot=1 char=chr_0003_endminf weaponTemplate=wpn_sword_0021 weaponLv=0 refine=1 break=0 equips={} equipSuit={}",
            "[09:59:59.000] LOADOUT slot=2 char=chr_0028_wulfa weaponTemplate=wpn_sword_0016 weaponLv=0 refine=1 break=0 equips={} equipSuit={}",
            '[10:00:00.100] BUFF_START #1 id="buff_chr_0003_endminf_talent_1" uid=1 owner=chr_0028_wulfa src=chr_0003_endminf dur=15.00 lifeT=15.00 passed=0.00 enh=1',
            "[10:00:00.100]   BB[1]: atk_up=0.15 duration=15",
            '[10:00:01.000] HP_V2 #2 hit=115 cum=115 raw=115.00 pHP=5000 eHP=899885 skill="chr_0028_wulfa_attack1" hits=1 src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="endmin-essence-collapse-parent-share.log",
        first_hit_hint="10-00-00-000",
        last_hit_hint="10-00-02-000",
    )

    assert parsed["rdps_preflight"]["ok"] is True
    assert parsed["rdps_preflight"]["accepted_effect_buff_count"] == 1

    participants = {entry["character_key"]: entry for entry in parsed["participants"]}
    assert participants["chr_0028_wulfa"]["total_rd"] == 100.00000000000001
    assert round(participants["chr_0003_endminf"]["total_rd"], 6) == 15.0

    damage_events = [event for event in parsed["timeline_events"] if event["lane_type"] == "skill"]
    assert damage_events[0]["rdps_contributions"] == [
        {
            "character_key": "chr_0003_endminf",
            "character_name": "管理员",
            "value": 15.0,
        },
        {
            "character_key": "chr_0028_wulfa",
            "character_name": "洛茜",
            "value": 100.0,
        },
    ]


def test_parse_raw_battle_log_text_allocates_endmin_reality_stasis_only_on_originum_frozen_physical_hits() -> None:
    content = "\n".join(
        [
            "[09:59:59.000] LOADOUT reason=SC_SELF_SCENE_INFO",
            "[09:59:59.000] LOADOUT slot=1 char=chr_0003_endminf weaponTemplate=wpn_sword_0021 weaponLv=0 refine=1 break=0 equips={} equipSuit={}",
            "[09:59:59.000] LOADOUT slot=2 char=chr_0029_pograni weaponTemplate=wpn_sword_0016 weaponLv=0 refine=1 break=0 equips={} equipSuit={}",
            '[10:00:00.000] BUFF_START #1 id="991" uid=1 owner=chr_0003_endminf src=chr_0003_endminf dur=9999.00 lifeT=9999.00 passed=0.00 enh=1',
            "[10:00:00.000]   BB[1]: dmg=0.2",
            '[10:00:00.000] BUFF_START #2 id="20" uid=2 owner=chr_0029_pograni src=chr_0003_endminf dur=9999.00 lifeT=9999.00 passed=0.00 enh=1',
            "[10:00:00.000]   BB[2]: dmg=0.2",
            '[10:00:00.000] BUFF_START #3 id="340" uid=3 owner=eny_0051_rodin src=chr_0003_endminf dur=10.00 lifeT=10.00 passed=0.00 enh=1',
            '[10:00:01.000] HP_V2 #4 hit=120 cum=120 raw=120.00 pHP=5000 eHP=899880 skill="chr_0029_pograni_attack1" hits=1 src=chr_0029_pograni tgt=eny_0051_rodin atk=chr_0029_pograni seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="endmin-reality-stasis.log",
        first_hit_hint="10-00-00-000",
        last_hit_hint="10-00-02-000",
    )

    assert parsed["rdps_preflight"]["ok"] is True
    assert parsed["rdps_preflight"]["accepted_effect_buff_count"] == 1
    assert parsed["rdps_preflight"]["accepted_non_rdps_buff_count"] == 1

    participants = {entry["character_key"]: entry for entry in parsed["participants"]}
    assert participants["chr_0029_pograni"]["total_rd"] == 100.0
    assert participants["chr_0003_endminf"]["total_rd"] == 20.0

    buff_event = next(
        event
        for event in parsed["buff_events"]
        if event["event_key"] == "buff_chr_0003_endminf_talent_0"
    )
    assert buff_event["zone_effects"] == [
        {
            "zone": "vuln_taken",
            "element": "physical",
            "rate": 0.2,
            "condition": {
                "type": "all",
                "source": "CheckBuffStackNumAdvanced+CheckDamageType",
                "conditions": [
                    {"type": "damage_type_in", "source": "CheckDamageType", "elements": ["physical"]},
                    {
                        "type": "target_has_buff",
                        "source": "CheckBuffStackNumAdvanced",
                        "buff_ids": ["buff_common_originum_frozen"],
                    },
                ],
            },
            "semantic_source": "damageEffects",
            "bb_key": "dmg",
        }
    ]

    no_state_content = content.replace(
        '[10:00:00.000] BUFF_START #3 id="340" uid=3 owner=eny_0051_rodin src=chr_0003_endminf dur=10.00 lifeT=10.00 passed=0.00 enh=1\n',
        "",
    )
    no_state_parsed = parse_raw_battle_log_text(
        no_state_content,
        file_name="endmin-reality-stasis-no-state.log",
        first_hit_hint="10-00-00-000",
        last_hit_hint="10-00-02-000",
    )
    no_state_participants = {entry["character_key"]: entry for entry in no_state_parsed["participants"]}
    assert no_state_participants["chr_0029_pograni"]["total_rd"] == 120.0
    assert "chr_0003_endminf" not in no_state_participants

def test_parse_raw_battle_log_text_reports_packet_grounded_rdps_basis() -> None:
    content = "\n".join(
        [
            "[10:00:00.000] GAME_TIMER_START seq=1 source=PacketBattleState startMs=0 expireMs=0 official=1",
            '[10:00:00.000] ATTR_MOD buff="buff_pelica_team_atk" i=0 attrType=2 modType=0 formula=6 useKey=1 val=0.2500 bbKey="atk_up"',
            '[10:00:00.000] BUFF_START #1 id="buff_pelica_team_atk" uid=1 owner=chr_0027_tangtang src=chr_0004_pelica dur=3.00 lifeT=3.00 passed=0.00 enh=1',
            '[10:00:00.000]   BB[1]: atk_up=0.25',
            '[10:00:00.500] HP_V2 #2 hit=100 cum=100 raw=99.50 packetFinalValue=99.5001 pHP=5000 eHP=900000 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:03.000] GAME_TIMER_END seq=1 source=PacketBattleState elapsedMs=3000 startMs=0 endMs=3000 expireMs=0 sane=1 official=1",
        ]
    )

    parsed = parse_raw_battle_log_text(content, file_name="packet-grounded-rdps.log")

    basis = parsed["rdps_damage_basis"]
    assert basis["mode"] == "packet_hp_loss"
    assert basis["hit_count"] == 1
    assert basis["packet_grounded_hit_count"] == 1
    assert basis["packet_final_value_count"] == 1
    assert basis["formula_grounded_hit_count"] == 0
    assert basis["rdps_conservation_ok"] is True
    assert basis["total_damage"] == 100
    assert basis["total_packet_final_value"] == 99.5001

    participants = {entry["character_key"]: entry for entry in parsed["participants"]}
    assert participants["chr_0027_tangtang"]["total_rd"] == 100.0
    assert "chr_0004_pelica" not in participants
    assert rdps_totals_from_raw_report(parsed) == {
        "chr_0027_tangtang": 100.0,
    }

    damage_event = next(event for event in parsed["timeline_events"] if event["lane_type"] == "skill")
    assert damage_event["value"] == 100
    assert damage_event["value_source"] == "packet_hit"
    assert damage_event["packet_hit_value"] == 100
    assert damage_event["packet_raw_value"] == 99.5
    assert damage_event["packet_final_value"] == 99.5001
    assert damage_event["rdps_basis_value"] == 100


def test_parse_raw_battle_log_text_includes_static_self_multipliers_in_rdps_baseline() -> None:
    content = "\n".join(
        [
            "[10:00:00.000] LOADOUT slot=0 char=chr_0016_laevat weaponTemplate=wpn_sword_0006 weaponLv=0 refine=0 break=0 equips={[0]=item_equip_t4_suit_fire_natr01_hand_02|stats=sub3:灼热和自然伤害提升=0.249167@3} equipSuit={}",
            "[10:00:00.001] LOADOUT_STATS slot=0 char=chr_0016_laevat weaponTemplate=wpn_sword_0006 weaponSourceSkills={2237:level=4:potentialLv=0:bb={fire_dmg_up=0.256,normal_atk_up=1.2,duration=20}}",
            '[10:00:00.500] HP_V2 #1 hit=1000 cum=1000 raw=1000.00 pHP=5000 eHP=900000 skill="chr_0016_laevat_attack1" hits=1 src=chr_0016_laevat tgt=eny_0051_rodin atk=chr_0016_laevat seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="static-self-rdps.log",
        first_hit_hint="10-00-00-500",
        last_hit_hint="10-00-00-500",
        include_rdps_debug=True,
    )

    hit = parsed["debug_hits"][0]
    dmg_inc = next(zone for zone in hit["zones"] if zone["zone"] == "dmg_inc")
    assert dmg_inc["self_rate"] == 0.5052
    contributor_labels = {item["event_name"] for item in dmg_inc["contributors"]}
    assert any("动火用手甲" in label for label in contributor_labels)
    assert any("fire_dmg_up" in label for label in contributor_labels)
    assert all("normal_atk_up" not in label for label in contributor_labels)


def test_parse_raw_battle_log_text_uses_baseline_self_side_when_allocating_rdps() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] BUFF_START #1 id="buff_spell_up_demo" uid=1 owner=chr_0027_tangtang src=chr_0004_pelica dur=2.00 lifeT=2.00 passed=0.00 enh=1',
            "[10:00:00.000]   BB[1]: dmg_up=0.25",
            '[10:00:00.500] HP_V2 #2 hit=150 cum=150 raw=150.00 pHP=5000 eHP=900000 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:00.500] BASELINE #2 17=0.2500",
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="baseline-self-side-rdps.log",
        first_hit_hint="10-00-00-000",
        last_hit_hint="10-00-01-000",
    )

    participants = {entry["character_key"]: entry for entry in parsed["participants"]}
    assert participants["chr_0027_tangtang"]["rdps"] == 150.0
    assert "chr_0004_pelica" not in participants

    damage_events = [event for event in parsed["timeline_events"] if event["lane_type"] == "skill"]
    assert damage_events[0]["rdps_contributions"] == [{"character_key": "chr_0027_tangtang", "character_name": "汤汤", "value": 150.0}]


def test_parse_raw_battle_log_text_calibrates_external_rdps_with_dpd_bucket() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] BUFF_START #1 id="buff_spell_up_demo" uid=1 owner=chr_0027_tangtang src=chr_0004_pelica dur=2.00 lifeT=2.00 passed=0.00 enh=1',
            "[10:00:00.000]   BB[1]: dmg_up=0.25",
            '[10:00:00.500] HP_V2 #2 hit=150 cum=150 raw=150.00 pHP=5000 eHP=900000 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:00.500] DPD_RAW #2 probe=2 calc=150.0000 atkScale=1.0000 blocked=0 damageType=0x0 decorateMask=0x80 collider="BattleShape" atkZones=[1.0000,1.5000,1.0000,1.0000,1.0000,1.0000] defZones=[1.0000,1.0000,1.0000,1.0000,1.0000,1.0000]',
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="dpd-calibrated-rdps.log",
        first_hit_hint="10-00-00-000",
        last_hit_hint="10-00-01-000",
    )

    participants = {entry["character_key"]: entry for entry in parsed["participants"]}
    assert participants["chr_0027_tangtang"]["rdps"] == 150.0
    assert "chr_0004_pelica" not in participants

    damage_events = [event for event in parsed["timeline_events"] if event["lane_type"] == "skill"]
    assert damage_events[0]["rdps_contributions"] == [{"character_key": "chr_0027_tangtang", "character_name": "汤汤", "value": 150.0}]


def test_parse_raw_battle_log_text_rejects_unlisted_weapon_stack_credit() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] ATTR_MOD buff="buff_wpn_sword_0018_atk_up" i=0 attrType=2 modType=0 formula=6 useKey=1 val=0.0000 bbKey="atk_up"',
            '[10:00:00.000] BUFF_START #1 id="buff_wpn_sword_0018_atk_up" uid=1 owner=chr_0016_laevat src=chr_0019_karin ownerId=1001 srcId=2001 dur=20.00 lifeT=20.00 passed=0.00 enh=1',
            "[10:00:00.000]   BB[4]: atk_up=0.09 duration=20 max_stack=4",
            '[10:00:00.001] BUFF_START #101 id="buff_wpn_sword_0018_atk_up" uid=101 owner=chr_0019_karin src=chr_0019_karin ownerId=2001 srcId=2001 dur=20.00 lifeT=20.00 passed=0.00 enh=1',
            '[10:00:00.001] BB[101]: atk_up=0.09 duration=20 max_stack=4',
            '[10:00:00.100] ATTR_MOD buff="buff_wpn_sword_0018_atk_up" i=0 attrType=2 modType=0 formula=6 useKey=1 val=0.0000 bbKey="atk_up"',
            '[10:00:00.100] BUFF_START #2 id="buff_wpn_sword_0018_atk_up" uid=2 owner=chr_0016_laevat src=chr_0019_karin ownerId=1001 srcId=2001 dur=20.00 lifeT=20.00 passed=0.00 enh=1',
            "[10:00:00.100]   BB[4]: atk_up=0.09 duration=20 max_stack=4",
            '[10:00:00.101] BUFF_START #102 id="buff_wpn_sword_0018_atk_up" uid=102 owner=chr_0019_karin src=chr_0019_karin ownerId=2001 srcId=2001 dur=20.00 lifeT=20.00 passed=0.00 enh=1',
            '[10:00:00.101] BB[102]: atk_up=0.09 duration=20 max_stack=4',
            '[10:00:00.200] ATTR_MOD buff="buff_wpn_sword_0018_atk_up" i=0 attrType=2 modType=0 formula=6 useKey=1 val=0.0000 bbKey="atk_up"',
            '[10:00:00.200] BUFF_START #3 id="buff_wpn_sword_0018_atk_up" uid=3 owner=chr_0016_laevat src=chr_0019_karin ownerId=1001 srcId=2001 dur=20.00 lifeT=20.00 passed=0.00 enh=1',
            "[10:00:00.200]   BB[4]: atk_up=0.09 duration=20 max_stack=4",
            '[10:00:00.201] BUFF_START #103 id="buff_wpn_sword_0018_atk_up" uid=103 owner=chr_0019_karin src=chr_0019_karin ownerId=2001 srcId=2001 dur=20.00 lifeT=20.00 passed=0.00 enh=1',
            '[10:00:00.201] BB[103]: atk_up=0.09 duration=20 max_stack=4',
            '[10:00:00.300] ATTR_MOD buff="buff_wpn_sword_0018_atk_up" i=0 attrType=2 modType=0 formula=6 useKey=1 val=0.0000 bbKey="atk_up"',
            '[10:00:00.300] BUFF_START #4 id="buff_wpn_sword_0018_atk_up" uid=4 owner=chr_0016_laevat src=chr_0019_karin ownerId=1001 srcId=2001 dur=20.00 lifeT=20.00 passed=0.00 enh=1',
            "[10:00:00.300]   BB[4]: atk_up=0.09 duration=20 max_stack=4",
            '[10:00:00.301] BUFF_START #104 id="buff_wpn_sword_0018_atk_up" uid=104 owner=chr_0019_karin src=chr_0019_karin ownerId=2001 srcId=2001 dur=20.00 lifeT=20.00 passed=0.00 enh=1',
            '[10:00:00.301] BB[104]: atk_up=0.09 duration=20 max_stack=4',
            '[10:00:00.350] BUFF_ENABLE {"schemaVersion":1,"buffUid":"1","ownerId":"1001","enabled":true,"operationSeq":1}',
            '[10:00:00.350] BUFF_ENABLE {"schemaVersion":1,"buffUid":"2","ownerId":"1001","enabled":true,"operationSeq":2}',
            '[10:00:00.350] BUFF_ENABLE {"schemaVersion":1,"buffUid":"3","ownerId":"1001","enabled":true,"operationSeq":3}',
            '[10:00:00.350] BUFF_ENABLE {"schemaVersion":1,"buffUid":"4","ownerId":"1001","enabled":true,"operationSeq":4}',
            '[10:00:00.350] BUFF_ENABLE {"schemaVersion":1,"buffUid":"101","ownerId":"2001","enabled":true,"operationSeq":5}',
            '[10:00:00.350] BUFF_ENABLE {"schemaVersion":1,"buffUid":"102","ownerId":"2001","enabled":true,"operationSeq":6}',
            '[10:00:00.350] BUFF_ENABLE {"schemaVersion":1,"buffUid":"103","ownerId":"2001","enabled":true,"operationSeq":7}',
            '[10:00:00.350] BUFF_ENABLE {"schemaVersion":1,"buffUid":"104","ownerId":"2001","enabled":true,"operationSeq":8}',
            '[10:00:00.400] HP_V2 #10 hit=1180 cum=1180 raw=1180.00 pHP=100 eHP=10000 skill="chr_0016_laevat_attack1" hits=1 src=chr_0016_laevat tgt=eny_0051_rodin atk=chr_0016_laevat atkId=1001 tgtId=3001 seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="weapon-stack-cap-rdps.log",
        first_hit_hint="10-00-00-000",
        last_hit_hint="10-00-01-000",
    )

    participants = {entry["character_key"]: entry for entry in parsed["participants"]}
    # This weapon effect is absent from the active whitelist. A decoded
    # attribute and repeated instances cannot authorize external credit.
    assert participants["chr_0016_laevat"]["rdps"] == 1180
    assert participants.get("chr_0019_karin", {}).get("rdps", 0) == 0
    assert parsed['rdps_damage_basis']['rdps_strict_ok'] is False
    # This synthetic observation declares a complete four-stack cap; only
    # whitelist rejection should block credit, not missing stack evidence.
    assert not any(row['reason'] == 'unresolved_stack_evidence'
                   for row in parsed['rdps_preflight']['blockers'])
    assert any(row['reason'] == 'external_effect_not_whitelisted'
               for row in parsed['rdps_preflight']['blockers'])
    assert parsed["rdps_damage_basis"]["rdps_strict_ok"] is False


def test_parse_raw_battle_log_text_does_not_map_enemy_phase_alias_to_dungeon() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=5000 eHP=900000 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0079_nefarp2 atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="nef-phase-alias.log",
        first_hit_hint="10-00-00-000",
        last_hit_hint="10-00-01-000",
    )

    assert parsed["battle"]["boss_key"] == "eny_0079_nefarp2"
    assert parsed["battle"]["boss_name"] == "聂菲斯，“征服者”"
    assert parsed["battle"]["dungeon_key"] == "unknown_dungeon"
    assert parsed["battle"]["dungeon_name"] == "未知副本"
    assert parsed["battle"]["dungeon_context_id"] is None
    assert parsed["battle"]["dungeon_identity_source"] == "missing_dungeon_context"


def test_parse_raw_battle_log_text_maps_cryst_amp_attr_type_to_support_rdps() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] ATTR_MOD buff="buff_common_affixes_enhance_crystal" i=0 attrType=67 modType=0 formula=5 useKey=1 val=0.0000 bbKey="rate"',
            '[10:00:00.000] BUFF_START #1 id="buff_common_affixes_enhance_crystal" uid=1 owner=chr_0026_lastrite src=chr_0011_seraph dur=2.00 lifeT=2.00 passed=0.00 enh=1',
            '[10:00:00.000]   BB[2]: rate=0.5 =0 duration=2',
            '[10:00:00.500] HP_V2 #2 hit=150 cum=150 raw=150.00 pHP=5000 eHP=900000 skill="buff_chr_0026_lastrite_normal_skill_phantom_main" hits=1 src=chr_0026_lastrite tgt=eny_0051_rodin atk=chr_0026_lastrite seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="cryst-amp.log",
        first_hit_hint="10-00-00-000",
        last_hit_hint="10-00-01-000",
    )

    participants = {entry["character_key"]: entry for entry in parsed["participants"]}
    assert participants["chr_0026_lastrite"]["dps"] == 150.0
    assert participants["chr_0026_lastrite"]["rdps"] == 150.0
    assert "chr_0011_seraph" not in participants


def test_parse_raw_battle_log_text_respects_physical_vulnerable_element_filter() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] ATTR_MOD buff="buff_common_affixes_vulnerable_physical" i=0 attrType=70 modType=0 formula=5 useKey=1 val=0.0000 bbKey="rate"',
            '[10:00:00.000] BUFF_START #1 id="buff_common_affixes_vulnerable_physical" uid=1 owner=eny_0051_rodin src=chr_0025_ardelia dur=2.00 lifeT=2.00 passed=0.00 enh=1',
            '[10:00:00.000]   BB[2]: rate=0.5 =0 duration=2',
            '[10:00:00.500] HP_V2 #2 hit=150 cum=150 raw=150.00 pHP=5000 eHP=900000 skill="chr_0019_karin_attack1" hits=1 src=chr_0019_karin tgt=eny_0051_rodin atk=chr_0019_karin seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:01.000] HP_V2 #3 hit=150 cum=150 raw=150.00 pHP=5000 eHP=899850 skill="chr_0017_yvonne_ult_attack1_projhit" hits=1 src=chr_0017_yvonne tgt=eny_0051_rodin atk=chr_0017_yvonne seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="physical-vulnerable.log",
        first_hit_hint="10-00-00-000",
        last_hit_hint="10-00-02-000",
    )

    participants = {entry["character_key"]: entry for entry in parsed["participants"]}
    assert participants["chr_0019_karin"]["dps"] == 75.0
    assert participants["chr_0019_karin"]["rdps"] == 50.0
    assert participants["chr_0017_yvonne"]["dps"] == 75.0
    assert participants["chr_0017_yvonne"]["rdps"] == 75.0
    assert participants["chr_0025_ardelia"]["dps"] == 0.0
    assert participants["chr_0025_ardelia"]["rdps"] == 25.0


def test_parse_raw_battle_log_text_uses_dmg_mod_enemy_cryst_up_for_rdps() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] DMG_MOD buff="buff_chr_0011_seraph_talent_1_crystup" d=0 p=0 enableSide=1 class="DamageScaleProcessor" side=1 zone="NormalCalcZone" useKey=1 val=0.0000 bbKey="cryst_up"',
            '[10:00:00.000] BUFF_START #1 id="buff_chr_0011_seraph_talent_1_crystup" uid=1 owner=eny_0051_rodin src=chr_0011_seraph dur=2.00 lifeT=2.00 passed=0.00 enh=1',
            '[10:00:00.000]   BB[2]: cryst_up=0.5 =0 duration=2',
            '[10:00:00.500] HP_V2 #2 hit=150 cum=150 raw=150.00 pHP=5000 eHP=900000 skill="buff_chr_0026_lastrite_normal_skill_phantom_main" hits=1 src=chr_0026_lastrite tgt=eny_0051_rodin atk=chr_0026_lastrite seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="enemy-cryst-up.log",
        first_hit_hint="10-00-00-000",
        last_hit_hint="10-00-01-000",
    )

    participants = {entry["character_key"]: entry for entry in parsed["participants"]}
    assert participants["chr_0026_lastrite"]["dps"] == 150.0
    assert participants["chr_0026_lastrite"]["rdps"] == 100.0
    assert participants["chr_0011_seraph"]["dps"] == 0.0
    assert participants["chr_0011_seraph"]["rdps"] == 50.0


def test_parse_raw_battle_log_text_uses_dmg_mod_spell_res_down_for_spell_only_hits() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] DMG_MOD buff="buff_pelica_spell_res_down" d=3 p=0 enableSide=1 class="DamageScaleProcessor" side=1 zone="NormalCalcZone" useKey=1 val=0.0000 bbKey="final_spell_resistance_decrease"',
            '[10:00:00.000] BUFF_START #1 id="buff_pelica_spell_res_down" uid=1 owner=eny_0051_rodin src=chr_0004_pelica dur=2.00 lifeT=2.00 passed=0.00 enh=1',
            '[10:00:00.000]   BB[1]: final_spell_resistance_decrease=0.5',
            '[10:00:00.500] HP_V2 #2 hit=150 cum=150 raw=150.00 pHP=5000 eHP=900000 skill="chr_0019_karin_attack1" hits=1 src=chr_0019_karin tgt=eny_0051_rodin atk=chr_0019_karin seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:01.000] HP_V2 #3 hit=150 cum=150 raw=150.00 pHP=5000 eHP=899850 skill="chr_0017_yvonne_ult_attack1_projhit" hits=1 src=chr_0017_yvonne tgt=eny_0051_rodin atk=chr_0017_yvonne seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="spell-res-down.log",
        first_hit_hint="10-00-00-000",
        last_hit_hint="10-00-02-000",
    )

    participants = {entry["character_key"]: entry for entry in parsed["participants"]}
    assert participants["chr_0019_karin"]["dps"] == 75.0
    assert participants["chr_0019_karin"]["rdps"] == 75.0
    assert participants["chr_0017_yvonne"]["dps"] == 75.0
    assert participants["chr_0017_yvonne"]["rdps"] == 50.0
    assert participants["chr_0004_pelica"]["dps"] == 0.0
    assert participants["chr_0004_pelica"]["rdps"] == 25.0


def test_parse_raw_battle_log_text_prefers_final_modifier_key_over_base_key() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] DMG_MOD buff="buff_common_pulse_pulse_conduct_triggered_do" d=0 p=0 enableSide=1 class="DamageScaleProcessor" side=1 zone="NormalCalcZone" useKey=1 val=0.0000 bbKey="final_spell_resistance_decrease"',
            '[10:00:00.000] BUFF_START #1 id="buff_common_pulse_pulse_conduct_triggered_do" uid=1 owner=eny_0051_rodin src=chr_0004_pelica dur=2.00 lifeT=2.00 passed=0.00 enh=1',
            "[10:00:00.000]   BB[3]: spell_resistance_decrease=0.1816 duration=2 final_spell_resistance_decrease=0.2415",
            '[10:00:00.500] HP_V2 #2 hit=124 cum=124 raw=124.15 pHP=5000 eHP=900000 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    labels = _collect_buff_labels(
        {
            "event_key": "buff_common_pulse_pulse_conduct_triggered_do",
            "target_enemy_key": "eny_0051_rodin",
            "source_character_key": "chr_0004_pelica",
            "bb_values": {
                "spell_resistance_decrease": 0.1816,
                "final_spell_resistance_decrease": 0.2415,
                "duration": 2.0,
            },
            "bb_keys": [
                "spell_resistance_decrease",
                "final_spell_resistance_decrease",
                "duration",
            ],
            "attr_mods": [],
            "attr_types": [],
        }
    )

    assert "导电" in labels


def test_parse_raw_battle_log_text_respects_spell_only_buff_filter() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] BUFF_START #1 id="buff_spell_vuln_demo" uid=1 owner=eny_0051_rodin src=chr_0004_pelica dur=2.00 lifeT=2.00 passed=0.00 enh=1',
            '[10:00:00.000]   BB[1]: spell_taken_up=0.5',
            '[10:00:00.500] HP_V2 #2 hit=150 cum=150 raw=150.00 pHP=5000 eHP=900000 skill="chr_0019_karin_attack1" hits=1 src=chr_0019_karin tgt=eny_0051_rodin atk=chr_0019_karin seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:01.000] HP_V2 #3 hit=150 cum=150 raw=150.00 pHP=5000 eHP=899850 skill="chr_0017_yvonne_ult_attack1_projhit" hits=1 src=chr_0017_yvonne tgt=eny_0051_rodin atk=chr_0017_yvonne seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="element-filter.log",
        first_hit_hint="10-00-00-000",
        last_hit_hint="10-00-02-000",
    )

    participants = {entry["character_key"]: entry for entry in parsed["participants"]}
    assert participants["chr_0019_karin"]["dps"] == 75.0
    assert participants["chr_0019_karin"]["rdps"] == 75.0
    assert participants["chr_0017_yvonne"]["dps"] == 75.0
    assert participants["chr_0017_yvonne"]["rdps"] == 50.0
    assert participants["chr_0004_pelica"]["dps"] == 0.0
    assert participants["chr_0004_pelica"]["rdps"] == 25.0


def test_parse_raw_battle_log_text_labels_frozen_status_even_without_rdps_effect() -> None:
    labels = _collect_buff_labels(
        {
            "event_key": "buff_common_enemy_spell_status_frozen",
            "target_enemy_key": "eny_0051_rodin",
            "source_character_key": "chr_0027_tangtang",
            "bb_values": {},
            "bb_keys": [],
            "attr_mods": [],
            "attr_types": [],
        }
    )

    assert labels == ["冻结"]


def test_collect_zone_effects_ignores_atk02_aura_detect_marker() -> None:
    record = {
        "event_key": "buff_equipsuit_atk_02_aruadetect",
        "source_character_key": "chr_0005_chen",
        "target_character_key": "chr_0003_endminf",
        "target_enemy_key": None,
        "bb_values": {"atk_up": 0.1, "dmg_up": 0.2, "max_stack": 3},
        "bb_keys": ["atk_up", "dmg_up", "max_stack"],
        "attr_mods": [],
        "attr_types": [],
    }

    assert _collect_buff_labels(record) == []
    assert _collect_zone_effects(record) == []


def test_collect_zone_effects_atk02_parent_keeps_only_wearer_attack() -> None:
    record = {
        "event_key": "buff_equipsuit_atk_02",
        "source_character_key": "chr_0005_chen",
        "target_character_key": "chr_0005_chen",
        "target_enemy_key": None,
        "bb_values": {"atk_up": 0.15, "dmg_up": 0.2, "max_stack": 3},
        "bb_keys": ["atk_up", "dmg_up", "max_stack"],
        "attr_mods": [],
        "attr_types": [],
    }

    assert _collect_buff_labels(record) == ["攻击提升"]
    assert _collect_zone_effects(record) == [{"zone": "atk", "element": "all", "rate": 0.15}]


def test_parse_raw_battle_log_text_does_not_credit_atk02_detect_marker_to_wearer() -> None:
    content = "\n".join(
        [
            "[09:59:59.000] LOADOUT slot=1 char=chr_0005_chen weaponTemplate=wpn_sword_0017 weaponLv=0 refine=3 break=0 equips={} equipSuit={[suit_atk02]=3}",
            "[09:59:59.000] LOADOUT slot=2 char=chr_0003_endminf weaponTemplate=wpn_sword_0021 weaponLv=0 refine=1 break=0 equips={} equipSuit={}",
            '[10:00:00.000] BUFF_START #1 id="1397" uid=1 owner=chr_0003_endminf src=chr_0005_chen dur=5.00 lifeT=5.00 passed=0.00 enh=1',
            "[10:00:00.000]   BB[1]: atk_up=0.1 dmg_up=0.2 max_stack=3",
            '[10:00:01.000] HP_V2 #2 hit=100 cum=100 raw=100.00 pHP=5000 eHP=899900 skill="chr_0003_endminf_attack1" hits=1 src=chr_0003_endminf tgt=eny_0051_rodin atk=chr_0003_endminf seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="atk02-detect-marker.log",
        first_hit_hint="10-00-00-000",
        last_hit_hint="10-00-02-000",
    )

    participants = {entry["character_key"]: entry for entry in parsed["participants"]}
    assert participants["chr_0003_endminf"]["total_rd"] == 100
    assert "chr_0005_chen" not in participants
    assert not [
        event
        for event in parsed["timeline_events"]
        if event["lane_type"] == "buff" and event["event_key"] == "buff_equipsuit_atk_02_aruadetect"
    ]


def test_parse_raw_battle_log_text_ignores_legacy_false_fracture_projection() -> None:
    content = "\n".join(
        [
            "[10:00:00.000] GAME_TIMER_START seq=1 source=ChallengeStart self=0000000000000000 msg=0000000000000000 startMs=0 expireMs=0 prepareSeconds=0",
            '[10:00:00.000] BUFF_START #1 id="933" uid=1 owner=eny_0051_rodin src=chr_0029_pograni dur=5.00 lifeT=5.00 passed=0.00 enh=1',
            "[10:00:00.000]   BB[1]: duration=5 physical_res_down=0.2",
            '[10:00:01.000] HP_V2 #2 hit=120 cum=120 raw=120.00 pHP=5000 eHP=899880 skill="chr_0003_endminf_attack1" hits=1 src=chr_0003_endminf tgt=eny_0051_rodin atk=chr_0003_endminf seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:02.000] GAME_TIMER_END seq=1 source=ChallengeComplete self=0000000000000000 msg=0000000000000000 elapsedMs=2000 startMs=0 expireMs=0 forceLeaveTs=0 sane=1",
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="pograni-fracture-buff-axis.log",
        first_hit_hint="10-00-00-000",
        last_hit_hint="10-00-02-000",
    )

    assert not [event for event in parsed["timeline_events"] if event["lane_type"] == "buff"]

    damage_event = next(event for event in parsed["timeline_events"] if event["lane_type"] == "skill")
    assert damage_event["rdps_contributions"] == [
        {"character_key": "chr_0003_endminf", "character_name": "管理员", "value": 120.0},
    ]


def test_parse_raw_battle_log_text_suppresses_legacy_false_fracture_for_all_elements() -> None:
    content = "\n".join(
        [
            "[10:00:00.000] GAME_TIMER_START seq=1 source=ChallengeStart self=0000000000000000 msg=0000000000000000 startMs=0 expireMs=0 prepareSeconds=0",
            '[10:00:00.000] BUFF_START #1 id="buff_common_enemy_spell_status_do_frozen" uid=99 owner=eny_0051_rodin src=chr_0029_pograni dur=24.00 lifeT=24.00 passed=0.00 enh=1',
            "[10:00:00.000]   BB[99]: duration=24 physical_res_down=0.2 count=3 atk_scale=5.36",
            '[10:00:01.000] HP_V2 #2 hit=120 cum=120 raw=120.00 packetFinalValue=120.0 pHP=5000 eHP=899880 skill="chr_0013_aglina_attack1_projhit" hits=1 src=chr_0013_aglina tgt=eny_0051_rodin atk=chr_0013_aglina seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:01.000] PKT_MOD #2 atk=[] def=[99]",
            '[10:00:01.500] HP_V2 #3 hit=120 cum=240 raw=120.00 packetFinalValue=120.0 pHP=5000 eHP=899760 skill="chr_0028_wulfa_normal_skill_projhit2" hits=1 src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:01.500] PKT_MOD #3 atk=[] def=[99]",
            "[10:00:02.000] GAME_TIMER_END seq=1 source=ChallengeComplete self=0000000000000000 msg=0000000000000000 elapsedMs=2000 startMs=0 expireMs=0 forceLeaveTs=0 sane=1",
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        include_rdps_debug=True,
        file_name="pograni-fracture-natural-physical-school.log",
        first_hit_hint="10-00-00-000",
        last_hit_hint="10-00-02-000",
    )

    damage_events = [event for event in parsed["timeline_events"] if event["lane_type"] == "skill"]
    natural_hit, fire_spell_hit = damage_events
    assert natural_hit["damage_element"] == "natural"
    assert natural_hit["damage_school"] == "physical"
    assert natural_hit["rdps_contributions"] == [
        {"character_key": "chr_0013_aglina", "character_name": "洁尔佩塔", "value": 120.0},
    ]
    assert fire_spell_hit["damage_element"] == "fire"
    assert fire_spell_hit["damage_school"] == "spell"
    assert fire_spell_hit["rdps_contributions"] == [
        {"character_key": "chr_0028_wulfa", "character_name": "洛茜", "value": 120.0},
    ]
    assert parsed["rdps_preflight"]["ok"] is True


def test_parse_raw_battle_log_text_suppresses_mislabeled_legacy_false_fracture() -> None:
    content = "\n".join(
        [
            "[10:00:00.000] DUNGEON_CONTEXT dungeonId=dung01_bossrush01_04 source=SC_SELF_SCENE_INFO",
            "[10:00:00.000] GAME_TIMER_START seq=1 source=PacketBattleState startMs=0 expireMs=0 official=1",
            "[10:00:00.000] SQUAD size=2 members=[chr_0028_wulfa_100514 chr_0029_pograni_100542]",
            '[10:00:01.000] BUFF_START #1 id="buff_common_enemy_spell_status_do_frozen" uid=99 owner=chr_0028_wulfa src=chr_0029_pograni dur=9999.00 lifeT=9999.00 passed=0.00 enh=1',
            "[10:00:01.000]   BB[99]: duration=24 physical_res_down=0.2 count=3 atk_scale=2",
            '[10:00:02.000] HP_V2 #2 hit=120 cum=120 raw=120.00 packetFinalValue=120.0 pHP=0 eHP=899880 skill="chr_0028_wulfa_attack1" hits=1 skillLv=1 templateIntId=1993 actionId=6 src=chr_0028_wulfa tgt=chr_0028_wulfa atk=chr_0028_wulfa atkId=100514 tgtId=100590 seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:02.000] PKT_MOD #2 atk=[] def=[99]",
            "[10:00:30.000] GAME_TIMER_END seq=3 source=ChallengeComplete self=0 msg=0 elapsedMs=30000 startMs=0 expireMs=0 forceLeaveTs=0 sane=1",
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="pograni-fracture-mislabeled-owner.log",
        first_hit_hint="10-00-00-000",
        last_hit_hint="10-00-30-000",
    )

    assert not [event for event in parsed["timeline_events"] if event["lane_type"] == "buff"]

    damage_event = next(event for event in parsed["timeline_events"] if event["lane_type"] == "skill")
    assert damage_event["target_character_key"] == "eny_0051_rodin"
    assert damage_event["rdps_contributions"] == [
        {"character_key": "chr_0028_wulfa", "character_name": "洛茜", "value": 120.0},
    ]


def test_parse_raw_battle_log_text_ignores_pograni_fracture_trigger_atk_scale() -> None:
    content = "\n".join(
        [
            "[10:00:00.000] DUNGEON_CONTEXT dungeonId=dung01_bossrush01_04 source=SC_SELF_SCENE_INFO",
            "[10:00:00.000] GAME_TIMER_START seq=1 source=PacketBattleState startMs=0 expireMs=0 official=1",
            "[10:00:00.000] SQUAD size=2 members=[chr_0028_wulfa_100514 chr_0029_pograni_100542]",
            '[10:00:01.000] BUFF_START #1 id="1211" uid=99 owner=chr_0028_wulfa src=chr_0029_pograni dur=9999.00 lifeT=9999.00 passed=0.00 enh=1',
            "[10:00:01.000]   BB[99]: duration=24 physical_res_down=0.2 count=3 atk_scale=2",
            '[10:00:02.000] HP_V2 #2 hit=120 cum=120 raw=120.00 packetFinalValue=120.0 pHP=0 eHP=899880 skill="chr_0028_wulfa_attack1" hits=1 skillLv=1 templateIntId=1993 actionId=6 src=chr_0028_wulfa tgt=chr_0028_wulfa atk=chr_0028_wulfa atkId=100514 tgtId=100590 seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:02.000] PKT_MOD #2 atk=[] def=[99]",
            "[10:00:30.000] GAME_TIMER_END seq=3 source=ChallengeComplete self=0 msg=0 elapsedMs=30000 startMs=0 expireMs=0 forceLeaveTs=0 sane=1",
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="pograni-fracture-trigger-atk-scale.log",
        first_hit_hint="10-00-00-000",
        last_hit_hint="10-00-30-000",
    )

    buff_event = next(
        event
        for event in parsed["timeline_events"]
        if event["lane_type"] == "buff" and event["source_character_key"] == "chr_0029_pograni"
    )
    assert buff_event["event_key"] == "buff_physical_do_fracture"
    assert buff_event["event_name"] == "碎甲"
    assert buff_event["effects"] == [
        {
            "zone": "vuln_taken",
            "element": "physical",
            "rate": 0.2,
            "condition": {
                "type": "damage_type_in",
                "source": "blackboard_key",
                "elements": ["physical"],
            },
        }
    ]

    damage_event = next(event for event in parsed["timeline_events"] if event["lane_type"] == "skill")
    assert damage_event["rdps_contributions"] == [
        {"character_key": "chr_0028_wulfa", "character_name": "洛茜", "value": 100.0},
        {"character_key": "chr_0029_pograni", "character_name": "骏卫", "value": 20.0},
    ]


def test_arts_strength_allocates_only_anomaly_damage() -> None:
    base = [
        "[10:00:00.000] DUNGEON_CONTEXT dungeonId=dung01_bossrush01_04 source=SC_SELF_SCENE_INFO",
        "[10:00:00.000] SQUAD size=2 members=[chr_0028_wulfa_1001 chr_0029_pograni_1002]",
        '[10:00:00.100] BUFF_START #1 id="buff_chr_0029_pograni_talent1" uid=1 owner=chr_0028_wulfa src=chr_0029_pograni dur=20.00 lifeT=20.00 passed=0.00 enh=1',
        "[10:00:00.100]   BB[1]: duration=20 physpell_up=30",
    ]
    anomaly = parse_raw_battle_log_text(
        "\n".join(
            base
            + [
                '[10:00:01.000] HP_V2 #2 hit=130 cum=130 raw=130.00 pHP=5000 eHP=899870 skill="buff_common_cryst_cryst_triggered" hits=1 src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa seg=0 shared=2 critFlag=0 critDmg=0.5000',
            ]
        ),
        file_name="arts-strength-anomaly.log",
        first_hit_hint="10-00-00-000",
        last_hit_hint="10-00-02-000",
    )
    normal = parse_raw_battle_log_text(
        "\n".join(
            base
            + [
                '[10:00:01.000] HP_V2 #2 hit=130 cum=130 raw=130.00 pHP=5000 eHP=899870 skill="chr_0028_wulfa_attack1" hits=1 src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa seg=0 shared=2 critFlag=0 critDmg=0.5000',
            ]
        ),
        file_name="arts-strength-normal.log",
        first_hit_hint="10-00-00-000",
        last_hit_hint="10-00-02-000",
    )

    anomaly_event = next(event for event in anomaly["timeline_events"] if event["lane_type"] == "skill")
    assert anomaly_event["rdps_contributions"] == [
        {"character_key": "chr_0028_wulfa", "character_name": "洛茜", "value": 100.0},
        {"character_key": "chr_0029_pograni", "character_name": "骏卫", "value": 30.0},
    ]
    normal_event = next(event for event in normal["timeline_events"] if event["lane_type"] == "skill")
    assert normal_event["rdps_contributions"] == [
        {"character_key": "chr_0028_wulfa", "character_name": "洛茜", "value": 130.0},
    ]


def test_arts_strength_uses_loadout_baseline_in_anomaly_denominator() -> None:
    content = "\n".join(
        [
            "[09:59:59.000] LOADOUT reason=SC_SELF_SCENE_INFO",
            "[09:59:59.000] LOADOUT slot=0 char=chr_0028_wulfa weaponTemplate=wpn_sword_0022 weaponLv=90 refine=1 break=4 equips={[0]=item_test|stats=main:防御力=1;sub3:源石技艺强度=100@3} equipSuit={}",
            "[09:59:59.000] LOADOUT slot=1 char=chr_0029_pograni weaponTemplate=wpn_sword_0008 weaponLv=90 refine=1 break=4 equips={} equipSuit={}",
            '[10:00:00.100] BUFF_START #1 id="buff_chr_0029_pograni_talent1" uid=1 owner=chr_0028_wulfa src=chr_0029_pograni dur=20.00 lifeT=20.00 passed=0.00 enh=1',
            "[10:00:00.100]   BB[1]: duration=20 physpell_up=30",
            '[10:00:01.000] HP_V2 #2 hit=115 cum=115 raw=115.00 pHP=5000 eHP=899885 skill="buff_common_cryst_cryst_triggered" hits=1 src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="arts-strength-static-baseline.log",
        first_hit_hint="10-00-00-000",
        last_hit_hint="10-00-02-000",
    )
    damage_event = next(event for event in parsed["timeline_events"] if event["lane_type"] == "skill")
    assert damage_event["rdps_contributions"] == [
        {"character_key": "chr_0028_wulfa", "character_name": "洛茜", "value": 100.0},
        {"character_key": "chr_0029_pograni", "character_name": "骏卫", "value": 15.0},
    ]


def test_arts_strength_reattributes_enhanced_fracture_effect() -> None:
    content = "\n".join(
        [
            "[10:00:00.000] DUNGEON_CONTEXT dungeonId=dung01_bossrush01_04 source=SC_SELF_SCENE_INFO",
            "[10:00:00.000] SQUAD size=3 members=[chr_0028_wulfa_1001 chr_0013_aglina_1002 chr_0029_pograni_1003]",
            '[10:00:00.100] BUFF_START #1 id="buff_chr_0029_pograni_talent1" uid=1 owner=chr_0013_aglina src=chr_0029_pograni dur=20.00 lifeT=20.00 passed=0.00 enh=1',
            "[10:00:00.100]   BB[1]: duration=20 physpell_up=30",
            '[10:00:01.000] BUFF_START #2 id="buff_physical_do_fracture" uid=2 owner=eny_0051_rodin src=chr_0013_aglina dur=20.00 lifeT=20.00 passed=0.00 enh=1',
            "[10:00:01.000]   BB[2]: duration=20 physical_res_down=0.36 atk_scale=2",
            '[10:00:02.000] HP_V2 #3 hit=136 cum=136 raw=136.00 packetFinalValue=136.0 pHP=5000 eHP=899864 skill="chr_0028_wulfa_attack1" hits=1 src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:02.000] PKT_MOD #3 atk=[] def=[2]",
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="arts-strength-fracture-provenance.log",
        first_hit_hint="10-00-00-000",
        last_hit_hint="10-00-03-000",
    )
    damage_event = next(event for event in parsed["timeline_events"] if event["lane_type"] == "skill")
    contributions = {row["character_key"]: row["value"] for row in damage_event["rdps_contributions"]}
    assert round(contributions["chr_0028_wulfa"], 4) == 100.0
    assert round(contributions["chr_0013_aglina"], 4) == 30.4615
    assert round(contributions["chr_0029_pograni"], 4) == 5.5385


def test_parse_raw_battle_log_text_handles_conduct_with_mislabeled_owner() -> None:
    content = "\n".join(
        [
            "[10:00:00.000] DUNGEON_CONTEXT dungeonId=dung01_bossrush01_04 source=SC_SELF_SCENE_INFO",
            "[10:00:00.000] GAME_TIMER_START seq=1 source=PacketBattleState startMs=0 expireMs=0 official=1",
            "[10:00:00.000] SQUAD size=2 members=[chr_0017_yvonne_1001 chr_0004_pelica_1002]",
            '[10:00:01.000] BUFF_START #1 id="2268" uid=1 owner=chr_0027_tangtang src=chr_0004_pelica dur=9999.00 lifeT=9999.00 passed=0.00 enh=1',
            "[10:00:01.000]   BB[1]: spell_resistance_decrease=0.2 duration=8.75 final_spell_resistance_decrease=0.25 count=1",
            '[10:00:02.000] HP_V2 #2 hit=125 cum=125 raw=125.00 packetFinalValue=125.0 pHP=0 eHP=899875 skill="chr_0017_yvonne_ult_attack1_projhit" hits=1 src=chr_0017_yvonne tgt=chr_0017_yvonne atk=chr_0017_yvonne atkId=1001 tgtId=2000 seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:02.000] PKT_MOD #2 atk=[] def=[1]",
            "[10:00:10.000] GAME_TIMER_END seq=3 source=ChallengeComplete self=0 msg=0 elapsedMs=10000 startMs=0 expireMs=0 forceLeaveTs=0 sane=1",
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="conduct-mislabeled-owner.log",
        first_hit_hint="10-00-00-000",
        last_hit_hint="10-00-10-000",
    )

    buff_event = next(
        event
        for event in parsed["timeline_events"]
        if event["lane_type"] == "buff" and "导电" in event["event_name"]
    )
    assert buff_event["event_key"] == "buff_common_pulse_pulse_conduct_triggered_do"
    assert buff_event["target_player_key"] is None
    assert buff_event["target_enemy_key"] == "eny_0051_rodin"
    assert buff_event["duration_ms"] == 8750
    assert buff_event["effects"] == [
        {"zone": "vuln_taken", "element": "spell", "rate": 0.25, "bb_key": "final_spell_resistance_decrease"}
    ]

    damage_event = next(event for event in parsed["timeline_events"] if event["lane_type"] == "skill")
    assert damage_event["rdps_contributions"] == [
        {"character_key": "chr_0004_pelica", "character_name": "佩丽卡", "value": 25.0},
        {"character_key": "chr_0017_yvonne", "character_name": "伊冯", "value": 100.0},
    ]


def test_parse_raw_battle_log_text_handles_corrosion_with_mislabeled_owner() -> None:
    content = "\n".join(
        [
            "[10:00:00.000] DUNGEON_CONTEXT dungeonId=dung01_bossrush01_04 source=SC_SELF_SCENE_INFO",
            "[10:00:00.000] GAME_TIMER_START seq=1 source=PacketBattleState startMs=0 expireMs=0 official=1",
            "[10:00:00.000] SQUAD size=2 members=[chr_0027_tangtang_1001 chr_0013_aglina_1002]",
            '[10:00:01.000] BUFF_START #1 id="327" uid=1 owner=chr_0027_tangtang src=chr_0013_aglina dur=9999.00 lifeT=9999.00 passed=0.00 enh=1',
            "[10:00:01.000]   BB[1]: def_decrease=0.1 max_def_decrease=0.1 duration=15 def_decrease_tick=0 start_def_decrease=0.1 count=1",
            '[10:00:02.000] HP_V2 #2 hit=110 cum=110 raw=110.00 packetFinalValue=110.0 pHP=0 eHP=899890 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=chr_0027_tangtang atk=chr_0027_tangtang atkId=1001 tgtId=2000 seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:20.000] GAME_TIMER_END seq=3 source=ChallengeComplete self=0 msg=0 elapsedMs=20000 startMs=0 expireMs=0 forceLeaveTs=0 sane=1",
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="corrosion-mislabeled-owner.log",
        first_hit_hint="10-00-00-000",
        last_hit_hint="10-00-20-000",
    )

    buff_event = next(
        event
        for event in parsed["timeline_events"]
        if event["lane_type"] == "buff" and "腐蚀" in event["event_name"]
    )
    assert buff_event["event_key"] == "buff_common_natural_cryst_triggered"
    assert buff_event["target_player_key"] is None
    assert buff_event["target_enemy_key"] == "eny_0051_rodin"
    assert buff_event["duration_ms"] == 15000
    assert buff_event["effects"] == [{"zone": "res", "element": "all", "rate": 0.1}]

    damage_event = next(event for event in parsed["timeline_events"] if event["lane_type"] == "skill")
    assert damage_event["rdps_contributions"] == [
        {"character_key": "chr_0013_aglina", "character_name": "洁尔佩塔", "value": 10.0},
        {"character_key": "chr_0027_tangtang", "character_name": "汤汤", "value": 100.0},
    ]


def test_parse_raw_battle_log_text_applies_hidden_karin_combo_rule() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] BUFF_START #1 id="buff_chr_0019_karin_talent_2_combo" uid=1 owner=chr_0027_tangtang src=chr_0019_karin dur=5.00 lifeT=5.00 passed=0.00 enh=1',
            '[10:00:00.500] HP_V2 #2 hit=130 cum=130 raw=130.00 pHP=5000 eHP=900000 skill="chr_0027_tangtang_normal_skill" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="karin-combo.log",
        first_hit_hint="10-00-00-000",
        last_hit_hint="10-00-01-000",
    )

    participants = {entry["character_key"]: entry for entry in parsed["participants"]}
    assert participants["chr_0027_tangtang"]["dps"] == 130.0
    assert participants["chr_0027_tangtang"]["rdps"] == 100.0
    assert participants["chr_0019_karin"]["dps"] == 0.0
    assert participants["chr_0019_karin"]["rdps"] == 30.0
    combo_event = next(event for event in parsed["timeline_events"] if event["event_name"] == "连击增伤")
    assert combo_event["target_player_key"] == "chr_0027_tangtang"
    assert combo_event["target_enemy_key"] is None


def test_parse_raw_battle_log_text_applies_generic_combo_consume_rule_to_normal_skill() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] BUFF_START #1 id="buff_common_affixes_combo_trigger" uid=1 owner=chr_0027_tangtang src=chr_0015_lifeng dur=2.00 lifeT=2.00 passed=0.00 enh=1',
            '[10:00:00.000]   BB[1]: imbue_scale=0.2',
            '[10:00:00.050] BUFF_START #2 id="buff_common_affixes_skillimbue" uid=2 owner=chr_0027_tangtang src=chr_0015_lifeng dur=2.00 lifeT=2.00 passed=0.00 enh=1',
            '[10:00:00.050]   BB[1]: imbue_scale=0.2',
            '[10:00:00.100] HP_V2 #2 hit=130 cum=130 raw=130.00 pHP=5000 eHP=900000 skill="chr_0027_tangtang_normal_skill" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="lifeng-combo.log",
        first_hit_hint="10-00-00-000",
        last_hit_hint="10-00-01-000",
    )

    participants = {entry["character_key"]: entry for entry in parsed["participants"]}
    assert participants["chr_0027_tangtang"]["dps"] == 130.0
    assert participants["chr_0027_tangtang"]["rdps"] == 100.0
    assert participants["chr_0015_lifeng"]["dps"] == 0.0
    assert participants["chr_0015_lifeng"]["rdps"] == 30.0
    combo_event = next(event for event in parsed["timeline_events"] if event["event_name"] == "连击增伤")
    assert combo_event["target_player_key"] == "chr_0027_tangtang"
    assert combo_event["target_enemy_key"] is None


def test_parse_raw_battle_log_text_applies_generic_combo_stack_curve_to_normal_skill() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] BUFF_START #1 id="buff_common_affixes_combo_trigger" uid=1 owner=chr_0027_tangtang src=chr_0019_karin dur=2.00 lifeT=2.00 passed=0.00 enh=1',
            '[10:00:00.000]   BB[1]: imbue_scale=0.2',
            '[10:00:00.010] BUFF_START #2 id="buff_common_affixes_combo_trigger" uid=2 owner=chr_0027_tangtang src=chr_0015_lifeng dur=2.00 lifeT=2.00 passed=0.00 enh=1',
            '[10:00:00.010]   BB[2]: imbue_scale=0.2',
            '[10:00:00.050] BUFF_START #3 id="buff_common_affixes_skillimbue" uid=3 owner=chr_0027_tangtang src=chr_0019_karin dur=2.00 lifeT=2.00 passed=0.00 enh=1',
            '[10:00:00.050] BUFF_START #4 id="buff_common_affixes_skillimbue" uid=4 owner=chr_0027_tangtang src=chr_0015_lifeng dur=2.00 lifeT=2.00 passed=0.00 enh=1',
            '[10:00:00.100] HP_V2 #5 hit=145 cum=145 raw=145.00 pHP=5000 eHP=900000 skill="chr_0027_tangtang_normal_skill" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="combo-stack-normal-skill.log",
        first_hit_hint="10-00-00-000",
        last_hit_hint="10-00-01-000",
    )

    participants = {entry["character_key"]: entry for entry in parsed["participants"]}
    assert participants["chr_0027_tangtang"]["dps"] == 145.0
    assert participants["chr_0027_tangtang"]["rdps"] == 100.0
    assert participants["chr_0019_karin"]["rdps"] == 30.0
    assert participants["chr_0015_lifeng"]["rdps"] == 15.0

    combo_events = [event for event in parsed["timeline_events"] if event["event_name"] == "连击增伤"]
    assert sorted(event["effects"][0]["rate"] for event in combo_events) == [0.15, 0.3]


def test_parse_raw_battle_log_text_applies_generic_combo_consume_rule_to_ultimate_family() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] BUFF_START #1 id="buff_common_affixes_combo_trigger" uid=1 owner=chr_0015_lifeng src=chr_0019_karin dur=2.00 lifeT=2.00 passed=0.00 enh=1',
            '[10:00:00.000]   BB[1]: imbue_scale=0.2',
            '[10:00:00.050] BUFF_START #2 id="buff_common_affixes_skillimbue" uid=2 owner=chr_0015_lifeng src=chr_0019_karin dur=2.00 lifeT=2.00 passed=0.00 enh=1',
            '[10:00:00.050]   BB[1]: imbue_scale=0.2',
            '[10:00:00.100] HP_V2 #2 hit=75 cum=75 raw=75.00 pHP=5000 eHP=900000 skill="sk_wpn_lance_0010" hits=1 src=chr_0015_lifeng tgt=eny_0051_rodin atk=chr_0015_lifeng seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:00.100] HP_V2 #3 hit=100 cum=175 raw=100.00 pHP=5000 eHP=899925 skill="chr_0015_lifeng_ultimate_skill_abentity" hits=2 src=chr_0015_lifeng tgt=eny_0051_rodin atk=chr_0015_lifeng seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="lifeng-ultimate-combo.log",
        first_hit_hint="10-00-00-000",
        last_hit_hint="10-00-01-000",
    )

    participants = {entry["character_key"]: entry for entry in parsed["participants"]}
    assert participants["chr_0015_lifeng"]["dps"] == 175.0
    assert participants["chr_0015_lifeng"]["rdps"] == 145.83
    assert participants["chr_0019_karin"]["dps"] == 0.0
    assert participants["chr_0019_karin"]["rdps"] == 29.17


def test_parse_raw_battle_log_text_consumes_replicated_combo_trigger_once_globally() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] BUFF_START #1 id="buff_common_affixes_combo_trigger" uid=1 owner=chr_0027_tangtang src=chr_0019_karin dur=2.00 lifeT=2.00 passed=0.00 enh=1',
            '[10:00:00.000]   BB[1]: imbue_scale=0.2',
            '[10:00:00.001] BUFF_START #2 id="buff_common_affixes_combo_trigger" uid=2 owner=chr_0006_wolfgd src=chr_0019_karin dur=2.00 lifeT=2.00 passed=0.00 enh=1',
            '[10:00:00.001]   BB[1]: imbue_scale=0.2',
            '[10:00:00.100] HP_V2 #3 hit=120 cum=120 raw=120.00 pHP=5000 eHP=900000 skill="chr_0006_wolfgd_normal_skill" hits=1 src=chr_0006_wolfgd tgt=eny_0051_rodin atk=chr_0006_wolfgd seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:00.150] BUFF_START #4 id="buff_common_affixes_skillimbue" uid=4 owner=chr_0027_tangtang src=chr_0019_karin dur=2.00 lifeT=2.00 passed=0.00 enh=1',
            '[10:00:00.150]   BB[1]: imbue_scale=0.2',
            '[10:00:00.200] HP_V2 #5 hit=130 cum=130 raw=130.00 pHP=5000 eHP=899880 skill="chr_0027_tangtang_normal_skill" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:00.300] HP_V2 #6 hit=120 cum=120 raw=120.00 pHP=5000 eHP=899750 skill="chr_0006_wolfgd_normal_skill" hits=1 src=chr_0006_wolfgd tgt=eny_0051_rodin atk=chr_0006_wolfgd seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="karin-combo-global-consume.log",
        first_hit_hint="10-00-00-000",
        last_hit_hint="10-00-01-000",
    )

    participants = {entry["character_key"]: entry for entry in parsed["participants"]}
    assert participants["chr_0027_tangtang"]["rdps"] == 100.0
    assert participants["chr_0006_wolfgd"]["rdps"] == 240.0
    assert participants["chr_0019_karin"]["rdps"] == 30.0


def test_parse_raw_battle_log_text_uses_owner_for_self_attached_weapon_buffs() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] ATTR_MOD buff="buff_wpn_funnel_0010_atk_up" i=0 attrType=2 modType=0 formula=6 useKey=1 val=0.0000 bbKey="atk_up"',
            '[10:00:00.000] BUFF_START #1 id="buff_wpn_funnel_0010_atk_up" uid=1 owner=chr_0027_tangtang src=chr_0027_tangtang dur=2.00 lifeT=2.00 passed=0.00 enh=1',
            '[10:00:00.000]   BB[2]: atk_up=0.5 =0 duration=2',
            '[10:00:00.500] HP_V2 #2 hit=150 cum=150 raw=150.00 pHP=5000 eHP=900000 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="weapon-self-owner.log",
        first_hit_hint="10-00-00-000",
        last_hit_hint="10-00-01-000",
    )

    participants = {entry["character_key"]: entry for entry in parsed["participants"]}
    assert participants["chr_0027_tangtang"]["dps"] == 150.0
    assert participants["chr_0027_tangtang"]["rdps"] == 150.0
    assert participants.get("chr_0011_seraph") is None


def test_parse_raw_battle_log_text_does_not_credit_replicated_unlisted_weapon_effect() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] ATTR_MOD buff="buff_wpn_sword_0018_atk_up" i=0 attrType=2 modType=0 formula=6 useKey=1 val=0.0000 bbKey="atk_up"',
            '[10:00:00.000] BUFF_START #1 id="buff_wpn_sword_0018_atk_up" uid=1 owner=chr_0019_karin src=chr_0019_karin dur=2.00 lifeT=2.00 passed=0.00 enh=1',
            '[10:00:00.000]   BB[2]: atk_up=0.09 =0 duration=2',
            '[10:00:00.001] ATTR_MOD buff="buff_wpn_sword_0018_atk_up" i=0 attrType=2 modType=0 formula=6 useKey=1 val=0.0000 bbKey="atk_up"',
            '[10:00:00.001] BUFF_START #2 id="buff_wpn_sword_0018_atk_up" uid=2 owner=chr_0027_tangtang src=chr_0019_karin dur=2.00 lifeT=2.00 passed=0.00 enh=1',
            '[10:00:00.001]   BB[2]: atk_up=0.09 =0 duration=2',
            '[10:00:00.500] HP_V2 #3 hit=109 cum=109 raw=109.00 pHP=5000 eHP=900000 skill="chr_0027_tangtang_attack1_projhit" actionId=0 damageUnitIndex=0 hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="weapon-team-source.log",
        first_hit_hint="10-00-00-000",
        last_hit_hint="10-00-01-000",
    )

    participants = {entry["character_key"]: entry for entry in parsed["participants"]}
    assert participants["chr_0027_tangtang"]["dps"] == 109.0
    assert participants["chr_0027_tangtang"]["rdps"] == 109.0
    assert participants.get("chr_0019_karin", {}).get("rdps", 0) == 0.0
    assert any(row['reason'] == 'external_effect_not_whitelisted'
               for row in parsed['rdps_preflight']['blockers'])


def test_parse_raw_battle_log_text_credits_pograni_pd_up_weapon_team_buff() -> None:
    content = "\n".join(
        [
            "[10:00:00.000] GAME_TIMER_START seq=1 source=ChallengeStart self=0 msg=0 startMs=0 expireMs=0 prepareSeconds=0",
            '[10:00:00.000] BUFF_START #1 id="1806" uid=1 owner=chr_0029_pograni src=chr_0029_pograni dur=30.00 lifeT=30.00 passed=0.00 enh=1',
            "[10:00:00.000]   BB[1]: pd_up=0.08 duration=30 max_stack=5",
            '[10:00:00.001] BUFF_START #2 id="1806" uid=2 owner=chr_0003_endminf src=chr_0029_pograni dur=30.00 lifeT=30.00 passed=0.00 enh=1',
            "[10:00:00.001]   BB[1]: pd_up=0.04 duration=30 max_stack=5",
            '[10:00:00.500] HP_V2 #3 hit=104 cum=104 raw=104.00 pHP=5000 eHP=900000 skill="chr_0003_endminf_attack1" hits=1 src=chr_0003_endminf tgt=eny_0051_rodin atk=chr_0003_endminf seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:01.000] GAME_TIMER_END seq=1 source=ChallengeComplete self=0 msg=0 elapsedMs=1000 startMs=0 expireMs=0 forceLeaveTs=0 sane=1",
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="pograni-pd-up-weapon-team.log",
        first_hit_hint="10-00-00-000",
        last_hit_hint="10-00-01-000",
    )

    participants = {entry["character_key"]: entry for entry in parsed["participants"]}
    assert participants["chr_0003_endminf"]["dps"] == 104.0
    assert participants["chr_0003_endminf"]["rdps"] == 100.0
    assert participants["chr_0029_pograni"]["dps"] == 0.0
    assert participants["chr_0029_pograni"]["rdps"] == 4.0

    team_buff_event = next(
        event
        for event in parsed["timeline_events"]
        if event["lane_type"] == "buff" and event["target_player_key"] == "chr_0003_endminf"
    )
    assert team_buff_event["event_key"] == "buff_wpn_sword_0016_valid"
    assert team_buff_event["event_name"] == "增伤"
    assert team_buff_event["source_character_key"] == "chr_0029_pograni"
    assert team_buff_event["effects"] == [{"zone": "dmg_inc", "element": "physical", "rate": 0.04}]


def test_parse_raw_battle_log_text_maps_cryst_attr_type_to_damage_increase() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] ATTR_MOD buff="buff_cryst_damage_up_demo" i=0 attrType=53 modType=0 formula=5 useKey=1 val=0.0000 bbKey="cryst_dmg_up2"',
            '[10:00:00.000] BUFF_START #1 id="buff_cryst_damage_up_demo" uid=1 owner=chr_0027_tangtang src=chr_0013_aglina dur=2.00 lifeT=2.00 passed=0.00 enh=1',
            '[10:00:00.000]   BB[2]: cryst_dmg_up2=0.25 =0 duration=2',
            '[10:00:00.500] HP_V2 #2 hit=150 cum=150 raw=150.00 pHP=5000 eHP=900000 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="damage-up-bb-key.log",
        first_hit_hint="10-00-00-000",
        last_hit_hint="10-00-01-000",
    )

    participants = {entry["character_key"]: entry for entry in parsed["participants"]}
    assert participants["chr_0027_tangtang"]["dps"] == 150.0
    assert participants["chr_0027_tangtang"]["rdps"] == 120.0
    assert participants["chr_0013_aglina"]["dps"] == 0.0
    assert participants["chr_0013_aglina"]["rdps"] == 30.0
    buff_event = next(event for event in parsed["timeline_events"] if event["lane_type"] == "buff")
    assert buff_event["event_name"] == "增伤"


def test_parse_raw_battle_log_text_supports_dynamic_def_decrease_windows() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] ATTR_MOD buff="buff_common_natural_cryst_triggered" i=0 attrType=82 modType=0 formula=5 useKey=1 val=0.0000 bbKey="def_decrease"',
            '[10:00:00.000] BUFF_START #1 id="buff_common_natural_cryst_triggered" uid=1 owner=eny_0051_rodin src=chr_0013_aglina dur=15.00 lifeT=15.00 passed=0.00 enh=1',
            '[10:00:00.000]   BB[4]: def_decrease=0.08129 =0 max_def_decrease=0.271 =0 def_decrease_tick=0.01897 =0 duration=15',
            '[10:00:10.000] HP_V2 #2 hit=150 cum=150 raw=150.00 pHP=5000 eHP=900000 skill="buff_chr_0026_lastrite_normal_skill_phantom_main" hits=1 src=chr_0026_lastrite tgt=eny_0051_rodin atk=chr_0026_lastrite seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="dynamic-def-decrease.log",
        first_hit_hint="10-00-10-000",
        last_hit_hint="10-00-11-000",
    )

    participants = {entry["character_key"]: entry for entry in parsed["participants"]}
    assert participants["chr_0026_lastrite"]["dps"] == 150.0
    assert participants["chr_0026_lastrite"]["rdps"] == 118.02
    assert participants["chr_0013_aglina"]["dps"] == 0.0
    assert participants["chr_0013_aglina"]["rdps"] == 31.98
    buff_event = next(event for event in parsed["timeline_events"] if event["lane_type"] == "buff")
    assert buff_event["event_name"] == "腐蚀 / 减抗"


def test_parse_raw_battle_log_text_classifies_enhance_default_child_as_spell_amp() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] BUFF_START #1 id="buff_common_affixes_enhance_spell_default_child" uid=1 owner=chr_0026_lastrite src=chr_0011_seraph dur=2.00 lifeT=2.00 passed=0.00 enh=1',
            '[10:00:00.000]   BB[2]: rate=0.5 =0 duration=2',
            '[10:00:00.500] HP_V2 #2 hit=150 cum=150 raw=150.00 pHP=5000 eHP=900000 skill="buff_chr_0026_lastrite_normal_skill_phantom_main" hits=1 src=chr_0026_lastrite tgt=eny_0051_rodin atk=chr_0026_lastrite seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="enhance-default-child.log",
        first_hit_hint="10-00-00-000",
        last_hit_hint="10-00-01-000",
    )

    participants = {entry["character_key"]: entry for entry in parsed["participants"]}
    assert participants["chr_0026_lastrite"]["dps"] == 150.0
    assert participants["chr_0026_lastrite"]["rdps"] == 100.0
    assert participants["chr_0011_seraph"]["dps"] == 0.0
    assert participants["chr_0011_seraph"]["rdps"] == 50.0


def test_parse_raw_battle_log_text_rehomes_off_loadout_player_enhance_source_to_owner() -> None:
    content = "\n".join(
        [
            "[10:00:00.000] LOADOUT slot=0 char=chr_0030_zhuangfy weaponTemplate=wpn_sword_0006 equipSuit={}",
            "[10:00:00.000] LOADOUT slot=1 char=chr_0019_karin weaponTemplate=wpn_sword_0006 equipSuit={}",
            '[10:00:00.000] ATTR_MOD buff="buff_common_affixes_enhance_pulse_default_child" i=0 attrType=66 modType=0 formula=5 useKey=1 val=0.0000 bbKey="rate"',
            '[10:00:00.000] BUFF_START #1 id="buff_common_affixes_enhance_pulse_default_child" uid=1 owner=chr_0030_zhuangfy src=chr_0013_aglina dur=2.00 lifeT=2.00 passed=0.00 enh=1',
            "[10:00:00.000]   BB[1]: rate=0.18 duration=2",
            '[10:00:00.500] HP_V2 #2 hit=1180 cum=1180 raw=1180.00 pHP=5000 eHP=900000 skill="chr_0030_zhuangfy_normal_skill_ult" hits=1 src=chr_0030_zhuangfy tgt=eny_0051_rodin atk=chr_0030_zhuangfy seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="off-loadout-player-enhance.log",
        first_hit_hint="10-00-00-000",
        last_hit_hint="10-00-01-000",
    )

    participants = {entry["character_key"]: entry for entry in parsed["participants"]}
    assert set(participants) == {"chr_0030_zhuangfy"}
    assert participants["chr_0030_zhuangfy"]["total_rd"] == 1180.0
    damage_event = next(event for event in parsed["timeline_events"] if event["lane_type"] == "skill")
    assert {item["character_key"] for item in damage_event["rdps_contributions"]} == {"chr_0030_zhuangfy"}
    buff_event = next(event for event in parsed["timeline_events"] if event["lane_type"] == "buff")
    assert buff_event["source_character_key"] == "chr_0030_zhuangfy"


def test_parse_raw_battle_log_text_filters_enhance_default_child_by_element() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] BUFF_START #1 id="buff_common_affixes_enhance_spell_default_child" uid=1 owner=chr_0015_lifeng src=chr_0011_seraph dur=2.00 lifeT=2.00 passed=0.00 enh=1',
            '[10:00:00.000]   BB[2]: rate=0.5 =0 duration=2',
            '[10:00:00.500] HP_V2 #2 hit=150 cum=150 raw=150.00 pHP=5000 eHP=900000 skill="chr_0015_lifeng_attack1" hits=1 src=chr_0015_lifeng tgt=eny_0051_rodin atk=chr_0015_lifeng seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="enhance-default-child-element.log",
        first_hit_hint="10-00-00-000",
        last_hit_hint="10-00-01-000",
    )

    participants = {entry["character_key"]: entry for entry in parsed["participants"]}
    assert participants["chr_0015_lifeng"]["dps"] == 150.0
    assert participants["chr_0015_lifeng"]["rdps"] == 150.0
    assert "chr_0011_seraph" not in participants or participants["chr_0011_seraph"]["rdps"] == 0.0


def test_parse_raw_battle_log_text_does_not_treat_seraph_ultimate_wrapper_as_attack_buff() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] BUFF_START #1 id="buff_chr_0011_seraph_atk_buff" uid=1 owner=chr_0027_tangtang src=chr_0011_seraph dur=12.00 lifeT=12.00 passed=0.00 enh=1',
            "[10:00:00.000]   BB[6]: atk_up=0.242 =0 duration=12 =0 wisd_up=0.000308 =0",
            '[10:00:00.000] ATTR_MOD buff="buff_common_affixes_enhance_crystal" i=0 attrType=67 modType=0 formula=5 useKey=1 val=0.0000 bbKey="rate"',
            '[10:00:00.000] BUFF_START #2 id="buff_common_affixes_enhance_crystal" uid=2 owner=chr_0027_tangtang src=chr_0011_seraph dur=12.00 lifeT=12.00 passed=0.00 enh=1',
            "[10:00:00.000]   BB[3]: rate=0.5 =0 duration=12",
            '[10:00:00.500] HP_V2 #3 hit=150 cum=150 raw=150.00 pHP=5000 eHP=900000 skill="buff_chr_0026_lastrite_normal_skill_phantom_main" hits=1 src=chr_0026_lastrite tgt=eny_0051_rodin atk=chr_0026_lastrite seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="seraph-ultimate-wrapper.log",
        first_hit_hint="10-00-00-000",
        last_hit_hint="10-00-01-000",
    )

    buff_events = [event for event in parsed["timeline_events"] if event["lane_type"] == "buff"]
    assert [event["event_key"] for event in buff_events] == ["buff_common_affixes_enhance_crystal"]
    assert buff_events[0]["event_name"] == "增幅"


def test_parse_raw_battle_log_text_recognizes_seraph_ultimate_child_buff_as_elemental_amp() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] BUFF_START #1 id="buff_chr_0011_seraph_ultimate_effect" uid=1 owner=chr_0017_yvonne src=chr_0011_seraph dur=12.00 lifeT=12.00 passed=0.00 enh=1',
            "[10:00:00.000]   BB[2]: rate=0.414858 duration=12",
            '[10:00:00.500] HP_V2 #2 hit=150 cum=150 raw=150.00 pHP=5000 eHP=900000 skill="chr_0017_yvonne_ult_attack1_projhit" hits=1 src=chr_0017_yvonne tgt=eny_0051_rodin atk=chr_0017_yvonne seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="seraph-ultimate-child.log",
        first_hit_hint="10-00-00-000",
        last_hit_hint="10-00-01-000",
    )

    participants = {entry["character_key"]: entry for entry in parsed["participants"]}
    assert participants["chr_0017_yvonne"]["dps"] == 150.0
    assert participants["chr_0017_yvonne"]["rdps"] == 106.02
    assert round(participants["chr_0011_seraph"]["rdps"], 2) == 43.98
    buff_event = next(event for event in parsed["timeline_events"] if event["lane_type"] == "buff")
    assert buff_event["event_key"] == "buff_chr_0011_seraph_ultimate_effect"
    assert buff_event["event_name"] == "增幅"


def test_parse_raw_battle_log_text_keeps_valid_generic_buff_src_without_chr_borrow() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] BUFF_START #1 id="buff_chr_0017_yvonne_ultimate_skill_voice_start" uid=1 owner=chr_0017_yvonne src=chr_0017_yvonne dur=4.00 lifeT=4.00 passed=0.00 enh=1',
            '[10:00:00.000]   BB[1]: duration=4',
            '[10:00:00.100] ATTR_MOD buff="buff_common_affixes_enhance_crystal" i=0 attrType=67 modType=0 formula=5 useKey=1 val=0.0000 bbKey="rate"',
            '[10:00:00.100] BUFF_START #2 id="buff_common_affixes_enhance_crystal" uid=2 owner=chr_0017_yvonne src=chr_0011_seraph dur=2.00 lifeT=2.00 passed=0.00 enh=1',
            '[10:00:00.100]   BB[2]: rate=0.5 =0 duration=2',
            '[10:00:00.500] HP_V2 #3 hit=150 cum=150 raw=150.00 pHP=5000 eHP=900000 skill="chr_0017_yvonne_ult_attack1_projhit" hits=1 src=chr_0017_yvonne tgt=eny_0051_rodin atk=chr_0017_yvonne seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="generic-src.log",
        first_hit_hint="10-00-00-000",
        last_hit_hint="10-00-01-000",
    )

    participants = {entry["character_key"]: entry for entry in parsed["participants"]}
    assert participants["chr_0017_yvonne"]["dps"] == 150.0
    assert participants["chr_0017_yvonne"]["rdps"] == 100.0
    assert participants["chr_0011_seraph"]["dps"] == 0.0
    assert participants["chr_0011_seraph"]["rdps"] == 50.0


def test_parse_raw_battle_log_text_extends_indefinite_enemy_vulnerable_to_battle_end() -> None:
    content = "\n".join(
        [
            "[10:00:00.000] GAME_TIMER_START seq=1 source=OnSrvStart self=0 startMs=0 expireMs=0",
            '[10:00:00.000] BUFF_START #1 id="buff_chr_0013_aglina_ultimate_spell_vulnerable" uid=1 owner=eny_0051_rodin src=chr_0013_aglina dur=340282346638528859811704183484516925440.00 lifeT=340282346638528859811704183484516925440.00 passed=0.00 enh=1',
            '[10:00:00.000]   BB[1]: rate=0.5',
            '[10:00:05.000] HP_V2 #2 hit=150 cum=150 raw=150.00 pHP=5000 eHP=900000 skill="chr_0017_yvonne_ult_attack1_projhit" hits=1 src=chr_0017_yvonne tgt=eny_0051_rodin atk=chr_0017_yvonne seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:06.000] GAME_TIMER_END seq=3 source=OnSrvComplete self=0 elapsedMs=6000 startMs=0 expireMs=0 sane=1",
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="indefinite-vuln.log",
        first_hit_hint="10-00-00-000",
        last_hit_hint="10-00-06-000",
    )

    participants = {entry["character_key"]: entry for entry in parsed["participants"]}
    assert participants["chr_0017_yvonne"]["dps"] == 25.0
    assert participants["chr_0017_yvonne"]["rdps"] == 16.67
    assert participants["chr_0013_aglina"]["dps"] == 0.0
    assert participants["chr_0013_aglina"]["rdps"] == 8.33


def test_parse_raw_battle_log_text_infers_missing_debuff_end_from_related_parent() -> None:
    content = "\n".join(
        [
            "[10:00:00.000] GAME_TIMER_START seq=1 source=OnSrvStart self=0 startMs=0 expireMs=0",
            '[10:00:00.000] BUFF_START #1 id="buff_chr_0013_aglina_ultimate_skill" uid=parent owner=eny_0051_rodin src=chr_0013_aglina dur=9999.00 lifeT=9999.00 passed=0.00 enh=1',
            '[10:00:00.000] ATTR_MOD buff="buff_chr_0013_aglina_ultimate_spell_vulnerable" i=0 attrType=85 modType=0 formula=0 useKey=1 val=0.0000 bbKey="rate"',
            '[10:00:00.000] BUFF_START #2 id="buff_chr_0013_aglina_ultimate_spell_vulnerable" uid=main owner=eny_0051_rodin src=chr_0013_aglina dur=9999.00 lifeT=9999.00 passed=0.00 enh=1',
            "[10:00:00.000]   BB[main]: rate=0.5 duration=-1",
            '[10:00:05.000] HP_V2 #3 hit=150 cum=150 raw=150.00 pHP=5000 eHP=900000 skill="chr_0017_yvonne_ult_attack1_projhit" hits=1 src=chr_0017_yvonne tgt=eny_0051_rodin atk=chr_0017_yvonne seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:06.000] BUFF_END #4 id="buff_chr_0013_aglina_ultimate_skill" uid=parent',
            '[10:00:07.000] HP_V2 #5 hit=150 cum=300 raw=150.00 pHP=5000 eHP=899850 skill="chr_0017_yvonne_ult_attack1_projhit" hits=2 src=chr_0017_yvonne tgt=eny_0051_rodin atk=chr_0017_yvonne seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:08.000] GAME_TIMER_END seq=6 source=OnSrvComplete self=0 elapsedMs=8000 startMs=0 expireMs=0 sane=1",
        ]
    )

    parsed = parse_raw_battle_log_text(content, file_name="related-parent-end.log")

    buff_events = [
        event
        for event in parsed["timeline_events"]
        if event["lane_type"] == "buff"
        and event["event_key"] == "buff_chr_0013_aglina_ultimate_spell_vulnerable"
    ]
    assert len(buff_events) == 1
    assert buff_events[0]["duration_ms"] == 6000
    participants = {entry["character_key"]: entry for entry in parsed["participants"]}
    assert round(participants["chr_0013_aglina"]["total_rd"], 2) == 50.0


def test_parse_raw_battle_log_text_does_not_infer_end_before_packet_modifier_evidence() -> None:
    content = "\n".join(
        [
            "[10:00:00.000] GAME_TIMER_START seq=1 source=OnSrvStart self=0 startMs=0 expireMs=0",
            '[10:00:00.000] BUFF_START #1 id="buff_chr_0028_wulfa_normal_bleed" uid=bleed owner=eny_0051_rodin src=chr_0028_wulfa dur=9999.00 lifeT=9999.00 passed=0.00 enh=1',
            "[10:00:00.000]   BB[bleed]: duration=25 damage_up=0.12",
            '[10:00:00.000] BUFF_START #2 id="buff_chr_0028_wulfa_normal_bleed_effect" uid=bleed_effect owner=eny_0051_rodin src=chr_0028_wulfa dur=9999.00 lifeT=9999.00 passed=0.00 enh=1',
            "[10:00:00.000]   BB[bleed_effect]: duration=0.9 atk_scale=0.3",
            '[10:00:00.500] BUFF_END #3 id="buff_chr_0028_wulfa_normal_bleed_effect" uid=bleed_effect',
            '[10:00:02.000] HP_V2 #1 hit=1000 cum=1000 raw=1000.00 pHP=0 eHP=900000 skill="chr_0029_pograni_attack1" hits=1 src=chr_0029_pograni tgt=eny_0051_rodin atk=chr_0029_pograni seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:02.000] PKT_MOD #1 atk=[] def=[bleed]",
            "[10:00:03.000] GAME_TIMER_END seq=2 source=OnSrvComplete self=0 elapsedMs=3000 startMs=0 expireMs=0 sane=1",
        ]
    )

    parsed = parse_raw_battle_log_text(content, file_name="packet-modifier-end-guard.log")

    buff_event = next(
        event
        for event in parsed["timeline_events"]
        if event["lane_type"] == "buff"
        and event["event_key"] == "buff_chr_0028_wulfa_normal_bleed"
    )
    assert buff_event["duration_ms"] == 2000
    participants = {entry["character_key"]: entry for entry in parsed["participants"]}
    assert round(participants["chr_0028_wulfa"]["total_rd"], 2) > 0.0


def test_parse_raw_battle_log_text_infers_missing_debuff_end_from_default_child() -> None:
    content = "\n".join(
        [
            "[10:00:00.000] GAME_TIMER_START seq=1 source=OnSrvStart self=0 startMs=0 expireMs=0",
            '[10:00:00.000] ATTR_MOD buff="buff_chr_0013_aglina_ultimate_spell_vulnerable" i=0 attrType=85 modType=0 formula=0 useKey=1 val=0.0000 bbKey="rate"',
            '[10:00:00.000] BUFF_START #2 id="buff_chr_0013_aglina_ultimate_spell_vulnerable" uid=main owner=eny_0051_rodin src=chr_0013_aglina dur=9999.00 lifeT=9999.00 passed=0.00 enh=1',
            "[10:00:00.000]   BB[main]: rate=0.5 duration=-1",
            '[10:00:00.000] BUFF_START #3 id="buff_common_affixes_vulnerable_spell_default_child" uid=child owner=eny_0051_rodin src=chr_0013_aglina dur=9999.00 lifeT=9999.00 passed=0.00 enh=1',
            "[10:00:00.000]   BB[child]: rate=0.5 duration=-1",
            '[10:00:05.000] HP_V2 #4 hit=150 cum=150 raw=150.00 pHP=5000 eHP=900000 skill="chr_0017_yvonne_ult_attack1_projhit" hits=1 src=chr_0017_yvonne tgt=eny_0051_rodin atk=chr_0017_yvonne seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:06.000] BUFF_END #5 id="buff_common_affixes_vulnerable_spell_default_child" uid=child',
            '[10:00:07.000] HP_V2 #6 hit=150 cum=300 raw=150.00 pHP=5000 eHP=899850 skill="chr_0017_yvonne_ult_attack1_projhit" hits=2 src=chr_0017_yvonne tgt=eny_0051_rodin atk=chr_0017_yvonne seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:08.000] GAME_TIMER_END seq=7 source=OnSrvComplete self=0 elapsedMs=8000 startMs=0 expireMs=0 sane=1",
        ]
    )

    parsed = parse_raw_battle_log_text(content, file_name="related-child-end.log")

    buff_event = next(
        event
        for event in parsed["timeline_events"]
        if event["lane_type"] == "buff"
        and event["event_key"] == "buff_chr_0013_aglina_ultimate_spell_vulnerable"
    )
    assert buff_event["duration_ms"] == 6000
    participants = {entry["character_key"]: entry for entry in parsed["participants"]}
    assert round(participants["chr_0013_aglina"]["total_rd"], 2) == 50.0


def test_parse_raw_battle_log_text_preserves_overlapping_stackable_buff_windows() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] BUFF_START #1 id="buff_stack_spell_vuln" uid=1 owner=eny_0051_rodin src=chr_0004_pelica dur=5.00 lifeT=5.00 passed=0.00 enh=1',
            '[10:00:00.000]   BB[1]: spell_taken_up=0.5',
            '[10:00:01.000] BUFF_START #2 id="buff_stack_spell_vuln" uid=2 owner=eny_0051_rodin src=chr_0004_pelica dur=5.00 lifeT=5.00 passed=0.00 enh=1',
            '[10:00:01.000]   BB[1]: spell_taken_up=0.5',
            '[10:00:01.500] HP_V2 #3 hit=150 cum=150 raw=150.00 pHP=5000 eHP=900000 skill="chr_0017_yvonne_ult_attack1_projhit" hits=1 src=chr_0017_yvonne tgt=eny_0051_rodin atk=chr_0017_yvonne seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="stacked-vuln.log",
        first_hit_hint="10-00-00-000",
        last_hit_hint="10-00-02-000",
    )

    participants = {entry["character_key"]: entry for entry in parsed["participants"]}
    assert participants["chr_0017_yvonne"]["dps"] == 75.0
    assert participants["chr_0017_yvonne"]["rdps"] == 37.5
    assert participants["chr_0004_pelica"]["dps"] == 0.0
    assert participants["chr_0004_pelica"]["rdps"] == 37.5


def test_parse_raw_battle_log_text_filters_attrisuit_next_skill_damage() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] BUFF_START #1 id="buff_equipsuit_attrisuitup_01" uid=1 owner=chr_0027_tangtang src=chr_0004_pelica dur=10.00 lifeT=10.00 passed=0.00 enh=1',
            '[10:00:00.000]   BB[2]: dmg_up=0.3 max_stack=2',
            '[10:00:00.500] HP_V2 #2 hit=130 cum=130 raw=130.00 pHP=5000 eHP=900000 skill="chr_0027_tangtang_combo_skill" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:01.000] HP_V2 #3 hit=130 cum=260 raw=130.00 pHP=5000 eHP=899870 skill="chr_0027_tangtang_normal_skill_water_projhit_1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="attrisuit-next-skill.log",
        first_hit_hint="10-00-00-500",
        last_hit_hint="10-00-01-000",
    )

    damage_events = [event for event in parsed["timeline_events"] if event["lane_type"] == "skill"]
    first_contribs = {item["character_key"]: item["value"] for item in damage_events[0]["rdps_contributions"]}
    second_contribs = {item["character_key"]: item["value"] for item in damage_events[1]["rdps_contributions"]}

    assert first_contribs == {"chr_0027_tangtang": 130.0}
    assert second_contribs == {"chr_0027_tangtang": 100.0, "chr_0004_pelica": 30.0}


def test_parse_raw_battle_log_text_filters_attr_type_skill_damage() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] ATTR_MOD buff="buff_demo_normal_skill_up" i=0 attrType=32 modType=0 formula=5 useKey=1 val=0.0000 bbKey="dmg_up"',
            '[10:00:00.000] BUFF_START #1 id="buff_demo_normal_skill_up" uid=1 owner=chr_0027_tangtang src=chr_0004_pelica dur=10.00 lifeT=10.00 passed=0.00 enh=1',
            '[10:00:00.000]   BB[1]: dmg_up=0.5',
            '[10:00:00.500] HP_V2 #2 hit=150 cum=150 raw=150.00 pHP=5000 eHP=900000 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:01.000] HP_V2 #3 hit=150 cum=300 raw=150.00 pHP=5000 eHP=899850 skill="chr_0027_tangtang_normal_skill_water_projhit_1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="attr-type-skill-filter.log",
        first_hit_hint="10-00-00-500",
        last_hit_hint="10-00-01-000",
    )

    damage_events = [event for event in parsed["timeline_events"] if event["lane_type"] == "skill"]
    first_contribs = {item["character_key"]: item["value"] for item in damage_events[0]["rdps_contributions"]}
    second_contribs = {item["character_key"]: item["value"] for item in damage_events[1]["rdps_contributions"]}

    assert first_contribs == {"chr_0027_tangtang": 150.0}
    assert second_contribs == {"chr_0027_tangtang": 100.0, "chr_0004_pelica": 50.0}


def test_parse_raw_battle_log_text_spell_vuln_does_not_apply_to_physical_school_natural_hit() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] BUFF_START #1 id="buff_wpn_funnel_0008_magic_damage_taken_up" uid=1 owner=eny_0051_rodin src=chr_0023_antal dur=15.00 lifeT=15.00 passed=0.00 enh=1',
            '[10:00:00.000]   BB[3]: spell_damage_taken_up=0.198 duration=15 lv=7',
            '[10:00:00.500] HP_V2 #2 hit=123 cum=123 raw=122.66 pHP=5000 eHP=900000 skill="chr_0025_ardelia_remain_loop_sheep" hits=1 src=chr_0025_ardelia tgt=eny_0051_rodin atk=chr_0025_ardelia seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:00.500] PKT_MOD #2 atk=[] def=[1]',
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="spell-vuln-school-filter.log",
        first_hit_hint="10-00-00-500",
        last_hit_hint="10-00-00-500",
    )

    damage_event = next(event for event in parsed["timeline_events"] if event["lane_type"] == "skill")
    contribs = {item["character_key"]: item["value"] for item in damage_event["rdps_contributions"]}
    assert contribs == {"chr_0025_ardelia": 123.0}


def test_parse_raw_battle_log_text_filters_external_buffs_by_packet_modifier_uid() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] GAME_TIMER_START seq=1 source=PacketBattleState startMs=0 expireMs=0 official=1',
            '[10:00:00.000] DMG_MOD buff="buff_pelica_spell_res_down" d=3 p=0 enableSide=1 class="DamageScaleProcessor" side=1 zone="NormalCalcZone" useKey=1 val=0.0000 bbKey="final_spell_resistance_decrease"',
            '[10:00:00.000] BUFF_START #1 id="buff_pelica_spell_res_down" uid=1 owner=eny_0051_rodin src=chr_0004_pelica dur=2.00 lifeT=2.00 passed=0.00 enh=1',
            '[10:00:00.000]   BB[1]: final_spell_resistance_decrease=0.2',
            '[10:00:00.000] DMG_MOD buff="buff_antal_spell_res_down" d=3 p=0 enableSide=1 class="DamageScaleProcessor" side=1 zone="NormalCalcZone" useKey=1 val=0.0000 bbKey="final_spell_resistance_decrease"',
            '[10:00:00.000] BUFF_START #2 id="buff_antal_spell_res_down" uid=2 owner=eny_0051_rodin src=chr_0023_antal dur=2.00 lifeT=2.00 passed=0.00 enh=1',
            '[10:00:00.000]   BB[2]: final_spell_resistance_decrease=0.2',
            '[10:00:00.500] HP_V2 #3 hit=144 cum=144 raw=144.00 pHP=5000 eHP=900000 skill="chr_0028_wulfa_ultimate_skill" hits=1 skillLv=12 templateIntId=2397 actionId=177 origTemplateIntId=2397 damageUnitIndex=0 src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:00.500] PKT_MOD #3 atk=[] def=[1]',
            '[10:00:01.000] GAME_TIMER_END seq=3 source=ChallengeComplete self=0 msg=0 elapsedMs=1000 startMs=0 expireMs=0 forceLeaveTs=0 sane=1',
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="packet-uid-filter.log",
        first_hit_hint="10-00-00-000",
        last_hit_hint="10-00-01-000",
    )

    damage_event = next(event for event in parsed["timeline_events"] if event["lane_type"] == "skill")
    contribs = {item["character_key"]: item["value"] for item in damage_event["rdps_contributions"]}
    assert contribs == {
        "chr_0028_wulfa": 120.0,
        "chr_0004_pelica": 24.0,
    }


def test_parse_raw_battle_log_text_keeps_enemy_fragile_when_packet_modifier_def_list_is_partial() -> None:
    content = "\n".join(
        [
            "[10:00:00.000] GAME_TIMER_START seq=1 source=PacketBattleState startMs=0 expireMs=0 official=1",
            '[10:00:00.000] ATTR_MOD buff="buff_chr_0013_aglina_ultimate_spell_vulnerable" i=0 attrType=85 modType=0 formula=0 useKey=1 val=0.0000 bbKey="rate"',
            '[10:00:00.000] BUFF_START #1 id="buff_chr_0013_aglina_ultimate_spell_vulnerable" uid=1 owner=eny_0051_rodin src=chr_0013_aglina dur=9999.00 lifeT=9999.00 passed=0.00 enh=1',
            "[10:00:00.000] BB[1]: rate=0.33 duration=-1",
            '[10:00:02.000] HP_V2 #2 hit=1330 cum=1330 raw=1330.00 pHP=5000 eHP=898670 skill="chr_0028_wulfa_ultimate_skill" hits=1 skillLv=12 templateIntId=2397 actionId=177 origTemplateIntId=2397 damageUnitIndex=0 src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:02.000] PKT_MOD #2 atk=[] def=[other_debuff]",
            "[10:00:03.000] GAME_TIMER_END seq=3 source=ChallengeComplete self=0 msg=0 elapsedMs=3000 startMs=0 expireMs=0 forceLeaveTs=0 sane=1",
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="partial-def-packet-fragile.log",
        first_hit_hint="10-00-00-000",
        last_hit_hint="10-00-03-000",
    )

    damage_event = next(event for event in parsed["timeline_events"] if event["lane_type"] == "skill")
    contribs = {item["character_key"]: item["value"] for item in damage_event["rdps_contributions"]}
    assert contribs == {
        "chr_0028_wulfa": 1000.0,
        "chr_0013_aglina": 330.0,
    }


def test_parse_raw_battle_log_text_physical_fragile_applies_to_physical_school_fire_hit() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] BUFF_START #1 id="buff_chr_0025_ardelia_affixes_vulnerable_physic_child" uid=1 owner=eny_0051_rodin src=chr_0025_ardelia dur=15.00 lifeT=15.00 passed=0.00 enh=1',
            '[10:00:00.000]   BB[1]: rate=0.28',
            '[10:00:00.500] HP_V2 #2 hit=370 cum=370 raw=370.42 pHP=5000 eHP=900000 skill="chr_0006_wolfgd_attack2_projhit" hits=1 src=chr_0006_wolfgd tgt=eny_0051_rodin atk=chr_0006_wolfgd seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="physical-fragile-school-filter.log",
        first_hit_hint="10-00-00-500",
        last_hit_hint="10-00-00-500",
    )

    damage_event = next(event for event in parsed["timeline_events"] if event["lane_type"] == "skill")
    contribs = {item["character_key"]: item["value"] for item in damage_event["rdps_contributions"]}
    assert contribs["chr_0025_ardelia"] > 0.0


def test_parse_raw_battle_log_text_projects_tangtang_speed_and_slow_utility_windows() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] BUFF_START #1 id="buff_chr_0027_tangtang_comboskill_waterbuff_outaura" uid=1 owner=chr_0004_pelica src=chr_0027_tangtang dur=3.00 lifeT=3.00 passed=0.00 enh=1',
            '[10:00:00.000]   BB[2]: duration_talent1buff=3 ratio_speed=0.2',
            '[10:00:00.000] BUFF_START #2 id="buff_chr_0027_tangtang_comboskill_waterdebuff_outaura" uid=2 owner=eny_0051_rodin src=chr_0027_tangtang dur=3.00 lifeT=3.00 passed=0.00 enh=1',
            '[10:00:00.000]   BB[2]: duration_talent1buff=3 ratio_speedreduction=0.6',
            '[10:00:00.500] HP_V2 #3 hit=100 cum=100 raw=100.00 pHP=5000 eHP=899900 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="tangtang-utility-windows.log",
        first_hit_hint="10-00-00-500",
        last_hit_hint="10-00-00-500",
    )

    buff_events = [event for event in parsed["timeline_events"] if event["lane_type"] == "buff"]
    speed = next(event for event in buff_events if event["event_key"] == "buff_chr_0027_tangtang_comboskill_waterbuff_outaura")
    slow = next(event for event in buff_events if event["event_key"] == "buff_chr_0027_tangtang_comboskill_waterdebuff_outaura")

    assert speed["event_name"] == "加速"
    assert speed["effects"] == [{"zone": "speedup", "element": "all", "rate": 0.2}]
    assert slow["event_name"] == "缓速"
    assert slow["effects"] == [{"zone": "slow", "element": "all", "rate": 0.6}]


def test_parse_raw_battle_log_text_does_not_treat_internal_cryst_trigger_numeric_buff_as_external_damage_buff() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] BUFF_START #1 id="buff_common_enemy_spell_cryst_triggered_fx" uid=1 owner=eny_0051_rodin src=chr_0027_tangtang dur=6.00 lifeT=6.00 passed=0.00 enh=1',
            '[10:00:00.000]   BB[4]: count=1 consumed_layer=1 atk_scale=1.6 shatter_dmg=2.4 frozen_duration=6',
            '[10:00:00.000] BUFF_START #2 id="225" uid=2 owner=eny_0051_rodin src=chr_0027_tangtang dur=6.00 lifeT=6.00 passed=0.00 enh=1',
            '[10:00:00.000]   BB[6]: duration=6 phy_dmg_up=0.2 final_phy_dmg_up=0.2 count=1 atk_scale=1.6 shatter_dmg=2.4 consumed_layer=1',
            '[10:00:00.050] HP_V2 #3 hit=15418 cum=15418 raw=15418.35 pHP=0 eHP=1358508 skill="chr_0027_tangtang_skill_225" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=-1.0000',
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="tangtang-internal-trigger.log",
        first_hit_hint="10-00-00-050",
        last_hit_hint="10-00-00-050",
    )

    buff_record = next(event for event in parsed["buff_events"] if event["event_key"] == "225")
    assert buff_record["zone_effects"] == []
    assert buff_record["packet_classification"]["class"] == "utility_or_marker"

    timeline_events = [event for event in parsed["timeline_events"] if event["lane_type"] == "buff"]
    assert all(event["event_key"] != "225" for event in timeline_events)


def test_parse_raw_battle_log_text_suppresses_duplicate_corrosion_fx_projection() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] BUFF_START #1 id="buff_common_enemy_spell_cryst_triggered_fx" uid=1 owner=eny_0051_rodin src=chr_0013_aglina dur=15.00 lifeT=15.00 passed=0.00 enh=1',
            "[10:00:00.000] BB[1]: duration=15 start_def_decrease=0.04 def_decrease_tick=0.01 max_def_decrease=0.14",
            '[10:00:00.100] BUFF_START #2 id="buff_common_natural_cryst_triggered" uid=2 owner=eny_0051_rodin src=chr_0013_aglina dur=15.00 lifeT=15.00 passed=0.00 enh=1',
            "[10:00:00.100] BB[2]: duration=15 start_def_decrease=0.04 def_decrease_tick=0.01 max_def_decrease=0.14",
            '[10:00:01.000] HP_V2 #3 hit=114 cum=114 raw=114.00 pHP=5000 eHP=899886 skill="chr_0028_wulfa_attack1" hits=1 src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        include_rdps_debug=True,
        file_name="corrosion-duplicate-fx.log",
        first_hit_hint="10-00-00-000",
        last_hit_hint="10-00-02-000",
    )
    effects = [
        contributor
        for zone in parsed["debug_hits"][0]["zones"]
        for contributor in zone["contributors"]
        if contributor.get("source_character_key") == "chr_0013_aglina"
    ]
    assert effects
    assert {row["event_key"] for row in effects} == {"buff_common_natural_cryst_triggered"}


def test_parse_raw_battle_log_text_does_not_promote_enemy_side_buff_id_prefix_into_party_source() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] BUFF_START #1 id="buff_chr_0021_whiten_combo_skill_physical_vulnerable" uid=1 owner=eny_0051_rodin src=eny_0051_rodin dur=3.00 lifeT=3.00 passed=0.00 enh=1',
            '[10:00:00.000]   BB[1]: rate=0.28',
            '[10:00:00.500] HP_V2 #2 hit=1000 cum=1000 raw=1000.00 pHP=5000 eHP=899000 skill="chr_0028_wulfa_attack1" hits=1 src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="enemy-side-buff-prefix.log",
        first_hit_hint="10-00-00-500",
        last_hit_hint="10-00-00-500",
    )

    participants = {entry["character_key"]: entry for entry in parsed["participants"]}
    assert "chr_0021_whiten" not in participants

    damage_event = next(event for event in parsed["timeline_events"] if event["lane_type"] == "skill")
    contrib_keys = {item["character_key"] for item in damage_event["rdps_contributions"]}
    assert "chr_0021_whiten" not in contrib_keys


def test_parse_raw_battle_log_text_does_not_credit_speedup_or_slow_as_rdps_damage_gain() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] ATTR_MOD buff="buff_wpn_funnel_0005_atk_up" i=0 attrType=2 modType=0 formula=6 useKey=1 val=0.0000 bbKey="atk_up"',
            '[10:00:00.000] BUFF_START #1 id="buff_wpn_funnel_0005_atk_up" uid=1 owner=chr_0028_wulfa src=chr_0004_pelica dur=20.00 lifeT=20.00 passed=0.00 enh=1',
            '[10:00:00.000]   BB[2]: atk_up=0.224 duration=20',
            '[10:00:00.000] BUFF_START #2 id="buff_chr_0027_tangtang_comboskill_waterbuff" uid=2 owner=chr_0028_wulfa src=chr_0027_tangtang dur=20.00 lifeT=20.00 passed=0.00 enh=1',
            '[10:00:00.000]   BB[2]: duration_waterbuff=20 ratio_speed=0.2',
            '[10:00:00.000] BUFF_START #3 id="buff_chr_0027_tangtang_comboskill_waterdebuff" uid=3 owner=eny_0051_rodin src=chr_0027_tangtang dur=20.00 lifeT=20.00 passed=0.00 enh=1',
            '[10:00:00.000]   BB[2]: duration_waterdebuff=20 ratio_speedreduction=0.6',
            '[10:00:00.500] HP_V2 #4 hit=1224 cum=1224 raw=1224.00 pHP=5000 eHP=898776 skill="chr_0028_wulfa_attack1" hits=1 src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="speedup-slow-not-rdps.log",
        first_hit_hint="10-00-00-500",
        last_hit_hint="10-00-00-500",
    )

    damage_event = next(event for event in parsed["timeline_events"] if event["lane_type"] == "skill")
    contribs = {item["character_key"]: item["value"] for item in damage_event["rdps_contributions"]}
    assert "chr_0027_tangtang" not in contribs
    assert abs(sum(contribs.values()) - 1224.0) < 0.001


def test_parse_raw_battle_log_text_uses_canonical_display_name_for_derived_skill() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] HP_V2 #1 hit=123 cum=123 raw=122.66 pHP=5000 eHP=900000 skill="chr_0025_ardelia_remain_loop_sheep" hits=1 src=chr_0025_ardelia tgt=eny_0051_rodin atk=chr_0025_ardelia seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="canonical-display-name.log",
        first_hit_hint="10-00-00-000",
        last_hit_hint="10-00-00-000",
    )

    damage_event = next(event for event in parsed["timeline_events"] if event["lane_type"] == "skill")
    assert damage_event["event_key"] == "chr_0025_ardelia_remain_loop_sheep"
    assert damage_event["event_name"] == "绵羊持续伤害"


def test_parse_raw_battle_log_text_maps_runtime_damage_buff_hit_back_to_origin_skill() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] BUFF_START #1 id="2018" uid=1 owner=eny_0051_rodin src=chr_0028_wulfa dur=9999.00 lifeT=9999.00 passed=0.00 enh=1',
            '[10:00:00.000]   BB[4]: duration=0.3 atk_scale=0.1725 trigger_times=3 damage_interval=0.125',
            '[10:00:00.200] HP_V2 #2 hit=614 cum=614 raw=613.62 pHP=0 eHP=899386 skill="chr_0028_wulfa_skill_2017" hits=1 skillLv=unknown templateIntId=2017 actionId=unknown origTemplateIntId=2257 damageUnitIndex=0 partInstId=unknown dynBB=unknown calcBB=unknown src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa seg=0 shared=2 critFlag=0 critDmg=-1.0000',
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="wulfa-combo-2-trigger.log",
        first_hit_hint="10-00-00-200",
        last_hit_hint="10-00-00-200",
    )

    damage_event = next(event for event in parsed["timeline_events"] if event["lane_type"] == "skill")
    assert damage_event["event_key"] == "chr_0028_wulfa_combo_2_skill"
    assert damage_event["event_name"] == "燎影时刻 / 派生"


def test_parse_raw_battle_log_text_maps_runtime_skill_number_to_damage_buff_name() -> None:
    content = "\n".join(
        [
            '[10:00:00.200] HP_V2 #2 hit=1049 cum=1049 raw=1049.39 pHP=0 eHP=899000 skill="chr_0028_wulfa_skill_2193" hits=1 skillLv=unknown templateIntId=2193 actionId=2 origTemplateIntId=unknown damageUnitIndex=0 partInstId=unknown dynBB=unknown calcBB=unknown src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa seg=0 shared=2 critFlag=0 critDmg=-1.0000',
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="wulfa-normal-bleed.log",
        first_hit_hint="10-00-00-200",
        last_hit_hint="10-00-00-200",
    )

    damage_event = next(event for event in parsed["timeline_events"] if event["lane_type"] == "skill")
    assert damage_event["event_key"] == "buff_chr_0028_wulfa_normal_bleed"
    assert damage_event["event_name"] == "爪印斫痕"


def test_parse_raw_battle_log_text_treats_equipsuit_reapply_as_refresh() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] BUFF_START #1 id="buff_equipsuit_attrisuit_01" uid=1 owner=chr_0027_tangtang src=chr_0027_tangtang dur=999.00 lifeT=999.00 passed=0.00 enh=1',
            '[10:00:00.000]   BB[2]: atk_up=0.15 dmg_up=0.3',
            '[10:00:05.000] BUFF_START #2 id="buff_equipsuit_attrisuit_01" uid=2 owner=chr_0027_tangtang src=chr_0027_tangtang dur=999.00 lifeT=999.00 passed=0.00 enh=1',
            '[10:00:05.000]   BB[2]: atk_up=0.15 dmg_up=0.3',
            '[10:00:06.000] HP_V2 #3 hit=100 cum=100 raw=100.00 pHP=5000 eHP=900000 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="equipsuit-refresh.log",
        first_hit_hint="10-00-06-000",
        last_hit_hint="10-00-06-000",
    )

    buff_events = [
        event
        for event in parsed["timeline_events"]
        if event["lane_type"] == "buff" and event["event_key"] == "buff_equipsuit_attrisuit_01"
    ]
    assert len(buff_events) == 1


def test_parse_raw_battle_log_text_maps_rate_spellvulnerable_to_fragile() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] BUFF_START #1 id="buff_chr_0027_tangtang_normalskill_spellvulnerable" uid=1 owner=eny_0051_rodin src=chr_0027_tangtang dur=5.00 lifeT=5.00 passed=0.00 enh=1',
            '[10:00:00.000]   BB[3]: duration_spellvulnerable=5 =0 rate_spellvulnerable=0.2 =0 cntmax=1',
            '[10:00:00.500] HP_V2 #2 hit=150 cum=150 raw=150.00 pHP=5000 eHP=900000 skill="chr_0017_yvonne_ult_attack1_projhit" hits=1 src=chr_0017_yvonne tgt=eny_0051_rodin atk=chr_0017_yvonne seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="rate-spellvulnerable.log",
        first_hit_hint="10-00-00-000",
        last_hit_hint="10-00-01-000",
    )

    participants = {entry["character_key"]: entry for entry in parsed["participants"]}
    assert participants["chr_0017_yvonne"]["dps"] == 150.0
    assert participants["chr_0017_yvonne"]["rdps"] == 125.0
    assert participants["chr_0027_tangtang"]["dps"] == 0.0
    assert participants["chr_0027_tangtang"]["rdps"] == 25.0


def test_parse_raw_battle_log_text_maps_lifeng_purify_to_physical_fragile() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] BUFF_START #1 id="buff_chr_0015_lifeng_purify" uid=1 owner=eny_0051_rodin src=chr_0015_lifeng dur=9999.00 lifeT=9999.00 passed=0.00 enh=1',
            '[10:00:00.000]   BB[1]: duration=12 rate=0.1',
            '[10:00:00.000] BUFF_START #2 id="buff_chr_0015_lifeng_purify_icon" uid=2 owner=eny_0051_rodin src=chr_0015_lifeng dur=9999.00 lifeT=9999.00 passed=0.00 enh=1',
            '[10:00:00.000]   BB[2]: duration=12 rate=0.1',
            '[10:00:00.500] HP_V2 #3 hit=110 cum=110 raw=110.00 pHP=5000 eHP=900000 skill="chr_0028_wulfa_attack1" hits=1 src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="lifeng-physical-fragile.log",
        first_hit_hint="10-00-00-000",
        last_hit_hint="10-00-01-000",
        include_rdps_debug=True,
    )

    participants = {entry["character_key"]: entry for entry in parsed["participants"]}
    assert participants["chr_0028_wulfa"]["dps"] == 110.0
    assert participants["chr_0028_wulfa"]["rdps"] == 100.0
    assert participants["chr_0015_lifeng"]["dps"] == 0.0
    assert participants["chr_0015_lifeng"]["rdps"] == 10.0
    assert parsed["rdps_preflight"]["ok"] is True


def test_parse_raw_battle_log_text_allows_avywen_ultimate_pulse_fragile_with_skill_guard() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] SKILL_CAST_START seq=1 startMs=0 inst=ult1 owner=chr_0012_avywen skill=chr_0012_avywen_ultimate_skill',
            '[10:00:00.100] BUFF_START #2 id="buff_common_affixes_vulnerable_pulse_default_child" uid=2 owner=eny_0051_rodin src=chr_0012_avywen dur=10.00 lifeT=10.00 passed=0.00 enh=1',
            "[10:00:00.100]   BB[1]: duration=10 rate=0.1",
            '[10:00:00.500] HP_V2 #3 hit=110 cum=110 raw=110.00 pHP=5000 eHP=900000 skill="unknown_proc_damage" hits=1 src=chr_0004_pelica tgt=eny_0051_rodin atk=chr_0004_pelica seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:00.500] DPD_RAW #3 probe=3 calc=110.0000 atkScale=1.0000 blocked=0 damageType=0x3 decorateMask=0x80 collider="BattleShape" atkZones=[1.0000,1.0000,1.0000,1.0000,1.0000,1.0000] defZones=[1.0000,1.0000,1.0000,1.0000,1.0000,1.1000]',
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="avywen-ultimate-pulse-fragile.log",
        first_hit_hint="10-00-00-000",
        last_hit_hint="10-00-01-000",
        include_rdps_debug=True,
    )

    participants = {entry["character_key"]: entry for entry in parsed["participants"]}
    assert parsed["rdps_preflight"]["ok"] is True
    assert participants["chr_0004_pelica"]["rdps"] == 100.0
    assert participants["chr_0012_avywen"]["rdps"] == 10.0


def test_parse_raw_battle_log_text_blocks_avywen_pulse_fragile_without_ultimate_guard() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] SKILL_CAST_START seq=1 startMs=0 inst=skill1 owner=chr_0012_avywen skill=chr_0012_avywen_normal_skill',
            '[10:00:00.100] BUFF_START #2 id="buff_common_affixes_vulnerable_pulse_default_child" uid=2 owner=eny_0051_rodin src=chr_0012_avywen dur=10.00 lifeT=10.00 passed=0.00 enh=1',
            "[10:00:00.100]   BB[1]: duration=10 rate=0.1",
            '[10:00:00.500] HP_V2 #3 hit=110 cum=110 raw=110.00 pHP=5000 eHP=900000 skill="unknown_proc_damage" hits=1 src=chr_0004_pelica tgt=eny_0051_rodin atk=chr_0004_pelica seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:00.500] DPD_RAW #3 probe=3 calc=110.0000 atkScale=1.0000 blocked=0 damageType=0x3 decorateMask=0x80 collider="BattleShape" atkZones=[1.0000,1.0000,1.0000,1.0000,1.0000,1.0000] defZones=[1.0000,1.0000,1.0000,1.0000,1.0000,1.1000]',
        ]
    )

    parsed = parse_raw_battle_log_text(
        content,
        file_name="avywen-normal-pulse-fragile-blocked.log",
        first_hit_hint="10-00-00-000",
        last_hit_hint="10-00-01-000",
        include_rdps_debug=True,
    )

    assert parsed["rdps_preflight"]["ok"] is False
    assert parsed["rdps_preflight"]["blockers"][0]["event_key"] == "buff_common_affixes_vulnerable_pulse_default_child"
    assert parsed["rdps_damage_basis"]["rdps_strict_ok"] is False


def test_canonical_num_table_skill_id_rejects_enemy_static_skill_for_player_runtime_id() -> None:
    assert _canonical_num_table_skill_id("chr_0015_lifeng_skill_1817", "chr_0015_lifeng") is None


def test_parse_raw_battle_log_text_marks_poise_damage_hits() -> None:
    content = "\n".join(
        [
            "[10:00:00.000] GAME_TIMER_START seq=1 source=ChallengeStart startMs=0 expireMs=0",
            '[10:00:00.500] HP_V2 #1 hit=100 cum=100 raw=100.00 packetFinalValue=100.0 pHP=5000 eHP=900000 skill="chr_0028_wulfa_attack4" hits=1 src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa seg=0 shared=2 critFlag=0 critDmg=0.5000 templateIntId=1996 origTemplateIntId=1996',
            '[10:00:01.000] HP_V2 #2 hit=600 cum=700 raw=600.00 packetFinalValue=600.0 pHP=5000 eHP=899300 skill="chr_0028_wulfa_attack5" hits=1 src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa seg=0 shared=2 critFlag=0 critDmg=0.5000 templateIntId=1997 origTemplateIntId=1997',
            "[10:00:01.000] POISE_V1 seq=3 tick=1000 type=PoiseDamage value=-18 cur=262 attacker=? attackerId=unknown ownerType=BATTLE_ACTION_OWNER_TYPE_None sourceType=Skill source=chr_0028_wulfa_attack5 sourceInt=1997 origSourceType=Skill origSource=chr_0028_wulfa_attack5 origSourceInt=1997 actionId=100741",
            '[10:00:01.500] HP_V2 #4 hit=300 cum=1000 raw=300.00 packetFinalValue=300.0 pHP=5000 eHP=899000 skill="chr_0017_yvonne_ult_attack1_projhit" hits=1 src=chr_0017_yvonne tgt=eny_0051_rodin atk=chr_0017_yvonne seg=0 shared=2 critFlag=0 critDmg=0.5000 templateIntId=2101 origTemplateIntId=2101',
            "[10:00:02.000] GAME_TIMER_END seq=1 source=ChallengeComplete elapsedMs=2000 startMs=0 endMs=2000 expireMs=0 sane=1",
        ]
    )

    parsed = parse_raw_battle_log_text(content, include_rdps_debug=True, file_name="poise-damage.log")
    skill_events = {
        event["event_key"]: event
        for event in parsed["timeline_events"]
        if event["lane_type"] == "skill"
    }

    assert skill_events["chr_0028_wulfa_attack4"].get("poise_damage") is None
    assert skill_events["chr_0028_wulfa_attack5"]["poise_damage"] == {
        "type": "PoiseDamage",
        "value": -18.0,
        "current_value": 262.0,
        "source": "chr_0028_wulfa_attack5",
        "source_int": 1997,
        "orig_source": "chr_0028_wulfa_attack5",
        "orig_source_int": 1997,
    }
    assert skill_events["chr_0017_yvonne_ultimate_skill"]["poise_damage"] is None


def test_parse_raw_battle_log_text_emits_zero_damage_cast_events() -> None:
    content = "\n".join(
        [
            "[10:00:00.000] GAME_TIMER_START seq=1 source=ChallengeStart startMs=0 expireMs=0",
            '[10:00:00.500] HP_V2 #1 hit=100 cum=100 raw=100.00 packetFinalValue=100.0 pHP=5000 eHP=900000 skill="chr_0019_karin_attack1" hits=1 src=chr_0019_karin tgt=eny_0051_rodin atk=chr_0019_karin seg=0 shared=2 critFlag=0 critDmg=0.5000 templateIntId=1996 origTemplateIntId=1996',
            "[10:00:01.000] SKILL_CAST_START seq=5 startMs=1000 inst=100900 owner=chr_0019_karin skill=chr_0019_karin_ultimate_skill",
            "[10:00:01.400] SKILL_CAST_END seq=6 endMs=1400 inst=100900 owner=chr_0019_karin skill=chr_0019_karin_ultimate_skill",
            "[10:00:01.450] SKILL_CAST_START seq=7 startMs=1450 inst=100901 owner=chr_0019_karin skill=chr_0019_karin_skill_100901",
            "[10:00:01.500] SKILL_CAST_START seq=8 startMs=1500 inst=100902 owner=chr_0017_yvonne skill=chr_0017_yvonne_ultimate_skill",
            '[10:00:01.800] HP_V2 #2 hit=300 cum=400 raw=300.00 packetFinalValue=300.0 pHP=5000 eHP=899700 skill="chr_0017_yvonne_ult_attack1_projhit" hits=1 src=chr_0017_yvonne tgt=eny_0051_rodin atk=chr_0017_yvonne seg=0 shared=2 critFlag=0 critDmg=0.5000 templateIntId=2101 origTemplateIntId=2101',
            "[10:00:02.000] GAME_TIMER_END seq=1 source=ChallengeComplete elapsedMs=2000 startMs=0 endMs=2000 expireMs=0 sane=1",
        ]
    )

    parsed = parse_raw_battle_log_text(content, include_rdps_debug=True, file_name="zero-damage-cast.log")
    cast_events = [
        event for event in parsed["timeline_events"] if event.get("event_type") == "cast"
    ]

    # 只有秋栗的零伤害终结技被补录；运行时兜底 id 不发；带伤害的终结技不重复。
    assert [event["event_key"] for event in cast_events] == ["chr_0019_karin_ultimate_skill"]
    cast_event = cast_events[0]
    assert cast_event["lane_type"] == "skill"
    assert cast_event["source_character_key"] == "chr_0019_karin"
    assert cast_event["value"] is None
    assert cast_event["important"] is True


def test_parse_raw_battle_log_text_marks_damage_events_with_real_cast_start() -> None:
    content = "\n".join(
        [
            "[10:00:00.000] GAME_TIMER_START seq=1 source=ChallengeStart startMs=0 expireMs=0",
            "[10:00:00.000] SKILL_CAST_START seq=2 startMs=0 inst=cast1 owner=chr_0028_wulfa skill=chr_0028_wulfa_normal_skill skillSource=unknown",
            '[10:00:00.500] HP_V2 #1 hit=100 cum=100 raw=100.00 packetFinalValue=100.0 pHP=5000 eHP=900000 skill="chr_0028_wulfa_normal_skill" hits=1 src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:01.400] SKILL_CAST_START seq=3 startMs=1400 inst=proj1 owner=chr_0028_wulfa skill=chr_0028_wulfa_normal_skill_projhit5 skillSource=Summon",
            '[10:00:01.500] HP_V2 #2 hit=200 cum=300 raw=200.00 packetFinalValue=200.0 pHP=5000 eHP=899700 skill="chr_0028_wulfa_normal_skill" hits=1 src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:02.000] GAME_TIMER_END seq=1 source=ChallengeComplete elapsedMs=2000 startMs=0 endMs=2000 expireMs=0 sane=1",
        ]
    )

    parsed = parse_raw_battle_log_text(content, include_rdps_debug=True, file_name="cast-start-damage.log")
    skill_events = [
        event
        for event in parsed["timeline_events"]
        if event["lane_type"] == "skill" and event["event_key"] == "chr_0028_wulfa_normal_skill"
    ]

    assert [event["ts_ms_from_start"] for event in skill_events] == [500, 1500]
    assert [event["actual_start_ms_from_start"] for event in skill_events] == [0, 0]
