"""Spec §6/§7 (amended 2026-07-14): the single-press holistic ⌃⌘W readout —
grammar v2: unified "Voice and keyboard: …"; diverged Keyboard carries its own
pile; Also = sentence entries in value-tier order + quiet collapse —
and the verbosity-gated registration announce. The double-press roster is
DELETED (owner amendment: one press announces everything)."""
from sonari.protocol import MsgType
from sonari.daemon.features import lifecycle
from tests.daemon_helpers import make_daemon, stream_queue


def _msg(t, session, **kw):
    from sonari.protocol import PROTOCOL_VERSION
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


# --- (a) the full merged string: divergence + a muted third + a waiting fourth ---
def test_holistic_w_speaks_the_full_merged_string_under_divergence():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.set_foreground("A", cwd="/x/web")          # keyboard, number 1
    sessions.register("B", cwd="/x/api")                # number 2
    sessions.set_speaker("B")                           # voice=B (api); kbd=A (web) -> diverged
    sessions.register("C", cwd="/x/etl")                # number 3, muted
    daemon._stream("C").stopped = True
    sessions.register("D", cwd="/x/logs")               # number 4, 2 waiting
    daemon._enqueue("D", "prose", "d1", False)
    daemon._enqueue("D", "prose", "d2", False)
    daemon.handle_message(_msg(MsgType.WHERE_AM_I, ""))
    daemon._speak_loop_once()
    assert speaker.spoken == [
        "Voice: api 2, playing. Keyboard: web 1. Also: 4 logs, 2 waiting. 3 etl, muted."
    ]


# --- (b) no other sessions -> NO "Also:" clause (the absence IS the signal) ---
def test_no_other_sessions_omits_the_also_clause():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.set_foreground("A", cwd="/x/web")
    daemon.handle_message(_msg(MsgType.WHERE_AM_I, ""))
    daemon._speak_loop_once()
    assert speaker.spoken == ["Voice and keyboard: web 1, playing."]


# --- (c) speaker None after stop_all -> state cue + Keyboard clause + the map
#         (grammar v2: the workspace rides the Keyboard clause, excluded from the map) ---
def test_speaker_none_after_stop_all_reads_state_cue_keyboard_and_map():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground=None)
    sessions.focus("A", cwd="/x/web")                   # number 1; workspace=A, NO stream yet
    sessions.register("B", cwd="/x/api")                # number 2
    daemon._enqueue("B", "prose", "b1", False)
    sessions.set_speaker(None)                          # idle voice, workspace stays A
    daemon.handle_message(_msg(MsgType.STOP_ALL, ""))   # speaker None -> no cue; B muted
    daemon.handle_message(_msg(MsgType.WHERE_AM_I, ""))
    assert speaker.earcons == []                        # playable workspace: no error tone
    items = list(stream_queue(daemon, "A")._items)
    assert [it.text for it in items] == [
        "All stopped. Keyboard: web 1. Also: 2 api, muted, 1 waiting."
    ]
    assert items[0].control_cue   # delivery flag unchanged


# --- (d) the exclusion rule: the Keyboard-clause session does NOT reappear in Also ---
def test_keyboard_session_does_not_reappear_in_the_also_map():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.set_foreground("A", cwd="/x/web")          # kbd, number 1
    sessions.register("B", cwd="/x/api")                # voice, number 2
    sessions.set_speaker("B")
    sessions.register("C", cwd="/x/etl")                # number 3 -> the ONLY Also entry
    daemon.history.record("C", "prose", "c line.")      # pile: a leaked keyboard session
    daemon.handle_message(_msg(MsgType.WHERE_AM_I, ""))  # would surface as "Plus one quiet."
    daemon._speak_loop_once()
    assert speaker.spoken == ["Voice: api 2, playing. Keyboard: web 1. Also: 3 etl, 1 unheard."]


# --- (e) unknown folder -> "{n} another session" ---
def test_unknown_folder_in_the_also_map_says_another_session():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.set_foreground("A", cwd="/x/web")
    sessions.register("C")                              # no cwd -> unknown folder, number 2
    daemon.history.record("C", "prose", "c line.")      # a pile, or quiet-collapse hides it
    daemon.handle_message(_msg(MsgType.WHERE_AM_I, ""))
    daemon._speak_loop_once()
    assert speaker.spoken == [
        "Voice and keyboard: web 1, playing. Also: 2 another session, 1 unheard."
    ]


# --- the registration announce ---
def test_session_start_announces_folder_and_number(monkeypatch):
    monkeypatch.setattr(lifecycle, "_setup_health", lambda v: ("ok", None))
    daemon, queue, speaker, sessions, _ = make_daemon(foreground=None)
    daemon.handle_message(_msg(MsgType.SESSION_START, "s1", cwd="/x/proj"))
    q = stream_queue(daemon, "s1")
    assert len(q) == 1
    item = q.pop_next()
    assert item.text == "1, proj."
    # Task 10 reverts the birth announce off control_cue: it is an ambient
    # announcement, not a gesture answer, so it must not bypass a mute.
    # names_session is unchanged -- it still names itself.
    assert item.names_session and not item.control_cue


def test_announce_suppressed_at_verbosity_quiet(monkeypatch):
    monkeypatch.setattr(lifecycle, "_setup_health", lambda v: ("ok", None))
    daemon, queue, speaker, sessions, _ = make_daemon(verbosity="quiet", foreground=None)
    daemon.handle_message(_msg(MsgType.SESSION_START, "s1", cwd="/x/proj"))
    assert len(stream_queue(daemon, "s1")) == 0


def test_announce_not_refired_on_resume_of_a_known_session(monkeypatch):
    monkeypatch.setattr(lifecycle, "_setup_health", lambda v: ("ok", None))
    daemon, queue, speaker, sessions, _ = make_daemon(foreground=None)
    daemon.handle_message(_msg(MsgType.SESSION_START, "s1", cwd="/x/proj"))
    daemon.handle_message(_msg(MsgType.SESSION_START, "s1", cwd="/x/proj"))  # resume/compact
    assert len(stream_queue(daemon, "s1")) == 1        # ONE announce, not two


def test_announce_unknown_folder_says_another_session(monkeypatch):
    monkeypatch.setattr(lifecycle, "_setup_health", lambda v: ("ok", None))
    daemon, queue, speaker, sessions, _ = make_daemon(foreground=None)
    daemon.handle_message(_msg(MsgType.SESSION_START, "s1"))
    assert stream_queue(daemon, "s1").pop_next().text == "1, Another session."
