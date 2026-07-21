from sonari.protocol import MsgType, PROTOCOL_VERSION
from sonari.sessions import Identity
from tests.daemon_helpers import make_daemon, stream_queue
from tests.test_daemon_focus_follow import RecordingRaiseService


def _msg(mtype, session, **extra):
    d = {"v": PROTOCOL_VERSION, "type": mtype, "session": session}
    d.update(extra)
    return d


def test_choice_enqueues_when_foreground():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    daemon.handle_message(_msg(MsgType.CHOICE, "fg", questions=[
        {"question": "Pick a color", "options": [{"label": "Red"}, {"label": "Blue"}]},
    ]))
    # A content message NEVER earcons; the alert is a separate EARCON message.
    assert speaker.earcons == []
    assert len(queue) == 1
    item = queue.pop_next()
    assert item.kind == "choice"
    assert item.is_decision is True
    assert "Pick a color" in item.text
    assert "Option 1: Red." in item.text
    assert "Option 2: Blue." in item.text


def test_plan_enqueues_when_foreground():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    daemon.handle_message(_msg(MsgType.PLAN, "fg", text="Step one then step two."))
    assert speaker.earcons == []
    assert len(queue) == 1
    item = queue.pop_next()
    assert item.kind == "plan"
    assert item.is_decision is True
    assert "Step one then step two." in item.text


def test_permission_enqueues_when_foreground():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    daemon.handle_message(_msg(MsgType.PERMISSION, "fg", action="run rm -rf"))
    assert speaker.earcons == []
    assert len(queue) == 1
    item = queue.pop_next()
    assert item.kind == "permission"
    assert item.is_decision is True
    assert "run rm -rf" in item.text


def test_decision_content_not_enqueued_when_not_foreground():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    daemon.handle_message(_msg(MsgType.CHOICE, "other", questions=[{"question": "Q"}]))
    # Content messages never earcon (the EARCON message does), and a
    # non-foreground decision's spoken text is not enqueued.
    assert speaker.earcons == []
    assert len(queue) == 0


def test_tool_announce_enqueues_only_when_verbosity_everything():
    daemon, queue, speaker, sessions, config = make_daemon(verbosity="everything", foreground="fg")
    daemon.handle_message(_msg(MsgType.TOOL, "fg", tool="Bash", summary="run tests"))
    assert len(queue) == 1
    item = queue.pop_next()
    assert item.kind == "tool_announce"
    assert item.is_decision is False
    assert "run tests" in item.text


def test_tool_announce_dropped_when_verbosity_medium():
    daemon, queue, speaker, sessions, config = make_daemon(verbosity="medium", foreground="fg")
    daemon.handle_message(_msg(MsgType.TOOL, "fg", tool="Bash", summary="run tests"))
    assert len(queue) == 0


def test_tool_announce_dropped_when_verbosity_quiet():
    daemon, queue, speaker, sessions, config = make_daemon(verbosity="quiet", foreground="fg")
    daemon.handle_message(_msg(MsgType.TOOL, "fg", tool="Bash", summary="run tests"))
    assert len(queue) == 0


def test_tool_announce_dropped_when_not_foreground():
    daemon, queue, speaker, sessions, config = make_daemon(verbosity="everything", foreground="fg")
    daemon.handle_message(_msg(MsgType.TOOL, "other", tool="Bash", summary="run tests"))
    assert len(queue) == 0


def test_decision_enqueued_at_everything():
    for mtype, kwargs, kind in [
        (MsgType.CHOICE, {"questions": [{"question": "Q?"}]}, "choice"),
        (MsgType.PLAN, {"text": "Do X."}, "plan"),
        (MsgType.PERMISSION, {"action": "rm -rf"}, "permission"),
    ]:
        daemon, queue, speaker, sessions, config = make_daemon(verbosity="everything", foreground="fg")
        daemon.handle_message(_msg(mtype, "fg", **kwargs))
        assert len(queue) == 1, f"{kind} not enqueued at everything"
        assert queue.pop_next().kind == kind


def test_decision_enqueued_at_medium():
    for mtype, kwargs, kind in [
        (MsgType.CHOICE, {"questions": [{"question": "Q?"}]}, "choice"),
        (MsgType.PLAN, {"text": "Do X."}, "plan"),
        (MsgType.PERMISSION, {"action": "rm -rf"}, "permission"),
    ]:
        daemon, queue, speaker, sessions, config = make_daemon(verbosity="medium", foreground="fg")
        daemon.handle_message(_msg(mtype, "fg", **kwargs))
        assert len(queue) == 1, f"{kind} not enqueued at medium"
        assert queue.pop_next().kind == kind


def test_decision_enqueued_at_quiet():
    for mtype, kwargs, kind in [
        (MsgType.CHOICE, {"questions": [{"question": "Q?"}]}, "choice"),
        (MsgType.PLAN, {"text": "Do X."}, "plan"),
        (MsgType.PERMISSION, {"action": "rm -rf"}, "permission"),
    ]:
        daemon, queue, speaker, sessions, config = make_daemon(verbosity="quiet", foreground="fg")
        daemon.handle_message(_msg(mtype, "fg", **kwargs))
        assert len(queue) == 1, f"{kind} not enqueued at quiet"
        assert queue.pop_next().kind == kind


def test_decision_for_foreground_enqueues_to_its_stream():
    """The flip: a question/permission for the FOREGROUND session always enqueues
    into its own stream (no voice-claim arbitration). Was
    test_decision_for_foreground_claims_voice_from_background_owner."""
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    daemon.handle_message(_msg(MsgType.CHOICE, "A", questions=[
        {"question": "Pick", "options": [{"label": "Red"}]},
    ]))
    assert len(queue) == 1                      # queue == A's (foreground) stream
    item = queue.pop_next()
    assert item.kind == "choice" and item.session == "A"


def test_decision_for_background_session_enqueues_to_its_own_stream():
    """The flip: a decision for a background session enqueues into THAT session's
    own stream (not the foreground's), instead of being dropped. Was
    test_decision_for_current_owner_still_enqueues_even_if_backgrounded."""
    daemon, queue, speaker, sessions, config = make_daemon(foreground="B")
    daemon.handle_message(_msg(MsgType.PERMISSION, "A", action="run X"))
    assert len(stream_queue(daemon, "A")) == 1
    assert stream_queue(daemon, "A").pop_next().session == "A"


def test_jump_decision_drops_pending_and_marks_current_heard():
    """M6: JUMP_DECISION discards queued non-decision items before the decision.
    Those items' _pending_heard entries must be dropped (no leak) and the cancelled
    current item must be marked heard so they don't linger in unheard()."""
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    # A current item being spoken, with a heard-marker entry.
    cur_entry = daemon.history.record("fg", "prose", "current")
    from sonari.queue import SpeechItem
    cur = SpeechItem(id=99, session="fg", kind="prose", text="current", is_decision=False)
    daemon._current_item = cur
    daemon._pending_heard[cur.id] = cur_entry
    # Two queued prose items (with heard-markers) ahead of a decision.
    e1 = daemon.history.record("fg", "prose", "p1")
    e2 = daemon.history.record("fg", "prose", "p2")
    daemon._enqueue("fg", "prose", "p1", False, entry=e1)
    daemon._enqueue("fg", "prose", "p2", False, entry=e2)
    daemon._enqueue("fg", "choice", "decide", True)
    prose_ids = [it.id for it in list(queue._items) if not it.is_decision]
    assert all(pid in daemon._pending_heard for pid in prose_ids)

    daemon.handle_message({"type": "jump_decision", "session": "fg"})

    assert speaker.cancels == 1
    assert cur_entry.heard is True                 # cancelled current marked heard
    # the dropped prose items' pending-heard entries are gone (no leak)
    assert all(pid not in daemon._pending_heard for pid in prose_ids)
    # the decision remains at the front
    assert queue.pop_next().text == "decide"


def test_bare_earcon_message_becomes_your_turn_for_flowing_speaker():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    daemon.handle_message(_msg(MsgType.EARCON, "fg", kind="turn_done"))
    assert speaker.earcons == ["your_turn"]               # D2 §6.1 solo boundary tone
    assert len(queue) == 0                                # a transient queues nothing

def test_bare_earcon_message_dings_for_non_speaker():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    sessions.register("bg", cwd="/x/bg")
    daemon.handle_message(_msg(MsgType.EARCON, "bg", kind="turn_done"))
    assert speaker.earcons == ["turn_done"]


def test_jump_decision_targets_the_focused_session_not_foreground():
    # ⌃⌘D acts on the OS-focused session (like on_nav), not the voice's foreground —
    # so a decision-jump fired while looking at another terminal jumps THAT session AND
    # moves the voice to it (crossed → focus()).
    from sonari.sessions import Identity
    from tests.daemon_helpers import stream_queue
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("B")
    sessions.set_identity("B", Identity(term_program="Apple_Terminal", tty="/dev/ttys9"))
    sessions.set_os_focus(term_program="Apple_Terminal", tty="/dev/ttys9")
    assert sessions.focused_session() == "B"          # B is OS-focused; A owns the voice
    daemon._enqueue("B", "prose", "skip me", False)
    daemon._enqueue("B", "choice", "decide now", True)
    daemon.handle_message({"type": "jump_decision"})
    assert sessions.foreground() == "B"               # voice MOVED to B (crossed→focus)
    assert stream_queue(daemon, "B").pop_next().text == "decide now"   # B jumped, not A
    assert speaker.cancels == 1


def test_jump_decision_crossed_with_folder_enqueues_folder_cue():
    # When the voice crosses to the focused session AND that session has a folder,
    # a folder-name cue is enqueued at_front (after jump_to_decision so it plays first).
    from sonari.sessions import Identity
    from tests.daemon_helpers import stream_queue
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/bravo")
    sessions.set_identity("B", Identity(term_program="Apple_Terminal", tty="/dev/ttys10"))
    sessions.set_os_focus(term_program="Apple_Terminal", tty="/dev/ttys10")
    assert sessions.focused_session() == "B"
    assert sessions.folder("B") == "bravo"        # _record stores basename only
    daemon._enqueue("B", "prose", "skip me", False)
    daemon._enqueue("B", "choice", "decide later", True)
    daemon.handle_message({"type": "jump_decision"})
    assert sessions.foreground() == "B"               # voice moved
    assert speaker.cancels == 1
    bq = stream_queue(daemon, "B")
    # Queue should be: folder cue at front, then the decision
    folder_item = bq.pop_next()
    assert folder_item.text == "bravo."
    decision_item = bq.pop_next()
    assert decision_item.text == "decide later"


def test_answer_targets_workspace():
    # ⌃⌘⏎/⌃⌘⎋ must answer the WORKSPACE session (OS-focused or foreground),
    # not the literal foreground when a different terminal is OS-focused.
    # B is OS-focused (workspace), A owns the voice (foreground). The answer
    # must resolve B's pending decision, not A's.
    import threading
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    sessions.set_identity("B", Identity(term_program="Apple_Terminal", tty="/dev/ttysB"))
    sessions.set_os_focus(term_program="Apple_Terminal", tty="/dev/ttysB")  # workspace == B
    assert sessions.workspace() == "B"
    assert sessions.foreground() == "A"
    daemon._pending_decisions["B"] = {"event": threading.Event(), "behavior": None}
    daemon.handle_message(_msg(MsgType.ANSWER_PERMISSION, "", behavior="allow"))
    assert daemon._pending_decisions["B"]["behavior"] == "allow"   # answered B (workspace), not A


def test_jump_decision_raises_target_window():
    # ⌃⌘D must raise the target terminal window (R5/R9 — C2 fix), mirroring
    # the same raise machinery on_jump_waiting and the chooser commit use.
    # B is the workspace (OS-focused) and owns the pending decision; we expect
    # the raise service to fire exactly once, targeting B's identity.
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    rs = RecordingRaiseService(will=True)
    daemon.raise_service = rs
    sessions.register("B", cwd="/x/B")
    sessions.set_identity("B", Identity(term_program="Apple_Terminal", tty="/dev/ttysB"))
    sessions.set_os_focus(term_program="Apple_Terminal", tty="/dev/ttysB")  # workspace/target == B
    daemon._enqueue("B", "permission", "Allow X?", True)  # pending decision lands on B
    daemon.handle_message(_msg(MsgType.JUMP_DECISION, ""))
    assert len(rs.attempts) == 1                           # jump_decision attempted a raise
    ident, gen = rs.attempts[0]
    assert ident.tty == "/dev/ttysB" and gen >= 1
