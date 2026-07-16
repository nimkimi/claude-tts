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
