---
description: List Sonari hotkey bindings (incl. unbound); '<action> clear' to unbind
argument-hint: [<action> clear]
---

Run the Sonari keymap command with the Bash tool, forwarding any arguments:

```
bash "${CLAUDE_PLUGIN_ROOT}/bin/sonari" keymap $ARGUMENTS
```

Print the command's output to the user verbatim. You may briefly explain any binding the user asks about — this table is meant to be understood, not just displayed. With no arguments it lists every
action and its hotkey (unbound actions show "(unbound)"). `<action> clear` (or
`<action> none`) unbinds that action and applies it live (the daemon re-registers
its global hotkeys; on macOS the separate hotkeyd is reloaded to re-read the
keymap).
