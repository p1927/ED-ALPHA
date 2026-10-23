"""One public-article transport: checked peers, original TLS identity, bounded time/body."""

from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import time
import zlib
from queue import Empty, Queue
from threading import BoundedSemaphore, Thread, Timer
from urllib.parse import urljoin, urlsplit

import requests

MAX_ARTICLE_BYTES = 4 * 1024 * 1024
MAX_REDIRECTS = 5
ARTICLE_TIMEOUT_SECONDS = 30.0
_INTERNAL_SUFFIXES = (
    ".localhost",
    ".local",
    ".internal",
    ".localdomain",
    ".lan",
    ".home.arpa",
)
# ponytail: bound potentially slow OS resolvers to four daemon workers; timed-out
# lookups cannot grow an unbounded pool or prevent interpreter shutdown.
_DNS_SLOTS = BoundedSemaphore(4)


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise requests.Timeout("article total deadline exceeded")
    return remaining


def _resolve(host: str, port: int, deadline: float) -> list[str]:
    if not _DNS_SLOTS.acquire(timeout=_remaining(deadline)):
        raise requests.Timeout("article DNS deadline exceeded")
    answer: Queue = Queue(maxsize=1)

    def work():
        try:
            rows = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            answer.put(([row[4][0].split("%")[0] for row in rows], None))
        except Exception as exc:
            answer.put((None, exc))
        finally:
            _DNS_SLOTS.release()

    Thread(target=work, daemon=True).start()
    try:
        addresses, error = answer.get(timeout=_remaining(deadline))
    except Empty as exc:
        raise requests.Timeout("article DNS deadline exceeded") from exc
    if error is not None:
        raise requests.RequestException(
            f"article DNS lookup failed: {error}"
        ) from error
    return addresses


def _target(url: str, deadline: float) -> tuple[str, str, int, list[str], str]:
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").rstrip(".").lower()
        if (
            parts.scheme not in ("http", "https")
            or not host
            or parts.username is not None
            or parts.password is not None
            or host == "localhost"
            or host.endswith(_INTERNAL_SUFFIXES)
            or "%" in host
        ):
            raise ValueError("article URL is not a public HTTP(S) address")
        port = (
            parts.port
            if parts.port is not None
            else (443 if parts.scheme == "https" else 80)
        )
        if not 0 < port <= 65535:
            raise ValueError("article URL has an invalid port")
        try:
            addresses = [str(ipaddress.ip_address(host))]
        except ValueError:
            try:
                addresses = [str(ipaddress.ip_address(socket.inet_aton(host)))]
            except OSError:
                if "." not in host:
                    raise ValueError("article URL is not a public HTTP(S) address")
                addresses = _resolve(host, port, deadline)
        if not addresses or not all(
            (getattr(ip, "ipv4_mapped", None) or ip).is_global
            for ip in map(ipaddress.ip_address, addresses)
        ):
            raise ValueError("article URL is not a public HTTP(S) address")
        path = (parts.path or "/") + ("?" + parts.query if parts.query else "")
        return parts.scheme, host, port, addresses, path
    except requests.RequestException:
        raise
    except (ValueError, OSError) as exc:
        raise requests.RequestException(str(exc)) from exc


def public_article_url(url: str) -> bool:
    try:
        _target(url, time.monotonic() + ARTICLE_TIMEOUT_SECONDS)
        return True
    except requests.RequestException:
        return False


def _connect_peer(peer: str, port: int, deadline: float) -> socket.socket:
    family = (
        socket.AF_INET6 if ipaddress.ip_address(peer).version == 6 else socket.AF_INET
    )
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        sock.settimeout(_remaining(deadline))
        sock.connect((peer, port))  # literal address: no second hostname resolution
        sock.settimeout(
            _remaining(deadline)
        )  # TCP time is deducted before the TLS handshake
        return sock
    except BaseException:
        sock.close()
        raise


def fetch_article_html(
    session: requests.Session, url: str, *, headers: dict[str, str]
) -> str:
    """One deadline for DNS, redirects, TLS, headers and decompressed body.

    Retain the scorer's call contract; never forward session proxy/cookie state
    to arbitrary article hosts. http.client preserves Host and TLS verification.
    """
    del session
    deadline = time.monotonic() + ARTICLE_TIMEOUT_SECONDS
    try:
        for hop in range(MAX_REDIRECTS + 1):
            scheme, host, port, addresses, path = _target(url, deadline)
            connection = (
                http.client.HTTPSConnection(
                    host,
                    port,
                    timeout=_remaining(deadline),
                    context=ssl.create_default_context(),
                )
                if scheme == "https"
                else http.client.HTTPConnection(
                    host, port, timeout=_remaining(deadline)
                )
            )
            connection._create_connection = lambda *_a, **_kw: _connect_peer(
                addresses[0], port, deadline
            )
            watchdog = None
            try:
                connection.connect()
                peer_socket = connection.sock

                def interrupt():
                    try:
                        peer_socket.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass

                watchdog = Timer(_remaining(deadline), interrupt)
                watchdog.daemon = True
                watchdog.start()
                connection.request("GET", path, headers=headers)
                with connection.getresponse() as response:
                    if response.status in (301, 302, 303, 307, 308):
                        location = response.getheader("Location")
                        if not location or hop == MAX_REDIRECTS:
                            raise requests.RequestException(
                                "article redirect missing a target or exceeds limit"
                            )
                        url = urljoin(url, location)
                        continue
                    if response.status >= 400:
                        error_response = requests.Response()
                        error_response.status_code = response.status
                        raise requests.HTTPError(
                            f"HTTP {response.status}", response=error_response
                        )
                    declared = int(response.getheader("Content-Length", "0"))
                    if declared < 0 or declared > MAX_ARTICLE_BYTES:
                        raise requests.RequestException(
                            "article body exceeds byte limit"
                        )
                    encoding = response.getheader(
                        "Content-Encoding", "identity"
                    ).lower()
                    if encoding not in ("identity", "gzip", "deflate"):
                        raise requests.RequestException(
                            f"unsupported article content encoding: {encoding}"
                        )
                    decoder = (
                        zlib.decompressobj(31 if encoding == "gzip" else 15)
                        if encoding != "identity"
                        else None
                    )
                    body = bytearray()
                    while True:
                        _remaining(deadline)
                        chunk = response.read1(64 * 1024)
                        if not chunk:
                            break
                        if decoder is not None:
                            chunk = decoder.decompress(
                                chunk, MAX_ARTICLE_BYTES - len(body) + 1
                            )
                        if len(body) + len(chunk) > MAX_ARTICLE_BYTES:
                            raise requests.RequestException(
                                "article body exceeds byte limit"
                            )
                        body.extend(chunk)
                    _remaining(deadline)
                    if decoder is not None and (not decoder.eof or decoder.unused_data):
                        raise requests.RequestException(
                            "article compressed body is incomplete or has trailing data"
                        )
                    charset = response.headers.get_content_charset() or "utf-8"
                    try:
                        return bytes(body).decode(charset, errors="replace")
                    except LookupError:
                        return bytes(body).decode("utf-8", errors="replace")
            finally:
                if watchdog is not None:
                    watchdog.cancel()
                connection.close()
    except requests.RequestException:
        raise
    except (OSError, ValueError, http.client.HTTPException, zlib.error) as exc:
        if time.monotonic() >= deadline:
            raise requests.Timeout("article total deadline exceeded") from exc
        raise requests.RequestException(f"article fetch failed: {exc}") from exc
    raise requests.RequestException("article redirect exceeds limit")
