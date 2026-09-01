"""Liveness probes.

stdio: really spawn the server, do the MCP `initialize` handshake, call
`tools/list`, then tear the whole process group down.

http/sse: attempt a Streamable-HTTP `initialize` POST. Anything we cannot
honestly interpret is reported as ``unknown``/``unsupported`` rather than
guessed at.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Optional

from .models import ProbeResult, ServerEntry
from .procgroup import kill_group, spawn_kwargs

PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "mcp-freshness", "version": "0.1.0"}
DEFAULT_TIMEOUT = 10.0


def _initialize_params() -> dict[str, Any]:
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {},
        "clientInfo": dict(CLIENT_INFO),
    }


# --------------------------------------------------------------------------- stdio


class _LineReader:
    """Background stdout reader so a silent server cannot block us."""

    def __init__(self, stream) -> None:
        self._q: "queue.Queue[Optional[str]]" = queue.Queue()
        self.eof = False  # True once the server closed stdout
        self._thread = threading.Thread(target=self._run, args=(stream,), daemon=True)
        self._thread.start()

    def _run(self, stream) -> None:
        try:
            for line in stream:
                self._q.put(line)
        except Exception:  # noqa: BLE001 - pipe closed under us
            pass
        finally:
            self._q.put(None)

    def next_line(self, timeout: float) -> Optional[str]:
        try:
            line = self._q.get(timeout=max(timeout, 0.0))
        except queue.Empty:
            return None
        if line is None:
            self.eof = True
        return line


def _read_response(reader: _LineReader, want_id: int, deadline: float) -> Optional[dict[str, Any]]:
    """Pull lines until we see the JSON-RPC response with ``want_id``.

    Non-JSON chatter and unrelated notifications are skipped, which keeps
    noisy-but-working servers from being scored as dead.
    """
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        line = reader.next_line(remaining)
        if line is None:
            return None
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(msg, dict) and msg.get("id") == want_id:
            return msg


def probe_stdio(entry: ServerEntry, timeout: float = DEFAULT_TIMEOUT) -> ProbeResult:
    if not entry.command:
        return ProbeResult(liveness="unreachable", detail="no command in config")

    env = dict(os.environ)
    env.update(entry.env)
    env.setdefault("PYTHONUNBUFFERED", "1")

    argv = [entry.command, *entry.args]
    start = time.monotonic()
    deadline = start + timeout

    try:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            **spawn_kwargs(),
        )
    except FileNotFoundError:
        return ProbeResult(liveness="unreachable", detail="command not found: " + str(entry.command))
    except OSError as exc:
        return ProbeResult(liveness="unreachable", detail="spawn failed: {0}".format(exc))

    reader = _LineReader(proc.stdout)
    try:
        def send(payload: dict[str, Any]) -> bool:
            try:
                if proc.stdin is None:
                    return False
                proc.stdin.write(json.dumps(payload) + "\n")
                proc.stdin.flush()
                return True
            except (BrokenPipeError, OSError, ValueError):
                return False

        if not send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": _initialize_params()}):
            return ProbeResult(liveness="unreachable", detail="server closed stdin during initialize")

        init = _read_response(reader, 1, deadline)
        if init is None:
            # EOF on stdout means the server died or hung up. Give the OS a
            # moment to reap it so we can report the real exit code instead of
            # mislabelling a crash as a timeout.
            if reader.eof:
                try:
                    rc = proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    rc = None
                if rc is not None:
                    return ProbeResult(
                        liveness="unreachable",
                        detail="server exited with code {0} before completing the handshake".format(rc),
                    )
                return ProbeResult(
                    liveness="unreachable",
                    detail="server closed stdout before completing the handshake",
                )
            if proc.poll() is not None:
                return ProbeResult(
                    liveness="unreachable",
                    detail="server exited with code {0} before completing the handshake".format(
                        proc.returncode
                    ),
                )
            return ProbeResult(
                liveness="timeout",
                detail="no initialize response within {0:.0f}s".format(timeout),
            )
        if "error" in init:
            err = init.get("error") or {}
            return ProbeResult(
                liveness="unreachable",
                detail="initialize error: {0}".format(err.get("message", err)),
            )

        latency_ms = (time.monotonic() - start) * 1000.0
        result = init.get("result") or {}
        info = result.get("serverInfo") or {}

        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        tools_msg = _read_response(reader, 2, deadline)

        tool_count: Optional[int] = None
        detail = ""
        if tools_msg is None:
            detail = "handshake ok, tools/list did not answer in time"
        elif "error" in tools_msg:
            detail = "handshake ok, tools/list returned an error"
            tool_count = 0
        else:
            tools = (tools_msg.get("result") or {}).get("tools")
            tool_count = len(tools) if isinstance(tools, list) else 0

        return ProbeResult(
            liveness="reachable",
            latency_ms=round(latency_ms, 1),
            tool_count=tool_count,
            server_name=info.get("name"),
            server_version=info.get("version"),
            protocol_version=result.get("protocolVersion"),
            detail=detail,
        )
    finally:
        kill_group(proc)


# ---------------------------------------------------------------------------- http

HttpFetch = Callable[[str, dict, bytes, float], tuple]


def urllib_post(url: str, headers: dict, body: bytes, timeout: float):
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return resp.status, dict(resp.headers), resp.read()


def probe_http(
    entry: ServerEntry,
    timeout: float = DEFAULT_TIMEOUT,
    fetch: Optional[HttpFetch] = None,
) -> ProbeResult:
    if not entry.url:
        return ProbeResult(liveness="unreachable", detail="no url in config")

    fetch = fetch or urllib_post
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": PROTOCOL_VERSION,
    }
    headers.update(entry.headers)
    body = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": _initialize_params()}
    ).encode("utf-8")

    start = time.monotonic()
    try:
        status, resp_headers, payload = fetch(entry.url, headers, body, timeout)
    except urllib.error.HTTPError as exc:
        code = exc.code
        if code in (401, 403):
            return ProbeResult(
                liveness="unsupported",
                detail="endpoint answered HTTP {0} - credentials required, cannot judge liveness".format(code),
            )
        return ProbeResult(liveness="unreachable", detail="HTTP {0}".format(code))
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, TimeoutError) or "timed out" in str(reason).lower():
            return ProbeResult(liveness="timeout", detail="no response within {0:.0f}s".format(timeout))
        return ProbeResult(liveness="unreachable", detail="connection failed: {0}".format(reason))
    except Exception as exc:  # noqa: BLE001 - never traceback on a transport quirk
        return ProbeResult(
            liveness="unknown",
            detail="probe failed: {0}: {1}".format(type(exc).__name__, exc),
        )

    latency_ms = round((time.monotonic() - start) * 1000.0, 1)

    if status in (401, 403):
        return ProbeResult(
            liveness="unsupported",
            latency_ms=latency_ms,
            detail="endpoint answered HTTP {0} - credentials required, cannot judge liveness".format(status),
        )
    if status >= 400:
        return ProbeResult(liveness="unreachable", latency_ms=latency_ms, detail="HTTP {0}".format(status))

    msg = _extract_json_rpc(payload, resp_headers)
    if msg is None:
        return ProbeResult(
            liveness="unknown",
            latency_ms=latency_ms,
            detail="HTTP {0} but no JSON-RPC initialize result could be parsed".format(status),
        )
    if "error" in msg:
        err = msg.get("error") or {}
        return ProbeResult(
            liveness="unreachable",
            latency_ms=latency_ms,
            detail="initialize error: {0}".format(err.get("message", err)),
        )

    result = msg.get("result") or {}
    info = result.get("serverInfo") or {}
    return ProbeResult(
        liveness="reachable",
        latency_ms=latency_ms,
        tool_count=None,
        server_name=info.get("name"),
        server_version=info.get("version"),
        protocol_version=result.get("protocolVersion"),
        detail="handshake ok (tool count not collected over http)",
    )


def _extract_json_rpc(payload: bytes, headers: dict) -> Optional[dict]:
    """Parse either a plain JSON body or the first SSE data frame."""
    text = payload.decode("utf-8", errors="replace").strip()
    if not text:
        return None
    ctype = ""
    for k, v in (headers or {}).items():
        if str(k).lower() == "content-type":
            ctype = str(v).lower()
            break
    if "text/event-stream" in ctype or text.startswith("event:") or text.startswith("data:"):
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                try:
                    obj = json.loads(line[5:].strip())
                except (json.JSONDecodeError, ValueError):
                    continue
                if isinstance(obj, dict):
                    return obj
        return None
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def probe(
    entry: ServerEntry,
    timeout: float = DEFAULT_TIMEOUT,
    fetch: Optional[HttpFetch] = None,
) -> ProbeResult:
    if entry.transport == "stdio":
        return probe_stdio(entry, timeout)
    if entry.transport in ("http", "sse"):
        return probe_http(entry, timeout, fetch)
    return ProbeResult(
        liveness="unsupported",
        detail="transport '{0}' cannot be probed; declare type/command/url".format(entry.transport),
    )
