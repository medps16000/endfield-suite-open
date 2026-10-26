from __future__ import annotations

from pathlib import Path

from parser_core.unified import (
    parse_hit_viewer_log_text,
    parse_overlay_battle_snapshot_text,
    parse_upload_battle_log_text,
    rdps_totals_from_hit_debug,
    rdps_totals_from_raw_report,
)
from parser_core.audit_viewer import build_audit_viewer_html
from parser_core.damage_core import (
    effect_applies_to_damage_element,
    infer_damage_element,
    infer_damage_school,
)
from parser_core.live import LiveOverlayBattleParser
from parser_core import battle_log_parser as blp


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def test_unified_rdps_totals_match_between_viewer_and_uploader_logs() -> None:
    # The former absolute-path historical logs are unavailable. This stored
    # synthetic fixture exercises refresh, same-time ordering and toggles;
    # it does not claim coverage of those missing historical battles.
    text = Path(__file__).with_name('fixtures').joinpath('rdps_snapshot_contract.log').read_text(encoding='utf-8')
    expected = {'chr_0004_pelica': 640, 'chr_0035_liino': 120}
    assert rdps_totals_from_hit_debug(parse_hit_viewer_log_text(text)) == expected
    assert rdps_totals_from_raw_report(parse_upload_battle_log_text(text)) == expected


def test_hit_viewer_credits_usp02_damageup_alias() -> None:
    text = "\n".join(
        [
            "[10:00:00.000] LOADOUT reason=SC_SELF_SCENE_INFO slotCount=2 memberCount=2 roster=[1 2]",
            "[10:00:00.000] LOADOUT slot=0 char=chr_0016_laevat weaponTemplate=wpn_sword_0006 equipSuit={}",
            "[10:00:00.000] LOADOUT slot=1 char=chr_0025_ardelia weaponTemplate=wpn_funnel_0013 equipSuit={[suit_usp02]=3}",
            '[10:00:01.000] BUFF_START #1 id="buff_equipsuit_usp_02_dmgup" uid=1 owner=chr_0016_laevat src=chr_0025_ardelia dur=15.00 lifeT=15.00 passed=0.00 enh=1',
            "[10:00:01.000] BB[1]: hp_up=1000 dmg_up=0.16 duration=15",
            '[10:00:02.000] HP_V2 #1 hit=1160 cum=1160 raw=1160.00 pHP=0 eHP=900000 skill="chr_0016_laevat_attack1" hits=1 src=chr_0016_laevat tgt=eny_0051_rodin atk=chr_0016_laevat seg=0 shared=2 critFlag=0 critDmg=-1.0000',
        ]
    )

    report = parse_hit_viewer_log_text(text, file_name="usp02-dmgup-alias.log")
    hit = report["hits"][0]

    assert any(
        window["event_key"] == "buff_equipsuit_usp_02_AddAttack"
        for window in report["buff_windows"]
    )
    dmg_inc_zone = next(zone for zone in hit["zones"] if zone["zone"] == "dmg_inc")
    assert any(
        contributor["source_character_key"] == "chr_0025_ardelia"
        and contributor["event_key"] == "buff_equipsuit_usp_02_AddAttack"
        and contributor["rate"] == 0.16
        for contributor in dmg_inc_zone["contributors"]
    )


def test_hit_viewer_extends_conduct_window_from_packet_modifier_uid() -> None:
    text = "\n".join(
        [
            "[10:00:00.000] DUNGEON_CONTEXT dungeonId=dung01_bossrush01_04 source=SC_SELF_SCENE_INFO",
            "[10:00:00.000] GAME_TIMER_START seq=1 source=PacketBattleState startMs=0 expireMs=0 official=1",
            "[10:00:00.000] SQUAD size=2 members=[chr_0028_wulfa_1001 chr_0004_pelica_1002]",
            '[10:00:00.000] DMG_MOD buff="buff_common_pulse_pulse_conduct_triggered_do" d=0 p=0 enableSide=1 class="DamageScaleProcessor" side=1 zone="NormalCalcZone" useKey=1 val=0.0000 bbKey="final_spell_resistance_decrease"',
            '[10:00:00.000] BUFF_START #1 id="buff_common_pulse_pulse_conduct_triggered_do" uid=1 owner=eny_0051_rodin src=chr_0004_pelica dur=9999.00 lifeT=9999.00 passed=0.00 enh=1',
            "[10:00:00.000] BB[1]: spell_resistance_decrease=0.12 duration=2 final_spell_resistance_decrease=0.12",
            '[10:00:02.500] HP_V2 #2 hit=112 cum=112 raw=112.00 pHP=5000 eHP=899888 skill="chr_0028_wulfa_ultimate_skill" hits=1 skillLv=12 templateIntId=2397 actionId=177 origTemplateIntId=2397 damageUnitIndex=0 src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa atkId=1001 tgtId=2000 seg=0 shared=2 critFlag=0 critDmg=-1.0000',
            "[10:00:02.500] PKT_MOD #2 atk=[] def=[other]",
            '[10:00:03.000] HP_V2 #3 hit=112 cum=224 raw=112.00 pHP=5000 eHP=899776 skill="chr_0028_wulfa_ultimate_skill" hits=1 skillLv=12 templateIntId=2397 actionId=177 origTemplateIntId=2397 damageUnitIndex=0 src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa atkId=1001 tgtId=2000 seg=0 shared=2 critFlag=0 critDmg=-1.0000',
            "[10:00:03.000] PKT_MOD #3 atk=[] def=[1]",
            "[10:00:04.000] GAME_TIMER_END seq=3 source=ChallengeComplete self=0 msg=0 elapsedMs=4000 startMs=0 expireMs=0 forceLeaveTs=0 sane=1",
        ]
    )

    report = parse_hit_viewer_log_text(text, file_name="conduct-packet-mod-extension.log")
    mismatched_hit, hit = report["hits"]
    conduct_zone = next(zone for zone in hit["zones"] if zone["zone"] == "vuln_taken")
    conduct_row = next(
        contributor
        for contributor in conduct_zone["contributors"]
        if contributor["event_key"] == "buff_common_pulse_pulse_conduct_triggered_do"
    )

    assert all(zone["zone"] != "vuln_taken" for zone in mismatched_hit["zones"])
    assert hit["damage_school"] == "spell"
    assert hit["damage_element"] == "fire"
    assert conduct_row["source_character_key"] == "chr_0004_pelica"
    assert conduct_row["rdps_credit"] == 12.0


def test_hit_viewer_keeps_attacker_buffs_when_packet_modifier_list_is_partial() -> None:
    text = "\n".join(
        [
            "[10:00:00.000] LOADOUT reason=SC_SELF_SCENE_INFO slotCount=2 memberCount=2 roster=[1 2]",
            "[10:00:00.000] LOADOUT slot=0 char=chr_0028_wulfa weaponTemplate=wpn_sword_0006 equipSuit={}",
            "[10:00:00.000] LOADOUT slot=1 char=chr_0013_aglina weaponTemplate=wpn_funnel_0005 equipSuit={[suit_usp02]=3}",
            "[10:00:00.000] DUNGEON_CONTEXT dungeonId=dung01_bossrush01_04 source=SC_SELF_SCENE_INFO",
            "[10:00:00.000] GAME_TIMER_START seq=1 source=PacketBattleState startMs=0 expireMs=0 official=1",
            "[10:00:00.000] SQUAD size=2 members=[chr_0028_wulfa_1001 chr_0013_aglina_1002]",
            '[10:00:00.000] BUFF_START #1 id="buff_equipsuit_usp_02_dmgup" uid=1 owner=chr_0028_wulfa src=chr_0013_aglina dur=20.00 lifeT=20.00 passed=0.00 enh=1',
            "[10:00:00.000] BB[1]: hp_up=1000 dmg_up=0.16 duration=20",
            '[10:00:01.000] BUFF_START #2 id="buff_self_marker" uid=2 owner=chr_0028_wulfa src=chr_0028_wulfa dur=20.00 lifeT=20.00 passed=0.00 enh=1',
            '[10:00:02.000] HP_V2 #3 hit=1160 cum=1160 raw=1160.00 pHP=5000 eHP=898840 skill="chr_0028_wulfa_ultimate_skill" hits=1 skillLv=12 templateIntId=2397 actionId=177 origTemplateIntId=2397 damageUnitIndex=0 src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa atkId=1001 tgtId=2000 seg=0 shared=2 critFlag=0 critDmg=-1.0000',
            "[10:00:02.000] PKT_MOD #3 atk=[2] def=[]",
            "[10:00:03.000] GAME_TIMER_END seq=3 source=ChallengeComplete self=0 msg=0 elapsedMs=3000 startMs=0 expireMs=0 forceLeaveTs=0 sane=1",
        ]
    )

    report = parse_hit_viewer_log_text(text, file_name="partial-attacker-packet-mod.log")
    hit = report["hits"][0]
    dmg_inc_zone = next(zone for zone in hit["zones"] if zone["zone"] == "dmg_inc")

    assert any(
        contributor["source_character_key"] == "chr_0013_aglina"
        and contributor["event_key"] == "buff_equipsuit_usp_02_AddAttack"
        for contributor in dmg_inc_zone["contributors"]
    )


def test_weapon_buff_source_is_item_carrier_not_target() -> None:
    # Synthetic item-source case includes explicit instance and enable evidence.
    text = "\n".join(
        [
            "[10:00:00.000] LOADOUT reason=SC_SELF_SCENE_INFO slotCount=2 memberCount=2 roster=[1 2]",
            "[10:00:00.000] LOADOUT slot=0 char=chr_0028_wulfa weaponTemplate=wpn_sword_0006 equipSuit={}",
            "[10:00:00.000] LOADOUT slot=1 char=chr_0013_aglina weaponTemplate=wpn_funnel_0005 equipSuit={}",
            '[10:00:00.000] BUFF_START #1 id="buff_wpn_funnel_0005_atk_up" uid=1 owner=chr_0028_wulfa ownerId=1001 src=chr_0013_aglina srcId=1002 dur=20.00 lifeT=20.00 passed=0.00 enh=1',
            "[10:00:00.000] BB[1]: atk_up=0.224 duration=20",
            '[10:00:00.000] BUFF_ENABLE {"schemaVersion":1,"buffUid":"1","ownerId":"1001","enabled":true,"operationSeq":1}',
            '[10:00:01.000] HP_V2 #2 hit=1224 cum=1224 raw=1224.00 pHP=5000 eHP=898776 skill="chr_0028_wulfa_ultimate_skill" hits=1 skillLv=12 templateIntId=2397 actionId=176 origTemplateIntId=2397 damageUnitIndex=0 src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa atkId=1001 tgtId=2000 seg=0 shared=2 critFlag=0 critDmg=-1.0000',
        ]
    )

    report = parse_hit_viewer_log_text(text, file_name="weapon-carrier-source.log")
    hit = report["hits"][0]
    atk_zone = next(zone for zone in hit["zones"] if zone["zone"] == "atk")

    assert any(
        contributor["source_character_key"] == "chr_0013_aglina"
        and contributor["target_character_key"] == "chr_0028_wulfa"
        and contributor["event_key"] == "buff_wpn_funnel_0005_atk_up"
        for contributor in atk_zone["contributors"]
    )
    assert all(
        contributor["source_character_key"] != "chr_0028_wulfa"
        for contributor in atk_zone["contributors"]
    )


def test_weapon_buff_requires_source_active_weapon_not_target_weapon() -> None:
    text = "\n".join(
        [
            "[10:00:00.000] LOADOUT reason=SC_SELF_SCENE_INFO slotCount=2 memberCount=2 roster=[1 2]",
            "[10:00:00.000] LOADOUT slot=0 char=chr_0028_wulfa weaponTemplate=wpn_funnel_0005 equipSuit={}",
            "[10:00:00.000] LOADOUT slot=1 char=chr_0013_aglina weaponTemplate=wpn_sword_0006 equipSuit={}",
            '[10:00:00.000] BUFF_START #1 id="buff_wpn_funnel_0005_atk_up" uid=1 owner=chr_0028_wulfa src=chr_0013_aglina dur=20.00 lifeT=20.00 passed=0.00 enh=1',
            "[10:00:00.000] BB[1]: atk_up=0.224 duration=20",
            '[10:00:01.000] HP_V2 #2 hit=1224 cum=1224 raw=1224.00 pHP=5000 eHP=898776 skill="chr_0028_wulfa_ultimate_skill" hits=1 skillLv=12 templateIntId=2397 actionId=176 origTemplateIntId=2397 damageUnitIndex=0 src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa atkId=1001 tgtId=2000 seg=0 shared=2 critFlag=0 critDmg=-1.0000',
        ]
    )

    report = parse_hit_viewer_log_text(text, file_name="weapon-source-guard.log")
    hit = report["hits"][0]

    assert all(zone["zone"] != "atk" for zone in hit["zones"])
    assert all(
        window["event_key"] != "buff_wpn_funnel_0005_atk_up"
        for window in report["buff_windows"]
    )


def test_raw_parser_uses_mechanism_registry_when_packet_buff_map_lacks_item_effect(monkeypatch) -> None:
    # Synthetic registry fallback keeps activation evidence independent of mapping.
    monkeypatch.setattr(blp, "_load_packet_numeric_buff_map", lambda: {})
    monkeypatch.setattr(blp, "_load_packet_canonical_buff_map", lambda: {})

    text = "\n".join(
        [
            "[10:00:00.000] LOADOUT reason=SC_SELF_SCENE_INFO slotCount=2 memberCount=2 roster=[1 2]",
            "[10:00:00.000] LOADOUT slot=0 char=chr_0028_wulfa weaponTemplate=wpn_sword_0006 equipSuit={}",
            "[10:00:00.000] LOADOUT slot=1 char=chr_0013_aglina weaponTemplate=wpn_funnel_0005 equipSuit={}",
            '[10:00:00.000] BUFF_START #1 id="1645" uid=1 owner=chr_0028_wulfa ownerId=1001 src=chr_0013_aglina srcId=1002 dur=20.00 lifeT=20.00 passed=0.00 enh=1',
            "[10:00:00.000] BB[1]: atk_up=0.224 duration=20",
            '[10:00:00.000] BUFF_ENABLE {"schemaVersion":1,"buffUid":"1","ownerId":"1001","enabled":true,"operationSeq":1}',
            '[10:00:01.000] HP_V2 #2 hit=1224 cum=1224 raw=1224.00 pHP=5000 eHP=898776 skill="chr_0028_wulfa_ultimate_skill" hits=1 skillLv=12 templateIntId=2397 actionId=176 origTemplateIntId=2397 damageUnitIndex=0 src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa atkId=1001 tgtId=2000 seg=0 shared=2 critFlag=0 critDmg=-1.0000',
        ]
    )

    report = blp.parse_raw_battle_log_text(
        text,
        file_name="registry-fallback-weapon.log",
        include_rdps_debug=True,
    )
    hit = report["debug_hits"][0]
    atk_zone = next(zone for zone in hit["zones"] if zone["zone"] == "atk")

    assert any(
        contributor["source_character_key"] == "chr_0013_aglina"
        and contributor["event_key"] == "buff_wpn_funnel_0005_atk_up"
        and contributor["rate"] == 0.224
        for contributor in atk_zone["contributors"]
    )


def test_mechanism_registry_fallback_requires_source_item_guard(monkeypatch) -> None:
    monkeypatch.setattr(blp, "_load_packet_numeric_buff_map", lambda: {})
    monkeypatch.setattr(blp, "_load_packet_canonical_buff_map", lambda: {})

    text = "\n".join(
        [
            "[10:00:00.000] LOADOUT reason=SC_SELF_SCENE_INFO slotCount=2 memberCount=2 roster=[1 2]",
            "[10:00:00.000] LOADOUT slot=0 char=chr_0028_wulfa weaponTemplate=wpn_funnel_0005 equipSuit={}",
            "[10:00:00.000] LOADOUT slot=1 char=chr_0013_aglina weaponTemplate=wpn_sword_0006 equipSuit={}",
            '[10:00:00.000] BUFF_START #1 id="1645" uid=1 owner=chr_0028_wulfa src=chr_0013_aglina dur=20.00 lifeT=20.00 passed=0.00 enh=1',
            "[10:00:00.000] BB[1]: atk_up=0.224 duration=20",
            '[10:00:01.000] HP_V2 #2 hit=1224 cum=1224 raw=1224.00 pHP=5000 eHP=898776 skill="chr_0028_wulfa_ultimate_skill" hits=1 skillLv=12 templateIntId=2397 actionId=176 origTemplateIntId=2397 damageUnitIndex=0 src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa atkId=1001 tgtId=2000 seg=0 shared=2 critFlag=0 critDmg=-1.0000',
        ]
    )

    report = blp.parse_raw_battle_log_text(
        text,
        file_name="registry-fallback-guard.log",
        include_rdps_debug=True,
    )
    hit = report["debug_hits"][0]

    assert all(zone["zone"] != "atk" for zone in hit["zones"])


def test_hit_viewer_keeps_enemy_fragile_when_packet_modifier_def_list_is_partial() -> None:
    text = "\n".join(
        [
            "[10:00:00.000] DUNGEON_CONTEXT dungeonId=dung01_bossrush01_04 source=SC_SELF_SCENE_INFO",
            "[10:00:00.000] GAME_TIMER_START seq=1 source=PacketBattleState startMs=0 expireMs=0 official=1",
            "[10:00:00.000] SQUAD size=2 members=[chr_0028_wulfa_1001 chr_0013_aglina_1002]",
            '[10:00:00.000] ATTR_MOD buff="buff_chr_0013_aglina_ultimate_spell_vulnerable" i=0 attrType=85 modType=0 formula=0 useKey=1 val=0.0000 bbKey="rate"',
            '[10:00:00.000] BUFF_START #1 id="buff_chr_0013_aglina_ultimate_spell_vulnerable" uid=1 owner=eny_0051_rodin src=chr_0013_aglina dur=9999.00 lifeT=9999.00 passed=0.00 enh=1',
            "[10:00:00.000] BB[1]: rate=0.33 duration=-1",
            '[10:00:02.000] HP_V2 #2 hit=1330 cum=1330 raw=1330.00 pHP=5000 eHP=898670 skill="chr_0028_wulfa_ultimate_skill" hits=1 skillLv=12 templateIntId=2397 actionId=177 origTemplateIntId=2397 damageUnitIndex=0 src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa atkId=1001 tgtId=2000 seg=0 shared=2 critFlag=0 critDmg=-1.0000',
            "[10:00:02.000] PKT_MOD #2 atk=[] def=[other_debuff]",
            "[10:00:03.000] GAME_TIMER_END seq=3 source=ChallengeComplete self=0 msg=0 elapsedMs=3000 startMs=0 expireMs=0 forceLeaveTs=0 sane=1",
        ]
    )

    report = parse_hit_viewer_log_text(text, file_name="partial-def-packet-fragile.log")
    hit = report["hits"][0]
    fragile_zone = next(zone for zone in hit["zones"] if zone["zone"] == "fragile")

    assert any(
        contributor["source_character_key"] == "chr_0013_aglina"
        and contributor["event_key"] == "buff_chr_0013_aglina_ultimate_spell_vulnerable"
        for contributor in fragile_zone["contributors"]
    )


def test_hit_viewer_does_not_merge_raw_debug_hits_by_index_when_seq_mismatch(monkeypatch) -> None:
    text = "\n".join(
        [
            '[10:00:00.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=0 eHP=900000 skill="chr_0004_pelica_attack1_projhit" hits=1 src=chr_0004_pelica tgt=eny_0051_rodin atk=chr_0004_pelica seg=0 shared=2 critFlag=0 critDmg=-1.0000',
        ]
    )

    def fake_raw_report(*args, **kwargs):
        return {
            "debug_hits": [
                {
                    "seq": 161,
                    "damage_school": "physical",
                    "zones": [
                        {
                            "zone": "vuln_taken",
                            "contributors": [
                                {
                                    "event_key": "fake_misaligned_buff",
                                    "event_name": "错位覆盖",
                                }
                            ],
                        }
                    ],
                }
            ]
        }

    monkeypatch.setattr("parser_core.unified.parse_raw_battle_log_text", fake_raw_report)

    report = parse_hit_viewer_log_text(text, file_name="seq-mismatch.log")
    hit = report["hits"][0]

    assert hit["seq"] == 1
    assert hit["damage_school"] == "spell"
    assert all(
        contributor.get("event_key") != "fake_misaligned_buff"
        for zone in hit.get("zones") or []
        for contributor in zone.get("contributors") or []
    )


def test_overlay_snapshot_uses_viewer_rdps_totals() -> None:
    text = Path(__file__).with_name('fixtures').joinpath('rdps_snapshot_contract.log').read_text(encoding='utf-8')

    upload_totals = rdps_totals_from_raw_report(parse_upload_battle_log_text(text, file_name="6.log"))
    overlay_report = parse_overlay_battle_snapshot_text(text, file_name="6.log")

    overlay_totals = {
        participant["character_key"]: participant["total_rd"]
        for participant in overlay_report["participants"]
    }
    assert set(overlay_totals) == set(upload_totals)
    assert overlay_totals == {'chr_0004_pelica': 640, 'chr_0035_liino': 120}
    for character_key, upload_total in upload_totals.items():
        assert abs(upload_total - overlay_totals[character_key]) < 0.001


def test_live_overlay_parser_uses_unified_rdps_totals() -> None:
    text = Path(__file__).with_name('fixtures').joinpath('rdps_snapshot_contract.log').read_text(encoding='utf-8')
    expected = {
        participant["character_key"]: participant["total_rd"]
        for participant in parse_overlay_battle_snapshot_text(text, file_name="6.log")["participants"]
    }

    live = LiveOverlayBattleParser()
    for line in text.splitlines():
        live.feed_line(line)
    snapshot = live.snapshot()
    assert snapshot is not None

    actual = {
        participant["character_key"]: participant["total_rd"]
        for participant in snapshot["participants"]
    }
    assert set(actual) == set(expected)
    assert actual == {'chr_0004_pelica': 640, 'chr_0035_liino': 120}
    for character_key, expected_total in expected.items():
        assert abs(actual[character_key] - expected_total) < 0.001


def test_hit_viewer_uses_authoritative_overlay_rdps_for_ambiguous_enemy_owned_buff() -> None:
    text = "\n".join(
        [
            '[10:00:00.000] BUFF_START #1 id="buff_chr_0021_whiten_combo_skill_physical_vulnerable" uid=1 owner=eny_0051_rodin src=eny_0051_rodin dur=3.00 lifeT=3.00 passed=0.00 enh=1',
            '[10:00:00.000] BB[1]: rate=0.6 duration=3',
            '[10:00:00.100] HP_V2 #2 hit=1000 cum=1000 raw=1000.00 pHP=0 eHP=899000 skill="chr_0017_yvonne_skill_162" hits=1 src=chr_0017_yvonne tgt=eny_0051_rodin atk=chr_0017_yvonne seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    viewer = parse_hit_viewer_log_text(text, file_name="ambiguous-enemy-owned-buff.log")
    overlay = parse_overlay_battle_snapshot_text(text, file_name="ambiguous-enemy-owned-buff.log")

    viewer_hit = viewer["hits"][0]
    overlay_event = next(event for event in overlay["timeline_events"] if event["lane_type"] == "skill")

    assert viewer_hit["rdps_contributions"] == overlay_event["rdps_contributions"]
    assert all(item["character_key"] != "chr_0021_whiten" for item in viewer_hit["rdps_contributions"])


def test_live_overlay_parser_splits_on_game_timer_start() -> None:
    live = LiveOverlayBattleParser()
    for line in [
        "[10:00:00.000] GAME_TIMER_START seq=1 source=OnSrvStart self=0 startMs=1 expireMs=0",
        '[10:00:01.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=5000 eHP=900000 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
        "[10:00:05.000] GAME_TIMER_END seq=1 source=OnSrvComplete self=0 elapsedMs=5000 startMs=1 expireMs=0 sane=1",
        "[10:00:06.000] GAME_TIMER_START seq=2 source=OnSrvStart self=0 startMs=2 expireMs=0",
        '[10:00:07.000] HP_V2 #2 hit=200 cum=200 raw=200.00 pHP=5000 eHP=900000 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
        "[10:00:16.000] GAME_TIMER_END seq=2 source=OnSrvComplete self=0 elapsedMs=10000 startMs=2 expireMs=0 sane=1",
    ]:
        live.feed_line(line)

    snapshot = live.snapshot()
    assert snapshot is not None
    assert snapshot["battle"]["duration_ms"] == 10000
    assert snapshot["battle"]["total_damage"] == 200


def test_live_overlay_parser_starts_new_window_after_timer_end_only() -> None:
    live = LiveOverlayBattleParser()
    for line in [
        '[10:00:01.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=5000 eHP=900000 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
        "[10:00:05.000] GAME_TIMER_END seq=1 source=ChallengeComplete self=0 msg=0 elapsedMs=5000 startMs=0 expireMs=0 forceLeaveTs=0 sane=1",
    ]:
        live.feed_line(line)

    first_snapshot = live.snapshot()
    assert first_snapshot is not None
    assert first_snapshot["battle"]["duration_ms"] == 5000
    assert first_snapshot["battle"]["total_damage"] == 100

    live.feed_line(
        '[10:00:07.000] HP_V2 #2 hit=200 cum=200 raw=200.00 pHP=5000 eHP=900000 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000'
    )
    second_snapshot = live.snapshot()
    assert second_snapshot is not None
    assert second_snapshot["battle"]["total_damage"] == 200


def test_live_overlay_parser_keeps_final_snapshot_after_post_end_idle_lines() -> None:
    live = LiveOverlayBattleParser(idle_split_ms=30_000)
    for line in [
        "[10:00:00.000] OFFICIAL_TIMER_START source=SC_GAME_MECHANICS_SYNC_CHALLENGE_START gameId=indie_hard011_s challengeStartTs=1000",
        "[10:00:00.000] GAME_TIMER_START seq=1 source=PacketBattleState startMs=1000 expireMs=0 official=1",
        '[10:00:05.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=0 eHP=900 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0090_wgabyss atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
        "[10:00:10.000] OFFICIAL_TIMER_END source=SC_GAME_MECHANICS_SYNC_CHALLENGE_COMPLETE gameId=indie_hard011_s isPass=1 passTime=10000",
        "[10:00:11.000] GAME_TIMER_END seq=1 source=PacketBattleState elapsedMs=10000 startMs=1000 endMs=10000 expireMs=0 sane=1 official=1 packetElapsedMs=10000",
        '[10:00:12.000] HP_V2 #2 hit=999 cum=999 raw=999.00 pHP=0 eHP=800 skill="eny_0090_wgabyss_skill04_missile_projhit" hits=1 src=eny_0090_wgabyss tgt=chr_0027_tangtang atk=eny_0090_wgabyss seg=0 shared=2 critFlag=0 critDmg=0.5000',
    ]:
        live.feed_line(line)

    final_snapshot = live.snapshot()
    assert final_snapshot is not None
    assert final_snapshot["battle"]["boss_key"] == "eny_0090_wgabyss"
    assert final_snapshot["battle"]["total_damage"] == 100

    live.feed_line("[10:00:45.000] DUNGEON_CONTEXT dungeonId=indie_hard011_s source=SC_SELF_SCENE_INFO")
    idle_snapshot = live.snapshot()

    assert idle_snapshot is not None
    assert idle_snapshot["battle"]["boss_key"] == "eny_0090_wgabyss"
    assert idle_snapshot["battle"]["total_damage"] == 100


def test_live_overlay_parser_uses_first_party_action_window_for_synthetic_timer() -> None:
    live = LiveOverlayBattleParser()
    for line in [
        "[10:00:00.000] GAME_TIMER_START seq=1 source=BattleOpModifyBattleState startMs=1 expireMs=0 official=0",
        '[10:00:01.000] SKILL_CAST_START seq=2 startMs=1000 inst=1 owner=chr_0027_tangtang skill=chr_0027_tangtang_skill_2038',
        '[10:00:04.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=5000 eHP=899900 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
        '[10:00:09.000] HP_V2 #2 hit=150 cum=250 raw=150.00 pHP=5000 eHP=899750 skill="chr_0027_tangtang_attack1" hits=2 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
        "[10:00:10.000] GAME_TIMER_END seq=1 source=BattleOpModifyBattleState elapsedMs=10000 startMs=1 expireMs=0 sane=1 official=0",
    ]:
        live.feed_line(line)

    snapshot = live.snapshot()
    assert snapshot is not None
    assert snapshot["battle"]["time_source"] == "party_action_window"
    assert snapshot["battle"]["duration_ms"] == 8000


def test_parser_rejects_authoritative_timer_window_without_hits() -> None:
    text = "\n".join(
        [
            '[10:00:00.000] SKILL_CAST_START seq=1 startMs=1 inst=1 owner=chr_0027_tangtang skill=chr_0027_tangtang_skill_2038',
            '[10:00:01.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=5000 eHP=899900 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:04.000] HP_V2 #2 hit=200 cum=300 raw=200.00 pHP=5000 eHP=899700 skill="chr_0027_tangtang_attack1" hits=2 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:01:30.000] GAME_TIMER_END seq=1 source=PacketBattleState elapsedMs=10000 startMs=1 expireMs=0 sane=1 official=1",
        ]
    )

    report = parse_upload_battle_log_text(text)

    assert report["battle"]["time_source"] == "invalid_timer_window"
    assert report["battle"]["duration_ms"] == 4000
    assert report["battle"]["timer_window_valid"] is False
    assert report["battle"]["clear_flag"] is False
    skill_offsets = [
        event["ts_ms_from_start"]
        for event in report["timeline_events"]
        if event["lane_type"] == "skill"
    ]
    assert skill_offsets == [1000, 4000]


def test_live_overlay_parser_keeps_official_timer_when_game_timer_starts_later() -> None:
    live = LiveOverlayBattleParser()
    for line in [
        "[10:00:00.000] OFFICIAL_TIMER_START source=SC_GAME_MECHANICS_SYNC_CHALLENGE_START gameId=indie_hard007_s challengeStartTs=1000 challengeExpireTs=61000",
        "[10:00:02.000] GAME_TIMER_START seq=1 source=PacketBattleState startMs=1000 expireMs=0 official=1",
        '[10:00:10.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=5000 eHP=100 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
        '[10:01:23.000] HP_V2 #2 hit=200 cum=200 raw=200.00 pHP=5000 eHP=0 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
        "[10:01:23.945] OFFICIAL_TIMER_END source=SC_GAME_MECHANICS_SYNC_CHALLENGE_COMPLETE gameId=indie_hard007_s isPass=1 passTime=83945",
        "[10:01:24.000] GAME_TIMER_END seq=1 source=PacketBattleState elapsedMs=82000 startMs=1000 endMs=83000 expireMs=0 sane=1 official=1 packetElapsedMs=64000",
    ]:
        live.feed_line(line)

    snapshot = live.snapshot()

    assert snapshot is not None
    assert snapshot["battle"]["time_source"] == "game_timer"
    assert snapshot["battle"]["duration_ms"] == 83945


def test_overlay_parser_recovers_boss_hits_when_target_actor_was_mislabeled_as_party() -> None:
    text = "\n".join(
        [
            "[10:00:00.000] DUNGEON_CONTEXT dungeonId=dung02_bossrush02_03 source=SC_SELF_SCENE_INFO",
            "[10:00:00.000] ENTITY_STATS id=101710 template=chr_0027_tangtang kind=character level=90 hp=7030 attrs=[]",
            "[10:00:00.000] ENTITY_STATS id=101736 template=chr_0028_wulfa kind=character level=90 hp=6085 attrs=[]",
            "[10:00:00.000] SQUAD size=2 members=[chr_0027_tangtang_101710 chr_0028_wulfa_101736]",
            "[10:00:01.000] OFFICIAL_TIMER_START source=SC_GAME_MECHANICS_SYNC_CHALLENGE_START gameId=dung02_bossrush02_03",
            "[10:00:01.000] GAME_TIMER_START seq=1 source=PacketBattleState startMs=1000 expireMs=0 official=1",
            '[10:00:02.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=0 eHP=900 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0000_unknown atk=chr_0027_tangtang atkId=101710 tgtId=101788 seg=0 shared=2 critFlag=0 critDmg=-1.0000',
            '[10:00:03.000] HP_V2 #2 hit=200 cum=200 raw=200.00 pHP=0 eHP=700 skill="chr_0028_wulfa_attack1" hits=1 src=chr_0028_wulfa tgt=chr_0027_tangtang atk=chr_0028_wulfa atkId=101736 tgtId=101788 seg=0 shared=2 critFlag=0 critDmg=-1.0000',
            '[10:00:04.000] HP_V2 #3 hit=200 cum=200 raw=200.00 pHP=0 eHP=500 skill="chr_0028_wulfa_attack2" hits=1 src=chr_0028_wulfa tgt=chr_0027_tangtang atk=chr_0028_wulfa atkId=101736 tgtId=101788 seg=0 shared=2 critFlag=0 critDmg=-1.0000',
            '[10:00:05.000] HP_V2 #4 hit=50 cum=50 raw=50.00 pHP=0 eHP=6035 skill="chr_0027_tangtang_skill_2906" hits=1 src=chr_0027_tangtang tgt=chr_0028_wulfa atk=chr_0027_tangtang atkId=101788 tgtId=101736 seg=0 shared=2 critFlag=0 critDmg=-1.0000',
            "[10:00:11.000] OFFICIAL_TIMER_END source=SC_GAME_MECHANICS_SYNC_CHALLENGE_COMPLETE gameId=dung02_bossrush02_03 isPass=1 passTime=10000",
        ]
    )

    snapshot = parse_overlay_battle_snapshot_text(text, file_name="mislabeled-target.log")

    assert snapshot["battle"]["boss_key"] == "eny_0079_nefarp2"
    assert snapshot["battle"]["total_damage"] == 500
    by_key = {participant["character_key"]: participant for participant in snapshot["participants"]}
    assert by_key["chr_0027_tangtang"]["total_damage"] == 100
    assert by_key["chr_0028_wulfa"]["total_damage"] == 400


def test_overlay_parser_retargets_unknown_hits_when_context_identifies_same_boss() -> None:
    text = "\n".join(
        [
            "[10:00:00.000] DUNGEON_CONTEXT dungeonId=indie_hard011_s source=SC_SELF_SCENE_INFO",
            "[10:00:00.000] ENTITY_STATS id=101710 template=chr_0027_tangtang kind=character level=90 hp=7030 attrs=[]",
            "[10:00:00.000] ENTITY_STATS id=101736 template=chr_0028_wulfa kind=character level=90 hp=6085 attrs=[]",
            "[10:00:00.000] SQUAD size=2 members=[chr_0027_tangtang_101710 chr_0028_wulfa_101736]",
            "[10:00:01.000] OFFICIAL_TIMER_START source=SC_GAME_MECHANICS_SYNC_CHALLENGE_START gameId=indie_hard011_s",
            "[10:00:01.000] GAME_TIMER_START seq=1 source=PacketBattleState startMs=1000 expireMs=0 official=1",
            '[10:00:02.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=0 eHP=900 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0000_unknown atk=chr_0027_tangtang atkId=101710 tgtId=101788 seg=0 shared=2 critFlag=0 critDmg=-1.0000',
            '[10:00:03.000] HP_V2 #2 hit=200 cum=200 raw=200.00 pHP=0 eHP=700 skill="chr_0028_wulfa_attack1" hits=1 src=chr_0028_wulfa tgt=chr_0027_tangtang atk=chr_0028_wulfa atkId=101736 tgtId=101788 seg=0 shared=2 critFlag=0 critDmg=-1.0000',
            "[10:00:11.000] OFFICIAL_TIMER_END source=SC_GAME_MECHANICS_SYNC_CHALLENGE_COMPLETE gameId=indie_hard011_s isPass=1 passTime=10000",
        ]
    )

    snapshot = parse_overlay_battle_snapshot_text(text, file_name="mixed-unknown-target.log")

    assert snapshot["battle"]["boss_key"] == "eny_0090_wgabyss"
    assert snapshot["battle"]["boss_name"] == "破潮之像"
    assert snapshot["battle"]["total_damage"] == 300


def test_live_overlay_parser_advances_official_timer_between_hits() -> None:
    live = LiveOverlayBattleParser()
    for line in [
        "[10:00:00.000] OFFICIAL_TIMER_START source=SC_GAME_MECHANICS_SYNC_CHALLENGE_START gameId=indie_hard011_s challengeStartTs=1000",
        '[10:00:05.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=0 eHP=900000 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0090_wgabyss atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
    ]:
        live.feed_line(line)

    first_snapshot = live.snapshot(now_ms=((10 * 60) * 60 + 5) * 1000)
    assert first_snapshot is not None
    assert first_snapshot["battle"]["duration_ms"] == 5000
    assert first_snapshot["battle"]["official_timer_end_seen"] is False
    assert first_snapshot["participants"][0]["dps"] == 20.0

    later_snapshot = live.snapshot(now_ms=((10 * 60) * 60 + 15) * 1000)
    assert later_snapshot is not None
    assert later_snapshot["battle"]["duration_ms"] == 15000
    assert later_snapshot["battle"]["official_timer_end_seen"] is False
    assert round(later_snapshot["participants"][0]["dps"], 2) == 6.67


def test_live_overlay_pauses_wall_clock_when_capture_is_not_live() -> None:
    live = LiveOverlayBattleParser()
    for line in [
        "[10:00:00.000] OFFICIAL_TIMER_START source=SC_GAME_MECHANICS_SYNC_CHALLENGE_START gameId=indie_hard011_s challengeStartTs=1000",
        '[10:00:05.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=0 eHP=900000 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0090_wgabyss atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
    ]:
        live.feed_line(line)

    live.set_live_clock_enabled(False)
    frozen_snapshot = live.snapshot()
    assert frozen_snapshot is not None
    assert frozen_snapshot["battle"]["duration_ms"] == 5000
    assert frozen_snapshot["participants"][0]["dps"] == 20.0
    assert live.snapshot()["battle"]["duration_ms"] == 5000


def test_live_overlay_capture_loss_fallback_does_not_close_timer() -> None:
    live = LiveOverlayBattleParser()
    for line in [
        "[10:00:00.000] OFFICIAL_TIMER_START source=SC_GAME_MECHANICS_SYNC_CHALLENGE_START gameId=indie_battletower007_ex challengeStartTs=1000",
        '[10:00:02.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=0 eHP=900000 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0082_hsbear atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
        "[10:00:04.000] GAME_TIMER_END seq=0 source=CaptureSessionLostFallback elapsedMs=4000 startMs=0 endMs=0 expireMs=0 sane=1 official=1 fallback=1 isPass=0",
    ]:
        live.feed_line(line)

    snapshot = live.snapshot(now_ms=((10 * 60) * 60 + 40) * 1000)
    assert snapshot is not None
    assert snapshot["battle"]["duration_ms"] == 40000
    assert snapshot["battle"]["timer_end_seen"] is False
    assert snapshot["battle"]["clear_flag"] is False


def test_live_overlay_scene_fallback_does_not_close_official_timer() -> None:
    live = LiveOverlayBattleParser()
    for line in [
        "[10:00:00.000] OFFICIAL_TIMER_START source=SC_GAME_MECHANICS_SYNC_CHALLENGE_START gameId=indie_battletower007_ex challengeStartTs=1000",
        '[10:00:02.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=0 eHP=900000 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0082_hsbear atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
        "[10:00:04.000] GAME_TIMER_END seq=0 source=SceneSetBattleFallback elapsedMs=4000 startMs=0 endMs=0 expireMs=0 sane=1 official=1 fallback=1",
    ]:
        live.feed_line(line)

    final_snapshot = live.snapshot(now_ms=((10 * 60) * 60 + 40) * 1000)

    assert final_snapshot is not None
    assert final_snapshot["battle"]["duration_ms"] == 40000
    assert final_snapshot["battle"]["timer_end_seen"] is False
    assert final_snapshot["battle"]["official_timer_end_seen"] is False

    live.feed_line(
        "[10:00:04.500] OFFICIAL_TIMER_END source=SC_GAME_MECHANICS_SYNC_CHALLENGE_COMPLETE "
        "gameId=indie_battletower007_ex isPass=1 passTime=4500"
    )
    official_snapshot = live.snapshot(now_ms=((10 * 60) * 60 + 40) * 1000)
    assert official_snapshot is not None
    assert official_snapshot["battle"]["duration_ms"] == 4500
    assert official_snapshot["battle"]["official_timer_end_seen"] is True


def test_live_overlay_scene_load_reset_clears_previous_battle() -> None:
    live = LiveOverlayBattleParser()
    for line in [
        "[10:00:00.000] OFFICIAL_TIMER_START source=SC_GAME_MECHANICS_SYNC_CHALLENGE_START gameId=indie_battletower007_ex challengeStartTs=1000",
        '[10:00:02.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=0 eHP=900000 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0082_hsbear atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
    ]:
        live.feed_line(line)
    assert live.snapshot(now_ms=((10 * 60) * 60 + 3) * 1000) is not None

    assert live.feed_line("[10:00:04.000] GAME_TIMER_RESET source=CS_SCENE_LOAD_FINISH scene=430") is None
    assert live.snapshot(now_ms=((10 * 60) * 60 + 40) * 1000) is None


def test_war_echo_pass_result_without_official_complete_stays_uncleared() -> None:
    text = "\n".join(
        [
            "[10:00:00.000] OFFICIAL_TIMER_START source=SC_GAME_MECHANICS_SYNC_CHALLENGE_START gameId=indie_battletower007_ex challengeStartTs=1000",
            '[10:00:02.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=0 eHP=900000 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0082_hsbear atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:04.000] GAME_TIMER_END seq=0 source=SceneSetBattleFallback elapsedMs=4000 startMs=0 endMs=0 expireMs=0 sane=1 official=1 fallback=1 isPass=0",
            "[10:00:04.100] BATTLE_RESULT source=SC_SELF_SCENE_INFO dungeonId=indie_battletower007_ex isCalc=1 isPass=1",
        ]
    )

    parsed = blp.parse_raw_battle_log_text(text, file_name="war-echo-pass.log")

    assert parsed["battle"]["timer_end_seen"] is False
    assert parsed["battle"]["challenge_pass_confirmed"] is True
    assert parsed["battle"]["official_pass_confirmed"] is False
    assert parsed["battle"]["completion_source"] == "SC_SELF_SCENE_INFO"
    assert parsed["battle"]["clear_flag"] is False


def test_war_echo_scene_exit_without_pass_result_stays_uncleared() -> None:
    text = "\n".join(
        [
            "[10:00:00.000] OFFICIAL_TIMER_START source=SC_GAME_MECHANICS_SYNC_CHALLENGE_START gameId=indie_battletower007_ex challengeStartTs=1000",
            '[10:00:02.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=0 eHP=900000 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0082_hsbear atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:04.000] GAME_TIMER_END seq=0 source=SceneSetBattleFallback elapsedMs=4000 startMs=0 endMs=0 expireMs=0 sane=1 official=1 fallback=1 isPass=0",
        ]
    )

    parsed = blp.parse_raw_battle_log_text(text, file_name="war-echo-exit.log")

    assert parsed["battle"]["timer_end_seen"] is False
    assert parsed["battle"]["challenge_pass_confirmed"] is False
    assert parsed["battle"]["clear_flag"] is False


def test_live_war_echo_late_pass_result_does_not_replace_official_complete() -> None:
    live = LiveOverlayBattleParser()
    for line in [
        "[10:00:00.000] OFFICIAL_TIMER_START source=SC_GAME_MECHANICS_SYNC_CHALLENGE_START gameId=indie_battletower007_ex challengeStartTs=1000",
        '[10:00:02.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=0 eHP=900000 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0082_hsbear atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
        "[10:00:04.000] GAME_TIMER_END seq=0 source=SceneSetBattleFallback elapsedMs=4000 startMs=0 endMs=0 expireMs=0 sane=1 official=1 fallback=1 isPass=0",
    ]:
        live.feed_line(line)

    before_result = live.snapshot(now_ms=((10 * 60) * 60 + 4) * 1000)
    assert before_result is not None
    assert before_result["battle"]["clear_flag"] is False

    live.feed_line(
        "[10:00:04.100] BATTLE_RESULT source=SC_SELF_SCENE_INFO "
        "dungeonId=indie_battletower007_ex isCalc=1 isPass=1"
    )
    after_result = live.snapshot(now_ms=((10 * 60) * 60 + 5) * 1000)
    assert after_result is not None
    assert after_result["battle"]["clear_flag"] is False


def test_live_overlay_parser_keeps_two_phase_official_timer_open_between_game_timers() -> None:
    live = LiveOverlayBattleParser(idle_split_ms=30_000)
    for line in [
        "[10:00:00.000] OFFICIAL_TIMER_START source=SC_GAME_MECHANICS_SYNC_CHALLENGE_START gameId=dung02_bossrush02_03 challengeStartTs=1000",
        '[10:00:10.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=0 eHP=1000 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0078_nefarp1 atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
        "[10:00:20.000] GAME_TIMER_END seq=1 source=PacketBattleState elapsedMs=20000 startMs=1000 endMs=20000 expireMs=0 sane=1 official=1 packetElapsedMs=19000",
        "[10:00:22.000] GAME_TIMER_START seq=2 source=PacketBattleState startMs=22000 expireMs=0 official=1",
        "[10:00:50.000] PHASE_TRANSITION state=waiting_for_second_phase",
        '[10:01:00.000] HP_V2 #2 hit=200 cum=200 raw=200.00 pHP=0 eHP=800 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0079_nefarp2 atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
    ]:
        live.feed_line(line)

    mid_phase_snapshot = live.snapshot()
    assert mid_phase_snapshot is not None
    assert mid_phase_snapshot["battle"]["total_damage"] == 300
    assert mid_phase_snapshot["battle"]["duration_ms"] == 60000

    live.feed_line(
        "[10:01:10.000] OFFICIAL_TIMER_END source=SC_GAME_MECHANICS_SYNC_CHALLENGE_COMPLETE gameId=dung02_bossrush02_03 isPass=1 passTime=70000"
    )
    final_snapshot = live.snapshot()
    assert final_snapshot is not None
    assert final_snapshot["battle"]["duration_ms"] == 70000
    assert final_snapshot["battle"]["total_damage"] == 300


def test_overlay_snapshot_includes_frozen_loadout_members_before_they_hit() -> None:
    text = "\n".join(
        [
            "[10:00:00.000] LOADOUT reason=BATTLE_START slotCount=2 memberCount=2 roster=[1 2]",
            "[10:00:00.000] LOADOUT slot=0 char=chr_0027_tangtang weaponTemplate=wpn_pistol_0011 equipSuit={} skillIntIds=[2038]",
            "[10:00:00.000] LOADOUT slot=1 char=chr_0004_pelica weaponTemplate=wpn_funnel_0005 equipSuit={} skillIntIds=[98]",
            '[10:00:02.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=5000 eHP=899900 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )

    snapshot = parse_overlay_battle_snapshot_text(text, file_name="frozen-loadout.log")
    participant_keys = [participant["character_key"] for participant in snapshot["participants"]]

    assert participant_keys == ["chr_0027_tangtang", "chr_0004_pelica"]
    pelica = next(participant for participant in snapshot["participants"] if participant["character_key"] == "chr_0004_pelica")
    assert pelica["total_damage"] == 0.0
    assert pelica["dps"] == 0.0


def test_overlay_snapshot_discards_stale_loadout_when_runtime_party_disagrees() -> None:
    text = "\n".join(
        [
            "[10:00:00.000] DUNGEON_CONTEXT dungeonId=indie_battletower007_ex source=CS_ENTER_DUNGEON charTeamCount=0",
            "[10:00:00.000] LOADOUT reason=BATTLE_START slotCount=2 memberCount=2 roster=[1 2]",
            "[10:00:00.000] LOADOUT slot=0 char=chr_0031_mifu weaponTemplate=wpn_claym_0017 equipSuit={}",
            "[10:00:00.000] LOADOUT slot=1 char=chr_0013_aglina weaponTemplate=wpn_funnel_0016 equipSuit={}",
            '[10:00:02.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=0 eHP=899900 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0000_unknown atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=-1.0000',
            '[10:00:03.000] HP_V2 #2 hit=50 cum=50 raw=50.00 pHP=0 eHP=899850 skill="chr_0013_aglina_attack1" hits=1 src=chr_0013_aglina tgt=eny_0000_unknown atk=chr_0013_aglina seg=0 shared=2 critFlag=0 critDmg=-1.0000',
        ]
    )

    snapshot = parse_overlay_battle_snapshot_text(text, file_name="stale-loadout.log")

    assert snapshot["battle"]["boss_key"] == "eny_0082_hsbear"
    assert snapshot["battle"]["loadout_stale"] is True
    assert snapshot["loadout"] == []
    assert {row["character_key"] for row in snapshot["participants"]} == {
        "chr_0027_tangtang",
        "chr_0013_aglina",
    }


def test_live_overlay_shows_frozen_party_before_first_hit_and_starts_timer_on_first_action() -> None:
    live = LiveOverlayBattleParser()
    for line in [
        "[10:00:00.000] LOADOUT reason=BATTLE_START slotCount=2 memberCount=2 roster=[1 2]",
        "[10:00:00.000] LOADOUT slot=0 char=chr_0027_tangtang weaponTemplate=wpn_pistol_0011 equipSuit={} skillIntIds=[2038]",
        "[10:00:00.000] LOADOUT slot=1 char=chr_0004_pelica weaponTemplate=wpn_funnel_0005 equipSuit={} skillIntIds=[98]",
        "[10:00:00.000] GAME_TIMER_START seq=1 source=BattleOpModifyBattleState startMs=1 expireMs=0 official=0",
    ]:
        live.feed_line(line)

    pre_action = live.snapshot()
    assert pre_action is not None
    assert pre_action["battle"]["time_source"] == "battle_ready"
    assert [row["character_key"] for row in pre_action["participants"]] == [
        "chr_0027_tangtang",
        "chr_0004_pelica",
    ]
    assert all((row["total_damage"], row["dps"]) == (0.0, 0.0) for row in pre_action["participants"])

    live.feed_line(
        '[10:00:01.000] SKILL_CAST_START seq=2 startMs=1000 inst=1 owner=chr_0027_tangtang skill=chr_0027_tangtang_skill_2038'
    )
    post_action = live.snapshot()
    assert post_action is not None
    assert post_action["battle"]["time_source"] == "party_action_window"
    assert post_action["battle"]["duration_ms"] == 0


def test_live_overlay_parser_falls_back_to_raw_actor_dps_when_actor_map_is_missing() -> None:
    live = LiveOverlayBattleParser()
    for line in [
        "[10:00:00.000] GAME_TIMER_START",
        '[10:00:01.000] HP_V2 #1 hit=1000 cum=1000 raw=1000.00 pHP=0 eHP=900000 skill="2030" hits=1 src=id_100211 tgt=id_100314 atk=id_100211 seg=0 shared=2 critFlag=0 critDmg=0.5000',
        '[10:00:02.000] HP_V2 #2 hit=500 cum=500 raw=500.00 pHP=0 eHP=899500 skill="2132" hits=1 src=id_100237 tgt=id_100314 atk=id_100237 seg=0 shared=3 critFlag=1 critDmg=0.5000',
    ]:
        live.feed_line(line)

    snapshot = live.snapshot()

    assert snapshot is not None
    assert snapshot["battle"]["actor_mapping_complete"] is False
    assert snapshot["battle"]["rdps_available"] is False
    assert [entry["character_key"] for entry in snapshot["participants"]] == ["id_100211", "id_100237"]
    assert snapshot["participants"][0]["total_damage"] == 1000
    assert snapshot["participants"][0]["rdps"] == 0.0


def test_overlay_parser_recovers_short_actor_ids_from_loadout_bootstrap_buffs() -> None:
    text = "\n".join(
        [
            "[10:00:00.000] LOADOUT reason=SC_SELF_SCENE_INFO slotCount=2 memberCount=2 roster=[1 2]",
            "[10:00:00.000] LOADOUT slot=0 char=chr_0016_laevat weaponTemplate=wpn_sword_0006 equipSuit={} skillIntIds=[748]",
            "[10:00:00.000] LOADOUT slot=1 char=chr_0023_antal weaponTemplate=wpn_funnel_0008 equipSuit={} skillIntIds=[1462]",
            '[10:00:00.100] BUFF_START #1 id="576" uid=1 owner=id_100474 src=id_100474 dur=9999.00 lifeT=9999.00 passed=0.00 enh=1',
            '[10:00:00.100] BUFF_START #2 id="2575" uid=2 owner=id_100500 src=id_100500 dur=9999.00 lifeT=9999.00 passed=0.00 enh=1',
            "[10:00:01.000] GAME_TIMER_START",
            '[10:00:02.000] HP_V2 #1 hit=1000 cum=1000 raw=1000.00 pHP=0 eHP=900000 skill="skill_2258" hits=1 src=id_100500 tgt=id_100577 atk=id_100500 seg=0 shared=2 critFlag=0 critDmg=-1.0000',
        ]
    )

    snapshot = parse_overlay_battle_snapshot_text(text, file_name="short-actor.log")

    assert snapshot["battle"]["boss_key"] == "eny_0000_unknown"
    assert snapshot["participants"][0]["character_key"] == "chr_0023_antal"
    assert snapshot["participants"][0]["total_damage"] == 1000


def test_overlay_parser_does_not_recover_short_actor_ids_from_char_bag_loadout() -> None:
    text = "\n".join(
        [
            "[10:00:00.000] LOADOUT reason=SC_SYNC_CHAR_BAG_INFO slotCount=2 memberCount=2 roster=[1 2]",
            "[10:00:00.000] LOADOUT slot=0 char=chr_0016_laevat weaponTemplate=wpn_sword_0006 equipSuit={} skillIntIds=[748]",
            "[10:00:00.000] LOADOUT slot=1 char=chr_0023_antal weaponTemplate=wpn_funnel_0008 equipSuit={} skillIntIds=[1462]",
            '[10:00:00.100] BUFF_START #1 id="576" uid=1 owner=id_100474 src=id_100474 dur=9999.00 lifeT=9999.00 passed=0.00 enh=1',
            '[10:00:00.100] BUFF_START #2 id="2575" uid=2 owner=id_100500 src=id_100500 dur=9999.00 lifeT=9999.00 passed=0.00 enh=1',
            "[10:00:01.000] GAME_TIMER_START",
            '[10:00:02.000] HP_V2 #1 hit=1000 cum=1000 raw=1000.00 pHP=0 eHP=900000 skill="skill_999999" hits=1 src=id_100500 tgt=id_100577 atk=id_100500 seg=0 shared=2 critFlag=0 critDmg=-1.0000',
        ]
    )
    live = LiveOverlayBattleParser()
    for line in text.splitlines():
        live.feed_line(line)

    snapshot = live.snapshot()

    assert snapshot is not None
    assert snapshot["battle"]["actor_mapping_complete"] is False
    assert snapshot["participants"][0]["character_key"] == "id_100500"


def test_overlay_parser_recovers_short_actor_ids_from_runtime_fingerprint() -> None:
    text = "\n".join(
        [
            "[10:00:00.000] LOADOUT reason=SC_SYNC_CHAR_BAG_INFO slotCount=2 memberCount=2 roster=[1 2]",
            "[10:00:00.000] LOADOUT slot=0 char=chr_0016_laevat weaponTemplate=wpn_sword_0006 equipSuit={} skillIntIds=[748]",
            "[10:00:00.000] LOADOUT slot=1 char=chr_0023_antal weaponTemplate=wpn_funnel_0008 equipSuit={} skillIntIds=[1462]",
            "[10:00:01.000] GAME_TIMER_START",
            '[10:00:02.000] HP_V2 #1 hit=1000 cum=1000 raw=1000.00 pHP=0 eHP=900000 skill="skill_2258" hits=1 src=id_100500 tgt=id_100577 atk=id_100500 seg=0 shared=2 critFlag=0 critDmg=-1.0000',
        ]
    )

    snapshot = parse_overlay_battle_snapshot_text(text, file_name="runtime-fingerprint.log")

    assert snapshot["participants"][0]["character_key"] == "chr_0023_antal"
    assert snapshot["participants"][0]["total_damage"] == 1000


def test_overlay_parser_exports_loadout_and_full_buff_events() -> None:
    text = "\n".join(
        [
            "[10:00:00.000] LOADOUT reason=SC_SELF_SCENE_INFO slotCount=1 memberCount=1 roster=[1]",
            "[10:00:00.000] LOADOUT slot=0 char=chr_0023_antal slotCharInstId=1 resolvedCharInstId=1 character=packet template=chr_0023_antal potential=5 weaponInstId=2 weaponTemplate=wpn_funnel_0008 weaponLv=7 refine=0 break=0 equipInsts={} equips={[0]=item_equip_t4_suit_combo_cd01_hand_02|lv=1:3,2:3,3:3|stats=main:防御力=42;sub1:智识=84@3;sub2:意志=55@3;sub3:终结技充能效率=0.266964@3 [1]=item_equip_t4_suit_combo_cd01_body_01|stats=main:防御力=56} equipSuit={[suit_combo_cd01]=3} skillIntIds=[1462,1481]",
            "[10:00:00.000] LOADOUT_STATS slot=0 char=chr_0023_antal weaponInstId=2 weaponTemplate=wpn_funnel_0008 weaponBaseAtk=490 weaponBaseAtkLv1=50 weaponBaseAtkMax=490 weaponRefineStats={2228:level=7:potentialLv=5:bb={duration=15,lv=7,second_attr_up=0.22,spell_damage_taken_up=0.198}} weaponSourceSkills={2228:level=7:potentialLv=5:bb={duration=15,lv=7,second_attr_up=0.22,spell_damage_taken_up=0.198}} gemTemplate=1069 gemTerms={72:pulse_fragile@cost3}",
            "[10:00:01.000] GAME_TIMER_START",
            '[10:00:02.000] BUFF_START #1 id="912" uid=1 owner=eny_0000_unknown src=chr_0023_antal dur=25.00 lifeT=25.00 passed=0.00 enh=1',
            "[10:00:02.000] BB[1] rate=0.10 potential_5_rate=0.04 delay_time=20",
            '[10:00:03.000] HP_V2 #1 hit=1000 cum=1000 raw=1000.00 pHP=0 eHP=900000 skill="chr_0023_antal_normal_skill" hits=1 src=chr_0023_antal tgt=eny_0000_unknown atk=chr_0023_antal seg=0 shared=2 critFlag=0 critDmg=-1.0000',
            '[10:00:24.000] HP_V2 #2 hit=1000 cum=1000 raw=1000.00 pHP=0 eHP=899000 skill="chr_0023_antal_normal_skill" hits=1 src=chr_0023_antal tgt=eny_0000_unknown atk=chr_0023_antal seg=0 shared=2 critFlag=0 critDmg=-1.0000',
            '[10:00:27.000] BUFF_END #1 id="912" uid=1',
        ]
    )

    snapshot = parse_overlay_battle_snapshot_text(text, file_name="loadout-buffs.log")

    assert snapshot["loadout"][0]["weapon_template"] == "wpn_funnel_0008"
    assert snapshot["loadout"][0]["weapon_source_skills"][0]["bb"]["spell_damage_taken_up"] == 0.198
    assert snapshot["loadout"][0]["suit_effects"][0]["suit_id"] == "suit_combo_cd01"
    buff_events = snapshot["buff_events"]
    assert buff_events[0]["raw_event_key"] == "912"
    assert buff_events[0]["event_key"] == "buff_chr_0023_antal_normal_fragile"
    assert buff_events[0]["start_time"] == "10:00:02.000"
    assert buff_events[0]["raw_end_time"] == "10:00:27.000"
    # Keep the inputs; a nominal delay does not establish its wall-clock
    # boundary when the game timer can pause.
    assert buff_events[0]["bb_values"]["delay_time"] == 20
    assert buff_events[0]["effect_segments"] == []


def test_audit_viewer_exports_loadout_and_buff_events() -> None:
    snapshot = {
        "file_name": "unit.log",
        "battle": {
            "boss_name": "测试 Boss",
            "duration_ms": 12000,
            "total_damage": 3000,
            "rdps_available": True,
            "actor_mapping_complete": True,
        },
        "loadout": [
            {
                "character_key": "chr_0023_antal",
                "character_name": "安塔尔",
                "potential": 5,
                "weapon_template": "wpn_funnel_0008",
                "weapon_name": "爆破单元",
                "weapon_source_skills": [
                    {"skill_id": 2228, "bb": {"spell_damage_taken_up": 0.198}},
                ],
                "suit_effects": [
                    {"suit_id": "suit_combo_cd01", "suit_name": "清波", "piece_count": 3, "active": True},
                ],
            },
        ],
        "buff_events": [
            {
                "status": "included",
                "raw_event_key": "912",
                "event_key": "buff_chr_0023_antal_normal_fragile",
                "source_character_name": "安塔尔",
                "target_character_name": "测试 Boss",
                "start_time": "10:00:02.000",
                "end_time": "10:00:27.000",
                "duration_ms": 25000,
                "effect_summary": ["脆弱/fire 10% -> 14%"],
                "bb_keys": ["rate", "potential_5_rate", "delay_time"],
                "bb_values": {"rate": 0.1, "potential_5_rate": 0.04, "delay_time": 20},
                "effect_segments": [{"mode": "dynamic_after_delay", "rate": 0.14}],
            },
        ],
    }

    html = build_audit_viewer_html(snapshot)

    assert "当前队伍 / 装备" in html
    assert "BUFF 审计" in html
    assert "buff_chr_0023_antal_normal_fragile" in html
    assert "spell_damage_taken_up" in html


def test_live_overlay_preserves_loadout_and_bootstrap_buffs_across_timer_start() -> None:
    live = LiveOverlayBattleParser()
    for line in [
        "[10:00:00.000] LOADOUT reason=SC_SELF_SCENE_INFO slotCount=2 memberCount=2 roster=[1 2]",
        "[10:00:00.000] LOADOUT slot=0 char=chr_0016_laevat weaponTemplate=wpn_sword_0006 equipSuit={} skillIntIds=[748]",
        "[10:00:00.000] LOADOUT slot=1 char=chr_0023_antal weaponTemplate=wpn_funnel_0008 equipSuit={} skillIntIds=[1462]",
        '[10:00:00.100] BUFF_START #1 id="576" uid=1 owner=id_100474 src=id_100474 dur=9999.00 lifeT=9999.00 passed=0.00 enh=1',
        '[10:00:00.100] BUFF_START #2 id="2575" uid=2 owner=id_100500 src=id_100500 dur=9999.00 lifeT=9999.00 passed=0.00 enh=1',
        "[10:00:01.000] GAME_TIMER_START",
        '[10:00:02.000] HP_V2 #1 hit=1000 cum=1000 raw=1000.00 pHP=0 eHP=900000 skill="skill_2258" hits=1 src=id_100500 tgt=id_100577 atk=id_100500 seg=0 shared=2 critFlag=0 critDmg=-1.0000',
    ]:
        live.feed_line(line)

    snapshot = live.snapshot()

    assert snapshot is not None
    assert snapshot["participants"][0]["character_key"] == "chr_0023_antal"
    assert snapshot["participants"][0]["total_damage"] == 1000


def test_live_overlay_preserves_dungeon_context_across_timer_start() -> None:
    live = LiveOverlayBattleParser()
    for line in [
        "[10:00:00.000] DUNGEON_CONTEXT dungeonId=dung01_bossrush01_04 source=SC_SELF_SCENE_INFO",
        "[10:00:00.000] LOADOUT reason=BATTLE_START slotCount=2 memberCount=2 roster=[1 2]",
        "[10:00:00.000] LOADOUT slot=0 char=chr_0027_tangtang weaponTemplate=wpn_pistol_0011 equipSuit={}",
        "[10:00:00.000] LOADOUT slot=1 char=chr_0013_aglina weaponTemplate=wpn_funnel_0005 equipSuit={}",
        "[10:00:01.000] GAME_TIMER_START seq=1 source=PacketBattleState startMs=0 expireMs=0 official=1",
        '[10:00:02.000] HP_V2 #1 hit=1000 cum=1000 raw=1000.00 pHP=0 eHP=0 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0000_unknown atk=chr_0027_tangtang atkId=1 tgtId=99 seg=0 shared=2 critFlag=0 critDmg=-1.0000',
        "[10:00:04.000] GAME_TIMER_END seq=2 source=PacketBattleState elapsedMs=3000 startMs=0 endMs=3000 expireMs=0 sane=1 official=1",
    ]:
        live.feed_line(line)

    snapshot = live.snapshot()

    assert snapshot is not None
    assert snapshot["battle"]["boss_key"] == "eny_0051_rodin"
    assert snapshot["battle"]["dungeon_key"] == "dung01_group_bossrush01"


def test_damage_core_uses_exact_unit_and_never_dpd_bucket() -> None:
    assert (
        infer_damage_element(
            "buff_chr_0028_wulfa_normal_bleed_crit_extra_damage",
            "chr_0028_wulfa",
            action_id=4, damage_unit_index=0,
            dpd={"damageType": 2},
        )
        == "fire"
    )
    assert infer_damage_element('buff_chr_0028_wulfa_normal_bleed_crit_extra_damage',
                                dpd={'damageType': 2}) is None


def test_damage_core_requires_crush_action_and_unit_identity() -> None:
    key = 'buff_common_cryst_triggered_physical_break'
    assert infer_damage_element(key, action_id=0, damage_unit_index=0) == 'physical'
    assert infer_damage_school(key, action_id=0, damage_unit_index=0) == 'physical'
    assert infer_damage_element(key) is None
    assert infer_damage_school(key) is None


def test_damage_core_school_requires_resolved_hp_unit() -> None:
    assert infer_damage_element("chr_0025_ardelia_remain_loop_sheep", "chr_0025_ardelia") is None
    assert infer_damage_school("chr_0025_ardelia_remain_loop_sheep", "chr_0025_ardelia") is None
    assert infer_damage_school('chr_0016_laevat_normal_skill', action_id=36, damage_unit_index=0) == 'spell'
    assert infer_damage_school('chr_0016_laevat_normal_skill', action_id=36, damage_unit_index=1) is None


def test_damage_core_uses_school_for_physical_and_spell_effects() -> None:
    assert (
        effect_applies_to_damage_element("spell", "natural", "physical") is False
    )
    assert (
        effect_applies_to_damage_element("spell", "fire", "spell") is True
    )
    assert (
        effect_applies_to_damage_element("physical", "fire", "physical") is True
    )


def test_live_overlay_parser_throttles_reparse_and_forces_final_on_timer_end(monkeypatch) -> None:
    import parser_core.live as live_module

    clock = {"now": 100.0}
    monkeypatch.setattr(live_module.time, "monotonic", lambda: clock["now"])

    live = LiveOverlayBattleParser(min_reparse_interval_ms=500)
    live.feed_line(
        '[10:00:01.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=5000 eHP=900000 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000'
    )
    first = live.snapshot()
    assert first is not None
    assert first["battle"]["total_damage"] == 100

    live.feed_line(
        '[10:00:02.000] HP_V2 #2 hit=100 cum=200 raw=100.00 pHP=5000 eHP=900000 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000'
    )
    throttled = live.snapshot()
    assert throttled is first

    clock["now"] = 100.6
    refreshed = live.snapshot()
    assert refreshed is not None
    assert refreshed["battle"]["total_damage"] == 200

    live.feed_line(
        '[10:00:03.000] HP_V2 #3 hit=100 cum=300 raw=100.00 pHP=5000 eHP=900000 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000'
    )
    live.feed_line(
        "[10:00:05.000] GAME_TIMER_END seq=1 source=ChallengeComplete self=0 msg=0 elapsedMs=5000 startMs=0 expireMs=0 forceLeaveTs=0 sane=1"
    )
    final = live.snapshot()
    assert final is not None
    assert final["battle"]["total_damage"] == 300


def test_live_overlay_parser_does_not_repeat_same_failed_parse(monkeypatch) -> None:
    import parser_core.live as live_module

    calls = 0

    def fail_parse(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise ValueError("no HP_V2 events")

    monkeypatch.setattr(live_module, "parse_overlay_battle_snapshot_text", fail_parse)
    live = LiveOverlayBattleParser()
    live.feed_line("[10:00:00.000] DUNGEON_CONTEXT id=test")

    assert live.snapshot() is None
    assert live.snapshot() is None
    assert calls == 1
