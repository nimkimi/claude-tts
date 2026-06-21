from __future__ import annotations

import threading

from sonari.protocol import MsgType
from sonari.daemon.registry import handler


@handler(MsgType.RELOAD_KEYMAP)
def on_reload_keymap(ctx, msg):
    # keymap.json changed (e.g. an unbind): re-register hotkeys so it takes
    # effect without a daemon restart. Run it OFF the daemon lock: this
    # handler is invoked while holding self._lock, but _reload_hotkeys joins
    # the Windows hotkey pump thread, which itself needs self._lock to
    # dispatch a fire. Joining under the lock could stall the daemon up to
    # the join timeout and, on timeout, leave an orphaned thread that
    # re-creates the H2 dark-hotkey race. A short-lived thread does the
    # reload lock-free (and _reload_lock serializes concurrent reloads).
    threading.Thread(target=ctx.host._reload_hotkeys,
                     name="sonari-keymap-reload", daemon=True).start()
    return None
