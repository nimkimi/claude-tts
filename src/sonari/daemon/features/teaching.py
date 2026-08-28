"""Self-teaching: learn mode (SP-D1). The daemon teaches its own cockpit.

Learn-mode interception lives in SpeechDaemon.handle_message, the single dispatch
chokepoint (socket / hotkey / catch-up all funnel through it): while learn mode is
on, any message that resolves to a registered action speaks that action's teach
line instead of dispatching. Non-action-shaped messages (CLI control carrying a
"v" key, hook prose) never equal a registered action, so they are never taught; an
action-shaped message teaches regardless of transport — on macOS the socket IS the
hotkey transport (hotkeyd sends resolved action messages over it). This module
owns the toggle.

Strings checked against the D3 liveness tiers 2026-08-01 — unchanged: the
"waiting"-phrased hints below stay true under jump-waiting's live-only
definition (spec D3 §4j)."""
from __future__ import annotations

from sonari.protocol import MsgType
from sonari.daemon.registry import handler
from sonari.daemon.features.control import _has_decision

# wording provisional, pending owner ear-pass
LEARN_ON = ("Learn mode. Press any Sonari key to hear what it does. "
            "Press the same key again to exit.")
LEARN_OFF = "Learn mode off."


@handler(MsgType.LEARN_MODE)
def on_learn_mode(ctx, msg):
    host = ctx.host
    entering = not host._learn_mode
    host._set_learn_mode(entering)
    ws = host.sessions.workspace()
    if ws is not None:
        # T2 (wave1 safety-net closure, owner-ruled 2026-08-15): the toggle
        # composes into workspace() unconditionally, same RR-2 shape as the
        # settings readbacks — a dead workspace with the voice idle strands
        # this without the single-item sanction (host.py _sanction_dead_read).
        host._enqueue(ws, "prose", LEARN_ON if entering else LEARN_OFF, False,
                      control_cue=True,
                      at_front=host._sanction_dead_read(ws, whole=False))
    return None


# wording provisional, pending owner ear-pass
HINTS = {
    "decision": ("Press Control Command Return to approve, Escape to deny, "
                 "or Control Command O to hear the options again."),
    "background_turn": ("A background session finished. "
                        "Control Command J jumps the voice to it."),
    "chooser": ("Hold the chord and tap Tab to browse. Release to switch. "
                "Digits teleport."),
    "catch_up_done": "That was a summary. Control Command R repeats it.",
}


# wording provisional, pending owner ear-pass
QUERY_DECISION = ("A decision is waiting. Control Command Return approves, "
                  "Escape denies, O re-reads the options.")
QUERY_STOPPED = "Speech is stopped. Control Command S resumes this session."
QUERY_DEFAULT = ("Control Command W says where you are. J jumps to a waiting "
                 "session. Hold Tab on the chord to browse sessions.")


@handler(MsgType.QUERY_ACTIONS)
def on_query_actions(ctx, msg):
    host = ctx.host
    ws = host.sessions.workspace()
    if ws is None:
        return None
    if _has_decision(host, ws):
        text = QUERY_DECISION
    elif host.voice_state != "flowing":
        text = QUERY_STOPPED
    else:
        text = QUERY_DEFAULT
    # T2: same shape as the toggle above — a dead workspace with the voice
    # idle stranded this readout without the single-item sanction.
    host._enqueue(ws, "prose", text, False,
                  control_cue=True,
                  at_front=host._sanction_dead_read(ws, whole=False))
    return None


def maybe_hint(host, key, session) -> None:
    """First-encounter hint: once per daemon run, 'everything' verbosity only.

    Marks the key consumed ONLY when there is a session to actually speak it
    into. A moment that has nothing playable (e.g. the chooser's first preview
    landing on no live speaker AND a muted workspace) must not permanently
    burn the one-shot with nothing ever heard -- leave the key open so the
    next real encounter this daemon run still teaches it."""
    if key in host._hinted:
        return
    if host.config.get("verbosity", "everything") != "everything":
        return
    if session is None:
        return
    host._hinted.add(key)
    host._enqueue(session, "prose", HINTS[key], False)
