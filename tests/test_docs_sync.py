"""Drift guards: the README's generated islands must match the registry.

gen_docs.py lives in scripts/ (repo tooling, not shipped); load it by path."""
import importlib.util
import pathlib
import re

import pytest

import sonari.platform as platform

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load_gen_docs():
    spec = importlib.util.spec_from_file_location(
        "gen_docs", ROOT / "scripts" / "gen_docs.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def mac(monkeypatch):
    monkeypatch.setattr(platform.sys, "platform", "darwin")
    platform._CACHE = None
    yield
    platform._CACHE = None


def test_readme_hotkey_island_is_current(mac):
    gen = _load_gen_docs()
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "<!-- sonari:generated:hotkeys:begin -->" in text
    assert gen.regenerate(text) == text, (
        "README generated islands are stale — run: python scripts/gen_docs.py")


def test_regenerate_is_idempotent(mac):
    gen = _load_gen_docs()
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    once = gen.regenerate(text)
    assert gen.regenerate(once) == once


def test_every_slash_command_has_a_cli_verb(mac):
    gen = _load_gen_docs()
    slash, cli = gen.slash_verbs(), gen.cli_verbs()
    missing = set(slash) - set(cli)
    assert not missing, "slash commands with no CLI verb: {0}".format(missing)


def test_every_cli_verb_is_documented(mac):
    gen = _load_gen_docs()
    table = gen.render_commands()
    for verb in gen.cli_verbs():
        assert "`sonari {0}".format(verb) in table, verb


def test_readme_commands_island_is_current(mac):
    gen = _load_gen_docs()
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "<!-- sonari:generated:commands:begin -->" in text
    assert gen.regenerate(text) == text


def test_commands_table_rows_are_well_formed(mac):
    """A frontmatter/help description containing a literal '|' (e.g. verbosity's
    'everything | medium | quiet') must not widen the row past the header's
    column count — an unescaped pipe splits GFM tables and silently drops text."""
    gen = _load_gen_docs()
    lines = gen.render_commands().splitlines()
    header_cols = len(re.findall(r"(?<!\\)\|", lines[0]))
    for line in lines[2:]:
        assert len(re.findall(r"(?<!\\)\|", line)) == header_cols, line


def test_manifest_versions_agree():
    import json
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    assert plugin["version"] == market["plugins"][0]["version"]


def test_readme_sounds_island_is_current(mac):
    gen = _load_gen_docs()
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "<!-- sonari:generated:sounds:begin -->" in text
    assert gen.regenerate(text) == text, (
        "README generated islands are stale — run: python scripts/gen_docs.py")


def test_every_registered_cue_is_documented(mac):
    from sonari.cues import CUES
    gen = _load_gen_docs()
    table = gen.render_sounds()
    for name in CUES:
        assert "`{0}`".format(name) in table, name


def test_sounds_table_rows_are_well_formed(mac):
    gen = _load_gen_docs()
    lines = gen.render_sounds().splitlines()
    header_cols = len(re.findall(r"(?<!\\)\|", lines[0]))
    for line in lines[2:]:
        assert len(re.findall(r"(?<!\\)\|", line)) == header_cols, line
