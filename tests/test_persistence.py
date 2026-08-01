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
    assert state == {"frontier": [3, 0], "stopped": False}
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


def test_provisional_session_named_pending_in_where_am_i_also_map():
    from sonari.sessions import Identity
    from sonari.daemon.features import control
    from tests.daemon_helpers import make_daemon
    daemon, *_ = make_daemon(foreground=None)
    daemon.sessions.load_state({"s1": {"folder": "repo", "number": 1}})  # provisional
    daemon._stream("s1")                                  # create its stream (frontier None)
    daemon.history.record("s1", "prose", "unheard pile")  # non-empty clause otherwise
    # D3 §4a supersedes SP6's silent quarantine: a restored session holding
    # content is NAMED and MARKED, so a recovered pile is never invisible.
    assert control._also_clause(daemon) == " Also: 1 repo, pending, 1 unheard."
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


def test_snapshot_restore_round_trip_and_behavior_decisions():
    from tests.daemon_helpers import make_daemon
    # Source daemon: a real pile, a partway frontier, a bumped id counter, a
    # held stop (DURABLE — owner ruling 2026-07-21) and a global voice-state
    # (still transient).
    src, *_ = make_daemon(foreground=None)
    src.sessions.register("s1", cwd="/x/repo")
    src.history.record("s1", "prose", "one"); src.history.end_message("s1")
    src.history.record("s1", "prose", "two")
    src._stream("s1").advance_frontier((0, 0))
    src._stream("s1").stopped = True             # D1: a held stop
    src.voice_state = "stopped-all"              # D1: a global hold
    src._state._next_id = 41
    with src._lock:
        data = src._snapshot_state()
    src._store.save(data)

    # Fresh daemon restores from the same (isolated) SONARI_DIR/state.json.
    dst, _q, speaker, sessions, _c = make_daemon(foreground=None)
    dst._restore_state()

    assert [e.text for e in dst.history.unheard("s1")] == ["one", "two"]
    assert dst._streams["s1"].frontier == (0, 0)
    assert dst._state._next_id == 41
    assert sessions.folder("s1") == "repo" and sessions.number("s1") == 1
    # Owner ruling 2026-07-21 (flips the earlier D1 call, on the E4b
    # silent-burial evidence): a held stop SURVIVES restart — the muted session
    # stays stopped and new turns pile durably instead of advancing the
    # frontier past muted content. The GLOBAL voice-state stays transient.
    assert dst.voice_state == "flowing"
    assert dst._streams["s1"].stopped is True
    # D2 + §4.4: identity NOT restored; the session is provisional.
    assert sessions.identity("s1") is None
    assert sessions.is_provisional("s1") is True
    # BOOT_CUE safety: restore never touched the speaker (can't swallow/dup the cue).
    assert speaker.spoken == [] and speaker.earcons == [] and speaker.cancels == 0


def test_restore_fail_open_on_corrupt_and_version_mismatch():
    from tests.daemon_helpers import make_daemon
    for content in ("{ not json", json.dumps({"version": 999, "sessions": {}})):
        daemon, _q, speaker, sessions, _c = make_daemon(foreground=None)
        os.makedirs(os.path.dirname(daemon._store._path), exist_ok=True)
        with open(daemon._store._path, "w", encoding="utf-8") as fh:
            fh.write(content)
        daemon._restore_state()                  # must NOT raise
        assert sessions.session_ids() == []      # booted empty
        assert dict(daemon._streams) == {}
        assert speaker.spoken == [] and speaker.earcons == []


def test_restore_missing_file_is_a_noop():
    from tests.daemon_helpers import make_daemon
    daemon, _q, _sp, sessions, _c = make_daemon(foreground=None)
    daemon._restore_state()                      # no state.json exists
    assert sessions.session_ids() == []


def test_restore_drops_a_stale_pile_and_keeps_a_fresh_one():
    from tests.daemon_helpers import make_daemon
    daemon, *_ = make_daemon(foreground=None)
    saved = 1_000_000.0
    stale = {
        "version": STATE_VERSION, "saved_wall": saved, "next_id": 3,
        "sessions": {"old": {"folder": "old", "number": 1},
                     "fresh": {"folder": "fresh", "number": 2}},
        "streams": {},
        "history": {
            "old": {"msg_id": 0, "group_seq": 1, "turn_id": 0, "entries": [
                {"text": "x", "kind": "prose", "msg_id": 0, "seq": 0, "turn_id": 0,
                 "heard": False, "wall_stamp": saved - 25 * 3600}]},   # 25h > 24h
            "fresh": {"msg_id": 0, "group_seq": 1, "turn_id": 0, "entries": [
                {"text": "y", "kind": "prose", "msg_id": 0, "seq": 0, "turn_id": 0,
                 "heard": False, "wall_stamp": saved - 1 * 3600}]},     # 1h < 24h
        },
    }
    daemon._store.save(stale)
    daemon._restore_state()
    assert "old" not in daemon.sessions.session_ids()          # dropped
    assert "fresh" in daemon.sessions.session_ids()            # kept
    assert daemon.history.unheard("old") == []
    assert [e.text for e in daemon.history.unheard("fresh")] == ["y"]


def test_restore_fails_open_to_empty_on_malformed_inner_shape():
    """StateStore.load() validates ONLY the top level (dict + version); it
    passes through a version-valid file whose INNER shape is malformed. The
    apply phase must fail open to EMPTY, never PARTIAL: "good"'s history entry
    is well-formed and (under an in-place, apply-as-you-go restore) would
    commit successfully before "bad"'s entry -- missing "text" -- raises and
    aborts the rest. A partial boot (history restored, roster/streams empty)
    would violate the fail-open-to-EMPTY contract (§8) just as much as a raise
    would."""
    from tests.daemon_helpers import make_daemon
    daemon, _q, speaker, sessions, _c = make_daemon(foreground=None)
    saved = 1_000_000.0
    payload = {
        "version": STATE_VERSION, "saved_wall": saved, "next_id": 9,
        "sessions": {"good": {"folder": "good", "number": 1},
                     "bad": {"folder": "bad", "number": 2}},
        "streams": {},
        "history": {
            "good": {"msg_id": 0, "group_seq": 1, "turn_id": 0, "entries": [
                {"text": "leaked", "kind": "prose", "msg_id": 0, "seq": 0,
                 "turn_id": 0, "heard": False, "wall_stamp": saved - 10}]},
            "bad": {"msg_id": 0, "group_seq": 1, "turn_id": 0, "entries": [
                {"kind": "prose", "msg_id": 0, "seq": 0,          # missing "text"
                 "turn_id": 0, "heard": False, "wall_stamp": saved - 10}]},
        },
    }
    daemon._store.save(payload)
    daemon._restore_state()                       # must NOT raise
    assert sessions.session_ids() == []            # booted EMPTY, not partial
    assert dict(daemon._streams) == {}
    assert daemon.history.unheard("good") == []    # NOT leaked from a partial apply
    assert daemon.history.unheard("bad") == []
    assert speaker.spoken == [] and speaker.earcons == []


def _hotkey(daemon, mtype, session, **kw):
    from sonari.protocol import PROTOCOL_VERSION
    daemon._dispatch_hotkey({"v": PROTOCOL_VERSION, "type": mtype,
                             "session": session, **kw})


def test_store_targets_the_isolated_sonari_dir():
    from sonari.paths import SONARI_DIR
    from tests.daemon_helpers import make_daemon
    daemon, *_ = make_daemon(foreground=None)
    assert daemon._store._path == str(SONARI_DIR / "state.json")
    assert daemon._persistence is not None            # writer constructed, not started


def test_hotkey_only_skip_pile_marks_dirty():
    from sonari.protocol import MsgType
    from tests.daemon_helpers import make_daemon
    daemon, *_ = make_daemon(foreground="s0")
    daemon.sessions.register("s0", cwd="/x/s0")
    daemon.history.record("s0", "prose", "a pile")    # something to skip
    daemon._persistence._dirty.clear()
    _hotkey(daemon, MsgType.SKIP_PILE, "s0")
    assert daemon._persistence._dirty.is_set()        # frontier advance persisted


def test_hotkey_only_flush_marks_dirty():
    from sonari.protocol import MsgType
    from tests.daemon_helpers import make_daemon
    daemon, *_ = make_daemon(foreground="s0")
    daemon._persistence._dirty.clear()
    _hotkey(daemon, MsgType.FLUSH, "s0")
    assert daemon._persistence._dirty.is_set()        # turn/msg counter bump persisted


def test_note_spoken_marks_dirty():
    from sonari.queue import SpeechItem
    from tests.daemon_helpers import make_daemon
    daemon, *_ = make_daemon(foreground="s0")
    it = SpeechItem(id=1, session="s0", kind="prose", text="x", is_decision=False)
    daemon._current_item = it
    daemon._persistence._dirty.clear()
    daemon.note_spoken(it, completed=True)
    assert daemon._persistence._dirty.is_set()


def test_flush_persists_the_last_delta():
    from sonari.daemon.persistence import StateStore
    from tests.daemon_helpers import make_daemon
    daemon, *_ = make_daemon(foreground=None)
    daemon.sessions.register("s1", cwd="/x/repo")
    daemon.history.record("s1", "prose", "hello")
    daemon._persistence.flush()                        # the shutdown write
    data = StateStore(daemon._store._path).load()      # reload via a fresh store
    assert data is not None
    assert data["sessions"]["s1"]["folder"] == "repo"
    assert data["history"]["s1"]["entries"][0]["text"] == "hello"


def test_restore_pile_becomes_catchable_and_provisional_until_reidentified(monkeypatch):
    from sonari import ttyutil
    from sonari.protocol import MsgType, PROTOCOL_VERSION
    from sonari.daemon.features import control
    from tests.daemon_helpers import make_daemon

    # /dev/ttys404 is a node that does not exist — pinned here rather than left
    # to the host filesystem so the dead-tty leg below is hermetic.
    monkeypatch.setattr(ttyutil, "tty_alive", lambda tty: tty != "/dev/ttys404")

    # Source: session s1 with a 4-message pile; frontier dealt-with through msg 1,
    # so the catch-up tail is msg 2 + msg 3 (2 items), never the whole 4.
    src, *_ = make_daemon(foreground=None)
    src.sessions.register("s1", cwd="/x/repo")
    for i in range(4):
        src.history.record("s1", "prose", "p{0}".format(i)); src.history.end_message("s1")
    src._stream("s1").advance_frontier((1, 0))
    src._state._next_id = 9
    with src._lock:
        data = src._snapshot_state()
    src._store.save(data)

    # Restore into a fresh daemon (same isolated SONARI_DIR/state.json).
    dst, _q, _sp, sessions, _c = make_daemon(foreground=None)
    dst._restore_state()
    assert sessions.is_provisional("s1") is True
    assert sessions.identity("s1") is None                 # D2

    # Provisional => NAMED and marked pending in the ⌃⌘W Also-map (D3 §4a):
    # the restored pile is discoverable before the session ever comes back.
    assert control._also_clause(dst) == " Also: 1 repo, pending, 2 unheard."

    # The session's next prompt: SET_FOREGROUND WITH a tty (the provisional-clear
    # trigger) — sets the workspace pointer AND re-captures identity.
    with dst._state.transaction():
        dst.handle_message({"v": PROTOCOL_VERSION, "type": MsgType.SET_FOREGROUND,
                            "session": "s1", "cwd": "/x/repo", "tty": "/dev/ttys404"})
    assert sessions.is_provisional("s1") is False
    assert sessions.workspace() == "s1"

    # Quarantine lifted, but the tty it captured is a node that never existed:
    # the map re-tiers it from pending to closed (D3 §4a), still named because
    # it still holds content.
    assert control._also_clause(dst) == " Also: 1 repo, closed, 2 unheard."

    # Catch-up reads the FRONTIER'd tail (2 items), not the whole restored pile.
    with dst._state.transaction():
        dst.handle_message({"v": PROTOCOL_VERSION, "type": MsgType.CATCH_UP,
                            "session": "s1"})
    acks = [it.text for it in dst._stream("s1").queue._items if "Catching up" in it.text]
    assert acks == ["Catching up 2 items in repo."]

    # WHERE_AM_I now reflects the restored unheard for the (now non-provisional) session.
    assert "unheard" in control._entry_clauses(dst, "s1")


def test_sigterm_handler_requests_clean_shutdown_so_flush_runs():
    import signal
    from tests.daemon_helpers import make_daemon
    # A SIGTERM (what `launchctl unload` sends on `sonari install`) must drop the
    # daemon out of run()'s loop so the finally's stop->join->flush runs — without
    # this handler, Python's default SIGTERM kills the process skipping `finally`,
    # and only the debounced periodic writer's last save survives.
    daemon, *_ = make_daemon(foreground=None)
    daemon._running.set()
    daemon._wake.clear()
    old = signal.getsignal(signal.SIGTERM)
    try:
        daemon._install_signal_handlers()
        assert signal.getsignal(signal.SIGTERM) == daemon._on_shutdown_signal
        daemon._on_shutdown_signal(signal.SIGTERM, None)   # simulate the signal
        assert not daemon._running.is_set()   # loop exits -> finally -> flush
        assert daemon._wake.is_set()          # speak loop woken for a prompt join
    finally:
        signal.signal(signal.SIGTERM, old)    # restore pytest's handler


def test_stopped_only_stream_is_serialized_and_restored():
    # The widened _snapshot_state filter: a muted stream with NO frontier must
    # still persist (the mute is its sole durable fact — E4b).
    from tests.daemon_helpers import make_daemon
    src, _q, _sp, _se, _c = make_daemon(foreground=None)
    src.sessions.register("m1", cwd="/x/m")
    src._stream("m1").stopped = True
    with src._lock:
        data = src._snapshot_state()
    assert data["streams"]["m1"] == {"frontier": None, "stopped": True}
    src._store.save(data)
    dst, _q, speaker, sessions, _c = make_daemon(foreground=None)
    dst._restore_state()
    assert dst._streams["m1"].stopped is True
    assert dst._streams["m1"].frontier is None


def test_old_state_files_without_stopped_load_unmuted():
    from sonari.session_stream import SessionStream
    st = SessionStream()
    st.load_state({"frontier": [3, 1]})                  # pre-0.8.0 stream shape
    assert st.frontier == (3, 1)
    assert st.stopped is False
