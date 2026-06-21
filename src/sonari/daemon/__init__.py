from __future__ import annotations

from sonari.daemon.host import SpeechDaemon
from sonari.daemon.bootstrap import main, ensure_running
from sonari.daemon.registry import assert_complete
from sonari.protocol import MsgType

# Guard: every MsgType must have a registered handler — a dropped @handler
# registration becomes an import-time error, not a silent runtime no-op.
# MsgType is a plain class (not Enum), so we enumerate all 27 known keys explicitly.
assert_complete([
    MsgType.PROSE,
    MsgType.CHOICE,
    MsgType.PLAN,
    MsgType.PERMISSION,
    MsgType.EARCON,
    MsgType.FLUSH,
    MsgType.TOOL,
    MsgType.SESSION_START,
    MsgType.SESSION_END,
    MsgType.SET_FOREGROUND,
    MsgType.STOP,
    MsgType.SKIP,
    MsgType.NAV,
    MsgType.PAUSE,
    MsgType.MUTE,
    MsgType.PIN_TOGGLE,
    MsgType.JUMP_DECISION,
    MsgType.JUMP_WAITING,
    MsgType.SET_RATE,
    MsgType.SET_VERBOSITY,
    MsgType.SET_VOICE,
    MsgType.SET_MINQUEUE,
    MsgType.STATUS,
    MsgType.PING,
    MsgType.REREAD_OPTIONS,
    MsgType.CYCLE_VERBOSITY,
    MsgType.RELOAD_KEYMAP,
])

__all__ = ["SpeechDaemon", "main", "ensure_running"]
