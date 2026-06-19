from sonari.protocol import MsgType, PROTOCOL_VERSION
from tests.daemon_helpers import make_daemon


def _msg(mtype, session, **extra):
    d = {"v": PROTOCOL_VERSION, "type": mtype, "session": session}
    d.update(extra)
    return d


def test_flush_resets_playback_but_keeps_mute():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    # mute A (sticky) and give it open/streaming + buffered state
    daemon.handle_message(_msg(MsgType.MUTE, "A"))
    # Raise minqueue so prose buffer accumulates instead of immediately draining
    config["minqueue"] = 5
    daemon.handle_message(_msg(MsgType.PROSE, "A", delta="hello there. ", index=0, final=False))
    st = daemon._stream("A")
    assert st.muted is True
    assert st.open_msg is True
    # Verify buffer is non-empty before FLUSH (so post-FLUSH == [] actually tests reset)
    assert st.prose_buffer != []

    daemon.handle_message(_msg(MsgType.FLUSH, "A"))

    st = daemon._stream("A")
    assert st.open_msg is False          # playback reset
    assert st.prose_buffer == []
    assert st.muted is True              # sticky preserved across a new prompt


def test_session_end_drops_the_whole_stream():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    daemon.handle_message(_msg(MsgType.PROSE, "A", delta="some text. ", index=0, final=False))
    assert "A" in daemon._streams

    daemon.handle_message(_msg(MsgType.SESSION_END, "A"))

    # whole stream gone — including assembler + nav_cursor, which the old
    # SESSION_END leaked (spec §4.3 cleanup-divergence fix).
    assert "A" not in daemon._streams


def test_no_legacy_per_session_containers_remain():
    daemon, *_ = make_daemon()
    for attr in ("_assemblers", "_prose_buffer", "_options", "_captured_msg",
                 "_open_msg", "_nav_cursor", "_muted_sessions",
                 "_warned_immediate", "_guided_sessions"):
        assert not hasattr(daemon, attr), f"legacy container {attr} still present"
