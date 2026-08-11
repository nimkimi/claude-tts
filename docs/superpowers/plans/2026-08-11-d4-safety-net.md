# D4 — Make the Safety Net Real: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `sonari doctor` an eyes-free, deep, honest safety net — and make `sonari uninstall` actually uninstall.

**Architecture:** `doctor()` keeps returning `(check, ok, detail)` 3-tuples (zero churn to 13 existing tests). Static per-check metadata — spoken name, warn-vs-fail class — lives in a side table, because those are properties of a *check*, not of a *run*. A pure `verdict()` folds rows into one sentence; a delivery layer speaks it daemon-first with a direct-`say` fallback. Uninstall gains a pinned teardown that stops the real process and proves it stopped.

**Tech Stack:** Python 3.9+, stdlib only (`socket`, `signal`, `subprocess`, `fcntl`). pytest. No new dependencies.

## Global Constraints

- **Spec:** `docs/superpowers/specs/2026-08-11-d4-safety-net-design.md`. Read §2 (verified facts) and §2.1 (correction of record) before starting — they prevent re-deriving things that are already settled or already false.
- **Branch:** `build/d4-safety-net`, off `main @ e92bfd2`. **Never push** — publishing is the owner's.
- **TDD, always.** Every task: write the failing test, *run it and see it fail for the stated reason*, implement minimally, see it pass, commit. A test that passes before implementation is not testing the change.
- **Gate after every task:** `.venv/bin/python -m pytest -q` → **1402 passed, 1 skipped** baseline, rising as tests are added. Never commit a red tree.
- **`doctor()` must never raise.** Every check body is individually `try/except`'d; a raising check becomes a `FAIL` row naming the exception.
- **All new spoken strings ship marked `PROVISIONAL`** with a `# PROVISIONAL (ear-batch-4)` comment on the same line, per D3's convention. Wording is not final and is not a review target.
- **Never write `state.json` semantics from memory** — read `daemon/persistence.py` first.
- **AI-trace hygiene:** no Claude/AI attribution in any commit message or file. `grep -rn` before the final task.
- **Do not touch:** `src/sonari/hooks_prime.py`, `scratchpad/` (both intentionally untracked).

## File Structure

| File | Responsibility | Tasks |
|---|---|---|
| `src/sonari/cli/checkmeta.py` | **New.** Static per-check metadata: spoken name + warn class. No logic. | T1 |
| `src/sonari/cli/verdict.py` | **New.** Pure `verdict(rows) -> str`. No I/O, no clock. | T2 |
| `src/sonari/cli/voiceout.py` | **New.** Delivery: tty gate, daemon-first send, direct-`say` fallback. | T4, T5 |
| `src/sonari/cli/doctor.py` | Check registry + `_cmd_doctor`. Gains 5 rows + supervision detail. | T3, T6–T12 |
| `src/sonari/daemon/faultcue.py` | **New.** Fire-once-per-class suppressor, re-armed by success. | T13 |
| `src/sonari/daemon/host.py` | `_signal_speak_failure`: cue wiring + #54's two gaps. | T14, T15 |
| `src/sonari/client.py` | `ensure_daemon` backoff + failure memo. | T16 |
| `src/sonari/platform/macos/supervisor.py` | Un-`DEVNULL` relaunch stderr; honour `unload` rc. | T17, T22 |
| `hooks/hooks.json` | `async: true` except `PermissionRequest`. | T18 |
| `bin/sonari-hook` | Install-record gate on `ensure_daemon`. | T19 |
| `src/sonari/cli/install.py` | Eared install summary; uninstall disclosure + teardown. | T20–T23 |
| `PRIVACY.md` | `state.json` disclosure. | T24 |

---

## Task 1: Static check metadata

**Files:**
- Create: `src/sonari/cli/checkmeta.py`
- Test: `tests/test_check_meta.py`

**Interfaces:**
- Produces: `spoken_name(check: str) -> str`, `is_warn(check: str) -> bool`.

Spoken names differ from printed names because printed names may be long and precise while spoken names must survive being read aloud in a list. `is_warn` marks checks whose failure is advisory — they print, but never make the verdict "unhealthy" and are never named aloud (spec §4, §5).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_check_meta.py
from sonari.cli import checkmeta


def test_known_check_has_a_short_spoken_name():
    assert checkmeta.spoken_name("SONARI_DIR writable") == "storage"


def test_unknown_check_falls_back_to_its_printed_name():
    assert checkmeta.spoken_name("some new row") == "some new row"


def test_neural_voices_is_advisory_not_a_failure():
    assert checkmeta.is_warn("neural voices") is True


def test_daemon_socket_is_a_hard_failure():
    assert checkmeta.is_warn("daemon socket") is False
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/test_check_meta.py -v`
Expected: `ModuleNotFoundError: No module named 'sonari.cli.checkmeta'`

- [ ] **Step 3: Implement**

```python
# src/sonari/cli/checkmeta.py
"""Static per-check metadata for the doctor registry.

Spoken names and warn-class are properties of a CHECK, not of a run, so they
live here rather than widening doctor()'s (check, ok, detail) row — which 13
existing tests unpack positionally.
"""
from __future__ import annotations

# Printed name -> short name that survives being read aloud in a list.
_SPOKEN = {
    "SONARI_DIR writable": "storage",
    "daemon socket": "daemon socket",
    "hooks installed": "hooks",
    "keymap resolves": "keymap",
    "neural voices": "neural voices",
    "python3": "python",
    "plugin path resolved": "plugin path",
    "speech path": "speech path",
    "restore health": "restore health",
    "hotkeyd": "hotkeyd",
    "fault log": "fault log",
    "reachability": "reachability",
}

# Checks whose failure is advisory: printed, but never spoken and never
# enough to call the whole system unhealthy.
_WARN = frozenset({"neural voices", "fault log"})


def spoken_name(check: str) -> str:
    """Short sayable name for *check*; falls back to the printed name."""
    return _SPOKEN.get(check, check)


def is_warn(check: str) -> bool:
    """True if a failure of *check* is advisory rather than unhealthy."""
    return check in _WARN
```

- [ ] **Step 4: Run it and watch it pass**

Run: `.venv/bin/python -m pytest tests/test_check_meta.py -v` → 4 passed.

- [ ] **Step 5: Full gate + commit**

```bash
.venv/bin/python -m pytest -q
git add src/sonari/cli/checkmeta.py tests/test_check_meta.py
git commit -m "feat(doctor): static per-check spoken names and warn class"
```

---

## Task 2: The verdict function

**Files:**
- Create: `src/sonari/cli/verdict.py`
- Test: `tests/test_verdict.py`

**Interfaces:**
- Consumes: `checkmeta.spoken_name`, `checkmeta.is_warn` (T1).
- Produces: `verdict(rows) -> str` where `rows` is the list of `(check, ok, detail)` tuples `doctor()` returns.

Pure and **total**: it must return a sentence for an empty list, an all-warn list, and an all-failed list. No I/O, no clock, no config. Counts in the sentence are whatever the registry produced — never a pinned literal (spec §5).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_verdict.py
from sonari.cli.verdict import verdict


def test_all_green_reports_healthy_and_the_count():
    rows = [("python3", True, "ok"), ("keymap resolves", True, "ok")]
    assert verdict(rows) == "Sonari is healthy. 2 checks passed."


def test_failures_are_named_with_their_spoken_names():
    rows = [("python3", True, "ok"),
            ("daemon socket", False, "not reachable"),
            ("SONARI_DIR writable", False, "not writable")]
    out = verdict(rows)
    assert out.startswith("Sonari is unhealthy. 2 checks failed:")
    assert "daemon socket" in out
    assert "storage" in out          # spoken name, not the printed one


def test_a_warn_row_neither_fails_the_verdict_nor_is_spoken():
    rows = [("python3", True, "ok"), ("neural voices", False, "venv broken")]
    out = verdict(rows)
    assert out.startswith("Sonari is healthy.")
    assert "neural" not in out


def test_empty_rows_still_produce_a_sentence():
    assert verdict([]) == "Sonari ran no checks."


def test_singular_wording_for_one_failure():
    rows = [("daemon socket", False, "down")]
    assert verdict(rows) == "Sonari is unhealthy. 1 check failed: daemon socket."
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/test_verdict.py -v`
Expected: `ModuleNotFoundError: No module named 'sonari.cli.verdict'`

- [ ] **Step 3: Implement**

```python
# src/sonari/cli/verdict.py
"""Rows -> one spoken sentence. Pure and total: no I/O, no clock, no config.

Failing checks are NAMED, not merely counted: the shipped rule forbids a
relaying session from glossing doctor, and a count-only verdict would gloss it
by ear instead. Enumeration is self-bounding — names appear only on failure.
"""
from __future__ import annotations

from sonari.cli import checkmeta

# PROVISIONAL (ear-batch-4) — every literal in this module.
_NONE = "Sonari ran no checks."
_HEALTHY = "Sonari is healthy. {n} check{s} passed."
_UNHEALTHY = "Sonari is unhealthy. {n} check{s} failed: {names}."


def verdict(rows) -> str:
    """Fold doctor rows into one sayable sentence."""
    rows = list(rows or [])
    if not rows:
        return _NONE
    failed = [c for c, ok, _ in rows if not ok and not checkmeta.is_warn(c)]
    if not failed:
        n = len(rows)
        return _HEALTHY.format(n=n, s="" if n == 1 else "s")
    n = len(failed)
    return _UNHEALTHY.format(
        n=n, s="" if n == 1 else "s",
        names=", ".join(checkmeta.spoken_name(c) for c in failed))
```

- [ ] **Step 4: Run it and watch it pass**

Run: `.venv/bin/python -m pytest tests/test_verdict.py -v` → 5 passed.

- [ ] **Step 5: Full gate + commit**

```bash
.venv/bin/python -m pytest -q
git add src/sonari/cli/verdict.py tests/test_verdict.py
git commit -m "feat(doctor): pure total verdict function"
```

---

## Task 3: `--speak` / `--quiet` flags and the tty rule

**Files:**
- Modify: `src/sonari/cli/__init__.py:117-118` (the `doctor` subparser)
- Modify: `src/sonari/cli/doctor.py` — **add `should_speak` at module level only.** `_cmd_doctor` is NOT touched in this task; Task 6 rewrites it to call `should_speak`.
- Test: `tests/test_doctor_speaks.py`

**Interfaces:**
- Produces: `doctor.should_speak(args) -> bool`.

Speak when `sys.stdout.isatty()`; silent when piped. `--speak` / `--quiet` override. **This is the first `isatty` guard in the codebase** — without it the 1402-test suite would start talking.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_doctor_speaks.py
import argparse
from unittest import mock

from sonari.cli import doctor as doctor_cmd


def _args(**kw):
    ns = argparse.Namespace(speak=False, quiet=False)
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def test_speaks_when_stdout_is_a_tty():
    with mock.patch("sys.stdout.isatty", return_value=True):
        assert doctor_cmd.should_speak(_args()) is True


def test_silent_when_piped():
    with mock.patch("sys.stdout.isatty", return_value=False):
        assert doctor_cmd.should_speak(_args()) is False


def test_speak_flag_overrides_a_pipe():
    with mock.patch("sys.stdout.isatty", return_value=False):
        assert doctor_cmd.should_speak(_args(speak=True)) is True


def test_quiet_flag_overrides_a_tty():
    with mock.patch("sys.stdout.isatty", return_value=True):
        assert doctor_cmd.should_speak(_args(quiet=True)) is False


def test_quiet_wins_if_both_given():
    with mock.patch("sys.stdout.isatty", return_value=True):
        assert doctor_cmd.should_speak(_args(speak=True, quiet=True)) is False
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/test_doctor_speaks.py -v`
Expected: `AttributeError: module 'sonari.cli.doctor' has no attribute 'should_speak'`

- [ ] **Step 3: Implement**

Add to `src/sonari/cli/doctor.py` (module level, after the imports):

```python
def should_speak(args) -> bool:
    """Speak when a human is at a terminal; stay silent when piped or scripted.

    The standard convention (git, ls, grep). It also keeps the test suite and
    every scripted invocation silent WITHOUT threading a flag through them.
    --quiet wins over --speak: the quieter reading of a contradictory command.
    """
    import sys
    if getattr(args, "quiet", False):
        return False
    if getattr(args, "speak", False):
        return True
    try:
        return bool(sys.stdout.isatty())
    except Exception:  # noqa: BLE001 - a detached stdout must not break doctor
        return False
```

In `src/sonari/cli/__init__.py`, replace line 117-118:

```python
    dp = sub.add_parser("doctor", help="run health checks")
    dp.add_argument("--speak", action="store_true",
                    help="speak the verdict even when output is piped")
    dp.add_argument("--quiet", action="store_true",
                    help="never speak the verdict")
    dp.set_defaults(func=doctor_cmd._cmd_doctor)
```

- [ ] **Step 4: Run it and watch it pass**

Run: `.venv/bin/python -m pytest tests/test_doctor_speaks.py -v` → 5 passed.

- [ ] **Step 5: Full gate + commit**

```bash
.venv/bin/python -m pytest -q
git add src/sonari/cli/doctor.py src/sonari/cli/__init__.py tests/test_doctor_speaks.py
git commit -m "feat(doctor): tty-gated speech with --speak/--quiet overrides"
```

---

## Task 4: The direct fallback voice

**Files:**
- Create: `src/sonari/cli/voiceout.py`
- Test: `tests/test_voiceout_direct.py`

**Interfaces:**
- Produces: `speak_direct(text: str) -> bool` — returns True iff `say` was spawned.

The last resort, used when the daemon cannot carry the verdict. Same reasoning as hotkeyd's witness alarm (`sonari-hotkeyd.swift:169-171`): *the daemon is the thing that just died, so nothing may route through it.* Must pass `--` before the text (`tts.py:194`) and must **never raise** — it has nothing to escalate to.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_voiceout_direct.py
from unittest import mock

from sonari.cli import voiceout


def test_spawns_say_with_the_option_terminator():
    with mock.patch("subprocess.Popen") as popen:
        assert voiceout.speak_direct("Sonari is unhealthy.") is True
    argv = popen.call_args[0][0]
    assert argv[0] == "say"
    assert "--" in argv
    # The text must come AFTER the terminator, so a leading '-' is not an option.
    assert argv.index("--") < argv.index("Sonari is unhealthy.")


def test_returns_false_and_never_raises_when_say_is_missing():
    with mock.patch("subprocess.Popen", side_effect=FileNotFoundError()):
        assert voiceout.speak_direct("anything") is False


def test_empty_text_is_not_spoken():
    with mock.patch("subprocess.Popen") as popen:
        assert voiceout.speak_direct("") is False
    popen.assert_not_called()
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/test_voiceout_direct.py -v`
Expected: `ModuleNotFoundError: No module named 'sonari.cli.voiceout'`

- [ ] **Step 3: Implement**

```python
# src/sonari/cli/voiceout.py
"""Delivery for CLI-originated speech (the doctor verdict, install/uninstall).

Daemon-first so the utterance obeys D8's atomic cue+speech contract and can
never interleave with live session speech; direct `say` when the daemon is the
thing being diagnosed. The direct path is the LAST resort: it is best-effort
and silent on its own failure, because it has nothing to escalate to.
"""
from __future__ import annotations

import subprocess


def speak_direct(text: str) -> bool:
    """Speak *text* with a raw `say`, bypassing the daemon. True iff spawned.

    `--` ends option parsing so a verdict starting with '-' is spoken rather
    than rejected as an unknown option (the tts.py:194 lesson). Never raises.
    """
    if not text:
        return False
    try:
        subprocess.Popen(["say", "--", text])
        return True
    except Exception:  # noqa: BLE001 - the last resort cannot itself escalate
        return False
```

- [ ] **Step 4: Run it and watch it pass**

Run: `.venv/bin/python -m pytest tests/test_voiceout_direct.py -v` → 3 passed.

- [ ] **Step 5: Full gate + commit**

```bash
.venv/bin/python -m pytest -q
git add src/sonari/cli/voiceout.py tests/test_voiceout_direct.py
git commit -m "feat(cli): direct say fallback for CLI-originated speech"
```

---

## Task 5: Daemon-first delivery with fallback

**Files:**
- Modify: `src/sonari/cli/voiceout.py`
- Test: `tests/test_voiceout_routing.py`

**Interfaces:**
- Consumes: `speak_direct` (T4).
- Produces: `speak(text: str, *, prefer_daemon: bool = True) -> str` returning `"daemon"`, `"direct"`, or `"silent"`.

Routing rule (spec §6): try the daemon; on any failure fall back to direct. Callers that already know the speech path is broken pass `prefer_daemon=False` to skip a pointless 2-second socket timeout.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_voiceout_routing.py
from unittest import mock

from sonari.cli import voiceout


def test_prefers_the_daemon_when_it_is_reachable():
    with mock.patch("sonari.client.send") as send, \
         mock.patch.object(voiceout, "speak_direct") as direct:
        assert voiceout.speak("hello") == "daemon"
    send.assert_called_once()
    direct.assert_not_called()


def test_falls_back_to_direct_when_the_daemon_is_unreachable():
    with mock.patch("sonari.client.send", side_effect=OSError("no daemon")), \
         mock.patch.object(voiceout, "speak_direct", return_value=True) as direct:
        assert voiceout.speak("hello") == "direct"
    direct.assert_called_once_with("hello")


def test_skips_the_daemon_entirely_when_the_caller_knows_it_is_broken():
    with mock.patch("sonari.client.send") as send, \
         mock.patch.object(voiceout, "speak_direct", return_value=True):
        assert voiceout.speak("hello", prefer_daemon=False) == "direct"
    send.assert_not_called()


def test_reports_silent_when_both_paths_fail():
    with mock.patch("sonari.client.send", side_effect=OSError()), \
         mock.patch.object(voiceout, "speak_direct", return_value=False):
        assert voiceout.speak("hello") == "silent"
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/test_voiceout_routing.py -v`
Expected: `AttributeError: module 'sonari.cli.voiceout' has no attribute 'speak'`

- [ ] **Step 3: Implement**

Append to `src/sonari/cli/voiceout.py`:

```python
def speak(text: str, *, prefer_daemon: bool = True) -> str:
    """Speak *text*, daemon-first. Returns "daemon" | "direct" | "silent".

    prefer_daemon=False is for callers that ALREADY know the speech path is
    broken (a red speech-path row, a stopped daemon) — it skips a pointless
    socket timeout rather than changing the policy.
    """
    if not text:
        return "silent"
    if prefer_daemon:
        try:
            from sonari import client
            from sonari.protocol import MsgType, PROTOCOL_VERSION
            client.send({"v": PROTOCOL_VERSION, "type": MsgType.PROSE,
                         "text": text})
            return "daemon"
        except Exception:  # noqa: BLE001 - any daemon failure means fall back
            pass
    return "direct" if speak_direct(text) else "silent"
```

- [ ] **Step 4: Run it and watch it pass**

Run: `.venv/bin/python -m pytest tests/test_voiceout_routing.py -v` → 4 passed.

- [ ] **Step 5: Full gate + commit**

```bash
.venv/bin/python -m pytest -q
git add src/sonari/cli/voiceout.py tests/test_voiceout_routing.py
git commit -m "feat(cli): daemon-first speech routing with direct fallback"
```

---

## Task 6: Speak the verdict from `sonari doctor`

**Files:**
- Modify: `src/sonari/cli/doctor.py:90-97` (`_cmd_doctor`)
- Test: `tests/test_doctor_verdict_delivery.py`

**Interfaces:**
- Consumes: `verdict` (T2), `should_speak` (T3), `voiceout.speak` (T5).

This is where **the verdict becomes its own end-to-end ear proof** (spec §6): if the sentence is heard, synthesis, playback and routing all just worked.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_doctor_verdict_delivery.py
from unittest import mock

from sonari import cli

_ROWS = [("say", True, "ok"), ("daemon socket", False, "down")]


def _run(speak: bool):
    with mock.patch("sonari.cli.doctor.doctor", return_value=_ROWS), \
         mock.patch("sonari.cli.doctor.should_speak", return_value=speak), \
         mock.patch("sonari.cli.voiceout.speak") as spoken:
        rc = cli.main(["doctor"])
    return rc, spoken


def test_speaks_the_verdict_when_interactive():
    rc, spoken = _run(True)
    assert rc == 1
    spoken.assert_called_once()
    assert "unhealthy" in spoken.call_args[0][0]


def test_says_nothing_when_not_interactive():
    rc, spoken = _run(False)
    assert rc == 1
    spoken.assert_not_called()


def test_rows_are_still_printed_when_speaking(capsys):
    with mock.patch("sonari.cli.doctor.doctor", return_value=_ROWS), \
         mock.patch("sonari.cli.doctor.should_speak", return_value=True), \
         mock.patch("sonari.cli.voiceout.speak"):
        cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "daemon socket" in out          # printed output is not replaced


def test_skips_the_daemon_when_the_speech_path_row_is_red():
    rows = [("speech path", False, "wedged")]
    with mock.patch("sonari.cli.doctor.doctor", return_value=rows), \
         mock.patch("sonari.cli.doctor.should_speak", return_value=True), \
         mock.patch("sonari.cli.voiceout.speak") as spoken:
        cli.main(["doctor"])
    assert spoken.call_args.kwargs["prefer_daemon"] is False
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/test_doctor_verdict_delivery.py -v`
Expected: `test_speaks_the_verdict_when_interactive` fails — `spoken.assert_called_once()` gets 0 calls, because `_cmd_doctor` only prints today.

- [ ] **Step 3: Implement**

Replace `_cmd_doctor` in `src/sonari/cli/doctor.py`:

```python
def _cmd_doctor(args=None) -> int:
    from sonari.cli import voiceout
    from sonari.cli.verdict import verdict

    rows = doctor()
    all_ok = True
    speech_path_ok = True
    for check, ok, detail in rows:
        mark = "ok " if ok else "FAIL"
        print(f"[{mark}] {check}: {detail}")
        all_ok = all_ok and ok
        if check == "speech path" and not ok:
            speech_path_ok = False

    if should_speak(args):
        # A red speech-path row means the daemon cannot carry the sentence;
        # go straight to the fallback rather than waiting out a socket timeout.
        voiceout.speak(verdict(rows), prefer_daemon=speech_path_ok)
    return 0 if all_ok else 1
```

- [ ] **Step 4: Run it and watch it pass**

Run: `.venv/bin/python -m pytest tests/test_doctor_verdict_delivery.py -v` → 4 passed.

- [ ] **Step 5: Full gate + commit**

```bash
.venv/bin/python -m pytest -q
git add src/sonari/cli/doctor.py tests/test_doctor_verdict_delivery.py
git commit -m "feat(doctor): speak the verdict; the sentence is its own ear proof"
```

---

## Task 7: The speech-path row — wedge vs idle

**Files:**
- Modify: `src/sonari/cli/doctor.py` (add row before the `hooks` row)
- Test: `tests/test_doctor_speech_path.py`

**Interfaces:**
- Produces: the `"speech path"` row.

**The whole point of D4's depth.** `PING` is answered by the socket thread, so a wedged speak-loop reports green. `STATUS` already returns everything needed (`features/control.py:298-330`): `last_drain_age_s` (monotonic) and `current_item`.

**The predicate:** a wedge is `current_item is True` **and** `last_drain_age_s > WEDGE_S`. An idle daemon has a huge `last_drain_age_s` and `current_item is False` — that is healthy, not wedged. Age alone would make every idle machine report a fault.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_doctor_speech_path.py
from unittest import mock

from sonari import cli
from tests._fakeplatform import fake_platform, FakeSupervisor, FakeHotkey


def _rows(status):
    pb = fake_platform(supervisor=FakeSupervisor(), hotkey=FakeHotkey())
    with mock.patch.object(cli, "_platform", lambda: pb), \
         mock.patch("sonari.client.send", return_value=status):
        return {n: (ok, d) for n, ok, d in cli.doctor.doctor()}


def test_idle_daemon_is_healthy_however_long_it_has_been_quiet():
    ok, detail = _rows({"ok": True, "current_item": False,
                        "last_drain_age_s": 86400.0})["speech path"]
    assert ok is True
    assert "idle" in detail


def test_a_claimed_item_that_never_drains_is_a_wedge():
    ok, detail = _rows({"ok": True, "current_item": True,
                        "last_drain_age_s": 900.0})["speech path"]
    assert ok is False
    assert "wedged" in detail


def test_a_claimed_item_draining_normally_is_healthy():
    ok, _ = _rows({"ok": True, "current_item": True,
                   "last_drain_age_s": 0.5})["speech path"]
    assert ok is True


def test_wedge_is_reported_even_though_the_socket_answers():
    """The exact lie D4 kills: the daemon replies, so 'daemon socket' is green,
    while the speech path is dead. If both rows agree, this test is worthless."""
    rows = _rows({"ok": True, "current_item": True, "last_drain_age_s": 900.0})
    assert rows["daemon socket"][0] is True
    assert rows["speech path"][0] is False


def test_unreachable_daemon_makes_the_row_fail_without_raising():
    with mock.patch("sonari.client.send", side_effect=OSError("down")):
        pb = fake_platform(supervisor=FakeSupervisor(), hotkey=FakeHotkey())
        with mock.patch.object(cli, "_platform", lambda: pb):
            rows = {n: (ok, d) for n, ok, d in cli.doctor.doctor()}
    assert rows["speech path"][0] is False
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/test_doctor_speech_path.py -v`
Expected: `KeyError: 'speech path'` on every test.

- [ ] **Step 3: Implement**

Add to `src/sonari/cli/doctor.py`, after the `daemon socket` block. Note it reuses the STATUS reply rather than adding a message type:

```python
    # Speech-path liveness. PING is answered by the socket thread, so a wedged
    # speak loop still reports "reachable" — this row is the one that can tell
    # a wedge from silence. STATUS already carries both facts we need.
    WEDGE_S = 120.0
    try:
        from sonari import client
        st = client.send({"v": PROTOCOL_VERSION, "type": MsgType.STATUS},
                         expect_reply=True) or {}
        age = st.get("last_drain_age_s")
        claimed = bool(st.get("current_item"))
        if not claimed:
            results.append(("speech path", True,
                            "idle (nothing claimed by the speak loop)"))
        elif age is not None and age > WEDGE_S:
            results.append(("speech path", False,
                            f"wedged: an utterance has been claimed for "
                            f"{age:.0f}s without draining"))
        else:
            results.append(("speech path", True, "draining normally"))
    except Exception as exc:  # noqa: BLE001 - doctor must never raise
        results.append(("speech path", False, f"cannot read daemon status: {exc}"))
```

- [ ] **Step 4: Run it and watch it pass**

Run: `.venv/bin/python -m pytest tests/test_doctor_speech_path.py -v` → 5 passed.

- [ ] **Step 5: Full gate + commit**

```bash
.venv/bin/python -m pytest -q
git add src/sonari/cli/doctor.py tests/test_doctor_speech_path.py
git commit -m "feat(doctor): speech-path row distinguishes a wedge from silence"
```

---

## Task 8: The restore-health row (P17)

**Files:**
- Modify: `src/sonari/cli/doctor.py`
- Test: `tests/test_doctor_restore_health.py`

**Interfaces:**
- Produces: the `"restore health"` row.

**Read `src/sonari/daemon/persistence.py` first** — take `STATE_VERSION`, the filename and the envelope shape from the code, never from memory. Today you can lose the entire backlog to the SP6 crash/upgrade path and still get an all-green doctor.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_doctor_restore_health.py
import json
from unittest import mock

from sonari import cli
from sonari.daemon import persistence
from tests._fakeplatform import fake_platform, FakeSupervisor, FakeHotkey


def _rows(tmp_path, payload=None, write=True):
    state = tmp_path / "state.json"
    if write:
        state.write_text(json.dumps(payload), encoding="utf-8")
    pb = fake_platform(supervisor=FakeSupervisor(), hotkey=FakeHotkey())
    with mock.patch.object(cli, "_platform", lambda: pb), \
         mock.patch("sonari.paths.STATE_PATH", state), \
         mock.patch("sonari.client.send", return_value={"ok": True}):
        return {n: (ok, d) for n, ok, d in cli.doctor.doctor()}


def test_absent_state_is_reported_but_not_a_failure(tmp_path):
    ok, detail = _rows(tmp_path, write=False)["restore health"]
    assert ok is True
    assert "no saved state" in detail


def test_unparseable_state_fails_loudly(tmp_path):
    state = tmp_path / "state.json"
    state.write_text("{not json", encoding="utf-8")
    pb = fake_platform(supervisor=FakeSupervisor(), hotkey=FakeHotkey())
    with mock.patch.object(cli, "_platform", lambda: pb), \
         mock.patch("sonari.paths.STATE_PATH", state), \
         mock.patch("sonari.client.send", return_value={"ok": True}):
        rows = {n: (ok, d) for n, ok, d in cli.doctor.doctor()}
    assert rows["restore health"][0] is False


def test_version_mismatch_warns_that_the_pile_will_be_dropped(tmp_path):
    bad = {"version": persistence.STATE_VERSION + 1, "sessions": {}}
    ok, detail = _rows(tmp_path, bad)["restore health"]
    assert ok is False
    assert "dropped" in detail


def test_healthy_state_reports_the_session_count(tmp_path):
    good = {"version": persistence.STATE_VERSION,
            "sessions": {"a": {}, "b": {}}}
    ok, detail = _rows(tmp_path, good)["restore health"]
    assert ok is True
    assert "2" in detail
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/test_doctor_restore_health.py -v`
Expected: `KeyError: 'restore health'`.

- [ ] **Step 3: Implement**

First confirm the real names: `grep -n "STATE_VERSION\|STATE_PATH\|def load_state" src/sonari/daemon/persistence.py src/sonari/paths.py`. If `STATE_PATH` is not exported from `paths`, add it there rather than rebuilding the path inline. Then:

```python
    # Restore health (P17): you can lose the whole backlog to the crash/upgrade
    # path and still get an all-green doctor today.
    try:
        from sonari.daemon import persistence
        state_path = paths.STATE_PATH
        if not os.path.exists(str(state_path)):
            results.append(("restore health", True,
                            "no saved state yet (nothing to restore)"))
        else:
            import json as _json
            with open(str(state_path), "r", encoding="utf-8") as fh:
                blob = _json.load(fh)
            ver = blob.get("version")
            n = len(blob.get("sessions") or {})
            age_h = (time.time() - os.path.getmtime(str(state_path))) / 3600.0
            if ver != persistence.STATE_VERSION:
                results.append(("restore health", False,
                                f"state version {ver} != {persistence.STATE_VERSION}; "
                                f"the restored pile will be dropped at next boot"))
            else:
                results.append(("restore health", True,
                                f"{n} session(s), saved {age_h:.1f}h ago"))
    except Exception as exc:  # noqa: BLE001 - doctor must never raise
        results.append(("restore health", False, f"unreadable: {exc}"))
```

Add `import time` at the top of `doctor.py` if absent.

- [ ] **Step 4: Run it and watch it pass**

Run: `.venv/bin/python -m pytest tests/test_doctor_restore_health.py -v` → 4 passed.

- [ ] **Step 5: Full gate + commit**

```bash
.venv/bin/python -m pytest -q
git add src/sonari/cli/doctor.py tests/test_doctor_restore_health.py
git commit -m "feat(doctor): restore-health row closes P17"
```

---

## Task 9: The hotkeyd row — liveness, and the witness is armed

**Files:**
- Modify: `src/sonari/platform/macos/supervisor.py` (`doctor_rows`, near the existing `hotkeyd_loaded` check at :326)
- Test: `tests/test_macos_supervisor_hotkeyd_row.py`

**Interfaces:**
- Produces: the `"hotkeyd"` row.

Closes **R1**: today doctor checks hotkeyd's static *presence*; the watchdog itself is unwatched. Two facts matter — the process is alive, and its witness alarm is **armed** (`alarmEnabled` true, `alarmAsset` readable). A hotkeyd running with a disabled or unplayable alarm is a watchdog that cannot bark.

Read `hotkeyd/sonari-hotkeyd.swift:167-190` for the config shape and `paths.HOTKEYD_RESOLVED_PATH` for where `witness_config` is written.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_macos_supervisor_hotkeyd_row.py
import json
from unittest import mock

import pytest

from sonari.platform.macos import supervisor as sup_mod

pytestmark = pytest.mark.skipif(not hasattr(sup_mod, "MacSupervisor"),
                                reason="macOS supervisor only")


def _row(tmp_path, loaded=True, witness=None):
    resolved = tmp_path / "hotkeyd.resolved.json"
    if witness is not None:
        resolved.write_text(json.dumps({"witness_config": witness}),
                            encoding="utf-8")
    sup = sup_mod.MacSupervisor()
    with mock.patch.object(sup, "launchctl", return_value=0 if loaded else 1), \
         mock.patch("sonari.paths.HOTKEYD_RESOLVED_PATH", resolved):
        rows = {n: (ok, d) for n, ok, d in sup.doctor_rows()}
    return rows["hotkeyd"]


def test_not_loaded_is_a_failure(tmp_path):
    ok, detail = _row(tmp_path, loaded=False, witness={"alarmEnabled": True})
    assert ok is False
    assert "not running" in detail


def test_running_with_the_alarm_disabled_is_a_failure(tmp_path):
    """A watchdog that cannot bark is worse than none — it looks like cover."""
    ok, detail = _row(tmp_path, witness={"alarmEnabled": False})
    assert ok is False
    assert "alarm" in detail


def test_running_and_armed_is_healthy(tmp_path):
    asset = tmp_path / "Hero.aiff"
    asset.write_bytes(b"x")
    ok, detail = _row(tmp_path, witness={"alarmEnabled": True,
                                         "alarmAsset": str(asset)})
    assert ok is True
    assert "armed" in detail


def test_missing_resolved_file_still_reports_armed_compiled_defaults(tmp_path):
    """sonari-hotkeyd.swift:172-177 compiles in defaults precisely so a stale or
    missing resolved file cannot silently disable the alarm."""
    ok, _ = _row(tmp_path, witness=None)
    assert ok is True
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/test_macos_supervisor_hotkeyd_row.py -v`
Expected: `KeyError: 'hotkeyd'`.

- [ ] **Step 3: Implement**

In `doctor_rows`, replace the bare `hotkeyd_loaded` row with:

```python
        # R1: the watchdog must itself be watched. Presence is not enough —
        # a hotkeyd whose alarm is disabled or unplayable cannot bark.
        hotkeyd_loaded = self.launchctl(["list", HOTKEYD_LAUNCH_AGENT_LABEL]) == 0
        if not hotkeyd_loaded:
            rows.append(("hotkeyd", False,
                         "not running — no independent alarm if the daemon dies"))
        else:
            enabled, asset = True, None      # compiled-in defaults (swift:175-177)
            try:
                with open(str(paths.HOTKEYD_RESOLVED_PATH), "r",
                          encoding="utf-8") as fh:
                    wc = (json.load(fh) or {}).get("witness_config") or {}
                enabled = bool(wc.get("alarmEnabled", True))
                asset = wc.get("alarmAsset")
            except (OSError, ValueError):
                pass                          # no resolved file -> defaults apply
            if not enabled:
                rows.append(("hotkeyd", False,
                             "running, but its alarm is disabled — the daemon "
                             "could die silently"))
            elif asset and not os.path.exists(asset):
                rows.append(("hotkeyd", False,
                             f"running, but its alarm sound is missing: {asset}"))
            else:
                rows.append(("hotkeyd", True, "running, alarm armed"))
```

Ensure `import json` and `import os` are present in `supervisor.py`.

- [ ] **Step 4: Run it and watch it pass**

Run: `.venv/bin/python -m pytest tests/test_macos_supervisor_hotkeyd_row.py -v` → 4 passed.

- [ ] **Step 5: Full gate + commit**

```bash
.venv/bin/python -m pytest -q
git add src/sonari/platform/macos/supervisor.py tests/test_macos_supervisor_hotkeyd_row.py
git commit -m "feat(doctor): hotkeyd liveness + witness-armed row closes R1"
```

---

## Task 10: The fault-log row

**Files:**
- Modify: `src/sonari/cli/doctor.py`
- Test: `tests/test_doctor_fault_log.py`

**Interfaces:**
- Produces: the `"fault log"` row (marked `warn` in T1 — a past crash is history, not a current fault).

`_arm_faulthandler` (`bootstrap.py:27-49`) writes `=== faulthandler armed: pid N ===` then opens mode `'w'`, so the file holds only the current boot. Anything **after** the arming line is a native crash dump.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_doctor_fault_log.py
from unittest import mock

from sonari import cli
from tests._fakeplatform import fake_platform, FakeSupervisor, FakeHotkey


def _row(tmp_path, contents=None):
    log = tmp_path / "faulthandler.log"
    if contents is not None:
        log.write_text(contents, encoding="utf-8")
    pb = fake_platform(supervisor=FakeSupervisor(), hotkey=FakeHotkey())
    with mock.patch.object(cli, "_platform", lambda: pb), \
         mock.patch("sonari.paths.FAULTLOG_PATH", log), \
         mock.patch("sonari.client.send", return_value={"ok": True}):
        return {n: (ok, d) for n, ok, d in cli.doctor.doctor()}["fault log"]


def test_no_log_is_clean(tmp_path):
    ok, detail = _row(tmp_path)
    assert ok is True and "no crash" in detail


def test_armed_line_only_is_clean(tmp_path):
    ok, _ = _row(tmp_path, "=== faulthandler armed: pid 42 ===\n")
    assert ok is True


def test_a_dump_after_the_armed_line_is_reported(tmp_path):
    ok, detail = _row(tmp_path,
                      "=== faulthandler armed: pid 42 ===\n"
                      "Current thread 0x00007ff8 (most recent call first):\n"
                      '  File "tts.py", line 194 in speak\n')
    assert ok is False
    assert "crash" in detail
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/test_doctor_fault_log.py -v`
Expected: `KeyError: 'fault log'`.

- [ ] **Step 3: Implement**

Add `FAULTLOG_PATH = SONARI_DIR / "faulthandler.log"` to `paths.py` (it is currently built inline in two places — `bootstrap.py:40` and `cli/install.py:161`; point both at the new constant in this task so the path has one home). Then in `doctor.py`:

```python
    # Did the daemon die natively since it last armed? bootstrap.py opens the
    # log mode 'w', so anything after the arming line belongs to THIS boot.
    try:
        fl = str(paths.FAULTLOG_PATH)
        if not os.path.exists(fl):
            results.append(("fault log", True, "no crash log"))
        else:
            with open(fl, "r", encoding="utf-8", errors="replace") as fh:
                body = fh.read()
            after = body.split("===", 2)[-1] if "===" in body else body
            if after.strip():
                results.append(("fault log", False,
                                f"a native crash was recorded — see {fl}"))
            else:
                results.append(("fault log", True, "armed, no crash recorded"))
    except Exception as exc:  # noqa: BLE001 - doctor must never raise
        results.append(("fault log", False, f"unreadable: {exc}"))
```

- [ ] **Step 4: Run it and watch it pass**

Run: `.venv/bin/python -m pytest tests/test_doctor_fault_log.py -v` → 3 passed.

- [ ] **Step 5: Full gate + commit**

```bash
.venv/bin/python -m pytest -q
git add src/sonari/paths.py src/sonari/daemon/bootstrap.py src/sonari/cli/install.py src/sonari/cli/doctor.py tests/test_doctor_fault_log.py
git commit -m "feat(doctor): fault-log row; give the log path one home"
```

---

## Task 11: The reachability row

**Files:**
- Modify: `src/sonari/cli/doctor.py`
- Test: `tests/test_doctor_reachability.py`

**Interfaces:**
- Produces: the `"reachability"` row — is `sonari` on `PATH`?

Closes the R10 half that bites eyes-free users: the launcher exists at `~/.local/bin/sonari` but that directory is not on `PATH`, so every documented command fails with "command not found". `supervisor._local_bin_on_path()` (`:30`) already answers this.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_doctor_reachability.py
from unittest import mock

from sonari import cli
from tests._fakeplatform import fake_platform, FakeSupervisor, FakeHotkey


def _row(on_path):
    sup = FakeSupervisor()
    sup.reachability_row = lambda: (
        ("reachability", True, "sonari is on your PATH") if on_path
        else ("reachability", False,
              "~/.local/bin is not on your PATH — 'sonari' will not run"))
    pb = fake_platform(supervisor=sup, hotkey=FakeHotkey())
    with mock.patch.object(cli, "_platform", lambda: pb), \
         mock.patch("sonari.client.send", return_value={"ok": True}):
        return {n: (ok, d) for n, ok, d in cli.doctor.doctor()}["reachability"]


def test_on_path_is_healthy():
    assert _row(True)[0] is True


def test_off_path_fails_and_names_the_directory():
    ok, detail = _row(False)
    assert ok is False
    assert ".local/bin" in detail
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/test_doctor_reachability.py -v`
Expected: `KeyError: 'reachability'`.

- [ ] **Step 3: Implement**

Add to `MacSupervisor` in `supervisor.py`:

```python
    def reachability_row(self) -> tuple:
        """Is the `sonari` launcher actually runnable from a shell?

        The launcher can exist while ~/.local/bin is off PATH — every command
        in the docs then fails with 'command not found', which reads as
        "Sonari is broken" rather than "your PATH is short".
        """
        if not os.path.exists(_launcher_path()):
            return ("reachability", False,
                    "no launcher installed — run: sonari install")
        if not _local_bin_on_path():
            return ("reachability", False,
                    f"{_local_bin_dir()} is not on your PATH — "
                    f"'sonari' will not run from a new shell")
        return ("reachability", True, "sonari is on your PATH")
```

Add the same method to `FakeSupervisor` in `tests/_fakeplatform.py` returning `("reachability", True, "ok")`, and call it in `doctor()`:

```python
    results.append(_platform().supervisor.reachability_row())
```

- [ ] **Step 4: Run it and watch it pass**

Run: `.venv/bin/python -m pytest tests/test_doctor_reachability.py -v` → 2 passed.

- [ ] **Step 5: Full gate + commit**

```bash
.venv/bin/python -m pytest -q
git add src/sonari/platform/macos/supervisor.py src/sonari/cli/doctor.py tests/_fakeplatform.py tests/test_doctor_reachability.py
git commit -m "feat(doctor): reachability row — the launcher exists but is it runnable"
```

---

## Task 12: Supervision detail — launchd job or detached orphan

**Files:**
- Modify: `src/sonari/cli/doctor.py` (the `daemon socket` row)
- Test: `tests/test_doctor_supervision.py`

**Interfaces:**
- Extends the `daemon socket` row's *detail* string. Row stays `(check, ok, detail)`.

Makes §1's orphan visible. A daemon spawned by `ensure_running` is `start_new_session=True` and **not a launchd job**, so `launchctl` cannot stop it. It works — so this is advisory, carried in the detail rather than failing the row.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_doctor_supervision.py
from unittest import mock

from sonari import cli
from tests._fakeplatform import fake_platform, FakeSupervisor, FakeHotkey


def _detail(job_loaded):
    sup = FakeSupervisor()
    sup.daemon_is_launchd_job = lambda: job_loaded
    pb = fake_platform(supervisor=sup, hotkey=FakeHotkey())
    with mock.patch.object(cli, "_platform", lambda: pb), \
         mock.patch("sonari.client.send", return_value={"ok": True}):
        rows = {n: (ok, d) for n, ok, d in cli.doctor.doctor()}
    return rows["daemon socket"]


def test_supervised_daemon_says_so():
    ok, detail = _detail(True)
    assert ok is True
    assert "launchd" in detail


def test_orphan_is_named_but_does_not_fail_the_row():
    ok, detail = _detail(False)
    assert ok is True                      # it works; it just cannot be stopped
    assert "orphan" in detail
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/test_doctor_supervision.py -v`
Expected: both fail — the detail is `"reachable"` with no supervision word.

- [ ] **Step 3: Implement**

Add to `MacSupervisor`:

```python
    def daemon_is_launchd_job(self) -> bool:
        """True if launchd is supervising the speech daemon."""
        return self.launchctl(["list", LAUNCH_AGENT_LABEL]) == 0
```

Add to `FakeSupervisor`: `def daemon_is_launchd_job(self): return True`.

In `doctor()`, replace the reachable detail:

```python
        if ok:
            supervised = _platform().supervisor.daemon_is_launchd_job()
            detail = ("reachable (supervised by launchd)" if supervised else
                      "reachable, but running as a detached orphan — "
                      "'launchctl' cannot stop it")
            results.append(("daemon socket", True, detail))
        else:
            results.append(("daemon socket", False, "no ok reply from daemon"))
```

- [ ] **Step 4: Run it and watch it pass**

Run: `.venv/bin/python -m pytest tests/test_doctor_supervision.py -v` → 2 passed.

- [ ] **Step 5: Full gate + commit**

```bash
.venv/bin/python -m pytest -q
git add src/sonari/platform/macos/supervisor.py src/sonari/cli/doctor.py tests/_fakeplatform.py tests/test_doctor_supervision.py
git commit -m "feat(doctor): name a detached-orphan daemon in the socket row"
```

---

## Task 13: Pin the side-effect-free invariant

**Files:**
- Test: `tests/test_doctor_no_side_effects.py` (test-only task)

**Interfaces:** none.

Per spec §2.1 this is a **pin, not a fix** — doctor already never relaunches, because `client.send` connects-or-raises and `ensure_daemon`'s only caller is `bin/sonari-hook:79`. The pin stops a future edit from quietly reintroducing a self-healing probe.

**Verify the pin has teeth:** temporarily insert `client.ensure_daemon()` at the top of `doctor()`, confirm the test FAILS, then remove it. A pin that cannot fail is decoration.

- [ ] **Step 1: Write the test**

```python
# tests/test_doctor_no_side_effects.py
"""doctor() observes; it never repairs.

A diagnostic that restarts what it measures cannot measure it, and it would
resurrect a daemon the user just uninstalled. This is an INVARIANT PIN: the
property already holds (spec 2.1), and this test keeps it holding.
"""
from unittest import mock

from sonari import cli
from tests._fakeplatform import fake_platform, FakeSupervisor, FakeHotkey


def test_doctor_spawns_no_processes():
    pb = fake_platform(supervisor=FakeSupervisor(), hotkey=FakeHotkey())
    with mock.patch.object(cli, "_platform", lambda: pb), \
         mock.patch("sonari.client.send", return_value={"ok": True}), \
         mock.patch("subprocess.Popen") as popen:
        cli.doctor.doctor()
    assert popen.call_count == 0, (
        f"doctor spawned {popen.call_count} process(es); it must only observe")


def test_doctor_never_calls_ensure_running():
    pb = fake_platform(supervisor=FakeSupervisor(), hotkey=FakeHotkey())
    with mock.patch.object(cli, "_platform", lambda: pb), \
         mock.patch("sonari.client.send", return_value={"ok": True}), \
         mock.patch("sonari.daemon.ensure_running") as ensure:
        cli.doctor.doctor()
    ensure.assert_not_called()
```

- [ ] **Step 2: Prove the pin bites**

Temporarily add `from sonari import client; client.ensure_daemon()` as the first line of `doctor()`.
Run: `.venv/bin/python -m pytest tests/test_doctor_no_side_effects.py -v`
Expected: **both tests FAIL.** Now remove the temporary line and re-run — both pass. Record this in the task report.

- [ ] **Step 3: Full gate + commit**

```bash
.venv/bin/python -m pytest -q
git add tests/test_doctor_no_side_effects.py
git commit -m "test(doctor): pin the observe-never-repair invariant"
```

---

## Task 14: The fire-once failure-cue suppressor

**Files:**
- Create: `src/sonari/daemon/faultcue.py`
- Test: `tests/test_faultcue.py`

**Interfaces:**
- Produces: `class FaultCue` with `should_fire(cls: str) -> bool` and `note_success(cls: str) -> None`.

Mirrors the shipped witness pattern (`sonari-hotkeyd.swift:167-173`): sound **once** per failure class, re-arm only after a later success. Not a new suppression model — the same one, in Python.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_faultcue.py
from sonari.daemon.faultcue import FaultCue


def test_first_failure_of_a_class_fires():
    assert FaultCue().should_fire("speak") is True


def test_repeat_failures_stay_quiet():
    fc = FaultCue()
    assert fc.should_fire("speak") is True
    assert fc.should_fire("speak") is False
    assert fc.should_fire("speak") is False


def test_a_success_re_arms_the_class():
    fc = FaultCue()
    fc.should_fire("speak")
    fc.note_success("speak")
    assert fc.should_fire("speak") is True


def test_classes_are_independent():
    fc = FaultCue()
    fc.should_fire("speak")
    assert fc.should_fire("earcon") is True


def test_success_for_one_class_does_not_re_arm_another():
    fc = FaultCue()
    fc.should_fire("speak")
    fc.note_success("earcon")
    assert fc.should_fire("speak") is False
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/test_faultcue.py -v`
Expected: `ModuleNotFoundError: No module named 'sonari.daemon.faultcue'`

- [ ] **Step 3: Implement**

```python
# src/sonari/daemon/faultcue.py
"""Fire-once-per-failure-class cue suppression, re-armed by a later success.

The same discipline hotkeyd's witness already uses (sonari-hotkeyd.swift:167-173).
Without it a repeating fault becomes a repeating nag, the user mutes it, and the
signal is gone — the failure mode that costs the whole cue.

Thread-safe: failure signalling runs on the speak thread and on handler threads.
"""
from __future__ import annotations

import threading


class FaultCue:
    def __init__(self) -> None:
        self._fired = set()
        self._lock = threading.Lock()

    def should_fire(self, cls: str) -> bool:
        """True the first time *cls* fails; False until a success re-arms it."""
        with self._lock:
            if cls in self._fired:
                return False
            self._fired.add(cls)
            return True

    def note_success(self, cls: str) -> None:
        """A success for *cls* — the next failure may speak again."""
        with self._lock:
            self._fired.discard(cls)
```

- [ ] **Step 4: Run it and watch it pass**

Run: `.venv/bin/python -m pytest tests/test_faultcue.py -v` → 5 passed.

- [ ] **Step 5: Full gate + commit**

```bash
.venv/bin/python -m pytest -q
git add src/sonari/daemon/faultcue.py tests/test_faultcue.py
git commit -m "feat(daemon): fire-once failure-cue suppressor, re-armed by success"
```

---

## Task 15: Wire the cue + close #54's two gaps

**Files:**
- Modify: `src/sonari/daemon/host.py:908-932` (`_signal_speak_failure`), and `__init__` near `:191` to hold a `FaultCue`
- Test: `tests/test_speak_failure_cue.py`

**Interfaces:**
- Consumes: `FaultCue` (T14), `voiceout.speak_direct` (T4).

Three changes in one place:
1. **#54 gap A** — the session-less branch (`:924`) speaks a word instead of a bare tone.
2. **#54 gap B** — when the failure *is* the TTS path, the word cannot go through it; use `speak_direct`.
3. **The "try doctor" cue** — appended once per failure class.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_speak_failure_cue.py
from unittest import mock


def test_sessionless_failure_speaks_a_word_not_just_a_tone(daemon):
    """#54 gap A: host.py:924 fired a BARE tone when no session was known."""
    with mock.patch.object(daemon, "cue") as cue:
        try:
            raise RuntimeError("synth died")
        except RuntimeError:
            daemon._signal_speak_failure(None)
    assert cue.call_args.kwargs.get("word"), "session-less failure had no word"


def test_try_doctor_is_suggested_once_then_suppressed(daemon):
    with mock.patch.object(daemon, "cue") as cue:
        for _ in range(3):
            try:
                raise RuntimeError("synth died")
            except RuntimeError:
                daemon._signal_speak_failure(None)
    words = " ".join(str(c.kwargs.get("word", "")) for c in cue.call_args_list)
    assert words.count("doctor") == 1, "the doctor hint nagged"


def test_a_later_success_re_arms_the_hint(daemon):
    with mock.patch.object(daemon, "cue") as cue:
        try:
            raise RuntimeError("x")
        except RuntimeError:
            daemon._signal_speak_failure(None)
        daemon._faultcue.note_success("speak")
        try:
            raise RuntimeError("x")
        except RuntimeError:
            daemon._signal_speak_failure(None)
    words = " ".join(str(c.kwargs.get("word", "")) for c in cue.call_args_list)
    assert words.count("doctor") == 2


def test_falls_back_to_direct_say_when_the_cue_itself_cannot_speak(daemon):
    """#54 gap B: the word was routed through the TTS path that just failed."""
    with mock.patch.object(daemon, "cue", side_effect=RuntimeError("tts down")), \
         mock.patch("sonari.cli.voiceout.speak_direct") as direct:
        try:
            raise RuntimeError("synth died")
        except RuntimeError:
            daemon._signal_speak_failure(None)
    direct.assert_called_once()
```

The `daemon` fixture: reuse whatever existing host tests use — check `tests/conftest.py` for a built `SpeechDaemon`. If none exists, build one the way the nearest existing host test does; **do not invent a new construction path.**

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/test_speak_failure_cue.py -v`
Expected: test 1 fails (no `word` kwarg on the session-less branch); tests 2–4 fail (no hint, no `_faultcue`, no fallback).

- [ ] **Step 3: Implement**

In `SpeechDaemon.__init__` (near `:195`):

```python
        # Fire-once-per-class suppression for failure hints (D4).
        from sonari.daemon.faultcue import FaultCue
        self._faultcue = FaultCue()
```

Replace the body of `_signal_speak_failure`:

```python
        hint = ""
        if self._faultcue.should_fire("speak"):
            hint = " Things seem off — try sonari doctor."  # PROVISIONAL (ear-batch-4)
        spoken = False
        try:
            if session is not None:
                with self._lock:
                    self.cue("error_system", word=SPEAK_FAILURE_WORD + hint,
                             session=session)
            else:
                # #54 gap A: this branch used to fire a BARE tone. An eyes-free
                # user got a sound with no account of what failed.
                self.cue("error_system", word=SPEAK_FAILURE_WORD + hint)
            spoken = True
        except Exception:  # noqa: BLE001 - signaling must not wedge the loop
            pass
        if not spoken:
            # #54 gap B: the word was routed through the very TTS path that
            # just failed. Same reasoning as hotkeyd's raw shell-out alarm.
            try:
                from sonari.cli import voiceout
                voiceout.speak_direct(SPEAK_FAILURE_WORD + hint)
            except Exception:  # noqa: BLE001 - last resort; nothing to escalate to
                pass
```

Keep the existing `traceback.print_exc(file=sys.stderr)` block below, unchanged.

- [ ] **Step 4: Run it and watch it pass**

Run: `.venv/bin/python -m pytest tests/test_speak_failure_cue.py -v` → 4 passed.

- [ ] **Step 5: Full gate + commit**

```bash
.venv/bin/python -m pytest -q
git add src/sonari/daemon/host.py tests/test_speak_failure_cue.py
git commit -m "feat(daemon): try-doctor hint + close #54's bare-tone and fallback gaps"
```

---

## Task 16: `ensure_daemon` backoff and failure memo

**Files:**
- Modify: `src/sonari/client.py:43-52`
- Test: `tests/test_client_ensure_backoff.py` (existing `tests/test_client_ensure.py` must stay green)

**Interfaces:**
- Produces: `ensure_daemon(timeout=3.0)` with exponential backoff and a short-lived failure memo.

Today: a fixed 50 ms poll for 3 s, with no memory. On a broken install **every** qualifying hook event pays the full 3 s. The memo makes it one timeout, not one per event.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_client_ensure_backoff.py
from unittest import mock

from sonari import client as client_mod


def test_polling_backs_off_rather_than_hammering_at_50ms():
    sleeps = []
    with mock.patch.object(client_mod, "_connectable", return_value=False), \
         mock.patch.object(client_mod, "ensure_running"), \
         mock.patch("time.sleep", side_effect=lambda s: sleeps.append(s)):
        client_mod.reset_failure_memo()
        client_mod.ensure_daemon(timeout=1.0)
    assert len(sleeps) >= 2
    assert sleeps == sorted(sleeps), f"intervals did not grow: {sleeps}"
    assert sleeps[-1] > sleeps[0]


def test_a_recent_failure_short_circuits_the_next_call():
    with mock.patch.object(client_mod, "_connectable", return_value=False), \
         mock.patch.object(client_mod, "ensure_running") as spawn, \
         mock.patch("time.sleep"):
        client_mod.reset_failure_memo()
        client_mod.ensure_daemon(timeout=1.0)
        assert spawn.call_count == 1
        client_mod.ensure_daemon(timeout=1.0)   # memo still hot
        assert spawn.call_count == 1, "respawned despite a fresh failure memo"


def test_the_memo_expires_so_recovery_is_still_possible():
    with mock.patch.object(client_mod, "_connectable", return_value=False), \
         mock.patch.object(client_mod, "ensure_running") as spawn, \
         mock.patch("time.sleep"):
        client_mod.reset_failure_memo()
        client_mod.ensure_daemon(timeout=1.0)
        client_mod._FAILED_AT = 0.0          # simulate an expired memo
        client_mod.ensure_daemon(timeout=1.0)
        assert spawn.call_count == 2


def test_success_clears_the_memo():
    with mock.patch.object(client_mod, "_connectable", return_value=True):
        client_mod.reset_failure_memo()
        client_mod.ensure_daemon(timeout=1.0)
    assert client_mod._FAILED_AT == 0.0
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/test_client_ensure_backoff.py -v`
Expected: `AttributeError: module 'sonari.client' has no attribute 'reset_failure_memo'`

- [ ] **Step 3: Implement**

Replace `ensure_daemon` in `client.py`:

```python
# A broken install must cost ONE timeout, not one per hook event. Short enough
# that a real recovery (sonari install) is noticed within a few seconds.
_MEMO_S = 30.0
_FAILED_AT = 0.0


def reset_failure_memo() -> None:
    """Forget any recorded spawn failure (test seam + explicit recovery)."""
    global _FAILED_AT
    _FAILED_AT = 0.0


def ensure_daemon(timeout: float = 3.0) -> None:
    global _FAILED_AT
    if _connectable():
        _FAILED_AT = 0.0
        return
    if _FAILED_AT and (time.time() - _FAILED_AT) < _MEMO_S:
        return          # we just tried and failed; don't pay the timeout again
    ensure_running()
    deadline = time.time() + timeout
    delay = 0.02
    while time.time() < deadline:
        if _connectable():
            _FAILED_AT = 0.0
            return
        time.sleep(delay)
        delay = min(delay * 1.6, 0.25)
    _FAILED_AT = time.time()
```

- [ ] **Step 4: Run it and watch it pass**

Run: `.venv/bin/python -m pytest tests/test_client_ensure_backoff.py tests/test_client_ensure.py -v` → all pass.

- [ ] **Step 5: Full gate + commit**

```bash
.venv/bin/python -m pytest -q
git add src/sonari/client.py tests/test_client_ensure_backoff.py
git commit -m "perf(client): back off and memo ensure_daemon failures"
```

---

## Task 17: Stop discarding the relaunch's stderr

**Files:**
- Modify: `src/sonari/platform/macos/supervisor.py:164-170` (`launch_spec`)
- Test: `tests/test_macos_launch_spec.py`

**Interfaces:**
- `launch_spec()` still returns `(argv, kwargs)`; `stderr` becomes an open file handle.

`host.py:930` faithfully writes a traceback to `sys.stderr` — and `:170` sends it to `DEVNULL`. Every lazily-spawned daemon's faults are unrecoverable. This also gives T10's fault-log row something to report.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_macos_launch_spec.py
import subprocess
from unittest import mock

import pytest

from sonari.platform.macos import supervisor as sup_mod

pytestmark = pytest.mark.skipif(not hasattr(sup_mod, "MacSupervisor"),
                                reason="macOS supervisor only")


def test_relaunch_stderr_is_captured_not_discarded(tmp_path):
    log = tmp_path / "daemon.err.log"
    with mock.patch("sonari.paths.DAEMON_ERR_PATH", log):
        _, kwargs = sup_mod.MacSupervisor().launch_spec()
    assert kwargs["stderr"] is not subprocess.DEVNULL
    try:
        kwargs["stderr"].write("boom\n")
    finally:
        kwargs["stderr"].close()
    assert "boom" in log.read_text(encoding="utf-8")


def test_stdin_stays_devnull(tmp_path):
    with mock.patch("sonari.paths.DAEMON_ERR_PATH", tmp_path / "e.log"):
        _, kwargs = sup_mod.MacSupervisor().launch_spec()
    assert kwargs["stdin"] is subprocess.DEVNULL


def test_an_unwritable_log_falls_back_to_devnull(tmp_path):
    """Diagnostics must never prevent the daemon from starting."""
    with mock.patch("sonari.paths.DAEMON_ERR_PATH", tmp_path / "no" / "such" / "e.log"):
        _, kwargs = sup_mod.MacSupervisor().launch_spec()
    assert kwargs["stderr"] is subprocess.DEVNULL
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/test_macos_launch_spec.py -v`
Expected: test 1 fails — `stderr` *is* `DEVNULL`.

- [ ] **Step 3: Implement**

Add `DAEMON_ERR_PATH = SONARI_DIR / "daemon.err.log"` to `paths.py`. Then:

```python
    def launch_spec(self):
        """Return (argv, spawn_kwargs) to lazily start the daemon process.

        stderr goes to a real file, not DEVNULL: host.py's failure handler
        writes tracebacks there, and discarding them made every lazily-spawned
        daemon's faults unrecoverable. Mode 'w' — only the latest run matters,
        so the log cannot grow unbounded (the faulthandler.log discipline).
        """
        shim = os.path.join(paths.repo_root(), "bin", "sonari-daemon")
        try:
            err = open(str(paths.DAEMON_ERR_PATH), "w", encoding="utf-8")
        except OSError:
            err = subprocess.DEVNULL   # diagnostics must never block startup
        return ([shim], {"start_new_session": True,
                         "stdin": subprocess.DEVNULL,
                         "stdout": subprocess.DEVNULL,
                         "stderr": err})
```

Add `DAEMON_ERR_PATH` to uninstall's artifact list in `cli/install.py:155-162`.

- [ ] **Step 4: Run it and watch it pass**

Run: `.venv/bin/python -m pytest tests/test_macos_launch_spec.py -v` → 3 passed.

- [ ] **Step 5: Full gate + commit**

```bash
.venv/bin/python -m pytest -q
git add src/sonari/paths.py src/sonari/platform/macos/supervisor.py src/sonari/cli/install.py tests/test_macos_launch_spec.py
git commit -m "fix(supervisor): capture the lazy relaunch's stderr instead of discarding it"
```

---

## Task 18: Async hook registrations

**Files:**
- Modify: `hooks/hooks.json`
- Test: `tests/test_hooks_async.py`

**Interfaces:** none (data file).

Every registration is synchronous today, so each qualifying event blocks Claude Code for the duration. Only `PermissionRequest` returns a decision — it must stay synchronous. **Read `hooks/hooks.json` fully first**; do not assume its shape.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_hooks_async.py
import json
import pathlib

HOOKS = pathlib.Path(__file__).resolve().parents[1] / "hooks" / "hooks.json"


def _registrations():
    blob = json.loads(HOOKS.read_text(encoding="utf-8"))
    for event, entries in (blob.get("hooks") or {}).items():
        for entry in entries:
            for hook in entry.get("hooks", []):
                yield event, hook


def test_permission_request_stays_synchronous():
    for event, hook in _registrations():
        if event == "PermissionRequest":
            assert hook.get("async") is not True, (
                "PermissionRequest returns a decision; it cannot be async")


def test_every_other_registration_is_async():
    for event, hook in _registrations():
        if event != "PermissionRequest":
            assert hook.get("async") is True, f"{event} still blocks the session"


def test_at_least_one_of_each_kind_exists():
    events = {e for e, _ in _registrations()}
    assert "PermissionRequest" in events
    assert events - {"PermissionRequest"}
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/test_hooks_async.py -v`
Expected: `test_every_other_registration_is_async` fails — no `async` keys exist.

- [ ] **Step 3: Implement**

Add `"async": true` to every hook object in `hooks/hooks.json` **except** those under `PermissionRequest`. Preserve existing key order and formatting; change nothing else.

- [ ] **Step 4: Run it and watch it pass**

Run: `.venv/bin/python -m pytest tests/test_hooks_async.py -v` → 3 passed.
Also run `.venv/bin/python -m pytest tests/test_manifests.py -q` — the manifest tests read this file.

- [ ] **Step 5: Full gate + commit**

```bash
.venv/bin/python -m pytest -q
git add hooks/hooks.json tests/test_hooks_async.py
git commit -m "perf(hooks): async registrations except the answering PermissionRequest"
```

---

## Task 19: Close the hook-resurrection path

**Files:**
- Modify: `bin/sonari-hook:78-81`
- Test: `tests/test_hook_install_gate.py`

**Interfaces:** none.

**Spec §8.1 step 5.** `bin/sonari-hook:79` calls `ensure_daemon()` unconditionally, so after `sonari uninstall` the next hook event **respawns** the daemon from the plugin's own `src/`. The gate: still `send()` to an already-reachable daemon, never **spawn** one for an uninstalled Sonari.

> **Gate on TWO signals, not one.** The obvious gate — "spawn only if the install record exists" — is **wrong and dangerous**. `INSTALL_RECORD_PATH` can be legitimately absent while Sonari is fully installed (doctor's own plugin-path row says `install.json missing ... (run 'sonari install')`, which is a *recoverable* state, not an uninstalled one). A record-only gate would silently kill lazy relaunch for those users: the daemon dies, nothing resurrects it, and Sonari goes mute until a manual `sonari install` — **the exact silent-death class D4 exists to close, introduced by D4.**
>
> Uninstall removes **both** the record **and** `APP_DIR` (`cli/install.py:155-177`). So suppress the spawn only when **both are gone**; if either survives, Sonari is installed and lazy relaunch must still work.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_hook_install_gate.py
"""bin/sonari-hook must not resurrect the daemon after uninstall."""
import runpy
import sys
from unittest import mock

import pytest

HOOK = "bin/sonari-hook"


def _run_hook(monkeypatch, tmp_path, record_exists, app_dir_exists, stdin=b"{}"):
    # A REAL directory rather than a global os.path.isdir patch — runpy imports
    # a lot, and a blanket isdir patch would perturb unrelated import machinery.
    app_dir = tmp_path / "app"
    if app_dir_exists:
        app_dir.mkdir()
    monkeypatch.setattr(sys, "argv", ["sonari-hook", "Notification"])
    monkeypatch.setattr(sys, "stdin", mock.MagicMock(buffer=mock.MagicMock(
        read=lambda: stdin)))
    with mock.patch("sonari.client.ensure_daemon") as ensure, \
         mock.patch("sonari.client.send"), \
         mock.patch("sonari.install_record.read_install_record",
                    return_value={"app_path": "/a"} if record_exists else None), \
         mock.patch("sonari.paths.APP_DIR", app_dir), \
         mock.patch("sonari.hooks_entry.handle_event",
                    return_value=[{"type": "prose", "text": "hi"}]):
        runpy.run_path(HOOK, run_name="__main__")
        return ensure


def test_no_spawn_only_when_BOTH_signals_say_uninstalled(monkeypatch, tmp_path):
    ensure = _run_hook(monkeypatch, tmp_path, record_exists=False,
                       app_dir_exists=False)
    ensure.assert_not_called()


def test_spawn_is_allowed_when_installed(monkeypatch, tmp_path):
    ensure = _run_hook(monkeypatch, tmp_path, record_exists=True,
                       app_dir_exists=True)
    ensure.assert_called_once()


def test_a_missing_record_alone_must_NOT_disable_lazy_relaunch(monkeypatch, tmp_path):
    """THE case that protects live users. install.json can go missing while
    Sonari is fully installed (doctor treats it as recoverable: "run sonari
    install"). Suppressing the spawn here would make the daemon's death
    permanent and silent — the exact defect D4 exists to close."""
    ensure = _run_hook(monkeypatch, tmp_path, record_exists=False,
                       app_dir_exists=True)
    ensure.assert_called_once()


def test_a_missing_app_dir_alone_still_spawns(monkeypatch, tmp_path):
    ensure = _run_hook(monkeypatch, tmp_path, record_exists=True,
                       app_dir_exists=False)
    ensure.assert_called_once()


def test_messages_are_still_sent_when_uninstalled_but_reachable(monkeypatch):
    """A daemon that is still up keeps receiving events; we only refuse to
    START one. Refusing to send would silence a working Sonari."""
    monkeypatch.setattr(sys, "argv", ["sonari-hook", "Notification"])
    monkeypatch.setattr(sys, "stdin", mock.MagicMock(buffer=mock.MagicMock(
        read=lambda: b"{}")))
    with mock.patch("sonari.client.ensure_daemon"), \
         mock.patch("sonari.client.send") as send, \
         mock.patch("sonari.install_record.read_install_record", return_value=None), \
         mock.patch("sonari.hooks_entry.handle_event",
                    return_value=[{"type": "prose", "text": "hi"}]):
        runpy.run_path(HOOK, run_name="__main__")
    send.assert_called_once()
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/test_hook_install_gate.py -v`
Expected: `test_no_spawn_when_sonari_is_uninstalled` fails — `ensure_daemon` is called unconditionally.

- [ ] **Step 3: Implement**

Replace lines 78-81 of `bin/sonari-hook`:

```python
    # Only START a daemon for an INSTALLED Sonari. After `sonari uninstall` the
    # plugin's hooks keep firing until the user disables the plugin by hand; an
    # unconditional ensure_daemon() respawned the daemon from this very src/ —
    # and every resurrection was born a detached orphan launchctl cannot stop.
    # We still send() below, so a daemon that is genuinely up keeps working.
    #
    # TWO signals, both of which uninstall removes. A missing install record
    # ALONE is a recoverable state, not an uninstall (doctor says "run sonari
    # install"), and treating it as one would make a dead daemon permanently
    # and silently dead — the very failure D4 exists to close.
    try:
        import os as _os
        from sonari import install_record, paths as _paths
        installed = bool(install_record.read_install_record()) or \
            _os.path.isdir(str(_paths.APP_DIR))
        if installed:
            client.ensure_daemon()
    except Exception:
        pass
```

- [ ] **Step 4: Run it and watch it pass**

Run: `.venv/bin/python -m pytest tests/test_hook_install_gate.py -v` → 3 passed.

- [ ] **Step 5: Full gate + commit**

```bash
.venv/bin/python -m pytest -q
git add bin/sonari-hook tests/test_hook_install_gate.py
git commit -m "fix(hook): never spawn a daemon for an uninstalled Sonari"
```

---

## Task 20: Uninstall stops the daemon and proves it

**Files:**
- Modify: `src/sonari/cli/install.py:141-150`
- Create: `src/sonari/cli/teardown.py`
- Test: `tests/test_uninstall_teardown.py`

**Interfaces:**
- Produces: `teardown.stop_daemon(timeout: float = 5.0) -> str` returning `"not-running"`, `"stopped"`, or `"still-running"`.

**Spec §8.1 steps 2–4.** Read `pid` from the lockfile **before** deleting it (`transport.py:18-20`), SIGTERM (the handler at `host.py:1346-1361` exits into SP6's clean shutdown flush, so state is preserved), then acquire `SINGLETON_PATH`'s flock as **positive proof** of death.

> **`SINGLETON_PATH` must never be added to uninstall's artifact list.** It is deliberately absent today. A `flock` is held against an *open file description*, so deleting and recreating the path orphans the daemon's lock — `_singleton_free()` would then acquire a lock on a brand-new inode and report "stopped" while the daemon is very much alive. T17 adds `DAEMON_ERR_PATH` to that list; do not sweep the singleton in alongside it as another "log-like file".

- [ ] **Step 1: Write the failing test**

```python
# tests/test_uninstall_teardown.py
import json
import os
import signal
from unittest import mock

from sonari.cli import teardown


def _lockfile(tmp_path, pid=4242):
    p = tmp_path / "daemon.lock"
    p.write_text(json.dumps({"host": "127.0.0.1", "port": 1, "token": "t",
                             "pid": pid}), encoding="utf-8")
    return p


def test_no_lockfile_means_nothing_to_stop(tmp_path):
    with mock.patch("sonari.paths.LOCK_PATH", tmp_path / "absent.lock"):
        assert teardown.stop_daemon() == "not-running"


def test_sigterm_is_sent_to_the_pid_from_the_lockfile(tmp_path):
    lock = _lockfile(tmp_path, pid=4242)
    with mock.patch("sonari.paths.LOCK_PATH", lock), \
         mock.patch("os.kill") as kill, \
         mock.patch.object(teardown, "_singleton_free", return_value=True):
        assert teardown.stop_daemon() == "stopped"
    kill.assert_called_once_with(4242, signal.SIGTERM)


def test_sigterm_not_sigkill_so_the_shutdown_flush_runs(tmp_path):
    """host.py:1346-1361 turns SIGTERM into SP6's clean flush. SIGKILL would
    truncate the pile the user is about to be asked about."""
    lock = _lockfile(tmp_path)
    with mock.patch("sonari.paths.LOCK_PATH", lock), \
         mock.patch("os.kill") as kill, \
         mock.patch.object(teardown, "_singleton_free", return_value=True):
        teardown.stop_daemon()
    assert signal.SIGKILL not in [c[0][1] for c in kill.call_args_list]


def test_a_survivor_is_reported_not_papered_over(tmp_path):
    lock = _lockfile(tmp_path)
    with mock.patch("sonari.paths.LOCK_PATH", lock), \
         mock.patch("os.kill"), \
         mock.patch.object(teardown, "_singleton_free", return_value=False), \
         mock.patch("time.sleep"):
        assert teardown.stop_daemon(timeout=0.1) == "still-running"


def test_an_already_dead_pid_is_not_an_error(tmp_path):
    lock = _lockfile(tmp_path)
    with mock.patch("sonari.paths.LOCK_PATH", lock), \
         mock.patch("os.kill", side_effect=ProcessLookupError()), \
         mock.patch.object(teardown, "_singleton_free", return_value=True):
        assert teardown.stop_daemon() == "stopped"
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/test_uninstall_teardown.py -v`
Expected: `ModuleNotFoundError: No module named 'sonari.cli.teardown'`

- [ ] **Step 3: Implement**

```python
# src/sonari/cli/teardown.py
"""Stop the running daemon during uninstall — and PROVE it stopped.

launchctl unload cannot stop a daemon that ensure_running() spawned with
start_new_session=True: it is not a launchd job. The lockfile is the only
record of its pid, and uninstall deletes it — so read the pid FIRST.

Proof of death is the singleton flock, not the absence of a lockfile: the
daemon holds SINGLETON_PATH for its whole process lifetime, so acquiring it
means no daemon survives.
"""
from __future__ import annotations

import json
import os
import signal
import time

from sonari import paths


def _read_pid():
    try:
        with open(str(paths.LOCK_PATH), "r", encoding="utf-8") as fh:
            return int(json.load(fh)["pid"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _singleton_free() -> bool:
    """True if SINGLETON_PATH's flock is acquirable — i.e. no daemon holds it."""
    from sonari.platform import transport
    held = transport.acquire_singleton(paths.SINGLETON_PATH)
    if held is None:
        return False
    try:
        held.close()          # release immediately; we only wanted the answer
    except OSError:
        pass
    return True


def stop_daemon(timeout: float = 5.0) -> str:
    """SIGTERM the running daemon and wait for proof it died.

    Returns "not-running" | "stopped" | "still-running".
    SIGTERM (never SIGKILL): host.py's handler exits into SP6's shutdown flush,
    so the pile survives the stop we are about to disclose to the user.
    """
    pid = _read_pid()
    if pid is None:
        return "not-running" if _singleton_free() else "still-running"
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass                                  # already gone
    except OSError:
        return "still-running"
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _singleton_free():
            return "stopped"
        time.sleep(0.1)
    return "still-running"
```

Wire it into `uninstall()` **after** `sup.uninstall()` (so launchd cannot restart it) and **before** the artifact loop that deletes `LOCK_PATH`:

```python
    from sonari.cli import teardown
    outcome = teardown.stop_daemon()
    if outcome == "still-running":
        print("warning: the Sonari daemon is STILL RUNNING and could not be "
              "stopped; it will exit at logout.")
    elif outcome == "stopped":
        print("Stopped the Sonari daemon.")
```

- [ ] **Step 4: Run it and watch it pass**

Run: `.venv/bin/python -m pytest tests/test_uninstall_teardown.py -v` → 5 passed.

- [ ] **Step 5: Full gate + commit**

```bash
.venv/bin/python -m pytest -q
git add src/sonari/cli/teardown.py src/sonari/cli/install.py tests/test_uninstall_teardown.py
git commit -m "fix(uninstall): stop the daemon by lockfile pid and prove it stopped"
```

---

## Task 21: Honour `launchctl unload`'s return code

**Files:**
- Modify: `src/sonari/platform/macos/supervisor.py:224-243`
- Test: `tests/test_macos_uninstall_rc.py`

**Interfaces:** none.

Today "Removed LaunchAgent" prints when the **file delete** succeeds, regardless of whether the process actually stopped (`:228-231`). The rc is discarded.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_macos_uninstall_rc.py
from unittest import mock

import pytest

from sonari.platform.macos import supervisor as sup_mod

pytestmark = pytest.mark.skipif(not hasattr(sup_mod, "MacSupervisor"),
                                reason="macOS supervisor only")


def test_a_failed_unload_is_surfaced_not_swallowed(capsys, tmp_path):
    plist = tmp_path / "agent.plist"
    plist.write_text("<plist/>", encoding="utf-8")
    sup = sup_mod.MacSupervisor()
    with mock.patch.object(sup_mod, "LAUNCH_AGENT_PATH", str(plist)), \
         mock.patch.object(sup, "launchctl", return_value=1):
        sup.uninstall()
    out = capsys.readouterr().out
    assert "warning" in out.lower()
    assert "Removed LaunchAgent" not in out


def test_a_clean_unload_still_reports_removal(capsys, tmp_path):
    plist = tmp_path / "agent.plist"
    plist.write_text("<plist/>", encoding="utf-8")
    sup = sup_mod.MacSupervisor()
    with mock.patch.object(sup_mod, "LAUNCH_AGENT_PATH", str(plist)), \
         mock.patch.object(sup, "launchctl", return_value=0):
        sup.uninstall()
    assert "Removed LaunchAgent" in capsys.readouterr().out
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/test_macos_uninstall_rc.py -v`
Expected: test 1 fails — "Removed LaunchAgent" prints even with rc=1.

- [ ] **Step 3: Implement**

```python
        if os.path.exists(LAUNCH_AGENT_PATH):
            rc = self.launchctl(["unload", LAUNCH_AGENT_PATH])
            try:
                os.remove(LAUNCH_AGENT_PATH)
            except OSError as exc:
                print("warning: could not remove {0}: {1}".format(
                    LAUNCH_AGENT_PATH, exc))
            else:
                # The rc is the only evidence the PROCESS stopped; a file delete
                # proves nothing. Reporting removal on the delete alone was the lie.
                if rc == 0:
                    print("Removed LaunchAgent: {0}".format(LAUNCH_AGENT_PATH))
                else:
                    print("warning: 'launchctl unload' returned {0} — the plist "
                          "was removed but the daemon may still be running."
                          .format(rc))
```

- [ ] **Step 4: Run it and watch it pass**

Run: `.venv/bin/python -m pytest tests/test_macos_uninstall_rc.py -v` → 2 passed.

- [ ] **Step 5: Full gate + commit**

```bash
.venv/bin/python -m pytest -q
git add src/sonari/platform/macos/supervisor.py tests/test_macos_uninstall_rc.py
git commit -m "fix(uninstall): report removal on the unload rc, not the file delete"
```

---

## Task 22: The spoken `state.json` disclosure

**Files:**
- Modify: `src/sonari/cli/install.py` (`uninstall`), `src/sonari/cli/__init__.py` (uninstall subparser)
- Test: `tests/test_uninstall_disclosure.py`

**Interfaces:**
- Produces: `install.transcript_summary() -> tuple[int, int]` (sessions, utterances); `--purge-transcripts` / `--keep-transcripts`.

**Spec §8.1 step 1: ask FIRST**, while the daemon can still carry the question. Default with no tty and no flag is **keep** — silence must never destroy data.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_uninstall_disclosure.py
import json
from unittest import mock

from sonari.cli import install as install_cmd


def _state(tmp_path, sessions=2, per=3):
    p = tmp_path / "state.json"
    p.write_text(json.dumps({"version": 1, "sessions": {
        f"s{i}": {"entries": [{"text": "x"}] * per} for i in range(sessions)}}),
        encoding="utf-8")
    return p


def test_summary_counts_sessions_and_utterances(tmp_path):
    with mock.patch("sonari.paths.STATE_PATH", _state(tmp_path, 2, 3)):
        assert install_cmd.transcript_summary() == (2, 6)


def test_absent_state_summarises_as_nothing(tmp_path):
    with mock.patch("sonari.paths.STATE_PATH", tmp_path / "absent.json"):
        assert install_cmd.transcript_summary() == (0, 0)


def test_the_full_teardown_order_is_ask_then_unload_then_stop(tmp_path):
    """Spec 8.1's pin, all three steps — asserting only ask<stop would let an
    implementer land ask -> SIGTERM -> unload, and launchd would then restart
    the daemon the SIGTERM just stopped."""
    order = []
    sup = mock.MagicMock()
    sup.uninstall.side_effect = lambda *a, **k: order.append("unloaded")
    with mock.patch("sonari.paths.STATE_PATH", _state(tmp_path)), \
         mock.patch("sonari.cli.voiceout.speak",
                    side_effect=lambda *a, **k: order.append("asked")), \
         mock.patch("sonari.cli.teardown.stop_daemon",
                    side_effect=lambda *a, **k: order.append("stopped") or "stopped"), \
         mock.patch("builtins.input", return_value="n"), \
         mock.patch("sys.stdout.isatty", return_value=True), \
         mock.patch("sonari.cli._platform",
                    return_value=mock.MagicMock(supervisor=sup)):
        install_cmd.uninstall()
    assert order == ["asked", "unloaded", "stopped"], order


def test_a_piped_uninstall_prints_the_question_but_does_not_speak_it(tmp_path):
    """Same tty discipline as doctor (T3): speaking a question we will not wait
    for an answer to is noise in a script."""
    with mock.patch("sonari.paths.STATE_PATH", _state(tmp_path)), \
         mock.patch("sys.stdout.isatty", return_value=False), \
         mock.patch("sonari.cli.voiceout.speak") as spoken, \
         mock.patch("sonari.cli.teardown.stop_daemon", return_value="stopped"), \
         mock.patch("sonari.cli._platform"):
        install_cmd.uninstall()
    spoken.assert_not_called()


def test_purge_flag_deletes_the_transcripts(tmp_path):
    state = _state(tmp_path)
    with mock.patch("sonari.paths.STATE_PATH", state), \
         mock.patch("sonari.cli.teardown.stop_daemon", return_value="stopped"), \
         mock.patch("sonari.cli._platform"), \
         mock.patch("sonari.cli.voiceout.speak"):
        install_cmd.uninstall(purge=True)
    assert not state.exists()


def test_silence_preserves_the_transcripts(tmp_path):
    """No tty, no flag -> keep. Silence must never destroy data."""
    state = _state(tmp_path)
    with mock.patch("sonari.paths.STATE_PATH", state), \
         mock.patch("sys.stdout.isatty", return_value=False), \
         mock.patch("sonari.cli.teardown.stop_daemon", return_value="stopped"), \
         mock.patch("sonari.cli._platform"), \
         mock.patch("sonari.cli.voiceout.speak"):
        install_cmd.uninstall()
    assert state.exists()
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/test_uninstall_disclosure.py -v`
Expected: `AttributeError: ... has no attribute 'transcript_summary'`

- [ ] **Step 3: Implement**

Add to `install.py`:

```python
def transcript_summary():
    """(session_count, utterance_count) held in state.json. Never raises."""
    try:
        import json as _json
        with open(str(paths.STATE_PATH), "r", encoding="utf-8") as fh:
            blob = _json.load(fh) or {}
        sessions = blob.get("sessions") or {}
        n = sum(len(s.get("entries") or []) for s in sessions.values())
        return (len(sessions), n)
    except Exception:  # noqa: BLE001
        return (0, 0)
```

Change the signature to `def uninstall(purge=None) -> int:` and insert, as the **first** action:

```python
    from sonari.cli import voiceout
    from sonari.cli.doctor import should_speak
    sessions, utterances = transcript_summary()
    if sessions and purge is None:
        # PROVISIONAL (ear-batch-4)
        q = (f"Sonari saved transcript text from {sessions} session"
             f"{'' if sessions == 1 else 's'}. Delete it?")
        print(q + f" ({utterances} utterances at {paths.STATE_PATH})")
        interactive = sys.stdout.isatty()
        # Same tty discipline as doctor: speaking a question we will not wait
        # for an answer to is noise in a script.
        if interactive:
            voiceout.speak(q)
        try:
            purge = interactive and input("  delete? [y/N] ").strip().lower() in ("y", "yes")
        except (EOFError, OSError):
            purge = False       # silence keeps the data
```

and, in the artifact loop area, delete `STATE_PATH` only when `purge` is true; otherwise print its path.

Register the flags in `cli/__init__.py`:

```python
    up = sub.add_parser("uninstall", help="remove Sonari (LaunchAgents, launcher, runtime files)")
    up.add_argument("--purge-transcripts", dest="purge", action="store_true", default=None)
    up.add_argument("--keep-transcripts", dest="purge", action="store_false")
    up.set_defaults(func=install_cmd._cmd_uninstall)
```

and have `_cmd_uninstall` pass `purge=getattr(args, "purge", None)`.

- [ ] **Step 4: Run it and watch it pass**

Run: `.venv/bin/python -m pytest tests/test_uninstall_disclosure.py -v` → 5 passed.

- [ ] **Step 5: Full gate + commit**

```bash
.venv/bin/python -m pytest -q
git add src/sonari/cli/install.py src/sonari/cli/__init__.py tests/test_uninstall_disclosure.py
git commit -m "feat(uninstall): spoken state.json disclosure with purge/keep"
```

---

## Task 23: The eared install summary

**Files:**
- Modify: `src/sonari/cli/install.py:132-134` (end of `install()`)
- Test: `tests/test_install_summary.py`

**Interfaces:**
- Consumes: `doctor()`, `verdict()`, `voiceout.speak`.

Reuses the **same verdict** as doctor (spec §8) — one policy that cannot drift, not a second one.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_install_summary.py
from unittest import mock

from sonari.cli import install as install_cmd


def test_install_speaks_the_same_verdict_doctor_uses():
    rows = [("say", True, "ok"), ("daemon socket", False, "down")]
    with mock.patch("sonari.cli.doctor.doctor", return_value=rows), \
         mock.patch("sonari.cli.voiceout.speak") as spoken, \
         mock.patch("sys.stdout.isatty", return_value=True), \
         mock.patch("sonari.cli._platform"), \
         mock.patch("sonari.cli.install._install_body", create=True):
        install_cmd.install()
    assert "unhealthy" in spoken.call_args[0][0]
    assert "daemon socket" in spoken.call_args[0][0]


def test_install_is_silent_when_not_interactive():
    with mock.patch("sonari.cli.doctor.doctor", return_value=[("say", True, "ok")]), \
         mock.patch("sonari.cli.voiceout.speak") as spoken, \
         mock.patch("sys.stdout.isatty", return_value=False), \
         mock.patch("sonari.cli._platform"), \
         mock.patch("sonari.cli.install._install_body", create=True):
        install_cmd.install()
    spoken.assert_not_called()
```

If `install()` has no seam to stub its body, add one in this task (`_install_body()`), because a test must not perform a real install.

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/test_install_summary.py -v`
Expected: `spoken.assert_called` gets 0 calls.

- [ ] **Step 3: Implement**

Replace `install()`'s tail (`sup.post_install_notes(); return 0`) with:

```python
    sup.post_install_notes()
    # The same verdict doctor speaks — one policy, so the two can never drift.
    from sonari.cli import voiceout
    from sonari.cli.doctor import doctor, should_speak
    from sonari.cli.verdict import verdict
    rows = doctor()
    for check, ok, detail in rows:
        print("[{0}] {1}: {2}".format("ok " if ok else "FAIL", check, detail))
    if should_speak(argparse.Namespace(speak=False, quiet=False)):
        voiceout.speak(verdict(rows))
    return 0
```

- [ ] **Step 4: Run it and watch it pass**

Run: `.venv/bin/python -m pytest tests/test_install_summary.py -v` → 2 passed.

- [ ] **Step 5: Full gate + commit**

```bash
.venv/bin/python -m pytest -q
git add src/sonari/cli/install.py tests/test_install_summary.py
git commit -m "feat(install): eared summary reusing doctor's verdict"
```

---

## Task 24: PRIVACY.md tells the truth about `state.json`

**Files:**
- Modify: `PRIVACY.md:30-37`
- Test: `tests/test_privacy_doc.py`

**Interfaces:** none.

The inventory omits `state.json`, and the "not designed to record session content" claim is contradicted by the file's own contents.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_privacy_doc.py
import pathlib

DOC = (pathlib.Path(__file__).resolve().parents[1] / "PRIVACY.md").read_text(
    encoding="utf-8")


def test_state_json_is_in_the_inventory():
    assert "state.json" in DOC


def test_the_contradicted_claim_is_gone():
    assert "not designed to record session content" not in DOC


def test_the_purge_route_is_documented():
    assert "--purge-transcripts" in DOC
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/test_privacy_doc.py -v`
Expected: all three fail.

- [ ] **Step 3: Implement**

Edit `PRIVACY.md`: add `state.json` to the inventory (what it holds — verbatim spoken text, up to `history_cap` utterances per session; where — `~/.sonari/state.json`; how long — until purged; how to remove — `sonari uninstall --purge-transcripts`, or delete the file). Remove the contradicted sentence and replace it with an accurate one: Sonari **does** persist recent spoken text locally so a restart does not lose unheard output; it is never transmitted anywhere.

- [ ] **Step 4: Run it and watch it pass**

Run: `.venv/bin/python -m pytest tests/test_privacy_doc.py -v` → 3 passed.

- [ ] **Step 5: Full gate + commit**

```bash
.venv/bin/python -m pytest -q
git add PRIVACY.md tests/test_privacy_doc.py
git commit -m "docs(privacy): disclose state.json and drop the contradicted claim"
```

---

## Task 25: Version 0.10.0 and ship checks

**Files:**
- Modify: `tests/test_manifests.py:88`, `pyproject.toml:7`, `.claude-plugin/plugin.json:4`, `.claude-plugin/marketplace.json:12`, `src/sonari/__init__.py:4`

**Interfaces:** none.

- [ ] **Step 1: Change the pin first and watch it fail**

Set `VERSION = "0.10.0"` in `tests/test_manifests.py:88`.
Run: `.venv/bin/python -m pytest tests/test_manifests.py -v`
Expected: FAIL — the four files still say `0.9.0`.

- [ ] **Step 2: Bump the other four**

`pyproject.toml:7`, `.claude-plugin/plugin.json:4`, `.claude-plugin/marketplace.json:12`, `src/sonari/__init__.py:4` → `0.10.0`.

- [ ] **Step 3: Run and watch it pass**

Run: `.venv/bin/python -m pytest tests/test_manifests.py -v` → pass.

- [ ] **Step 4: Full ship gates**

```bash
.venv/bin/python -m pytest -q                                   # all green
.venv/bin/python -m pytest tests/test_protocol.py tests/test_concurrency_guards.py -q   # 16 guards
.venv/bin/python scripts/gen_docs.py --check; echo "gen_docs exit: $?"   # expect 0
/usr/bin/grep -rniE "claude\.ai|co-authored|generated by" --include="*.py" --include="*.md" src tests docs || echo "AI traces: clean"
```

- [ ] **Step 5: Commit**

**Never `git add -A` in this repo.** `.superpowers/` (the SDD workspace and the campaign chronicle), `scratchpad/`, and `src/sonari/hooks_prime.py` are untracked but **not** git-ignored, so `-A` would sweep all three into the release commit — violating this plan's "do not touch" constraint. Name the five files:

```bash
git add pyproject.toml .claude-plugin/plugin.json .claude-plugin/marketplace.json \
        src/sonari/__init__.py tests/test_manifests.py
git status --short          # expect ONLY untracked ?? lines to remain
git commit -m "chore(release): 0.10.0 — the safety net"
```

---

## Task 26: Live ear verification (controller, never the owner)

**Files:** none (verification task).

**Requires unsandboxed Bash** — audio and install paths are blocked by the seatbelt (`afplay`/`say` and Chromium-class launches fail with "Permission denied"). Run each command and record the actual observed output in the task report. **Do not ask the owner to listen to anything.**

- [ ] **Step 1: Install the branch build and confirm the version**

```bash
.venv/bin/python -m sonari.cli install
.venv/bin/python -c "import sonari; print(sonari.__version__)"   # 0.10.0
```

- [ ] **Step 2: Hear a healthy verdict** — `sonari doctor` in a real terminal. Record the spoken sentence and confirm it matches the printed rows.

- [ ] **Step 3: Prove the fallback** — `launchctl unload` the agent, `kill` the daemon, then run `sonari doctor`. **The verdict must still be audible** (direct `say`), and the socket + speech-path rows must both be red.

- [ ] **Step 4: Prove the pipe is silent** — `sonari doctor | cat` must print rows and say nothing.

- [ ] **Step 5: Prove uninstall actually uninstalls**

```bash
sonari doctor                       # daemon up
sonari uninstall --keep-transcripts # expect: disclosure, "Stopped the Sonari daemon."
ps aux | grep -c "[s]onari.daemon"  # expect 0 — the orphan is gone
ls ~/.sonari/state.json             # still present (we said keep)
```

Then fire a hook event and confirm **no daemon respawns** (T19's gate).

- [ ] **Step 6: Reinstall and confirm green** — `sonari install`, then `sonari doctor` all-ok.

- [ ] **Step 7: Record everything** in `.superpowers/sdd/d4-safety-net/live-verification.md` and append the outcome to `.superpowers/sdd/progress.md`.

---

## Self-Review

**Spec coverage.** §4.1 speech path → T7; restore health → T8; hotkeyd → T9; fault log → T10; reachability → T11. §4.2 invariant pin → T13, supervision detail → T12. §5 verdict → T2 (meta T1). §6 delivery → T3–T6. §7 suppressor → T14, T15. §8 install summary → T23; §8.1 step 1 → T22, step 2 → T21, steps 3–4 → T20, step 5 → T19, step 6 → T22. §9 hook responsiveness → T16, T17, T18. §10 issue #54 → T15. §11 error handling → the `never raise` constraint, enforced per-check in T7–T11. §12 testing → every task's test block, plus T13's mutation check and T26's live run. §13 provisional strings → the global constraint. §14 version → T25.

**No gaps.** Every spec section maps to at least one task.

**Type consistency.** `spoken_name`/`is_warn` (T1) are consumed only by `verdict` (T2). `speak_direct` (T4) is consumed by `speak` (T5) and T15. `speak(text, *, prefer_daemon)` returns a `str` in every caller. `stop_daemon` returns the same three literals in T20's implementation and tests. `doctor()` returns 3-tuples throughout — **no task widens the row**, which is why the 13 existing doctor tests never change.

**Ordering.** T1→T2→T5→T6 and T14→T15 are hard dependencies. T20 must land before T22 (the disclosure calls `stop_daemon`). T25 must be last before T26. Everything else is independent.

**Known risk to flag at review:** T22 rewires `uninstall()`'s control flow while T20 and T21 also modify it. Land them in the stated order and re-read the whole function after T22 rather than trusting three separate diffs.
