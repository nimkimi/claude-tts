"""Audit pins: doctor's one spoken diagnosis can certify health while the
audio path is either about to fail, or was never actually observed.

See /Users/Nima.Hakimi/projects/private/claude-tts/scratchpad/e3-review/test-audit/HUNT-RESULTS.json
findings 8, 9, and 10 for the full adjudication.
"""
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

from sonari.cli.doctor import _voice_row
from sonari.cli.verdict import verdict
from sonari.protocol import MsgType

REPO_SRC = str(Path(__file__).resolve().parent.parent / "src")


@pytest.mark.xfail(
    strict=True,
    reason="BUG-7 (pre-existing at 073b82b, per the hunter's own base-tree "
           "control run): the voice row's fail-open guard only fires on an "
           "EMPTY listing, not an UNREADABLE one -- with the neural venv "
           "provisioned, a failed native `say -v ?` still leaves a non-empty "
           "Kokoro-only listing, and a working `say` voice reads RED; "
           "awaiting owner fix decision -- see HUNT dossier finding 10.",
)
def test_bug7_voice_row_red_for_a_working_voice_when_native_listing_fails_under_kokoro():
    """BUG-7 (CONFIRMED, finding 10, severity medium).

    mechanism: src/sonari/cli/doctor.py:132-166 _voice_row()'s fail-open
    guard is `if not installed: return ("voice", True, "voice listing
    unavailable")`, but `installed` is
    platform/macos/tts.py:197-212 list_voices()'s UNION of native `say -v ?`
    output and the 28 Kokoro names -- and list_voices() SWALLOWS a native
    `say -v ?` failure (FileNotFoundError/OSError/SubprocessError,
    tts.py:205-209) and still returns `[] + kokoro_voices` whenever the
    neural venv is provisioned. With the neural venv provisioned that union
    is non-empty, so the fail-open guard never fires, and a native `say`
    voice like a configured "Voice 1" is simply absent from the Kokoro-only
    list -- the row goes RED naming a voice that is actually fine.

    ratified basis: spec docs/superpowers/specs/2026-08-28-receipts-design.md
    Sec 6.3, verbatim: "Fail-open: an empty or unreadable listing (`say`
    missing, `subprocess` error) renders the row green with 'voice listing
    unavailable'." The implementation conditions fail-open on the UNION
    being EMPTY, not on the NATIVE listing being unreadable -- so the
    fail-open promise holds only while Kokoro is unprovisioned.
    """
    # Exactly what tts.list_voices() returns when `say -v ?` raises but the
    # neural venv is provisioned: native swallowed to [], kokoro names kept.
    kokoro_only_listing = ["af_heart", "af_bella", "bf_emma"]
    row = _voice_row({"voice": "Voice 1"}, list_voices=lambda: kokoro_only_listing)
    assert row == ("voice", True, "voice listing unavailable")


@pytest.mark.xfail(
    strict=True,
    reason="BUG-11 (new-in-receipts, _voice_row landed at 54cc167): the "
           "spoken doctor verdict claims 'healthy' with a present-but-broken "
           "Kokoro venv, because the one row that sees the breakage is "
           "warn-class and never reaches the spoken failure list; awaiting "
           "owner fix decision -- see HUNT dossier finding 9.",
)
def test_bug11_spoken_verdict_claims_healthy_with_a_present_but_broken_kokoro_venv():
    """BUG-11 (CONFIRMED via DOWNGRADED verdict; finding 9, corrected
    severity medium, corrected from high).

    mechanism: src/sonari/cli/doctor.py:132-166 _voice_row() asks only
    `voice in list_voices()`. platform/macos/tts.py:197-212 composes that
    list off `kokoro.is_installed() or kokoro_provision.neural_enabled()`,
    and kokoro_provision.py:21-23 neural_enabled() is nothing but
    `os.path.exists(kokoro_venv_python())` -- the venv's PRESENCE, not its
    HEALTH (a separate predicate, neural_healthy(),
    kokoro_provision.py:96-103). So a venv whose python exists but cannot
    import kokoro still contributes all 28 Kokoro names, the configured
    Kokoro voice reads "installed", and the voice row is green -- while
    platform/macos/tts.py:184 kokoro.require_installed() raises
    RuntimeError on every single utterance. The one row that DOES see the
    breakage, "neural voices", is warn-class (cli/checkmeta.py:29 _WARN) and
    cli/verdict.py:22 filters warn rows out of the spoken failure list
    entirely -- so the by-ear owner hears only "healthy".

    ratified basis: spec docs/superpowers/specs/2026-08-28-receipts-design.md
    Sec 6.3, verbatim: "a config[\"voice\"] naming a voice that is gone (an
    OS update, a broken Kokoro venv) makes `say` exit non-zero on every
    utterance: total silence with a green doctor." The voice row was built
    to close exactly this case.
    """
    # venv present -> list_voices() contributes the Kokoro names regardless
    # of whether the venv is actually healthy (neural_enabled(), not
    # neural_healthy()).
    kokoro_listing = ["af_heart", "af_bella", "bf_emma"]
    voice_row = _voice_row({"voice": "af_heart"}, list_voices=lambda: kokoro_listing)
    neural_row = ("neural voices", False,
                  "venv present but Kokoro import failed - re-run: sonari voices install")

    spoken = verdict([voice_row, neural_row])
    # RATIFIED: doctor must not certify health while the daemon will raise
    # on every single utterance.
    assert "unhealthy" in spoken.lower()


@pytest.mark.xfail(
    strict=True,
    reason="BUG-10 (pre-existing at 073b82b, per the hunter's own base-tree "
           "control run, DOWNGRADED verdict): the hooks-installed row checks "
           "only the plugin's OWN source tree, never whether Claude Code can "
           "fire a hook at all -- doctor still speaks 'healthy' under total "
           "hook silence; awaiting owner fix decision -- see HUNT dossier "
           "finding 8.",
)
def test_bug10_doctor_speaks_healthy_when_claude_code_can_never_fire_a_hook(tmp_path):
    """BUG-10 (CONFIRMED via DOWNGRADED verdict; finding 8, corrected
    severity medium, corrected from high -- the finder's "structurally
    unfailable" framing overclaimed (the row IS reachable red for other
    causes -- a missing hooks/ dir, the LaunchAgent's APP_DIR-only copy) and
    is NOT what this pins; what survives is that the row observes nothing
    about Claude Code's own state, so it cannot be the row that catches the
    single most probable cause of total silence.

    mechanism: src/sonari/platform/macos/supervisor.py:320-325
    hooks_doctor_row() is `os.path.exists(repo_root()/hooks/hooks.json)` --
    a fact about the PLUGIN'S OWN SOURCE TREE. It reads nothing about
    whether Claude Code has the plugin enabled or will ever fire a hook into
    Sonari: no `.claude`, `settings.json`, `CLAUDE_PLUGIN_ROOT`, or
    plugin-enablement state is ever consulted on doctor's path. With hooks
    not firing, every session stays silent forever while the daemon still
    PINGs -- and _cmd_doctor still speaks "Sonari is healthy."

    ratified basis: receipts design Sec 6.3 retired the OLD "enhanced voice"
    row for being structurally unfailable, on exactly this ground (green
    under every condition doctor can reach); and the product definition
    ("it tells me what happened and what needs me") makes hooks-not-firing
    the single most probable cause of total silence -- on the row literally
    named for it.
    """
    from sonari import cli
    from sonari.platform.macos.supervisor import MacSupervisorBackend
    from sonari import paths, install_record
    from tests._fakeplatform import fake_platform, FakeSupervisor, FakeHotkey, FakeRaiseBackend

    # This process's HOME is conftest's sacrificial redirect: no real
    # ~/.claude, no real ~/.claude.json -- Claude Code is not configured at
    # all here, so no plugin is enabled and no hook could ever fire.
    home = Path(os.environ["HOME"])
    assert not (home / ".claude").exists()
    assert not (home / ".claude.json").exists()

    # Stub only the ONE unrelated row that would otherwise fail in a bare
    # test tree for a reason that has nothing to do with hooks: point
    # app_path at this checkout's own src/ (which genuinely holds
    # sonari/__init__.py).
    install_record.write_install_record(
        python=sys.executable, python_version="3.12",
        plugin_root=str(paths.SONARI_DIR), app_path=REPO_SRC,
        plugin_version="0.11.1")

    # The ONE row under test is the REAL implementation (never faked); every
    # other platform-dependent row is faked green so the only thing that can
    # make the verdict unhealthy is the row genuinely under test.
    real_hooks_row = MacSupervisorBackend().hooks_doctor_row
    sup = FakeSupervisor(rows=[])
    sup.hooks_doctor_row = real_hooks_row
    sup.reachability_row = lambda: ("reachability", True, "ok")
    sup.daemon_is_launchd_job = lambda: True
    pb = fake_platform(supervisor=sup, hotkey=FakeHotkey(), raise_backend=FakeRaiseBackend())

    def fake_send(m, expect_reply=True):
        if m.get("type") == MsgType.PING:
            return {"ok": True}
        return {"sessions": [], "current_item": False, "keepalive": "idle"}

    with mock.patch.object(cli, "_platform", lambda: pb), \
         mock.patch("sonari.client.send", side_effect=fake_send):
        rows = cli.doctor.doctor()

    by_name = {n: (ok, d) for n, ok, d in rows}
    assert by_name["hooks installed"][0] is True     # the row genuinely renders green here
    spoken = verdict(rows)

    # RATIFIED: doctor's one spoken diagnosis must not certify health while
    # nothing on its path has ever observed whether Claude Code can fire a
    # hook at all.
    assert not spoken.startswith("Sonari is healthy")
