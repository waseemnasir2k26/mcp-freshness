"""Scoring math and weight loading."""

from __future__ import annotations

from mcp_freshness.models import ProbeResult, RepoInfo
from mcp_freshness.scoring import (
    DEFAULT_WEIGHTS,
    Weights,
    aggregate_score,
    grade_for,
    load_weights,
    score_server,
)


def live(latency=100.0, tools=5):
    return ProbeResult(liveness="reachable", latency_ms=latency, tool_count=tools)


def repo(days=5, archived=False):
    return RepoInfo(repo="o/n", days_since_push=days, archived=archived, status="ok")


def test_perfect_server_scores_100():
    score, grade, _ = score_server(live(latency=50, tools=9), repo(days=1))
    assert score == 100
    assert grade == "healthy"


def test_dead_server_scores_zero_on_reachability():
    score, grade, reasons = score_server(
        ProbeResult(liveness="unreachable", detail="server exited with code 1"),
        RepoInfo(status="unmapped"),
    )
    assert score == 0
    assert grade == "dead"
    assert any("unreachable" in r for r in reasons)


def test_timeout_counts_as_unreachable_for_scoring():
    score, _, reasons = score_server(ProbeResult(liveness="timeout"), RepoInfo(status="unmapped"))
    assert score == 0
    assert any("timed out" in r for r in reasons)


def test_zero_tools_costs_exactly_the_tool_weight():
    with_tools, _, _ = score_server(live(latency=50, tools=3), repo(days=1))
    without, _, _ = score_server(live(latency=50, tools=0), repo(days=1))
    w = DEFAULT_WEIGHTS
    total = w.reachable + w.tools + w.latency + w.freshness
    expected_drop = round(100.0 * w.tools / total)
    assert with_tools - without == expected_drop


def test_archived_is_a_hard_absolute_penalty():
    fresh, _, _ = score_server(live(), repo(days=2, archived=False))
    arch, grade, reasons = score_server(live(), repo(days=2, archived=True))
    assert fresh - arch == int(DEFAULT_WEIGHTS.archived_penalty)
    assert any("ARCHIVED" in r for r in reasons)
    assert grade in ("aging", "stale", "dead")


def test_freshness_curve_is_monotonic_and_bounded():
    scores = [score_server(live(), repo(days=d))[0] for d in (0, 30, 90, 200, 365, 900)]
    assert scores == sorted(scores, reverse=True)
    assert scores[0] == scores[1], "anything inside fresh_days gets full credit"
    assert scores[-1] == scores[-2], "past stale_days the freshness credit is already zero"


def test_latency_curve_is_monotonic():
    scores = [score_server(live(latency=ms), repo())[0] for ms in (10, 500, 2000, 5000, 20000)]
    assert scores == sorted(scores, reverse=True)


def test_unknown_signals_are_not_counted_against_the_server():
    """No GitHub mapping must not be scored as 'stale'."""
    mapped, _, _ = score_server(live(latency=50, tools=4), repo(days=1))
    unmapped, _, reasons = score_server(live(latency=50, tools=4), RepoInfo(status="unmapped"))
    assert mapped == unmapped == 100
    assert any("no GitHub repo mapped" in r for r in reasons)


def test_nothing_measurable_yields_unknown_grade():
    score, grade, _ = score_server(
        ProbeResult(liveness="unsupported"), RepoInfo(status="unmapped")
    )
    assert score == 0
    assert grade == "unknown"


def test_grade_bands():
    assert grade_for(100) == "healthy"
    assert grade_for(80) == "healthy"
    assert grade_for(79) == "aging"
    assert grade_for(60) == "aging"
    assert grade_for(59) == "stale"
    assert grade_for(40) == "stale"
    assert grade_for(39) == "dead"
    assert grade_for(0) == "dead"


def test_score_is_always_clamped_to_0_100():
    harsh = Weights(archived_penalty=500.0)
    score, _, _ = score_server(live(), repo(days=1, archived=True), harsh)
    assert score == 0


def test_custom_weights_change_the_answer():
    tools_only = Weights(reachable=0.0, freshness=0.0, latency=0.0, tools=100.0)
    score, _, _ = score_server(live(latency=9999, tools=1), repo(days=999), tools_only)
    assert score == 100


def test_aggregate_score_ignores_unknown_grades():
    from mcp_freshness.models import ServerEntry, ServerReport

    def rep(score, grade):
        return ServerReport(
            entry=ServerEntry(name="x"), probe=ProbeResult(), repo=RepoInfo(),
            score=score, grade=grade,
        )

    assert aggregate_score([rep(100, "healthy"), rep(0, "dead")]) == 50
    assert aggregate_score([rep(0, "unknown")]) is None
    assert aggregate_score([]) is None


# ------------------------------------------------------------- weight files


def test_load_weights_from_toml(tmp_path):
    p = tmp_path / ".mcp-freshness.toml"
    p.write_text("[weights]\nreachable = 60\nfresh_days = 7\n", encoding="utf-8")
    w, warnings = load_weights(p)
    assert w.reachable == 60
    assert w.fresh_days == 7
    assert w.tools == DEFAULT_WEIGHTS.tools
    assert warnings == []


def test_load_weights_flat_table_also_works(tmp_path):
    p = tmp_path / "w.toml"
    p.write_text("reachable = 10\n", encoding="utf-8")
    w, _ = load_weights(p)
    assert w.reachable == 10


def test_unknown_and_bad_weight_keys_warn_but_do_not_crash(tmp_path):
    p = tmp_path / "w.toml"
    p.write_text("[weights]\nbogus = 5\nreachable = \"lots\"\n", encoding="utf-8")
    w, warnings = load_weights(p)
    assert w.reachable == DEFAULT_WEIGHTS.reachable
    assert any("bogus" in x for x in warnings)
    assert any("must be a number" in x for x in warnings)


def test_missing_weights_file_falls_back_to_defaults(tmp_path):
    w, warnings = load_weights(tmp_path / "nope.toml")
    assert w.to_dict() == DEFAULT_WEIGHTS.to_dict()
    assert warnings and "not found" in warnings[0]


def test_no_weights_file_in_cwd_uses_defaults(tmp_path):
    w, warnings = load_weights(None, cwd=tmp_path)
    assert w.to_dict() == DEFAULT_WEIGHTS.to_dict()
    assert warnings == []
