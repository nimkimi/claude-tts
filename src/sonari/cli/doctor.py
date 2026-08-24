"""Read-only health diagnostics (`sonari doctor`)."""
from __future__ import annotations

import os
import time

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


def _keepalive_row(st):
    """Render STATUS's 'keepalive' field as a doctor row. idle/hold/disabled
    are healthy-by-policy (not errors — the manager is doing its job); only
    'degraded' (spawns kept dying) or a missing field (old daemon / STATUS
    unreachable) fails the row."""
    state = st.get("keepalive")
    if state in ("running", "idle", "hold", "disabled"):
        return ("keepalive", True, state)
    if state == "degraded":
        return ("keepalive", False,
                "degraded: silent-stream spawns kept dying; Bluetooth clipping is back")
    return ("keepalive", False, "daemon reported no keepalive state")


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
        if ok:
            # The PING already PROVED the daemon is reachable. Supervision is
            # advisory on top of that, so its probe gets its own guard: without
            # one, a raising launchctl (PermissionError — anything but the
            # FileNotFoundError the helper handles) fell to the outer except and
            # reported "not reachable ... (run 'sonari install')", sending an
            # eyes-free user to reinstall a system that was working fine.
            try:
                supervised = _platform().supervisor.daemon_is_launchd_job()
                detail = ("reachable (supervised by launchd)" if supervised else
                          "reachable, but running as a detached orphan — "
                          "'launchctl' cannot stop it")
            except Exception as exc:  # noqa: BLE001 - advisory, never fails the row
                detail = f"reachable; supervision unknown ({exc})"
            results.append(("daemon socket", True, detail))
        else:
            results.append(("daemon socket", False, "no ok reply from daemon"))
    except Exception as exc:  # noqa: BLE001
        results.append(("daemon socket", False,
                        f"not reachable: {exc} (run 'sonari install')"))

    # Speech-path liveness. PING is answered by the socket thread, so a wedged
    # speak loop still reports "reachable" — this row is the one that can tell
    # a wedge from silence. STATUS already carries both facts we need.
    WEDGE_S = 120.0
    try:
        from sonari import client
        st = client.send({"v": PROTOCOL_VERSION, "type": MsgType.STATUS},
                         expect_reply=True) or {}
        age = st.get("last_drain_age_s")
        claimed = bool(st.get("current_item"))
        if not claimed:
            results.append(("speech path", True,
                            "idle (nothing claimed by the speak loop)"))
        elif age is not None and age > WEDGE_S:
            results.append(("speech path", False,
                            f"wedged: an utterance has been claimed for "
                            f"{age:.0f}s without draining"))
        else:
            results.append(("speech path", True, "draining normally"))
    except Exception as exc:  # noqa: BLE001 - doctor must never raise
        results.append(("speech path", False, f"cannot read daemon status: {exc}"))

    # Bluetooth keep-alive state. idle/hold/disabled are healthy-by-policy;
    # only 'degraded' (the silent-stream spawn giving up) or a missing field
    # (old daemon / STATUS unreachable) fails the row.
    try:
        results.append(_keepalive_row(st))
    except Exception as exc:  # noqa: BLE001 - doctor must never raise
        results.append(("keepalive", False, f"error: {exc}"))

    # Restore health (P17): you can lose the whole backlog to the crash/upgrade
    # path and still get an all-green doctor today.
    try:
        from sonari.daemon import persistence
        state_path = paths.STATE_PATH
        if not os.path.exists(str(state_path)):
            results.append(("restore health", True,
                            "no saved state yet (nothing to restore)"))
        else:
            import json as _json
            with open(str(state_path), "r", encoding="utf-8") as fh:
                blob = _json.load(fh)
            ver = blob.get("version")
            n = len(blob.get("sessions") or {})
            age_h = (time.time() - os.path.getmtime(str(state_path))) / 3600.0
            if ver != persistence.STATE_VERSION:
                results.append(("restore health", False,
                                f"state version {ver} != {persistence.STATE_VERSION}; "
                                f"the restored pile will be dropped at next boot"))
            else:
                results.append(("restore health", True,
                                f"{n} session(s), saved {age_h:.1f}h ago"))
    except Exception as exc:  # noqa: BLE001 - doctor must never raise
        results.append(("restore health", False, f"unreadable: {exc}"))

    # Did the daemon die natively since it last armed? bootstrap.py opens the
    # log mode 'w', so anything after the arming line belongs to THIS boot.
    try:
        fl = str(paths.FAULTLOG_PATH)
        if not os.path.exists(fl):
            results.append(("fault log", True, "no crash log"))
        else:
            with open(fl, "r", encoding="utf-8", errors="replace") as fh:
                body = fh.read()
            after = body.split("===", 2)[-1] if "===" in body else body
            if after.strip():
                results.append(("fault log", False,
                                f"a native crash was recorded — see {fl}"))
            else:
                results.append(("fault log", True, "armed, no crash recorded"))
    except Exception as exc:  # noqa: BLE001 - doctor must never raise
        results.append(("fault log", False, f"unreadable: {exc}"))

    results.append(_platform().supervisor.hooks_doctor_row())

    try:
        results.append(_platform().supervisor.reachability_row())
    except Exception as exc:  # noqa: BLE001 - doctor must never raise
        results.append(("reachability", False, f"error: {exc}"))

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


def _cmd_doctor(args=None) -> int:
    from sonari.cli import voiceout
    from sonari.cli.verdict import verdict

    rows = doctor()
    all_ok = True
    speech_path_ok = True
    for check, ok, detail in rows:
        mark = "ok " if ok else "FAIL"
        print(f"[{mark}] {check}: {detail}")
        all_ok = all_ok and ok
        if check == "speech path" and not ok:
            speech_path_ok = False

    if should_speak(args):
        # A red speech-path row means the daemon cannot carry the sentence;
        # go straight to the fallback rather than waiting out a socket timeout.
        voiceout.speak(verdict(rows), prefer_daemon=speech_path_ok)
    return 0 if all_ok else 1
