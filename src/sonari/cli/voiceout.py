"""Delivery for CLI-originated speech (the doctor verdict, install/uninstall).

Daemon-first so the utterance obeys D8's atomic cue+speech contract and can
never interleave with live session speech; direct `say` when the daemon is the
thing being diagnosed. The direct path is the LAST resort: it is best-effort
and silent on its own failure, because it has nothing to escalate to.

It deliberately shells out raw rather than reusing platform.macos.tts or the
daemon's own _alarm_popen — every one of those lives behind the daemon or its
config, and this path exists precisely for when the daemon is dead. Same
reasoning hotkeyd's witness alarm states for its own raw shell-out.
"""
from __future__ import annotations

import subprocess


def speak_direct(text: str) -> bool:
    """Speak *text* with a raw `say`, bypassing the daemon. True iff spawned.

    `--` ends option parsing so a verdict starting with '-' is spoken rather
    than rejected as an unknown option (the tts.py:194 lesson). Never raises.
    """
    if not text:
        return False
    try:
        subprocess.Popen(["say", "--", text])
        return True
    except Exception:  # noqa: BLE001 - the last resort cannot itself escalate
        return False


def speak(text: str, *, prefer_daemon: bool = True) -> str:
    """Speak *text*, daemon-first. Returns "daemon" | "direct" | "silent".

    prefer_daemon=False is for callers that ALREADY know the speech path is
    broken (a red speech-path row, a stopped daemon) — it skips a pointless
    socket timeout rather than changing the policy.
    """
    if not text:
        return "silent"
    if prefer_daemon:
        try:
            from sonari import client
            from sonari.protocol import MsgType, PROTOCOL_VERSION
            client.send({"v": PROTOCOL_VERSION, "type": MsgType.PROSE,
                         "text": text})
            return "daemon"
        except Exception:  # noqa: BLE001 - any daemon failure means fall back
            pass
    return "direct" if speak_direct(text) else "silent"
