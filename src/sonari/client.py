from __future__ import annotations

import time

from sonari.protocol import encode, decode
from sonari.paths import LOCK_PATH, DAEMON_FAIL_MEMO_PATH, socket_connectable
from sonari.platform import transport
from sonari.daemon import ensure_running


class DaemonNotRunning(OSError):
    """Raised when the Sonari daemon socket cannot be reached."""


def send(msg: dict, expect_reply: bool = False, timeout: float = 2.0):
    try:
        s = transport.connect(LOCK_PATH, timeout=timeout)
    except OSError as exc:
        raise DaemonNotRunning(
            "Sonari daemon is not running. Run: sonari install"
        ) from exc
    try:
        s.sendall(encode(msg))
        if not expect_reply:
            return None
        buf = b""
        while b"\n" not in buf:
            data = s.recv(4096)
            if not data:
                break
            buf += data
        if not buf:
            return None
        line = buf.split(b"\n", 1)[0]
        return decode(line)
    finally:
        try:
            s.close()
        except OSError:
            pass


# A broken install must cost ONE timeout, not one per hook event. bin/sonari-hook
# fires as a brand-new OS process per hook event (hooks/hooks.json: every entry
# is `type: command`), so the memo has to survive on disk, not in a module
# variable -- an in-process global resets to nothing on every single call. The
# memo is the mtime of DAEMON_FAIL_MEMO_PATH: whether it exists and how old it
# is IS the state, so a torn/partial write can't leave a corrupt value to parse,
# only "missing" (treated as no memo) or "some real timestamp". All memo I/O is
# wrapped in try/except: this runs inside a Claude Code hook and must never
# raise, even with an unwritable/unreadable SONARI_DIR.
_MEMO_S = 30.0


def reset_failure_memo() -> None:
    """Forget any recorded spawn failure (test seam + explicit recovery)."""
    try:
        DAEMON_FAIL_MEMO_PATH.unlink(missing_ok=True)
    except OSError:
        pass


def _memo_is_fresh() -> bool:
    try:
        age = time.time() - DAEMON_FAIL_MEMO_PATH.stat().st_mtime
    except OSError:
        return False          # no memo, or can't read it -> behave as if there's none
    return 0 <= age < _MEMO_S  # a future mtime (clock skew) is never "fresh"


def _mark_failure() -> None:
    try:
        DAEMON_FAIL_MEMO_PATH.parent.mkdir(parents=True, exist_ok=True)
        DAEMON_FAIL_MEMO_PATH.touch()
    except OSError:
        pass


def ensure_daemon(timeout: float = 3.0) -> None:
    if _connectable():
        reset_failure_memo()
        return
    if _memo_is_fresh():
        return          # we just tried and failed; don't pay the timeout (or a new spawn) again
    ensure_running()
    deadline = time.time() + timeout
    delay = 0.02
    while time.time() < deadline:
        if _connectable():
            reset_failure_memo()
            return
        time.sleep(delay)
        delay = min(delay * 1.6, 0.25)
    _mark_failure()


def _connectable() -> bool:
    return socket_connectable()
