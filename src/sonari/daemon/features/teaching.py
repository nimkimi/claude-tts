"""Self-teaching: learn mode (SP-D1). The daemon teaches its own cockpit.

Learn-mode interception itself lives in SpeechDaemon._dispatch_hotkey (it must
see the raw hotkey message before dispatch); this module owns the toggle."""
from __future__ import annotations

from sonari.protocol import MsgType
from sonari.daemon.registry import handler

# wording provisional, pending owner ear-pass
LEARN_ON = ("Learn mode. Press any Sonari key to hear what it does. "
            "Press the same key again to exit.")
LEARN_OFF = "Learn mode off."


@handler(MsgType.LEARN_MODE)
def on_learn_mode(ctx, msg):
    host = ctx.host
    entering = not host._learn_mode
    host._set_learn_mode(entering)
    ws = host.sessions.workspace()
    if ws is not None:
        host._enqueue(ws, "prose", LEARN_ON if entering else LEARN_OFF, False,
                      mute_exempt=True, pause_exempt=True)
    return None
