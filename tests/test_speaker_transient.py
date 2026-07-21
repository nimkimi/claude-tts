"""D8 law 3: short non-verbal tones play through a ONE-SLOT arbiter — a new
transient terminates a still-playing one (latest-wins supersede, owner ruling
2); transients never stack. Asset resolution mirrors earcon(): config-first,
then _FALLBACK_EARCONS, silent no-op for unconfigured legacy kinds."""
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


def test_new_failure_kinds_fall_back_to_builtin_assets():
    player = RecordingPlayer()
    sp = Speaker(earcon_player=player, earcons={})
    sp.transient("error_system")
    assert player.paths == ["/System/Library/Sounds/Blow.aiff"]


def test_config_entry_wins_over_the_fallback():
    player = RecordingPlayer()
    sp = Speaker(earcon_player=player,
                 earcons={"error_misdirected": "/custom/door.aiff"})
    sp.transient("error_misdirected")
    assert player.paths == ["/custom/door.aiff"]


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
