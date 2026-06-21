# Sonari Stage 2 — Phase 1: Safety Net + Structural Scaffolding (Implementation Plan)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the behavior-preserving safety net and the low-risk structural foundation — package split, platform-contract collapse, and the dispatch table — for decomposing the Sonari daemon, without changing any user-facing behavior.

**Architecture:** Feature-primary decomposition (per the approved design spec `docs/superpowers/specs/2026-06-21-sonari-architecture-design.md`). This plan covers spec **Steps 0–3** only: Step 0 builds the black-box net + permanent concurrency guards + a measured perf baseline; Steps 1–3 are the lowest-risk structural moves (split `daemon.py` into a package, collapse the single-impl platform ABCs to lean contracts, and turn the 27-branch dispatch ladder into a dict registry calling the same private methods). The riskier moves — the speak-loop/state relocation (Phase 2, spec Steps 7–8) and the feature-module extraction + the one approved behavior change (spec Steps 4–6) — are **deferred to later plans, written only after this net is green** (a plan must not assume what its safety net has not yet proven).

**Tech Stack:** Python 3.9+ (forward-ref string type hints), pytest, `threading`, macOS (`say`/`afplay`). No new runtime dependencies.

## Global Constraints

*Every task's requirements implicitly include this section.*

- **Repo / branch:** `/Users/Nima.Hakimi/Projects/private/claude-tts`, branch `sonari-stage2-architecture` (already created off local `main`). **Local commits only — NEVER `git push` or open a remote PR.** **NEVER** put a `claude.ai/code/session` link or any Claude-session footer in a commit message (hard rule).
- **Suite gate command (run at the end of every task):** `.venv/bin/python -m pytest -q --ignore=tests/test_kokoro.py`. The gate is **full suite GREEN (zero failures)**. The pass-count GROWS additively as tasks add tests and must **never drop** — a behavior-preserving move keeps the count; a task that adds pins raises it. Single test: `.venv/bin/python -m pytest tests/test_x.py::test_y -v`.
- **Test-count ledger (EXPECTED totals — the real gate is "green + never-drops", since the net's characterization-test count can vary with how many family tests the executor writes):**
  | After | Count | Delta |
  |---|---|---|
  | (entering Phase 1) | **682** | baseline |
  | Step 0 | **697** | +13 black-box net, +2 concurrency guards |
  | Step 1 | **700** | +3 package pins |
  | Step 2 | **701** | +1 (test_platform_base 4→5 fns) |
  | Step 3 | **701 + Step-3 pins** | each 3.x task states its own additions |
  If your machine's number differs, trust the **rule** (green + never-drops), reconcile, then proceed. ⚠️ **The Step-1 section below was drafted assuming a fresh 682 start; with Step 0 done, add +15 to every absolute count in Step 1 (before = 697, after = 700).** Steps 2–3 are already ledger-aligned.
- **Python 3.9 target:** every NEW `src/` module's first code line is `from __future__ import annotations`; type hints are forward-ref **strings** (`"str | None"`). `tests/test_py39_compat.py` scans `src/sonari` **non-recursively**, so new `daemon/` and `platform/` submodules need explicit per-module future-import pins (the steps include them; do NOT make the scan recursive).
- **TDD:** for a task that introduces new modules/behavior, write the pin/test first, watch it fail, implement, watch it pass, commit. For a behavior-preserving MOVE, the existing suite + the Step-0 black-box net ARE the regression test — confirm green before, make the move, confirm green after, commit.
- **Behavior-preserving:** speech/earcon output + ordering stay byte-identical through all of Phase 1. The only intentional behavior changes (SET_VOICE/SET_VERBOSITY validation; the `_signal_speak_failure` log fix) are spec **Step 6 — NOT in this plan**.
- **venvs:** `.venv` (py3.13) is the gate interpreter; `.venv39` (py3.9) exists for compat spot-checks. `tests/test_kokoro.py` needs the optional `[kokoro]` extra (numpy) and is excluded by the gate; Step 0.1 makes a bare `pytest` skip it cleanly instead of aborting collection.

---
## Step 0 — Build the safety net

**Goal:** No production code changes. Build the instrument that proves the Phase-1 moves are behavior-preserving: fix the pytest collection abort, grow a black-box characterization net, add two permanent concurrency guards, and bank a perf baseline. Everything downstream rests on this step.

**Branch:** `sonari-stage2-architecture` (already checked out). **Green gate (run at the end of every task):** `.venv/bin/python -m pytest -q --ignore=tests/test_kokoro.py` → expect `682 passed`. **Verified baseline:** 682 passed in ~4.5s.

**Key facts verified against source for this step:**
- Bare `.venv/bin/python -m pytest -q --co` aborts at collection with `ModuleNotFoundError: No module named 'numpy'` from `tests/test_kokoro.py:4` (`import numpy as np`). A grep over `tests/` confirms `test_kokoro.py` is the ONLY file with a bare top-level numpy import; the other kokoro-touching files (`test_macos_tts.py`, `test_cli_*.py`, etc.) import `sonari.kokoro` / `sonari.kokoro_provision`, which do NOT pull numpy at collection time (collection only aborts on `test_kokoro.py`).
- The existing `drain_queue(daemon, speaker)` in `tests/test_e2e_pipeline.py:56-70` is deliberately synchronous (no threads, no lock) and reaches into `daemon._streams` + `daemon.sessions.foreground()`, then pops and speaks **raw** `item.text` — it skips attribution (`_attributed_text` folder prefix) and mute-drop. The replacement seam `drain_once(daemon)` instead runs the REAL `daemon._speak_loop_once()` once (synchronous because the FakeSpeaker is instant). **Proven equivalent:** running the e2e scripted scenario through `drain_once` reproduces the exact published log including the choice-earcon-first ordering proof, AND it correctly applies attribution + mute-drop (which `drain_queue` cannot).
- `daemon._speak_loop_once()` (src/sonari/daemon.py:939) calls `self.speaker.cancel()` on many handler paths (FLUSH cut-on-switch line 450, NAV line 830, JUMP_WAITING line 623, etc.). To make cut-on-switch observable in the black-box log, the harness FakeSpeaker records `cancel()` as a `("cancel", None)` log entry. This does NOT perturb the prose-ordering family (that scenario never cancels — verified: its log has zero cancel entries).
- `SpeechQueue` API (src/sonari/queue.py): `enqueue`, `enqueue_front`, `pop_next`, `pop_pause_exempt`, `clear`, `has_decision`, `__len__`, and `._items` (the underlying deque). `SessionStream` exposes `.queue`, `.prose_buffer`, `.muted`, `.options`, `.waiting_signaled`, `.nav_cursor`, `.nav_turn` (src/sonari/session_stream.py).
- Daemon concurrency fields (src/sonari/daemon.py:42-59): `self._lock` (non-reentrant `threading.Lock`), `self._streams` (dict), `self._paused` (`threading.Event`), `self._wake` (`threading.Event`), `self._current_item`, `self._last_spoken_session`, `self._pending_heard` (dict), `self._running` (`threading.Event`). The speak loop's two lock regions live in `_speak_loop_once` (lines 970-993 region A, lines 1007-1021 region B), with `speaker.speak()` OUTSIDE the lock (line 1002).

---

### Task 0.1 — Fix the collection-abort foot-gun

**Why:** A bare `pytest` aborts the WHOLE collection (1 error, 0 tests run) because of one unguarded import, so anyone who forgets `--ignore=tests/test_kokoro.py` gets a red wall instead of the suite. Guard the import so the file skips cleanly when numpy is absent, and pin `testpaths` so collection is scoped.

- [ ] **Confirm the abort exists (the "before").** Run:
  ```
  .venv/bin/python -m pytest -q --co 2>&1 | tail -4
  ```
  Expected (the foot-gun, before the fix):
  ```
  =========================== short test summary info ============================
  ERROR tests/test_kokoro.py
  !!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
  682 tests collected, 1 error in 0.14s
  ```

- [ ] **Confirm test_kokoro.py is the only bare-numpy file.** Run:
  ```
  grep -rln "^import numpy\|^from numpy\|^    import numpy" tests/
  ```
  Expected output (exactly one line):
  ```
  tests/test_kokoro.py
  ```
  (If more than one file appears, each gets the same `importorskip` guard below — the digest and this grep agree it is only `test_kokoro.py`.)

- [ ] **Guard the numpy import in `tests/test_kokoro.py`.** Read the file's top. The current lines 1-7 are:
  ```python
  """Unit tests for the Kokoro neural-TTS provider (pure logic; no model load)."""
  import io
  import wave

  import numpy as np

  from sonari import kokoro
  ```
  Replace `import numpy as np` (line 4) so it skips the module when numpy is missing. Change:
  ```python
  import numpy as np
  ```
  to:
  ```python
  import pytest
  np = pytest.importorskip("numpy")
  ```
  `pytest.importorskip` raises `pytest.skip` at collection (not `ImportError`), so the file is reported as skipped rather than aborting the run.

- [ ] **Add the pytest config to `pyproject.toml`.** The file currently ends at line 27-28:
  ```toml
  [tool.setuptools.packages.find]
  where = ["src"]
  ```
  Append a new table at the end of the file:
  ```toml

  [tool.pytest.ini_options]
  testpaths = ["tests"]
  ```

- [ ] **Verify the bare run no longer aborts.** Run:
  ```
  .venv/bin/python -m pytest -q 2>&1 | tail -3
  ```
  Expected (collection completes; the kokoro file is skipped, not an error):
  ```
  ...
  682 passed, 1 skipped in 4.6s
  ```
  The key change: `1 skipped` (the kokoro module) instead of `Interrupted: 1 error during collection`, and a non-zero passed count.

- [ ] **Confirm the explicit green gate still reads 682.** Run:
  ```
  .venv/bin/python -m pytest -q --ignore=tests/test_kokoro.py 2>&1 | tail -1
  ```
  Expected: `682 passed in 4.5s` (unchanged).

- [ ] **Commit.**
  ```
  git add tests/test_kokoro.py pyproject.toml
  git commit -m "test(stage2): guard numpy import + pin testpaths so bare pytest no longer aborts collection"
  ```

---

### Task 0.2 — Grow the black-box characterization net

**Why:** The Phase-1 moves are behavior-preserving; the only way to PROVE byte-identical speech/earcon output is a net that drives the REAL daemon (`handle_message` + `_speak_loop_once`) through a recording FakeSpeaker and asserts on the recorded log. These tests CHARACTERIZE current behavior — they PASS on first correct run and become the contract every later step must keep green. Each assertion below is the ACTUAL recorded log from a probe run against the current daemon (not a guess).

- [ ] **Create the harness + first 4 characterization tests** in a new file `tests/test_blackbox_net.py`. Full contents:
  ```python
  """Black-box behavior net: drive the REAL SpeechDaemon (handle_message +
  _speak_loop_once) through a recording FakeSpeaker and assert ONLY on the
  ordered (kind, payload) log + STATUS/PING replies.

  These are CHARACTERIZATION tests: each assertion is the actual recorded log
  of the CURRENT daemon. They pass on first run and are the behavior-preserving
  contract for every Stage-2 move. Unlike test_e2e_pipeline.drain_queue (which
  pops raw text and skips attribution/mute), drain_once() runs the REAL
  _speak_loop_once once (synchronous, because FakeSpeaker is instant), so the
  net exercises folder attribution, mute-drop, and cut-on-switch cancels too.
  """
  from sonari.protocol import MsgType, PROTOCOL_VERSION
  from sonari.sessions import SessionManager
  from sonari.daemon import SpeechDaemon
  from sonari.config import DEFAULTS


  class FakeSpeaker:
      """Records speak/earcon/cancel/set_* into ONE shared ordered log.

      cancel() is recorded as ("cancel", None) so cut-on-switch is observable in
      the log; speak() returns self.complete (True) so drain_once mirrors a clean
      completion (no pause-requeue)."""

      def __init__(self, log):
          self.log = log
          self.voice = None
          self.rate = DEFAULTS["rate"]
          self.complete = True
          self._epoch = 0

      def speak(self, text, cancel_epoch=None):
          self.log.append(("text", text))
          return self.complete

      def cancel_epoch(self):
          return self._epoch

      def cancel(self):
          self.log.append(("cancel", None))
          self._epoch += 1

      def earcon(self, kind):
          self.log.append(("earcon", kind))

      def set_voice(self, v):
          self.voice = v

      def set_rate(self, r):
          self.rate = r


  def make_net(verbosity="everything", foreground="fg",
               background_policy="earcon_only"):
      """A real SpeechDaemon wired to the recording FakeSpeaker. Returns
      (daemon, speaker, log, sessions, config)."""
      log = []
      speaker = FakeSpeaker(log)
      sessions = SessionManager(background_policy=background_policy)
      if foreground is not None:
          sessions.set_foreground(foreground)
      config = {k: (dict(v) if isinstance(v, dict) else v)
                for k, v in DEFAULTS.items()}
      config["verbosity"] = verbosity
      daemon = SpeechDaemon(speaker, sessions, config)
      daemon._setup_health = lambda v: ("ok", None)  # no setup cue in ordering
      return daemon, speaker, log, sessions, config


  def msg(t, session=None, **kw):
      d = {"v": PROTOCOL_VERSION, "type": t}
      if session is not None:
          d["session"] = session
      d.update(kw)
      return d


  def prose(daemon, session, delta, index=0, final=False):
      daemon.handle_message(msg(MsgType.PROSE, session,
                                delta=delta, index=index, final=final))


  def drain_once(daemon):
      """Run exactly ONE speak-loop iteration synchronously and report whether
      the foreground stream had an item to act on. The non-blocking seam that
      replaces drain_queue's reach into _streams: it runs the REAL
      _speak_loop_once (faithful attribution + mute-drop), but guards the call so
      it never blocks on an empty foreground stream."""
      fg = daemon.sessions.foreground()
      st = daemon._streams.get(fg)
      if st is None or len(st.queue) == 0:
          return False
      daemon._speak_loop_once()
      return True


  def drain(daemon, limit=1000):
      """drain_once to exhaustion of the foreground stream."""
      for _ in range(limit):
          if not drain_once(daemon):
              return


  # ---------------------------------------------------------------------------
  # Family: prose ordering (the e2e seed, characterized through drain_once)
  # ---------------------------------------------------------------------------

  def test_prose_ordering_decision_earcon_fires_before_fifo_text():
      daemon, speaker, log, sessions, config = make_net()
      from sonari.hooks_entry import handle_event

      def feed(event, payload):
          for m in handle_event(event, payload):
              assert m["v"] == PROTOCOL_VERSION
              daemon.handle_message(m)

      sid = "sess-net-1"
      feed("SessionStart", {"session_id": sid})
      feed("MessageDisplay", {"session_id": sid,
                              "delta": "Let me check the files. I will start now.",
                              "index": 0, "final": True})
      feed("PreToolUse", {"session_id": sid, "tool_name": "AskUserQuestion",
                          "tool_input": {"questions": [{"question": "Which approach?",
                              "options": [{"label": "Refactor"}, {"label": "Rewrite"}]}]}})
      drain(daemon)
      feed("UserPromptSubmit", {"session_id": sid})
      feed("MessageDisplay", {"session_id": sid,
                              "delta": "Applying the change now.",
                              "index": 0, "final": True})
      feed("Notification", {"session_id": sid,
                            "notification_type": "permission_prompt",
                            "action": "Run: pytest -q"})
      drain(daemon)
      feed("Stop", {"session_id": sid})
      drain(daemon)

      assert log == [
          ("earcon", "choice"),
          ("text", "Let me check the files."),
          ("text", "I will start now."),
          ("text", "Which approach? Option 1: Refactor. Option 2: Rewrite. "
                   "Press the option's number to choose, or Escape to cancel. "
                   "Selecting is immediate."),
          ("earcon", "permission"),
          ("text", "Applying the change now."),
          ("text", "Run: pytest -q Press the option's number to choose, "
                   "or Escape to cancel."),
          ("earcon", "turn_done"),
      ]


  # ---------------------------------------------------------------------------
  # Family: background is earcon-only (waiting + decision earcon, no text)
  # ---------------------------------------------------------------------------

  def test_background_session_is_earcon_only():
      daemon, speaker, log, sessions, config = make_net(foreground="fg")
      prose(daemon, "bg", "Background chatter that must stay silent. ",
            index=0, final=True)
      daemon.handle_message(msg(MsgType.CHOICE, "bg", questions=[
          {"question": "Pick one", "options": [{"label": "A"}, {"label": "B"}]}]))
      drain(daemon)  # foreground "fg" has nothing -> bg text never spoken
      assert log == [("earcon", "waiting"), ("earcon", "choice")]


  # ---------------------------------------------------------------------------
  # Family: minqueue batching (held below threshold, flushed all at once)
  # ---------------------------------------------------------------------------

  def test_minqueue_batches_below_threshold_then_flushes_together():
      daemon, speaker, log, sessions, config = make_net(foreground="fg")
      config["minqueue"] = 3
      prose(daemon, "fg", "One. Two. ", index=0, final=False)
      drain(daemon)
      assert log == []  # two sentences < threshold 3: nothing flushed, nothing spoken
      prose(daemon, "fg", "Three. ", index=1, final=False)
      drain(daemon)
      assert log == [("text", "One."), ("text", "Two."), ("text", "Three.")]


  # ---------------------------------------------------------------------------
  # Family: 2-level nav seek-and-play (within-turn prev cancels then replays)
  # ---------------------------------------------------------------------------

  def test_nav_within_turn_prev_seeks_and_plays():
      daemon, speaker, log, sessions, config = make_net(foreground="fg")
      h = daemon.history
      h.record("fg", "prose", "m0a"); h.record("fg", "prose", "m0b")
      h.end_message("fg")
      h.record("fg", "prose", "m1"); h.end_message("fg")
      h.record("fg", "prose", "m2")
      daemon.handle_message(msg(MsgType.NAV, "fg", to="prev"))
      drain(daemon)
      # prev cancels the current utterance then replays the target message AND
      # every later one (seek-and-play): m1 then m2.
      assert log == [("cancel", None), ("text", "m1"), ("text", "m2")]
  ```

- [ ] **Run the 4 characterization tests; they must PASS on first run.** Run:
  ```
  .venv/bin/python -m pytest tests/test_blackbox_net.py -v 2>&1 | tail -8
  ```
  Expected:
  ```
  tests/test_blackbox_net.py::test_prose_ordering_decision_earcon_fires_before_fifo_text PASSED
  tests/test_blackbox_net.py::test_background_session_is_earcon_only PASSED
  tests/test_blackbox_net.py::test_minqueue_batches_below_threshold_then_flushes_together PASSED
  tests/test_blackbox_net.py::test_nav_within_turn_prev_seeks_and_plays PASSED
  4 passed in ...s
  ```

- [ ] **Add the remaining family characterization tests** to `tests/test_blackbox_net.py`. For EACH item below: write the scenario, run it once, copy the ACTUAL recorded `log` (or `STATUS` reply) into the assertion, then confirm it passes. **Method to capture the assertion (verdict-blind):** add the test with `assert log == []` (deliberately wrong), run `-v`, read the AssertionError's "got" side, paste that exact list into the assertion, re-run to green. Do NOT hand-author the expected log. The 9 remaining families:

  1. **EARCON turn_done sub-threshold flush.** `make_net(foreground="fg")`, `config["minqueue"]=5`; `prose(daemon,"fg","Only one. ",final=True)` then `drain(daemon)` (assert `log==[]` — `final` alone does NOT flush); then `handle_message(msg(MsgType.EARCON,"fg",kind="turn_done"))` then `drain(daemon)`. Capture: the turn_done earcon then the flushed `"Only one."`. (Mirrors `test_daemon_minqueue.py::test_turn_boundary_flushes_sub_threshold_remainder`, but characterized through the speaker log.)

  2. **Decision FIFO + cue.** `make_net(foreground="fg")`; `prose(daemon,"fg","Some prose. ",final=False)`; `handle_message(msg(MsgType.EARCON,"fg",kind="choice"))`; `handle_message(msg(MsgType.CHOICE,"fg",questions=[{"question":"Q","options":[{"label":"A"},{"label":"B"}]}]))`; `drain(daemon)`. Capture: choice earcon, then `"Some prose."`, then the full choice text with cue (`Press the option's number...`) + `Selecting is immediate.` — proves earcon-first, text-FIFO-after-prose.

  3. **Foreground gating.** `make_net(foreground="a")`; `prose(daemon,"a","alpha. ",final=False)` and `prose(daemon,"b","beta. ",final=False)`; `drain(daemon)`. Capture: only `("text","alpha.")` is spoken (b is background — accumulates, never reaches the log). (Mirrors `test_daemon_streams.py::test_speak_loop_plays_only_the_foreground_stream`.)

  4. **Pause/resume re-queue.** `make_net(foreground="fg")`; `prose(daemon,"fg","interrupted. ",final=False)`; pause via `handle_message(msg(MsgType.PAUSE,"fg"))`; `drain(daemon)` (the paused branch voices only the pause_exempt `"Paused."`); then `handle_message(msg(MsgType.PAUSE,"fg"))` (resume) and `drain(daemon)`. Capture: the cancel from pause, `"Paused."`, then `"Resumed."`, then the re-queued `"interrupted."`. (Mirrors `test_daemon_pause_mute.py::test_resume_speaks_resumed_then_continues_interrupted`; note `PAUSE` enqueues `"Resumed."` at front per daemon.py:520-527.)

  5. **Mute.** `make_net(foreground="fg")`; `handle_message(msg(MsgType.MUTE,"fg"))` then `drain(daemon)` (speaks the mute_exempt `"Session muted."`); `prose(daemon,"fg","secret. ",final=False)` then `drain(daemon)` (dropped, silent); `handle_message(msg(MsgType.MUTE,"fg"))` then `drain(daemon)` (`"Session unmuted."`). Capture the full log; assert `"secret."` never appears. (Mirrors `test_daemon_pause_mute.py::test_mute_drops_speech_but_unmute_resumes`.)

  6. **Pin.** `make_net(foreground="fg")`; `sessions.set_foreground("fg",cwd="/home/me/myapp")`; `handle_message(msg(MsgType.PIN_TOGGLE,"fg"))` then `drain(daemon)`. Capture: `("text","Pinned myapp.")`. (Mirrors `test_daemon_pin.py::test_pin_toggle_pins_current_and_speaks_folder`.)

  7. **Jump-waiting target order (blocked outranks prose-only).** `make_net(foreground="a")`; `sessions.register("b",cwd="/x/proseonly")`, `sessions.register("c",cwd="/x/blocked")`; `prose(daemon,"b","just text. ",final=False)`; `handle_message(msg(MsgType.CHOICE,"c",questions=[{"question":"Pick?","options":[{"label":"One"},{"label":"Two"}]}]))`; `handle_message(msg(MsgType.JUMP_WAITING,"a"))`; `drain(daemon)`. Capture: foreground becomes `"c"` (assert `sessions.foreground()=="c"`); log = cancel, then `"Jumping to blocked. Bring it forward to type."`, then the choice text. (Probe-verified output: `Jumping to blocked. Bring it forward to type.` followed by `Pick? Option 1: One. Option 2: Two. ...`.)

  8. **FLUSH cut-on-switch.** `make_net(foreground="fg")`; enqueue then claim an item as current to simulate mid-utterance: `daemon._enqueue("fg","prose","sentence A",False)`; `it = daemon._stream("fg").queue.pop_next(); daemon._current_item = it`; clear `log`; `handle_message(msg(MsgType.FLUSH,"fg"))`. Capture: `log == [("cancel", None)]` and `len(daemon._stream("fg").queue) == 0` (the new prompt cut the current utterance — same-session FLUSH cancels per daemon.py:448-450). (Probe-verified.)

  9. **Config STATUS snapshot.** `make_net(foreground="fg")`; `daemon._enqueue("fg","prose","x",False)`; `reply = handle_message(msg(MsgType.STATUS,"fg"))`. Capture: `reply == {"verbosity":"everything","rate":200,"voice":None,"foreground":"fg","queue_len":1,"minqueue":1}` (probe-verified). Also `handle_message(msg(MsgType.PING,"fg")) == {"ok": True}`.

- [ ] **Run the full net file; all 13 family tests must pass.** Run:
  ```
  .venv/bin/python -m pytest tests/test_blackbox_net.py -v 2>&1 | tail -16
  ```
  Expected: 13 passed (4 from the first batch + the 9 just added), 0 failed.

- [ ] **Run the full suite green gate.** Run:
  ```
  .venv/bin/python -m pytest -q --ignore=tests/test_kokoro.py 2>&1 | tail -1
  ```
  Expected: `695 passed in ...s` (682 baseline + 13 new black-box tests).

- [ ] **Commit.**
  ```
  git add tests/test_blackbox_net.py
  git commit -m "test(stage2): black-box characterization net (drain_once seam, 13 behavior families)"
  ```

---

### Task 0.3 — Two permanent concurrency guards

**Why:** The black-box net is structurally blind to thread interleaving — `drain_once` runs the speak loop synchronously, with no real lock contention. The M2/L2 races (cancel in the pop→speak gap; a FLUSH resurrecting a paused item) only manifest when the REAL blocking `_speak_loop` runs against a slow speaker while other threads hammer handlers. These two guards are PERMANENT (never retired) and must exist BEFORE any state/loop relocation.

- [ ] **Create `tests/test_concurrency_guards.py`** with both guards. Full contents:
  ```python
  """PERMANENT concurrency guards for the Stage-2 speak-loop/state core.

  The black-box net is synchronous and cannot see thread interleaving. These two
  tests run the REAL blocking _speak_loop against a fake say_runner while other
  threads hammer the handlers under the real lock, and a deterministic re-entrant
  speaker that fires PAUSE/FLUSH from inside speak(). They guard the M2/L2 races
  (cancel in the pop->speak gap; a FLUSH racing a paused-item re-queue) and the
  "list changed size during iteration" failure class. NEVER retire these.
  """
  from __future__ import annotations

  import threading
  import time

  from sonari.speaker import Speaker
  from sonari.sessions import SessionManager
  from sonari.daemon import SpeechDaemon
  from sonari.config import DEFAULTS
  from sonari.protocol import MsgType, PROTOCOL_VERSION

  TIMEOUT = 5.0


  def _msg(t, session, **kw):
      d = {"v": PROTOCOL_VERSION, "type": t, "session": session}
      d.update(kw)
      return d


  class _SlowProc:
      """Event-gated stand-in for the `say` subprocess. wait() blocks until
      terminate() (cancel) or finish() ends playback, so the test controls when
      each utterance ends and can guarantee a real concurrent window."""

      def __init__(self) -> None:
          self.returncode = None
          self._ended = threading.Event()

      def wait(self, timeout=None):
          cap = TIMEOUT if timeout is None else min(timeout, TIMEOUT)
          if not self._ended.wait(cap):
              import subprocess
              raise subprocess.TimeoutExpired(cmd=["say"], timeout=timeout)
          return self.returncode

      def terminate(self) -> None:
          if self.returncode is None:
              self.returncode = -15
          self._ended.set()

      def poll(self):
          return self.returncode

      def finish(self, rc=0) -> None:
          self.returncode = rc
          self._ended.set()


  class _FastRunner:
      """say_runner whose procs finish almost immediately, so the real speak loop
      churns fast and the hammer threads collide with live pop/note_spoken."""

      def __init__(self) -> None:
          self.calls = 0
          self._lock = threading.Lock()

      def __call__(self, text, voice, rate):
          with self._lock:
              self.calls += 1
          p = _SlowProc()
          p.finish(0)  # already done: speak() returns True without blocking long
          return p


  def _make_real_daemon(runner, foreground="s0"):
      speaker = Speaker(say_runner=runner)
      sessions = SessionManager()
      sessions.set_foreground(foreground)
      config = {k: (v.copy() if isinstance(v, dict) else v)
                for k, v in DEFAULTS.items()}
      config["verbosity"] = "everything"
      daemon = SpeechDaemon(speaker, sessions, config)
      return daemon, speaker


  def test_stress_no_lost_duplicated_or_resurrected_item():
      """Real-threaded stress: the REAL blocking _speak_loop runs against a fake
      say_runner while threads hammer PAUSE/FLUSH/SET_FOREGROUND/JUMP_WAITING. The
      invariant: no crash (no 'dictionary/list changed size during iteration'),
      the speak thread never dies, and every stream's pending count stays bounded
      and non-negative — i.e. no item is lost, duplicated, or resurrected into a
      flushed queue. Probabilistic by design (interleaving pressure)."""
      runner = _FastRunner()
      daemon, speaker = _make_real_daemon(runner, foreground="s0")
      sessions = daemon.sessions
      for s in ("s0", "s1", "s2"):
          sessions.register(s, cwd="/x/" + s)

      errors: list = []
      speak_thread = threading.Thread(target=daemon._speak_loop, daemon=True)
      speak_thread.start()

      stop = threading.Event()

      def feeder(sess):
          i = 0
          while not stop.is_set():
              try:
                  daemon._dispatch_hotkey(_msg(MsgType.PROSE, sess,
                      delta="line {0}. ".format(i), index=i, final=False))
                  i += 1
              except Exception as e:  # noqa: BLE001
                  errors.append(("feeder", sess, e))
                  return

      def hammer(sess):
          ops = [MsgType.PAUSE, MsgType.FLUSH, MsgType.SET_FOREGROUND,
                 MsgType.JUMP_WAITING]
          n = 0
          while not stop.is_set():
              try:
                  daemon._dispatch_hotkey(_msg(ops[n % len(ops)], sess))
                  n += 1
              except Exception as e:  # noqa: BLE001
                  errors.append(("hammer", sess, e))
                  return

      threads = []
      for s in ("s0", "s1", "s2"):
          threads.append(threading.Thread(target=feeder, args=(s,), daemon=True))
          threads.append(threading.Thread(target=hammer, args=(s,), daemon=True))
      for t in threads:
          t.start()

      time.sleep(1.0)  # let the interleaving run
      stop.set()
      for t in threads:
          t.join(TIMEOUT)
          assert not t.is_alive(), "a hammer/feeder thread deadlocked"

      daemon._running.clear()
      daemon._wake.set()
      speak_thread.join(TIMEOUT)

      # No handler raised (the "list changed size during iteration" class).
      assert errors == [], "concurrency errors: {0}".format(errors[:3])
      # The speak thread survived the whole storm.
      assert not speak_thread.is_alive(), "speak thread died under stress"
      # Every stream's queue is non-negative and bounded by the backlog cap; the
      # _pending_heard dict never exceeds the total queued (no leak/resurrection).
      with daemon._lock:
          total_queued = sum(len(st.queue) for st in daemon._streams.values())
          for st in daemon._streams.values():
              assert len(st.queue) <= daemon._backlog_cap
          assert len(daemon._pending_heard) <= total_queued + 1


  class _ReentrantSpeaker:
      """Deterministic re-entrant FakeSpeaker: its speak() fires PAUSE then FLUSH
      (in that order) BEFORE returning not-completed, exactly reproducing the L2
      race — a FLUSH landing between speak() returning and the pause re-queue. The
      interrupted item must NOT be resurrected into the flushed queue; because FLUSH
      (not a bare PAUSE) wins, the re-queue/rollback branch (daemon.py:1011) is
      skipped, so _last_spoken_session stays committed (no rollback)."""

      def __init__(self, daemon):
          self.daemon = daemon
          self.log: list = []
          self._epoch = 0
          self._fired = False

      def speak(self, text, cancel_epoch=None):
          self.log.append(text)
          if not self._fired:
              self._fired = True
              # PAUSE sets _paused; FLUSH then clears it AND flushes the queue.
              self.daemon.handle_message(_msg(MsgType.PAUSE, "fg"))
              self.daemon.handle_message(_msg(MsgType.FLUSH, "fg"))
          return False  # interrupted

      def cancel_epoch(self):
          return self._epoch

      def cancel(self):
          self._epoch += 1

      def earcon(self, kind):
          self.log.append(("earcon", kind))

      def set_rate(self, r):
          pass

      def set_voice(self, v):
          pass


  def test_reentrant_flush_does_not_resurrect_paused_item():
      """L2 (deterministic): speak() fires PAUSE then FLUSH before returning
      not-completed. The re-queue-on-pause check is INSIDE the lock and re-reads
      _paused, so the FLUSH (which cleared pause) wins — the item is NOT
      resurrected; and because FLUSH won, the re-queue/rollback branch
      (daemon.py:1011) is skipped, so _last_spoken_session stays committed."""
      sessions = SessionManager()
      sessions.set_foreground("fg")
      config = {k: (v.copy() if isinstance(v, dict) else v)
                for k, v in DEFAULTS.items()}
      config["verbosity"] = "everything"
      daemon = SpeechDaemon(None, sessions, config)
      speaker = _ReentrantSpeaker(daemon)
      daemon.speaker = speaker
      daemon._last_spoken_session = None  # pre-speak baseline

      daemon._enqueue("fg", "prose", "interrupted", False)
      daemon._speak_loop_once()

      assert speaker.log == ["interrupted"]            # spoken once, not replayed
      assert not daemon._paused.is_set()                # FLUSH cleared the pause
      assert len(daemon._stream("fg").queue) == 0       # NOT resurrected
      assert daemon._current_item is None               # claim released
      assert daemon._last_spoken_session == "fg"        # NOT rolled back: FLUSH cleared pause, so the re-queue branch (daemon.py:1011) is skipped — no resurrect, no rollback
  ```

- [ ] **Run both guards; they must pass.** Run:
  ```
  .venv/bin/python -m pytest tests/test_concurrency_guards.py -v 2>&1 | tail -6
  ```
  Expected:
  ```
  tests/test_concurrency_guards.py::test_stress_no_lost_duplicated_or_resurrected_item PASSED
  tests/test_concurrency_guards.py::test_reentrant_flush_does_not_resurrect_paused_item PASSED
  2 passed in ...s
  ```
  (The stress test sleeps ~1s; total runtime is a few seconds. If the stress test ever flakes, that is a real race surfacing — do NOT loosen the assertions; investigate.)

- [ ] **Run the full suite green gate.** Run:
  ```
  .venv/bin/python -m pytest -q --ignore=tests/test_kokoro.py 2>&1 | tail -1
  ```
  Expected: `697 passed in ...s` (695 + 2 new guards).

- [ ] **Commit.**
  ```
  git add tests/test_concurrency_guards.py
  git commit -m "test(stage2): permanent concurrency guards (real-threaded stress + deterministic L2 re-entrant)"
  ```

---

### Task 0.4 — Bank the perf baseline

**Why:** The one hard product constraint is speak-path latency: the per-utterance critical section must not gain overhead in Phase 2. Bank a MEASURED before-number now, on the current daemon, so Step 7 can prove no regression. The benchmark times the `_enqueue → pop_next` critical section over N iterations and writes a JSON baseline.

- [ ] **Create the benchmark `scripts/perf_baseline.py`.** Full contents:
  ```python
  """Micro-benchmark the daemon's enqueue->pop critical section and bank a JSON
  baseline. Step 7 (the speak-loop/state relocation) re-runs this and compares,
  so the per-utterance hot path is gated on a measured number, not an ear test.

  Run:  .venv/bin/python scripts/perf_baseline.py
  Writes: scripts/perf_baseline.json  (committed as the before-number)
  """
  from __future__ import annotations

  import json
  import os
  import sys
  import time

  _ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
  for p in (_ROOT, os.path.join(_ROOT, "src")):
      if p not in sys.path:
          sys.path.insert(0, p)

  from sonari.sessions import SessionManager
  from sonari.daemon import SpeechDaemon
  from sonari.config import DEFAULTS

  N = 200_000
  WARMUP = 10_000


  def _make_daemon():
      class _NullSpeaker:
          rate = DEFAULTS["rate"]

          def speak(self, text, cancel_epoch=None):
              return True

          def cancel_epoch(self):
              return 0

          def cancel(self):
              pass

          def earcon(self, kind):
              pass

          def set_rate(self, r):
              pass

          def set_voice(self, v):
              pass

      sessions = SessionManager()
      sessions.set_foreground("fg")
      config = {k: (v.copy() if isinstance(v, dict) else v)
                for k, v in DEFAULTS.items()}
      daemon = SpeechDaemon(_NullSpeaker(), sessions, config)
      return daemon


  def _bench(daemon, n):
      st = daemon._stream("fg")
      q = st.queue
      best = float("inf")
      total = 0.0
      for _ in range(n):
          t0 = time.perf_counter()
          daemon._enqueue("fg", "prose", "x", False)
          item = q.pop_next()
          dt = time.perf_counter() - t0
          total += dt
          if dt < best:
              best = dt
          assert item is not None
      return total, best


  def main() -> None:
      daemon = _make_daemon()
      _bench(daemon, WARMUP)  # warm up; discard
      total, best = _bench(daemon, N)
      result = {
          "iterations": N,
          "section": "enqueue+pop_next",
          "total_seconds": round(total, 6),
          "mean_ns": round(total / N * 1e9, 1),
          "best_ns": round(best * 1e9, 1),
          "python": sys.version.split()[0],
          "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
      }
      out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "perf_baseline.json")
      with open(out, "w") as f:
          json.dump(result, f, indent=2)
          f.write("\n")
      print("Wrote {0}".format(out))
      print(json.dumps(result, indent=2))


  if __name__ == "__main__":
      main()
  ```

- [ ] **Run the benchmark; it writes the baseline JSON.** Run:
  ```
  .venv/bin/python scripts/perf_baseline.py
  ```
  Expected: a line `Wrote .../scripts/perf_baseline.json` followed by a JSON object with `"iterations": 200000`, `"section": "enqueue+pop_next"`, and a `"mean_ns"` number (single-to-low-double-digit microseconds per iteration on this machine — the absolute value is the bank; the comparison is what matters in Step 7).

- [ ] **Confirm the baseline file exists and is valid JSON.** Run:
  ```
  .venv/bin/python -c "import json; print(json.load(open('scripts/perf_baseline.json'))['section'])"
  ```
  Expected: `enqueue+pop_next`.

- [ ] **Confirm the benchmark did not perturb the suite.** Run:
  ```
  .venv/bin/python -m pytest -q --ignore=tests/test_kokoro.py 2>&1 | tail -1
  ```
  Expected: `697 passed in ...s` (unchanged — `scripts/` is outside `testpaths=["tests"]`, so the benchmark is never collected).

- [ ] **Commit.**
  ```
  git add scripts/perf_baseline.py scripts/perf_baseline.json
  git commit -m "test(stage2): bank measured enqueue->pop perf baseline for the Phase-2 speak-path gate"
  ```

---

**Step 0 exit state (the safety net):** bare `pytest` no longer aborts (kokoro skips cleanly); `tests/test_blackbox_net.py` characterizes 13 behavior families through the REAL daemon via the `drain_once` seam; `tests/test_concurrency_guards.py` holds the two permanent race guards; `scripts/perf_baseline.json` banks the before-number. Full suite = 697 passed (`--ignore=tests/test_kokoro.py`). No production code changed. Steps 1-3 now have the instrument that proves their moves are byte-identical.

---

> ℹ️ **Count note:** the absolute pass-counts in this section are ledger-aligned (Step 0 runs first and adds 15 tests, so this step runs **697 → 700**). One spot inside Task 1.2 still cites the raw observed deltas from the harvest (which was run on a 682 tree without Step 0); the gate rule is what matters — green, and the count never drops.

## Step 1 — `daemon/` package + bootstrap split + the conftest repoint

**Goal.** Turn the single module `src/sonari/daemon.py` (1236 lines) into a package `src/sonari/daemon/`, split into `host.py` (the `SpeechDaemon` class + run loop + server + speak loop + all handlers + the `LOCK_PATH` import) and `bootstrap.py` (`main()`, `ensure_running()`, `_arm_faulthandler()`, and the module globals `_SINGLETON`, `_FAULT_FILE`, plus the `SINGLETON_PATH` import). `__init__.py` re-exports `SpeechDaemon, main, ensure_running` for back-compat; `__main__.py` keeps `python -m sonari.daemon` working. This is **behavior-preserving** — no speech/earcon output changes — so the existing 682-test suite is the regression net, plus one new pin file.

**Branch / gates (every task).** Work on `sonari-stage2-architecture`. Full suite gate: `.venv/bin/python -m pytest -q --ignore=tests/test_kokoro.py`. The green baseline ENTERING this step is **697** (per the ledger: 682 + Step 0's 15); it RISES to **700** after Task 1.1 adds the pin file (3 test functions) and STAYS 700 through Task 1.2 (the move adds no tests). Commit after each task, conventional-commit style, scope `stage2`. NEVER put a Claude-session footer/link in a commit message. Local commits only — never `git push`.

> **Empirically harvested, not predicted.** The move was performed on-branch, the suite run, and the failure set recorded; the tree was then restored. The repoint list below is the *observed* red set (6 files), not a guess. The `python -m sonari.daemon` entrypoint is NOT exercised by the existing suite (`tests/test_bin_shims.py` only reads the shim text and execs the *CLI* `sonari` shim, line 48), so the suite can stay green while the entrypoint is broken — that gap is closed by `__main__.py` + a permanent in-process pin in Task 1.1.

---

### Task 1.1 — Write the pin file FIRST (`tests/test_daemon_package.py`)

This file pins three contracts that the move must preserve: (a) the public re-export surface, (b) the `-m sonari.daemon` entrypoint dispatches to `bootstrap.main()`, and (c) the four new package modules each begin with `from __future__ import annotations` (because `tests/test_py39_compat.py` scans `src/sonari/` *non-recursively* via `os.listdir`, so it will NOT descend into `daemon/` — verified at `tests/test_py39_compat.py:11,48`).

- [ ] Confirm green BEFORE: run `.venv/bin/python -m pytest -q --ignore=tests/test_kokoro.py` → expect last line `697 passed in <N>s` (the post-Step-0 count).
- [ ] Create `tests/test_daemon_package.py` with EXACTLY this content:

```python
from __future__ import annotations

import runpy
from pathlib import Path

import sonari.daemon.bootstrap as bootstrap

_SRC = Path(__file__).resolve().parent.parent / "src" / "sonari" / "daemon"
_FUTURE = "from __future__ import annotations"


def test_public_reexports_importable():
    # Back-compat floor: external importers (client.py, cli.py, daemon_helpers,
    # the e2e + speaker-cancel tests) all do `from sonari.daemon import <name>`.
    # The package __init__ must keep these three names resolvable.
    from sonari.daemon import SpeechDaemon, main, ensure_running

    assert callable(main)
    assert callable(ensure_running)
    assert isinstance(SpeechDaemon, type)


def test_dash_m_entrypoint_dispatches_to_bootstrap_main(monkeypatch):
    # `python -m sonari.daemon` (bin/sonari-daemon:14, macos/supervisor.py:163)
    # runs daemon/__main__.py, which must call bootstrap.main(). The suite does
    # not otherwise exec this path, so without this pin the package could ship
    # with a broken entrypoint and a green suite. In-process via runpy with a
    # patched main (a real subprocess would bind a socket and run forever).
    calls = []
    monkeypatch.setattr(bootstrap, "main", lambda: calls.append(1))
    runpy.run_module("sonari.daemon", run_name="__main__")
    assert calls == [1]


def test_new_package_modules_declare_future_annotations():
    # test_py39_compat scans src/sonari/ NON-recursively (os.listdir), so it does
    # not reach daemon/ submodules. Pin the Python-3.9 convention for the package
    # here: every module's first code line must be the future-annotations import.
    for name in ("host.py", "bootstrap.py", "__init__.py", "__main__.py"):
        text = (_SRC / name).read_text(encoding="utf-8")
        first = next(
            line.strip() for line in text.splitlines()
            if line.strip() and not line.strip().startswith("#")
        )
        assert first == _FUTURE, f"{name}: first code line must be {_FUTURE!r}"
```

- [ ] Run the three new tests; they MUST FAIL now (the `daemon/` package does not exist yet — `import sonari.daemon.bootstrap` raises `ModuleNotFoundError`): `.venv/bin/python -m pytest tests/test_daemon_package.py -v` → expect `3 failed` (each erroring on the `import sonari.daemon.bootstrap` at module top / collection).
- [ ] Run the full suite: `.venv/bin/python -m pytest -q --ignore=tests/test_kokoro.py` → because the new file fails at *collection* (top-level import error), expect a collection error line `ERROR tests/test_daemon_package.py` and `697 passed, 1 error`. This is the expected RED that Task 1.2 turns green; do not commit a broken collection.
- [ ] **Commit together with Task 1.2** (the pin file cannot be green until the package exists). Do NOT commit at this checkpoint — proceed straight to Task 1.2, then make one commit covering both.

> Why no standalone commit: a commit here leaves the suite with a collection error (red). Task 1.2 is the smallest change that makes 1.1 green, so they share one commit. The pin file is still *written first* (TDD: red → green).

---

### Task 1.2 — Atomic move: package split + entrypoint + conftest repoint + all 6 test repoints

This is ONE task on purpose. The harvest showed that before every repoint lands, `tests/test_daemon_conn.py` raises a **collection** error (`from sonari.daemon import _MAX_CONN_THREADS` — the package has no such top-level name), which halts the entire run (`Interrupted: 1 error during collection`, 0 tests collected). Any task boundary between the `git mv` and the final repoint would report a collection error, never a pass count — so it cannot satisfy the green gate. All sub-steps below land in a single commit.

#### 1.2a — `git mv` the module into the package

- [ ] `git mv src/sonari/daemon.py src/sonari/daemon/host.py` (git auto-creates the `daemon/` dir; this is a tracked rename).

#### 1.2b — Trim `host.py` imports and delete the bootstrap tail

`host.py` keeps only what `SpeechDaemon` uses: `save_config` (handlers, lines ~679–717), `LOCK_PATH` + `ensure_sonari_dir` (run loop, lines ~1128, 1156), `INSTALL_RECORD_PATH` (setup-health, line ~300), `transport` (run loop). It DROPS `subprocess`, `load_config`, `SINGLETON_PATH`, `socket_connectable`, and the `_SINGLETON` global — all of which move to `bootstrap.py`.

- [ ] In `src/sonari/daemon/host.py`, replace the import header + `_SINGLETON` global. OLD (current lines 3–20):

```python
import os
import secrets
import socket
import subprocess
import threading

from sonari.protocol import MsgType, encode, decode
from sonari.queue import SpeechItem
from sonari.config import save_config, load_config
from sonari.session_stream import SessionStream
from sonari.paths import (
    LOCK_PATH, SINGLETON_PATH, ensure_sonari_dir, socket_connectable,
    INSTALL_RECORD_PATH,
)
from sonari.platform import transport

# Holds the single-instance flock for this process's lifetime (see main()).
_SINGLETON = None
```

NEW:

```python
import os
import secrets
import socket
import threading

from sonari.protocol import MsgType, encode, decode
from sonari.queue import SpeechItem
from sonari.config import save_config
from sonari.session_stream import SessionStream
from sonari.paths import (
    LOCK_PATH, ensure_sonari_dir,
    INSTALL_RECORD_PATH,
)
from sonari.platform import transport
```

- [ ] In `src/sonari/daemon/host.py`, DELETE everything from the blank line before `def ensure_running()` to end of file. That is, delete current lines 1160–1231 — the `ensure_running()` def, the `_FAULT_FILE = None` global, the `_arm_faulthandler()` def, the `main()` def, and the `if __name__ == "__main__": main()` block. The file must now END right after the `run()` method's `finally` block, whose last lines are:

```python
            try:
                os.unlink(LOCK_PATH)
            except FileNotFoundError:
                pass
```

(Concretely: cut from `\n\ndef ensure_running()` onward; the file ends with the `pass` above followed by a single trailing newline.)

#### 1.2c — Create `bootstrap.py` (the moved code, verbatim bodies)

- [ ] Create `src/sonari/daemon/bootstrap.py` with EXACTLY this content (bodies are the originals, byte-for-byte; only the imports are reorganized for the new home, and `from sonari.daemon.host import SpeechDaemon` replaces the same-module reference `main()` used to have):

```python
from __future__ import annotations

import os
import subprocess

from sonari.config import load_config
from sonari.paths import SINGLETON_PATH, ensure_sonari_dir, socket_connectable
from sonari.platform import transport
from sonari.daemon.host import SpeechDaemon

# Holds the single-instance flock for this process's lifetime (see main()).
_SINGLETON = None


def ensure_running() -> None:
    if socket_connectable():
        return
    from sonari.platform import get_platform
    argv, kwargs = get_platform().supervisor.launch_spec()
    subprocess.Popen(argv, **kwargs)


_FAULT_FILE = None


def _arm_faulthandler() -> None:
    """Dump every thread's Python stack to SONARI_DIR/faulthandler.log on a NATIVE
    crash (access violation / segfault in WinRT, ctypes, or winsound) — the only
    way to see otherwise-silent C-level daemon deaths. Never raises."""
    global _FAULT_FILE
    try:
        import faulthandler
        # Import SONARI_DIR LIVE (not at module top) so the conftest monkeypatch /
        # any SONARI_DIR redirection takes effect; a top-level import would freeze
        # the value before tests patch it and leak into the real ~/.sonari.
        from sonari.paths import SONARI_DIR
        path = str(SONARI_DIR / "faulthandler.log")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # mode 'w': only the latest run's crash matters; never grow unbounded.
        _FAULT_FILE = open(path, "w", encoding="utf-8")
        _FAULT_FILE.write("=== faulthandler armed: pid {0} ===\n".format(os.getpid()))
        _FAULT_FILE.flush()
        faulthandler.enable(file=_FAULT_FILE, all_threads=True)
    except Exception:  # noqa: BLE001 - diagnostics must never break startup
        pass


def main() -> None:
    _arm_faulthandler()
    # Single-instance guard. The fast path avoids work when a daemon is clearly
    # already serving. The AUTHORITATIVE guard is the exclusive flock below:
    # with an ephemeral TCP port, bind() never collides (unlike the old fixed
    # AF_UNIX path), so socket_connectable() alone is racy and lets concurrent
    # lazy-starts each bind their own port -> a daemon explosion. The flock lets
    # exactly one process win; the rest exit. The lock auto-releases on death.
    global _SINGLETON
    if socket_connectable():
        return
    ensure_sonari_dir()
    _SINGLETON = transport.acquire_singleton(SINGLETON_PATH)
    if _SINGLETON is None:
        return  # another daemon already owns the single-instance lock

    from sonari.speaker import Speaker
    from sonari.sessions import SessionManager
    from sonari.platform import get_platform

    _backend = get_platform()
    cfg = load_config()
    if "earcons" not in cfg:
        cfg["earcons"] = _backend.earcon.default_earcons()
    speaker = Speaker(
        voice=cfg.get("voice"),
        rate=cfg.get("rate", 200),
        say_runner=_backend.tts.run,
        earcon_player=_backend.earcon.play,
        earcons=cfg.get("earcons"),
    )
    sessions = SessionManager(background_policy=cfg.get("background_policy", "earcon_only"))
    daemon = SpeechDaemon(speaker, sessions, cfg)
    daemon.run()


if __name__ == "__main__":
    main()
```

#### 1.2d — Create `__init__.py` (re-export floor)

- [ ] Create `src/sonari/daemon/__init__.py` with EXACTLY:

```python
from __future__ import annotations

from sonari.daemon.host import SpeechDaemon
from sonari.daemon.bootstrap import main, ensure_running

__all__ = ["SpeechDaemon", "main", "ensure_running"]
```

#### 1.2e — Create `__main__.py` (keep `-m sonari.daemon` alive)

`bin/sonari-daemon:14` runs `exec "$py" -m sonari.daemon "$@"` and `src/sonari/platform/macos/supervisor.py:163` builds `[python_executable, "-m", "sonari.daemon"]` (asserted verbatim by `tests/test_macos_supervisor.py:15`). Once `daemon` is a package, `-m sonari.daemon` needs `daemon/__main__.py` or it dies with "cannot be directly executed". This file keeps the shim, the supervisor, AND that supervisor test working with ZERO edits to any of them.

- [ ] Create `src/sonari/daemon/__main__.py` with EXACTLY:

```python
from __future__ import annotations

from sonari.daemon.bootstrap import main

main()
```

#### 1.2f — Repoint `tests/conftest.py` (lines 76–84) — LOAD-BEARING

The autouse `_isolate_sonari_dir` fixture currently patches `LOCK_PATH`/`SINGLETON_PATH`/`_SINGLETON` on the single `daemon` module. After the split those live on two different modules: `LOCK_PATH` on `host`, `SINGLETON_PATH` + `_SINGLETON` on `bootstrap`. Without this repoint, relocated code reads the REAL `~/.sonari` under test (the singleton flock + faulthandler log would escape isolation).

- [ ] In `tests/conftest.py`, replace lines 76–84. OLD:

```python
    # daemon.py binds LOCK_PATH + SINGLETON_PATH by value at import; main() takes
    # an exclusive flock on SINGLETON_PATH for single-instance. Repoint per-test
    # (each test has a unique sonari_dir) and reset the process-wide held-flock
    # global so a main()-calling test never blocks a later one.
    monkeypatch.setattr(paths, "SINGLETON_PATH", sonari_dir / "daemon.singleton", raising=False)
    import sonari.daemon as daemon
    monkeypatch.setattr(daemon, "LOCK_PATH", sonari_dir / "daemon.lock", raising=False)
    monkeypatch.setattr(daemon, "SINGLETON_PATH", sonari_dir / "daemon.singleton", raising=False)
    monkeypatch.setattr(daemon, "_SINGLETON", None, raising=False)
```

NEW:

```python
    # daemon/host.py binds LOCK_PATH by value at import; daemon/bootstrap.py binds
    # SINGLETON_PATH and main() takes an exclusive flock on it for single-instance.
    # Repoint each module's copy per-test (each test has a unique sonari_dir) and
    # reset the process-wide held-flock global so a main()-calling test never
    # blocks a later one.
    monkeypatch.setattr(paths, "SINGLETON_PATH", sonari_dir / "daemon.singleton", raising=False)
    import sonari.daemon.host as daemon_host
    import sonari.daemon.bootstrap as daemon_bootstrap
    monkeypatch.setattr(daemon_host, "LOCK_PATH", sonari_dir / "daemon.lock", raising=False)
    monkeypatch.setattr(daemon_bootstrap, "SINGLETON_PATH", sonari_dir / "daemon.singleton", raising=False)
    monkeypatch.setattr(daemon_bootstrap, "_SINGLETON", None, raising=False)
```

#### 1.2g — Repoint the 6 test files whose patch targets moved

These are the EXACT files the harvest turned red. Mechanism: `mock.patch("sonari.daemon.NAME")` only takes effect if the code under test looks `NAME` up via that exact path. After the split, `bootstrap.main()` resolves `socket_connectable`/`load_config`/`subprocess` in *bootstrap's own globals*, and `_arm_faulthandler` does `global _FAULT_FILE` (rebinding `bootstrap._FAULT_FILE`). So the patch/import targets must point at the new home module — re-exporting cannot fix it.

- [ ] `tests/test_daemon_conn.py:3` — change `from sonari.daemon import _MAX_CONN_THREADS` → `from sonari.daemon.host import _MAX_CONN_THREADS`. (This is the *collection* blocker; fixing it lets the rest of the suite collect.)
- [ ] `tests/test_daemon_faulthandler.py:3` — change `import sonari.daemon as daemon_mod` → `import sonari.daemon.bootstrap as daemon_mod`. (Uses `daemon_mod._arm_faulthandler()` and `daemon_mod._FAULT_FILE`, both now on `bootstrap`.)
- [ ] `tests/test_daemon_main.py` — change line 3 `import sonari.daemon as daemon_mod` → `import sonari.daemon.bootstrap as daemon_mod`; and in the four `mock.patch(...)` strings change `sonari.daemon.socket_connectable` → `sonari.daemon.bootstrap.socket_connectable` (lines 7, 15), `sonari.daemon.subprocess.Popen` → `sonari.daemon.bootstrap.subprocess.Popen` (lines 8, 16), `sonari.daemon.load_config` → `sonari.daemon.bootstrap.load_config` (line 30). LEAVE line 31 `mock.patch("sonari.daemon.SpeechDaemon.run", autospec=True)` UNCHANGED — `SpeechDaemon` is re-exported into the package, so that target still resolves to the same class object. `daemon_mod.main()` / `daemon_mod.SpeechDaemon` / `built.config` now read from the bootstrap module, which imports `SpeechDaemon` and defines `main` — verified green.
- [ ] `tests/test_daemon_single_instance.py` — change line 3 `import sonari.daemon as daemon_mod` → `import sonari.daemon.bootstrap as daemon_mod`; change `sonari.daemon.socket_connectable` → `sonari.daemon.bootstrap.socket_connectable` (lines 7, 15) and `sonari.daemon.load_config` → `sonari.daemon.bootstrap.load_config` (lines 9, 17). LEAVE the `mock.patch.object(daemon_mod.SpeechDaemon, "run")` calls (lines 8, 16) UNCHANGED — same re-exported class object.
- [ ] `tests/test_daemon_settings.py` — change all 10 occurrences of `mock.patch("sonari.daemon.save_config")` → `mock.patch("sonari.daemon.host.save_config")` (lines 17, 30, 39, 42, 49, 58, 68, 76, 79, 87 — `save_config` STAYS in `host`; verified: exactly 10 occurrences).
- [ ] `tests/test_daemon_setup_health.py` — change all 7 occurrences of `monkeypatch.setattr("sonari.daemon.INSTALL_RECORD_PATH", ...)` → `monkeypatch.setattr("sonari.daemon.host.INSTALL_RECORD_PATH", ...)` (lines 15, 25, 36, 46, 56, 67, 78 — `INSTALL_RECORD_PATH` STAYS in `host`).

> **Verify-don't-edit (no change needed, confirm by green):** `src/sonari/cli.py:518` (`from . import daemon; daemon.main()`), `src/sonari/client.py:8` (`from sonari.daemon import ensure_running`), `tests/daemon_helpers.py:3`, `tests/test_e2e_pipeline.py:14`, `tests/test_speaker_cancel_2b.py:336` (all `from sonari.daemon import SpeechDaemon`) — all resolve via `__init__.py` re-exports. `bin/sonari-daemon`, `src/sonari/platform/macos/supervisor.py:163`, `tests/test_macos_supervisor.py:15` — all keep `-m sonari.daemon` via `__main__.py`. `src/sonari.egg-info/SOURCES.txt` is autogenerated — ignore it.

#### 1.2h — Run the suite and commit

- [ ] Run the full suite: `.venv/bin/python -m pytest -q --ignore=tests/test_kokoro.py` → expect last line `700 passed in <N>s` (the 697 post-Step-0 count + the 3 pin tests from Task 1.1, all now green).
- [ ] Spot-check the entrypoint pin in isolation: `.venv/bin/python -m pytest tests/test_daemon_package.py -v` → expect `3 passed` (`test_public_reexports_importable`, `test_dash_m_entrypoint_dispatches_to_bootstrap_main`, `test_new_package_modules_declare_future_annotations`).
- [ ] Stage everything (the rename, 4 new files, conftest, 6 test files, the pin file) and commit. Suggested message:

```
git add -A && git commit -m "refactor(stage2): split daemon.py into daemon/ package (host + bootstrap)

Turn src/sonari/daemon.py into a package: SpeechDaemon + run loop + handlers
in daemon/host.py; main/ensure_running/_arm_faulthandler + singleton state in
daemon/bootstrap.py. __init__ re-exports the public floor; __main__ keeps
\`python -m sonari.daemon\` working (bin shim + macOS supervisor). Repoint the
conftest isolation patches and 6 test files whose mock targets moved. Behavior
unchanged; suite 682 -> 685 (adds tests/test_daemon_package.py pins)."
```

(No Claude-session footer. Local commit only — do not push.)

---

**NOTE for Step 2 / Step 3 authors:** `tests/test_py39_compat.py` enforces `from __future__ import annotations` via a NON-recursive `os.listdir(src/sonari)` (line 11/48), so it does NOT reach `src/sonari/daemon/` or `src/sonari/platform/` submodules. Step 1 pins its 4 package modules inside `tests/test_daemon_package.py::test_new_package_modules_declare_future_annotations`. When you add `daemon/registry.py`, `daemon/context.py`, `daemon/state.py` (Step 3) or `platform/contracts.py` (Step 2), add an equivalent per-module future-import assertion (or extend the Step-1 pin's module tuple) — do NOT make the global scan recursive, as that could turn existing `platform/` subpackage modules red, which is out of scope for these steps.

---

## Step 2 — Platform contract + collapse

> **Phase 1 · Step 2** of the §10 migration. Independent of Steps 1/3 (touches only `src/sonari/platform/**` and its tests). Goal (§6): create `platform/contracts.py` holding all five backend contracts as lean **signatures only**, delete the dead Windows ceremony, drop the ABC base from the concrete Mac classes (Protocols are structural), and delete `platform/base.py`. **Behavior-preserving**: the existing suite is the regression net.
>
> **Branch:** `sonari-stage2-architecture` (already checked out from Step 1).
> **Suite count entering this step (per the ledger in Global Constraints): 700.** Run with `.venv/bin/python -m pytest -q --ignore=tests/test_kokoro.py`. This step rewrites `test_platform_base.py` in place (4 → 5 test functions), so the **final total is 701**; read the printed number and adjust the gate if your tree differs. Single test: `.venv/bin/python -m pytest tests/test_x.py::test_y -v`.
> **Per task:** confirm green *before*, make the change, run the suite green, `git commit` (conventional-commit, scope `stage2`, no Claude-session footer, local only).

### Audit carried into this plan (defined-vs-inherited — decides delete-vs-move)

Verified against source before writing these tasks. For the 4 single-impl interfaces, every base method becomes a Protocol **signature** with no body, so all base bodies disappear — but any base body the Mac class **inherited and relies on** must be *re-added explicitly* to the Mac class or production breaks:

| Base method (single-impl iface) | Mac overrides? | Called on Mac path? | Resolution |
|---|---|---|---|
| `HotkeyBackend.key_codes / mod_masks / default_mods` | **yes** (`hotkeys.py:104/108/112`) | `keymap.py:59/71` | already concrete — keep |
| `HotkeyBackend.extra_default_bindings` | **yes** (`hotkeys.py:116`) | `keymap.py:74` | already concrete — keep |
| `HotkeyBackend.reload` | **yes** (`hotkeys.py:125`) | `daemon.py:787` | concrete; base default is **dead, delete** |
| `HotkeyBackend.start` / `stop` | **no** (inherited no-op) | `daemon.py:762/769` | **MOVE** — add explicit no-op bodies to `MacHotkeyBackend` |
| `HotkeyBackend.doctor_rows` | **no** (inherited `[]`) | `cli.py:198` | **MOVE** — add explicit `[]` body to `MacHotkeyBackend` |
| `SupervisorBackend.post_install_notes` | **yes** (`supervisor.py:250`) | `cli.py:426` | already concrete — keep |
| `SupervisorBackend.hooks_doctor_row` | **yes** (`supervisor.py:261`) | `cli.py:222` | already concrete — keep |
| `TtsBackend` / `EarconBackend` | (no base bodies) | — | nothing to move |

`start`/`stop`/`doctor_rows` on `MacHotkeyBackend` is the one **move** in this step (the spec's §6 delete-list omits `hotkey.doctor_rows`; the audit corrects it). Everything else in the §6 delete-list is genuinely dead.

`RaiseBackend` stays an `abc.ABC` (the one polymorphic seam: `NoopRaiseBackend` relies on its `supports`/`doctor_rows` bodies). `MacRaiseBackend` already overrides `supports`/`doctor_rows` and **keeps** its `RaiseBackend` base.

**`base.py` → delete (not shim).** Only two non-test importers (`platform/__init__.py`, `platform/macos/__init__.py`) plus 5 backend modules + 3 test files — all enumerated below and repointed in this step. A re-export shim would keep a dead `base.py` alive (the "dead namespace" foot-gun §2 warns about) for zero benefit; updating importers is lower-risk and matches the §4 target tree (`contracts.py`, no `base.py`).

**`isinstance` constraint (load-bearing, verified empirically).** `tests/test_macos_backend.py` and `tests/test_platform_factory.py` do `isinstance(pb.tts, base.TtsBackend)` etc. A plain `Protocol` **raises `TypeError`** under `isinstance`; a `@runtime_checkable` Protocol returns `True` structurally even with **no inheritance**. So the 4 Protocols **must** be `@runtime_checkable`, and the Mac classes must *define* every method named in their Protocol (they do — confirmed by the audit).

### Call-sites updated in this step (every `platform.base` reference)

- [ ] `src/sonari/platform/__init__.py:6` — `from sonari.platform.base import PlatformBackend` → `contracts`
- [ ] `src/sonari/platform/macos/__init__.py:1` — `from sonari.platform.base import PlatformBackend` → `contracts`
- [ ] `src/sonari/platform/macos/tts.py:24` — `from sonari.platform.base import TtsBackend` → `contracts`
- [ ] `src/sonari/platform/macos/earcon.py:7` — `from sonari.platform.base import EarconBackend` → `contracts`
- [ ] `src/sonari/platform/macos/hotkeys.py:10` — `from sonari.platform.base import HotkeyBackend` → `contracts`
- [ ] `src/sonari/platform/macos/supervisor.py:11` — `from sonari.platform.base import SupervisorBackend` → `contracts`
- [ ] `src/sonari/platform/macos/raiser.py:12` — `from sonari.platform.base import RaiseBackend` → `contracts`
- [ ] `tests/test_macos_backend.py:3` — `from sonari.platform import base` → `contracts as base` (+ refs lines 8–12)
- [ ] `tests/test_platform_factory.py:2` — `from sonari.platform import base` → `contracts as base` (+ ref line 9)
- [ ] `tests/test_platform_base.py:3-4` — rewritten wholesale in Task 2.4 to target `contracts`
- [ ] `tests/test_platform_raise_seam.py:2` — `from sonari.platform.base import …` → `contracts`
- [ ] `tests/_fakeplatform.py` — **no change** (uses `types.SimpleNamespace`; never imports `base`)
- [ ] `src/sonari.egg-info/SOURCES.txt` — generated artifact, not hand-edited (regenerates on build)

`daemon.py`, `cli.py`, `keymap.py`, `client.py`, `paths.py` use only `get_platform()` / `platform import transport` — **untouched** (the §6 "no consumer touched" guarantee; `transport.py` is not a backend and does not move).

---

### Task 2.1 — Create `platform/contracts.py` (the 5 lean contracts)

Pure-add; nothing imports it yet, so the suite is unaffected. Confirm green before:

```
.venv/bin/python -m pytest -q --ignore=tests/test_kokoro.py
# expect: 700 passed
```

Create `src/sonari/platform/contracts.py`:

```python
"""Platform backend contracts. The portable core depends ONLY on these; the
concrete macOS impl lives in the sibling macos package and is wired in by
get_platform(). The 4 single-impl backends are runtime_checkable Protocols
(signatures only); RaiseBackend is the one polymorphic seam (Mac + Noop)."""
from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@runtime_checkable
class TtsBackend(Protocol):
    def run(self, text: "str", voice, rate: "int"): ...
    def best_voice(self) -> "str": ...
    def list_voices(self) -> "list[str]": ...


@runtime_checkable
class EarconBackend(Protocol):
    def play(self, path: "str"): ...
    def default_earcons(self) -> "dict": ...


@runtime_checkable
class HotkeyBackend(Protocol):
    def install(self, log_path: "str", agent_path: "str", launchctl_fn) -> "tuple": ...
    def uninstall(self) -> None: ...
    def display_combo(self, modifiers: "int", key_code: "int") -> "str": ...
    def key_codes(self) -> "dict": ...
    def mod_masks(self) -> "dict": ...
    def default_mods(self) -> "list": ...
    def extra_default_bindings(self) -> "dict": ...
    def start(self, dispatch) -> None: ...
    def stop(self) -> None: ...
    def reload(self, dispatch) -> None: ...
    def doctor_rows(self) -> "list": ...


@runtime_checkable
class SupervisorBackend(Protocol):
    def install(self, python: "str", app_dir: "str") -> None: ...
    def uninstall(self) -> None: ...
    def is_running(self) -> bool: ...
    def is_installed(self) -> bool: ...
    def resolve_python(self): ...
    def launch_spec(self) -> "tuple": ...
    def doctor_rows(self) -> "list": ...
    def post_install_notes(self) -> None: ...
    def hooks_doctor_row(self) -> "tuple": ...


class RaiseBackend(abc.ABC):
    """Bring a session's terminal window/tab to the foreground (focus-follow)."""

    @abc.abstractmethod
    def raise_session(self, identity) -> bool:
        """Raise the window/tab for *identity* (a sessions.Identity). Return True
        only on a confirmed raise; False for unsupported/missing/denied/failed.
        Safe to call off the main thread; must never raise or hang."""

    def supports(self, identity) -> bool:
        """True if this backend can attempt a raise for *identity*. Default: no."""
        return False

    def doctor_rows(self) -> "list":
        """Diagnostic [(name, ok, detail), ...] rows. Default: none."""
        return []


class NoopRaiseBackend(RaiseBackend):
    """Inert backend for sessions without focus-follow (tests / unsupported)."""

    def raise_session(self, identity) -> bool:
        return False


@dataclass
class PlatformBackend:
    tts: "TtsBackend"
    earcon: "EarconBackend"
    hotkey: "HotkeyBackend"
    supervisor: "SupervisorBackend"
    raise_backend: "RaiseBackend"
```

> The Protocol signatures now include `key_codes/mod_masks/default_mods/extra_default_bindings/start/stop/reload/doctor_rows` for `HotkeyBackend` and `post_install_notes/hooks_doctor_row` for `SupervisorBackend` — these become **required** members (no defaults), exactly the type-checkable checklist §6 wants. No Windows prose, no no-op bodies.

Run + commit:

```
.venv/bin/python -m pytest -q --ignore=tests/test_kokoro.py        # 700 passed
git add src/sonari/platform/contracts.py
git commit -m "feat(stage2): add platform/contracts.py with lean backend contracts"
```

---

### Task 2.2 — Repoint the production importers + add the moved Mac hotkey methods

Update the 2 platform importers and the 5 Mac backend imports, drop the ABC bases from the 4 concrete classes, and **move** the three inherited-no-op hotkey methods onto `MacHotkeyBackend`. Confirm green before (`700 passed`).

`src/sonari/platform/__init__.py:6` — `from sonari.platform.base import PlatformBackend` → `from sonari.platform.contracts import PlatformBackend`.

`src/sonari/platform/macos/__init__.py:1` — `from sonari.platform.base import PlatformBackend` → `from sonari.platform.contracts import PlatformBackend`.

`src/sonari/platform/macos/tts.py:24` — import → `from sonari.platform.contracts import TtsBackend`; `class MacTtsBackend(TtsBackend):` (line 162) → `class MacTtsBackend:`.

`src/sonari/platform/macos/earcon.py:7` — import → `from sonari.platform.contracts import EarconBackend`; `class MacEarconBackend(EarconBackend):` (line 20) → `class MacEarconBackend:`.

`src/sonari/platform/macos/supervisor.py:11` — import → `from sonari.platform.contracts import SupervisorBackend`; `class MacSupervisorBackend(SupervisorBackend):` (line 42) → `class MacSupervisorBackend:`.

`src/sonari/platform/macos/raiser.py:12` — import → `from sonari.platform.contracts import RaiseBackend`. **Keep** `class MacRaiseBackend(RaiseBackend):` (RaiseBackend is still an ABC).

`src/sonari/platform/macos/hotkeys.py:10` — import → `from sonari.platform.contracts import HotkeyBackend`; `class MacHotkeyBackend(HotkeyBackend):` (line 94) → `class MacHotkeyBackend:`. Then **add the three inherited-no-op methods explicitly** (base bodies the Mac class never overrode but daemon/cli call — audit row "MOVE"). Insert after `reload` (after `hotkeys.py:140`):

```python
    def start(self, dispatch) -> None:
        """No-op: the macOS hotkeyd is a SEPARATE process (started by its
        LaunchAgent), not an in-process listener."""
        return None

    def stop(self) -> None:
        """No-op: see start()."""
        return None

    def doctor_rows(self) -> "list":
        """Platform hotkey diagnostics. None here (hotkeyd self-reports)."""
        return []
```

> Without these, dropping `HotkeyBackend` as a base makes `daemon.py:762/769` (`.start`/`.stop`) and `cli.py:198` (`.doctor_rows`) raise `AttributeError` at runtime. They were live no-ops, so they move — they are not dead.

Run + commit:

```
.venv/bin/python -m pytest -q --ignore=tests/test_kokoro.py        # 700 passed
git add src/sonari/platform/__init__.py src/sonari/platform/macos/
git commit -m "refactor(stage2): repoint platform importers to contracts; drop ABC bases, move Mac hotkey no-ops"
```

> Why still green here: `test_platform_factory` / `test_macos_backend` / `test_platform_base` / `test_platform_raise_seam` still import from the **live `base.py`** (not deleted until Task 2.3), so their `isinstance(...)` checks pass unchanged. The Mac classes are now bare but structurally complete; `make_backend()` builds the same `PlatformBackend`.

---

### Task 2.3 — Repoint the remaining test importers + delete `base.py`

Move the last consumers off `base` and delete the file. Confirm green before (`700 passed`). **Do not run the gate or commit between 2.3 and 2.4** — deleting `base.py` breaks `test_platform_base.py` until 2.4 rewrites it; they share one green boundary and one commit.

`tests/test_platform_raise_seam.py:2` — `from sonari.platform.base import RaiseBackend, NoopRaiseBackend, PlatformBackend` → `from sonari.platform.contracts import RaiseBackend, NoopRaiseBackend, PlatformBackend`. (Its 3 tests pass unchanged: `raise_backend` field present; `get_platform().raise_backend` is a `RaiseBackend`; `NoopRaiseBackend` inert.)

`tests/test_platform_factory.py:2` — `from sonari.platform import base` → `from sonari.platform import contracts as base`. (The single `base.PlatformBackend` ref on line 9 needs no further edit; `PlatformBackend` is a real dataclass → `isinstance` passes.)

`tests/test_macos_backend.py:3` — `from sonari.platform import base` → `from sonari.platform import contracts as base`. (Lines 8–12 do `isinstance(pb.tts, base.TtsBackend)` etc.; the 4 Protocols are `@runtime_checkable` and the Mac classes define every method → all 5 checks return `True`; `PlatformBackend` is a dataclass → line 8 passes.)

Delete the file:

```
git rm src/sonari/platform/base.py
```

Proceed directly to Task 2.4.

---

### Task 2.4 — Rewrite `tests/test_platform_base.py` to target `contracts`

Replace the whole file. The original has **4** functions; the rewrite has **5** (drops the now-inapplicable ABC-instantiation test, adds the `contracts.py` future-import pin and a structural-satisfaction pin). Overwrite `tests/test_platform_base.py`:

```python
"""Contracts pins for the collapsed platform layer (Stage 2).

The 4 single-impl backends are runtime_checkable Protocols (structural, no
inheritance); RaiseBackend stays an ABC. These pins replace the old base.py
ABC-instantiation tests.
"""
import os

from sonari.platform import contracts


def test_four_backends_are_runtime_checkable_protocols():
    # runtime_checkable: structural isinstance must work without inheritance,
    # and a bare object must NOT satisfy any backend.
    for proto in (contracts.TtsBackend, contracts.EarconBackend,
                  contracts.HotkeyBackend, contracts.SupervisorBackend):
        assert not isinstance(object(), proto)


def test_mac_backends_satisfy_their_protocols_structurally():
    from sonari.platform.macos.tts import MacTtsBackend
    from sonari.platform.macos.earcon import MacEarconBackend
    from sonari.platform.macos.hotkeys import MacHotkeyBackend
    from sonari.platform.macos.supervisor import MacSupervisorBackend
    assert isinstance(MacTtsBackend(), contracts.TtsBackend)
    assert isinstance(MacEarconBackend(), contracts.EarconBackend)
    assert isinstance(MacHotkeyBackend(), contracts.HotkeyBackend)
    assert isinstance(MacSupervisorBackend(), contracts.SupervisorBackend)


def test_platform_backend_bundles_the_five_fields():
    fields = contracts.PlatformBackend.__dataclass_fields__
    assert set(fields) == {"tts", "earcon", "hotkey", "supervisor", "raise_backend"}


def test_macos_hotkey_exposes_keytables_default_mods_and_lifecycle():
    from sonari.platform.macos.hotkeys import MacHotkeyBackend
    hk = MacHotkeyBackend()
    assert hk.key_codes()["s"] == 1 and hk.mod_masks()["cmd"] == 256
    assert hk.default_mods() == ["ctrl", "cmd"]
    # the moved no-ops: macOS hotkeyd is a separate process
    hk.start(lambda msg: None)
    hk.stop()
    assert hk.doctor_rows() == []


def test_contracts_module_has_future_annotations():
    # test_py39_compat scans src/sonari NON-recursively, so platform/contracts.py
    # is NOT covered there. Pin its future-import here. (Do NOT make that scan
    # recursive — keep this local assertion instead.)
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "src", "sonari", "platform", "contracts.py")
    with open(path, encoding="utf-8") as f:
        text = f.read()
    assert "from __future__ import annotations" in text
    # ...and it is the FIRST code line after the module docstring.
    body = text.split('"""', 2)[-1].lstrip()
    assert body.startswith("from __future__ import annotations")
```

> The dropped `test_backends_are_abstract` asserted `issubclass(cls, abc.ABC)` + `TypeError` on instantiation — neither applies to Protocols (not ABCs; structurally satisfiable). `test_platform_backend_bundles_the_four` becomes `..._bundles_the_five_fields` (asserts the field set directly; no need to hand-roll four stub classes). The old `test_base_hotkey_lifecycle_defaults_are_noops` is absorbed into the keytables pin — now asserting the *moved* Mac no-ops, not inherited base defaults. The split/`lstrip` future-import logic was verified to pass against the Task 2.1 module content.

Run + commit (one commit covering Task 2.3 + 2.4):

```
.venv/bin/python -m pytest tests/test_platform_base.py -v          # 5 passed
.venv/bin/python -m pytest -q --ignore=tests/test_kokoro.py        # 701 passed (4->5 in this module = +1)
git add tests/ src/sonari/platform/base.py
git commit -m "refactor(stage2): repoint platform tests to contracts; delete dead base.py"
```

> ⚠️ **Count honesty:** the module goes 4 → 5 functions, so the suite total reads **701**, not 700. The hard rule is "full-suite green," not a fixed number — state whatever the run prints; if it is not 701, reconcile before committing.

---

### Task 2.5 — Verify the seam + py39 + no-OS-branch invariants (exit gate)

No production change — a verification checkpoint, no separate commit. Confirm every invariant the spec calls out:

```
.venv/bin/python -m pytest tests/test_platform_raise_seam.py -v     # 3 passed
.venv/bin/python -m pytest tests/test_no_os_branch_in_core.py -v    # 3 passed
.venv/bin/python -m pytest tests/test_py39_compat.py -v             # 2 passed
.venv/bin/python -m pytest -q --ignore=tests/test_kokoro.py         # 701 passed
```

> `test_no_os_branch_in_core` reads CORE module *source text* and asserts no `sys.platform` branch / no `platform.macos` import in `assembler/cleaner/queue/history/sessions/protocol/hooks_entry/speaker/config`. This step adds neither to any core module — the only `sys.platform` branch remains in `platform/__init__.py:15` (which the test *requires*), and `platform/contracts.py` has no OS branch. `test_py39_compat`'s non-recursive `src/sonari` scan is unchanged and stays green; `contracts.py` (in the `platform/` subdir) is pinned separately by `test_contracts_module_has_future_annotations`.

**Step 2 exit criteria:** `base.py` deleted; all call-sites repointed (checkboxes above); the 4 backends are `@runtime_checkable` Protocols; the Mac classes are concrete with no ABC base (except `MacRaiseBackend`, which keeps `RaiseBackend`); `MacHotkeyBackend` carries explicit `start`/`stop`/`doctor_rows`; `get_platform()`'s single darwin branch intact; `test_platform_raise_seam` + `test_no_os_branch_in_core` + `test_py39_compat` green; full suite green (701). No consumer outside `platform/**` + its tests was touched.


---

## Step 3 — Ladder → dict registry + registry/context/state scaffolding

**Goal:** dissolve the 403-line / 27-branch `handle_message` ladder in `src/sonari/daemon/host.py` into a uniform `(ctx, msg) → reply|None` **dict registry**, *without moving any branch body out of the host*. Each `if t == MsgType.X:` body is extracted **verbatim** into a host method `_on_<x>(self, msg)`; the registry routes a thin thunk to it. This is a pure dispatch-shape transform — behavior byte-identical, proven by the Step-0 black-box net (`tests/test_blackbox_net.py`) plus the existing daemon suite.

**Branch shape (verified against source, lines 335-736 of the pre-Step-1 file):** every branch is a top-level `if t == ...: …; return` (no `elif`), so branches are mutually exclusive and order-independent — `HANDLERS.get(t, _ignore)` is provably isomorphic to the ladder, including the trailing unknown-type `return None`. The **only** compound branch is line 462 `if t in (MsgType.SET_FOREGROUND, MsgType.SESSION_START):` — one body, two types (handled below). So the table has **27 type-rows but 26 distinct `_on_*` methods**.

**Branch / state inventory carried in from the read (do not re-derive):**
- The host already builds in `__init__`: `self._lock = threading.Lock()`, plus the ledger fields `self._streams`, `self._pending_heard`, `self._current_item`, `self._last_spoken_session`, `self._next_id`, `self._paused`, `self._wake`, `self._reload_lock`. **All ledger fields STAY on the host for Phase 1.** `SessionState` owns only the lock + `transaction()` now.
- Tests reach the host via `from sonari.daemon import SpeechDaemon` (re-exported by `daemon/__init__.py`) and call `daemon.handle_message(msg)` directly (`tests/daemon_helpers.py`). So `handle_message(self, msg)` MUST stay the public entry with the same signature.
- The two lock-holding call sites are `_handle_message_guarded` (`with self._lock: return self.handle_message(msg)`) and `_dispatch_hotkey` (`with self._lock: self.handle_message(message)`). `RELOAD_KEYMAP`'s body spawns an off-lock `threading.Thread(target=self._reload_hotkeys, …).start()` — **kept verbatim, never normalized.**

**No conftest change in this step.** Nothing relocates off the host — the ledger stays put and `SessionState` merely wraps the existing `self._lock`. The `~/.sonari` monkeypatch foot-gun (§2) is not touched here; do not chase it.

**Hard conventions for every task below:**
- Branch: `sonari-stage2-architecture`. Commit after each task; conventional-commit scope `stage2`; NEVER include a Claude-session link/footer in any commit message. Local only — never push.
- **Full-suite green gate, every task:** `.venv/bin/python -m pytest -q --ignore=tests/test_kokoro.py` → expect `N passed`, where **N = the Step-2 count (701, per the ledger), unchanged, plus M new pins this task adds** (the structural transform moves no behavior, so the existing count never drops; each task states its own M). Do not fabricate an absolute N — it is the live count carried from Step 2 plus this task's pins.
- **Python 3.9:** every new src module's FIRST code line is `from __future__ import annotations`; all type hints are forward-ref **strings**. `test_py39_compat` scans `src/sonari` **non-recursively** (`os.listdir(SRC)`), so it will NOT check `daemon/` submodules — every new `daemon/*.py` module needs an explicit future-import pin (Task 3.1 extends the Step-1 pin tuple in `tests/test_daemon_package.py` to cover `registry.py`, `context.py`, `state.py`).

---

### Task 3.1 — Scaffolding: `registry.py` + `state.py` + `context.py`, wired into the host (ladder UNCHANGED)

Introduce the three new modules and wire them into the host, **without changing `handle_message` or the ladder at all** — the ladder still runs verbatim, so behavior and the suite are untouched. This task is pure plumbing + the lock-model swap to the same lock object.

**TDD order — write the pins first, watch them fail, then implement:**

1. **`tests/test_daemon_registry.py`** (new pins):
   - `HANDLERS` is a `dict` and starts empty before any feature registers.
   - `handler(t)` is a decorator that sets `HANDLERS[t] = fn` and **returns `fn`** (so it stacks — see Task 3.2).
   - `dispatch(ctx, msg)` for an unknown `msg["type"]` returns `None` (reproduces the ladder's trailing `return None`) and does not raise.
   - `assert_complete(known_types)` raises (`AssertionError` or a named error) when a known type lacks a handler, and is a no-op when all are present. (Negative pin: register all-but-one, assert it raises naming the missing type.)

2. **`tests/test_daemon_state.py`** (new pins):
   - `SessionState(lock)` exposes `.transaction()` as a context manager whose `__enter__`/`__exit__` acquire/release the **exact lock object passed in** (`state._lock is lock`; entering the transaction makes `lock.locked()` true, exiting clears it).
   - It is the SAME object the host uses: see the host pin in step 5 below.

3. **`tests/test_daemon_context.py`** (new pins) — build a tiny fake host (object with `.config`, `.speaker`, `.sessions`, `.history` attrs) and a `Ctx`:
   - `Ctx(host).host is host`; `.speaker / .sessions / .config / .history` pass straight through to the host.
   - After `ctx.bind({"type": "ping", "session": "abc"})`, `ctx.session == "abc"`; after `ctx.bind({"type": "ping"})`, `ctx.session == ""` (the `msg.get("session","")` default).
   - `ctx.verbosity == host.config.get("verbosity","everything")` (set `config = {"verbosity": "quiet"}` → `"quiet"`; empty config → `"everything"`).

4. **`tests/test_daemon_package.py`** — extend the Step-1 future-import pin tuple to include `registry.py`, `context.py`, `state.py` so the 3.9 guard covers the new submodules (the flat `test_py39_compat` scanner cannot).

5. **Host lock-model pin** in `tests/test_daemon_streams.py` (or a new `tests/test_daemon_dispatch.py`):
   - `daemon._state` is a `SessionState` and `daemon._state._lock is daemon._lock` (same object — behavior identical).
   - `daemon._ctx` is a `Ctx` whose `.host is daemon`.

**Implementation:**

- **`src/sonari/daemon/registry.py`** (first line `from __future__ import annotations`):
  ```python
  from __future__ import annotations

  HANDLERS = {}


  def handler(t):
      def deco(fn):
          HANDLERS[t] = fn
          return fn
      return deco


  def _ignore(ctx, msg):
      return None


  def dispatch(ctx, msg):
      return HANDLERS.get(msg.get("type"), _ignore)(ctx, msg)


  def assert_complete(known_types):
      missing = [t for t in known_types if t not in HANDLERS]
      assert not missing, "MsgType(s) without a handler: {0}".format(missing)
  ```

- **`src/sonari/daemon/state.py`** (first line `from __future__ import annotations`): `class SessionState` constructed with the host's existing lock; `transaction()` returns a context manager equivalent to `with lock:` (return the lock itself, or a `@contextmanager` wrapping `with self._lock:`). The ledger fields stay on the host — `SessionState` owns only the lock + `transaction()`.
  ```python
  from __future__ import annotations

  from contextlib import contextmanager


  class SessionState:
      def __init__(self, lock):
          self._lock = lock

      @contextmanager
      def transaction(self):
          with self._lock:
              yield
  ```

- **`src/sonari/daemon/context.py`** (first line `from __future__ import annotations`): `class Ctx` constructed `Ctx(host)`; `.bind(msg)` stores the current message; `.host / .speaker / .sessions / .config / .history` pass through; `.session` = `msg.get("session","")`; `.verbosity` = `host.config.get("verbosity","everything")`. **Build exactly this surface — no more.** (The wider §4 facade — `.enqueue / .flush_prose / .state / .raise` — lands in Step 5 when bodies move to `(ctx, msg)` handlers; adding it now is gold-plating.)
  ```python
  from __future__ import annotations


  class Ctx:
      def __init__(self, host):
          self._host = host
          self._msg = {}

      def bind(self, msg):
          self._msg = msg
          return self

      @property
      def host(self):
          return self._host

      @property
      def speaker(self):
          return self._host.speaker

      @property
      def sessions(self):
          return self._host.sessions

      @property
      def config(self):
          return self._host.config

      @property
      def history(self):
          return self._host.history

      @property
      def session(self):
          return self._msg.get("session", "")

      @property
      def verbosity(self):
          return self._host.config.get("verbosity", "everything")
  ```

- **`src/sonari/daemon/host.py`** — in `__init__`, after `self._lock = threading.Lock()` is created, build ONE of each:
  ```python
  self._state = SessionState(self._lock)
  self._ctx = Ctx(self)
  ```
  (import `SessionState` from `sonari.daemon.state`, `Ctx` from `sonari.daemon.context` at module top.)

- **Switch the two lock sites to the transaction boundary (same lock object → behavior identical):**
  - `_handle_message_guarded`: `with self._lock:` → `with self._state.transaction():`
  - `_dispatch_hotkey`: `with self._lock:` → `with self._state.transaction():`
  - Leave `handle_message` and the ladder **completely unchanged** in this task.
  - Leave the speak loop's own lock regions on `self._lock` for now (Phase 2 / Step 7 owns the loop relocation — out of scope here).

- **`src/sonari/daemon/__init__.py`** — add side-effect-free re-exports if helpful, but do NOT yet import features (none exist). Keep `SpeechDaemon` re-export intact.

**Gate:** `.venv/bin/python -m pytest -q --ignore=tests/test_kokoro.py` → `N passed` = Step-2 count (701) **+ M** (M = the new registry/state/context/package pins added here; the structural wiring moves no behavior so nothing drops). Commit: `test(stage2): scaffold registry/state/context + transaction lock boundary`.

---

### The mechanical extraction rule (applies to Tasks 3.2–3.5, state once)

For each `if t == MsgType.X:` branch in `handle_message` (lines 335-736), the executor does three mechanical edits:

1. **Create the host method `_on_<x>(self, msg)`.** At its top, **re-derive ONLY the preamble locals that branch body actually references** (see the "locals to re-derive" column), then paste the branch body **verbatim** (same code, just indented one level into the method). The branch already ends in `return <value>` (`return None`, or the dict for STATUS/PING) — that becomes the method's return, so `_on_<x>` returns exactly what the branch returned.
   - The re-derivation values are the originals computed at lines 336-338:
     - `t = msg.get("type")`
     - `session = msg.get("session", "")`
     - `verbosity = self.config.get("verbosity", "everything")`
   - Re-derive locally inside `_on_<x>` (keeps the body byte-identical) rather than reading `ctx.session`/`ctx.verbosity` — the `Ctx` properties get *used* in Step 5 when bodies move to `(ctx, msg)` signatures. A body that references no preamble local re-derives nothing (most rows).
   - Header pattern for a body that uses both `session` and `verbosity` (e.g. `_on_choice`), shown so the wrinkle is concrete:
     ```python
     def _on_choice(self, msg):
         session = msg.get("session", "")
         verbosity = self.config.get("verbosity", "everything")
         # ... verbatim body of the CHOICE branch ...
     ```

2. **Replace the ladder branch body** with a single delegating line, keeping the `if`:
   ```python
   if t == MsgType.X:
       return self._on_x(msg)
   ```
   The ladder still routes every type, so the suite + net stay green at every per-family commit (the flip to `dispatch()` happens only in the final task, after all 27 are registered).

3. **Register the thunk** in the matching feature-family block (a `@handler`-decorated closure that calls the host method):
   ```python
   @handler(MsgType.X)
   def _h(ctx, msg):
       return ctx.host._on_x(msg)
   ```

**Two worked examples** (the trivial row and the large row; every other row follows the same shape, sized by the table):

**One-liner — PING → `_on_ping`** (no preamble locals; demonstrates exception #2, a reply-producing row):
```python
# host.py
def _on_ping(self, msg):
    return {"ok": True}

# registry registration (control family block)
@handler(MsgType.PING)
def _h_ping(ctx, msg):
    return ctx.host._on_ping(msg)
```
Ladder branch becomes `if t == MsgType.PING: return self._on_ping(msg)`.

**Large — JUMP_WAITING → `_on_jump_waiting`** (the ~40-line body is just a row; references **zero** preamble locals — it derives `fg` itself in-body — so it extracts purely verbatim with no header):
```python
# host.py
def _on_jump_waiting(self, msg):
    fg = self.sessions.foreground()
    target = self._waiting_target(exclude=fg)
    if target is None:
        if fg is not None:
            self._enqueue(fg, "prose", "No session waiting.", False,
                          mute_exempt=True)
        else:
            self.speaker.earcon("error")
        return None
    self.sessions.focus(target)
    self.speaker.cancel()
    folder = self.sessions.folder(target)
    identity = self.sessions.identity(target)
    will_raise = self._raise().will_attempt(identity)
    gen = self._raise().bump_generation()
    base = ("Jumping to {0}.".format(folder) if folder
            else "Jumping to another session.")
    if not will_raise:
        base += " Bring it forward to type."
    self._enqueue(target, "prose", base, False,
                  mute_exempt=True, at_front=True, names_session=True)
    if will_raise:
        self._raise().raise_async(
            identity, gen,
            on_failure=lambda s=target, f=folder: self._raise_failed(s, f))
    return None

# registry registration (focus family block)
@handler(MsgType.JUMP_WAITING)
def _h_jump_waiting(ctx, msg):
    return ctx.host._on_jump_waiting(msg)
```
*(The comments inside the JUMP_WAITING branch — the gen-bump rationale at lines 626-634 etc. — are part of the verbatim body and stay; elided above only for brevity.)*

**The one non-uniform registration — `SET_FOREGROUND` / `SESSION_START`** (line 462, the sole compound branch): ONE method `_on_set_foreground(self, msg)` registered under BOTH keys via stacked decorators (legal because `handler` returns `fn`). The body re-derives `t` (for the inner `if t == MsgType.SESSION_START`) **and** `session`:
```python
@handler(MsgType.SET_FOREGROUND)
@handler(MsgType.SESSION_START)
def _h_set_foreground(ctx, msg):
    return ctx.host._on_set_foreground(msg)
```
`assert_complete` over all 27 types passes because both keys are present.

---

### The complete extraction table — every MsgType → method → source lines → locals to re-derive

(Source line-ranges are the branch bodies in the pre-Step-1 `daemon.py`, identical text post-Step-1 in `daemon/host.py`. **"Locals" = the preamble locals from lines 336-338 the body references**; "—" means re-derive nothing. 27 type-rows, 26 distinct methods.)

| MsgType | Host method | Source lines (body) | Locals to re-derive | Family / Task |
|---|---|---|---|---|
| PROSE | `_on_prose` | 340-366 | `session`, `verbosity` | prose · 3.2 |
| TOOL | `_on_tool` | 416-424 | `session`, `verbosity` | prose · 3.2 |
| EARCON | `_on_earcon` | 426-436 | `session` | prose · 3.2 |
| FLUSH | `_on_flush` | 438-460 | `session` | prose · 3.2 |
| CHOICE | `_on_choice` | 373-388 | `session`, `verbosity` | decisions · 3.3 |
| PLAN | `_on_plan` | 390-401 | `session`, `verbosity` | decisions · 3.3 |
| PERMISSION | `_on_permission` | 403-414 | `session`, `verbosity` | decisions · 3.3 |
| REREAD_OPTIONS | `_on_reread_options` | 594-604 | — | decisions · 3.3 |
| NAV | `_on_nav` | 504-513 | — | navigation · 3.3 |
| PAUSE | `_on_pause` | 515-541 | — | playback · 3.4 |
| MUTE | `_on_mute` | 543-560 | — | playback · 3.4 |
| PIN_TOGGLE | `_on_pin_toggle` | 562-579 | — | playback · 3.4 |
| STOP | `_on_stop` | 484-493 | — | playback · 3.4 |
| SKIP | `_on_skip` | 495-502 | — | playback · 3.4 |
| JUMP_DECISION | `_on_jump_decision` | 647-660 | — | playback · 3.4 |
| JUMP_WAITING | `_on_jump_waiting` | 606-645 | — | focus · 3.4 |
| SESSION_START | `_on_set_foreground` | 462-473 | `t`, `session` | lifecycle · 3.5 |
| SET_FOREGROUND | `_on_set_foreground` *(same method)* | 462-473 | `t`, `session` | lifecycle · 3.5 |
| SESSION_END | `_on_session_end` | 475-482 | `session` | lifecycle · 3.5 |
| SET_RATE | `_on_set_rate` | 662-684 | — | control · 3.5 |
| SET_VOICE | `_on_set_voice` | 686-691 | — | control · 3.5 |
| SET_VERBOSITY | `_on_set_verbosity` | 693-696 | — | control · 3.5 |
| SET_MINQUEUE | `_on_set_minqueue` | 698-707 | — | control · 3.5 |
| CYCLE_VERBOSITY | `_on_cycle_verbosity` | 709-721 | — | control · 3.5 |
| STATUS | `_on_status` | 723-731 | — | control · 3.5 |
| PING | `_on_ping` | 733-734 | — | control · 3.5 |
| RELOAD_KEYMAP | `_on_reload_keymap` | 581-592 | — | hotkeys · 3.5 |

**RELOAD_KEYMAP note:** its body's off-lock `threading.Thread(target=self._reload_hotkeys, name="sonari-keymap-reload", daemon=True).start()` is pasted **verbatim** — do NOT normalize it into the dispatch. The thunk runs under the held lock and returns fast; the real reload work runs off-lock on the spawned thread (the H2 dark-hotkey race fix).

---

### Task 3.2 — Extract the **prose** family (PROSE, TOOL, EARCON, FLUSH)

Per the mechanical rule, extract the four branch bodies (lines 340-366, 416-424, 426-436, 438-460) into `_on_prose / _on_tool / _on_earcon / _on_flush` on the host, re-deriving the locals shown in the table. Replace each ladder branch body with `return self._on_<x>(msg)` (keep the `if`). Register the four `@handler` thunks in a prose-family block (place it where Step 5 will lift it into `features/prose.py` — keep the four together so that lift is a 1:1 move; for now they may live at the bottom of `host.py` or in a `daemon/_handlers_prose.py` imported by `__init__`, executor's choice, but grouped by family).

The ladder still routes every type, so dispatch behavior is unchanged.

**Gate:** suite green (count unchanged from Task 3.1; M = 0 new pins — the black-box net already covers prose ordering / EARCON turn_done flush / minqueue batching behaviorally) **and** explicitly run `tests/test_blackbox_net.py` green. Command: `.venv/bin/python -m pytest -q --ignore=tests/test_kokoro.py`. Commit: `refactor(stage2): extract prose family handlers (prose/tool/earcon/flush)`.

---

### Task 3.3 — Extract the **decisions + navigation** family (CHOICE, PLAN, PERMISSION, REREAD_OPTIONS, NAV)

Extract bodies at lines 373-388, 390-401, 403-414, 594-604, 504-513 into `_on_choice / _on_plan / _on_permission / _on_reread_options / _on_nav`, re-deriving per the table (CHOICE/PLAN/PERMISSION need `session`+`verbosity`; REREAD_OPTIONS and NAV need nothing). Replace each branch with its delegating line; register the five thunks grouped for the Step-5 lift into `features/decisions.py` (CHOICE/PLAN/PERMISSION/REREAD_OPTIONS) and `features/navigation.py` (NAV).

**Gate:** suite green (count unchanged; M = 0 — net covers decision FIFO+cue and 2-level nav seek-and-play) **and** `tests/test_blackbox_net.py` green. Command above. Commit: `refactor(stage2): extract decisions + navigation handlers`.

---

### Task 3.4 — Extract the **playback + focus** family (PAUSE, MUTE, PIN_TOGGLE, STOP, SKIP, JUMP_DECISION, JUMP_WAITING)

Extract bodies at lines 515-541, 543-560, 562-579, 484-493, 495-502, 647-660 into the `playback` methods, and lines 606-645 into `_on_jump_waiting` (focus). None re-derive any preamble local (all derive `fg` in-body). Replace each branch with its delegating line; register the seven thunks grouped for the Step-5 lift into `features/playback.py` (PAUSE/MUTE/PIN_TOGGLE/STOP/SKIP/JUMP_DECISION) and `features/focus.py` (JUMP_WAITING).

**Gate:** suite green (count unchanged; M = 0 — net covers pause/resume re-queue, mute, pin, jump-waiting order; the permanent concurrency guards still pass since the lock model is unchanged) **and** `tests/test_blackbox_net.py` green. Command above. Commit: `refactor(stage2): extract playback + focus handlers`.

---

### Task 3.5 — Extract the **control + lifecycle + hotkeys** family, then **flip `handle_message` to the registry**

This is the closing task: extract the last family, then collapse the ladder.

1. **Extract** bodies at lines 662-684, 686-691, 693-696, 698-707, 709-721, 723-731, 733-734 (control) into `_on_set_rate / _on_set_voice / _on_set_verbosity / _on_set_minqueue / _on_cycle_verbosity / _on_status / _on_ping`; lines 462-473 into `_on_set_foreground` (re-deriving `t` + `session`, registered under BOTH keys via the stacked decorator shown above); lines 475-482 into `_on_session_end` (re-derive `session`); lines 581-592 into `_on_reload_keymap` (off-lock thread spawn **verbatim**). Group thunks for the Step-5 lift into `features/control.py`, `features/lifecycle.py`, `features/hotkeys.py`.

2. **Flip the entry.** With all 27 type-keys now registered, replace the entire ladder body of `handle_message` with the registry call:
   ```python
   def handle_message(self, msg):
       self._ctx.bind(msg)
       return dispatch(self._ctx, msg)
   ```
   Delete the now-dead `if t == ...` ladder (lines 336-736 collapse to the two lines above). The preamble locals (`t`/`session`/`verbosity`) are no longer computed in `handle_message` — each `_on_*` re-derives its own.

3. **Arm the completeness guard.** Call `assert_complete(...)` over all 27 `MsgType` values at registry-population time — at the end of the module that imports every feature thunk (the `daemon/__init__` side-effect import chain, after all `@handler`s have run), so a dropped registration is an import-time red, not a silent runtime no-op. Pass the 27 known types explicitly (enumerate `MsgType.PROSE … MsgType.RELOAD_KEYMAP`).

**New pins this task (M):**
- **Completeness guard pin** (`tests/test_daemon_registry.py`): all 27 `MsgType` values are in `HANDLERS`; and a negative pin — temporarily pop one and assert `assert_complete` raises naming it (restore after).
- **Dispatch-under-lock pin** (`tests/test_daemon_dispatch.py`): monkeypatch `daemon._on_ping` to record `daemon._lock.locked()`, call `daemon._handle_message_guarded({"type":"ping"})`, assert the recorded value is `True` (dispatch runs while the transaction holds the lock). Add the symmetric pin for `_dispatch_hotkey`.
- **Unknown-type pin** (or reuse 3.1): `handle_message({"type":"nonexistent"})` returns `None` (the `_ignore` path reproduces the ladder's trailing `return None`).
- **Reply-row pin:** `handle_message({"type":"ping"})` returns `{"ok": True}` and a STATUS message returns the snapshot dict — confirming exception #2 (reply-producing rows) survives the flip.

**Gate:** suite green (count = prior **+ M** new pins above; no behavior count drops) **and** `tests/test_blackbox_net.py` green — this is the proof the ladder→registry transform is byte-identical end to end. Command: `.venv/bin/python -m pytest -q --ignore=tests/test_kokoro.py`. Commit: `refactor(stage2): flip handle_message to dict registry + assert_complete guard`.

---

**End-of-Step-3 state:** `handle_message` is two lines (`bind` + `dispatch`); all 27 branches live as verbatim `_on_*` methods on the host behind `@handler` thunks grouped by feature family (ready for the Step-5 lift into `features/*.py`); `SessionState.transaction()` is the sole lock boundary at both dispatch sites (same lock object — behavior identical); `assert_complete` guards every `MsgType`. No body has moved off the host, no ledger field relocated, no conftest target changed — the black-box net + existing daemon suite + permanent concurrency guards are all green, proving the transform behavior-preserving.

---

## Execution handoff

This plan is **Phase 1 of Sonari Stage 2** (spec Steps 0–3). On completion — net green, package split, platform contracts in, dispatch table flipped, suite at 701 + the Step-3 pins — the next plan (spec Steps 4–6: extract the `features/*` modules + apply the one approved behavior change) is written, and only then Phase 2 (spec Steps 7–8: the speak-loop/state relocation under the now-proven net).

**Order is load-bearing:** Step 0 first (it is the safety net), then 1 → 2 → 3. Step 2 is independent of 1 and 3 (touches only `platform/**`) and may be reordered if convenient, but 3 depends on 1 (it edits `daemon/host.py`, the post-Step-1 path).
