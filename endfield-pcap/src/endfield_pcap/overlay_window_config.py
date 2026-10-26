"""Validated, independent settings for the optional web overlay windows."""
from __future__ import annotations

import copy
import json
import math
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

MAX_WINDOWS = 24


def validate_source(source_type: str, source_value: str, *, must_exist: bool = True) -> tuple[str, str]:
    if source_type not in {"file", "url"}:
        raise ValueError("来源必须是本地 HTML 或 HTTPS 页面。")
    if not isinstance(source_value, str) or not source_value.strip() or len(source_value) > 4096:
        raise ValueError("请填写有效页面入口。")
    value = source_value.strip()
    if source_type == "file":
        path = Path(value).expanduser().resolve(strict=must_exist)
        if path.suffix.lower() not in {".html", ".htm"} or (must_exist and not path.is_file()):
            raise ValueError("请选择现有 HTML 文件。")
        return source_type, str(path)
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("网页端口无效。") from exc
    if parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username or parsed.password or port not in {None, 443}:
        raise ValueError("网页仅支持不含账号信息、使用标准端口的公网 HTTPS 地址。")
    host = parsed.hostname.encode("idna").decode("ascii").lower()
    if ":" in host:
        host = f"[{host}]"
    return source_type, urlunsplit(("https", host, parsed.path or "/", parsed.query, ""))


def normalize_entry(value: dict, *, must_exist: bool = False) -> dict:
    if not isinstance(value, dict):
        raise ValueError("窗口配置无效。")
    identifier = str(value.get("id", ""))
    if not identifier or len(identifier) > 64 or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in identifier):
        raise ValueError("窗口标识无效。")
    name = value.get("name", "悬浮窗")
    if not isinstance(name, str) or not name.strip() or len(name) > 80:
        raise ValueError("窗口名称应为 1 至 80 个字符。")
    source_type, source_value = validate_source(value.get("sourceType"), value.get("sourceValue"), must_exist=must_exist)
    geometry = value.get("geometry", {})
    if not isinstance(geometry, dict):
        raise ValueError("窗口尺寸无效。")
    dimensions = {}
    for key, default, minimum, maximum in (("x", 100, -100000, 100000), ("y", 100, -100000, 100000), ("width", 440, 160, 4096), ("height", 320, 100, 4096)):
        number = geometry.get(key, default)
        if isinstance(number, bool) or not isinstance(number, (float, int)) or not math.isfinite(number):
            raise ValueError("窗口坐标与尺寸必须是有效数字。")
        dimensions[key] = max(minimum, min(maximum, round(number)))
    opacity = value.get("opacity", 1.0)
    if isinstance(opacity, bool) or not isinstance(opacity, (int, float)) or not math.isfinite(opacity):
        raise ValueError("透明度无效。")
    result = {"id": identifier, "name": name.strip(), "sourceType": source_type, "sourceValue": source_value, "geometry": dimensions, "opacity": max(0.2, min(1.0, float(opacity)))}
    for key, default in (("enabled", True), ("visible", True), ("locked", False), ("clickThrough", False)):
        if key in value and not isinstance(value[key], bool):
            raise ValueError("窗口开关必须为布尔值。")
        result[key] = value.get(key, default)
    return result


class OverlayWindowConfig:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.entries: list[dict] = []
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(data, dict) or data.get("schemaVersion") != 1 or not isinstance(data.get("windows"), list):
                    raise ValueError("配置格式无效。")
                if len(data["windows"]) > MAX_WINDOWS:
                    raise ValueError("窗口数量超出限制。")
                self.entries = [normalize_entry(row) for row in data["windows"]]
                if len({row["id"] for row in self.entries}) != len(self.entries):
                    raise ValueError("窗口标识重复。")
            except (ValueError, TypeError, KeyError, OSError) as exc:
                raise ValueError(f"悬浮窗配置无法读取，已保留原文件：{exc}") from exc

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        try:
            temporary.write_text(json.dumps({"schemaVersion": 1, "windows": self.entries}, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self.path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def list_entries(self) -> list[dict]:
        return copy.deepcopy(self.entries)

    def get_entry(self, identifier: str) -> dict:
        for entry in self.entries:
            if entry["id"] == identifier:
                return copy.deepcopy(entry)
        raise ValueError("未找到此悬浮窗。")

    def add_entry(self, name: str, source_type: str, source_value: str) -> dict:
        if len(self.entries) >= MAX_WINDOWS:
            raise ValueError(f"最多创建 {MAX_WINDOWS} 个独立窗口。")
        entry = normalize_entry({"id": uuid.uuid4().hex, "name": name, "sourceType": source_type, "sourceValue": source_value}, must_exist=True)
        self.entries.append(entry)
        try:
            self.save()
        except OSError:
            self.entries.pop()
            raise
        return copy.deepcopy(entry)

    def update_entry(self, identifier: str, **patch) -> dict:
        allowed = {"name", "sourceType", "sourceValue", "enabled", "visible", "locked", "clickThrough", "opacity", "geometry"}
        if set(patch) - allowed:
            raise ValueError("包含不支持的窗口设置。")
        current = self.get_entry(identifier)
        entry = normalize_entry({**current, **patch}, must_exist=bool({"sourceType", "sourceValue"} & set(patch)))
        index = next(i for i, row in enumerate(self.entries) if row["id"] == identifier)
        self.entries[index] = entry
        try:
            self.save()
        except OSError:
            self.entries[index] = current
            raise
        return copy.deepcopy(entry)

    def remove_entry(self, identifier: str):
        current = self.get_entry(identifier)
        index = next(i for i, row in enumerate(self.entries) if row["id"] == identifier)
        self.entries.pop(index)
        try:
            self.save()
        except OSError:
            self.entries.insert(index, current)
            raise
