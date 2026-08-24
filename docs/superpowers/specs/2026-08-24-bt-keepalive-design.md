# Bluetooth Keep-Alive — Design Spec (2026-08-24)

## Problem (measured, not inferred)

On a Bluetooth headset (owner's Sonos Ace, log-verified 2026-08-24), macOS suspends
the A2DP audio stream ~1.1s after the last audio client goes quiet. Every Sonari
inter-item gap ≥ ~1s therefore tears the stream down; the next utterance triggers a
stream re-establishment (AVDTP handshake ~80ms + the headset's own unmute ramp)
that **swallows the head of the utterance**. Short earcons (~0.3s) can vanish
entirely inside the window; tails can be cut when the suspend races the sink buffer.

Evidence from the owner's `bluetoothd` log during one real failed readout:
- 27 stream suspend/re-establish cycles in 20 minutes, clustered on readout minutes
  (one teardown every 4–12s — i.e., at item boundaries).
- Suspend fires ~1.1s after audio stops; gaps as short as 0.45s observed triggering it.
- Mechanism proof (silent-burst probe): 4 silent bursts with 2.5s gaps → 4/4
  teardowns bare; the same bursts with a continuous silent stream playing → **0**.

A second, independent problem was measured and is explicitly OUT OF SCOPE:
sustained 2.4GHz interference (ReTx 12–34%, NoSync in every link-quality sample at
strong RSSI). That is environmental; no Sonari change can affect it.

## Decision record (owner-ratified 2026-08-24)

- **Fix = daemon-owned keep-alive stream** that holds the output device open so the
  A2DP stream never suspends between utterances.
- **Policy = session-scoped**: keep-alive runs while ≥1 **live** session is
  registered, plus a trailing hold after the last live session disappears.
  ("Speech-window only" rejected: first utterance after a pause still clips — the
  proactive-notification head is exactly what an eyes-free user cannot lose.
  "Always-on" rejected: the stream makes coreaudiod hold a
  `PreventUserIdleSystemSleep` assertion — measured — so an always-on stream means
  the Mac never auto-sleeps; a TTS tool silently preventing system sleep 24/7 is a
  defect class.)
- **Branch base = main @ ce56659** (ships independent of the parked wave-1 gate).
- **Config knob, default ON** (the fix is the point; costs are bounded by
  session-scoping and disclosed in docs).

## Design

### Component

`KeepAlive` manager, owned by the daemon Host, single responsibility: keep exactly
one (briefly two, during overlap) silent `afplay` child running whenever policy says
the device must stay open; kill them all when it says stop.

### Policy inputs (the liveness seam — load-bearing)

"Active" means: `any(sessions.is_live(s) for s in sessions.session_ids())`.
`is_live` is D3's single liveness composition and **fails closed** for
restored-but-unconfirmed (SP6 "pending") sessions and dead-tty sessions. Binding to
the raw roster instead would degrade session-scoped into always-on (restored
sessions linger up to `restore_max_age_hours=24`), silently defeating the sleep
tradeoff the owner chose. Any test suite for this feature MUST cover: a
restored/pending-only roster does NOT start keep-alive.

Re-evaluation triggers: session register, session unregister, and any event that
flips liveness for an existing session (reconnect confirm / tty eviction) — the
manager exposes one idempotent `poke()` (re-evaluate now) rather than distinct
started/stopped entry points, so call sites cannot get the edge-direction wrong.

### Trailing hold

When the last live session disappears, keep streaming for `KEEPALIVE_HOLD_S = 600`
(10 min, owner-ratified), then stop. A new live session during the hold cancels the
pending stop (timer cancelled, stream simply continues). Mirror the existing
`threading.Timer` pattern already used by the daemon (host.py LEARN_MODE idle
timer) including its cancellation discipline.

### The stream itself — overlap, never respawn-into-a-gap (measured requirement)

A sequential respawn loop **leaks**: with a 10-min silent file in a plain respawn
loop, the owner's log shows a stream teardown at exactly the 10-minute file
boundaries (2/2 marks). The spawn latency of the next `afplay` can exceed the
~1.1s suspend grace. Therefore:

- Silent asset: 300s of silence, 8kHz mono 16-bit WAV (~4.7MB), generated at
  runtime with the stdlib `wave` module into the Sonari dir (via `paths.py`; never
  a hardcoded `~`). Generated if missing, at daemon start or first keep-alive
  start. 8kHz proven sufficient to hold the stream (the probe used 8kHz files);
  CoreAudio mixes/resamples independently of the speech clients.
- Player cadence: spawn player A; `OVERLAP_S = 5` seconds before A's file ends,
  spawn player B; reap A when it exits; repeat. At least one player is always
  streaming; zeros mixing with zeros (or with speech) is inaudible by
  construction. The manager runs this in one daemon thread with an
  interruptible `threading.Event` wait (never bare `time.sleep`), so stop() is
  prompt.
- Stop: terminate all players, cancel timers, join the thread (bounded join —
  the daemon must never hang on shutdown because afplay wedged; mirror the
  bounded-reap discipline in `_AfplayHandle.terminate`).

### Failure semantics (must not create a new silent-failure class, and must not spin)

- A player that exits early (afplay missing, audio device error) is respawned with
  a 1s backoff. If 5 consecutive spawns die within 2s each, the manager **gives up
  for this activation** (state = "degraded"), stops retrying until the next
  policy edge (all-sessions-gone → active again). Rationale: degradation only
  returns the system to the pre-feature status quo (boundary clipping); it must
  never become a spawn storm — this is the exact shape of the wave-1
  `_signal_speak_failure` Critical (unbounded respawn), reviewed accordingly.
- No spoken cue on keep-alive failure (over-cueing; nothing is lost that speech
  itself won't reveal). Surface state via: one `sonari doctor` row (running /
  idle-by-policy / degraded / disabled) and the daemon log.
- Keep-alive children are fully independent of the Speaker's process bookkeeping —
  no shared handles, no queue interaction, no `_play_lock`. The Speaker must be
  able to wedge, restart, or fail without the keep-alive noticing, and vice versa.

### Config

`config.json` key `keepalive: "on" | "off"`, default `"on"`. Read the way the
nearest existing boolean knob (`focus_follow`) is read (same cadence: if that knob
is live-read per use, this one is; if boot-cached, this one is — mirror, don't
innovate). `off` ⇒ manager never spawns, doctor row says "disabled". User-facing
setter: `sonari:keepalive` command analog to existing knob skills (verbosity/rate),
plus README one-liner documenting the two disclosed costs: while active, the
headset's radio streams continuously (battery like music playback) and the Mac
won't idle-sleep (coreaudiod assertion).

### Platform scope

macOS only, additive: the manager shells `afplay` directly (it is not speech — it
must not enter the TTS backend's Kokoro/say routing). No Windows change; guard so
non-darwin platforms get a no-op manager (mirror the platform-split conventions).

## Non-goals

- 2.4GHz interference / ReTx (environmental; owner informed: Sonos firmware + RF
  hygiene are the only levers).
- Bluetooth-output *detection* (no polling `system_profiler`; the stream is
  harmless on wired/speaker outputs, and the sleep-assertion cost is bounded by
  session-scoping + the config knob).
- HFP/mic-profile handling (solved environmentally 2026-08-24: default input moved
  off the headset).
- Persisting keep-alive state across daemon restarts (policy re-derives from the
  live roster at boot).

## Verification

- TDD throughout; every test under a sacrificial `HOME` (repo standing rule).
- Unit: policy edges (start on first live, no-start on pending-only roster, hold
  timer, cancel-during-hold, config off, degraded gives up and stays given-up,
  bounded shutdown), overlap cadence (B spawned before A ends), WAV generation
  (valid header, correct duration, idempotent).
- Live (owner's ears + one `log show` query, AFTER his install): a long readout
  over Bluetooth shows zero `Successfully suspended stream` events between items
  and no boundary clipping. The mechanism itself is already field-proven (temp
  relief running 2026-08-24).

## Ship gates (owner's, unchanged)

Merge, push, version bump into a release, and `sonari install` to the live daemon
are all the owner's calls. Never run against the real `~/.sonari`.
