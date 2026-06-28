from __future__ import annotations

import os
import subprocess

from sonari.config import load_config
from sonari.paths import SINGLETON_PATH, ensure_sonari_dir, socket_connectable
from sonari.platform import transport
from sonari.daemon.host import SpeechDaemon

# Holds the single-instance flock for this process's lifetime (see main()).
_SINGLETON = None


def ensure_running() -> None:
    if socket_connectable():
        return
    from sonari.platform import get_platform
    argv, kwargs = get_platform().supervisor.launch_spec()
    subprocess.Popen(argv, **kwargs)


_FAULT_FILE = None


def _arm_faulthandler() -> None:
    """Dump every thread's Python stack to SONARI_DIR/faulthandler.log on a NATIVE
    crash (access violation / segfault in WinRT, ctypes, or winsound) — the only
    way to see otherwise-silent C-level daemon deaths. On-demand thread dump via
    'kill -USR1 <pid>'. Never raises."""
    global _FAULT_FILE
    try:
        import faulthandler
        import signal
        # Import SONARI_DIR LIVE (not at module top) so the conftest monkeypatch /
        # any SONARI_DIR redirection takes effect; a top-level import would freeze
        # the value before tests patch it and leak into the real ~/.sonari.
        from sonari.paths import SONARI_DIR
        path = str(SONARI_DIR / "faulthandler.log")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # mode 'w': only the latest run's crash matters; never grow unbounded.
        _FAULT_FILE = open(path, "w", encoding="utf-8")
        _FAULT_FILE.write("=== faulthandler armed: pid {0} ===\n".format(os.getpid()))
        _FAULT_FILE.flush()
        faulthandler.enable(file=_FAULT_FILE, all_threads=True)
        faulthandler.register(signal.SIGUSR1, file=_FAULT_FILE, all_threads=True, chain=False)
    except Exception:  # noqa: BLE001 - diagnostics must never break startup
        pass


def main() -> None:
    _arm_faulthandler()
    # Single-instance guard. The fast path avoids work when a daemon is clearly
    # already serving. The AUTHORITATIVE guard is the exclusive flock below:
    # with an ephemeral TCP port, bind() never collides (unlike the old fixed
    # AF_UNIX path), so socket_connectable() alone is racy and lets concurrent
    # lazy-starts each bind their own port -> a daemon explosion. The flock lets
    # exactly one process win; the rest exit. The lock auto-releases on death.
    global _SINGLETON
    if socket_connectable():
        return
    ensure_sonari_dir()
    _SINGLETON = transport.acquire_singleton(SINGLETON_PATH)
    if _SINGLETON is None:
        return  # another daemon already owns the single-instance lock

    from sonari.speaker import Speaker
    from sonari.sessions import SessionManager
    from sonari.platform import get_platform

    _backend = get_platform()
    cfg = load_config()
    if "earcons" not in cfg:
        cfg["earcons"] = _backend.earcon.default_earcons()
    speaker = Speaker(
        voice=cfg.get("voice"),
        rate=cfg.get("rate", 200),
        say_runner=_backend.tts.run,
        afplay_runner=_backend.earcon.play,   # spearcon audio_path playback (same afplay)
        earcon_player=_backend.earcon.play,
        earcons=cfg.get("earcons"),
    )
    sessions = SessionManager(background_policy=cfg.get("background_policy", "earcon_only"))
    from sonari.spearcon import SpearconCache
    from sonari.paths import SONARI_DIR
    spearcons = SpearconCache(
        SONARI_DIR / "spearcons",
        voice=cfg.get("spearcon_voice", "Samantha"),
        rate=cfg.get("spearcon_rate", 525),
    )
    spearcons.cleanup()                       # prune stale cache files at daemon start
    daemon = SpeechDaemon(speaker, sessions, cfg, spearcons=spearcons)
    daemon.run()


if __name__ == "__main__":
    main()
