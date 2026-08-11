"""ANNOUNCE's sanction grain on a DEAD target that still holds a backlog.

The five tests shipped with ANNOUNCE all run against a live target, so none of
them could see the grain. The original code copied where-am-I's whole=True
sanction, but ⌃⌘W is a read OF its session while an announcement merely LANDS
on whatever workspace()/speaker() resolves to — _sanction_dead_read's own
docstring puts that in the whole=False category, alongside the settings
readbacks. With whole=True the verdict would be back-appended behind a stale
pile and drag the ENTIRE pile out with it: unrelated speech triggered by a
health check.
"""
from sonari import ttyutil
from sonari.protocol import MsgType, PROTOCOL_VERSION
from sonari.sessions import Identity
from tests.daemon_helpers import make_daemon

DEAD_TTY = "/dev/ttys999"


def _announce(daemon, text="Sonari is unhealthy."):
    return daemon.handle_message(
        {"v": PROTOCOL_VERSION, "type": MsgType.ANNOUNCE, "text": text})


def _make_dead_target_with_backlog(monkeypatch):
    """A workspace whose session is dead and already has unheard items."""
    daemon, _q, _sp, sessions, _c = make_daemon(foreground="fg")
    sessions.set_identity("fg", Identity(term_program="Apple_Terminal",
                                         tty=DEAD_TTY))
    monkeypatch.setattr(ttyutil, "tty_alive",
                        lambda tty: True if not tty else tty != DEAD_TTY)
    st = daemon._stream("fg")
    daemon._enqueue("fg", "prose", "stale backlog one", False)
    daemon._enqueue("fg", "prose", "stale backlog two", False)
    return daemon, sessions, st


def test_the_target_is_actually_dead(monkeypatch):
    """Guard the fixture itself: if this session is not dead, every other test
    in this file silently degrades into a live-target test and proves nothing."""
    _daemon, sessions, _st = _make_dead_target_with_backlog(monkeypatch)
    assert sessions.liveness("fg") == "dead"


def test_the_verdict_jumps_the_stale_backlog(monkeypatch):
    daemon, _sessions, st = _make_dead_target_with_backlog(monkeypatch)
    assert _announce(daemon) == {"ok": True}
    assert st.queue._items[0].text == "Sonari is unhealthy.", (
        "the verdict was queued behind a stale backlog instead of at the front")


def test_the_sanction_is_single_item_not_whole_stream(monkeypatch):
    """whole=False must be spent on one item, so the health check does not
    drag the dead session's unrelated pile out with it."""
    daemon, _sessions, _st = _make_dead_target_with_backlog(monkeypatch)
    _announce(daemon)
    assert daemon._deliberate_dead_read == "fg"
    assert daemon._deliberate_dead_whole is False, (
        "a whole-stream sanction would drain the stale backlog too")


def test_a_live_target_is_unaffected():
    """On a live workspace the sanction returns False and at_front stays off —
    today's path byte-for-byte, per _readback's own contract."""
    daemon, _q, _sp, _sessions, _c = make_daemon(foreground="fg")
    daemon._enqueue("fg", "prose", "earlier item", False)
    assert _announce(daemon) == {"ok": True}
    assert daemon._stream("fg").queue._items[0].text == "earlier item"
