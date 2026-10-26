from parser_core.battle_log_parser import parse_raw_battle_log_text


def _parse_with_timer(*, pass_time_ms: int, end_wall_ms: int, packet_elapsed_ms: int):
    end_seconds = end_wall_ms // 1000
    end_millis = end_wall_ms % 1000
    text = "\n".join(
        [
            "[10:00:00.000] DUNGEON_CONTEXT dungeonId=dung01_bossrush01_04 source=SC_SELF_SCENE_INFO",
            "[10:00:00.000] OFFICIAL_TIMER_START source=SC_GAME_MECHANICS_SYNC_CHALLENGE_START gameId=dung01_bossrush01_04 challengeStartTs=1",
            "[10:00:00.000] GAME_TIMER_START seq=1 source=PacketBattleState startMs=1 expireMs=0 official=1",
            "[10:00:01.000] SQUAD size=1 members=[chr_0028_wulfa_1001]",
            '[10:00:02.000] HP_V2 #1 hit=1000000 cum=1000000 raw=1000000.00 pHP=5000 eHP=1000000 skill="chr_0028_wulfa_ultimate_skill" hits=1 src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa seg=0 shared=2 critFlag=0 critDmg=-1.0000',
            f'[10:00:{max(end_seconds - 2, 3):02d}.000] HP_V2 #2 hit=1000000 cum=2000000 raw=1000000.00 pHP=5000 eHP=0 skill="chr_0028_wulfa_ultimate_skill" hits=1 src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa seg=0 shared=2 critFlag=0 critDmg=-1.0000',
            f"[10:00:{end_seconds:02d}.{end_millis:03d}] OFFICIAL_TIMER_END source=SC_GAME_MECHANICS_SYNC_CHALLENGE_COMPLETE gameId=dung01_bossrush01_04 isPass=1 passTime={pass_time_ms}",
            f"[10:00:{end_seconds:02d}.{end_millis:03d}] GAME_TIMER_END seq=2 source=PacketBattleState elapsedMs={pass_time_ms} startMs=1 endMs={packet_elapsed_ms + 1} expireMs=0 sane=1 official=1 packetElapsedMs={packet_elapsed_ms}",
        ]
    )
    return parse_raw_battle_log_text(text, file_name="timer-integrity.log")


def test_timer_integrity_accepts_normal_official_duration() -> None:
    report = _parse_with_timer(
        pass_time_ms=10_000,
        end_wall_ms=12_000,
        packet_elapsed_ms=8_000,
    )
    battle = report["battle"]

    assert battle["duration_ms"] == 10_000
    assert battle["official_timer_observed_elapsed_ms"] == 12_000
    assert battle["packet_timer_elapsed_ms"] == 8_000
    assert battle["timer_integrity_ok"] is True
    assert battle["timer_integrity_reasons"] == []


def test_timer_integrity_does_not_infer_tampering_from_cross_clock_deltas() -> None:
    report = _parse_with_timer(
        pass_time_ms=151,
        end_wall_ms=10_000,
        packet_elapsed_ms=8_000,
    )
    battle = report["battle"]

    assert battle["duration_ms"] == 151
    assert battle["official_timer_observed_elapsed_ms"] == 10_000
    assert battle["packet_timer_elapsed_ms"] == 8_000
    # Receipt time and simulation ticks do not prove a server duration false.
    assert battle["timer_integrity_ok"] is True
    assert battle["timer_integrity_reasons"] == []


def test_new_official_start_cannot_inherit_previous_completion_or_duration() -> None:
    text = '\n'.join([
        '[10:00:00.000] DUNGEON_CONTEXT dungeonId=dung01_bossrush01_04 source=SC_SELF_SCENE_INFO',
        '[10:00:00.000] OFFICIAL_TIMER_START source=SC_GAME_MECHANICS_SYNC_CHALLENGE_START gameId=dung01_bossrush01_04 challengeStartTs=1',
        '[10:00:00.000] SQUAD size=1 members=[chr_0028_wulfa_1001]',
        '[10:00:01.000] HP_V2 #1 hit=1000 cum=1000 raw=1000.00 pHP=5000 eHP=1000 skill="chr_0028_wulfa_ultimate_skill" hits=1 src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa seg=0 shared=2 critFlag=0 critDmg=-1.0000',
        '[10:00:12.000] OFFICIAL_TIMER_END source=SC_GAME_MECHANICS_SYNC_CHALLENGE_COMPLETE gameId=dung01_bossrush01_04 isPass=1 passTime=10000',
        '[10:00:12.000] GAME_TIMER_END seq=2 source=PacketBattleState elapsedMs=10000 startMs=1 endMs=8001 sane=1 official=1 packetElapsedMs=8000',
        '[10:01:00.000] OFFICIAL_TIMER_START source=SC_GAME_MECHANICS_SYNC_CHALLENGE_START gameId=dung01_bossrush01_04 challengeStartTs=60001',
        '[10:01:01.000] HP_V2 #2 hit=100 cum=100 raw=100.00 pHP=5000 eHP=9000 skill="chr_0028_wulfa_ultimate_skill" hits=1 src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa seg=0 shared=2 critFlag=0 critDmg=-1.0000',
        '[10:01:04.000] HP_V2 #3 hit=100 cum=200 raw=100.00 pHP=5000 eHP=8900 skill="chr_0028_wulfa_ultimate_skill" hits=1 src=chr_0028_wulfa tgt=eny_0051_rodin atk=chr_0028_wulfa seg=0 shared=2 critFlag=0 critDmg=-1.0000',
        '[10:01:05.000] OFFICIAL_TIMER_MISSING boundary=SC_GAME_MECHANICS_SYNC_LEAVE_GAME_INST packetBattleActive=1 officialExpected=1 officialStartSeen=1 gameId=dung01_bossrush01_04',
    ])
    report = parse_raw_battle_log_text(text, file_name='two-attempts.log')
    battle = report['battle']
    assert battle['total_damage'] == 200
    assert battle['duration_ms'] == 4000
    assert battle['official_timer_end_seen'] is False
    assert battle['official_timer_observed_elapsed_ms'] is None
    assert battle['packet_timer_elapsed_ms'] is None
    assert battle['timer_integrity_ok'] is None
    assert battle['clear_flag'] is False

    completed_text = text.rsplit('\n', 1)[0] + '\n' + (
        '[10:01:06.000] OFFICIAL_TIMER_END source=SC_GAME_MECHANICS_SYNC_CHALLENGE_COMPLETE '
        'gameId=dung01_bossrush01_04 isPass=1 passTime=6000'
    )
    completed = parse_raw_battle_log_text(completed_text, file_name='two-completed-attempts.log')['battle']
    assert completed['total_damage'] == 200
    assert completed['duration_ms'] == 6000
    assert completed['packet_timer_elapsed_ms'] is None
    assert completed['official_timer_observed_elapsed_ms'] == 6000
    assert completed['timer_integrity_ok'] is True
    assert completed['clear_flag'] is True

    empty_attempt = text.split('[10:01:01.000]', 1)[0]
    empty = parse_raw_battle_log_text(empty_attempt, file_name='new-attempt-before-damage.log')
    assert empty['battle']['total_damage'] == 0
    assert empty['battle']['official_timer_end_seen'] is False
    assert empty['battle']['clear_flag'] is False
    assert empty['participants'] == []
