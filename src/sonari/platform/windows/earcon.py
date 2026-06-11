"""Windows earcon backend — winsound.PlaySound with poll()-able handles.

winsound is Windows-only; imported lazily (guarded try/except ModuleNotFoundError)
so this module is importable on macOS/Linux for tests via the _winfakes harness.
"""
from __future__ import annotations

import pathlib

from sonari.platform.base import EarconBackend

# winsound is Windows-only; imported lazily so the module is importable
# on macOS/Linux (for tests / dev).
try:
    import winsound as _winsound
    _SND_FLAGS = _winsound.SND_FILENAME | _winsound.SND_ASYNC  # 0x20004 = 131076
except ModuleNotFoundError:  # non-Windows; only reached at runtime, not import-time
    _winsound = None  # type: ignore[assignment]
    _SND_FLAGS = 0


class _DoneHandle:
    """Returned on a successful play() call.

    winsound.PlaySound(..., SND_ASYNC) hands the audio buffer to the Win32
    multimedia scheduler and returns immediately — there is no OS-level
    process or thread handle exposed to Python.  poll() therefore returns 0
    (POSIX convention: exited normally) immediately, which satisfies the
    EarconBackend contract (caller may call .poll() to check completion).

    CAVEAT — single-channel truncation:
    If you supply a stereo (2-channel) WAV, Windows mixes it down silently;
    you will NOT get a RuntimeError.  However, non-standard PCM variants
    (float32, 24-bit int, ADPCM) cause PlaySound to return False or raise
    RuntimeError on some Windows builds.  Always generate 16-bit integer
    PCM at 44100 Hz (what generate_earcon() produces).

    CAVEAT — concurrent calls:
    Each new SND_ASYNC call stops the previous one.  Do NOT delete the .wav
    file immediately after play(); the Win32 scheduler still holds a handle
    to it for the duration of playback.
    """
    def poll(self) -> int:
        return 0  # immediately "done" from Python's perspective


class _MissingHandle:
    """Returned when the .wav path does not exist."""
    def poll(self) -> None:
        return None


class WinEarconBackend(EarconBackend):
    """Earcon backend for Windows using winsound.PlaySound."""

    def play(self, path: str) -> _DoneHandle | _MissingHandle:
        """Play *path* asynchronously via winsound.

        Returns a handle whose .poll() mimics subprocess.Popen.poll():
          0    -> sound was dispatched successfully
          None -> file was missing (nothing played)

        Raises RuntimeError (from winsound itself) only if Windows cannot
        open the audio device, which is distinct from a missing file.
        """
        if not pathlib.Path(path).exists():
            return _MissingHandle()
        # Re-import at call time so that the _winfakes harness injected in
        # conftest.py is always picked up — even if the module-level try/except
        # ran before the fakes were installed.
        try:
            import winsound as _ws
        except ModuleNotFoundError:
            _ws = _winsound  # type: ignore[assignment]
        flags = _ws.SND_FILENAME | _ws.SND_ASYNC
        _ws.PlaySound(path, flags)
        return _DoneHandle()

    def default_earcons(self) -> dict:
        """Return the platform's default {kind: sound_path} mapping."""
        from sonari.platform.windows.earcons import default_earcons
        return default_earcons()
