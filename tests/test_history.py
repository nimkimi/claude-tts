from sonari.history import SessionHistory


def test_record_and_last_message_groups_by_message_boundary():
    h = SessionHistory()
    h.record("s1", "prose", "First sentence.")
    h.record("s1", "prose", "Second sentence.")
    h.end_message("s1")
    h.record("s1", "prose", "Next message.")
    assert [e.text for e in h.last_message("s1")] == ["Next message."]


def test_last_message_returns_whole_group():
    h = SessionHistory()
    h.record("s1", "prose", "A.")
    h.record("s1", "prose", "B.")
    h.end_message("s1")
    assert [e.text for e in h.last_message("s1")] == ["A.", "B."]


def test_last_message_empty_session():
    h = SessionHistory()
    assert h.last_message("nope") == []


def test_unheard_until_marked():
    h = SessionHistory()
    e1 = h.record("s1", "prose", "A.")
    e2 = h.record("s1", "prose", "B.")
    assert [e.text for e in h.unheard("s1")] == ["A.", "B."]
    e1.heard = True
    assert [e.text for e in h.unheard("s1")] == ["B."]
    e2.heard = True
    assert h.unheard("s1") == []


def test_reset_drops_session():
    h = SessionHistory()
    h.record("s1", "prose", "A.")
    h.reset("s1")
    assert h.last_message("s1") == []
    assert h.unheard("s1") == []


def test_rolling_cap_bounds_memory():
    h = SessionHistory(cap=3)
    for i in range(10):
        h.record("s1", "prose", "S{0}.".format(i))
    texts = [e.text for e in h.unheard("s1")]
    assert texts == ["S7.", "S8.", "S9."]


def test_eviction_excludes_truncated_head_group_from_navigation():
    # #8: when the rolling cap evicts the HEAD of an older message group, nav must
    # not replay the surviving fragment — message_ids excludes the truncated group.
    h = SessionHistory(cap=3)
    h.record("s", "prose", "a1")
    h.record("s", "prose", "a2")        # group 0: a1, a2
    h.end_message("s")
    h.record("s", "prose", "b1")
    h.record("s", "prose", "b2")        # group 1: b1, b2 -> evicts a1 (group 0 head)
    assert h.message_ids("s") == [1]    # truncated group 0 excluded
    assert [e.text for e in h.entries_for_message("s", 1)] == ["b1", "b2"]


def test_complete_groups_remain_navigable():
    # A group whose head is still present stays navigable (regression guard).
    h = SessionHistory(cap=10)
    h.record("s", "prose", "a1"); h.end_message("s")
    h.record("s", "prose", "b1"); h.end_message("s")
    assert h.message_ids("s") == [0, 1]


def test_nth_last_message_walks_back_through_groups():
    from sonari.history import SessionHistory
    h = SessionHistory()
    # group 0: two prose sentences
    h.record("s", "prose", "a1"); h.record("s", "prose", "a2"); h.end_message("s")
    # group 1: one choice
    h.record("s", "choice", "b1"); h.end_message("s")
    # group 2: current (open) prose
    h.record("s", "prose", "c1")
    assert [e.text for e in h.nth_last_message("s", 0)] == ["c1"]       # current
    assert [e.text for e in h.nth_last_message("s", 1)] == ["b1"]       # previous
    assert [e.text for e in h.nth_last_message("s", 2)] == ["a1", "a2"] # two back
    assert h.nth_last_message("s", 3) == []                            # out of range
    assert h.nth_last_message("s", -1) == []
    assert h.nth_last_message("missing", 0) == []


def test_message_ids_and_entries_for_message():
    from sonari.history import SessionHistory
    h = SessionHistory()
    h.record("s", "prose", "a1"); h.record("s", "prose", "a2"); h.end_message("s")
    h.record("s", "choice", "b1"); h.end_message("s")
    h.record("s", "prose", "c1")
    ids = h.message_ids("s")
    assert ids == [0, 1, 2]                                   # oldest -> newest
    assert [e.text for e in h.entries_for_message("s", 0)] == ["a1", "a2"]
    assert [e.text for e in h.entries_for_message("s", 1)] == ["b1"]
    assert h.message_ids("missing") == []


def test_record_stamps_current_turn_id():
    h = SessionHistory()
    e0 = h.record("s", "prose", "a")
    assert e0.turn_id == 0                      # default turn before any start_turn
    h.start_turn("s")
    e1 = h.record("s", "prose", "b")
    assert e1.turn_id == 1                      # new turn after start_turn
    assert e0.turn_id == 0                      # prior entry unchanged


def test_start_turn_starts_a_fresh_message_group():
    h = SessionHistory()
    h.record("s", "prose", "a1"); h.end_message("s")
    h.record("s", "prose", "a2")               # an OPEN group in turn 0 (no end_message)
    h.start_turn("s")
    e = h.record("s", "prose", "b1")           # first entry of turn 1
    assert e.seq == 0                           # fresh group, did not continue a2's group
    assert e.turn_id == 1
    assert [x.text for x in h.last_message("s")] == ["b1"]   # its own group


def test_start_turn_keeps_prior_entries_unlike_reset():
    h = SessionHistory()
    h.record("s", "prose", "old")
    h.start_turn("s")                          # opens turn 1, KEEPS "old"
    assert [e.text for e in h.last_message("s")] == ["old"]   # prior turn persists
    h.reset("s")                               # SESSION_END semantics: forget everything
    assert h.last_message("s") == []
    assert h.record("s", "prose", "fresh").turn_id == 0       # reset cleared _turn_id


def test_message_ids_scoped_to_current_turn():
    h = SessionHistory()
    h.record("s", "prose", "t0a"); h.end_message("s")        # turn 0, msg group
    h.record("s", "prose", "t0b"); h.end_message("s")        # turn 0, msg group
    h.start_turn("s")                                        # -> turn 1
    h.record("s", "prose", "t1a")                            # turn 1, msg group
    ids = h.message_ids("s")
    assert len(ids) == 1                                     # only the current (turn 1) group
    # the current-turn id resolves to the turn-1 entry, not a turn-0 one:
    assert [e.text for e in h.entries_for_message("s", ids[0])] == ["t1a"]


def test_entries_for_message_still_reaches_prior_turns():
    # Stage 5 replays past turns via explicit msg_id -> entries_for_message must NOT
    # be turn-scoped (only message_ids/unheard are).
    h = SessionHistory()
    h.record("s", "prose", "old"); h.end_message("s")
    old_id = h.message_ids("s")[0]                           # captured while turn 0 is current
    h.start_turn("s")
    h.record("s", "prose", "new")
    assert [e.text for e in h.entries_for_message("s", old_id)] == ["old"]   # still reachable


def test_unheard_bounded_to_current_turn():
    # §7: unheard stays bounded to the live turn even though the transcript persists.
    h = SessionHistory()
    h.record("s", "prose", "t0")                            # turn 0, never heard
    h.start_turn("s")
    h.record("s", "prose", "t1")                            # turn 1, never heard
    assert [e.text for e in h.unheard("s")] == ["t1"]       # the prior-turn unheard is excluded


def test_empty_current_turn_has_no_nav_or_unheard_but_persists():
    # snap-to-live-edge: after opening a turn with no entries yet, the current-turn
    # views are empty (nothing to navigate / nothing unheard), but the prior turn
    # is retained (persistence).
    h = SessionHistory()
    h.record("s", "prose", "kept")
    h.start_turn("s")                                       # turn 1 is empty
    assert h.message_ids("s") == []
    assert h.unheard("s") == []
    assert [e.text for e in h.last_message("s")] == ["kept"]   # not wiped


def test_cap_spans_whole_session_evicting_oldest_turns():
    # Stage 4: the rolling cap now bounds the WHOLE-session transcript (it was
    # effectively per-turn when history reset each prompt). Oldest entries (oldest
    # turns) evict first; the current turn stays intact and navigable.
    h = SessionHistory(cap=3)
    h.start_turn("s")                                  # turn 1
    h.record("s", "prose", "t1"); h.end_message("s")
    h.start_turn("s")                                  # turn 2
    h.record("s", "prose", "t2a"); h.end_message("s")
    h.record("s", "prose", "t2b"); h.end_message("s")
    h.record("s", "prose", "t2c")                      # 4th entry -> evicts "t1"
    ids = h.message_ids("s")
    assert len(ids) == 3                               # current turn (t2a/t2b/t2c) intact
    # the evicted oldest turn is gone from the transcript entirely
    assert all(e.text != "t1" for e in h._entries["s"])


def test_message_ids_in_turn_returns_that_turns_groups():
    h = SessionHistory()
    h.record("s", "prose", "t0a"); h.end_message("s")
    h.record("s", "prose", "t0b"); h.end_message("s")
    h.start_turn("s")                                       # -> turn 1
    h.record("s", "prose", "t1a")
    t0 = h.message_ids_in_turn("s", 0)
    t1 = h.message_ids_in_turn("s", 1)
    assert len(t0) == 2 and len(t1) == 1
    assert [e.text for e in h.entries_for_message("s", t1[0])] == ["t1a"]
    assert h.message_ids_in_turn("s", 99) == []             # no such turn


def test_message_ids_delegates_to_current_turn():
    h = SessionHistory()
    h.record("s", "prose", "t0a"); h.end_message("s")
    h.start_turn("s")
    h.record("s", "prose", "t1a")
    # message_ids == message_ids of the live turn (regression: Stage 4 behavior kept)
    assert h.message_ids("s") == h.message_ids_in_turn("s", 1)


def test_turn_ids_lists_navigable_turns_oldest_first():
    h = SessionHistory()
    h.record("s", "prose", "a"); h.end_message("s")         # turn 0
    h.start_turn("s"); h.record("s", "prose", "b"); h.end_message("s")   # turn 1
    h.start_turn("s"); h.record("s", "prose", "c")          # turn 2
    assert h.turn_ids("s") == [0, 1, 2]
    assert h.turn_ids("missing") == []


def test_turn_ids_excludes_evicted_and_truncated_turns():
    # cap small enough that turn 0's group HEAD evicts -> turn 0 becomes a fragment
    # (no seq==0 head present) -> not navigable -> excluded from turn_ids.
    h = SessionHistory(cap=3)
    h.record("s", "prose", "a1"); h.record("s", "prose", "a2")
    h.record("s", "prose", "a3")                            # turn 0: one group, 3 entries
    h.start_turn("s")                                       # turn 1 (bumps msg_id -> fresh group)
    h.record("s", "prose", "b1")                            # evicts a1 (turn 0 head)
    assert h.message_ids_in_turn("s", 0) == []              # turn 0 truncated
    assert h.turn_ids("s") == [1]                           # turn 0 excluded


def test_record_stamps_with_injected_monotonic_clock():
    """W2/E1: stamps come from the injected clock, non-decreasing (spec §3)."""
    ticks = iter([10.0, 11.5, 11.5, 12.0])
    h = SessionHistory(cap=10, clock=lambda: next(ticks))
    e1 = h.record("s", "prose", "a")
    e2 = h.record("s", "prose", "b")
    e3 = h.record("s", "prose", "c")
    assert (e1.stamp, e2.stamp, e3.stamp) == (10.0, 11.5, 11.5)
    assert e1.stamp <= e2.stamp <= e3.stamp


def test_default_clock_is_monotonic_and_bounded():
    """Default clock = time.monotonic (ratified: monotonic, NOT time.time)."""
    import time as _t
    h = SessionHistory(cap=4)
    lo = _t.monotonic()
    e1 = h.record("s", "prose", "a")
    e2 = h.record("s", "prose", "b")
    hi = _t.monotonic()
    assert lo <= e1.stamp <= e2.stamp <= hi


def test_unheard_age_reports_oldest_unheard_entry_age():
    """v2 grammar: the 'stale' word reads the OLDEST unheard entry's age."""
    ticks = [100.0]
    h = SessionHistory(cap=10, clock=lambda: ticks[0])
    h.record("s", "prose", "old.")
    ticks[0] = 150.0
    h.record("s", "prose", "newer.")
    ticks[0] = 1100.0
    assert h.unheard_age("s") == 1000.0            # anchored to the oldest


def test_unheard_age_is_none_when_nothing_unheard():
    h = SessionHistory(cap=10)
    assert h.unheard_age("s") is None


def test_unheard_from_frontier_spans_turns_keyed_on_msgid_seq():
    from sonari.history import SessionHistory
    h = SessionHistory()
    h.record("s", "prose", "t1a"); h.end_message("s")   # (0,0)
    h.record("s", "prose", "t1b")                         # (1,0)
    h.start_turn("s")                                     # new turn: msg_id bumps
    h.record("s", "prose", "t2a")                         # (2,0)
    entries, aged = h.unheard_from_frontier("s", (0, 0))
    assert [e.text for e in entries] == ["t1b", "t2a"] and aged is False   # crosses turns
    entries, aged = h.unheard_from_frontier("s", None)
    assert [e.text for e in entries] == ["t1a", "t1b", "t2a"] and aged is False


def test_unheard_from_frontier_ignores_heard_flag():
    from sonari.history import SessionHistory
    h = SessionHistory()
    h.record("s", "prose", "a"); h.end_message("s")       # (0,0)
    b = h.record("s", "prose", "b")                        # (1,0)
    b.heard = True                                         # browse-replayed ABOVE the frontier
    entries, _ = h.unheard_from_frontier("s", (0, 0))
    assert [e.text for e in entries] == ["b"]              # heard=True yet still returned (B1 read side)


def test_unheard_from_frontier_aged_out_fail_loud():
    from sonari.history import SessionHistory
    h = SessionHistory(cap=3)                              # tiny window forces eviction
    for i in range(5):
        h.record("s", "prose", "m{0}".format(i)); h.end_message("s")
    entries, aged = h.unheard_from_frontier("s", (0, 0))   # frontier behind the evicted head
    assert aged is True
    assert entries[0].text == "m2"                         # resumes at oldest SURVIVING, never mid-pile


def test_newest_key_is_live_edge():
    from sonari.history import SessionHistory
    h = SessionHistory()
    assert h.newest_key("s") is None
    h.record("s", "prose", "a"); h.end_message("s")
    last = h.record("s", "prose", "b")
    assert h.newest_key("s") == (last.msg_id, last.seq)


def test_unheard_stays_turn_scoped_for_shipped_consumers():
    from sonari.history import SessionHistory
    h = SessionHistory()
    h.record("s", "prose", "old"); h.start_turn("s")
    h.record("s", "prose", "new")
    assert [e.text for e in h.unheard("s")] == ["new"]     # unchanged: current-turn floor
