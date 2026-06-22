"""One atomic JSON writer: temp file in the same dir + os.replace.

Parameterized to reproduce every current write site's behavior exactly
(indent, optional fsync, optional chmod). Streaming downloads and the
install-record writer are intentionally NOT consumers (see the Stage 3 spec).
"""
from __future__ import annotations

import json
import os


def atomic_write_json(path, data, *, indent=None, chmod=None, fsync=True) -> None:
    """Atomically write `data` as JSON to `path` via a sibling .tmp + os.replace."""
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=indent)
        fh.flush()
        if fsync:
            os.fsync(fh.fileno())
    if chmod is not None:
        os.chmod(tmp, chmod)
    os.replace(tmp, str(path))
