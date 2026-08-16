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
import json
import subprocess
import sys
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Imports sonari.client / sonari.install_record BEFORE isolating, so the probe
# can prove those two module-level names are genuine by-value binds pointing at
# the real home first (a repoint of a name that never existed would prove
# nothing -- setattr happily invents it).
_PROBE = textwrap.dedent(
    """
    import json, sys
    from pathlib import Path

    sys.path.insert(0, {tests!r})
    sys.path.insert(0, {src!r})

    import sonari.client as client
    import sonari.install_record as install_record

    before = {{
        "client.LOCK_PATH": str(client.LOCK_PATH),
        "install_record.INSTALL_RECORD_PATH": str(
            install_record.INSTALL_RECORD_PATH),
    }}

    from _isolation import isolate_paths

    root = Path(sys.argv[1]) / ".sonari"
    isolate_paths(root)

    import sonari.paths as paths

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
        "home": str(Path.home()),
        "root": str(root),
        "before": before,
        "after": after,
        "constants": constants,
    }}))
    """
)


def _run_probe(sandbox):
    script = _PROBE.format(tests=str(REPO / "tests"), src=str(REPO / "src"))
    proc = subprocess.run(
        [sys.executable, "-c", script, str(sandbox)],
        capture_output=True,
        text=True,
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
