#!/usr/bin/env python3
"""Fixture: a well-behaved MCP server over stdio. Answers initialize + tools/list."""
import json
import sys

TOOLS = [
    {"name": "read_file", "description": "read a file", "inputSchema": {"type": "object"}},
    {"name": "write_file", "description": "write a file", "inputSchema": {"type": "object"}},
    {"name": "list_dir", "description": "list a directory", "inputSchema": {"type": "object"}},
]

SERVER_INFO = {"name": "fixture-healthy", "version": "1.2.3"}


def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main():
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
                "serverInfo": SERVER_INFO,
            }})
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}})
        elif mid is not None:
            send({"jsonrpc": "2.0", "id": mid,
                  "error": {"code": -32601, "message": "method not found"}})


if __name__ == "__main__":
    main()
