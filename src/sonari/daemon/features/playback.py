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


@handler(MsgType.STOP_ALL)
def on_stop_all(ctx, msg):
    # Stop EVERY session at once (the master quiet key, ⌃⌘M). One-way: bring each
    # session back individually with ⌃⌘S. Cancels any in-flight utterance; the
    # speak loop re-queues it at the front of its own (now stopped) stream.
    for st in ctx.host._streams.values():
        st.stopped = True
    if ctx.host._current_item is not None:
        ctx.host.speaker.cancel()
    fg = ctx.host.sessions.foreground()
    if fg is not None:
        # Ensure the foreground stream is stopped even if it had no stream yet, then
        # voice the confirmation (pause_exempt -> the held branch speaks it).
        ctx.host._stream(fg).stopped = True
        ctx.host._enqueue(fg, "prose", "All stopped.", False,
                          mute_exempt=True, pause_exempt=True)
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
