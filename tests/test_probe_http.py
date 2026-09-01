"""HTTP/SSE probes with an injected transport. No sockets are opened."""

from __future__ import annotations

import json
import urllib.error

from mcp_freshness.models import ServerEntry
from mcp_freshness.probe import probe_http

OK_BODY = json.dumps({
    "jsonrpc": "2.0", "id": 1,
    "result": {"protocolVersion": "2025-06-18", "serverInfo": {"name": "remote", "version": "2.0"}},
}).encode()


def entry(url="https://example.test/mcp", **kw):
    return ServerEntry(name="remote", transport="http", url=url, **kw)


def test_json_handshake_is_reachable():
    def fetch(url, headers, body, timeout):
        assert url == "https://example.test/mcp"
        assert json.loads(body)["method"] == "initialize"
        return 200, {"Content-Type": "application/json"}, OK_BODY

    r = probe_http(entry(), timeout=5, fetch=fetch)
    assert r.liveness == "reachable"
    assert r.server_name == "remote"
    assert r.tool_count is None       # honestly not collected over http


def test_sse_framed_response_is_parsed():
    sse = b"event: message\ndata: " + OK_BODY + b"\n\n"

    def fetch(url, headers, body, timeout):
        return 200, {"Content-Type": "text/event-stream"}, sse

    r = probe_http(entry("https://example.test/sse"), timeout=5, fetch=fetch)
    assert r.liveness == "reachable"
    assert r.server_version == "2.0"


def test_auth_required_degrades_to_unsupported_not_dead():
    def fetch(url, headers, body, timeout):
        return 401, {}, b"unauthorized"

    r = probe_http(entry(), timeout=5, fetch=fetch)
    assert r.liveness == "unsupported"
    assert "credentials required" in r.detail


def test_http_error_status_is_unreachable():
    def fetch(url, headers, body, timeout):
        return 502, {}, b"bad gateway"

    r = probe_http(entry(), timeout=5, fetch=fetch)
    assert r.liveness == "unreachable"
    assert "502" in r.detail


def test_connection_refused_is_unreachable():
    def fetch(url, headers, body, timeout):
        raise urllib.error.URLError(ConnectionRefusedError("connection refused"))

    r = probe_http(entry(), timeout=5, fetch=fetch)
    assert r.liveness == "unreachable"
    assert "connection failed" in r.detail


def test_socket_timeout_is_reported_as_timeout():
    def fetch(url, headers, body, timeout):
        raise urllib.error.URLError(TimeoutError("timed out"))

    r = probe_http(entry(), timeout=7, fetch=fetch)
    assert r.liveness == "timeout"
    assert "7s" in r.detail


def test_unparseable_body_is_unknown_not_a_guess():
    def fetch(url, headers, body, timeout):
        return 200, {"Content-Type": "text/html"}, b"<html>hello</html>"

    r = probe_http(entry(), timeout=5, fetch=fetch)
    assert r.liveness == "unknown"


def test_jsonrpc_error_is_unreachable():
    payload = json.dumps({"jsonrpc": "2.0", "id": 1,
                          "error": {"code": -32600, "message": "nope"}}).encode()

    def fetch(url, headers, body, timeout):
        return 200, {"Content-Type": "application/json"}, payload

    r = probe_http(entry(), timeout=5, fetch=fetch)
    assert r.liveness == "unreachable"
    assert "nope" in r.detail


def test_custom_headers_are_forwarded():
    seen = {}

    def fetch(url, headers, body, timeout):
        seen.update(headers)
        return 200, {"Content-Type": "application/json"}, OK_BODY

    probe_http(entry(headers={"Authorization": "Bearer test-value"}), timeout=5, fetch=fetch)
    assert seen["Authorization"] == "Bearer test-value"
    assert seen["MCP-Protocol-Version"] == "2025-06-18"


def test_missing_url_is_unreachable():
    r = probe_http(ServerEntry(name="x", transport="http"), timeout=1, fetch=None)
    assert r.liveness == "unreachable"
    assert "no url" in r.detail


def test_unexpected_transport_exception_never_tracebacks():
    def fetch(url, headers, body, timeout):
        raise RuntimeError("something exotic")

    r = probe_http(entry(), timeout=5, fetch=fetch)
    assert r.liveness == "unknown"
    assert "RuntimeError" in r.detail
