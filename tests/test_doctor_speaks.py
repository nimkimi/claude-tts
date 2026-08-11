import argparse
from unittest import mock

from sonari.cli import doctor as doctor_cmd


def _args(**kw):
    ns = argparse.Namespace(speak=False, quiet=False)
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def test_speaks_when_stdout_is_a_tty():
    with mock.patch("sys.stdout.isatty", return_value=True):
        assert doctor_cmd.should_speak(_args()) is True


def test_silent_when_piped():
    with mock.patch("sys.stdout.isatty", return_value=False):
        assert doctor_cmd.should_speak(_args()) is False


def test_speak_flag_overrides_a_pipe():
    with mock.patch("sys.stdout.isatty", return_value=False):
        assert doctor_cmd.should_speak(_args(speak=True)) is True


def test_quiet_flag_overrides_a_tty():
    with mock.patch("sys.stdout.isatty", return_value=True):
        assert doctor_cmd.should_speak(_args(quiet=True)) is False


def test_quiet_wins_if_both_given():
    with mock.patch("sys.stdout.isatty", return_value=True):
        assert doctor_cmd.should_speak(_args(speak=True, quiet=True)) is False
