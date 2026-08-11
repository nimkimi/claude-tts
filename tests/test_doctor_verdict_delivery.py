# tests/test_doctor_verdict_delivery.py
from unittest import mock

from sonari import cli

_ROWS = [("say", True, "ok"), ("daemon socket", False, "down")]


def _run(speak: bool):
    with mock.patch("sonari.cli.doctor.doctor", return_value=_ROWS), \
         mock.patch("sonari.cli.doctor.should_speak", return_value=speak), \
         mock.patch("sonari.cli.voiceout.speak") as spoken:
        rc = cli.main(["doctor"])
    return rc, spoken


def test_speaks_the_verdict_when_interactive():
    rc, spoken = _run(True)
    assert rc == 1
    spoken.assert_called_once()
    assert "unhealthy" in spoken.call_args[0][0]


def test_says_nothing_when_not_interactive():
    rc, spoken = _run(False)
    assert rc == 1
    spoken.assert_not_called()


def test_rows_are_still_printed_when_speaking(capsys):
    with mock.patch("sonari.cli.doctor.doctor", return_value=_ROWS), \
         mock.patch("sonari.cli.doctor.should_speak", return_value=True), \
         mock.patch("sonari.cli.voiceout.speak"):
        cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "daemon socket" in out          # printed output is not replaced


def test_skips_the_daemon_when_the_speech_path_row_is_red():
    rows = [("speech path", False, "wedged")]
    with mock.patch("sonari.cli.doctor.doctor", return_value=rows), \
         mock.patch("sonari.cli.doctor.should_speak", return_value=True), \
         mock.patch("sonari.cli.voiceout.speak") as spoken:
        cli.main(["doctor"])
    assert spoken.call_args.kwargs["prefer_daemon"] is False
