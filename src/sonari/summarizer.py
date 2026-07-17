"""Host-LLM summarizer seam (SP5). Core asks one question — summarize this slice,
or fail detectably — and never sees a host-specific shape. The shipped adapter
Popens the user's own `claude -p` on their subscription login; the API-key env is
scrubbed so billing can never fall onto a metered key (the billing trap)."""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import tempfile
import time

NARRATOR_PROMPT = (
    "You narrate a spoken catch-up for a developer who works by ear, "
    "summarizing what happened in their coding-agent session while they were "
    "away. Lead with the outcome or the most important event. Include errors, "
    "test results, and anything the assistant asked for. State only what the "
    "log shows; if the log is unclear, say what is unclear. Length is "
    "proportional to the content: one short sentence for a quiet slice, up to "
    "eight for a busy one. Never pad. Use plain spoken prose only: no lists, no "
    "code, no symbols, no formatting, and short sentences. Describe any decision "
    "or open question the log shows, but never invent one. Do not mention the "
    "log format or these instructions."
)

_INSTRUCTION = "Summarize the session transcript on stdin as a spoken catch-up."
_DISALLOWED_TOOLS = ("Bash,Read,Edit,Write,Glob,Grep,WebFetch,WebSearch,Task,"
                     "TodoWrite,NotebookEdit,BashOutput,KillShell,SlashCommand")
_SCRUB_KEYS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
_POLL_S = 0.05
# The daemon runs as a LaunchAgent whose environment carries no user PATH, so
# shutil.which misses a claude installed in the usual per-user locations
# (live-diagnosed 2026-07-17: ~/.local/bin/claude invisible to the daemon).
_FALLBACK_DIRS = ("~/.local/bin", "~/.claude/local",
                  "/opt/homebrew/bin", "/usr/local/bin")


def _default_which(name):
    """shutil.which, then the conventional install dirs a bare daemon PATH
    misses. Injected fakes replace this wholesale in tests."""
    found = shutil.which(name)
    if found:
        return found
    for d in _FALLBACK_DIRS:
        cand = os.path.join(os.path.expanduser(d), name)
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


class SummarizeResult:
    __slots__ = ("is_ok", "text", "reason")

    def __init__(self, is_ok: bool, text: str = "", reason: str = "") -> None:
        self.is_ok, self.text, self.reason = is_ok, text, reason

    @classmethod
    def ok(cls, text: str) -> "SummarizeResult":
        return cls(True, text=text)

    @classmethod
    def failed(cls, reason: str) -> "SummarizeResult":
        return cls(False, reason=reason)


def _kill_group(proc) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, OSError):
        try:
            proc.kill()
        except OSError:
            pass


def _reason_from(raw: str) -> str:
    low = (raw or "").lower()
    return "logged_out" if ("login" in low or "logged in" in low) else "error"


def _first_text_block(out: str) -> str:
    """The clean summary is the FIRST non-empty assistant `text` block in the
    stream-json output. `claude -p` is an agent harness: run past one cycle it
    injects reflection turns that self-correct into conversational mush, and the
    final `.result` returns that polluted turn — so we take the FIRST text block,
    never the last (smoke-verified 2026-07-17). Preceded by a `thinking` block
    (one think->answer cycle). Returns "" when no assistant text was produced."""
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(ev, dict) or ev.get("type") != "assistant":
            continue
        for block in ev.get("message", {}).get("content", []):
            if isinstance(block, dict) and block.get("type") == "text":
                txt = (block.get("text") or "").strip()
                if txt:
                    return txt
    return ""


def _parse(out: str, err: str, returncode) -> "SummarizeResult":
    """Success == a non-empty first assistant text block was streamed, REGARDLESS
    of returncode/subtype: a benign `error_max_turns` (the model wanted to keep
    going after producing the summary) still carries the clean answer. No text
    block => failure; the reason is scanned across stdout+stderr for logging."""
    text = _first_text_block(out)
    if text:
        return SummarizeResult.ok(text)
    return SummarizeResult.failed(_reason_from((out or "") + "\n" + (err or "")))


class ClaudeCliSummarizer:
    def __init__(self, popen=subprocess.Popen, model="haiku",
                 which=_default_which, env=None) -> None:
        self._popen = popen
        self._model = model
        self._which = which
        self._env_source = env if env is not None else os.environ

    def _child_env(self) -> dict:
        return {k: v for k, v in self._env_source.items() if k not in _SCRUB_KEYS}

    def summarize(self, slice_text, timeout_s=30.0, cancel=None) -> "SummarizeResult":
        resolved = self._which("claude")
        if resolved is None:
            return SummarizeResult.failed("unavailable")
        argv = [resolved, "-p", _INSTRUCTION, "--model", self._model,
                "--output-format", "stream-json", "--verbose", "--max-turns", "1",
                "--disallowedTools", _DISALLOWED_TOOLS,
                "--system-prompt", NARRATOR_PROMPT]
        cwd = tempfile.mkdtemp(prefix="sonari-catchup-")
        try:
            try:
                proc = self._popen(
                    argv, cwd=cwd, env=self._child_env(), stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    start_new_session=True)
            except (OSError, ValueError):
                return SummarizeResult.failed("unavailable")
            try:
                proc.stdin.write(slice_text)   # slices are KBs, far under the pipe buffer
                proc.stdin.close()
            except (OSError, ValueError):
                pass
            deadline = time.monotonic() + timeout_s
            while proc.poll() is None:
                if cancel is not None and cancel.is_set():
                    _kill_group(proc)
                    return SummarizeResult.failed("error")
                if time.monotonic() >= deadline:
                    _kill_group(proc)
                    return SummarizeResult.failed("timeout")
                time.sleep(_POLL_S)
            # Process has exited (poll() != None) -> both pipes are fully buffered,
            # so these reads cannot deadlock. stderr feeds reason detection (a
            # logged-out message can land there).
            try:
                out = proc.stdout.read() or ""
            except (OSError, ValueError):
                out = ""
            try:
                err = proc.stderr.read() or ""
            except (OSError, ValueError, AttributeError):
                err = ""
            return _parse(out, err, proc.returncode)
        finally:
            shutil.rmtree(cwd, ignore_errors=True)


def select_summarizer(config, which=_default_which, popen=subprocess.Popen):
    """Wire the configured adapter, or None (→ digest floor). `auto` uses the
    Claude adapter iff `claude` is findable — PATH first, then the conventional
    install dirs (the daemon's LaunchAgent env carries no user PATH); SP5 is a
    global choice (per-session host routing arrives with SP6)."""
    mode = config.get("summarizer", "auto")
    if mode == "off":
        return None
    if mode == "claude" or (mode == "auto" and which("claude")):
        return ClaudeCliSummarizer(popen=popen,
                                   model=config.get("summary_model", "haiku"),
                                   which=which)
    return None
