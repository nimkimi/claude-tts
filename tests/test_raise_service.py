# tests/test_raise_service.py
import threading

from sonari.raise_service import RaiseService
from sonari.sessions import Identity


class FakeBackend:
    def __init__(self, supports=True, result=True, gate=None, entered=None):
        self._supports = supports
        self._result = result
        self._gate = gate          # threading.Event the call waits on, if set
        self._entered = entered    # threading.Event set when raise_session begins
        self.calls = []

    def supports(self, identity):
        return self._supports

    def raise_session(self, identity):
        self.calls.append(identity)
        if self._entered is not None:
            self._entered.set()
        if self._gate is not None:
            self._gate.wait(2.0)
        return self._result


def test_will_attempt_requires_flag_identity_and_support():
    ident = Identity(term_program="Apple_Terminal", tty="/dev/ttys1")
    assert RaiseService(FakeBackend(supports=True), {"focus_follow": True}).will_attempt(ident) is True
    assert RaiseService(FakeBackend(supports=True), {"focus_follow": False}).will_attempt(ident) is False
    assert RaiseService(FakeBackend(supports=False), {"focus_follow": True}).will_attempt(ident) is False
    assert RaiseService(FakeBackend(supports=True), {"focus_follow": True}).will_attempt(None) is False


def test_successful_raise_does_not_call_on_failure():
    be = FakeBackend(result=True)
    svc = RaiseService(be, {"focus_follow": True})
    called = []
    gen = svc.bump_generation()
    svc.raise_async(Identity(tty="/dev/ttys1"), gen, on_failure=lambda: called.append(1))
    svc.join(2.0)
    assert be.calls and not called


def test_failed_current_raise_calls_on_failure():
    be = FakeBackend(result=False)
    svc = RaiseService(be, {"focus_follow": True})
    called = []
    gen = svc.bump_generation()
    svc.raise_async(Identity(tty="/dev/ttys1"), gen, on_failure=lambda: called.append(1))
    svc.join(2.0)
    assert called == [1]


def test_stale_generation_aborts_before_raise():
    be = FakeBackend(result=False)
    svc = RaiseService(be, {"focus_follow": True})
    called = []
    stale = svc.bump_generation()   # gen 1
    svc.bump_generation()           # gen 2 (now current) — supersedes
    svc.raise_async(Identity(tty="/dev/ttys1"), stale, on_failure=lambda: called.append(1))
    svc.join(2.0)
    assert be.calls == []           # raise never attempted
    assert called == []             # no stale failure cue


def test_supersede_during_slow_raise_suppresses_failure_cue():
    gate, entered = threading.Event(), threading.Event()
    be = FakeBackend(result=False, gate=gate, entered=entered)
    svc = RaiseService(be, {"focus_follow": True})
    called = []
    gen = svc.bump_generation()
    svc.raise_async(Identity(tty="/dev/ttys1"), gen, on_failure=lambda: called.append(1))
    assert entered.wait(2.0)        # raise is in-flight
    svc.bump_generation()           # a newer jump arrives mid-raise
    gate.set()                      # let the slow raise finish (returns False)
    svc.join(2.0)
    assert be.calls                 # it did run
    assert called == []             # but the failure cue is suppressed (superseded)
