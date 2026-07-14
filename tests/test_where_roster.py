"""Spec §6/§7: numbers in the ⌃⌘W clauses, the double-press roster (2.0 s,
daemon-side, injectable clock), and the verbosity-gated registration announce."""
from sonari.protocol import MsgType
from sonari.daemon.features import control, lifecycle
from tests.daemon_helpers import make_daemon, stream_queue


def _msg(t, session, **kw):
    from sonari.protocol import PROTOCOL_VERSION
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


def _clock(monkeypatch, start=100.0):
    t = {"v": start}
    monkeypatch.setattr(control, "_now", lambda: t["v"])
    return t


# --- double-press detection: 1.9 s escalates, 2.1 s does not ---
def test_double_press_within_2s_escalates_to_the_roster(monkeypatch):
    t = _clock(monkeypatch)
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.set_foreground("A", cwd="/x/web")
    sessions.register("B", cwd="/x/api")
    daemon._enqueue("B", "prose", "b1", False)
    daemon._enqueue("B", "prose", "b2", False)
    daemon._stream("B").stopped = True                  # muted AND 2 waiting
    daemon.handle_message(_msg(MsgType.WHERE_AM_I, ""))
    daemon._speak_loop_once()
    assert speaker.spoken[-1] == "Voice: web 1, Playing. 0 waiting, 1 muted."
    t["v"] += 1.9
    daemon.handle_message(_msg(MsgType.WHERE_AM_I, ""))
    daemon._speak_loop_once()
    assert speaker.spoken[-1] == "1, web. 2, api, muted, 2 waiting."


def test_slow_second_press_repeats_the_summary(monkeypatch):
    t = _clock(monkeypatch)
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.set_foreground("A", cwd="/x/web")
    daemon.handle_message(_msg(MsgType.WHERE_AM_I, ""))
    daemon._speak_loop_once()
    t["v"] += 2.1
    daemon.handle_message(_msg(MsgType.WHERE_AM_I, ""))
    daemon._speak_loop_once()
    assert speaker.spoken[-1] == "Voice: web 1, Playing. 0 waiting, 0 muted."


def test_roster_lists_all_sessions_in_number_order(monkeypatch):
    t = _clock(monkeypatch)
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.set_foreground("A", cwd="/x/web")
    sessions.register("B", cwd="/x/api")
    sessions.register("C")                              # unknown folder
    daemon._enqueue("C", "prose", "c1", False)
    daemon.handle_message(_msg(MsgType.WHERE_AM_I, ""))
    daemon._speak_loop_once()
    t["v"] += 0.5
    daemon.handle_message(_msg(MsgType.WHERE_AM_I, ""))
    daemon._speak_loop_once()
    assert speaker.spoken[-1] == "1, web. 2, api. 3, another session, 1 waiting."


def test_roster_delivery_barges_in_and_resumes_like_the_summary(monkeypatch):
    from sonari.queue import SpeechItem
    t = _clock(monkeypatch)
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.set_foreground("A", cwd="/x/web")
    daemon.handle_message(_msg(MsgType.WHERE_AM_I, ""))
    daemon._speak_loop_once()
    daemon._current_item = SpeechItem(id=905, session="A", kind="prose",
                                      text="interrupted", is_decision=False)
    t["v"] += 1.0
    cancels_before = speaker.cancels
    daemon.handle_message(_msg(MsgType.WHERE_AM_I, ""))
    assert speaker.cancels == cancels_before + 1        # barge-in
    texts = [it.text for it in daemon._stream("A").queue._items]
    assert texts[0] == "1, web."                        # roster first
    assert texts[1] == "interrupted"                    # then the resume


# --- the registration announce ---
def test_session_start_announces_folder_and_number(monkeypatch):
    monkeypatch.setattr(lifecycle, "_setup_health", lambda v: ("ok", None))
    daemon, queue, speaker, sessions, _ = make_daemon(foreground=None)
    daemon.handle_message(_msg(MsgType.SESSION_START, "s1", cwd="/x/proj"))
    q = stream_queue(daemon, "s1")
    assert len(q) == 1
    item = q.pop_next()
    assert item.text == "proj, 1."
    assert item.mute_exempt and item.names_session


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
    assert stream_queue(daemon, "s1").pop_next().text == "Another session, 1."
