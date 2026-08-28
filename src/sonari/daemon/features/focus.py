from __future__ import annotations

import sys

from sonari.protocol import MsgType
from sonari.daemon.registry import handler
from sonari.daemon.features.control import CLOSED_WORD, _COUNT_WORDS


def _waiting_target(ctx, exclude):
    """The background session jump-to-waiting should switch to, or None.

    Considers only streams with a non-empty, non-stopped queue (live backlog —
    Stage 3 keys off the queue, not history). A stream holding an unplayed
    decision (choice|plan|permission) ranks ahead of prose-only ones; ties break
    by session insertion order. Excludes *exclude* (the current foreground) and
    the current speaker() — both are already receiving attention."""
    blocked, prose = [], []
    sessions = ctx.host.sessions
    spk = sessions.speaker()
    for sess, st in ctx.host._streams.items():          # insertion-ordered
        if (sess == exclude or sess == spk or st.stopped
                or len(st.queue) == 0 or not sessions.is_live(sess)):
            continue
        (blocked if st.queue.has_decision() else prose).append(sess)
    ordered = blocked + prose
    return ordered[0] if ordered else None


def _empty_case_tail(ctx, exclude):
    """D3 spec §4c(2): when nothing is offered, distinguish a bare fleet from
    one where non-live sessions are sitting on unreachable backlog — the old
    plain 'No session waiting.' was identical for both (the empty-case lie).
    Mirrors _waiting_target's stream criteria minus the liveness clause (same
    sessions that WOULD have been offered but for liveness), tallied by tier.
    Returns '' for a bare fleet (byte-exact plain string preserved)."""
    sessions = ctx.host.sessions
    spk = sessions.speaker()
    pending = dead = 0
    for sess, st in ctx.host._streams.items():
        if sess == exclude or sess == spk or st.stopped or len(st.queue) == 0:
            continue
        lv = sessions.liveness(sess)
        if lv == "pending":
            pending += 1
        elif lv == "dead":
            dead += 1
    tail = ""
    # PROVISIONAL (ear-batch-3), D3 §5 slot 4: pending sentence first, then
    # closed — same digit-free-above-nine degrade as the also-map's tails.
    if pending:
        tail += " {0} pending.".format(_COUNT_WORDS.get(pending, "many").capitalize())
    if dead:
        tail += " {0} closed.".format(_COUNT_WORDS.get(dead, "many").capitalize())
    return tail


@handler(MsgType.OS_FOCUS)
def on_os_focus(ctx, msg):
    """Inbound OS-focus signal from the focus-watcher. Session-less: reads the front
    terminal's identity off the message and resolves it to a session. Fire-and-forget."""
    changed = ctx.host.sessions.set_os_focus(
        term_program=msg.get("term_program", ""),
        tty=msg.get("tty", ""),
        iterm_session_id=msg.get("iterm_session_id", ""),
        focused=msg.get("focused", True),
    )
    if changed:
        # D2 §6.2 (RL3): the workspace under your keyboard is now a DIFFERENT
        # session — mark the crossing. Tone-only (clicks are too frequent for
        # words); the no-op refocus / every-click cases return False above.
        ctx.host.cue("repoint")
    return None


@handler(MsgType.JUMP_WAITING)
def on_jump_waiting(ctx, msg):
    # W11 collapse: the exclusion is the WORKSPACE (front terminal + keyboard),
    # not the internal foreground pointer. ⌃⌘J never jumps to the session your
    # keyboard is on (workspace()) nor the one you're hearing (speaker(), already
    # excluded in _waiting_target); every other waiting session is eligible.
    ws = ctx.host.sessions.workspace()
    target = _waiting_target(ctx, exclude=ws)
    if target is None:
        # Nothing waiting: say so. Route to speaker() so the cue lands in the
        # stream the speak loop is already reading. When speaker() is None (loop
        # idle), workspace() is where you are — enqueue there so it isn't lost
        # (W11: foreground() is no longer a gesture target). If both are None,
        # fall back to an error earcon.
        tgt = ctx.host.sessions.speaker() or ws
        if tgt is not None:
            text = "No session waiting." + _empty_case_tail(ctx, ws)
            # RR-2: that fallback can resolve to a DEAD workspace, which post-T9
            # nothing adopts — the answer, tail and all, was composed into
            # silence. Single-item grain: this cue, not the closed pile behind it.
            ctx.host._sanction_dead_read(tgt, whole=False)
            ctx.host._enqueue(tgt, "prose", text, False,
                              control_cue=True, at_front=True)
        else:
            ctx.host.cue("error")
        return None
    # The selection→focus gap (D3 spec §4c): _waiting_target's candidates are
    # is_live-filtered at SELECTION time only — a target that died mid-flight
    # (between selection and this commit) must not be focused. Mirrors the
    # commit-time re-check the chooser already has (chooser.py _commit).
    if ctx.host.sessions.liveness(target) == "dead":
        dest = ctx.host.sessions.speaker() or ws
        ctx.host.cue("error", word=CLOSED_WORD, session=dest)   # D7a word, never silent
        return None
    # Explicit move: switch the VOICE (not OS focus) to the
    # target, cut the current utterance so the switch is immediate, and lead
    # with a spoken folder label. The foreground-driven loop then drains the
    # target's accumulated backlog.
    ctx.host.sessions.focus(target)
    ctx.host.voice_state = "flowing"                 # ⌃⌘J re-engage (SPEC:190,287)
    ctx.host.speaker.cancel()
    folder = ctx.host.sessions.folder(target)
    identity = ctx.host.sessions.identity(target)
    will_raise = ctx.host._raise().will_attempt(identity)
    # Diagnostic: classify identity state for debugging FOCUS-1 (jump_waiting raise fails).
    try:
        if identity is None:
            identity_class = "none"
        elif not identity.tty:
            identity_class = "tty-empty"
        else:
            identity_class = "present"
        print(f"sonari[focus]: jump_waiting target={target} identity={identity_class} will_raise={will_raise}",
              file=sys.stderr)
    except Exception:
        pass  # Never raise from diagnostic emit
    # Bump the jump generation on EVERY jump, not only raising ones. A jump to
    # a non-followable target must still advance the generation so a prior
    # in-flight raise sees itself superseded (its _is_current(genOld) check
    # returns False -> no-ops). If this lived inside `if will_raise:`, a
    # non-raising jump B would leave the generation pinned at A's value, and a
    # slow raise(A) would yank focus back to A while the voice is on B (spec
    # §4.5 lines 191-201).
    gen = ctx.host._raise().bump_generation()
    spearcon = ctx.host._spearcon_path(folder)
    if spearcon:
        # Spearcon names the destination (replaces the spoken "Jumping to {folder}.");
        # the actionable "Bring it forward to type." stays speech when not raising.
        # Enqueue the suffix FIRST (at_front), then the spearcon (at_front) so the
        # head order is: spearcon, [suffix].
        # Provably not stopped: target came from _waiting_target, which skips
        # st.stopped streams.
        if not will_raise:
            ctx.host._enqueue(target, "prose", "Bring it forward to type.", False,
                              control_cue=True, at_front=True)
        ctx.host._enqueue(target, "prose", folder, False, audio_path=spearcon,
                          control_cue=True, at_front=True, names_session=True)
    else:
        base = ("Jumping to {0}.".format(folder) if folder
                else "Jumping to another session.")
        if not will_raise:
            base += " Bring it forward to type."
        # Provably not stopped: target came from _waiting_target, which skips
        # st.stopped streams.
        ctx.host._enqueue(target, "prose", base, False,
                          control_cue=True, at_front=True, names_session=True)
    if will_raise:
        ctx.host._raise().raise_async(
            identity, gen,
            on_failure=lambda s=target, f=folder: ctx.host._raise_failed(s, f))
    return None
