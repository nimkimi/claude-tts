# Sonari Session Streams — Stage 1 (Extract `SessionStream`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move all per-session speech state out of `SpeechDaemon`'s ten parallel dicts/sets into one `SessionStream` object per session, with **zero behavior change** (the speech queue stays shared; per-stream queues are Stage 2).

**Architecture:** Introduce `src/sonari/session_stream.py` holding a plain `SessionStream` container. `SpeechDaemon` keeps a `self._streams: dict[str, SessionStream]` and a lazy `self._stream(session)` accessor. Every existing per-session read/write is rewritten to go through the stream, field group by field group, with the full test suite green after each group. `FLUSH` becomes `stream.reset_for_new_prompt()`; `SESSION_END` becomes `self._streams.pop(session)` (which incidentally fixes the documented `_assemblers`/`_nav_cursor` cleanup leak).

**Tech Stack:** Python 3.9+, stdlib only (no third-party imports in core). pytest. macOS/Windows core is OS-agnostic.

## Global Constraints

- **No third-party packages in `src/sonari/` core.** Stdlib only (the `[kokoro]` extra is the sole exception, and it is not touched here).
- **Python floor: 3.9** — no `match` statements; walrus (`:=`) is allowed but avoided here for clarity.
- **Behavior-preserving.** The full suite must stay green at every step: baseline is **693 passed, 2 skipped** via `python -m pytest -q --ignore=tests/test_kokoro.py` (the 2 skips + the ignored module need the `[kokoro]`/numpy extra, absent in this venv).
- **One sticky-vs-playback distinction must be preserved exactly:** `FLUSH` (new prompt) resets playback state (assembler, prose buffer, options, nav cursor, captured, open_msg) but **keeps** the sticky flags (`muted`, `warned_immediate`, `guided`). `SESSION_END` drops everything for that session.
- Work on a feature branch, not `main`. Commits only (no pushes) — pushing is a separate, user-initiated step.

---

## Step 0: Branch + baseline (do once, before Task 1)

- [ ] **Create the feature branch**

```bash
cd /Users/Nima.Hakimi/Projects/private/claude-tts
git checkout -b feat/session-stream-extract
```

- [ ] **Confirm the green baseline**

Run: `source .venv/bin/activate; python -m pytest -q --ignore=tests/test_kokoro.py`
Expected: `693 passed, 2 skipped`

---

## File Structure

- **Create:** `src/sonari/session_stream.py` — the `SessionStream` container + `reset_for_new_prompt()`. One responsibility: hold one session's speech state.
- **Create:** `tests/test_session_stream.py` — unit tests for the container.
- **Modify:** `src/sonari/daemon.py` — replace ten per-session containers with `self._streams` + `self._stream()`; rewrite every per-session reference.
- **Modify:** `tests/test_daemon_*.py` — no behavioral changes expected; only add the new cleanup-consistency tests in `tests/test_daemon_streams.py` (created in Task 3). If any existing test reaches into a removed attribute (e.g. `daemon._muted_sessions`), update it to the stream equivalent (Task 3 covers the audit).

---

## Task 1: `SessionStream` container

**Files:**
- Create: `src/sonari/session_stream.py`
- Test: `tests/test_session_stream.py`

**Interfaces:**
- Produces: `class SessionStream` with public attributes `assembler: ProseAssembler`, `prose_buffer: list`, `options: str | None`, `nav_cursor` (message id or `None`), `captured: bool`, `open_msg: bool`, `muted: bool`, `warned_immediate: bool`, `guided: bool`; and method `reset_for_new_prompt() -> None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_session_stream.py
from sonari.session_stream import SessionStream
from sonari.assembler import ProseAssembler


def test_defaults_are_empty_and_unflagged():
    s = SessionStream()
    assert isinstance(s.assembler, ProseAssembler)
    assert s.prose_buffer == []
    assert s.options is None
    assert s.nav_cursor is None
    assert s.captured is False
    assert s.open_msg is False
    assert s.muted is False
    assert s.warned_immediate is False
    assert s.guided is False


def test_reset_for_new_prompt_clears_playback_keeps_sticky():
    s = SessionStream()
    # playback state
    s.prose_buffer.append(("hi", object()))
    s.options = "Pick one"
    s.nav_cursor = 7
    s.captured = True
    s.open_msg = True
    old_assembler = s.assembler
    # sticky state
    s.muted = True
    s.warned_immediate = True
    s.guided = True

    s.reset_for_new_prompt()

    # playback reset
    assert s.prose_buffer == []
    assert s.options is None
    assert s.nav_cursor is None
    assert s.captured is False
    assert s.open_msg is False
    assert s.assembler is not old_assembler   # a fresh assembler
    # sticky preserved
    assert s.muted is True
    assert s.warned_immediate is True
    assert s.guided is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_session_stream.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sonari.session_stream'`

- [ ] **Step 3: Write the implementation**

```python
# src/sonari/session_stream.py
from __future__ import annotations

from sonari.assembler import ProseAssembler


class SessionStream:
    """All per-session speech state for one Claude Code session, in one place.

    Stage 1 of the per-session-streams redesign: a pure container that replaces
    the parallel per-session dicts/sets formerly held directly on SpeechDaemon.
    The speech queue stays shared in Stage 1; per-stream queues arrive in Stage 2.
    """

    def __init__(self) -> None:
        self.assembler = ProseAssembler()
        self.prose_buffer: list = []        # [(text, HistoryEntry)] awaiting minqueue flush
        self.options: "str | None" = None   # last decision text, for reread
        self.nav_cursor = None              # anchored message id (None == latest)
        self.captured = False               # message started while the voice was unavailable
        self.open_msg = False               # an assistant message is currently streaming
        self.muted = False                  # sticky per-session mute
        self.warned_immediate = False       # warned once about immediate selection
        self.guided = False                 # received the setup-guidance cue once

    def reset_for_new_prompt(self) -> None:
        """A new user prompt (FLUSH): reset playback state with a fresh assembler,
        but KEEP the sticky flags (muted / warned_immediate / guided), matching the
        current FLUSH handler exactly."""
        self.assembler = ProseAssembler()
        self.prose_buffer = []
        self.options = None
        self.nav_cursor = None
        self.captured = False
        self.open_msg = False
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_session_stream.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/sonari/session_stream.py tests/test_session_stream.py
git commit -m "feat: add SessionStream per-session state container"
```

---

## Task 2: Migrate `SpeechDaemon` onto `SessionStream`

**Files:**
- Modify: `src/sonari/daemon.py`

**Interfaces:**
- Consumes: `SessionStream` from Task 1.
- Produces: `self._streams: dict[str, SessionStream]` and `self._stream(session) -> SessionStream` on `SpeechDaemon`. Removes attributes `_assemblers`, `_prose_buffer`, `_options`, `_captured_msg`, `_open_msg`, `_nav_cursor`, `_muted_sessions`, `_warned_immediate`, `_guided_sessions`, and the `_assembler()` method.

> **Method this guards against:** migrate **one field group at a time, all references together**, and run the suite after each. Migrating half a field's references (a writer on the new flag, a reader still on the old set) creates an inconsistency that the suite will catch — so never split a field group across steps.

- [ ] **Step 1: Add the import, the `_streams` dict, and the accessor**

In `src/sonari/daemon.py`, add to the imports near the top (with the other `from sonari....` imports):

```python
from sonari.session_stream import SessionStream
```

In `__init__`, **add** (leave the old containers in place for now — they go in Step 7):

```python
        self._streams: "dict[str, SessionStream]" = {}
```

Add these methods next to `_assembler` (`_stream` will replace `_assembler`; `_owner_open` removes the duplicated owner-open check that `_claim_for_decision` and `_nav` both need):

```python
    def _stream(self, session: str) -> SessionStream:
        s = self._streams.get(session)
        if s is None:
            s = SessionStream()
            self._streams[session] = s
        return s

    def _owner_open(self) -> bool:
        """True if the current voice owner still has a streaming (open) message.
        Replaces the former `self._voice_owner not in self._open_msg` checks
        (open_msg only — NOT prose_buffer, unlike `_owner_mid_reply`)."""
        if self._voice_owner is None:
            return False
        st = self._streams.get(self._voice_owner)
        return st is not None and st.open_msg
```

Run: `python -m pytest -q --ignore=tests/test_kokoro.py` → Expected: `693 passed, 2 skipped` (new infra is unused; nothing changes).

- [ ] **Step 2: Migrate the `open_msg` field (all references together)**

Rewrite `_owner_mid_reply` (currently lines ~167–172) and `_claim_for_decision` (currently ~187–204) to read the stream, and convert the set sites:

```python
    def _owner_mid_reply(self, session) -> bool:
        if session is None:
            return False
        st = self._streams.get(session)
        if st is None:
            return False
        return st.open_msg or bool(st.prose_buffer)
```

```python
    def _claim_for_decision(self, session: str) -> bool:
        if self._voice_owner == session:
            return True
        if self.sessions.is_foreground(session) and not self._owner_open():
            self._voice_owner = session
            self._stream(session).captured = False
            return True
        return False
```

> Note: `_claim_for_decision` also touches `captured` (line ~202); it is handled here, together with `open_msg`, because the two are entangled in this method. The remaining `captured` sites move in Step 3.

Exact one-line conversions (set → flag):

| Location (current) | Before | After |
|---|---|---|
| PROSE non-final (~332) | `self._open_msg.add(session)` | `self._stream(session).open_msg = True` |
| PROSE final (~358) | `self._open_msg.discard(session)` | `self._stream(session).open_msg = False` |
| EARCON turn_done (~431) | `self._open_msg.discard(session)` | `self._stream(session).open_msg = False` |
| FLUSH (~449) | `self._open_msg.discard(session)` | `self._stream(session).open_msg = False` |
| SESSION_END (~468) | `self._open_msg.discard(session)` | `self._stream(session).open_msg = False` |
| `_nav` (~791) | `if self._voice_owner == session or self._voice_owner not in self._open_msg:` | `if self._voice_owner == session or not self._owner_open():` |

Run: `python -m pytest -q --ignore=tests/test_kokoro.py` → Expected: `693 passed, 2 skipped`.

- [ ] **Step 3: Migrate the `captured` field (all remaining references)**

Rewrite `_may_speak` (currently ~174–185):

```python
    def _may_speak(self, session: str) -> bool:
        if self._voice_owner == session:
            return True
        if (self._voice_owner is None
                and self.sessions.is_foreground(session)
                and not self._stream(session).captured):
            self._voice_owner = session
            return True
        return False
```

Exact one-line conversions:

| Location (current) | Before | After |
|---|---|---|
| PROSE not-spoken (~348) | `self._captured_msg.add(session)` | `self._stream(session).captured = True` |
| PROSE final (~357) | `self._captured_msg.discard(session)` | `self._stream(session).captured = False` |
| FLUSH (~448) | `self._captured_msg.discard(session)` | `self._stream(session).captured = False` |
| SESSION_END (~467) | `self._captured_msg.discard(session)` | `self._stream(session).captured = False` |
| `_nav` (~793) | `self._captured_msg.discard(session)` | `self._stream(session).captured = False` |

Run: `python -m pytest -q --ignore=tests/test_kokoro.py` → Expected: `693 passed, 2 skipped`.

- [ ] **Step 4: Migrate the `assembler` + `prose_buffer` fields**

Rewrite `_assembler` usage and the two prose-buffer helpers:

Replace the call at PROSE (~333) `a = self._assembler(session)` with `a = self._stream(session).assembler`.

```python
    def _buffer_prose(self, session: str, text: str, entry) -> None:
        st = self._stream(session)
        st.prose_buffer.append((text, entry))
        if len(st.prose_buffer) >= self._minqueue():
            self._flush_prose_buffer(session)

    def _flush_prose_buffer(self, session: str) -> None:
        st = self._stream(session)
        buf = st.prose_buffer
        if not buf:
            return
        st.prose_buffer = []
        for text, entry in buf:
            self._enqueue(session, "prose", text, False, entry=entry)
```

Exact one-line conversions:

| Location (current) | Before | After |
|---|---|---|
| FLUSH (~441) | `self._assemblers.pop(session, None)` | *(delete — folded into reset in Step 6)* |
| FLUSH (~442) | `self._prose_buffer.pop(session, None)` | `self._stream(session).prose_buffer = []` |
| SESSION_END (~463) | `self._prose_buffer.pop(session, None)` | `self._stream(session).prose_buffer = []` |

> The `_owner_mid_reply` reference to `prose_buffer` was already updated in Step 2. Leave the FLUSH assembler line deleted now; the fresh-assembler reset is added in Step 6.

Run: `python -m pytest -q --ignore=tests/test_kokoro.py` → Expected: `693 passed, 2 skipped`.

- [ ] **Step 5: Migrate the `options` + `nav_cursor` fields**

Rewrite the relevant lines of `_nav` (currently ~798/814/816):

| Location (current) | Before | After |
|---|---|---|
| `_nav` (~798) | `cur_id = self._nav_cursor.get(session)` | `cur_id = self._stream(session).nav_cursor` |
| `_nav` (~814) | `self._nav_cursor.pop(session, None)` | `self._stream(session).nav_cursor = None` |
| `_nav` (~816) | `self._nav_cursor[session] = ids[new]` | `self._stream(session).nav_cursor = ids[new]` |
| PROSE final (~359) | `self._options.pop(session, None)` | `self._stream(session).options = None` |
| CHOICE (~375) | `self._options[session] = text` | `self._stream(session).options = text` |
| PLAN (~388) | `self._options[session] = text` | `self._stream(session).options = text` |
| PERMISSION (~401) | `self._options[session] = text` | `self._stream(session).options = text` |
| REPEAT (~577) | `self._nav_cursor.pop(fg, None)` | `self._stream(fg).nav_cursor = None` |
| REREAD (~590) | `text = self._options.get(fg)` | `st = self._streams.get(fg)`<br>`text = st.options if st is not None else None` |
| FLUSH (~444) | `self._nav_cursor.pop(session, None)` | `self._stream(session).nav_cursor = None` |
| FLUSH (~450) | `self._options.pop(session, None)` | `self._stream(session).options = None` |
| SESSION_END (~469) | `self._options.pop(session, None)` | `self._stream(session).options = None` |

Run: `python -m pytest -q --ignore=tests/test_kokoro.py` → Expected: `693 passed, 2 skipped`.

- [ ] **Step 6: Migrate the sticky fields `muted` + `warned_immediate` + `guided`**

Rewrite `_selection_cue` (currently ~257–264) and `_maybe_guide_setup` (the two `_guided_sessions` lines):

```python
    def _selection_cue(self, session: str, verbosity: str) -> str:
        if verbosity != "everything":
            return ""
        cue = "Press the option's number to choose, or Escape to cancel."
        st = self._stream(session)
        if not st.warned_immediate:
            st.warned_immediate = True
            cue += " Selecting is immediate."
        return cue
```

In `_maybe_guide_setup`: `if session in self._guided_sessions:` → `if self._stream(session).guided:`; and `self._guided_sessions.add(session)` → `self._stream(session).guided = True`.

In the MUTE handler (~530–534):

```python
            st = self._stream(fg)
            if st.muted:
                st.muted = False
                self._enqueue(fg, "prose", "Session unmuted.", False)
            else:
                st.muted = True
                self._drop_pending(self.queue.flush_session(fg))
                cur = self._current_item
                if cur is not None and cur.session == fg:
                    self.speaker.cancel()
                self._enqueue(fg, "prose", "Session muted.", False, mute_exempt=True)
```

In `_speak_loop_once` (the mute check, ~915–917):

```python
            st = self._streams.get(item.session) if item is not None else None
            muted = (item is not None
                     and st is not None and st.muted
                     and not item.mute_exempt)
```

> `warned_immediate` / `guided` SESSION_END discards (~470/471) are left in place; they fold into the `_streams.pop` in Step 7.

Run: `python -m pytest -q --ignore=tests/test_kokoro.py` → Expected: `693 passed, 2 skipped`.

- [ ] **Step 7: Consolidate cleanup handlers + delete the old containers**

In the **FLUSH** handler, replace the per-field reset lines now reading `self._stream(session).X = ...` (the former lines ~442/444/448/449/450 and the deleted assembler line) with a single call, keeping the non-per-stream logic (`_drop_pending`/`flush_session`, cancel-current, `_voice_owner`, `history.reset`, `_paused.clear`, `_wake.set`):

```python
            self._stream(session).reset_for_new_prompt()
```

In the **SESSION_END** handler, replace the per-field clear lines now reading `self._stream(session).X = ...` (former lines ~463/467/468/469) and the discards (~470/471) with a single pop, keeping `_drop_pending(self.queue.flush_session(session))`, the `_voice_owner` release, and `history.reset(session)`:

```python
            self._streams.pop(session, None)
```

> This pop removes the whole stream — including `assembler` and `nav_cursor`, which the old `SESSION_END` never cleared. That **incidentally fixes the documented cleanup leak** (`daemon.py` per the spec §1/§4.3). It is safe: session ids are UUIDs and never recur, so no test depends on the leftover state. Task 3 adds a test asserting the full cleanup.

Delete the now-unused container declarations from `__init__` (former lines ~43, 53, 55, 59, 65, 67, 69, 71, 72): `_assemblers`, `_options`, `_captured_msg`, `_prose_buffer`, `_open_msg`, `_nav_cursor`, `_muted_sessions`, `_warned_immediate`, `_guided_sessions`. Delete the `_assembler` method (now replaced by `_stream(session).assembler`).

- [ ] **Step 8: Run the full suite green**

Run: `python -m pytest -q --ignore=tests/test_kokoro.py`
Expected: `693 passed, 2 skipped`

If a test fails with `AttributeError: 'SpeechDaemon' object has no attribute '_muted_sessions'` (or similar), that test reaches into a removed container — note it for Task 3's audit and convert it to the stream equivalent (e.g. `daemon._stream(s).muted`).

- [ ] **Step 9: Commit**

```bash
git add src/sonari/daemon.py
git commit -m "refactor: route per-session state through SessionStream"
```

---

## Task 3: Lock cleanup consistency + audit for stray container access

**Files:**
- Create: `tests/test_daemon_streams.py`
- Modify: any `tests/test_daemon_*.py` that referenced a removed container (only if Step 8 surfaced one).

**Interfaces:**
- Consumes: `make_daemon` from `tests/daemon_helpers.py`; `SessionStream` accessor `daemon._stream(session)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_daemon_streams.py
from sonari.protocol import MsgType, PROTOCOL_VERSION
from tests.daemon_helpers import make_daemon


def _msg(mtype, session, **extra):
    d = {"v": PROTOCOL_VERSION, "type": mtype, "session": session}
    d.update(extra)
    return d


def test_flush_resets_playback_but_keeps_mute():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    # mute A (sticky) and give it open/streaming + buffered state
    daemon.handle_message(_msg(MsgType.MUTE, "A"))
    daemon.handle_message(_msg(MsgType.PROSE, "A", delta="hello there. ", index=0, final=False))
    st = daemon._stream("A")
    assert st.muted is True
    assert st.open_msg is True

    daemon.handle_message(_msg(MsgType.FLUSH, "A"))

    st = daemon._stream("A")
    assert st.open_msg is False          # playback reset
    assert st.prose_buffer == []
    assert st.muted is True              # sticky preserved across a new prompt


def test_session_end_drops_the_whole_stream():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    daemon.handle_message(_msg(MsgType.PROSE, "A", delta="some text. ", index=0, final=False))
    assert "A" in daemon._streams

    daemon.handle_message(_msg(MsgType.SESSION_END, "A"))

    # whole stream gone — including assembler + nav_cursor, which the old
    # SESSION_END leaked (spec §4.3 cleanup-divergence fix).
    assert "A" not in daemon._streams


def test_no_legacy_per_session_containers_remain():
    daemon, *_ = make_daemon()
    for attr in ("_assemblers", "_prose_buffer", "_options", "_captured_msg",
                 "_open_msg", "_nav_cursor", "_muted_sessions",
                 "_warned_immediate", "_guided_sessions"):
        assert not hasattr(daemon, attr), f"legacy container {attr} still present"
```

- [ ] **Step 2: Run the tests to verify they pass**

Run: `python -m pytest tests/test_daemon_streams.py -v`
Expected: PASS (3 passed). (These assert the post-migration state, so they pass on the Task 2 code; if any fails, the migration missed a site — fix it in `daemon.py`.)

- [ ] **Step 3: Run the full suite once more**

Run: `python -m pytest -q --ignore=tests/test_kokoro.py`
Expected: `693 passed, 2 skipped`  → now `696 passed, 2 skipped` after the 3 new tests (plus the 2 from Task 1 = total reflects additions).

- [ ] **Step 4: Commit**

```bash
git add tests/test_daemon_streams.py
git commit -m "test: lock SessionStream cleanup consistency and leak fix"
```

---

## Self-Review (completed during authoring)

- **Spec coverage:** Stage 1 in spec §8 = "Extract `SessionStream` container; move per-session dicts into it; pure refactor; characterization tests guard it." Covered by Tasks 1–3. The spec's noted cleanup-divergence fix (§4.3) is realized in Task 2 Step 7 and asserted in Task 3.
- **Placeholder scan:** No TBD/TODO; every code step shows complete code or an exact before→after line.
- **Type/name consistency:** `SessionStream` field names (`assembler`, `prose_buffer`, `options`, `nav_cursor`, `captured`, `open_msg`, `muted`, `warned_immediate`, `guided`) and `reset_for_new_prompt()` are used identically in Tasks 2 and 3. The accessor is `_stream(session)` throughout.
- **Out of scope (correctly deferred):** per-stream queues (Stage 2), the multi-session policy flip, the REPEAT/catch_up dup fix (Stage 6), the Speaker cancel verification (Stage 7). Line numbers are marked "~" because earlier edits shift them; the grep map in the spec discussion is authoritative for locating each site.
