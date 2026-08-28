# The reconciliation pass

Run this before closing ANY campaign (feature arc, fix wave, release):

1. **Regenerate the told story:** `.venv/bin/python scripts/gen_docs.py`
   (README islands), then `--check` must exit 0. Re-read the hand-authored
   README sections your campaign touched: does every behavioral claim still
   have code behind it?
2. **Run the full suite + guards:**
   `.venv/bin/python -m pytest -q` and
   `.venv/bin/python -c "import sonari.daemon"` and
   `.venv/bin/python -m pytest -q tests/test_protocol.py tests/test_concurrency_guards.py`.
3. **Sweep the shared-invariant consumers.** These rules are enforced at more
   than one call site; if your campaign touched ONE, check them ALL:
   - "may this session's voice reach the ear?" is answered ONLY by
     `SessionManager.liveness()` (three states: `live`/`pending`/`dead`) or its
     derived binary `is_live()`. The raw signals it composes (`_provisional`,
     `_tty_evicted`, `ttyutil.tty_alive`) are private to `sessions.py` — a
     drift guard in `tests/test_liveness_contract.py` fails if any other
     module reaches past the composed predicate. R1: any inbound message
     whose session is quarantined (`pending`) clears that quarantine at the
     `handle_message` dispatch chokepoint, not per-handler (WITNESS_PING is
     excepted by TYPE — not because it carries no session — the check is
     `msg.get("type") != MsgType.WITNESS_PING`, and
     `test_witness_ping_never_clears_quarantine` pins the type exemption
     itself by sending a forged session field). Per-consumer dispositions:
     - **chooser** (`chooser.py`) — candidates are `is_live`-filtered (hiding
       a non-live session is correct for the HANDS: you cannot dial into a
       session you cannot reach). Both the chord-release commit and a digit
       press re-check liveness at commit time and speak the shared word
       ("That session closed.") on a target that died mid-browse.
     - **jump-waiting** (`focus.py`) — candidates are `is_live`-filtered; a
       commit-time re-check (`liveness(target) == "dead"`) closes the
       selection-to-focus gap. This is narrower than the chooser's two-shape
       check (`_commit` also tests `target not in sessions.session_ids()`,
       for a candidate snapshot that can outlive a real SESSION_END between
       separate messages) — not a parity with it. The narrower check is safe
       here because SESSION_END pops `_streams` (`lifecycle.py:183`), and
       both selection (`_waiting_target`) and the recheck require the target
       to hold a stream: a fully unregistered session can never become
       `target` in the first place, within one handler dispatch. The empty
       case ("No session waiting.") gains a truthful tail counting
       backlog-holding non-live sessions, e.g. "No session waiting. Two
       pending."
     - **where-am-I** (`control.py`) — the MARK surface. A dead voice or
       keyboard pointer gains a marker clause, e.g. "Keyboard: web 1,
       closed." The Also-map names any pending-or-dead session that still
       holds content clauses (leading with "pending" or "closed"), collapses
       clause-less pending sessions into an aggregate tail ("Two pending."),
       and drops clause-less dead sessions entirely — nothing to act on.
     - **keep-going** (`host.py` `_select_keep_going`) — skips dead sessions:
       a dead session's backlog is never auto-voiced onto the ear, and a
       speaker that dies MID-DRAIN is released (`_release_dead_speaker`, at
       the pop boundary) rather than read to the end of its pile (R-1). One
       exception: a deliberate press may sanction ONE dead stream
       (`host._sanction_dead_read`), one-shot and never automatic, at a GRAIN
       matching what the press asked for:
       - **whole stream, backlog included** — a read OF that session: idle
         ⌃⌘W, ⌃⌘W on a dead speaker (`control.py`), ⌃⌘L catch-up
         (`catchup.py _cue_dest`), and ⌃⌘←/→ navigation (`navigation.py`,
         whose seek-and-play clears the queue first, so the whole stream IS
         the requested content).
       - **the one front item** — an answer that merely LANDED there because
         its destination falls back to `workspace()`: the rate/verbosity
         readbacks (`control.py _readback`), jump-waiting's empty case
         (`focus.py`), the repeat/skip-pile/jump-decision fallbacks
         (`playback.py`), the chooser preview (`chooser.py
         _deliver_preview`), the answer-permission approve/deny confirm
         (`decisions.py on_answer_permission` — RR-3, fix-wave E),
         ⌃⌘S-start's "Resumed." on a dead MUTED workspace (`playback.py
         on_stop_session` — RR-4, fix-wave E), and — owner-ruled
         2026-08-15, closing the family — the learn-mode toggle and the
         query-actions readout (`teaching.py on_learn_mode` /
         `on_query_actions`, both targeting `workspace()`), the learn-mode
         IDLE AUTO-EXIT (`host.py` `_learn_mode_expired`, its OWN site: the
         toggle's wiring never reached it, and wave1-T4 item E closed it in
         code without this list being updated), and re-read
         options (`decisions.py on_reread_options`, targeting `foreground()`
         instead — the different accessor; both of its enqueues, the
         cached-options branch and the "No options right now." fallback, are
         sanctioned). A settings nudge must never read out a closed session's
         pile, and neither must un-muting one. A single-item press also never
         claims the voice for a STOPPED stream — the held branch returns
         above the pop boundary, so the mark could not be spent and the voice
         would wedge there.
       Pending sessions stay adoptable — post-R1 the only content a pending
       stream can hold is the daemon-authored restart line, whose delivery
       deliberately rides this exact path (`tests/test_restart_line.py` pins
       it). Idle-⌃⌘W, catch-up, nav and every single-item answer DEPEND on
       this adoption/sanction machinery: any campaign touching
       `_select_keep_going` must also sweep `_release_dead_speaker` and every
       `_sanction_dead_read` call site above. (A fourth candidate,
       ⌃⌘S-start, was carried in this same list through fix-wave D as
       "pre-existing" — fix-wave E's re-review measured it byte-identical to
       the pre-release base, i.e. an R-1 release regression, not
       pre-existing; it is wired above, not listed here. Fix-wave E's
       re-review also found the answer-permission confirm, which had
       appeared in NEITHER the wired nor the unwired list at any point — it
       is wired above too.) The three sites once carried here as an explicit
       HONEST LIMIT (learn mode, query-actions, re-read options — measured
       pre-existing, left for an owner-adjacent ruling) were ruled on
       2026-08-15 and closed above, single-item grain, wave1-T2. That does
       NOT make the family fully wired — this paragraph said so and was
       wrong. `host.py` `_raise_failed` — the "Bring X forward to type."
       line — enqueues with `at_front=True` and no `_sanction_dead_read`;
       the wave1 whole-branch review measured it as the remaining single-item
       site without one, and no wider re-audit has been run since. Wiring it
       needs its own RED test and is booked, not done.
     - **prose gating** (`prose.py`) — no gate in the handler. R1 clears the
       pending tier at the dispatch chokepoint, so `on_prose` never buffers
       into a quarantined stream. A dead session's prose still buffers, by
       design: the pile stays discoverable via where-am-I's closed mark and
       readable via catch-up, and keep-going never voices it.
     - **catch-up** (`catchup.py`) — proceeds on any registered workspace
       target; reading a closed session's stored pile is a legitimate
       recovery act, so it is never blocked. A dead target's acknowledgment
       gains the closed marker; pending is structurally unreachable as a
       workspace target.
     - **navigation** (`navigation.py`) — SANCTIONED UNGUARDED: navigating is
       deliberate re-reading of already-stored transcript content, and a
       liveness check on every press would be noise, not signal. (The
       workspace pointer itself going stale on a dead session is separate
       hygiene work, out of this pass's scope.)
     - **jump-decision** (`playback.py` `on_jump_decision`) — the crossed
       path, which moves the voice via `sessions.focus`, checks
       `liveness(target) == "dead"` only — a dead target speaks the closed
       word and the move is refused. Same guard shape (and same safety
       reasoning) as jump-waiting, not the chooser's two-shape check: this
       path only reaches the recheck when the target's stream still holds a
       queued decision, and SESSION_END popping `_streams` means an
       unregistered target can never arrive here. The non-crossed path
       (acting within the current workspace) is deliberate reading, like
       navigation, and stays unguarded.
     - **restart line** (`host.py` `_compose_restore_line`) — every restored
       session is uniformly pending at delivery, so the line stays
       content-only by design (a liveness qualifier there would carry zero
       information); the per-session mark is where-am-I's job.
     - **teaching** (`teaching.py`) — checked 2026-08-01, unchanged: its
       "waiting"-phrased hints stay true because jump-waiting only ever
       offers live sessions.

     A new consumer must consult `liveness()`/`is_live()` and add its
     disposition here, plus a pin in the per-surface suite
     (`tests/test_liveness_marks.py`).
   - `_enqueue` delivery flags (`control_cue`/`at_front`) — any new cue must
     state WHY each flag is set or not. (`mute_exempt` and `pause_exempt` were
     two names for one idea and are gone; `control_cue` replaced both.
     `tests/test_control_cue_contract.py` fails on either name reappearing in
     `src/`.)
   - the cue registry (`src/sonari/cues.py`): every audible emission flows
     through `host.cue(kind)` or an enqueued prelude/content item — with TWO
     sanctioned off-queue emissions: the ALARM TIER (next bullet) and the W8
     BOOT CUE (`bootstrap.py`, a direct one-shot `speaker.speak` because an
     enqueued boot cue would never voice pre-loop; designed not to overlap;
     moves on-queue with D5's boot reorder — the R2 residual) — and the
     retired `earcon`/`earcon_then`/`pitch` APIs stay dead (the drift tests in
     tests/test_cue_contract.py cover this — trust them, but a NEW sound needs
     a registry entry + reachability before it needs an asset); every
     registered cue stays reachable from a call site or socket kind; the
     coupling law holds — every user-meaningful ledger operation maps to ONE
     distinct sound, and no two operations share one
   - the ALARM-TIER EXEMPTION (the witness): `alarm_daemon_down` /
     `alarm_hotkeys_down` are contractually out-of-band — raw process spawn,
     never `host.cue()` / the transient arbiter / the queue — because they
     exist for when those may be dead. Law-1 sweeps treat them as the ONE
     sanctioned exception. The alarm paths themselves are UNWATCHED (the
     R1 half-open residual); a third launchd-scheduled checker is named
     future work, deliberately not built.
   - answerability (D7b): the directive signature == blocking permissions ==
     the `_pending_decisions` registrants, pinned by tests/test_answerability.py.
     A new decision producer must go through `_announce_decision` with an
     explicit `answerable=`; every unanswerable announce carries the advisory
     frame.
   - spoken session references ("this session" vs "another session"): the split
     is semantic — "this session" = the workspace you are currently at (the
     catch-up target), "another session" = some other workspace — so preserve it;
     collapsing to one word makes one context say something false, a meaning
     regression, not a cleanup
   - `commands/*.md` ↔ CLI parser ↔ README (the drift tests cover this — trust
     them, but new surfaces need new tests)
   - manifest descriptions and versions (pyproject / plugin.json /
     marketplace.json / `sonari.__version__` move together)
4. **Record the pass** in `.superpowers/sdd/progress.md`.

New docs surfaces get a generator + drift test, not a hand-maintained copy.
