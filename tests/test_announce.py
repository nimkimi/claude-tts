"""ANNOUNCE carries CLI-originated speech (the doctor verdict, install/uninstall).

The daemon speaks only into session streams; a CLI sentence has no session, so
this handler picks a playable one. It MUST ack: without a reply, voiceout.speak
cannot tell a delivered sentence from a dropped one and would never fall back.
"""
from sonari.protocol import MsgType
from tests.daemon_helpers import make_daemon


def test_announce_is_a_distinct_message_type():
    assert MsgType.ANNOUNCE == "announce"
    # It must not collide with the session-scoped prose path.
    assert MsgType.ANNOUNCE != MsgType.PROSE


def test_announce_enqueues_the_sentence_and_acks():
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    sessions.set_foreground("s1")
    reply = daemon.handle_message(
        {"v": 1, "type": MsgType.ANNOUNCE, "text": "Sonari is healthy."})
    assert reply == {"ok": True}
    queued = [i.text for st in daemon._streams.values() for i in st.queue._items]
    assert "Sonari is healthy." in queued


def test_announce_refuses_when_there_is_nowhere_audible():
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    reply = daemon.handle_message(
        {"v": 1, "type": MsgType.ANNOUNCE, "text": "Sonari is healthy."})
    assert reply == {"ok": False}


def test_empty_text_is_refused():
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    sessions.set_foreground("s1")
    reply = daemon.handle_message({"v": 1, "type": MsgType.ANNOUNCE, "text": ""})
    assert reply == {"ok": False}


def test_announce_is_mute_and_pause_exempt():
    """A safety-net verdict must be audible even on a muted or held stream —
    it is the message that explains why everything else is quiet."""
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    sessions.set_foreground("s1")
    daemon.handle_message(
        {"v": 1, "type": MsgType.ANNOUNCE, "text": "Sonari is unhealthy."})
    item = [i for st in daemon._streams.values() for i in st.queue._items][-1]
    assert item.mute_exempt is True
    assert item.pause_exempt is True
