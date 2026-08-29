"""D4 T15: the try-doctor hint, and closing #54's two gaps in
_signal_speak_failure.

Both gaps were verified against the real code before this test was written
(not assumed from the plan): host.py:924 fired a bare `self.cue("error_system")`
with no word when session was None (gap A), and there was no fallback when
`cue()` itself raised (gap B).

Gap A's fix does NOT route the word through `cue(word=..., session=None)` —
`cue()` (host.py:568-571) only enqueues `word` when BOTH `word` and `session`
are not None, so that call would silently drop the word in production even
though a test mocking `daemon.cue` wholesale would go green. Session-less
failures speak via `voiceout.speak_direct` (T4) instead — there is no session
queue to enqueue onto, and this handler fires because speech itself just
broke, so riding that same path might never be heard anyway. Tests here mock
`speak_direct`, not just `daemon.cue`, so a regression back to the dropped-word
behaviour would actually be caught (the mock-blindness lesson from earlier in
this campaign)."""
from unittest import mock

from tests.daemon_helpers import make_daemon
from sonari.daemon.host import SPEAK_FAILURE_WORD


def test_sessionless_failure_speaks_a_word_not_just_a_tone():
    """#54 gap A: host.py:924 fired a BARE tone when no session was known."""
    daemon = make_daemon()[0]
    with mock.patch.object(daemon, "cue") as cue, \
         mock.patch("sonari.cli.voiceout.speak_direct") as direct:
        try:
            raise RuntimeError("synth died")
        except RuntimeError:
            daemon._signal_speak_failure(None)
    cue.assert_called_once_with("error_system")     # the tone still fires
    direct.assert_called_once()
    assert SPEAK_FAILURE_WORD in direct.call_args.args[0], "session-less failure had no word"


def test_try_doctor_is_suggested_once_then_suppressed():
    daemon = make_daemon()[0]
    with mock.patch.object(daemon, "cue"), \
         mock.patch("sonari.cli.voiceout.speak_direct") as direct:
        for _ in range(3):
            try:
                raise RuntimeError("synth died")
            except RuntimeError:
                daemon._signal_speak_failure(None)
    words = " ".join(c.args[0] for c in direct.call_args_list)
    assert words.count("doctor") == 1, "the doctor hint nagged"


def test_a_later_success_re_arms_the_hint():
    daemon = make_daemon()[0]
    with mock.patch.object(daemon, "cue"), \
         mock.patch("sonari.cli.voiceout.speak_direct") as direct:
        try:
            raise RuntimeError("x")
        except RuntimeError:
            daemon._signal_speak_failure(None)
        daemon._faultcue.note_success("speak")
        try:
            raise RuntimeError("x")
        except RuntimeError:
            daemon._signal_speak_failure(None)
    words = " ".join(c.args[0] for c in direct.call_args_list)
    assert words.count("doctor") == 2


def test_falls_back_to_direct_say_when_the_cue_itself_cannot_speak():
    """#54 gap B: the word was routed through the TTS path that just failed."""
    daemon = make_daemon()[0]
    with mock.patch.object(daemon, "cue", side_effect=RuntimeError("tts down")), \
         mock.patch("sonari.cli.voiceout.speak_direct") as direct:
        try:
            raise RuntimeError("synth died")
        except RuntimeError:
            daemon._signal_speak_failure(None)
    direct.assert_called_once()
    assert SPEAK_FAILURE_WORD in direct.call_args.args[0], "gap-B fallback dropped the word"
