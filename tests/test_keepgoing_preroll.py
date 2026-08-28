"""W13 via D8 (owner ruling 4): keep-going's pre-roll spearcon is the content
item's own PRELUDE — one atomic unit, so FLUSH/STOP can never split the pair
(the flush-between-pair attribution seam). Selection stays byte-identical."""
from sonari.protocol import PROTOCOL_VERSION
from tests.daemon_helpers import make_daemon, stream_queue


def _msg(t, session, **kw):
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


def _prime(daemon):
    """Speak one fg item so _last_spoken_session is set (the first-utterance
    rule would otherwise suppress the splice the miss test asserts)."""
    daemon._enqueue("fg", "prose", "fg content.", False)
    daemon._speak_loop_once()


def test_hit_binds_the_spearcon_prelude_to_the_content_item():
    daemon, queue, speaker, sessions, config = make_daemon()
    _prime(daemon)
    sessions.register("bg", cwd="/x/bg")
    daemon._spearcons.available["bg"] = "/sp/bg.aiff"
    daemon._enqueue("bg", "prose", "bg content.", False)
    daemon._speak_loop_once()                      # ONE iteration: spearcon + content
    assert sessions.speaker() == "bg"
    assert speaker.audio_paths[-2:] == ["/sp/bg.aiff", None]
    assert speaker.spoken[-1] == "bg content."     # NO spliced folder prefix


def test_hit_preserves_an_existing_prelude_never_clobbers_it():
    """A chirp-preluded confirm (owner ruling 3: the directional chirp is bound to
    Approved./Denied.) sitting in a background stream is delivered by keep-going; a
    spearcon cache HIT must NOT overwrite its bound chirp with the folder call-sign.
    The prelude is one atomic unit and the yes/no chirp is the redundant channel."""
    daemon, queue, speaker, sessions, config = make_daemon()
    _prime(daemon)
    sessions.register("bg", cwd="/x/bg")
    daemon._spearcons.available["bg"] = "/sp/bg.aiff"   # cache HIT — the clobber trigger
    daemon._enqueue("bg", "prose", "Approved.", False,
                    control_cue=True,
                    prelude=("/pitch/up.wav",))
    daemon._speak_loop_once()                      # keep-going delivers the confirm
    assert sessions.speaker() == "bg"
    assert speaker.audio_paths[-2:] == ["/pitch/up.wav", None]   # chirp kept, NOT /sp/bg.aiff
    assert speaker.spoken[-1] == "Approved."


def test_miss_binds_the_neutral_crossing_marker_and_keeps_the_splice():
    # D2 §6.6 (RL1): the most frequent voice-crossing gets a marker even when
    # the spearcon is uncached — a fixed neutral asset, bound atomically (law
    # 2), while the spoken splice still NAMES the destination (names_session
    # stays False: the marker is neutral, not an attribution claim).
    daemon, queue, speaker, sessions, config = make_daemon()
    _prime(daemon)
    sessions.register("bg", cwd="/x/bg")           # no cached spearcon
    daemon._enqueue("bg", "prose", "bg content.", False)
    daemon._speak_loop_once()
    assert speaker.audio_paths[-2:] == ["/System/Library/Sounds/Frog.aiff", None]
    assert speaker.spoken[-1] == "bg. bg content."   # the splice, unchanged
    assert "bg" in daemon._spearcons.generated       # still self-heals by next time


def test_miss_never_clobbers_an_existing_prelude():
    """The D8 whole-branch Major, mirrored on the MISS side: a chirp-preluded
    confirm delivered by keep-going with NO cached spearcon keeps its bound
    chirp — the crossing marker never overwrites an atomic unit."""
    daemon, queue, speaker, sessions, config = make_daemon()
    _prime(daemon)
    sessions.register("bg", cwd="/x/bg")           # cache MISS
    daemon._enqueue("bg", "prose", "Approved.", False,
                    control_cue=True,
                    prelude=("/pitch/up.wav",))
    daemon._speak_loop_once()
    assert sessions.speaker() == "bg"
    assert speaker.audio_paths[-2:] == ["/pitch/up.wav", None]   # chirp kept, no Frog
    assert speaker.spoken[-1] == "Approved."


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
        """FLUSH(bg) lands DURING the prelude's playback — the in-flight unit is
        superseded by the new prompt (dropped, marker released; never resurrected),
        exactly like any cut item."""
        def __init__(self):
            self._epoch = 0
            self.fired = False

        def speak(self, text=None, audio_path=None, cancel_epoch=None, voice=None):
            if not self.fired:
                self.fired = True
                daemon.handle_message(_msg("flush", "bg"))
            return False

        def cancel_epoch(self):
            return self._epoch

        def cancel(self):
            self._epoch += 1

        def transient(self, kind):
            pass

    daemon.speaker = _Reentrant()
    daemon._speak_loop_once()                      # unit claimed; FLUSH races it
    assert daemon._current_item is None            # claim released
    assert len(stream_queue(daemon, "bg")._items) == 0   # content NOT resurrected
    assert daemon._pending_heard == {}             # no orphaned marker
