"""Spec §6 session numbers (stable lowest-free) + §8 recency (MRU, deliberate
acts only). Pure SessionManager unit tests — no daemon, no ttys, no stubs needed
(is_live is never called here)."""
from sonari.sessions import Identity, SessionManager


# --- numbering: lowest-free, stable, holes refill, >9 speakable ---
def test_numbers_assigned_lowest_free_at_registration():
    m = SessionManager()
    m.register("a"); m.register("b"); m.register("c")
    assert (m.number("a"), m.number("b"), m.number("c")) == (1, 2, 3)


def test_number_stable_across_re_registration_and_foreground():
    m = SessionManager()
    m.register("a"); m.register("b")
    m.set_foreground("a", cwd="/x/a")      # re-records a
    m.register("b", cwd="/x/b")            # re-records b
    assert m.number("a") == 1 and m.number("b") == 2


def test_unregister_frees_the_number_and_the_hole_refills():
    m = SessionManager()
    m.register("a"); m.register("b"); m.register("c")
    m.unregister("b")
    assert m.number("b") is None
    m.register("d")
    assert m.number("d") == 2              # lowest FREE, not max+1


def test_numbers_above_nine_are_assigned():
    m = SessionManager()
    for i in range(11):
        m.register("s{0}".format(i))
    assert m.number("s10") == 11           # spoken but digit-unreachable (spec §6)


def test_session_for_number_round_trip_and_unknown():
    m = SessionManager()
    m.register("a"); m.register("b")
    assert m.session_for_number(2) == "b"
    assert m.session_for_number(7) is None


def test_set_foreground_and_focus_assign_numbers_too():
    m = SessionManager()
    m.set_foreground("fg")                 # every _record path numbers
    m.focus("j")
    assert m.number("fg") == 1 and m.number("j") == 2


# --- MRU: deliberate acts only ---
def test_mru_updated_by_set_foreground_and_focus_most_recent_first():
    m = SessionManager()
    m.set_foreground("a")
    m.focus("b")
    m.set_foreground("c")
    assert m.mru() == ["c", "b", "a"]
    m.focus("a")                           # re-touch moves to front, no duplicate
    assert m.mru() == ["a", "c", "b"]


def test_mru_never_updated_by_set_speaker():
    m = SessionManager()
    m.set_foreground("a")
    m.register("b")
    m.set_speaker("b")                     # keep-going voice drift is NOT presence
    assert m.mru() == ["a"]


def test_mru_updated_by_matched_os_focus_only():
    m = SessionManager()
    m.set_foreground("a")
    m.register("b")
    m.set_identity("b", Identity(term_program="Apple_Terminal", tty="/dev/ttysB"))
    m.set_os_focus(term_program="Apple_Terminal", tty="/dev/ttysB")   # a click: matched
    assert m.mru()[0] == "b"
    m.set_os_focus(term_program="Apple_Terminal", tty="/dev/ttysZZ")  # unmatched
    assert m.mru()[0] == "b"               # no phantom touch


def test_unregister_removes_from_mru():
    m = SessionManager()
    m.set_foreground("a")
    m.focus("b")
    m.unregister("b")
    assert m.mru() == ["a"]


def test_mru_returns_a_copy():
    m = SessionManager()
    m.set_foreground("a")
    m.mru().append("evil")
    assert m.mru() == ["a"]
