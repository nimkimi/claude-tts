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


# The canonical default asset table, as a fresh install would see it. R3 moves
# this table into config.DEFAULTS; this ONE function is the seam, so that move
# is a one-line change here and no test has to know where the table lives.
def _default_earcons() -> dict:
    from sonari.platform.macos.earcon import _DEFAULTS
    return dict(_DEFAULTS)


# Every FakeSpeaker built during the CURRENT test. conftest's autouse
# _no_silent_cues fixture drains this and fails the test if any of them
# recorded a cue that would have made no sound.
_LIVE_FAKE_SPEAKERS: list = []


class FakeSpeaker:
    """Records every Speaker call instead of touching audio."""

    def __init__(self, earcons=None):
        self.spoken: list[str] = []
        self.audio_paths: list = []
        self.earcons: list[str] = []       # kinds that WOULD have played
        self.earcon_paths: list[str] = []  # the asset each resolved to
        self.silent_cues: list[str] = []   # kinds that resolved to NOTHING
        self.cancels: int = 0
        self.rates: list[int] = []
        self.voices: list = []
        self.spoken_voices: list = []
        self.complete = True          # next speak() reports completed?
        self._epoch = 0
        self.epochs: list = []        # cancel_epoch passed to each speak() call
        self._earcons = dict(_default_earcons() if earcons is None else earcons)
        _LIVE_FAKE_SPEAKERS.append(self)

    def speak(self, text=None, audio_path=None, cancel_epoch=None, voice=None) -> bool:
        self.spoken.append(text)
        self.audio_paths.append(audio_path)
        self.spoken_voices.append(voice)
        self.epochs.append(cancel_epoch)
        return self.complete

    def cancel_epoch(self) -> int:
        return self._epoch

    def transient(self, kind: str) -> None:
        # The SAME single lookup the real Speaker does (one lookup post-R3).
        # A cue that resolves to nothing is not an earcon -- it is silence, and
        # recording it as an earcon is what let `repoint` ship dead.
        path = self._earcons.get(kind)
        if path is None:
            self.silent_cues.append(kind)
            return
        self.earcons.append(kind)
        self.earcon_paths.append(path)

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


class InertKeepaliveProc:
    """A keep-alive player that never was: no process, nothing to reap.

    Roughly half the suite goes make_daemon() -> handle_message(SESSION_START),
    which now pushes set_active(True) into the keep-alive manager. On the DEFAULT
    seam that spawns a REAL afplay playing 300s of silence — orphaned past the
    suite and nondeterministic under the sandbox (where afplay is blocked and the
    manager would flip to "degraded"). poll() returning None keeps it "running"
    so the manager never scores a fast death.
    """

    def poll(self):
        return None

    def wait(self, timeout=None):
        return 0

    def terminate(self):
        pass


class InertKeepaliveTimer:
    """Records nothing, fires never — the overlap/hold timers must not schedule
    real threading.Timers that outlive the test that armed them."""

    def __init__(self, interval, fn):
        self.daemon = False

    def start(self):
        pass

    def cancel(self):
        pass


def inert_hid_idle() -> float:
    """conftest's default HID-idle seam: 0.0 == "the user just typed".

    The keep-alive presence check shells out to `ioreg` on every reaping tick
    whose cache has expired. Un-neutralised, the suite would spawn a real
    subprocess from ~40 tests AND — on a machine that has genuinely been idle
    past KEEPALIVE_PRESENCE_S, which is exactly the unattended run — read back
    "absent" and flip keep-alive off under tests that assert "running". A named
    function, not a lambda, so the hermeticity guard can assert it by identity.
    """
    return 0.0


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
                 summarizer=None, earcons=None):
    """Build a SpeechDaemon. The returned `queue` is the FOREGROUND session's own
    stream queue (where its items now land and where the loop drains), so most
    single-session tests need no change. Use stream_queue() for other sessions."""
    # earcons=None means the full default table -- the fake mirrors a FRESH
    # INSTALL, which is exactly what bootstrap produces today. Pass a dict to
    # ask what a specific user's config would actually sound like.
    table = _default_earcons() if earcons is None else dict(earcons)
    speaker = FakeSpeaker(earcons=table)
    sessions = SessionManager()
    if foreground is not None:
        sessions.set_foreground(foreground)
    config = {k: (v.copy() if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
    config["verbosity"] = verbosity
    config["summarizer"] = "off"      # SP5: no test may ever reach a real `claude`
    config["earcons"] = table
    daemon = SpeechDaemon(speaker, sessions, config, spearcons=FakeSpearconCache(),
                          summarizer=summarizer)
    # Inert before ANY handler can run: no real afplay child, no real Timer thread.
    # __init__ itself never spawns (set_enabled only flips a flag), so injecting
    # here — before the caller's first handle_message — is early enough. The
    # keep-alive tests overwrite both seams with their recording fakes.
    daemon.keepalive._popen = lambda cmd: InertKeepaliveProc()
    daemon.keepalive._timer_factory = InertKeepaliveTimer
    daemon._voices_provider = lambda: []      # SP5: hermetic renders — no `say -v ?`
    queue = daemon._stream(foreground).queue if foreground is not None else SpeechQueue()
    return daemon, queue, speaker, sessions, config


def stream_queue(daemon, session: str):
    """The per-session speech queue, for assertions on a non-foreground session."""
    return daemon._stream(session).queue
