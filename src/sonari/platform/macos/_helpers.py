"""Shared macOS plist-escape + Swift-compile (were duplicated across the macOS backends)."""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess


def xml_escape(s: str) -> str:
    """Escape the three XML-significant characters for safe plist interpolation."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_swift_binary(src, out, hash_path, src_label, unchanged_note):
    """Compile `src` -> `out` with swiftc if present and the source changed.
    Skips recompile (preserving any OS permission grant) when the source hash
    is unchanged. Returns (ok: bool, detail: str)."""
    if shutil.which("swiftc") is None:
        return (False, "swiftc not found")
    try:
        with open(src, "rb") as fh:
            src_hash = hashlib.sha256(fh.read()).hexdigest()
    except OSError as exc:
        return (False, "cannot read {0} source: {1}".format(src_label, exc))
    if os.path.exists(str(out)):
        try:
            with open(hash_path, "r", encoding="utf-8") as fh:
                if fh.read().strip() == src_hash:
                    return (True, "{0} (unchanged; kept to preserve {1})".format(
                        out, unchanged_note))
        except OSError:
            pass
    rc = subprocess.call(["swiftc", src, "-o", str(out)])
    if rc == 0:
        try:
            with open(hash_path, "w", encoding="utf-8") as fh:
                fh.write(src_hash)
        except OSError:
            pass
        return (True, str(out))
    return (False, "swiftc exited {0}".format(rc))
