from __future__ import annotations

import threading

from sonari.protocol import MsgType
from sonari.daemon.registry import handler


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
    session = ctx.session                 # was: msg.get("session", "")
    verbosity = ctx.verbosity             # was: self.config.get("verbosity", "everything")
    text = _choice_text(msg)
    extras = [e for e in (
        _choice_notes(msg),
        _selection_cue(ctx, session, verbosity),
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
    text = _plan_text(msg)
    cue = _selection_cue(ctx, session, verbosity)
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
    text = _permission_text(msg)
    cue = _selection_cue(ctx, session, verbosity)
    if cue:
        text = "{0} {1}".format(text, cue)
    ctx.host._stream(session).options = text
    entry = ctx.host.history.record(session, "permission", text)
    ctx.host.history.end_message(session)
    # The flip: enqueue unconditionally into this session's own stream.
    ctx.host._flush_prose_buffer(session)   # prose before the permission ask
    ctx.host._enqueue(session, "permission", text, True, entry=entry)
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
    host = ctx.host
    session = ctx.session
    text = _permission_request_text(msg)
    host.speaker.earcon("permission")
    entry = host.history.record(session, "permission", text)
    host.history.end_message(session)
    host._flush_prose_buffer(session)        # prose before the permission ask
    host._enqueue(session, "permission", text, True, entry=entry)
    # We are under the daemon lock here, so mutate the store directly.
    prev = host._pending_decisions.get(session)
    if prev is not None:
        prev["event"].set()                  # release any stale waiter for this session
    host._pending_decisions[session] = {"event": threading.Event(), "behavior": None}
    return {"__await_decision__": True, "session": session}


@handler(MsgType.ANSWER_PERMISSION)
def on_answer_permission(ctx, msg):
    # ⌃⌘⏎ approve / ⌃⌘⎋ deny. Answer ONLY the focused session's own pending decision.
    host = ctx.host
    behavior = msg.get("behavior")
    if behavior not in ("allow", "deny"):
        host.speaker.earcon("error")
        return None
    target = host.sessions.focused_session() or host.sessions.foreground()
    pd = host._pending_decisions.get(target) if target is not None else None
    if pd is None:
        host.speaker.earcon("error")         # nothing to answer on the focused session
        return None
    pd["behavior"] = behavior
    pd["event"].set()
    host.speaker.cancel()                     # barge-in: confirm immediately
    host._enqueue(target, "prose",
                  "Approved." if behavior == "allow" else "Denied.",
                  False, mute_exempt=True, at_front=True)
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
