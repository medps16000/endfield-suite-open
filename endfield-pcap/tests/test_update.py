from __future__ import annotations

import hashlib
import json
from pathlib import Path
import zipfile

from endfield_pcap.update import (
    _download_file,
    _fetch_json,
    _format_bytes,
    _is_remote_newer,
    _prepare_updater,
    _select_patch_manifest,
    _version_key,
)


def _load_updater_module():
    import importlib.util

    updater_path = Path(__file__).resolve().parents[1] / "updater.py"
    spec = importlib.util.spec_from_file_location("endfield_test_updater", updater_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_version_key_parses_common_release_strings() -> None:
    assert _version_key("2.10.1") == (2, 10, 1)
    assert _version_key("20260508") == (20260508,)
    assert _version_key("v2.10.0-beta") == (2, 10, 0)


def test_remote_update_compares_version_before_build() -> None:
    assert _is_remote_newer("2.10.0", "20260508", {"version": "2.10.1", "build": "20260501"})
    assert not _is_remote_newer("2.10.1", "20260508", {"version": "2.10.0", "build": "20260509"})
    assert _is_remote_newer("2.10.0", "20260508", {"version": "2.10.0", "build": "20260509"})
    assert not _is_remote_newer("2.10.0", "20260508", {"version": "2.10.0", "build": "20260508"})
    assert _is_remote_newer("2.10.90", "20260908", {"version": "3.0.1", "build": "20260914"})
    assert _is_remote_newer("2.10.94-local", "20260914", {"version": "3.0.1", "build": "20260914"})
    assert not _is_remote_newer("3.0.1", "20260914", {"version": "2.10.90", "build": "20260930"})
    assert not _is_remote_newer("3.0.1", "20260914", {"version": "3.0.1", "build": "20260914"})


def test_select_patch_manifest_uses_compatible_local_version_and_build() -> None:
    manifest = {
        "url": "https://example.test/full.zip",
        "sha256": "f" * 64,
        "patches": [
            {
                "fromVersion": "2.10.37",
                "fromBuild": "20260604",
                "url": "https://example.test/old.patch.zip",
                "sha256": "a" * 64,
            },
            {
                "fromVersion": "2.10.38",
                "fromBuild": "20260605",
                "url": "https://example.test/current.patch.zip",
                "sha256": "b" * 64,
            },
        ],
    }

    patch = _select_patch_manifest(manifest, local_version="2.10.38", local_build="20260605")

    assert patch is not None
    assert patch["url"] == "https://example.test/current.patch.zip"


def test_select_patch_manifest_ignores_incompatible_or_incomplete_patches() -> None:
    manifest = {
        "patches": [
            {
                "fromVersion": "2.10.37",
                "fromBuild": "20260604",
                "url": "https://example.test/old.patch.zip",
                "sha256": "a" * 64,
            },
            {
                "fromVersion": "2.10.38",
                "fromBuild": "20260605",
                "url": "",
                "sha256": "b" * 64,
            },
        ],
    }

    assert _select_patch_manifest(manifest, local_version="2.10.38", local_build="20260605") is None


def test_download_file_reports_progress(monkeypatch, tmp_path: Path) -> None:
    class FakeResponse:
        headers = {"Content-Length": "6"}

        def __init__(self) -> None:
            self._chunks = [b"ab", b"cdef", b""]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, size: int) -> bytes:
            return self._chunks.pop(0)

    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: FakeResponse())

    progress: list[tuple[int, int | None]] = []
    target = tmp_path / "client.zip"

    _download_file(
        "https://example.test/client.zip",
        target,
        progress=lambda downloaded, total: progress.append((downloaded, total)),
    )

    assert target.read_bytes() == b"abcdef"
    assert progress == [(0, 6), (2, 6), (6, 6)]


def test_fetch_json_busts_manifest_cache(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, size: int) -> bytes:
            return b'{"version":"2.10.44"}'

    def fake_urlopen(request, **kwargs):
        captured["url"] = request.full_url
        captured["cache_control"] = request.headers.get("Cache-control")
        captured["pragma"] = request.headers.get("Pragma")
        return FakeResponse()

    monkeypatch.setattr("endfield_pcap.update.time.time", lambda: 123.456)
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    assert _fetch_json("https://example.test/client/latest.json?channel=stable") == {"version": "2.10.44"}
    assert captured["url"] == "https://example.test/client/latest.json?channel=stable&_=123456"
    assert captured["cache_control"] == "no-cache"
    assert captured["pragma"] == "no-cache"


def test_download_file_retries_after_transient_failure(monkeypatch, tmp_path: Path) -> None:
    class FakeResponse:
        headers = {"Content-Length": "2"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, size: int) -> bytes:
            if getattr(self, "_done", False):
                return b""
            self._done = True
            return b"ok"

    calls = {"count": 0}

    def fake_urlopen(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise OSError("temporary EOF")
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("endfield_pcap.update.time.sleep", lambda delay: None)

    target = tmp_path / "client.zip"
    _download_file("https://example.test/client.zip", target)

    assert calls["count"] == 2
    assert target.read_bytes() == b"ok"


def test_format_bytes_uses_human_units() -> None:
    assert _format_bytes(42) == "42 B"
    assert _format_bytes(1536) == "1.5 KB"
    assert _format_bytes(2 * 1024 * 1024) == "2.0 MB"


def test_prepare_updater_uses_fresh_cache_dir_when_stale_dir_exists(monkeypatch, tmp_path: Path) -> None:
    source_dir = tmp_path / "source" / "updater" / "EndfieldLogsUpdater"
    source_dir.mkdir(parents=True)
    (source_dir / "EndfieldLogsUpdater.exe").write_text("updater", encoding="utf-8")

    updates_root = tmp_path / "updates"
    stale_dir = updates_root / "updater" / "EndfieldLogsUpdater"
    stale_dir.mkdir(parents=True)
    (stale_dir / "old.txt").write_text("old", encoding="utf-8")

    def fail_remove(path: Path) -> None:
        if Path(path) == stale_dir:
            raise PermissionError("locked")

    monkeypatch.setattr("endfield_pcap.update._updater_source_dir", lambda: source_dir)
    monkeypatch.setattr("endfield_pcap.update._updates_root", lambda: updates_root)
    monkeypatch.setattr("endfield_pcap.update.shutil.rmtree", fail_remove)
    monkeypatch.setattr("endfield_pcap.update.time.time", lambda: 123.456)

    updater_exe = _prepare_updater()

    assert updater_exe is not None
    assert updater_exe.exists()
    assert updater_exe.parent.name.startswith("EndfieldLogsUpdater-")
    assert updater_exe.parent != stale_dir
    assert stale_dir.exists()


def test_updater_finds_nested_package_root(tmp_path: Path) -> None:
    module = _load_updater_module()

    package_root = tmp_path / "EndfieldLogsClient"
    package_root.mkdir()
    (package_root / "EndfieldLogsClient.exe").write_text("", encoding="utf-8")

    assert module._find_package_root(tmp_path) == package_root


def test_updater_applies_partial_patch_package(monkeypatch, tmp_path: Path) -> None:
    module = _load_updater_module()
    monkeypatch.setattr(module, "_terminate_processes_under", lambda path: None)

    target = tmp_path / "EndfieldLogsClient"
    target.mkdir()
    (target / "version.json").write_text('{"version":"2.10.38"}', encoding="utf-8")
    (target / "remove.txt").write_text("remove me", encoding="utf-8")
    (target / "unchanged.txt").write_text("keep me", encoding="utf-8")

    files = {
        "version.json": b'{"version":"2.10.39"}\n',
        "data/packet_semantics/buff_numeric_map.json": b'{"version":1,"mappings":{"3057":{}}}\n',
    }
    patch_manifest = {
        "kind": "endfield_partial_update",
        "version": "2.10.39",
        "build": "20260605",
        "files": [
            {
                "path": path,
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
            }
            for path, data in files.items()
        ],
        "delete": ["remove.txt"],
    }
    patch_path = tmp_path / "client.patch.zip"
    with zipfile.ZipFile(patch_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("patch_manifest.json", json.dumps(patch_manifest, ensure_ascii=False))
        for path, data in files.items():
            archive.writestr(f"payload/{path}", data)

    module._apply_patch_package(patch_path, target, tmp_path / "work")

    assert (target / "version.json").read_text(encoding="utf-8") == '{"version":"2.10.39"}\n'
    assert json.loads((target / "data" / "packet_semantics" / "buff_numeric_map.json").read_text(encoding="utf-8"))[
        "mappings"
    ] == {"3057": {}}
    assert not (target / "remove.txt").exists()
    assert (target / "unchanged.txt").read_text(encoding="utf-8") == "keep me"


def test_updater_main_accepts_patch_argument_without_package_path(monkeypatch, tmp_path: Path) -> None:
    module = _load_updater_module()
    target = tmp_path / "EndfieldLogsClient"
    target.mkdir()
    patch_path = tmp_path / "client.patch.zip"
    patch_path.write_bytes(b"patch")
    applied: list[tuple[Path, Path]] = []
    messages: list[tuple[str, str, bool]] = []

    class FakeProgress:
        def update(self, *args, **kwargs) -> None:
            return None

        def close(self) -> None:
            return None

    monkeypatch.setattr(module, "_ApplyProgressDialog", FakeProgress)
    monkeypatch.setattr(module, "_wait_for_pid", lambda pid: None)
    monkeypatch.setattr(module, "_appdata_root", lambda: tmp_path / "appdata")
    monkeypatch.setattr(module, "_apply_patch_package", lambda patch, install, work, progress: applied.append((patch, install)))
    monkeypatch.setattr(module, "_start_client", lambda install, exe_name: None)
    monkeypatch.setattr(module, "_show_message", lambda title, message, error=False: messages.append((title, message, error)))
    monkeypatch.setattr(
        module.sys,
        "argv",
        [
            "EndfieldLogsUpdater",
            "--patch",
            str(patch_path),
            "--target-dir",
            str(target),
            "--exe-name",
            "EndfieldLogsClient.exe",
        ],
    )

    assert module.main() == 0
    assert applied == [(patch_path.resolve(), target.resolve())]
    assert messages == [("更新完成", "客户端已更新完成并重新启动。", False)]


def test_updater_process_termination_is_path_scoped(monkeypatch, tmp_path: Path) -> None:
    module = _load_updater_module()
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command: list[str], **kwargs: object) -> None:
        calls.append((command, kwargs))

    monkeypatch.setattr(module.os, "name", "nt")
    monkeypatch.setattr(module, "_subprocess_creationflags", lambda: 0)
    monkeypatch.setattr(module.subprocess, "run", fake_run)

    module._terminate_processes_under(tmp_path / "EndfieldLogsClient")

    assert len(calls) == 1
    command_text = "\n".join(str(part) for part in calls[0][0])
    assert "taskkill" not in command_text.casefold()
    assert "Test-UnderTarget" in command_text
    assert "ExecutablePath" in command_text
    assert "StartsWith($targetPrefix" in command_text


def test_rename_retry_stays_path_scoped(monkeypatch, tmp_path: Path) -> None:
    module = _load_updater_module()
    scoped_terminations: list[object] = []

    class LockedOnce:
        def __init__(self) -> None:
            self.rename_calls = 0

        def rename(self, target: Path) -> None:
            self.rename_calls += 1
            if self.rename_calls == 1:
                raise PermissionError("locked")

    source = LockedOnce()
    monkeypatch.setattr(module, "_terminate_processes_under", lambda path: scoped_terminations.append(path))
    monkeypatch.setattr(module, "_log", lambda message: None)
    monkeypatch.setattr(module.time, "sleep", lambda seconds: None)

    module._rename_with_retries(source, tmp_path / "EndfieldLogsClient.old", attempts=2, delay_sec=0)

    assert source.rename_calls == 2
    assert scoped_terminations == [source]
