from __future__ import annotations

from sonari.protocol import MsgType
from sonari.paths import INSTALL_RECORD_PATH
from sonari.daemon.registry import handler


def _read_install_record():
    """Return the install.json dict, or None if unreadable/absent. Never raises."""
    import json
    try:
        with open(str(INSTALL_RECORD_PATH), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001 - health check must never raise
        return None


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
    rec = _read_install_record()
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
    ctx.host.sessions.set_foreground(session, cwd=msg.get("cwd"))
    if t == MsgType.SESSION_START:
        ctx.host.sessions.register(session, cwd=msg.get("cwd"))
        from sonari.sessions import Identity
        ctx.host.sessions.set_identity(session, Identity(
            term_program=msg.get("term_program", ""),
            tty=msg.get("tty", ""),
            iterm_session_id=msg.get("iterm_session_id", ""),
        ))
        _maybe_guide_setup(ctx, session, msg.get("plugin_version", ""))
    return None


@handler(MsgType.SESSION_END)
def on_session_end(ctx, msg):
    session = ctx.session
    ctx.host.sessions.unregister(session)
    st = ctx.host._streams.get(session)
    if st is not None:
        ctx.host._drop_pending(st.queue.clear())
    ctx.host.history.reset(session)
    ctx.host._streams.pop(session, None)
    return None
