"""D8 law 4: the registry is the chokepoint — complete (every kind string the
daemon emits), well-formed (known families/tiers only), helper-consistent."""
from sonari.cues import CUES, Cue, is_registered, transient_kinds

FAMILIES = {"attention", "feedback", "failure", "status", "attribution", "content"}
TIERS = {"transient", "prelude", "queued", "alarm"}


def test_registry_keys_match_entry_names():
    assert all(name == cue.name for name, cue in CUES.items())


def test_every_entry_is_well_formed():
    for cue in CUES.values():
        assert isinstance(cue, Cue)
        assert cue.family in FAMILIES
        assert cue.tier in TIERS
        assert cue.doc


def test_the_complete_transient_set():
    assert transient_kinds() == {
        "turn_done", "choice", "plan", "permission",
        "error", "error_misdirected", "error_system", "permission_expired",
        "submit_ack", "repoint"}


def test_prelude_and_queued_entries():
    assert {n for n, c in CUES.items() if c.tier == "prelude"} == {
        "pitch_up", "pitch_down", "callsign", "crossing"}
    assert {n for n, c in CUES.items() if c.tier == "queued"} == {
        "speech", "summary_voice"}


def test_is_registered():
    assert is_registered("error")
    assert not is_registered("waiting")   # the retired SP3 kind must not come back


def test_alarm_tier_entries():
    # §7 registry honesty: the witness alarms are REGISTERED but out-of-band —
    # raw-spawn playback for when the queue/arbiter may be dead.
    assert {n for n, c in CUES.items() if c.tier == "alarm"} == {
        "alarm_daemon_down", "alarm_hotkeys_down"}
