"""D3 spec §4a: the ⌃⌘W readout consults liveness() per session and MARKS the
non-live tiers instead of narrating them as live (R4's silent quarantine /
T1's dead-tty lie). Also-map half: a pending session WITH content clauses is
NAMED with a leading ", pending"; a dead one with ", closed". A clause-less
pending session collapses into the aggregate "{Count} pending." tail — never
a named phantom — and a clause-less dead session is dropped entirely (nothing
to act on). Pointer half (Task 5 + F-D3-1): a dead Voice/Keyboard/unified
pointer gains a trailing ", closed" marker clause after its state/number and
before any content clause — "playing, closed" is deliberate honesty (the
voice can still be reading a closed session's stored stream). The VOICE
pointer can also be PENDING — keep-going may adopt a still-quarantined
restored session — and gains the symmetric ", pending" marker the same way
("playing, pending, decision."); the Keyboard/idle pointer (workspace())
cannot resolve to a provisional session (recon §6, T10's guard test), so its
pending leg stays a structural no-op. Live entries keep grammar v2
byte-for-byte; these strings are byte-exact for the same reason the
grammar-v2 suites are."""
from sonari.protocol import PROTOCOL_VERSION
from sonari import ttyutil
from sonari.daemon.features import control
from sonari.sessions import Identity
from tests.daemon_helpers import make_daemon, stream_queue


def _liveness(monkeypatch, dead=()):
    """Fake tty_alive: empty tty -> live (fail-open); else live iff not in `dead`."""
    monkeypatch.setattr(ttyutil, "tty_alive",
                        lambda tty: True if not tty else tty not in dead)


def _ident(sessions, sid, tty):
    sessions.set_identity(sid, Identity(term_program="Apple_Terminal", tty=tty))


def _focus_on(sessions, sid, tty):
    """Diverge the workspace onto *sid* via a matched OS-focus signal (the
    idiom test_whereami_v2.py uses to drift the keyboard)."""
    sessions.set_identity(sid, Identity(term_program="Apple_Terminal", tty=tty))
    sessions.set_os_focus(term_program="Apple_Terminal", tty=tty)


def _msg(t, session, **kw):
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


def _where(daemon, speaker, session):
    daemon.handle_message(_msg("where_am_i", session))
    daemon._speak_loop_once()
    return speaker.spoken[-1]


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


# --- Task 5: pointer clauses (Voice / Keyboard / unified) -------------------


def test_dead_keyboard_pointer_gains_closed_clause(monkeypatch):
    _liveness(monkeypatch, dead={"/dev/ttysW"})
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    sessions.register("web", cwd="/x/web")                  # number 1
    sessions.register("voice", cwd="/x/voice")               # number 2
    sessions.set_foreground("voice")
    sessions.set_speaker("voice")
    daemon.history.record("web", "prose", "l1.")
    daemon.history.record("web", "prose", "l2.")
    _focus_on(sessions, "web", "/dev/ttysW")                 # workspace drifts to the dead web session
    out = _where(daemon, speaker, "voice")
    assert out == "Voice: voice 2, playing. Keyboard: web 1, closed, 2 unheard."


def test_dead_voice_pointer_gains_closed_clause(monkeypatch):
    _liveness(monkeypatch, dead={"/dev/ttysA"})
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    sessions.register("web", cwd="/x/web")                  # number 1
    sessions.register("api", cwd="/x/api")                   # number 2
    sessions.set_foreground("api")
    sessions.set_speaker("api")
    _ident(sessions, "api", "/dev/ttysA")                     # api's captured node is gone
    _focus_on(sessions, "web", "/dev/ttysB")                 # diverged, keyboard on live web
    out = _where(daemon, speaker, "api")
    assert out.startswith("Voice: api 2, playing, closed.")


def test_unified_dead_pointer(monkeypatch):
    _liveness(monkeypatch, dead={"/dev/ttysA"})
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    sessions.register("other", cwd="/x/other")               # number 1
    sessions.register("api", cwd="/x/api")                    # number 2
    sessions.set_foreground("api")
    sessions.set_speaker("api")
    _ident(sessions, "api", "/dev/ttysA")                     # api's captured node is gone
    out = _where(daemon, speaker, "api")
    assert out.startswith("Voice and keyboard: api 2, playing, closed.")


def test_idle_branch_dead_keyboard(monkeypatch):
    _liveness(monkeypatch, dead={"/dev/ttysW"})
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    sessions.register("web", cwd="/x/web")                  # number 1
    sessions.set_foreground("web")
    _ident(sessions, "web", "/dev/ttysW")                     # web's captured node is gone
    sessions.set_speaker(None)                                # loop idle
    daemon.handle_message(_msg("where_am_i", "web"))
    texts = [it.text for it in stream_queue(daemon, "web")._items]
    assert texts[0] == "Nothing playing. Keyboard: web 1, closed."


def test_dead_voice_with_decision_orders_closed_before_decision(monkeypatch):
    _liveness(monkeypatch, dead={"/dev/ttysA"})
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    sessions.register("web", cwd="/x/web")                  # number 1
    sessions.register("api", cwd="/x/api")                    # number 2
    sessions.set_foreground("api")
    sessions.set_speaker("api")
    _ident(sessions, "api", "/dev/ttysA")                     # api's captured node is gone
    _focus_on(sessions, "web", "/dev/ttysB")                 # diverged, keyboard on live web
    daemon._pending_decisions["api"] = {"text": "Bash: rm x"}
    out = _where(daemon, speaker, "api")
    assert out.startswith("Voice: api 2, playing, closed, decision.")


def test_pending_voice_pointer_from_adoption_gains_pending_clause(monkeypatch):
    """F-D3-1: unlike the Keyboard/idle pointer (workspace(), structurally never
    pending — recon §6, T10's guard test), the VOICE pointer (speaker()) is NOT
    pending-proof: keep-going's adoption calls set_speaker on a still-quarantined
    restored session (host.py's _select_keep_going). ADOPTION recipe (pins the
    CREATION PATH, not just the string): a daemon-authored item — never
    session-authored, or R1 would clear the quarantine — is seeded under the
    lock on a load_state'd session and adopted on one speak-loop turn, leaving
    speaker() pending."""
    _liveness(monkeypatch)
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    sessions.load_state({"s1": {"folder": "repo", "number": 1}})
    assert sessions.is_provisional("s1") is True
    with daemon._lock:
        daemon._enqueue("s1", "prose", "Resumed.", False,
                        mute_exempt=True, pause_exempt=True)
    daemon._speak_loop_once()                       # adopts s1
    assert sessions.speaker() == "s1"
    assert sessions.liveness("s1") == "pending"
    # session="" (no session field): a real ⌃⌘W hotkey message carries none
    # (wb-runtime.md's R1 audit); the "" here must NOT clear s1's own quarantine.
    daemon.handle_message(_msg("where_am_i", ""))
    daemon._speak_loop_once()
    out = speaker.spoken[-1]
    assert out.startswith("Voice and keyboard: repo 1, playing, pending.")
    assert sessions.liveness("s1") == "pending"      # the press itself proves nothing


def test_live_pointers_unchanged(monkeypatch):
    _liveness(monkeypatch)                                    # everyone alive: no marker path
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    sessions.register("web", cwd="/x/web")                  # number 1
    sessions.register("api", cwd="/x/api")                    # number 2
    sessions.set_foreground("api")
    sessions.set_speaker("api")
    daemon.history.record("web", "prose", "l1.")
    daemon.history.record("web", "prose", "l2.")
    _focus_on(sessions, "web", "/dev/ttysW")                 # diverged, keyboard on live web
    out = _where(daemon, speaker, "api")
    assert out == "Voice: api 2, playing. Keyboard: web 1, 2 unheard."
    assert "closed" not in out
