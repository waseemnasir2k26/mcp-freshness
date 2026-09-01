# mcp-freshness

Reads your MCP config, actually starts every server, and scores each one 0-100 for **liveness** and **maintenance freshness** — so you find out a server is dead from a table, not from an agent failing mid-task.

```
$ mcp-freshness

SERVER          TRANSPORT  LIVENESS  TOOLS  LATENCY  LAST COMMIT    SCORE  GRADE
--------------  ---------  --------  -----  -------  -------------  -----  -------
filesystem      stdio      live      3      31ms     3d ago         100    healthy
github          stdio      live      3      31ms     11d ago        100    healthy
legacy-crm      stdio      live      0      63ms     412d ARCHIVED  30     dead
scraper         stdio      timeout   -      -        260d ago       11     dead
internal-tools  stdio      dead      -      -        96d ago        29     dead

5 server(s)  3 reachable  1 archived  overall 54/100
```

<sub>(That table is produced by `python examples/demo.py`, which runs the real pipeline against the test fixtures.)</sub>

[![MCP freshness](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fwaseemnasir2k26%2Fmcp-freshness%2Fmain%2F.github%2Fbadges%2Fmcp-freshness.json)](#badge-for-your-readme)
![CI](https://img.shields.io/badge/ci-pytest-blue)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
![Runtime deps: 0](https://img.shields.io/badge/runtime_deps-0-lightgrey)

<sub>The first badge is this tool scoring `examples/demo.mcp.json` — a deliberately mixed-health config, so it is _supposed_ to be orange.</sub>

An independent third-party audit of 1,847 MCP servers reported that roughly **52% were unreachable**, with "no last-commit date, no health-check light, no maintainer SLA" — source: [rapidclaw.dev](https://rapidclaw.dev/blog/mcp-servers-dead-what-it-means-2026). That number is theirs, not a measurement of ours. This tool exists so you can measure _your own_ config instead of trusting anybody's average.

## Quick start

```bash
pip install git+https://github.com/waseemnasir2k26/mcp-freshness   # zero runtime deps
cd your-project                                               # anywhere with a .mcp.json
mcp-freshness
```

(PyPI release pending; until then install from git as above.)

That is the whole setup. It auto-discovers `.mcp.json`, `.vscode/mcp.json`, `~/.claude.json`, and `claude_desktop_config.json`. Point it somewhere specific with `-c/--config PATH`, get machine output with `--json`, and add `--explain` to see why each score landed where it did.

## What it checks

| Check               | How                                                   | Notes                                                                   |
| ------------------- | ----------------------------------------------------- | ----------------------------------------------------------------------- |
| Starts at all       | spawns the `command`, real MCP `initialize` handshake | stdio servers                                                           |
| Answers requests    | `tools/list` after the handshake                      | tool count recorded                                                     |
| Responds in time    | hard `--timeout` (default 10s) per server             | on expiry the whole **process group** is killed, grandchildren included |
| Reachable over HTTP | Streamable-HTTP `initialize` POST, SSE frames parsed  | 401/403 reported as `unsupported`, never guessed                        |
| Last commit         | GitHub API `pushed_at`                                | works unauthenticated; `GITHUB_TOKEN` used if set                       |
| Archived            | GitHub API `archived`                                 | hard penalty                                                            |
| Issue backlog       | GitHub API `open_issues_count`                        | reported, not scored (yet)                                              |

Responses are cached to disk with a TTL (`--cache-ttl`, default 6h) so re-running does not burn the unauthenticated 60 req/hr budget. Hitting the limit prints one clear line, never a traceback.

## The score

Each signal contributes its weight, and the total is normalised over **only the signals that could actually be measured** — a server is never marked down because _we_ could not map it to a repo.

| Signal                           |  Weight | Full credit            | No credit                       |
| -------------------------------- | ------: | ---------------------- | ------------------------------- |
| Reachable (handshake completed)  |      45 | handshake ok           | unreachable or timed out        |
| Freshness (days since last push) |      25 | ≤ 30 days              | ≥ 365 days (linear between)     |
| Exposes ≥ 1 tool                 |      15 | `tools/list` non-empty | zero tools                      |
| Handshake latency                |      15 | ≤ 500 ms               | ≥ 5000 ms (linear between)      |
| **Archived on GitHub**           | **−30** | —                      | subtracted from the final score |

Grades: `healthy` ≥ 80 · `aging` ≥ 60 · `stale` ≥ 40 · `dead` below that.

Every number is tunable. Drop a `.mcp-freshness.toml` next to your config (or pass `--weights PATH`):

```toml
[weights]
reachable = 60          # we care more about "does it run" than "is it new"
freshness = 15
tools     = 15
latency   = 10
archived_penalty = 40
fresh_days = 14         # our team ships weekly
stale_days = 180
healthy_at = 85
```

Use `--fail-under 70` in CI to make a rotting stack a build failure.

## Badge for your README

```bash
mcp-freshness --badge .github/badges/mcp-freshness.json
```

Commit that file, then paste this (swap in your repo path):

```markdown
![MCP freshness](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2FOWNER%2FREPO%2Fmain%2F.github%2Fbadges%2Fmcp-freshness.json)
```

Re-run it in CI and the badge updates itself. If you would rather not host a file, `--badge --badge-style static` prints a ready-made static shields.io URL plus the Markdown line.

## This is not a security scanner

It does not look for prompt injection, tool poisoning, rug-pull updates, or malicious server behaviour. It answers exactly two questions: _does this server still run_, and _is anyone still maintaining it_.

For the security lane, use [mcp-scan](https://github.com/invariantlabs-ai/mcp-scan) by Invariant Labs / Snyk. It is the mature tool for that job and the two compose fine — freshness gate first, security scan second.

## Limitations

- **Liveness is a point-in-time probe.** A `live` result means it answered once, just now. It is not uptime monitoring.
- **Servers that need credentials will report unreachable without them.** The probe passes the entry's own `env` block through to the child process, so put the keys where your MCP client already expects them and the probe inherits them. It also inherits your shell environment. Secrets are never printed — `env` and `headers` are masked to `<set>` in `--json` output.
- **HTTP/SSE probing is shallower than stdio.** We complete the `initialize` POST but do not enumerate tools, and a `401`/`403` is honestly reported as `unsupported` rather than guessed either way.
- **GitHub mapping is best-effort.** It resolves a repo from an explicit GitHub URL in the entry, or from a handful of well-known package prefixes. Anything else shows `unmapped` — supply the rest with `--map map.json` (`{"server-name": "owner/repo"}`).
- **Unauthenticated GitHub is 60 req/hr.** Set `GITHUB_TOKEN` for 5000. The disk cache absorbs most repeat runs.
- **Freshness is a proxy, not a verdict.** A finished, stable server with no commits in a year is not automatically bad — it is just a thing you should have decided on deliberately.

## Contributing

Issues and PRs welcome, particularly: more package-prefix → repo mappings, deeper HTTP/SSE probing, and registry metadata sources beyond GitHub.

```bash
git clone https://github.com/waseemnasir2k26/mcp-freshness
cd mcp-freshness
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest
```

The test suite makes **no network calls** — the GitHub client is injectable and the liveness tests spawn local fake MCP servers from `tests/fixtures/`. Please keep it that way.

---

Built by **SkynetLabs (Waseem Nasir)** — we audit and harden agent stacks.
[skynetjoe.com](https://skynetjoe.com) · [Book a free consultation](https://calendly.com/skynetlabs/schedule-a-free-consultation)

## License

MIT — see [LICENSE](LICENSE).
