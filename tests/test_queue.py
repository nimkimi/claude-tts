from sonari.queue import SpeechItem, SpeechQueue


def _item(id, session="s1", kind="prose", text="t", is_decision=False):
    return SpeechItem(id=id, session=session, kind=kind, text=text, is_decision=is_decision)


def test_enqueue_then_pop_next_is_fifo():
    q = SpeechQueue()
    q.enqueue(_item(1, text="first"))
    q.enqueue(_item(2, text="second"))
    q.enqueue(_item(3, text="third"))
    assert q.pop_next().text == "first"
    assert q.pop_next().text == "second"
    assert q.pop_next().text == "third"


def test_pop_next_on_empty_returns_none():
    q = SpeechQueue()
    assert q.pop_next() is None


def test_pop_pause_exempt_returns_first_exempt_skipping_non_exempt():
    # The interrupted utterance (re-queued at the front on pause) is NOT exempt, so
    # the pause confirmation must be found past it — pop_pause_exempt scans.
    q = SpeechQueue()
    q.enqueue(_item(1, text="interrupted"))                      # non-exempt, at front
    exempt = SpeechItem(id=2, session="s1", kind="prose", text="Paused.",
                        is_decision=False, pause_exempt=True)
    q.enqueue(exempt)
    got = q.pop_pause_exempt()
    assert got is exempt
    assert len(q) == 1 and q.pop_next().text == "interrupted"    # other item untouched


def test_pop_pause_exempt_returns_none_when_no_exempt_item():
    q = SpeechQueue()
    q.enqueue(_item(1, text="a"))
    assert q.pop_pause_exempt() is None
    assert len(q) == 1                                           # nothing removed


def test_len_tracks_pending_items():
    q = SpeechQueue()
    assert len(q) == 0
    q.enqueue(_item(1))
    q.enqueue(_item(2))
    assert len(q) == 2
    q.pop_next()
    assert len(q) == 1


def test_jump_to_decision_drops_leading_non_decision_keeps_decision_at_front():
    q = SpeechQueue()
    q.enqueue(_item(1, kind="prose", text="p1", is_decision=False))
    q.enqueue(_item(2, kind="prose", text="p2", is_decision=False))
    q.enqueue(_item(3, kind="choice", text="decide", is_decision=True))
    q.enqueue(_item(4, kind="prose", text="after", is_decision=False))
    dropped = q.jump_to_decision()
    assert [i.text for i in dropped] == ["p1", "p2"]   # returns what it discarded
    assert len(q) == 2
    first = q.pop_next()
    assert first.text == "decide"
    assert first.is_decision is True
    assert q.pop_next().text == "after"


def test_jump_to_decision_with_no_decision_empties_queue():
    q = SpeechQueue()
    q.enqueue(_item(1, is_decision=False))
    q.enqueue(_item(2, is_decision=False))
    dropped = q.jump_to_decision()
    assert len(dropped) == 2
    assert len(q) == 0
    assert q.pop_next() is None


def test_jump_to_decision_on_empty_is_noop():
    q = SpeechQueue()
    q.jump_to_decision()
    assert len(q) == 0


def test_clear_empties_queue():
    q = SpeechQueue()
    q.enqueue(_item(1))
    q.enqueue(_item(2))
    q.clear()
    assert len(q) == 0
    assert q.pop_next() is None


def test_clear_returns_dropped_items():
    from sonari.queue import SpeechQueue, SpeechItem
    q = SpeechQueue()
    q.enqueue(SpeechItem(1, "s", "prose", "A.", False))
    q.enqueue(SpeechItem(2, "s", "prose", "B.", False))
    dropped = q.clear()
    assert [i.id for i in dropped] == [1, 2]
    assert len(q) == 0


def test_has_decision_is_false_for_prose_only():
    q = SpeechQueue()
    q.enqueue(SpeechItem(id=1, session="s", kind="prose", text="a", is_decision=False))
    assert q.has_decision() is False


def test_has_decision_true_when_a_decision_is_queued():
    q = SpeechQueue()
    q.enqueue(SpeechItem(id=1, session="s", kind="prose", text="a", is_decision=False))
    q.enqueue(SpeechItem(id=2, session="s", kind="choice", text="q", is_decision=True))
    assert q.has_decision() is True


# --- backlog cap tests ---

def _cap_item(i, decision=False):
    return SpeechItem(id=i, session="s", kind="plan" if decision else "prose",
                      text="t{0}".format(i), is_decision=decision)


def test_enqueue_unbounded_when_no_cap():
    q = SpeechQueue()
    assert all(q.enqueue(_cap_item(i)) is None for i in range(50))
    assert len(q) == 50


def test_enqueue_evicts_and_returns_oldest_prose_at_cap():
    q = SpeechQueue(cap=3)
    assert q.enqueue(_cap_item(0)) is None
    assert q.enqueue(_cap_item(1)) is None
    assert q.enqueue(_cap_item(2)) is None
    evicted = q.enqueue(_cap_item(3))            # full -> evict oldest (id 0)
    assert evicted is not None and evicted.id == 0
    assert len(q) == 3
    assert [it.id for it in (q.pop_next(), q.pop_next(), q.pop_next())] == [1, 2, 3]


def test_enqueue_exempts_decisions_evicting_oldest_prose_instead():
    q = SpeechQueue(cap=3)
    q.enqueue(_cap_item(0, decision=True))       # a waiting decision, oldest
    q.enqueue(_cap_item(1))                       # prose
    q.enqueue(_cap_item(2))                       # prose
    evicted = q.enqueue(_cap_item(3))            # full -> skip the decision, evict oldest prose (id 1)
    assert evicted is not None and evicted.id == 1
    assert [it.id for it in list(q._items)] == [0, 2, 3]   # decision retained at head


def test_enqueue_all_decisions_exceeds_cap_rather_than_drop():
    q = SpeechQueue(cap=2)
    assert q.enqueue(_cap_item(0, decision=True)) is None
    assert q.enqueue(_cap_item(1, decision=True)) is None
    assert q.enqueue(_cap_item(2, decision=True)) is None       # nothing evictable -> exceed cap
    assert len(q) == 3


def test_enqueue_front_is_not_subject_to_cap():
    q = SpeechQueue(cap=2)
    q.enqueue(_cap_item(1)); q.enqueue(_cap_item(2))
    q.enqueue_front(_cap_item(0))                 # resume-requeue: re-inserts a just-popped item
    assert [it.id for it in list(q._items)] == [0, 1, 2]


def test_speech_item_audio_path_defaults_none_and_is_settable():
    from sonari.queue import SpeechItem
    a = SpeechItem(id=1, session="s", kind="prose", text="hi", is_decision=False)
    assert a.audio_path is None
    b = SpeechItem(id=2, session="s", kind="prose", text="hi", is_decision=False,
                   audio_path="/cache/x.aiff")
    assert b.audio_path == "/cache/x.aiff"


# --- SP2 T0: oldest_id() ---

def test_oldest_id_empty_is_none():
    assert SpeechQueue().oldest_id() is None


def test_oldest_id_returns_head_id_not_tail():
    q = SpeechQueue()
    q.enqueue(_item(7))
    q.enqueue(_item(9))
    assert q.oldest_id() == 7          # the head (oldest), not the tail


def test_oldest_id_does_not_pop():
    q = SpeechQueue()
    q.enqueue(_item(7))
    q.oldest_id()
    assert len(q) == 1                 # non-destructive


def test_remove_by_id_removes_and_returns_the_item():
    q = SpeechQueue()
    q.enqueue(_item(7))
    q.enqueue(_item(9))
    got = q.remove_by_id(7)
    assert got is not None and got.id == 7
    assert [it.id for it in q._items] == [9]


def test_remove_by_id_unknown_returns_none_and_leaves_queue():
    q = SpeechQueue()
    q.enqueue(_item(7))
    assert q.remove_by_id(42) is None
    assert len(q) == 1


def test_insert_after_lands_items_right_behind_the_anchor():
    q = SpeechQueue()
    q.enqueue(_item(1, text="ack"))
    q.enqueue(_item(2, text="tail"))
    assert q.insert_after(1, [_item(3, text="render")]) is True
    assert [q.pop_next().text for _ in range(3)] == ["ack", "render", "tail"]


def test_insert_after_returns_false_when_anchor_absent():
    q = SpeechQueue()
    q.enqueue(_item(1, text="only"))
    assert q.insert_after(999, [_item(2, text="render")]) is False
    assert len(q) == 1 and q.pop_next().text == "only"   # nothing inserted

