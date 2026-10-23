import gzip
import socket
import ssl
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock

import pytest
import requests

import article_fetch as module


@pytest.fixture
def article_server(monkeypatch):
    calls, resolutions, peers = [], [], []
    compressed = gzip.compress(b"x" * (module.MAX_ARTICLE_BYTES + 1))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append((self.path, self.headers.get("Host")))
            try:
                if self.path == "/redirect-private":
                    self.send_response(302)
                    self.send_header("Location", "http://127.0.0.1/private")
                    self.end_headers()
                    return
                if self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", "/small")
                    self.end_headers()
                    return
                if self.path == "/headers-drip":
                    self.wfile.write(b"HTTP/1.1 200 OK\r\nX-Drip: ")
                    self.wfile.flush()
                    for _ in range(100):
                        self.wfile.write(b"x")
                        self.wfile.flush()
                        time.sleep(0.02)
                    return
                if self.path == "/drip":
                    self.send_response(200)
                    self.send_header("Content-Length", "100")
                    self.end_headers()
                    for _ in range(100):
                        self.wfile.write(b"x")
                        self.wfile.flush()
                        time.sleep(0.02)
                    return
                data = (
                    compressed
                    if self.path == "/large"
                    else b"<html><title>fixture</title></html>"
                )
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                if self.path == "/large":
                    self.send_header("Content-Encoding", "gzip")
                self.end_headers()
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    original_connect = module._connect_peer

    def resolve(host, port, **kwargs):
        resolutions.append(host)
        # A second DNS lookup for the request would rebound to loopback.
        ip = "8.8.8.8" if resolutions.count(host) == 1 else "127.0.0.1"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]

    def connect(peer, port, deadline):
        peers.append(peer)
        assert peer == "8.8.8.8"
        # Relocate the checked public peer to our owned socket fixture only.
        return original_connect("127.0.0.1", port, deadline)

    monkeypatch.setattr(module.socket, "getaddrinfo", resolve)
    monkeypatch.setattr(module, "_connect_peer", connect)
    try:
        yield server, calls, resolutions, peers
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://127.0.0.1/",
        "http://10.0.0.1/",
        "http://[::1]/",
        "http://2130706433/",
        "http://metadata.internal/",
    ],
)
def test_unsafe_initial_url_makes_zero_requests(url):
    session = Mock()
    with pytest.raises(requests.RequestException, match="not a public"):
        module.fetch_article_html(session, url, headers={})
    session.get.assert_not_called()


def test_all_resolved_addresses_must_be_public(monkeypatch):
    monkeypatch.setattr(
        module.socket,
        "getaddrinfo",
        lambda *a, **kw: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))
            for ip in ("8.8.8.8", "127.0.0.1")
        ],
    )
    assert not module.public_article_url("https://fixture.example/article")


def test_checked_peer_is_dialed_once_with_original_host_and_no_proxy(
    article_server, monkeypatch
):
    server, calls, resolutions, peers = article_server
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    result = module.fetch_article_html(
        Mock(), f"http://fixture.example:{server.server_port}/small", headers={}
    )
    assert (
        "fixture" in result
        and resolutions == ["fixture.example"]
        and peers == ["8.8.8.8"]
    )
    assert calls == [("/small", f"fixture.example:{server.server_port}")]


def test_private_redirect_is_not_requested(article_server):
    server, calls, resolutions, peers = article_server
    with pytest.raises(requests.RequestException, match="not a public"):
        module.fetch_article_html(
            Mock(),
            f"http://fixture.example:{server.server_port}/redirect-private",
            headers={},
        )
    assert len(calls) == len(resolutions) == len(peers) == 1


def test_real_http_stream_caps_decompressed_gzip_and_keeps_valid_html(article_server):
    server, _, resolutions, _ = article_server
    target = f"http://fixture.example:{server.server_port}"
    assert "fixture" in module.fetch_article_html(Mock(), target + "/small", headers={})
    resolutions.clear()  # Independent fetch has an independent checked resolution.
    with pytest.raises(requests.RequestException, match="byte limit"):
        module.fetch_article_html(Mock(), target + "/large", headers={})


@pytest.mark.parametrize("path", ["/drip", "/headers-drip"])
def test_total_deadline_stops_slow_drip_headers_and_body(
    article_server, monkeypatch, path
):
    server, _, _, _ = article_server
    monkeypatch.setattr(module, "ARTICLE_TIMEOUT_SECONDS", 0.15)
    started = time.monotonic()
    with pytest.raises(requests.Timeout, match="deadline"):
        module.fetch_article_html(
            Mock(), f"http://fixture.example:{server.server_port}{path}", headers={}
        )
    assert time.monotonic() - started < 0.8


def test_dns_resolution_is_bounded_by_the_same_total_deadline(monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(module, "ARTICLE_TIMEOUT_SECONDS", 0.05)

    def dns(*args, **kwargs):
        release.wait(2)
        return []

    monkeypatch.setattr(module.socket, "getaddrinfo", dns)
    started = time.monotonic()
    try:
        with pytest.raises(requests.Timeout, match="DNS deadline"):
            module.fetch_article_html(Mock(), "http://fixture.example/", headers={})
        assert time.monotonic() - started < 0.5
    finally:
        release.set()


def test_existing_article_cache_entrypoint_uses_bounded_fetch(monkeypatch):
    import score_gdelt_news as scorer

    cursor = Mock()
    cursor.__enter__ = Mock(return_value=cursor)
    cursor.__exit__ = Mock(return_value=False)
    cursor.fetchone.return_value = None
    connection = Mock()
    connection.cursor.return_value = cursor
    fetch = Mock(
        return_value='<html><title>Fixture</title><meta name="description" content="Fixture description"></html>'
    )
    monkeypatch.setattr(module, "fetch_article_html", fetch)
    writes = []
    monkeypatch.setattr(scorer, "upsert_article", lambda *args: writes.append(args))
    session = Mock()
    title, snippet = scorer.get_article_content(
        connection, session, "https://fixture.example/article", "fixture-agent"
    )
    assert title == "Fixture" and "Fixture" in snippet
    fetch.assert_called_once_with(
        session,
        "https://fixture.example/article",
        headers={
            "User-Agent": "fixture-agent",
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        },
    )
    session.get.assert_not_called()
    assert len(writes) == 1 and writes[0][-1] is None


def test_pinned_https_keeps_original_sni_and_certificate_verification(
    monkeypatch, tmp_path
):
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=fixture.example",
            "-addext",
            "subjectAltName=DNS:fixture.example",
            "-keyout",
            str(key),
            "-out",
            str(cert),
        ],
        check=True,
        capture_output=True,
    )

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = self.headers["Host"].encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    names = []
    context.set_servername_callback(lambda sock, name, ctx: names.append(name))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    original_context, original_connect = (
        ssl.create_default_context,
        module._connect_peer,
    )
    monkeypatch.setattr(
        module.ssl, "create_default_context", lambda: original_context(cafile=str(cert))
    )
    monkeypatch.setattr(
        module.socket,
        "getaddrinfo",
        lambda host, port, **kw: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", port))
        ],
    )
    monkeypatch.setattr(
        module,
        "_connect_peer",
        lambda peer, port, deadline: original_connect("127.0.0.1", port, deadline),
    )
    try:
        expected = f"fixture.example:{server.server_port}"
        assert (
            module.fetch_article_html(Mock(), f"https://{expected}/", headers={})
            == expected
        )
        assert names == ["fixture.example"]
        with pytest.raises(requests.RequestException, match="certificate"):
            module.fetch_article_html(
                Mock(), f"https://wrong.example:{server.server_port}/", headers={}
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
