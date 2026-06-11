"""Fake Windows modules so platform/windows/* imports + unit-tests on macOS/Linux.
install() is idempotent and uses setdefault — a no-op on real Windows."""
import sys, types, threading


def install():
    if sys.platform == "win32":
        return
    # --- winsound ---
    if "winsound" not in sys.modules:
        ws = types.ModuleType("winsound")
        ws.SND_FILENAME = 0x20000; ws.SND_ASYNC = 0x0004
        ws.SND_NODEFAULT = 0x0002; ws.SND_SYNC = 0x0000
        ws._calls = []
        ws.PlaySound = lambda sound, flags: ws._calls.append((sound, flags))
        sys.modules["winsound"] = ws
    # --- winreg ---
    if "winreg" not in sys.modules:
        wr = types.ModuleType("winreg")
        wr.HKEY_LOCAL_MACHINE = 0x80000002
        wr.OpenKey = lambda *a, **k: object()
        def _enum(key, i):
            raise OSError()
        wr.EnumKey = _enum
        sys.modules["winreg"] = wr
    # --- msvcrt (single-instance lock) ---
    # Track locked INODES (not fds): real msvcrt.locking is a system-wide
    # byte-range lock, so two handles to the SAME file conflict across
    # processes. Modelling by inode makes the cross-process test meaningful.
    if "msvcrt" not in sys.modules:
        mc = types.ModuleType("msvcrt")
        mc.LK_NBLCK = 2; mc.LK_UNLCK = 0
        mc._locked = set()
        def _locking(fd, mode, nbytes):
            import os as _os
            ino = _os.fstat(fd).st_ino
            if mode == mc.LK_NBLCK:
                if ino in mc._locked:
                    raise OSError("locked")
                mc._locked.add(ino)
            elif mode == mc.LK_UNLCK:
                mc._locked.discard(ino)
        mc.locking = _locking
        sys.modules["msvcrt"] = mc
    # --- winrt tree ---
    if "winrt" not in sys.modules:
        _install_winrt()


def _install_winrt():
    mk = lambda n: sys.modules.setdefault(n, types.ModuleType(n))
    mk("winrt"); sysmod = mk("winrt.system")
    mk("winrt.windows"); mk("winrt.windows.media")
    play = mk("winrt.windows.media.playback")
    synth = mk("winrt.windows.media.speechsynthesis")

    class Object: pass
    sysmod.Object = Object

    class SpeechAppendedSilence: DEFAULT = 0; MIN = 1
    class SpeechPunctuationSilence: DEFAULT = 0; MIN = 1
    class _Opts:
        appended_silence = 0; punctuation_silence = 0; speaking_rate = 1.0
    class _Stream: pass
    class _AsyncOp:
        def __init__(self, r): self._r = r
        def get(self): return self._r
    class _Voice:
        def __init__(self, id="HKLM\\SOFTWARE\\Microsoft\\Speech_OneCore\\en-US",
                     language="en-US", display_name="FakeVoice"):
            self.id = id; self.language = language; self.display_name = display_name
    class SpeechSynthesizer:
        all_voices = [_Voice()]; default_voice = _Voice()
        def __init__(self): self.voice = None; self.options = _Opts()
        def synthesize_text_to_stream_async(self, t): return _AsyncOp(_Stream())
        def synthesize_ssml_to_stream_async(self, t): return _AsyncOp(_Stream())
    synth.SpeechSynthesizer = SpeechSynthesizer
    synth.SpeechAppendedSilence = SpeechAppendedSilence
    synth.SpeechPunctuationSilence = SpeechPunctuationSilence

    class MediaPlayerAudioCategory: SPEECH = 3
    class MediaPlayer:
        def __init__(self): self._cb = None; self.audio_category = None
        def set_stream_source(self, s): pass
        def add_media_ended(self, cb): self._cb = cb; return 0
        def play(self):
            t = threading.Timer(0.01, lambda: self._cb and self._cb(self, None))
            t.daemon = True; t.start()
        def pause(self): pass
        def close(self): pass
    play.MediaPlayer = MediaPlayer
    play.MediaPlayerAudioCategory = MediaPlayerAudioCategory
