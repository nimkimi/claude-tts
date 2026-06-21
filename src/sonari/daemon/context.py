from __future__ import annotations


class Ctx:
    def __init__(self, host):
        self._host = host
        self._msg = {}

    def bind(self, msg):
        self._msg = msg
        return self

    @property
    def host(self):
        return self._host

    @property
    def speaker(self):
        return self._host.speaker

    @property
    def sessions(self):
        return self._host.sessions

    @property
    def config(self):
        return self._host.config

    @property
    def history(self):
        return self._host.history

    @property
    def session(self):
        return self._msg.get("session", "")

    @property
    def verbosity(self):
        return self._host.config.get("verbosity", "everything")
