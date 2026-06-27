from pathlib import Path

from sonari.spearcon import SpearconCache, spearcon_label


def test_label_first_word_capped_at_12():
    assert spearcon_label("backend") == "backend"
    assert spearcon_label("my project here") == "my"          # first whitespace word
    assert spearcon_label("averylongfoldername") == "averylongfol"   # 12 chars
    assert spearcon_label("") == ""


def _recording():
    calls = []
    return calls, (lambda cmd: calls.append(cmd))


def test_key_is_stable_and_voice_rate_sensitive(tmp_path):
    a = SpearconCache(tmp_path, voice="Samantha", rate=525)
    b = SpearconCache(tmp_path, voice="Alex", rate=525)
    c = SpearconCache(tmp_path, voice="Samantha", rate=300)
    assert a.path_for("backend") == a.path_for("backend")     # deterministic
    assert a.path_for("backend") != b.path_for("backend")     # voice in key
    assert a.path_for("backend") != c.path_for("backend")     # rate in key
    assert a.path_for("backend").suffix == ".aiff"


def test_get_miss_kicks_background_generation_and_returns_none(tmp_path):
    calls, popen = _recording()
    cache = SpearconCache(tmp_path, voice="Samantha", rate=525, popen=popen)
    assert cache.get("backend") is None                       # not generated yet
    assert calls == [["say", "-v", "Samantha", "-r", "525",
                      "-o", str(cache.path_for("backend")), "backend"]]


def test_get_hit_returns_path_and_does_not_regenerate(tmp_path):
    calls, popen = _recording()
    cache = SpearconCache(tmp_path, voice="Samantha", rate=525, popen=popen)
    p = cache.path_for("backend")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"FORM....AIFF")                            # simulate a cached file
    assert cache.get("backend") == str(p)
    assert calls == []                                        # no regeneration on a hit


def test_generate_uses_truncated_label_as_say_text(tmp_path):
    calls, popen = _recording()
    cache = SpearconCache(tmp_path, voice="Samantha", rate=525, popen=popen)
    cache.generate("my project here")
    assert calls[0][-1] == "my"                               # spearcon_label applied


def test_pregenerate_skips_already_cached(tmp_path):
    calls, popen = _recording()
    cache = SpearconCache(tmp_path, voice="Samantha", rate=525, popen=popen)
    hit = cache.path_for("backend")
    hit.parent.mkdir(parents=True, exist_ok=True)
    hit.write_bytes(b"x")
    cache.pregenerate(["backend", "frontend", ""])           # backend cached, "" skipped
    assert [c[-1] for c in calls] == ["frontend"]


def test_cleanup_keeps_newest_by_mtime(tmp_path):
    import os
    cache = SpearconCache(tmp_path, voice="Samantha", rate=525)
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
    cache = SpearconCache(tmp_path, voice="Samantha", rate=525, popen=boom)
    assert cache.generate("backend") is None                # never raises
