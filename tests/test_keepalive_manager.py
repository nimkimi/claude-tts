"""KeepAliveManager unit tests — all subprocess/timer/clock seams faked.
Spec: docs/superpowers/specs/2026-08-24-bt-keepalive-design.md."""
from sonari.daemon.keepalive import KeepAliveManager


class FakeProc:
    def __init__(self, cmd):
        self.cmd = cmd
        self.terminated = False
        self._rc = None

    def poll(self):
        return self._rc

    def wait(self, timeout=None):
        return self._rc if self._rc is not None else 0

    def terminate(self):
        self.terminated = True
        self._rc = -15

    def die(self, rc=1):
        self._rc = rc


class FakeTimer:
    """Records (interval, fn); fires only when the test calls .fire()."""
    instances = []

    def __init__(self, interval, fn):
        self.interval = interval
        self.fn = fn
        self.cancelled = False
        self.started = False
        self.daemon = False
        FakeTimer.instances.append(self)

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True

    def fire(self):
        if not self.cancelled:
            self.fn()


def make_mgr():
    FakeTimer.instances = []
    spawned = []
    clock = {"t": 0.0}

    def popen(cmd):
        proc = FakeProc(cmd)
        spawned.append(proc)
        return proc

    mgr = KeepAliveManager(popen=popen, timer_factory=FakeTimer,
                           clock=lambda: clock["t"])
    return mgr, spawned, clock


def test_activation_spawns_one_player_and_arms_overlap_timer():
    mgr, spawned, clock = make_mgr()
    mgr.set_active(True)
    assert len(spawned) == 1
    assert spawned[0].cmd[0] == "afplay"
    assert spawned[0].cmd[1].endswith("keepalive.wav")
    overlaps = [t for t in FakeTimer.instances if t.interval == 295.0]
    assert len(overlaps) == 1 and overlaps[0].started
    assert overlaps[0].daemon is True   # never wedge interpreter shutdown
    assert mgr.status() == "running"


def test_set_active_true_is_idempotent():
    mgr, spawned, clock = make_mgr()
    mgr.set_active(True)
    mgr.set_active(True)
    assert len(spawned) == 1


def test_overlap_timer_spawns_next_player_before_reaping_old():
    mgr, spawned, clock = make_mgr()
    mgr.set_active(True)
    overlap = [t for t in FakeTimer.instances if t.interval == 295.0][0]
    clock["t"] = 295.0
    overlap.fire()
    assert len(spawned) == 2          # B spawned while A still runs — no gap
    assert not spawned[0].terminated  # A is reaped when it EXITS, never killed early
    rearmed = [t for t in FakeTimer.instances if t.interval == 295.0]
    assert len(rearmed) == 2          # a fresh overlap timer for B


def test_deactivate_arms_hold_then_expiry_stops_players():
    mgr, spawned, clock = make_mgr()
    mgr.set_active(True)
    mgr.set_active(False)
    assert mgr.status() == "hold"
    assert not spawned[0].terminated              # still streaming during hold
    hold = [t for t in FakeTimer.instances if t.interval == 600.0][0]
    assert hold.daemon is True                    # never wedge interpreter shutdown
    hold.fire()
    assert spawned[0].terminated
    assert mgr.status() == "idle"


def test_reactivation_during_hold_cancels_stop_and_keeps_stream():
    mgr, spawned, clock = make_mgr()
    mgr.set_active(True)
    mgr.set_active(False)
    hold = [t for t in FakeTimer.instances if t.interval == 600.0][0]
    mgr.set_active(True)
    hold.fire()                                   # stale timer must be a no-op
    assert not spawned[0].terminated
    assert len(spawned) == 1                      # stream simply continued
    assert mgr.status() == "running"


def test_tick_respawns_dead_player_after_backoff():
    mgr, spawned, clock = make_mgr()
    mgr.set_active(True)
    clock["t"] = 10.0
    spawned[0].die()
    mgr.tick()
    assert len(spawned) == 1                      # backoff: not instantly
    clock["t"] = 11.5
    mgr.tick()
    assert len(spawned) == 2


def test_five_consecutive_fast_deaths_degrade_and_stop_spawning():
    mgr, spawned, clock = make_mgr()
    mgr.set_active(True)
    for _ in range(5):
        spawned[-1].die()
        clock["t"] += 0.5                         # died 0.5s after spawn: FAST
        mgr.tick()                                # observe death, counter++
        clock["t"] += 1.0                         # past BACKOFF_S
        mgr.tick()                                # respawn (until degraded)
    assert mgr.status() == "degraded"
    n = len(spawned)                              # 5: initial + 4 respawns
    clock["t"] += 100.0
    mgr.tick()
    assert len(spawned) == n                      # gave up — no spawn storm
    mgr.set_active(False)                         # no players left: no hold timer needed
    mgr.set_active(True)                          # fresh False->True edge resets the give-up
    assert len(spawned) == n + 1
    assert mgr.status() == "running"


def _drive_to_degraded(mgr, spawned, clock):
    """GIVEUP_N consecutive fast deaths — the cadence of
    test_five_consecutive_fast_deaths_degrade_and_stop_spawning."""
    for _ in range(KeepAliveManager.GIVEUP_N):
        spawned[-1].die()
        clock["t"] += 0.5                         # died 0.5s after spawn: FAST
        mgr.tick()                                # observe death, counter++
        clock["t"] += 1.0                         # past BACKOFF_S
        mgr.tick()                                # respawn (until degraded)


def test_enable_edge_forgives_a_give_up_so_the_toggle_is_a_recovery_lever():
    """`sonari keepalive off` then `on` is the user's ONLY way back from degraded.

    A Bluetooth dropout latches the give-up mid-session, and the session stays
    live — so no set_active(False)->(True) edge is coming to forgive it. Without
    the reset on the enable EDGE the documented recovery did nothing at all and
    the headset kept clipping until the daemon restarted.
    """
    mgr, spawned, clock = make_mgr()
    mgr.set_active(True)
    _drive_to_degraded(mgr, spawned, clock)
    assert mgr.status() == "degraded"
    n = len(spawned)

    mgr.set_enabled(False)
    mgr.set_enabled(True)
    assert len(spawned) == n                      # the flag flip alone spawns nothing
    mgr.set_active(True)                          # the next policy push re-ensures it
    assert len(spawned) == n + 1
    assert mgr.status() == "running"


def test_same_value_enable_calls_do_not_forgive_a_give_up():
    """EDGE-gated, never level-gated. host._keepalive_recheck re-applies the SAME
    config value to set_enabled at the speak loop's ~10 Hz cadence, so a reset per
    CALL would erase the give-up bound ten times a second — turning a permanently
    broken spawn into exactly the storm GIVEUP_N exists to stop.
    """
    mgr, spawned, clock = make_mgr()
    mgr.set_active(True)
    _drive_to_degraded(mgr, spawned, clock)
    n = len(spawned)

    for _ in range(3):                            # the recheck's per-tick sequence
        mgr.set_enabled(True)                     # same value, as the tick applies it
        mgr.set_active(True)
        clock["t"] += 100.0                       # far past BACKOFF_S
        mgr.tick()
    assert mgr.status() == "degraded"
    assert len(spawned) == n                      # still no storm


def test_slow_death_does_not_count_toward_degraded():
    mgr, spawned, clock = make_mgr()
    mgr.set_active(True)
    for _ in range(6):
        clock["t"] += 50.0                        # died long after spawn: SLOW
        spawned[-1].die()
        mgr.tick()                                # observe: counter resets
        clock["t"] += 1.5                         # past BACKOFF_S
        mgr.tick()                                # respawn
    assert mgr.status() == "running"
    assert len(spawned) == 7                      # initial + 6 respawns


def test_disabled_never_spawns_and_terminates_running():
    mgr, spawned, clock = make_mgr()
    mgr.set_active(True)
    mgr.set_enabled(False)
    assert spawned[0].terminated
    assert mgr.status() == "disabled"
    mgr.set_active(True)
    assert len(spawned) == 1
    mgr.set_enabled(True)
    mgr.set_active(True)
    assert len(spawned) == 2


def test_stop_cancels_timers_and_terminates_players():
    mgr, spawned, clock = make_mgr()
    mgr.set_active(True)
    mgr.stop()
    assert spawned[0].terminated
    assert all(t.cancelled for t in FakeTimer.instances if t.started)
    assert mgr.status() == "idle"


def test_orphaned_overlap_chain_cannot_resurrect_an_idle_manager():
    """An overlap timer outliving its player must not restart the chain forever.

    A player that crashes on its own is pruned by tick(), which leaves the list
    empty — so the following set_active(False) arms NO hold timer and nothing
    cancels the overlap timer. Ungated, its callback would spawn a player AND a
    fresh overlap timer on an idle manager: a self-perpetuating afplay chain that
    a repeat set_active(False) cannot even stop (it early-returns on _want).
    """
    mgr, spawned, clock = make_mgr()
    mgr.set_active(True)
    overlap = [t for t in FakeTimer.instances if t.interval == 295.0][0]
    spawned[0].die()
    clock["t"] = 10.0
    mgr.tick()                                    # prunes the corpse; backoff blocks respawn
    mgr.set_active(False)                         # no players left ⇒ no hold armed
    overlap.fn()                                  # the still-armed orphan fires
    assert len(spawned) == 1                      # no chain, no storm
    assert mgr.status() == "idle"


def test_crash_during_hold_is_resurrected_until_hold_expiry():
    """The DESIGNED counterpart: while a hold is in flight the chain continues.

    The hold exists to keep the device open for a user who comes right back, so a
    player that dies mid-hold is replaced by the overlap chain rather than leaving
    the stream dead — and an armed hold with a momentarily empty player list still
    reads "hold", never "idle".
    """
    mgr, spawned, clock = make_mgr()
    mgr.set_active(True)
    overlap = [t for t in FakeTimer.instances if t.interval == 295.0][0]
    mgr.set_active(False)                         # players alive ⇒ hold armed
    spawned[0].die()
    clock["t"] = 10.0
    mgr.tick()
    assert mgr.status() == "hold"                 # armed hold outranks the empty list
    clock["t"] = 295.0
    overlap.fire()
    assert len(spawned) == 2                      # device stays open for the hold
    assert mgr.status() == "hold"


def test_stale_hold_timer_callback_is_a_no_op():
    """A real threading.Timer can fire just past its cancel window; FakeTimer.fire()
    refuses cancelled timers, so call the callback directly to exercise the guard."""
    mgr, spawned, clock = make_mgr()
    mgr.set_active(True)
    mgr.set_active(False)
    stale = [t for t in FakeTimer.instances if t.interval == 600.0][0]
    mgr.set_active(True)                          # cancels the hold and re-wants
    stale.fn()                                    # ...but it ran anyway
    assert not spawned[0].terminated              # identity check saved the stream
    assert len(spawned) == 1
    assert mgr.status() == "running"


def test_stale_overlap_timer_callback_does_not_double_spawn():
    """Same guard on the overlap side: a timer that already handed the chain over
    to its successor must not spawn again if its callback runs a second time."""
    mgr, spawned, clock = make_mgr()
    mgr.set_active(True)
    first = [t for t in FakeTimer.instances if t.interval == 295.0][0]
    clock["t"] = 295.0
    first.fire()                                  # live fire: spawns B, re-arms a NEW timer
    assert len(spawned) == 2
    first.fn()                                    # the superseded timer fires again
    assert len(spawned) == 2                      # identity check rejects it


def test_spawn_failure_goes_degraded_not_raise():
    FakeTimer.instances = []
    def popen(cmd):
        raise OSError("no afplay")
    mgr = KeepAliveManager(popen=popen, timer_factory=FakeTimer,
                           clock=lambda: 0.0)
    mgr.set_active(True)                          # must not raise
    assert mgr.status() == "degraded"


class LockProbeProc(FakeProc):
    """Records whether the manager lock was FREE at terminate()/wait() time.

    threading.Lock is not reentrant, so acquire(blocking=False) from the reaping
    thread itself returns False iff the manager still holds it.
    """

    def __init__(self, cmd, box):
        FakeProc.__init__(self, cmd)
        self._box = box
        self.lock_free_at_terminate = None
        self.lock_free_at_wait = None

    def _probe(self):
        lock = self._box["mgr"]._lock
        got = lock.acquire(blocking=False)
        if got:
            lock.release()
        return got

    def terminate(self):
        self.lock_free_at_terminate = self._probe()
        FakeProc.terminate(self)

    def wait(self, timeout=None):
        self.lock_free_at_wait = self._probe()
        return FakeProc.wait(self, timeout)


def test_manager_lock_is_never_held_across_a_child_reap():
    """THE contention invariant. terminate()+wait(2.0) per player is unbounded-ish
    work; holding self._lock across it makes every other manager entry point queue
    behind it — including set_active(), which lifecycle handlers call while holding
    the DAEMON lock. Two overlapped wedged players would then stall the whole daemon
    for ~4s. Every reaping path must compute what to kill under the lock and kill it
    outside."""
    def run(trigger):
        box = {}
        FakeTimer.instances = []
        procs = []

        def popen(cmd):
            proc = LockProbeProc(cmd, box)
            procs.append(proc)
            return proc

        mgr = KeepAliveManager(popen=popen, timer_factory=FakeTimer,
                               clock=lambda: 0.0)
        box["mgr"] = mgr
        mgr.set_active(True)
        # Two overlapped players: the chain hands over before the file ends, so a
        # real shutdown can find more than one child to reap.
        [t for t in FakeTimer.instances if t.interval == 295.0][0].fire()
        assert len(procs) == 2
        trigger(mgr)
        assert all(p.lock_free_at_terminate for p in procs), \
            "manager lock held across terminate()"
        assert all(p.lock_free_at_wait for p in procs), \
            "manager lock held across the bounded wait() — daemon-lock stall"

    def expire_hold(mgr):
        mgr.set_active(False)
        holds = [t for t in FakeTimer.instances
                 if t.interval == KeepAliveManager.HOLD_S and not t.cancelled]
        holds[-1].fire()

    run(lambda mgr: mgr.stop())
    run(lambda mgr: mgr.set_enabled(False))
    run(expire_hold)
