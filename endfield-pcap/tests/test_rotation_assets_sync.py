import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtGui")
from PySide6.QtGui import QColor, QImage

SPEC = importlib.util.spec_from_file_location("rotation_asset_sync", Path(__file__).resolve().parents[1] / "scripts/sync_rotation_assets.py")
assets = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(assets)


@pytest.fixture
def inputs(tmp_path):
    public = tmp_path / "web/public"
    image = QImage(7, 5, QImage.Format.Format_RGBA8888)
    image.fill(QColor(25, 120, 230, 93))
    skill = public / "images/character/game-skills/skill_one.webp"
    avatar = public / "images/character/game-avatars/real_avatar.hash.png"
    for path, encoding in ((skill, "WEBP"), (avatar, "PNG")):
        path.parent.mkdir(parents=True, exist_ok=True)
        assert image.save(str(path), encoding)
    skill_url, avatar_url = "/" + skill.relative_to(public).as_posix(), "/" + avatar.relative_to(public).as_posix()
    (skill.parent / "manifest.json").write_text(json.dumps({"schemaVersion": 1, "icons": {
        "skill_one": {"url": skill_url, "width": 7, "height": 5, "viewBox": "0 0 7 5"}}}), "utf-8")
    (avatar.parent / "manifest.json").write_text(json.dumps({"avatars": {
        "canonical_character": {"url": avatar_url, "width": 7, "height": 5}}}), "utf-8")
    targets = (tmp_path / "client/data/public", tmp_path / "server/data/public")
    return public, targets, skill, avatar, skill_url, avatar_url


def test_headless_generation_preserves_every_rgba_pixel_and_exact_avatar_alias(inputs):
    public, targets, skill, avatar, skill_url, avatar_url = inputs
    index = assets.generate_rotation_assets(public, targets)
    assert set(index["images"]) == {skill_url, avatar_url}
    assert index["images"][skill_url]["path"] == "skills/skill_one.png"
    assert index["images"][avatar_url]["path"] == "avatars/canonical_character.png"
    assert index["images"][skill_url]["viewBox"] == "0 0 7 5"
    for source, url in ((skill, skill_url), (avatar, avatar_url)):
        entry = index["images"][url]
        first = targets[0] / "rotation-assets" / entry["path"]
        second = targets[1] / "rotation-assets" / entry["path"]
        assert first.read_bytes() == second.read_bytes()
        assert first.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
        assert hashlib.sha256(first.read_bytes()).hexdigest() == entry["sha256"]
        assert hashlib.sha256(source.read_bytes()).hexdigest() == entry["sourceImageSha256"]
        assert assets._pixels(QImage(str(source))) == assets._pixels(QImage(str(first))) == entry["pixelSha256"]
    assert (targets[0] / "rotation-assets/avatars/canonical_character.png").read_bytes() == avatar.read_bytes()
    assert assets.validate_rotation_assets(public, targets) == index


def test_validate_is_read_only_and_generate_is_idempotent(inputs):
    public, targets, *_ = inputs
    assets.generate_rotation_assets(public, targets)
    before = {str(p): (p.stat().st_mtime_ns, p.read_bytes()) for t in targets for p in t.rglob("*") if p.is_file()}
    assets.validate_rotation_assets(public, targets)
    assets.generate_rotation_assets(public, targets)
    after = {str(p): (p.stat().st_mtime_ns, p.read_bytes()) for t in targets for p in t.rglob("*") if p.is_file()}
    assert before == after


@pytest.mark.parametrize("url", [
    "https://example.com/image.webp", "//example.com/image.webp",
    "/images/character/game-skills/../outside.webp",
    "/images/character/game-skills/%2e%2e.webp",
    "/images/character/game-skills/skill_one.webp?x=1",
    "/images/character/game-skills/skill_one.webp#fragment",
    "/images/character/game-skills/subdir/skill_one.webp",
    "/images/character/game-skills/skill_one\\x.webp",
])
def test_only_explicit_local_official_urls_are_accepted(inputs, url):
    public, targets, skill, *_ = inputs
    path = skill.parent / "manifest.json"
    raw = json.loads(path.read_text("utf-8"))
    raw["icons"]["skill_one"]["url"] = url
    path.write_text(json.dumps(raw), "utf-8")
    with pytest.raises(ValueError, match="URL"):
        assets.generate_rotation_assets(public, targets)
    assert not any(p.exists() for p in targets)


@pytest.mark.parametrize("mutation", ["source", "metadata", "png", "manifest", "missing", "extra"])
def test_validation_rejects_stale_source_tampering_missing_and_unlisted_files(inputs, mutation):
    public, targets, skill, _, skill_url, _ = inputs
    index = assets.generate_rotation_assets(public, targets)
    output = targets[1] / "rotation-assets" / index["images"][skill_url]["path"]
    if mutation == "source":
        skill.write_bytes(skill.read_bytes() + b"changed-source")
    elif mutation == "metadata":
        path = skill.parent / "manifest.json"
        raw = json.loads(path.read_text("utf-8"))
        raw["icons"]["skill_one"]["width"] = 8
        path.write_text(json.dumps(raw), "utf-8")
    elif mutation == "png":
        output.write_bytes(output.read_bytes() + b"different-png-bytes")
    elif mutation == "manifest":
        path = targets[1] / "rotation-assets.json"
        raw = json.loads(path.read_text("utf-8"))
        raw["images"][skill_url]["path"] = "../outside.png"
        path.write_text(json.dumps(raw), "utf-8")
    elif mutation == "missing":
        output.unlink()
    else:
        (output.parent / "unlisted.png").write_bytes(b"unlisted")
    before = {str(p): p.read_bytes() for t in targets for p in t.rglob("*") if p.is_file()}
    with pytest.raises(ValueError):
        assets.validate_rotation_assets(public, targets)
    after = {str(p): p.read_bytes() for t in targets for p in t.rglob("*") if p.is_file()}
    assert before == after


def test_duplicate_manifest_urls_and_keys_are_rejected(inputs):
    public, _, skill, *_ = inputs
    path = skill.parent / "manifest.json"
    raw = json.loads(path.read_text("utf-8"))
    raw["icons"]["skill_two"] = dict(raw["icons"]["skill_one"])
    path.write_text(json.dumps(raw), "utf-8")
    with pytest.raises(ValueError, match="Duplicate official image URL"):
        assets.build_rotation_assets(public)
    path.write_text('{"icons": {}, "icons": {}}', "utf-8")
    with pytest.raises(ValueError, match="Duplicate JSON key"):
        assets.build_rotation_assets(public)


def test_canonical_manifest_fingerprint_ignores_only_json_formatting(inputs):
    public, targets, skill, *_ = inputs
    index = assets.generate_rotation_assets(public, targets)
    path = skill.parent / "manifest.json"
    raw = json.loads(path.read_text("utf-8"))
    path.write_text(json.dumps(raw, indent=4, sort_keys=True), "utf-8")
    assert assets.validate_rotation_assets(public, targets) == index


def test_generate_refuses_unknown_output_without_deleting_it(inputs):
    public, targets, *_ = inputs
    assets.generate_rotation_assets(public, targets)
    unknown = targets[0] / "rotation-assets/skills/unknown.png"
    unknown.write_bytes(b"keep this file")
    with pytest.raises(ValueError, match="no automatic deletion"):
        assets.generate_rotation_assets(public, targets)
    assert unknown.read_bytes() == b"keep this file"
