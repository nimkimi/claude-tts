# tests/test_uninstall_disclosure.py
import json
from unittest import mock

from sonari.cli import install as install_cmd


def _state(tmp_path, sessions=2, per=3):
    """The REAL envelope. The brief's fixture put `entries` under `sessions`;
    the daemon puts the transcript pile under `history` (history.py:216-238)
    and keeps only folder/number in `sessions`. A fixture with the wrong shape
    made a broken summary look correct."""
    p = tmp_path / "state.json"
    p.write_text(json.dumps({
        "version": 1,
        "sessions": {f"s{i}": {"folder": "x", "number": i}
                     for i in range(sessions)},
        "history": {f"s{i}": {"entries": [{"text": "x"}] * per}
                    for i in range(sessions)},
    }), encoding="utf-8")
    return p


def test_summary_counts_sessions_and_utterances(tmp_path):
    with mock.patch("sonari.paths.STATE_PATH", _state(tmp_path, 2, 3)):
        assert install_cmd.transcript_summary() == (2, 6)


def test_absent_state_summarises_as_nothing(tmp_path):
    with mock.patch("sonari.paths.STATE_PATH", tmp_path / "absent.json"):
        assert install_cmd.transcript_summary() == (0, 0)


def test_the_full_teardown_order_is_ask_then_unload_then_stop(tmp_path):
    """Spec 8.1's pin, all three steps — asserting only ask<stop would let an
    implementer land ask -> SIGTERM -> unload, and launchd would then restart
    the daemon the SIGTERM just stopped."""
    order = []
    sup = mock.MagicMock()
    sup.uninstall.side_effect = lambda *a, **k: order.append("unloaded")
    with mock.patch("sonari.paths.STATE_PATH", _state(tmp_path)), \
         mock.patch("sonari.cli.voiceout.speak",
                    side_effect=lambda *a, **k: order.append("asked")), \
         mock.patch("sonari.cli.teardown.stop_daemon",
                    side_effect=lambda *a, **k: order.append("stopped") or "stopped"), \
         mock.patch("builtins.input", return_value="n"), \
         mock.patch("sys.stdout.isatty", return_value=True), \
         mock.patch("sonari.cli._platform",
                    return_value=mock.MagicMock(supervisor=sup)):
        install_cmd.uninstall()
    assert order == ["asked", "unloaded", "stopped"], order


def test_a_piped_uninstall_prints_the_question_but_does_not_speak_it(tmp_path, capsys):
    """Same tty discipline as doctor (T3): speaking a question we will not wait
    for an answer to is noise in a script."""
    with mock.patch("sonari.paths.STATE_PATH", _state(tmp_path)), \
         mock.patch("sys.stdout.isatty", return_value=False), \
         mock.patch("sonari.cli.voiceout.speak") as spoken, \
         mock.patch("sonari.cli.teardown.stop_daemon", return_value="stopped"), \
         mock.patch("sonari.cli._platform"):
        install_cmd.uninstall()
    spoken.assert_not_called()
    assert "Delete it?" in capsys.readouterr().out   # the question was actually printed


def test_purge_flag_deletes_the_transcripts(tmp_path):
    state = _state(tmp_path)
    with mock.patch("sonari.paths.STATE_PATH", state), \
         mock.patch("sonari.cli.teardown.stop_daemon", return_value="stopped"), \
         mock.patch("sonari.cli._platform"), \
         mock.patch("sonari.cli.voiceout.speak"):
        install_cmd.uninstall(purge=True)
    assert not state.exists()


def test_silence_preserves_the_transcripts(tmp_path):
    """No tty, no flag -> keep. Silence must never destroy data."""
    state = _state(tmp_path)
    with mock.patch("sonari.paths.STATE_PATH", state), \
         mock.patch("sys.stdout.isatty", return_value=False), \
         mock.patch("sonari.cli.teardown.stop_daemon", return_value="stopped"), \
         mock.patch("sonari.cli._platform"), \
         mock.patch("sonari.cli.voiceout.speak"):
        install_cmd.uninstall()
    assert state.exists()


def _real_shape(tmp_path, sessions_with_text=2, per=3, roster=0):
    """state.json as the daemon ACTUALLY writes it: the transcript pile lives in
    `history[session]["entries"]` (SessionHistory.to_state, history.py:216-238);
    `sessions` is only the live roster (folder/number), which carries no text."""
    p = tmp_path / "state.json"
    p.write_text(json.dumps({
        "version": 1,
        "sessions": {f"r{i}": {"folder": "x", "number": i}
                     for i in range(roster)},
        "history": {f"s{i}": {"msg_id": 1, "group_seq": 0, "turn_id": 0,
                              "entries": [{"text": "x", "kind": "prose"}] * per}
                    for i in range(sessions_with_text)},
    }), encoding="utf-8")
    return p


def test_the_count_comes_from_history_not_the_live_roster(tmp_path):
    """The roster holds folder/number, never text. Counting it reported 0
    utterances against a real 168 KB state.json holding 818 — a disclosure that
    understates what deletion destroys defeats the consent it exists to obtain."""
    with mock.patch("sonari.paths.STATE_PATH",
                    _real_shape(tmp_path, sessions_with_text=2, per=3, roster=9)):
        assert install_cmd.transcript_summary() == (2, 6)


def test_text_with_an_empty_roster_still_gets_disclosed(tmp_path):
    """Sessions end and leave the roster; their transcripts stay (forget() is
    called nowhere). Gating the ask on the roster would skip it entirely."""
    state = _real_shape(tmp_path, sessions_with_text=1, per=4, roster=0)
    with mock.patch("sonari.paths.STATE_PATH", state), \
         mock.patch("sys.stdout.isatty", return_value=False), \
         mock.patch("sonari.cli.teardown.stop_daemon", return_value="stopped"), \
         mock.patch("sonari.cli._platform"), \
         mock.patch("sonari.cli.voiceout.speak"):
        install_cmd.uninstall()
        # inside the patch: outside it, conftest's isolated STATE_PATH answers
        assert install_cmd.transcript_summary() == (1, 4)
    assert state.exists()          # silence still keeps the data


def test_a_failed_purge_says_so_instead_of_claiming_success(tmp_path, capsys):
    """purge=True that cannot delete must not go quiet: the user asked for the
    data to be gone and it is still there."""
    state = _real_shape(tmp_path, sessions_with_text=1, per=1)
    with mock.patch("sonari.paths.STATE_PATH", state), \
         mock.patch("sonari.cli.teardown.stop_daemon", return_value="stopped"), \
         mock.patch("sonari.cli._platform"), \
         mock.patch("sonari.cli.voiceout.speak"), \
         mock.patch("os.remove", side_effect=OSError("denied")):
        install_cmd.uninstall(purge=True)
    out = capsys.readouterr().out
    assert "could not delete" in out.lower()
