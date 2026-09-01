"""Shared dataclasses. No I/O here."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Literal, Optional

Transport = Literal["stdio", "http", "sse", "unknown"]

Liveness = Literal[
    "reachable",      # handshake completed
    "unreachable",    # process/host refused, crashed, or errored
    "timeout",        # exceeded --timeout, killed
    "unsupported",    # we deliberately do not probe this transport
    "unknown",        # probing skipped
]


@dataclass
class ServerEntry:
    """One MCP server as declared in a config file."""

    name: str
    transport: Transport = "stdio"
    command: Optional[str] = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: Optional[str] = None
    headers: dict[str, str] = field(default_factory=dict)
    source: str = ""          # path of the config file it came from
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("raw", None)
        d["env"] = {k: "<set>" for k in self.env}      # never echo secrets
        d["headers"] = {k: "<set>" for k in self.headers}
        return d


@dataclass
class ProbeResult:
    liveness: Liveness = "unknown"
    latency_ms: Optional[float] = None
    tool_count: Optional[int] = None
    server_name: Optional[str] = None
    server_version: Optional[str] = None
    protocol_version: Optional[str] = None
    detail: str = ""

    @property
    def reachable(self) -> bool:
        return self.liveness == "reachable"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RepoInfo:
    """GitHub repository freshness facts."""

    repo: Optional[str] = None            # "owner/name"
    pushed_at: Optional[str] = None       # ISO-8601
    days_since_push: Optional[int] = None
    archived: Optional[bool] = None
    open_issues: Optional[int] = None
    stars: Optional[int] = None
    status: str = "unmapped"              # unmapped | ok | not_found | rate_limited | error | cached
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ServerReport:
    entry: ServerEntry
    probe: ProbeResult
    repo: RepoInfo
    score: int = 0
    grade: str = "unknown"
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.entry.name,
            "transport": self.entry.transport,
            "source": self.entry.source,
            "server": self.entry.to_dict(),
            "probe": self.probe.to_dict(),
            "repo": self.repo.to_dict(),
            "score": self.score,
            "grade": self.grade,
            "reasons": self.reasons,
        }
