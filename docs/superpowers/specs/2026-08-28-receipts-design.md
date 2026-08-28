# Direction 0 + Receipts — Design Spec (2026-08-28)

Base: `main @ 288a2a6`, v0.11.0 (what is installed and running).
Status: SPEC. No production code written. No tracked file modified by this document's authoring.

## 0. The premise

The sealed product definition this spec serves, verbatim:

> "Sonari is my ears across all my Claude Code sessions — it tells me what happened and what
> needs me, in whichever session needs me, with just enough controls to answer and move between
> them without looking."

The owner's own framing of the work, verbatim:

> "i want to clean up and fix bugs that have been introduced by agents when they were building
> other featheres"

Sonari is his instrument, published as-is (owner ruling 2026-08-28). This spec fixes bugs and
deletes machinery. It adds one concept and removes two.

---

## 1. The problem, measured

### 1.1 One shape, eight instances

Every bug in this spec is the same shape: **a per-site opt-in safety list with no completeness
check.** Each individual session that added a call site was locally correct. Nothing was wrong at
the point it was written. The defect lives in the seam, and only a whole-product sweep sees it.

There are two such lists on `main`:

| list | opt-in unit | consumer | consumer correctness |
|---|---|---|---|
| `pause_exempt=` / `mute_exempt=` kwargs | per `_enqueue` call site | `SpeechQueue.pop_pause_exempt`, `host._attributed_text` | airtight, well tested |
| `speaker._FALLBACK_EARCONS` | per earcon kind | `Speaker.transient`, `host._asset_path`, `keymap.py:417` | airtight, well tested |

In both cases the consuming code is correct and the **enrollment list feeding it is incomplete**,
and nothing anywhere enumerates "every site that should be enrolled" against "every site that is."

### 1.2 The 25 / 0 / 15 span-parse — REPRODUCED 2026-08-28

AST parse of every `_enqueue(...)` call in `daemon/host.py` + `daemon/features/*.py`
(script: `scratchpad/e3-review/span.py`; 54 `_enqueue` + 27 `cue` = the 81 audio call sites):

| | count |
|---|---|
| sets **both** `pause_exempt=True` and `mute_exempt=True` | **25** |
| sets `pause_exempt=True` **alone** | **0** |
| sets `mute_exempt=True` **alone** | **15** |
| sets **neither** | **14** |

**Zero sites in the entire codebase want one flag without the other in the control-cue direction.**
The two flags are orthogonal in MECHANISM — `mute_exempt` is a rendering concern (skip the folder
prefix, `host.py:629-648`), `pause_exempt` is an admission-control concern (`queue.pop_pause_exempt`,
consumed at `host.py:1269`) — and perfectly correlated in INTENT. They have never once been needed
independently. A developer adding a deliberate-press cue must remember two independently-named
booleans, and 15 sites remembered one.

Of the 15 `mute_exempt`-only sites, **7 are provably safe** (an upstream guard proves the target
cannot be stopped: `_select_keep_going` and `_waiting_target` both skip `st.stopped`;
`playback.py:157` un-stops two lines earlier; `announce_resume` is armed only when
`st is None or not st.stopped`). The other 8, plus the `neither` sites reachable from a hotkey, are
the live defects in §5.

### 1.3 The S1, reproduced — and a second reproduction it did not predict

**S1 (already on record, `MUTED-SESSION-SILENCE.md`):** pressing ⌃⌘D on a muted session holding a
real permission ask moves the voice there and says **nothing at all** — no tone, no word — while
`voice_state` reads `flowing`. Un-mute and the ask is destroyed; the only thing spoken is
"Resumed." Reproduced in-process, both variants (standing on the muted session; clicking into a
muted terminal), `scratchpad/e3-review/probe_muted_crossing.py`.

**New reproduction, 2026-08-28** (`scratchpad/e3-review/probe_receipts.py`, sacrificial HOME, pure fakes).
Six mechanisms, all OBSERVED silent on `main`:

```
=== P1. chooser commit (⌃⌘Tab) onto a MUTED target — chooser.py:227 ===
  B stopped=True  A stopped=False
  after commit onto muted B    SPOKEN=[]  EARCONS=[] speaker=None workspace=B Bq=1

=== P2. nav (⌃⌘←) onto a MUTED session — navigation.py:64/95/140 ===
  prev_response on muted B     SPOKEN=[]  EARCONS=[] Bq=1
  nav prev on muted B          SPOKEN=[]  EARCONS=[] Bq=2

=== P3. reread-options (⌃⌘O) on a MUTED session — decisions.py:266/268 ===
  ⌃⌘O on muted B               SPOKEN=[]  EARCONS=[] Bq=1

=== P4. SET_RATE delta while muted — control.py:190 via :235 (**kw) ===
  ⌃⌘= (faster) on muted B      SPOKEN=[]  EARCONS=[] rate=225 Bq=1
  ⌃⌘V (verbosity) on muted B   SPOKEN=['Verbosity quiet.']   <-- CONTROL: same helper, both flags

=== P5. SESSION_START while stop-all — lifecycle.py:131 one-shot burn ===
  NEW born stopped=True  claim_announce spent=True
  new session under stop-all   SPOKEN=[]  EARCONS=[] NEWq=2
  after un-mute (⌃⌘S)          SPOKEN=['Resumed.'] NEWq=0        <-- both one-shots destroyed

=== P6. _raise_failed onto a muted session — host.py:387 ===
  raise failure word on muted B  SPOKEN=[]  EARCONS=[] Bq=1
```

P4 is the whole disease in four lines: **⌃⌘= and ⌃⌘V go through the same helper (`_readback`,
`control.py:173-190`). One caller passes the flags, the other does not.** The rate nudge is silent;
the verbosity nudge speaks.

**P1 is new and it changes the design.** `_commit`'s ratified Fork-2 policy (chooser.py:215-219)
does `sessions.set_speaker(None)` when landing on a muted target, *then* enqueues the landing cue to
that target. The held branch (`host.py:1263-1297`) fires only when the SPEAKER's stream is stopped
and scans only the SPEAKER's queue. With `speaker() is None` there is no speaker stream, so the
held branch never runs at all. Counterfactual probe C1 (`scratchpad/e3-review/probe_counterfactual.py`) sets
`pause_exempt = True` on every item in B's queue and ticks the loop twenty times:

```
=== C1. chooser commit onto muted: does pause_exempt ALONE deliver? ===
  after flipping pause_exempt: SPOKEN=[] speaker=None Bq=1
  -> pause_exempt alone is INSUFFICIENT
```

**Adding the flag is not enough for this site.** The flag system has no representation for
"stopped AND not the speaker". §4.4's mechanism change (M2) is therefore load-bearing, not a
nice-to-have.

Counterfactual C2 confirms the opposite direction — the flag IS sufficient once the item is
reachable:

```
=== C2. ⌃⌘D onto muted (S1) ===
  TODAY:          SPOKEN=[] speaker=B Bq=1
  COUNTERFACTUAL: SPOKEN=['A question needs your answer. — at the terminal.'] Bq=0
```

### 1.4 The 13 / 6 / 6 earcon arithmetic — REPRODUCED 2026-08-28

Live read of `~/.sonari/config.json` (read-only), 2026-08-28:

```
earcon keys (6): ['choice', 'error', 'permission', 'plan', 'ready', 'turn_done']
voice: 'Voice 1'  rate: 225  verbosity: medium  focus_follow: True
```

- `platform/macos/earcon.py::_DEFAULTS` defines **13** kinds.
- `speaker.py::_FALLBACK_EARCONS` covers **6** of them.
- his live `earcons` block holds **6**, one of which (`ready`) is an orphan dropped from `_DEFAULTS`
  by `1c0f5fb` on 2026-06-27 — which dates his config block to **before 2026-06-27**.

Probe C3 resolves all 13 kinds through a real `Speaker` built with exactly his legacy six:

```
    repoint                -> SILENT
    submit_ack             -> SILENT
    (the other 11 resolve)
  _DEFAULTS=13  _FALLBACK_EARCONS=6  legacy config=6
  AFTER per-key _deep_merge: 14 keys; repoint -> /System/Library/Sounds/Bottle.aiff
  null-means-mute survives: None
```

Why it never healed: `daemon/bootstrap.py:96-97` is
`if "earcons" not in cfg: cfg["earcons"] = default_earcons()` — **all-or-nothing on the whole key.**
His key has existed since June, so **no earcon added after that date has ever reached him.**

What it costs: `focus.py:72` fires `cue("repoint")` when his own click moves the workspace to a
different session. He runs `focus_follow: true`. The cue carries no word — the tone IS the whole
signal. So **for five weeks, when his click has moved the voice, he has been told nothing.** The one
cue whose entire job is "the voice just moved because of something you did" is dead. That is a
direct hit on the sealed definition's "in whichever session needs me".

### 1.5 Why the suite is green through all of it

Two structural blindnesses, both measured on `main`:

**(a) The daemon's own test double throws away the information.** `tests/daemon_helpers.py:61-64`:

```python
def transient(self, kind: str) -> None:
    self.earcons.append(kind)
```

It appends unconditionally. It does not replicate the real two-dict resolution, so it is
**incapable** of catching a dead-asset bug no matter what config a test passes. Measured blast
radius on `main`: **183 test files; 24 use `speaker.earcons`; 62 `.earcons ==` assertions; 83 files
import `daemon_helpers`.** Every one of those 62 assertions proves `cue()` was *called*. None proves
a sound would *play*. `tests/test_repoint.py` is the clearest instance: it asserts
`speaker.earcons == ["repoint"]` against the fake, and `repoint` has been silent on the real install
the entire time.

**(b) The isolation fixture is itself a per-site opt-in list with no completeness check.**
`tests/conftest.py`'s `_isolate_sonari_dir` is ~20 hand-maintained `monkeypatch.setattr` lines
repointing by-value binds. Canary run on `main` (sacrificial HOME, read-only assertions, file
removed afterwards) — four Sonari paths still resolve under `$HOME` *while the fixture is active*:

```
AssertionError: ['paths.KOKORO_VENV = $HOME/.sonari/venv',
                 'paths.RAISE_BIN_PATH = $HOME/.sonari/sonari-raise']
AssertionError: ['supervisor.LAUNCH_AGENT_PATH = $HOME/Library/LaunchAgents/com.sonari.speechd.plist',
                 'hotkeys.LAUNCH_AGENT_PATH  = $HOME/Library/LaunchAgents/com.sonari.hotkeyd.plist']
```

`kokoro_provision.uninstall_kokoro()` rmtree's the first; `MacSupervisorBackend.uninstall()` removes
the LaunchAgents. **The suite has destroyed his real install TWICE.** `~/.sonari` and
`~/.local/bin` are outside git, so `git status` clean is not evidence.

### 1.6 The same blindness, confirmed on an independent pass

`build/safety-net-closure` (wave 1, 18 commits, parked at his gate) contains commit `ad34469`
"sanction the three closing dead-workspace-silent presses (T2)". It edits
`decisions.py:266/268` — **the exact two lines in row 3 of §5's table** — to close the DEAD-workspace
axis, and leaves the STOPPED axis untouched:

```python
    if text:
-       ctx.host._enqueue(fg, "choice", text, False)
+       ctx.host._enqueue(fg, "choice", text, False,
+                         at_front=ctx.host._sanction_dead_read(fg, whole=False))
```

A different wave, different agents, a deliberate lateral sweep over the same handler — and the
`pause_exempt` axis was invisible, because `_sanction_dead_read` was a **named idiom** with a
documented enrollment discipline and `pause_exempt` was an unnamed kwarg. That is `METHOD-GAP.md`'s
thesis confirmed by a second independent pass. It is the argument for the registry.

---

## 2. Scope

### In scope

| id | item |
|---|---|
| **D0.1** | Suite hermeticity: a refusal that cannot run against his real HOME, and a completeness check on the isolation list |
| **D0.2** | A daemon-level fake speaker that replicates the REAL asset resolution |
| **R3** | Earcon defaults into `config.DEFAULTS`; three resolvers collapse to one; `repoint` revives |
| **R4** | `pause_exempt` + `mute_exempt` collapse into one `control_cue` concept on a registry with a completeness test; the 8 mismatched sites fixed |
| **R5** | Three effect-side doctor rows |

### Explicitly OUT of scope — named so nobody drifts

- **Direction 2 — nav/pile pollution** (the 87–94% tool entries in nav's replay, the roster debris,
  `sessions.unregister()`'s single caller, "one pile, true numbers"). This is the immediate
  follow-up, not this spec.
- **Direction 3 — the `foreground()` → `workspace()` W11 collapse and the waiting-predicate
  widening.** Not touched. §4.4's held-branch change (M2) deliberately does NOT alter which accessor
  any handler resolves its target from.
- **Direction 4 — install/uninstall certification.** Not touched.
- **Direction 5 — any cut or demote** (the permission chord, feature removal). Not touched. The two
  deletions in R3 (`_FALLBACK_EARCONS`, `EarconBackend.default_earcons`) are dead-code consequences
  of a merge, not feature cuts; §4.3 argues each.
- **Pushing, publishing, `origin`, the README audience line.** Not touched.
- **Every ear/taste item.** Goes to §10's audition list, never to him as a live question.
- **The `_drop_pending(st.queue.clear())` at ⌃⌘S-resume** (`playback.py:149`). Ratified 2026-07-16;
  §4.5 argues why it stays and where its premise genuinely fails.

---

## 3. Direction 0 — the hermetic-suite keystone

Direction 0 gates everything else. Nothing in R3/R4/R5 may be written before it lands.

### 3.1 D0.1 — the suite cannot run against his real HOME

**Finding that changes the plan: most of this is already built, reviewed, and parked.**
`build/safety-net-closure` commits `2b57099`, `939b701`, `6ad78d5`, `eb71516`, `144a8db`, `2513600`
(task T5, review APPROVED) already deliver:

- `tests/_isolation.py` — ONE `isolate_paths(root, monkeypatch=None)` list, callable from ad-hoc
  scripts as well as pytest, that **repoints `HOME`** (the only repoint that survives a fork,
  because `paths.py` derives from `Path.home()` and a subprocess re-imports from scratch).
- `tests/test_paths_conftest_isolation.py` — **the completeness guard**: every uppercase module-level
  assignment in `paths.py` must appear in the isolation list, with an empty allowlist. This is the
  same cure this spec applies to `pause_exempt`, applied to paths. It closes `KOKORO_VENV` and
  `RAISE_BIN_PATH` by construction.
- `tests/test_no_independent_home_derivation.py` — no module outside `paths.py` may call
  `expanduser`/`Path.home()`/read `$HOME`, except an explicit allowlist where each entry carries a
  verified one-line reason.
- `paths.py` gains `SPEECHD_LAUNCH_AGENT_PATH` / `HOTKEYD_LAUNCH_AGENT_PATH`, so the LaunchAgent
  plists fall under the guard instead of under per-test discipline.

**Recommendation: adopt that mechanism rather than building late-bound accessors on `main`.**

Reasoning: late-bound accessors and a repoint-list-with-a-completeness-guard buy the identical
property — *forgetting becomes a test failure* — and the second is written, reviewed, and 1 100
lines of it are sitting at his gate. Building a competing mechanism on `main` guarantees a large,
semantically confusing conflict against a branch he has not yet ruled on, in the one area of the
codebase where a bad merge has twice cost him his install. The cheap thing is to take the built
mechanism and add the two things it does not have.

**What wave 1 does NOT have, and what this spec builds:**

**(i) A REFUSAL.** Nothing anywhere computes the real home independently of `$HOME` and declines to
run. `isolate_paths` repoints `$HOME`, but a script that forgets to call it, a test module that
imports and acts at collection time, or a `launchd` child (which does not inherit your environment)
is unprotected, and the failure is silent and destructive. Build:

- `tests/conftest.py`, at **module import time** (before `import sonari` anywhere, so that even
  import-time `Path.home()` binds resolve sacrificially): set `os.environ["HOME"]` to a per-run
  temp dir.
- Immediately after, a hard gate: `REAL = pathlib.Path(pwd.getpwuid(os.getuid()).pw_dir)` —
  `getpwuid` reads the password database and is immune to `$HOME`. If `REAL` is a parent of, or
  equal to, any of `paths.SONARI_DIR`, `paths.APP_DIR`, `supervisor._local_bin_dir()`,
  `paths.SPEECHD_LAUNCH_AGENT_PATH`: `pytest.exit(...)` with a one-line reason. Not a test failure —
  an abort before collection completes, because a test failure arrives after the damage.

**(ii) A post-run canary.** A session-scoped autouse fixture that stats `REAL/.sonari`,
`REAL/.local/bin/sonari` and `REAL/Library/LaunchAgents/com.sonari.*.plist` at session start and
again at session end, and fails the session loudly if any mtime, inode or existence changed. The
refusal is the preventive; the canary is the detective, and it is the only thing that would have
named the culprit either of the two times this happened.

**Fallback if wave 1 does not merge first:** cherry-pick `2b57099`, `939b701`, `6ad78d5`, `eb71516`,
`144a8db`, `2513600` onto this spec's branch before anything else, then add (i) and (ii). Those six
commits touch only `tests/*` and `src/sonari/paths.py` and are independent of wave 1's daemon
changes. Do not re-derive them.

### 3.2 D0.2 — a fake speaker that replicates real asset resolution

`FakeSpeaker.transient` becomes honest:

```python
class FakeSpeaker:
    def __init__(self, earcons=None):
        self.earcons: list[str] = []       # kinds that WOULD have played (unchanged meaning)
        self.earcon_paths: list[str] = []  # the asset each resolved to
        self.silent_cues: list[str] = []   # kinds that resolved to NOTHING — the receipt
        self._earcons = dict(earcons or {})

    def transient(self, kind: str) -> None:
        path = self._earcons.get(kind)     # the SAME single lookup the real Speaker does post-R3
        if path is None:
            self.silent_cues.append(kind)
            return                         # a silent cue is not an earcon
        self.earcons.append(kind)
        self.earcon_paths.append(path)
```

`make_daemon()` gains `earcons=None` and seeds both the config and the fake from it; `None` means
the full default table — i.e. **the fake mirrors a fresh install**, which is exactly what
`bootstrap.py:96-97` produces today. That keeps all 62 existing `.earcons ==` assertions green
(every kind resolves) while making a dead asset detectable.

The receipt that turns this from a helper change into a guarantee: a **conftest autouse fixture that
fails any test whose FakeSpeaker recorded a `silent_cue`**. Implementation: `FakeSpeaker.__init__`
appends `self` to a module-level per-test registry that the fixture drains and asserts on at
teardown. From that point, **all 83 daemon-test files are dead-asset detectors** and no future cue
can ship DOA without the suite saying so.

The 5 tests that legitimately assert silence (an explicitly unconfigured kind) opt out through a
named marker, `@pytest.mark.expects_silent_cue`, with the reason in the test docstring — the
labelled exception, not the unlabelled default.

---

## 4. Design

### 4.1 What `control_cue` IS

> **A control cue is the utterance a deliberate operator gesture produces as its own answer.**
> It exists because he pressed a key. It is not narrated content, and it is delivered regardless of
> whether the stream it lands on is held.

One concept, two effects — the two that today's two booleans carry separately:

| effect | today | after |
|---|---|---|
| never folder-prefixed; does not claim `_last_spoken_session` | `mute_exempt` (`host._attributed_text`) | `control_cue` |
| voiced even while its stream is stopped | `pause_exempt` (`queue.pop_pause_exempt`) | `control_cue` |

`SpeechItem.mute_exempt` and `SpeechItem.pause_exempt` are **deleted**. Not deprecated, not aliased.

**Hard replacement, not migration.** Defended on three facts:

1. **Nothing persists them.** `SessionStream.to_state()` serializes exactly
   `{"frontier": ..., "stopped": ...}`; `_snapshot_state` (host.py:1460-1472) serializes only
   streams carrying a durable fact. **No queue item, and therefore no flag, ever reaches
   `state.json`.** Verified by reading both. `STATE_VERSION` does not move.
2. **Nothing outside the process reads them.** They are not on the STATUS wire, not in the protocol,
   not in the config, not in any doc.
3. **He is the only user, and the installed app is a copy of the source made by `sonari install`.**
   There is no version skew to bridge.

An alias would preserve exactly the thing being deleted: two names for one idea. The whole point is
that a future author cannot reach for the wrong half.

The mechanical consumers that must move with it — the three sites that read a flag off an
existing item rather than choosing one: `on_repeat_last`'s in-flight re-queue
(`playback.py:309-313`) and `_restore_and_clear`'s captured-item restore
(`chooser.py:130-134`) both become `control_cue=c.control_cue`; `host.cue(word=...)`
(`host.py:625-627`) passes `control_cue=True`; `queue.pop_pause_exempt` is renamed
`pop_control_cue`.

### 4.2 Where it is declared, and how completeness is enumerated

**The registry is `keymap.py`'s `ACTIONS`** — it already carries per-action `message` / `label` /
`teach` / `doc` metadata that `teaching.py` and `scripts/gen_docs.py` read. The requirement is
action-shaped: `cues.py` scopes itself to tone assets and never registers the prose control
confirmations, which is where every one of the 8 mismatches lives.

**Declaration.** Every entry in `ACTIONS` gains a mandatory `"control_cue"` key:

```python
"faster": {
    "message": {"type": "set_rate", "delta": 25},
    "label": "Faster",
    "teach": "Faster. Raises the speech rate.",
    "doc": "Speak faster",
    "proposed": None,
    "control_cue": True,      # ⌃⌘= answers audibly, muted or not
},
```

Mandatory means asserted with `"control_cue" in meta`, never `meta.get(...)` — **an action added
without it fails the suite at the declaration, before anyone has to notice a silence.** A `False`
value additionally requires a non-empty `"control_cue_waiver"` string giving the reason; a static
test enforces the pair. **All 22 actions are `True`; the one waiver in the whole registry is
`chooser_cancel`, verified silent by design.** That near-uniformity is not a smell — it is the finding restated: *every gesture answers*, and the 8
mismatched sites are simply places where the code stopped agreeing with a rule nobody had written
down.

**The adjunct, for gestures hotkeyd sends outside the keymap.** `hotkeyd/sonari-hotkeyd.swift` sends
five message types the resolved keymap does not carry: `chooser_commit` (line 291),
`chooser_cancel` (299), `chooser_digit` (324), `os_focus` (152), `witness_ping` (216). Four of those
are operator gestures. So, beside `ACTIONS` in the same file:

```python
# Operator gestures hotkeyd sends directly, outside the resolved keymap.
# Kept out of ACTIONS so hotkey_rows()/gen_docs never advertise an unbindable verb.
CONTROL_GESTURES = {
    "chooser_commit": {"message": {"type": "chooser_commit"}, "control_cue": True},
    # VERIFIED SILENT BY DESIGN — the only waiver in the registry today.
    "chooser_cancel": {
        "message": {"type": "chooser_cancel"},
        "control_cue": False,
        "control_cue_waiver":
            "_restore_and_clear (chooser.py:121-135) is a deliberate no-op: "
            "'move nothing, say nothing'. It requeues the captured item at the "
            "front of its own stream; resuming that item IS the answer. Adding a "
            "confirmation here is a wording change, not a defect fix.",
    },
    "chooser_digit":  {"message": {"type": "chooser_digit", "digit": 1}, "control_cue": True},
    "os_focus":       {"message": {"type": "os_focus", ...}, "control_cue": True},
    # witness_ping is machinery, not a gesture: hotkeyd's own liveness heartbeat.
}
```

`os_focus`'s answer is the `repoint` earcon — the cue R3 revives. That is where the two receipts
meet, and it is why R4's `os_focus` row depends on R3.

**Completeness, in two layers.** The static layer keeps the ratified idiom
(`test_cue_contract.py::test_every_cue_literal_is_a_registered_transient` — grep the literals,
assert registry membership); the behavioural layer is added because static analysis provably cannot
close this class here:

*Static* — `tests/test_control_cue_contract.py`:

1. `test_every_action_declares_control_cue` — `assert "control_cue" in meta` for all of `ACTIONS`
   and `CONTROL_GESTURES`.
2. `test_a_control_cue_waiver_carries_a_reason` — any `False` has a non-empty waiver string.
3. `test_the_legacy_exempt_flags_are_gone` — `mute_exempt` / `pause_exempt` appear zero times under
   `src/`. (71 hits on `main`.)
4. `test_every_hotkeyd_message_type_is_a_declared_gesture` — the set of `"type": "..."` literals in
   `hotkeyd/sonari-hotkeyd.swift`, minus a named machinery allowlist (`witness_ping`,
   `reload_keymap`), is covered by `ACTION_MESSAGES` ∪ `CONTROL_GESTURES`.

*Behavioural* — `tests/test_muted_press_receipts.py`, parametrized over every declared gesture:

```python
@pytest.mark.parametrize("action", sorted(_declared_control_cues()))
def test_every_gesture_answers_on_a_muted_session(action):
    daemon, _, speaker, sessions, _ = make_daemon(verbosity="medium", foreground="A")
    _arm(action, daemon, sessions)          # per-action preconditions, one map in this file
    _mute(daemon, sessions, "B")
    speaker.spoken.clear(); speaker.earcons.clear()
    daemon.handle_message(_message(action))
    _tick(daemon)
    assert speaker.spoken or speaker.earcons, \
        f"{action} pressed on a muted session produced no sound at all"
```

Why behavioural and not a call-site AST walk: **`control.py:190` is inside `_readback(host, text,
**kw)`.** The flags arrive through `**kw` from three different callers, so an AST walk over the
handler's own `_enqueue` sites reads `_enqueue(ws, "prose", text, False, at_front=...)` and sees
nothing wrong — my own span-parse in §1.2 classified it under `neither` for exactly that reason. A
static test would have passed on the site that P4 proves is broken. And the repo's own thesis, from
`METHOD-GAP.md`, is that **the tests pin the call, not the ear**; the fix for that is a test that
listens.

The `_arm` map is the honest cost of this design: 25 declared control cues (22 actions + commit,
digit, os_focus), each with its own precondition
(a queued decision for ⌃⌘D, two turns of history for nav, `st.options` for ⌃⌘O, a live
`_pending_decisions` entry for ⌃⌘Return). It is roughly 120 lines in one file, and it is the
enumeration that does not exist today. A new action with no `_arm` entry errors rather than passing
silently.

### 4.3 R3 — one earcon table, one resolver

**Move the table into `config.DEFAULTS`.**

```python
# config.py
DEFAULTS = {
    ...,
    # Every registered cue's default asset. In DEFAULTS (not bootstrap) so
    # load_config()'s per-key _deep_merge heals an existing install: a kind added
    # after a user's config.json was written still reaches them. bootstrap's old
    # whole-key guard could not — it is why `repoint` was silent for five weeks.
    "earcons": {
        "permission": "/System/Library/Sounds/Funk.aiff",
        ...                       # all 13 kinds, moved verbatim from platform/macos/earcon.py
    },
}
```

`load_config()` is **unchanged** — `_deep_merge` (config.py:29-44) already recurses into nested
dicts, so a persisted `earcons` block is merged per key over the defaults. Verified by probe C3:
his legacy six merge to 14 keys (13 + the harmless `ready` orphan) and `repoint` resolves to
`Bottle.aiff`.

**Deletions this makes possible** — each is a second source of truth for the same table, which is
the drift this receipt closes:

- `speaker.py::_FALLBACK_EARCONS` (6 kinds) — deleted. `Speaker.transient` becomes one lookup:
  `path = self._earcons.get(kind); if path is None: return`.
- `host._asset_path` (host.py:584-592) — the config-then-fallback dance becomes
  `(self.config.get("earcons") or {}).get(kind)`.
- `keymap.py:416-418` — the `earcons.get(...) or _FALLBACK_EARCONS[...]` line becomes a plain
  `load_config().get("earcons", {}).get("alarm_daemon_down")`. This is the site that proves the
  merge must live in `load_config`, not in `bootstrap`: `keymap.py` runs in the hotkeyd/CLI process,
  which never executes `bootstrap.main()`.
- `daemon/bootstrap.py:96-97` — deleted.
- `platform/macos/earcon.py::_DEFAULTS` and `MacEarconBackend.default_earcons()` — deleted, and
  `EarconBackend.default_earcons` removed from `platform/contracts.py`. `MacEarconBackend` keeps
  `play()`. Its only caller was `bootstrap.py:97`.

Three resolvers → one. Two tables → one.

**Core-purity check, done.** `tests/test_no_os_branch_in_core.py:12-15` lists `config.py` as CORE
and forbids exactly two things: the string `sys.platform` and the string `platform.macos`. A literal
asset table introduces neither. And there is a direct precedent one file over: `speaker.py` is also
in CORE and already holds `/System/Library/Sounds/*.aiff` literals in `_FALLBACK_EARCONS` — which
this change **removes**. Core ends up with one macOS table where it had one, and the platform
package ends up with none where it had one. The OS seam is DEAD by owner ruling (macOS only
forever), so the abstraction being dissolved here is one nobody will re-enter.

**"Does anyone rely on a null earcon key meaning mute?" — verified, answer: no.**

- No production code treats absence as a mute. The one real mute mechanism is a separate boolean,
  `config["submit_ack_enabled"] = False` (config.py:24) — muting is done with a flag, not by
  deleting a key.
- No doc describes it. `/usr/bin/grep -rni earcon docs commands README.md .claude-plugin skills`
  returns only prose about which tone plays when.
- **An explicit `null` still mutes after the change.** `_deep_merge` writes the override value even
  when it is `None` (config.py:42-43), and `Speaker.transient` returns on a `None` path. Verified:
  `_deep_merge({'earcons': _DEFAULTS}, {'earcons': {'repoint': None}})['earcons']['repoint']` → `None`.
- The only thing that *does* depend on today's shape is one Speaker unit test,
  `test_speaker_transient.py::test_unconfigured_legacy_kind_is_silent_noop`, which passes an empty
  dict directly. It still passes verbatim. Its two siblings that pin the fallback layer
  (`test_new_failure_kinds_fall_back_to_builtin_assets`, `test_config_entry_wins_over_the_fallback`)
  are rewritten against the merged defaults.

**What changes for him at his next `sonari install`:** `repoint` starts sounding (Bottle.aiff) —
a tone he has never heard, hence §10. `submit_ack` becomes resolvable but stays dark
(`submit_ack_enabled: False`). His `config.json` on disk is **not rewritten**; the merge happens in
memory at `load_config()`, so his `ready` orphan and his five overrides survive untouched.

### 4.4 R4 — the three mechanism changes

**M1 — the flag.** `SpeechItem.control_cue: bool` replaces both booleans; `_enqueue` takes
`control_cue: bool = False`; `_attributed_text` branches on it; `SpeechQueue.pop_pause_exempt`
becomes `pop_control_cue`. The 25 both-sites become `control_cue=True` 1:1. The 7 provably-safe
`mute_exempt`-only sites also become `control_cue=True` and thereby gain held-branch delivery they
did not have — with **no observable change**, because in each case an upstream guard proves the
target cannot be stopped (§1.2). Each of the 7 gets a one-line comment naming its guard, so the
proof lives next to the code rather than in this document.

**M2 — the held branch scans every stopped stream, not just the speaker's.**

Today (`host.py:1263-1297`) the loop enters the held branch only when the SPEAKER's stream is
stopped, and pops only from the SPEAKER's queue. P1/C1 prove that is the wrong scope: the ratified
Fork-2 commit-onto-muted policy sets `speaker() = None`, so the landing cue is enqueued to a stream
that no branch of the loop will ever look at. `_select_keep_going` (host.py:139-171) also skips
stopped streams, so nothing else reaches it either.

Restructure `_speak_loop_once`, hoisting the pop above the speaker check and widening it:

```python
# BEFORE resolving the speaker: a control cue is the answer to a press, and a
# press can land anywhere — including a stopped stream that is not (or is no
# longer) the speaker. Fork-2's commit-onto-muted RELEASES the voice, so the
# speaker-scoped scan could never reach its own landing cue (probe C1).
with self._lock:
    item = self._pop_held_control_cue()      # oldest by global id, across STOPPED streams only
    self._state._current_item = item
    cancel_epoch = self.speaker.cancel_epoch()
if item is not None:
    ... speak it (prelude-atomic, same body as today's held branch) ...
    self.note_spoken(item, completed)
    return
fg0 = self.sessions.speaker()
st0 = self._state._streams.get(fg0)
if st0 is not None and st0.stopped:
    self._state._wake.wait(self._poll_interval); self._state._wake.clear(); return
... normal branch, unchanged ...
```

`_pop_held_control_cue` iterates `self._state._streams`, considers only streams with
`st.stopped`, takes the minimum `queue.oldest_control_cue_id()` (a new non-destructive peek beside
the existing `oldest_id()`), and pops that one item. **Oldest-first by the daemon-global monotonic
`SpeechItem.id`** — the same ordering key `_select_keep_going` already uses, so there is no new
ordering concept.

Deliberately unchanged by M2:
- **Non-stopped streams are untouched.** Their items ride the normal queue in order; D8 law 1
  ("verbal never bypasses the queue") is not weakened.
- **Which accessor a handler resolves its target from.** M2 changes delivery, not routing. The W11
  `foreground()`/`workspace()` question stays out of scope.
- **The behaviour when only the speaker's stream is stopped** — byte-identical to today: the same
  item is found, by the same scan, in the same order.

**M3 — a press delivers what it asked for.**

A gesture's answer is not only its confirmation; for the read gestures it is the content. The
codebase already ratified this shape twice: `on_repeat_last` re-speaks a whole utterance through a
mute (`playback.py:319-320`, both flags today) and catch-up reads a whole summary through one
(`catchup.py:79`, `:183`, both flags). Nav, ⌃⌘O and ⌃⌘D are the same gesture class and were simply
never enrolled.

- `_nav`'s seek-and-play (`navigation.py:51`) and `_nav_response`'s (`:98`) enqueue with
  `control_cue=True`. Both handlers clear the queue first, so the drained set is exactly the
  requested content and nothing else.
- `on_reread_options`' two enqueues (`decisions.py:266`, `:268`) — `control_cue=True`.
- `on_jump_decision`'s HIT path: after `st.queue.jump_to_decision()` has left the decision at the
  head, and **only when `st.stopped`**, claim it:

  ```python
  if st.stopped:
      # ⌃⌘D IS the request to hear this ask. Marking the head rather than
      # re-enqueuing preserves its entry/prelude/forward provenance.
      st.queue.claim_head_as_control_cue()
  ```

  A named, documented queue operation — not an anonymous in-place mutation. Gated on `stopped` so
  the un-muted path is byte-identical to today.

Prefix safety, checked for each: crossed nav and crossed ⌃⌘D both enqueue a `names_session` folder
cue at_front, which claims `_last_spoken_session`, so suppressing the prefix on the content behind
it changes nothing. Within-session nav and ⌃⌘O are same-session, where no prefix is emitted anyway.

**The `_readback` fix.** `_readback` (control.py:173-190) sets `control_cue=True`
**unconditionally** and the `**kw` pass-through for it is removed from its three callers. A readback
*is* a control cue; there was never a reason for the caller to opt in, and the opt-in is what broke.

### 4.5 The one-shot markers are NOT control cues

`lifecycle.py:53` (the install nag), `lifecycle.py:131` (the SESSION_START "{number}, {folder}."
announce) and `teaching.py:91` (`maybe_hint`) are in the finding's list, and their fix is **not**
enrollment.

They are not answers to a gesture — they are ambient announcements. Making them break through a
mute would violate the ratified R7 "lasting quiet": he pressed ⌃⌘M meaning *silence everything*, and
a session opening in that window announcing itself is precisely what he asked not to happen. Their
actual defect is different and worse: **the one-shot marker is burned before delivery is possible.**

P5 shows it exactly: a session created under stop-all is born stopped (`host.py:398-403`),
`claim_announce` is consumed inside the `if` at `lifecycle.py:120`, `st.guided` is set at
`lifecycle.py:51` before the enqueue at `:53`, and both items are then destroyed at the next ⌃⌘S.
`claim_announce spent=True`, `NEWq=2` → `NEWq=0`, spoken `[]`. **Never heard, ever, for that
session's whole life.** `teaching.py`'s `maybe_hint` is the same, and its own docstring already
promises the correct behaviour it does not implement — it "marks the key consumed ONLY when there is
a session to actually speak it into", and checks `session is None` but never `.stopped`.

**Fix: claim on deliverability, not on attempt.**

| site | today | after |
|---|---|---|
| `teaching.py:84-91` | `_hinted.add(key)` then enqueue | check `not host._stream(session).stopped` before `_hinted.add`; leave the key open otherwise. Makes the code match its own docstring. |
| `lifecycle.py:45-53` | `guided = True` at :51, always | `guided = True` when `state == "ok"` (nothing to say — throttle forever) or when the target stream is not stopped. No new field. |
| `lifecycle.py:120-135` | `claim_announce()` consumed in the `if` | test deliverability *before* claiming; and on ⌃⌘S-start (`playback.py:132-157`, after `st.stopped = False`), if `claim_announce(fg)` is still unclaimed, enqueue the announce then. **The session names itself the first time he can actually hear it.** |

**And `playback.py:149`'s `_drop_pending(st.queue.clear())` stays.** Per the pre-registration's
ratified-state rule: the quiet-resume drop was ratified 2026-07-16 on the stated premise that "the
pile persists in the history transcript BEHIND the frozen frontier … reachable later by SP5's
catch-up", and that a live blocking permission "stays answerable via `_pending_decisions` / ⌃⌘D".
Both are true — decisions go through `_announce_decision`, which records to history before enqueuing
(`decisions.py:35-38`). The premise holds for content, so the ratification holds. Where it is
**false** is exactly the daemon-authored one-shots above: they are direct `_enqueue` calls with no
`entry=`, they exist nowhere but that queue, and catch-up cannot recover them. Fixing the marker
lifecycle fixes precisely the case the ratification does not cover, and touches nothing it does.

---

## 5. Behaviour table — THIS IS THE ACCEPTANCE CRITERIA

One row per site in `EXEMPT-FLAG-ROOT-CAUSE.md`'s ranked list, ordered by how often he hits it.
Evidence column per the pre-registration: **OBSERVED** = driven and recorded; **OBSERVED-by-class** =
the mechanism was driven, this instance shares it identically.

| # | site / gesture | spoken TODAY (on a muted target) | spoken AFTER | evidence |
|---|---|---|---|---|
| 1 | `navigation.py:27, 64, 95, 140` + content `:51, :98` — ⌃⌘←/→/↑/↓, on or into a muted session | **Nothing.** No tone, no word. `SPOKEN=[] EARCONS=[]`, items pile in B's queue. | Crossing: the folder spearcon / `"{folder}."`, then the seek-and-play content. Within-session: `"Oldest response."` / `"Back to the latest."` / `"N responses back."` / `"Nothing to navigate yet."`, then the content. | **OBSERVED** — P2, both `prev_response` and `prev` |
| 2 | `playback.py:256` (crossed landing) + the head decision — ⌃⌘D onto a muted session's pending ask. **THE REPRODUCED S1.** | **Nothing**, while `voice_state` reads `flowing`. Un-mute → the ask is destroyed; only `"Resumed."` is spoken. The ask is never heard, at any point, ever. | The folder spearcon / `"{folder}."`, then the ask itself: `"A question needs your answer. — at the terminal."` | **OBSERVED** — `probe_muted_crossing.py` (both variants) + C2 counterfactual |
| 2b | `playback.py:219` — ⌃⌘D MISS path re-speaking a stored pending prompt | **Nothing.** (`:222`, three lines below, same `tgt`, has both flags and does speak.) | The stored prompt text. | **OBSERVED-by-class** — same handler, same `tgt`, same held-branch path as row 2 |
| 3 | `decisions.py:266, 268` — ⌃⌘O re-read the options, on a muted session | **Nothing.** | The pending question's options; or `"No options right now."` | **OBSERVED** — P3 |
| 4 | `chooser.py:227` — ⌃⌘Tab commit onto a muted session | **Nothing**, and `speaker()` is now `None` so no flag alone can ever reach it. | The folder spearcon / `"{folder}."` — the landing confirmation. Fork-2 is untouched: the workspace stays on the muted target, the voice is still released, the target is still not un-muted. | **OBSERVED** — P1 + C1 (proves the flag alone is insufficient) |
| 5 | `control.py:190` via `:235` — ⌃⌘= / ⌃⌘− rate nudge while muted | **Nothing.** `rate` changes to 225 and is persisted; he is not told. | `"Rate 225."` — exactly what ⌃⌘V already says through the same helper. | **OBSERVED** — P4, with ⌃⌘V as the in-run control |
| 6 | `lifecycle.py:131` + `:53` — a session starting while ⌃⌘M is active | **Nothing**, and both one-shots are **burned and then destroyed** — never heard for that session's whole life. | Nothing at the moment of the mute (R7 lasting quiet is respected). The markers stay unclaimed; the announce `"{number}, {folder}."` is delivered at the ⌃⌘S-start that first makes the session audible. | **OBSERVED** — P5 (`claim_announce spent=True`, `NEWq=2` → `0`, spoken `[]`) |
| 7 | `teaching.py:91` — a first-encounter hint landing on a stopped stream | **Nothing**, marker burned daemon-run-wide for every session. Currently unreachable for him (gated on `verbosity == "everything"`; he runs `medium`) — live the moment he changes verbosity. | Nothing now; the hint key stays open and teaches at the next real encounter this daemon run. | **OBSERVED-by-class** — identical burn-before-delivery mechanism to row 6, driven in P5 |
| 8 | `host.py:387` `_raise_failed` — the window-raise failed after a jump onto a muted session | **Nothing.** He is left believing the terminal came forward, and types into the wrong window. | `"Bring {folder} forward to type."` | **OBSERVED** — P6 |

**Rows that must NOT change** (regression pins, each with an explicit test):

| site | why it must stay as it is |
|---|---|
| `decisions.py:38` `_announce_decision` | narration, not a gesture answer. A decision arriving on a muted session stays silent — that is what the mute is for. |
| `host.py:1339`, `focus.py:147/149/156`, `playback.py:157`, `lifecycle.py:147`, `prose.py:139` | the 7 provably-safe sites. `control_cue=True` must produce byte-identical behaviour. |
| `chooser.py` Fork-2 (`set_speaker(None)`, no un-mute) | ratified. M2 delivers the cue *without* touching the policy. |
| `playback.py:149` resume-drop | ratified 2026-07-16; §4.5. |
| prose / tool-announce / catch-up ordering | untouched. |

---

## 6. R5 — the doctor rows

Three rows. Each names the fact it reads, the threshold, the exact words when red, and what he
DOES. A row he cannot act on is noise, and noise is worse than absence for someone who can only
listen.

### 6.0 Deviation from "facts already on the STATUS wire" — named up front

The brief says these rows are built only from what `control.py:330-333` already carries. Two of the
three need one new field each. Naming it here rather than letting the executor discover it:

| new STATUS field | why it cannot be derived | cost |
|---|---|---|
| `keepalive_oldest_player_age_s: float \| None` | `on_status` sends `host.keepalive.status()`, a **string** (`disabled\|degraded\|running\|hold\|idle`). The age exists — `KeepAliveManager._players` holds `(proc, spawned_at)` and `self._clock` is monotonic — but nothing exposes it. | one expression in `keepalive.status_age()`, one key in `on_status` |
| `sessions[].live: bool` | `sessions.is_live(sid)`. Without it the wedge row cannot tell a genuinely stuck loop from a **dead** session's backlog, which D3 §4d deliberately never auto-voices — so the row would fire on correct behaviour and the sentence would have to hedge into something he cannot act on. | one call inside the existing list comprehension at `control.py:330-333` |

Both are read-only diagnostics on a wire that already carries eleven of them. Neither changes daemon
behaviour. The voice row (6.3) needs nothing new — `voice` has been an original STATUS key since
day one.

### 6.1 `speech path` — the unclaimed wedge (extends the existing row)

- **Facts read:** `current_item` (False), `voice_state` (`"flowing"`), `speaker_held`,
  `sum(s.queue_len for s in sessions if not s.stopped and s.live)`, `last_drain_age_s`.
- **Threshold:** `WEDGE_HOLD_S = 300.0`. Red when nothing is claimed, the voice is `flowing`, the
  voice is **not** parked on a muted stream (`not speaker_held`), at least one live non-stopped
  stream holds items, and `last_drain_age_s` is `None` or `> 300`.
- **Why it exists:** today this exact state renders **green** — `("speech path", True, "idle
  (nothing claimed by the speak loop)")`. It is the state the confirmed assembler wedge produces
  (`.claude/HANDOFF.md`: an unterminated streamed block leaves `has_pending()` true forever,
  `_stream_quiescent` stays false, the keep-going gate never opens, and **every other session is
  silenced indefinitely**). Doctor currently calls that healthy.
- **Red words:** `speech path: {n} items are waiting in {k} live sessions and nothing has been
  spoken for {m} minutes — the speak loop is stuck, not idle. Restart it: sonari install.`
- **He does:** runs `sonari install` (the established restart action, already the action in four
  other doctor sentences; there is no `sonari restart` verb).
- **False-positive analysis:** stop-all and quiet-hold are excluded by `voice_state`; a starved
  session's own backlog by `not s.stopped`; dead-session backlog by `s.live`. A genuinely idle
  daemon has no queued items and stays green.
- **CORRECTED after this row shipped.** The first clause above used to read "stop-all and
  quiet-hold are excluded by `voice_state`; **per-session mutes** by `not s.stopped`", and the
  second half of that was **false**. `not s.stopped` excludes only the *muted* session's own
  backlog — never the mute itself. Three ratified "deliberate re-engage" lifts
  (`navigation.py`'s crossed nav, `playback.py`'s ⌃⌘D crossed and within-session) set
  `voice_state = "flowing"` and then `sessions.focus()` the voice **onto a stopped stream**: the
  loop holds every tick, every *other* live session starves with `stopped: False`, and the enum
  still reads `flowing`. Past 300 s the row fired a spoken RED — *"Sonari is unhealthy. 1 check
  failed: speech path."* — and named a **destructive** remedy (`sonari install` restarts a healthy
  daemon) immediately after §5 acceptance rows 1 and 2. Proof it was a verdict about the enum and
  not about health: after ⌃⌘S alone the identical physical state rendered green, and one ⌃⌘D
  flipped it red with nothing else changed. **The producers are ratified**
  (`tests/test_sp3_lifts.py::test_jump_decision_lifts_hold` pins the R5 jump-class lift), so the
  repair is consumer-side: STATUS gained `speaker_held` (the voice owner's own stream is
  `stopped` — not derivable from `sessions`, which names no speaker, nor from `foreground`, which
  is the workspace), and the row returns green `held (the voice is on a muted session - un-mute it
  to resume)` instead of the wedge sentence. The assembler wedge stays red: there the speaker is
  not stopped. Receipts: `tests/test_doctor_speech_path.py`'s three `speaker_held` tests (one
  drives the ⌃⌘S-then-⌃⌘D sequence through a real daemon) and
  `tests/test_status_diagnostics.py`'s three producer tests.

### 6.2 `keepalive` — the stalled overlap chain (extends the existing row)

- **Fact read:** `keepalive_oldest_player_age_s`.
- **Threshold:** `SILENCE_S + OVERLAP_S = 305.0`. Each player plays 300 s of silence and its
  successor is armed at 295 s (`keepalive.py:16, 73, 287-290`), so **no live player may ever be
  older than 305 s.** Older = the chain stalled or a player was orphaned.
- **Why it exists:** the reviewed history of this class is an orphaned-overlap-chain Critical, and
  there is a **leaked keepalive player, pid 80075, on his live install right now** (recorded in
  `STATE.md`). Today `status()` returns `"running"` for that state and the row is green.
- **Red words:** `keepalive: the same silent player has been holding the audio device for {m}
  minutes — the overlap chain stalled and Bluetooth clipping will come back. Run: sonari keepalive
  off, then sonari keepalive on.`
- **He does:** exactly that toggle — it is already the documented and only recovery lever
  (`set_enabled`'s False→True edge is what forgives a give-up, `keepalive.py`).

### 6.3 `voice` — the configured voice is not installed (new row; retires `enhanced voice`)

- **Facts read:** `STATUS.voice` (falling back to `load_config()["voice"]` when the daemon is
  unreachable), and `_platform().tts.list_voices()`.
- **Threshold:** membership. `None` → green, `"system default"`.
- **Why it exists — the row it replaces is structurally unfailable.** `supervisor.doctor_rows()`
  reports `("enhanced voice", bool(voice), ...)` where `voice = MacTtsBackend().best_voice()`, and
  `best_voice` returns `"Samantha"` as a hard-coded last resort on every path (tts.py:214-234).
  `bool("Samantha")` is `True`. Measured on this machine: the row reports **"Samantha"** while his
  config runs **"Voice 1"** — so it is not merely unfailable, it is reporting a voice he does not
  use. Meanwhile a `config["voice"]` naming a voice that is gone (an OS update, a broken Kokoro
  venv) makes `say` exit non-zero on **every utterance** — total silence with a green doctor.
- **Red words:** `voice: the configured voice, {name}, is not installed — every utterance will
  fail. Run: sonari voice, to hear what is installed, then: sonari voice {a name}.`
- **He does:** `sonari voice` (lists), then `sonari voice <name>`.
- **Fail-open:** an empty or unreadable listing (`say` missing, `subprocess` error) renders the row
  green with `"voice listing unavailable"`. A doctor that cries wolf about a working voice is worse
  than one that stays quiet.
- **Verified green on his install today:** `"Voice 1" in list_voices()` → `True` (measured;
  `_parse_listing` strips only `(Premium)`/`(Enhanced)` qualifiers, so a bare name with a space and
  a digit survives). This row will not go red on him on day one.

Each new/changed row gets a `checkmeta._SPOKEN` entry (`"voice": "voice"`); none is `_WARN` — all
three are genuinely unhealthy states.

---

## 7. Test strategy — TDD, and what fails on `main` today

Every test below is written RED first. The "fails on main because" column is the evidence that it
is a real test and not a tautology; where it says OBSERVED, the failure has already been produced.

### D0.1

| test | asserts | fails on `main` because |
|---|---|---|
| `test_isolation_refuses_the_real_home` | with `$HOME` forced to `pwd.getpwuid(os.getuid()).pw_dir`, the conftest gate aborts before collection | no such gate exists |
| `test_no_sonari_path_resolves_under_home_while_isolated` | no `paths.*` constant, and no module's `LAUNCH_AGENT_PATH`, resolves under `$HOME` while the fixture is active | **OBSERVED**: 4 leaks — `KOKORO_VENV`, `RAISE_BIN_PATH`, both `LAUNCH_AGENT_PATH`s (canary output in §1.5) |
| `test_the_real_home_is_byte_identical_after_the_session` | session-scoped canary over `~/.sonari`, `~/.local/bin/sonari`, the two plists | no canary exists |

### D0.2

| test | asserts | fails on `main` because |
|---|---|---|
| `test_fake_speaker_records_a_silent_cue_instead_of_an_earcon` | `FakeSpeaker(earcons={}).transient("repoint")` → `earcons == []`, `silent_cues == ["repoint"]` | `daemon_helpers.py:61-64` appends unconditionally |
| `test_the_suite_fails_a_test_that_fired_a_silent_cue` | the autouse teardown fails a deliberately-silent test | no such fixture |
| `test_make_daemon_seeds_the_fake_from_the_config_earcons` | `make_daemon(earcons=LEGACY_SIX)` → the fake resolves against those six | `make_daemon` has no `earcons` parameter |

### R3

| test | asserts | fails on `main` because |
|---|---|---|
| `test_load_config_merges_new_earcon_kinds_into_a_legacy_config` | writing his real 6-key block to `CONFIG_PATH`, `load_config()["earcons"]["repoint"] == ".../Bottle.aiff"` | **OBSERVED (C3)**: `DEFAULTS` has no `earcons` key, so the merge yields exactly the six persisted keys — `KeyError` |
| `test_repoint_is_audible_on_a_legacy_config` (daemon-level; needs D0.2) | `make_daemon(earcons=LEGACY_SIX)`, drive `OS_FOCUS` to another session → `speaker.earcons == ["repoint"]`, `silent_cues == []` | **passes falsely on `main`** (the fake lies); fails the moment D0.2 lands. This is the "tests pin the call, not the ear" closure, made concrete. |
| `test_an_explicit_null_still_mutes_a_cue` | `{"earcons": {"choice": null}}` → silent | passes today and must keep passing — regression pin, **verified** |
| `test_only_one_resolver_reads_the_earcon_table` | `_FALLBACK_EARCONS` appears zero times under `src/` | 8 hits |
| `test_every_registered_cue_has_a_default_asset` | `set(CUES) - ASSET_EXEMPT <= set(DEFAULTS["earcons"])`, where `ASSET_EXEMPT` is `{speech, summary_voice, pitch_up, pitch_down, callsign}` each with its reason | **passes on `main`** against `_DEFAULTS` — declared here as a KEEP-GREEN forward guard, not a RED test. It is the assertion that makes a future cue impossible to ship DOA. |

### R4

| test | asserts | fails on `main` because |
|---|---|---|
| `test_every_action_declares_control_cue` | `"control_cue" in meta` for `ACTIONS` ∪ `CONTROL_GESTURES` | the key does not exist |
| `test_a_control_cue_waiver_carries_a_reason` | `False` ⇒ non-empty waiver | as above |
| `test_the_legacy_exempt_flags_are_gone` | zero `mute_exempt`/`pause_exempt` under `src/` | 71 hits |
| `test_every_hotkeyd_message_type_is_a_declared_gesture` | the Swift's `"type": "..."` literals ⊆ declared ∪ machinery allowlist | `CONTROL_GESTURES` does not exist |
| `test_every_gesture_answers_on_a_muted_session[...]` (25 params) | something is spoken or toned | **OBSERVED**: 8 params silent (P1–P6, C1, `probe_muted_crossing.py`) |
| `test_a_control_cue_on_a_stopped_non_speaker_stream_is_voiced` | M2 directly: `speaker() is None`, cue on a stopped stream → spoken | **OBSERVED (C1)**: the flag alone delivers nothing |
| `test_jump_decision_speaks_the_ask_on_a_muted_target` | S1 end-to-end, both variants | **OBSERVED**: `probe_muted_crossing.py` → `SPOKEN=[]` |
| `test_a_rate_nudge_reads_back_while_muted` | `"Rate 225."` | **OBSERVED (P4)** |
| `test_a_one_shot_marker_survives_an_undeliverable_enqueue` | after P5's sequence, `claim_announce` is still unclaimed and the announce arrives at ⌃⌘S-start | **OBSERVED (P5)**: `claim_announce spent=True`, spoken `[]` |
| `test_maybe_hint_leaves_the_key_open_on_a_stopped_stream` | the docstring's own promise | the code checks `session is None`, never `.stopped` |
| `test_a_decision_announcement_stays_silent_on_a_muted_session` | the mute still means something | regression pin — must pass before and after |
| `test_the_seven_safe_sites_are_byte_identical` | the 7 provably-safe sites' output is unchanged under `control_cue` | regression pin |

### R5

| test | asserts | fails on `main` because |
|---|---|---|
| `test_speech_path_fails_when_live_streams_hold_and_nothing_drains` | red, with the exact sentence | the row renders green `"idle (nothing claimed by the speak loop)"` |
| `test_speech_path_stays_green_for_a_deliberate_mute` / `..._for_a_dead_session_backlog` | no false positives | the second needs `sessions[].live`, which does not exist |
| `test_keepalive_fails_on_a_player_older_than_305s` | red, with the exact sentence | `status()` returns `"running"`; no age is on the wire |
| `test_voice_row_fails_for_an_uninstalled_voice` | red, with the exact sentence | the row does not exist |
| `test_voice_row_is_green_for_the_owners_configured_voice` | `"Voice 1"` → green | **verified** against the live listing |
| `test_voice_row_fails_open_on_an_unreadable_listing` | green with `"voice listing unavailable"` | as above |
| `test_enhanced_voice_row_is_gone` | the structurally-unfailable row is retired | it is still emitted |

---

## 8. Ordering and dependencies

```
D0.1  hermetic refusal + canary        ── gates everything. Nothing else may be written first.
  │        (adopt wave-1 T5's mechanism; add refusal + canary)
  ▼
D0.2  honest FakeSpeaker + make_daemon(earcons=)
  │        depends on D0.1 (it changes 83 test files' harness; do that behind a refusal, not in front of one)
  ├──────────────► R3  earcon defaults into config.DEFAULTS
  │                 │      D0.2 is what makes R3's daemon-level receipt able to fail
  │                 │
  ├──────────────► R4  control_cue: M1 flag → M2 held-branch scan → M3 requested content
  │                 │      M1 first (the type change), then M2 (delivery), then M3 (content),
  │                 │      then the 8 sites, then the one-shot markers.
  │                 │      R4's os_focus/repoint behavioural row needs R3. ────────┐
  │                 ▼                                                              │
  └──────────────► R5  doctor rows  ◄──── serialize AFTER R4: both edit control.py's on_status
                                          and R4 edits doctor.py's neighbourhood via wave-1's I3 hunk
```

Within R4, the order is not negotiable: M1 is a type change that touches all 40 flagged sites at
once; M2 changes delivery and must be proven against C1 before any site depends on it; M3 rides on
both. Doing the 8 sites before M2 produces a table where row 4 is still silent and the test suite
says otherwise.

**Suite gates at every step:** `1592 passed / 1 skipped` on `main` today (measured), guards green,
`gen_docs --check` rc 0. Every run under a sacrificial HOME.

---

## 9. Risks, and the reversal condition for each choice

| # | choice | risk | reversal condition |
|---|---|---|---|
| 1 | **Adopt wave-1's isolation mechanism instead of late-bound accessors** | wave 1 may never merge; the fallback cherry-pick could carry unrelated drift | If the six T5 commits do not cherry-pick cleanly onto `main`, build late-bound accessors instead — the property required is "forgetting is a test failure", and either mechanism delivers it. |
| 2 | **Hard replacement of the two flags** (no alias, no deprecation) | one missed call site becomes a `TypeError` at import or a silently-dropped kwarg | `_enqueue` has no `**kwargs`, so a stale `mute_exempt=` is an immediate `TypeError`, not a silent no-op — the failure mode is loud by construction. Reverse only if a consumer outside this repo is found reading the flags; none exists (§4.1). |
| 3 | **M2: the held branch scans every stopped stream** | a control cue on a *background* muted stream now interrupts the speaker's flow at the next item boundary | If he reports a cue arriving from a session he was not attending, narrow the scan to `{speaker(), workspace()}` instead of all stopped streams. That still fixes row 4 (workspace stays on the muted target after Fork-2) and re-opens only the exotic cases. |
| 4 | **M3: nav and ⌃⌘D read *content* through a mute** | a full turn's readout breaking a mute may be too loud | Narrow `control_cue` on `navigation.py:51/:98` to the orientation cue only, leaving content muted. Precedent cuts the other way (⌃⌘R and catch-up already read through a mute), so this is a taste reversal, not a correctness one — §10. |
| 5 | **`config.DEFAULTS` carries macOS asset paths** | a portable core holding OS-specific literals | The OS seam is DEAD by owner ruling; `speaker.py` (also CORE) holds the same literals today and this change removes them. If the OS seam ever revives, move the table behind `get_platform()` and give `load_config()` a lazy platform call — one function, no call-site change. |
| 6 | **Deleting `EarconBackend.default_earcons`** | removes a platform-contract method | Its only caller (`bootstrap.py:97`) is deleted in the same change. Keeping it would leave a second source of truth for the asset table — the exact drift this receipt closes. Reverse if a second OS backend is ever added. |
| 7 | **One-shot markers claim on deliverability** | a marker that never becomes deliverable never fires at all | Bounded: `claim_announce` is delivered at ⌃⌘S-start, `_hinted` is daemon-run-scoped and re-teaches on the next real encounter, `guided` re-checks on the next SESSION_START. Reverse (back to claim-on-attempt) if he reports a repeated announce. |
| 8 | **Two new STATUS fields** | scope creep on the wire | Both are read-only diagnostics. Reverse `sessions[].live` if he judges dead-session-backlog false positives acceptable; the row then hedges its sentence. `keepalive_oldest_player_age_s` has no alternative — drop the row rather than the field. |
| 9 | **Wave-1 collision** (see below) | conflicts in `doctor.py`, `host.py`, `teaching.py`, `decisions.py`, `speaker.py`, `bootstrap.py` | Sequencing is his call, not this spec's — the constraint is stated below. |

### 9.1 The wave-1 collision, stated precisely

`build/safety-net-closure` — 18 commits, parked at his gate, **not merged**. Overlap with this spec:

| file | wave 1 does | this spec does | collision |
|---|---|---|---|
| `tests/conftest.py`, `tests/_isolation.py`, `paths.py`, `platform/macos/{supervisor,hotkeys}.py` | T5 hermeticity (rewrites the fixture) | D0.1 | **total** — §3.1 resolves it by adopting rather than competing |
| `cli/doctor.py` | I3: adds a `speak.fail_memo` branch to the **`if not claimed:`** line | R5 extends the same line | **direct, small, mechanical** |
| `daemon/host.py` (+194) | `_signal_speak_failure`, `_sanction_dead_read`'s docstring | M1/M2 in `_enqueue`, `_speak_loop_once`, `_attributed_text`, `_raise_failed` | overlapping regions, disjoint hunks |
| `features/decisions.py:266,268` | T2 sanction (dead axis) | R4 rows 3 (stopped axis) | **same two lines** |
| `features/teaching.py:34,71` | T2 sanction | M1 flag rename | same two lines |
| `speaker.py` (+50) | `SpeakFailure` | R3 deletes `_FALLBACK_EARCONS` | disjoint hunks, same file |
| `daemon/bootstrap.py` (+41) | boot-cue memo | R3 deletes lines 96-97 | disjoint hunks, same file |

**If wave 1 merges first** (recommended): D0.1 shrinks to refusal + canary; the other four
collisions are ordinary small merges resolved in the direction of *both* changes (T2's `at_front=`
sanction and this spec's `control_cue=True` are orthogonal and both belong).
**If this spec merges first**: wave 1 must be re-based across a flag rename that touches four of its
files, and its version lockstep (already stale at 0.10.1, needing 0.11.x) gets harder. That is the
worse order, and it is stated so he can pick, not so this spec can.

---

## 10. Open questions — nothing here is routed to him as a live question

1. **Sequencing: does wave 1 merge before this?** §9.1 has the cost of each order.
   **Recommendation: wave 1 first.** It is built and reviewed, it makes D0.1 nearly free, and the
   reverse order taxes a branch already at his gate. Executable either way — the fallback in §3.1
   needs no answer to start.
2. **Should ⌃⌘D onto a muted session also un-mute it?** Today's ratified rule (R7, chooser Fork 2)
   is that a mute lasts until its own ⌃⌘S-start, and this spec keeps it: ⌃⌘D delivers the ask and
   leaves the session muted. **Recommendation: keep the mute.** Reversal: if he finds himself
   pressing ⌃⌘D then ⌃⌘S every single time, un-mute-on-⌃⌘D is the right rule and it is a two-line
   change.
3. **Is nav's seek-and-play through a mute too much speech?** §9 risk 4. Precedent (⌃⌘R, catch-up)
   says it is right; his ear decides. Reversal is scoped to two call sites.
4. **A red doctor row's DETAIL is never spoken.** `verdict()` speaks only the failing check's short
   name ("speech path", "voice"); the sentence carrying the action is printed, not said. For an
   operator who works by ear that is half a row. **Recommendation (not in this spec):** when exactly
   one row failed, append its detail to the spoken verdict. Small, and it changes a shipped spoken
   string — so it belongs to a wording pass, not here.
5. **Should the `neither`-bucket sites that are NOT reachable from a gesture stay flagless?**
   `host.py:564`, `chooser.py:130`, `control.py:464`, `playback.py:309`, `prose.py:63` are narration
   or internal re-queues. **Recommendation: yes, leave them.** They are content; the mute is
   supposed to hold them. Named here so the executor does not "complete" the sweep by flagging them.
6. **The roster debris** (`t26-probe` holding number **1**, `workdir_permcheck3/4`, `t26-ear`) and
   whatever wrote `workdir_permcheck3/4` into his REAL `~/.sonari/state.json`. Same family as the
   twice-repeated install destruction: probes reaching the live state dir. D0.1's refusal prevents
   *new* instances; cleaning the existing four and giving `sessions.unregister()` a second caller is
   **Direction 2**, out of scope here.

---

## 11. Deferred to audition — his ears, never a live question

| # | item | why it needs his ear |
|---|---|---|
| 1 | **The `repoint` tone itself** (Bottle.aiff, `focus.py:72`) | It has been dead for five weeks. The asset was ratified by sample at ear-batch-2 (2026-08-01) but **he has never heard it fire in context** — every click that moves his workspace will now make a sound. Frequency, not timbre, is the risk. |
| 2 | `submit_ack` (Morse.aiff) | becomes resolvable in R3 but stays dark (`submit_ack_enabled: False`). If he ever flips it on, it is a first hearing. |
| 3 | The SESSION_START announce arriving at ⌃⌘S-start instead of at session start (§4.5) | a familiar string in a new moment. |
| 4 | Nav's seek-and-play speaking through a mute (§10.3) | volume of speech, not wording. |
| 5 | The three doctor red sentences (§6.1–6.3) | PROVISIONAL wording. They are read aloud only as their short names today, but they are written to be sayable in case §10.4 lands. |
| 6 | `checkmeta._SPOKEN["voice"] = "voice"` | one word inside the verdict sentence. |
| 7 | Whether "Rate 225." arriving while muted is welcome or startling | it is the correct behaviour; the ear decides if the number is the right readback. |

---

## 12. Safety protocol — binding on every step of execution

- **Every pytest run and every mutating probe runs under a sacrificial `HOME`.** The suite has
  destroyed his real install TWICE; `~/.sonari` and `~/.local/bin` are outside git, so
  `git status` clean is NOT evidence. The only real check is `sonari doctor` returning its full row
  set. Never `git add -A` in this repo.
- **His daemon is LIVE.** Read-only observation only: `status`, `doctor`, logs, `~/.sonari` reads.
  No restart, no rebind, no control messages, no `say`/`afplay` that produces audio.
  (`say -v '?'` is a listing, produces no audio, and is the one exception used in §6.3's
  verification.)
- **Probe rigs to reuse, not re-derive:** `scratchpad/e3-review/probe_muted_crossing.py` (the S1),
  `scratchpad/e3-review/probe_receipts.py` (P1–P6), `scratchpad/e3-review/probe_counterfactual.py` (C1–C3),
  `scratchpad/e3-review/span.py` (the 25/0/15 parse, read-only AST). `probe_receipts.py` and
  `probe_counterfactual.py` open with a `pwd.getpwuid` refusal guard that aborts unless `$HOME`
  is sacrificial — keep it, and add it to `probe_muted_crossing.py`, which relies on the
  environment alone.
- **`git log --since=DATE -- <path>` silently returns EMPTY in this repo** for files that do have
  commits in the window. Use unfiltered path logs, explicit-offset `--since`, or `git log -S`.
- **Never route a question to him.** Open items go to §10; ear items go to §11.
