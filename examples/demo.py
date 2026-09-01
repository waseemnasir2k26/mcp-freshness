#!/usr/bin/env python3
"""Render the README sample table using the real pipeline.

The liveness column comes from genuinely spawning the test fixtures in
``tests/fixtures``; the GitHub column comes from a fixed local table so the
demo is reproducible and needs no network. Run:

    python examples/demo.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from mcp_freshness.models import RepoInfo, ServerEntry  # noqa: E402
from mcp_freshness.probe import probe_stdio  # noqa: E402
from mcp_freshness.render import render_summary, render_table  # noqa: E402
from mcp_freshness.scoring import aggregate_score, build_report  # noqa: E402

FIX = ROOT / "tests" / "fixtures"
NOW = datetime.now(timezone.utc)

# name -> (fixture script, repo slug, days since last push, archived)
DEMO = [
    ("filesystem", "healthy_server.py", "modelcontextprotocol/servers", 3, False),
    ("github", "healthy_server.py", "github/github-mcp-server", 11, False),
    ("legacy-crm", "zero_tools_server.py", "acme/legacy-crm-mcp", 412, True),
    ("scraper", "slow_server.py", "someone/scraper-mcp", 260, False),
    ("internal-tools", "crash_server.py", "acme/internal-tools-mcp", 96, False),
]


def repo_info(slug: str, days: int, archived: bool) -> RepoInfo:
    pushed = (NOW - timedelta(days=days)).isoformat().replace("+00:00", "Z")
    return RepoInfo(
        repo=slug, pushed_at=pushed, days_since_push=days,
        archived=archived, open_issues=14, stars=430, status="ok",
    )


def main() -> int:
    reports = []
    for name, script, slug, days, archived in DEMO:
        entry = ServerEntry(name=name, transport="stdio",
                            command=sys.executable, args=[str(FIX / script)])
        result = probe_stdio(entry, timeout=3.0)
        reports.append(build_report(entry, result, repo_info(slug, days, archived)))

    print(render_table(reports))
    print()
    print(render_summary(reports, aggregate_score(reports)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
