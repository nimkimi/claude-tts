"""T2 — STOP_ALL cue routing: "All stopped." must land in speaker()'s stream.

When speaker() has diverged from foreground() (keep-going or set_speaker call),
the held branch in _speak_loop_once reads speaker(). If on_stop_all enqueues
the confirmation to foreground() instead, the held branch never sees it → F3
(abrupt silence, no confirmation). Routing to speaker() fixes this; at parity
(speaker()==foreground()) behaviour is unchanged.
"""
from sonari.protocol import MsgType
from tests.daemon_helpers import make_daemon


def _msg(t, session, **kw):
    from sonari.protocol import PROTOCOL_VERSION
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


def test_stop_all_confirmation_voiced_under_divergence():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    daemon._enqueue("B", "prose", "b backlog", False)
    sessions.set_speaker("B")                      # voice=B, workspace=A
    daemon.handle_message(_msg(MsgType.STOP_ALL, ""))
    # The cue must land in the SPEAKER's stream (B) so the held branch can voice it.
    bq = daemon._stream("B").queue
    assert any(it.text == "All stopped." for it in bq._items)
    aq = daemon._stream("A").queue
    assert not any(it.text == "All stopped." for it in aq._items)
    # Proof it is actually heard: the held branch (reads speaker()==B, B is stopped)
    # pops the pause-exempt cue and voices it.
    daemon._speak_loop_once()
    assert any(s and "All stopped." in s for s in speaker.spoken)
