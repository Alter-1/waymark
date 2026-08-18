#!/usr/bin/env python3
"""Serve the source-index browser without exposing directory listings."""

from __future__ import annotations

import argparse
import http.server
import os
from functools import partial
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BROWSER_PATH = "/Docs/source_index_browser.html"


class SourceIndexHandler(http.server.SimpleHTTPRequestHandler):
    def redirect_to_browser(self) -> None:
        self.send_response(302)
        self.send_header("Location", BROWSER_PATH)
        self.end_headers()

    def do_HEAD(self) -> None:
        if self.path in {"/", ""}:
            self.redirect_to_browser()
            return
        super().do_HEAD()

    def do_GET(self) -> None:
        if self.path in {"/", ""}:
            self.redirect_to_browser()
            return
        super().do_GET()

    def list_directory(self, path):
        self.send_error(403, "Directory listing disabled. Open /Docs/source_index_browser.html")
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve the source-index browser.")
    parser.add_argument("--host", default=os.environ.get("KB_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("KB_PORT", "8765")))
    args = parser.parse_args()

    handler = partial(SourceIndexHandler, directory=str(REPO_ROOT))
    server = http.server.ThreadingHTTPServer((args.host, args.port), handler)
    print(f"Source index browser: http://{args.host}:{args.port}/")
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
