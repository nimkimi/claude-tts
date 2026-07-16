"""W8 (spec §9): the daemon announces its own restart. Direct one-shot speaker
thread — an enqueued cue would never voice (no registered sessions at boot; the
loop plays only speaker()'s stream and keep-going scans only registered ids)."""
import threading
import time

import sonari.daemon.bootstrap as bootstrap


def test_boot_cue_exact_string_spoken_once():
    spoken = []

    class _Spk:
        def speak(self, text=None, audio_path=None, cancel_epoch=None):
            spoken.append(text)
            return True

    bootstrap._start_boot_cue(_Spk())
    deadline = time.time() + 2.0
    while not spoken and time.time() < deadline:
        time.sleep(0.01)
    assert spoken == ["Sonari restarted. Sessions re-register on their next prompt."]


def test_boot_cue_start_is_non_blocking():
    release = threading.Event()

    class _Blocking:
        def speak(self, text=None, audio_path=None, cancel_epoch=None):
            release.wait(2.0)
            return True

    t0 = time.monotonic()
    bootstrap._start_boot_cue(_Blocking())
    assert time.monotonic() - t0 < 0.5             # returned while speak() still blocked
    release.set()


def test_main_wires_the_cue_before_run(monkeypatch):
    order = []
    monkeypatch.setattr(bootstrap, "_arm_faulthandler", lambda: None)
    monkeypatch.setattr(bootstrap, "socket_connectable", lambda: False)
    monkeypatch.setattr(bootstrap, "ensure_sonari_dir", lambda: None)
    monkeypatch.setattr(bootstrap.transport, "acquire_singleton", lambda p: object())
    monkeypatch.setattr(bootstrap, "load_config", lambda: {"earcons": {}})

    class _FakePlat:
        class tts:  # noqa: D106 - attribute container
            run = staticmethod(lambda *a, **k: None)

        class earcon:  # noqa: D106
            play = staticmethod(lambda *a, **k: None)
            default_earcons = staticmethod(lambda: {})

    monkeypatch.setattr("sonari.platform.get_platform", lambda: _FakePlat)

    class _FakeCache:
        def __init__(self, *a, **k):
            pass

        def cleanup(self):
            pass

    monkeypatch.setattr("sonari.spearcon.SpearconCache", _FakeCache)

    class _FakeDaemon:
        def __init__(self, *a, **k):
            pass

        def run(self):
            order.append("run")

    monkeypatch.setattr(bootstrap, "SpeechDaemon", _FakeDaemon)
    monkeypatch.setattr(bootstrap, "_start_boot_cue", lambda spk: order.append("cue"))

    bootstrap.main()
    assert order == ["cue", "run"]                 # cue armed, THEN the daemon serves
