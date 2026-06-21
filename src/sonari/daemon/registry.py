from __future__ import annotations

HANDLERS = {}


def handler(t):
    def deco(fn):
        HANDLERS[t] = fn
        return fn
    return deco


def _ignore(ctx, msg):
    return None


def dispatch(ctx, msg):
    return HANDLERS.get(msg.get("type"), _ignore)(ctx, msg)


def assert_complete(known_types):
    missing = [t for t in known_types if t not in HANDLERS]
    assert not missing, "MsgType(s) without a handler: {0}".format(missing)
