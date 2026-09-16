# Changelog

All notable changes to this project are documented in this file.

## [2026.09] - 2026-09-16

- Maintenance review of mcp-freshness — a Python CLI that reads your MCP config, actually starts every server, and scores each one 0-100 for liveness and maintenance freshness, printing a per-server table with transport, tool count, latency, last commit and grade.
- Stack: Python 3.11+, zero runtime dependencies, packaged via `pyproject.toml` (v0.1.0). Source in `src/mcp_freshness/` (config discovery, stdio/HTTP probes, GitHub mapping, scoring, badge and render layers); pytest suite in `tests/` that makes no network calls, using local fake MCP servers from fixtures.
- Status: last change September 2026. Auto-discovers `.mcp.json`, `.vscode/mcp.json`, `~/.claude.json` and `claude_desktop_config.json`; supports `--json`, `--explain`, `-c/--config` and a shields.io endpoint badge committed at `.github/badges/mcp-freshness.json`. MIT licensed.
- Reviewed September 2026: docs refreshed, CHANGELOG started, versioned as v2026.09. No source, tests or packaging metadata changed.
- Known gaps: the CI workflow is still parked at `.ci-pending/ci.yml` and not installed under `.github/workflows/` (last commit says it was deferred until the push token had workflow scope), so the pytest suite does not run on push; not published to PyPI, so the README's install path is `pip install git+https://…`; no CHANGELOG before this release.
