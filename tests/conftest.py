"""Shared test helpers. No network is used anywhere in this suite."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_entry(name: str, script: str, **kwargs):
    from mcp_freshness.models import ServerEntry

    return ServerEntry(
        name=name,
        transport="stdio",
        command=sys.executable,
        args=[str(FIXTURES / script)],
        source="test",
        **kwargs,
    )


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture
def make_entry():
    return fixture_entry


def fake_github_fetch(payload: dict, status: int = 200, headers: dict | None = None):
    """Build an injectable GitHub fetch that records its calls."""
    calls: list[str] = []

    def fetch(url: str, hdrs: dict):
        calls.append(url)
        return status, headers or {}, json.dumps(payload).encode("utf-8")

    fetch.calls = calls  # type: ignore[attr-defined]
    return fetch


def no_network_fetch(url: str, headers: dict):  # pragma: no cover - guard
    raise AssertionError("test attempted a real network call to " + url)
