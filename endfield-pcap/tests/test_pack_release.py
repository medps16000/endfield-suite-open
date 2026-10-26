from __future__ import annotations

import json
import gzip
from pathlib import Path
import zipfile

import pack_release
import pytest


def test_release_validates_official_skilldata_damage_catalog() -> None:
    assert pack_release._validate_official_skill_damage_catalog() == 0


def test_release_rejects_invalid_official_skilldata_damage_catalog(monkeypatch, tmp_path: Path) -> None:
    catalog = tmp_path / "damage_element_catalog.json"
    catalog.write_text('{"source":"akedata","skills":{}}\n', encoding="utf-8")
    monkeypatch.setattr(pack_release, "OFFICIAL_SKILL_DAMAGE_CATALOG", catalog)

    assert pack_release._validate_official_skill_damage_catalog() == 1


def test_release_rejects_legacy_catalog_even_when_known_skill_labels_match(monkeypatch, tmp_path):
    path = tmp_path / 'legacy.json'
    path.write_text(json.dumps({'source': 'official_local_skilldata_memorypack', 'skills': {
        key: {'resolved': True, 'elements': [element]} for key, element in [
            ('chr_0032_lizhiyan_attack1', 'natural'), ('chr_0035_liino_attack1', 'pulse'),
            ('chr_0035_liino_normal_skill_projhit', 'pulse')]
    }}), encoding='utf8')
    monkeypatch.setattr(pack_release, 'OFFICIAL_SKILL_DAMAGE_CATALOG', path)
    assert pack_release._validate_official_skill_damage_catalog() != 0


def test_release_checks_resources_outside_small_regression_skill_list(monkeypatch, tmp_path):
    payload = json.loads(pack_release.OFFICIAL_SKILL_DAMAGE_CATALOG.read_text(encoding='utf8'))
    key = 'buff_common_burning_status'
    payload['skills'][key]['proof']['consumedBytes'] -= 1
    path = tmp_path / 'partial.json'
    path.write_text(json.dumps(payload), encoding='utf8')
    monkeypatch.setattr(pack_release, 'OFFICIAL_SKILL_DAMAGE_CATALOG', path)
    assert pack_release._validate_official_skill_damage_catalog() != 0


def test_release_rejects_partial_buff_root(monkeypatch, tmp_path):
    payload = json.loads(gzip.decompress(pack_release.OFFICIAL_BUFF_ROOTS.read_bytes()))
    payload['entries']['buff_common_burning_status']['root']['$end'] -= 1
    path = tmp_path / 'roots.gz'
    path.write_bytes(gzip.compress(json.dumps(payload).encode('utf8')))
    monkeypatch.setattr(pack_release, 'OFFICIAL_BUFF_ROOTS', path)
    assert pack_release._validate_official_skill_damage_catalog() != 0


def test_release_manifest_defaults_to_forced_update() -> None:
    parser = pack_release.build_parser()

    assert parser.parse_args([]).force is True
    assert parser.parse_args([]).version == "3.0.12"
    assert parser.parse_args([]).min_version == "3.0.9"
    assert parser.parse_args([]).enable_patch is False
    assert parser.parse_args(["--no-force"]).force is False
    formal = parser.parse_args(["--version", "3.0.12", "--min-version", "3.0.9", "--no-force", "--no-patch"])
    assert (formal.version, formal.min_version, formal.force, formal.enable_patch) == ("3.0.12", "3.0.9", False, False)


def test_release_stops_before_building_when_game_resources_are_stale(monkeypatch, tcl_release_sandbox):
    monkeypatch.setattr(pack_release, '_verify_game_resource_snapshot', lambda: 1)
    monkeypatch.setattr(pack_release, '_build_full_uploader', lambda: pytest.fail('stale resources must not build'))
    assert pack_release.main() == 1


def test_release_dependency_check_rejects_protobuf_version_drift(monkeypatch) -> None:
    monkeypatch.setattr(pack_release.importlib.metadata, "version", lambda _name: "7.35.1")

    error = pack_release._verify_release_dependencies()

    assert error is not None
    assert "required=6.33.6" in error
    assert "actual=7.35.1" in error


def test_release_requires_validated_runtime_for_retained_multi_battle_results(monkeypatch) -> None:
    monkeypatch.setattr(pack_release.importlib.metadata, "version", lambda _name: "6.33.6")
    monkeypatch.setattr(pack_release.sys, "version_info", (3, 13, 15))
    assert "required=3.14.7" in pack_release._verify_release_dependencies()
    monkeypatch.setattr(pack_release.sys, "version_info", (3, 14, 7))
    assert pack_release._verify_release_dependencies() is None


@pytest.fixture
def tcl_release_sandbox(monkeypatch, tmp_path):
    root = tmp_path / "project"
    runtime = tmp_path / "python3147"
    (runtime / "DLLs").mkdir(parents=True)
    root.mkdir()
    for dll, prefix, script in (
        ("tcl90.dll", "tcl_library", "init.tcl"),
        ("tcl9tk90.dll", "tk_library", "tk.tcl"),
    ):
        with zipfile.ZipFile(runtime / "DLLs" / dll, "w") as archive:
            archive.writestr(f"{prefix}/{script}", "# test Tcl resource\n")
            archive.writestr(f"{prefix}/encoding/test.enc", "test encoding\n")
    monkeypatch.setattr(pack_release, "ROOT", root)
    monkeypatch.setattr(pack_release, "BUILD_META_DIR", root / "build_meta")
    monkeypatch.setattr(pack_release, "VERSION_JSON", root / "build_meta/version.json")
    monkeypatch.setattr(pack_release, "TCL_RUNTIME_DIR", root / "build_meta/tcl_runtime_3.14.7")
    monkeypatch.setattr(pack_release, "DIST", root / "dist/EndfieldLogsClient")
    monkeypatch.setattr(pack_release, "UPDATER_DIST", root / "dist/EndfieldLogsUpdater")
    monkeypatch.setattr(pack_release, "DETECTOR_DIST", root / "dist_detector")
    monkeypatch.setattr(pack_release, "DETECTOR_EXE", root / "dist_detector/EndfieldLogsDetector.exe")
    monkeypatch.setattr(pack_release.sys, "base_prefix", str(runtime))
    monkeypatch.setattr(pack_release.sys, "argv", ["pack_release.py", "--no-patch"])
    monkeypatch.setattr(pack_release, "_prefer_venv_python", lambda: None)
    monkeypatch.setattr(pack_release, "_verify_release_dependencies", lambda: None)
    monkeypatch.setattr(pack_release, "_verify_game_resource_snapshot", lambda: 0)
    monkeypatch.setattr(pack_release, "_sync_required_local_tables", lambda: 0)
    monkeypatch.setattr(pack_release, "_sync_and_validate_character_catalog", lambda: 0)
    monkeypatch.setattr(pack_release, "_validate_official_skill_damage_catalog", lambda: 0)
    monkeypatch.setattr(pack_release, "_build_full_uploader", lambda: 0)
    return root, runtime


def test_all_three_tk_build_commands_include_extracted_runtime(monkeypatch, tcl_release_sandbox):
    root, _runtime = tcl_release_sandbox
    commands = []

    def record_build(command, **kwargs):
        commands.append(command)
        # Simulate only the minimum build outputs, entirely under tmp_path.
        pack_release.DIST.mkdir(parents=True, exist_ok=True)
        (pack_release.DIST / "EndfieldLogsClient.exe").write_bytes(b"MZ test")
        pack_release.DETECTOR_EXE.parent.mkdir(parents=True, exist_ok=True)
        pack_release.DETECTOR_EXE.write_bytes(b"MZ detector test")
        return 0

    monkeypatch.setattr(pack_release.subprocess, "call", record_build)
    assert pack_release.main() == 0
    assert commands[0] == [pack_release.sys.executable, str(root / "scripts/sync_rotation_assets.py"), "validate"]
    asset_checks = [command for command in commands if command == commands[0]]
    assert len(asset_checks) == 2
    assert commands[-1] == commands[0]
    builds = [command for command in commands if command != commands[0]]
    assert len(builds) == 3
    assert {command[command.index("--name") + 1] for command in builds} == {
        "EndfieldLogsClient", "EndfieldLogsUpdater", "EndfieldLogsDetector",
    }
    for command in builds:
        data_arguments = [command[index + 1] for index, arg in enumerate(command) if arg == "--add-data"]
        for folder in ("_tcl_data", "_tk_data"):
            assert f"{pack_release.TCL_RUNTIME_DIR / folder};{folder}" in data_arguments
    assert (pack_release.TCL_RUNTIME_DIR / "_tcl_data/init.tcl").read_text() == "# test Tcl resource\n"
    assert (pack_release.TCL_RUNTIME_DIR / "_tk_data/tk.tcl").read_text() == "# test Tcl resource\n"
    assert not (root / "escape.tcl").exists()


def test_invalid_rotation_assets_block_before_build(monkeypatch, tcl_release_sandbox):
    root, _runtime = tcl_release_sandbox
    calls = []
    def reject_assets(command, **kwargs):
        calls.append(command)
        return 1
    monkeypatch.setattr(pack_release.subprocess, "call", reject_assets)
    monkeypatch.setattr(pack_release, "_build_full_uploader", lambda: pytest.fail("invalid assets must block the build"))
    assert pack_release.main() == 1
    assert calls == [[pack_release.sys.executable, str(root / "scripts/sync_rotation_assets.py"), "validate"]]


@pytest.mark.parametrize("failed_gate", ["typed_roots", "rotation_assets"])
def test_post_build_resource_failure_prevents_archive_and_manifest(monkeypatch, tcl_release_sandbox, failed_gate):
    root, _runtime = tcl_release_sandbox
    counts = {"typed_roots": 0, "rotation_assets": 0}

    def typed_roots():
        counts["typed_roots"] += 1
        return int(failed_gate == "typed_roots" and counts["typed_roots"] == 2)

    def build_or_validate(command, **kwargs):
        if command == [pack_release.sys.executable, str(root / "scripts/sync_rotation_assets.py"), "validate"]:
            counts["rotation_assets"] += 1
            return int(failed_gate == "rotation_assets" and counts["rotation_assets"] == 2)
        pack_release.DIST.mkdir(parents=True, exist_ok=True)
        (pack_release.DIST / "EndfieldLogsClient.exe").write_bytes(b"MZ test")
        pack_release.DETECTOR_EXE.parent.mkdir(parents=True, exist_ok=True)
        pack_release.DETECTOR_EXE.write_bytes(b"MZ detector test")
        return 0

    monkeypatch.setattr(pack_release, "_validate_official_skill_damage_catalog", typed_roots)
    monkeypatch.setattr(pack_release.subprocess, "call", build_or_validate)
    assert pack_release.main() == 1
    assert counts["typed_roots"] == 2
    assert counts["rotation_assets"] == (2 if failed_gate == "rotation_assets" else 1)
    assert not list(root.glob("EndfieldLogsClient_*.zip"))
    assert not (root / "latest.json").exists()


@pytest.mark.parametrize("unsafe_name", [
    "tcl_library/../escape.tcl", "tcl_library/C:/escape.tcl",
    "tcl_library/a\\..\\escape.tcl", "unexpected/init.tcl",
])
def test_tcl_resource_extraction_rejects_unsafe_paths(monkeypatch, tcl_release_sandbox, unsafe_name):
    _root, runtime = tcl_release_sandbox
    with zipfile.ZipFile(runtime / "DLLs/tcl90.dll", "a") as archive:
        archive.writestr(unsafe_name, "invalid")
    monkeypatch.setattr(pack_release, "_build_full_uploader", lambda: pytest.fail("must not start a build"))
    assert pack_release.main() == 1


def test_tcl_resource_extraction_rejects_bad_crc(monkeypatch, tcl_release_sandbox):
    _root, runtime = tcl_release_sandbox
    path = runtime / "DLLs/tcl90.dll"
    original = path.read_bytes()
    assert b"# test Tcl resource" in original
    path.write_bytes(original.replace(b"# test Tcl resource", b"! test Tcl resource", 1))
    monkeypatch.setattr(pack_release, "_build_full_uploader", lambda: pytest.fail("must not start a build"))
    assert pack_release.main() == 1


def test_build_patch_archive_contains_only_changed_files_and_delete_manifest(monkeypatch, tmp_path: Path) -> None:
    dist = tmp_path / "dist" / "EndfieldLogsClient"
    dist.mkdir(parents=True)
    (dist / "version.json").write_text('{"version":"2.10.39","build":"20260605"}\n', encoding="utf-8")
    (dist / "keep.txt").write_text("same", encoding="utf-8")
    (dist / "changed.txt").write_text("new", encoding="utf-8")
    (dist / "new.txt").write_text("created", encoding="utf-8")
    monkeypatch.setattr(pack_release, "DIST", dist)

    base_package = tmp_path / "EndfieldLogsClient_2.10.38_20260605.zip"
    with zipfile.ZipFile(base_package, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("EndfieldLogsClient/version.json", '{"version":"2.10.38","build":"20260605"}\n')
        archive.writestr("EndfieldLogsClient/keep.txt", "same")
        archive.writestr("EndfieldLogsClient/changed.txt", "old")
        archive.writestr("EndfieldLogsClient/deleted.txt", "removed")

    patch_archive = tmp_path / "client.patch.zip"
    patch_info = pack_release._build_patch_archive(
        base_package=base_package,
        patch_archive=patch_archive,
        version="2.10.39",
        build="20260605",
    )

    assert patch_info is not None
    assert patch_info["fromVersion"] == "2.10.38"
    assert patch_info["fromBuild"] == "20260605"
    assert patch_info["fileCount"] == 3
    assert patch_info["deleteCount"] == 1
    with zipfile.ZipFile(patch_archive) as archive:
        names = set(archive.namelist())
        manifest = json.loads(archive.read("patch_manifest.json").decode("utf-8"))
    assert names == {
        "patch_manifest.json",
        "payload/version.json",
        "payload/changed.txt",
        "payload/new.txt",
    }
    assert {item["path"] for item in manifest["files"]} == {"version.json", "changed.txt", "new.txt"}
    assert manifest["delete"] == ["deleted.txt"]
