"""Install / uninstall / neural-voice lifecycle — the file-mutating, highest-care unit."""
from __future__ import annotations

import json
import os
import shutil
import sys

from sonari import paths
from sonari import keymap
from sonari import install_record
# `_platform` / `_build_raise_helper` are reached function-locally from sonari.cli
# where needed; `kokoro_provision` is imported locally inside the voices handlers
# (matching the current deferred-import style).


def _daemon_python(sup):
    """Interpreter the daemon should run on: the neural venv's Python when it is
    provisioned AND probes >=3.10, else the system Python from resolve_python().
    Deriving neural-state from the venv keeps re-runs of `sonari install` on the
    venv interpreter without a separate flag."""
    from sonari import kokoro_provision as kp
    if kp.neural_enabled():
        venv_py = paths.kokoro_venv_python()
        ver = sup._probe_python_version(venv_py)
        if ver is not None and ver >= (3, 10):
            return venv_py
    return sup.resolve_python()


def _read_plugin_version(plugin_root: str) -> str:
    """Return the plugin's declared version, or "" if unreadable.

    Reads <plugin_root>/.claude-plugin/plugin.json 'version'; falls back to the
    CLAUDE_PLUGIN_VERSION env var. Never raises (version is advisory).
    """
    path = os.path.join(plugin_root, ".claude-plugin", "plugin.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        v = data.get("version") if isinstance(data, dict) else None
        if isinstance(v, str) and v:
            return v
    except Exception:  # noqa: BLE001 - version is advisory, never fatal
        pass
    return os.environ.get("CLAUDE_PLUGIN_VERSION", "") or ""


def _copy_app(plugin_root: str) -> str:
    """Copy the plugin's sonari package into the stable APP_DIR. Returns APP_DIR.

    Overwrites on every install so a plugin update fully refreshes the copy
    (stale modules from a prior version do not linger). The daemon LaunchAgent
    points PYTHONPATH at APP_DIR, decoupling the long-lived daemon from the
    version-pinned marketplace cache.
    """
    app_dir = str(paths.APP_DIR)
    src_pkg = os.path.join(plugin_root, "src", "sonari")
    dst_pkg = os.path.join(app_dir, "sonari")
    os.makedirs(app_dir, exist_ok=True)
    if os.path.isdir(dst_pkg):
        shutil.rmtree(dst_pkg)
    shutil.copytree(src_pkg, dst_pkg)
    return app_dir


def install() -> int:
    """Install Sonari: resolve python, copy the runtime, write the install
    record, then delegate OS-specific autostart + hooks + launcher + hotkeys to
    the platform backend (macOS: LaunchAgents + hotkeyd; Windows: Task Scheduler
    + settings.json hooks + sonari.cmd)."""
    from sonari.cli import _platform, _build_raise_helper
    paths.ensure_sonari_dir()
    sup = _platform().supervisor

    # 1. Resolve the best Python >= 3.9 (FATAL if none).
    python = _daemon_python(sup)
    if python is None:
        print("No suitable Python >= 3.9 found. Install Python 3.9+ "
              "(python.org) and re-run: sonari install")
        return 1
    ver = sup._probe_python_version(python)
    py_ver = "{0}.{1}".format(*ver) if ver else "3.9"
    print(f"Using interpreter: {python} (Python {py_ver})")

    plugin_root = os.path.realpath(paths.repo_root())

    # 2. Copy the package into the stable APP_DIR (decouples the long-lived
    #    daemon from the version-pinned marketplace cache; see spec §3.B).
    try:
        app_dir = _copy_app(plugin_root)
    except OSError as exc:
        print(f"Could not copy the runtime to ~/.sonari/app: {exc}. "
              f"Check that ~/.sonari is writable.")
        return 1
    print(f"Copied runtime to: {app_dir}")

    # 3. Keymap setup.
    keymap.write_default_keymap_if_absent()
    keymap.write_resolved()

    # 4. Durable install record.
    plugin_version = _read_plugin_version(plugin_root)
    install_record.write_install_record(python=python, python_version=py_ver,
                                        plugin_root=plugin_root, app_path=app_dir,
                                        plugin_version=plugin_version)

    # 5. OS-specific autostart + hooks + launcher (the platform backend owns it).
    sup.install(python, app_dir)

    # 6. Global hotkeys. Each backend prints its own outcome (macOS: build +
    #    load hotkeyd; Windows: deferred to M3, announced in post_install_notes).
    hk_log = os.path.join(os.path.dirname(str(paths.LOG_PATH)), "hotkeyd.log")
    launchctl_fn = getattr(sup, "launchctl", None) or (lambda a: 0)
    _platform().hotkey.install(
        log_path=hk_log, agent_path=None, launchctl_fn=launchctl_fn)

    # 6b. Focus-follow: build the sonari-raise helper (macOS Automation dialog is
    #     one-time per app, with a safe voice fallback — no proactive grant needed).
    try:
        _build_raise_helper(_platform().raise_backend)
    except Exception:  # noqa: BLE001 - focus-follow setup must never break install
        pass

    # 7. Voice check (best_voice() is a display-name str on every platform).
    try:
        voice = _platform().tts.best_voice()
        print(f"Voice: {voice}." if voice else "Voice: default.")
    except Exception:  # noqa: BLE001 - voice check must never break install
        pass

    # 8. OS-specific next steps.
    sup.post_install_notes()
    return 0


def _cmd_install(_args) -> int:
    return install()


def uninstall() -> int:
    """Remove Sonari's OS autostart/hooks/launcher (via the platform backend)
    plus the shared runtime artifacts, PRESERVING config.json + keymap.json."""
    from sonari.cli import _platform
    sup = _platform().supervisor
    sup.uninstall()
    try:
        _platform().hotkey.uninstall()
    except Exception:  # noqa: BLE001 - hotkey teardown must never break uninstall
        pass

    # Spec §5.4: remove Sonari-owned runtime artifacts but PRESERVE the user's
    # keymap.json AND config.json so customizations survive uninstall/reinstall.
    sonari_dir = paths.SONARI_DIR
    artifacts = [
        paths.LOCK_PATH,
        paths.LOG_PATH,
        paths.HOTKEYD_RESOLVED_PATH,
        paths.INSTALL_RECORD_PATH,
        sonari_dir / "hotkeyd.log",
        sonari_dir / "faulthandler.log",
    ]
    for artifact in artifacts:
        if os.path.exists(str(artifact)):
            try:
                os.remove(str(artifact))
            except OSError:
                pass

    # Remove the stable app copy (spec §3.B). config.json + keymap.json live in
    # SONARI_DIR (not APP_DIR) and are preserved below.
    if os.path.isdir(str(paths.APP_DIR)):
        try:
            shutil.rmtree(str(paths.APP_DIR))
            print(f"Removed app copy: {paths.APP_DIR}")
        except OSError:
            pass

    preserved = []
    if os.path.exists(str(paths.KEYMAP_PATH)):
        preserved.append("keymap.json")
    if os.path.exists(str(paths.CONFIG_PATH)):
        preserved.append("config.json")
    if preserved:
        print(f"Preserved your settings: {', '.join(preserved)}")
    print(f"Removed Sonari runtime files from {sonari_dir} "
          f"(keymap.json and config.json left in place).")

    print("Done. Disable the 'sonari' plugin via /plugin in Claude Code if enabled.")
    return 0


def _cmd_uninstall(_args) -> int:
    return uninstall()


def _cmd_voices_install(_args) -> int:
    """Provision the Kokoro neural-voice venv, then re-wire the daemon onto it."""
    from sonari import kokoro_provision as kp
    paths.ensure_sonari_dir()
    print("Provisioning neural voices (uv + Kokoro, one-time ~316 MB download)…")
    try:
        # Pass repo src as PYTHONPATH so predownload_model can import sonari even
        # before install() populates APP_DIR (on a fresh machine APP_DIR is empty).
        kp.install_kokoro(os.path.join(paths.repo_root(), "src"))
    except Exception as exc:  # noqa: BLE001 - report, do not half-wire
        print(f"Neural-voice setup failed: {exc}", file=sys.stderr)
        kp.uninstall_kokoro()  # revert any half-built venv so neural_enabled() stays False
        return 1
    rc = install()  # re-wires the daemon onto the venv python (neural_enabled() now True)
    if rc == 0 and kp.neural_healthy(str(paths.APP_DIR)):
        print("Neural voices ready. Pick one with: sonari voice af_heart")
    return rc


def _cmd_voices_uninstall(_args) -> int:
    """Remove the neural venv and revert the daemon to system Python."""
    from sonari import kokoro_provision as kp
    kp.uninstall_kokoro()
    rc = install()  # neural_enabled() now False -> reverts to resolve_python()
    print("Neural voices removed; reverted to the system voice.")
    return rc
