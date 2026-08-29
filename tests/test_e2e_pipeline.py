"""End-to-end pipeline test: real hooks_entry -> real SpeechDaemon -> recording FakeSpeaker.

Proves the ordering contract from the design spec section 4:
  - a decision earcon fires IMMEDIATELY when the decision appears,
  - but the decision's spoken text is FIFO and is voiced only AFTER the
    preceding prose,
  - foreground gating works,
  - a turn_done earcon ends the turn.
No real audio is ever produced: the Speaker is replaced by a recorder.
"""
import pytest
from sonari.hooks_entry import handle_event
from sonari.protocol import PROTOCOL_VERSION, MsgType
from sonari.sessions import SessionManager
from sonari.daemon import SpeechDaemon
from sonari.daemon.features import lifecycle, teaching
from sonari.config import DEFAULTS

# Module import (never the bare name -- tests/ and the repo root are both on
# sys.path without an __init__.py, so `daemon_helpers` and
# `tests.daemon_helpers` would be two distinct modules with two distinct
# _LIVE_FAKE_SPEAKERS lists; conftest's drain only ever sees the latter).
# Also avoids shadowing this file's own local make_daemon().
from tests import daemon_helpers


@pytest.fixture(autouse=True)
def _silence_setup_cue(monkeypatch):
    monkeypatch.setattr(lifecycle, "_setup_health", lambda v: ("ok", None))  # no setup cue in ordering tests


SID = "sess-e2e-1"


class FakeSpeaker:
    """Records spoken text and earcons into one shared ordered list.

    Mirrors the public surface SpeechDaemon uses: speak/cancel/earcon/
    set_voice/set_rate. speak() is synchronous here (no threads) so the
    drain helper produces a deterministic ordering. earcon()/transient()
    resolve against `earcons` the same single lookup the real Speaker does:
    a kind that resolves is logged as ("earcon", kind); a kind that does NOT
    resolve is silence -- recorded in silent_cues, never appended to the
    ordered log, since nothing would have played. The instance registers
    with daemon_helpers._LIVE_FAKE_SPEAKERS so conftest's autouse drain
    still fails the test if a silent cue fires here, same as every other
    daemon test. (Only transient() is reachable from the real daemon --
    src/sonari/daemon/host.py calls speaker.transient(kind), never
    .earcon() -- but the honesty flip covers both so a caller of either
    gets the same true-or-silent contract.)
    """

    def __init__(self, log, earcons=None):
        self.log = log
        self.voice = None
        self.rate = DEFAULTS["rate"]
        self.cancelled = 0
        self._epoch = 0
        self._earcons = dict(
            daemon_helpers._default_earcons() if earcons is None else earcons)
        self.silent_cues: list[str] = []
        daemon_helpers._LIVE_FAKE_SPEAKERS.append(self)

    def speak(self, text, cancel_epoch=None):
        self.log.append(("text", text))

    def cancel_epoch(self):
        return self._epoch

    def cancel(self):
        self.cancelled += 1
        self._epoch += 1

    def _resolve_transient(self, kind):
        # A cue that resolves to nothing is not an earcon -- it is silence,
        # and logging it as one is what let `repoint` ship dead.
        path = self._earcons.get(kind)
        if path is None:
            self.silent_cues.append(kind)
            return
        self.log.append(("earcon", kind))

    def earcon(self, kind):
        self._resolve_transient(kind)

    def transient(self, kind):
        self._resolve_transient(kind)

    def set_voice(self, v):
        self.voice = v

    def set_rate(self, r):
        self.rate = r


def drain_queue(daemon, speaker):
    """Run the _speak_loop logic to exhaustion: pop FIFO, speak each item.

    Stage 2: the voice plays the FOREGROUND session's own stream (not a shared
    queue), so drain that — exactly what SpeechDaemon._speak_loop_once does, minus
    the blocking wait, so the test is deterministic and never touches threads.
    Background streams accumulate untouched (the earcon-only test relies on this).
    """
    while True:
        fg = daemon.sessions.foreground()
        st = daemon._streams.get(fg)
        item = st.queue.pop_next() if st is not None else None
        if item is None:
            return
        speaker.speak(item.text)


def make_daemon():
    log = []
    sessions = SessionManager(background_policy="earcon_only")
    cfg = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
    cfg["verbosity"] = "everything"
    speaker = FakeSpeaker(log, earcons=cfg["earcons"])
    daemon = SpeechDaemon(speaker, sessions, cfg)
    return daemon, speaker, log


@pytest.mark.expects_silent_cue
def test_local_fake_speaker_records_a_silent_cue_not_an_earcon():
    """A kind absent from the resolved table must not enter the ordered log
    as ("earcon", kind) -- it silently resolved to nothing, same as a real
    Speaker would. Direct construction, bypassing make_daemon/the daemon, so
    this is a receipt for the fake itself, not for any one call site. Covers
    the reachable path (transient()); earcon() shares the same resolution
    method, so a second identical assertion would add coverage but no new
    information."""
    log = []
    speaker = FakeSpeaker(log, earcons={})
    speaker.transient("repoint")
    assert log == []
    assert speaker.silent_cues == ["repoint"]


def feed_event(daemon, event, payload):
    """Run a hook event through the real handle_event and feed every
    resulting protocol message into the real daemon, just like bin/sonari-hook
    -> client -> daemon would in production."""
    for msg in handle_event(event, payload):
        assert msg["v"] == PROTOCOL_VERSION
        daemon.handle_message(msg)


def test_scripted_session_full_ordering():
    daemon, speaker, log = make_daemon()

    # 1. SessionStart: registers + sets foreground (no audio yet).
    feed_event(daemon, "SessionStart", {"session_id": SID})

    # 2. MessageDisplay: two sentences of prose, streamed as one final delta.
    feed_event(daemon, "MessageDisplay", {
        "session_id": SID,
        "delta": "Let me check the files. I will start now.",
        "index": 0,
        "final": True,
    })

    # 3. PreToolUse AskUserQuestion with two options.
    #    -> choice earcon fires IMMEDIATELY (recorded now),
    #       choice TEXT is enqueued AFTER the queued prose (recorded on drain).
    feed_event(daemon, "PreToolUse", {
        "session_id": SID,
        "tool_name": "AskUserQuestion",
        "tool_input": {
            "questions": [{
                "question": "Which approach?",
                "options": [
                    {"label": "Refactor"},
                    {"label": "Rewrite"},
                ],
            }],
        },
    })

    # Drain everything queued so far: prose sentences first, THEN the choice
    # text. The choice EARCON is already in the log from step 3 (before any
    # of this prose was spoken) -> that is the ordering proof.
    drain_queue(daemon, speaker)

    # 4. UserPromptSubmit: user answered + sent a new prompt -> flush + fg.
    feed_event(daemon, "UserPromptSubmit", {"session_id": SID})

    # 5. MessageDisplay: more prose for the new turn.
    feed_event(daemon, "MessageDisplay", {
        "session_id": SID,
        "delta": "Applying the change now.",
        "index": 0,
        "final": True,
    })

    # 6. PreToolUse Bash -> permission via Notification permission_prompt.
    #    permission earcon fires immediately; permission text enqueued after.
    feed_event(daemon, "Notification", {
        "session_id": SID,
        "notification_type": "permission_prompt",
        "action": "Run: pytest -q",
    })

    drain_queue(daemon, speaker)

    # 7. Stop: turn_done earcon ends the turn.
    feed_event(daemon, "Stop", {"session_id": SID})
    drain_queue(daemon, speaker)

    assert log == [
        ("earcon", "choice"),
        # T18a: the new-session announce now fires through the real hook
        # sequence (previously swallowed by the is_new/two-message bug).
        # SID is the only session so far -> number 1.
        ("text", "1, Another session."),
        ("text", "Let me check the files."),
        ("text", "I will start now."),
        ("text", "Which approach? Option 1: Refactor. Option 2: Rewrite. Press the option's number to choose, or Escape to cancel. Selecting is immediate. — at the terminal."),
        ("earcon", "permission"),
        ("text", "Applying the change now."),
        ("text", "Run: pytest -q Press the option's number to choose, or Escape to cancel. — at the terminal."),
        ("earcon", "turn_done"),
    ]


def test_background_session_is_earcon_only():
    """A non-foreground session still fires decision earcons immediately, but its
    prose and decision TEXT are NOT voiced: they accumulate in the background
    session's own stream, and the voice only plays the FOREGROUND stream (Stage 2
    flip — the text is no longer dropped, just held until that session is current)."""
    daemon, speaker, log = make_daemon()

    feed_event(daemon, "SessionStart", {"session_id": "fg"})
    # Background session never becomes foreground.
    feed_event(daemon, "MessageDisplay", {
        "session_id": "bg",
        "delta": "Background chatter that must stay silent.",
        "index": 0,
        "final": True,
    })
    feed_event(daemon, "PreToolUse", {
        "session_id": "bg",
        "tool_name": "AskUserQuestion",
        "tool_input": {"questions": [{
            "question": "Pick one",
            "options": [{"label": "A"}, {"label": "B"}],
        }]},
    })
    drain_queue(daemon, speaker)

    # Earcon fired (alerts are cross-session), but no BACKGROUND text was
    # spoken — bg's own chatter/choice text never reaches the log. SP3: the
    # "waiting" earcon is retired (no mid-turn ding); the sessionless
    # "choice" decision earcon still fires immediately. T18a: the one text
    # item is fg's OWN registration announce (number 1), not bg's — the
    # bg-stays-silent invariant this test exists to prove is untouched.
    assert log == [("earcon", "choice"), ("text", "1, Another session.")]


def test_background_reinvocation_does_not_hijack_foreground_voice():
    """#65: while A is mid-reply, a *different* session B's background re-invocation
    (the agent/workflow completion or /loop tick fires UserPromptSubmit ->
    SET_FOREGROUND + FLUSH) must NOT seize the voice. A's reply finishes
    uninterrupted; B accumulates in its own stream as a jump-to-waiting target;
    foreground stays A until an explicit jump."""
    daemon, speaker, log = make_daemon()

    feed_event(daemon, "SessionStart", {"session_id": "A"})
    # A streams a long reply -> three sentences queue in A's stream, NOT yet drained
    # (this is A "mid-speech": it still has backlog to deliver).
    feed_event(daemon, "MessageDisplay", {
        "session_id": "A",
        "delta": "First part of the answer. Second part of the answer. Third part.",
        "index": 0,
        "final": True,
    })
    # Background session B re-invokes (its agent finished / a loop ticked): a
    # programmatic UserPromptSubmit, then its completion output, then turn end.
    feed_event(daemon, "UserPromptSubmit", {"session_id": "B"})
    feed_event(daemon, "MessageDisplay", {
        "session_id": "B",
        "delta": "Background result is ready.",
        "index": 0,
        "final": True,
    })
    feed_event(daemon, "Stop", {"session_id": "B"})

    # Voice ownership unchanged: still A. (Cut-prevention with a genuinely in-flight
    # utterance is proven at the unit level by
    # test_new_prompt_does_not_steal_an_actively_speaking_session — here nothing has
    # been drained yet, so _current_item is None and a cancel assertion would be vacuous.)
    assert daemon.sessions.foreground() == "A"

    # Draining the voice plays A's whole reply in order — not B's completion.
    # Task 11: B's turn_done landed-ding also fires the one-shot background_turn
    # hint, but it must reach the stream the voice is ACTUALLY playing (A's,
    # since A is the speaker) rather than sitting unheard in B's own stream —
    # so it lands right after A's reply, FIFO.
    drain_queue(daemon, speaker)
    spoken = [text for kind, text in log if kind == "text"]
    assert spoken == [
        # T18a: A's own registration announce, enqueued at SessionStart ahead
        # of the reply prose — B never SessionStart's here, so this is A's
        # only announce (number 1).
        "1, Another session.",
        "First part of the answer.",
        "Second part of the answer.",
        "Third part.",
        teaching.HINTS["background_turn"],
    ]
    # B's completion accumulated in B's OWN stream (a waiting target), not lost.
    b_texts = [i.text for i in daemon._stream("B").queue._items]
    assert b_texts == [
        "Background result is ready.",
    ]

    # And an EXPLICIT jump reaches B — the contract's sanctioned voice switch.
    daemon.handle_message({"v": PROTOCOL_VERSION, "type": MsgType.JUMP_WAITING,
                           "session": "A"})
    assert daemon.sessions.foreground() == "B"
