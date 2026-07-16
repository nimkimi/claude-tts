"""W10 (spec §11): under global quiet, prose never queues (prose.py:20) but IS
recorded — the Also-map's waiting count reads 0 for an hour-old pile. Surface
the recorded-but-not-queued floor as ', {u} unheard'. The -k subtraction kills
the double-count (queued items' history entries are also unheard until spoken)."""
from sonari.protocol import PROTOCOL_VERSION
from tests.daemon_helpers import make_daemon


def _msg(t, session, **kw):
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


def _where(daemon, speaker, session="fg"):
    daemon.handle_message(_msg("where_am_i", session))
    daemon._speak_loop_once()
    return speaker.spoken[-1]


def test_quiet_pile_surfaces_as_unheard():
    daemon, queue, speaker, sessions, config = make_daemon(verbosity="quiet")
    sessions.register("b", cwd="/x/b")
    for i in range(3):                             # recorded, never queued (the quiet gate)
        daemon.history.record("b", "prose", "line {0}.".format(i))
    assert "2 b, 3 unheard" in _where(daemon, speaker)


def test_queued_items_are_not_double_counted():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.register("b", cwd="/x/b")
    entry = daemon.history.record("b", "prose", "queued line.")
    daemon._enqueue("b", "prose", "queued line.", False, entry=entry)
    out = _where(daemon, speaker)
    assert "2 b, 1 waiting" in out
    assert "unheard" not in out                    # u = max(0, 1 - 1) = 0


def test_mixed_pile_orders_waiting_then_unheard():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.register("b", cwd="/x/b")
    entry = daemon.history.record("b", "prose", "queued line.")
    daemon._enqueue("b", "prose", "queued line.", False, entry=entry)
    daemon.history.record("b", "prose", "cut one.")
    daemon.history.record("b", "prose", "cut two.")
    assert "2 b, 1 waiting, 2 unheard" in _where(daemon, speaker)


def test_fully_heard_sessions_show_no_unheard_clause():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.register("b", cwd="/x/b")
    entry = daemon.history.record("b", "prose", "b line.")
    daemon._enqueue("b", "prose", "b line.", False, entry=entry)
    sessions.set_speaker("b")
    daemon._speak_loop_once()                      # spoken to completion -> heard
    sessions.set_speaker("fg")
    assert "unheard" not in _where(daemon, speaker)


def test_genuine_preemption_pile_appears_even_non_quiet():
    # A ⌃⌘J/⌃⌘D preemption-cut leaves the cut item recorded, unheard, un-queued
    # (host.py:535 re-queues only when the OWN stream is stopped). Surfacing it
    # is CORRECT (spec §11 correction): a genuine pile, not a leak.
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.register("b", cwd="/x/b")
    daemon.history.record("b", "prose", "cut mid-sentence.")
    assert "2 b, 1 unheard" in _where(daemon, speaker)
