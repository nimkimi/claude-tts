# Sonari Stage 2 — Phase 1 (rest): Server extraction + feature modules + the approved behavior change (Implementation Plan)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish Phase 1 of the Sonari daemon decomposition — carve the transport/server out of the host, lift each message-handler family out of `host.py` into a `features/*.py` module, and apply the one owner-approved behavior change — without changing any speech/earcon behavior except that single approved change.

**Architecture:** Feature-primary decomposition (per the approved spec `docs/superpowers/specs/2026-06-21-sonari-architecture-design.md`). This plan covers spec **Steps 4–6** (the remainder of Phase 1; Steps 0–3 are done and green on this branch). Step 4 extracts a `Server` that owns the localhost-TCP transport (socket/accept/bounded conn-pool/M8 permit recovery/token handshake/newline framing) and calls back into the host's *locked* dispatch entry. Step 5 lifts the 27-branch dispatch's `_on_*` bodies — already grouped behind `@handler` thunks in Step 3 — into eight `features/*.py` modules, one family at a time, behind forwarding shims that keep every existing caller green. Step 6 applies the approved `SET_VOICE`/`SET_VERBOSITY` validation and the `_signal_speak_failure` log-only fix, each as its own commit. The riskiest carve — relocating the speak loop + state — is **Phase 2 (spec Steps 7–8), a later plan**; it is explicitly out of scope here.

**Tech Stack:** Python 3.9+ (forward-ref string type hints), pytest, `threading`, macOS (`say`/`afplay`). No new runtime dependencies.

## Global Constraints

*Every task's requirements implicitly include this section.*

- **Repo / branch:** `/Users/Nima.Hakimi/Projects/private/claude-tts`, branch `sonari-stage2-architecture` (HEAD `3ed0259` at plan time, 23 ahead of local `main`). **Local commits only — NEVER `git push` or open a remote PR** (`origin/main` is far behind; a push drags everything). **NEVER** put a `claude.ai/code/session` link or any Claude-session footer in a commit message (hard rule).
- **`git add` EXACT paths only.** Every commit stages the precise files it changed by path — **never `git add -A` / `.` / `-u`**. Two untracked files are present in the working tree (`​.convergence-plan.md`, `docs/getting-started.md`) and **must NEVER be committed** — exact-path staging is what keeps them out.
- **Suite gate command (run at the end of every task):** `.venv/bin/python -m pytest -q --ignore=tests/test_kokoro.py`. The gate is **full suite GREEN (zero failures)**, and the pass-count **never drops** — a behavior-preserving move keeps the count; a task that adds pins raises it. Single test: `.venv/bin/python -m pytest tests/test_x.py::test_y -v`.
- **Test-count baseline:** **730** entering this plan (the Phase-1 net + scaffolding result). Each task states the new pins it adds; trust the **rule** (green + never-drops) over any absolute number — reconcile if your machine differs, then proceed.
- **Behavior-preserving** through Steps 4–5: speech/earcon output + ordering stay byte-identical, proven by `tests/test_blackbox_net.py` (the synchronous `drain_once` net) + the two permanent guards in `tests/test_concurrency_guards.py`. The ONLY intentional behavior change in this entire plan is **Step 6.1** (`SET_VOICE`/`SET_VERBOSITY` validation). **NEVER retire the two concurrency guards.**
- **Registration-order is the per-family gate (Step 5).** `daemon/__init__.py` calls `assert_complete([...27 MsgType...])` at import time (line ~11), AFTER `from sonari.daemon.host import SpeechDaemon` (line 3). A `@handler` registers only as an import side-effect. So when a family's handlers move into `features/<family>.py`, **`host.py` must import that module** (e.g. `from sonari.daemon.features import <family>`) so the import chain `__init__ → host → features/<family> → registry` registers all 27 keys before `assert_complete` runs. Forget it and the gate is a hard import-time `AssertionError` — by design.
- **Forwarding shims stay through Phase 1; test retirement is deferred to Phase 2 / Step 8.** Each Step-5 family lift leaves the host `_on_*` methods as one-line forwarding shims (spec §12: "deleted in Step 8") and **deletes no behavior tests** — every existing test stays green by routing through the registry to the new feature handler. This keeps the "green + never-drops" gate intact and concentrates the net-vs-white-box retirement judgment into Step 8's deliberate sweep (which §10-Step-8 already authorizes). *(The only test edits in Step 5 are mechanical patch-target repoints in the same commit as the move — the `save_config` and `_on_ping` repoints in Task 5.1; see "Patch-target repoints" under the lift rule.)* Note this plan moves the message **handlers** into `features/*.py`; the co-located **helpers** §4 also lists there (the decision text builders, `_seek_and_play`, `_waiting_target`, setup-health) stay host-owned through Phase 1 and move in Step 8 — so the feature modules are intentionally not yet §4-complete at the end of this plan (authorized by §12 + §10-Step-8).
- **Python 3.9 target:** every NEW `src/sonari` module's first code line is `from __future__ import annotations`; type hints are forward-ref **strings**. `tests/test_py39_compat.py` scans `src/sonari` **non-recursively**, so each new `daemon/server.py` and `daemon/features/*.py` submodule needs an explicit per-module future-import pin added to `tests/test_daemon_package.py` (the tasks include them; do NOT make the scanner recursive).
- **Never use the owner as a test harness.** Verify any runtime yourself via a sacrificial-HOME smoke (`SONARI_DIR = Path.home()/.sonari` is HOME-derived; run under `HOME=$(mktemp -d)` for an ephemeral TCP port + separate singleton flock, fully isolated from the live daemon). Do **NOT** run `sonari install` (it restarts the owner's running daily-driver daemon).
- **venvs:** `.venv` (py3.13) is the gate interpreter; `.venv39` (py3.9) exists for compat spot-checks. `tests/test_kokoro.py` needs the optional `[kokoro]` extra and is excluded by the gate.

---

## Orientation: the current shape (verified against source at HEAD `3ed0259`)

`src/sonari/daemon/host.py` is **1366 lines**, one class `SpeechDaemon`. Dispatch is already registry-mediated (Step 3):

`handle_message(msg)` (host.py:336-338) binds `self._ctx` to `msg` and calls `dispatch(self._ctx, msg)` → `registry.HANDLERS.get(msg["type"], _ignore)(ctx, msg)` → a module-level `@handler` **thunk** (host.py:1197-1366) of the form `def _h_x(ctx, msg): return ctx.host._on_x(msg)` → the host method `_on_x(self, msg)` that holds the real logic.

The thunks are already grouped by feature family with comment banners naming their Step-5 target module (`features/prose.py`, `features/decisions.py`, `features/navigation.py`, `features/playback.py`, `features/focus.py`, `features/lifecycle.py`, `features/hotkeys.py`, `features/control.py`). `Ctx` (context.py) already exposes `.host/.speaker/.sessions/.config/.history/.session/.verbosity` and `.bind(msg)`; **`.session` and `.verbosity` already return exactly the duplicated preamble values** (`msg.get("session","")` and `config.get("verbosity","everything")`), so the Step-5 consolidation target exists. `SessionState` (state.py) exposes only `.transaction()` (the lock boundary). The transport lives entirely on the host: `run`/`_accept_loop`/`_handle_conn`/`_handle_conn_guarded`/`_spawn_conn_handler`/`_handle_message_guarded`/the `_conn_sem` `BoundedSemaphore`/`stop`. `server.py` and the `features/` package do **not** exist yet.

The speak loop + kernel ops + all ledger fields (`_current_item`, `_last_spoken_session`, `_pending_heard`, `_paused`, `_wake`, `_running`, `_streams`, `_next_id`) **stay on the host** in this plan — their relocation is Phase 2 / Step 7. Because state stays host-owned, feature handlers reach it via `ctx.host._current_item` etc. with no property shim — **property shims are a Phase-2 concern, not needed here.**

---

## Step 4 — Extract the transport into `Server`

### Task 4.1 — Carve `server.py` (socket / accept / conn-pool / M8 / handshake / framing); host keeps `run()`, the lockfile, and the locked dispatch entry

**Why:** The transport is a cohesive, lock-independent unit (the spec's L0) tangled into the host today. Extracting it shrinks the host toward its concurrency-core floor and gives the socket/handshake/framing/back-pressure their own testable home. The host keeps `run()`, `ensure_sonari_dir`, token generation, and the lockfile write/unlink — so **`LOCK_PATH` stays imported in `host.py` and `tests/conftest.py` needs NO repoint** (the conftest patches `daemon_host.LOCK_PATH`; keeping `run()` on the host keeps that patch valid). The host hands the `Server` its **locked dispatch entry** (`_handle_message_guarded`, which opens `state.transaction()` around `handle_message`) as a callback, and the **same `_running` Event** it shares with the speak loop and `stop()`.

**Goal.** All of `_accept_loop`, `_handle_conn`, `_handle_conn_guarded`, `_spawn_conn_handler`, and the `_conn_sem` `BoundedSemaphore` move verbatim into `src/sonari/daemon/server.py`. `_handle_message_guarded` STAYS on the host (it is the locked dispatch entry passed to the Server). `run()` and `stop()` stay on the host and delegate the transport to the Server. Behavior — including the exact shutdown coordination and the token-handshake-before-first-message ordering — is byte-identical.

**Server / lifecycle inventory carried in from the read (do not re-derive):**
- Shared `_running` Event (host.py:43): read by `_accept_loop` (`while self._running.is_set()`), by `_handle_conn`'s inner read loop, by the speak loop, and by `run()`'s join loop; cleared by `stop()`. **The Server MUST receive and use this SAME object — never its own flag.**
- `_conn_sem = threading.BoundedSemaphore(_MAX_CONN_THREADS)` (host.py:57): acquired non-blocking in `_spawn_conn_handler`; released in `_handle_conn_guarded`'s `finally` (normal path) AND in `_spawn_conn_handler`'s `except` (the M8 spawn-failure recovery — the permit the never-run handler would have released). **Both release sites + the acquire are one matched set and must live together in the Server.**
- Token: generated in `run()` as `self._token = secrets.token_hex(32)`; compared in `_handle_conn`'s handshake. The Server reads it lazily via an injected `token_provider`.
- `LOCK_PATH`, `ensure_sonari_dir`, `transport.write_lockfile`, `os.unlink(LOCK_PATH)`: **stay in host `run()`**.

**TDD order — write the pins first, watch them fail, then implement:**

1. **`tests/test_daemon_package.py`** — extend the py39 future-import pin set to include the new `src/sonari/daemon/server.py`.

2. **`tests/test_daemon_server.py`** (new) — unit-pin the extracted `Server` directly, with a fake dispatch + a real `threading.Event` for `running` and an in-process socket pair (or a loopback connect to a bound `Server`):
   - **Handshake rejects a wrong token:** a peer whose first line ≠ the token gets dropped; dispatch is never called.
   - **Handshake accepts + frames:** a peer that sends `token\n` then `{json}\n` causes exactly one `dispatch(msg)` call with the decoded dict; a non-None reply is `encode`d back; a second `{json}\n` on the same connection dispatches again.
   - **Same-packet message after token:** `token\n{json}\n` in one `recv` still dispatches the buffered message (pins the "process already-buffered" loop).
   - **Conn cap:** with `_conn_sem` exhausted, a new connection is closed without spawning a handler (`_spawn_conn_handler` returns `False`).
   - **M8 recovery:** monkeypatch `threading.Thread` (or the spawn path) to raise on `.start()`; assert the permit count is restored (no leak) and the connection is closed.

3. **Repoint the two existing tests that reach the moved transport symbols directly** (same commit as the move — the carve relocates `_MAX_CONN_THREADS`, `_conn_sem`, `_spawn_conn_handler`, `_handle_conn`, `_handle_conn_guarded`, `_accept_loop` off the host, and turns `self._server` from a raw socket into a `Server`, so these break unless repointed):
   - **`tests/test_daemon_conn.py`** — line 3 `from sonari.daemon.host import _MAX_CONN_THREADS` → `from sonari.daemon.server import _MAX_CONN_THREADS`. Repoint the three Server-internal tests to drive the Server: `daemon._conn_sem` → `daemon._server._conn_sem`; `daemon._spawn_conn_handler(...)` → `daemon._server._spawn_conn_handler(...)`; `daemon._handle_conn = raising` → `daemon._server._handle_conn = raising`; `daemon._handle_conn_guarded(object())` → `daemon._server._handle_conn_guarded(object())`. `test_handle_message_guarded_contains_exceptions` is UNCHANGED — it patches `daemon.handle_message` and calls `daemon._handle_message_guarded`, both of which stay on the host.
   - **`tests/test_daemon_loop.py`** — the `_make_inet_daemon` helper (lines 103-125) hand-builds a raw socket, sets `daemon._server = srv`, and spawns `daemon._accept_loop`. Repoint it to drive the `Server`: set `daemon._token = "testtoken"`, `daemon._running.set()`, then `port = daemon._server.bind()` and (after starting the speak thread) `daemon._server.serve()`; return `port` with `host = "127.0.0.1"`. (The `Server`'s `token_provider=lambda: self._token` reads the test's `daemon._token`, so the handshake still expects `b"testtoken\n"`.) The three `test_handle_conn_*` round-trip tests then pass unchanged.
   - **Genuinely unaffected (do NOT touch):** `tests/test_daemon_main.py` (uses only `bootstrap.ensure_running()`), and the `tests/test_daemon_hotkeys.py` reference (a comment). The `test_daemon_server.py` pins from step 2 are the new direct Server coverage.

**Implementation:**

- **Create `src/sonari/daemon/server.py`** (first line `from __future__ import annotations`). Move `_MAX_CONN_THREADS`, the `_conn_sem`, and the four transport methods into a `Server` class. The handshake/framing body of `_handle_conn` is pasted **verbatim** except three substitutions: `self._token` → `self._token_provider()`, `self._running` stays (it is the injected shared Event), and `self._handle_message_guarded(msg)` → `self._dispatch(msg)`.

  ```python
  from __future__ import annotations

  import socket
  import threading

  from sonari.protocol import encode, decode
  from sonari.platform import transport

  _MAX_CONN_THREADS = 32


  class Server:
      """Owns the localhost-TCP transport: bind/listen, the accept loop, the
      bounded connection-handler pool (+ M8 permit-leak recovery), the token
      handshake, and newline framing. Holds NO daemon state and never takes the
      daemon lock; per message it calls the injected `dispatch` callback — the
      host's locked dispatch entry, which opens state.transaction() around
      handle_message. Lifecycle is shared with the host via the same `running`
      Event (the accept/conn loops gate on it; the host's stop() clears it)."""

      def __init__(self, dispatch, token_provider, running):
          self._dispatch = dispatch                 # host._handle_message_guarded
          self._token_provider = token_provider     # callable -> current token str
          self._running = running                   # the host's shared Event
          self._sock = None
          self._accept_thread = None
          self._conn_sem = threading.BoundedSemaphore(_MAX_CONN_THREADS)

      def bind(self) -> int:
          """Bind an ephemeral localhost port and listen. Returns the port. Does
          NOT start accepting (the caller writes the lockfile + sets running
          first, so the accept loop never observes running==False at startup)."""
          srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
          srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
          srv.bind((transport.HOST, 0))
          srv.listen(16)
          self._sock = srv
          return srv.getsockname()[1]

      def serve(self) -> None:
          """Spawn the accept thread (running must already be set)."""
          self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
          self._accept_thread.start()

      def join(self, timeout=None) -> None:
          if self._accept_thread is not None:
              self._accept_thread.join(timeout)

      def is_alive(self) -> bool:
          return self._accept_thread is not None and self._accept_thread.is_alive()

      def stop(self) -> None:
          srv = self._sock
          if srv is not None:
              try:
                  srv.close()
              except OSError:
                  pass

      def _accept_loop(self) -> None:
          srv = self._sock
          while self._running.is_set():
              try:
                  conn, _ = srv.accept()
              except OSError:
                  return
              self._spawn_conn_handler(conn)

      # _spawn_conn_handler, _handle_conn_guarded, _handle_conn: paste the host
      # bodies verbatim, with self._conn_sem (now the Server's), and in
      # _handle_conn replace self._token -> self._token_provider() and
      # self._handle_message_guarded(msg) -> self._dispatch(msg).
  ```

- **In `host.py` `__init__`,** build the Server once (lazily reading the token, which `run()` sets later). Add `from sonari.daemon.server import Server` at module top. **Keep `self._token = None` (host.py:49)** — the `token_provider=lambda: self._token` closes over it and `run()` sets the real token before `serve()`; removing it would `AttributeError` the first handshake. Replace the `self._server = None` line's role:
  ```python
  self._server = Server(
      dispatch=self._handle_message_guarded,
      token_provider=lambda: self._token,
      running=self._running,
  )
  ```
  Keep `self._conn_sem` OFF the host (it now lives on the Server). `_handle_message_guarded` stays a host method unchanged (it is the callback).

- **Rewrite host `run()`** to delegate the transport while preserving the exact original order (ensure-dir → bind/listen → token → lockfile → set running → spawn speak → spawn accept → join loop → finally stop + unlink). OLD (host.py:1159-1194) is the inline socket version; NEW:
  ```python
  def run(self) -> None:
      ensure_sonari_dir()
      self._token = secrets.token_hex(32)
      port = self._server.bind()
      transport.write_lockfile(
          LOCK_PATH, transport.HOST, port, self._token, os.getpid())
      self._running.set()
      speak_thread = threading.Thread(target=self._speak_loop, daemon=True)
      speak_thread.start()
      self._server.serve()          # accept thread starts after speak (matches original order)
      self._start_hotkeys()
      try:
          while self._running.is_set():
              self._server.join(timeout=0.25)
              if not self._server.is_alive():
                  break
      except KeyboardInterrupt:
          pass
      finally:
          self.stop()
          try:
              os.unlink(LOCK_PATH)
          except FileNotFoundError:
              pass
  ```
  `self._running.set()` MUST precede `self._server.serve()` (the accept loop gates on `_running`; spawning the accept thread before setting it would make the loop exit immediately).

- **Rewrite host `stop()`** (host.py:770-779) to close the socket via the Server. NEW:
  ```python
  def stop(self) -> None:
      self._running.clear()
      self._wake.set()
      self._stop_hotkeys()
      self._server.stop()
  ```

- **Delete from `host.py`:** `_accept_loop`, `_handle_conn`, `_handle_conn_guarded`, `_spawn_conn_handler`, the `self._conn_sem` field, and the `import socket` if now unused (it is used only by the moved methods + `run()`'s `socket.socket` — which also moved to `Server.bind()`; **confirm `socket` is no longer referenced in `host.py` and drop the import if so**; `secrets`/`os` stay — `run()` still uses them). Keep `_handle_message_guarded` and `secrets.token_hex` in `run()`.

- **Confirm NO conftest change is needed:** grep that `LOCK_PATH` is still referenced only in `host.py` `run()` (not in `server.py`). `tests/conftest.py:84` (`monkeypatch.setattr(daemon_host, "LOCK_PATH", ...)`) stays valid because `run()` and its `LOCK_PATH` binding stay on the host. Do **not** edit conftest in this task. *(If you find yourself wanting to move `run()` or the lockfile I/O into `server.py`, STOP — that reintroduces the `~/.sonari` foot-gun and would force a same-commit conftest repoint; the plan deliberately keeps `run()` on the host to avoid it.)*

**Gate:** `.venv/bin/python -m pytest -q --ignore=tests/test_kokoro.py` → green; count = 730 + the new `test_daemon_server.py` pins + 1 package pin. Run a **sacrificial-HOME startup smoke** to confirm the entrypoint still binds/serves/stops cleanly (drive `bin/sonari-hook` or `python -m sonari.daemon` under `HOME=$(mktemp -d)`; never `sonari install`). Commit: `refactor(stage2): extract localhost-TCP transport into daemon/server.py (Server)`.

---

## Step 5 — Lift the handler families into `features/*.py`

### The mechanical lift rule (applies to Tasks 5.1–5.8 — stated once here)

Step 3 already split every branch into a host method `_on_x(self, msg)` behind a `@handler` thunk `_h_x(ctx, msg) → ctx.host._on_x(msg)`, grouped by family. Step 5 lifts each family's logic into a `features/<family>.py` module. For each handler in a family, the implementer does four mechanical edits:

1. **Write the real handler in the feature module.** In `src/sonari/daemon/features/<family>.py`, define `@handler(MsgType.X) def on_x(ctx, msg):` and paste the body of the host's `_on_x` **verbatim**, with exactly two rewrites:
   - **`self.` → `ctx.host.`** everywhere (e.g. `self._enqueue(...)` → `ctx.host._enqueue(...)`, `self.speaker` → `ctx.host.speaker`, `self.config` → `ctx.host.config`, `self._stream(s)` → `ctx.host._stream(s)`). Uniform `ctx.host.` keeps the diff one-substitution-per-line and behavior-identical (`ctx.host.speaker is ctx.speaker`). Do **not** mix in `ctx.speaker`/`ctx.config` aliases — uniformity is what makes the lift reviewable and byte-faithful. *(The narrow `ctx.enqueue`/`ctx.flush_prose` facade from spec §4 is a later cleanup; it does not exist yet.)*
   - **Preamble re-source (only for the handlers the table marks).** A body that opened with `session = msg.get("session", "")` becomes `session = ctx.session`; one with `verbosity = self.config.get("verbosity", "everything")` becomes `verbosity = ctx.verbosity`. Keep them as local aliases at the top of the function so the rest of the body stays byte-identical. These are equal by construction (`ctx.session` ≡ `msg.get("session","")` on the bound msg; `ctx.verbosity` ≡ `config.get("verbosity","everything")`). **`_on_set_foreground` also reads `t = msg.get("type")` — keep that one local/inline; there is NO `ctx.type`, and adding one is out of scope.**

2. **Delete the `_h_x` thunk** from `host.py` (the feature's `@handler def on_x` replaces it — `on_x` IS the registered handler now, not a thunk that calls back).

3. **Replace the host `_on_x` method with a one-line forwarding shim** so any direct caller stays green (spec §12; the shims are deleted in Step 8):
   ```python
   def _on_x(self, msg):
       return on_x(self._ctx.bind(msg), msg)
   ```
   Import the family's handlers into `host.py` for both the shim and registration: `from sonari.daemon.features import <family>` plus the specific names the shims call (e.g. `from sonari.daemon.features.control import on_set_rate, on_ping, ...`). The `from ... import <family>` line is what runs the module's `@handler` decorators (registration); it satisfies the registration-order gate because `host.py` is imported by `daemon/__init__.py` before `assert_complete`.

4. **No behavior tests are deleted** (Global Constraints): the suite reaches `on_x` through `dispatch`, which now resolves to the feature handler, so every `handle_message`/`handle_event`/socket test stays green unchanged — **except** the handful of tests that patch a now-moved module-level name (see "patch-target repoints" below).

**Module-level imports each feature module needs.** `self. → ctx.host.` rewrites attribute access only — it does NOT touch a **free module-level name** the body references, so each feature module must re-import those itself. Audited against source (`grep` over the `_on_*` bodies), the free names are:

| Module | First-line `from __future__` + always | Extra module-level imports the bodies need |
|---|---|---|
| `features/control.py` | `from sonari.protocol import MsgType` · `from sonari.daemon.registry import handler` | `from sonari.config import save_config` · `from sonari.daemon.limits import RATE_MIN, RATE_MAX, MINQUEUE_MIN, MINQUEUE_MAX` |
| `features/hotkeys.py` | same two | **`import threading`** (the `_on_reload_keymap` body calls `threading.Thread(...)` — host.py:688) |
| `features/prose.py` | same two | none — `PARAGRAPH_BREAK` is a `from sonari.assembler import ...` **local** import inside `_on_prose` (host.py:351); it travels with the body |
| `features/lifecycle.py` | same two | none — `Identity` is a `from sonari.sessions import ...` **local** import inside `_on_set_foreground` (host.py:656); `MsgType` (used at host.py:654) is already imported for the decorator |
| `features/decisions.py`, `features/navigation.py`, `features/playback.py`, `features/focus.py` | same two | none — every name in these bodies is reached via `self.` (→ `ctx.host.`) |

If a body references any other bare name you did not expect, import it in the feature module — a missing import is a `NameError` at the family's gate.

**Patch-target repoints (the only test edits Step 5 makes).** Moving a handler changes where a `mock.patch` must aim and where the registry resolves a monkeypatch. Two are forced (both stated in their family task, both in the same commit as the move):
- **`save_config`** (Task 5.1): `test_daemon_settings.py` patches `sonari.daemon.host.save_config` (10 sites) and asserts on the mock. After control lifts, the call resolves in `features.control`, so those patches must repoint to `sonari.daemon.features.control.save_config`. *(Bounded: `INSTALL_RECORD_PATH` — patched in `test_daemon_setup_health.py` — STAYS host-owned via the host's `_maybe_guide_setup`, so those patches remain valid. `save_config` is the only moved module-level patch target.)*
- **`_on_ping`** (Task 5.1): the dispatch-lock tests monkeypatch the host method; dispatch now resolves the registered feature handler, so they repoint to `registry.HANDLERS["ping"]` (see Task 5.1).

**Two worked examples** (trivial + complex; every other handler follows the same shape):

**Trivial — PING → `features/control.py`** (no preamble; a reply-producing row):
```python
# src/sonari/daemon/features/control.py
@handler(MsgType.PING)
def on_ping(ctx, msg):
    return {"ok": True}
```
```python
# host.py — thunk deleted; method becomes a shim
def _on_ping(self, msg):
    return on_ping(self._ctx.bind(msg), msg)
```

**Complex — CHOICE → `features/decisions.py`** (both preamble locals; `self.` → `ctx.host.`):
```python
# src/sonari/daemon/features/decisions.py
@handler(MsgType.CHOICE)
def on_choice(ctx, msg):
    session = ctx.session                 # was: msg.get("session", "")
    verbosity = ctx.verbosity             # was: self.config.get("verbosity", "everything")
    # ... the rest of the CHOICE body verbatim, every `self.` -> `ctx.host.` ...
    # e.g. ctx.host._stream(session), ctx.host.history.record(...),
    #      ctx.host._flush_prose_buffer(session), ctx.host._enqueue(session, "decision", text, True)
    return None
```
```python
# host.py
def _on_choice(self, msg):
    return on_choice(self._ctx.bind(msg), msg)
```

**The complete lift table — handler → host method → source line-range → preamble locals to re-source → feature module.** *(Line ranges are the current `host.py` at HEAD `3ed0259`; they shift down as earlier family tasks remove code — locate by method name, not absolute line. "—" = no preamble; re-source nothing.)*

| MsgType key(s) | Host method | Source lines (body) | Preamble → ctx | Feature module · Task |
|---|---|---|---|---|
| SET_RATE | `_on_set_rate` | 696-718 | — | control · 5.1 |
| SET_VOICE | `_on_set_voice` | 720-725 | — | control · 5.1 |
| SET_VERBOSITY | `_on_set_verbosity` | 727-730 | — | control · 5.1 |
| SET_MINQUEUE | `_on_set_minqueue` | 732-741 | — | control · 5.1 |
| CYCLE_VERBOSITY | `_on_cycle_verbosity` | 743-755 | — | control · 5.1 |
| STATUS | `_on_status` | 757-765 | — | control · 5.1 |
| PING | `_on_ping` | 767-768 | — | control · 5.1 |
| CHOICE | `_on_choice` | 428-445 | `session`, `verbosity` | decisions · 5.2 |
| PLAN | `_on_plan` | 447-460 | `session`, `verbosity` | decisions · 5.2 |
| PERMISSION | `_on_permission` | 462-475 | `session`, `verbosity` | decisions · 5.2 |
| REREAD_OPTIONS | `_on_reread_options` | 477-487 | — | decisions · 5.2 |
| SESSION_START + SET_FOREGROUND | `_on_set_foreground` *(one method, both keys)* | 650-663 | `t` (local), `session` | lifecycle · 5.3 |
| SESSION_END | `_on_session_end` | 665-673 | `session` | lifecycle · 5.3 |
| NAV | `_on_nav` | 489-498 | — | navigation · 5.4 |
| STOP | `_on_stop` | 504-513 | — | playback · 5.5 |
| SKIP | `_on_skip` | 515-522 | — | playback · 5.5 |
| PAUSE | `_on_pause` | 524-550 | — | playback · 5.5 |
| MUTE | `_on_mute` | 552-569 | — | playback · 5.5 |
| PIN_TOGGLE | `_on_pin_toggle` | 571-588 | — | playback · 5.5 |
| JUMP_DECISION | `_on_jump_decision` | 631-644 | — | playback · 5.5 |
| JUMP_WAITING | `_on_jump_waiting` | 590-629 | — | focus · 5.6 |
| PROSE | `_on_prose` | 344-372 | `session`, `verbosity` | prose · 5.7 |
| TOOL | `_on_tool` | 374-384 | `session`, `verbosity` | prose · 5.7 |
| EARCON | `_on_earcon` | 386-397 | `session` | prose · 5.7 |
| FLUSH | `_on_flush` | 399-422 | `session` | prose · 5.7 |
| RELOAD_KEYMAP | `_on_reload_keymap` | 679-690 | — | hotkeys · 5.8 |

**The one compound registration — `_on_set_foreground`** is registered under BOTH `SET_FOREGROUND` and `SESSION_START` via stacked decorators (legal because `handler` returns `fn`):
```python
@handler(MsgType.SET_FOREGROUND)
@handler(MsgType.SESSION_START)
def on_set_foreground(ctx, msg):
    t = msg.get("type")          # kept local — no ctx.type
    session = ctx.session
    # ... body verbatim, self. -> ctx.host. ; inner `if t == MsgType.SESSION_START:` unchanged ...
    return None
```

**The RELOAD_KEYMAP off-lock spawn is load-bearing** — its body's `threading.Thread(target=self._reload_hotkeys, name="sonari-keymap-reload", daemon=True).start()` becomes `ctx.host._reload_hotkeys` and is pasted verbatim; do NOT normalize it (the H2 dark-hotkey race fix runs the real reload work off the held lock).

**Each family task's shape (TDD):**
- **Pin first:** add a py39 future-import pin for the new `features/<family>.py` (and, in the first such task, `features/__init__.py`) to `tests/test_daemon_package.py`; add a one-line registry pin asserting each of the family's MsgType keys resolves to a function defined in `sonari.daemon.features.<family>` (e.g. `registry.HANDLERS[MsgType.X].__module__ == "sonari.daemon.features.<family>"`). Watch them fail.
- **Lift** per the rule + table rows for this family.
- **Wire** the `from sonari.daemon.features import <family>` import into `host.py`.
- **Gate** (every task): full suite green, count = prior + this task's new pins (no behavior count drops), AND `tests/test_blackbox_net.py` + `tests/test_concurrency_guards.py` green (the byte-identity proof). Commit: `refactor(stage2): lift <family> handlers into daemon/features/<family>.py`.

---

### Task 5.1 — Extract the **control** family → `features/control.py` (and create the `features/` package)

**Why:** Control (`SET_RATE/SET_VOICE/SET_VERBOSITY/SET_MINQUEUE/CYCLE_VERBOSITY/STATUS/PING`) is the simplest family (mostly small config setters + two reply rows) and is the right place to establish the package, the import wiring, and the one test-mechanics repoint before the more entangled families. STATUS/PING are net-proven; the SET_*/CYCLE handlers are net-silent but stay fully covered by `tests/test_daemon_settings.py` + `tests/test_daemon_phase2.py` through the registry.

**Implementation:**
- **Create `src/sonari/daemon/features/__init__.py`** — first line `from __future__ import annotations` (an otherwise-empty package marker).
- **Create `src/sonari/daemon/features/control.py`** — first line `from __future__ import annotations`, then `from sonari.protocol import MsgType`, `from sonari.daemon.registry import handler`, `from sonari.config import save_config`, and `from sonari.daemon.host import RATE_MIN, RATE_MAX, MINQUEUE_MIN, MINQUEUE_MAX`. **Wait — that import is a cycle** (`host` imports `features.control`, `control` imports from `host`). Avoid it: the rate/minqueue clamp constants `RATE_MIN/RATE_MAX/MINQUEUE_MIN/MINQUEUE_MAX` currently live in `host.py` (lines 22-28). **Move those four constants into a new `src/sonari/daemon/limits.py`** (first line `from __future__ import annotations`) and re-import them in `host.py` (`from sonari.daemon.limits import RATE_MIN, RATE_MAX, MINQUEUE_MIN, MINQUEUE_MAX`) so both `host.py` and `features/control.py` import them from `limits` with no cycle. Pin `limits.py` in the py39 set too.
- **Lift** all seven control handlers per the rule (all have `—` preamble). `on_set_rate`'s delta branch keeps `ctx.host.sessions.foreground()` / `ctx.host._enqueue(...)`; `on_cycle_verbosity` keeps its `order = ["everything","medium","quiet"]` body verbatim; `on_status` returns the same six-key dict (`ctx.host.config.get(...)`, `sum(len(st.queue) for st in ctx.host._streams.values())`).
- **Wire** `host.py`: `from sonari.daemon.features import control` + the specific `from sonari.daemon.features.control import (on_set_rate, on_set_voice, on_set_verbosity, on_set_minqueue, on_cycle_verbosity, on_status, on_ping)` for the shims; delete the seven control thunks (host.py:1333-1366); replace the seven `_on_*` methods with one-line shims. **Also delete host.py's now-dead `from sonari.config import save_config` (line 10)** — all five callers (host.py:713/724/729/740/751) moved into `features/control.py`, so the host import is unused; leaving it dead would also let the `host.save_config` patches below "succeed" silently without intercepting. (`limits.py`, `INSTALL_RECORD_PATH`, and `LOCK_PATH` imports STAY — they back code still on the host.)
- **Repoint the `save_config` mock targets (forced by the lift).** `tests/test_daemon_settings.py` patches `mock.patch("sonari.daemon.host.save_config")` at 10 sites (lines 17, 30, 39, 42, 49, 58, 68, 76, 79, 87) and asserts on the mock; after the lift the call resolves in `features.control`, so repoint all 10 to `mock.patch("sonari.daemon.features.control.save_config")`. Without this, the five `save.assert_called_once_with(config)` happy-path tests go red (the real `save_config` runs unmocked) and — once host's dead import is dropped — the patch `AttributeError`s at patch time. The `assert_not_called` reject-tests pass either way but repoint them too for uniformity.
- **Repoint the `_on_ping` lock-discipline tests** (`tests/test_daemon_dispatch.py`, both `test_dispatch_under_lock_*`). They monkeypatch `daemon._on_ping` (a one-arg bound method) and assert `recorded == [True]` — but dispatch now resolves the registered feature handler `on_ping(ctx, msg)`, not the host shim, so the monkeypatch no longer intercepts. Patch the **registered handler** instead (note the signature is now two-arg `(ctx, msg)`; keep `recorded` a list and the `== [True]` assertion). Replace the body of each test's setup/call with:
  ```python
  from sonari.daemon import registry
  recorded = []
  original = registry.HANDLERS["ping"]            # MsgType.PING == "ping"; no MsgType import needed

  def recording_ping(ctx, msg):
      recorded.append(daemon._lock.locked())
      return original(ctx, msg)

  registry.HANDLERS["ping"] = recording_ping
  try:
      daemon._handle_message_guarded({"type": "ping"})   # test 2: daemon._dispatch_hotkey({"type": "ping"})
  finally:
      registry.HANDLERS["ping"] = original
  assert recorded == [True], "expected lock held during dispatch, got: {0}".format(recorded)
  ```
  This preserves the assertion that dispatch runs under the held lock; the recording function's signature changes one-arg → two-arg and the patch target moves from the host method to the registry entry. Count unchanged by these repoints.

**Gate:** green; count = Task 4.1 count + the new py39 pins (`features/__init__.py`, `features/control.py`, `limits.py`) + the control registry pin. Commit: `refactor(stage2): lift control handlers into daemon/features/control.py`.

---

### Task 5.2 — Extract the **decisions** family → `features/decisions.py`

**Why:** `CHOICE/PLAN/PERMISSION/REREAD_OPTIONS` + their pure text builders. CHOICE/PERMISSION are net-proven (the event-feed net test + the FIFO test); PLAN/REREAD are net-silent but covered by `test_daemon_decisions.py`/`test_daemon_phase2.py`/`test_daemon_phase21.py`.

**Implementation:** Create `features/decisions.py` (future-import; `from sonari.protocol import MsgType`; `from sonari.daemon.registry import handler`). Lift `on_choice/on_plan/on_permission` (each re-sources `session` + `verbosity`) and `on_reread_options` (no preamble) per the rule + table. The text builders `_choice_text`/`_plan_text`/`_permission_text` are host methods the bodies call as `ctx.host._choice_text(...)` — they **stay on the host** for now (they have zero direct test callers and are shared helpers; moving them is a Step-8 cleanup). Wire the `from sonari.daemon.features import decisions` import + shims into `host.py`; delete the four decisions thunks (host.py:1227-1244).

**Gate:** green; count = prior + py39 pin + decisions registry pin. Commit: `refactor(stage2): lift decisions handlers into daemon/features/decisions.py`.

---

### Task 5.3 — Extract the **lifecycle** family → `features/lifecycle.py`

**Why:** `SESSION_START`/`SET_FOREGROUND` (one method, stacked-registered) + `SESSION_END`. SET_FOREGROUND/SESSION_START are net-proven via the event feed; SESSION_END is net-silent but covered by `test_daemon_control.py`/`test_daemon_streams.py`/`test_daemon_phase2.py`/`test_daemon_setup_health.py`.

**Implementation:** Create `features/lifecycle.py`. Lift `on_set_foreground` (stacked `@handler(SET_FOREGROUND)`/`@handler(SESSION_START)`; keep `t = msg.get("type")` local, re-source `session = ctx.session`; the inner `if t == MsgType.SESSION_START:` branch and its `from sonari.sessions import Identity` local import paste verbatim with `self.` → `ctx.host.`, including `ctx.host._maybe_guide_setup(...)`). Lift `on_session_end` (re-source `session`). Wire imports + the **two** shims (`_on_set_foreground`, `_on_session_end`) into `host.py`; delete the two lifecycle thunks (host.py:1307-1315). Note the stacked decorator means `on_set_foreground` is the registered handler for both keys; the registry pin asserts both `HANDLERS[MsgType.SET_FOREGROUND]` and `HANDLERS[MsgType.SESSION_START]` resolve to `features.lifecycle.on_set_foreground`.

**Gate:** green; count = prior + py39 pin + lifecycle registry pin. Commit: `refactor(stage2): lift lifecycle handlers into daemon/features/lifecycle.py`.

---

### Task 5.4 — Extract the **navigation** family → `features/navigation.py`

**Why:** `NAV` (within-turn + cross-turn) routing to the `_nav`/`_nav_response`/`_seek_and_play` helpers. The net proves only the one within-turn `prev` seek-and-play; the deep nav suite (`test_daemon_nav.py`, 21 functions) is net-silent and stays.

**Implementation:** Create `features/navigation.py`. Lift `on_nav` (no preamble; `fg = ctx.host.sessions.foreground()`, `to = msg.get("to", "prev")`, routing to `ctx.host._nav_response(...)` / `ctx.host._nav(...)`). The `_nav`/`_nav_response`/`_seek_and_play` helpers **stay on the host** (zero direct test callers; shared seek logic; Step-8 cleanup). Wire import + shim; delete the nav thunk (host.py:1252-1254).

**Gate:** green; count = prior + py39 pin + nav registry pin. Commit: `refactor(stage2): lift navigation handler into daemon/features/navigation.py`.

---

### Task 5.5 — Extract the **playback** family → `features/playback.py`

**Why:** `STOP/SKIP/PAUSE/MUTE/PIN_TOGGLE/JUMP_DECISION`. PAUSE/resume, MUTE, PIN_TOGGLE are net-proven; STOP/SKIP/JUMP_DECISION are net-silent but covered by `test_daemon_control.py`/`test_daemon_pause_mute.py`/`test_daemon_pin.py`/`test_daemon_decisions.py`.

**Implementation:** Create `features/playback.py`. Lift all six handlers (all `—` preamble) per the rule + table. `on_pause`'s resume branch calls `ctx.host._resume()`; the `_resume` helper **stays on the host**. These bodies read/mutate host ledger state via `ctx.host._current_item` / `ctx.host._paused` / `ctx.host._pending_heard` / `ctx.host.speaker.cancel()` — direct attribute access works (state is host-owned; no property shim). Wire import + the six shims; delete the six playback thunks (host.py:1262-1289).

**Gate:** green; count = prior + py39 pin + playback registry pin. Commit: `refactor(stage2): lift playback handlers into daemon/features/playback.py`.

---

### Task 5.6 — Extract the **focus** family → `features/focus.py`

**Why:** `JUMP_WAITING` (the ~40-line waiting-target focus + OS-raise) + the `_waiting_target` helper's callers. Net-proven for the ranking; focus-follow/raise specifics are net-silent but covered by `test_daemon_focus_follow.py`/`test_daemon_streams.py`.

**Implementation:** Create `features/focus.py`. Lift `on_jump_waiting` verbatim per the rule (no preamble; `fg = ctx.host.sessions.foreground()`, `target = ctx.host._waiting_target(exclude=fg)`, the `ctx.host._raise()...` chain, the `on_failure=lambda ...: ctx.host._raise_failed(...)` callback). `_waiting_target`/`_raise`/`_raise_failed` **stay on the host**. Wire import + shim; delete the focus thunk (host.py:1297-1299).

**Gate:** green; count = prior + py39 pin + focus registry pin. Commit: `refactor(stage2): lift focus handler into daemon/features/focus.py`.

---

### Task 5.7 — Extract the **prose** family → `features/prose.py`

**Why:** `PROSE/TOOL/EARCON/FLUSH` — the highest-traffic, most-entangled family (assembler feed, minqueue buffering, turn-boundary flush, the background-waiting earcon). PROSE/EARCON/FLUSH are net-proven; TOOL is net-silent but covered by `test_daemon_decisions.py`/`test_daemon_minqueue.py`. Extracted near-last so the lift pattern is proven on simpler families first.

**Implementation:** Create `features/prose.py`. Lift `on_prose`/`on_tool` (re-source `session` + `verbosity`), `on_earcon`/`on_flush` (re-source `session` only) per the rule + table. The bodies call host kernel ops verbatim as `ctx.host._buffer_prose(...)`, `ctx.host._flush_prose_buffer(session)`, `ctx.host._enqueue(...)`, `ctx.host._stream(session)`, `ctx.host.history.record(...)`, `ctx.host.speaker.earcon(...)`, `ctx.host._minqueue()` — all stay on the host. Wire import + the four shims; delete the four prose thunks (host.py:1202-1219). **Run the net + both concurrency guards explicitly after this task** — prose is where a byte-drift would most likely surface.

**Gate:** green; count = prior + py39 pin + prose registry pin; `test_blackbox_net.py` + `test_concurrency_guards.py` green. Commit: `refactor(stage2): lift prose handlers into daemon/features/prose.py`.

---

### Task 5.8 — Extract the **hotkeys** family → `features/hotkeys.py`

**Why:** `RELOAD_KEYMAP` — the off-lock reload (H2 race fix). Net-silent; covered by `test_daemon_hotkeys.py`.

**Implementation:** Create `features/hotkeys.py` — first line `from __future__ import annotations`, then **`import threading`** (the body uses the bare `threading.Thread(...)` — `self. → ctx.host.` does not supply it), `from sonari.protocol import MsgType`, `from sonari.daemon.registry import handler`. Lift `on_reload_keymap` verbatim (no preamble); its body's `threading.Thread(target=ctx.host._reload_hotkeys, name="sonari-keymap-reload", daemon=True).start()` is pasted **verbatim** — the thunk returns fast under the held lock; the real reload runs off-lock on the spawned thread. *(`test_daemon_hotkeys.py:101` calls `daemon.handle_message({"type":"reload_keymap"})` directly, so a missing `import threading` is a `NameError` at this task's gate, not a silent one.)* `_reload_hotkeys`/`_start_hotkeys`/`_stop_hotkeys`/`_reload_lock` **stay on the host**. Wire import + shim; delete the hotkeys thunk (host.py:1323-1325). **This is the last family — after it lands, `host.py` should be back well under the 1236-line concern (bodies gone; only 1-line shims + the kernel ops + speak loop remain).**

**Gate:** green; count = prior + py39 pin + hotkeys registry pin. Commit: `refactor(stage2): lift hotkeys handler into daemon/features/hotkeys.py`.

---

## Step 6 — The owner-approved behavior change + the log-only fix

### Task 6.1 — Unified `SET_VOICE` / `SET_VERBOSITY` validation (reject malformed → no-op)

**Why (spec §9.1, APPROVED user-facing change):** today `SET_RATE`/`SET_MINQUEUE` validate-and-clamp, but `SET_VOICE`/`SET_VERBOSITY` persist the raw payload unchecked — a malformed voice or an out-of-set verbosity is written to `config` on disk and can break synthesis or wedge verbosity gating until the bad config is removed. Route all four setters through one validation surface so malformed input is **rejected (no-op, nothing persisted)**, matching the existing `SET_RATE`/`SET_MINQUEUE` reject-on-bad contract. This is the ONLY intentional behavior change in the plan — its own commit.

**The validation predicates (derived from the code, not invented):**
- **verbosity** is valid iff it is one of `("everything", "medium", "quiet")` — the exact set `_on_cycle_verbosity` cycles through (host.py:744). A value outside the set is rejected (no-op).
- **voice** is valid iff it is a non-empty string (`isinstance(voice, str) and voice.strip()`). There is no daemon-side enum of macOS voices (the speaker passes the name straight to `say -v`); the defensible, platform-independent guard is "a non-empty string" — it rejects `None`, non-strings, and empty/whitespace, which are the malformed cases that reach disk and break synthesis. (Do NOT enumerate `say -v ?` — heavy, platform-coupled, out of scope.)
- **rate / minqueue** keep their existing int-parse + clamp-to-range behavior (unchanged outcomes), now expressed through the shared surface.

**Implementation (in `features/control.py`, post-5.1):**
- Add a small validation surface at the top of `features/control.py`:
  ```python
  from sonari.daemon.limits import RATE_MIN, RATE_MAX, MINQUEUE_MIN, MINQUEUE_MAX

  VERBOSITY_LEVELS = ("everything", "medium", "quiet")


  def _clamp_int(raw, lo, hi):
      """Return int(raw) clamped to [lo, hi], or None if raw is not a valid int."""
      try:
          return max(lo, min(hi, int(raw)))
      except (TypeError, ValueError):
          return None


  def _valid_verbosity(raw):
      """Return raw if it is a known verbosity level, else None."""
      return raw if raw in VERBOSITY_LEVELS else None


  def _valid_voice(raw):
      """Return raw if it is a non-empty string, else None."""
      return raw if isinstance(raw, str) and raw.strip() else None
  ```
- Route the setters through it:
  - `on_set_rate`: replace the inline `max(RATE_MIN, min(RATE_MAX, int(...)))` (both delta and absolute branches) with `_clamp_int(...)`, `return None` when it is `None` — same outcomes as today.
  - `on_set_minqueue`: `n = _clamp_int(msg.get("minqueue"), MINQUEUE_MIN, MINQUEUE_MAX); if n is None: return None` — same as today.
  - `on_set_voice` (NEW behavior): `voice = _valid_voice(msg.get("voice")); if voice is None: return None` before `ctx.host.config["voice"] = voice; ctx.host.speaker.set_voice(voice); save_config(...)`.
  - `on_set_verbosity` (NEW behavior): `v = _valid_verbosity(msg.get("verbosity")); if v is None: return None` before persisting.

**TDD:** add to `tests/test_daemon_settings.py`, mirroring the existing `test_set_rate_absolute_rejects_non_numeric` style — but **patch `mock.patch("sonari.daemon.features.control.save_config")`**, NOT the host namespace (Task 5.1 already repointed the file; a `host.save_config` patch here would never be on the call path, making a `save.assert_not_called()` pass vacuously and shipping the change unverified). Assert config unchanged + `speaker` untouched + `save_config` not called:
- `test_set_voice_rejects_none_and_non_string` — `SET_VOICE` with `voice=None` and with `voice=123`: config["voice"] unchanged, `speaker.voices == []`, `save_config` not called.
- `test_set_voice_rejects_empty_string` — `voice=""` / `voice="   "`: rejected, nothing persisted.
- `test_set_voice_accepts_valid_string` — `voice="Samantha"` still persists (the existing `test_set_voice_updates_config_and_speaker_and_saves` already covers the happy path; this re-confirms post-change).
- `test_set_verbosity_rejects_unknown_level` — `verbosity="loud"`: config["verbosity"] unchanged, `save_config` not called.
- `test_set_verbosity_accepts_each_known_level` — `everything`/`medium`/`quiet` each persist.
Write them failing against the current raw-persist behavior, watch the voice/verbosity ones fail, implement, watch all pass.

**Gate:** green; count = Step-5 count + the new settings pins. Commit: `feat(stage2): validate SET_VOICE/SET_VERBOSITY through a unified clamp/validate helper (reject malformed)`.

---

### Task 6.2 — Restore the lost `_signal_speak_failure` traceback (log-only fix)

**Why (spec §9.2, NOT user-facing — log-only):** `_signal_speak_failure` (host.py:954-969) fires the error earcon (good) and then calls `traceback.print_exc(file=sys.stderr)` inside `try/except: pass` — but `traceback` and `sys` are **not** module-level imports in `host.py` (they are imported *locally* inside `_speak_loop` at lines 949-950 and `_handle_message_guarded` at 1110-1111). Those locals are not in `_signal_speak_failure`'s frame, so the call raises `NameError`, which the bare `except` swallows — the promised daemon-log traceback is silently lost on every inner speak-loop failure. The earcon still fires, so the eyes-free experience is unchanged; only the operator's log line is missing. Restore it, as its own commit, never folded into a structural move.

**TDD — reproduce the silent loss empirically first (do not trust the reasoning blind):**
- Add to `tests/test_daemon_speak_resilience.py` (or a new `tests/test_daemon_signal_failure.py`):
  - `test_signal_speak_failure_logs_traceback_to_stderr` — drive a `_signal_speak_failure` call from inside a real `except` block (raise something, enter `except`, call `daemon._signal_speak_failure()`), capturing `sys.stderr` (pytest `capsys` or `contextlib.redirect_stderr(io.StringIO())`). Assert the captured text contains a traceback marker (e.g. `"Traceback (most recent call last)"`).
  - **Run it BEFORE the fix and confirm it FAILS** (stderr empty — the `NameError` was swallowed). This is the proof the bug is real; if it unexpectedly passes, stop and investigate (the fix would then be for a non-bug).
  - Also assert the error earcon still fires (the existing earcon behavior is untouched).
- **Fix:** add the imports inside `_signal_speak_failure` so the name resolves, matching the codebase's local-import idiom for `sys`/`traceback`:
  ```python
  def _signal_speak_failure(self) -> None:
      """...docstring unchanged..."""
      try:
          self.speaker.earcon("error")
      except Exception:  # noqa: BLE001 - signaling failure must not wedge the loop
          pass
      try:
          import sys
          import traceback
          traceback.print_exc(file=sys.stderr)
      except Exception:  # noqa: BLE001 - logging failure must not wedge the loop
          pass
  ```
  Watch the test pass.

**Gate:** green; count = Task 6.1 count + 1 (the resilience pin). Commit: `fix(stage2): restore the swallowed _signal_speak_failure traceback (sys/traceback were out of scope)`.

---

## Self-review (run against the spec before declaring the plan done)

- **Spec §10 Step 4** → Task 4.1 (Server extraction, host keeps the locked dispatch entry). ✓
- **Spec §10 Step 5 / §4 features map** → Tasks 5.1–5.8 (all 8 families: control, decisions, lifecycle, navigation, playback, focus, prose, hotkeys). ✓
- **Spec §10 Step 6 / §9.1 / §9.2** → Tasks 6.1 (validation) + 6.2 (log fix), each its own commit. ✓
- **Spec §8 "repoint conftest/tests in the same step as any module move"** → the `~/.sonari` conftest patch needs no repoint (Task 4.1 keeps `run()`/`LOCK_PATH` on the host); but moving symbols still forces same-commit *test* repoints, each stated in its task: Task 4.1 repoints `test_daemon_conn.py` + `test_daemon_loop.py` (the transport symbols moved to `Server`); Task 5.1 repoints the 10 `save_config` patches + the two `_on_ping` dispatch tests. ✓
- **Spec §8 property-shim warning** → not triggered: ledger fields stay host-owned this plan (state relocation is Phase 2), so handlers reach them via `ctx.host.*` with no property shim. ✓
- **Free module-level names** (`threading`/`save_config`/limits) re-imported per the lift rule's imports table; `PARAGRAPH_BREAK`/`Identity` are body-local imports that travel. ✓
- **Spec §5 "shared preamble computed once on Ctx"** → the lift rule re-sources `session`/`verbosity` from `ctx`. ✓
- **Spec §5 / §10 RELOAD_KEYMAP off-lock** → preserved verbatim in Task 5.8. ✓
- **Registration-order guard (`assert_complete`)** → every family task wires `host.py`'s feature import; stated as the per-family gate. ✓
- **DoD "no new file approaches 1236 lines"** → each `features/*.py` is one small family; `host.py` shrinks as bodies leave (Task 5.8 note). ✓

## Execution handoff

This plan is the **rest of Phase 1 of Sonari Stage 2** (spec Steps 4–6), executed on branch `sonari-stage2-architecture` (do NOT merge to `main` until the owner decides the migration is finished). On completion — transport extracted, all eight feature modules lifted, the approved validation + the log fix landed — the only remaining Stage-2 work is **Phase 2 (spec Steps 7–8): relocate the speak loop + state LAST**, gated on the perf micro-benchmark vs `scripts/perf_baseline.json` + the two permanent concurrency guards + an on-Mac `sonari:doctor` smoke (fabricated fresh session, never the owner as a harness) — a separate plan written only after this one is green.

**Order is load-bearing:** Step 4 (4.1) first; then Step 5 in any family order but **5.1 first** (it creates the `features/` package + `limits.py` + the import wiring the other families reuse); then Step 6 (6.1 depends on `features/control.py` from 5.1). Every task is independently green-committable; the suite + the black-box net + the two concurrency guards are the regression proof at each commit.
