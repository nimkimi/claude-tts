# Sonari Stage 3 — CLI / Install Restructure (Design Spec)

**Date:** 2026-06-22 · **Status:** approved design, pre-implementation · **Repo:** `claude-tts` (macOS-only) · **Branch base:** `main` @ `3212ced`

> The fast-follow named in the Stage-2 spec §11. Stage 2 tamed the daemon god object;
> Stage 3 tames the **other** center of gravity — `cli.py` — and resolves the four
> measured duplications. Self-contained: the load-bearing measurements are inline.

---

## 1. Goal

After Stage 2 decomposed `daemon.py`, **`cli.py` (568 lines) is now the repo's #1 source
file by both size and churn** (53 of ~66 measured `*.py` commits ≈ 80%), and it holds the
**only code that can destroy real user state** (`shutil.rmtree`, `os.remove`, plist/launcher
removal under `~`). Stage 3 gives it the same treatment Stage 2 gave the daemon:

1. **Isolate the hot, dangerous region.** Move install / uninstall / voices / lifecycle
   helpers — ~80% of cli.py's churn and 100% of its destructive-file code — into a single
   walled-off unit that is the highest-care code in the repo.
2. **Resolve the four measured duplications** (`_read_install_record`, `_xml_escape`,
   atomic-write, `swiftc build()`) into shared helpers — without changing behavior.
3. **Stay behavior-identical.** No user-facing change. Every spoken word, every file
   written, every file deleted is byte-for-byte what it is today.

### Owner decisions (the hard inputs)

- **Shape = Approach A: a `cli/` package** mirroring the `daemon/features/` idiom Stage 2
  established (one consistent "god-file → package" pattern across the codebase). The flat-
  siblings variant (B) was rejected — equal cost/risk, but diverges from the established idiom.
- **No behavior changes.** Unlike Stage 2 (which carried one approved validation change),
  Stage 3 is purely structural. The dedup helpers are **parameterized to reproduce each
  call site's current behavior exactly** (§5).
- **§6 of the Stage-2 spec is corrected, not executed** (§6 below): dedup the macOS helpers,
  do **not** flatten `platform/macos/*` into one file.

---

## 2. Evidence basis (measured 2026-06-22, against `main` @ `3212ced`)

- **`cli.py` is the new center of gravity.** Largest source file (568 lines; next is
  `daemon/host.py` at 453). Highest churn: `git log --follow` = **53 commits**, vs **66**
  total `src/sonari/**/*.py` commits ≈ **80%**. It is a leaf in *production* but the dominant
  edit surface.
- **Churn is concentrated, not spread** (same shape as the Stage-2 god object). Bucketing the
  53 commit subjects: **~38–40** touch install / uninstall / plist / launcher /
  python-resolution / kokoro-venv; **~8** touch doctor (usually co-changing with install);
  **~3** touch the control senders; **~2** the keymap subcommand; **~4** scaffold/rename.
  The control senders and keymap subcommand are **stable thin wrappers**.
- **`cli.py` is a pure production leaf.** `rg` finds **zero** `from sonari.cli` / `import cli`
  imports anywhere in `src/`. The only coupling is **tests** (913 lines across 7 files) that
  **monkeypatch cli internals** (`cli._copy_app`, `cli._read_install_record`,
  `cli._write_install_record`, `cli._platform`, …). This is the migration's defining
  constraint (§7).
- **Entry point is dual, and one form is fragile under packaging:**
  - `pyproject.toml [project.scripts]`: `sonari = "sonari.cli:main"` — **survives** the package
    conversion *iff* `cli/__init__.py` exports `main`.
  - `bin/sonari` (the launcher the owner uses; also what `sonari install` wires into the
    LaunchAgent): `exec "$py" -m sonari.cli "$@"` — the **`python -m sonari.cli`** form
    **breaks** when `cli.py` becomes a package **unless** `src/sonari/cli/__main__.py` exists.
    `cli/__main__.py` is therefore a **mandatory** deliverable, not optional.
- **The four duplications, exact:**
  1. **`_read_install_record` — behavior-equivalent (NOT byte-identical)** in `cli.py:315-322`
     and `daemon/features/lifecycle.py:8-16`. Both read `paths.INSTALL_RECORD_PATH` and return
     `None` on any exception, but the two bodies differ in four textual ways: docstring wording,
     module-level vs inline `import json`, `paths.INSTALL_RECORD_PATH` vs bare
     `INSTALL_RECORD_PATH`, and noqa comment text. The **reader is the genuine duplication**;
     **`_write_install_record` exists ONLY in `cli.py:296-312`** (no duplication — see §5 Dedup #1).
     Tests: `test_cli_doctor.py:21` patches `cli._read_install_record`;
     `test_daemon_setup_health.py` patches `lifecycle.INSTALL_RECORD_PATH` at **7 sites**
     (lines 15,24,34,43,52,62,72) and calls `lifecycle._read_install_record()` directly at line 73.
  2. **`_xml_escape` — byte-identical** in `platform/macos/hotkeys.py:57-59` and
     `platform/macos/supervisor.py:37-39`. **No module-level circular import** — the
     hotkeys↔supervisor cross-imports are all *method-local* (`hotkeys.py:132`,
     `hotkeys.py:218`, `supervisor.py:275-276`). There is no cycle to dissolve.
  3. **Atomic-write — 4 near-identical sites, diverging on two axes only:**
     `config.py:55-63 save_config` (fsync, no chmod, indent=2);
     `keymap.py:161-169 _write_user_keymap` (fsync, no chmod, indent=2);
     `keymap.py:200-213 write_resolved` (fsync, no chmod, no indent — `json.dumps` then
     `fh.write`); `platform/transport.py:16-22 write_lockfile` (**no fsync**, **chmod 0o600**,
     no indent). `kokoro.py:119-128 _download` is a **streaming** download — a different
     pattern, **out of scope**.
  4. **`swiftc build()` — near-identical clones** in `platform/macos/hotkeys.py:155-183` and
     `platform/macos/raiser.py:63-89`. They differ in **five** values: source file, output path,
     hash file, the OSError source-name infix (`hotkeyd source` vs `sonari-raise source`), and
     the unchanged-message suffix (`any permission grants` vs `the Automation grant`) — two
     independent user-visible strings, not one (§5 Dedup #4). The `swiftc` references in
     `supervisor.py:208,299-302` and `cli.py`/
     `lifecycle.py` are **presence checks / comments / diagnostics — not compiles** — and stay.
     *(This corrects the handoff's "swiftc spread across 5 files": there are 2 real sites.)*
- **The destructive-file risk surface** (all under `~`, all paths frozen at import via
  `Path.home()` / `expanduser`):
  - `uninstall()` (`cli.py:434-482`): `shutil.rmtree(APP_DIR)`; `os.remove` of LOCK, LOG,
    HOTKEYD_RESOLVED, INSTALL_RECORD, hotkeyd.log, faulthandler.log; delegates plist removal
    (speechd + hotkeyd) and launcher (`~/.local/bin/sonari`) + hotkeyd-bin removal to the
    platform backends. **Preserves `config.json` + `keymap.json`.**
  - `install()` (`cli.py:361-427`): `shutil.rmtree(APP_DIR)` then `copytree`; writes install
    record, default keymap, plists, launcher.
  - `_cmd_voices_uninstall` → `kp.uninstall_kokoro()`: `shutil.rmtree(KOKORO_VENV)`.
  - **Test isolation:** `conftest.py`'s autouse `_isolate_sonari_dir` rebinds **each module's**
    imported path constant to a per-test `tmp_path/.sonari`. Because the constants freeze at
    import, setting `$HOME` after import does **not** redirect them — a *subprocess* launched
    with `HOME=$(mktemp -d)` is the only way live execution sees a sacrificial home.

---

## 3. The chosen shape (Approach A)

`cli.py` becomes a **`cli/` package**: a thin entry/dispatch core + three feature modules
drawn at the churn/risk seams the measurement found. This is the exact idiom Stage 2 applied
to `daemon.py` (thin host + `features/*`), so the codebase has **one** decomposition story.
Three shared-helper modules absorb the duplications.

**Why not B (flat siblings — `installer.py` + `doctor.py` + slim `cli.py`):** identical
substance, identical migration cost and risk, but it invents a second structural idiom for no
gain. `cli` is a pure leaf, so the package split is as safe as the flat one. (Owner decision.)

---

## 4. Target architecture

```
src/sonari/cli/
  __init__.py    # main(argv), _build_parser(), _register_local(), dispatch, _cmd_daemon,
                 #   + shared plumbing used across command groups: _send(), _platform(),
                 #     _daemon_not_running_message(), _resolve_python()
                 #   + back-compat re-exports of every symbol the test suite patches (§7)
  __main__.py    # `sys.exit(main())` — REQUIRED so `python -m sonari.cli` (bin/sonari) works
  control.py     # _cmd_status/_cmd_verbosity/_cmd_rate/_cmd_minqueue/_cmd_voice/
                 #   _cmd_stop/_cmd_skip + _combo_label/_cmd_keymap  (thin protocol senders)
  doctor.py      # doctor() + _cmd_doctor()                          (read-only diagnostics)
  install.py     # install()/_cmd_install, uninstall()/_cmd_uninstall,
                 #   _cmd_voices_install/_cmd_voices_uninstall,
                 #   + helpers: _daemon_python, _read_plugin_version, _copy_app,
                 #     _build_raise_helper
                 #   THE HIGH-RISK, FILE-MUTATING UNIT — isolated, highest-care

src/sonari/install_record.py     # read_install_record() / write_install_record(record)
src/sonari/atomicio.py           # atomic_write_json(path, data, *, indent=None,
                                 #   chmod=None, fsync=True)
src/sonari/platform/macos/_helpers.py  # xml_escape(s) + build_swift_binary(src, out,
                                       #   hash_path, src_label, unchanged_note) -> (ok, detail)
```

### Unit contracts

| Unit | Responsibility | Public contract | Depends on |
|---|---|---|---|
| `cli/__init__.py` | Parse argv, dispatch to a command handler, friendly daemon-down message; expose the patch surface | `main(argv=None) -> int`; `_cmd_daemon`; shared plumbing `_send`, `_platform`, `_daemon_not_running_message`, `_resolve_python`; re-exports (§7) | `protocol`, `paths`, `keymap`, `platform`, the three command modules (the command modules reach `_platform`/`_send`/`_resolve_python` via **function-local** imports — §7 rule 8) |
| `cli/__main__.py` | Module-execution entry for `python -m sonari.cli` | `sys.exit(main())` | `cli` |
| `cli/control.py` | The thin protocol senders + keymap list/unbind | each `_cmd_*(args) -> int`; `_combo_label` | `cli._send`, `protocol`, `keymap`, `platform` |
| `cli/doctor.py` | Read-only health diagnostics | `doctor() -> list[(check, ok, detail)]`; `_cmd_doctor(args) -> int` | `paths`, `platform`, `install_record`, `keymap`, `client`, `kokoro_provision`; `_resolve_python`/`_platform` from `cli/__init__` (function-local import — §7 rule 8) |
| `cli/install.py` | All install/uninstall/voices + lifecycle helpers; the destructive paths | `install()`, `uninstall()`, `_cmd_*` (install/uninstall/voices); `_daemon_python`, `_read_plugin_version`, `_copy_app`, `_build_raise_helper` | `paths`, `platform`, `keymap`, `install_record`, `kokoro_provision` |
| `install_record.py` | The single install.json reader/writer | `read_install_record() -> dict\|None`; `write_install_record(record: dict) -> None` | `paths`, `atomicio` (for the write) |
| `atomicio.py` | One atomic JSON writer covering all current call shapes | `atomic_write_json(path, data, *, indent=None, chmod=None, fsync=True) -> None` | stdlib only |
| `platform/macos/_helpers.py` | Shared plist-escape + Swift-compile | `xml_escape(s) -> str`; `build_swift_binary(src, out, hash_path, src_label, unchanged_note) -> (bool, str)` | `paths`, stdlib |

Each unit passes the independence test (what it does / how to use it / what it depends on,
without reading internals). `cli/install.py` is the one to read closely — it is the
state-mutating unit and carries the §7 risk discipline.

---

## 5. The four deduplications — behavior-preserving by construction

**Dedup #1 — install record.** New `install_record.py` holds `read_install_record()` and
`write_install_record(record)`.
- **`read_install_record()`** resolves the genuine cli↔lifecycle duplication. The two copies are
  behavior-equivalent but textually differ (§2), so the spec names the canonical body explicitly:
  the **lifecycle copy adapted to the repo norm** — module-level `import json`,
  `paths.INSTALL_RECORD_PATH` (not bare), docstring "Return the install.json dict, or None if
  unreadable/absent. Never raises.", noqa "install-record must never raise". Reads exactly as today.
- **`write_install_record(record)` is moved VERBATIM** from `cli._write_install_record`:
  `os.makedirs(parent, exist_ok=True)` → a **plain** `open()` → `json.dump(record, f, indent=2)`
  → `f.write("\n")`. It is **NOT** routed through `atomic_write_json`. Rationale: the writer is
  **not duplicated** (it exists only in `cli.py`, §2), so there is no dedup to perform; and the
  current write is non-atomic, non-fsynced, and trailing-newline-terminated — routing it through
  the atomic helper would silently change install.json's bytes (drop the trailing `\n`), add an
  fsync, add an atomic rename, and break on a missing parent dir. Moving it verbatim keeps §8's
  byte-identical guarantee **unconditional**. (An fsync/atomicity hardening of install.json would
  be a separate, explicitly-approved behavior change — out of scope here.)

`cli/install.py`, `cli/doctor.py`, and `daemon/features/lifecycle.py` import from this module.

**Dedup #2 — `xml_escape`.** New `platform/macos/_helpers.py` holds `xml_escape(s)` (verbatim).
`hotkeys.py` and `supervisor.py` import it. No cycle exists or is introduced (the cross-imports
were always method-local).

**Dedup #3 — atomic write.** `atomic_write_json(path, data, *, indent=None, chmod=None,
fsync=True)` does: write `str(path)+".tmp"` → `json.dump(data, fh, indent=indent)` → `flush` →
`os.fsync` *iff* `fsync` → `os.chmod(tmp, chmod)` *iff* `chmod is not None` → `os.replace`.
Call sites map to **byte-identical on-disk output**:
- `config.save_config`: `indent=2, fsync=True, chmod=None`
- `keymap._write_user_keymap`: `indent=2, fsync=True, chmod=None`
- `keymap.write_resolved`: `indent=None, fsync=True, chmod=None` (matches `json.dumps(...)` →
  `fh.write`, which equals `json.dump(..., fh)` with no indent)
- `transport.write_lockfile`: `indent=None, **fsync=False**, chmod=0o600` (preserves the
  no-fsync + 0o600 behavior exactly)
`kokoro._download` is **not** migrated (streaming, different shape). **`write_install_record` is
NOT a consumer** either — it is moved verbatim as a plain write (Dedup #1), since it is not
duplicated and routing it here would change install.json's bytes.

**Dedup #4 — Swift compile.** `build_swift_binary(src, out, hash_path, src_label,
unchanged_note) -> (ok, detail)` is the verbatim `build()` body, parameterized on the **five**
things that differed — two of them independent user-visible strings a single `label` could not
cover. Its two message returns:
- OSError path: `(False, "cannot read {src_label} source: {exc}")`
- unchanged path: `(True, "{out} (unchanged; kept to preserve {unchanged_note})")`

Call sites (both become one-line delegations):
- `MacHotkeyBackend.build`: `build_swift_binary(src, HOTKEYD_BIN_PATH, hash_path, "hotkeyd", "any permission grants")`
- `MacRaiseBackend.build`: `build_swift_binary(src, RAISE_BIN_PATH, hash_path, "sonari-raise", "the Automation grant")`

`test_macos_raise.py:149` (asserts "automation grant" in the raiser detail) is the
behavior-preservation gate for this dedup. The `swiftc` *presence checks* in `supervisor.py`
(install warning + doctor row) are a different concern and stay untouched.

---

## 6. §6 correction (Stage-2 spec predates this measurement)

The Stage-2 spec listed an *optional* "flatten `platform/macos/{tts,earcon,hotkeys,supervisor}`
into one `macos.py`, dissolving the hotkeys↔supervisor circular import." The measurement
**refutes both halves**:
- **No circular import exists** — both cross-imports are method-local; there is no module cycle.
- **Flattening would create a ~900-line file** (supervisor 349 + tts 234 + hotkeys 233 + earcon
  + raiser), directly violating the spec's own "small focused files" goal.

So Stage 3 takes only the **real** value of §6 — the dedup (`xml_escape` + `build_swift_binary`
in `_helpers.py`) — and **does not flatten**. The per-backend macОS files stay as they are.

---

## 7. Test & migration strategy (the load-bearing part)

`cli.py` is a production leaf whose only couplers are 913 lines of tests that **monkeypatch its
internals**. The trap: **a re-export preserves name resolution but NOT monkeypatch
interception.** Once `doctor()` lives in `cli/doctor.py`, it looks up `read_install_record` in
*its own* module namespace; a test that patches `cli._read_install_record` becomes a **silent
no-op** — the real reader (and, in install tests, the real `rmtree`/`copytree`) runs, the
assertion still passes, and the test is **green but hollow**. "752 still green" will NOT catch
this.

Rules baked into the plan:

1. **Patch targets move with the symbol, in the same commit.** When a function moves from `cli`
   to `cli/install.py` (etc.), every test that patches it is repointed to where it is now
   *looked up* — i.e. patch `sonari.cli.install._copy_app`, not `sonari.cli._copy_app`. This is
   real migration work touching a meaningful slice of the 913 test lines, **not** zero.
2. **The gate is "the fake still fires," not "suite green."** For each moved-and-repatched
   symbol, prove interception with an explicit signal: `mock.assert_called…`, a sentinel return
   the test asserts on, or a deliberate "make the fake raise and confirm the test sees it" check.
   A test that can pass with the real function running is not a valid gate for that move.
   **Concrete hardening required (these tests assert nothing about the patched symbol today and
   would go green-but-hollow):** `test_cli_install.py`'s `_copy_app`/`_write_install_record`
   patches are plain lambdas with no `assert_called` (e.g. lines 26-27, 196-197, 249-250) —
   replace each with a `mock.Mock` + `assert_called_once()` **before** the move. `test_cli_voices.py`
   patches `cli.install` (lines ~8-17, 20-30, 46-52); the correct post-move target is
   `sonari.cli.install.install` — repoint in the same commit as Step 7 and add an `assert_called`.
3. **Back-compat re-exports are for the entry contract, not the test patches.** `cli/__init__.py`
   re-exports `main` (and any genuinely public name) so `sonari.cli:main` and `import sonari.cli`
   keep working. Re-exports are **not** relied on to keep monkeypatch tests intercepting — those
   are repointed per rule #1.
4. **`cli/__main__.py` ships in the same step as the package conversion** so `python -m
   sonari.cli` (and therefore `bin/sonari` and the installed LaunchAgent) never breaks.
5. **All install/uninstall execution runs under a sacrificial HOME.** In-process tests rely on
   conftest's per-test constant rebinding (extend it for any new module that imports a path
   constant). Any *live* smoke runs as a **subprocess with `HOME=$(mktemp -d)`** (constants
   freeze at import, so only a fresh process sees the sacrificial home). **Never** run `sonari
   install`/`uninstall` against the real `~/.sonari`; **never** use the owner as a test harness.
6. **Suite gate:** `.venv/bin/python -m pytest -q --ignore=tests/test_kokoro.py` stays at
   **752 passed**. Green AND count-never-drops at every task boundary.
7. **Dedup consumers verified at the source.** For each dedup, after the shared helper lands and
   call sites delegate, run the *existing* tests that cover each consumer (config save, keymap
   write/resolve, transport lockfile, both Swift builds, the lifecycle install-record reader) —
   these are the behavior-preservation gate for §5.
8. **The reverse hollow trap: shared plumbing that STAYS in `__init__` but is CALLED from a moved
   module must be reached via a function-local import.** `_platform()`, `_send()`,
   `_daemon_not_running_message()`, and `_resolve_python()` stay in `cli/__init__.py`, but
   `control.py`/`doctor.py`/`install.py` all call them, and the suite patches them as
   `cli._platform`/`cli._send` (e.g. `test_cli_install.py:25,48,59,195,248`,
   `test_cli_doctor.py:15,101,113`, `test_cli_focus_follow.py:44,46`, `test_cli_control.py:105`).
   A **module-level** `from sonari.cli import _platform` in `control.py` resolves with no error but
   binds the **unpatched original** into that module's namespace → the patch becomes a silent
   no-op (green but hollow). So every cross-boundary call to `__init__` plumbing uses a
   **function-local** `from sonari.cli import _platform` inside the function body — matching
   cli.py's existing deferred-import discipline. This is rule #1 in reverse (symbol stays, caller
   moves); apply the rule-#2 "fake fires" gate to these call sites too.

---

## 8. User-facing behavior changes

**None.** Stage 3 is purely structural. Speech output, earcons, ordering, every file written,
every file deleted, file permissions (incl. the lockfile's `0o600`), and fsync behavior are
byte-for-byte identical. The dedup helpers are parameterized specifically to guarantee this
(§5). In particular, **`write_install_record` is moved verbatim** (plain write, `indent=2`,
trailing `\n`, no fsync, no atomic rename) rather than routed through `atomic_write_json` —
precisely so install.json's bytes and write semantics are unchanged (§5 Dedup #1). Any
divergence found during implementation is a bug, not an approved change.

---

## 9. Migration sequence — phased, net-first, install LAST

Risk increases monotonically; the destructive `install.py` lands last, like the speak loop did
in Stage 2.

- **Step 1 — `atomicio.py` + adopt it.** Create `atomic_write_json`; switch `config`, `keymap`
  (×2), `transport` to it with the §5 parameters. Gate: each consumer's existing tests +
  "fake fires" where applicable. No cli changes yet. *(Pure, lowest risk, immediately useful.)*
- **Step 2 — `install_record.py` + adopt it in the daemon.** Create the module (`read_…`
  canonical body per §5; `write_…` moved verbatim — but the writer is not used by the daemon).
  Repoint `daemon/features/lifecycle.py` to import `read_install_record` from it; delete the
  lifecycle copy **and** lifecycle's `from sonari.paths import INSTALL_RECORD_PATH`. **This breaks
  8 sites in `test_daemon_setup_health.py` in the same commit:** the 7
  `monkeypatch.setattr(lifecycle, "INSTALL_RECORD_PATH", …)` (lines 15,24,34,43,52,62,72) → repoint
  to `install_record`; and the direct `lifecycle._read_install_record()` (line 73) →
  `install_record.read_install_record()`. **Extend `conftest._isolate_sonari_dir` to also rebind
  `install_record.INSTALL_RECORD_PATH`** (it binds the constant by value at import, exactly as
  lifecycle did). Gate: run `test_daemon_setup_health.py` in isolation; prove the fake fires (force
  a bad path, confirm the test sees `None`, not a real-file read).
- **Step 3 — `platform/macos/_helpers.py` (dedup #2 + #4).** Add `xml_escape` +
  `build_swift_binary`; make `hotkeys`/`supervisor` use the shared escape and
  `hotkeys.build`/`raiser.build` delegate. Gate: hotkeyd-build, raise-build, and plist-escape
  tests.
- **Step 4 — Create the `cli/` package skeleton.** Convert `cli.py` → `cli/__init__.py`
  (verbatim contents to start) + add `cli/__main__.py`. Confirm `python -m sonari.cli`,
  `sonari.cli:main`, and the full cli test suite are green **before** moving any function.
  *(This is the "ladder→table calling the same methods" analog: shape change, no bodies moved.)*
- **Step 5 — Move `control.py`** (senders + keymap). Repoint their patch targets; prove fakes
  fire. Low risk (stable, thin).
- **Step 6 — Move `doctor.py`.** Repoint only the patch targets for symbols that physically
  *moved*: the install-record reader (`cli._read_install_record` → patch
  `install_record.read_install_record` where doctor looks it up). `_platform`/`_resolve_python`
  stay in `cli/__init__` and are reached via **function-local** imports (§7 rule 8), so their
  existing `cli._platform` / `cli._resolve_python` patches keep intercepting **without**
  repointing — that is the point of rule 8. `doctor()` keeps its local `kokoro_provision` import.
  Prove the moved-symbol fakes fire.
- **Step 7 — Move `install.py` (the dangerous unit) LAST.** Move install/uninstall/voices +
  the lifecycle helpers (`_daemon_python`, `_read_plugin_version`, `_copy_app`,
  `_build_raise_helper`); have `cli/install.py` use `install_record.write_install_record`.
  (`_resolve_python` and `_cmd_daemon` do **not** move here — they live in `cli/__init__`.)
  Repoint the patch targets for the *moved* symbols (`cli._copy_app` / `cli._write_install_record`
  / `cli._daemon_python` / `cli._read_plugin_version` / `cli._build_raise_helper` →
  `sonari.cli.install.*`); `cli._platform` / `cli._send` stay reached function-locally and keep
  intercepting (§7 rule 8). **Harden the lambda patches to `mock.Mock` + `assert_called_once` and
  repoint `test_cli_voices.py`'s `cli.install` → `sonari.cli.install.install`** (§7 rule 2); prove
  each fake fires (especially the `rmtree`/`copytree`/`os.remove` doubles). Gate: full suite (752)
  + a subprocess sacrificial-HOME smoke of `sonari doctor` (read-only; never a live install).
- **Step 8 — Tidy `cli/__init__.py`** to its floor: keep `main`/parser/dispatch/shared plumbing
  + the public re-export; remove any now-dead internal forwarders that no test or entry point
  needs. Gate: suite green + entry points verified.

Each step is its own commit (or a tight commit group) with the suite green at the boundary.

---

## 10. Out of scope (explicit)

- **`kokoro._download`** — streaming atomic write, a different pattern; not folded into
  `atomic_write_json`.
- **Flattening `platform/macos/*`** — refuted in §6.
- **Any behavior change** — §8. No validation tightening, no new flags, no copy changes.
- **`daemon/`** — Stage 2 is done and shipped; untouched here except the one `lifecycle.py`
  import repoint in Step 2.
- **Re-adding Windows/Linux** — unchanged stance from the Stage-2 spec.

---

## 11. Risks & honest weak spots

1. **Green-but-hollow tests** (the §7 trap) are the primary risk. Mitigation: patch-targets-
   move-with-symbol + "fake fires" gate, enforced per moved symbol — not "suite green."
2. **`install.py` is genuinely destructive.** Mitigation: it moves last, verbatim; all live
   execution is subprocess-sacrificial-HOME; the owner is never the harness; uninstall's
   preserve-list (`config.json`, `keymap.json`) is pinned by an existing test that must stay green.
3. **`atomic_write_json` must reproduce 4 distinct behaviors** (fsync on/off, chmod on/off,
   indent on/off). Mitigation: the §5 parameter table is explicit; each consumer's existing
   tests are the gate; `transport`'s 0o600 + no-fsync is called out specifically.
4. **conftest path-rebinding is brittle to new modules.** `install_record.py` imports a path
   constant; if any test exercises it via the real path, conftest must rebind it too. Mitigation:
   Step 2 extends conftest in the same commit.
5. **Entry-point dual contract.** `bin/sonari` uses `-m sonari.cli`; the console script uses
   `sonari.cli:main`. Mitigation: `cli/__main__.py` + `main` re-export, both verified in Step 4.

---

## 12. Definition of done

- `cli/` package exists per §4; `install.py` isolates 100% of the destructive paths; **no new
  file exceeds ~350 lines** (below the accepted 453-line `daemon/host.py`; if `cli/install.py`
  lands materially above, that triggers an explicit owner decision, not a silent pass); each unit
  passes the independence test.
- The four duplications are resolved: `read_install_record` shared (and `write_install_record`
  homed once, moved verbatim), one `atomic_write_json` (covering config/keymap×2/transport), one
  `xml_escape`, one `build_swift_binary`.
- §6 corrected: macOS helpers deduped, files **not** flattened.
- Behavior byte-identical (§8); the uninstall preserve-list test and all dedup-consumer tests green.
- Every moved-and-repatched symbol proven intercepted ("fake fires"), not merely suite-green.
- `python -m sonari.cli`, `sonari.cli:main`, and `bin/sonari` all work.
- Full suite green at **752** (`pytest --ignore=tests/test_kokoro.py`); a subprocess
  sacrificial-HOME `sonari doctor` smoke is clean; the owner was never a test harness.
- Work done on a Stage-3 branch off `main`; merged to **local** `main` only on the owner's call;
  not pushed without an explicit ask.
