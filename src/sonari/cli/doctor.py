"""Read-only health diagnostics (`sonari doctor`)."""
from __future__ import annotations

import os

from sonari import paths
from sonari import keymap
from sonari import install_record
from sonari.protocol import MsgType, PROTOCOL_VERSION


def should_speak(args) -> bool:
    """Speak when a human is at a terminal; stay silent when piped or scripted.

    The standard convention (git, ls, grep). It also keeps the test suite and
    every scripted invocation silent WITHOUT threading a flag through them.
    --quiet wins over --speak: the quieter reading of a contradictory command.
    """
    import sys
    if getattr(args, "quiet", False):
        return False
    if getattr(args, "speak", False):
        return True
    try:
        return bool(sys.stdout.isatty())
    except Exception:  # noqa: BLE001 - a detached stdout must not break doctor
        return False


def doctor() -> list:
    """Return a list of (check, ok, detail) health-check tuples."""
    from sonari.cli import _platform, _resolve_python
    results = []

    # Platform-specific rows supplied by the OS backend (macOS: say/afplay/
    # swiftc/LaunchAgents/...; Windows: schtasks/Task/pythonw/neural voice/...).
    results.extend(_platform().supervisor.doctor_rows())
    # Hotkey diagnostics (Windows: collisions + UIPI/elevation; macOS: none here).
    results.extend(_platform().hotkey.doctor_rows())
    results.extend(_platform().raise_backend.doctor_rows())

    # Neutral rows (portable, keep inline).
    try:
        paths.ensure_sonari_dir()
        writable = os.access(str(paths.SONARI_DIR), os.W_OK)
        results.append(("SONARI_DIR writable", writable,
                        str(paths.SONARI_DIR) if writable
                        else f"{paths.SONARI_DIR} is not writable"))
    except Exception as exc:  # noqa: BLE001
        results.append(("SONARI_DIR writable", False, f"error: {exc}"))

    try:
        from sonari import client
        reply = client.send({"v": PROTOCOL_VERSION, "type": MsgType.PING},
                            expect_reply=True)
        ok = bool(reply) and reply.get("ok") is True
        results.append(("daemon socket", ok,
                        "reachable" if ok else "no ok reply from daemon"))
    except Exception as exc:  # noqa: BLE001
        results.append(("daemon socket", False,
                        f"not reachable: {exc} (run 'sonari install')"))

    results.append(_platform().supervisor.hooks_doctor_row())

    try:
        keymap.resolve_keymap(keymap.load_keymap())
        results.append(("keymap resolves", True, "ok"))
    except Exception as exc:  # noqa: BLE001
        results.append(("keymap resolves", False, f"error: {exc}"))

    try:
        from sonari import kokoro_provision as kp
        if not kp.neural_enabled():
            results.append(("neural voices", True, "not installed (optional)"))
        elif kp.neural_healthy(str(paths.APP_DIR)):
            results.append(("neural voices", True,
                            f"ready ({paths.kokoro_venv_python()})"))
        else:
            results.append(("neural voices", False,
                            "venv present but Kokoro import failed — "
                            "re-run: sonari voices install"))
    except Exception as exc:  # noqa: BLE001 - doctor must never raise
        results.append(("neural voices", False, f"error: {exc}"))

    # python3 >= 3.9 resolved.
    try:
        py = _resolve_python()
        results.append(("python3", py is not None,
                        py or "no python3 >= 3.9 found"))
    except Exception as exc:  # noqa: BLE001
        results.append(("python3", False, f"error: {exc}"))

    # plugin path resolved (install.json -> src contains sonari/__init__.py).
    try:
        rec = install_record.read_install_record()
        app = rec.get("app_path") if rec else None
        init = os.path.join(app, "sonari", "__init__.py") if app else None
        ok = bool(init) and os.path.exists(init)
        results.append(("plugin path resolved", ok,
                        app if ok else "install.json missing or app copy has no "
                                       "sonari package (run 'sonari install')"))
    except Exception as exc:  # noqa: BLE001
        results.append(("plugin path resolved", False, f"error: {exc}"))

    return results


def _cmd_doctor(_args) -> int:
    rows = doctor()
    all_ok = True
    for check, ok, detail in rows:
        mark = "ok " if ok else "FAIL"
        print(f"[{mark}] {check}: {detail}")
        all_ok = all_ok and ok
    return 0 if all_ok else 1
