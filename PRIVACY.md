# Sonari — Privacy Policy

_Last updated: 2026-08-11_

Sonari is a macOS accessibility plugin for [Claude Code](https://claude.ai/code) that reads
Claude Code's output aloud so you can work eyes-free. This policy explains exactly what it
does — and does not do — with your data.

## The short version

**Sonari runs entirely on your own Mac. It does not collect, transmit, sell, or share any of
your data.** There are no servers, no accounts, no telemetry, no analytics, and no crash
reporting.

Sonari makes exactly one kind of outbound connection, and only if you ask for it: running
`sonari voices install` downloads the neural-voice runtime and model — Python packages from
PyPI and a ~316 MB voice model from GitHub — the same way any package install does. It sends
nothing about you: no data of yours leaves your Mac, then or ever. Skip that command and
Sonari never touches the network at all.

## What Sonari processes

To speak Claude Code's output, Sonari receives the text that Claude Code passes to it through
plugin hooks — assistant prose, the options in multiple-choice questions, plan text, and
permission-prompt actions. That text is:

- processed **in memory** on your machine,
- handed to the built-in macOS `say` command to be spoken, and
- stored **only on your machine**: Sonari keeps recent spoken text in
  `~/.sonari/state.json` so a daemon restart doesn't lose output you haven't
  heard yet. It is never transmitted anywhere.

Sonari's components talk to each other only over a **local socket** on your machine. Nothing
Sonari handles ever leaves your computer.

## What Sonari stores on your machine

Sonari keeps a few small local files under `~/.sonari/` (and LaunchAgent files under
`~/Library/LaunchAgents/`):

- `config.json` — your preferences (voice, speech rate, verbosity).
- `keymap.json` and `hotkeyd.resolved.json` — your global-hotkey bindings.
- `install.json` — local file paths and the install timestamp.
- `state.json` — **session content**: the verbatim text of what Sonari has
  spoken or has yet to speak, up to `history_cap` (200 by default) recent
  utterances per open session, plus a small per-session roster (your
  project-folder name and an assigned number, used for voice cues like
  "session two"). Kept so your unheard backlog survives a daemon restart.
  Local only, never transmitted — see "Removing your data" below for how to
  delete it.
- `speechd.log`, `daemon.err.log`, `faulthandler.log`, `hotkeyd.log`,
  `daemon.fail_memo` — operational/diagnostic files only (startup messages,
  error tracebacks, native-crash dumps, and a restart-retry timestamp
  marker). They record what Sonari's process is doing, not what it speaks;
  the text Sonari narrates is persisted only in `state.json` by default (see
  "Optional diagnostic capture" below for the one opt-in exception).

- `spearcons/` — a cache of short rendered audio clips of your session labels
  (the project-folder name and its number), so Sonari can play them without
  re-synthesising each time. Same information as the roster above, in audio
  form. `sonari uninstall` does **not** remove this folder; delete
  `~/.sonari/spearcons/` yourself, or the whole `~/.sonari` folder.

None of these files are transmitted off your machine.

## Optional diagnostic capture (off by default)

For troubleshooting, Sonari has an **opt-in** capture mode that is **disabled unless you
explicitly enable it** by setting the `SONARI_CAPTURE` environment variable to a folder path.
When enabled, it writes the raw hook payloads it receives (which include session content) to
that folder **on your machine**, to help diagnose problems. It is local-only and never
transmitted. Leave `SONARI_CAPTURE` unset to keep it off; delete the folder to remove any
captured files.

## No personal data, no tracking

Sonari does not collect personal information, does not use cookies or identifiers, does not
profile or track usage, and contains no analytics or third-party data processors.

## Removing your data

Run `sonari uninstall`. If `state.json` holds saved transcript text, it asks
before deleting it — "Sonari saved transcript text from N sessions. Delete
it?" — and if you don't answer (no terminal attached, or you decline), it
defaults to **keeping** that file, so an unattended uninstall can never
destroy data you didn't agree to lose. To skip the prompt: `sonari uninstall
--purge-transcripts` deletes `state.json` immediately, and `sonari uninstall
--keep-transcripts` keeps it. Either way, `config.json` and `keymap.json`
(your settings) are always preserved so they survive a reinstall. To remove
everything, including any kept transcript text, delete the `~/.sonari/`
folder, or delete `state.json` alone at any time.

## Changes to this policy

Any changes will be committed to this file in the project repository, with the "Last updated"
date above revised accordingly.

## Contact

Questions about privacy? Open an issue at
<https://github.com/nimkimi/sonari/issues> or email hakimi.nima1@gmail.com.
