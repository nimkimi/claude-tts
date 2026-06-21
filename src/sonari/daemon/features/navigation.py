from __future__ import annotations

from sonari.protocol import MsgType
from sonari.daemon.registry import handler


@handler(MsgType.NAV)
def on_nav(ctx, msg):
    fg = ctx.host.sessions.foreground()
    if fg is None:
        return None
    to = msg.get("to", "prev")
    if to in ("prev_response", "next_response"):
        ctx.host._nav_response(fg, to)
    else:
        ctx.host._nav(fg, to)
    return None
