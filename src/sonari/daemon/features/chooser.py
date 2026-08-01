"""The session chooser (⌃⌘Tab held — spec 2026-07-14 §3).

Browse spoken previews that move NOTHING (no voice change, no workspace change,
no raise), then commit ONCE on chord-release or a digit. Replaces the old
CYCLE_SESSION ring: browsing state lives HERE, advanced only by the gesture's
own messages, so it cannot pin on OS-focus failures by construction — there is
no anchor recomputation between taps and no raise until the single commit.

All handlers run inside the daemon's _state.transaction() (the one lock), so
every snapshot/preview/commit is atomic with the speak loop's pop+claim (M1).
"""
from __future__ import annotations

import time

from sonari.protocol import MsgType
from sonari.daemon.registry import handler
from sonari.daemon.features import teaching

# Injectable clock: tests monkeypatch chooser._now to drive the stale window.
_now = time.monotonic

# An open older than this is STALE — hotkeyd died mid-gesture (its own 30 s cap
# normally sends CHOOSER_CANCEL first). The next CHOOSER_* message implicitly
# cancels (restores the capture) and starts fresh (spec §3 Cancel).
STALE_S = 30.0


class ChooserState:
    """One open chooser gesture (chord held). Lives on host._chooser."""

    def __init__(self, origin, candidates, opened_at, captured, captured_entry):
        self.origin = origin            # session current at open: the no-op commit target
        self.candidates = candidates    # snapshot: [origin?] + MRU + never-visited (is_live)
        self.index = 0                  # cursor (0 == origin when origin is live)
        self.opened_at = opened_at      # _now() at open (stale detection)
        self.captured = captured        # the in-flight SpeechItem cut at open, or None
        self.captured_entry = captured_entry   # its pending-heard entry, or None
        self.preview_id = None          # the queued preview item's id (swapped each step)
        self.preview_session = None     # which stream holds that preview


def _snapshot(sessions):
    """(origin, candidates) at open. Order: the current session (workspace() —
    it already falls back to foreground), then MRU most-recent first, then
    never-visited sessions in registration order. Filter: is_live() ONLY —
    identical to the old ring's W1 + sp3.2 eviction semantics; muted sessions
    stay browsable (Fork 2)."""
    origin = sessions.workspace()
    out = []
    if origin is not None and sessions.is_live(origin):
        out.append(origin)
    for s in sessions.mru():
        if s != origin and s not in out and sessions.is_live(s):
            out.append(s)
    for s in sessions.session_ids():
        if s != origin and s not in out and sessions.is_live(s):
            out.append(s)
    return origin, out


def _open(host):
    """Open the chooser: snapshot + capture-and-cut the in-flight item (the ⌃⌘W
    pattern, control.py:184-222 — requeued on cancel / no-op commit). Returns the
    new state, or None (error-toned) when no live candidate exists."""
    origin, candidates = _snapshot(host.sessions)
    if not candidates:
        host.cue("error")
        return None
    cur = host._current_item
    entry = host._pending_heard.get(cur.id) if cur is not None else None
    if cur is not None:
        host.speaker.cancel()      # cut NOW so a later restore is a true resume
        # The claim transfers to st.captured (parked, not in-flight): clear it
        # here rather than waiting for note_spoken, so a second open before the
        # speak thread catches up (e.g. the stale-reopen path) never recaptures
        # the same item twice.
        host._current_item = None
    host._chooser = ChooserState(origin, candidates, _now(), cur, entry)
    return host._chooser


def _state_or_none(host):
    """The live open state, after stale handling: a >STALE_S leftover is
    implicitly cancelled (captured item restored) and reported as None."""
    st = host._chooser
    if st is None:
        return None
    if _now() - st.opened_at > STALE_S:
        _restore_and_clear(host)
        return None
    return st


def _remove_preview(host, st):
    """Swap out the previous preview: drop it from its queue if still waiting,
    cut it if it is the utterance in flight (it is chooser UI, never content)."""
    if st.preview_id is None:
        return
    stream = host._streams.get(st.preview_session)
    if stream is not None:
        stream.queue.remove_by_id(st.preview_id)
    cur = host._current_item
    if cur is not None and cur.id == st.preview_id:
        host.speaker.cancel()
    st.preview_id = None
    st.preview_session = None


def _restore_and_clear(host):
    """The cancel path: remove any pending preview, requeue the captured item at
    the front of its own stream (resume), move nothing, say nothing."""
    st = host._chooser
    if st is None:
        return
    _remove_preview(host, st)
    if st.captured is not None:
        c = st.captured
        host._enqueue(c.session, c.kind, c.text, c.is_decision,
                      entry=st.captured_entry, mute_exempt=c.mute_exempt,
                      pause_exempt=c.pause_exempt, names_session=c.names_session,
                      audio_path=c.audio_path, forward=c.forward, at_front=True,
                      prelude=c.prelude)
    host._chooser = None


def _preview_text(host, st):
    """'{number}, {folder}[, muted][, current].' — '{number}, another session'
    when the folder is unknown. Plain speech in v1 (no spearcon — plan D3).

    A candidate that died mid-browse (unregistered after the OPEN snapshot,
    branch-review MINOR A fix) has no number() anymore — speak the
    folder-fallback WITHOUT a number prefix rather than the literal 'None'."""
    target = st.candidates[st.index]
    sessions = host.sessions
    folder = sessions.folder(target)
    number = sessions.number(target)
    label = folder if folder else "another session"
    text = "{0}, {1}".format(number, label) if number is not None else label
    stream = host._streams.get(target)
    if stream is not None and stream.stopped:
        text += ", muted"
    if target == st.origin:
        text += ", current"
    return text + "."


def _deliver_preview(host, st):
    """Speak one preview exactly like a ⌃⌘W cue: barge-in the previous utterance,
    enqueue to the SPEAKER's stream (or the playable-workspace fallback when the
    speaker is None — mirroring on_where_am_i's None branch, control.py:158-183)
    with mute_exempt + pause_exempt + at_front. Moves NOTHING."""
    _remove_preview(host, st)
    host.speaker.cancel()
    tgt = host.sessions.speaker()
    if tgt is None:
        ws = host.sessions.workspace()
        ws_st = host._streams.get(ws) if ws is not None else None
        playable = ws is not None and not (ws_st is not None and ws_st.stopped)
        if not playable:
            host.cue("error")   # nowhere voiceable; browse stays open
            return
        tgt = ws
    host._enqueue(tgt, "prose", _preview_text(host, st), False,
                  mute_exempt=True, pause_exempt=True, at_front=True)
    st.preview_id = host._next_id          # the id _enqueue just allocated
    st.preview_session = tgt


def _commit(host, st, target):
    """Land. target == origin: the silent no-op (no cut, no cue, capture resumes).
    Otherwise: EXACTLY the ratified cycle-landing semantics, copied from the old
    on_cycle_session (focus.py:137-159 at 3430cbf) — focus(), flowing, cut,
    muted-landing keep-go release, names_session cue (spearcon-capable), raise.

    Guard (branch-review fix): the snapshot is is_live-filtered ONLY at OPEN, so
    a candidate can die WHILE it is being browsed, before this commit fires.
    Landing there must never reach sessions.focus() — its _record() would
    silently RE-REGISTER a dead session id (a phantom in the roster, the
    workspace pinned to a closed terminal). Both death shapes are checked:
    SESSION_END unregisters (out of session_ids(), but is_live() fail-opens on
    the now-missing identity); a dead tty stays registered (is_live() catches
    it via the captured-tty check) — neither check alone covers both."""
    if target == st.origin:
        _restore_and_clear(host)
        return
    sessions = host.sessions
    if target not in sessions.session_ids() or not sessions.is_live(target):
        host.cue("error")   # audible failed landing (eyes-free), never silent
        _restore_and_clear(host)       # resume the captured item, move nothing
        return None
    _remove_preview(host, st)
    st.captured = None                     # cycle-cut parity: no resume on a real landing
    host._chooser = None
    sessions.focus(target)                 # workspace + voice -> target (R12: the one writer)
    host.speaker.cancel()
    host.voice_state = "flowing"           # a commit is a deliberate re-engage
    if host._stream(target).stopped:
        # Commit-onto-muted (Fork 2, ratified): keep the WORKSPACE on the muted
        # target, RELEASE the voice so keep-going moves it to an ACTIVE session.
        # Do NOT un-mute the target (R7 — it stays muted until its own ⌃⌘S-start).
        sessions.set_speaker(None)
    folder = sessions.folder(target)
    identity = sessions.identity(target)
    will_raise = host._raise().will_attempt(identity)
    # Bump on EVERY commit, raising or not, so a prior in-flight raise sees
    # itself superseded (same reasoning as on_jump_waiting, focus.py:84-90).
    gen = host._raise().bump_generation()
    cue = folder + "." if folder else "Another session."
    host._enqueue(target, "prose", cue, False,
                  audio_path=host._spearcon_path(folder),
                  mute_exempt=True, at_front=True, names_session=True)
    if will_raise:
        host._raise().raise_async(
            identity, gen,
            on_failure=lambda s=target, f=folder: host._raise_failed(s, f))


@handler(MsgType.CHOOSER_STEP)
def on_chooser_step(ctx, msg):
    host = ctx.host
    st = _state_or_none(host)
    opened = st is None
    if st is None:
        st = _open(host)
        if st is None:
            return None                    # no live candidates: error toned
    step = -1 if msg.get("direction", "next") == "prev" else 1
    # Open-on-first-step: index starts at 0 (current), so the opening step lands
    # on index 1 — a quick tap-and-release IS the previous-session toggle.
    st.index = (st.index + step) % len(st.candidates)
    _deliver_preview(host, st)
    if opened:
        teaching.maybe_hint(host, "chooser", st.preview_session)
    return None


@handler(MsgType.CHOOSER_DIGIT)
def on_chooser_digit(ctx, msg):
    host = ctx.host
    st = _state_or_none(host)
    if st is None:
        # No open gesture: hotkeyd only ever registers ⌃⌘1-9 WHILE the chord is
        # held (spec §5), so a digit with nothing open is a race/stray message
        # (its own hotkeyd socket, arriving after CHOOSER_COMMIT already landed
        # on the modifier-release socket) -- never a fresh gesture to honor.
        # Opening here would teleport the workspace on a message the user never
        # intended (branch-review fix). No-op: no earcon, no state, no speech.
        return None
    try:
        digit = int(msg.get("digit"))
    except (TypeError, ValueError):
        digit = None
    target = host.sessions.session_for_number(digit) if digit is not None else None
    if target is None or not host.sessions.is_live(target):
        host.cue("error")       # unknown/dead number: browse stays open (§3)
        return None
    _commit(host, st, target)
    return None


@handler(MsgType.CHOOSER_COMMIT)
def on_chooser_commit(ctx, msg):
    host = ctx.host
    st = _state_or_none(host)
    if st is None:
        return None                        # release with no open gesture
    _commit(host, st, st.candidates[st.index])
    return None


@handler(MsgType.CHOOSER_CANCEL)
def on_chooser_cancel(ctx, msg):
    st = _state_or_none(ctx.host)          # stale state restores here too
    if st is not None:
        _restore_and_clear(ctx.host)
    return None
