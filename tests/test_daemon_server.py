"""Unit pins for the extracted Server transport (Task 4.1).

Drives Server directly with a fake dispatch + a real threading.Event for
'running', using in-process loopback connections for round-trip tests.
"""
from __future__ import annotations

import socket
import threading
import time

import pytest

from sonari.protocol import encode, decode


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_server(dispatch=None, token="testtoken"):
    """Build a Server with its accept thread NOT yet started.

    Returns (server, running_event).  Caller calls server.bind() then
    server.serve() if it needs accept-loop coverage.
    """
    from sonari.daemon.server import Server

    running = threading.Event()
    if dispatch is None:
        dispatch = lambda msg: None  # noqa: E731
    srv = Server(
        dispatch=dispatch,
        token_provider=lambda: token,
        running=running,
    )
    return srv, running


def _connect_authenticated(host, port, token="testtoken") -> socket.socket:
    """Open a connection, send the token handshake, return the socket."""
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client.connect((host, port))
    client.settimeout(2.0)
    client.sendall((token + "\n").encode("utf-8"))
    return client


# ---------------------------------------------------------------------------
# Handshake / framing
# ---------------------------------------------------------------------------

def test_handshake_rejects_wrong_token():
    """A peer whose first line ≠ the token is dropped; dispatch is never called."""
    dispatched = []

    def fake_dispatch(msg):
        dispatched.append(msg)
        return None

    srv, running = _make_server(dispatch=fake_dispatch)
    running.set()
    port = srv.bind()
    srv.serve()
    time.sleep(0.02)

    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client.connect(("127.0.0.1", port))
    client.settimeout(2.0)
    client.sendall(b"wrongtoken\n")
    # The server closes the connection; recv returns b"" on clean close.
    data = b""
    try:
        data = client.recv(4096)
    except OSError:
        pass
    finally:
        client.close()

    running.clear()
    srv.stop()
    assert dispatched == []
    assert data == b""


def test_handshake_accepts_and_dispatches_message():
    """Authenticated peer: token\n then {json}\n → dispatch called once with decoded dict."""
    received = []

    def fake_dispatch(msg):
        received.append(msg)
        return {"ok": True}

    srv, running = _make_server(dispatch=fake_dispatch)
    running.set()
    port = srv.bind()
    srv.serve()
    time.sleep(0.02)

    client = _connect_authenticated("127.0.0.1", port)
    client.sendall(encode({"type": "PING"}))

    buf = b""
    while b"\n" not in buf:
        buf += client.recv(4096)
    client.close()
    running.clear()
    srv.stop()

    assert len(received) == 1
    assert received[0] == {"type": "PING"}
    reply = decode(buf.split(b"\n")[0])
    assert reply == {"ok": True}


def test_handshake_accepts_two_messages_on_same_connection():
    """Second {json}\\n on the same connection dispatches again (connection reuse)."""
    received = []

    def fake_dispatch(msg):
        received.append(msg)
        return None

    srv, running = _make_server(dispatch=fake_dispatch)
    running.set()
    port = srv.bind()
    srv.serve()
    time.sleep(0.02)

    client = _connect_authenticated("127.0.0.1", port)
    client.sendall(encode({"type": "PING"}))
    client.sendall(encode({"type": "STATUS"}))
    # Give the server time to process both messages.
    time.sleep(0.1)
    client.close()
    running.clear()
    srv.stop()

    assert len(received) == 2


def test_same_packet_message_after_token():
    """token\\n{json}\\n in one send still dispatches the buffered message.

    Pins the 'process already-buffered' inner loop in _handle_conn.
    """
    received = []

    def fake_dispatch(msg):
        received.append(msg)
        return None

    srv, running = _make_server(dispatch=fake_dispatch)
    running.set()
    port = srv.bind()
    srv.serve()
    time.sleep(0.02)

    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client.connect(("127.0.0.1", port))
    client.settimeout(2.0)
    # Send token + message in a single write so they arrive in the same recv.
    combined = b"testtoken\n" + encode({"type": "PING"})
    client.sendall(combined)
    time.sleep(0.1)
    client.close()
    running.clear()
    srv.stop()

    assert len(received) == 1
    assert received[0] == {"type": "PING"}


# ---------------------------------------------------------------------------
# Connection cap (M14 / conn_sem)
# ---------------------------------------------------------------------------

def test_conn_cap_drops_connection_when_exhausted():
    """With _conn_sem exhausted a new connection is closed without spawning a handler."""
    from sonari.daemon.server import _MAX_CONN_THREADS

    srv, running = _make_server()
    running.set()
    # Exhaust all permits manually (no actual threads).
    for _ in range(_MAX_CONN_THREADS):
        srv._conn_sem.acquire(blocking=False)

    closed = {"n": 0}

    class FakeConn:
        def close(self):
            closed["n"] += 1

    result = srv._spawn_conn_handler(FakeConn())
    running.clear()
    srv.stop()

    assert result is False
    assert closed["n"] == 1


# ---------------------------------------------------------------------------
# M8 permit-recovery on thread-start failure
# ---------------------------------------------------------------------------

def test_m8_permit_recovered_when_thread_start_fails(monkeypatch):
    """If Thread.start() raises, the permit is released (no leak) and conn closed."""
    from sonari.daemon.server import _MAX_CONN_THREADS

    class BoomThread:
        def __init__(self, *a, **k):
            pass

        def start(self):
            raise RuntimeError("cannot create thread")

    monkeypatch.setattr("threading.Thread", BoomThread)

    srv, running = _make_server()
    running.set()

    closed = {"n": 0}

    class FakeConn:
        def close(self):
            closed["n"] += 1

    result = srv._spawn_conn_handler(FakeConn())
    running.clear()
    srv.stop()

    assert result is False
    assert closed["n"] == 1

    # All permits must be free — none leaked.
    n = 0
    while srv._conn_sem.acquire(blocking=False):
        n += 1
    assert n == _MAX_CONN_THREADS
