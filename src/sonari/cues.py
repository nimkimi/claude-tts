"""The cue registry (D8 law 4): every audible emission Sonari can make, in one
chokepoint table. Feature code reaches audio ONLY through host.cue(kind)
(transients) or enqueue-with-prelude (verbal units); the drift guards in
tests/test_cue_contract.py enforce it.

Tiers:
- "transient": short non-verbal tone; bypasses the queue via the Speaker's
  one-slot arbiter — a new transient TERMINATES a still-playing one. May
  coexist with speech, never with another transient.
- "prelude": audio bound to a specific utterance's SpeechItem, played by the
  speak loop before its content as ONE indivisible unit (same claim, same
  cancel epoch — barge-in cuts the whole unit and it replays whole).
- "queued": the speak loop's normal verbal traffic (registered for
  completeness and the generated README island; no separate mechanism).
- "alarm": contractually OUT-OF-BAND verbal+tone (the witness): played via a
  raw process spawn precisely because the queue/arbiter may be dead; never
  fired through host.cue() (it rejects the tier) nor the transient arbiter.

D2/D7 delivered their reserved cues (failure words ride cue(word=); the
silences are submit_ack/repoint/crossing; the witness owns the alarm
tier). Still deliberately NOT entries (YAGNI): a watcher for the alarm paths
themselves (the disclosed residual), and the off-queue boot cue's future
home."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Cue:
    name: str          # registry key
    family: str        # "attention" | "feedback" | "failure" | "status" | "attribution" | "content"
    tier: str          # "transient" | "prelude" | "queued" | "alarm"
    doc: str           # user-facing one-liner for the generated README island
    # transient cues resolve their asset via Speaker (config-first + fallbacks);
    # prelude cues resolve per call site (spearcon path / pitch asset).


CUES = {c.name: c for c in (
    Cue("turn_done", "status", "transient",
        "A session finished its turn"),
    Cue("choice", "attention", "transient",
        "A question with options is waiting"),
    Cue("plan", "attention", "transient",
        "A plan is ready for review"),
    Cue("permission", "attention", "transient",
        "A permission ask is waiting"),
    Cue("error", "feedback", "transient",
        "That press had nothing to act on"),
    Cue("error_misdirected", "feedback", "transient",
        "Valid answer, wrong session"),
    Cue("error_system", "failure", "transient",
        "Sonari itself failed; the content is preserved unheard"),
    Cue("permission_expired", "failure", "transient",
        "A permission ask timed out unanswered"),
    # --- D2 §6 silences (2026-07-21; assets ratified by the ear-batch-2
    # audition 2026-08-01). No distinct solo-boundary kind: the owner's ear
    # ruling collapsed your_turn into turn_done — one sound at every turn
    # boundary; re-splitting needs a new ear ruling. ---
    Cue("submit_ack", "status", "transient",
        "Your prompt was submitted (off by default)"),
    Cue("repoint", "status", "transient",
        "Your click moved the workspace to a different session"),
    Cue("pitch_up", "feedback", "prelude",
        "Rising chirp bound to the front of an approval"),
    Cue("pitch_down", "feedback", "prelude",
        "Falling chirp bound to the front of a denial"),
    Cue("callsign", "attribution", "prelude",
        "The asking session's spoken label, bound to its own utterance"),
    Cue("crossing", "attribution", "prelude",
        "A neutral marker where the voice crossed to a session whose call-sign is not cached yet"),
    Cue("speech", "content", "queued",
        "Spoken readout of session output"),
    Cue("summary_voice", "content", "queued",
        "The catch-up summary's island voice"),
    # --- §7 witness alarm tier (out-of-band). Assets ratified (ear-batch-2);
    # load_config()'s merge of config.DEFAULTS keeps them from ever being
    # silently unconfigured; playback is a raw spawn, never the arbiter. ---
    Cue("alarm_daemon_down", "failure", "alarm",
        "hotkeyd lost the daemon and sounds the alarm itself"),
    Cue("alarm_hotkeys_down", "failure", "alarm",
        "The daemon lost hotkeyd and sounds the alarm itself"),
)}


def transient_kinds() -> "frozenset[str]":
    """The names feature code may pass to host.cue()."""
    return frozenset(n for n, c in CUES.items() if c.tier == "transient")


def is_registered(name: str) -> bool:
    return name in CUES
