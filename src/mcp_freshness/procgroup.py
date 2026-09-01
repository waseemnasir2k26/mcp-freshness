"""Cross-platform process-group spawn + hard kill.

A hung MCP server must never hang us, and terminating only the direct child
leaves `npx`/`uv` grandchildren running. So we always start the child in its
own group/session and kill the whole group.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from typing import Any

IS_WINDOWS = os.name == "nt"


def spawn_kwargs() -> dict[str, Any]:
    if IS_WINDOWS:
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return {"creationflags": flags}
    return {"start_new_session": True}


def kill_group(proc: subprocess.Popen, grace: float = 0.5) -> None:
    """Kill the child and every process it spawned. Idempotent, never raises."""
    if proc.poll() is not None:
        _drain(proc)
        return

    try:
        if IS_WINDOWS:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            try:
                proc.wait(timeout=grace)
            except subprocess.TimeoutExpired:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:  # noqa: BLE001 - best effort teardown
        pass

    try:
        proc.kill()
    except Exception:  # noqa: BLE001
        pass
    try:
        proc.wait(timeout=grace)
    except Exception:  # noqa: BLE001
        pass
    _drain(proc)


def _drain(proc: subprocess.Popen) -> None:
    """Close pipes so no fds leak when we abandon a server."""
    for stream in (proc.stdin, proc.stdout, proc.stderr):
        try:
            if stream is not None:
                stream.close()
        except Exception:  # noqa: BLE001
            pass


def is_alive(pid: int) -> bool:
    """Best-effort liveness check for a pid (used by tests for cleanup proof)."""
    if pid <= 0:
        return False
    if IS_WINDOWS:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return str(pid) in (out.stdout or "")
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


PY = sys.executable
