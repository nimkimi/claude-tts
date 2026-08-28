from __future__ import annotations

from sonari.protocol import MsgType
from sonari.daemon.registry import handler
from sonari.daemon.features.control import CLOSED_WORD


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


@handler(MsgType.SKIP_PILE)
def on_skip_pile(ctx, msg):
    # Deliberate PILE-SKIP (Fork B1): a distinct confirmatory gesture (NOT the safe ⌃⌘↓
    # browse return) that advances the frontier PAST the unheard pile to the live edge
    # and SPEAKS a count — the skipped entries stay heard=False in the transcript
    # (browsable) but BELOW the frontier (out of the auto-catch-up path). Fork C1'
    # (owner ear-pass ruling, 2026-07-17 — supersedes C1): PILE-SEEKING,
    # WORKSPACE-FIRST. C1 targeted the flooding SPEAKER under divergence and never
    # looked at the workspace's own pile — standing ON a piled session while the
    # voice flows elsewhere said "Nothing to skip." instead of clearing the pile
    # under your keyboard. C1' targets the workspace whenever IT has a pile;
    # only when the workspace is clean does it fall through to a flowing,
    # diverged speaker's pile (the original flood remedy, preserved). Frontier
    # write-path (b).
    host = ctx.host
    sessions = host.sessions
    ws = sessions.workspace()
    spk = sessions.speaker()

    def pile(session):
        st = host._stream(session)
        entries, _ = host.history.unheard_from_frontier(session, st.frontier)
        return entries

    target = None
    entries = []
    ws_pile = pile(ws) if ws is not None else []
    if ws is not None and len(ws_pile) > 0:
        target, entries = ws, ws_pile
    elif host.voice_state == "flowing" and spk is not None and spk != ws:
        spk_pile = pile(spk)
        if len(spk_pile) > 0:
            target, entries = spk, spk_pile   # preserved §10.1 flood remedy: workspace is clean

    if target is None:
        # No addressable pile anywhere. Route the "Nothing to skip." cue to
        # whichever of workspace/speaker is addressable (workspace preferred,
        # mirroring the old default target) — error earcon only when NEITHER
        # is addressable.
        cue_target = ws if ws is not None else spk
        if cue_target is None:
            host.cue("error")
            return None
        # RR-2 (single-item grain): a dead workspace is adopted by nothing, so
        # this answer was composed into silence. One cue, not the closed pile.
        host._sanction_dead_read(cue_target, whole=False)
        # Nothing ahead of the frontier — do NOT nag "skipped 0" (B3 rejected).
        host._enqueue(cue_target, "prose", "Nothing to skip.", False,
                      control_cue=True, at_front=True)
        return None
    count = len(entries)
    st = host._stream(target)
    # Advance PAST the pile to the live edge WITHOUT marking the skipped entries heard.
    st.advance_frontier(host.history.newest_key(target))
    # Stop the flood: drop the target's queued pile and cut its in-flight utterance.
    host._drop_pending(st.queue.clear())
    cur = host._current_item
    if cur is not None and cur.session == target:
        host.speaker.cancel()
    # Confirmatory count — OWNER EAR-GATE string (grammar v2: role word "item(s)"
    # adjacent to the number, folder-named like on_jump_waiting's crossed cue).
    # Barge-in-class cue: control_cue + at_front.
    noun = "item" if count == 1 else "items"
    folder = sessions.folder(target)
    where = "in {0}".format(folder) if folder else "in another session"
    # T6 re-review finding 8: when TARGET diverges from the current speaker (owner
    # standing on a piled workspace while the voice flows elsewhere), the target's
    # stream isn't heard until keep-going eventually rotates there — enqueueing the
    # confirmation THERE leaves it functionally silent. Voice it on the SPEAKER's
    # stream instead (still at_front, so it lands at the next sentence boundary);
    # the speaker is innocent, so this never cancels/cuts its in-flight utterance.
    # Converged (target == spk) and the flood fall-through (target already IS the
    # speaker) keep the original routing unchanged; a None speaker (idle voice)
    # also keeps it — keep-going adopts a playable workspace cue immediately (see
    # on_where_am_i's idle branch, control.py).
    cue_dest = spk if (spk is not None and spk != target) else target
    host._sanction_dead_read(cue_dest, whole=False)      # RR-2: same seam, same grain
    host._enqueue(cue_dest, "prose", "Skipping {0} {1} {2}.".format(count, noun, where), False,
                  control_cue=True, at_front=True)
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
        ctx.host.cue("error")
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
        # enqueue (which is at_front) so it is not itself dropped. Whole-branch review
        # Minor 1: st.prose_buffer (sub-threshold prose held below minqueue) is a
        # SECOND pre-start pile the queue clear doesn't touch — its entries are already
        # in history (recorded at buffer time, prose.py on_prose), so dropping the
        # buffer here just leaves them behind the frontier too, instead of letting the
        # next turn boundary flush them forward=True and drag the frontier over them.
        st.stopped = False
        ctx.host.voice_state = "flowing"
        sessions.set_speaker(fg)
        ctx.host._drop_pending(st.queue.clear())
        st.prose_buffer = []
        # RR-4 (fix-wave E, re-adjudicated): this branch TAKES the voice itself
        # (set_speaker above), unlike the enqueue-only single-item sites — so on
        # a DEAD fg, R-1's release now strands "Resumed." the instant it lands.
        # Single-item, not the whole-stream restore-base the controller
        # rejected: the resumed stream is un-muted, not a request to be read.
        ctx.host._sanction_dead_read(fg, whole=False)
        # Provably not stopped: st.stopped = False, set 11 lines above.
        ctx.host._enqueue(fg, "prose", "Resumed.", False, control_cue=True, at_front=True)
        # The session names itself the first time he can actually hear it. A
        # session born under stop-all had its announce burned before delivery
        # was possible and then destroyed at this very resume. Gated on the
        # ARMED flag, never on "never announced": claim_announce is a bare
        # test-and-set whose only caller is SESSION_START, so an unconditional
        # claim here fires for every session that never ran one -- which is
        # every fixture session -- and would break tests/test_daemon_stop.py:27
        # and tests/test_frontier.py:219,246 by exact equality. And gated on
        # is_live BEFORE the claim: _sanction_dead_read(fg, whole=False) just
        # above sanctions exactly ONE pop, which "Resumed." consumes, so on a
        # dead stream this second item is stranded -- no `entry=`, so no history
        # transcript and no catch-up -- with the one-shot already spent.
        # Short-circuiting leaves both the flag and the marker armed for the
        # next audible chance. claim_announce stays the final arbiter, so even a
        # re-armed flag can never speak the announce twice; and a
        # quiet-verbosity resume leaves the flag armed for a later audible one.
        if (st.announce_deferred
                and ctx.host.config.get("verbosity", "everything") != "quiet"
                and sessions.is_live(fg)
                and sessions.claim_announce(fg)):
            st.announce_deferred = False
            folder = sessions.folder(fg)
            ctx.host._enqueue(
                fg, "prose",
                "{0}, {1}.".format(sessions.number(fg),
                                   folder or "Another session"),
                False, names_session=True)
    else:
        # Stopping -> quiet-hold (SPEC §6). Cancel only if THIS session is in flight.
        st.stopped = True
        # Task 10 / R21: invalidate a Policy-A "Resumed." claim armed on this
        # stream (lifecycle.py's on_set_foreground) but not yet delivered.
        # Arm-time only proved deliverability at ARM time; this ⌃⌘S press
        # falsifies it right here -- without this, the still-armed flag rides
        # to the next FLUSH/SESSION_START and speaks a stale "Resumed." on the
        # very stream this press just muted, through the mute, because it is
        # a control cue.
        st.announce_resume = False
        ctx.host.voice_state = "quiet-hold"
        cur = ctx.host._current_item
        if cur is not None and cur.session == fg:
            ctx.host.speaker.cancel()
        # "Stopped." is a control cue: voiced by the held branch even while stopped.
        ctx.host._enqueue(fg, "prose", "Stopped.", False, control_cue=True)
    return None


@handler(MsgType.STOP_ALL)
def on_stop_all(ctx, msg):
    # Stop EVERY session at once (the master quiet key, ⌃⌘M). One-way: bring each
    # session back individually with ⌃⌘S. Cancels any in-flight utterance; the
    # speak loop re-queues it at the front of its own (now stopped) stream.
    for st in ctx.host._streams.values():
        st.stopped = True
        # Task 10 / R21: the master quiet invalidates every armed "Resumed."
        # claim at once, same reasoning as on_stop_session's stopping branch
        # above -- an armed-but-undelivered flag must not survive the very
        # stop that falsifies it.
        st.announce_resume = False
    ctx.host.voice_state = "stopped-all"             # SPEC §6/§270: every session muted
    if ctx.host._current_item is not None:
        ctx.host.speaker.cancel()
    spk = ctx.host.sessions.speaker()
    if spk is not None:
        # Ensure the SPEAKER's stream is stopped even if it had no stream yet, then
        # voice the confirmation there (control_cue -> the held branch, which reads
        # speaker(), speaks it under divergence).
        ctx.host._stream(spk).stopped = True
        ctx.host._enqueue(spk, "prose", "All stopped.", False,
                          control_cue=True)
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
        # RR-2 (single-item grain): the miss answers — the re-spoken prompt and
        # "No decision here." — fall back to a workspace that can be dead, where
        # nothing would ever adopt them. Fork-2 still holds: the stream is only
        # made audible for this one cue, never drained or otherwise touched.
        ctx.host._sanction_dead_read(tgt, whole=False)
        if pending is not None:
            # Answerable-but-already-narrated (inside the ~120s window):
            # re-speak the STORED prompt; the queue holds nothing to drain to.
            ctx.host._enqueue(tgt, "prose", pending["text"], False,
                              control_cue=True, at_front=True)
        elif tgt is not None:
            ctx.host._enqueue(tgt, "prose", "No decision here.", False,
                              control_cue=True, at_front=True)
        else:
            ctx.host.cue("error")
        return None
    crossed = target != sessions.speaker()   # voice owner is speaker(); SP2 advances it independently of workspace()
    if crossed and sessions.liveness(target) == "dead":
        # D3 spec §4i: the crossed path MOVES the voice — a dead target gets
        # the word and no move; the non-crossed path is deliberate reading
        # within the session already in flight and stays unguarded. Must
        # fire before the voice_state write below (no state mutation).
        dest = sessions.speaker() or sessions.workspace()
        ctx.host.cue("error", word=CLOSED_WORD, session=dest)
        return None
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
        if st.stopped:
            # ctrl-cmd-D IS the request to hear this ask. Marking the head
            # rather than re-enqueuing preserves its entry/prelude/forward
            # provenance. Gated on stopped so the un-muted path is
            # byte-identical to today.
            st.queue.claim_head_as_control_cue()
    ctx.host.speaker.cancel()
    # Compute folder once — reused by both the crossed-folder spearcon cue and
    # the raise on_failure lambda below (DRY; avoids a second sessions.folder()
    # call inside the raise closure, which would capture a stale binding).
    folder = sessions.folder(target)
    if crossed and folder:
        ctx.host._enqueue(target, "prose", folder + ".", False,
                          audio_path=ctx.host._spearcon_path(folder),
                          control_cue=True, at_front=True, names_session=True)
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
            host.cue("error")
            return None
        tgt = ws
    # RR-2 (single-item grain), covering both answers below: a dead tgt is
    # adopted by nothing, so the repeat — and "Nothing to repeat." — were
    # composed into silence. ⌃⌘R asks for ONE utterance; the interrupted item
    # re-queued behind it stays behind the §4d rule, unread.
    host._sanction_dead_read(tgt, whole=False)
    last = host._last_utterance
    if last is None:
        # Spoken cue, not an error tone — an empty repeat is not a mis-press.
        host._enqueue(tgt, "prose", "Nothing to repeat.", False,
                      control_cue=True, at_front=True)
        return None
    text, audio_path = last
    cur = host._current_item
    entry = host._pending_heard.get(cur.id) if cur is not None else None
    host.speaker.cancel()                          # barge-in: cut the current utterance
    if cur is not None:
        # cur.control_cue is carried, not re-decided: a captured item keeps
        # whatever it was enqueued with for its whole lifetime, so re-queuing
        # it here is held-branch eligible if its stream is (or later becomes)
        # stopped -- this is a re-queue of an existing item, not a fresh
        # enqueue site subject to the enqueue-time reasoning above.
        host._enqueue(cur.session, cur.kind, cur.text, cur.is_decision,
                      entry=entry, control_cue=cur.control_cue,
                      names_session=cur.names_session,
                      audio_path=cur.audio_path, forward=cur.forward, at_front=True,
                      prelude=cur.prelude)
    # The repeat is a control_cue: never re-captured (idempotence), never prefixed
    # (the captured text already carries any prefix). A spearcon-only last
    # utterance replays its audio file (audio_path passthrough). Preludes never
    # replay: _last_utterance captures content only — the chirp/call-sign
    # decorated the ORIGINAL moment, not the repeat.
    host._enqueue(tgt, "prose", text, False, control_cue=True,
                  at_front=True, audio_path=audio_path)
    return None
