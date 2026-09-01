"""Config discovery + parsing across every shape we claim to support."""

from __future__ import annotations

import json

import pytest

from mcp_freshness.config import (
    ConfigError,
    discover_config_files,
    load_config_file,
    load_servers,
    parse_config_data,
    parse_entry,
)


def write(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_parses_mcpservers_stdio(tmp_path):
    p = write(tmp_path / ".mcp.json", {
        "mcpServers": {
            "files": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]}
        }
    })
    entries = load_config_file(p)
    assert len(entries) == 1
    e = entries[0]
    assert e.name == "files"
    assert e.transport == "stdio"
    assert e.command == "npx"
    assert e.args == ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]


def test_parses_http_and_sse_transports(tmp_path):
    p = write(tmp_path / ".mcp.json", {
        "mcpServers": {
            "remote": {"type": "http", "url": "https://example.test/mcp"},
            "streamy": {"url": "https://example.test/sse"},
            "declared": {"transport": "streamable-http", "url": "https://example.test/x"},
        }
    })
    by_name = {e.name: e for e in load_config_file(p)}
    assert by_name["remote"].transport == "http"
    assert by_name["streamy"].transport == "sse"          # inferred from /sse suffix
    assert by_name["declared"].transport == "http"        # alias normalised
    assert by_name["remote"].url == "https://example.test/mcp"


def test_accepts_servers_key_and_bare_map():
    assert [e.name for e in parse_config_data({"servers": {"a": {"command": "x"}}})] == ["a"]
    assert [e.name for e in parse_config_data({"b": {"command": "y"}})] == ["b"]


def test_claude_json_projects_nesting():
    data = {"projects": {"/home/w/proj": {"mcpServers": {"deep": {"command": "z"}}}}}
    entries = parse_config_data(data, source="claude.json")
    assert [e.name for e in entries] == ["deep"]
    assert entries[0].source == "claude.json#/home/w/proj"


def test_tolerates_unknown_keys_and_odd_values():
    e = parse_entry("weird", {
        "command": "run-me",
        "args": "single-string-arg",
        "somethingNew": {"deeply": "nested"},
        "env": {"TOKEN": "abc", "NULLED": None},
    })
    assert e.args == ["single-string-arg"]
    assert e.env == {"TOKEN": "abc"}
    assert e.raw["somethingNew"] == {"deeply": "nested"}

    broken = parse_entry("broken", "not-an-object")
    assert broken.transport == "unknown"


def test_command_as_list_is_split():
    e = parse_entry("listy", {"command": ["npx", "-y", "pkg"], "args": ["--flag"]})
    assert e.command == "npx"
    assert e.args == ["-y", "pkg", "--flag"]


def test_env_and_headers_are_masked_in_output():
    e = parse_entry("secretive", {"command": "x", "env": {"API_KEY": "super-secret"},
                                  "headers": {"Authorization": "Bearer nope"}})
    d = e.to_dict()
    assert d["env"] == {"API_KEY": "<set>"}
    assert d["headers"] == {"Authorization": "<set>"}
    assert "super-secret" not in json.dumps(d)


def test_bad_json_raises_config_error(tmp_path):
    p = tmp_path / "broken.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config_file(p)


def test_load_servers_reports_bad_file_as_warning_not_crash(tmp_path):
    good = write(tmp_path / "a.json", {"mcpServers": {"ok": {"command": "x"}}})
    bad = tmp_path / "b.json"
    bad.write_text("{", encoding="utf-8")
    entries, warnings = load_servers([good, bad])
    assert [e.name for e in entries] == ["ok"]
    assert any("not valid JSON" in w for w in warnings)


def test_duplicate_names_first_wins(tmp_path):
    a = write(tmp_path / "a.json", {"mcpServers": {"dup": {"command": "first"}}})
    b = write(tmp_path / "b.json", {"mcpServers": {"dup": {"command": "second"}}})
    entries, warnings = load_servers([a, b])
    assert len(entries) == 1
    assert entries[0].command == "first"
    assert any("duplicate" in w for w in warnings)


def test_discovery_finds_dot_mcp_json(tmp_path, monkeypatch):
    write(tmp_path / ".mcp.json", {"mcpServers": {"found": {"command": "x"}}})
    found = discover_config_files(cwd=tmp_path, home=tmp_path / "nohome")
    assert (tmp_path / ".mcp.json") in found


def test_discovery_returns_empty_when_nothing_exists(tmp_path):
    assert discover_config_files(cwd=tmp_path / "empty", home=tmp_path / "nohome") == []
