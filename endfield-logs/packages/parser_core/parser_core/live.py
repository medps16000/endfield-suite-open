from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from parser_core.unified import parse_overlay_battle_snapshot_text

LOGGER = logging.getLogger(__name__)

_TIMESTAMP_RE = re.compile(r"^\[(\d{2}):(\d{2}):(\d{2})\.(\d{3})\]")
_HIT_RE = re.compile(r"\bHP_V2\s+#\d+\b")
_LIVE_CONTEXT_RE = re.compile(
    r"(?:\b(?:DUNGEON_CONTEXT|BATTLE_RESULT|SQUAD|ENTITY_STATS|LOADOUT(?:_STATS)?|BUFF_START|ATTR_MOD|DMG_MOD)\b|"
    r"\bBB\[\d+\](?=:))"
)
_PARTY_ACTION_RE = re.compile(r"\bSKILL_CAST_START\b")
_TIMER_START_RE = re.compile(r"\bGAME_TIMER_START\b")
_TIMER_END_RE = re.compile(r"\bGAME_TIMER_END\b")
_TIMER_RESET_RE = re.compile(r"\bGAME_TIMER_RESET\b")
_OFFICIAL_TIMER_START_RE = re.compile(r"\bOFFICIAL_TIMER_START\b")
_OFFICIAL_TIMER_END_RE = re.compile(r"\bOFFICIAL_TIMER_END\b")
_DAY_MS = 24 * 60 * 60 * 1000
_LIVE_CLOCK_MAX_DRIFT_MS = 5 * 60 * 1000


def _line_timestamp_ms(line: str) -> int | None:
    match = _TIMESTAMP_RE.match(line)
    if not match:
        return None
    hours, minutes, seconds, millis = (int(part) for part in match.groups())
    return (((hours * 60) + minutes) * 60 + seconds) * 1000 + millis


def _current_clock_ms() -> int:
    now = datetime.now().astimezone()
    return (((now.hour * 60) + now.minute) * 60 + now.second) * 1000 + now.microsecond // 1000


def _clock_distance_ms(left_ms: int, right_ms: int) -> int:
    delta = abs(left_ms - right_ms) % _DAY_MS
    return min(delta, _DAY_MS - delta)


def _format_log_timestamp_ms(abs_ms: int) -> str:
    clock_ms = abs_ms % _DAY_MS
    hours, rem = divmod(clock_ms, 60 * 60 * 1000)
    minutes, rem = divmod(rem, 60 * 1000)
    seconds, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


def _preserved_context_lines(lines: list[str]) -> list[str]:
    boundary = -1
    for index, line in enumerate(lines):
        if (
            _TIMER_START_RE.search(line)
            or _TIMER_END_RE.search(line)
            or _TIMER_RESET_RE.search(line)
            or _OFFICIAL_TIMER_END_RE.search(line)
        ):
            boundary = index
    return [line for line in lines[boundary + 1 :] if _LIVE_CONTEXT_RE.search(line)]


class LiveOverlayBattleParser:
    """Live line-fed battle parser for overlay display.

    The class keeps only the current battle window and recomputes the shared
    parser report as new trace lines arrive. It gives the overlay real-time
    snapshots while keeping damage/rDPS rules in parser_core.
    """

    def __init__(
        self,
        *,
        idle_split_ms: int = 30_000,
        max_lines: int = 30_000,
        min_reparse_interval_ms: int = 0,
        align_to_data_watermark: bool = False,
    ) -> None:
        self.idle_split_ms = idle_split_ms
        self.max_lines = max_lines
        # Full text re-parse is O(buffered lines); polling callers (overlay) set
        # this so at most one re-parse runs per interval. 0 keeps every
        # snapshot() call exact for batch/test callers.
        self.min_reparse_interval_ms = min_reparse_interval_ms
        # DPS consumers can publish elapsed time at the same processed log
        # watermark as damage, even when their consumer is behind wall time.
        self._align_to_data_watermark = bool(align_to_data_watermark)
        self._last_parse_monotonic: float | None = None
        self._force_reparse = False
        self._lines: list[str] = []
        self._last_clock_ms: int | None = None
        self._day_offset_ms = 0
        self._last_hit_abs_ms: int | None = None
        self._official_timer_active = False
        self._official_timer_start_abs_ms: int | None = None
        self._official_timer_game_id = ""
        self._hit_count = 0
        self._snapshot: dict[str, Any] | None = None
        self._snapshot_from_explicit_clock = False
        self._dirty = False
        self._revision = 0
        self._generation = 0
        self._closed_by_timer_end = False
        self._closed_by_official_timer_end = False
        self._live_clock_enabled = True
        self._lock = threading.Lock()

    def reset(self) -> None:
        with self._lock:
            self._revision += 1
            self._generation += 1
            self._lines.clear()
            self._last_clock_ms = None
            self._day_offset_ms = 0
            self._last_hit_abs_ms = None
            self._official_timer_active = False
            self._official_timer_start_abs_ms = None
            self._official_timer_game_id = ""
            self._hit_count = 0
            self._snapshot = None
            self._snapshot_from_explicit_clock = False
            self._dirty = False
            self._closed_by_timer_end = False
            self._closed_by_official_timer_end = False
            self._last_parse_monotonic = None
            self._force_reparse = False

    def set_live_clock_enabled(self, enabled: bool) -> None:
        """Allow wall-clock advancement; data-watermark alignment still wins."""
        with self._lock:
            enabled = bool(enabled)
            if self._live_clock_enabled == enabled:
                return
            self._live_clock_enabled = enabled
            self._revision += 1
            self._dirty = True
            self._force_reparse = True

    def feed_lines(self, lines: Iterable[str]) -> dict[str, Any] | None:
        snapshot = None
        for line in lines:
            snapshot = self.feed_line(line)
        return snapshot

    def feed_line(self, line: str) -> dict[str, Any] | None:
        raw_line = line if line.endswith("\n") else f"{line}\n"
        if _TIMER_RESET_RE.search(raw_line):
            self.reset()
            return None
        clock_ms = _line_timestamp_ms(raw_line)
        abs_ms = None
        is_hit = bool(_HIT_RE.search(raw_line))
        is_context = bool(_LIVE_CONTEXT_RE.search(raw_line))
        is_party_action = bool(_PARTY_ACTION_RE.search(raw_line))
        is_official_timer_start = bool(_OFFICIAL_TIMER_START_RE.search(raw_line))
        is_official_timer_end = bool(_OFFICIAL_TIMER_END_RE.search(raw_line))
        is_timer_start = is_official_timer_start or (
            bool(_TIMER_START_RE.search(raw_line)) and not self._official_timer_active
        )
        is_timer_end = bool(_OFFICIAL_TIMER_END_RE.search(raw_line)) or bool(_TIMER_END_RE.search(raw_line))

        with self._lock:
            self._revision += 1
            if clock_ms is not None:
                if self._last_clock_ms is not None and clock_ms + 300_000 < self._last_clock_ms:
                    self._day_offset_ms += _DAY_MS
                self._last_clock_ms = clock_ms
                abs_ms = clock_ms + self._day_offset_ms

                if (
                    not is_hit
                    and not self._official_timer_active
                    and not self._closed_by_timer_end
                    and self._last_hit_abs_ms is not None
                    and abs_ms - self._last_hit_abs_ms > self.idle_split_ms
                ):
                    self._lines = _preserved_context_lines(self._lines)
                    self._generation += 1
                    self._last_hit_abs_ms = None
                    self._hit_count = 0

            if is_timer_start:
                self._lines = _preserved_context_lines(self._lines)
                self._generation += 1
                self._last_hit_abs_ms = None
                self._official_timer_active = is_official_timer_start
                self._official_timer_start_abs_ms = abs_ms if is_official_timer_start and abs_ms is not None else None
                if is_official_timer_start:
                    game_match = re.search(r"\bgameId=(\S+)", raw_line)
                    self._official_timer_game_id = game_match.group(1) if game_match else ""
                else:
                    self._official_timer_game_id = ""
                self._hit_count = 0
                self._snapshot = None
                self._closed_by_timer_end = False
                self._closed_by_official_timer_end = False

            if is_hit and abs_ms is not None:
                if self._closed_by_timer_end and not self._closed_by_official_timer_end:
                    self._lines = _preserved_context_lines(self._lines)
                    self._generation += 1
                    self._last_hit_abs_ms = None
                    self._official_timer_active = False
                    self._hit_count = 0
                    self._snapshot = None
                    self._closed_by_timer_end = False
                    self._closed_by_official_timer_end = False
                if not self._closed_by_timer_end:
                    if (
                        not self._official_timer_active
                        and self._last_hit_abs_ms is not None
                        and abs_ms - self._last_hit_abs_ms > self.idle_split_ms
                    ):
                        self._lines = _preserved_context_lines(self._lines)
                        self._generation += 1
                        self._official_timer_active = False
                        self._hit_count = 0
                        self._snapshot = None
                    self._last_hit_abs_ms = abs_ms
                    self._hit_count += 1

            self._lines.append(raw_line)
            if len(self._lines) > self.max_lines:
                del self._lines[: len(self._lines) - self.max_lines]

            self._dirty = (
                self._dirty
                or self._hit_count > 0
                or is_context
                or is_party_action
                or is_timer_start
                or (is_timer_end and bool(self._lines))
            )
            if is_timer_end:
                if is_official_timer_end:
                    self._official_timer_active = False
                    self._official_timer_start_abs_ms = None
                    self._official_timer_game_id = ""
                    self._closed_by_timer_end = True
                    self._closed_by_official_timer_end = True
                    self._force_reparse = True
                elif not self._official_timer_active:
                    self._closed_by_timer_end = True
                    self._force_reparse = True
            return self._snapshot

    def _live_timer_snapshot_line(self, now_ms: int | None = None) -> str | None:
        if (
            not self._official_timer_active
            or self._official_timer_start_abs_ms is None
            or self._hit_count <= 0
            or self._closed_by_timer_end
        ):
            return None
        if now_ms is None and (self._align_to_data_watermark or not self._live_clock_enabled):
            if self._last_clock_ms is None:
                return None
            now_clock_ms = self._last_clock_ms
        elif now_ms is None:
            now_clock_ms = _current_clock_ms()
            if self._last_clock_ms is not None and _clock_distance_ms(now_clock_ms, self._last_clock_ms) > _LIVE_CLOCK_MAX_DRIFT_MS:
                # A replayed/paused log must freeze at its last observed game
                # timestamp. Returning no tick would resurrect an earlier
                # phase GAME_TIMER_END and make the overlay drop later hits.
                now_clock_ms = self._last_clock_ms
        else:
            now_clock_ms = now_ms % _DAY_MS
        day_offset_ms = self._day_offset_ms
        if self._last_clock_ms is not None and now_clock_ms + 300_000 < self._last_clock_ms:
            day_offset_ms += _DAY_MS
        now_abs_ms = now_clock_ms + day_offset_ms
        if self._last_hit_abs_ms is not None:
            now_abs_ms = max(now_abs_ms, self._last_hit_abs_ms)
        elapsed_ms = max(now_abs_ms - self._official_timer_start_abs_ms, 1)
        parts = [
            f"[{_format_log_timestamp_ms(now_abs_ms)}]",
            "LIVE_TIMER_TICK",
            "source=LIVE_TIMER_TICK",
        ]
        if self._official_timer_game_id:
            parts.append(f"gameId={self._official_timer_game_id}")
        parts.append(f"elapsedMs={elapsed_ms}")
        return " ".join(parts) + "\n"

    def snapshot(self, now_ms: int | None = None, *, refresh: bool = True) -> dict[str, Any] | None:
        """Return the shared report; explicit ``now_ms`` is an observer clock.

        In data-watermark mode, normal calls use only the last processed log
        timestamp. An explicit observer parameter retains its existing clock
        semantics and must not become the cached time for later normal calls.
        """
        with self._lock:
            # UI readers use the latest completed shared-parser result. The
            # log consumer refreshes it; rendering must not run a full replay.
            if not refresh:
                return self._snapshot
            if (self._align_to_data_watermark and now_ms is None
                    and not self._dirty and not self._snapshot_from_explicit_clock):
                return self._snapshot
            live_timer_line = self._live_timer_snapshot_line(now_ms)
            if not self._dirty and live_timer_line is None:
                return self._snapshot
            if (
                self.min_reparse_interval_ms > 0
                and not self._force_reparse
                and not (self._align_to_data_watermark and now_ms is None
                         and self._snapshot_from_explicit_clock)
                and self._snapshot is not None
                and self._last_parse_monotonic is not None
                and (time.monotonic() - self._last_parse_monotonic) * 1000.0 < self.min_reparse_interval_ms
            ):
                return self._snapshot
            text = "".join(self._lines)
            revision, generation = self._revision, self._generation
            if live_timer_line is not None:
                text += live_timer_line
        try:
            snapshot = parse_overlay_battle_snapshot_text(text, file_name="live-overlay.log")
        except Exception:
            LOGGER.exception("live overlay parser failed; dropping stale snapshot")
            with self._lock:
                if generation != self._generation:
                    return self._snapshot
                self._snapshot = None
                self._snapshot_from_explicit_clock = False
                # The same unchanged input must not be reparsed on every UI
                # read. A later trace line marks the parser dirty again and
                # preserves the original failure in logs without flooding it.
                if revision == self._revision:
                    self._dirty = False
                    self._force_reparse = False
                self._last_parse_monotonic = time.monotonic()
                return None
        with self._lock:
            if generation != self._generation:
                return self._snapshot
            self._snapshot = snapshot
            self._snapshot_from_explicit_clock = now_ms is not None
            if revision == self._revision:
                self._dirty = False
                self._force_reparse = False
            self._last_parse_monotonic = time.monotonic()
            return self._snapshot
