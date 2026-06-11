# tests/test_win_earcon.py
import wave, struct, math
from sonari.platform.windows.earcon import WinEarconBackend

def _wav(tmp_path):
    p = tmp_path / "e.wav"
    with wave.open(str(p), "w") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(44100)
        w.writeframes(b"".join(struct.pack("<h", int(math.sin(i/10)*1000)) for i in range(4410)))
    return p

def test_play_existing_returns_done_handle(tmp_path):
    import winsound; winsound._calls.clear()
    h = WinEarconBackend().play(str(_wav(tmp_path)))
    assert h.poll() == 0
    assert len(winsound._calls) == 1
    assert winsound._calls[0][1] == (winsound.SND_FILENAME | winsound.SND_ASYNC)

def test_play_missing_returns_none(tmp_path):
    import winsound; winsound._calls.clear()
    result = WinEarconBackend().play(str(tmp_path / "nope.wav"))
    assert result is None and winsound._calls == []
