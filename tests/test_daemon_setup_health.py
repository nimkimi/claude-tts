
from sonari.daemon.features import lifecycle
import sonari.install_record as install_record
from tests.daemon_helpers import make_daemon, stream_queue


def _write_install_json(tmp_path, plugin_version="0.4.0"):
    rec = tmp_path / "install.json"
    import json
    rec.write_text(json.dumps({"plugin_version": plugin_version}))
    return rec


def test_setup_health_not_installed_when_no_record(tmp_path, monkeypatch):
    missing = tmp_path / "install.json"  # never created
    monkeypatch.setattr(install_record, "INSTALL_RECORD_PATH", str(missing))
    monkeypatch.setattr(lifecycle, "_launcher_present", lambda: True)
    state, cue = lifecycle._setup_health("0.4.0")
    assert state == "not_installed"
    assert "slash sonari install" in cue.lower()


def test_setup_health_not_installed_when_launcher_missing(tmp_path, monkeypatch):
    rec = _write_install_json(tmp_path)
    monkeypatch.setattr(install_record, "INSTALL_RECORD_PATH", str(rec))
    monkeypatch.setattr(lifecycle, "_launcher_present", lambda: False)
    state, cue = lifecycle._setup_health("0.4.0")
    assert state == "not_installed"
    assert "slash sonari install" in cue.lower()


def test_setup_health_ok_speech_only_no_hotkeyd(tmp_path, monkeypatch):
    # install.json + launcher present, hotkeyd binary ABSENT, versions match.
    rec = _write_install_json(tmp_path, plugin_version="0.4.0")
    monkeypatch.setattr(install_record, "INSTALL_RECORD_PATH", str(rec))
    monkeypatch.setattr(lifecycle, "_launcher_present", lambda: True)
    state, cue = lifecycle._setup_health("0.4.0")
    assert state == "ok"
    assert cue is None


def test_setup_health_ok_when_versions_match(tmp_path, monkeypatch):
    rec = _write_install_json(tmp_path, plugin_version="0.4.0")
    monkeypatch.setattr(install_record, "INSTALL_RECORD_PATH", str(rec))
    monkeypatch.setattr(lifecycle, "_launcher_present", lambda: True)
    state, cue = lifecycle._setup_health("0.4.0")
    assert state == "ok"
    assert cue is None


def test_setup_health_version_drift(tmp_path, monkeypatch):
    rec = _write_install_json(tmp_path, plugin_version="0.3.0")
    monkeypatch.setattr(install_record, "INSTALL_RECORD_PATH", str(rec))
    monkeypatch.setattr(lifecycle, "_launcher_present", lambda: True)
    state, cue = lifecycle._setup_health("0.4.0")
    assert state == "version_drift"
    assert "updated" in cue.lower()
    assert "slash sonari install" in cue.lower()


def test_setup_health_no_drift_when_session_version_empty(tmp_path, monkeypatch):
    rec = _write_install_json(tmp_path, plugin_version="0.3.0")
    monkeypatch.setattr(install_record, "INSTALL_RECORD_PATH", str(rec))
    monkeypatch.setattr(lifecycle, "_launcher_present", lambda: True)
    state, cue = lifecycle._setup_health("")  # unknown session version
    assert state == "ok"
    assert cue is None


def test_read_install_record_returns_none_on_corrupt(tmp_path, monkeypatch):
    rec = tmp_path / "install.json"
    rec.write_text("{ not json")
    monkeypatch.setattr(install_record, "INSTALL_RECORD_PATH", str(rec))
    assert install_record.read_install_record() is None


from sonari.protocol import MsgType, PROTOCOL_VERSION


def _ss(session, plugin_version=""):
    return {"v": PROTOCOL_VERSION, "type": MsgType.SESSION_START,
            "session": session, "plugin_version": plugin_version}


def _se(session):
    return {"v": PROTOCOL_VERSION, "type": MsgType.SESSION_END, "session": session}


def test_session_start_enqueues_one_cue_when_not_installed(monkeypatch):
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    monkeypatch.setattr(lifecycle, "_setup_health",
                        lambda v: ("not_installed", "RUN slash sonari install"))
    daemon.handle_message(_ss("s1"))
    # SESSION_START sets s1 foreground; the cue lands in s1's own stream.
    q = stream_queue(daemon, "s1")
    assert len(q) == 1
    item = q.pop_next()
    assert item.kind == "prose"
    assert "slash sonari install" in item.text.lower()


def test_session_start_silent_when_ok(monkeypatch):
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    monkeypatch.setattr(lifecycle, "_setup_health", lambda v: ("ok", None))
    daemon.handle_message(_ss("s1"))
    assert len(queue) == 0


def test_session_start_cue_throttled_per_session(monkeypatch):
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    monkeypatch.setattr(lifecycle, "_setup_health",
                        lambda v: ("not_installed", "RUN slash sonari install"))
    daemon.handle_message(_ss("s1"))
    daemon.handle_message(_ss("s1"))  # same session again
    assert len(stream_queue(daemon, "s1")) == 1  # only ONE cue (in s1's stream)


def test_session_end_clears_throttle_so_cue_can_fire_again(monkeypatch):
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    monkeypatch.setattr(lifecycle, "_setup_health",
                        lambda v: ("not_installed", "RUN slash sonari install"))
    daemon.handle_message(_ss("s1"))
    assert len(stream_queue(daemon, "s1")) == 1
    stream_queue(daemon, "s1").pop_next()
    daemon.handle_message(_se("s1"))     # drops s1's stream + throttle
    daemon.handle_message(_ss("s1"))  # new session lifecycle, same id
    # SESSION_END destroyed the old stream; the cue fires again into the fresh one.
    assert len(stream_queue(daemon, "s1")) == 1


def test_setup_health_exception_never_breaks_session(monkeypatch):
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    def _boom(v):
        raise RuntimeError("health blew up")
    monkeypatch.setattr(lifecycle, "_setup_health", _boom)
    # Must not raise; just no cue.
    daemon.handle_message(_ss("s1"))
    assert len(queue) == 0
