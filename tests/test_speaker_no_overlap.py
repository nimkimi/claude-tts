"""D8: one voice. Two threads must never have utterances playing at once.

Observed live by the owner on a daemon restart: `bootstrap._start_boot_cue()`
speaks W8 on its own thread while `daemon.run()`'s speak loop is already
draining the queue, so two `say` processes played simultaneously and talked
over each other. `Speaker._current_lock` did not prevent it — it guards only
the `_current` handle and the cancel epoch, while `proc.wait()` (the entire
duration of the utterance) ran outside any lock.
"""
import threading

from sonari.speaker import Speaker


class _OverlapDetectingProc:
    """A proc whose wait() reports whether another proc was mid-wait with it."""

    returncode = 0
    _live = 0
    _seen_overlap = False
    _guard = threading.Lock()

    def __init__(self, hold):
        self._hold = hold

    def wait(self, timeout=None):
        with _OverlapDetectingProc._guard:
            _OverlapDetectingProc._live += 1
            if _OverlapDetectingProc._live > 1:
                _OverlapDetectingProc._seen_overlap = True
        self._hold.wait(timeout=1.0)      # stay "playing" long enough to collide
        with _OverlapDetectingProc._guard:
            _OverlapDetectingProc._live -= 1
        return 0

    def terminate(self):
        self._hold.set()


def test_two_threads_speaking_never_overlap():
    _OverlapDetectingProc._live = 0
    _OverlapDetectingProc._seen_overlap = False
    hold = threading.Event()
    started = threading.Semaphore(0)

    def runner(text, voice, rate):
        started.release()
        return _OverlapDetectingProc(hold)

    sp = Speaker(say_runner=runner)
    threads = [threading.Thread(target=sp.speak, args=(f"utterance {i}",))
               for i in range(2)]
    for t in threads:
        t.start()
    # Let the first utterance get into wait(), then release both.
    started.acquire(timeout=2.0)
    hold.set()
    for t in threads:
        t.join(timeout=5.0)

    assert not _OverlapDetectingProc._seen_overlap, \
        "two utterances played at the same time — the boot cue talking over the queue"
