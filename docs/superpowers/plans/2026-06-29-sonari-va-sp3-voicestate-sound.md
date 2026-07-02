# SP3 — Voice-Global State Machine + Sound Rewire + Cycle Defects — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use `superpowers:subagent-driven-development` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Authored from the adversarially-vetted synthesis `.superpowers/sdd/sp3-recon-synthesis.md` (the design ORACLE — read it first) plus four main-session verification overrides folded in below (Fork 4; hot-path read discipline; control-cue-audibility; all four forks PENDING). All `file:line` quotes are verified against `HEAD = 16b59cf` on branch `design/voice-arbitration`.

**Goal:** Introduce the ONE decision layer SP1/SP2 never made explicit — the **voice-global state** (`flowing` / `quiet-hold` / `stopped-all`, SPEC §6) — and wire five things onto it: (a) the enum + its transitions under the one lock; (b) a keep-going **suppression gate** so a deliberate quiet is *lasting* (R7); (c) `⌃⌘W`/STATUS **reporting** of the state; (d) the §11 **sound rewire** (the "something landed" ding becomes the turn-completion earcon for non-speakers, `turn_done` is suppressed for the flowing speaker, the `waiting` earcon is retired, the permission double-earcon self-heals); (e) the two live-test **cycle defects** (anchor-drift + muted-in-roster dead-stop). SP3 **rebuilds only this layer** — it keeps the streams, `SpeechQueue`, `ProseAssembler`, the speak-loop pop+claim+speak mechanism, and `SessionHistory` untouched.

**Architecture:** One new field — `SessionState._voice_state` (a 3-value `str`, default `"flowing"`), matching the Stage-2/Step-7 pattern (cross-thread state lives on `SessionState`, read on the hot path as `self._state._voice_state`, bridged for cold-path callers by a host `voice_state` property). Handlers write it under the existing lock (`transaction()`); the speak loop reads it inside its existing lock block. The keep-going gate becomes a **pure additive read** — `and self._state._voice_state == "flowing"` appended to the one `if` at `host.py:473`. Quiet-hold/stopped-all ENTRY hangs off ⌃⌘S / ⌃⌘M (next to the existing `st.stopped` writes); re-engage LIFTS hang off the deliberate hotkeys (⌃⌘S-start / ⌃⌘J / ⌃⌘D / cross-nav). Two BLOCKER reconciles keep the enum honest: born-muted (a session created under stopped-all is born `stopped`) and SESSION_END-reconcile (a departing muted speaker lifts a phantom hold). The sound rewire is independent of the entry/lift wiring (it only reads the enum). The cycle defects are isolated LAST behind the Nima forks.

**Tech Stack:** Python 3.13, `pytest`, the existing daemon (`src/sonari/daemon/*`, `src/sonari/sessions.py`, `src/sonari/session_stream.py`, `src/sonari/queue.py`). macOS-only, no new dependencies.

## Global Constraints

- **Baseline:** `887 passed, 1 skipped` (`.venv/bin/python -m pytest -q`, verified at `16b59cf`; the 1 skip = `test_kokoro.py`, numpy). Must end green (baseline + new tests; the FLIP/REWRITE/DELETE tests in each task's breaking-test block replace the old expected values).
- **The THREE PERMANENT concurrency guards (`tests/test_concurrency_guards.py`) stay green at EVERY commit.** They are `test_stress_no_lost_duplicated_or_resurrected_item` (`:86`), `test_reentrant_stop_flush_requeues_item_exactly_once` (`:268`), `test_keep_going_flush_race_leaves_no_orphan` (`:330`). **EXTEND the hammer, NEVER weaken.** `STOP_SESSION`/`CYCLE_SESSION` are ALREADY in the hammer ops (`:167-168`); T5 ADDS `STOP_ALL`. Never drop `keep_going_fires[0] > 0` (`:205`) — if it flakes, widen the idle window, never delete it.
- **M1 atomicity (the #1 risk).** The keep-going gate is a **pure additive read inside the ONE existing lock block** (`host.py:469-499`). scan + `_select_keep_going` + `set_speaker` + `pop_next` + `_current_item` claim + `cancel_epoch` capture stay atomic. **NEVER move the scan out of the lock; never add a new lock; never read the enum outside a lock scope** (F5/F7). The gate adds one boolean term to the existing `if`, nothing else.
- **Hot/cold read discipline (main-session override 2).** The keep-going gate — on the hot path — reads `self._state._voice_state` **DIRECTLY** (the Step-7 rule: hot path reads `self._state._X`; property shims measured +10% and were REJECTED in this repo). The host `voice_state` property is **COLD-PATH ONLY** (⌃⌘W handler, STATUS, tests). Do NOT introduce a `_voice_quiet_hold` shim — the synthesis lists it as optional and itself prefers the direct `== "flowing"` read (YAGNI; a name with no hot-path consumer).
- **R12:** `_foreground` is written ONLY by `set_foreground` / `focus` / `unregister`. A hold LIFT writes the ENUM only — never `_foreground`, never `st.stopped`. Stop handlers never move the workspace.
- **Invariant Q1 (F10/M6):** quiet-hold is entered **only** via a path that sets `st.stopped=True` on the speaker's stream (⌃⌘S already does). This is load-bearing: the gate covers only the keep-going *scan*, not the ungated primary `pop_next` (`host.py:472`) — the primary pop is safe under hold ONLY because the held branch (`host.py:441`, speaker stream stopped) fires first and never reaches it. A quiet-hold entry that omits `st.stopped=True` would leak speaker output during a hold. A deterministic test asserts `voice_state=="quiet-hold" ⟹ speaker stream stopped`.
- **macOS-only; no new dependencies.** TDD: red → green → commit, bite-sized (one action per step). DRY, YAGNI.
- **Scope fence (each NOT-SP3 boundary is cited in synthesis §1):** NO frontier / browse-cursor / two-position marker (→ SP4). NO catch-up key / navigable-pile absorption (→ SP5) — SP3's only interim absorption is ⌃⌘S-start = resume-and-drain (`playback.py:46-51`); do NOT write the R7:193 "never auto-blast" as an SP3 test. NO persistence — **`_voice_state` is TRANSIENT, explicitly NOT serialized; a live hold is lost on daemon restart (→ `flowing`). Acceptable (→ SP6); state it so it isn't mistaken for a bug.** NO tool-transcript fidelity (→ SP4). NO change to `_voice_busy_elsewhere` (keeps the 4 #65 canaries untouched, F8).

- **THE SPINE (risk-ordering — read this first).** **T0 lands the enum with default `flowing`, so the gate is a NO-OP** — every SP2 keep-going test, the 3 permanent guards, and all `make_daemon` consumers are unaffected (F9). **T1 makes the gate LIVE** and carries the two BLOCKERs (F1 SESSION_END phantom-hold, F2 born-muted primary-pop leak) — this is the single highest-risk task (concurrency-core + the primary-pop leak the enum-gate alone does NOT close), higher than the sound rewire; it gets OPUS review. **T2 (re-engage lifts) and T3 (sound rewire) are mutually independent** — each needs only T0/T1. **T4 (cycle defects) lands LAST** — it needs all four Nima fork answers and it races keep-going (OPUS). **T5 extends the guards** (OPUS). Activating a live gate before the two blockers are proven is the way a deliberate quiet silently breaks (F1) or silently leaks (F2) — that is why T1 is one task, RED-first, and reviewed.

### Open decisions to STATE (not bury) — ALL FOUR forks are PENDING (Nima is AFK; leans presented, no answer yet)

The plan is structured so **T0–T3 + T5 are fully buildable NOW** (fork-independent) and **T4 is the sole fork-gated task**. T4 is coded to the leans below with per-fork deltas; DO NOT build it until Nima ratifies.

- **Fork 1 — cycle anchor (`workspace()` vs `speaker()`).** Does ⌃⌘Tab step from what you HEAR (`speaker()`, SP2-T6 status quo) or your front window (`workspace()`)? **Lean: `workspace()`** — the direct fix for the live-test anchor-drift (keep-going advances `speaker()` between presses → nondeterministic landings). One-line swap at `focus.py:126`; flips one test (`test_sp2_t6_control_grammar.py:72`).
- **Fork 2 — muted sessions in the cycle roster (KEEP vs SKIP).** **Lean: KEEP + the RATIFIED land-and-keep-go** (`RECON:184,207` #3). KEEP is the only choice consistent with the already-ratified cycle-onto-muted behavior and keeps muted sessions keyboard-reachable. SKIP is load-bearing-bad: combined with jump excluding stopped (`focus.py:20`) and jump-widening owned by no SP, **no hotkey could reach a muted session**.
- **Fork 3 — submit-lifts-hold (DEFER vs IMPLEMENT).** SPEC §6 (`:287`) says a typed submit lifts a hold, but safe submit-lift needs the typed-vs-autonomous discriminator the spec **itself parks to Pass-2** (`SPEC:542`). **Lean: DEFER** — an unconditional submit-lift lets an autonomous /loop tick silently defeat a deliberate quiet (violates R7 "lasting quiet"); reached instead via ⌃⌘J/⌃⌘Tab (Policy A's own escape hatch). Consequence to state: **while held, typing a new prompt does nothing to the voice until you press a hotkey.**
- **Fork 4 — ⌃⌘S start-target (NEW; main-session verification).** The synthesis §5(c)#3 claimed Fork 2 resolves the "can't un-mute a non-speaker" hole. **That is WRONG: reachability ≠ startability.** Trace: cycle onto muted A → ratified keep-go leaves `workspace()==A`, `speaker()==C` (active) → ⌃⌘S (`on_stop_session` targets `speaker()`, `playback.py:40`) STOPS C instead of STARTING A; under stopped-all with `speaker()==None` it error-tones. R7's "navigate to it, then start it (⌃⌘S)" is **keyboard-unreachable**. **Lean: ASYMMETRIC** (spec-faithful — R7 says "Stop-the-speaker" for stop but "start the session you navigated to" for start): *if `workspace()`'s stream is stopped → ⌃⌘S STARTS the workspace session (and moves the voice onto it via `set_speaker` so it is actually heard); else ⌃⌘S STOPS the speaker.* Deltas for (b) T6-pure-always-speaker and (c) always-workspace are listed in T4.

### Co-design flag — TWO new spoken words for Nima's ear (NOT a structural fork)

The ⌃⌘W state cues introduce **two new eyes-free strings** that ship as speech Nima will hear. Per the co-design rule (spoken wording for his cockpit is co-designed, not blessed finished), fold these into the SAME ratification batch as the four forks — one question, zero extra round-trip:

- **`"On hold."`** — ⌃⌘W when `voice_state == "quiet-hold"` (replaces what the old per-session path would speak as "Stopped").
- **`"Nothing playing."`** — ⌃⌘W None-branch, the playable-workspace cue (see T0/T4).
- (`"All stopped."` under stopped-all is **not new** — it reuses the existing ⌃⌘M / `on_stop_all` confirmation at `playback.py:82`; no flag needed.)

Ask verbatim alongside the forks: *"and 2 new spoken words — 'On hold.' / 'Nothing playing.' — OK, or your call?"* If he re-words, it is a one-string swap per site (no structural change).

## Test-harness facts (verified against the repo at 16b59cf — use these EXACT shapes)

- `from tests.daemon_helpers import make_daemon, stream_queue`. **`make_daemon(verbosity="everything", foreground="fg")` returns a 5-tuple `(daemon, queue, speaker, sessions, config)`** — always unpack all five (`_` for unused). It `set_foreground`s the `foreground` arg and creates its stream; pass `foreground=None` for a no-speaker daemon. It passes `spearcons=FakeSpearconCache()`.
- The fake speaker is `FakeSpeaker`: `speaker.spoken` (list; entries may be `None`), `speaker.earcons` (list), `speaker.pitches` (list), `speaker.audio_paths` (list), `speaker.cancels` (int), `speaker.complete` (default `True`). `speak()` returns immediately, so observability tests are **synchronous**: run one `_speak_loop_once()`, then assert on post-state.
- Build messages with a module-local `_msg`:
  ```python
  def _msg(t, session, **kw):
      from sonari.protocol import PROTOCOL_VERSION
      return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}
  ```
  Drive via `daemon.handle_message(_msg(MsgType.X, session, **kw))`. Import `from sonari.protocol import MsgType`, `from sonari.queue import SpeechItem`.
- `daemon._enqueue(session, kind, text, is_decision, entry=None, mute_exempt=False, pause_exempt=False, at_front=False, names_session=False, audio_path=None)` enqueues directly. `daemon._stream(s).queue` is the per-session `SpeechQueue` (`._items` is a deque). `daemon._stream(s).stopped` is the per-session stop flag. `daemon._current_item` is a settable property. `daemon._pending_heard` is the marker dict.
- **`ctx.session` returns `self._msg.get("session", "")`** (`context.py:33-35`) — a sessionless EARCON msg (`choice`/`plan`/`permission`) has `ctx.session == ""`. This is the M3 gate's sessionless trap: `"" == speaker()` must never be evaluated.
- The EARCON hook emission (`hooks_entry.py`): `choice` (`:53`), `plan` (`:62`), `permission` (`:78`) carry **NO session**; only `turn_done` (`:91`) carries the session. Verified.
- `SessionStart` tests must `monkeypatch.setattr(lifecycle, "_setup_health", lambda v: ("ok", None))` (else `_maybe_guide_setup` may enqueue a guidance cue) — mirror `tests/test_daemon_focus_follow.py:39-41`.
- For T5 (`tests/test_concurrency_guards.py`): the REAL daemon is built by `_make_real_daemon(runner, foreground="s0")` with `Speaker(say_runner=runner)`; `_FastRunner` churns the loop fast; the instance-shadow pattern (`daemon._speak_loop_once = ...`, `daemon.handle_message = ...` at `:129-144`) wraps a bound method; `sessions.set_speaker` is shadowed the same way to count keep-going fires (`:114-118`). The module-local `_msg` already exists (`:24-27`).

## File Structure

| File | Change | Task |
|---|---|---|
| `src/sonari/daemon/state.py` | Modify (`__init__`, `:18-25`) | T0: add `self._voice_state = "flowing"`. |
| `src/sonari/daemon/host.py` | Modify (`:73-143` props; `:175-180` `_stream`; `:473` gate; `:257-261` waiting fire) | T0: host `voice_state` property + gate. T1: born-muted in `_stream`. T3: retire the waiting fire. |
| `src/sonari/daemon/features/playback.py` | Modify (`:40-64` ⌃⌘S; `:67-84` ⌃⌘M) | T1: quiet-hold + stopped-all enum writes. T2: ⌃⌘S-start lift + ⌃⌘D lift. T4: Fork-4 ⌃⌘S target. |
| `src/sonari/daemon/features/lifecycle.py` | Modify (`:100-109` SESSION_END) | T1: SESSION_END reconcile (F1). T4: submit-lift (Fork 3=B only). |
| `src/sonari/daemon/features/focus.py` | Modify (`:40-110` ⌃⌘J; `:113-147` ⌃⌘Tab) | T2: ⌃⌘J lift + "No session waiting." pause_exempt. T4: cycle anchor + muted-landing. |
| `src/sonari/daemon/features/navigation.py` | Modify (`:108` cross) | T2: nav-cross lift (inside `if crossed:` only). |
| `src/sonari/daemon/features/control.py` | Modify (`:110-141` STATUS; `:144-191` ⌃⌘W) | T0: voice-state reporting + state-aware None-branch + STATUS `voice_state` key. |
| `src/sonari/daemon/features/prose.py` | Modify (`:53-65` `on_earcon`) | T3: M3 turn_done gate + stale comment fix. |
| `src/sonari/session_stream.py` | Modify (`:25`, `:37`) | T3: remove `waiting_signaled`. |
| `src/sonari/platform/macos/earcon.py` | Modify (`:15`) | T3: drop `"waiting"` from `_DEFAULTS`. |
| `src/sonari/cli/control.py` | Modify (`:37-38`) | T0: `_cmd_status` voice-state summary line (M7). |
| `tests/test_sp3_voicestate.py` (new) | Add | T0: gate no-op, ⌃⌘W wording, STATUS key, None-branch. |
| `tests/test_sp3_hold_entry.py` (new) | Add | T1: quiet-hold/stopped-all entry, born-muted (F2), SESSION_END (F1), Q1, hold-suppresses-keep-going. |
| `tests/test_sp3_lifts.py` (new) | Add | T2: re-engage lifts + audible "No session waiting." under hold. |
| `tests/test_sp3_sound.py` (new) | Add | T3: M3 gate, muted-dings (F11), waiting retirement. |
| `tests/test_sp3_cycle.py` (new) | Add | T4 (fork-gated): anchor, muted-landing, Fork-4 ⌃⌘S. |
| Existing test files | FLIP/REWRITE/DELETE per each task's breaking-test block | T0/T3/T4 |

**Task order:** T0 → T1 → T2 → T3 → (Nima ratifies forks) → T4 → T5. T2 and T3 are independent. T4 is fork-gated and LAST.

---

## Task T0 — Enum primitive + keep-going gate + ⌃⌘W/STATUS reporting (no transitions → pure no-op)

Add `SessionState._voice_state` (default `"flowing"`), a host `voice_state` cold-path property, the additive keep-going gate, and voice-state reporting (⌃⌘W wording + state-aware None-branch, STATUS key, `_cmd_status` line). **No transitions are wired yet**, so the enum stays `flowing` and behavior is unchanged — the gate is a proven no-op before T1 makes it live (F9). *Review: standard.*

**Files:** `src/sonari/daemon/state.py` (`:18-25`), `src/sonari/daemon/host.py` (`:137-143` props, `:473`/`:479-480` gate), `src/sonari/daemon/features/control.py` (`:110-141` STATUS, `:144-191` ⌃⌘W), `src/sonari/cli/control.py` (`:37-38`). Test: `tests/test_sp3_voicestate.py` (new). *Depends on: nothing.*

**Interfaces produced:** `SpeechDaemon.voice_state` (property, get/set → `self._state._voice_state`, COLD-PATH). Hot path reads `self._state._voice_state` directly.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_sp3_voicestate.py (new)
from sonari.protocol import MsgType
from tests.daemon_helpers import make_daemon


def _msg(t, session, **kw):
    from sonari.protocol import PROTOCOL_VERSION
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


# --- the enum defaults flowing; the property bridges it (cold path) ---
def test_voice_state_defaults_flowing():
    daemon, *_ = make_daemon(foreground="fg")
    assert daemon.voice_state == "flowing"
    assert daemon._state._voice_state == "flowing"       # hot-path read target


# --- the gate: keep-going is SUPPRESSED when the enum is not flowing ---
def test_gate_suppresses_keep_going_when_not_flowing():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    sessions.register("bg", cwd="/x/bg")
    daemon._enqueue("bg", "prose", "from bg", False)
    daemon._state._voice_state = "quiet-hold"            # set manually (real entry lands in T1)
    daemon._speak_loop_once()                            # fg idle -> gate blocks the scan
    assert sessions.speaker() == "fg"                    # voice did NOT advance to bg
    assert not any(s and "from bg" in s for s in speaker.spoken)


# --- regression guard: default flowing -> keep-going STILL fires (F9 no-op) ---
def test_gate_noop_keep_going_fires_when_flowing():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    sessions.register("bg", cwd="/x/bg")
    daemon._enqueue("bg", "prose", "from bg", False)
    daemon._speak_loop_once()
    assert sessions.speaker() == "bg"                    # advanced (no regression)
    assert any(s and "from bg" in s for s in speaker.spoken)


# --- ⌃⌘W flowing wording is UNCHANGED (Nima's ear-approved SP-B grammar preserved) ---
def test_where_am_i_flowing_wording_unchanged():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="fg")
    sessions.set_foreground("fg", cwd="/x/work")
    daemon.handle_message(_msg(MsgType.WHERE_AM_I, "fg"))
    daemon._speak_loop_once()
    assert speaker.spoken == ["work. Playing. 0 waiting."]


# --- state-aware None-branch: speaker() None but a workspace exists + flowing
#     -> report "Nothing playing." instead of an error tone (R7 discoverability) ---
def test_where_am_i_none_speaker_with_workspace_reports_nothing_playing():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground=None)
    sessions.focus("w", cwd="/x/work")                   # workspace=w, speaker=w
    sessions.set_speaker(None)                           # legit None speaker, workspace stays w
    assert sessions.speaker() is None and sessions.workspace() == "w"
    daemon.handle_message(_msg(MsgType.WHERE_AM_I, ""))
    daemon._speak_loop_once()
    assert speaker.spoken == ["Nothing playing."]


# --- STATUS surfaces the voice-state ---
def test_status_reports_voice_state():
    daemon, *_ = make_daemon(foreground="fg")
    reply = daemon.handle_message(_msg(MsgType.STATUS, ""))
    assert reply["voice_state"] == "flowing"
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_sp3_voicestate.py -q`
Expected: FAIL — `AttributeError: 'SpeechDaemon' object has no attribute 'voice_state'` / no `_voice_state` on `SessionState` / `test_gate_suppresses_...` shows `speaker()=="bg"` (unsuppressed) / `KeyError: 'voice_state'` on STATUS / the None-branch errors instead of speaking.

- [ ] **Step 3: Add the enum field + host property**

`src/sonari/daemon/state.py` `SessionState.__init__` (after `:25` `self._last_spoken_session = None`):
```python
        # The voice-global mode (SPEC §6): exactly one of "flowing" / "quiet-hold"
        # / "stopped-all". Born flowing so the keep-going gate is a no-op until a
        # deliberate ⌃⌘S / ⌃⌘M transitions it (T1). Read on the hot path directly
        # as self._state._voice_state; cold-path callers use host.voice_state.
        # TRANSIENT: not serialized -- a live hold is lost on daemon restart (-> SP6).
        self._voice_state = "flowing"
```

`src/sonari/daemon/host.py` — add among the ledger shims (after the `_next_id` setter, `:143`):
```python
    @property
    def voice_state(self):
        """The voice-global mode ("flowing"/"quiet-hold"/"stopped-all"). COLD-PATH
        shim (⌃⌘W, STATUS, tests, handlers); the speak-loop gate reads
        self._state._voice_state directly (Step-7 hot-path discipline)."""
        return self._state._voice_state

    @voice_state.setter
    def voice_state(self, value):
        self._state._voice_state = value
```

- [ ] **Step 4: Wire the keep-going gate + replace the seam comment**

`src/sonari/daemon/host.py` `_speak_loop_once`, the keep-going condition (`:473`):
```python
            if item is None and _stream_quiescent(st) and self._state._voice_state == "flowing":
```
Replace the now-stale seam comment (`:479-480`, the `_voice_quiet_hold` note) with:
```python
                # own (R12/D10). The scan is gated on the voice-global state in the
                # condition above: keep-going advances the voice ONLY while `flowing`
                # (a deliberate quiet-hold / stopped-all suppresses it — R7 "lasting
                # quiet"). Read directly off _state on the hot path.
```

- [ ] **Step 5: Voice-state reporting (⌃⌘W additive wording + None-branch, STATUS key, CLI line)**

`src/sonari/daemon/features/control.py` `on_where_am_i` — replace the None-branch (`:154-157`) and the state-word line (`:165`):
```python
    fg = host.sessions.speaker()
    if fg is None:
        # speaker() None is LEGITIMATE post-SP3 (stopped-all all-ended; cycle-onto-
        # muted with nothing active). Report the voice-state to a PLAYABLE workspace
        # stream rather than error-toning (R7 discoverability). DELIVERY NOTE: the loop
        # plays speaker() (None here), so the cue must land where keep-going can adopt
        # it — a NON-stopped workspace stream (keep-going skips stopped streams). A
        # muted/None workspace has nothing voiceable -> the honest fallback is the error
        # earcon. (A workspace with no stream yet counts as playable: _enqueue creates it
        # non-stopped and keep-going then adopts it.)
        # BEHAVIOR NAMED (vs (c)#4 "⌃⌘W never moves the voice"): (c)#4 forbids ⌃⌘W
        # STEALING the voice from an ACTIVE speaker. Here speaker() is None — the voice
        # is IDLE — so keep-going adopting the playable workspace (effectively
        # set_speaker(workspace) on the next loop turn) is the idle voice landing on
        # where you already are, NOT a steal. This is intended, not a (c)#4 violation.
        ws = host.sessions.workspace()
        ws_st = host._streams.get(ws) if ws is not None else None
        playable = ws is not None and not (ws_st is not None and ws_st.stopped)
        if playable:
            vs = host.voice_state
            cue = ("All stopped." if vs == "stopped-all"
                   else "On hold." if vs == "quiet-hold"
                   else "Nothing playing.")
            host._enqueue(ws, "prose", cue, False, mute_exempt=True, pause_exempt=True)
        else:
            host.speaker.earcon("error")
        return None
```
And the per-session state word (`:165`) becomes voice-state-aware (additive — the flowing branch is byte-identical to today, so Nima's ear-approved wording is untouched):
```python
    vs = host.voice_state
    if vs == "stopped-all":
        state = "All stopped"
    elif vs == "quiet-hold":
        state = "On hold"
    else:
        state = "Stopped" if (st is not None and st.stopped) else "Playing"
```

`src/sonari/daemon/features/control.py` `on_status` — add the key (after `"current_item"`, `:140`) and update the stale comment (`:138-139`):
```python
        # The voice-global mode (SPEC §6): flowing / quiet-hold / stopped-all. This
        # SUBSUMES the old "no global stop_all flag" note — stopped-all is now a
        # first-class state surfaced here (per-stream st.stopped stays in "sessions").
        "current_item": host._state._current_item is not None,
        "voice_state": host.voice_state,
    }
```

`src/sonari/cli/control.py` `_cmd_status` — add voice-state to the summary line (`:32-38`, `.get()` for old-daemon tolerance):
```python
        voice_state = reply.get("voice_state")
        vs_str = voice_state if voice_state is not None else "?"
        print("Uptime: {0}  |  Sessions: {1}  |  Speaking: {2}  |  Voice: {3}".format(
            uptime_str, count_str, speaking_str, vs_str))
```

- [ ] **Step 6: Run green + the breaking test (one FLIP)**

The additive wording keeps every existing ⌃⌘W / spearcon exact-string test GREEN (flowing → "Playing"/"Stopped" unchanged). **One decision to record — `test_daemon_where_am_i.py:42` `test_where_am_i_no_foreground_errors` SURVIVES:** with `make_daemon(foreground=None)` BOTH `speaker()` and `workspace()` are None (no OS focus, no `_foreground`), so the None-branch hits `earcon("error")` — "genuinely nothing to report". Do NOT change it. (The "Nothing playing." path needs a live workspace; it is covered by `test_where_am_i_none_speaker_with_workspace_reports_nothing_playing`.)

Run: `.venv/bin/python -m pytest tests/test_sp3_voicestate.py tests/test_daemon_where_am_i.py tests/test_daemon_spearcon.py tests/test_daemon_control.py tests/test_sp2_keepgoing.py tests/test_concurrency_guards.py -q`
Expected: PASS (new file green; existing ⌃⌘W/spearcon/control/keep-going/guards unchanged). Then full suite `.venv/bin/python -m pytest -q` → `887 passed + 6 new, 1 skipped`.

- [ ] **Step 7: Commit**

```bash
git add src/sonari/daemon/state.py src/sonari/daemon/host.py src/sonari/daemon/features/control.py src/sonari/cli/control.py tests/test_sp3_voicestate.py
git commit -m "feat(sp3): voice-global state enum + keep-going gate + ⌃⌘W/STATUS reporting (no-op; T0)"
```

---

## Task T1 — Quiet-hold + stopped-all ENTRY + the two BLOCKERs (F1, F2)

Wire the ENTRY transitions (⌃⌘S → quiet-hold, ⌃⌘M → stopped-all) next to the existing `st.stopped` writes, and land the two reconciles that keep the enum honest: **born-muted** (F2 — a session created under stopped-all is born `stopped`, closing the ungated-primary-pop leak the gate alone misses) and **SESSION_END-reconcile** (F1 — a departing muted speaker lifts a phantom hold so the voice isn't dead-forever). *Review: **OPUS** — concurrency-core; F1/F2 are the two BLOCKERs and the primary-pop leak is not closed by the gate.*

**Files:** `src/sonari/daemon/features/playback.py` (`:53` ⌃⌘S stop branch, `:67-84` ⌃⌘M), `src/sonari/daemon/host.py` (`:175-180` `_stream` born-muted), `src/sonari/daemon/features/lifecycle.py` (`:100-109` SESSION_END). Test: `tests/test_sp3_hold_entry.py` (new). *Depends on: T0.*

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_sp3_hold_entry.py (new)
from sonari.protocol import MsgType
from sonari.daemon.features import lifecycle
from tests.daemon_helpers import make_daemon


def _msg(t, session, **kw):
    from sonari.protocol import PROTOCOL_VERSION
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


# --- ENTRY: ⌃⌘S -> quiet-hold; Q1 invariant (speaker stream stopped) ---
def test_stop_session_enters_quiet_hold_and_stops_speaker_stream():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    daemon.handle_message(_msg(MsgType.STOP_SESSION, "A"))
    assert daemon.voice_state == "quiet-hold"
    assert daemon._stream(sessions.speaker()).stopped is True     # Q1


def test_stop_all_enters_stopped_all():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    daemon.handle_message(_msg(MsgType.STOP_ALL, ""))
    assert daemon.voice_state == "stopped-all"
    assert daemon._stream("A").stopped and daemon._stream("B").stopped


# --- the hold SUPPRESSES keep-going for everyone (end-to-end) ---
def test_quiet_hold_suppresses_keep_going():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    daemon._enqueue("B", "prose", "b backlog", False)
    daemon.handle_message(_msg(MsgType.STOP_SESSION, "A"))         # quiet-hold, A stopped
    daemon._speak_loop_once()                                     # held branch; no keep-go
    assert sessions.speaker() == "A"
    assert not any(s and "b backlog" in s for s in speaker.spoken)


# --- F1: SESSION_END of the MUTED speaker lifts a phantom quiet-hold ---
def test_session_end_of_muted_speaker_lifts_phantom_quiet_hold():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    daemon._enqueue("B", "prose", "b backlog", False)
    daemon.handle_message(_msg(MsgType.STOP_SESSION, "A"))         # quiet-hold on A
    daemon.handle_message(_msg(MsgType.SESSION_END, "A"))          # the muted speaker ends
    assert daemon.voice_state == "flowing"                        # phantom hold cleared
    assert sessions.speaker() is None
    daemon._speak_loop_once()                                     # keep-going resumes onto B
    assert sessions.speaker() == "B"
    assert any(s and "b backlog" in s for s in speaker.spoken)


# --- F1: SESSION_END under stopped-all STAYS stopped-all (others still muted) ---
def test_session_end_under_stopped_all_stays_stopped_all():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    daemon.handle_message(_msg(MsgType.STOP_ALL, ""))
    daemon.handle_message(_msg(MsgType.SESSION_END, "A"))
    assert daemon.voice_state == "stopped-all"


# --- F2: a session born AFTER ⌃⌘M (speaker ended -> None bootstrap) is muted + silent ---
def test_session_born_under_stopped_all_is_muted_and_silent(monkeypatch):
    monkeypatch.setattr(lifecycle, "_setup_health", lambda v: ("ok", None))
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    daemon.handle_message(_msg(MsgType.STOP_ALL, ""))             # stopped-all; A stopped
    daemon.handle_message(_msg(MsgType.SESSION_END, "A"))         # speaker() -> None; state stays
    assert sessions.speaker() is None and daemon.voice_state == "stopped-all"
    daemon.handle_message(_msg(MsgType.SESSION_START, "N", cwd="/x/N",
                               term_program="Apple_Terminal", tty="/dev/ttysN"))
    daemon._enqueue("N", "prose", "late output", False)
    assert daemon._stream("N").stopped is True                   # born muted (closes primary-pop leak)
    daemon._speak_loop_once()
    assert not any(s and "late output" in s for s in speaker.spoken)


# --- F2 negative: under quiet-hold a NEW session is born ACTIVE (piles + dings, not muted) ---
def test_session_born_under_quiet_hold_is_active(monkeypatch):
    monkeypatch.setattr(lifecycle, "_setup_health", lambda v: ("ok", None))
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    daemon.handle_message(_msg(MsgType.STOP_SESSION, "A"))        # quiet-hold (only A muted)
    daemon.handle_message(_msg(MsgType.SESSION_START, "N", cwd="/x/N",
                               term_program="Apple_Terminal", tty="/dev/ttysN"))
    daemon._enqueue("N", "prose", "n out", False)
    assert daemon._stream("N").stopped is False                  # born active under quiet-hold


# --- ⌃⌘W wording for the now-reachable states (state word; speaker present) ---
def test_where_am_i_reports_on_hold_under_quiet_hold():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="fg")
    sessions.set_foreground("fg", cwd="/x/work")
    daemon.handle_message(_msg(MsgType.STOP_SESSION, "fg"))
    daemon.handle_message(_msg(MsgType.WHERE_AM_I, "fg"))
    daemon._speak_loop_once()                                    # pause_exempt cue voices under hold
    assert speaker.spoken[-1] == "work. On hold. 0 waiting."


def test_where_am_i_reports_all_stopped_under_stopped_all():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="fg")
    sessions.set_foreground("fg", cwd="/x/work")
    daemon.handle_message(_msg(MsgType.STOP_ALL, ""))
    daemon.handle_message(_msg(MsgType.WHERE_AM_I, "fg"))
    daemon._speak_loop_once()
    assert speaker.spoken[-1] == "work. All stopped. 0 waiting."
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_sp3_hold_entry.py -q`
Expected: FAIL — `voice_state` stays `"flowing"` after ⌃⌘S/⌃⌘M (no entry wired); the born-under-stopped-all session's stream is un-stopped and "late output" IS spoken (leak); the phantom hold is never lifted; ⌃⌘W says "Playing"/"Stopped" not "On hold"/"All stopped".

- [ ] **Step 3: Wire the ENTRY transitions**

`src/sonari/daemon/features/playback.py` `on_stop_session` stop branch — after `st.stopped = True` (`:53`), add the enum write:
```python
        st.stopped = True
        ctx.host.voice_state = "quiet-hold"          # SPEC §6: ⌃⌘S on the speaker -> quiet-hold
```
(Do NOT write it in the RESUME branch — the ⌃⌘S-start LIFT lands in T2.)

`src/sonari/daemon/features/playback.py` `on_stop_all` — after the `for st ... st.stopped = True` loop (`:72-73`), add:
```python
    ctx.host.voice_state = "stopped-all"             # SPEC §6/§270: every session muted
```

- [ ] **Step 4: Born-muted (F2) in `_stream`**

`src/sonari/daemon/host.py` `_stream` — inside the lazy-create block (`:177-179`):
```python
        s = self._state._streams.get(session)
        if s is None:
            s = SessionStream(queue_cap=self._backlog_cap)
            if self._state._voice_state == "stopped-all":
                # Born-muted (F2/M2, SPEC:270-272): a session created while the voice
                # is stopped-all is born stopped, so the ungated primary pop can't
                # speak it (the gate covers only the keep-going scan). ONLY under
                # stopped-all — under quiet-hold a new session piles + dings.
                s.stopped = True
            self._state._streams[session] = s
        return s
```
(`_stream` is always called under the lock, so the `_state` read is lock-consistent.)

- [ ] **Step 5: SESSION_END reconcile (F1)**

`src/sonari/daemon/features/lifecycle.py` `on_session_end` (`:100-109`):
```python
@handler(MsgType.SESSION_END)
def on_session_end(ctx, msg):
    session = ctx.session
    was_speaker = (session == ctx.host.sessions.speaker())
    ctx.host.sessions.unregister(session)
    # F1/M1: if the departing session WAS the muted speaker holding a quiet-hold, the
    # enum would otherwise stay "quiet-hold" with _speaker now None -> keep-going
    # permanently skipped (voice dead) and ⌃⌘W inverts into an error tone. Lift it.
    # stopped-all STAYS (the other sessions remain individually muted).
    if was_speaker and ctx.host.voice_state == "quiet-hold":
        ctx.host.voice_state = "flowing"
    st = ctx.host._streams.get(session)
    if st is not None:
        ctx.host._drop_pending(st.queue.clear())
    ctx.host.history.reset(session)
    ctx.host._streams.pop(session, None)
    return None
```

- [ ] **Step 6: Run green + SURVIVE checks (voice_state layers ON TOP of st.stopped)**

Run: `.venv/bin/python -m pytest tests/test_sp3_hold_entry.py tests/test_daemon_stop.py tests/test_blackbox_net.py tests/test_sp2_t6_control_grammar.py tests/test_daemon_setup_health.py tests/test_concurrency_guards.py -q`
Expected: PASS. The existing stop/stop-all/resume tests SURVIVE because SP3 adds the enum ALONGSIDE `st.stopped` (never replaces it). Then full suite `.venv/bin/python -m pytest -q` → green.

- [ ] **Step 7: Commit**

```bash
git add src/sonari/daemon/features/playback.py src/sonari/daemon/host.py src/sonari/daemon/features/lifecycle.py tests/test_sp3_hold_entry.py
git commit -m "feat(sp3): quiet-hold/stopped-all entry + born-muted (F2) + SESSION_END reconcile (F1); T1"
```

---

## Task T2 — Re-engage LIFTS at the unambiguous hotkeys (fork-independent)

A hold lifts (→ `flowing`) on a **deliberate** re-engage: ⌃⌘S-start, ⌃⌘J, ⌃⌘D, a cross-nav. A lift writes the ENUM only — the specifically-muted session's `st.stopped` is NEVER cleared by a lift (except ⌃⌘S-start, which un-stops its OWN target). Also fold in the control-cue-audibility fix (main-session override 3): ⌃⌘J's "No session waiting." becomes `pause_exempt` so it voices under a hold. **Submit-lift is NOT wired (Fork 3 = DEFER).** *Review: standard.*

**Files:** `src/sonari/daemon/features/playback.py` (`:49` ⌃⌘S-start, `:88-97` ⌃⌘D), `src/sonari/daemon/features/focus.py` (`:54-55` cue, `:63` ⌃⌘J), `src/sonari/daemon/features/navigation.py` (`:108-109` cross). Test: `tests/test_sp3_lifts.py` (new). *Depends on: T1.*

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_sp3_lifts.py (new)
from sonari.protocol import MsgType
from tests.daemon_helpers import make_daemon


def _msg(t, session, **kw):
    from sonari.protocol import PROTOCOL_VERSION
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


# --- ⌃⌘S-start lifts (state-based: lifts even with nothing queued, (c)#10) ---
def test_ctrl_s_start_lifts_hold():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    daemon.handle_message(_msg(MsgType.STOP_SESSION, "A"))         # quiet-hold, A stopped, nothing queued
    daemon.handle_message(_msg(MsgType.STOP_SESSION, "A"))         # ⌃⌘S-start (resume)
    assert daemon.voice_state == "flowing"                        # lifted (state-based)
    assert daemon._stream("A").stopped is False


# --- ⌃⌘J lifts + the stopped one STAYS muted (R7:191) ---
def test_jump_lifts_hold_and_leaves_stopped_muted():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    daemon._enqueue("B", "prose", "b backlog", False)
    daemon.handle_message(_msg(MsgType.STOP_SESSION, "A"))         # quiet-hold on A
    daemon.handle_message(_msg(MsgType.JUMP_WAITING, ""))
    assert daemon.voice_state == "flowing"
    assert sessions.speaker() == "B"
    assert daemon._stream("A").stopped is True                    # A stays muted (lift != un-mute)


# --- ⌃⌘J with NO target: does NOT lift, but the cue is AUDIBLE under hold (override 3) ---
def test_jump_no_target_is_audible_under_hold_and_does_not_lift():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    daemon.handle_message(_msg(MsgType.STOP_SESSION, "A"))         # quiet-hold, A stopped, no other session
    daemon.handle_message(_msg(MsgType.JUMP_WAITING, ""))          # nothing waiting
    daemon._speak_loop_once()                                     # held branch pops pause_exempt
    assert any(s and "No session waiting." in s for s in speaker.spoken)
    assert daemon.voice_state == "quiet-hold"                     # no jump happened -> no lift


# --- ⌃⌘D (jump-decision) lifts (R5 jump-class) ---
def test_jump_decision_lifts_hold():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    daemon._enqueue("A", "permission", "Allow X?", True)
    daemon.handle_message(_msg(MsgType.STOP_SESSION, "A"))         # quiet-hold
    daemon.handle_message(_msg(MsgType.JUMP_DECISION, ""))
    assert daemon.voice_state == "flowing"


# --- a CROSS-nav lifts; a WITHIN-response nav does NOT ---
def test_nav_cross_lifts_but_within_does_not():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    daemon.history.record("B", "prose", "b msg"); daemon.history.end_message("B")
    daemon._enqueue("B", "prose", "b backlog", False)
    daemon.handle_message(_msg(MsgType.STOP_SESSION, "A"))         # quiet-hold, speaker=workspace=A
    # within-response nav on A (not crossed: workspace()==speaker()==A) -> NO lift
    daemon.history.record("A", "prose", "a msg"); daemon.history.end_message("A")
    daemon.handle_message(_msg(MsgType.NAV, "", to="prev"))
    assert daemon.voice_state == "quiet-hold"                     # within-nav did NOT lift
    # now cross to B via OS focus -> crossed -> lift
    from sonari.sessions import Identity
    sessions.set_identity("B", Identity(term_program="Apple_Terminal", tty="/dev/ttysB"))
    sessions.set_os_focus(term_program="Apple_Terminal", tty="/dev/ttysB")
    daemon.handle_message(_msg(MsgType.NAV, "", to="prev"))
    assert daemon.voice_state == "flowing"                        # cross-nav lifted
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_sp3_lifts.py -q`
Expected: FAIL — no lifts wired (`voice_state` stays `"quiet-hold"` after ⌃⌘S-start/⌃⌘J/⌃⌘D/cross-nav); "No session waiting." never voiced under hold (not pause_exempt).

- [ ] **Step 3: Wire the four lifts + the audibility fix**

`src/sonari/daemon/features/playback.py` `on_stop_session` RESUME branch — after `st.stopped = False` (`:49`):
```python
        st.stopped = False
        ctx.host.voice_state = "flowing"             # ⌃⌘S-start counts as re-engage (SPEC:286)
```

`src/sonari/daemon/features/playback.py` `on_jump_decision` — after `crossed = ...` (`:95`), lift (⌃⌘D is a deliberate R5 jump-class act; writing flowing is safe even when there's nothing to jump to):
```python
    crossed = target != sessions.speaker()
    ctx.host.voice_state = "flowing"                 # ⌃⌘D re-engage (R5:149 groups jump/⌃⌘D)
```

`src/sonari/daemon/features/focus.py` `on_jump_waiting` — make the None-branch cue audible under hold (`:54-55`):
```python
            ctx.host._enqueue(tgt, "prose", "No session waiting.", False,
                              mute_exempt=True, pause_exempt=True)
```
and add the LIFT after the real jump's `focus(target)` (`:63`):
```python
    ctx.host.sessions.focus(target)
    ctx.host.voice_state = "flowing"                 # ⌃⌘J re-engage (SPEC:190,287)
```

`src/sonari/daemon/features/navigation.py` `on_nav` — lift ONLY inside the `if crossed:` branch (`:108-109`); a within-response browse must NOT lift:
```python
    if crossed:
        sessions.focus(target)                     # move the voice to the navigated session
        ctx.host.voice_state = "flowing"           # cross-nav is a deliberate re-engage; within-nav is not
```

- [ ] **Step 4: Run green + affected suites + guards**

Run: `.venv/bin/python -m pytest tests/test_sp3_lifts.py tests/test_daemon_focus_nav.py tests/test_daemon_stop.py tests/test_sp3_hold_entry.py tests/test_concurrency_guards.py -q`
Expected: PASS (the lifts are additive enum writes; no existing behavior changes). Then full suite `.venv/bin/python -m pytest -q` → green.

- [ ] **Step 5: Commit**

```bash
git add src/sonari/daemon/features/playback.py src/sonari/daemon/features/focus.py src/sonari/daemon/features/navigation.py tests/test_sp3_lifts.py
git commit -m "feat(sp3): re-engage lifts (⌃⌘S-start/⌃⌘J/⌃⌘D/cross-nav) + audible 'No session waiting.' under hold; T2"
```

---

## Task T3 — Sound rewire: retire `waiting`, the M3 turn_done gate, F11 (fork-independent)

Retire the mid-turn `waiting` earcon; make the **turn-completion `turn_done`** the "something landed" ding for non-speakers + the muted ex-speaker, **suppressed only for the flowing speaker** (M3 gate). The buffered-prose flush at the turn boundary stays UNCONDITIONAL. The permission double-earcon self-heals. Independent of T2. *Review: standard.*

**Files:** `src/sonari/daemon/features/prose.py` (`:53-65` `on_earcon`), `src/sonari/daemon/host.py` (`:257-261` waiting fire), `src/sonari/session_stream.py` (`:25`, `:37`), `src/sonari/platform/macos/earcon.py` (`:15`). Test: `tests/test_sp3_sound.py` (new) + the breaking-test block below. *Depends on: T0 (reads `voice_state`).*

- [ ] **Step 1: Write the new failing tests**

```python
# tests/test_sp3_sound.py (new)
from sonari.protocol import MsgType
from tests.daemon_helpers import make_daemon


def _msg(t, session, **kw):
    from sonari.protocol import PROTOCOL_VERSION
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


# --- M3 core: the FLOWING speaker's turn_done is SUPPRESSED (heard live, req 18) ---
def test_flowing_speaker_turn_done_suppressed():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    daemon.handle_message(_msg(MsgType.EARCON, "fg", kind="turn_done"))
    assert speaker.earcons == []


# --- a NON-speaker's turn_done DINGS ("something landed", req 16) ---
def test_non_speaker_turn_done_dings():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    sessions.register("bg", cwd="/x/bg")
    daemon.handle_message(_msg(MsgType.EARCON, "bg", kind="turn_done"))
    assert speaker.earcons == ["turn_done"]


# --- F11: the MUTED ex-speaker still dings under hold (session==speaker but NOT flowing;
#     guards against the C2 speaker-only-suppression mis-implementation) ---
def test_muted_ex_speaker_dings_under_hold():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    daemon.handle_message(_msg(MsgType.STOP_SESSION, "A"))       # quiet-hold: A is speaker AND muted
    speaker.earcons.clear()
    daemon.handle_message(_msg(MsgType.EARCON, "A", kind="turn_done"))
    assert speaker.earcons == ["turn_done"]                     # R7:192-193 muted piles + dings


# --- the flush side-effect at turn_done is UNCONDITIONAL (survives suppression) ---
def test_turn_done_flush_survives_earcon_suppression():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    config["minqueue"] = 5
    daemon.handle_message(_msg(MsgType.PROSE, "fg", delta="Only one. ", index=0, final=True))
    assert len(daemon._stream("fg").queue) == 0                 # held below threshold
    daemon.handle_message(_msg(MsgType.EARCON, "fg", kind="turn_done"))
    assert speaker.earcons == []                                # earcon suppressed (flowing speaker)
    assert len(daemon._stream("fg").queue) > 0                 # ... but the flush STILL ran


# --- waiting RETIRED: background prose no longer dings mid-turn ---
def test_background_prose_no_longer_dings_mid_turn():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    daemon.handle_message(_msg(MsgType.PROSE, "bg", delta="chatter. ", index=0, final=False))
    assert speaker.earcons == []


# --- sessionless choice/plan/permission earcons are UNAFFECTED (the trap) ---
def test_sessionless_decision_earcons_still_fire():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    daemon.handle_message(_msg(MsgType.EARCON, "", kind="choice"))
    assert speaker.earcons == ["choice"]


# --- permission double-earcon self-heals when waiting retires ---
def test_permission_earcon_no_longer_doubles_with_waiting():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    sessions.register("bg", cwd="/x/bg")
    config["minqueue"] = 5
    daemon._buffer_prose("bg", "pending prose.", None)          # held below threshold, no earcon yet
    assert speaker.earcons == []
    daemon.handle_message(_msg(MsgType.PERMISSION_REQUEST, "bg", tool="Bash", summary="ls"))
    assert speaker.earcons == ["permission"]                    # was ["permission","waiting"] pre-SP3
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_sp3_sound.py -q`
Expected: FAIL — `test_flowing_speaker_turn_done_suppressed` sees `["turn_done"]`; `test_turn_done_flush_survives...` sees `["turn_done"]`; `test_background_prose_no_longer_dings_mid_turn` sees `["waiting"]`; `test_permission_earcon_no_longer_doubles...` sees `["permission","waiting"]`.

- [ ] **Step 3: The M3 gate in `on_earcon`**

`src/sonari/daemon/features/prose.py` `on_earcon` (`:53-65`) — rewrite (also drops the stale Windows comment `:56-57`):
```python
@handler(MsgType.EARCON)
def on_earcon(ctx, msg):
    session = ctx.session
    kind = msg.get("kind", "")
    # The turn-completion ding is the "something landed" cue (SPEC §11): it fires for
    # non-speaking sessions AND the muted ex-speaker, and is SUPPRESSED only for the
    # session you are hearing live (session == speaker() AND voice is flowing). Branch
    # on turn_done ONLY: choice/plan/permission EARCON msgs are SESSIONLESS
    # (ctx.session == ""), so the session==speaker() test must never reach them.
    if kind == "turn_done":
        host = ctx.host
        if not (session == host.sessions.speaker() and host.voice_state == "flowing"):
            host.speaker.earcon(kind)
        # End-of-turn boundary: flush any sub-threshold buffered prose UNCONDITIONALLY
        # (a message below the minqueue threshold must still be read) — the flush
        # survives the earcon suppression above.
        host._flush_prose_buffer(session)
    else:
        ctx.host.speaker.earcon(kind)
    return None
```

**Known edge (F4 — ACCEPT + DOCUMENT, do not fix).** The gate reads `speaker()` at the moment the `turn_done` EARCON is HANDLED, not at turn-end. If keep-going advances `speaker()` off the just-finished session in the window between its turn ending and its `turn_done` message arriving, the gate sees `session != speaker()` and **dings a turn you just heard live** (one extra ding). This is rare (requires another session's backlog to win keep-going in that sub-turn gap) and self-limiting (one ding, never a stream of them). The synthesis (`§4 F4`) rules it acceptable — chasing it would need a per-turn "was-this-the-live-speaker" latch that the sessionless-earcon trap (`ctx.session==""`) makes fragile. Leave it; this note is the documentation.

- [ ] **Step 4: Retire the `waiting` earcon (fire + flag + default)**

`src/sonari/daemon/host.py` `_flush_prose_buffer` — DELETE the debounce+fire block (`:252-261`, the comment + the `if (not st.waiting_signaled ...)` through `st.waiting_signaled = True`). The method ends after the enqueue loop (`:250-251`). The `Stop` hook fires once per turn, which IS the debounce — no per-stream flag is needed.

`src/sonari/session_stream.py` — remove the `waiting_signaled` declaration (`:25`) and its reset (`:37`, the line in `reset_for_new_prompt`).

`src/sonari/platform/macos/earcon.py` — drop `"waiting"` from `_DEFAULTS` (`:15`). (Retirement-safe: an unknown/missing earcon key is a silent no-op — `speaker.py:102-104` — so a stale persisted `earcons.waiting` on an installed machine is harmless; no config migration.)

- [ ] **Step 5: Run the new file green (breaking-test edits are Step 6)**

Run: `.venv/bin/python -m pytest tests/test_sp3_sound.py -q`
Expected: PASS. (The existing suite is still RED here — Step 6 lands the breaking-test edits.)

- [ ] **Step 6: Land the breaking-test edits (verbatim — no placeholders)**

**`tests/test_daemon_streams.py`:**
- **DELETE** `test_background_prose_fires_one_waiting_earcon` (`:126-129`), `test_foreground_prose_does_not_fire_waiting` (`:131-134`), `test_waiting_rearms_after_new_prompt` (`:142-148`) — they pin the retired `waiting` earcon + its debounce (new ding coverage lives in `test_sp3_sound.py`).
- **REPLACE** `test_stopped_background_does_not_fire_waiting` (`:136-140`, the vacuously-green trap) with the F11 muted-dings test:
```python
def test_muted_session_dings_on_turn_completion():
    # A muted (stopped) BACKGROUND session still dings when its turn completes
    # (R7:192-193 "its output piles, dinging on turn-completion"). The retired
    # `waiting` gate had `not st.stopped` and wrongly silenced it.
    daemon, queue, speaker, sessions, config = make_daemon(foreground="a")
    daemon._stream("b").stopped = True
    daemon.handle_message(_msg(MsgType.EARCON, "b", kind="turn_done"))
    assert speaker.earcons == ["turn_done"]
```
- **REWRITE** `test_minqueue_waiting_earcon_fires_at_flush_not_on_chunk` (`:188` through its end ~`:207`) to keep the flush pin, drop the waiting:
```python
def test_minqueue_prose_flushes_at_turn_done_no_waiting():
    # minqueue>1: a sub-threshold message is still read at the turn boundary (the
    # turn_done flush). The retired `waiting` earcon no longer fires; a background
    # session's turn_done IS its "landed" ding.
    daemon, queue, speaker, sessions, config = make_daemon(foreground="a")
    config["minqueue"] = 3
    _prose(daemon, "b", "one sentence. ")                # bg, below threshold
    assert "waiting" not in speaker.earcons
    assert len(stream_queue(daemon, "b")) == 0           # not flushed yet
    daemon.handle_message(_msg(MsgType.EARCON, "b", kind="turn_done"))
    assert speaker.earcons == ["turn_done"]              # bg dings at completion (req 16)
    assert len(stream_queue(daemon, "b")) > 0            # ... and the buffered prose flushed
```

**`tests/test_daemon_decisions.py`** — **FLIP** `test_bare_earcon_message_plays_kind` (`:177-181`) to the suppressed case + **ADD** a non-speaker sibling:
```python
def test_bare_earcon_message_suppressed_for_flowing_speaker():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    daemon.handle_message(_msg(MsgType.EARCON, "fg", kind="turn_done"))
    assert speaker.earcons == []                          # flowing speaker's turn_done suppressed
    assert len(queue) == 0

def test_bare_earcon_message_dings_for_non_speaker():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    sessions.register("bg", cwd="/x/bg")
    daemon.handle_message(_msg(MsgType.EARCON, "bg", kind="turn_done"))
    assert speaker.earcons == ["turn_done"]
```

**`tests/test_macos_earcon.py`:**
- **FLIP** the set in `test_default_earcons_are_macos_system_sounds` (`:21`): `assert set(d) == {"permission", "choice", "plan", "error", "turn_done"}` (drop `"waiting"`).
- **DELETE** `test_default_earcons_includes_waiting_pop` (`:24-26`).

**`tests/test_blackbox_net.py`:**
- **FLIP** the foreground all-kinds log (`:151-163`): remove the trailing `("earcon", "turn_done"),` (`:162`) — the fg IS the flowing speaker, so its turn_done is suppressed (the prose before it stays).
- **REWRITE** `test_background_session_is_earcon_only` (`:170-179`):
```python
def test_background_session_dings_at_turn_completion():
    daemon, speaker, log, sessions, config = make_net(foreground="fg")
    prose(daemon, "bg", "Background chatter that stays silent until the turn ends. ",
          index=0, final=True)
    drain(daemon)
    assert log == []                                     # no mid-turn ding (waiting retired)
    daemon.handle_message(msg(MsgType.EARCON, "bg", kind="turn_done"))
    drain(daemon)
    assert log == [("earcon", "turn_done")]              # the "landed" ding at completion (req 16)
```
- **FLIP** `test_turn_done_earcon_flushes_sub_threshold_prose` (`:227`): `assert log == [("text", "Only one.")]` — the fg (flowing speaker) turn_done earcon is suppressed, but the unconditional flush still speaks "Only one." (the load-bearing flush-still-works pin).
- **FLIP** the a/b divergence log (`:260`): `assert log == [("text", "alpha.")]` (drop the leading `("earcon", "waiting")`).
- **FLIP** `test_jump_waiting_blocked_session_outranks_prose_only` (`:317-323`): remove the leading `("earcon", "waiting"),` (`:318`) from the log list.

**`tests/test_e2e_pipeline.py`:**
- **FLIP** the foreground all-kinds log (`:160-169`): remove the trailing `("earcon", "turn_done"),` (`:168`).
- **FLIP** `test_background_session_is_earcon_only` (`:200`): `assert log == [("earcon", "choice")]` (drop the `("earcon", "waiting")`; the sessionless choice earcon still fires). Update the stale `:197-199` comment.

**`tests/test_sp2_divergence.py`** — **REWRITE** `test_ding_gate_uses_speaker_not_foreground` (`:37-44`) to turn_done semantics (the SP2-T1 speaker()-gate invariant carries over to the new ding kind):
```python
def test_ding_gate_uses_speaker_not_foreground():
    # SP3: the "landed" ding is turn_done at completion, suppressed for the flowing
    # SPEAKER (not the foreground). Under divergence (speaker=B, workspace=A) B's
    # turn_done is suppressed; the non-speaker A's dings.
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    sessions.set_speaker("B")                            # voice=B, workspace=A
    daemon.handle_message(_msg(MsgType.EARCON, "B", kind="turn_done"))
    assert speaker.earcons == []                         # never ding the session talking (speaker)
    daemon.handle_message(_msg(MsgType.EARCON, "A", kind="turn_done"))
    assert speaker.earcons == ["turn_done"]              # A is NOT the speaker -> landed ding
```

**SURVIVE (do NOT touch):** `test_hooks_entry.py:231` / `test_sonari_hook_bin.py` (hook-side turn_done emission — suppression is daemon-side); `test_daemon_minqueue.py:37/49/63` (flush unaffected); `test_e2e_pipeline.py:203` #65 canary (no waiting assertion).

- [ ] **Step 7: Run green + full suite**

Run: `.venv/bin/python -m pytest tests/test_sp3_sound.py tests/test_daemon_streams.py tests/test_daemon_decisions.py tests/test_macos_earcon.py tests/test_blackbox_net.py tests/test_e2e_pipeline.py tests/test_sp2_divergence.py tests/test_concurrency_guards.py -q`
Expected: PASS. Then full suite `.venv/bin/python -m pytest -q` → green (baseline + SP3 new − deleted waiting tests).

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat(sp3): sound rewire — retire waiting, turn_done M3 gate, muted-dings (F11); T3"
```

---

## Task T4 — Cycle defects + Fork-4 ⌃⌘S target (FORK-GATED, LAST)

> ## ⛔ FORK-GATED — DO NOT BUILD until Nima ratifies Forks 1–4.
> All four forks are PENDING (Nima is AFK). The code below is written **to the leans** (Fork 1 = `workspace()`, Fork 2 = KEEP + ratified muted-landing, Fork 3 = DEFER, Fork 4 = ASYMMETRIC). Each fork's exact per-choice **delta** (file:line, what changes) is listed so a ratified answer is a small edit, not a rewrite. **This task RACES keep-going and rewrites `on_cycle_session` + `on_stop_session` — OPUS review.** It also SUPERSEDES the `on_stop_session` edits from T1/T2 with the full asymmetric version (the T1 quiet-hold-entry write and the T2 ⌃⌘S-start lift are folded in below — nothing is lost).

**Files:** `src/sonari/daemon/features/focus.py` (`:113-147` `on_cycle_session`), `src/sonari/daemon/features/playback.py` (`:31-64` `on_stop_session`), (Fork 3=B only) `src/sonari/daemon/features/lifecycle.py`. Test: `tests/test_sp3_cycle.py` (new) + one FLIP. *Depends on: T0, T1, T2, T3 + all four fork answers.*

- [ ] **Step 1: Write the failing tests** (see Step 4 below — the test file lands with the code)

- [ ] **Step 2: Rewrite `on_cycle_session` (Fork 1 anchor + Fork 2 roster + muted-landing)**

`src/sonari/daemon/features/focus.py` `on_cycle_session` (`:113-147`) — full replacement (coded to Fork1=workspace, Fork2=KEEP):
```python
@handler(MsgType.CYCLE_SESSION)
def on_cycle_session(ctx, msg):
    # ⌃⌘Tab / ⌃⌘⇧Tab: cycle the VOICE through the roster in insertion order, wrapping.
    # Raises the target window (a deliberate cycle is a workspace action).
    sessions = ctx.host.sessions
    # Fork 2 = KEEP: the roster INCLUDES muted sessions. Filter at the CALL SITE (never
    # in session_ids()) so the insertion-order pins in test_sessions.py survive.
    roster = sessions.session_ids()
    if len(roster) < 2:
        ctx.host.speaker.earcon("error")          # <2 sessions: confirm fired, no silent no-op
        return None
    # Fork 1 = workspace(): anchor on the front window (moves only on deliberate acts),
    # so consecutive ⌃⌘Tabs step deterministically — fixes the live-test anchor-drift
    # (keep-going advances speaker() between presses; sessions.py:79-83).
    fg = sessions.workspace()
    cur = roster.index(fg) if fg in roster else 0
    step = 1 if msg.get("direction", "next") == "next" else -1
    target = roster[(cur + step) % len(roster)]
    ctx.host.speaker.pitch("up" if step == 1 else "down")   # directional chirp first
    sessions.focus(target)                        # workspace + voice -> target; raises
    ctx.host.speaker.cancel()
    ctx.host.voice_state = "flowing"              # cycle is a deliberate re-engage (req 9)
    if ctx.host._stream(target).stopped:
        # Cycle-onto-muted (RATIFIED, RECON:184 #3): keep the WORKSPACE on the muted
        # target (focus + raise), but RELEASE the voice so the keep-going scan moves it
        # to another ACTIVE session (speaker != workspace). set_speaker(None): the next
        # speak-loop pass (now flowing) picks the longest-waiting active session; if
        # none, _speaker stays None (flowing-but-silent) and ⌃⌘W reports the state.
        # Do NOT un-mute the target (R7:191 — it stays muted until its own ⌃⌘S-start).
        sessions.set_speaker(None)
    folder = sessions.folder(target)
    identity = sessions.identity(target)
    will_raise = ctx.host._raise().will_attempt(identity)
    gen = ctx.host._raise().bump_generation()
    cue = folder + "." if folder else "Another session."
    ctx.host._enqueue(target, "prose", cue, False,
                      audio_path=ctx.host._spearcon_path(folder),
                      mute_exempt=True, at_front=True, names_session=True)
    if will_raise:
        ctx.host._raise().raise_async(
            identity, gen,
            on_failure=lambda s=target, f=folder: ctx.host._raise_failed(s, f))
    return None
```

**Fork deltas for `on_cycle_session`:**
- **Fork 1 = B (`speaker()`, status quo):** change the anchor line to `fg = sessions.speaker()`. (Keeps the anchor-drift the live-test reported; flips nothing else.)
- **Fork 2 = SKIP:** change the roster to `roster = [s for s in sessions.session_ids() if not (ctx.host._streams.get(s) is not None and ctx.host._streams.get(s).stopped)]`, and DELETE the `if ctx.host._stream(target).stopped:` muted-landing block (it becomes dead code — SKIP never lands on a mute). **LOAD-BEARING consequence to surface:** with jump excluding stopped (`focus.py:20`) and jump-widening owned by no SP, **no hotkey can reach a muted session** (R7:194-196 and SP5 catch-up lose their access path). Choosing SKIP forces adding jump-widening to SP3 (scope creep) or accepting keyboard-unreachable mutes.

- [ ] **Step 3: Rewrite `on_stop_session` (Fork 4 asymmetric target) — folds in T1/T2's enum writes**

`src/sonari/daemon/features/playback.py` `on_stop_session` (`:31-64`) — full replacement (coded to Fork4=asymmetric):
```python
@handler(MsgType.STOP_SESSION)
def on_stop_session(ctx, msg):
    # ⌃⌘S per-session stop/start. Fork 4 = ASYMMETRIC target: if the session you
    # NAVIGATED TO (workspace) is stopped, START it (R7 "start the session you navigated
    # to"); otherwise STOP the speaker (R7 "Stop-the-speaker"). Without this, cycle-onto-
    # muted leaves workspace=muted, speaker=active, and a speaker-target ⌃⌘S would STOP
    # the active speaker instead of starting the mute -> the mute is keyboard-unstartable.
    sessions = ctx.host.sessions
    ws = sessions.workspace()
    ws_st = ctx.host._streams.get(ws) if ws is not None else None
    if ws_st is not None and ws_st.stopped:
        fg = ws                                   # START the navigated-to (muted) workspace
    else:
        fg = sessions.speaker()                   # STOP the speaker (status quo)
    if fg is None:
        ctx.host.speaker.earcon("error")
        return None
    st = ctx.host._stream(fg)
    if st.stopped:
        # Resuming (⌃⌘S-start re-engage, SPEC:286): un-stop, lift to flowing, and MOVE
        # THE VOICE to the started session so it is actually heard (set_speaker is a
        # no-op when fg is already the speaker — the non-divergent case). "Resumed."
        # leads (at_front, ahead of the interrupted item the speak loop re-queued).
        st.stopped = False
        ctx.host.voice_state = "flowing"
        sessions.set_speaker(fg)
        ctx.host._enqueue(fg, "prose", "Resumed.", False, mute_exempt=True, at_front=True)
    else:
        # Stopping -> quiet-hold (SPEC §6). Cancel only if THIS session is in flight.
        st.stopped = True
        ctx.host.voice_state = "quiet-hold"
        cur = ctx.host._current_item
        if cur is not None and cur.session == fg:
            ctx.host.speaker.cancel()
        # "Stopped." is pause_exempt (held branch voices it) + mute_exempt (control cue).
        ctx.host._enqueue(fg, "prose", "Stopped.", False, mute_exempt=True, pause_exempt=True)
    return None
```
**Non-divergent SURVIVE proof:** in every existing stop test `workspace()==speaker()`, so the asymmetric selection resolves to `speaker()` (if ws stopped, ws==speaker; else speaker) and `set_speaker(fg)` is a no-op — behavior is byte-identical, so `test_daemon_stop.py` / `test_sp2_t6_control_grammar.py` stop tests SURVIVE.

**Fork deltas for `on_stop_session`:**
- **Fork 4 = (b) T6-pure always-speaker:** replace the ws-asymmetric selection with `fg = sessions.speaker()` and drop `sessions.set_speaker(fg)` from the resume branch. LEAVES the un-mute-a-navigated-session hole OPEN (a cycled-to mute cannot be started by ⌃⌘S).
- **Fork 4 = (c) always-workspace:** replace the selection with `fg = sessions.workspace()` (both stop and start target the workspace). REVERTS T6's "stop what's talking" — ⌃⌘S now stops the workspace, not the speaker (changes the stop UX).

- [ ] **Step 3b: Fork 3 = DEFER (lean) → NO submit-lift code.** If Nima rules **Fork 3 = B (IMPLEMENT)**, add the delta (a distinct, discriminator-bearing change — do NOT fold silently): in `lifecycle.py on_set_foreground`, lift `voice_state="flowing"` on a submit **only** for a deliberate/typed re-engage; this REQUIRES `_voice_busy_elsewhere` to gain `and not st.stopped` on the speaker clause (`host.py:197`) so a muted speaker stops reading busy, PLUS a new #65 canary asserting a background/autonomous submit CANNOT seize the voice under hold. This pulls the Pass-2 typed-vs-autonomous discriminator forward; it is its own task, not part of SP3's lean.

- [ ] **Step 4: The fork-gated tests** (`tests/test_sp3_cycle.py`, new — coded to the leans):

```python
from sonari.protocol import MsgType
from tests.daemon_helpers import make_daemon


def _msg(t, session, **kw):
    from sonari.protocol import PROTOCOL_VERSION
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


# --- Fork 1: the anchor is workspace(), not the (keep-going-advanced) speaker() ---
def test_cycle_anchor_is_workspace_not_speaker():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    sessions.register("C", cwd="/x/C")             # roster [A, B, C]
    sessions.set_speaker("C")                      # keep-going advanced the voice to C; workspace=A
    daemon.handle_message(_msg(MsgType.CYCLE_SESSION, "", direction="next"))
    assert sessions.speaker() == "B"               # stepped from workspace A(0) -> B; NOT speaker C -> A


# --- Fork 2: cycle onto a MUTED session keeps the workspace there + keep-goes the voice ---
def test_cycle_onto_muted_keeps_going_to_active():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="B")
    sessions.register("A", cwd="/x/A")             # roster [B, A, C]
    sessions.register("C", cwd="/x/C")
    daemon._stream("A").stopped = True             # A muted
    daemon._enqueue("C", "prose", "c active", False)
    daemon.handle_message(_msg(MsgType.CYCLE_SESSION, "", direction="next"))  # B(0) -> A(1), muted
    assert sessions.workspace() == "A"             # workspace landed on the muted target
    assert sessions.speaker() is None              # voice released off the mute (no dead-stop)
    assert daemon.voice_state == "flowing"         # hold lifted
    assert daemon._stream("A").stopped is True     # target stays muted (R7:191)
    daemon._speak_loop_once()                      # keep-going voices an ACTIVE session
    assert sessions.speaker() == "C"
    assert any(s and "c active" in s for s in speaker.spoken)


# --- Fork 2 edge (c)#9: cycle onto muted with NO active session -> speaker None, ⌃⌘W reports ---
def test_cycle_onto_muted_no_active_reports_via_where_am_i():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="B")
    sessions.register("A", cwd="/x/A")             # roster [B, A]; B has nothing, A muted
    daemon._stream("A").stopped = True
    daemon.handle_message(_msg(MsgType.CYCLE_SESSION, "", direction="next"))  # -> A, muted
    assert sessions.workspace() == "A" and sessions.speaker() is None
    # ⌃⌘W: speaker None + a MUTED workspace -> nothing voiceable without moving the
    # voice -> honest error earcon (the playable-workspace path is exercised in T0).
    daemon.handle_message(_msg(MsgType.WHERE_AM_I, ""))
    assert speaker.earcons[-1] == "error"


# --- Fork 4: ⌃⌘S STARTS the navigated-to muted workspace (not stop the active speaker) ---
def test_ctrl_s_starts_navigated_muted_workspace():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="B")
    sessions.register("A", cwd="/x/A")             # roster [B, A, C]
    sessions.register("C", cwd="/x/C")
    daemon._stream("A").stopped = True             # A muted
    daemon._enqueue("A", "prose", "a pile", False)
    daemon._enqueue("C", "prose", "c active", False)
    daemon.handle_message(_msg(MsgType.CYCLE_SESSION, "", direction="next"))  # workspace=A(muted), keep-go
    daemon._speak_loop_once()                       # voice keep-goes to C
    assert sessions.workspace() == "A" and sessions.speaker() == "C"
    daemon.handle_message(_msg(MsgType.STOP_SESSION, ""))   # ⌃⌘S: workspace A is muted -> START A
    assert daemon._stream("A").stopped is False     # A started (un-muted)
    assert sessions.speaker() == "A"                # voice moved to the started session
    assert daemon._stream("C").stopped is False     # C the ACTIVE speaker was NOT stopped


# --- Fork 4 else-branch: workspace active -> ⌃⌘S STOPS the speaker (status quo) ---
def test_ctrl_s_stops_speaker_when_workspace_active():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    daemon.handle_message(_msg(MsgType.STOP_SESSION, ""))   # workspace A active -> stop the speaker A
    assert daemon._stream("A").stopped is True
    assert daemon.voice_state == "quiet-hold"
```

- [ ] **Step 5: Land the Fork-1 FLIP + run**

**`tests/test_sp2_t6_control_grammar.py`** — **FLIP** `test_cycle_session_from_speaker_not_foreground_under_divergence` (`:72-81`) iff Fork1=workspace: rename to `..._from_workspace_not_speaker...` and change the assertion to `assert sessions.speaker() == "B"` (workspace A(0) → B, not speaker B(1) → C). The parity test (`:84-90`) SURVIVES. (If Fork1=B, this test is UNCHANGED.)

Run: `.venv/bin/python -m pytest tests/test_sp3_cycle.py tests/test_sp3_hold_entry.py tests/test_sp3_lifts.py tests/test_sp2_t6_control_grammar.py tests/test_daemon_cycle.py tests/test_daemon_stop.py tests/test_sessions.py tests/test_concurrency_guards.py -q`
(`test_sp3_hold_entry.py` + `test_sp3_lifts.py` are in the task-local run BECAUSE T4 REWRITES `on_stop_session` wholesale — folding in T1's quiet-hold-entry write and T2's ⌃⌘S-start lift under the new Fork-4 targeting — so they must be re-proven IN PLACE, not left to the final full-suite pass.)
Expected: PASS. Then full suite green.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(sp3): cycle anchor + muted-landing keep-go + asymmetric ⌃⌘S start (Forks 1/2/4); T4"
```

---

## Task T5 — Concurrency-guard extension: STOP_ALL in the hammer (fork-independent)

Extend the permanent stress hammer to exercise the new voice-state transitions under real lock contention (⌃⌘M racing keep-going's `set_speaker`), and re-affirm the three permanent guards + the Q1 invariant. **No duplication:** the F1/F2/F11 RED-first tests already land in T1/T3; the muted-landing-no-dead-stop canary is fork-gated (T4). *Review: **OPUS** — guards.*

**Files:** `tests/test_concurrency_guards.py` (`:167-168` hammer ops). *Depends on: T1 (STOP_ALL now transitions the enum + born-mutes). Fork-independent.*

- [ ] **Step 1: Add STOP_ALL to the hammer ops**

`tests/test_concurrency_guards.py` `hammer()` ops list (`:167-168`):
```python
        ops = [MsgType.STOP_SESSION, MsgType.FLUSH, MsgType.SET_FOREGROUND,
               MsgType.JUMP_WAITING, MsgType.CYCLE_SESSION, MsgType.STOP_ALL]
```
Update the `hammer()` docstring to note: `STOP_ALL` sets `voice_state=stopped-all` (suppressing keep-going) and stops every stream — including the passive `s_bg`; the enum is lifted back to `flowing` by the interleaved `STOP_SESSION`-resume / `CYCLE_SESSION` / `JUMP_WAITING` ops, so flowing windows recur and keep-going still fires. `on_stop_all` iterates `_streams.values()` under the one lock, so the born-muted read and the enum write are lock-consistent with the speak loop's gate read (no torn read; F3/F5).

- [ ] **Step 2: Run the guards (repeat the probabilistic one)**

Run: `.venv/bin/python -m pytest tests/test_concurrency_guards.py -q` (repeat ~5×; the stress test is probabilistic).
Expected: PASS every run — no crash, no deadlock, speak thread survives, no orphaned pending-heard markers, and `keep_going_fires[0] > 0`.
**Flake protocol (NEVER weaken):** `STOP_ALL` shrinks the flowing windows and permanently stops `s_bg` after its first fire, so `keep_going_fires` can be small. If it ever hits 0, WIDEN the idle window — raise the feeder `time.sleep` (`:157`) or extend the 1.0 s storm (`:185`) to 2.0 s — never drop or lower the assertion. Verify Q1 (`test_quiet_hold_implies_speaker_stream_stopped`, T1) and the three permanent guards are green.

- [ ] **Step 3: Full suite + commit**

Run: `.venv/bin/python -m pytest -q`
Expected: `887 baseline + SP3 net-new − deleted waiting tests`, 1 skipped, 0 failures; all three concurrency guards green.

```bash
git add tests/test_concurrency_guards.py
git commit -m "test(sp3): extend the concurrency hammer with STOP_ALL (voice-state transitions under contention); T5"
```

---

## Final: full suite + invariant sweep

- [ ] **Run the whole suite:** `.venv/bin/python -m pytest -q` — 0 failures, 1 skip, 3 concurrency guards green.
- [ ] **Invariant sweep (confirm, don't assume):**
  - **M1:** the keep-going gate is one added boolean term inside the existing lock block (`host.py:469-499`); the scan never moved out.
  - **R12:** `git grep -n '_foreground =' src/sonari/sessions.py` shows writers ONLY in `set_foreground`/`focus`/`unregister`; no SP3 lift writes `_foreground`.
  - **Q1:** `voice_state=="quiet-hold"` ⟹ the speaker's stream is stopped (every entry path sets `st.stopped=True`).
  - **F8:** `_voice_busy_elsewhere` is UNCHANGED → the 4 #65 canaries (`test_daemon_control.py:109/121/131/159`, `test_e2e_pipeline.py:203`) are green.
  - **Transience:** `_voice_state` is never serialized — a live hold is lost on restart (→ SP6). Confirm no persistence code references it.

## Self-Review

**1. Spec coverage — every synthesis §2 requirement → its task:**
- Enum exists / transitions / M1 lock (req 1, 27) → T0 (enum+gate) + T1 (entry). ✓
- ⌃⌘S→quiet-hold, muted resumes, ⌃⌘S-start→flowing (req 2, 3, 10) → T1 (entry) + T2/T4 (⌃⌘S-start lift + Fork-4 target). ✓
- Quiet-hold suppresses keep-going for everyone; dings continue (req 4, 5) → T0/T1 (gate) + T3 (ding). ✓
- ⌃⌘W reports voice-state (req 6) → T0 (wording + None-branch) + T1 (state assertions). ✓
- Re-engage lifts: submit (req 7, DEFERRED Fork 3), jump (req 8), cycle (req 9, 21), stopped one stays muted (req 11), nav-never-unmutes (req 12) → T2 + T4. ✓
- Muted piles + dings (req 13) → T3 (F11). ✓
- ⌃⌘M→stopped-all + born-muted (req 14) + re-engage lands silent (req 15) → T1 (entry+born-muted) + T2/T4 (lifts leave `st.stopped`). ✓
- Ding = turn_done at completion (req 16), no per-chunk dings (req 17), suppress for flowing speaker (req 18), retire waiting (req 19), permission double self-heals (req 20) → T3. ✓
- Cycle anchor (req 22), muted-in-roster (req 23) → T4. ✓
- Stop cues audible under hold (req 24), stop never moves workspace (req 25), decision/R9 ordering (req 26) → preserved (T1/T2 don't touch them) + override-3 "No session waiting." audibility (T2). ✓
- Vet-added M1–M9: M1/M2 (T1 BLOCKERs), M3 (T3 gate), M4 (whitelist stated + T2 audibility fix), M5 (F12 accepted), M6/Q1 (T1 invariant), M7 (T0 STATUS+CLI), M8 (F8 untouched), M9 (FLUSH pile-loss accepted). ✓

**2. Placeholder scan:** every code step has complete runnable code (no `...`/`TODO`). The only deferred specifics are the T4 fork DELTAS — deliberately gated on Nima's answers, each given as an exact file:line edit, not a placeholder.

**3. Type/name consistency:** `voice_state` (host property, cold-path) / `self._state._voice_state` (hot-path read) used consistently; the enum values `"flowing"`/`"quiet-hold"`/`"stopped-all"` are string-literal-identical at every site; `set_speaker(None)` (muted-landing) matches the `set_speaker(session)` signature; the new test files (`test_sp3_voicestate.py`/`_hold_entry.py`/`_lifts.py`/`_sound.py`/`_cycle.py`) each define their own module-local `_msg` per the harness pattern.

**4. What this review caught (disagreements with the synthesis, folded in — not silently diverged):**
- **⌃⌘W None-branch delivery bug (FOUND + FIXED).** The synthesis §3d says the None-branch should "report state, not error." Naively enqueuing to `workspace()` never voices when that stream is stopped (the loop plays `speaker()==None`; keep-going skips stopped streams). Made it **playable-aware**: report only to a non-stopped workspace, else honest `error`. Named the resulting idle-voice adoption vs (c)#4 (a steal is forbidden; an idle voice landing where you are is not).
- **Additive ⌃⌘W wording vs synthesis §7 "6 tests FLIP" (DISAGREE — they SURVIVE).** Kept the flowing wording byte-identical to Nima's SP-B-approved grammar; added words only for the new states. So the 6 where_am_i + 2 spearcon exact-string tests SURVIVE, not flip — LESS churn, respects prior taste approval. `test_daemon_where_am_i.py:42` also SURVIVES (both `speaker()` and `workspace()` are None → honest error).
- **Fork 4 confirmed REAL (synthesis §5(c)#3 was WRONG).** Reachability ≠ startability; ⌃⌘S targets `speaker()`, so a cycled-to muted session is keyboard-un-startable. Resolved ASYMMETRIC + `set_speaker(fg)` so the started session is actually heard.
- **Dropped the `_voice_quiet_hold` shim** (synthesis offered it optional; no hot-path consumer — YAGNI).
- **Co-design gap (advisor-caught):** the two NEW spoken strings (`"On hold."`/`"Nothing playing."`) are Nima's taste, not a free default — flagged for his ear in the fork ratification batch (§ after Fork 4).
- **F4 one-extra-ding edge** documented at the T3 gate (accept, not fix); **T4 task-local run** now re-proves `test_sp3_hold_entry.py`/`_lifts.py` in place (T4 rewrites `on_stop_session`).
