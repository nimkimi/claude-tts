"""Audit pins: the Bluetooth keep-alive manager's give-up latch is easier to
trigger, and harder to recover from, than the spec ratifies.

See /Users/Nima.Hakimi/projects/private/claude-tts/scratchpad/e3-review/test-audit/HUNT-RESULTS.json
findings 16 and 17 for the full adjudication.
"""
import pytest

from tests.daemon_helpers import make_daemon
from sonari.daemon.keepalive import KeepAliveManager


@pytest.mark.xfail(
    strict=True,
    reason="BUG-9 (new-in-receipts, wired 2026-08-24): the prescribed "
           "'keepalive off' then 'keepalive on' recovery does nothing when "
           "both land inside one utterance -- no speak-loop recheck runs "
           "between them, so the manager never observes the edge that "
           "forgives a give-up; awaiting owner fix decision -- see HUNT "
           "dossier finding 17.",
)
def test_bug9_keepalive_off_then_on_landing_in_one_utterance_silently_does_nothing():
    """BUG-9 (CONFIRMED, finding 17, severity medium).

    mechanism: daemon/features/control.py:284-299 on_set_keepalive writes
    ctx.host.config["keepalive_enabled"] and saves -- by SINGLE-WRITER
    DISCIPLINE it must never touch the manager directly.
    daemon/host.py:1308-1310, inside `if reap:`, is the SOLE caller of
    keepalive.set_enabled(), and it POLLS that config value; host.py:1393
    puts that call at the TOP of _speak_loop_once, so it does not run at all
    while speaker.speak() is blocking on an utterance. keepalive.py:109-118
    set_enabled forgives a give-up ONLY on the False->True EDGE of
    self._enabled. Two SET_KEEPALIVE messages that land between two
    rechecks therefore collapse into ONE same-value set_enabled(True): no
    edge is ever observed, no forgiveness, and _fast_deaths/_degraded both
    survive -- while cli/control.py still prints "keepalive off" then
    "keepalive on" as if both landed.

    ratified basis: keepalive.py:102-104 and
    tests/test_keepalive_manager.py name this toggle "the user's ONLY
    recovery lever" from a mid-session give-up; a remedy that reports
    success while changing nothing violates that contract.
    """
    class FastDyingProc:
        def poll(self):
            return 1              # already exited, every time it's asked

        def wait(self, timeout=None):
            return 1

        def terminate(self):
            pass

    daemon, _, _, sessions, config = make_daemon(foreground="A")
    sessions.register("A", cwd="/x/a")
    daemon.keepalive._popen = lambda cmd: FastDyingProc()

    # Drive the manager into "degraded" directly (the give-up mechanism
    # itself is BUG-14's target, not this one) -- what matters here is
    # ONLY the edge-detection in set_enabled.
    daemon.keepalive._enabled = True
    daemon.keepalive._want = True
    daemon.keepalive._degraded = True
    daemon.keepalive._fast_deaths = 5
    assert daemon.keepalive.status() == "degraded"

    # BOTH toggles land while a single utterance is in flight -- no recheck
    # (reap=True) runs between them, exactly as when the voice is reading.
    config["keepalive_enabled"] = False
    config["keepalive_enabled"] = True
    daemon._keepalive_recheck(reap=True)     # the FIRST recheck after both writes

    # RATIFIED: the prescribed off-then-on recovery must actually recover.
    assert daemon.keepalive.status() == "running"


@pytest.mark.xfail(
    strict=True,
    reason="BUG-14 (pre-existing at 073b82b, per the hunter's own base-tree "
           "control run): a spawn that cannot even start gives up "
           "immediately, bypassing the spec's own 5-strike bound entirely; "
           "awaiting owner fix decision -- see HUNT dossier finding 16.",
)
def test_bug14_one_failed_spawn_gives_up_immediately_bypassing_the_five_strike_bound():
    """BUG-14 (CONFIRMED via DOWNGRADED verdict; finding 16, corrected
    severity low, corrected from medium).

    mechanism: keepalive.py:220-231 _spawn_locked wraps the Popen call in
    `except Exception: self._degraded = True; return` -- it does NOT touch
    self._fast_deaths, so keepalive.py:155's `if self._fast_deaths >=
    GIVEUP_N` (the whole 5-strike machine) is never even consulted for a
    spawn that cannot START at all (a transient fork failure -- EAGAIN/
    ENOMEM under load -- or a momentarily unreadable SONARI_DIR inside
    ensure_silence_wav()). One such failure latches _degraded permanently:
    tick()'s respawn branch (keepalive.py:161) and set_active's ensure
    branch (keepalive.py:138) both require `not self._degraded`, and the
    only forgiving edges (set_enabled/set_active False->True) require a
    20-minute speech-idle+HID-idle window (host.py
    KEEPALIVE_PRESENCE_S=1200) that never arrives while the owner is
    actively working.

    ratified basis: docs/superpowers/specs/2026-08-24-bt-keepalive-design.md
    :123-126, verbatim: "A player that exits early (afplay missing, audio
    device error) is respawned with a 1s backoff. If 5 consecutive spawns
    die within 2s each, the manager gives up for this activation." A missing
    afplay raises FileNotFoundError out of Popen -- the spec's OWN
    parenthetical assigns exactly this cannot-start case the 5-strike path,
    and the code gives up on strike 1.
    """
    def flaky_popen(cmd):
        raise OSError("fork failed: resource temporarily unavailable")

    class NoopTimer:
        def __init__(self, interval, fn):
            pass

        def start(self):
            pass

        def cancel(self):
            pass

    mgr = KeepAliveManager(popen=flaky_popen, timer_factory=NoopTimer)
    mgr.set_active(True)      # the daemon's live-session verdict, first activation

    # RATIFIED: ONE failed fork must count as a strike toward the 5-strike
    # bound, not an immediate, permanent give-up.
    assert mgr._fast_deaths == 1
    assert mgr.status() != "degraded"
