"""Module-execution entry: `python -m sonari.cli` (used by bin/sonari)."""
import sys

from sonari.cli import main

if __name__ == "__main__":
    sys.exit(main())
