---
description: Set Sonari verbosity (everything | medium | quiet)
argument-hint: everything | medium | quiet
---

Run the Sonari verbosity command with the Bash tool, forwarding the requested level:

```
PYTHONPATH="${CLAUDE_PLUGIN_ROOT}/src" python -m sonari.cli verbosity $ARGUMENTS
```

Print the command's output to the user verbatim. If the command errors, report it briefly.
