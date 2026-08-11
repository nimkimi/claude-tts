from unittest import mock

from sonari.cli import voiceout


def test_prefers_the_daemon_when_it_is_reachable():
    with mock.patch("sonari.client.send") as send, \
         mock.patch.object(voiceout, "speak_direct") as direct:
        assert voiceout.speak("hello") == "daemon"
    send.assert_called_once()
    direct.assert_not_called()


def test_falls_back_to_direct_when_the_daemon_is_unreachable():
    with mock.patch("sonari.client.send", side_effect=OSError("no daemon")), \
         mock.patch.object(voiceout, "speak_direct", return_value=True) as direct:
        assert voiceout.speak("hello") == "direct"
    direct.assert_called_once_with("hello")


def test_skips_the_daemon_entirely_when_the_caller_knows_it_is_broken():
    with mock.patch("sonari.client.send") as send, \
         mock.patch.object(voiceout, "speak_direct", return_value=True):
        assert voiceout.speak("hello", prefer_daemon=False) == "direct"
    send.assert_not_called()


def test_reports_silent_when_both_paths_fail():
    with mock.patch("sonari.client.send", side_effect=OSError()), \
         mock.patch.object(voiceout, "speak_direct", return_value=False):
        assert voiceout.speak("hello") == "silent"
