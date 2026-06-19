# tests/test_raise_swift.py
import os
import shutil
import subprocess

import pytest

SWIFT_SRC = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "hotkeyd", "sonari-raise.swift")


def test_swift_source_exists():
    assert os.path.exists(SWIFT_SRC)


@pytest.mark.skipif(shutil.which("swiftc") is None, reason="swiftc not available")
def test_swift_source_compiles(tmp_path):
    out = tmp_path / "sonari-raise"
    proc = subprocess.run(["swiftc", SWIFT_SRC, "-o", str(out)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert out.exists()
    assert "warning:" not in proc.stderr, proc.stderr


@pytest.mark.skipif(shutil.which("swiftc") is None, reason="swiftc not available")
def test_usage_exit_code(tmp_path):
    out = tmp_path / "sonari-raise"
    subprocess.run(["swiftc", SWIFT_SRC, "-o", str(out)], check=True)
    r = subprocess.run([str(out)], capture_output=True, text=True)
    assert r.returncode == 2  # no args -> usage
