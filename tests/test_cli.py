"""End-to-end CLI runs against fixture servers, with GitHub fully stubbed."""

from __future__ import annotations

import io
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from mcp_freshness import cli, github as gh_mod
from mcp_freshness.models import ProbeResult, RepoInfo, ServerEntry, ServerReport
from mcp_freshness.render import render_summary, render_table
from mcp_freshness.runner import run

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 9, 1, tzinfo=timezone.utc)


def mixed_config(tmp_path: Path) -> Path:
    cfg = {
        "mcpServers": {
            "filesystem": {"command": sys.executable, "args": [str(FIXTURES / "healthy_server.py")]},
            "empty-tools": {"command": sys.executable, "args": [str(FIXTURES / "zero_tools_server.py")]},
            "abandoned": {"command": sys.executable, "args": [str(FIXTURES / "crash_server.py")]},
            "remote-api": {"type": "http", "url": "https://example.test/mcp"},
        }
    }
    p = tmp_path / ".mcp.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    return p


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    """Hard guarantee: this suite never touches the network."""
    def boom(*a, **k):  # pragma: no cover
        raise AssertionError("test tried to make a real HTTP request")

    monkeypatch.setattr(gh_mod, "urllib_get", boom)
    monkeypatch.setattr("urllib.request.urlopen", boom)


@pytest.fixture
def stub_github(monkeypatch):
    """Every GitHub lookup answers from a table, no sockets."""
    table = {
        "modelcontextprotocol/servers": (12, False),
        "acme/abandoned-server": (900, True),
    }

    def fake_lookup(self, repo):
        if not repo:
            return RepoInfo(status="unmapped", detail="no GitHub repo could be resolved")
        if repo not in table:
            return RepoInfo(repo=repo, status="not_found", detail="repo not found or private")
        days, archived = table[repo]
        pushed = (NOW - timedelta(days=days)).isoformat().replace("+00:00", "Z")
        return RepoInfo(repo=repo, pushed_at=pushed, days_since_push=days,
                        archived=archived, open_issues=3, stars=100, status="ok")

    monkeypatch.setattr(gh_mod.GitHubClient, "lookup", fake_lookup)
    return table


def call(argv):
    out, err = io.StringIO(), io.StringIO()
    code = cli.main(argv, stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def test_list_mode(tmp_path):
    cfg = mixed_config(tmp_path)
    code, out, _ = call(["--config", str(cfg), "--list"])
    assert code == 0
    assert "filesystem" in out
    assert "remote-api\thttp" in out


def test_table_output_covers_every_health_state(tmp_path, stub_github):
    cfg = mixed_config(tmp_path)
    code, out, _ = call(["--config", str(cfg), "--timeout", "15", "--no-cache"])
    assert code == 0
    for name in ("filesystem", "empty-tools", "abandoned", "remote-api"):
        assert name in out
    assert "SCORE" in out and "GRADE" in out
    assert "reachable" in out or "live" in out
    assert "server(s)" in out


def test_json_output_is_machine_readable_and_leaks_no_secrets(tmp_path, stub_github):
    cfg_data = {
        "mcpServers": {
            "healthy": {
                "command": sys.executable,
                "args": [str(FIXTURES / "healthy_server.py")],
                "env": {"MY_API_KEY": "sk-do-not-leak-me"},
            }
        }
    }
    cfg = tmp_path / ".mcp.json"
    cfg.write_text(json.dumps(cfg_data), encoding="utf-8")

    code, out, _ = call(["--config", str(cfg), "--timeout", "15", "--no-cache", "--json"])
    assert code == 0
    data = json.loads(out)
    assert data["servers_total"] == 1
    assert data["servers_reachable"] == 1
    assert data["servers"][0]["probe"]["tool_count"] == 3
    assert data["overall_score"] is not None
    assert "weights" in data
    assert "sk-do-not-leak-me" not in out
    assert data["servers"][0]["server"]["env"] == {"MY_API_KEY": "<set>"}


def test_badge_to_stdout(tmp_path, stub_github):
    cfg = mixed_config(tmp_path)
    code, out, _ = call(["--config", str(cfg), "--timeout", "15", "--no-cache", "--badge"])
    assert code == 0
    payload = json.loads(out)
    assert payload["schemaVersion"] == 1
    assert "/100" in payload["message"]


def test_badge_to_file(tmp_path, stub_github):
    cfg = mixed_config(tmp_path)
    dest = tmp_path / "out" / "badge.json"
    code, out, _ = call(["--config", str(cfg), "--timeout", "15", "--no-cache", "--badge", str(dest)])
    assert code == 0
    assert dest.is_file()
    assert json.loads(dest.read_text(encoding="utf-8"))["schemaVersion"] == 1
    assert "badge written to" in out


def test_static_badge_style(tmp_path, stub_github):
    cfg = mixed_config(tmp_path)
    code, out, _ = call(["--config", str(cfg), "--timeout", "15", "--no-cache",
                         "--badge", "--badge-style", "static"])
    assert code == 0
    assert "https://img.shields.io/badge/" in out
    assert "![MCP freshness]" in out


def test_fail_under_returns_exit_1(tmp_path, stub_github):
    cfg = mixed_config(tmp_path)
    code, _, _ = call(["--config", str(cfg), "--timeout", "15", "--no-cache", "--fail-under", "100"])
    assert code == 1
    code, _, _ = call(["--config", str(cfg), "--timeout", "15", "--no-cache", "--fail-under", "0"])
    assert code == 0


def test_only_filter(tmp_path, stub_github):
    cfg = mixed_config(tmp_path)
    code, out, _ = call(["--config", str(cfg), "--timeout", "15", "--no-cache",
                         "--only", "filesystem", "--json"])
    assert code == 0
    data = json.loads(out)
    assert [s["name"] for s in data["servers"]] == ["filesystem"]


def test_no_probe_mode_skips_spawning(tmp_path, stub_github):
    cfg = mixed_config(tmp_path)
    code, out, _ = call(["--config", str(cfg), "--no-probe", "--no-cache", "--json"])
    assert code == 0
    data = json.loads(out)
    assert all(s["probe"]["liveness"] == "unknown" for s in data["servers"])


def test_no_github_mode(tmp_path):
    cfg = mixed_config(tmp_path)
    code, out, _ = call(["--config", str(cfg), "--timeout", "15", "--no-github", "--json"])
    assert code == 0
    data = json.loads(out)
    assert all(s["repo"]["status"] == "unmapped" for s in data["servers"])


def test_explain_prints_reasons(tmp_path, stub_github):
    cfg = mixed_config(tmp_path)
    code, out, _ = call(["--config", str(cfg), "--timeout", "15", "--no-cache", "--explain"])
    assert code == 0
    assert "    - " in out


def test_map_file_is_applied(tmp_path, stub_github):
    cfg = tmp_path / ".mcp.json"
    cfg.write_text(json.dumps({"mcpServers": {
        "abandoned": {"command": sys.executable, "args": [str(FIXTURES / "crash_server.py")]}
    }}), encoding="utf-8")
    mapfile = tmp_path / "map.json"
    mapfile.write_text(json.dumps({"abandoned": "acme/abandoned-server"}), encoding="utf-8")

    code, out, _ = call(["--config", str(cfg), "--timeout", "15", "--no-cache",
                         "--map", str(mapfile), "--json"])
    assert code == 0
    server = json.loads(out)["servers"][0]
    assert server["repo"]["repo"] == "acme/abandoned-server"
    assert server["repo"]["archived"] is True
    assert server["grade"] == "dead"


def test_bad_map_file_is_a_usage_error_not_a_traceback(tmp_path):
    cfg = mixed_config(tmp_path)
    bad = tmp_path / "bad.json"
    bad.write_text("nope", encoding="utf-8")
    code, _, err = call(["--config", str(cfg), "--map", str(bad)])
    assert code == 2
    assert "bad --map file" in err


def test_missing_config_reports_cleanly(tmp_path):
    code, _, err = call(["--config", str(tmp_path / "nothing.json")])
    assert code == 2
    assert "No MCP servers found" in err or "cannot read" in err


def test_version_flag():
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0


# ------------------------------------------------------- runner / rendering


def test_runner_injects_a_stub_probe():
    entries = [ServerEntry(name="a", transport="stdio", command="x")]
    seen = []

    def fake_probe(entry, timeout):
        seen.append((entry.name, timeout))
        return ProbeResult(liveness="reachable", latency_ms=10.0, tool_count=2)

    reports = run(entries, probe_fn=fake_probe, skip_github=True, timeout=3.5, concurrency=1)
    assert seen == [("a", 3.5)]
    assert reports[0].score > 0


def test_render_table_and_summary_shape():
    rep = ServerReport(
        entry=ServerEntry(name="srv", transport="stdio"),
        probe=ProbeResult(liveness="reachable", latency_ms=42.0, tool_count=2),
        repo=RepoInfo(repo="o/n", days_since_push=3, archived=True, status="ok"),
        score=55, grade="stale", reasons=["x"],
    )
    table = render_table([rep])
    assert "srv" in table and "42ms" in table and "ARCHIVED" in table
    assert "1 server(s)" in render_summary([rep], 55)
