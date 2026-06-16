from unittest import mock

from sonari import cli
from tests._fakeplatform import fake_platform, FakeSupervisor, FakeHotkey, FakeTts


# --- install() dispatch contract (OS mechanics live in the backend tests) ---

def test_install_dispatches_through_platform(tmp_path, monkeypatch, capsys):
    sup = FakeSupervisor(python="/PY/pythonw.exe")
    hk = FakeHotkey(ok=False, detail="M3")
    pb = fake_platform(supervisor=sup, hotkey=hk, tts=FakeTts("Aria"))
    monkeypatch.setattr(cli, "_platform", lambda: pb)
    monkeypatch.setattr(cli, "_copy_app", lambda root: str(tmp_path / "app"))
    monkeypatch.setattr(cli, "_write_install_record", lambda **k: None)
    monkeypatch.setattr(cli, "_read_plugin_version", lambda root: "0.5.0")
    monkeypatch.setattr("sonari.keymap.write_default_keymap_if_absent", lambda: None)
    monkeypatch.setattr("sonari.keymap.write_resolved", lambda: None)
    monkeypatch.setattr("sonari.paths.ensure_sonari_dir", lambda: None)

    rc = cli.install()
    assert rc == 0
    # Supervisor got install(python, app_dir) then post_install_notes().
    assert ("install", "/PY/pythonw.exe", str(tmp_path / "app")) in sup.calls
    assert ("notes",) in sup.calls
    assert hk.calls and hk.calls[0][0] == "install"
    out = capsys.readouterr().out
    assert "Aria" in out                       # voice name (not an object repr)
    assert "hotkeys not enabled" in out         # hotkey.install returned False


def test_install_fatal_when_no_python_found(monkeypatch, capsys):
    sup = FakeSupervisor(python=None)
    monkeypatch.setattr(cli, "_platform", lambda: fake_platform(supervisor=sup))
    monkeypatch.setattr("sonari.paths.ensure_sonari_dir", lambda: None)
    rc = cli.install()
    assert rc == 1
    out = capsys.readouterr().out.lower()
    assert "python" in out and "3.9" in out
    assert ("install", mock.ANY, mock.ANY) not in sup.calls  # no install attempted


def test_install_copy_failure_is_fatal(monkeypatch, capsys):
    sup = FakeSupervisor(python="/PY/pythonw.exe")
    monkeypatch.setattr(cli, "_platform", lambda: fake_platform(supervisor=sup))
    monkeypatch.setattr(cli, "_copy_app", mock.Mock(side_effect=OSError("read-only")))
    monkeypatch.setattr("sonari.paths.ensure_sonari_dir", lambda: None)
    rc = cli.install()
    assert rc == 1
    assert sup.calls == []  # backend install never reached
    out = capsys.readouterr().out.lower()
    assert "~/.sonari" in out or ".sonari is writable" in out


def test_install_subcommand_invokes_install():
    with mock.patch("sonari.cli.install", return_value=0) as inst:
        rc = cli.main(["install"])
    inst.assert_called_once()
    assert rc == 0


# --- shared install-record / plugin-version / copy-app helpers (unchanged) ---

def test_write_install_record_writes_expected_keys(tmp_path):
    rec = tmp_path / "install.json"
    with mock.patch.object(cli.paths, "INSTALL_RECORD_PATH", rec):
        cli._write_install_record(
            python="/usr/bin/python3",
            python_version="3.9",
            plugin_root="/plug",
            app_path="/home/u/.sonari/app",
            plugin_version="0.4.0",
        )
    import json as _json
    data = _json.loads(rec.read_text())
    assert data["python"] == "/usr/bin/python3"
    assert data["python_version"] == "3.9"
    assert data["plugin_root"] == "/plug"
    assert data["app_path"] == "/home/u/.sonari/app"
    assert data["plugin_version"] == "0.4.0"
    assert "src" not in data  # src key was replaced by app_path
    assert "installed_at" in data and isinstance(data["installed_at"], str)


def test_read_plugin_version_reads_version_from_plugin_json(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_PLUGIN_VERSION", raising=False)
    pdir = tmp_path / ".claude-plugin"
    pdir.mkdir()
    (pdir / "plugin.json").write_text('{"name": "sonari", "version": "0.4.0"}')
    assert cli._read_plugin_version(str(tmp_path)) == "0.4.0"


def test_read_plugin_version_missing_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_PLUGIN_VERSION", raising=False)
    assert cli._read_plugin_version(str(tmp_path)) == ""


def test_read_plugin_version_corrupt_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_PLUGIN_VERSION", raising=False)
    pdir = tmp_path / ".claude-plugin"
    pdir.mkdir()
    (pdir / "plugin.json").write_text("{ not json")
    assert cli._read_plugin_version(str(tmp_path)) == ""


def test_read_plugin_version_falls_back_to_env(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_VERSION", "9.9.9")
    assert cli._read_plugin_version(str(tmp_path)) == "9.9.9"


def test_copy_app_copies_package_into_app_dir(tmp_path):
    plugin_root = tmp_path / "plugin"
    src_pkg = plugin_root / "src" / "sonari"
    src_pkg.mkdir(parents=True)
    (src_pkg / "__init__.py").write_text("# sonari\n")
    (src_pkg / "daemon.py").write_text("# daemon\n")
    app_dir = tmp_path / "home" / ".sonari" / "app"
    with mock.patch.object(cli.paths, "APP_DIR", app_dir):
        returned = cli._copy_app(str(plugin_root))
    assert returned == str(app_dir)
    assert (app_dir / "sonari" / "__init__.py").exists()
    assert (app_dir / "sonari" / "daemon.py").exists()


def test_copy_app_is_remove_then_copy_so_stale_modules_vanish(tmp_path):
    app_dir = tmp_path / "home" / ".sonari" / "app"

    def _root_with(modules):
        root = tmp_path / ("plug-" + "-".join(modules))
        pkg = root / "src" / "sonari"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("# pkg\n")
        for m in modules:
            (pkg / m).write_text("# " + m + "\n")
        return root

    first = _root_with(["old_only.py", "daemon.py"])
    second = _root_with(["daemon.py"])
    with mock.patch.object(cli.paths, "APP_DIR", app_dir):
        cli._copy_app(str(first))
        assert (app_dir / "sonari" / "old_only.py").exists()
        cli._copy_app(str(second))
    assert not (app_dir / "sonari" / "old_only.py").exists()
    assert (app_dir / "sonari" / "daemon.py").exists()


def test_copy_app_raises_oserror_when_source_missing(tmp_path):
    plugin_root = tmp_path / "plugin"  # no src/sonari beneath it
    app_dir = tmp_path / "home" / ".sonari" / "app"
    with mock.patch.object(cli.paths, "APP_DIR", app_dir):
        try:
            cli._copy_app(str(plugin_root))
            raised = False
        except OSError:
            raised = True
    assert raised is True
