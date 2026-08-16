"""Regression tests for the single isolation list (tests/_isolation.py).

On 2026-08-15 an ad-hoc probe script ran against the developer's machine with a
sacrificial mkdtemp HOME. It repointed `sonari.paths.*` but missed the two
BY-VALUE binds -- `install_record.INSTALL_RECORD_PATH` and `client.LOCK_PATH`
-- so it wrote fixture values into the REAL ~/.sonari/install.json and deleted
the REAL ~/.sonari/daemon.lock while the real daemon kept running. The daemon's
port and token live ONLY in that lockfile, so the running daemon became
permanently unreachable -- and it still held the single-instance flock, so no
replacement could start either. The user, who is blind, had no speech at all
for about nineteen hours.

The failure mode was PARTIAL isolation. These tests prove isolation is atomic
in both modes:

  - standalone (`isolate_paths(root)`, no monkeypatch) -- the path an ad-hoc
    script takes. Exercised in a SUBPROCESS: those repoints are
    process-lifetime by design, so applying them in-process would leak into
    every test that runs after this one.
  - under monkeypatch -- the suite's own path, asserted live from inside the
    autouse fixture.
"""
import ast
import importlib
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# Captured at module import, which happens before any fixture runs -- so this is
# the home the developer actually has, even though the isolation fixture
# repoints HOME for the duration of each test. The standalone probe models an
# ad-hoc script run on a real machine, so it must be handed a HOME that is NOT
# already sandboxed, or its "did this escape to the real home?" assertions
# become tautologies.
HOME_AT_IMPORT = Path.home()
SRC = REPO / "src" / "sonari"


def _paths_py_constants():
    tree = ast.parse((SRC / "paths.py").read_text(encoding="utf-8"))
    return {
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name) and target.id.isupper()
    }


def _module_level_by_value_binds():
    """Every (module, attribute) pair in src/ that copies a paths.py constant at
    import time — the module-COPY class, which is what the outage actually was.

    Derived from `src/`, deliberately NOT from tests/_isolation.py. A list
    derived from the helper would shrink in lockstep with a deleted repoint and
    the test would pass hollowly, proving nothing. Derived from src/ it also
    fails for a BRAND-NEW by-value bind that nobody remembered to add to the
    helper — which is the outage's actual root cause, not just its symptom.

    AST rather than this repo's usual text matching, and the reason is specific:
    the property that matters is *module-level* binding. A function-local
    `from sonari.paths import FAULTLOG_PATH` (daemon/bootstrap.py, inside
    _arm_faulthandler) is a LIVE read and needs no repoint at all; the identical
    text at module level is a stale copy that does. bootstrap.py contains both
    forms, so text matching cannot tell them apart — it would demand repoints
    for names that must not have them.
    """
    constants = _paths_py_constants()
    pairs = []
    for pyfile in sorted(SRC.rglob("*.py")):
        if pyfile.name == "paths.py":
            continue
        dotted = "sonari." + str(
            pyfile.relative_to(SRC).with_suffix("")).replace("/", ".")
        dotted = dotted[: -len(".__init__")] if dotted.endswith(".__init__") else dotted
        for node in ast.parse(pyfile.read_text(encoding="utf-8")).body:
            # `from sonari.paths import LOCK_PATH` — a by-value bind.
            if isinstance(node, ast.ImportFrom) and node.module == "sonari.paths":
                for alias in node.names:
                    if alias.name in constants:
                        pairs.append((dotted, alias.asname or alias.name))
            # `LAUNCH_AGENT_PATH = str(paths.SPEECHD_LAUNCH_AGENT_PATH)` — a
            # by-value bind under a DIFFERENT name, which an import scan misses.
            elif isinstance(node, ast.Assign):
                rhs = ast.unparse(node.value)
                if any("paths." + const in rhs for const in constants):
                    pairs.extend(
                        (dotted, t.id) for t in node.targets
                        if isinstance(t, ast.Name))
    return sorted(set(pairs))


def _resolve(value):
    """Module copies are Paths, except the two LAUNCH_AGENT_PATHs, which are str."""
    return value if isinstance(value, Path) else Path(value)

# Every module is imported BEFORE isolating, which is the outage's own shape:
# the 2026-08-15 script had sonari loaded and THEN repointed paths.*, so every
# module copy was already stale. It also keeps the probe honest twice over --
# a repoint of a name that never existed would prove nothing (setattr happily
# invents one), and a module imported AFTER isolation inherits the repointed
# value for free, so isolating first would test nothing at all.
_PROBE = textwrap.dedent(
    """
    import importlib, json, sys
    from pathlib import Path

    sys.path.insert(0, {tests!r})
    sys.path.insert(0, {src!r})

    import sonari.client as client
    import sonari.install_record as install_record

    pairs = json.loads(sys.argv[2])
    for dotted, _attr in pairs:
        importlib.import_module(dotted)

    def _copies():
        return {{
            dotted + "." + attr:
                str(getattr(sys.modules[dotted], attr))
            for dotted, attr in pairs
        }}

    before = {{
        "client.LOCK_PATH": str(client.LOCK_PATH),
        "install_record.INSTALL_RECORD_PATH": str(
            install_record.INSTALL_RECORD_PATH),
    }}
    copies_before = _copies()
    # BEFORE isolating: isolate_paths repoints HOME as well, so Path.home()
    # read afterwards would be the sandbox and every "did it escape?" check
    # would compare the sandbox against itself.
    home_before = str(Path.home())

    from _isolation import isolate_paths

    root = Path(sys.argv[1]) / ".sonari"
    isolate_paths(root)

    import sonari.paths as paths

    copies = _copies()

    after = {{
        "client.LOCK_PATH": str(client.LOCK_PATH),
        "install_record.INSTALL_RECORD_PATH": str(
            install_record.INSTALL_RECORD_PATH),
        "paths.LOCK_PATH": str(paths.LOCK_PATH),
        "paths.INSTALL_RECORD_PATH": str(paths.INSTALL_RECORD_PATH),
    }}
    constants = {{
        name: str(value)
        for name, value in vars(paths).items()
        if name.isupper() and isinstance(value, Path)
    }}
    print(json.dumps({{
        "home": home_before,
        "root": str(root),
        "before": before,
        "after": after,
        "constants": constants,
        "copies_before": copies_before,
        "copies": copies,
    }}))
    """
)


def _run_probe(sandbox):
    script = _PROBE.format(tests=str(REPO / "tests"), src=str(REPO / "src"))
    pairs = json.dumps(_module_level_by_value_binds())
    env = dict(os.environ, HOME=str(HOME_AT_IMPORT))
    proc = subprocess.run(
        [sys.executable, "-c", script, str(sandbox), pairs],
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 0, (
        "standalone isolation probe failed:\nstdout={0}\nstderr={1}".format(
            proc.stdout, proc.stderr))
    return json.loads(proc.stdout)


def test_standalone_isolation_covers_the_two_binds_that_caused_the_outage(tmp_path):
    """`isolate_paths(root)` with no monkeypatch -- the ad-hoc-script path."""
    out = _run_probe(tmp_path)
    root = Path(out["root"])
    home = Path(out["home"])

    # The premise: both names really are bound by value to the real home when
    # nobody has isolated them. If this ever stops holding, the assertions
    # below stop meaning anything and this test must be re-derived.
    for name, value in out["before"].items():
        assert Path(value).is_relative_to(home / ".sonari"), (
            "{0} was expected to point into the real ~/.sonari before "
            "isolation (it is a by-value bind); got {1}".format(name, value))

    for name, value in out["after"].items():
        assert Path(value).is_relative_to(root), (
            "{0} is {1} after isolate_paths({2}) -- the standalone path did "
            "NOT repoint it. This is the exact gap that destroyed the "
            "developer's real ~/.sonari on 2026-08-15.".format(
                name, value, root))
        assert not Path(value).is_relative_to(home / ".sonari"), (
            "{0} still resolves inside the real ~/.sonari: {1}".format(
                name, value))


def test_standalone_isolation_is_atomic_for_every_paths_constant(tmp_path):
    """Not just the two binds: nothing in paths.py may survive pointing home.

    Behavioural counterpart to test_paths_conftest_isolation.py, which matches
    the repoint list textually and so cannot see a repoint whose VALUE is wrong.
    """
    out = _run_probe(tmp_path)
    sandbox = tmp_path
    stray = {
        name: value
        for name, value in out["constants"].items()
        if not Path(value).is_relative_to(sandbox)
    }
    assert not stray, (
        "after isolate_paths({0}), these sonari.paths constants still resolve "
        "outside the sandbox: {1}. Isolation must be all-or-nothing.".format(
            tmp_path / ".sonari", stray))


def test_standalone_isolation_covers_every_module_level_by_value_bind(tmp_path):
    """The ad-hoc-script path gets the same all-or-nothing guarantee. This is
    the path the 2026-08-15 probe script took, so it is the one that matters
    most for the module copies."""
    out = _run_probe(tmp_path)
    home = Path(out["home"])

    # Premise: every one of these really was a stale copy pointing into the
    # user's own directories before isolation. Without this half, a copy that
    # happened to be harmless would still "pass" the check below.
    not_stale = {
        name: value
        for name, value in out["copies_before"].items()
        if not Path(value).is_relative_to(home)
    }
    assert not not_stale, (
        "expected every module-level copy to point into the real home before "
        "isolation; these did not, so the scan is finding the wrong things: "
        "{0}".format(not_stale))

    stray = {
        name: value
        for name, value in out["copies"].items()
        if not Path(value).is_relative_to(tmp_path)
    }
    assert not stray, (
        "after isolate_paths({0}), these module-level copies of paths.py "
        "constants still resolve outside the sandbox: {1}".format(
            tmp_path / ".sonari", stray))


def test_suite_isolation_covers_the_two_binds_that_caused_the_outage(tmp_path):
    """Same guarantee for the monkeypatch path the whole suite runs under.

    Nothing else asserts the module-COPY repoints behaviourally: the textual
    guard only matches repoints of the `paths` module itself.
    """
    import sonari.client as client
    import sonari.install_record as install_record

    assert client.LOCK_PATH == tmp_path / ".sonari" / "daemon.lock"
    assert (install_record.INSTALL_RECORD_PATH
            == tmp_path / ".sonari" / "install.json")


def test_the_by_value_bind_scan_is_not_vacuous():
    """The enumeration below is only worth anything if it finds the binds.

    A scanner that silently returned [] would make every test that consumes it
    pass while checking nothing — the same hollowing this file exists to
    prevent, one level up. Pinned to the two binds that caused the outage plus a
    floor on the count, so a scan that quietly stops matching fails loudly.
    """
    pairs = _module_level_by_value_binds()
    assert ("sonari.client", "LOCK_PATH") in pairs
    assert ("sonari.install_record", "INSTALL_RECORD_PATH") in pairs
    assert len(pairs) >= 10, (
        "expected at least the 10 known module-level by-value binds, found "
        "{0}: {1}".format(len(pairs), pairs))


def test_suite_isolation_covers_every_module_level_by_value_bind(tmp_path):
    """Not just the two outage binds: EVERY module copy, or isolation is not
    atomic. Guard 1 is blind to all of these by construction — it matches
    repoints of the `paths` module only, never the module copies."""
    stray = {}
    for dotted, attr in _module_level_by_value_binds():
        module = importlib.import_module(dotted)
        value = _resolve(getattr(module, attr))
        if not value.is_relative_to(tmp_path):
            stray["{0}.{1}".format(dotted, attr)] = str(value)
    assert not stray, (
        "these module-level copies of paths.py constants were NOT repointed by "
        "tests/_isolation.py and still resolve outside this test's tmp_path: "
        "{0}. A missing module-copy repoint is the exact 2026-08-15 failure "
        "mode -- add _setattr(<module>, \"<NAME>\", ...) to isolate_paths()."
        .format(stray))


def test_suite_isolation_repoints_home_so_spawned_children_are_covered(tmp_path):
    """The only repoint that survives a fork, so it gets its own test.

    A child re-imports sonari from scratch: attribute repoints cannot reach it,
    HOME can, because paths.py derives from Path.home(). `ensure_running()` does
    subprocess.Popen, and an escaped uninstall() child is what deleted the
    developer's real ~/.sonari/app and ~/.local/bin/sonari on 2026-08-16 — so
    this is proven by actually spawning a child, not by reading os.environ.
    """
    proc = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, {0!r}); "
         "from sonari.paths import INSTALL_RECORD_PATH; "
         "print(INSTALL_RECORD_PATH)".format(str(REPO / "src"))],
        capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    child_path = Path(proc.stdout.strip())
    assert child_path.is_relative_to(tmp_path), (
        "a subprocess spawned from a test resolved INSTALL_RECORD_PATH to {0}, "
        "outside this test's tmp_path -- isolate_paths() is no longer "
        "repointing HOME, so anything the suite spawns can reach the real "
        "~/.sonari".format(child_path))


def test_suite_isolation_is_atomic_for_every_paths_constant(tmp_path):
    """Every paths.py constant lands under this test's own tmp_path.

    Deliberately compared against tmp_path rather than "not under $HOME": the
    per-test dir is the sharper claim, and it does not false-pass on a machine
    whose TMPDIR happens to live under the home directory.
    """
    import sonari.paths as paths

    stray = {
        name: str(value)
        for name, value in vars(paths).items()
        if name.isupper() and isinstance(value, Path)
        and not value.is_relative_to(tmp_path)
    }
    assert not stray, (
        "conftest's autouse isolation left these sonari.paths constants "
        "outside this test's tmp_path: {0}".format(stray))
