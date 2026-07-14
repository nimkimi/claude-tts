"""Sonari wire protocol: newline-delimited JSON over a Unix stream socket."""
from __future__ import annotations

import json

PROTOCOL_VERSION = 1


class MsgType:
    PROSE = "prose"
    CHOICE = "choice"
    PLAN = "plan"
    TOOL = "tool_announce"
    PERMISSION = "permission"
    EARCON = "earcon"
    FLUSH = "flush"
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    SET_FOREGROUND = "set_foreground"
    STOP = "stop"
    SKIP = "skip"
    NAV = "nav"          # message-cursor navigation: msg["to"] in next|prev|first|last
    STOP_SESSION = "stop_session"   # ⌃⌘S: toggle a per-session stop (resume-from-spot, sticky)
    STOP_ALL = "stop_all"   # ⌃⌘M: stop EVERY session at once (one-way; return per-session via ⌃⌘S)
    JUMP_DECISION = "jump_decision"
    JUMP_WAITING = "jump_waiting"   # switch the voice to a waiting background session
    SET_RATE = "set_rate"
    SET_VERBOSITY = "set_verbosity"
    SET_VOICE = "set_voice"
    SET_MINQUEUE = "set_minqueue"
    STATUS = "status"
    PING = "ping"
    REREAD_OPTIONS = "reread_options"
    CYCLE_VERBOSITY = "cycle_verbosity"
    RELOAD_KEYMAP = "reload_keymap"   # re-read keymap.json + re-register hotkeys
    OS_FOCUS = "os_focus"   # focus-watcher: which terminal (tty / iterm id) has OS keyboard focus
    WHERE_AM_I = "where_am_i"   # ⌃⌘W: terse SPOKEN status (barge-in + interjection-resume)
    PERMISSION_REQUEST = "permission_request"   # PermissionRequest hook: BLOCKING ask; daemon replies {"decision": ...}
    ANSWER_PERMISSION = "answer_permission"     # ⌃⌘⏎/⌃⌘⎋: answer the focused session's pending decision (msg["behavior"])
    CHOOSER_STEP = "chooser_step"       # ⌃⌘Tab held: step the chooser (msg["direction"]); the first step opens
    CHOOSER_DIGIT = "chooser_digit"     # ⌃⌘1-9 while held: instant commit to that session number (msg["digit"])
    CHOOSER_COMMIT = "chooser_commit"   # chord released: land on the current candidate
    CHOOSER_CANCEL = "chooser_cancel"   # 30 s cap / hotkeyd death: restore the capture, move nothing


def encode(msg: dict) -> bytes:
    """Serialize a message dict to a newline-terminated UTF-8 byte line."""
    return (json.dumps(msg) + chr(10)).encode("utf-8")


def decode(line: bytes) -> dict:
    """Parse one newline-delimited JSON line back into a dict."""
    return json.loads(line)
