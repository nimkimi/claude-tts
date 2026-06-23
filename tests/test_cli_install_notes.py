from sonari import paths
from sonari.cli import _build_raise_helper


class _Backend:
    def __init__(self, detail):
        self._detail = detail

    def build(self):
        return (True, self._detail)


def test_regrant_note_printed_on_recompile(capsys):
    # build() returns detail == str(out) when it actually recompiled (cdhash changed).
    _build_raise_helper(_Backend(str(paths.RAISE_BIN_PATH)))
    out = capsys.readouterr().out
    assert "re-allow 'sonari-raise'" in out


def test_no_regrant_note_when_unchanged(capsys):
    _build_raise_helper(_Backend(str(paths.RAISE_BIN_PATH)
                                 + " (unchanged; kept to preserve the Automation grant)"))
    out = capsys.readouterr().out
    assert "re-allow 'sonari-raise'" not in out
