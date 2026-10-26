from dataclasses import replace

import pytest

from parser_core.rotation_clock import ALGORITHM_ID, RotationClockSpec, RotationObserverClock


def spec():
    # Explicit synthetic proof identities; current-version values from native.
    return RotationClockSpec("a" * 64, "b" * 64, "c" * 64, 20, 1000, 180000, 0, 0)


def proof():
    return {"schemaVersion": 1, "algorithmId": ALGORITHM_ID, "proofComplete": True,
            "gameAssemblySha256": "a" * 64, "metadataSha256": "b" * 64, "dumpSha256": "c" * 64,
            "constants": {"sessionThreadSleepMs": 20, "maxServerBiasMs": 1000,
                          "loginBiasThresholdMs": 180000, "initialBiasMs": 0, "missingFreezeKeyMs": 0}}


def sync(clock, *, client=1000000, receive=1000039, server=999967, logical=30091):
    assert clock.observe_sync(source="CS_PING", client_ts=client, capture_ms=client + 1, logical_ts=logical)
    return clock.observe_sync(source="SC_PING", client_ts=client, capture_ms=receive, server_ts=server)


def ready_clock():
    clock = RotationObserverClock(spec())
    assert sync(clock)
    clock.begin_challenge("game-a", 1000000)
    return clock


def test_proof_is_complete_and_bound_to_current_gameassembly():
    assert RotationClockSpec.from_proof(proof(), expected_game_assembly_sha256="a" * 64) == spec()
    with pytest.raises(ValueError):
        RotationClockSpec.from_proof(proof(), expected_game_assembly_sha256="d" * 64)
    raw = proof()
    del raw["constants"]["sessionThreadSleepMs"]
    with pytest.raises(ValueError):
        RotationClockSpec.from_proof(raw, expected_game_assembly_sha256="a" * 64)


@pytest.mark.parametrize("change", [
    {"schemaVersion": True}, {"algorithmId": "other"}, {"proofComplete": False},
    {"metadataSha256": "invalid"}, {"dumpSha256": None},
])
def test_invalid_proof_never_creates_a_clock(change):
    raw = proof()
    raw.update(change)
    with pytest.raises(ValueError):
        RotationClockSpec.from_proof(raw, expected_game_assembly_sha256="a" * 64)


def test_no_spec_no_pair_or_unmatched_response_stays_unknown():
    clock = RotationObserverClock()
    assert not clock.observe_sync(source="CS_PING", client_ts=1, capture_ms=2)
    clock.begin_challenge("game-a", 1000000)
    assert clock.elapsed(1001000) is None
    clock = RotationObserverClock(spec())
    clock.begin_challenge("game-a", 1000000)
    assert not clock.observe_sync(source="SC_PING", client_ts=1000000, capture_ms=1000039, server_ts=999967)
    assert clock.elapsed(1001000) is None


def test_initial_native_bias_zero_survives_small_login_and_ping_offsets():
    clock = ready_clock()
    state = clock.diagnostics()
    assert state["biasMs"] == 0
    assert state["lastSync"]["nativePingMs"] == 19
    assert state["lastSync"]["candidateBiasMs"] == -63
    assert not state["lastSync"]["loginBranchChangedBias"]
    assert not state["lastSync"]["pingSetterChangedBias"]
    assert clock.elapsed(1001000) == 1000


@pytest.mark.parametrize("delta,changed", [(1000, False), (1001, True), (-1000, False), (-1001, True)])
def test_ping_setter_uses_strict_native_threshold(delta, changed):
    clock = ready_clock()
    receive, client = 1010040, 1010000  # native ping 20, half 10
    candidate = delta
    assert sync(clock, client=client, receive=receive, server=receive - 10 + candidate)
    assert clock.diagnostics()["lastSync"]["pingSetterChangedBias"] is changed
    assert clock.diagnostics()["biasMs"] == (candidate if changed else 0)


@pytest.mark.parametrize("delta,changed", [(180000, False), (180001, True), (-180000, False), (-180001, True)])
def test_login_branch_uses_strict_native_180000_threshold(delta, changed):
    clock = RotationObserverClock(spec())
    assert sync(clock, client=1000000, receive=1000040, server=1000040 + delta)
    assert clock.diagnostics()["lastSync"]["loginBranchChangedBias"] is changed


def test_login_difference_includes_old_bias_and_replaces_without_adding_it_back():
    clock = RotationObserverClock(replace(spec(), initial_bias_ms=500))
    assert sync(clock, client=1000000, receive=1000040, server=1200641)
    # Login candidate = 1200641 - (1000040 + 500) = 200101.
    # Ping candidate 200611 differs by only 510: native setter keeps login value.
    assert clock.diagnostics()["biasMs"] == 200101
    assert clock.diagnostics()["lastSync"]["loginBranchChangedBias"]
    assert not clock.diagnostics()["lastSync"]["pingSetterChangedBias"]


def test_threshold_comparison_reproduces_float32_rounding():
    clock = RotationObserverClock(replace(spec(), max_server_bias_ms=16777216))
    assert sync(clock)
    receive = 30000040
    assert sync(clock, client=30000000, receive=receive, server=receive - 10 - 16777217)
    assert clock.diagnostics()["biasMs"] == 0  # float32 rounds 16777217 to 16777216
    assert sync(clock, client=31000000, receive=31000040, server=31000040 - 10 - 16777218)
    assert clock.diagnostics()["biasMs"] == -16777218


@pytest.mark.parametrize("client,receive", [(2**31 - 39, 2**31 + 1), (2**32 - 35, 2**32 + 5)])
def test_native_ping_int32_wraparound_is_exact(client, receive):
    clock = RotationObserverClock(spec())
    assert sync(clock, client=client, receive=receive, server=receive)
    assert clock.diagnostics()["lastSync"]["nativePingMs"] == 20


def test_negative_native_ping_clamps_to_one_and_uses_integer_truncation():
    clock = RotationObserverClock(spec())
    assert sync(clock, client=1000040, receive=1000000, server=1000000)
    assert clock.diagnostics()["lastSync"]["nativePingMs"] == 1
    assert clock.diagnostics()["lastSync"]["candidateBiasMs"] == 0


def test_duplicate_reply_cannot_reapply_calibration_without_its_request():
    clock = ready_clock()
    state = clock.diagnostics()
    assert not clock.observe_sync(source="SC_PING", client_ts=1000000, capture_ms=1000039, server_ts=999967)
    assert clock.diagnostics() == state


def test_missing_capture_epoch_is_not_recovered_from_logical_clock():
    clock = RotationObserverClock(spec())
    assert not clock.observe_sync(source="CS_PING", client_ts=1000000, capture_ms=None, logical_ts=30091)
    assert not clock.observe_sync(source="SC_PING", client_ts=1000000, capture_ms=1000039, server_ts=999967)
    assert not clock.observe_sync(source="SC_FLUSH_SYNC", client_ts=1000000, capture_ms=1000039, server_ts=999967)
    assert not clock.diagnostics()["ready"]


def test_counting_uses_float64_floor_and_only_challenge_key_zero():
    clock = ready_clock()
    assert clock.elapsed(1001000.99) == 1000
    clock.update_freeze("game-a", [(1, 900000), (2, 800000)])
    assert clock.elapsed(1001000.99) == 1000
    clock.update_freeze("game-a", [(0, 741)])
    assert clock.elapsed(1001000.99) == 259
    clock.update_freeze("game-other", [(0, 900)])
    assert clock.elapsed(1001000) == 259
    clock.update_freeze("game-a", [])
    assert clock.elapsed(1001000) == 259  # empty update doesn't clear native LUT


def test_pause_hides_progress_and_resume_uses_explicit_cumulative_counter():
    clock = ready_clock()
    clock.set_freeze_state(True)
    assert clock.elapsed(1022000) is None
    clock.update_freeze("game-a", [(0, 11356)])
    clock.set_freeze_state(False)
    assert clock.elapsed(1022000) == 10644
    clock.set_freeze_state(None)
    assert clock.elapsed(1022000) is None


def test_new_start_does_not_invent_counter_reset():
    clock = ready_clock()
    clock.update_freeze("game-a", [(0, 741)])
    clock.begin_challenge("game-a", 1001000)
    assert clock.elapsed(1002000) == 259
    clock.mark_counter_unknown("game-a")
    clock.begin_challenge("game-a", 1001000)
    assert clock.elapsed(1002000) is None
    clock.update_freeze("game-a", [(0, 0)])
    assert clock.elapsed(1002000) == 1000


def test_different_game_counter_is_unknown_until_explicit_value_or_fresh_object():
    clock = ready_clock()
    clock.update_freeze("game-a", [(0, 741)])
    clock.begin_challenge("game-b", 1001000)
    assert clock.elapsed(1002000) is None
    clock.update_freeze("game-b", [(0, 0)])
    assert clock.elapsed(1002000) == 1000
    clock.reset_subgame("game-c")
    clock.begin_challenge("game-c", 1001000)
    assert clock.elapsed(1002000) == 1000


def test_capture_reset_discards_calibration_and_requires_another_exact_pair():
    clock = ready_clock()
    clock.reset_session()
    clock.begin_challenge("game-a", 1000000)
    assert clock.elapsed(1001000) is None
    assert sync(clock)
    assert clock.elapsed(1001000) == 1000


@pytest.mark.parametrize("now", [True, float("nan"), float("inf"), -1, "1001000"])
def test_invalid_observer_measurement_stays_unknown(now):
    assert ready_clock().elapsed(now) is None


def test_conflicting_ping_identity_and_bad_freeze_record_fail_closed():
    clock = ready_clock()
    assert clock.observe_sync(source="CS_PING", client_ts=2000000, capture_ms=2000001)
    assert not clock.observe_sync(source="CS_PING", client_ts=2000000, capture_ms=2000002)
    assert clock.elapsed(2000040) is None
    clock = ready_clock()
    clock.update_freeze("game-a", [(0, 100), (0, 200)])
    assert clock.elapsed(1001000) is None


def test_native_factory_proof_resets_only_new_enter_not_duplicate_current_identity():
    clock = RotationObserverClock(replace(spec(), entry_creates_fresh_subgame=True))
    assert sync(clock)
    clock.observe_instance("game-a", "instance-a", 100)
    clock.begin_challenge("game-a", 1000000)
    clock.update_freeze("game-a", [(0, 741)])
    clock.observe_instance("game-a", "instance-a", 100)
    assert clock.elapsed(1001000) == 259
    clock.observe_instance("game-a", "instance-b", 200)
    clock.begin_challenge("game-a", 1000000)
    assert clock.elapsed(1001000) == 1000
    assert clock.diagnostics()["ready"]
    clock.update_freeze("game-a", [(0, 741)])
    clock.note_entry_boundary()
    clock.observe_instance("game-a", "instance-b", 200)
    clock.begin_challenge("game-a", 1000000)
    assert clock.elapsed(1001000) == 1000


def test_instance_identity_survives_challenge_clear_without_unproved_reset():
    clock = ready_clock()
    clock.observe_instance("game-a", "instance-a", 100)
    clock.update_freeze("game-a", [(0, 741)])
    clock.clear_challenge()
    clock.observe_instance("game-a", "instance-b", 200)
    clock.begin_challenge("game-a", 1000000)
    assert clock.elapsed(1001000) is None
    clock.update_freeze("game-a", [(0, 0)])
    assert clock.elapsed(1001000) == 1000
