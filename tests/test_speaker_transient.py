"""D8 law 3: short non-verbal tones play through a ONE-SLOT arbiter — a new
transient terminates a still-playing one (latest-wins supersede, owner ruling
2); transients never stack. Asset resolution is one lookup in the config
dict (config.DEFAULTS merged in by load_config()); silent no-op for a kind
absent from the table."""
from pathlib import Path

from sonari.speaker import Speaker


class FakeProc:
    def __init__(self):
        self._finished = False
        self.terminate_calls = 0

    def finish(self):
        self._finished = True

    def poll(self):
        return 0 if self._finished else None

    def terminate(self):
        self.terminate_calls += 1
        self._finished = True


class RecordingPlayer:
    def __init__(self):
        self.paths = []
        self.procs = []

    def __call__(self, path):
        proc = FakeProc()
        self.paths.append(path)
        self.procs.append(proc)
        return proc


def test_transient_plays_configured_path():
    player = RecordingPlayer()
    sp = Speaker(earcon_player=player, earcons={"error": "/snd/Sosumi.aiff"})
    sp.transient("error")
    assert player.paths == ["/snd/Sosumi.aiff"]


def test_second_transient_terminates_a_still_playing_first():
    player = RecordingPlayer()
    sp = Speaker(earcon_player=player,
                 earcons={"error": "/snd/a.aiff", "turn_done": "/snd/b.aiff"})
    sp.transient("error")
    sp.transient("turn_done")                      # first still playing
    assert player.procs[0].terminate_calls == 1    # superseded, never stacked
    assert player.paths == ["/snd/a.aiff", "/snd/b.aiff"]
    # The terminated tone is RETAINED for a deterministic reap, not dropped
    # unreaped (the old earcon reap's guarantee — CPython GC is non-deterministic).
    assert sp._terminated_procs == [player.procs[0]]
    sp.transient("error")                          # a third tone reaps the finished first
    assert player.procs[0] not in sp._terminated_procs   # poll-purged, never leaked
    assert len(sp._terminated_procs) <= 1          # bounded: only the just-superseded proc


def test_finished_transient_is_not_terminated():
    player = RecordingPlayer()
    sp = Speaker(earcon_player=player,
                 earcons={"error": "/snd/a.aiff", "turn_done": "/snd/b.aiff"})
    sp.transient("error")
    player.procs[0].finish()
    sp.transient("turn_done")
    assert player.procs[0].terminate_calls == 0


def test_unconfigured_legacy_kind_is_silent_noop():
    player = RecordingPlayer()
    sp = Speaker(earcon_player=player, earcons={})
    sp.transient("turn_done")
    assert player.paths == []


def test_new_failure_kinds_resolve_from_the_merged_defaults():
    from sonari.config import DEFAULTS
    player = RecordingPlayer()
    sp = Speaker(earcon_player=player, earcons=dict(DEFAULTS["earcons"]))
    sp.transient("error_system")
    assert player.paths == ["/System/Library/Sounds/Sosumi.aiff"]


def test_config_entry_wins_over_the_default():
    from sonari.config import _deep_merge, DEFAULTS
    player = RecordingPlayer()
    merged = _deep_merge(DEFAULTS, {"earcons": {"error_misdirected": "/custom/door.aiff"}})
    sp = Speaker(earcon_player=player, earcons=merged["earcons"])
    sp.transient("error_misdirected")
    assert player.paths == ["/custom/door.aiff"]


def test_a_kind_absent_from_the_table_is_silent_now_that_nothing_rescues_it():
    """The behavioural receipt for the collapse itself.

    Before this task, the speaker-level fallback table rescued alarm_daemon_down
    whatever the caller passed, so this played Hero.aiff. With one resolver, the
    table IS the answer and an absent kind is silent. Reintroducing any
    Python-level fallback in Speaker.transient makes this test fail -- which is
    the point: the two tests above it pass identically with or without the
    fallback, so without this one the only thing standing between the collapse
    and a quiet re-introduction is a source-text grep.
    """
    player = RecordingPlayer()
    sp = Speaker(earcon_player=player, earcons={"error": "/System/Library/Sounds/Sosumi.aiff"})
    sp.transient("alarm_daemon_down")
    assert player.paths == []


def test_transient_without_player_is_noop():
    Speaker().transient("error")          # must not raise


def test_player_returning_none_leaves_the_slot_empty():
    sp = Speaker(earcon_player=lambda path: None,
                 earcons={"error": "/snd/a.aiff", "turn_done": "/snd/b.aiff"})
    sp.transient("error")
    sp.transient("turn_done")             # must not deref the missing proc
    assert sp._transient_proc is None


def test_pitch_asset_resolves_the_packaged_chirp():
    sp = Speaker()
    up = sp.pitch_asset("up")
    assert up.endswith("/assets/pitch_up.wav")
    assert Path(up).exists()              # a real committed asset path
    assert sp.pitch_asset("down").endswith("/assets/pitch_down.wav")
    assert sp.pitch_asset("sideways") is None
