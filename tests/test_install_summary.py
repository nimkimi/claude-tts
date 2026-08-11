from unittest import mock

from sonari.cli import install as install_cmd


def test_install_speaks_the_same_verdict_doctor_uses():
    rows = [("say", True, "ok"), ("daemon socket", False, "down")]
    with mock.patch("sonari.cli.doctor.doctor", return_value=rows), \
         mock.patch("sonari.cli.voiceout.speak") as spoken, \
         mock.patch("sys.stdout.isatty", return_value=True), \
         mock.patch("sonari.cli._platform"), \
         mock.patch("sonari.cli.install._install_body", create=True):
        install_cmd.install()
    assert "unhealthy" in spoken.call_args[0][0]
    assert "daemon socket" in spoken.call_args[0][0]


def test_install_is_silent_when_not_interactive():
    with mock.patch("sonari.cli.doctor.doctor", return_value=[("say", True, "ok")]), \
         mock.patch("sonari.cli.voiceout.speak") as spoken, \
         mock.patch("sys.stdout.isatty", return_value=False), \
         mock.patch("sonari.cli._platform"), \
         mock.patch("sonari.cli.install._install_body", create=True):
        install_cmd.install()
    spoken.assert_not_called()
