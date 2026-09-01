"""Orchestration: entries -> probes -> GitHub freshness -> scored reports."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

from .github import GitHubClient, map_repo
from .models import ProbeResult, RepoInfo, ServerEntry, ServerReport
from .probe import DEFAULT_TIMEOUT, probe as default_probe
from .scoring import DEFAULT_WEIGHTS, Weights, build_report

ProbeFn = Callable[[ServerEntry, float], ProbeResult]


def run(
    entries: list[ServerEntry],
    github: Optional[GitHubClient] = None,
    weights: Weights = DEFAULT_WEIGHTS,
    timeout: float = DEFAULT_TIMEOUT,
    probe_fn: Optional[ProbeFn] = None,
    repo_overrides: Optional[dict] = None,
    skip_probe: bool = False,
    skip_github: bool = False,
    concurrency: int = 4,
) -> list[ServerReport]:
    probe_fn = probe_fn or (lambda e, t: default_probe(e, t))

    if skip_probe:
        probes = [ProbeResult(liveness="unknown", detail="probing skipped (--no-probe)") for _ in entries]
    elif concurrency > 1 and len(entries) > 1:
        # Probes are I/O-bound and each has its own hard timeout, so running a
        # few at once is safe and keeps a 10-server config under ~1 timeout.
        with ThreadPoolExecutor(max_workers=min(concurrency, len(entries))) as pool:
            probes = list(pool.map(lambda e: probe_fn(e, timeout), entries))
    else:
        probes = [probe_fn(e, timeout) for e in entries]

    reports: list[ServerReport] = []
    for entry, p in zip(entries, probes):
        if skip_github:
            repo = RepoInfo(status="unmapped", detail="GitHub lookup skipped (--no-github)")
        else:
            client = github or GitHubClient()
            repo = client.lookup(map_repo(entry, repo_overrides))
        reports.append(build_report(entry, p, repo, weights))
    return reports
