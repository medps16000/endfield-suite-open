from concurrent.futures import ThreadPoolExecutor
from collections import Counter
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import json
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import delete

from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.db.models import AuthSessionRecord, AuthUserRecord, RotationPlanRecord
from app.db.session import SessionLocal
from app.main import create_app
from app.schemas.rotation import RotationUpdateRequest
from app.services.auth import DatabaseAuthService, SESSION_COOKIE_NAME
from app.services.rotations import REPO_ROOT, RotationService, _verify_resource_tree, rotation_catalog, rotation_service


@pytest.fixture
def accounts():
    result = []
    now = datetime.now(UTC)
    with SessionLocal() as session:
        for _ in range(2):
            key = uuid4().hex
            token = f"rotation-test-{key}"
            session.add(AuthUserRecord(id=key, email=f"{key}@example.com", nickname=f"rotation-{key}",
                created_at=now, is_disabled=False, is_admin=False))
            session.add(AuthSessionRecord(token=DatabaseAuthService._hash_secret(token), user_id=key,
                email=f"{key}@example.com", nickname=f"rotation-{key}", client_type="web",
                created_at=now, expires_at=now + timedelta(days=1)))
            result.append({"id": key, "token": token})
        session.commit()
    yield result
    with SessionLocal() as session:
        for account in result:
            session.execute(delete(RotationPlanRecord).where(RotationPlanRecord.owner_user_id == account["id"]))
            session.execute(delete(AuthSessionRecord).where(AuthSessionRecord.user_id == account["id"]))
            session.execute(delete(AuthUserRecord).where(AuthUserRecord.id == account["id"]))
        session.commit()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(get_settings(), "rotations_enabled", True)
    with TestClient(create_app()) as value:
        yield value


def auth(account):
    return {"Authorization": f"Bearer {account['token']}"}


@pytest.fixture
def payload():
    catalog = rotation_catalog()
    members = catalog.characters[:4]
    return {"name": "测试排轴", "dungeonId": catalog.maps[0].id,
        "characterIds": [item.id for item in members], "enabled": True,
        "steps": [{"id": "step-1", "atMs": 0, "characterId": members[0].id,
                   "skillId": members[0].skills[0].id, "note": "起手", "phase": "开场"},
                  {"id": "step-2", "atMs": 1000, "characterId": members[1].id,
                   "skillId": members[1].skills[0].id, "note": "", "phase": "开场"}],
        "displayNotes": "潜能 5，精炼 6，仅供参考"}


def create(client, account, payload):
    response = client.post("/api/rotations", headers=auth(account), json=payload)
    assert response.status_code == 201, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


@pytest.fixture(scope="module")
def ranked_map_sources():
    """Independent board -> native-child expectations from the selected resources."""
    catalog = json.loads((REPO_ROOT / "data/local_tables/dungeon_catalog.json").read_text("utf-8"))
    series = {}
    children = {}
    for path in (REPO_ROOT / "data/local_tables/dungeon/items").glob("*.json"):
        item = json.loads(path.read_text("utf-8"))
        series[item["templateId"]] = item
        for dungeon_id, child in item.get("childDungeons", {}).items():
            assert child["dungeonId"] == dungeon_id
            assert child["dungeonSeriesId"] == item["templateId"]
            assert dungeon_id in item["includeDungeonIds"]
            assert dungeon_id not in children
            children[dungeon_id] = child
    expected = {}
    boards = {}
    groups = Counter()
    for group in catalog["web"]["ranking_groups"]:
        for board in group["bosses"]:
            slug = board["slug"]
            assert slug not in boards
            if slug in children:
                native_ids = [slug]
            else:
                parent = series[slug]
                # A parent board expands only its explicitly declared complete
                # child relation, never a suffix, translated label or category.
                native_ids = parent["includeDungeonIds"]
                assert set(native_ids) == set(parent["childDungeons"])
                assert all(children[dungeon_id]["dungeonSeriesId"] == slug for dungeon_id in native_ids)
            assert native_ids
            boards[slug] = list(native_ids)
            for dungeon_id in native_ids:
                assert dungeon_id not in expected
                expected[dungeon_id] = {"id": dungeon_id, "name": children[dungeon_id]["name"],
                                        "seriesName": group["label"]}
                groups[group["key"]] += 1
    return {"expected": expected, "boards": boards, "groups": groups,
            "all_children": children, "series": series}


def test_catalog_has_only_exact_official_roots_and_maps(client, ranked_map_sources):
    response = client.get("/api/rotations/catalog")
    assert response.status_code == 200
    result = response.json()
    assert set(result) == {"schemaVersion", "maps", "characters"}
    assert result["schemaVersion"] == 2
    official = json.loads((REPO_ROOT / "data/local_static/skill/display_name_catalog.json").read_text("utf-8"))
    aliases = {"chr_0002_endminm", "chr_0003_endminf", "chr_9000_endmin"}
    for character in result["characters"]:
        for skill in character["skills"]:
            entry = official["entries"][skill["id"]]
            assert entry["proofKind"] == "official_char_growth_skill_list"
            assert any(path["steps"] == [] and path["rootSkillId"] == skill["id"]
                and path["skillGroupId"] == skill["groupId"]
                and (path["characterId"] == character["id"]
                     or character["id"] == "chr_9000_endmin" and path["characterId"] in aliases)
                for path in entry["paths"])
    maps = {item["id"]: item for item in result["maps"]}
    assert "indie_hard026_s" in maps
    assert len(maps) == len(result["maps"])
    assert maps == ranked_map_sources["expected"]
    assert {item["id"] for item in result["characters"]} & aliases == {"chr_9000_endmin"}


def test_ranked_maps_keep_all_native_difficulties_for_parent_boards_and_only_named_child_boards(client, ranked_map_sources):
    response = client.get("/api/rotations/catalog")
    assert response.status_code == 200
    maps = response.json()["maps"]
    assert len(maps) == len(ranked_map_sources["expected"]) == 82
    assert len(ranked_map_sources["boards"]) == 64
    assert ranked_map_sources["groups"] == {
        "crisis": 22, "crisisFragment": 6, "contingencyContract": 1, "warEcho": 20,
        "shadowPhase1": 9, "shadowPhase2": 6, "shadowPhase3": 6,
        "shadowPhase4": 4, "shadowPhase5": 4, "shadowPhase6": 4,
    }
    ids = {item["id"] for item in maps}
    assert set(ranked_map_sources["series"]["dung02_group_bossrush04"]["includeDungeonIds"]) <= ids
    assert {"dung02_bossrush04_01", "dung02_bossrush04_02", "dung02_bossrush04_03"} <= ids
    assert "indie_contract001" in ids
    assert not {"dung02_group_bossrush04", "indie_group_ccdg"} & ids
    board_order = {native: order for order, native_ids in enumerate(ranked_map_sources["boards"].values())
                   for native in native_ids}
    order = [board_order[item["id"]] for item in maps]
    assert order == sorted(order)


@pytest.mark.parametrize("dungeon_id", [
    "dung01_gold01",             # Official resource stage.
    "dung_zhuangfy_chartrain01",  # Official character teaching stage.
    "indie_hard026",             # Normal shadow stage, not its ranked child.
    "indie_battletower001",      # Official normal war-echo stage.
    "indie_battletower001_s",    # Official hard war-echo stage.
    "dungeon_gamblingdungeon01", # Explicitly unranked 百趣演武.
])
def test_real_official_nonranking_maps_are_rejected_on_create_and_update_without_changing_saved_plan(
        client, accounts, payload, ranked_map_sources, dungeon_id):
    assert dungeon_id in ranked_map_sources["all_children"]
    assert dungeon_id not in ranked_map_sources["expected"]
    owner = accounts[0]
    saved = create(client, owner, payload)
    before = client.get("/api/rotations", headers=auth(owner)).json()
    changed = {**payload, "dungeonId": dungeon_id}
    for response in (
        client.post("/api/rotations", headers=auth(owner), json=changed),
        client.patch(f"/api/rotations/{saved['id']}", headers=auth(owner), json={**changed, "revision": saved["revision"]}),
    ):
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "rotation_map_invalid"
        assert response.headers["cache-control"] == "no-store"
    assert client.get("/api/rotations", headers=auth(owner)).json() == before
    with SessionLocal() as session:
        row = session.get(RotationPlanRecord, saved["id"])
        assert row.dungeon_id == saved["dungeonId"]
        assert row.revision == saved["revision"]
        assert row.updated_at == saved["updatedAt"]


def test_preexisting_nonranking_plan_is_returned_without_automatic_migration_or_deletion(
        client, accounts, payload, ranked_map_sources):
    owner = accounts[0]
    old_map = "dungeon_gamblingdungeon01"
    assert old_map in ranked_map_sources["all_children"] and old_map not in ranked_map_sources["expected"]
    stored = {"id": f"rot_{uuid4().hex}", "owner_user_id": owner["id"], "name": "旧地图排轴仍保留",
        "dungeon_id": old_map, "character_ids_json": json.dumps(payload["characterIds"], ensure_ascii=False, indent=2),
        "enabled": True, "position": 0, "steps_json": json.dumps(payload["steps"], ensure_ascii=False, indent=2),
        "display_notes": "用户原备注", "revision": 7, "updated_at": "2026-10-14T00:00:00+00:00"}
    # Only this test account in the explicitly isolated pytest database is seeded.
    with SessionLocal() as session:
        session.add(RotationPlanRecord(**stored))
        session.commit()
    listed = client.get("/api/rotations", headers=auth(owner)).json()
    assert len(listed["rotations"]) == 1
    old = listed["rotations"][0]
    assert old["id"] == stored["id"] and old["dungeonId"] == old_map
    assert old["revision"] == 7 and old["enabled"] is True
    assert old["steps"] == payload["steps"]
    catalog = client.get("/api/rotations/catalog")
    assert catalog.status_code == 200
    assert old_map not in {item["id"] for item in catalog.json()["maps"]}
    rejected = client.patch(f"/api/rotations/{stored['id']}", headers=auth(owner),
        json={**payload, "dungeonId": old_map, "revision": 7})
    assert rejected.status_code == 422
    assert client.get("/api/rotations", headers=auth(owner)).json() == listed
    with SessionLocal() as session:
        row = session.get(RotationPlanRecord, stored["id"])
        assert row is not None
        assert {key: getattr(row, key) for key in stored} == stored


def test_catalog_action_roots_are_typed_player_actions_not_display_groups(client):
    catalog = client.get("/api/rotations/catalog").json()
    chars = {item['id']: item for item in catalog['characters']}
    def roots(char, kind):
        return next(set(action['rootSkillIds']) for action in chars[char]['actions'] if action['kind'] == kind)
    assert roots('chr_0030_zhuangfy', 'ultimate_skill') == {'chr_0030_zhuangfy_ultimate_skill'}
    assert roots('chr_0030_zhuangfy', 'battle_skill') == {
        'chr_0030_zhuangfy_normal_skill', 'chr_0030_zhuangfy_normal_skill_ult'}
    assert 'chr_0033_camille_normal_skill_2' in roots('chr_0033_camille', 'battle_skill')
    assert all('chr_0034_typhoea_floating_attack' not in skill
               for skill in roots('chr_0034_typhoea', 'battle_skill'))
    assert roots('chr_0019_karin', 'ultimate_skill') == {'chr_0019_karin_ultimate_skill'}
    assert next(a['skillName'] for a in chars['chr_0019_karin']['actions']
                if a['kind'] == 'ultimate_skill') == '小队，集结！'
    assert 'chr_0027_tangtang_passive_0' not in roots('chr_0027_tangtang', 'battle_skill')
    assert len(chars) == 33
    assert chars['chr_0036_jsspsi']['name'] == '祀'
    assert roots('chr_0036_jsspsi', 'battle_skill') == {
        'chr_0036_jsspsi_normal_skill', 'chr_0036_jsspsi_normal_skill_1'}
    assert roots('chr_0036_jsspsi', 'combo_skill') == {'chr_0036_jsspsi_combo_skill'}
    assert roots('chr_0036_jsspsi', 'ultimate_skill') == {'chr_0036_jsspsi_ultimate_skill'}
    for char in chars.values():
        assert {a['kind'] for a in char['actions']} == {'battle_skill', 'combo_skill', 'ultimate_skill'}
        allowed = {s['id'] for s in char['skills']}
        for action in char['actions']:
            assert action['rootSkillIds'] and len(action['rootSkillIds']) == len(set(action['rootSkillIds']))
            assert set(action['rootSkillIds']) <= allowed
            assert action['name'] in {'战技', '连携技', '终结技'}
            assert action['skillName'] and isinstance(action['iconUrl'], str)


def test_v1_exact_steps_and_new_actions_roundtrip_without_nulls(client, accounts, payload):
    owner = accounts[0]
    old = create(client, owner, payload)
    assert all('skillId' in step and 'actionKind' not in step for step in old['steps'])
    mixed = deepcopy(payload)
    del mixed['steps'][1]['skillId']
    mixed['steps'][1]['actionKind'] = 'battle_skill'
    new = create(client, owner, mixed)
    assert 'actionKind' not in new['steps'][0] and 'skillId' not in new['steps'][1]
    assert new['steps'][1]['actionKind'] == 'battle_skill'
    listing = client.get('/api/rotations', headers=auth(owner)).json()
    assert listing['schemaVersion'] == 2 and listing['rotations'] == [old, new]
    with SessionLocal() as session:
        assert json.loads(session.get(RotationPlanRecord, old['id']).steps_json) == old['steps']
        assert json.loads(session.get(RotationPlanRecord, new['id']).steps_json) == new['steps']
    # A pre-v2 request remains an exact binding after editing and storage.
    updated = client.patch(f"/api/rotations/{old['id']}", headers=auth(owner),
                           json={**payload, 'name': '旧步骤更新', 'revision': 1})
    assert updated.status_code == 200
    assert updated.json()['steps'] == old['steps']
    ordered = client.post('/api/rotations/order', headers=auth(owner), json={'ids': [new['id'], old['id']]}).json()
    assert ordered['schemaVersion'] == 2
    assert ordered['rotations'][0]['steps'] == new['steps']


@pytest.mark.parametrize('selection', [
    {}, {'actionKind': None}, {'skillId': None}, {'actionKind': ''}, {'actionKind': 'normal_attack'},
    {'actionKind': 'ultimate_skill', 'skillId': None},
    {'actionKind': None, 'skillId': 'chr_0002_endminm_ultimate_skill'},
    {'actionKind': 'ultimate_skill', 'skillId': 'chr_0002_endminm_ultimate_skill'},
])
def test_step_requires_one_explicit_nonnull_exact_skill_or_supported_action(client, accounts, payload, selection):
    body = deepcopy(payload)
    body['steps'][0].pop('skillId')
    body['steps'][0].update(selection)
    response = client.post('/api/rotations', headers=auth(accounts[0]), json=body)
    assert response.status_code == 422


def test_private_auth_ownership_and_no_store(client, accounts, payload):
    owner, other = accounts
    for headers in ({}, {"Authorization": "Bearer expired"}):
        response = client.get("/api/rotations", headers=headers)
        assert response.status_code == 401
        assert response.headers["cache-control"] == "no-store"
    plan = create(client, owner, payload)
    listed = client.get("/api/rotations", headers=auth(owner)).json()
    assert listed["accountId"] == owner["id"]
    assert listed["rotations"] == [plan]
    assert len(listed["revision"]) == 64
    assert client.get("/api/rotations", headers=auth(other)).json()["rotations"] == []
    patch = {**payload, "revision": 1}
    for method, body in (("patch", patch), ("delete", None)):
        denied = getattr(client, method)(f"/api/rotations/{plan['id']}", headers=auth(other),
                                         **({"json": body} if body else {}))
        missing = getattr(client, method)("/api/rotations/rot_missing", headers=auth(other),
                                         **({"json": body} if body else {}))
        assert denied.status_code == missing.status_code == 404
        assert denied.json() == missing.json()
        assert denied.headers["cache-control"] == "no-store"


def test_update_conflicts_keep_original_and_display_notes_do_not_select(client, accounts, payload):
    owner = accounts[0]
    plan = create(client, owner, payload)
    before = client.get("/api/rotations", headers=auth(owner)).json()["revision"]
    changed = client.patch(f"/api/rotations/{plan['id']}", headers=auth(owner),
        json={**payload, "name": "新排轴", "displayNotes": "任意装备说明", "revision": 1})
    assert changed.status_code == 200
    assert changed.json()["revision"] == 2
    assert changed.json()["dungeonId"] == plan["dungeonId"]
    assert changed.json()["characterIds"] == plan["characterIds"]
    stale = client.patch(f"/api/rotations/{plan['id']}", headers=auth(owner), json={**payload, "revision": 1})
    assert stale.status_code == 409
    assert stale.headers["cache-control"] == "no-store"
    listed = client.get("/api/rotations", headers=auth(owner)).json()
    assert listed["revision"] != before
    assert listed["rotations"][0]["name"] == "新排轴"


def test_order_exact_permutation_stable_activation_and_delete_compaction(client, accounts, payload):
    owner, other = accounts
    plans = [create(client, owner, {**payload, "enabled": enabled}) for enabled in (True, False, True)]
    outsider = create(client, other, payload)
    ids = [plan["id"] for plan in plans]
    for invalid in (ids[:-1], [ids[0], ids[0], ids[2]], [*ids[:-1], outsider["id"]]):
        result = client.post("/api/rotations/order", headers=auth(owner), json={"ids": invalid})
        assert result.status_code == 422
        assert result.headers["cache-control"] == "no-store"
    ordered = client.post("/api/rotations/order", headers=auth(owner), json={"ids": ids[::-1]}).json()
    assert [plan["id"] for plan in ordered["rotations"]] == ids[::-1]
    assert [plan["position"] for plan in ordered["rotations"]] == [0, 1, 2]
    assert [plan["revision"] for plan in ordered["rotations"]] == [2, 1, 2]
    assert [plan["id"] for plan in ordered["rotations"] if plan["enabled"]] == [ids[2], ids[0]]
    deleted = client.delete(f"/api/rotations/{ids[2]}", headers=auth(owner))
    assert deleted.status_code == 200 and deleted.json() == {"ok": True}
    remaining = client.get("/api/rotations", headers=auth(owner)).json()
    assert [plan["position"] for plan in remaining["rotations"]] == [0, 1]
    assert [plan["revision"] for plan in remaining["rotations"]] == [2, 3]


def test_invalid_identity_schema_and_strict_runtime_limits(client, accounts, payload):
    owner = accounts[0]
    invalids = []
    for field, value in (("dungeonId", "indie_hard026_unknown"), ("characterIds", payload["characterIds"][:3]),
                         ("name", "  "), ("enabled", 1), ("steps", []), ("displayNotes", "x" * 2001),
                         ("position", 0), ("revision", 1), ("name", "x" * 81)):
        invalids.append({**payload, field: value})
    duplicate = deepcopy(payload)
    duplicate["characterIds"][1] = duplicate["characterIds"][0]
    invalids.append(duplicate)
    for field, value in (("atMs", True), ("atMs", 3_600_001), ("atMs", -1), ("id", "   "),
                         ("skillId", "buff_chr_0030_zhuangfy_sword_triggerd"),
                         ("skillId", payload["steps"][1]["skillId"]), ("note", "x" * 501)):
        changed = deepcopy(payload)
        changed["steps"][0][field] = value
        invalids.append(changed)
    reversed_steps = deepcopy(payload)
    reversed_steps["steps"].reverse()
    invalids.append(reversed_steps)
    duplicate_step = deepcopy(payload)
    duplicate_step["steps"][1]["id"] = duplicate_step["steps"][0]["id"]
    invalids.append(duplicate_step)
    for invalid in invalids:
        response = client.post("/api/rotations", headers=auth(owner), json=invalid)
        assert response.status_code == 422, response.text
        assert response.headers["cache-control"] == "no-store"
        assert "error" in response.json()
    assert client.get("/api/rotations", headers=auth(owner)).json()["rotations"] == []


def test_administrator_alias_canonicalization_and_canonical_duplicates(client, accounts, payload):
    owner = accounts[0]
    male = deepcopy(payload)
    male["characterIds"][0] = "chr_0002_endminm"
    male["steps"][0]["characterId"] = "chr_0002_endminm"
    plan = create(client, owner, male)
    assert plan["characterIds"][0] == plan["steps"][0]["characterId"] == "chr_9000_endmin"
    duplicate = deepcopy(male)
    duplicate["characterIds"][1] = "chr_0003_endminf"
    assert client.post("/api/rotations", headers=auth(owner), json=duplicate).status_code == 422


def test_cookie_csrf_no_store_and_disabled_owner(client, accounts, payload):
    owner = accounts[0]
    client.cookies.set(SESSION_COOKIE_NAME, owner["token"])
    response = client.post("/api/rotations", json=payload, headers={"Origin": "https://untrusted.example"})
    assert response.status_code == 403
    assert response.headers["cache-control"] == "no-store"
    assert client.post("/api/rotations", json=payload, headers={"Origin": "http://localhost:3000"}).status_code == 201
    with SessionLocal() as session:
        session.get(AuthUserRecord, owner["id"]).is_disabled = True
        session.commit()
    assert client.get("/api/rotations", headers=auth(owner)).status_code == 401


def test_concurrent_edits_only_one_revision_wins(client, accounts, payload):
    owner = accounts[0]
    plan = create(client, owner, payload)
    service = rotation_service
    def change(name):
        try:
            result = service.update(owner["id"], plan["id"], RotationUpdateRequest(**{**payload, "name": name, "revision": 1}))
            return result.name
        except AppError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(change, ("甲", "乙")))
    assert results.count("rotation_conflict") == 1
    assert client.get("/api/rotations", headers=auth(owner)).json()["rotations"][0]["revision"] == 2


def test_plan_quota_is_persistent_and_checked_under_owner_lock(client, accounts, payload):
    owner = accounts[0]
    plan = create(client, owner, payload)
    with SessionLocal() as session:
        original = session.get(RotationPlanRecord, plan["id"])
        for position in range(1, 100):
            session.add(RotationPlanRecord(id=f"rot_{uuid4().hex}", owner_user_id=owner["id"],
                name=original.name, dungeon_id=original.dungeon_id, character_ids_json=original.character_ids_json,
                enabled=True, position=position, steps_json=original.steps_json, display_notes="",
                revision=1, updated_at=original.updated_at))
        session.commit()
    from app.schemas.rotation import RotationCreateRequest
    with pytest.raises(AppError) as exc:
        RotationService().create(owner["id"], RotationCreateRequest(**payload))
    assert exc.value.code == "rotation_limit"


def test_private_unexpected_failures_are_not_cached_or_exposed(client, accounts, monkeypatch):
    def fail(owner):
        raise RuntimeError("private internal data")
    monkeypatch.setattr(rotation_service, "list", fail)
    response = client.get("/api/rotations", headers=auth(accounts[0]))
    assert response.status_code == 500
    assert response.headers["cache-control"] == "no-store"
    assert "private internal data" not in response.text


def test_catalog_receipt_verifies_bytes_with_release_newline_convention(tmp_path):
    from hashlib import sha256
    path = tmp_path / "official.json"
    path.write_bytes(b'{"value":1}\r\n')
    files = {"official.json": sha256(b'{"value":1}\n').hexdigest()}
    expected = {"sha256": sha256(json.dumps(files, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest(), "fileCount": 1, "jsonNewlines": "LF"}
    _verify_resource_tree(tmp_path, expected)
    path.write_bytes(b'{"value":2}\r\n')
    with pytest.raises(ValueError):
        _verify_resource_tree(tmp_path, expected)


def test_utf8_account_size_quota_rejects_create_and_update_atomically(client, accounts, payload, monkeypatch):
    import app.services.rotations as module
    owner = accounts[0]
    plan = create(client, owner, payload)
    before = client.get("/api/rotations", headers=auth(owner))
    # A UTF-8 Chinese note uses three bytes per character. Keep a fixed small
    # test ceiling to exercise the production byte-based quota without 8 MiB.
    monkeypatch.setattr(module, "MAX_ACCOUNT_BYTES", len(before.content) + 600)
    oversized = {**payload, "displayNotes": "中" * 500}
    response = client.patch(f"/api/rotations/{plan['id']}", headers=auth(owner),
                            json={**oversized, "revision": 1})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "rotation_storage_limit"
    response = client.post("/api/rotations", headers=auth(owner), json=oversized)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "rotation_storage_limit"
    assert client.get("/api/rotations", headers=auth(owner)).json() == before.json()


def test_expected_ui_account_is_checked_atomically_for_all_private_operations(client, accounts, payload):
    owner, other = accounts
    plan = create(client, owner, payload)
    other_plan = create(client, other, payload)
    before_owner = client.get("/api/rotations", headers=auth(owner)).json()
    before_other = client.get("/api/rotations", headers=auth(other)).json()
    # A tab still rendered for A, but another tab replaced the shared cookie
    # with B. Every mutation must fail before creating or changing B's data.
    switched = {"Cookie": f"{SESSION_COOKIE_NAME}={other['token']}",
                "X-Endfield-Rotation-Account": owner["id"], "Origin": "http://localhost:3000"}
    for method, path, body in (("get", "/api/rotations", None),
                              ("post", "/api/rotations", payload),
                              ("patch", f"/api/rotations/{other_plan['id']}", {**payload, "revision": 1}),
                              ("post", "/api/rotations/order", {"ids": [other_plan["id"]]}),
                              ("delete", f"/api/rotations/{other_plan['id']}", None)):
        response = getattr(client, method)(path, headers=switched, **({"json": body} if body else {}))
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "ROTATION_ACCOUNT_CHANGED"
        assert response.headers["cache-control"] == "no-store"
    assert client.get("/api/rotations", headers=auth(owner)).json() == before_owner
    assert client.get("/api/rotations", headers=auth(other)).json() == before_other
    same_account = {**auth(owner), "X-Endfield-Rotation-Account": owner["id"]}
    assert client.get("/api/rotations", headers=same_account).json() == before_owner
    assert client.post("/api/rotations", headers=same_account, json=payload).status_code == 201
    bearer_mismatch = {**auth(other), "X-Endfield-Rotation-Account": owner["id"]}
    assert client.get("/api/rotations", headers=bearer_mismatch).json()["error"]["code"] == "ROTATION_ACCOUNT_CHANGED"
    assert client.get("/api/rotations/catalog", headers=switched).status_code == 200


def test_cors_preflight_allows_expected_rotation_account_header(client):
    response = client.options("/api/rotations", headers={"Origin": "http://localhost:3000",
        "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "X-Endfield-Rotation-Account,Content-Type"})
    assert response.status_code == 200
    assert "x-endfield-rotation-account" in response.headers["access-control-allow-headers"].lower()
    assert response.headers["cache-control"] == "no-store"


def test_rotations_are_disabled_by_default_and_can_be_explicitly_restored(monkeypatch):
    monkeypatch.delenv("ROTATIONS_ENABLED", raising=False)
    assert Settings(_env_file=None).rotations_enabled is False
    monkeypatch.setenv("ROTATIONS_ENABLED", "true")
    assert Settings(_env_file=None).rotations_enabled is True


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/rotations/catalog"),
    ("POST", "/api/rotations"),
    ("POST", "/api/rotations/order"),
    ("PATCH", "/api/rotations/rot_existing"),
    ("DELETE", "/api/rotations/rot_existing"),
    ("GET", "/api/rotations/rot_existing/export"),
    ("GET", "/api/rotations/assets/icon.webp"),
    ("POST", "/api/rotations/rot_existing/activation"),
    ("POST", "/api/rotations/activate"),
    ("GET", "/api/rotations/unknown"),
    ("GET", "/api/rotations/"),
    ("HEAD", "/api/rotations"),
    ("OPTIONS", "/api/rotations"),
])
def test_disabled_rotation_namespace_rejects_before_validation_or_storage(
    client, monkeypatch, method, path
):
    import app.api.routes.rotations as routes

    monkeypatch.setattr(get_settings(), "rotations_enabled", False)

    def fail(*args, **kwargs):
        raise AssertionError("Disabled rotations must not access their service or catalog")

    monkeypatch.setattr(routes, "rotation_catalog", fail)
    for operation in ("list", "create", "reorder", "update", "delete"):
        monkeypatch.setattr(rotation_service, operation, fail)
    response = client.request(method, path, content=b'{"private-note":invalid',
                              headers={"Content-Type": "application/json"})
    assert response.status_code == 410
    if method != "HEAD":
        assert response.json() == {"error": {
            "code": "ROTATIONS_DISABLED", "message": "排轴功能已下线。",
        }}
    assert "private-note" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["vary"] == "Cookie, Authorization"


def test_disabled_sync_returns_feature_forbidden_without_reading_or_changing_plans(
    client, accounts, payload, monkeypatch
):
    owner, other = accounts
    saved = create(client, owner, payload)
    original = client.get("/api/rotations", headers=auth(owner)).json()
    with SessionLocal() as session:
        row = session.get(RotationPlanRecord, saved["id"])
        stored = {column.name: getattr(row, column.name) for column in RotationPlanRecord.__table__.columns}

    monkeypatch.setattr(get_settings(), "rotations_enabled", False)
    with monkeypatch.context() as blocked:
        import app.api.routes.rotations as routes

        def fail(*args, **kwargs):
            raise AssertionError("Disabled sync must not authenticate or access stored plans")

        blocked.setattr(routes, "_owner_id", fail)
        for operation in ("list", "create", "reorder", "update", "delete"):
            blocked.setattr(rotation_service, operation, fail)
        response = client.get("/api/rotations", headers=auth(owner))
        assert response.status_code == 403
        assert response.json() == {"error": {
            "code": "ROTATIONS_DISABLED", "message": "排轴功能已下线。",
        }}
        assert response.headers["cache-control"] == "no-store"
        assert client.get("/api/rotations").status_code == 403
        assert client.get("/api/rotations", headers={
            **auth(owner), "X-Endfield-Rotation-Account": other["id"],
        }).status_code == 403
        for method in ("POST", "PATCH", "DELETE"):
            path = "/api/rotations" if method == "POST" else f"/api/rotations/{saved['id']}"
            assert client.request(method, path, headers=auth(owner), json=payload).status_code == 410

    with SessionLocal() as session:
        row = session.get(RotationPlanRecord, saved["id"])
        assert {column.name: getattr(row, column.name)
                for column in RotationPlanRecord.__table__.columns} == stored
    monkeypatch.setattr(get_settings(), "rotations_enabled", True)
    assert client.get("/api/rotations", headers=auth(owner)).json() == original


def test_rotation_disable_does_not_change_other_api_routes(client, monkeypatch):
    paths = ("/healthz", "/api/auth/me", "/api/battles/mine", "/api/rotations-other")
    original = [(response.status_code, response.json()) for response in
                (client.get(path) for path in paths)]
    assert original[0] == (200, {"status": "ok"})
    monkeypatch.setattr(get_settings(), "rotations_enabled", False)
    after = [(response.status_code, response.json()) for response in
             (client.get(path) for path in paths)]
    assert after == original
