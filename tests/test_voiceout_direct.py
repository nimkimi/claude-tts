from unittest import mock

from sonari.cli import voiceout


def test_spawns_say_with_the_option_terminator():
    with mock.patch("subprocess.Popen") as popen:
        assert voiceout.speak_direct("Sonari is unhealthy.") is True
    argv = popen.call_args[0][0]
    assert argv[0] == "say"
    assert "--" in argv
    # The text must come AFTER the terminator, so a leading '-' is not an option.
    assert argv.index("--") < argv.index("Sonari is unhealthy.")


def test_returns_false_and_never_raises_when_say_is_missing():
    with mock.patch("subprocess.Popen", side_effect=FileNotFoundError()):
        assert voiceout.speak_direct("anything") is False


def test_empty_text_is_not_spoken():
    with mock.patch("subprocess.Popen") as popen:
        assert voiceout.speak_direct("") is False
    popen.assert_not_called()
