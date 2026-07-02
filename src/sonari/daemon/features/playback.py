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
    # Per-session stop/start (⌃⌘S). Toggles the SPEAKER session — the track you are
    # currently HEARING; in the keep-going era that may differ from the workspace.
    # "Stop what's talking." Stopping holds this session's stream and re-reads from
    # the interrupted item on resume; the state is sticky across new prompts (a
    # stopped session stays silent until ⌃⌘S'd again). The "Stopped."/"Resumed." cue
    # is enqueued to the SAME target (fg), so the held branch voices it under
    # divergence (the held branch reads speaker(), which is fg after the repoint).
    fg = ctx.host.sessions.speaker()
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
        ctx.host.voice_state = "quiet-hold"          # SPEC §6: ⌃⌘S on the speaker -> quiet-hold
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
    ctx.host.voice_state = "stopped-all"             # SPEC §6/§270: every session muted
    if ctx.host._current_item is not None:
        ctx.host.speaker.cancel()
    spk = ctx.host.sessions.speaker()
    if spk is not None:
        # Ensure the SPEAKER's stream is stopped even if it had no stream yet, then
        # voice the confirmation there (pause_exempt -> the held branch, which reads
        # speaker(), speaks it under divergence).
        ctx.host._stream(spk).stopped = True
        ctx.host._enqueue(spk, "prose", "All stopped.", False,
                          mute_exempt=True, pause_exempt=True)
    return None



@handler(MsgType.JUMP_DECISION)
def on_jump_decision(ctx, msg):
    # ⌃⌘D: jump to the question/decision. Follows OS focus like on_nav — when the
    # focused session isn't the one speaking, MOVE the voice to it and voice its
    # decision (crossed→focus+folder cue); otherwise jump within the foreground.
    sessions = ctx.host.sessions
    target = sessions.workspace()
    crossed = target != sessions.speaker()   # voice owner is speaker(); SP2 advances it independently of workspace()
    if crossed:
        sessions.focus(target)
    else:
        # Acting on the session in flight: mark its current item heard (it's being
        # skipped past). When crossed there is no in-flight item for the target.
        cur = ctx.host._current_item
        if cur is not None:
            entry = ctx.host._pending_heard.get(cur.id)
            if entry is not None:
                entry.heard = True
    st = ctx.host._streams.get(target)
    if st is not None:
        ctx.host._drop_pending(st.queue.jump_to_decision())
    ctx.host.speaker.cancel()
    # Compute folder once — reused by both the crossed-folder spearcon cue and
    # the raise on_failure lambda below (DRY; avoids a second sessions.folder()
    # call inside the raise closure, which would capture a stale binding).
    folder = sessions.folder(target)
    if crossed and folder:
        ctx.host._enqueue(target, "prose", folder + ".", False,
                          audio_path=ctx.host._spearcon_path(folder),
                          mute_exempt=True, at_front=True, names_session=True)
    # Raise the target window (R5/R9 — C2 fix): ⌃⌘D is a deliberate workspace
    # action, so the terminal follows the jump, mirroring on_cycle_session and
    # on_jump_waiting. bump_generation() runs on EVERY invocation (outside the
    # guard) so a non-raising jump still supersedes any prior in-flight raise.
    identity = sessions.identity(target)
    will_raise = ctx.host._raise().will_attempt(identity)
    gen = ctx.host._raise().bump_generation()
    if will_raise:
        ctx.host._raise().raise_async(
            identity, gen,
            on_failure=lambda s=target, f=folder: ctx.host._raise_failed(s, f))
    return None
