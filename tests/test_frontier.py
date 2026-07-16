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
