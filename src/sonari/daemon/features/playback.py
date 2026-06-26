from __future__ import annotations

from sonari.protocol import MsgType
from sonari.daemon.registry import handler


@handler(MsgType.STOP)
def on_stop(ctx, msg):
    # Stop acts on the FOREGROUND stream only — clearing every stream would
    # wipe a background session's backlog the user hasn't heard yet (the 2a
    # global-STOP clobber). Background streams accumulate untouched.
    fg = ctx.host.sessions.foreground()
    st = ctx.host._streams.get(fg)
    if st is not None:
        ctx.host._drop_pending(st.queue.clear())
    ctx.host.speaker.cancel()
    return None


@handler(MsgType.SKIP)
def on_skip(ctx, msg):
    cur = ctx.host._current_item
    if cur is not None:
        entry = ctx.host._pending_heard.get(cur.id)
        if entry is not None:
            entry.heard = True
    ctx.host.speaker.cancel()
    return None


@handler(MsgType.STOP_SESSION)
def on_stop_session(ctx, msg):
    # Per-session stop/start (⌃⌘S). Toggles the FOREGROUND session — the track you
    # are currently flying; switch with ⌃⌘Tab / ⌃⌘J first to stop another. Stopping
    # holds this session's stream and re-reads from the interrupted item on resume;
    # the state is sticky across new prompts (a stopped session stays silent until
    # ⌃⌘S'd again).
    fg = ctx.host.sessions.foreground()
    if fg is None:
        ctx.host.speaker.earcon("error")
        return None
    st = ctx.host._stream(fg)
    if st.stopped:
        # Resuming: "Resumed." FIRST (at the front, ahead of the interrupted item the
        # speak loop re-queued there on stop), then clear the flag. _enqueue wakes
        # the loop. mute_exempt so the control cue is never folder-prefixed.
        st.stopped = False
        ctx.host._enqueue(fg, "prose", "Resumed.", False,
                          mute_exempt=True, at_front=True)
    else:
        st.stopped = True
        # Cancel only if THIS session is the one in flight, so stopping never cuts
        # another session's utterance (the loop only plays the foreground, so a live
        # claim is the foreground's — the session check is belt-and-suspenders).
        cur = ctx.host._current_item
        if cur is not None and cur.session == fg:
            ctx.host.speaker.cancel()
        # "Stopped." is pause_exempt (the held branch voices it past the re-queued
        # item) and mute_exempt (a control cue, never folder-prefixed).
        ctx.host._enqueue(fg, "prose", "Stopped.", False,
                          mute_exempt=True, pause_exempt=True)
    return None


@handler(MsgType.MUTE)
def on_mute(ctx, msg):
    # Toggle a sticky per-session mute. Earcons still fire (alerts), and the
    # "muted"/"unmuted" confirmation is spoken (the mute-on case is exempt).
    fg = ctx.host.sessions.foreground()
    if fg is None:
        return None
    st = ctx.host._stream(fg)
    if st.muted:
        st.muted = False
        ctx.host._enqueue(fg, "prose", "Session unmuted.", False)
    else:
        st.muted = True
        ctx.host._drop_pending(st.queue.clear())
        cur = ctx.host._current_item
        if cur is not None and cur.session == fg:
            ctx.host.speaker.cancel()
        ctx.host._enqueue(fg, "prose", "Session muted.", False, mute_exempt=True)
    return None


@handler(MsgType.PIN_TOGGLE)
def on_pin_toggle(ctx, msg):
    # Pin the voice to the current (last-prompt) session, or unpin it.
    # The pin overrides "foreground", so a later SET_FOREGROUND from another
    # session can't steal the voice. Confirmation is mute_exempt so the user
    # always hears it; the no-session case has nothing to speak through, so
    # it is an error earcon only.
    action, folder = ctx.host.sessions.pin_toggle()
    if action == "none":
        ctx.host.speaker.earcon("error")
        return None
    fg = ctx.host.sessions.foreground()
    if action == "pinned":
        text = "Pinned {0}.".format(folder) if folder else "Pinned."
    else:
        text = "Auto."
    ctx.host._enqueue(fg, "prose", text, False, mute_exempt=True,
                      names_session=(action == "pinned"))
    return None


@handler(MsgType.JUMP_DECISION)
def on_jump_decision(ctx, msg):
    # Mark the cancelled current item heard and clear heard-markers of the
    # skipped prose items so they don't linger in unheard() (M6).
    cur = ctx.host._current_item
    if cur is not None:
        entry = ctx.host._pending_heard.get(cur.id)
        if entry is not None:
            entry.heard = True
    fg = ctx.host.sessions.foreground()
    st = ctx.host._streams.get(fg)
    if st is not None:
        ctx.host._drop_pending(st.queue.jump_to_decision())
    ctx.host.speaker.cancel()
    return None
