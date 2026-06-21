# Contributing to Sonari

Sonari is a macOS-only tool with a single maintainer (Nima). The workflow rests
on one split: **machines check the portable logic; the maintainer checks the
macOS runtime on real hardware.**

## Branch model

- **`main` is the trunk and is always releasable.** There are no long-lived
  integration branches.
- **Branch off `main`, one concern per branch, short-lived.** Name it
  `area/short-desc`:
  - `macos/...` — macOS backend (`src/sonari/platform/macos/**`)
  - `core/...` — shared core (daemon, assembler, speaker, protocol, keymap)
  - `docs/...`, `test/...` — docs and test-only changes
- **One concern per PR.** If a change spans three layers (e.g. a backend change +
  a UX feature + a review pass), open three PRs, not one. Big multi-layer
  branches are hard to review and hard to trace.
- **Squash-merge into `main`** — every PR becomes a single commit on `main`.
  Commit however you like *inside* your branch; history is squashed at merge.
- **Delete the branch after it merges** (locally and on the remote).

## Ownership

| Area | Owner | Review rule |
|------|-------|-------------|
| Everything (`src/sonari/**`, docs, tests) | Nima | Nima approves |

## Two layers of verification

**1. The logic suite (machine-checkable, runs headless).**
The `pytest` suite uses fakes for audio and hotkeys, so it runs headless. Before
opening a PR, run it:

```
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest -q
```

It must be green before merge.

**2. Runtime acceptance (human, on macOS hardware).**
The suite proves *nothing* about real speech, the daemon crash/interrupt path, the
global-hotkey pump, earcon mixing, or autostart — those are OS-runtime behaviors
only a real machine can confirm. **The maintainer runs the macOS acceptance
checklist on real hardware and signs off before merge.**

## Platform discipline

The platform seam (`src/sonari/platform/`) keeps OS-specific code isolated.
**macOS-specific code stays in `src/sonari/platform/macos/`**, off the shared
core path, so the seam stays a clean boundary.

## A PR merges when

1. It is one concern, branched off `main`.
2. The logic suite is green.
3. The maintainer has approved.
4. If it touches runtime behavior, it has been accepted on real macOS hardware.
5. It is squash-merged, and the branch is deleted.

## Behavior changes

Sonari is an eyes-free tool — changes to core controls (hotkeys, what gets
spoken, default bindings) are user-facing decisions. **Call them out explicitly**
in the PR description (a `⚠️ behavior change` line) rather than burying them in a
feature branch, and raise anything that removes or remaps a default before you
build it.
