"""W13 (spec §14, Block-1 ratified): the most frequent voice switch carries the
thinnest cue. Keep-going now pre-rolls the new speaker's folder spearcon —
delivery only; selection is byte-identical (anchor 7); all inside the M1 lock."""
from sonari.protocol import PROTOCOL_VERSION
from tests.daemon_helpers import make_daemon, stream_queue


def _msg(t, session, **kw):
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


def _prime(daemon):
    """Speak one fg item so _last_spoken_session is set (the first-utterance
    rule would otherwise suppress the splice the miss test asserts)."""
    daemon._enqueue("fg", "prose", "fg content.", False)
    daemon._speak_loop_once()


def test_hit_plays_the_spearcon_then_unprefixed_content():
    daemon, queue, speaker, sessions, config = make_daemon()
    _prime(daemon)
    sessions.register("bg", cwd="/x/bg")
    daemon._spearcons.available["bg"] = "/sp/bg.aiff"
    daemon._enqueue("bg", "prose", "bg content.", False)
    daemon._speak_loop_once()                      # keep-going claims the PRE-ROLL
    assert speaker.audio_paths[-1] == "/sp/bg.aiff"
    assert sessions.speaker() == "bg"
    daemon._speak_loop_once()                      # then the content, attribution claimed
    assert speaker.spoken[-1] == "bg content."     # NO spliced folder prefix
    assert speaker.audio_paths[-1] is None


def test_miss_keeps_todays_splice_byte_identically_and_kicks_generation():
    daemon, queue, speaker, sessions, config = make_daemon()
    _prime(daemon)
    sessions.register("bg", cwd="/x/bg")           # no cached spearcon
    daemon._enqueue("bg", "prose", "bg content.", False)
    daemon._speak_loop_once()
    assert speaker.spoken[-1] == "bg. bg content."   # the splice, unchanged
    assert speaker.audio_paths[-1] is None
    assert "bg" in daemon._spearcons.generated       # self-heals by next time


def test_selection_is_byte_identical_cache_state_never_biases_it():
    daemon, queue, speaker, sessions, config = make_daemon()
    _prime(daemon)
    sessions.register("older", cwd="/x/older")
    sessions.register("newer", cwd="/x/newer")
    daemon._enqueue("older", "prose", "older content.", False)
    daemon._enqueue("newer", "prose", "newer content.", False)
    daemon._spearcons.available["newer"] = "/sp/newer.aiff"   # only the LOSER is cached
    daemon._speak_loop_once()
    assert sessions.speaker() == "older"           # longest-waiting-first, unchanged


def test_preroll_never_unmutes_and_never_selects_a_stopped_stream():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.register("bg", cwd="/x/bg")
    daemon._spearcons.available["bg"] = "/sp/bg.aiff"
    daemon._enqueue("bg", "prose", "bg content.", False)
    daemon._stream("bg").stopped = True            # Fork-2: muted stays muted
    daemon._speak_loop_once()
    assert sessions.speaker() == "fg"              # selector skipped it (unchanged)
    assert daemon._stream("bg").stopped is True


def test_preroll_moves_no_pointer():
    daemon, queue, speaker, sessions, config = make_daemon()
    _prime(daemon)
    sessions.register("bg", cwd="/x/bg")
    daemon._spearcons.available["bg"] = "/sp/bg.aiff"
    daemon._enqueue("bg", "prose", "bg content.", False)
    daemon._speak_loop_once()
    assert sessions.foreground() == "fg"           # R12: the workspace never moves on its own


def test_flush_mid_preroll_loses_nothing_and_leaves_no_orphan():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.register("bg", cwd="/x/bg")
    daemon._spearcons.available["bg"] = "/sp/bg.aiff"

    class _Entry:
        heard = False

    daemon._enqueue("bg", "prose", "bg content.", False, entry=_Entry())

    class _Reentrant:
        """FLUSH(bg) lands DURING the pre-roll spearcon's playback — the queued
        content item must be cleared exactly like any queued item (inherited
        FLUSH semantics), with no orphaned marker and no resurrection."""
        def __init__(self):
            self._epoch = 0
            self.fired = False

        def speak(self, text=None, audio_path=None, cancel_epoch=None):
            if not self.fired:
                self.fired = True
                daemon.handle_message(_msg("flush", "bg"))
            return False

        def cancel_epoch(self):
            return self._epoch

        def cancel(self):
            self._epoch += 1

        def earcon(self, kind):
            pass

    daemon.speaker = _Reentrant()
    daemon._speak_loop_once()                      # pre-roll claimed; FLUSH races it
    assert daemon._current_item is None            # claim released
    assert len(stream_queue(daemon, "bg")._items) == 0   # content flushed, NOT resurrected
    assert daemon._pending_heard == {}             # no orphaned marker
