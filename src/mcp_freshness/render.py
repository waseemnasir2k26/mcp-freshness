"""Plain-text table rendering. No TUI dependency by design."""

from __future__ import annotations

import os
import sys
from typing import Optional

from .models import ServerReport

STATUS_GLYPH = {
    "healthy": "OK ",
    "aging": "AGE",
    "stale": "OLD",
    "dead": "DEAD",
    "unknown": " ? ",
}

_COLORS = {
    "healthy": "\033[32m",
    "aging": "\033[33m",
    "stale": "\033[33m",
    "dead": "\033[31m",
    "unknown": "\033[90m",
}
_RESET = "\033[0m"


def use_color(stream=None) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    stream = stream or sys.stdout
    return bool(getattr(stream, "isatty", lambda: False)())


def _liveness_cell(r: ServerReport) -> str:
    p = r.probe
    if p.liveness == "reachable":
        return "live"
    if p.liveness == "timeout":
        return "timeout"
    if p.liveness == "unreachable":
        return "dead"
    if p.liveness == "unsupported":
        return "n/a"
    return "?"


def _tools_cell(r: ServerReport) -> str:
    return "-" if r.probe.tool_count is None else str(r.probe.tool_count)


def _latency_cell(r: ServerReport) -> str:
    return "-" if r.probe.latency_ms is None else "{0:.0f}ms".format(r.probe.latency_ms)


def _commit_cell(r: ServerReport) -> str:
    repo = r.repo
    if repo.status == "unmapped":
        return "unmapped"
    if repo.status == "rate_limited":
        return "rate-limited"
    if repo.status == "not_found":
        return "not found"
    if repo.status != "ok" or repo.days_since_push is None:
        return "?"
    d = repo.days_since_push
    if repo.archived:
        return "{0}d ARCHIVED".format(d)
    return "{0}d ago".format(d)


HEADERS = ["SERVER", "TRANSPORT", "LIVENESS", "TOOLS", "LATENCY", "LAST COMMIT", "SCORE", "GRADE"]


def render_table(reports: list[ServerReport], color: bool = False) -> str:
    rows: list[list[str]] = []
    for r in reports:
        rows.append(
            [
                r.entry.name,
                r.entry.transport,
                _liveness_cell(r),
                _tools_cell(r),
                _latency_cell(r),
                _commit_cell(r),
                "-" if r.grade == "unknown" else str(r.score),
                r.grade,
            ]
        )

    widths = [len(h) for h in HEADERS]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def fmt(cells: list[str]) -> str:
        return "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(cells)).rstrip()

    lines = [fmt(HEADERS), "  ".join("-" * w for w in widths)]
    for r, row in zip(reports, rows):
        line = fmt(row)
        if color:
            line = _COLORS.get(r.grade, "") + line + _RESET
        lines.append(line)
    return "\n".join(lines)


def render_summary(reports: list[ServerReport], overall: Optional[int]) -> str:
    total = len(reports)
    live = sum(1 for r in reports if r.probe.liveness == "reachable")
    archived = sum(1 for r in reports if r.repo.archived)
    parts = ["{0} server(s)".format(total), "{0} reachable".format(live)]
    if archived:
        parts.append("{0} archived".format(archived))
    parts.append("overall {0}".format("n/a" if overall is None else "{0}/100".format(overall)))
    return "  ".join(parts)


def render_reasons(reports: list[ServerReport]) -> str:
    out: list[str] = []
    for r in reports:
        out.append("{0}  [{1}]".format(r.entry.name, r.grade))
        for reason in r.reasons:
            out.append("    - " + reason)
    return "\n".join(out)
