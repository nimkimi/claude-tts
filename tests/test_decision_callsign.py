"""D8 (spec §5.5): a decision's call-sign is a PRELUDE on the decision's own
SpeechItem — chime at arrival (transient), spearcon + sentence as one atomic
queued unit (this closes the W9 overlap race). Sessionless legacy messages and
cache misses degrade to the chime alone / today's folder splice."""
from sonari.protocol import PROTOCOL_VERSION
from tests.daemon_helpers import make_daemon, stream_queue


def _msg(t, session, **kw):
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


def test_blocking_permission_chimes_and_binds_the_callsign_prelude():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.register("backend", cwd="/x/backend")
    daemon._spearcons.available["backend"] = "/sp/backend.aiff"
    daemon.handle_message(_msg("permission_request", "backend",
                               tool="Bash", summary="rm x"))
    assert speaker.earcons == ["permission"]       # chime only at arrival
    item = stream_queue(daemon, "backend")._items[0]   # [0]: the decision precedes on_permission_request's teaching hint
    assert item.is_decision
    assert item.prelude == ("/sp/backend.aiff",)
    assert item.names_session                      # spearcon replaces the splice


def test_choice_content_gains_the_prelude_at_the_enqueue_chokepoint():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.register("billing", cwd="/x/billing")
    daemon._spearcons.available["billing"] = "/sp/billing.aiff"
    daemon.handle_message(_msg("choice", "billing", questions=[
        {"question": "Pick one.", "options": [{"label": "A"}]}]))
    item = stream_queue(daemon, "billing")._items[-1]
    assert item.is_decision
    assert item.prelude == ("/sp/billing.aiff",)
    assert item.names_session


def test_spearcon_miss_degrades_to_chime_plus_splice_and_kicks_generation():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.register("backend", cwd="/x/backend")
    daemon.handle_message(_msg("permission_request", "backend",
                               tool="Bash", summary="rm x"))
    assert speaker.earcons == ["permission"]       # chime alone, byte-identical
    item = stream_queue(daemon, "backend")._items[0]   # [0]: the decision precedes on_permission_request's teaching hint
    assert item.prelude == ()
    assert not item.names_session                  # today's splice still applies
    assert "backend" in daemon._spearcons.generated   # self-heals by next time


def test_sessioned_earcon_message_is_chime_only():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.register("billing", cwd="/x/billing")
    daemon._spearcons.available["billing"] = "/sp/billing.aiff"
    daemon.handle_message(_msg("earcon", "billing", kind="choice"))
    assert speaker.earcons == ["choice"]           # no spearcon rides the message
    assert speaker.audio_paths == []               # no verbal sound at all


def test_sessionless_legacy_earcon_is_chime_alone():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon.handle_message(_msg("earcon", "", kind="choice"))   # old hook version
    assert speaker.earcons == ["choice"]
    assert speaker.audio_paths == []


def test_loop_plays_spearcon_then_sentence_unprefixed():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.set_foreground("fg", cwd="/x/fg")     # attach a folder to the speaker
    daemon._spearcons.available["fg"] = "/sp/fg.aiff"
    daemon.handle_message(_msg("choice", "fg", questions=[
        {"question": "Pick one.", "options": [{"label": "A"}]}]))
    daemon._speak_loop_once()
    assert speaker.audio_paths == ["/sp/fg.aiff", None]
    assert speaker.spoken[-1].startswith("Pick one.")  # no spliced folder prefix
