from __future__ import annotations

import time

from sonari.protocol import MsgType
from sonari.daemon.registry import handler
from sonari.config import save_config
from sonari.daemon.limits import RATE_MIN, RATE_MAX, MINQUEUE_MIN, MINQUEUE_MAX

# The three known verbosity levels (must match on_cycle_verbosity order).
VERBOSITY_LEVELS = ("everything", "medium", "quiet")


def _numbered(host, session):
    """'{folder} {n}' — the spoken name+number for a session ('another session'
    fallback, §7; the number is omitted only if the session is somehow
    unregistered)."""
    folder = host.sessions.folder(session) or "another session"
    n = host.sessions.number(session)
    return "{0} {1}".format(folder, n) if n is not None else folder


def _also_clause(host, exclude=()):
    """The holistic ⌃⌘W 'Also:' map (§7): every registered session NOT in
    *exclude*, in NUMBER order, entries '{n} {folder}[, muted][, {k} waiting]'
    joined by '; ' — number-first, because the Also-list is the teleport
    dial-pad. Returns ' Also: {entries}.' (leading space, appendable to the
    lead sentence) or '' when no entries remain: the ABSENT landmark is the
    "no other sessions" signal, the same trained pattern as the Keyboard
    clause. Unfiltered by liveness, like the old counts (plan D4)."""
    sessions = host.sessions
    ids = sorted((s for s in sessions.session_ids() if s not in exclude),
                 key=lambda s: sessions.number(s) or 0)
    parts = []
    for s in ids:
        seg = "{0} {1}".format(sessions.number(s),
                               sessions.folder(s) or "another session")
        st = host._streams.get(s)
        if st is not None and st.stopped:
            seg += ", muted"
        k = len(st.queue) if st is not None else 0
        if k > 0:
            seg += ", {0} waiting".format(k)
        # W10: the recorded-but-not-queued unheard FLOOR. Queued items' history
        # entries are ALSO unheard until spoken (host.py:309-320 flips heard on
        # completion), so a raw len(unheard) double-counts every queued item —
        # subtract k (approximation in the caller's favor: never overstates).
        # unheard() is current-turn-bounded: the spoken count is a floor across
        # a multi-turn pile (documented; frontier counts are SP5, NOT built).
        # The word "unheard" is an OWNER GATE (his ear tunes it at review).
        u = max(0, len(host.history.unheard(s)) - k)
        if u > 0:
            seg += ", {0} unheard".format(u)
        parts.append(seg)
    return " Also: {0}.".format("; ".join(parts)) if parts else ""


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
        # W11: the terminal you're at hears its own confirmation ("Rate 250."
        # used to land on foreground() — a session you may not be hearing).
        ws = ctx.host.sessions.workspace()
        if ws is not None:
            ctx.host._enqueue(ws, "prose", "Rate {0}.".format(rate), False)
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
    # W3: confirm on the LIVE path (the built confirmation was stranded on the
    # dead CYCLE_VERBOSITY handler, 0 senders). Targets workspace() (W11's
    # collapsed pointer — the terminal you're at hears its own confirmation);
    # mute_exempt+pause_exempt so a settings readback can never be silently
    # swallowed while the voice is held — "Verbosity quiet." IS the last thing
    # you hear (direct _enqueue cues bypass the on_prose quiet gate).
    # Idempotent by design: setting the same value re-confirms (readback).
    ws = ctx.host.sessions.workspace()
    if ws is not None:
        ctx.host._enqueue(ws, "prose", "Verbosity {0}.".format(v), False,
                          mute_exempt=True, pause_exempt=True)
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
    # ⌃⌘W "where am I": ONE holistic SPOKEN readout (§7, amended 2026-07-14 —
    # the double-press roster is deleted), barge-in + interjection-resume
    # unchanged. Plain speech end-to-end (SP3.1 W3):
    # "Voice: {folder} {n}, {state}.[ Keyboard: {folder} {n}.][ Also: {entries}.]"
    # — the Keyboard clause only when the workspace resolves to a different
    # session; the Also-map names every OTHER registered session.
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
            # State-cue lead + the FULL map (§7): with no voice session to
            # anchor on, "Also:" covers ALL registered sessions, no exclusions.
            cue = ("All stopped." if vs == "stopped-all"
                   else "On hold." if vs == "quiet-hold"
                   else "Nothing playing.") + _also_clause(host)
            host._enqueue(ws, "prose", cue, False, mute_exempt=True, pause_exempt=True)
        else:
            host.speaker.earcon("error")
        return None
    # Capture the in-flight item BEFORE cancel so we can resume it afterwards.
    cur = host._current_item
    entry = host._pending_heard.get(cur.id) if cur is not None else None
    voice_folder = _numbered(host, fg)
    st = host._streams.get(fg)
    vs = host.voice_state
    if vs == "stopped-all":
        state = "all stopped"
    elif vs == "quiet-hold":
        state = "on hold"
    else:
        state = "stopped" if (st is not None and st.stopped) else "playing"
    # Keyboard clause ONLY when the workspace (keyboard) resolves to a session other
    # than the voice — otherwise there is nothing to disambiguate.
    ws = host.sessions.workspace()
    diverged = ws is not None and ws != fg
    kbd = (" Keyboard: {0}.".format(_numbered(host, ws))
           if diverged else "")
    # The Also-map excludes the voice session and, when diverged, the keyboard
    # session — both are already named by their own clauses (§7).
    exclude = (fg, ws) if diverged else (fg,)
    text = "Voice: {0}, {1}.{2}{3}".format(
        voice_folder, state, kbd, _also_clause(host, exclude))
    host.speaker.cancel()                          # barge-in: cut the current utterance
    # Resume-after-interjection: re-queue the interrupted item FIRST so it ends up
    # DEEPEST (the status cue is appendleft'd in front of it below).
    if cur is not None:
        host._enqueue(cur.session, cur.kind, cur.text, cur.is_decision,
                      entry=entry, mute_exempt=cur.mute_exempt,
                      pause_exempt=cur.pause_exempt, names_session=cur.names_session,
                      audio_path=cur.audio_path, at_front=True)
    host._enqueue(fg, "prose", text, False,
                  mute_exempt=True, pause_exempt=True, at_front=True)
    return None


@handler(MsgType.PING)
def on_ping(ctx, msg):
    return {"ok": True}
