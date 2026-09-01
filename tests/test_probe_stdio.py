"""Real subprocess probes against the fake MCP server fixtures.

These spawn local Python processes only - no network."""

from __future__ import annotations

import time

import pytest

from mcp_freshness.models import ServerEntry
from mcp_freshness.probe import probe, probe_stdio
from mcp_freshness.procgroup import is_alive


def test_healthy_server_is_reachable_with_tools(make_entry):
    r = probe_stdio(make_entry("healthy", "healthy_server.py"), timeout=15)
    assert r.liveness == "reachable"
    assert r.tool_count == 3
    assert r.server_name == "fixture-healthy"
    assert r.server_version == "1.2.3"
    assert r.protocol_version == "2025-06-18"
    assert r.latency_ms is not None and r.latency_ms > 0


def test_zero_tool_server_handshakes_but_reports_no_tools(make_entry):
    r = probe_stdio(make_entry("empty", "zero_tools_server.py"), timeout=15)
    assert r.liveness == "reachable"
    assert r.tool_count == 0


def test_noisy_stdout_does_not_break_the_handshake(make_entry):
    r = probe_stdio(make_entry("noisy", "noisy_server.py"), timeout=15)
    assert r.liveness == "reachable"
    assert r.tool_count == 1


def test_crashing_server_is_unreachable(make_entry):
    r = probe_stdio(make_entry("crasher", "crash_server.py"), timeout=15)
    assert r.liveness == "unreachable"
    assert r.tool_count is None
    assert "exited" in r.detail or "stdin" in r.detail


def test_initialize_error_is_unreachable(make_entry):
    r = probe_stdio(make_entry("rejector", "init_error_server.py"), timeout=15)
    assert r.liveness == "unreachable"
    assert "initialize error" in r.detail


def test_missing_command_is_unreachable_not_a_traceback():
    entry = ServerEntry(name="ghost", transport="stdio",
                        command="definitely-not-a-real-binary-xyz", args=[])
    r = probe_stdio(entry, timeout=5)
    assert r.liveness == "unreachable"
    assert "not found" in r.detail or "spawn failed" in r.detail


def test_no_command_configured_is_unreachable():
    r = probe_stdio(ServerEntry(name="empty", transport="stdio"), timeout=1)
    assert r.liveness == "unreachable"
    assert "no command" in r.detail


def test_slow_server_trips_the_timeout_and_returns_promptly(make_entry):
    timeout = 2.0
    start = time.monotonic()
    r = probe_stdio(make_entry("slow", "slow_server.py"), timeout=timeout)
    elapsed = time.monotonic() - start

    assert r.liveness == "timeout"
    assert str(int(timeout)) in r.detail
    # The hard timeout is the point: the tool must not hang.
    assert elapsed < timeout + 20, "probe overran its own timeout by too much"


def test_timeout_kills_the_whole_process_group(make_entry, tmp_path):
    """A naive terminate() leaves grandchildren running. Prove we kill them."""
    pidfile = tmp_path / "grandchild.pid"
    entry = make_entry("slow", "slow_server.py", env={"MCPF_CHILD_PIDFILE": str(pidfile)})

    r = probe_stdio(entry, timeout=3)
    assert r.liveness == "timeout"

    assert pidfile.is_file(), "fixture never recorded its grandchild pid"
    pid = int(pidfile.read_text(encoding="utf-8").strip())

    # Give the OS a moment to reap the tree.
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and is_alive(pid):
        time.sleep(0.25)

    assert not is_alive(pid), "grandchild pid {0} survived the probe".format(pid)


def test_probe_dispatches_unknown_transport_to_unsupported():
    r = probe(ServerEntry(name="mystery", transport="unknown"), timeout=1)
    assert r.liveness == "unsupported"
    assert "cannot be probed" in r.detail
