from __future__ import annotations

import time

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
    host = ctx.host
    last_drain = host._last_drain
    return {
        # Original 6 keys — kept verbatim for backward-compat.
        "verbosity": host.config.get("verbosity"),
        "rate": host.config.get("rate"),
        "voice": host.config.get("voice"),
        "foreground": host.sessions.foreground(),
        "queue_len": sum(len(st.queue) for st in host._streams.values()),
        "minqueue": host.config.get("minqueue"),
        # Diagnostic additions (DIAG-3).
        # Per-session snapshot: one entry per known stream.
        "sessions": [
            {"session": sid, "queue_len": len(st.queue), "stopped": st.stopped}
            for sid, st in host._streams.items()
        ],
        "session_count": len(host._streams),
        # Wall-clock seconds since construction. Normally >=0, but time.time()
        # is not monotonic (NTP / manual clock step) so this can briefly go
        # backward; for a wedge-vs-idle read prefer last_drain_age_s (monotonic).
        "uptime_s": time.time() - host._started_at,
        # Monotonic age since the last drained item; None until the first drain.
        "last_drain_age_s": (
            time.monotonic() - last_drain if last_drain is not None else None
        ),
        # True when an item is currently claimed by the speak loop (in-flight utterance).
        # The voice-global mode (SPEC §6): flowing / quiet-hold / stopped-all. This
        # SUBSUMES the old "no global stop_all flag" note — stopped-all is now a
        # first-class state surfaced here (per-stream st.stopped stays in "sessions").
        "current_item": host._state._current_item is not None,
        "voice_state": host.voice_state,
    }


@handler(MsgType.WHERE_AM_I)
def on_where_am_i(ctx, msg):
    # ⌃⌘W "where am I": a terse SPOKEN status (distinct from the CLI STATUS dict),
    # barge-in + interjection-resume per §7. Plain text for sub-project B (spearcon /
    # pitch polish is sub-project D): "{folder}. {Playing|Stopped}. {N} waiting."
    host = ctx.host
    # Report the SPEAKER's state (voice-state), not the workspace. §8 reconciliation:
    # ⌃⌘W answers "what am I hearing?" — in the keep-going era the speaker may differ
    # from the foreground, so the status cue is enqueued to the speaker's stream
    # (the held branch reads speaker(), ensuring it's voiced under divergence).
    fg = host.sessions.speaker()
    if fg is None:
        # speaker() None is LEGITIMATE post-SP3 (stopped-all all-ended; cycle-onto-
        # muted with nothing active). Report the voice-state to a PLAYABLE workspace
        # stream rather than error-toning (R7 discoverability). DELIVERY NOTE: the loop
        # plays speaker() (None here), so the cue must land where keep-going can adopt
        # it — a NON-stopped workspace stream (keep-going skips stopped streams). A
        # muted/None workspace has nothing voiceable -> the honest fallback is the error
        # earcon. (A workspace with no stream yet counts as playable: _enqueue creates it
        # non-stopped and keep-going then adopts it.)
        # BEHAVIOR NAMED (vs (c)#4 "⌃⌘W never moves the voice"): (c)#4 forbids ⌃⌘W
        # STEALING the voice from an ACTIVE speaker. Here speaker() is None — the voice
        # is IDLE — so keep-going adopting the playable workspace (effectively
        # set_speaker(workspace) on the next loop turn) is the idle voice landing on
        # where you already are, NOT a steal. This is intended, not a (c)#4 violation.
        ws = host.sessions.workspace()
        ws_st = host._streams.get(ws) if ws is not None else None
        playable = ws is not None and not (ws_st is not None and ws_st.stopped)
        if playable:
            vs = host.voice_state
            cue = ("All stopped." if vs == "stopped-all"
                   else "On hold." if vs == "quiet-hold"
                   else "Nothing playing.")
            host._enqueue(ws, "prose", cue, False, mute_exempt=True, pause_exempt=True)
        else:
            host.speaker.earcon("error")
        return None
    # Capture the in-flight item BEFORE cancel so we can resume it afterwards.
    cur = host._current_item
    # Capture entry now: cancel() doesn't touch _pending_heard, but grabbing it here
    # keeps the invariant that we read all in-flight state before any mutation.
    entry = host._pending_heard.get(cur.id) if cur is not None else None
    folder = host.sessions.folder(fg) or "Unknown session"
    st = host._streams.get(fg)
    vs = host.voice_state
    if vs == "stopped-all":
        state = "All stopped"
    elif vs == "quiet-hold":
        state = "On hold"
    else:
        state = "Stopped" if (st is not None and st.stopped) else "Playing"
    # Waiting = background sessions with live, non-stopped backlog (mirrors _waiting_target).
    waiting = sum(1 for sess, s in host._streams.items()
                  if sess != fg and not s.stopped and len(s.queue) > 0)
    host.speaker.cancel()                          # barge-in: cut the current utterance
    # Resume-after-interjection: re-queue the interrupted item FIRST so it ends up
    # DEEPEST (the status cue / spearcon are appendleft'd in front of it below).
    if cur is not None:
        host._enqueue(cur.session, cur.kind, cur.text, cur.is_decision,
                      entry=entry, mute_exempt=cur.mute_exempt,
                      pause_exempt=cur.pause_exempt, names_session=cur.names_session,
                      audio_path=cur.audio_path, at_front=True)
    spearcon = host._spearcon_path(folder)
    if spearcon:
        # Spearcon names the session (replaces the spoken folder); state + count stay
        # speech. Enqueue state FIRST (at_front), then the spearcon (at_front) so the
        # head order is: spearcon, state, [resumed item].
        host._enqueue(fg, "prose", "{0}. {1} waiting.".format(state, waiting),
                      False, mute_exempt=True, pause_exempt=True, at_front=True)
        host._enqueue(fg, "prose", folder, False, audio_path=spearcon,
                      mute_exempt=True, pause_exempt=True, at_front=True,
                      names_session=True)
    else:
        host._enqueue(fg, "prose",
                      "{0}. {1}. {2} waiting.".format(folder, state, waiting),
                      False, mute_exempt=True, pause_exempt=True, at_front=True)
    return None


@handler(MsgType.PING)
def on_ping(ctx, msg):
    return {"ok": True}
