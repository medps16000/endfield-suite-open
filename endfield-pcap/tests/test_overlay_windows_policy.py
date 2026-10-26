from __future__ import annotations

import ssl
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from endfield_pcap import overlay_windows as host


def entry(kind="file", value="D:/example/page.html", identifier="test"):
    return {"id": identifier, "sourceType": kind, "sourceValue": value}


def test_exact_entry_and_blocked_navigation():
    policy = host.EntryPolicy(entry("url", "https://example.com/overlay/index.html?q=1"))
    assert policy.exact_entry(policy.entry_url + "#section")
    assert not policy.exact_entry(policy.entry_url + "&new=1")
    assert policy.request_action("file:///C:/Windows/win.ini") == ("deny", None)
    assert policy.request_action("http://127.0.0.1:8765/status") == ("deny", None)
    assert policy.request_action("wss://example.com/socket") == ("deny", None)
    assert policy.request_action("https://cdn.example.com/app.js") == ("deny", None)
    assert policy.request_action("https://example.com/app.js") == ("redirect", "endfield-overlay://wtest/app.js")
    assert policy.request_action("https://example.com/app.js", main_frame=True) == ("deny", None)


def test_file_handler_rejects_escape_and_symlink(tmp_path):
    root = tmp_path / "pages"
    root.mkdir()
    page = root / "index.html"
    page.write_text("test")
    sibling = root / "style.css"
    sibling.write_text("body{}")
    outside = tmp_path / "secret.txt"
    outside.write_text("private")
    policy = host.EntryPolicy(entry(value=str(page)))
    assert policy.local_path("endfield-overlay://wtest/style.css") == sibling
    for route in ("/../secret.txt", "/%2e%2e/secret.txt", "/%5c..%5csecret.txt", "/C:%5csecret.txt"):
        with pytest.raises((ValueError, OSError)):
            policy.local_path("endfield-overlay://wtest" + route)
    link = root / "escape.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        if sys.platform != "win32":
            raise
        # Windows junctions need no symlink privilege and exercise the same
        # resolved-directory escape boundary on the actual filesystem.
        import _winapi
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()
        (outside_dir / "secret.txt").write_text("private")
        junction = root / "escape-directory"
        _winapi.CreateJunction(str(outside_dir), str(junction))
        try:
            with pytest.raises(ValueError):
                policy.local_path("endfield-overlay://wtest/escape-directory/secret.txt")
        finally:
            junction.rmdir()
        assert (outside_dir / "secret.txt").read_text() == "private"
        return
    with pytest.raises(ValueError):
        policy.local_path("endfield-overlay://wtest/escape.txt")


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "172.16.0.1", "192.168.1.1", "169.254.169.254", "::1", "fc00::1", "0.0.0.0", "224.0.0.1"])
def test_all_dns_answers_must_be_public(monkeypatch, address):
    monkeypatch.setattr(host.socket, "getaddrinfo", lambda *_args, **_kwargs: [(2, 1, 6, "", ("1.1.1.1", 443)), (2, 1, 6, "", (address, 443))])
    with pytest.raises(ValueError):
        host.public_addresses("example.com")


def test_transport_pins_address_and_validates_hostname(monkeypatch):
    connection = host._PinnedHTTPSConnection("example.com", "1.1.1.1")
    assert connection._context.check_hostname
    assert connection._context.verify_mode == ssl.CERT_REQUIRED
    raw = Mock()
    wrapped = Mock()
    connect = Mock(return_value=raw)
    wrap = Mock(return_value=wrapped)
    monkeypatch.setattr(host.socket, "create_connection", connect)
    monkeypatch.setattr(connection._context, "wrap_socket", wrap)
    connection.connect()
    connect.assert_called_once_with(("1.1.1.1", 443), timeout=12)
    wrap.assert_called_once_with(raw, server_hostname="example.com")
    assert connection.sock is wrapped


def test_proxy_does_not_follow_redirects_or_send_cookies(monkeypatch):
    monkeypatch.setattr(host, "public_addresses", lambda _: ["1.1.1.1"])
    instance = Mock()
    instance.getresponse.return_value.status = 302
    factory = Mock(return_value=instance)
    monkeypatch.setattr(host, "_PinnedHTTPSConnection", factory)
    with pytest.raises(ValueError, match="重定向"):
        host.fetch_public_https("https://example.com/overlay")
    factory.assert_called_once_with("example.com", "1.1.1.1")
    headers = instance.request.call_args.kwargs["headers"]
    assert not {"Cookie", "Authorization", "Proxy-Authorization"} & set(headers)
    instance.close.assert_called_once()


def test_sandbox_disabled_is_fail_closed_not_import_failure(monkeypatch):
    monkeypatch.setenv("QTWEBENGINE_DISABLE_SANDBOX", "1")
    assert not host.sandbox_enabled()
    with pytest.raises(RuntimeError, match="沙箱"):
        host.OverlayWindowManager(None, "unused", "unused")
