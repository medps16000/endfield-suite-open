from parser_core.hit_context import build_hit_context
from parser_core.hit_debug import parse_hit_debug_log_text


def test_hit_debug_uses_only_official_dungeon_context_for_stage_identity() -> None:
    hit = (
        '[10:00:00.500] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=5000 eHP=900000 '
        'skill="chr_0028_wulfa_normal_skill" hits=1 skillLv=12 templateIntId=2132 actionId=97 '
        'origTemplateIntId=2132 damageUnitIndex=0 src=chr_0028_wulfa tgt=eny_0051_rodin '
        'atk=chr_0028_wulfa seg=0 shared=2 critFlag=0 critDmg=-1.0000'
    )

    without_context = parse_hit_debug_log_text(hit, file_name="missing-stage.log")
    assert without_context["summary"]["boss_key"] == "eny_0051_rodin"
    assert without_context["summary"]["dungeon_key"] == "unknown_dungeon"
    assert without_context["summary"]["dungeon_identity_source"] == "missing_dungeon_context"

    with_context = parse_hit_debug_log_text(
        '\n'.join(
            [
                '[10:00:00.000] DUNGEON_CONTEXT dungeonId=indie_battletower004_ex',
                hit,
            ]
        ),
        file_name="official-stage.log",
    )
    assert with_context["summary"]["boss_key"] == "eny_0051_rodin"
    assert with_context["summary"]["dungeon_key"] == "indie_battletower004_ex"
    assert with_context["summary"]["dungeon_context_id"] == "indie_battletower004_ex"
    assert with_context["summary"]["dungeon_identity_source"] == "dungeon_context"


def test_hit_context_uses_damage_school_for_spell_vulnerability() -> None:
    spell_vuln_window = {
        "start_ts_ms": 0,
        "end_ts_ms": 10_000,
        "event_key": "buff_common_pulse_pulse_conduct_triggered_do",
        "event_name": "导电 / 承伤易伤",
        "uid": "conduct",
        "source_character_key": "chr_0004_pelica",
        "source_character_name": "佩丽卡",
        "target_character_key": "eny_0051_rodin",
        "target_actor_id": "enemy-1",
        "target_character_name": "“碾骨之拳”罗丹",
        "zone_effects": [{"zone": "vuln_taken", "element": "spell", "rate": 0.12}],
    }
    base_hit = {
        "character_key": "chr_0028_wulfa",
        "target_enemy_key": "eny_0051_rodin",
        "target_actor_id": "enemy-1",
        "ts_ms": 1_000,
        "hit_value": 112,
        "skill_key": "chr_0028_wulfa_normal_skill",
        "baseline": {},
        "dpd_raw": None,
    }

    spell_school_context = build_hit_context(
        {**base_hit, "damage_element": "physical", "damage_school": "spell"},
        [spell_vuln_window],
    )
    physical_school_context = build_hit_context(
        {**base_hit, "damage_element": "natural", "damage_school": "physical"},
        [spell_vuln_window],
    )

    assert spell_school_context["external_sources"] == [
        {
            "character_key": "chr_0004_pelica",
            "character_name": "佩丽卡",
            "effect_count": 1,
            "rdps_credit": 12.0,
        }
    ]
    assert physical_school_context["external_sources"] == []
    assert physical_school_context["ignored_effects"][0]["reason"] == "damage_school_filtered"
    assert physical_school_context["ignored_effects"][0]["reason_group"] == "element_mismatch"
    assert physical_school_context["ignored_effects"][0]["effect_element"] == "spell"
    assert physical_school_context["ignored_effects"][0]["hit_damage_school"] == "physical"


def test_parse_hit_debug_log_text_uses_action_damage_type_for_wulfa_skill_hits() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] DMG_MOD buff="buff_common_pulse_pulse_conduct_triggered_do" d=0 p=0 enableSide=1 class="DamageScaleProcessor" side=1 zone="NormalCalcZone" useKey=1 val=0.0000 bbKey="final_spell_resistance_decrease"',
            '[10:00:00.000] BUFF_START #1 id="buff_common_pulse_pulse_conduct_triggered_do" uid=1 owner=eny_0051_rodin src=chr_0004_pelica dur=2.00 lifeT=2.00 passed=0.00 enh=1',
            "[10:00:00.000]   BB[3]: spell_resistance_decrease=0.12 duration=2 final_spell_resistance_decrease=0.12",
            '[10:00:00.500] HP_V2 #2 hit=100 cum=100 raw=100.00 pHP=5000 eHP=900000 skill="chr_0028_wulfa_normal_skill" hits=1 skillLv=12 templateIntId=2132 actionId=97 origTemplateIntId=2132 damageUnitIndex=0 src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa seg=0 shared=2 critFlag=0 critDmg=-1.0000',
            '[10:00:00.700] HP_V2 #3 hit=112 cum=112 raw=112.00 pHP=5000 eHP=899888 skill="chr_0028_wulfa_normal_skill_projhit2" hits=1 skillLv=1 templateIntId=2156 actionId=88 origTemplateIntId=2132 damageUnitIndex=0 src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa seg=0 shared=2 critFlag=0 critDmg=-1.0000',
        ]
    )

    parsed = parse_hit_debug_log_text(content, file_name="wulfa-action-damage-type.log")
    physical_hit, fire_hit = parsed["hits"]

    assert physical_hit["damage_element"] == "physical"
    assert physical_hit["damage_school"] == "physical"
    assert physical_hit["zones"] == []
    assert fire_hit["damage_element"] == "fire"
    assert fire_hit["damage_school"] == "spell"
    assert fire_hit["zones"][0]["zone"] == "vuln_taken"
    assert fire_hit["external_sources"][0]["character_key"] == "chr_0004_pelica"


def test_parse_hit_debug_log_text_uses_enemy_status_bb_duration_for_conduct() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] DMG_MOD buff="buff_common_pulse_pulse_conduct_triggered_do" d=0 p=0 enableSide=1 class="DamageScaleProcessor" side=1 zone="NormalCalcZone" useKey=1 val=0.0000 bbKey="final_spell_resistance_decrease"',
            '[10:00:00.000] BUFF_START #1 id="buff_common_pulse_pulse_conduct_triggered_do" uid=conduct owner=eny_0051_rodin src=chr_0004_pelica dur=9999.00 lifeT=9999.00 passed=0.00 enh=1',
            "[10:00:00.000]   BB[conduct]: spell_resistance_decrease=0.12 duration=2 final_spell_resistance_decrease=0.12",
            '[10:00:01.000] HP_V2 #2 hit=100 cum=100 raw=100.00 pHP=5000 eHP=900000 skill="chr_0027_tangtang_normal_skill" hits=1 skillLv=12 templateIntId=2032 actionId=1 origTemplateIntId=2032 damageUnitIndex=0 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=-1.0000',
            '[10:00:03.000] HP_V2 #3 hit=100 cum=200 raw=100.00 pHP=5000 eHP=899900 skill="chr_0027_tangtang_normal_skill" hits=1 skillLv=12 templateIntId=2032 actionId=1 origTemplateIntId=2032 damageUnitIndex=0 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=-1.0000',
        ]
    )

    parsed = parse_hit_debug_log_text(content, file_name="conduct-bb-duration.log")
    inside_hit, outside_hit = parsed["hits"]
    conduct_audit = next(
        row
        for row in parsed["buff_audit"]
        if row["event_key"] == "buff_common_pulse_pulse_conduct_triggered_do"
    )

    assert conduct_audit["raw_duration_ms"] == 2000
    assert conduct_audit["effective_duration_ms"] == 2000
    assert inside_hit["zones"][0]["zone"] == "vuln_taken"
    assert outside_hit["zones"] == []


def test_parse_hit_debug_log_text_maps_wulfa_runtime_damage_buffs_before_cast_context() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] DMG_MOD buff="buff_common_pulse_pulse_conduct_triggered_do" d=0 p=0 enableSide=1 class="DamageScaleProcessor" side=1 zone="NormalCalcZone" useKey=1 val=0.0000 bbKey="final_spell_resistance_decrease"',
            '[10:00:00.000] BUFF_START #1 id="buff_common_pulse_pulse_conduct_triggered_do" uid=conduct owner=eny_0051_rodin src=chr_0004_pelica dur=2.00 lifeT=2.00 passed=0.00 enh=1',
            "[10:00:00.000]   BB[conduct]: spell_resistance_decrease=0.12 duration=2 final_spell_resistance_decrease=0.12",
            '[10:00:00.500] SKILL_CAST_START seq=2 startMs=500 inst=proj owner=chr_0028_wulfa skill=chr_0028_wulfa_normal_skill_projhit5',
            '[10:00:00.500] BUFF_START #3 id="buff_chr_0028_wulfa_normal_bleed" uid=bleed owner=eny_0051_rodin src=chr_0028_wulfa dur=25.00 lifeT=25.00 passed=0.00 enh=1',
            "[10:00:00.500]   BB[bleed]: atk_scale=0.3 damage_up=0.12 duration=25",
            '[10:00:00.500] HP_V2 #4 hit=100 cum=100 raw=100.00 pHP=5000 eHP=900000 skill="chr_0028_wulfa_skill_2193" hits=1 templateIntId=2193 actionId=2 damageUnitIndex=0 src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa seg=0 shared=2 critFlag=0 critDmg=-1.0000',
            '[10:00:00.600] BUFF_START #5 id="buff_chr_0028_wulfa_normal_bleed_crit_extra_damage" uid=crit_bleed owner=eny_0051_rodin src=chr_0028_wulfa dur=1.20 lifeT=1.20 passed=0.00 enh=1',
            "[10:00:00.600]   BB[crit_bleed]: atk_scale=0.24 duration=1.2",
            '[10:00:00.600] HP_V2 #6 hit=112 cum=112 raw=112.00 pHP=5000 eHP=899888 skill="chr_0028_wulfa_skill_2293" hits=1 templateIntId=2293 actionId=11 damageUnitIndex=0 src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa seg=0 shared=2 critFlag=1 critDmg=-1.0000',
        ]
    )

    parsed = parse_hit_debug_log_text(content, file_name="wulfa-runtime-damage-buffs.log")
    bleed_hit, crit_bleed_hit = parsed["hits"]

    assert bleed_hit["skill_key"] == "buff_chr_0028_wulfa_normal_bleed"
    assert bleed_hit["damage_element"] == "physical"
    assert bleed_hit["damage_school"] == "physical"
    assert bleed_hit["zones"] == []
    assert crit_bleed_hit["skill_key"] == "buff_chr_0028_wulfa_normal_bleed_crit_extra_damage"
    assert crit_bleed_hit["damage_element"] == "fire"
    assert crit_bleed_hit["damage_school"] == "spell"
    zones = {zone["zone"]: zone for zone in crit_bleed_hit["zones"]}
    bleed_contributor = next(
        contributor
        for contributor in zones["vuln_taken"]["contributors"]
        if contributor["event_key"] == "buff_chr_0028_wulfa_normal_bleed"
    )
    assert bleed_contributor["scope"] == "self"
    assert bleed_contributor["rate"] == 0.12
    assert bleed_contributor["condition"]["source"] == "CheckDamageType"
    assert zones["vuln_taken"]["contributors"][0]["event_key"] == "buff_common_pulse_pulse_conduct_triggered_do"


def test_parse_hit_debug_log_text_recovers_chr_template_enemy_targets() -> None:
    content = "\n".join(
        [
            "[10:00:00.000] SQUAD size=1 members=[chr_0027_tangtang_1001]",
            '[10:00:01.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=0 eHP=900000 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=chr_0016_laevat atk=chr_0027_tangtang atkId=1001 tgtId=2001 seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_hit_debug_log_text(content, file_name="mislabeled-chr-enemy.log")

    assert parsed["summary"]["hit_count"] == 1
    assert parsed["hits"][0]["target_enemy_key"] == "eny_0000_unknown"
    assert parsed["participants"][0]["total_damage"] == 100


def test_parse_hit_debug_log_text_caps_final_boss_overkill_damage() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=5000 eHP=200 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:01.000] HP_V2 #2 hit=500 cum=500 raw=500.00 pHP=5000 eHP=0 skill="chr_0004_pelica_attack1" hits=1 src=chr_0004_pelica tgt=eny_0051_rodin atk=chr_0004_pelica seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_hit_debug_log_text(content, file_name="sample.log")

    assert parsed["summary"]["total_damage"] == 300
    by_key = {participant["character_key"]: participant for participant in parsed["participants"]}
    assert by_key["chr_0027_tangtang"]["total_damage"] == 100
    assert by_key["chr_0004_pelica"]["total_damage"] == 200
    final_hit = parsed["hits"][-1]
    assert final_hit["hit_value"] == 200
    assert final_hit["raw_hit_value"] == 500
    assert final_hit["overkill_damage"] == 300


def test_parse_hit_debug_log_text_explains_external_buff_source() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] ATTR_MOD buff="buff_pelica_team_atk" i=0 attrType=2 modType=0 formula=6 useKey=1 val=0.5000 bbKey="atk_up"',
            '[10:00:00.000] BUFF_START #1 id="buff_pelica_team_atk" uid=1 owner=chr_0027_tangtang src=chr_0004_pelica dur=2.00 lifeT=2.00 passed=0.00 enh=1',
            "[10:00:00.000]   BB[2]: atk_up=0.5 =0",
            '[10:00:00.500] HP_V2 #2 hit=150 cum=150 raw=150.00 pHP=5000 eHP=900000 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:00.500] DPD_RAW #2 probe=2 calc=150.0000 atkScale=1.0000 blocked=0 damageType=0x0 decorateMask=0x80 collider="BattleShape" atkZones=[1.0000,1.0000,1.0000,1.0000,1.0000,1.0000] defZones=[1.0000,1.0000,1.0000,1.0000,1.0000,1.0000]',
        ]
    )

    parsed = parse_hit_debug_log_text(content, file_name="sample.log")

    assert parsed["summary"]["hit_count"] == 1
    assert parsed["summary"]["dpd_count"] == 1
    hit = parsed["hits"][0]
    assert hit["seq"] == 2
    assert hit["attacker_share"] == 100.0
    assert hit["external_pool"] == 50.0
    assert hit["external_sources"] == [
        {
            "character_key": "chr_0004_pelica",
            "character_name": "佩丽卡",
            "effect_count": 1,
            "rdps_credit": 50.0,
        }
    ]

    atk_zone = hit["zones"][0]
    assert atk_zone["zone"] == "atk"
    assert atk_zone["external_multiplier"] == 1.5
    assert atk_zone["contributors"][0]["source_character_key"] == "chr_0004_pelica"
    assert atk_zone["contributors"][0]["event_key"] == "buff_pelica_team_atk"


def test_parse_hit_debug_log_text_respects_local_buff_max_stack_for_enemy_vulnerable() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] ATTR_MOD buff="buff_chr_0013_aglina_ultimate_spell_vulnerable" i=0 attrType=85 modType=0 formula=0 useKey=1 val=0.0000 bbKey="rate"',
            '[10:00:00.000] BUFF_START #1 id="buff_chr_0013_aglina_ultimate_spell_vulnerable" uid=1 owner=eny_0051_rodin src=chr_0013_aglina dur=12.00 lifeT=12.00 passed=0.00 enh=1',
            "[10:00:00.000]   BB[1]: rate=0.42",
            '[10:00:05.000] ATTR_MOD buff="buff_chr_0013_aglina_ultimate_spell_vulnerable" i=0 attrType=85 modType=0 formula=0 useKey=1 val=0.0000 bbKey="rate"',
            '[10:00:05.000] BUFF_START #2 id="buff_chr_0013_aglina_ultimate_spell_vulnerable" uid=2 owner=eny_0051_rodin src=chr_0013_aglina dur=12.00 lifeT=12.00 passed=0.00 enh=1',
            "[10:00:05.000]   BB[2]: rate=0.42",
            '[10:00:10.000] ATTR_MOD buff="buff_chr_0013_aglina_ultimate_spell_vulnerable" i=0 attrType=85 modType=0 formula=0 useKey=1 val=0.0000 bbKey="rate"',
            '[10:00:10.000] BUFF_START #3 id="buff_chr_0013_aglina_ultimate_spell_vulnerable" uid=3 owner=eny_0051_rodin src=chr_0013_aglina dur=12.00 lifeT=12.00 passed=0.00 enh=1',
            "[10:00:10.000]   BB[3]: rate=0.42",
            '[10:00:11.000] HP_V2 #4 hit=1000 cum=1000 raw=1000.00 pHP=5000 eHP=899000 skill="chr_0017_yvonne_ult_attack_end" hits=1 src=chr_0017_yvonne tgt=eny_0051_rodin atk=chr_0017_yvonne seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_hit_debug_log_text(content, file_name="nonstacking-enemy-vulnerable.log")
    hit = parsed["hits"][0]
    fragile_zone = next(zone for zone in hit["zones"] if zone["zone"] == "fragile")

    assert fragile_zone["external_rate"] == 0.42
    assert len(fragile_zone["contributors"]) == 1
    assert fragile_zone["contributors"][0]["event_key"] == "buff_chr_0013_aglina_ultimate_spell_vulnerable"
    assert hit["external_sources"] == [
        {
            "character_key": "chr_0013_aglina",
            "character_name": "洁尔佩塔",
            "effect_count": 1,
            "rdps_credit": 295.7746,
        }
    ]


def test_parse_hit_debug_log_text_infers_missing_debuff_end_from_related_skill_cast() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] SKILL_CAST_START seq=1 startMs=1000 inst=field owner=chr_0013_aglina skill=chr_0013_aglina_ultimate_skill_abilityrange',
            '[10:00:00.000] ATTR_MOD buff="buff_chr_0013_aglina_ultimate_spell_vulnerable" i=0 attrType=85 modType=0 formula=0 useKey=1 val=0.0000 bbKey="rate"',
            '[10:00:00.000] BUFF_START #2 id="buff_chr_0013_aglina_ultimate_spell_vulnerable" uid=main owner=eny_0051_rodin src=chr_0013_aglina dur=9999.00 lifeT=9999.00 passed=0.00 enh=1',
            "[10:00:00.000]   BB[main]: rate=0.5 duration=-1",
            '[10:00:05.000] HP_V2 #3 hit=150 cum=150 raw=150.00 pHP=5000 eHP=900000 skill="chr_0017_yvonne_ult_attack1_projhit" hits=1 src=chr_0017_yvonne tgt=eny_0051_rodin atk=chr_0017_yvonne seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:06.000] SKILL_CAST_END seq=4 endMs=6000 inst=field owner=chr_0013_aglina skill=chr_0013_aglina_ultimate_skill_abilityrange',
            '[10:00:07.000] HP_V2 #5 hit=150 cum=300 raw=150.00 pHP=5000 eHP=899850 skill="chr_0017_yvonne_ult_attack1_projhit" hits=2 src=chr_0017_yvonne tgt=eny_0051_rodin atk=chr_0017_yvonne seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_hit_debug_log_text(
        content,
        file_name="related-skill-end.log",
        first_hit_hint="10-00-00-000",
        last_hit_hint="10-00-08-000",
    )

    vuln_row = next(
        row
        for row in parsed["buff_audit"]
        if row["event_key"] == "buff_chr_0013_aglina_ultimate_spell_vulnerable"
        and row["status"] == "included"
    )
    assert vuln_row["effective_duration_ms"] == 6000
    first_hit_fragile = next(zone for zone in parsed["hits"][0]["zones"] if zone["zone"] == "fragile")
    assert first_hit_fragile["contributors"][0]["event_key"] == "buff_chr_0013_aglina_ultimate_spell_vulnerable"
    late_fragile = [zone for zone in parsed["hits"][1]["zones"] if zone["zone"] == "fragile"]
    assert not late_fragile


def test_parse_hit_debug_log_text_keeps_potion_duration_when_refresh_end_is_same_tick() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] ATTR_MOD buff="buff_common_atk_buff_potion_1" i=0 attrType=2 modType=0 formula=6 useKey=1 val=0.1000 bbKey="value"',
            '[10:00:00.000] BUFF_START #1 id="buff_common_atk_buff_potion_1" uid=1 owner=chr_0027_tangtang src=chr_0027_tangtang dur=300.00 lifeT=300.00 passed=0.00 enh=1',
            "[10:00:00.000]   BB[1]: value=0.27",
            '[10:00:00.000] BUFF_END #2 id="buff_common_atk_buff_potion_1" uid=1 owner=chr_0027_tangtang src=chr_0027_tangtang dur=300.00 lifeT=245.71 passed=0.00 enh=1',
            '[10:00:00.001] ATTR_MOD buff="buff_common_atk_buff_potion_1" i=0 attrType=2 modType=0 formula=6 useKey=1 val=0.1000 bbKey="value"',
            '[10:00:00.001] BUFF_START #3 id="buff_common_atk_buff_potion_1" uid=2 owner=chr_0027_tangtang src=chr_0027_tangtang dur=300.00 lifeT=300.00 passed=0.00 enh=1',
            "[10:00:00.001]   BB[1]: value=0.27",
            '[10:00:05.000] HP_V2 #4 hit=127 cum=127 raw=127.00 pHP=5000 eHP=900000 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_hit_debug_log_text(content, file_name="potion-refresh.log")

    potion_rows = [
        row for row in parsed["buff_audit"] if row["event_key"] == "buff_common_atk_buff_potion_1"
    ]
    assert [row["effective_duration_ms"] for row in potion_rows] == [5000, 4999]
    assert [row["status"] for row in potion_rows] == ["included", "merged"]
    hit = parsed["hits"][0]
    atk_zone = next(zone for zone in hit["zones"] if zone["zone"] == "atk")
    potion_contributors = [
        row for row in atk_zone["contributors"] if row["event_key"] == "buff_common_atk_buff_potion_1"
    ]
    assert len(potion_contributors) == 1
    assert potion_contributors[0]["rate"] == 0.27


def test_parse_hit_debug_log_text_filters_effectless_wrapper_blackboard_values() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] BUFF_START #1 id="buff_chr_0011_seraph_spawnball" uid=1 owner=chr_0011_seraph src=chr_0011_seraph dur=2.00 lifeT=2.00 passed=0.00 enh=1',
            "[10:00:00.000]   BB[3]: atk_up=0.18 will_up=0.74 atk_scale=0.1",
            '[10:00:01.000] HP_V2 #2 hit=100 cum=100 raw=100.00 pHP=5000 eHP=900000 skill="chr_0011_seraph_attack1" hits=1 src=chr_0011_seraph tgt=eny_0051_rodin atk=chr_0011_seraph seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_hit_debug_log_text(content, file_name="effectless-wrapper.log")

    row = next(row for row in parsed["buff_audit"] if row["event_key"] == "buff_chr_0011_seraph_spawnball")
    assert row["status"] == "filtered"
    assert "无可识别正数效果" in row["reasons"]
    hit = parsed["hits"][0]
    assert hit["zones"] == []


def test_parse_hit_debug_log_text_exposes_numeric_buff_candidates() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] BUFF_START #1 id="1083" uid=1 owner=chr_0009_azrila src=chr_0009_azrila dur=2.00 lifeT=2.00 passed=0.00 enh=1',
            "[10:00:00.000]   BB[2]: duration=2 rate=0.1",
            '[10:00:00.500] HP_V2 #2 hit=100 cum=100 raw=100.00 pHP=5000 eHP=900000 skill="chr_0009_azrila_attack1" hits=1 src=chr_0009_azrila tgt=eny_0051_rodin atk=chr_0009_azrila seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_hit_debug_log_text(content, file_name="numeric-candidate.log")
    row = parsed["buff_audit"][0]

    assert row["raw_event_key"] == "1083"
    assert row["packet_classification"]["class"] == "unknown_blackboard"
    assert row["semantic_candidates"]
    assert row["semantic_candidates"][0]["candidate_buff_id"].startswith("buff_chr_0009_azrila")


def test_parse_hit_debug_log_text_exposes_actor_map_skill_candidates() -> None:
    content = "\n".join(
        [
            "[10:00:00.000] ACTOR_MAP id=42 template=chr_0016_laevat source=launch_projectile skill=chr_0016_laevat_skill_3998",
            '[10:00:00.200] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=5000 eHP=900000 skill="chr_0016_laevat_skill_3999" hits=1 src=chr_0016_laevat tgt=eny_0051_rodin atk=chr_0016_laevat seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_hit_debug_log_text(content, file_name="actor-map-candidate.log")
    mapping = parsed["hits"][0]["skill_mapping"]

    assert mapping["status"] == "candidate"
    assert mapping["candidates"][0]["candidate_skill_id"] == "chr_0016_laevat_skill_3998"
    assert mapping["candidates"][0]["delta_ms"] == 200


def test_parse_hit_debug_log_text_uses_raw_loadout_skill_ids_for_skill_evidence() -> None:
    content = "\n".join(
        [
            "[10:00:00.000] LOADOUT slot=2 char=chr_0006_wolfgd weaponTemplate=wpn_pistol_0009 weaponLv=1 refine=0 break=0 equipSuit={} equips={} skillIntIds=[170,192]",
            "[10:00:01.000] ACTOR_MAP id=42 template=chr_0006_wolfgd source=launch_projectile skill=chr_0006_wolfgd_skill_192",
            '[10:00:01.120] HP_V2 #1 hit=370 cum=370 raw=370.42 pHP=5000 eHP=900000 skill="chr_0006_wolfgd_skill_192" hits=1 src=chr_0006_wolfgd tgt=eny_0051_rodin atk=chr_0006_wolfgd seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_hit_debug_log_text(content, file_name="raw-loadout-skill-evidence.log")
    mapping = parsed["hits"][0]["skill_mapping"]

    assert parsed["loadout"][0]["skill_int_ids"] == ["170", "192"]
    assert mapping["status"] == "mapped"
    assert mapping["canonical_skill_id"] == "chr_0006_wolfgd_ultimate_skill"
    assert mapping["evidence_kind"] == "canonical_parser_identity"
    assert "confidence" not in mapping
    assert mapping["candidates"] == []


def test_parse_hit_debug_log_text_prefers_num_table_mapping_over_action_graph_candidates() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] HP_V2 #1 hit=1955 cum=1955 raw=1955.00 pHP=5000 eHP=900000 skill="chr_0016_laevat_skill_1955" hits=1 src=chr_0016_laevat tgt=eny_0051_rodin atk=chr_0016_laevat seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_hit_debug_log_text(content, file_name="action-graph-skill-candidate.log")
    hit = parsed["hits"][0]
    mapping = hit["skill_mapping"]

    assert mapping["status"] == "mapped"
    assert mapping["canonical_skill_id"] == "chr_0016_laevat_normal_skill_abilityentity"
    assert mapping["confidence"] == "num_id_str_skill_id"
    assert mapping["action_graph_candidates"] == []
    assert hit["skill_name"] == "焚灭 / 实体"


def test_parse_hit_debug_log_text_prefers_packet_cast_over_runtime_number() -> None:
    content = "\n".join(
        [
            "[10:00:00.000] SKILL_CAST_START seq=10 startMs=1000 inst=9001 owner=chr_0016_laevat skill=chr_0016_laevat_combo_skill",
            '[10:00:00.050] BUFF_START #1 id="3999" uid=1 owner=eny_0051_rodin src=chr_0016_laevat dur=9999.00 lifeT=9999.00 passed=0.00 enh=1',
            '[10:00:00.050] HP_V2 #1 hit=10000 cum=10000 raw=10000.00 pHP=5000 eHP=900000 skill="chr_0016_laevat_skill_3999" hits=1 src=chr_0016_laevat tgt=eny_0051_rodin atk=chr_0016_laevat seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_hit_debug_log_text(content, file_name="packet-cast-skill-evidence.log")
    hit = parsed["hits"][0]
    mapping = hit["skill_mapping"]

    assert mapping["status"] == "mapped"
    assert mapping["canonical_skill_id"] == "chr_0016_laevat_combo_skill"
    assert mapping["confidence"] == "skill_start_cast_packet"
    assert hit["skill_key"] == "chr_0016_laevat_combo_skill"
    assert hit["raw_skill_key"] == "chr_0016_laevat_skill_3999"
    assert hit["skill_group_type"] == 3
    assert hit["skill_name"] == "沸腾"


def test_parse_hit_debug_log_text_prefers_runtime_truth_over_strong_actor_map_candidate() -> None:
    content = "\n".join(
        [
            "[10:00:00.000] ACTOR_MAP id=42 template=chr_0016_laevat source=launch_projectile skill=chr_0016_laevat_attack4",
            '[10:00:00.102] HP_V2 #1 hit=1623 cum=1623 raw=1623.17 pHP=5000 eHP=900000 skill="chr_0016_laevat_skill_900" hits=1 src=chr_0016_laevat tgt=eny_0051_rodin atk=chr_0016_laevat seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_hit_debug_log_text(content, file_name="actor-map-strong-skill-evidence.log")
    hit = parsed["hits"][0]
    mapping = hit["skill_mapping"]

    assert mapping["status"] == "mapped"
    assert mapping["canonical_skill_id"] == "chr_0016_laevat_attack_5_projhit"
    assert mapping["confidence"] == "runtime_truth_global_alias"
    assert hit["skill_key"] == "chr_0016_laevat_attack_5_projhit"
    assert hit["raw_skill_key"] == "chr_0016_laevat_skill_900"
    assert hit["skill_name"] == "A5 派生"


def test_parse_hit_debug_log_text_promotes_same_frame_trigger_buff_damage() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] HP_V2 #1 hit=32937 cum=32937 raw=32937.24 pHP=5000 eHP=900000 skill="chr_0016_laevat_skill_278" hits=1 src=chr_0016_laevat tgt=eny_0051_rodin atk=chr_0016_laevat seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:00.000] BUFF_START #1 id="277" uid=1 owner=eny_0051_rodin src=chr_0016_laevat dur=9999.00 lifeT=9999.00 passed=0.00 enh=1',
        ]
    )

    parsed = parse_hit_debug_log_text(content, file_name="same-frame-trigger-buff.log")
    hit = parsed["hits"][0]
    mapping = hit["skill_mapping"]

    assert mapping["status"] == "mapped"
    assert mapping["canonical_skill_id"] == "buff_common_fire_fire_triggered"
    assert mapping["confidence"] == "num_id_str_buff_id"
    assert hit["skill_key"] == "buff_common_fire_fire_triggered"
    assert hit["raw_skill_key"] == "chr_0016_laevat_skill_278"
    assert hit["skill_name"] == "灼热爆发"


def test_parse_hit_debug_log_text_prefers_enemy_trigger_over_self_marker_same_frame() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] BUFF_START #1 id="1544" uid=1 owner=chr_0005_chen src=chr_0005_chen dur=9999.00 lifeT=9999.00 passed=0.00 enh=1',
            '[10:00:00.000] BUFF_START #2 id="buff_common_cryst_triggered_physical_break" uid=2 owner=eny_0051_rodin src=chr_0005_chen dur=9999.00 lifeT=9999.00 passed=0.00 enh=1',
            '[10:00:00.000] HP_V2 #3 hit=100 cum=100 raw=100.00 pHP=0 eHP=899900 skill="chr_0005_chen_skill_601" hits=1 src=chr_0005_chen tgt=eny_0051_rodin atk=chr_0005_chen seg=0 shared=2 critFlag=0 critDmg=-1.0000',
        ]
    )

    parsed = parse_hit_debug_log_text(content, file_name="prefer-enemy-trigger.log")
    hit = parsed["hits"][0]

    assert hit["skill_key"] == "buff_common_cryst_triggered_physical_break"
    assert hit["skill_name"] == "猛击"
    assert hit["damage_element"] == "physical"


def test_parse_hit_debug_log_text_maps_pograni_trigger_641_to_fracture() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] BUFF_START #1 id="641" uid=1 owner=eny_0051_rodin src=chr_0029_pograni dur=9999.00 lifeT=9999.00 passed=0.00 enh=1',
            '[10:00:00.000] BUFF_START #2 id="buff_common_enemy_spell_status_do_frozen" uid=2 owner=eny_0051_rodin src=chr_0029_pograni dur=9999.00 lifeT=9999.00 passed=0.00 enh=1',
            '[10:00:00.000] BB[2]: duration=12 physical_res_down=0.171912 count=1 atk_scale=1.828',
            '[10:00:00.000] HP_V2 #3 hit=399 cum=399 raw=398.89 pHP=0 eHP=899601 skill="chr_0029_pograni_skill_1211" hits=1 src=chr_0029_pograni tgt=eny_0051_rodin atk=chr_0029_pograni seg=0 shared=3 critFlag=1 critDmg=-1.0000',
        ]
    )

    parsed = parse_hit_debug_log_text(content, file_name="pograni-fracture-trigger.log")
    hit = parsed["hits"][0]

    assert hit["skill_key"] == "buff_common_enemy_spell_status_do_frozen"
    assert hit["skill_name"] == "碎甲"


def test_parse_hit_debug_log_text_uses_canonical_display_name_for_derived_skill() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] HP_V2 #1 hit=123 cum=123 raw=122.66 pHP=5000 eHP=900000 skill="chr_0025_ardelia_remain_loop_sheep" hits=1 src=chr_0025_ardelia tgt=eny_0051_rodin atk=chr_0025_ardelia seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_hit_debug_log_text(content, file_name="canonical-display-name.log")
    hit = parsed["hits"][0]

    assert hit["skill_key"] == "chr_0025_ardelia_remain_loop_sheep"
    assert hit["skill_name"] == "绵羊持续伤害"


def test_parse_hit_debug_log_text_maps_runtime_damage_buff_hit_back_to_origin_skill() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] BUFF_START #1 id="2018" uid=1 owner=eny_0051_rodin src=chr_0028_wulfa dur=9999.00 lifeT=9999.00 passed=0.00 enh=1',
            '[10:00:00.000]   BB[4]: duration=0.3 atk_scale=0.1725 trigger_times=3 damage_interval=0.125',
            '[10:00:00.200] HP_V2 #2 hit=614 cum=614 raw=613.62 pHP=0 eHP=899386 skill="chr_0028_wulfa_skill_2017" hits=1 skillLv=unknown templateIntId=2017 actionId=unknown origTemplateIntId=2257 damageUnitIndex=0 partInstId=unknown dynBB=unknown calcBB=unknown src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa seg=0 shared=2 critFlag=0 critDmg=-1.0000',
        ]
    )

    parsed = parse_hit_debug_log_text(content, file_name="wulfa-combo-2-trigger.log")
    hit = parsed["hits"][0]
    mapping = hit["skill_mapping"]

    assert mapping["status"] == "mapped"
    assert mapping["canonical_skill_id"] == "chr_0028_wulfa_combo_2_skill_projhit"
    assert mapping["confidence"] == "orig_template_trigger_buff_chain"
    assert mapping["origin_skill_id"] == "chr_0028_wulfa_combo_2_skill"
    assert mapping["trigger_buff_id"] == "buff_chr_0028_wulfa_combo_2_damage"
    assert hit["skill_key"] == "chr_0028_wulfa_combo_2_skill_projhit"
    assert hit["skill_name"] == "燎影时刻 / 派生"


def test_parse_hit_debug_log_text_maps_runtime_skill_number_to_damage_buff_name() -> None:
    content = "\n".join(
        [
            '[10:00:00.200] HP_V2 #1 hit=1049 cum=1049 raw=1049.39 pHP=0 eHP=899000 skill="chr_0028_wulfa_skill_2193" hits=1 skillLv=unknown templateIntId=2193 actionId=2 origTemplateIntId=unknown damageUnitIndex=0 partInstId=unknown dynBB=unknown calcBB=unknown src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa seg=0 shared=2 critFlag=0 critDmg=-1.0000',
        ]
    )

    parsed = parse_hit_debug_log_text(content, file_name="wulfa-normal-bleed.log")
    hit = parsed["hits"][0]
    mapping = hit["skill_mapping"]

    assert mapping["status"] == "mapped"
    assert mapping["canonical_skill_id"] == "buff_chr_0028_wulfa_normal_bleed"
    assert mapping["confidence"] == "num_id_str_buff_id"
    assert hit["skill_key"] == "buff_chr_0028_wulfa_normal_bleed"
    assert hit["skill_name"] == "爪印斫痕"


def test_parse_hit_debug_log_text_uses_human_name_for_yvonne_combo_runtime_damage() -> None:
    content = "\n".join(
        [
            '[10:00:00.200] HP_V2 #1 hit=1049 cum=1049 raw=1049.39 pHP=0 eHP=899000 skill="chr_0017_yvonne_skill_162" hits=1 skillLv=unknown templateIntId=162 actionId=14 origTemplateIntId=1187 damageUnitIndex=0 partInstId=unknown dynBB=unknown calcBB=unknown src=chr_0017_yvonne tgt=eny_0051_rodin atk=chr_0017_yvonne seg=0 shared=2 critFlag=0 critDmg=-1.0000',
            '[10:00:00.400] HP_V2 #2 hit=2049 cum=2049 raw=2049.39 pHP=0 eHP=896951 skill="chr_0017_yvonne_skill_163" hits=1 skillLv=unknown templateIntId=163 actionId=9 origTemplateIntId=1187 damageUnitIndex=0 partInstId=unknown dynBB=unknown calcBB=unknown src=chr_0017_yvonne tgt=eny_0051_rodin atk=chr_0017_yvonne seg=0 shared=2 critFlag=0 critDmg=-1.0000',
        ]
    )

    parsed = parse_hit_debug_log_text(content, file_name="yvonne-combo-runtime-damage.log")

    assert parsed["hits"][0]["skill_name"] == "伊冯连携 / 机器人持续伤害"
    assert parsed["hits"][1]["skill_name"] == "伊冯连携 / 机器人终结爆炸"


def test_parse_hit_debug_log_text_ignores_generic_runtime_skill_cast_packets() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] SKILL_CAST_START seq=1 startMs=1000 inst=9001 owner=chr_0028_wulfa skill=skill_1887',
            '[10:00:00.200] HP_V2 #2 hit=1049 cum=1049 raw=1049.39 pHP=0 eHP=899000 skill="chr_0028_wulfa_skill_2193" hits=1 skillLv=unknown templateIntId=2193 actionId=2 origTemplateIntId=unknown damageUnitIndex=0 partInstId=unknown dynBB=unknown calcBB=unknown src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa seg=0 shared=2 critFlag=0 critDmg=-1.0000',
        ]
    )

    parsed = parse_hit_debug_log_text(content, file_name="generic-runtime-cast.log")
    hit = parsed["hits"][0]

    assert hit["skill_key"] == "buff_chr_0028_wulfa_normal_bleed"
    assert hit["skill_name"] == "爪印斫痕"


def test_parse_hit_debug_log_text_does_not_treat_seraph_ultimate_wrapper_as_attack_buff() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] BUFF_START #1 id="buff_chr_0011_seraph_atk_buff" uid=1 owner=chr_0027_tangtang src=chr_0011_seraph dur=12.00 lifeT=12.00 passed=0.00 enh=1',
            "[10:00:00.000]   BB[6]: atk_up=0.242 =0 duration=12 =0 wisd_up=0.000308 =0",
            '[10:00:00.000] ATTR_MOD buff="buff_common_affixes_enhance_crystal" i=0 attrType=67 modType=0 formula=5 useKey=1 val=0.0000 bbKey="rate"',
            '[10:00:00.000] BUFF_START #2 id="buff_common_affixes_enhance_crystal" uid=2 owner=chr_0026_lastrite src=chr_0011_seraph dur=12.00 lifeT=12.00 passed=0.00 enh=1',
            "[10:00:00.000]   BB[3]: rate=0.5 =0 duration=12",
            '[10:00:00.500] HP_V2 #3 hit=150 cum=150 raw=150.00 pHP=5000 eHP=900000 skill="buff_chr_0026_lastrite_normal_skill_phantom_main" hits=1 src=chr_0026_lastrite tgt=eny_0051_rodin atk=chr_0026_lastrite seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:00.500] DPD_RAW #3 probe=3 calc=150.0000 atkScale=1.0000 blocked=0 damageType=0x8 decorateMask=0x80 collider="BattleShape" atkZones=[1.0000,1.0000,1.0000,1.5000,1.0000,1.0000] defZones=[1.0000,1.0000,1.0000,1.0000,1.0000,1.0000]',
        ]
    )

    parsed = parse_hit_debug_log_text(content, file_name="seraph-ultimate-wrapper.log")

    seraph_row = next(
        row for row in parsed["buff_audit"] if row["event_key"] == "buff_chr_0011_seraph_atk_buff"
    )
    assert seraph_row["status"] == "filtered"
    assert seraph_row["effect_summary"] == []
    hit = parsed["hits"][0]
    assert [zone["zone"] for zone in hit["zones"]] == ["amp"]
    assert hit["zones"][0]["contributors"][0]["event_key"] == "buff_common_affixes_enhance_crystal"


def test_parse_hit_debug_log_text_attaches_dpd_bucket_to_damage_zone() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] BUFF_START #1 id="buff_spell_up_demo" uid=1 owner=chr_0027_tangtang src=chr_0004_pelica dur=2.00 lifeT=2.00 passed=0.00 enh=1',
            "[10:00:00.000]   BB[1]: spell_up=0.25",
            '[10:00:00.500] HP_V2 #2 hit=125 cum=125 raw=125.00 pHP=5000 eHP=900000 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:00.500] DPD_RAW #2 probe=2 calc=125.0000 atkScale=1.0000 blocked=0 damageType=0x0 decorateMask=0x80 collider="BattleShape" atkZones=[1.0000,1.2500,1.0000,1.0000,1.0000,1.0000] defZones=[1.0000,1.0000,1.0000,1.0000,1.0000,1.0000]',
        ]
    )

    parsed = parse_hit_debug_log_text(content, file_name="sample.log")
    hit = parsed["hits"][0]

    dmg_zone = hit["zones"][0]
    assert dmg_zone["zone"] == "dmg_inc"
    assert dmg_zone["dpd_bucket"] == {"side": "atk", "index": 1, "value": 1.25}


def test_parse_hit_debug_log_text_filters_attr_type_skill_damage() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] ATTR_MOD buff="buff_demo_normal_skill_up" i=0 attrType=32 modType=0 formula=5 useKey=1 val=0.0000 bbKey="dmg_up"',
            '[10:00:00.000] BUFF_START #1 id="buff_demo_normal_skill_up" uid=1 owner=chr_0027_tangtang src=chr_0004_pelica dur=2.00 lifeT=2.00 passed=0.00 enh=1',
            "[10:00:00.000]   BB[1]: dmg_up=0.5",
            '[10:00:00.500] HP_V2 #2 hit=100 cum=100 raw=100.00 pHP=5000 eHP=900000 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:00.500] DPD_RAW #2 probe=2 calc=100.0000 atkScale=1.0000 blocked=0 damageType=0x0 decorateMask=0x80 collider="BattleShape" atkZones=[1.0000,1.0000,1.0000,1.0000,1.0000,1.0000] defZones=[1.0000,1.0000,1.0000,1.0000,1.0000,1.0000]',
            '[10:00:01.000] HP_V2 #3 hit=150 cum=250 raw=150.00 pHP=5000 eHP=899850 skill="chr_0027_tangtang_normal_skill_water_projhit_1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:01.000] DPD_RAW #3 probe=3 calc=150.0000 atkScale=1.0000 blocked=0 damageType=0x0 decorateMask=0x80 collider="BattleShape" atkZones=[1.0000,1.5000,1.0000,1.0000,1.0000,1.0000] defZones=[1.0000,1.0000,1.0000,1.0000,1.0000,1.0000]',
        ]
    )

    parsed = parse_hit_debug_log_text(content, file_name="attr-type-filter.log")
    first, second = parsed["hits"]

    assert first["zones"] == []
    assert second["zones"][0]["zone"] == "dmg_inc"
    assert second["zones"][0]["external_rate"] == 0.5
    assert second["external_sources"][0]["character_key"] == "chr_0004_pelica"


def test_parse_hit_debug_log_text_classifies_resistance_decrease_as_vulnerability() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] DMG_MOD buff="buff_common_pulse_pulse_conduct_triggered_do" d=0 p=0 enableSide=1 class="DamageScaleProcessor" side=1 zone="NormalCalcZone" useKey=1 val=0.0000 bbKey="final_spell_resistance_decrease"',
            '[10:00:00.000] BUFF_START #1 id="buff_common_pulse_pulse_conduct_triggered_do" uid=1 owner=eny_0051_rodin src=chr_0004_pelica dur=2.00 lifeT=2.00 passed=0.00 enh=1',
            "[10:00:00.000]   BB[3]: spell_resistance_decrease=0.1816 duration=2 final_spell_resistance_decrease=0.2415",
            '[10:00:00.500] HP_V2 #2 hit=124 cum=124 raw=124.15 pHP=5000 eHP=900000 skill="chr_0027_tangtang_normal_skill" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:00.500] DPD_RAW #2 probe=2 calc=124.1500 atkScale=1.0000 blocked=0 damageType=0x0 decorateMask=0x80 collider="BattleShape" atkZones=[1.0000,1.0000,1.0000,1.0000,1.0000,1.0000] defZones=[1.0000,1.2415,1.0000,1.0000,1.0000,1.0000]',
        ]
    )

    parsed = parse_hit_debug_log_text(content, file_name="pulse-conduct.log")
    hit = parsed["hits"][0]

    assert len(hit["zones"]) == 1
    zone = hit["zones"][0]
    assert zone["zone"] == "vuln_taken"
    assert zone["zone_label"] == "易伤"
    assert zone["external_rate"] == 0.2415
    assert zone["dpd_bucket"] == {"side": "def", "index": 1, "value": 1.2415}
    conduct_row = next(row for row in parsed["buff_audit"] if row["event_key"] == "buff_common_pulse_pulse_conduct_triggered_do")
    assert conduct_row["event_name"].startswith("导电")


def test_parse_hit_debug_log_text_classifies_cryst_dmg_up2_as_damage_increase() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] ATTR_MOD buff="buff_wpn_pistol_0011_valid" i=0 attrType=53 modType=0 formula=5 useKey=1 val=0.0000 bbKey="cryst_dmg_up2"',
            '[10:00:00.000] BUFF_START #1 id="buff_wpn_pistol_0011_valid" uid=1 owner=chr_0027_tangtang src=chr_0027_tangtang dur=2.00 lifeT=2.00 passed=0.00 enh=1',
            "[10:00:00.000]   BB[2]: cryst_dmg_up2=0.32 duration=2",
            '[10:00:00.500] HP_V2 #2 hit=132 cum=132 raw=132.00 pHP=5000 eHP=900000 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:00.500] DPD_RAW #2 probe=2 calc=132.0000 atkScale=1.0000 blocked=0 damageType=0x0 decorateMask=0x80 collider="BattleShape" atkZones=[1.0000,1.3200,1.0000,1.0000,1.0000,1.0000] defZones=[1.0000,1.0000,1.0000,1.0000,1.0000,1.0000]',
        ]
    )

    parsed = parse_hit_debug_log_text(content, file_name="pistol-dmg-up.log")
    hit = parsed["hits"][0]

    assert len(hit["zones"]) == 1
    zone = hit["zones"][0]
    assert zone["zone"] == "dmg_inc"
    assert zone["zone_label"] == "增伤"
    assert zone["self_rate"] == 0.32
    assert zone["dpd_bucket"] == {"side": "atk", "index": 1, "value": 1.32}
    assert zone["contributors"][0]["event_key"] == "buff_wpn_pistol_0011_valid"
    assert zone["contributors"][0]["event_name"] == "增伤"


def test_parse_hit_debug_log_text_uses_dpd_damage_type_as_element_fallback() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] BUFF_START #1 id="buff_fire_taken_probe" uid=1 owner=eny_0051_rodin src=chr_0004_pelica dur=2.00 lifeT=2.00 passed=0.00 enh=1',
            "[10:00:00.000]   BB[1]: damage_taken_up_fire=0.2 duration=2",
            '[10:00:00.500] HP_V2 #2 hit=120 cum=120 raw=120.00 pHP=5000 eHP=900000 skill="unknown_proc_damage" hits=1 src=chr_9999_dummy tgt=eny_0051_rodin atk=chr_9999_dummy seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:00.500] DPD_RAW #2 probe=2 calc=120.0000 atkScale=1.0000 blocked=0 damageType=0x2 decorateMask=0x80 collider="BattleShape" atkZones=[1.0000,1.0000,1.0000,1.0000,1.0000,1.0000] defZones=[1.0000,1.2000,1.0000,1.0000,1.0000,1.0000]',
        ]
    )

    parsed = parse_hit_debug_log_text(content, file_name="dpd-damage-type-fallback.log")
    hit = parsed["hits"][0]

    assert hit["damage_element"] == "fire"
    vuln_zone = next(zone for zone in hit["zones"] if zone["zone"] == "vuln_taken")
    assert vuln_zone["external_rate"] == 0.2
    assert vuln_zone["contributors"][0]["event_key"] == "buff_fire_taken_probe"


def test_parse_hit_debug_log_text_spell_vuln_does_not_apply_when_official_element_is_unresolved() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] BUFF_START #1 id="buff_wpn_funnel_0008_magic_damage_taken_up" uid=1 owner=eny_0051_rodin src=chr_0023_antal dur=15.00 lifeT=15.00 passed=0.00 enh=1',
            "[10:00:00.000]   BB[3]: spell_damage_taken_up=0.198 duration=15 lv=7",
            '[10:00:00.500] HP_V2 #2 hit=123 cum=123 raw=122.66 pHP=5000 eHP=900000 skill="chr_0025_ardelia_remain_loop_sheep" hits=1 src=chr_0025_ardelia tgt=eny_0051_rodin atk=chr_0025_ardelia seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:00.500] PKT_MOD #2 atk=[] def=[1]',
        ]
    )

    parsed = parse_hit_debug_log_text(content, file_name="spell-vuln-school-filter.log")
    hit = parsed["hits"][0]

    assert hit["damage_element"] is None
    assert hit["damage_school"] == "physical"
    assert hit["zones"] == []


def test_parse_hit_debug_log_text_filters_external_buffs_by_packet_modifier_uid() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] BUFF_START #1 id="buff_pelica_spell_vuln" uid=1 owner=eny_0051_rodin src=chr_0004_pelica dur=2.00 lifeT=2.00 passed=0.00 enh=1',
            "[10:00:00.000]   BB[1]: spell_damage_taken_up=0.2 duration=2",
            '[10:00:00.000] BUFF_START #2 id="buff_antal_spell_vuln" uid=2 owner=eny_0051_rodin src=chr_0023_antal dur=2.00 lifeT=2.00 passed=0.00 enh=1',
            "[10:00:00.000]   BB[2]: spell_damage_taken_up=0.2 duration=2",
            '[10:00:00.500] HP_V2 #3 hit=144 cum=144 raw=144.00 pHP=5000 eHP=900000 skill="chr_0028_wulfa_ultimate_skill" hits=1 skillLv=12 templateIntId=2397 actionId=177 origTemplateIntId=2397 damageUnitIndex=0 src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:00.500] PKT_MOD #3 atk=[] def=[1]',
        ]
    )

    parsed = parse_hit_debug_log_text(content, file_name="packet-uid-filter.log")
    hit = parsed["hits"][0]

    assert hit["external_sources"] == [
        {
            "character_key": "chr_0004_pelica",
            "character_name": "佩丽卡",
            "effect_count": 1,
            "rdps_credit": 24.0,
        }
    ]
    assert hit["attacker_share"] == 120.0
    vuln_zone = next(zone for zone in hit["zones"] if zone["zone"] == "vuln_taken")
    assert [row["source_character_key"] for row in vuln_zone["contributors"] if row["scope"] == "external"] == [
        "chr_0004_pelica"
    ]
    ignored = [row for row in hit["ignored_effects"] if row.get("reason") == "packet_defender_uid_suppressed"]
    assert ignored
    assert ignored[0]["uid"] == "2"
    assert ignored[0]["reason_group"] == "packet_modifier_uid_mismatch"
    assert ignored[0]["packet_modifier_guard"] == "defender_uid_selection"
    assert ignored[0]["packet_modifier_uids"]["defender"] == ["1"]
    assert ignored[0]["candidate_uids"] == ["2"]


def test_parse_hit_debug_log_text_keeps_distinct_packet_vuln_effect_keys() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] DMG_MOD buff="buff_common_pulse_pulse_conduct_triggered_do" d=0 p=0 enableSide=1 class="DamageScaleProcessor" side=1 zone="NormalCalcZone" useKey=1 val=0.0000 bbKey="final_spell_resistance_decrease"',
            '[10:00:00.000] BUFF_START #1 id="buff_common_pulse_pulse_conduct_triggered_do" uid=conduct owner=eny_0051_rodin src=chr_0004_pelica dur=2.00 lifeT=2.00 passed=0.00 enh=1',
            "[10:00:00.000]   BB[conduct]: spell_resistance_decrease=0.12 duration=2 final_spell_resistance_decrease=0.12",
            '[10:00:00.000] BUFF_START #2 id="buff_wpn_pistol_0011_valid2" uid=tangtang owner=eny_0051_rodin src=chr_0027_tangtang dur=2.00 lifeT=2.00 passed=0.00 enh=1',
            "[10:00:00.000]   BB[tangtang]: spell_damage_taken_up=0.096 duration2=20",
            '[10:00:00.500] HP_V2 #3 hit=144 cum=144 raw=144.00 pHP=5000 eHP=900000 skill="chr_0028_wulfa_ultimate_skill" hits=1 skillLv=12 templateIntId=2397 actionId=177 origTemplateIntId=2397 damageUnitIndex=0 src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:00.500] PKT_MOD #3 atk=[] def=[tangtang]',
        ]
    )

    parsed = parse_hit_debug_log_text(content, file_name="packet-uid-distinct-keys.log")
    hit = parsed["hits"][0]

    vuln_zone = next(zone for zone in hit["zones"] if zone["zone"] == "vuln_taken")
    external_keys = {
        contributor["event_key"]
        for contributor in vuln_zone["contributors"]
        if contributor["scope"] == "external"
    }
    assert external_keys == {
        "buff_common_pulse_pulse_conduct_triggered_do",
        "buff_wpn_pistol_0011_valid2",
    }
    assert not [
        row
        for row in hit["ignored_effects"]
        if row.get("event_key") == "buff_common_pulse_pulse_conduct_triggered_do"
        and row.get("reason") == "packet_defender_uid_suppressed"
    ]


def test_parse_hit_debug_log_text_physical_fragile_applies_to_physical_school_fire_hit() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] BUFF_START #1 id="buff_chr_0025_ardelia_affixes_vulnerable_physic_child" uid=1 owner=eny_0051_rodin src=chr_0025_ardelia dur=15.00 lifeT=15.00 passed=0.00 enh=1',
            "[10:00:00.000]   BB[1]: rate=0.28",
            '[10:00:00.500] HP_V2 #2 hit=370 cum=370 raw=370.42 pHP=5000 eHP=900000 skill="chr_0006_wolfgd_attack2_projhit" hits=1 src=chr_0006_wolfgd tgt=eny_0051_rodin atk=chr_0006_wolfgd seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_hit_debug_log_text(content, file_name="physical-fragile-school-filter.log")
    hit = parsed["hits"][0]

    assert hit["damage_element"] == "fire"
    assert hit["damage_school"] == "physical"
    assert hit["zones"][0]["zone"] == "fragile"
    assert hit["zones"][0]["contributors"][0]["event_key"] == "buff_chr_0025_ardelia_affixes_vulnerable_physic_child"


def test_parse_hit_debug_log_text_uses_canonical_packet_hint_for_antal_fragile() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] BUFF_START #1 id="buff_chr_0023_antal_normal_fragile" uid=1 owner=eny_0051_rodin src=chr_0023_antal dur=25.00 lifeT=25.00 passed=0.00 enh=1',
            "[10:00:00.000]   BB[3]: rate=0.10 potential_5_rate=0.04 delay_time=20",
            '[10:00:05.000] HP_V2 #2 hit=100 cum=100 raw=100.00 pHP=5000 eHP=900000 skill="chr_0006_wolfgd_attack2_projhit" hits=1 src=chr_0006_wolfgd tgt=eny_0051_rodin atk=chr_0006_wolfgd seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:25.000] HP_V2 #3 hit=100 cum=100 raw=100.00 pHP=5000 eHP=899900 skill="chr_0006_wolfgd_attack2_projhit" hits=1 src=chr_0006_wolfgd tgt=eny_0051_rodin atk=chr_0006_wolfgd seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_hit_debug_log_text(content, file_name="antal-fragile-canonical.log")
    early, late = parsed["hits"]

    assert early["zones"][0]["zone"] == "fragile"
    assert early["zones"][0]["contributors"][0]["rate"] == 0.1
    assert late["zones"][0]["contributors"][0]["rate"] == 0.14


def test_parse_hit_debug_log_text_does_not_allocate_speedup_or_slow_into_rdps() -> None:
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

    parsed = parse_hit_debug_log_text(content, file_name="speedup-slow-hit-debug.log")
    hit = parsed["hits"][0]
    contribs = {item["character_key"]: item["value"] for item in hit["rdps_contributions"]}

    assert "chr_0027_tangtang" not in contribs
    assert abs(sum(contribs.values()) - 1224.0) < 0.001
    assert any(item["reason"] == "utility_not_allocated" for item in hit["ignored_effects"])


def test_parse_hit_debug_log_text_dedupes_ardelia_parent_and_child_fragile() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] BUFF_START #1 id="buff_chr_0025_ardelia_normal_skill_fragile" uid=1 owner=eny_0051_rodin src=chr_0025_ardelia dur=15.00 lifeT=15.00 passed=0.00 enh=1',
            "[10:00:00.000]   BB[1]: rate=0.28",
            '[10:00:00.000] BUFF_START #2 id="buff_chr_0025_ardelia_affixes_vulnerable_physic_child" uid=2 owner=eny_0051_rodin src=chr_0025_ardelia dur=15.00 lifeT=15.00 passed=0.00 enh=1',
            "[10:00:00.000]   BB[1]: rate=0.28",
            '[10:00:00.000] BUFF_START #3 id="buff_chr_0025_ardelia_affixes_vulnerable_spell_child" uid=3 owner=eny_0051_rodin src=chr_0025_ardelia dur=15.00 lifeT=15.00 passed=0.00 enh=1',
            "[10:00:00.000]   BB[1]: rate=0.28",
            '[10:00:00.500] HP_V2 #4 hit=370 cum=370 raw=370.42 pHP=5000 eHP=900000 skill="chr_0006_wolfgd_attack2_projhit" hits=1 src=chr_0006_wolfgd tgt=eny_0051_rodin atk=chr_0006_wolfgd seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_hit_debug_log_text(content, file_name="ardelia-parent-child-dedupe.log")
    hit = parsed["hits"][0]

    fragile_zone = hit["zones"][0]
    assert fragile_zone["zone"] == "fragile"
    assert [row["event_key"] for row in fragile_zone["contributors"]] == [
        "buff_chr_0025_ardelia_affixes_vulnerable_physic_child"
    ]


def test_parse_hit_debug_log_text_exposes_normal_attack_up_and_dpd_self_residual() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] ATTR_MOD buff="buff_wpn_sword_0006_valid" i=0 attrType=17 modType=0 formula=5 useKey=1 val=0.0000 bbKey="normal_atk_up_valid"',
            '[10:00:00.000] BUFF_START #1 id="buff_wpn_sword_0006_valid" uid=1 owner=chr_0016_laevat src=chr_0016_laevat dur=20.00 lifeT=20.00 passed=0.00 enh=1',
            "[10:00:00.000]   BB[2]: normal_atk_up_valid=1.2 =0",
            '[10:00:00.500] HP_V2 #2 hit=150 cum=150 raw=150.00 pHP=5000 eHP=900000 skill="chr_0016_laevat_ult_attack2" hits=1 src=chr_0016_laevat tgt=eny_0051_rodin atk=chr_0016_laevat seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:00.500] DPD_RAW #2 probe=2 calc=150.0000 atkScale=1.0000 blocked=0 damageType=0x2 decorateMask=0x80 collider="BattleShape" atkZones=[1.0000,2.5000,1.0000,1.0000,1.0000,1.0000] defZones=[1.0000,1.0000,1.0000,1.0000,1.0000,1.0000]',
        ]
    )

    parsed = parse_hit_debug_log_text(content, file_name="laevat-normal-up.log")
    hit = parsed["hits"][0]

    zone = hit["zones"][0]
    assert zone["zone"] == "dmg_inc"
    assert zone["self_rate"] == 1.5
    assert zone["dpd_bucket"] == {"side": "atk", "index": 1, "value": 2.5}
    assert [
        contributor["event_key"]
        for contributor in zone["contributors"]
    ] == ["buff_wpn_sword_0006_valid", "__dpd_self_residual__"]
    assert zone["contributors"][0]["rate"] == 1.2
    assert zone["contributors"][1]["event_name"] == "自身基线/未归因（DPD残差）"
    assert zone["contributors"][1]["rate"] == 0.3


def test_parse_hit_debug_log_text_reuses_baseline_for_followup_hits() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=5000 eHP=900000 skill="chr_0016_laevat_attack1" hits=1 src=chr_0016_laevat tgt=eny_0051_rodin atk=chr_0016_laevat seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:00.000] DPD_RAW #1 probe=1 calc=100.0000 atkScale=1.0000 blocked=0 damageType=0x2 decorateMask=0x80 collider="BattleShape" atkZones=[1.0000,1.2900,1.0000,1.0000,1.0000,1.0000] defZones=[1.0000,1.0000,1.0000,1.0000,1.0000,1.0000]',
            "[10:00:00.000] BASELINE #1 2=7437.2449 51=0.2900",
            '[10:00:00.500] HP_V2 #2 hit=100 cum=200 raw=100.00 pHP=5000 eHP=900000 skill="chr_0016_laevat_attack2" hits=2 src=chr_0016_laevat tgt=eny_0051_rodin atk=chr_0016_laevat seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:00.500] DPD_RAW #2 probe=2 calc=100.0000 atkScale=1.0000 blocked=0 damageType=0x2 decorateMask=0x80 collider="BattleShape" atkZones=[1.0000,1.2900,1.0000,1.0000,1.0000,1.0000] defZones=[1.0000,1.0000,1.0000,1.0000,1.0000,1.0000]',
        ]
    )

    parsed = parse_hit_debug_log_text(content, file_name="baseline-cache.log")
    second = parsed["hits"][1]

    assert second["baseline"] == {2: 7437.2449, 51: 0.29}
    zones = {zone["zone"]: zone for zone in second["zones"]}
    assert "atk" not in zones
    assert zones["dmg_inc"]["self_rate"] == 0.29
    assert zones["dmg_inc"]["contributors"][0]["event_key"] == "__baseline_attr_51__"


def test_parse_hit_debug_log_text_enriches_embedded_loadout_affixes() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=5000 eHP=900000 skill="chr_0016_laevat_attack1" hits=1 src=chr_0016_laevat tgt=eny_0051_rodin atk=chr_0016_laevat seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "## ENDFIELD_RAW_LOG_INTEGRITY_BEGIN sep=0",
            "{",
            '  "meta": {',
            '    "loadout": [',
            "      {",
            '        "slot": 0,',
            '        "char_key": "chr_0016_laevat",',
            '        "char_name": "莱万汀",',
            '        "potential": 0,',
            '        "weapon_template": "wpn_sword_0006",',
            '        "weapon_level": 90,',
            '        "weapon_refine": 0,',
            '        "equips": [',
            "          {",
            '            "slot": 0,',
            '            "item_id": "item_equip_t4_suit_fire_natr01_hand_02",',
            '            "enhance_levels": [0, 0, 3],',
            '            "enhance_failed_times": -42040624',
            "          },",
            "          {",
            '            "slot": 1,',
            '            "item_id": "3",',
            '            "enhance_failed_times": -42242784',
            "          }",
            "        ]",
            "      }",
            "    ]",
            "  }",
            "}",
            "## ENDFIELD_RAW_LOG_INTEGRITY_END",
        ]
    )

    parsed = parse_hit_debug_log_text(content, file_name="loadout-affixes.log")

    loadout = parsed["loadout"][0]
    assert loadout["weapon_base_atk"] == 510
    assert len(loadout["equips"]) == 1
    equip = loadout["equips"][0]
    assert equip["enhance_failed_times"] is None
    assert equip["piece_name"] == "动火用手甲"
    fire_attr = next(attr for attr in equip["affixes"] if attr["desc"] == "灼热和自然伤害提升")
    assert fire_attr["level"] == 3
    assert fire_attr["selected_value"] == 0.249167


def test_parse_hit_debug_log_text_ignores_antal_ultimate_icon_rate() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] ATTR_MOD buff="buff_chr_0023_antal_ultimate_enhance" i=0 attrType=65 modType=0 formula=5 useKey=1 val=0.0000 bbKey="rate"',
            '[10:00:00.000] BUFF_START #1 id="buff_chr_0023_antal_ultimate_enhance" uid=1 owner=chr_0016_laevat src=chr_0023_antal dur=12.00 lifeT=12.00 passed=0.00 enh=1',
            "[10:00:00.000] BB[1]: rate=0.22 duration=12",
            '[10:00:00.000] BUFF_START #2 id="buff_chr_0023_antal_ultimate_icon" uid=2 owner=chr_0016_laevat src=chr_0016_laevat dur=12.00 lifeT=12.00 passed=0.00 enh=1',
            "[10:00:00.000] BB[2]: rate=0.22 duration=12",
            '[10:00:00.500] HP_V2 #3 hit=1000 cum=1000 raw=1000.00 pHP=0 eHP=900000 skill="chr_0016_laevat_ult_attack1" hits=1 src=chr_0016_laevat tgt=eny_0051_rodin atk=chr_0016_laevat seg=0 shared=2 critFlag=0 critDmg=-1.0000',
        ]
    )

    parsed = parse_hit_debug_log_text(content, file_name="antal-ultimate-icon.log")
    hit = parsed["hits"][0]
    amp_zone = next(zone for zone in hit["zones"] if zone["zone"] == "amp")

    assert amp_zone["total_multiplier"] == 1.22
    assert [row["event_key"] for row in amp_zone["contributors"]] == [
        "buff_chr_0023_antal_ultimate_enhance"
    ]
    icon_record = next(row for row in parsed["buff_audit"] if row["event_key"] == "buff_chr_0023_antal_ultimate_icon")
    assert icon_record["status"] == "filtered"


def test_parse_hit_debug_log_text_treats_nonstacking_weapon_buff_as_refresh() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] ATTR_MOD buff="buff_wpn_funnel_0010_atk_up" i=0 attrType=2 modType=0 formula=6 useKey=1 val=0.0000 bbKey="atk_up"',
            '[10:00:00.000] BUFF_START #1 id="buff_wpn_funnel_0010_atk_up" uid=1 owner=chr_0011_seraph src=chr_0011_seraph dur=15.00 lifeT=15.00 passed=0.00 enh=1',
            "[10:00:00.000]   BB[1]: atk_up=0.18",
            '[10:00:00.001] ATTR_MOD buff="buff_wpn_funnel_0010_atk_up" i=0 attrType=2 modType=0 formula=6 useKey=1 val=0.0000 bbKey="atk_up"',
            '[10:00:00.001] BUFF_START #2 id="buff_wpn_funnel_0010_atk_up" uid=2 owner=chr_0026_lastrite src=chr_0011_seraph dur=15.00 lifeT=15.00 passed=0.00 enh=1',
            "[10:00:00.001]   BB[1]: atk_up=0.18",
            '[10:00:05.000] ATTR_MOD buff="buff_wpn_funnel_0010_atk_up" i=0 attrType=2 modType=0 formula=6 useKey=1 val=0.0000 bbKey="atk_up"',
            '[10:00:05.000] BUFF_START #3 id="buff_wpn_funnel_0010_atk_up" uid=3 owner=chr_0011_seraph src=chr_0011_seraph dur=15.00 lifeT=15.00 passed=0.00 enh=1',
            "[10:00:05.000]   BB[1]: atk_up=0.18",
            '[10:00:05.001] ATTR_MOD buff="buff_wpn_funnel_0010_atk_up" i=0 attrType=2 modType=0 formula=6 useKey=1 val=0.0000 bbKey="atk_up"',
            '[10:00:05.001] BUFF_START #4 id="buff_wpn_funnel_0010_atk_up" uid=4 owner=chr_0026_lastrite src=chr_0011_seraph dur=15.00 lifeT=15.00 passed=0.00 enh=1',
            "[10:00:05.001]   BB[1]: atk_up=0.18",
            '[10:00:05.100] HP_V2 #10 hit=1000 cum=1000 raw=1000.00 pHP=100 eHP=10000 skill="chr_0026_lastrite_attack1" hits=1 src=chr_0026_lastrite tgt=eny_0051_rodin atk=chr_0026_lastrite seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_hit_debug_log_text(content, file_name="nonstacking-weapon-refresh.log")
    hit = parsed["hits"][0]
    atk_zone = next(zone for zone in hit["zones"] if zone["zone"] == "atk")
    weapon_rows = [
        row
        for row in atk_zone["contributors"]
        if row["event_key"] == "buff_wpn_funnel_0010_atk_up"
        and row["target_character_key"] == "chr_0026_lastrite"
    ]

    assert atk_zone["external_rate"] == 0.18
    assert len(weapon_rows) == 1
    assert weapon_rows[0]["uid"] == "2"


def test_parse_hit_debug_log_text_does_not_double_count_seraph_heal_attack_wrapper() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] BUFF_START #1 id="buff_chr_0011_seraph_mainchr_heal" uid=10 owner=chr_0027_tangtang src=chr_0011_seraph ownerId=1001 srcId=2001 dur=2.00 lifeT=2.00 passed=0.00 enh=1',
            "[10:00:00.000]   BB[4]: atk_scale=0.1 heal_value=316.8 potential_1=0 atk_up=0.18",
            '[10:00:00.240] HP_V2 #2 hit=811 cum=811 raw=811.29 pHP=6721 eHP=6885 skill="buff_chr_0011_seraph_mainchr_heal" hits=1 src=chr_0011_seraph tgt=chr_0027_tangtang atk=? seg=-1 shared=2 critFlag=0 critDmg=-1.0000',
            '[10:00:00.240] ATTR_MOD buff="buff_wpn_funnel_0010_atk_up" i=0 attrType=2 modType=0 formula=6 useKey=1 val=0.0000 bbKey="atk_up"',
            '[10:00:00.240] BUFF_START #3 id="buff_wpn_funnel_0010_atk_up" uid=20 owner=chr_0027_tangtang src=chr_0011_seraph ownerId=1001 srcId=2001 dur=15.00 lifeT=15.00 passed=0.00 enh=1',
            "[10:00:00.240]   BB[2]: atk_up=0.18 duration=15",
            '[10:00:00.240] ATTR_MOD buff="buff_wpn_funnel_0010_atk_up" i=0 attrType=2 modType=0 formula=6 useKey=1 val=0.0000 bbKey="atk_up"',
            '[10:00:00.240] BUFF_START #30 id="buff_wpn_funnel_0010_atk_up" uid=30 owner=chr_0011_seraph src=chr_0011_seraph ownerId=2001 srcId=2001 dur=15.00 lifeT=15.00 passed=0.00 enh=1',
            "[10:00:00.240]   BB[2]: atk_up=0.18 duration=15",
            '[10:00:00.240] BUFF_START #4 id="buff_chr_0011_seraph_potential_1_atkup" uid=21 owner=chr_0027_tangtang src=chr_0011_seraph ownerId=1001 srcId=2001 dur=25.00 lifeT=25.00 passed=0.00 enh=1',
            "[10:00:00.240]   BB[1]: atk_up=0.18",
            '[10:00:00.350] BUFF_ENABLE {"schemaVersion":1,"buffUid":"10","ownerId":"1001","enabled":true,"operationSeq":1}',
            '[10:00:00.350] BUFF_ENABLE {"schemaVersion":1,"buffUid":"20","ownerId":"1001","enabled":true,"operationSeq":2}',
            '[10:00:00.350] BUFF_ENABLE {"schemaVersion":1,"buffUid":"30","ownerId":"2001","enabled":true,"operationSeq":3}',
            '[10:00:00.350] BUFF_ENABLE {"schemaVersion":1,"buffUid":"21","ownerId":"1001","enabled":true,"operationSeq":4}',
            '[10:00:01.000] HP_V2 #5 hit=1180 cum=1180 raw=1180.00 pHP=100 eHP=10000 skill="chr_0027_tangtang_attack1_projhit" actionId=0 damageUnitIndex=0 hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang atkId=1001 tgtId=3001 seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_hit_debug_log_text(content, file_name="seraph-heal-atk-wrapper.log")
    hit = parsed["hits"][-1]
    atk_zone = next(zone for zone in hit["zones"] if zone["zone"] == "atk")
    seraph_attack_rows = [
        row for row in atk_zone["contributors"] if row["source_character_key"] == "chr_0011_seraph"
    ]
    wrapper_row = next(
        row
        for row in parsed["buff_audit"]
        if row["event_key"] == "buff_chr_0011_seraph_potential_1_atkup"
    )

    assert atk_zone["external_rate"] == 0.18
    assert [row["event_key"] for row in seraph_attack_rows] == ["buff_wpn_funnel_0010_atk_up"]
    assert wrapper_row["status"] == "filtered"
    assert wrapper_row["effect_summary"] == []


def test_parse_hit_debug_log_text_caps_weapon_buff_stack_limit() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] ATTR_MOD buff="buff_wpn_sword_0012_atk_up" i=0 attrType=2 modType=0 formula=6 useKey=1 val=0.0000 bbKey="atk_up2"',
            '[10:00:00.000] BUFF_START #1 id="buff_wpn_sword_0012_atk_up" uid=1 owner=chr_0016_laevat src=chr_0019_karin ownerId=1001 srcId=2001 dur=20.00 lifeT=20.00 passed=0.00 enh=1',
            "[10:00:00.000]   BB[4]: atk_up2=0.09 duration=20 max_stack=2",
            '[10:00:00.001] BUFF_START #101 id="buff_wpn_sword_0012_atk_up" uid=101 owner=chr_0019_karin src=chr_0019_karin ownerId=2001 srcId=2001 dur=20.00 lifeT=20.00 passed=0.00 enh=1',
            '[10:00:00.001] BB[101]: atk_up2=0.09 duration=20 max_stack=2',
            '[10:00:00.100] ATTR_MOD buff="buff_wpn_sword_0012_atk_up" i=0 attrType=2 modType=0 formula=6 useKey=1 val=0.0000 bbKey="atk_up2"',
            '[10:00:00.100] BUFF_START #2 id="buff_wpn_sword_0012_atk_up" uid=2 owner=chr_0016_laevat src=chr_0019_karin ownerId=1001 srcId=2001 dur=20.00 lifeT=20.00 passed=0.00 enh=1',
            "[10:00:00.100]   BB[4]: atk_up2=0.09 duration=20 max_stack=2",
            '[10:00:00.101] BUFF_START #102 id="buff_wpn_sword_0012_atk_up" uid=102 owner=chr_0019_karin src=chr_0019_karin ownerId=2001 srcId=2001 dur=20.00 lifeT=20.00 passed=0.00 enh=1',
            '[10:00:00.101] BB[102]: atk_up2=0.09 duration=20 max_stack=2',
            '[10:00:00.200] ATTR_MOD buff="buff_wpn_sword_0012_atk_up" i=0 attrType=2 modType=0 formula=6 useKey=1 val=0.0000 bbKey="atk_up2"',
            '[10:00:00.200] BUFF_START #3 id="buff_wpn_sword_0012_atk_up" uid=3 owner=chr_0016_laevat src=chr_0019_karin ownerId=1001 srcId=2001 dur=20.00 lifeT=20.00 passed=0.00 enh=1',
            "[10:00:00.200]   BB[4]: atk_up2=0.09 duration=20 max_stack=2",
            '[10:00:00.201] BUFF_START #103 id="buff_wpn_sword_0012_atk_up" uid=103 owner=chr_0019_karin src=chr_0019_karin ownerId=2001 srcId=2001 dur=20.00 lifeT=20.00 passed=0.00 enh=1',
            '[10:00:00.201] BB[103]: atk_up2=0.09 duration=20 max_stack=2',
            '[10:00:00.300] ATTR_MOD buff="buff_wpn_sword_0012_atk_up" i=0 attrType=2 modType=0 formula=6 useKey=1 val=0.0000 bbKey="atk_up2"',
            '[10:00:00.300] BUFF_START #4 id="buff_wpn_sword_0012_atk_up" uid=4 owner=chr_0016_laevat src=chr_0019_karin ownerId=1001 srcId=2001 dur=20.00 lifeT=20.00 passed=0.00 enh=1',
            "[10:00:00.300]   BB[4]: atk_up2=0.09 duration=20 max_stack=2",
            '[10:00:00.301] BUFF_START #104 id="buff_wpn_sword_0012_atk_up" uid=104 owner=chr_0019_karin src=chr_0019_karin ownerId=2001 srcId=2001 dur=20.00 lifeT=20.00 passed=0.00 enh=1',
            '[10:00:00.301] BB[104]: atk_up2=0.09 duration=20 max_stack=2',
            '[10:00:00.350] BUFF_ENABLE {"schemaVersion":1,"buffUid":"1","ownerId":"1001","enabled":false,"operationSeq":1}',
            '[10:00:00.350] BUFF_ENABLE {"schemaVersion":1,"buffUid":"2","ownerId":"1001","enabled":false,"operationSeq":2}',
            '[10:00:00.350] BUFF_ENABLE {"schemaVersion":1,"buffUid":"3","ownerId":"1001","enabled":true,"operationSeq":3}',
            '[10:00:00.350] BUFF_ENABLE {"schemaVersion":1,"buffUid":"4","ownerId":"1001","enabled":true,"operationSeq":4}',
            '[10:00:00.350] BUFF_ENABLE {"schemaVersion":1,"buffUid":"101","ownerId":"2001","enabled":false,"operationSeq":5}',
            '[10:00:00.350] BUFF_ENABLE {"schemaVersion":1,"buffUid":"102","ownerId":"2001","enabled":false,"operationSeq":6}',
            '[10:00:00.350] BUFF_ENABLE {"schemaVersion":1,"buffUid":"103","ownerId":"2001","enabled":true,"operationSeq":7}',
            '[10:00:00.350] BUFF_ENABLE {"schemaVersion":1,"buffUid":"104","ownerId":"2001","enabled":true,"operationSeq":8}',
            '[10:00:00.400] HP_V2 #10 hit=1180 cum=1180 raw=1180.00 pHP=100 eHP=10000 skill="chr_0016_laevat_attack1" hits=1 src=chr_0016_laevat tgt=eny_0051_rodin atk=chr_0016_laevat atkId=1001 tgtId=3001 seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_hit_debug_log_text(content, file_name="weapon-stack-cap.log")
    hit = parsed["hits"][0]
    atk_zone = next(zone for zone in hit["zones"] if zone["zone"] == "atk")
    weapon_rows = [
        row
        for row in atk_zone["contributors"]
        if row["event_key"] == "buff_wpn_sword_0012_atk_up"
    ]

    assert atk_zone["external_rate"] == 0.18
    # The observed enable state selects these instances; UID order is not a FIFO proof.
    assert [row["uid"] for row in weapon_rows] == ["3", "4"]
    inactive = {row["uid"]: row for row in parsed["buff_audit"] if row["uid"] in {"1", "2"}}
    assert set(inactive) == {"1", "2"}
    for uid, row in inactive.items():
        assert row["enable_observations"][-1]["enabled"] is False
        assert row["raw_end_ts_ms"] is None
        assert row["raw_duration_ms"] == 20000
    assert inactive["1"]["start_ts_ms"] == 36000000
    assert inactive["2"]["start_ts_ms"] == 36000100


def test_parse_hit_debug_log_text_drops_default_child_when_parent_has_effects() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] ATTR_MOD buff="buff_common_affixes_enhance_spell" i=0 attrType=65 modType=0 formula=5 useKey=1 val=0.0000 bbKey="rate"',
            '[10:00:00.000] ATTR_MOD buff="buff_common_affixes_enhance_spell" i=1 attrType=66 modType=0 formula=5 useKey=1 val=0.0000 bbKey="rate"',
            '[10:00:00.000] ATTR_MOD buff="buff_common_affixes_enhance_spell" i=2 attrType=67 modType=0 formula=5 useKey=1 val=0.0000 bbKey="rate"',
            '[10:00:00.000] ATTR_MOD buff="buff_common_affixes_enhance_spell" i=3 attrType=68 modType=0 formula=5 useKey=1 val=0.0000 bbKey="rate"',
            '[10:00:00.000] BUFF_START #1 id="buff_common_affixes_enhance_spell" uid=1 owner=chr_0026_lastrite src=chr_0011_seraph dur=25.00 lifeT=25.00 passed=0.00 enh=1',
            "[10:00:00.000]   BB[3]: rate=0.18 =0 duration=25",
            '[10:00:00.000] BUFF_START #2 id="buff_common_affixes_enhance_spell_default_child" uid=2 owner=chr_0026_lastrite src=chr_0011_seraph dur=25.00 lifeT=25.00 passed=0.00 enh=1',
            "[10:00:00.000]   BB[2]: rate=0.18 =0",
            '[10:00:00.500] HP_V2 #3 hit=1000 cum=1000 raw=1000.00 pHP=5000 eHP=900000 skill="buff_chr_0026_lastrite_normal_skill_phantom_main" hits=1 src=chr_0026_lastrite tgt=eny_0051_rodin atk=chr_0026_lastrite seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_hit_debug_log_text(content, file_name="default-child-parent.log")
    hit = parsed["hits"][0]
    amp_zone = next(zone for zone in hit["zones"] if zone["zone"] == "amp")
    amp_events = [row["event_key"] for row in amp_zone["contributors"]]

    assert amp_zone["external_rate"] == 0.18
    assert amp_events == ["buff_common_affixes_enhance_spell"]


def test_parse_hit_debug_log_text_ignores_wrapper_buff_with_forwarded_effect_key() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] BUFF_START #1 id="buff_chr_0016_laevat_energy_icon_5" uid=10 owner=chr_0016_laevat src=chr_0016_laevat dur=9999.00 lifeT=9999.00 passed=0.00 enh=1',
            "[10:00:00.000]   BB[3]: duration=0 =0 ignore_fire_resist=0.2",
            '[10:00:00.001] DMG_MOD buff="buff_chr_0016_laevat_ignore_fire_resist" d=0 p=0 enableSide=0 class="InstantModifyAttribute" side=1 zone="" useKey=0 val=0.0000 bbKey=""',
            '[10:00:00.001] BUFF_START #2 id="buff_chr_0016_laevat_ignore_fire_resist" uid=11 owner=chr_0016_laevat src=chr_0016_laevat dur=20.00 lifeT=20.00 passed=0.00 enh=1',
            "[10:00:00.001]   BB[2]: ignore_fire_resist=0.2 =0",
            '[10:00:00.500] HP_V2 #3 hit=100 cum=100 raw=100.00 pHP=5000 eHP=900000 skill="chr_0016_laevat_ult_attack4" hits=1 src=chr_0016_laevat tgt=eny_0051_rodin atk=chr_0016_laevat seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:00.500] DPD_RAW #3 probe=3 calc=100.0000 atkScale=1.0000 blocked=0 damageType=0x2 decorateMask=0x80 collider="BattleShape" atkZones=[1.0000,1.0000,1.0000,1.0000,1.0000,1.0000] defZones=[1.0000,1.0000,1.0000,1.0000,1.0000,1.0000]',
        ]
    )

    parsed = parse_hit_debug_log_text(content, file_name="wrapper-forwarded-key.log")
    hit = parsed["hits"][0]

    res_zone = next(zone for zone in hit["zones"] if zone["zone"] == "res")
    assert res_zone["self_rate"] == 0.2
    assert [
        contributor["event_key"]
        for contributor in res_zone["contributors"]
    ] == ["buff_chr_0016_laevat_ignore_fire_resist"]


def test_parse_hit_debug_log_text_classifies_corrosion_def_decrease_as_res() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] ATTR_MOD buff="buff_common_natural_cryst_triggered" i=0 attrType=82 modType=0 formula=5 useKey=1 val=0.0000 bbKey="def_decrease"',
            '[10:00:00.000] BUFF_START #1 id="buff_common_natural_cryst_triggered" uid=1 owner=eny_0051_rodin src=chr_0013_aglina dur=15.00 lifeT=15.00 passed=0.00 enh=1',
            "[10:00:00.000]   BB[4]: def_decrease=0.08129 =0 max_def_decrease=0.271 =0 def_decrease_tick=0.01897 =0 duration=15",
            '[10:00:10.000] HP_V2 #2 hit=150 cum=150 raw=150.00 pHP=5000 eHP=900000 skill="buff_chr_0026_lastrite_normal_skill_phantom_main" hits=1 src=chr_0026_lastrite tgt=eny_0051_rodin atk=chr_0026_lastrite seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:10.000] DPD_RAW #2 probe=2 calc=150.0000 atkScale=1.0000 blocked=0 damageType=0x0 decorateMask=0x80 collider="BattleShape" atkZones=[1.0000,1.0000,1.0000,1.0000,1.0000,1.0000] defZones=[1.0000,1.0000,1.0000,1.0000,1.0000,1.0000]',
        ]
    )

    parsed = parse_hit_debug_log_text(content, file_name="corrosion-def-decrease.log")
    hit = parsed["hits"][0]

    zones = {zone["zone"]: zone for zone in hit["zones"]}
    assert "fragile" not in zones
    assert zones["res"]["external_rate"] == 0.271
    assert zones["res"]["contributors"][0]["event_key"] == "buff_common_natural_cryst_triggered"


def test_parse_hit_debug_log_text_keeps_corrupt_do_def_decrease_as_res() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] ATTR_MOD buff="buff_common_natural_natural_corrupt_do" i=0 attrType=84 modType=0 formula=5 useKey=1 val=0.0000 bbKey="def_decrease"',
            '[10:00:00.000] BUFF_START #1 id="buff_common_natural_natural_corrupt_do" uid=1 owner=eny_0051_rodin src=chr_0025_ardelia dur=7.00 lifeT=7.00 passed=0.00 enh=1',
            "[10:00:00.000]   BB[5]: def_decrease=0.036 max_def_decrease=0.12 duration=7 def_decrease_tick=0.0084 additional_def_decrease=0",
            '[10:00:01.000] HP_V2 #2 hit=150 cum=150 raw=150.00 pHP=5000 eHP=900000 skill="chr_0016_laevat_ult_attack1" hits=1 src=chr_0016_laevat tgt=eny_0051_rodin atk=chr_0016_laevat seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:01.000] DPD_RAW #2 probe=2 calc=150.0000 atkScale=1.0000 blocked=0 damageType=0x2 decorateMask=0x80 collider="BattleShape" atkZones=[1.0000,1.0000,1.0000,1.0000,1.0000,1.0000] defZones=[1.0000,1.0000,1.0000,1.0000,1.0000,1.0000]',
        ]
    )

    parsed = parse_hit_debug_log_text(content, file_name="corrupt-do-def-decrease.log")
    hit = parsed["hits"][0]

    zones = {zone["zone"]: zone for zone in hit["zones"]}
    assert "fragile" not in zones
    assert zones["res"]["external_rate"] == 0.0654
    assert zones["res"]["contributors"][0]["event_key"] == "buff_common_natural_natural_corrupt_do"
    corrupt_row = next(row for row in parsed["buff_audit"] if row["event_key"] == "buff_common_natural_natural_corrupt_do")
    assert corrupt_row["event_name"].startswith("腐蚀")


def test_parse_hit_debug_log_text_gates_check_hp_damage_scale() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] BUFF_START #1 id="buff_chr_0005_chen_potential_1" uid=1 owner=chr_0005_chen src=chr_0005_chen dur=10.00 lifeT=10.00 passed=0.00 enh=1',
            "[10:00:00.000] BB[1]: hp_remain=0.5 extra_dmg=0.2",
            '[10:00:00.100] HP_V2 #2 hit=100 cum=100 raw=100.00 pHP=5000 eHP=900 skill="chr_0005_chen_attack1" hits=1 src=chr_0005_chen tgt=eny_0051_rodin atk=chr_0005_chen seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:00.200] HP_V2 #3 hit=500 cum=600 raw=500.00 pHP=5000 eHP=400 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:00.300] HP_V2 #4 hit=100 cum=700 raw=100.00 pHP=5000 eHP=300 skill="chr_0005_chen_attack1" hits=2 src=chr_0005_chen tgt=eny_0051_rodin atk=chr_0005_chen seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    parsed = parse_hit_debug_log_text(content, file_name="check-hp-condition.log", _allow_history=False)
    chen_hits = [hit for hit in parsed["hits"] if hit["character_key"] == "chr_0005_chen"]

    assert chen_hits[0]["enemy_hp_before"] == 1000
    assert chen_hits[0]["ignored_effects"][0]["reason"] == "target_hp_above_threshold"
    assert chen_hits[1]["enemy_hp_before"] == 400
    zones = {zone["zone"]: zone for zone in chen_hits[1]["zones"]}
    assert zones["dmg_inc"]["self_rate"] == 0.2

    check_hp_row = next(row for row in parsed["buff_audit"] if row["event_key"] == "buff_chr_0005_chen_potential_1")
    assert check_hp_row["effect_summary"] == ["增伤 20.00% (target HP <= 50.00%)"]


def test_parse_hit_debug_log_text_gates_check_damage_type_damage_scale() -> None:
    content = "\n".join(
        [
            '[10:00:00.000] BUFF_START #1 id="buff_chr_0011_seraph_talent_1_crystup" uid=1 owner=eny_0051_rodin src=chr_0011_seraph dur=2.00 lifeT=2.00 passed=0.00 enh=1',
            "[10:00:00.000] BB[1]: duration=2 cryst_up=0.3",
            '[10:00:00.100] HP_V2 #2 hit=100 cum=100 raw=100.00 pHP=5000 eHP=900 skill="unknown_proc_damage" hits=1 src=chr_9999_dummy tgt=eny_0051_rodin atk=chr_9999_dummy seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:00.100] DPD_RAW #2 probe=2 calc=100.0000 atkScale=1.0000 blocked=0 damageType=0x2 decorateMask=0x80 collider="BattleShape" atkZones=[1.0000,1.0000,1.0000,1.0000,1.0000,1.0000] defZones=[1.0000,1.0000,1.0000,1.0000,1.0000,1.0000]',
            '[10:00:00.200] HP_V2 #3 hit=130 cum=230 raw=130.00 pHP=5000 eHP=770 skill="unknown_proc_damage" hits=2 src=chr_9999_dummy tgt=eny_0051_rodin atk=chr_9999_dummy seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:00.200] DPD_RAW #3 probe=3 calc=130.0000 atkScale=1.0000 blocked=0 damageType=0x4 decorateMask=0x80 collider="BattleShape" atkZones=[1.0000,1.0000,1.0000,1.0000,1.0000,1.0000] defZones=[1.0000,1.3000,1.0000,1.0000,1.0000,1.0000]',
        ]
    )

    parsed = parse_hit_debug_log_text(content, file_name="check-damage-type-condition.log", _allow_history=False)

    assert parsed["hits"][0]["damage_element"] == "fire"
    assert parsed["hits"][0]["ignored_effects"][0]["condition"]["source"] == "CheckDamageType"
    assert parsed["hits"][1]["damage_element"] == "cryst"
    zones = {zone["zone"]: zone for zone in parsed["hits"][1]["zones"]}
    assert zones["vuln_taken"]["external_rate"] == 0.3

    check_type_row = next(row for row in parsed["buff_audit"] if row["event_key"] == "buff_chr_0011_seraph_talent_1_crystup")
    assert check_type_row["effect_summary"] == ["易伤/cryst 30.00% (damage type: cryst)"]
