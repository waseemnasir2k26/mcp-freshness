"""Tiny JSON disk cache with per-entry TTL.

Purpose: unauthenticated GitHub gives 60 requests/hour. Re-running
``mcp-freshness`` in a loop must not burn that budget.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Optional

DEFAULT_TTL_SECONDS = 6 * 3600


def default_cache_path() -> Path:
    env = os.environ.get("MCP_FRESHNESS_CACHE")
    if env:
        return Path(env)
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "mcp-freshness" / "cache.json"
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "mcp-freshness" / "cache.json"


class DiskCache:
    """Load-on-init, write-on-set. Small enough that a single file is right."""

    def __init__(
        self,
        path: Optional[Path] = None,
        ttl: float = DEFAULT_TTL_SECONDS,
        clock: Callable[[], float] = time.time,
        enabled: bool = True,
    ) -> None:
        self.path = Path(path) if path is not None else default_cache_path()
        self.ttl = float(ttl)
        self.clock = clock
        self.enabled = enabled
        self._data: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not self.enabled:
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            return
        if isinstance(raw, dict) and isinstance(raw.get("entries"), dict):
            self._data = {k: v for k, v in raw["entries"].items() if isinstance(v, dict)}

    def get(self, key: str) -> Optional[Any]:
        """Return the cached value, or None when missing or past its TTL."""
        if not self.enabled:
            return None
        rec = self._data.get(key)
        if not isinstance(rec, dict) or "value" not in rec:
            return None
        stored_at = rec.get("stored_at")
        if not isinstance(stored_at, (int, float)):
            return None
        age = self.clock() - float(stored_at)
        if age < 0 or age > self.ttl:
            return None
        return rec["value"]

    def set(self, key: str, value: Any) -> None:
        if not self.enabled:
            return
        self._data[key] = {"stored_at": self.clock(), "value": value}
        self._flush()

    def _flush(self) -> None:
        payload = json.dumps({"version": 1, "entries": self._data}, indent=0)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # temp + replace: a crashed write must never truncate the cache
            fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(payload)
                os.replace(tmp, self.path)
            except Exception:  # noqa: BLE001
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        except OSError:
            # A read-only cache dir degrades to no caching, never to a crash.
            self.enabled = False
