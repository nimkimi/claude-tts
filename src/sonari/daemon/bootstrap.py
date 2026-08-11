from __future__ import annotations

import os
import subprocess
import threading

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
        # Import FAULTLOG_PATH LIVE (not at module top) so the conftest monkeypatch /
        # any SONARI_DIR redirection takes effect; a top-level import would freeze
        # the value before tests patch it and leak into the real ~/.sonari.
        from sonari.paths import FAULTLOG_PATH
        path = str(FAULTLOG_PATH)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # mode 'w': only the latest run's crash matters; never grow unbounded.
        _FAULT_FILE = open(path, "w", encoding="utf-8")
        _FAULT_FILE.write("=== faulthandler armed: pid {0} ===\n".format(os.getpid()))
        _FAULT_FILE.flush()
        faulthandler.enable(file=_FAULT_FILE, all_threads=True)
        faulthandler.register(signal.SIGUSR1, file=_FAULT_FILE, all_threads=True, chain=False)
    except Exception:  # noqa: BLE001 - diagnostics must never break startup
        pass


# W8 (spec §9): spoken once per daemon boot, at every verbosity — a trust cue,
# not narration. Exported as a constant so the test imports the exact string.
BOOT_CUE = "Sonari restarted. Sessions re-register on their next prompt."


def _start_boot_cue(speaker) -> None:
    """Speak the restart trust cue on a one-shot daemon thread (W8). The cue
    CANNOT ride the queue: at boot no session is registered, the speak loop
    plays only speaker()'s stream and keep-going scans only registered sessions
    — an enqueued boot cue would never voice. A direct thread keeps the socket
    bind (which lazy-start clients poll) unblocked; the overlap window with the
    first real utterance is human-timescale-empty (sessions re-register on
    their next prompt). Never raises."""
    def _run() -> None:
        try:
            speaker.speak(BOOT_CUE)
        except Exception:  # noqa: BLE001 - the cue must never break startup
            pass

    threading.Thread(target=_run, daemon=True).start()


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
    _start_boot_cue(speaker)          # W8: restart trust cue (pre-loop, pre-session)
    daemon.run()


if __name__ == "__main__":
    main()
