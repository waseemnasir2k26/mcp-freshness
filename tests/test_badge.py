"""Badge output."""

from __future__ import annotations

import json

from mcp_freshness.badge import (
    badge_color,
    endpoint_badge_url,
    endpoint_json,
    markdown_snippet,
    render_badge,
    static_badge_url,
)


def test_endpoint_json_matches_shields_schema():
    payload = endpoint_json(92, "healthy")
    assert payload["schemaVersion"] == 1
    assert payload["label"] == "mcp freshness"
    assert payload["message"] == "92/100"
    assert payload["color"] == "brightgreen"
    json.dumps(payload)  # must be serialisable


def test_endpoint_json_includes_counts_when_supplied():
    payload = endpoint_json(71, "aging", total=6, healthy=4)
    assert payload["message"] == "71/100 (4/6 live)"
    assert payload["color"] == "yellow"


def test_unknown_score_is_rendered_honestly():
    payload = endpoint_json(None, "unknown")
    assert payload["message"] == "unknown"
    assert payload["color"] == "lightgrey"


def test_colors_per_grade():
    assert badge_color(90, "healthy") == "brightgreen"
    assert badge_color(70, "aging") == "yellow"
    assert badge_color(50, "stale") == "orange"
    assert badge_color(10, "dead") == "red"
    assert badge_color(10, "nonsense") == "lightgrey"


def test_static_badge_url_escapes_shields_specials():
    url = static_badge_url(88, "healthy", label="mcp freshness")
    assert url.startswith("https://img.shields.io/badge/")
    assert "mcp_freshness" in url            # space -> underscore
    assert "88%2F100" in url                 # slash encoded
    assert url.endswith("-brightgreen")


def test_static_badge_label_with_dash_is_doubled():
    url = static_badge_url(50, "stale", label="my-servers")
    assert "my--servers" in url


def test_endpoint_badge_url_is_url_encoded():
    url = endpoint_badge_url("https://raw.githubusercontent.com/o/n/main/badge.json")
    assert url.startswith("https://img.shields.io/endpoint?url=")
    assert "https%3A%2F%2Fraw.githubusercontent.com" in url


def test_markdown_snippet():
    assert markdown_snippet("https://x/y", "alt") == "![alt](https://x/y)"


def test_render_badge_switches_between_json_and_url():
    as_json = render_badge(80, "healthy", as_json=True)
    assert json.loads(as_json)["message"] == "80/100"
    as_url = render_badge(80, "healthy", as_json=False)
    assert as_url.startswith("https://img.shields.io/badge/")
