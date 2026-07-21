"""host.cue(kind): the ONE feature-facing transient API (D8 law 4) — validates
against CUES; an unknown or non-transient kind is a stderr line, never a raise
(an eyes-free daemon must not crash on a bad cue name)."""
from tests.daemon_helpers import make_daemon


def test_cue_plays_a_registered_transient():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon.cue("error")
    assert speaker.earcons == ["error"]


def test_unregistered_kind_is_a_stderr_noop(capsys):
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon.cue("nope")
    assert speaker.earcons == []
    assert "unregistered cue: nope" in capsys.readouterr().err


def test_non_transient_kind_is_refused(capsys):
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon.cue("callsign")                # prelude tier — not fireable directly
    assert speaker.earcons == []
    assert "unregistered cue: callsign" in capsys.readouterr().err
