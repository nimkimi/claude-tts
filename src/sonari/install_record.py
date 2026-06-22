"""The single install.json reader/writer (was duplicated cli + daemon lifecycle).

read_install_record() resolves the genuine cli<->lifecycle duplication.
write_install_record() is added in the install task (it has only the cli caller)
and is moved verbatim — a plain write, NOT routed through atomic_write_json, so
install.json's bytes are unchanged.
"""
from __future__ import annotations

import json
import os

from sonari.paths import INSTALL_RECORD_PATH


def read_install_record():
    """Return the install.json dict, or None if unreadable/absent. Never raises."""
    try:
        with open(str(INSTALL_RECORD_PATH), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001 - install-record must never raise
        return None


def write_install_record(python, python_version, plugin_root, app_path,
                         plugin_version) -> None:
    """Persist the durable install record used by doctor + session-start health.
    Moved verbatim from cli._write_install_record — plain write + trailing newline."""
    from datetime import datetime, timezone
    record = {
        "python": python,
        "python_version": python_version,
        "app_path": app_path,
        "plugin_root": plugin_root,
        "plugin_version": plugin_version,
        "installed_at": datetime.now(timezone.utc).isoformat(),
    }
    os.makedirs(os.path.dirname(str(INSTALL_RECORD_PATH)), exist_ok=True)
    with open(str(INSTALL_RECORD_PATH), "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2)
        f.write("\n")
