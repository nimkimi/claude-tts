from __future__ import annotations

import pytest


def test_handlers_is_dict_and_starts_empty():
    import sonari.daemon.registry as reg
    saved = dict(reg.HANDLERS)
    reg.HANDLERS.clear()
    try:
        assert isinstance(reg.HANDLERS, dict)
        assert reg.HANDLERS == {}
    finally:
        reg.HANDLERS.clear()
        reg.HANDLERS.update(saved)


def test_handler_decorator_registers_and_returns_fn():
    import sonari.daemon.registry as reg
    saved = dict(reg.HANDLERS)
    reg.HANDLERS.clear()
    try:
        def my_fn(ctx, msg):
            return "ok"

        result = reg.handler("ping")(my_fn)
        assert result is my_fn          # decorator returns fn (stackable)
        assert reg.HANDLERS["ping"] is my_fn
    finally:
        reg.HANDLERS.clear()
        reg.HANDLERS.update(saved)


def test_dispatch_unknown_type_returns_none_no_raise():
    import sonari.daemon.registry as reg
    saved = dict(reg.HANDLERS)
    reg.HANDLERS.clear()
    try:
        result = reg.dispatch(object(), {"type": "no_such_type"})
        assert result is None
    finally:
        reg.HANDLERS.clear()
        reg.HANDLERS.update(saved)


def test_dispatch_missing_type_key_returns_none():
    import sonari.daemon.registry as reg
    saved = dict(reg.HANDLERS)
    reg.HANDLERS.clear()
    try:
        result = reg.dispatch(object(), {})
        assert result is None
    finally:
        reg.HANDLERS.clear()
        reg.HANDLERS.update(saved)


def test_dispatch_calls_registered_handler():
    import sonari.daemon.registry as reg
    saved = dict(reg.HANDLERS)
    reg.HANDLERS.clear()
    try:
        calls = []

        @reg.handler("foo")
        def _h(ctx, msg):
            calls.append((ctx, msg))
            return "result"

        ctx = object()
        msg = {"type": "foo"}
        result = reg.dispatch(ctx, msg)
        assert result == "result"
        assert calls == [(ctx, msg)]
    finally:
        reg.HANDLERS.clear()
        reg.HANDLERS.update(saved)


def test_assert_complete_raises_on_missing_type():
    import sonari.daemon.registry as reg
    saved = dict(reg.HANDLERS)
    reg.HANDLERS.clear()
    try:
        @reg.handler("type_a")
        def _ha(ctx, msg):
            return None

        # type_b is NOT registered; assert_complete should raise naming it
        with pytest.raises((AssertionError, Exception)) as exc_info:
            reg.assert_complete(["type_a", "type_b"])
        assert "type_b" in str(exc_info.value)
    finally:
        reg.HANDLERS.clear()
        reg.HANDLERS.update(saved)


# ------------------------------------------------------------------ #
# Task 3.5 pins — all 27 MsgType keys present + negative guard        #
# ------------------------------------------------------------------ #

# Use MsgType attributes directly — the values are opaque strings; spelling them
# by attribute name ensures the pin tracks protocol changes automatically.
from sonari.protocol import MsgType as _MsgType

ALL_28 = [
    _MsgType.PROSE, _MsgType.CHOICE, _MsgType.PLAN, _MsgType.PERMISSION,
    _MsgType.EARCON, _MsgType.FLUSH, _MsgType.TOOL,
    _MsgType.SESSION_START, _MsgType.SESSION_END, _MsgType.SET_FOREGROUND,
    _MsgType.STOP, _MsgType.SKIP, _MsgType.NAV, _MsgType.STOP_SESSION,
    _MsgType.STOP_ALL, _MsgType.PIN_TOGGLE,
    _MsgType.JUMP_DECISION, _MsgType.JUMP_WAITING,
    _MsgType.SET_RATE, _MsgType.SET_VERBOSITY, _MsgType.SET_VOICE,
    _MsgType.SET_MINQUEUE, _MsgType.STATUS, _MsgType.PING,
    _MsgType.REREAD_OPTIONS, _MsgType.CYCLE_VERBOSITY, _MsgType.RELOAD_KEYMAP,
    _MsgType.OS_FOCUS,
]


def test_all_28_msgtypes_registered():
    """Every known MsgType must have a handler after the package is imported."""
    import sonari.daemon  # noqa: F401 — side-effect: registers all @handler thunks
    import sonari.daemon.registry as reg
    missing = [t for t in ALL_28 if t not in reg.HANDLERS]
    assert missing == [], "Missing handlers: {0}".format(missing)


def test_negative_assert_complete_names_missing_type():
    """Pop one registered type, assert_complete must raise naming it, restore."""
    import sonari.daemon  # noqa: F401 — ensure handlers are populated
    import sonari.daemon.registry as reg
    saved = dict(reg.HANDLERS)
    popped = reg.HANDLERS.pop("ping")
    try:
        with pytest.raises(AssertionError) as exc_info:
            reg.assert_complete(ALL_28)
        assert "ping" in str(exc_info.value)
    finally:
        reg.HANDLERS["ping"] = popped


def test_assert_complete_noop_when_all_present():
    import sonari.daemon.registry as reg
    saved = dict(reg.HANDLERS)
    reg.HANDLERS.clear()
    try:
        @reg.handler("type_x")
        def _hx(ctx, msg):
            return None

        @reg.handler("type_y")
        def _hy(ctx, msg):
            return None

        # Should not raise
        reg.assert_complete(["type_x", "type_y"])
    finally:
        reg.HANDLERS.clear()
        reg.HANDLERS.update(saved)
