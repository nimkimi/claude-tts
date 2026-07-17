# tests/test_frontier.py
from sonari.queue import SpeechItem
from sonari.sessions import SessionManager
from sonari.daemon import SpeechDaemon
from sonari.protocol import MsgType, PROTOCOL_VERSION
from sonari.config import DEFAULTS


def _cfg():
    c = {k: (v.copy() if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
    c["verbosity"] = "everything"
    return c


class _FakeSpeaker:
    def __init__(self): self._epoch = 0
    def cancel(self): self._epoch += 1
    def cancel_epoch(self): return self._epoch
    def earcon(self, kind): pass


def test_speech_item_forward_defaults_false():
    it = SpeechItem(id=1, session="s", kind="prose", text="x", is_decision=False)
    assert it.forward is False


def test_enqueue_threads_forward_flag():
    sessions = SessionManager(); sessions.set_foreground("s0")
    d = SpeechDaemon(_FakeSpeaker(), sessions, _cfg())
    d._enqueue("s0", "prose", "a", False, forward=True)
    d._enqueue("s0", "prose", "b", False)
    items = list(d._stream("s0").queue._items)
    assert items[0].forward is True and items[1].forward is False


def test_whereami_requeue_preserves_forward():
    sessions = SessionManager(); sessions.set_foreground("s0")
    sessions.register("s0", cwd="/x/s0")
    d = SpeechDaemon(_FakeSpeaker(), sessions, _cfg())
    it = SpeechItem(id=99, session="s0", kind="prose", text="live",
                    is_decision=False, forward=True)
    d._current_item = it                       # in-flight forward readout
    with d._state.transaction():
        d.handle_message({"v": PROTOCOL_VERSION, "type": MsgType.WHERE_AM_I,
                          "session": "s0"})
    requeued = [x for x in d._stream("s0").queue._items if x.text == "live"]
    assert requeued and requeued[0].forward is True


def test_repeat_last_requeue_preserves_forward():
    # playback.py:191 (⌃⌘R) — the barge-in re-queue of the interrupted item
    # must thread forward=cur.forward, same as the ⌃⌘W site above.
    sessions = SessionManager(); sessions.set_foreground("s0")
    sessions.register("s0", cwd="/x/s0")
    d = SpeechDaemon(_FakeSpeaker(), sessions, _cfg())
    d._last_utterance = ("earlier content.", None)   # required or REPEAT_LAST no-ops
    it = SpeechItem(id=98, session="s0", kind="prose", text="mid-flight",
                    is_decision=False, forward=True)
    d._current_item = it                       # in-flight forward readout, barged in
    with d._state.transaction():
        d.handle_message({"v": PROTOCOL_VERSION, "type": MsgType.REPEAT_LAST,
                          "session": "s0"})
    requeued = [x for x in d._stream("s0").queue._items if x.text == "mid-flight"]
    assert requeued and requeued[0].forward is True


def test_chooser_cancel_restore_preserves_forward():
    # chooser.py:118 (chooser cancel/restore) — the captured in-flight item is
    # re-queued when the browse gesture is cancelled; must thread forward=c.forward.
    sessions = SessionManager(); sessions.set_foreground("s0")
    sessions.register("s0", cwd="/x/s0")
    d = SpeechDaemon(_FakeSpeaker(), sessions, _cfg())
    it = SpeechItem(id=97, session="s0", kind="prose", text="captured",
                    is_decision=False, forward=True)
    d._current_item = it                       # in-flight forward readout, captured at open
    with d._state.transaction():
        d.handle_message({"v": PROTOCOL_VERSION, "type": MsgType.CHOOSER_STEP,
                          "session": "s0", "direction": "next"})
    with d._state.transaction():
        d.handle_message({"v": PROTOCOL_VERSION, "type": MsgType.CHOOSER_CANCEL,
                          "session": "s0"})
    requeued = [x for x in d._stream("s0").queue._items if x.text == "captured"]
    assert requeued and requeued[0].forward is True


def test_advance_frontier_monotonic():
    from sonari.session_stream import SessionStream
    st = SessionStream()
    assert st.frontier is None
    st.advance_frontier((1, 0)); assert st.frontier == (1, 0)
    st.advance_frontier((2, 3)); assert st.frontier == (2, 3)
    st.advance_frontier((1, 5)); assert st.frontier == (2, 3)   # never retreats
    st.advance_frontier(None);   assert st.frontier == (2, 3)   # None-safe no-op


def test_frontier_survives_new_prompt_reset():
    from sonari.session_stream import SessionStream
    st = SessionStream(); st.advance_frontier((4, 1))
    st.reset_for_new_prompt()
    assert st.frontier == (4, 1)                # monotonic across turns; only SESSION_END drops it


def test_note_spoken_advances_frontier_only_on_forward_completion():
    sessions = SessionManager(); sessions.set_foreground("s0")
    d = SpeechDaemon(_FakeSpeaker(), sessions, _cfg())
    st = d._stream("s0")
    e = d.history.record("s0", "prose", "hello")     # (msg_id 0, seq 0)
    it = SpeechItem(id=1, session="s0", kind="prose", text="hello",
                    is_decision=False, forward=True)
    d._state._pending_heard[it.id] = e; d._current_item = it
    d.note_spoken(it, completed=True)
    assert e.heard is True and st.frontier == (e.msg_id, e.seq)


def test_browse_replay_flips_heard_but_frontier_stays():
    sessions = SessionManager(); sessions.set_foreground("s0")
    d = SpeechDaemon(_FakeSpeaker(), sessions, _cfg())
    st = d._stream("s0")
    e = d.history.record("s0", "prose", "old")
    it = SpeechItem(id=1, session="s0", kind="prose", text="old",
                    is_decision=False, forward=False)   # browse replay: NOT forward
    d._state._pending_heard[it.id] = e; d._current_item = it
    d.note_spoken(it, completed=True)
    assert e.heard is True                    # heard still flips (nav's other uses)
    assert st.frontier is None                # but the frontier did NOT move (B1)


def test_mid_item_barge_in_leaves_frontier_unchanged():
    sessions = SessionManager(); sessions.set_foreground("s0")
    d = SpeechDaemon(_FakeSpeaker(), sessions, _cfg())
    st = d._stream("s0")
    e = d.history.record("s0", "prose", "cut")
    it = SpeechItem(id=1, session="s0", kind="prose", text="cut",
                    is_decision=False, forward=True)
    d._state._pending_heard[it.id] = e; d._current_item = it
    d.note_spoken(it, completed=False)        # R-8: mid-item cut, not full completion
    assert e.heard is False and st.frontier is None


def test_note_spoken_advances_frontier_on_decision_readout_completion():
    # A decision-kind readout (choice/plan/permission enqueue site, T2 step 5's
    # forward=True) must advance the frontier the same as a prose readout — this
    # was the untested write path the T2 arithmetic gap (+6 claimed / 5 shown)
    # exposed. Mirrors test_note_spoken_advances_frontier_only_on_forward_completion
    # but with a decision kind + is_decision=True, matching decisions.py's real
    # _enqueue(session, "choice", text, True, entry=entry, forward=True) shape.
    sessions = SessionManager(); sessions.set_foreground("s0")
    d = SpeechDaemon(_FakeSpeaker(), sessions, _cfg())
    st = d._stream("s0")
    e = d.history.record("s0", "choice", "pick one")
    it = SpeechItem(id=1, session="s0", kind="choice", text="pick one",
                    is_decision=True, forward=True)
    d._state._pending_heard[it.id] = e; d._current_item = it
    d.note_spoken(it, completed=True)
    assert e.heard is True and st.frontier == (e.msg_id, e.seq)


def _tool_msg(session, tool, summary):
    from sonari.protocol import PROTOCOL_VERSION, MsgType
    return {"v": PROTOCOL_VERSION, "type": MsgType.TOOL, "session": session,
            "tool": tool, "summary": summary}


def test_on_tool_records_history_at_every_verbosity():
    for verb in ("everything", "medium", "quiet"):
        sessions = SessionManager(); sessions.set_foreground("s0")
        c = _cfg(); c["verbosity"] = verb
        d = SpeechDaemon(_FakeSpeaker(), sessions, c)
        with d._state.transaction():
            d.handle_message(_tool_msg("s0", "Grep", "searching for TODO"))
        entries = d.history.entries_for_message("s0", 0)
        assert [(e.kind, e.text) for e in entries] == [("tool", "searching for TODO")], verb


def test_on_tool_announces_forward_at_everything_only():
    sessions = SessionManager(); sessions.set_foreground("s0")
    d = SpeechDaemon(_FakeSpeaker(), sessions, _cfg())      # everything
    with d._state.transaction():
        d.handle_message(_tool_msg("s0", "Grep", "searching for TODO"))
    announce = [x for x in d._stream("s0").queue._items if x.kind == "tool_announce"]
    assert announce and announce[0].forward is True

    sessions2 = SessionManager(); sessions2.set_foreground("s0")
    c = _cfg(); c["verbosity"] = "medium"
    d2 = SpeechDaemon(_FakeSpeaker(), sessions2, c)
    with d2._state.transaction():
        d2.handle_message(_tool_msg("s0", "Grep", "searching for TODO"))
    assert not [x for x in d2._stream("s0").queue._items if x.kind == "tool_announce"]


def test_on_tool_falls_back_to_running_tool_when_no_summary():
    sessions = SessionManager(); sessions.set_foreground("s0")
    d = SpeechDaemon(_FakeSpeaker(), sessions, _cfg())
    with d._state.transaction():
        d.handle_message(_tool_msg("s0", "Bash", ""))
    entries = d.history.entries_for_message("s0", 0)
    assert entries[0].text == "Running Bash."


def test_start_is_quiet_resume_drops_pre_start_pile_keeps_history():
    from sonari.protocol import MsgType, PROTOCOL_VERSION
    sessions = SessionManager(); sessions.set_foreground("s0")
    sessions.register("s0", cwd="/x/s0")
    d = SpeechDaemon(_FakeSpeaker(), sessions, _cfg())
    st = d._stream("s0")
    st.stopped = True                              # stopped, piling behind a frozen frontier
    e1 = d.history.record("s0", "prose", "pile 1")
    d._enqueue("s0", "prose", "pile 1", False, entry=e1, forward=True)
    e2 = d.history.record("s0", "prose", "pile 2")
    d._enqueue("s0", "prose", "pile 2", False, entry=e2, forward=True)
    assert len(st.queue) == 2 and st.frontier is None
    with d._state.transaction():                   # ⌃⌘S-start (Fork-4 asymmetric START)
        d.handle_message({"v": PROTOCOL_VERSION, "type": MsgType.STOP_SESSION,
                          "session": "s0"})
    assert st.stopped is False
    assert [x.text for x in st.queue._items] == ["Resumed."]   # pre-start pile dropped from the queue
    assert st.frontier is None                                 # frontier stayed BEHIND the pile
    entries, _ = d.history.unheard_from_frontier("s0", st.frontier)
    assert [e.text for e in entries] == ["pile 1", "pile 2"]   # pile persists, catch-up-reachable
    assert d._pending_heard == {}                              # markers dropped, no orphans


def test_quiet_resume_also_drops_the_buffered_prose_tail_below_frontier():
    # Whole-branch review Minor 1: at minqueue>1, sub-threshold prose sits in
    # st.prose_buffer (not the queue) while a session is stopped. D2's resume
    # only cleared st.queue — the buffered tail survived, flushed with
    # forward=True on the next turn boundary, and advanced the frontier OVER
    # pre-start content, violating "the pile stays behind the frozen frontier."
    sessions = SessionManager(); sessions.set_foreground("s0")
    sessions.register("s0", cwd="/x/s0")
    d = SpeechDaemon(_FakeSpeaker(), sessions, _cfg())
    d.config["minqueue"] = 3
    st = d._stream("s0")
    st.stopped = True                              # stopped, piling behind a frozen frontier
    with d._state.transaction():
        d.handle_message({"v": PROTOCOL_VERSION, "type": MsgType.PROSE, "session": "s0",
                          "delta": "pre one. pre two. ", "index": 0, "final": False})
    assert len(st.queue) == 0                       # below minqueue=3: buffered, not queued
    with d._state.transaction():                   # ⌃⌘S-start (Fork-4 asymmetric START)
        d.handle_message({"v": PROTOCOL_VERSION, "type": MsgType.STOP_SESSION,
                          "session": "s0"})
    assert st.stopped is False
    assert [x.text for x in st.queue._items] == ["Resumed."]   # pre-start queue dropped
    with d._state.transaction():                    # end-of-turn boundary
        d.handle_message({"v": PROTOCOL_VERSION, "type": MsgType.EARCON,
                          "kind": "turn_done", "session": "s0"})
    # Speak everything now queued (mirrors the speak loop's note_spoken bookkeeping).
    while len(st.queue):
        item = st.queue.pop_next()
        d._current_item = item
        d.note_spoken(item, completed=True)
    assert st.frontier is None                                   # frontier stayed BEHIND the pre-start pile
    entries, _ = d.history.unheard_from_frontier("s0", st.frontier)
    assert [e.text for e in entries] == ["pre one.", "pre two."]  # full pre-start pile, catch-up-reachable


def test_skip_pile_is_a_resolvable_unbound_action():
    from sonari.keymap import ACTION_MESSAGES, resolve_keymap, default_keymap
    assert ACTION_MESSAGES["skip_pile"] == {"type": "skip_pile"}
    assert "skip_pile" not in default_keymap()          # ships UNBOUND — his ear-gate chord
    binding = default_keymap()["where_am_i"]            # a known-good key+mods for this platform
    resolved = resolve_keymap({"skip_pile": binding})
    assert any(r["action"] == "skip_pile" for r in resolved)   # bindable via keymap.json


def test_skip_pile_advances_frontier_and_announces_count():
    from sonari.protocol import MsgType, PROTOCOL_VERSION
    sessions = SessionManager(); sessions.set_foreground("s0")
    sessions.register("s0", cwd="/x/s0")
    d = SpeechDaemon(_FakeSpeaker(), sessions, _cfg())
    st = d._stream("s0")
    for i in range(3):
        e = d.history.record("s0", "prose", "p{0}".format(i)); d.history.end_message("s0")
        d._enqueue("s0", "prose", "p{0}".format(i), False, entry=e, forward=True)
    assert st.frontier is None and len(st.queue) == 3
    with d._state.transaction():
        d.handle_message({"v": PROTOCOL_VERSION, "type": MsgType.SKIP_PILE, "session": "s0"})
    assert st.frontier == d.history.newest_key("s0")     # advanced PAST the pile to live
    assert all(not e.heard for e in d.history.entries_for_message("s0", 2))  # NOT marked heard
    ahead, _ = d.history.unheard_from_frontier("s0", st.frontier)
    assert ahead == []                                   # pile now below the frontier
    assert [x.text for x in st.queue._items] == ["Skipping 3 items in s0."]  # count+folder cue; pile dropped
    assert d._pending_heard == {}


def test_skip_pile_nothing_to_skip_does_not_nag():
    from sonari.protocol import MsgType, PROTOCOL_VERSION
    sessions = SessionManager(); sessions.set_foreground("s0")
    sessions.register("s0", cwd="/x/s0")
    d = SpeechDaemon(_FakeSpeaker(), sessions, _cfg())
    with d._state.transaction():
        d.handle_message({"v": PROTOCOL_VERSION, "type": MsgType.SKIP_PILE, "session": "s0"})
    assert [x.text for x in d._stream("s0").queue._items] == ["Nothing to skip."]


def test_skip_pile_falls_through_to_flowing_speaker_when_workspace_clean():
    # C1' preserved flood remedy: the workspace has NO pile of its own, so
    # skip falls through to the flowing, diverged speaker (the original C1
    # target) instead of doing nothing.
    from sonari.protocol import MsgType, PROTOCOL_VERSION
    sessions = SessionManager(); sessions.set_foreground("ws")
    sessions.register("ws", cwd="/x/ws"); sessions.register("spk", cwd="/x/spk")
    sessions.set_speaker("spk")                          # diverged: speaker != workspace
    d = SpeechDaemon(_FakeSpeaker(), sessions, _cfg())
    d.voice_state = "flowing"
    spk_st = d._stream("spk")
    e = d.history.record("spk", "prose", "flood")
    d._enqueue("spk", "prose", "flood", False, entry=e, forward=True)
    with d._state.transaction():
        d.handle_message({"v": PROTOCOL_VERSION, "type": MsgType.SKIP_PILE, "session": "ws"})
    assert spk_st.frontier == d.history.newest_key("spk")   # the SPEAKER's frontier advanced
    assert d._stream("ws").frontier is None                 # the workspace was NOT touched (it was empty)
    assert sessions.workspace() == "ws"                     # window unmoved
    assert [x.text for x in spk_st.queue._items] == ["Skipping 1 item in spk."]  # singular arm too


def test_skip_pile_workspace_wins_over_diverged_flowing_speaker():
    # THE OWNER'S EAR-PASS REPRO (C1' ruling, 2026-07-17): standing ON a piled
    # session while the voice flows elsewhere used to say "Nothing to skip." —
    # C1 targeted the clean flowing SPEAKER and never looked at the workspace's
    # own pile. C1' is pile-seeking, workspace-first: the workspace wins whenever
    # IT has a pile, regardless of voice_state or where the voice is flowing.
    from sonari.protocol import MsgType, PROTOCOL_VERSION
    sessions = SessionManager(); sessions.set_foreground("b")
    sessions.register("b", cwd="/x/b"); sessions.register("a", cwd="/x/a")
    sessions.set_speaker("a")                             # diverged: voice flows on a, workspace is b
    d = SpeechDaemon(_FakeSpeaker(), sessions, _cfg())
    d.voice_state = "flowing"
    b_st = d._stream("b")
    for i in range(5):
        e = d.history.record("b", "prose", "p{0}".format(i)); d.history.end_message("b")
        d._enqueue("b", "prose", "p{0}".format(i), False, entry=e, forward=True)
    with d._state.transaction():
        d.handle_message({"v": PROTOCOL_VERSION, "type": MsgType.SKIP_PILE, "session": "b"})
    assert b_st.frontier == d.history.newest_key("b")        # workspace's OWN pile skipped
    assert d._stream("a").frontier is None                   # the flowing speaker was NOT touched
    assert [x.text for x in b_st.queue._items] == ["Skipping 5 items in b."]


def test_skip_pile_both_piled_workspace_first_then_speaker_on_second_press():
    # Both diverged sessions have a pile: the workspace wins the first press;
    # a second press then drains the (still-piled) flowing speaker — two
    # presses drain both, in that order.
    from sonari.protocol import MsgType, PROTOCOL_VERSION
    sessions = SessionManager(); sessions.set_foreground("ws")
    sessions.register("ws", cwd="/x/ws"); sessions.register("spk", cwd="/x/spk")
    sessions.set_speaker("spk")
    d = SpeechDaemon(_FakeSpeaker(), sessions, _cfg())
    d.voice_state = "flowing"
    ws_st = d._stream("ws"); spk_st = d._stream("spk")
    for i in range(2):
        e = d.history.record("ws", "prose", "w{0}".format(i)); d.history.end_message("ws")
        d._enqueue("ws", "prose", "w{0}".format(i), False, entry=e, forward=True)
    for i in range(3):
        e = d.history.record("spk", "prose", "s{0}".format(i)); d.history.end_message("spk")
        d._enqueue("spk", "prose", "s{0}".format(i), False, entry=e, forward=True)
    with d._state.transaction():
        d.handle_message({"v": PROTOCOL_VERSION, "type": MsgType.SKIP_PILE, "session": "ws"})
    assert ws_st.frontier == d.history.newest_key("ws")      # first press: workspace pile cleared
    assert spk_st.frontier is None                           # speaker pile untouched so far
    assert [x.text for x in ws_st.queue._items] == ["Skipping 2 items in ws."]
    with d._state.transaction():
        d.handle_message({"v": PROTOCOL_VERSION, "type": MsgType.SKIP_PILE, "session": "ws"})
    assert spk_st.frontier == d.history.newest_key("spk")    # second press: speaker pile cleared
    assert [x.text for x in spk_st.queue._items] == ["Skipping 3 items in spk."]


def test_skip_pile_non_flowing_gate_blocks_fallthrough_to_speaker():
    # voice_state isn't "flowing" (quiet-hold/stopped-all): even though the
    # speaker has a pile, the fall-through must NOT fire — the workspace being
    # clean is not itself license to reach across to the speaker.
    from sonari.protocol import MsgType, PROTOCOL_VERSION
    sessions = SessionManager(); sessions.set_foreground("ws")
    sessions.register("ws", cwd="/x/ws"); sessions.register("spk", cwd="/x/spk")
    sessions.set_speaker("spk")
    d = SpeechDaemon(_FakeSpeaker(), sessions, _cfg())
    d.voice_state = "quiet-hold"                              # NOT flowing
    e = d.history.record("spk", "prose", "flood"); d.history.end_message("spk")
    d._enqueue("spk", "prose", "flood", False, entry=e, forward=True)
    with d._state.transaction():
        d.handle_message({"v": PROTOCOL_VERSION, "type": MsgType.SKIP_PILE, "session": "ws"})
    assert [x.text for x in d._stream("ws").queue._items] == ["Nothing to skip."]
    assert d._stream("spk").frontier is None                 # speaker pile left untouched


def test_skip_pile_singular_item_uses_singular_noun():
    # T6 review gap: the workspace-first primary path with exactly one item
    # must say "item", not "items".
    from sonari.protocol import MsgType, PROTOCOL_VERSION
    sessions = SessionManager(); sessions.set_foreground("s0")
    sessions.register("s0", cwd="/x/s0")
    d = SpeechDaemon(_FakeSpeaker(), sessions, _cfg())
    e = d.history.record("s0", "prose", "only one"); d.history.end_message("s0")
    d._enqueue("s0", "prose", "only one", False, entry=e, forward=True)
    with d._state.transaction():
        d.handle_message({"v": PROTOCOL_VERSION, "type": MsgType.SKIP_PILE, "session": "s0"})
    assert [x.text for x in d._stream("s0").queue._items] == ["Skipping 1 item in s0."]
