#!/usr/bin/env python3
"""Build a lightweight SQLite source index for this repository.

The index is intentionally simple: regex-based symbol extraction plus a small
human-curated annotation file. It is meant for navigation and architecture
lookup, not for compiler-grade parsing.
"""

from __future__ import annotations

import argparse
import atexit
import gzip
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
KB_BACKUP_DIR = REPO_ROOT / ".tools" / "kb-backups"
KB_BACKUP_KEEP = 60
BRANCH_ANNOTATION_PREFIX = "source_index_annotations"


def load_project_config() -> dict:
    """Read kb.config.json -- the ONLY place this engine is allowed to know the project.

    Everything host-specific belongs here: which directories to scan, where the KB file lives,
    which file carries the version, the command dialect the api index recognises, and any project
    plugin. The engine's own defaults are deliberately generic so the same code indexes any
    repository, and a MISSING config is an ordinary repo rather than an error.
    """
    try:
        cfg = json.loads((REPO_ROOT / "kb.config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return cfg if isinstance(cfg, dict) else {}


PROJECT = load_project_config()
DEFAULT_ANNOTATIONS = REPO_ROOT / PROJECT.get("annotations", "Docs/source_index_annotations.json")
# Generic default: scan the repository itself; SKIP_DIRS keeps that sane.
DEFAULT_ROOTS = list(PROJECT.get("roots") or ["."])

SOURCE_EXTS = {
    ".c", ".h", ".cpp", ".hpp", ".cc", ".hh", ".ino",
    ".py", ".js", ".html", ".htm", ".css", ".sh",
}
SKIP_DIRS = {
    ".git", ".tools", ".agents", ".codex", "__pycache__", ".history",
    "build", "dist", "Production", "Archive",
}
MAX_FILE_BYTES = 2_000_000
INDEX_SCHEMA_VERSION = "7"
CLS_CODE = "C"
CLS_LINE_COMMENT = "L"
CLS_BLOCK_COMMENT = "B"
CLS_STRING = "S"
CLS_CHAR = "H"
C_LIKE_EXTS = {".c", ".h", ".cpp", ".hpp", ".cc", ".hh", ".ino"}
JS_LIKE_EXTS = {".js", ".html", ".htm"}

PY_DEF_RE = re.compile(r"^\s*(?:async\s+def|def|class)\s+([A-Za-z_][A-Za-z0-9_]*)\b")
C_DEFINE_RE = re.compile(r"^\s*#\s*define\s+([A-Za-z_][A-Za-z0-9_]*)\b(?:\s+(.*))?$")
C_TYPE_RE = re.compile(r"^\s*(?:typedef\s+)?(?:struct|enum)\s+([A-Za-z_][A-Za-z0-9_]*)\b")
# THE NAME MAY BE QUALIFIED. The prefix alternation has to end in whitespace, so it can never
# consume "BaseSerial::" -- which meant EVERY C++ member definition was missed and only free
# functions were indexed. uart_base.cpp contributed two symbols out of dozens, and the hot path
# (BaseSerial::write_sync, SerialWrapper2::write, IPWrapper::sendPacket, SingleWireSerial::available)
# was absent from the index entirely. Capturing Class::method keeps the qualified name, and a LIKE
# search for the bare method still finds it.
C_FUNC_RE = re.compile(
    r"^\s*(?:static\s+|inline\s+|extern\s+|IRAM_ATTR\s+|void\s+|int\s+|bool\s+|char\s+|"
    r"uint\d+_t\s+|int\d+_t\s+|size_t\s+|esp_err_t\s+|String\s+|auto\s+|[A-Za-z_][\w:<>\*\s]+?\s+)"
    r"((?:[A-Za-z_][A-Za-z0-9_]*\s*::\s*)*[A-Za-z_][A-Za-z0-9_]*)\s*\([^;]*\)\s*(?:\{|$)"
)
JS_FUNC_RE = re.compile(r"^\s*(?:function\s+([A-Za-z_][A-Za-z0-9_]*)|(?:const|let|var)\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:function|\([^)]*\)\s*=>))")
ASSIGN_CONST_RE = re.compile(r"^\s*([A-Z][A-Z0-9_]{2,})\s*=\s*(.+)")
# The api index recognises a project's COMMAND DIALECT (here AT+xxx and ?xxx), which is by
# definition project-specific. With no api_regex configured the engine indexes no api markers
# rather than inventing a dialect the host project does not have.
API_RE = re.compile(PROJECT["api_regex"]) if PROJECT.get("api_regex") else None
TOKEN_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b")
ARCH_COMMENT_RE = re.compile(r"\b(?:KB_ARCH|ARCH)\s*:\s*(.*)", re.IGNORECASE)


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


def default_index_path(ext: str) -> Path:
    return REPO_ROOT / ".tools" / f"code_index.{current_branch_slug()}.{ext}"


DEFAULT_DB = default_index_path("sqlite")
DEFAULT_JSON = default_index_path("json")


def rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def rel_or_text(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA journal_mode=WAL")
    return con


def file_state(paths: list[Path]) -> list[dict]:
    state = []
    for path in sorted(paths):
        try:
            st = path.stat()
        except OSError:
            state.append({"path": rel_or_text(path), "missing": True})
            continue
        state.append({
            "path": rel_or_text(path),
            "size": st.st_size,
            "mtime_ns": st.st_mtime_ns,
        })
    return state


def digest_state(state: object) -> str:
    raw = json.dumps(state, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def table_count(con: sqlite3.Connection, table: str) -> int:
    return con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def current_stats(con: sqlite3.Connection) -> dict:
    return {
        "files": table_count(con, "files"),
        "symbols": table_count(con, "symbols"),
        "symbol_comments": table_count(con, "symbol_comments"),
        "architecture_comments": table_count(con, "architecture_comments"),
        "constants": table_count(con, "constants"),
        "refs": table_count(con, "refs"),
        "api_markers": table_count(con, "api_markers"),
        "routes": table_count(con, "routes"),
        "concepts": table_count(con, "concepts"),
        "params": table_count(con, "params"),
        "claims": table_count(con, "claims"),
        "branch_overrides": table_count(con, "branch_overrides"),
        "kb_links": table_count(con, "kb_links"),
        "symbol_lifecycle": table_count(con, "symbol_lifecycle"),
        "symbol_metadata": table_count(con, "symbol_metadata"),
        "branch_symbols": table_count(con, "branch_symbols"),
    }


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def current_branch_name() -> str:
    try:
        branch = subprocess.check_output(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "--abbrev-ref", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        branch = "nogit"
    return branch or "unknown"


def meta_value(con: sqlite3.Connection, key: str) -> str | None:
    row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def index_is_fresh(con: sqlite3.Connection, source_digest: str, annotation_digest: str) -> bool:
    try:
        return (
            meta_value(con, "index_schema") == INDEX_SCHEMA_VERSION
            and meta_value(con, "source_digest") == source_digest
            and meta_value(con, "annotation_digest") == annotation_digest
        )
    except sqlite3.Error:
        return False


# WHAT AN INCREMENTAL BUILD MAY KEEP, and what it must always redo.
# SCANNED_TABLES hold one row per thing found IN A FILE, so they can be invalidated file by file --
# that is the whole saving. KB_TABLES are derived from the annotation file rather than from source
# and are rebuilt from scratch every run: measured at 0.14 s on a 678-file tree, which is far below
# the cost of reasoning about whether they are stale.
# NOT LISTED, DELIBERATELY: symbol_lifecycle, branch_symbols and symbol_metadata carry history and
# hand-written notes that no build may throw away.
SCANNED_TABLES = ("symbols", "symbol_comments", "architecture_comments",
                  "refs", "constants", "api_markers")
KB_TABLES = ("annotations", "claims", "concepts", "params", "routes", "kb_links",
             "kb_relations",
             "symbol_annotations", "commit_links", "branch_overrides")

DISPOSABLE_TABLES_DROP_SQL = """
DROP TABLE IF EXISTS files;
DROP TABLE IF EXISTS symbols;
DROP TABLE IF EXISTS symbol_comments;
DROP TABLE IF EXISTS architecture_comments;
DROP TABLE IF EXISTS refs;
DROP TABLE IF EXISTS constants;
DROP TABLE IF EXISTS api_markers;
DROP TABLE IF EXISTS routes;
DROP TABLE IF EXISTS concepts;
DROP TABLE IF EXISTS params;
DROP TABLE IF EXISTS branch_overrides;
DROP TABLE IF EXISTS kb_links;
DROP TABLE IF EXISTS kb_relations;
DROP TABLE IF EXISTS annotations;
DROP TABLE IF EXISTS claims;
DROP TABLE IF EXISTS symbol_annotations;
DROP TABLE IF EXISTS commit_links;
DROP TABLE IF EXISTS meta;
"""


def stored_file_state(con: sqlite3.Connection) -> dict:
    """path -> (size, mtime_ns), as the previous build recorded it."""
    try:
        return {r[0]: (r[1], r[2]) for r in con.execute("SELECT path, size, mtime_ns FROM files")}
    except sqlite3.Error:
        return {}


def plan_incremental(con: sqlite3.Connection, files: list) -> tuple | None:
    """(rescan, gone) for an incremental build, or None if this one has to be done in full.

    None whenever the previous index cannot be trusted to be complete: a different schema, or no
    file rows at all. Everything else is a straight size+mtime comparison per file.
    """
    # A BRAND-NEW DATABASE HAS NO meta TABLE AT ALL, and meta_value() raises rather than returning
    # None -- which crashed the FIRST build in every fresh repository. index_is_fresh() guards its
    # own call for exactly this reason; this one has to as well.
    try:
        if meta_value(con, "index_schema") != INDEX_SCHEMA_VERSION:
            return None
    except sqlite3.Error:
        return None
    stored = stored_file_state(con)
    if not stored:
        return None
    current = {}
    for path in files:
        try:
            st = path.stat()
        except OSError:
            continue
        current[rel(path)] = (st.st_size, st.st_mtime_ns)
    rescan = sorted(p for p, v in current.items() if stored.get(p) != v)
    gone = sorted(p for p in stored if p not in current)
    return rescan, gone


def forget_files(con: sqlite3.Connection, rel_paths: list) -> None:
    """Drop every scanned row belonging to these files, so they can be re-scanned from clean."""
    for start in range(0, len(rel_paths), 400):      # SQLite caps host parameters per statement
        chunk = rel_paths[start:start + 400]
        marks = ",".join("?" * len(chunk))
        con.execute(f"DELETE FROM files WHERE path IN ({marks})", chunk)
        for table in SCANNED_TABLES:
            con.execute(f"DELETE FROM {table} WHERE file IN ({marks})", chunk)


def symbol_name_digest(con: sqlite3.Connection) -> str:
    """Digest of the exact name set scan_refs matches against.

    THE ONE THING THAT MAKES INCREMENTAL REFS UNSAFE. scan_refs looks for tokens from the COMPLETE
    symbols+constants table in every file, so a symbol added in file B means references to it in an
    UNCHANGED file A were never recorded. Re-scanning only the changed files would leave those out,
    and a missing reference is indistinguishable from one that was never written. So: if this
    digest moved, refs are redone for every file; if it did not, only the changed files need it.
    """
    rows = con.execute("SELECT name FROM symbols UNION SELECT name FROM constants ORDER BY name")
    return digest_state([r[0] for r in rows])


def init_db(con: sqlite3.Connection, wipe: bool = True) -> None:
    """Create the schema. wipe=False keeps whatever rows are already there.

    An INCREMENTAL build re-scans only the files that changed, so the disposable tables must
    survive: the rows for unchanged files ARE the saving. Every CREATE here is IF NOT EXISTS for
    that reason, and the DROPs are a separate step the caller asks for. The persistent tables
    (symbol_lifecycle, symbol_metadata, branch_symbols) were already IF NOT EXISTS -- they carry
    history no build may throw away.
    """
    if wipe:
        con.executescript(DISPOSABLE_TABLES_DROP_SQL)
    con.executescript(
        """
        -- symbol_annotations is DERIVED and rebuilt every run, so it belongs with the volatile
        -- tables. Creating it without dropping it made the whole executescript fail on the SECOND
        -- build with "table already exists" -- AFTER the drops above had run, leaving an index with
        -- zero files, symbols and refs. Silent, because the traceback went to a suppressed stderr.

        CREATE TABLE IF NOT EXISTS files(
            id INTEGER PRIMARY KEY,
            path TEXT UNIQUE NOT NULL,
            ext TEXT NOT NULL,
            module TEXT NOT NULL,
            size INTEGER NOT NULL,
            -- size+mtime is what an incremental build compares to decide "unchanged". Same pair
            -- git uses in its index, and it inherits the same caveat: a checkout can restore an
            -- old mtime. --force is the answer to that, not a slower default.
            mtime_ns INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS symbols(
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            kind TEXT NOT NULL,
            file TEXT NOT NULL,
            line INTEGER NOT NULL,
            signature TEXT NOT NULL,
            commented_out INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS symbols_name_idx ON symbols(name);

        CREATE TABLE IF NOT EXISTS symbol_comments(
            symbol TEXT NOT NULL,
            kind TEXT NOT NULL,
            file TEXT NOT NULL,
            line INTEGER NOT NULL,
            comment TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS symbol_comments_symbol_idx ON symbol_comments(symbol);

        CREATE TABLE IF NOT EXISTS architecture_comments(
            title TEXT NOT NULL,
            file TEXT NOT NULL,
            line INTEGER NOT NULL,
            comment TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS architecture_comments_title_idx ON architecture_comments(title);

        CREATE TABLE IF NOT EXISTS refs(
            symbol TEXT NOT NULL,
            file TEXT NOT NULL,
            line INTEGER NOT NULL,
            in_symbol TEXT NOT NULL DEFAULT '',
            context TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS refs_symbol_idx ON refs(symbol);

        CREATE TABLE IF NOT EXISTS constants(
            name TEXT NOT NULL,
            value TEXT NOT NULL,
            file TEXT NOT NULL,
            line INTEGER NOT NULL,
            category TEXT NOT NULL,
            commented_out INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS constants_name_idx ON constants(name);

        -- THE OBSERVABLE SIDE. constants answers "what is ETH2S_PROTO_RAW"; this answers the
        -- question you actually have in front of a device: "the dump says FU=2 / P=*, what is
        -- that?". One row per selectable value, so a value can be looked up directly.
        CREATE TABLE IF NOT EXISTS claims(
            entry TEXT NOT NULL,
            kind TEXT NOT NULL,
            text TEXT NOT NULL,
            evidence TEXT NOT NULL DEFAULT 'unstated',
            status TEXT NOT NULL DEFAULT 'live',
            dated TEXT NOT NULL DEFAULT '',
            killed_by TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS claims_entry_idx ON claims(entry);
        CREATE INDEX IF NOT EXISTS claims_status_idx ON claims(status);

        CREATE TABLE IF NOT EXISTS params(
            target TEXT NOT NULL,
            field TEXT NOT NULL,
            kind TEXT NOT NULL,
            label TEXT NOT NULL DEFAULT '',
            label_src TEXT NOT NULL DEFAULT '',
            value TEXT,
            meaning TEXT,
            is_default INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS params_field_idx ON params(field);
        CREATE INDEX IF NOT EXISTS params_value_idx ON params(value);

        CREATE TABLE IF NOT EXISTS api_markers(
            marker TEXT NOT NULL,
            file TEXT NOT NULL,
            line INTEGER NOT NULL,
            context TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS api_marker_idx ON api_markers(marker);

        CREATE TABLE IF NOT EXISTS routes(
            name TEXT NOT NULL,
            source TEXT NOT NULL,
            destination TEXT NOT NULL,
            protocol TEXT NOT NULL,
            file TEXT,
            line INTEGER,
            notes TEXT
        );
        CREATE INDEX IF NOT EXISTS routes_name_idx ON routes(name);

        CREATE TABLE IF NOT EXISTS concepts(
            concept_id TEXT NOT NULL,
            name TEXT NOT NULL,
            kind TEXT NOT NULL,
            symbols TEXT NOT NULL,
            value TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS concepts_id_idx ON concepts(concept_id);
        CREATE INDEX IF NOT EXISTS concepts_name_idx ON concepts(name);

        CREATE TABLE IF NOT EXISTS branch_overrides(
            branch TEXT NOT NULL,
            name TEXT NOT NULL,
            kind TEXT NOT NULL,
            value TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS branch_overrides_name_idx ON branch_overrides(name);

        -- RELATIONS THE ENGINE CAN TEST. see_also says two entries are related; a relation asserts
        -- something about the CODE that a build can go and check. Right now that is
        -- must_not_call_from: "nothing on this path may reach this symbol". Checked against the
        -- call graph in refs, so it is instrumentation rather than documentation -- and reported
        -- exactly like a broken link, because that is what it is: an authored claim the tree no
        -- longer supports.
        CREATE TABLE IF NOT EXISTS kb_relations(
            source_name TEXT NOT NULL,
            relation TEXT NOT NULL,
            target TEXT NOT NULL,
            status TEXT NOT NULL,
            path TEXT NOT NULL DEFAULT '',
            note TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS kb_relations_status_idx ON kb_relations(status);

        CREATE TABLE IF NOT EXISTS kb_links(
            -- WHERE THE LINK WAS WRITTEN. 'see_also' is the declared field; 'inline' is a [[name]]
            -- written in the prose, which is how most of them are actually written -- 232 of them
            -- against 29 see_also entries on the tree this engine came from. They were not indexed
            -- at all, so nothing validated them and the link graph showed a KB that looked
            -- unconnected when it is densely cross-referenced.
            origin TEXT NOT NULL DEFAULT 'see_also',
            source_kind TEXT NOT NULL,
            source_name TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target TEXT NOT NULL,
            status TEXT NOT NULL,
            resolved_kind TEXT,
            resolved_name TEXT,
            file TEXT,
            line INTEGER,
            note TEXT
        );
        CREATE INDEX IF NOT EXISTS kb_links_source_idx ON kb_links(source_kind, source_name);
        CREATE INDEX IF NOT EXISTS kb_links_target_idx ON kb_links(target_type, target);
        CREATE INDEX IF NOT EXISTS kb_links_status_idx ON kb_links(status);

        CREATE TABLE IF NOT EXISTS annotations(
            name TEXT NOT NULL,
            kind TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'n/a',
            evidence TEXT NOT NULL DEFAULT 'unknown',
            value TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS annotations_status_idx ON annotations(status);

        -- WHAT CONSTRAINS THIS SYMBOL. The notes were only reachable by keyword, so editing a
        -- function cold surfaced nothing: dump-drop-policy states the rule queue_packet() must obey
        -- and was findable only by searching "dump". This is the inverse link.
        CREATE TABLE IF NOT EXISTS symbol_annotations(
            symbol TEXT NOT NULL,
            annotation TEXT NOT NULL,
            kind TEXT NOT NULL,
            status TEXT NOT NULL,
            evidence TEXT NOT NULL,
            link TEXT NOT NULL,         -- declared: listed in the entry's symbols[]
                                        -- mentioned: the name appears in its text
            defs INTEGER NOT NULL DEFAULT 0   -- definitions of this name; >1 means read with care
        );
        CREATE INDEX IF NOT EXISTS symbol_annotations_symbol_idx ON symbol_annotations(symbol);

        -- ONE FIX IS MANY COMMITS, and git cannot say which belong together. A single fix can be
        -- three commits across two branches, or eight across all of them; and from `git log` alone
        -- you cannot tell that a commit is DELIBERATELY absent from a branch rather than forgotten.
        -- The notes already group them, in prose. This makes that grouping queryable.
        -- `branches` is DERIVED at index time from git, never stored in the KB, so it cannot go
        -- stale when a fix is ported.
        CREATE TABLE IF NOT EXISTS commit_links(
            sha TEXT NOT NULL,
            annotation TEXT NOT NULL,
            kind TEXT NOT NULL,
            status TEXT NOT NULL,
            subject TEXT NOT NULL,
            branches TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS commit_links_sha_idx ON commit_links(sha);
        CREATE INDEX IF NOT EXISTS commit_links_annotation_idx ON commit_links(annotation);

        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);

        CREATE TABLE IF NOT EXISTS symbol_lifecycle(
            symbol_key TEXT PRIMARY KEY,
            token_type TEXT NOT NULL,
            name TEXT NOT NULL,
            kind TEXT NOT NULL,
            first_file TEXT,
            first_line INTEGER,
            signature TEXT,
            added_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            deleted_at TEXT,
            status TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS symbol_lifecycle_name_idx ON symbol_lifecycle(name);
        CREATE INDEX IF NOT EXISTS symbol_lifecycle_status_idx ON symbol_lifecycle(status);

        CREATE TABLE IF NOT EXISTS symbol_metadata(
            symbol_key TEXT PRIMARY KEY,
            notes TEXT NOT NULL DEFAULT '',
            keywords TEXT NOT NULL DEFAULT '[]',
            see_also TEXT NOT NULL DEFAULT '[]',
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS branch_symbols(
            branch TEXT NOT NULL,
            symbol_key TEXT NOT NULL,
            token_type TEXT NOT NULL,
            name TEXT NOT NULL,
            kind TEXT NOT NULL,
            file TEXT NOT NULL,
            line INTEGER NOT NULL,
            signature TEXT NOT NULL,
            commented_out INTEGER NOT NULL DEFAULT 0,
            added_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            deleted_at TEXT,
            status TEXT NOT NULL,
            PRIMARY KEY(branch, symbol_key)
        );
        CREATE INDEX IF NOT EXISTS branch_symbols_branch_idx ON branch_symbols(branch);
        CREATE INDEX IF NOT EXISTS branch_symbols_name_idx ON branch_symbols(name);
        CREATE INDEX IF NOT EXISTS branch_symbols_status_idx ON branch_symbols(status);
        """
    )


def iter_source_files(roots: list[str]) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        base = (REPO_ROOT / root).resolve()
        if not base.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.endswith("-backups")]
            for filename in filenames:
                path = Path(dirpath) / filename
                if path.suffix.lower() not in SOURCE_EXTS:
                    continue
                try:
                    if path.stat().st_size > MAX_FILE_BYTES:
                        continue
                except OSError:
                    continue
                files.append(path)
    return sorted(files)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


@dataclass
class LexedSource:
    lines: list[str]
    classes: list[str]
    code_lines: list[str]
    non_comment_lines: list[str]
    clean_lines: list[str]


def _append_char(
    out_lines: list[list[str]],
    class_lines: list[list[str]],
    ch: str,
    cls: str,
) -> None:
    if ch == "\n":
        out_lines.append([])
        class_lines.append([])
        return
    out_lines[-1].append(ch)
    class_lines[-1].append(cls)


def _materialize_lex(out_lines: list[list[str]], class_lines: list[list[str]]) -> LexedSource:
    lines = ["".join(line) for line in out_lines]
    classes = ["".join(line) for line in class_lines]
    code_lines: list[str] = []
    non_comment_lines: list[str] = []
    clean_lines: list[str] = []
    for line, cls_line in zip(lines, classes):
        code_lines.append("".join(ch if cls == CLS_CODE else " " for ch, cls in zip(line, cls_line)))
        non_comment_lines.append("".join(
            ch if cls not in {CLS_LINE_COMMENT, CLS_BLOCK_COMMENT} else " "
            for ch, cls in zip(line, cls_line)
        ))
        clean_lines.append("".join(
            ch if cls not in {CLS_LINE_COMMENT, CLS_BLOCK_COMMENT} else " "
            for ch, cls in zip(line, cls_line)
        ))
    return LexedSource(lines, classes, code_lines, non_comment_lines, clean_lines)


def lex_source(text: str, ext: str) -> LexedSource:
    """Classify source characters while preserving line/column positions.

    This is intentionally not a full parser. It is a small state machine that
    prevents the indexer's regexes from seeing disabled code in comments, while
    still leaving string literals available for API-marker discovery.
    """
    out_lines: list[list[str]] = [[]]
    class_lines: list[list[str]] = [[]]
    c_like = ext in {".c", ".h", ".cpp", ".hpp", ".cc", ".hh", ".ino", ".js", ".css"}
    py_like = ext in {".py", ".sh"}
    html_like = ext in {".html", ".htm"}
    i = 0
    state = CLS_CODE
    quote = ""
    triple_quote = ""
    escape = False
    n = len(text)

    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        nxt2 = text[i + 2] if i + 2 < n else ""

        if state == CLS_CODE:
            if c_like and ch == "/" and nxt == "/":
                _append_char(out_lines, class_lines, ch, CLS_LINE_COMMENT)
                _append_char(out_lines, class_lines, nxt, CLS_LINE_COMMENT)
                i += 2
                state = CLS_LINE_COMMENT
                continue
            if c_like and ch == "/" and nxt == "*":
                _append_char(out_lines, class_lines, ch, CLS_BLOCK_COMMENT)
                _append_char(out_lines, class_lines, nxt, CLS_BLOCK_COMMENT)
                i += 2
                state = CLS_BLOCK_COMMENT
                continue
            if html_like and text.startswith("<!--", i):
                for c in "<!--":
                    _append_char(out_lines, class_lines, c, CLS_BLOCK_COMMENT)
                i += 4
                state = CLS_BLOCK_COMMENT
                continue
            if py_like and ch == "#":
                _append_char(out_lines, class_lines, ch, CLS_LINE_COMMENT)
                i += 1
                state = CLS_LINE_COMMENT
                continue
            if py_like and ch in {"'", '"'} and nxt == ch and nxt2 == ch:
                triple_quote = ch * 3
                for _ in range(3):
                    _append_char(out_lines, class_lines, ch, CLS_STRING)
                i += 3
                state = CLS_STRING
                quote = ch
                escape = False
                continue
            if ch == '"':
                _append_char(out_lines, class_lines, ch, CLS_STRING)
                i += 1
                state = CLS_STRING
                quote = ch
                triple_quote = ""
                escape = False
                continue
            if ch == "'" and not html_like:
                _append_char(out_lines, class_lines, ch, CLS_CHAR if c_like else CLS_STRING)
                i += 1
                state = CLS_CHAR if c_like else CLS_STRING
                quote = ch
                triple_quote = ""
                escape = False
                continue
            _append_char(out_lines, class_lines, ch, CLS_CODE)
            i += 1
            continue

        if state == CLS_LINE_COMMENT:
            _append_char(out_lines, class_lines, ch, CLS_LINE_COMMENT)
            i += 1
            if ch == "\n":
                state = CLS_CODE
            continue

        if state == CLS_BLOCK_COMMENT:
            if c_like and ch == "*" and nxt == "/":
                _append_char(out_lines, class_lines, ch, CLS_BLOCK_COMMENT)
                _append_char(out_lines, class_lines, nxt, CLS_BLOCK_COMMENT)
                i += 2
                state = CLS_CODE
                continue
            if html_like and text.startswith("-->", i):
                for c in "-->":
                    _append_char(out_lines, class_lines, c, CLS_BLOCK_COMMENT)
                i += 3
                state = CLS_CODE
                continue
            _append_char(out_lines, class_lines, ch, CLS_BLOCK_COMMENT)
            i += 1
            continue

        if state in {CLS_STRING, CLS_CHAR}:
            cls = state
            _append_char(out_lines, class_lines, ch, cls)
            i += 1
            if triple_quote:
                if ch == quote and text.startswith(triple_quote, i - 1):
                    # The first quote was just emitted; emit the remaining two.
                    for _ in range(2):
                        if i < n:
                            _append_char(out_lines, class_lines, text[i], cls)
                            i += 1
                    state = CLS_CODE
                    quote = ""
                    triple_quote = ""
                continue
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == quote:
                state = CLS_CODE
                quote = ""
            continue

    return _materialize_lex(out_lines, class_lines)


def module_for(path: Path) -> str:
    parts = rel(path).split("/")
    if len(parts) >= 2 and parts[0] == "AV":
        return "/".join(parts[:3])
    return parts[0]


def classify_constant(name: str, value: str) -> str:
    text = f"{name} {value}".lower()
    if "hdr" in text or "eth2s" in text or "packet" in text:
        return "protocol"
    if "build" in text or "ver" in text or "rc" in text:
        return "version"
    if "baud" in text or "uart" in text:
        return "serial"
    if "aes" in text or "key" in text or "crypto" in text:
        return "crypto"
    return "constant"


def insert_file(con: sqlite3.Connection, path: Path) -> None:
    con.execute(
        "INSERT INTO files(path, ext, module, size, mtime_ns) VALUES(?,?,?,?,?)",
        (rel(path), path.suffix.lower(), module_for(path), path.stat().st_size,
         path.stat().st_mtime_ns),
    )


def clean_comment_line(line: str) -> str:
    text = line.strip()
    for prefix in ("///", "//", "/**", "/*", "#", "<!--", "*"):
        if text.startswith(prefix):
            text = text[len(prefix):].lstrip()
            break
    for suffix in ("*/", "-->"):
        if text.endswith(suffix):
            text = text[:-len(suffix)].rstrip()
    return text


def _comment_text(line: str, cls_line: str) -> str:
    return "".join(
        ch if cls in {CLS_LINE_COMMENT, CLS_BLOCK_COMMENT} else " "
        for ch, cls in zip(line, cls_line)
    )


def _has_code(line: str, cls_line: str) -> bool:
    return any(ch.strip() and cls == CLS_CODE for ch, cls in zip(line, cls_line))


def comment_ranges(lexed: LexedSource) -> list[dict]:
    ranges: list[dict] = []
    i = 0
    while i < len(lexed.lines):
        line = lexed.lines[i]
        cls_line = lexed.classes[i]
        text = _comment_text(line, cls_line)
        if not text.strip() or _has_code(line, cls_line):
            i += 1
            continue
        start = i + 1
        block = [clean_comment_line(text)]
        while i + 1 < len(lexed.lines):
            next_line = lexed.lines[i + 1]
            next_cls = lexed.classes[i + 1]
            next_text = _comment_text(next_line, next_cls)
            if not next_text.strip() or _has_code(next_line, next_cls):
                break
            i += 1
            block.append(clean_comment_line(next_text))
        ranges.append({"start": start, "end": i + 1, "text": "\n".join(block).strip()})
        i += 1
    return ranges


def commented_out_source_lines(lexed: LexedSource) -> list[tuple[int, str]]:
    lines: list[tuple[int, str]] = []
    for lineno, (line, cls_line) in enumerate(zip(lexed.lines, lexed.classes), 1):
        if _has_code(line, cls_line):
            continue
        text = clean_comment_line(_comment_text(line, cls_line))
        if text.strip():
            lines.append((lineno, text))
    return lines


def preceding_comment(ranges: list[dict], lines: list[str], line_no: int) -> str:
    for item in reversed(ranges):
        if item["end"] >= line_no:
            continue
        gap = lines[item["end"]:line_no - 1]
        if all(not line.strip() for line in gap):
            return item["text"][:2000]
        break
    return ""


def inline_comment(line: str, cls_line: str) -> str:
    try:
        idx = next(i for i, cls in enumerate(cls_line) if cls in {CLS_LINE_COMMENT, CLS_BLOCK_COMMENT})
    except StopIteration:
        return ""
    if not any(ch.strip() and cls == CLS_CODE for ch, cls in zip(line[:idx], cls_line[:idx])):
        return ""
    return clean_comment_line(line[idx:])[:1000]


def merged_comment(ranges: list[dict], lines: list[str], line_no: int, line: str, cls_line: str) -> str:
    parts = [preceding_comment(ranges, lines, line_no), inline_comment(line, cls_line)]
    return "\n".join(part for part in parts if part).strip()


def insert_symbol(
    con: sqlite3.Connection,
    name: str,
    kind: str,
    file: str,
    line: int,
    signature: str,
    comment: str,
    commented_out: int = 0,
) -> None:
    con.execute(
        "INSERT INTO symbols(name, kind, file, line, signature, commented_out) VALUES(?,?,?,?,?,?)",
        (name, kind, file, line, signature, commented_out),
    )
    if comment and not commented_out:
        con.execute(
            "INSERT INTO symbol_comments(symbol, kind, file, line, comment) VALUES(?,?,?,?,?)",
            (name, kind, file, line, comment),
        )


def scan_architecture_comments(con: sqlite3.Connection, path: Path, ranges: list[dict]) -> None:
    rpath = rel(path)
    for item in ranges:
        match = ARCH_COMMENT_RE.search(item["text"])
        if not match:
            continue
        title = match.group(1).strip() or item["text"].splitlines()[0].strip()
        title = title[:100] if title else "architecture note"
        con.execute(
            "INSERT INTO architecture_comments(title, file, line, comment) VALUES(?,?,?,?)",
            (title, rpath, item["start"], item["text"][:4000]),
        )


def c_function_name(line: str) -> str:
    m = C_FUNC_RE.match(line)
    if not m or m.group(1) in {"if", "for", "while", "switch", "return",
                             "else", "do", "sizeof", "catch", "__attribute__"}:
        return ""
    return m.group(1)


def js_function_name(line: str) -> str:
    m = JS_FUNC_RE.match(line)
    return (m.group(1) or m.group(2)) if m else ""


def indexed_symbol_name(ext: str, line: str) -> str:
    stripped = line.strip()
    if not stripped:
        return ""
    if ext == ".py":
        m = PY_DEF_RE.match(line)
        return m.group(1) if m else ""
    if ext in C_LIKE_EXTS:
        m = C_DEFINE_RE.match(line) or C_TYPE_RE.match(line)
        return m.group(1) if m else c_function_name(line)
    if ext in JS_LIKE_EXTS:
        return js_function_name(line)
    return ""


def symbol_comment_ranges_for_leading_comments(ranges: list[dict], lines: list[str], line_no: int) -> set[int]:
    matched: set[int] = set()
    for idx, item in enumerate(ranges):
        if item["end"] >= line_no:
            continue
        gap = lines[item["end"]:line_no - 1]
        if all(not line.strip() for line in gap):
            matched.add(idx)
    return matched


def enclosing_symbols_by_line(ext: str, lexed: LexedSource) -> dict[int, str]:
    enclosing: dict[int, str] = {}
    if ext in C_LIKE_EXTS | JS_LIKE_EXTS:
        stack: list[dict] = []
        pending = ""
        depth = 0
        for lineno, (line, code_line) in enumerate(zip(lexed.non_comment_lines, lexed.code_lines), 1):
            if stack:
                enclosing[lineno] = stack[-1]["name"]
            stripped = line.strip()
            name = c_function_name(line) if ext in C_LIKE_EXTS else js_function_name(line)
            opens = code_line.count("{")
            closes = code_line.count("}")
            if name and stripped and not stripped.endswith(";"):
                if opens:
                    stack.append({"name": name, "depth": depth + opens})
                else:
                    pending = name
            elif pending and opens:
                stack.append({"name": pending, "depth": depth + opens})
                pending = ""
            new_depth = max(depth + opens - closes, 0)
            while stack and new_depth < stack[-1]["depth"]:
                stack.pop()
            depth = new_depth
        return enclosing

    if ext == ".py":
        stack: list[dict] = []
        for lineno, line in enumerate(lexed.non_comment_lines, 1):
            stripped = line.strip()
            indent = len(line) - len(line.lstrip(" "))
            while stack and stripped and indent <= stack[-1]["indent"]:
                stack.pop()
            if stack:
                enclosing[lineno] = stack[-1]["name"]
            m = PY_DEF_RE.match(line)
            if m and stripped.endswith(":"):
                stack.append({"name": m.group(1), "indent": indent})
        return enclosing

    return enclosing


def insert_source_comments(
    con: sqlite3.Connection,
    rpath: str,
    ranges: list[dict],
    lines: list[str],
    attached_ranges: set[int],
    enclosing_by_line: dict[int, str],
) -> None:
    for idx, item in enumerate(ranges):
        if idx in attached_ranges or not item["text"]:
            continue
        symbol = enclosing_by_line.get(item["start"]) or f"file:{rpath}"
        kind = "in_body" if symbol in enclosing_by_line.values() else "source_comment"
        con.execute(
            "INSERT INTO symbol_comments(symbol, kind, file, line, comment) VALUES(?,?,?,?,?)",
            (symbol, kind, rpath, item["start"], item["text"][:4000]),
        )


def scan_definition_line(
    con: sqlite3.Connection,
    rpath: str,
    ext: str,
    lineno: int,
    line: str,
    comment: str,
    commented_out: int = 0,
) -> None:
    stripped = line.strip()
    if not stripped:
        return

    if ext == ".py":
        m = PY_DEF_RE.match(line)
        if m:
            kind = "class" if stripped.startswith("class ") else "function"
            insert_symbol(con, m.group(1), kind, rpath, lineno, stripped[:240], comment, commented_out)
        m = ASSIGN_CONST_RE.match(line)
        if m:
            con.execute(
                "INSERT INTO constants(name, value, file, line, category, commented_out) VALUES(?,?,?,?,?,?)",
                (
                    m.group(1), m.group(2).strip()[:240], rpath, lineno,
                    classify_constant(m.group(1), m.group(2)), commented_out,
                ),
            )

    if ext in C_LIKE_EXTS:
        m = C_DEFINE_RE.match(line)
        if m:
            value = (m.group(2) or "").strip()
            insert_symbol(con, m.group(1), "macro", rpath, lineno, stripped[:240], comment, commented_out)
            con.execute(
                "INSERT INTO constants(name, value, file, line, category, commented_out) VALUES(?,?,?,?,?,?)",
                (m.group(1), value[:240], rpath, lineno, classify_constant(m.group(1), value), commented_out),
            )
        m = C_TYPE_RE.match(line)
        if m:
            insert_symbol(con, m.group(1), "type", rpath, lineno, stripped[:240], comment, commented_out)
        name = c_function_name(line)
        if name:
            insert_symbol(con, name, "function", rpath, lineno, stripped[:240], comment, commented_out)

    if ext in JS_LIKE_EXTS:
        name = js_function_name(line)
        if name:
            insert_symbol(con, name, "js_function", rpath, lineno, stripped[:240], comment, commented_out)


def scan_definitions(con: sqlite3.Connection, path: Path, text: str,
                     lexed_lines: dict | None = None) -> None:
    """Index the definitions in one file.

    lexed_lines, when given, receives this file's stripped code lines. LEXING IS 65% OF A BUILD and
    every file used to be lexed TWICE -- once here and once in scan_refs, which cannot be fused with
    this pass because it needs the complete symbol table first. Handing the lines forward removes
    the second lex without changing what either pass computes. Optional so both functions still work
    standalone.
    """
    rpath = rel(path)
    ext = path.suffix.lower()
    lexed = lex_source(text, ext)
    if lexed_lines is not None:
        lexed_lines[rpath] = lexed.code_lines
    ranges = comment_ranges(lexed)
    attached_ranges: set[int] = set()
    scan_architecture_comments(con, path, ranges)
    for lineno, line in enumerate(lexed.non_comment_lines, 1):
        stripped = line.strip()
        if not stripped:
            continue
        comment = merged_comment(ranges, lexed.lines, lineno, lexed.lines[lineno - 1], lexed.classes[lineno - 1])
        if comment and indexed_symbol_name(ext, line):
            attached_ranges.update(symbol_comment_ranges_for_leading_comments(ranges, lexed.lines, lineno))
        scan_definition_line(con, rpath, ext, lineno, line, comment)
        for marker in (API_RE.findall(line) if API_RE else ()):
            con.execute(
                "INSERT INTO api_markers(marker, file, line, context) VALUES(?,?,?,?)",
                (marker[:120], rpath, lineno, stripped[:240]),
            )
    insert_source_comments(con, rpath, ranges, lexed.lines, attached_ranges, enclosing_symbols_by_line(ext, lexed))
    for lineno, line in commented_out_source_lines(lexed):
        scan_definition_line(con, rpath, ext, lineno, line, "", commented_out=1)


def enclosing_symbol_map(con: sqlite3.Connection, rpath: str) -> list[tuple[int, str]]:
    """[(line, name)] for this file's function-like symbols, ascending -- so a reference can be
    attributed to the function it sits IN."""
    return [
        (row[1], row[0])
        for row in con.execute(
            "SELECT name, line FROM symbols WHERE file=? AND kind IN ('function','js_function') "
            "ORDER BY line",
            (rpath,),
        )
    ]


def scan_refs(con: sqlite3.Connection, files: list[Path],
              lexed_lines: dict | None = None) -> None:
    # BOTH FORMS OF A QUALIFIED NAME. Symbols are now indexed as Class::method, but every CALL SITE
    # writes the bare method -- so matching only the qualified form finds no references to a member
    # function at all. That is a regression the qualified-name change introduced and this undoes:
    # the tail after the last "::" goes into the match set too, and the ref is recorded under the
    # token actually written in the source.
    names: set[str] = set()
    for row in con.execute("SELECT name FROM symbols UNION SELECT name FROM constants"):
        full = row[0]
        if len(full) >= 3:
            names.add(full)
        tail = full.rsplit("::", 1)[-1]
        if len(tail) >= 3:
            names.add(tail)
    if not names:
        return
    for path in files:
        rpath = rel(path)
        # Already lexed by scan_definitions in the same run -- see the note there.
        code_lines = None if lexed_lines is None else lexed_lines.get(rpath)
        if code_lines is None:
            code_lines = lex_source(read_text(path), path.suffix.lower()).code_lines
        # WHO the caller is, not just where the line is. A bare file:line answers "is it referenced";
        # the enclosing function answers "who does this". Twice in one session the enclosing function
        # WAS the finding: send_udp_dbg_log() turned out to be called from custom_log() when its
        # buffer fills, and available()'s two call sites both sit inside the else of a bHalfDuplex
        # test, which is why a single-wire port never reaches it.
        # Nearest PRECEDING definition -- approximate at a file's tail, and honest about it.
        # Headers are mostly prototypes, which sit inside no function at all -- attributing them to
        # the nearest definition above produces a confident wrong answer -- a prototype in a
        # header gets attributed to whatever happened to be defined above it. Better blank than
        # wrong.
        defs = [] if path.suffix.lower() in (".h", ".hpp") else enclosing_symbol_map(con, rpath)
        di = 0
        cur_sym = ""
        for lineno, line in enumerate(code_lines, 1):
            while di < len(defs) and defs[di][0] <= lineno:
                cur_sym = defs[di][1]
                di += 1
            found = set(TOKEN_RE.findall(line)) & names
            for name in found:
                con.execute(
                    "INSERT INTO refs(symbol, file, line, in_symbol, context) VALUES(?,?,?,?,?)",
                    (name, rpath, lineno, "" if cur_sym == name else cur_sym, line.strip()[:240]),
                )


# WHICH BRANCHES TO WALK belongs to the host project, so it comes from kb.config.json. With
# none configured the commit map is simply not built -- the feature is off, not broken.
COMMIT_BRANCHES = tuple(PROJECT.get("commit_branches") or ())
COMMIT_SHA_RE = re.compile(r"\b[0-9a-f]{8,40}\b")


def git_commit_map(branches: tuple = COMMIT_BRANCHES) -> dict:
    """{short_sha: (subject, [branches])} -- ONE `git log` per branch, not one per commit.

    Asking git branch --contains per SHA walks history every time; with a few hundred linked
    commits that is minutes. Reading each branch once and doing set lookups is seconds.
    """
    out: dict[str, list] = {}
    for branch in branches:
        try:
            raw = subprocess.check_output(
                ["git", "-C", str(REPO_ROOT), "log", "--format=%H\x1f%s", branch],
                text=True, stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.CalledProcessError):
            continue
        for line in raw.splitlines():
            full, _, subject = line.partition("\x1f")
            if len(full) < 8:
                continue
            entry = out.setdefault(full[:8], [subject, []])
            if branch not in entry[1]:
                entry[1].append(branch)
    return out


def link_commits(con: sqlite3.Connection) -> int:
    """Fill commit_links from each feature's `commits` list."""
    commits = git_commit_map()
    inserted = 0
    for name, kind, status, value in con.execute(
        "SELECT name, kind, status, value FROM annotations"
    ).fetchall():
        try:
            item = json.loads(value)
        except Exception:
            continue
        for raw in item.get("commits", []) or []:
            sha = str(raw).strip().lower()[:8]
            if not sha:
                continue
            subject, branches = commits.get(sha, ("(not in any indexed branch)", []))
            if not branches:
                annotation_status_problems.append(f"{name}: commit {sha} not found on any branch")
            con.execute(
                "INSERT INTO commit_links(sha, annotation, kind, status, subject, branches) "
                "VALUES(?,?,?,?,?,?)",
                (sha, name, kind, status, subject, ",".join(branches)),
            )
            inserted += 1
    return inserted


def link_annotations_to_symbols(con: sqlite3.Connection) -> int:
    """Fill symbol_annotations: which notes constrain which symbol.

    Three ways a note reaches a symbol, kept distinct because they are worth different amounts.
    DECLARED means the entry names the symbol deliberately: either in its symbols[] list, or by
    BEING named after it -- an entry called "wal_append" is about wal_append, and that is the most
    direct statement of intent in the file. MENTIONED means the symbol's name appears in the entry's
    own text, which is how the useful ones were actually written: dump-drop-policy names
    queue_packet() in prose and nothing pointed at it from the function.

    The name was not read here until 210826, and the omission was invisible because it only bit
    entries whose prose does NOT repeat their own name. Notes written about a function usually say
    the function's name somewhere, so they linked by MENTION and looked fine; a short note
    ("Opens the widget.") linked to nothing and `notes <Symbol>` answered "No matches" for an entry
    keyed on exactly that symbol. An empty answer reads as "nothing recorded", which is the one
    failure a knowledge base must not have.
    """
    # HOW MANY PLACES DEFINE THIS NAME, and where. A bare-name link is only trustworthy when the
    # name is distinctive: a name like `main` can have dozens of definitions in one tree AND be a
    # path component of every source file, so scanning an entry's prose for it links piles of
    # unrelated notes to it. That is not ambiguity, it is a false positive on a path fragment.
    defs: dict[str, int] = {}
    files_by_name: dict[str, set] = {}
    for name_, file_ in con.execute("SELECT name, file FROM symbols"):
        for key in {name_, name_.rsplit("::", 1)[-1]}:
            if len(key) >= 4:
                defs[key] = defs.get(key, 0) + 1
                files_by_name.setdefault(key, set()).add(file_)
    names = set(defs)
    if not names:
        return 0
    AMBIGUOUS_AT = 3     # more definitions than this and a bare mention proves nothing

    inserted = 0
    for name, kind, status, evidence, value in con.execute(
        "SELECT name, kind, status, evidence, value FROM annotations"
    ).fetchall():
        try:
            item = json.loads(value)
        except Exception:
            continue
        seen: dict[str, str] = {}
        # The entry's OWN name, when it is the name of something indexed. Intersecting with `names`
        # is what keeps this honest: a concept titled "Primary protocol channel" matches no symbol
        # and links to nothing, so only entries genuinely keyed on a symbol gain a link.
        own = str(item.get("name", "")).strip()
        if own in names:
            seen[own] = "declared"
        for declared in item.get("symbols", []) or []:
            token = str(declared).strip()
            if token:
                seen[token] = "declared"
        text = " ".join(
            str(item.get(key, "")) for key in ("notes", "brief", "meaning", "keywords")
        )
        # An entry's own files[] disambiguates: if it names the files it is about, a mentioned
        # symbol counts only when it is DEFINED in one of them. That is what turns a prose mention
        # into a real link, and it drops the path-fragment noise entirely.
        entry_files = {str(f) for f in (item.get("files") or [])}
        for token in set(TOKEN_RE.findall(text)) & names:
            # A DISTINCTIVE name is trusted on its own -- queue_packet has 3 definitions and every
            # mention of it means the function. files[] is there to RESCUE an ambiguous name, not to
            # veto a distinctive one: requiring it cost a genuine link, because an entry that
            # discusses queue_packet need not list uart_base.cpp among its files.
            if defs.get(token, 0) <= AMBIGUOUS_AT:
                pass
            elif entry_files and (files_by_name.get(token, set()) & entry_files):
                pass              # ambiguous, but this entry says which file it means
            else:
                continue          # 80 definitions and nothing to pin it down -- prove nothing
            seen.setdefault(token, "mentioned")
        for token, link in seen.items():
            con.execute(
                "INSERT INTO symbol_annotations(symbol, annotation, kind, status, evidence, link, defs) "
                "VALUES(?,?,?,?,?,?,?)",
                (token, name, kind, status, evidence, link, defs.get(token, 0)),
            )
            inserted += 1
    return inserted


def symbol_key(token_type: str, kind: str, name: str, file: str) -> str:
    return "|".join((token_type, kind, name, file))


def current_symbol_rows(con: sqlite3.Connection) -> list[dict]:
    rows: list[dict] = []
    for row in con.execute(
        """
        SELECT name, kind, file, line, signature, commented_out
        FROM symbols
        ORDER BY file, line, name
        """
    ):
        name, kind, file, line, signature, commented_out = row
        rows.append({
            "symbol_key": symbol_key("symbol", kind, name, file),
            "token_type": "symbol",
            "name": name,
            "kind": kind,
            "file": file,
            "line": line,
            "signature": signature,
            "commented_out": commented_out,
        })
    for row in con.execute(
        """
        SELECT name, category, file, line, value, commented_out
        FROM constants
        ORDER BY file, line, name
        """
    ):
        name, category, file, line, value, commented_out = row
        rows.append({
            "symbol_key": symbol_key("constant", category, name, file),
            "token_type": "constant",
            "name": name,
            "kind": category,
            "file": file,
            "line": line,
            "signature": value,
            "commented_out": commented_out,
        })
    return rows


def update_symbol_lifecycle(con: sqlite3.Connection, branch: str, timestamp: str,
                            full_scan: bool = True) -> None:
    rows = current_symbol_rows(con)
    seen = {row["symbol_key"] for row in rows}
    for row in rows:
        con.execute(
            """
            INSERT INTO symbol_lifecycle(
                symbol_key, token_type, name, kind, first_file, first_line, signature,
                added_at, last_seen_at, deleted_at, status
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(symbol_key) DO UPDATE SET
                token_type=excluded.token_type,
                name=excluded.name,
                kind=excluded.kind,
                signature=excluded.signature,
                last_seen_at=excluded.last_seen_at,
                deleted_at=NULL,
                status='active'
            """,
            (
                row["symbol_key"], row["token_type"], row["name"], row["kind"], row["file"],
                row["line"], row["signature"], timestamp, timestamp, None, "active",
            ),
        )
        con.execute(
            """
            INSERT INTO symbol_metadata(symbol_key, notes, keywords, see_also, updated_at)
            VALUES(?, '', '[]', '[]', NULL)
            ON CONFLICT(symbol_key) DO NOTHING
            """,
            (row["symbol_key"],),
        )
        con.execute(
            """
            INSERT INTO branch_symbols(
                branch, symbol_key, token_type, name, kind, file, line, signature,
                commented_out, added_at, last_seen_at, deleted_at, status
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(branch, symbol_key) DO UPDATE SET
                token_type=excluded.token_type,
                name=excluded.name,
                kind=excluded.kind,
                file=excluded.file,
                line=excluded.line,
                signature=excluded.signature,
                commented_out=excluded.commented_out,
                last_seen_at=excluded.last_seen_at,
                deleted_at=NULL,
                status='active'
            """,
            (
                branch, row["symbol_key"], row["token_type"], row["name"], row["kind"],
                row["file"], row["line"], row["signature"], row["commented_out"],
                timestamp, timestamp, None, "active",
            ),
        )

    # ONLY A FULL SCAN MAY DELETE. roots is nargs="*", so `index_code.py <subdir>` indexes ONE
    # subtree -- and marking every active symbol it did not see as deleted would wipe the history
    # of every other root, which is exactly the persistent data the lifecycle exists to keep.
    # A partial scan updates what it saw and touches nothing else.
    if not full_scan:
        return
    deleted_rows = con.execute(
        "SELECT symbol_key FROM symbol_lifecycle WHERE status='active'"
    ).fetchall()
    for (key,) in deleted_rows:
        if key in seen:
            continue
        con.execute(
            """
            UPDATE symbol_lifecycle
            SET status='deleted', deleted_at=COALESCE(deleted_at, ?), last_seen_at=last_seen_at
            WHERE symbol_key=?
            """,
            (timestamp, key),
        )
        con.execute(
            """
            UPDATE branch_symbols
            SET status='deleted', deleted_at=COALESCE(deleted_at, ?)
            WHERE branch=? AND symbol_key=? AND status='active'
            """,
            (timestamp, branch, key),
        )


def detect_fw_version() -> str | None:
    # NO version_file CONFIGURED MEANS NO VERSION, not "look at the repo root". `REPO_ROOT / ""`
    # resolves to the root DIRECTORY, and Path.exists() is true for a directory, so the unguarded
    # form sailed past the check and died in read_text() on the first foreign repo it met.
    configured = PROJECT.get("version_file")
    if not configured:
        return None
    build_id = REPO_ROOT / configured
    if not build_id.is_file():
        return None
    text = build_id.read_text(encoding="utf-8", errors="replace")
    ver = re.search(r'#\s*define\s+BUILD_VER\s+"([^"]+)"', text)
    if not ver:
        return None
    return ver.group(1)


def normalize_version(version: str) -> str:
    return version.replace(".", "_").replace("-", "_")


def snapshot_annotations(paths: list[Path]) -> None:
    """Keep a gzipped copy of every annotation file this run read.

    The KB is the one artefact here that is NOT in git: local, untracked, shared by every branch,
    and authored by hand over months. A rebuild against a missing or truncated file produces an
    EMPTY index WITHOUT erroring, which is the failure that looks most like success -- so the only
    thing between a stray edit and a year of recorded knowledge is a copy taken before the fact.

    One snapshot per distinct CONTENT, not per run. Rebuilds here are constant and casual, and a
    backup directory that grows on every invocation is one nobody reads and nobody prunes. Writing
    is best-effort: failing to back up must never be the reason an index build fails.
    """
    for path in paths:
        try:
            raw = path.read_bytes()
        except OSError:
            continue        # a version overlay that is not present is normal, not an error
        stem = path.name[: -len(".json")] if path.name.endswith(".json") else path.name
        digest = hashlib.sha256(raw).hexdigest()[:16]
        try:
            KB_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            if any(KB_BACKUP_DIR.glob(f"{stem}.*.{digest}.json.gz")):
                continue    # this exact content is already kept
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            with gzip.open(KB_BACKUP_DIR / f"{stem}.{stamp}.{digest}.json.gz", "wb") as fh:
                fh.write(raw)
            # the stamp leads the digest, so a plain name sort is chronological
            for stale in sorted(KB_BACKUP_DIR.glob(f"{stem}.*.json.gz"))[:-KB_BACKUP_KEEP]:
                stale.unlink()
        except OSError as exc:
            print(f"kb-backup: {path.name}: {exc}", file=sys.stderr)


def default_annotation_paths() -> list[Path]:
    paths = [DEFAULT_ANNOTATIONS]
    version = detect_fw_version()
    if version:
        version_path = DEFAULT_ANNOTATIONS.with_name(f"{BRANCH_ANNOTATION_PREFIX}.{normalize_version(version)}.json")
        if version_path.exists():
            paths.append(version_path)
    return paths


# WHAT IS OPEN HAS TO BE ANSWERABLE. Before this, status lived in `kind` and was spelled five ways
# (bug / open_bug / resolved_bug / resolved / open_issue) on the 43% of entries that carried a kind
# at all, so "what is still open?" could only be answered from memory -- which is how a test image's
# source got lost. `status` is now its own required field with a closed vocabulary.
ANNOTATION_STATUSES = ("open", "resolved", "wontfix", "n/a")
# HOW DO WE KNOW? Reading has been wrong four times on one bug in a single session -- available()
# blamed for a path it never reaches, an inline UDP send blamed for a web-log symptom, and two
# commits "proved" inert that a measurement later exonerated for different reasons. A claim that was
# reasoned to is not the same kind of thing as one that was measured, and the reader deserves to
# know which before acting on it.
ANNOTATION_EVIDENCE = ("measured", "inferred", "mixed", "unknown")
annotation_status_problems: list[str] = []


# Only FEATURE entries carry a problem/solution and so a status. Concepts, symbols, routes and
# branch overrides are descriptions -- warning about a missing status there is noise, and noise is
# how a real warning gets ignored.
STATUS_BEARING_KINDS = ("feature",)

# CLAIM-LEVEL PROVENANCE. An entry-level `evidence` marks the whole note, but a note is a mix: on
# 2026-08-17 one entry asserted "dd27853f is dead code here" (inferred, right), "e597b14f likewise"
# (inferred, WRONG) and "RC13 measured 49.4% BAD" (measured, right) in the same prose, in the same
# voice, and the wrong one was acted on for hours. A claim carries its own provenance, and can be
# killed without deleting it -- a dead hypothesis is the most reusable thing in the KB, because it
# is what stops the next session re-deriving it.
CLAIM_EVIDENCE = ("measured", "inferred", "reported", "unstated")
CLAIM_STATUSES = ("live", "dead", "open")


def insert_annotation(con: sqlite3.Connection, name: str, kind: str, item: dict) -> None:
    status = str(item.get("status") or "").strip().lower()
    if kind not in STATUS_BEARING_KINDS:
        # `status` is not ours outside features -- branch_overrides has used it for years to say how
        # complete an implementation is ("fuller"). Record n/a and leave the entry's own field alone.
        status = "n/a"
    elif not status:
        status = "n/a"
        annotation_status_problems.append(f"{name}: no status, assumed n/a")
    elif status not in ANNOTATION_STATUSES:
        annotation_status_problems.append(
            f"{name}: unknown status {status!r} (use one of {'/'.join(ANNOTATION_STATUSES)})")
        status = "n/a"
    evidence = str(item.get("evidence") or "").strip().lower()
    if kind not in STATUS_BEARING_KINDS:
        evidence = "unknown"
    elif not evidence:
        evidence = "unknown"
    elif evidence not in ANNOTATION_EVIDENCE:
        annotation_status_problems.append(
            f"{name}: unknown evidence {evidence!r} (use one of {'/'.join(ANNOTATION_EVIDENCE)})")
        evidence = "unknown"
    con.execute(
        "INSERT INTO annotations(name, kind, status, evidence, value) VALUES(?,?,?,?,?)",
        (name, kind, status, evidence, json.dumps(item, ensure_ascii=False, sort_keys=True)),
    )
    for claim in item.get("claims") or []:
        if isinstance(claim, str):
            claim = {"text": claim}
        text = str(claim.get("text") or "").strip()
        if not text:
            continue
        cev = str(claim.get("evidence") or "unstated").strip().lower()
        cst = str(claim.get("status") or "live").strip().lower()
        if cev not in CLAIM_EVIDENCE:
            annotation_status_problems.append(
                f"{name}: claim evidence {cev!r} (use one of {'/'.join(CLAIM_EVIDENCE)})")
            cev = "unstated"
        if cst not in CLAIM_STATUSES:
            annotation_status_problems.append(
                f"{name}: claim status {cst!r} (use one of {'/'.join(CLAIM_STATUSES)})")
            cst = "live"
        con.execute(
            "INSERT INTO claims(entry, kind, text, evidence, status, dated, killed_by)"
            " VALUES(?,?,?,?,?,?,?)",
            # ACCEPT EITHER SPELLING. The column is `dated` and the documented authoring key is
            # `date`, which is exactly the kind of near-miss that gets typed the other way --
            # and this repository's own sample KB did, losing every claim date SILENTLY. No
            # error, no warning, just a blank column nobody looks at until they want to know
            # when something was established.
            (name, kind, text, cev, cst,
             str(claim.get("date") or claim.get("dated") or ""),
             str(claim.get("killed_by") or "")),
        )


def source_name_for_item(item: dict, fallback: str = "") -> str:
    return str(item.get("name") or item.get("concept_id") or item.get("symbol") or fallback)


def parse_see_also_target(raw: object) -> dict:
    if isinstance(raw, dict):
        target = str(raw.get("target") or raw.get("name") or raw.get("value") or "").strip()
        target_type = str(raw.get("type") or raw.get("target_type") or "").strip()
        status = str(raw.get("status") or "").strip()
        note = str(raw.get("note") or raw.get("notes") or "").strip()
        replaced_by = str(raw.get("replaced_by") or "").strip()
    else:
        target = str(raw).strip()
        target_type = ""
        status = ""
        note = ""
        replaced_by = ""
    if ":" in target and not target_type:
        prefix, rest = target.split(":", 1)
        if prefix in {"symbol", "constant", "annotation", "concept", "route", "comment", "file", "api", "arch", "override"}:
            target_type = prefix
            target = rest.strip()
    return {
        "target_type": target_type or "auto",
        "target": target,
        "authored_status": status,
        "note": note,
        "replaced_by": replaced_by,
    }


def insert_link(
    con: sqlite3.Connection,
    source_kind: str,
    source_name: str,
    raw_target: object,
    origin: str = "see_also",
) -> None:
    target = parse_see_also_target(raw_target)
    if not target["target"]:
        return
    con.execute(
        """
        INSERT INTO kb_links(origin, source_kind, source_name, target_type, target, status, note)
        VALUES(?,?,?,?,?,?,?)
        """,
        (
            origin,
            source_kind,
            source_name,
            target["target_type"],
            target["target"],
            target["authored_status"] or "unresolved",
            target["note"] or (f"replaced_by:{target['replaced_by']}" if target["replaced_by"] else ""),
        ),
    )


INLINE_LINK_RE = re.compile(r"\[\[([^\]|]{2,120})\]\]")


def insert_links_for_item(con: sqlite3.Connection, source_kind: str, item: dict, fallback: str = "") -> None:
    source_name = source_name_for_item(item, fallback)
    see_also = item.get("see_also") or []
    if isinstance(see_also, (str, dict)):
        see_also = [see_also]
    for raw_target in see_also:
        insert_link(con, source_kind, source_name, raw_target)

    # [[name]] IN THE PROSE IS A LINK TOO, and it is how nearly all of them are written: 232 inline
    # references against 29 see_also entries on the tree this engine came from, 99 of them
    # resolving to a real entry. None were indexed, with two consequences -- the link graph showed
    # an unconnected KB that is in fact densely cross-referenced, and NOTHING VALIDATED THEM, so
    # renaming an entry silently broke every reference to it in someone else's notes.
    seen = set()
    for key, value in item.items():
        if key == "see_also" or not isinstance(value, str):
            continue
        for target in INLINE_LINK_RE.findall(value):
            # A REFERENCE MAY BE WRAPPED ACROSS LINES. Prose is hard-wrapped, so [[a-long-entry-
            # name]] arrives with a newline inside it and resolves against nothing -- which reads
            # as a broken link rather than as a formatting artefact.
            target = " ".join(target.split()).replace("- ", "-")
            if target and target not in seen:
                seen.add(target)
                insert_link(con, source_kind, source_name, target, origin="inline")


RELATION_KINDS = ("must_not_call_from",)


def insert_relations_for_item(con: sqlite3.Connection, item: dict, fallback: str = "") -> None:
    """Record any checkable relations an entry declares. Validation happens once, later."""
    source_name = source_name_for_item(item, fallback)
    relations = item.get("relations") or {}
    if not isinstance(relations, dict):
        return
    for relation, targets in relations.items():
        if relation not in RELATION_KINDS:
            con.execute(
                "INSERT INTO kb_relations(source_name, relation, target, status, note) "
                "VALUES(?,?,?,?,?)",
                (source_name, relation, "", "unknown-relation",
                 "this engine checks: " + ", ".join(RELATION_KINDS)))
            continue
        if isinstance(targets, str):
            targets = [targets]
        for target in targets or []:
            con.execute(
                "INSERT INTO kb_relations(source_name, relation, target, status) VALUES(?,?,?,?)",
                (source_name, relation, str(target), "unresolved"))


def call_graph(con: sqlite3.Connection) -> dict:
    """callee -> {callers}, from the refs already indexed.

    APPROXIMATE, AND THE LIMIT MATTERS. refs.in_symbol is the nearest preceding definition, and
    headers get no attribution at all -- so this graph is good enough to FIND a path that should
    not exist, and never good enough to prove one does not. Worse, a source-level graph cannot see
    calls the compiler invents: the flash-in-ISR bug this idea came from went through a GCC
    $constprop clone and had to be caught by disassembly. So a violation is a finding; silence is
    not evidence.
    """
    graph: dict = {}
    for callee, caller in con.execute("SELECT symbol, in_symbol FROM refs WHERE in_symbol != ''"):
        graph.setdefault(callee, set()).add(caller)
    return graph


def find_call_path(graph: dict, target: str, origin: str, max_depth: int = 12) -> list:
    """Shortest caller chain origin -> ... -> target, or [] if none within max_depth."""
    if target == origin:
        return [target]
    seen = {target}
    frontier = [(target, [target])]
    for _ in range(max_depth):
        nxt = []
        for node, trail in frontier:
            for caller in sorted(graph.get(node, ())):
                if caller in seen:
                    continue
                if caller == origin:
                    return list(reversed(trail + [caller]))
                seen.add(caller)
                nxt.append((caller, trail + [caller]))
        frontier = nxt
        if not frontier:
            break
    return []


def validate_kb_relations(con: sqlite3.Connection) -> None:
    """Test every declared relation against the code, and say which ones could not be tested."""
    rows = con.execute(
        "SELECT rowid, source_name, relation, target FROM kb_relations "
        "WHERE status='unresolved' ORDER BY rowid").fetchall()
    if not rows:
        return
    graph = call_graph(con)
    known = {r[0] for r in con.execute("SELECT DISTINCT name FROM symbols")}
    for rowid, source_name, relation, target in rows:
        # A RELATION NOBODY CHECKED MUST SAY SO. Silently passing an unresolvable name would be the
        # worst outcome here: the author believes an invariant is enforced and nothing is watching.
        if source_name not in known or target not in known:
            missing = source_name if source_name not in known else target
            con.execute("UPDATE kb_relations SET status=?, note=? WHERE rowid=?",
                        ("unchecked", "not a known symbol: %s" % missing, rowid))
            continue
        path = find_call_path(graph, source_name, target)
        if path:
            con.execute("UPDATE kb_relations SET status=?, path=? WHERE rowid=?",
                        ("VIOLATED", " -> ".join(path), rowid))
        else:
            con.execute("UPDATE kb_relations SET status=? WHERE rowid=?", ("ok", rowid))


def _concept_of(value: object) -> str:
    """The subject a KB row belongs to, or "" when the row does not name one."""
    try:
        payload = json.loads(value or "{}")
    except (ValueError, TypeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("concept_id") or "")


def _facet_rank(cand: dict) -> int:
    """Content first: an elaboration outranks the row that only names the subject."""
    kind = cand.get("kind") or ""
    if kind == "concept":
        return 2
    if kind == "annotation:concept":
        return 1
    return 0


def _collapse_one_subject(candidates: list[dict]) -> list[dict]:
    """Fold the facets of ONE subject into a single target.

    A concept and the annotation elaborating it share a concept_id and describe the same thing at
    two altitudes -- ten of the twelve duplicated annotation names here are exactly that pair. So
    `annotation:bench-checklist` was reported ambiguous, which asked the author to choose between
    a thing and itself: no spelling of the link could ever resolve. Collapse ONLY when every
    candidate agrees on a non-empty subject; disagreement keeps all candidates and stays
    ambiguous, which remains the rule for genuinely different targets.
    """
    if len(candidates) < 2:
        return candidates
    subjects = {cand.get("subject") or "" for cand in candidates}
    if len(subjects) != 1 or "" in subjects:
        return candidates
    return [min(candidates, key=_facet_rank)]


def _candidate(
    kind: str,
    name: str,
    file: str | None = None,
    line: int | None = None,
    subject: str = "",
) -> dict:
    return {"kind": kind, "name": name, "file": file, "line": line, "subject": subject}


def resolve_link_candidates(con: sqlite3.Connection, target_type: str, target: str) -> list[dict]:
    candidates: list[dict] = []
    if target_type in {"auto", "symbol"}:
        rows = con.execute(
            """
            SELECT name, file, line
            FROM symbols
            WHERE name=? AND commented_out=0
            ORDER BY file, line
            LIMIT 2
            """,
            (target,),
        )
        candidates.extend(_candidate("symbol", row[0], row[1], row[2]) for row in rows)
        # A METHOD IS INDEXED QUALIFIED (BaseSerial::check_when_can_send) AND AUTHORED BARE. The
        # `symbol` subcommand matches on a substring, so the documented way to verify a target
        # before linking it accepts the bare name -- and then this exact match called the link
        # missing. Fall back to the tail. 67 of 9361 names carry a `::` at all and only 14 tails
        # collide, so the honest case resolves; a colliding tail returns both rows and stays
        # ambiguous, which is the standing rule and tells the author to qualify it.
        if not candidates and "::" not in target:
            tail = target.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            rows = con.execute(
                """
                SELECT name, file, line
                FROM symbols
                WHERE name LIKE ? ESCAPE '\\' AND commented_out=0
                ORDER BY file, line
                LIMIT 2
                """,
                (f"%::{tail}",),
            )
            candidates.extend(_candidate("symbol", row[0], row[1], row[2]) for row in rows)
    if target_type in {"auto", "constant"}:
        rows = con.execute(
            """
            SELECT name, file, line
            FROM constants
            WHERE name=? AND commented_out=0
            ORDER BY file, line
            LIMIT 2
            """,
            (target,),
        )
        candidates.extend(_candidate("constant", row[0], row[1], row[2]) for row in rows)
    if target_type in {"auto", "annotation"}:
        rows = con.execute("SELECT name, kind, value FROM annotations WHERE name=? LIMIT 3", (target,))
        candidates.extend(_candidate(f"annotation:{row[1]}", row[0], subject=_concept_of(row[2])) for row in rows)
    if target_type in {"auto", "concept"}:
        rows = con.execute("SELECT concept_id, name FROM concepts WHERE concept_id=? OR name=? LIMIT 2", (target, target))
        candidates.extend(_candidate("concept", row[0] or row[1], subject=row[0] or "") for row in rows)
    if target_type in {"auto", "route"}:
        rows = con.execute("SELECT name, file, line FROM routes WHERE name=? LIMIT 2", (target,))
        candidates.extend(_candidate("route", row[0], row[1], row[2]) for row in rows)
    if target_type in {"auto", "override"}:
        rows = con.execute("SELECT name, branch FROM branch_overrides WHERE name=? LIMIT 2", (target,))
        candidates.extend(_candidate("override", f"{row[1]}:{row[0]}") for row in rows)
    if target_type in {"file"}:
        rows = con.execute("SELECT path FROM files WHERE path=? LIMIT 2", (target,))
        candidates.extend(_candidate("file", row[0], row[0]) for row in rows)
    if target_type in {"comment"}:
        rows = con.execute(
            "SELECT symbol, kind, file, line FROM symbol_comments WHERE symbol LIKE ? OR comment LIKE ? LIMIT 2",
            (f"%{target}%", f"%{target}%"),
        )
        candidates.extend(_candidate(f"comment:{row[1]}", row[0], row[2], row[3]) for row in rows)
    if target_type in {"api"}:
        rows = con.execute("SELECT marker, file, line FROM api_markers WHERE marker=? LIMIT 2", (target,))
        candidates.extend(_candidate("api", row[0], row[1], row[2]) for row in rows)
    if target_type in {"arch"}:
        rows = con.execute("SELECT title, file, line FROM architecture_comments WHERE title LIKE ? OR comment LIKE ? LIMIT 2", (f"%{target}%", f"%{target}%"))
        candidates.extend(_candidate("arch", row[0], row[1], row[2]) for row in rows)
    return _collapse_one_subject(candidates)


def validate_kb_links(con: sqlite3.Connection) -> None:
    rows = con.execute(
        "SELECT rowid, target_type, target, status, note FROM kb_links ORDER BY rowid"
    ).fetchall()
    for rowid, target_type, target, status, note in rows:
        authored = status if status != "unresolved" else ""
        if authored in {"expired", "renamed"}:
            resolved_status = authored
            candidates: list[dict] = []
        else:
            candidates = resolve_link_candidates(con, target_type, target)
            resolved_status = "missing" if not candidates else "ok"
            # AMBIGUOUS MEANS MORE THAN ONE CANDIDATE, not "candidates of different kinds". There
            # are 1055 duplicate name/kind groups in this tree, so `symbol:main` resolved silently
            # to whichever row came first. A link that cannot name one target is not resolved.
            if len(candidates) > 1:
                resolved_status = "ambiguous"
        chosen = candidates[0] if candidates else {}
        con.execute(
            """
            UPDATE kb_links
            SET status=?, resolved_kind=?, resolved_name=?, file=?, line=?
            WHERE rowid=?
            """,
            (
                resolved_status,
                chosen.get("kind"),
                chosen.get("name"),
                chosen.get("file"),
                chosen.get("line"),
                rowid,
            ),
        )


def load_params(con: sqlite3.Connection) -> None:
    """Regenerate the parameter map from the web source and index it.

    Generated on every rebuild rather than committed as data: the web page and the firmware ship
    together, so a map built from anything else is a second copy waiting to drift. See
    .tools/gen_param_map.py for why the observable side needs indexing at all.
    """
    # IN MEMORY. Indexing is a read operation and must not rewrite tracked files: running
    # `--write` here made `index_code.py` silently modify Docs/param_map_*.txt, so a rebuild --
    # something done constantly and casually -- produced working-tree changes the author never
    # asked for. --write stays an explicit maintenance command; this only READS the web source.
    # AN UNCONFIGURED PLUGIN IS NOT A PROBLEM TO REPORT. Only a project that ASKS for the param
    # map hears about it failing; everyone else got "parameter map skipped: No module named ..."
    # on every single build, which is noise pretending to be a warning.
    if "gen_param_map" not in (PROJECT.get("plugins") or []):
        return
    try:
        import gen_param_map
    except Exception as exc:                      # never let the map take the whole index down
        print(f"parameter map skipped: {exc}", file=sys.stderr)
        return
    # THE TARGET NAMES BELONG TO THE PROJECT, not to the engine. kb.config.json supplies them as
    # [[name, primary_flag], ...]; the flag is whatever the project's own plugin wants to
    # switch on. Nothing here should have to know what a given project calls its variants.
    for target, primary in [tuple(x) for x in (PROJECT.get("param_targets") or [])]:
        try:
            fields = gen_param_map.value_map(primary)
            src = str(gen_param_map.sources(primary)[0].relative_to(REPO_ROOT))
        except Exception:
            continue                              # branch without that target's web source
        for field, spec in fields.items():
            label = spec.get("label", "") or ""
            label_src = spec.get("label_src", "") or ""
            for value, meaning in (spec.get("options") or {}).items():
                con.execute(
                    "INSERT INTO params(target, field, kind, label, label_src, value, meaning,"
                    " is_default, source) VALUES(?,?,?,?,?,?,?,?,?)",
                    (target, field, "select", label, label_src, value, meaning,
                     1 if value == spec.get("default") else 0, src),
                )

    # And SAY SO if the committed reference has drifted from the code, rather than quietly fixing
    # it: a stale Docs/param_map_*.txt is exactly the kind of thing that is believed and wrong.
    try:
        chk = subprocess.run([sys.executable, str(Path(__file__).with_name("gen_param_map.py")),
                              "--check"], cwd=str(REPO_ROOT), capture_output=True, text=True,
                             timeout=120)
        if chk.returncode != 0:
            print(f"parameter map is STALE -- run .tools/gen_param_map.py --write\n"
                  f"  {chk.stdout.strip()}", file=sys.stderr)
    except Exception:
        pass


def load_annotations(con: sqlite3.Connection, path: Path) -> None:
    # Absent is not reported HERE: this function cannot tell a KB the project ASKED for from the
    # generic default in a repository that simply has no KB. The caller knows which it is and warns
    # there -- see the note at the load loop.
    if not path.exists():
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    label = data.get("branch") or data.get("version") or path.name

    for item in data.get("concepts", []):
        symbols = json.dumps(item.get("symbols", []), ensure_ascii=False)
        con.execute(
            "INSERT INTO concepts(concept_id, name, kind, symbols, value) VALUES(?,?,?,?,?)",
            (
                item["concept_id"],
                item.get("name", item["concept_id"]),
                item.get("kind", "concept"),
                symbols,
                json.dumps(item, ensure_ascii=False, sort_keys=True),
            ),
        )
        insert_annotation(con, item.get("name", item["concept_id"]), "concept", item)
        insert_links_for_item(con, "concept", item)
        insert_relations_for_item(con, item)

    for item in data.get("symbols", []):
        insert_annotation(con, item["name"], "symbol", item)
        insert_links_for_item(con, "symbol", item)
        insert_relations_for_item(con, item)
    for item in data.get("symbol_comments", []):
        symbol = item.get("symbol") or item["name"]
        comment = item.get("comment") or item.get("notes") or item.get("meaning", "")
        con.execute(
            "INSERT INTO symbol_comments(symbol, kind, file, line, comment) VALUES(?,?,?,?,?)",
            (
                symbol,
                item.get("kind", "persistent"),
                item.get("file", ""),
                int(item.get("line") or 0),
                comment,
            ),
        )
        insert_annotation(con, symbol, "symbol_comment", item)
        insert_links_for_item(con, "symbol_comment", item, symbol)
        insert_relations_for_item(con, item, symbol)
    for item in data.get("features", []):
        insert_annotation(con, item["name"], "feature", item)
        insert_links_for_item(con, "feature", item)
        insert_relations_for_item(con, item)
    for item in data.get("routes", []):
        con.execute(
            "INSERT INTO routes(name, source, destination, protocol, file, line, notes) VALUES(?,?,?,?,?,?,?)",
            (
                item["name"], item.get("source", ""), item.get("destination", ""),
                item.get("protocol", ""), item.get("file"), item.get("line"),
                item.get("notes", ""),
            ),
        )
        insert_annotation(con, item["name"], "route", item)
        insert_links_for_item(con, "route", item)
        insert_relations_for_item(con, item)
    for item in data.get("branch_overrides", []):
        con.execute(
            "INSERT INTO branch_overrides(branch, name, kind, value) VALUES(?,?,?,?)",
            (
                item.get("branch", label),
                item["name"],
                item.get("kind", "override"),
                json.dumps(item, ensure_ascii=False, sort_keys=True),
            ),
        )
        insert_annotation(con, item["name"], f"override:{item.get('branch', label)}", item)
        insert_links_for_item(con, f"override:{item.get('branch', label)}", item)
        insert_relations_for_item(con, item)


def query_all(con: sqlite3.Connection, sql: str) -> list[dict]:
    cur = con.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def export_json(con: sqlite3.Connection, path: Path, stats: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "schema": 1,
        "stats": stats,
        "meta": {row["key"]: row["value"] for row in query_all(con, "SELECT key, value FROM meta ORDER BY key")},
        "files": query_all(con, "SELECT path, ext, module, size FROM files ORDER BY path"),
        "symbols": query_all(con, "SELECT name, kind, file, line, signature, commented_out FROM symbols ORDER BY name, file, line"),
        "symbol_comments": query_all(con, "SELECT symbol, kind, file, line, comment FROM symbol_comments ORDER BY symbol, file, line"),
        "architecture_comments": query_all(con, "SELECT title, file, line, comment FROM architecture_comments ORDER BY file, line"),
        "refs": query_all(con, "SELECT symbol, file, line, context FROM refs ORDER BY symbol, file, line"),
        "constants": query_all(con, "SELECT name, category, value, file, line, commented_out FROM constants ORDER BY name, file, line"),
        "api_markers": query_all(con, "SELECT marker, file, line, context FROM api_markers ORDER BY marker, file, line"),
        "routes": query_all(con, "SELECT name, source, destination, protocol, file, line, notes FROM routes ORDER BY name"),
        "concepts": query_all(con, "SELECT concept_id, name, kind, symbols, value FROM concepts ORDER BY concept_id"),
        "branch_overrides": query_all(con, "SELECT branch, name, kind, value FROM branch_overrides ORDER BY branch, name"),
        "kb_links": query_all(con, "SELECT source_kind, source_name, target_type, target, status, resolved_kind, resolved_name, file, line, note FROM kb_links ORDER BY source_kind, source_name, target_type, target"),
        "symbol_lifecycle": query_all(con, "SELECT symbol_key, token_type, name, kind, first_file, first_line, signature, added_at, last_seen_at, deleted_at, status FROM symbol_lifecycle ORDER BY name, kind, first_file"),
        "symbol_metadata": query_all(con, "SELECT symbol_key, notes, keywords, see_also, updated_at FROM symbol_metadata ORDER BY symbol_key"),
        "branch_symbols": query_all(con, "SELECT branch, symbol_key, token_type, name, kind, file, line, signature, commented_out, added_at, last_seen_at, deleted_at, status FROM branch_symbols ORDER BY branch, name, kind, file"),
        "annotations": query_all(con, "SELECT name, kind, value FROM annotations ORDER BY kind, name"),
        # Advertised in stats since they were added; without these the export said params=174 and
        # shipped none of them, so any consumer of the JSON saw a count it could not read.
        "params": query_all(con, "SELECT target, field, kind, label, label_src, value, meaning, is_default, source FROM params ORDER BY target, field, value"),
        "claims": query_all(con, "SELECT entry, kind, text, evidence, status, dated, killed_by FROM claims ORDER BY status, entry"),
    }
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, sort_keys=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build repo source architecture index.")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Output SQLite DB path")
    parser.add_argument("--json-output", default=str(DEFAULT_JSON), help="Output browser JSON path")
    parser.add_argument("--no-json", action="store_true", help="Do not write browser JSON export")
    parser.add_argument(
        "--annotations",
        action="append",
        help="Machine-readable annotation JSON. Repeatable. Defaults to common plus detected version overlay.",
    )
    parser.add_argument("--no-auto-annotations", action="store_true", help="Do not auto-load detected version annotation overlay.")
    parser.add_argument("--force", action="store_true", help="Rebuild even when indexed files and annotations are unchanged.")
    parser.add_argument("--emit-clean", metavar="PATH", help="Print one source file with comments blanked and exit.")
    parser.add_argument("roots", nargs="*", default=DEFAULT_ROOTS, help="Repo subdirectories to scan")
    args = parser.parse_args()

    if args.emit_clean:
        path = Path(args.emit_clean)
        if not path.is_absolute():
            path = REPO_ROOT / path
        text = read_text(path)
        sys.stdout.write("\n".join(lex_source(text, path.suffix.lower()).clean_lines))
        if text.endswith("\n"):
            sys.stdout.write("\n")
        return 0

    con = connect(Path(args.db))
    files = iter_source_files(args.roots)
    if args.annotations:
        annotation_paths = [Path(p) for p in args.annotations]
    elif args.no_auto_annotations:
        annotation_paths = [DEFAULT_ANNOTATIONS]
    else:
        annotation_paths = default_annotation_paths()
    branch = current_branch_name()
    build_started_at = now_iso()
    snapshot_annotations(annotation_paths)

    source_digest = digest_state({
        "schema": INDEX_SCHEMA_VERSION,
        "roots": args.roots,
        "engine": file_state([Path(__file__)]),
        "files": file_state(files),
    })
    annotation_digest = digest_state(file_state(annotation_paths))
    # symbol_metadata is PERSISTENT and hand-edited -- the docs invite editing it -- but it is not a
    # file, so no file digest notices a change. Without this a cached run reports "fresh" and, since
    # the JSON is only exported when missing, an edit stays invisible in code_index.<branch>.json.
    try:
        meta_state = con.execute(
            "SELECT count(*), coalesce(max(updated_at),''), coalesce(sum(length(notes)),0) "
            "FROM symbol_metadata"
        ).fetchone()
    except sqlite3.Error:
        meta_state = (0, "", 0)
    annotation_digest = digest_state([annotation_digest, list(meta_state)])
    if not args.force and index_is_fresh(con, source_digest, annotation_digest):
        stats = current_stats(con)
        stats["cached"] = True
        if not args.no_json and not Path(args.json_output).exists():
            export_json(con, Path(args.json_output), stats)
        print(json.dumps(stats, indent=2, sort_keys=True))
        return 0

    # INCREMENTAL WHEN IT CAN BE, FULL WHEN IT MUST BE. Lexing and scanning is essentially all of a
    # build (measured: 19.7 s of 19.7 s on a 678-file tree, with every global pass under 0.2 s), and
    # almost every rebuild follows an edit to a handful of files. --force always does the lot.
    plan = None if args.force else plan_incremental(con, files)
    # BEFORE anything is deleted. Taken after forget_files() this digest always looks changed --
    # the changed file's own symbols are missing from it -- so every incremental build fell back to
    # a full refs pass and saved almost nothing. Measured: 9.7 s where it should have been 0.4 s.
    names_before = symbol_name_digest(con) if plan is not None else ""

    # NOBODY MAY SEE A HALF-BUILT INDEX. The rebuild used to happen in the live database: DROP
    # TABLE lands immediately (executescript commits before it runs), so for the whole build a
    # concurrent reader saw the tables EMPTY. Measured on a 678-file tree, hammering the DB with
    # read-only queries through one full rebuild: 1143 reads got an empty kb_links, 14 got
    # "no such table", 292 were correct. The crash is the 1 % that is VISIBLE -- the other 79 %
    # answered "0 links, 0 broken, 0 annotations" with no error at all, which is a confident wrong
    # answer and would let selftest report a clean pass mid-rebuild.
    # So: build into a PRIVATE file and publish it with one atomic rename. Per-process name, so two
    # builds racing each other cannot share a temp file -- each produces a complete index and the
    # last rename wins. Wasted work, never corruption, and no lock needed for that. It also makes an
    # interrupted build harmless: Ctrl-C now leaves the previous index in place instead of a
    # half-built one.
    # (The incremental path happened to be safe already -- it drops nothing, and its DML commits in
    #  one transaction -- but only by accident: one executescript added mid-build would silently
    #  reopen the hole. This makes it correct by construction instead.)
    real_db = Path(args.db)
    build_db = real_db.with_name(f"{real_db.name}.{os.getpid()}.tmp")
    for leftover in (build_db, Path(str(build_db) + "-wal"), Path(str(build_db) + "-shm")):
        if leftover.exists():
            leftover.unlink()
    if plan is not None:
        # Seed from the current index -- an incremental build's whole saving is the rows it keeps.
        # Checkpoint first: copying the main file alone would drop anything still in the -wal.
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        con.close()
        shutil.copy2(real_db, build_db)
    else:
        con.close()
    # A BUILD THAT DIES MUST NOT LEAVE ITS SCRATCH FILE BEHIND -- it is a full-sized copy of the
    # index, and a directory slowly filling with <db>.<pid>.tmp is its own kind of confusing.
    # atexit covers the ordinary ways this ends, including Ctrl-C and an unhandled exception; it is
    # unregistered once the rename has happened and the file is no longer scratch.
    def _discard_build_db():
        for leftover in (build_db, Path(str(build_db) + "-wal"), Path(str(build_db) + "-shm")):
            try:
                leftover.unlink()
            except OSError:
                pass
    atexit.register(_discard_build_db)
    con = connect(build_db)

    if plan is None:
        init_db(con)
        rescan_paths, gone = None, []
    else:
        rescan_paths, gone = plan
        init_db(con, wipe=False)
        # The KB half is rebuilt from the annotation file every run regardless -- it is cheap, and
        # deciding whether a note went stale is not.
        for table in KB_TABLES:
            con.execute(f"DELETE FROM {table}")
        forget_files(con, list(rescan_paths) + list(gone))

    todo = files if plan is None else [f for f in files if rel(f) in set(rescan_paths)]
    lexed_lines: dict = {}
    for path in todo:
        insert_file(con, path)
        scan_definitions(con, path, read_text(path), lexed_lines)

    # Refs for everything when the name set moved (or on a full build), otherwise only for the files
    # that changed -- see symbol_name_digest().
    if plan is None or symbol_name_digest(con) != names_before:
        con.execute("DELETE FROM refs")
        scan_refs(con, files, lexed_lines)
        refs_scope = "all"
    else:
        scan_refs(con, todo, lexed_lines)
        refs_scope = "changed"
    load_params(con)
    # A CONFIGURED KB THAT IS MISSING MUST BE REPORTED. Silence here was the worst failure a
    # knowledge base can have: the build finished normally, printed its usual counts, and produced
    # an index with ZERO notes -- after which every query answered "no matches", which reads as an
    # empty topic rather than a broken build. Reported from a Windows host, where a mis-resolved
    # path is easy to produce and the notes vanished on every rebuild.
    # ONLY WHEN IT WAS ASKED FOR, though. A repository with no kb.config.json and no KB file is an
    # ordinary repository, and an engine that shouts at it is an engine nobody runs twice.
    kb_was_requested = bool(PROJECT.get("annotations")) or bool(args.annotations)
    for annotation_path in annotation_paths:
        if kb_was_requested and not annotation_path.exists():
            print(f"KB annotations NOT FOUND: {annotation_path}\n"
                  f"  the index is being built WITHOUT any notes -- check `annotations` in "
                  f"kb.config.json, and that the file exists", file=sys.stderr)
        load_annotations(con, annotation_path)
    # After BOTH symbols and annotations exist: the link needs each side.
    link_annotations_to_symbols(con)
    link_commits(con)
    validate_kb_links(con)
    validate_kb_relations(con)
    # Deleting is only safe when this run covered everything the lifecycle knows about.
    full_scan = sorted(args.roots) == sorted(DEFAULT_ROOTS)
    if not full_scan:
        print(f"partial scan ({' '.join(args.roots)}): lifecycle deletions skipped", file=sys.stderr)
    update_symbol_lifecycle(con, branch, build_started_at, full_scan)
    # OR REPLACE: meta has a PRIMARY KEY and, unlike the scanned tables, it SURVIVES an
    # incremental build -- a plain INSERT would hit a UNIQUE constraint on the second run.
    con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", ("root", str(REPO_ROOT)))
    con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", ("branch", branch))
    con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", ("build_started_at", build_started_at))
    con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", ("file_count", str(len(files))))
    con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", ("fw_version", detect_fw_version() or ""))
    con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", ("annotations", ",".join(str(p) for p in annotation_paths)))
    con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", ("index_schema", INDEX_SCHEMA_VERSION))
    con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", ("source_digest", source_digest))
    con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", ("annotation_digest", annotation_digest))
    con.commit()

    stats = current_stats(con)
    stats["cached"] = False
    # SAY WHAT THIS BUILD ACTUALLY DID. "full" vs a handful of rescanned files is the difference
    # between 13 s and 2 s, and if incremental ever silently stops engaging, this is the line that
    # shows it -- a build that quietly went back to doing everything looks exactly like a slow day.
    stats["scan"] = "full" if plan is None else "incremental"
    if plan is not None:
        stats["rescanned"] = len(todo)
        stats["dropped"] = len(gone)
        stats["refs_rescan"] = refs_scope
    if not args.no_json:
        export_json(con, Path(args.json_output), stats)
    # PUBLISH. Closing checkpoints the WAL and removes the -wal/-shm sidecars, so what gets renamed
    # is one self-contained file; renaming a live WAL database without them loses committed rows.
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    con.close()
    os.replace(str(build_db), str(real_db))
    atexit.unregister(_discard_build_db)
    print(json.dumps(stats, indent=2, sort_keys=True))
    # Say it out loud, on stderr, so a missing or mistyped status cannot pass unnoticed: an entry
    # that silently defaults to n/a drops off the `open` list, which is the one place an unresolved
    # problem is supposed to stay visible.
    if annotation_status_problems:
        print(f"\nannotation status: {len(annotation_status_problems)} problem(s)", file=sys.stderr)
        for line in annotation_status_problems[:20]:
            print(f"  {line}", file=sys.stderr)
        if len(annotation_status_problems) > 20:
            print(f"  ... and {len(annotation_status_problems)-20} more", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
