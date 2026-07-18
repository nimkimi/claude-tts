"""Drift guards: the README's generated islands must match the registry.

gen_docs.py lives in scripts/ (repo tooling, not shipped); load it by path."""
import importlib.util
import pathlib

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
