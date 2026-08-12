"""Stop the running daemon during uninstall — and PROVE it stopped.

launchctl unload cannot stop a daemon that ensure_running() spawned with
start_new_session=True: it is not a launchd job. The lockfile is the only
record of its pid, and uninstall deletes it — so read the pid FIRST.

Proof of death is the singleton flock, not the absence of a lockfile: the
daemon holds SINGLETON_PATH for its whole process lifetime, so acquiring it
means no daemon survives.
"""
from __future__ import annotations

import os
import signal
import time

from sonari import paths
from sonari.platform import transport


def _read_pid():
    """The pid recorded in the lockfile, or None if it is absent, unreadable,
    corrupt, or its pid field is missing/non-numeric. Reuses
    transport.read_lockfile rather than re-parsing JSON here."""
    data = transport.read_lockfile(paths.LOCK_PATH)
    if not isinstance(data, dict):
        return None
    try:
        return int(data["pid"])
    except (KeyError, TypeError, ValueError):
        return None


def _singleton_free() -> bool:
    """True if SINGLETON_PATH's flock is acquirable — i.e. no daemon holds it.

    A missing SONARI_DIR is treated as proof of absence: the daemon cannot run
    without writing into that directory, so if it does not exist, nothing can
    hold the lock. This also sidesteps transport.acquire_singleton's
    os.open(..., O_CREAT), which raises FileNotFoundError when the parent
    directory itself is gone (e.g. `sonari uninstall` run where Sonari was
    never installed) rather than "lock held" — that error is not proof of
    life, so it must not be read as "still-running".

    Any OTHER failure to open or lock the file (permission denied, a
    directory sitting where the file should be, ...) is likewise NOT treated
    as proof of death — it is reported as still held, so uninstall never
    claims success out of a mere inability to check.
    """
    if not os.path.isdir(str(paths.SONARI_DIR)):
        return True
    try:
        held = transport.acquire_singleton(paths.SINGLETON_PATH)
    except OSError:
        return False
    if held is None:
        return False
    try:
        held.close()          # release immediately; we only wanted the answer
    except OSError:
        pass
    return True


def stop_daemon(timeout: float = 10.0) -> str:
    """SIGTERM the running daemon and wait for proof it died.

    Returns "not-running" | "stopped" | "still-running".

    The window must OUTLAST the daemon's own graceful shutdown, or the proof
    loses a race it was never told it was in. host.py's run() burns
    `speak_thread.join(timeout=5.0)` in full whenever the speak thread is
    inside proc.wait(), and the persistence-thread join before it has no
    timeout at all — so a daemon that is MID-UTTERANCE at SIGTERM needs more
    than 5 s to exit. At the old 5.0 s this reported "still-running" for a
    daemon that had already died: found live, where `pgrep` showed zero the
    moment after the warning printed. Costs nothing when the daemon is idle —
    the poll returns the instant the flock frees, so the ceiling is only ever
    paid when it really is still exiting.
    SIGTERM (never SIGKILL): host.py's handler exits into SP6's shutdown flush,
    so the pile survives the stop we are about to disclose to the user.

    The pid comes from the lockfile alone (spec §8.1 step 3) — this does not
    independently verify the pid still belongs to a Sonari process before
    signalling it. A stale, OS-recycled pid is the residual risk that leaves;
    it is bounded by SIGTERM being a request a foreign process can ignore
    (never SIGKILL) and by "stopped" being reported only on the singleton
    flock's positive proof, never inferred from the signal alone.
    """
    pid = _read_pid()
    # pid <= 0 (corrupt lockfile) is never signalled: 0 is a process-group
    # broadcast on POSIX and a negative pid targets an entire process group.
    if pid is None or pid <= 0:
        return "not-running" if _singleton_free() else "still-running"
    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, OverflowError):
        # ProcessLookupError: already gone. OverflowError: a corrupt lockfile
        # can hold a pid too large for C's pid_t (os.kill raises OverflowError,
        # not OSError, in that case) -- it names no real process either way.
        pass
    except OSError:
        return "still-running"                # e.g. owned by another user
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _singleton_free():
            return "stopped"
        time.sleep(0.1)
    return "still-running"
