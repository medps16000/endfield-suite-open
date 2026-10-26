from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import http.server
import threading
import zipfile

import pytest

from endfield_pcap import component_updates as components
from endfield_pcap import update


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _bundle(root, version="3.0.13-test.3", build="20261026", feature=b"client", data=b"official"):
    files = {"EndfieldLogsClient.exe": feature, "_internal/version.json": json.dumps({"version": version, "build": build}).encode(),
             "_internal/PySide6/Qt6WebEngineCore.dll": b"same-native-runtime", "_internal/base_library.zip": b"same-python",
             "_internal/data/local_tables/NumIdStrTable.json": data, "_internal/src/endfield_pcap/proto_generated/SAMPLE_pb2.py": b"protocol-source"}
    for relative, content in files.items():
        path = root / relative; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(content)
    return root


def _release(bundle, out, version="3.0.13-test.3", build="20261026", abi="cp314-win_amd64-qt611"):
    return components.build_component_release(bundle, out, "test-release", version, build, abi, "https://example.test/components")


def _remote(tmp_path, *, abi="cp314-win_amd64-qt611", data=b"official"):
    local = _bundle(tmp_path / "installed", version="3.0.12", build="20261016")
    _release(local, tmp_path / "old-components", version="3.0.12", build="20261016")
    candidate = _bundle(tmp_path / "candidate", feature=b"new client", data=data)
    out = tmp_path / "remote"
    descriptor = _release(candidate, out, abi=abi)
    requested = []
    def fetch(artifact):
        name = artifact["url"].rsplit("/", 1)[-1]; requested.append(name)
        return out / name
    return local, candidate, out, descriptor, requested, fetch


def _prepare(tmp_path, local, descriptor, fetch):
    return components.prepare_component_package(descriptor, local, tmp_path / "cache", fetch,
                                                expected_version="3.0.13-test.3", expected_build="20261026")


def _snapshot(root):
    return {path.relative_to(root).as_posix(): _hash(path) for path in root.rglob("*") if path.is_file()}


def _updater():
    path = Path(__file__).resolve().parents[1] / "updater.py"
    spec = importlib.util.spec_from_file_location("component_update_test_updater", path)
    result = importlib.util.module_from_spec(spec); spec.loader.exec_module(result)
    return result


def test_changed_client_reuses_actual_runtime_and_data_then_assembles_exact_full_tree(tmp_path):
    local, candidate, out, descriptor, requested, fetch = _remote(tmp_path)
    before = _snapshot(local)
    package, sha, report = _prepare(tmp_path, local, descriptor, fetch)
    assert sha == _hash(package)
    assert report == {"reused": ["runtime", "game-data"], "downloaded": ["client"], "repaired": [],
                      "version": "3.0.13-test.3", "build": "20261026"}
    assert len(requested) == 2 and "component-client" in requested[1]
    assert _snapshot(local) == before
    extracted = tmp_path / "extracted"
    with zipfile.ZipFile(package) as archive: archive.extractall(extracted)
    actual = extracted / "EndfieldLogsClient"
    components.verify_installation(actual)
    assert _snapshot(actual) == _snapshot(candidate)


@pytest.mark.parametrize("damage", ["modified", "missing"])
def test_unchanged_component_is_downloaded_when_actual_baseline_is_modified_or_missing(tmp_path, damage):
    local, _, _, descriptor, requested, fetch = _remote(tmp_path)
    native = local / "_internal/PySide6/Qt6WebEngineCore.dll"
    if damage == "modified": native.write_bytes(b"same-length-bad-binary")
    else: native.unlink()
    before = _snapshot(local)
    package, _, report = _prepare(tmp_path, local, descriptor, fetch)
    assert report["repaired"] == ["runtime"]
    assert report["downloaded"] == ["client", "runtime"]
    assert report["reused"] == ["game-data"]
    assert any("component-runtime" in item for item in requested)
    assert package.is_file() and _snapshot(local) == before


def test_game_data_update_downloads_data_but_reuses_runtime(tmp_path):
    local, _, _, descriptor, _, fetch = _remote(tmp_path, data=b"new-version-exact-official-data")
    _, _, report = _prepare(tmp_path, local, descriptor, fetch)
    assert report["reused"] == ["runtime"]
    assert report["downloaded"] == ["client", "game-data"]


def test_component_archive_is_reproducible_when_only_source_mtime_changes(tmp_path):
    bundle = _bundle(tmp_path / "candidate")
    first = _release(bundle, tmp_path / "one")
    manifest = components.load_manifest(bundle / components.MANIFEST_NAME)
    before = {item["id"]: item["archive"]["sha256"] for item in manifest["components"]}
    for source in bundle.rglob("*"):
        if source.is_file(): os.utime(source, (1700000000, 1700000000))
    _release(bundle, tmp_path / "two")
    after = components.load_manifest(bundle / components.MANIFEST_NAME)
    assert before == {item["id"]: item["archive"]["sha256"] for item in after["components"]}
    assert first["sha256"] == _hash(tmp_path / "two/test-release-components.json")


def test_updater_code_change_does_not_force_runtime_component_download(tmp_path):
    bundle = _bundle(tmp_path / "candidate")
    executable = bundle / "_internal/updater/EndfieldLogsUpdater/EndfieldLogsUpdater.exe"
    executable.parent.mkdir(parents=True); executable.write_bytes(b"old updater code")
    dependency = executable.parent / "_internal/python314.dll"
    dependency.parent.mkdir(); dependency.write_bytes(b"shared unchanged updater runtime")
    _release(bundle, tmp_path / "one")
    before = {item["id"]:item["contentId"] for item in components.load_manifest(bundle/components.MANIFEST_NAME)["components"]}
    executable.write_bytes(b"new updater code")
    _release(bundle, tmp_path / "two")
    after = {item["id"]:item["contentId"] for item in components.load_manifest(bundle/components.MANIFEST_NAME)["components"]}
    assert after["client"] != before["client"]
    assert after["runtime"] == before["runtime"] and after["game-data"] == before["game-data"]


def test_local_http_real_downloader_component_staging_and_activation(tmp_path, monkeypatch):
    local, candidate, out, descriptor, _, _ = _remote(tmp_path)
    requests = []
    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs): super().__init__(*args, directory=out, **kwargs)
        def log_message(self, *args): pass
        def do_GET(self):
            requests.append(self.path)
            super().do_GET()
    class NoUi:
        def __init__(self, **kwargs): pass
        def update(self, *args): pass
        def set_status(self, *args): pass
        def close(self): pass
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
    try:
        remote_path = out / descriptor["url"].rsplit("/",1)[-1]
        remote = components.load_manifest(remote_path)
        urlbase = f"http://127.0.0.1:{server.server_port}"
        for item in remote["components"]: item["archive"]["url"] = urlbase + "/" + item["archive"]["url"].rsplit("/",1)[-1]
        remote_path.write_bytes(components._canonical(remote)+b"\n")
        (candidate/components.MANIFEST_NAME).write_bytes(remote_path.read_bytes())
        descriptor.update(url=urlbase+"/"+remote_path.name, sha256=_hash(remote_path), bytes=remote_path.stat().st_size)
        downloads = tmp_path / "download-cache"; downloads.mkdir()
        monkeypatch.setattr(update, "_updates_root", lambda: downloads)
        monkeypatch.setattr(update, "_DownloadProgressDialog", NoUi)
        def fetch(artifact):
            path, expected, actual = update._download_update_artifact(artifact,remote_version="3.0.13-test.3",remote_build="20261026")
            assert actual == expected
            return path
        before = _snapshot(local)
        package, _, report = _prepare(tmp_path, local, descriptor, fetch)
        assert len(requests) == 2 and requests[0].endswith("-components.json") and "component-client" in requests[1]
        assert _snapshot(local) == before and report["reused"] == ["runtime","game-data"]
        updater = _updater(); monkeypatch.setattr(updater,"_terminate_processes_under",lambda path:None)
        extract = tmp_path / "apply/extract"; updater._extract_package(package,extract)
        backup = updater._replace_installation(updater._find_package_root(extract),local)
        assert _snapshot(local) == _snapshot(candidate) and _snapshot(backup) == before
    finally:
        server.shutdown(); server.server_close(); worker.join(timeout=5)


def test_abi_change_and_missing_baseline_reject_before_component_downloads(tmp_path):
    local, _, _, descriptor, requested, fetch = _remote(tmp_path, abi="cp315-win_amd64-qt612")
    before = _snapshot(local)
    with pytest.raises(components.ComponentUpdateError, match="ABI"):
        _prepare(tmp_path, local, descriptor, fetch)
    assert len(requested) == 1 and _snapshot(local) == before
    (local / components.MANIFEST_NAME).unlink()
    with pytest.raises(FileNotFoundError): _prepare(tmp_path, local, descriptor, fetch)
    assert len(requested) == 2


@pytest.mark.parametrize("badpath", ["../escape", "/absolute", "C:/outside", "_internal/CON.txt", "_internal/data/../a", "a\\b", "trailing.", "a?.dll"])
def test_untrusted_component_paths_are_rejected(tmp_path, badpath):
    _, candidate, _, _, _, _ = _remote(tmp_path)
    manifest = components.load_manifest(candidate / components.MANIFEST_NAME)
    manifest["components"][0]["files"][0]["path"] = badpath
    with pytest.raises(components.ComponentUpdateError): components.validate_manifest(manifest)


def test_duplicate_cross_component_paths_and_omitted_components_rejected(tmp_path):
    _, candidate, _, _, _, _ = _remote(tmp_path)
    manifest = components.load_manifest(candidate / components.MANIFEST_NAME)
    altered = copy.deepcopy(manifest)
    altered["components"][1]["files"].append(altered["components"][0]["files"][0])
    with pytest.raises(components.ComponentUpdateError): components.validate_manifest(altered)
    altered = copy.deepcopy(manifest); altered["components"].pop()
    with pytest.raises(components.ComponentUpdateError): components.validate_manifest(altered)


def test_manifest_hash_mismatch_stops_before_installation_changes(tmp_path):
    local, _, _, descriptor, _, fetch = _remote(tmp_path)
    before = _snapshot(local)
    descriptor["sha256"] = "0" * 64
    with pytest.raises(components.ComponentUpdateError, match="manifest SHA256"):
        _prepare(tmp_path, local, descriptor, fetch)
    assert _snapshot(local) == before


@pytest.mark.parametrize("corruption", ["hash", "missing", "unlisted", "traversal", "symlink"])
def test_corrupt_component_payload_never_changes_active_installation(tmp_path, corruption):
    local, _, out, descriptor, _, fetch = _remote(tmp_path)
    remote_path = out / descriptor["url"].rsplit("/", 1)[-1]
    remote = components.load_manifest(remote_path)
    component = remote["components"][0]
    archive_path = out / component["archive"]["url"].rsplit("/", 1)[-1]
    if corruption == "hash": archive_path.write_bytes(b"corrupt-archive")
    else:
        with zipfile.ZipFile(archive_path) as archive: members = {name: archive.read(name) for name in archive.namelist()}
        if corruption == "missing": members.pop(next(iter(members)))
        if corruption == "unlisted": members["extra.dll"] = b"unlisted"
        if corruption == "traversal": members["../outside"] = b"escape"
        with zipfile.ZipFile(archive_path, "w") as archive:
            for name, data in members.items():
                if corruption == "symlink" and name == next(iter(members)):
                    info = zipfile.ZipInfo(name); info.external_attr = 0o120777 << 16
                    archive.writestr(info, data)
                else: archive.writestr(name, data)
        component["archive"].update(sha256=_hash(archive_path), bytes=archive_path.stat().st_size)
        remote_path.write_bytes(components._canonical(remote) + b"\n")
        descriptor["sha256"] = _hash(remote_path)
    before = _snapshot(local)
    with pytest.raises(components.ComponentUpdateError): _prepare(tmp_path, local, descriptor, fetch)
    assert _snapshot(local) == before and not (tmp_path / "outside").exists()


def test_updater_rejects_incomplete_component_tree_before_renaming_active_install(tmp_path, monkeypatch):
    local, candidate, _, _, _, _ = _remote(tmp_path)
    before = _snapshot(local)
    (candidate / "_internal/PySide6/Qt6WebEngineCore.dll").unlink()
    updater = _updater()
    monkeypatch.setattr(updater, "_terminate_processes_under", lambda path: pytest.fail("must validate before stopping processes"))
    with pytest.raises(components.ComponentUpdateError): updater._replace_installation(candidate, local)
    assert _snapshot(local) == before


def test_updater_directory_activation_rolls_back_on_move_failure(tmp_path, monkeypatch):
    local, candidate, _, _, _, _ = _remote(tmp_path)
    before = _snapshot(local)
    updater = _updater()
    monkeypatch.setattr(updater, "_terminate_processes_under", lambda path: None)
    monkeypatch.setattr(updater.shutil, "move", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("injected locked move")))
    with pytest.raises(OSError, match="injected"): updater._replace_installation(candidate, local)
    assert _snapshot(local) == before
    assert (candidate / components.MANIFEST_NAME).is_file()


def test_updater_directory_activation_rolls_back_on_postcopy_corruption(tmp_path, monkeypatch):
    local, candidate, _, _, _, _ = _remote(tmp_path)
    before = _snapshot(local)
    updater = _updater(); real_move = shutil.move
    monkeypatch.setattr(updater, "_terminate_processes_under", lambda path: None)
    def corrupt_after_move(source, target):
        result = real_move(source, target)
        (Path(target) / "_internal/base_library.zip").write_bytes(b"injected copy corruption")
        return result
    monkeypatch.setattr(updater.shutil, "move", corrupt_after_move)
    with pytest.raises(components.ComponentUpdateError): updater._replace_installation(candidate, local)
    assert _snapshot(local) == before


def test_updater_component_activation_preserves_old_tree_for_restore(tmp_path, monkeypatch):
    local, candidate, _, _, _, _ = _remote(tmp_path)
    old = _snapshot(local); new = _snapshot(candidate)
    updater = _updater(); monkeypatch.setattr(updater, "_terminate_processes_under", lambda path: None)
    backup = updater._replace_installation(candidate, local)
    assert _snapshot(local) == new and _snapshot(backup) == old


def test_client_component_failure_falls_back_to_full_legacy_package(tmp_path, monkeypatch):
    monkeypatch.setenv("ENDFIELD_LOGS_ENABLE_DEV_UPDATE", "1")
    monkeypatch.delenv("ENDFIELD_LOGS_DISABLE_UPDATE", raising=False)
    manifest = {"version":"3.0.13", "build":"20261026", "url":"https://example.test/full.zip", "sha256":"a"*64,
                "components":{"url":"https://example.test/components.json", "sha256":"b"*64}}
    monkeypatch.setattr(update, "_read_version_payload", lambda: {"version":"3.0.12", "build":"20261016"})
    monkeypatch.setattr(update, "_fetch_json", lambda *args: manifest)
    monkeypatch.setattr(update, "_show_update_prompt", lambda *args, **kwargs: True)
    monkeypatch.setattr(update, "_updates_root", lambda: tmp_path / "updates")
    monkeypatch.setattr(update, "app_root", lambda: tmp_path / "installed")
    monkeypatch.setattr(update, "prepare_component_package", lambda *args, **kwargs: (_ for _ in ()).throw(components.ComponentUpdateError("bad component")))
    full = tmp_path / "full.zip"; full.write_bytes(b"mock package")
    downloads=[]; launched=[]
    monkeypatch.setattr(update, "_download_update_artifact", lambda artifact, **kw: (downloads.append(artifact) or (full,"a"*64,"a"*64)))
    monkeypatch.setattr(update, "_prepare_updater", lambda: tmp_path / "updater.exe")
    monkeypatch.setattr(update.subprocess,"Popen",lambda command, **kwargs: launched.append(command))
    assert update.check_and_maybe_start_update() is True
    assert downloads == [manifest] and "--package" in launched[0]
    assert str(full) in launched[0]


def test_client_success_uses_assembled_package_without_full_download(tmp_path, monkeypatch):
    monkeypatch.setenv("ENDFIELD_LOGS_ENABLE_DEV_UPDATE", "1"); monkeypatch.delenv("ENDFIELD_LOGS_DISABLE_UPDATE", raising=False)
    manifest={"version":"3.0.13","build":"20261026","url":"https://example.test/full.zip","sha256":"a"*64,"components":{"schemaVersion":1}}
    monkeypatch.setattr(update,"_read_version_payload",lambda:{"version":"3.0.12","build":"20261016"})
    monkeypatch.setattr(update,"_fetch_json",lambda *args:manifest)
    monkeypatch.setattr(update,"_show_update_prompt",lambda *args,**kwargs:True)
    monkeypatch.setattr(update,"_updates_root",lambda:tmp_path/'updates');monkeypatch.setattr(update,"app_root",lambda:tmp_path/'installed')
    package=tmp_path/'assembled.zip';package.write_bytes(b'assembled')
    monkeypatch.setattr(update,"prepare_component_package",lambda *args,**kwargs:(package,"b"*64,{"reused":["runtime"],"downloaded":["client","game-data"],"repaired":[]}))
    monkeypatch.setattr(update,"_download_update_artifact",lambda *args,**kwargs:pytest.fail('no full download'))
    monkeypatch.setattr(update,"_prepare_updater",lambda:tmp_path/'updater.exe')
    launched=[];monkeypatch.setattr(update.subprocess,"Popen",lambda command,**kwargs:launched.append(command))
    assert update.check_and_maybe_start_update() and str(package) in launched[0]
