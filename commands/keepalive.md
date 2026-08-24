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

Print the command's output to the user verbatim. If the command errors, report it briefly.
