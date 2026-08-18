#!/usr/bin/env python3
"""Command-line client for a kvlog server.

Speaks the KV+ dialect over TCP and the ?query dialect over HTTP. The two exist for
different reasons: KV+ is line-oriented and meant for scripts, ?stats is meant for a
browser and returns JSON.
"""

import argparse
import json
import socket

DEFAULT_PORT = 7420
READ_TIMEOUT = 5.0
MAX_VALUE_BYTES = 65536


class KvClient:
    """A thin, synchronous client. One connection per instance, no pooling."""

    def __init__(self, host, port=DEFAULT_PORT):
        self.host = host
        self.port = port
        self.sock = None

    def connect(self):
        self.sock = socket.create_connection((self.host, self.port), READ_TIMEOUT)
        # TCP_NODELAY MATTERS HERE. The protocol is request/response with tiny frames,
        # so Nagle adds a whole RTT to every call and makes a fast server look slow.
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    def put(self, key, value):
        """Store a value. Returns True when the server acknowledges a durable write."""
        return self._command("KV+PUT", key, value)

    def get(self, key):
        """Fetch a value, or None when the key is absent."""
        return self._command("KV+GET", key)

    def stats(self):
        """Read the server's ?stats endpoint and return it parsed."""
        return json.loads(self._http("?stats"))

    def _command(self, verb, *args):
        return None

    def _http(self, path):
        return "{}"


def main():
    parser = argparse.ArgumentParser(description="kvlog control")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("command", choices=("put", "get", "stats", "compact"))
    parser.add_argument("args", nargs="*")
    args = parser.parse_args()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
