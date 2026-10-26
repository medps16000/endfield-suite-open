import threading

import overlay
from parser_core import live


def trace_lines(time="10:00:00", hit=100):
    return (f"[{time}.000] GAME_TIMER_RESET source=CAPTURE_SESSION_START\n"
            f"[{time}.010] DUNGEON_CONTEXT dungeonId=indie_battletower015_ex source=SC_SELF_SCENE_INFO\n"
            f"[{time}.020] OFFICIAL_TIMER_START source=SC_GAME_MECHANICS_SYNC_CHALLENGE_START gameId=indie_battletower015_ex\n"
            f'[{time}.100] HP_V2 #1 hit={hit} cum={hit} raw={hit}.00 pHP=0 eHP=900000 skill="chr_0004_pelica_attack1" hits=1 src=chr_0004_pelica tgt=eny_0120_klbear_tower015 atk=chr_0004_pelica seg=0 shared=2 critFlag=0 critDmg=-1.0000\n').encode()


def make_tailer(monkeypatch, path):
    monkeypatch.setattr(overlay, "TRACE_FILE", str(path))
    return overlay.LogTailer()


def test_bootstrap_and_live_reader_share_exact_byte_cursor(monkeypatch, tmp_path):
    path = tmp_path / "trace.log"
    original = trace_lines()
    appended = trace_lines("10:01:00", 200)
    path.write_bytes(original)
    parse = overlay.LogTailer._parse
    seen = []

    def during_bootstrap(self, line, gen):
        seen.append(line)
        if len(seen) == 1:
            with path.open("ab") as stream:
                stream.write(appended)
        return parse(self, line, gen)

    monkeypatch.setattr(overlay.LogTailer, "_parse", during_bootstrap)
    tailer = make_tailer(monkeypatch, path)
    assert tailer._tail_position == len(original)
    assert path.stat().st_size > tailer._tail_position
    monkeypatch.setattr(overlay.time, "sleep", lambda _: tailer.stop())
    tailer.run()
    assert seen == [*original.decode().splitlines()[2:], *appended.decode().splitlines()]
    assert tailer._live_core._official_timer_start_abs_ms == 36_060_020
    assert sum(row.total_dmg for row in tailer.stats.values()) == 200
    tailer._live_core.min_reparse_interval_ms = 0
    monkeypatch.setattr(live, "_current_clock_ms", lambda: 36_120_100)
    assert tailer._live_core.snapshot()["battle"]["duration_ms"] == 80


def test_empty_bootstrap_still_reads_context_without_damage(monkeypatch, tmp_path):
    path = tmp_path / "trace.log"
    path.write_text("[10:00:00.000] GAME_TIMER_RESET source=CAPTURE_SESSION_START\n"
                    "[10:00:00.001] DUNGEON_CONTEXT dungeonId=indie_battletower015_ex source=SC_SELF_SCENE_INFO\n", encoding="utf-8")
    tailer = make_tailer(monkeypatch, path)
    assert tailer._tail_position == 0
    monkeypatch.setattr(overlay.time, "sleep", lambda _: tailer.stop())
    tailer.run()
    report = tailer._live_core.snapshot(refresh=False)
    assert report["battle"]["dungeon_context_id"] == "indie_battletower015_ex"
    assert report["participants"] == []


def test_bootstrap_partial_line_does_not_flush_pending_hit(monkeypatch, tmp_path):
    path = tmp_path / "trace.log"
    complete = trace_lines()
    metadata = "[10:00:00.100] BASELINE #1 atk=100 label=元数据\n".encode()
    split = metadata.index("元".encode()) + 1
    path.write_bytes(complete + metadata[:split])
    tailer = make_tailer(monkeypatch, path)
    assert tailer._tail_position == len(complete)
    assert 1 in tailer._pending_hits
    assert not tailer.stats
    with path.open("ab") as stream:
        stream.write(metadata[split:])
    monkeypatch.setattr(overlay.time, "sleep", lambda _: tailer.stop())
    tailer.run()
    assert not tailer._pending_hits
    assert sum(row.total_dmg for row in tailer.stats.values()) == 100


def test_partial_restart_and_utf8_are_consumed_once_when_complete(monkeypatch, tmp_path):
    path = tmp_path / "trace.log"
    path.write_bytes(trace_lines())
    tailer = make_tailer(monkeypatch, path)
    reset = "[10:00:01.000] GAME_TIMER_RESET source=SC_GAME_MECHANICS_SYNC_RESTART_GAME_INST note=重开\n".encode()
    split = reset.index("重".encode()) + 1
    with path.open("ab") as stream:
        stream.write(reset[:split])
    seen, sleeps = [], []
    parse = tailer._parse

    def observe(line, gen):
        seen.append(line)
        return parse(line, gen)

    def next_chunk(_):
        sleeps.append(True)
        if len(sleeps) == 1:
            assert seen == []
            assert tailer._live_core._official_timer_active
            with path.open("ab") as stream:
                stream.write(reset[split:])
        else:
            tailer.stop()

    monkeypatch.setattr(tailer, "_parse", observe)
    monkeypatch.setattr(overlay.time, "sleep", next_chunk)
    tailer.run()
    assert seen == [reset.decode().strip()]
    assert not tailer._live_core._official_timer_active
    assert not tailer.stats


def test_same_length_file_replacement_clears_old_state(monkeypatch, tmp_path):
    path = tmp_path / "trace.log"
    old = trace_lines(hit=100)
    new = trace_lines("11:00:00", 200)
    assert len(old) == len(new)
    path.write_bytes(old)
    tailer = make_tailer(monkeypatch, path)
    previous_identity = tailer._tail_file_identity
    replacement = tmp_path / "new.log"
    replacement.write_bytes(new)
    replacement.replace(path)
    monkeypatch.setattr(overlay.time, "sleep", lambda _: tailer.stop())
    tailer.run()
    assert tailer._tail_file_identity != previous_identity
    assert tailer._live_core._official_timer_start_abs_ms == 39_600_020
    assert sum(row.total_dmg for row in tailer.stats.values()) == 200


def test_slice_uses_supplied_snapshot_including_empty_bytes(tmp_path):
    path = tmp_path / "trace.log"
    old, current = trace_lines(), trace_lines("11:00:00")
    path.write_bytes(current)
    lines, meta = overlay.read_latest_battle_log_slice(str(path), trace_bytes=old)
    assert any("10:00:00.020" in line for line in lines)
    assert not any("11:00:00" in line for line in lines)
    assert overlay.read_latest_battle_log_slice(str(path), trace_bytes=b"") == ([], None)


def test_slow_previous_battle_cannot_block_restart_or_restore_its_report(monkeypatch, tmp_path):
    path = tmp_path / "trace.log"
    path.write_bytes(trace_lines())
    tailer = make_tailer(monkeypatch, path)
    monkeypatch.setattr(overlay, "TAIL_INTERVAL", 0.005)
    old_started, release_old = threading.Event(), threading.Event()
    new_started, release_new = threading.Event(), threading.Event()
    new_consumed, new_published = threading.Event(), threading.Event()
    original_parse = live.parse_overlay_battle_snapshot_text
    original_feed = tailer._parse
    calls = []
    active = 0
    maximum_active = 0

    def blocked_parse(text, **kwargs):
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        calls.append(text)
        try:
            if len(calls) == 1:
                old_started.set()
                assert release_old.wait(10), "test did not release old snapshot"
            else:
                new_started.set()
                assert release_new.wait(10), "test did not release new snapshot"
            return original_parse(text, **kwargs)
        finally:
            active -= 1

    def observe_feed(line, gen):
        original_feed(line, gen)
        if "10:01:00.100" in line:
            new_consumed.set()

    original_snapshot = tailer._live_core.snapshot

    def observe_snapshot(*args, **kwargs):
        report = original_snapshot(*args, **kwargs)
        if report and report["battle"]["duration_ms"] == 80:
            new_published.set()
        return report

    monkeypatch.setattr(live, "parse_overlay_battle_snapshot_text", blocked_parse)
    monkeypatch.setattr(tailer, "_parse", observe_feed)
    monkeypatch.setattr(tailer._live_core, "snapshot", observe_snapshot)
    reader = threading.Thread(target=tailer.run, daemon=True)
    reader.start()
    try:
        assert old_started.wait(5)
        with path.open("ab") as stream:
            stream.write(trace_lines("10:01:00", 200))
        # The previous parser remains blocked. An independent log reader must
        # consume the reset/new hit anyway and clear the visible old battle.
        assert new_consumed.wait(5)
        assert not release_old.is_set()
        assert tailer._live_core.snapshot(refresh=False) is None
        assert tailer.get_sorted() == []
        assert tailer.get_sorted_rdps() == []
        assert len(calls) == maximum_active == 1
        release_old.set()
        # No further writes: the EOF path must pick up the dirty new battle.
        assert new_started.wait(5)
        assert tailer._live_core.snapshot(refresh=False) is None
        assert maximum_active == 1
        release_new.set()
        assert new_published.wait(5)
        report = tailer._live_core.snapshot(refresh=False)
        assert report["battle"]["duration_ms"] == 80
        assert sum(row["total_damage"] for row in report["participants"]) == 200
        assert len(calls) == 2
    finally:
        release_old.set()
        release_new.set()
        tailer.stop()
        reader.join(10)
    assert not reader.is_alive()
    assert active == 0


def test_official_completion_arriving_during_snapshot_is_published_at_eof(monkeypatch, tmp_path):
    path = tmp_path / "trace.log"
    path.write_bytes(trace_lines())
    tailer = make_tailer(monkeypatch, path)
    monkeypatch.setattr(overlay, "TAIL_INTERVAL", 0.005)
    started, release = threading.Event(), threading.Event()
    completed_input, completed_report = threading.Event(), threading.Event()
    original_parse = live.parse_overlay_battle_snapshot_text
    original_feed = tailer._parse
    original_snapshot = tailer._live_core.snapshot
    calls = []

    def blocked_parse(text, **kwargs):
        calls.append(text)
        if len(calls) == 1:
            started.set()
            assert release.wait(10)
        return original_parse(text, **kwargs)

    def observe_feed(line, gen):
        original_feed(line, gen)
        if "OFFICIAL_TIMER_END" in line:
            completed_input.set()

    def observe_snapshot(*args, **kwargs):
        report = original_snapshot(*args, **kwargs)
        if report and report["battle"]["duration_ms"] == 4000:
            completed_report.set()
        return report

    monkeypatch.setattr(live, "parse_overlay_battle_snapshot_text", blocked_parse)
    monkeypatch.setattr(tailer, "_parse", observe_feed)
    monkeypatch.setattr(tailer._live_core, "snapshot", observe_snapshot)
    reader = threading.Thread(target=tailer.run, daemon=True)
    reader.start()
    try:
        assert started.wait(5)
        with path.open("ab") as stream:
            stream.write(b"[10:00:06.000] OFFICIAL_TIMER_END source=SC_GAME_MECHANICS_SYNC_CHALLENGE_COMPLETE gameId=indie_battletower015_ex isPass=1 passTime=4000\n")
        assert completed_input.wait(5)
        release.set()
        assert completed_report.wait(5)
        assert tailer._live_core.snapshot(refresh=False)["battle"]["duration_ms"] == 4000
        assert len(calls) == 2
    finally:
        release.set()
        tailer.stop()
        reader.join(10)
    assert not reader.is_alive()


def test_obsolete_reader_generation_cannot_feed_a_manually_reset_core(monkeypatch, tmp_path):
    path = tmp_path / "trace.log"
    path.write_bytes(trace_lines())
    tailer = make_tailer(monkeypatch, path)
    old_generation = tailer._gen
    with tailer.lock:
        tailer._gen += 1
        tailer._live_core.reset()
    tailer._parse(trace_lines("10:01:00").decode().splitlines()[-1], old_generation)
    assert tailer._live_core._lines == []
    assert tailer._live_core.snapshot(refresh=False) is None


def test_idle_reader_restores_data_watermark_after_an_explicit_clock_observation(monkeypatch, tmp_path):
    path = tmp_path / "trace.log"
    path.write_bytes(trace_lines())
    tailer = make_tailer(monkeypatch, path)
    explicit = tailer._live_core.snapshot(now_ms=36_060_100)
    assert explicit["battle"]["duration_ms"] == 60_080
    assert not tailer._live_core._dirty
    monkeypatch.setattr(overlay.time, "sleep", lambda _: tailer.stop())
    tailer.run()
    report = tailer._live_core.snapshot(refresh=False)
    assert report["battle"]["duration_ms"] == 80
    assert sum(row["total_damage"] for row in report["participants"]) == 100
