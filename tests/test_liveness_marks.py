"""D3 spec §4a (Also-map half): the ⌃⌘W map consults liveness() per session and
MARKS the two non-live tiers instead of the old is_provisional exclusion (R4's
silent quarantine). A pending session WITH content clauses is NAMED with a
leading ", pending"; a dead one with ", closed". A clause-less pending session
collapses into the aggregate "{Count} pending." tail — never a named phantom —
and a clause-less dead session is dropped entirely (nothing to act on). Live
entries keep grammar v2 byte-for-byte; these strings are byte-exact for the
same reason the grammar-v2 suites are."""
from sonari import ttyutil
from sonari.daemon.features import control
from sonari.sessions import Identity
from tests.daemon_helpers import make_daemon


def _liveness(monkeypatch, dead=()):
    """Fake tty_alive: empty tty -> live (fail-open); else live iff not in `dead`."""
    monkeypatch.setattr(ttyutil, "tty_alive",
                        lambda tty: True if not tty else tty not in dead)


def _ident(sessions, sid, tty):
    sessions.set_identity(sid, Identity(term_program="Apple_Terminal", tty=tty))


def test_pending_with_content_named_with_leading_pending_clause(monkeypatch):
    _liveness(monkeypatch)
    daemon, *_ = make_daemon(foreground=None)
    daemon.sessions.load_state({"s1": {"folder": "repo", "number": 1}})
    daemon._stream("s1")                                  # its stream (frontier None)
    daemon.history.record("s1", "prose", "restored line.")
    assert control._also_clause(daemon) == " Also: 1 repo, pending, 1 unheard."


def test_dead_with_content_named_with_leading_closed_clause(monkeypatch):
    _liveness(monkeypatch, dead={"/dev/ttys404"})
    daemon, *_ = make_daemon(foreground=None)
    daemon.sessions.register("s1", cwd="/x/etl")
    _ident(daemon.sessions, "s1", "/dev/ttys404")         # captured node is gone
    daemon.history.record("s1", "prose", "orphan line.")
    assert control._also_clause(daemon) == " Also: 1 etl, closed, 1 unheard."


def test_clause_less_pending_aggregate_never_a_named_phantom(monkeypatch):
    _liveness(monkeypatch)
    daemon, *_ = make_daemon(foreground=None)
    daemon.sessions.load_state({"s1": {"folder": "repo", "number": 1},
                                "s2": {"folder": "logs", "number": 2}})
    out = control._also_clause(daemon)
    assert out == " Two pending."
    for never_spoken in ("repo", "logs", "1", "2"):       # no number, no folder
        assert never_spoken not in out


def test_clause_less_dead_dropped_entirely(monkeypatch):
    _liveness(monkeypatch, dead={"/dev/ttys404"})
    daemon, *_ = make_daemon(foreground=None)
    daemon.sessions.register("s1", cwd="/x/etl")
    _ident(daemon.sessions, "s1", "/dev/ttys404")
    assert control._also_clause(daemon) == ""             # not named, not quiet-counted


def test_pending_decision_still_sorts_in_the_decision_tier(monkeypatch):
    _liveness(monkeypatch)
    daemon, *_ = make_daemon(foreground=None)
    daemon.sessions.register("live1", cwd="/x/board")      # number 1: a live pile
    daemon.history.record("live1", "prose", "board line.")
    daemon.sessions.load_state({"s2": {"folder": "logs", "number": 2}})
    daemon._pending_decisions["s2"] = {"text": "Bash: rm x"}
    # Tier keys on the CONTENT clauses, computed before the marker is prefixed.
    assert control._also_clause(daemon) == (
        " Also: 2 logs, pending, decision. 1 board, 1 unheard.")


def test_quiet_and_pending_tails_compose(monkeypatch):
    _liveness(monkeypatch)

    # entries + quiet + pending: the aggregate is a terminal sentence AFTER quiet.
    daemon, *_ = make_daemon(foreground=None)
    daemon.sessions.register("a", cwd="/x/board")          # 1: a pile entry
    daemon.history.record("a", "prose", "board line.")
    daemon.sessions.register("b", cwd="/x/docs")           # 2: quiet
    daemon.sessions.register("c", cwd="/x/jam")            # 3: quiet
    daemon.sessions.load_state({"p1": {"folder": "repo", "number": 4},
                                "p2": {"folder": "logs", "number": 5}})
    assert control._also_clause(daemon) == (
        " Also: 1 board, 1 unheard. Plus two quiet. Two pending.")

    # no entries, quiet > 0, pending > 0
    daemon, *_ = make_daemon(foreground=None)
    daemon.sessions.register("b", cwd="/x/docs")           # 1: quiet
    daemon.sessions.load_state({"p1": {"folder": "repo", "number": 2},
                                "p2": {"folder": "logs", "number": 3}})
    assert control._also_clause(daemon) == " All quiet. Two pending."

    # no entries, quiet == 0, pending > 0: the map is no longer empty
    daemon, *_ = make_daemon(foreground=None)
    daemon.sessions.load_state({"p1": {"folder": "repo", "number": 1},
                                "p2": {"folder": "logs", "number": 2}})
    assert control._also_clause(daemon) == " Two pending."

    # no other sessions at all: the trained absent landmark, unchanged
    daemon, *_ = make_daemon(foreground=None)
    assert control._also_clause(daemon) == ""


def test_pending_aggregate_degrades_to_many_never_a_digit(monkeypatch):
    # Same digit-free law as the quiet collapse: above the word map the count
    # degrades rather than reviving a spoken numeral.
    _liveness(monkeypatch)
    daemon, *_ = make_daemon(foreground=None)
    daemon.sessions.load_state({"p{0}".format(i): {"folder": "f{0}".format(i),
                                                   "number": i + 1}
                                for i in range(12)})
    out = control._also_clause(daemon)
    assert out == " Many pending."
    assert "12" not in out
