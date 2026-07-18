"""Learn mode (SP-D1, Task 10): while on, every Sonari hotkey speaks its own
'teach' sentence instead of acting, with a 120s idle auto-exit. The interception
lives in SpeechDaemon._dispatch_hotkey (the raw hotkey path), so a socket message
is never intercepted; the LEARN_MODE handler in features/teaching.py owns only
the toggle.

Task 11: first-encounter hints (teaching.maybe_hint) — one-shot spoken cues fired
the first time each of four moments happens in a daemon run, 'everything'
verbosity only."""
import threading

import pytest

from sonari import keymap
from sonari.daemon.features import teaching
from tests.daemon_helpers import make_daemon, stream_queue


@pytest.fixture
def timers(monkeypatch):
    """Replace threading.Timer with a fake that records (interval, fn) and starts
    NO real background thread — so the 120s idle auto-exit is inspectable and the
    suite never leaves a live daemon timer behind."""
    created = []

    class _FakeTimer:
        def __init__(self, interval, fn):
            self.interval = interval
            self.fn = fn
            self.daemon = False
            self.started = False
            self.cancelled = False
            created.append(self)

        def start(self):
            self.started = True

        def cancel(self):
            self.cancelled = True

    monkeypatch.setattr(threading, "Timer", _FakeTimer)
    return created


def test_learn_mode_toggle_announces(timers):
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    daemon.handle_message({"type": "learn_mode"})
    assert daemon._learn_mode is True
    item = queue._items[-1]
    assert item.text.startswith("Learn mode.")
    assert item.mute_exempt and item.pause_exempt
    daemon.handle_message({"type": "learn_mode"})
    assert daemon._learn_mode is False
    assert queue._items[-1].text == "Learn mode off."
    assert timers[0].cancelled is True and len(timers) == 1   # manual exit cancels the idle timer


def test_hotkey_intercepted_speaks_teach_and_does_not_execute(timers, monkeypatch):
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    daemon.handle_message({"type": "learn_mode"})          # learn mode ON
    dispatched = []
    real = daemon.handle_message

    def spy(m):
        dispatched.append(m.get("type"))
        return real(m)

    monkeypatch.setattr(daemon, "handle_message", spy)
    daemon._dispatch_hotkey({"type": "where_am_i"})
    assert "where_am_i" not in dispatched                  # the action never dispatched
    assert daemon._learn_mode is True                      # interception does not toggle
    item = queue._items[-1]
    assert item.text == keymap.ACTIONS["where_am_i"]["teach"]
    assert item.mute_exempt and item.pause_exempt


def test_learn_mode_toggle_key_is_exempt_from_interception(timers):
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    daemon.handle_message({"type": "learn_mode"})          # ON
    assert daemon._learn_mode is True
    daemon._dispatch_hotkey({"type": "learn_mode"})        # the toggle key itself
    assert daemon._learn_mode is False                     # exits (not taught)
    assert queue._items[-1].text == "Learn mode off."


def test_socket_path_never_intercepted(timers):
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    daemon.handle_message({"type": "learn_mode"})          # ON
    daemon.handle_message({"type": "stop"})                # socket path
    assert speaker.cancels == 1                            # stop executed, not taught
    assert daemon._learn_mode is True                      # socket path leaves mode alone


def test_auto_exit_timer_rearms_and_fires(timers):
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    daemon.handle_message({"type": "learn_mode"})          # arms the idle timer
    assert timers[-1].interval == 120
    assert timers[-1].started is True
    first = timers[-1]
    daemon._dispatch_hotkey({"type": "where_am_i"})        # a press re-arms it
    assert first.cancelled is True                         # the old timer is cancelled
    assert len(timers) == 2 and timers[-1].interval == 120
    timers[-1].fn()                                        # the idle timer fires
    assert daemon._learn_mode is False
    assert queue._items[-1].text == "Learn mode off."


# --- Task 11: first-encounter hints -------------------------------------------

def test_hint_keys_all_have_sentences():
    assert set(teaching.HINTS) == {"decision", "background_turn", "chooser",
                                   "catch_up_done"}


def test_hint_fires_once_per_daemon_run():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    for i in range(2):                                     # trigger the decision moment twice
        with daemon._state.transaction():
            daemon.handle_message(
                {"type": "permission_request", "session": "fg",
                 "tool": "Bash", "summary": "cmd{0}".format(i)})
    hints = [it for it in queue._items if it.text == teaching.HINTS["decision"]]
    assert len(hints) == 1                                 # enqueued exactly once


def test_hint_respects_verbosity():
    daemon, queue, speaker, sessions, config = make_daemon(
        foreground="fg", verbosity="medium")
    with daemon._state.transaction():
        daemon.handle_message(
            {"type": "permission_request", "session": "fg",
             "tool": "Bash", "summary": "cmd"})
    hints = [it for it in queue._items if it.text == teaching.HINTS["decision"]]
    assert hints == []                                     # medium -> no hint


def test_background_turn_hint_fires_on_the_landed_ding():
    # The hint fires only on the branch that actually dings (a session that is
    # NOT the live speaker finishing) -- matches "A background session finished."
    # It must land in the STREAM THE VOICE IS PLAYING (the speaker's), not the
    # just-finished session's own stream: background streams sit unheard until
    # the user reaches them some other way (the speak loop only ever pops the
    # speaker stream), which would bury the "Control Command J jumps to it" text
    # exactly where it's needed to save the user from.
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    sessions.set_speaker("B")                              # voice=B, workspace=A
    daemon.handle_message({"type": "earcon", "session": "A", "kind": "turn_done"})
    a_hints = [it for it in queue._items
               if it.text == teaching.HINTS["background_turn"]]
    assert a_hints == []                                   # not stuck in A's own stream
    b_hints = [it for it in stream_queue(daemon, "B")._items
               if it.text == teaching.HINTS["background_turn"]]
    assert len(b_hints) == 1                                # heard on the speaker's stream


def test_chooser_hint_fires_on_first_open():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    daemon.handle_message({"type": "chooser_step", "session": "", "direction": "next"})
    hints = [it for it in queue._items if it.text == teaching.HINTS["chooser"]]
    assert len(hints) == 1
    # a second step (still the same open gesture) must not fire it again
    daemon.handle_message({"type": "chooser_step", "session": "", "direction": "next"})
    hints = [it for it in queue._items if it.text == teaching.HINTS["chooser"]]
    assert len(hints) == 1


def test_chooser_hint_survives_a_first_open_with_no_playable_preview():
    # The very first chooser open can land on a step whose preview has nowhere
    # to speak (no live speaker AND the workspace stream is muted) -- that must
    # not permanently burn the "chooser" key. The hint should still fire the
    # next time an open actually delivers a preview somewhere.
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    daemon._stream("A").stopped = True                     # workspace muted
    sessions.set_speaker(None)                              # nowhere to speak
    daemon.handle_message({"type": "chooser_step", "session": "", "direction": "next"})
    assert speaker.earcons[-1] == "error"                   # the failed preview, confirmed
    hints = [it for it in queue._items if it.text == teaching.HINTS["chooser"]]
    assert hints == []                                      # nothing spoken: not burned yet
    daemon.handle_message({"type": "chooser_cancel", "session": ""})
    assert daemon._chooser is None
    daemon._stream("A").stopped = False                     # workspace playable again
    sessions.set_speaker("A")
    daemon.handle_message({"type": "chooser_step", "session": "", "direction": "next"})
    hints = [it for it in queue._items if it.text == teaching.HINTS["chooser"]]
    assert len(hints) == 1                                  # the retried open still teaches it


def test_maybe_hint_does_not_consume_the_key_on_a_null_session():
    # Unit-level lock on the ordering fix: a moment with nothing to speak into
    # must leave the key available for the NEXT call, not silently and
    # permanently consume it (teaching.maybe_hint, the mark-before-check bug).
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    teaching.maybe_hint(daemon, "chooser", None)
    assert "chooser" not in daemon._hinted
    teaching.maybe_hint(daemon, "chooser", "A")
    assert "chooser" in daemon._hinted
    hints = [it for it in queue._items if it.text == teaching.HINTS["chooser"]]
    assert len(hints) == 1


def test_catch_up_done_hint_fires_on_successful_summary():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    sessions.set_foreground("fg", cwd="/x/myrepo")
    daemon._catchup = {"id": 1, "target": "fg", "folder": "myrepo",
                       "slice_end": (0, 0), "digest": "Summary unavailable.",
                       "cancel": threading.Event(), "phase": "preparing",
                       "render_id": None, "ended": False, "ack_id": None}
    daemon.handle_message({"type": "catchup_result", "request_id": 1,
                           "ok": True, "text": "The build is green.", "reason": ""})
    hints = [it for it in queue._items if it.text == teaching.HINTS["catch_up_done"]]
    assert len(hints) == 1


# --- Task 12: the query key ("what can I do right now") -----------------------

def test_query_pending_decision():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    with daemon._state.transaction():
        daemon.handle_message(
            {"type": "permission_request", "session": "fg",
             "tool": "Bash", "summary": "cmd"})
    daemon.handle_message({"type": "query_actions"})
    item = queue._items[-1]
    assert item.text == teaching.QUERY_DECISION
    assert item.mute_exempt and item.pause_exempt


def test_query_while_stopped():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    daemon.voice_state = "stopped-all"
    daemon.handle_message({"type": "query_actions"})
    item = queue._items[-1]
    assert item.text == teaching.QUERY_STOPPED
    assert item.mute_exempt and item.pause_exempt


def test_query_default():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    daemon.handle_message({"type": "query_actions"})
    item = queue._items[-1]
    assert item.text == teaching.QUERY_DEFAULT
    assert item.mute_exempt and item.pause_exempt
