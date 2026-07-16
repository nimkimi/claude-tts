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
    # ⌃⌘S per-session stop/start. Fork 4 = ASYMMETRIC target: if the session you
    # NAVIGATED TO (workspace) is stopped, START it (R7 "start the session you navigated
    # to"); otherwise STOP the speaker (R7 "Stop-the-speaker"). Without this, a chooser
    # commit onto a mute leaves workspace=muted, speaker=active, and a speaker-target ⌃⌘S would STOP
    # the active speaker instead of starting the mute -> the mute is keyboard-unstartable.
    sessions = ctx.host.sessions
    ws = sessions.workspace()
    ws_st = ctx.host._streams.get(ws) if ws is not None else None
    if ws_st is not None and ws_st.stopped:
        fg = ws                                   # START the navigated-to (muted) workspace
    else:
        fg = sessions.speaker()                   # STOP the speaker (status quo)
    if fg is None:
        ctx.host.speaker.earcon("error")
        return None
    st = ctx.host._stream(fg)
    if st.stopped:
        # Resuming (⌃⌘S-start re-engage). D2 (2026-07-16): a QUIET RESUME. Un-stop,
        # lift to flowing, and MOVE THE VOICE to the started session. DROP the pre-start
        # QUEUE (its buffered pile) so start does NOT auto-drain it — the pile persists
        # in the history transcript BEHIND the frozen frontier (nothing advanced it),
        # reachable later by SP5's catch-up; only post-start output flows. Dropped
        # decision items also persist in history, and a live blocking permission stays
        # answerable via _pending_decisions / ⌃⌘D. The clear MUST precede the "Resumed."
        # enqueue (which is at_front) so it is not itself dropped.
        st.stopped = False
        ctx.host.voice_state = "flowing"
        sessions.set_speaker(fg)
        ctx.host._drop_pending(st.queue.clear())
        ctx.host._enqueue(fg, "prose", "Resumed.", False, mute_exempt=True, at_front=True)
    else:
        # Stopping -> quiet-hold (SPEC §6). Cancel only if THIS session is in flight.
        st.stopped = True
        ctx.host.voice_state = "quiet-hold"
        cur = ctx.host._current_item
        if cur is not None and cur.session == fg:
            ctx.host.speaker.cancel()
        # "Stopped." is pause_exempt (held branch voices it) + mute_exempt (control cue).
        ctx.host._enqueue(fg, "prose", "Stopped.", False, mute_exempt=True, pause_exempt=True)
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
    # W4 miss safety: a HIT = a queued decision item OR a live pending blocking
    # decision. has_decision() scans QUEUED items only (queue.py:78-81), so an
    # answerable-but-already-narrated permission has an empty queue — a queue-
    # scoped cue would lie exactly where honesty matters (spec §5 deviation).
    # On a miss: speak the cue and do NOTHING else — no drain, no cancel, no
    # voice/workspace move, no heard-marking, no voice_state write, no raise
    # (Fork-2: the stream is never touched). Routing mirrors ⌃⌘J's empty cue.
    st_t = ctx.host._streams.get(target) if target is not None else None
    pending = ctx.host._pending_decisions.get(target) if target is not None else None
    has_queued = st_t is not None and st_t.queue.has_decision()
    if not has_queued:
        tgt = sessions.speaker() or target
        if pending is not None:
            # Answerable-but-already-narrated (inside the ~120s window):
            # re-speak the STORED prompt; the queue holds nothing to drain to.
            ctx.host._enqueue(tgt, "prose", pending["text"], False,
                              mute_exempt=True, at_front=True)
        elif tgt is not None:
            ctx.host._enqueue(tgt, "prose", "No decision here.", False,
                              mute_exempt=True, pause_exempt=True, at_front=True)
        else:
            ctx.host.speaker.earcon("error")
        return None
    crossed = target != sessions.speaker()   # voice owner is speaker(); SP2 advances it independently of workspace()
    ctx.host.voice_state = "flowing"                 # ⌃⌘D re-engage (R5:149 groups jump/⌃⌘D)
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
    # action, so the terminal follows the jump, mirroring on_jump_waiting and the
    # chooser commit. bump_generation() runs on EVERY invocation (outside the
    # guard) so a non-raising jump still supersedes any prior in-flight raise.
    identity = sessions.identity(target)
    will_raise = ctx.host._raise().will_attempt(identity)
    gen = ctx.host._raise().bump_generation()
    if will_raise:
        ctx.host._raise().raise_async(
            identity, gen,
            on_failure=lambda s=target, f=folder: ctx.host._raise_failed(s, f))
    return None


@handler(MsgType.REPEAT_LAST)
def on_repeat_last(ctx, msg):
    # ⌃⌘R (W12): re-speak the last completed content utterance, verbatim as
    # spoken. Barge-in-class with the ⌃⌘W capture-park-resume discipline: the
    # interrupted item is re-queued at_front FIRST (ends up deepest), then the
    # repeat at_front — so the ear hears repeat, then the resumed utterance.
    # Fork-2: enqueues to the speaker / a playable workspace, never un-stops.
    host = ctx.host
    sessions = host.sessions
    tgt = sessions.speaker()
    if tgt is None:
        # Mirror ⌃⌘W's None-speaker branch: a playable workspace stream can be
        # adopted by keep-going; a muted/None workspace has nothing voiceable.
        ws = sessions.workspace()
        ws_st = host._streams.get(ws) if ws is not None else None
        playable = ws is not None and not (ws_st is not None and ws_st.stopped)
        if not playable:
            host.speaker.earcon("error")
            return None
        tgt = ws
    last = host._last_utterance
    if last is None:
        # Spoken cue, not an error tone — an empty repeat is not a mis-press.
        host._enqueue(tgt, "prose", "Nothing to repeat.", False,
                      mute_exempt=True, pause_exempt=True, at_front=True)
        return None
    text, audio_path = last
    cur = host._current_item
    entry = host._pending_heard.get(cur.id) if cur is not None else None
    host.speaker.cancel()                          # barge-in: cut the current utterance
    if cur is not None:
        host._enqueue(cur.session, cur.kind, cur.text, cur.is_decision,
                      entry=entry, mute_exempt=cur.mute_exempt,
                      pause_exempt=cur.pause_exempt, names_session=cur.names_session,
                      audio_path=cur.audio_path, forward=cur.forward, at_front=True)
    # The repeat is mute_exempt: never re-captured (idempotence), never prefixed
    # (the captured text already carries any prefix). A spearcon-only last
    # utterance replays its audio file (audio_path passthrough).
    host._enqueue(tgt, "prose", text, False, mute_exempt=True,
                  pause_exempt=True, at_front=True, audio_path=audio_path)
    return None
