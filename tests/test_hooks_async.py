import json
import pathlib

HOOKS = pathlib.Path(__file__).resolve().parents[1] / "hooks" / "hooks.json"

SYNC_EVENTS = {"PermissionRequest", "SessionStart"}


def _registrations():
    blob = json.loads(HOOKS.read_text(encoding="utf-8"))
    for event, entries in (blob.get("hooks") or {}).items():
        for entry in entries:
            for hook in entry.get("hooks", []):
                yield event, hook


def test_permission_request_stays_synchronous():
    for event, hook in _registrations():
        if event == "PermissionRequest":
            assert hook.get("async") is not True, (
                "PermissionRequest returns a decision; it cannot be async")


def test_session_start_stays_synchronous():
    """Ordering, not blocking. SessionStart is the only observation point for
    is_new (lifecycle.py:62-64), which gates the spoken "{folder}, {number}"
    registration announce. UserPromptSubmit also records the session, so an
    async SessionStart that loses the race silently suppresses that announce."""
    for event, hook in _registrations():
        if event == "SessionStart":
            assert hook.get("async") is not True, (
                "SessionStart is ordering-critical; async can lose the "
                "new-session announce")


def test_every_other_registration_is_async():
    for event, hook in _registrations():
        if event not in SYNC_EVENTS:
            assert hook.get("async") is True, f"{event} still blocks the session"


def test_the_async_set_is_not_empty_and_both_sync_events_exist():
    events = {e for e, _ in _registrations()}
    assert SYNC_EVENTS <= events
    assert events - SYNC_EVENTS
