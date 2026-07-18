"""Regenerate README.md's generated islands from the code they describe.

Run:    .venv/bin/python scripts/gen_docs.py          # rewrite README in place
Check:  .venv/bin/python scripts/gen_docs.py --check  # exit 1 if stale

Islands are marker-delimited; everything outside the markers is hand-authored.
tests/test_docs_sync.py runs regenerate() against the committed README, so a
stale island fails the suite."""
from __future__ import annotations

import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (_ROOT, os.path.join(_ROOT, "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from sonari import keymap

README = os.path.join(_ROOT, "README.md")
_CLI_EXCLUDE = {"daemon"}          # internal; not a user-facing verb
COMMANDS_DIR = os.path.join(_ROOT, "commands")


def render_hotkeys() -> str:
    rows = keymap.hotkey_rows()
    lines = ["| Hotkey | Effect |", "|---|---|"]
    for r in rows:
        if r["combo"]:
            lines.append("| {0} | {1} |".format(r["combo"], r["doc"]))
    unbound = [r for r in rows if not r["combo"]]
    if unbound:
        lines.append("")
        lines.append("Available but **unbound by default** "
                     "(bind via `~/.sonari/keymap.json`):")
        lines.append("")
        lines.append("| Action | Effect | Suggested binding |")
        lines.append("|---|---|---|")
        for r in unbound:
            lines.append("| `{0}` | {1} | {2} |".format(
                r["action"], r["doc"], r["proposed"] or "—"))
    return "\n".join(lines)


def slash_verbs() -> dict:
    """verb -> frontmatter description, from commands/*.md."""
    out = {}
    for fname in sorted(os.listdir(COMMANDS_DIR)):
        if not fname.endswith(".md"):
            continue
        verb = fname[:-3]
        desc = ""
        with open(os.path.join(COMMANDS_DIR, fname), encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("description:"):
                    desc = line.split(":", 1)[1].strip()
                    break
        out[verb] = desc
    return out


def cli_verbs() -> dict:
    """verb -> argparse help string, from the real parser."""
    from sonari.cli import _build_parser
    parser = _build_parser()
    out = {}
    for action in parser._subparsers._group_actions:
        for choice, sub in action.choices.items():
            if choice in _CLI_EXCLUDE:
                continue
            help_by_choice = {
                a.dest: a.help for a in action._choices_actions}
            out[choice] = help_by_choice.get(choice) or sub.description or ""
    return out


def render_commands() -> str:
    slash, cli = slash_verbs(), cli_verbs()
    lines = ["| Slash command | CLI | Effect |", "|---|---|---|"]
    for verb in sorted(set(slash) | set(cli)):
        # escape literal '|' (e.g. verbosity's "everything | medium | quiet") so it
        # can't be mistaken for a table column separator and truncate the row
        effect = (slash.get(verb) or cli.get(verb, "")).replace("|", "\\|")
        s = "`/sonari:{0}`".format(verb) if verb in slash else "—"
        c = "`sonari {0}`".format(verb) if verb in cli else "—"
        lines.append("| {0} | {1} | {2} |".format(s, c, effect))
    return "\n".join(lines)


_BLOCKS = {"hotkeys": render_hotkeys, "commands": render_commands}


def regenerate(text: str) -> str:
    for name, renderer in _BLOCKS.items():
        begin = "<!-- sonari:generated:{0}:begin -->".format(name)
        end = "<!-- sonari:generated:{0}:end -->".format(name)
        pattern = re.compile(
            re.escape(begin) + r".*?" + re.escape(end), re.DOTALL)
        replacement = begin + "\n" + renderer() + "\n" + end
        if not pattern.search(text):
            raise SystemExit("marker block missing from README: " + name)
        text = pattern.sub(lambda _m: replacement, text)
    return text


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    with open(README, "r", encoding="utf-8") as fh:
        text = fh.read()
    new = regenerate(text)
    if "--check" in argv:
        if new != text:
            print("README generated islands are stale; run scripts/gen_docs.py")
            return 1
        return 0
    if new != text:
        with open(README, "w", encoding="utf-8") as fh:
            fh.write(new)
        print("README.md regenerated.")
    else:
        print("README.md already current.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
