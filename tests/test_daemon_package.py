from __future__ import annotations

import runpy
from pathlib import Path

import sonari.daemon.bootstrap as bootstrap

_SRC = Path(__file__).resolve().parent.parent / "src" / "sonari" / "daemon"
_FUTURE = "from __future__ import annotations"


def test_public_reexports_importable():
    # Back-compat floor: external importers (client.py, cli.py, daemon_helpers,
    # the e2e + speaker-cancel tests) all do `from sonari.daemon import <name>`.
    # The package __init__ must keep these three names resolvable.
    from sonari.daemon import SpeechDaemon, main, ensure_running

    assert callable(main)
    assert callable(ensure_running)
    assert isinstance(SpeechDaemon, type)


def test_dash_m_entrypoint_dispatches_to_bootstrap_main(monkeypatch):
    # `python -m sonari.daemon` (bin/sonari-daemon:14, macos/supervisor.py:163)
    # runs daemon/__main__.py, which must call bootstrap.main(). The suite does
    # not otherwise exec this path, so without this pin the package could ship
    # with a broken entrypoint and a green suite. In-process via runpy with a
    # patched main (a real subprocess would bind a socket and run forever).
    calls = []
    monkeypatch.setattr(bootstrap, "main", lambda: calls.append(1))
    runpy.run_module("sonari.daemon", run_name="__main__")
    assert calls == [1]


def test_new_package_modules_declare_future_annotations():
    # test_py39_compat scans src/sonari/ NON-recursively (os.listdir), so it does
    # not reach daemon/ submodules. Pin the Python-3.9 convention for the package
    # here: every module's first code line must be the future-annotations import.
    for name in ("host.py", "bootstrap.py", "__init__.py", "__main__.py",
                 "registry.py", "context.py", "state.py", "server.py",
                 "limits.py",
                 "features/__init__.py", "features/control.py"):
        text = (_SRC / name).read_text(encoding="utf-8")
        first = next(
            line.strip() for line in text.splitlines()
            if line.strip() and not line.strip().startswith("#")
        )
        assert first == _FUTURE, f"{name}: first code line must be {_FUTURE!r}"


def test_control_handlers_registered_in_features_control():
    # Each lifted handler must resolve to a function defined in
    # features.control, not the old host thunk — proves @handler decorators
    # in features/control.py actually ran and replaced the thunks.
    from sonari.daemon import registry
    from sonari.protocol import MsgType

    expected_module = "sonari.daemon.features.control"
    for key in (MsgType.SET_RATE, MsgType.SET_VOICE, MsgType.SET_VERBOSITY,
                MsgType.SET_MINQUEUE, MsgType.CYCLE_VERBOSITY,
                MsgType.STATUS, MsgType.PING):
        fn = registry.HANDLERS[key]
        assert fn.__module__ == expected_module, (
            f"HANDLERS[{key!r}].__module__ == {fn.__module__!r}, want {expected_module!r}"
        )
