"""STATUS carries keepalive state; doctor renders it as a row that only fails
on 'degraded' (idle/hold/disabled are all healthy-by-policy)."""
from unittest import mock

from sonari import cli
from sonari.protocol import PROTOCOL_VERSION, MsgType
from tests._fakeplatform import fake_platform, FakeSupervisor, FakeHotkey
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
    assert _keepalive_row({"keepalive": "hold"}) == ("keepalive", True, "hold")
    assert _keepalive_row({"keepalive": "disabled"}) == ("keepalive", True, "disabled")
    name, ok, detail = _keepalive_row({"keepalive": "degraded"})
    assert (name, ok) == ("keepalive", False)
    assert "degraded" in detail
    name, ok, detail = _keepalive_row({})
    assert (name, ok) == ("keepalive", False)     # old daemon / no field = surface it


def test_unreachable_daemon_row_reads_as_no_state_not_a_python_error():
    """The exact scenario doctor exists for: the daemon is DOWN. STATUS raises,
    so the keepalive row must still find a dict to read — an unbound `st` here
    rendered "error: cannot access local variable 'st'", and doctor's verdict
    SPEAKS that sentence to an eyes-free user."""
    with mock.patch("sonari.client.send", side_effect=OSError("down")):
        pb = fake_platform(supervisor=FakeSupervisor(), hotkey=FakeHotkey())
        with mock.patch.object(cli, "_platform", lambda: pb):
            rows = cli.doctor.doctor()
    assert [r for r in rows if r[0] == "keepalive"] == [
        ("keepalive", False, "daemon reported no keepalive state")]
