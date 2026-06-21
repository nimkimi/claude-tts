# Sonari Stage 2 — Phase 2 (Steps 7–8) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the Stage-2 carve — relocate the global speech ledger onto `SessionState` (its lock-owning home) without speak-path overhead, then collapse the host to its concurrency-core floor by moving the pure handler helpers into their feature modules and deleting the dead forwarding shims.

**Architecture:** Phase 1 already renamed `daemon.py → daemon/host.py` and extracted `features/*`, `server.py`, `registry.py`, `context.py`, `state.py`, `bootstrap.py`, `limits.py`. The speak loop and all kernel ops + handler helpers still live on the fat host. Phase 2 has two steps. **Step 7** moves the 7 global-ledger fields off the host onto `SessionState`; the host's hot path (speak loop + kernel ops) reads/writes them **directly as `self._state._X`** (one cheap attribute load), and **property shims** on the host bridge the old `self._X` name for the cold-path callers (tests, concurrency guards, feature modules on the connection thread). **Step 8** moves the pure text-builder / compute helpers into their single-caller feature modules and deletes the 27 forwarding-shim methods, leaving the host as the concurrency core the spec §4 describes.

**Tech Stack:** Python 3.9-floor (CI), 3.13 dev. stdlib only (`threading`, `socket`, `secrets`). pytest. macOS-only.

---

## Global Constraints

Every task's requirements implicitly include this section. Values copied verbatim from the spec + the standing handoff contracts.

- **The one hard product constraint — speak-path perf.** The per-utterance critical section must not gain overhead. The banked before-number is `scripts/perf_baseline.json` → `mean_ns: 788.7` (`enqueue+pop_next`, N=200000). **Approach decided by the owner after a measured spike:** uniform property shims on the hot path measured **+10%** (884 vs 805 ns) and are REJECTED; the chosen approach (ledger on `SessionState`, hot path re-sourced to `self._state._X`) measured **perf-neutral** (~794 ns). Task 7.1 carries a mandatory before/after perf gate.
- **Behavior-preserving.** Byte-identical speech/earcon output and ordering. **Phase 2 introduces NO user-facing behavior change** (the one approved Step-6 change already landed in Phase 1). The net + the 2 permanent concurrency guards are the proof.
- **The ONE lock, host-created, shared.** `self._lock = threading.Lock()` is created on the host and passed into `SessionState(self._lock)`. The invariant **`daemon._lock is daemon._state._lock` MUST hold** (asserted by `tests/test_daemon_streams.py:329`). `_lock` is NOT one of the relocated fields — it stays exactly as today. Keep the non-reentrant `Lock` (never `RLock`). `state.transaction()` is the only way to hold the lock; features NEVER acquire a lock.
- **The net is SYNCHRONOUS (`drain_once`) and BLIND to thread races.** `tests/test_concurrency_guards.py` (the real-threaded stress test + the deterministic re-entrant-flush test — the M2/L2/M6/M8/H2 guards) is **PERMANENT. NEVER retire it.** Paused/blocking net tests set `daemon._poll_interval = 0`.
- **`MsgType` is a plain class of string constants, NOT an Enum.** `MsgType.TOOL == "tool_announce"` (not `"tool"`). Use `MsgType.*`, never bare-string guesses, in any registry sanity check.
- **`Ctx.session` ≡ `msg.get("session","")`, `Ctx.verbosity` ≡ `config.get("verbosity","everything")`** — feature handlers re-source the preamble from `ctx`; they reach host state/kernel ops via `ctx.host.*`.
- **py39 floor.** Every new/edited `src/sonari` module's first line is `from __future__ import annotations`. `tests/test_daemon_package.py::test_py39_compat` scans non-recursively — no new submodules are created in Phase 2 (only existing ones grow), so no new per-module pins are needed; do not remove existing ones.
- **mock-where-used.** `monkeypatch.setattr(daemon, '_X', …)` only intercepts a call reached as `ctx.host._X` / `self._X`. When a helper becomes a module-level function, its patches MUST be repointed to `sonari.daemon.features.<module>._X` **in the same commit** (this is the Phase-1 `save_config` lesson — see Task 8.4).
- **conftest foot-gun.** `tests/conftest.py` patches by-value path constants (`daemon_host.LOCK_PATH`, `daemon_bootstrap.SINGLETON_PATH`/`_SINGLETON`). Phase 2 does **not** move `LOCK_PATH`, `run()`, or any path constant, so conftest is untouched — but if any task finds itself relocating a path constant, repoint conftest in the same commit.
- **Git.** Work on branch `sonari-stage2-phase2` (already created off `main`). Merge to **LOCAL `main` only** when Phase 2 is done (owner's call). **NEVER `git push` / open a remote PR.** **NEVER** a `claude.ai/code/session` link or footer in any commit message. **`git add` EXACT paths only** — never `-A`/`.`/`-u`. The two untracked files **`.convergence-plan.md` and `docs/getting-started.md` must NEVER be committed.**
- **Gate (every task):** `.venv/bin/python -m pytest -q --ignore=tests/test_kokoro.py` → **750 passed** at the start of Phase 2. Rule = green, and the count never drops. Task 7.1 may *raise* the count (new `SessionState` assertions). **No test is retired in this plan** (see "Test retirement" below).
- **Never use the owner as a test harness.** Verify any runtime yourself with a sacrificial-HOME smoke (`HOME=$(mktemp -d)` → the daemon uses `~/.sonari` under that temp HOME → ephemeral TCP port + isolated singleton flock, fully isolated from the live daemon). **Do NOT run `sonari install`** (it restarts the owner's daily-driver daemon).

### Test retirement (explicit policy)

Spec §10 Step 8 permits retiring "only duplicative white-box tests with a proven net-equivalent." **This plan retires NOTHING.** The white-box daemon tests assert on internal state the synchronous net cannot observe, so almost none have a true net-equivalent, and the DoD (§13) does not require retirement. Any retirement is deferred to the final whole-branch review as an explicit, owner-visible decision (a specifically-named white-box test + the specific net/e2e test that asserts the same behavior); the 5 concurrency guards are never eligible. Until then the gate is strict: **green + 750 (or higher), never lower.**

---

## Key facts the implementer must not re-derive

- **The speak loop is ALREADY in `host.py`** (`_speak_loop` / `_speak_loop_once`, ~lines 618–736). Step 7 does **NOT** move loop code into the file — it relocates the 7 ledger fields *underneath* the unchanged loop and re-sources the loop's field accesses from `self._X` to `self._state._X`. Logic, order, and lock regions are identical; only the storage backing the names changes.
- **The shim recipe (verified by a full-repo sweep + a working spike):**
  - **3 read/write properties** (rebindable scalars the loop reassigns): `_current_item`, `_last_spoken_session`, `_next_id` — each a `@property` getter + a `@X.setter`, both delegating to `self._state`.
  - **4 read-only properties** (mutated in place, never reassigned outside `__init__`): `_paused` (Event), `_pending_heard` (dict), `_streams` (dict), `_wake` (Event) — getter only, returns the live object on `self._state`.
  - No test or non-host source reassigns the 4 mutate-in-place attrs (swept and confirmed), so read-only getters are safe.
- **The re-source rule (Step 7).** Inside `host.py`, every host-internal reference to the 7 names becomes `self._state._<name>`. The property shims are then reached **only from outside `host.py`**. Verification grep after 7.1 (MUST be empty):
  `rg 'self\.(_streams|_next_id|_wake|_pending_heard|_paused|_current_item|_last_spoken_session)\b' src/sonari/daemon/host.py`
  (The property method *definitions* — `def _current_item(self):`, `@_current_item.setter` — do not contain `self._current_item`, so they don't match. `_lock`, `_running`, `_reload_lock` are NOT in the set and stay as-is.)
- **The lift pattern (Step 8) — proven across all 8 families in Phase 1.** To move a host method into a feature module: copy the body verbatim into the feature module as a module-level function; drop `self`/`@staticmethod`; if the body used `self.<host-attr/method>`, add a leading `ctx` (or `host`) parameter and rewrite `self.` → `ctx.host.`; update the calling handler(s) in that same module to call the new module-local function instead of `ctx.host._X`. Add only the imports the moved body needs (each module already has `from __future__ import annotations`).
- **Dispatch is registry-mediated.** `handle_message` → `dispatch(ctx, msg)` → `HANDLERS.get(type, _ignore)(ctx, msg)`. The `_on_*` host methods are dead forwarding shims with **zero callers** (swept and confirmed) — Step 8.1 deletes them with no test impact.
- **cli.py has its OWN `_read_install_record`** (`cli.py:315`), distinct from the daemon's. `tests/test_cli_doctor.py` patches `cli._read_install_record` — Step 8.4 must NOT touch cli.py or those patches.

---

# STEP 7 — Relocate the global ledger onto `SessionState`; re-source the hot path

### Task 7.1: Move the 7 ledger fields to `SessionState`; re-source host hot path; add property shims

**Files:**
- Modify: `src/sonari/daemon/state.py` (SessionState gains the 7 fields)
- Modify: `src/sonari/daemon/host.py` (remove 7 `__init__` assigns; add 7 properties; re-source every host-internal field access to `self._state._X`)
- Modify: `tests/test_daemon_state.py` (assert the new SessionState surface + the property-shim identity)
- Modify: `docs/superpowers/specs/2026-06-21-sonari-architecture-design.md` (record that the measured Option B supersedes the literal "byte-for-byte" wording)
- Gate with: full suite, `tests/test_concurrency_guards.py`, `scripts/perf_baseline.py`

**Interfaces:**
- Consumes: nothing new.
- Produces (relied on by Step 8 and by external callers):
  - `SessionState(lock)` now also initializes, in `__init__`: `self._streams = {}`, `self._next_id = 0`, `self._wake = threading.Event()`, `self._pending_heard = {}`, `self._paused = threading.Event()`, `self._current_item = None`, `self._last_spoken_session = None`. `transaction()` unchanged.
  - `SpeechDaemon` exposes properties: read-only `_streams`, `_pending_heard`, `_paused`, `_wake`; read/write `_current_item`, `_last_spoken_session`, `_next_id` — all backed by `self._state`. `daemon._lock is daemon._state._lock` still holds.

- [ ] **Step 1: Record the fresh same-session perf baseline (before any edit)**

The branch tree currently equals `main`, so this measures the committed (pre-relocation) hot path. Run 3×, note the median `mean_ns` as **BASE**, then restore the banked json (the script overwrites it):

```bash
for i in 1 2 3; do .venv/bin/python scripts/perf_baseline.py 2>&1 | grep '"mean_ns"'; done
git checkout -- scripts/perf_baseline.json
```
Expected: three numbers near ~800 ns. Record the median as BASE (do not commit the json).

- [ ] **Step 2: Give `SessionState` the 7 ledger fields**

Replace the body of `src/sonari/daemon/state.py` with:

```python
from __future__ import annotations

import threading
from contextlib import contextmanager


class SessionState:
    """The lock owner + the global speech ledger.

    Holds the cross-thread fields the speak loop, the connection threads, and the
    hotkey thread all touch under the one lock: the per-session stream registry,
    the pending-heard markers, the in-flight claim, the folder-attribution cursor,
    the id counter, and the pause/wake Events. The host reads/writes these directly
    as ``self._state._X`` on the hot path; property shims on the host bridge the old
    ``self._X`` names for cold-path callers (tests, guards, feature modules).
    """

    def __init__(self, lock):
        self._lock = lock
        self._streams: "dict" = {}
        self._next_id = 0
        self._wake = threading.Event()
        self._pending_heard: "dict" = {}
        self._paused = threading.Event()
        self._current_item = None
        self._last_spoken_session = None

    @contextmanager
    def transaction(self):
        with self._lock:
            yield
```

- [ ] **Step 3: Remove the 7 field assignments from the host `__init__` and add the property shims**

In `src/sonari/daemon/host.py`, in `SpeechDaemon.__init__`, delete these 7 assignment lines (keep `_running`, `_lock`, `_state`, `_reload_lock`, and everything else):

```python
        self._streams: "dict[str, SessionStream]" = {}
        self._next_id = 0
        self._wake = threading.Event()
        ...
        self._pending_heard: dict = {}            # SpeechItem.id -> HistoryEntry
        self._paused = threading.Event()          # play/pause: set == speech halted
        self._current_item = None                 # item being spoken right now
        ...
        self._last_spoken_session = None          # for folder attribution on switch
```

`self._lock = threading.Lock()` and `self._state = SessionState(self._lock)` stay and must remain before any other use. Then, immediately after `__init__` (before `_raise`), add the shim block:

```python
    # --- Ledger shims (Step 7): storage lives on SessionState. The hot path
    # (speak loop + kernel ops) goes through self._state._X directly; these
    # properties bridge the self._X name for cold-path callers (tests, the
    # concurrency guards, feature modules on the connection thread). 3 are
    # read/write (rebindable scalars); 4 are read-only (mutated in place). ---
    @property
    def _streams(self):
        return self._state._streams

    @property
    def _pending_heard(self):
        return self._state._pending_heard

    @property
    def _paused(self):
        return self._state._paused

    @property
    def _wake(self):
        return self._state._wake

    @property
    def _current_item(self):
        return self._state._current_item

    @_current_item.setter
    def _current_item(self, value):
        self._state._current_item = value

    @property
    def _last_spoken_session(self):
        return self._state._last_spoken_session

    @_last_spoken_session.setter
    def _last_spoken_session(self, value):
        self._state._last_spoken_session = value

    @property
    def _next_id(self):
        return self._state._next_id

    @_next_id.setter
    def _next_id(self, value):
        self._state._next_id = value
```

- [ ] **Step 4: Re-source every host-internal access of the 7 fields to `self._state._X`**

In `host.py`, replace every `self._<name>` with `self._state._<name>` for the 7 names — in the kernel ops (`_alloc_id`, `_stream`, `_enqueue`, `_drop_pending`, `_attributed_text`, `note_spoken`, `_waiting_target`), the speak loop (`_speak_loop`, `_speak_loop_once` — both the paused and normal branches: the `pop`/claim of `_current_item`, the `_streams.get`, the `cancel_epoch` snapshot's neighbours, the `_pending_heard.pop`, the `prev = ... _last_spoken_session` snapshot and its rollback, the `_wake.wait/.clear`), `_resume` (`_paused`, `_wake`), and `stop()` (`_wake.set`). Do NOT touch `_lock`, `_running`, or `_reload_lock`. Example shape (the normal speak-loop claim region stays under the same lock, byte-for-byte except the storage backing):

```python
        with self._lock:
            fg = self.sessions.foreground()
            st = self._state._streams.get(fg)
            item = st.queue.pop_next() if st is not None else None
            self._state._current_item = item
            cancel_epoch = self.speaker.cancel_epoch()
            ist = self._state._streams.get(item.session) if item is not None else None
            ...
            prev = self._state._last_spoken_session
```

- [ ] **Step 5: Verify the re-source is complete**

Run (MUST print nothing — every host-internal access now goes through `self._state`):

```bash
rg 'self\.(_streams|_next_id|_wake|_pending_heard|_paused|_current_item|_last_spoken_session)\b' src/sonari/daemon/host.py
```
Expected: no output. (Matches inside the property bodies are written `self._state._X`, and the `def`/`@X.setter` lines have no `self.` prefix, so they don't match.) If any line prints, re-source it.

- [ ] **Step 6: Update `tests/test_daemon_state.py` to assert the new surface**

The existing file asserts only `state._lock is lock`. Add assertions that `SessionState` now owns the 7 fields with the right initial values and that the host's shims read/write through to it. Add to `tests/test_daemon_state.py`:

```python
def test_sessionstate_owns_the_global_ledger():
    import threading
    from sonari.daemon.state import SessionState
    s = SessionState(threading.Lock())
    assert s._streams == {}
    assert s._pending_heard == {}
    assert s._next_id == 0
    assert s._current_item is None
    assert s._last_spoken_session is None
    assert not s._paused.is_set()
    assert not s._wake.is_set()


def test_host_ledger_shims_delegate_to_state(make_daemon):
    daemon = make_daemon()
    # read-only shims return the SAME live object as state
    assert daemon._streams is daemon._state._streams
    assert daemon._pending_heard is daemon._state._pending_heard
    assert daemon._paused is daemon._state._paused
    assert daemon._wake is daemon._state._wake
    # read/write shims write through to state
    daemon._current_item = "sentinel"
    assert daemon._state._current_item == "sentinel"
    daemon._last_spoken_session = "sess-x"
    assert daemon._state._last_spoken_session == "sess-x"
    daemon._next_id = 41
    assert daemon._alloc_id() == 42
    assert daemon._state._next_id == 42
```

If `tests/test_daemon_state.py` has no `make_daemon` fixture available, build the daemon inline the way `tests/test_daemon_streams.py` does (a `Speaker`/fake, a `SessionManager`, a `DEFAULTS` copy) — match the neighbouring test file's construction idiom; do not invent a new fixture.

- [ ] **Step 7: Run the full suite + the concurrency guards**

```bash
.venv/bin/python -m pytest -q --ignore=tests/test_kokoro.py
.venv/bin/python -m pytest -q tests/test_concurrency_guards.py
```
Expected: full suite **≥ 750 passed** (the 2 new SessionState tests raise it); concurrency guards **2 passed**. If `test_daemon_streams.py::...lock identity...` fails, the lock was wrongly relocated — `_lock` must stay host-created and shared, not moved onto SessionState's own `Lock()`.

- [ ] **Step 8: Run the perf gate (the hard constraint)**

```bash
for i in 1 2 3; do .venv/bin/python scripts/perf_baseline.py 2>&1 | grep '"mean_ns"'; done
git checkout -- scripts/perf_baseline.json
```
Record the median as **AFTER**. **Assert `AFTER <= BASE * 1.03`** (no systematic regression; Option B measured ~794 vs ~805 ns — neutral). Do NOT commit the json (it stays the historical before-number). **If AFTER regresses > 3%, STOP and report** — a hot-path access was likely left on a property shim; re-check Step 5's grep and the loop/kernel re-source.

- [ ] **Step 9: Note the measured-Option-B decision in the spec**

In `docs/superpowers/specs/2026-06-21-sonari-architecture-design.md`, append to §5 (after the "Speak-path latency is unchanged" paragraph) a short note:

```markdown
> **Phase-2 measured correction (2026-06-21).** Relocating the ledger to
> `SessionState` behind *byte-identical property shims* measured **+10%** on the
> `enqueue+pop` hot path (884 vs 805 ns) — over the perf constraint. The
> shipped approach keeps the ledger on `SessionState` but re-sources the host's
> hot path (speak loop + kernel ops) to `self._state._X` (one attribute load,
> not a descriptor call); property shims remain only for cold-path callers.
> Measured perf-neutral (~794 ns). The loop's lock regions are therefore
> logically identical, not literally byte-identical — verified by the permanent
> concurrency guards + this measured gate.
```

- [ ] **Step 10: Commit**

```bash
git add src/sonari/daemon/state.py src/sonari/daemon/host.py tests/test_daemon_state.py docs/superpowers/specs/2026-06-21-sonari-architecture-design.md
git commit -m "refactor(daemon): relocate global ledger onto SessionState; re-source host hot path

Move _streams/_next_id/_wake/_pending_heard/_paused/_current_item/
_last_spoken_session onto SessionState. Host hot path (speak loop + kernel
ops) reads/writes self._state._X directly; property shims bridge self._X for
cold-path callers (tests, concurrency guards, features). Lock stays host-
created and shared. Measured perf-neutral (~794ns vs ~805ns baseline); the
byte-identical-shim alternative measured +10% and was rejected."
```

---

### Task 7.2: On-Mac sacrificial-HOME runtime smoke

**Files:** none modified (verification task). The controller (not a subagent — audio/runtime can't be verified by a subagent, and the owner is never the harness) runs this after 7.1 is reviewed clean.

> **This IS the DoD's "on-Mac `sonari:doctor` smoke", in its faithful form.** A literal `sonari doctor` under a no-install sacrificial HOME reports *not-installed* (install is forbidden — it restarts the owner's daemon), so doctor cannot read green here. Phase 1 used the same startup/bind/serve smoke for the same reason; this follows that precedent rather than redefining the gate.

**Interfaces:** Consumes the merged 7.1 daemon. Produces a recorded smoke result for the ledger.

- [ ] **Step 1: Start the relocated daemon under an isolated HOME**

Drive a fresh daemon under a throwaway HOME so it binds an ephemeral port + its own singleton flock, fully isolated from the owner's live daemon. Do NOT run `sonari install`. Example (adjust the entrypoint to the repo's actual daemon-run path, e.g. `python -m sonari.daemon` or the `bootstrap.main` path):

```bash
TMPHOME="$(mktemp -d)"; export TMPHOME
HOME="$TMPHOME" .venv/bin/python -c "
import os, threading, time
from sonari.daemon import SpeechDaemon
from sonari.sessions import SessionManager
from sonari.config import DEFAULTS

class _NullSpeaker:
    rate = DEFAULTS['rate']
    def speak(self, text, cancel_epoch=None): return True
    def cancel_epoch(self): return 0
    def cancel(self): pass
    def earcon(self, kind): pass
    def set_rate(self, r): pass
    def set_voice(self, v): pass

sessions = SessionManager(); sessions.set_foreground('smoke')
cfg = {k:(v.copy() if isinstance(v,dict) else v) for k,v in DEFAULTS.items()}
d = SpeechDaemon(_NullSpeaker(), sessions, cfg)
t = threading.Thread(target=d.run, daemon=True); t.start()
time.sleep(0.5)
# fabricated fresh session — drive a prose message through the LOCKED entry
# (_handle_message_guarded wraps state.transaction(), matching the production
# socket path; calling the bare handle_message would race the speak loop's
# unlocked pop -> 'dict changed size'). Mirror test_blackbox_net.py's _msg if
# the literal schema below drifts (PROTOCOL_VERSION / MsgType.PROSE).
d._handle_message_guarded({'v':1,'type':'prose','session':'smoke','delta':'hello. ','index':0,'final':True})
time.sleep(0.3)
assert d._server.is_alive(), 'server thread died'
d.stop(); t.join(2.0)
assert not t.is_alive(), 'daemon did not shut down'
print('SMOKE OK: bind/serve/handle/stop clean under', os.environ['HOME'])
"
rm -rf "$TMPHOME"
```
Expected: `SMOKE OK: ...`. (Use the `MsgType`/protocol-version values the repo expects; mirror `test_blackbox_net.py`'s message construction if the literal above drifts from the real schema.)

- [ ] **Step 2: Confirm isolation**

Confirm the smoke used the temp HOME's `~/.sonari` (ephemeral port + separate flock), never the owner's `~/.sonari`. Record the result in `.git/sdd/progress.md`. No commit (no files changed).

---

# STEP 8 — Collapse the host to its concurrency-core floor

> **Spec §4 deviations (deliberate, noted for the owner).** Three helper groups the §4 table nominally assigns to feature modules **stay on the host** because they are concurrency-core / service-integration / lifecycle, not per-message logic: (a) `_resume` (touches `_paused`/`_wake`), (b) `_raise` + `_raise_failed` (lazy RaiseService builder cached on the host + an off-path lock-acquiring callback), (c) the hotkey lifecycle `_start_hotkeys`/`_stop_hotkeys`/`_reload_hotkeys`/`_dispatch_hotkey` (bound to `run()`/`stop()` and registered as the platform callback — like the speak loop, lifecycle stays on the host). The RELOAD_KEYMAP *handler* (`on_reload_keymap`) already lives in `features/hotkeys.py` and calls `ctx.host._reload_hotkeys()` — that boundary is correct and unchanged. This matches §4's host description ("the concurrency core — lock, speak loop, stream registry, kernel ops, dispatch entry").

### Task 8.1: Delete the 27 `_on_*` forwarding shims

**Files:** Modify `src/sonari/daemon/host.py`.

**Interfaces:** none. These methods have **zero callers** in `src/` or `tests/` (verified: `rg '\._on_[a-z]' src tests` → empty). Dispatch goes through the registry, not these.

- [ ] **Step 1: Re-confirm zero callers**

```bash
rg -n '\._on_[a-z]' src tests
```
Expected: no output. If anything prints, STOP — a caller exists; do not delete.

- [ ] **Step 2: Delete the shim methods**

Delete all `def _on_*(self, msg): return on_*(self._ctx.bind(msg), msg)` methods and their section-comment banners from `host.py` (the block roughly spanning the "Prose family handlers" comment through `_on_ping`, ~lines 352–452). Leave `handle_message`, `_handle_message_guarded`, `_dispatch_hotkey`, and everything else intact.

- [ ] **Step 3: Run the suite**

```bash
.venv/bin/python -m pytest -q --ignore=tests/test_kokoro.py
```
Expected: **≥ 752 passed** (the count is 752 from Task 7.1 onward — it added two `SessionState` tests; this task changes it by 0). If you see 752, that is correct — do NOT "fix" it down to 750.

- [ ] **Step 4: Commit**

```bash
git add src/sonari/daemon/host.py
git commit -m "refactor(daemon): delete the 27 dead _on_* forwarding shims (zero callers)"
```

### Task 8.2: Move the decision text-builders into `features/decisions.py`

**Files:** Modify `src/sonari/daemon/host.py`, `src/sonari/daemon/features/decisions.py`.

**Interfaces:** Move these host methods into `decisions.py` as module-level functions (the lift pattern). Zero test references to any of them (swept), so no test migration.

| Host method (current) | Kind | New module function signature |
|---|---|---|
| `_choice_text(msg)` | `@staticmethod` | `def _choice_text(msg) -> str` |
| `_plan_text(msg)` | `@staticmethod` | `def _plan_text(msg) -> str` |
| `_permission_text(msg)` | `@staticmethod` | `def _permission_text(msg) -> str` |
| `_choice_notes(msg)` | `@staticmethod` | `def _choice_notes(msg) -> str` |
| `_selection_cue(self, session, verbosity)` | instance | `def _selection_cue(ctx, session, verbosity) -> str` (body `self._stream(...)` → `ctx.host._stream(...)`) |

- [ ] **Step 1: Copy the five bodies verbatim into `decisions.py`** as module-level functions per the table (drop `@staticmethod`/`self`; for `_selection_cue` add the leading `ctx` param and rewrite `self.` → `ctx.host.`). Place them above the handlers that use them.
- [ ] **Step 2: Update the `decisions.py` handlers** — replace every `ctx.host._choice_text(...)`, `ctx.host._plan_text(...)`, `ctx.host._permission_text(...)`, `ctx.host._choice_notes(...)`, `ctx.host._selection_cue(...)` with the module-local `_choice_text(...)`, `_plan_text(...)`, `_permission_text(...)`, `_choice_notes(...)`, `_selection_cue(ctx, ...)`.
- [ ] **Step 3: Delete the five methods from `host.py`.**
- [ ] **Step 4: Run the suite** — `.venv/bin/python -m pytest -q --ignore=tests/test_kokoro.py` → **≥ 752 passed** (unchanged from Task 7.1). (Targeted: `tests/test_daemon_decisions.py` covers these paths.)
- [ ] **Step 5: Commit**
```bash
git add src/sonari/daemon/host.py src/sonari/daemon/features/decisions.py
git commit -m "refactor(daemon): move decision text-builders into features/decisions.py"
```

### Task 8.3: Move `_nav`/`_nav_response` into `features/navigation.py` and `_waiting_target` into `features/focus.py`

**Files:** Modify `src/sonari/daemon/host.py`, `src/sonari/daemon/features/navigation.py`, `src/sonari/daemon/features/focus.py`. Two commits (one per module).

**Interfaces:** All three are instance methods called from exactly one feature module's handler, with zero test references (swept). They run UNDER the held lock (their callers hold `transaction()`), acquire no lock themselves, and never mutate the rebindable global scalars — safe to move.

| Host method | New module function | Body rewrite |
|---|---|---|
| `_nav(self, session, to)` | `features/navigation.py`: `def _nav(ctx, session, to) -> None` | `self.` → `ctx.host.` (`_stream`, `history`, `speaker`, `_enqueue`, `_drop_pending`) |
| `_nav_response(self, session, direction)` | `features/navigation.py`: `def _nav_response(ctx, session, direction) -> None` | same |
| `_waiting_target(self, exclude)` | `features/focus.py`: `def _waiting_target(ctx, exclude)` | `self._streams` → `ctx.host._streams` (read-only iteration under lock) |

- [ ] **Step 1 (navigation):** copy `_nav` + `_nav_response` bodies verbatim into `navigation.py` as module functions (add leading `ctx`, rewrite `self.` → `ctx.host.`); update `on_nav` to call the module-local `_nav(ctx, ...)`/`_nav_response(ctx, ...)`; delete both methods from `host.py`.
- [ ] **Step 2:** `.venv/bin/python -m pytest -q --ignore=tests/test_kokoro.py` → **≥ 752 passed** (unchanged from Task 7.1; `tests/test_daemon_nav.py` covers this); then commit:
```bash
git add src/sonari/daemon/host.py src/sonari/daemon/features/navigation.py
git commit -m "refactor(daemon): move two-level nav (_nav/_nav_response) into features/navigation.py"
```
- [ ] **Step 3 (focus):** copy `_waiting_target` into `focus.py` as `def _waiting_target(ctx, exclude)` (`self._streams` → `ctx.host._streams`); update `on_jump_waiting` to call the module-local function; delete the method from `host.py`.
- [ ] **Step 4:** `.venv/bin/python -m pytest -q --ignore=tests/test_kokoro.py` → **≥ 752 passed** (unchanged from Task 7.1; `tests/test_daemon_focus_follow.py` + jump-waiting net coverage); then commit:
```bash
git add src/sonari/daemon/host.py src/sonari/daemon/features/focus.py
git commit -m "refactor(daemon): move _waiting_target into features/focus.py"
```

### Task 8.4: Move the setup-health cluster into `features/lifecycle.py` and repoint its ~15 test patches

**Files:** Modify `src/sonari/daemon/host.py`, `src/sonari/daemon/features/lifecycle.py`, and 5 test files. **This is the only test-migration-heavy task** — the patches MUST move in the same commit as the code (mock-where-used).

**Interfaces:** Move four host methods into `lifecycle.py` (co-located — `lifecycle` is the only caller, via `on_session_start`→`_maybe_guide_setup`). Spec §4 nominally placed setup-health in `control.py`; **co-locating in `lifecycle.py` is the deliberate choice** (the only caller; splitting would force a cross-feature import for zero benefit).

| Host method | Kind | New `lifecycle.py` function |
|---|---|---|
| `_maybe_guide_setup(self, session, plugin_version)` | instance (uses `_stream`/`_enqueue`) | `def _maybe_guide_setup(ctx, session, plugin_version) -> None` (`self.` → `ctx.host.`; calls `_setup_health(plugin_version)` module-local) |
| `_setup_health(self, plugin_version)` | instance, but body uses NO `self` state (only the two static helpers) | `def _setup_health(plugin_version)` — **no `ctx`/`self`**; calls `_read_install_record()`/`_launcher_present()` module-local |
| `_read_install_record()` | `@staticmethod` | `def _read_install_record()` |
| `_launcher_present()` | `@staticmethod` | `def _launcher_present() -> bool` |

> Intra-module calls resolve by bare name through the module globals, so a test that does `monkeypatch.setattr('sonari.daemon.features.lifecycle._launcher_present', …)` is seen by `_setup_health`'s call to `_launcher_present()`, and patching `lifecycle._setup_health` is seen by `_maybe_guide_setup`'s call — the standard mock-where-used chain, intact post-move.

- [ ] **Step 1: Move the four bodies** verbatim into `lifecycle.py` per the table. `_maybe_guide_setup` (needs `ctx` for `ctx.host._stream`/`ctx.host._enqueue`) calls `_setup_health(plugin_version)`; `_setup_health` (no params beyond `plugin_version`) calls `_read_install_record()`/`_launcher_present()`. Update wherever lifecycle calls `ctx.host._maybe_guide_setup(...)` to the module-local `_maybe_guide_setup(ctx, ...)`. `_read_install_record` needs `INSTALL_RECORD_PATH` and `_launcher_present` needs the platform import — add to `lifecycle.py`: `from sonari.paths import INSTALL_RECORD_PATH` and keep the body-local `from sonari.platform import get_platform` exactly as the host had it. Delete the four methods from `host.py` (and the now-unused `INSTALL_RECORD_PATH` import on the host **only if** nothing else there uses it — grep first: `rg 'INSTALL_RECORD_PATH' src/sonari/daemon/host.py`).

- [ ] **Step 2: Repoint the test patches to `sonari.daemon.features.lifecycle.*`** — in the SAME commit. The references (swept) are:
  - `tests/test_daemon_setup_health.py`: direct calls `daemon._setup_health("0.4.0")` (lines ~17,27,38,48,58,69) and `daemon._read_install_record()` (~79) → `from sonari.daemon.features import lifecycle` then `lifecycle._setup_health("0.4.0")` / `lifecycle._read_install_record()` (neither takes `ctx`); and the monkeypatches `setattr(daemon,'_launcher_present',…)` / `setattr(daemon,'_setup_health',…)` / `setattr(daemon,'_read_install_record',…)` (~16,26,37,47,57,68,96,109,116,125,140) → `monkeypatch.setattr('sonari.daemon.features.lifecycle._launcher_present', …)` etc.
  - `tests/test_daemon_control.py:88`, `tests/test_blackbox_net.py:66`, `tests/test_daemon_focus_follow.py:40`, `tests/test_e2e_pipeline.py:80`: each `monkeypatch.setattr(daemon,'_setup_health',…)` (silences the guide cue) → `monkeypatch.setattr('sonari.daemon.features.lifecycle._setup_health', …)`. **Note the new fake's signature drops `self`** — it must accept `(plugin_version)`, not `(self, plugin_version)`.
  - **Do NOT touch `tests/test_cli_doctor.py`** — it patches `cli._read_install_record` (a different module's function).

- [ ] **Step 3: Run the full suite** (many files touched):
```bash
.venv/bin/python -m pytest -q --ignore=tests/test_kokoro.py
```
Expected: **≥ 752 passed** (unchanged from Task 7.1). If `test_daemon_setup_health.py` fails on a patch target, the monkeypatch still points at the host attr (now gone) instead of `features.lifecycle` — fix the target.

- [ ] **Step 4: Commit**
```bash
git add src/sonari/daemon/host.py src/sonari/daemon/features/lifecycle.py tests/test_daemon_setup_health.py tests/test_daemon_control.py tests/test_blackbox_net.py tests/test_daemon_focus_follow.py tests/test_e2e_pipeline.py
git commit -m "refactor(daemon): move setup-health cluster into features/lifecycle.py; repoint patches"
```

---

## Definition of done (Phase 2)

- The 7 global-ledger fields live on `SessionState`; the host reaches them via `self._state._X` on the hot path with property shims for cold callers; `daemon._lock is daemon._state._lock` holds.
- The perf micro-benchmark shows no speak-path regression vs the banked baseline (AFTER ≤ BASE × 1.03; recorded in the ledger).
- The host is at its concurrency-core floor: no `_on_*` shims; the pure text-builders + nav + `_waiting_target` + setup-health live in their feature modules; the host keeps only the lock, speak loop, stream registry, kernel ops, dispatch entry, raise integration, and hotkey lifecycle (the §4 deviations are noted above).
- Full suite green (≥ 750), the 2 permanent concurrency guards green, the on-Mac sacrificial-HOME smoke clean.
- No test retired. No user-facing behavior change. Work on `sonari-stage2-phase2`, merged to local `main` only on the owner's go (never pushed; no remote PR; no session-link in commits).

## Self-review checklist (run before dispatching Task 1)

- Spec coverage: §7 state ownership → 7.1; §5 perf gate → 7.1 Steps 1/8; §10 Step 7 → Task 7.1+7.2; §10 Step 8 → 8.1–8.4; §13 DoD → above. ✅
- Type/name consistency: the 7 field names, the 3 r/w + 4 read-only split, and `ctx`-param signatures match the swept evidence. ✅
- No placeholders: every move has exact source method, target module, signature, and call-site update. ✅
