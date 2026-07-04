from sonari.protocol import MsgType
from sonari.daemon.features import lifecycle
from tests.daemon_helpers import make_daemon


def _msg(t, session, **kw):
    from sonari.protocol import PROTOCOL_VERSION
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


# --- a UPS SET_FOREGROUND re-populates identity after a simulated restart-wipe ---
def test_ups_recaptures_identity_after_wipe(monkeypatch):
    monkeypatch.setattr(lifecycle, "_setup_health", lambda v: ("ok", None))
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    daemon.handle_message(_msg(MsgType.SESSION_START, "s1", cwd="/x/proj",
                               term_program="Apple_Terminal", tty="/dev/ttys009"))
    assert sessions.identity("s1").tty == "/dev/ttys009"
    sessions._identities.pop("s1", None)                 # simulate the daemon-restart gap
    assert sessions.identity("s1") is None
    daemon.handle_message(_msg(MsgType.SET_FOREGROUND, "s1", cwd="/x/proj",
                               term_program="Apple_Terminal", tty="/dev/ttys009"))
    assert sessions.identity("s1") is not None
    assert sessions.identity("s1").tty == "/dev/ttys009"


# --- a partial UPS (tty moved, program empty) updates tty, keeps the good program ---
def test_ups_partial_identity_updates_only_nonempty_fields(monkeypatch):
    monkeypatch.setattr(lifecycle, "_setup_health", lambda v: ("ok", None))
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    daemon.handle_message(_msg(MsgType.SESSION_START, "s1", cwd="/x",
                               term_program="Apple_Terminal", tty="/dev/ttys009"))
    daemon.handle_message(_msg(MsgType.SET_FOREGROUND, "s1", cwd="/x",
                               term_program="", tty="/dev/ttys010"))
    assert sessions.identity("s1").tty == "/dev/ttys010"        # updated
    assert sessions.identity("s1").term_program == "Apple_Terminal"   # empty kept the good value


# --- an all-empty UPS does NOT touch identity (the "field present" guard skips it) ---
def test_ups_all_empty_identity_does_not_touch(monkeypatch):
    monkeypatch.setattr(lifecycle, "_setup_health", lambda v: ("ok", None))
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    daemon.handle_message(_msg(MsgType.SESSION_START, "s1", cwd="/x",
                               term_program="Apple_Terminal", tty="/dev/ttys009"))
    daemon.handle_message(_msg(MsgType.SET_FOREGROUND, "s1", cwd="/x",
                               term_program="", tty="", iterm_session_id=""))
    assert sessions.identity("s1").tty == "/dev/ttys009"        # preserved
