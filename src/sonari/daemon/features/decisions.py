from __future__ import annotations

import threading

from sonari.protocol import MsgType
from sonari.daemon.registry import handler
from sonari.daemon.features import teaching
from sonari.spearcon import spearcon_label


# D7a (§4) misdirected-answer words (the RL5 false-remedy heal) — ratified
# (ear-batch-2, 2026-08-01).
MISDIRECT_ROUTE_WORD = "No ask here — {0} is asking."
MISDIRECT_EMPTY_WORD = "Nothing to answer."

# D7b (§5) the advisory frame: an UNANSWERABLE decision keeps its distinct
# arrival timbre + callsign but its spoken frame says where it is serviceable.
# Wording ratified (ear-batch-2, 2026-08-01); attachment point = end of the full
# composed announce, inside the one chokepoint below.
ADVISORY_SUFFIX = " — at the terminal."


def _announce_decision(ctx, session, kind, text, *, answerable):
    """The ONE announce chokepoint for every decision producer (D7b/T7c).
    answerable=True is RESERVED for the blocking permission request — the only
    ask serviceable from the keyboard (⌃⌘Return / ⌃⌘Escape); CHOICE/PLAN/legacy
    PERMISSION are structurally unanswerable (upstream-blocked, see
    docs/upstream/claude-code-feature-request-answer-hook.md — no answer
    gesture may be attempted for them) and arrive as advisories wearing the
    terminal frame. Returns (item_id, text-as-announced); the drift guards in
    tests/test_answerability.py pin that no decision enqueue lives anywhere
    else and no other producer passes True."""
    if not answerable:
        text = text + ADVISORY_SUFFIX
    entry = ctx.host.history.record(session, kind, text)
    ctx.host.history.end_message(session)
    ctx.host._flush_prose_buffer(session)   # prose before the ask (unchanged order)
    item_id = ctx.host._enqueue(session, kind, text, True, entry=entry, forward=True)
    return item_id, text


def _choice_text(msg) -> str:
    parts = []
    for q in msg.get("questions", []) or []:
        qtext = q.get("question", "") if isinstance(q, dict) else str(q)
        multi = bool(isinstance(q, dict) and q.get("multiSelect"))
        opts = q.get("options", []) if isinstance(q, dict) else []
        segs = []
        for i, o in enumerate(opts, 1):
            if isinstance(o, dict):
                label = o.get("label", "")
                desc = (o.get("description") or "").strip()
            else:
                label, desc = str(o), ""
            if not label:
                continue   # keep numbering aligned with the TUI's digits
            seg = "Option {0}: {1}.".format(i, label)
            if desc:
                seg += " {0}{1}".format(
                    desc, "" if desc.endswith((".", "!", "?")) else ".")
            segs.append(seg)
        head = qtext
        if multi:
            head = "{0}{1}".format(
                (qtext + " ") if qtext else "",
                "This is a multi-select; you can pick more than one.")
        if head and segs:
            parts.append("{0} {1}".format(head, " ".join(segs)))
        elif segs:
            parts.append(" ".join(segs))
        elif head:
            parts.append(head)
    return " ".join(parts) if parts else "A question needs your answer."


def _plan_text(msg) -> str:
    text = (msg.get("text") or "").strip()
    if text:
        return "Plan ready. {0}".format(text)
    return "A plan is ready for your review."


def _permission_text(msg) -> str:
    # The 'permission' earcon already signals approval is needed; speak the
    # pending action, else the human-readable message, else a generic cue.
    action = (msg.get("action") or "").strip()
    if action:
        return action
    message = (msg.get("message") or "").strip()
    return message if message else "Permission needed."


def _selection_cue(ctx, session: str, verbosity: str) -> str:
    if verbosity != "everything":
        return ""
    cue = "Press the option's number to choose, or Escape to cancel."
    st = ctx.host._stream(session)
    if not st.warned_immediate:
        st.warned_immediate = True
        cue += " Selecting is immediate."
    return cue


def _choice_notes(msg) -> str:
    notes = []
    questions = msg.get("questions", []) or []
    if any(isinstance(q, dict) and q.get("multiSelect") for q in questions):
        notes.append(
            "Select multiple: press each number, or Space on the "
            "highlighted item, then Enter to confirm."
        )
    if any(
        isinstance(q, dict) and len(q.get("options", []) or []) > 9
        for q in questions
    ):
        notes.append("More than nine options; use arrow keys for ten and up.")
    return " ".join(notes)


@handler(MsgType.CHOICE)
def on_choice(ctx, msg):
    session = ctx.session
    verbosity = ctx.verbosity
    text = _choice_text(msg)
    extras = [e for e in (
        _choice_notes(msg),
        _selection_cue(ctx, session, verbosity),
    ) if e]
    if extras:
        text = "{0} {1}".format(text, " ".join(extras))
    # The flip: gating moved to playback. Every session enqueues its own
    # decision into its own stream; the foreground-driven loop voices it.
    _, text = _announce_decision(ctx, session, "choice", text, answerable=False)
    ctx.host._stream(session).options = text
    return None


@handler(MsgType.PLAN)
def on_plan(ctx, msg):
    session = ctx.session
    verbosity = ctx.verbosity
    text = _plan_text(msg)
    cue = _selection_cue(ctx, session, verbosity)
    if cue:
        text = "{0} {1}".format(text, cue)
    _, text = _announce_decision(ctx, session, "plan", text, answerable=False)
    ctx.host._stream(session).options = text
    return None


@handler(MsgType.PERMISSION)
def on_permission(ctx, msg):
    session = ctx.session
    verbosity = ctx.verbosity
    text = _permission_text(msg)
    cue = _selection_cue(ctx, session, verbosity)
    if cue:
        text = "{0} {1}".format(text, cue)
    _, text = _announce_decision(ctx, session, "permission", text, answerable=False)
    ctx.host._stream(session).options = text
    return None


def _permission_request_text(msg) -> str:
    # Render the spoken prompt for a blocking PermissionRequest. The payload carries the
    # tool name + a short summary (Bash command / file). Prefer an explicit action/message
    # if present (forward-compatible), else "{tool}: {summary}".
    action = (msg.get("action") or "").strip()
    if action:
        return action
    tool = (msg.get("tool") or "").strip()
    summary = (msg.get("summary") or "").strip()
    if tool and summary and summary != tool:
        return "{0}: {1}".format(tool, summary)
    return summary or tool or "Permission needed."


@handler(MsgType.PERMISSION_REQUEST)
def on_permission_request(ctx, msg):
    # BLOCKING permission ask from the PermissionRequest hook. Speak the prompt on the
    # ASKING session as a decision item (so ⌃⌘D lands on it), register a pending decision,
    # and return the AWAIT sentinel — _handle_message_guarded then blocks OUTSIDE the lock.
    # NOTE: st.options is deliberately NOT set here (W4: reread falls back to the
    # stored pending text), and answerable=True — the one keyboard-serviceable ask.
    host = ctx.host
    session = ctx.session
    host.cue("permission")     # arrival chime, immediate; the call-sign binds
                               # to the enqueued ask below (ruling 1)
    item_id, text = _announce_decision(ctx, session, "permission",
                                       _permission_request_text(msg), answerable=True)
    teaching.maybe_hint(host, "decision", session)
    # We are under the daemon lock here, so mutate the store directly.
    prev = host._pending_decisions.get(session)
    if prev is not None:
        prev["event"].set()                  # release any stale waiter for this session
    host._pending_decisions[session] = {"event": threading.Event(), "behavior": None,
                                        "text": text, "item_id": item_id}
    return {"__await_decision__": True, "session": session}


@handler(MsgType.ANSWER_PERMISSION)
def on_answer_permission(ctx, msg):
    # ⌃⌘⏎ approve / ⌃⌘⎋ deny. Answer ONLY the focused session's own pending decision.
    host = ctx.host
    behavior = msg.get("behavior")
    if behavior not in ("allow", "deny"):
        host.cue("error")
        return None
    target = host.sessions.workspace()
    pd = host._pending_decisions.get(target) if target is not None else None
    if pd is None:
        # W6 misdirected, now the RL5 false-remedy HEAL: the tone stays the
        # instant part; the word says WHERE the live ask actually is — the
        # asker's spoken short label, same source as its spearcon text — or
        # that none exists anywhere. A None workspace has nowhere to speak
        # the word: tone only. First-registered pending entry wins (dict
        # insertion order == oldest live ask); it can never be `target`
        # (target's own miss is what brought us here).
        if target is None:
            host.cue("error_misdirected")
            return None
        other = next(iter(host._pending_decisions), None)
        if other is not None:
            label = spearcon_label(host.sessions.folder(other) or "") or "another session"
            word = MISDIRECT_ROUTE_WORD.format(label)
        else:
            word = MISDIRECT_EMPTY_WORD
        host.cue("error_misdirected", word=word, session=target)
        return None
    pd["behavior"] = behavior
    pd["event"].set()
    host.speaker.cancel()                     # barge-in: confirm immediately
    # Owner ruling 3: the directional chirp is the confirm's PRELUDE — barge,
    # then chirp, then the word, strictly ordered as one unit (the barge already
    # cleared the channel, so binding costs no latency).
    # RR-3 (fix-wave E): same seam as the settings readbacks — target falls
    # back to workspace() unconditionally, so a dead workspace strands this
    # confirm unless a deliberate press sanctions it. at_front below is
    # already unconditional (barge-in), so the sanction call is only needed
    # for its marking side effect; live destinations are untouched.
    host._sanction_dead_read(target, whole=False)
    host._enqueue(target, "prose",
                  "Approved." if behavior == "allow" else "Denied.",
                  False, control_cue=True, at_front=True,
                  prelude=(host.speaker.pitch_asset(
                      "up" if behavior == "allow" else "down"),))
    return None


@handler(MsgType.REREAD_OPTIONS)
def on_reread_options(ctx, msg):
    fg = ctx.host.sessions.foreground()
    if fg is None:
        return None
    st = ctx.host._streams.get(fg)
    text = st.options if st is not None else None
    if not text:
        # W4 sub-item: a live blocking permission never sets st.options
        # (on_permission_request writes _pending_decisions, not options), so
        # without this fallback REREAD_OPTIONS is silently broken for exactly
        # the asks that matter. Re-speak the stored prompt instead of lying.
        pending = ctx.host._pending_decisions.get(fg)
        if pending is not None:
            text = pending.get("text")
    # T2 (wave1 safety-net closure, owner-ruled 2026-08-15): both enqueues below
    # compose into foreground() unconditionally (the different accessor from
    # the workspace()-targeted sites above) — a dead foreground with the voice
    # idle strands either one without the single-item sanction (RR-2 shape).
    if text:
        ctx.host._enqueue(fg, "choice", text, False,
                          at_front=ctx.host._sanction_dead_read(fg, whole=False))
    else:
        ctx.host._enqueue(fg, "prose", "No options right now.", False,
                          at_front=ctx.host._sanction_dead_read(fg, whole=False))
    return None
