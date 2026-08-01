"""D3 law (spec §3): the raw liveness signals (_provisional, _tty_evicted,
ttyutil.tty_alive) stay private to sessions.py — every other caller must go
through SessionManager.liveness()/is_live(). Grep tripwire in the
test_cue_contract.py idiom — crude but loud: a hit here means a new call site
drifted from the one composition instead of using the public API."""
import pathlib
import re

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "sonari"

_RAW = re.compile(r"\b_provisional\b|\b_tty_evicted\b|\btty_alive\b")
ALLOWED = {"sessions.py", "ttyutil.py"}

# D3 §3 second half: is_provisional() is a SessionManager-internal reporter (and
# a test/roster affordance), not a consumer predicate — a call site that asks
# "is this session provisional?" is asking a liveness question with one of the
# three states missing, which is exactly the drift that made where-am-I hide
# restored piles and narrate dead ones. Consumers consult liveness()/is_live().
_PROVISIONAL_CALL = re.compile(r"\bis_provisional\s*\(")
PROVISIONAL_ALLOWED = {"sessions.py"}


def _src_files():
    return sorted(SRC.rglob("*.py"))


def test_raw_liveness_signals_stay_private_to_sessions():
    for path in _src_files():
        if path.name in ALLOWED:
            continue
        hits = [(i + 1, ln) for i, ln in
                enumerate(path.read_text(encoding="utf-8").splitlines())
                if _RAW.search(ln)]
        assert not hits, "{0} references a raw liveness signal: {1}".format(
            path.name, hits[:3])


def test_no_src_consumer_asks_is_provisional():
    for path in _src_files():
        if path.name in PROVISIONAL_ALLOWED:
            continue
        hits = [(i + 1, ln) for i, ln in
                enumerate(path.read_text(encoding="utf-8").splitlines())
                if _PROVISIONAL_CALL.search(ln)]
        assert not hits, "{0} calls is_provisional instead of liveness(): {1}".format(
            path.name, hits[:3])


def test_guard_regex_ignores_the_public_method_names():
    # is_provisional must NOT trip the guard: no word boundary between the
    # "s" of "is" and the "_" of "_provisional" (both word chars) — only the
    # raw underscore-prefixed attribute/module-level name matches.
    assert not _RAW.search("sessions.is_provisional(session)")
    assert _RAW.search("self._provisional")
    assert _RAW.search("self._tty_evicted")
    assert _RAW.search("ttyutil.tty_alive(tty)")
