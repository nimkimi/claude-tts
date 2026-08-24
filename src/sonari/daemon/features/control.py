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


# Grammar v2 (spec 2026-07-16-sonari-whereami-grammar-v2): age is spoken as
# the WORD "stale", never a number — one bit, zero digits. Owner-tunable.
_STALE_AFTER_S = 900.0

_COUNT_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
                6: "six", 7: "seven", 8: "eight", 9: "nine"}

# PROVISIONAL (ear-batch-3): spec D3 §5 slot 3 — ONE word, four sites
# (chooser commit/digit, jump commit, jump-decision crossed) + the
# catch-up ack marker. Rides the existing `error` tone via the D7a
# word channel.
CLOSED_WORD = "That session closed."

# D3 §4a/§5 slots 1-2: the marker clause a non-live session's Also-map entry
# leads with, reused by _pointer_mark for both pointer clauses. One word per
# tier, fleet-wide vocabulary. PROVISIONAL (ear-batch-3) — the owner's ear
# may flip mark->hide. This map + its two lookups (the Also-map's own
# composition and _pointer_mark) is only HALF that revert: the
# "That session closed." word (chooser.py's commit/digit sites, focus.py's
# jump-waiting commit, playback.py's jump-decision crossed path, catchup.py's
# ack marker) and focus.py's _empty_case_tail count-tail are separate marked
# clauses that would need to come out in the same pass. See spec.md §4 for
# the complete per-surface clause list.
_LIVENESS_MARKS = {"pending": ", pending", "dead": ", closed"}


def _pointer_mark(host, session):
    # PROVISIONAL (ear-batch-3): spec D3 §4a/F-D3-1 — the pointer clauses'
    # tier marker, over the SAME _LIVENESS_MARKS lookup the Also-map half
    # already uses (whole-branch review: generalized from a dead-only check).
    # The Keyboard/idle pointer (workspace()) can never resolve to "pending"
    # (recon §6, T10's guard test: test_catchup_press.py::
    # test_workspace_never_resolves_to_a_pending_session), so that leg stays a
    # structural no-op. The VOICE pointer (speaker()) is not pending-proof —
    # keep-going's adoption can set it on a still-quarantined restored session
    # (host.py's _select_keep_going) — so "playing, pending." is reachable.
    return _LIVENESS_MARKS.get(host.sessions.liveness(session), "")


def _has_decision(host, session):
    """The ⌃⌘D hit predicate verbatim (W4): a queued decision item OR a live
    pending blocking decision — an answerable-but-already-narrated ask has an
    empty queue, and the readout must not hide exactly the asks that matter."""
    st = host._streams.get(session)
    if st is not None and st.queue.has_decision():
        return True
    return session in host._pending_decisions


def _entry_clauses(host, session):
    """The comma clauses for one session, fixed order (grammar v2): decision,
    muted, waiting, unheard, stale. Fixed clause order is itself a role cue —
    position tells the ear what a number means before the noun lands. Returns
    '' when the session has nothing to report."""
    st = host._streams.get(session)
    seg = ""
    if _has_decision(host, session):
        # Inline role WORD, never a standalone "Decision:" landmark — a
        # sentence-initial frame that clips at rate silently demotes the
        # highest-stakes fact to a routine pile entry (design attack finding).
        seg += ", decision"
    if st is not None and st.stopped:
        seg += ", muted"
    k = len(st.queue) if st is not None else 0
    if k > 0:
        seg += ", {0} waiting".format(k)
    # W10 → SP5: the unheard count now decomposes the SAME transcript pile that
    # skip and catch-up announce whole (spec §8), not the current-turn floor.
    # unheard_from_frontier is frontier-keyed + heard-flag-independent; subtract
    # k (the queued items, whose history entries are also still unheard) so the
    # split stays imminent-vs-backlog and never double-counts. Floored at 0.
    # (`stale` below still reads the current-turn unheard_age — spec §8 scopes
    # only u; the age word is a separate approximation, unchanged this wave.)
    frontier = st.frontier if st is not None else None
    pile, _ = host.history.unheard_from_frontier(session, frontier)
    u = max(0, len(pile) - k)
    if u > 0:
        seg += ", {0} unheard".format(u)
        age = host.history.unheard_age(session)
        if age is not None and age > _STALE_AFTER_S:
            seg += ", stale"
    return seg


def _also_clause(host, exclude=()):
    """The holistic ⌃⌘W 'Also:' map, grammar v2: every registered session NOT
    in *exclude*; each entry keeps the digit-first shape '{n} {folder}{clauses}'
    (digit·name·count·noun — two numbers never abut, the anti-blur invariant)
    but entries are now SENTENCES joined '. ' (periods are the only pause that
    survives 225-400 wpm), ordered by VALUE tier — decision entries, then
    piles, then muted-only — number-ascending within each tier, so barge-in
    always cuts on the most actionable prefix. Sessions with nothing to report
    collapse into a digit-free terminal 'Plus {word} quiet.'; when everything
    else is quiet the map is the positive 'All quiet.'; zero other sessions
    keeps the trained absent landmark.

    D3 §4a — the map is the fleet's truth-teller, so it consults liveness() per
    session and MARKS the two non-live tiers rather than hiding or mis-narrating
    them (the old `is_provisional` exclusion made a restored pile invisible and
    let a dead-tty session speak exactly like a live one). A pending or dead
    session WITH content clauses is named as usual, its entry leading with the
    tier's marker clause ('1 repo, pending, 2 unheard.'). A clause-less pending
    session is never named — naming a phantom you cannot act on is noise — and
    collapses into its own terminal 'Two pending.' after the quiet tail. A
    clause-less dead session is dropped entirely: nothing to act on, nothing to
    recover."""
    sessions = host.sessions
    ids = sorted((s for s in sessions.session_ids() if s not in exclude),
                 key=lambda s: sessions.number(s) or 0)
    decisions, piles, muted_only = [], [], []
    quiet = 0
    pending_quiet = 0
    for s in ids:
        lv = sessions.liveness(s)
        # PURE content clauses: the tier keys on these and the pointer clauses
        # reuse them, so the marker is composed here, never in _entry_clauses.
        clauses = _entry_clauses(host, s)
        if not clauses:
            if lv == "pending":
                pending_quiet += 1
            elif lv == "live":
                quiet += 1
            continue
        entry = "{0} {1}{2}{3}".format(sessions.number(s),
                                       sessions.folder(s) or "another session",
                                       _LIVENESS_MARKS.get(lv, ""),
                                       clauses)
        if clauses.startswith(", decision"):
            decisions.append(entry)
        elif "waiting" in clauses or "unheard" in clauses:
            piles.append(entry)
        else:
            muted_only.append(entry)
    parts = decisions + piles + muted_only
    # PROVISIONAL (ear-batch-3), D3 §5 slot 1: the clause-less pending count,
    # a separate terminal sentence so it survives a barge-in cut of the map.
    pending_tail = (" {0} pending.".format(
        _COUNT_WORDS.get(pending_quiet, "many").capitalize())
        if pending_quiet else "")
    if not parts:
        return (" All quiet." + pending_tail) if quiet else pending_tail
    out = " Also: {0}.".format(". ".join(parts))
    if quiet:
        # Digit-free ALWAYS: above the word map the count degrades to "many"
        # rather than reviving a spoken digit (the blur this grammar kills).
        # Precision is worthless up there — "many quiet" is the whole signal.
        out += " Plus {0} quiet.".format(_COUNT_WORDS.get(quiet, "many"))
    return out + pending_tail


def _readback(host, text, **kw):
    """Voice a settings confirmation on the WORKSPACE — W11: the terminal you're
    at hears its own confirmation, not the session you may not be listening to.

    One helper for all three readbacks (rate delta, set-verbosity, cycle-
    verbosity), which had three copies of this shape: this file's own drift is
    exactly the disease D3 is about. RR-2 seam — post-T9 keep-going refuses a
    dead stream, so with the voice idle these were composed into a stream nothing
    would ever adopt. The SINGLE-ITEM sanction claims the idle voice for exactly
    this cue; at_front so the delivered item is the answer rather than whatever
    the closed session's pile starts with (these are back-appends, unlike the
    barge-in-class cues elsewhere). Live workspaces keep today's path
    byte-for-byte — the sanction returns False and at_front stays off.
    """
    ws = host.sessions.workspace()
    if ws is None:
        return
    host._enqueue(ws, "prose", text, False,
                  at_front=host._sanction_dead_read(ws, whole=False), **kw)


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
        _readback(ctx.host, "Rate {0}.".format(rate))
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
    # dead CYCLE_VERBOSITY handler, 0 senders). mute_exempt+pause_exempt so a
    # settings readback can never be silently swallowed while the voice is held
    # — "Verbosity quiet." IS the last thing you hear (direct _enqueue cues
    # bypass the on_prose quiet gate). Idempotent by design: setting the same
    # value re-confirms (readback).
    _readback(ctx.host, "Verbosity {0}.".format(v),
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


@handler(MsgType.SET_KEEPALIVE)
def on_set_keepalive(ctx, msg):
    # SINGLE-WRITER DISCIPLINE (plan amendment, 2026-08-24, binding): this
    # handler runs under the daemon lock. It must NEVER call
    # ctx.host.keepalive directly — KeepAliveManager.set_enabled(False) reaps
    # on the CALLING thread, and a handler-side reap would stall every socket
    # message, hotkey and speak-loop claim behind the daemon lock on a hung
    # child. Config mutation + persistence ONLY; the lock-free speak-loop
    # site (`_keepalive_recheck`'s `if reap:` branch) is the ONLY writer that
    # applies this value to the manager.
    v = msg.get("enabled")
    if not isinstance(v, bool):
        return None
    ctx.host.config["keepalive_enabled"] = v
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
    # Aligned with the live SET_VERBOSITY confirmation (on_set_verbosity, W3):
    # mute_exempt+pause_exempt so the readback can never be silently swallowed
    # while the voice is held.
    _readback(ctx.host, "Verbosity {0}.".format(nxt),
              mute_exempt=True, pause_exempt=True)
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
        # §7 witness: age of the last hotkeyd WITNESS_PING; None before the
        # first (the daemon-side alarm arms only after a first ping).
        "witness_ping_age_s": (
            time.monotonic() - host._witness_last_ping
            if host._witness_last_ping is not None else None
        ),
    }


@handler(MsgType.WHERE_AM_I)
def on_where_am_i(ctx, msg):
    # ⌃⌘W "where am I": ONE holistic SPOKEN readout (§7, amended 2026-07-14;
    # grammar v2 2026-07-16 — spec 2026-07-16-sonari-whereami-grammar-v2),
    # barge-in + interjection-resume unchanged. Plain speech end-to-end:
    # unified "Voice and keyboard: {folder} {n}, {state}[, decision]." —
    # unity stated POSITIVELY, never by an absent clause; diverged
    # "Voice: … . Keyboard: {folder} {m}{clauses}." (the Keyboard clause
    # carries the workspace's own pile); then the Also-map's sentence
    # entries in value-tier order (see _also_clause).
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
            # State-cue lead + a POSITIVE keyboard location (grammar v2 — the
            # owner rejected absence-as-signal): the idle Keyboard clause
            # behaves exactly like the diverged one — it carries the
            # workspace's own clauses and the workspace leaves the map (a
            # session is never named twice). Everything else stays in the map.
            cue = ("All stopped." if vs == "stopped-all"
                   else "On hold." if vs == "quiet-hold"
                   else "Nothing playing.")
            cue += " Keyboard: {0}{1}{2}.".format(
                _numbered(host, ws), _pointer_mark(host, ws), _entry_clauses(host, ws))
            cue += _also_clause(host, exclude=(ws,))
            host._enqueue(ws, "prose", cue, False, mute_exempt=True, pause_exempt=True)
            # D3 §4d seam (WB-C1): the delivery note above predates T9. Keep-going
            # now SKIPS dead streams, so on a dead workspace the cue it just
            # composed — correctly marked ", closed" — would sit here forever:
            # no speech, no tone, from the primary status verb. This press is
            # deliberate, so it sanctions reading that stream (and takes the idle
            # voice itself, which is what "adoption on the next loop turn" above
            # relied on keep-going to do).
            host._sanction_dead_read(ws)
        else:
            host.cue("error")
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
    # Grammar v2: unity is stated POSITIVELY — the owner rejected the old
    # absence-as-signal Keyboard clause ("if they are at the same session say
    # so"). Unified merges both trained landmarks into one clause; diverged
    # keeps the split, and the Keyboard clause carries its OWN pile clauses —
    # a silent backlog sitting exactly where he types is the one pile worth
    # pointer-line space. A blocking decision on a pointer session rides its
    # clause inline (the session is never repeated in the Also-map).
    ws = host.sessions.workspace()
    diverged = ws is not None and ws != fg
    voice_dec = ", decision" if _has_decision(host, fg) else ""
    if diverged:
        # D3 §4a: each pointer gets its OWN mark — voice and keyboard can die
        # independently, and (F-D3-1) the Voice pointer can be pending too.
        # The Keyboard clause's pending tier stays unmarked in practice:
        # workspace() cannot resolve to a provisional session (recon §6,
        # T10's guard test), so that leg of the shared helper is a
        # structural no-op — only Voice below can ever show ", pending".
        lead = "Voice: {0}, {1}{2}{3}. Keyboard: {4}{5}{6}.".format(
            voice_folder, state, _pointer_mark(host, fg), voice_dec,
            _numbered(host, ws), _pointer_mark(host, ws), _entry_clauses(host, ws))
    else:
        lead = "Voice and keyboard: {0}, {1}{2}{3}.".format(
            voice_folder, state, _pointer_mark(host, fg), voice_dec)
    # The Also-map excludes the voice session and, when diverged, the keyboard
    # session — both are already named by their own clauses (§7).
    exclude = (fg, ws) if diverged else (fg,)
    text = lead + _also_clause(host, exclude)
    host.speaker.cancel()                          # barge-in: cut the current utterance
    # Resume-after-interjection: re-queue the interrupted item FIRST so it ends up
    # DEEPEST (the status cue is appendleft'd in front of it below). A catch-up
    # render item (render_id set) is NEVER re-queued — a cut render is gone by
    # design (note_spoken dropped its siblings + cleared _catchup); the pile stays
    # unburned and the next press re-summarizes (§2.8).
    if cur is not None and getattr(cur, "render_id", None) is None:
        host._enqueue(cur.session, cur.kind, cur.text, cur.is_decision,
                      entry=entry, mute_exempt=cur.mute_exempt,
                      pause_exempt=cur.pause_exempt, names_session=cur.names_session,
                      audio_path=cur.audio_path, forward=cur.forward, at_front=True,
                      prelude=cur.prelude)
    host._enqueue(fg, "prose", text, False,
                  mute_exempt=True, pause_exempt=True, at_front=True)
    # Same deliberate-press rule as the idle branch above, for the case where the
    # VOICE session is the dead one: this readout (and the item barge-in just
    # re-queued) live in its stream, which the speak loop would otherwise hand
    # back rather than drain (§4d). ⌃⌘W is exactly when the user is asking about
    # a fleet in this state — answering "playing, closed" out loud is the point.
    host._sanction_dead_read(fg)
    return None


@handler(MsgType.PING)
def on_ping(ctx, msg):
    return {"ok": True}


@handler(MsgType.WITNESS_PING)
def on_witness_ping(ctx, msg):
    # §7 witness (hotkeyd-death direction): stamp the age the speak-loop tick
    # checks; a ping is also the re-arm signal after a fired alarm.
    ctx.host._witness_last_ping = time.monotonic()
    ctx.host._witness_alarmed = False
    return {"ok": True}


@handler(MsgType.ANNOUNCE)
def on_announce(ctx, msg):
    """Speak one CLI-originated sentence and ACK whether it landed.

    The daemon only speaks into session streams, so a sentence with no session
    needs a playable one chosen for it — the same problem where-am-I solves for
    its own readout when speaker() is None. Ack matters: an unacked send cannot
    be distinguished from a dropped one, and the CLI's direct fallback depends
    on knowing the difference.
    """
    host = ctx.host
    text = (msg.get("text") or "").strip()
    if not text:
        return {"ok": False}
    target = host.sessions.workspace()
    if target is None:
        target = host.sessions.speaker()
    if target is None:
        return {"ok": False}
    st = host._streams.get(target)
    if st is not None and st.stopped:
        return {"ok": False}
    # SINGLE-ITEM grain, at_front — the _readback() idiom (control.py:173-191),
    # not where-am-I's. ⌃⌘W is a read OF its session, so it earns whole=True;
    # an announcement merely LANDS on whatever workspace()/speaker() resolves
    # to, which _sanction_dead_read's own docstring names as the whole=False
    # category. The two halves are ATOMIC: whole=False is spent on the first
    # pop, so without at_front the sanction would be consumed by a stale
    # backlog item and the verdict itself would be stranded unspoken — after
    # {"ok": True} was already returned. On a live target both are no-ops.
    host._enqueue(target, "prose", text, False,
                  mute_exempt=True, pause_exempt=True,
                  at_front=host._sanction_dead_read(target, whole=False))
    return {"ok": True}
