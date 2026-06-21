from __future__ import annotations


class _FakeHost:
    """Minimal stand-in for SpeechDaemon — only the attrs Ctx reads."""

    def __init__(self, config=None):
        self.speaker = object()
        self.sessions = object()
        self.history = object()
        self.config = config if config is not None else {}


def test_ctx_host_passthrough():
    from sonari.daemon.context import Ctx

    host = _FakeHost()
    ctx = Ctx(host)
    assert ctx.host is host


def test_ctx_speaker_passthrough():
    from sonari.daemon.context import Ctx

    host = _FakeHost()
    ctx = Ctx(host)
    assert ctx.speaker is host.speaker


def test_ctx_sessions_passthrough():
    from sonari.daemon.context import Ctx

    host = _FakeHost()
    ctx = Ctx(host)
    assert ctx.sessions is host.sessions


def test_ctx_config_passthrough():
    from sonari.daemon.context import Ctx

    host = _FakeHost()
    ctx = Ctx(host)
    assert ctx.config is host.config


def test_ctx_history_passthrough():
    from sonari.daemon.context import Ctx

    host = _FakeHost()
    ctx = Ctx(host)
    assert ctx.history is host.history


def test_ctx_bind_sets_session_from_msg():
    from sonari.daemon.context import Ctx

    host = _FakeHost()
    ctx = Ctx(host)
    ctx.bind({"type": "ping", "session": "abc"})
    assert ctx.session == "abc"


def test_ctx_bind_session_defaults_to_empty_string():
    from sonari.daemon.context import Ctx

    host = _FakeHost()
    ctx = Ctx(host)
    ctx.bind({"type": "ping"})
    assert ctx.session == ""


def test_ctx_verbosity_from_config():
    from sonari.daemon.context import Ctx

    host = _FakeHost(config={"verbosity": "quiet"})
    ctx = Ctx(host)
    assert ctx.verbosity == "quiet"


def test_ctx_verbosity_defaults_to_everything():
    from sonari.daemon.context import Ctx

    host = _FakeHost(config={})
    ctx = Ctx(host)
    assert ctx.verbosity == "everything"


def test_ctx_bind_returns_self():
    """bind() returns self so callers can chain: ctx.bind(msg).session."""
    from sonari.daemon.context import Ctx

    host = _FakeHost()
    ctx = Ctx(host)
    result = ctx.bind({"type": "ping"})
    assert result is ctx
