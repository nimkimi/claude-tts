"""Derive the controlling tty of this process by walking process ancestry.

A Claude Code hook runs as a subprocess whose own stdin/stdout are pipes and
whose /dev/tty is not configured; but an ancestor (the `claude` process) carries
the terminal tab's real tty. We walk parents until we find one, then normalize to
a /dev/ttysNNN path that matches what Terminal.app reports as `tty of tab`.
"""
from __future__ import annotations

import os


def _default_ps(pid: int) -> "tuple[int, str]":
    """Return (ppid, tty_raw) for *pid* via `ps`. Raises on failure (caller guards)."""
    import subprocess
    out = subprocess.run(
        ["ps", "-o", "ppid=,tty=", "-p", str(pid)],
        capture_output=True, text=True, timeout=5,
    ).stdout.strip()
    if not out:
        return (0, "")
    parts = out.split(None, 1)
    ppid = int(parts[0])
    tty = parts[1].strip() if len(parts) > 1 else ""
    return (ppid, tty)


def _normalize(tty: str) -> str:
    """A real tty device name -> /dev/ttysNNN; '??'/'?'/'' -> ''."""
    tty = tty.strip()
    if not tty or tty in ("?", "??"):
        return ""
    if tty.startswith("/dev/"):
        return tty
    return "/dev/" + tty


def controlling_tty(pid: "int | None" = None, ps_runner=None) -> str:
    """First ancestor's real controlling tty as /dev/ttysNNN, else ''. Never raises."""
    runner = ps_runner or _default_ps
    cur = os.getpid() if pid is None else pid
    seen = set()
    try:
        for _ in range(32):  # bounded walk; cannot loop
            if cur in (0, 1) or cur in seen:
                return ""
            seen.add(cur)
            ppid, tty_raw = runner(cur)
            norm = _normalize(tty_raw)
            if norm:
                return norm
            cur = ppid
        return ""
    except Exception:  # noqa: BLE001 - best-effort; any failure -> no tty
        return ""
