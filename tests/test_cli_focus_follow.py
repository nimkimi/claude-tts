# tests/test_cli_focus_follow.py
from sonari import cli
from sonari import kokoro_provision as kp


class FakeRaise:
    def __init__(self):
        self.built = False

    def build(self):
        self.built = True
        return (True, "/tmp/sonari-raise")

    def doctor_rows(self):
        return [("focus-follow helper", True, "ok")]


def test_build_raise_helper_calls_build_and_prints(capsys):
    fake = FakeRaise()
    cli._build_raise_helper(fake)
    assert fake.built is True
    out = capsys.readouterr().out
    assert "focus-follow helper" in out
    assert "Allow" in out  # the one-time dialog note


def test_doctor_includes_raise_rows(monkeypatch):
    fake = FakeRaise()

    class P:
        raise_backend = fake

        class supervisor:
            @staticmethod
            def doctor_rows(): return []

            @staticmethod
            def hooks_doctor_row(): return ("hooks", True, "ok")

        class hotkey:
            @staticmethod
            def doctor_rows(): return []

    monkeypatch.setattr(cli, "_platform", lambda: P)
    # avoid the daemon-socket + neural rows touching the real system
    monkeypatch.setattr(cli, "_send", lambda *a, **k: {"ok": True})
    # pin neural_enabled to avoid filesystem touches
    monkeypatch.setattr(kp, "neural_enabled", lambda: False)
    rows = cli.doctor()
    assert ("focus-follow helper", True, "ok") in rows
