"""Audit pins: three ways the roster (the set of sessions everything else
consults) diverges from the truth and never self-heals.

See /Users/Nima.Hakimi/projects/private/claude-tts/scratchpad/e3-review/test-audit/HUNT-RESULTS.json
findings 11, 12, and 13 for the full adjudication.
"""
import pytest

from tests.daemon_helpers import make_daemon
from sonari.protocol import MsgType, PROTOCOL_VERSION


def msg(t, session, **kw):
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


@pytest.mark.xfail(
    strict=True,
    reason="BUG-8 (pre-existing at 073b82b): a stream that was never "
           "registered on the roster survives a snapshot/restore round trip "
           "forever, and the boot line keeps asserting it is muted with no "
           "name and no key that clears it; awaiting owner fix decision -- "
           "see HUNT dossier finding 12.",
)
def test_bug8_orphan_muted_stream_is_immortal_across_a_restore():
    """BUG-8 (CONFIRMED, finding 12, severity medium).

    mechanism: host.py:1621-1630 _snapshot_state serializes EVERY entry of
    _state._streams that carries a frontier or is stopped -- roster
    membership is never checked. host.py:1640-1714 _restore_state updates
    _state._streams from that same map while restoring the roster from a
    DIFFERENT key (sessions), so a stream with no matching roster entry
    round-trips forever. host.py:1732-1758 _compose_restore_line then walks
    _state._streams and speaks "{folder or 'Another session'} is muted." for
    every stopped one regardless of roster membership -- while control.py's
    _also_clause (the ctrl-cmd-W truth-teller, D3 spec Sec 4a) walks
    sessions.session_ids() (the roster) and can never even see it. The only
    code that clears a stream entry, lifecycle.py:220 (SESSION_END), can only
    come from the vanished session's own hook process, which will never run
    again.

    ratified basis: D3 spec Sec 4a names ctrl-cmd-W's Also-map "the fleet's
    truth-teller" -- a session the boot line asserts is muted must be
    discoverable and clearable through that same truth-teller, not a
    standing false alarm with no remedy. His live state (state.json,
    2026-08-29) already carries two such orphans.
    """
    src, _, _, sessions, _ = make_daemon(foreground=None)
    # An orphan stream: some past content path reached it, but it was NEVER
    # registered on the roster -- exactly the shape a session open before
    # Sonari's daemon started, or a fail-open restore, produces.
    src._stream("orphan-session").stopped = True
    sessions.register("live-session", cwd="/x/board-copilot")

    with src._lock:
        data = src._snapshot_state()
    assert "orphan-session" in data["streams"]
    assert "orphan-session" not in data["sessions"]
    src._store.save(data)

    dst, _, _, dst_sessions, _ = make_daemon(foreground=None)
    dst._restore_state()
    assert "orphan-session" in dst._streams

    roster = set(dst_sessions.session_ids())
    stopped_sids = {sid for sid, st in dst._streams.items() if st.stopped}
    # RATIFIED: anything the boot line can assert is "muted" must be a member
    # of the roster -- nameable, and clearable through ctrl-cmd-W's Also-map.
    assert stopped_sids <= roster


@pytest.mark.xfail(
    strict=True,
    reason="BUG-12 (pre-existing at 073b82b, per the hunter's own base-tree "
           "control run): a content stream the daemon never registered on "
           "the roster is never auto-voiced, even with the voice flowing and "
           "idle; awaiting owner fix decision -- see HUNT dossier finding 11.",
)
def test_bug12_a_content_stream_the_daemon_never_registered_is_never_auto_voiced():
    """BUG-12 (CONFIRMED via DOWNGRADED verdict; finding 11, corrected
    severity medium, corrected from high -- the flush-discard leg the
    original finding also claimed is RATIFIED behaviour (spec: "submitting a
    new prompt clears the queue", and the content is recoverable via
    catch-up) and is deliberately NOT pinned here; only the never-auto-voiced
    leg is a real defect.

    mechanism: host.py:423-434 _stream() mints a SessionStream for ANY
    session id reached on the content path (every _enqueue), but the ONLY
    roster-registration sites in the whole tree are lifecycle.py:81/106/122
    (SET_FOREGROUND/SESSION_START). So a stream can exist with content queued
    while sessions.session_ids() (the roster) never heard of it:
    host.py:164-196 _select_keep_going iterates sessions.session_ids() only,
    so the auto-voice path can never adopt it. This is exactly the shape a
    session lost while the daemon was down (client.py's silent
    DaemonNotRunning swallow) or a fail-open restore (host.py:1640-1727)
    leaves behind.

    ratified basis: the product definition, "it tells me what happened and
    what needs me" -- a completed turn producing silence, with the voice
    otherwise idle and flowing and nothing else to say, is the pure failure
    state.
    """
    daemon, _, speaker, sessions, _ = make_daemon(foreground=None)
    daemon.voice_state = "flowing"

    entry = daemon.history.record("orphan", "prose", "the migration finished")
    daemon.history.end_message("orphan")
    daemon._enqueue("orphan", "prose", "the migration finished", False,
                    entry=entry, forward=True)
    assert "orphan" not in sessions.session_ids()
    assert "orphan" in daemon._streams

    for _ in range(5):
        daemon._speak_loop_once()

    # RATIFIED: a completed turn's content must be heard, not silently
    # dropped because the stream never made it onto the roster.
    assert speaker.spoken == ["the migration finished"]


@pytest.mark.xfail(
    strict=True,
    reason="BUG-13 (pre-existing at 073b82b, per the hunter's own base-tree "
           "control run): a roster entry with no history at all is invisible "
           "to the bounded-staleness sweep and survives forever, contrary to "
           "the sp6 Not-Doing list's own covering claim; awaiting owner fix "
           "decision -- see HUNT dossier finding 13.",
)
def test_bug13_a_historyless_roster_entry_survives_the_bounded_staleness_sweep_forever():
    """BUG-13 (CONFIRMED via DOWNGRADED verdict; finding 13, corrected
    severity low, corrected from medium -- pins ONLY the surviving leg: a
    spoken session number > 9 being unreachable by ctrl-cmd-1-9 is a RATIFIED
    accepted edge (docs/superpowers/specs/2026-07-14-sonari-session-chooser-
    design.md Sec 6, "realistic fleet <= 5") and is deliberately NOT pinned
    here.

    mechanism: host.py:1666-1683's bounded-staleness drop-on-load -- the
    ONLY roster reducer besides unregister() (sessions.py:177, whose one
    caller is SESSION_END, lifecycle.py:196) -- iterates
    `for sid, sd in hist.items()` ONLY, so a roster entry with NO history
    entry is never even considered for staleness, no matter how old the
    saved state is.

    ratified basis: docs/superpowers/specs/2026-08-24-sonari-sp6-persistence-
    design.md Sec 4.4 line 59 names this exact hazard ("no liveness reaper,
    so ghosts accumulate ... holding the low spoken numbers"), and its
    Not-Doing list (line 153) ratifies shipping without a dedicated reaper on
    the EXPRESS claim that "the provisional flag + bounded-staleness
    drop-on-load cover roster growth" -- a claim this exact shape falsifies.
    A history-less roster entry (a session that registered and never spoke)
    must not be immortal; it is exactly the ghost the bounded-staleness drop
    was meant to cover.
    """
    from sonari.daemon.persistence import STATE_VERSION

    src, _, _, _, _ = make_daemon(foreground=None)
    data = {
        "version": STATE_VERSION,
        "saved_wall": 100000000.0,   # arbitrarily far past any real save
        "next_id": 1,
        "sessions": {
            "debris-probe": {"folder": "ghost", "number": 1},   # no history at all
        },
        "streams": {},
        "history": {},               # debris-probe never appears here
    }
    src._store.save(data)
    src._restore_state()

    # RATIFIED: a history-less roster ghost must be reachable by the SAME
    # bounded-staleness sweep that reaps every other stale entry.
    assert "debris-probe" not in src.sessions.session_ids()
