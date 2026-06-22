from sonari.platform.macos._helpers import xml_escape, build_swift_binary


def test_xml_escape_three_chars():
    assert xml_escape("a&b<c>d") == "a&amp;b&lt;c&gt;d"


def test_build_swift_missing_swiftc(monkeypatch, tmp_path):
    import sonari.platform.macos._helpers as h
    monkeypatch.setattr(h.shutil, "which", lambda _: None)
    ok, detail = build_swift_binary(
        str(tmp_path / "x.swift"), str(tmp_path / "out"),
        str(tmp_path / "h"), "hotkeyd", "any permission grants")
    assert ok is False and detail == "swiftc not found"


def test_build_swift_unreadable_source_uses_src_label(monkeypatch, tmp_path):
    import sonari.platform.macos._helpers as h
    monkeypatch.setattr(h.shutil, "which", lambda _: "/usr/bin/swiftc")
    ok, detail = build_swift_binary(
        str(tmp_path / "missing.swift"), str(tmp_path / "out"),
        str(tmp_path / "h"), "sonari-raise", "the Automation grant")
    assert ok is False
    assert "cannot read sonari-raise source" in detail
