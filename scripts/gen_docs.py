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


_BLOCKS = {"hotkeys": render_hotkeys}


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
