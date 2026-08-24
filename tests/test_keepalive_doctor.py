"""STATUS carries keepalive state; doctor renders it as a row that only fails
on 'degraded' (idle/hold/disabled are all healthy-by-policy)."""
from sonari.protocol import PROTOCOL_VERSION, MsgType
from tests.daemon_helpers import make_daemon
from tests.test_keepalive_manager import FakeProc, FakeTimer


def _msg(t, session="s1", **kw):
    m = {"v": PROTOCOL_VERSION, "type": t, "session": session}
    m.update(kw)
    return m


def test_status_reply_carries_keepalive_state():
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    daemon.keepalive._popen = lambda cmd: FakeProc(cmd)
    daemon.keepalive._timer_factory = FakeTimer
    reply = daemon.handle_message(_msg(MsgType.STATUS))
    assert reply["keepalive"] == "idle"
    daemon.handle_message(_msg(MsgType.SESSION_START))
    reply = daemon.handle_message(_msg(MsgType.STATUS))
    assert reply["keepalive"] == "running"


def test_doctor_row_ok_for_policy_states_fail_for_degraded():
    from sonari.cli.doctor import _keepalive_row
    assert _keepalive_row({"keepalive": "running"}) == ("keepalive", True, "running")
    assert _keepalive_row({"keepalive": "idle"}) == ("keepalive", True, "idle")
    assert _keepalive_row({"keepalive": "disabled"}) == ("keepalive", True, "disabled")
    name, ok, detail = _keepalive_row({"keepalive": "degraded"})
    assert (name, ok) == ("keepalive", False)
    assert "degraded" in detail
    name, ok, detail = _keepalive_row({})
    assert (name, ok) == ("keepalive", False)     # old daemon / no field = surface it
