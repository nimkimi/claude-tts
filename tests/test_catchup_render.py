import threading

from sonari.catchup import resolve_summary_voice
from tests.daemon_helpers import make_daemon, stream_queue


def _result(rid, ok, text="", reason=""):
    return {"v": 1, "type": "catchup_result", "request_id": rid,
            "ok": ok, "text": text, "reason": reason}


def _inflight(daemon, target="fg", folder="myrepo",
              digest="Summary unavailable. Last: x."):
    daemon._catchup = {"id": 1, "target": target, "folder": folder,
                       "slice_end": (0, 0), "digest": digest,
                       "cancel": threading.Event(), "phase": "preparing",
                       "render_id": None, "ended": False, "ack_id": None}
    return 1


def _catch_up(session="fg"):
    return {"v": 1, "type": "catch_up", "session": session}


def _drain(daemon, n=4):
    for _ in range(n):
        daemon._speak_loop_once()


def test_resolve_summary_voice_rules():
    assert resolve_summary_voice("Daniel", "Alex", ["Alex", "Daniel"]) == "Daniel"
    assert resolve_summary_voice("auto", "Alex", ["Alex", "Samantha"]) == "Samantha"
    assert resolve_summary_voice("auto", "Alex", ["Alex"]) == "Alex"
    assert resolve_summary_voice("auto", None, []) is None
    assert resolve_summary_voice("off", "Alex", ["Bob"]) == "Alex"


def test_success_renders_frame_then_body_in_summary_voice():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.set_foreground("fg", cwd="/x/myrepo")
    config["summary_voice"] = "Daniel"
    rid = _inflight(daemon, target="fg", folder="myrepo")
    daemon.handle_message(_result(rid, ok=True, text="The build is green."))
    _drain(daemon)
    assert speaker.spoken[:2] == ["Summary:", "The build is green."]
    assert speaker.spoken_voices[0] is None          # frame -> main voice
    assert speaker.spoken_voices[1] == "Daniel"      # body -> summary voice


def test_pending_decision_appends_tail_last():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.set_foreground("fg", cwd="/x/r")
    daemon._pending_decisions["fg"] = {"event": None, "behavior": None,
                                       "text": "?", "item_id": None}
    rid = _inflight(daemon, target="fg", folder="r")
    daemon.handle_message(_result(rid, ok=True, text="Ran tests."))
    _drain(daemon)
    assert speaker.spoken[speaker.spoken.index("Ran tests.") + 1] == "Decision waiting."


def test_no_decision_no_tail():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.set_foreground("fg", cwd="/x/r")
    rid = _inflight(daemon, target="fg", folder="r")
    daemon.handle_message(_result(rid, ok=True, text="Ran tests."))
    _drain(daemon)
    assert "Decision waiting." not in speaker.spoken


def test_failure_renders_digest_main_voice_no_frame():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.set_foreground("fg", cwd="/x/r")
    config["summary_voice"] = "Daniel"
    rid = _inflight(daemon, folder="r", digest="Summary unavailable. Last: All done.")
    daemon.handle_message(_result(rid, ok=False, reason="timeout"))
    _drain(daemon)
    assert "Summary:" not in speaker.spoken
    assert speaker.spoken[0] == "Summary unavailable. Last: All done."
    assert speaker.spoken_voices[0] is None


def test_empty_summary_falls_to_digest():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.set_foreground("fg", cwd="/x/r")
    rid = _inflight(daemon, folder="r", digest="Summary unavailable. Last: x.")
    daemon.handle_message(_result(rid, ok=True, text="```\n\n```"))
    _drain(daemon)
    assert "Summary:" not in speaker.spoken
    assert "Summary unavailable. Last: x." in speaker.spoken


def test_session_ended_midprep_prepends_folder_ended_no_tail():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.set_foreground("live", cwd="/x/live")   # a live speaker to voice on
    daemon._pending_decisions["gone"] = {"event": None, "behavior": None,
                                         "text": "?", "item_id": None}
    rid = _inflight(daemon, target="gone", folder="oldrepo")   # 'gone' unregistered
    daemon.handle_message(_result(rid, ok=True, text="It finished the refactor."))
    _drain(daemon)
    assert speaker.spoken[0] == "oldrepo ended."
    assert "Summary:" in speaker.spoken
    assert "Decision waiting." not in speaker.spoken


def test_stale_result_after_cancel_is_dropped():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.set_foreground("fg", cwd="/x/r")
    rid = _inflight(daemon)
    daemon._catchup = None                           # a cancel landed first
    daemon.handle_message(_result(rid, ok=True, text="Late summary."))
    _drain(daemon)
    assert "Late summary." not in speaker.spoken and "Summary:" not in speaker.spoken


def test_wrong_request_id_is_dropped():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.set_foreground("fg", cwd="/x/r")
    _inflight(daemon)                                # id == 1
    daemon.handle_message(_result(999, ok=True, text="Wrong."))
    _drain(daemon)
    assert "Wrong." not in speaker.spoken


def test_diverged_speaker_render_carries_folder_attribution():
    # The summary can land after the user switched to another session mid-prep;
    # the render then plays on the NEW speaker's stream, where mute_exempt
    # suppresses the standard folder prefix. The frame must carry the
    # attribution itself — an unattributed cross-session summary reads as the
    # wrong session to an eyes-free user.
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.set_foreground("fg", cwd="/x/myrepo")
    rid = _inflight(daemon, target="fg", folder="myrepo")
    sessions.set_foreground("other", cwd="/x/other")   # speaker diverges mid-prep
    daemon.handle_message(_result(rid, ok=True, text="The refactor landed."))
    _drain(daemon)
    assert "myrepo. Summary:" in speaker.spoken
    assert "Summary:" not in speaker.spoken            # only the attributed frame


def test_diverged_speaker_digest_carries_folder_attribution():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.set_foreground("fg", cwd="/x/myrepo")
    rid = _inflight(daemon, target="fg", folder="myrepo",
                    digest="Summary unavailable. Last: x.")
    sessions.set_foreground("other", cwd="/x/other")
    daemon.handle_message(_result(rid, ok=False, reason="timeout"))
    _drain(daemon)
    assert "myrepo. Summary unavailable. Last: x." in speaker.spoken


def test_converged_render_frame_stays_unprefixed():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.set_foreground("fg", cwd="/x/myrepo")
    rid = _inflight(daemon, target="fg", folder="myrepo")
    daemon.handle_message(_result(rid, ok=True, text="Done."))
    _drain(daemon)
    assert "Summary:" in speaker.spoken                # no folder prefix when convergent


def test_failure_reason_is_logged(capsys):
    # A silent fall to the digest floor made the live PATH miss invisible —
    # the failure reason must land in speechd.log (stderr), focus-log idiom.
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.set_foreground("fg", cwd="/x/r")
    rid = _inflight(daemon, folder="r")
    daemon.handle_message(_result(rid, ok=False, reason="unavailable"))
    assert "sonari[catchup]: summary failed reason=unavailable" in capsys.readouterr().err
