import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# pytest already prepends this directory (tests/ has no __init__.py), but say so
# explicitly: _isolation is also imported by scripts that run outside pytest,
# and the two entry points should reach it the same way.
_HERE = _REPO_ROOT / "tests"
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import pytest

from _isolation import isolate_paths


@pytest.fixture(autouse=True)
def _no_blocking_prompts(monkeypatch):
    """A test that reaches a real input() hangs the whole suite with no output.

    `uninstall()` prompts before deleting transcripts, gated on isatty(); pytest's
    captured stdout reports False, so it is unreachable *by accident today* — but
    that is incidental, not designed. Under `pytest -s`, or a CI runner that
    allocates a tty, it would block forever. Two agents were lost to exactly this
    hang before it was diagnosed. Fail loudly instead: any test that genuinely
    needs input() mocks it, and its mock takes precedence over this fixture.
    """
    def _refuse(prompt=""):
        raise AssertionError(
            "a test reached a real input() — mock it; an unmocked prompt hangs "
            "the suite instead of failing it (prompt was: {0!r})".format(prompt))
    monkeypatch.setattr("builtins.input", _refuse)


@pytest.fixture(autouse=True)
def _isolate_sonari_dir(tmp_path, monkeypatch):
    """Redirect every Sonari path to a per-test tmp dir.

    save_config (and anything else that writes under SONARI_DIR) targets
    CONFIG_PATH = ~/.sonari/config.json by default, which lives OUTSIDE the repo
    and is not git-tracked. Without isolation, daemon tests that exercise the
    real save_config() (e.g. the SET_RATE delta path) mutate the developer's
    actual Sonari config as a filesystem side effect. This autouse fixture
    repoints the path constants on every module that imported them so no test
    can ever touch the real ~/.sonari.

    The repoint list itself lives in tests/_isolation.py rather than here, so
    that an ad-hoc script can apply the identical list without pytest. Getting
    only part of that list is what caused the 2026-08-15 outage; the module's
    docstring carries the story and every per-constant comment.
    """
    isolate_paths(tmp_path / ".sonari", monkeypatch)
    yield
