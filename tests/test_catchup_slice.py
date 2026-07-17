from sonari.history import HistoryEntry
from sonari.catchup import render_slice, build_digest


def _e(kind, text, turn=0):
    return HistoryEntry(text, kind, msg_id=0, seq=0, turn_id=turn)


def test_render_slice_header_and_tags_oldest_first():
    entries = [_e("prose", "Working on it.", 0),
               _e("tool", "Bash: pytest", 0),
               _e("permission", "Allow deploy?", 1)]
    lines = render_slice(entries, "myrepo").split("\n")
    assert lines[0] == "Slice: 3 items across 2 turns in myrepo."
    assert lines[1] == "assistant: Working on it."
    assert lines[2] == "tool: Bash: pytest"
    assert lines[3] == "permission: Allow deploy?"


def test_render_slice_no_folder_fallback():
    lines = render_slice([_e("prose", "Hi.")], None).split("\n")
    assert lines[0] == "Slice: 1 item across 1 turn in this session."


def test_digest_extracts_last_assistant_sentence():
    entries = [_e("prose", "Started."), _e("tool", "ran"),
               _e("prose", "All tests passed.")]
    assert build_digest(entries) == "Summary unavailable. Last: All tests passed."


def test_digest_appends_period_when_missing():
    out = build_digest([_e("prose", "no terminal punctuation")])
    assert out == "Summary unavailable. Last: no terminal punctuation."


def test_digest_falls_back_to_last_entry_when_no_prose():
    entries = [_e("tool", "ran a thing"), _e("permission", "Allow deploy?")]
    assert build_digest(entries) == "Summary unavailable. Last: Allow deploy?"
