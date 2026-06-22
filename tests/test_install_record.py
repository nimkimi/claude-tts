import json
import sonari.install_record as install_record


def test_write_install_record_trailing_newline(tmp_path, monkeypatch):
    p = tmp_path / "install.json"
    monkeypatch.setattr(install_record, "INSTALL_RECORD_PATH", p)
    install_record.write_install_record("/py", "3.11", "/plugin", "/app", "0.5.0")
    raw = p.read_bytes()
    assert raw.endswith(b"\n")                    # the verbatim trailing newline
    rec = json.loads(raw)
    assert rec["python"] == "/py" and rec["plugin_version"] == "0.5.0"
    assert "installed_at" in rec
