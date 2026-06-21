from __future__ import annotations

from sonari.protocol import MsgType
from sonari.daemon.registry import handler


@handler(MsgType.JUMP_WAITING)
def on_jump_waiting(ctx, msg):
    fg = ctx.host.sessions.foreground()
    target = ctx.host._waiting_target(exclude=fg)
    if target is None:
        # Nothing waiting: say so (mute_exempt so it's always heard). With no
        # foreground to speak through, fall back to an error earcon.
        if fg is not None:
            ctx.host._enqueue(fg, "prose", "No session waiting.", False,
                              mute_exempt=True)
        else:
            ctx.host.speaker.earcon("error")
        return None
    # Explicit move: clear any pin, switch the VOICE (not OS focus) to the
    # target, cut the current utterance so the switch is immediate, and lead
    # with a spoken folder label. The foreground-driven loop then drains the
    # target's accumulated backlog.
    ctx.host.sessions.focus(target)
    ctx.host.speaker.cancel()
    folder = ctx.host.sessions.folder(target)
    identity = ctx.host.sessions.identity(target)
    will_raise = ctx.host._raise().will_attempt(identity)
    # Bump the jump generation on EVERY jump, not only raising ones. A jump to
    # a non-followable target must still advance the generation so a prior
    # in-flight raise sees itself superseded (its _is_current(genOld) check
    # returns False -> no-ops). If this lived inside `if will_raise:`, a
    # non-raising jump B would leave the generation pinned at A's value, and a
    # slow raise(A) would yank focus back to A while the voice is on B (spec
    # §4.5 lines 191-201).
    gen = ctx.host._raise().bump_generation()
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
