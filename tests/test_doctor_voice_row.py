"""R5.3: the row it replaces is structurally unfailable.

supervisor.doctor_rows() reported ("enhanced voice", bool(voice), ...) where
best_voice() returns "Samantha" as a hard-coded last resort on every path.
bool("Samantha") is True. Measured on the owner's machine: the row reported
"Samantha" while his config runs "Voice 1" -- not merely unfailable, but
reporting a voice he does not use. Meanwhile a config voice that is GONE makes
`say` exit non-zero on every utterance: total silence, green doctor.
Spec: docs/superpowers/specs/2026-08-28-receipts-design.md 6.3.
"""
from sonari.cli.doctor import _voice_row


def test_voice_row_fails_for_an_uninstalled_voice():
    name, ok, detail = _voice_row({"voice": "Ghost"},
                                  list_voices=lambda: ["Samantha", "Voice 1"])
    assert (name, ok) == ("voice", False)
    assert "Ghost" in detail
    assert "every utterance will fail" in detail
    assert "sonari voice" in detail


def test_voice_row_is_green_for_the_owners_configured_voice():
    """Measured against the live listing on 2026-08-28: "Voice 1" is present.
    This row will not go red on him on day one."""
    name, ok, _ = _voice_row({"voice": "Voice 1"},
                             list_voices=lambda: ["Samantha", "Voice 1"])
    assert (name, ok) == ("voice", True)


def test_voice_row_is_green_for_the_system_default():
    name, ok, detail = _voice_row({"voice": None},
                                  list_voices=lambda: ["Samantha"])
    assert ok is True
    assert detail == "system default"


def test_voice_row_fails_open_on_an_unreadable_listing():
    """A doctor that cries wolf about a working voice is worse than one that
    stays quiet."""
    def boom():
        raise OSError("say: not found")

    name, ok, detail = _voice_row({"voice": "Voice 1"}, list_voices=boom)
    assert ok is True
    assert detail == "voice listing unavailable"
    assert _voice_row({"voice": "Voice 1"}, list_voices=lambda: [])[1] is True


def test_enhanced_voice_row_is_gone():
    from sonari.platform.macos.supervisor import MacSupervisorBackend

    names = {row[0] for row in MacSupervisorBackend().doctor_rows()}
    assert "enhanced voice" not in names


def test_the_voice_row_is_spoken_by_name():
    # Assert MEMBERSHIP, not the return value: spoken_name falls back to the
    # raw check name, so `spoken_name("voice") == "voice"` is true whether or
    # not the _SPOKEN entry was ever added.
    from sonari.cli import checkmeta
    assert "voice" in checkmeta._SPOKEN
    assert "keepalive" in checkmeta._SPOKEN
    assert not checkmeta.is_warn("voice")
    assert not checkmeta.is_warn("keepalive")
