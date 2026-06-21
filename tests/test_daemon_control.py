from sonari.protocol import MsgType, PROTOCOL_VERSION
from sonari.queue import SpeechItem
from sonari.daemon.features import lifecycle
from tests.daemon_helpers import make_daemon, stream_queue


def _msg(mtype, session=None, **extra):
    d = {"v": PROTOCOL_VERSION, "type": mtype}
    if session is not None:
        d["session"] = session
    d.update(extra)
    return d


def _seed(daemon, session, n, decision_at=None):
    # Per-stream: each item lands in its OWN session's stream (the speak loop plays
    # the foreground stream). For the foreground session that IS the unpacked queue.
    for i in range(n):
        is_dec = decision_at is not None and i == decision_at
        daemon._stream(session).queue.enqueue(SpeechItem(
            id=daemon._alloc_id(),
            session=session,
            kind="plan" if is_dec else "prose",
            text="item {0}".format(i),
            is_decision=is_dec,
        ))


def test_flush_drops_session_items_without_cancelling_other_speech():
    # Flush now cancels only when the current utterance belongs to the flushed
    # session. There is no current utterance in this unit test, so no cancel.
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    _seed(daemon, "fg", 2)
    _seed(daemon, "other", 1)
    daemon.handle_message(_msg(MsgType.FLUSH, "fg"))
    assert speaker.cancels == 0
    # fg's own stream is cleared; the 'other' session's stream is untouched
    assert len(queue) == 0
    assert len(stream_queue(daemon, "other")) == 1
    assert stream_queue(daemon, "other").pop_next().session == "other"


def test_stop_clears_foreground_and_cancels():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    _seed(daemon, "fg", 3)
    daemon.handle_message(_msg(MsgType.STOP, "fg"))
    assert len(queue) == 0
    assert speaker.cancels == 1


def test_stop_leaves_background_streams_untouched():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="a")
    _seed(daemon, "b", 2)    # background b has backlog
    _seed(daemon, "a", 2)    # foreground a
    daemon.handle_message(_msg(MsgType.STOP, "a"))
    assert len(queue) == 0                               # foreground cleared
    assert len(stream_queue(daemon, "b")) == 2           # background untouched
    assert speaker.cancels == 1


def test_skip_only_cancels_current():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    _seed(daemon, "fg", 3)
    daemon.handle_message(_msg(MsgType.SKIP, "fg"))
    assert speaker.cancels == 1
    # queue untouched by skip
    assert len(queue) == 3


def test_jump_decision_drops_to_first_decision_and_cancels():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    # items 0,1 prose; item 2 is a decision
    _seed(daemon, "fg", 4, decision_at=2)
    daemon.handle_message(_msg(MsgType.JUMP_DECISION, "fg"))
    assert speaker.cancels == 1
    nxt = queue.pop_next()
    assert nxt.is_decision is True
    assert nxt.text == "item 2"


def test_set_foreground_sets_foreground():
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    daemon.handle_message(_msg(MsgType.SET_FOREGROUND, "s9"))
    assert sessions.foreground() == "s9"


def test_session_start_sets_foreground_and_registers(monkeypatch):
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    monkeypatch.setattr(lifecycle, "_setup_health", lambda v: ("ok", None))  # keep focus on fg/register
    daemon.handle_message(_msg(MsgType.SESSION_START, "s9"))
    assert sessions.foreground() == "s9"
    assert sessions.is_foreground("s9") is True


def test_session_end_unregisters():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="s9")
    daemon.handle_message(_msg(MsgType.SESSION_END, "s9"))
    assert sessions.foreground() is None



# ---------------------------------------------------------------------------
# Task 5: Cut-on-switch (new prompt)
# ---------------------------------------------------------------------------

def test_new_prompt_cuts_a_different_sessions_current_utterance():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="a")
    daemon._current_item = SpeechItem(id=1, session="a", kind="prose",
                                      text="long answer.", is_decision=False)
    daemon.handle_message(_msg(MsgType.SET_FOREGROUND, "b", cwd="/x/b"))
    daemon.handle_message(_msg(MsgType.FLUSH, "b"))
    assert speaker.cancels == 1                          # a's sentence cut

def test_new_prompt_does_not_cut_when_pinned_elsewhere():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="a")
    daemon.handle_message(_msg(MsgType.PIN_TOGGLE, "a"))   # pin a
    daemon._current_item = SpeechItem(id=1, session="a", kind="prose",
                                      text="answer.", is_decision=False)
    daemon.handle_message(_msg(MsgType.SET_FOREGROUND, "b", cwd="/x/b"))
    daemon.handle_message(_msg(MsgType.FLUSH, "b"))
    assert speaker.cancels == 0                          # a stays — pinned

def test_new_prompt_same_session_still_cuts():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="a")
    daemon._current_item = SpeechItem(id=1, session="a", kind="prose",
                                      text="answer.", is_decision=False)
    daemon.handle_message(_msg(MsgType.FLUSH, "a"))
    assert speaker.cancels == 1                          # existing behavior preserved
