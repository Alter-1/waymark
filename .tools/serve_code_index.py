#!/usr/bin/env python3
"""Serve the KB browser, and answer its queries by running the CLI.

WHY IT DISPATCHES INSTEAD OF QUERYING. The browser this replaces downloaded the whole exported
index -- 40 MB on a real tree -- and reimplemented the searches in JavaScript. Two consequences,
both of which happened: it offered a fraction of the CLI's subcommands, and it never learned about
new ones (`relations` shipped and the browser knew nothing about it). So this server owns NO
knowledge of the KB. It asks query_code_index.py which commands exist, and forwards every query to
it with --json. A search added to the CLI is available here the moment it exists, and there is one
implementation of each search rather than two that drift.

    python3 .tools/serve_code_index.py            # http://127.0.0.1:8765/

Local by default and by intent: it runs a subprocess per query and serves the KB, neither of which
belongs on a shared address.
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import subprocess
import sys
import urllib.parse
from functools import partial
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
REPO_ROOT = TOOLS.parents[0]
QUERY = TOOLS / "query_code_index.py"
BROWSER = "kb_browser.html"

# Flags the browser may pass through, by name. NOT a blanket forward: everything here reaches a
# subprocess argument list, so the set is small, explicit, and reviewed.
# THE SPLIT MATTERS. argparse puts --json/--db/--full on the PARSER and --limit/--status on each
# SUBPARSER, so a flag on the wrong side of the command name is a usage error, not an option --
# which came back as rc=2 and an empty result, i.e. indistinguishable from "nothing recorded".
GLOBAL_VALUE = {"db"}
GLOBAL_BOOL = {"full", "brief"}
SUB_VALUE = {"limit", "status", "evidence", "branches"}
SUB_BOOL = {"dead-first", "include-deleted"}


def cli(args: list) -> tuple:
    """Run the query tool. Returns (exit code, stdout, stderr) -- no shell anywhere."""
    proc = subprocess.run([sys.executable, str(QUERY)] + args,
                          cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=120)
    return proc.returncode, proc.stdout, proc.stderr


def known_commands() -> list:
    rc, out, _ = cli(["--list-commands"])
    try:
        return json.loads(out) if rc == 0 else []
    except ValueError:
        return []


class Handler(http.server.BaseHTTPRequestHandler):
    commands: list = []

    def log_message(self, fmt, *a):        # one line per query, not per asset
        if self.path.startswith("/api/"):
            sys.stderr.write("  %s\n" % self.path[:160])

    def reply(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def reply_json(self, code: int, payload) -> None:
        self.reply(code, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        url = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(url.query)

        if url.path in ("/", ""):
            page = TOOLS / BROWSER
            if not page.is_file():
                self.reply(404, b"kb_browser.html is missing next to serve_code_index.py",
                           "text/plain; charset=utf-8")
                return
            self.reply(200, page.read_bytes(), "text/html; charset=utf-8")
            return

        if url.path == "/api/commands":
            self.reply_json(200, {"commands": self.commands})
            return

        if url.path == "/api/q":
            cmd = (q.get("cmd") or [""])[0]
            # An unknown command must be refused rather than forwarded: this builds an argument
            # list for a subprocess, and the allowlist is the CLI's own subcommand table.
            if cmd not in self.commands:
                self.reply_json(400, {"error": "unknown command: %s" % cmd,
                                      "commands": self.commands})
                return
            def flags(bools, values):
                out = []
                for name in sorted(bools):
                    if (q.get(name) or ["0"])[0] not in ("0", "", "false"):
                        out.append("--" + name)
                for name in sorted(values):
                    value = (q.get(name) or [""])[0]
                    if value:
                        out += ["--" + name, value]
                return out

            argv = ["--json"] + flags(GLOBAL_BOOL, GLOBAL_VALUE) + [cmd]
            term = (q.get("term") or [""])[0]
            if term:
                argv.append(term)
            argv += flags(SUB_BOOL, SUB_VALUE)
            rc, out, err = cli(argv)
            try:
                self.reply_json(200, {"rc": rc, "rows": json.loads(out) if out.strip() else []})
            except ValueError:
                # Not JSON: a stale index, a usage error, an empty-result note. Hand it back as
                # text rather than as an empty result, which reads as "nothing recorded".
                self.reply_json(200, {"rc": rc, "text": (out + err).strip()[:4000]})
            return

        self.reply(404, b"not found -- this server exposes / and /api/*",
                   "text/plain; charset=utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="Serve the KB browser.")
    ap.add_argument("--host", default=os.environ.get("KB_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("KB_PORT", "8765")))
    a = ap.parse_args()

    Handler.commands = known_commands()
    if not Handler.commands:
        print("warning: could not ask the CLI for its subcommands -- is the index built?",
              file=sys.stderr)
    server = http.server.ThreadingHTTPServer((a.host, a.port), partial(Handler))
    print("KB browser: http://%s:%d/   (%d searches)" % (a.host, a.port, len(Handler.commands)))
    print("Ctrl-C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
