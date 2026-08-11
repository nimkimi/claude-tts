# tests/test_privacy_doc.py
import pathlib

DOC = (pathlib.Path(__file__).resolve().parents[1] / "PRIVACY.md").read_text(
    encoding="utf-8")


def test_state_json_is_in_the_inventory():
    assert "state.json" in DOC


def test_the_contradicted_claim_is_gone():
    assert "not designed to record session content" not in DOC


def test_the_purge_route_is_documented():
    assert "--purge-transcripts" in DOC
