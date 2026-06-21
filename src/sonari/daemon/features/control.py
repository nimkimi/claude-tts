from __future__ import annotations

from sonari.protocol import MsgType
from sonari.daemon.registry import handler
from sonari.config import save_config
from sonari.daemon.limits import RATE_MIN, RATE_MAX, MINQUEUE_MIN, MINQUEUE_MAX


@handler(MsgType.SET_RATE)
def on_set_rate(ctx, msg):
    is_delta = "delta" in msg
    if is_delta:
        try:
            cur = int(ctx.host.config.get("rate", 200))
            rate = max(RATE_MIN, min(RATE_MAX, cur + int(msg.get("delta", 0))))
        except (ValueError, TypeError):
            return None
    else:
        # Validate/clamp the absolute rate just like the delta branch — an
        # unvalidated value here is persisted to disk and breaks synthesis.
        try:
            rate = max(RATE_MIN, min(RATE_MAX, int(msg.get("rate"))))
        except (TypeError, ValueError):
            return None
    ctx.host.config["rate"] = rate
    ctx.host.speaker.set_rate(rate)
    save_config(ctx.host.config)
    if is_delta:
        fg = ctx.host.sessions.foreground()
        if fg is not None:
            ctx.host._enqueue(fg, "prose", "Rate {0}.".format(rate), False)
    return None


@handler(MsgType.SET_VOICE)
def on_set_voice(ctx, msg):
    voice = msg.get("voice")
    ctx.host.config["voice"] = voice
    ctx.host.speaker.set_voice(voice)
    save_config(ctx.host.config)
    return None


@handler(MsgType.SET_VERBOSITY)
def on_set_verbosity(ctx, msg):
    ctx.host.config["verbosity"] = msg.get("verbosity")
    save_config(ctx.host.config)
    return None


@handler(MsgType.SET_MINQUEUE)
def on_set_minqueue(ctx, msg):
    # Validate/clamp before persisting — a bad value reaches disk and would
    # wedge prose buffering on every turn (mirrors the SET_RATE guard).
    try:
        n = max(MINQUEUE_MIN, min(MINQUEUE_MAX, int(msg.get("minqueue"))))
    except (TypeError, ValueError):
        return None
    ctx.host.config["minqueue"] = n
    save_config(ctx.host.config)
    return None


@handler(MsgType.CYCLE_VERBOSITY)
def on_cycle_verbosity(ctx, msg):
    order = ["everything", "medium", "quiet"]
    cur = ctx.host.config.get("verbosity", "everything")
    if cur in order:
        nxt = order[(order.index(cur) + 1) % len(order)]
    else:
        nxt = order[0]
    ctx.host.config["verbosity"] = nxt
    save_config(ctx.host.config)
    fg = ctx.host.sessions.foreground()
    if fg is not None:
        ctx.host._enqueue(fg, "prose", "Verbosity {0}.".format(nxt), False)
    return None


@handler(MsgType.STATUS)
def on_status(ctx, msg):
    return {
        "verbosity": ctx.host.config.get("verbosity"),
        "rate": ctx.host.config.get("rate"),
        "voice": ctx.host.config.get("voice"),
        "foreground": ctx.host.sessions.foreground(),
        "queue_len": sum(len(st.queue) for st in ctx.host._streams.values()),
        "minqueue": ctx.host.config.get("minqueue"),
    }


@handler(MsgType.PING)
def on_ping(ctx, msg):
    return {"ok": True}
