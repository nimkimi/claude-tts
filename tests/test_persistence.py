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


def test_history_state_round_trip_including_heard():
    from sonari.history import SessionHistory
    h = SessionHistory(cap=200)
    e0 = h.record("s1", "prose", "one"); h.end_message("s1")
    e1 = h.record("s1", "choice", "two")          # noqa: F841
    e0.heard = True
    state = json.loads(json.dumps(h.to_state()))  # must survive a JSON round-trip
    h2 = SessionHistory(cap=200)
    h2.load_state(state)
    got = list(h2._entries["s1"])
    assert [(e.text, e.kind, e.msg_id, e.seq, e.turn_id, e.heard) for e in got] == [
        ("one", "prose", 0, 0, 0, True),
        ("two", "choice", 1, 0, 0, False),
    ]
    assert h2._msg_id["s1"] == h._msg_id["s1"]
    assert h2._group_seq["s1"] == h._group_seq["s1"]
    assert h2._turn_id["s1"] == h._turn_id.get("s1", 0)


def test_history_clock_normalization_spans_downtime_hermetically():
    from sonari.history import SessionHistory
    mono = [1000.0]; wall = [50000.0]
    h = SessionHistory(clock=lambda: mono[0])
    h.record("s1", "prose", "x")                        # stamp == 1000.0
    state = h.to_state(clock=lambda: mono[0], now=lambda: wall[0])
    # Simulate a 1h downtime spanning a fresh, unrelated process monotonic clock:
    # the monotonic seam jumps (5000), wall advances by exactly 3600.
    mono2 = [5000.0]; wall2 = [50000.0 + 3600.0]
    h2 = SessionHistory()
    h2.load_state(state, clock=lambda: mono2[0], now=lambda: wall2[0])
    age = h2.unheard_age("s1")
    assert abs(age - 3600.0) < 1e-6                     # true elapsed incl downtime
    assert age >= 0                                     # never negative


def test_history_shrunk_cap_keeps_newest_and_frontier_ages_out():
    from sonari.history import SessionHistory
    h = SessionHistory(cap=5)
    for i in range(5):
        h.record("s1", "prose", "p{0}".format(i)); h.end_message("s1")  # msg 0..4
    state = h.to_state()
    h2 = SessionHistory(cap=3)                          # SHRUNK cap
    h2.load_state(state)
    assert [e.text for e in h2._entries["s1"]] == ["p2", "p3", "p4"]    # newest 3
    assert len(h2._entries["s1"]) == 3                  # maxlen honored, not unbounded
    entries, aged_out = h2.unheard_from_frontier("s1", (0, 0))
    assert aged_out is True                             # (0,0) behind oldest survivor (2,0)
    assert [e.text for e in entries] == ["p2", "p3", "p4"]  # tuple compare, no TypeError


def test_session_stream_frontier_round_trip_via_json():
    from sonari.session_stream import SessionStream
    st = SessionStream(); st.advance_frontier((3, 0))
    state = json.loads(json.dumps(st.to_state()))       # tuple -> list over JSON
    assert state == {"frontier": [3, 0]}
    st2 = SessionStream(); st2.load_state(state)
    assert st2.frontier == (3, 0) and isinstance(st2.frontier, tuple)
    assert (4, 0) > st2.frontier                         # tuple compare, no TypeError


def test_session_stream_none_frontier_round_trip():
    from sonari.session_stream import SessionStream
    st = SessionStream()                                 # frontier None
    st2 = SessionStream(); st2.load_state(st.to_state())
    assert st2.frontier is None


def test_sessions_state_round_trip_and_provisional_seed():
    from sonari.sessions import SessionManager
    sm = SessionManager()
    sm.register("s1", cwd="/x/repo"); sm.register("s2", cwd="/x/other")
    state = json.loads(json.dumps(sm.to_state()))
    sm2 = SessionManager()
    sm2.load_state(state)
    assert sm2.folder("s1") == "repo" and sm2.number("s1") == sm.number("s1")
    assert sm2.folder("s2") == "other" and sm2.number("s2") == sm.number("s2")
    assert sm2.is_provisional("s1") and sm2.is_provisional("s2")
    assert sm2.identity("s1") is None                    # D2: identity NOT restored


def test_provisional_session_is_not_live():
    from sonari.sessions import SessionManager
    sm = SessionManager()
    sm.load_state({"s1": {"folder": "repo", "number": 1}})
    assert sm.is_live("s1") is False                     # fail-CLOSED while provisional


def test_set_identity_clears_provisional_and_restores_liveness():
    from sonari.sessions import SessionManager, Identity
    sm = SessionManager()
    sm.load_state({"s1": {"folder": "repo", "number": 1}})
    assert sm.is_live("s1") is False
    sm.set_identity("s1", Identity())                    # empty identity still clears it
    assert sm.is_provisional("s1") is False
    assert sm.is_live("s1") is True                      # empty tty -> fail-OPEN again


def test_provisional_session_absent_from_where_am_i_also_map():
    from sonari.sessions import Identity
    from sonari.daemon.features import control
    from tests.daemon_helpers import make_daemon
    daemon, *_ = make_daemon(foreground=None)
    daemon.sessions.load_state({"s1": {"folder": "repo", "number": 1}})  # provisional
    daemon._stream("s1")                                  # create its stream (frontier None)
    daemon.history.record("s1", "prose", "unheard pile")  # non-empty clause otherwise
    assert control._also_clause(daemon) == ""             # provisional -> excluded entirely
    daemon.sessions.set_identity("s1", Identity())        # clears provisional
    out = control._also_clause(daemon)
    assert "repo" in out and "unheard" in out             # now visible, named + counted


def test_provisional_session_absent_from_chooser_snapshot():
    from sonari.daemon.features import chooser
    from tests.daemon_helpers import make_daemon
    daemon, *_ = make_daemon(foreground=None)
    daemon.sessions.load_state({"s1": {"folder": "repo", "number": 1}})
    _origin, candidates = chooser._snapshot(daemon.sessions)
    assert "s1" not in candidates                         # is_live False -> filtered; no chooser edit


class _CountingStore:
    def __init__(self):
        self.saves = 0
        self.last = None

    def save(self, data):
        self.saves += 1
        self.last = data


class _RaisingStore:
    def save(self, data):
        raise RuntimeError("disk full")


def test_writer_snapshot_runs_under_the_lock():
    from sonari.daemon.persistence import PersistenceWriter
    store = _CountingStore()
    lock = threading.Lock()

    def snap():
        assert lock.locked() is True            # snapshot MUST be built under the lock
        return {"version": STATE_VERSION, "n": 7}

    writer = PersistenceWriter(store, snap, lock)
    writer.flush()                               # synchronous snapshot + save
    assert store.saves == 1
    assert store.last == {"version": STATE_VERSION, "n": 7}


def test_writer_coalesces_a_burst_into_one_save():
    from sonari.daemon.persistence import PersistenceWriter
    store = _CountingStore()
    lock = threading.Lock()
    writer = PersistenceWriter(store, lambda: {"version": STATE_VERSION},
                               lock, debounce=0.0, sleep=lambda _s: None)
    writer._running.set()                        # arm without launching the thread
    for _ in range(5):
        writer.mark_dirty()                      # 5 marks -> one set Event
    assert writer._run_one_cycle() is True
    assert store.saves == 1                      # coalesced: 5 marks -> 1 save
    assert not writer._dirty.is_set()            # drained


def test_writer_flush_swallows_a_raising_save():
    from sonari.daemon.persistence import PersistenceWriter
    writer = PersistenceWriter(_RaisingStore(), lambda: {"version": STATE_VERSION},
                               threading.Lock())
    writer.flush()                               # must NOT raise


def test_mark_dirty_acquires_no_lock():
    from sonari.daemon.persistence import PersistenceWriter
    lock = threading.Lock()
    writer = PersistenceWriter(_CountingStore(), lambda: {}, lock)
    with lock:                                   # hold the lock the writer was given
        writer.mark_dirty()                      # must not block/deadlock on it
    assert writer._dirty.is_set()


def test_writer_stop_joins_a_started_thread():
    from sonari.daemon.persistence import PersistenceWriter
    store = _CountingStore()
    writer = PersistenceWriter(store, lambda: {"version": STATE_VERSION},
                               threading.Lock(), debounce=0.0)
    writer.start()
    writer.mark_dirty()
    writer.stop()                                # unblocks the wait + joins
    assert writer._thread is None
