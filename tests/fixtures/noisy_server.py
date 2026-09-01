#!/usr/bin/env python3
"""Fixture: prints non-JSON banner noise on stdout but is otherwise healthy.

Real servers do this. The probe must skip the noise instead of scoring the
server dead.
"""
import json
import sys


def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main():
    sys.stdout.write("[boot] fixture server starting, please wait...\n")
    sys.stdout.write("not json at all\n")
    sys.stdout.flush()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        method = msg.get("method")
        mid = msg.get("id")
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fixture-noisy", "version": "1.0.0"},
            }})
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": mid, "result": {"tools": [
                {"name": "ping", "description": "ping", "inputSchema": {"type": "object"}},
            ]}})


if __name__ == "__main__":
    main()
