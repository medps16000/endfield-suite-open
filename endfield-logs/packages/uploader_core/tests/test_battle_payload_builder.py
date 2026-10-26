from __future__ import annotations

import uploader_core.battle_payload_builder as builder
from uploader_core.battle_payload_builder import (
    _canonical_character_keys,
    _split_trace_into_battles,
    build_battle_upload_payloads_from_log,
)


def test_split_trace_keeps_short_post_timer_tail() -> None:
    raw = "\n".join(
        [
            "[10:00:00.000] OFFICIAL_TIMER_START source=SC_GAME_MECHANICS_SYNC_CHALLENGE_START gameId=indie_hard011_s challengeStartTs=1000",
            "[10:00:00.000] GAME_TIMER_START seq=1 source=PacketBattleState startMs=1000 expireMs=0 official=1",
            '[10:00:05.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=0 eHP=900 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0090_wgabyss atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:10.000] OFFICIAL_TIMER_END source=SC_GAME_MECHANICS_SYNC_CHALLENGE_COMPLETE gameId=indie_hard011_s isPass=1 passTime=10000",
            "[10:00:10.050] GAME_TIMER_END seq=1 source=PacketBattleState elapsedMs=10000 startMs=1000 endMs=10000 expireMs=0 sane=1 official=1 packetElapsedMs=10000",
            '[10:00:12.000] BUFF_END #1 id="buff_chr_0027_tangtang_test" uid=42',
            "[10:00:13.000] BB[42]: extraDamageRate=0.1500",
            "[10:00:21.000] DUNGEON_CONTEXT dungeonId=indie_hard011_s source=SC_SELF_SCENE_INFO",
        ]
    )

    segments = _split_trace_into_battles(raw)

    assert len(segments) == 1
    assert 'BUFF_END #1 id="buff_chr_0027_tangtang_test"' in segments[0]["content"]
    assert "BB[42]: extraDamageRate=0.1500" in segments[0]["content"]
    assert "10:00:21.000" not in segments[0]["content"]


def test_split_trace_post_timer_tail_does_not_cross_next_start() -> None:
    raw = "\n".join(
        [
            "[10:00:00.000] OFFICIAL_TIMER_START source=SC_GAME_MECHANICS_SYNC_CHALLENGE_START gameId=indie_hard011_s challengeStartTs=1000",
            '[10:00:01.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=0 eHP=900 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0090_wgabyss atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:05.000] OFFICIAL_TIMER_END source=SC_GAME_MECHANICS_SYNC_CHALLENGE_COMPLETE gameId=indie_hard011_s isPass=1 passTime=5000",
            '[10:00:06.000] BUFF_END #1 id="buff_chr_0027_tangtang_test" uid=42',
            "[10:00:07.000] OFFICIAL_TIMER_START source=SC_GAME_MECHANICS_SYNC_CHALLENGE_START gameId=indie_hard012_s challengeStartTs=7000",
            '[10:00:08.000] HP_V2 #2 hit=200 cum=200 raw=200.00 pHP=0 eHP=800 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0090_wgabyss atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:12.000] OFFICIAL_TIMER_END source=SC_GAME_MECHANICS_SYNC_CHALLENGE_COMPLETE gameId=indie_hard012_s isPass=1 passTime=5000",
        ]
    )

    segments = _split_trace_into_battles(raw)

    assert len(segments) == 2
    assert "BUFF_END #1" in segments[0]["content"]
    assert "10:00:07.000" not in segments[0]["content"]


def test_split_trace_keeps_phases_together_until_explicit_boundary() -> None:
    raw = "\n".join(
        [
            "[10:00:00.000] OFFICIAL_TIMER_START source=SC_GAME_MECHANICS_SYNC_CHALLENGE_START gameId=indie_hard011_s challengeStartTs=0",
            "[10:00:00.100] GAME_TIMER_START seq=1 source=PacketBattleState startMs=0 expireMs=0 official=1",
            '[10:00:01.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=0 eHP=900 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0090_wgabyss atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:05.000] GAME_TIMER_END seq=1 source=PacketBattleState elapsedMs=5000 startMs=0 endMs=5000 expireMs=0 sane=1 official=1",
            "[10:00:10.000] OFFICIAL_TIMER_START source=SC_GAME_MECHANICS_SYNC_CHALLENGE_START gameId=indie_hard012_s challengeStartTs=10000",
            "[10:00:10.100] GAME_TIMER_START seq=2 source=PacketBattleState startMs=10000 expireMs=0 official=1",
            '[10:00:11.000] HP_V2 #2 hit=200 cum=200 raw=200.00 pHP=0 eHP=800 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0090_wgabyss atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:15.000] GAME_TIMER_END seq=2 source=PacketBattleState elapsedMs=5000 startMs=10000 endMs=15000 expireMs=0 sane=1 official=1",
            "[10:01:00.000] GAME_TIMER_START seq=3 source=PacketBattleState startMs=60000 expireMs=0 official=1",
            '[10:01:01.000] HP_V2 #3 hit=300 cum=300 raw=300.00 pHP=0 eHP=700 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0090_wgabyss atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:01:05.000] GAME_TIMER_END seq=3 source=PacketBattleState elapsedMs=5000 startMs=60000 endMs=65000 expireMs=0 sane=1 official=1",
        ]
    )

    segments = _split_trace_into_battles(raw)

    assert len(segments) == 2
    assert "HP_V2 #1" in segments[0]["content"]
    assert "HP_V2 #2" in segments[1]["content"]
    assert "HP_V2 #3" in segments[1]["content"]
    reset = '[10:00:16.000] GAME_INST_LEAVE source=SC_GAME_MECHANICS_SYNC_LEAVE_GAME_INST gameId=indie_hard012_s\n'
    ended = raw.replace('[10:01:00.000]', reset + '[10:01:00.000]')
    segments = _split_trace_into_battles(ended)
    assert len(segments) == 3
    assert "HP_V2 #3" in segments[2]["content"]
    assert "HP_V2 #3" not in segments[1]["content"]


def test_split_trace_post_timer_tail_stops_before_next_hit_without_start_marker() -> None:
    raw = "\n".join(
        [
            '[10:00:01.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=0 eHP=900 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0090_wgabyss atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:05.000] GAME_TIMER_END seq=1 source=ChallengeComplete elapsedMs=5000 startMs=0 expireMs=0 sane=1",
            '[10:00:06.000] BUFF_END #1 id="buff_chr_0027_tangtang_test" uid=42',
            '[10:00:08.000] HP_V2 #2 hit=200 cum=200 raw=200.00 pHP=0 eHP=800 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0090_wgabyss atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:10.000] GAME_TIMER_END seq=2 source=ChallengeComplete elapsedMs=5000 startMs=0 expireMs=0 sane=1",
        ]
    )

    segments = _split_trace_into_battles(raw)

    assert len(segments) == 2
    assert "BUFF_END #1" in segments[0]["content"]
    assert "HP_V2 #2" not in segments[0]["content"]


def test_split_trace_does_not_duplicate_previous_hits_for_empty_start_marker() -> None:
    raw = "\n".join(
        [
            "[10:00:00.000] OFFICIAL_TIMER_START source=SC_GAME_MECHANICS_SYNC_CHALLENGE_START gameId=indie_hard011_s challengeStartTs=0",
            "[10:00:00.100] DUNGEON_CONTEXT dungeonId=indie_hard011_s source=SC_SELF_SCENE_INFO",
            "[10:00:00.200] LOADOUT slot=0 char=chr_0027_tangtang weaponTemplate=wpn_sword_0006 equipSuit={}",
            '[10:00:01.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=0 eHP=900 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0090_wgabyss atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:05.000] GAME_TIMER_END seq=1 source=PacketBattleState elapsedMs=5000 startMs=0 endMs=5000 expireMs=0 sane=1 official=1",
            "[10:00:06.000] DUNGEON_CONTEXT dungeonId=indie_hard011_s source=SC_SELF_SCENE_INFO",
            "[10:00:07.000] OFFICIAL_TIMER_START source=SC_GAME_MECHANICS_SYNC_CHALLENGE_START gameId=indie_hard011_s challengeStartTs=7000",
            "[10:00:08.000] DUNGEON_CONTEXT dungeonId=indie_hard012_s source=SC_SELF_SCENE_INFO",
            "[10:00:09.000] OFFICIAL_TIMER_START source=SC_GAME_MECHANICS_SYNC_CHALLENGE_START gameId=indie_hard012_s challengeStartTs=9000",
            '[10:00:10.000] HP_V2 #2 hit=200 cum=200 raw=200.00 pHP=0 eHP=800 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0090_wgabyss atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:15.000] GAME_TIMER_END seq=2 source=PacketBattleState elapsedMs=6000 startMs=9000 endMs=15000 expireMs=0 sane=1 official=1",
        ]
    )

    segments = _split_trace_into_battles(raw)

    assert len(segments) == 2
    assert "HP_V2 #1" in segments[0]["content"]
    assert "HP_V2 #2" not in segments[0]["content"]
    assert "HP_V2 #2" in segments[1]["content"]
    assert "HP_V2 #1" not in segments[1]["content"]


def test_build_payloads_skip_segments_without_uploadable_damage(tmp_path) -> None:
    raw = "\n".join(
        [
            "[10:00:00.000] OFFICIAL_TIMER_START source=SC_GAME_MECHANICS_SYNC_CHALLENGE_START gameId=indie_hard011_s challengeStartTs=0",
            '[10:00:01.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=1000 eHP=0 skill="eny_0090_wgabyss_attack1" hits=1 src=eny_0090_wgabyss tgt=chr_0027_tangtang atk=eny_0090_wgabyss seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:05.000] GAME_TIMER_END seq=1 source=PacketBattleState elapsedMs=5000 startMs=0 endMs=5000 expireMs=0 sane=1 official=1",
            "[10:00:10.000] OFFICIAL_TIMER_START source=SC_GAME_MECHANICS_SYNC_CHALLENGE_START gameId=indie_hard012_s challengeStartTs=10000",
            '[10:00:11.000] HP_V2 #2 hit=200 cum=200 raw=200.00 pHP=0 eHP=800 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0090_wgabyss atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:15.000] GAME_TIMER_END seq=2 source=PacketBattleState elapsedMs=5000 startMs=10000 endMs=15000 expireMs=0 sane=1 official=1",
        ]
    )
    log_path = tmp_path / "raw.log"
    log_path.write_text(raw, encoding="utf-8")

    payloads = build_battle_upload_payloads_from_log(str(log_path))

    assert len(payloads) == 1
    assert payloads[0]["battle"]["totalDamage"] == 200


def test_build_payloads_resolve_v1d3_hard_official_timer_game_id(tmp_path) -> None:
    raw = "\n".join(
        [
            "[10:00:00.000] OFFICIAL_TIMER_START source=SC_GAME_MECHANICS_SYNC_CHALLENGE_START gameId=indie_hard018_s challengeStartTs=1000 challengeExpireTs=61000",
            '[10:00:10.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=5000 eHP=100 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0082_hsbear_hdg018 atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            '[10:00:20.000] HP_V2 #2 hit=200 cum=200 raw=200.00 pHP=5000 eHP=0 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0082_hsbear_hdg018 atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:40.440] OFFICIAL_TIMER_END source=SC_GAME_MECHANICS_SYNC_CHALLENGE_COMPLETE gameId=indie_hard018_s isPass=1 passTime=39440",
        ]
    )
    log_path = tmp_path / "raw_v1d3_hard.log"
    log_path.write_text(raw, encoding="utf-8")

    payloads = build_battle_upload_payloads_from_log(str(log_path))

    assert len(payloads) == 1
    assert payloads[0]["battle"]["dungeonKey"] == "indie_hard018_s"
    assert payloads[0]["battle"]["dungeonName"] == "忿鼓咆声·苦难"
    assert payloads[0]["battle"]["bossKey"] == "eny_0082_hsbear_hdg018"


def test_build_payload_marks_hp_zero_without_timer_end_uncleared(tmp_path) -> None:
    raw = "\n".join(
        [
            "[10:00:00.000] GAME_TIMER_START seq=1 source=PacketBattleState startMs=0 expireMs=0 official=1",
            '[10:00:01.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=0 eHP=0 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0090_wgabyss atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
        ]
    )
    log_path = tmp_path / "raw.log"
    log_path.write_text(raw, encoding="utf-8")

    payloads = build_battle_upload_payloads_from_log(str(log_path))

    assert len(payloads) == 1
    assert payloads[0]["battle"]["timerEndSeen"] is False
    assert payloads[0]["battle"]["clearFlag"] is False


def test_build_payload_keeps_war_echo_pass_result_without_official_complete_unfinished(tmp_path) -> None:
    raw = "\n".join(
        [
            "[10:00:00.000] OFFICIAL_TIMER_START source=SC_GAME_MECHANICS_SYNC_CHALLENGE_START gameId=indie_battletower007_ex challengeStartTs=1000",
            '[10:00:02.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=0 eHP=900000 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0082_hsbear atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:04.000] GAME_TIMER_END seq=0 source=SceneSetBattleFallback elapsedMs=4000 startMs=0 endMs=0 expireMs=0 sane=1 official=1 fallback=1 isPass=0",
            "[10:00:04.100] BATTLE_RESULT source=SC_SELF_SCENE_INFO dungeonId=indie_battletower007_ex isCalc=1 isPass=1",
            "[10:00:04.100] DUNGEON_CONTEXT dungeonId=indie_battletower007_ex source=SC_SELF_SCENE_INFO isCalc=1 isPass=1",
        ]
    )
    log_path = tmp_path / "war_echo_pass.log"
    log_path.write_text(raw, encoding="utf-8")

    payloads = build_battle_upload_payloads_from_log(str(log_path))

    assert len(payloads) == 1
    assert payloads[0]["battle"]["timerEndSeen"] is False
    assert payloads[0]["battle"]["officialTimerEndSeen"] is False
    assert payloads[0]["battle"]["clearFlag"] is False


def test_build_payload_trusts_official_pass_when_final_hp_is_not_zero(tmp_path) -> None:
    raw = "\n".join(
        [
            "[10:00:00.000] OFFICIAL_TIMER_START source=SC_GAME_MECHANICS_SYNC_CHALLENGE_START gameId=indie_battletower007_ex challengeStartTs=1000",
            '[10:00:02.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=0 eHP=900000 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0082_hsbear atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:04.000] OFFICIAL_TIMER_END source=SC_GAME_MECHANICS_SYNC_CHALLENGE_COMPLETE gameId=indie_battletower007_ex isPass=1 passTime=4000",
        ]
    )
    log_path = tmp_path / "war_echo_official_pass.log"
    log_path.write_text(raw, encoding="utf-8")

    payloads = build_battle_upload_payloads_from_log(str(log_path))

    assert len(payloads) == 1
    assert payloads[0]["battle"]["officialTimerEndSeen"] is True
    assert payloads[0]["battle"]["clearFlag"] is True


def test_build_payload_keeps_war_echo_multi_wave_damage_in_one_completed_run(tmp_path) -> None:
    raw = "\n".join(
        [
            "[10:00:00.000] DUNGEON_CONTEXT dungeonId=indie_battletower004_ex source=SC_SELF_SCENE_INFO",
            "[10:00:00.100] GAME_TIMER_START seq=1 source=PacketBattleState startMs=100 expireMs=0 official=1",
            '[10:00:02.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=0 eHP=900 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0068_lbtough2 atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:04.000] OFFICIAL_TIMER_AWAIT source=BattleOpModifyBattleState gameId=indie_battletower004_ex officialStartSeen=0",
            "[10:00:05.000] BATTLE_PHASE_START seq=2 source=BattleOpModifyBattleState gameId=indie_battletower004_ex",
            '[10:00:07.000] HP_V2 #2 hit=200 cum=300 raw=200.00 pHP=0 eHP=700 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0068_lbtough2 atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:09.500] OFFICIAL_TIMER_END source=SC_GAME_MECHANICS_SYNC_COMPLETION_REWARD gameId=indie_battletower004_ex isPass=1 passTime=9400",
        ]
    )
    log_path = tmp_path / "war_echo_multi_wave.log"
    log_path.write_text(raw, encoding="utf-8")

    payloads = build_battle_upload_payloads_from_log(str(log_path))

    assert len(payloads) == 1
    assert payloads[0]["battle"]["dungeonKey"] == "indie_battletower004_ex"
    assert payloads[0]["battle"]["totalDamage"] == 300
    assert payloads[0]["battle"]["durationMs"] == 9400
    assert payloads[0]["battle"]["officialTimerEndSeen"] is True
    assert payloads[0]["battle"]["clearFlag"] is True


def test_build_payload_keeps_war_echo_exit_without_pass_uncleared(tmp_path) -> None:
    raw = "\n".join(
        [
            "[10:00:00.000] OFFICIAL_TIMER_START source=SC_GAME_MECHANICS_SYNC_CHALLENGE_START gameId=indie_battletower007_ex challengeStartTs=1000",
            '[10:00:02.000] HP_V2 #1 hit=100 cum=100 raw=100.00 pHP=0 eHP=900000 skill="chr_0027_tangtang_attack1" hits=1 src=chr_0027_tangtang tgt=eny_0082_hsbear atk=chr_0027_tangtang seg=0 shared=2 critFlag=0 critDmg=0.5000',
            "[10:00:04.000] GAME_TIMER_END seq=0 source=SceneSetBattleFallback elapsedMs=4000 startMs=0 endMs=0 expireMs=0 sane=1 official=1 fallback=1 isPass=0",
        ]
    )
    log_path = tmp_path / "war_echo_exit.log"
    log_path.write_text(raw, encoding="utf-8")

    payloads = build_battle_upload_payloads_from_log(str(log_path))

    assert len(payloads) == 1
    assert payloads[0]["battle"]["timerEndSeen"] is False
    assert payloads[0]["battle"]["clearFlag"] is False


def test_canonical_roster_ignores_buff_only_sources() -> None:
    parsed = {
        "battle": {
            "duration_ms": 20000,
            "roster": [
                {"character_key": "chr_0030_zhuangfy", "character_name": "庄方宜"},
                {"character_key": "chr_0019_karin", "character_name": "秋栗"},
            ],
        },
        "participants": [
            {"character_key": "chr_0030_zhuangfy", "total_damage": 1000},
            {"character_key": "chr_0019_karin", "total_damage": 0, "rdps": 50.0},
        ],
        "role_skill_stats": [],
        "timeline_events": [
            {
                "ts_ms_from_start": 1000,
                "lane_type": "skill",
                "source_character_key": "chr_0030_zhuangfy",
                "target_character_key": "eny_0051_rodin",
            },
            {
                "ts_ms_from_start": 2000,
                "lane_type": "buff",
                "source_character_key": "chr_0013_aglina",
                "target_character_key": "chr_0030_zhuangfy",
            },
        ],
    }
    loadout_entries = [
        {"slot": 1, "char_key": "chr_0030_zhuangfy"},
        {"slot": 2, "char_key": "chr_0019_karin"},
        {"slot": 3, "char_key": "chr_0013_aglina"},
    ]

    assert _canonical_character_keys(parsed, loadout_entries=loadout_entries) == [
        "chr_0030_zhuangfy",
        "chr_0019_karin",
    ]


def test_build_payload_preserves_contract_tags(monkeypatch) -> None:
    parsed = {
        "battle": {
            "dungeon_key": "indie_group_ccdg",
            "dungeon_name": "危机合约",
            "boss_key": "eny_0114_jzmking",
            "boss_name": "破潮之像",
            "battle_start_at": "2026-06-18T12:00:00+08:00",
            "battle_end_at": "2026-06-18T12:01:28+08:00",
            "duration_ms": 88000,
            "clear_flag": True,
            "total_damage": 1200000,
            "total_dps": 13636.36,
            "battle_fingerprint": "contract-tags-test",
            "parser_version": "test",
            "rules_version": "test",
            "roster": [{"character_key": "chr_0030_zhuangfy", "character_name": "庄方宜"}],
            "contract_tags": [
                {
                    "tag_id": "102802",
                    "score": "2",
                    "tagName": "角色攻击下降",
                    "icon": "icon_activity_contract_tag_303",
                    "buff_id": "global_buff_cc_chr_main_attribute_down",
                    "group_id": "10",
                    "terms": [{"key": "attack", "op": "down"}],
                    "values": {"rate": 0.25},
                },
                {"tagId": 900103, "score": 3, "description": "首领攻击提升"},
            ],
        },
        "participants": [
            {
                "character_key": "chr_0030_zhuangfy",
                "character_name": "庄方宜",
                "total_damage": 1200000,
                "dps": 13636.36,
                "rdps": 13636.36,
                "max_hit": 50000,
                "crit_rate": 0.42,
            }
        ],
        "character_states": [],
        "timeline_events": [],
        "role_skill_stats": [],
        "loadout": [],
    }

    monkeypatch.setattr(builder, "parse_upload_battle_log_text", lambda *_args, **_kwargs: parsed)

    payload = builder._build_payload_from_segment(
        segment={"content": "raw"},
        source_file_name="raw.log",
        reference_date=None,
        proof=None,
    )

    assert payload["battle"]["contractTagScore"] == 5
    assert payload["battle"]["contractTags"][0] == {
        "tagId": 102802,
        "score": 2,
        "name": "角色攻击下降",
        "description": None,
        "iconId": "icon_activity_contract_tag_303",
        "iconUrl": "/images/contract-tag/icon_activity_contract_tag_303.png",
        "buffId": "global_buff_cc_chr_main_attribute_down",
        "groupId": 10,
        "conflictId": None,
        "terms": [{"key": "attack", "op": "down"}],
        "values": {"rate": 0.25},
    }
    assert payload["battle"]["contractTags"][1]["description"] == "首领攻击提升"


def test_build_payload_preserves_timeline_poise_damage(monkeypatch) -> None:
    parsed = {
        "battle": {
            "dungeon_key": "indie_hard011_s",
            "dungeon_name": "测试副本",
            "boss_key": "eny_0051_rodin",
            "boss_name": "测试首领",
            "battle_start_at": "2026-06-20T12:00:00+08:00",
            "battle_end_at": "2026-06-20T12:00:02+08:00",
            "duration_ms": 2000,
            "clear_flag": True,
            "total_damage": 600,
            "total_dps": 300.0,
            "battle_fingerprint": "poise-damage-test",
            "parser_version": "test",
            "rules_version": "test",
            "roster": [{"character_key": "chr_0028_wulfa", "character_name": "佩丽卡"}],
        },
        "participants": [
            {
                "character_key": "chr_0028_wulfa",
                "character_name": "佩丽卡",
                "total_damage": 600,
                "dps": 300.0,
                "rdps": 300.0,
                "max_hit": 600,
                "crit_rate": 0.0,
            }
        ],
        "character_states": [],
        "timeline_events": [
            {
                "ts_ms_from_start": 1000,
                "lane_type": "skill",
                "source_character_key": "chr_0028_wulfa",
                "source_character_name": "佩丽卡",
                "target_character_key": "eny_0051_rodin",
                "target_character_name": "测试首领",
                "event_type": "damage",
                "event_key": "chr_0028_wulfa_attack5",
                "event_name": "A5",
                "value": 600,
                "poise_damage": {
                    "type": "PoiseDamage",
                    "value": -18.0,
                    "current_value": 262.0,
                    "source": "chr_0028_wulfa_attack5",
                    "source_int": 1997,
                    "orig_source": "chr_0028_wulfa_attack5",
                    "orig_source_int": 1997,
                },
                "duration_ms": None,
                "important": True,
            }
        ],
        "role_skill_stats": [],
        "loadout": [],
    }

    monkeypatch.setattr(builder, "parse_upload_battle_log_text", lambda *_args, **_kwargs: parsed)

    payload = builder._build_payload_from_segment(
        segment={"content": "raw"},
        source_file_name="raw.log",
        reference_date=None,
        proof=None,
    )

    assert payload["timelineEvents"][0]["poiseDamage"] == parsed["timeline_events"][0]["poise_damage"]


def test_local_game_catalog_overrides_new_weapon_and_equip_names() -> None:
    builder._load_weapon_catalog.cache_clear()
    builder._load_equip_catalog.cache_clear()

    weapons = builder._load_weapon_catalog()
    equips = builder._load_equip_catalog()

    assert weapons["wpn_funnel_0016"]["weaponName"] == "四二式·肃阵"
    assert weapons["wpn_funnel_0018"]["weaponName"] == "联结点"
    assert weapons["wpn_lance_0016"]["weaponName"] == "黄金时代"
    assert weapons["wpn_sword_0026"]["weaponName"] == "遥望"
    assert equips["item_equip_t4_parts_wuling00_body_01"]["pieceName"] == "集成实训护甲"
    assert equips["item_equip_t4_suit_usp02_hand_04"]["pieceName"] == "长息手套"


def test_payload_omits_source_skills_and_derived_refine_when_weapon_template_mismatches() -> None:
    loadouts = builder._build_loadout_by_character_key(
        None,
        loadout_entries=[
            {
                "char_key": "chr_0032_lizhiyan",
                "weapon_template": "wpn_funnel_0008",
                "weapon_name": "爆破单元",
                "weapon_refine": 5,
                "weapon_refine_source": "source_skill",
                # 3629 resolves to sk_wpn_funnel_0016, not the 0008 main skill.
                "weapon_source_skills": [
                    {"skill_id": 3629, "level": 5, "potential_level": 0}
                ],
            }
        ],
    )

    weapon = loadouts["chr_0032_lizhiyan"]["weapon"]
    assert weapon["weaponTemplate"] == "wpn_funnel_0008"
    assert weapon["skills"] == []
    assert weapon["weaponRefine"] is None


def test_payload_keeps_source_skills_when_weapon_template_matches() -> None:
    loadouts = builder._build_loadout_by_character_key(
        None,
        loadout_entries=[
            {
                "char_key": "chr_0032_lizhiyan",
                "weapon_template": "wpn_funnel_0008",
                "weapon_name": "爆破单元",
                "weapon_refine": 5,
                "weapon_refine_source": "source_skill",
                "weapon_source_skills": [
                    {"skill_id": 2228, "level": 5, "potential_level": 0}
                ],
            }
        ],
    )

    weapon = loadouts["chr_0032_lizhiyan"]["weapon"]
    assert weapon["skills"] == [
        {"skillKey": "sk_wpn_funnel_0008", "level": 5, "potentialLevel": 0}
    ]
    assert weapon["weaponRefine"] == 5


def test_raw_loadout_history_repairs_cross_character_weapon_swap() -> None:
    raw = "\n".join(
        [
            "[10:00:00.000] LOADOUT reason=SC_SYNC_CHAR_BAG_INFO slotCount=2 memberCount=2 roster=[]",
            "[10:00:00.000] LOADOUT slot=0 char=chr_0011_seraph template=chr_0011_seraph potential=5 weaponInstId=weapon_a weaponTemplate=wpn_funnel_0008 refine=5 attachedGem=gem_a skillIntIds=[101,2228] equipSuit={}",
            "[10:00:00.000] LOADOUT_STATS slot=0 char=chr_0011_seraph weaponInstId=weapon_a weaponTemplate=wpn_funnel_0008 weaponRefineStats={2228:level=9:potentialLv=5:bb={}} weaponSourceSkills={2228:level=9:potentialLv=5:bb={}} gemInstId=gem_a gemTemplate=1070 gemTerms={}",
            "[10:00:00.000] LOADOUT slot=1 char=chr_0032_lizhiyan template=chr_0032_lizhiyan potential=0 weaponInstId=weapon_b weaponTemplate=wpn_funnel_0016 refine=0 attachedGem=gem_b skillIntIds=[202,3629] equipSuit={}",
            "[10:00:00.000] LOADOUT_STATS slot=1 char=chr_0032_lizhiyan weaponInstId=weapon_b weaponTemplate=wpn_funnel_0016 weaponRefineStats={3629:level=4:potentialLv=0:bb={}} weaponSourceSkills={3629:level=4:potentialLv=0:bb={}} gemInstId=gem_b gemTemplate=1070 gemTerms={}",
            "[10:01:00.000] LOADOUT reason=SC_WEAPON_PUTON slotCount=2 memberCount=2 roster=[]",
            # Legacy cached owner remains on A; receiver points to A but keeps B's source skill.
            "[10:01:00.000] LOADOUT slot=0 char=chr_0011_seraph template=chr_0011_seraph potential=5 weaponInstId=weapon_a weaponTemplate=wpn_funnel_0008 refine=5 attachedGem=gem_a skillIntIds=[101,2228] equipSuit={}",
            "[10:01:00.000] LOADOUT_STATS slot=0 char=chr_0011_seraph weaponInstId=weapon_a weaponTemplate=wpn_funnel_0008 weaponRefineStats={2228:level=9:potentialLv=5:bb={}} weaponSourceSkills={2228:level=9:potentialLv=5:bb={}} gemInstId=gem_a gemTemplate=1070 gemTerms={}",
            "[10:01:00.000] LOADOUT slot=1 char=chr_0032_lizhiyan template=chr_0032_lizhiyan potential=0 weaponInstId=weapon_a weaponTemplate=wpn_funnel_0008 refine=0 attachedGem=gem_a skillIntIds=[202,3629] equipSuit={}",
            "[10:01:00.000] LOADOUT_STATS slot=1 char=chr_0032_lizhiyan weaponInstId=weapon_a weaponTemplate=wpn_funnel_0008 weaponRefineStats={3629:level=4:potentialLv=0:bb={}} weaponSourceSkills={3629:level=4:potentialLv=0:bb={}} gemInstId=gem_a gemTemplate=1070 gemTerms={}",
        ]
    )

    groups, fallback = builder._raw_text_loadout_state(raw)
    rows = {
        row["character_key"]: row
        for row in builder._raw_loadout_entries_for_segment(
            groups,
            fallback,
            {"first_hit_line_index": 999},
        )
    }

    assert rows["chr_0011_seraph"]["weapon_inst_id"] == "weapon_b"
    assert rows["chr_0011_seraph"]["weapon_template"] == "wpn_funnel_0016"
    assert rows["chr_0011_seraph"]["weapon_refine"] == 0
    assert rows["chr_0011_seraph"]["weapon_source_skills"][0]["skill_id"] == "3629"
    assert rows["chr_0032_lizhiyan"]["weapon_inst_id"] == "weapon_a"
    assert rows["chr_0032_lizhiyan"]["weapon_template"] == "wpn_funnel_0008"
    assert rows["chr_0032_lizhiyan"]["weapon_refine"] == 5
    assert rows["chr_0032_lizhiyan"]["weapon_source_skills"][0]["skill_id"] == "2228"


def test_raw_loadout_history_fails_closed_for_unrecoverable_bag_weapon_change() -> None:
    raw = "\n".join(
        [
            "[10:00:00.000] LOADOUT reason=SC_SYNC_CHAR_BAG_INFO slotCount=1 memberCount=1 roster=[]",
            "[10:00:00.000] LOADOUT slot=0 char=chr_0004_pelica template=chr_0004_pelica potential=5 weaponInstId=weapon_old weaponTemplate=wpn_funnel_0005 refine=5 skillIntIds=[101,2217] equipSuit={}",
            "[10:00:00.000] LOADOUT_STATS slot=0 char=chr_0004_pelica weaponInstId=weapon_old weaponTemplate=wpn_funnel_0005 weaponRefineStats={2217:level=9:potentialLv=5:bb={}} weaponSourceSkills={2217:level=9:potentialLv=5:bb={}} gemTerms={}",
            "[10:01:00.000] LOADOUT reason=SC_WEAPON_PUTON slotCount=1 memberCount=1 roster=[]",
            "[10:01:00.000] LOADOUT slot=0 char=chr_0004_pelica template=chr_0004_pelica potential=5 weaponInstId=weapon_new weaponTemplate=wpn_funnel_0014 refine=5 skillIntIds=[101,2217] equipSuit={}",
            "[10:01:00.000] LOADOUT_STATS slot=0 char=chr_0004_pelica weaponInstId=weapon_new weaponTemplate=wpn_funnel_0014 weaponRefineStats={2217:level=9:potentialLv=5:bb={}} weaponSourceSkills={2217:level=9:potentialLv=5:bb={}} gemTerms={}",
        ]
    )

    groups, fallback = builder._raw_text_loadout_state(raw)
    row = builder._raw_loadout_entries_for_segment(
        groups,
        fallback,
        {"first_hit_line_index": 999},
    )[0]

    assert row["weapon_template"] == "wpn_funnel_0014"
    assert row["weapon_source_skills"] == []
    assert row["weapon_refine_stats"] == []
    # The slot-level refine remains usable; only source-skill-derived refine
    # is cleared together with contradictory source skills.
    assert row["weapon_refine"] == 5
