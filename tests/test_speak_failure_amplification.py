"""C1: a persistently failing speaker must not amplify.

`_signal_speak_failure` enqueues SPEAK_FAILURE_WORD onto the FAILING session's
own queue. The speak loop pops that word, speaks it through the same broken
speaker, raises again, and enqueues another — so the queue never empties,
`item is None` is never true, and the loop never reaches its wait. Measured
steady state before the fix: one `say` spawn, one `afplay` spawn and one
traceback per iteration, unbounded, on BOTH loop branches (the normal one and
the held/stopped one — `cue()` marks the word pause-exempt, so the held branch
pops it too).

The amplification is older than this branch, but T1 widened the INPUT SET:
nonzero-exit, spawn-failure and hung-then-killed shapes now raise. So the exact
incident I3 exists to catch — AudioQueueStart(-66681), `say` exiting nonzero
fast — went from "silent but stable" to 100% CPU with unbounded spawns, and it
pins `_stream_quiescent` false forever, so the keep-going gate never opens and
no other session is ever adopted.

The property under test is BOUNDEDNESS: the spawn count must not grow with the
number of loop iterations. Every spawn shape is counted together —
`speaker.speak` (the `say`), `speaker.transient` (the `afplay` tone) and
`voiceout.speak_direct` (the raw `say` fallback) — because suppressing only one
of them just moves the spin to another (in particular, a suppressed word that
leaves `spoken` False would spin on the #54 gap-B `speak_direct` fallback).
"""
import pytest

from tests.daemon_helpers import make_daemon
from sonari.daemon.host import SPEAK_FAILURE_WORD


# One failing utterance (say + tone) plus the one failure word it is allowed to
# enqueue (say + tone). Anything above this is per-iteration amplification.
SPAWN_BOUND = 4


def _always_failing(daemon, speaker, monkeypatch):
    """Wire a speaker whose every speak() raises, and count every spawn shape."""
    counts = {"say": 0, "tone": 0, "direct": 0}

    def boom(*a, **k):
        counts["say"] += 1
        raise RuntimeError("say exited 1: AudioQueueStart(-66681)")

    def tone(kind):
        counts["tone"] += 1
        speaker.earcons.append(kind)

    def direct(*a, **k):
        counts["direct"] += 1

    monkeypatch.setattr(speaker, "speak", boom)
    monkeypatch.setattr(speaker, "transient", tone)
    monkeypatch.setattr("sonari.cli.voiceout.speak_direct", direct)
    # The fix waits out a failure-shaped turn; keep the suite fast.
    daemon._poll_interval = 0.001
    return counts


def _arm(daemon, held: bool) -> None:
    """Queue one item the loop will claim, on the branch under test."""
    if held:
        daemon._stream("fg").stopped = True
        daemon._enqueue("fg", "prose", "Stopped.", False, pause_exempt=True)
    else:
        daemon._enqueue("fg", "prose", "hello", False)


@pytest.mark.parametrize("held", [False, True], ids=["normal", "held"])
@pytest.mark.parametrize("iterations", [6, 30])
def test_persistent_speak_failure_does_not_amplify(monkeypatch, held, iterations):
    """Bounded means independent of N: 30 turns must cost no more than 6."""
    daemon, queue, speaker, *_ = make_daemon(foreground="fg")
    counts = _always_failing(daemon, speaker, monkeypatch)
    _arm(daemon, held)

    for _ in range(iterations):
        daemon._speak_loop_once()

    assert sum(counts.values()) <= SPAWN_BOUND, (
        "{0} iterations against a broken speaker spawned {1} processes "
        "({2}) — the failure word is re-enqueueing itself".format(
            iterations, sum(counts.values()), counts))
    assert len(queue) == 0, (
        "the queue never emptied, so the loop never reached its wait: {0!r}".format(
            [i.text for i in queue._items]))


@pytest.mark.parametrize("held", [False, True], ids=["normal", "held"])
def test_a_failure_shaped_turn_waits_before_retrying(monkeypatch, held):
    """A persistent fault must degrade to a slow retry, not a hot loop. One turn
    only: an item WAS claimed, so the idle wait cannot contaminate the reading."""
    daemon, queue, speaker, *_ = make_daemon(foreground="fg")
    _always_failing(daemon, speaker, monkeypatch)
    waits = []
    monkeypatch.setattr(daemon._state._wake, "wait", lambda t=None: waits.append(t))
    _arm(daemon, held)

    daemon._speak_loop_once()

    assert waits == [daemon._poll_interval], (
        "a failure-shaped turn returned without waiting (waits={0!r}) — a "
        "persistent fault spins".format(waits))


@pytest.mark.parametrize("held", [False, True], ids=["normal", "held"])
def test_a_cancelled_utterance_does_not_pay_the_back_off(monkeypatch, held):
    """speak() returning False is a barge-in, not a fault (the distinction
    test_daemon_speak_resilience already pins for the earcon). It must not eat
    the retry wait — interrupt latency is the whole point of a barge-in."""
    daemon, queue, speaker, *_ = make_daemon(foreground="fg")
    speaker.complete = False
    waits = []
    monkeypatch.setattr(daemon._state._wake, "wait", lambda t=None: waits.append(t))
    _arm(daemon, held)

    daemon._speak_loop_once()

    assert waits == [], "a barge-in paid the failure back-off: {0!r}".format(waits)


def test_the_failure_word_returns_once_speech_recovers(monkeypatch):
    """The suppression is "one outstanding word per session", not a permanent
    mute. An utterance that COMPLETES is live proof the audio path works (the
    same gate `_clear_speak_failure_memo` uses), so the next failure after one
    must get its word again."""
    daemon, queue, speaker, *_ = make_daemon(foreground="fg")
    counts = _always_failing(daemon, speaker, monkeypatch)
    broken = {"yes": True}
    original = speaker.speak

    def flaky(*a, **k):
        if broken["yes"]:
            return original(*a, **k)      # raises
        counts["say"] += 1
        return True

    monkeypatch.setattr(speaker, "speak", flaky)

    daemon._enqueue("fg", "prose", "one", False)
    daemon._speak_loop_once()             # fails -> the word is enqueued
    daemon._speak_loop_once()             # the word fails too -> suppressed
    assert len(queue) == 0

    broken["yes"] = False
    daemon._enqueue("fg", "prose", "two", False)
    daemon._speak_loop_once()             # completes -> proof of life

    broken["yes"] = True
    daemon._enqueue("fg", "prose", "three", False)
    daemon._speak_loop_once()             # fails again

    queued = [i.text for i in queue._items]
    assert any(SPEAK_FAILURE_WORD in t for t in queued), (
        "a recovered-then-broken session was left silent: {0!r}".format(queued))


def test_the_try_doctor_hint_is_not_burned_by_a_suppressed_failure(monkeypatch):
    """The hint fires once per class for the lifetime of the daemon (nothing in
    production calls `FaultCue.note_success`). A suppressed failure enqueues no
    word, so computing the hint there would spend it on a word nobody hears."""
    daemon, queue, speaker, *_ = make_daemon(foreground="fg")
    _always_failing(daemon, speaker, monkeypatch)

    daemon._enqueue("fg", "prose", "hello", False)
    daemon._speak_loop_once()             # first failure: word + hint
    words = [i.text for i in queue._items]
    daemon._speak_loop_once()             # the word fails -> suppressed

    assert any("doctor" in t for t in words), (
        "the first failure lost its hint: {0!r}".format(words))
    assert daemon._faultcue.should_fire("speak") is False, (
        "the hint budget is spent — that is expected after the FIRST failure")
