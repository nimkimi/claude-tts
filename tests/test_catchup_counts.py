from tests.daemon_helpers import make_daemon


def test_14_vs_2_same_pile_decomposed_by_w():
    # The owner's 14-vs-2: skip/catch-up announce the WHOLE pile; ⌃⌘W's u must
    # decompose that SAME pile, not a current-turn floor. A two-turn pile of 5 on
    # a background session must read "5 unheard", not the old current-turn "3".
    daemon, queue, speaker, sessions, config = make_daemon(verbosity="quiet")
    sessions.set_foreground("fg", cwd="/x/fg")       # the converged speaker
    sessions.register("bg", cwd="/x/bg")
    daemon.history.record("bg", "prose", "t0 a.")
    daemon.history.record("bg", "prose", "t0 b.")
    daemon.history.start_turn("bg")                  # new prompt -> turn 1
    daemon.history.record("bg", "prose", "t1 a.")
    daemon.history.record("bg", "prose", "t1 b.")
    daemon.history.record("bg", "prose", "t1 c.")
    pile, _ = daemon.history.unheard_from_frontier("bg", daemon._stream("bg").frontier)
    assert len(pile) == 5                            # the pile skip/catch-up would announce
    daemon.handle_message({"v": 1, "type": "where_am_i", "session": "fg"})
    daemon._speak_loop_once()
    assert "5 unheard" in speaker.spoken[-1]         # bg's Also-map entry, same pile


def test_u_floors_at_zero_when_queue_exceeds_pile():
    daemon, queue, speaker, sessions, config = make_daemon(verbosity="quiet")
    sessions.set_foreground("fg", cwd="/x/fg")
    sessions.register("bg", cwd="/x/bg")
    e = daemon.history.record("bg", "prose", "only one.")
    # frontier past the single entry -> pile empty; a queued item makes k=1 > pile
    daemon._stream("bg").advance_frontier((e.msg_id, e.seq))
    daemon._enqueue("bg", "prose", "queued.", False)
    daemon.handle_message({"v": 1, "type": "where_am_i", "session": "fg"})
    daemon._speak_loop_once()
    # Floored to 0 -> the unheard clause is suppressed entirely (never "0 unheard",
    # never "-1 unheard"). Strict: the word must not appear at all in this fixture.
    assert "unheard" not in speaker.spoken[-1]
