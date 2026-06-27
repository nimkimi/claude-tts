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


@handler(MsgType.WHERE_AM_I)
def on_where_am_i(ctx, msg):
    # ⌃⌘W "where am I": a terse SPOKEN status (distinct from the CLI STATUS dict),
    # barge-in + interjection-resume per §7. Plain text for sub-project B (spearcon /
    # pitch polish is sub-project D): "{folder}. {Playing|Stopped}. {N} waiting."
    host = ctx.host
    fg = host.sessions.foreground()
    if fg is None:
        host.speaker.earcon("error")              # always-confirm-fired: never a silent no-op
        return None
    # Capture the in-flight item BEFORE cancel so we can resume it afterwards.
    cur = host._current_item
    # Capture entry now: cancel() doesn't touch _pending_heard, but grabbing it here
    # keeps the invariant that we read all in-flight state before any mutation.
    entry = host._pending_heard.get(cur.id) if cur is not None else None
    folder = host.sessions.folder(fg) or "Unknown session"
    st = host._streams.get(fg)
    state = "Stopped" if (st is not None and st.stopped) else "Playing"
    # Waiting = background sessions with live, non-stopped backlog (mirrors _waiting_target).
    waiting = sum(1 for sess, s in host._streams.items()
                  if sess != fg and not s.stopped and len(s.queue) > 0)
    text = "{0}. {1}. {2} waiting.".format(folder, state, waiting)
    host.speaker.cancel()                          # barge-in: cut the current utterance
    # Resume-after-interjection: re-queue the interrupted item at the front (BEHIND the
    # status cue), carrying its pending-heard entry on a FRESH item id so the speak
    # loop's note_spoken (which pops the OLD id with completed=False) can't lose it.
    if cur is not None:
        host._enqueue(cur.session, cur.kind, cur.text, cur.is_decision,
                      entry=entry, mute_exempt=cur.mute_exempt,
                      pause_exempt=cur.pause_exempt, names_session=cur.names_session,
                      at_front=True)
    # Status cue at the very front (plays FIRST). pause_exempt so ⌃⌘W speaks even when the
    # foreground session is stopped; mute_exempt so it is never folder-prefixed.
    host._enqueue(fg, "prose", text, False,
                  mute_exempt=True, pause_exempt=True, at_front=True)
    return None


@handler(MsgType.PING)
def on_ping(ctx, msg):
    return {"ok": True}
