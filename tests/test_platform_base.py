"""Contracts pins for the collapsed platform layer (Stage 2).

The 4 single-impl backends are runtime_checkable Protocols (structural, no
inheritance); RaiseBackend stays an ABC. These pins replace the old base.py
ABC-instantiation tests.
"""
import os

from sonari.platform import contracts


def test_four_backends_are_runtime_checkable_protocols():
    # runtime_checkable: structural isinstance must work without inheritance,
    # and a bare object must NOT satisfy any backend.
    for proto in (contracts.TtsBackend, contracts.EarconBackend,
                  contracts.HotkeyBackend, contracts.SupervisorBackend):
        assert not isinstance(object(), proto)


def test_mac_backends_satisfy_their_protocols_structurally():
    from sonari.platform.macos.tts import MacTtsBackend
    from sonari.platform.macos.earcon import MacEarconBackend
    from sonari.platform.macos.hotkeys import MacHotkeyBackend
    from sonari.platform.macos.supervisor import MacSupervisorBackend
    assert isinstance(MacTtsBackend(), contracts.TtsBackend)
    assert isinstance(MacEarconBackend(), contracts.EarconBackend)
    assert isinstance(MacHotkeyBackend(), contracts.HotkeyBackend)
    assert isinstance(MacSupervisorBackend(), contracts.SupervisorBackend)


def test_platform_backend_bundles_the_five_fields():
    fields = contracts.PlatformBackend.__dataclass_fields__
    assert set(fields) == {"tts", "earcon", "hotkey", "supervisor", "raise_backend"}


def test_macos_hotkey_exposes_keytables_default_mods_and_lifecycle():
    from sonari.platform.macos.hotkeys import MacHotkeyBackend
    hk = MacHotkeyBackend()
    assert hk.key_codes()["s"] == 1 and hk.mod_masks()["cmd"] == 256
    assert hk.default_mods() == ["ctrl", "cmd"]
    # the moved no-ops: macOS hotkeyd is a separate process
    hk.start(lambda msg: None)
    hk.stop()
    assert hk.doctor_rows() == []


def test_contracts_module_has_future_annotations():
    # test_py39_compat scans src/sonari NON-recursively, so platform/contracts.py
    # is NOT covered there. Pin its future-import here. (Do NOT make that scan
    # recursive — keep this local assertion instead.)
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "src", "sonari", "platform", "contracts.py")
    with open(path, encoding="utf-8") as f:
        text = f.read()
    assert "from __future__ import annotations" in text
    # ...and it is the FIRST code line after the module docstring.
    body = text.split('"""', 2)[-1].lstrip()
    assert body.startswith("from __future__ import annotations")
