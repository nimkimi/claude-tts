from __future__ import annotations

from sonari.protocol import MsgType
from sonari.daemon.registry import handler


@handler(MsgType.CHOICE)
def on_choice(ctx, msg):
    session = ctx.session                 # was: msg.get("session", "")
    verbosity = ctx.verbosity             # was: self.config.get("verbosity", "everything")
    text = ctx.host._choice_text(msg)
    extras = [e for e in (
        ctx.host._choice_notes(msg),
        ctx.host._selection_cue(session, verbosity),
    ) if e]
    if extras:
        text = "{0} {1}".format(text, " ".join(extras))
    ctx.host._stream(session).options = text
    entry = ctx.host.history.record(session, "choice", text)
    ctx.host.history.end_message(session)
    # The flip: gating moved to playback. Every session enqueues its own
    # decision into its own stream; the foreground-driven loop voices it.
    ctx.host._flush_prose_buffer(session)   # prose before the question
    ctx.host._enqueue(session, "choice", text, True, entry=entry)
    return None


@handler(MsgType.PLAN)
def on_plan(ctx, msg):
    session = ctx.session                 # was: msg.get("session", "")
    verbosity = ctx.verbosity             # was: self.config.get("verbosity", "everything")
    text = ctx.host._plan_text(msg)
    cue = ctx.host._selection_cue(session, verbosity)
    if cue:
        text = "{0} {1}".format(text, cue)
    ctx.host._stream(session).options = text
    entry = ctx.host.history.record(session, "plan", text)
    ctx.host.history.end_message(session)
    # The flip: enqueue unconditionally into this session's own stream.
    ctx.host._flush_prose_buffer(session)   # prose before the plan
    ctx.host._enqueue(session, "plan", text, True, entry=entry)
    return None


@handler(MsgType.PERMISSION)
def on_permission(ctx, msg):
    session = ctx.session                 # was: msg.get("session", "")
    verbosity = ctx.verbosity             # was: self.config.get("verbosity", "everything")
    text = ctx.host._permission_text(msg)
    cue = ctx.host._selection_cue(session, verbosity)
    if cue:
        text = "{0} {1}".format(text, cue)
    ctx.host._stream(session).options = text
    entry = ctx.host.history.record(session, "permission", text)
    ctx.host.history.end_message(session)
    # The flip: enqueue unconditionally into this session's own stream.
    ctx.host._flush_prose_buffer(session)   # prose before the permission ask
    ctx.host._enqueue(session, "permission", text, True, entry=entry)
    return None


@handler(MsgType.REREAD_OPTIONS)
def on_reread_options(ctx, msg):
    fg = ctx.host.sessions.foreground()
    if fg is None:
        return None
    st = ctx.host._streams.get(fg)
    text = st.options if st is not None else None
    if text:
        ctx.host._enqueue(fg, "choice", text, False)
    else:
        ctx.host._enqueue(fg, "prose", "No options right now.", False)
    return None
