from endfield_pcap.trace_bridge import TraceBridge


def _damage_event(target_id: int = 100804) -> dict:
    return {
        "type": "BattleOpTriggerAction",
        "timestamp_ms": 2_000,
        "owner_id": 100701,
        "template_str_id": "chr_0027_tangtang_attack1",
        "action": {
            "action_type": "BattleActionDamage",
            "damage_action": {
                "attacker_id": 100701,
                "details": [
                    {
                        "target_id": target_id,
                        "value": 1234,
                        "cur_hp": 5678,
                        "is_crit": False,
                    }
                ],
            },
        },
    }


def test_damage_target_stays_unresolved_with_only_dungeon_context(tmp_path):
    trace_path = tmp_path / "trace.log"
    bridge = TraceBridge(trace_path)
    bridge.id_to_name[100701] = "chr_0027_tangtang"

    bridge.handle_event(
        {
            "type": "DUNGEON_CONTEXT",
            "timestamp_ms": 1_000,
            "dungeon_id": "dung02_bossrush02_03",
        }
    )
    bridge.handle_event(_damage_event())

    lines = trace_path.read_text(encoding="utf-8").splitlines()
    hp_line = next(line for line in lines if " HP_V2 " in line)
    assert "tgt=id_100804" in hp_line
    assert "tgtId=100804" in hp_line


def test_damage_trace_preserves_packet_instance_and_sequence(tmp_path):
    path = tmp_path / 'identity.log'
    bridge = TraceBridge(path)
    bridge.id_to_name[100701] = 'chr_0027_tangtang'
    event = _damage_event()
    event.update(inst_id=2305843009213695858, seq_id=2377, client_tick_tms=35597)
    bridge.handle_event(event)
    line = next(line for line in path.read_text(encoding='utf-8').splitlines() if ' HP_V2 ' in line)
    assert 'eventInstId=2305843009213695858 eventOwnerId=100701 packetSeq=2377 clientTick=35597' in line


def test_damage_target_stays_unresolved_with_only_battletower_context(tmp_path):
    trace_path = tmp_path / "trace.log"
    bridge = TraceBridge(trace_path)
    bridge.id_to_name[100701] = "chr_0027_tangtang"

    bridge.handle_event(
        {
            "type": "DUNGEON_CONTEXT",
            "timestamp_ms": 1_000,
            "dungeon_id": "indie_battletower007_ex",
        }
    )
    bridge.handle_event(_damage_event())

    hp_line = next(
        line
        for line in trace_path.read_text(encoding="utf-8").splitlines()
        if " HP_V2 " in line
    )
    assert "tgt=id_100804" in hp_line
    bridge.id_to_name[100804] = 'eny_0082_hsbear'
    bridge.handle_event(_damage_event())
    assert 'tgt=eny_0082_hsbear' in trace_path.read_text(encoding='utf-8').splitlines()[-1]


def test_damage_target_stays_unresolved_with_only_indie_context(tmp_path):
    trace_path = tmp_path / "trace.log"
    bridge = TraceBridge(trace_path)
    bridge.id_to_name[100701] = "chr_0027_tangtang"

    bridge.handle_event(
        {
            "type": "DUNGEON_CONTEXT",
            "timestamp_ms": 1_000,
            "dungeon_id": "indie_hard011_s",
        }
    )
    bridge.handle_event(_damage_event())

    lines = trace_path.read_text(encoding="utf-8").splitlines()
    hp_line = next(line for line in lines if " HP_V2 " in line)
    assert "tgt=id_100804" in hp_line
    assert "tgtId=100804" in hp_line


def test_damage_target_never_uses_buff_name_as_actor_identity(tmp_path):
    trace_path = tmp_path / "trace.log"
    bridge = TraceBridge(trace_path)
    bridge.id_to_name[100701] = "chr_0027_tangtang"

    bridge.handle_event(
        {
            "type": "BattleOpAddBuff",
            "timestamp_ms": 1_000,
            "buff_inst_id": 123,
            "str_id": "buff_eny_0078_nefarp1_player_jump",
            "src_inst_id": 100804,
            "target_inst_id": 100804,
        }
    )
    bridge.handle_event(_damage_event())

    lines = trace_path.read_text(encoding="utf-8").splitlines()
    hp_line = next(line for line in lines if " HP_V2 " in line)
    assert "tgt=id_100804" in hp_line


def test_damage_target_keeps_existing_known_enemy_over_context_hint(tmp_path):
    trace_path = tmp_path / "trace.log"
    bridge = TraceBridge(trace_path)
    bridge.id_to_name[100701] = "chr_0027_tangtang"
    bridge.id_to_name[100804] = "eny_0051_rodin"

    bridge.handle_event(
        {
            "type": "DUNGEON_CONTEXT",
            "timestamp_ms": 1_000,
            "dungeon_id": "dung02_bossrush02_03",
        }
    )
    bridge.handle_event(_damage_event())

    lines = trace_path.read_text(encoding="utf-8").splitlines()
    hp_line = next(line for line in lines if " HP_V2 " in line)
    assert "tgt=eny_0051_rodin" in hp_line
    assert "tgt=eny_0079_nefarp2" not in hp_line


def test_character_buff_and_dungeon_context_cannot_identify_target(tmp_path):
    trace_path = tmp_path / "trace.log"
    bridge = TraceBridge(trace_path)
    bridge.id_to_name[100701] = "chr_0027_tangtang"

    bridge.handle_event(
        {
            "type": "DUNGEON_CONTEXT",
            "timestamp_ms": 1_000,
            "dungeon_id": "dung02_bossrush02_03",
        }
    )
    bridge.handle_event(
        {
            "type": "BattleOpAddBuff",
            "timestamp_ms": 1_500,
            "buff_inst_id": 456,
            "str_id": "buff_chr_0027_tangtang_comboskill_spelllnfliction",
            "src_inst_id": 100701,
            "target_inst_id": 100804,
        }
    )
    bridge.handle_event(_damage_event(target_id=100804))

    lines = trace_path.read_text(encoding="utf-8").splitlines()
    hp_line = next(line for line in lines if " HP_V2 " in line)
    assert "tgt=id_100804" in hp_line
    assert "tgt=chr_0027_tangtang" not in hp_line


def test_numeric_buff_identity_wins_and_duplicate_uid_start_is_suppressed(tmp_path):
    trace_path = tmp_path / "trace.log"
    bridge = TraceBridge(trace_path)
    bridge.id_to_name[100266] = "chr_0032_lizhiyan"
    bridge.id_to_name[100378] = "eny_0063_agmelee2"
    bridge.active_weapons_by_char = {"chr_0027_tangtang": "wpn_pistol_0011"}

    bridge.handle_event(
        {
            "type": "BattleOpTriggerAction",
            "timestamp_ms": 1_000,
            "owner_id": 100266,
            "inst_id": 500,
            "trigger_type": 0,
            "template_type": "Skill",
            "template_str_id": "chr_0032_lizhiyan_normal_skill",
            "action": {
                "action_type": "BattleActionCreateBuff",
                "create_buff_action": {
                    "details": [
                        {
                            "source_id": 100266,
                            "target_id": 100378,
                            "buff_inst_id": 9001,
                            "buff_num_id": 3724,
                            "assigned_items": [
                                {"target_key": "spell_dmg_taken_up2", "numeric_value": 0.084}
                            ],
                        }
                    ]
                },
            },
        }
    )
    bridge.handle_event({"type": "BattleOpEntityDie", "timestamp_ms": 1_001, "entity_inst_id": 1})
    bridge.handle_event(
        {
            "type": "BattleOpAddBuff",
            "timestamp_ms": 1_100,
            "buff_inst_id": 9001,
            "int_id": 3724,
            "src_inst_id": 100266,
            "target_inst_id": 100378,
        }
    )
    bridge.handle_event({"type": "BattleOpEntityDie", "timestamp_ms": 1_101, "entity_inst_id": 2})

    lines = trace_path.read_text(encoding="utf-8").splitlines()
    starts = [line for line in lines if " BUFF_START " in line and "uid=9001" in line]
    assert len(starts) == 1
    assert 'id="buff_wpn_funnel_0016_will_dmg"' in starts[0]
    assert any("BB[9001]: spell_dmg_taken_up2=0.084" in line for line in lines)


def test_explicit_created_buff_number_overrides_stale_uid_identity(tmp_path):
    trace_path = tmp_path / "trace.log"
    bridge = TraceBridge(trace_path)
    bridge.id_to_name[100327] = "chr_0019_karin"
    bridge.id_to_name[100352] = "chr_0006_wolfgd"
    bridge.buff_id_by_uid[9002] = "buff_eny_0081_ruanyi_P1_weakness_2"

    bridge.handle_event(
        {
            "type": "BattleOpTriggerAction",
            "timestamp_ms": 2_000,
            "owner_id": 100327,
            "inst_id": 5002,
            "trigger_type": 0,
            "template_type": "Buff",
            "template_int_id": 585,
            "action": {
                "action_type": "BattleActionCreateBuff",
                "create_buff_action": {
                    "details": [
                        {
                            "source_id": 100327,
                            "target_id": 100352,
                            "buff_inst_id": 9002,
                            "buff_num_id": 1407,
                            "assigned_items": [{"target_key": "dmg_up", "numeric_value": 0.16}],
                        }
                    ]
                },
            },
        }
    )
    bridge.handle_event({"type": "BattleOpEntityDie", "timestamp_ms": 2_001, "entity_inst_id": 3})

    text = trace_path.read_text(encoding="utf-8")
    assert 'id="buff_equipsuit_combosuit_01_adddamage" uid=9002' in text
    assert 'id="buff_eny_0081_ruanyi_P1_weakness_2" uid=9002' not in text


def test_battle_state_timer_end_uses_wall_elapsed_and_keeps_packet_delta(tmp_path):
    trace_path = tmp_path / "trace.log"
    bridge = TraceBridge(trace_path)

    bridge.handle_event(
        {
            "type": "BattleOpModifyBattleState",
            "timestamp_ms": 1_000,
            "seq_id": 10,
            "client_tick_tms": 12_345,
            "is_in_battle": True,
        }
    )
    bridge.handle_event(
        {
            "type": "BattleOpModifyBattleState",
            "timestamp_ms": 4_500,
            "seq_id": 11,
            "client_tick_tms": 15_999,
            "is_in_battle": False,
        }
    )

    lines = trace_path.read_text(encoding="utf-8").splitlines()
    assert "GAME_TIMER_START" in lines[0]
    assert "source=PacketBattleState" in lines[0]
    assert "official=1" in lines[0]
    assert "startMs=12345" in lines[0]
    assert "GAME_TIMER_END" in lines[1]
    assert "source=PacketBattleState" in lines[1]
    assert "official=1" in lines[1]
    assert "elapsedMs=3500" in lines[1]
    assert "startMs=12345" in lines[1]
    assert "endMs=15999" in lines[1]
    assert "packetElapsedMs=3654" in lines[1]
    assert "wallElapsedMs" not in lines[1]


def test_war_echo_scene_exit_waits_for_official_complete(tmp_path):
    trace_path = tmp_path / "trace.log"
    bridge = TraceBridge(trace_path)

    bridge.handle_event(
        {
            "type": "BattleOpModifyBattleState",
            "timestamp_ms": 1_000,
            "seq_id": 10,
            "client_tick_tms": 12_345,
            "is_in_battle": True,
        }
    )
    bridge.handle_event(
        {
            "type": "SC_GAME_MECHANICS_SYNC_CHALLENGE_START",
            "timestamp_ms": 2_000,
            "game_id": "indie_battletower007_ex",
            "challenge_start_ts": 2_100,
        }
    )
    bridge.handle_event(
        {
            "type": "CS_SCENE_SET_BATTLE",
            "timestamp_ms": 5_000,
            "in_battle": False,
        }
    )
    bridge.handle_event(
        {
            "type": "SC_SCENE_SET_BATTLE",
            "timestamp_ms": 5_050,
            "in_battle": False,
        }
    )
    bridge.handle_event(
        {
            "type": "SC_GAME_MECHANICS_SYNC_CHALLENGE_COMPLETE",
            "timestamp_ms": 5_100,
            "game_id": "indie_battletower007_ex",
            "is_pass": True,
            "pass_time": 3_100,
        }
    )

    lines = trace_path.read_text(encoding="utf-8").splitlines()
    assert not any("fallback=1" in line for line in lines)
    assert not any(" GAME_TIMER_END " in line for line in lines)
    assert any(" OFFICIAL_TIMER_AWAIT " in line for line in lines)
    assert any(
        " OFFICIAL_TIMER_END " in line and "isPass=1" in line and "passTime=3100" in line
        for line in lines
    )
    assert sum(" SCENE_BATTLE_STATE " in line for line in lines) == 2


def test_war_echo_pass_result_does_not_synthesize_timer_end(tmp_path):
    trace_path = tmp_path / "trace.log"
    bridge = TraceBridge(trace_path)

    bridge.handle_event(
        {
            "type": "DUNGEON_CONTEXT",
            "timestamp_ms": 900,
            "dungeon_id": "indie_battletower007_ex",
            "source": "SC_SELF_SCENE_INFO",
            "is_calc": False,
            "is_pass": False,
        }
    )
    bridge.handle_event(
        {
            "type": "BattleOpModifyBattleState",
            "timestamp_ms": 1_000,
            "seq_id": 10,
            "client_tick_tms": 12_345,
            "is_in_battle": True,
        }
    )
    bridge.handle_event(
        {
            "type": "SC_GAME_MECHANICS_SYNC_CHALLENGE_START",
            "timestamp_ms": 2_000,
            "game_id": "indie_battletower007_ex",
            "challenge_start_ts": 2_000,
        }
    )
    bridge.handle_event(
        {
            "type": "DUNGEON_CONTEXT",
            "timestamp_ms": 4_900,
            "dungeon_id": "indie_battletower007_ex",
            "source": "SC_SELF_SCENE_INFO",
            "is_calc": True,
            "is_pass": True,
        }
    )
    bridge.handle_event(
        {
            "type": "SC_SCENE_SET_BATTLE",
            "timestamp_ms": 5_000,
            "in_battle": False,
        }
    )

    lines = trace_path.read_text(encoding="utf-8").splitlines()
    assert any(
        "BATTLE_RESULT source=SC_SELF_SCENE_INFO" in line
        and "isCalc=1" in line
        and "isPass=1" in line
        for line in lines
    )
    assert any(" OFFICIAL_TIMER_AWAIT " in line for line in lines)
    assert not any(" GAME_TIMER_END " in line for line in lines)
    assert not any(" OFFICIAL_TIMER_END " in line for line in lines)


def test_scene_battle_false_does_not_split_non_war_echo_multi_phase_timer(tmp_path):
    trace_path = tmp_path / "trace.log"
    bridge = TraceBridge(trace_path)

    bridge.handle_event(
        {
            "type": "DUNGEON_CONTEXT",
            "timestamp_ms": 900,
            "dungeon_id": "dung02_bossrush02_03",
        }
    )
    bridge.handle_event(
        {
            "type": "BattleOpModifyBattleState",
            "timestamp_ms": 1_000,
            "seq_id": 10,
            "client_tick_tms": 12_345,
            "is_in_battle": True,
        }
    )
    bridge.handle_event(
        {
            "type": "SC_GAME_MECHANICS_SYNC_CHALLENGE_START",
            "timestamp_ms": 2_000,
            "game_id": "dung02_bossrush02_03",
            "challenge_start_ts": 2_000,
        }
    )
    bridge.handle_event(
        {
            "type": "SC_SCENE_SET_BATTLE",
            "timestamp_ms": 5_000,
            "in_battle": False,
        }
    )
    bridge.handle_event(
        {
            "type": "BattleOpModifyBattleState",
            "timestamp_ms": 6_000,
            "seq_id": 11,
            "client_tick_tms": 17_345,
            "is_in_battle": False,
        }
    )

    lines = trace_path.read_text(encoding="utf-8").splitlines()
    assert not any("source=SceneSetBattleFallback" in line for line in lines)
    assert any("GAME_TIMER_END" in line and "source=PacketBattleState" in line for line in lines)


def test_scene_load_marks_missing_timer_then_resets_for_next_battle(tmp_path):
    trace_path = tmp_path / "trace.log"
    bridge = TraceBridge(trace_path)

    bridge.handle_event(
        {
            "type": "BattleOpModifyBattleState",
            "timestamp_ms": 1_000,
            "seq_id": 10,
            "client_tick_tms": 10_000,
            "is_in_battle": True,
        }
    )
    bridge.handle_event(
        {
            "type": "CS_SCENE_LOAD_FINISH",
            "timestamp_ms": 4_000,
            "scene_num_id": 430,
        }
    )
    bridge.handle_event(
        {
            "type": "BattleOpModifyBattleState",
            "timestamp_ms": 5_000,
            "seq_id": 20,
            "client_tick_tms": 20_000,
            "is_in_battle": True,
        }
    )

    lines = trace_path.read_text(encoding="utf-8").splitlines()
    assert any("BATTLE_TIMER_MISSING boundary=CS_SCENE_LOAD_FINISH" in line for line in lines)
    assert not any("source=SceneLoadFallback" in line for line in lines)
    assert any("GAME_TIMER_RESET source=CS_SCENE_LOAD_FINISH scene=430" in line for line in lines)
    assert sum(" GAME_TIMER_START " in line for line in lines) == 2


def test_war_echo_capture_session_loss_reports_missing_official_end_without_fallback(tmp_path):
    trace_path = tmp_path / "trace.log"
    bridge = TraceBridge(trace_path)

    bridge.handle_event(
        {
            "type": "BattleOpModifyBattleState",
            "timestamp_ms": 1_000,
            "seq_id": 10,
            "client_tick_tms": 10_000,
            "is_in_battle": True,
        }
    )
    bridge.handle_event(
        {
            "type": "SC_GAME_MECHANICS_SYNC_CHALLENGE_START",
            "timestamp_ms": 2_000,
            "game_id": "indie_battletower007_ex",
            "challenge_start_ts": 2_000,
        }
    )

    assert bridge.end_capture_session(timestamp_ms=5_000) is False
    assert bridge.end_capture_session(timestamp_ms=5_100) is False
    bridge.begin_capture_session(timestamp_ms=6_000)

    lines = trace_path.read_text(encoding="utf-8").splitlines()
    assert not any("fallback=1" in line for line in lines)
    missing = [line for line in lines if " OFFICIAL_TIMER_MISSING " in line]
    assert len(missing) == 1
    assert "boundary=CAPTURE_SESSION_LOST" in missing[0]
    assert "officialStartSeen=1" in missing[0]
    assert lines[-1].endswith("GAME_TIMER_RESET source=CAPTURE_SESSION_START")


def test_war_echo_missing_official_start_stays_incomplete_and_is_visible(tmp_path):
    trace_path = tmp_path / "trace.log"
    bridge = TraceBridge(trace_path)

    bridge.handle_event(
        {
            "type": "DUNGEON_CONTEXT",
            "timestamp_ms": 900,
            "dungeon_id": "indie_battletower007_ex",
        }
    )
    bridge.handle_event(
        {
            "type": "BattleOpModifyBattleState",
            "timestamp_ms": 1_000,
            "seq_id": 10,
            "client_tick_tms": 10_000,
            "is_in_battle": True,
        }
    )
    bridge.handle_event(
        {
            "type": "CS_SCENE_SET_BATTLE",
            "timestamp_ms": 5_000,
            "in_battle": False,
        }
    )
    bridge.handle_event(
        {
            "type": "BattleOpModifyBattleState",
            "timestamp_ms": 6_000,
            "seq_id": 20,
            "client_tick_tms": 20_000,
            "is_in_battle": True,
        }
    )
    bridge.handle_event(
        {
            "type": "CS_SCENE_LOAD_FINISH",
            "timestamp_ms": 7_000,
            "scene_num_id": 430,
        }
    )

    lines = trace_path.read_text(encoding="utf-8").splitlines()
    assert not any(" GAME_TIMER_END " in line for line in lines)
    assert any(
        " OFFICIAL_TIMER_AWAIT " in line and "officialStartSeen=0" in line
        for line in lines
    )
    assert sum(" GAME_TIMER_START " in line for line in lines) == 1
    assert any(
        " BATTLE_PHASE_START " in line
        and "gameId=indie_battletower007_ex" in line
        for line in lines
    )
    assert not any("boundary=NEXT_BATTLE_START" in line for line in lines)
    assert any(
        " OFFICIAL_TIMER_MISSING " in line
        and "boundary=CS_SCENE_LOAD_FINISH" in line
        and "officialStartSeen=0" in line
        for line in lines
    )


def test_war_echo_multi_wave_week_raid_settlement_closes_single_run(tmp_path):
    trace_path = tmp_path / "trace.log"
    bridge = TraceBridge(trace_path)

    bridge.handle_event(
        {
            "type": "DUNGEON_CONTEXT",
            "timestamp_ms": 900,
            "dungeon_id": "indie_battletower004_ex",
        }
    )
    bridge.handle_event(
        {
            "type": "BattleOpModifyBattleState",
            "timestamp_ms": 1_000,
            "seq_id": 10,
            "client_tick_tms": 10_000,
            "is_in_battle": True,
        }
    )
    bridge.handle_event(
        {
            "type": "BattleOpModifyBattleState",
            "timestamp_ms": 4_000,
            "seq_id": 11,
            "client_tick_tms": 13_000,
            "is_in_battle": False,
        }
    )
    bridge.handle_event(
        {
            "type": "BattleOpModifyBattleState",
            "timestamp_ms": 5_000,
            "seq_id": 20,
            "client_tick_tms": 14_000,
            "is_in_battle": True,
        }
    )
    bridge.handle_event(
        {
            "type": "BattleOpModifyBattleState",
            "timestamp_ms": 8_000,
            "seq_id": 21,
            "client_tick_tms": 17_000,
            "is_in_battle": False,
        }
    )
    bridge.handle_event(
        {
            "type": "SC_SYNC_WEEK_RAID_SETTLEMENT",
            "timestamp_ms": 9_500,
            "game_id": "indie_battletower004_ex",
            "total_playtime": 8_250,
            "bp_score": 200,
            "danger_meter": 100,
        }
    )

    lines = trace_path.read_text(encoding="utf-8").splitlines()
    assert sum(" GAME_TIMER_START " in line for line in lines) == 1
    assert sum(" BATTLE_PHASE_START " in line for line in lines) == 1
    assert not any(" OFFICIAL_TIMER_MISSING " in line for line in lines)
    assert any(
        " OFFICIAL_TIMER_END " in line
        and "source=SC_SYNC_WEEK_RAID_SETTLEMENT" in line
        and "gameId=indie_battletower004_ex" in line
        and "isPass=1" in line
        and "passTime=8250" in line
        and "bpScore=200" in line
        and "dangerMeter=100" in line
        for line in lines
    )


def test_week_raid_settlement_without_active_run_does_not_create_clear(tmp_path):
    trace_path = tmp_path / "trace.log"
    bridge = TraceBridge(trace_path)

    bridge.handle_event(
        {
            "type": "DUNGEON_CONTEXT",
            "timestamp_ms": 900,
            "dungeon_id": "indie_battletower004_ex",
        }
    )
    bridge.handle_event(
        {
            "type": "SC_SYNC_WEEK_RAID_SETTLEMENT",
            "timestamp_ms": 1_000,
            "game_id": "indie_battletower004_ex",
            "total_playtime": 100,
        }
    )

    lines = trace_path.read_text(encoding="utf-8").splitlines()
    assert not any(" OFFICIAL_TIMER_END " in line for line in lines)


def test_completion_reward_is_not_used_as_week_raid_settlement(tmp_path):
    trace_path = tmp_path / "trace.log"
    bridge = TraceBridge(trace_path)

    bridge.handle_event(
        {
            "type": "DUNGEON_CONTEXT",
            "timestamp_ms": 900,
            "dungeon_id": "indie_battletower004_ex",
        }
    )
    bridge.handle_event(
        {
            "type": "BattleOpModifyBattleState",
            "timestamp_ms": 1_000,
            "seq_id": 10,
            "client_tick_tms": 10_000,
            "is_in_battle": True,
        }
    )
    bridge.handle_event(
        {
            "type": "SC_GAME_MECHANICS_SYNC_COMPLETION_REWARD",
            "timestamp_ms": 2_000,
            "game_id": "indie_battletower004_ex",
            "is_pass": True,
        }
    )

    lines = trace_path.read_text(encoding="utf-8").splitlines()
    assert not any(" OFFICIAL_TIMER_END " in line for line in lines)


def test_week_raid_settlement_allows_next_run_to_refresh(tmp_path):
    trace_path = tmp_path / "trace.log"
    bridge = TraceBridge(trace_path)

    bridge.handle_event(
        {
            "type": "DUNGEON_CONTEXT",
            "timestamp_ms": 900,
            "dungeon_id": "indie_battletower004_ex",
        }
    )
    bridge.handle_event(
        {
            "type": "BattleOpModifyBattleState",
            "timestamp_ms": 1_000,
            "seq_id": 10,
            "client_tick_tms": 10_000,
            "is_in_battle": True,
        }
    )
    bridge.handle_event(
        {
            "type": "BattleOpModifyBattleState",
            "timestamp_ms": 4_000,
            "seq_id": 11,
            "client_tick_tms": 13_000,
            "is_in_battle": False,
        }
    )
    bridge.handle_event(
        {
            "type": "SC_SYNC_WEEK_RAID_SETTLEMENT",
            "timestamp_ms": 5_000,
            "game_id": "indie_battletower004_ex",
            "total_playtime": 3_000,
        }
    )
    bridge.handle_event(
        {
            "type": "BattleOpModifyBattleState",
            "timestamp_ms": 7_000,
            "seq_id": 20,
            "client_tick_tms": 20_000,
            "is_in_battle": True,
        }
    )

    lines = trace_path.read_text(encoding="utf-8").splitlines()
    assert sum(" GAME_TIMER_START " in line for line in lines) == 2
    assert sum(" OFFICIAL_TIMER_END " in line for line in lines) == 1
    assert not any(" BATTLE_PHASE_START " in line and "seq=20" in line for line in lines)


def test_game_mechanics_time_freeze_writes_reading_values(tmp_path):
    trace_path = tmp_path / "trace.log"
    bridge = TraceBridge(trace_path)

    bridge.handle_event(
        {
            "type": "SC_GAME_MECHANICS_MODIFY_INST_TIME_FREEZE",
            "timestamp_ms": 1_000,
            "game_id": "indie_battletower007_ex",
            "time_freeze_infos": [
                {"time_key": 7, "total_freeze_time_in_ms": 869},
                {"time_key": 9, "total_freeze_time_in_ms": 1_250},
            ],
        }
    )

    line = trace_path.read_text(encoding="utf-8").strip()
    assert "GAME_MECHANICS_TIME_FREEZE" in line
    assert "freezeInfos=[7:869,9:1250]" in line
    assert "totalFreezeMs=1250" in line


def test_dungeon_context_is_written_and_repeated_at_battle_start(tmp_path):
    trace_path = tmp_path / "trace.log"
    bridge = TraceBridge(trace_path)

    bridge.handle_event(
        {
            "type": "DUNGEON_CONTEXT",
            "timestamp_ms": 1_000,
            "source": "SC_SELF_SCENE_INFO",
            "dungeon_id": "indie_hard008_s",
            "scene_num_id": 210,
            "is_reward": True,
            "is_calc": False,
            "is_pass": False,
        }
    )
    bridge.handle_event(
        {
            "type": "BattleOpModifyBattleState",
            "timestamp_ms": 2_000,
            "seq_id": 10,
            "client_tick_tms": 12_345,
            "is_in_battle": True,
        }
    )

    lines = trace_path.read_text(encoding="utf-8").splitlines()
    assert "DUNGEON_CONTEXT" in lines[0]
    assert "dungeonId=indie_hard008_s" in lines[0]
    assert "source=SC_SELF_SCENE_INFO" in lines[0]
    assert "scene=210" in lines[0]
    assert "isReward=1" in lines[0]
    assert "DUNGEON_CONTEXT" in lines[1]
    assert "dungeonId=indie_hard008_s" in lines[1]
    assert "GAME_TIMER_START" in lines[2]
    assert "source=PacketBattleState" in lines[2]
    assert "official=1" in lines[2]


def test_contract_tags_are_written_to_trace(tmp_path):
    trace_path = tmp_path / "trace.log"
    bridge = TraceBridge(trace_path)

    bridge.handle_event(
        {
            "type": "CONTRACT_TAGS",
            "timestamp_ms": 1_000,
            "dungeon_id": "indie_contract001",
            "tag_ids": [100501, 101301, 102801],
            "score": 3,
            "source": "SC_CONTINGENCY_CONTRACT_TAGS_SYNC",
            "direction": "sc",
            "msg_id": 2202,
        }
    )

    lines = trace_path.read_text(encoding="utf-8").splitlines()
    assert "CONTRACT_TAGS" in lines[0]
    assert "dungeonId=indie_contract001" in lines[0]
    assert "tagIds=[100501,101301,102801]" in lines[0]
    assert "score=3" in lines[0]
    assert "msgId=2202" in lines[0]


def test_object_enter_view_writes_entity_kind_and_stats(tmp_path):
    trace_path = tmp_path / "trace.log"
    bridge = TraceBridge(trace_path)

    bridge.handle_event(
        {
            "type": "SC_OBJECT_ENTER_VIEW",
            "timestamp_ms": 1_000,
            "objects": [
                {
                    "kind": "monster",
                    "battle_inst_id": 100804,
                    "obj_id": 7001,
                    "templateid": "eny_0051_rodin",
                    "level": 65,
                    "hp": 68869,
                    "attrs": [
                        {"attr_type": 2, "base": 1231, "value": 1231},
                    ],
                }
            ],
        }
    )

    lines = trace_path.read_text(encoding="utf-8").splitlines()
    assert any("ENTITY_STATS id=100804" in line for line in lines)
    entity_line = next(line for line in lines if "ENTITY_STATS id=100804" in line)
    assert "template=eny_0051_rodin" in entity_line
    assert "kind=monster" in entity_line
    assert "level=65" in entity_line
    assert "attrs=[2:1231/1231]" in entity_line


def test_trigger_action_writes_blackboard_and_ability_entity_trace(tmp_path):
    trace_path = tmp_path / "trace.log"
    bridge = TraceBridge(trace_path)
    bridge.id_to_name[100701] = "chr_0027_tangtang"

    base = {
        "type": "BattleOpTriggerAction",
        "timestamp_ms": 2_000,
        "seq_id": 10,
        "client_tick_tms": 12_345,
        "inst_id": 555,
        "owner_id": 100701,
        "template_str_id": "chr_0027_tangtang_normal_skill",
    }
    bridge.handle_event(
        {
            **base,
            "action": {
                "action_type": "BattleActionModifyDynamicBlackboard",
                "action_id": 7,
                "modify_dynamic_blackboard_action": {"client_value": 1.25},
            },
        }
    )
    bridge.handle_event(
        {
            **base,
            "action": {
                "action_type": "BattleActionSimpleCalcBb",
                "action_id": 8,
                "simple_calc_bb_action": {
                    "client_target_key": "atk_scale",
                    "client_final_value": 0.355,
                    "client_value_a": 0.71,
                    "client_value_b": 0.5,
                },
            },
        }
    )
    bridge.handle_event(
        {
            **base,
            "action": {
                "action_type": "BattleActionSpawnAbilityEntity",
                "action_id": 86,
                "spawn_ability_entity_action": {
                    "details": [
                        {
                            "client_ability_entity_id": 2305843009213695178,
                            "source_id": 100701,
                            "init_pos": {"x": -1, "y": 2, "z": 3.5},
                            "rotation": {"x": 0, "y": 90, "z": 0},
                        }
                    ]
                },
            },
        }
    )

    text = trace_path.read_text(encoding="utf-8")
    assert "BB_MODIFY_DYNAMIC seq=10 tick=12345 inst=555 ownerId=100701 owner=chr_0027_tangtang" in text
    assert "skill=chr_0027_tangtang_normal_skill actionId=7 value=1.25" in text
    assert "BB_SIMPLE_CALC seq=10 tick=12345 inst=555 ownerId=100701 owner=chr_0027_tangtang" in text
    assert "key=atk_scale final=0.355 valueA=0.71 valueB=0.5" in text
    assert "ABILITY_ENTITY_SPAWN seq=10 tick=12345 actionId=86 inst=555" in text
    assert "entityId=2305843009213695178" in text
    assert "pos=-1.0,2.0,3.5 rot=0.0,90.0,0.0" in text
