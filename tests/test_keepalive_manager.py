"""KeepAliveManager unit tests — all subprocess/timer/clock seams faked.
Spec: docs/superpowers/specs/2026-08-24-bt-keepalive-design.md."""
import threading

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


def make_mgr(now=None):
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


def _timers(kind=None):
    return [t for t in FakeTimer.instances if not t.cancelled and t.started]


def test_activation_spawns_one_player_and_arms_overlap_timer():
    mgr, spawned, clock = make_mgr()
    mgr.set_active(True)
    assert len(spawned) == 1
    assert spawned[0].cmd[0] == "afplay"
    assert spawned[0].cmd[1].endswith("keepalive.wav")
    overlaps = [t for t in FakeTimer.instances if t.interval == 295.0]
    assert len(overlaps) == 1 and overlaps[0].started
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


def test_spawn_failure_goes_degraded_not_raise():
    FakeTimer.instances = []
    def popen(cmd):
        raise OSError("no afplay")
    mgr = KeepAliveManager(popen=popen, timer_factory=FakeTimer,
                           clock=lambda: 0.0)
    mgr.set_active(True)                          # must not raise
    assert mgr.status() == "degraded"
