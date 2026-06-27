from pathlib import Path

from sonari.speaker import Speaker


class RecordingPlayer:
    def __init__(self):
        self.paths = []

    def __call__(self, path):
        self.paths.append(path)
        return None        # fire-and-forget; no proc to track


def test_pitch_up_plays_the_up_asset_directly_from_the_package():
    player = RecordingPlayer()
    sp = Speaker(earcon_player=player)
    sp.pitch("up")
    assert len(player.paths) == 1
    assert player.paths[0].endswith("/assets/pitch_up.wav")
    assert Path(player.paths[0]).exists()           # a real committed asset path


def test_pitch_down_plays_the_down_asset():
    player = RecordingPlayer()
    Speaker(earcon_player=player).pitch("down")
    assert player.paths[0].endswith("/assets/pitch_down.wav")


def test_pitch_unknown_direction_is_noop():
    player = RecordingPlayer()
    Speaker(earcon_player=player).pitch("sideways")
    assert player.paths == []


def test_pitch_without_player_is_noop():
    Speaker().pitch("up")        # must not raise
