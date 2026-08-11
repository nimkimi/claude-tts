"""The seam between voiceout.speak() and the daemon that has to act on it.

This file exists because a bug lived exactly here and nothing could see it.
speak() was sending {"type": PROSE, "text": ...}; PROSE is session-scoped and
its handler reads delta/index, so ProseAssembler().feed("", 0, False) returned
[] and the sentence was silently discarded — while speak() reported success.

Every test on either side passed throughout: the routing tests mock
client.send, and the handler tests build the message by hand. Both halves were
green and the join was broken. These tests take the message speak() ACTUALLY
builds and feed it to a REAL daemon, so the two can never drift apart again.
"""
from unittest import mock

from sonari.cli import voiceout
from tests.daemon_helpers import make_daemon


def _message_speak_sends(text: str) -> dict:
    """The exact dict speak() puts on the wire — captured, not reconstructed."""
    with mock.patch("sonari.client.send", return_value={"ok": True}) as send, \
         mock.patch.object(voiceout, "speak_direct"):
        voiceout.speak(text)
    return send.call_args[0][0]


def test_the_message_speak_sends_is_one_the_daemon_actually_speaks():
    daemon, queue, _speaker, _sessions, _config = make_daemon()
    reply = daemon.handle_message(_message_speak_sends("Sonari is healthy."))
    assert reply == {"ok": True}, "the daemon refused the message speak() sends"
    assert "Sonari is healthy." in [i.text for i in queue._items], (
        "the daemon accepted the message but nothing was queued to speak")


def test_a_refusal_is_reported_so_the_caller_can_fall_back():
    """No foreground and no speaker: there is nowhere audible. The daemon must
    say so rather than accept-and-drop, because speak() falls back on ok False."""
    daemon, _queue, _speaker, _sessions, _config = make_daemon(foreground=None)
    reply = daemon.handle_message(_message_speak_sends("Sonari is unhealthy."))
    assert reply == {"ok": False}


def test_speak_reports_daemon_only_when_the_daemon_really_took_it():
    """Ties the two halves together: route through the real handler as
    client.send's side effect, so speak()'s return value reflects a real
    daemon outcome rather than a mocked one."""
    daemon, queue, _speaker, _sessions, _config = make_daemon()

    with mock.patch("sonari.client.send",
                    side_effect=lambda m, **kw: daemon.handle_message(m)), \
         mock.patch.object(voiceout, "speak_direct", return_value=True) as direct:
        assert voiceout.speak("Sonari is healthy.") == "daemon"

    direct.assert_not_called()
    assert "Sonari is healthy." in [i.text for i in queue._items]


def test_speak_falls_back_when_the_real_daemon_refuses():
    daemon, _queue, _speaker, _sessions, _config = make_daemon(foreground=None)

    with mock.patch("sonari.client.send",
                    side_effect=lambda m, **kw: daemon.handle_message(m)), \
         mock.patch.object(voiceout, "speak_direct", return_value=True) as direct:
        assert voiceout.speak("Sonari is unhealthy.") == "direct"

    direct.assert_called_once_with("Sonari is unhealthy.")
