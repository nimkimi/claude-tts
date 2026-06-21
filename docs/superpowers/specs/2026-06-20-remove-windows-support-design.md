# Remove Windows support — minimal-removal design

**Date:** 2026-06-20
**Status:** approved (design); pending implementation plan
**Scope:** Make Sonari macOS-only by **removing** Windows, with the *smallest safe diff*. Deliberately **not** an architecture change.

## Why

Maintaining a Windows backend without a Windows machine is the bottleneck: every
feature has to be built and tested twice, the macOS maintainer can't verify the
Windows runtime, and the Windows collaborator can't verify macOS. Dropping
Windows removes that dual-platform burden so feature work on macOS can move.

A separate, deliberate **architecture refactor (Step 2)** follows this — that is
where the platform abstraction is collapsed (or kept), long files are split, and
a code-organization pattern is chosen. See [Step 2 charter](#step-2-charter--architecture-refactor-deferred).

## Scope decision: minimal removal, keep the seam

This step **only removes Windows**. It does **not** restructure macOS code.
Concretely, we **keep** — untouched except for deleting the Windows branch:

- `src/sonari/platform/base.py` — the five backend ABCs + `PlatformBackend` + `NoopRaiseBackend`
- `src/sonari/platform/macos/**` — the macOS backend (all 7 modules)
- `src/sonari/platform/__init__.py`'s `get_platform()` factory (minus the `win32` arm)
- every consumer call site (`cli.py`, `daemon.py`, `keymap.py`, `client.py`, `paths.py`) — **unchanged**
- the long files (`daemon.py`, `cli.py`, …) — **unchanged**

Rationale: collapsing the abstraction is an *architecture* decision, not a
Windows-removal decision (the abstraction only looks Windows-shaped because it
was born to support Windows). Choosing flat-modules vs vertical-slice vs layered
belongs to Step 2, against a clean single-platform baseline. Doing it now would
commit to a structure before the pattern is chosen and would be redone by Step 2.

The result of this step is a **clean, fully-tested, single-platform baseline** —
behavior-identical on macOS — that Step 2 starts from.

## Changes

### 1. Production source — remove the 4 `sys.platform` branches (collapse to the POSIX arm)

After this, the only remaining `sys.platform` check is `get_platform()`'s
`darwin`-else-raise guard — the seam we deliberately keep. Every **Windows**
(`win32`) branch is gone, and `transport.py` no longer branches at all, so the
`test_no_os_branch_in_core` guard ("only `platform/__init__.py` branches on
`sys.platform`") becomes *more* accurate, not less.

- **`src/sonari/platform/__init__.py` — `get_platform()`**: delete the
  `elif sys.platform == "win32": from sonari.platform.windows import make_backend`
  arm. Keep the `darwin` arm and the `else: raise RuntimeError(...)` (a non-macOS
  host still errors cleanly).
- **`src/sonari/platform/transport.py` — `acquire_singleton()`**: delete the
  `if sys.platform == "win32": import msvcrt; msvcrt.locking(...)` arm; the
  `else:` `fcntl.flock` body becomes unconditional. Delete the Windows narration
  in the docstring (the `msvcrt` / named-mutex / `M2-WINDOWS-ACCEPTANCE` note).
- **`src/sonari/paths.py` — `kokoro_venv_python()`**: delete the
  `if sys.platform == "win32": return .../Scripts/python.exe`; the
  `return str(KOKORO_VENV / "bin" / "python")` becomes unconditional. (The local
  `import sys` can go too.)
- **`src/sonari/kokoro_provision.py` — `ensure_uv()`**: replace
  `exe = "uv.exe" if sys.platform == "win32" else "uv"` with `exe = "uv"`.

### 2. Production source — delete the Windows subpackage

Delete `src/sonari/platform/windows/` in full. This is **empirically verified
safe**, not just map-asserted: `grep -rni windows src/sonari/platform/macos/`
returns only stale *comments* in `macos/tts.py` (the `_TMP_PREFIX` dedup note) —
**zero imports** — and the only production reference to the subpackage anywhere
is the `platform/__init__.py:18` `win32` arm removed in §1. So no macOS or core
code path can call into deleted Windows code (the one failure the
"run-the-suite" net can't catch, since deleted tests can't fail).

- `__init__.py`, `tts.py`, `earcon.py`, `hotkeys.py`, `keytables.py`,
  `supervisor.py`, `supervisor_loop.py`
- `earcons/__init__.py`, `earcons/generate.py`, and the 7 bundled `.wav` assets

### 3. Tests

After the source changes, run the full suite; failures sort into three buckets.
The known set (from the footprint map):

**Whole-delete — Windows-only test files (~15):**
`test_win_autostart`, `test_win_backend`, `test_win_doctor_rows`,
`test_win_earcon`, `test_win_earcons_assets`, `test_win_hooks`,
`test_win_hotkeys`, `test_win_keytables`, `test_win_settings_hooks`,
`test_win_supervisor`, `test_win_tts`, `test_win_tts_kokoro`, plus
`_winfakes.py`, `test_winfakes.py`, and `test_earcon_generator.py` (imports
`windows.earcons.generate`). Deleting these loses **no** macOS coverage
(macOS earcons → `test_macos_earcon`; macOS hooks → the plugin `hooks.json` path).

**Surgery — shared files, keep the file, remove only the Windows part:**
- `tests/conftest.py` — remove the 4-line `tests._winfakes` import + `install()`
  bootstrap. Keep the rest (the `_isolate_sonari_dir` fixture).
- `tests/test_keymap.py` — remove the module-level `win` fixture and its 5
  Windows tests. Keep the `mac` fixture and macOS tests.
- `tests/test_kokoro_provision.py` — delete the Windows-only test
  (`test_ensure_uv_windows_uses_scripts_uv_exe`); collapse the
  `Scripts/python.exe` vs `bin/python` branched asserts to the POSIX arm.
- `tests/test_transport.py` — delete `test_acquire_singleton_windows_branch`.
- `tests/test_macos_tts_kokoro.py` — delete
  `test_tmp_prefix_matches_windows_for_cross_sweep` (imports `windows.tts`; the
  cross-backend WAV-sweep invariant ceases to exist).
- `tests/test_bin_shims.py` — remove the `.cmd` test and the `Windows_NT`
  assertion; keep the `/usr/bin/python3`-preference tests.
- `tests/test_cli_install.py` — cosmetic: rename the `/PY/pythonw.exe` fake
  string to a neutral path; drop the M3-Windows comment.

**Keep untouched — the seam's own tests stay valid:**
`test_platform_base`, `test_platform_factory` (darwin → macOS backend, and
unknown-OS → raises: both still true), `test_platform_raise_seam`,
`test_macos_backend`, `_fakeplatform.py`, all `test_macos_*`,
`test_no_os_branch_in_core` (the guard's invariant — only `platform/__init__.py`
branches on `sys.platform`, core stays OS-agnostic — still holds; its stale "M3
adds Windows" comment is deferred-scrub).

If the suite surfaces a Windows assertion the map didn't list (e.g. in
`test_paths`), it falls into the same buckets: import-error from a deleted module
→ delete the file; a `sys.platform = "win32"` monkeypatch test → delete the
function; a branched assert → collapse to POSIX.

### 4. Packaging — `pyproject.toml`

- Line 8 description: drop ` + Windows` →
  `"Eyes-free text-to-speech layer for Claude Code (macOS)"`. (This is the only
  place that advertised Windows to users; `plugin.json`, `marketplace.json`, and
  `README` already read macOS-only.)
- Remove the `[project.optional-dependencies] windows = [...]` group (the three
  `winrt-*` deps) and its comment.
- Remove the `[tool.setuptools.package-data] sonari = ["platform/windows/earcons/*.wav"]`
  entry (the whole `[tool.setuptools.package-data]` table, since it has no other keys).
- **No version bump** (stays `0.5.0`), **no CHANGELOG** — internal cleanup; no
  published artifact ever promised Windows.
- `src/sonari.egg-info/` is generated build metadata; it regenerates on the next
  build and needs no manual edit (it carries the stale claim only until rebuilt).

### 5. `bin/` launchers

- Delete `bin/sonari.cmd` and `bin/sonari-hook.cmd` (Windows launchers).
- `bin/sonari` — remove the `OS == Windows_NT` guard branch; keep the
  `/usr/bin/python3` preference. (Lockstep with the `test_bin_shims` edits above.)
- `hooks/hooks.json` — no edit (it references the extensionless `bin/sonari-hook`).

### 6. Docs

- **Delete** the 11 wholly-Windows docs (git history preserves them):
  - `docs/superpowers/m2-windows-api-reference.md`
  - `docs/superpowers/M2-WINDOWS-ACCEPTANCE.md`
  - `docs/superpowers/M3-WINDOWS-ACCEPTANCE.md`
  - `docs/superpowers/WINDOWS-FRIEND-TEST-ROUND1.md`
  - `docs/superpowers/specs/2026-06-10-sonari-phase3-windows-design.md`
  - `docs/superpowers/specs/2026-06-16-windows-install-seam-design.md`
  - `docs/superpowers/specs/2026-06-16-windows-tts-no-voices-error-design.md`
  - `docs/superpowers/plans/2026-06-11-sonari-phase3-m2-windows-speech.md`
  - `docs/superpowers/plans/2026-06-16-sonari-phase3-m3-windows-hotkeys.md`
  - `docs/superpowers/plans/2026-06-16-windows-install-seam.md`
  - `docs/superpowers/plans/2026-06-16-windows-tts-no-voices-error.md`
- **Leave** shared/historical docs with incidental Windows mentions as dated
  records (the M1 platform-seam plan, packaging-hardening design, session-streams
  / focus-follow / kokoro plans). Editing dated history is noise.

### 7. `CONTRIBUTING.md` — rewrite to sole-maintainer, macOS-only

The current file frames Sonari as two maintainers on two OSes; it also claims
"CI will run it on both macOS and Windows" — **already false** (there is no
`.github/` CI). Rewrite to a single-maintainer, macOS-only model:

- Drop the two-OS premise (lines 3–6) → single maintainer, single platform.
- Branch model: remove the `win/...` scope; keep `macos/...`, `core/...`,
  `docs/...`, `test/...`.
- Ownership table: remove the `platform/windows/** → Max` row; collapse to "all
  areas → Nima."
- Verification: drop the "CI on both OSes / cross-platform owner confirms"
  language and the `test_win_supervisor` skipif reference; keep the local
  `pytest` gate + the macOS runtime-acceptance step.
- Platform-discipline section: simplify to "macOS-specific code stays isolated in
  `src/sonari/platform/macos/`" (kept verbatim from the seam we retain).

## Deliberately unchanged

- The platform abstraction (`base.py` ABCs, `get_platform()`, `PlatformBackend`,
  `NoopRaiseBackend`) — kept as-is; whether to collapse it is a Step 2 decision.
- The long files — kept as-is; splitting them is Step 2.
- Consumer call sites — unchanged, so the **hotkey lifecycle** and
  **focus-follow / raise wiring** keep their exact current behavior (no
  collapse-induced risk to those two runtime-sensitive paths).
- Broad "Windows" wording in comments/docstrings across `base.py`, `daemon.py`,
  `cli.py`, `keymap.py`, `kokoro.py`, `macos/tts.py` — deferred to Step 2 (those
  files are rewritten there anyway). Only docstrings **adjacent to code we edit**
  (e.g. `transport.acquire_singleton`) are scrubbed in this step.

## Testing & verification

This is a behavior-preserving removal for macOS, so the macOS test suite is the
safety net and **stays green at every commit**.

- **Step 0 — establish the green baseline:** run the full macOS suite *before*
  any deletion, so "green at every commit" has a defined origin to compare against.
- After each removal commit, run `python -m pytest -q`; it must be green.
- The three POSIX-collapse edits (`transport`, `paths`, `kokoro_provision`) only
  remove the *Windows* arm — the macOS arm they leave behind is the code that
  already ran on macOS, so behavior is identical.
- Final empirical check on the real Mac (per "verify before completion"): the
  daemon starts and acquires its singleton lock, speech + earcons play, a hotkey
  fires, focus-follow raises a real Terminal/iTerm2 window, and
  `sonari doctor` reads green. (These paths are *unchanged* by minimal removal,
  but the POSIX-collapse touched the lock + venv-python paths, so confirm once.)

## Safety

Tag `pre-windows-removal` on `main` **before** the first deletion commit, so the
full Windows backend stays trivially recoverable (`git checkout pre-windows-removal -- …`)
even after history moves on.

## Branch / PR

Per repo convention: branch `core/remove-windows` off `main`, one concern, the
suite green on macOS, squash-merge. (No direct push to `main`.)

## Step 2 charter — architecture refactor (deferred)

> **Goal:** Choose and apply a coherent code-organization architecture for the
> now macOS-only Sonari, and improve maintainability — starting from the clean
> single-platform baseline this spec produces.
>
> **In scope:** (1) Pick the pattern — evaluate vertical-slice (group by feature:
> speech, hotkeys, focus-follow, install) vs layered vs domain-module, against
> how Sonari's code actually clusters. (2) Decide the fate of the platform
> abstraction — collapse `base.py`/`get_platform()`/`PlatformBackend` and inline
> macOS, or keep a thin seam — *as a consequence of the chosen pattern*, not in
> isolation. (3) Split the long files — `daemon.py` (~1,200 lines), `cli.py`, and
> any others the chosen pattern fragments — into focused units. (4) Resolve the
> now-unused-in-production `NoopRaiseBackend`, the stale Windows wording across
> comments/docstrings, the `macos/tts.py` `_TMP_PREFIX` dedup rationale, and the
> `test_no_os_branch_in_core` guard's purpose (repurpose as a "no `win32` ever
> returns" regression guard, or retire).
>
> **Out of scope:** new features; runtime-behavior changes (it's a refactor —
> the macOS test suite must stay green throughout).
>
> **Entry condition:** Step 1 (this spec) merged; macOS suite green; baseline tagged.
>
> **First action:** a dedicated brainstorm that *measures* the codebase (file
> sizes, dependency clusters, change-coupling) before proposing patterns — the
> architecture should be chosen from evidence, not picked off a shelf.
