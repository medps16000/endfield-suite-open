from pathlib import Path

import pytest
import json

from app.services.battle_payload_builder import (
    build_battle_upload_payload_from_log,
    build_battle_upload_payloads_from_log,
)
from app.services.log_integrity import (
    RAW_LOG_LOADOUT_BEGIN_PREFIX,
    RAW_LOG_LOADOUT_END,
    RAW_LOG_PROOF_BEGIN_PREFIX,
    RAW_LOG_PROOF_END,
    build_raw_log_proof,
    load_raw_log_integrity,
)
from app.services.upload_document import build_raw_log_upload_document


def test_load_raw_log_integrity_detects_tamper(tmp_path) -> None:
    log_path = tmp_path / "sample.log"
    content = "HP_V2 #1 hit=17\nBUFF_START demo\n"
    log_path.write_text(content, encoding="utf-8")

    proof = build_raw_log_proof(log_path.read_bytes(), file_name=log_path.name, meta={"hit_count": 1})
    proof_path = tmp_path / "sample.log.integrity.json"
    proof_path.write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding="utf-8")

    result = load_raw_log_integrity(str(log_path))
    assert result["verified"] is True
    assert result["issues"] == []

    log_path.write_text(content + "HP_V2 #2 hit=19\n", encoding="utf-8")
    tampered = load_raw_log_integrity(str(log_path))
    assert tampered["verified"] is False
    assert "proof.sha256 mismatch" in tampered["issues"]


def test_build_raw_log_upload_document_marks_tamper_silently(tmp_path) -> None:
    log_path = tmp_path / "sample.log"
    content = "HP_V2 #1 hit=17\n"
    log_path.write_text(content, encoding="utf-8")
    proof = build_raw_log_proof(log_path.read_bytes(), file_name=log_path.name)
    (tmp_path / "sample.log.integrity.json").write_text(
        json.dumps(proof, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    clean_doc = build_raw_log_upload_document(str(log_path))
    assert clean_doc["integrity_gate"]["tamper_suspected"] is False
    assert clean_doc["integrity_gate"]["integrity_proof_present"] is True
    assert clean_doc["proof"] is not None

    log_path.write_text(content + "HP_V2 #2 hit=19\n", encoding="utf-8")
    tampered_doc = build_raw_log_upload_document(str(log_path))
    assert tampered_doc["integrity_gate"]["tamper_suspected"] is True
    assert "proof.sha256 mismatch" in tampered_doc["integrity_gate"]["reasons"]


def test_load_raw_log_integrity_reads_embedded_proof(tmp_path) -> None:
    log_path = tmp_path / "sample.log"
    content = "HP_V2 #1 hit=17\nBUFF_START demo\n"
    proof = build_raw_log_proof(content.encode("utf-8"), file_name=log_path.name, meta={"hit_count": 1})
    embedded = (
        content
        + f"{RAW_LOG_PROOF_BEGIN_PREFIX}0\n"
        + json.dumps(proof, ensure_ascii=False, indent=2, sort_keys=True)
        + f"\n{RAW_LOG_PROOF_END}\n"
    )
    log_path.write_bytes(embedded.encode("utf-8"))

    result = load_raw_log_integrity(str(log_path))
    assert result["verified"] is True
    assert result["proof_source"] == "embedded"
    assert result["proof_path"] == "<embedded>"
    assert result["raw_content"] == content


def test_build_raw_log_upload_document_strips_embedded_proof(tmp_path) -> None:
    log_path = tmp_path / "sample.log"
    content = "HP_V2 #1 hit=17\nBUFF_START demo\n"
    proof = build_raw_log_proof(content.encode("utf-8"), file_name=log_path.name, meta={"hit_count": 1})
    embedded = (
        content
        + f"{RAW_LOG_PROOF_BEGIN_PREFIX}0\n"
        + json.dumps(proof, ensure_ascii=False, indent=2, sort_keys=True)
        + f"\n{RAW_LOG_PROOF_END}\n"
    )
    log_path.write_bytes(embedded.encode("utf-8"))

    document = build_raw_log_upload_document(str(log_path))
    assert document["content"] == content
    assert "ENDFIELD_RAW_LOG_INTEGRITY_BEGIN" not in document["content"]
    assert document["proof"] is not None
    assert document["proof"]["file_name"] == "sample.log"
    assert document["integrity_gate"]["tamper_suspected"] is False
    assert document["integrity_gate"]["integrity_proof_present"] is True


def test_build_raw_log_upload_document_strips_embedded_loadout_summary(tmp_path) -> None:
    log_path = tmp_path / "sample_loadout.log"
    content = "HP_V2 #1 hit=17\nBUFF_START demo\n"
    loadout_summary = (
        "汤汤 chr_0027_tangtang：角色潜能 0，武器 落草 (wpn_pistol_0011)，"
        "武器等级 90，武器潜能/精炼 0。装备是 碾骨重扳机 (item_equip_t4_suit_attri01_hand_02)。"
    )
    proof = build_raw_log_proof(content.encode("utf-8"), file_name=log_path.name, meta={"hit_count": 1})
    embedded = (
        content
        + f"{RAW_LOG_LOADOUT_BEGIN_PREFIX}0\n"
        + loadout_summary
        + f"\n{RAW_LOG_LOADOUT_END}\n"
        + f"{RAW_LOG_PROOF_BEGIN_PREFIX}0\n"
        + json.dumps(proof, ensure_ascii=False, indent=2, sort_keys=True)
        + f"\n{RAW_LOG_PROOF_END}\n"
    )
    log_path.write_bytes(embedded.encode("utf-8"))

    integrity = load_raw_log_integrity(str(log_path))
    assert integrity["verified"] is True
    assert integrity["raw_content"] == content

    document = build_raw_log_upload_document(str(log_path))
    assert document["content"] == content
    assert "ENDFIELD_LOADOUT_SUMMARY_BEGIN" not in document["content"]
    assert document["integrity_gate"]["tamper_suspected"] is False


def test_build_battle_upload_payload_keeps_explicit_refine_when_third_level_is_ambiguous(tmp_path) -> None:
    log_path = tmp_path / "sample_weapon_refine.log"
    content = "\n".join(
        [
            "[10:00:00.000] LOADOUT reason=BATTLE_START slotCount=1 memberCount=1 roster=[1]",
            "[10:00:00.000] LOADOUT slot=0 char=chr_0011_seraph "
            "character=packet charInfo.instId=1 template=chr_0011_seraph charLv=80 potential=5 "
            "weaponInstId=2 weaponTemplate=wpn_funnel_0008 weaponLv=0 refine=5 break=0 "
            "equips={} equipSuit={} skillIntIds=[2228]",
            "[10:00:00.000] LOADOUT_STATS slot=0 char=chr_0011_seraph "
            "weaponInstId=2 weaponTemplate=wpn_funnel_0008 weaponSync=template_only+source_skill_refine "
            "weaponBaseAtk=490 weaponBaseAtkLv1=50 weaponBaseAtkMax=490 "
            "weaponRefineStats={1391:level=5:potentialLv=5:bb={mainattr=71};"
            "2253:level=9:potentialLv=5:bb={atk=0,physpell=78};"
            "2228:level=4:potentialLv=5:bb={duration=15,lv=4,second_attr_up=0.16,spell_damage_taken_up=0.144}} "
            "weaponSourceSkills={1391:level=5:potentialLv=5:bb={mainattr=71};"
            "2253:level=9:potentialLv=5:bb={atk=0,physpell=78};"
            "2228:level=4:potentialLv=5:bb={duration=15,lv=4,second_attr_up=0.16,spell_damage_taken_up=0.144}}",
            "[10:00:00.500] LOADOUT reason=BATTLE_START slotCount=1 memberCount=1 roster=[1]",
            "[10:00:00.500] LOADOUT slot=0 char=chr_0011_seraph "
            "character=packet charInfo.instId=1 template=chr_0011_seraph charLv=80 potential=5 "
            "weaponInstId=2 weaponTemplate=wpn_funnel_0008 weaponLv=0 refine=5 break=0 "
            "equips={} equipSuit={} skillIntIds=[2228]",
            "[10:00:01.000] GAME_TIMER_START seq=1 source=ChallengeStart self=0 msg=0 startMs=0 expireMs=0 prepareSeconds=0",
            '[10:00:02.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=5000 eHP=900000 skill="chr_0011_seraph_attack1" hits=1 src=chr_0011_seraph tgt=eny_0051_rodin atk=chr_0011_seraph seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:05.000] GAME_TIMER_END seq=1 source=ChallengeComplete self=0 msg=0 elapsedMs=5000 startMs=0 expireMs=0 forceLeaveTs=0 sane=1",
        ]
    )
    log_path.write_text(content, encoding="utf-8")

    payload = build_battle_upload_payload_from_log(str(log_path))

    seraph = next(entry for entry in payload["battle"]["roster"] if entry["characterKey"] == "chr_0011_seraph")
    assert seraph["weapon"]["weaponRefine"] == 5


def test_build_battle_upload_payload_from_embedded_log(tmp_path) -> None:
    log_path = tmp_path / "sample.log"
    content = "\n".join(
        [
            "[09:59:59.500] DUNGEON_CONTEXT dungeonId=dung01_bossrush01_04 source=SC_SELF_SCENE_INFO",
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
    proof = build_raw_log_proof(
        content.encode("utf-8"),
        file_name=log_path.name,
        meta={
            "first_hit_ts": "10-00-00-000",
            "last_hit_ts": "10-00-02-000",
            "hit_count": 3,
            "roster": [
                {
                    "char_key": "chr_0027_tangtang",
                    "char_name": "汤汤",
                    "potential": 0,
                    "weapon_name": "落草",
                    "weapon_template": "wpn_pistol_0011",
                    "weapon_level": 90,
                    "weapon_refine": 0,
                    "equips": [
                        {
                            "slot": 0,
                            "item_id": "item_equip_t4_suit_attri01_hand_02",
                            "piece_name": "碾骨腕带·壹型",
                            "part_name": "护手",
                            "suit_name": "碾骨",
                        }
                    ],
                }
            ],
        },
    )
    embedded = (
        content
        + f"{RAW_LOG_PROOF_BEGIN_PREFIX}0\n"
        + json.dumps(proof, ensure_ascii=False, indent=2, sort_keys=True)
        + f"\n{RAW_LOG_PROOF_END}\n"
    )
    log_path.write_bytes(embedded.encode("utf-8"))

    payload = build_battle_upload_payload_from_log(str(log_path))
    assert payload["battle"]["bossKey"] == "eny_0051_rodin"
    assert payload["battle"]["bossName"] == "“碾骨之拳”罗丹"
    assert payload["battle"]["dungeonKey"] == "dung01_group_bossrush01"
    assert payload["battle"]["dungeonContextId"] == "dung01_bossrush01_04"
    assert payload["battle"]["dungeonIdentitySource"] == "dungeon_context"
    assert payload["battle"]["dungeonName"] == "危境再现·罗丹"
    assert payload["battle"]["durationMs"] == 2000
    assert payload["battle"]["clearFlag"] is False
    assert payload["participants"][0]["characterName"] == "汤汤"
    assert payload["participants"][0]["rdps"] == payload["participants"][0]["dps"]
    assert payload["battle"]["roster"][0]["characterPotential"] == 0
    assert payload["battle"]["roster"][0]["weapon"]["weaponTemplate"] == "wpn_pistol_0011"
    assert payload["battle"]["roster"][0]["weapon"]["weaponLevel"] == 90
    assert payload["battle"]["roster"][0]["equips"][0]["itemId"] == "item_equip_t4_suit_attri01_hand_02"
    assert payload["battle"]["roster"][0]["equips"][0]["iconUrl"] is not None
    assert payload["battle"]["loadoutFallbackUsed"] is True
    damage_events = [event for event in payload["timelineEvents"] if event["laneType"] == "skill"]
    assert damage_events[0]["eventType"] == "damage"
    assert damage_events[0]["eventName"] == "崩你脑壳！"
    assert damage_events[0]["eventGroupKey"] is not None
    assert damage_events[0]["eventGroupKey"] == damage_events[1]["eventGroupKey"]
    assert damage_events[2]["eventGroupKey"] != damage_events[0]["eventGroupKey"]
    buff_events = [event for event in payload["timelineEvents"] if event["laneType"] == "buff"]
    assert len(buff_events) == 1


def test_build_battle_upload_payload_marks_clear_when_boss_hp_reaches_zero(tmp_path) -> None:
    log_path = tmp_path / "sample_clear.log"
    content = "\n".join(
        [
            "[10:00:00.000] GAME_TIMER_START seq=1 source=PacketBattleState startMs=0 expireMs=0 official=1",
            '[10:00:00.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=5000 eHP=900000 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:02.000] HP_V2 #2 hit=900000 cum=900100 raw=900000.00 pHP=5000 eHP=0 skill="chr_0027_tangtang_attack1" hits=2 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:03.000] GAME_TIMER_END seq=1 source=PacketBattleState elapsedMs=3000 startMs=0 endMs=3000 expireMs=0 sane=1 official=1",
        ]
    )
    proof = build_raw_log_proof(
        content.encode("utf-8"),
        file_name=log_path.name,
        meta={"first_hit_ts": "10-00-00-000", "last_hit_ts": "10-00-02-000", "hit_count": 2},
    )
    embedded = (
        content
        + f"{RAW_LOG_PROOF_BEGIN_PREFIX}0\n"
        + json.dumps(proof, ensure_ascii=False, indent=2, sort_keys=True)
        + f"\n{RAW_LOG_PROOF_END}\n"
    )
    log_path.write_bytes(embedded.encode("utf-8"))

    payload = build_battle_upload_payload_from_log(str(log_path))

    assert payload["battle"]["clearFlag"] is True


def test_build_battle_upload_payload_keeps_two_phase_official_timer_as_one_battle(tmp_path) -> None:
    log_path = tmp_path / "sample_two_phase.log"
    content = "\n".join(
        [
            "[10:00:00.000] DUNGEON_CONTEXT dungeonId=dung02_bossrush02_03 source=SC_SELF_SCENE_INFO",
            "[10:00:00.000] OFFICIAL_TIMER_START source=SC_GAME_MECHANICS_SYNC_CHALLENGE_START gameId=dung02_bossrush02_03 challengeStartTs=1000",
            '[10:00:02.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=5000 eHP=0 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0078_nefarp1 atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:03.000] GAME_TIMER_END seq=1 source=PacketBattleState elapsedMs=3000 startMs=0 endMs=3000 expireMs=0 sane=1 official=1",
            "[10:00:04.000] GAME_TIMER_START seq=2 source=PacketBattleState startMs=3000 expireMs=0 official=1",
            '[10:00:07.000] HP_V2 #2 hit=200 cum=200 raw=200.00 pHP=5000 eHP=300 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0079_nefarp2 atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:09.000] HP_V2 #3 hit=300 cum=300 raw=300.00 pHP=5000 eHP=0 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0079_nefarp2 atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:10.000] OFFICIAL_TIMER_END source=SC_GAME_MECHANICS_SYNC_CHALLENGE_COMPLETE gameId=dung02_bossrush02_03 isPass=1 passTime=8000",
            "[10:00:10.050] GAME_TIMER_END seq=2 source=PacketBattleState elapsedMs=6050 startMs=3000 endMs=9050 expireMs=0 sane=1 official=1",
        ]
    )
    log_path.write_text(content, encoding="utf-8")

    payloads = build_battle_upload_payloads_from_log(str(log_path))

    assert len(payloads) == 1
    payload = payloads[0]
    assert payload["battle"]["durationMs"] == 8000
    assert payload["battle"]["timeSource"] == "game_timer"
    assert payload["battle"]["timelineZeroSource"] == "official_timer_start"
    assert payload["battle"]["officialTimerStartSeen"] is True
    assert payload["battle"]["officialTimerEndSeen"] is True
    assert payload["battle"]["timerStartInferred"] is False
    assert payload["battle"]["bossKey"] == "eny_0079_nefarp2"
    assert payload["battle"]["clearFlag"] is True
    assert payload["battle"]["totalDamage"] == 600


def test_build_battle_upload_payload_fills_missing_proof_equips_from_raw_loadout(tmp_path) -> None:
    log_path = tmp_path / "sample_loadout_fallback.log"
    content = "\n".join(
        [
            "[10:00:00.000] LOADOUT slot=0 char=chr_0027_tangtang potential=0 weaponTemplate=wpn_pistol_0011 weaponLv=90 refine=5 break=4 equips={[0]=item_equip_t4_suit_attri01_hand_02 [1]=item_equip_t4_suit_atk02_body_02 [2]=item_equip_t4_suit_attri01_edc_07 [3]=item_equip_t4_suit_attri01_edc_07} equipSuit={}",
            '[10:00:01.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=5000 eHP=900000 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )
    proof = build_raw_log_proof(
        content.encode("utf-8"),
        file_name=log_path.name,
        meta={
            "first_hit_ts": "10-00-01-000",
            "last_hit_ts": "10-00-01-000",
            "hit_count": 1,
            "loadout": [
                {
                    "slot": 0,
                    "char_key": "chr_0027_tangtang",
                    "char_name": "汤汤",
                    "weapon_template": "wpn_pistol_0011",
                    "weapon_level": 90,
                    "weapon_refine": 5,
                    "equips": [],
                }
            ],
        },
    )
    embedded = (
        content
        + f"{RAW_LOG_PROOF_BEGIN_PREFIX}0\n"
        + json.dumps(proof, ensure_ascii=False, indent=2, sort_keys=True)
        + f"\n{RAW_LOG_PROOF_END}\n"
    )
    log_path.write_bytes(embedded.encode("utf-8"))

    payload = build_battle_upload_payload_from_log(str(log_path))
    loadout = payload["battle"]["roster"][0]

    assert loadout["weapon"]["weaponRefine"] == 5
    assert [equip["slot"] for equip in loadout["equips"]] == [0, 1, 2, 3]
    assert len(loadout["equips"]) == 4


def test_build_battle_upload_payloads_merge_global_loadout_into_segment_delta(tmp_path) -> None:
    log_path = tmp_path / "sample_segment_delta_loadout.log"
    content = "\n".join(
        [
            "[10:00:00.000] LOADOUT reason=SC_SYNC_CHAR_BAG_INFO slotCount=1 memberCount=1 roster=[1]",
            "[10:00:00.000] LOADOUT slot=0 char=chr_0027_tangtang potential=0 weaponTemplate=wpn_pistol_0011 weaponLv=90 refine=5 break=4 equips={[0]=item_equip_t4_suit_attri01_hand_02 [1]=item_equip_t4_suit_atk02_body_02 [2]=item_equip_t4_suit_attri01_edc_07 [3]=item_equip_t4_suit_attri01_edc_07} equipSuit={}",
            "[10:00:01.000] GAME_TIMER_START seq=1 source=PacketBattleState startMs=0 expireMs=0 official=1",
            '[10:00:02.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=5000 eHP=900000 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:03.000] GAME_TIMER_END seq=1 source=PacketBattleState elapsedMs=2000 startMs=0 endMs=2000 expireMs=0 sane=1 official=1",
            "[10:01:00.000] GAME_TIMER_START seq=2 source=PacketBattleState startMs=0 expireMs=0 official=1",
            "[10:01:00.100] LOADOUT reason=BATTLE_START slotCount=1 memberCount=1 roster=[1]",
            "[10:01:00.100] LOADOUT slot=0 char=chr_0027_tangtang potential=0 weaponTemplate=wpn_pistol_0011 weaponLv=90 refine=5 break=4 equips={[2]=item_equip_t4_suit_phy01_edc_04} equipSuit={}",
            '[10:01:01.000] HP_V2 #2 hit=200 cum=200 raw=200.00 pHP=5000 eHP=899800 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:01:02.000] GAME_TIMER_END seq=2 source=PacketBattleState elapsedMs=2000 startMs=0 endMs=2000 expireMs=0 sane=1 official=1",
        ]
    )
    log_path.write_text(content, encoding="utf-8")

    payloads = build_battle_upload_payloads_from_log(str(log_path))

    assert len(payloads) == 2
    equips = payloads[1]["battle"]["roster"][0]["equips"]
    assert payloads[1]["battle"]["loadoutFallbackUsed"] is False
    assert [equip["slot"] for equip in equips] == [0, 1, 2, 3]
    assert [equip["itemId"] for equip in equips] == [
        "item_equip_t4_suit_attri01_hand_02",
        "item_equip_t4_suit_atk02_body_02",
        "item_equip_t4_suit_phy01_edc_04",
        "item_equip_t4_suit_attri01_edc_07",
    ]


def test_build_battle_upload_payload_normalizes_one_based_equip_slots(tmp_path) -> None:
    log_path = tmp_path / "sample_one_based_equips.log"
    content = "\n".join(
        [
            '[10:00:01.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=5000 eHP=900000 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )
    proof = build_raw_log_proof(
        content.encode("utf-8"),
        file_name=log_path.name,
        meta={
            "first_hit_ts": "10-00-01-000",
            "last_hit_ts": "10-00-01-000",
            "hit_count": 1,
            "loadout": [
                {
                    "slot": 0,
                    "char_key": "chr_0027_tangtang",
                    "char_name": "汤汤",
                    "weapon_template": "wpn_pistol_0011",
                    "weapon_level": 90,
                    "weapon_refine": 5,
                    "equips": [
                        {"slot": 1, "item_id": "item_equip_t4_suit_attri01_hand_02"},
                        {"slot": 2, "item_id": "item_equip_t4_suit_atk02_body_02"},
                        {"slot": 3, "item_id": "item_equip_t4_suit_attri01_edc_07"},
                        {"slot": 4, "item_id": "item_equip_t4_suit_attri01_edc_07"},
                    ],
                }
            ],
        },
    )
    embedded = (
        content
        + f"{RAW_LOG_PROOF_BEGIN_PREFIX}0\n"
        + json.dumps(proof, ensure_ascii=False, indent=2, sort_keys=True)
        + f"\n{RAW_LOG_PROOF_END}\n"
    )
    log_path.write_bytes(embedded.encode("utf-8"))

    payload = build_battle_upload_payload_from_log(str(log_path))

    assert [equip["slot"] for equip in payload["battle"]["roster"][0]["equips"]] == [0, 1, 2, 3]


@pytest.mark.skipif(
    not (Path(__file__).resolve().parents[3] / "data/local_semantics/buff/details.json").is_file(),
    reason="buff timeline classification requires externally supplied game semantics",
)
def test_build_battle_upload_payloads_split_multi_battle_log(tmp_path) -> None:
    log_path = tmp_path / "sample_multi.log"
    content = "\n".join(
        [
            '[10:00:00.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=5000 eHP=900000 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:01.000] HP_V2 #2 hit=250 cum=350 raw=250.00 pHP=5000 eHP=899750 skill="chr_0027_tangtang_attack1" hits=2 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=3 critFlag=1 critDmg=0.5000',
            '[10:00:02.000] HP_V2 #3 hit=300 cum=300 raw=300.00 pHP=4800 eHP=899450 skill="chr_0004_pelica_attack1" hits=1 src=chr_0004_pelica tgt=eny_0051_rodin atk=chr_0004_pelica seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:01:09.500] ATTR_MOD buff="buff_equipsuit_critsuitatk_01" i=0 attrType=2 modType=0 formula=6 useKey=1 val=0.0500 bbKey="atk_up"',
            '[10:01:09.500] BUFF_START #4 id="buff_equipsuit_critsuitatk_01" uid=1 owner=chr_0027_tangtang src=chr_0027_tangtang dur=5.00 lifeT=5.00 passed=0.00 enh=1',
            '[10:01:09.500]   BB[4]: atk_up=0.05 =0 crit_up2=0.05 =0',
            '[10:01:10.000] HP_V2 #5 hit=180 cum=180 raw=180.00 pHP=5000 eHP=899270 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:01:12.000] HP_V2 #6 hit=220 cum=220 raw=220.00 pHP=4800 eHP=899050 skill="chr_0004_pelica_attack1" hits=1 src=chr_0004_pelica tgt=eny_0051_rodin atk=chr_0004_pelica seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )
    proof = build_raw_log_proof(
        content.encode("utf-8"),
        file_name=log_path.name,
        meta={"first_hit_ts": "10-00-00-000", "last_hit_ts": "10-01-12-000", "hit_count": 5},
    )
    embedded = (
        content
        + f"{RAW_LOG_PROOF_BEGIN_PREFIX}0\n"
        + json.dumps(proof, ensure_ascii=False, indent=2, sort_keys=True)
        + f"\n{RAW_LOG_PROOF_END}\n"
    )
    log_path.write_bytes(embedded.encode("utf-8"))

    payloads = build_battle_upload_payloads_from_log(str(log_path))
    assert len(payloads) == 2

    first_payload, second_payload = payloads
    assert first_payload["battle"]["durationMs"] == 2000
    assert second_payload["battle"]["durationMs"] == 2000
    assert first_payload["battle"]["battleFingerprint"] != second_payload["battle"]["battleFingerprint"]
    assert {participant["characterKey"] for participant in second_payload["participants"]} == {
        "chr_0027_tangtang",
        "chr_0004_pelica",
    }
    second_buff_events = [event for event in second_payload["timelineEvents"] if event["laneType"] == "buff"]
    assert len(second_buff_events) == 1

    first_only = build_battle_upload_payload_from_log(str(log_path))
    assert first_only["battle"]["battleFingerprint"] == first_payload["battle"]["battleFingerprint"]


def test_build_battle_upload_payloads_stops_at_known_fingerprint(tmp_path) -> None:
    log_path = tmp_path / "sample_incremental.log"
    content = "\n".join(
        [
            "[10:00:00.000] GAME_TIMER_START seq=1 source=PacketBattleState startMs=0 expireMs=0 official=1",
            '[10:00:01.000] HP_V2 #1 hit=1000 cum=1000 raw=1000.00 pHP=5000 eHP=900000 skill="chr_0028_wulfa_attack1" hits=1 src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:05.000] GAME_TIMER_END seq=1 source=ChallengeComplete self=0 msg=0 elapsedMs=5000 startMs=0 expireMs=0 forceLeaveTs=0 sane=1",
            "[10:01:00.000] GAME_TIMER_START seq=2 source=PacketBattleState startMs=0 expireMs=0 official=1",
            '[10:01:01.000] HP_V2 #2 hit=2000 cum=2000 raw=2000.00 pHP=5000 eHP=900000 skill="chr_0028_wulfa_attack1" hits=1 src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:01:05.000] GAME_TIMER_END seq=2 source=ChallengeComplete self=0 msg=0 elapsedMs=5000 startMs=0 expireMs=0 forceLeaveTs=0 sane=1",
        ]
    )
    log_path.write_text(content, encoding="utf-8")

    payloads = build_battle_upload_payloads_from_log(str(log_path))
    assert len(payloads) == 2
    first_payload, second_payload = payloads

    incremental_payloads = build_battle_upload_payloads_from_log(
        str(log_path),
        known_fingerprints={first_payload["battle"]["battleFingerprint"]},
    )
    assert [payload["battle"]["battleFingerprint"] for payload in incremental_payloads] == [
        second_payload["battle"]["battleFingerprint"]
    ]
    assert "_sourceBattleIndex" not in incremental_payloads[0]

    indexed_incremental_payloads = build_battle_upload_payloads_from_log(
        str(log_path),
        known_fingerprints={first_payload["battle"]["battleFingerprint"]},
        known_battle_index=1,
    )
    assert [payload["battle"]["battleFingerprint"] for payload in indexed_incremental_payloads] == [
        second_payload["battle"]["battleFingerprint"]
    ]

    no_new_payloads = build_battle_upload_payloads_from_log(
        str(log_path),
        known_fingerprints={second_payload["battle"]["battleFingerprint"]},
        known_battle_index=2,
    )
    assert no_new_payloads == []


def test_build_battle_upload_payloads_split_close_retries_by_game_timer(tmp_path) -> None:
    log_path = tmp_path / "sample_close_retries.log"
    content = "\n".join(
        [
            "[10:00:00.000] GAME_TIMER_START seq=1 source=ChallengeStart self=0 msg=0 startMs=100 expireMs=0 prepareSeconds=0",
            '[10:00:01.000] HP_V2 #1 hit=1000 cum=1000 raw=1000.00 pHP=5000 eHP=900000 skill="chr_0028_wulfa_attack1" hits=1 src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:03.000] HP_V2 #2 hit=2000 cum=2000 raw=2000.00 pHP=5000 eHP=898000 skill="chr_0028_wulfa_attack1" hits=1 src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:05.000] GAME_TIMER_END seq=1 source=ChallengeComplete self=0 msg=0 elapsedMs=5000 startMs=100 expireMs=0 forceLeaveTs=0 sane=1",
            "[10:00:10.000] GAME_TIMER_START seq=2 source=ChallengeStart self=0 msg=0 startMs=200 expireMs=0 prepareSeconds=0",
            '[10:00:11.000] HP_V2 #3 hit=4000 cum=4000 raw=4000.00 pHP=5000 eHP=900000 skill="chr_0028_wulfa_attack1" hits=1 src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:13.000] HP_V2 #4 hit=5000 cum=5000 raw=5000.00 pHP=5000 eHP=895000 skill="chr_0028_wulfa_attack1" hits=1 src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:15.000] GAME_TIMER_END seq=2 source=ChallengeComplete self=0 msg=0 elapsedMs=5000 startMs=200 expireMs=0 forceLeaveTs=0 sane=1",
        ]
    )
    proof = build_raw_log_proof(
        content.encode("utf-8"),
        file_name=log_path.name,
        meta={"first_hit_ts": "10-00-01-000", "last_hit_ts": "10-00-13-000", "hit_count": 4},
    )
    embedded = (
        content
        + f"{RAW_LOG_PROOF_BEGIN_PREFIX}0\n"
        + json.dumps(proof, ensure_ascii=False, indent=2, sort_keys=True)
        + f"\n{RAW_LOG_PROOF_END}\n"
    )
    log_path.write_bytes(embedded.encode("utf-8"))

    payloads = build_battle_upload_payloads_from_log(str(log_path))

    assert len(payloads) == 2
    assert payloads[0]["battle"]["durationMs"] == 5000
    assert payloads[0]["battle"]["timelineZeroSource"] == "game_timer_start"
    assert payloads[0]["battle"]["timerStartSeen"] is True
    assert payloads[0]["battle"]["timerEndSeen"] is True
    assert payloads[0]["battle"]["timerStartInferred"] is False
    assert payloads[0]["battle"]["totalDamage"] == 3000
    assert payloads[0]["participants"][0]["dps"] == 600.0
    assert payloads[1]["battle"]["durationMs"] == 5000
    assert payloads[1]["battle"]["timelineZeroSource"] == "game_timer_start"
    assert payloads[1]["battle"]["totalDamage"] == 9000
    assert payloads[1]["participants"][0]["dps"] == 1800.0


def test_build_battle_upload_payloads_split_close_retries_by_official_timer_and_keeps_dungeon_context(tmp_path) -> None:
    log_path = tmp_path / "sample_official_timer_retries.log"
    content = "\n".join(
        [
            "[10:00:00.000] DUNGEON_CONTEXT dungeonId=indie_hard008_s source=SC_SELF_SCENE_INFO scene=210 isReward=1 isCalc=0 isPass=0",
            "[10:00:00.100] OFFICIAL_TIMER_START source=SC_GAME_MECHANICS_SYNC_CHALLENGE_START gameId=indie_hard008_s challengeStartTs=1000 challengeExpireTs=61000",
            "[10:00:00.200] GAME_TIMER_START seq=1 source=PacketBattleState startMs=1000 expireMs=0 official=1",
            '[10:00:01.000] HP_V2 #1 hit=1000 cum=1000 raw=1000.00 pHP=5000 eHP=732731 skill="chr_0028_wulfa_attack1" hits=1 src=chr_0028_wulfa tgt=eny_0085_hsrogue_hard atk=chr_0028_wulfa seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:05.000] OFFICIAL_TIMER_END source=SC_GAME_MECHANICS_SYNC_CHALLENGE_COMPLETE gameId=indie_hard008_s isPass=1 passTime=5000",
            "[10:00:05.100] GAME_TIMER_END seq=1 source=PacketBattleState elapsedMs=4900 startMs=1000 endMs=5900 expireMs=0 sane=1 official=1",
            "[10:00:10.000] DUNGEON_CONTEXT dungeonId=indie_hard009_s source=SC_SELF_SCENE_INFO scene=210 isReward=1 isCalc=0 isPass=0",
            "[10:00:10.100] OFFICIAL_TIMER_START source=SC_GAME_MECHANICS_SYNC_CHALLENGE_START gameId=indie_hard009_s challengeStartTs=2000 challengeExpireTs=62000",
            "[10:00:10.200] GAME_TIMER_START seq=2 source=PacketBattleState startMs=2000 expireMs=0 official=1",
            '[10:00:11.000] HP_V2 #2 hit=4000 cum=4000 raw=4000.00 pHP=5000 eHP=896000 skill="chr_0028_wulfa_attack1" hits=1 src=chr_0028_wulfa tgt=eny_0047_firebat_hdg009 atk=chr_0028_wulfa seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:15.000] OFFICIAL_TIMER_END source=SC_GAME_MECHANICS_SYNC_CHALLENGE_COMPLETE gameId=indie_hard009_s isPass=1 passTime=5000",
            "[10:00:15.100] GAME_TIMER_END seq=2 source=PacketBattleState elapsedMs=4900 startMs=2000 endMs=6900 expireMs=0 sane=1 official=1",
        ]
    )
    proof = build_raw_log_proof(
        content.encode("utf-8"),
        file_name=log_path.name,
        meta={"first_hit_ts": "10-00-01-000", "last_hit_ts": "10-00-11-000", "hit_count": 2},
    )
    embedded = (
        content
        + f"{RAW_LOG_PROOF_BEGIN_PREFIX}0\n"
        + json.dumps(proof, ensure_ascii=False, indent=2, sort_keys=True)
        + f"\n{RAW_LOG_PROOF_END}\n"
    )
    log_path.write_bytes(embedded.encode("utf-8"))

    payloads = build_battle_upload_payloads_from_log(str(log_path))

    assert len(payloads) == 2
    assert payloads[0]["battle"]["dungeonKey"] == "indie_hard008_s"
    assert payloads[0]["battle"]["dungeonName"] == "怨憎雾海·苦难"
    assert payloads[0]["battle"]["durationMs"] == 5000
    assert payloads[1]["battle"]["dungeonKey"] == "indie_hard009_s"
    assert payloads[1]["battle"]["dungeonName"] == "血肉熔点·苦难"
    assert payloads[1]["battle"]["durationMs"] == 5000


def test_build_battle_upload_payloads_uses_official_timer_game_id_without_dungeon_context(tmp_path) -> None:
    log_path = tmp_path / "sample_official_timer_game_id_only.log"
    content = "\n".join(
        [
            "[10:00:00.100] OFFICIAL_TIMER_START source=SC_GAME_MECHANICS_SYNC_CHALLENGE_START gameId=indie_hard013_s challengeStartTs=1000 challengeExpireTs=61000",
            '[10:00:01.000] HP_V2 #1 hit=1000 cum=1000 raw=1000.00 pHP=5000 eHP=732731 skill="chr_0028_wulfa_attack1" hits=1 src=chr_0028_wulfa tgt=eny_0059_erhound atk=chr_0028_wulfa seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:05.000] OFFICIAL_TIMER_END source=SC_GAME_MECHANICS_SYNC_CHALLENGE_COMPLETE gameId=indie_hard013_s isPass=1 passTime=5000",
        ]
    )
    proof = build_raw_log_proof(
        content.encode("utf-8"),
        file_name=log_path.name,
        meta={"first_hit_ts": "10-00-01-000", "last_hit_ts": "10-00-01-000", "hit_count": 1},
    )
    embedded = (
        content
        + f"{RAW_LOG_PROOF_BEGIN_PREFIX}0\n"
        + json.dumps(proof, ensure_ascii=False, indent=2, sort_keys=True)
        + f"\n{RAW_LOG_PROOF_END}\n"
    )
    log_path.write_bytes(embedded.encode("utf-8"))

    payloads = build_battle_upload_payloads_from_log(str(log_path))

    assert len(payloads) == 1
    assert payloads[0]["battle"]["dungeonKey"] == "indie_hard013_s"
    assert payloads[0]["battle"]["dungeonName"] == "沉寂视界·苦难"
    assert payloads[0]["battle"]["durationMs"] == 5000


def test_build_battle_upload_payloads_split_close_retries_by_timer_end_only(tmp_path) -> None:
    log_path = tmp_path / "sample_close_retries_end_only.log"
    content = "\n".join(
        [
            '[10:00:01.000] HP_V2 #1 hit=1000 cum=1000 raw=1000.00 pHP=5000 eHP=900000 skill="chr_0028_wulfa_attack1" hits=1 src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:03.000] HP_V2 #2 hit=2000 cum=2000 raw=2000.00 pHP=5000 eHP=898000 skill="chr_0028_wulfa_attack1" hits=1 src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:05.000] GAME_TIMER_END seq=1 source=ChallengeComplete self=0 msg=0 elapsedMs=5000 startMs=0 expireMs=0 forceLeaveTs=0 sane=1",
            '[10:00:11.000] HP_V2 #3 hit=4000 cum=4000 raw=4000.00 pHP=5000 eHP=900000 skill="chr_0028_wulfa_attack1" hits=1 src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:13.000] HP_V2 #4 hit=5000 cum=5000 raw=5000.00 pHP=5000 eHP=895000 skill="chr_0028_wulfa_attack1" hits=1 src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:15.000] GAME_TIMER_END seq=2 source=ChallengeComplete self=0 msg=0 elapsedMs=5000 startMs=0 expireMs=0 forceLeaveTs=0 sane=1",
        ]
    )
    proof = build_raw_log_proof(
        content.encode("utf-8"),
        file_name=log_path.name,
        meta={"first_hit_ts": "10-00-01-000", "last_hit_ts": "10-00-13-000", "hit_count": 4},
    )
    embedded = (
        content
        + f"{RAW_LOG_PROOF_BEGIN_PREFIX}0\n"
        + json.dumps(proof, ensure_ascii=False, indent=2, sort_keys=True)
        + f"\n{RAW_LOG_PROOF_END}\n"
    )
    log_path.write_bytes(embedded.encode("utf-8"))

    payloads = build_battle_upload_payloads_from_log(str(log_path))

    assert len(payloads) == 2
    assert payloads[0]["battle"]["durationMs"] == 5000
    assert payloads[0]["battle"]["timelineZeroSource"] == "timer_end_inferred"
    assert payloads[0]["battle"]["timerStartSeen"] is False
    assert payloads[0]["battle"]["timerEndSeen"] is True
    assert payloads[0]["battle"]["timerStartInferred"] is True
    assert payloads[0]["battle"]["totalDamage"] == 3000
    assert payloads[0]["participants"][0]["dps"] == 600.0
    assert payloads[1]["battle"]["durationMs"] == 5000
    assert payloads[1]["battle"]["timelineZeroSource"] == "timer_end_inferred"
    assert payloads[1]["battle"]["totalDamage"] == 9000
    assert payloads[1]["participants"][0]["dps"] == 1800.0


def test_build_battle_upload_payload_filters_non_loadout_admin_character(tmp_path) -> None:
    log_path = tmp_path / "sample_admin.log"
    content = "\n".join(
        [
            '[12:59:54.646] BUFF_START #1683 id="buff_chr_0003_endminf_potential1" uid=44369 owner=chr_0002_endminm src=chr_0002_endminm dur=9999.00 lifeT=9999.00 passed=0.00 enh=1',
            '[13:00:15.503] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=5000 eHP=900000 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0079_nefarp2 atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[13:00:16.000] HP_V2 #2 hit=200 cum=200 raw=200.00 pHP=5000 eHP=899800 skill="chr_0004_pelica_attack1" hits=1 src=chr_0004_pelica tgt=eny_0079_nefarp2 atk=chr_0004_pelica seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )
    proof = build_raw_log_proof(
        content.encode("utf-8"),
        file_name=log_path.name,
        meta={
            "first_hit_ts": "13-00-15-503",
            "last_hit_ts": "13-00-16-000",
            "hit_count": 2,
            "loadout": [
                {
                    "slot": 0,
                    "char_key": "chr_0027_tangtang",
                    "char_name": "汤汤",
                    "potential": 0,
                    "weapon_name": "落草",
                    "weapon_template": "wpn_pistol_0011",
                    "weapon_level": 90,
                    "weapon_refine": 0,
                    "equips": [],
                },
                {
                    "slot": 1,
                    "char_key": "chr_0004_pelica",
                    "char_name": "佩丽卡",
                    "potential": 5,
                    "weapon_name": "悼亡诗",
                    "weapon_template": "wpn_funnel_0005",
                    "weapon_level": 1,
                    "weapon_refine": 5,
                    "equips": [],
                },
            ],
        },
    )
    embedded = (
        content
        + f"{RAW_LOG_PROOF_BEGIN_PREFIX}0\n"
        + json.dumps(proof, ensure_ascii=False, indent=2, sort_keys=True)
        + f"\n{RAW_LOG_PROOF_END}\n"
    )
    log_path.write_bytes(embedded.encode("utf-8"))

    payload = build_battle_upload_payload_from_log(str(log_path))

    assert [entry["characterKey"] for entry in payload["battle"]["roster"]] == [
        "chr_0027_tangtang",
        "chr_0004_pelica",
    ]
    assert [entry["characterKey"] for entry in payload["participants"]] == [
        "chr_0004_pelica",
        "chr_0027_tangtang",
    ]
    assert all(
        event.get("sourceCharacterKey") != "chr_0002_endminm"
        and event.get("targetCharacterKey") != "chr_0002_endminm"
        for event in payload["timelineEvents"]
    )


def test_build_battle_upload_payload_filters_post_battle_self_buff_loadout_character(tmp_path) -> None:
    log_path = tmp_path / "sample_post_battle_admin.log"
    content = "\n".join(
        [
            '[10:00:00.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=5000 eHP=900000 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:02.000] HP_V2 #2 hit=200 cum=200 raw=200.00 pHP=5000 eHP=899800 skill="chr_0004_pelica_attack1" hits=1 src=chr_0004_pelica tgt=eny_0051_rodin atk=chr_0004_pelica seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:15.000] BUFF_START #3 id="buff_chr_0003_endminf_talent_1" uid=9 owner=chr_0003_endminf src=chr_0003_endminf dur=9999.00 lifeT=9999.00 passed=0.00 enh=1',
        ]
    )
    proof = build_raw_log_proof(
        content.encode("utf-8"),
        file_name=log_path.name,
        meta={
            "first_hit_ts": "10-00-00-000",
            "last_hit_ts": "10-00-02-000",
            "hit_count": 2,
            "loadout": [
                {
                    "slot": 0,
                    "char_key": "chr_0003_endminf",
                    "char_name": "管理员",
                    "potential": 2,
                    "equips": [],
                },
                {
                    "slot": 1,
                    "char_key": "chr_0027_tangtang",
                    "char_name": "汤汤",
                    "potential": 0,
                    "weapon_name": "落草",
                    "weapon_template": "wpn_pistol_0011",
                    "weapon_level": 90,
                    "weapon_refine": 0,
                    "equips": [],
                },
                {
                    "slot": 2,
                    "char_key": "chr_0004_pelica",
                    "char_name": "佩丽卡",
                    "potential": 5,
                    "weapon_name": "悼亡诗",
                    "weapon_template": "wpn_funnel_0005",
                    "weapon_level": 1,
                    "weapon_refine": 5,
                    "equips": [],
                },
            ],
        },
    )
    embedded = (
        content
        + f"{RAW_LOG_PROOF_BEGIN_PREFIX}0\n"
        + json.dumps(proof, ensure_ascii=False, indent=2, sort_keys=True)
        + f"\n{RAW_LOG_PROOF_END}\n"
    )
    log_path.write_bytes(embedded.encode("utf-8"))

    payload = build_battle_upload_payload_from_log(str(log_path))

    assert payload["battle"]["durationMs"] == 2000
    assert [entry["characterKey"] for entry in payload["battle"]["roster"]] == [
        "chr_0027_tangtang",
        "chr_0004_pelica",
    ]
    assert [entry["characterKey"] for entry in payload["participants"]] == [
        "chr_0004_pelica",
        "chr_0027_tangtang",
    ]
    assert all(
        event["tsMsFromStart"] <= payload["battle"]["durationMs"]
        for event in payload["timelineEvents"]
    )
    assert all(
        event.get("sourceCharacterKey") != "chr_0003_endminf"
        and event.get("targetCharacterKey") != "chr_0003_endminf"
        for event in payload["timelineEvents"]
    )


def test_build_battle_upload_payload_maps_admin_canonical_loadout_to_endmin_variant(tmp_path) -> None:
    log_path = tmp_path / "sample_admin_weapon.log"
    content = "\n".join(
        [
            '[10:00:00.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=5000 eHP=900000 skill="chr_0003_endminf_attack1" hits=1 src=chr_0003_endminf tgt=eny_0051_rodin atk=chr_0003_endminf seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:01.000] HP_V2 #2 hit=200 cum=300 raw=200.00 pHP=5000 eHP=899800 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0051_rodin atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )
    proof = build_raw_log_proof(
        content.encode("utf-8"),
        file_name=log_path.name,
        meta={
            "first_hit_ts": "10-00-00-000",
            "last_hit_ts": "10-00-01-000",
            "hit_count": 2,
            "loadout": [
                {
                    "slot": 0,
                    "char_key": "chr_9000_endmin",
                    "char_name": "管理员",
                    "weapon_name": "测试武器",
                    "weapon_template": "wpn_funnel_0008",
                    "weapon_level": 90,
                    "weapon_refine": 1,
                    "equips": [],
                },
                {
                    "slot": 1,
                    "char_key": "chr_0027_tangtang",
                    "char_name": "汤汤",
                    "weapon_name": "落草",
                    "weapon_template": "wpn_pistol_0011",
                    "weapon_level": 90,
                    "weapon_refine": 0,
                    "equips": [],
                },
            ],
        },
    )
    embedded = (
        content
        + f"{RAW_LOG_PROOF_BEGIN_PREFIX}0\n"
        + json.dumps(proof, ensure_ascii=False, indent=2, sort_keys=True)
        + f"\n{RAW_LOG_PROOF_END}\n"
    )
    log_path.write_bytes(embedded.encode("utf-8"))

    payload = build_battle_upload_payload_from_log(str(log_path))
    roster_by_key = {entry["characterKey"]: entry for entry in payload["battle"]["roster"]}

    assert roster_by_key["chr_0003_endminf"]["characterName"] == "管理员"
    assert roster_by_key["chr_0003_endminf"]["weapon"]["weaponTemplate"] == "wpn_funnel_0008"
    assert roster_by_key["chr_0003_endminf"]["weapon"]["weaponRefine"] == 1
