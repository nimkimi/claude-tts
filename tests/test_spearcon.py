import shlex
import subprocess
from pathlib import Path

from sonari.spearcon import SpearconCache, spearcon_label


def test_label_splits_on_hyphen_underscore_whitespace():
    # hyphen-separated (typical Claude Code folder names)
    assert spearcon_label("backend-api") == "backend"
    assert spearcon_label("invoice-generator") == "invoice"
    assert spearcon_label("claude-everywhere") == "claude"
    # underscore-separated
    assert spearcon_label("my_project") == "my"
    # whitespace still works
    assert spearcon_label("  spaced name ") == "spaced"
    # no delimiter — whole word returned
    assert spearcon_label("frontend") == "frontend"
    # 12-char cap on a long single component
    assert spearcon_label("averylongfoldername") == "averylongfol"
    # empty / falsy
    assert spearcon_label("") == ""
    assert spearcon_label(None) == ""


def _recording():
    calls = []
    return calls, (lambda cmd: calls.append(cmd))


def _fake_voice_lister():
    """Return a fake voice list with Samantha available (hermetic, no real say shell-out)."""
    return "Samantha            en_US    # Hello, I'm Samantha.\nAlex                en_US    # Hi.\n"


def _script(cache, label, *, voice=True):
    """The exact sh script generate() spawns for *label* (tmp render + publish)."""
    final = cache.path_for(label)
    tmp = final.parent / (final.name + ".part")
    cmd = ["say"]
    if voice:
        cmd += ["-v", "Samantha"]
    cmd += ["-r", "525", "-o", str(tmp), spearcon_label(label)]
    return "{0} && mv {1} {2}".format(
        " ".join(shlex.quote(c) for c in cmd),
        shlex.quote(str(tmp)), shlex.quote(str(final)))


def test_key_is_stable_and_voice_rate_sensitive(tmp_path):
    a = SpearconCache(tmp_path, voice="Samantha", rate=525, voice_lister=_fake_voice_lister)
    b = SpearconCache(tmp_path, voice="Alex", rate=525, voice_lister=_fake_voice_lister)
    c = SpearconCache(tmp_path, voice="Samantha", rate=300, voice_lister=_fake_voice_lister)
    assert a.path_for("backend") == a.path_for("backend")     # deterministic
    assert a.path_for("backend") != b.path_for("backend")     # voice in key
    assert a.path_for("backend") != c.path_for("backend")     # rate in key
    assert a.path_for("backend").suffix == ".aiff"


def test_get_miss_kicks_background_generation_and_returns_none(tmp_path):
    calls, popen = _recording()
    cache = SpearconCache(tmp_path, voice="Samantha", rate=525, popen=popen, voice_lister=_fake_voice_lister)
    assert cache.get("backend") is None                       # not generated yet
    # One spawned shell: render to the sibling temp path, atomic-rename on
    # success — a killed say can never leave a truncated cache hit (R1 rider).
    assert calls == [["sh", "-c", _script(cache, "backend")]]


def test_get_hit_returns_path_and_does_not_regenerate(tmp_path):
    calls, popen = _recording()
    cache = SpearconCache(tmp_path, voice="Samantha", rate=525, popen=popen, voice_lister=_fake_voice_lister)
    p = cache.path_for("backend")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"FORM....AIFF")                            # simulate a cached file
    assert cache.get("backend") == str(p)
    assert calls == []                                        # no regeneration on a hit


def test_generate_uses_truncated_label_as_say_text(tmp_path):
    calls, popen = _recording()
    cache = SpearconCache(tmp_path, voice="Samantha", rate=525, popen=popen, voice_lister=_fake_voice_lister)
    cache.generate("my project here")
    assert " my && mv " in calls[0][2]                        # spearcon_label applied


def test_pregenerate_skips_already_cached(tmp_path):
    calls, popen = _recording()
    cache = SpearconCache(tmp_path, voice="Samantha", rate=525, popen=popen, voice_lister=_fake_voice_lister)
    hit = cache.path_for("backend")
    hit.parent.mkdir(parents=True, exist_ok=True)
    hit.write_bytes(b"x")
    cache.pregenerate(["backend", "frontend", ""])           # backend cached, "" skipped
    assert len(calls) == 1 and " frontend && mv " in calls[0][2]


def test_cleanup_keeps_newest_by_mtime(tmp_path):
    import os
    cache = SpearconCache(tmp_path, voice="Samantha", rate=525, voice_lister=_fake_voice_lister)
    d = tmp_path
    d.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, name in enumerate(["a", "b", "c"]):
        p = d / "{0}.aiff".format(name)
        p.write_bytes(b"x")
        os.utime(p, (i, i))                                  # a oldest, c newest
        paths.append(p)
    cache.cleanup(max_files=2)
    assert not paths[0].exists()                             # oldest pruned
    assert paths[1].exists() and paths[2].exists()


def test_generate_swallows_popen_error(tmp_path):
    def boom(cmd):
        raise OSError("say missing")
    cache = SpearconCache(tmp_path, voice="Samantha", rate=525, popen=boom,
                          voice_lister=lambda: "Samantha  en_US  # Hi.\n")
    assert cache.generate("backend") is None                # never raises


# ---------------------------------------------------------------------------
# Voice-availability fallback tests (spec §17.1)
# ---------------------------------------------------------------------------

_VOICE_LIST_WITH_SAMANTHA = "Samantha            en_US    # Hello, I'm Samantha.\nAlex                en_US    # Hi.\n"
_VOICE_LIST_WITHOUT_SAMANTHA = "Alex                en_US    # Hi.\nFred                en_US    # Hello.\n"


def test_generate_includes_voice_flag_when_voice_available(tmp_path):
    """Voice found in `say -v '?'` → command carries -v <voice>."""
    calls, popen = _recording()
    cache = SpearconCache(tmp_path, voice="Samantha", rate=525, popen=popen,
                          voice_lister=lambda: _VOICE_LIST_WITH_SAMANTHA)
    cache.generate("backend")
    assert calls[0] == ["sh", "-c", _script(cache, "backend")]


def test_generate_omits_voice_flag_when_voice_not_in_list(tmp_path):
    """Voice absent from `say -v '?'` → command uses system default (no -v)."""
    calls, popen = _recording()
    cache = SpearconCache(tmp_path, voice="Samantha", rate=525, popen=popen,
                          voice_lister=lambda: _VOICE_LIST_WITHOUT_SAMANTHA)
    cache.generate("backend")
    assert calls[0] == ["sh", "-c", _script(cache, "backend", voice=False)]


def test_generate_omits_voice_flag_when_voice_lister_raises(tmp_path):
    """voice_lister error (e.g. `say` missing) → fall back to system default."""
    calls, popen = _recording()
    def boom():
        raise OSError("say not found")
    cache = SpearconCache(tmp_path, voice="Samantha", rate=525, popen=popen,
                          voice_lister=boom)
    cache.generate("backend")
    assert calls[0] == ["sh", "-c", _script(cache, "backend", voice=False)]


# ---------------------------------------------------------------------------
# R1 rider: atomic tmp+rename publish (D2+D7 first commit)
# ---------------------------------------------------------------------------

def _stub_say_env(tmp_path, script_body):
    """A PATH bin dir whose `say` runs *script_body*; returns a popen that uses it."""
    import os
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "say"
    stub.write_text("#!/bin/sh\n" + script_body)
    stub.chmod(0o755)
    env = dict(os.environ, PATH="{0}:{1}".format(bin_dir, os.environ["PATH"]))
    return lambda cmd: subprocess.Popen(cmd, env=env)


_SAY_PARSE_OUT = (
    'out=""\n'
    'while [ $# -gt 0 ]; do\n'
    '  if [ "$1" = "-o" ]; then out="$2"; shift; fi\n'
    '  shift\n'
    'done\n'
)


def test_failed_render_never_leaves_a_truncated_final_file(tmp_path):
    # `say` writes a partial temp file then dies (exit 1): the && never
    # publishes, so get() can never treat the wreck as a permanent cache hit.
    popen = _stub_say_env(tmp_path, _SAY_PARSE_OUT + 'printf partial > "$out"\nexit 1\n')
    cache = SpearconCache(tmp_path / "cache", voice="Samantha", rate=525,
                          popen=popen, voice_lister=_fake_voice_lister)
    proc = cache.generate("backend")
    proc.wait(timeout=10)
    assert not cache.path_for("backend").exists()             # no truncated hit
    assert cache.get("backend") is None                       # still a MISS


def test_successful_render_publishes_the_final_file(tmp_path):
    popen = _stub_say_env(tmp_path, _SAY_PARSE_OUT + 'printf FORMAIFF > "$out"\nexit 0\n')
    cache = SpearconCache(tmp_path / "cache", voice="Samantha", rate=525,
                          popen=popen, voice_lister=_fake_voice_lister)
    proc = cache.generate("backend")
    proc.wait(timeout=10)
    final = cache.path_for("backend")
    assert final.exists() and final.read_bytes() == b"FORMAIFF"
    assert not (final.parent / (final.name + ".part")).exists()   # tmp consumed
    assert cache.get("backend") == str(final)                 # a real hit now


def test_cleanup_sweeps_stale_part_files(tmp_path):
    cache = SpearconCache(tmp_path, voice="Samantha", rate=525, voice_lister=_fake_voice_lister)
    keep = tmp_path / "x.aiff"
    keep.write_bytes(b"x")
    stale = tmp_path / "y.aiff.part"
    stale.write_bytes(b"y")
    cache.cleanup(max_files=10)
    assert keep.exists()
    assert not stale.exists()                                 # stale render swept
