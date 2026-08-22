#!/usr/bin/env python3
"""Query the repo source index built by .tools/index_code.py."""

from __future__ import annotations

import argparse
import json
import sqlite3
import re
import subprocess
import sys
from pathlib import Path


def _ensure_json_functions(con: sqlite3.Connection) -> None:
    """Register a json_extract() fallback when SQLite was built without JSON1.

    _AT_ 200826 19:05  The `notes` subcommand is the only query that calls json_extract, and it
    died with "no such function: json_extract" on this toolchain: the build Python here is 3.7.8
    with SQLite 3.31.1 and no JSON1 extension. Notes were still being WRITTEN to
    Docs/symbol_notes.json and folded into the index, so nothing was lost - but they could never
    be read back, which quietly removes the recall half of "record findings to the KB".
    Only $.key paths are used, so a small pure-Python extractor is enough.
    """
    try:
        con.execute("SELECT json_extract('{\"a\":1}', '$.a')").fetchone()
        return
    except sqlite3.OperationalError:
        pass

    def json_extract(doc, path):
        if doc is None or not path or not path.startswith("$"):
            return None
        try:
            cur = json.loads(doc)
        except (TypeError, ValueError):
            return None
        for part in [p for p in path[1:].split(".") if p]:
            if isinstance(cur, dict):
                cur = cur.get(part)
            elif isinstance(cur, list) and part.isdigit():
                cur = cur[int(part)] if int(part) < len(cur) else None
            else:
                return None
            if cur is None:
                return None
        return cur if isinstance(cur, str) else json.dumps(cur, ensure_ascii=False)

    con.create_function("json_extract", 2, json_extract)


REPO_ROOT = Path(__file__).resolve().parents[1]
BRIEF_VALUE_LINES = 4
BRIEF_VALUE_CHARS = 500


def current_branch_slug() -> str:
    try:
        branch = subprocess.check_output(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "--abbrev-ref", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        branch = "nogit"
    if not branch or branch == "HEAD":
        branch = "detached"
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", branch).strip("._-")
    return slug or "unknown"


DEFAULT_DB = REPO_ROOT / ".tools" / f"code_index.{current_branch_slug()}.sqlite"


def branch_db_path(branch: str) -> Path:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", branch).strip("._-") or "unknown"
    return REPO_ROOT / ".tools" / f"code_index.{slug}.sqlite"


def branch_from_db_path(path: Path) -> str:
    name = path.name
    prefix = "code_index."
    suffix = ".sqlite"
    if name.startswith(prefix) and name.endswith(suffix):
        return name[len(prefix):-len(suffix)]
    return path.stem


def selected_branch_dbs(branches: str, current_db: Path = DEFAULT_DB) -> list[tuple[str, Path]]:
    if branches == "current":
        return [(current_branch_slug(), current_db)]
    if branches == "all":
        return sorted(
            (branch_from_db_path(path), path)
            for path in (REPO_ROOT / ".tools").glob("code_index.*.sqlite")
        )
    return [(branch.strip(), branch_db_path(branch.strip())) for branch in branches.split(",") if branch.strip()]


def rows_to_dicts(cursor: sqlite3.Cursor) -> list[dict]:
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def brief_value(value: object) -> str:
    text = str(value)
    try:
        parsed = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        parsed = None
    if isinstance(parsed, dict):
        lines: list[str] = []
        for key in ("concept_id", "kind", "min_fw"):
            if parsed.get(key) not in {"", None}:
                lines.append(f"{key}: {parsed[key]}")
        if parsed.get("keywords"):
            keywords = ", ".join(str(k) for k in parsed["keywords"][:12])
            if len(parsed["keywords"]) > 12:
                keywords += ", ..."
            lines.append(f"keywords: {keywords}")
        if parsed.get("files"):
            files = ", ".join(str(f) for f in parsed["files"][:4])
            if len(parsed["files"]) > 4:
                files += ", ..."
            lines.append(f"files: {files}")
        if parsed.get("see_also"):
            refs = parsed["see_also"]
            if isinstance(refs, (str, dict)):
                refs = [refs]
            shown_refs = []
            for ref in refs[:8]:
                if isinstance(ref, dict):
                    target = ref.get("target") or ref.get("name") or ref.get("value")
                    ref_type = ref.get("type") or ref.get("target_type")
                    shown_refs.append(f"{ref_type}:{target}" if ref_type and target else str(ref))
                else:
                    shown_refs.append(str(ref))
            text = ", ".join(shown_refs)
            if len(refs) > 8:
                text += ", ..."
            lines.append(f"see_also: {text}")
        body_key = next((key for key in ("brief", "notes", "meaning", "value") if parsed.get(key) not in {"", None}), None)
        body = parsed.get(body_key) if body_key else None
        if body not in {"", None}:
            body_text = brief_value(body)
            if "\n" in body_text:
                lines.append(f"{body_key}:")
                lines.extend(f"  {line}" for line in body_text.splitlines())
            else:
                lines.append(f"{body_key}: {body_text}")
        return "\n".join(lines) if lines else text
    lines = text.splitlines()
    truncated = len(lines) > BRIEF_VALUE_LINES
    shown = lines[:BRIEF_VALUE_LINES]
    out = "\n".join(shown)
    if len(out) > BRIEF_VALUE_CHARS:
        out = out[:BRIEF_VALUE_CHARS].rstrip()
        truncated = True
    if truncated:
        out += "\n..."
    return out


# THE HEADLINE IS WHAT GETS READ, AND IT CAN OUTLIVE THE ENTRY UNDER IT.
# Two incidents in a single day (2026-08-21) had exactly this shape: a one-line summary said one
# thing, the body under it said the opposite, and the summary is what got acted on.
#     "AT+R stays silent-reset"          -- the note itself said: bare AT+R is a FACTORY RESET
#     "...fix (2.1 done, 2.0 pending)"   -- the body said: PORTED TO ALL 3 BRANCHES, bench-validated
# The first one cost a bench device its entire configuration. Neither entry was WRONG; both were
# right underneath and stale on top. A lint would not have helped, because nobody runs a lint at
# the moment they are reading an answer -- so the warning belongs HERE, in the reader.
#
# ONLY THE VERDICT POSITION IS TESTED: the opening of the brief, where an entry states what it
# concluded. The rest of a brief legitimately narrates history ("...was believed fixed until..."),
# and matching there flagged entries whose brief literally opened with "OPEN LEAD". Anchored, a
# real 206-entry KB flags exactly one -- and that one is a genuine disagreement. A check that cries
# wolf on a healthy KB is worse than no check at all.
_VERDICT_HEAD = 160
_VERDICT_DONE = re.compile(r"^\W*(RESOLVED|FIXED|SOLVED|CLOSED|DONE)\b", re.I)
_VERDICT_OPEN = re.compile(r"^\W*(STILL\s+OPEN|OPEN|PENDING|UNRESOLVED|NOT\s+FIXED|TODO)\b", re.I)


def verdict_conflict(row: dict) -> str:
    """Does this entry's headline disagree with its own status? Returns the warning, or "".

    Deliberately NOT a judgement about which one is right: a mixed state is legitimate (a fix that
    landed while its bench re-test is still outstanding is honestly both). The point is to say so
    at the moment somebody is about to act on the headline alone.
    """
    value = row.get("value")
    if isinstance(value, (str, bytes)):
        try:
            value = json.loads(value)
        except Exception:
            return ""
    if not isinstance(value, dict):
        return ""
    status = str(value.get("status") or "").strip().lower()
    brief = str(value.get("brief") or "").strip()[:_VERDICT_HEAD]
    if not status or not brief:
        return ""
    for state, pattern in (("open", _VERDICT_DONE), ("resolved", _VERDICT_OPEN)):
        if status != state:
            continue
        found = pattern.search(brief)
        if found:
            return (f"status={state}, but the brief opens by calling it "
                    f"{found.group(1).upper()} -- read the notes, not the headline")
    return ""


def print_rows(
    rows: list[dict],
    as_json: bool,
    brief: bool,
    more_available: bool = False,
    limit: int | None = None,
    query_text: str = "",
) -> None:
    # Machine consumers get the same warning as a field -- an agent reading --json would otherwise
    # be the one reader that never sees it, and agents are most of the traffic here.
    #
    # Spelled without ':=' on purpose. The walrus is 3.8+, and this engine is run on a 3.7 host --
    # the same one the json_extract fallback exists for. A SyntaxError is worse than a missing
    # feature: it takes out every query, including selftest, so the KB reports nothing rather than
    # reporting less.
    def _with_conflict(row):
        conflict = verdict_conflict(row)
        return dict(row, verdict_conflict=conflict) if conflict else row

    rows = [_with_conflict(r) for r in rows]
    if as_json:
        if limit is not None:
            print(json.dumps(
                {
                    "rows": rows,
                    "limit": limit,
                    "more_available": more_available,
                    "query": query_text,
                },
                indent=2,
                ensure_ascii=False,
                sort_keys=True,
            ))
            return
        print(json.dumps(rows, indent=2, ensure_ascii=False, sort_keys=True))
        return
    if not rows:
        print("No matches")
    else:
        for row in rows:
            loc = ""
            if row.get("file"):
                loc = str(row["file"])
                if row.get("line") not in {"", None}:
                    loc += f":{row['line']}"
            head = " ".join(str(row.get(k, "")) for k in ("name", "kind", "marker", "symbol", "table") if row.get(k))
            print(f"{head} {loc}".strip())
            # Immediately under the name, before the value it contradicts -- a warning printed
            # after 40 lines of notes is a warning read after the decision was made.
            if row.get("verdict_conflict"):
                print(f"  !! {row['verdict_conflict']}")
            for key, value in row.items():
                if key in {"name", "kind", "marker", "symbol", "file", "line", "verdict_conflict"} or value in {"", None, "[]"}:
                    continue
                if not brief:
                    print(f"  {key}: {value}")
                    continue
                text = brief_value(value)
                if "\n" in text:
                    print(f"  {key}:")
                    for line in text.splitlines():
                        print(f"    {line}")
                else:
                    print(f"  {key}: {text}")
    if more_available:
        if query_text:
            print(f"More results available for '{query_text}'; rerun with --limit above {limit}.")
        else:
            print(f"More results available; rerun with --limit above {limit}.")


def limited_rows(cursor: sqlite3.Cursor, limit: int) -> tuple[list[dict], bool]:
    rows = rows_to_dicts(cursor)
    return rows[:limit], len(rows) > limit


def print_limited_rows(
    cursor: sqlite3.Cursor,
    limit: int,
    as_json: bool,
    brief: bool,
    query_text: str = "",
) -> None:
    rows, more_available = limited_rows(cursor, limit)
    print_rows(rows, as_json, brief, more_available=more_available, limit=limit, query_text=query_text)


def print_empty_note(note: str, as_json: bool, limit: int | None = None, query_text: str = "") -> None:
    if as_json:
        payload = {"rows": [], "note": note}
        if limit is not None:
            payload.update({"limit": limit, "more_available": False, "query": query_text})
        print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))
        return
    print(note)


def fetch_symbol_rows_from_db(
    db_path: Path,
    branch_label: str,
    name: str,
    include_deleted: bool,
) -> list[dict]:
    if not db_path.exists():
        return [{
            "name": name,
            "kind": "missing_db",
            "branch": branch_label,
            "status": "missing_db",
            "note": f"Index DB not found: {db_path}",
        }]
    con = sqlite3.connect(db_path)
    _ensure_json_functions(con)
    # SUBSTRING MATCH, like every other subcommand. The bare name was passed straight to LIKE, so
    # this was an EXACT match -- and since member definitions are indexed by their qualified name,
    # `symbol write_sync` could never find BaseSerial::write_sync.
    like = name if "%" in name else f"%{name}%"
    try:
        branch_row = con.execute("SELECT value FROM meta WHERE key='branch'").fetchone()
        branch = branch_row[0] if branch_row else branch_label
        if con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='branch_symbols'"
        ).fetchone():
            status_clause = "" if include_deleted else "AND b.status='active'"
            cur = con.execute(
                f"""
                SELECT b.name, b.kind, b.file, b.line, b.signature,
                       CASE WHEN b.commented_out THEN 1 ELSE NULL END AS commented_out,
                       b.status, b.added_at, b.last_seen_at, b.deleted_at,
                       m.notes, m.keywords, m.see_also
                FROM branch_symbols b
                LEFT JOIN symbol_metadata m ON m.symbol_key=b.symbol_key
                WHERE b.name LIKE ? {status_clause}
                ORDER BY b.file, b.line
                """,
                (like,),
            )
            rows = rows_to_dicts(cur)
        else:
            cur = con.execute(
                """
                SELECT name, kind, file, line, signature,
                       CASE WHEN commented_out THEN 1 ELSE NULL END AS commented_out
                FROM symbols
                WHERE name LIKE ?
                ORDER BY file, line
                """,
                (like,),
            )
            rows = rows_to_dicts(cur)
        for row in rows:
            row["branch"] = branch
        return rows
    finally:
        con.close()


def print_symbol_rows_across_branches(
    name: str,
    branches: str,
    include_deleted: bool,
    limit: int,
    as_json: bool,
    brief: bool,
    current_db: Path = DEFAULT_DB,
) -> None:
    rows: list[dict] = []
    for branch, db_path in selected_branch_dbs(branches, current_db):
        rows.extend(fetch_symbol_rows_from_db(db_path, branch, name, include_deleted))
    rows.sort(key=lambda row: (str(row.get("branch", "")), str(row.get("file", "")), int(row.get("line") or 0), str(row.get("name", ""))))
    print_rows(rows[:limit], as_json, brief, more_available=len(rows) > limit, limit=limit, query_text=name)


def native_path_term(term: str) -> str:
    """Make a path argument match what the index stores.

    The index stores repo-relative paths via Path.as_posix(), i.e. always with forward slashes.
    A caller on Windows types them the way the OS shows them, with backslashes, and the LIKE
    comparison then matched nothing at all -- indistinguishable from "not indexed". Translating
    here keeps every stored path in one canonical form instead of storing both.
    """
    return term.replace("\\", "/")


def main() -> int:
    parser = argparse.ArgumentParser(description="Query source index.")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite DB path")
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    # THE SERVER ASKS FOR THIS rather than keeping its own allowlist. A hand-kept list is how the
    # old browser came to offer a fraction of the CLI's searches and never learn about new ones --
    # `relations` shipped and the browser knew nothing about it. Taken from argparse itself, so it
    # cannot drift from what actually exists.
    parser.add_argument("--list-commands", action="store_true",
                        help="Print every subcommand name as JSON and exit")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--brief", action="store_true", help="Emit compact text output (default)")
    mode.add_argument("--full", action="store_true", help="Emit expanded text output")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("summary")

    p = sub.add_parser("symbol")
    p.add_argument("name")
    p.add_argument("--limit", type=int, default=80)
    p.add_argument("--branches", default="current", help="current, all, or comma-separated branch names")
    p.add_argument("--include-deleted", action="store_true", help="Include deleted lifecycle rows")

    p = sub.add_parser("selftest",
                       help="check the KB for the ways it has silently gone wrong before")

    p = sub.add_parser("claim")
    p.add_argument("term", nargs="?", default="", help="words from the claim, or an entry name")
    p.add_argument("--status", default="", help="live, dead or open; all by default")
    p.add_argument("--evidence", default="", help="measured, inferred, reported or unstated")
    p.add_argument("--dead-first", action="store_true",
                   help="killed hypotheses first -- what has already been tested and disproved is "
                        "the most reusable thing here")
    p.add_argument("--since", default="",
                   help="only claims dated on or after this (YYYY-MM-DD) -- what a session "
                        "concluded, without reading a handover to find out")
    p.add_argument("--limit", type=int, default=80)

    p = sub.add_parser("param")
    p.add_argument("term", help="a field id (FU), a VALUE seen on a device (2, '*'), or words from "
                                "the meaning (single wire)")
    p.add_argument("--target", default="", help="restrict to one target from the parameter map; all by default")
    p.add_argument("--limit", type=int, default=80)

    p = sub.add_parser("constant")
    p.add_argument("name", help="a name, a name fragment, or a VALUE -- "
                                "`constant '*'` answers \"which constant is '*'?\"")
    p.add_argument("--limit", type=int, default=80)

    p = sub.add_parser("comment")
    p.add_argument("term", nargs="?", default="")
    p.add_argument("--limit", type=int, default=80)

    p = sub.add_parser("arch")
    p.add_argument("term", nargs="?", default="")
    p.add_argument("--limit", type=int, default=80)

    p = sub.add_parser("refs")
    p.add_argument("name")
    p.add_argument("--limit", type=int, default=80)

    p = sub.add_parser("api")
    p.add_argument("marker")
    p.add_argument("--limit", type=int, default=80)

    p = sub.add_parser("route")
    p.add_argument("term", nargs="?", default="")
    p.add_argument("--limit", type=int, default=80)

    p = sub.add_parser("concept")
    p.add_argument("term", nargs="?", default="")
    p.add_argument("--limit", type=int, default=80)

    p = sub.add_parser("override")
    p.add_argument("term", nargs="?", default="")
    p.add_argument("--limit", type=int, default=80)

    p = sub.add_parser("annotation")
    p.add_argument("term", nargs="?", default="")
    p.add_argument("--limit", type=int, default=80)

    # "What is still open?" -- the question a separate ticket system would exist to answer. It does
    # not need one: the notes already hold the problems, they only lacked a reliable status field.
    # "I am editing this function -- what do I need to know?" The inverse of a keyword search.
    # "What is this commit part of, and where has it landed?" One fix is many commits, and git
    # cannot group them; the notes can, so this reads that grouping back.
    p = sub.add_parser("commits", help="work item a commit belongs to, and its branch presence")
    p.add_argument("term", help="a sha prefix, or an annotation name")
    p.add_argument("--limit", type=int, default=80)

    p = sub.add_parser("notes", help="annotations that constrain a symbol")
    p.add_argument("symbol")
    # EXACT by default: `notes main` also returning app_main is noise on exactly the generic names
    # that are hardest to review. --fuzzy restores substring matching when you want it.
    p.add_argument("--fuzzy", action="store_true", help="substring match instead of exact")
    p.add_argument("--limit", type=int, default=80)

    p = sub.add_parser("open", help="list annotations with status=open")
    p.add_argument("term", nargs="?", default="")
    p.add_argument("--status", default="open",
                   help="open (default), resolved, wontfix, n/a, or all")
    p.add_argument("--limit", type=int, default=80)

    p = sub.add_parser("file")
    p.add_argument("term")
    p.add_argument("--limit", type=int, default=80)

    p = sub.add_parser("links")
    p.add_argument("term", nargs="?", default="")
    p.add_argument("--limit", type=int, default=80)

    p = sub.add_parser("graph",
                       help="the KB as nodes and edges -- see_also, relations, shared subjects")
    p.add_argument("term", nargs="?", default="", help="limit to entries matching this")
    p.add_argument("--limit", type=int, default=400)

    p = sub.add_parser("relations",
                       help="declared code relations and whether the tree still supports them")
    p.add_argument("term", nargs="?", default="", help="symbol, relation or status")
    p.add_argument("--limit", type=int, default=80)

    p = sub.add_parser("broken-links")
    p.add_argument("term", nargs="?", default="")
    p.add_argument("--limit", type=int, default=80)

    # BEFORE parse_args(): the subcommand is required, so --list-commands on its own would be

    # rejected as a usage error rather than answered.

    if "--list-commands" in sys.argv:

        print(json.dumps(sorted(sub.choices)))

        return 0


    args = parser.parse_args()
    brief = not args.full
    db_path = Path(args.db)
    if args.cmd == "symbol":
        print_symbol_rows_across_branches(
            args.name,
            args.branches,
            args.include_deleted,
            args.limit,
            args.json,
            brief,
            current_db=db_path,
        )
        return 0

    if not db_path.exists():
        print(f"Index DB not found: {db_path}", file=sys.stderr)
        print("Run: python3 .tools/index_code.py", file=sys.stderr)
        return 2
    con = sqlite3.connect(db_path)
    _ensure_json_functions(con)

    if args.cmd == "summary":
        rows = []
        for table in (
            "files", "symbols", "symbol_comments", "architecture_comments",
            "constants", "refs", "api_markers", "routes", "concepts",
            "branch_overrides", "kb_links", "symbol_lifecycle", "symbol_annotations", "commit_links",
            "symbol_metadata", "branch_symbols", "annotations", "params", "claims",
        ):
            count = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            rows.append({"table": table, "count": count})
        print_rows(rows, args.json, brief)
    elif args.cmd == "selftest":
        # THE FAILURES THIS EXISTS TO CATCH, each of which has happened:
        #   * a rebuild that errored midway left the index with 0 files/symbols and NOTHING said
        #     so, because the build output was being sent to /dev/null;
        #   * Docs/param_map_*.txt drifting from the code it is generated from, while being
        #     consulted as a reference;
        #   * a claim recorded with no provenance, so an inference reads like a measurement.
        problems, checks = [], []
        def chk(name, ok, detail=""):
            checks.append((name, "ok" if ok else "PROBLEM", detail))
            if not ok:
                problems.append(name)

        counts = {}
        for t in ("files", "symbols", "constants", "annotations", "params", "claims"):
            try:
                counts[t] = con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            except Exception:
                counts[t] = -1
        for t in ("files", "symbols"):
            chk(f"{t} non-empty", counts[t] > 0, f"{counts[t]} rows")

        # A CODEBASE WITH NO CONSTANTS IS NOT A BROKEN INDEX. files and symbols already prove the
        # index is not empty, which is the whole point of this guard; a C file with no #define, or a
        # project whose constants are simply not SHOUTED, legitimately has none. Failing there made
        # selftest red for a project that had done nothing wrong -- the same mistake as judging a
        # project for not having written its first annotation yet. Report it, do not judge it.
        if counts["constants"] > 0:
            chk("constants non-empty", True, f"{counts['constants']} rows")
        else:
            checks.append(("constants non-empty", "n/a", "this codebase defines none"))

        # ZERO ANNOTATIONS MEANS TWO VERY DIFFERENT THINGS. If the KB file is THERE and the index
        # still came back empty, that is the disaster this check exists for: the annotation file is
        # untracked, and a rebuild against a missing or truncated one produces an empty index
        # without erroring. But a project that has not written its first note yet is simply new --
        # failing there made a fresh install's very first selftest red for doing nothing wrong.
        # list(), not the bare glob: a generator object is ALWAYS truthy, so any(glob(...) ...)
        # reports "present" for every repository, including one with no KB at all.
        _root = Path(__file__).resolve().parents[1]
        kb_present = any(list(_root.glob(g)) for g in
                         ("Docs/source_index_annotations*.json", "source_index_annotations*.json"))
        if kb_present or counts["annotations"] > 0:
            chk("annotations non-empty", counts["annotations"] > 0, f"{counts['annotations']} rows")
        else:
            checks.append(("annotations non-empty", "n/a", "no KB authored yet"))

        # The validator writes ok / missing / ambiguous; renamed and expired come from the authored
        # JSON. An earlier version of this check accepted 'resolved' -- a word nothing ever writes,
        # taken from the resolved_kind column name -- so every VALID link would have counted as
        # broken. It passed only because no entry used see_also, i.e. a guard that had never been
        # seen to fire. Name the bad states explicitly rather than listing the good ones.
        broken = con.execute("SELECT count(*) FROM kb_links WHERE status IN "
                             "('missing','ambiguous')").fetchone()[0]
        chk("kb links resolve", broken == 0, f"{broken} unresolved (see broken-links)")

        # A HEADLINE THAT DISAGREES WITH ITS OWN ENTRY -- reported, never failed. verdict_conflict()
        # explains the two incidents behind it. This is a READING LIST, not a verdict: a mixed state
        # is legitimate (a fix that has landed while its bench re-test is still outstanding is
        # honestly both), so failing here would leave a healthy KB permanently red, and a check that
        # is always red is a check nobody reads. The reader prints the same warning inline, which is
        # where it actually catches somebody.
        conflicts = []
        try:
            for _name, _value in con.execute("SELECT name, value FROM annotations"):
                if verdict_conflict({"value": _value}):
                    conflicts.append(_name)
        except Exception:
            conflicts = []
        if conflicts:
            checks.append(("headline agrees with status", "REVIEW",
                           f"{len(conflicts)} to re-read: " + ", ".join(sorted(conflicts)[:4])))
        else:
            chk("headline agrees with status", True, "no entry contradicts its own status")

        # A RELATION THE TREE NO LONGER SUPPORTS IS A DEFECT, not a note: somebody asserted that a
        # path could not happen and it now can. Reported like a broken link, because it is one --
        # an authored claim about the code that the code has stopped honouring.
        # unchecked/unknown-relation are listed but never failed: they mean "nothing is watching
        # this", which the author needs to KNOW without it blocking every build.
        try:
            violated = con.execute(
                "SELECT count(*) FROM kb_relations WHERE status='VIOLATED'").fetchone()[0]
            unwatched = con.execute(
                "SELECT count(*) FROM kb_relations WHERE status IN "
                "('unchecked','unknown-relation')").fetchone()[0]
            total = con.execute("SELECT count(*) FROM kb_relations").fetchone()[0]
        except sqlite3.Error:
            violated = unwatched = total = 0
        if total:
            chk("code relations hold", violated == 0,
                f"{violated} violated (see: relations)")
            if unwatched:
                checks.append(("relations are being checked", "REVIEW",
                               f"{unwatched} of {total} not verifiable -- nothing is watching them"))

        unstated = con.execute("SELECT count(*) FROM claims WHERE evidence = 'unstated'").fetchone()[0]
        chk("claims carry provenance", unstated == 0, f"{unstated} without evidence")

        suspect = con.execute("SELECT count(DISTINCT field) FROM params "
                              "WHERE label_src = 'suspect'").fetchone()[0]
        chk("param labels clean", suspect == 0, f"{suspect} fields with uncertain labels")

        # THE PARAM MAP IS AN OPTIONAL PROJECT PLUGIN. A repository that does not ship
        # gen_param_map.py has no maps to drift, so the check does not apply -- reporting it as a
        # PROBLEM made selftest exit 1 on every project but this one, which is the difference
        # between a health check and a nuisance.
        gen = Path(__file__).with_name("gen_param_map.py")
        if not gen.is_file():
            pass
        else:
            try:
                r = subprocess.run([sys.executable, str(gen), "--check"],
                                   cwd=str(Path(__file__).resolve().parents[1]),
                                   capture_output=True, text=True, timeout=120)
                chk("param maps current", r.returncode == 0, r.stdout.strip()[:80])
            except Exception as exc:
                chk("param maps current", False, str(exc)[:60])

        rows = [{"check": c, "result": v, "detail": d} for c, v, d in checks]
        print_rows(rows, args.json, brief)
        if problems:
            print(f"\n{len(problems)} problem(s): " + ", ".join(problems), file=sys.stderr)
            return 1
        return 0
    elif args.cmd == "claim":
        # `claim --status dead` is the anti-re-derivation query: what was tested and DIED. That is
        # the most reusable content in the KB and it used to be buried in prose -- a 57k-character
        # notes blob nobody reads, in which live and killed claims read identically.
        cur = con.execute(
            """
            SELECT status, evidence, entry, text, dated, killed_by
            FROM claims
            WHERE (:t = '' OR text LIKE :frag OR entry LIKE :frag)
              AND (:st = '' OR status = :st)
              AND (:ev = '' OR evidence = :ev)
              -- string compare is correct for ISO dates. A claim with NO date is EXCLUDED rather
              -- than treated as ancient: undated means unknown, not old.
              AND (:since = '' OR (dated <> '' AND dated >= :since))
            ORDER BY CASE status WHEN 'dead' THEN :deadrank
                                 WHEN 'open' THEN 1 WHEN 'live' THEN 2 ELSE 3 END,
                     CASE evidence WHEN 'measured' THEN 0 WHEN 'reported' THEN 1 ELSE 2 END,
                     entry
            LIMIT :lim
            """,
            {"t": args.term, "frag": f"%{args.term}%", "st": args.status.lower(),
             "ev": args.evidence.lower(), "since": args.since, "lim": args.limit + 1,
             "deadrank": 0 if args.dead_first else 9},
        )
        print_limited_rows(cur, args.limit, args.json, brief, query_text=args.term)
    elif args.cmd == "param":
        # THE DUMP-SIDE LOOKUP. You are holding a key and a value off a device -- "FU": 2, "P": "*"
        # -- and need the meaning. Matching the VALUE and the MEANING, not just the field id, is the
        # whole point: guessing '*' as SMART (it is RAW) cost a day of bench work on 2026-08-17.
        cur = con.execute(
            """
            SELECT target, field, value, meaning, label,
                   CASE WHEN is_default THEN 1 ELSE NULL END AS is_default
            FROM params
            WHERE (:t = '' OR target = :t)
              AND (field = :n OR value = :n OR field LIKE :frag OR meaning LIKE :frag
                   OR label LIKE :frag)
            ORDER BY CASE WHEN field = :n THEN 0 WHEN value = :n THEN 1 ELSE 2 END,
                     target, field, value
            LIMIT :lim
            """,
            {"t": args.target, "n": args.term, "frag": f"%{args.term}%", "lim": args.limit + 1},
        )
        print_limited_rows(cur, args.limit, args.json, brief, query_text=args.term)
    elif args.cmd == "constant":
        # SEARCH THE VALUE TOO, and fall back to a fragment. The old query was `name LIKE ?` with no
        # wildcards, i.e. exact-name-only: `constant ETH2S_PROTO` found nothing even though the
        # family is indexed, and there was NO way to ask the question you actually have when you are
        # staring at a device dump -- you hold a value ('*' out of ?jconf) and need the name. Guessing
        # instead cost a day of measurements here: '*' was read as SMART when ETH2S_PROTO_RAW = '*',
        # so every reading was attributed to the wrong protocol path.
        # Exact name first, then exact value, then fragment -- one query, best match on top.
        # Values are indexed AS WRITTEN in the source, quotes included ('*', not *), so the value arm
        # has to trim them; without that it matches nothing and the reverse lookup looks unsupported.
        cur = con.execute(
            """
            SELECT name, category, value, file, line,
                   CASE WHEN commented_out THEN 1 ELSE NULL END AS commented_out
            FROM constants
            WHERE name = :n OR value = :n OR trim(value, '''"') = :n OR name LIKE :frag
            ORDER BY CASE WHEN name = :n THEN 0
                          WHEN value = :n OR trim(value, '''"') = :n THEN 1 ELSE 2 END, file, line
            LIMIT :lim
            """,
            {"n": args.name, "frag": f"%{args.name}%", "lim": args.limit + 1},
        )
        print_limited_rows(cur, args.limit, args.json, brief, query_text=args.name)
    elif args.cmd == "comment":
        term = f"%{args.term}%"
        cur = con.execute(
            """
            SELECT symbol AS name, kind, file, line, comment
            FROM symbol_comments
            WHERE symbol LIKE ? OR kind LIKE ? OR comment LIKE ?
            ORDER BY file, line
            LIMIT ?
            """,
            (term, term, term, args.limit + 1),
        )
        print_limited_rows(cur, args.limit, args.json, brief, query_text=args.term)
    elif args.cmd == "arch":
        term = f"%{args.term}%"
        if con.execute("SELECT count(*) FROM architecture_comments").fetchone()[0] == 0:
            print_empty_note(
                "Architecture comments table is empty. Add KB_ARCH: or ARCH: source comments, then rebuild the index.",
                args.json,
                limit=args.limit,
                query_text=args.term,
            )
            return 0
        cur = con.execute(
            """
            SELECT title AS name, file, line, comment
            FROM architecture_comments
            WHERE title LIKE ? OR comment LIKE ?
            ORDER BY file, line
            LIMIT ?
            """,
            (term, term, args.limit + 1),
        )
        print_limited_rows(cur, args.limit, args.json, brief, query_text=args.term)
    elif args.cmd == "refs":
        cur = con.execute(
            "SELECT symbol, in_symbol, file, line, context FROM refs WHERE symbol LIKE ? "
            "ORDER BY file, line LIMIT ?",
            (args.name, args.limit + 1),
        )
        print_limited_rows(cur, args.limit, args.json, brief, query_text=args.name)
    elif args.cmd == "api":
        cur = con.execute(
            "SELECT marker, file, line, context FROM api_markers WHERE marker LIKE ? ORDER BY file, line LIMIT ?",
            (args.marker, args.limit + 1),
        )
        print_limited_rows(cur, args.limit, args.json, brief, query_text=args.marker)
    elif args.cmd == "route":
        term = f"%{args.term}%"
        cur = con.execute(
            """
            SELECT name, source, destination, protocol, file, line, notes
            FROM routes
            WHERE name LIKE ? OR source LIKE ? OR destination LIKE ? OR protocol LIKE ? OR notes LIKE ?
            ORDER BY name
            LIMIT ?
            """,
            (term, term, term, term, term, args.limit + 1),
        )
        print_limited_rows(cur, args.limit, args.json, brief, query_text=args.term)
    elif args.cmd == "concept":
        term = f"%{args.term}%"
        cur = con.execute(
            """
            SELECT concept_id AS name, kind, symbols, value
            FROM concepts
            WHERE concept_id LIKE ? OR name LIKE ? OR symbols LIKE ? OR value LIKE ?
            ORDER BY concept_id
            LIMIT ?
            """,
            (term, term, term, term, args.limit + 1),
        )
        print_limited_rows(cur, args.limit, args.json, brief, query_text=args.term)
    elif args.cmd == "override":
        term = f"%{args.term}%"
        cur = con.execute(
            """
            SELECT name, kind, branch, value
            FROM branch_overrides
            WHERE name LIKE ? OR branch LIKE ? OR kind LIKE ? OR value LIKE ?
            ORDER BY branch, name
            LIMIT ?
            """,
            (term, term, term, term, args.limit + 1),
        )
        print_limited_rows(cur, args.limit, args.json, brief, query_text=args.term)
    elif args.cmd == "annotation":
        term = f"%{args.term}%"
        cur = con.execute(
            "SELECT name, kind, value FROM annotations WHERE name LIKE ? OR value LIKE ? ORDER BY kind, name LIMIT ?",
            (term, term, args.limit + 1),
        )
        print_limited_rows(cur, args.limit, args.json, brief, query_text=args.term)
    elif args.cmd == "commits":
        term = f"%{args.term}%"
        cur = con.execute(
            """
            SELECT sha, subject, branches, annotation, status, kind
            FROM commit_links
            WHERE sha LIKE ? OR annotation LIKE ? OR subject LIKE ?
            ORDER BY annotation, sha
            LIMIT ?
            """,
            (term, term, term, args.limit + 1),
        )
        print_limited_rows(cur, args.limit, args.json, brief, query_text=args.term)
    elif args.cmd == "notes":
        cur = con.execute(
            """
            SELECT sa.symbol, sa.annotation, sa.kind, sa.status, sa.evidence, sa.link,
                   sa.defs,
                   CASE WHEN sa.link='declared' THEN '' WHEN sa.defs>3 THEN 'AMBIGUOUS: '
                        || sa.defs || ' definitions of this name' ELSE '' END AS caveat,
                   substr(json_extract(a.value,'$.notes'), 1, 400) AS notes
            FROM symbol_annotations sa
            LEFT JOIN annotations a ON a.name = sa.annotation AND a.kind = sa.kind
            WHERE (CASE WHEN ?='1' THEN sa.symbol LIKE ? ELSE sa.symbol = ? END)
            ORDER BY CASE sa.link WHEN 'declared' THEN 0 ELSE 1 END,
                     CASE sa.status WHEN 'open' THEN 0 ELSE 1 END, sa.annotation
            LIMIT ?
            """,
            # EXACT MEANS EQUALS. LIKE treats % and _ as wildcards, so "exact" still matched
            # `ma_n` against main -- exactly the generic-name noise --fuzzy was split out to avoid.
            ("1" if args.fuzzy else "0", f"%{args.symbol}%", args.symbol, args.limit + 1),
        )
        print_limited_rows(cur, args.limit, args.json, brief, query_text=args.symbol)
    elif args.cmd == "open":
        term = f"%{args.term}%"
        if args.status == "all":
            cur = con.execute(
                """
                SELECT status, evidence, name, kind, value FROM annotations
                WHERE name LIKE ? OR value LIKE ?
                ORDER BY CASE status WHEN 'open' THEN 0 WHEN 'wontfix' THEN 1
                                     WHEN 'resolved' THEN 2 ELSE 3 END, name
                LIMIT ?
                """,
                (term, term, args.limit + 1),
            )
        else:
            cur = con.execute(
                """
                SELECT status, evidence, name, kind, value FROM annotations
                WHERE status = ? AND (name LIKE ? OR value LIKE ?)
                ORDER BY name LIMIT ?
                """,
                (args.status, term, term, args.limit + 1),
            )
        print_limited_rows(cur, args.limit, args.json, brief, query_text=args.term)
    elif args.cmd == "file":
        # PATHS ARE STORED POSIX, SO ACCEPT EITHER SEPARATOR. index_code.py normalises with
        # Path.as_posix() when it stores, but a Windows user naturally types Docs\\file.h -- and a
        # backslash matched nothing, silently, looking like "that file is not indexed".
        term = f"%{native_path_term(args.term)}%"
        cur = con.execute(
            "SELECT path AS file, module, ext, size FROM files WHERE path LIKE ? ORDER BY path LIMIT ?",
            (term, args.limit + 1),
        )
        print_limited_rows(cur, args.limit, args.json, brief, query_text=args.term)
    elif args.cmd == "links":
        term = f"%{args.term}%"
        cur = con.execute(
            """
            SELECT source_name AS name, source_kind AS kind, target_type, target, status,
                   resolved_kind, resolved_name, file, line, note
            FROM kb_links
            WHERE source_name LIKE ? OR source_kind LIKE ? OR target_type LIKE ? OR target LIKE ?
               OR status LIKE ? OR resolved_kind LIKE ? OR resolved_name LIKE ? OR note LIKE ?
            ORDER BY source_kind, source_name, target_type, target
            LIMIT ?
            """,
            (term, term, term, term, term, term, term, term, args.limit + 1),
        )
        print_limited_rows(cur, args.limit, args.json, brief, query_text=args.term)
    elif args.cmd == "graph":
        # ONE PLACE BUILDS THIS. The browser draws the graph but does not know how to derive it --
        # if it did, the two would drift the moment a new edge type is added, exactly as the old
        # static-JSON browser drifted from the CLI's searches.
        like = f"%{args.term}%"
        nodes, edges = {}, []

        def note(name, kind="", status=""):
            if name and name not in nodes:
                nodes[name] = {"id": name, "kind": kind, "status": status}
            elif name and kind and not nodes[name]["kind"]:
                nodes[name].update({"kind": kind, "status": status})

        for name, kind, status in con.execute(
                "SELECT name, kind, coalesce(status,'') FROM annotations "
                "WHERE ? = '' OR name LIKE ? OR value LIKE ? ORDER BY name LIMIT ?",
                (args.term, like, like, args.limit)):
            note(name, kind, status)

        # see_also, keyed on what it RESOLVED to: an edge drawn to the spelling rather than the
        # subject puts the same entry on the canvas twice.
        for src, target, resolved, status in con.execute(
                "SELECT source_name, target, coalesce(resolved_name,''), status FROM kb_links"):
            if src not in nodes and (resolved or target) not in nodes:
                continue
            dst = resolved or target
            note(src); note(dst)
            edges.append({"from": src, "to": dst, "type": "see_also", "status": status})

        try:
            for src, relation, target, status in con.execute(
                    "SELECT source_name, relation, target, status FROM kb_relations"):
                if src not in nodes and target not in nodes:
                    continue
                note(src); note(target)
                edges.append({"from": src, "to": target, "type": relation, "status": status})
        except sqlite3.Error:
            pass

        # A shared concept_id is the strongest link there is -- the entries are ABOUT the same
        # thing -- but joining every pair would draw a clique. Carry it as a node property and let
        # the viewer cluster on it.
        for name, concept in con.execute(
                "SELECT name, json_extract(value,'$.concept_id') FROM annotations"):
            if name in nodes and concept:
                nodes[name]["concept"] = concept

        payload = {"nodes": sorted(nodes.values(), key=lambda n: n["id"]), "edges": edges}
        if args.json:
            print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))
        else:
            print(f"{len(payload['nodes'])} nodes, {len(payload['edges'])} edges")
            for e in edges[:args.limit]:
                flag = "" if e["status"] in ("ok", "") else f"  [{e['status']}]"
                print(f"  {e['from']}  --{e['type']}->  {e['to']}{flag}")
    elif args.cmd == "relations":
        term = f"%{args.term}%"
        cur = con.execute(
            """
            SELECT source_name AS name, relation, target, status, path, note
            FROM kb_relations
            WHERE source_name LIKE ? OR relation LIKE ? OR target LIKE ? OR status LIKE ?
            ORDER BY CASE status WHEN 'VIOLATED' THEN 0 WHEN 'unknown-relation' THEN 1
                                 WHEN 'unchecked' THEN 2 ELSE 3 END, source_name, target
            LIMIT ?
            """,
            (term, term, term, term, args.limit + 1),
        )
        print_limited_rows(cur, args.limit, args.json, brief, query_text=args.term)
    elif args.cmd == "broken-links":
        term = f"%{args.term}%"
        cur = con.execute(
            """
            SELECT source_name AS name, source_kind AS kind, target_type, target, status,
                   resolved_kind, resolved_name, file, line, note
            FROM kb_links
            WHERE status <> 'ok'
              AND (source_name LIKE ? OR source_kind LIKE ? OR target_type LIKE ? OR target LIKE ?
                   OR status LIKE ? OR resolved_kind LIKE ? OR resolved_name LIKE ? OR note LIKE ?)
            ORDER BY status, source_kind, source_name, target_type, target
            LIMIT ?
            """,
            (term, term, term, term, term, term, term, term, args.limit + 1),
        )
        print_limited_rows(cur, args.limit, args.json, brief, query_text=args.term)
    return 0


def run_cli() -> int:
    """main(), with a stale index reported as such instead of as a traceback.

    An index built by an older engine can be missing a table this one queries -- kb_links did not
    always exist -- and the bare sqlite3 error ("no such table: kb_links") reads like a bug in the
    tool rather than an instruction to rebuild. Same shape as the crash the indexer had on a fresh
    database: an ordinary situation reported as a failure with no next step in it. Schema changes
    make this reachable on purpose -- the index format has just gone 5 -> 6, so every index built
    before that is stale until somebody rebuilds it.
    """
    try:
        return main()
    except sqlite3.OperationalError as exc:
        message = str(exc)
        if "no such table" in message or "no such column" in message:
            print("this index is stale or was built by an older engine: %s\n"
                  "  rebuild it:  python3 .tools/index_code.py" % message, file=sys.stderr)
            return 2
        raise


if __name__ == "__main__":
    raise SystemExit(run_cli())
