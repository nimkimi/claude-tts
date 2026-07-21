# Sonari

**Eyes-free text-to-speech for [Claude Code](https://claude.ai/code) on macOS — an
accessibility tool for blind and low-vision developers.**

Sonari reads Claude Code's output aloud — prose, plans, multiple-choice questions, and
permission prompts — in order, plays a distinct sound the instant a decision needs you, and
lets you answer and control the speech without looking. Run a full session with the screen
off.

- **Ordered narration** — prose, plans, questions, and permissions are spoken in order, never out of sequence.
- **Per-decision earcons** — a distinct sound the moment a question, plan, permission, or error appears.
- **Selection by number** — answer prompts with the option's number; no key injection.
- **Global hotkeys** — stop, repeat, jump between sessions, jump-to-decision, where-am-I, re-read options, verbosity, rate — all work mid-speech. Catch-up and skip-the-pile ship unbound, ready to bind.
  - **Catch-up** summarizes a session via your own logged-in coding-agent CLI (no separate API key). It draws from that subscription's usage — roughly 16–32k tokens a press, far cheaper on repeats within the hour — and falls back to a plain last-line digest when the summary is unavailable.
- **Self-contained core** — the speech engine runs on the macOS system Python; no pip, no third-party packages. (The optional Kokoro neural voice is a separate ~316 MB add-on — see Voices.)

## Privacy

Sonari runs entirely on your own Mac. It collects nothing, sends nothing over the network,
and has no servers, telemetry, or analytics.

Sonari stores session text locally in ~/.sonari/state.json so unheard speech survives
restarts. Nothing leaves your Mac. Uninstall preserves this file — delete ~/.sonari to
remove everything. See [PRIVACY.md](PRIVACY.md).

## Requirements

- macOS (Sonari uses the built-in `say` and `afplay` commands).
- Python 3.9 or newer — macOS ships `/usr/bin/python3`, which is enough. Sonari
  picks the best `python3 >= 3.9` it can find automatically.
- Xcode Command Line Tools for global hotkeys — `xcode-select --install`. (Speech
  works without them; only the hotkeys need `swiftc`.)
- Claude Code 2.1.162 or newer.

## Install

Sonari installs from a Claude Code marketplace. You start hearing Claude as soon as the
plugin is enabled; one more command turns on global hotkeys and autostart.

1. Add the marketplace: `/plugin marketplace add nimkimi/sonari` (or, in a shell,
   `claude plugin marketplace add nimkimi/sonari`).
2. Install the plugin: `/plugin install sonari@sonari` (or
   `claude plugin install sonari@sonari`). The marketplace is named `sonari`, so the
   install target is `sonari@sonari`. You will start hearing Claude immediately — the
   daemon lazy-starts on the first hook.
3. Run `/sonari:install` from inside Claude Code to finish setup (each step is printed and
   spoken). Until you run it, every new session Sonari reminds you once: *"Sonari is reading
   aloud. To enable hotkeys and autostart, run /sonari:install."*
4. Run `/sonari:doctor` to confirm everything is green (the only expected failure is
   `swiftc` / Xcode Command Line Tools on a machine without them — speech still works;
   only the hotkeys need them).

For local development you can skip the marketplace and load the repo per session with
`claude --plugin-dir <path-to-sonari>`.

If you already have `sonari` on your PATH, the CLI equivalent of step 3 is:

```bash
sonari install
```

`sonari install` resolves the best `python3 >= 3.9`, **copies the runtime to
`~/.sonari/app`** (so it survives plugin auto-updates), builds the hotkey
daemon, writes both LaunchAgents, and places the `~/.local/bin/sonari` launcher.
After a plugin update, Sonari says once — *"Sonari was updated. Run /sonari:install
to apply."* — so you can refresh the copy.

### Development

Contributors can run the test suite from a venv:

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest -q
```

The public install path above does **not** use `pip` — the venv is for tests only.

## What it sounds like

You ask Claude to refactor a file and look away:

> *"I'll update the parser to handle nested blocks. First I'll read the current implementation…"*
> — spoken as it streams, sentence by sentence.
>
> **♪ chime** — the permission earcon, the instant Claude asks.
> *"Edit parser.py. Press Control Command Return to approve."*
>
> You press ⌃⌘Return without looking up. The voice continues:
> *"Done. The parser now handles nested blocks."*
> **♪ ding** — your turn.

A second session finishing in the background plays its own turn-done sound; press ⌃⌘J to jump the voice over to it, or ⌃⌘W anytime to hear where everything stands.

Every sound Sonari makes comes from one registry (`src/sonari/cues.py`). A
*transient* tone plays immediately and never stacks with another; a *prelude*
is bound to the sentence it decorates and plays with it as one unit.

<!-- sonari:generated:sounds:begin -->
| Sound | Plays as | When you hear it |
|---|---|---|
| `turn_done` | transient | A session finished its turn |
| `choice` | transient | A question with options is waiting |
| `plan` | transient | A plan is ready for review |
| `permission` | transient | A permission ask is waiting |
| `error` | transient | That press had nothing to act on |
| `error_misdirected` | transient | Valid answer, wrong session |
| `error_system` | transient | Sonari itself failed; the content is preserved unheard |
| `permission_expired` | transient | A permission ask timed out unanswered |
| `your_turn` | transient | The turn you were hearing live just finished |
| `submit_ack` | transient | Your prompt was submitted (off by default) |
| `repoint` | transient | Your click moved the workspace to a different session |
| `pitch_up` | prelude | Rising chirp bound to the front of an approval |
| `pitch_down` | prelude | Falling chirp bound to the front of a denial |
| `callsign` | prelude | The asking session's spoken label, bound to its own utterance |
| `speech` | queued | Spoken readout of session output |
| `summary_voice` | queued | The catch-up summary's island voice |
<!-- sonari:generated:sounds:end -->

## The cockpit

Start with **⌃⌘W** — it's home base. It speaks a terse status of every session you have
running: what the voice is on, what's piled up behind it, and where your hands are focused
right now. Press it whenever you lose track; everything below is easier once you know you
can always ask.

### Global hotkeys

Default modifier is **Ctrl+Cmd** (rebindable via `~/.sonari/keymap.json`). A tiny Swift
helper registers these with Carbon `RegisterEventHotKey`, so no macOS accessibility
permission is needed.

<!-- sonari:generated:hotkeys:begin -->
| Hotkey | Effect |
|---|---|
| Ctrl+Cmd+→ | Step forward one item in the current turn |
| Ctrl+Cmd+← | Step back one item in the current turn |
| Ctrl+Cmd+↑ | Jump back one whole reply |
| Ctrl+Cmd+↓ | Jump forward one whole reply |
| Ctrl+Cmd+S | Stop/resume the current session's voice |
| Ctrl+Cmd+M | Stop every session's voice |
| Ctrl+Cmd+J | Move the voice to a background session that is waiting |
| Ctrl+Cmd+D | Jump to the pending decision |
| Ctrl+Cmd+R | Re-speak the last utterance |
| Ctrl+Cmd+Tab | Browse sessions forward (hold chord, tap Tab) |
| Ctrl+Cmd+Shift+Tab | Browse sessions backward |
| Ctrl+Cmd+W | Speak a terse status of all sessions |
| Ctrl+Cmd+Return | Approve the pending permission request |
| Ctrl+Cmd+Esc | Deny the pending permission request |
| Ctrl+Cmd+= | Speak faster |
| Ctrl+Cmd+- | Speak slower |
| Ctrl+Cmd+O | Re-speak the pending question's options |
| Ctrl+Cmd+V | Cycle verbosity: everything / medium / quiet |

Available but **unbound by default** (bind via `~/.sonari/keymap.json`):

| Action | Effect | Suggested binding |
|---|---|---|
| `skip_pile` | Settle the unheard backlog without hearing it | Ctrl+Cmd+Shift+↓ |
| `catch_up` | Hear a summary of the unheard backlog | Ctrl+Cmd+L |
| `learn_mode` | Toggle learn mode: keys speak what they do instead of doing it | — |
| `query_actions` | Speak the actions available right now | — |
<!-- sonari:generated:hotkeys:end -->

### Selecting options

When a question, permission prompt, or plan (`AskUserQuestion` / permission /
`ExitPlanMode`) appears, choose an option by pressing its **number (1-9)**, or `Esc` to
cancel — using Claude Code's native numeric selection, no key injection. For a
**multi-select** question, press each option's number (or `Space` on the highlighted item),
then `Enter` to confirm. If a question has **more than nine options**, numbers cover 1-9;
use the **arrow keys** plus `Enter` for the tenth and beyond. Sonari speaks these cues when
they apply.

Digits answer the prompt on screen; digits while holding the chooser chord (⌃⌘Tab) switch
sessions instead — two different digit meanings, never active at once.

## Sessions and the fleet

The voice only ever follows one session at a time — the **speaker**. Run several sessions
side by side and the rest keep going in the background: their prose and decisions pile up
silently in their own queue (nothing is lost), and each one plays a short **earcon** the
moment it finishes a turn or needs you, so you know something happened without hearing it
live.

Press **⌃⌘J** to jump the voice straight to whichever background session is waiting, or
hold **⌃⌘Tab** and tap through the chooser to browse the fleet by number. Left alone,
Sonari's keep-going behavior also advances the voice to the longest-waiting background
session once the current one runs out of queued speech.

Wherever you answer a question or approve a permission is simply whatever terminal you're
typing in — your response always goes there, whether or not that session currently has the
voice.

## Voices

Sonari defaults to the best enhanced/neural English voice it can find and falls back to
**Samantha**.

### Enhanced voices (recommended)

Enhanced voices sound dramatically better and are free and offline. To install one:

1. Open **System Settings → Accessibility → Spoken Content**.
2. Click **System Voice → Manage Voices…**.
3. Pick an English voice marked **(Enhanced)** or **(Premium)** — e.g. *Ava (Premium)*,
   *Zoe (Premium)*, or *Allison* — and download it.
4. Run `sonari doctor` to confirm Sonari picks it up, or pin it explicitly:

```bash
sonari voice "Ava (Premium)"
```

### Neural voice (Kokoro)

`/sonari:voices` provisions the optional Kokoro neural voice — a ~316 MB download that
installs its own Python runtime via uv/pip. Quality is comparable to Apple's Premium voices;
try those (free, no download tooling) first.

## Slash commands and CLI

Sonari's setup and speech-control commands are also available as `sonari` CLI subcommands
and, where it makes sense inside a session, as namespaced slash commands.

<!-- sonari:generated:commands:begin -->
| Slash command | CLI | Effect |
|---|---|---|
| `/sonari:doctor` | `sonari doctor` | Run Sonari health checks (TTS, voice, daemon, hooks, hotkeys) |
| `/sonari:install` | `sonari install` | One-time Sonari setup — autostart, global hotkeys, control CLI |
| `/sonari:keymap` | `sonari keymap` | List Sonari hotkey bindings (incl. unbound); '<action> clear' to unbind |
| `/sonari:minqueue` | `sonari minqueue` | Set how many items Sonari batches before reading (1 = read immediately) |
| `/sonari:rate` | `sonari rate` | Set Sonari speech rate in words per minute |
| — | `sonari skip` | skip the current item |
| `/sonari:status` | `sonari status` | Show Sonari speech daemon status (verbosity, rate, voice, queue) |
| — | `sonari stop` | stop all speech and clear the queue |
| `/sonari:uninstall` | `sonari uninstall` | Remove Sonari's autostart, hotkey helper, launcher, and app copy |
| `/sonari:verbosity` | `sonari verbosity` | Set Sonari verbosity (everything \| medium \| quiet) |
| `/sonari:voice` | `sonari voice` | Set the Sonari say voice (omit the name to list installed voices) |
| `/sonari:voices` | `sonari voices` | Install or remove Sonari neural (Kokoro) voices |
<!-- sonari:generated:commands:end -->

## Verbosity

Three live-switchable levels (earcons fire in **all** of them):

- **everything** (default) — prose narration, questions, plans, permissions, *and* brief
  tool announcements (a short summary of what's running, e.g. "Running git status").
- **medium** — prose narration plus decisions (questions / plans / permissions); **drops**
  routine tool announcements.
- **quiet** — decisions only (questions / plans / permissions); drops both tool
  announcements **and** prose narration. Earcons still fire at every level.

## How ordering works

Sonari's voice never jumps ahead of you. Spoken content is **strictly first-in, first-out**: a
question, plan, or permission is voiced *in its natural place* — after the prose that
explains it — so if the voice is mid-sentence when a permission appears, you still hear the
remaining sentences first, then the permission. What *is* instant is the **alert**: the
moment any decision appears, a short distinct earcon plays immediately (a different sound for
permission, choice, plan, error, turn-done), while the spoken detail waits its
turn in the queue. Claude Code blocks on the prompt until you respond, so hearing the
context first costs nothing. "Higher priority" therefore means *"alert you instantly with a
sound,"* never *"speak it out of order."*

## Doctor and troubleshooting

Run `sonari doctor` first — it reports each check as pass/fail. Common issues:

- **No speech at all.** Confirm `sonari status` shows your session as the foreground. The
  daemon starts lazily on the first hook; if the socket is unreachable, run `sonari install`
  to (re)load the daemon (`sonari doctor` tells you whether the socket is reachable), or
  check `~/.sonari/speechd.log`.
- **Robotic voice.** No enhanced voice is installed; see *Voices* above.
- **Hooks not firing.** Re-enable `sonari` via `/plugin` (or re-launch with
  `claude --plugin-dir /path/to/sonari`), then run `sonari doctor` and confirm the
  `plugin hooks.json` check passes.
- **Speech too fast/slow.** `sonari rate 180` (default is 200 wpm).
- **Too chatty.** `sonari verbosity medium` or `sonari verbosity quiet`.
- **Everything is stuck.** `sonari stop` clears the queue and cancels the current utterance.

State, config, the socket, and logs all live under `~/.sonari/`
(`config.json`, `speechd.sock`, `speechd.log`).

## Uninstall

Disable the `sonari` plugin via `/plugin` (or stop passing `--plugin-dir`), then run
`sonari uninstall` (or, from inside a session, `/sonari:uninstall`).

This removes the LaunchAgents, the hotkey helper, the `~/.local/bin/sonari` launcher, and
the stable app copy at `~/.sonari/app`. It **preserves** `config.json`, `keymap.json`, and
`state.json` (your session text) — delete `~/.sonari` to remove everything.

## License

MIT — see [LICENSE](LICENSE).
