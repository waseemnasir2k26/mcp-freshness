"""shields.io badge output.

Two shapes:
  * endpoint JSON - commit it, point shields.io at the raw URL, badge updates
    itself every time CI re-runs mcp-freshness.
  * a static shields.io URL - paste-and-go, no hosting.
"""

from __future__ import annotations

import json
import urllib.parse
from typing import Optional

SHIELDS_ENDPOINT = "https://img.shields.io/endpoint?url="
SHIELDS_STATIC = "https://img.shields.io/badge/"

COLORS = {
    "healthy": "brightgreen",
    "aging": "yellow",
    "stale": "orange",
    "dead": "red",
    "unknown": "lightgrey",
}


def badge_color(score: Optional[int], grade: str) -> str:
    return COLORS.get(grade, "lightgrey")


def endpoint_json(
    score: Optional[int],
    grade: str,
    label: str = "mcp freshness",
    total: Optional[int] = None,
    healthy: Optional[int] = None,
) -> dict:
    """The schemaVersion-1 object shields.io expects from an endpoint badge."""
    if score is None:
        message = "unknown"
    elif total is not None and healthy is not None:
        message = "{0}/100 ({1}/{2} live)".format(score, healthy, total)
    else:
        message = "{0}/100".format(score)
    return {
        "schemaVersion": 1,
        "label": label,
        "message": message,
        "color": badge_color(score, grade),
    }


def endpoint_badge_url(raw_json_url: str) -> str:
    return SHIELDS_ENDPOINT + urllib.parse.quote(raw_json_url, safe="")


def _shield_escape(text: str) -> str:
    # shields.io static path: '-' -> '--', '_' -> '__', ' ' -> '_'
    return text.replace("-", "--").replace("_", "__").replace(" ", "_")


def static_badge_url(score: Optional[int], grade: str, label: str = "mcp freshness") -> str:
    message = "unknown" if score is None else "{0}%2F100".format(score)
    return "{0}{1}-{2}-{3}".format(
        SHIELDS_STATIC,
        _shield_escape(label),
        message,
        badge_color(score, grade),
    )


def markdown_snippet(url: str, alt: str = "MCP freshness") -> str:
    return "![{0}]({1})".format(alt, url)


def render_badge(
    score: Optional[int],
    grade: str,
    label: str = "mcp freshness",
    total: Optional[int] = None,
    healthy: Optional[int] = None,
    as_json: bool = True,
) -> str:
    if as_json:
        return json.dumps(endpoint_json(score, grade, label, total, healthy), indent=2)
    return static_badge_url(score, grade, label)
