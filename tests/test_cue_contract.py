"""D8 law 4 pinned: feature code reaches audio ONLY through host.cue(kind) and
enqueue-with-prelude; the retired APIs stay dead; every emission maps to a
registered cue and every registered transient stays reachable; the say/afplay
runners are reachable only through Speaker.speak / Speaker.transient. Grep/AST
tripwires in the test_no_os_branch_in_core idiom — crude but loud: a hit here
means a NEW sound path bypassed the registry."""
import ast
import inspect
import pathlib
import re

from sonari.cues import CUES, transient_kinds

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "sonari"

FORBIDDEN = (".earcon(", ".earcon_then(", ".pitch(")


def _src_files():
    return sorted(SRC.rglob("*.py"))


def test_retired_audio_apis_are_never_called():
    for path in _src_files():
        text = path.read_text(encoding="utf-8")
        for token in FORBIDDEN:
            assert token not in text, \
                "{0} uses retired API {1}".format(path.name, token)


def test_transient_is_called_only_by_speaker_and_host_cue():
    allowed = {"speaker.py", "host.py"}
    for path in _src_files():
        if path.name in allowed:
            continue
        assert ".transient(" not in path.read_text(encoding="utf-8"), \
            "{0} bypasses host.cue".format(path)


_CUE_LIT = re.compile(r'\.cue\(\s*([\'"])([^\'"]+)\1')


def _cue_literals():
    # Either quote style: a single-quoted host.cue('bogus') must not evade the
    # registry-completeness check. Group 1 is the quote (backref), group 2 the kind.
    out = set()
    for path in _src_files():
        out |= {m[1] for m in _CUE_LIT.findall(path.read_text(encoding="utf-8"))}
    return out


def test_every_cue_literal_is_a_registered_transient():
    lits = _cue_literals()
    assert lits, "no host.cue call sites found — the scan is broken"
    assert lits <= transient_kinds(), lits - transient_kinds()


# turn_done/choice/plan/permission reach cue() DYNAMICALLY as the EARCON socket
# kind (tests/test_hooks_entry.py pins the hook side), so reachability is
# literal-OR-socket, never literal-only.
EARCON_SOCKET_KINDS = {"turn_done", "choice", "plan", "permission"}


def test_every_registered_transient_is_reachable():
    dead = transient_kinds() - _cue_literals() - EARCON_SOCKET_KINDS
    assert not dead, "registered but unreachable transients: {0}".format(dead)


def test_every_default_asset_kind_is_registered():
    from sonari.config import DEFAULTS
    assert set(DEFAULTS["earcons"]) <= set(CUES)


def test_prelude_entries_cover_chirps_callsign_and_crossing():
    assert {n for n, c in CUES.items() if c.tier == "prelude"} == {
        "pitch_up", "pitch_down", "callsign", "crossing"}


# --- verbal exclusivity (law 1): runners reachable only via speak/transient ---

PLAYERS = {"_say_runner", "_afplay_runner", "_earcon_player"}


def test_no_module_outside_speaker_touches_a_playback_runner():
    for path in _src_files():
        if path.name == "speaker.py":
            continue
        text = path.read_text(encoding="utf-8")
        for name in PLAYERS:
            assert name not in text, \
                "{0} touches {1}".format(path.name, name)


def _speaker_methods():
    tree = ast.parse((SRC / "speaker.py").read_text(encoding="utf-8"))
    cls = next(n for n in tree.body
               if isinstance(n, ast.ClassDef) and n.name == "Speaker")
    return {n.name: n for n in cls.body if isinstance(n, ast.FunctionDef)}


def _player_refs(fn):
    return {n.attr for n in ast.walk(fn)
            if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
            and n.value.id == "self" and n.attr in PLAYERS}


def test_runners_are_reachable_only_from_speak_and_transient():
    for name, fn in _speaker_methods().items():
        refs = _player_refs(fn)
        if name == "__init__":
            continue                                   # assignment only
        if name == "speak":
            assert refs <= {"_say_runner", "_afplay_runner"}, refs
        elif name == "transient":
            assert refs <= {"_earcon_player"}, refs
        else:
            assert not refs, "{0} touches a playback runner".format(name)


def test_transient_takes_a_kind_never_a_path():
    from sonari.speaker import Speaker
    assert list(inspect.signature(Speaker.transient).parameters) == ["self", "kind"]
