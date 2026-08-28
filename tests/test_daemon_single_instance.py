from unittest import mock

import sonari.daemon.bootstrap as daemon_mod


def test_main_exits_without_building_when_socket_connectable():
    with mock.patch("sonari.daemon.bootstrap.socket_connectable", return_value=True), \
         mock.patch.object(daemon_mod.SpeechDaemon, "run") as run, \
         mock.patch("sonari.daemon.bootstrap.load_config", return_value={}):
        daemon_mod.main()
    run.assert_not_called()


def test_main_builds_and_runs_when_socket_not_connectable():
    # See test_daemon_main.py: the real SpearconCache built here would shell
    # out to `say -v ?`, which constraint 3 forbids. No voice configured, so
    # the stubbed answer is the same answer.
    with mock.patch("sonari.daemon.bootstrap.socket_connectable", return_value=False), \
         mock.patch.object(daemon_mod.SpeechDaemon, "run") as run, \
         mock.patch("sonari.spearcon._default_voice_lister", return_value=""), \
         mock.patch("sonari.daemon.bootstrap.load_config", return_value={}), \
         mock.patch("sonari.speaker.Speaker"):
        daemon_mod.main()
    run.assert_called_once()
