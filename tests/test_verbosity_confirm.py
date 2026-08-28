# tests/test_verbosity_confirm.py (new)
"""W3 (spec §4): SET_VERBOSITY confirms itself on the live path, at every
verbosity (direct _enqueue cues bypass the on_prose quiet gate), targeting
workspace() (born on W11's collapsed target)."""
from sonari.protocol import PROTOCOL_VERSION
from tests.daemon_helpers import make_daemon, stream_queue


def _msg(t, session, **kw):
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


def _nosave(monkeypatch):
    from sonari.daemon.features import control
    monkeypatch.setattr(control, "save_config", lambda cfg: None)


def test_each_level_speaks_its_exact_confirmation(monkeypatch):
    _nosave(monkeypatch)
    daemon, queue, speaker, sessions, config = make_daemon()
    for level, want in (("quiet", "Verbosity quiet."),
                        ("medium", "Verbosity medium."),
                        ("everything", "Verbosity everything.")):
        daemon.handle_message(_msg("set_verbosity", "fg", verbosity=level))
        daemon._speak_loop_once()
        assert speaker.spoken[-1] == want
        assert config["verbosity"] == level


def test_confirmation_is_idempotent_on_resets_of_the_same_value(monkeypatch):
    _nosave(monkeypatch)
    daemon, queue, speaker, sessions, config = make_daemon()
    for _ in range(2):
        daemon.handle_message(_msg("set_verbosity", "fg", verbosity="quiet"))
        daemon._speak_loop_once()
    assert speaker.spoken == ["Verbosity quiet.", "Verbosity quiet."]


def test_invalid_value_stays_silent(monkeypatch):
    _nosave(monkeypatch)
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon.handle_message(_msg("set_verbosity", "fg", verbosity="loud"))
    assert len(queue._items) == 0
    assert config["verbosity"] == "everything"


def test_confirmation_lands_on_the_workspace_not_the_drifted_speaker(monkeypatch):
    _nosave(monkeypatch)
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.register("b", cwd="/x/b")
    sessions.set_speaker("b")                      # keep-going drifted the voice
    daemon.handle_message(_msg("set_verbosity", "fg", verbosity="medium"))
    assert [it.text for it in queue._items] == ["Verbosity medium."]
    assert len(stream_queue(daemon, "b")._items) == 0


# ---------------------------------------------------------------------------
# Task 9: CYCLE_VERBOSITY's confirmation aligned to the same live path.
# ---------------------------------------------------------------------------

def test_cycle_verbosity_speaks_on_workspace_with_exempt_flags(monkeypatch):
    _nosave(monkeypatch)
    daemon, queue, speaker, sessions, config = make_daemon(verbosity="everything")
    sessions.register("b", cwd="/x/b")
    sessions.set_speaker("b")                      # keep-going drifted the voice
    daemon.handle_message(_msg("cycle_verbosity", "fg"))
    assert config["verbosity"] == "medium"
    assert [it.text for it in queue._items] == ["Verbosity medium."]
    item = queue._items[0]
    assert item.control_cue is True
    assert len(stream_queue(daemon, "b")._items) == 0
