"""Resolve the bundled earcon .wav paths for Windows.

Package layout::

    sonari/platform/windows/earcons/
        __init__.py        <- this file
        generate.py        <- stdlib WAV generator
        permission.wav
        choice.wav
        plan.wav
        error.wav
        turn_done.wav
        ready.wav

pyproject.toml declares these as package data::

    [tool.setuptools.package-data]
    sonari = ["platform/windows/earcons/*.wav"]
"""
from __future__ import annotations

import importlib.resources as _ilr
import pathlib
import sys

_EARCON_NAMES: tuple[str, ...] = (
    "permission",
    "choice",
    "plan",
    "error",
    "turn_done",
    "ready",
)

# Cache so we only resolve paths once per process
_cache: dict[str, str] = {}


def default_earcons() -> dict[str, str]:
    """Return {earcon_name: absolute_wav_path} for all bundled earcons.

    Resolution strategy:
    * Python 3.9+ : importlib.resources.files() — zip-safe Traversable API.
      Works when sonari is installed from a wheel (.whl) without unpacking.
    * Python 3.7-3.8 : pathlib relative to __file__ (dev installs,
      editable installs, and unpacked wheels).

    Raises FileNotFoundError if an expected .wav is absent from the package
    (e.g. package data was not included in the distribution).
    """
    if _cache:
        return dict(_cache)

    for name in _EARCON_NAMES:
        fname = f"{name}.wav"

        if sys.version_info >= (3, 9):
            # Traversable path — works inside zip archives (wheels, zipapp)
            ref = _ilr.files(__package__).joinpath(fname)
            with _ilr.as_file(ref) as p:
                resolved = str(p.resolve())
        else:
            # __file__-relative — reliable for editable / unpacked installs
            resolved = str(
                (pathlib.Path(__file__).parent / fname).resolve()
            )

        if not pathlib.Path(resolved).exists():
            raise FileNotFoundError(
                f"Bundled earcon not found: {resolved!r}\n"
                f"Run: python -m sonari.platform.windows.earcons.generate  "
                f"(then commit the .wav files)"
            )
        _cache[name] = resolved

    return dict(_cache)
