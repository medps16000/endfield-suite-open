"""Bounded, redacted diagnostics owned by the native overlay host."""
from __future__ import annotations

from collections import OrderedDict, deque
import json
from pathlib import Path
import re
import threading
import time

MAX_WINDOWS = 24
MAX_ROWS = 100
MAX_LOG_BYTES = 512 * 1024
MAX_EVENTS_PER_MINUTE = 30


def safe_text(value: object, limit: int = 320) -> str:
    """Do not persist full URLs, local paths, credentials or long opaque values."""
    text = str(value)[:4096]
    text = re.sub(r"(?:https?|file|endfield-overlay|wss?)://[^\s<>\"']+", "[地址已隐藏]", text, flags=re.I)
    text = re.sub(r"(?i)(?<![\w])(?P<keyquote>[\"']?)(?:token|password|passwd|secret|api[_-]?key|authorization|cookie|session[_-]?id)(?P=keyquote)\s*[:=]\s*(?:\"(?:\\.|[^\"\\])*(?:\"|$)|'(?:\\.|[^'\\])*(?:'|$)|[^\s,;}\]]+)", "[凭据已隐藏]", text)
    text = re.sub(r"(?i)\b(?:bearer|basic)\s+\S+", "[凭据已隐藏]", text)
    text = re.sub(r"(?:[A-Za-z]:[\\/]|\\\\)[^\r\n<>\"']+", "[路径已隐藏]", text)
    text = re.sub(r"(?<![\w])/(?:[^\s/<>\"']+/)+[^\s<>\"']*", "[路径已隐藏]", text)
    text = re.sub(r"\b[A-Za-z0-9_.+%-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", "[邮箱已隐藏]", text)
    text = re.sub(r"[A-Za-z0-9_+=/-]{24,}", "[长标识已隐藏]", text)
    return " ".join(text.split())[:limit]


class OverlayDiagnostics:
    def __init__(self, root: Path):
        self.path = Path(root) / "overlay-windows.jsonl"
        self._rows = OrderedDict()
        self._times = {}
        self._dropped = {}
        self._lock = threading.Lock()

    def record(self, identifier: str, code: str, message: str, *, level: str = "error", line: int = 0) -> None:
        now = time.monotonic()
        identifier = str(identifier)
        identifier = identifier if re.fullmatch(r"[A-Za-z0-9_-]{1,80}", identifier) else "unknown"
        with self._lock:
            if identifier not in self._rows:
                if len(self._rows) >= MAX_WINDOWS:
                    oldest, _ = self._rows.popitem(last=False)
                    self._times.pop(oldest, None)
                    self._dropped.pop(oldest, None)
                self._rows[identifier] = deque(maxlen=MAX_ROWS)
            stamps = self._times.setdefault(identifier, deque())
            while stamps and now - stamps[0] >= 60:
                stamps.popleft()
            if code == "javascript" and len(stamps) >= MAX_EVENTS_PER_MINUTE:
                self._dropped[identifier] = self._dropped.get(identifier, 0) + 1
                return
            if code == "javascript":
                stamps.append(now)
            row = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "windowId": identifier,
                   "level": safe_text(level, 16), "code": safe_text(code, 64),
                   "message": safe_text(message), "line": max(0, min(int(line), 10000000))}
            self._rows[identifier].append(row)
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                encoded = (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
                if self.path.exists() and self.path.stat().st_size + len(encoded) > MAX_LOG_BYTES:
                    self.path.replace(self.path.with_suffix(".previous.jsonl"))
                with self.path.open("ab") as stream:
                    stream.write(encoded)
            except OSError:
                # A diagnostic I/O failure must never stop capture, the UI or
                # recovery. The in-memory bounded rows remain available.
                pass

    def forget(self, identifier: str) -> None:
        with self._lock:
            self._rows.pop(identifier, None)
            self._times.pop(identifier, None)
            self._dropped.pop(identifier, None)

    def text(self, identifier: str) -> str:
        with self._lock:
            rows = list(self._rows.get(identifier, ()))
            dropped = self._dropped.get(identifier, 0)
        lines = [f"{row['at']} [{row['level']}] {row['code']}" +
                 (f" 第 {row['line']} 行" if row['line'] else "") + f"：{row['message']}" for row in rows]
        if dropped:
            lines.append(f"本窗口另有 {dropped} 条高频诊断未记录。")
        return "\n".join(lines) if lines else "本次运行尚无窗口诊断记录。"
