from copy import deepcopy

import pytest

from parser_core.rotation_guide import RotationGuideEngine


CHARACTERS = ["chr_0016_laevat", "chr_0033_camille", "chr_0032_lizhiyan", "chr_0035_liino"]
ACTORS = ["102140", "102171", "102201", "102231"]
GAME = "indie_hard013_s"
START = 1791811352908


def plan(identity="plan-1", *, position=0, enabled=True, dungeon=GAME):
    return {"id": identity, "name": "测试轴", "dungeonId": dungeon,
            "characterIds": CHARACTERS.copy(), "position": position, "enabled": enabled,
            "revision": 1, "updatedAt": "2026-10-14T00:00:00Z", "displayNotes": "装备只展示",
            "steps": [{"id": "step-1", "atMs": 2000, "characterId": CHARACTERS[3],
                       "skillId": "chr_0035_liino_normal_skill", "note": "战技", "phase": "开场"},
                      {"id": "step-2", "atMs": 4000, "characterId": CHARACTERS[3],
                       "skillId": "chr_0035_liino_normal_skill", "note": "再释放", "phase": ""}]}


def envelope(plans=None, account="user-1"):
    return {"schemaVersion": 1, "accountId": account, "revision": "a", "rotations": plans or [plan()]}


def feed(engine, *lines):
    for line in lines:
        engine.feed_line("[21:22:34.800] " + line)


def context(engine, *, dungeon=GAME, reverse=False):
    members = [f"{char}_{actor}" for char, actor in zip(CHARACTERS, ACTORS)]
    if reverse:
        members.reverse()
    feed(engine, f"DUNGEON_CONTEXT dungeonId={dungeon} source=SC_SELF_SCENE_INFO sceneId=scene-1",
         f"SQUAD size=4 members=[{' '.join(members)}]")


def start(engine, *, start_ts=START):
    feed(engine, f"OFFICIAL_TIMER_START source=SC_GAME_MECHANICS_SYNC_CHALLENGE_START gameId={GAME} challengeStartTs={start_ts}",
         "GAME_TIMER_START seq=29242 source=PacketBattleState startMs=25266 official=1")


def packet_engine(plans=None):
    engine = RotationGuideEngine(require_official_clock=False)
    assert engine.replace_plans(envelope(plans))
    context(engine)
    start(engine)
    return engine


def cast(*, seq=29246, tick=26744, inst="102232", owner=CHARACTERS[3], actor=ACTORS[3],
         skill="chr_0035_liino_normal_skill", source="unknown"):
    return (f"SKILL_CAST_START seq={seq} startMs={tick} inst={inst} owner={owner} "
            f"ownerId={actor} skill={skill} skillSource={source}")


def test_lab_progress_is_explicit_not_receipt_or_client_clock():
    engine = RotationGuideEngine(allow_lab_progress=True)
    engine.replace_plans(envelope())
    context(engine)
    start(engine)
    feed(engine, cast(), "GAME_CLOCK_TICK source=CS_BATTLE_OP tickMs=30000")
    assert engine.snapshot(now_ms=10**12) is None
    feed(engine, f"OFFICIAL_TIMER_PROGRESS source=VERIFIED_OFFICIAL_CHALLENGE_CLOCK gameId={GAME} challengeStartTs={START} elapsedMs=1200")
    assert engine.snapshot()["elapsedMs"] == 1200
    assert engine.snapshot()["clockSource"] == "lab_verified_progress"
    assert not engine.snapshot()["steps"][0]["completed"]
    feed(engine, cast(seq=30001, inst="102233", tick=31000))
    assert engine.snapshot()["steps"][0]["completed"]
    assert engine.snapshot()["steps"][0]["completedAtMs"] is None


@pytest.mark.parametrize("suffix", [
    f"source=SC_PING gameId={GAME} challengeStartTs={START} elapsedMs=2000",
    f"source=VERIFIED_OFFICIAL_CHALLENGE_CLOCK gameId=other challengeStartTs={START} elapsedMs=2000",
    f"source=VERIFIED_OFFICIAL_CHALLENGE_CLOCK gameId={GAME} challengeStartTs={START + 1} elapsedMs=2000",
    f"source=VERIFIED_OFFICIAL_CHALLENGE_CLOCK gameId={GAME} challengeStartTs={START} elapsedMs=-1",
])
def test_official_progress_requires_exact_identity_and_verified_source(suffix):
    engine = RotationGuideEngine()
    engine.replace_plans(envelope())
    context(engine)
    start(engine)
    feed(engine, "OFFICIAL_TIMER_PROGRESS " + suffix)
    assert engine.snapshot() is None


def test_exact_map_four_role_match_and_explicit_enabled_position():
    later = plan("later", position=10)
    disabled = plan("disabled", position=0, enabled=False)
    first = plan("first", position=2)
    engine = packet_engine([later, disabled, first])
    assert engine.snapshot()["planId"] == "first"
    assert engine.snapshot()["elapsedMs"] == 0
    assert engine.snapshot()["clockLabel"] == "网络包战斗时钟"


def test_squad_order_is_irrelevant():
    engine = RotationGuideEngine(require_official_clock=False)
    engine.replace_plans(envelope())
    context(engine, reverse=True)
    start(engine)
    assert engine.snapshot() is not None


@pytest.mark.parametrize("setup", ["missing_squad", "missing_map", "wrong_map", "three_members", "missing_official_start", "wrong_timer_source"])
def test_unknown_or_mismatched_required_evidence_hides(setup):
    engine = RotationGuideEngine(require_official_clock=False)
    engine.replace_plans(envelope())
    if setup != "missing_map":
        feed(engine, f"DUNGEON_CONTEXT dungeonId={'other' if setup == 'wrong_map' else GAME} sceneId=scene-1")
    if setup != "missing_squad":
        count = 3 if setup == "three_members" else 4
        members = " ".join(f"{c}_{a}" for c, a in zip(CHARACTERS[:count], ACTORS[:count]))
        feed(engine, f"SQUAD size={count} members=[{members}]")
    if setup != "missing_official_start":
        feed(engine, f"OFFICIAL_TIMER_START source=SC_GAME_MECHANICS_SYNC_CHALLENGE_START gameId={GAME} challengeStartTs={START}")
    feed(engine, f"GAME_TIMER_START source={'other' if setup == 'wrong_timer_source' else 'PacketBattleState'} startMs=25266 official=1")
    assert engine.snapshot() is None


def test_completion_matches_only_own_actor_and_exact_skill_next_in_authored_order():
    p = plan()
    p["steps"][1]["skillId"] = "chr_0035_liino_ultimate_skill"
    engine = packet_engine([p])
    feed(engine, cast(skill="chr_0035_liino_ultimate_skill"))
    assert engine.snapshot()["completedCount"] == 0
    feed(engine, cast(seq=29247, tick=26800, actor="2305843009213702194", inst="2305843009213702196", source="Summon"))
    assert engine.snapshot()["completedCount"] == 0
    feed(engine, cast(seq=29248, tick=27000, owner=CHARACTERS[0]))
    assert engine.snapshot()["completedCount"] == 0
    feed(engine, cast(seq=29249, tick=27200, skill="chr_0035_liino_normal_skill_soundwave_projhit"))
    assert engine.snapshot()["completedCount"] == 0
    feed(engine, cast(seq=29250, tick=27400))
    assert engine.snapshot()["steps"][0]["completedAtMs"] == 2134
    assert engine.snapshot()["nextStep"]["id"] == "step-2"
    feed(engine, cast(seq=29251, tick=28000, skill="chr_0035_liino_ultimate_skill"))
    assert engine.snapshot()["allCompleted"]


def test_repeated_cast_identity_or_sequence_never_completes_twice():
    engine = packet_engine()
    feed(engine, cast(), cast(), cast(seq=29247))
    assert engine.snapshot()["completedCount"] == 1
    feed(engine, cast(seq=29248, tick=30000))
    assert engine.snapshot()["completedCount"] == 2


def test_conflicting_sequence_or_clock_regression_hides():
    engine = packet_engine()
    feed(engine, cast(), cast(inst="102999"))
    assert engine.snapshot() is None
    engine = packet_engine()
    feed(engine, "GAME_CLOCK_TICK source=CS_BATTLE_OP tickMs=27000", "GAME_CLOCK_TICK source=CS_BATTLE_OP tickMs=26999")
    assert engine.snapshot() is None


def test_observed_clock_never_advances_from_wall_time_or_untrusted_tick():
    engine = packet_engine()
    feed(engine, "GAME_CLOCK_TICK source=CS_BATTLE_OP tickMs=28000")
    assert engine.snapshot(now_ms=10**12)["elapsedMs"] == 2734
    feed(engine, "GAME_CLOCK_TICK source=SC_PING tickMs=29000")
    assert engine.snapshot()["elapsedMs"] == 2734


def test_official_freeze_response_hides_until_explicit_release():
    engine = packet_engine()
    feed(engine, "GAME_TIME_FREEZE_STATE source=SC_GAME_TIME_FREEZE_START_RSP inTimeFreeze=1", cast())
    assert engine.snapshot() is None
    feed(engine, "GAME_TIME_FREEZE_STATE source=SC_GAME_TIME_FREEZE_END_RSP inTimeFreeze=0")
    assert engine.snapshot()["completedCount"] == 0


@pytest.mark.parametrize("boundary", [
    "GAME_TIMER_RESET source=CS_ENTER_DUNGEON",
    f"GAME_INST_RESTART source=SC_GAME_MECHANICS_SYNC_RESTART_GAME_INST gameId={GAME}",
    f"GAME_INST_LEAVE source=SC_GAME_MECHANICS_SYNC_LEAVE_GAME_INST gameId={GAME}",
    f"OFFICIAL_TIMER_END source=SC_GAME_MECHANICS_SYNC_CHALLENGE_COMPLETE gameId={GAME} passTime=87585",
    "DUNGEON_CONTEXT dungeonId=other sceneId=scene-2",
    f"DUNGEON_CONTEXT dungeonId={GAME} sceneId=scene-2",
    "SQUAD size=0 members=[]",
    "ACTOR_MAP id=102231 template=chr_0035_liino source=launch_projectile",
])
def test_boundaries_clear_active_guide(boundary):
    engine = packet_engine()
    feed(engine, cast(), boundary)
    assert engine.snapshot() is None


def test_phase_end_and_other_game_completion_do_not_end_official_challenge():
    engine = packet_engine()
    feed(engine, "GAME_TIMER_END source=PacketBattleState startMs=25266 endMs=30000 packetElapsedMs=4734",
         "OFFICIAL_TIMER_END source=SC_GAME_MECHANICS_SYNC_CHALLENGE_COMPLETE gameId=other passTime=2000")
    assert engine.snapshot() is not None


def test_duplicate_official_start_preserves_completion_new_identity_resets():
    engine = packet_engine()
    feed(engine, cast())
    start(engine)
    assert engine.snapshot()["completedCount"] == 1
    start(engine, start_ts=START + 10000)
    assert engine.snapshot()["completedCount"] == 0


def test_same_account_library_update_is_latched_for_next_battle():
    engine = packet_engine()
    changed = plan("new")
    engine.replace_plans(envelope([changed]))
    assert engine.snapshot()["planId"] == "plan-1"
    start(engine, start_ts=START + 1)
    assert engine.snapshot()["planId"] == "new"


def test_sync_mid_battle_does_not_activate_newly_downloaded_plan():
    engine = RotationGuideEngine(require_official_clock=False)
    engine.replace_plans({**envelope(), "rotations": []})
    context(engine)
    start(engine)
    engine.replace_plans(envelope())
    feed(engine, "GAME_CLOCK_TICK source=CS_BATTLE_OP tickMs=28000")
    assert engine.snapshot() is None
    start(engine, start_ts=START + 1)
    assert engine.snapshot() is not None


def test_account_change_and_capture_gap_never_reuse_old_battle():
    engine = packet_engine()
    engine.replace_plans(envelope(account="user-2"))
    assert engine.snapshot() is None
    start(engine, start_ts=START + 1)
    assert engine.snapshot() is not None
    engine.set_available(False)
    feed(engine, cast())
    engine.set_available(True)
    feed(engine, "GAME_CLOCK_TICK source=CS_BATTLE_OP tickMs=30000")
    assert engine.snapshot() is None
    context(engine)
    start(engine, start_ts=START + 2)
    assert engine.snapshot()["completedCount"] == 0


def test_exact_admin_alias_and_catalog_labels_do_not_change_skill_identity():
    p = plan()
    p["characterIds"][3] = "chr_9000_endmin"
    for step in p["steps"]:
        step["characterId"] = "chr_0003_endminf"
        step["skillId"] = "chr_0003_endminf_normal_skill"
    engine = RotationGuideEngine(require_official_clock=False, catalog={"schemaVersion": 1, "characters": [
        {"id": "chr_9000_endmin", "name": "管理员", "skills": [{"id": "chr_0003_endminf_normal_skill", "name": "战技"}]}]})
    engine.replace_plans(envelope([p]))
    feed(engine, f"DUNGEON_CONTEXT dungeonId={GAME}", "SQUAD size=4 members=[chr_0016_laevat_102140 chr_0033_camille_102171 chr_0032_lizhiyan_102201 chr_0003_endminf_102231]")
    start(engine)
    feed(engine, cast(owner="chr_0003_endminf", skill="chr_0002_endminm_normal_skill"))
    assert engine.snapshot()["completedCount"] == 0
    feed(engine, cast(seq=29247, tick=27000, owner="chr_0003_endminf", skill="chr_0003_endminf_normal_skill"))
    assert engine.snapshot()["steps"][0]["characterName"] == "管理员"
    assert engine.snapshot()["steps"][0]["skillName"] == "战技"
    assert engine.snapshot()["completedCount"] == 1


@pytest.mark.parametrize("mutate", [
    lambda e: e.update(schemaVersion=True),
    lambda e: e["rotations"][0].update(enabled="true"),
    lambda e: e["rotations"][0]["steps"][0].update(atMs=True),
    lambda e: e["rotations"][0]["steps"][1].update(atMs=0),
    lambda e: e["rotations"][0]["steps"][1].update(id="step-1"),
    lambda e: e["rotations"][0].update(characterIds=[CHARACTERS[0]] * 4),
    lambda e: e["rotations"][0].update(steps=[]),
    lambda e: e["rotations"].append(deepcopy(e["rotations"][0])),
])
def test_invalid_cache_fails_closed(mutate):
    engine = packet_engine()
    raw = envelope()
    mutate(raw)
    assert not engine.replace_plans(raw)
    assert engine.snapshot() is None


def test_snapshot_is_detached_from_internal_plan():
    engine = packet_engine()
    snapshot = engine.snapshot()
    snapshot["steps"][0]["skillId"] = "changed"
    snapshot["characterIds"].clear()
    feed(engine, cast())
    assert engine.snapshot()["completedCount"] == 1
    assert len(engine.snapshot()["characterIds"]) == 4


def action_catalog():
    roots = ["chr_0035_liino_normal_skill", "chr_0035_liino_normal_skill_ult",
             "chr_0035_liino_attack1_ult", "chr_0035_liino_ultimate_skill"]
    return {"schemaVersion": 2, "maps": [], "characters": [{
        "id": CHARACTERS[3], "name": "梨诺", "skills": [{"id": root, "name": "官方技能"} for root in roots],
        "actions": [{"kind": "battle_skill", "name": "战技", "skillName": "闪耀焦点", "iconUrl": "/battle.png", "rootSkillIds": roots[:2]},
                    {"kind": "ultimate_skill", "name": "终结技", "skillName": "晨星的协奏曲", "iconUrl": "/ultimate.png", "rootSkillIds": roots[3:]}],
    }]}


def action_plan(kind="battle_skill"):
    value = plan()
    for step in value["steps"]:
        del step["skillId"]
        step["actionKind"] = kind
    return value


def action_engine(catalog=None, kind="battle_skill"):
    engine = RotationGuideEngine(require_official_clock=False, catalog=catalog)
    assert engine.replace_plans({**envelope([action_plan(kind)]), "schemaVersion": 2})
    context(engine)
    start(engine)
    return engine


def test_player_battle_action_accepts_only_explicit_normal_and_empowered_roots():
    engine = action_engine(action_catalog())
    feed(engine, cast(skill="chr_0035_liino_attack1_ult"))
    assert engine.snapshot()["completedCount"] == 0
    feed(engine, cast(seq=29247, tick=27000, skill="chr_0035_liino_normal_skill"))
    assert engine.snapshot()["completedCount"] == 1
    feed(engine, cast(seq=29248, tick=28000, skill="chr_0035_liino_normal_skill_ult"))
    report = engine.snapshot()
    assert report["completedCount"] == 2
    assert report["steps"][0]["actionName"] == "战技"
    assert report["steps"][0]["skillName"] == "闪耀焦点"
    assert report["steps"][0]["iconUrl"] == "/battle.png"
    assert "skillId" not in report["steps"][0]


def test_ultimate_action_does_not_accept_empowered_attacks_or_battle_skill():
    engine = action_engine(action_catalog(), "ultimate_skill")
    for index, root in enumerate(["chr_0035_liino_attack1_ult", "chr_0035_liino_normal_skill_ult", "chr_0035_liino_normal_skill"]):
        feed(engine, cast(seq=29246 + index, tick=27000 + index, skill=root))
    assert engine.snapshot()["completedCount"] == 0
    feed(engine, cast(seq=29250, tick=28000, skill="chr_0035_liino_ultimate_skill"))
    assert engine.snapshot()["completedCount"] == 1


def test_player_action_still_requires_own_character_and_unique_cast():
    engine = action_engine(action_catalog())
    feed(engine, cast(actor="9999"), cast(seq=29247, owner=CHARACTERS[2], actor=ACTORS[2]), cast(seq=29248, source="Summon"))
    assert engine.snapshot()["completedCount"] == 0
    event = cast(seq=29249, tick=28000)
    feed(engine, event, event)
    assert engine.snapshot()["completedCount"] == 1


def test_legacy_exact_step_under_v2_still_requires_its_single_selected_root():
    engine = RotationGuideEngine(require_official_clock=False, catalog=action_catalog())
    assert engine.replace_plans({**envelope(), "schemaVersion": 2})
    context(engine)
    start(engine)
    feed(engine, cast(skill="chr_0035_liino_normal_skill_ult"))
    assert engine.snapshot()["completedCount"] == 0
    feed(engine, cast(seq=29247, tick=27000))
    assert engine.snapshot()["completedCount"] == 1


@pytest.mark.parametrize("catalog", [None, {"schemaVersion": 1, "characters": []}, {"schemaVersion": 2, "characters": []}])
def test_missing_action_catalog_never_guesses_a_root_group(catalog):
    engine = action_engine(catalog)
    feed(engine, cast(), cast(seq=29247, tick=27000, skill="chr_0035_liino_normal_skill_ult"))
    report = engine.snapshot()
    assert report["completedCount"] == 0
    assert report["steps"][0]["characterName"] == "未识别角色"
    assert report["steps"][0]["actionName"] == "战技"
    assert report["steps"][0]["skillName"] == "未识别技能"


@pytest.mark.parametrize("mutate", [
    lambda e: e.update(schemaVersion=1),
    lambda e: e["rotations"][0]["steps"][0].update(actionKind="heavy_attack"),
    lambda e: e["rotations"][0]["steps"][0].update(actionKind=[]),
    lambda e: e["rotations"][0]["steps"][0].update(actionKind=None),
    lambda e: e["rotations"][0]["steps"][0].update(skillId="exact-root"),
    lambda e: e["rotations"][0]["steps"][0].update(skillId=""),
    lambda e: e["rotations"][0]["steps"][0].pop("actionKind"),
])
def test_invalid_player_action_steps_are_rejected(mutate):
    value = {**envelope([action_plan()]), "schemaVersion": 2}
    mutate(value)
    engine = RotationGuideEngine(require_official_clock=False)
    assert not engine.replace_plans(value)
    assert engine.snapshot() is None


@pytest.mark.parametrize("mutate", [
    lambda a: a["actions"][0].update(rootSkillIds=[]),
    lambda a: a["actions"][0].update(rootSkillIds=["not-an-official-root"]),
    lambda a: a["actions"][0].update(rootSkillIds=["chr_0035_liino_normal_skill"] * 2),
    lambda a: a["actions"][0].update(rootSkillIds=[{}]),
    lambda a: a["actions"][0].update(kind="unknown"),
    lambda a: a["actions"][0].update(name=""),
    lambda a: a["actions"].append(deepcopy(a["actions"][0])),
])
def test_invalid_catalog_actions_never_register_matching_roots(mutate):
    catalog = action_catalog()
    mutate(catalog["characters"][0])
    engine = action_engine(catalog)
    feed(engine, cast())
    assert engine.snapshot()["completedCount"] == 0


def test_catalog_and_its_caller_owned_data_are_latched_at_challenge_start():
    catalog = action_catalog()
    engine = action_engine(catalog)
    catalog["characters"][0]["actions"][0]["rootSkillIds"].clear()
    changed = action_catalog()
    changed["characters"][0]["actions"][0].update(rootSkillIds=["chr_0035_liino_attack1_ult"], skillName="改名后技能")
    engine.replace_catalog(changed)
    feed(engine, cast(skill="chr_0035_liino_attack1_ult"))
    assert engine.snapshot()["completedCount"] == 0
    feed(engine, cast(seq=29247, tick=27000))
    assert engine.snapshot()["completedCount"] == 1
    assert engine.snapshot()["steps"][0]["skillName"] == "闪耀焦点"
    start(engine, start_ts=START + 1)
    feed(engine, cast(seq=29248, tick=28000, skill="chr_0035_liino_attack1_ult"))
    assert engine.snapshot()["completedCount"] == 1
    assert engine.snapshot()["steps"][0]["skillName"] == "改名后技能"


def test_catalog_update_after_start_before_clock_available_is_for_next_battle_only():
    engine = RotationGuideEngine(require_official_clock=False, catalog=action_catalog())
    engine.replace_plans({**envelope([action_plan()]), "schemaVersion": 2})
    context(engine)
    feed(engine, f"OFFICIAL_TIMER_START source=SC_GAME_MECHANICS_SYNC_CHALLENGE_START gameId={GAME} challengeStartTs={START}")
    assert engine.snapshot() is None
    engine.replace_catalog(None)
    feed(engine, "GAME_TIMER_START source=PacketBattleState startMs=25266 official=1", cast())
    assert engine.snapshot()["completedCount"] == 1


def test_legacy_unknown_skill_display_never_falls_back_to_internal_root_code():
    report = packet_engine().snapshot()
    assert report["steps"][0]["characterName"] == "未识别角色"
    assert report["steps"][0]["actionName"] == "旧步骤"
    assert report["steps"][0]["skillName"] == "未识别技能"
