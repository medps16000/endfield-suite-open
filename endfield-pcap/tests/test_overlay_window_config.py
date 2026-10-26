from __future__ import annotations

import json
from pathlib import Path

import pytest

from endfield_pcap.overlay_window_config import OverlayWindowConfig, validate_source


def test_config_persists_window_controls_and_keeps_original_html(tmp_path):
    html = tmp_path / "page.html"
    html.write_text("<h1>window</h1>", encoding="utf-8")
    path = tmp_path / "windows.json"
    store = OverlayWindowConfig(path)
    first = store.add_entry("一号", "file", str(html))
    second = store.add_entry("二号", "file", str(html))
    assert first["id"] != second["id"]
    store.update_entry(first["id"], locked=True, clickThrough=True, visible=False, opacity=0.42, geometry={"x": -300, "y": 80, "width": 501, "height": 207})
    loaded = OverlayWindowConfig(path)
    actual = loaded.get_entry(first["id"])
    assert (actual["locked"], actual["clickThrough"], actual["visible"], actual["opacity"]) == (True, True, False, 0.42)
    assert actual["geometry"] == {"x": -300, "y": 80, "width": 501, "height": 207}
    actual["name"] = "mutated"
    assert loaded.get_entry(first["id"])["name"] == "一号"
    loaded.remove_entry(first["id"])
    assert html.exists() and len(OverlayWindowConfig(path).list_entries()) == 1


@pytest.mark.parametrize("url", ["http://example.com/", "file:///C:/secret", "https://user:password@example.com/", "https://example.com:8443/", "javascript:alert(1)"])
def test_source_rejects_broad_or_credentialed_urls(url):
    with pytest.raises(ValueError):
        validate_source("url", url)


def test_invalid_config_is_not_replaced(tmp_path):
    path = tmp_path / "bad.json"
    original = '{"schemaVersion": 100, "windows": []}'
    path.write_text(original, encoding="utf-8")
    with pytest.raises(ValueError, match="已保留"):
        OverlayWindowConfig(path)
    assert path.read_text(encoding="utf-8") == original


def test_missing_original_page_preserves_saved_entry(tmp_path):
    path = tmp_path / "windows.json"
    html = tmp_path / "page.html"
    html.write_text("test", encoding="utf-8")
    first = OverlayWindowConfig(path).add_entry("test", "file", str(html))
    html.unlink()
    store = OverlayWindowConfig(path)
    assert store.get_entry(first["id"])["sourceValue"] == str(html.resolve())
    store.update_entry(first["id"], visible=False)
    assert not store.get_entry(first["id"])["visible"]
