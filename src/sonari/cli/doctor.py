"""Read-only health diagnostics (`sonari doctor`)."""
from __future__ import annotations

import os
import time

from sonari import paths
from sonari import keymap
from sonari import install_record
from sonari.protocol import MsgType, PROTOCOL_VERSION

# I3: how long a recorded speak failure (SPEAK_FAIL_MEMO_PATH) still FAILs the
# speech-path row. See the full justification inline in doctor(), next to
# where this is read — module-level so tests derive the window from source
# rather than pinning the literal (tests/test_uninstall_teardown.py's style).
SPEAK_FAIL_FRESH_S = 24 * 3600.0

WEDGE_S = 120.0        # lifted from doctor()'s locals, unchanged; the
                       # claimed-but-stalled branch below needs it at module
                       # scope now that the branch itself lives out here
WEDGE_HOLD_S = 300.0   # nothing claimed, yet live streams are holding

KEEPALIVE_MAX_PLAYER_AGE_S = 305.0   # SILENCE_S + OVERLAP_S


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
    'degraded' (spawns kept dying), a stalled overlap chain, or a missing
    field (old daemon / STATUS unreachable) fails the row."""
    state = st.get("keepalive")
    age = st.get("keepalive_oldest_player_age_s")
    if age is not None and age > KEEPALIVE_MAX_PLAYER_AGE_S:
        return ("keepalive", False,
                "the same silent player has been holding the audio device for "
                "{0} minutes - the overlap chain stalled and Bluetooth "
                "clipping will come back. Run: sonari keepalive off, then "
                "sonari keepalive on.".format(int(age // 60)))
    if state in ("running", "idle", "hold", "disabled"):
        return ("keepalive", True, state)
    if state == "degraded":
        return ("keepalive", False,
                "degraded: silent-stream spawns kept dying; Bluetooth clipping "
                "is back — run 'sonari keepalive off' then 'on' to retry")
    return ("keepalive", False, "daemon reported no keepalive state")


def _speech_path_row(st, memo_row):
    """Render the claimed/drained STATUS facts as the speech-path row.

    Two independent wedge shapes share this row: nothing CLAIMED while live
    streams hold queued items and nothing drains (the assembler wedge — an
    unterminated streamed block leaves the keep-going gate shut and every
    other session silenced indefinitely), and something CLAIMED that never
    drains (the existing claimed-and-stalled shape). memo_row — a recorded
    SpeakFailure, see doctor()'s own comment where it is built — wins over
    both, since it names a confirmed failure rather than an inferred one.
    """
    if memo_row is not None:
        return memo_row
    age = st.get("last_drain_age_s")
    claimed = bool(st.get("current_item"))
    if not claimed:
        # Stop-all and quiet-hold are excluded by voice_state; a STARVED
        # session's own backlog by `not stopped`; dead-session backlog by
        # `live`. A genuinely idle daemon has no queued items and stays green
        # below.
        #
        # `voice_state` does NOT exclude every deliberate mute, and believing
        # it did was this row's one shipped defect. ⌃⌘D and crossed nav are
        # ratified "deliberate re-engage" lifts: they set voice_state =
        # "flowing" and then focus() the voice ONTO a stopped stream, so the
        # loop holds every tick and the starved sessions — which are not
        # themselves stopped — all count. That is a mute, not a wedge, and
        # `speaker_held` is the only field that tells the two apart.
        held = [s for s in st.get("sessions", [])
                if s.get("queue_len") and not s.get("stopped")
                and s.get("live")]
        n = sum(s["queue_len"] for s in held)
        if (held and st.get("voice_state") == "flowing"
                and (age is None or age > WEDGE_HOLD_S)):
            if st.get("speaker_held"):
                # Absent on a pre-0.11.1 daemon, which reads falsy and keeps
                # the old behaviour rather than inventing a green.
                return ("speech path", True,
                        "held (the voice is on a muted session - un-mute it "
                        "to resume)")
            # Today this renders GREEN. It is the state the assembler wedge
            # produces: has_pending() stays true forever, the keep-going gate
            # never opens, and every other session is silenced indefinitely.
            # `age is None` is the never-drained-since-boot wedge: the loop
            # jammed on its FIRST item, so there is no measured age to name.
            # Rendering it as "0 minutes" would say "nothing has been spoken
            # for 0 minutes - the speak loop is stuck" in one breath.
            since = ("since the daemon started" if age is None
                     else "for {0} minutes".format(int(age // 60)))
            return ("speech path", False,
                    "{0} items are waiting in {1} live sessions and nothing "
                    "has been spoken {2} - the speak loop is "
                    "stuck, not idle. Restart it: sonari install.".format(
                        n, len(held), since))
        return ("speech path", True, "idle (nothing claimed by the speak loop)")
    if age is not None and age > WEDGE_S:
        # I2: `age` is last_drain_age_s — time since anything last DRAINED,
        # NOT how long the current item has been claimed. STATUS carries no
        # claim timestamp, so name what was measured: after a quiet spell the
        # drain age is already large the instant the next item is claimed.
        return ("speech path", False,
                f"wedged: nothing has drained for {age:.0f}s "
                f"while an utterance is claimed")
    return ("speech path", True, "draining normally")


def _voice_row(st, list_voices=None):
    """The configured voice must actually be installed.

    The row this replaces reported ("enhanced voice", bool(best_voice()), ...)
    and best_voice() hard-codes "Samantha" as its last resort on every path --
    so it was green under every condition, and reported a voice the owner does
    not use. Meanwhile a config voice that is gone makes `say` exit non-zero on
    EVERY utterance: total silence, green doctor.
    """
    voice = st.get("voice")
    if voice is None:
        from sonari.config import load_config
        try:
            voice = load_config().get("voice")
        except Exception:
            voice = None
    if not voice:
        return ("voice", True, "system default")
    if list_voices is None:
        from sonari.platform import get_platform
        list_voices = get_platform().tts.list_voices
    try:
        installed = list(list_voices() or [])
    except Exception:
        installed = []
    if not installed:
        # Fail open. A doctor that cries wolf about a working voice is worse
        # than one that stays quiet.
        return ("voice", True, "voice listing unavailable")
    if voice in installed:
        return ("voice", True, voice)
    return ("voice", False,
            "the configured voice, {0}, is not installed - every utterance "
            "will fail. Run: sonari voice, to hear what is installed, then: "
            "sonari voice {1}.".format(voice, "<a name>"))


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
    # I3: a broken audio device (say/afplay exits nonzero, e.g. AudioQueueStart
    # failures) plays NOTHING and drains promptly doing it — STATUS alone can't
    # see it (last_drain_age_s advances on every drain, completed or not; a
    # daemon that fails every utterance instantly still looks "draining
    # normally"). Speaker.speak() raises SpeakFailure for that shape, which
    # SpeechDaemon._signal_speak_failure (host.py) records to
    # SPEAK_FAIL_MEMO_PATH (mtime-based, matching DAEMON_FAIL_MEMO_PATH).
    # Unlike that 30s memo — sized only to skip a redundant retry-timeout
    # within the same hook burst — this one has to survive until a silent,
    # eyes-free user gets suspicious enough to run `sonari doctor`, which can
    # be a long time after the actual failure. 24h mirrors this codebase's
    # existing "is a recorded fact still meaningful" boundary (persistence's
    # restore_max_age_hours default, below) and is cleared early anyway the
    # moment any utterance next completes (SpeechDaemon.note_spoken) — it only
    # lingers this long when nothing has spoken successfully since.
    # Bound BEFORE the try: an unreachable daemon (the exact case doctor exists
    # for) raises inside it, and the keepalive row below reads `st`. Unbound, it
    # raised UnboundLocalError there and the row rendered "error: cannot access
    # local variable 'st'..." — a sentence _cmd_doctor PRINTS as that row's
    # detail. (Not spoken: verdict() names only the failing CHECKS, so the ear
    # gets "1 check failed: keepalive" and the diagnosis stays on screen — which
    # for an eyes-free user is the same as not being told at all.)
    st = {}
    # Read the memo BEFORE the STATUS probe rather than inside it. The memo is
    # a local file; STATUS is a socket round-trip that an unreachable daemon —
    # a state a dead audio path can itself produce — makes raise. Read inside,
    # the whole chain below was skipped and the row said only "cannot read
    # daemon status", withholding the on-disk record of WHY that was sitting
    # right there. Built once here and appended from both arms so the wording
    # stays identical whichever way STATUS goes.
    fail_age = None
    try:
        fail_age = time.time() - paths.SPEAK_FAIL_MEMO_PATH.stat().st_mtime
    except OSError:
        pass              # no memo, or can't read it -> behave as if there's none
    memo_row = None
    if fail_age is not None and 0 <= fail_age < SPEAK_FAIL_FRESH_S:
        mins = int(fail_age // 60)
        memo_row = ("speech path", False,
                    f"speech failure recorded {mins}m ago — "
                    f"see {paths.SPEAK_FAIL_MEMO_PATH}")
    try:
        from sonari import client
        st = client.send({"v": PROTOCOL_VERSION, "type": MsgType.STATUS},
                         expect_reply=True) or {}
        results.append(_speech_path_row(st, memo_row))
    except Exception as exc:  # noqa: BLE001 - doctor must never raise
        if memo_row is not None:
            results.append(memo_row)
        else:
            results.append(("speech path", False,
                            f"cannot read daemon status: {exc}"))

    # Bluetooth keep-alive state. idle/hold/disabled are healthy-by-policy;
    # only 'degraded' (the silent-stream spawn giving up), a stalled overlap
    # chain, or a missing field (old daemon / STATUS unreachable) fails the row.
    try:
        results.append(_keepalive_row(st))
    except Exception as exc:  # noqa: BLE001 - doctor must never raise
        results.append(("keepalive", False, f"error: {exc}"))

    # The configured voice must actually be installed, or every utterance
    # fails silently (say exits non-zero; STATUS still looks "draining").
    try:
        results.append(_voice_row(st))
    except Exception as exc:  # noqa: BLE001 - doctor must never raise
        results.append(("voice", False, f"error: {exc}"))

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
