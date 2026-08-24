"""Control subcommands: build a protocol message and hand it to the daemon."""
from __future__ import annotations

import json
import sys

from sonari.protocol import MsgType, PROTOCOL_VERSION
from sonari import keymap

# _send / _platform live in sonari.cli; import them INSIDE the functions that
# use them (function-local) so monkeypatch.setattr(cli, "_platform", ...) still
# intercepts (Stage 3 spec §7 rule 8) and to avoid an import cycle.


def _cmd_status(_args) -> int:
    from sonari.cli import _send
    reply = _send({"v": PROTOCOL_VERSION, "type": MsgType.STATUS},
                  expect_reply=True)
    if reply is None:
        print("sonari: no response from daemon (is it running?)")
        return 1
    # Raw JSON first — preserves every key for scripting / copy-paste diagnosis.
    print(json.dumps(reply, indent=2))
    # Human-readable diagnostic summary for the new DIAG-3 fields.
    # All lookups use .get() so this renders cleanly against an older daemon that
    # does not yet return these keys.
    uptime_s = reply.get("uptime_s")
    session_count = reply.get("session_count")
    last_drain_age_s = reply.get("last_drain_age_s")
    current_item = reply.get("current_item")
    sessions = reply.get("sessions")  # None when key absent (old daemon)
    if uptime_s is not None or session_count is not None:
        uptime_str = "{:.1f}s".format(uptime_s) if uptime_s is not None else "?"
        count_str = str(session_count) if session_count is not None else "?"
        speaking_str = ("yes" if current_item else "no") if current_item is not None else "?"
        voice_state = reply.get("voice_state")
        vs_str = voice_state if voice_state is not None else "?"
        print("---")
        print("Uptime: {0}  |  Sessions: {1}  |  Speaking: {2}  |  Voice: {3}".format(
            uptime_str, count_str, speaking_str, vs_str))
        # last_drain_age_s key present but None = no drain yet; key absent = old daemon.
        if "last_drain_age_s" in reply:
            if last_drain_age_s is None:
                print("Heartbeat: no drain yet")
            else:
                print("Heartbeat: {:.2f}s ago".format(last_drain_age_s))
        if sessions:
            for s in sessions:
                state = "stopped" if s.get("stopped") else "active"
                print("  {0}  queue={1}  [{2}]".format(
                    s.get("session", "?"), s.get("queue_len", 0), state))
    return 0


def _cmd_verbosity(args) -> int:
    from sonari.cli import _send
    _send({"v": PROTOCOL_VERSION, "type": MsgType.SET_VERBOSITY,
           "verbosity": args.level})
    return 0


def _cmd_rate(args) -> int:
    from sonari.cli import _send
    _send({"v": PROTOCOL_VERSION, "type": MsgType.SET_RATE, "rate": args.wpm})
    return 0


def _cmd_minqueue(args) -> int:
    from sonari.cli import _send
    _send({"v": PROTOCOL_VERSION, "type": MsgType.SET_MINQUEUE, "minqueue": args.n})
    print("Min queue set to {0}.".format(args.n))
    return 0


def _cmd_keepalive(args) -> int:
    from sonari.cli import _send
    enabled = args.state == "on"
    _send({"v": PROTOCOL_VERSION, "type": MsgType.SET_KEEPALIVE, "enabled": enabled})
    print("keepalive {0}".format("on" if enabled else "off"))
    return 0


def _cmd_voice(args) -> int:
    from sonari.cli import _platform
    from sonari.cli import _send
    # No name -> list the installed voices so the user can pick one (changes
    # nothing). A name -> set it; the name may be several words ("Microsoft David"),
    # so join them rather than requiring the user to quote.
    name = " ".join(args.name).strip() if args.name else ""
    if not name:
        try:
            voices = _platform().tts.list_voices()
        except Exception as exc:  # noqa: BLE001 - listing must not crash the CLI
            print(f"sonari: could not list voices: {exc}", file=sys.stderr)
            return 1
        if not voices:
            print("No voices installed.")
            return 0
        print("Installed voices (set with: sonari voice <name>):")
        for v in voices:
            print("  " + (getattr(v, "display_name", None) or str(v)))
        return 0
    _send({"v": PROTOCOL_VERSION, "type": MsgType.SET_VOICE, "voice": name})
    return 0


def _cmd_stop(_args) -> int:
    from sonari.cli import _send
    _send({"v": PROTOCOL_VERSION, "type": MsgType.STOP})
    return 0


def _cmd_skip(_args) -> int:
    from sonari.cli import _send
    _send({"v": PROTOCOL_VERSION, "type": MsgType.SKIP})
    return 0


def _combo_label(modifiers: int, key_code: int) -> str:
    from sonari.cli import _platform
    return _platform().hotkey.display_combo(modifiers, key_code)


def _cmd_keymap(args) -> int:
    from sonari.cli import _send
    action = getattr(args, "action", None)
    value = getattr(args, "value", None)
    # `keymap <action> clear|none` -> unbind that action.
    if action:
        if value not in ("clear", "none"):
            print("sonari: usage: sonari keymap [<action> clear]", file=sys.stderr)
            return 2
        try:
            keymap.unbind_action(action)
        except ValueError as exc:
            print(f"sonari: {exc}", file=sys.stderr)
            return 1
        try:                                  # apply live; harmless if daemon is down
            _send({"v": PROTOCOL_VERSION, "type": MsgType.RELOAD_KEYMAP})
        except Exception:  # noqa: BLE001 - the keymap.json write is what matters
            pass
        print(f"Unbound {action}.")
        return 0
    # No args: list EVERY action against the user's ACTUAL keymap (defaults
    # merged with any persisted overrides) — bound ones show their combo, the
    # rest "unbound".
    for row in keymap.hotkey_rows(keymap.load_keymap()):
        if row["combo"]:
            where = row["combo"]
        elif row["proposed"]:
            where = "unbound (suggested {0})".format(row["proposed"])
        else:
            where = "unbound"
        print("{0} — {1}   [{2}]".format(row["label"], where, row["action"]))
    return 0
