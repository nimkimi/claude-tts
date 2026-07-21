"""D2 §6.1 submit-ack (the mirror boundary): fired on the daemon-side
prompt-submit path (FLUSH — only UserPromptSubmit sends it), gated by
`submit_ack_enabled`, DEFAULT OFF (dark pending the owner's ear)."""
from sonari.protocol import PROTOCOL_VERSION, MsgType
from tests.daemon_helpers import make_daemon


def _msg(t, session, **kw):
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


def test_submit_ack_ships_dark_by_default():
    from sonari.config import DEFAULTS
    assert DEFAULTS["submit_ack_enabled"] is False


def test_flush_is_silent_when_disabled():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon.handle_message(_msg(MsgType.FLUSH, "fg"))
    assert speaker.earcons == []


def test_flush_fires_submit_ack_when_enabled():
    daemon, queue, speaker, sessions, config = make_daemon()
    config["submit_ack_enabled"] = True
    daemon.handle_message(_msg(MsgType.FLUSH, "fg"))
    assert speaker.earcons == ["submit_ack"]
    assert len(queue) == 0                       # a transient, not a queued item
