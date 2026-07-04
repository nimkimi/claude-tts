# SP3.1 — Live-test fix wave (W2 chirps · W3 ⌃⌘W grammar · W4 identity re-capture · W1 ring hygiene)

> **For agentic workers:** REQUIRED SUB-SKILL: use `superpowers:subagent-driven-development` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax. Authored from the design ORACLE `.superpowers/sdd/sp3fix-recon-synthesis.md` (read it first) with every `file:line` re-verified against `HEAD = 1ff3c4c` on branch `design/voice-arbitration`. This is a **short fix wave (4 tasks), not a sub-project** — same rigor, scaled scope.

**Goal:** Close the four live-test findings behind the SP3 voice-arbitration work, without touching the voice hot path: (W2) remove the directional pitch chirps at the cycle + nav sites (keep the approve/deny answer chirp); (W3) rebuild the ⌃⌘W "where am I" utterance into the both-sides + counts grammar (`Voice: … Keyboard: … N waiting, M muted.`); (W4) re-capture terminal identity on every UserPromptSubmit so a post-restart session self-heals its tty (no `/clear` needed); (W1) filter phantom (closed-terminal) sessions out of the two cold roster paths (⌃⌘Tab cycle + ⌃⌘J waiting-target) using a timestamp-free tty-liveness signal.

**Architecture:** No new subsystems. W2/W3 are edits inside two existing handlers (`focus.py`/`navigation.py` chirp calls; `control.py on_where_am_i`). W4 piggybacks the three identity fields onto UserPromptSubmit's existing `SET_FOREGROUND` message and widens the daemon-side `set_identity` guard from "message type == SESSION_START" to "any identity field present". W1 adds one pure predicate at two layers — `ttyutil.tty_alive(tty)` (a `/dev/ttysNNN` device-node existence check, fail-open on empty/error) and `SessionManager.is_live(session)` — then filters at the two COLD call sites only. The keep-going hot path is deliberately NOT touched.

**Tech Stack:** Python 3.13, `pytest`, the existing daemon (`src/sonari/daemon/*`, `src/sonari/sessions.py`, `src/sonari/ttyutil.py`, `src/sonari/hooks_entry.py`). macOS-only, no new dependencies.

## Global Constraints

- **Baseline:** `916 passed, 1 skipped` (`.venv/bin/python -m pytest -q`, at `1ff3c4c`; the 1 skip = `test_kokoro.py`, numpy). Must end green (baseline ± each task's FLIP/REWRITE/DELETE edits + the net-new tests).
- **The THREE PERMANENT concurrency guards (`tests/test_concurrency_guards.py`) stay green at EVERY commit** — `test_stress_no_lost_duplicated_or_resurrected_item`, `test_reentrant_stop_flush_requeues_item_exactly_once`, `test_keep_going_flush_race_leaves_no_orphan`. Plus the `real_keep_going_fires` assertion (`keep_going_fires[0] > 0`). **EXTEND, NEVER weaken.** If a stress assertion ever flakes, WIDEN the idle window — never lower or delete it. No task in this wave adds a hammer op.
- **M1 (hot-path atomicity):** W1 touches NO hot path. Its filter lives at the two COLD call sites (`on_cycle_session`, `_waiting_target`), both already running under the existing transaction lock (host.py hotkey path). keep-going (`_select_keep_going`, the M1 speak-loop lock) is **NOT** touched — no `os.path.exists` syscall ever runs under the voice lock. The one phantom keep-going can still see (a backlog phantom) is a documented, deferred limitation (synthesis §6), NOT this wave.
- **R12:** `_foreground` is written ONLY by `set_foreground`/`focus`/`unregister`. Every predicate in W1 is a pure read (no `_foreground`, `_speaker`, or `unregister` write). W3 reads only. W4 writes only `_identities` (via `set_identity`), never `_foreground`.
- **Q1 / hot-cold discipline:** unchanged. `voice_state` cold reads only in the ⌃⌘W handler (W3) as `host.voice_state`; no new hot-path read.
- **Fail direction (W1):** every failure mode of the liveness predicate resolves to "keep the session in the ring" (never hide a live session). Empty/unknown tty → live; `os.path.exists` raises → live; tty recycle → phantom lingers (safe), never a live session hidden.
- **macOS-only; no new dependencies.** TDD: red → green → commit, bite-sized (one action per step). DRY, YAGNI.
- **Scope fence (deferred items stay deferred — synthesis §6, reference don't build):** NO garbage-collection / `unregister` of phantoms; NO last-activity timestamps (the tty device node IS the liveness signal — there is no clock in this design); NO keep-going changes; NO change to the stuck-`_foreground`/`_speaker` pointer vector (a roster filter hides phantoms from rosters, it does not repoint a stuck pointer). SESSION_START identity behavior is UNCHANGED — W4 is purely additive on the UserPromptSubmit SET_FOREGROUND path.

### Ratified decisions (bake in — do NOT re-open)

Ratified by Nima 2026-07-04: **chirps REMOVED** at the cycle + nav sites; **⌃⌘W = both-sides + counts** grammar; **voice config is user-managed** (no code change in this wave). Three vetoable defaults taken on recommendation (Nima AFK) — coded as below, each flippable in one line:

- **F1 — KEEP the approve/deny answer chirp** (`decisions.py:192` UNTOUCHED). *Flip:* delete that `pitch(...)` line + flip `test_answer_allow_chirps_up_deny_chirps_down` to `[]`.
- **F2 — ⌃⌘W speaks PLAIN SPEECH end-to-end** (drop the spearcon split; one composed sentence, no folder-audio clip). *Flip:* re-introduce the split as a multi-enqueue with `audio_path=spearcon` for the voice folder (fragments the utterance).
- **F3 — the muted count EXCLUDES the session the keyboard is parked on** i.e. it excludes the voice/`fg` session (mirrors the existing `waiting` scan; the speaker's own state is already carried by `state`). *Flip:* count ALL `stopped` streams incl. `fg` (the speaker then double-reports).

## Test-harness facts (verified against the repo at 1ff3c4c — use these EXACT shapes)

- `from tests.daemon_helpers import make_daemon, stream_queue`. **`make_daemon(verbosity="everything", foreground="fg")` returns a 5-tuple `(daemon, queue, speaker, sessions, config)`** — unpack all five (`_` for unused). It `set_foreground`s the `foreground` arg; pass `foreground=None` for a no-speaker daemon. It passes `spearcons=FakeSpearconCache()`.
- The fake speaker is `FakeSpeaker` (`tests/daemon_helpers.py`): `speaker.spoken` (list; entries may be `None`), `speaker.earcons` (list), `speaker.pitches` (list — `pitch(direction)` calls), `speaker.audio_paths` (list; a plain spoken cue appends `None` here), `speaker.cancels` (int). `speak()` returns immediately, so observability tests are synchronous: run `_speak_loop_once()`, then assert on post-state.
- Module-local `_msg` per test file:
  ```python
  def _msg(t, session, **kw):
      from sonari.protocol import PROTOCOL_VERSION
      return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}
  ```
  Drive via `daemon.handle_message(_msg(MsgType.X, session, **kw))`. Import `from sonari.protocol import MsgType`.
- `daemon._stream(s)` is the per-session stream; `.queue` its `SpeechQueue` (`._items` a deque); `.stopped` its stop flag. `daemon._enqueue(session, kind, text, is_decision, ...)` enqueues directly. `daemon.voice_state` is the cold-path enum property.
- Identity: `from sonari.sessions import Identity`; `sessions.set_identity(s, Identity(term_program=..., tty=..., iterm_session_id=...))`; `sessions.identity(s)` reads it back (or `None`). `sessions._identities` is the raw dict (tests may `.pop()` to simulate a restart wipe).
- W1 liveness is faked by monkeypatching the module function: `import sonari.ttyutil as ttyutil; monkeypatch.setattr(ttyutil, "tty_alive", fake)` — `SessionManager.is_live` calls `ttyutil.tty_alive(...)` module-qualified, so the patch is visible (established pattern: `test_hooks_entry` monkeypatches `hooks_entry.ttyutil.controlling_tty`).

## File Structure

| File | Change | Task |
|---|---|---|
| `src/sonari/daemon/features/focus.py` | Modify (`:132` cycle chirp; `:9-24` `_waiting_target`; `:121` cycle roster) | T1 (W2 chirp), T4 (W1 filter). |
| `src/sonari/daemon/features/navigation.py` | Modify (`:112-115` `_chirp` block) | T1 (W2 chirp). |
| `src/sonari/daemon/features/control.py` | Modify (`on_where_am_i` has-speaker branch, `:183-222`) | T2 (W3 grammar). |
| `src/sonari/hooks_entry.py` | Modify (`:93-98` UserPromptSubmit branch) | T3 (W4 hook side). |
| `src/sonari/daemon/features/lifecycle.py` | Modify (`on_set_foreground`, `:83-90` identity guard) | T3 (W4 daemon side). |
| `src/sonari/ttyutil.py` | Modify (add `tty_alive`) | T4 (W1). |
| `src/sonari/sessions.py` | Modify (add `is_live` + `ttyutil` import) | T4 (W1). |
| `tests/test_pitch_dispatch.py` | FLIP 4 assertions to `[]` | T1. |
| `tests/test_daemon_where_am_i.py`, `test_sp3_voicestate.py`, `test_sp3_hold_entry.py`, `test_daemon_spearcon.py`, `test_sp2_t6_control_grammar.py` | Update ⌃⌘W string pins + invert the divergence pin | T2. |
| `tests/test_sp3fix_grammar.py` (new) | Add W3 new-semantics tests | T2. |
| `tests/test_hooks_entry.py` | REWRITE the UPS exact-list test (`:238-242`) | T3. |
| `tests/test_sp3fix_identity.py` (new) | Add W4 daemon-side re-capture tests | T3. |
| `tests/test_sp3fix_ring.py` (new) | Add W1 liveness-filter tests | T4. |

**Task order:** T1 (W2) → T2 (W3) → T3 (W4) → T4 (W1, OPUS review). No hard code dependency between tasks; W1 and W2 both edit `focus.py` but at different lines, sequential. W1's cycle-roster edit lands on the same `on_cycle_session` W2 edited (chirp already gone by then) — no conflict.

---

## Task T1 — W2: remove the directional pitch chirps at cycle + nav (KEEP the answer chirp)

Remove the two `speaker.pitch(...)` calls at the cycle site (`focus.py:132`) and the nav site (`navigation.py:112-115`). The approve/deny answer chirp (`decisions.py:192`) is KEPT (F1 default). The `Speaker.pitch` primitive and its `pitch_up.wav`/`pitch_down.wav` assets stay (the answer site still uses them). Smallest, isolated, no concurrency. *Review: sonnet.*

**Files:** `src/sonari/daemon/features/focus.py` (`:132`), `src/sonari/daemon/features/navigation.py` (`:112-115`). Test: `tests/test_pitch_dispatch.py` (FLIP 4). *Depends on: nothing.*

- [ ] **Step 1: Flip the four pitch-dispatch tests red-first**

`tests/test_pitch_dispatch.py` — rewrite the four cycle/nav assertions to expect `[]` and rename them to match the new behavior (a name asserting `chirps_up` while expecting `[]` is contradictory). The two answer tests (`:61-69`, `:72-76`) and the two already-`[]` tests (`:23-26`, `:43-48`) STAY untouched.

```python
def test_cycle_next_does_not_chirp():
    daemon, q, speaker, sessions, _ = make_daemon(foreground="A")
    _two(daemon, sessions); sessions.set_foreground("A")
    daemon.handle_message({"type": "cycle_session", "direction": "next"})
    assert speaker.pitches == []


def test_cycle_prev_does_not_chirp():
    daemon, q, speaker, sessions, _ = make_daemon(foreground="A")
    _two(daemon, sessions); sessions.set_foreground("A")
    daemon.handle_message({"type": "cycle_session", "direction": "prev"})
    assert speaker.pitches == []


def test_nav_next_prev_do_not_chirp():
    daemon, q, speaker, sessions, _ = make_daemon(foreground="fg")
    _seed(daemon)
    daemon.handle_message({"type": "nav", "to": "next", "session": "fg"})
    daemon.handle_message({"type": "nav", "to": "prev", "session": "fg"})
    assert speaker.pitches == []


def test_nav_response_does_not_chirp():
    daemon, q, speaker, sessions, _ = make_daemon(foreground="fg")
    h = daemon.history
    h.record("fg", "prose", "t0"); h.end_message("fg"); h.start_turn("fg")
    h.record("fg", "prose", "t1")
    daemon.handle_message({"type": "nav", "to": "prev_response", "session": "fg"})
    daemon.handle_message({"type": "nav", "to": "next_response", "session": "fg"})
    assert speaker.pitches == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_pitch_dispatch.py -q`
Expected: the four renamed tests FAIL — cycle still yields `["up"]`/`["down"]`, nav still yields `["up","down"]`/`["down","up"]` (chirps not yet removed).

- [ ] **Step 3: Remove the cycle chirp**

`src/sonari/daemon/features/focus.py` `on_cycle_session` — DELETE line 132 (`ctx.host.speaker.pitch("up" if step == 1 else "down")` and its `# directional chirp first` comment). `step` stays used at line 131 (`target = roster[(cur + step) % len(roster)]`), so the deletion is clean.

- [ ] **Step 4: Remove the nav chirp**

`src/sonari/daemon/features/navigation.py` `on_nav` — DELETE the `_chirp` block (lines 112-115):
```python
    _chirp = {"next": "up", "prev": "down",
              "next_response": "up", "prev_response": "down"}.get(to)
    if _chirp:
        ctx.host.speaker.pitch(_chirp)             # directional chirp first; first/last get none
```
KEEP line 111 (`to = msg.get("to", "prev")`) — it is used at line 116 (`if to in ("prev_response", "next_response")`). After the deletion, `to` is assigned then used directly by the branch below.

- [ ] **Step 5: Run green + the affected suites**

Run: `.venv/bin/python -m pytest tests/test_pitch_dispatch.py tests/test_daemon_cycle.py tests/test_daemon_focus_nav.py tests/test_speaker_pitch.py tests/test_pitch_assets.py tests/test_concurrency_guards.py -q`
Expected: PASS. The answer chirp + the `Speaker.pitch` primitive/assets are untouched, so `test_speaker_pitch.py` / `test_pitch_assets.py` / the answer tests stay green. Then full suite `.venv/bin/python -m pytest -q` → green (baseline, 4 tests renamed).

- [ ] **Step 6: Commit**

```bash
git add src/sonari/daemon/features/focus.py src/sonari/daemon/features/navigation.py tests/test_pitch_dispatch.py
git commit -m "fix(sp3.1): remove directional pitch chirps at cycle + nav; keep answer chirp (W2)"
```

---

## Task T2 — W3: ⌃⌘W both-sides + counts grammar

Rebuild the ⌃⌘W has-speaker utterance into one composed sentence: `Voice: {voice_folder}, {state}.[ Keyboard: {kbd_folder}.] {N} waiting, {M} muted.` — the Keyboard clause appears ONLY when the workspace (keyboard) resolves to a DIFFERENT session than the voice. Fold `waiting` + `muted` into one background-only pass (fg excluded, F3). Drop the spearcon split (F2 — one plain spoken sentence). The None-speaker branch (control.py:157-182) is UNCHANGED. Reads only, under the already-held handler lock. *Review: sonnet.*

**Files:** `src/sonari/daemon/features/control.py` (`on_where_am_i` has-speaker branch, `:183-222`). Tests: string-pin updates across 5 files + `tests/test_sp3fix_grammar.py` (new). *Depends on: nothing.*

- [ ] **Step 1: Write the new-semantics tests (red-first)**

```python
# tests/test_sp3fix_grammar.py (new)
from sonari.protocol import MsgType
from tests.daemon_helpers import make_daemon


def _msg(t, session, **kw):
    from sonari.protocol import PROTOCOL_VERSION
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


# --- divergence: BOTH folders named + both counts, in one composed sentence ---
def test_where_am_i_names_both_folders_and_counts_under_divergence():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.set_foreground("A", cwd="/x/web")          # keyboard/workspace folder = web
    sessions.register("B", cwd="/x/api")
    sessions.set_speaker("B")                            # voice=B (api); keyboard=A (web) -> diverged
    daemon._enqueue("C", "prose", "c backlog", False)   # a waiting background
    daemon._stream("D").stopped = True                  # a muted background
    daemon.handle_message(_msg(MsgType.WHERE_AM_I, ""))
    daemon._speak_loop_once()
    assert speaker.spoken == ["Voice: api, Playing. Keyboard: web. 1 waiting, 1 muted."]


# --- no divergence -> NO Keyboard clause ---
def test_where_am_i_omits_keyboard_clause_when_not_diverged():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    sessions.set_foreground("fg", cwd="/x/work")        # voice == keyboard == fg
    daemon.handle_message(_msg(MsgType.WHERE_AM_I, "fg"))
    daemon._speak_loop_once()
    assert speaker.spoken == ["Voice: work, Playing. 0 waiting, 0 muted."]
    assert not any(s and "Keyboard:" in s for s in speaker.spoken)


# --- muted counts BACKGROUND stopped streams (fg excluded, F3); independent of voice_state ---
def test_where_am_i_muted_count_is_background_stopped_streams():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    sessions.set_foreground("fg", cwd="/x/work")
    daemon._stream("b1").stopped = True
    daemon._stream("b2").stopped = True                 # two individually-muted backgrounds
    daemon.handle_message(_msg(MsgType.WHERE_AM_I, "fg"))
    daemon._speak_loop_once()
    assert speaker.spoken == ["Voice: work, Playing. 0 waiting, 2 muted."]
```

Run: `.venv/bin/python -m pytest tests/test_sp3fix_grammar.py -q`
Expected: FAIL — HEAD emits the old `"{folder}. {state}. {N} waiting."` grammar (no `Voice:` prefix, no `Keyboard:` clause, no muted count).

- [ ] **Step 2: Rewrite the has-speaker branch of `on_where_am_i`**

`src/sonari/daemon/features/control.py` — replace the has-speaker branch body (everything from `# Capture the in-flight item BEFORE cancel` at line 183 through the `else:` combined-cue enqueue ending at line 221, i.e. lines 183-221) with:

```python
    # Capture the in-flight item BEFORE cancel so we can resume it afterwards.
    cur = host._current_item
    entry = host._pending_heard.get(cur.id) if cur is not None else None
    voice_folder = host.sessions.folder(fg) or "Unknown session"
    st = host._streams.get(fg)
    vs = host.voice_state
    if vs == "stopped-all":
        state = "All stopped"
    elif vs == "quiet-hold":
        state = "On hold"
    else:
        state = "Stopped" if (st is not None and st.stopped) else "Playing"
    # One pass over the BACKGROUND streams (fg excluded, mirroring _waiting_target):
    # waiting = live non-stopped backlog; muted = individually-stopped sessions. The
    # muted count is independent of voice_state (per-stream st.stopped, not the enum).
    waiting = muted = 0
    for sess, s in host._streams.items():
        if sess == fg:
            continue
        if s.stopped:
            muted += 1
        elif len(s.queue) > 0:
            waiting += 1
    # Keyboard clause ONLY when the workspace (keyboard) resolves to a session other
    # than the voice — otherwise there is nothing to disambiguate.
    ws = host.sessions.workspace()
    diverged = ws is not None and ws != fg
    kbd = (" Keyboard: {0}.".format(host.sessions.folder(ws) or "Unknown session")
           if diverged else "")
    text = "Voice: {0}, {1}.{2} {3} waiting, {4} muted.".format(
        voice_folder, state, kbd, waiting, muted)
    host.speaker.cancel()                          # barge-in: cut the current utterance
    # Resume-after-interjection: re-queue the interrupted item FIRST so it ends up
    # DEEPEST (the status cue is appendleft'd in front of it below).
    if cur is not None:
        host._enqueue(cur.session, cur.kind, cur.text, cur.is_decision,
                      entry=entry, mute_exempt=cur.mute_exempt,
                      pause_exempt=cur.pause_exempt, names_session=cur.names_session,
                      audio_path=cur.audio_path, at_front=True)
    host._enqueue(fg, "prose", text, False,
                  mute_exempt=True, pause_exempt=True, at_front=True)
    return None
```

This drops the `spearcon = host._spearcon_path(folder)` lookup and the two-enqueue split (F2). The barge-in `cancel()` and the resume-requeue of the in-flight item are PRESERVED. The None-speaker branch above (lines 157-182) is untouched.

Run: `.venv/bin/python -m pytest tests/test_sp3fix_grammar.py -q` → PASS. (Existing pins are still RED here — Step 3 updates them.)

---
- [ ] **Step 3: Update every pinned ⌃⌘W string (old → new, verbatim)**

Each is an exact-string assertion. `M` (muted) is read from each fixture's actual background stream set (`stopped=True`, fg excluded). Where a background stream is stopped-but-queued, it counts as muted (NOT waiting).

**`tests/test_daemon_where_am_i.py`:**
- `:10` `assert speaker.spoken == ["work. Playing. 0 waiting."]` → `["Voice: work, Playing. 0 waiting, 0 muted."]`
- `:18` `["Unknown session. Playing. 0 waiting."]` → `["Voice: Unknown session, Playing. 0 waiting, 0 muted."]`
- `:27` `["work. Stopped. 0 waiting."]` → `["Voice: work, Stopped. 0 waiting, 0 muted."]` (fg stream stopped, voice_state flowing; no background stopped stream → M=0)
- `:39` `["work. Playing. 2 waiting."]` → `["Voice: work, Playing. 2 waiting, 1 muted."]` — **M=1, NOT 0.** The fixture creates `bg3` as `stopped=True` AND `_enqueue`s to it (a stopped background with a queued item), so `bg3` counts as **muted** while `bg1`/`bg2` are the 2 waiting. (This corrects the synthesis §4 entry, which read M=0 by only counting the 2 waiting.)
- `:55` (`test_where_am_i_with_nothing_in_flight_still_barges_in`) `["work. Playing. 0 waiting."]` → `["Voice: work, Playing. 0 waiting, 0 muted."]`
- `:77` (`test_where_am_i_resumes_interrupted_item_after_the_status_cue`, the `speaker.spoken[-1] ==` assertion) `"work. Playing. 0 waiting."` → `"Voice: work, Playing. 0 waiting, 0 muted."` (lines 74/79 assert the item text `"interrupted sentence"` — UNCHANGED)

**`tests/test_sp3_voicestate.py`:**
- `:44` (`test_where_am_i_flowing_wording_unchanged`) `["work. Playing. 0 waiting."]` → `["Voice: work, Playing. 0 waiting, 0 muted."]`. Rename the test (its premise — "unchanged" — no longer holds) to `test_where_am_i_flowing_wording`.
- `:56` (`Nothing playing.`, None-branch) — **STAYS** (None-branch untouched).

**`tests/test_sp3_hold_entry.py`:**
- `:93` (`test_where_am_i_reports_on_hold_under_quiet_hold`, the `speaker.spoken[-1] ==`) `"work. On hold. 0 waiting."` → `"Voice: work, On hold. 0 waiting, 0 muted."` (only fg session; fg excluded from muted → M=0)
- `:102` (`test_where_am_i_reports_all_stopped_under_stopped_all`) `"work. All stopped. 0 waiting."` → `"Voice: work, All stopped. 0 waiting, 0 muted."` (only fg session; M=0)

**`tests/test_daemon_spearcon.py`** (the spearcon split is dropped for ⌃⌘W — F2):
- `:79-87` REWRITE `test_where_am_i_splits_spearcon_then_state_on_hit` — the split no longer exists; ⌃⌘W emits ONE plain spoken cue even on a spearcon HIT:
```python
def test_where_am_i_no_spearcon_split_single_cue_on_hit():
    daemon, q, speaker, sessions, _ = make_daemon(foreground="fg")
    sessions.set_foreground("fg", cwd="/x/work")
    p = _hit(daemon, "work")                      # spearcon available, but ⌃⌘W no longer splits
    daemon.handle_message({"type": "where_am_i", "session": "fg"})
    daemon._speak_loop_once()
    assert speaker.spoken == ["Voice: work, Playing. 0 waiting, 0 muted."]
    assert p not in speaker.audio_paths           # the folder spearcon is NOT played for ⌃⌘W
```
- `:90-95` `test_where_am_i_miss_keeps_combined_cue` — string only: `["work. Playing. 0 waiting."]` → `["Voice: work, Playing. 0 waiting, 0 muted."]` (rename to `test_where_am_i_single_cue_on_miss`).

**`tests/test_sp2_t6_control_grammar.py`** — INVERT the divergence pin (`:96-107`). HEAD asserts `"bravo" in` present and `"alpha" not in` (speaker folder only). W3's purpose is to ADD the keyboard folder, so under divergence `alpha` (keyboard) is now intentionally PRESENT. Rename `test_where_am_i_reports_speaker_folder_under_divergence` → `test_where_am_i_reports_both_voice_and_keyboard_folders_under_divergence` and replace lines 106-107:
```python
    assert any(s and "Voice: bravo" in s for s in speaker.spoken)
    assert any(s and "Keyboard: alpha" in s for s in speaker.spoken)
```
(Fixture: speaker=B/`bravo`, foreground=A/`alpha`, no OS focus → workspace=A → diverged. B is non-empty so no keep-going; only A's empty stream is background → 0 waiting, 0 muted.)

**Not string pins (stay green, do NOT touch):** `test_keymap.py` (`where_am_i` binding + message shape); the ⌃⌘W barge-in/resume mechanics in `test_daemon_where_am_i.py` (cancels count, heard-marker) — only the spoken-string lines change.

- [ ] **Step 4: Run green + full suite**

Run: `.venv/bin/python -m pytest tests/test_sp3fix_grammar.py tests/test_daemon_where_am_i.py tests/test_sp3_voicestate.py tests/test_sp3_hold_entry.py tests/test_daemon_spearcon.py tests/test_sp2_t6_control_grammar.py tests/test_concurrency_guards.py -q`
Expected: PASS. Then full suite `.venv/bin/python -m pytest -q` → green (baseline − 0 + 3 new).

- [ ] **Step 5: Commit**

```bash
git add src/sonari/daemon/features/control.py tests/test_sp3fix_grammar.py tests/test_daemon_where_am_i.py tests/test_sp3_voicestate.py tests/test_sp3_hold_entry.py tests/test_daemon_spearcon.py tests/test_sp2_t6_control_grammar.py
git commit -m "fix(sp3.1): ⌃⌘W both-sides + waiting/muted counts grammar; drop spearcon split (W3)"
```

---

## Task T3 — W4: re-capture terminal identity on every UserPromptSubmit

Piggyback the three identity fields (`term_program`, `iterm_session_id`, `tty`) onto the `SET_FOREGROUND` message UserPromptSubmit already emits, and widen the daemon-side `set_identity` guard from "message type == SESSION_START" to "any identity field present". A post-restart session then self-heals its tty on its next prompt — no `/clear`. `set_identity`'s don't-clobber-with-empties rule (sessions.py:113-131) makes every-prompt refresh safe. SESSION_START behavior is UNCHANGED. *Review: sonnet.*

**Files:** `src/sonari/hooks_entry.py` (`:93-98` UserPromptSubmit branch), `src/sonari/daemon/features/lifecycle.py` (`on_set_foreground`, `:83-90`). Tests: `tests/test_hooks_entry.py` (REWRITE the UPS exact-list) + `tests/test_sp3fix_identity.py` (new). *Depends on: nothing.*

- [ ] **Step 1: Write the daemon-side tests (red-first)**

```python
# tests/test_sp3fix_identity.py (new)
from sonari.protocol import MsgType
from sonari.daemon.features import lifecycle
from tests.daemon_helpers import make_daemon


def _msg(t, session, **kw):
    from sonari.protocol import PROTOCOL_VERSION
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


# --- a UPS SET_FOREGROUND re-populates identity after a simulated restart-wipe ---
def test_ups_recaptures_identity_after_wipe(monkeypatch):
    monkeypatch.setattr(lifecycle, "_setup_health", lambda v: ("ok", None))
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    daemon.handle_message(_msg(MsgType.SESSION_START, "s1", cwd="/x/proj",
                               term_program="Apple_Terminal", tty="/dev/ttys009"))
    assert sessions.identity("s1").tty == "/dev/ttys009"
    sessions._identities.pop("s1", None)                 # simulate the daemon-restart gap
    assert sessions.identity("s1") is None
    daemon.handle_message(_msg(MsgType.SET_FOREGROUND, "s1", cwd="/x/proj",
                               term_program="Apple_Terminal", tty="/dev/ttys009"))
    assert sessions.identity("s1") is not None
    assert sessions.identity("s1").tty == "/dev/ttys009"


# --- a partial UPS (tty moved, program empty) updates tty, keeps the good program ---
def test_ups_partial_identity_updates_only_nonempty_fields(monkeypatch):
    monkeypatch.setattr(lifecycle, "_setup_health", lambda v: ("ok", None))
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    daemon.handle_message(_msg(MsgType.SESSION_START, "s1", cwd="/x",
                               term_program="Apple_Terminal", tty="/dev/ttys009"))
    daemon.handle_message(_msg(MsgType.SET_FOREGROUND, "s1", cwd="/x",
                               term_program="", tty="/dev/ttys010"))
    assert sessions.identity("s1").tty == "/dev/ttys010"        # updated
    assert sessions.identity("s1").term_program == "Apple_Terminal"   # empty kept the good value


# --- an all-empty UPS does NOT touch identity (the "field present" guard skips it) ---
def test_ups_all_empty_identity_does_not_touch(monkeypatch):
    monkeypatch.setattr(lifecycle, "_setup_health", lambda v: ("ok", None))
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    daemon.handle_message(_msg(MsgType.SESSION_START, "s1", cwd="/x",
                               term_program="Apple_Terminal", tty="/dev/ttys009"))
    daemon.handle_message(_msg(MsgType.SET_FOREGROUND, "s1", cwd="/x",
                               term_program="", tty="", iterm_session_id=""))
    assert sessions.identity("s1").tty == "/dev/ttys009"        # preserved
```

Run: `.venv/bin/python -m pytest tests/test_sp3fix_identity.py -q`
Expected: `test_ups_recaptures_identity_after_wipe` FAILS — after the wipe, the UPS SET_FOREGROUND does not re-populate identity (HEAD only sets identity under `t == SESSION_START`); `identity("s1")` stays `None`. (The partial/all-empty tests pass vacuously at HEAD but pin the guard once wired.)

- [ ] **Step 2: Hook side — carry identity on the UPS SET_FOREGROUND**

`src/sonari/hooks_entry.py` `UserPromptSubmit` branch (`:93-98`) — add the three derivations SessionStart already uses (`os` and `ttyutil` are already imported):
```python
    if event == "UserPromptSubmit":
        return [
            _msg(type=MsgType.SET_FOREGROUND, session=session,
                 cwd=payload.get("cwd", ""),
                 term_program=os.environ.get("TERM_PROGRAM", ""),
                 iterm_session_id=os.environ.get("ITERM_SESSION_ID", ""),
                 tty=ttyutil.controlling_tty()),
            _msg(type=MsgType.FLUSH, session=session),
        ]
```
The SessionStart branch (`:100-116`) is UNCHANGED — its own SET_FOREGROUND stays identity-less and SESSION_START still carries identity exactly as today.

- [ ] **Step 3: Daemon side — widen the identity guard to "field present"**

`src/sonari/daemon/features/lifecycle.py` `on_set_foreground` — pull `set_identity` OUT of the `if t == MsgType.SESSION_START:` block (`:83-90`) into a message-type-agnostic "field present" guard. `register`, `_maybe_guide_setup`, and spearcon pregen STAY under `t == SESSION_START`. Replace lines 83-96 with:
```python
    # Identity (re)capture — piggybacked on ANY message carrying identity fields:
    # both SESSION_START and UserPromptSubmit's SET_FOREGROUND supply them, so a
    # post-restart session re-populates its tty on its next prompt (W4). Guard on
    # "field present", not message type. set_identity's don't-clobber-with-empties
    # rule keeps an intermittently-empty best-effort tty from destroying a good value,
    # so refreshing every prompt is safe. (register/set_foreground never touch
    # _identities, so this ordering is independent of the SESSION_START register below.)
    if msg.get("term_program") or msg.get("tty") or msg.get("iterm_session_id"):
        from sonari.sessions import Identity
        ctx.host.sessions.set_identity(session, Identity(
            term_program=msg.get("term_program", ""),
            tty=msg.get("tty", ""),
            iterm_session_id=msg.get("iterm_session_id", ""),
        ))
    if t == MsgType.SESSION_START:
        ctx.host.sessions.register(session, cwd=cwd)
        _maybe_guide_setup(ctx, session, msg.get("plugin_version", ""))
        if ctx.host._spearcons is not None:
            # Pre-render spearcons for the known roster in the background (Popen,
            # non-blocking); skips already-cached labels. Never on the hot path.
            ctx.host._spearcons.pregenerate(
                [ctx.host.sessions.folder(s) for s in ctx.host.sessions.session_ids()])
    return None
```
(The `from sonari.sessions import Identity` import moves with `set_identity`; it is used nowhere else in the handler.)

- [ ] **Step 4: Rewrite the breaking hook test**

The test that breaks is the exact-list UPS assertion — `test_user_prompt_submit_sets_foreground_then_flush` at **`tests/test_hooks_entry.py:238-242`** (NOT the SessionStart tests at 250-339: those assert SessionStart's SET_FOREGROUND is identity-less and SESSION_START carries identity, both still TRUE — they SURVIVE untouched). The exact-list `==` now sees the three identity keys on the UPS SET_FOREGROUND. Rewrite it hermetically (mirror the SessionStart tests' env/tty monkeypatching so `controlling_tty()` does not walk real `ps`):
```python
def test_user_prompt_submit_sets_foreground_then_flush(monkeypatch):
    monkeypatch.delenv("TERM_PROGRAM", raising=False)
    monkeypatch.delenv("ITERM_SESSION_ID", raising=False)
    monkeypatch.setattr(hooks_entry.ttyutil, "controlling_tty", lambda: "")
    assert handle_event("UserPromptSubmit", {"session_id": "sess-9"}) == [
        {"v": PROTOCOL_VERSION, "type": MsgType.SET_FOREGROUND, "session": "sess-9",
         "cwd": "", "term_program": "", "iterm_session_id": "", "tty": ""},
        {"v": PROTOCOL_VERSION, "type": MsgType.FLUSH, "session": "sess-9"},
    ]
```
**SURVIVE (do NOT touch):** `test_user_prompt_submit_sets_foreground_with_cwd` (`:288`) and `test_missing_cwd_defaults_to_empty_string` (`:300`) — both filter to `fg[0]["cwd"]` only, so the extra identity keys don't break them (they now invoke the real `controlling_tty()` harmlessly — it never raises).

- [ ] **Step 5: Run green + full suite**

Run: `.venv/bin/python -m pytest tests/test_sp3fix_identity.py tests/test_hooks_entry.py tests/test_daemon_focus_follow.py tests/test_daemon_setup_health.py tests/test_concurrency_guards.py -q`
Expected: PASS. Then full suite `.venv/bin/python -m pytest -q` → green.

- [ ] **Step 6: Commit**

```bash
git add src/sonari/hooks_entry.py src/sonari/daemon/features/lifecycle.py tests/test_hooks_entry.py tests/test_sp3fix_identity.py
git commit -m "fix(sp3.1): re-capture terminal identity on every UserPromptSubmit (W4)"
```

---

## Task T4 — W1: filter phantom sessions from the cycle + waiting-target rosters

Add a timestamp-free liveness predicate — `ttyutil.tty_alive(tty)` (the tty device node exists ⟺ the terminal is open; a pty slave node vanishes on close, measured on macOS devfs) and `SessionManager.is_live(session)` — then filter phantoms at the two COLD roster sites: the ⌃⌘Tab cycle roster (`focus.py:121`) and the ⌃⌘J `_waiting_target` gate (`focus.py:20`). Predicate is `is_live` ALONE (independent of `stopped`), so a muted-but-live session stays reachable (R7); a muted+dead one drops. Every failure mode fails OPEN (keep in ring). keep-going (M1 hot path) is NOT touched. *Review: **OPUS** — the load-bearing risks are R7 muted-reachability correctness and the ring-anchor math when the anchor is itself filtered out (NOT lock safety — M1 is untouched).*

**Files:** `src/sonari/ttyutil.py` (add `tty_alive`), `src/sonari/sessions.py` (add `is_live` + `ttyutil` import), `src/sonari/daemon/features/focus.py` (`:121` cycle roster, `:9-24` `_waiting_target`). Test: `tests/test_sp3fix_ring.py` (new). *Depends on: nothing (uses monkeypatched `tty_alive`).*

- [ ] **Step 1: Write the liveness-filter tests (red-first)**

```python
# tests/test_sp3fix_ring.py (new)
import sonari.ttyutil as ttyutil
from sonari.protocol import MsgType
from sonari.sessions import Identity
from tests.daemon_helpers import make_daemon


def _msg(t, session, **kw):
    from sonari.protocol import PROTOCOL_VERSION
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


def _liveness(monkeypatch, dead):
    """Fake tty_alive: empty tty -> live (fail-open); else live iff its tty not in `dead`."""
    monkeypatch.setattr(ttyutil, "tty_alive",
                        lambda tty: True if not tty else tty not in dead)


def _ident(sessions, sid, tty):
    sessions.set_identity(sid, Identity(term_program="Apple_Terminal", tty=tty))


# --- 1. cycle skips a dead-tty phantom and lands on the next LIVE session ---
def test_cycle_skips_dead_tty_phantom_lands_on_next_live(monkeypatch):
    _liveness(monkeypatch, dead={"/dev/ttysB"})
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    _ident(sessions, "A", "/dev/ttysA")
    sessions.register("B", cwd="/x/B"); _ident(sessions, "B", "/dev/ttysB")   # phantom
    sessions.register("C", cwd="/x/C"); _ident(sessions, "C", "/dev/ttysC")
    daemon.handle_message(_msg(MsgType.CYCLE_SESSION, "", direction="next"))
    assert sessions.speaker() == "C"          # roster [A,C]; A(0) -> C, phantom B skipped


# --- 2. R7: a MUTED (stopped) session with a LIVE tty stays cycle-reachable ---
def test_cycle_keeps_muted_but_live_session_reachable(monkeypatch):
    _liveness(monkeypatch, dead=set())
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    _ident(sessions, "A", "/dev/ttysA")
    sessions.register("B", cwd="/x/B"); _ident(sessions, "B", "/dev/ttysB")
    daemon._stream("B").stopped = True        # muted, but its terminal is open
    daemon.handle_message(_msg(MsgType.CYCLE_SESSION, "", direction="next"))
    assert sessions.workspace() == "B"        # muted-live stays reachable (not filtered)


# --- 3. muted + dead tty -> filtered (muted-live vs muted-dead distinguished) ---
def test_cycle_filters_muted_and_dead_session(monkeypatch):
    _liveness(monkeypatch, dead={"/dev/ttysB"})
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    _ident(sessions, "A", "/dev/ttysA")
    sessions.register("B", cwd="/x/B"); _ident(sessions, "B", "/dev/ttysB")
    daemon._stream("B").stopped = True        # muted AND terminal closed
    sessions.register("C", cwd="/x/C"); _ident(sessions, "C", "/dev/ttysC")
    daemon.handle_message(_msg(MsgType.CYCLE_SESSION, "", direction="next"))
    assert sessions.speaker() == "C"          # muted+dead B filtered; landed on live C


# --- 4. empty-tty session -> NOT filtered (fail-open) ---
def test_cycle_does_not_filter_empty_tty_session(monkeypatch):
    _liveness(monkeypatch, dead=set())
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    _ident(sessions, "A", "/dev/ttysA")
    sessions.register("B", cwd="/x/B"); _ident(sessions, "B", "")   # empty tty
    daemon.handle_message(_msg(MsgType.CYCLE_SESSION, "", direction="next"))
    assert sessions.workspace() == "B"        # empty tty fail-open -> stays reachable


# --- 5. anchor-is-the-phantom: workspace anchor is dead -> cycle still lands on a live
#        session (needs >=2 LIVE besides the phantom, else <2 -> error, see test 6) ---
def test_cycle_when_anchor_is_phantom_lands_on_live(monkeypatch):
    _liveness(monkeypatch, dead={"/dev/ttysA"})   # the anchor A itself is dead
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    _ident(sessions, "A", "/dev/ttysA")
    sessions.register("B", cwd="/x/B"); _ident(sessions, "B", "/dev/ttysB")
    sessions.register("C", cwd="/x/C"); _ident(sessions, "C", "/dev/ttysC")
    daemon.handle_message(_msg(MsgType.CYCLE_SESSION, "", direction="next"))
    # A filtered out of the roster -> cur falls back to 0 over [B,C]; next -> C. Never A.
    assert sessions.speaker() == "C"
    assert sessions.workspace() != "A"


# --- 6. 1 live + 1 phantom -> filtered roster has <2 -> error tone (no phantom landing) ---
def test_cycle_one_live_one_phantom_plays_error_tone(monkeypatch):
    _liveness(monkeypatch, dead={"/dev/ttysB"})
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    _ident(sessions, "A", "/dev/ttysA")
    sessions.register("B", cwd="/x/B"); _ident(sessions, "B", "/dev/ttysB")
    daemon.handle_message(_msg(MsgType.CYCLE_SESSION, "", direction="next"))
    assert speaker.earcons == ["error"]       # <2 live -> error, phantom never satisfies >=2


# --- 7. ⌃⌘J waiting-target skips a phantom that has a backlog, jumps to the live one ---
def test_jump_waiting_skips_phantom_backlog(monkeypatch):
    _liveness(monkeypatch, dead={"/dev/ttysB"})
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    _ident(sessions, "A", "/dev/ttysA")
    sessions.register("B", cwd="/x/B"); _ident(sessions, "B", "/dev/ttysB")
    daemon._enqueue("B", "prose", "b backlog", False)     # phantom WITH backlog
    sessions.register("C", cwd="/x/C"); _ident(sessions, "C", "/dev/ttysC")
    daemon._enqueue("C", "prose", "c backlog", False)
    daemon.handle_message(_msg(MsgType.JUMP_WAITING, ""))
    assert sessions.speaker() == "C"          # phantom B skipped; jumped to live C
```

Run: `.venv/bin/python -m pytest tests/test_sp3fix_ring.py -q`
Expected: FAIL — `is_live`/`tty_alive` don't exist yet (`AttributeError`); once the monkeypatch target is created, tests 1/3/5/6/7 still fail because the phantom is not yet filtered.

---
- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_sp3fix_ring.py -q`
Expected: FAIL/ERROR — `ttyutil.tty_alive` does not exist (monkeypatch `AttributeError`).

- [ ] **Step 3: Add `tty_alive` to `ttyutil.py`**

`src/sonari/ttyutil.py` — append the predicate (mirrors the injectable-runner spirit; `os` is already imported):
```python
def tty_alive(tty: str) -> bool:
    """True if this tty's device node currently exists — a timestamp-free liveness
    signal for a terminal session: on macOS the pty slave node vanishes the instant
    its terminal closes. Empty/unknown tty -> True (fail OPEN: never hide a live
    session whose best-effort capture returned '')."""
    if not tty:
        return True
    try:
        return os.path.exists(tty)
    except OSError:      # belt-and-suspenders; a weird path can never classify live as dead
        return True
```

- [ ] **Step 4: Add `is_live` to `SessionManager`**

`src/sonari/sessions.py` — add the module import at the top (after `from dataclasses import dataclass`):
```python
from sonari import ttyutil
```
(No import cycle: `ttyutil` imports only `os`.) Then add the method to `SessionManager` (next to `identity`, `:133-134`):
```python
    def is_live(self, session: str) -> bool:
        """True if *session*'s terminal is still open (its captured tty device node
        exists). Fail-open: an unknown identity or empty tty -> live (never hide a
        live session). Pure read over _identities; writes nothing."""
        ident = self._identities.get(session)
        return ttyutil.tty_alive(ident.tty if ident is not None else "")
```

- [ ] **Step 5: Filter at the two COLD call sites**

`src/sonari/daemon/features/focus.py` `on_cycle_session` — replace the roster line (`:121`) and extend its comment:
```python
    # Fork 2 = KEEP: the roster INCLUDES muted sessions (filter at the CALL SITE, never
    # in session_ids(), so the insertion-order pins in test_sessions.py survive). W1:
    # also drop PHANTOM sessions (closed terminal -> dead tty node) via is_live — a pure
    # read. is_live is independent of `stopped`, so a muted-but-live session stays
    # cycle-reachable (R7); only muted+dead (or active+dead) drops.
    roster = [s for s in sessions.session_ids() if sessions.is_live(s)]
```
(The anchor `fg = sessions.workspace()` (`:128`) and the existing `cur = roster.index(fg) if fg in roster else 0` fallback (`:129`) are UNCHANGED — if the anchor itself is a phantom it is absent from the filtered roster and `cur` falls back to 0 over the live-only roster, which by construction lands on a live session. This anchor-missing fallback is pre-existing behavior, not introduced by W1; OPUS should confirm the resulting landing is acceptable, per test 5.)

`src/sonari/daemon/features/focus.py` `_waiting_target` (`:17-22`) — add a local `sessions` and the liveness clause to the gate:
```python
    blocked, prose = [], []
    sessions = ctx.host.sessions
    spk = sessions.speaker()
    for sess, st in ctx.host._streams.items():          # insertion-ordered
        if (sess == exclude or sess == spk or st.stopped
                or len(st.queue) == 0 or not sessions.is_live(sess)):
            continue
        (blocked if st.queue.has_decision() else prose).append(sess)
```
(Only phantoms with a live-looking BACKLOG polluted `_waiting_target` — an empty-queue phantom is already inert via the `len(st.queue) == 0` skip; the new clause covers the backlog case.)

- [ ] **Step 6: Run green + affected suites + guards**

Run: `.venv/bin/python -m pytest tests/test_sp3fix_ring.py tests/test_daemon_cycle.py tests/test_daemon_focus_nav.py tests/test_sessions.py tests/test_ttyutil.py tests/test_concurrency_guards.py -q`
Expected: PASS. `test_sessions.py` (insertion-order pins) stays green — `session_ids()` is untouched; the filter is at the call site. Then full suite `.venv/bin/python -m pytest -q` → green (baseline + 7 new).

- [ ] **Step 7: Commit**

```bash
git add src/sonari/ttyutil.py src/sonari/sessions.py src/sonari/daemon/features/focus.py tests/test_sp3fix_ring.py
git commit -m "fix(sp3.1): filter phantom sessions from cycle + waiting-target rosters via tty liveness (W1)"
```

---

## Final: full suite + invariant sweep

- [ ] **Run the whole suite:** `.venv/bin/python -m pytest -q` — 0 failures, 1 skip, 3 concurrency guards + `real_keep_going_fires` green.
- [ ] **Invariant sweep (confirm, don't assume):**
  - **M1:** no W1 filter runs under the speak-loop lock; `git grep -n 'is_live\|tty_alive' src/sonari/daemon/host.py` returns nothing (keep-going untouched).
  - **R12:** `git grep -n '_foreground =' src/sonari/sessions.py` shows writers ONLY in `set_foreground`/`focus`/`unregister`; no W1/W3/W4 change writes `_foreground`.
  - **Fail-open (W1):** `is_live` returns True for empty tty and on `OSError`; the two call sites only ever HIDE a session, never mutate it.
  - **W4 idempotency:** `set_identity`'s don't-clobber rule is unchanged; only the call FREQUENCY (every prompt) increased.
  - **Chirp primitive intact (W2):** `git grep -n 'speaker.pitch(' src/sonari` shows exactly ONE remaining call — `decisions.py:192` (the answer chirp, F1 KEEP).

## Self-Review

**1. Task coverage — every design-oracle work-item → its task:**
- W2 remove cycle+nav chirps, keep answer chirp → T1. ✓
- W3 both-sides + counts ⌃⌘W grammar, drop spearcon split, None-branch untouched → T2. ✓
- W4 identity piggyback on UPS SET_FOREGROUND + "field present" guard, SESSION_START unchanged → T3. ✓
- W1 `tty_alive` + `is_live` + cycle/waiting-target filters, keep-going untouched → T4 (OPUS). ✓
- Vetoable defaults F1/F2/F3 coded + each flip noted; deferred items (GC, timestamps, keep-going backlog phantom, stuck pointer) referenced, not built. ✓

**2. Placeholder scan:** every code step has complete runnable code (no `...`/`TODO`).

**3. Self-checked each test against its own step code by hand-tracing** (single vs multi drain, exact strings):
- T2 pins: hand-traced `M` (muted) per fixture from the ACTUAL background stream set. `where_am_i:39` = **1 muted** (bg3 is stopped-with-queue), correcting the synthesis's "0". All other pins have no background stopped stream → M=0. Each ⌃⌘W pin is one enqueue + `cur is None` → single `_speak_loop_once()` yields exactly the asserted string.
- T4 ring math: traced each cycle landing through `roster = [live...]`, `cur = index(fg) if fg in roster else 0`, `target = roster[(cur+step)%len]`. Anchor-phantom (test 5) → cur=0 over [B,C] → next→C. 1-live+1-phantom (test 6) → filtered len 1 <2 → error tone. Muted-live (test 2) lands and `set_speaker(None)` leaves workspace==B.

**4. What this review caught (disagreements with the synthesis — flagged, not silently diverged):**
- **`test_daemon_where_am_i.py:39` muted count is 1, not 0** (synthesis §4 said 0). The fixture's `bg3` is `stopped=True` with a queued item → a background MUTED stream → M=1. Baked the correct string in.
- **The W4 breaking test is `test_hooks_entry.py:238-242` (the UPS exact-list), NOT `:250-339`.** The synthesis said the SessionStart-region tests (250-339) "invert"; verified against HEAD they SURVIVE unchanged (W4 leaves the SessionStart branch — and thus its identity-less SET_FOREGROUND and identity-carrying SESSION_START — exactly as-is). The one exact-list assertion that breaks is the UPS test above line 243. Retargeted the fix + made it hermetic (monkeypatch env + `controlling_tty`).
- **Synthesis W1 test #5 wording ("lands on the one live session") is ambiguous** — with only 1 live + phantom-anchor the filtered roster is <2 → error (test 6), so test 5 must carry ≥2 live. Coded test 5 as phantom-anchor + 2 live (lands on a live session, never the phantom), consistent with test 6.
