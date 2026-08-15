"""D3 fix-waves A, D, and E: who may voice a DEAD session's stream, and how much.

§4d keeps the voice off a dead session's backlog AUTOMATICALLY — keep-going
never adopts one. Three rules compose that, and the whole-branch review plus
its re-review found the seams between them broken in both directions:

1. Automatic flow never adopts dead (T9, `_select_keep_going` — unchanged).
2. A DELIBERATE press may adopt a dead stream and sanctions reading it. Idle
   ⌃⌘W and catch-up delivery (§4f) compose their answer INTO the dead
   destination's own stream; with keep-going refusing it and speaker() None,
   the correct answer was composed and never voiced (WB-C1 CRITICAL, WB-C2).
   Such a press now takes the voice itself and marks the stream consciously
   re-opened.
3. A dead speaker WITHOUT that sanction RELEASES the voice. §4d was
   selection-time only, so a session that died MID-DRAIN was auto-voiced to
   the end of its pile (R-1). Skipping the pop wedges the voice forever
   (probe G); releasing it — `set_speaker(None)`, queue intact — lets the
   bootstrap-from-None path reach a live session, the idiom chooser.py:214
   already uses.

Rule 2's sanction carries a GRAIN, because "may read it" and "how much of it"
are different questions (fix-wave D):

- WHOLE stream, backlog included (the wrinkle, pinned below) when the press is
  a read OF that session: idle ⌃⌘W, ⌃⌘W on a dead speaker, catch-up, and
  navigation — whose seek-and-play clears the queue first, so the whole stream
  IS the requested content (RR-1: rule 3 had silenced it outright).
- ONE front item when the press merely LANDED an answer there because its
  destination falls back to workspace(): the settings readbacks, jump-waiting's
  empty case, the repeat/skip/jump-decision fallbacks, the chooser preview
  (RR-2), the answer-permission approve/deny confirm, and ⌃⌘S-start's
  "Resumed." on a dead MUTED workspace (RR-3/RR-4, fix-wave E). Owner-ruled
  2026-08-15 (wave1-T2), closing the family: the three remaining siblings —
  learn mode and query actions (both `workspace()`-targeted), and re-read
  options (`foreground()`-targeted instead — the different accessor; both of
  its enqueues, the cached-options branch and the "No options right now."
  fallback, are sanctioned) — are wired below too, same grain. A rate nudge
  is not a request to be read a closed session's pile, and neither is
  un-muting one.

Wave1-T4 item E, same day: T2's implementer found, but deliberately left out
of scope, one more single-item site one level down — the learn-mode idle
auto-exit (`_learn_mode_expired`) enqueues its own LEARN_OFF, separately from
the manual toggle T2 just wired, and had the identical `workspace()`-falls-
back gap. Wired below too, same grain, same day.
"""
import threading

from sonari import ttyutil
from sonari.protocol import PROTOCOL_VERSION
from sonari.sessions import Identity
from tests.daemon_helpers import make_daemon


def _liveness(monkeypatch, dead):
    """Fake tty_alive: empty tty -> live (fail-open); else live iff not in `dead`
    (the tests/test_chooser.py idiom). `dead` is mutated in place by the
    mid-drain tests, so a terminal can close between two speak-loop turns."""
    monkeypatch.setattr(ttyutil, "tty_alive",
                        lambda tty: True if not tty else tty not in dead)


def _ident(sessions, sid, tty):
    sessions.set_identity(sid, Identity(term_program="Apple_Terminal", tty=tty))


def _msg(t, session, **kw):
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


def _drain(daemon, n):
    for _ in range(n):
        daemon._speak_loop_once()


def _fake_learn_timer(monkeypatch):
    """Replace threading.Timer with a no-real-thread fake (test_teaching.py's
    own pattern): .fn() fires the idle callback synchronously, so item E's
    120s idle auto-exit is testable without waiting or leaving a live daemon
    timer running past the test."""
    class _FakeTimer:
        def __init__(self, interval, fn):
            self.interval = interval
            self.fn = fn
            self.daemon = False

        def start(self):
            pass

        def cancel(self):
            pass

    monkeypatch.setattr(threading, "Timer", _FakeTimer)


def _dead_workspace(monkeypatch, folder="web", tty="/dev/ttysW"):
    """A one-session fleet whose workspace terminal is gone and whose voice is
    idle — the exact WB-C1/WB-C2 conjunction (a crashed terminal plus the other
    session ending normally, the morning-after fleet state D3 targets)."""
    dead = {tty}
    _liveness(monkeypatch, dead)
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    sessions.register("ws", cwd="/x/" + folder)
    sessions.set_foreground("ws")
    _ident(sessions, "ws", tty)                  # its captured node no longer exists
    sessions.set_speaker(None)                   # the voice is idle (legitimate post-SP3)
    assert sessions.liveness("ws") == "dead"
    return daemon, speaker, sessions


# --- WB-C1 (CRITICAL): the idle where-am-I readout on a dead workspace ---
def test_idle_whereami_on_a_dead_workspace_is_voiced(monkeypatch):
    """The P2 recipe. The readout composes correctly (Task 5 marks it ", closed")
    and lands in the workspace stream because speaker() is None — but T9 taught
    keep-going to skip dead streams, so it sat there forever: no speech, no
    tone, nothing, from the product's primary status verb."""
    daemon, speaker, sessions = _dead_workspace(monkeypatch)
    daemon.handle_message(_msg("where_am_i", "ws"))
    _drain(daemon, 5)
    assert "Nothing playing. Keyboard: web 1, closed." in speaker.spoken


# --- the sanctioned-drain wrinkle: the deliberate read is the WHOLE stream ---
def test_sanctioned_dead_read_also_drains_that_stream_s_backlog(monkeypatch):
    """Consciously pinned (WB-C1's "wrinkle"): the press re-opened THIS stream on
    purpose, so the backlog already sitting in it is read too — deliberate
    reading of a closed session's stored pile, exactly what §4f sanctions. It
    re-opens nothing for any OTHER dead stream, and nothing automatic."""
    daemon, speaker, sessions = _dead_workspace(monkeypatch)
    daemon._enqueue("ws", "prose", "backlog one", False)
    daemon._enqueue("ws", "prose", "backlog two", False)
    daemon.handle_message(_msg("where_am_i", "ws"))
    _drain(daemon, 6)
    assert any(s and "backlog one" in s for s in speaker.spoken)
    assert any(s and "backlog two" in s for s in speaker.spoken)
    # Byte-exact (the grammar-v2 reason): the mark precedes the Keyboard clause's
    # own content clause, and the backlog it counts is the backlog just drained.
    assert "Nothing playing. Keyboard: web 1, closed, 2 waiting." in speaker.spoken


# --- WB-C2 (MAJOR): catch-up's sanctioned recovery read, same conjunction ---
def test_catchup_on_a_dead_workspace_with_idle_voice_is_voiced(monkeypatch):
    """§4f: "reading a closed session's pile is a legitimate recovery act...
    catch-up still PROCEEDS." It proceeded silently — `_cue_dest` routes both
    the ack and the render into the dead target's own stream when speaker() is
    None. The frontier work happened and the user heard nothing."""
    daemon, speaker, sessions = _dead_workspace(monkeypatch, folder="myrepo",
                                                tty="/dev/ttysM")
    for i in range(2):
        daemon.history.record("ws", "prose", "line {0}.".format(i))
    daemon.handle_message(_msg("catch_up", "ws"))
    _drain(daemon, 8)
    assert "Catching up 2 items in myrepo. That session closed." in speaker.spoken
    # summarizer=None -> straight to the digest floor; the RENDER must be voiced
    # too, not just the ack (it lands on the same stream one tick later).
    assert any(s and s.startswith("Summary unavailable.") for s in speaker.spoken)


# --- the third deliberate site, forced out by rule 3 (see the module docstring) ---
def test_whereami_on_a_dead_speaker_is_voiced(monkeypatch):
    """The readout about a dead VOICE session lands in that session's own stream,
    so rule 3 would hand the voice back and strand it — WB-C1's silence one
    branch over. ⌃⌘W is exactly when the user is asking about a fleet in this
    state, so the press sanctions the read; "playing, closed" is the answer.
    Task 5's pointer-mark tests pin the string; this pins that it is HEARD."""
    _liveness(monkeypatch, dead={"/dev/ttysA"})
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    sessions.register("api", cwd="/x/api")
    sessions.set_foreground("api")
    sessions.set_speaker("api")
    _ident(sessions, "api", "/dev/ttysA")
    daemon.handle_message(_msg("where_am_i", "api"))
    _drain(daemon, 3)
    assert "Voice and keyboard: api 1, playing, closed." in speaker.spoken


def _dying_fleet(monkeypatch):
    """A live session auto-adopted by keep-going, mid-drain, with a live third
    session holding its own backlog — the shape R-1 measured. Returns the set the
    test mutates to close B's terminal between two turns."""
    dead = set()
    _liveness(monkeypatch, dead)
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B"); _ident(sessions, "B", "/dev/ttysB")
    sessions.register("C", cwd="/x/C"); _ident(sessions, "C", "/dev/ttysC")
    for i in range(5):
        daemon._enqueue("B", "prose", "b-item-{0}".format(i), False)
    daemon._enqueue("C", "prose", "c-item", False)      # younger -> B is adopted first
    daemon._speak_loop_once()                            # A idle -> adopt B, speak b-item-0
    assert sessions.speaker() == "B"
    assert any(s and "b-item-0" in s for s in speaker.spoken)
    dead.add("/dev/ttysB")                               # the terminal closes MID-READ
    return daemon, speaker, sessions


# --- R-1 (probe H): §4d was selection-time only; a mid-drain death kept reading ---
def test_speaker_dying_mid_drain_is_silenced_and_its_queue_preserved(monkeypatch):
    """Spec §4d promises "a dead session's backlog is never auto-voiced", but the
    guard only ran at SELECTION: once adopted, the pop branch drained the whole
    pile with no liveness re-check — up to the full backlog_cap of 200 items read
    into an empty room. "I closed the terminal while it was still reading to me"
    is the likelier shape than "it was already closed"."""
    daemon, speaker, sessions = _dying_fleet(monkeypatch)
    _drain(daemon, 6)
    for i in range(1, 5):
        assert not any(s and "b-item-{0}".format(i) in s for s in speaker.spoken)
    # The pile is KEPT, not dropped: it stays discoverable via where-am-I (§4a)
    # and readable via catch-up (§4f) — the release silences, it never destroys.
    assert len(daemon._stream("B").queue) == 4
    assert sessions.speaker() == "C"                     # the live fleet keeps flowing
    assert any(s and "c-item" in s for s in speaker.spoken)


# --- R-1 (probe G): the naive "skip the pop" fix WEDGES the voice; release doesn't ---
def test_release_never_wedges_the_voice_on_a_dead_speaker(monkeypatch):
    """Skipping the pop leaves the dead stream non-empty, so _stream_quiescent stays
    False, so the keep-going gate never opens and the voice is stuck on a dead
    session forever while a live one waits. The voice must leave within one turn."""
    daemon, speaker, sessions = _dying_fleet(monkeypatch)
    daemon._speak_loop_once()                            # exactly ONE turn
    assert sessions.speaker() != "B"
    assert not any(s and "b-item-1" in s for s in speaker.spoken)


# --- composition: sanction beats release while it holds, and only while it holds ---
def test_sanctioned_read_survives_until_its_stream_empties_then_releases(monkeypatch):
    """The two halves meet here. A sanctioned dead read must NOT be cut off
    mid-way by rule 3 (it is the deliberate act rule 3 exists to protect), and
    the sanction must not outlive it — one press re-opens one stream once."""
    daemon, speaker, sessions = _dead_workspace(monkeypatch)
    daemon._enqueue("ws", "prose", "backlog one", False)
    daemon._enqueue("ws", "prose", "backlog two", False)
    daemon.handle_message(_msg("where_am_i", "ws"))
    assert sessions.speaker() == "ws"                    # the press took the idle voice
    for _ in range(3):                                   # backlog one, backlog two, readout
        daemon._speak_loop_once()
        assert sessions.speaker() == "ws", "a sanctioned read was cut off mid-pile"
    daemon._speak_loop_once()                            # the tick after it runs dry
    assert sessions.speaker() is None
    assert daemon._deliberate_dead_read is None          # one-shot: never a standing licence


def _seed_transcript(daemon, session="ws"):
    """Three navigable messages in *session*'s stored transcript — what ⌃⌘← is
    for on a closed session: "read me what that session said"."""
    h = daemon.history
    h.record(session, "prose", "hist 0"); h.end_message(session)
    h.record(session, "prose", "hist 1"); h.end_message(session)
    h.record(session, "prose", "hist 2")


# --- RR-1 (CRITICAL): the release silenced NAVIGATION on a dead workspace ---
def test_nav_on_a_dead_workspace_is_voiced(monkeypatch):
    """Nav is the fourth deliberate site, and the only press that TAKES the voice
    itself (crossed nav calls sessions.focus() on the unguarded workspace) rather
    than relying on keep-going adoption. Pre-release it delivered BECAUSE the pop
    had no liveness check; the release closes that gap, so with no sanction here
    the read composes, the voice lands on the dead session, and the next pop
    hands it back — everything strands. §4g rules nav deliberate transcript
    reading, and workspace() stays on a dead session indefinitely (recon §6), so
    this is the morning-after gesture answered by permanent silence.

    Byte-exact against the pre-release measurement at 2bd7c38 (folder cue first,
    then the seek-and-play transcript)."""
    daemon, speaker, sessions = _dead_workspace(monkeypatch)
    _seed_transcript(daemon)
    daemon.handle_message(_msg("nav", "ws", to="first"))
    _drain(daemon, 6)
    assert [s for s in speaker.spoken if s] == ["web.", "hist 0", "hist 1", "hist 2"]


def test_nav_on_a_dead_workspace_cuts_the_live_read_not_the_nav_read(monkeypatch):
    """The second measured variant: a LIVE session holds the voice when ⌃⌘← lands
    on the dead workspace. Post-release both reads were lost — _nav's cancel cut
    the live one and the release dropped the nav one. Base parity is that the
    live read is the one cut (that is what a deliberate jump means) and the nav
    read is delivered; never both-silent. The live session's remaining backlog
    resumes afterwards via keep-going, so nothing is destroyed either."""
    _liveness(monkeypatch, dead={"/dev/ttysW"})
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    sessions.register("ws", cwd="/x/web"); _ident(sessions, "ws", "/dev/ttysW")
    sessions.register("S", cwd="/x/api"); _ident(sessions, "S", "/dev/ttysS")
    sessions.set_foreground("ws")                        # workspace() -> the dead session
    sessions.set_speaker("S")                            # ... while a LIVE session speaks
    daemon._enqueue("S", "prose", "s-live-1", False)
    daemon._enqueue("S", "prose", "s-live-2", False)
    daemon._speak_loop_once()
    assert "s-live-1" in speaker.spoken
    _seed_transcript(daemon)
    daemon.handle_message(_msg("nav", "ws", to="first"))
    _drain(daemon, 8)
    assert [s for s in speaker.spoken if s] == [
        "s-live-1", "web.", "hist 0", "hist 1", "hist 2", "api. s-live-2"]
    assert speaker.cancels == 1                          # _nav cut S; nothing cut the nav read


# --- RR-2: the SINGLE-ITEM grain — an answer is not a request for the pile ---
def test_settings_readback_on_a_dead_workspace_is_voiced_without_its_backlog(monkeypatch):
    """The grain pin. A rate press on the dead conjunction must be HEARD — the
    confirmation targets workspace() unconditionally (W11: the terminal you're at
    hears its own confirmation), and post-T9 nothing adopts that stream, so it was
    composed into silence. But it must be heard ALONE: a settings press is not the
    deliberate "read me that closed session" act ⌃⌘W/⌃⌘L/nav are, so the whole-
    stream wrinkle would turn a rate nudge into an unrequested backlog reading."""
    daemon, speaker, sessions = _dead_workspace(monkeypatch)
    daemon._enqueue("ws", "prose", "backlog one", False)
    daemon._enqueue("ws", "prose", "backlog two", False)
    daemon.handle_message(_msg("set_rate", "ws", delta=50))
    _drain(daemon, 6)
    assert "Rate 250." in speaker.spoken
    assert not any(s and "backlog" in s for s in speaker.spoken)
    assert len(daemon._stream("ws").queue) == 2           # kept, never destroyed
    assert sessions.speaker() is None                     # released at the next boundary
    assert daemon._deliberate_dead_read is None           # spent by its one item


def test_single_item_grain_never_truncates_a_sanctioned_whole_read(monkeypatch):
    """The two grains compose on one stream. A readback arriving mid-⌃⌘W must not
    narrow the whole read that is already running to a single item — re-sanctioning
    the same stream keeps the WIDER grain."""
    daemon, speaker, sessions = _dead_workspace(monkeypatch)
    daemon._enqueue("ws", "prose", "backlog one", False)
    daemon._enqueue("ws", "prose", "backlog two", False)
    daemon.handle_message(_msg("where_am_i", "ws"))        # whole sanction
    daemon._speak_loop_once()                              # the readout
    daemon.handle_message(_msg("set_rate", "ws", delta=50))  # single, mid-drain
    _drain(daemon, 6)
    assert any(s and "backlog one" in s for s in speaker.spoken)
    assert any(s and "backlog two" in s for s in speaker.spoken)
    assert "Rate 250." in speaker.spoken


def test_a_readback_never_parks_the_voice_on_a_dead_muted_workspace(monkeypatch):
    """Anti-wedge (probe G's shape, one grain over). The single-item sanction takes
    the IDLE voice, and a stopped stream never reaches the pop boundary at all —
    the held branch returns above it — so claiming the voice for a dead MUTED
    workspace would strand it there forever while live sessions wait. A muted
    workspace has nothing voiceable whether it is live or dead: the sanction must
    never make a dead stream more audible than the same stream would be alive."""
    _liveness(monkeypatch, dead={"/dev/ttysW"})
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    sessions.register("ws", cwd="/x/web"); _ident(sessions, "ws", "/dev/ttysW")
    sessions.register("L", cwd="/x/live"); _ident(sessions, "L", "/dev/ttysL")
    sessions.set_foreground("ws")
    sessions.set_speaker(None)
    daemon._stream("ws").stopped = True                   # muted AND dead
    daemon._enqueue("L", "prose", "l-item", False)
    daemon.handle_message(_msg("set_rate", "ws", delta=50))
    _drain(daemon, 4)
    assert sessions.speaker() == "L"
    assert any(s and "l-item" in s for s in speaker.spoken)


# --- RR-2: one representative per remaining site family ---
def test_verbosity_confirmation_on_a_dead_workspace_is_voiced(monkeypatch):
    daemon, speaker, sessions = _dead_workspace(monkeypatch)
    daemon.handle_message(_msg("cycle_verbosity", "ws"))
    _drain(daemon, 3)
    assert "Verbosity medium." in speaker.spoken


def test_jump_waiting_empty_case_on_a_dead_workspace_is_voiced(monkeypatch):
    daemon, speaker, sessions = _dead_workspace(monkeypatch)
    daemon.handle_message(_msg("jump_waiting", "ws"))
    _drain(daemon, 3)
    assert "No session waiting." in speaker.spoken


def test_repeat_last_on_a_dead_workspace_is_voiced(monkeypatch):
    daemon, speaker, sessions = _dead_workspace(monkeypatch)
    daemon._last_utterance = ("the last line.", None)
    daemon.handle_message(_msg("repeat_last", "ws"))
    _drain(daemon, 3)
    assert "the last line." in speaker.spoken


def test_skip_pile_empty_cue_on_a_dead_workspace_is_voiced(monkeypatch):
    daemon, speaker, sessions = _dead_workspace(monkeypatch)
    daemon.handle_message(_msg("skip_pile", "ws"))
    _drain(daemon, 3)
    assert "Nothing to skip." in speaker.spoken


def test_jump_decision_miss_on_a_dead_workspace_is_voiced(monkeypatch):
    daemon, speaker, sessions = _dead_workspace(monkeypatch)
    daemon.handle_message(_msg("jump_decision", "ws"))
    _drain(daemon, 3)
    assert "No decision here." in speaker.spoken


def test_chooser_preview_fallback_on_a_dead_workspace_is_voiced(monkeypatch):
    """_deliver_preview's playable-workspace leg: the browse gesture needs a LIVE
    candidate to open at all, but its preview still falls back to the (dead)
    workspace stream when the voice is idle."""
    _liveness(monkeypatch, dead={"/dev/ttysW"})
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    sessions.register("ws", cwd="/x/web"); _ident(sessions, "ws", "/dev/ttysW")
    sessions.register("api", cwd="/x/api"); _ident(sessions, "api", "/dev/ttysA")
    sessions.set_foreground("ws")
    sessions.set_speaker(None)
    daemon.handle_message(_msg("chooser_step", "ws", direction="next"))
    _drain(daemon, 3)
    assert "2, api." in speaker.spoken


# --- RR-3 (fix-wave E): the eighth single-item site, unnamed anywhere in D's own sweep ---
def test_answer_permission_confirmation_on_a_dead_workspace_is_voiced_without_its_backlog(monkeypatch):
    """approve/deny targets workspace() unconditionally (W6/D7a), with no liveness
    consult on the path — an eyes-free approval on the dead conjunction landed
    (behavior set, event fired) but its confirm stranded: an eyes-free approval
    answered by nothing is WB-C1's own argument for wiring ⌃⌘O, one step
    earlier in the same flow. Same grain as the settings readbacks: at_front is
    already unconditional here (a barge-in cue), so the sanction call only
    needs to arm the mark — confirm heard, backlog kept unread."""
    daemon, speaker, sessions = _dead_workspace(monkeypatch)
    daemon._enqueue("ws", "prose", "backlog one", False)
    daemon._enqueue("ws", "prose", "backlog two", False)
    daemon._pending_decisions["ws"] = {"event": threading.Event(), "behavior": None}
    daemon.handle_message(_msg("answer_permission", "ws", behavior="allow"))
    _drain(daemon, 6)
    assert "Approved." in speaker.spoken
    assert not any(s and "backlog" in s for s in speaker.spoken)
    assert len(daemon._stream("ws").queue) == 2           # kept, never destroyed
    assert sessions.speaker() is None                     # released at the next boundary
    assert daemon._deliberate_dead_read is None           # spent by its one item


# --- RR-4 (fix-wave E, re-adjudicated): ⌃⌘S-start on a dead MUTED workspace ---
def test_stop_session_resume_on_a_dead_muted_workspace_is_voiced_single_item(monkeypatch):
    """⌃⌘S-start is a fix-wave-A RELEASE regression, not pre-existing (the
    re-review measured it byte-identical to base pre-release): the start branch
    TAKES the voice itself (sessions.set_speaker(fg)), so R-1's release now
    strands "Resumed." the instant it lands on a dead stream. Single-item, not
    the whole-stream restore-base the controller rejected — proven by seeding a
    second item AFTER the press (content arriving right after the un-mute, the
    same outlived-tty shape RR-3/RR-4 both need): a whole mark would keep
    draining ws and never reach L; the single-item mark releases after
    "Resumed." alone, so it goes unheard, kept, and the voice moves on."""
    _liveness(monkeypatch, dead={"/dev/ttysW"})
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    sessions.register("ws", cwd="/x/web"); _ident(sessions, "ws", "/dev/ttysW")
    sessions.register("L", cwd="/x/live"); _ident(sessions, "L", "/dev/ttysL")
    sessions.set_foreground("ws")
    sessions.set_speaker(None)
    daemon._stream("ws").stopped = True                   # muted AND dead
    daemon._enqueue("ws", "prose", "pre-start pile", False)
    daemon._enqueue("L", "prose", "l-item", False)
    daemon.handle_message(_msg("stop_session", "ws"))
    daemon._enqueue("ws", "prose", "fresh output after unmute", False)
    _drain(daemon, 6)
    assert "Resumed." in speaker.spoken
    assert not any(s and "pre-start pile" in s for s in speaker.spoken)
    assert not any(s and "fresh output" in s for s in speaker.spoken)
    assert sessions.speaker() == "L"                      # released, not wedged
    assert any(s and "l-item" in s for s in speaker.spoken)
    assert daemon._deliberate_dead_read is None           # spent by its one item


# --- T2 (wave1 safety-net closure, owner-ruled 2026-08-15): the three
# remaining family sites — learn mode, query actions, re-read options ---
def test_learn_mode_toggle_on_a_dead_workspace_is_voiced(monkeypatch):
    """teaching.py on_learn_mode composes LEARN_ON/OFF into workspace()
    unconditionally, same RR-2 conjunction as the settings readbacks: with the
    voice idle on a dead workspace, the toggle's own confirmation was composed
    and never voiced. Single-item — the toggle answers itself, it is not a
    request to hear the closed session's pile."""
    daemon, speaker, sessions = _dead_workspace(monkeypatch)
    daemon._enqueue("ws", "prose", "backlog one", False)
    daemon._enqueue("ws", "prose", "backlog two", False)
    daemon.handle_message(_msg("learn_mode", "ws"))
    _drain(daemon, 6)
    assert ("Learn mode. Press any Sonari key to hear what it does. "
            "Press the same key again to exit.") in speaker.spoken
    assert not any(s and "backlog" in s for s in speaker.spoken)
    assert len(daemon._stream("ws").queue) == 2            # kept, never destroyed
    assert sessions.speaker() is None                      # released at the next boundary
    assert daemon._deliberate_dead_read is None            # spent by its one item


def test_query_actions_readout_on_a_dead_workspace_is_voiced(monkeypatch):
    """Same conjunction, teaching.py on_query_actions -> QUERY_DEFAULT."""
    daemon, speaker, sessions = _dead_workspace(monkeypatch)
    daemon._enqueue("ws", "prose", "backlog one", False)
    daemon._enqueue("ws", "prose", "backlog two", False)
    daemon.handle_message(_msg("query_actions", "ws"))
    _drain(daemon, 6)
    assert ("Control Command W says where you are. J jumps to a waiting "
            "session. Hold Tab on the chord to browse sessions.") in speaker.spoken
    assert not any(s and "backlog" in s for s in speaker.spoken)
    assert len(daemon._stream("ws").queue) == 2
    assert sessions.speaker() is None
    assert daemon._deliberate_dead_read is None


def test_reread_options_with_a_cached_prompt_on_a_dead_foreground_is_voiced(monkeypatch):
    """decisions.py on_reread_options targets foreground(), not workspace() (the
    different accessor the brief calls out) — the two coincide here because
    _dead_workspace makes one session both. This is its FIRST enqueue: the
    cached-options ('choice' kind) branch."""
    daemon, speaker, sessions = _dead_workspace(monkeypatch)
    daemon._stream("ws").options = "Option 1: Red."
    daemon._enqueue("ws", "prose", "backlog one", False)
    daemon._enqueue("ws", "prose", "backlog two", False)
    daemon.handle_message(_msg("reread_options", "ws"))
    _drain(daemon, 6)
    assert "Option 1: Red." in speaker.spoken
    assert not any(s and "backlog" in s for s in speaker.spoken)
    assert len(daemon._stream("ws").queue) == 2
    assert sessions.speaker() is None
    assert daemon._deliberate_dead_read is None


def test_reread_options_fallback_on_a_dead_foreground_is_voiced(monkeypatch):
    """on_reread_options's SECOND enqueue: the 'No options right now.' fallback
    when nothing is cached and nothing is pending — also a deliberate-press
    answer, also gets the sanction."""
    daemon, speaker, sessions = _dead_workspace(monkeypatch)
    daemon._enqueue("ws", "prose", "backlog one", False)
    daemon._enqueue("ws", "prose", "backlog two", False)
    daemon.handle_message(_msg("reread_options", "ws"))
    _drain(daemon, 6)
    assert "No options right now." in speaker.spoken
    assert not any(s and "backlog" in s for s in speaker.spoken)
    assert len(daemon._stream("ws").queue) == 2
    assert sessions.speaker() is None
    assert daemon._deliberate_dead_read is None


def test_the_three_closing_sites_are_byte_identical_on_a_live_workspace():
    """Live parity, the family's own contract restated by every wired site: the
    sanction returns False on a live session and at_front stays off, so today's
    path is untouched. A pre-existing item stays at the head (never displaced)
    across all four enqueues (learn on, learn off, query, reread fallback)."""
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    daemon._enqueue("fg", "prose", "earlier item", False)
    daemon.handle_message({"type": "learn_mode"})
    assert queue._items[0].text == "earlier item"
    assert queue._items[-1].text.startswith("Learn mode.")
    daemon.handle_message({"type": "learn_mode"})           # OFF, tidy state
    assert queue._items[0].text == "earlier item"
    assert queue._items[-1].text == "Learn mode off."
    daemon.handle_message({"type": "query_actions"})
    assert queue._items[0].text == "earlier item"
    assert queue._items[-1].text == (
        "Control Command W says where you are. J jumps to a waiting "
        "session. Hold Tab on the chord to browse sessions.")
    daemon.handle_message({"type": "reread_options"})
    assert queue._items[0].text == "earlier item"
    assert queue._items[-1].text == "No options right now."


# --- item E (wave1-T4, found by T2's implementer, deliberately left for a
# separate task): the idle auto-exit's OWN LEARN_OFF is a SEPARATE enqueue,
# one level below the manual toggle T2 just closed above ---
def test_learn_mode_idle_auto_exit_on_a_dead_workspace_is_voiced(monkeypatch):
    """_learn_mode_expired composes LEARN_OFF into workspace() unconditionally,
    the identical RR-2 conjunction as the manual toggle — but it is its own
    enqueue that T2 never touched (out of its scope). The realistic shape:
    learn mode goes on while the workspace is still live, the terminal closes
    (or the voice simply goes idle) before the 120s idle window elapses, and
    the auto-exit's own announcement then lands on a now-dead stream nothing
    adopts. Unheard, this strands more than silence: learn mode changes what
    every key DOES, so a user who never hears the exit believes keys still
    describe themselves, presses one, and it ACTS instead."""
    _fake_learn_timer(monkeypatch)
    dead = set()
    _liveness(monkeypatch, dead)
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    sessions.register("ws", cwd="/x/web")
    sessions.set_foreground("ws")
    _ident(sessions, "ws", "/dev/ttysW")
    daemon.handle_message(_msg("learn_mode", "ws"))        # ON while ws is still LIVE
    assert daemon._learn_mode is True
    _drain(daemon, 3)                                       # the ON announcement is heard live
    assert any(s and s.startswith("Learn mode.") for s in speaker.spoken)
    speaker.spoken.clear()
    sessions.set_speaker(None)                              # the voice goes idle
    dead.add("/dev/ttysW")                                  # ... and the terminal closes
    assert sessions.liveness("ws") == "dead"
    daemon._enqueue("ws", "prose", "backlog one", False)
    daemon._enqueue("ws", "prose", "backlog two", False)
    daemon._learn_timer.fn()                                # the idle timer fires
    _drain(daemon, 6)
    assert daemon._learn_mode is False
    assert "Learn mode off." in speaker.spoken
    assert not any(s and "backlog" in s for s in speaker.spoken)
    assert len(daemon._stream("ws").queue) == 2             # kept, never destroyed
    assert sessions.speaker() is None                       # released at the next boundary
    assert daemon._deliberate_dead_read is None             # spent by its one item
