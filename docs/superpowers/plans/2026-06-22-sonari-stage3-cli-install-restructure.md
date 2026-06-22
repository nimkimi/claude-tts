# Sonari Stage 3 — CLI / Install Restructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure `src/sonari/cli.py` (568 lines, the repo's #1 file by size and churn) into a `cli/` package that isolates the destructive install/uninstall code, and resolve four measured duplications into shared helpers — with byte-identical behavior.

**Architecture:** Mirror the Stage-2 `daemon/features/` idiom: `cli.py` → a `cli/` package (`__init__` dispatch core + `control`/`doctor`/`install` modules + `__main__`). Three shared-helper modules absorb the dups (`atomicio.py`, `install_record.py`, `platform/macos/_helpers.py`). Net-first, risk-monotonic: pure helpers first, the package skeleton mid, the destructive `install.py` last.

**Tech Stack:** Python 3.9+, stdlib only (argparse, json, os, shutil, subprocess, hashlib). pytest for tests. macOS-only.

**Spec:** `docs/superpowers/specs/2026-06-22-sonari-stage3-cli-install-restructure.md`. **Branch:** `sonari-stage3-cli-install` (off `main`; spec committed at `10b4734`).

## Global Constraints

- **Behavior byte-identical.** No user-facing change: same speech/earcons, same files written/deleted, same file bytes, same permissions (lockfile `0o600`), same fsync behavior. Any divergence is a bug, not a change.
- **`write_install_record` moves VERBATIM** — plain `open()` + `json.dump(record, f, indent=2)` + `f.write("\n")`, no fsync, no atomic rename. It is **NOT** routed through `atomic_write_json` (it is not duplicated; routing it would change install.json's bytes). Keeps its real 5-arg signature `(python, python_version, plugin_root, app_path, plugin_version)` that builds the record + `installed_at` timestamp internally.
- **`atomic_write_json` reproduces each call site exactly** via params: `config`/`keymap` → `indent=2, fsync=True, chmod=None`; `keymap.write_resolved` → `indent=None, fsync=True, chmod=None`; `transport.write_lockfile` → `indent=None, fsync=False, chmod=0o600`. `kokoro._download` is NOT migrated (streaming).
- **`build_swift_binary(src, out, hash_path, src_label, unchanged_note)`** — five params; two independent message strings (OSError infix + unchanged-grant note), not one.
- **Test discipline — "fake fires," not "suite green."** When a symbol moves, repoint its patch target in the SAME commit to where it is now looked up, and prove interception (`mock.Mock` + `assert_called…`, sentinel, or make-the-fake-raise). A test that passes with the real function running is not a valid gate.
- **Function-local imports for shared plumbing** (`_platform`/`_send`/`_daemon_not_running_message`/`_resolve_python`): command modules import these from `sonari.cli` INSIDE function bodies, never at module level (a module-level import binds the unpatched original → silent hollow; also avoids an import cycle).
- **`cli/__main__.py` is mandatory** — `bin/sonari` runs `python -m sonari.cli`; the console script is `sonari.cli:main`.
- **Suite gate:** `.venv/bin/python -m pytest -q --ignore=tests/test_kokoro.py` → **752 passed**, green AND count-never-drops at every task boundary.
- **Sacrificial HOME only** for any live install/uninstall execution (subprocess with `HOME=$(mktemp -d)`). NEVER `sonari install`/`uninstall` against the real `~/.sonari`. NEVER the owner as a test harness.
- **Git:** work stays on `sonari-stage3-cli-install`. `git add` EXACT paths only (never `-A`/`.`/`-u`). NEVER commit `.convergence-plan.md` or `docs/getting-started.md`. NO `claude.ai/code/session` link in any commit message. Do NOT push or open PRs.

---

### Task 1: `atomicio.atomic_write_json` helper

**Files:**
- Create: `src/sonari/atomicio.py`
- Test: `tests/test_atomicio.py`

**Interfaces:**
- Produces: `atomic_write_json(path, data, *, indent=None, chmod=None, fsync=True) -> None` — writes `str(path)+".tmp"`, `json.dump(data, fh, indent=indent)`, flush, `os.fsync` iff `fsync`, `os.chmod(tmp, chmod)` iff `chmod is not None`, then `os.replace(tmp, str(path))`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_atomicio.py
import json
import os
import stat

from sonari.atomicio import atomic_write_json


def test_writes_indented_json_no_trailing_newline(tmp_path):
    p = tmp_path / "x.json"
    atomic_write_json(p, {"b": 2, "a": 1}, indent=2)
    raw = p.read_bytes()
    assert raw == json.dumps({"b": 2, "a": 1}, indent=2).encode("utf-8")
    assert not raw.endswith(b"\n")  # json.dump adds no trailing newline


def test_compact_when_indent_none(tmp_path):
    p = tmp_path / "x.json"
    atomic_write_json(p, {"b": 2, "a": 1}, indent=None)
    assert p.read_bytes() == json.dumps({"b": 2, "a": 1}).encode("utf-8")


def test_chmod_applied_to_final_file(tmp_path):
    p = tmp_path / "x.json"
    atomic_write_json(p, {"k": 1}, chmod=0o600)
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600


def test_no_tmp_left_behind(tmp_path):
    p = tmp_path / "x.json"
    atomic_write_json(p, {"k": 1})
    assert not (tmp_path / "x.json.tmp").exists()
    assert p.exists()


def test_fsync_false_still_writes(tmp_path):
    p = tmp_path / "x.json"
    atomic_write_json(p, {"k": 1}, fsync=False)
    assert json.loads(p.read_text()) == {"k": 1}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_atomicio.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'sonari.atomicio'`

- [ ] **Step 3: Implement the helper**

```python
# src/sonari/atomicio.py
"""One atomic JSON writer: temp file in the same dir + os.replace.

Parameterized to reproduce every current write site's behavior exactly
(indent, optional fsync, optional chmod). Streaming downloads and the
install-record writer are intentionally NOT consumers (see the Stage 3 spec).
"""
from __future__ import annotations

import json
import os


def atomic_write_json(path, data, *, indent=None, chmod=None, fsync=True) -> None:
    """Atomically write `data` as JSON to `path` via a sibling .tmp + os.replace."""
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=indent)
        fh.flush()
        if fsync:
            os.fsync(fh.fileno())
    if chmod is not None:
        os.chmod(tmp, chmod)
    os.replace(tmp, str(path))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_atomicio.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/sonari/atomicio.py tests/test_atomicio.py
git commit -m "feat(atomicio): atomic_write_json helper (indent/fsync/chmod params)"
```

---

### Task 2: Adopt `atomic_write_json` in config / keymap / transport

**Files:**
- Modify: `src/sonari/config.py:55-63` (`save_config`)
- Modify: `src/sonari/keymap.py:161-169` (`_write_user_keymap`), `:200-213` (`write_resolved`)
- Modify: `src/sonari/platform/transport.py:16-22` (`write_lockfile`)
- Test (existing, must stay green): `tests/test_config.py`, `tests/test_keymap.py`, `tests/test_transport.py` (run whichever exist)

**Interfaces:**
- Consumes: `atomic_write_json(path, data, *, indent=None, chmod=None, fsync=True)` from Task 1.

- [ ] **Step 1: Add a byte-equivalence guard test for the lockfile (the one with chmod)**

```python
# tests/test_transport.py — append
import os, stat, json
from sonari.platform.transport import write_lockfile

def test_write_lockfile_bytes_and_mode(tmp_path):
    p = tmp_path / "daemon.lock"
    write_lockfile(p, "127.0.0.1", 5051, "tok", 4242)
    assert json.loads(p.read_text()) == {
        "host": "127.0.0.1", "port": 5051, "token": "tok", "pid": 4242}
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    assert not p.read_bytes().endswith(b"\n")  # compact, no trailing newline
```

- [ ] **Step 2: Run it (green on current code — characterization)**

Run: `.venv/bin/python -m pytest tests/test_transport.py -q`
Expected: PASS (pins current behavior before the swap)

- [ ] **Step 3: Migrate `config.save_config`**

```python
# src/sonari/config.py — replace save_config body; keep ensure_sonari_dir() call
from sonari.atomicio import atomic_write_json  # add to imports

def save_config(cfg: dict) -> None:
    """Atomically persist cfg to CONFIG_PATH."""
    ensure_sonari_dir()
    atomic_write_json(CONFIG_PATH, cfg, indent=2)
```

- [ ] **Step 4: Migrate `keymap._write_user_keymap` and `keymap.write_resolved`**

```python
# src/sonari/keymap.py — add import, replace both bodies
from sonari.atomicio import atomic_write_json  # add to imports

def _write_user_keymap(user: dict) -> None:
    """Atomically persist the user's keymap.json overrides."""
    ensure_sonari_dir()
    atomic_write_json(KEYMAP_PATH, user, indent=2)


def write_resolved(keymap=None) -> str:
    """Atomically write the resolved array to HOTKEYD_RESOLVED_PATH; return its
    path. Uses load_keymap() when no explicit keymap is given."""
    if keymap is None:
        keymap = load_keymap()
    ensure_sonari_dir()
    atomic_write_json(HOTKEYD_RESOLVED_PATH, resolve_keymap(keymap), indent=None)
    return str(HOTKEYD_RESOLVED_PATH)
```

- [ ] **Step 5: Migrate `transport.write_lockfile`**

```python
# src/sonari/platform/transport.py — replace body
from sonari.atomicio import atomic_write_json  # add to imports

def write_lockfile(path, host, port, token, pid) -> None:
    data = {"host": host, "port": int(port), "token": token, "pid": int(pid)}
    atomic_write_json(path, data, indent=None, fsync=False, chmod=0o600)
```

- [ ] **Step 6: Run the affected suites + full gate**

Run: `.venv/bin/python -m pytest tests/test_config.py tests/test_keymap.py tests/test_transport.py -q`
Expected: PASS
Run: `.venv/bin/python -m pytest -q --ignore=tests/test_kokoro.py`
Expected: **752 passed**

- [ ] **Step 7: Commit**

```bash
git add src/sonari/config.py src/sonari/keymap.py src/sonari/platform/transport.py tests/test_transport.py
git commit -m "refactor: route config/keymap/transport writes through atomic_write_json"
```

---

### Task 3: `install_record.read_install_record` + adopt in the daemon

**Files:**
- Create: `src/sonari/install_record.py`
- Modify: `src/sonari/daemon/features/lifecycle.py:1-16` (drop the local copy + its `INSTALL_RECORD_PATH` import; import the shared reader)
- Modify: `tests/conftest.py` (extend `_isolate_sonari_dir` to rebind `install_record.INSTALL_RECORD_PATH`)
- Modify: `tests/test_daemon_setup_health.py` (repoint 7 `monkeypatch.setattr(lifecycle, "INSTALL_RECORD_PATH", …)` at lines 15,24,34,43,52,62,72 + the `lifecycle._read_install_record()` call at line 73)

**Interfaces:**
- Produces: `read_install_record() -> dict | None` (reads `paths.INSTALL_RECORD_PATH`; returns None on any exception). `write_install_record(...)` is added later in Task 8 (it has no daemon consumer).

- [ ] **Step 1: Create `install_record.py` with the reader (canonical body)**

```python
# src/sonari/install_record.py
"""The single install.json reader/writer (was duplicated cli + daemon lifecycle).

read_install_record() resolves the genuine cli<->lifecycle duplication.
write_install_record() is added in the install task (it has only the cli caller)
and is moved verbatim — a plain write, NOT routed through atomic_write_json, so
install.json's bytes are unchanged.
"""
from __future__ import annotations

import json

from sonari.paths import INSTALL_RECORD_PATH


def read_install_record():
    """Return the install.json dict, or None if unreadable/absent. Never raises."""
    try:
        with open(str(INSTALL_RECORD_PATH), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001 - install-record must never raise
        return None
```

- [ ] **Step 2: Adopt it in lifecycle; delete the local copy**

```python
# src/sonari/daemon/features/lifecycle.py — module top
from __future__ import annotations

from sonari.protocol import MsgType
from sonari.install_record import read_install_record
from sonari.daemon.registry import handler
# (REMOVE: `from sonari.paths import INSTALL_RECORD_PATH` and the local
#  `_read_install_record` def at lines 8-16)
```

Then replace the one internal call site (in `_setup_health`) `_read_install_record()` → `read_install_record()`.

- [ ] **Step 3: Extend conftest to rebind the new module's path constant**

```python
# tests/conftest.py — inside _isolate_sonari_dir, alongside the other by-value rebinds
    import sonari.install_record as install_record
    monkeypatch.setattr(
        install_record, "INSTALL_RECORD_PATH", sonari_dir / "install.json",
        raising=False)
```

- [ ] **Step 4: Repoint the 8 sites in `test_daemon_setup_health.py`**

At the top of the file add `import sonari.install_record as install_record`. Then:
- The 7 `monkeypatch.setattr(lifecycle, "INSTALL_RECORD_PATH", <val>)` (lines 15,24,34,43,52,62,72) → `monkeypatch.setattr(install_record, "INSTALL_RECORD_PATH", <val>)`.
- Line 73 `assert lifecycle._read_install_record() is None` → `assert install_record.read_install_record() is None`.

- [ ] **Step 5: Prove the fake fires + run the suite**

Run: `.venv/bin/python -m pytest tests/test_daemon_setup_health.py -q`
Expected: PASS — and confirm interception is real: line 73's test points `install_record.INSTALL_RECORD_PATH` at a corrupt/missing file and asserts `None`. To prove it's not reading the real file, temporarily point it at a path containing valid JSON and confirm the test would fail (then revert).
Run: `.venv/bin/python -m pytest -q --ignore=tests/test_kokoro.py`
Expected: **752 passed**

- [ ] **Step 6: Commit**

```bash
git add src/sonari/install_record.py src/sonari/daemon/features/lifecycle.py tests/conftest.py tests/test_daemon_setup_health.py
git commit -m "refactor: share read_install_record via sonari.install_record; daemon adopts it"
```

---

### Task 4: `platform/macos/_helpers.py` (xml_escape + build_swift_binary)

**Files:**
- Create: `src/sonari/platform/macos/_helpers.py`
- Modify: `src/sonari/platform/macos/hotkeys.py` (drop local `_xml_escape`; import shared; `build` delegates)
- Modify: `src/sonari/platform/macos/supervisor.py` (drop local `_xml_escape`; import shared)
- Modify: `src/sonari/platform/macos/raiser.py` (`build` delegates)
- Test (existing, must stay green): `tests/test_macos_raise.py` (line 149 pins "automation grant"), `tests/test_macos_hotkeys.py` (line 69 pins "unchanged"), any plist-escape test

**Interfaces:**
- Produces:
  - `xml_escape(s: str) -> str` — `s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")`
  - `build_swift_binary(src, out, hash_path, src_label, unchanged_note) -> (bool, str)` — verbatim build() body; OSError return `(False, "cannot read {src_label} source: {exc}")`; unchanged return `(True, "{out} (unchanged; kept to preserve {unchanged_note})")`.

- [ ] **Step 1: Write the failing tests for the shared helpers**

```python
# tests/test_macos_helpers.py
from sonari.platform.macos._helpers import xml_escape, build_swift_binary


def test_xml_escape_three_chars():
    assert xml_escape("a&b<c>d") == "a&amp;b&lt;c&gt;d"


def test_build_swift_missing_swiftc(monkeypatch, tmp_path):
    import sonari.platform.macos._helpers as h
    monkeypatch.setattr(h.shutil, "which", lambda _: None)
    ok, detail = build_swift_binary(
        str(tmp_path / "x.swift"), str(tmp_path / "out"),
        str(tmp_path / "h"), "hotkeyd", "any permission grants")
    assert ok is False and detail == "swiftc not found"


def test_build_swift_unreadable_source_uses_src_label(monkeypatch, tmp_path):
    import sonari.platform.macos._helpers as h
    monkeypatch.setattr(h.shutil, "which", lambda _: "/usr/bin/swiftc")
    ok, detail = build_swift_binary(
        str(tmp_path / "missing.swift"), str(tmp_path / "out"),
        str(tmp_path / "h"), "sonari-raise", "the Automation grant")
    assert ok is False
    assert "cannot read sonari-raise source" in detail
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_macos_helpers.py -q`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement `_helpers.py`**

```python
# src/sonari/platform/macos/_helpers.py
"""Shared macOS plist-escape + Swift-compile (were duplicated across the macOS backends)."""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess

from sonari import paths


def xml_escape(s: str) -> str:
    """Escape the three XML-significant characters for safe plist interpolation."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_swift_binary(src, out, hash_path, src_label, unchanged_note):
    """Compile `src` -> `out` with swiftc if present and the source changed.
    Skips recompile (preserving any OS permission grant) when the source hash
    is unchanged. Returns (ok: bool, detail: str)."""
    if shutil.which("swiftc") is None:
        return (False, "swiftc not found")
    try:
        with open(src, "rb") as fh:
            src_hash = hashlib.sha256(fh.read()).hexdigest()
    except OSError as exc:
        return (False, "cannot read {0} source: {1}".format(src_label, exc))
    if os.path.exists(str(out)):
        try:
            with open(hash_path, "r", encoding="utf-8") as fh:
                if fh.read().strip() == src_hash:
                    return (True, "{0} (unchanged; kept to preserve {1})".format(
                        out, unchanged_note))
        except OSError:
            pass
    rc = subprocess.call(["swiftc", src, "-o", str(out)])
    if rc == 0:
        try:
            with open(hash_path, "w", encoding="utf-8") as fh:
                fh.write(src_hash)
        except OSError:
            pass
        return (True, str(out))
    return (False, "swiftc exited {0}".format(rc))
```

- [ ] **Step 4: Delegate from `hotkeys.py`**

Replace the local `_xml_escape` (lines 57-59) — delete it and add `from sonari.platform.macos._helpers import xml_escape, build_swift_binary` at module top; replace the two `_xml_escape(` call sites in `_hotkeyd_plist` with `xml_escape(`. Replace the `build` method body (lines 155-183) with the delegation (preserving the exact strings):

```python
    def build(self):
        src = os.path.join(paths.repo_root(), "hotkeyd", "sonari-hotkeyd.swift")
        hash_path = str(paths.SONARI_DIR / ".hotkeyd.srchash")
        return build_swift_binary(
            src, paths.HOTKEYD_BIN_PATH, hash_path,
            "hotkeyd", "any permission grants")
```

- [ ] **Step 5: Delegate from `supervisor.py` and `raiser.py`**

`supervisor.py`: delete local `_xml_escape` (lines 37-39); add `from sonari.platform.macos._helpers import xml_escape`; replace its `_xml_escape(` call sites with `xml_escape(`.

`raiser.py`: add `from sonari.platform.macos._helpers import build_swift_binary`; replace the `build` body (lines 63-89) with:

```python
    def build(self):
        src = os.path.join(paths.repo_root(), "hotkeyd", "sonari-raise.swift")
        hash_path = str(paths.SONARI_DIR / ".raise.srchash")
        return build_swift_binary(
            src, paths.RAISE_BIN_PATH, hash_path,
            "sonari-raise", "the Automation grant")
```

- [ ] **Step 6: Run helper tests + macOS backend tests + full gate**

Run: `.venv/bin/python -m pytest tests/test_macos_helpers.py tests/test_macos_raise.py tests/test_macos_hotkeys.py tests/test_macos_supervisor.py -q`
Expected: PASS — `test_macos_raise.py:149` ("automation grant") and `test_macos_hotkeys.py:69` ("unchanged") still green (the behavior-preservation gate).
Run: `.venv/bin/python -m pytest -q --ignore=tests/test_kokoro.py`
Expected: **752 passed**

- [ ] **Step 7: Commit**

```bash
git add src/sonari/platform/macos/_helpers.py src/sonari/platform/macos/hotkeys.py src/sonari/platform/macos/supervisor.py src/sonari/platform/macos/raiser.py tests/test_macos_helpers.py
git commit -m "refactor(macos): share xml_escape + build_swift_binary via _helpers"
```

---

### Task 5: `cli/` package skeleton (verbatim conversion + `__main__`)

**Files:**
- Convert: `src/sonari/cli.py` → `src/sonari/cli/__init__.py` (CONTENTS UNCHANGED — pure file move)
- Create: `src/sonari/cli/__main__.py`
- Test (existing, must stay green): all 7 `tests/test_cli_*.py`

**Interfaces:**
- Produces: package `sonari.cli` exporting `main` (and everything cli.py exported, unchanged); `python -m sonari.cli` works via `__main__`.

- [ ] **Step 1: Move the file into a package (no content change)**

```bash
mkdir -p src/sonari/cli
git mv src/sonari/cli.py src/sonari/cli/__init__.py
```

Remove the `if __name__ == "__main__":\n    sys.exit(main())` block from `__init__.py` (it moves to `__main__.py`).

- [ ] **Step 2: Create `__main__.py`**

```python
# src/sonari/cli/__main__.py
"""Module-execution entry: `python -m sonari.cli` (used by bin/sonari)."""
import sys

from sonari.cli import main

if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 3: Verify both entry forms resolve**

Run: `.venv/bin/python -m sonari.cli --help`
Expected: prints the CLI help (the `-m` form works against the package).
Run: `.venv/bin/python -c "from sonari.cli import main; print(callable(main))"`
Expected: `True` (the `sonari.cli:main` console-script target resolves).

- [ ] **Step 4: Run the full cli suite + gate**

Run: `.venv/bin/python -m pytest tests/test_cli_install.py tests/test_cli_doctor.py tests/test_cli_control.py tests/test_cli_voices.py tests/test_cli_uninstall.py tests/test_cli_focus_follow.py tests/test_cli_hotkeyd.py -q`
Expected: PASS (no test changes — `import sonari.cli` and `cli.<attr>` resolve identically against the package).
Run: `.venv/bin/python -m pytest -q --ignore=tests/test_kokoro.py`
Expected: **752 passed**

- [ ] **Step 5: Commit**

```bash
git add src/sonari/cli/__init__.py src/sonari/cli/__main__.py
git commit -m "refactor(cli): convert cli.py to a package + __main__ (verbatim; -m entry preserved)"
```

---

### Task 6: Extract `cli/control.py` (thin senders + keymap)

**Files:**
- Create: `src/sonari/cli/control.py`
- Modify: `src/sonari/cli/__init__.py` (move the functions out; import them for dispatch wiring)
- Test (existing, must stay green): `tests/test_cli_control.py`, `tests/test_cli_hotkeyd.py`

**Interfaces:**
- Consumes (function-local, per Global Constraints): `from sonari.cli import _send, _platform`.
- Produces: `_cmd_status/_cmd_verbosity/_cmd_rate/_cmd_minqueue/_cmd_voice/_cmd_stop/_cmd_skip`, `_combo_label`, `_cmd_keymap` — all in `sonari.cli.control`.

- [ ] **Step 1: Create `control.py` with the verbatim function bodies**

Move `_cmd_status` (59-66), `_cmd_verbosity` (69-72), `_cmd_rate` (75-77), `_cmd_minqueue` (80-83), `_cmd_voice` (86-105), `_cmd_stop` (108-110), `_cmd_skip` (113-115), `_combo_label` (118-119), `_cmd_keymap` (122-152) into `control.py` UNCHANGED, with module top:

```python
# src/sonari/cli/control.py
"""Control subcommands: build a protocol message and hand it to the daemon."""
from __future__ import annotations

from sonari.protocol import MsgType
from sonari import keymap

# _send / _platform live in sonari.cli; import them INSIDE the functions that
# use them (function-local) so monkeypatch.setattr(cli, "_platform", ...) still
# intercepts (Stage 3 spec §7 rule 8) and to avoid an import cycle.
```

Inside `_cmd_voice` (and any other sender that uses `_platform`/`_send`), add a function-local `from sonari.cli import _platform` / `from sonari.cli import _send` at the top of the body. `_cmd_keymap` keeps its `keymap` usage; it sends via `_send` (function-local import).

- [ ] **Step 2: Wire dispatch from `__init__.py`**

In `cli/__init__.py`, delete the moved bodies. Add `from . import control` near the bottom (after `_send`/`_platform` are defined, to keep the import non-circular at load). In `_build_parser`/`_register_local`, change the `set_defaults(func=_cmd_status)` (etc.) references to `control._cmd_status`, …, `control._cmd_keymap`.

- [ ] **Step 3: Run control + hotkeyd suites**

Run: `.venv/bin/python -m pytest tests/test_cli_control.py tests/test_cli_hotkeyd.py -q`
Expected: PASS. These patch `sonari.client.send` (stable) and `cli.paths.*` — both unaffected by the move. Confirm `_cmd_voice`'s `_platform` use still intercepts: `test_cli_control.py`'s voice test patches `_platform` (or `client.send`); prove it fires (assert the fake's recorded call).

- [ ] **Step 4: Full gate**

Run: `.venv/bin/python -m pytest -q --ignore=tests/test_kokoro.py`
Expected: **752 passed**

- [ ] **Step 5: Commit**

```bash
git add src/sonari/cli/__init__.py src/sonari/cli/control.py tests/test_cli_control.py
git commit -m "refactor(cli): extract control senders + keymap into cli/control.py"
```

---

### Task 7: Extract `cli/doctor.py`

**Files:**
- Create: `src/sonari/cli/doctor.py`
- Modify: `src/sonari/cli/__init__.py` (move `doctor`/`_cmd_doctor` out; dispatch wiring)
- Modify: `tests/test_cli_doctor.py` (repoint the `cli._read_install_record` patch at line 21 → `sonari.install_record.read_install_record`)

**Interfaces:**
- Consumes: `install_record.read_install_record` (module-qualified call); `kokoro_provision` (local import, kept); `from sonari.cli import _resolve_python, _platform` (function-local).
- Produces: `doctor() -> list[(check, ok, detail)]`, `_cmd_doctor(args) -> int` in `sonari.cli.doctor`.

- [ ] **Step 1: Create `doctor.py` with verbatim bodies, rerouted to the shared reader**

Move `doctor` (190-264) and `_cmd_doctor` (267-274) into `doctor.py` UNCHANGED except: replace the `_read_install_record()` call with `install_record.read_install_record()` (module-qualified). Module top:

```python
# src/sonari/cli/doctor.py
"""Read-only health diagnostics (`sonari doctor`)."""
from __future__ import annotations

import os

from sonari import paths
from sonari import install_record
# `from sonari import kokoro_provision as kp` stays a LOCAL import inside doctor().
# `_resolve_python` / `_platform` are imported function-locally from sonari.cli
# (Stage 3 spec §7 rule 8).
```

In `doctor()`'s body, keep the existing local `from sonari import kokoro_provision as kp`; add a function-local `from sonari.cli import _resolve_python` and use `_resolve_python()` and (where used) `from sonari.cli import _platform`.

- [ ] **Step 2: Wire dispatch from `__init__.py`**

Delete the moved bodies from `__init__.py`. **Also delete the now-dead `_read_install_record` (315-322)** — `doctor()` was its only caller and now uses `install_record.read_install_record()`, so this removes the temporary cli↔install_record reader duplication that existed since Task 3. Add `from . import doctor as doctor_cmd` (alias to avoid the `doctor` function/module ambiguity) near the bottom; change `set_defaults(func=_cmd_doctor)` → `doctor_cmd._cmd_doctor`.

- [ ] **Step 3: Repoint the doctor test patch (prove the fake fires)**

In `tests/test_cli_doctor.py`, the `_patches` fixture (line 21) patches `cli._read_install_record`. The reader now lives in `install_record` and `doctor()` calls `install_record.read_install_record()`, so repoint:

```python
# was: mock.patch.object(cli, "_read_install_record", return_value=...)
mock.patch("sonari.install_record.read_install_record",
           return_value=install_record_value or {"app_path": "/home/u/.sonari/app"}),
```

To prove interception: one doctor test already asserts a row derived from the record dict; set `return_value={"app_path": "/SENTINEL"}` and assert the sentinel reaches the doctor output (so a hollow patch — real reader returning None — would fail).

- [ ] **Step 4: Run doctor + focus-follow (also a doctor-rows test) + gate**

Run: `.venv/bin/python -m pytest tests/test_cli_doctor.py tests/test_cli_focus_follow.py -q`
Expected: PASS. `_platform` patches keep working unchanged (reached function-locally).
Run: `.venv/bin/python -m pytest -q --ignore=tests/test_kokoro.py`
Expected: **752 passed**

- [ ] **Step 5: Commit**

```bash
git add src/sonari/cli/__init__.py src/sonari/cli/doctor.py tests/test_cli_doctor.py
git commit -m "refactor(cli): extract doctor into cli/doctor.py; route reader through install_record"
```

---

### Task 8: Extract `cli/install.py` (the destructive unit — LAST)

**Files:**
- Create: `src/sonari/cli/install.py`
- Modify: `src/sonari/install_record.py` (ADD `write_install_record`, moved verbatim from `cli._write_install_record`)
- Modify: `src/sonari/cli/__init__.py` (move install/uninstall/voices + helpers out; dispatch wiring; keep `_resolve_python`, `_cmd_daemon`)
- Modify: `tests/test_cli_install.py`, `tests/test_cli_voices.py`, `tests/test_cli_uninstall.py` (repoint moved-symbol patches; harden lambda patches)

**Interfaces:**
- Consumes: `install_record.write_install_record(python, python_version, plugin_root, app_path, plugin_version)`; `from sonari.cli import _platform, _build_raise_helper` (function-local for `_platform`); `paths`, `platform`, `keymap`, `kokoro_provision`.
- Produces: `install()`, `_cmd_install`, `uninstall()`, `_cmd_uninstall`, `_cmd_voices_install`, `_cmd_voices_uninstall`, `_daemon_python`, `_read_plugin_version`, `_copy_app`, `_build_raise_helper` in `sonari.cli.install`.

- [ ] **Step 1: Add `write_install_record` (verbatim) to `install_record.py`**

```python
# src/sonari/install_record.py — append (keep the plain write; NOT atomic_write_json)
import os


def write_install_record(python, python_version, plugin_root, app_path,
                         plugin_version) -> None:
    """Persist the durable install record used by doctor + session-start health.
    Moved verbatim from cli._write_install_record — plain write + trailing
    newline, no fsync, no atomic rename (byte-identical to today)."""
    from datetime import datetime, timezone
    record = {
        "python": python,
        "python_version": python_version,
        "app_path": app_path,
        "plugin_root": plugin_root,
        "plugin_version": plugin_version,
        "installed_at": datetime.now(timezone.utc).isoformat(),
    }
    os.makedirs(os.path.dirname(str(INSTALL_RECORD_PATH)), exist_ok=True)
    with open(str(INSTALL_RECORD_PATH), "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2)
        f.write("\n")
```

- [ ] **Step 2: Add a byte-identity guard test for the writer**

```python
# tests/test_install_record.py
import json
import sonari.install_record as install_record


def test_write_install_record_trailing_newline(tmp_path, monkeypatch):
    p = tmp_path / "install.json"
    monkeypatch.setattr(install_record, "INSTALL_RECORD_PATH", p)
    install_record.write_install_record("/py", "3.11", "/plugin", "/app", "0.5.0")
    raw = p.read_bytes()
    assert raw.endswith(b"\n")                    # the verbatim trailing newline
    rec = json.loads(raw)
    assert rec["python"] == "/py" and rec["plugin_version"] == "0.5.0"
    assert "installed_at" in rec
```

Run: `.venv/bin/python -m pytest tests/test_install_record.py -q` → PASS.

- [ ] **Step 3: Create `install.py` with the verbatim bodies, rerouted to the shared writer**

Move `install` (361-427), `_cmd_install` (430-431), `uninstall` (434-482), `_cmd_uninstall` (485-486), `_cmd_voices_install` (489-505), `_cmd_voices_uninstall` (508-514), `_daemon_python` (282-293), `_read_plugin_version` (325-340), `_copy_app` (343-358) into `install.py` UNCHANGED except: replace the `_write_install_record(...)` call inside `install()` with `install_record.write_install_record(...)`. Module top:

```python
# src/sonari/cli/install.py
"""Install / uninstall / neural-voice lifecycle — the file-mutating, highest-care unit."""
from __future__ import annotations

import os
import shutil

from sonari import paths
from sonari import keymap
from sonari import install_record
# `_platform` / `_build_raise_helper` are reached function-locally from sonari.cli
# where needed; `kokoro_provision` is imported locally inside the voices handlers
# (matching the current deferred-import style).
```

Inside `install()`/`uninstall()`/voices handlers, add function-local `from sonari.cli import _platform` (and `_build_raise_helper` if used). `_resolve_python` does NOT move (stays in `__init__`; only doctor uses it). `_cmd_daemon` does NOT move (stays in `__init__`).

- [ ] **Step 4: Wire dispatch from `__init__.py`; delete `_write_install_record`**

In `cli/__init__.py`: delete the moved bodies AND the now-unused `_write_install_record` (315→ its body is in `install_record` now; the reader `_read_install_record` was already removed in Task 7's reroute — confirm no remaining `__init__` caller). Add `from . import install as install_cmd` near the bottom; change the `_register_local` `set_defaults(func=...)` for install/uninstall/daemon/voices to `install_cmd.install`-backed handlers (`install_cmd._cmd_install`, `install_cmd._cmd_uninstall`, `install_cmd._cmd_voices_install`, `install_cmd._cmd_voices_uninstall`) and keep `func=_cmd_daemon` (still local to `__init__`).

- [ ] **Step 5: Repoint + harden the install/voices/uninstall test patches**

`tests/test_cli_install.py`:
- Lines 26-27 (lambda patches) — repoint to the install module AND harden to Mocks:
```python
copy_mock = mock.Mock(return_value=str(tmp_path / "app"))
write_mock = mock.Mock()
monkeypatch.setattr("sonari.cli.install._copy_app", copy_mock)
# the writer now lives in install_record (install() calls
# install_record.write_install_record), so patch it THERE, not on cli:
monkeypatch.setattr("sonari.install_record.write_install_record", write_mock)
# ... after asserting rc:
copy_mock.assert_called_once()
write_mock.assert_called_once()
```
  (The writer now lives in `install_record`; patch `sonari.install_record.write_install_record`. `_copy_app` lives in `sonari.cli.install`.) Apply the same Mock+assert hardening to the same lambda pattern in `test_install_macos_stdout_locks_hotkeyd_and_speechd_lines` (≈196-197) and `test_install_uses_venv_interpreter_when_neural_enabled` (≈249-250).
- Line 60 (`_copy_app` Mock side_effect) → `monkeypatch.setattr("sonari.cli.install._copy_app", mock.Mock(side_effect=OSError("read-only")))`.
- Line 80 (`mock.patch.object(cli.paths, "INSTALL_RECORD_PATH", rec)`) → patch `sonari.install_record.INSTALL_RECORD_PATH` (where the writer reads it). Add `assert rec.read_bytes().endswith(b"\n")` to pin the byte format.
- Any `cli._platform` patch stays as-is (function-local; still intercepts).

`tests/test_cli_voices.py`:
- Lines 14 & 28 `monkeypatch.setattr(cli, "install", …)` → `monkeypatch.setattr("sonari.cli.install.install", …)` (the function in the install module; the voices handlers call bare `install()` resolved in their own module). Keep/strengthen the order-list/`assert_called` checks.

`tests/test_cli_uninstall.py`:
- Line 26 `cli._platform` patch stays (function-local). If it asserts on `rmtree`/`os.remove` doubles, ensure those doubles are passed via `_platform`'s fake backends (unchanged).

- [ ] **Step 6: Prove the destructive fakes fire (anti-hollow gate)**

Run: `.venv/bin/python -m pytest tests/test_cli_install.py tests/test_cli_voices.py tests/test_cli_uninstall.py -q`
Expected: PASS — and `copy_mock.assert_called_once()` / `write_mock.assert_called_once()` confirm the real `copytree`/`rmtree`/writer did NOT run. If any assert_called fails, the patch target is wrong (hollow) — fix before proceeding.

- [ ] **Step 7: Full gate + subprocess sacrificial-HOME doctor smoke**

Run: `.venv/bin/python -m pytest -q --ignore=tests/test_kokoro.py`
Expected: **752 passed**
Run (read-only smoke; NEVER a live install):
```bash
HOME="$(mktemp -d)" .venv/bin/python -m sonari.cli doctor; echo "exit=$?"
```
Expected: prints doctor rows against the sacrificial HOME and exits without touching the real `~/.sonari` (exit 0 or 1 by health, no traceback).

- [ ] **Step 8: Commit**

```bash
git add src/sonari/cli/__init__.py src/sonari/cli/install.py src/sonari/install_record.py tests/test_install_record.py tests/test_cli_install.py tests/test_cli_voices.py tests/test_cli_uninstall.py
git commit -m "refactor(cli): isolate install/uninstall/voices into cli/install.py; writer via install_record"
```

---

### Task 9: Tidy `cli/__init__.py` to its floor

**Files:**
- Modify: `src/sonari/cli/__init__.py`
- Test (existing, must stay green): all `tests/test_cli_*.py`

**Interfaces:**
- Produces: a thin `cli/__init__.py` = `main`, `_build_parser`, `_register_local`, dispatch, `_cmd_daemon`, shared plumbing (`_send`, `_platform`, `_daemon_not_running_message`, `_resolve_python`, `_PLATFORM`), `from . import control, doctor as doctor_cmd, install as install_cmd`, and `from sonari import paths, keymap` (so `cli.paths`/`cli.keymap` still resolve for tests).

- [ ] **Step 1: Remove dead forwarders / unused imports**

Confirm `__init__.py` no longer defines any moved function. Remove imports only used by moved code (e.g. `shutil` if no longer used in `__init__`; keep `argparse`, `json`, `os`, `sys`, `Optional`, `MsgType`/`PROTOCOL_VERSION`, `paths`, `keymap`, `get_platform` as still referenced). Keep `_cmd_daemon` and the shared plumbing. Do NOT remove `from . import paths`/`keymap` — tests reference `cli.paths`/`cli.keymap`.

- [ ] **Step 2: Verify entry points still resolve**

Run: `.venv/bin/python -m sonari.cli --help` → prints help.
Run: `.venv/bin/python -c "from sonari.cli import main; main(['--help'])"` (expect SystemExit 0 from argparse help) — confirms the console-script target.

- [ ] **Step 3: Full cli suite + gate**

Run: `.venv/bin/python -m pytest tests/test_cli_install.py tests/test_cli_doctor.py tests/test_cli_control.py tests/test_cli_voices.py tests/test_cli_uninstall.py tests/test_cli_focus_follow.py tests/test_cli_hotkeyd.py -q`
Expected: PASS
Run: `.venv/bin/python -m pytest -q --ignore=tests/test_kokoro.py`
Expected: **752 passed**

- [ ] **Step 4: Confirm the line-count goal (DoD)**

Run: `wc -l src/sonari/cli/*.py src/sonari/install_record.py src/sonari/atomicio.py src/sonari/platform/macos/_helpers.py`
Expected: no new file exceeds ~350 lines. If `cli/install.py` is materially above, STOP and surface to the owner (explicit decision, not a silent pass).

- [ ] **Step 5: Commit**

```bash
git add src/sonari/cli/__init__.py
git commit -m "refactor(cli): tidy __init__ to dispatch core + shared plumbing"
```

---

## Final whole-branch review

After Task 9, dispatch the final code review (superpowers:requesting-code-review on the most capable model) over `git merge-base main HEAD`..HEAD. Verify: behavior byte-identical (no diff to speech/earcon/file outputs), every moved-symbol patch proven non-hollow (assert_called / sentinels present), `python -m sonari.cli` + `sonari.cli:main` + `bin/sonari` all work, suite 752 green, the subprocess sacrificial-HOME doctor smoke clean, no `claude.ai/code/session` strings in commits, and `.convergence-plan.md`/`docs/getting-started.md` not staged. Then use superpowers:finishing-a-development-branch — merge to **local** `main` only on the owner's call; do not push.

## Self-review notes (plan author)

- **Spec coverage:** §5 dups → Tasks 1-4 + 8 (writer); §4 package → Tasks 5-9; §7 rules 1/2/8 → repoint+harden steps in Tasks 3/6/7/8; §9 sequence → Task order 1-9; §8 byte-identity → guard tests in Tasks 1/2/8 + macOS message gates in Task 4.
- **Signature consistency:** `write_install_record(python, python_version, plugin_root, app_path, plugin_version)` used identically in Task 8 def + call + test (supersedes the spec's shorthand `write_install_record(record)`). `build_swift_binary(src, out, hash_path, src_label, unchanged_note)` identical in Task 4 def + both call sites + tests. `atomic_write_json(path, data, *, indent, chmod, fsync)` identical across Tasks 1-2.
- **Known subtlety (carried in-plan):** `cli.install`/`cli.doctor` — the submodule vs re-exported-function name collision is sidestepped by importing submodules under aliases (`install_cmd`, `doctor_cmd`) and using string-based `mock.patch("sonari.cli.install.install")` repoints.
