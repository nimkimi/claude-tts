"""macOS focus-follow backend. Terminal.app -> exec the sonari-raise helper
(AppleScript, holds the Automation grant). iTerm2 -> open an iterm2:///reveal URL
(no grant needed). Everything else -> unsupported."""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess

from sonari import paths
from sonari.platform.base import RaiseBackend

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

    # --- permission ---
    def check_grant(self, term_program="Apple_Terminal") -> str:
        # The iTerm2 AppleScript path needs its own Automation grant (separate
        # from Terminal's), so probe the terminal the user is actually in: an
        # iTerm user must not be prompted for Terminal, and vice-versa. The
        # default keeps existing no-arg callers probing Terminal.
        if not self._helper_exists():
            return "unknown"
        flag = "--check-iterm" if term_program == "iTerm.app" else "--check"
        try:
            rc = self._run([str(paths.RAISE_BIN_PATH), flag],
                           timeout=_HELPER_TIMEOUT).returncode
        except Exception:  # noqa: BLE001
            return "unknown"
        if rc == 0:
            return "granted"
        if rc == 3:
            return "denied"
        return "unknown"

    # --- build (mirror MacHotkeyBackend.build: skip if source unchanged to keep
    #     the Automation grant, which is keyed to the binary's cdhash) ---
    def build(self):
        if shutil.which("swiftc") is None:
            return (False, "swiftc not found")
        src = os.path.join(paths.repo_root(), "hotkeyd", "sonari-raise.swift")
        try:
            with open(src, "rb") as fh:
                src_hash = hashlib.sha256(fh.read()).hexdigest()
        except OSError as exc:
            return (False, "cannot read sonari-raise source: {0}".format(exc))
        hash_path = str(paths.SONARI_DIR / ".raise.srchash")
        if os.path.exists(str(paths.RAISE_BIN_PATH)):
            try:
                with open(hash_path, "r", encoding="utf-8") as fh:
                    if fh.read().strip() == src_hash:
                        return (True, "{0} (unchanged; kept to preserve the "
                                "Automation grant)".format(paths.RAISE_BIN_PATH))
            except OSError:
                pass
        rc = subprocess.call(["swiftc", src, "-o", str(paths.RAISE_BIN_PATH)])
        if rc == 0:
            try:
                with open(hash_path, "w", encoding="utf-8") as fh:
                    fh.write(src_hash)
            except OSError:
                pass
            return (True, str(paths.RAISE_BIN_PATH))
        return (False, "swiftc exited {0}".format(rc))

    # --- diagnostics ---
    def doctor_rows(self, term_program=None) -> "list":
        # When the caller knows the terminal (TERM_PROGRAM), probe and describe
        # that terminal's grant; otherwise keep the Terminal default so existing
        # no-arg callers are unchanged.
        rows = []
        built = self._helper_exists()
        rows.append(("focus-follow helper", built,
                     str(paths.RAISE_BIN_PATH) if built
                     else "not built; run 'sonari install'"))
        if built:
            app = "iTerm2" if term_program == "iTerm.app" else "Terminal"
            grant = self.check_grant(term_program)  # None -> Terminal default
            ok = grant == "granted"
            detail = {
                "granted": "Automation granted",
                "denied": "Automation denied — allow 'sonari-raise' to control "
                          "{0} in System Settings > Privacy & Security > "
                          "Automation".format(app),
                "unknown": "grant unknown ({0} not running, or not yet "
                           "granted)".format(app),
            }.get(grant, grant)
            rows.append(("focus-follow permission", ok, detail))
        return rows
