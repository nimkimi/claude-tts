from sonari.queue import SpeechQueue
from sonari.sessions import SessionManager
from sonari.daemon import SpeechDaemon
from sonari.config import DEFAULTS


class FakeSpeaker:
    """Records every Speaker call instead of touching audio."""

    def __init__(self):
        self.spoken: list[str] = []
        self.earcons: list[str] = []
        self.cancels: int = 0
        self.rates: list[int] = []
        self.voices: list = []
        self.complete = True          # next speak() reports completed?
        self._epoch = 0
        self.pitches: list[str] = []  # pitch(direction) calls

    def speak(self, text: str, cancel_epoch=None) -> bool:
        self.spoken.append(text)
        return self.complete

    def cancel_epoch(self) -> int:
        return self._epoch

    def earcon(self, kind: str) -> None:
        self.earcons.append(kind)

    def pitch(self, direction: str) -> None:
        self.pitches.append(direction)

    def cancel(self) -> None:
        self.cancels += 1
        self._epoch += 1

    def set_rate(self, r: int) -> None:
        self.rates.append(r)

    def set_voice(self, v) -> None:
        self.voices.append(v)


def make_daemon(verbosity: str = "everything", foreground: "str | None" = "fg"):
    """Build a SpeechDaemon. The returned `queue` is the FOREGROUND session's own
    stream queue (where its items now land and where the loop drains), so most
    single-session tests need no change. Use stream_queue() for other sessions."""
    speaker = FakeSpeaker()
    sessions = SessionManager()
    if foreground is not None:
        sessions.set_foreground(foreground)
    config = {k: (v.copy() if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
    config["verbosity"] = verbosity
    daemon = SpeechDaemon(speaker, sessions, config)
    queue = daemon._stream(foreground).queue if foreground is not None else SpeechQueue()
    return daemon, queue, speaker, sessions, config


def stream_queue(daemon, session: str):
    """The per-session speech queue, for assertions on a non-foreground session."""
    return daemon._stream(session).queue
