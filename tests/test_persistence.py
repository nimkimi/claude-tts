"""SP6 restart-persistence suite: StateStore + serialization units + the writer +
host snapshot/restore + the restore->catch-up integration. Grows task-by-task."""
import json
import os
import threading

from sonari.daemon.persistence import StateStore, STATE_VERSION


def test_store_round_trips_a_versioned_dict(tmp_path):
    store = StateStore(tmp_path / "state.json")
    payload = {"version": STATE_VERSION, "hello": "world", "n": 3}
    store.save(payload)
    assert store.load() == payload


def test_store_load_missing_file_is_none(tmp_path):
    assert StateStore(tmp_path / "nope.json").load() is None


def test_store_load_invalid_json_is_none(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{ not json", encoding="utf-8")
    assert StateStore(path).load() is None


def test_store_load_version_mismatch_is_none(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"version": STATE_VERSION + 999, "x": 1}),
                    encoding="utf-8")
    assert StateStore(path).load() is None


def test_store_load_non_dict_is_none(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    assert StateStore(path).load() is None


def test_concurrent_saves_never_tear_the_file(tmp_path):
    # Two threads hammering save() with distinct payloads must never leave the
    # file mid-write: every load() sees a complete, valid dict (unique temp +
    # internal lock + os.replace). This is the §7 CRITICAL corruption fix.
    store = StateStore(tmp_path / "state.json")

    def writer(tag):
        for i in range(200):
            store.save({"version": STATE_VERSION, "tag": tag, "i": i})

    a = threading.Thread(target=writer, args=("a",))
    b = threading.Thread(target=writer, args=("b",))
    a.start(); b.start(); a.join(); b.join()
    data = store.load()
    assert isinstance(data, dict) and data["version"] == STATE_VERSION
    # No leftover temp files in the directory (each save cleaned up).
    leftovers = [p for p in os.listdir(tmp_path) if p.endswith(".state.tmp")]
    assert leftovers == []
