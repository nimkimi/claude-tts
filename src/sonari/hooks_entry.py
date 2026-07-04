"""Pure mapping from Claude Code hook events to protocol message dicts."""
from __future__ import annotations

import json
import os

from sonari import ttyutil
from sonari.protocol import PROTOCOL_VERSION, MsgType


def _msg(**fields):
    """Build a protocol message dict, always stamped with the protocol version."""
    out = {"v": PROTOCOL_VERSION}
    out.update(fields)
    return out


def _tool_summary(tool: str, ti: dict) -> str:
    """Short, speakable, tool-specific description of a pending tool call."""
    if tool == "Bash":
        cmd = (ti.get("command") or "").strip()
        return cmd[:120] if cmd else "Bash"
    if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        path = ti.get("file_path") or ti.get("notebook_path") or ""
        base = os.path.basename(path.rstrip("/")) if path else ""
        return base if base else (tool or "")
    return tool or ""


def handle_event(event: str, payload: dict) -> list[dict]:
    """Map (event name, parsed stdin payload) to a list of protocol messages.

    PURE except a best-effort tty probe in SessionStart. Returns [] for any event it does not handle.
    """
    session = payload.get("session_id", "")

    if event == "MessageDisplay":
        return [
            _msg(
                type=MsgType.PROSE,
                session=session,
                delta=payload.get("delta", ""),
                index=payload.get("index", 0),
                final=payload.get("final", False),
            )
        ]

    if event == "PreToolUse":
        tool = payload.get("tool_name")
        ti = payload.get("tool_input", {})
        if tool == "AskUserQuestion":
            return [
                _msg(type=MsgType.EARCON, kind="choice"),
                _msg(
                    type=MsgType.CHOICE,
                    session=session,
                    questions=ti.get("questions", []),
                ),
            ]
        if tool == "ExitPlanMode":
            return [
                _msg(type=MsgType.EARCON, kind="plan"),
                _msg(type=MsgType.PLAN, session=session, text=ti.get("plan", "")),
            ]
        return [
            _msg(
                type=MsgType.TOOL,
                session=session,
                tool=tool,
                summary=_tool_summary(tool, ti),
            )
        ]

    if event == "Notification":
        nt = payload.get("notification_type") or payload.get("matcher")
        if nt == "permission_prompt":
            return [
                _msg(type=MsgType.EARCON, kind="permission"),
                _msg(
                    type=MsgType.PERMISSION,
                    session=session,
                    action=payload.get("action", ""),
                    message=payload.get("message", ""),
                ),
            ]
        return []

    if event == "Stop":
        # session is carried so the daemon can close this session's open prose
        # message at the turn boundary (releases the held voice — H1).
        return [_msg(type=MsgType.EARCON, kind="turn_done", session=session)]

    if event == "UserPromptSubmit":
        return [
            _msg(type=MsgType.SET_FOREGROUND, session=session,
                 cwd=payload.get("cwd", ""),
                 term_program=os.environ.get("TERM_PROGRAM", ""),
                 iterm_session_id=os.environ.get("ITERM_SESSION_ID", ""),
                 tty=ttyutil.controlling_tty()),
            _msg(type=MsgType.FLUSH, session=session),
        ]

    if event == "SessionStart":
        return [
            _msg(type=MsgType.SET_FOREGROUND, session=session,
                 cwd=payload.get("cwd", "")),
            _msg(
                type=MsgType.SESSION_START,
                session=session,
                cwd=payload.get("cwd", ""),
                plugin_version=os.environ.get("CLAUDE_PLUGIN_VERSION", ""),
                plugin_root=os.environ.get("CLAUDE_PLUGIN_ROOT", ""),
                # Terminal identity for OS keyboard-focus-follow (best-effort; the
                # daemon runs under launchd and cannot derive these itself).
                term_program=os.environ.get("TERM_PROGRAM", ""),
                iterm_session_id=os.environ.get("ITERM_SESSION_ID", ""),
                tty=ttyutil.controlling_tty(),
            ),
        ]

    if event == "SessionEnd":
        return [_msg(type=MsgType.SESSION_END, session=session)]

    if event == "PermissionRequest":
        tool = payload.get("tool_name")
        ti = payload.get("tool_input", {})
        return [
            _msg(type=MsgType.PERMISSION_REQUEST, session=session,
                 tool=tool, summary=_tool_summary(tool, ti)),
        ]

    return []


def permission_decision_stdout(reply) -> "str | None":
    """Render the PermissionRequest hook's stdout JSON from the daemon reply, or None
    to fall through to Claude Code's normal terminal prompt. Fail-closed: anything that
    is not an explicit allow/deny -> None (never auto-allow)."""
    behavior = reply.get("decision") if isinstance(reply, dict) else None
    if behavior in ("allow", "deny"):
        return json.dumps({"hookSpecificOutput": {
            "hookEventName": "PermissionRequest",
            "decision": {"behavior": behavior}}})
    return None
