from __future__ import annotations

import sys
import threading

from sonari.protocol import MsgType, PROTOCOL_VERSION
from sonari.daemon.registry import handler
from sonari.catchup import render_slice, build_digest, sanitize_summary, resolve_summary_voice
from sonari.daemon.features.control import _has_decision, CLOSED_WORD
from sonari.daemon.features import teaching


def _result_msg(request_id, result):
    return {"v": PROTOCOL_VERSION, "type": MsgType.CATCHUP_RESULT,
            "request_id": request_id, "ok": result.is_ok,
            "text": result.text, "reason": result.reason}


def _cue_dest(host, target):
    """The stream this catch-up's cues must land in — resolved AND made audible.

    Route audible cues to the SPEAKER when it diverges from the caught-up target
    (the SP4 skip-cue lesson: a diverged target's stream isn't heard). Else target.

    D3 §4d seam (WB-C2): when the destination resolves to a DEAD session, claim
    the voice for it (host._sanction_dead_read). §4f rules catch-up on a closed
    session a legitimate recovery act that PROCEEDS — but post-T9 keep-going
    refuses dead streams, so with speaker() None the ack and the render landed
    in a stream nothing would ever adopt: the frontier work happened and the
    user heard nothing. The claim lives HERE, not at the three call sites, so
    every present and future cue destination in this file is audible by
    construction — the one-chokepoint discipline D3 is built on. All three
    callers are handlers under the dispatch transaction, which is the lock
    _sanction_dead_read requires.
    """
    sessions = host.sessions
    spk = sessions.speaker()
    dest = spk if (spk is not None and spk != target) else target
    host._sanction_dead_read(dest)
    return dest


@handler(MsgType.CATCH_UP)
def on_catch_up(ctx, msg):
    host = ctx.host
    sessions = host.sessions
    if host._catchup is not None:            # in flight -> pure cancel (§2.9)
        _cancel_catchup(host)
        return None
    target = sessions.workspace()
    if target is None:
        host.cue("error")
        return None
    st = host._stream(target)
    entries, aged_out = host.history.unheard_from_frontier(target, st.frontier)
    folder = sessions.folder(target)
    dest = _cue_dest(host, target)
    if not entries:
        host._enqueue(dest, "prose", "Nothing to catch up.", False,
                      mute_exempt=True, pause_exempt=True, at_front=True)
        return None
    n = len(entries)
    # No-folder fallback = "this session" (the target IS the workspace the user sits
    # at — never "another session"; matches render_slice's fallback). Owner ear-pass
    # veto string, like every other spoken string here.
    where = "in {0}".format(folder) if folder else "in this session"
    ack = "Catching up {0} {1} {2}.".format(n, "item" if n == 1 else "items", where)
    if aged_out:
        ack = "Earlier output aged out. " + ack
    # D3 spec §4f: reading a closed session's pile is a legitimate recovery act, so
    # catch-up still PROCEEDS on a dead target — only the ack gains the marker,
    # last so it always trails the aged-out prefix. `== "dead"` EXACTLY, never
    # `!= "live"`: pending is structurally unreachable here (workspace() cannot
    # resolve to a quarantined session — see the guard test), so this stays a
    # closed two-state branch and any future invariant break falls back to
    # today's unmarked ack rather than speaking a lie.
    if sessions.liveness(target) == "dead":
        ack += " " + CLOSED_WORD
    ack_id = host._enqueue(dest, "prose", ack, False,
                           mute_exempt=True, pause_exempt=True, at_front=True)
    last = entries[-1]
    slice_text = render_slice(entries, folder)      # pinned + rendered AT PRESS
    host._catchup_seq += 1
    request_id = host._catchup_seq
    cancel = threading.Event()
    # `ack_id` lets on_catchup_result land the render RIGHT AFTER the still-queued
    # ack (never ahead of it), so the ground-truth magnitude always speaks first.
    host._catchup = {"id": request_id, "target": target, "folder": folder,
                     "slice_end": (last.msg_id, last.seq),
                     "digest": build_digest(entries), "cancel": cancel,
                     "phase": "preparing", "render_id": None, "ended": False,
                     "ack_id": ack_id}
    summarizer = host._summarizer()
    if summarizer is None:                          # no adapter -> straight to the floor
        from sonari.summarizer import SummarizeResult
        host._catchup_inbox.put(_result_msg(request_id, SummarizeResult.failed("unavailable")))
        host._wake.set()
        return None

    def _run():                                     # worker: touches NO daemon state
        result = summarizer.summarize(slice_text, timeout_s=30.0, cancel=cancel)
        host._catchup_inbox.put(_result_msg(request_id, result))
        host._wake.set()
    threading.Thread(target=_run, daemon=True).start()
    return None


def _cancel_catchup(host):
    cu = host._catchup
    if cu is None:
        return
    cu["cancel"].set()                       # kill an in-flight child if still preparing
    rid = cu.get("render_id")
    if rid is not None:                      # already speaking: cut + drop the render
        dest = cu.get("dest")
        if dest is not None:
            host._drop_render_items(dest, rid)
        cur = host._current_item
        if cur is not None and getattr(cur, "render_id", None) == rid:
            host.speaker.cancel()
    host._catchup = None                     # no burn on cancel (§2.9)
    dest = _cue_dest(host, cu["target"])
    if dest is not None:
        host._enqueue(dest, "prose", "Cancelled.", False,
                      mute_exempt=True, pause_exempt=True, at_front=True)


@handler(MsgType.CATCHUP_RESULT)
def on_catchup_result(ctx, msg):
    host = ctx.host
    cu = host._catchup
    if cu is None or cu.get("id") != msg.get("request_id"):
        return None                                  # stale (cancelled/superseded) -> drop
    if not msg.get("ok"):
        # The spoken fallback is identical for every reason — the log is the only
        # place the WHY survives (the live PATH miss was invisible without it).
        try:
            print("sonari[catchup]: summary failed reason={0}".format(
                msg.get("reason") or "unknown"), file=sys.stderr)
        except Exception:  # noqa: BLE001 - never raise from diagnostic emit
            pass
    sessions = host.sessions
    target = cu["target"]
    ended = target not in sessions.session_ids()     # SESSION_END destroyed its live state
    cfg_voice = host.config.get("summary_voice")     # only 'auto' consults the voice list
    voices = host._installed_voices() if cfg_voice == "auto" else []
    body_voice = resolve_summary_voice(cfg_voice, host.config.get("voice"), voices)
    segments = []                                    # ordered (text, voice)
    if ended:
        folder = cu["folder"]
        segments.append(("{0} ended.".format(folder) if folder else "The session ended.", None))
    body = sanitize_summary(msg.get("text", "")) if msg.get("ok") else ""
    if body:
        segments.append(("Summary:", None))          # frame -> main voice
        segments.append((body, body_voice))          # body -> distinct voice
    else:
        segments.append((cu["digest"], None))        # digest replaces frame+body, main voice
    if not ended and _has_decision(host, target):
        segments.append(("Decision waiting.", None))
    render_id = cu["id"]
    cu["render_id"] = render_id
    cu["phase"] = "rendering"
    cu["ended"] = ended
    dest = _cue_dest(host, target)
    if dest is None:
        host._catchup = None                         # nowhere audible (last session gone)
        return None
    cu["dest"] = dest                                # the stream the render items live on (for cancel/cut)
    if dest != target and not ended and cu["folder"]:
        # The speaker diverged from the caught-up target mid-prep: the render
        # plays on dest's stream, where mute_exempt suppresses the standard
        # folder prefix — carry the attribution inline on the first segment
        # (the ended case already names the folder in its own first segment).
        first_text, first_voice = segments[0]
        segments[0] = ("{0}. {1}".format(cu["folder"], first_text), first_voice)
    last = len(segments) - 1
    ack_id = cu.get("ack_id")                        # land the render AFTER the still-queued ack
    for i in range(last, -1, -1):                    # reverse -> preserved play order (after the ack, else at_front)
        text, voice = segments[i]
        # The last item is the render-DONE marker (always) — it clears self._catchup
        # on completion; whether it also BURNS is gated on `not ended` in Task 8, so
        # an ended render still clears the bundle (no spurious "Cancelled." next press).
        host._enqueue(dest, "prose", text, False, mute_exempt=True, pause_exempt=True,
                      at_front=True, voice=voice, render_id=render_id,
                      catchup_burn=(i == last), after_id=ack_id)
    if body:                                          # a real summary, not the digest fallback
        teaching.maybe_hint(host, "catch_up_done", dest)
    return None
