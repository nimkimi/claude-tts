# TEST-MAP — Sonari suite, post test-integrity audit

Scope: the **surviving suite** — `bd9a5c2` (the Receipts merge HEAD) with the three audit
branches applied: `audit/p3-fixture-honesty` (fixture honesty), `audit/p4-cleanups` (cull +
consolidate), `audit/p4b-pins` (survivor pins). These three branches are still separate at the
time this map was written; the controller lands them together. Verified here by an actual
three-way `git merge-tree` (clean, no conflicts) followed by a real pytest run on the merged
tree, both from `/Users/Nima.Hakimi/Projects/private/sonari` (the repo all the `sonari-*`
worktrees share):

- **207** `.py` files under `tests/` (199 collecting test functions, ~8 conftest/support/fixture
  shims), **1816 test functions** (`pytest --collect-only`).
- Full suite: **1815 passed, 1 failed, 1 skipped**. The one failure
  (`test_concurrency_guards.py::test_stress_no_lost_duplicated_or_resurrected_item`) is the same
  pre-existing timing-sensitive flake both P4-REPORT.md and P4B-REPORT.md independently
  documented — reproduced flaky-in-isolation here too (fail / pass / pass across three solo
  reruns), unrelated to any audit change. Not a regression from combining the three branches.
- `git diff --stat bd9a5c2 <merged-tree> -- src/` is **empty** — confirms the entire audit wave
  (P3+P4+P4b) is test-only, exactly as each phase's report claims independently.
- Sacrificial HOME used throughout (`$TMPDIR`-rooted, never `/var/folders` — the seatbelt blocks
  that path for `mktemp`).

If you're reading this after the controller's merge landed, the file counts above should match
`git ls-tree` / `pytest --collect-only` on `main` (or `audit/test-integrity`) directly — if they
don't, something moved after this map was written; regenerate before trusting stale rows.

## Tier legend

| Tag | Meaning |
|---|---|
| **D** | Drive-covered — integration test through `make_daemon()`/`make_net()` (`tests/daemon_helpers.py`), dispatching real `{"type": ...}` messages and asserting on the audible/observable surface. Highest confidence; this is the tier that caught the receipts-era composition bugs. |
| **U** | Unit — imports and exercises one class/function directly, no daemon dispatch. Right-sized for algorithmic cores (queue, speaker, history, sessions, assembler, cleaner, summarizer). |
| **S** | Structural guard — AST/source-text scan asserting an invariant about the *code*, not a runtime behavior (chokepoint guards, exhaustive enum pins, Protocol conformance, `gen_docs.py --check`). |
| **B** | Black-box — subprocess/CLI/socket boundary; no in-process `sonari` import. Includes the hook-JSON and Swift-compile checks. |

Most files are single-tier; a few subsystems mix tiers on purpose (noted inline).

## 1. Control-cue & mute integrity

The coupling-law surface: every ledger op must map to exactly one distinct sound, and a muted
session must never leak audio outside its own confirmations.

| File(s) | Tests | Tier | Src module(s) |
|---|---|---|---|
| `test_cue_api.py`, `test_control_cue_contract.py`, `test_control_cue_flag.py`, `test_carried_control_cue.py`, `test_held_control_cue.py`, `test_boot_cue.py`, `test_earcon_defaults.py`, `test_sp2_cue_routing.py`, `test_sp2_t6_control_grammar.py`, `test_prelude_unit.py`, `test_stopped_streams_hold_no_armed_resume.py` | ~65 | D | `cues.py`, `daemon/features/control.py`, `daemon/features/lifecycle.py`, `daemon/host.py` (control_cue enqueue chokepoint), `config.py` |
| `test_cues.py` | 6 | U | `cues.py` (registry structural pins — every entry well-formed, keys match names) |
| `test_cue_contract.py` | 8 | **S** | Source-scans that `.transient(` is called only by `Speaker`/host cue code, not scattered call sites |
| `test_faultcue.py` | 5 | D | `daemon/faultcue.py` |
| `test_muted_confirmations.py`, `test_muted_empty_answers.py`, `test_muted_press_receipts.py`, `test_muted_read_gestures.py` | 51 | D | daemon/features/* — every declared gesture answers on a muted session (receipts-era, all-new) |
| `test_fake_speaker_receipt.py` | 10 | D/**S** | This file **is** the fixture-honesty fix's own receipt — pins the resolved-vs-silent `FakeSpeaker` split and the autouse `_no_silent_cues` drain that fails any test firing a silent cue |
| `test_blackbox_net.py`, `test_e2e_pipeline.py` | 17 | **B** | Full-pipeline blackbox over the real socket/process boundary; each defined a local unregistered `FakeSpeaker` that reproduced the E-3 bug — fixed by P3 (`audit/p3-fixture-honesty`, commit `a47b8a3`) to route through the same resolved/silent split |

## 2. Nav / where-am-i / decisions

| File(s) | Tests | Tier | Src module(s) |
|---|---|---|---|
| `test_daemon_nav.py`, `test_daemon_focus_nav.py`, `test_jump_decision_miss.py`, `test_daemon_spearcon.py`, `test_pointer_collapse.py`, `test_permission_expiry.py`, `test_sp2_divergence.py`, `test_sp2_jump_waiting_cue.py`, `test_sp3_cycle.py`, `test_sp3_hold_entry.py`, `test_sp3_lifts.py`, `test_sp3_voicestate.py`, `test_sp3_sound.py` (nav half), `test_decision_callsign.py`, `test_decisions_answer.py`, `test_daemon_decisions.py` | ~135 | D | `daemon/features/navigation.py`, `daemon/features/decisions.py`, `daemon/features/focus.py` |
| `test_whereami_v2.py` | 16 | D | Grammar-oracle home for the "where am I" clause ordering — received the P4-cull's two moved-in tests from the deleted `test_sp3fix_grammar.py` |
| `test_daemon_where_am_i.py`, `test_also_map_unheard.py` | 13 | D | same |
| `test_answerability.py` | 6 | **S** | The chokepoint AST guard — "the only `is_decision` enqueue lives in the announce chokepoint" — **widened by P4b** from `decisions.py`-only to every `daemon/features/*.py` module (this is the guard that closed navigation.py real-gaps #48/#58/#105) |
| `test_session_numbers_mru.py` | 11 | D | `sessions.py` (spoken session-number pool) |

## 3. Chooser / session-switching / frontier

| File(s) | Tests | Tier | Src module(s) |
|---|---|---|---|
| `test_chooser.py` | 38 | D | `daemon/features/chooser.py` |
| `test_frontier.py` | 25 | D | frontier/candidate filtering; local `_FakeSpeaker.transient()` is a bare no-op (least-protected fake in the suite) but no test currently relies on its effect — latent risk, not a live gap |
| `test_sp3_ring.py` (renamed from `test_sp3fix_ring.py` by P4, content-neutral) | 7 | D | dead-tty-phantom filtering in the chooser |
| `test_dead_stream_voice.py`, `test_identity_eviction.py`, `test_where_roster.py`, `test_daemon_focus_follow.py`, `test_sp2_keepgoing.py`, `test_keepgoing_preroll.py` | ~65 | D | `daemon/features/chooser.py`, `session_stream.py`, `sessions.py` |
| `test_session_stream.py` | 5 | U | `session_stream.py` |
| `test_ttyutil.py` | 6 | U | `ttyutil.py` (tty-liveness primitive the chooser's phantom filter uses) |

## 4. Catch-up / render / summarize

| File(s) | Tests | Tier | Src module(s) |
|---|---|---|---|
| `test_catchup_burn.py`, `test_catchup_counts.py`, `test_catchup_press.py`, `test_catchup_render.py`, `test_catchup_sanitizer.py`, `test_catchup_slice.py` | 55 | D | `daemon/features/catchup.py`, `catchup.py` |
| `test_assembler.py` | 14 | U | `assembler.py` |
| `test_cleaner.py` | 10 | U | `cleaner.py` |
| `test_summarizer.py` | 15 | U | `summarizer.py` |

## 5. Keepalive (Bluetooth)

| File(s) | Tests | Tier | Src module(s) |
|---|---|---|---|
| `test_keepalive_asset.py`, `test_keepalive_doctor.py`, `test_keepalive_manager.py`, `test_keepalive_presence.py`, `test_keepalive_toggle.py` | 49 | U | `daemon/keepalive.py` |
| `test_keepalive_wiring.py` | 11 | D | same — through the speak loop (`test_recheck_never_raises_into_the_speak_loop`) |

Mutation: **scored, PENDING triage** — see §Mutation below.

## 6. Doctor / health diagnostics

| File(s) | Tests | Tier | Src module(s) |
|---|---|---|---|
| `test_cli_doctor.py`, `test_doctor_fault_log.py`, `test_doctor_reachability.py`, `test_doctor_restore_health.py`, `test_doctor_speaks.py`, `test_doctor_speech_path.py`, `test_doctor_supervision.py`, `test_doctor_verdict_delivery.py`, `test_doctor_voice_row.py`, `test_macos_supervisor_hotkeyd_row.py` | ~62 | D | `cli/doctor.py` |
| `test_doctor_no_side_effects.py` | 2 | **S** | asserts doctor never calls `ensure_running` — a guard on the diagnostic's own side-effect-free contract |
| `test_status_diagnostics.py` | 10 | D | `cli/doctor.py`, `daemon/host.py` (speaker_held / wedge rows) |

Note: `test_doctor_speech_path.py` alone carries 22 of the cluster's ~62 tests (43%) — flagged by
Phase 1 as a duplication-smell candidate; not resolved by this audit wave, still worth a look.
Mutation: **PENDING, not started** — scope resolved (30 files), no mutmut run yet.

## 7. Sessions / roster / persistence

| File(s) | Tests | Tier | Src module(s) |
|---|---|---|---|
| `test_sessions.py` | 40 | U | `sessions.py` |
| `test_persistence.py` | 35 | D/U | `daemon/persistence.py` |
| `test_atomicio.py` | 5 | U | `atomicio.py` |
| `test_history.py` | 31 | U | `history.py` |
| `test_paths.py` | 15 | U | `paths.py` |
| `test_one_shot_deliverability.py` | 13 | D | receipts one-shot delivery guarantee across `history.py`/`daemon/features/*.py` |
| `test_daemon_streams.py` | 20 | D | `session_stream.py`, `daemon/host.py` |

Mutation: `sessions.py` **PENDING / IN FLIGHT** — see §Mutation.

## 8. Speaker / queue — the audible pipeline

| File(s) | Tests | Tier | Src module(s) |
|---|---|---|---|
| `test_speaker.py`, `test_speaker_cancel_2b.py`, `test_speaker_no_overlap.py`, `test_speaker_transient.py` | 41 | U | `speaker.py` |
| `test_queue.py` | 27 | U | `queue.py` |
| `test_speak_failure_cue.py`, `test_speak_failure_memo.py`, `test_speak_failure_amplification.py`, `test_failure_tones.py`, `test_daemon_speak_resilience.py` | 47 | D | `speaker.py`, `daemon/features/*.py` failure-fallback paths |
| `test_voiceout_direct.py`, `test_voiceout_routing.py`, `test_voice_per_utterance.py`, `test_spearcon.py` (audible half) | ~27 | D | `cli/voiceout.py`, `spearcon.py` |
| `test_repeat_last.py` | 8 | D | replay-last-utterance path |

Mutation: **both CLOSED** — see §Mutation (`queue.py`, `speaker.py`).

## 9. Hooks / install / uninstall

The heaviest black-box cluster in the suite — Claude Code hook JSON, subprocess spawn, real
filesystem paths outside `src/`.

| File(s) | Tests | Tier | Src module(s) |
|---|---|---|---|
| `test_hooks_entry.py` | 34 | D/**B** | `hooks_entry.py` |
| `test_hooks_json.py` | 1 | **S** | validates `hooks/hooks.json` shape directly |
| `test_hooks_async.py`, `test_hook_install_gate.py`, `test_sonari_hook_bin.py` | 19 | **B** | subprocess-drive `bin/sonari-hook` |
| `test_bin_shims.py`, `test_bin_sonari.py` | 8 | **B** | subprocess-drive `bin/sonari`, `bin/sonari-daemon` |
| `test_cli_install.py`, `test_cli_install_notes.py`, `test_install_record.py`, `test_install_summary.py` | 21 | D/**B** | `cli/install.py`, `install_record.py` |
| `test_cli_uninstall.py`, `test_uninstall_disclosure.py`, `test_uninstall_hook_gate.py`, `test_uninstall_teardown.py`, `test_macos_uninstall_rc.py` | 27 | D/**B** | `cli/teardown.py` |

## 10. Daemon core, dispatch, protocol, concurrency

| File(s) | Tests | Tier | Src module(s) |
|---|---|---|---|
| `test_daemon_loop.py`, `test_daemon_main.py`, `test_daemon_single_instance.py`, `test_daemon_package.py`, `test_daemon_server.py`, `test_daemon_conn.py`, `test_daemon_dispatch.py`, `test_daemon_context.py`, `test_daemon_registry.py`, `test_daemon_faulthandler.py`, `test_daemon_helpers.py` (harness self-test) | ~65 | D | `daemon/host.py`, `daemon/bootstrap.py`, `daemon/registry.py`, `daemon/server.py`, `daemon/context.py` |
| `test_daemon_control.py`, `test_daemon_stop.py`, `test_daemon_settings.py`, `test_daemon_minqueue.py` | 50 | D | dispatch of control/settings verbs (rate, minqueue, stop, skip) |
| `test_daemon_phase2.py`, `test_daemon_phase21.py`, `test_daemon_setup_health.py` | 60 | D | verbosity/cue-gating integration, session-start health |
| `test_daemon_prose.py`, `test_daemon_teaching.py`→`test_teaching.py` | 30 | D | `daemon/features/prose.py`, `daemon/features/teaching.py` |
| `test_daemon_state.py` | 5 | U | `daemon/state.py` |
| `test_concurrency_guards.py` | 6 | D | cross-stream storm/race guards — this file owns the pre-existing flaky stress test noted above |
| `test_protocol.py` | 10 | **S** | exhaustive `MsgType` pin (both directions: every constant present, no extras) — the same file P4b's navigation fix and the `skip`-verb dead-pinning check both cite as the "would need editing on any cut" guard |
| `test_witness.py` | 9 | D | daemon watchdog ping/alarm |

Mutation: `daemon/host.py` **PENDING / IN FLIGHT** (largest single module, only a fraction of its
mutant population generated so far — see §Mutation).

## 11. Hotkeys / keymap / platform (macOS)

| File(s) | Tests | Tier | Src module(s) |
|---|---|---|---|
| `test_keymap.py` | 46 | D/U | `keymap.py` — largest file in the suite; its 3× copy-pasted `mac` fixture was hoisted to `conftest.py` by P4 (28 call sites across this file + `test_cli_control.py` + `test_docs_sync.py`, zero caller-side edits needed) |
| `test_daemon_hotkeys.py`, `test_hotkeyd_contract.py` | 16 | D | hotkey dispatch → daemon |
| `test_hotkeyd_swift.py`, `test_raise_swift.py` | 7 | **B** | shell out to `swiftc` to compile `sonari-hotkeyd.swift` / `sonari-raise.swift` |
| `test_macos_hotkeys.py`, `test_macos_raise.py`, `test_macos_supervisor.py`, `test_macos_tts.py`, `test_macos_tts_kokoro.py`, `test_macos_earcon.py`, `test_macos_helpers.py`, `test_macos_backend.py`, `test_macos_launch_spec.py` | ~77 | U | `platform/macos/*.py` |
| `test_platform_base.py` | 5 | **S** | Protocol structural guard — the 4 single-impl backends (`TtsBackend`, `EarconBackend`, `HotkeyBackend`, `SupervisorBackend`) stay `runtime_checkable`; `RaiseBackend` (the one real ABC with 2 impls) is separately exercised |
| `test_platform_factory.py`, `test_platform_raise_seam.py`, `test_raise_service.py`, `test_transport.py` | 15 | U | `platform/__init__.py`, `raise_service.py`, `platform/transport.py` |
| `test_pitch_assets.py` | 2 | **B** | reads real `.wav` assets via the `wave` module |
| `test_kokoro_provision.py` | 13 | D | `kokoro_provision.py` |

## 12. CLI surface & settings

| File(s) | Tests | Tier | Src module(s) |
|---|---|---|---|
| `test_cli_control.py` | 33 | D | `cli/control.py` — every CLI verb (`nav`, `repeat`, `skip`, …) sends the right message |
| `test_cli_focus_follow.py`, `test_cli_resolve_python.py`, `test_cli_voices.py`, `test_verbosity_confirm.py`, `test_voice_state_submit.py`, `test_submit_ack.py`, `test_commands.py` | ~24 | D | `cli/control.py`, `cli/__init__.py` |

## 13. Config / manifests / client / liveness / misc units

| File(s) | Tests | Tier | Src module(s) |
|---|---|---|---|
| `test_config.py` | 18 | U | `config.py` |
| `test_manifests.py`, `test_check_meta.py`, `test_verdict.py` | 18 | D/U | `cli/checkmeta.py`, `cli/verdict.py` |
| `test_client_ensure.py`, `test_client_ensure_backoff.py`, `test_client_send.py` | 9 | U | `client.py` |
| `test_liveness.py`, `test_liveness_marks.py` | 28 | U/D | liveness watchdog marks |
| `test_liveness_contract.py` | 3 | **B** | subprocess/text-scan contract on the liveness protocol |
| `test_announce.py`, `test_announce_grain.py`, `test_announce_seam.py`, `test_session_announce.py`, `test_restart_line.py` | 22 | D | announce composition |
| `test_docs_sync.py` | 10 | **S** | `.venv/bin/python scripts/gen_docs.py --check` — generated docs match source |
| `test_privacy_doc.py` | 8 | **S** | PRIVACY.md content assertions |
| `test_fixtures.py` | 7 | U | fixture-loader self-test (loads `tests/fixtures/*.json`) |
| `test_daemon_prose.py` (cross-listed, see §10) | — | — | — |

## 14. Hermeticity & isolation guards (cross-cutting)

These don't own a product feature — they guard the audit's own standing rule (sacrificial HOME,
never touch the real `~/.sonari`).

| File(s) | Tests | Tier | Notes |
|---|---|---|---|
| `test_hermetic_refusal.py` | 4 | **S** | real-HOME is computed from the password database, not env — closes the class of bug that let a hand-rolled mkdtemp bind by value and destroy the real install |
| `test_real_home_canary.py` | 7 | **S**/**B** | session-scoped canary; stays quiet when nothing outside the sandbox moved (receipts-born, `test_fake_speaker_receipt.py`'s docstring-sibling) |
| `test_no_independent_home_derivation.py`, `test_no_os_branch_in_core.py`, `test_py39_compat.py` | 8 | **S** | source-text scans for banned patterns / Python-3.9-compatible shebang |
| `test_paths_conftest_isolation.py` | 3 | **B** | isolation.py / conftest repoint itself |
| `test_repoint.py` | 8 | D | the repoint mechanism — historically the exact file whose assertion went silent on the owner's real install for five weeks (see `test_fake_speaker_receipt.py`'s docstring) |
| `test_isolation_helper.py` | 8 | U | `tests/_isolation.py` helper itself |

---

## Mutation-scored modules

Scoring source: `/Users/Nima.Hakimi/projects/private/sonari-audit/scratchpad/test-audit/modules/*/analysis.json`
(reclassified survivors) and raw `ids.*` files (unreclassified runs), cross-checked against
`TRIAGE.json` for the three modules that got a full real-gap/equivalent/trivial-cosmetic pass.

| Module | Mutants | Killed | Raw survived | True survivors | Status |
|---|---|---|---|---|---|
| `queue.py` | 46 | 37 | 9 | 5 | **CLOSED** — 4 equivalent-mutant (dead-default / inert-annotation, ids 5/6/12/15), 1 real-gap (id 32, `claim_head_as_control_cue`'s success/failure signal) — pinned by P4b (`4c225ee`) |
| `speaker.py` | 78 | 62 | 16 | 12 | **CLOSED** — 1 equivalent (id 13), 5 trivial-cosmetic (23/25/26/49/50), 4 real-gap pinned by P4b (id 51 `cancel_epoch`, 66 `.transient` duck-type guard, 77 `set_voice`, 78 `set_rate`), 2 equivalent unactioned (52/53, no pin needed) |
| `daemon/features/navigation.py` | 129 | 113 | 16 | 12 | **CLOSED** — 2 equivalent (ids 11, 104), 10 real-gap pinned by P4b (ids 12/13/48/58/72/99/103/105/117/127) via the widened `test_answerability.py` chokepoint guard + targeted per-site asserts |
| `cues.py` | 78 | 77 | 1 | 0 | **CLOSED-CLEAN** — sole raw survivor reclassified away (flake/scope, not a true survivor); no P4b action needed |
| `daemon/state.py` | 14 | 12 | 2 | 1 | **PENDING TRIAGE** — true survivor id 2 identified but never classified real-gap/equivalent |
| `session_stream.py` | 39 | 35 | 4 | 3 | **PENDING TRIAGE** — true survivors ids 4/7/26 identified but never classified |
| `daemon/keepalive.py` | 149 | 128 | 21 | — | **PENDING** — full raw mutmut run complete, but the reclassification/true-survivor pass that separates flakes from real gaps never ran |
| `daemon/host.py` | ~716+ | 62 of ~84 run | 22 | — | **PENDING / IN FLIGHT** — largest module in the suite; only ~84 of 716+ generated mutants have been run |
| `daemon/features/playback.py` | 232 | 70 of ~90 run | 18 | — | **PENDING / IN FLIGHT** — only 90 of 232 mutants run |
| `sessions.py` | 161 | 8 of 10 run | 2 | — | **PENDING / IN FLIGHT** — only 10 of 161 mutants run |
| `cli/doctor.py` | — | — | — | — | **PENDING, NOT STARTED** — test scope resolved (30 owning files), no mutmut run |

Stray directory `modules/daemon_features_navigation/` (single underscore, vs. the real
`daemon__features__navigation/`) holds only an orphaned `patches/` subfolder with no results — a
stale/duplicate artifact from an earlier tool naming convention, not a second scored module.
Ignore it; the real navigation data is in `daemon__features__navigation/`.

---

## Known gaps

### Closed — real-gap mutation survivors, fixed in this audit wave (P4b)

All 15 pinned in `/Users/Nima.Hakimi/projects/private/sonari-p4b` branch `audit/p4b-pins`
(commits `4c225ee`, `45a18d8`, `2da23f0`), teeth-proofed against the actual mutant patch each.
Kept here as the historical record so a future mutation re-run on these modules recognizes
already-closed IDs.

| Module | id | Behavior that was unpinned | Now pinned by |
|---|---|---|---|
| `queue.py` | 32 | `claim_head_as_control_cue()` always returned the same value regardless of success | `test_queue.py::test_claim_head_as_control_cue_marks_the_head_and_reports_success` + empty-queue sibling |
| `speaker.py` | 51 | `cancel_epoch()` could collide across cancels | `test_speaker.py::test_cancel_epoch_advances_on_every_cancel_never_collides` |
| `speaker.py` | 66 | A truthy non-`None` player without `.poll` was accepted | `test_speaker.py::test_player_returning_a_truthy_object_without_poll_is_rejected` |
| `speaker.py` | 77 | `set_voice()` had no test proving the next `speak()` used it | `test_speaker.py::test_set_voice_changes_the_voice_a_subsequent_speak_uses` |
| `speaker.py` | 78 | same, for `set_rate()` | `test_speaker.py::test_set_rate_changes_the_rate_a_subsequent_speak_uses` |
| `daemon/features/navigation.py` | 12, 13 | empty-history announce text/`is_decision` flag unpinned | `test_daemon_nav.py::test_nav_with_empty_history_announces` (widened) |
| `daemon/features/navigation.py` | 48, 58, 105 | non-chokepoint `is_decision` enqueue sites unguarded | widened `test_answerability.py` chokepoint AST guard (now covers every `daemon/features/*.py`, not just `decisions.py`) |
| `daemon/features/navigation.py` | 72, 99 | `next_response` arithmetic / plural-branch wording | `test_daemon_nav.py::test_next_response_steps_forward_one_turn_at_a_time` |
| `daemon/features/navigation.py` | 103 | cursor anchoring after prev-then-next | `test_daemon_nav.py::test_prev_response_then_next_step_anchors_cursor_at_the_responses_start` |
| `daemon/features/navigation.py` | 117 | missing `to` key silently went nowhere instead of defaulting to prev | `test_daemon_nav.py::test_nav_with_no_to_key_defaults_to_prev` |
| `daemon/features/navigation.py` | 127 | a crossed nav onto a muted target dropped the folder-name cue | `test_muted_read_gestures.py::test_a_crossed_nav_names_the_folder_on_a_muted_target` |

### Open — CONFIRMED live bugs from the seam-bug hunt, not yet fixed

From `HUNT-RESULTS.json` (9 CONFIRMED verdicts out of 18 findings; 5 DOWNGRADED, 1 REFUTED
excluded here — see that file for the full record). Each was independently re-reproduced under a
fresh sacrificial HOME before being marked CONFIRMED. These are **composition/sequencing bugs** —
exactly the class the charter's REASON TO EXIST describes, found by driving real gesture
sequences rather than single-site assertions. Out of this audit's P2–P5 scope to fix; recorded
here so the next pass starts from a map, not a re-hunt.

| Severity | Bug | Owning src module(s) |
|---|---|---|
| High | A muted session's nav replay is marked control_cue wholesale — ⌃⌘M can't stop it, starves a live session's decision announce | `daemon/features/navigation.py`, `daemon/features/playback.py`, `daemon/host.py` |
| High | ⌃⌘S resume promises the dropped pile stays catch-up-reachable; the first post-resume utterance silently buries all of it | `daemon/features/playback.py`, `daemon/host.py`, `history.py`, `session_stream.py` |
| High | ⌃⌘M cannot silence a read gesture — the muted seek-and-play backlog keeps speaking, "All stopped." lies at the end of it | `daemon/features/navigation.py`, `daemon/features/playback.py`, `daemon/host.py` |
| High | Chooser-commit onto a live session — the voice keeps reading the session left behind, unattributed, landing cue arrives fourth | `daemon/features/chooser.py`, `daemon/host.py` |
| Medium | `aged_out` is structurally unreachable when the frontier is `None` — a never-spoken session's catch-up starts silently mid-pile | `history.py` |
| Medium | ⌃⌘R after a muted read answers with a stale pre-mute utterance, with no cue that it's stale | `daemon/features/playback.py`, `daemon/host.py` |
| Medium | `voice` row's specced fail-open is defeated whenever the neural venv is provisioned — a failed listing produces a confident RED naming the working voice | `cli/doctor.py` |
| Medium | An orphan muted stream is invisible and unclearable — boot line names a session ⌃⌘W says doesn't exist, no key can un-mute it | `sessions.py`, `daemon/host.py` (boot-announce path; not confirmed to file:line — mechanism field carried no explicit src cite, inferred from title/repro content) |
| Medium | The prescribed 'degraded' recovery (`sonari keepalive off` then `on`) silently does nothing when both land inside one utterance | `daemon/features/control.py`, `daemon/keepalive.py` |

---

*This map reflects the audit's projected merge state, not a committed tree, at the time it was
written. Regenerate the file-count/test-count header and the mutation table whenever the
underlying branches change materially.*
