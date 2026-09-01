"""mcp-freshness command line."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Sequence

from . import __version__
from .badge import endpoint_json, markdown_snippet, static_badge_url
from .cache import DEFAULT_TTL_SECONDS, DiskCache, default_cache_path
from .config import ConfigError, load_servers
from .github import GitHubClient, load_map_file
from .render import render_reasons, render_summary, render_table, use_color
from .runner import run
from .scoring import aggregate_score, load_weights

EXIT_OK = 0
EXIT_THRESHOLD = 1
EXIT_USAGE = 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mcp-freshness",
        description="Score every configured MCP server for liveness and maintenance freshness.",
    )
    p.add_argument("--version", action="version", version="mcp-freshness " + __version__)
    p.add_argument("-c", "--config", action="append", metavar="PATH",
                   help="config file to read (repeatable). Default: auto-discover.")
    p.add_argument("--map", metavar="PATH", help="JSON file of {server-name: owner/repo} overrides.")
    p.add_argument("--weights", metavar="PATH", help="TOML weights file. Default: ./.mcp-freshness.toml")
    p.add_argument("--timeout", type=float, default=10.0, metavar="SECONDS",
                   help="hard per-server probe timeout (default: 10)")
    p.add_argument("--concurrency", type=int, default=4, metavar="N",
                   help="how many servers to probe at once (default: 4)")
    p.add_argument("--only", action="append", metavar="NAME", help="only check these servers (repeatable)")
    p.add_argument("--no-probe", action="store_true", help="skip liveness probes, freshness only")
    p.add_argument("--no-github", action="store_true", help="skip GitHub lookups, liveness only")
    p.add_argument("--no-cache", action="store_true", help="bypass the GitHub response cache")
    p.add_argument("--cache-ttl", type=float, default=DEFAULT_TTL_SECONDS, metavar="SECONDS",
                   help="GitHub cache TTL in seconds (default: 21600)")
    p.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    p.add_argument("--badge", nargs="?", const="-", metavar="PATH",
                   help="write a shields.io endpoint JSON (default: stdout)")
    p.add_argument("--badge-style", choices=("endpoint", "static"), default="endpoint",
                   help="endpoint JSON (self-updating) or a static shields.io URL")
    p.add_argument("--badge-label", default="mcp freshness", help="badge label text")
    p.add_argument("--fail-under", type=int, metavar="SCORE",
                   help="exit 1 if the overall score is below SCORE (for CI)")
    p.add_argument("--explain", action="store_true", help="print why each server scored what it did")
    p.add_argument("--list", action="store_true", help="list discovered servers and exit")
    return p


def main(argv: Optional[Sequence[str]] = None, stdout=None, stderr=None) -> int:
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    args = build_parser().parse_args(argv)

    try:
        entries, warnings = load_servers(args.config)
    except ConfigError as exc:
        print("error: {0}".format(exc), file=err)
        return EXIT_USAGE

    for w in warnings:
        print("warning: {0}".format(w), file=err)

    if args.only:
        wanted = set(args.only)
        entries = [e for e in entries if e.name in wanted]

    if args.list:
        for e in entries:
            print("{0}\t{1}\t{2}".format(e.name, e.transport, e.source), file=out)
        return EXIT_OK

    if not entries:
        print("No MCP servers found. Pass --config PATH to point at a config file.", file=err)
        return EXIT_USAGE

    weights, wwarn = load_weights(args.weights)
    for w in wwarn:
        print("warning: {0}".format(w), file=err)

    overrides = {}
    if args.map:
        try:
            overrides = load_map_file(args.map)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print("error: bad --map file: {0}".format(exc), file=err)
            return EXIT_USAGE

    cache = DiskCache(ttl=args.cache_ttl, enabled=not args.no_cache) if not args.no_github else None
    github = GitHubClient(cache=cache)

    reports = run(
        entries,
        github=github,
        weights=weights,
        timeout=args.timeout,
        repo_overrides=overrides,
        skip_probe=args.no_probe,
        skip_github=args.no_github,
        concurrency=max(1, args.concurrency),
    )

    overall = aggregate_score(reports)
    overall_grade = "unknown"
    if overall is not None:
        from .scoring import grade_for
        overall_grade = grade_for(overall, weights)

    healthy = sum(1 for r in reports if r.probe.liveness == "reachable")

    if args.badge:
        if args.badge_style == "static":
            payload = static_badge_url(overall, overall_grade, args.badge_label)
        else:
            payload = json.dumps(
                endpoint_json(overall, overall_grade, args.badge_label, len(reports), healthy),
                indent=2,
            )
        if args.badge == "-":
            print(payload, file=out)
            if args.badge_style == "static":
                print(markdown_snippet(payload), file=out)
        else:
            path = Path(args.badge)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(payload + "\n", encoding="utf-8")
            print("badge written to {0}".format(path), file=out)
        if not args.json:
            return _threshold_exit(args, overall)

    if args.json:
        print(
            json.dumps(
                {
                    "version": __version__,
                    "overall_score": overall,
                    "overall_grade": overall_grade,
                    "servers_total": len(reports),
                    "servers_reachable": healthy,
                    "weights": weights.to_dict(),
                    "cache_path": str(default_cache_path()) if cache and cache.enabled else None,
                    "servers": [r.to_dict() for r in reports],
                },
                indent=2,
            ),
            file=out,
        )
        return _threshold_exit(args, overall)

    print(render_table(reports, color=use_color(out)), file=out)
    print("", file=out)
    print(render_summary(reports, overall), file=out)
    if args.explain:
        print("", file=out)
        print(render_reasons(reports), file=out)

    rate_limited = [r for r in reports if r.repo.status == "rate_limited"]
    if rate_limited:
        print(
            "\nnote: GitHub rate limit hit for {0} repo(s). Set GITHUB_TOKEN to raise it to "
            "5000 req/hr; cached results are reused for {1:.0f}h.".format(
                len(rate_limited), args.cache_ttl / 3600.0
            ),
            file=err,
        )

    return _threshold_exit(args, overall)


def _threshold_exit(args, overall: Optional[int]) -> int:
    if args.fail_under is not None and overall is not None and overall < args.fail_under:
        return EXIT_THRESHOLD
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
