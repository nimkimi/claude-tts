---
description: Show Sonari speech daemon status (verbosity, rate, voice, queue)
---

Run the Sonari status command with the Bash tool:

```
PYTHONPATH="${CLAUDE_PLUGIN_ROOT}/src" python -m sonari.cli status
```

Print the command's output to the user verbatim so they can see the current
verbosity, rate, voice, foreground session, and queue length. Do not add
commentary beyond the raw status. If the command errors, report the error briefly.
