The advisor's guidance is clear and aligns with my reading of the task. Key disciplines: concern-first organization (not map-by-map recap), report clustering never propose splits, dedup cross-subsystem friction, present the no-op `start()` as by-design, frame §8 forks with both sides. The deliverable is my final message, not a file. Writing now.

# Sonari Codebase — Evidence Digest for Architecture Redesign

*Synthesized from five subsystem maps + git churn analysis (332 commits, 2026-06-04..06-20, ~16 days; 136 touch `src/sonari/*.py`). Load-bearing structural claims independently re-verified against source. Where a map says "measured/verified," it is cited as such; inferences are not upgraded to fact.*

---

## 1. What Sonari is & end-to-end data flow

Sonari is a macOS Claude Code plugin that turns Claude's hook events into spoken audio for an eyes-free user. A single long-lived **daemon process** owns one voice and one speak loop; hooks fire short-lived client processes that ship messages to it over a localhost-TCP socket.

The end-to-end path of one assistant text block becoming speech:

1. **Claude Code fires a hook** → runs `${CLAUDE_PLUGIN_ROOT}/bin/sonari-hook <Event>` (wired by `hooks/hooks.json` for 7 events) with payload JSON on stdin.
2. **`bin/sonari-hook`** (95 lines) reads stdin, mutates `sys.path` to shadow any stale global `sonari` with the plugin's own `src/` (`bin/sonari-hook:41-43`), optionally dumps a capture file (`SONARI_CAPTURE`), then is a total try/except that **always exits 0** so a hook failure never surfaces to Claude.
3. **`hooks_entry.handle_event(event, payload)`** (`hooks_entry.py:29`, pure, no I/O) maps the event name + payload to a list of protocol dicts — e.g. an earcon + a `PROSE` message, or a decision earcon + `CHOICE`/`PLAN`/`PERMISSION`. SessionStart additionally probes the controlling tty via `ttyutil.controlling_tty` and reads `TERM_PROGRAM`/`ITERM_SESSION_ID` for focus-follow identity.
4. **`client.send`** (`client.py:15-40`) calls `ensure_daemon` (which calls `daemon.ensure_running` → `subprocess.Popen` of `bin/sonari-daemon` if no socket is connectable), then opens a Unix-style TCP socket to `LOCK_PATH`'s advertised host/port, does the token handshake, and writes `protocol.encode(msg)` (NDJSON, `PROTOCOL_VERSION=1`).
5. **Daemon socket layer** (`daemon._accept_loop` → `_spawn_conn_handler` → `_handle_conn`, lines 1025-1126): token handshake, newline-framed decode, BoundedSemaphore-capped (32) handler threads, 5s recv timeout. Each decoded message goes to the one chokepoint.
6. **`daemon.handle_message(msg)`** (lines 335-737, a **403-line flat 27-branch if-chain** over `MsgType`): the `PROSE` branch feeds the delta into the session's `ProseAssembler.feed` (`assembler.py`), which dedupes by index, splits sentences, replaces ``` fenced blocks with an "N-line LANG code block" summary, and emits `PARAGRAPH_BREAK` sentinels. `cleaner.clean_markdown` (`cleaner.py`) scrubs markdown noise inside the assembler. Output chunks go to `_buffer_prose`/`_flush_prose_buffer`, which apply the per-session **minqueue** batching threshold, then `_enqueue` builds a `SpeechItem` (`queue.py`) into that session's `SpeechQueue` inside its `SessionStream` (`session_stream.py`). `history.record`/`end_message` log each narrated sentence.
7. **The speak loop** (`_speak_loop`/`_speak_loop_once`, lines 907-1024) runs on its own thread, consuming **only the foreground session's** queue (`sessions.foreground()` decides whose voice plays), computing folder-attributed text, and calling `Speaker.speak` (`speaker.py`).
8. **`Speaker`** orchestrates one utterance at a time via an injected `say_runner` (= `_backend.tts.run`) under a cancel-epoch, blocking on the proc handle.
9. **`MacTtsBackend.run`** (`platform/macos/tts.py`) routes Kokoro neural voices through the in-process `KokoroEngine` → WAV → `afplay`, and everything else through the `say` command.

Modules in the hot path: `bin/sonari-hook` → `hooks_entry` → `client` → `protocol`/`transport` → `daemon.handle_message` → `assembler`+`cleaner` → `queue`/`session_stream`/`sessions`/`history` → `Speaker` → `platform/macos/tts` → `say`/`afplay`. Hotkeys take a parallel entry (a separate `hotkeyd` process feeds synthesized messages back into `handle_message`).

---

## 2. Responsibility inventory

| Responsibility | Owner(s) today | Split / Duplicated? |
|---|---|---|
| Wire protocol (vocabulary, version, NDJSON framing) | `protocol.py` (`MsgType`×24, `encode`/`decode`) | — |
| Client transport (connect, send, ensure daemon) | `client.py:15-40` | — |
| Hook event → protocol dict mapping | `hooks_entry.handle_event` (pure) | — |
| Hook side-effecting shell (stdin, capture, exit-0) | `bin/sonari-hook:14-86` | — |
| Markdown scrubbing | `cleaner.clean_markdown` (does NOT handle fences) | — |
| Streamed-delta assembly (dedup, sentence split, fence summary, paragraph break) | `assembler.ProseAssembler` | — |
| Per-session speech queue (bounded FIFO, decision-exempt eviction) | `queue.SpeechQueue` | — |
| Per-session state aggregation | `session_stream.SessionStream` (passive bag) | **SPLIT**: holds fields; *all* mutating logic lives in `daemon.py` |
| Voice ownership / foreground / pin / identity | `sessions.SessionManager` + `Identity` | — |
| **Minqueue batching** | buffer on `SessionStream.prose_buffer`; threshold logic in `daemon._buffer_prose/_flush_prose_buffer/_minqueue` (116-150) | **SPLIT** across `session_stream.py` + `daemon.py` |
| Central message dispatch | `daemon.handle_message` (335-737) | the 27-branch hub |
| Speak loop / playback bookkeeping | `daemon._speak_loop*`, `note_spoken`, `_attributed_text` | — |
| One-utterance orchestration + cancel-epoch | `speaker.Speaker` | — |
| Narration history + nav queries | `history.SessionHistory` (pure) | — |
| Within-turn + cross-turn navigation | `daemon._nav` (791-836) + `daemon._nav_response` (837-882) | **DUPLICATED tail**: both do `speaker.cancel()` → `queue.clear()` → re-enqueue loop, near-verbatim (830-835 vs 876-881) |
| Decision text construction | `daemon._choice_text/_plan_text/_permission_text/_selection_cue/_choice_notes` (218-293, pure/static) | — |
| Decision handling (CHOICE/PLAN/PERMISSION) | `handle_message` branches 373-414 | **DUPLICATED**: three near-identical blocks differing only in text-builder + kind string |
| Config validation/clamping | SET_RATE (662-684), SET_MINQUEUE (698-707) | **DUPLICATED + INCONSISTENT**: rate/minqueue re-implement `max(MIN,min(MAX,…))`; SET_VOICE/SET_VERBOSITY persist raw payload with no validation |
| TTS synthesis (say + Kokoro/afplay) | `platform/macos/tts.py` | — |
| Neural engine (catalog, gating, download, synth) | `kokoro.py` (in-process) | — |
| Neural venv lifecycle (uv, venv, pip, health) | `kokoro_provision.py` (out-of-process) | **SPLIT** across a process boundary; meet only via subprocess string literals + `tts.py` importing both. "Is neural available?" defined two ways (`require_installed` vs `is_installed`/`neural_enabled`) |
| Earcon playback | `platform/macos/earcon.py` (+ `Speaker.earcon` reaping) | — |
| Hotkey lifecycle (compile/load/reload Swift hotkeyd) | `platform/macos/hotkeys.py` + `daemon._start/_stop/_reload/_dispatch_hotkey` | — |
| Hotkey action → message table | `keymap.ACTION_MESSAGES` | — |
| Keymap resolution to Carbon codes | `keymap.py` + `platform/macos/keytables.py` (data) | — |
| Daemon supervision (python resolve, plist, launchctl, launcher) | `platform/macos/supervisor.py` | — |
| Focus-follow (raise the right terminal window) | `raise_service.RaiseService` + `platform/macos/raiser.py` | — |
| Install/uninstall lifecycle | `cli.install`/`cli.uninstall` (361-482) | — |
| Health diagnostics (doctor) | `cli.doctor` (190-264) + `supervisor.doctor_rows` + `raiser.doctor_rows` + `hotkeys.doctor_rows` | **SPLIT**: threaded through whole macos package; `supervisor.doctor_rows` reaches into `tts.best_voice` and `hotkeys`' label |
| Path constants | `paths.py` (within-subsystem hub) | — |
| Persisted config (defaults, merge-load, atomic save) | `config.py` | — |
| **Atomic JSON write (temp→fsync→os.replace)** | `config.save_config:63`, `keymap.py:169` (+:212), and `write_default_keymap_if_absent` | **DUPLICATED** ~3–4× (verified `os.replace` at config:63, keymap:169, keymap:212; pattern reimplemented per site) |
| **Corruption-tolerant load (merge over defaults)** | `config.load_config` (39-52) + `keymap.load_keymap` (124-148) + `keymap._read_user_keymap` | **DUPLICATED** 2–3× |
| Single-instance guard + process bootstrap | `daemon.main` (1198-1231) | — |
| Lazy daemon start | `daemon.ensure_running` (1165) | — |
| Native-crash diagnostics | `daemon._arm_faulthandler` (1176) | — |
| Self-contained `src/` resolution | bash `PYTHONPATH` (`bin/sonari`, `bin/sonari-daemon`); runtime `sys.path` insert (`bin/sonari-hook:41-43`); LaunchAgent env `PYTHONPATH` (`supervisor.py:165`) | **DUPLICATED** 3 different ways |

---

## 3. The daemon.py problem (1236 lines)

`daemon.py` is the codebase's center of gravity by three independent measures that converge (see §5): largest file (1236 lines), highest churn (47/136 src commits = 35%), and the import/execution hub where every pipeline file's logic actually runs. Distinct concerns living inside it, with separability assessed factually:

**Concerns inside daemon.py:**

1. **TCP socket server lifecycle** — bind/listen(16)/lockfile/accept/teardown (`run`, `_accept_loop`, `stop`, 1118-1163). *Cohesive cluster, touches daemon state only via the single `handle_message` call.*
2. **Per-connection protocol handling** — token handshake, newline framing, recv timeout (`_handle_conn`, 1025-1069). *Same cohesive cluster.*
3. **Connection concurrency control** — BoundedSemaphore(32), permit-leak recovery (`_spawn_conn_handler`, `_handle_conn_guarded`, 1083-1116). *Same cluster.*
4. **Central message dispatch** — the 403-line 27-branch if-chain (335-737). *This is the hub; every other method is reached through it. Not separable as a unit, but the branch families below are.*
5. **Prose assembly + minqueue batching** — (116-151, 340-366). *Couples to `SessionStream` + assembler; the batching logic is split with `session_stream.py`.*
6. **Decision text construction** — five pure/static builders (218-293). *Cleanest separable cluster: raw dict in, string out, touches no daemon state except `_selection_cue`'s warned flag.*
7. **The speak loop** — foreground-only playback, pop/claim, attribution, pause re-queue (188-217, 907-1024). *The tightest state-sharing knot (`_lock`, `_current_item`, `_last_spoken_session`, `_pending_heard`); lives on its own thread; consumer side of the queue. Separable from dispatch but bound to shared concurrency state.*
8. **Per-session stream registry** — lazy create/cache/evict (80-115, 169-171).
9. **Within-turn navigation** — `_nav` (791-836). *Separable; duplicates a tail with #10.*
10. **Cross-turn navigation** — `_nav_response` (837-882). *Separable; shares nav state + the duplicated seek-and-play tail with #9.*
11. **Play/pause + resume** — (515-541, 883-888).
12. **Per-session mute + pin toggles** — (543-579).
13. **Jump-to-waiting** — backlog target + RaiseService raise (61-79, 173-186, 606-645).
14. **Global hotkey lifecycle** — start/stop/reload + kill-switch (749-789, 890-905). *Cohesive cluster; connects to the rest only by feeding a synthesized message into `handle_message`.*
15. **Live config mutation + persistence** — SET_RATE/VOICE/VERBOSITY/MINQUEUE/CYCLE (662-721). *The 12 config/control branches cluster around `self.config` + `save_config` + `self.speaker`, largely independent of the prose/decision pipeline.*
16. **Setup-health guidance** — once-per-session degraded-install nudge (152-167, 295-334). *Self-contained; reads install.json; shares no state with playback.*
17. **Status/ping reporting** — (723-734).
18. **Single-instance guard + process bootstrap** — `main` (1198-1231). *Module-level; distinct lifecycle from running behavior.*
19. **Lazy daemon start** — `ensure_running` (1165-1170). *Module-level.*
20. **Native-crash diagnostics** — `_arm_faulthandler` (1176-1195). *Module-level; pure diagnostics scaffolding.*

**Most observably separable** (low shared state with the playback pipeline): the socket/connection layer (#1-3), the pure decision text builders (#6), config/control branches (#15), hotkey lifecycle (#14), setup-health (#16), and the three module-level bootstrap functions (#18-20). **Hardest to separate** (deep shared concurrency state under `_lock`): the speak loop (#7), enqueue helpers, and dispatch's prose path — the maps call this "the tightest state-sharing knot in the file." `_speak_loop_once` alone (939-1024, 86 lines) interleaves four concerns (foreground resolution, mute filtering, attribution with rollback, pause re-queue) across two lock regions.

---

## 4. The platform abstraction question

The `platform/base.py` ABC layer was shaped for a multi-platform world the code no longer contains. **4 of the 5 production ABCs are single-impl** (verified: `MacTtsBackend`, `MacEarconBackend`, `MacHotkeyBackend`, `MacSupervisorBackend` each the sole impl of their ABC). **Only `RaiseBackend` is genuinely polymorphic** — `MacRaiseBackend` + the inert `NoopRaiseBackend` (`base.py:120`). `find src/sonari -iname '*win*'` returns nothing; there is no `windows/` package.

The Windows rationale survives in `base.py` prose with nothing behind it (verified quotes):

- `base.py:2` — *"concrete macOS/**Windows** implementations live in sibling packages"* (no windows sibling exists).
- `base.py:76` — *"# --- in-process lifecycle (**Windows** runs a thread; macOS runs a process) ---"*
- `base.py:89-92` — `reload()` default is *"correct for an in-process listener like **Windows**, whose stop() releases its chords before start() re-registers them). Platforms whose hotkeys run in a SEPARATE process (macOS) override this…"*
- `base.py:121` — `NoopRaiseBackend` docstring: *"Inert backend for platforms without focus-follow (**Windows**/Linux/tests)."*

**Single-impl ceremony, concretely:**

- `HotkeyBackend.start`/`stop` (`base.py:77-84`) are no-op defaults, and `MacHotkeyBackend` **never overrides them** (verified: no `def start` in `hotkeys.py`). So `daemon.py:762` `get_platform().hotkey.start(self._dispatch_hotkey)` is wired to a no-op. **This is by design, not a bug**: per `base.py:76-92`, macOS delivers hotkeys via a *separate* `hotkeyd` process that round-trips over the socket, so the in-process `start(dispatch)` callback is dead-on-macOS and the `_dispatch_hotkey` callback is never invoked through this path on macOS.
- `key_codes`/`mod_masks`/`default_mods`/`extra_default_bindings` have concrete `return {}`/`[]` defaults (`base.py:57-74`) that exist only for absent non-mac platforms.
- `HotkeyBackend.install` declares `(self, log_path, agent_path, launchctl_fn)` (`base.py:40`), but the contract-test double defines `install(self)` and the cli fake uses `install(self, **kwargs)` — three incompatible shapes coexist because Python ABCs don't check signatures. Two of the three params are near-ceremony: cli passes `agent_path=None` (backend replaces it with its own constant) and a `launchctl_fn` that `hotkeys.reload/uninstall` ignore anyway (they call `MacSupervisorBackend().launchctl` directly).
- Trivial single-impl bodies behind abstract methods: `default_earcons` → `dict(_DEFAULTS)`; `is_installed` → `os.path.exists(_launcher_path())`; `is_running` → `_p.socket_connectable()`; `launch_spec` → a 4-line tuple return.

**The change-history fact framing this:** an entire `platform/windows/*` subtree (tts/keytables/hotkeys/supervisor/supervisor_loop/earcons) was **ADDED 2026-06-18** ("Land the Windows port", #37) and **fully DELETED 2026-06-20** — built and removed inside a 2-day window. During that span `base.py` (7 commits), `transport.py` (5), `__init__.py` (4) churned. The seam was "kept intact" after the deletion. `transport.py`, filed under `platform/`, is OS-agnostic stdlib and is *not* a `PlatformBackend` — `base.py`'s own docstring flags that it "branches separately."

Factual characterization: 4/5 ABCs are now single-impl ceremony with dead cross-platform rationale; 1/5 (`RaiseBackend`) earns its abstraction. (Whether to keep or collapse the seam is a §8 fork.)

---

## 5. Coupling & seams (maps × git co-change)

Raw co-change overstates `daemon.py` because it is in 35% of commits and pairs with almost everything. The churn analysis computes a **directional ratio** (pair-count ÷ smaller file's total commits) to separate real binding from coincidental co-activity. Three clusters emerge, two of which route *around* daemon — and they are confirmed by **both** import edges and change history:

**Cluster A — the daemon runtime core (the hub).** Triangulated by three converging sources: largest file (1236 LOC), highest churn (47 commits), import + execution hub. Tightly bound by directional ratio AND imports:
- `queue.py` 9/10 = **0.90** (also imported: `SpeechItem`)
- `config.py` 6/7 = **0.86** (imported: `save_config`/`load_config`)
- `protocol.py` 8/10 = **0.80** (imported: `MsgType`/`encode`/`decode`)
- `session_stream.py` 4/6 = **0.67** (imported: `SessionStream`)
- plus `paths` (5), `speaker` (4), `hooks_entry` (4), `history` co-move with daemon.

These small files rarely change *without* daemon — because their logic *executes inside* daemon. `SessionStream` is the extreme case: a passive state bag whose every mutation lives in `daemon.py`.

**Cluster B — hotkey/keymap, daemon-INDEPENDENT.** Confirmed by both imports and change-history; `daemon.py` does not appear in keymap's top co-change pairs:
- `keymap.py` + `platform/macos/keytables.py` 5/5 = **1.00** (keytables NEVER changed without keymap; `keytables` is pure data re-exported through `MacHotkeyBackend` to `keymap`)
- `keymap.py` + `platform/macos/hotkeys.py` 4/6
- Import edge: `keymap._keytables`/`default_keymap` lazily import `platform.get_platform`; `hotkeys.reload` lazily imports `keymap.write_resolved`. This is a self-contained subsystem.

**Cluster C — install/lifecycle, anchored on cli.** Confirmed by both:
- `cli.py` + `platform/macos/supervisor.py` 5/5 = **1.00** (supervisor only ever changed alongside cli — consistent with the maps' note that supervisor methods are "verbatim moves" lifted from cli)
- `cli.py` + `platform/base.py` 4/7; `cli.py` + `paths.py` 4
- Import edge: `cli.install`/`doctor` drive the supervisor/hotkey/raise backends.

**NOT bound, just both busy:** `cli.py` + `daemon.py` is rank-2 by raw count (8) but only **8/40 = 0.20** of cli's commits — cli and daemon are individually the two busiest files and overlap by chance, not dependency. They share `protocol` + `paths` but not each other's internals.

**A fourth observed cluster (cross-subsystem) — the path/persistence hub.** `paths.py` (14 commits, 3rd hottest) is the within-package dependency hub: imported by `config`, `keymap`, `kokoro_provision`, `cli`, `client`, `daemon`, and all macos modules. Its co-change spreads thin across many partners (`daemon`+`paths` 5, `cli`+`paths` 4, `paths`+`protocol` 4) — consistent with a shared-constants module touched whenever a new `~/.sonari` artifact is added.

**Mutual coupling inside macos:** `hotkeys.reload/uninstall` lazy-import `MacSupervisorBackend` (for `.launchctl`), while `supervisor.doctor_rows` imports the hotkeyd `LAUNCH_AGENT_LABEL` from `hotkeys.py` — a circular macos-package dependency.

**Where the code observably clusters:** one dominant daemon-runtime hub (A) that the protocol/queue/config/session/paths/speaker/history files co-move with; a separate, daemon-independent hotkey cluster (B); an install/lifecycle cluster on cli (C); and the platform abstraction layer that was the site of the largest add-then-remove churn event in the repo's history (§4).

---

## 6. Friction inventory (deduped, cross-subsystem, with file:line)

*Cross-subsystem frictions consolidated to one entry each rather than repeated per map.*

**Verified bugs / dead paths:**

- **Latent NameError, silent diagnostic loss** — `daemon._signal_speak_failure` (922-937) calls `traceback.print_exc(file=sys.stderr)` at line 935, but neither `traceback` nor `sys` is imported in scope (module top imports only os/secrets/socket/subprocess/threading; they're imported locally only inside *other* functions at 917-918, 1078-1079, 1089-1090). The call sits in `try/except Exception: pass` (934-936), so the NameError is swallowed: the error earcon still fires, but the traceback the docstring promises is **never written**. Every *inner* speak-loop failure loses its diagnostic silently. (Verified by reading the function + module-top imports.)
- **No-op hotkey `start()` on macOS** — `daemon.py:762` calls `hotkey.start(self._dispatch_hotkey)`, but `MacHotkeyBackend` never overrides the `base.py:77-80` no-op default (verified). Dead-on-macOS *by design* (hotkeyd is a separate process; §4), not a defect — but a reader cannot tell from the call site.

**Large multi-concern functions:**

- `daemon.handle_message` — 403-line 27-branch flat if-chain, no dispatch table; adding a `MsgType` means editing the ladder (335-737).
- `daemon._speak_loop_once` — 86 lines, four interleaved concerns across two lock regions (939-1024).
- `cli.install` — ~66 lines, 8 numbered concerns in one function (361-427).
- `cli.doctor` — ~74 lines reaching into paths/client/keymap/kokoro_provision/platform (190-264).
- `supervisor.doctor_rows` — ~82 lines, flat ad-hoc probe list that instantiates `MacTtsBackend` and imports the hotkeyd label (268-349).
- `kokoro.py` — one 191-line module mixing four unrelated concerns (voice identity/rate math, extra gating, file download/IO, synth engine).

**Duplication (cross-cutting):**

- **Atomic-JSON-write** (temp→fsync→`os.replace`) reimplemented at 3–4 sites: `config.save_config:63`, `keymap.py:169`, `keymap.py:212` (+ `write_default_keymap_if_absent`). (Verified `os.replace` at the three lines; pattern repeated per call site.)
- **Corruption-tolerant load** (merge user file over defaults, return defaults on error) duplicated 2–3×: `config.load_config:39-52`, `keymap.load_keymap:124-148`, `keymap._read_user_keymap:151-158`.
- **`build()` Swift-compile** logic near-identical at `hotkeys.py:142-170` and `raiser.py:63-89` (sha256-gated swiftc); `build()` is not on any ABC (verified both `def build`).
- **`_xml_escape`** defined identically at `supervisor.py:37` and `hotkeys.py:57`; plist XML generation duplicated (`supervisor.plist:98-156` vs `hotkeys._hotkeyd_plist:62-91`). (Verified.)
- **Decision handlers** CHOICE/PLAN/PERMISSION — three copy-pasted blocks (`daemon.py:373-414`).
- **Nav seek-and-play tail** — `daemon._nav` (830-835) vs `_nav_response` (876-881) near-verbatim.
- **Config validation** — SET_RATE/SET_MINQUEUE re-implement clamping; SET_VOICE/SET_VERBOSITY skip it entirely (`daemon.py:662-707`).
- **`src/` resolution** solved 3 ways (bash PYTHONPATH / runtime sys.path / LaunchAgent env).
- **Interpreter-selection** "prefer /usr/bin/python3 else PATH" duplicated across `bin/sonari:9-14` and `bin/sonari-daemon:8-13`.
- **"Normal rate = 200"** as three independent literals: `history.py:28` cap 200, `speaker.py:13` rate 200, `kokoro.py:72` `/200.0`; history cap 200 also duplicates `config.DEFAULTS` history_cap 200.

**Windows residue (appears in EVERY subsystem — one consolidated entry):** dead cross-platform comments/docstrings with no implementation behind them, despite the 2026-06-20 macOS-only removal:
- daemon: `427` (Windows earcon), `585-586`/`750-751`/`778` (Windows hotkey threads), `1178` (faulthandler docstring centers on WinRT/winsound).
- cli: `195`, `197`, `364-365`, `405` (schtasks/Task Scheduler/pythonw/UIPI/M3).
- platform/base: `2`, `76`, `89-92`, `121` (verified, §4).
- platform/tts: `8-9`, `11-15` ("deliberately duplicate the Windows backend's", `#42`/`#41` parity).
- kokoro: `1` ("cross-platform"), `6` (winsound), `100` (WinRT `_require_winrt` #7).
- keymap: `21`, `45`, `67-68` (Windows chords / response-nav bindings).
- sessions: `_basename` (14-22) strips Windows backslashes "regardless of host OS."
- tests: `test_macos_tts_kokoro.py:3`, `test_daemon_loop.py:209-211`, `_fakeplatform.py:6` reference deleted `test_win_*` files; no `test_win_*.py` remain.

**Process-boundary / string-literal coupling:**

- `kokoro_provision` invokes `kokoro` logic through Python source in **string literals** run in a subprocess (`_PREDOWNLOAD:82-84`, `_HEALTH:86`) — renaming `kokoro.is_installed` or `KokoroEngine` silently breaks them, invisible to refactoring tools.

**Cross-seam reach / suppressions:**

- `cli._daemon_python` calls a leading-underscore method `sup._probe_python_version(...)` across the platform seam (`cli.py:291,375`).
- `voice` command splits transports: listing runs in-process (`tts.list_voices`), setting goes over IPC (`SET_VOICE`) — `cli.py:92-104`.
- Pervasive broad `except Exception # noqa: BLE001` across `cli.py` (~14 sites) and bare excepts in `bin/sonari-hook` (7 sites) — failures swallowed broadly. `cli.main` only special-cases `DaemonNotRunning`, re-raises every other OSError, lets non-OSError propagate (`559-564`).

**Test-suite friction (measured by the analysis):**

- **VERIFIED**: on a base env, `pytest` collection **aborts the whole run** because `tests/test_kokoro.py:4` does an unguarded top-level `import numpy` with no skip guard. Measured: excluding the 3 kokoro files, 650 passed in ~4.8s (kokoro tests not run). A single optional-extra import gates the entire suite.
- Tests assert heavily on daemon private internals (`_stream`, `_streams`, `_enqueue`, `_speak_loop_once`, `_pending_heard`, `_current_item`, `_paused`, `_wake`, `_backlog_cap`) — tight coupling to private structure; the daemon's internal refactor surface is large.
- Three separate `FakeSpeaker` definitions (`daemon_helpers.py:7`, `test_e2e_pipeline.py:21`, the real-Speaker rig in `test_speaker_cancel_2b.py`).
- Build-phase file names (`test_daemon_phase2.py`, `_phase21.py`) obscure behavior; local `_msg`/`_prose`/`_drain` helpers re-implemented across many daemon test files instead of in `daemon_helpers`.
- No pytest config at all (no `[tool.pytest.ini_options]`, testpaths, markers).

**Style:**

- Type hints written as forward-ref strings (`"str | None"`, `"dict[str, SessionStream]"`) throughout — Python 3.9 target workaround; `test_py39_compat.py` enforces the future-annotations first-line.

---

## 7. Change patterns (volatile vs stable)

**Volatile (high churn, still hot at 2026-06-20):**
- `daemon.py` — 47/136 commits (35%), 1236 lines; last touched 06-20. The overwhelming hub. Embedded fix tags mark patched hot spots: M2 (line 976 pop→speak gap), M6 (650 heard-marker), M8 (1109 permit leak), H2 (591 dark-hotkey race), L2/L (1008, 985 lock re-check). Comments repeatedly assert "NOTHING may permanently kill the speak thread" (912) — a stability concern driving defensive try/except wrappers that will gate future loop changes.
- `cli.py` — 40 commits, 568 lines; last touched 06-20. The install/lifecycle surface.
- `paths.py` (14), `keymap.py` (11), `queue.py` (10), `protocol.py` (10) — next tier. `queue` and `protocol` are volatile *because* they co-move with daemon (the control surface grows: `MsgType` is at 24 entries; each new hotkey action — JUMP_WAITING, PIN_TOGGLE, RELOAD_KEYMAP, CYCLE_VERBOSITY — extends the dispatch ladder).
- `platform/base.py` (7) + `transport.py` (5) + `__init__.py` (4) — churned heavily during the 2-day Windows add/remove. The platform abstraction was the single most actively reshaped-then-collapsed area.
- The **navigation feature** grew in staged phases (`history.py` Stage 4 cross-turn, Stage 5 two-level; `keymap` added nav_prev/next_response as new `to` values on the existing NAV message). Active growth area.
- **"The flip"** (daemon.py:346, 384, 398, 411; assembler; session_stream) — a recently-completed structural change: gating moved from production-time to playback-time, each session now buffers into its OWN stream. The dominant recent shift; the per-session-streams redesign.

**Stable (low churn, settled):**
- Leaf utilities: `ttyutil.py` (2), `cleaner.py` (2), `kokoro.py` (2), `assembler.py` (3), `client.py` (3), `raise_service.py` (3). The core text transform (cleaner/assembler) is comparatively settled even as the control surface churns.
- Portable-core unit tests (test_queue/_assembler/_cleaner/_protocol/_history/_sessions/_config/_session_stream/_ttyutil/_transport) carry no stage markers — they pin settled data structures.

**Config knobs (the tunable surface, all in `config.DEFAULTS`):** `voice`, `rate` (100-400), `verbosity` (everything|medium|quiet), `background_policy` (earcon_only), `history_cap` (200), `backlog_cap` (200), `minqueue` (1-10), `focus_follow`. `minqueue` and `verbosity` directly gate the prose pipeline's flush/speak decisions. New settings land here.

**Kill-switches signaling still-stabilizing subsystems:** `SONARI_DISABLE_HOTKEYS` env + `~/.sonari/no_hotkeys` file (daemon.py:757-758, 781-782, explicitly "crash-diagnosis isolation lever"); `_MAX_CONN_THREADS=32` (defensive hardening bound); `_arm_faulthandler` (added to catch native C-level deaths). These cluster on the hotkey + connection + native-crash surfaces.

**Phase/version markers:** Stage 2-6 / Task 2-5 / M2/M6/M7/M8 / Phase 1-2 / spec §-refs throughout; version hard-pinned to **0.5.0** across plugin.json/marketplace.json/pyproject (`test_manifests` enforces). `PROTOCOL_VERSION=1` stamped on every message — wire format treated as a stable contract expected to evolve.

**Load-bearing version constraints likely to shift:** `kokoro_provision` pins CPython 3.12 for the neural venv (system python defaults to 3.9, kokoro-onnx needs ≥3.10); `_PYTHON_CANDIDATE_NAMES` enumerates 3.9-3.13; Kokoro model URLs/size-floors hardcoded to upstream tag `model-files-v1.0`; Carbon `KEY_CODES`/`MOD_MASKS` hand-maintained.

---

## 8. Open questions for the architect

*Genuine forks where the evidence is real on both sides and does not by itself decide. Framed as questions, with the evidence each way.*

1. **The platform seam: keep the 5-ABC abstraction, or collapse to concrete macOS?**
   *Keep:* the seam was deliberately "kept intact" after Windows removal; Windows was built and deleted in a 2-day window (06-18→06-20), so the capability to re-add a backend has demonstrated value; `RaiseBackend` is genuinely polymorphic (Mac+Noop); `test_no_os_branch_in_core` enforces the boundary as an invariant. *Collapse:* 4/5 production ABCs are single-impl, with dead Windows rationale in `base.py:2,76,89-92,121`, no-op defaults that no impl overrides, a 3-shape `install()` signature ABCs can't enforce, and `build()` already living *outside* the ABC as an informal cli convention. The evidence quantifies the ceremony but does not weigh future-portability against present-simplicity.

2. **daemon.py decomposition: along which axis?** The maps surface multiple non-aligned seams — by *message family* (config/control vs prose vs decision vs nav), by *thread* (socket-accept thread vs speak thread vs reload thread), by *lifecycle* (bootstrap functions vs running behavior), and by *state-sharing* (the `_lock`-bound speak-loop+enqueue knot vs the lock-independent text builders). These axes cut across each other. Which axis the redesign privileges determines whether the 403-line dispatch, the 86-line speak loop, or the concurrency invariants become the seam — the evidence shows all four are real but does not rank them.

3. **`SessionStream` (passive bag) vs `daemon` (all logic): where should per-session behavior live?** Today state and the logic that mutates it are physically separated; minqueue batching straddles both files; `reset_for_new_prompt` resets some state while FLUSH clears the queue separately. Co-locating behavior with state vs keeping daemon as the single orchestrator are both defensible — the evidence shows the split is real and fragile but not which direction resolves it.

4. **The prose-transform contract (assembler ↔ cleaner ↔ daemon): is the implicit 3-way contract acceptable, or does it need an explicit boundary?** `assembler._split_sentences` re-runs `clean_markdown` on the growing raw buffer and slices `cleaned[_emitted:]`, depending on `clean_markdown` being deterministic and length-stable in a way the cleaner has no idea it must guarantee (a documented past bug at assembler.py:167-173). The `final` flag is overloaded (end-of-block, not end-of-turn) requiring two separate message handlers to cooperate. The evidence shows the fragility but not whether to formalize the contract or restructure the stages.

5. **Neural (kokoro) coupling: in-process engine + out-of-process venv, joined by subprocess string literals — keep the process boundary or unify?** The CLI runs on system python (3.9), the daemon runs on the neural venv (3.12+) — a real reason the two halves are split and "is neural available?" is defined two ways. But the join via Python source in string literals is refactor-invisible. The version-constraint reason is load-bearing; whether it justifies the string-literal coupling is not settled by evidence.

6. **The four atomic-write / corruption-tolerant-load duplications: extract a shared persistence helper, or accept the copies?** The pattern is reimplemented across `config.py` and `keymap.py` (3-4 atomic-write sites, 2-3 load sites). The files are in different clusters (config co-moves with daemon at 0.86; keymap is in the daemon-independent hotkey cluster) — so a shared helper would create a new cross-cluster dependency where none exists today. The evidence shows both the duplication and the cluster separation; it does not decide whether consolidation is worth the new coupling.

7. **Test-suite coupling to daemon internals: does the redesign budget for re-pinning?** ~21 daemon test files (~3049 lines, ~250 functions) assert on private daemon internals through `make_daemon` + `handle_message`. Any daemon decomposition invalidates a large fraction of these. The evidence quantifies the blast radius but does not decide whether to refactor tests first (to behavior-level contracts) or accept churning them with the code.

8. **The dispatch ladder vs a dispatch table: the control surface is the frequently-extended part.** `MsgType` is at 24-27 entries and grows with each control feature; each addition edits a 403-line if-chain. A table/registry is one obvious shape, but the branches are heterogeneous (one-liners to 40-line blocks, each mutating shared state and reaching into different helpers under `_lock`), so uniform dispatch may not fit cleanly. The evidence shows the growth pressure and the heterogeneity; it does not decide the form.

---

*Key files by absolute path for navigation: `/Users/Nima.Hakimi/Projects/private/claude-tts/src/sonari/daemon.py` (1236), `…/cli.py` (568), `…/platform/base.py` (164), `…/platform/macos/supervisor.py` (349), `…/assembler.py` (215), `…/keymap.py` (213), `…/kokoro.py` (191), `…/history.py` (162), `…/hooks_entry.py` (122), `…/queue.py` (88), `…/protocol.py` (46), `…/session_stream.py` (37), `…/cleaner.py` (37), and the bin shims under `…/bin/`.*