# Sonari Session-Streams Stage 3 — Multi-session UX + per-stream controls — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make multiple concurrent sessions usable eyes-free — a soft "waiting" earcon when a background session has backlog, a dedicated jump-to-waiting-session hotkey, spoken session attribution on every voice switch, foreground-scoped controls, cut-on-switch — and retire the single-queue-era `catch_up`/`REPEAT` commands the per-stream model has made redundant.

**Architecture:** Builds directly on Stage 2 (per-stream queues + foreground-driven speak loop). Everything new keys off the **live per-stream queue** (current turn) — NOT history `unheard` (that's Stage 4). The voice already plays `streams[foreground].queue`; Stage 3 adds (a) a background-backlog earcon fired from the PROSE path, (b) a `jump_waiting` handler that moves the *audio* foreground (not OS focus) to a blocked-first waiting stream, (c) folder-name attribution computed in the speak loop, (d) prompt-cut in the FLUSH handler, (e) foreground-scoped STOP, (f) deletion of the `catch_up`/`REPEAT` handlers + CLI + protocol + tests.

**Tech Stack:** Python 3.9+, stdlib-only core, pytest. One speak thread, one `self._lock`, per-connection handlers (unchanged).

## Global Constraints

_Every task's requirements implicitly include this section._

- **Python 3.9 floor, stdlib-only** in the core. No new dependencies.
- **Full suite green at every step.** Baseline is **698 passed, 2 skipped**. Run: `source .venv/bin/activate && python -m pytest -q --ignore=tests/test_kokoro.py` (the 2 skips + ignored module need the `[kokoro]`/numpy extra absent in `.venv` — pre-existing, not ours).
- **`.get` vs `_stream()` under the lock contract:** read-only sites use `self._streams.get(session)` (None/absent-safe); mutate/lazy-create sites use `self._stream(session)`. All `handle_message` branches and the speak-loop reads run under `self._lock`.
- **Stage 3 keys off the live per-stream QUEUE, never history `unheard`.** `has_waiting`, the waiting earcon, and the jump target all read `stream.queue`. The `unheard`/history refinement is Stage 4 (spec §7).
- **Do NOT touch history lifecycle.** FLUSH still calls `self.history.reset(session)` — removing that reset is Stage 4. Stage 3 must not change when history is wiped.
- **Locked design values (spec §6, do not deviate):**
  - Waiting earcon kind `"waiting"` → macOS `/System/Library/Sounds/Pop.aiff`; Windows a soft generated blip.
  - Jump action `jump_waiting`, protocol type `"jump_waiting"`, default key **`j`** (Ctrl+Cmd+J).
  - Jump target = **blocked-first** (a queue holding an unplayed decision item ranks ahead of prose-only), ties by session insertion order; excludes the current foreground and muted sessions.
  - Jump moves the **voice only** (not OS keyboard focus), **clears any pin**, does **not** re-pin, speaks **`"Jumping to <folder>."`**; empty case speaks **`"No session waiting."`**.
  - Session attribution: prepend `"<folder>. "` on the **first actually-spoken item** from a session different from the last spoken — but **never on the very first utterance** (a single ongoing session is never labeled). Self-naming cues (`names_session`) and `mute_exempt` control cues don't get the generic prefix.
  - **Keep** `JUMP_DECISION` and `REREAD_OPTIONS`. **Retire** `catch_up` and `REPEAT` entirely.
- **Do NOT push `main` or open a PR unless asked.** Do NOT touch `docs/getting-started.md` or `.convergence-plan.md` (pre-existing untracked, not ours).
- Branch off `main` (HEAD `1e182f7`). Each task is one or more commits; the stage is one PR-equivalent.

---

## File Structure

**Modified:**
- `src/sonari/protocol.py` — add `JUMP_WAITING`; remove `REPEAT`, `CATCH_UP` (Task 7).
- `src/sonari/keymap.py` — add `jump_waiting` action + default key `j`.
- `src/sonari/queue.py` — add `SpeechQueue.has_decision()`; add `SpeechItem.names_session`.
- `src/sonari/sessions.py` — add `SessionManager.focus()`.
- `src/sonari/session_stream.py` — add `waiting_signaled` flag + reset.
- `src/sonari/daemon.py` — `_waiting_target`, `JUMP_WAITING` handler, waiting-earcon firing, `_attributed_text` + `_last_spoken_session`, FLUSH prompt-cut, STOP rescope, delete `REPEAT`/`CATCH_UP` handlers.
- `src/sonari/history.py` — remove `other_session_with_unheard` (Task 7).
- `src/sonari/cli.py` — remove the `repeat` subcommand (Task 7).
- `src/sonari/platform/macos/earcon.py` — add `"waiting"` → Pop.aiff.
- `src/sonari/platform/windows/earcons/generate.py` — add `"waiting"` spec; regenerate `waiting.wav`.

**New test functions** land in the existing files noted per task. **`waiting.wav`** is a new committed asset.

---

## Task 1: Jump plumbing (additive primitives)

Add the constant, action, key, and the two small helpers the jump handler (Task 2) consumes. Purely additive — no handler yet (a `jump_waiting` message simply isn't matched and returns the default), so the suite stays green.

**Files:**
- Modify: `src/sonari/protocol.py` (after `JUMP_DECISION`)
- Modify: `src/sonari/keymap.py` (`ACTION_MESSAGES`, `_DEFAULT_KEYS`)
- Modify: `src/sonari/queue.py` (`SpeechQueue.has_decision`)
- Modify: `src/sonari/sessions.py` (`SessionManager.focus`)
- Test: `tests/test_protocol.py`, `tests/test_keymap.py`, `tests/test_queue.py`, `tests/test_sessions.py`

**Interfaces:**
- Produces: `MsgType.JUMP_WAITING == "jump_waiting"`; `keymap.ACTION_MESSAGES["jump_waiting"] == {"type": "jump_waiting"}`; default key `j`; `SpeechQueue.has_decision() -> bool`; `SessionManager.focus(session, cwd=None) -> None` (clears pin, sets foreground, does not re-pin).

- [ ] **Step 1: Write failing tests**

In `tests/test_queue.py` (follow the file's existing imports for `SpeechQueue`/`SpeechItem`):
```python
def test_has_decision_is_false_for_prose_only():
    q = SpeechQueue()
    q.enqueue(SpeechItem(id=1, session="s", kind="prose", text="a", is_decision=False))
    assert q.has_decision() is False

def test_has_decision_true_when_a_decision_is_queued():
    q = SpeechQueue()
    q.enqueue(SpeechItem(id=1, session="s", kind="prose", text="a", is_decision=False))
    q.enqueue(SpeechItem(id=2, session="s", kind="choice", text="q", is_decision=True))
    assert q.has_decision() is True
```

In `tests/test_sessions.py` (follow the file's existing import for `SessionManager`):
```python
def test_focus_clears_pin_and_sets_foreground():
    sm = SessionManager()
    sm.set_foreground("a")
    sm.pin_toggle()                       # pin a
    assert sm.pinned() == "a"
    sm.focus("b")
    assert sm.pinned() is None            # explicit jump overrides the pin
    assert sm.foreground() == "b"

def test_focus_records_cwd_folder():
    sm = SessionManager()
    sm.focus("b", cwd="/work/backend")
    assert sm.folder("b") == "backend"
```

In `tests/test_keymap.py` (follow the file's existing `keymap` import):
```python
def test_jump_waiting_action_message():
    assert keymap.ACTION_MESSAGES["jump_waiting"] == {"type": "jump_waiting"}

def test_default_keymap_binds_jump_waiting_to_j():
    km = keymap.default_keymap()
    assert km["jump_waiting"]["key"] == "j"
```

In `tests/test_protocol.py`, add `"JUMP_WAITING": "jump_waiting",` to the `expected` dict in **both** `test_msgtype_has_every_constant_with_exact_values` and `test_msgtype_defines_no_extra_string_constants` (place it next to the existing `"JUMP_DECISION": "jump_decision",` entry in each).

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `source .venv/bin/activate && python -m pytest tests/test_queue.py tests/test_sessions.py tests/test_keymap.py tests/test_protocol.py -q`
Expected: the new `has_decision`/`focus`/`jump_waiting` tests FAIL (AttributeError / KeyError); the protocol enum tests FAIL (`MsgType` missing `JUMP_WAITING`).

- [ ] **Step 3: Implement the primitives**

`src/sonari/protocol.py` — add after the `JUMP_DECISION` line:
```python
    JUMP_DECISION = "jump_decision"
    JUMP_WAITING = "jump_waiting"   # switch the voice to a waiting background session
```

`src/sonari/keymap.py` — in `ACTION_MESSAGES`, add (e.g. after `pin_toggle`):
```python
    "jump_waiting": {"type": "jump_waiting"},  # switch voice to a waiting background session
```
and in `_DEFAULT_KEYS` add `jump_waiting` bound to `j`:
```python
_DEFAULT_KEYS = {
    "nav_prev": "left", "nav_next": "right", "nav_first": "up", "nav_last": "down",
    "pause": "s", "mute": "m", "pin_toggle": "p", "jump_waiting": "j",
}
```

`src/sonari/queue.py` — add to `SpeechQueue`:
```python
    def has_decision(self) -> bool:
        """True if any queued item is a decision (choice|plan|permission). Used to
        rank a waiting session ahead of prose-only ones for jump-to-waiting."""
        return any(item.is_decision for item in self._items)
```

`src/sonari/sessions.py` — add to `SessionManager`:
```python
    def focus(self, session: str, cwd=None) -> None:
        """Explicitly move the voice to *session* (the jump-to-waiting hotkey):
        clear any pin — an explicit jump overrides a pin — and set it foreground.
        Does NOT re-pin."""
        self._record(session, cwd)
        self._pinned = None
        self._foreground = session
```

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `source .venv/bin/activate && python -m pytest tests/test_queue.py tests/test_sessions.py tests/test_keymap.py tests/test_protocol.py -q`
Expected: PASS.

- [ ] **Step 5: Full suite**

Run: `source .venv/bin/activate && python -m pytest -q --ignore=tests/test_kokoro.py`
Expected: 698 baseline + the new tests, all green (the self-validating `tests/test_hotkeyd_contract.py::test_all_action_messages_are_known_msgtypes` now also covers `jump_waiting`).

- [ ] **Step 6: Commit**

```bash
git add src/sonari/protocol.py src/sonari/keymap.py src/sonari/queue.py src/sonari/sessions.py tests/test_protocol.py tests/test_keymap.py tests/test_queue.py tests/test_sessions.py
git commit -m "feat: jump-to-waiting plumbing (JUMP_WAITING, key j, has_decision, focus) (Stage 3 Task 1)"
```

---

## Task 2: `jump_waiting` handler

The core feature: pick the blocked-first waiting background stream, move the voice to it (clearing any pin), cut the current sentence, and lead with a spoken folder label. Empty case speaks `"No session waiting."`.

**Files:**
- Modify: `src/sonari/daemon.py` (new `_waiting_target` helper; `JUMP_WAITING` branch in `handle_message`)
- Test: `tests/test_daemon_streams.py` (follow its existing `_msg`/`_prose`/`make_daemon`/`stream_queue` helpers; import `SpeechItem`/`MsgType` as the file already does)

**Interfaces:**
- Consumes: `MsgType.JUMP_WAITING` (Task 1), `SpeechQueue.has_decision()` (Task 1), `SessionManager.focus()` (Task 1), `sessions.foreground()`, `sessions.folder()`.
- Produces: a `JUMP_WAITING` handler. Enqueues the preamble with `mute_exempt=True, at_front=True` (Task 4 will add `names_session=True` to it).

- [ ] **Step 1: Write failing tests**

In `tests/test_daemon_streams.py`, first add a local prose helper — the file has `_msg` but **no `_prose`** (prose is fed via raw PROSE messages). Add it once, near the top:
```python
def _prose(daemon, session, text, index=0, final=False):
    daemon.handle_message(_msg(MsgType.PROSE, session, delta=text, index=index, final=final))
```
Then the tests:
```python
def test_jump_waiting_switches_to_background_and_announces_folder():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="a")
    sessions.register("b", cwd="/work/backend")
    _prose(daemon, "b", "All done. ")                  # b accumulates in the background
    assert len(stream_queue(daemon, "b")) >= 1
    daemon.handle_message(_msg(MsgType.JUMP_WAITING, "a"))
    assert sessions.foreground() == "b"
    assert speaker.cancels == 1                          # cut-on-switch
    assert stream_queue(daemon, "b")._items[0].text == "Jumping to backend."

def test_jump_waiting_prefers_a_blocked_session():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="a")
    sessions.register("b", cwd="/x/proseonly")
    sessions.register("c", cwd="/x/blocked")
    _prose(daemon, "b", "just text. ")
    daemon.handle_message(_msg(MsgType.CHOICE, "c",
                               questions=[{"question": "Pick?",
                                           "options": [{"label": "One"}, {"label": "Two"}]}]))
    daemon.handle_message(_msg(MsgType.JUMP_WAITING, "a"))
    assert sessions.foreground() == "c"                  # blocked outranks prose-only

def test_jump_waiting_excludes_current_foreground_backlog():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="a")
    _prose(daemon, "a", "my own backlog. ")             # only the foreground has backlog
    daemon.handle_message(_msg(MsgType.JUMP_WAITING, "a"))
    assert sessions.foreground() == "a"
    assert queue._items[-1].text == "No session waiting."

def test_jump_waiting_skips_a_muted_background_session():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="a")
    daemon._stream("b").muted = True
    _prose(daemon, "b", "secret. ")
    daemon.handle_message(_msg(MsgType.JUMP_WAITING, "a"))
    assert sessions.foreground() == "a"
    assert queue._items[-1].text == "No session waiting."

def test_jump_waiting_clears_an_active_pin():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="a")
    daemon.handle_message(_msg(MsgType.PIN_TOGGLE, "a"))   # pin a
    sessions.register("b", cwd="/x/backend")
    _prose(daemon, "b", "ready. ")
    daemon.handle_message(_msg(MsgType.JUMP_WAITING, "a"))
    assert sessions.pinned() is None
    assert sessions.foreground() == "b"
```

- [ ] **Step 2: Run to verify they fail**

Run: `source .venv/bin/activate && python -m pytest tests/test_daemon_streams.py -q -k jump_waiting`
Expected: FAIL — no handler, so foreground never changes / no preamble enqueued.

- [ ] **Step 3: Implement the handler**

In `src/sonari/daemon.py`, add the helper (near the other private helpers, e.g. after `_drop_pending`):
```python
    def _waiting_target(self, exclude):
        """The background session jump-to-waiting should switch to, or None.

        Considers only streams with a non-empty, non-muted queue (live backlog —
        Stage 3 keys off the queue, not history). A stream holding an unplayed
        decision (choice|plan|permission) ranks ahead of prose-only ones; ties break
        by session insertion order. Excludes *exclude* (the current foreground)."""
        blocked, prose = [], []
        for sess, st in self._streams.items():          # insertion-ordered
            if sess == exclude or st.muted or len(st.queue) == 0:
                continue
            (blocked if st.queue.has_decision() else prose).append(sess)
        ordered = blocked + prose
        return ordered[0] if ordered else None
```

Add the handler branch (place it next to the `JUMP_DECISION` branch):
```python
        if t == MsgType.JUMP_WAITING:
            fg = self.sessions.foreground()
            target = self._waiting_target(exclude=fg)
            if target is None:
                # Nothing waiting: say so (mute_exempt so it's always heard). With no
                # foreground to speak through, fall back to an error earcon.
                if fg is not None:
                    self._enqueue(fg, "prose", "No session waiting.", False,
                                  mute_exempt=True)
                else:
                    self.speaker.earcon("error")
                return None
            # Explicit move: clear any pin, switch the VOICE (not OS focus) to the
            # target, cut the current utterance so the switch is immediate, and lead
            # with a spoken folder label. The foreground-driven loop then drains the
            # target's accumulated backlog.
            self.sessions.focus(target)
            self.speaker.cancel()
            folder = self.sessions.folder(target)
            preamble = ("Jumping to {0}.".format(folder) if folder
                        else "Jumping to another session.")
            self._enqueue(target, "prose", preamble, False,
                          mute_exempt=True, at_front=True)
            return None
```

- [ ] **Step 4: Run to verify they pass**

Run: `source .venv/bin/activate && python -m pytest tests/test_daemon_streams.py -q -k jump_waiting`
Expected: PASS. (If the `test_jump_waiting_prefers_a_blocked_session` CHOICE payload shape differs from the file's existing choice helper, use that helper instead — the requirement is that `c` ends with a decision item queued.)

- [ ] **Step 5: Full suite**

Run: `source .venv/bin/activate && python -m pytest -q --ignore=tests/test_kokoro.py`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/sonari/daemon.py tests/test_daemon_streams.py
git commit -m "feat: jump-to-waiting handler (blocked-first, voice-only, cut + folder preamble) (Stage 3 Task 2)"
```

---

## Task 3: Waiting earcon

A soft `"waiting"` earcon fires once when a **background** (non-foreground, non-muted) session produces its first speakable prose of a turn. Debounced per stream; re-arms on the next prompt. Decisions keep their own alert earcon (this is prose-only).

**Files:**
- Modify: `src/sonari/platform/windows/earcons/generate.py` (add `"waiting"` spec; update the "6"→"7" docstrings) and regenerate `waiting.wav`
- Modify: `src/sonari/platform/macos/earcon.py` (`_DEFAULTS`)
- Modify: `src/sonari/session_stream.py` (`waiting_signaled` + reset)
- Modify: `src/sonari/daemon.py` (PROSE branch fires the earcon)
- Test: `tests/test_macos_earcon.py`, `tests/test_win_earcon.py` / `tests/test_win_earcons_assets.py` / `tests/test_earcon_generator.py` (whichever enumerate kinds), `tests/test_daemon_streams.py`

**Interfaces:**
- Consumes: `sessions.foreground()`, `stream.muted`.
- Produces: earcon kind `"waiting"` in both backends' `default_earcons()`; `SessionStream.waiting_signaled` (reset by `reset_for_new_prompt`).

- [ ] **Step 1: Write failing tests**

In `tests/test_daemon_streams.py`:
```python
def test_background_prose_fires_one_waiting_earcon():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="a")
    _prose(daemon, "b", "first. second. third. ")       # b is background
    assert speaker.earcons.count("waiting") == 1         # once per turn, not per sentence

def test_foreground_prose_does_not_fire_waiting():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="a")
    _prose(daemon, "a", "hello. world. ")
    assert "waiting" not in speaker.earcons

def test_muted_background_does_not_fire_waiting():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="a")
    daemon._stream("b").muted = True
    _prose(daemon, "b", "x. y. ")
    assert "waiting" not in speaker.earcons

def test_waiting_rearms_after_new_prompt():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="a")
    _prose(daemon, "b", "turn one. ")
    assert speaker.earcons.count("waiting") == 1
    daemon.handle_message(_msg(MsgType.FLUSH, "b"))      # new prompt to b (still background)
    _prose(daemon, "b", "turn two. ")
    assert speaker.earcons.count("waiting") == 2
```

In `tests/test_macos_earcon.py` (follow the file's pattern), assert the macOS default map includes `waiting`:
```python
def test_default_earcons_includes_waiting_pop():
    from sonari.platform.macos.earcon import MacEarconBackend
    assert MacEarconBackend().default_earcons()["waiting"].endswith("/Pop.aiff")
```

For Windows, find the test that enumerates earcon kinds (likely `tests/test_win_earcons_assets.py` or `tests/test_earcon_generator.py`) and add `"waiting"` to the expected name set / count. If `tests/test_win_earcon.py` asserts `default_earcons()` keys, add `waiting` there:
```python
def test_windows_default_earcons_includes_waiting():
    names = set(default_earcons().keys())          # use the file's existing import
    assert "waiting" in names
```

- [ ] **Step 2: Run to verify they fail**

Run: `source .venv/bin/activate && python -m pytest tests/test_daemon_streams.py tests/test_macos_earcon.py tests/test_win_earcon.py tests/test_win_earcons_assets.py tests/test_earcon_generator.py -q -k "waiting or earcon"`
Expected: FAIL — no `"waiting"` kind; no earcon fired.

- [ ] **Step 3a: Add the Windows spec + regenerate the asset**

In `src/sonari/platform/windows/earcons/generate.py`, add to `_EARCON_SPECS`:
```python
    "waiting":    (392.0, 0.09, "sine",    None ),  # G4 — soft, brief background-backlog blip
```
Update the count wording in the module/docstrings ("The 6 canonical Sonari earcons" → "The 7 canonical Sonari earcons"; `generate_all_earcons` "Write all 6 earcon .wav files" → "all 7").

Regenerate the committed asset:
```bash
source .venv/bin/activate
python -m sonari.platform.windows.earcons.generate src/sonari/platform/windows/earcons
```
This writes `src/sonari/platform/windows/earcons/waiting.wav` (and rewrites the others identically). Confirm `waiting.wav` now exists.

- [ ] **Step 3b: Add the macOS default**

In `src/sonari/platform/macos/earcon.py`, add to `_DEFAULTS`:
```python
    "waiting":    "/System/Library/Sounds/Pop.aiff",
```

- [ ] **Step 3c: Add the debounce flag**

In `src/sonari/session_stream.py`, add to `__init__`:
```python
        self.waiting_signaled = False       # background "waiting" earcon fired this turn
```
and add to `reset_for_new_prompt` (so a new prompt re-arms it):
```python
        self.waiting_signaled = False
```

- [ ] **Step 3d: Fire the earcon when background prose reaches the queue**

Fire from `_flush_prose_buffer` — **not** on chunk production — so the cue is queue-consistent: it fires only once items are actually in the stream's queue (at `minqueue>1`, firing on chunk production would signal "waiting" while the queue is still empty, and an immediate jump would then say "No session waiting"). The PROSE branch is left unchanged.

**Re-arm rationale (write into the comment so it reads as intentional):** the flag re-arms on `reset_for_new_prompt` (FLUSH) only — i.e. **once per turn**, matching spec §6 ("on empty→waiting and on a new turn, not per sentence"). It deliberately does NOT re-arm on queue-drain mid-turn; a background session that keeps producing within one turn earcons once, not per sentence.

In `src/sonari/daemon.py`, modify `_flush_prose_buffer`:
```python
    def _flush_prose_buffer(self, session: str) -> None:
        """Enqueue everything buffered for *session* (e.g. at the turn boundary, so
        a message that ended below the threshold is still read)."""
        st = self._stream(session)
        buf = st.prose_buffer
        if not buf:
            return
        st.prose_buffer = []
        for text, entry in buf:
            self._enqueue(session, "prose", text, False, entry=entry)
        # Background-backlog cue: ONCE per turn, when a NON-foreground, non-muted
        # session's prose reaches its (now non-empty) queue. Debounced via the
        # per-stream flag, re-armed only by reset_for_new_prompt (a new prompt =
        # a new turn) — never per sentence. Decisions carry their own alert earcon,
        # so this is prose-only.
        if (not st.waiting_signaled and not st.muted
                and session != self.sessions.foreground()
                and len(st.queue) > 0):
            self.speaker.earcon("waiting")
            st.waiting_signaled = True
```

- [ ] **Step 4: Run to verify they pass**

Run: `source .venv/bin/activate && python -m pytest tests/test_daemon_streams.py tests/test_macos_earcon.py tests/test_win_earcon.py tests/test_win_earcons_assets.py tests/test_earcon_generator.py -q -k "waiting or earcon"`
Expected: PASS.

- [ ] **Step 5: Full suite**

Run: `source .venv/bin/activate && python -m pytest -q --ignore=tests/test_kokoro.py`
Expected: all green. (If a Windows assets test asserts an exact earcon count, it was updated in Step 1.)

- [ ] **Step 6: Commit**

```bash
git add src/sonari/platform/windows/earcons/generate.py src/sonari/platform/windows/earcons/waiting.wav src/sonari/platform/macos/earcon.py src/sonari/session_stream.py src/sonari/daemon.py tests/
git commit -m "feat: background-waiting earcon (Pop / soft blip), debounced per turn (Stage 3 Task 3)"
```

---

## Task 4: Session attribution ("who's speaking?")

The voice prepends the folder name on the first spoken item from a newly-foregrounded session — never on the very first utterance, and never doubled with a self-naming cue.

**Files:**
- Modify: `src/sonari/queue.py` (`SpeechItem.names_session` field)
- Modify: `src/sonari/daemon.py` (`_enqueue` passthrough; `_last_spoken_session`; `_attributed_text`; speak loop; set `names_session` on the jump preamble + pin confirmation)
- Test: `tests/test_daemon_streams.py`

**Interfaces:**
- Consumes: `sessions.folder()`.
- Produces: `SpeechItem.names_session: bool` (default False); `_enqueue(..., names_session=False)`; folder-prefixed speech in the loop.

- [ ] **Step 1: Write failing tests**

In `tests/test_daemon_streams.py`, add a local drain helper — the file has `_pump_one` (one iteration) but no full drainer. Add it once, near `_pump_one`:
```python
def _drain(daemon):
    """Run the speak loop until the foreground stream's queue is empty (no thread)."""
    for _ in range(1000):
        fg = daemon.sessions.foreground()
        st = daemon._streams.get(fg)
        if st is None or len(st.queue) == 0:
            break
        daemon._speak_loop_once()
```
Then the tests (they also use the `_prose` helper added in Task 2):
```python
def test_no_folder_prefix_on_the_first_utterance_single_session():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="a")
    sessions.register("a", cwd="/x/frontend")
    _prose(daemon, "a", "one. two. ")
    _drain(daemon)                                       # speak loop processes a's items
    assert speaker.spoken == ["one.", "two."]            # never labeled — single session

def test_voice_announces_folder_when_switching_sessions():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="a")
    sessions.register("a", cwd="/x/frontend")
    sessions.register("b", cwd="/x/backend")
    _prose(daemon, "a", "alpha. ")
    _drain(daemon)                                       # _last_spoken -> a
    daemon.handle_message(_msg(MsgType.SET_FOREGROUND, "b", cwd="/x/backend"))
    _prose(daemon, "b", "beta. ")
    _drain(daemon)
    assert "backend. beta." in speaker.spoken            # folder prefix on the switch

def test_jump_preamble_does_not_double_announce_the_folder():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="a")
    sessions.register("a", cwd="/x/frontend")
    sessions.register("b", cwd="/x/backend")
    _prose(daemon, "a", "alpha. ")
    _drain(daemon)                                       # _last_spoken -> a
    _prose(daemon, "b", "beta. ")                        # b accumulates
    daemon.handle_message(_msg(MsgType.JUMP_WAITING, "a"))
    _drain(daemon)
    assert "Jumping to backend." in speaker.spoken
    assert "beta." in speaker.spoken                     # the prose itself is NOT prefixed
    assert "backend. beta." not in speaker.spoken        # no double-announce
```

- [ ] **Step 2: Run to verify they fail**

Run: `source .venv/bin/activate && python -m pytest tests/test_daemon_streams.py -q -k "folder or announce or double_announce"`
Expected: FAIL (no prefixing; `names_session`/`_attributed_text` absent).

- [ ] **Step 3: Implement attribution**

`src/sonari/queue.py` — add a field to `SpeechItem`:
```python
    names_session: bool = False  # text already speaks the session's folder (jump/pin cue)
```

`src/sonari/daemon.py` — thread the param through `_enqueue`:
```python
    def _enqueue(self, session: str, kind: str, text: str, is_decision: bool,
                 entry=None, mute_exempt: bool = False,
                 pause_exempt: bool = False, at_front: bool = False,
                 names_session: bool = False) -> None:
        item = SpeechItem(
            id=self._alloc_id(),
            session=session,
            kind=kind,
            text=text,
            is_decision=is_decision,
            mute_exempt=mute_exempt,
            pause_exempt=pause_exempt,
            names_session=names_session,
        )
```
(rest of `_enqueue` unchanged.)

Add the tracker in `__init__`:
```python
        self._last_spoken_session = None          # for folder attribution on switch
```

Add the helper (near `note_spoken`):
```python
    def _attributed_text(self, item):
        """item.text, prefixed with the session's folder name when the voice switches
        to a session different from the one last spoken — so the user knows who's
        talking. Never prefixes the very first utterance (last == None), a self-naming
        cue (names_session), or a control cue (mute_exempt). Updates _last_spoken_session.
        Called under self._lock from the speak loop."""
        text = item.text
        if item.names_session:
            self._last_spoken_session = item.session
        elif not item.mute_exempt:
            if (self._last_spoken_session is not None
                    and item.session != self._last_spoken_session):
                folder = self.sessions.folder(item.session)
                if folder:
                    text = "{0}. {1}".format(folder, item.text)
            self._last_spoken_session = item.session
        return text
```

In `_speak_loop_once`, compute the attributed text under the claim lock and speak it. In the **normal (non-paused) branch**, change the claim block so a local `text` is computed when not muted, and pass it to `speak()`:
```python
        with self._lock:
            fg = self.sessions.foreground()
            st = self._streams.get(fg)
            item = st.queue.pop_next() if st is not None else None
            self._current_item = item
            cancel_epoch = self.speaker.cancel_epoch()
            ist = self._streams.get(item.session) if item is not None else None
            muted = (item is not None
                     and ist is not None and ist.muted
                     and not item.mute_exempt)
            text = None
            if muted:
                self._current_item = None
                self._pending_heard.pop(item.id, None)
            elif item is not None:
                text = self._attributed_text(item)
        if item is None:
            self._wake.wait(self._poll_interval)
            self._wake.clear()
            return
        if muted:
            return
        try:
            completed = self.speaker.speak(text, cancel_epoch=cancel_epoch)
        except Exception:  # noqa: BLE001
            self._signal_speak_failure()
            completed = False
```
(The pause-exempt branch speaks short control cues like "Paused." which are `mute_exempt` and must NOT be attributed; leave that branch speaking `item.text` unchanged.)

Set `names_session=True` on the self-naming cues:
- In the `JUMP_WAITING` handler (Task 2), the preamble enqueue becomes:
```python
            self._enqueue(target, "prose", preamble, False,
                          mute_exempt=True, at_front=True, names_session=True)
```
- In the `PIN_TOGGLE` handler, the confirmation enqueue becomes (only the "Pinned <folder>." case self-names):
```python
            self._enqueue(fg, "prose", text, False, mute_exempt=True,
                          names_session=(action == "pinned"))
```

- [ ] **Step 4: Run to verify they pass**

Run: `source .venv/bin/activate && python -m pytest tests/test_daemon_streams.py -q -k "folder or announce or double_announce or jump_waiting"`
Expected: PASS.

- [ ] **Step 5: Full suite**

Run: `source .venv/bin/activate && python -m pytest -q --ignore=tests/test_kokoro.py`
Expected: all green. (Watch the existing speak-loop tests that assert `speaker.spoken`: a single-session test that drained from a fresh daemon should be unaffected because the first utterance is never prefixed — if any pre-existing multi-session test now sees a folder prefix, that is the new correct behavior; update its expectation and note why in the commit.)

- [ ] **Step 6: Commit**

```bash
git add src/sonari/queue.py src/sonari/daemon.py tests/test_daemon_streams.py
git commit -m "feat: spoken folder attribution on voice switch (no first-utterance / no double-announce) (Stage 3 Task 4)"
```

---

## Task 5: Cut-on-switch (new prompt)

A new prompt in a different session cuts the currently-playing session's sentence so the voice moves immediately. Routed through FLUSH (prompt-only — `SESSION_START` emits no FLUSH, so a bare new session does not cut). Pin-aware.

**Files:**
- Modify: `src/sonari/daemon.py` (`FLUSH` branch cancel condition)
- Test: `tests/test_daemon_control.py` (follow its `_msg`/`make_daemon` helpers; import `SpeechItem`)

**Interfaces:**
- Consumes: `sessions.foreground()`.

- [ ] **Step 1: Write failing tests**

In `tests/test_daemon_control.py`:
```python
def test_new_prompt_cuts_a_different_sessions_current_utterance():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="a")
    daemon._current_item = SpeechItem(id=1, session="a", kind="prose",
                                      text="long answer.", is_decision=False)
    daemon.handle_message(_msg(MsgType.SET_FOREGROUND, "b", cwd="/x/b"))
    daemon.handle_message(_msg(MsgType.FLUSH, "b"))
    assert speaker.cancels == 1                          # a's sentence cut

def test_new_prompt_does_not_cut_when_pinned_elsewhere():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="a")
    daemon.handle_message(_msg(MsgType.PIN_TOGGLE, "a"))   # pin a
    daemon._current_item = SpeechItem(id=1, session="a", kind="prose",
                                      text="answer.", is_decision=False)
    daemon.handle_message(_msg(MsgType.SET_FOREGROUND, "b", cwd="/x/b"))
    daemon.handle_message(_msg(MsgType.FLUSH, "b"))
    assert speaker.cancels == 0                          # a stays — pinned

def test_new_prompt_same_session_still_cuts():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="a")
    daemon._current_item = SpeechItem(id=1, session="a", kind="prose",
                                      text="answer.", is_decision=False)
    daemon.handle_message(_msg(MsgType.FLUSH, "a"))
    assert speaker.cancels == 1                          # existing behavior preserved
```

- [ ] **Step 2: Run to verify they fail**

Run: `source .venv/bin/activate && python -m pytest tests/test_daemon_control.py -q -k "new_prompt"`
Expected: `test_new_prompt_cuts_a_different_sessions_current_utterance` FAILS (cancels==0 today); the other two should already pass — confirm they do, to lock the preserved behavior.

- [ ] **Step 3: Generalize the FLUSH cancel**

In `src/sonari/daemon.py`, the `MsgType.FLUSH` branch — replace the cancel condition:
```python
        if t == MsgType.FLUSH:
            st = self._stream(session)
            self._drop_pending(st.queue.clear())
            cur = self._current_item
            # Cut the current utterance on a new prompt: same-session (the new prompt
            # supersedes the old reply) OR a cross-session switch where this prompt's
            # session is now the foreground (pin-aware) — so the voice moves to it
            # immediately instead of finishing the old session's sentence (§4.2
            # cut-on-switch). SESSION_START sends no FLUSH, so a bare new session
            # never cuts.
            if cur is not None and (cur.session == session
                                    or self.sessions.foreground() == session):
                self.speaker.cancel()
            st.reset_for_new_prompt()
            self.history.reset(session)
            self._paused.clear()
            self._wake.set()
            return None
```

- [ ] **Step 4: Run to verify they pass**

Run: `source .venv/bin/activate && python -m pytest tests/test_daemon_control.py -q -k "new_prompt"`
Expected: PASS.

- [ ] **Step 5: Full suite**

Run: `source .venv/bin/activate && python -m pytest -q --ignore=tests/test_kokoro.py`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/sonari/daemon.py tests/test_daemon_control.py
git commit -m "feat: cut-on-switch — a new prompt cuts a different session's utterance, pin-aware (Stage 3 Task 5)"
```

---

## Task 6: Rescope STOP to the foreground stream

STOP clears only the foreground stream's queue; background backlog survives (fixes the 2a global-STOP clobber).

**Files:**
- Modify: `src/sonari/daemon.py` (`STOP` branch)
- Test: `tests/test_daemon_control.py` (rename the existing all-streams test for accuracy; add a background-survives test)

**Interfaces:**
- Consumes: `sessions.foreground()`, `self._streams.get()`.

- [ ] **Step 1: Write the failing test + correct the stale one**

In `tests/test_daemon_control.py`, add:
```python
def test_stop_leaves_background_streams_untouched():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="a")
    _seed(stream_queue(daemon, "b"), daemon, "b", 2)    # background b has backlog
    _seed(queue, daemon, "a", 2)                         # foreground a
    daemon.handle_message(_msg(MsgType.STOP, "a"))
    assert len(queue) == 0                               # foreground cleared
    assert len(stream_queue(daemon, "b")) == 2           # background untouched
    assert speaker.cancels == 1
```
(If `stream_queue` is not already imported in this file, import it from `tests.daemon_helpers` as the other files do.) Also rename the existing `test_stop_clears_all_and_cancels` (line ~42) to `test_stop_clears_foreground_and_cancels` — it only ever seeds the foreground stream, so the old name is now inaccurate; the assertions are unchanged and still pass.

- [ ] **Step 2: Run to verify the new test fails**

Run: `source .venv/bin/activate && python -m pytest tests/test_daemon_control.py -q -k "stop"`
Expected: `test_stop_leaves_background_streams_untouched` FAILS (today STOP clears b too).

- [ ] **Step 3: Rescope STOP**

In `src/sonari/daemon.py`, the `MsgType.STOP` branch:
```python
        if t == MsgType.STOP:
            # Stop acts on the FOREGROUND stream only — clearing every stream would
            # wipe a background session's backlog the user hasn't heard yet (the 2a
            # global-STOP clobber). Background streams accumulate untouched.
            fg = self.sessions.foreground()
            st = self._streams.get(fg)
            if st is not None:
                self._drop_pending(st.queue.clear())
            self.speaker.cancel()
            return None
```

- [ ] **Step 4: Run to verify they pass**

Run: `source .venv/bin/activate && python -m pytest tests/test_daemon_control.py -q -k "stop"`
Expected: PASS (both the new test and the renamed one).

- [ ] **Step 5: Full suite**

Run: `source .venv/bin/activate && python -m pytest -q --ignore=tests/test_kokoro.py`
Expected: all green. (`tests/test_hotkeyd_contract.py::test_stop_message_clears_and_cancels` and `tests/test_daemon_phase21.py::test_stop_leaves_entries_unheard` seed only the foreground stream, so they still pass.)

- [ ] **Step 6: Commit**

```bash
git add src/sonari/daemon.py tests/test_daemon_control.py
git commit -m "fix: STOP acts on the foreground stream only — background backlog survives (Stage 3 Task 6)"
```

---

## Task 7: Retire `catch_up` + `REPEAT` entirely

Delete both handlers, their protocol constants, the now-dead `other_session_with_unheard`, the `repeat` CLI subcommand, and every test that encodes their replay semantics. Keep `JUMP_DECISION`, `REREAD_OPTIONS`, and `history.unheard` (a surviving STOP test uses it; Stage 4 will use it again).

**Files:**
- Modify: `src/sonari/daemon.py` (delete the `REPEAT` and `CATCH_UP` branches; fix stale comments)
- Modify: `src/sonari/protocol.py` (remove `REPEAT`, `CATCH_UP`)
- Modify: `src/sonari/history.py` (remove `other_session_with_unheard`)
- Modify: `src/sonari/cli.py` (remove `_cmd_repeat` + the `repeat` subparser)
- Modify/Delete tests (see Step 1)

**Interfaces:**
- Removes: `MsgType.REPEAT`, `MsgType.CATCH_UP`, `SessionHistory.other_session_with_unheard`, `sonari repeat`.

- [ ] **Step 1: Update/delete the tests first (red-by-removal), then the code**

This task removes behavior, so the tests that assert that behavior are **deleted** (not rewritten into tautologies), and the enum/CLI lists that name the removed identifiers are **trimmed**. Per the inventory:

**Delete these test functions entirely** (their core assertion is catch_up/repeat replay, which ceases to exist):
- `tests/test_daemon_phase21.py`: `test_repeat_respeaks_whole_last_message_not_last_fragment`, `test_repeat_targets_last_message_only`, `test_repeat_with_no_history_says_nothing_to_repeat`, `test_repeat_acts_on_foreground_session_history`, `test_catch_up_replays_unheard_oldest_first_then_marks_heard`, `test_catch_up_interrupted_sentence_replays_from_its_start`, `test_catch_up_all_heard_says_caught_up`, `test_catch_up_falls_back_to_other_session_backlog`, `test_catch_up_does_not_double_speak_queued_items`.
- `tests/test_daemon_control.py`: `test_catch_up_no_longer_discards_the_backlog`, `test_repeat_noop_when_nothing_spoken_yet`, `test_repeat_reenqueues_last_spoken_text`, `test_repeat_noop_when_no_foreground_session`, `test_repeat_drives_speak_path`.
- `tests/test_hotkeyd_contract.py`: `test_repeat_message_reenqueues_last_spoken`, `test_catch_up_message_replays_unheard_backlog`.
- `tests/test_daemon_streams.py`: `test_catch_up_routes_cross_session_backlog_into_the_foreground_stream`.
- `tests/test_cli_control.py`: `test_repeat_sends_repeat`.

**Trim these:**
- `tests/test_protocol.py`: remove the `"REPEAT": "repeat",` and `"CATCH_UP": "catch_up",` entries from the `expected` dict in **both** `test_msgtype_has_every_constant_with_exact_values` and `test_msgtype_defines_no_extra_string_constants`.
- `tests/test_cli_control.py`: remove the `["repeat"]` entry from the `CONTROL_SUBCOMMANDS` list.
- `tests/test_hotkeyd_contract.py`: update the module docstring that lists `stop/skip/repeat` so it no longer claims `repeat` is exercised here.
- `tests/test_daemon_phase21.py`: in `test_choice_for_background_session_enqueues_to_its_own_stream`, delete the `# recorded for catch_up` comment; **keep** the `assert daemon.history.unheard("b")` assertion (history records background prose as unheard — a property independent of catch_up).

Run the suite now to see the expected failures from removed identifiers (the deleted tests are gone; remaining references to `MsgType.REPEAT`/`MsgType.CATCH_UP` in code will surface in Step 2):
Run: `source .venv/bin/activate && python -m pytest -q --ignore=tests/test_kokoro.py`
Expected: green for tests, OR failures only where production code still references the about-to-be-removed constants — which Step 2 removes.

- [ ] **Step 2: Delete the production code**

`src/sonari/daemon.py`:
- Delete the entire `if t == MsgType.REPEAT:` branch (the block beginning `if t == MsgType.REPEAT:` and ending at its `return None`).
- Delete the entire `if t == MsgType.CATCH_UP:` branch (from `if t == MsgType.CATCH_UP:` through its `return None`).
- Fix stale comments: in the `JUMP_DECISION` branch, reword the comment that mentions "so a later `CATCH_UP` doesn't replay them out of order (M6)" to drop the dead reference (the heard-marking it describes still applies); and update any other surviving comment that names `repeat`/`catch_up`. Verify with: `grep -ni "catch_up\|catchup\|repeat" src/sonari/daemon.py` — only legitimate survivors (none expected) should remain.

`src/sonari/protocol.py` — remove:
```python
    REPEAT = "repeat"
```
and
```python
    CATCH_UP = "catch_up"
```

`src/sonari/history.py` — remove the whole `other_session_with_unheard` method (its only caller was the catch_up handler). Keep `unheard` (used by `tests/test_daemon_phase21.py::test_stop_leaves_entries_unheard` and Stage 4).

`src/sonari/cli.py` — remove `_cmd_repeat` (the 3-line function) and its subparser registration:
```python
def _cmd_repeat(_args) -> int:
    _send({"v": PROTOCOL_VERSION, "type": MsgType.REPEAT})
    return 0
```
```python
    sub.add_parser("repeat", help="repeat the last spoken item").set_defaults(
        func=_cmd_repeat)
```

- [ ] **Step 3: Run the full suite**

Run: `source .venv/bin/activate && python -m pytest -q --ignore=tests/test_kokoro.py`
Expected: all green. Confirm no surviving references with:
```bash
grep -rni "catch_up\|catchup\|MsgType.REPEAT\|other_session_with_unheard" src/sonari tests | grep -v ".pyc"
```
Expected: no production references; any test hit should be an intentional, unrelated string (e.g. `test_assembler.py::test_repeated_index_is_ignored`).

- [ ] **Step 4: Commit**

```bash
git add src/sonari/daemon.py src/sonari/protocol.py src/sonari/history.py src/sonari/cli.py tests/
git commit -m "refactor: retire catch_up + REPEAT entirely (handlers, protocol, CLI, dead helper, tests) (Stage 3 Task 7)"
```

---

## Self-Review (run by the author after writing)

**Spec coverage (§8.3 pieces → task):**
- (a) waiting earcon → Task 3 ✓
- (b) jump_waiting hotkey (blocked-first, voice-only, preamble, empty case, no-pin) → Tasks 1+2 ✓
- (c) scope controls to foreground → Task 6 (STOP; PAUSE/NAV/MUTE/SKIP already foreground-scoped per the Stage-2 code — confirmed, no change needed) ✓
- (d) cut-on-switch (jump + new prompt) → Task 2 (jump) + Task 5 (prompt); SESSION_START correctly excluded ✓
- (e) session attribution → Task 4 ✓
- (f) retire catch_up + REPEAT (keep JUMP_DECISION/REREAD_OPTIONS) → Task 7 ✓
- Durability scope (queue, not history; no history-lifecycle change) → honored: only Task 5 touches FLUSH and it does not alter `history.reset`; `has_waiting`/earcon/jump all read `stream.queue`. ✓

**Placeholder scan:** none — every code/test step contains real code or an exact identifier list.

**Type/name consistency:** `JUMP_WAITING`/`"jump_waiting"`, `jump_waiting` action+key `j`, `has_decision()`, `focus()`, `waiting_signaled`, `names_session`, `_last_spoken_session`, `_attributed_text`, `_waiting_target`, earcon kind `"waiting"` — used identically across tasks. The jump preamble's `names_session=True` (Task 4) updates the Task-2 enqueue site (noted in Task 4 Step 3).

**Ordering / dependencies:** Task 1 → 2 (primitives); Task 2 → 4 (preamble flag); 3, 5, 6, 7 independent. Subagent-driven execution is sequential, so 1→7 is safe.

**Known nuance for reviewers:** cut-on-switch drops the cut sentence from playback (it was already popped/claimed); jumping back resumes at the next item, not the cut sentence — acceptable for Stage 3. The "blocked" signal is queue-state (an unplayed decision item); if the user answers a permission directly in that terminal, the queued copy is stale until drained — acceptable for Stage 3 (documented in spec §6).
