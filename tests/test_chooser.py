"""The session chooser (spec 2026-07-14 §3): browse previews that move NOTHING,
commit once. Includes the coverage MIGRATED from the deleted CYCLE_SESSION ring:
W1 dead-tty filtering, sp3.2 eviction filtering, muted-stays-browsable (Fork 2),
and the muted-commit keep-go landing."""
import sonari.ttyutil as ttyutil
from sonari.protocol import MsgType
from sonari.queue import SpeechItem
from sonari.sessions import Identity
from sonari.daemon.features import chooser, teaching
from tests.daemon_helpers import make_daemon
from tests.test_daemon_focus_follow import RecordingRaiseService


def _msg(t, session, **kw):
    from sonari.protocol import PROTOCOL_VERSION
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


def _liveness(monkeypatch, dead):
    """Fake tty_alive: empty tty -> live (fail-open); else live iff not in `dead`."""
    monkeypatch.setattr(ttyutil, "tty_alive",
                        lambda tty: True if not tty else tty not in dead)


def _ident(sessions, sid, tty):
    sessions.set_identity(sid, Identity(term_program="Apple_Terminal", tty=tty))


def _step(daemon, direction="next"):
    daemon.handle_message(_msg(MsgType.CHOOSER_STEP, "", direction=direction))


# --- open-on-first-step: the FIRST step lands on index 1 (tap-release = ⌘Tab toggle) ---
def test_first_step_opens_and_previews_index_one():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("A", cwd="/x/alpha")
    sessions.register("B", cwd="/x/bravo")
    _step(daemon)
    assert daemon._chooser is not None and daemon._chooser.index == 1
    daemon._speak_loop_once()
    assert speaker.spoken == ["2, bravo."]        # number + folder, nothing else moved
    assert sessions.foreground() == "A"           # previews move NOTHING
    assert sessions.speaker() == "A"


def test_snapshot_order_is_current_then_mru_then_registration_order():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    sessions.register("C", cwd="/x/C")
    sessions.register("D", cwd="/x/D")
    sessions.focus("C")                            # deliberate visit -> MRU
    sessions.focus("A")                            # back to A (current)
    _step(daemon)
    assert daemon._chooser.candidates == ["A", "C", "B", "D"]
    assert daemon._chooser.origin == "A"


def test_snapshot_anchor_is_workspace_not_the_diverged_speaker():
    # MIGRATED from test_sp3_cycle.test_cycle_anchor_is_workspace_not_speaker:
    # keep-going drift never re-anchors browsing.
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    sessions.register("C", cwd="/x/C")
    sessions.set_speaker("C")                      # voice drifted to C; workspace=A
    _step(daemon)
    assert daemon._chooser.origin == "A"           # anchored on the workspace
    assert daemon._chooser.candidates[0] == "A"


def test_step_wraps_past_the_end_back_to_current():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("A", cwd="/x/alpha")
    sessions.register("B", cwd="/x/bravo")
    _step(daemon); _step(daemon)                   # A(0) -> B(1) -> wrap -> A(0)
    assert daemon._chooser.index == 0
    daemon._speak_loop_once()
    assert speaker.spoken[-1] == "1, alpha, current."


def test_step_prev_walks_backward():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    sessions.register("C", cwd="/x/C")
    _step(daemon, "prev")                          # -1 from 0 wraps to the last
    assert daemon._chooser.index == 2


def test_each_step_swaps_the_previous_queued_preview():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/bravo")
    sessions.register("C", cwd="/x/charlie")
    _step(daemon); _step(daemon)                   # B then C, no loop turn between
    # Task 11: the first step also fires the one-shot chooser hint (untouched by
    # preview swapping, unlike the preview items themselves) -- filter it out,
    # it is orthogonal to what this test proves.
    texts = [it.text for it in daemon._stream("A").queue._items
             if it.text != teaching.HINTS["chooser"]]
    assert texts == ["3, charlie."]                # B's preview swapped out, not stacked
    assert speaker.cancels >= 2                    # each preview barge-ins the last


def test_preview_flags_are_the_w_cue_flags():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/bravo")
    _step(daemon)
    item = daemon._stream("A").queue._items[0]
    assert item.mute_exempt and item.pause_exempt  # speakable under mute/hold
    assert item.audio_path is None                 # v1 previews are plain speech (D3)


def test_muted_session_stays_browsable_with_muted_suffix():
    # MIGRATED Fork-2 coverage: filter is is_live ONLY, never st.stopped.
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/bravo")
    daemon._stream("B").stopped = True
    _step(daemon)
    daemon._speak_loop_once()
    assert speaker.spoken == ["2, bravo, muted."]


def test_dead_tty_phantom_filtered_from_candidates(monkeypatch):
    # MIGRATED W1 coverage (test_sp3fix_ring pattern).
    _liveness(monkeypatch, dead={"/dev/ttysB"})
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    _ident(sessions, "A", "/dev/ttysA")
    sessions.register("B", cwd="/x/B"); _ident(sessions, "B", "/dev/ttysB")   # phantom
    sessions.register("C", cwd="/x/C"); _ident(sessions, "C", "/dev/ttysC")
    _step(daemon)
    assert daemon._chooser.candidates == ["A", "C"]   # phantom B can never land


def test_evicted_session_filtered_from_candidates(monkeypatch):
    # MIGRATED sp3.2 eviction coverage (test_identity_eviction pattern).
    _liveness(monkeypatch, dead=set())             # the node exists (recycled)
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    _ident(sessions, "A", "/dev/ttysA")
    sessions.register("stale", cwd="/x/stale"); _ident(sessions, "stale", "/dev/ttysT")
    sessions.register("fresh", cwd="/x/fresh"); _ident(sessions, "fresh", "/dev/ttysT")
    _step(daemon)
    assert daemon._chooser.candidates == ["A", "fresh"]


def test_empty_live_roster_errors_and_does_not_open():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground=None)
    _step(daemon)
    assert speaker.earcons == ["error"]
    assert daemon._chooser is None


def test_single_live_candidate_previews_current():
    # MIGRATED from test_cycle_with_fewer_than_two_sessions / one-live-one-phantom:
    # not an error tone anymore — a degenerate browse with honest spoken feedback (D6).
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("A", cwd="/x/alpha")
    _step(daemon)
    daemon._speak_loop_once()
    assert speaker.spoken == ["1, alpha, current."]
    daemon.handle_message(_msg(MsgType.CHOOSER_COMMIT, ""))
    assert sessions.foreground() == "A"            # no-op landing


# --- commit: the old cycle-landing semantics verbatim ---
def test_commit_lands_focus_flowing_cue_and_raise(monkeypatch):
    monkeypatch.setattr(ttyutil, "tty_alive", lambda tty: True)
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    rs = RecordingRaiseService(will=True)
    daemon.raise_service = rs
    sessions.register("B", cwd="/x/bravo")
    sessions.set_identity("B", Identity(term_program="Apple_Terminal", tty="/dev/ttysB"))
    daemon.voice_state = "quiet-hold"
    _step(daemon)
    assert rs.attempts == []                       # previews NEVER raise
    daemon.handle_message(_msg(MsgType.CHOOSER_COMMIT, ""))
    assert sessions.foreground() == "B"            # focus(target): workspace + voice
    assert daemon.voice_state == "flowing"         # deliberate re-engage
    assert len(rs.attempts) == 1                   # landing raises (cycle parity)
    ident, gen = rs.attempts[0]
    assert ident.tty == "/dev/ttysB" and gen >= 1
    daemon._speak_loop_once()
    assert speaker.spoken[-1] == "bravo."          # the landing cue, names_session
    assert daemon._chooser is None


def test_commit_cue_is_at_front_names_session_mute_exempt():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/bravo")
    daemon._enqueue("B", "prose", "b backlog", False)
    _step(daemon)
    daemon.handle_message(_msg(MsgType.CHOOSER_COMMIT, ""))
    head = daemon._stream("B").queue._items[0]
    assert head.text == "bravo." and head.names_session and head.mute_exempt


def test_commit_onto_muted_keeps_going_to_active():
    # MIGRATED from test_sp3_cycle.test_cycle_onto_muted_keeps_going_to_active.
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="B")
    sessions.register("A", cwd="/x/A")
    sessions.register("C", cwd="/x/C")
    daemon._stream("A").stopped = True             # A muted
    daemon._enqueue("C", "prose", "c active", False)
    _step(daemon)                                  # B(0) -> A(1), muted
    daemon.handle_message(_msg(MsgType.CHOOSER_COMMIT, ""))
    assert sessions.workspace() == "A"             # workspace landed on the mute
    assert sessions.speaker() is None              # voice released (Fork 2 keep-go)
    assert daemon.voice_state == "flowing"
    assert daemon._stream("A").stopped is True     # stays muted (R7)
    daemon._speak_loop_once()                      # keep-going voices an ACTIVE session
    assert sessions.speaker() == "C"
    assert any(s and "c active" in s for s in speaker.spoken)


def test_commit_onto_muted_no_active_reports_via_where_am_i():
    # MIGRATED from test_sp3_cycle.test_cycle_onto_muted_no_active_reports_via_where_am_i.
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="B")
    sessions.register("A", cwd="/x/A")
    daemon._stream("A").stopped = True
    _step(daemon)
    daemon.handle_message(_msg(MsgType.CHOOSER_COMMIT, ""))   # -> A, muted
    assert sessions.workspace() == "A" and sessions.speaker() is None
    daemon.handle_message(_msg(MsgType.WHERE_AM_I, ""))
    assert speaker.earcons[-1] == "error"          # muted workspace: honest error tone


def test_commit_updates_mru():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    _step(daemon)
    daemon.handle_message(_msg(MsgType.CHOOSER_COMMIT, ""))
    assert sessions.mru()[0] == "B"                # focus() touched recency


# --- commit onto a candidate that died mid-browse (branch-review fix) ---
# The snapshot is is_live-filtered only at OPEN; a candidate can die WHILE the
# user is still browsing it (before release/digit-commit). Landing there must
# never call sessions.focus() -- focus()'s _record() silently RE-REGISTERS a
# dead session id (a phantom in the roster, the workspace pinned to a closed
# terminal, the captured item dropped). Two death shapes, both guarded:
# SESSION_END unregisters (out of session_ids(), but is_live() fail-opens on
# the now-missing identity); a dead tty stays registered (still in
# session_ids()) but is_live() catches it via ttyutil.tty_alive.
def test_commit_onto_session_end_mid_browse_errors_and_does_not_reregister():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("A", cwd="/x/alpha")
    sessions.register("B", cwd="/x/bravo")
    daemon._current_item = SpeechItem(id=910, session="A", kind="prose",
                                      text="mid sentence", is_decision=False)
    _step(daemon)                                  # A(0) -> B(1): captures + cuts "mid sentence"
    daemon.handle_message(_msg(MsgType.SESSION_END, "B"))   # B's terminal closes mid-browse
    daemon.handle_message(_msg(MsgType.CHOOSER_COMMIT, ""))
    assert speaker.earcons == ["error"]            # audible failed landing, never silent
    assert sessions.foreground() == "A"            # workspace/foreground unchanged
    assert "B" not in sessions.session_ids()       # NOT phantom-re-registered by focus()
    assert daemon._chooser is None                 # chooser cleared
    head = daemon._stream("A").queue._items[0]
    assert head.text == "mid sentence"             # captured item resumed (re-enqueued)


def test_commit_onto_dead_tty_mid_browse_errors_and_does_not_reregister(monkeypatch):
    dead = set()
    _liveness(monkeypatch, dead=dead)
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    _ident(sessions, "A", "/dev/ttysA")
    sessions.register("B", cwd="/x/bravo"); _ident(sessions, "B", "/dev/ttysB")
    daemon._current_item = SpeechItem(id=911, session="A", kind="prose",
                                      text="mid sentence two", is_decision=False)
    _step(daemon)                                  # A(0) -> B(1): live at open, captures + cuts
    dead.add("/dev/ttysB")                          # B's terminal dies mid-browse (crash, not SESSION_END)
    daemon.handle_message(_msg(MsgType.CHOOSER_COMMIT, ""))
    assert speaker.earcons == ["error"]            # audible failed landing, never silent
    assert sessions.foreground() == "A"            # workspace/foreground unchanged
    assert "B" in sessions.session_ids()           # still registered (dead-tty != unregistered)
    assert not sessions.is_live("B")               # ...but is_live() catches it
    assert daemon._chooser is None                 # chooser cleared
    head = daemon._stream("A").queue._items[0]
    assert head.text == "mid sentence two"         # captured item resumed (re-enqueued)


def test_commit_onto_dead_tty_speaks_the_closed_word(monkeypatch):
    # D7a word channel (spec §4b/§5 slot 3): the mid-browse-death error tone
    # gains its paired word, routed to speaker-or-workspace (never the dead
    # target's own stream).
    dead = set()
    _liveness(monkeypatch, dead=dead)
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    _ident(sessions, "A", "/dev/ttysA")
    sessions.register("B", cwd="/x/bravo"); _ident(sessions, "B", "/dev/ttysB")
    daemon._current_item = SpeechItem(id=913, session="A", kind="prose",
                                      text="mid sentence three", is_decision=False)
    _step(daemon)                                  # A(0) -> B(1): live at open, captures + cuts
    dead.add("/dev/ttysB")                          # B's terminal dies mid-browse (crash, not SESSION_END)
    daemon.handle_message(_msg(MsgType.CHOOSER_COMMIT, ""))
    assert speaker.earcons == ["error"]
    texts = [it.text for it in daemon._stream("A").queue._items]
    assert "That session closed." in texts          # word lands in speaker()-or-workspace() stream


def test_preview_no_none_for_candidate_that_died_mid_browse():
    # MINOR A: sessions.number() returns None post-unregister, so the naive
    # "{0}, {1}".format(number, folder) speaks the literal word "None". Once a
    # candidate dies mid-browse, stepping back onto it must speak the
    # folder-fallback WITHOUT a number prefix, never "None, ...".
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("A", cwd="/x/alpha")
    sessions.register("B", cwd="/x/bravo")
    _step(daemon)                                  # A(0) -> B(1): preview "2, bravo."
    daemon.handle_message(_msg(MsgType.SESSION_END, "B"))   # B dies mid-browse (unregistered)
    _step(daemon, "prev")                           # -> A(0), preview "1, alpha, current."
    _step(daemon)                                    # -> B(1) again: re-delivers for the dead candidate
    daemon._speak_loop_once()
    assert speaker.spoken[-1] == "another session."
    assert "None" not in speaker.spoken[-1]


# --- the no-op landing + capture/resume ---
def test_commit_to_current_is_silent_noop_and_resumes_captured():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("A", cwd="/x/alpha")
    sessions.register("B", cwd="/x/bravo")
    daemon._current_item = SpeechItem(id=901, session="A", kind="prose",
                                      text="mid sentence", is_decision=False)
    _step(daemon); _step(daemon)                   # around and back to index 0
    assert daemon._chooser.index == 0
    cancels_before = speaker.cancels
    daemon.handle_message(_msg(MsgType.CHOOSER_COMMIT, ""))
    assert speaker.cancels == cancels_before       # no cut at commit
    assert daemon._chooser is None
    head = daemon._stream("A").queue._items[0]
    assert head.text == "mid sentence"             # interrupted speech resumes
    assert not any(it.names_session for it in daemon._stream("A").queue._items)  # no cue


def test_cancel_restores_captured_item_and_moves_nothing():
    class _Entry:
        heard = False
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    entry = _Entry()
    cur = SpeechItem(id=902, session="A", kind="prose",
                     text="cut me", is_decision=False)
    daemon._current_item = cur
    daemon._pending_heard[902] = entry
    _step(daemon)                                  # open captures + cuts
    assert speaker.cancels >= 1
    st = daemon._chooser
    assert st.captured is cur and st.captured_entry is entry
    daemon.handle_message(_msg(MsgType.CHOOSER_CANCEL, ""))
    assert daemon._chooser is None
    head = daemon._stream("A").queue._items[0]
    assert head.text == "cut me"                   # restored at the front
    assert daemon._pending_heard[head.id] is entry # heard-marker carried over
    assert sessions.foreground() == "A"            # nothing moved
    assert not any("2," in (it.text or "") for it in daemon._stream("A").queue._items)


def test_commit_to_other_drops_the_captured_item():
    # Cycle-cut parity: landing elsewhere cuts; the interrupted item does NOT resume.
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    daemon._current_item = SpeechItem(id=903, session="A", kind="prose",
                                      text="cut for good", is_decision=False)
    _step(daemon)
    daemon.handle_message(_msg(MsgType.CHOOSER_COMMIT, ""))
    assert sessions.foreground() == "B"
    assert not any(it.text == "cut for good"
                   for it in daemon._stream("A").queue._items)


# --- digits ---
def test_digit_instant_commits_to_that_number():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/bravo")
    sessions.register("C", cwd="/x/charlie")
    _step(daemon)
    daemon.handle_message(_msg(MsgType.CHOOSER_DIGIT, "", digit=3))
    assert sessions.foreground() == "C"            # absolute teleport
    assert daemon._chooser is None


def test_digit_with_no_open_state_is_a_noop():
    # Branch-review fix: a digit can only ever legitimately arrive while the
    # chord is held (hotkeyd registers ⌃⌘1-9 ONLY during an open chooser mode,
    # spec §5) -- CHOOSER_DIGIT with no host._chooser is therefore a RACE/STRAY
    # message (e.g. arriving over hotkeyd's separate digit socket AFTER a
    # CHOOSER_COMMIT already landed on the modifier-release socket). Reopening
    # here would teleport the workspace on a message the user never intended
    # as a fresh gesture. FIX: no-op -- no open, no earcon, nothing spoken.
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/bravo")
    daemon.handle_message(_msg(MsgType.CHOOSER_DIGIT, "", digit=2))
    assert sessions.foreground() == "A"            # unchanged -- no teleport
    assert daemon._chooser is None                 # no state opened
    assert speaker.spoken == []                    # nothing spoken
    assert speaker.earcons == []                   # no earcon either


def test_unknown_digit_errors_and_stays_open():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/bravo")
    _step(daemon)
    daemon.handle_message(_msg(MsgType.CHOOSER_DIGIT, "", digit=7))
    assert speaker.earcons[-1] == "error"
    assert daemon._chooser is not None             # browse continues (spec §3)
    daemon.handle_message(_msg(MsgType.CHOOSER_COMMIT, ""))
    assert sessions.foreground() == "B"            # the held candidate still commits


def test_unknown_digit_does_not_speak_the_closed_word():
    # Fix round (independent review): a digit that never maps to ANY session
    # (typo/out-of-range) is not a death -- the D7a word ("That session
    # closed.") must attach ONLY to a confirmed mid-browse death (spec §4b:
    # "the mid-browse-death error tone gains its D7a word"), never to the
    # plain wrong-digit case. Tone-only here.
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/bravo")
    _step(daemon)
    daemon.handle_message(_msg(MsgType.CHOOSER_DIGIT, "", digit=7))
    assert speaker.earcons[-1] == "error"
    texts = [it.text for it in daemon._stream("A").queue._items]
    assert "That session closed." not in texts     # never mapped -> no death word


def test_digit_to_dead_session_errors(monkeypatch):
    _liveness(monkeypatch, dead={"/dev/ttysB"})
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    _ident(sessions, "A", "/dev/ttysA")
    sessions.register("B", cwd="/x/B"); _ident(sessions, "B", "/dev/ttysB")
    _step(daemon)
    daemon.handle_message(_msg(MsgType.CHOOSER_DIGIT, "", digit=2))
    assert speaker.earcons[-1] == "error"          # W1 also guards the teleport
    assert sessions.foreground() == "A"


def test_digit_to_dead_session_speaks_the_closed_word(monkeypatch):
    # D7a word channel (spec §4b/§5 slot 3): same word as the commit path
    # (identity across both death shapes/sites is the point).
    _liveness(monkeypatch, dead={"/dev/ttysB"})
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    _ident(sessions, "A", "/dev/ttysA")
    sessions.register("B", cwd="/x/B"); _ident(sessions, "B", "/dev/ttysB")
    _step(daemon)
    daemon.handle_message(_msg(MsgType.CHOOSER_DIGIT, "", digit=2))
    assert speaker.earcons[-1] == "error"
    texts = [it.text for it in daemon._stream("A").queue._items]
    assert "That session closed." in texts          # word lands in speaker()-or-workspace() stream


def test_digit_onto_pending_session_speaks_only_the_error_tone(monkeypatch):
    # WB-C3/R-2: session_for_number() looks over the WHOLE roster, and a
    # restored session keeps its persisted number (that persistence is the
    # point) -- so an ordinary morning-after digit press can resolve to a
    # PENDING target, not just live/dead. The old `not is_live` guard treated
    # pending the same as dead and spoke CLOSED_WORD -- the dead tier's word --
    # about a session that might still be alive, breaking §5's one-word-per-tier
    # law. §4b mints no chooser-side pending string and pending is never
    # dialable, so the honest output is the bare tone, same as an unknown digit.
    # Probe E recipe: restore two numbered sessions, re-prove one via PROSE
    # (R1 clears its quarantine at dispatch), dial the number of the one
    # that's still silent.
    _liveness(monkeypatch, dead=set())
    daemon, queue, speaker, sessions, _ = make_daemon(foreground=None)
    sessions.load_state({"Y": {"folder": "yankee", "number": 1},
                         "Z": {"folder": "zulu", "number": 3}})
    daemon.handle_message(_msg(MsgType.PROSE, "Y", index=0, final=True, delta="hi"))
    assert sessions.liveness("Y") == "live"
    assert sessions.liveness("Z") == "pending"
    sessions.set_foreground("Y")                     # you're sitting in Y's terminal
    _step(daemon)                                   # opens: only Y (is_live) is a candidate
    daemon.handle_message(_msg(MsgType.CHOOSER_DIGIT, "", digit=3))   # Z's persisted number
    assert speaker.earcons[-1] == "error"
    assert daemon._chooser is not None               # browse continues (§3), same as unknown digit
    assert sessions.liveness("Z") == "pending"        # untouched by the press
    texts = [it.text for it in daemon._stream("Y").queue._items]
    assert "That session closed." not in texts        # no D7a word for a not-yet-dead session


def test_digit_onto_session_end_mid_browse_speaks_the_closed_word():
    # Fix-round follow-up (independent review): unregister frees the digit's
    # number, so a fresh session_for_number() lookup at press time can no
    # longer distinguish "genuinely out-of-range" from "this exact session
    # just closed" -- the same digit press that closed test_unknown_digit_...
    # for the typo shape must still speak the word for the SESSION_END shape
    # (mirrors _commit's OPEN-time-snapshot approach, spec §4b: both death
    # shapes speak the same word on the digit path too).
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("A", cwd="/x/alpha")
    sessions.register("B", cwd="/x/bravo")
    _step(daemon)                                   # A(0) -> B(1): live at open, holds number 2
    daemon.handle_message(_msg(MsgType.SESSION_END, "B"))   # B's own session closes mid-browse
    daemon.handle_message(_msg(MsgType.CHOOSER_DIGIT, "", digit=2))   # B's own number, pressed
    assert speaker.earcons[-1] == "error"
    assert "B" not in sessions.session_ids()        # not phantom-re-registered
    texts = [it.text for it in daemon._stream("A").queue._items]
    assert "That session closed." in texts          # word lands in speaker()-or-workspace() stream


def test_digit_of_current_session_is_the_noop_landing():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    _step(daemon)
    daemon.handle_message(_msg(MsgType.CHOOSER_DIGIT, "", digit=1))
    assert daemon._chooser is None
    assert sessions.foreground() == "A"
    assert not any(it.names_session for it in daemon._stream("A").queue._items)


# --- stale + orphan messages ---
def test_stale_open_is_implicitly_cancelled_then_fresh(monkeypatch):
    t = {"v": 0.0}
    monkeypatch.setattr(chooser, "_now", lambda: t["v"])
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    daemon._current_item = SpeechItem(id=904, session="A", kind="prose",
                                      text="stale capture", is_decision=False)
    _step(daemon)                                  # open at t=0
    t["v"] = 31.0                                  # > STALE_S
    _step(daemon)                                  # implicit cancel + fresh open
    assert daemon._chooser.opened_at == 31.0
    assert daemon._chooser.captured is None        # fresh open had nothing in flight
    assert any(it.text == "stale capture"
               for it in daemon._stream("A").queue._items)   # old capture restored


def test_commit_without_open_is_a_noop():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    daemon.handle_message(_msg(MsgType.CHOOSER_COMMIT, ""))
    assert sessions.foreground() == "A" and speaker.cancels == 0


def test_cancel_without_open_is_a_noop():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    daemon.handle_message(_msg(MsgType.CHOOSER_CANCEL, ""))
    assert sessions.foreground() == "A" and speaker.cancels == 0


# --- preview routing when the speaker is None ---
def test_preview_falls_back_to_playable_workspace_when_speaker_none():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/bravo")
    sessions.set_speaker(None)                     # voice released (e.g. muted landing)
    _step(daemon)
    assert any(it.text == "2, bravo." for it in daemon._stream("A").queue._items)


def test_preview_errors_when_neither_speaker_nor_playable_workspace():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/bravo")
    daemon._stream("A").stopped = True             # workspace muted
    sessions.set_speaker(None)
    _step(daemon)
    assert speaker.earcons[-1] == "error"          # honest: nowhere voiceable
    assert daemon._chooser is not None             # commit still possible (blind)
