"""Thin Python supervisor loop — Task Scheduler launches this; it restarts the
sonari.daemon process indefinitely with exponential back-off.

WINDOWS-only behaviour, but the module imports cleanly on macOS/Linux (the
process-creation flags are hex literals, not subprocess.CREATE_NO_WINDOW which
is win32-only). "Imports + mock-green" does NOT mean Windows-verified — the
DETACHED_PROCESS/CREATE_NO_WINDOW spawn behaviour is a deferred acceptance item
(docs/superpowers/M2-WINDOWS-ACCEPTANCE.md).

Body copied verbatim from docs/superpowers/m2-windows-api-reference.md
(§Thin Python supervisor loop), adapting only the import location.
"""
from __future__ import annotations

import subprocess
import time

# These constants are defined in subprocess only on win32.
# Use hex literals so this file imports cleanly on macOS/Linux.
_CREATE_NO_WINDOW = 0x08000000
_DETACHED_PROCESS = 0x00000008
_SPAWN_FLAGS      = _CREATE_NO_WINDOW | _DETACHED_PROCESS  # 0x08000008

# Never combine start_new_session=True with DETACHED_PROCESS:
# Python 3.9+ raises ValueError on Windows if both are set.


def launch_spec(pythonw: str) -> tuple:
    """Return (argv, spawn_kwargs) compatible with subprocess.Popen(**kwargs).

    argv drives both the supervisor loop and is returned from
    WinSupervisorBackend.launch_spec() for the lazy-start path.
    """
    argv = [pythonw, "-m", "sonari.daemon"]
    kwargs = dict(
        creationflags=_SPAWN_FLAGS,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        # start_new_session intentionally absent — incompatible with DETACHED_PROCESS
    )
    return argv, kwargs


def run_supervisor_loop(pythonw: str) -> None:
    """Restart sonari.daemon indefinitely with exponential back-off.

    Back-off resets to base when the daemon ran for >= 300 s (healthy restart).
    Sequence (seconds): 2, 4, 8, 16, 32, 64, 120, 120, 120 ...
    """
    BASE, CAP, HEALTHY_UPTIME = 2, 120, 300
    attempt = 0
    while True:
        argv, kwargs = launch_spec(pythonw)
        t_start = time.monotonic()
        proc = subprocess.Popen(argv, **kwargs)
        proc.wait()  # blocks until daemon exits
        elapsed = time.monotonic() - t_start
        if elapsed >= HEALTHY_UPTIME:
            attempt = 0          # reset debt after a healthy run
        else:
            attempt += 1
        delay = min(BASE * (2 ** (attempt - 1)), CAP)
        time.sleep(delay)


# Entry point when Task Scheduler launches this file directly:
# schtasks Action: pythonw.exe "<path>/supervisor_loop.py"
if __name__ == "__main__":
    from sonari.platform.windows.supervisor import WinSupervisorBackend
    pw = WinSupervisorBackend().resolve_python()
    if pw:
        run_supervisor_loop(pw)
