"""Discover and parse MCP client configs.

Tolerant by design: unknown keys are preserved in ``raw`` and ignored, a
malformed single entry never sinks the whole file.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable

from .models import ServerEntry, Transport


class ConfigError(Exception):
    """Raised when a config file cannot be read or is not JSON."""


# Keys under which different clients nest their server maps.
_SERVER_MAP_KEYS = ("mcpServers", "mcp_servers", "servers")


def default_config_paths(cwd: Path | None = None, home: Path | None = None) -> list[Path]:
    """Well-known locations, in discovery order. Non-existent paths included;
    caller filters. Kept pure so tests can pin cwd/home."""
    explicit_home = home is not None
    cwd = Path(cwd or Path.cwd())
    home = Path(home) if explicit_home else Path.home()

    paths: list[Path] = [
        cwd / ".mcp.json",
        cwd / ".vscode" / "mcp.json",
        home / ".claude.json",
        home / ".claude" / "mcp.json",
    ]

    # Environment variables are only consulted when the caller did not pin a
    # home directory - otherwise tests (and --config sandboxes) would leak out
    # into the real user profile.
    if sys.platform == "darwin":
        paths.append(home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json")
    elif os.name == "nt":
        appdata = None if explicit_home else os.environ.get("APPDATA")
        base = Path(appdata) if appdata else home / "AppData" / "Roaming"
        paths.append(base / "Claude" / "claude_desktop_config.json")
    else:
        xdg = None if explicit_home else os.environ.get("XDG_CONFIG_HOME")
        base = Path(xdg) if xdg else home / ".config"
        paths.append(base / "Claude" / "claude_desktop_config.json")

    # de-dupe, preserve order
    seen: set[str] = set()
    out: list[Path] = []
    for p in paths:
        key = str(p)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def discover_config_files(cwd: Path | None = None, home: Path | None = None) -> list[Path]:
    return [p for p in default_config_paths(cwd, home) if p.is_file()]


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return []


def _as_str_map(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items() if v is not None}
    return {}


def _infer_transport(raw: dict[str, Any]) -> Transport:
    declared = str(raw.get("type") or raw.get("transport") or "").strip().lower()
    if declared in ("stdio", "http", "sse"):
        return declared  # type: ignore[return-value]
    if declared in ("streamable-http", "streamablehttp", "http-stream"):
        return "http"
    if raw.get("command"):
        return "stdio"
    url = raw.get("url") or raw.get("endpoint") or raw.get("serverUrl")
    if url:
        return "sse" if str(url).rstrip("/").endswith("/sse") else "http"
    return "unknown"


def parse_entry(name: str, raw: Any, source: str = "") -> ServerEntry:
    """Turn one config value into a ServerEntry. Never raises for odd shapes."""
    if not isinstance(raw, dict):
        return ServerEntry(name=name, transport="unknown", source=source, raw={"value": raw})

    transport = _infer_transport(raw)
    url = raw.get("url") or raw.get("endpoint") or raw.get("serverUrl")
    command = raw.get("command")
    if isinstance(command, list):  # some configs use ["npx", "-y", "pkg"]
        cmd_list = _as_str_list(command)
        command = cmd_list[0] if cmd_list else None
        args = cmd_list[1:] + _as_str_list(raw.get("args"))
    else:
        command = str(command) if command else None
        args = _as_str_list(raw.get("args"))

    return ServerEntry(
        name=name,
        transport=transport,
        command=command,
        args=args,
        env=_as_str_map(raw.get("env")),
        url=str(url) if url else None,
        headers=_as_str_map(raw.get("headers")),
        source=source,
        raw=raw,
    )


def parse_config_data(data: Any, source: str = "") -> list[ServerEntry]:
    """Accept both ``{"mcpServers": {...}}`` and a bare ``{name: {...}}`` map."""
    if not isinstance(data, dict):
        return []

    server_map: dict[str, Any] | None = None
    for key in _SERVER_MAP_KEYS:
        candidate = data.get(key)
        if isinstance(candidate, dict):
            server_map = candidate
            break

    if server_map is None:
        # `~/.claude.json` nests per-project configs under "projects".
        projects = data.get("projects")
        if isinstance(projects, dict):
            entries: list[ServerEntry] = []
            for proj, pdata in projects.items():
                if isinstance(pdata, dict):
                    for e in parse_config_data(pdata, source=f"{source}#{proj}" if source else proj):
                        entries.append(e)
            if entries:
                return entries
        # Bare map: every value that looks like a server entry.
        if all(isinstance(v, dict) for v in data.values()) and data:
            looks_like = any(
                ("command" in v or "url" in v or "type" in v) for v in data.values() if isinstance(v, dict)
            )
            server_map = data if looks_like else {}
        else:
            server_map = {}

    return [parse_entry(str(name), raw, source=source) for name, raw in server_map.items()]


def load_config_file(path: str | Path) -> list[ServerEntry]:
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read {p}: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{p} is not valid JSON: {exc}") from exc
    return parse_config_data(data, source=str(p))


def load_servers(
    paths: Iterable[str | Path] | None = None,
    cwd: Path | None = None,
    home: Path | None = None,
) -> tuple[list[ServerEntry], list[str]]:
    """Load from explicit paths, else from discovery.

    Returns ``(entries, warnings)``. Duplicate server names across files keep
    the first occurrence (discovery order = precedence).
    """
    warnings: list[str] = []
    files = [Path(p) for p in paths] if paths else discover_config_files(cwd, home)

    entries: list[ServerEntry] = []
    seen: set[str] = set()
    for f in files:
        try:
            found = load_config_file(f)
        except ConfigError as exc:
            warnings.append(str(exc))
            continue
        for e in found:
            if e.name in seen:
                warnings.append(f"duplicate server '{e.name}' in {f} ignored (already defined)")
                continue
            seen.add(e.name)
            entries.append(e)

    if not files:
        warnings.append("no MCP config files found; pass --config PATH")
    return entries, warnings
