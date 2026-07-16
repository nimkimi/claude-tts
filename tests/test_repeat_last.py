"""W12 (spec §13): "say that again" — the single most frequent by-ear need.
Captures the last COMPLETED non-mute_exempt utterance AS SPOKEN (prefix
included); replays it with the ⌃⌘W capture-park-resume discipline; idempotent."""
from sonari.protocol import PROTOCOL_VERSION
from sonari.queue import SpeechItem
from tests.daemon_helpers import make_daemon, stream_queue


def _msg(t, session, **kw):
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


def test_repeat_speaks_the_last_content_utterance_verbatim_including_prefix():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.register("b", cwd="/x/b")
    daemon._enqueue("fg", "prose", "first thing.", False)
    daemon._speak_loop_once()
    sessions.set_speaker("b")
    daemon._enqueue("b", "prose", "second thing.", False)
    daemon._speak_loop_once()
    assert speaker.spoken[-1] == "b. second thing."    # prefixed: the voice switched
    daemon.handle_message(_msg("repeat_last", "b"))
    daemon._speak_loop_once()
    assert speaker.spoken[-1] == "b. second thing."    # verbatim = what your ear got


def test_repeat_is_idempotent_across_presses():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon._enqueue("fg", "prose", "content.", False)
    daemon._speak_loop_once()
    for _ in range(3):
        daemon.handle_message(_msg("repeat_last", "fg"))
        daemon._speak_loop_once()
    assert speaker.spoken[-3:] == ["content."] * 3     # the repeat never becomes the target


def test_control_cues_are_never_the_repeat_target():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon._enqueue("fg", "prose", "content.", False)
    daemon._speak_loop_once()
    daemon.handle_message(_msg("where_am_i", "fg"))
    daemon._speak_loop_once()                          # the ⌃⌘W readout (mute_exempt)
    daemon.handle_message(_msg("repeat_last", "fg"))
    daemon._speak_loop_once()
    assert speaker.spoken[-1] == "content."            # chrome excluded


def test_interrupted_utterance_is_not_captured():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon._enqueue("fg", "prose", "content.", False)
    daemon._speak_loop_once()                          # captured
    speaker.complete = False
    daemon._enqueue("fg", "prose", "cut off.", False)
    daemon._speak_loop_once()                          # NOT completed -> not captured
    speaker.complete = True
    daemon.handle_message(_msg("repeat_last", "fg"))
    daemon._speak_loop_once()
    assert speaker.spoken[-1] == "content."


def test_repeat_parks_and_resumes_the_interrupted_item():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon._enqueue("fg", "prose", "earlier content.", False)
    daemon._speak_loop_once()                          # captured as last utterance
    inflight = SpeechItem(id=999, session="fg", kind="prose",
                          text="interrupted.", is_decision=False)
    daemon._current_item = inflight                    # simulate mid-utterance
    daemon.handle_message(_msg("repeat_last", "fg"))
    assert speaker.cancels == 1                        # barge-in
    texts = [it.text for it in queue._items]
    assert texts[0] == "earlier content."              # the repeat leads
    assert texts[1] == "interrupted."                  # parked DEEPER — resumes after


def test_nothing_to_repeat_is_a_spoken_cue_not_an_error():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon.handle_message(_msg("repeat_last", "fg"))
    daemon._speak_loop_once()
    assert speaker.spoken[-1] == "Nothing to repeat."
    assert speaker.earcons == []                       # an empty repeat is not a mis-press


def test_no_speaker_routes_to_a_playable_workspace():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon._enqueue("fg", "prose", "content.", False)
    daemon._speak_loop_once()
    sessions.set_speaker(None)                         # voice released
    daemon.handle_message(_msg("repeat_last", "fg"))
    assert [it.text for it in queue._items] == ["content."]   # fg = playable workspace


def test_no_speaker_and_muted_workspace_plays_error_tone():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon._enqueue("fg", "prose", "content.", False)
    daemon._speak_loop_once()
    sessions.set_speaker(None)
    daemon._stream("fg").stopped = True
    daemon.handle_message(_msg("repeat_last", "fg"))
    assert speaker.earcons == ["error"]                # mirror ⌃⌘W's None-speaker branch
