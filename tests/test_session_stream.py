from sonari.session_stream import SessionStream
from sonari.assembler import ProseAssembler


def test_defaults_are_empty_and_unflagged():
    s = SessionStream()
    assert isinstance(s.assembler, ProseAssembler)
    assert s.prose_buffer == []
    assert s.options is None
    assert s.nav_cursor is None
    assert s.captured is False
    assert s.open_msg is False
    assert s.muted is False
    assert s.warned_immediate is False
    assert s.guided is False


def test_reset_for_new_prompt_clears_playback_keeps_sticky():
    s = SessionStream()
    # playback state
    s.prose_buffer.append(("hi", object()))
    s.options = "Pick one"
    s.nav_cursor = 7
    s.captured = True
    s.open_msg = True
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
    assert s.captured is False
    assert s.open_msg is False
    assert s.assembler is not old_assembler   # a fresh assembler
    # sticky preserved
    assert s.muted is True
    assert s.warned_immediate is True
    assert s.guided is True
