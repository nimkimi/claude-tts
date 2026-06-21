from __future__ import annotations

from sonari.protocol import MsgType
from sonari.daemon.registry import handler
from sonari.config import save_config
from sonari.daemon.limits import RATE_MIN, RATE_MAX, MINQUEUE_MIN, MINQUEUE_MAX

# The three known verbosity levels (must match on_cycle_verbosity order).
VERBOSITY_LEVELS = ("everything", "medium", "quiet")


def _clamp_int(raw, lo, hi):
    """Return int(raw) clamped to [lo, hi], or None if raw is not a valid int."""
    try:
        return max(lo, min(hi, int(raw)))
    except (TypeError, ValueError):
        return None


def _valid_verbosity(raw):
    """Return raw if it is a known verbosity level, else None."""
    return raw if raw in VERBOSITY_LEVELS else None


def _valid_voice(raw):
    """Return raw if it is a non-empty string, else None."""
    return raw if isinstance(raw, str) and raw.strip() else None


@handler(MsgType.SET_RATE)
def on_set_rate(ctx, msg):
    is_delta = "delta" in msg
    if is_delta:
        # Parse both values first (matching original single try/except), then
        # clamp only the SUM — pre-clamping cur would shift the result when the
        # stored rate is outside [RATE_MIN, RATE_MAX] (e.g. a stale hand-edited
        # config), producing a different final value than the original behavior.
        try:
            base = int(ctx.host.config.get("rate", 200)) + int(msg.get("delta", 0))
        except (TypeError, ValueError):
            return None
        rate = _clamp_int(base, RATE_MIN, RATE_MAX)  # base is int, never None
    else:
        # Validate/clamp the absolute rate — an unvalidated value persisted to
        # disk breaks synthesis on every utterance until the bad config is removed.
        rate = _clamp_int(msg.get("rate"), RATE_MIN, RATE_MAX)
        if rate is None:
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
    voice = _valid_voice(msg.get("voice"))
    if voice is None:
        return None
    ctx.host.config["voice"] = voice
    ctx.host.speaker.set_voice(voice)
    save_config(ctx.host.config)
    return None


@handler(MsgType.SET_VERBOSITY)
def on_set_verbosity(ctx, msg):
    v = _valid_verbosity(msg.get("verbosity"))
    if v is None:
        return None
    ctx.host.config["verbosity"] = v
    save_config(ctx.host.config)
    return None


@handler(MsgType.SET_MINQUEUE)
def on_set_minqueue(ctx, msg):
    # Validate/clamp before persisting — a bad value reaches disk and would
    # wedge prose buffering on every turn (mirrors the SET_RATE guard).
    n = _clamp_int(msg.get("minqueue"), MINQUEUE_MIN, MINQUEUE_MAX)
    if n is None:
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
