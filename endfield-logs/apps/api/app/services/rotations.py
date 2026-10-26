"""Private, explicitly ordered rotation plans validated against official data."""
from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path
from threading import Lock
from uuid import uuid4

from sqlalchemy import select

from app.core.errors import AppError
from app.db.models import AuthUserRecord, RotationPlanRecord
from app.db.session import SessionLocal
from app.schemas.rotation import (
    RotationAction, RotationCatalog, RotationCharacter, RotationCreateRequest, RotationEnvelope,
    RotationMap, RotationPlan, RotationSkill, RotationUpdateRequest,
)
from app.services.public_data import ADMIN_CHARACTER_KEYS, ADMIN_STATISTICS_CHARACTER_KEY


REPO_ROOT = Path(__file__).resolve().parents[4]
MAX_PLANS = 100
MAX_ACCOUNT_BYTES = 8 * 1024 * 1024


def canonical_character(value: str) -> str:
    return ADMIN_STATISTICS_CHARACTER_KEY if value in ADMIN_CHARACTER_KEYS else value


def _verify_resource_tree(root: Path, expected: dict) -> None:
    # Check the release receipt, including JSON newline normalization used by
    # the resource packager. No installation-dependent extracted files needed.
    files = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or path.is_junction():
            raise ValueError("Indirect official resource path")
        if path.is_file():
            data = path.read_bytes()
            if path.suffix.lower() == ".json":
                data = data.replace(b"\r\n", b"\n")
            files[path.relative_to(root).as_posix()] = sha256(data).hexdigest()
    canonical = json.dumps(files, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    if not files or expected != {"sha256": sha256(canonical).hexdigest(),
                                 "fileCount": len(files), "jsonNewlines": "LF"}:
        raise ValueError("Official resources differ from release receipt")


def _attach_actions(characters: dict[str, RotationCharacter], official_ids: set[str],
                    action_catalog: dict, icons: dict) -> None:
    actions = {}
    for original in sorted(official_ids):
        owner = canonical_character(original)
        allowed = {skill.id for skill in characters[owner].skills}
        seen = set()
        for action in action_catalog["characters"][original]["actions"]:
            roots = action["rootSkillIds"]
            if (not isinstance(roots, list) or not roots or len(set(roots)) != len(roots)
                    or any(root not in allowed for root in roots) or action["kind"] in seen):
                raise ValueError("Action roots differ from the exact official owner roots")
            seen.add(action["kind"])
            icon = icons.get(action["iconId"], {})
            icon_url = icon.get("url", "")
            if icon_url and icon_url != f"/images/character/game-skills/{action['iconId']}.webp":
                raise ValueError("Unexpected official action icon URL")
            public = RotationAction(kind=action["kind"], name=action["name"],
                skillName=action["skillName"], iconUrl=icon_url, rootSkillIds=sorted(roots))
            key = (owner, public.kind)
            if key in actions:
                previous = actions[key]
                if (previous.name, previous.skillName, previous.iconUrl) != (public.name, public.skillName, public.iconUrl):
                    raise ValueError("Conflicting canonical official action labels")
                previous.rootSkillIds = sorted(set(previous.rootSkillIds) | set(public.rootSkillIds))
            else:
                actions[key] = public
    for (owner, _), action in actions.items():
        characters[owner].actions.append(action)


@lru_cache(maxsize=1)
def rotation_catalog() -> RotationCatalog:
    """Only ranked official dungeon IDs and direct player skill roots."""
    try:
        snapshot = json.loads((REPO_ROOT / "data/game_resource_snapshot.json").read_text("utf-8"))
        if snapshot.get("schemaVersion") != 1:
            raise ValueError("Unknown resource receipt")
        for name in ("local_tables", "local_static"):
            _verify_resource_tree(REPO_ROOT / "data" / name, snapshot["generated"][name])
        raw_characters = json.loads((REPO_ROOT / "data/local_tables/character/manifest.json").read_text("utf-8"))
        avatar_manifest = json.loads((REPO_ROOT / "apps/web/public/images/character/game-avatars/manifest.json").read_text("utf-8"))
        skill_catalog = json.loads((REPO_ROOT / "data/local_static/skill/display_name_catalog.json").read_text("utf-8"))
        action_catalog = json.loads((REPO_ROOT / "data/local_static/skill/action_catalog.json").read_text("utf-8"))
        if (skill_catalog.get("schemaVersion") != 1
                or skill_catalog.get("source") != "official_CharGrowth_I18n_typed_skill_display"
                or not isinstance(skill_catalog.get("sourceTables"), dict)):
            raise ValueError("Unknown skill catalog")
        if (action_catalog.get("schemaVersion") != 1
                or action_catalog.get("source") != "official_typed_skill_specification_rotation_actions"
                or action_catalog.get("decoder") != skill_catalog.get("decoder")
                or action_catalog.get("sourceTables") != skill_catalog.get("sourceTables")):
            raise ValueError("Action catalog is not bound to the selected official resources")
        characters = {}
        official_ids = set()
        for item in raw_characters["entries"]:
            original = item["id"]
            official_ids.add(original)
            canonical = canonical_character(original)
            if canonical not in characters:
                avatar = avatar_manifest["avatars"].get(canonical, {}).get("url", "")
                characters[canonical] = RotationCharacter(id=canonical, name=item["name"],
                                                          avatarUrl=avatar, skills=[])
        for skill_id, item in sorted(skill_catalog["entries"].items()):
            if (skill_id in skill_catalog["conflicts"]
                    or item.get("proofKind") != "official_char_growth_skill_list"):
                continue
            owners = {}
            for path in item.get("paths", []):
                owner = path.get("characterId")
                if (owner in official_ids and path.get("steps") == []
                        and path.get("rootSkillId") == skill_id
                        and path.get("skillGroupId") in item.get("skillGroupIds", [])):
                    owners.setdefault(canonical_character(owner), []).append(path)
            if len(owners) != 1 or not item.get("displayName"):
                continue
            owner, paths = next(iter(owners.items()))
            # Administrator aliases are the existing exact ID relation. Prefer
            # its explicit canonical owner path where provided, never rewrite
            # skill identities or infer owners from an ID prefix.
            paths.sort(key=lambda p: (p["characterId"] != owner, p["characterId"], p["skillGroupId"]))
            groups = {p["skillGroupId"] for p in paths if p["characterId"] == paths[0]["characterId"]}
            if len(groups) != 1:
                continue
            characters[owner].skills.append(RotationSkill(id=skill_id, name=item["displayName"],
                                                          groupId=next(iter(groups))))
        icon_manifest = REPO_ROOT / "apps/web/public/images/character/game-skills/manifest.json"
        icons = json.loads(icon_manifest.read_text("utf-8"))["icons"] if icon_manifest.is_file() else {}
        _attach_actions(characters, official_ids, action_catalog, icons)
        ranking_catalog = json.loads((REPO_ROOT / "data/local_tables/dungeon_catalog.json").read_text("utf-8"))
        ranked_boards = {}
        for group in ranking_catalog["web"]["ranking_groups"]:
            for board in group["bosses"]:
                if board["slug"] in ranked_boards:
                    raise ValueError("Conflicting ranking board identity")
                ranked_boards[board["slug"]] = (group["label"], len(ranked_boards))
        maps = {}
        map_boards = {}
        for path in sorted((REPO_ROOT / "data/local_tables/dungeon/items").glob("*.json")):
            series = json.loads(path.read_text("utf-8"))
            for dungeon_id, item in series.get("childDungeons", {}).items():
                if (item.get("dungeonId") != dungeon_id
                        or item.get("dungeonSeriesId") != series["templateId"]
                        or dungeon_id not in series.get("includeDungeonIds", [])
                        or not item.get("name")):
                    continue
                # A board explicitly names either this native child or its
                # official parent series. Never infer rankability from names,
                # category strings, ID suffixes, enemy identity, or difficulty.
                targets = {identity for identity in (dungeon_id, series["templateId"]) if identity in ranked_boards}
                if not targets:
                    continue
                if len(targets) != 1:
                    raise ValueError("Dungeon has conflicting explicit ranking boards")
                board = next(iter(targets))
                entry = RotationMap(id=dungeon_id, name=item["name"], seriesName=ranked_boards[board][0])
                if dungeon_id in maps and (maps[dungeon_id] != entry or map_boards[dungeon_id] != board):
                    raise ValueError("Conflicting explicit dungeon identity")
                maps[dungeon_id] = entry
                map_boards[dungeon_id] = board
        if set(map_boards.values()) != set(ranked_boards):
            raise ValueError("A ranking board has no exact official dungeon identity")
        if not maps or not characters or any(not item.skills for item in characters.values()):
            raise ValueError("Incomplete official rotation catalog")
        return RotationCatalog(maps=sorted(maps.values(), key=lambda item: ranked_boards[map_boards[item.id]][1]),
                               characters=list(characters.values()))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise AppError(503, "rotation_catalog_unavailable", "排轴资源目录暂不可用，请稍后重试。") from exc


def _validated_payload(payload: RotationCreateRequest) -> dict:
    catalog = rotation_catalog()
    character_skills = {item.id: {skill.id for skill in item.skills} for item in catalog.characters}
    character_actions = {item.id: {action.kind for action in item.actions if action.rootSkillIds}
                         for item in catalog.characters}
    if payload.dungeonId not in {item.id for item in catalog.maps}:
        raise AppError(422, "rotation_map_invalid", "副本不在当前榜单收录范围内，请重新选择。")
    squad = [canonical_character(value) for value in payload.characterIds]
    if len(set(squad)) != 4 or any(value not in character_skills for value in squad):
        raise AppError(422, "rotation_squad_invalid", "请选择四位不同的官方角色。")
    steps = []
    for step in payload.steps:
        owner = canonical_character(step.characterId)
        if owner not in squad or (step.skillId is not None
                and step.skillId not in character_skills.get(owner, set())) or (step.actionKind is not None
                and step.actionKind not in character_actions.get(owner, set())):
            raise AppError(422, "rotation_skill_invalid", "步骤技能必须是该阵容角色的官方直接释放技能。")
        steps.append({**step.model_dump(exclude_none=True), "characterId": owner})
    return {"name": payload.name, "dungeon_id": payload.dungeonId,
            "character_ids_json": json.dumps(squad, ensure_ascii=False), "enabled": payload.enabled,
            "steps_json": json.dumps(steps, ensure_ascii=False), "display_notes": payload.displayNotes}


def _plan(record: RotationPlanRecord) -> RotationPlan:
    return RotationPlan(id=record.id, name=record.name, dungeonId=record.dungeon_id,
                        characterIds=json.loads(record.character_ids_json), enabled=record.enabled,
                        position=record.position, steps=json.loads(record.steps_json),
                        displayNotes=record.display_notes, revision=record.revision,
                        updatedAt=record.updated_at)


def _envelope(owner: str, records: list[RotationPlanRecord]) -> RotationEnvelope:
    plans = [_plan(record) for record in records]
    content = json.dumps([plan.model_dump(exclude_none=True) for plan in plans], ensure_ascii=False,
                         sort_keys=True, separators=(",", ":")).encode()
    return RotationEnvelope(accountId=owner, revision=sha256(content).hexdigest(), rotations=plans)


def _check_storage_quota(owner: str, records: list[RotationPlanRecord]) -> None:
    # Bound the actual UTF-8 envelope, not characters or a mean plan size. The
    # client's 12 MB cache ceiling has room for this 8 MiB server-owned payload.
    if len(_envelope(owner, records).model_dump_json(exclude_none=True).encode("utf-8")) > MAX_ACCOUNT_BYTES:
        raise AppError(422, "rotation_storage_limit", "每个账号的排轴总容量最多 8 MiB，请精简步骤备注或删除旧排轴。")


class RotationService:
    def __init__(self) -> None:
        # PostgreSQL serializes per-owner writes with FOR UPDATE. SQLite's
        # single-process development API uses this lock for equivalent safety.
        self._write_lock = Lock()

    @staticmethod
    def _owner(session, owner: str) -> None:
        user = session.scalar(select(AuthUserRecord).where(AuthUserRecord.id == owner).with_for_update())
        if user is None or user.is_disabled:
            raise AppError(401, "unauthorized", "请先登录后再管理排轴。")

    @staticmethod
    def _records(session, owner: str) -> list[RotationPlanRecord]:
        return list(session.scalars(select(RotationPlanRecord)
                    .where(RotationPlanRecord.owner_user_id == owner)
                    .order_by(RotationPlanRecord.position, RotationPlanRecord.id)))

    @staticmethod
    def _owned_record(session, owner: str, plan_id: str) -> RotationPlanRecord:
        record = session.scalar(select(RotationPlanRecord).where(
            RotationPlanRecord.id == plan_id, RotationPlanRecord.owner_user_id == owner))
        if record is None:
            raise AppError(404, "rotation_not_found", "排轴不存在。")
        return record

    def list(self, owner: str) -> RotationEnvelope:
        with SessionLocal() as session:
            return _envelope(owner, self._records(session, owner))

    def create(self, owner: str, payload: RotationCreateRequest) -> RotationPlan:
        values = _validated_payload(payload)
        with self._write_lock, SessionLocal() as session:
            self._owner(session, owner)
            records = self._records(session, owner)
            if len(records) >= MAX_PLANS:
                raise AppError(422, "rotation_limit", "每个账号最多保存 100 套排轴。")
            record = RotationPlanRecord(id=f"rot_{uuid4().hex}", owner_user_id=owner,
                **values, position=len(records), revision=1, updated_at=datetime.now(UTC).isoformat())
            _check_storage_quota(owner, [*records, record])
            session.add(record)
            session.commit()
            return _plan(record)

    def update(self, owner: str, plan_id: str, payload: RotationUpdateRequest) -> RotationPlan:
        with self._write_lock, SessionLocal() as session:
            self._owner(session, owner)
            record = self._owned_record(session, owner, plan_id)
            if record.revision != payload.revision:
                raise AppError(409, "rotation_conflict", "排轴已在其他页面修改，请刷新后再保存。")
            values = _validated_payload(payload)
            for key, value in values.items():
                setattr(record, key, value)
            record.revision += 1
            record.updated_at = datetime.now(UTC).isoformat()
            _check_storage_quota(owner, self._records(session, owner))
            session.commit()
            return _plan(record)

    def delete(self, owner: str, plan_id: str) -> None:
        with self._write_lock, SessionLocal() as session:
            self._owner(session, owner)
            record = self._owned_record(session, owner, plan_id)
            session.delete(record)
            now = datetime.now(UTC).isoformat()
            remaining = [item for item in self._records(session, owner) if item.id != plan_id]
            for position, item in enumerate(remaining):
                if item.position != position:
                    item.position = position
                    item.revision += 1
                    item.updated_at = now
            session.commit()

    def reorder(self, owner: str, ids: list[str]) -> RotationEnvelope:
        with self._write_lock, SessionLocal() as session:
            self._owner(session, owner)
            records = {item.id: item for item in self._records(session, owner)}
            if len(ids) != len(records) or set(ids) != records.keys():
                raise AppError(422, "rotation_order_invalid", "排序必须包含当前账号的全部排轴，且不能重复。")
            now = datetime.now(UTC).isoformat()
            for position, plan_id in enumerate(ids):
                record = records[plan_id]
                if record.position != position:
                    record.position = position
                    record.revision += 1
                    record.updated_at = now
            _check_storage_quota(owner, list(records.values()))
            session.commit()
            return _envelope(owner, [records[plan_id] for plan_id in ids])


rotation_service = RotationService()
