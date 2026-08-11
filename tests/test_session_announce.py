"""The new-session announce must survive the REAL hook emission.

SessionStart emits TWO messages (SET_FOREGROUND then SESSION_START) and the
first one registers the session — so any test that sends only the bare
SESSION_START proves nothing about production. These drive
hooks_entry.handle_event's actual output.
"""
from sonari import hooks_entry
from tests.daemon_helpers import make_daemon


def _spoken(daemon):
    return [i.text for st in daemon._streams.values() for i in st.queue._items]


def _feed(daemon, event, payload):
    for m in hooks_entry.handle_event(event, payload):
        daemon.handle_message(m)


def test_a_new_session_announces_itself_through_the_real_hook_sequence():
    daemon, _q, _sp, _se, _c = make_daemon(foreground=None)
    _feed(daemon, "SessionStart", {"session_id": "s1", "cwd": "/x/myproj"})
    assert any("myproj" in t for t in _spoken(daemon)), (
        "the new-session announce did not fire for the real two-message sequence")


def test_the_announce_does_not_repeat_on_a_second_session_start():
    """Resume/clear/compact re-fire SessionStart for a known id; the announce
    must not repeat. This is the property is_new was there to provide."""
    daemon, _q, _sp, _se, _c = make_daemon(foreground=None)
    _feed(daemon, "SessionStart", {"session_id": "s1", "cwd": "/x/myproj"})
    before = len(_spoken(daemon))
    _feed(daemon, "SessionStart", {"session_id": "s1", "cwd": "/x/myproj"})
    assert len(_spoken(daemon)) == before, "the announce repeated on resume"


def test_a_second_distinct_session_announces_too():
    daemon, _q, _sp, _se, _c = make_daemon(foreground=None)
    _feed(daemon, "SessionStart", {"session_id": "s1", "cwd": "/x/one"})
    _feed(daemon, "SessionStart", {"session_id": "s2", "cwd": "/x/two"})
    spoken = " ".join(_spoken(daemon))
    assert "one" in spoken and "two" in spoken


def test_quiet_verbosity_still_suppresses_the_announce():
    daemon, _q, _sp, _se, _c = make_daemon(verbosity="quiet", foreground=None)
    _feed(daemon, "SessionStart", {"session_id": "s1", "cwd": "/x/myproj"})
    assert not any("myproj" in t for t in _spoken(daemon))


def test_claim_announce_is_a_one_shot_reset_by_unregister():
    from sonari.sessions import SessionManager
    sm = SessionManager()
    sm.register("s1", cwd="/x/myproj")
    assert sm.claim_announce("s1") is True
    assert sm.claim_announce("s1") is False
    sm.unregister("s1")
    sm.register("s1", cwd="/x/myproj")
    assert sm.claim_announce("s1") is True
