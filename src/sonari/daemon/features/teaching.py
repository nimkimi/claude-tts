"""Self-teaching: learn mode (SP-D1). The daemon teaches its own cockpit.

Learn-mode interception itself lives in SpeechDaemon._dispatch_hotkey (it must
see the raw hotkey message before dispatch); this module owns the toggle."""
from __future__ import annotations

from sonari.protocol import MsgType
from sonari.daemon.registry import handler

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
        host._enqueue(ws, "prose", LEARN_ON if entering else LEARN_OFF, False,
                      mute_exempt=True, pause_exempt=True)
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
