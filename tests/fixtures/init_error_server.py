#!/usr/bin/env python3
"""Fixture: starts, but rejects the initialize handshake with a JSON-RPC error."""
import json
import sys


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if msg.get("method") == "initialize":
            sys.stdout.write(json.dumps({
                "jsonrpc": "2.0",
                "id": msg.get("id"),
                "error": {"code": -32600, "message": "unsupported protocol version"},
            }) + "\n")
            sys.stdout.flush()
            return


if __name__ == "__main__":
    main()
