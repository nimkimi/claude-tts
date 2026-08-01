from __future__ import annotations

from sonari.protocol import MsgType
from sonari.daemon.registry import handler
from sonari.daemon.features import teaching


@handler(MsgType.PROSE)
def on_prose(ctx, msg):
    session = ctx.session                 # was: msg.get("session", "")
    verbosity = ctx.verbosity             # was: self.config.get("verbosity", "everything")
    final = msg.get("final", False)
    a = ctx.host._stream(session).assembler
    chunks = a.feed(msg.get("delta", ""), msg.get("index", 0), final)
    if chunks:
        from sonari.assembler import PARAGRAPH_BREAK
        # The flip: every non-quiet session buffers its OWN prose into its
        # OWN stream (the speak loop plays only the foreground stream). The
        # old _may_speak gate + captured-drop are gone — background output
        # accumulates instead of being lost.
        speak = verbosity != "quiet"
        for chunk in chunks:
            if chunk is PARAGRAPH_BREAK:
                # A blank-line boundary: start a new message group so the
                # nav cursor treats each paragraph as its own 'item'.
                ctx.host.history.end_message(session)
                continue
            entry = ctx.host.history.record(session, "prose", chunk)
            if speak:
                ctx.host._buffer_prose(session, chunk, entry)
    if final:
        # `final` marks the end of ONE assistant text block, not the whole
        # turn — the buffer is flushed at the real turn boundary (turn_done)
        # and when the threshold is hit, so it is NOT flushed here.
        ctx.host.history.end_message(session)
        ctx.host._stream(session).options = None
    return None


@handler(MsgType.TOOL)
def on_tool(ctx, msg):
    session = ctx.session                 # was: msg.get("session", "")
    verbosity = ctx.verbosity             # was: self.config.get("verbosity", "everything")
    tool = msg.get("tool", "")
    summary = (msg.get("summary") or "").strip()
    text = summary if summary else "Running {0}.".format(tool)
    # Fork A/A1: record the tool-use input summary to the transcript at EVERY
    # verbosity (today on_tool records nothing in any verbosity — a total gap).
    # `summary` is the already-computed generic string; agent-neutral, no new hook,
    # no CC-specific shape crosses the socket. Own message group, like a decision.
    entry = ctx.host.history.record(session, "tool", text)
    ctx.host.history.end_message(session)
    if verbosity == "everything":
        # Everything also ANNOUNCES (medium's spoken summary + quiet are §15 Pass-2).
        # Keep textual order: read prose that preceded this tool call first, then link
        # the announce to its entry (forward=True) so hearing it advances the frontier.
        ctx.host._flush_prose_buffer(session)
        ctx.host._enqueue(session, "tool_announce", text, False,
                          entry=entry, forward=True)
    return None


@handler(MsgType.EARCON)
def on_earcon(ctx, msg):
    session = ctx.session
    kind = msg.get("kind", "")
    # The turn-completion ding is the "something landed" cue (SPEC §11): it fires for
    # non-speaking sessions AND the muted ex-speaker, and is SUPPRESSED only for the
    # session you are hearing live (session == speaker() AND voice is flowing). Branch
    # on turn_done ONLY: choice/plan/permission EARCON msgs may carry a session (the
    # chime is sessionless either way — the call-sign binds where the decision CONTENT
    # enqueues, never here), so the session==speaker() test must never reach them.
    if kind == "turn_done":
        host = ctx.host
        # speaker() is read at earcon-ARRIVAL time; it only gates the hint
        # below, so keep-going advancing the voice in the hook-latency window
        # can at worst mis-skip one hint.
        speaker = host.sessions.speaker()
        # ONE boundary sound for every case, solo or background (owner ear
        # ruling, ear-batch-2 slot 4, 2026-08-01: the solo/background split was
        # "confusing" — do not re-introduce a distinct solo kind without a new
        # ear ruling). D2 §6.1's warrant still holds: the boundary is never
        # silent, so "done" and "stack died" stay distinguishable.
        host.cue(kind)
        # The hint is about a BACKGROUND session finishing, so fire it only when
        # the finished session is NOT the speaker. When it IS a background turn,
        # target the SPEAKER's stream (the only stream the speak loop pops), not
        # the finished session's own stream, or the hint would sit unheard.
        if session != speaker:
            teaching.maybe_hint(host, "background_turn", speaker)
        # End-of-turn boundary: flush any sub-threshold buffered prose UNCONDITIONALLY
        # (a message below the minqueue threshold must still be read) — the flush
        # survives the earcon suppression above.
        host._flush_prose_buffer(session)
    else:
        # Arrival chime only (D8 ruling 1): this message is fire-and-forget and
        # owns no utterance, so no verbal sound may ride it.
        ctx.host.cue(kind)
    return None


@handler(MsgType.FLUSH)
def on_flush(ctx, msg):
    session = ctx.session                 # was: msg.get("session", "")
    if ctx.host.config.get("submit_ack_enabled", False):
        # D2 §6.1 submit-ack (the mirror boundary), built DARK: FLUSH is the
        # daemon-side prompt-submit discriminator (only UserPromptSubmit sends
        # it; SessionStart's SET_FOREGROUND leg never reaches here). A
        # transient — the queue clear below cannot touch it.
        ctx.host.cue("submit_ack")
    st = ctx.host._stream(session)
    ctx.host._drop_pending(st.queue.clear())
    cur = ctx.host._current_item
    # Cut the current utterance on a new prompt: same-session (the new prompt
    # supersedes the old reply) OR a switch where THIS prompt's session is the
    # current SPEAKER — i.e. the speaker self-submitting (the ratified Policy-A
    # same-session cut, F7). Under keep-going (speaker B, workspace A) an autonomous
    # submit from A is NOT the speaker, so it does NOT cut B's live readout (Policy A:
    # autonomous never cuts; only the speaker or the idle voice does). The typed-vs-
    # autonomous discriminator that would let a human-typed A preempt is Pass-2-deferred.
    # SESSION_START sends no FLUSH, so a bare new session never cuts.
    if cur is not None and (cur.session == session
                            or ctx.host.sessions.speaker() == session):
        ctx.host.speaker.cancel()
    st.reset_for_new_prompt()
    # Stage 4: a new prompt opens a NEW TURN and KEEPS the prior turn's
    # transcript (persistent, navigable in Stage 5). reset_for_new_prompt()
    # already cleared live playback (queue, assembler, nav_cursor -> snap to
    # live edge); history is no longer wiped here. SESSION_END still clears it.
    ctx.host.history.start_turn(session)
    if st.announce_resume:
        # D2 §6.3: the Policy-A submit lift (lifecycle) deferred its audible
        # mark past this clear — deliver it now, mirroring ⌃⌘S resume's cue.
        st.announce_resume = False
        ctx.host._enqueue(session, "prose", "Resumed.", False,
                          mute_exempt=True, at_front=True)
    if ctx.host._restore_pending:
        # D2 §6.4/§6.5: the all-muted restart line deferred past boot — the
        # first submit is the first moment a reachable queue exists (a muted
        # speaker's pause-exempt cue is voiced by the held branch). F2:
        # RECOMPOSE now rather than replay the boot-time string — a restored
        # session may have been ⌃⌘S-resumed (or a pile consumed) since boot,
        # and the line must report what is still true, not what was.
        line = ctx.host._compose_restore_line()
        if line is not None:
            ctx.host._enqueue(session, "prose", line, False,
                              mute_exempt=True, pause_exempt=True)
        ctx.host._restore_pending = False
    ctx.host._wake.set()
    return None
