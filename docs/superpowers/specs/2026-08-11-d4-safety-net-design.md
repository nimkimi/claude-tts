# D4 — Make the safety net real (design)

**Date:** 2026-08-11 · **Status:** owner-approved design ("go", 2026-08-11), pre-plan · **Direction:** MAP §5 **D4** (`.superpowers/sdd/e2-streamline/MAP.md:313`) · **Branch:** `build/d4-safety-net` (off `main @ e92bfd2`, v0.9.0) · **Target:** **v0.10.0**

> **Scope was an owner call.** The session recommended splitting D4 into a diagnosis half and an offboarding half; the owner took **the full MAP list in one campaign**, and additionally folded in the queued hook-responsiveness fix and the surviving half of issue #54. Honest size: **~20+ tasks**, larger than D3 (13 tasks, 24 commits, four fix waves). The plan must not pretend otherwise.

---

## 1. Problem

`sonari doctor` is branded the safety net. It is not one.

It is **sighted** — 97 lines in `cli/doctor.py` that build `(check, ok, detail)` tuples and `print()` them. An eyes-free user cannot run the tool that tells them why they can't hear anything.

It is **shallow** — its daemon row is a `PING`, answered by the socket thread. A daemon whose speech pipeline is wedged answers `PING` and gets a green row. The check that exists to catch "Sonari has gone quiet" cannot see the most likely cause of quiet.

It is **blind** — nothing checks the heartbeat, `state.json` freshness or last-restore outcome (P17), hotkeyd *liveness* as opposed to mere presence, the faulthandler log, or login/CLI reachability.

And **`sonari uninstall` does not uninstall.** This is the correction of record described in §2.1 — the mechanism is not the one the MAP and this spec's first revision assumed. Three faults compose:

1. **`launchctl unload` cannot stop a lazily-spawned daemon.** `ensure_running` (`daemon/bootstrap.py:16-21`) starts the daemon with `subprocess.Popen(..., start_new_session=True)` — a detached session leader that is **not a launchd job**. Unloading the LaunchAgent has no effect on it.
2. **Hooks resurrect it afterwards.** Uninstall's last line asks the user to disable the plugin *manually*, so hook events keep firing. `bin/sonari-hook:79` calls `client.ensure_daemon()` **unconditionally** — no install gate — which finds no lockfile, calls `ensure_running`, and **respawns the daemon** from the plugin's own `src/` (which survives the `APP_DIR` removal). Every resurrected daemon is born an orphan.
3. **Removing the lockfile makes the survivor unfindable.** `LOCK_PATH` is in the removed-artifacts list, and it is the only record of the daemon's host/port/**pid**. After uninstall, nothing can locate the process that is still running — and still speaking.

The spoken uninstall confirmation D4 adds would otherwise narrate *"Sonari removed"* while Sonari keeps talking. That is the exact class of confident lie this campaign exists to end.

The rest of the lifecycle is equally un-eared: `sonari install` ends in a silent transcript; `sonari uninstall` has no spoken confirmation, discards `launchctl unload`'s return code (so "Removed LaunchAgent" prints on a file delete alone — `platform/macos/supervisor.py:228-231`), and leaves `state.json` — verbatim spoken text, up to 200 utterances per session — on disk, absent from PRIVACY.md's inventory and contradicting its own "not designed to record session content" claim.

D4 closes all of it, and in doing so completes **R1** ("the reader cannot announce its own death").

## 2.1 Correction of record (2026-08-11, during plan authoring)

The first revision of this spec claimed doctor **self-heals**: that `client.send` → `ensure_daemon` → `ensure_running` relaunches the daemon, making the socket row report `reachable` right after uninstall. **That is false, and the tree falsifies it:**

- `client.send` (`client.py:15-40`) connects or raises `DaemonNotRunning`. It never calls `ensure_daemon`.
- `ensure_daemon` has **exactly one caller in the entire repo**: `bin/sonari-hook:79`.
- Therefore **`sonari doctor` never relaunches anything.** Decision #9's *intent* — doctor observes, never repairs — was already true; only the invariant was unpinned.

The claim rode unchecked from MAP §5's bullet into this spec. It was caught by reading the code before a single build task ran. **The real defect behind the misdiagnosis is worse** — §1's three-fault "uninstall does not uninstall" — and it is what D4 now fixes.

**Consequence for the plan:** the "probe-only send path" task is deleted. It would have fixed nothing. What survives is the **invariant pin** (§4.2) and the genuine teardown work (§8.1).

## 2. What is already true — verified, so the plan does not rediscover it

These were confirmed in the tree at `e92bfd2` during design. They are load-bearing for the decisions below.

| Fact | Evidence |
|---|---|
| **The witness exists and works.** hotkeyd pings speechd every ~5 s; at 3 consecutive failures (~15 s) it sounds an alarm **once** and re-arms only after a later successful send. Config: `alarmAsset` / `alarmWords` / `alarmEnabled`, with compiled-in defaults so a stale resolved file cannot silently disable it. | `hotkeyd/sonari-hotkeyd.swift:167-177`, `:208-210` |
| **The witness alarm is a raw shell-out, deliberately.** Its own comment: *"the daemon is the thing that just died, so nothing may route through it."* | `hotkeyd/sonari-hotkeyd.swift:169-171` |
| The lazy relaunch is spawned with **stdin/stdout/stderr all `DEVNULL`**. | `platform/macos/supervisor.py:168-170` |
| `_signal_speak_failure` cues `error_system` **with** `SPEAK_FAILURE_WORD` when the failing item's session is known, and a **bare tone** when it is not; then writes a traceback to `sys.stderr` — which is the `DEVNULL` above. | `daemon/host.py:908-932` (`:922`, `:924`, `:930`) |
| `say` is already invoked with `--` before the text, so narration starting with `-` is not silently dropped. | `platform/macos/tts.py:191-194` |
| The faulthandler log already resolves through `SONARI_DIR` (imported **live**, so tests' monkeypatch applies) and opens mode `'w'` so it cannot grow unbounded. | `daemon/bootstrap.py:36-43` |
| **No `isatty` guard exists anywhere in `src/`.** Four test files call `doctor()`. | `grep -rn "isatty" src/` → empty |
| `ensure_daemon(timeout=3.0)` polls `_connectable()` every 50 ms with no backoff and no failure memo, after calling `ensure_running()`. | `client.py:43-52` |
| Five version sites are pinned by the manifest test. | `pyproject.toml:7`, `.claude-plugin/plugin.json:4`, `.claude-plugin/marketplace.json:12`, `src/sonari/__init__.py:4`, `tests/test_manifests.py:88` |
| **The lockfile carries the daemon's `pid`** — alongside host/port/token. So the running process is addressable *before* the lockfile is deleted. | `platform/transport.py:18-20` |
| **A SIGTERM handler already exists** and exits `run()` into SP6's clean shutdown flush. SIGTERM is therefore the *correct* stop mechanism — it preserves state rather than hard-killing. | `daemon/host.py:1346-1361` |
| **There is no SHUTDOWN/QUIT message type.** `STOP` / `STOP_ALL` / `STOP_SESSION` stop *speech*, not the process. | `protocol.py:9-50` |
| **`SINGLETON_PATH` is absent from uninstall's artifact list**, and the daemon holds it as a process-lifetime `flock`. Acquiring it is therefore a positive proof that no daemon survives. | `cli/install.py:155-162`, `platform/transport.py:55-58` |
| Transport is **TCP with a lockfile**, not a Unix socket — `connectable()` reads the lockfile first, so deleting it makes a live daemon unreachable *and* unfindable. | `platform/transport.py:31-52`, `paths.py:29-32` |

**Two MAP items are therefore already closed and must not be re-opened as work:**
- §7(b) *"Build the witness?"* — built and shipped in D2+D7. What remains for D4 is **checking that it is alive and armed** (R1's "the watchdog is itself unwatched").
- The `say --` separator (issue #50) and the faulthandler path (issue #14) — both fixed; the issues were closed during this design with the evidence above.

## 3. Approach — three layers

The current `doctor()` is one function doing collection, formatting, and output. D4 needs the same facts in three different shapes (spoken sentence, printed rows, per-adapter contribution), so the first move is to separate them.

```
  checks (registry)  ──►  rows  ──►  verdict (pure fn)  ──►  delivery
   observe only            data       rows → sentence       daemon-first,
   never repair                                             direct fallback
```

- **Rejected — keep one function, add a `speak=` flag.** The verdict sentence, the printed table, and M1's per-adapter checks are three consumers of one dataset; threading a flag through a single function makes each new consumer another branch. The registry split is what lets M1 Task 7 add `doctor_checks()` without touching doctor's core.
- **Rejected — make the daemon own doctor entirely** (CLI just asks the daemon to self-diagnose and speak). Fatal: the failure being diagnosed is often "the daemon is dead or wedged", and a self-diagnosis routed through the dead thing is exactly what hotkeyd's witness comment forbids.

## 4. Layer 1 — the check registry

Each check becomes a callable returning a record. Beyond today's `(check, ok, detail)` it carries:

- **`spoken`** — a short, sayable name distinct from the printed one (`"daemon socket"`, `"hotkeyd"`, `"neural voices"`). The printed name may be long and precise; the spoken name must survive being read in a list.
- **`severity`** — `fail` vs `warn`. A `warn` row (e.g. neural voices absent-but-optional) is **printed only**: it never makes the verdict "unhealthy" and is **never named in the spoken sentence**. Mixing warnings into a verdict that then says "healthy" would reproduce, in the safety net itself, the ambiguous-signal problem D2 spent a campaign removing.

### 4.1 New rows

| Row | What it asserts | Closes |
|---|---|---|
| **speech path** | The daemon reports its speaker-thread heartbeat and the age of its last *successful* utterance. Stale heartbeat ⇒ FAIL **even though `PING` succeeds**. | the shallow-check defect |
| **restore health** | `state.json` exists, parses, `STATE_VERSION` matches, mtime freshness, last-restore outcome, session + utterance counts. | **P17** |
| **hotkeyd** | Process **alive** (not merely installed) **and** its witness armed: `alarmEnabled` true and the resolved `alarmAsset` readable. | **R1** — the watchdog is watched |
| **fault log** | Whether `SONARI_DIR/faulthandler.log` holds a crash dump written *after* the arming line — i.e. did the daemon die natively since boot. | silent C-level deaths |
| **reachability** | Both LaunchAgents loaded, and `sonari` resolvable on `PATH`. | R10 / login-item drift |

### 4.2 The side-effect-free invariant (pin, not a fix)

Per §2.1, doctor **already** never relaunches — `client.send` connects or raises, and `ensure_daemon` is reachable only from `bin/sonari-hook`. There is no repair work to do here.

What D4 owes is the **pin**: a test proving that a full `doctor()` run spawns **zero** processes, so a future edit cannot quietly reintroduce a self-healing probe. A spawn-counting fake is the mechanism. This is cheap and it is the whole task.

**Additionally, one new row makes the orphan visible:** `daemon socket` gains a supervision detail — whether the reachable daemon is a **launchd job** or a **detached orphan** (`launchctl list <label>` vs a live lockfile `pid` with no corresponding job). An orphan is a `warn`, not a `fail` — it works, it just cannot be stopped by `launchctl`, which is precisely what §8.1 fixes.

## 5. Layer 2 — the verdict

A **pure, total** function: rows → one sentence. No I/O, no clock, no config reads.

- All green → `"Sonari is healthy. Fifteen checks passed."`
- Failures → `"Sonari is unhealthy. Three checks failed: daemon socket, hotkeyd, neural voices."`

The counts in those examples are **illustrative, not specified**: the sentence reports however many rows the registry actually produced, which varies with platform backend and with M1's per-adapter contribution. The plan must not pin a literal total.

**Why enumerate rather than headline-only:** the shipped rule already forbids a relaying session from glossing doctor. A count-only verdict would gloss it by ear instead. Enumeration is self-bounding — names are spoken only for failures, and a healthy system says one short line.

Rejected: reading every row aloud (≈15 rows of speech in the common all-green case trains the user to stop listening — which destroys the check that matters), and headline-plus-drill-down (adds a gesture and a mode for a list that is short by construction).

## 6. Layer 3 — delivery

**When it speaks.** Speak when `sys.stdout.isatty()`; stay silent when piped or redirected. `--speak` and `--quiet` override in both directions. This is the standard convention (`git`, `ls`, `grep`), it keeps the 1402-test suite silent by default, and it means the flag is never needed in the case that actually matters — a human at a terminal.

**How it speaks.**
1. **Daemon-first.** Send the verdict as a normal utterance so it obeys D8's atomic cue+speech contract and can never interleave with live session speech.
2. **Direct fallback** — a raw `say` (with `--`, per `tts.py:194`) plus an earcon — when the daemon is unreachable, the send fails, **or the speech-path row is red**. This is deliberately the same shape, and the same reasoning, as the witness alarm's raw shell-out.

**The verdict is its own end-to-end proof.** If the sentence is heard, the whole ear path just worked — synthesis, playback, routing. No separate probe tone is needed, and no extra noise is added. The heartbeat row (§4.1) serves the printed and CI reader, who cannot hear anything.

## 7. The "try sonari doctor" cue

A suppressor keyed by **failure class**: fire once, then stay silent for that class until a later **success** of that class re-arms it.

This is not a new suppression model — it is the witness's shipped pattern (`sonari-hotkeyd.swift:167-173`) applied in Python. Rejected: cue on every failure (a repeating fault becomes a repeating nag, the user mutes it, and the signal is lost) and a hand-picked "hard failures only" set (the same rule minus the re-arm, with a hard/soft line that will be wrong at the edges).

Applied to the failure paths D4 already touches: `_signal_speak_failure`, earcon spawn failure, and daemon-unreachable in `client`.

## 8. Lifecycle

**`sonari install`** ends with the **same verdict** (§5). One policy, not a second one that can drift.

### 8.1 `sonari uninstall` — the pinned teardown sequence

> **SCOPE RIDER — APPROVED 2026-08-11.** Steps 3 and 5 (actually stopping the daemon, and closing the hook-resurrection path) were new scope discovered during plan authoring, not present in the MAP or the approved design. They are the real defect behind §2.1's misdiagnosis, and the owner folded them into D4 on the recommendation. Decision #15 in the register.

The order is **pinned**, because the disclosure and the teardown compete for the same voice, and because stopping a process you have already made unfindable is impossible:

1. **Ask first, while the daemon is still alive.** *"Sonari saved transcript text from N sessions. Delete it?"* goes out on the **normal daemon-first path** (§6), so the question obeys D8's contract and cannot collide with whatever the user is still hearing. **Exception:** if the daemon is already dead, the disclosure uses the **direct fallback** — the question must be audible precisely when Sonari is broken, which is a common reason to be uninstalling.
2. **Unload the LaunchAgents first**, so launchd cannot restart what step 3 is about to stop — and **honour `launchctl unload`'s return code** instead of discarding it (`supervisor.py:228-231`).
3. **Stop the running daemon.** Read `pid` from the lockfile **before** deleting it (`transport.py:18-20`) and send **SIGTERM** — which the daemon already handles by exiting into SP6's clean shutdown flush (`host.py:1346-1361`), so state is preserved rather than truncated. This is the step that makes uninstall true for a lazily-spawned orphan.
4. **Verify it actually stopped** by acquiring the `SINGLETON_PATH` flock. The daemon holds it for its process lifetime, so a successful acquire is **positive proof** no daemon survives — not an inference from a missing lockfile. On timeout, say so out loud rather than reporting success.
5. **Close the resurrection path.** `bin/sonari-hook` gates its `ensure_daemon()` call on the install record existing. It may still `send()` to an already-reachable daemon, but it must never **spawn** one for an uninstalled Sonari. `INSTALL_RECORD_PATH` is already removed by uninstall, so the gate needs no new state.
6. **Then remove files**, purging or preserving `state.json` per the answer from step 1.

Two orderings are explicitly rejected: **unloading before asking** (it kills the voice that has to ask the question, forcing every disclosure down the fallback path for no gain), and **deleting the lockfile before stopping the process** (it discards the only record of the pid — today's behaviour, and the reason the orphan is unfindable).

Non-interactive: `--purge-transcripts` / `--keep-transcripts`. With neither flag and no tty, the default is **keep**, and the path is printed. Silence must never destroy data.

With §4.2 in place, doctor no longer self-heals a daemon that uninstall just stopped.

**PRIVACY.md** gains `state.json` in its inventory — what it contains, where it lives, how long it persists, how to purge it — and loses the "not designed to record session content" claim its own contents contradict.

## 9. Hook responsiveness (folded in)

Previously a separate queue; the owner folded it into D4 because the cost appears exactly when the daemon is broken, which is the state D4 exists to make honest — and because doctor's own probe traverses the same code.

- **`hooks/hooks.json`:** `async: true` on every registration that never returns a decision. **`PermissionRequest` stays synchronous** — it answers, so it cannot be fire-and-forget.
- **`ensure_daemon`:** timebox plus backoff plus a short-lived failure memo, so a broken daemon costs one timeout rather than one per event.
- **Un-`DEVNULL` the relaunch's stderr** (`supervisor.py:168-170`) into a real log under `SONARI_DIR`, so the traceback at `host.py:930` survives to be read — and so the new **fault log** row has something to report.

## 10. Issue #54 — the failure that cannot announce itself

Verified partly fixed: `host.py:922` already speaks a word with the tone when the session is known. Two gaps survive, and both are D4's own defect class one layer down:

1. `host.py:924` — the session-less path is a **bare tone**, no word. It gets a word.
2. The word is spoken **through the TTS path that just failed**. It reuses Layer 3's direct fallback (§6).

## 11. Error handling

Doctor's existing contract — **never raise** — extends to every new check. Each check is individually guarded; a check that raises becomes a `FAIL` row naming the exception, never a traceback and never a crash. A registry where one bad check hides the other thirteen would be a worse safety net than today's.

The verdict function is total: it must produce a sentence for an empty row list, an all-`warn` list, and a list where every row failed.

The direct fallback voice is **best-effort and silent on its own failure**. It is the last resort; it has nothing to escalate to, and an exception there must not mask the diagnosis it was trying to deliver.

## 12. Testing

TDD throughout, per repo default. Pins the plan must carry:

- **isatty both ways** — tty ⇒ speaks; pipe ⇒ silent. The suite stays silent by default.
- **probe-only** — doctor does **not** relaunch (spawn-counting fake proves zero spawns), **and** the normal `client.send` relaunch path is unchanged.
- **verdict** — all-green, single failure, many failures, empty, all-warn; and the spoken-name mapping.
- **wedge** — heartbeat stale ⇒ speech-path row FAILs **while `PING` still succeeds**. This is the exact lie D4 exists to kill; if this test does not fail on the pre-D4 tree, it is not testing the right thing.
- **suppressor** — fires once, stays quiet on repeats, re-arms after an intervening success.
- **uninstall** — disclosure fires; purge deletes; keep preserves; a failed `launchctl unload` surfaces instead of printing success.
- **`ensure_daemon`** — backoff **measured**, not asserted by comment.
- **check isolation** — a raising check yields a FAIL row and the other rows still render.

**Live ear verification is the controller's, never the owner's.** Audio and install paths need unsandboxed Bash; the controller runs the real ear path and reports what it heard. The owner is never used as a test harness.

## 13. Provisional strings → ear-batch-4

Every new spoken string ships marked `PROVISIONAL` and is auditioned at D4's merge gate as **ear-batch-4**: the verdict headline (healthy and unhealthy forms), the spoken check names, the uninstall disclosure question, the install summary, the "try sonari doctor" cue, and #54's session-less failure word.

Kept separate from the pending **ear-batch-3** (D3's five strings and the "pending" collision) so that batch stays free to happen on the owner's word rather than waiting out a ~20-task campaign.

## 14. Mechanics, version, non-goals

- **Branch** `build/d4-safety-net` off `main @ e92bfd2`. Subagent-driven per the D-track pattern.
- **Version 0.10.0** across the five sites in §2, manifest test first. D4 ships before the multi-harness milestones, so **M0 was renumbered to 0.11.0 and M1 to 0.12.0** on `docs/multi-harness-recon` (commit `ecc4edf`).
- The registry is shaped so **M1 Task 7's per-adapter `doctor_checks()`** plugs in without rework — a standing HANDOFF constraint.
- `src/sonari/hooks_prime.py` and `scratchpad/` stay **untracked**; neither is swept into a D4 commit.
- **No pushes.** Publishing is the owner's `!`, as with every prior increment.

**Non-goals.** D5's boot-reorder and welcome-back debrief; D6's catch-up fork; the Kokoro demotion question; the tape-transport question. D4 makes the safety net honest — it does not renegotiate what the net is for.

## 15. Decision register (owner-settled 2026-08-11, grill to empty frontier)

| # | Decision | Where |
|---|---|---|
| 1 | Doctor speaks **daemon-first with a direct fallback** | §6 |
| 2 | **The spoken verdict is the end-to-end proof**, plus a heartbeat telemetry row | §6, §4.1 |
| 3 | Uninstall **discloses `state.json` by ear and offers to purge** | §8 |
| 4 | **One D4, the full MAP list** (owner override of the recommended split) | header |
| 5 | Verdict = **headline plus every failing check named** | §5 |
| 6 | "Try doctor" cue = **one-shot per class, re-armed by a success** | §7 |
| 7 | **Hook-responsiveness fix folded in** | §9 |
| 8 | **D4 = 0.10.0**; M0 → 0.11.0, M1 → 0.12.0 | §14 |
| 9 | Doctor is **side-effect-free** — never relaunches. **Not reopened; intent unchanged. §2.1 found it was already true — the premise behind the question (a self-healing probe) was false, so the work shrinks from a fix to an invariant pin.** | §2.1, §4.2 |
| 10 | **Speaks when interactive**, silent when piped; `--speak` / `--quiet` | §6 |
| 11 | D4 gets its **own ear-batch-4** | §13 |
| 12 | The 23 tagged `[Windows]` issues **closed** | done 2026-08-11 |
| 13 | **#54's surviving half folded into D4** | §10 |
| 14 | Four verified-stale issues (#14, #50, #22, #1) **closed with evidence** | done 2026-08-11 |
| 15 | **Uninstall must actually stop the daemon** and close the hook-resurrection path (post-go scope rider, approved) | §8.1 steps 3, 5 |
