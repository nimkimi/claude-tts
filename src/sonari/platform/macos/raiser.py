"""macOS focus-follow backend. Terminal.app -> exec the sonari-raise helper
(AppleScript, holds the Automation grant). iTerm2 -> open an iterm2:///reveal URL
(no grant needed). Everything else -> unsupported."""
from __future__ import annotations

import os
import subprocess

from sonari import paths
from sonari.platform.contracts import RaiseBackend
from sonari.platform.macos._helpers import build_swift_binary

_HELPER_TIMEOUT = 6.0


class MacRaiseBackend(RaiseBackend):
    # --- injectable seams (overridden in tests) ---
    def _run(self, argv, timeout=None):
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)

    def _helper_exists(self) -> bool:
        return os.path.exists(str(paths.RAISE_BIN_PATH))

    # --- capability ---
    def supports(self, identity) -> bool:
        if identity is None:
            return False
        tp = identity.term_program
        if tp == "Apple_Terminal":
            return bool(identity.tty)
        if tp == "iTerm.app":
            return bool(identity.iterm_session_id)
        return False

    # --- the raise ---
    def raise_session(self, identity) -> bool:
        if not self.supports(identity):
            return False
        try:
            if identity.term_program == "Apple_Terminal":
                if not self._helper_exists():
                    return False
                rc = self._run([str(paths.RAISE_BIN_PATH), identity.tty],
                               timeout=_HELPER_TIMEOUT).returncode
                return rc == 0
            if identity.term_program == "iTerm.app":
                # The iterm2:///reveal URL lands on the wrong session on macOS
                # Tahoe; route through the helper's validated AppleScript recipe
                # (--iterm), like Terminal. The helper strips to the bare GUID.
                if not self._helper_exists():
                    return False
                rc = self._run([str(paths.RAISE_BIN_PATH), "--iterm",
                                identity.iterm_session_id],
                               timeout=_HELPER_TIMEOUT).returncode
                return rc == 0
        except Exception:  # noqa: BLE001 - never raise/hang the raise thread
            return False
        return False

    # --- build (mirror MacHotkeyBackend.build: skip if source unchanged to keep
    #     the Automation grant, which is keyed to the binary's cdhash) ---
    def build(self):
        src = os.path.join(paths.repo_root(), "hotkeyd", "sonari-raise.swift")
        hash_path = str(paths.SONARI_DIR / ".raise.srchash")
        return build_swift_binary(
            src, paths.RAISE_BIN_PATH, hash_path,
            "sonari-raise", "the Automation grant")

    # --- diagnostics ---
    def doctor_rows(self) -> "list":
        built = self._helper_exists()
        return [("focus-follow helper", built,
                 str(paths.RAISE_BIN_PATH) if built
                 else "not built; run 'sonari install'")]
