"""Guard: no module outside paths.py derives a path from the user's home
directory independently. expanduser(), Path.home(), and the HOME env var must
all be read from paths.py (the single source of truth conftest.py's isolation
fixture repoints) -- except a small, explicitly justified allowlist of reads
that must legitimately stay absolute, verified by hand below. Mirrors
test_no_os_branch_in_core.py's convention: plain substring checks over an
explicit file list, not AST parsing.

Full inventory:
/Users/Nima.Hakimi/projects/private/sonari/.superpowers/sdd/hermeticity-audit.md
(main checkout, read-only reference -- not part of this worktree's history).
"""
import pathlib

SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "sonari"

_HOME_PATTERNS = (
    "expanduser(", "Path.home()",
    'os.environ["HOME"]', "os.environ['HOME']",
    'os.getenv("HOME"', "os.getenv('HOME'",
    'environ.get("HOME"', "environ.get('HOME'",
)

# Files outside paths.py that legitimately construct a home-relative path
# independently. Each entry carries a one-line reason, verified by reading
# the actual code (not taken on the audit's word alone). Anything NOT listed
# here that hits a pattern above is a hermeticity bug: report it, don't
# silently add it here.
ALLOWLIST = {
    # daemon/host.py's no_hotkeys kill-switch (two call sites: _start_hotkeys,
    # _reload_hotkeys) must be readable "however the daemon is spawned" (its
    # own comment) -- hooks inherit their own env, not SONARI_DIR's test-time
    # patch, so it is deliberately NOT derived from paths.py. Read-only
    # os.path.exists() check, never writes.
    "daemon/host.py":
        "no_hotkeys kill-switch flag; must work regardless of how/whether "
        "SONARI_DIR was patched -- read-only exists() check, never writes",
    # summarizer.py's _default_which() fallback probe for the user's real
    # `claude` CLI: the daemon's LaunchAgent PATH is bare, so shutil.which
    # alone misses per-user install locations. Read-only isfile()/access()
    # probe, never writes.
    "summarizer.py":
        "claude-binary fallback dirs probe (LaunchAgent PATH is bare); "
        "read-only isfile()/access(), never writes",
    # platform/macos/supervisor.py's _launcher_path()/_local_bin_dir() are the
    # ALREADY-FIXED 53850cc case: conftest.py's _isolate_sonari_dir fixture
    # replaces these two FUNCTIONS wholesale with lambdas (not a
    # path-constant patch), so they are structurally isolated today by a
    # different mechanism than this repo's paths.py-vs-conftest diff guard
    # (test_paths_conftest_isolation.py). Not moved into paths.py here: this
    # task's scope is guards + isolation only, no production-code behavior
    # change (see task-5-report.md). The allowlist-rot test below asserts the
    # conftest lambda replacements are still in place, so if that mechanism
    # is ever removed, this entry fails loudly instead of silently
    # re-opening the 53850cc hazard.
    "platform/macos/supervisor.py":
        "_launcher_path()/_local_bin_dir() -- isolated via conftest's "
        "function-object replacement (53850cc), not a path-constant patch; "
        "see the allowlist-rot test for the trip-wire on that mechanism",
}


def test_no_home_derivation_outside_paths_py_or_allowlist():
    hits = {}
    for pyfile in SRC.rglob("*.py"):
        rel = str(pyfile.relative_to(SRC))
        if rel == "paths.py":
            continue
        text = pyfile.read_text(encoding="utf-8")
        found = [p for p in _HOME_PATTERNS if p in text]
        if found and rel not in ALLOWLIST:
            hits[rel] = found
    assert not hits, (
        "src/sonari/ files derive a path from the home directory "
        "independently of paths.py: {0}. Either derive it from a paths.py "
        "constant (preferred -- conftest.py's isolation fixture then "
        "protects it automatically), or add the file to this guard's "
        "ALLOWLIST in tests/test_no_independent_home_derivation.py with a "
        "one-line reason it must legitimately stay absolute.".format(hits)
    )


def test_allowlist_entries_still_exist_and_still_match():
    """Catches allowlist rot: a stale entry that no longer hits a pattern
    (dead allowlist entry, hides nothing) or no longer exists on disk."""
    for rel in ALLOWLIST:
        pyfile = SRC / rel
        assert pyfile.is_file(), "allowlisted {0} no longer exists".format(rel)
        text = pyfile.read_text(encoding="utf-8")
        assert any(p in text for p in _HOME_PATTERNS), (
            "allowlisted {0} no longer contains any home-derivation pattern "
            "-- remove it from ALLOWLIST (a dead entry hides nothing, and "
            "silently permits a future, unrelated expanduser() call there)."
            .format(rel))


def test_supervisor_launch_path_functions_still_isolated_via_conftest():
    """Trip-wire for the supervisor.py allowlist entry's actual justification:
    it is safe ONLY because conftest.py replaces _local_bin_dir/_launcher_path
    wholesale. If that replacement is ever removed, this must fail loudly --
    a silent ALLOWLIST entry would otherwise re-open the 53850cc hazard
    (running the suite deletes the developer's real ~/.local/bin/sonari)."""
    conftest_text = (
        pathlib.Path(__file__).resolve().parent / "conftest.py"
    ).read_text(encoding="utf-8")
    assert '"_local_bin_dir"' in conftest_text, (
        "conftest.py no longer patches supervisor._local_bin_dir -- the "
        "platform/macos/supervisor.py entry in this guard's ALLOWLIST is "
        "now UNSAFE (see 53850cc); either restore the patch or close the "
        "gap some other way before removing the trip-wire")
    assert '"_launcher_path"' in conftest_text, (
        "conftest.py no longer patches supervisor._launcher_path -- the "
        "platform/macos/supervisor.py entry in this guard's ALLOWLIST is "
        "now UNSAFE (see 53850cc); either restore the patch or close the "
        "gap some other way before removing the trip-wire")
