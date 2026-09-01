#!/usr/bin/env python3
"""Fixture: accepts input and never answers. Must trip the probe timeout.

It also spawns a grandchild that would outlive a naive proc.terminate(), so
the suite can prove the whole process group is killed, not just the child.
"""
import os
import subprocess
import sys
import time


def main():
    pidfile = os.environ.get("MCPF_CHILD_PIDFILE")
    if pidfile:
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(600)"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        with open(pidfile, "w", encoding="utf-8") as fh:
            fh.write(str(child.pid))
            fh.flush()
            os.fsync(fh.fileno())
    # Deliberately never respond to anything.
    while True:
        time.sleep(0.2)


if __name__ == "__main__":
    main()
