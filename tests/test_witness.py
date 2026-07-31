"""§7 witness, daemon side (hotkeyd-death direction): armed only after the
FIRST ping; fires ONCE past 15 s via the raw queue-bypassing seam; re-arms on
ping recovery; age surfaced in STATUS. The check method and handler are driven
directly — never a live daemon."""
import time

from sonari.protocol import PROTOCOL_VERSION, MsgType
from tests.daemon_helpers import make_daemon


def _msg(t, session="", **kw):
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


def _arm(daemon):
    calls = []
    daemon._alarm_popen = lambda cmd: calls.append(cmd)
    return calls


def test_ping_stamps_and_replies_ok():
    daemon, queue, speaker, sessions, config = make_daemon()
    assert daemon._witness_last_ping is None
    reply = daemon.handle_message(_msg(MsgType.WITNESS_PING))
    assert reply == {"ok": True}
    assert daemon._witness_last_ping is not None


def test_no_alarm_before_the_first_ping():
    # Hotkeys disabled / never installed == no pings ever == no false alarm.
    daemon, queue, speaker, sessions, config = make_daemon()
    calls = _arm(daemon)
    daemon._speak_loop_once()                      # the tick runs the check
    assert calls == []


def test_alarm_fires_once_past_the_timeout_queue_bypassing():
    daemon, queue, speaker, sessions, config = make_daemon()
    calls = _arm(daemon)
    daemon.handle_message(_msg(MsgType.WITNESS_PING))
    daemon._witness_last_ping = time.monotonic() - 16.0
    daemon._check_witness()
    assert calls == [["afplay", "/System/Library/Sounds/Glass.aiff"],
                     ["say", "Hotkeys are down."]]
    daemon._check_witness()                        # once-latch: no re-fire
    assert len(calls) == 2
    assert speaker.spoken == [] and speaker.earcons == []   # NOT Speaker, NOT the queue


def test_ping_recovery_rearms():
    daemon, queue, speaker, sessions, config = make_daemon()
    calls = _arm(daemon)
    daemon.handle_message(_msg(MsgType.WITNESS_PING))
    daemon._witness_last_ping = time.monotonic() - 16.0
    daemon._check_witness()
    assert len(calls) == 2
    daemon.handle_message(_msg(MsgType.WITNESS_PING))      # hotkeyd is back
    daemon._witness_last_ping = time.monotonic() - 16.0    # ...and dies again
    daemon._check_witness()
    assert len(calls) == 4


def test_alarm_asset_resolves_config_first():
    daemon, queue, speaker, sessions, config = make_daemon()
    config["earcons"] = {"alarm_hotkeys_down": "/custom/horn.aiff"}
    calls = _arm(daemon)
    daemon.handle_message(_msg(MsgType.WITNESS_PING))
    daemon._witness_last_ping = time.monotonic() - 16.0
    daemon._check_witness()
    assert calls[0] == ["afplay", "/custom/horn.aiff"]


def test_status_exposes_witness_ping_age():
    daemon, queue, speaker, sessions, config = make_daemon()
    reply = daemon.handle_message(_msg(MsgType.STATUS))
    assert reply["witness_ping_age_s"] is None             # before the first ping
    daemon.handle_message(_msg(MsgType.WITNESS_PING))
    reply = daemon.handle_message(_msg(MsgType.STATUS))
    assert reply["witness_ping_age_s"] >= 0.0


def test_witness_ping_does_not_mark_persistence_dirty():
    # The ~5 s heartbeat changes no durable state; if it rode the dispatch
    # chokepoint's mark_dirty, an idle 24/7 daemon would fsync state.json
    # forever (and build the snapshot under the speak loop's lock each time).
    daemon, queue, speaker, sessions, config = make_daemon()
    marks = []
    daemon._persistence.mark_dirty = lambda: marks.append(1)
    daemon.handle_message(_msg(MsgType.WITNESS_PING))
    assert marks == []


def test_non_witness_messages_still_mark_persistence_dirty():
    daemon, queue, speaker, sessions, config = make_daemon()
    marks = []
    daemon._persistence.mark_dirty = lambda: marks.append(1)
    daemon.handle_message(_msg(MsgType.STATUS))
    assert marks == [1]
