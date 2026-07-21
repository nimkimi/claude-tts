"""D8 law 2 (atomic binding): a prelude decorates its OWN SpeechItem and the
speak loop plays prelude parts + content as ONE indivisible unit — same claim,
same cancel epoch. An interrupted part abandons the rest (never half-heard);
a stop mid-unit requeues the WHOLE item, prelude included (aria-atomic)."""
from sonari.protocol import PROTOCOL_VERSION
from sonari.queue import SpeechItem
from tests.daemon_helpers import make_daemon, stream_queue


def _msg(t, session, **kw):
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


def test_prelude_defaults_empty():
    item = SpeechItem(id=1, session="s", kind="prose", text="x", is_decision=False)
    assert item.prelude == ()


def test_loop_plays_prelude_paths_then_content_in_one_iteration():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon._enqueue("fg", "prose", "the content.", False,
                    prelude=("/sp/one.aiff", "/sp/two.aiff"))
    daemon._speak_loop_once()
    assert speaker.audio_paths == ["/sp/one.aiff", "/sp/two.aiff", None]
    assert speaker.spoken[-1] == "the content."


def test_unit_shares_one_cancel_epoch():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon._enqueue("fg", "prose", "the content.", False, prelude=("/sp/one.aiff",))
    daemon._speak_loop_once()
    assert len(speaker.epochs) == 2               # prelude + content
    assert len(set(speaker.epochs)) == 1          # one claim baseline


def test_interrupted_prelude_abandons_the_rest_and_stays_unheard():
    daemon, queue, speaker, sessions, config = make_daemon()

    class _Entry:
        heard = False

    entry = _Entry()
    speaker.complete = False                      # the first part is cut
    daemon._enqueue("fg", "prose", "never spoken.", False,
                    prelude=("/sp/one.aiff", "/sp/two.aiff"), entry=entry)
    daemon._speak_loop_once()
    assert speaker.audio_paths == ["/sp/one.aiff"]  # part 2 + content skipped
    assert speaker.spoken == [None]                 # only the prelude call
    assert entry.heard is False                     # the unit stays unheard
    assert daemon._current_item is None             # claim released


def test_held_branch_plays_the_unit_for_a_pause_exempt_item():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon._stream("fg").stopped = True
    daemon._enqueue("fg", "prose", "Approved.", False, mute_exempt=True,
                    pause_exempt=True, at_front=True, prelude=("/pitch/up.wav",))
    daemon._speak_loop_once()
    assert speaker.audio_paths == ["/pitch/up.wav", None]
    assert speaker.spoken[-1] == "Approved."


def test_stop_mid_prelude_requeues_the_whole_unit():
    daemon, queue, speaker, sessions, config = make_daemon()

    class _StopMidPrelude:
        """STOP lands during the prelude's playback; the unit must requeue WHOLE
        (prelude included) so resume replays it from the top."""
        def __init__(self):
            self._epoch = 0

        def speak(self, text=None, audio_path=None, cancel_epoch=None, voice=None):
            daemon.handle_message(_msg("stop_session", "fg"))
            return False

        def cancel_epoch(self):
            return self._epoch

        def cancel(self):
            self._epoch += 1

        def transient(self, kind):
            pass

    daemon._enqueue("fg", "prose", "the content.", False, prelude=("/sp/one.aiff",))
    daemon.speaker = _StopMidPrelude()
    daemon._speak_loop_once()
    item = stream_queue(daemon, "fg")._items[0]     # requeued at the head
    assert item.prelude == ("/sp/one.aiff",)
    assert item.text == "the content."


def test_captured_in_flight_unit_restores_with_its_prelude():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon._state._last_utterance = ("earlier words.", None)
    cur = SpeechItem(id=999, session="fg", kind="prose", text="mid-flight.",
                     is_decision=False, prelude=("/sp/fg.aiff",))
    daemon._state._current_item = cur
    daemon.handle_message(_msg("repeat_last", "fg"))   # captures + re-enqueues cur
    items = stream_queue(daemon, "fg")._items
    assert items[0].text == "earlier words."           # the repeat itself, at head
    assert items[0].prelude == ()                      # repeats never carry preludes
    assert items[1].text == "mid-flight."              # the interrupted unit resumes
    assert items[1].prelude == ("/sp/fg.aiff",)        # ...WHOLE (aria-atomic)
