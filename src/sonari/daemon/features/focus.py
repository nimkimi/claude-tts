from __future__ import annotations

import sys

from sonari.protocol import MsgType
from sonari.daemon.registry import handler


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


@handler(MsgType.OS_FOCUS)
def on_os_focus(ctx, msg):
    """Inbound OS-focus signal from the focus-watcher. Session-less: reads the front
    terminal's identity off the message and resolves it to a session. Fire-and-forget."""
    ctx.host.sessions.set_os_focus(
        term_program=msg.get("term_program", ""),
        tty=msg.get("tty", ""),
        iterm_session_id=msg.get("iterm_session_id", ""),
        focused=msg.get("focused", True),
    )
    return None


@handler(MsgType.JUMP_WAITING)
def on_jump_waiting(ctx, msg):
    fg = ctx.host.sessions.foreground()
    target = _waiting_target(ctx, exclude=fg)
    if target is None:
        # Nothing waiting: say so. Route to speaker() so the cue lands in the
        # stream the speak loop is already reading (held or normal branch both
        # read speaker()). When speaker() is None (loop idle), foreground() is
        # the next session keep-going will adopt — enqueue there so it isn't
        # lost. If both are None, fall back to an error earcon.
        # NOTE: fg is kept for the _waiting_target exclude= arg above; don't
        # use it as the enqueue target — that's the divergence bug this fixes.
        tgt = ctx.host.sessions.speaker() or fg
        if tgt is not None:
            ctx.host._enqueue(tgt, "prose", "No session waiting.", False,
                              mute_exempt=True, pause_exempt=True)
        else:
            ctx.host.speaker.earcon("error")
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
        if not will_raise:
            ctx.host._enqueue(target, "prose", "Bring it forward to type.", False,
                              mute_exempt=True, at_front=True)
        ctx.host._enqueue(target, "prose", folder, False, audio_path=spearcon,
                          mute_exempt=True, at_front=True, names_session=True)
    else:
        base = ("Jumping to {0}.".format(folder) if folder
                else "Jumping to another session.")
        if not will_raise:
            base += " Bring it forward to type."
        ctx.host._enqueue(target, "prose", base, False,
                          mute_exempt=True, at_front=True, names_session=True)
    if will_raise:
        ctx.host._raise().raise_async(
            identity, gen,
            on_failure=lambda s=target, f=folder: ctx.host._raise_failed(s, f))
    return None


@handler(MsgType.CYCLE_SESSION)
def on_cycle_session(ctx, msg):
    # ⌃⌘Tab / ⌃⌘⇧Tab: cycle the VOICE through the roster in insertion order, wrapping.
    # Raises the target window (a deliberate cycle is a workspace action).
    sessions = ctx.host.sessions
    # Fork 2 = KEEP: the roster INCLUDES muted sessions (filter at the CALL SITE, never
    # in session_ids(), so the insertion-order pins in test_sessions.py survive). W1:
    # also drop PHANTOM sessions (closed terminal -> dead tty node) via is_live — a pure
    # read. is_live is independent of `stopped`, so a muted-but-live session stays
    # cycle-reachable (R7); only muted+dead (or active+dead) drops.
    roster = [s for s in sessions.session_ids() if sessions.is_live(s)]
    if len(roster) < 2:
        ctx.host.speaker.earcon("error")          # <2 sessions: confirm fired, no silent no-op
        return None
    # Fork 1 = workspace(): anchor on the front window (moves only on deliberate acts),
    # so consecutive ⌃⌘Tabs step deterministically — fixes the live-test anchor-drift
    # (keep-going advances speaker() between presses; sessions.py:79-83).
    fg = sessions.workspace()
    cur = roster.index(fg) if fg in roster else 0
    step = 1 if msg.get("direction", "next") == "next" else -1
    target = roster[(cur + step) % len(roster)]
    sessions.focus(target)                        # workspace + voice -> target; raises
    ctx.host.speaker.cancel()
    ctx.host.voice_state = "flowing"              # cycle is a deliberate re-engage (req 9)
    if ctx.host._stream(target).stopped:
        # Cycle-onto-muted (RATIFIED, RECON:184 #3): keep the WORKSPACE on the muted
        # target (focus + raise), but RELEASE the voice so the keep-going scan moves it
        # to another ACTIVE session (speaker != workspace). set_speaker(None): the next
        # speak-loop pass (now flowing) picks the longest-waiting active session; if
        # none, _speaker stays None (flowing-but-silent) and ⌃⌘W reports the state.
        # Do NOT un-mute the target (R7:191 — it stays muted until its own ⌃⌘S-start).
        sessions.set_speaker(None)
    folder = sessions.folder(target)
    identity = sessions.identity(target)
    will_raise = ctx.host._raise().will_attempt(identity)
    gen = ctx.host._raise().bump_generation()
    cue = folder + "." if folder else "Another session."
    ctx.host._enqueue(target, "prose", cue, False,
                      audio_path=ctx.host._spearcon_path(folder),
                      mute_exempt=True, at_front=True, names_session=True)
    if will_raise:
        ctx.host._raise().raise_async(
            identity, gen,
            on_failure=lambda s=target, f=folder: ctx.host._raise_failed(s, f))
    return None
