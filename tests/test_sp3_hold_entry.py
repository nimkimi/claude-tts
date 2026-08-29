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


# --- F1 + D2 §6.3: SESSION_END of the MUTED speaker lifts the phantom hold AUDIBLY ---
def test_session_end_of_muted_speaker_lifts_phantom_quiet_hold():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    daemon._enqueue("B", "prose", "b backlog", False)
    daemon.handle_message(_msg(MsgType.STOP_SESSION, "A"))         # quiet-hold on A
    daemon.handle_message(_msg(MsgType.SESSION_END, "A"))          # the muted speaker ends
    assert daemon.voice_state == "flowing"                        # phantom hold cleared
    assert sessions.speaker() is None
    daemon._speak_loop_once()                                     # keep-going: the lift word first
    assert sessions.speaker() == "B"
    assert speaker.spoken[-1] == "Resumed."                       # D2 §6.3 audible lift
    daemon._speak_loop_once()                                     # then the resumed backlog
    assert any(s and "b backlog" in s for s in speaker.spoken)


def test_session_end_lift_with_nothing_waiting_stays_wordless():
    # No eligible keep-going pick == no audible resumption to mark (seam 6).
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")                             # B idle, empty queue
    daemon.handle_message(_msg(MsgType.STOP_SESSION, "A"))
    daemon.handle_message(_msg(MsgType.SESSION_END, "A"))
    assert daemon.voice_state == "flowing"
    assert all(len(st.queue) == 0 for st in daemon._streams.values())


# --- Whole-branch F1: with TWO+ eligible backgrounds the lift must mark the
# session the loop ACTUALLY adopts, not a handler-time pre-pick. Pre-picking and
# front-enqueuing "Resumed." gave that stream the daemon-global max id, flipping
# oldest_id() so the loop adopted the OTHER session — the real resumption went
# unmarked and a stale word played before unrelated content. The handler now arms
# a flag; the loop marks its own keep-going pick. ---
def test_session_end_lift_marks_the_actual_adoptee_with_two_backgrounds():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    sessions.register("C", cwd="/x/C")
    daemon._enqueue("B", "prose", "b backlog", False)             # oldest id -> the pick
    daemon._enqueue("C", "prose", "c backlog", False)             # newer id
    daemon.handle_message(_msg(MsgType.STOP_SESSION, "A"))         # quiet-hold on A
    daemon.handle_message(_msg(MsgType.SESSION_END, "A"))          # muted speaker ends
    daemon._speak_loop_once()                                     # keep-going adopts B
    assert sessions.speaker() == "B"                             # the true oldest pick
    spoken = [s for s in speaker.spoken if s]
    assert spoken[0] == "Resumed."                              # marked ON the adoptee
    for _ in range(6):                                           # drain B then C
        daemon._speak_loop_once()
    spoken = [s for s in speaker.spoken if s]
    assert spoken.count("Resumed.") == 1                        # exactly one, no stale word


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


# --- a UPS SET_FOREGROUND re-populates identity after a simulated restart-wipe ---
def test_ups_recaptures_identity_after_wipe(monkeypatch):
    monkeypatch.setattr(lifecycle, "_setup_health", lambda v: ("ok", None))
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    daemon.handle_message(_msg(MsgType.SESSION_START, "s1", cwd="/x/proj",
                               term_program="Apple_Terminal", tty="/dev/ttys009"))
    assert sessions.identity("s1").tty == "/dev/ttys009"
    sessions._identities.pop("s1", None)                 # simulate the daemon-restart gap
    assert sessions.identity("s1") is None
    daemon.handle_message(_msg(MsgType.SET_FOREGROUND, "s1", cwd="/x/proj",
                               term_program="Apple_Terminal", tty="/dev/ttys009"))
    assert sessions.identity("s1") is not None
    assert sessions.identity("s1").tty == "/dev/ttys009"


# --- a partial UPS (tty moved, program empty) updates tty, keeps the good program ---
def test_ups_partial_identity_updates_only_nonempty_fields(monkeypatch):
    monkeypatch.setattr(lifecycle, "_setup_health", lambda v: ("ok", None))
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    daemon.handle_message(_msg(MsgType.SESSION_START, "s1", cwd="/x",
                               term_program="Apple_Terminal", tty="/dev/ttys009"))
    daemon.handle_message(_msg(MsgType.SET_FOREGROUND, "s1", cwd="/x",
                               term_program="", tty="/dev/ttys010"))
    assert sessions.identity("s1").tty == "/dev/ttys010"        # updated
    assert sessions.identity("s1").term_program == "Apple_Terminal"   # empty kept the good value


# --- an all-empty UPS does NOT touch identity (the "field present" guard skips it) ---
def test_ups_all_empty_identity_does_not_touch(monkeypatch):
    monkeypatch.setattr(lifecycle, "_setup_health", lambda v: ("ok", None))
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    daemon.handle_message(_msg(MsgType.SESSION_START, "s1", cwd="/x",
                               term_program="Apple_Terminal", tty="/dev/ttys009"))
    daemon.handle_message(_msg(MsgType.SET_FOREGROUND, "s1", cwd="/x",
                               term_program="", tty="", iterm_session_id=""))
    assert sessions.identity("s1").tty == "/dev/ttys009"        # preserved


# --- ⌃⌘W wording for the now-reachable states (state word; speaker present) ---
def test_where_am_i_reports_on_hold_under_quiet_hold():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="fg")
    sessions.set_foreground("fg", cwd="/x/work")
    daemon.handle_message(_msg(MsgType.STOP_SESSION, "fg"))
    daemon.handle_message(_msg(MsgType.WHERE_AM_I, "fg"))
    daemon._speak_loop_once()                                    # control cue voices under hold
    assert speaker.spoken[-1] == "Voice and keyboard: work 1, on hold."


def test_where_am_i_reports_all_stopped_under_stopped_all():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="fg")
    sessions.set_foreground("fg", cwd="/x/work")
    daemon.handle_message(_msg(MsgType.STOP_ALL, ""))
    daemon.handle_message(_msg(MsgType.WHERE_AM_I, "fg"))
    daemon._speak_loop_once()
    assert speaker.spoken[-1] == "Voice and keyboard: work 1, all stopped."
