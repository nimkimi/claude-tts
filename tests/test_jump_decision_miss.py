"""W4 (spec §5) + the REREAD_OPTIONS sub-item: ⌃⌘D on a session with no hit
must say so and do NOTHING else — no drain, no cancel, no pointer/enum writes,
no raise. Hit predicate is two-part: queued decision OR live pending blocking
decision (queue-scoped alone would lie over an answerable-but-already-read ask)."""
import sonari.ttyutil as ttyutil
from sonari.protocol import PROTOCOL_VERSION
from sonari.sessions import Identity
from tests.daemon_helpers import make_daemon, stream_queue
from tests.test_daemon_focus_follow import RecordingRaiseService


def _msg(t, session, **kw):
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


def _liveness(monkeypatch, dead):
    """Fake tty_alive: empty tty -> live (fail-open); else live iff not in `dead`."""
    monkeypatch.setattr(ttyutil, "tty_alive",
                        lambda tty: True if not tty else tty not in dead)


def _ident(sessions, sid, tty):
    sessions.set_identity(sid, Identity(term_program="Apple_Terminal", tty=tty))


def test_miss_speaks_the_cue_and_touches_nothing():
    daemon, queue, speaker, sessions, config = make_daemon()
    rs = RecordingRaiseService(will=True)
    daemon.raise_service = rs
    daemon.voice_state = "quiet-hold"              # any enum write would be visible
    daemon._enqueue("fg", "prose", "backlog one.", False)
    daemon._enqueue("fg", "prose", "backlog two.", False)
    before = [(it.id, it.text) for it in queue._items]
    daemon.handle_message(_msg("jump_decision", "fg"))
    after = [(it.id, it.text) for it in queue._items]
    assert after[0][1] == "No decision here."
    assert after[1:] == before                     # queue preserved byte-for-byte behind the cue
    assert speaker.cancels == 0                    # no barge-in
    assert daemon.voice_state == "quiet-hold"      # no enum write
    assert rs.attempts == []                       # no window raise
    head = queue._items[0]
    assert head.control_cue  # spec-mandated flag


def test_queued_decision_hit_behaves_exactly_as_today():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon._enqueue("fg", "prose", "chatter.", False)
    daemon._enqueue("fg", "permission", "May I write x?", True)
    daemon.handle_message(_msg("jump_decision", "fg"))
    assert queue._items[0].is_decision             # drained to the decision
    assert speaker.cancels == 1                    # today's barge-in, unchanged


def test_pending_request_stores_its_spoken_text():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon.handle_message(_msg("permission_request", "fg", tool="Bash",
                               summary="rm -rf build"))
    assert daemon._pending_decisions["fg"]["text"] == "Bash: rm -rf build"


def test_live_pending_but_unqueued_respeaks_the_stored_prompt():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon.handle_message(_msg("permission_request", "fg", tool="Bash",
                               summary="rm -rf build"))
    queue.pop_next()                               # already narrated; still answerable
    daemon.handle_message(_msg("jump_decision", "fg"))
    texts = [it.text for it in queue._items]
    assert texts[0] == "Bash: rm -rf build"        # re-spoken from _pending_decisions["text"]
    assert "No decision here." not in texts        # never claims "no decision" over an answerable one
    assert speaker.cancels == 0                    # no drain, no barge-in


def test_reread_options_falls_back_to_the_pending_text():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon.handle_message(_msg("permission_request", "fg", tool="Bash",
                               summary="rm -rf build"))
    # on_permission_request never writes st.options -> options is empty for fg.
    daemon.handle_message(_msg("reread_options", "fg"))
    assert [it.text for it in queue._items][-1] == "Bash: rm -rf build"


def test_reread_options_without_any_pending_still_says_no_options():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon.handle_message(_msg("reread_options", "fg"))
    assert [it.text for it in queue._items] == ["No options right now."]


def test_miss_with_no_speaker_and_no_target_plays_error_tone():
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    daemon.handle_message(_msg("jump_decision", ""))
    assert speaker.earcons == ["error"]


# --- D3 §4i: the crossed path consults the chokepoint (Task 8) -------------


def test_crossed_jump_decision_onto_dead_target_no_move(monkeypatch):
    """The crossed path (target != speaker) MOVES the voice on a HIT — a dead
    target must not get it. Mirrors the chooser's/on_jump_waiting's commit-time
    dead-target guard, same CLOSED_WORD channel (D3 spec §4i)."""
    _liveness(monkeypatch, dead={"/dev/ttysB"})
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    _ident(sessions, "A", "/dev/ttysA")
    sessions.register("B", cwd="/x/B")
    _ident(sessions, "B", "/dev/ttysB")
    sessions.set_os_focus(term_program="Apple_Terminal", tty="/dev/ttysB")  # workspace -> B
    assert sessions.workspace() == "B"
    assert sessions.speaker() == "A"                       # crossed: target(B) != speaker(A)
    daemon._enqueue("B", "permission", "Allow X?", True)    # queued decision -> has_queued True
    rs = RecordingRaiseService(will=True)
    daemon.raise_service = rs
    daemon.voice_state = "quiet-hold"                       # a value the guard must NOT touch
    b_before = [(it.id, it.text, it.is_decision) for it in stream_queue(daemon, "B")._items]

    daemon.handle_message(_msg("jump_decision", ""))

    assert speaker.earcons == ["error"]
    texts = [it.text for it in stream_queue(daemon, "A")._items]   # speaker()-or-workspace(): A
    assert "That session closed." in texts
    assert sessions.speaker() == "A"                        # no focus move
    assert sessions.foreground() == "A"
    assert daemon.voice_state == "quiet-hold"               # :212 write never reached
    b_after = [(it.id, it.text, it.is_decision) for it in stream_queue(daemon, "B")._items]
    assert b_after == b_before                              # B's queue untouched, still queued
    assert speaker.cancels == 0                             # no barge-in
    assert rs.attempts == []                                # no raise attempted


def test_crossed_jump_decision_onto_live_target_unchanged(monkeypatch):
    """Regression pin: the live crossed path stays byte-identical to today
    (focus move, folder spearcon cue, drain to the decision)."""
    _liveness(monkeypatch, dead=set())                       # everyone alive, deterministically
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/bravo")
    _ident(sessions, "B", "/dev/ttysLive")
    sessions.set_os_focus(term_program="Apple_Terminal", tty="/dev/ttysLive")
    assert sessions.workspace() == "B"
    daemon._enqueue("B", "choice", "decide", True)

    daemon.handle_message(_msg("jump_decision", ""))

    assert sessions.foreground() == "B"                     # voice moved (crossed -> focus)
    assert sessions.speaker() == "B"
    assert daemon.voice_state == "flowing"
    assert speaker.cancels == 1
    bq = stream_queue(daemon, "B")
    folder_item = bq.pop_next()
    assert folder_item.text == "bravo."                     # folder spearcon cue, unchanged
    decision_item = bq.pop_next()
    assert decision_item.text == "decide"                   # drained to the decision
