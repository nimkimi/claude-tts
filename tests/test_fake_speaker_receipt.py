"""D0.2: the daemon's test double must be able to catch a dead cue.

daemon_helpers.FakeSpeaker.transient appended the kind unconditionally, so 65
`.earcons ==` assertions across 27 files proved a cue was CALLED and none
proved a sound would PLAY. test_repoint.py asserted repoint fired while
repoint had been silent on the real install for five weeks.
Spec: docs/superpowers/specs/2026-08-28-receipts-design.md §3.2.
"""
import pytest

from daemon_helpers import FakeSpeaker, make_daemon


LEGACY_SIX = {
    "choice": "/System/Library/Sounds/Ping.aiff",
    "error": "/System/Library/Sounds/Sosumi.aiff",
    "permission": "/System/Library/Sounds/Funk.aiff",
    "plan": "/System/Library/Sounds/Submarine.aiff",
    "ready": "/System/Library/Sounds/Glass.aiff",
    "turn_done": "/System/Library/Sounds/Tink.aiff",
}


@pytest.mark.expects_silent_cue
def test_fake_speaker_records_a_silent_cue_instead_of_an_earcon():
    """An unresolvable kind is NOT an earcon. It is a receipt of silence."""
    sp = FakeSpeaker(earcons={})
    sp.transient("repoint")
    assert sp.earcons == []
    assert sp.earcon_paths == []
    assert sp.silent_cues == ["repoint"]


def test_a_resolvable_cue_records_the_asset_it_resolved_to():
    sp = FakeSpeaker(earcons={"choice": "/System/Library/Sounds/Ping.aiff"})
    sp.transient("choice")
    assert sp.earcons == ["choice"]
    assert sp.earcon_paths == ["/System/Library/Sounds/Ping.aiff"]
    assert sp.silent_cues == []


@pytest.mark.expects_silent_cue
def test_an_explicit_null_asset_still_mutes_a_cue():
    """Regression pin: `{"choice": null}` in config.json means MUTE, and must
    keep meaning mute after the R3 merge (spec 4.3)."""
    sp = FakeSpeaker(earcons={"choice": None})
    sp.transient("choice")
    assert sp.earcons == []
    assert sp.silent_cues == ["choice"]


@pytest.mark.expects_silent_cue
def test_make_daemon_seeds_the_fake_from_the_config_earcons():
    """make_daemon(earcons=...) seeds BOTH the config and the fake, so a test
    can ask 'what would this user's config actually sound like?'. Deliberately
    fires `repoint` against a config that lacks it, so the drain would catch
    this test too without the marker."""
    daemon, _, speaker, _, config = make_daemon(earcons=LEGACY_SIX)
    assert config["earcons"] == LEGACY_SIX
    speaker.transient("choice")
    assert speaker.earcons == ["choice"]
    speaker.transient("repoint")
    assert speaker.silent_cues == ["repoint"], (
        "repoint is absent from this legacy config and must read as silent"
    )


def test_make_daemon_defaults_to_a_fresh_install_where_every_kind_resolves():
    """None means the full default table -- the fake mirrors a fresh install,
    which is what bootstrap produces today. This is what keeps the suite's 65
    existing `.earcons ==` assertions green."""
    daemon, _, speaker, _, config = make_daemon()
    for kind in ("choice", "error", "permission", "plan", "turn_done",
                 "repoint", "submit_ack", "crossing", "error_system",
                 "error_misdirected", "permission_expired",
                 "alarm_daemon_down", "alarm_hotkeys_down"):
        speaker.transient(kind)
    assert speaker.silent_cues == []
    assert len(speaker.earcons) == 13


@pytest.mark.expects_silent_cue
def test_the_drain_fails_a_test_that_fired_a_silent_cue():
    """The rule itself: a cue that would have made no sound is a failure.

    Builds its own FakeSpeaker that fires a silent cue to prove the helper
    raises -- that FakeSpeaker also lands in the autouse fixture's registry,
    so this test is itself caught by the live fixture at teardown."""
    import conftest

    sp = FakeSpeaker(earcons={})
    sp.transient("repoint")
    with pytest.raises(AssertionError, match="repoint"):
        conftest._assert_no_silent_cues([sp], marker=None)


@pytest.mark.expects_silent_cue
def test_the_drain_lets_a_marked_test_through():
    """Same reason as the test above: this FakeSpeaker's silent cue also
    lands in the autouse fixture's registry, so this test opts out too."""
    import conftest

    sp = FakeSpeaker(earcons={})
    sp.transient("repoint")
    conftest._assert_no_silent_cues([sp], marker=object())   # no raise


def test_the_drain_is_wired_as_an_autouse_fixture():
    """From here on all 83 daemon-test files are dead-asset detectors."""
    import conftest

    fixture = conftest._no_silent_cues
    # pytest 9 renamed the FixtureFunctionDefinition marker attribute from
    # _pytestfixturefunction to _fixture_function_marker; the brief predates
    # that rename (repo pins pytest>=7, installed venv is 9.0.3).
    assert fixture._fixture_function_marker.autouse is True
