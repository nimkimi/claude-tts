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
   - "may this session's voice reach the ear?" — chooser, focus/jump, where-am-i,
     keep-going (host), prose gating
   - `_enqueue` delivery flags (`mute_exempt`/`pause_exempt`/`at_front`) — any
     new cue must state WHY each flag is set or not
   - the cue registry (`src/sonari/cues.py`): every audible emission flows
     through `host.cue(kind)` or an enqueued prelude/content item, and the
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
