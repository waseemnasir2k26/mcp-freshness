"""GitHub mapping, lookup, caching, and rate-limit handling. Fully stubbed."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from mcp_freshness.cache import DiskCache
from mcp_freshness.config import parse_entry
from mcp_freshness.github import GitHubClient, load_map_file, map_repo, normalize_repo

NOW = datetime(2026, 9, 1, tzinfo=timezone.utc)


def repo_payload(days_ago=10, archived=False, issues=7, stars=1200):
    pushed = (NOW - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")
    return {
        "full_name": "owner/name",
        "pushed_at": pushed,
        "archived": archived,
        "open_issues_count": issues,
        "stargazers_count": stars,
        "some_unknown_field": "ignored",
    }


def fetcher(payload, status=200, headers=None):
    calls = []

    def fetch(url, hdrs):
        calls.append((url, dict(hdrs)))
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        return status, headers or {}, body

    fetch.calls = calls
    return fetch


def client(fetch, cache=None, token=None):
    return GitHubClient(fetch=fetch, cache=cache, token=token, now=lambda: NOW)


# ----------------------------------------------------------------- mapping


def test_normalize_repo_accepts_urls_and_slugs():
    assert normalize_repo("https://github.com/owner/name") == "owner/name"
    assert normalize_repo("https://github.com/owner/name.git") == "owner/name"
    assert normalize_repo("git@github.com:owner/name.git") == "owner/name"
    assert normalize_repo("owner/name") == "owner/name"
    assert normalize_repo("not a repo at all") is None


def test_map_repo_from_github_url_in_args():
    e = parse_entry("x", {"command": "uvx", "args": ["--from", "git+https://github.com/acme/tool", "tool"]})
    assert map_repo(e) == "acme/tool"


def test_map_repo_from_known_package_prefix():
    e = parse_entry("files", {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem"]})
    assert map_repo(e) == "modelcontextprotocol/servers"


def test_explicit_override_beats_everything():
    e = parse_entry("files", {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem"]})
    assert map_repo(e, {"files": "https://github.com/me/fork"}) == "me/fork"


def test_unmappable_server_returns_none():
    assert map_repo(parse_entry("mystery", {"command": "./run.sh"})) is None


def test_load_map_file(tmp_path):
    p = tmp_path / "map.json"
    p.write_text(json.dumps({"a": "owner/a"}), encoding="utf-8")
    assert load_map_file(str(p)) == {"a": "owner/a"}

    bad = tmp_path / "bad.json"
    bad.write_text("[1,2]", encoding="utf-8")
    with pytest.raises(ValueError):
        load_map_file(str(bad))


# ----------------------------------------------------------------- lookup


def test_lookup_extracts_freshness_facts():
    c = client(fetcher(repo_payload(days_ago=42)))
    info = c.lookup("owner/name")
    assert info.status == "ok"
    assert info.days_since_push == 42
    assert info.archived is False
    assert info.open_issues == 7
    assert info.stars == 1200


def test_unmapped_repo_is_reported_not_guessed():
    info = client(fetcher(repo_payload())).lookup(None)
    assert info.status == "unmapped"


def test_archived_flag_is_carried_through():
    info = client(fetcher(repo_payload(days_ago=800, archived=True))).lookup("owner/dead")
    assert info.archived is True
    assert info.days_since_push == 800


def test_404_is_not_found():
    info = client(fetcher({"message": "Not Found"}, status=404)).lookup("owner/gone")
    assert info.status == "not_found"


def test_rate_limit_403_is_handled_gracefully():
    f = fetcher({"message": "API rate limit exceeded"}, status=403,
                headers={"X-RateLimit-Remaining": "0"})
    c = client(f)
    info = c.lookup("owner/name")
    assert info.status == "rate_limited"
    assert "GITHUB_TOKEN" in info.detail
    assert c.rate_limited is True


def test_rate_limit_short_circuits_subsequent_lookups():
    f = fetcher({"message": "API rate limit exceeded"}, status=403,
                headers={"X-RateLimit-Remaining": "0"})
    c = client(f)
    c.lookup("owner/one")
    c.lookup("owner/two")
    c.lookup("owner/three")
    # Only the first call hits the API; the rest are short-circuited.
    assert len(f.calls) == 1


def test_rate_limit_message_omits_token_hint_when_token_present():
    f = fetcher({}, status=403, headers={"X-RateLimit-Remaining": "0"})
    info = client(f, token="fake-test-token").lookup("owner/name")
    assert info.status == "rate_limited"
    assert "GITHUB_TOKEN" not in info.detail


def test_403_without_rate_limit_header_is_a_plain_error():
    info = client(fetcher({}, status=403, headers={"X-RateLimit-Remaining": "55"})).lookup("owner/name")
    assert info.status == "error"
    assert "403" in info.detail


def test_network_exception_becomes_error_not_traceback():
    def boom(url, headers):
        raise OSError("network unreachable")

    info = client(boom).lookup("owner/name")
    assert info.status == "error"
    assert "OSError" in info.detail


def test_unparseable_body_is_error():
    info = client(fetcher(b"<html>", status=200)).lookup("owner/name")
    assert info.status == "error"


def test_token_is_sent_as_bearer_when_present():
    f = fetcher(repo_payload())
    client(f, token="fake-test-token").lookup("owner/name")
    _, headers = f.calls[0]
    assert headers["Authorization"] == "Bearer fake-test-token"


def test_no_auth_header_when_no_token():
    f = fetcher(repo_payload())
    client(f, token="").lookup("owner/name")
    _, headers = f.calls[0]
    assert "Authorization" not in headers


# ----------------------------------------------------------------- caching


def test_second_lookup_is_served_from_cache(tmp_path):
    clock = {"t": 1000.0}
    cache = DiskCache(path=tmp_path / "c.json", ttl=3600, clock=lambda: clock["t"])
    f = fetcher(repo_payload(days_ago=5))

    first = client(f, cache=cache).lookup("owner/name")
    second = client(f, cache=cache).lookup("owner/name")

    assert len(f.calls) == 1, "cache did not prevent the second API call"
    assert first.days_since_push == second.days_since_push == 5
    assert second.detail == "from cache"


def test_cache_expires_after_ttl(tmp_path):
    clock = {"t": 1000.0}
    cache = DiskCache(path=tmp_path / "c.json", ttl=60, clock=lambda: clock["t"])
    f = fetcher(repo_payload())

    client(f, cache=cache).lookup("owner/name")
    clock["t"] += 61
    client(f, cache=cache).lookup("owner/name")

    assert len(f.calls) == 2, "expired cache entry was wrongly reused"


def test_cache_survives_a_new_process(tmp_path):
    path = tmp_path / "c.json"
    clock = {"t": 5000.0}
    f = fetcher(repo_payload())
    client(f, cache=DiskCache(path=path, ttl=3600, clock=lambda: clock["t"])).lookup("owner/name")

    # Fresh DiskCache instance = simulated fresh process.
    reloaded = DiskCache(path=path, ttl=3600, clock=lambda: clock["t"])
    client(f, cache=reloaded).lookup("owner/name")
    assert len(f.calls) == 1


def test_no_cache_mode_never_reads_or_writes(tmp_path):
    path = tmp_path / "c.json"
    cache = DiskCache(path=path, ttl=3600, enabled=False)
    f = fetcher(repo_payload())
    client(f, cache=cache).lookup("owner/name")
    client(f, cache=cache).lookup("owner/name")
    assert len(f.calls) == 2
    assert not path.exists()


def test_corrupt_cache_file_is_ignored_not_fatal(tmp_path):
    path = tmp_path / "c.json"
    path.write_text("{{{ not json", encoding="utf-8")
    cache = DiskCache(path=path, ttl=3600)
    assert cache.get("repo:owner/name") is None
    cache.set("repo:owner/name", {"pushed_at": "x"})
    assert cache.get("repo:owner/name") == {"pushed_at": "x"}
