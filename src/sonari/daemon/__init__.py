from __future__ import annotations

from sonari.daemon.host import SpeechDaemon
from sonari.daemon.bootstrap import main, ensure_running
from sonari.daemon.registry import assert_complete
from sonari.protocol import MsgType

# Guard: every MsgType must have a registered handler — a dropped @handler
# registration becomes an import-time error, not a silent runtime no-op.
# MsgType is a plain class (not Enum), so we enumerate every known key explicitly.
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
    MsgType.STOP_SESSION,
    MsgType.STOP_ALL,
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
    MsgType.OS_FOCUS,
    MsgType.WHERE_AM_I,
    MsgType.PERMISSION_REQUEST,
    MsgType.ANSWER_PERMISSION,
    MsgType.CHOOSER_STEP,
    MsgType.CHOOSER_DIGIT,
    MsgType.CHOOSER_COMMIT,
    MsgType.CHOOSER_CANCEL,
    MsgType.REPEAT_LAST,
    MsgType.SKIP_PILE,     # close the pre-existing SP4 omission (handler at playback.py:31)
    MsgType.CATCH_UP,
    MsgType.CATCHUP_RESULT,
    MsgType.LEARN_MODE,    # SP-D1 teaching handler at features/teaching.py
    MsgType.QUERY_ACTIONS,  # "what can I do" teaching handler at features/teaching.py
])

__all__ = ["SpeechDaemon", "main", "ensure_running"]
