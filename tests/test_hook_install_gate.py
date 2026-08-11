"""bin/sonari-hook must not resurrect the daemon after uninstall."""
import runpy
import sys
from pathlib import Path
from unittest import mock

import pytest

# Absolute path (matches test_sonari_hook_bin.py's idiom) so this file is not
# cwd-dependent when pytest is invoked from elsewhere.
HOOK = str(Path(__file__).resolve().parent.parent / "bin" / "sonari-hook")


def _run_hook(monkeypatch, tmp_path, record_exists, app_dir_exists, stdin=b"{}"):
    # A REAL directory rather than a global os.path.isdir patch — runpy imports
    # a lot, and a blanket isdir patch would perturb unrelated import machinery.
    app_dir = tmp_path / "app"
    if app_dir_exists:
        app_dir.mkdir()
    monkeypatch.setattr(sys, "argv", ["sonari-hook", "Notification"])
    monkeypatch.setattr(sys, "stdin", mock.MagicMock(buffer=mock.MagicMock(
        read=lambda: stdin)))
    with mock.patch("sonari.client.ensure_daemon") as ensure, \
         mock.patch("sonari.client.send"), \
         mock.patch("sonari.install_record.read_install_record",
                    return_value={"app_path": "/a"} if record_exists else None), \
         mock.patch("sonari.paths.APP_DIR", app_dir), \
         mock.patch("sonari.hooks_entry.handle_event",
                    return_value=[{"type": "prose", "text": "hi"}]):
        # bin/sonari-hook's `if __name__ == "__main__":` block always calls
        # sys.exit(0) at the end (bin/sonari-hook:114) -- fine in the real
        # subprocess invocation, but runpy.run_path executes it in-process, so
        # the SystemExit propagates through pytest as a raised BaseException
        # unless caught here. Mocks retain their call records after the `with`
        # exits, so the caller can still assert on `ensure` afterward.
        with pytest.raises(SystemExit) as exc:
            runpy.run_path(HOOK, run_name="__main__")
        assert exc.value.code == 0
        return ensure


def test_no_spawn_only_when_BOTH_signals_say_uninstalled(monkeypatch, tmp_path):
    ensure = _run_hook(monkeypatch, tmp_path, record_exists=False,
                       app_dir_exists=False)
    ensure.assert_not_called()


def test_spawn_is_allowed_when_installed(monkeypatch, tmp_path):
    ensure = _run_hook(monkeypatch, tmp_path, record_exists=True,
                       app_dir_exists=True)
    ensure.assert_called_once()


def test_a_missing_record_alone_must_NOT_disable_lazy_relaunch(monkeypatch, tmp_path):
    """THE case that protects live users. install.json can go missing while
    Sonari is fully installed (doctor treats it as recoverable: "run sonari
    install"). Suppressing the spawn here would make the daemon's death
    permanent and silent — the exact defect D4 exists to close."""
    ensure = _run_hook(monkeypatch, tmp_path, record_exists=False,
                       app_dir_exists=True)
    ensure.assert_called_once()


def test_a_missing_app_dir_alone_still_spawns(monkeypatch, tmp_path):
    ensure = _run_hook(monkeypatch, tmp_path, record_exists=True,
                       app_dir_exists=False)
    ensure.assert_called_once()


def test_messages_are_still_sent_when_uninstalled_but_reachable(monkeypatch):
    """A daemon that is still up keeps receiving events; we only refuse to
    START one. Refusing to send would silence a working Sonari."""
    monkeypatch.setattr(sys, "argv", ["sonari-hook", "Notification"])
    monkeypatch.setattr(sys, "stdin", mock.MagicMock(buffer=mock.MagicMock(
        read=lambda: b"{}")))
    with mock.patch("sonari.client.ensure_daemon"), \
         mock.patch("sonari.client.send") as send, \
         mock.patch("sonari.install_record.read_install_record", return_value=None), \
         mock.patch("sonari.hooks_entry.handle_event",
                    return_value=[{"type": "prose", "text": "hi"}]):
        with pytest.raises(SystemExit) as exc:
            runpy.run_path(HOOK, run_name="__main__")
        assert exc.value.code == 0
    send.assert_called_once()
