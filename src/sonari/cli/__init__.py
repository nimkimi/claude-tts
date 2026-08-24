"""Sonari command-line interface.

Subcommands fall into two groups:
  * control  -> build a protocol message and hand it to sonari.client.send
  * local    -> doctor / install / uninstall / daemon (run in-process)

main(argv) returns an int exit code. Heavy imports (client, daemon) are done
inside the handlers so the module imports cheaply and is easy to patch in tests.
"""
from __future__ import annotations

import argparse
import sys
from typing import Optional

from .. import paths
from .. import keymap
from sonari.platform import get_platform

_PLATFORM = None


def _platform():
    """Return the cached PlatformBackend for this OS (the only OS dispatch point)."""
    global _PLATFORM
    if _PLATFORM is None:
        _PLATFORM = get_platform()
    return _PLATFORM


VERBOSITY_CHOICES = ("everything", "medium", "quiet")


def _send(msg: dict, expect_reply: bool = False):
    from .. import client  # local import so tests can patch sonari.client.send
    return client.send(msg, expect_reply=expect_reply)


def _build_raise_helper(raise_backend) -> None:
    """Build the sonari-raise helper. macOS asks for Automation permission the first
    time it controls a window - one-time, per app, with a safe voice fallback. Editing
    the helper changes its cdhash and DROPS the grant, so on a recompile we tell the
    user to re-allow it (focus-follow AND focus-aware navigation both depend on it)."""
    from sonari import paths
    ok, detail = raise_backend.build()
    print("focus-follow helper: {0}".format(detail))
    if not ok:
        return
    recompiled = (detail == str(paths.RAISE_BIN_PATH))
    if recompiled:
        print("focus-follow + focus-aware navigation: macOS will ask to re-allow "
              "'sonari-raise' to control Terminal/iTerm2 - click Allow (the same "
              "one-time grant).")
    else:
        print("focus-follow: the first time you jump into a Terminal or iTerm2 "
              "window, macOS will ask to allow 'sonari-raise' to control it - "
              "click Allow. One-time, per app.")


def _daemon_not_running_message() -> str:
    return "Sonari daemon is not running. Run: sonari install"


from . import control
from . import doctor as doctor_cmd
from . import install as install_cmd


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sonari",
                                description="Sonari eyes-free TTS for Claude Code")
    sub = p.add_subparsers(dest="command")

    sub.add_parser("status", help="print daemon status").set_defaults(
        func=control._cmd_status)

    sp = sub.add_parser("verbosity", help="set verbosity level")
    sp.add_argument("level", choices=VERBOSITY_CHOICES)
    sp.set_defaults(func=control._cmd_verbosity)

    sp = sub.add_parser("rate", help="set words-per-minute speech rate")
    sp.add_argument("wpm", type=int)
    sp.set_defaults(func=control._cmd_rate)

    sp = sub.add_parser("voice", help="set the say voice (omit name to list voices)")
    sp.add_argument("name", nargs="*", help="voice name; omit to list installed voices")
    sp.set_defaults(func=control._cmd_voice)

    sp = sub.add_parser(
        "minqueue", help="items to batch before reading (1 = read immediately)")
    sp.add_argument("n", type=int)
    sp.set_defaults(func=control._cmd_minqueue)

    sp = sub.add_parser(
        "keepalive",
        help="Hold the audio device open while sessions are live (fixes Bluetooth clipping)")
    sp.add_argument("state", choices=["on", "off"])
    sp.set_defaults(func=control._cmd_keepalive)

    sub.add_parser("stop", help="stop all speech and clear the queue").set_defaults(
        func=control._cmd_stop)
    sub.add_parser("skip", help="skip the current item").set_defaults(
        func=control._cmd_skip)

    # Local subcommands are registered in later tasks via _register_local(sub).
    _register_local(sub)
    return p


def _resolve_python():
    """Resolve the best Python >= 3.9 via the platform supervisor."""
    return _platform().supervisor.resolve_python()


def _cmd_daemon(_args) -> int:
    from .. import daemon
    daemon.main()
    return 0


def _register_local(sub) -> None:
    """Register local (non-control) subcommands."""
    dp = sub.add_parser("doctor", help="run health checks")
    dp.add_argument("--speak", action="store_true",
                    help="speak the verdict even when output is piped")
    dp.add_argument("--quiet", action="store_true",
                    help="never speak the verdict")
    dp.set_defaults(func=doctor_cmd._cmd_doctor)
    sub.add_parser("install", help="install the LaunchAgent + SONARI_DIR").set_defaults(
        func=install_cmd._cmd_install)
    up = sub.add_parser("uninstall",
                        help="remove Sonari (LaunchAgents, launcher, runtime files)")
    # default=None means "ask" (spec §8.1 step 1); the flags are for scripts and
    # for anyone who would rather not be asked.
    up.add_argument("--purge-transcripts", dest="purge", action="store_true", default=None)
    up.add_argument("--keep-transcripts", dest="purge", action="store_false")
    up.set_defaults(func=install_cmd._cmd_uninstall)
    sub.add_parser("daemon", help="run the speech daemon in the foreground").set_defaults(
        func=_cmd_daemon)
    sp = sub.add_parser(
        "keymap",
        help="list hotkey bindings (incl. unbound); '<action> clear' to unbind")
    sp.add_argument("action", nargs="?", help="action to unbind")
    sp.add_argument("value", nargs="?", help="'clear' or 'none' to unbind the action")
    sp.set_defaults(func=control._cmd_keymap)
    vp = sub.add_parser("voices", help="install/remove neural (Kokoro) voices")
    vsub = vp.add_subparsers(dest="voices_command")
    vsub.add_parser("install", help="provision neural voices").set_defaults(
        func=install_cmd._cmd_voices_install)
    vsub.add_parser("uninstall", help="remove neural voices").set_defaults(
        func=install_cmd._cmd_voices_uninstall)
    vp.set_defaults(func=lambda _a: (vp.print_help() or 2))


def main(argv: Optional[list] = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    try:
        return args.func(args)
    except OSError as exc:
        from ..client import DaemonNotRunning  # local import; client may not be loaded
        if isinstance(exc, DaemonNotRunning):
            print(_daemon_not_running_message(), file=sys.stderr)
            return 1
        raise
