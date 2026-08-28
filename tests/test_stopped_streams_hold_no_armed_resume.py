"""R21 as an executable INVARIANT: no stopped stream anywhere ever holds an
armed `announce_resume`.

An armed-but-undelivered "Resumed." that survives the stop which falsified it
rides to the next FLUSH/SESSION_START and speaks on the very stream the press
just muted -- through the mute, because it is a control cue.

Stated as an invariant rather than as a list of the sites that arm and clear
the flag, deliberately. The enumeration this replaces was built from a grep
for `.stopped = True` and it already missed a site: playback.py's stop-all can
CREATE the speaker's stream, and a stream that did not exist when the loop ran
is not a stream the loop cleared. An invariant cannot go stale as the code
grows; an enumeration silently can, and did.
"""
from tests.daemon_helpers import make_daemon
from sonari.protocol import MsgType
from sonari.session_stream import SessionStream


def _msg(t, session, **kw):
    from sonari.protocol import PROTOCOL_VERSION
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


def _violations(daemon):
    return {sid: (st.stopped, st.announce_resume)
            for sid, st in daemon._streams.items()
            if st.stopped and st.announce_resume}


def test_invariant_holds_after_stop_session():
    daemon, _, speaker, sessions, _ = make_daemon()
    daemon.voice_state = "quiet-hold"
    daemon.handle_message(_msg(MsgType.SET_FOREGROUND, "fg", cwd="/x/fg"))
    assert daemon._stream("fg").announce_resume is True          # armed
    daemon.handle_message(_msg(MsgType.STOP_SESSION, "fg"))
    assert _violations(daemon) == {}, _violations(daemon)


def test_invariant_holds_after_stop_all_with_many_armed_streams():
    daemon, _, speaker, sessions, _ = make_daemon()
    for sid in ("a", "b", "c"):
        daemon.voice_state = "quiet-hold"
        daemon.handle_message(_msg(MsgType.SET_FOREGROUND, sid, cwd="/x/" + sid))
        assert daemon._stream(sid).announce_resume is True
    daemon.handle_message(_msg(MsgType.STOP_ALL, "a"))
    assert _violations(daemon) == {}, _violations(daemon)


def test_invariant_holds_for_the_speaker_stream_created_BY_stop_all():
    """The site the enumeration missed. on_stop_all's `_stream(spk).stopped =
    True` can CREATE a stream the loop above it never saw, so clearing the flag
    inside that loop is not sufficient. Force the speaker to have no stream at
    STOP_ALL time."""
    daemon, _, speaker, sessions, _ = make_daemon(foreground=None)
    sessions.set_foreground("ghost", cwd="/x/ghost")
    daemon._streams.pop("ghost", None)                 # speaker with NO stream
    assert sessions.speaker() == "ghost"
    assert "ghost" not in daemon._streams
    daemon.handle_message(_msg(MsgType.STOP_ALL, "ghost"))
    assert daemon._stream("ghost").stopped is True     # the site fired
    assert _violations(daemon) == {}, _violations(daemon)


def test_invariant_holds_for_a_stream_born_stopped_under_stop_all():
    daemon, _, speaker, sessions, _ = make_daemon(foreground="A")
    daemon.handle_message(_msg(MsgType.STOP_ALL, "A"))
    daemon.handle_message(_msg(MsgType.SESSION_START, "NEW", cwd="/x/new"))
    assert daemon._stream("NEW").stopped is True       # host.py's arm fired
    assert _violations(daemon) == {}, _violations(daemon)


def test_load_state_would_be_a_hole_if_it_ran_on_a_live_armed_stream():
    """A deliberate TRIPWIRE, not a passing guarantee.

    SessionStream.load_state sets `stopped` from persisted data -- the only
    non-literal write to it, and the one a grep for `.stopped = True` cannot
    see -- and it does NOT clear announce_resume. So the invariant above rests
    entirely on load_state being boot-only, called on freshly built objects
    before anything can arm them, and NOT on any enumeration of stop sites.

    This test pins that dependency in the open. If load_state ever gains a
    runtime caller, the invariant breaks and this receipt is where the reason
    is written down.
    """
    st = SessionStream()
    st.announce_resume = True
    st.load_state({"frontier": None, "stopped": True})
    assert st.stopped is True
    assert st.announce_resume is True          # <-- survives; harmless ONLY
                                               #     because the single call
                                               #     site builds fresh objects
                                               #     pre-bind.
