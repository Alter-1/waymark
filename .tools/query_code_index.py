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


def print_rows(
    rows: list[dict],
    as_json: bool,
    brief: bool,
    more_available: bool = False,
    limit: int | None = None,
    query_text: str = "",
) -> None:
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
            for key, value in row.items():
                if key in {"name", "kind", "marker", "symbol", "file", "line"} or value in {"", None, "[]"}:
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Query source index.")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite DB path")
    parser.add_argument("--json", action="store_true", help="Emit JSON")
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

    p = sub.add_parser("broken-links")
    p.add_argument("term", nargs="?", default="")
    p.add_argument("--limit", type=int, default=80)

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
            ORDER BY CASE status WHEN 'dead' THEN :deadrank
                                 WHEN 'open' THEN 1 WHEN 'live' THEN 2 ELSE 3 END,
                     CASE evidence WHEN 'measured' THEN 0 WHEN 'reported' THEN 1 ELSE 2 END,
                     entry
            LIMIT :lim
            """,
            {"t": args.term, "frag": f"%{args.term}%", "st": args.status.lower(),
             "ev": args.evidence.lower(), "lim": args.limit + 1,
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
        term = f"%{args.term}%"
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


if __name__ == "__main__":
    raise SystemExit(main())
