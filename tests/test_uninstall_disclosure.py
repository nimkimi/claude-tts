# tests/test_uninstall_disclosure.py
import json
from unittest import mock

from sonari.cli import install as install_cmd


def _state(tmp_path, sessions=2, per=3):
    p = tmp_path / "state.json"
    p.write_text(json.dumps({"version": 1, "sessions": {
        f"s{i}": {"entries": [{"text": "x"}] * per} for i in range(sessions)}}),
        encoding="utf-8")
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


def test_a_piped_uninstall_prints_the_question_but_does_not_speak_it(tmp_path):
    """Same tty discipline as doctor (T3): speaking a question we will not wait
    for an answer to is noise in a script."""
    with mock.patch("sonari.paths.STATE_PATH", _state(tmp_path)), \
         mock.patch("sys.stdout.isatty", return_value=False), \
         mock.patch("sonari.cli.voiceout.speak") as spoken, \
         mock.patch("sonari.cli.teardown.stop_daemon", return_value="stopped"), \
         mock.patch("sonari.cli._platform"):
        install_cmd.uninstall()
    spoken.assert_not_called()


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
