from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
import sys


_LOCAL_API_BASE_URL = "http://127.0.0.1:8000"
_LOCAL_WEB_BASE_URL = "http://127.0.0.1:3000"
_PUBLIC_BASE_URL = os.environ.get("ENDFIELD_LOGS_PUBLIC_BASE_URL") or "https://zmdlogs.com"
_LEGACY_PUBLIC_BASE_URLS = {"http://192.0.2.10", "https://192.0.2.10", "http://zmdlogs.com"}


def _is_frozen_build() -> bool:
    return bool(getattr(sys, "frozen", False) or getattr(sys, "_MEIPASS", None))


def _default_api_base_url() -> str:
    return _PUBLIC_BASE_URL if _is_frozen_build() else _LOCAL_API_BASE_URL


def _default_web_base_url() -> str:
    return _PUBLIC_BASE_URL if _is_frozen_build() else _LOCAL_WEB_BASE_URL


@dataclass
class UploaderSettings:
    api_base_url: str = field(default_factory=_default_api_base_url)
    web_base_url: str = field(default_factory=_default_web_base_url)
    last_log_dir: str = ""


class SettingsStore:
    def __init__(self) -> None:
        appdata_root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".endfield-pcap")
        self._settings_path = appdata_root / "EndfieldPCAP" / "uploader_settings.json"
        self._legacy_settings_path = appdata_root / "EndfieldLogsUploader" / "settings.json"
        self._migrate_legacy_settings()

    def _migrate_legacy_settings(self) -> None:
        if self._settings_path.exists() or not self._legacy_settings_path.exists():
            return
        try:
            self._settings_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self._legacy_settings_path, self._settings_path)
        except OSError:
            pass

    def load_settings(self) -> UploaderSettings:
        if not self._settings_path.exists():
            return UploaderSettings()
        try:
            payload = json.loads(self._settings_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return UploaderSettings()

        api_base_url = str(payload.get("apiBaseUrl") or _default_api_base_url())
        web_base_url = str(payload.get("webBaseUrl") or _default_web_base_url())

        # Packaged builds should default to the public deployment. When a machine
        # only has the legacy localhost defaults saved from local development,
        # treat them as implicit defaults and promote them to production URLs.
        if _is_frozen_build():
            if api_base_url == _LOCAL_API_BASE_URL and web_base_url == _LOCAL_WEB_BASE_URL:
                api_base_url = _PUBLIC_BASE_URL
                web_base_url = _PUBLIC_BASE_URL
            elif api_base_url in _LEGACY_PUBLIC_BASE_URLS and web_base_url in _LEGACY_PUBLIC_BASE_URLS:
                api_base_url = _PUBLIC_BASE_URL
                web_base_url = _PUBLIC_BASE_URL

        return UploaderSettings(
            api_base_url=api_base_url,
            web_base_url=web_base_url,
            last_log_dir=str(payload.get("lastLogDir") or ""),
        )

    def save_settings(self, settings: UploaderSettings) -> None:
        self._settings_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "apiBaseUrl": settings.api_base_url,
            "webBaseUrl": settings.web_base_url,
            "lastLogDir": settings.last_log_dir,
        }
        self._settings_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
