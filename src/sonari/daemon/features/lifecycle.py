from __future__ import annotations

from sonari.protocol import MsgType
from sonari.daemon.registry import handler


@handler(MsgType.SET_FOREGROUND)
@handler(MsgType.SESSION_START)
def on_set_foreground(ctx, msg):
    t = msg.get("type")          # KEEP LOCAL — there is NO ctx.type
    session = ctx.session
    ctx.host.sessions.set_foreground(session, cwd=msg.get("cwd"))
    if t == MsgType.SESSION_START:
        ctx.host.sessions.register(session, cwd=msg.get("cwd"))
        from sonari.sessions import Identity
        ctx.host.sessions.set_identity(session, Identity(
            term_program=msg.get("term_program", ""),
            tty=msg.get("tty", ""),
            iterm_session_id=msg.get("iterm_session_id", ""),
        ))
        ctx.host._maybe_guide_setup(session, msg.get("plugin_version", ""))
    return None


@handler(MsgType.SESSION_END)
def on_session_end(ctx, msg):
    session = ctx.session
    ctx.host.sessions.unregister(session)
    st = ctx.host._streams.get(session)
    if st is not None:
        ctx.host._drop_pending(st.queue.clear())
    ctx.host.history.reset(session)
    ctx.host._streams.pop(session, None)
    return None
