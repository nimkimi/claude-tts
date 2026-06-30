# SP2 — Keep-Going + Policy-A Preempt + the `foreground()`→`speaker()` Divergence Sweep — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Authored from the adversarially-vetted synthesis `.superpowers/sdd/sp2-recon-synthesis.md` (the design oracle — read it first). All `file:line` quotes below are verified against `HEAD = cf0770e` on branch `design/voice-arbitration`.

**Goal:** Make the voice **keep going** through accumulated background output on its own (advancing `speaker()` alone, never moving the workspace), and make a new submit **preempt only when it should** (Policy A: take the voice iff idle or already-speaker; otherwise ding + accrue). SP2 is the change that first makes `speaker()` diverge from `foreground()`, so it also lands the **complete divergence sweep** of every site written under the old `speaker()==foreground()` invariant.

**Architecture:** Two new primitives — `SpeechQueue.oldest_id()` (non-destructive head-id) and `SessionManager.set_speaker()` (advances the voice owner ONLY, never the workspace). The keep-going engine is one **atomic in-lock block** inside `_speak_loop_once`'s existing normal-branch lock (`host.py:432`): when the speaker is at its live edge and fully idle, scan registered sessions for the longest-waiting (minimum `SpeechItem.id`) eligible background session, `set_speaker` to it, and pop its oldest item — all under the one existing `self._lock`. Policy A replaces the #65 seize-gate in `on_set_foreground` with a workspace-split guard (take voice + workspace only on the idle bootstrap; an auto-advanced speaker self-submitting takes voice already-ours but never drifts the workspace). Four mechanical repoints (`foreground()`→`speaker()`) and one cue-route land FIRST as no-ops while the pointers coincide; keep-going lands LAST, after the sweep is provably complete.

**Tech Stack:** Python 3, `pytest`, the existing daemon (`src/sonari/daemon/*`, `src/sonari/sessions.py`, `src/sonari/queue.py`). macOS-only.

## Global Constraints

- **Baseline:** `858 passed, 1 skipped` (`.venv/bin/python -m pytest -q`, verified at `cf0770e`). Must end green (baseline + new tests).
- **The two PERMANENT concurrency guards (`tests/test_concurrency_guards.py`) stay green at EVERY commit.** T5 EXTENDS their hammer set (a passive `s_bg` session + a `set_speaker` shadow asserting `keep_going_fires > 0` + `CYCLE_SESSION` in the hammer ops) and adds the deterministic flush-race-no-orphan Test A. NEVER weaken an existing assertion.
- **THE SPINE (the #1 risk mitigation — read this first).** The repoints (T1/T2) are **no-ops while `speaker()==foreground()`**, so they land FIRST, safely. The Policy-A gate (T3) lands next. **Keep-going (T4) lands LAST** — it is what first diverges `speaker()` from `foreground()` and activates every `foreground()`-keyed site. The divergence tests (B in T1, E in T3, C in T4) are the **gate that proves the sweep is complete** before T4 flips the switch. Activating keep-going before the sweep is provably complete is the single highest risk (a stranded confirmation cue F3, or a wrong-session cut F1, the instant the pointers diverge) — higher than lock atomicity, which both vets cleared.
- **M1 atomicity (the #2 risk).** The keep-going scan + select + `set_speaker` + pop + `_current_item` claim + `cancel_epoch` capture are **ONE atomic block** under the EXISTING `self._lock` (handlers reach it via `self._state.transaction()` which wraps the same `self._lock` — `state.py:27-30`; the speak loop holds it directly — `host.py:432`; single non-reentrant lock, single speak thread `host.py:510`). **NEVER move the candidate scan outside the lock** — that reintroduces the TOCTOU race (a `FLUSH`/`STOP_SESSION` landing between "candidate has items" and "pop" leaves a claim with no queue entry). No helper on the scan path may re-acquire the lock.
- **macOS-only; no new dependencies.** Python 3 / `say` / `afplay`.
- **TDD:** red → green → commit, bite-sized (one action per step). DRY, YAGNI.
- **Scope fence:** SP2 only. NO frontier/marker (R3/§10 — the next foundational SP), NO voice-global quiet-hold (later SP; leave the one-line seam), NO `focused_session()` promotion / `answer_permission` fallback hardening (later R12 task — see Open Decisions). NO control-grammar taste repoints except behind the T6 gate.

### Open decisions to STATE (not bury)

- **F7 — same-session autonomous self-cut (recommend KEEP, tie to R2).** When the keep-going-advanced speaker B autonomously self-submits, `FLUSH(B)` cuts B's own live readout (`cur.session==B` → `cancel()`). This is the existing same-session supersede path (`host.py:469-471`: `stopped is False` → no re-queue → clean supersede), unchanged by SP2. It sits against R2 ("speaker never auto-cut") only in composition. **Recommendation: keep it** — it IS the ratified Policy-A same-session mechanism. Pinned by Test E's second half. Surfaced here, not silently settled.
- **F8 — SP2 is NOT full R6 completeness.** "Has unheard output" = `len(st.queue) > 0` is the SP2 **proxy** for the spec's §7 frontier. A fast background self-submitter whose `FLUSH` clears its own queue (`prose.py:72`) drops its turn-N queued backlog before keep-going reaches it. Do **not** claim full R6 completeness; the frontier/catch-up SP recovers dropped backlog. State the proxy explicitly.
- **F11 / Test G — fail-closed answer (intended-consistent).** A keep-going-voiced decision on B is unanswerable until you jump: `answer_permission` targets `workspace()` (=A), which has no pending decision → error tone (`decisions.py:188`). Intended: you answer where you are, never the auto-voiced session. Pinned by Test G.
- **§3.5 scope boundary (do not orphan).** SP2 ships the §3.2 workspace-split guard but does NOT promote `focused_session()` to authoritative workspace nor harden `answer_permission`'s fallback — that is the later R12 task. Stated so the hardening isn't lost between R6 and R12.

## Test-harness facts (verified against the repo — use these exact shapes)

- `from tests.daemon_helpers import make_daemon, stream_queue` — **`make_daemon(verbosity="everything", foreground="fg")` returns a 5-tuple `(daemon, queue, speaker, sessions, config)`.** Always unpack all five (use `_` for unused). `make_daemon` `set_foreground`s the `foreground` arg (registering it) and creates its stream; pass `foreground=None` for a no-speaker daemon.
- The fake speaker is `FakeSpeaker` (returned as `speaker`): records spoken text in **`speaker.spoken`** (list; entries may be `None`), earcons in `speaker.earcons` (list), pitches in `speaker.pitches`, `speaker.cancels` (int). `speaker.speak()` returns immediately (`self.complete`, default `True`) — it CANNOT hold a mid-flight assertion, so keep-going observability tests are **synchronous**: run one `_speak_loop_once()`, then assert on the post-state.
- Build messages with a module-local `_msg(t, session, **kw)` helper (define it once per new test module):
  ```python
  def _msg(t, session, **kw):
      from sonari.protocol import PROTOCOL_VERSION
      return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}
  ```
  Drive via `daemon.handle_message(_msg(MsgType.X, session, **kw))`. Import `from sonari.protocol import MsgType`, `from sonari.sessions import Identity`, `from sonari.queue import SpeechItem`.
- `daemon._enqueue(session, kind, text, is_decision, entry=None, mute_exempt=False, pause_exempt=False, at_front=False, names_session=False, audio_path=None)` enqueues directly. `daemon._current_item` is a settable property (`daemon._current_item = SpeechItem(...)`). `daemon._stream(s).queue` is the per-session `SpeechQueue` (its items are `daemon._stream(s).queue._items`, a deque). `daemon._pending_heard` is the marker dict. `daemon._stream(s).stopped` is the per-session stop flag.
- `SessionStart` tests must `monkeypatch.setattr(lifecycle, "_setup_health", lambda v: ("ok", None))` (else `_maybe_guide_setup` may enqueue a guidance cue) — mirror `tests/test_daemon_focus_follow.py:39-41`.
- For T5 (`tests/test_concurrency_guards.py`): the REAL daemon is built by `_make_real_daemon(runner, foreground="s0")` with a `Speaker(say_runner=runner)`; `_FastRunner` churns the loop fast; the existing instance-shadow pattern (`daemon._speak_loop_once = ...`, `daemon.handle_message = ...` at `:112-127`) is how to wrap a bound method — `sessions.set_speaker` is shadowed the same way to count keep-going fires. The module-local `_msg` already exists (`:24-27`).

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `src/sonari/queue.py` | Modify (after `__len__`, `:88-89`) | Add `oldest_id() -> int | None` (non-destructive head-id). |
| `src/sonari/sessions.py` | Modify (after `set_foreground`, `:54-57`) | Add `set_speaker(session)` (sets `_speaker` ONLY). |
| `src/sonari/daemon/host.py` | Modify (`:144-162`, `:220-224`, `:392-452`) | T1: `_voice_busy_elsewhere` clause 2 reads `speaker()`; ding gate reads `speaker()`. T4: add module-level `_stream_quiescent` + `_select_keep_going`; refactor `_voice_busy_elsewhere` clause 2 to call `_stream_quiescent`; the keep-going block inside the normal-branch lock. |
| `src/sonari/daemon/features/prose.py` | Modify (`:80-88`) | T1: `on_flush` cross-session cut reads `speaker()`; update the stale comment. |
| `src/sonari/daemon/features/focus.py` | Modify (`:9-22`) | T1: `_waiting_target` also excludes `speaker()`. T6 (gated): `on_cycle_session` from-index. |
| `src/sonari/daemon/features/playback.py` | Modify (`:79`) | T2: `on_stop_all` cue → `speaker()`. T6 (gated): `on_stop_session` target + cue. |
| `src/sonari/daemon/features/lifecycle.py` | Modify (`:66-69`) | T3: rewrite the #65 seize-gate to Policy A + the workspace-split guard (apply to `SESSION_START`); keep the identity-registration block unconditional. |
| `src/sonari/daemon/features/control.py` | Modify (`:150`) | T6 (gated): `on_where_am_i` report target. |
| `tests/test_queue.py`, `tests/test_sessions.py` | Add | T0 primitives. |
| `tests/test_sp2_divergence.py` (new) | Add | T1: Test B + ding-gate + flush-cut + jump-exclude (parity + forced divergence). |
| `tests/test_sp2_cue_routing.py` (new) | Add | T2: stop-all cue under divergence. |
| `tests/test_sp2_policy_a.py` (new) | Add | T3: Test E + SESSION_START idle-only/idle-bootstrap (+ re-run Test B). |
| `tests/test_sp2_keepgoing.py` (new) | Add | T4: Tests C, D, F, G. |
| `tests/test_concurrency_guards.py` | Modify | T5: extend the stress guard + add Test A. |

**Task order (dependency-ordered):** T0 → T1 → T2 → T3 → T4 → T5 → T6. T6 is gated on a Nima taste decision and is implemented LAST (after T4 makes divergence real).

---

## Task T0 — Primitives (no behavior change)

**Files:** Modify `src/sonari/queue.py`, `src/sonari/sessions.py`. Test: `tests/test_queue.py`, `tests/test_sessions.py`.

**Interfaces produced:** `SpeechQueue.oldest_id() -> "int | None"`; `SessionManager.set_speaker(session) -> None` (sets `_speaker` only — no `_record`, no registration, no `_foreground` write). *Depends on: nothing.*

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_queue.py (append)
from sonari.queue import SpeechQueue, SpeechItem

def _item(i):
    return SpeechItem(id=i, session="a", kind="prose", text="x", is_decision=False)

def test_oldest_id_empty_is_none():
    assert SpeechQueue().oldest_id() is None

def test_oldest_id_returns_head_id_not_tail():
    q = SpeechQueue()
    q.enqueue(_item(7))
    q.enqueue(_item(9))
    assert q.oldest_id() == 7          # the head (oldest), not the tail

def test_oldest_id_does_not_pop():
    q = SpeechQueue()
    q.enqueue(_item(7))
    q.oldest_id()
    assert len(q) == 1                 # non-destructive
```

```python
# tests/test_sessions.py (append)
from sonari.sessions import SessionManager

def test_set_speaker_moves_voice_only():
    sm = SessionManager()
    sm.set_foreground("a")             # both pointers -> a
    sm.set_speaker("b")                # voice -> b
    assert sm.speaker() == "b"
    assert sm.foreground() == "a"      # set_speaker did NOT move the workspace
    assert sm.workspace() == "a"       # workspace tracks foreground (no OS focus)
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_queue.py -k oldest_id tests/test_sessions.py -k set_speaker -v`
Expected: FAIL — `AttributeError: 'SpeechQueue' object has no attribute 'oldest_id'` / `'SessionManager' object has no attribute 'set_speaker'`.

- [ ] **Step 3: Implement the primitives**

In `src/sonari/queue.py`, after `__len__` (`:88-89`):
```python
    def oldest_id(self) -> "int | None":
        """The id of the head (oldest) item, or None when empty. Non-destructive — the
        keep-going selector compares queue ages WITHOUT popping. Keeps _items private;
        the smallest surviving id across all queues is the oldest unheard output (the id
        counter is daemon-global and monotonic)."""
        return self._items[0].id if self._items else None
```

In `src/sonari/sessions.py`, after `set_foreground` (`:54-57`):
```python
    def set_speaker(self, session: str) -> None:
        """Advance the VOICE owner WITHOUT moving the workspace. Keep-going calls this
        to read accumulated background output while _foreground (the last
        deliberately-acted session) stays put — the window never moves on its own
        (R12/D10). Unlike set_foreground()/focus() it writes ONLY _speaker: no folder
        _record, no registration, no _foreground write. Caller holds the daemon lock
        by convention (keep-going runs inside the speak-loop lock)."""
        self._speaker = session
```

- [ ] **Step 4: Run to verify green + guards**

Run: `.venv/bin/python -m pytest tests/test_queue.py tests/test_sessions.py tests/test_concurrency_guards.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/sonari/queue.py src/sonari/sessions.py tests/test_queue.py tests/test_sessions.py
git commit -m "feat(sp2): add SpeechQueue.oldest_id() + SessionManager.set_speaker() primitives"
```

---

## Task T1 — The divergence sweep: REPOINT subset (no-ops today)

Four `foreground()`→`speaker()` repoints, each behavior-identical while `speaker()==foreground()` and each correct under forced divergence (via `set_speaker`). These pin failure modes **F1** (`prose.py:87`), **F5** (`host.py:221`), **F6** (`focus.py:40`) and **Test B / CONC-1** (`host.py:156`).

**Files:** Modify `src/sonari/daemon/host.py` (`:156`, `:221`), `src/sonari/daemon/features/prose.py` (`:80-88`), `src/sonari/daemon/features/focus.py` (`:9-22`). Test: `tests/test_sp2_divergence.py` (new). *Depends on: T0 (`set_speaker` forces divergence in tests).*

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_sp2_divergence.py (new)
from sonari.protocol import MsgType
from sonari.queue import SpeechItem
from tests.daemon_helpers import make_daemon

def _msg(t, session, **kw):
    from sonari.protocol import PROTOCOL_VERSION
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


# --- CHANGE 3 / Test B: _voice_busy_elsewhere reads speaker() (the CONC-1 pin) ---
def test_voice_busy_predicate_reads_speaker_under_divergence():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    daemon._enqueue("B", "prose", "b1", False)
    daemon._enqueue("B", "prose", "b2", False)
    sessions.set_speaker("B")                      # diverge: voice=B (with backlog), workspace=A
    assert sessions.speaker() == "B" and sessions.foreground() == "A"
    # The repointed predicate reads speaker()==B (busy) -> A IS busy-elsewhere.
    # (With the old foreground() read this returns False -> CONC-1 relocated: A would seize B.)
    assert daemon._voice_busy_elsewhere("A") is True
    # End-to-end at the still-#65 gate: A's submit registers only; B keeps the voice.
    daemon.handle_message(_msg(MsgType.SET_FOREGROUND, "A"))
    assert sessions.speaker() == "B"
    assert len(daemon._stream("B").queue) == 2     # b1,b2 untouched (not seized/flushed)

def test_voice_busy_predicate_parity_when_aligned():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    daemon._enqueue("A", "prose", "a1", False)     # the speaker(==foreground) has backlog
    assert daemon._voice_busy_elsewhere("B") is True   # B sees A (the speaker) busy
    assert daemon._voice_busy_elsewhere("A") is False  # A is the speaker -> not "elsewhere"


# --- CHANGE 4 / F5: the ding gate suppresses for the SPEAKER, not foreground ---
def test_ding_gate_uses_speaker_not_foreground():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    sessions.set_speaker("B")                      # voice=B, workspace=A
    daemon._buffer_prose("B", "live from b", None) # minqueue=1 -> flushes now; B is the speaker
    assert "waiting" not in speaker.earcons        # never ding the session currently talking
    daemon._buffer_prose("A", "background a", None)# A is NOT the speaker -> background ding
    assert speaker.earcons.count("waiting") == 1


# --- CHANGE 2 / F1: on_flush cuts only the speaker's own / same-session readout ---
def test_flush_does_not_cut_across_speaker_divergence():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    sessions.set_speaker("B")                      # voice=B, workspace=A
    daemon._current_item = SpeechItem(id=901, session="B", kind="prose",
                                      text="b live", is_decision=False)
    before = speaker.cancels
    daemon.handle_message(_msg(MsgType.FLUSH, "A"))    # autonomous A submits
    assert speaker.cancels == before               # B's live readout NOT cut (Policy A)
    daemon._current_item = SpeechItem(id=902, session="B", kind="prose",
                                      text="b live2", is_decision=False)
    daemon.handle_message(_msg(MsgType.FLUSH, "B"))    # same-session supersede (F7, ratified)
    assert speaker.cancels == before + 1


# --- F6: jump-to-waiting also excludes the speaker ---
def test_jump_waiting_excludes_the_speaker():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    sessions.register("C", cwd="/x/C")
    daemon._enqueue("B", "prose", "b waiting", False)
    daemon._enqueue("C", "prose", "c waiting", False)
    sessions.set_speaker("B")                      # voice=B (already voiced), workspace=A
    daemon.handle_message(_msg(MsgType.JUMP_WAITING, ""))
    assert sessions.speaker() == "C"               # jumps to C, NOT the already-voiced B
```

- [ ] **Step 2: Run to verify they fail (the divergence assertions go red)**

Run: `.venv/bin/python -m pytest tests/test_sp2_divergence.py -q`
Expected: FAIL — the divergence cases read `foreground()` today (`test_voice_busy_predicate_reads_speaker_under_divergence`, `test_ding_gate_uses_speaker_not_foreground`, `test_flush_does_not_cut_across_speaker_divergence`, `test_jump_waiting_excludes_the_speaker`). The parity case may already pass.

- [ ] **Step 3: Apply the four repoints**

`src/sonari/daemon/host.py` `_voice_busy_elsewhere` (CHANGE 3): line 156 `fg = self.sessions.foreground()` → `spk = self.sessions.speaker()`, and update the two following uses (`:157-158`) to `spk`:
```python
        spk = self.sessions.speaker()
        if spk is not None and spk != session:
            st = self._state._streams.get(spk)
            if st is not None and (len(st.queue) > 0 or len(st.prose_buffer) > 0
                                   or st.assembler.has_pending()):
                return True                   # the voice owner still has speech to deliver
```
(Leave clause 1 — the in-flight `_current_item` check at `:153-154` — untouched. The `_stream_quiescent` extraction is deferred to T4.)

`src/sonari/daemon/host.py` `_flush_prose_buffer` ding gate (CHANGE 4): line 221 `and session != self.sessions.foreground()` → `and session != self.sessions.speaker()`.

`src/sonari/daemon/features/prose.py` `on_flush` (CHANGE 2): line 87 `or ctx.host.sessions.foreground() == session):` → `or ctx.host.sessions.speaker() == session):`. Replace the now-stale `:80-85` comment:
```python
    # Cut the current utterance on a new prompt: same-session (the new prompt
    # supersedes the old reply) OR a switch where THIS prompt's session is the
    # current SPEAKER — i.e. the speaker self-submitting (the ratified Policy-A
    # same-session cut, F7). Under keep-going (speaker B, workspace A) an autonomous
    # submit from A is NOT the speaker, so it does NOT cut B's live readout (Policy A:
    # autonomous never cuts; only the speaker or the idle voice does). The typed-vs-
    # autonomous discriminator that would let a human-typed A preempt is Pass-2-deferred.
    # SESSION_START sends no FLUSH, so a bare new session never cuts.
```

`src/sonari/daemon/features/focus.py` `_waiting_target` (F6): also exclude the speaker. After the `blocked, prose = [], []` line, read the speaker once and add it to the skip:
```python
    blocked, prose = [], []
    spk = ctx.host.sessions.speaker()
    for sess, st in ctx.host._streams.items():          # insertion-ordered
        if sess == exclude or sess == spk or st.stopped or len(st.queue) == 0:
            continue
```
(In parity `sess == exclude` already covers it; under divergence this adds the speaker exclusion.)

- [ ] **Step 4: Run to verify green + affected suites + guards**

Run: `.venv/bin/python -m pytest tests/test_sp2_divergence.py tests/test_daemon_prose.py tests/test_daemon_streams.py tests/test_daemon_focus_nav.py tests/test_daemon_stop.py tests/test_concurrency_guards.py -q`
Expected: PASS. If any existing test encoded the old `foreground()` behavior in a way that is NOT a pure rename (an actual behavior expectation), STOP and surface it — do not change behavior silently.

- [ ] **Step 5: Commit**

```bash
git add src/sonari/daemon/host.py src/sonari/daemon/features/prose.py src/sonari/daemon/features/focus.py tests/test_sp2_divergence.py
git commit -m "refactor(sp2): repoint voice-busy/ding/flush-cut/jump-exclude to speaker() (F1/F5/F6 + CONC-1)"
```

---

## Task T2 — Held-branch cue routing (STOP_ALL)

`on_stop_all`'s "All stopped." confirmation is enqueued to `foreground()` today (`playback.py:79`); the held branch reads `speaker()` (`host.py:408`). The instant the pointers diverge the cue lands in a stream the held branch never reads → abrupt silence, no confirm (**F3**, eyes-free). STOP_ALL stops EVERY session including the speaker, so routing the cue to `speaker()` is the target-agnostic required fix. (`on_stop_session`'s cue is coupled to its *target*, which is the T6 taste decision — see T6; it is NOT touched here.)

**Files:** Modify `src/sonari/daemon/features/playback.py` (`:74-80`). Test: `tests/test_sp2_cue_routing.py` (new). *Depends on: T0 (`set_speaker` forces divergence).*

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sp2_cue_routing.py (new)
from sonari.protocol import MsgType
from tests.daemon_helpers import make_daemon

def _msg(t, session, **kw):
    from sonari.protocol import PROTOCOL_VERSION
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}

def test_stop_all_confirmation_voiced_under_divergence():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    daemon._enqueue("B", "prose", "b backlog", False)
    sessions.set_speaker("B")                      # voice=B, workspace=A
    daemon.handle_message(_msg(MsgType.STOP_ALL, ""))
    # The cue must land in the SPEAKER's stream (B) so the held branch can voice it.
    bq = daemon._stream("B").queue
    assert any(it.text == "All stopped." for it in bq._items)
    aq = daemon._stream("A").queue
    assert not any(it.text == "All stopped." for it in aq._items)
    # Proof it is actually heard: the held branch (reads speaker()==B, B is stopped)
    # pops the pause-exempt cue and voices it.
    daemon._speak_loop_once()
    assert any(s and "All stopped." in s for s in speaker.spoken)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_sp2_cue_routing.py -q`
Expected: FAIL — the cue is enqueued to `foreground()`==A; the held branch (speaker==B) never voices it, and the cue is absent from B's stream.

- [ ] **Step 3: Route the STOP_ALL cue to `speaker()`**

`src/sonari/daemon/features/playback.py` `on_stop_all` (`:74-80`): change `fg = ctx.host.sessions.foreground()` → `spk = ctx.host.sessions.speaker()` and use `spk` for the stream + cue:
```python
    spk = ctx.host.sessions.speaker()
    if spk is not None:
        # Ensure the SPEAKER's stream is stopped even if it had no stream yet, then
        # voice the confirmation there (pause_exempt -> the held branch, which reads
        # speaker(), speaks it under divergence).
        ctx.host._stream(spk).stopped = True
        ctx.host._enqueue(spk, "prose", "All stopped.", False,
                          mute_exempt=True, pause_exempt=True)
```

- [ ] **Step 4: Run to verify green + the stop suite + guards**

Run: `.venv/bin/python -m pytest tests/test_sp2_cue_routing.py tests/test_daemon_stop.py tests/test_concurrency_guards.py -q`
Expected: PASS (in parity `speaker()==foreground()`, so existing stop tests are unaffected).

- [ ] **Step 5: Commit**

```bash
git add src/sonari/daemon/features/playback.py tests/test_sp2_cue_routing.py
git commit -m "fix(sp2): route 'All stopped.' cue to speaker() so the held branch voices it (F3)"
```

---

## Task T3 — Policy-A gate + workspace-split guard

Replace the #65 seize-gate (`lifecycle.py:66-69`) with Policy A: take the voice iff the voice is **idle** OR the submitter **is the current speaker**; otherwise register only (ding + accrue). The allow branch splits **voice-take** from **workspace-move**: an auto-advanced speaker self-submitting takes voice (already ours) but must NOT drift the workspace onto an autonomous session (**F2**; R12/M3). The identity-registration block (`:70-83`) stays unconditional. `SESSION_START` runs the same gate (its `is_speaker` disjunct can never fire for a brand-new session → effectively idle-only, M4).

**Files:** Modify `src/sonari/daemon/features/lifecycle.py` (`:66-69`). Test: `tests/test_sp2_policy_a.py` (new). *Depends on: T1 (the `speaker()`-reading `_voice_busy_elsewhere`).*

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_sp2_policy_a.py (new)
import threading
from sonari.protocol import MsgType
from sonari.queue import SpeechItem
from sonari.daemon.features import lifecycle
from tests.daemon_helpers import make_daemon

def _msg(t, session, **kw):
    from sonari.protocol import PROTOCOL_VERSION
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


# --- Test E: the keep-going-advanced speaker self-submitting does NOT move the workspace ---
def test_policy_a_speaker_self_submit_does_not_move_workspace():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    sessions.set_speaker("B")                      # diverge: voice=B, workspace=A
    assert sessions.speaker() == "B" and sessions.foreground() == "A"
    daemon.handle_message(_msg(MsgType.SET_FOREGROUND, "B"))   # B autonomously self-submits
    assert sessions.foreground() == "A"            # workspace did NOT drift onto the speaker
    assert sessions.speaker() == "B"               # voice already ours; unchanged
    # The same-session self-cut still fires (F7, ratified), workspace still A.
    daemon._current_item = SpeechItem(id=11, session="B", kind="prose",
                                      text="b", is_decision=False)
    before = speaker.cancels
    daemon.handle_message(_msg(MsgType.FLUSH, "B"))
    assert speaker.cancels == before + 1
    assert sessions.foreground() == "A"


# --- Test B stays green after the gate swap (asserts on OUTCOMES, not gate internals) ---
def test_policy_a_non_speaker_foreground_does_not_seize():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    daemon._enqueue("B", "prose", "b1", False)
    daemon._enqueue("B", "prose", "b2", False)
    sessions.set_speaker("B")                      # voice=B (busy), workspace=A
    daemon.handle_message(_msg(MsgType.SET_FOREGROUND, "A"))   # A re-submits while B speaks
    assert sessions.speaker() == "B"               # denied: B keeps the voice
    assert len(daemon._stream("B").queue) == 2     # b1,b2 untouched


# --- M4: SESSION_START takes the voice only when idle; identity block is unconditional ---
def test_session_start_does_not_seize_busy_voice(monkeypatch):
    monkeypatch.setattr(lifecycle, "_setup_health", lambda v: ("ok", None))
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    daemon._enqueue("B", "prose", "b busy", False)
    sessions.set_speaker("B")                      # voice busy on B
    daemon.handle_message(_msg(MsgType.SESSION_START, "C", cwd="/x/C",
                               term_program="Apple_Terminal", tty="/dev/ttysC"))
    assert sessions.speaker() == "B"               # idle-only: brand-new C did not seize
    assert sessions.identity("C") is not None      # identity registration ran unconditionally
    assert "C" in sessions.session_ids()

def test_session_start_takes_voice_when_idle(monkeypatch):
    monkeypatch.setattr(lifecycle, "_setup_health", lambda v: ("ok", None))
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)   # no speaker
    daemon.handle_message(_msg(MsgType.SESSION_START, "C", cwd="/x/C",
                               term_program="Apple_Terminal", tty="/dev/ttysC"))
    assert sessions.speaker() == "C"               # idle bootstrap took the voice
    assert sessions.foreground() == "C"            # AND the workspace (first genuine session)
```

- [ ] **Step 2: Run to verify (Test E fails; the others may pass under the #65 gate)**

Run: `.venv/bin/python -m pytest tests/test_sp2_policy_a.py -q`
Expected: `test_policy_a_speaker_self_submit_does_not_move_workspace` FAILS — under the #65 gate, B-self-submit takes the `else` branch (`_voice_busy_elsewhere(B)`==False since B is the speaker) → `set_foreground(B)` → `_foreground` drifts to B. The other three may already pass; the gate rewrite must keep them green.

- [ ] **Step 3: Rewrite the gate to Policy A + the workspace-split guard**

`src/sonari/daemon/features/lifecycle.py` `on_set_foreground` — replace the #65 block (`:66-69`):
```python
    # Policy A (R6 resolved): take the VOICE iff it is idle OR the submitter already
    # owns it (is the speaker); otherwise register only (ding + accrue as a jump/keep-
    # going target). Split voice-take from workspace-move: an auto-advanced speaker
    # self-submitting takes voice (already ours) but must NOT drift the workspace onto
    # an autonomous session (F2; R12/M3 — you'd answer the wrong session). All reads run
    # under self._lock (the handler path holds it), atomic with the speak loop's claim.
    voice_idle = not ctx.host._voice_busy_elsewhere(session)
    is_speaker = (session == ctx.host.sessions.speaker())
    if voice_idle or is_speaker:
        if is_speaker and ctx.host.sessions.speaker() != ctx.host.sessions.foreground():
            # keep-going-advanced speaker self-submitting: voice is ALREADY ours, so do
            # NOT move the workspace onto an auto-advanced session.
            ctx.host.sessions.register(session, cwd=cwd)
        else:
            # idle bootstrap, or speaker==foreground already aligned: take voice + workspace.
            # (Do NOT blanket-remove set_foreground — the single-session / focus-unknown
            # bootstrap needs _foreground set, or answer_permission has no target. The
            # idle-non-speaker move is pre-existing SP1 #65 residual, not SP2-new.)
            ctx.host.sessions.set_foreground(session, cwd=cwd)
    else:
        ctx.host.sessions.register(session, cwd=cwd)     # denied: ding + accrue
```
(The `SESSION_START` identity block at `:70-83` — `register`, `set_identity`, `_maybe_guide_setup`, spearcon pregen — stays exactly as-is, running unconditionally after this gate. The same workspace-split guard now also protects a background `compact`/`resume` re-firing `SESSION_START` for an existing session.)

- [ ] **Step 4: Run to verify green + lifecycle/focus suites + guards**

Run: `.venv/bin/python -m pytest tests/test_sp2_policy_a.py tests/test_sp2_divergence.py tests/test_daemon_focus_follow.py tests/test_daemon_setup_health.py tests/test_concurrency_guards.py -q`
Expected: PASS. (Test B re-asserted here stays green: both the #65 gate and Policy A register-only for a busy non-speaker submit.)

- [ ] **Step 5: Commit**

```bash
git add src/sonari/daemon/features/lifecycle.py tests/test_sp2_policy_a.py
git commit -m "feat(sp2): Policy-A preempt gate + workspace-split guard (R6/M4; F2)"
```

---

## Task T4 — The keep-going engine (the in-lock scan) — LANDS LAST

Add the module-level `_stream_quiescent` + `_select_keep_going`, refactor `_voice_busy_elsewhere` clause 2 to call `_stream_quiescent` (DRY, §2.3), and insert the keep-going block inside `_speak_loop_once`'s EXISTING normal-branch lock (`host.py:432`). This is what first diverges `speaker()` from `foreground()` and activates the whole T1–T3 sweep. Tests C, D, F, G.

**Files:** Modify `src/sonari/daemon/host.py`. Test: `tests/test_sp2_keepgoing.py` (new). *Depends on: T0, T1, T2, T3.*

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_sp2_keepgoing.py (new)
import threading
from sonari.protocol import MsgType
from sonari.queue import SpeechItem
from tests.daemon_helpers import make_daemon

def _msg(t, session, **kw):
    from sonari.protocol import PROTOCOL_VERSION
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


# --- Test C: keep-going advances the voice but NEVER moves the workspace (R12/D10) ---
def test_keep_going_does_not_move_foreground():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    daemon._enqueue("B", "prose", "from b", False)
    daemon._speak_loop_once()                      # A empty/idle -> keep-going adopts B
    assert sessions.speaker() == "B"               # voice advanced
    assert sessions.foreground() == "A"            # workspace stayed put
    assert any(s and "from b" in s for s in speaker.spoken)


# --- Test D: longest-waiting-first = minimum oldest SpeechItem.id (§14), NOT insertion order ---
def test_keep_going_longest_waiting_first():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    sessions.register("late", cwd="/x/late")       # registered FIRST (insertion order would pick it)
    sessions.register("early", cwd="/x/early")
    daemon._enqueue("early", "prose", "older", False)   # lower id (enqueued first)
    daemon._enqueue("late", "prose", "newer", False)    # higher id
    daemon._speak_loop_once()
    assert sessions.speaker() == "early"           # picked min oldest_id, not insertion order
    assert any(s and "older" in s for s in speaker.spoken)
    # (Few items, far below the 200 backlog_cap, so cap eviction never fires — §4. If a
    # variant preloads past the cap, build the streams with cap=None so "oldest" can't
    # silently become "oldest-surviving".)


# --- Test F: keep-going bootstraps from a None speaker (the post-session-end path) ---
def test_keep_going_bootstraps_from_none_speaker():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    daemon._enqueue("B", "prose", "hello b", False)
    sessions.unregister("A")                       # A ends -> _speaker becomes None (sessions.py:93-94)
    assert sessions.speaker() is None
    daemon._speak_loop_once()
    assert sessions.speaker() == "B"               # adopted the background session from None
    assert any(s and "hello b" in s for s in speaker.spoken)


# --- Test G: a keep-going-voiced decision is unanswerable until you jump (R10, fail-closed) ---
def test_keep_going_voiced_decision_unanswerable_until_jump():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    daemon._pending_decisions["B"] = {"event": threading.Event(), "behavior": None}
    daemon._enqueue("B", "permission", "Allow X?", True)
    daemon._speak_loop_once()                      # keep-going voices B's decision
    assert sessions.speaker() == "B"
    assert sessions.workspace() == "A"             # workspace still A (no deliberate move)
    daemon.handle_message(_msg(MsgType.ANSWER_PERMISSION, "", behavior="allow"))
    assert daemon._pending_decisions["B"]["behavior"] is None   # B NOT auto-answered
    assert speaker.earcons[-1] == "error"          # fail-closed error tone (decisions.py:188)
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_sp2_keepgoing.py -q`
Expected: FAIL — no keep-going yet: `_speak_loop_once()` on an empty speaker stream just waits; `speaker()` never advances off A/None.

- [ ] **Step 3: Add the helpers + refactor the predicate + insert the in-lock block**

`src/sonari/daemon/host.py` — add two MODULE-LEVEL functions (above `class SpeechDaemon`, after the imports; they take no `self`):
```python
def _stream_quiescent(st) -> bool:
    """True when *st* has nothing left to voice: no queued items, no buffered prose,
    no half-assembled sentence. None (no stream) counts as quiescent. The inverse of
    _voice_busy_elsewhere's stream clause (shared so the busy predicate and the
    keep-going gate use one definition)."""
    return st is None or (len(st.queue) == 0
                          and len(st.prose_buffer) == 0
                          and not st.assembler.has_pending())


def _select_keep_going(streams, sessions) -> "str | None":
    """The longest-waiting eligible background session, or None. Eligible = a
    registered session other than the current speaker whose stream exists, is not
    stopped, and has a non-empty queue. Among those, pick the minimum
    SpeechQueue.oldest_id() (the globally-monotonic SpeechItem.id of the oldest unheard
    item). Runs INSIDE the speak-loop lock; never pokes _items.

    §14 is longest-waiting-first AT EACH IDLE WINDOW, not global starvation-freedom:
    re-selection happens only at speaker-idle, so a busy speaker drains FIFO ahead of
    older items elsewhere, and a perpetually-busy autonomous producer defers all
    background sessions indefinitely — the escape is a deliberate ⌃⌘J / ⌃⌘Tab."""
    spk = sessions.speaker()
    best = None
    best_id = None
    for s in sessions.session_ids():
        if s == spk:
            continue
        st = streams.get(s)
        if st is None or st.stopped or len(st.queue) == 0:
            continue
        oid = st.queue.oldest_id()
        if oid is None:
            continue
        if best_id is None or oid < best_id:
            best, best_id = s, oid
    return best
```

`_voice_busy_elsewhere` clause 2 (`:157-161`) — refactor to call `_stream_quiescent` (behavior-identical: `not _stream_quiescent(None)` is False, so the st-is-None fall-through is preserved; clause 1 stays inline):
```python
        spk = self.sessions.speaker()
        if spk is not None and spk != session:
            st = self._state._streams.get(spk)
            if not _stream_quiescent(st):
                return True                   # the voice owner still has speech to deliver
        return False
```

`_speak_loop_once` normal-branch lock (`:432-447`) — insert the keep-going block BETWEEN the first `pop_next` and the `_current_item` claim. Keep the existing post-pop sequence (`_current_item` → `cancel_epoch` → `prev` snapshot → `_attributed_text`) intact:
```python
        with self._lock:
            fg = self.sessions.speaker()
            st = self._state._streams.get(fg)
            item = st.queue.pop_next() if st is not None else None
            if item is None and _stream_quiescent(st):
                # KEEP-GOING (M1): the speaker is at its live edge and fully idle.
                # Advance the VOICE (only _speaker) to the longest-waiting eligible
                # background session and pop ITS oldest item — scan+select+set_speaker+
                # pop+claim ALL inside this one lock so a FLUSH/STOP can't race the
                # TOCTOU gap. _foreground is untouched: the workspace never moves on its
                # own (R12/D10). (A later SP gates this scan on a voice-global quiet-hold:
                # add `and not self._voice_quiet_hold` to the condition above.)
                next_sess = _select_keep_going(self._state._streams, self.sessions)
                if next_sess is not None:
                    self.sessions.set_speaker(next_sess)
                    st = self._state._streams.get(next_sess)
                    item = st.queue.pop_next() if st is not None else None
            self._state._current_item = item
            # Capture the speaker's cancel baseline atomically with the claim, so a
            # cancel() arriving during speak() is detected (M2 — the pop->speak gap).
            cancel_epoch = self.speaker.cancel_epoch()
            text = None
            # Snapshot before _attributed_text so we can roll back if a stop interrupts.
            prev = self._state._last_spoken_session
            if item is not None:
                text = self._attributed_text(item)
```

- [ ] **Step 4: Run to verify green + the full divergence/policy/cue suites + guards**

Run: `.venv/bin/python -m pytest tests/test_sp2_keepgoing.py tests/test_sp2_divergence.py tests/test_sp2_policy_a.py tests/test_sp2_cue_routing.py tests/test_daemon_streams.py tests/test_concurrency_guards.py -q`
Expected: PASS. Then the whole suite: `.venv/bin/python -m pytest -q` — 858 baseline + all new SP2 tests, 0 failures.

- [ ] **Step 5: Commit**

```bash
git add src/sonari/daemon/host.py tests/test_sp2_keepgoing.py
git commit -m "feat(sp2): keep-going engine — voice advances through idle background output (R4/§14)"
```

---

## Task T5 — Concurrency-guard extensions (the new interleavings)

Extend the permanent stress guard to exercise the keep-going scan+`set_speaker`+pop under real lock contention (a passive `s_bg` that can ONLY drain via keep-going; a `set_speaker` shadow asserting it fired; `CYCLE_SESSION` racing keep-going's `set_speaker` against `focus()`), and add the deterministic flush-race-no-orphan **Test A** (M1).

**Files:** Modify `tests/test_concurrency_guards.py`. *Depends on: T4.*

- [ ] **Step 1: Add the deterministic Test A (red first)**

Append to `tests/test_concurrency_guards.py`:
```python
class _ReentrantFlusher:
    """speak() fires FLUSH(bg) once, before returning not-completed — a FLUSH landing
    in the keep-going pop->speak gap. bg is NOT stopped, so the L2 re-queue check sees
    stopped=False and does NOT re-queue; note_spoken drops the marker. No orphan survives."""

    def __init__(self, daemon, bg):
        self.daemon = daemon
        self.bg = bg
        self._epoch = 0
        self._fired = False

    def speak(self, text, audio_path=None, cancel_epoch=None):
        if not self._fired:
            self._fired = True
            self.daemon.handle_message(_msg(MsgType.FLUSH, self.bg))
        return False

    def cancel_epoch(self):
        return self._epoch

    def cancel(self):
        self._epoch += 1

    def earcon(self, kind):
        pass


class _HeardEntry:
    def __init__(self):
        self.heard = False


def test_keep_going_flush_race_leaves_no_orphan():
    """M1: a kept-going item is popped+claimed under the lock; a FLUSH fired during
    speak() clears bg's (already-emptied) queue and, because bg is not stopped, the L2
    check does NOT re-queue -> note_spoken drops the marker. If scan+pop were not atomic
    this would strand a claim with no marker."""
    sessions = SessionManager()
    sessions.set_foreground("fg")                  # speaker=fg, empty/idle
    config = {k: (v.copy() if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
    config["verbosity"] = "everything"
    daemon = SpeechDaemon(None, sessions, config)
    speaker = _ReentrantFlusher(daemon, bg="bg")
    daemon.speaker = speaker
    daemon._last_spoken_session = None
    sessions.register("bg", cwd="/x/bg")
    daemon._enqueue("bg", "prose", "race", False, entry=_HeardEntry())

    daemon._speak_loop_once()                      # keep-going pops bg, claims, speaks; FLUSH races

    assert daemon._current_item is None            # claim released
    assert len(daemon._stream("bg").queue) == 0    # item popped; FLUSH cleared nothing extra
    assert daemon._pending_heard == {}             # NO orphaned marker
```
Run: `.venv/bin/python -m pytest tests/test_concurrency_guards.py -k flush_race -v` — expect PASS once T4 is in (the engine already satisfies M1); if it FAILS, the scan/pop atomicity regressed — STOP and fix the engine, not the test.

- [ ] **Step 2: Extend the stress guard**

In `test_stress_no_lost_duplicated_or_resurrected_item`, after the existing 3-session registration loop (`:100-101`):
```python
    # Passive keep-going target: preloaded backlog, NO feeder/hammer thread, excluded
    # from every SET_FOREGROUND op. It can ONLY drain via keep-going, so a non-zero
    # set_speaker count proves the new in-lock scan+pop ran under real contention.
    sessions.register("s_bg", cwd="/x/s_bg")
    for i in range(50):
        daemon._enqueue("s_bg", "prose", "bg {0}.".format(i), False)
    keep_going_fires = [0]
    _orig_set_speaker = sessions.set_speaker
    def _counting_set_speaker(s):                  # set_speaker is called ONLY by keep-going
        keep_going_fires[0] += 1
        return _orig_set_speaker(s)
    sessions.set_speaker = _counting_set_speaker
```
Add `CYCLE_SESSION` to the hammer ops (`:145-146`):
```python
        ops = [MsgType.STOP_SESSION, MsgType.FLUSH, MsgType.SET_FOREGROUND,
               MsgType.JUMP_WAITING, MsgType.CYCLE_SESSION]
```
(Cycle calls `sessions.focus()` — resets BOTH pointers — racing keep-going's `set_speaker()` which moves only `_speaker`; exercises diverge-then-resync under contention. `will_attempt(None)` is False with no identities set, so no raise fires — same as the existing `JUMP_WAITING` op.)

To manufacture idle windows so keep-going reliably fires against the `_FastRunner`, add a tiny sleep in the feeder loop (after `i += 1`):
```python
                time.sleep(0.001)                  # brief gaps so the speaker goes idle -> keep-going fires
```
After the storm, before the orphan assertion (`:190`):
```python
    # The new in-lock keep-going scan+pop actually ran under contention.
    assert keep_going_fires[0] > 0, "keep-going never fired; the idle window was empty"
```
**Do NOT weaken this assertion.** If it flakes, widen the idle window (raise the feeder sleep, or extend the 1.0s storm to 2.0s) — never drop the assertion. The orphan-marker assertion (`:190-196`) already covers keep-going items unchanged.

- [ ] **Step 3: Run both guards (and a few repeats for the probabilistic one)**

Run: `.venv/bin/python -m pytest tests/test_concurrency_guards.py -q && .venv/bin/python -m pytest tests/test_concurrency_guards.py -q -p no:randomly --count 1` (repeat the stress test a handful of times manually if available) 
Expected: PASS, `keep_going_fires > 0` every run, no orphaned markers, speak thread survives.

- [ ] **Step 4: Commit**

```bash
git add tests/test_concurrency_guards.py
git commit -m "test(sp2): extend concurrency guards — keep-going under contention + flush-race no-orphan (M1)"
```

---

## Task T6 — The batched Nima control-grammar decision (TASTE) — GATED ON NIMA

**Do NOT pre-implement.** Three control keys still read `foreground()` (the silent workspace) where, in the keep-going era, the **speaker** (what's talking) may be the intended target. They are control-grammar taste, not mechanical repoints — present them as ONE batched decision, get the answer, then implement the chosen repoints + tests.

**The single question to put to Nima:** *In the keep-going era, do these control keys act on / report the **speaker** (what's currently talking) or the **workspace** (where you are)?*

| Key | Site | Today reads | Recommendation |
|---|---|---|---|
| ⌃⌘S (`stop_session` target + its "Stopped."/"Resumed." cue) | `playback.py:38,48,60` | `foreground()` | **speaker()** — "stop what's talking." Also the only way ⌃⌘S's cue is audible: the held branch voices only the speaker's stream, so a foreground-targeted stop under divergence is silently un-confirmed (F4). The cue follows the target. |
| ⌃⌘Tab (`cycle_session` from-index) | `focus.py:115` | `foreground()` | **speaker()** — cycling *from* the silent workspace while you hear the speaker is surprising. |
| ⌃⌘W (`where_am_i` report subject) | `control.py:150` | `foreground()` | **speaker()** — the reconciliation §8 says ⌃⌘W "reports voice-state." |

**Recommended answer: `speaker()` for all three** ("act on what's talking"). Reasoning: it makes the control grammar consistent with keep-going (the keys act on the session the user is actually hearing), and it is the only choice under which ⌃⌘S's confirmation is reliably voiced (the held branch reads the speaker's stream). Why-not workspace(): keeps the keys anchored to where you'd *type*, but under divergence that is the silent session — surprising for an eyes-free user and it strands ⌃⌘S's cue.

> When this question is put to Nima, play `afplay /System/Library/Sounds/Glass.aiff` in the same turn (he works eyes-free).

- [ ] **Step 1 (after the decision): write the failing tests** for the chosen target (mirror the T1 divergence pattern: force `set_speaker("B")`, assert the key acts on the chosen pointer). For ⌃⌘S, assert the "Stopped."/"Resumed." cue lands in the chosen target's stream AND is voiced by the held branch when that target is the speaker.
- [ ] **Step 2: implement the chosen repoints** — `playback.py:38` (`on_stop_session` `fg = ...foreground()` → the chosen pointer, cue follows), `focus.py:115` (`on_cycle_session` `fg = sessions.foreground()` → chosen), `control.py:150` (`on_where_am_i` `fg = host.sessions.foreground()` → chosen). Keep each behavior-preserving in parity.
- [ ] **Step 3: run the affected suites + guards green; commit** `feat(sp2): control keys (⌃⌘S/⌃⌘Tab/⌃⌘W) act on <speaker|workspace> in the keep-going era (T6 taste)`.

*Depends on: T4 (divergence must exist for the choice to be meaningful); gated on Nima.*

---

## Final: full suite + sweep verification

- [ ] **Run the whole suite:** `.venv/bin/python -m pytest -q` — Expected: 858 baseline + all new SP2 tests, 0 failures, both concurrency guards green.
- [ ] **Confirm the SP2 divergence is real and contained:** after a keep-going advance, `sessions.speaker() != sessions.foreground()` is reachable, AND `foreground()` only ever moves via a deliberate setter (`set_foreground`/`focus`), never via `set_speaker`.

---

## The complete `foreground()`→`speaker()` sweep — every site ruled (provably exhaustive)

Every `.foreground()`-reading site in `src/sonari/` (plus the SP1-correct `workspace()`/`speaker()` sites), each assigned a task or KEPT with a recorded reason. The **14 `foreground()`-reading sweep sites** are rows 1–14; row 15 is the gate rewrite; rows 16–18 are SP1-CORRECT (verified/pinned). Verdicts and line numbers verified at `cf0770e`.

| # | Site | Reads | SP2 verdict | Task |
|---|---|---|---|---|
| 1 | `host.py:156` `_voice_busy_elsewhere` clause 2 | `foreground()` | REPOINT → `speaker()` (CHANGE 3; CONC-1 pin) | **T1** |
| 2 | `host.py:221` `_flush_prose_buffer` ding gate | `foreground()` | REPOINT → `speaker()` (CHANGE 4; F5) | **T1** |
| 3 | `prose.py:87` `on_flush` cross-session cut | `foreground()` | REPOINT → `speaker()` (CHANGE 2; F1) + comment | **T1** |
| 4 | `playback.py:79` `on_stop_all` "All stopped." | `foreground()` | CUE-ROUTE → `speaker()` (F3) | **T2** |
| 5 | `playback.py:38,48,60` `on_stop_session` target + cue | `foreground()` | TASTE → recommend `speaker()` (F4) | **T6** |
| 6 | `focus.py:40→9-22` `_waiting_target` exclude | `foreground()` | ALSO EXCLUDE `speaker()` (F6) | **T1** |
| 7 | `focus.py:115` `on_cycle_session` from-index | `foreground()` | TASTE → recommend `speaker()` (F13) | **T6** |
| 8 | `control.py:150` `on_where_am_i` report subject | `foreground()` | TASTE → recommend `speaker()` (F13) | **T6** |
| 9 | `decisions.py:202` `on_reread_options` | `foreground()` | KEEP — workspace-scoped (reread where you are); arguably `workspace()`, out of SP2 scope | — |
| 10 | `control.py:55` `on_set_rate` "Rate N." | `foreground()` | KEEP (minor) — workspace-local config cue; delayed, not lost | — |
| 11 | `control.py:104` `on_cycle_verbosity` "Verbosity X." | `foreground()` | KEEP (minor) — same as rate | — |
| 12 | `control.py:119` `on_status` "foreground" key | `foreground()` | KEEP — CLI diagnostic; literally reports foreground | — |
| 13 | `playback.py:12` `on_stop` (`STOP`) | `foreground()` | LATENT — `STOP` in protocol but UNBOUND in keymap; same divergence class if ever rebound (F12) | — (note) |
| 14 | `sessions.py:82` `is_foreground()` | `foreground()` | KEEP — it IS the definition of foreground; `should_speak` consumer effectively dead | — |
| 15 | `lifecycle.py:66-69` `on_set_foreground` | (gate) | REWRITE — Policy A + workspace-split guard | **T3** |
| 16 | `decisions.py:185` `on_answer_permission` | `workspace()` | CORRECT (SP1) — §3.2 guard keeps `workspace()` off an auto-advanced speaker | pinned by Test G (**T4**) |
| 17 | `navigation.py:104,107` `on_nav` | `workspace()`/`speaker()` | CORRECT (SP1) | — |
| 18 | `playback.py:91,92` `on_jump_decision` | `workspace()`/`speaker()` | CORRECT (SP1) | — |

---

## Self-Review

**1. Spec / synthesis coverage (against §6 sweep + §7 failure modes + §8 tasks):**
- **§8 task decomposition** followed exactly: T0 primitives → T1 REPOINT subset → T2 held-branch cue routing → T3 Policy-A gate + workspace-split guard → T4 keep-going (LAST) → T5 concurrency-guard extensions → T6 batched Nima taste. ✓
- **§6 sweep (14 sites):** all 14 `foreground()`-reading sites assigned a task (rows 1–4,6 → T1/T2; rows 5,7,8 → T6) or KEPT with a recorded reason (rows 9–14); the gate (row 15) → T3; the 3 SP1-CORRECT sites (rows 16–18) verified/pinned. Provably exhaustive — every `.foreground()` read in `src/sonari/` is in the table. ✓
- **§7 failure modes:** F1 (T1 CHANGE 2 + Test in T1), F2 (T3 guard + Test E), F3 (T2 + cue-routing test), F4 (T6 TASTE), F5 (T1 CHANGE 4 + ding test), F6 (T1 jump-exclude test), F7 (KEEP, tied to R2, pinned by Test E half 2), F8 (Open Decisions — queue proxy, not full R6), F9 (§14 framing in `_select_keep_going` docstring + Test D uncapped note), F10 (corrected `enqueue_front` direction — front cues carry a fresh HIGHER id, deprioritizing; baked into the `oldest_id` reasoning), F11/F12 (Open Decisions + row 13 LATENT note), F13 (T6). ✓
- **Tests A–G:** A→T5 (`test_keep_going_flush_race_leaves_no_orphan`), B→T1 (`test_voice_busy_predicate_reads_speaker_under_divergence`, re-asserted in T3 on outcomes), C→T4 (`test_keep_going_does_not_move_foreground`), D→T4 (`test_keep_going_longest_waiting_first`), E→T3 (`test_policy_a_speaker_self_submit_does_not_move_workspace`), F→T4 (`test_keep_going_bootstraps_from_none_speaker`), G→T4 (`test_keep_going_voiced_decision_unanswerable_until_jump`). All seven have real assertion code using verified harness shapes. ✓

**2. Placeholder scan:** every code step has complete, runnable code (no `...`, no TODO). The only deferred specifics are the T6 repoints — deliberately gated on Nima's taste decision, not a placeholder.

**3. Type / name consistency across tasks:** `set_speaker(session) -> None` (T0 def; consumed in T4's keep-going block + T5 shadow), `oldest_id() -> "int | None"` (T0 def; consumed in `_select_keep_going`), `_stream_quiescent(st) -> bool` and `_select_keep_going(streams, sessions) -> "str | None"` (T4 module-level defs; the keep-going block calls them by bare name; `_voice_busy_elsewhere` calls `_stream_quiescent`). Named identically everywhere. The keep-going block keeps the existing post-pop sequence (`_current_item` → `cancel_epoch` → `prev` → `_attributed_text`) intact under the one lock.

**4. The M1 atomicity invariant** is stated in Global Constraints, embedded in the T4 block comment, and pinned by Test A (T5). The "never move the scan outside the lock" warning appears in both places.

**Open question for the build (flag, don't guess):** if any existing test in `test_daemon_stop.py` / `test_daemon_prose.py` / `test_daemon_focus_nav.py` encodes the old `foreground()` behavior as a real expectation (not a pure rename), STOP and surface it — the parity assertions assume `speaker()==foreground()` holds for every pre-SP2 path, which the SP1 invariant guarantees, but verify rather than assume.
