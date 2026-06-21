from __future__ import annotations

import socket
import threading

from sonari.protocol import encode, decode
from sonari.platform import transport

_MAX_CONN_THREADS = 32


class Server:
    """Owns the localhost-TCP transport: bind/listen, the accept loop, the
    bounded connection-handler pool (+ M8 permit-leak recovery), the token
    handshake, and newline framing. Holds NO daemon state and never takes the
    daemon lock; per message it calls the injected `dispatch` callback — the
    host's locked dispatch entry, which opens state.transaction() around
    handle_message. Lifecycle is shared with the host via the same `running`
    Event (the accept/conn loops gate on it; the host's stop() clears it)."""

    def __init__(self, dispatch, token_provider, running):
        self._dispatch = dispatch                 # host._handle_message_guarded
        self._token_provider = token_provider     # callable -> current token str
        self._running = running                   # the host's shared Event
        self._sock = None
        self._accept_thread = None
        self._conn_sem = threading.BoundedSemaphore(_MAX_CONN_THREADS)

    def bind(self) -> int:
        """Bind an ephemeral localhost port and listen. Returns the port. Does
        NOT start accepting (the caller writes the lockfile + sets running
        first, so the accept loop never observes running==False at startup)."""
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((transport.HOST, 0))
        srv.listen(16)
        self._sock = srv
        return srv.getsockname()[1]

    def serve(self) -> None:
        """Spawn the accept thread (running must already be set)."""
        self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._accept_thread.start()

    def join(self, timeout=None) -> None:
        if self._accept_thread is not None:
            self._accept_thread.join(timeout)

    def is_alive(self) -> bool:
        return self._accept_thread is not None and self._accept_thread.is_alive()

    def stop(self) -> None:
        srv = self._sock
        if srv is not None:
            try:
                srv.close()
            except OSError:
                pass

    def _accept_loop(self) -> None:
        srv = self._sock
        while self._running.is_set():
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            self._spawn_conn_handler(conn)

    def _handle_conn(self, conn) -> None:
        try:
            buf = b""
            with conn:
                conn.settimeout(5.0)
                # --- token handshake: the first newline-terminated line must
                # equal the daemon's session token, or the peer is dropped. ---
                while b"\n" not in buf:
                    try:
                        data = conn.recv(4096)
                    except (OSError, socket.timeout):
                        return
                    if not data:
                        return
                    buf += data
                token_line, buf = buf.split(b"\n", 1)
                if token_line.decode("utf-8", "replace") != self._token_provider():
                    return  # reject unauthenticated peer
                while self._running.is_set():
                    # Process any complete messages already buffered (e.g. a
                    # message that arrived in the same packet as the token).
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        if not line.strip():
                            continue
                        try:
                            msg = decode(line)
                        except (ValueError, UnicodeDecodeError):
                            continue
                        reply = self._dispatch(msg)
                        if reply is not None:
                            try:
                                conn.sendall(encode(reply))
                            except OSError:
                                return
                    try:
                        data = conn.recv(4096)
                    except (OSError, socket.timeout):
                        return
                    if not data:
                        return
                    buf += data
        except OSError:
            return

    def _handle_conn_guarded(self, conn) -> None:
        """Run _handle_conn, contain any crash (log it, don't die silently), and
        always release the concurrency permit so capacity recovers."""
        try:
            self._handle_conn(conn)
        except Exception:  # noqa: BLE001 - a handler crash must be logged, not silent
            import sys
            import traceback
            traceback.print_exc(file=sys.stderr)
        finally:
            self._conn_sem.release()

    def _spawn_conn_handler(self, conn) -> bool:
        """Spawn a handler thread for *conn* if under the concurrency cap; else
        drop (close) the connection. Returns True iff a handler was spawned."""
        if not self._conn_sem.acquire(blocking=False):
            try:
                conn.close()
            except OSError:
                pass
            return False
        try:
            th = threading.Thread(target=self._handle_conn_guarded, args=(conn,), daemon=True)
            th.start()
        except Exception:  # noqa: BLE001 - thread creation can fail (resource limits)
            # The handler that would release the permit never ran: release it here
            # and drop the connection, else this slot leaks forever (M8).
            self._conn_sem.release()
            try:
                conn.close()
            except OSError:
                pass
            return False
        return True
