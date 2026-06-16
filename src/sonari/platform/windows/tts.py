"""Windows OneCore TTS backend via PyWinRT — a subprocess-like playback handle.

OneCore (Windows.Media.SpeechSynthesis) has no subprocess: it synthesizes a
stream and plays it through a MediaPlayer on a WinRT thread-pool callback.  To
fit Sonari's say_runner contract (the Speaker orchestrates a proc-like object),
run() returns a _TtsHandle whose .wait(timeout)/.terminate()/.returncode mimic
subprocess.Popen.

WINDOWS-only: every winrt.* import is LAZY (done inside the methods that need
it) so this module imports cleanly on macOS/Linux.  Tests exercise it via the
fake winrt tree injected by tests/_winfakes.py.  "Working" under the mocks is
NOT a claim that real OneCore playback works — that can only be verified on
Windows.

Requirements (Windows only):
    pip install winrt-runtime winrt-Windows.Media.SpeechSynthesis \
                winrt-Windows.Media.Playback winrt-Windows.Media.Core \
                winrt-Windows.Storage.Streams

Source pattern: pywinrt text_to_speech sample +
https://learn.microsoft.com/en-us/uwp/api/windows.media.speechsynthesis ;
https://learn.microsoft.com/en-us/uwp/api/windows.media.playback.mediaplayer
"""
from __future__ import annotations

import subprocess
import threading
from typing import Optional

from sonari.platform.base import TtsBackend

_BASELINE_WPM: float = 200.0  # Sonari's default wpm maps to SpeakingRate 1.0


def wpm_to_speaking_rate(wpm: float) -> float:
    """Map Sonari [100-400] wpm to a SpeakingRate multiplier [0.5-6.0].

    SpeakingRate is a multiplier, not an absolute wpm; values outside
    [0.5, 6.0] raise on real WinRT, so we always clamp.
    """
    return max(0.5, min(6.0, wpm / _BASELINE_WPM))


class _TtsHandle:
    """Subprocess-like handle for in-flight TTS playback.

    returncode: None while playing, 0 = completed normally, 1 = interrupted.
    Holds GC-refs to the stream/synth/callback so COM's underlying WinRT
    objects are not released while playback is still in flight.
    """

    def __init__(self, player, stream, synth):
        self._player = player
        self._stream = stream    # GC-ref: must outlive playback
        self._synth = synth      # GC-ref: must outlive stream
        self._done = threading.Event()
        self.returncode: Optional[int] = None

        def _on_media_ended(sender, args) -> None:
            if self.returncode is None:
                self.returncode = 0
            self._done.set()

        self._cb = _on_media_ended   # prevent GC of callback closure
        self._token = player.add_media_ended(_on_media_ended)

    def wait(self, timeout: Optional[float] = None) -> int:
        completed = self._done.wait(timeout=timeout)
        if not completed:
            raise subprocess.TimeoutExpired(cmd="onecore-tts", timeout=timeout)
        return self.returncode

    def terminate(self) -> None:
        if self.returncode is None:
            self.returncode = 1
        try:
            self._player.pause()
            self._player.close()
        except Exception:
            pass
        self._done.set()

    def poll(self) -> Optional[int]:
        """subprocess-like: returncode if finished, else None."""
        return self.returncode


class WinTtsBackend(TtsBackend):
    """OneCore TTS backend. All winrt.* imports are lazy (inside methods)."""

    def list_voices(self) -> list:
        """Return all installed VoiceInformation objects (may be empty)."""
        from winrt.windows.media.speechsynthesis import SpeechSynthesizer
        return list(SpeechSynthesizer.all_voices)

    def _best_voice_info(self, lang_prefix: str = "en-US"):
        """Select a VoiceInformation in priority order:
          1. en-US OneCore (Neural/HQ) — Id path contains 'Speech_OneCore'
          2. Any en-US voice
          3. System default_voice
        Raises RuntimeError if no voices are installed at all.

        Internal: returns the WinRT object (assigned to synth.voice). The public
        ABC method best_voice() returns its display NAME (str).
        """
        from winrt.windows.media.speechsynthesis import SpeechSynthesizer
        voices = self.list_voices()
        if not voices:
            raise RuntimeError(
                "No TTS voices installed. Add a Speech language pack in "
                "Settings -> Time & language -> Speech -> Add voices."
            )
        ll = lang_prefix.lower()

        def _is_onecore(v) -> bool:
            return "speech_onecore" in (v.id or "").lower()

        for v in voices:
            if v.language.lower().startswith(ll) and _is_onecore(v):
                return v
        for v in voices:
            if v.language.lower().startswith(ll):
                return v
        return SpeechSynthesizer.default_voice

    def best_voice(self, lang_prefix: str = "en-US") -> str:
        """ABC contract: return the best installed voice's display NAME (str)."""
        return self._best_voice_info(lang_prefix).display_name

    def _resolve_voice(self, name):
        """Resolve a Sonari config voice-NAME (or None) to a VoiceInformation.

        Speaker passes the configured voice as a display-name string or None,
        but synth.voice requires a VoiceInformation object.  If *name* is a
        non-empty string, match it against all_voices by display_name
        (case-insensitive); if not found (e.g. a stale macOS name like
        "Samantha"), or if *name* is None/empty, fall back to best_voice().
        """
        if name:
            for v in self.list_voices():
                if (v.display_name or "").lower() == str(name).lower():
                    return v
        return self._best_voice_info()

    def run(self, text: str, voice, rate: int):
        """Synthesize *text* and begin playback immediately.

        Returns a _TtsHandle the caller can .wait()/.terminate()/.poll().

        Args:
            text:  Utterance to speak.
            voice: configured voice NAME (str) or None — resolved internally.
            rate:  Sonari wpm (int) — mapped via wpm_to_speaking_rate().
        """
        from winrt.windows.media.speechsynthesis import (
            SpeechSynthesizer,
            SpeechAppendedSilence,
            SpeechPunctuationSilence,
        )
        from winrt.windows.media.playback import (
            MediaPlayer,
            MediaPlayerAudioCategory,
        )

        speaking_rate = wpm_to_speaking_rate(rate)

        # Resolve the voice BEFORE constructing the synthesizer. On a box with
        # no OneCore voices, SpeechSynthesizer() activation itself throws a
        # cryptic FileNotFoundError (WinError -2147024894); resolving first lets
        # best_voice() raise the actionable "install a voice" message instead.
        resolved_voice = self._resolve_voice(voice)

        synth = SpeechSynthesizer()
        synth.voice = resolved_voice

        opts = synth.options
        opts.appended_silence = SpeechAppendedSilence.MIN
        opts.punctuation_silence = SpeechPunctuationSilence.MIN

        use_ssml = False
        try:
            opts.speaking_rate = float(speaking_rate)
        except AttributeError:
            # Win10 < 1709: speaking_rate not available; fall back to SSML.
            pct = int(speaking_rate * 100)
            safe_txt = (
                text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
            )
            text = (
                '<speak version="1.0" '
                'xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="en-US">'
                f'<prosody rate="{pct}%">{safe_txt}</prosody>'
                '</speak>'
            )
            use_ssml = True

        if use_ssml:
            stream = synth.synthesize_ssml_to_stream_async(text).get()
        else:
            stream = synth.synthesize_text_to_stream_async(text).get()

        player = MediaPlayer()
        player.audio_category = MediaPlayerAudioCategory.SPEECH
        player.set_stream_source(stream)

        handle = _TtsHandle(player=player, stream=stream, synth=synth)
        player.play()
        return handle
