# Sonari testing charter

Filled from `~/.claude/skills/pipelining-subagent-work/TESTING-CHARTER.template.md`. Read
`TEST-MAP.md` (same directory) alongside this file — the map locates coverage, this file explains
why the suite is shaped the way it is and how to extend it without re-introducing the bug class
this audit closed.

## Reason to exist

Three high-severity, new-in-receipts audible-behavior bugs survived seven reviews, dual
whole-branch MERGE-READY verdicts, and 1807 green tests. Every one is a **composition** the
per-site tests could not see. The suite's blind spot is not individual assertions — it is
cross-gesture sequences. Tests here earn their place by driving real gesture compositions through
`make_daemon()` and asserting on the audible surface.

The seam-bug hunt this audit ran (see `TEST-MAP.md`'s "Known gaps → Open") found nine more bugs of
exactly this shape after the suite was already green and after the fixture-honesty fix (P3)
closed the *narrower* E-3 blind spot (a `FakeSpeaker` that recorded a cue was *called* without
proving a sound would *play*). Fixing the recording problem did not fix the composition problem —
they are different failure classes and this charter treats them as different. A single-site test
proves a chokepoint fires; only a driven, multi-step scenario proves the *sequence of* chokepoints
that a real ⌃⌘ gesture triggers doesn't step on itself.

## The coupling law

**Every user-meaningful ledger operation maps to exactly one distinct sound.** (Verbatim, from the
cue registry's own doctrine, restated in the Receipts design spec.) This is the oracle a
control-cue/mute test should check against: not "was `.transient()` called" (that's the exact
assertion shape that went blind — 65 assertions across 27 files proved a cue was called and none
proved a sound would play, until the P3/D0.2 `FakeSpeaker` fix), but "did exactly the one sound
this ledger op promises reach the audible surface, and no other."

The sealed product definition this suite ultimately serves, verbatim:

> "Sonari is my ears across all my Claude Code sessions — it tells me what happened and what needs
> me, in whichever session needs me, with just enough controls to answer and move between them
> without looking."

Silence is the failure state. A test that can't distinguish "spoke the right thing" from "spoke
nothing" or "spoke the wrong thing" is not proving what its name claims, no matter how green it
runs. (`docs/RECONCILIATION.md`'s companion doc, `silence-inventory.md`, catalogues 34 points in
the pipeline where an utterance can fail to reach the ear — read it before writing a new test in
the control-cue/mute/keepalive/persistence areas; several of its "apparently un-chosen" gaps are
exactly the shape this charter asks new tests to close.)

## What each test tier is for

Sonari's suite runs on four tiers, not the template's generic three — the audit found this
vocabulary already load-bearing in the P2–P4 reports and TEST-MAP.md uses it, so the charter
matches rather than introduces a second taxonomy.

| Tier | What it's for here | When to write one |
|---|---|---|
| **Drive** (integration, via `make_daemon()`/`make_net()` in `tests/daemon_helpers.py`) | Proves a user-visible behavior across modules by dispatching real `{"type": ...}` messages and asserting on the audible/observable surface (`speaker.spoken`, `speaker.earcons`, `speaker.silent_cues`). This is the tier the composition bugs live in, and the tier that catches them. | **DEFAULT.** Prefer extending a drive test in the owning subsystem cluster (see `TEST-MAP.md`) over adding a unit test, unless the behavior is a pure algorithmic core. |
| **Unit** | Algorithmic cores with real branching, tested by importing the class/function directly: `queue.py`, `speaker.py`, `history.py`, `sessions.py`, `assembler.py`, `cleaner.py`, `summarizer.py`, `config.py`. | Only where logic density earns it — never one-per-function. A unit test that can't fail against a real mutant (see Done-gates below) shouldn't exist. |
| **Structural guard** | AST/source-text scans asserting an invariant about the *code*, not a runtime behavior: chokepoint guards (`test_answerability.py`), exhaustive enum pins (`test_protocol.py`'s `MsgType` pin), Protocol conformance (`test_platform_base.py`), banned-pattern scans (`test_no_os_branch_in_core.py`). | When the invariant is "this shape of code must never exist/must always exist here," not "this input produces this output." Cheap, durable, easy to widen (P4b widened one to close four navigation real-gaps at once) — but it proves nothing about the audible surface, so it's never a substitute for a drive test on the same behavior. |
| **Black-box** | Subprocess/CLI/socket boundary tests with no in-process `sonari` import: hook JSON dispatch, `bin/sonari*` shims, Swift-source compiles, real `.wav` asset reads. 16 files are pure black-box by design (verified — not accidental import misses). | Only for behavior that genuinely lives outside the Python process: compiled artifacts, subprocess boundaries, file-text conventions Claude Code itself consumes. |

House default, same as the template: **prefer extending a Drive test; new Unit tests only for
algorithmic cores.** A wall of passing units can still miss a broken system — that's the
documented Sonari SIGTERM gap the template cites, and it's the same shape as this audit's three
receipts-era bugs.

## How to choose extend-or-new

Before writing any test:

1. **Locate the behavior in `TEST-MAP.md`.** Find the subsystem section (control-cue/mute,
   nav/decisions, chooser, catchup, keepalive, doctor, sessions/persistence, speaker/queue,
   hooks/install, daemon-core, hotkeys/platform, CLI, config/misc, hermeticity guards).
2. **If a file in that cluster already drives the same feature area**, extend it. A new test
   function in an existing file beats a new file — it keeps the cluster's fixtures and setup
   shared instead of re-derived.
3. **Open a new file only when no cluster owns the behavior domain**, or when the existing
   cluster's fixture shape genuinely doesn't fit (rare — check `tests/daemon_helpers.py` and
   `conftest.py` first; most behaviors fit `make_daemon()`).
4. **Never create a `..._fix` / `..._v2` patch-on-patch file next to the original.** This audit's
   P4 work item B existed specifically to undo that anti-pattern: `test_sp3fix_grammar.py`,
   `test_sp3fix_identity.py`, `test_sp3fix_ring.py` were three files layered beside their `sp3_*`
   originals instead of extending them in place — folded back in (2 files' tests moved into their
   spec-anchored home, 1 file renamed, 1 duplicate deleted). If you're tempted to name a file
   `..._fix` or `_v2`, that's the signal to extend the original instead.
5. **A new test file requires a `TEST-MAP.md` update in the same commit.** The red-probe warns
   when this is missed.
6. **State the decision in the task report** — which existing test(s) you extended, or why a new
   file was necessary. This is a tamper-audit input, not paperwork: P4's report names every test
   it deleted/moved/renamed with the justification, and that narrative is what let the reviewer
   trust a 178-line diff touching 28 files as maintenance rather than coverage loss.

## The known blind spot

Two distinct blind spots, both real, both partially closed:

1. **Fixture dishonesty** (E-3's original finding, narrower). `FakeSpeaker.transient()` appended
   every cue kind to the log unconditionally, so an assertion like `speaker.log ==
   [("earcon","choice"), ...]` proved a cue was *requested*, never that a sound would *play* — a
   silently-dead earcon key and a correctly-resolved one produced byte-identical test output.
   **Status: closed** — `tests/daemon_helpers.py`'s `FakeSpeaker` now splits resolved cues
   (`.earcons`) from unresolvable ones (`.silent_cues`), and an autouse `conftest.py` fixture
   (`_no_silent_cues`) fails any test that fires a silent cue, for free, on every test using the
   shared fake. The two local `FakeSpeaker` definitions that reproduced the same bug outside the
   shared harness (`test_blackbox_net.py`, `test_e2e_pipeline.py`) were fixed by P3
   (`audit/p3-fixture-honesty`, commit `a47b8a3`). `test_frontier.py`'s local `_FakeSpeaker` is
   still a bare no-op — no test currently relies on its effect, so it's a latent risk, not a live
   gap; don't add an assertion that depends on it without fixing it first.

2. **Composition blindness** (this charter's REASON TO EXIST, broader, **not closed**). A test
   that asserts one chokepoint fires correctly cannot see that a *sequence* of correct chokepoints
   produces a wrong outcome — a muted-nav that's individually correct but starves a live session's
   decision announce three gestures later, a resume that individually restores frontier but a
   subsequent catch-up press silently buries the pile it promised. The nine CONFIRMED bugs in
   `TEST-MAP.md`'s "Known gaps → Open" section are the live demonstration: found by driving real
   multi-step gesture sequences through `make_daemon()`, not by any per-module mutation pass. No
   mutation score, however complete, catches this class — mutation tells you a Drive test's
   assertions have teeth against small code changes, not that the Drive test's *scenario* covers
   the sequence a real user gesture produces. When you write a new Drive test, prefer a scenario
   that chains at least two real gestures (mute → nav → resume, not just nav) over one that
   exercises a single dispatch in isolation.

## Fixture conventions

- **Sacrificial HOME, always.** Every pytest/mutmut/probe run uses a `$TMPDIR`-rooted sacrificial
  `HOME` via the repo's own repoint (`tests/_isolation.py` / `conftest.py`) — never a hand-rolled
  `mkdtemp`. The by-value-bind class of bug this guards against has destroyed the real install
  twice. `mktemp -d` under `/var/folders` is blocked by the sandbox seatbelt in agent contexts —
  root it under `$TMPDIR` or a repo-local scratch dir instead.
- **`conftest.py`'s 5 autouse fixtures** (`_no_blocking_prompts`, `_isolate_sonari_dir`,
  `_inert_keepalive_seams`, `_real_home_canary`, `_no_silent_cues`) apply to every test
  automatically — don't redefine any of their behavior locally.
- **Never define a local `FakeSpeaker`.** Use `tests/daemon_helpers.py`'s shared one via
  `make_daemon()`/`make_net()` — it's the one wired into the silent-cue drain. A local fake is how
  the fixture-dishonesty bug reproduces itself even after the shared harness was fixed (see Known
  blind spot §1).
- **The `mac` platform-pinning fixture lives in `conftest.py`.** It was copy-pasted three times
  (`test_docs_sync.py`, `test_cli_control.py`, `test_keymap.py`) before P4 hoisted it; use the
  conftest version, don't redefine it locally even for a "just this file" tweak.
- **`tests/fixtures/*.json`** holds static hook-event payloads consumed by `test_fixtures.py`'s
  glob loader and individual hook tests — add new fixture JSON there, not inline dicts, when a
  test needs a realistic Claude Code event payload.

## Suite hygiene

- **Delta accounting:** every task report states tests added/modified/deleted against the size of
  the change. Twelve new tests for a 20-line fix is a reviewable smell, not invisible accretion.
- **Consolidation is legitimate**, with a tamper-audit justification per deleted/moved/renamed
  test — see P4-REPORT.md's "Tamper-audit narrative" section for the pattern: every deletion cites
  the exact surviving test that subsumes it, byte-compared, not asserted from memory.
- **Periodic suite review** using mutation data: tests killing zero mutants are deletion
  candidates (see `zero_kill` in each module's `analysis.json`); tests killing identical mutant
  sets are merge candidates (`clusters`). Re-run the module's mutation score after any cull — a
  dropped score means the deletion took real coverage with it, not just redundant coverage.
- **Node-id reconciliation on rename/move:** if a mutation ledger already scored tests under their
  pre-consolidation names (as `queue`/`cues` did going into P4b), don't silently rewrite the
  ledger — write a join-through map (`test-renames.json` is the precedent) so old-keyed rows stay
  interpretable instead of orphaned or misread as "never scored."

## Done-gates

Mechanics in `PLAYBOOK.md`. Every task that writes or modifies tests, before DONE:

1. **Red-probe** — `~/.claude/scripts/red-probe.sh <base-ref>`: every new/changed test must FAIL
   against base source. Deliberate characterization tests carry the `red-probe: characterization`
   marker and are reported, not blocked.
2. **Mutation gate** — `~/.claude/scripts/mutation-gate.sh <base-ref>`: diff-scoped mutants of the
   changed source; survivors get one bounded kill-loop, then a per-survivor "equivalent
   because…" justification if still standing (the reviewer treats that line as a review object,
   not a formality — `TRIAGE.json`'s equivalent-mutant entries are the model: each names the exact
   grep/read that proved no test can or should catch the mutation).
3. **Delta accounting** — stated in the report, per Suite hygiene above.
4. **`PYTHONDONTWRITEBYTECODE=1` or a `__pycache__` clear** around every mutation run — stale
   `.pyc` has corrupted evidence in both directions on this repo before; every source edit
   (including a `cp`-restore after a manual mutant demo) changes mtime and forces
   cache-invalidation, but only if nothing already wrote a stale compiled file first.

Environment-restricted runs relocate to the controller per the standing rule (report "not run
(sandbox)", never "passed").
