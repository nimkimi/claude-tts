from sonari.speaker import Speaker
from tests.daemon_helpers import make_daemon


class _Proc:
    returncode = 0

    def wait(self, timeout=None):
        return 0

    def terminate(self):
        pass


def test_speaker_speak_overrides_voice_then_reverts():
    seen = []

    def runner(text, voice, rate):
        seen.append(voice)
        return _Proc()

    sp = Speaker(voice="Main", rate=200, say_runner=runner)
    assert sp.speak("hi", voice="Alt") is True
    assert sp.speak("bye") is True
    assert seen == ["Alt", "Main"]


def test_item_voice_reaches_speaker_through_the_loop():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon._enqueue("fg", "prose", "Body sentence.", False, voice="Daniel")
    daemon._speak_loop_once()
    assert speaker.spoken[-1] == "Body sentence."
    assert speaker.spoken_voices[-1] == "Daniel"


def test_default_item_voice_is_none():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon._enqueue("fg", "prose", "Plain.", False)
    daemon._speak_loop_once()
    assert speaker.spoken_voices[-1] is None
