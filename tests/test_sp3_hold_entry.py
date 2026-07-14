from sonari.protocol import MsgType
from sonari.daemon.features import lifecycle
from tests.daemon_helpers import make_daemon


def _msg(t, session, **kw):
    from sonari.protocol import PROTOCOL_VERSION
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


# --- ENTRY: ⌃⌘S -> quiet-hold; Q1 invariant (speaker stream stopped) ---
def test_stop_session_enters_quiet_hold_and_stops_speaker_stream():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    daemon.handle_message(_msg(MsgType.STOP_SESSION, "A"))
    assert daemon.voice_state == "quiet-hold"
    assert daemon._stream(sessions.speaker()).stopped is True     # Q1


def test_stop_all_enters_stopped_all():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    daemon.handle_message(_msg(MsgType.STOP_ALL, ""))
    assert daemon.voice_state == "stopped-all"
    assert daemon._stream("A").stopped and daemon._stream("B").stopped


# --- the hold SUPPRESSES keep-going for everyone (end-to-end) ---
def test_quiet_hold_suppresses_keep_going():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    daemon._enqueue("B", "prose", "b backlog", False)
    daemon.handle_message(_msg(MsgType.STOP_SESSION, "A"))         # quiet-hold, A stopped
    daemon._speak_loop_once()                                     # held branch; no keep-go
    assert sessions.speaker() == "A"
    assert not any(s and "b backlog" in s for s in speaker.spoken)


# --- F1: SESSION_END of the MUTED speaker lifts a phantom quiet-hold ---
def test_session_end_of_muted_speaker_lifts_phantom_quiet_hold():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    daemon._enqueue("B", "prose", "b backlog", False)
    daemon.handle_message(_msg(MsgType.STOP_SESSION, "A"))         # quiet-hold on A
    daemon.handle_message(_msg(MsgType.SESSION_END, "A"))          # the muted speaker ends
    assert daemon.voice_state == "flowing"                        # phantom hold cleared
    assert sessions.speaker() is None
    daemon._speak_loop_once()                                     # keep-going resumes onto B
    assert sessions.speaker() == "B"
    assert any(s and "b backlog" in s for s in speaker.spoken)


# --- F1: SESSION_END under stopped-all STAYS stopped-all (others still muted) ---
def test_session_end_under_stopped_all_stays_stopped_all():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    daemon.handle_message(_msg(MsgType.STOP_ALL, ""))
    daemon.handle_message(_msg(MsgType.SESSION_END, "A"))
    assert daemon.voice_state == "stopped-all"


# --- F2: a session born AFTER ⌃⌘M (speaker ended -> None bootstrap) is muted + silent ---
def test_session_born_under_stopped_all_is_muted_and_silent(monkeypatch):
    monkeypatch.setattr(lifecycle, "_setup_health", lambda v: ("ok", None))
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    daemon.handle_message(_msg(MsgType.STOP_ALL, ""))             # stopped-all; A stopped
    daemon.handle_message(_msg(MsgType.SESSION_END, "A"))         # speaker() -> None; state stays
    assert sessions.speaker() is None and daemon.voice_state == "stopped-all"
    daemon.handle_message(_msg(MsgType.SESSION_START, "N", cwd="/x/N",
                               term_program="Apple_Terminal", tty="/dev/ttysN"))
    daemon._enqueue("N", "prose", "late output", False)
    assert daemon._stream("N").stopped is True                   # born muted (closes primary-pop leak)
    daemon._speak_loop_once()
    assert not any(s and "late output" in s for s in speaker.spoken)


# --- F2 negative: under quiet-hold a NEW session is born ACTIVE (piles + dings, not muted) ---
def test_session_born_under_quiet_hold_is_active(monkeypatch):
    monkeypatch.setattr(lifecycle, "_setup_health", lambda v: ("ok", None))
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    daemon.handle_message(_msg(MsgType.STOP_SESSION, "A"))        # quiet-hold (only A muted)
    daemon.handle_message(_msg(MsgType.SESSION_START, "N", cwd="/x/N",
                               term_program="Apple_Terminal", tty="/dev/ttysN"))
    daemon._enqueue("N", "prose", "n out", False)
    assert daemon._stream("N").stopped is False                  # born active under quiet-hold


# --- ⌃⌘W wording for the now-reachable states (state word; speaker present) ---
def test_where_am_i_reports_on_hold_under_quiet_hold():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="fg")
    sessions.set_foreground("fg", cwd="/x/work")
    daemon.handle_message(_msg(MsgType.STOP_SESSION, "fg"))
    daemon.handle_message(_msg(MsgType.WHERE_AM_I, "fg"))
    daemon._speak_loop_once()                                    # pause_exempt cue voices under hold
    assert speaker.spoken[-1] == "Voice: work 1, on hold."


def test_where_am_i_reports_all_stopped_under_stopped_all():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="fg")
    sessions.set_foreground("fg", cwd="/x/work")
    daemon.handle_message(_msg(MsgType.STOP_ALL, ""))
    daemon.handle_message(_msg(MsgType.WHERE_AM_I, "fg"))
    daemon._speak_loop_once()
    assert speaker.spoken[-1] == "Voice: work 1, all stopped."
