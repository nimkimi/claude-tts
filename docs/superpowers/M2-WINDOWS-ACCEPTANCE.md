# Sonari M2 — Windows Acceptance Checklist

> **Purpose:** This file is the gate M2 cannot close on macOS. Every item marked ⚠ is mock-blind — it cannot be verified by the macOS test suite. A human on a real Windows 10/11 machine must work through each item and tick it off before M2 is declared production-ready on Windows.
>
> **Scope:** Windows 10 21H2+ and Windows 11. Python 3.9+. Tested architectures: win-amd64. win-arm64 has an open risk — see Risks section.

---

## Pre-requisites

- A real Windows 10 or 11 machine (not a VM without audio, not Docker/Server Core — see Risk a).
- Python 3.9+ installed from python.org or via the Microsoft Store **real** installer (not a Store stub — see Risk e).
- `claude` CLI installed and at least one session run so the hooks directory exists.
- `git` and the Sonari repo checked out, or the wheel installed via pip.

---

## 1. Install

### 1a. Install the PyWinRT projection set

```powershell
pip install winrt-runtime ^
            winrt-Windows.Media.SpeechSynthesis ^
            winrt-Windows.Media.Playback ^
            winrt-Windows.Media.Core ^
            winrt-Windows.Storage.Streams
```

Expected: all packages install without error. Confirm no `win-arm64` availability warning is printed (see Risk h).

### 1b. Register the Task Scheduler task (non-admin)

```powershell
sonari install
```

Expected:
- No UAC elevation prompt appears (the install runs at LeastPrivilege for the current user).
- Exit code 0.

### 1c. Confirm the task is visible

```powershell
schtasks /query /tn Sonari.Speechd
```

Expected: the task appears in the output with status "Ready" or "Running". If the command returns exit code 1 ("ERROR: The system cannot find the file specified"), the install failed.

### 1d. Inspect the registered task XML

```powershell
schtasks /query /tn Sonari.Speechd /xml
```

Confirm:
- `<LogonType>InteractiveToken</LogonType>` is present (required for SAPI audio in the GUI session).
- `<RunLevel>LeastPrivilege</RunLevel>` is present (confirms no admin required).
- `<RestartOnFailure><Interval>PT5M</Interval>` is present.
- `<UserId>` matches your own `DOMAIN\username` (run `whoami` to check).

---

## 2. ⚠ Speech

> **Mock-blind risk.** The macOS suite proves the `_TtsHandle` contract holds against a fake `MediaPlayer`. It does NOT prove that `MediaPlayer.play()` actually routes audio to the speakers from a `DETACHED_PROCESS | CREATE_NO_WINDOW` Task Scheduler process. See Risk (a).

### 2a. Start a `claude` session and send a short prompt

```cmd
claude "Say hello"
```

Expected:
- Claude's prose response is spoken aloud by a **OneCore** neural voice (not a legacy Desktop SAPI voice).
- The voice is intelligible at the default rate (~150 wpm).
- There is no noticeable trailing silence longer than ~750 ms after the utterance ends (the `SpeechAppendedSilence.MIN` + `SpeechPunctuationSilence.MIN` options were applied).

If no audio is heard, proceed to Risk (a) diagnostics.

### 2b. Confirm the voice is OneCore (neural), not Desktop legacy

```powershell
python -c "
from winrt.windows.media.speechsynthesis import SpeechSynthesizer
v = SpeechSynthesizer.default_voice
print(v.display_name, v.id)
"
```

Expected: `v.id` contains `Speech_OneCore` (e.g. `HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech_OneCore\Voices\Tokens\TTS_MS_EN-US_ARIA_11.0`).

If only Desktop voices are listed (id contains `Speech\Voices\Tokens`), install a OneCore language pack: **Settings → Time & language → Speech → Add voices** and select an English (United States) Neural voice (e.g. "Microsoft Aria Online (Natural)").

---

## 3. ⚠ Interrupt

> **Mock-blind risk.** `_TtsHandle.terminate()` calls `MediaPlayer.pause()` + `MediaPlayer.close()` on the fake. On real WinRT, `close()` may raise a COM exception if the player is already in a terminal state — the `try/except Exception: pass` guard silences this, but the audio must actually stop.

### 3a. Trigger skip mid-utterance

While a long Claude response is being spoken, issue a skip/stop command (exact key or command depends on hotkey configuration — M3 implements real hotkeys; for now trigger via the daemon socket directly or a short `sonari stop` CLI call if wired).

Expected:
- Audio cuts off within ~100 ms of the interrupt command.
- The next utterance (if any) starts without delay.
- The daemon remains running (confirm via `tasklist | findstr python` — the daemon process is still present).

### 3b. Confirm returncode after terminate

Instrument a test script to verify:
```python
from sonari.platform.windows.tts import WinTtsBackend
h = WinTtsBackend().run("This is a long utterance that we will interrupt")
import time; time.sleep(0.2)
h.terminate()
assert h.returncode == 1
```

Expected: assertion passes and audio stops.

---

## 4. ⚠ Earcons

> **Mock-blind risk.** `winsound.PlaySound(..., SND_ASYNC)` posts audio to the Win32 multimedia scheduler. The mock records the call but cannot verify that audio reaches the speakers. Rapid successive earcons may truncate each other (see Risk g).

### 4a. Confirm each earcon is distinct and audible

Trigger each of the 6 earcon types in sequence (permission, choice, plan, error, turn_done, ready) and verify each plays its distinct generated `.wav` tone:

```python
from sonari.platform.windows.earcon import WinEarconBackend
from sonari.platform.windows.earcons import default_earcons
import time

b = WinEarconBackend()
for name, path in default_earcons().items():
    print(f"Playing: {name}")
    h = b.play(path)
    assert h.poll() == 0
    time.sleep(0.4)  # wait for async playback to complete before next
```

Expected: 6 distinct short tones play in sequence, each audibly different.

### 4b. Confirm rapid succession does not crash

```python
from sonari.platform.windows.earcon import WinEarconBackend
from sonari.platform.windows.earcons import default_earcons
earcons = list(default_earcons().values())
b = WinEarconBackend()
for p in earcons[:3]:
    b.play(p)  # no sleep — rapid fire
```

Expected: no crash or exception. Audio may be truncated (SND_ASYNC behavior — see Risk g), but the process must not raise.

---

## 5. ⚠ Single-instance

> **Mock-blind risk.** The `msvcrt.locking` fake tracks inodes in-process. Real `msvcrt.locking` is a system-wide byte-range lock — two separate `python.exe` processes must not both hold it. This cross-process behavior cannot be verified from a mock.

### 5a. Confirm two daemons cannot start simultaneously

In two separate PowerShell windows, launch the daemon directly:

```powershell
# Terminal 1
python -m sonari.daemon

# Terminal 2 (immediately after)
python -m sonari.daemon
```

Expected:
- Terminal 1: daemon starts and listens.
- Terminal 2: daemon exits immediately (the singleton lock is held by Terminal 1).
- `tasklist | findstr python` shows exactly **one** daemon process.

### 5b. Confirm the lock releases on daemon exit

Kill Terminal 1's daemon (Ctrl+C), then start the daemon in Terminal 2.

Expected: Terminal 2's daemon starts successfully and acquires the lock.

### 5c. If `msvcrt.locking` proves unreliable

If the above test reveals that two daemons start simultaneously (e.g. on a network drive or unusual filesystem), switch to a named mutex:

```python
# Alternative (named mutex — add to transport.py if needed)
import ctypes
mutex = ctypes.windll.kernel32.CreateMutexW(None, True, "Global\\SonariDaemon")
if ctypes.windll.kernel32.GetLastError() == 0xB7:  # ERROR_ALREADY_EXISTS
    sys.exit("Another instance is already running.")
```

This is the fallback documented in `transport.acquire_singleton`.

---

## 6. ⚠ Autostart + Restart

> **Mock-blind risk.** The Task Scheduler task XML was validated by `ET.fromstring()` in the test suite. Whether `schtasks` actually accepts it (UTF-16 BOM, namespace, non-admin LogonTrigger) and whether the daemon starts on logon is unverifiable from macOS.

### 6a. Logoff/logon autostart

1. Log off the Windows session.
2. Log back in.
3. Wait ~10 seconds, then:
   ```powershell
   tasklist | findstr python
   ```
   Expected: the Sonari daemon process is running.

### 6b. Supervisor restart after daemon kill

1. Identify the daemon's PID:
   ```powershell
   tasklist | findstr python
   ```
2. Kill the daemon process:
   ```powershell
   taskkill /PID <daemon_pid> /F
   ```
3. Wait for the backoff interval (base = 2 seconds for the first crash), then check again:
   ```powershell
   tasklist | findstr python
   ```
   Expected: a new daemon process is running within ~5 seconds.

### 6c. Confirm backoff sequence on repeated crashes

Kill the daemon 4 times in rapid succession and measure the restart delays. Expected sequence (seconds): 2, 4, 8, 16 (capped at 120 for subsequent crashes). Backoff resets to 2s after the daemon runs for ≥ 300 seconds without crashing.

---

## 7. ⚠ Hooks Fire

> **Mock-blind risk.** The exec-form hooks.json builder (`build_hooks_json`) was tested against a JSON schema contract. Whether Claude Code on Windows actually resolves the hooks config path and fires the exec-form hook (no bash shim) with the correct `command` + `args` is unverifiable from macOS.

### 7a. Locate the Claude Code Windows hooks directory

Run `claude` and check where it reads hooks from. Typically one of:
- `%APPDATA%\Claude\hooks.json`
- `%USERPROFILE%\.claude\hooks.json`
- The directory printed by `claude config get hooks_dir` (if that CLI command exists)

Confirm the exact path and update this checklist entry once confirmed.

### 7b. Deploy the hooks.json

```powershell
sonari install
```

Inspect the written `hooks.json` file. Confirm:
- `"type": "command"` is present (exec-form, not shell-form).
- `"command"` is the resolved `pythonw.exe` path (absolute, backslashes doubled in JSON).
- `"args"` is `["<path>\\hook.py", "MessageDisplay"]` for the MessageDisplay hook.

### 7c. Confirm a hook fires during a claude session

Start a `claude` session and send a prompt. Monitor the daemon log (or add a brief debug print to `hook.py`):

```powershell
Get-Content "$env:USERPROFILE\.sonari\daemon.log" -Wait
```

Expected: a line appears in the log each time Claude produces output (MessageDisplay hook fired → daemon received the event → TTS was triggered).

### 7d. Confirm Stop hook fires on session end

At the end of a `claude` session, confirm the Stop hook fires and the daemon receives it (no orphaned speech after the session ends).

---

## 8. RISKS — Probe Explicitly (Mock-Blind)

The following risks cannot be verified from macOS and must be probed on the Windows box. Each is a potential show-stopper.

### Risk (a): SAPI / MediaPlayer audio from a DETACHED_PROCESS | CREATE_NO_WINDOW Task-Scheduler process

**This is the #1 risk.** Neural `SpeechSynthesizer` / `MediaPlayer` uses COM and requires an STA (Single-Threaded Apartment) with a message pump. A `DETACHED_PROCESS` started by Task Scheduler may have no audio device access or may hang on `play()` without `CoInitializeEx(COINIT_APARTMENTTHREADED)`.

**Diagnostic:** If no audio plays from the Task Scheduler–launched daemon, add this to the daemon startup (before any TTS calls):

```python
import ctypes
ctypes.windll.ole32.CoInitializeEx(None, 0)  # 0 = COINIT_APARTMENTTHREADED
```

If audio then works, this call must be added permanently to `src/sonari/daemon.py` for the Windows path.

**Fallback:** If `CoInitializeEx` is insufficient, run the daemon as a standard session process (not a Task Scheduler task) and use a persistent background thread with `CoInitializeEx`.

### Risk (b): `IAsyncOperation.get()` blocking behavior in a daemon thread

`synthesize_text_to_stream_async(text).get()` is called synchronously on the daemon's TTS thread. Verify that `.get()` actually blocks and returns the stream (and does not require `await` in an asyncio context or a WinRT message pump).

**Test:** Run the TTS backend directly in a script:
```python
from sonari.platform.windows.tts import WinTtsBackend
h = WinTtsBackend().run("test blocking")
rc = h.wait(timeout=5.0)
print("returncode:", rc)  # must be 0
```

If this hangs indefinitely, `.get()` requires a message pump — add a `comtypes`-based STA loop or switch to the `asyncio`-based PyWinRT pattern.

### Risk (c): Single-instance truly excludes across processes

Covered in section 5 above. If `msvcrt.locking` fails cross-process, the fallback is `kernel32.CreateMutexW` + `GetLastError() == ERROR_ALREADY_EXISTS` (documented in `transport.acquire_singleton` docstring).

### Risk (d): `schtasks /xml` UTF-16 acceptance + non-admin LogonTrigger registration (no UAC)

The `TASK_XML_TEMPLATE` is written with `encoding='utf-16'` (Python emits UTF-16 LE with BOM). On Windows builds before 22H2 this is required; UTF-8 causes "The task XML is malformed." Confirm schtasks accepts the file without error.

Also confirm that a **standard (non-admin) user** can register the task. Expected: no UAC prompt. If UAC appears, the `RunLevel` or `LogonType` is wrong — verify `LeastPrivilege` and `InteractiveToken` are both set.

### Risk (e): Store-stub avoidance on a machine where only Store Python exists

On a fresh Windows 11 install, `python` on PATH may point to `%LOCALAPPDATA%\Microsoft\WindowsApps\python.exe` (the Store stub, exit code 9009). `resolve_python_windows()` must skip this and find the real interpreter via `py -3` launcher or a PATH-based probe.

**Test:** On a machine where Store Python is the only `python` on PATH, confirm `sonari install` still resolves a real Python >= 3.9 and completes without error. If it fails, install Python from python.org and ensure `py.exe` launcher is available.

### Risk (f): `importlib.resources.as_file` temp-path lifetime for wheel installs

`default_earcons()` stores paths extracted via `importlib.resources.as_file()`. For wheel (`.whl`) installs the extracted temp file is cleaned up when the `with as_file(...)` context exits. The current implementation stores the resolved path inside the `with` block, which is safe for file-based (editable) installs but may produce a stale path for zip-based wheel installs.

**Test:** Install Sonari from a wheel (`pip install sonari-*.whl`), then:
```python
from sonari.platform.windows.earcons import default_earcons
paths = default_earcons()
import pathlib
for name, p in paths.items():
    assert pathlib.Path(p).exists(), f"Missing: {p}"
```

If paths are stale, fix: extract to a stable per-process tempdir registered with `atexit.register(shutil.rmtree, tmpdir)`.

### Risk (g): `winsound` rapid-earcon truncation

Each new `PlaySound(..., SND_ASYNC)` call silently cancels the previous async sound. If two earcons are triggered in rapid succession (< ~200 ms apart), the first is cut off. There is no OS-level completion callback exposed by `winsound`.

**Mitigation options (pick one if this is observed):**
1. Add a minimum gap guard (e.g. 150 ms) in the daemon scheduler before issuing a new earcon.
2. Use `SND_NOSTOP` flag — `PlaySound` returns `False` (does not play) if a sound is already active. This avoids truncation but may drop earcons.
3. Switch to a higher-level API (`pywaveout` or `win32api.PlaySound`) that exposes a completion callback.

### Risk (h): PyWinRT projection availability for win-arm64

The PyWinRT packages (`winrt-Windows.Media.SpeechSynthesis`, etc.) are confirmed available for `win-amd64`. As of the M2 research date (2026-06-11), `win-arm64` wheels may be unavailable on PyPI for all projection packages.

**Test (on arm64 hardware or via pip dry-run):**
```powershell
pip install --dry-run winrt-Windows.Media.SpeechSynthesis
```

If no arm64 wheel is found, document this as a known gap. Fallback: use the Windows SAPI 5 COM interface directly via `comtypes` or `pywin32`, which ships arm64 wheels.

---

## 9. Residual

- **Nima is low-vision (magnifier user).** A fully-blind + NVDA screen reader pass is a separate pre-GA step. Confirm that: (a) the spoken audio does not conflict with NVDA speech; (b) NVDA can navigate the `sonari install` output; (c) earcon volume is not overpowering relative to NVDA speech.
- **Uninstall path:** `sonari uninstall` on Windows must delete the Task Scheduler task and the hooks.json. Verify both are removed and no orphaned process remains.
- **Upgrade path:** running `sonari install` over an existing installation (task already registered) must not fail — the `/f` flag on `schtasks /create` overwrites silently.

---

## Sign-off

| Item | Tester | Date | Result | Notes |
|------|--------|------|--------|-------|
| 1a. PyWinRT install | | | | |
| 1b. sonari install (no UAC) | | | | |
| 1c. schtasks /query | | | | |
| 1d. schtasks /query /xml | | | | |
| 2a. Speech audible (OneCore) | | | | |
| 2b. Voice is OneCore/neural | | | | |
| 3a. Skip mid-utterance | | | | |
| 3b. returncode after terminate | | | | |
| 4a. All 6 earcons distinct | | | | |
| 4b. Rapid succession no crash | | | | |
| 5a. Single-instance cross-process | | | | |
| 5b. Lock releases on exit | | | | |
| 6a. Autostart on logon | | | | |
| 6b. Supervisor restarts daemon | | | | |
| 6c. Backoff sequence | | | | |
| 7a. Hooks dir located | | | | |
| 7b. hooks.json exec-form | | | | |
| 7c. MessageDisplay hook fires | | | | |
| 7d. Stop hook fires | | | | |
| Risk (a): COM/STA audio | | | | |
| Risk (b): IAsyncOperation.get() | | | | |
| Risk (c): msvcrt cross-process | | | | |
| Risk (d): UTF-16 + non-admin | | | | |
| Risk (e): Store stub avoidance | | | | |
| Risk (f): as_file temp lifetime | | | | |
| Risk (g): rapid earcon truncation | | | | |
| Risk (h): arm64 PyWinRT | | | | |
| Residual: NVDA pass | | | | |
| Residual: uninstall path | | | | |
| Residual: upgrade path | | | | |
