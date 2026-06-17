---
description: Set the Sonari say voice
argument-hint: <voice name>
---

Run the Sonari voice command with the Bash tool, forwarding the requested voice:

```
PYTHONPATH="${CLAUDE_PLUGIN_ROOT}/src" python -m sonari.cli voice $ARGUMENTS
```

Print the command's output to the user verbatim. If the command errors, report it briefly.
