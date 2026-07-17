import io
import json

from sonari.summarizer import (
    ClaudeCliSummarizer, SummarizeResult, select_summarizer, NARRATOR_PROMPT,
)


class _FakeProc:
    def __init__(self, out, err="", returncode=0):
        self.returncode = returncode
        self.pid = 4242
        self.stdin = io.StringIO()
        self.stdout = io.StringIO(out)
        self.stderr = io.StringIO(err)

    def poll(self):
        return self.returncode      # already complete


class _FakePopen:
    def __init__(self, out, err="", returncode=0):
        self._out, self._err, self._rc, self.calls = out, err, returncode, []

    def __call__(self, argv, **kwargs):
        self.calls.append({"argv": argv, **kwargs})
        return _FakeProc(self._out, self._err, self._rc)


def _stream(*events):
    """Build a stream-json stdout blob (one JSON object per line)."""
    return "\n".join(json.dumps(e) for e in events)


def _assistant(*blocks):
    return {"type": "assistant", "message": {"content": list(blocks)}}


def _text(t):
    return {"type": "text", "text": t}


def _thinking(t="mulling"):
    return {"type": "thinking", "thinking": t}


def _result(subtype="success", is_error=False):
    return {"type": "result", "subtype": subtype, "is_error": is_error, "num_turns": 1}


def _ok(text="All tests passed."):
    # Real shape: a thinking block, then the clean text block, then a result event.
    return _stream(_assistant(_thinking(), _text(text)), _result())


def test_child_env_scrubs_both_api_keys_and_inherits_the_rest():
    env = {"ANTHROPIC_API_KEY": "sk-secret", "ANTHROPIC_AUTH_TOKEN": "tok",
           "PATH": "/usr/bin", "HOME": "/home/nima"}
    fake = _FakePopen(_ok())
    s = ClaudeCliSummarizer(popen=fake, which=lambda n: "/usr/bin/claude", env=env)
    s.summarize("Slice: 1 item.\nassistant: hi.", timeout_s=5)
    child_env = fake.calls[0]["env"]
    assert "ANTHROPIC_API_KEY" not in child_env
    assert "ANTHROPIC_AUTH_TOKEN" not in child_env
    assert child_env["PATH"] == "/usr/bin" and child_env["HOME"] == "/home/nima"


def test_argv_carries_flags_model_and_stable_narrator_prompt():
    fake = _FakePopen(_ok())
    s = ClaudeCliSummarizer(popen=fake, model="haiku",
                            which=lambda n: "/c", env={})
    s.summarize("x", timeout_s=5)
    argv = fake.calls[0]["argv"]
    assert argv[0] == "/c" and argv[1] == "-p"   # argv[0] = the which()-resolved path
    assert argv[argv.index("--model") + 1] == "haiku"
    # stream-json + --verbose: we read the FIRST assistant text block, not .result.
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert "--verbose" in argv
    assert argv[argv.index("--max-turns") + 1] == "1"
    assert NARRATOR_PROMPT in argv
    # Spec §6 non-negotiable #3 pinned: never resume/continue the user's live session.
    assert "--continue" not in argv and "--resume" not in argv
    assert fake.calls[0]["cwd"]                  # neutral temp cwd, not the caller's


def test_first_text_block_is_the_summary():
    out = _ok("The build is green.")
    r = ClaudeCliSummarizer(popen=_FakePopen(out),
                            which=lambda n: "/c", env={}).summarize("x", timeout_s=5)
    assert r.is_ok and r.text == "The build is green."


def test_error_max_turns_with_text_is_still_success():
    # The load-bearing case: the model produced a clean summary, then the harness
    # aborted with error_max_turns (it wanted to keep going). We ALREADY have the
    # answer -> success, ignore the error subtype and the non-zero exit.
    out = _stream(_assistant(_thinking(), _text("All 1105 tests passed.")),
                  _result(subtype="error_max_turns", is_error=True))
    r = ClaudeCliSummarizer(popen=_FakePopen(out, returncode=1),
                            which=lambda n: "/c", env={}).summarize("x", timeout_s=5)
    assert r.is_ok and r.text == "All 1105 tests passed."


def test_first_text_block_wins_over_later_pollution():
    # A later reflection turn ("You're right...") must NOT be what we speak.
    out = _stream(
        _assistant(_thinking(), _text("Tests passed and the build is green.")),
        _assistant(_thinking(), _text("You're right, I was summarizing the transcript.")),
        _result())
    r = ClaudeCliSummarizer(popen=_FakePopen(out),
                            which=lambda n: "/c", env={}).summarize("x", timeout_s=5)
    assert r.is_ok and r.text == "Tests passed and the build is green."


def test_logged_out_no_text_block_is_detected():
    # Logged-out fails before any assistant text; the message may land on stderr.
    r = ClaudeCliSummarizer(popen=_FakePopen("", err="Not logged in · Please run /login",
                                             returncode=1),
                            which=lambda n: "/c", env={}).summarize("x", timeout_s=5)
    assert not r.is_ok and r.reason == "logged_out"


def test_no_text_block_maps_to_error():
    # Only a thinking block + an error result, no text -> failure (=> digest).
    out = _stream(_assistant(_thinking()), _result(subtype="error", is_error=True))
    r = ClaudeCliSummarizer(popen=_FakePopen(out, returncode=1),
                            which=lambda n: "/c", env={}).summarize("x", timeout_s=5)
    assert not r.is_ok and r.reason == "error"


def test_missing_binary_is_unavailable_without_spawning():
    fake = _FakePopen(_ok())
    r = ClaudeCliSummarizer(popen=fake, which=lambda n: None,
                            env={}).summarize("x", timeout_s=5)
    assert not r.is_ok and r.reason == "unavailable"
    assert fake.calls == []


class _HangingPopen:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append(kwargs)

        class _P:
            pid, returncode = 4243, None
            stdin, stdout, stderr = io.StringIO(), io.StringIO(""), io.StringIO("")
            poll = staticmethod(lambda: None)          # never completes
        return _P()


def test_timeout_kills_group_and_returns_timeout(monkeypatch):
    import sonari.summarizer as m
    killed = {"n": 0}
    monkeypatch.setattr(m, "_kill_group", lambda p: killed.__setitem__("n", killed["n"] + 1))
    r = m.ClaudeCliSummarizer(popen=_HangingPopen(), which=lambda n: "/c",
                              env={}).summarize("x", timeout_s=0.05)
    assert not r.is_ok and r.reason == "timeout" and killed["n"] == 1


def test_select_summarizer_off_auto_claude():
    assert select_summarizer({"summarizer": "off"}) is None
    assert select_summarizer({"summarizer": "auto"}, which=lambda n: None) is None
    s = select_summarizer({"summarizer": "auto", "summary_model": "haiku"},
                          which=lambda n: "/c")
    assert isinstance(s, ClaudeCliSummarizer)


def test_config_defaults_include_summarizer_keys():
    from sonari.config import DEFAULTS
    assert DEFAULTS["summarizer"] == "auto"
    assert DEFAULTS["summary_voice"] == "auto"
    assert DEFAULTS["summary_model"] == "haiku"
