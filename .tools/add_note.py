#!/usr/bin/env python3
"""Write a durable finding into the project's annotation file.

Waymark had a query half and no write half. Recording a fact meant hand-editing the annotation
JSON, which is the step most likely to be skipped when context is short and the step most likely to
be done wrong when it is not: agents reach for a `note` subcommand that has never existed, and a
mistyped path silently writes somewhere nothing reads.

    python3 .tools/add_note.py <Symbol> "<note>" [--keywords "a, b, c"] [--file <path>]

The note lands under "symbols" in the file named by kb.config.json ("annotations"), so it survives
a reindex - the generated SQLite is disposable, this file is not. Rebuild and read back with:

    python3 .tools/index_code.py
    python3 .tools/query_code_index.py notes <Symbol>

Key the note on the SYMPTOM a future reader will search for, not on the cause they do not yet know.
Where a finding depends on a call or dependency chain, write the chain out (A -> B -> C) rather
than only its conclusion: the chain is the expensive part to re-derive, and the conclusion alone
cannot be checked.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import subprocess
import time
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_EXTS = (".c", ".h", ".cpp", ".hpp", ".cc", ".hh", ".ino",
                ".py", ".js", ".html", ".htm", ".css", ".sh", ".cs")


@contextlib.contextmanager
def kb_lock(path: Path, timeout: float = 10.0):
    """Hold an exclusive claim on the annotation file across read-modify-write.

    THE RENAME MAKES ONE WRITE ATOMIC; IT DOES NOT MAKE TWO WRITES SAFE. Each run reads the whole
    document, appends to it and writes it back, so two agents recording a finding at the same moment
    both start from the same copy and the second rename silently discards the first one's note.
    Nothing is corrupted and nothing complains -- the note is simply gone, which is the failure this
    tool exists to prevent. Waymark is explicitly for multi-session, multi-agent work, so that race
    is realistic rather than theoretical.

    O_CREAT|O_EXCL is the lock: atomic on POSIX and on Windows, and needs no dependency. The holder's
    pid goes inside, so a lock left behind by a killed process can be cleared knowingly rather than
    guessed at.
    """
    lockfile = path.with_name(path.name + ".lock")
    deadline = time.time() + timeout
    while True:
        try:
            fd = os.open(str(lockfile), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            if time.time() >= deadline:
                raise SystemExit(
                    "another add_note.py is holding %s\n"
                    "  if nothing else is running, that lock is stale -- delete it" % lockfile)
            time.sleep(0.05)
    try:
        os.write(fd, str(os.getpid()).encode("ascii"))
    finally:
        os.close(fd)
    try:
        yield
    finally:
        try:
            lockfile.unlink()
        except OSError:
            pass


def annotation_path() -> Path:
    """Resolve the annotation file the same way index_code.py does - kb.config.json or the default."""
    try:
        cfg = json.loads((REPO_ROOT / "kb.config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cfg = {}
    rel = cfg.get("annotations") if isinstance(cfg, dict) else None
    return REPO_ROOT / (rel or "Docs/source_index_annotations.json")


def guess_file(symbol: str) -> str:
    """Best-effort home for the symbol, so the note sits next to the code it constrains.

    A wrong guess is cheap - the note is still found by name and keyword - so this stays quiet
    rather than failing when the repository is not a git checkout or the symbol is a concept.
    """
    leaf = symbol.rsplit("::", 1)[-1]
    try:
        proc = subprocess.run(
            ["git", "grep", "-l", "-E", r"::%s\b|\b%s\s*\(" % (leaf, leaf)],
            cwd=str(REPO_ROOT), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
    except OSError:
        return ""
    hits = [h.strip() for h in proc.stdout.decode("utf-8", "replace").splitlines() if h.strip()]
    # Source only: the annotation file itself quotes symbol names, so an unfiltered match points
    # the note at the KB rather than at the code.
    hits = [h for h in hits if h.endswith(SOURCE_EXTS)]
    return hits[0] if hits else ""


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("symbol", help="symbol, file or concept the note constrains")
    ap.add_argument("note", help="the finding, keyed on the symptom a reader will search for")
    ap.add_argument("--keywords", default="", help="comma-separated recall terms")
    ap.add_argument("--file", default="", help="source file the note belongs to")
    ap.add_argument("--author", default="", help="author tag recorded with the note")
    ap.add_argument("--replace", action="store_true",
                    help="supersede this author's existing note for the symbol instead of appending")
    ap.add_argument("--annotations", default="", help="override the annotation file")
    a = ap.parse_args()

    path = Path(a.annotations) if a.annotations else annotation_path()
    if not path.exists():
        print("no annotation file at %s - create it or set 'annotations' in kb.config.json" % path,
              file=sys.stderr)
        return 1

    # ONE WRITER AT A TIME, across read AND write -- see kb_lock().
    with kb_lock(path):
        doc = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(doc, dict):
            print("%s is not an annotation document" % path, file=sys.stderr)
            return 1
        syms = doc.setdefault("symbols", [])

        if a.replace:
            before = len(syms)
            syms[:] = [s for s in syms
                       if not (s.get("name") == a.symbol and s.get("author", "") == a.author)]
            if before != len(syms):
                print("superseded %d earlier note(s)" % (before - len(syms)))

        entry = {"name": a.symbol, "notes": a.note}
        where = a.file or guess_file(a.symbol)
        if where:
            entry["file"] = where
        if a.keywords:
            entry["keywords"] = [k.strip() for k in a.keywords.split(",") if k.strip()]
        if a.author:
            entry["author"] = a.author
        entry["ts"] = datetime.now().strftime("%d%m%y %H:%M")
        syms.append(entry)
        # BY NAME ONLY, and lean on the sort being STABLE. Sorting on ts as well reads as chronological
        # and is not: ts is DDMMYY, so "010926" sorts before "210826" while being three weeks later.
        # Stable + append order gives the real chronology within a name for free.
        syms.sort(key=lambda s: s.get("name") or "")

        # WRITE ASIDE, THEN RENAME. This file is the only irreplaceable thing in a waymark repo -- the
        # SQLite index is rebuilt from it and the backups in .tools/kb-backups are only taken on
        # REBUILD, not here. Opening the real path with "w" truncates it before a single byte is
        # written, so any failure between that and the last line of json.dump leaves the whole KB
        # destroyed with no copy newer than the last index build. os.replace is atomic on POSIX and on
        # Windows within a volume, which is where this tool came from.
        # PER PROCESS: a shared scratch name lets two concurrent runs write the same
        # file and rename each other's half-finished document into place.
        tmp = path.with_name("%s.%d.tmp" % (path.name, os.getpid()))
        try:
            with io.open(str(tmp), "w", encoding="utf-8", newline="\n") as fh:
                json.dump(doc, fh, indent=1, ensure_ascii=False)
                fh.write("\n")
            os.replace(str(tmp), str(path))
        except Exception:
            # Leave the original untouched, and do not leave a half-written file lying next to it
            # looking like a KB.
            try:
                tmp.unlink()
            except OSError:
                pass
            raise

    print("noted %s%s (%d symbol annotations)"
          % (a.symbol, " -> " + where if where else "", len(syms)))
    print("now run: python3 .tools/index_code.py && python3 .tools/query_code_index.py selftest")
    return 0


if __name__ == "__main__":
    sys.exit(main())
