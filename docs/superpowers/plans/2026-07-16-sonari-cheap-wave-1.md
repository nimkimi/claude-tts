# Sonari Cheap Wave 1 (13 ratified eyes-free fixes + the REREAD_OPTIONS sub-item) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Authored from the ratified design oracle `docs/superpowers/specs/2026-07-16-sonari-cheap-wave-1-design.md` (read it first — it is the single source of truth; its §16 code-level refinements OVERRIDE any older atlas fix directions). All `file:line` quotes below are verified against `HEAD = bae3050` on branch `build/cheap-wave-1` (== `main`).

**Goal:** Ship the 13 ratified W-items (spec §2–§14) plus the owner-approved REREAD_OPTIONS fallback sub-item, in the spec's riskless-first wave order. Highlights: ⌃⌘J's empty cue jumps the backlog (W1); `HistoryEntry` gains monotonic stamps (W2, substrate only); verbosity confirms itself (W3); ⌃⌘D stops silently eating queues on a miss (W4); Policy-A submits lift a stale quiet-hold (W5); 3-class failure tones (W6); permission expiry gets an earcon + queue cleanup (W7); a restart boot cue (W8); decision chimes gain the asking session's spearcon call-sign (W9); the ⌃⌘W Also-map surfaces the quiet unheard pile (W10); the two-pointer collapse retargets ⌃⌘J + confirmations onto `workspace()` (W11); a new ⌃⌘R repeat-last verb (W12); and the keep-going pre-roll spearcon inside the M1 lock (W13).

**Ratified since the spec (folded in as decided, NOT open):** repeat-last chord = **⌃⌘R** (locked). Earcon assets provisionally Basso (misdirected) / Blow (system failure) / Purr (permission expired) — code references earcon KINDS only; assets stay config-level so the owner's ear-pass can swap them without a code change. The REREAD_OPTIONS fallback sub-item is IN scope (T3).

**Plan-author decisions (recorded, one line each):**
1. **W13 mechanism = the spec's `enqueue_front(spearcon)` then `pop_next()` alternative** (spec §14 offers two equivalents, "reviewer's pick") — it reuses the existing pop+claim shape verbatim, so FLUSH/STOP semantics are inherited with zero special-casing and the locked block's shape is visibly unchanged.
2. **W6/W7 never-silent fallback lives in `speaker.py` as a module-level `_FALLBACK_EARCONS` dict** (the `pitch()` package-side precedent, spec §16.1); the three new kinds ALSO join macOS `_DEFAULTS` so fresh installs get config-level entries — a config entry always wins, so the owner swaps assets by config edit, no code change.
3. **W12 handler lives in `playback.py`** (spec offered "playback.py or a sibling") — it is a playback verb sharing the file's STOP/SKIP/JUMP_DECISION vocabulary; the capture write happens ONLY in the main branch's existing tail lock (the held branch plays only pause-exempt control cues, which are `mute_exempt` and thus never capture targets).
4. **W6's guard obligation is met by a new DETERMINISTIC test in `test_concurrency_guards.py`** (a raising speaker through the real `_speak_loop_once` → `error_system` + loop survival), not by mutating the stress rotation — the stress runner never raises, so "the guards already drive speak failures" needed a real test, and the never-weaken mandate forbids touching existing assertions.

**Tech Stack:** Python 3, `pytest`, the existing daemon (`src/sonari/daemon/*`, `src/sonari/sessions.py`, `src/sonari/queue.py`, `src/sonari/history.py`, `src/sonari/speaker.py`). macOS-only. No new runtime deps. No Swift change (⌃⌘R flows through the resolved keymap; hotkeyd registers whatever the resolved JSON says).

## Global Constraints (campaign :11-18 + spec §17 — binding on every task)

- **Keep the machinery, touch only the decision layer.** No rewrites of `session_stream.py`, the `SpeechQueue` mechanism, `ProseAssembler`, the speak-loop pop+claim+speak+note_spoken core and cancel-epoch/barge-in, `SessionHistory` STORAGE (W2 EXTENDS, never replaces), the dispatch/registry/server/Ctx glue.
- **`tests/test_concurrency_guards.py` green at EVERY commit; speak-loop changes join the hammer set.** Applies to W6 (failure-tone path, T5), W12 (capture, T11), W13 (pre-roll, T12). NEVER weaken an existing assertion; extensions are additive.
- **TDD, spec as oracle.** Every given/when/then in the spec becomes a test; red → green → commit, bite-sized. Exact spoken strings from the spec, byte-for-byte.
- **macOS-only; Python 3 / `say` / `afplay` / the Swift hotkeyd; NO new runtime deps.**
- **Ratified 2026-06-29 decisions stay binding:** Policy A untouched (W5 fixes the enum write UNDER it), global verbosity untouched (W3/W10 are cue/content), the seven anchors respected. R12: `_foreground` written ONLY by `set_foreground`/`focus`/`unregister`. Fork-2: nothing un-mutes a session. M1: the keep-going scan+select+set_speaker+pop+claim stays ONE locked block — W13 extends INSIDE it, W12 captures under the EXISTING tail lock; no new locked region, no gap.
- **Deploy is the owner's step** (`./bin/sonari install` from a real GUI Terminal); live audio feel is his ears — mechanical verification only from sessions.
- **Wave-local:** suite green from the **987 passed / 1 skipped** baseline (measured at `bae3050`, ~9 s); no frontier substrate, no persistence, no presence model (SP4/SP5/SP6 borders per spec). Expected end state ≈ 1052 passed / 1 skipped (+65, inside the spec's +55–70 envelope). Per-task counts below assume the listed new tests; GREEN is the invariant — if a pinned-test update shifts a count, record the actual.
- **Commit style:** `feat(wave1)` / `fix(wave1)` / `test(wave1)` / `docs(wave1)`, one commit per task (T11's protocol+handler+registry+guard edits are ONE commit — the import-completeness guard makes a split an import-time error). Repo git identity already set (noreply) — do not touch git config. Never mention tooling/sessions in commit messages.

## Test-harness facts (verified against the repo at `bae3050` — use these exact shapes)

- Suite: `.venv/bin/python -m pytest -q` from the repo root — **baseline 987 passed, 1 skipped** (re-verified by this plan's author). Guards alone: `.venv/bin/python -m pytest tests/test_concurrency_guards.py -q` → currently **3 passed** (T5 makes it 4).
- `from tests.daemon_helpers import make_daemon, stream_queue` — `make_daemon(verbosity="everything", foreground="fg")` returns the 5-tuple `(daemon, queue, speaker, sessions, config)` (`tests/daemon_helpers.py:75-87`); `queue` is the foreground session's own stream queue; `daemon._spearcons` is a `FakeSpearconCache` (`daemon_helpers.py:7-33`): set `daemon._spearcons.available[folder] = path` for a HIT; a MISS appends to `.generated`. Every `.get(label)` appends to `.requested`.
- `FakeSpeaker` (`daemon_helpers.py:36-72`): `.spoken`, `.audio_paths`, `.earcons`, `.cancels`, `.pitches`, `.complete` (next speak() completed?). Tests are **synchronous**: `handle_message(...)` then `daemon._speak_loop_once()` then assert. T8 adds `.earcon_seqs` + `earcon_then()` to it.
- Module-local message helper for NEW test modules (`tests/test_sp3fix_ring.py:7-9` idiom):
  ```python
  def _msg(t, session, **kw):
      return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}
  ```
  When APPENDING to an existing module, import `make_daemon` INSIDE the test function and inline message dicts — never add a module-level `_msg` that could collide.
- `daemon._enqueue(session, kind, text, is_decision, entry=None, mute_exempt=False, pause_exempt=False, at_front=False, names_session=False, audio_path=None)` (`host.py:223-247`) — T6 makes it return `item.id`. Freshly-allocated id == `daemon._next_id`.
- `daemon._current_item` is a settable property; `daemon._pending_heard` is the marker dict; `daemon._stream(s).queue._items` is the deque; `daemon._stream(s).stopped` is the per-session mute; `daemon.voice_state` is the cold-path enum shim.
- The ⌃⌘W capture-and-requeue discipline to mirror in W12: `control.py:222-254` (`cur = host._current_item`; `entry = host._pending_heard.get(cur.id)`; `speaker.cancel()`; re-`_enqueue` the interrupted item with `entry=` + all its flags + `at_front=True` FIRST, then the cue `at_front=True` — the cue ends up at index 0, the interrupted item at index 1).
- The ⌃⌘W None-speaker routing to mirror (W12): `control.py:194-221` — `ws = sessions.workspace()`; playable = `ws is not None and not (ws_st is not None and ws_st.stopped)`; playable → enqueue to `ws`; else `speaker.earcon("error")`.
- Raise fake: `from tests.test_daemon_focus_follow import RecordingRaiseService`; `daemon.raise_service = rs`; `rs.attempts` is the list. `will_attempt(None)` is False.
- Handlers: `@handler(MsgType.X)` populates the registry at import; feature modules are side-effect-imported in `host.py:22-30`; `daemon/__init__.py:11-46` `assert_complete` makes a missing handler an import-time error — **adding a MsgType requires adding it to that list in the same commit** (T11).
- Concurrency guards: `_make_real_daemon(runner, foreground="s0")` (`test_concurrency_guards.py:76-84`); the hammer ops list is at `:242-244`; the module-level `_select_keep_going` counting patch (`:158-165`) is restored in a `finally` (`:316-317`) — do not disturb. The stress daemon currently has `spearcons=None` (T12 arms it).
- `history.record(session, kind, text)` returns the `HistoryEntry`; `history.unheard(session)` is current-turn-bounded (`history.py:145-154`). Recording WITHOUT enqueuing is the test shape for "recorded-but-not-queued" (quiet gate / preemption-cut piles).
- `save_config` is imported into `sonari.daemon.features.control` at module top — tests that fire SET_VERBOSITY/SET_RATE monkeypatch `control.save_config` to a no-op (`monkeypatch.setattr(control, "save_config", lambda cfg: None)`).
- Prose muting under quiet happens at `on_prose` (`prose.py:20-21`) — direct `_enqueue` cues speak at every verbosity.
- `_attributed_text` (`host.py:288-307`): never prefixes the FIRST utterance (`_last_spoken_session is None`) — attribution tests must speak one item first to establish `last_spoken`.
- `sessions.set_speaker(None)` is legal (the chooser's muted-landing release uses it) — the "speaker() is None" test shape.

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `src/sonari/history.py` | Modify | T1: `stamp` slot + injectable `clock` (W2); T9: `unheard()` docstring (first prod caller). |
| `src/sonari/daemon/features/focus.py` | Modify | T1: `at_front=True` on the empty cue (W1); T10: exclude/route via `workspace()` (W11). |
| `src/sonari/daemon/features/control.py` | Modify | T2: verbosity confirm (W3); T9: `_also_clause` unheard floor (W10); T10: rate confirm → `workspace()` (W11). |
| `src/sonari/daemon/features/lifecycle.py` | Modify | T4: quiet-hold lift on Policy-A take (W5). |
| `src/sonari/daemon/features/playback.py` | Modify | T3: ⌃⌘D miss guard (W4); T11: `on_repeat_last` (W12). |
| `src/sonari/daemon/features/decisions.py` | Modify | T3: `_pending_decisions["text"]` + REREAD fallback; T5: `error_misdirected` at the pd-None branch (W6); T6: `item_id` stored (W7); T8: blocking-chime call-sign (W9). |
| `src/sonari/daemon/features/prose.py` | Modify | T8: `on_earcon` decision-kind call-sign branch + comment sweep (W9). |
| `src/sonari/daemon/host.py` | Modify | T5: `error_system` in `_signal_speak_failure` (W6); T6: `_enqueue` returns id + `_expire_permission` (W7); T11: W12 capture in the tail lock + `_last_utterance` shim; T12: W13 pre-roll inside the keep-going lock. |
| `src/sonari/daemon/state.py` | Modify | T11: `_last_utterance` slot beside `_last_spoken_session`. |
| `src/sonari/daemon/bootstrap.py` | Modify | T7: `BOOT_CUE` + `_start_boot_cue()` thread before `daemon.run()` (W8). |
| `src/sonari/speaker.py` | Modify | T5: `_FALLBACK_EARCONS` + never-silent `earcon()` (W6/W7 kinds); T8: `earcon_then()` sequencer (W9). |
| `src/sonari/platform/macos/earcon.py` | Modify | T5: `_DEFAULTS` += error_misdirected/error_system; T6: += permission_expired. |
| `src/sonari/hooks_entry.py` | Modify | T8: decision EARCON msgs gain `session=` (W9). |
| `src/sonari/protocol.py` | Modify | T11: `REPEAT_LAST = "repeat_last"`. |
| `src/sonari/daemon/__init__.py` | Modify | T11: `MsgType.REPEAT_LAST` in `assert_complete` (same commit as the constant). |
| `src/sonari/keymap.py` | Modify | T11: `ACTION_MESSAGES["repeat_last"]` + `_DEFAULT_KEYS["repeat_last"] = "r"`. |
| `src/sonari/daemon/features/chooser.py` | Modify | T10: drop the provably-dead `or sessions.foreground()` at `:48` (W11 cleanup, zero behavior). |
| `tests/test_history.py` | Modify (append) | T1: stamp tests. |
| `tests/test_daemon_focus_nav.py` | Modify (append/update) | T1: W1 cue-order test; T10: W11 oracle updates. |
| `tests/test_verbosity_confirm.py` | **New** | T2. |
| `tests/test_jump_decision_miss.py` | **New** | T3 (incl. REREAD fallback). |
| `tests/test_voice_state_submit.py` | **New** | T4. |
| `tests/test_failure_tones.py` | **New** | T5. |
| `tests/test_concurrency_guards.py` | Modify | T5: +1 deterministic failure-tone guard; T11: hammer ops += REPEAT_LAST; T12: spearcons armed + additive pre-roll assertion. Assertions never weakened. |
| `tests/test_permission_expiry.py` | **New** | T6. |
| `tests/test_boot_cue.py` | **New** | T7. |
| `tests/test_decision_callsign.py` | **New** | T8 (+ hooks-entry pins updated in their own module). |
| `tests/test_also_map_unheard.py` | **New** | T9. |
| `tests/test_pointer_collapse.py` | **New** | T10 (+ updates in the two foreground-pinned files). |
| `tests/test_repeat_last.py` | **New** | T11. |
| `tests/test_keepgoing_preroll.py` | **New** | T12. |
| `tests/daemon_helpers.py` | Modify | T8: `FakeSpeaker.earcon_seqs` + `earcon_then()`. |
| `tests/test_protocol.py`, `tests/test_daemon_registry.py`, `tests/test_keymap.py` | Modify | T11: REPEAT_LAST rows / ALL_TYPES / default-binding sets. |

**Task order:** T1 → T12, exactly the spec's riskless-first wave order (T1=W1+W2 folded; T3 carries the REREAD sub-item; T11=W12 and T12=W13 stand alone with their M1 + guard obligations spelled out).

---

## Task T1 — W1 ⌃⌘J empty cue `at_front` + W2 monotonic `HistoryEntry` stamps

**Files:** Modify `src/sonari/daemon/features/focus.py`, `src/sonari/history.py`. Tests: append to `tests/test_daemon_focus_nav.py`, `tests/test_history.py`.

**Interfaces produced:** `HistoryEntry.stamp: float` (monotonic clock at record time; NO consumer this wave — spec W2 scope fence); `SessionHistory(cap=..., clock=time.monotonic)` injectable. The ⌃⌘J empty cue is the NEXT thing voiced. *Depends on: nothing.*

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_history.py` (reuse its existing `SessionHistory` import; add these functions at the end):

```python
def test_record_stamps_with_injected_monotonic_clock():
    """W2/E1: stamps come from the injected clock, non-decreasing (spec §3)."""
    ticks = iter([10.0, 11.5, 11.5, 12.0])
    h = SessionHistory(cap=10, clock=lambda: next(ticks))
    e1 = h.record("s", "prose", "a")
    e2 = h.record("s", "prose", "b")
    e3 = h.record("s", "prose", "c")
    assert (e1.stamp, e2.stamp, e3.stamp) == (10.0, 11.5, 11.5)
    assert e1.stamp <= e2.stamp <= e3.stamp


def test_default_clock_is_monotonic_and_bounded():
    """Default clock = time.monotonic (ratified: monotonic, NOT time.time)."""
    import time as _t
    h = SessionHistory(cap=4)
    lo = _t.monotonic()
    e1 = h.record("s", "prose", "a")
    e2 = h.record("s", "prose", "b")
    hi = _t.monotonic()
    assert lo <= e1.stamp <= e2.stamp <= hi
```

Append to `tests/test_daemon_focus_nav.py` (imports INSIDE the function — no module-level helper collision):

```python
def test_jump_waiting_empty_cue_is_voiced_ahead_of_the_backlog():
    """W1: pressed mid-flood, 'No session waiting.' must be the NEXT thing voiced,
    not the tail of the very backlog you're escaping (spec §2)."""
    from tests.daemon_helpers import make_daemon
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon._enqueue("fg", "prose", "backlog one.", False)
    daemon._enqueue("fg", "prose", "backlog two.", False)
    daemon.handle_message({"v": 1, "type": "jump_waiting", "session": "fg"})
    texts = [it.text for it in queue._items]
    assert texts[0] == "No session waiting."
    assert texts[1:] == ["backlog one.", "backlog two."]
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_history.py tests/test_daemon_focus_nav.py -q`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'clock'`, `AttributeError: ... no attribute 'stamp'`, and the W1 order assertion (`texts[0] == "backlog one."`).

- [ ] **Step 3: Implement**

`src/sonari/history.py` — add `import time` under `from collections import deque`; then:

```python
class HistoryEntry:
    __slots__ = ("text", "kind", "msg_id", "seq", "turn_id", "heard", "stamp")

    def __init__(self, text: str, kind: str, msg_id: int, seq: int = 0,
                 turn_id: int = 0, stamp: float = 0.0) -> None:
        self.text = text
        self.kind = kind          # prose|choice|plan|permission
        self.msg_id = msg_id      # message group; bumped by end_message()/start_turn()
        self.seq = seq            # 0-based index within the group; seq 0 == its head
        self.turn_id = turn_id    # turn group; bumped by start_turn() (a new prompt)
        self.heard = False
        self.stamp = stamp        # monotonic clock at record time (W2/E1 substrate;
                                  # NO spoken string/earcon/handler reads it this wave)
```

`SessionHistory.__init__` gains the injectable clock (extend, don't replace):

```python
    def __init__(self, cap: int = 200, clock=time.monotonic) -> None:
        self._cap = cap
        self._clock = clock       # injectable for tests; monotonic by ratification
        self._entries: "dict[str, deque]" = {}
        self._msg_id: "dict[str, int]" = {}
        self._group_seq: "dict[str, int]" = {}   # next entry index within the open group
        self._turn_id: "dict[str, int]" = {}     # current turn per session (a new prompt bumps it)
```

`record` stamps (only the `HistoryEntry(...)` construction changes):

```python
        entry = HistoryEntry(text, kind, self._msg_id.get(session, 0), seq,
                             self._turn_id.get(session, 0), stamp=self._clock())
```

`src/sonari/daemon/features/focus.py:56-57` — the existing empty-cue enqueue gains `at_front=True` (string and other flags unchanged):

```python
        if tgt is not None:
            ctx.host._enqueue(tgt, "prose", "No session waiting.", False,
                              mute_exempt=True, pause_exempt=True, at_front=True)
```

(Per the spec's review-found correction: `control.py:218`'s "Nothing playing.", `playback.py:66`'s "Stopped.", and `playback.py:86`'s "All stopped." also lack `at_front` — they are OUT OF SCOPE, unchanged.)

- [ ] **Step 4: Run the tests + guards**

Run: `.venv/bin/python -m pytest tests/test_history.py tests/test_daemon_focus_nav.py tests/test_concurrency_guards.py -q`
Expected: all pass. Then the full suite: `.venv/bin/python -m pytest -q` → **990 passed, 1 skipped**.

- [ ] **Step 5: Commit**

`git add -A && git commit -m "feat(wave1): W1 jump-waiting empty cue at_front + W2 monotonic HistoryEntry stamps"`

---

## Task T2 — W3 verbosity confirmation on the live path

**Files:** Modify `src/sonari/daemon/features/control.py` (`on_set_verbosity`, `:106-113`). Tests: `tests/test_verbosity_confirm.py` (new).

**Interfaces:** consumes `sessions.workspace()` (born on W11's collapsed target); produces the exact strings `"Verbosity quiet." / "Verbosity medium." / "Verbosity everything."`, `mute_exempt=True, pause_exempt=True`, no `at_front`. Invalid value → unchanged silent early return. Idempotent (same value re-confirms). The dead `on_cycle_verbosity` (`:128-141`) is LEFT AS-IS (registry surgery out of scope). *Depends on: nothing.*

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_verbosity_confirm.py (new)
"""W3 (spec §4): SET_VERBOSITY confirms itself on the live path, at every
verbosity (direct _enqueue cues bypass the on_prose quiet gate), targeting
workspace() (born on W11's collapsed target)."""
from sonari.protocol import PROTOCOL_VERSION
from tests.daemon_helpers import make_daemon, stream_queue


def _msg(t, session, **kw):
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


def _nosave(monkeypatch):
    from sonari.daemon.features import control
    monkeypatch.setattr(control, "save_config", lambda cfg: None)


def test_each_level_speaks_its_exact_confirmation(monkeypatch):
    _nosave(monkeypatch)
    daemon, queue, speaker, sessions, config = make_daemon()
    for level, want in (("quiet", "Verbosity quiet."),
                        ("medium", "Verbosity medium."),
                        ("everything", "Verbosity everything.")):
        daemon.handle_message(_msg("set_verbosity", "fg", verbosity=level))
        daemon._speak_loop_once()
        assert speaker.spoken[-1] == want
        assert config["verbosity"] == level


def test_confirmation_is_idempotent_on_resets_of_the_same_value(monkeypatch):
    _nosave(monkeypatch)
    daemon, queue, speaker, sessions, config = make_daemon()
    for _ in range(2):
        daemon.handle_message(_msg("set_verbosity", "fg", verbosity="quiet"))
        daemon._speak_loop_once()
    assert speaker.spoken == ["Verbosity quiet.", "Verbosity quiet."]


def test_invalid_value_stays_silent(monkeypatch):
    _nosave(monkeypatch)
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon.handle_message(_msg("set_verbosity", "fg", verbosity="loud"))
    assert len(queue._items) == 0
    assert config["verbosity"] == "everything"


def test_confirmation_lands_on_the_workspace_not_the_drifted_speaker(monkeypatch):
    _nosave(monkeypatch)
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.register("b", cwd="/x/b")
    sessions.set_speaker("b")                      # keep-going drifted the voice
    daemon.handle_message(_msg("set_verbosity", "fg", verbosity="medium"))
    assert [it.text for it in queue._items] == ["Verbosity medium."]
    assert len(stream_queue(daemon, "b")._items) == 0
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_verbosity_confirm.py -q`
Expected: 4 failed (no confirmation is enqueued today).

- [ ] **Step 3: Implement**

`src/sonari/daemon/features/control.py` — `on_set_verbosity` becomes:

```python
@handler(MsgType.SET_VERBOSITY)
def on_set_verbosity(ctx, msg):
    v = _valid_verbosity(msg.get("verbosity"))
    if v is None:
        return None
    ctx.host.config["verbosity"] = v
    save_config(ctx.host.config)
    # W3: confirm on the LIVE path (the built confirmation was stranded on the
    # dead CYCLE_VERBOSITY handler, 0 senders). Targets workspace() (W11's
    # collapsed pointer — the terminal you're at hears its own confirmation);
    # mute_exempt+pause_exempt so a settings readback can never be silently
    # swallowed while the voice is held — "Verbosity quiet." IS the last thing
    # you hear (direct _enqueue cues bypass the on_prose quiet gate).
    # Idempotent by design: setting the same value re-confirms (readback).
    ws = ctx.host.sessions.workspace()
    if ws is not None:
        ctx.host._enqueue(ws, "prose", "Verbosity {0}.".format(v), False,
                          mute_exempt=True, pause_exempt=True)
    return None
```

- [ ] **Step 4: Run tests + guards + suite**

Run: `.venv/bin/python -m pytest tests/test_verbosity_confirm.py tests/test_concurrency_guards.py -q` → 4 + 3 pass.
Full suite → **994 passed, 1 skipped**. (If an existing settings test asserted silence on SET_VERBOSITY, update it to the new ratified oracle and note it in the commit body.)

- [ ] **Step 5: Commit**

`git commit -am "feat(wave1): W3 verbosity confirmation on the live SET_VERBOSITY path"`

---

## Task T3 — W4 ⌃⌘D miss safety + the REREAD_OPTIONS fallback sub-item

**Files:** Modify `src/sonari/daemon/features/playback.py` (`on_jump_decision`), `src/sonari/daemon/features/decisions.py` (`on_permission_request` `:173`, `on_reread_options` `:200-211`). Tests: `tests/test_jump_decision_miss.py` (new).

**Interfaces:** `_pending_decisions[session]` gains `"text"` (the SAME string from `decisions.py:163`, no new computation). **Hit** = `queue.has_decision()` OR a live `_pending_decisions` entry. Miss → exactly `"No decision here."` (`mute_exempt, pause_exempt, at_front`, routed `speaker() or target`, else error earcon) and **nothing else happens**. Live-pending-but-unqueued → re-speak the stored text (`at_front, mute_exempt`), never drain. REREAD_OPTIONS falls back to the same stored text. *Depends on: nothing.*

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_jump_decision_miss.py (new)
"""W4 (spec §5) + the REREAD_OPTIONS sub-item: ⌃⌘D on a session with no hit
must say so and do NOTHING else — no drain, no cancel, no pointer/enum writes,
no raise. Hit predicate is two-part: queued decision OR live pending blocking
decision (queue-scoped alone would lie over an answerable-but-already-read ask)."""
from sonari.protocol import PROTOCOL_VERSION
from tests.daemon_helpers import make_daemon, stream_queue
from tests.test_daemon_focus_follow import RecordingRaiseService


def _msg(t, session, **kw):
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


def test_miss_speaks_the_cue_and_touches_nothing():
    daemon, queue, speaker, sessions, config = make_daemon()
    rs = RecordingRaiseService(will=True)
    daemon.raise_service = rs
    daemon.voice_state = "quiet-hold"              # any enum write would be visible
    daemon._enqueue("fg", "prose", "backlog one.", False)
    daemon._enqueue("fg", "prose", "backlog two.", False)
    before = [(it.id, it.text) for it in queue._items]
    daemon.handle_message(_msg("jump_decision", "fg"))
    after = [(it.id, it.text) for it in queue._items]
    assert after[0][1] == "No decision here."
    assert after[1:] == before                     # queue preserved byte-for-byte behind the cue
    assert speaker.cancels == 0                    # no barge-in
    assert daemon.voice_state == "quiet-hold"      # no enum write
    assert rs.attempts == []                       # no window raise
    head = queue._items[0]
    assert head.mute_exempt and head.pause_exempt  # spec-mandated flags


def test_queued_decision_hit_behaves_exactly_as_today():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon._enqueue("fg", "prose", "chatter.", False)
    daemon._enqueue("fg", "permission", "May I write x?", True)
    daemon.handle_message(_msg("jump_decision", "fg"))
    assert queue._items[0].is_decision             # drained to the decision
    assert speaker.cancels == 1                    # today's barge-in, unchanged


def test_pending_request_stores_its_spoken_text():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon.handle_message(_msg("permission_request", "fg", tool="Bash",
                               summary="rm -rf build"))
    assert daemon._pending_decisions["fg"]["text"] == "Bash: rm -rf build"


def test_live_pending_but_unqueued_respeaks_the_stored_prompt():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon.handle_message(_msg("permission_request", "fg", tool="Bash",
                               summary="rm -rf build"))
    queue.pop_next()                               # already narrated; still answerable
    daemon.handle_message(_msg("jump_decision", "fg"))
    texts = [it.text for it in queue._items]
    assert texts[0] == "Bash: rm -rf build"        # re-spoken from _pending_decisions["text"]
    assert "No decision here." not in texts        # never claims "no decision" over an answerable one
    assert speaker.cancels == 0                    # no drain, no barge-in


def test_reread_options_falls_back_to_the_pending_text():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon.handle_message(_msg("permission_request", "fg", tool="Bash",
                               summary="rm -rf build"))
    # on_permission_request never writes st.options -> options is empty for fg.
    daemon.handle_message(_msg("reread_options", "fg"))
    assert [it.text for it in queue._items][-1] == "Bash: rm -rf build"


def test_reread_options_without_any_pending_still_says_no_options():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon.handle_message(_msg("reread_options", "fg"))
    assert [it.text for it in queue._items] == ["No options right now."]


def test_miss_with_no_speaker_and_no_target_plays_error_tone():
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    daemon.handle_message(_msg("jump_decision", ""))
    assert speaker.earcons == ["error"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_jump_decision_miss.py -q`
Expected: FAIL — miss currently drains the queue + cancels; `KeyError: 'text'`; reread says "No options right now.".

- [ ] **Step 3: Implement**

`src/sonari/daemon/features/decisions.py:173` — extend the pending literal (reuse the `text` local from `:163`, one extra key, no new computation):

```python
    host._pending_decisions[session] = {"event": threading.Event(),
                                        "behavior": None, "text": text}
```

`decisions.py` — `on_reread_options` gains the fallback (W11 note: this handler legitimately stays on `foreground()` — unbound, 0 senders, out of the collapse's scope):

```python
@handler(MsgType.REREAD_OPTIONS)
def on_reread_options(ctx, msg):
    fg = ctx.host.sessions.foreground()
    if fg is None:
        return None
    st = ctx.host._streams.get(fg)
    text = st.options if st is not None else None
    if not text:
        # W4 sub-item: a live blocking permission never sets st.options
        # (on_permission_request writes _pending_decisions, not options), so
        # without this fallback REREAD_OPTIONS is silently broken for exactly
        # the asks that matter. Re-speak the stored prompt instead of lying.
        pending = ctx.host._pending_decisions.get(fg)
        if pending is not None:
            text = pending.get("text")
    if text:
        ctx.host._enqueue(fg, "choice", text, False)
    else:
        ctx.host._enqueue(fg, "prose", "No options right now.", False)
    return None
```

`src/sonari/daemon/features/playback.py` — `on_jump_decision`: insert the guard between `target = sessions.workspace()` and `crossed = ...` (everything below the guard is byte-identical today-behavior):

```python
@handler(MsgType.JUMP_DECISION)
def on_jump_decision(ctx, msg):
    sessions = ctx.host.sessions
    target = sessions.workspace()
    # W4 miss safety: a HIT = a queued decision item OR a live pending blocking
    # decision. has_decision() scans QUEUED items only (queue.py:78-81), so an
    # answerable-but-already-narrated permission has an empty queue — a queue-
    # scoped cue would lie exactly where honesty matters (spec §5 deviation).
    # On a miss: speak the cue and do NOTHING else — no drain, no cancel, no
    # voice/workspace move, no heard-marking, no voice_state write, no raise
    # (Fork-2: the stream is never touched). Routing mirrors ⌃⌘J's empty cue.
    st_t = ctx.host._streams.get(target) if target is not None else None
    pending = ctx.host._pending_decisions.get(target) if target is not None else None
    has_queued = st_t is not None and st_t.queue.has_decision()
    if not has_queued:
        tgt = sessions.speaker() or target
        if pending is not None:
            # Answerable-but-already-narrated (inside the ~120s window):
            # re-speak the STORED prompt; the queue holds nothing to drain to.
            ctx.host._enqueue(tgt, "prose", pending["text"], False,
                              mute_exempt=True, at_front=True)
        elif tgt is not None:
            ctx.host._enqueue(tgt, "prose", "No decision here.", False,
                              mute_exempt=True, pause_exempt=True, at_front=True)
        else:
            ctx.host.speaker.earcon("error")
        return None
    crossed = target != sessions.speaker()   # ... (rest of the handler unchanged)
```

- [ ] **Step 4: Run tests + guards + suite**

Run: `.venv/bin/python -m pytest tests/test_jump_decision_miss.py tests/test_concurrency_guards.py -q` → 7 + 3 pass.
Full suite → **1001 passed, 1 skipped**. If an existing ⌃⌘D test exercised the miss path expecting a drain, update it to the ratified oracle (the drain-on-miss WAS the bug) and say so in the commit body.

- [ ] **Step 5: Commit**

`git commit -am "feat(wave1): W4 jump-decision miss safety + reread-options pending fallback"`

---

## Task T4 — W5 `voice_state` staleness fix on Policy-A submit

**Files:** Modify `src/sonari/daemon/features/lifecycle.py` (`on_set_foreground` take-voice branches, `:71-85`). Tests: `tests/test_voice_state_submit.py` (new).

**Interfaces:** GIVEN `voice_state == "quiet-hold"`, WHEN a Policy-A submit takes/retains the voice AND the resulting speaker's stream is NOT stopped → `voice_state = "flowing"`, same handler transaction, no new speech. Denied branch: no write. `stopped-all`: never lifted. Muted self-submit: no lift. *Depends on: nothing.*

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_voice_state_submit.py (new)
"""W5 (spec §6): the missing enum write UNDER Policy A — the speak loop's held
branch gates on the STREAM's .stopped (host.py:451-453), not the enum, so a
Policy-A submit could leave voice_state='quiet-hold' while the voice audibly
talks (and keep the keep-going gate closed, host.py:485)."""
from sonari.protocol import PROTOCOL_VERSION
from tests.daemon_helpers import make_daemon


def _msg(t, session, **kw):
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


def test_take_voice_submit_lifts_quiet_hold_to_flowing():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon.voice_state = "quiet-hold"
    daemon.handle_message(_msg("set_foreground", "fg", cwd="/x/fg"))  # idle -> takes voice
    assert daemon.voice_state == "flowing"


def test_denied_submit_never_lifts():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon.voice_state = "quiet-hold"
    daemon._enqueue("fg", "prose", "still talking.", False)  # speaker fg non-quiescent
    daemon.handle_message(_msg("set_foreground", "b", cwd="/x/b"))    # denied: register-only
    assert daemon.voice_state == "quiet-hold"
    assert sessions.foreground() == "fg"


def test_stopped_all_is_never_lifted():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon.voice_state = "stopped-all"
    daemon.handle_message(_msg("set_foreground", "fg", cwd="/x/fg"))
    assert daemon.voice_state == "stopped-all"     # the master quiet is deliberate


def test_muted_self_submit_keeps_the_hold_honest():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon.voice_state = "quiet-hold"
    daemon._stream("fg").stopped = True            # the speaker muted itself (⌃⌘S)
    daemon.handle_message(_msg("set_foreground", "fg", cwd="/x/fg"))
    assert daemon.voice_state == "quiet-hold"      # "on hold" remains true


def test_where_am_i_says_playing_after_the_lift():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon.voice_state = "quiet-hold"
    daemon.handle_message(_msg("set_foreground", "fg", cwd="/x/fg"))
    daemon.handle_message(_msg("where_am_i", "fg"))
    daemon._speak_loop_once()
    assert speaker.spoken[-1] == "Voice: fg 1, playing."   # derivation unchanged; input no longer stale
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_voice_state_submit.py -q`
Expected: tests 1 and 5 FAIL (`voice_state` stays `"quiet-hold"`; ⌃⌘W says "on hold"); 2–4 already pass (they pin the MUST-NOTs).

- [ ] **Step 3: Implement**

`src/sonari/daemon/features/lifecycle.py` — inside `if voice_idle or is_speaker:` (after the `register`/`set_foreground` calls, before the `else:` denied branch):

```python
        # W5: a Policy-A submit that takes (or retains) a speakable voice lifts a
        # stale quiet-hold — the enum must match what the ear hears (the held
        # branch gates on the STREAM's .stopped, host.py:451-453, not this enum;
        # the stale enum also kept the keep-going gate closed, host.py:485).
        # stopped-all is NEVER lifted (the master quiet is deliberate; under it
        # new streams are born stopped anyway — belt-and-braces). A stopped
        # stream keeps the hold honest: a muted self-submitter stays "on hold".
        if ctx.host.voice_state == "quiet-hold":
            st = ctx.host._streams.get(session)
            if st is None or not st.stopped:
                ctx.host.voice_state = "flowing"
```

(Both take-voice branches funnel through this one block; the denied `else` at `:85` is untouched. Precedents for the write: `focus.py:66`, `playback.py:55,100`. This runs under the same lock as the M1 claim — the handler transaction, `host.py:543-551`.)

- [ ] **Step 4: Run tests + guards + suite**

Run: `.venv/bin/python -m pytest tests/test_voice_state_submit.py tests/test_concurrency_guards.py -q` → 5 + 3 pass.
Full suite → **1006 passed, 1 skipped**.
Note for the SP4 plan (do NOT edit it here): the SP4 synthesis's inherited open-Q6 is discharged by this item (spec §6).

- [ ] **Step 5: Commit**

`git commit -am "fix(wave1): W5 lift stale quiet-hold when a Policy-A submit takes a speakable voice"`

---

## Task T5 — W6 distinct failure tones (3-class taxonomy) + the deterministic guard

**Files:** Modify `src/sonari/speaker.py`, `src/sonari/platform/macos/earcon.py`, `src/sonari/daemon/features/decisions.py:188`, `src/sonari/daemon/host.py:431`. Tests: `tests/test_failure_tones.py` (new) + ONE additive test in `tests/test_concurrency_guards.py`.

**Interfaces:** earcon KINDS `error_misdirected` (decisions.py pd-None branch) and `error_system` (`_signal_speak_failure`); all other `error` sites byte-identical Sosumi. Never-silent fallback: config dict first, then `_FALLBACK_EARCONS` — ONLY for the new kinds (old kinds keep silent-no-op semantics). Assets are config-level data (owner's ear-pass swaps them without code). *Depends on: nothing.*

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_failure_tones.py (new)
"""W6 (spec §7): three failure classes, principled by what-you-should-do-next.
Invalid/nothing-there keeps Sosumi ('error', unchanged); misdirected answers get
'error_misdirected'; speak-loop crashes get 'error_system'. New kinds can NEVER
be silently disabled on an existing install (speaker-side fallback — the pitch()
precedent; bootstrap merges defaults only when the whole earcons key is absent)."""
from sonari.protocol import PROTOCOL_VERSION
from sonari.speaker import Speaker
from tests.daemon_helpers import make_daemon


def _msg(t, session, **kw):
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


def test_misdirected_answer_plays_error_misdirected():
    daemon, queue, speaker, sessions, config = make_daemon()
    # No pending decision on the workspace: valid intent, wrong session.
    daemon.handle_message(_msg("answer_permission", "fg", behavior="allow"))
    assert speaker.earcons == ["error_misdirected"]


def test_invalid_behavior_keeps_plain_error():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon.handle_message(_msg("answer_permission", "fg", behavior="maybe"))
    assert speaker.earcons == ["error"]


def test_speak_loop_failure_plays_error_system():
    daemon, queue, speaker, sessions, config = make_daemon()
    def _boom(text=None, audio_path=None, cancel_epoch=None):
        raise RuntimeError("synth failure")
    speaker.speak = _boom
    daemon._enqueue("fg", "prose", "doomed.", False)
    daemon._speak_loop_once()                      # must not raise; signals audibly
    assert "error_system" in speaker.earcons
    assert daemon._current_item is None            # claim released


def test_new_kinds_fall_back_on_an_existing_installs_config():
    played = []
    sp = Speaker(earcon_player=lambda p: played.append(p) or None,
                 earcons={"error": "/System/Library/Sounds/Sosumi.aiff"})
    sp.earcon("error_misdirected")
    sp.earcon("error_system")
    assert played == ["/System/Library/Sounds/Basso.aiff",
                      "/System/Library/Sounds/Blow.aiff"]


def test_config_entry_wins_and_old_kinds_keep_silent_noop():
    played = []
    sp = Speaker(earcon_player=lambda p: played.append(p) or None,
                 earcons={"error_misdirected": "/custom/door.aiff"})
    sp.earcon("error_misdirected")                 # config override wins
    sp.earcon("turn_done")                         # absent OLD kind: silent no-op unchanged
    assert played == ["/custom/door.aiff"]


def test_macos_defaults_gain_the_new_kinds():
    from sonari.platform.macos.earcon import _DEFAULTS
    assert _DEFAULTS["error_misdirected"] == "/System/Library/Sounds/Basso.aiff"
    assert _DEFAULTS["error_system"] == "/System/Library/Sounds/Blow.aiff"
    assert _DEFAULTS["error"] == "/System/Library/Sounds/Sosumi.aiff"  # unchanged
```

Append to `tests/test_concurrency_guards.py` (ADDITIVE — the failure-tone scenario joins the PERMANENT guard file; campaign :14):

```python
class _RaisingSpeaker:
    """speak() always raises — the W6 failure-tone path: the real loop must
    signal error_system audibly and survive (an eyes-free swallowed exception
    is a SILENT no-op, the worst outcome)."""

    def __init__(self):
        self.earcons: list = []
        self._epoch = 0

    def speak(self, text=None, audio_path=None, cancel_epoch=None):
        raise RuntimeError("synthesized failure")

    def cancel_epoch(self):
        return self._epoch

    def cancel(self):
        self._epoch += 1

    def earcon(self, kind):
        self.earcons.append(kind)


def test_speak_failure_signals_error_system_and_the_loop_survives():
    """W6 guard: a raising utterance inside the REAL _speak_loop_once fires the
    error_system tone via _signal_speak_failure, releases the claim, and leaves
    the loop able to keep going. PERMANENT, like everything in this file."""
    sessions = SessionManager()
    sessions.set_foreground("fg")
    config = {k: (v.copy() if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
    config["verbosity"] = "everything"
    daemon = SpeechDaemon(None, sessions, config)
    speaker = _RaisingSpeaker()
    daemon.speaker = speaker
    daemon._enqueue("fg", "prose", "doomed", False)
    daemon._speak_loop_once()                      # must NOT propagate
    assert "error_system" in speaker.earcons
    assert daemon._current_item is None            # claim released, no wedge
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_failure_tones.py tests/test_concurrency_guards.py -q`
Expected: the new tests FAIL (earcons are `["error"]`; fallback plays nothing; `_DEFAULTS` lacks the kinds); the 3 existing guards still PASS.

- [ ] **Step 3: Implement**

`src/sonari/platform/macos/earcon.py` — `_DEFAULTS` gains (assets = the provisional owner-gated picks; config-level, swappable at his ear-pass):

```python
_DEFAULTS = {
    "permission": "/System/Library/Sounds/Funk.aiff",
    "choice":     "/System/Library/Sounds/Ping.aiff",
    "plan":       "/System/Library/Sounds/Submarine.aiff",
    "error":      "/System/Library/Sounds/Sosumi.aiff",
    "turn_done":  "/System/Library/Sounds/Tink.aiff",
    # W6 failure taxonomy (spec §7): distinct kinds, provisional assets —
    # the OWNER's ear-pass may swap these paths (config-level, no code change).
    "error_misdirected": "/System/Library/Sounds/Basso.aiff",  # "wrong door"
    "error_system":      "/System/Library/Sounds/Blow.aiff",   # "broke inside"
}
```

`src/sonari/speaker.py` — module-level dict + the `earcon()` fallback (only these lines change):

```python
# Failure/expiry earcon kinds added AFTER GA: bootstrap merges platform defaults
# only when the whole `earcons` config key is absent (bootstrap.py:73-74), so on
# an EXISTING install a new config-dict kind would be SILENTLY disabled — the
# worst eyes-free failure. These kinds therefore resolve config-first, then fall
# back to the built-in asset (the pitch() precedent, below). A config entry
# always wins, so the owner swaps assets without a code change. Old kinds keep
# today's silent-no-op semantics when unconfigured.
_FALLBACK_EARCONS = {
    "error_misdirected": "/System/Library/Sounds/Basso.aiff",
    "error_system": "/System/Library/Sounds/Blow.aiff",
}
```

```python
    def earcon(self, kind: str) -> None:
        if self._earcon_player is None:
            return
        # Reap any finished earcon processes before launching a new one.
        self._reap_earcon_procs()
        path = self._earcons.get(kind)
        if path is None:
            path = _FALLBACK_EARCONS.get(kind)   # never-silent NEW kinds only
        if path is None:
            return
        proc = self._earcon_player(path)
        if proc is not None and hasattr(proc, "poll"):
            self._earcon_procs.append(proc)
```

`src/sonari/daemon/features/decisions.py:188` — the pd-None branch in `on_answer_permission` (the `:183` invalid-behavior branch keeps `"error"`):

```python
    if pd is None:
        # W6 misdirected: valid intent, wrong session — go to the asking one.
        host.speaker.earcon("error_misdirected")
        return None
```

`src/sonari/daemon/host.py:431` — in `_signal_speak_failure`:

```python
        try:
            self.speaker.earcon("error_system")   # W6: "Sonari itself failed; content preserved unheard"
```

- [ ] **Step 4: Run tests + guards + suite**

Run: `.venv/bin/python -m pytest tests/test_failure_tones.py tests/test_concurrency_guards.py -q` → 6 + 4 pass (the guard file is now 4).
Full suite → **1013 passed, 1 skipped**. If an existing test pinned `earcons == ["error"]` on the pd-None ANSWER_PERMISSION branch or on a speak-loop failure, update those exact pins to the new kinds (the taxonomy IS the ratified change); any OTHER earcon pin must stay byte-identical — if one fails, STOP and reconcile with spec §7's call-site table.

- [ ] **Step 5: Commit**

`git commit -am "feat(wave1): W6 distinct failure tones with never-silent fallback for new kinds"`

---

## Task T6 — W7 permission expiry: earcon + queued-text cleanup

**Files:** Modify `src/sonari/daemon/host.py` (`_enqueue` returns id; `_await_permission_decision` timeout branch + new `_expire_permission`), `src/sonari/daemon/features/decisions.py` (`on_permission_request` stores `item_id`), `src/sonari/platform/macos/earcon.py` + `src/sonari/speaker.py` (`permission_expired` kind). Tests: `tests/test_permission_expiry.py` (new).

**Interfaces:** `_enqueue(...) -> int` (the new item's id; all existing callers ignore it — non-breaking). `_pending_decisions[session]` gains `"item_id"`. On timeout (`got == False`, pd still current): `permission_expired` earcon once + `remove_by_id` + marker drop, inside the EXISTING tail lock (no new lock ordering — the wait itself stays OUTSIDE the lock by design, `host.py:322-325`). Answered/superseded → neither. History NOT cleaned (the transcript is archaeology; only the QUEUE must not lie). *Depends on: T5 (the fallback dict exists).*

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_permission_expiry.py (new)
"""W7 (spec §8): a blocking permission that dies at the ~120s daemon wait must
be MARKED (earcon) and its still-queued text removed — a later ⌃⌘D/read must
never voice the dead ask as answerable. Answered/superseded asks are untouched."""
import threading

from sonari.protocol import PROTOCOL_VERSION
from tests.daemon_helpers import make_daemon, stream_queue


def _msg(t, session, **kw):
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


def _request(daemon, session="fg"):
    r = daemon.handle_message(_msg("permission_request", session,
                                   tool="Bash", summary="rm -rf build"))
    assert r == {"__await_decision__": True, "session": session}


def test_enqueue_returns_the_new_items_id():
    daemon, queue, speaker, sessions, config = make_daemon()
    rid = daemon._enqueue("fg", "prose", "hello.", False)
    assert rid == queue._items[-1].id


def test_timeout_plays_the_expiry_earcon_and_cleans_the_queue():
    daemon, queue, speaker, sessions, config = make_daemon()
    _request(daemon)
    item_id = daemon._pending_decisions["fg"]["item_id"]
    assert item_id == queue._items[-1].id          # the queued ask is tracked
    r = daemon._await_permission_decision("fg", timeout=0.01)
    assert r == {"decision": None}                 # fail-closed, unchanged
    assert speaker.earcons[-1] == "permission_expired"
    assert all(it.id != item_id for it in queue._items)   # dead ask removed
    assert item_id not in daemon._pending_heard            # marker dropped
    assert daemon.history.last_message("fg")               # history KEPT (archaeology)


def test_answered_ask_gets_no_expiry_and_no_cleanup():
    daemon, queue, speaker, sessions, config = make_daemon()
    _request(daemon)
    pd = daemon._pending_decisions["fg"]
    pd["behavior"] = "allow"
    pd["event"].set()
    r = daemon._await_permission_decision("fg", timeout=1.0)
    assert r == {"decision": "allow"}
    assert "permission_expired" not in speaker.earcons
    assert len(queue._items) == 1                  # the ask still queued (spoken later)


def test_superseded_ask_gets_no_expiry_and_the_newer_owns_the_slot():
    daemon, queue, speaker, sessions, config = make_daemon()
    _request(daemon)
    out = {}
    t = threading.Thread(
        target=lambda: out.update(r=daemon._await_permission_decision("fg", 5.0)))
    t.start()
    _request(daemon)                               # newer request releases the stale waiter
    t.join(5.0)
    assert not t.is_alive()
    assert out["r"] == {"decision": None}
    assert "permission_expired" not in speaker.earcons
    assert daemon._pending_decisions["fg"]["item_id"] == queue._items[-1].id


def test_in_flight_at_expiry_is_left_to_finish():
    daemon, queue, speaker, sessions, config = make_daemon()
    _request(daemon)
    item = queue.pop_next()                        # already popped: in flight
    daemon._current_item = item
    daemon._await_permission_decision("fg", timeout=0.01)
    assert speaker.earcons[-1] == "permission_expired"     # the honest context beside it
    assert item.id in daemon._pending_heard               # marker left for note_spoken
```

Also add to `tests/test_failure_tones.py`-style coverage here: assert the macOS default exists —

```python
def test_macos_defaults_gain_permission_expired():
    from sonari.platform.macos.earcon import _DEFAULTS
    assert _DEFAULTS["permission_expired"] == "/System/Library/Sounds/Purr.aiff"
```

(put it in `tests/test_permission_expiry.py`; 6 tests total in the module.)

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_permission_expiry.py -q`
Expected: FAIL — `_enqueue` returns None; `KeyError: 'item_id'`; no earcon on timeout.

- [ ] **Step 3: Implement**

`src/sonari/daemon/host.py` — `_enqueue` signature `-> int` and final line gains:

```python
        self._state._wake.set()
        return item.id
```

(Docstring note: "Returns the new item's id (W7: on_permission_request tracks its queued ask); all other callers ignore it.")

`decisions.py` — `on_permission_request`: capture the id, extend the literal:

```python
    item_id = host._enqueue(session, "permission", text, True, entry=entry)
    # We are under the daemon lock here, so mutate the store directly.
    prev = host._pending_decisions.get(session)
    if prev is not None:
        prev["event"].set()                  # release any stale waiter for this session
    host._pending_decisions[session] = {"event": threading.Event(), "behavior": None,
                                        "text": text, "item_id": item_id}
```

`host.py` — the wait's tail lock gains the expiry branch + the helper:

```python
    def _await_permission_decision(self, session: str, timeout: float) -> dict:
        """Block (OUTSIDE the daemon lock) until the focused-session answer arrives
        or the wait expires. Returns {"decision": "allow"|"deny"|None}; None means the
        hook falls through to Claude Code's normal terminal prompt (fail-closed)."""
        with self._lock:
            pd = self._pending_decisions.get(session)
        if pd is None:
            return {"decision": None}
        got = pd["event"].wait(timeout)
        with self._lock:
            behavior = pd["behavior"] if got else None
            # Pop only if still ours (a newer request for the same session may have replaced it).
            if self._pending_decisions.get(session) is pd:
                self._pending_decisions.pop(session, None)
                if not got:
                    # W7: the ask silently died at the wall. Mark it audibly and
                    # remove the now-unanswerable queued text so a later read/⌃⌘D
                    # never voices a dead ask as live. History is KEPT (transcript
                    # replay is explicit archaeology; only the QUEUE must not lie).
                    self._expire_permission(session, pd)
        return {"decision": behavior}

    def _expire_permission(self, session: str, pd: dict) -> None:
        """Timeout housekeeping for a dead blocking permission (W7). Caller holds
        self._lock — the cleanup takes the lock exactly as the existing pop does,
        no new lock ordering. The earcon is a fire-and-forget Popen. If the text
        is IN FLIGHT (already popped) remove_by_id misses: it finishes playing and
        the expiry earcon beside it is the honest context (accepted edge)."""
        try:
            self.speaker.earcon("permission_expired")
        except Exception:  # noqa: BLE001 - expiry signaling must never break the reply
            pass
        item_id = pd.get("item_id")
        if item_id is None:
            return
        st = self._state._streams.get(session)
        if st is None:
            return
        removed = st.queue.remove_by_id(item_id)
        if removed is not None:
            self._state._pending_heard.pop(item_id, None)
```

`src/sonari/platform/macos/earcon.py` — `_DEFAULTS` gains:

```python
    # W7 permission expiry (spec §8): provisional asset, owner's ear-pass swaps it.
    "permission_expired": "/System/Library/Sounds/Purr.aiff",  # "it slipped away"
```

`src/sonari/speaker.py` — `_FALLBACK_EARCONS` gains:

```python
    "permission_expired": "/System/Library/Sounds/Purr.aiff",
```

- [ ] **Step 4: Run tests + guards + suite**

Run: `.venv/bin/python -m pytest tests/test_permission_expiry.py tests/test_concurrency_guards.py -q` → 6 + 4 pass.
Full suite → **1019 passed, 1 skipped**.

- [ ] **Step 5: Commit**

`git commit -am "feat(wave1): W7 permission expiry earcon + queued-text cleanup on the daemon wait timeout"`

---

## Task T7 — W8 restart boot cue

**Files:** Modify `src/sonari/daemon/bootstrap.py` (`BOOT_CUE` constant + `_start_boot_cue()` + one call in `main()`). Tests: `tests/test_boot_cue.py` (new).

**Interfaces:** exactly `"Sonari restarted. Sessions re-register on their next prompt."`, spoken once via a one-shot daemon thread calling `speaker.speak()` DIRECTLY (the cue cannot ride the queue: no session is registered at boot — spec §9 mechanism), started immediately before `daemon.run()` (never blocks the socket bind that lazy-start clients poll). Plays on every boot including first-ever (accepted looseness; SP6 refines). *Depends on: nothing.*

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_boot_cue.py (new)
"""W8 (spec §9): the daemon announces its own restart. Direct one-shot speaker
thread — an enqueued cue would never voice (no registered sessions at boot; the
loop plays only speaker()'s stream and keep-going scans only registered ids)."""
import threading
import time

import sonari.daemon.bootstrap as bootstrap


def test_boot_cue_exact_string_spoken_once():
    spoken = []

    class _Spk:
        def speak(self, text=None, audio_path=None, cancel_epoch=None):
            spoken.append(text)
            return True

    bootstrap._start_boot_cue(_Spk())
    deadline = time.time() + 2.0
    while not spoken and time.time() < deadline:
        time.sleep(0.01)
    assert spoken == ["Sonari restarted. Sessions re-register on their next prompt."]


def test_boot_cue_start_is_non_blocking():
    release = threading.Event()

    class _Blocking:
        def speak(self, text=None, audio_path=None, cancel_epoch=None):
            release.wait(2.0)
            return True

    t0 = time.monotonic()
    bootstrap._start_boot_cue(_Blocking())
    assert time.monotonic() - t0 < 0.5             # returned while speak() still blocked
    release.set()


def test_main_wires_the_cue_before_run(monkeypatch):
    order = []
    monkeypatch.setattr(bootstrap, "_arm_faulthandler", lambda: None)
    monkeypatch.setattr(bootstrap, "socket_connectable", lambda: False)
    monkeypatch.setattr(bootstrap, "ensure_sonari_dir", lambda: None)
    monkeypatch.setattr(bootstrap.transport, "acquire_singleton", lambda p: object())
    monkeypatch.setattr(bootstrap, "load_config", lambda: {"earcons": {}})

    class _FakePlat:
        class tts:  # noqa: D106 - attribute container
            run = staticmethod(lambda *a, **k: None)

        class earcon:  # noqa: D106
            play = staticmethod(lambda *a, **k: None)
            default_earcons = staticmethod(lambda: {})

    monkeypatch.setattr("sonari.platform.get_platform", lambda: _FakePlat)

    class _FakeCache:
        def __init__(self, *a, **k):
            pass

        def cleanup(self):
            pass

    monkeypatch.setattr("sonari.spearcon.SpearconCache", _FakeCache)

    class _FakeDaemon:
        def __init__(self, *a, **k):
            pass

        def run(self):
            order.append("run")

    monkeypatch.setattr(bootstrap, "SpeechDaemon", _FakeDaemon)
    monkeypatch.setattr(bootstrap, "_start_boot_cue", lambda spk: order.append("cue"))

    bootstrap.main()
    assert order == ["cue", "run"]                 # cue armed, THEN the daemon serves
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_boot_cue.py -q`
Expected: FAIL — `AttributeError: module ... has no attribute '_start_boot_cue'`.

- [ ] **Step 3: Implement**

`src/sonari/daemon/bootstrap.py` — add `import threading` at the top; after `_arm_faulthandler`:

```python
# W8 (spec §9): spoken once per daemon boot, at every verbosity — a trust cue,
# not narration. Exported as a constant so the test imports the exact string.
BOOT_CUE = "Sonari restarted. Sessions re-register on their next prompt."


def _start_boot_cue(speaker) -> None:
    """Speak the restart trust cue on a one-shot daemon thread (W8). The cue
    CANNOT ride the queue: at boot no session is registered, the speak loop
    plays only speaker()'s stream and keep-going scans only registered sessions
    — an enqueued boot cue would never voice. A direct thread keeps the socket
    bind (which lazy-start clients poll) unblocked; the overlap window with the
    first real utterance is human-timescale-empty (sessions re-register on
    their next prompt). Never raises."""
    def _run() -> None:
        try:
            speaker.speak(BOOT_CUE)
        except Exception:  # noqa: BLE001 - the cue must never break startup
            pass

    threading.Thread(target=_run, daemon=True).start()
```

In `main()`, between `daemon = SpeechDaemon(...)` and `daemon.run()`:

```python
    daemon = SpeechDaemon(speaker, sessions, cfg, spearcons=spearcons)
    _start_boot_cue(speaker)          # W8: restart trust cue (pre-loop, pre-session)
    daemon.run()
```

- [ ] **Step 4: Run tests + guards + suite**

Run: `.venv/bin/python -m pytest tests/test_boot_cue.py tests/test_concurrency_guards.py -q` → 3 + 4 pass.
Full suite → **1022 passed, 1 skipped**.

- [ ] **Step 5: Commit**

`git commit -am "feat(wave1): W8 restart boot cue on a one-shot pre-run speaker thread"`

---

## Task T8 — W9 decision chime call-sign spearcon

**Files:** Modify `src/sonari/speaker.py` (`earcon_then` sequencer), `src/sonari/hooks_entry.py` (decision EARCON msgs gain `session=`), `src/sonari/daemon/features/prose.py` (`on_earcon` decision branch + comment sweep), `src/sonari/daemon/features/decisions.py` (blocking chime), `tests/daemon_helpers.py` (`FakeSpeaker.earcon_then`). Tests: `tests/test_decision_callsign.py` (new) + the hooks-entry module's pinned dicts.

**Interfaces:** `Speaker.earcon_then(kind, audio_path)` — chime proc, `wait()`, THEN spearcon, all on one fire-and-forget thread (earcons are overlapping Popens; sequencing needs the explicit wait — spec §16.2); never blocks the caller. EARCON protocol msgs for choice/plan/permission gain an OPTIONAL `session` field (absent → chime alone, backward/forward compatible). Spearcon miss → chime alone + background generation kick (self-heals). `turn_done` untouched. *Depends on: nothing.*

- [ ] **Step 1: Write the failing tests**

`tests/daemon_helpers.py` — extend `FakeSpeaker` FIRST (test infrastructure, committed with this task): add to `__init__`: `self.earcon_seqs: list = []` and the method:

```python
    def earcon_then(self, kind: str, audio_path) -> None:
        self.earcon_seqs.append((kind, audio_path))
```

```python
# tests/test_decision_callsign.py (new)
"""W9 (spec §10): the decision chime gains the ASKING session's spearcon —
sequenced (chime, then the ~200ms folder label), never overlapped. Sessionless
legacy messages and cache misses fall back to the chime alone, byte-identically."""
import threading

from sonari.protocol import PROTOCOL_VERSION
from sonari.speaker import Speaker
from tests.daemon_helpers import make_daemon


def _msg(t, session, **kw):
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


class _SeqProc:
    def __init__(self, log, path):
        self.log = log
        self.path = path

    def wait(self, timeout=None):
        self.log.append(("waited", self.path))

    def poll(self):
        return 0


def test_real_speaker_sequences_chime_then_spearcon():
    log = []
    done = threading.Event()

    def player(path):
        log.append(("play", path))
        if path == "/sp/backend.aiff":
            done.set()
        return _SeqProc(log, path)

    sp = Speaker(earcon_player=player,
                 earcons={"permission": "/snd/Funk.aiff"})
    sp.earcon_then("permission", "/sp/backend.aiff")
    assert done.wait(2.0), "sequencer thread never played the spearcon"
    assert log[0] == ("play", "/snd/Funk.aiff")
    assert log[1] == ("waited", "/snd/Funk.aiff")   # chime finished FIRST
    assert log[2] == ("play", "/sp/backend.aiff")


def test_blocking_permission_gains_the_asking_sessions_callsign():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.register("backend", cwd="/x/backend")
    daemon._spearcons.available["backend"] = "/sp/backend.aiff"
    daemon.handle_message(_msg("permission_request", "backend",
                               tool="Bash", summary="rm x"))
    assert speaker.earcon_seqs == [("permission", "/sp/backend.aiff")]
    assert speaker.earcons == []                   # sequenced, not the plain path


def test_spearcon_miss_falls_back_to_chime_alone_and_kicks_generation():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.register("backend", cwd="/x/backend")
    daemon.handle_message(_msg("permission_request", "backend",
                               tool="Bash", summary="rm x"))
    assert speaker.earcons == ["permission"]       # today's behavior, byte-identical
    assert speaker.earcon_seqs == []
    assert "backend" in daemon._spearcons.generated   # miss kicked background gen


def test_sessionless_legacy_earcon_is_chime_alone():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon.handle_message(_msg("earcon", "", kind="choice"))   # old hook version
    assert speaker.earcons == ["choice"]
    assert speaker.earcon_seqs == []


def test_session_carrying_earcon_gets_the_callsign():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.register("billing", cwd="/x/billing")
    daemon._spearcons.available["billing"] = "/sp/billing.aiff"
    daemon.handle_message(_msg("earcon", "billing", kind="choice"))
    assert speaker.earcon_seqs == [("choice", "/sp/billing.aiff")]
```

Hooks-entry tests — append to the hooks-entry test module (find it: `ls tests | grep -i hook`; imports inside the functions if the module lacks a helper):

```python
def test_decision_earcons_carry_the_session():
    from sonari.hooks_entry import handle_event
    msgs = handle_event("PreToolUse", {"session_id": "s1",
                                       "tool_name": "AskUserQuestion",
                                       "tool_input": {"questions": []}})
    assert msgs[0] == {"v": 1, "type": "earcon", "kind": "choice", "session": "s1"}
    msgs = handle_event("PreToolUse", {"session_id": "s1",
                                       "tool_name": "ExitPlanMode",
                                       "tool_input": {"plan": "p"}})
    assert msgs[0] == {"v": 1, "type": "earcon", "kind": "plan", "session": "s1"}
    msgs = handle_event("Notification", {"session_id": "s1",
                                         "notification_type": "permission_prompt"})
    assert msgs[0] == {"v": 1, "type": "earcon", "kind": "permission", "session": "s1"}
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_decision_callsign.py -q` plus the hooks-entry module.
Expected: FAIL — `Speaker` has no `earcon_then`; daemon plays plain earcons; hooks msgs lack `session`.

- [ ] **Step 3: Implement**

`src/sonari/speaker.py` — beside `earcon()` (threading already imported):

```python
    def earcon_then(self, kind: str, audio_path) -> None:
        """W9 call-sign sequencer: play the *kind* chime, THEN *audio_path* (the
        asking session's spearcon), strictly ordered on ONE fire-and-forget
        thread. Earcons are overlapping Popens by design, so ordering needs an
        explicit wait between the spawns; neither blocks the caller. A missing
        chime asset degrades to the spearcon alone (attribution wins). The
        thread reaps its own procs by waiting on them — nothing joins
        _earcon_procs. Not cancellable/barge-able, same as today's chimes."""
        if self._earcon_player is None:
            return
        if audio_path is None:
            self.earcon(kind)
            return
        chime = self._earcons.get(kind)
        if chime is None:
            chime = _FALLBACK_EARCONS.get(kind)
        player = self._earcon_player

        def _run() -> None:
            try:
                if chime:
                    p = player(chime)
                    if p is not None and hasattr(p, "wait"):
                        try:
                            p.wait(timeout=10)
                        except Exception:  # noqa: BLE001 - a hung chime must not kill the call-sign
                            pass
                p2 = player(audio_path)
                if p2 is not None and hasattr(p2, "wait"):
                    try:
                        p2.wait(timeout=10)
                    except Exception:  # noqa: BLE001
                        pass
            except Exception:  # noqa: BLE001 - fire-and-forget: never raise off-thread
                pass

        threading.Thread(target=_run, daemon=True).start()
```

`src/sonari/hooks_entry.py` — the three decision EARCON messages gain `session=session` (choice `:53`, plan `:62`, permission-notification `:78`):

```python
                _msg(type=MsgType.EARCON, kind="choice", session=session),
                # ...
                _msg(type=MsgType.EARCON, kind="plan", session=session),
                # ...
                _msg(type=MsgType.EARCON, kind="permission", session=session),
```

`src/sonari/daemon/features/prose.py` — `on_earcon` gains the decision branch (and the `:60-61` "SESSIONLESS" comment is updated to "may carry a session since W9; absent → chime alone"):

```python
@handler(MsgType.EARCON)
def on_earcon(ctx, msg):
    session = ctx.session
    kind = msg.get("kind", "")
    if kind == "turn_done":
        host = ctx.host
        # (existing turn_done suppression + flush block — UNCHANGED)
        if not (session == host.sessions.speaker() and host.voice_state == "flowing"):
            host.speaker.earcon(kind)
        host._flush_prose_buffer(session)
    elif kind in ("choice", "plan", "permission") and session:
        # W9 call-sign: the decision chime gains the ASKING session's spearcon,
        # sequenced (chime, then the ~200ms folder label). Legacy hooks send
        # these SESSIONLESS (session == "") -> chime alone, unchanged. A
        # spearcon miss -> chime alone; get() kicks background generation
        # (spearcon.py:76-83) — self-heals by next time.
        host = ctx.host
        sp = host._spearcon_path(host.sessions.folder(session))
        if sp is not None:
            host.speaker.earcon_then(kind, sp)
        else:
            host.speaker.earcon(kind)
    else:
        ctx.host.speaker.earcon(kind)
    return None
```

`src/sonari/daemon/features/decisions.py` — `on_permission_request`'s chime (`:164`, session known in-hand):

```python
    sp = host._spearcon_path(host.sessions.folder(session))
    if sp is not None:
        host.speaker.earcon_then("permission", sp)     # W9: chime + call-sign
    else:
        host.speaker.earcon("permission")
```

- [ ] **Step 4: Run tests + guards + suite**

Run: `.venv/bin/python -m pytest tests/test_decision_callsign.py tests/test_concurrency_guards.py -q` → 5 + 4 pass.
Full suite → **1028 passed, 1 skipped** (5 in the new module + 1 in the hooks module). Expected collateral: the hooks-entry module's EXACT-dict pins for the three decision earcons gain `"session"` — update ONLY those rows. Existing daemon tests that fired sessionless EARCONs are byte-identical (the new branch requires a session). If any other file fails, STOP and reconcile with spec §10.

- [ ] **Step 5: Commit**

`git commit -am "feat(wave1): W9 decision chimes gain the asking session's spearcon call-sign"`

---

## Task T9 — W10 backlog-depth (", {u} unheard") in the ⌃⌘W Also-map

**Files:** Modify `src/sonari/daemon/features/control.py` (`_also_clause`), `src/sonari/history.py` (`unheard()` docstring — first prod caller). Tests: `tests/test_also_map_unheard.py` (new).

**Interfaces:** per Also-map entry, `u = max(0, len(history.unheard(s)) - k)`; `u > 0` → `", {u} unheard"` appended AFTER the waiting clause. `k`/muted clauses unchanged. The count is a current-turn FLOOR (never overstates; the `-k` kills the queued-item double-count). No frontier read (SP5 border). OWNER GATE: the word "unheard" is his ear-pass call — keep it a single format string. *Depends on: nothing.*

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_also_map_unheard.py (new)
"""W10 (spec §11): under global quiet, prose never queues (prose.py:20) but IS
recorded — the Also-map's waiting count reads 0 for an hour-old pile. Surface
the recorded-but-not-queued floor as ', {u} unheard'. The -k subtraction kills
the double-count (queued items' history entries are also unheard until spoken)."""
from sonari.protocol import PROTOCOL_VERSION
from tests.daemon_helpers import make_daemon


def _msg(t, session, **kw):
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


def _where(daemon, speaker, session="fg"):
    daemon.handle_message(_msg("where_am_i", session))
    daemon._speak_loop_once()
    return speaker.spoken[-1]


def test_quiet_pile_surfaces_as_unheard():
    daemon, queue, speaker, sessions, config = make_daemon(verbosity="quiet")
    sessions.register("b", cwd="/x/b")
    for i in range(3):                             # recorded, never queued (the quiet gate)
        daemon.history.record("b", "prose", "line {0}.".format(i))
    assert "2 b, 3 unheard" in _where(daemon, speaker)


def test_queued_items_are_not_double_counted():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.register("b", cwd="/x/b")
    entry = daemon.history.record("b", "prose", "queued line.")
    daemon._enqueue("b", "prose", "queued line.", False, entry=entry)
    out = _where(daemon, speaker)
    assert "2 b, 1 waiting" in out
    assert "unheard" not in out                    # u = max(0, 1 - 1) = 0


def test_mixed_pile_orders_waiting_then_unheard():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.register("b", cwd="/x/b")
    entry = daemon.history.record("b", "prose", "queued line.")
    daemon._enqueue("b", "prose", "queued line.", False, entry=entry)
    daemon.history.record("b", "prose", "cut one.")
    daemon.history.record("b", "prose", "cut two.")
    assert "2 b, 1 waiting, 2 unheard" in _where(daemon, speaker)


def test_fully_heard_sessions_show_no_unheard_clause():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.register("b", cwd="/x/b")
    entry = daemon.history.record("b", "prose", "b line.")
    daemon._enqueue("b", "prose", "b line.", False, entry=entry)
    sessions.set_speaker("b")
    daemon._speak_loop_once()                      # spoken to completion -> heard
    sessions.set_speaker("fg")
    assert "unheard" not in _where(daemon, speaker)


def test_genuine_preemption_pile_appears_even_non_quiet():
    # A ⌃⌘J/⌃⌘D preemption-cut leaves the cut item recorded, unheard, un-queued
    # (host.py:535 re-queues only when the OWN stream is stopped). Surfacing it
    # is CORRECT (spec §11 correction): a genuine pile, not a leak.
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.register("b", cwd="/x/b")
    daemon.history.record("b", "prose", "cut mid-sentence.")
    assert "2 b, 1 unheard" in _where(daemon, speaker)
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_also_map_unheard.py -q`
Expected: FAIL — no "unheard" clause is ever produced (tests 2 and 4 pass; 1, 3, 5 fail).

- [ ] **Step 3: Implement**

`src/sonari/daemon/features/control.py` — in `_also_clause`'s per-entry loop, after the waiting clause:

```python
        k = len(st.queue) if st is not None else 0
        if k > 0:
            seg += ", {0} waiting".format(k)
        # W10: the recorded-but-not-queued unheard FLOOR. Queued items' history
        # entries are ALSO unheard until spoken (host.py:309-320 flips heard on
        # completion), so a raw len(unheard) double-counts every queued item —
        # subtract k (approximation in the caller's favor: never overstates).
        # unheard() is current-turn-bounded: the spoken count is a floor across
        # a multi-turn pile (documented; frontier counts are SP5, NOT built).
        # The word "unheard" is an OWNER GATE (his ear tunes it at review).
        u = max(0, len(host.history.unheard(s)) - k)
        if u > 0:
            seg += ", {0} unheard".format(u)
        parts.append(seg)
```

`src/sonari/history.py` — amend `unheard()`'s docstring (behavior unchanged):

```python
    def unheard(self, session: str) -> list:
        """Not-yet-heard entries of the CURRENT turn only, oldest first.

        §7 (Stage 4): the transcript persists across turns, but `unheard` stays
        bounded to the live turn. Its first production consumer is the ⌃⌘W
        Also-map's ", {u} unheard" clause (W10), which reads it as a CURRENT-
        TURN FLOOR — the turn-bounding is now load-bearing: the spoken count
        may understate a multi-turn pile, never overstate it. Heard-marking
        still flips entries from the speak loop regardless of turn."""
```

- [ ] **Step 4: Run tests + guards + suite**

Run: `.venv/bin/python -m pytest tests/test_also_map_unheard.py tests/test_concurrency_guards.py -q` → 5 + 4 pass.
Full suite → **1033 passed, 1 skipped**. Existing ⌃⌘W string pins stay byte-identical in the ordinary case (`u == 0` whenever everything recorded is also queued); if one fails, it constructed a recorded-not-queued entry — reconcile against spec §11's correction before touching it.

- [ ] **Step 5: Commit**

`git commit -am "feat(wave1): W10 unheard-floor clause in the where-am-i Also-map"`

---

## Task T10 — W11 two-pointer collapse (⌃⌘J + confirmations → `workspace()`)

**Files:** Modify `src/sonari/daemon/features/focus.py` (`:44-45`, `:54`), `src/sonari/daemon/features/control.py` (`:89-91`), `src/sonari/daemon/features/chooser.py` (`:48` dead-code cleanup). Tests: `tests/test_pointer_collapse.py` (new) + updates in `tests/test_daemon_focus_nav.py` and `tests/test_cli_focus_follow.py`.

**Interfaces:** ⌃⌘J excludes `workspace()` (not `foreground()`); empty-cue fallback `speaker() or workspace()`; `"Rate {n}."` targets `workspace()`. Degenerate no-OS-focus case is byte-identical (`workspace()` falls back to `foreground()`, `sessions.py:132`). `foreground()` survives internally (STATUS, CLI STOP, REREAD — unchanged). R12 preserved: these surfaces READ `workspace()`, nothing new writes it. **Production behavior change — owner-ratified (Block 1).** *Depends on: T1 (the at_front flag on the empty cue is preserved through this edit).*

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_pointer_collapse.py (new)
"""W11 (spec §12, Block-1 ratified): the hands act on three pointers but ⌃⌘W
teaches two — the nameless third (foreground) stops being a gesture target.
Felt only under live focus divergence; without an OS-focus signal every
retargeted surface behaves byte-identically to today."""
from sonari.protocol import PROTOCOL_VERSION
from sonari.sessions import Identity
from tests.daemon_helpers import make_daemon, stream_queue


def _msg(t, session, **kw):
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


def _liveness(monkeypatch, dead=()):
    from sonari import ttyutil
    monkeypatch.setattr(ttyutil, "tty_alive", lambda tty: tty not in dead)


def _focus_on(sessions, sid, tty):
    sessions.set_identity(sid, Identity(term_program="Apple_Terminal", tty=tty))
    sessions.set_os_focus(term_program="Apple_Terminal", tty=tty)


def test_jump_excludes_the_workspace_not_the_stale_foreground(monkeypatch):
    _liveness(monkeypatch)
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.register("C", cwd="/x/C")
    _focus_on(sessions, "C", "/dev/ttysC")         # you clicked C: workspace=C
    sessions.set_speaker("B")                      # keep-going drifted the voice
    daemon._enqueue("A", "prose", "a waits.", False)
    daemon._enqueue("C", "prose", "c waits.", False)
    daemon.handle_message(_msg("jump_waiting", "A"))
    # Old exclude was foreground()=A -> ⌃⌘J could "jump" to C, the terminal
    # already in front of you. New: C (workspace) is excluded, A is reachable.
    assert sessions.speaker() == "A"


def test_empty_cue_routes_to_the_workspace_when_no_speaker(monkeypatch):
    _liveness(monkeypatch)
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.register("C", cwd="/x/C")
    _focus_on(sessions, "C", "/dev/ttysC")
    sessions.set_speaker(None)                     # loop idle
    daemon.handle_message(_msg("jump_waiting", "A"))
    assert [it.text for it in stream_queue(daemon, "C")._items] == ["No session waiting."]
    assert len(stream_queue(daemon, "A")._items) == 0


def test_rate_confirmation_lands_on_the_workspace(monkeypatch):
    from sonari.daemon.features import control
    monkeypatch.setattr(control, "save_config", lambda cfg: None)
    _liveness(monkeypatch)
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.register("C", cwd="/x/C")
    _focus_on(sessions, "C", "/dev/ttysC")
    daemon.handle_message(_msg("set_rate", "A", delta=25))
    assert any("Rate " in it.text for it in stream_queue(daemon, "C")._items)
    assert len(stream_queue(daemon, "A")._items) == 0


def test_degenerate_no_focus_case_is_byte_identical(monkeypatch):
    from sonari.daemon.features import control
    monkeypatch.setattr(control, "save_config", lambda cfg: None)
    daemon, queue, speaker, sessions, config = make_daemon()   # no OS focus at all
    daemon.handle_message(_msg("set_rate", "fg", delta=25))
    assert any("Rate " in it.text for it in queue._items)      # falls back to foreground
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_pointer_collapse.py -q`
Expected: tests 1–3 FAIL (foreground-targeting today); test 4 passes (the degenerate identity).

- [ ] **Step 3: Implement**

`src/sonari/daemon/features/focus.py` — `on_jump_waiting`'s head becomes (comment updated; `at_front=True` from T1 kept):

```python
@handler(MsgType.JUMP_WAITING)
def on_jump_waiting(ctx, msg):
    # W11 collapse: the exclusion is the WORKSPACE (front terminal + keyboard),
    # not the internal foreground pointer. ⌃⌘J never jumps to the session your
    # keyboard is on (workspace()) nor the one you're hearing (speaker(), already
    # excluded in _waiting_target); every other waiting session is eligible.
    ws = ctx.host.sessions.workspace()
    target = _waiting_target(ctx, exclude=ws)
    if target is None:
        # Nothing waiting: say so. Route to speaker() so the cue lands in the
        # stream the speak loop is already reading. When speaker() is None (loop
        # idle), workspace() is where you are — enqueue there so it isn't lost
        # (W11: foreground() is no longer a gesture target). If both are None,
        # fall back to an error earcon.
        tgt = ctx.host.sessions.speaker() or ws
        if tgt is not None:
            ctx.host._enqueue(tgt, "prose", "No session waiting.", False,
                              mute_exempt=True, pause_exempt=True, at_front=True)
        else:
            ctx.host.speaker.earcon("error")
        return None
    # (rest of the handler unchanged)
```

`src/sonari/daemon/features/control.py` — `on_set_rate`'s delta confirmation:

```python
    if is_delta:
        # W11: the terminal you're at hears its own confirmation ("Rate 250."
        # used to land on foreground() — a session you may not be hearing).
        ws = ctx.host.sessions.workspace()
        if ws is not None:
            ctx.host._enqueue(ws, "prose", "Rate {0}.".format(rate), False)
```

`src/sonari/daemon/features/chooser.py:48` — the optional dead-code cleanup (provably dead: `workspace()` falsy ⇒ `foreground()` is None too, `sessions.py:121-136`; zero behavior change, makes "no ⌃⌘ gesture's observable behavior depends on foreground()" literally true):

```python
    origin = sessions.workspace()
```

- [ ] **Step 4: Run the suite; update the two foreground-pinned files**

Run: `.venv/bin/python -m pytest -q`.
Expected failures confined to `tests/test_daemon_focus_nav.py` and `tests/test_cli_focus_follow.py` (the spec's named foreground-pinned files) — update ONLY assertions that pin the old `foreground()` exclude/routing to the new `workspace()` oracle, preserving each test's intent (the divergence behavior IS the ratified change; most tests never set OS focus and are byte-identical). **If a failure appears in ANY other file, STOP and reconcile with spec §12 before proceeding.**
Then: full suite green → **1037 passed, 1 skipped**; guards green.

- [ ] **Step 5: Commit**

`git commit -am "feat(wave1): W11 two-pointer collapse — jump exclusion and confirmations target the workspace"`

---

## Task T11 — W12 repeat-last (⌃⌘R) — OWN task: speak-loop capture + guard hammer

**Files:** Modify `src/sonari/protocol.py`, `src/sonari/daemon/__init__.py`, `src/sonari/daemon/state.py`, `src/sonari/daemon/host.py` (capture + shim), `src/sonari/daemon/features/playback.py` (handler), `src/sonari/keymap.py`. Tests: `tests/test_repeat_last.py` (new) + rows in `tests/test_protocol.py`, `tests/test_daemon_registry.py`, `tests/test_keymap.py` + the hammer-ops extension in `tests/test_concurrency_guards.py`. **ONE commit** (the registry completeness guard makes a split an import-time error).

**Interfaces:** `MsgType.REPEAT_LAST = "repeat_last"`; keymap action `repeat_last` → **⌃⌘R** (locked). The daemon tracks the last COMPLETED non-`mute_exempt` utterance as `(spoken_text, audio_path)` — text AS SPOKEN (`_attributed_text` output, folder prefix included). Repeat = the ⌃⌘W discipline verbatim (capture in-flight + entry, cancel, park interrupted `at_front` FIRST, then the repeat `at_front, mute_exempt, pause_exempt`), routed `speaker()`, None-speaker → playable-workspace else error earcon. Fresh boot → exactly `"Nothing to repeat."`. Idempotent (the repeat is `mute_exempt`, never becomes the new target).

**The M1 lock shape, spelled out:** the capture is ONE assignment added inside the EXISTING tail lock of `_speak_loop_once` (`host.py:529-541`, the block that already does the L2 re-queue re-check) — as an `elif` off the existing `if not completed and ...`. No new locked region, no gap, no reordering; the pop+claim+speak+note_spoken core is untouched. The held branch is NOT a capture site (it plays only pause-exempt control cues, which are `mute_exempt`). **REPEAT_LAST joins the guard hammer set** (campaign :14).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_repeat_last.py (new)
"""W12 (spec §13): "say that again" — the single most frequent by-ear need.
Captures the last COMPLETED non-mute_exempt utterance AS SPOKEN (prefix
included); replays it with the ⌃⌘W capture-park-resume discipline; idempotent."""
from sonari.protocol import PROTOCOL_VERSION
from sonari.queue import SpeechItem
from tests.daemon_helpers import make_daemon, stream_queue


def _msg(t, session, **kw):
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


def test_repeat_speaks_the_last_content_verbatim_including_prefix():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.register("b", cwd="/x/b")
    daemon._enqueue("fg", "prose", "first thing.", False)
    daemon._speak_loop_once()
    sessions.set_speaker("b")
    daemon._enqueue("b", "prose", "second thing.", False)
    daemon._speak_loop_once()
    assert speaker.spoken[-1] == "b. second thing."    # prefixed: the voice switched
    daemon.handle_message(_msg("repeat_last", "b"))
    daemon._speak_loop_once()
    assert speaker.spoken[-1] == "b. second thing."    # verbatim = what your ear got


def test_repeat_is_idempotent_across_presses():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon._enqueue("fg", "prose", "content.", False)
    daemon._speak_loop_once()
    for _ in range(3):
        daemon.handle_message(_msg("repeat_last", "fg"))
        daemon._speak_loop_once()
    assert speaker.spoken[-3:] == ["content."] * 3     # the repeat never becomes the target


def test_control_cues_are_never_the_repeat_target():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon._enqueue("fg", "prose", "content.", False)
    daemon._speak_loop_once()
    daemon.handle_message(_msg("where_am_i", "fg"))
    daemon._speak_loop_once()                          # the ⌃⌘W readout (mute_exempt)
    daemon.handle_message(_msg("repeat_last", "fg"))
    daemon._speak_loop_once()
    assert speaker.spoken[-1] == "content."            # chrome excluded


def test_interrupted_utterance_is_not_captured():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon._enqueue("fg", "prose", "content.", False)
    daemon._speak_loop_once()                          # captured
    speaker.complete = False
    daemon._enqueue("fg", "prose", "cut off.", False)
    daemon._speak_loop_once()                          # NOT completed -> not captured
    speaker.complete = True
    daemon.handle_message(_msg("repeat_last", "fg"))
    daemon._speak_loop_once()
    assert speaker.spoken[-1] == "content."


def test_repeat_parks_and_resumes_the_interrupted_item():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon._enqueue("fg", "prose", "earlier content.", False)
    daemon._speak_loop_once()                          # captured as last utterance
    inflight = SpeechItem(id=999, session="fg", kind="prose",
                          text="interrupted.", is_decision=False)
    daemon._current_item = inflight                    # simulate mid-utterance
    daemon.handle_message(_msg("repeat_last", "fg"))
    assert speaker.cancels == 1                        # barge-in
    texts = [it.text for it in queue._items]
    assert texts[0] == "earlier content."              # the repeat leads
    assert texts[1] == "interrupted."                  # parked DEEPER — resumes after


def test_nothing_to_repeat_is_a_spoken_cue_not_an_error():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon.handle_message(_msg("repeat_last", "fg"))
    daemon._speak_loop_once()
    assert speaker.spoken[-1] == "Nothing to repeat."
    assert speaker.earcons == []                       # an empty repeat is not a mis-press


def test_no_speaker_routes_to_a_playable_workspace():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon._enqueue("fg", "prose", "content.", False)
    daemon._speak_loop_once()
    sessions.set_speaker(None)                         # voice released
    daemon.handle_message(_msg("repeat_last", "fg"))
    assert [it.text for it in queue._items] == ["content."]   # fg = playable workspace


def test_no_speaker_and_muted_workspace_plays_error_tone():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon._enqueue("fg", "prose", "content.", False)
    daemon._speak_loop_once()
    sessions.set_speaker(None)
    daemon._stream("fg").stopped = True
    daemon.handle_message(_msg("repeat_last", "fg"))
    assert speaker.earcons == ["error"]                # mirror ⌃⌘W's None-speaker branch
```

Registration/keymap rows (existing modules):
- `tests/test_protocol.py`: add `"REPEAT_LAST": "repeat_last"` to BOTH exact dicts (the `:52` constants test and the `:94` no-extras test).
- `tests/test_daemon_registry.py`: append `_MsgType.REPEAT_LAST` to `ALL_TYPES` (comment "all 35" → "all 36").
- `tests/test_keymap.py`: add `"repeat_last"` to every default-action enumeration (the tests at `:50-63`, `:88-96`, `:98-103` assert the exact default set) and append:

```python
def test_repeat_last_action_message_and_default_key(mac):
    assert keymap.ACTION_MESSAGES["repeat_last"] == {"type": "repeat_last"}
    d = keymap.default_keymap()
    assert d["repeat_last"]["key"] == "r"              # ⌃⌘R (owner-locked)
```

(match the module's existing `mac` fixture usage at `:50`.)

- `tests/test_concurrency_guards.py`: the hammer ops list (`:242-244`) gains `MsgType.REPEAT_LAST` (append to the rotation; comment: "REPEAT_LAST (W12) hammers the capture+park path against the loop's tail-lock write"). Assertions untouched.

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_repeat_last.py tests/test_protocol.py tests/test_daemon_registry.py -q`
Expected: FAIL — no `REPEAT_LAST` constant / handler / capture.

- [ ] **Step 3: Implement (all pieces, one commit)**

`src/sonari/protocol.py` — after `CHOOSER_CANCEL`:

```python
    REPEAT_LAST = "repeat_last"         # ⌃⌘R: re-speak the last completed content utterance
```

`src/sonari/daemon/__init__.py` — `MsgType.REPEAT_LAST,` appended to `assert_complete` (comment: 36 known keys).

`src/sonari/daemon/state.py` — after `self._last_spoken_session = None`:

```python
        # W12 repeat-last: the last COMPLETED non-mute_exempt utterance as
        # (spoken_text, audio_path) — text AS SPOKEN (_attributed_text output,
        # folder prefix included: verbatim = what the ear got). Written by the
        # speak loop under the tail lock; read by the REPEAT_LAST handler under
        # the same lock (the handler transaction). None until first capture.
        self._last_utterance = None
```

`src/sonari/daemon/host.py` — property shim beside `_last_spoken_session`:

```python
    @property
    def _last_utterance(self):
        return self._state._last_utterance

    @_last_utterance.setter
    def _last_utterance(self, value):
        self._state._last_utterance = value
```

`host.py` `_speak_loop_once` — the tail lock gains the capture as an `elif` (NO other change to the block):

```python
        requeued = False
        with self._lock:
            # Re-check INSIDE the lock (L2). ... (existing comment unchanged)
            if not completed and self._stream(item.session).stopped:
                self._state._current_item = None
                self._stream(item.session).queue.enqueue_front(item)
                self._state._last_spoken_session = prev
                requeued = True
            elif completed and not item.mute_exempt:
                # W12 capture: the last COMPLETED content utterance, AS SPOKEN
                # (attributed text, prefix included). mute_exempt chrome (⌃⌘W
                # readouts, jump cues, the repeat playback itself) is excluded —
                # which also makes repeat idempotent. One assignment under the
                # EXISTING tail lock: no new locked region, no gap (M1).
                self._state._last_utterance = (text, item.audio_path)
        if not requeued:
            self.note_spoken(item, completed)
```

`src/sonari/daemon/features/playback.py` — the handler (mirrors ⌃⌘W's discipline `control.py:222-254` and its None-speaker branch `control.py:193-221`):

```python
@handler(MsgType.REPEAT_LAST)
def on_repeat_last(ctx, msg):
    # ⌃⌘R (W12): re-speak the last completed content utterance, verbatim as
    # spoken. Barge-in-class with the ⌃⌘W capture-park-resume discipline: the
    # interrupted item is re-queued at_front FIRST (ends up deepest), then the
    # repeat at_front — so the ear hears repeat, then the resumed utterance.
    # Fork-2: enqueues to the speaker / a playable workspace, never un-stops.
    host = ctx.host
    sessions = host.sessions
    tgt = sessions.speaker()
    if tgt is None:
        # Mirror ⌃⌘W's None-speaker branch: a playable workspace stream can be
        # adopted by keep-going; a muted/None workspace has nothing voiceable.
        ws = sessions.workspace()
        ws_st = host._streams.get(ws) if ws is not None else None
        playable = ws is not None and not (ws_st is not None and ws_st.stopped)
        if not playable:
            host.speaker.earcon("error")
            return None
        tgt = ws
    last = host._last_utterance
    if last is None:
        # Spoken cue, not an error tone — an empty repeat is not a mis-press.
        host._enqueue(tgt, "prose", "Nothing to repeat.", False,
                      mute_exempt=True, pause_exempt=True, at_front=True)
        return None
    text, audio_path = last
    cur = host._current_item
    entry = host._pending_heard.get(cur.id) if cur is not None else None
    host.speaker.cancel()                          # barge-in: cut the current utterance
    if cur is not None:
        host._enqueue(cur.session, cur.kind, cur.text, cur.is_decision,
                      entry=entry, mute_exempt=cur.mute_exempt,
                      pause_exempt=cur.pause_exempt, names_session=cur.names_session,
                      audio_path=cur.audio_path, at_front=True)
    # The repeat is mute_exempt: never re-captured (idempotence), never prefixed
    # (the captured text already carries any prefix). A spearcon-only last
    # utterance replays its audio file (audio_path passthrough).
    host._enqueue(tgt, "prose", text, False, mute_exempt=True,
                  pause_exempt=True, at_front=True, audio_path=audio_path)
    return None
```

`src/sonari/keymap.py` — `ACTION_MESSAGES` gains (after `jump_decision`):

```python
    "repeat_last": {"type": "repeat_last"},   # ⌃⌘R: re-speak the last content utterance
```

and `_DEFAULT_KEYS` gains `"repeat_last": "r",` (R is present-but-unbound in the keytables; no Swift change — hotkeyd registers whatever the resolved keymap says; the binding goes live at the owner's next `sonari install`/keymap reload).

- [ ] **Step 4: Run tests + guards + suite**

Run: `.venv/bin/python -m pytest tests/test_repeat_last.py tests/test_protocol.py tests/test_daemon_registry.py tests/test_keymap.py tests/test_concurrency_guards.py -q` → all pass (guards: 4, with REPEAT_LAST in the storm rotation).
Full suite → **1046 passed, 1 skipped** (8 in the new module + 1 new keymap test).

- [ ] **Step 5: Commit (ONE commit — constant + handler + registry + keymap + hammer together)**

`git commit -am "feat(wave1): W12 repeat-last verb on ctrl-cmd-R with speak-loop capture; joins the guard hammer set"`

---

## Task T12 — W13 keep-going pre-roll spearcon — OWN task: inside the M1 lock + guard hammer

**Files:** Modify `src/sonari/daemon/host.py` (the keep-going branch of `_speak_loop_once`, `:485-513`). Tests: `tests/test_keepgoing_preroll.py` (new) + the stress-guard arming in `tests/test_concurrency_guards.py`.

**Interfaces:** GIVEN keep-going advances A→B and B's spearcon is cached: the ear hears B's ~200ms folder spearcon, THEN B's first content sentence WITHOUT the spliced prefix (the spearcon claims attribution via `names_session`, exactly like a deliberate jump). Miss → today's splice byte-identically + background generation kick. Selection UNCHANGED (`_select_keep_going` byte-identical, anchor 7). Fork-2: no un-mute. R12: no pointer moves.

**The M1 lock shape, spelled out (plan-author decision 1 — the spec's `enqueue_front` alternative):** inside the EXISTING single locked block, after `set_speaker(next_sess)`: resolve `_spearcon_path(folder)` (verified never-blocking: a cache-path stat + non-blocking Popen kick, `spearcon.py:76-83`); on a hit, synthesize the spearcon `SpeechItem` (`mute_exempt=True, names_session=True, audio_path=...`) and `enqueue_front` it, then the existing `pop_next()` claims IT — the content item stays queued (popped normally next iteration, now attribution-claimed). Nothing leaves the lock; scan+select+set_speaker+pop+claim is still ONE block; no content pop is skipped-and-lost — the QUEUE, not a local, holds the content across iterations, so FLUSH/STOP semantics are inherited for free (an intervening FLUSH clears the content item exactly as it would any queued item). **The keep-going-with-spearcon scenario joins the guard hammer set.**

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_keepgoing_preroll.py (new)
"""W13 (spec §14, Block-1 ratified): the most frequent voice switch carries the
thinnest cue. Keep-going now pre-rolls the new speaker's folder spearcon —
delivery only; selection is byte-identical (anchor 7); all inside the M1 lock."""
from sonari.protocol import PROTOCOL_VERSION
from tests.daemon_helpers import make_daemon, stream_queue


def _msg(t, session, **kw):
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


def _prime(daemon):
    """Speak one fg item so _last_spoken_session is set (the first-utterance
    rule would otherwise suppress the splice the miss test asserts)."""
    daemon._enqueue("fg", "prose", "fg content.", False)
    daemon._speak_loop_once()


def test_hit_plays_the_spearcon_then_unprefixed_content():
    daemon, queue, speaker, sessions, config = make_daemon()
    _prime(daemon)
    sessions.register("bg", cwd="/x/bg")
    daemon._spearcons.available["bg"] = "/sp/bg.aiff"
    daemon._enqueue("bg", "prose", "bg content.", False)
    daemon._speak_loop_once()                      # keep-going claims the PRE-ROLL
    assert speaker.audio_paths[-1] == "/sp/bg.aiff"
    assert sessions.speaker() == "bg"
    daemon._speak_loop_once()                      # then the content, attribution claimed
    assert speaker.spoken[-1] == "bg content."     # NO spliced folder prefix
    assert speaker.audio_paths[-1] is None


def test_miss_keeps_todays_splice_byte_identically_and_kicks_generation():
    daemon, queue, speaker, sessions, config = make_daemon()
    _prime(daemon)
    sessions.register("bg", cwd="/x/bg")           # no cached spearcon
    daemon._enqueue("bg", "prose", "bg content.", False)
    daemon._speak_loop_once()
    assert speaker.spoken[-1] == "bg. bg content."   # the splice, unchanged
    assert speaker.audio_paths[-1] is None
    assert "bg" in daemon._spearcons.generated       # self-heals by next time


def test_selection_is_byte_identical_cache_state_never_biases_it():
    daemon, queue, speaker, sessions, config = make_daemon()
    _prime(daemon)
    sessions.register("older", cwd="/x/older")
    sessions.register("newer", cwd="/x/newer")
    daemon._enqueue("older", "prose", "older content.", False)
    daemon._enqueue("newer", "prose", "newer content.", False)
    daemon._spearcons.available["newer"] = "/sp/newer.aiff"   # only the LOSER is cached
    daemon._speak_loop_once()
    assert sessions.speaker() == "older"           # longest-waiting-first, unchanged


def test_preroll_never_unmutes_and_never_selects_a_stopped_stream():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.register("bg", cwd="/x/bg")
    daemon._spearcons.available["bg"] = "/sp/bg.aiff"
    daemon._enqueue("bg", "prose", "bg content.", False)
    daemon._stream("bg").stopped = True            # Fork-2: muted stays muted
    daemon._speak_loop_once()
    assert sessions.speaker() == "fg"              # selector skipped it (unchanged)
    assert daemon._stream("bg").stopped is True


def test_preroll_moves_no_pointer():
    daemon, queue, speaker, sessions, config = make_daemon()
    _prime(daemon)
    sessions.register("bg", cwd="/x/bg")
    daemon._spearcons.available["bg"] = "/sp/bg.aiff"
    daemon._enqueue("bg", "prose", "bg content.", False)
    daemon._speak_loop_once()
    assert sessions.foreground() == "fg"           # R12: the workspace never moves on its own


def test_flush_mid_preroll_loses_nothing_and_leaves_no_orphan():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.register("bg", cwd="/x/bg")
    daemon._spearcons.available["bg"] = "/sp/bg.aiff"

    class _Entry:
        heard = False

    daemon._enqueue("bg", "prose", "bg content.", False, entry=_Entry())

    class _Reentrant:
        """FLUSH(bg) lands DURING the pre-roll spearcon's playback — the queued
        content item must be cleared exactly like any queued item (inherited
        FLUSH semantics), with no orphaned marker and no resurrection."""
        def __init__(self):
            self._epoch = 0
            self.fired = False

        def speak(self, text=None, audio_path=None, cancel_epoch=None):
            if not self.fired:
                self.fired = True
                daemon.handle_message(_msg("flush", "bg"))
            return False

        def cancel_epoch(self):
            return self._epoch

        def cancel(self):
            self._epoch += 1

        def earcon(self, kind):
            pass

    daemon.speaker = _Reentrant()
    daemon._speak_loop_once()                      # pre-roll claimed; FLUSH races it
    assert daemon._current_item is None            # claim released
    assert len(stream_queue(daemon, "bg")._items) == 0   # content flushed, NOT resurrected
    assert daemon._pending_heard == {}             # no orphaned marker
```

`tests/test_concurrency_guards.py` — arm the stress daemon (ADDITIVE; existing assertions untouched):

1. Imports: `from tests.daemon_helpers import FakeSpearconCache` at the top.
2. A fast afplay helper beside `_FastRunner`:

```python
def _fast_afplay(path):
    """afplay runner for pre-roll spearcon items: completes immediately, like
    _FastRunner's say procs, so the storm churns through audio items too."""
    p = _SlowProc()
    p.finish(0)
    return p
```

3. `_make_real_daemon` arms the pre-roll (Speaker gains the afplay runner; every folder resolves a fake cached spearcon):

```python
def _make_real_daemon(runner, foreground="s0"):
    speaker = Speaker(say_runner=runner, afplay_runner=_fast_afplay)
    sessions = SessionManager()
    sessions.set_foreground(foreground)
    config = {k: (v.copy() if isinstance(v, dict) else v)
              for k, v in DEFAULTS.items()}
    config["verbosity"] = "everything"
    # W13: arm the keep-going pre-roll under the storm — every folder resolves a
    # (fake) cached spearcon, so each genuine keep-going fire synthesizes and
    # claims an audio item INSIDE the locked block, hammering the new path.
    spearcons = FakeSpearconCache()
    for s in ("s0", "s1", "s2", "s_bg"):
        spearcons.available[s] = "/x/fake-spearcon.aiff"
    daemon = SpeechDaemon(speaker, sessions, config, spearcons=spearcons)
    return daemon, speaker
```

4. One ADDITIVE assertion in the stress test, directly after the `real_keep_going_fires` assertion:

```python
        # W13 (additive): with every folder's spearcon armed above, each genuine
        # keep-going fire ran the pre-roll resolution inside the locked block
        # (set_speaker -> folder -> _spearcon_path -> cache.get). Non-zero here
        # proves the NEW path was exercised by this storm, not merely compiled.
        assert len(daemon._spearcons.requested) > 0, \
            "keep-going fired but the pre-roll spearcon path never resolved a label"
```

(The two deterministic guards construct `SpeechDaemon(None, ...)` with `spearcons=None` — the pre-roll is a no-op there; they stay byte-identical.)

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_keepgoing_preroll.py -q`
Expected: FAIL — the hit test gets the splice (`"bg. bg content."`) instead of the spearcon; tests 3–5 pass (they pin MUST-NOTs); the FLUSH test passes vacuously pre-change (content popped immediately) — it gains its teeth with the pre-roll.

- [ ] **Step 3: Implement**

`src/sonari/daemon/host.py` — the keep-going branch (ONLY the marked lines are new; `_select_keep_going` is byte-identical):

```python
                next_sess = _select_keep_going(self._state._streams, self.sessions)
                if next_sess is not None:
                    self.sessions.set_speaker(next_sess)
                    st = self._state._streams.get(next_sess)
                    # W13 PRE-ROLL (inside this SAME locked block — M1): the most
                    # frequent voice switch gets the same spearcon cue as a
                    # deliberate jump. On a cache hit, synthesize the ~200ms
                    # folder spearcon, enqueue_front it, and let the pop below
                    # claim IT — the content item stays queued (popped next
                    # iteration, attribution claimed via names_session, so
                    # _attributed_text no longer splices the folder prefix).
                    # The QUEUE, not a local, carries the content across
                    # iterations: FLUSH/STOP semantics are inherited for free.
                    # Miss -> today's splice byte-identically; _spearcon_path
                    # never blocks (a cache stat + non-blocking Popen kick).
                    if st is not None:
                        folder = self.sessions.folder(next_sess)
                        sp = self._spearcon_path(folder)
                        if sp is not None:
                            st.queue.enqueue_front(SpeechItem(
                                id=self._alloc_id(), session=next_sess,
                                kind="prose", text=folder, is_decision=False,
                                mute_exempt=True, names_session=True,
                                audio_path=sp))
                    # pop_next() is guaranteed non-None: _select_keep_going verified
                    # len(queue) > 0 for next_sess inside this same held lock.
                    item = st.queue.pop_next() if st is not None else None
```

(`SpeechItem` is already imported at `host.py:9`. `names_session=True` makes `_attributed_text` set `_last_spoken_session = next_sess` — the next content pop is unprefixed, exactly the deliberate-jump suppression. `mute_exempt=True` keeps the pre-roll out of W12's capture.)

- [ ] **Step 4: Run tests + guards + suite**

Run: `.venv/bin/python -m pytest tests/test_keepgoing_preroll.py tests/test_concurrency_guards.py -q` → 6 + 4 pass (the stress now storms the pre-roll path; if it EVER flakes, widen the idle window — never weaken).
Full suite → **1052 passed, 1 skipped** (≈ +65 total; record the actual).

- [ ] **Step 5: Commit**

`git commit -am "feat(wave1): W13 keep-going pre-roll spearcon inside the M1 lock; joins the guard hammer set"`

---

## Completion protocol (after T12)

1. **Full-suite + guards final run:** `.venv/bin/python -m pytest -q` green (expected ≈ 1052 passed / 1 skipped from the 987/1 baseline; the guard file at 4 tests, storm rotation including REPEAT_LAST, pre-roll armed). Record the exact final count.
2. **Spec-coverage sweep (self-review):** every W1–W13 row of the spec's §18 table + the REREAD sub-item maps to a committed task test file; every exact spoken string in the spec appears verbatim in a test; the three OWNER GATES (⌃⌘R already locked; earcon assets Basso/Blow/Purr as config-level provisional picks; the "unheard" wording) are honored as data, not hardcoded semantics.
3. **Whole-branch review:** request an independent whole-branch review (superpowers:requesting-code-review) with the spec as oracle — special attention to the two speak-loop tasks (T11 capture, T12 pre-roll: M1 shape, guard extensions additive-only) and W11's behavior change (the ratified point, not a regression).
4. **Install is the OWNER's step.** Do NOT run `sonari install`, touch `~/.sonari/`, or restart his daemon. The ⌃⌘R binding, the new earcon kinds, and the boot cue go live at HIS `./bin/sonari install` from a real GUI Terminal; live audio feel (asset picks, the "unheard" word, chime+call-sign pacing) is his ear-pass. Hand him: the branch name, the final suite count, and the three ear-pass gates.
5. **No merge from this plan.** Merge is the owner's gate after the ear pass (per the campaign protocol).
