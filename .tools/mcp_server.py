#!/usr/bin/env python3
"""Expose the knowledge base to an AI assistant, over MCP on stdin/stdout.

WHY THIS EXISTS. The CLI is the tool; this is reach. An assistant inside VS Code, Cursor, Zed or
Claude Desktop has no shell, so `query_code_index.py` is simply unreachable there -- and even where
a shell exists, nothing tells an assistant that a project-specific knowledge base is worth asking.
An advertised tool gets used unprompted, which is the habit the KB depends on.

IT OWNS NO KNOWLEDGE OF THE KB, for the same reason serve_code_index.py does not: it asks the CLI
which commands exist and forwards every query with --json. A search added to the CLI is available
here the moment it exists, and there is one implementation of each search rather than two that
drift. tests/test_engine.py asserts the two lists match.

ONE TOOL, NOT TWENTY-FOUR. A tool definition is context, re-sent on every turn of every
conversation whether or not the KB is touched. Twenty-four tools would spend that budget forever;
one tool with a `command` enum costs a fraction and stays correct as commands are added.

    python3 .tools/mcp_server.py            # speaks MCP on stdin/stdout

Standard library only -- no SDK, matching the rest of the engine. Wire it into a client as a stdio
server; see SETUP.md.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
REPO_ROOT = TOOLS.parents[0]
QUERY = TOOLS / "query_code_index.py"

# The protocol revision this speaks. A client asking for a different one is answered with ITS
# version when we can serve it -- MCP negotiates rather than rejecting -- and with ours otherwise.
PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "waymark", "version": "1"}

# Flags worth forwarding, and WHICH SIDE OF THE COMMAND NAME each belongs on. argparse puts
# --json/--full on the parser and --limit/--status on the subparser, so a flag on the wrong side is
# a usage error that returns rc=2 and no output -- indistinguishable from "nothing recorded".
# Same split serve_code_index.py documents, and the same reason.
GLOBAL_BOOL = {"full", "brief"}
SUB_VALUE = {"limit", "status", "evidence", "branches"}
SUB_BOOL = {"dead-first", "include-deleted"}


def cli(args: list) -> tuple:
    """Run the query tool. No shell anywhere -- arguments go straight to exec."""
    proc = subprocess.run([sys.executable, str(QUERY)] + args,
                          cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=120)
    return proc.returncode, proc.stdout, proc.stderr


def known_commands() -> list:
    rc, out, _ = cli(["--list-commands"])
    try:
        return json.loads(out) if rc == 0 else []
    except ValueError:
        return []


def tool_definition(commands: list) -> dict:
    return {
        "name": "waymark_query",
        "description": (
            "Search this project's knowledge base and source index. Consult it BEFORE reading "
            "source or starting an investigation: it records why things are the way they are, "
            "gotchas, root causes, and hypotheses that were tested and died.\n"
            "Useful starting points: `annotation <keyword>` searches the knowledge base by "
            "symptom; `symbol <name>` finds a definition; `comment <term>` finds rules left at "
            "the code site; `guard <SWITCH>` shows what a preprocessor switch gates.\n"
            "Available commands: " + ", ".join(commands)
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "enum": commands,
                            "description": "which search to run"},
                "term": {"type": "string",
                         "description": "the keyword, name or fragment to search for; omit for "
                                        "commands that take none, such as selftest or summary"},
                "full": {"type": "boolean",
                         "description": "full entries instead of the brief default -- ask for this "
                                        "once an entry looks relevant"},
                "limit": {"type": "integer", "description": "maximum rows"},
                "status": {"type": "string"},
                "evidence": {"type": "string"},
            },
            "required": ["command"],
        },
    }


def run_query(args: dict) -> tuple:
    """Build the argv the CLI expects, respecting which side of the command each flag sits on."""
    command = str(args.get("command") or "").strip()
    if command not in known_commands():
        return False, "unknown command %r -- available: %s" % (command, ", ".join(known_commands()))
    argv = ["--json"]
    for name in GLOBAL_BOOL:
        if args.get(name):
            argv.append("--" + name)
    argv.append(command)
    term = args.get("term")
    if term not in (None, ""):
        argv.append(str(term))
    for name in SUB_VALUE:
        val = args.get(name)
        if val not in (None, ""):
            argv += ["--" + name, str(val)]
    for name in SUB_BOOL:
        if args.get(name):
            argv.append("--" + name)
    rc, out, err = cli(argv)
    if rc != 0 and not out.strip():
        return False, (err.strip() or "query failed with exit code %d" % rc)
    return True, out.strip() or "[]"


def reply(msg_id, result=None, error=None) -> None:
    msg = {"jsonrpc": "2.0", "id": msg_id}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def main() -> int:
    commands = known_commands()
    if not commands:
        sys.stderr.write("waymark mcp: the query tool reported no commands -- is the index built?\n")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            continue
        method, msg_id = req.get("method"), req.get("id")
        # A notification has no id and takes no reply -- answering one is a protocol error.
        if msg_id is None:
            continue
        if method == "initialize":
            asked = (req.get("params") or {}).get("protocolVersion")
            reply(msg_id, {
                "protocolVersion": asked or PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
            })
        elif method == "tools/list":
            commands = known_commands() or commands
            reply(msg_id, {"tools": [tool_definition(commands)]})
        elif method == "tools/call":
            params = req.get("params") or {}
            if params.get("name") != "waymark_query":
                reply(msg_id, error={"code": -32602,
                                     "message": "unknown tool %r" % params.get("name")})
                continue
            ok, text = run_query(params.get("arguments") or {})
            reply(msg_id, {"content": [{"type": "text", "text": text}], "isError": not ok})
        elif method == "ping":
            reply(msg_id, {})
        else:
            reply(msg_id, error={"code": -32601, "message": "method not found: %s" % method})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
