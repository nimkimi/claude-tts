from sonari.cli import checkmeta


def test_known_check_has_a_short_spoken_name():
    assert checkmeta.spoken_name("SONARI_DIR writable") == "storage"


def test_unknown_check_falls_back_to_its_printed_name():
    assert checkmeta.spoken_name("some new row") == "some new row"


def test_neural_voices_is_advisory_not_a_failure():
    assert checkmeta.is_warn("neural voices") is True


def test_daemon_socket_is_a_hard_failure():
    assert checkmeta.is_warn("daemon socket") is False
