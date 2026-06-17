"""The daemon owns the in-process hotkey thread: run() starts it, stop() stops it,
and a fire is routed through the same handle_message() as a socket command."""
from tests.daemon_helpers import make_daemon


class _FakeHotkey:
    def __init__(self):
        self.started = None
        self.stopped = False
        self.reloaded = None

    def start(self, dispatch):
        self.started = dispatch

    def stop(self):
        self.stopped = True

    def reload(self, dispatch):
        self.reloaded = dispatch


class _FakePlatform:
    def __init__(self):
        self.hotkey = _FakeHotkey()


def test_start_hotkeys_passes_a_dispatch_callback(monkeypatch):
    pb = _FakePlatform()
    monkeypatch.setattr("sonari.platform.get_platform", lambda: pb)
    daemon = make_daemon()[0]
    daemon._start_hotkeys()
    assert callable(pb.hotkey.started)


def test_dispatch_routes_through_handle_message(monkeypatch):
    pb = _FakePlatform()
    monkeypatch.setattr("sonari.platform.get_platform", lambda: pb)
    daemon = make_daemon()[0]
    daemon._start_hotkeys()
    handled = []
    monkeypatch.setattr(daemon, "handle_message", lambda m: handled.append(m))
    pb.hotkey.started({"type": "skip"})       # simulate a hotkey fire
    assert handled == [{"type": "skip"}]


def test_stop_stops_the_hotkey_listener(monkeypatch):
    pb = _FakePlatform()
    monkeypatch.setattr("sonari.platform.get_platform", lambda: pb)
    daemon = make_daemon()[0]
    daemon._stop_hotkeys()
    assert pb.hotkey.stopped is True


def test_one_bad_hotkey_does_not_raise(monkeypatch):
    pb = _FakePlatform()
    monkeypatch.setattr("sonari.platform.get_platform", lambda: pb)
    daemon = make_daemon()[0]
    monkeypatch.setattr(daemon, "handle_message",
                        lambda m: (_ for _ in ()).throw(RuntimeError("boom")))
    daemon._dispatch_hotkey({"type": "stop"})   # swallowed, no raise


def test_reload_keymap_delegates_to_backend_reload(monkeypatch):
    # RELOAD_KEYMAP delegates to the platform backend's reload() seam (Windows:
    # thread-joined stop+start; macOS: rewrite resolved + reload hotkeyd). The
    # daemon passes its dispatch callback through.
    pb = _FakePlatform()
    monkeypatch.setattr("sonari.platform.get_platform", lambda: pb)
    monkeypatch.setattr("os.path.exists", lambda p: False)   # no kill-switch flag
    monkeypatch.delenv("SONARI_DISABLE_HOTKEYS", raising=False)
    daemon = make_daemon(foreground="fg")[0]
    daemon.handle_message({"type": "reload_keymap"})
    assert callable(pb.hotkey.reloaded)   # backend.reload(dispatch) was invoked


def test_reload_keymap_honors_kill_switch(monkeypatch):
    # With the kill switch set, reload must NOT re-register hotkeys; it just stops.
    pb = _FakePlatform()
    monkeypatch.setattr("sonari.platform.get_platform", lambda: pb)
    monkeypatch.setenv("SONARI_DISABLE_HOTKEYS", "1")
    daemon = make_daemon(foreground="fg")[0]
    daemon.handle_message({"type": "reload_keymap"})
    assert pb.hotkey.reloaded is None
    assert pb.hotkey.stopped is True
