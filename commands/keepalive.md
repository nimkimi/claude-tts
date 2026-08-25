---
description: Toggle the Bluetooth keep-alive (holds the audio device open while sessions are live; fixes clipped speech on Bluetooth headsets)
argument-hint: on | off
---

Run the Sonari keepalive command with the Bash tool, forwarding the requested
state:

```
bash "${CLAUDE_PLUGIN_ROOT}/bin/sonari" keepalive $ARGUMENTS
```

While on and any session is live, Sonari streams silence so a Bluetooth
headset never suspends its audio link between utterances — without it, the
first fraction of each utterance (and whole short earcons) can be swallowed.
Costs while active: the headset's radio streams continuously (battery use
comparable to music playback) and the Mac will not idle-sleep.

It is not held around the clock, though: the stream is released roughly half an
hour after you stop speaking *and* stop typing, and re-arms the moment you are
back — so a terminal tab you never close is not by itself a reason to keep the
Mac awake.

If `sonari doctor` reports the keep-alive as **degraded** — its silent-stream
spawns kept dying, so clipping is back — retry it by running this command with
`off` and then `on` again.

Print the command's output to the user verbatim. If the command errors, report it briefly.
