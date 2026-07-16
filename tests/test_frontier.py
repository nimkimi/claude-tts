# tests/test_frontier.py
from sonari.queue import SpeechItem
from sonari.sessions import SessionManager
from sonari.daemon import SpeechDaemon
from sonari.protocol import MsgType, PROTOCOL_VERSION
from sonari.config import DEFAULTS


def _cfg():
    c = {k: (v.copy() if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
    c["verbosity"] = "everything"
    return c


class _FakeSpeaker:
    def __init__(self): self._epoch = 0
    def cancel(self): self._epoch += 1
    def cancel_epoch(self): return self._epoch
    def earcon(self, kind): pass


def test_speech_item_forward_defaults_false():
    it = SpeechItem(id=1, session="s", kind="prose", text="x", is_decision=False)
    assert it.forward is False


def test_enqueue_threads_forward_flag():
    sessions = SessionManager(); sessions.set_foreground("s0")
    d = SpeechDaemon(_FakeSpeaker(), sessions, _cfg())
    d._enqueue("s0", "prose", "a", False, forward=True)
    d._enqueue("s0", "prose", "b", False)
    items = list(d._stream("s0").queue._items)
    assert items[0].forward is True and items[1].forward is False


def test_whereami_requeue_preserves_forward():
    sessions = SessionManager(); sessions.set_foreground("s0")
    sessions.register("s0", cwd="/x/s0")
    d = SpeechDaemon(_FakeSpeaker(), sessions, _cfg())
    it = SpeechItem(id=99, session="s0", kind="prose", text="live",
                    is_decision=False, forward=True)
    d._current_item = it                       # in-flight forward readout
    with d._state.transaction():
        d.handle_message({"v": PROTOCOL_VERSION, "type": MsgType.WHERE_AM_I,
                          "session": "s0"})
    requeued = [x for x in d._stream("s0").queue._items if x.text == "live"]
    assert requeued and requeued[0].forward is True


def test_repeat_last_requeue_preserves_forward():
    # playback.py:191 (⌃⌘R) — the barge-in re-queue of the interrupted item
    # must thread forward=cur.forward, same as the ⌃⌘W site above.
    sessions = SessionManager(); sessions.set_foreground("s0")
    sessions.register("s0", cwd="/x/s0")
    d = SpeechDaemon(_FakeSpeaker(), sessions, _cfg())
    d._last_utterance = ("earlier content.", None)   # required or REPEAT_LAST no-ops
    it = SpeechItem(id=98, session="s0", kind="prose", text="mid-flight",
                    is_decision=False, forward=True)
    d._current_item = it                       # in-flight forward readout, barged in
    with d._state.transaction():
        d.handle_message({"v": PROTOCOL_VERSION, "type": MsgType.REPEAT_LAST,
                          "session": "s0"})
    requeued = [x for x in d._stream("s0").queue._items if x.text == "mid-flight"]
    assert requeued and requeued[0].forward is True


def test_chooser_cancel_restore_preserves_forward():
    # chooser.py:118 (chooser cancel/restore) — the captured in-flight item is
    # re-queued when the browse gesture is cancelled; must thread forward=c.forward.
    sessions = SessionManager(); sessions.set_foreground("s0")
    sessions.register("s0", cwd="/x/s0")
    d = SpeechDaemon(_FakeSpeaker(), sessions, _cfg())
    it = SpeechItem(id=97, session="s0", kind="prose", text="captured",
                    is_decision=False, forward=True)
    d._current_item = it                       # in-flight forward readout, captured at open
    with d._state.transaction():
        d.handle_message({"v": PROTOCOL_VERSION, "type": MsgType.CHOOSER_STEP,
                          "session": "s0", "direction": "next"})
    with d._state.transaction():
        d.handle_message({"v": PROTOCOL_VERSION, "type": MsgType.CHOOSER_CANCEL,
                          "session": "s0"})
    requeued = [x for x in d._stream("s0").queue._items if x.text == "captured"]
    assert requeued and requeued[0].forward is True


def test_advance_frontier_monotonic():
    from sonari.session_stream import SessionStream
    st = SessionStream()
    assert st.frontier is None
    st.advance_frontier((1, 0)); assert st.frontier == (1, 0)
    st.advance_frontier((2, 3)); assert st.frontier == (2, 3)
    st.advance_frontier((1, 5)); assert st.frontier == (2, 3)   # never retreats
    st.advance_frontier(None);   assert st.frontier == (2, 3)   # None-safe no-op


def test_frontier_survives_new_prompt_reset():
    from sonari.session_stream import SessionStream
    st = SessionStream(); st.advance_frontier((4, 1))
    st.reset_for_new_prompt()
    assert st.frontier == (4, 1)                # monotonic across turns; only SESSION_END drops it


def test_note_spoken_advances_frontier_only_on_forward_completion():
    sessions = SessionManager(); sessions.set_foreground("s0")
    d = SpeechDaemon(_FakeSpeaker(), sessions, _cfg())
    st = d._stream("s0")
    e = d.history.record("s0", "prose", "hello")     # (msg_id 0, seq 0)
    it = SpeechItem(id=1, session="s0", kind="prose", text="hello",
                    is_decision=False, forward=True)
    d._state._pending_heard[it.id] = e; d._current_item = it
    d.note_spoken(it, completed=True)
    assert e.heard is True and st.frontier == (e.msg_id, e.seq)


def test_browse_replay_flips_heard_but_frontier_stays():
    sessions = SessionManager(); sessions.set_foreground("s0")
    d = SpeechDaemon(_FakeSpeaker(), sessions, _cfg())
    st = d._stream("s0")
    e = d.history.record("s0", "prose", "old")
    it = SpeechItem(id=1, session="s0", kind="prose", text="old",
                    is_decision=False, forward=False)   # browse replay: NOT forward
    d._state._pending_heard[it.id] = e; d._current_item = it
    d.note_spoken(it, completed=True)
    assert e.heard is True                    # heard still flips (nav's other uses)
    assert st.frontier is None                # but the frontier did NOT move (B1)


def test_mid_item_barge_in_leaves_frontier_unchanged():
    sessions = SessionManager(); sessions.set_foreground("s0")
    d = SpeechDaemon(_FakeSpeaker(), sessions, _cfg())
    st = d._stream("s0")
    e = d.history.record("s0", "prose", "cut")
    it = SpeechItem(id=1, session="s0", kind="prose", text="cut",
                    is_decision=False, forward=True)
    d._state._pending_heard[it.id] = e; d._current_item = it
    d.note_spoken(it, completed=False)        # R-8: mid-item cut, not full completion
    assert e.heard is False and st.frontier is None


def test_note_spoken_advances_frontier_on_decision_readout_completion():
    # A decision-kind readout (choice/plan/permission enqueue site, T2 step 5's
    # forward=True) must advance the frontier the same as a prose readout — this
    # was the untested write path the T2 arithmetic gap (+6 claimed / 5 shown)
    # exposed. Mirrors test_note_spoken_advances_frontier_only_on_forward_completion
    # but with a decision kind + is_decision=True, matching decisions.py's real
    # _enqueue(session, "choice", text, True, entry=entry, forward=True) shape.
    sessions = SessionManager(); sessions.set_foreground("s0")
    d = SpeechDaemon(_FakeSpeaker(), sessions, _cfg())
    st = d._stream("s0")
    e = d.history.record("s0", "choice", "pick one")
    it = SpeechItem(id=1, session="s0", kind="choice", text="pick one",
                    is_decision=True, forward=True)
    d._state._pending_heard[it.id] = e; d._current_item = it
    d.note_spoken(it, completed=True)
    assert e.heard is True and st.frontier == (e.msg_id, e.seq)
