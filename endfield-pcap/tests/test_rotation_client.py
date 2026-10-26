from __future__ import annotations

import copy
import hashlib
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from endfield_pcap.rotation_client import (
    OverlaySettingsStore, RotationCache, RotationGuideController, validate_rotation_envelope,
)
from uploader_core.api_client import ApiClientError
from parser_core.rotation_guide import RotationGuideEngine

@pytest.fixture(autouse=True)
def enable_retained_guide_logic_for_unit_tests(monkeypatch):
    # Exercise dormant cache/isolation code without changing the product gate.
    monkeypatch.setattr("endfield_pcap.rotation_client.ROTATION_GUIDANCE_ENABLED", True)


def envelope(owner="account-a"):
    characters = [f"chr_{i:04d}_test" for i in range(4)]
    return {"schemaVersion": 1, "accountId": owner, "revision": "library-a", "rotations": [{
        "id": "plan-a", "name": "测试轴", "dungeonId": "dungeon-a", "characterIds": characters,
        "enabled": True, "position": 0, "revision": 1, "updatedAt": "2026-10-14T00:00:00Z",
        "displayNotes": "5+6", "steps": [{"id": "step-a", "atMs": 0, "characterId": characters[0],
        "skillId": "exact-root", "note": "释放", "phase": "开场"}],
    }]}


class Sessions:
    def __init__(self, owner="account-a"):
        self.value = {"userId": owner, "sessionToken": "private-token"}

    def load_session(self):
        return copy.deepcopy(self.value)


class Settings:
    def load_settings(self):
        return SimpleNamespace(api_base_url="https://example.test", web_base_url="https://example.test")


class Client:
    def __init__(self, result):
        self.result = result
        self.client = SimpleNamespace(close=lambda: None)
        self.token = None

    def set_session_token(self, token):
        self.token = token

    def get_rotations(self):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def controller(tmp_path, result, *, asset_root=None):
    sessions = Sessions()
    client = Client(result)
    value = RotationGuideController(tmp_path / "trace.log", cache=RotationCache(tmp_path / "cache.json"),
        session_store=sessions, settings_store=Settings(), client_factory=lambda _: client, asset_root=asset_root)
    return value, sessions, client


def test_cache_binds_account_and_api_and_removes_credentials(tmp_path):
    cache = RotationCache(tmp_path / "cache.json")
    data = envelope()
    data.update(sessionToken="should-not-save", email="developer@example.com")
    data["rotations"][0]["token"] = "also-private"
    cache.save(data, "https://example.test/")
    assert cache.load("account-a", "https://example.test") == envelope()
    assert cache.load("account-b", "https://example.test") is None
    assert cache.load("account-a", "https://other.test") is None
    raw = cache.path.read_text(encoding="utf-8")
    assert "private" not in raw and "should-not-save" not in raw


def test_v2_player_action_cache_preserves_version_and_omits_legacy_root(tmp_path):
    data = envelope()
    data["schemaVersion"] = 2
    step = data["rotations"][0]["steps"][0]
    del step["skillId"]
    step["actionKind"] = "battle_skill"
    cache = RotationCache(tmp_path / "cache.json")
    cache.save(data, "https://example.test")
    assert cache.load("account-a", "https://example.test") == data
    assert "skillId" not in json.loads(cache.path.read_text(encoding="utf-8"))["rotations"][0]["steps"][0]


@pytest.mark.parametrize("version,fields", [
    (1, {"actionKind": "battle_skill"}), (2, {}), (2, {"actionKind": "heavy_attack"}),
    (2, {"actionKind": []}), (2, {"actionKind": None}), (2, {"actionKind": ""}),
    (2, {"actionKind": "battle_skill", "skillId": "root"}),
    (2, {"actionKind": "battle_skill", "skillId": ""}),
    (2, {"actionKind": "battle_skill", "skillId": None}),
])
def test_invalid_action_wire_format_is_rejected_before_caching(version, fields):
    data = envelope()
    data["schemaVersion"] = version
    step = data["rotations"][0]["steps"][0]
    del step["skillId"]
    step.update(fields)
    with pytest.raises(ValueError):
        validate_rotation_envelope(data, "account-a")


def test_v2_catalog_sync_is_saved_for_offline_actions(tmp_path):
    value, _, client = controller(tmp_path, envelope())
    catalog = {"schemaVersion": 2, "characters": [], "maps": []}
    client.get_rotation_catalog = lambda: catalog
    context = value._reconcile_session(force=True)
    value._sync_once(context)
    assert value._catalog == catalog
    assert json.loads(value._catalog_path.read_text(encoding="utf-8"))["catalog"] == catalog
    other, _, _ = controller(tmp_path, ApiClientError("offline"))
    other._reconcile_session(force=True)
    assert other._catalog == catalog


@pytest.mark.parametrize("mutate", [
    lambda p: p.update(schemaVersion=True),
    lambda p: p.update(accountId="other"),
    lambda p: p["rotations"][0].update(enabled=1),
    lambda p: p["rotations"][0].update(position=True),
    lambda p: p["rotations"][0].update(revision=0),
    lambda p: p["rotations"][0].update(characterIds=["same"] * 4),
    lambda p: p["rotations"][0]["steps"][0].update(atMs=1.5),
    lambda p: p["rotations"][0]["steps"][0].update(characterId="outside"),
    lambda p: p["rotations"].append(copy.deepcopy(p["rotations"][0])),
])
def test_invalid_envelope_never_reaches_cache(mutate):
    data = envelope()
    mutate(data)
    with pytest.raises(ValueError):
        validate_rotation_envelope(data, "account-a")


def test_same_account_network_failure_keeps_cache(tmp_path):
    value, _, _ = controller(tmp_path, ApiClientError("offline"))
    value.cache.save(envelope(), "https://example.test")
    context = value._reconcile_session(force=True)
    value._sync_once(context)
    assert value._envelope == envelope()
    assert value.sync_state()["offline"] is True
    assert value.sync_state()["planCount"] == 1


def test_retirement_empty_sync_stops_future_plans_but_does_not_clear_old_active_battle(tmp_path):
    empty = {"schemaVersion": 2, "accountId": "account-a", "revision": "rotations-disabled-v1", "rotations": []}
    value, _, _ = controller(tmp_path, empty)
    value.cache.save(envelope(), "https://example.test")
    context = value._reconcile_session(force=True)
    active = copy.deepcopy(value._engine._plans[0])
    value._engine._active = active
    value._engine._battle_plans = [copy.deepcopy(active)]
    value._sync_once(context)
    assert value._envelope == empty and value.sync_state()["planCount"] == 0
    assert value.cache.load("account-a", "https://example.test") == empty
    assert value._engine._plans == []
    # The published engine deliberately latches its same-account battle.
    # An empty sync alone cannot guarantee the already shown strip disappears.
    assert value._engine._active == active and value._engine._battle_plans == [active]


def test_logout_immediately_discards_cache_even_with_request_in_flight(tmp_path):
    value, sessions, client = controller(tmp_path, envelope())
    value.cache.save(envelope(), "https://example.test")
    context = value._reconcile_session(force=True)
    entered = threading.Event()
    resume = threading.Event()

    def pending_response():
        entered.set()
        assert resume.wait(2)
        return envelope()

    client.get_rotations = pending_response
    worker = threading.Thread(target=value._sync_once, args=(context,))
    worker.start()
    assert entered.wait(1)
    sessions.value = None
    value._reconcile_session(force=True)
    assert value._envelope is None and not value.cache.path.exists()
    resume.set()
    worker.join(2)
    assert not worker.is_alive()
    assert value._envelope is None and not value.cache.path.exists()


def test_switching_account_cannot_install_older_response(tmp_path):
    value, sessions, client = controller(tmp_path, envelope())
    context = value._reconcile_session(force=True)
    sessions.value = {"userId": "account-b", "sessionToken": "new-token"}
    value._reconcile_session(force=True)
    value._sync_once(context)
    assert value._identity[0] == "account-b" and value._envelope is None
    assert not value.cache.path.exists()


def test_auth_rejection_disables_cache(tmp_path):
    value, _, _ = controller(tmp_path, ApiClientError("expired", status_code=401))
    value.cache.save(envelope(), "https://example.test")
    context = value._reconcile_session(force=True)
    value._sync_once(context)
    assert value._envelope is None
    assert not value.cache.path.exists()
    assert "登录已失效" in value.sync_state()["message"]


def test_other_account_payload_cannot_replace_valid_cache(tmp_path):
    value, _, _ = controller(tmp_path, envelope("other-account"))
    value.cache.save(envelope(), "https://example.test")
    context = value._reconcile_session(force=True)
    value._sync_once(context)
    assert value._envelope == envelope()
    assert value.cache.load("account-a", "https://example.test") == envelope()


def test_atomic_write_failure_keeps_previous_valid_cache(tmp_path, monkeypatch):
    value = RotationCache(tmp_path / "cache.json")
    value.save(envelope(), "https://example.test")
    changed = envelope()
    changed["revision"] = "new-library"
    monkeypatch.setattr("endfield_pcap.rotation_client.os.replace", lambda *args: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(OSError):
        value.save(changed, "https://example.test")
    assert value.load("account-a", "https://example.test") == envelope()
    assert not tmp_path.joinpath("cache.json.tmp").exists()


def test_settings_reject_bad_values_and_round_trip_positions(tmp_path):
    store = OverlaySettingsStore(tmp_path / "settings.json")
    store.path.write_text(json.dumps({"opacity": "opaque", "meterEnabled": 1,
        "positions": {"guide": [-1, 100]}}), encoding="utf-8")
    assert store.load() == {"meterEnabled": True, "guideEnabled": True, "opacity": 0.90, "positions": {},
                            "guideScale": 1.0, "guideAnchor": [0.5, 0.13], "guidePositionMode": "game"}
    settings = {"meterEnabled": False, "guideEnabled": True, "opacity": 0.8, "positions": {"guide": [50, 250], "meter": [900, 80]}}
    store.save(settings)
    assert store.load() == {**settings, "guideScale": 1.0, "guideAnchor": [0.5, 0.13], "guidePositionMode": "absolute"}


def test_module_opacities_are_independent_and_legacy_settings_remain_unchanged(tmp_path):
    store = OverlaySettingsStore(tmp_path / "settings.json")
    store.save({"opacity": .8, "meterOpacity": .5, "guideOpacity": .95})
    settings = store.load()
    assert settings["opacity"] == .8
    assert settings["meterOpacity"] == .5 and settings["guideOpacity"] == .95
    store.save({"opacity": .8, "meterOpacity": float("nan"), "guideOpacity": True})
    settings = store.load()
    assert "meterOpacity" not in settings and "guideOpacity" not in settings


@pytest.mark.parametrize("scale,anchor", [
    (0.5, [0, 0]), (2, [1, 1]), (1.25, [0.4, 0.2]),
])
def test_relative_guide_settings_preserve_meter_opacity_and_absolute_fallback(tmp_path, scale, anchor):
    store = OverlaySettingsStore(tmp_path / "settings.json")
    settings = {"meterEnabled": False, "guideEnabled": True, "opacity": 0.75,
                "positions": {"meter": [900, 80], "guide": [50, 250]},
                "guideScale": scale, "guideAnchor": anchor, "guidePositionMode": "game"}
    store.save(settings)
    assert store.load() == settings
    settings["guidePositionMode"] = "absolute"
    store.save(settings)
    assert store.load() == settings


@pytest.mark.parametrize("scale", [True, "1.5", 0.49, 2.01, float("nan"), float("inf")])
def test_invalid_guide_scale_never_changes_the_other_overlay_settings(tmp_path, scale):
    store = OverlaySettingsStore(tmp_path / "settings.json")
    store.save({"guideScale": scale, "meterEnabled": False, "opacity": 0.8})
    value = store.load()
    assert value["guideScale"] == 1.0
    assert value["guideAnchor"] == [0.5, 0.13]
    assert value["guidePositionMode"] == "game"
    assert value["meterEnabled"] is False and value["opacity"] == 0.8


@pytest.mark.parametrize("anchor", [
    [True, 0.13], [0.5], [0.5, 0.13, 0], [-0.1, 0.13], [0.5, 1.1],
    [float("nan"), 0.13], [0.5, float("inf")], "center", None,
])
def test_invalid_relative_anchor_does_not_guess_a_ratio_from_old_screen_coordinates(tmp_path, anchor):
    store = OverlaySettingsStore(tmp_path / "settings.json")
    store.save({"guideAnchor": anchor, "positions": {"guide": [123, 456], "meter": [900, 80]}})
    value = store.load()
    assert value["guideAnchor"] == [0.5, 0.13]
    assert value["guidePositionMode"] == "game"
    assert value["positions"] == {"guide": [123, 456], "meter": [900, 80]}


def display_catalog():
    owner = envelope()["rotations"][0]["characterIds"][0]
    return {"schemaVersion": 2, "maps": [{"id": "dungeon-a", "name": "测试副本"}], "characters": [{
        "id": owner, "name": "测试角色", "avatarUrl": "/images/character/game-avatars/portrait.png",
        "skills": [{"id": "exact-root", "name": "旧精确技能", "groupId": "shared-display-group"},
                   {"id": "enhanced-attack", "name": "终结技中文名", "groupId": "shared-display-group"},
                   {"id": "ultimate-root", "name": "终结技中文名", "groupId": "shared-display-group"}],
        "actions": [{"kind": "battle_skill", "name": "战技", "skillName": "战技中文名",
                     "iconUrl": "/images/character/game-skills/battle.webp", "rootSkillIds": ["exact-root"]},
                    {"kind": "ultimate_skill", "name": "终结技", "skillName": "终结技中文名",
                     "iconUrl": "/images/character/game-skills/ultimate.webp", "rootSkillIds": ["ultimate-root"]}],
    }]}


def write_display_assets(root):
    # A bounded real PNG fixture; these tests check path/hash contracts, not GUI rendering.
    import base64
    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+y4wAAAABJRU5ErkJggg==")
    images = {}
    for url, relative in (("/images/character/game-skills/battle.webp", "skills/battle.png"),
                          ("/images/character/game-skills/ultimate.webp", "skills/ultimate.png"),
                          ("/images/character/game-avatars/portrait.png", "avatars/portrait.png")):
        path = root / "rotation-assets" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(png)
        images[url] = {"path": relative, "sha256": hashlib.sha256(png).hexdigest(),
                       "sourceSha256": "0" * 64}
    manifest = root / "rotation-assets.json"
    manifest.write_text(json.dumps({"schemaVersion": 1, "images": images}), encoding="utf-8")
    return images


def display_snapshot(value, monkeypatch, *, step=None):
    value._reconcile_session(force=True)
    value._catalog = display_catalog()
    original = copy.deepcopy(step or envelope()["rotations"][0]["steps"][0])
    original.update(characterName="测试角色", skillName="原步骤技能名", actionName="旧步骤",
                    iconUrl="", completed=False, completedAtMs=None)
    report = {"planId": "plan-a", "planRevision": 7, "name": "测试轴", "dungeonId": "dungeon-a",
              "elapsedMs": 12345, "clockSource": "official_algorithm_observer", "clockLabel": "官方算法计时",
              "steps": [original], "nextStep": original, "completedCount": 0, "allCompleted": False}
    monkeypatch.setattr(value._engine, "snapshot", lambda: copy.deepcopy(report))
    return report


@pytest.mark.parametrize("selector", [{"skillId": "exact-root"}, {"actionKind": "battle_skill"}])
def test_snapshot_uses_offline_exact_images_without_changing_selectors_or_clock(tmp_path, monkeypatch, selector):
    assets = tmp_path / "assets"
    write_display_assets(assets)
    value, _, _ = controller(tmp_path, ApiClientError("offline"), asset_root=assets)
    step = {**envelope()["rotations"][0]["steps"][0], **selector}
    if "actionKind" in selector:
        del step["skillId"]
    original = display_snapshot(value, monkeypatch, step=step)
    before = copy.deepcopy(original)
    shown = value.snapshot()
    current = shown["steps"][0]
    assert current["actionName"] == "战技" and current["skillName"] == "原步骤技能名"
    assert current["iconPath"] == str((assets / "rotation-assets/skills/battle.png").resolve())
    assert current["avatarPath"] == str((assets / "rotation-assets/avatars/portrait.png").resolve())
    assert current == shown["nextStep"]
    for field in ("id", "characterId", "skillId", "actionKind", "atMs", "phase", "note", "completed", "completedAtMs"):
        assert current.get(field) == before["steps"][0].get(field)
        assert (field in current) == (field in before["steps"][0])
    assert {key: shown[key] for key in before if key not in ("steps", "nextStep")} == {
        key: before[key] for key in before if key not in ("steps", "nextStep")}
    assert original == before
    # Presentation cannot turn an unavailable/unknown battle into a visible guide.
    monkeypatch.setattr(value._engine, "snapshot", lambda: None)
    assert value.snapshot() is None


@pytest.mark.parametrize("change", ["unknown", "same_name_group", "ambiguous", "mixed", "wrong_owner", "bad_catalog"])
def test_snapshot_never_guesses_a_legacy_icon_from_names_groups_or_first_action(tmp_path, monkeypatch, change):
    assets = tmp_path / "assets"
    write_display_assets(assets)
    value, _, _ = controller(tmp_path, ApiClientError("offline"), asset_root=assets)
    display_snapshot(value, monkeypatch)
    character = value._catalog["characters"][0]
    root = "missing-root" if change == "unknown" else "enhanced-attack" if change == "same_name_group" else "exact-root"
    if change == "ambiguous":
        character["actions"][1]["rootSkillIds"].append(root)
    elif change == "wrong_owner":
        character["id"] = "someone-else"
    elif change == "bad_catalog":
        value._catalog["schemaVersion"] = True
    step = {**envelope()["rotations"][0]["steps"][0], "skillId": root,
            "skillName": "终结技中文名", "completed": False}
    if change == "mixed":
        step["actionKind"] = "ultimate_skill"
    report = {"steps": [step], "nextStep": step}
    monkeypatch.setattr(value._engine, "snapshot", lambda: copy.deepcopy(report))
    for reverse in (False, True):
        if reverse:
            character["actions"].reverse()
        shown = value.snapshot()["steps"][0]
        assert shown["iconPath"] == shown["iconUrl"] == ""
        assert shown["skillId"] == root
        assert ("actionKind" in shown) is (change == "mixed")
    assert report["steps"][0] == step


def test_catalog_cache_and_bundled_images_work_together_offline(tmp_path, monkeypatch):
    assets = tmp_path / "assets"
    write_display_assets(assets)
    online, _, client = controller(tmp_path, envelope(), asset_root=assets)
    client.get_rotation_catalog = display_catalog
    online._sync_once(online._reconcile_session(force=True))
    offline, _, _ = controller(tmp_path, ApiClientError("offline"), asset_root=assets)
    offline._reconcile_session(force=True)
    assert offline._catalog == display_catalog()
    step = {**envelope()["rotations"][0]["steps"][0], "skillName": "旧缓存名称"}
    monkeypatch.setattr(offline._engine, "snapshot", lambda: {"steps": [step.copy()], "nextStep": step.copy()})
    offline._sync_once(offline._session_context())
    assert offline.sync_state()["offline"] is True
    shown = offline.snapshot()
    assert shown["steps"][0]["skillName"] == "旧缓存名称"
    assert shown["steps"][0]["iconPath"].endswith("battle.png")
    assert shown["steps"][0] == shown["nextStep"]


def test_legacy_icon_mapping_never_broadens_exact_cast_confirmation_to_action_variants(tmp_path):
    assets = tmp_path / "assets"
    write_display_assets(assets)
    value, _, _ = controller(tmp_path, envelope(), asset_root=assets)
    value._reconcile_session(force=True)
    catalog = display_catalog()
    catalog["characters"][0]["skills"].append({"id": "variant-root", "name": "明确战技变体"})
    catalog["characters"][0]["actions"][0]["rootSkillIds"].append("variant-root")
    value._catalog = catalog
    # An explicit packet-clock test engine isolates the unchanged cast-binding
    # contract; production retains its required official clock implementation.
    value._engine = RotationGuideEngine(require_official_clock=False, catalog=catalog)
    value._install(envelope(), "本地缓存", offline=True)
    value.set_available(True)
    characters = envelope()["rotations"][0]["characterIds"]
    actors = ["100", "101", "102", "103"]
    for line in ["DUNGEON_CONTEXT dungeonId=dungeon-a sceneId=fixture-scene",
                 "SQUAD size=4 members=[" + " ".join(f"{c}_{a}" for c, a in zip(characters, actors)) + "]",
                 "OFFICIAL_TIMER_START source=SC_GAME_MECHANICS_SYNC_CHALLENGE_START gameId=dungeon-a challengeStartTs=1000",
                 "GAME_TIMER_START source=PacketBattleState startMs=1000 official=1"]:
        value._engine.feed_line("[10:00:00.000] " + line)
    before = value.snapshot()
    assert before["steps"][0]["actionName"] == "战技"
    assert before["steps"][0]["iconPath"].endswith("battle.png")
    value._engine.feed_line(f"[10:00:01.000] SKILL_CAST_START seq=2 startMs=2000 inst=900 owner={characters[0]} ownerId=100 skill=variant-root skillSource=Player")
    untouched = value.snapshot()
    assert untouched["completedCount"] == 0
    assert untouched["steps"][0]["skillId"] == "exact-root"
    assert "actionKind" not in untouched["steps"][0]
    value._engine.feed_line(f"[10:00:02.000] SKILL_CAST_START seq=3 startMs=3000 inst=901 owner={characters[0]} ownerId=100 skill=exact-root skillSource=Player")
    completed = value.snapshot()
    assert completed["completedCount"] == 1 and completed["allCompleted"] is True
    assert completed["steps"][0]["skillId"] == "exact-root"
    assert "actionKind" not in completed["steps"][0]


@pytest.mark.parametrize("path", ["../escape.png", "/escape.png", "C:/escape.png", "skills\\escape.png", "skills/battle.webp"])
def test_asset_manifest_refuses_non_png_or_escaped_paths(tmp_path, path):
    assets = tmp_path / "assets"
    images = write_display_assets(assets)
    url = "/images/character/game-skills/battle.webp"
    images[url]["path"] = path
    (assets / "rotation-assets.json").write_text(json.dumps({"schemaVersion": 1, "images": images}), encoding="utf-8")
    value, _, _ = controller(tmp_path, envelope(), asset_root=assets)
    assert value._asset_path(url) == ""


def test_asset_resolution_requires_manifest_exact_url_and_matching_file_hash(tmp_path):
    assets = tmp_path / "assets"
    images = write_display_assets(assets)
    value, _, _ = controller(tmp_path, envelope(), asset_root=assets)
    url = "/images/character/game-skills/battle.webp"
    assert value._asset_path("https://example.test" + url) == ""
    assert value._asset_path("/images/character/game-skills/similar-battle.webp") == ""
    path = Path(value._asset_path(url))
    assert path.is_file()
    path.write_bytes(b"not the declared PNG")
    assert value._asset_path(url) == ""
    (assets / "rotation-assets.json").unlink()
    assert value._asset_path(url) == ""
    # Missing manifest was not permanently cached: a later official asset
    # install can resolve the same exact URL without resetting the battle.
    write_display_assets(assets)
    assert value._asset_path(url).endswith("battle.png")


def test_corrupt_cache_is_not_used(tmp_path):
    cache = RotationCache(tmp_path / "cache.json")
    cache.path.write_text("{partial", encoding="utf-8")
    assert cache.load("account-a", "https://example.test") is None


def test_deep_json_cannot_break_cache_or_settings_ui(tmp_path):
    path = tmp_path / "deep.json"
    path.write_text("[" * 5000 + "0" + "]" * 5000, encoding="utf-8")
    assert RotationCache(path).load("account-a", "https://example.test") is None
    assert OverlaySettingsStore(path).load()["guideEnabled"] is True


def test_exiting_does_not_commit_inflight_response(tmp_path):
    value, _, client = controller(tmp_path, envelope())
    context = value._reconcile_session(force=True)
    client.get_rotations = lambda: (value.stop() or envelope())
    value._sync_once(context)
    assert value._envelope is None and not value.cache.path.exists()


def test_auth_rejection_hides_even_when_cache_cannot_be_deleted(tmp_path, monkeypatch):
    value, _, _ = controller(tmp_path, ApiClientError("expired", status_code=401))
    value.cache.save(envelope(), "https://example.test")
    context = value._reconcile_session(force=True)
    monkeypatch.setattr(value.cache, "clear", lambda: (_ for _ in ()).throw(OSError("locked")))
    value._sync_once(context)
    assert value._envelope is None
    assert "登录已失效" in value.sync_state()["message"]


def test_tail_waits_for_complete_utf8_line_and_resets_when_file_replaced(tmp_path):
    value, sessions, _ = controller(tmp_path, envelope())
    sessions.value = None
    captured = []
    value._engine.feed_line = captured.append
    raw = "[10:00:00.000] SQUAD label=角色\n".encode("utf-8")
    value.trace_path.write_bytes(raw[:-2])
    worker = threading.Thread(target=value._tail_loop)
    worker.start()
    try:
        time.sleep(0.10)
        assert not captured
        with value.trace_path.open("ab") as stream:
            stream.write(raw[-2:])
        deadline = time.monotonic() + 2
        while not captured and time.monotonic() < deadline:
            time.sleep(0.02)
        assert captured == [raw.decode("utf-8").rstrip("\n")]
        old_engine = value._engine
        value.trace_path.write_bytes(b"[10:00:01.000] GAME_TIMER_RESET source=CAPTURE_SESSION_START\n")
        # Force a shorter file, independently of filesystem identity semantics.
        value.trace_path.write_bytes(b"RESET\n")
        deadline = time.monotonic() + 2
        while value._engine is old_engine and time.monotonic() < deadline:
            time.sleep(0.02)
        assert value._engine is not old_engine
        assert value.snapshot() is None
    finally:
        value.stop()
        worker.join(2)
        assert not worker.is_alive()
