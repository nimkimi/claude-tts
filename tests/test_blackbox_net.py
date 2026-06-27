"""Black-box behavior net: drive the REAL SpeechDaemon (handle_message +
_speak_loop_once) through a recording FakeSpeaker and assert ONLY on the
ordered (kind, payload) log + STATUS/PING replies.

These are CHARACTERIZATION tests: each assertion is the actual recorded log
of the CURRENT daemon. They pass on first run and are the behavior-preserving
contract for every Stage-2 move. Unlike test_e2e_pipeline.drain_queue (which
pops raw text and skips attribution/mute), drain_once() runs the REAL
_speak_loop_once once (synchronous, because FakeSpeaker is instant), so the
net exercises folder attribution, mute-drop, and cut-on-switch cancels too.
"""
import pytest
from sonari.protocol import MsgType, PROTOCOL_VERSION
from sonari.sessions import SessionManager
from sonari.daemon import SpeechDaemon
from sonari.daemon.features import lifecycle
from sonari.config import DEFAULTS


@pytest.fixture(autouse=True)
def _silence_setup_cue(monkeypatch):
    monkeypatch.setattr(lifecycle, "_setup_health", lambda v: ("ok", None))  # no setup cue in ordering


class FakeSpeaker:
    """Records speak/earcon/cancel/set_* into ONE shared ordered log.

    cancel() is recorded as ("cancel", None) so cut-on-switch is observable in
    the log; speak() returns self.complete (True) so drain_once mirrors a clean
    completion (no pause-requeue)."""

    def __init__(self, log):
        self.log = log
        self.voice = None
        self.rate = DEFAULTS["rate"]
        self.complete = True
        self._epoch = 0

    def speak(self, text, cancel_epoch=None):
        self.log.append(("text", text))
        return self.complete

    def cancel_epoch(self):
        return self._epoch

    def cancel(self):
        self.log.append(("cancel", None))
        self._epoch += 1

    def earcon(self, kind):
        self.log.append(("earcon", kind))

    def pitch(self, direction: str) -> None:
        # Chirps are parallel audio outside the speech queue; not mixed into
        # the ordering log so characterization tests don't need updating.
        pass

    def set_voice(self, v):
        self.voice = v

    def set_rate(self, r):
        self.rate = r


def make_net(verbosity="everything", foreground="fg",
             background_policy="earcon_only"):
    """A real SpeechDaemon wired to the recording FakeSpeaker. Returns
    (daemon, speaker, log, sessions, config)."""
    log = []
    speaker = FakeSpeaker(log)
    sessions = SessionManager(background_policy=background_policy)
    if foreground is not None:
        sessions.set_foreground(foreground)
    config = {k: (dict(v) if isinstance(v, dict) else v)
              for k, v in DEFAULTS.items()}
    config["verbosity"] = verbosity
    daemon = SpeechDaemon(speaker, sessions, config)
    return daemon, speaker, log, sessions, config


def msg(t, session=None, **kw):
    d = {"v": PROTOCOL_VERSION, "type": t}
    if session is not None:
        d["session"] = session
    d.update(kw)
    return d


def prose(daemon, session, delta, index=0, final=False):
    daemon.handle_message(msg(MsgType.PROSE, session,
                              delta=delta, index=index, final=final))


def drain_once(daemon):
    """Run exactly ONE speak-loop iteration synchronously and report whether
    the foreground stream had an item to act on. The non-blocking seam that
    replaces drain_queue's reach into _streams: it runs the REAL
    _speak_loop_once (faithful attribution + mute-drop), but guards the call so
    it never blocks on an empty foreground stream. NOTE: when the foreground
    stream is paused with a non-pause-exempt item, _speak_loop_once calls
    _wake.wait(_poll_interval), which blocks for the full interval (default 0.1s)
    up to 1000 drain iterations. For paused-stream tests, set daemon._poll_interval=0."""
    fg = daemon.sessions.foreground()
    st = daemon._streams.get(fg)
    if st is None or len(st.queue) == 0:
        return False
    daemon._speak_loop_once()
    return True


def drain(daemon, limit=1000):
    """drain_once to exhaustion of the foreground stream."""
    for _ in range(limit):
        if not drain_once(daemon):
            return


# ---------------------------------------------------------------------------
# Family: prose ordering (the e2e seed, characterized through drain_once)
# ---------------------------------------------------------------------------

def test_prose_ordering_decision_earcon_fires_before_fifo_text():
    daemon, speaker, log, sessions, config = make_net()
    from sonari.hooks_entry import handle_event

    def feed(event, payload):
        for m in handle_event(event, payload):
            assert m["v"] == PROTOCOL_VERSION
            daemon.handle_message(m)

    sid = "sess-net-1"
    feed("SessionStart", {"session_id": sid})
    feed("MessageDisplay", {"session_id": sid,
                            "delta": "Let me check the files. I will start now.",
                            "index": 0, "final": True})
    feed("PreToolUse", {"session_id": sid, "tool_name": "AskUserQuestion",
                        "tool_input": {"questions": [{"question": "Which approach?",
                            "options": [{"label": "Refactor"}, {"label": "Rewrite"}]}]}})
    drain(daemon)
    feed("UserPromptSubmit", {"session_id": sid})
    feed("MessageDisplay", {"session_id": sid,
                            "delta": "Applying the change now.",
                            "index": 0, "final": True})
    feed("Notification", {"session_id": sid,
                          "notification_type": "permission_prompt",
                          "action": "Run: pytest -q"})
    drain(daemon)
    feed("Stop", {"session_id": sid})
    drain(daemon)

    assert log == [
        ("earcon", "choice"),
        ("text", "Let me check the files."),
        ("text", "I will start now."),
        ("text", "Which approach? Option 1: Refactor. Option 2: Rewrite. "
                 "Press the option's number to choose, or Escape to cancel. "
                 "Selecting is immediate."),
        ("earcon", "permission"),
        ("text", "Applying the change now."),
        ("text", "Run: pytest -q Press the option's number to choose, "
                 "or Escape to cancel."),
        ("earcon", "turn_done"),
    ]


# ---------------------------------------------------------------------------
# Family: background is earcon-only (waiting + decision earcon, no text)
# ---------------------------------------------------------------------------

def test_background_session_is_earcon_only():
    daemon, speaker, log, sessions, config = make_net(foreground="fg")
    prose(daemon, "bg", "Background chatter that must stay silent. ",
          index=0, final=True)
    daemon.handle_message(msg(MsgType.CHOICE, "bg", questions=[
        {"question": "Pick one", "options": [{"label": "A"}, {"label": "B"}]}]))
    drain(daemon)  # foreground "fg" has nothing -> bg text never spoken
    # Surprise vs brief hint: CHOICE for a bg session fires no earcon —
    # only the prose's waiting earcon is emitted; choice earcon is not produced.
    assert log == [("earcon", "waiting")]


# ---------------------------------------------------------------------------
# Family: minqueue batching (held below threshold, flushed all at once)
# ---------------------------------------------------------------------------

def test_minqueue_batches_below_threshold_then_flushes_together():
    daemon, speaker, log, sessions, config = make_net(foreground="fg")
    config["minqueue"] = 3
    prose(daemon, "fg", "One. Two. ", index=0, final=False)
    drain(daemon)
    assert log == []  # two sentences < threshold 3: nothing flushed, nothing spoken
    prose(daemon, "fg", "Three. ", index=1, final=False)
    drain(daemon)
    assert log == [("text", "One."), ("text", "Two."), ("text", "Three.")]


# ---------------------------------------------------------------------------
# Family: 2-level nav seek-and-play (within-turn prev cancels then replays)
# ---------------------------------------------------------------------------

def test_nav_within_turn_prev_seeks_and_plays():
    daemon, speaker, log, sessions, config = make_net(foreground="fg")
    h = daemon.history
    h.record("fg", "prose", "m0a"); h.record("fg", "prose", "m0b")
    h.end_message("fg")
    h.record("fg", "prose", "m1"); h.end_message("fg")
    h.record("fg", "prose", "m2")
    daemon.handle_message(msg(MsgType.NAV, "fg", to="prev"))
    drain(daemon)
    # prev cancels the current utterance then replays the target message AND
    # every later one (seek-and-play): m1 then m2.
    assert log == [("cancel", None), ("text", "m1"), ("text", "m2")]


# ---------------------------------------------------------------------------
# Family: EARCON turn_done sub-threshold flush
# ---------------------------------------------------------------------------

def test_turn_done_earcon_flushes_sub_threshold_prose():
    daemon, speaker, log, sessions, config = make_net(foreground="fg")
    config["minqueue"] = 5
    prose(daemon, "fg", "Only one. ", final=True)
    drain(daemon)
    assert log == []  # final alone does NOT flush; prose is held below threshold
    daemon.handle_message(msg(MsgType.EARCON, "fg", kind="turn_done"))
    drain(daemon)
    assert log == [("earcon", "turn_done"), ("text", "Only one.")]


# ---------------------------------------------------------------------------
# Family: decision FIFO + cue (earcon-first, prose-FIFO-after)
# ---------------------------------------------------------------------------

def test_decision_fifo_earcon_fires_first_then_prose_then_choice():
    daemon, speaker, log, sessions, config = make_net(foreground="fg")
    prose(daemon, "fg", "Some prose. ", final=False)
    daemon.handle_message(msg(MsgType.EARCON, "fg", kind="choice"))
    daemon.handle_message(msg(MsgType.CHOICE, "fg", questions=[
        {"question": "Q", "options": [{"label": "A"}, {"label": "B"}]}]))
    drain(daemon)
    assert log == [
        ("earcon", "choice"),
        ("text", "Some prose."),
        ("text", "Q Option 1: A. Option 2: B. Press the option's number to choose, "
                 "or Escape to cancel. Selecting is immediate."),
    ]


# ---------------------------------------------------------------------------
# Family: foreground gating (only fg session text is spoken)
# ---------------------------------------------------------------------------

def test_foreground_gating_only_fg_session_spoken():
    daemon, speaker, log, sessions, config = make_net(foreground="a")
    prose(daemon, "a", "alpha. ", final=False)
    prose(daemon, "b", "beta. ", final=False)
    drain(daemon)
    # Surprise vs brief hint: a waiting earcon fires for session "b" arriving
    # while "a" is foreground; only "alpha." is spoken (beta stays in bg stream).
    assert log == [("earcon", "waiting"), ("text", "alpha.")]


# ---------------------------------------------------------------------------
# Family: pause/resume re-queue
# ---------------------------------------------------------------------------

def test_stop_resume_requeues_interrupted_utterance():
    daemon, speaker, log, sessions, config = make_net(foreground="fg")
    # poll_interval=0 so drain() doesn't block 0.1 s/iter when stopped with no
    # pause-exempt item left; wait(0) on threading.Event returns immediately.
    daemon._poll_interval = 0
    prose(daemon, "fg", "interrupted. ", final=False)
    daemon.handle_message(msg(MsgType.STOP_SESSION, "fg"))
    drain(daemon)
    daemon.handle_message(msg(MsgType.STOP_SESSION, "fg"))  # resume
    drain(daemon)
    assert log == [
        ("text", "Stopped."),
        ("text", "Resumed."),
        ("text", "interrupted."),
    ]


# ---------------------------------------------------------------------------
# Family: stop-all (⌃⌘M holds every session; only the cue speaks, one-way)
# ---------------------------------------------------------------------------

def test_stop_all_holds_speech_only_the_cue_is_spoken():
    daemon, speaker, log, sessions, config = make_net(foreground="fg")
    # poll_interval=0 so drain() doesn't block when held with no pause-exempt item.
    daemon._poll_interval = 0
    daemon.handle_message(msg(MsgType.STOP_ALL, "fg"))
    drain(daemon)
    prose(daemon, "fg", "secret. ", final=False)
    drain(daemon)
    # "secret." must never appear; one-way, so only the "All stopped." cue is spoken.
    assert log == [("text", "All stopped.")]


# ---------------------------------------------------------------------------
# Family: jump-waiting target order (blocked outranks prose-only)
# ---------------------------------------------------------------------------

def test_jump_waiting_blocked_session_outranks_prose_only():
    daemon, speaker, log, sessions, config = make_net(foreground="a")
    sessions.register("b", cwd="/x/proseonly")
    sessions.register("c", cwd="/x/blocked")
    prose(daemon, "b", "just text. ", final=False)
    daemon.handle_message(msg(MsgType.CHOICE, "c", questions=[
        {"question": "Pick?", "options": [{"label": "One"}, {"label": "Two"}]}]))
    daemon.handle_message(msg(MsgType.JUMP_WAITING, "a"))
    drain(daemon)
    assert sessions.foreground() == "c"
    # Surprise vs brief hint: a waiting earcon fires before the cancel+jump cue
    # because session "b"'s prose arrival triggered it; then cut-on-switch cancel,
    # then the jump announcement and the choice text for session "c".
    assert log == [
        ("earcon", "waiting"),
        ("cancel", None),
        ("text", "Jumping to blocked. Bring it forward to type."),
        ("text", "Pick? Option 1: One. Option 2: Two. Press the option's number to "
                 "choose, or Escape to cancel. Selecting is immediate."),
    ]


# ---------------------------------------------------------------------------
# Family: FLUSH cut-on-switch (same-session FLUSH cancels current item)
# ---------------------------------------------------------------------------

def test_flush_cuts_current_utterance_on_switch():
    daemon, speaker, log, sessions, config = make_net(foreground="fg")
    daemon._enqueue("fg", "prose", "sentence A", False)
    it = daemon._stream("fg").queue.pop_next()
    daemon._current_item = it
    log.clear()
    daemon.handle_message(msg(MsgType.FLUSH, "fg"))
    assert log == [("cancel", None)]
    assert len(daemon._stream("fg").queue) == 0


# ---------------------------------------------------------------------------
# Family: config STATUS snapshot
# ---------------------------------------------------------------------------

def test_status_snapshot_and_ping():
    daemon, speaker, log, sessions, config = make_net(foreground="fg")
    daemon._enqueue("fg", "prose", "x", False)
    reply = daemon.handle_message(msg(MsgType.STATUS, "fg"))
    assert reply == {
        "verbosity": "everything",
        "rate": 200,
        "voice": None,
        "foreground": "fg",
        "queue_len": 1,
        "minqueue": 1,
    }
    assert daemon.handle_message(msg(MsgType.PING, "fg")) == {"ok": True}
