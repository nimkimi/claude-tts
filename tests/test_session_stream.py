from sonari.session_stream import SessionStream
from sonari.assembler import ProseAssembler


def test_defaults_are_empty_and_unflagged():
    s = SessionStream()
    assert isinstance(s.assembler, ProseAssembler)
    assert s.prose_buffer == []
    assert s.options is None
    assert s.nav_cursor is None
    assert s.muted is False
    assert s.stopped is False
    assert s.warned_immediate is False
    assert s.guided is False


def test_reset_for_new_prompt_clears_playback_keeps_sticky():
    s = SessionStream()
    # playback state
    s.prose_buffer.append(("hi", object()))
    s.options = "Pick one"
    s.nav_cursor = 7
    old_assembler = s.assembler
    # sticky state
    s.muted = True
    s.warned_immediate = True
    s.guided = True

    s.reset_for_new_prompt()

    # playback reset
    assert s.prose_buffer == []
    assert s.options is None
    assert s.nav_cursor is None
    assert s.assembler is not old_assembler   # a fresh assembler
    # sticky preserved
    assert s.muted is True
    assert s.warned_immediate is True
    assert s.guided is True


def test_reset_for_new_prompt_keeps_stopped_sticky():
    # Per-session stop survives a new prompt: a session you stopped stays silent
    # until you ⌃⌘S it again (spec §6.1) — a background re-invocation must not
    # resurrect it.
    s = SessionStream()
    s.stopped = True
    s.reset_for_new_prompt()
    assert s.stopped is True


def test_new_stream_has_its_own_empty_speech_queue():
    from sonari.queue import SpeechQueue
    from sonari.session_stream import SessionStream
    st = SessionStream()
    assert isinstance(st.queue, SpeechQueue)
    assert len(st.queue) == 0


def test_reset_for_new_prompt_keeps_the_queue_object_and_items():
    # The FLUSH handler clears the queue explicitly (so it can drop heard-markers);
    # reset_for_new_prompt must NOT clear it, or those markers would leak.
    from sonari.queue import SpeechItem
    from sonari.session_stream import SessionStream
    st = SessionStream()
    q = st.queue
    st.queue.enqueue(SpeechItem(id=1, session="s", kind="prose",
                                text="x", is_decision=False))
    st.reset_for_new_prompt()
    assert st.queue is q
    assert len(st.queue) == 1
