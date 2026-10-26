"""Account-bound rotation cache and lightweight live guide input.

Network work and trace consumption run separately from Tk and the damage
parser. Saved plans contain no credentials and never cross account/origin.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path, PurePosixPath
from typing import Any

from parser_core.rotation_guide import RotationGuideEngine
from uploader_core.api_client import ApiClient, ApiClientError
from uploader_core.session_store import SessionStore
from uploader_core.settings_store import SettingsStore
from .rotation_clock_proof import load_rotation_clock_spec
from .runtime_paths import bundle_root

# Product retirement gate. Keep stored plans and settings intact; neither
# network sync nor offline cache matching runs while guidance is disabled.
ROTATION_GUIDANCE_ENABLED = False


def _app_data_root() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".endfield-pcap") / "EndfieldPCAP"


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def validate_rotation_envelope(payload: Any, account_id: str) -> dict:
    """Apply the API's bounded wire format before storing untrusted JSON."""
    if not isinstance(payload, dict) or type(payload.get("schemaVersion")) is not int or payload["schemaVersion"] not in {1, 2}:
        raise ValueError("不支持的排轴格式")
    if not account_id or payload.get("accountId") != account_id:
        raise ValueError("排轴账号不一致")
    revision = payload.get("revision")
    plans = payload.get("rotations")
    if not isinstance(revision, str) or len(revision) > 160 or not isinstance(plans, list) or len(plans) > 100:
        raise ValueError("排轴列表无效")

    def text_field(value: Any, maximum: int, *, required: bool = False) -> str:
        if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
            raise ValueError("排轴字段无效")
        return value

    clean: list[dict] = []
    ids: set[str] = set()
    positions: set[int] = set()
    for plan in plans:
        if not isinstance(plan, dict):
            raise ValueError("排轴记录无效")
        plan_id = text_field(plan.get("id"), 160, required=True)
        position = plan.get("position")
        plan_revision = plan.get("revision")
        enabled = plan.get("enabled")
        characters = plan.get("characterIds")
        steps = plan.get("steps")
        if plan_id in ids or type(position) is not int or position < 0 or position in positions:
            raise ValueError("排轴顺序无效")
        if type(plan_revision) is not int or plan_revision < 1 or type(enabled) is not bool:
            raise ValueError("排轴版本无效")
        if not isinstance(characters, list) or len(characters) != 4:
            raise ValueError("排轴需要四名角色")
        characters = [text_field(item, 160, required=True) for item in characters]
        if len(set(characters)) != 4 or not isinstance(steps, list) or not 1 <= len(steps) <= 500:
            raise ValueError("排轴角色或步骤无效")
        clean_steps = []
        step_ids: set[str] = set()
        previous_time = -1
        for step in steps:
            if not isinstance(step, dict):
                raise ValueError("排轴步骤无效")
            step_id = text_field(step.get("id"), 80, required=True)
            at_ms = step.get("atMs")
            character_id = text_field(step.get("characterId"), 160, required=True)
            if step_id in step_ids or type(at_ms) is not int or not previous_time <= at_ms <= 3_600_000 or at_ms < 0:
                raise ValueError("排轴步骤顺序无效")
            if character_id not in characters:
                raise ValueError("排轴步骤角色不在阵容内")
            skill_id, action_kind = step.get("skillId"), step.get("actionKind")
            if (("skillId" in step and (not isinstance(skill_id, str) or not skill_id.strip() or len(skill_id) > 160))
                    or ("actionKind" in step and (payload["schemaVersion"] != 2 or not isinstance(action_kind, str)
                                                   or action_kind not in {"battle_skill", "combo_skill", "ultimate_skill"}))
                    or ("skillId" in step) == ("actionKind" in step)):
                raise ValueError("排轴步骤技能无效")
            clean_steps.append({
                "id": step_id, "atMs": at_ms, "characterId": character_id,
                **({"skillId": skill_id} if skill_id is not None else {"actionKind": action_kind}),
                "note": text_field(step.get("note", ""), 500),
                "phase": text_field(step.get("phase", ""), 80),
            })
            step_ids.add(step_id)
            previous_time = at_ms
        clean.append({
            "id": plan_id, "position": position, "revision": plan_revision, "enabled": enabled,
            "name": text_field(plan.get("name"), 80, required=True),
            "dungeonId": text_field(plan.get("dungeonId"), 160, required=True),
            "characterIds": characters, "steps": clean_steps,
            "displayNotes": text_field(plan.get("displayNotes", ""), 2000),
            "updatedAt": text_field(plan.get("updatedAt"), 80, required=True),
        })
        ids.add(plan_id)
        positions.add(position)
    return {"schemaVersion": payload["schemaVersion"], "accountId": account_id, "revision": revision, "rotations": clean}


class RotationCache:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or _app_data_root() / "rotations" / "cache.json"

    def load(self, account_id: str, api_base_url: str) -> dict | None:
        try:
            if self.path.stat().st_size > 12_000_000:
                return None
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or payload.get("apiBaseUrl") != api_base_url.rstrip("/"):
                return None
            return validate_rotation_envelope(payload, account_id)
        except (OSError, ValueError, TypeError, RecursionError):
            return None

    def save(self, envelope: dict, api_base_url: str) -> None:
        clean = validate_rotation_envelope(envelope, envelope.get("accountId", ""))
        payload = {**clean, "apiBaseUrl": api_base_url.rstrip("/")}
        if len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > 12_000_000:
            raise ValueError("排轴缓存超过本地容量上限")
        _atomic_json(self.path, payload)

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)


class OverlaySettingsStore:
    """Common switches and independent positions for meter and guide."""
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or _app_data_root() / "overlay_settings.json"

    def load(self) -> dict:
        defaults = {"meterEnabled": True, "guideEnabled": True, "opacity": 0.90, "positions": {},
                    "guideScale": 1.0, "guideAnchor": [0.5, 0.13], "guidePositionMode": "game"}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, RecursionError):
            return defaults
        if not isinstance(payload, dict):
            return defaults
        for key in ("meterEnabled", "guideEnabled"):
            if type(payload.get(key)) is bool:
                defaults[key] = payload[key]
        opacity = payload.get("opacity")
        if type(opacity) in (int, float) and 0.35 <= opacity <= 1:
            defaults["opacity"] = float(opacity)
        for key in ("meterOpacity", "guideOpacity"):
            value = payload.get(key)
            if type(value) in (int, float) and 0.35 <= value <= 1:
                defaults[key] = float(value)
        positions = payload.get("positions")
        if isinstance(positions, dict):
            for key in ("meter", "guide"):
                point = positions.get(key)
                if isinstance(point, list) and len(point) == 2 and all(type(v) is int and 0 <= v <= 100_000 for v in point):
                    defaults["positions"][key] = list(point)
        scale = payload.get("guideScale")
        if type(scale) in (int, float) and 0.5 <= scale <= 2:
            defaults["guideScale"] = float(scale)
        anchor = payload.get("guideAnchor")
        if (isinstance(anchor, list) and len(anchor) == 2
                and all(type(value) in (int, float) and 0 <= value <= 1 for value in anchor)):
            defaults["guideAnchor"] = [float(value) for value in anchor]
        mode = payload.get("guidePositionMode")
        if mode in ("game", "absolute"):
            defaults["guidePositionMode"] = mode
        elif (not {"guideScale", "guideAnchor", "guidePositionMode"}.intersection(payload)
              and "guide" in defaults["positions"]):
            # A screen coordinate has no known game-client rectangle. Keep
            # legacy placement explicit rather than inventing an anchor ratio.
            defaults["guidePositionMode"] = "absolute"
        return defaults

    def save(self, settings: dict) -> None:
        _atomic_json(self.path, settings)


class RotationGuideController:
    def __init__(
        self, trace_path: Path, *, cache: RotationCache | None = None,
        session_store: SessionStore | None = None, settings_store: SettingsStore | None = None,
        client_factory=ApiClient, asset_root: Path | None = None,
    ) -> None:
        self.trace_path = trace_path
        self.cache = cache or RotationCache()
        self.session_store = session_store or SessionStore()
        self.settings_store = settings_store or SettingsStore()
        self._client_factory = client_factory
        self._lock = threading.RLock()
        self._clock_spec = load_rotation_clock_spec() if ROTATION_GUIDANCE_ENABLED else None
        self._engine = RotationGuideEngine(clock_spec=self._clock_spec)
        self._catalog: dict | None = None
        self._catalog_path = self.cache.path.with_name("catalog.json")
        self._asset_root = asset_root if asset_root is not None else bundle_root() / "data/public"
        self._asset_signature = None
        self._asset_entries: dict = {}
        self._asset_cache: dict[str, tuple[tuple[int, int, int, int], str]] = {}
        self._envelope: dict | None = None
        self._identity: tuple[str, str] | None = None
        self._context_check_at = 0.0
        self._available = False
        self._stop = threading.Event()
        self._sync_requested = threading.Event()
        self._threads: list[threading.Thread] = []
        self._state = {"message": "请在上传器登录后同步排轴" if ROTATION_GUIDANCE_ENABLED else "排轴与指导条已停用",
                       "offline": False, "planCount": 0}

    def start(self) -> None:
        if not ROTATION_GUIDANCE_ENABLED:
            return
        for target, name in ((self._sync_loop, "rotation-sync"), (self._tail_loop, "rotation-events")):
            thread = threading.Thread(target=target, name=name, daemon=True)
            self._threads.append(thread)
            thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._sync_requested.set()

    def request_sync(self) -> None:
        if not ROTATION_GUIDANCE_ENABLED:
            return
        self._sync_requested.set()

    def set_available(self, available: bool) -> None:
        if not ROTATION_GUIDANCE_ENABLED:
            self._available = False
            return
        with self._lock:
            self._available = bool(available)
            self._engine.set_available(self._available)

    def snapshot(self) -> dict | None:
        if not ROTATION_GUIDANCE_ENABLED:
            return None
        self._reconcile_session()
        with self._lock:
            report = self._engine.snapshot()
            if report is None:
                return None
            # Display-only enrichment of the engine's fresh snapshot. Neither
            # selectors nor the engine's clock/progress state are rewritten.
            catalog_valid = (isinstance(self._catalog, dict) and type(self._catalog.get("schemaVersion")) is int
                             and self._catalog["schemaVersion"] in {1, 2})
            characters = self._catalog.get("characters", []) if catalog_valid else []
            if not isinstance(characters, list) or len(characters) > 500:
                characters = []
            resolved_assets = {}
            seen = set()
            for step in [*report.get("steps", []), report.get("nextStep")]:
                if not isinstance(step, dict) or id(step) in seen:
                    continue
                seen.add(id(step))
                owners = [char for char in characters if isinstance(char, dict) and char.get("id") == step.get("characterId")]
                character = owners[0] if len(owners) == 1 else {}
                skills = character.get("skills", [])
                skills = [skill for skill in skills if isinstance(skill, dict)
                          and isinstance(skill.get("id"), str) and isinstance(skill.get("name"), str)
                          and 0 < len(skill["id"]) <= 160 and 0 < len(skill["name"]) <= 160
                          ] if isinstance(skills, list) and len(skills) <= 1000 else []
                exact = [skill for skill in skills if skill.get("id") == step.get("skillId")]
                known = {skill["id"] for skill in skills if isinstance(skill.get("id"), str)}
                raw_actions = character.get("actions", []) if catalog_valid and self._catalog["schemaVersion"] == 2 else []
                raw_actions = [action for action in raw_actions if isinstance(action, dict)] if isinstance(raw_actions, list) and len(raw_actions) <= 3 else []
                actions = []
                for action in raw_actions:
                    kind, roots = action.get("kind"), action.get("rootSkillIds")
                    if (not isinstance(kind, str) or kind not in {"battle_skill", "combo_skill", "ultimate_skill"}
                            or sum(item.get("kind") == kind for item in raw_actions) != 1
                            or not isinstance(action.get("name"), str) or not 0 < len(action["name"]) <= 160
                            or not isinstance(action.get("skillName"), str) or not 0 < len(action["skillName"]) <= 160
                            or not isinstance(roots, list) or not 1 <= len(roots) <= 1000
                            or not all(isinstance(root, str) and root in known for root in roots)
                            or len(set(roots)) != len(roots)):
                        continue
                    if ("skillId" in step and "actionKind" not in step and len(exact) == 1 and step["skillId"] in roots
                            or "actionKind" in step and "skillId" not in step and kind == step["actionKind"]):
                        actions.append(action)
                action = actions[0] if len(actions) == 1 else {}
                legacy = "skillId" in step and "actionKind" not in step
                ordinary = "actionKind" in step and "skillId" not in step
                old_name = step.get("skillName")
                if not isinstance(old_name, str) or old_name in ("", "未识别技能"):
                    old_name = exact[0].get("name") if len(exact) == 1 else None
                step["skillName"] = ((old_name or "未识别技能") if legacy else
                    (old_name or action.get("skillName") or "未识别技能") if ordinary else "未识别技能")
                step["actionName"] = action.get("name") or ("原有技能" if legacy else "未识别动作")
                if not step.get("characterName") or step.get("characterName") == "未识别角色":
                    name = character.get("name")
                    step["characterName"] = name if isinstance(name, str) and 0 < len(name) <= 160 else "未识别角色"
                step["iconUrl"] = action.get("iconUrl") or ""
                step["avatarUrl"] = character.get("avatarUrl") or ""
                for url_key, path_key in (("iconUrl", "iconPath"), ("avatarUrl", "avatarPath")):
                    url = step[url_key]
                    if not isinstance(url, str) or len(url) > 2048:
                        url = ""
                    step[url_key] = url
                    if url not in resolved_assets:
                        resolved_assets[url] = self._asset_path(url)
                    step[path_key] = resolved_assets[url]
            return report

    def _asset_path(self, url: str) -> str:
        """Resolve an exact official URL via the bundled PNG/hash manifest only."""
        if not url.startswith(("/images/character/game-skills/", "/images/character/game-avatars/")):
            return ""
        manifest = self._asset_root / "rotation-assets.json"
        try:
            info = manifest.stat()
            signature = (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size) if info.st_size <= 2_000_000 else None
        except OSError:
            signature = None
        if signature != self._asset_signature:
            self._asset_signature = signature
            self._asset_entries = {}
            self._asset_cache.clear()
            if signature is not None:
                try:
                    payload = json.loads(manifest.read_text("utf-8"))
                    if (isinstance(payload, dict) and type(payload.get("schemaVersion")) is int
                            and payload["schemaVersion"] == 1 and isinstance(payload.get("images"), dict)
                            and len(payload["images"]) <= 2000):
                        self._asset_entries = payload["images"]
                except (OSError, ValueError, RecursionError):
                    pass
        entry = self._asset_entries.get(url)
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str) or not isinstance(entry.get("sha256"), str):
            return ""
        relative = PurePosixPath(entry["path"])
        digest = entry["sha256"].lower()
        if (relative.is_absolute() or not relative.parts or ".." in relative.parts
                or any("\\" in part or ":" in part for part in relative.parts)
                or relative.suffix.lower() != ".png" or re.fullmatch(r"[0-9a-f]{64}", digest) is None):
            return ""
        try:
            root = (self._asset_root / "rotation-assets").resolve()
            path = (root / relative).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                return ""
            info = path.stat()
            if not 0 < info.st_size <= 10_000_000:
                return ""
            signature = (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size)
            cached = self._asset_cache.get(url)
            if cached is not None and cached[0] == signature:
                return cached[1]
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                return ""
            self._asset_cache[url] = (signature, str(path))
            return str(path)
        except (OSError, ValueError):
            return ""

    def sync_state(self) -> dict:
        if not ROTATION_GUIDANCE_ENABLED:
            return {"message": "排轴与指导条已停用", "offline": False, "planCount": 0,
                    "clockSupported": False, "disabled": True}
        with self._lock:
            return {**self._state, "clockSupported": self._clock_spec is not None}

    def _session_context(self) -> tuple[tuple[str, str], str] | None:
        if not ROTATION_GUIDANCE_ENABLED:
            return None
        try:
            session = self.session_store.load_session()
            settings = self.settings_store.load_settings()
            if not isinstance(session, dict) or not session.get("userId") or not session.get("sessionToken"):
                return None
            return ((str(session["userId"]), settings.api_base_url.rstrip("/")), str(session["sessionToken"]))
        except (OSError, ValueError, TypeError, AttributeError, RecursionError):
            return None

    def _install(self, envelope: dict | None, message: str, *, offline: bool = False) -> None:
        if not ROTATION_GUIDANCE_ENABLED:
            return
        with self._lock:
            if envelope is not None and (self._identity is None or envelope.get("accountId") != self._identity[0]):
                return
            if envelope != self._envelope:
                self._envelope = copy.deepcopy(envelope)
                self._engine.replace_plans(envelope or {"schemaVersion": 1, "accountId": "", "rotations": []})
            self._state = {"message": message, "offline": offline, "planCount": len((envelope or {}).get("rotations", []))}

    def _reconcile_session(self, *, force: bool = False) -> tuple[tuple[str, str], str] | None:
        if not ROTATION_GUIDANCE_ENABLED:
            return None
        now = time.monotonic()
        with self._lock:
            if not force and now < self._context_check_at:
                return None
            self._context_check_at = now + 1
        context = self._session_context()
        identity = context[0] if context else None
        with self._lock:
            if identity != self._identity:
                cached = self.cache.load(*identity) if identity else None
                if cached is None:
                    try:
                        self.cache.clear()
                    except OSError:
                        pass
                self._identity = identity
                self._catalog = None
                if identity:
                    try:
                        if self._catalog_path.stat().st_size <= 2_000_000:
                            saved_catalog = json.loads(self._catalog_path.read_text(encoding="utf-8"))
                            if isinstance(saved_catalog, dict) and saved_catalog.get("apiBaseUrl") == identity[1]:
                                self._catalog = saved_catalog.get("catalog")
                    except (OSError, ValueError, RecursionError):
                        pass
                self._engine.replace_catalog(self._catalog)
                self._install(cached, f"本地缓存：{len(cached['rotations'])} 套排轴" if cached else "请在上传器登录后同步排轴")
                self._sync_requested.set()
        return context

    def _sync_once(self, context: tuple[tuple[str, str], str]) -> None:
        if not ROTATION_GUIDANCE_ENABLED:
            return
        identity, token = context
        client = self._client_factory(identity[1])
        try:
            client.set_session_token(token)
            clean = validate_rotation_envelope(client.get_rotations(), identity[0])
            # A response from a former session must never re-install its plans.
            with self._lock:
                if self._stop.is_set() or self._session_context() != context or self._identity != identity:
                    return
                self.cache.save(clean, identity[1])
                self._install(clean, f"已同步 {len(clean['rotations'])} 套排轴")
            if hasattr(client, "get_rotation_catalog"):
                try:
                    catalog = client.get_rotation_catalog()
                    if (not isinstance(catalog, dict) or type(catalog.get("schemaVersion")) is not int
                            or catalog["schemaVersion"] not in {1, 2} or not isinstance(catalog.get("characters"), list)
                            or not isinstance(catalog.get("maps"), list) or len(catalog["characters"]) > 500
                            or len(catalog["maps"]) > 1000
                            or len(json.dumps(catalog, ensure_ascii=False).encode("utf-8")) > 2_000_000):
                        raise ValueError("技能目录无效")
                    with self._lock:
                        if not self._stop.is_set() and self._session_context() == context and self._identity == identity:
                            _atomic_json(self._catalog_path, {"apiBaseUrl": identity[1], "catalog": catalog})
                            self._catalog = catalog
                            self._engine.replace_catalog(catalog)
                except (ApiClientError, OSError, ValueError, TypeError, RecursionError):
                    pass  # Legacy exact roots remain valid; new actions require a cached catalog.
        except ApiClientError as exc:
            with self._lock:
                if self._stop.is_set() or self._session_context() != context or self._identity != identity:
                    return
                if exc.status_code in (401, 403):
                    self._install(None, "登录已失效，请在上传器重新登录")
                    try:
                        self.cache.clear()
                    except OSError:
                        pass
                else:
                    count = self._state["planCount"]
                    self._install(self._envelope, f"离线缓存：{count} 套排轴" if self._envelope else "暂时无法同步，尚无本地排轴", offline=True)
        except (OSError, ValueError, TypeError):
            with self._lock:
                if not self._stop.is_set() and self._session_context() == context and self._identity == identity:
                    self._install(self._envelope, "同步结果不可用，保留本地排轴", offline=True)
        finally:
            client.client.close()

    def _sync_loop(self) -> None:
        next_sync = 0.0
        last_token: str | None = None
        while ROTATION_GUIDANCE_ENABLED and not self._stop.is_set():
            context = self._reconcile_session(force=True)
            token = context[1] if context else None
            if token != last_token:
                next_sync = 0
                last_token = token
            if context and (time.monotonic() >= next_sync or self._sync_requested.is_set()):
                self._sync_requested.clear()
                try:
                    self._sync_once(context)
                except Exception:
                    # Worker remains alive if a response or filesystem is unavailable.
                    self._install(self._envelope, "暂时无法同步排轴", offline=True)
                next_sync = time.monotonic() + 60
            self._stop.wait(1)

    def _tail_loop(self) -> None:
        position = 0
        file_identity = None
        pending = b""
        while ROTATION_GUIDANCE_ENABLED and not self._stop.is_set():
            self._reconcile_session()
            try:
                stat = self.trace_path.stat()
                current_identity = (stat.st_dev, stat.st_ino)
                if (file_identity is not None and current_identity != file_identity) or stat.st_size < position:
                    position = 0
                    pending = b""
                    with self._lock:
                        self._engine = RotationGuideEngine(clock_spec=self._clock_spec)
                        self._engine.replace_catalog(self._catalog)
                        self._engine.replace_plans(self._envelope or {"schemaVersion": 1, "accountId": "", "rotations": []})
                        self._engine.set_available(self._available)
                file_identity = current_identity
                if stat.st_size > position:
                    with self.trace_path.open("rb") as stream:
                        stream.seek(position)
                        raw = stream.read(256 * 1024)
                        position = stream.tell()
                    pieces = (pending + raw).split(b"\n")
                    pending = pieces.pop()
                    # Bound malformed partial lines without inventing an event.
                    if len(pending) > 256 * 1024:
                        pending = b""
                        self.set_available(False)
                    with self._lock:
                        for line in pieces:
                            self._engine.feed_line(line.decode("utf-8", errors="replace"))
                    if stat.st_size > position:
                        continue
            except OSError:
                self.set_available(False)
            self._stop.wait(0.05)
