# tests/test_cli_focus_follow.py
from sonari import cli
from sonari import kokoro_provision as kp


class FakeRaise:
    def __init__(self, grant="granted"):
        self._grant = grant
        self.built = False
        self.checked = 0
        self.checked_terms = []  # records the term_program each check targeted

    def build(self):
        self.built = True
        return (True, "/tmp/sonari-raise")

    def check_grant(self, term_program="Apple_Terminal"):
        self.checked += 1
        self.checked_terms.append(term_program)
        return self._grant

    def doctor_rows(self, term_program=None):
        return [("focus-follow helper", True, "ok")]


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


def test_install_grant_step_builds_and_checks(monkeypatch):
    fake = FakeRaise(grant="denied")
    spoken = []
    # Patch out afplay so no real sound plays during the suite
    monkeypatch.setattr(cli.subprocess, "call", lambda *a, **k: 0)
    monkeypatch.setattr(cli, "_speak_once", lambda text: spoken.append(text))
    cli._focus_follow_setup(fake, focus_follow=True)
    assert fake.built is True
    assert fake.checked >= 1
    assert spoken  # spoke guidance because grant was 'denied'


def test_install_grant_step_silent_when_already_granted(monkeypatch):
    fake = FakeRaise(grant="granted")
    spoken = []
    monkeypatch.setattr(cli, "_speak_once", lambda text: spoken.append(text))
    cli._focus_follow_setup(fake, focus_follow=True)
    assert fake.built is True
    assert spoken == []  # no nagging when already granted


def test_install_grant_step_skipped_when_focus_follow_off(monkeypatch):
    fake = FakeRaise(grant="denied")
    spoken = []
    monkeypatch.setattr(cli, "_speak_once", lambda text: spoken.append(text))
    cli._focus_follow_setup(fake, focus_follow=False)
    assert fake.built is True   # still build the helper
    assert spoken == []         # but never prompt when the feature is off


def test_install_grant_step_targets_iterm_when_in_iterm(monkeypatch):
    # an iTerm user's grant probe must target iTerm2, not Terminal, so the
    # consent dialog that surfaces is iTerm2's.
    fake = FakeRaise(grant="denied")
    monkeypatch.setattr(cli.subprocess, "call", lambda *a, **k: 0)
    monkeypatch.setattr(cli, "_speak_once", lambda text: None)
    cli._focus_follow_setup(fake, focus_follow=True, term_program="iTerm.app")
    assert fake.checked_terms  # at least one grant check ran
    assert all(t == "iTerm.app" for t in fake.checked_terms)
