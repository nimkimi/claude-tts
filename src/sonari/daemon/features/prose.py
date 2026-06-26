from __future__ import annotations

from sonari.protocol import MsgType
from sonari.daemon.registry import handler


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
    if verbosity == "everything":
        tool = msg.get("tool", "")
        summary = (msg.get("summary") or "").strip()
        text = summary if summary else "Running {0}.".format(tool)
        # Keep textual order: read prose that preceded this tool call first.
        ctx.host._flush_prose_buffer(session)
        ctx.host._enqueue(session, "tool_announce", text, False)
    return None


@handler(MsgType.EARCON)
def on_earcon(ctx, msg):
    session = ctx.session                 # was: msg.get("session", "")
    # Instant: the Windows earcon backend plays on a separate audio path
    # that mixes with the speech, so it no longer cuts the reading.
    kind = msg.get("kind", "")
    ctx.host.speaker.earcon(kind)
    if kind == "turn_done":
        # End-of-turn boundary: flush any sub-threshold buffered prose so
        # it is not silently dropped when the assistant produces fewer items
        # than the minqueue threshold.
        ctx.host._flush_prose_buffer(session)
    return None


@handler(MsgType.FLUSH)
def on_flush(ctx, msg):
    session = ctx.session                 # was: msg.get("session", "")
    st = ctx.host._stream(session)
    ctx.host._drop_pending(st.queue.clear())
    cur = ctx.host._current_item
    # Cut the current utterance on a new prompt: same-session (the new prompt
    # supersedes the old reply) OR a cross-session switch where this prompt's
    # session is now the foreground — so the voice moves to it
    # immediately instead of finishing the old session's sentence (§4.2
    # cut-on-switch). SESSION_START sends no FLUSH, so a bare new session
    # never cuts.
    # NOTE: since #65 gated on_set_foreground, a *background* prompt can no longer
    # make foreground() == its own session while another session is speaking, so the
    # cross-session disjunct is now effectively dead for background re-invocations;
    # the same-session disjunct carries all live cut behavior. Retained (not removed)
    # to stay correct for any future explicit-switch path that FLUSHes its own
    # already-foreground session.
    if cur is not None and (cur.session == session
                            or ctx.host.sessions.foreground() == session):
        ctx.host.speaker.cancel()
    st.reset_for_new_prompt()
    # Stage 4: a new prompt opens a NEW TURN and KEEPS the prior turn's
    # transcript (persistent, navigable in Stage 5). reset_for_new_prompt()
    # already cleared live playback (queue, assembler, nav_cursor -> snap to
    # live edge); history is no longer wiped here. SESSION_END still clears it.
    ctx.host.history.start_turn(session)
    ctx.host._wake.set()
    return None
