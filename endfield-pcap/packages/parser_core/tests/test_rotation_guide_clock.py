import pytest

from parser_core.rotation_clock import RotationClockSpec
from parser_core.rotation_guide import RotationGuideEngine

from test_rotation_guide import ACTORS, CHARACTERS, GAME, START, action_catalog, action_plan, cast, context, envelope, feed, start


def spec():
    return RotationClockSpec("a" * 64, "b" * 64, "c" * 64, 20, 1000, 180000, 0, 0)


def sync(engine, *, client=START - 1040, receive=START - 1000, server=START - 1070):
    feed(engine, f"GAME_CLOCK_SYNC source=CS_PING clientTs={client} logicalTs=30091 captureMs={client + 1}",
         f"GAME_CLOCK_SYNC source=SC_PING clientTs={client} serverTs={server} captureMs={receive}")


def ready_engine():
    engine = RotationGuideEngine(clock_spec=spec())
    engine.replace_plans(envelope())
    context(engine)
    sync(engine)
    start(engine)
    return engine


def test_default_reserved_label_cannot_activate_a_guide_without_native_proof():
    engine = RotationGuideEngine()
    engine.replace_plans(envelope())
    context(engine)
    start(engine)
    feed(engine, f"OFFICIAL_TIMER_PROGRESS source=VERIFIED_OFFICIAL_CHALLENGE_CLOCK gameId={GAME} challengeStartTs={START} elapsedMs=1000")
    assert engine.snapshot(START + 1000) is None


def test_player_action_keeps_native_observer_time_pause_and_deduplication():
    engine = RotationGuideEngine(clock_spec=spec(), catalog=action_catalog())
    assert engine.replace_plans({**envelope([action_plan()]), "schemaVersion": 2})
    context(engine)
    sync(engine)
    start(engine)
    feed(engine, "GAME_TIME_FREEZE_STATE source=SC_GAME_TIME_FREEZE_START_RSP inTimeFreeze=1 reason=1")
    feed(engine, cast() + f" captureMs={START + 10000}")
    assert engine.snapshot(START + 11000) is None
    feed(engine, f"GAME_MECHANICS_TIME_FREEZE source=SC_GAME_MECHANICS_MODIFY_INST_TIME_FREEZE gameId={GAME} count=1 freezeInfos=[0:5000]",
         "GAME_TIME_FREEZE_STATE source=SC_GAME_TIME_FREEZE_END_RSP inTimeFreeze=0 reason=1")
    event = cast(seq=29247, skill="chr_0035_liino_normal_skill_ult") + f" captureMs={START + 11000}"
    feed(engine, event, event)
    report = engine.snapshot(START + 12000)
    assert report["clockSource"] == "official_algorithm_observer"
    assert report["elapsedMs"] == 7000
    assert report["completedCount"] == 1
    assert report["steps"][0]["completedAtMs"] == 6000


def test_observer_can_calibrate_before_official_start_and_advances_using_measured_utc():
    engine = ready_engine()
    first = engine.snapshot(START + 1000)
    second = engine.snapshot(START + 2000.99)
    assert first["elapsedMs"] == 1000
    assert second["elapsedMs"] == 2000
    assert second["clockSource"] == "official_algorithm_observer"
    assert second["clockLabel"] == "官方算法计时"
    assert second["clockProvenance"]["scope"] == "independent_observer"


def test_logical_tick_regression_cannot_disable_or_calibrate_official_clock():
    engine = ready_engine()
    feed(engine, "GAME_CLOCK_TICK source=CS_BATTLE_OP tickMs=30091",
         "GAME_CLOCK_TICK source=CS_BATTLE_OP tickMs=1", cast(tick=1))
    report = engine.snapshot(START + 15000)
    assert report["elapsedMs"] == 15000
    assert report["completedCount"] == 1


def test_ultimate_simulation_tick_pause_does_not_pause_official_algorithm_clock():
    engine = ready_engine()
    feed(engine, "GAME_CLOCK_TICK source=CS_BATTLE_OP tickMs=30091")
    assert engine.snapshot(START + 12621)["elapsedMs"] == 12621
    feed(engine, "GAME_CLOCK_TICK source=CS_BATTLE_OP tickMs=30091")
    assert engine.snapshot(START + 14681)["elapsedMs"] == 14681


def test_confirmed_menu_pause_hides_until_resume_and_uses_only_key_zero():
    engine = ready_engine()
    feed(engine, "GAME_TIME_FREEZE_STATE source=SC_GAME_TIME_FREEZE_START_RSP inTimeFreeze=1 reason=1")
    assert engine.snapshot(START + 22000) is None
    feed(engine, f"GAME_MECHANICS_TIME_FREEZE source=SC_GAME_MECHANICS_MODIFY_INST_TIME_FREEZE gameId={GAME} count=2 freezeInfos=[0:11356,1:999999]",
         "GAME_TIME_FREEZE_STATE source=SC_GAME_TIME_FREEZE_END_RSP inTimeFreeze=0 reason=1")
    assert engine.snapshot(START + 22000)["elapsedMs"] == 10644


def test_confirmed_pause_freezes_the_last_validated_display_not_the_receipt_time():
    engine = ready_engine()
    displayed = engine.snapshot(START + 1500)
    assert displayed["paused"] is False
    displayed["elapsedMs"] = 999999
    displayed["steps"][0]["note"] = "caller mutation"
    feed(engine, "GAME_TIME_FREEZE_STATE source=SC_GAME_TIME_FREEZE_START_RSP inTimeFreeze=1 reason=1",
         cast() + f" captureMs={START + 10000}")
    paused = engine.snapshot(START + 22000)
    assert paused["paused"] is True
    assert paused["pauseDisplaySource"] == "last_confirmed_snapshot"
    assert paused["elapsedMs"] == 1500
    assert paused["completedCount"] == 0
    assert paused["nextStep"]["id"] == "step-1"
    assert paused["steps"][0]["note"] != "caller mutation"
    paused["steps"][0]["note"] = "paused caller mutation"
    later = engine.snapshot(START + 40000)
    assert later["elapsedMs"] == 1500
    assert later["steps"][0]["note"] != "paused caller mutation"
    feed(engine, f"GAME_MECHANICS_TIME_FREEZE source=SC_GAME_MECHANICS_MODIFY_INST_TIME_FREEZE gameId={GAME} count=2 freezeInfos=[0:11356,1:999999]",
         "GAME_TIME_FREEZE_STATE source=SC_GAME_TIME_FREEZE_END_RSP inTimeFreeze=0 reason=1")
    resumed = engine.snapshot(START + 22000)
    assert resumed["paused"] is False
    assert resumed["elapsedMs"] == 10644
    assert "pauseDisplaySource" not in resumed
    assert resumed["completedCount"] == 0


@pytest.mark.parametrize("invalid", [
    ["GAME_TIME_FREEZE_STATE source=SC_GAME_TIME_FREEZE_START_RSP inTimeFreeze=unknown"],
    [f"GAME_MECHANICS_TIME_FREEZE source=SC_GAME_MECHANICS_MODIFY_INST_TIME_FREEZE gameId={GAME} count=1 freezeInfos=missing"],
    [f"GAME_CLOCK_SYNC source=CS_PING clientTs={START + 1000} captureMs={START + 1001}",
     f"GAME_CLOCK_SYNC source=CS_PING clientTs={START + 1000} captureMs={START + 1002}"],
])
def test_invalid_evidence_during_pause_cannot_reuse_the_old_display(invalid):
    engine = ready_engine()
    assert engine.snapshot(START + 1500) is not None
    feed(engine, "GAME_TIME_FREEZE_STATE source=SC_GAME_TIME_FREEZE_START_RSP inTimeFreeze=1")
    assert engine.snapshot(START + 2000)["paused"] is True
    feed(engine, *invalid, "GAME_TIME_FREEZE_STATE source=SC_GAME_TIME_FREEZE_START_RSP inTimeFreeze=1")
    assert engine.snapshot(START + 3000) is None


@pytest.mark.parametrize("boundary", [
    "GAME_TIMER_RESET source=CS_ENTER_DUNGEON",
    f"GAME_INST_RESTART source=SC_GAME_MECHANICS_SYNC_RESTART_GAME_INST gameId={GAME}",
    f"GAME_INST_LEAVE source=SC_GAME_MECHANICS_SYNC_LEAVE_GAME_INST gameId={GAME}",
    f"OFFICIAL_TIMER_END source=SC_GAME_MECHANICS_SYNC_CHALLENGE_COMPLETE gameId={GAME} passTime=4000",
    "DUNGEON_CONTEXT dungeonId=other sceneId=scene-2",
    "SQUAD size=0 members=[]",
    "ACTOR_MAP id=102231 template=chr_0035_liino source=launch_projectile",
])
def test_confirmed_pause_snapshot_is_cleared_at_explicit_battle_boundaries(boundary):
    engine = ready_engine()
    assert engine.snapshot(START + 1500) is not None
    feed(engine, "GAME_TIME_FREEZE_STATE source=SC_GAME_TIME_FREEZE_START_RSP inTimeFreeze=1")
    assert engine.snapshot(START + 2000)["paused"] is True
    feed(engine, boundary)
    assert engine.snapshot(START + 3000) is None


@pytest.mark.parametrize("boundary", ["capture_gap", "account_change"])
def test_confirmed_pause_does_not_survive_capture_gap_or_account_change(boundary):
    engine = ready_engine()
    assert engine.snapshot(START + 1500) is not None
    feed(engine, "GAME_TIME_FREEZE_STATE source=SC_GAME_TIME_FREEZE_START_RSP inTimeFreeze=1")
    assert engine.snapshot(START + 2000)["paused"] is True
    if boundary == "capture_gap":
        engine.set_available(False)
        engine.set_available(True)
    else:
        engine.replace_plans(envelope(account="different-account"))
    assert engine.snapshot(START + 3000) is None


def test_cast_completion_time_uses_its_capture_epoch_and_missing_time_stays_unknown():
    engine = ready_engine()
    feed(engine, cast() + f" captureMs={START + 2500}")
    report = engine.snapshot(START + 3000)
    assert report["steps"][0]["completedAtMs"] == 2500
    feed(engine, cast(seq=29247, inst="102233", tick=30000))
    report = engine.snapshot(START + 4000)
    assert report["steps"][1]["completed"]
    assert report["steps"][1]["completedAtMs"] is None


def test_official_cast_completion_does_not_require_an_unrelated_simulation_tick():
    engine = ready_engine()
    raw = cast().replace("startMs=26744 ", "")
    feed(engine, raw)
    assert engine.snapshot(START + 3000)["completedCount"] == 1
    feed(engine, raw)
    assert engine.snapshot(START + 3000)["completedCount"] == 1


def test_same_game_new_start_preserves_confirmed_native_counter():
    engine = ready_engine()
    feed(engine, f"GAME_MECHANICS_TIME_FREEZE source=SC_GAME_MECHANICS_MODIFY_INST_TIME_FREEZE gameId={GAME} count=1 freezeInfos=[0:741]")
    start(engine, start_ts=START + 1000)
    assert engine.snapshot(START + 2000)["elapsedMs"] == 259


def test_restart_counter_is_unknown_until_exact_server_update():
    engine = ready_engine()
    feed(engine, f"GAME_INST_RESTART source=SC_GAME_MECHANICS_SYNC_RESTART_GAME_INST gameId={GAME}")
    context(engine)
    start(engine, start_ts=START + 1000)
    assert engine.snapshot(START + 2000) is None
    feed(engine, f"GAME_MECHANICS_TIME_FREEZE source=SC_GAME_MECHANICS_MODIFY_INST_TIME_FREEZE gameId={GAME} count=1 freezeInfos=[0:0]")
    assert engine.snapshot(START + 2000)["elapsedMs"] == 1000


def test_capture_gap_clears_clock_and_does_not_reuse_unknown_counter():
    engine = ready_engine()
    engine.set_available(False)
    engine.set_available(True)
    context(engine)
    start(engine, start_ts=START + 1000)
    assert engine.snapshot(START + 2000) is None
    sync(engine)
    assert engine.snapshot(START + 2000) is None
    feed(engine, f"GAME_MECHANICS_TIME_FREEZE source=SC_GAME_MECHANICS_MODIFY_INST_TIME_FREEZE gameId={GAME} count=1 freezeInfos=[0:0]")
    assert engine.snapshot(START + 2000)["elapsedMs"] == 1000


def test_bad_freeze_format_cannot_be_turned_into_zero():
    engine = ready_engine()
    feed(engine, f"GAME_MECHANICS_TIME_FREEZE source=SC_GAME_MECHANICS_MODIFY_INST_TIME_FREEZE gameId={GAME} count=1 freezeInfos=missing")
    assert engine.snapshot(START + 2000) is None
