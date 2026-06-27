# Feature request: a hook/IPC to answer AskUserQuestion & ExitPlanMode from an external tool

## Problem
Claude Code's `PermissionRequest` hook lets an external tool allow/deny a *tool* call
(verified working). But there is no equivalent channel to (a) select an **AskUserQuestion**
option or (b) approve/reject an **ExitPlanMode** plan from outside the interactive session.
Hooks can gate a tool but cannot supply a tool's *result*, and there is no IPC/response API
for an interactive turn.

## Why it matters
Eyes-free / accessibility tools (e.g. the Sonari TTS cockpit) can speak these prompts but
cannot let the user answer them by hotkey — the final selection must happen in the terminal,
which defeats hands/eyes-free operation.

## Proposed
A hook event (or IPC) that fires on AskUserQuestion / ExitPlanMode and accepts a structured
response from an external tool: the chosen option index/label (single or multi-select) for
AskUserQuestion, and approve/reject for ExitPlanMode — mirroring how `PermissionRequest`
returns `hookSpecificOutput.decision.behavior`.

## Notes
Keystroke injection is not viable (Secure Event Input swallows synthetic keys; wrong-target
risk). A blocking hook with a timeout (as `PermissionRequest` already supports) is the proven
pattern.
