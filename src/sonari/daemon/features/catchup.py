from __future__ import annotations

import threading

from sonari.protocol import MsgType, PROTOCOL_VERSION
from sonari.daemon.registry import handler
from sonari.catchup import render_slice, build_digest, sanitize_summary, resolve_summary_voice
from sonari.daemon.features.control import _has_decision


def _result_msg(request_id, result):
    return {"v": PROTOCOL_VERSION, "type": MsgType.CATCHUP_RESULT,
            "request_id": request_id, "ok": result.is_ok,
            "text": result.text, "reason": result.reason}


def _cue_dest(sessions, target):
    # Route audible cues to the SPEAKER when it diverges from the caught-up target
    # (the SP4 skip-cue lesson: a diverged target's stream isn't heard). Else target.
    spk = sessions.speaker()
    return spk if (spk is not None and spk != target) else target


@handler(MsgType.CATCH_UP)
def on_catch_up(ctx, msg):
    host = ctx.host
    sessions = host.sessions
    if host._catchup is not None:            # in flight -> pure cancel (§2.9)
        _cancel_catchup(host)
        return None
    target = sessions.workspace()
    if target is None:
        host.speaker.earcon("error")
        return None
    st = host._stream(target)
    entries, aged_out = host.history.unheard_from_frontier(target, st.frontier)
    folder = sessions.folder(target)
    dest = _cue_dest(sessions, target)
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
    dest = _cue_dest(host.sessions, cu["target"])
    if dest is not None:
        host._enqueue(dest, "prose", "Cancelled.", False,
                      mute_exempt=True, pause_exempt=True, at_front=True)


@handler(MsgType.CATCHUP_RESULT)
def on_catchup_result(ctx, msg):
    host = ctx.host
    cu = host._catchup
    if cu is None or cu.get("id") != msg.get("request_id"):
        return None                                  # stale (cancelled/superseded) -> drop
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
    dest = _cue_dest(sessions, target)
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
    return None
