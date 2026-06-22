import json
import os
import stat

from sonari.atomicio import atomic_write_json


def test_writes_indented_json_no_trailing_newline(tmp_path):
    p = tmp_path / "x.json"
    atomic_write_json(p, {"b": 2, "a": 1}, indent=2)
    raw = p.read_bytes()
    assert raw == json.dumps({"b": 2, "a": 1}, indent=2).encode("utf-8")
    assert not raw.endswith(b"\n")  # json.dump adds no trailing newline


def test_compact_when_indent_none(tmp_path):
    p = tmp_path / "x.json"
    atomic_write_json(p, {"b": 2, "a": 1}, indent=None)
    assert p.read_bytes() == json.dumps({"b": 2, "a": 1}).encode("utf-8")


def test_chmod_applied_to_final_file(tmp_path):
    p = tmp_path / "x.json"
    atomic_write_json(p, {"k": 1}, chmod=0o600)
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600


def test_no_tmp_left_behind(tmp_path):
    p = tmp_path / "x.json"
    atomic_write_json(p, {"k": 1})
    assert not (tmp_path / "x.json.tmp").exists()
    assert p.exists()


def test_fsync_false_still_writes(tmp_path):
    p = tmp_path / "x.json"
    atomic_write_json(p, {"k": 1}, fsync=False)
    assert json.loads(p.read_text()) == {"k": 1}
