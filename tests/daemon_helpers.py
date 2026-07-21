from sonari.queue import SpeechQueue
from sonari.sessions import SessionManager
from sonari.daemon import SpeechDaemon
from sonari.config import DEFAULTS


class FakeSpearconCache:
    """In-memory stand-in for SpearconCache. `available` maps a folder -> a fake
    cached audio path (a HIT); everything else is a MISS (returns None and records
    the request as a generation kick)."""

    def __init__(self):
        self.available: dict[str, str] = {}
        self.requested: list[str] = []
        self.generated: list[str] = []
        self.pregenerated: list[str] = []
        self.cleaned = None

    def get(self, label):
        self.requested.append(label)
        hit = self.available.get(label)
        if hit is None:
            self.generated.append(label)
        return hit

    def generate(self, label):
        self.generated.append(label)

    def pregenerate(self, labels):
        self.pregenerated.extend(labels)

    def cleanup(self, max_files=256):
        self.cleaned = max_files


class FakeSpeaker:
    """Records every Speaker call instead of touching audio."""

    def __init__(self):
        self.spoken: list[str] = []
        self.audio_paths: list = []
        self.earcons: list[str] = []
        self.cancels: int = 0
        self.rates: list[int] = []
        self.voices: list = []
        self.spoken_voices: list = []
        self.complete = True          # next speak() reports completed?
        self._epoch = 0
        self.pitches: list[str] = []  # pitch(direction) calls
        self.earcon_seqs: list = []
        self.epochs: list = []        # cancel_epoch passed to each speak() call

    def speak(self, text=None, audio_path=None, cancel_epoch=None, voice=None) -> bool:
        self.spoken.append(text)
        self.audio_paths.append(audio_path)
        self.spoken_voices.append(voice)
        self.epochs.append(cancel_epoch)
        return self.complete

    def cancel_epoch(self) -> int:
        return self._epoch

    def earcon(self, kind: str) -> None:
        self.earcons.append(kind)

    def earcon_then(self, kind: str, audio_path) -> None:
        self.earcon_seqs.append((kind, audio_path))

    def pitch(self, direction: str) -> None:
        self.pitches.append(direction)

    def transient(self, kind: str) -> None:
        # One list for ALL tone assertions: transient kinds land in `earcons`
        # so per-kind checks hold across the earcon->transient migration.
        self.earcons.append(kind)

    def pitch_asset(self, direction: str) -> "str | None":
        if direction not in ("up", "down"):
            return None
        return "/pitch/{0}.wav".format(direction)

    def cancel(self) -> None:
        self.cancels += 1
        self._epoch += 1

    def set_rate(self, r: int) -> None:
        self.rates.append(r)

    def set_voice(self, v) -> None:
        self.voices.append(v)


class FakeSummarizer:
    """Records the slice text; returns a scripted SummarizeResult (default: ok)."""
    def __init__(self, result=None):
        self.result = result
        self.calls: list = []

    def summarize(self, slice_text, timeout_s=30.0, cancel=None):
        self.calls.append(slice_text)
        if self.result is not None:
            return self.result
        from sonari.summarizer import SummarizeResult
        return SummarizeResult.ok("Fake summary.")


def make_daemon(verbosity: str = "everything", foreground: "str | None" = "fg",
                 summarizer=None):
    """Build a SpeechDaemon. The returned `queue` is the FOREGROUND session's own
    stream queue (where its items now land and where the loop drains), so most
    single-session tests need no change. Use stream_queue() for other sessions."""
    speaker = FakeSpeaker()
    sessions = SessionManager()
    if foreground is not None:
        sessions.set_foreground(foreground)
    config = {k: (v.copy() if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
    config["verbosity"] = verbosity
    config["summarizer"] = "off"      # SP5: no test may ever reach a real `claude`
    daemon = SpeechDaemon(speaker, sessions, config, spearcons=FakeSpearconCache(),
                          summarizer=summarizer)
    daemon._voices_provider = lambda: []      # SP5: hermetic renders — no `say -v ?`
    queue = daemon._stream(foreground).queue if foreground is not None else SpeechQueue()
    return daemon, queue, speaker, sessions, config


def stream_queue(daemon, session: str):
    """The per-session speech queue, for assertions on a non-foreground session."""
    return daemon._stream(session).queue
