# Remove Windows Support — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Sonari macOS-only by removing the Windows backend, branches, tests, docs, and packaging claims — with the smallest safe diff and no architecture change.

**Architecture:** Behavior-preserving removal. The macOS test suite is the safety net; every commit keeps it green. The platform abstraction (`base.py` ABCs, `get_platform()`, `macos/`) and all consumer call sites are **kept untouched** — collapsing them is a separate Step 2 refactor. The only delicate part is **commit ordering**: delete things that *import* Windows code before deleting the Windows code itself, so no commit leaves a dangling import.

**Tech Stack:** Python ≥3.9, pytest. macOS-only runtime (`say`, `afplay`, `launchctl`, a Swift `hotkeyd`/`raise` helper).

**Spec:** `docs/superpowers/specs/2026-06-20-remove-windows-support-design.md`

## Global Constraints

- **Keep the seam.** Do NOT touch `base.py`, `macos/**`, `get_platform()` (beyond removing the `win32` arm), or any consumer call site (`cli.py`, `daemon.py`, `keymap.py`, `client.py`, `paths.py`). Collapsing the abstraction is Step 2.
- **macOS suite stays green at every commit.** Run `python -m pytest -q` before committing each task; it must pass.
- **No version bump** — `version` stays `0.5.0` in all files. **No CHANGELOG.**
- **Branch `core/remove-windows`** (already created and checked out). All commits land here. **Never push to `main`; PR only.**
- **Commit messages must NOT contain any `claude.ai/code/session...` link** (per repo preference). Plain Conventional-Commits subjects only.
- The venv for running tests: `python -m venv .venv && .venv/bin/pip install -e ".[dev]"`; run tests with `.venv/bin/python -m pytest -q`. (Use whatever invocation the repo's existing green run uses.)

---

## Task 1: Establish green baseline + safety tag

**Files:** none modified (tag + measurement only).

- [ ] **Step 1: Run the full suite and record the baseline**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS. **Record the passed-count** (e.g. "266 passed") — this is the origin "green" that later tasks compare against (each removal task will pass *fewer* tests, never fail any).

- [ ] **Step 2: Confirm Windows source is intact and unimported by mac code**

Run: `grep -rni "windows" src/sonari/platform/macos/`
Expected: only **comment** lines in `macos/tts.py` (the `_TMP_PREFIX` dedup note). **Zero `import` lines.** (Confirms the wholesale `windows/` delete cannot break the mac path.)

Run: `grep -rnE "platform\.windows|from \.windows" src/sonari --include="*.py" | grep -v "/platform/windows/"`
Expected: exactly one hit — `src/sonari/platform/__init__.py:` the `win32` elif. (Confirms the subpackage's only production reference is the dispatch arm.)

- [ ] **Step 3: Create the pre-removal safety tag**

```bash
git tag pre-windows-removal
git tag --list pre-windows-removal
```
Expected: the tag prints. (Lets `git checkout pre-windows-removal -- src/sonari/platform/windows` recover the full backend later if ever wanted.)

---

## Task 2: Delete the Windows-only test files

Deleting tests first means later source deletions have no test importing them. These files all pass today (via the `_winfakes` shim) and removing them cannot break anything else.

**Files — delete all of:**
- `tests/test_win_autostart.py`
- `tests/test_win_backend.py`
- `tests/test_win_doctor_rows.py`
- `tests/test_win_earcon.py`
- `tests/test_win_earcons_assets.py`
- `tests/test_win_hooks.py`
- `tests/test_win_hotkeys.py`
- `tests/test_win_keytables.py`
- `tests/test_win_settings_hooks.py`
- `tests/test_win_supervisor.py`
- `tests/test_win_tts.py`
- `tests/test_win_tts_kokoro.py`
- `tests/test_earcon_generator.py` (imports `sonari.platform.windows.earcons.generate`; macOS earcons are covered by `test_macos_earcon.py`)

**Keep for now:** `tests/_winfakes.py`, `tests/test_winfakes.py`, and `tests/conftest.py`'s `_winfakes` bootstrap — they are self-consistent and the Windows *source* still imports cleanly under them. They are removed in Task 5.

- [ ] **Step 1: Delete the files**

```bash
git rm tests/test_win_autostart.py tests/test_win_backend.py \
  tests/test_win_doctor_rows.py tests/test_win_earcon.py \
  tests/test_win_earcons_assets.py tests/test_win_hooks.py \
  tests/test_win_hotkeys.py tests/test_win_keytables.py \
  tests/test_win_settings_hooks.py tests/test_win_supervisor.py \
  tests/test_win_tts.py tests/test_win_tts_kokoro.py \
  tests/test_earcon_generator.py
```

- [ ] **Step 2: Run the suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS, with a *lower* count than the baseline. Zero failures, zero collection errors.

- [ ] **Step 3: Commit**

```bash
git commit -m "test(remove-windows): delete Windows-only test files"
```

---

## Task 3: Remove Windows cases from shared test files (surgery)

Each edit removes only the Windows-specific part; the macOS/portable assertions in each file stay. The Windows *source* still exists at this point, so on macOS every collapsed/kept assertion still reflects real behavior.

**Files — modify (remove only the named items):**
- `tests/test_keymap.py` — remove the module-level `win` fixture and these 5 tests: `test_windows_keytables_via_backend`, `test_default_keymap_windows_uses_ctrl_shift_alt`, `test_windows_default_bindings_are_collision_free`, `test_response_nav_resolves_on_windows`, `test_resolve_windows_vk_codes`. Keep the `mac` fixture and all macOS tests.
- `tests/test_transport.py` — remove the test `test_acquire_singleton_windows_branch` (it monkeypatches `sys.platform = "win32"` and relies on the `msvcrt` fake).
- `tests/test_kokoro_provision.py` — remove the test `test_ensure_uv_windows_uses_scripts_uv_exe`; for any test that asserts both `Scripts/python.exe` (win) and `bin/python` (posix) via a `sys.platform` branch, delete the win arm so only the `bin/python` assertion remains.
- `tests/test_macos_tts_kokoro.py` — remove the test `test_tmp_prefix_matches_windows_for_cross_sweep` (it imports `sonari.platform.windows.tts`).
- `tests/test_bin_shims.py` — remove the `.cmd` test (`test_sonari_hook_cmd_resolves_interpreter_and_logs_stderr`) and, in `test_sonari_prefers_usr_bin_python3_first`, remove the assertion that checks the `OS`/`Windows_NT` guard line. Keep the `/usr/bin/python3`-preference assertions.
- `tests/test_cli_install.py` — cosmetic: replace the fake python string `/PY/pythonw.exe` with a neutral path like `/PY/python3`; delete the comment referencing the Windows M3 hotkey note. (No structural change.)

- [ ] **Step 1: Locate each item before editing**

Run, to anchor the exact lines:
```bash
grep -nE "def (test_windows|test_default_keymap_windows|test_response_nav_resolves_on_windows|test_resolve_windows_vk_codes|test_windows_default_bindings)|^def win|win = |_force\(.*win32" tests/test_keymap.py
grep -n "test_acquire_singleton_windows_branch" tests/test_transport.py
grep -nE "test_ensure_uv_windows_uses_scripts_uv_exe|Scripts|win32|pythonw|python\.exe" tests/test_kokoro_provision.py
grep -n "test_tmp_prefix_matches_windows_for_cross_sweep" tests/test_macos_tts_kokoro.py
grep -nE "cmd|Windows_NT|pythonw" tests/test_bin_shims.py
grep -nE "pythonw\.exe|Windows|M3" tests/test_cli_install.py
```

- [ ] **Step 2: Make the edits above**

Remove each named fixture/test function in full (signature through its last line). For `test_kokoro_provision.py`, collapse any `if sys.platform == "win32": assert ...Scripts... else: assert ...bin/python...` to just the `bin/python` assertion. For `test_cli_install.py`, swap the `pythonw.exe` string. Do not touch anything else in these files.

- [ ] **Step 3: Run the suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS, count lower than Task 2. Zero failures.

- [ ] **Step 4: Commit**

```bash
git commit -am "test(remove-windows): drop Windows cases from shared tests"
```

---

## Task 4: Collapse the 4 Windows source branches to their POSIX arm

After Tasks 2–3, no test asserts any Windows branch, so simplifying the source breaks nothing. Keep the `darwin`-else-raise guard in `get_platform()`.

**Files — modify:**
- `src/sonari/platform/__init__.py`
- `src/sonari/platform/transport.py`
- `src/sonari/paths.py`
- `src/sonari/kokoro_provision.py`

- [ ] **Step 1: `platform/__init__.py` — drop the `win32` dispatch arm**

Remove these two lines from `get_platform()`:
```python
    elif sys.platform == "win32":
        from sonari.platform.windows import make_backend
```
Result: `if sys.platform == "darwin": from sonari.platform.macos import make_backend` followed directly by `else: raise RuntimeError("Unsupported platform: {0}".format(sys.platform))`.

- [ ] **Step 2: `transport.py` — drop the `msvcrt` arm in `acquire_singleton()`**

Replace the `if sys.platform == "win32": ... else: <fcntl block>` with the unconditional `fcntl` body:
```python
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)
    fh = os.fdopen(fd, "r+")
    import fcntl
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    try:
        fh.seek(0); fh.write(str(os.getpid())); fh.flush(); fh.truncate()
    except OSError:
        pass
    return fh
```
Also delete the Windows narration from the docstring (the `msvcrt` / "byte-range" / named-mutex / `M2-WINDOWS-ACCEPTANCE.md` sentences), leaving the POSIX description.

- [ ] **Step 3: `paths.py` — make `kokoro_venv_python()` POSIX-only**

Replace the body with:
```python
def kokoro_venv_python() -> str:
    """Absolute path to the neural venv's Python interpreter (may not exist)."""
    return str(KOKORO_VENV / "bin" / "python")
```
(Drops the `import sys` and the `if sys.platform == "win32": return .../Scripts/python.exe` line.)

- [ ] **Step 4: `kokoro_provision.py` — make `ensure_uv()` POSIX-only**

Replace `exe = "uv.exe" if sys.platform == "win32" else "uv"` with:
```python
    exe = "uv"
```

- [ ] **Step 5: Run the suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS, same count as Task 3 (these are source-only simplifications; the mac behavior is unchanged).

- [ ] **Step 6: Commit**

```bash
git commit -am "refactor(remove-windows): collapse sys.platform branches to the macOS/POSIX arm"
```

---

## Task 5: Delete the Windows subpackage + the `_winfakes` shim

Now nothing imports the Windows source or the shim. Remove them together so no commit leaves a dangling `_winfakes` import.

**Files — delete:**
- `src/sonari/platform/windows/` (entire directory: `__init__.py`, `tts.py`, `earcon.py`, `hotkeys.py`, `keytables.py`, `supervisor.py`, `supervisor_loop.py`, `earcons/__init__.py`, `earcons/generate.py`, and the 7 `.wav` files)
- `tests/_winfakes.py`
- `tests/test_winfakes.py`

**Files — modify:**
- `tests/conftest.py` — remove the ~4-line block that imports `tests._winfakes` and calls `install()` at collection time. Keep the rest of conftest (the `_isolate_sonari_dir` autouse fixture).

- [ ] **Step 1: Delete the subpackage and shim**

```bash
git rm -r src/sonari/platform/windows
git rm tests/_winfakes.py tests/test_winfakes.py
```

- [ ] **Step 2: Remove the `_winfakes` bootstrap from conftest**

Run `grep -n "_winfakes" tests/conftest.py` to find the block, then delete the import + `install()` call lines (the map placed them around lines 12–15). Leave everything else.

- [ ] **Step 3: Confirm nothing still references the deleted code**

Run: `grep -rnE "_winfakes|platform\.windows|from \.windows" src tests --include="*.py"`
Expected: **no output.**

- [ ] **Step 4: Run the suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS, count lower than Task 4 (the 2 winfakes tests are gone). Zero collection errors.

- [ ] **Step 5: Commit**

```bash
git commit -m "refactor(remove-windows): delete the Windows backend subpackage and _winfakes shim"
```

---

## Task 6: Remove Windows from packaging (`pyproject.toml`)

**Files — modify:** `pyproject.toml`

- [ ] **Step 1: Drop the Windows description claim**

Line 8: change
`description = "Eyes-free text-to-speech layer for Claude Code (macOS + Windows)"`
to
`description = "Eyes-free text-to-speech layer for Claude Code (macOS)"`

- [ ] **Step 2: Remove the `[windows]` optional-dependencies group**

Delete the comment + group (the three `winrt-*` entries):
```toml
# Windows TTS runtime. OneCore speech is reached via PyWinRT; without these the
# daemon cannot synthesize and Windows users get silent no-speech (#7).
windows = [
    "winrt-runtime; sys_platform == 'win32'",
    "winrt-Windows.Media.SpeechSynthesis; sys_platform == 'win32'",
    "winrt-Windows.Storage.Streams; sys_platform == 'win32'",
]
```
Keep the `dev` and `kokoro` groups.

- [ ] **Step 3: Remove the Windows earcon package-data**

Delete the whole table (it has no other keys):
```toml
[tool.setuptools.package-data]
sonari = ["platform/windows/earcons/*.wav"]
```

- [ ] **Step 4: Verify the package still builds/installs**

Run: `.venv/bin/pip install -e ".[dev]" -q && .venv/bin/python -m pytest -q`
Expected: install succeeds (no `[windows]` resolution), suite PASS (same count as Task 5).

- [ ] **Step 5: Commit**

```bash
git commit -am "build(remove-windows): drop Windows extras, earcon package-data, and platform claim"
```

---

## Task 7: Remove the Windows `bin/` launchers

The Windows_NT assertion was already removed from `test_bin_shims.py` in Task 3, so editing the shims now breaks no test.

**Files:**
- Delete: `bin/sonari.cmd`, `bin/sonari-hook.cmd`
- Modify: `bin/sonari` (remove the `OS`/`Windows_NT` guard branch; keep the `/usr/bin/python3` preference)

- [ ] **Step 1: Delete the `.cmd` launchers**

```bash
git rm bin/sonari.cmd bin/sonari-hook.cmd
```

- [ ] **Step 2: Strip the `Windows_NT` guard from `bin/sonari`**

Run `grep -n "Windows_NT\|OS" bin/sonari` to find the guard, then remove that conditional branch so the script unconditionally prefers `/usr/bin/python3` (its existing macOS path). Leave the rest of the shim intact.

- [ ] **Step 3: Run the suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (same count as Task 6). `test_bin_shims.py`'s remaining `/usr/bin/python3` tests still pass.

- [ ] **Step 4: Commit**

```bash
git commit -am "build(remove-windows): delete Windows launchers and the bin/sonari Windows_NT guard"
```

---

## Task 8: Rewrite `CONTRIBUTING.md` to sole-maintainer, macOS-only

**Files — modify:** `CONTRIBUTING.md`

- [ ] **Step 1: Rewrite to a single-maintainer, single-OS model**

Apply all of:
- Replace the opening two-maintainer/two-OS premise (the "maintained by two people on two operating systems" paragraph) with a single-maintainer, macOS-only framing: the machine-checkable `pytest` suite checks portable logic; the maintainer checks the macOS runtime on real hardware.
- **Branch model:** remove the `win/...` scope line. Keep `macos/...`, `core/...`, `docs/...`, `test/...`.
- **Ownership table:** delete the `src/sonari/platform/windows/** | Max` row; collapse to a single row — all areas owned by Nima, who approves.
- **Verification section:** delete the "CI will run it on both macOS and Windows" sentence (there is no CI — this claim is already false) and the cross-platform-owner / `test_win_supervisor.py` `skipif` language. Keep the local `pytest` gate and the macOS runtime-acceptance step.
- **Platform discipline:** simplify to "macOS-specific code stays isolated in `src/sonari/platform/macos/`."
- **"A PR merges when":** remove the "in CI on both" / other-OS-owner-acceptance clauses; keep: one concern off `main`, suite green, owner approval, squash-merge + delete branch.

- [ ] **Step 2: Sanity-check no stale Windows/CI claim remains**

Run: `grep -niE "windows|two.+(maintainer|operating)|CI will" CONTRIBUTING.md`
Expected: **no output.**

- [ ] **Step 3: Commit**

```bash
git commit -am "docs(remove-windows): rewrite CONTRIBUTING for a sole-maintainer macOS project"
```

---

## Task 9: Delete the wholly-Windows docs

These are superseded design/plan/acceptance records; git history (and the `pre-windows-removal` tag) preserves them.

**Files — delete:**

- [ ] **Step 1: Remove the 11 Windows docs**

```bash
git rm \
  docs/superpowers/m2-windows-api-reference.md \
  docs/superpowers/M2-WINDOWS-ACCEPTANCE.md \
  docs/superpowers/M3-WINDOWS-ACCEPTANCE.md \
  docs/superpowers/WINDOWS-FRIEND-TEST-ROUND1.md \
  docs/superpowers/specs/2026-06-10-sonari-phase3-windows-design.md \
  docs/superpowers/specs/2026-06-16-windows-install-seam-design.md \
  docs/superpowers/specs/2026-06-16-windows-tts-no-voices-error-design.md \
  docs/superpowers/plans/2026-06-11-sonari-phase3-m2-windows-speech.md \
  docs/superpowers/plans/2026-06-16-sonari-phase3-m3-windows-hotkeys.md \
  docs/superpowers/plans/2026-06-16-windows-install-seam.md \
  docs/superpowers/plans/2026-06-16-windows-tts-no-voices-error.md
```

- [ ] **Step 2: Commit**

```bash
git commit -m "docs(remove-windows): delete superseded Windows design/plan/acceptance docs"
```

(Leave shared/historical docs with incidental Windows mentions — the M1 platform-seam plan, packaging-hardening design, session-streams/focus-follow/kokoro plans — as dated history. Do not edit them.)

---

## Task 10: Final verification — full suite + on-Mac smoke

This is the "verify before completion" gate. The implementer runs it; do **not** ask Nima to watch output.

- [ ] **Step 1: Full suite, clean run**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS, zero failures, zero collection errors. Record the final count.

- [ ] **Step 2: Confirm zero Windows residue in shipped code**

Run: `grep -rniE "win32|winsound|winrt|msvcrt|windows" src/sonari --include="*.py"`
Expected: at most stale *comments* (e.g. `macos/tts.py`'s dedup note) — **zero imports, zero `sys.platform == "win32"`, zero `winrt`/`winsound`/`msvcrt` usage.** (Comment scrubbing is deferred to Step 2; flag any non-comment hit as a miss to fix here.)

- [ ] **Step 3: On-Mac runtime smoke**

Reinstall and exercise the real runtime (the POSIX-collapse touched the singleton lock + venv-python paths, so confirm once):
```bash
.venv/bin/pip install -e ".[dev]" -q
sonari install        # or the repo's install entrypoint
sonari doctor
```
Expected: `sonari doctor` rows read green; the daemon starts and acquires its singleton lock; speech + earcons play; a global hotkey fires; focus-follow raises a real Terminal/iTerm2 window. Report the observed results.

- [ ] **Step 4: Open the PR**

Push the branch and open a PR into `main` titled `Remove Windows support (macOS-only)`. Body: link the spec, summarize deleted/surgery/kept, note "no version bump — internal cleanup," and the `pre-windows-removal` tag. **No `claude.ai/code/session` link in the body.** (`git checkout -b` is already done; push and `gh pr create` are separate calls.)

---

## Self-Review (completed by plan author)

- **Spec coverage:** the 4 branches (Task 4), the `windows/` subpackage (Task 5), the ~15 windows-only tests (Task 2) + 7 shared-file surgeries (Task 3), pyproject (Task 6), bin shims (Task 7), CONTRIBUTING (Task 8), the 11 docs (Task 9), the safety tag + green baseline (Task 1), and final verification (Task 10) — each spec item maps to a task.
- **Ordering invariant:** tests that import Windows code (Task 2) and tests asserting Windows branches (Task 3) are removed *before* the source they depend on (Tasks 4–5); `_winfakes`/conftest bootstrap are removed in the same commit as the subpackage (Task 5). No commit leaves a dangling import → suite green throughout.
- **Out of scope (Step 2):** abstraction collapse, file splits, broad comment scrubbing, `NoopRaiseBackend` removal, the `test_no_os_branch_in_core` guard's new purpose — none are touched here.
