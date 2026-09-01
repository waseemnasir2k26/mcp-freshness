"""The 0-100 freshness score.

Every number here is tunable from ``.mcp-freshness.toml``; the defaults below
are the documented ones in the README. Scoring is pure - it takes a probe
result and a RepoInfo, and returns a number plus the reasons for it.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from .models import ProbeResult, RepoInfo, ServerEntry, ServerReport


@dataclass
class Weights:
    reachable: float = 45.0        # it answers the handshake
    freshness: float = 25.0        # days since last push
    tools: float = 15.0            # exposes at least one tool
    latency: float = 15.0          # how fast the handshake was
    archived_penalty: float = 30.0  # hard penalty, subtracted

    # freshness curve, in days: full credit at or under `fresh_days`,
    # zero credit at or over `stale_days`, linear between.
    fresh_days: int = 30
    stale_days: int = 365

    # latency curve, in milliseconds.
    fast_ms: float = 500.0
    slow_ms: float = 5000.0

    # grade bands (lower bound, inclusive)
    healthy_at: int = 80
    aging_at: int = 60
    stale_at: int = 40

    def to_dict(self) -> dict:
        return asdict(self)


DEFAULT_WEIGHTS = Weights()
CONFIG_FILENAME = ".mcp-freshness.toml"


def load_weights(path: Optional[str | Path] = None, cwd: Optional[Path] = None) -> tuple[Weights, list[str]]:
    """Load weights from a TOML file. Unknown keys are reported, not fatal."""
    warnings: list[str] = []
    if path is None:
        candidate = Path(cwd or Path.cwd()) / CONFIG_FILENAME
        if not candidate.is_file():
            return Weights(), warnings
        p = candidate
    else:
        p = Path(path)
        if not p.is_file():
            return Weights(), ["weights file not found: {0}".format(p)]

    try:
        data = tomllib.loads(p.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return Weights(), ["cannot read {0}: {1}".format(p, exc)]

    section = data.get("weights") if isinstance(data.get("weights"), dict) else data
    valid = {f for f in Weights().to_dict()}
    kwargs = {}
    for key, value in (section or {}).items():
        if key not in valid:
            warnings.append("unknown weight '{0}' in {1} ignored".format(key, p))
            continue
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            warnings.append("weight '{0}' must be a number; ignored".format(key))
            continue
        kwargs[key] = value
    return Weights(**kwargs), warnings


def _freshness_fraction(days: Optional[int], w: Weights) -> Optional[float]:
    if days is None:
        return None
    if days <= w.fresh_days:
        return 1.0
    if days >= w.stale_days:
        return 0.0
    span = float(w.stale_days - w.fresh_days) or 1.0
    return max(0.0, min(1.0, 1.0 - (days - w.fresh_days) / span))


def _latency_fraction(ms: Optional[float], w: Weights) -> Optional[float]:
    if ms is None:
        return None
    if ms <= w.fast_ms:
        return 1.0
    if ms >= w.slow_ms:
        return 0.0
    span = float(w.slow_ms - w.fast_ms) or 1.0
    return max(0.0, min(1.0, 1.0 - (ms - w.fast_ms) / span))


def grade_for(score: int, w: Weights = DEFAULT_WEIGHTS) -> str:
    if score >= w.healthy_at:
        return "healthy"
    if score >= w.aging_at:
        return "aging"
    if score >= w.stale_at:
        return "stale"
    return "dead"


def score_server(
    probe: ProbeResult,
    repo: RepoInfo,
    weights: Weights = DEFAULT_WEIGHTS,
) -> tuple[int, str, list[str]]:
    """Return ``(score, grade, reasons)``.

    Design choice: when a signal is genuinely unknown (no GitHub mapping, or a
    transport we cannot probe) we award its weight pro-rata from the signals we
    DO have, rather than scoring the server down for our own blind spot. A
    server is only penalised for facts we actually measured.
    """
    w = weights
    reasons: list[str] = []

    earned = 0.0
    available = 0.0

    # --- reachability (heaviest) -----------------------------------------
    if probe.liveness in ("reachable", "unreachable", "timeout"):
        available += w.reachable
        if probe.liveness == "reachable":
            earned += w.reachable
            reasons.append("reachable: MCP initialize handshake completed")
        elif probe.liveness == "timeout":
            reasons.append("unreachable: probe timed out and the process group was killed")
        else:
            reasons.append("unreachable: " + (probe.detail or "handshake failed"))
    else:
        reasons.append("liveness not measured ({0})".format(probe.liveness))

    # --- tools ------------------------------------------------------------
    if probe.tool_count is not None:
        available += w.tools
        if probe.tool_count > 0:
            earned += w.tools
            reasons.append("exposes {0} tool(s)".format(probe.tool_count))
        else:
            reasons.append("exposes 0 tools")

    # --- latency ----------------------------------------------------------
    lat = _latency_fraction(probe.latency_ms, w) if probe.liveness == "reachable" else None
    if lat is not None:
        available += w.latency
        earned += w.latency * lat
        reasons.append("handshake latency {0:.0f}ms".format(probe.latency_ms or 0.0))

    # --- freshness --------------------------------------------------------
    fresh = _freshness_fraction(repo.days_since_push, w) if repo.status == "ok" else None
    if fresh is not None:
        available += w.freshness
        earned += w.freshness * fresh
        reasons.append("last commit {0} day(s) ago".format(repo.days_since_push))
    elif repo.status == "unmapped":
        reasons.append("freshness unknown: no GitHub repo mapped (use --map)")
    elif repo.status == "rate_limited":
        reasons.append("freshness unknown: GitHub rate limit")
    elif repo.status != "ok":
        reasons.append("freshness unknown: {0}".format(repo.detail or repo.status))

    if available <= 0:
        return 0, "unknown", reasons or ["nothing could be measured"]

    score = 100.0 * earned / available

    # --- archived: a hard, absolute penalty -------------------------------
    if repo.archived:
        score -= w.archived_penalty
        reasons.append("ARCHIVED on GitHub (-{0:.0f})".format(w.archived_penalty))

    final = int(round(max(0.0, min(100.0, score))))
    return final, grade_for(final, w), reasons


def build_report(
    entry: ServerEntry,
    probe: ProbeResult,
    repo: RepoInfo,
    weights: Weights = DEFAULT_WEIGHTS,
) -> ServerReport:
    score, grade, reasons = score_server(probe, repo, weights)
    return ServerReport(entry=entry, probe=probe, repo=repo, score=score, grade=grade, reasons=reasons)


def aggregate_score(reports: list[ServerReport]) -> Optional[int]:
    scored = [r.score for r in reports if r.grade != "unknown"]
    if not scored:
        return None
    return int(round(sum(scored) / len(scored)))
