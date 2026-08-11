from unittest import mock

from sonari.cli import voiceout


def test_prefers_the_daemon_when_it_is_reachable():
    with mock.patch("sonari.client.send", return_value={"ok": True}) as send, \
         mock.patch.object(voiceout, "speak_direct") as direct:
        assert voiceout.speak("hello") == "daemon"
    send.assert_called_once()
    direct.assert_not_called()


def test_sends_ANNOUNCE_not_PROSE():
    """PROSE is session-scoped and reads delta/index; a CLI sentence sent as
    PROSE is accepted and silently dropped (ProseAssembler().feed('', 0, False)
    returns []). This test is the guard against that regression, which every
    other test here is blind to because they all mock client.send."""
    from sonari.protocol import MsgType
    with mock.patch("sonari.client.send", return_value={"ok": True}) as send, \
         mock.patch.object(voiceout, "speak_direct"):
        voiceout.speak("hello")
    msg = send.call_args[0][0]
    assert msg["type"] == MsgType.ANNOUNCE
    assert msg["text"] == "hello"
    assert send.call_args.kwargs["expect_reply"] is True


def test_a_dropped_message_falls_back_instead_of_claiming_success():
    """The daemon answered but refused to speak (nowhere audible). Reporting
    "daemon" here would leave the user in silence believing it spoke."""
    with mock.patch("sonari.client.send", return_value={"ok": False}), \
         mock.patch.object(voiceout, "speak_direct", return_value=True) as direct:
        assert voiceout.speak("hello") == "direct"
    direct.assert_called_once_with("hello")


def test_falls_back_to_direct_when_the_daemon_is_unreachable():
    with mock.patch("sonari.client.send", side_effect=OSError("no daemon")), \
         mock.patch.object(voiceout, "speak_direct", return_value=True) as direct:
        assert voiceout.speak("hello") == "direct"
    direct.assert_called_once_with("hello")


def test_skips_the_daemon_entirely_when_the_caller_knows_it_is_broken():
    with mock.patch("sonari.client.send", return_value={"ok": True}) as send, \
         mock.patch.object(voiceout, "speak_direct", return_value=True):
        assert voiceout.speak("hello", prefer_daemon=False) == "direct"
    send.assert_not_called()


def test_reports_silent_when_both_paths_fail():
    with mock.patch("sonari.client.send", side_effect=OSError()), \
         mock.patch.object(voiceout, "speak_direct", return_value=False):
        assert voiceout.speak("hello") == "silent"
