from __future__ import annotations

from sonari.protocol import MsgType
from sonari.daemon.registry import handler


def _nav(ctx, session: str, to: str) -> None:
    """Move the per-session message cursor within the ANCHORED response and play
    from there to its end. The anchored response is `nav_turn` (None == the live
    turn); a response jump (`_nav_response`) sets it, a new prompt clears it. If the
    anchored turn was evicted by the rolling cap, fall back to the live turn.

    The cursor indexes the anchored turn's messages, oldest..newest; absent == the
    latest. 'next'/'prev' step one message and CLAMP at the ends (no wrap; at the
    newest, 'next' just re-reads it); 'first'/'last' jump to the start/end. Every
    move cuts current speech, clears the queue, and reads the target message AND
    every later one (seek-and-play). Newly streamed prose enqueues after these."""
    st = ctx.host._stream(session)
    if st.nav_turn is not None and st.nav_turn not in ctx.host.history.turn_ids(session):
        st.nav_turn = None              # anchored turn evicted -> follow live again
        st.nav_cursor = None
    if st.nav_turn is None:
        ids = ctx.host.history.message_ids(session)
    else:
        ids = ctx.host.history.message_ids_in_turn(session, st.nav_turn)
    if not ids:
        ctx.host._enqueue(session, "prose", "Nothing to navigate yet.", False)
        return
    n = len(ids)
    cur_id = st.nav_cursor
    cur = ids.index(cur_id) if cur_id in ids else n - 1
    if to == "next":
        new = min(cur + 1, n - 1)
    elif to == "prev":
        new = max(cur - 1, 0)
    elif to == "first":
        new = 0
    elif to == "last":
        new = n - 1
    else:
        return
    if new >= n - 1:
        ctx.host._stream(session).nav_cursor = None
    else:
        ctx.host._stream(session).nav_cursor = ids[new]
    ctx.host.speaker.cancel()
    ctx.host._drop_pending(ctx.host._stream(session).queue.clear())
    # Seek-and-play: enqueue the target item AND every later one.
    for mid in ids[new:]:
        for e in ctx.host.history.entries_for_message(session, mid):
            ctx.host._enqueue(session, e.kind, e.text, False, entry=e)


def _nav_response(ctx, session: str, direction: str) -> None:
    """Response-to-response navigation (Stage 5). Move the turn anchor a whole
    response, read the target response from its start (seek-and-play), and lead with
    a relative orientation cue. Clamps at the oldest/latest. Read-only — replays
    stored text, never re-triggers the agent."""
    st = ctx.host._stream(session)
    turns = ctx.host.history.turn_ids(session)
    if len(turns) < 2:
        # 0 or 1 navigable responses -> nothing to move between.
        cue = "Nothing to navigate yet." if not turns else "No other response."
        ctx.host._enqueue(session, "prose", cue, False, mute_exempt=True)
        return
    # Current anchored index (None anchor == live == the latest turn).
    cur_turn = st.nav_turn
    cur_idx = turns.index(cur_turn) if cur_turn in turns else len(turns) - 1
    if direction == "prev_response":
        new_idx = max(cur_idx - 1, 0)
    else:
        new_idx = min(cur_idx + 1, len(turns) - 1)
    target_turn = turns[new_idx]
    at_newest = (new_idx == len(turns) - 1)
    # Follow live (anchor None) ONLY when the target is the ACTUAL live turn. When the
    # live turn is empty (FLUSH->first-prose window) it is excluded from turn_ids, so
    # the newest navigable turn is NOT the live turn — pin the anchor to it instead of
    # None (which would point at the empty live turn and dead-end within-nav).
    follow_live = at_newest and target_turn == ctx.host.history.current_turn(session)
    st.nav_turn = None if follow_live else target_turn
    # Relative orientation cue; boundary cues take precedence (Nima's decision).
    # "Back to the latest." fires at the newest navigable response, live or not.
    if at_newest:
        cue = "Back to the latest."
    elif new_idx == 0:
        cue = "Oldest response."
    else:
        back = (len(turns) - 1) - new_idx
        cue = "{0} response{1} back.".format(back, "" if back == 1 else "s")
    mids = ctx.host.history.message_ids_in_turn(session, target_turn)
    # Anchor the cursor at the START of the target response; None == follow live.
    st.nav_cursor = None if follow_live else (mids[0] if mids else None)
    ctx.host.speaker.cancel()
    ctx.host._drop_pending(st.queue.clear())
    ctx.host._enqueue(session, "prose", cue, False, mute_exempt=True)
    for mid in mids:
        for e in ctx.host.history.entries_for_message(session, mid):
            ctx.host._enqueue(session, e.kind, e.text, False, entry=e)


@handler(MsgType.NAV)
def on_nav(ctx, msg):
    # Sanctioned unguarded (D3 spec §4g; RECONCILIATION has the ruling): this
    # is deliberate re-reading of already-stored transcript content, not a
    # live voice hand-off, so no liveness check runs here.
    sessions = ctx.host.sessions
    target = sessions.workspace()
    if target is None:
        return None
    crossed = target != sessions.speaker()        # compute BEFORE focus() moves it; SP2: speaker() advances independently of workspace()
    if crossed:
        sessions.focus(target)                     # move the voice to the navigated session
        ctx.host.voice_state = "flowing"           # cross-nav is a deliberate re-engage; within-nav is not
    to = msg.get("to", "prev")
    if to in ("prev_response", "next_response"):
        _nav_response(ctx, target, to)             # both clear target queue, then enqueue transcript
    else:
        _nav(ctx, target, to)
    if crossed:
        # Lead with a short folder cue so an eyes-free user knows the voice jumped.
        # Enqueue AFTER _nav (its queue.clear() would drop an earlier enqueue); at_front
        # so it still plays first. names_session claims the session, suppressing the
        # auto folder-prefix on the following item (no double-announce) — mirrors on_jump_waiting.
        folder = sessions.folder(target)
        if folder:
            ctx.host._enqueue(target, "prose", folder + ".", False,
                              audio_path=ctx.host._spearcon_path(folder),
                              mute_exempt=True, at_front=True, names_session=True)
    return None
