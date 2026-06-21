from __future__ import annotations

import pytest


def _fresh_registry():
    """Return a fresh (HANDLERS={}) module-level state via reimport."""
    import importlib
    import sonari.daemon.registry as reg
    # Save and restore HANDLERS so tests are isolated from each other.
    original = dict(reg.HANDLERS)
    reg.HANDLERS.clear()
    return reg, original


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
