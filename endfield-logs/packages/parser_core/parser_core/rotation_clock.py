"""Independent observer clock using a version-proven native game algorithm.

This is an independent clock, not a claim to recover the game's private callback
timestamp or login state. Native constants must be supplied by a complete proof
bound to the current GameAssembly; there are no guessed defaults. Capture epoch
milliseconds and observer UTC milliseconds are measured inputs. Logical battle
ticks never calibrate this clock.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import re
import struct
import time
from typing import Any

ALGORITHM_ID = "endfield-native-observer-v1"
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_UINT64_MAX = (1 << 64) - 1
_INT64_MAX = (1 << 63) - 1


def _uint(value: Any) -> int | None:
    if type(value) is int:
        result = value
    elif isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
        result = int(value)
    else:
        return None
    return result if 0 <= result <= _UINT64_MAX else None


def _signed(value: int, bits: int) -> int:
    mask = (1 << bits) - 1
    value &= mask
    return value - (1 << bits) if value >= (1 << (bits - 1)) else value


def _float32(value: int | float) -> float:
    return struct.unpack("<f", struct.pack("<f", value))[0]


def utc_now_ms() -> float:
    return time.time_ns() / 1_000_000.0


@dataclass(frozen=True)
class RotationClockSpec:
    game_assembly_sha256: str
    metadata_sha256: str
    dump_sha256: str
    session_thread_sleep_ms: int
    max_server_bias_ms: int
    login_bias_threshold_ms: int
    initial_bias_ms: int
    missing_freeze_key_ms: int
    entry_creates_fresh_subgame: bool = False

    def __post_init__(self) -> None:
        if not all(isinstance(value, str) and _HASH.fullmatch(value) for value in (
            self.game_assembly_sha256, self.metadata_sha256, self.dump_sha256
        )):
            raise ValueError("clock proof requires three SHA-256 source identities")
        for value in (self.session_thread_sleep_ms, self.max_server_bias_ms,
                      self.login_bias_threshold_ms, self.missing_freeze_key_ms):
            if type(value) is not int or not 0 <= value <= _INT64_MAX:
                raise ValueError("clock constants must be explicitly proven integer values")
        if type(self.initial_bias_ms) is not int or not -_INT64_MAX - 1 <= self.initial_bias_ms <= _INT64_MAX:
            raise ValueError("clock initial bias must be explicitly proven")
        if type(self.entry_creates_fresh_subgame) is not bool:
            raise ValueError("entry lifecycle proof flag must be boolean")

    @classmethod
    def from_proof(cls, payload: Any, *, expected_game_assembly_sha256: str) -> "RotationClockSpec":
        if (not isinstance(payload, dict) or type(payload.get("schemaVersion")) is not int
                or payload["schemaVersion"] != 1 or payload.get("algorithmId") != ALGORITHM_ID
                or payload.get("proofComplete") is not True
                or not _HASH.fullmatch(str(expected_game_assembly_sha256))
                or payload.get("gameAssemblySha256") != expected_game_assembly_sha256):
            raise ValueError("clock proof is missing, incomplete, or belongs to another GameAssembly")
        constants = payload.get("constants")
        if not isinstance(constants, dict):
            raise ValueError("clock proof has no native constants")
        try:
            return cls(payload["gameAssemblySha256"], payload["metadataSha256"], payload["dumpSha256"],
                       constants["sessionThreadSleepMs"], constants["maxServerBiasMs"],
                       constants["loginBiasThresholdMs"], constants["initialBiasMs"], constants["missingFreezeKeyMs"],
                       payload.get("entryCreatesFreshSubGame", False))
        except KeyError as exc:
            raise ValueError("clock proof is missing a required native constant") from exc

    def provenance(self) -> dict[str, Any]:
        return {"algorithmId": ALGORITHM_ID, "gameAssemblySha256": self.game_assembly_sha256,
                "metadataSha256": self.metadata_sha256, "dumpSha256": self.dump_sha256,
                "scope": "independent_observer", "units": "milliseconds",
                "calibrationInput": "exact paired CS/SC_PING and measured captureMs",
                "initialization": "native static bias then first matched reply login branch",
                "elapsedInput": "floor(measured observer UTC + bias) - start - ChallengeTime",
                "entryCreatesFreshSubGame": self.entry_creates_fresh_subgame}


class RotationObserverClock:
    """Bounded packet consumer with native integer and float32 behavior."""

    def __init__(self, spec: RotationClockSpec | None = None) -> None:
        if spec is not None and not isinstance(spec, RotationClockSpec):
            raise TypeError("clock_spec must be a validated RotationClockSpec")
        self.spec = spec
        self.reset_session()

    def reset_session(self) -> None:
        self._bias_ms = self.spec.initial_bias_ms if self.spec else None
        self._ready = False
        self._invalid = False
        self._requests: dict[int, tuple[int, int | None]] = {}
        self._last_sync: dict[str, Any] | None = None
        self._frozen: bool | None = None
        self._freeze_state_invalid = False
        self._freeze_by_key: dict[int, int] = {}
        self._challenge_invalid = False
        self._counter_known = True  # fresh observer SubGame's proven ctor/default
        self._game_id: str | None = None
        self._instance_identity: tuple[str, str, int] | None = None
        self._entry_generation = 0
        self._instance_entry_generation: int | None = None
        self.clear_challenge()

    def clear_challenge(self) -> None:
        self._challenge: tuple[str, int] | None = None

    def reset_subgame(self, game_id: str | None = None) -> None:
        """Invoke only for a proven fresh object/reset; START alone is not one."""
        self.clear_challenge()
        self._game_id = game_id
        self._freeze_by_key = {}
        self._challenge_invalid = False
        self._counter_known = True

    def mark_counter_unknown(self, game_id: str | None = None) -> None:
        """A boundary whose native LUT reset is unproved cannot imply zero."""
        self.clear_challenge()
        if game_id is not None:
            self._game_id = game_id
        self._counter_known = False

    def bind_game(self, game_id: str) -> None:
        if self._game_id is not None and game_id != self._game_id:
            self.mark_counter_unknown(game_id)
        else:
            self._game_id = game_id

    def note_entry_boundary(self) -> None:
        """Explicit scene/leave/completion separates subsequent ENTER events."""
        self._entry_generation += 1

    def observe_instance(self, game_id: Any, instance_id: Any, unique_id: Any) -> bool:
        unique = _uint(unique_id)
        if not isinstance(game_id, str) or not game_id or not isinstance(instance_id, str) or not instance_id or unique in (None, 0):
            self.mark_counter_unknown(game_id if isinstance(game_id, str) else None)
            return False
        identity = (game_id, instance_id, unique)
        changed = self._instance_identity is not None and self._instance_identity != identity
        new_entry = self._instance_identity != identity or self._instance_entry_generation != self._entry_generation
        if (self.spec is not None and self.spec.entry_creates_fresh_subgame
                and new_entry):
            self.reset_subgame(game_id)
        elif changed or (self._instance_identity is not None and new_entry):
            self.mark_counter_unknown(game_id)
        else:
            self.bind_game(game_id)
        self._instance_identity = identity
        self._instance_entry_generation = self._entry_generation
        return changed or (new_entry and self._entry_generation > 0)

    def begin_challenge(self, game_id: str, start_ms: int) -> None:
        identity = (game_id, start_ms)
        if identity == self._challenge:
            return
        self.clear_challenge()
        if self._game_id is not None and game_id != self._game_id:
            # Never carry another subgame's cumulative counter into this one.
            # An explicit fresh-object/reset lifecycle clears this condition.
            self._counter_known = False
        self._game_id = game_id
        self._challenge = identity

    def set_freeze_state(self, value: bool | None) -> None:
        self._frozen = value
        self._freeze_state_invalid = type(value) is not bool

    def update_freeze(self, game_id: str, pairs: list[tuple[int, int]]) -> None:
        if self._game_id is None or game_id != self._game_id:
            return
        if (not isinstance(pairs, list) or len(pairs) > 512
                or any(not isinstance(pair, tuple) or len(pair) != 2
                       or type(pair[0]) is not int or type(pair[1]) is not int
                       or pair[0] < 0 or pair[1] < 0 or pair[1] > _INT64_MAX for pair in pairs)
                or len({pair[0] for pair in pairs}) != len(pairs)):
            self._challenge_invalid = True
            return
        for key, value in pairs:
            # Native HandleFreezeTimeInfo assigns received counters. A zero
            # update after reset is evidence, not a monotonicity estimate.
            self._freeze_by_key[key] = value
            if key == 0:
                self._counter_known = True

    def invalidate_challenge(self) -> None:
        self._challenge_invalid = True

    def _calibrated_ms(self, now_ms: int | float) -> int | None:
        if (self._bias_ms is None or type(now_ms) not in (int, float)
                or not math.isfinite(now_ms) or not 0 <= now_ms <= _INT64_MAX):
            return None
        value = float(now_ms) + float(self._bias_ms)
        if not -2**63 <= value < 2**63:
            return None
        return math.floor(value)

    def observe_sync(self, *, source: str, client_ts: Any, capture_ms: Any,
                     server_ts: Any = None, logical_ts: Any = None) -> bool:
        if self.spec is None or self._invalid or source not in {"CS_PING", "SC_PING"}:
            return False
        client, capture = _uint(client_ts), _uint(capture_ms)
        if client in (None, 0) or capture in (None, 0) or capture > _INT64_MAX:
            return False
        if source == "CS_PING":
            logical = _uint(logical_ts) if logical_ts is not None else None
            if logical_ts is not None and logical is None:
                return False
            request = (capture, logical)
            if client in self._requests and self._requests[client] != request:
                self._invalid = True
                return False
            if len(self._requests) >= 4096 and client not in self._requests:
                self._invalid = True
                return False
            self._requests[client] = request
            return True
        server = _uint(server_ts)
        if server in (None, 0) or client not in self._requests:
            return False
        request_capture, logical = self._requests.pop(client)
        before = self._bias_ms
        login_changed = False
        if not self._ready:
            calibrated = self._calibrated_ms(capture)
            if calibrated is None:
                return False
            login_delta = _signed(server - calibrated, 64)
            if _float32(abs(login_delta)) > _float32(self.spec.login_bias_threshold_ms):
                self._bias_ms = login_delta
                login_changed = True
        # Native arithmetic truncates to int32 before subtracting, including
        # wraparound, and clamps ping to at least 1. No fitted delay is used.
        raw_ping = _signed(_signed(capture, 32) - self.spec.session_thread_sleep_ms - _signed(client, 32), 32)
        ping = max(1, raw_ping)
        candidate = _signed(server + ping // 2 - capture, 64)
        delta = _signed(self._bias_ms - candidate, 64)
        setter_changed = _float32(abs(delta)) > _float32(self.spec.max_server_bias_ms)
        if setter_changed:
            self._bias_ms = candidate
        self._ready = True
        self._last_sync = {"clientTs": client, "serverTs": server, "captureMs": capture,
                           "requestCaptureMs": request_capture, "logicalTs": logical,
                           "nativePingMs": ping, "candidateBiasMs": candidate,
                           "biasBeforeMs": before, "biasAfterMs": self._bias_ms,
                           "loginBranchChangedBias": login_changed, "pingSetterChangedBias": setter_changed}
        return True

    def elapsed(self, now_ms: int | float | None = None) -> int | None:
        if (self.spec is None or not self._ready or self._invalid or self._challenge is None
                or self._challenge_invalid or not self._counter_known
                or self._frozen is True or self._freeze_state_invalid):
            return None
        calibrated = self._calibrated_ms(utc_now_ms() if now_ms is None else now_ms)
        if calibrated is None:
            return None
        freeze_ms = self._freeze_by_key.get(0, self.spec.missing_freeze_key_ms)
        result = _signed(calibrated - self._challenge[1] - freeze_ms, 64)
        return result if result >= 0 else None

    def diagnostics(self) -> dict[str, Any]:
        return {"ready": self._ready and not self._invalid, "biasMs": self._bias_ms,
                "pendingPingPairs": len(self._requests), "lastSync": dict(self._last_sync) if self._last_sync else None,
                "challengeInvalid": self._challenge_invalid, "freezeStateInvalid": self._freeze_state_invalid,
                "challengeCounterKnown": self._counter_known,
                "instanceIdentity": self._instance_identity,
                "frozen": self._frozen, "challengeFreezeMs": self._freeze_by_key.get(0),
                "provenance": self.spec.provenance() if self.spec else None}
