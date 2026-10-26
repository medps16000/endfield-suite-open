"""Evidence-fed rotation guidance, separate from damage parsing.

The capture receipt clock, client simulation tick and official challenge timer
are different clocks. A version-proven clock spec enables an independent observer
using the native calibration/counting algorithm. No logical tick conversion,
fitted offset or claim about the game's private callback time is performed.
"""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from parser_core.timer_evidence import START_SOURCE
from parser_core.rotation_clock import RotationClockSpec, RotationObserverClock

_ADMIN_ALIASES = {"chr_0002_endminm": "chr_9000_endmin", "chr_0003_endminf": "chr_9000_endmin"}
_LINE = re.compile(r"^(?:\[\d{2}:\d{2}:\d{2}\.\d{3}\]\s+)?([A-Z][A-Z0-9_]*)\b(.*)$")
_FIELD = re.compile(r"\b([A-Za-z][A-Za-z0-9_]*)=(\[[^\]]*\]|\S+)")
_MEMBER = re.compile(r"(.+)_([0-9]+)\Z")
_UINT = re.compile(r"[0-9]+\Z")
_OFFICIAL_PROGRESS_SOURCE = "VERIFIED_OFFICIAL_CHALLENGE_CLOCK"
_ACTION_NAMES = {"battle_skill": "战技", "combo_skill": "连携技", "ultimate_skill": "终结技"}


def _canonical(value: str) -> str:
    return _ADMIN_ALIASES.get(value, value)


def _integer(value: Any, maximum: int | None = None) -> int | None:
    if type(value) is int:
        result = value
    elif isinstance(value, str) and _UINT.fullmatch(value):
        result = int(value)
    else:
        return None
    return result if result >= 0 and (maximum is None or result <= maximum) else None


def _text(value: Any, maximum: int, *, empty: bool = False) -> bool:
    return isinstance(value, str) and (empty or bool(value.strip())) and len(value) <= maximum


def _validated_plan(raw: Any, schema_version: int = 1) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    if not (_text(raw.get("id"), 160) and _text(raw.get("name"), 80)
            and _text(raw.get("dungeonId"), 160) and type(raw.get("enabled")) is bool
            and type(raw.get("position")) is int and 0 <= raw["position"]
            and type(raw.get("revision")) is int and raw["revision"] >= 1
            and _text(raw.get("displayNotes", ""), 2000, empty=True)):
        return None
    characters = raw.get("characterIds")
    if (not isinstance(characters, list) or len(characters) != 4
            or not all(_text(char, 160) for char in characters)):
        return None
    characters = [_canonical(char) for char in characters]
    if len(set(characters)) != 4:
        return None
    raw_steps = raw.get("steps")
    if not isinstance(raw_steps, list) or not 1 <= len(raw_steps) <= 500:
        return None
    steps, ids, previous_time = [], set(), -1
    for step in raw_steps:
        if (not isinstance(step, dict) or not _text(step.get("id"), 80)
                or step["id"] in ids or type(step.get("atMs")) is not int
                or not previous_time <= step["atMs"] <= 3_600_000
                or step["atMs"] < 0 or not _text(step.get("characterId"), 160)
                or _canonical(step["characterId"]) not in characters
                or not _text(step.get("note", ""), 500, empty=True)
                or not _text(step.get("phase", ""), 80, empty=True)):
            return None
        skill, action = step.get("skillId"), step.get("actionKind")
        if (("skillId" in step and not _text(skill, 160))
                or ("actionKind" in step and (schema_version != 2 or not isinstance(action, str) or action not in _ACTION_NAMES))
                or ("skillId" in step) == ("actionKind" in step)):
            return None
        ids.add(step["id"])
        previous_time = step["atMs"]
        steps.append({"id": step["id"], "atMs": step["atMs"],
                      "characterId": _canonical(step["characterId"]),
                      **({"skillId": skill} if skill is not None else {"actionKind": action}),
                      "note": step.get("note", ""), "phase": step.get("phase", "")})
    return {"id": raw["id"], "name": raw["name"], "dungeonId": raw["dungeonId"],
            "characterIds": characters, "enabled": raw["enabled"], "position": raw["position"],
            "steps": steps, "displayNotes": raw.get("displayNotes", ""),
            "revision": raw["revision"], "updatedAt": raw.get("updatedAt", "")}


class RotationGuideEngine:
    """A deterministic, bounded event consumer; callers serialize access.

    ``require_official_clock=False`` is an explicit product/user policy choice,
    not a fallback when official clock evidence is absent. ``now_ms`` supplies
    measured observer UTC milliseconds to the official-algorithm clock. Packet
    test mode still advances only from observed client ticks.
    Library changes are latched for the next battle; an account change invalidates
    the active battle. A capture gap also invalidates it instead of replaying
    potentially missed user actions as though they had never happened.
    """

    def __init__(self, *, require_official_clock: bool = True, catalog: dict[str, Any] | None = None,
                 clock_spec: RotationClockSpec | None = None, allow_lab_progress: bool = False) -> None:
        self.require_official_clock = bool(require_official_clock)
        self._clock = RotationObserverClock(clock_spec)
        self._allow_lab_progress = bool(allow_lab_progress)
        self._account_id: str | None = None
        self._plans: list[dict[str, Any]] = []
        self._available = True
        self._character_names: dict[str, str] = {}
        self._skill_names: dict[tuple[str, str], str] = {}
        self._actions: dict[tuple[str, str], dict[str, Any]] = {}
        self._dungeon_id: str | None = None
        self._scene_id: str | None = None
        self._game_inst_id: str | None = None
        self._squad: dict[str, str] = {}
        self._clear_battle()
        self.replace_catalog(catalog)

    def _clear_battle(self) -> None:
        self._clock.clear_challenge()
        self._challenge: tuple[str, int] | None = None
        self._start_tick: int | None = None
        self._last_tick: int | None = None
        self._official_elapsed: int | None = None
        self._clock_conflict = False
        self._active: dict[str, Any] | None = None
        self._battle_plans: list[dict[str, Any]] = []
        self._battle_actions: dict[tuple[str, str], dict[str, Any]] = {}
        self._battle_character_names: dict[str, str] = {}
        self._battle_skill_names: dict[tuple[str, str], str] = {}
        self._active_squad: dict[str, str] = {}
        self._completed: list[dict[str, int | None] | None] = []
        self._seen_casts: set[tuple[str, str, int]] = set()
        self._seen_sequences: dict[tuple[str, int], tuple[str, str, int]] = {}
        self._paused: bool | None = None
        self._freeze_state_invalid = False
        self._last_confirmed_snapshot: dict[str, Any] | None = None

    def _clear_context(self) -> None:
        self._clear_battle()
        self._dungeon_id = self._scene_id = self._game_inst_id = None
        self._squad = {}

    def set_available(self, available: bool) -> None:
        if not available and self._available:
            had_challenge = self._challenge is not None
            self._clock.reset_session()
            if had_challenge:
                self._clock.mark_counter_unknown()
            self._clear_context()
        self._available = bool(available)

    def replace_catalog(self, catalog: Any) -> None:
        self._character_names, self._skill_names, self._actions = {}, {}, {}
        if (not isinstance(catalog, dict) or type(catalog.get("schemaVersion")) is not int
                or catalog["schemaVersion"] not in {1, 2}):
            return
        characters = catalog.get("characters")
        if not isinstance(characters, list) or len(characters) > 500:
            return
        for char in characters:
            if not isinstance(char, dict) or not _text(char.get("id"), 160):
                continue
            key = _canonical(char["id"])
            if _text(char.get("name"), 160):
                self._character_names[key] = char["name"]
            skills = char.get("skills")
            if not isinstance(skills, list) or len(skills) > 1000:
                continue
            for skill in skills:
                if (isinstance(skill, dict) and _text(skill.get("id"), 160)
                        and _text(skill.get("name"), 160)):
                    self._skill_names[key, skill["id"]] = skill["name"]
            if catalog["schemaVersion"] != 2:
                continue
            actions = char.get("actions")
            if not isinstance(actions, list) or len(actions) > len(_ACTION_NAMES):
                continue
            seen: set[str] = set()
            invalid: set[str] = set()
            for action in actions:
                if not isinstance(action, dict) or not isinstance(action.get("kind"), str):
                    continue
                kind = action["kind"]
                if kind in seen:
                    invalid.add(kind)
                    self._actions.pop((key, kind), None)
                    continue
                seen.add(kind)
                roots = action.get("rootSkillIds")
                # Only the explicit catalog relation joins native cast roots.
                # Similar display names, root suffixes and group IDs are not evidence.
                if (kind not in _ACTION_NAMES or kind in invalid
                        or not _text(action.get("name"), 160) or not _text(action.get("skillName"), 160)
                        or not _text(action.get("iconUrl", ""), 2048, empty=True)
                        or not isinstance(roots, list) or not 1 <= len(roots) <= 1000
                        or not all(_text(root, 160) and (key, root) in self._skill_names for root in roots)
                        or len(set(roots)) != len(roots)):
                    continue
                self._actions[key, kind] = {"name": action["name"], "skillName": action["skillName"],
                                           "iconUrl": action.get("iconUrl", ""), "rootSkillIds": frozenset(roots)}

    def replace_plans(self, envelope: Any) -> bool:
        if (not isinstance(envelope, dict) or type(envelope.get("schemaVersion")) is not int
                or envelope["schemaVersion"] not in {1, 2} or not _text(envelope.get("accountId"), 160)
                or not isinstance(envelope.get("rotations"), list)
                or len(envelope["rotations"]) > 100):
            self._plans = []
            self._account_id = None
            self._clear_battle()
            return False
        plans = [_validated_plan(plan, envelope["schemaVersion"]) for plan in envelope["rotations"]]
        if (any(plan is None for plan in plans)
                or len({plan["id"] for plan in plans if plan}) != len(plans)
                or len({plan["position"] for plan in plans if plan}) != len(plans)):
            self._plans = []
            self._account_id = None
            self._clear_battle()
            return False
        if envelope["accountId"] != self._account_id:
            self._clear_battle()
        self._account_id = envelope["accountId"]
        self._plans = sorted(plans, key=lambda plan: (plan["position"], plan["id"]))
        return True

    def _select_plan(self) -> None:
        if self._active is not None or self._challenge is None or self._clock_conflict:
            return
        if self._dungeon_id != self._challenge[0] or len(self._squad) != 4:
            return
        elapsed = self._elapsed()
        if elapsed is None:
            return
        squad = set(self._squad.values())
        for plan in self._battle_plans:
            if plan["enabled"] and plan["dungeonId"] == self._dungeon_id and set(plan["characterIds"]) == squad:
                self._active = deepcopy(plan)
                self._active_squad = dict(self._squad)
                self._completed = [None] * len(plan["steps"])
                return

    def _elapsed(self, now_ms: int | float | None = None) -> int | None:
        if self._clock_conflict or self._challenge is None:
            return None
        if self.require_official_clock:
            if self._clock.spec is not None:
                return self._clock.elapsed(now_ms)
            return self._official_elapsed if self._allow_lab_progress else None
        if self._start_tick is None or self._last_tick is None:
            return None
        return self._last_tick - self._start_tick

    def _observe_tick(self, tick: int) -> None:
        if self.require_official_clock:
            return  # logical clock discontinuities cannot invalidate official time
        if self._start_tick is None:
            return
        if tick < self._start_tick or (self._last_tick is not None and tick < self._last_tick):
            self._clock_conflict = True
            return
        self._last_tick = tick

    def feed_line(self, line: str) -> None:
        if not self._available:
            return
        match = _LINE.match(line.strip())
        if match is None:
            return
        label, raw = match.groups()
        fields = dict(_FIELD.findall(raw))
        if label == "GAME_CLOCK_SYNC":
            self._clock.observe_sync(source=fields.get("source", ""), client_ts=fields.get("clientTs"),
                                     capture_ms=fields.get("captureMs"), server_ts=fields.get("serverTs"),
                                     logical_ts=fields.get("logicalTs"))
            self._select_plan()
            return
        if label in {"GAME_TIMER_RESET", "GAME_INST_LEAVE", "CAPTURE_SESSION_END"}:
            if label == "GAME_TIMER_RESET" and fields.get("source") == "CAPTURE_SESSION_START":
                self._clock.reset_session()
            else:
                self._clock.note_entry_boundary()
            self._clear_context()
            return
        if label in {"GAME_INST_RESTART", "BATTLE_RESET_STATUS", "OFFICIAL_TIMER_MISSING"}:
            if label == "GAME_INST_RESTART":
                self._clock.mark_counter_unknown(fields.get("gameId"))
            self._clear_battle()
            self._squad = {}
            return
        if label == "DUNGEON_CONTEXT":
            dungeon, scene = fields.get("dungeonId"), fields.get("sceneId")
            if not dungeon or dungeon == "unknown":
                self._clear_context()
                return
            if ((self._dungeon_id is not None and dungeon != self._dungeon_id)
                    or (self._scene_id is not None and scene is not None and scene != self._scene_id)):
                self._clock.note_entry_boundary()
                self._clear_context()
            self._dungeon_id, self._scene_id = dungeon, scene
        elif label == "GAME_INST_ENTER":
            identity = fields.get("gameInstId")
            changed = self._clock.observe_instance(fields.get("gameId"), identity, fields.get("gameUniqueId"))
            if changed or (self._game_inst_id is not None and identity != self._game_inst_id):
                self._clear_battle()
            self._game_inst_id = identity
        elif label == "SQUAD":
            tokens = fields.get("members", "")[1:-1].split()
            squad: dict[str, str] = {}
            for token in tokens:
                member = _MEMBER.fullmatch(token)
                if member and int(member[2]) > 0:
                    squad[member[2]] = _canonical(member[1])
            if (fields.get("size") != "4" or len(tokens) != 4 or len(squad) != 4
                    or len(set(squad.values())) != 4):
                self._squad = {}
                self._clear_battle()
                return
            if self._active is not None and squad != self._active_squad:
                self._clear_battle()
            self._squad = squad
        elif label == "ACTOR_MAP":
            actor = fields.get("id")
            if actor in self._squad:
                # A child/source mapping conflicts with its own-character proof.
                self._squad.pop(actor)
                self._clear_battle()
        elif label == "OFFICIAL_TIMER_START":
            start = _integer(fields.get("challengeStartTs"))
            game = fields.get("gameId")
            if fields.get("source") != START_SOURCE or not game or not start:
                self._clear_battle()
                return
            identity = (game, start)
            if identity != self._challenge:
                self._clear_battle()
                self._challenge = identity
                self._clock.begin_challenge(game, start)
                self._battle_plans = deepcopy(self._plans)
                # A sync may replace the library while fighting; this battle's
                # action identities and labels must keep their original meaning.
                self._battle_actions = deepcopy(self._actions)
                self._battle_character_names = dict(self._character_names)
                self._battle_skill_names = dict(self._skill_names)
        elif label == "OFFICIAL_TIMER_END":
            if self._challenge is not None and fields.get("gameId") == self._challenge[0]:
                self._clock.note_entry_boundary()
                self._clear_battle()
        elif label == "GAME_TIMER_START":
            tick = _integer(fields.get("startMs"))
            if (self._challenge is None or fields.get("source") != "PacketBattleState"
                    or fields.get("official") != "1" or tick is None):
                return
            if self._start_tick is None:
                self._start_tick = self._last_tick = tick
        elif label == "GAME_CLOCK_TICK" and fields.get("source") == "CS_BATTLE_OP":
            tick = _integer(fields.get("tickMs"))
            if tick is not None:
                self._observe_tick(tick)
        elif label == "OFFICIAL_TIMER_PROGRESS":
            # Reserved for a future, proven official timer provider. Current
            # capture never fabricates this marker from ping or receipt time.
            progress = _integer(fields.get("elapsedMs"))
            identity = (fields.get("gameId"), _integer(fields.get("challengeStartTs")))
            if (self._allow_lab_progress and fields.get("source") == _OFFICIAL_PROGRESS_SOURCE and identity == self._challenge
                    and progress is not None):
                if self._official_elapsed is not None and progress < self._official_elapsed:
                    self._clock_conflict = True
                else:
                    self._official_elapsed = progress
        elif label == "GAME_TIME_FREEZE_STATE":
            if fields.get("source") in {"SC_GAME_TIME_FREEZE_START_RSP", "SC_GAME_TIME_FREEZE_END_RSP"}:
                value = fields.get("inTimeFreeze")
                self._paused = value == "1" if value in {"0", "1"} else None
                self._freeze_state_invalid = value not in {"0", "1"}
                if self._freeze_state_invalid:
                    self._last_confirmed_snapshot = None
                self._clock.set_freeze_state(self._paused)
        elif label == "GAME_MECHANICS_TIME_FREEZE":
            if fields.get("source") == "SC_GAME_MECHANICS_MODIFY_INST_TIME_FREEZE":
                values = fields.get("freezeInfos", "[]" if fields.get("count") == "0" else "")
                if (not re.fullmatch(r"\[(?:[0-9]+:[0-9]+(?:,[0-9]+:[0-9]+)*)?\]", values)
                        or not str(fields.get("count", "")).isdecimal()):
                    if self._challenge and fields.get("gameId") == self._challenge[0]:
                        self._clock.invalidate_challenge()
                else:
                    pairs = [tuple(map(int, token.split(":"))) for token in values[1:-1].split(",") if token]
                    if len(pairs) != int(fields["count"]):
                        if self._challenge and fields.get("gameId") == self._challenge[0]:
                            self._clock.invalidate_challenge()
                    else:
                        self._clock.update_freeze(fields.get("gameId", ""), pairs)
        elif label == "SKILL_CAST_START":
            self._feed_cast(fields)
        self._select_plan()

    def _feed_cast(self, fields: dict[str, str]) -> None:
        tick = _integer(fields.get("startMs"))
        if tick is not None:
            self._observe_tick(tick)
        self._select_plan()
        if self._active is None or self._elapsed() is None or self._paused or self._freeze_state_invalid:
            return
        owner, skill, inst = fields.get("ownerId"), fields.get("skill"), fields.get("inst")
        seq = _integer(fields.get("seq"))
        if (owner not in self._active_squad or (tick is None and not self.require_official_clock) or not skill
                or _integer(inst) in (None, 0) or seq is None
                or _canonical(fields.get("owner", "")) != self._active_squad[owner]
                or fields.get("skillSource") in {"Summon", "Buff"}):
            return
        cast = (inst, skill, seq if self.require_official_clock else tick)
        sequence = (owner, seq)
        if sequence in self._seen_sequences:
            if self._seen_sequences[sequence] != cast:
                self._clock_conflict = True
            return
        self._seen_sequences[sequence] = cast
        if cast in self._seen_casts:
            return
        if len(self._seen_casts) >= 50_000:
            self._clock_conflict = True
            return
        self._seen_casts.add(cast)
        index = next((i for i, value in enumerate(self._completed) if value is None), None)
        if index is None:
            return
        step = self._active["steps"][index]
        action = self._battle_actions.get((step["characterId"], step.get("actionKind")))
        matches = skill in action["rootSkillIds"] if action is not None else step.get("skillId") == skill
        if step["characterId"] == self._active_squad[owner] and matches:
            # The latest official progress observation is not the exact time
            # of a separate cast packet. Its completion time remains unknown.
            capture_ms = _integer(fields.get("captureMs"))
            completed_at = (self._clock.elapsed(capture_ms) if capture_ms is not None else None) if self.require_official_clock else self._elapsed()
            self._completed[index] = {"atMs": completed_at}

    def snapshot(self, now_ms: int | float | None = None) -> dict[str, Any] | None:
        if (not self._available or self._active is None or self._challenge is None or self._clock_conflict
                or self._squad != self._active_squad or self._dungeon_id != self._active["dungeonId"]
                or self._freeze_state_invalid):
            self._last_confirmed_snapshot = None
            return None
        if self._paused is True:
            if self.require_official_clock and self._clock.spec is not None:
                status = self._clock.diagnostics()
                if (not status["ready"] or status["challengeInvalid"] or status["freezeStateInvalid"]
                        or not status["challengeCounterKnown"]):
                    self._last_confirmed_snapshot = None
                    return None
            # A freeze response proves the state, but carries no official pause
            # epoch. Preserve only the last validated display; never estimate
            # the timer at response arrival or advance it while frozen.
            if self._last_confirmed_snapshot is None:
                return None
            frozen = deepcopy(self._last_confirmed_snapshot)
            frozen.update(paused=True, pauseDisplaySource="last_confirmed_snapshot")
            return frozen
        elapsed = self._elapsed(now_ms)
        if elapsed is None:
            self._last_confirmed_snapshot = None
            return None
        steps = []
        for index, step in enumerate(self._active["steps"]):
            completion = self._completed[index]
            action = self._battle_actions.get((step["characterId"], step.get("actionKind")))
            steps.append({**step, "characterName": self._battle_character_names.get(step["characterId"], "未识别角色"),
                          "actionName": action["name"] if action else _ACTION_NAMES.get(step.get("actionKind"), "旧步骤"),
                          "skillName": action["skillName"] if action else self._battle_skill_names.get((step["characterId"], step.get("skillId")), "未识别技能"),
                          "iconUrl": action["iconUrl"] if action else "",
                          "completed": completion is not None,
                          "completedAtMs": completion["atMs"] if completion is not None else None})
        pending = next((step for step in steps if not step["completed"]), None)
        result = {"planId": self._active["id"], "name": self._active["name"],
                "dungeonId": self._active["dungeonId"], "characterIds": list(self._active["characterIds"]),
                "elapsedMs": elapsed, "paused": False,
                "clockSource": ("official_algorithm_observer" if self._clock.spec else "lab_verified_progress") if self.require_official_clock else "packet_battle_tick",
                "clockLabel": "官方算法计时" if self.require_official_clock else "网络包战斗时钟",
                "clockProvenance": self._clock.diagnostics()["provenance"] if self.require_official_clock else None,
                "challengeStartTs": self._challenge[1], "nextStep": pending,
                "steps": steps, "displayNotes": self._active["displayNotes"],
                "completedCount": sum(step["completed"] for step in steps), "allCompleted": pending is None,
                "planRevision": self._active["revision"]}
        self._last_confirmed_snapshot = deepcopy(result)
        return result
