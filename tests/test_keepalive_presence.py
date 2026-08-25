"""Presence window (Task 6): a live session is not enough.

The owner never closes terminal tabs, so tty-anchored liveness alone made
"session-scoped" mean always-on — the coreaudiod assertion held 24/7 and the Mac
never idle-slept. The daemon now pushes `active = alive AND present`, where
present == spoke recently OR HID input recently.

Discipline these tests exist to pin (the review seats hunt exactly these):
the ioreg shell-out runs ONLY from the lock-free speak-loop site, never from a
bare handler call under the daemon lock; it never runs for a dead roster; a
broken sampler fails OPEN (stream held, never silent clipping); and the TTL
bypass that re-arms a returning user cannot become a 10 Hz spawn storm.
"""
import time

from sonari.protocol import PROTOCOL_VERSION, MsgType
from sonari.queue import SpeechItem
from tests.daemon_helpers import make_daemon
from tests.test_keepalive_manager import FakeProc, FakeTimer


def _msg(t, session="s1", **kw):
    m = {"v": PROTOCOL_VERSION, "type": t, "session": session}
    m.update(kw)
    return m


def _seam(daemon):
    FakeTimer.instances = []
    spawned = []

    def popen(cmd):
        proc = FakeProc(cmd)
        spawned.append(proc)
        return proc

    daemon.keepalive._popen = popen
    daemon.keepalive._timer_factory = FakeTimer
    return spawned


class FakeHid:
    """Counting HID-idle seam. `idle` is seconds since the last keypress;
    `raises` makes the sampler blow up the way a missing/wedged ioreg would."""

    def __init__(self, idle=0.0):
        self.idle = idle
        self.raises = False
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.raises:
            raise OSError("ioreg is gone")
        return self.idle


def _hid(daemon, idle=0.0):
    hid = FakeHid(idle)
    daemon._hid_idle_s = hid
    return hid


def _live(daemon):
    """A live session with the keep-alive already running (the bare SESSION_START
    path never samples, so this leaves the HID seam untouched)."""
    daemon.handle_message(_msg(MsgType.SESSION_START))


def _drain(daemon):
    """Empty every stream queue. SESSION_START itself enqueues the session
    announce and the install hint, and no speak loop runs here to drain them —
    so queue state is set explicitly in every test where it decides the outcome."""
    for st in list(daemon._state._streams.values()):
        st.queue.clear()


def _queue_item(daemon, session="s1"):
    daemon._stream(session).queue.enqueue(
        SpeechItem(id=1, session=session, kind="prose", text="hi",
                   is_decision=False))


# ---- presence composition -------------------------------------------------


def test_recent_speech_holds_the_stream_and_never_shells_out():
    """Speech inside the window IS presence — an agent reading to a listener who
    has not touched the keyboard for an hour must not lose the stream. And the
    common case must cost nothing: no ioreg while the voice is working."""
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    spawned = _seam(daemon)
    hid = _hid(daemon, idle=99999.0)          # HID says "long gone"
    _live(daemon)
    daemon._keepalive_last_spoke = time.monotonic()
    daemon._keepalive_recheck(reap=True)
    assert daemon.keepalive.status() == "running"
    assert not spawned[0].terminated
    assert hid.calls == 0


def test_stale_speech_and_idle_input_release_the_stream():
    """Nobody spoke, nobody typed: the tab is open but the human is not there."""
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    spawned = _seam(daemon)
    hid = _hid(daemon, idle=daemon.KEEPALIVE_PRESENCE_S + 100.0)
    _live(daemon)
    assert daemon.keepalive.status() == "running"
    assert daemon._keepalive_last_spoke is None    # fresh boot == not spoke-recently
    daemon._keepalive_recheck(reap=True)
    assert hid.calls == 1
    assert daemon.keepalive.status() == "hold"     # trailing hold, not a hard cut


def test_input_within_the_window_is_presence_without_any_speech():
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    _seam(daemon)
    hid = _hid(daemon, idle=daemon.KEEPALIVE_PRESENCE_S - 1.0)
    _live(daemon)
    daemon._keepalive_recheck(reap=True)
    assert hid.calls == 1
    assert daemon.keepalive.status() == "running"


def test_fresh_input_re_arms_after_a_release():
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    _seam(daemon)
    hid = _hid(daemon, idle=daemon.KEEPALIVE_PRESENCE_S + 100.0)
    _live(daemon)
    _drain(daemon)                                 # no bypass in play: the TTL alone
    daemon._keepalive_recheck(reap=True)
    assert daemon.keepalive.status() == "hold"
    hid.idle = 1.0                                 # the user comes back
    daemon._keepalive_hid_at -= daemon.KEEPALIVE_HID_TTL_S + 1.0   # cache expires
    daemon._keepalive_recheck(reap=True)
    assert hid.calls == 2
    assert daemon.keepalive.status() == "running"


def test_a_dead_roster_never_shells_out():
    """`active = alive AND present` short-circuits: no live session means no
    reason to ask the OS anything."""
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    _seam(daemon)
    hid = _hid(daemon)
    daemon._keepalive_recheck(reap=True)
    assert hid.calls == 0
    assert daemon.keepalive.status() == "idle"


# ---- where the sampler may run --------------------------------------------


def test_bare_handler_calls_never_sample_fresh():
    """THE lock-ordering invariant: the lifecycle handlers run UNDER the daemon
    lock and ioreg can block for tens of ms. Only the lock-free speak-loop site
    (reap=True) may sample; a bare call uses the cached verdict."""
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    _seam(daemon)
    hid = _hid(daemon, idle=99999.0)
    daemon.handle_message(_msg(MsgType.SESSION_START))
    daemon.handle_message(_msg(MsgType.SESSION_END))
    daemon.handle_message(_msg(MsgType.SESSION_START, session="s2"))
    daemon._keepalive_recheck()
    assert hid.calls == 0
    # ...and the cached verdict is the fail-open default, so a handler can never
    # cut the stream on a stale sample either.
    assert daemon.keepalive.status() == "running"


# ---- the cache and its bypass ---------------------------------------------


def test_the_verdict_is_cached_across_ticks():
    """At 10 Hz an unconditional sample would be 10 ioreg spawns a second."""
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    _seam(daemon)
    hid = _hid(daemon, idle=daemon.KEEPALIVE_PRESENCE_S + 100.0)
    _live(daemon)
    _drain(daemon)                                 # nothing waiting: the TTL alone
    for _ in range(20):
        daemon._keepalive_recheck(reap=True)
    assert hid.calls == 1
    assert daemon.keepalive.status() == "hold"


def test_queued_items_bypass_the_ttl_so_the_first_utterance_is_not_clipped():
    """The returning user's re-arm path: the cached verdict says absent, so it
    would hold keep-alive off — but something is waiting to be heard, and only a
    fresh sample can turn the stream back on before it plays."""
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    _seam(daemon)
    hid = _hid(daemon, idle=daemon.KEEPALIVE_PRESENCE_S + 100.0)
    _live(daemon)
    _drain(daemon)
    daemon._keepalive_recheck(reap=True)
    assert (hid.calls, daemon.keepalive.status()) == (1, "hold")
    hid.idle = 1.0                                 # back at the keyboard
    daemon._keepalive_hid_at -= daemon.KEEPALIVE_HID_MIN_S + 0.5   # still inside the TTL
    _queue_item(daemon)
    daemon._keepalive_recheck(reap=True)
    assert hid.calls == 2
    assert daemon.keepalive.status() == "running"


def test_the_bypass_never_samples_faster_than_the_floor():
    """THE anti-spin pin. A queue that cannot drain (every stream stopped, or the
    listener genuinely away) is "items waiting" on every one of the loop's ten
    ticks a second — a level-triggered bypass would shell out ten times a second
    for as long as it lasts. The bypass SHORTENS the TTL, it never removes it."""
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    _seam(daemon)
    hid = _hid(daemon, idle=daemon.KEEPALIVE_PRESENCE_S + 100.0)
    _live(daemon)
    _drain(daemon)
    daemon._keepalive_recheck(reap=True)
    assert hid.calls == 1
    _queue_item(daemon)
    for _ in range(20):                            # a whole loop-second of ticks
        daemon._keepalive_recheck(reap=True)
    assert hid.calls == 1
    assert daemon.keepalive.status() == "hold"


def test_no_bypass_while_the_cached_verdict_is_present():
    """Items waiting are a reason to re-sample only when the cached verdict would
    keep the stream OFF. With keep-alive already held, a fresh sample can only
    confirm it or wrongly cut it — so the TTL stands."""
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    _seam(daemon)
    hid = _hid(daemon, idle=1.0)
    _live(daemon)
    _drain(daemon)
    daemon._keepalive_recheck(reap=True)
    assert (hid.calls, daemon.keepalive.status()) == (1, "running")
    _queue_item(daemon)
    daemon._keepalive_hid_at -= daemon.KEEPALIVE_HID_MIN_S + 0.5
    for _ in range(10):
        daemon._keepalive_recheck(reap=True)
    assert hid.calls == 1


def test_an_empty_queue_never_bypasses():
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    _seam(daemon)
    hid = _hid(daemon, idle=daemon.KEEPALIVE_PRESENCE_S + 100.0)
    _live(daemon)
    _drain(daemon)
    daemon._keepalive_recheck(reap=True)
    daemon._keepalive_hid_at -= daemon.KEEPALIVE_HID_MIN_S + 0.5
    daemon._keepalive_recheck(reap=True)
    assert hid.calls == 1


# ---- failure behaviour -----------------------------------------------------


def test_a_raising_sampler_fails_open_and_logs_once(capsys):
    """A broken sampler must degrade to THIS build's behaviour (stream held),
    never to silent clipping — and never flood the daemon log at 10 Hz."""
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    spawned = _seam(daemon)
    hid = _hid(daemon)
    hid.raises = True
    _live(daemon)
    capsys.readouterr()                            # drop construction noise
    daemon._keepalive_recheck(reap=True)
    assert daemon.keepalive.status() == "running"
    assert not spawned[0].terminated
    assert "ioreg is gone" in capsys.readouterr().err
    for _ in range(3):
        daemon._keepalive_hid_at -= daemon.KEEPALIVE_HID_TTL_S + 1.0
        daemon._keepalive_recheck(reap=True)
    assert capsys.readouterr().err == ""
    # The seam has its OWN latch: a broken sampler must not consume the outer
    # once-latch that exists for keep-alive LOGIC failures.
    assert daemon._keepalive_reported is False


def test_an_unparseable_sample_fails_open():
    """`ioreg` ran but nothing matched (a macOS release renames the key): None is
    "I do not know", and not-knowing holds the stream."""
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    _seam(daemon)
    daemon._hid_idle_s = lambda: None
    _live(daemon)
    daemon._keepalive_recheck(reap=True)
    assert daemon.keepalive.status() == "running"


def test_a_failed_sample_is_cached_like_any_other():
    """Fail-open must not mean fail-often: a sampler that raises every call is
    bounded by the same TTL, or a wedged ioreg would be paid for on every tick."""
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    _seam(daemon)
    hid = _hid(daemon)
    hid.raises = True
    _live(daemon)
    for _ in range(20):
        daemon._keepalive_recheck(reap=True)
    assert hid.calls == 1


# ---- the spoke-recently stamp ---------------------------------------------


def test_note_spoken_stamps_the_presence_window():
    """ONE stamp site: every played item funnels through note_spoken, next to the
    _last_drain heartbeat it mirrors."""
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    _seam(daemon)
    assert daemon._keepalive_last_spoke is None
    item = SpeechItem(id=1, session="s1", kind="prose", text="hi",
                      is_decision=False)
    daemon.note_spoken(item, completed=True)
    assert daemon._keepalive_last_spoke is not None
    assert abs(daemon._keepalive_last_spoke - time.monotonic()) < 1.0


def test_an_interrupted_utterance_still_counts_as_speech():
    """completed=False means the audio was CUT, not that it never played — and a
    cut is a hotkey press, which is presence twice over. Ungated on purpose."""
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    _seam(daemon)
    item = SpeechItem(id=1, session="s1", kind="prose", text="hi",
                      is_decision=False)
    daemon.note_spoken(item, completed=False)
    assert daemon._keepalive_last_spoke is not None
