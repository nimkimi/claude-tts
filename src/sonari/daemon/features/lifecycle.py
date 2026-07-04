from __future__ import annotations

from sonari.protocol import MsgType
from sonari.install_record import read_install_record
from sonari.daemon.registry import handler


def _launcher_present() -> bool:
    """Delegating shim — logic lives in the platform supervisor backend."""
    from sonari.platform import get_platform
    return get_platform().supervisor.is_installed()


def _setup_health(plugin_version: str):
    """Return (state, cue) where state is one of:
    "ok"            -> fully installed, no version drift   -> cue None
    "not_installed" -> no install.json or launcher (never ran `sonari install`)
    "version_drift" -> installed but plugin_version differs from this session's

    Cheap: a few file stats + a string compare. No launchctl. Never raises.
    The hotkeyd binary is deliberately NOT part of this check so a deliberate
    speech-only user (no swiftc) is never nagged.
    """
    rec = read_install_record()
    installed = (rec is not None and _launcher_present())
    if not installed:
        return ("not_installed",
                "Sonari is reading aloud. To enable hotkeys and autostart, "
                "run, slash sonari install.")
    recorded = (rec.get("plugin_version") or "")
    # Only flag drift when BOTH sides are known and differ.
    if plugin_version and recorded and plugin_version != recorded:
        return ("version_drift",
                "Sonari was updated. Run, slash sonari install, to apply.")
    return ("ok", None)


def _maybe_guide_setup(ctx, session: str, plugin_version: str) -> None:
    """Speak ONE setup-guidance cue for this session, only when degraded.

    Throttle: at most once per session (recorded whether or not a cue fires).
    Silent when healthy. The check is a few file stats + a version compare
    (no launchctl) and never raises.
    """
    if ctx.host._stream(session).guided:
        return
    try:
        state, cue = _setup_health(plugin_version or "")
    except Exception:  # noqa: BLE001 - guidance must never break a session
        return
    ctx.host._stream(session).guided = True
    if state != "ok" and cue:
        ctx.host._enqueue(session, "prose", cue, False)


@handler(MsgType.SET_FOREGROUND)
@handler(MsgType.SESSION_START)
def on_set_foreground(ctx, msg):
    t = msg.get("type")          # KEEP LOCAL — there is NO ctx.type
    session = ctx.session
    cwd = msg.get("cwd")
    # Policy A (R6 resolved): take the VOICE iff it is idle OR the submitter already
    # owns it (is the speaker); otherwise register only (ding + accrue as a jump/keep-
    # going target). Split voice-take from workspace-move: an auto-advanced speaker
    # self-submitting takes voice (already ours) but must NOT drift the workspace onto
    # an autonomous session (F2; R12/M3 — you'd answer the wrong session). All reads run
    # under self._lock (the handler path holds it), atomic with the speak loop's claim.
    voice_idle = not ctx.host._voice_busy_elsewhere(session)
    is_speaker = (session == ctx.host.sessions.speaker())
    if voice_idle or is_speaker:
        if is_speaker and ctx.host.sessions.speaker() != ctx.host.sessions.foreground():
            # keep-going-advanced speaker self-submitting: voice is ALREADY ours, so do
            # NOT move the workspace onto an auto-advanced session.
            ctx.host.sessions.register(session, cwd=cwd)
        else:
            # idle bootstrap, or speaker==foreground already aligned: take voice + workspace.
            # (Do NOT blanket-remove set_foreground — the single-session / focus-unknown
            # bootstrap needs _foreground set, or answer_permission has no target. The
            # idle-non-speaker move is pre-existing SP1 #65 residual, not SP2-new.)
            ctx.host.sessions.set_foreground(session, cwd=cwd)
    else:
        ctx.host.sessions.register(session, cwd=cwd)     # denied: ding + accrue
    # Identity (re)capture — piggybacked on ANY message carrying identity fields:
    # both SESSION_START and UserPromptSubmit's SET_FOREGROUND supply them, so a
    # post-restart session re-populates its tty on its next prompt (W4). Guard on
    # "field present", not message type. set_identity's don't-clobber-with-empties
    # rule keeps an intermittently-empty best-effort tty from destroying a good value,
    # so refreshing every prompt is safe. (register/set_foreground never touch
    # _identities, so this ordering is independent of the SESSION_START register below.)
    if msg.get("term_program") or msg.get("tty") or msg.get("iterm_session_id"):
        from sonari.sessions import Identity
        ctx.host.sessions.set_identity(session, Identity(
            term_program=msg.get("term_program", ""),
            tty=msg.get("tty", ""),
            iterm_session_id=msg.get("iterm_session_id", ""),
        ))
    if t == MsgType.SESSION_START:
        ctx.host.sessions.register(session, cwd=cwd)
        _maybe_guide_setup(ctx, session, msg.get("plugin_version", ""))
        if ctx.host._spearcons is not None:
            # Pre-render spearcons for the known roster in the background (Popen,
            # non-blocking); skips already-cached labels. Never on the hot path.
            ctx.host._spearcons.pregenerate(
                [ctx.host.sessions.folder(s) for s in ctx.host.sessions.session_ids()])
    return None


@handler(MsgType.SESSION_END)
def on_session_end(ctx, msg):
    session = ctx.session
    was_speaker = (session == ctx.host.sessions.speaker())
    ctx.host.sessions.unregister(session)
    # F1/M1: if the departing session WAS the muted speaker holding a quiet-hold, the
    # enum would otherwise stay "quiet-hold" with _speaker now None -> keep-going
    # permanently skipped (voice dead) and ⌃⌘W inverts into an error tone. Lift it.
    # stopped-all STAYS (the other sessions remain individually muted).
    if was_speaker and ctx.host.voice_state == "quiet-hold":
        ctx.host.voice_state = "flowing"
    st = ctx.host._streams.get(session)
    if st is not None:
        ctx.host._drop_pending(st.queue.clear())
    ctx.host.history.reset(session)
    ctx.host._streams.pop(session, None)
    return None
