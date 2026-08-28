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


def test_cue_word_rides_the_queue_after_the_tone():
    daemon, queue, speaker, sessions, config = make_daemon()
    with daemon._lock:                       # the word path's caller contract
        daemon.cue("error_system", word="Speech failed; kept unheard.", session="fg")
    assert speaker.earcons == ["error_system"]      # tone fired, unchanged semantics
    item = queue.pop_next()
    assert item.text == "Speech failed; kept unheard."
    assert item.kind == "prose" and item.is_decision is False   # chrome, no call-sign
    assert item.control_cue   # speaks on a held speaker too
    assert item.forward is False                    # can never advance a frontier


def test_cue_word_enqueues_in_normal_order_not_front():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon._enqueue("fg", "prose", "earlier.", False)
    with daemon._lock:
        daemon.cue("permission_expired",
                   word="That ask timed out — check the terminal.", session="fg")
    assert queue.pop_next().text == "earlier."      # the tone was the instant part
    assert queue.pop_next().text == "That ask timed out — check the terminal."


def test_cue_word_without_session_is_tone_only():
    daemon, queue, speaker, sessions, config = make_daemon()
    with daemon._lock:
        daemon.cue("error_system", word="orphan word")
    assert speaker.earcons == ["error_system"]
    assert len(queue) == 0


def test_cue_rejected_kind_never_enqueues_its_word(capsys):
    daemon, queue, speaker, sessions, config = make_daemon()
    with daemon._lock:
        daemon.cue("nope", word="never spoken", session="fg")
    assert speaker.earcons == []
    assert len(queue) == 0
    assert "unregistered cue: nope" in capsys.readouterr().err


def test_alarm_tier_kind_is_refused_by_cue(capsys):
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon.cue("alarm_hotkeys_down")
    assert speaker.earcons == []                   # never reaches the arbiter
    assert "alarm cue misrouted" in capsys.readouterr().err
