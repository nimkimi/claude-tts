"""W9 (spec §10): the decision chime gains the ASKING session's spearcon —
sequenced (chime, then the ~200ms folder label), never overlapped. Sessionless
legacy messages and cache misses fall back to the chime alone, byte-identically."""
import threading

from sonari.protocol import PROTOCOL_VERSION
from sonari.speaker import Speaker
from tests.daemon_helpers import make_daemon


def _msg(t, session, **kw):
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


class _SeqProc:
    def __init__(self, log, path):
        self.log = log
        self.path = path

    def wait(self, timeout=None):
        self.log.append(("waited", self.path))

    def poll(self):
        return 0


def test_real_speaker_sequences_chime_then_spearcon():
    log = []
    done = threading.Event()

    def player(path):
        log.append(("play", path))
        if path == "/sp/backend.aiff":
            done.set()
        return _SeqProc(log, path)

    sp = Speaker(earcon_player=player,
                 earcons={"permission": "/snd/Funk.aiff"})
    sp.earcon_then("permission", "/sp/backend.aiff")
    assert done.wait(2.0), "sequencer thread never played the spearcon"
    assert log[0] == ("play", "/snd/Funk.aiff")
    assert log[1] == ("waited", "/snd/Funk.aiff")   # chime finished FIRST
    assert log[2] == ("play", "/sp/backend.aiff")


def test_blocking_permission_gains_the_asking_sessions_callsign():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.register("backend", cwd="/x/backend")
    daemon._spearcons.available["backend"] = "/sp/backend.aiff"
    daemon.handle_message(_msg("permission_request", "backend",
                               tool="Bash", summary="rm x"))
    assert speaker.earcon_seqs == [("permission", "/sp/backend.aiff")]
    assert speaker.earcons == []                   # sequenced, not the plain path


def test_spearcon_miss_falls_back_to_chime_alone_and_kicks_generation():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.register("backend", cwd="/x/backend")
    daemon.handle_message(_msg("permission_request", "backend",
                               tool="Bash", summary="rm x"))
    assert speaker.earcons == ["permission"]       # today's behavior, byte-identical
    assert speaker.earcon_seqs == []
    assert "backend" in daemon._spearcons.generated   # miss kicked background gen


def test_sessionless_legacy_earcon_is_chime_alone():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon.handle_message(_msg("earcon", "", kind="choice"))   # old hook version
    assert speaker.earcons == ["choice"]
    assert speaker.earcon_seqs == []


def test_session_carrying_earcon_gets_the_callsign():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.register("billing", cwd="/x/billing")
    daemon._spearcons.available["billing"] = "/sp/billing.aiff"
    daemon.handle_message(_msg("earcon", "billing", kind="choice"))
    assert speaker.earcon_seqs == [("choice", "/sp/billing.aiff")]
