"""Best-effort GitHub repo mapping + freshness lookup.

The network call sits behind an injectable ``fetch`` callable so the test
suite can exercise every branch with zero network.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from .cache import DiskCache
from .models import RepoInfo, ServerEntry

API_ROOT = "https://api.github.com/repos/"

# (status, headers, body-bytes)
Fetch = Callable[[str, dict], tuple]

_GITHUB_URL_RE = re.compile(
    r"github\.com[/:]+([A-Za-z0-9][A-Za-z0-9._-]*)/([A-Za-z0-9][A-Za-z0-9._-]*?)(?:\.git)?(?:[/#?]|$)",
    re.IGNORECASE,
)

# npm/PyPI package prefixes we can map with confidence.
_PACKAGE_MAP: dict[str, str] = {
    "@modelcontextprotocol/": "modelcontextprotocol/servers",
    "mcp-server-": "modelcontextprotocol/servers",
    "@playwright/mcp": "microsoft/playwright-mcp",
    "@upstash/context7-mcp": "upstash/context7",
    "@github/github-mcp-server": "github/github-mcp-server",
    "figma-developer-mcp": "GLips/Figma-Context-MCP",
}


def _candidate_strings(entry: ServerEntry) -> list[str]:
    parts: list[str] = []
    if entry.command:
        parts.append(entry.command)
    parts.extend(entry.args)
    if entry.url:
        parts.append(entry.url)
    for key in ("repository", "repo", "homepage", "source", "url"):
        val = entry.raw.get(key)
        if isinstance(val, str):
            parts.append(val)
        elif isinstance(val, dict) and isinstance(val.get("url"), str):
            parts.append(val["url"])
    return parts


def normalize_repo(value: str) -> Optional[str]:
    """Accept a URL, an ``owner/name`` slug, or ``git@github.com:owner/name``."""
    if not value:
        return None
    value = value.strip()
    m = _GITHUB_URL_RE.search(value)
    if m:
        return "{0}/{1}".format(m.group(1), m.group(2))
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*", value):
        return value
    return None


def map_repo(entry: ServerEntry, overrides: Optional[dict] = None) -> Optional[str]:
    """Resolve a server to ``owner/name``. Explicit map wins, then an explicit
    GitHub URL anywhere in the entry, then a known package prefix."""
    overrides = overrides or {}
    if entry.name in overrides:
        return normalize_repo(str(overrides[entry.name]))

    candidates = _candidate_strings(entry)
    for c in candidates:
        m = _GITHUB_URL_RE.search(c)
        if m:
            return "{0}/{1}".format(m.group(1), m.group(2))

    for c in candidates:
        token = c.strip()
        for prefix, repo in _PACKAGE_MAP.items():
            if token == prefix or token.startswith(prefix):
                return repo
    return None


def load_map_file(path: str) -> dict:
    """Read a ``--map`` JSON file: ``{"server-name": "owner/repo"}``."""
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("map file must be a JSON object of server -> owner/repo")
    return {str(k): str(v) for k, v in data.items()}


def urllib_get(url: str, headers: dict) -> tuple:
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read()


def _days_since(iso: str, now: Optional[datetime] = None) -> Optional[int]:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return max(0, int((now - dt).total_seconds() // 86400))


class GitHubClient:
    """Fetches and caches repo freshness facts. Never raises for API failure."""

    def __init__(
        self,
        fetch: Optional[Fetch] = None,
        cache: Optional[DiskCache] = None,
        token: Optional[str] = None,
        now: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.fetch = fetch or urllib_get
        self.cache = cache
        self.token = token if token is not None else os.environ.get("GITHUB_TOKEN")
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.rate_limited = False

    def _headers(self) -> dict:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "mcp-freshness",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        return headers

    def lookup(self, repo: Optional[str]) -> RepoInfo:
        if not repo:
            return RepoInfo(status="unmapped", detail="no GitHub repo could be resolved")

        cache_key = "repo:" + repo.lower()
        if self.cache is not None:
            cached = self.cache.get(cache_key)
            if isinstance(cached, dict):
                return self._to_info(repo, cached, cached_hit=True)

        if self.rate_limited:
            return RepoInfo(
                repo=repo,
                status="rate_limited",
                detail="GitHub rate limit already hit this run; set GITHUB_TOKEN for 5000 req/hr",
            )

        try:
            status, headers, body = self.fetch(API_ROOT + repo, self._headers())
        except Exception as exc:  # noqa: BLE001 - a network hiccup is a data gap, not a crash
            return RepoInfo(repo=repo, status="error", detail="{0}: {1}".format(type(exc).__name__, exc))

        if status == 403 or status == 429:
            remaining = str((headers or {}).get("X-RateLimit-Remaining", ""))
            if remaining == "0" or status == 429:
                self.rate_limited = True
                hint = "" if self.token else " - set GITHUB_TOKEN to raise the limit to 5000/hr"
                return RepoInfo(
                    repo=repo,
                    status="rate_limited",
                    detail="GitHub API rate limit reached (60/hr unauthenticated)" + hint,
                )
            return RepoInfo(repo=repo, status="error", detail="GitHub returned HTTP 403 (access denied)")
        if status == 404:
            return RepoInfo(repo=repo, status="not_found", detail="repo not found or private")
        if status >= 400:
            return RepoInfo(repo=repo, status="error", detail="GitHub returned HTTP {0}".format(status))

        try:
            data = json.loads((body or b"").decode("utf-8", errors="replace"))
        except (json.JSONDecodeError, ValueError):
            return RepoInfo(repo=repo, status="error", detail="unparseable GitHub response")
        if not isinstance(data, dict):
            return RepoInfo(repo=repo, status="error", detail="unexpected GitHub response shape")

        slim = {
            "pushed_at": data.get("pushed_at"),
            "archived": bool(data.get("archived")),
            "open_issues_count": data.get("open_issues_count"),
            "stargazers_count": data.get("stargazers_count"),
        }
        if self.cache is not None:
            self.cache.set(cache_key, slim)
        return self._to_info(repo, slim, cached_hit=False)

    def _to_info(self, repo: str, slim: dict, cached_hit: bool) -> RepoInfo:
        pushed = slim.get("pushed_at")
        return RepoInfo(
            repo=repo,
            pushed_at=pushed if isinstance(pushed, str) else None,
            days_since_push=_days_since(pushed, self.now()) if isinstance(pushed, str) else None,
            archived=bool(slim.get("archived")) if slim.get("archived") is not None else None,
            open_issues=slim.get("open_issues_count") if isinstance(slim.get("open_issues_count"), int) else None,
            stars=slim.get("stargazers_count") if isinstance(slim.get("stargazers_count"), int) else None,
            status="ok",
            detail="from cache" if cached_hit else "",
        )
