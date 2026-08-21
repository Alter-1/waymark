#!/usr/bin/env python3
"""Tests for the waymark engine itself.

The engine ships a `selftest` that checks a KNOWLEDGE BASE. These check the ENGINE, against the
sample project in this repository, which exists partly to be this fixture. Every case here is a
behaviour that was once wrong: each one is a regression someone actually shipped.

    python3 tests/test_engine.py

Standard library only, no test framework, exit code 1 on failure.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / ".tools"
FAILED = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'}  {name}" + (f"   -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def run(args, cwd=ROOT):
    p = subprocess.run([sys.executable] + args, cwd=str(cwd), capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def query(*args, cwd=ROOT):
    # ALWAYS the copy inside `cwd`. query_code_index.py resolves its paths from __file__, so
    # running ROOT's copy against another directory silently queries the SAMPLE's database and
    # every assertion about the other repo is answered by the wrong index.
    return run([str(Path(cwd) / ".tools" / "query_code_index.py")] + list(args), cwd=cwd)


def resolver(cwd, db):
    """Import the engine and ask the resolver directly -- the CLI cannot show ambiguity."""
    import importlib.util
    import sqlite3
    sys.path.insert(0, str(cwd / ".tools"))
    for stale in ("index_code",):
        sys.modules.pop(stale, None)
    spec = importlib.util.spec_from_file_location("index_code", cwd / ".tools" / "index_code.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["index_code"] = mod
    spec.loader.exec_module(mod)
    return mod, sqlite3.connect(db)


def main():
    print("waymark engine tests\n")

    # ---- the sample builds, and builds the same way twice --------------------
    rc, out, _ = run([str(TOOLS / "index_code.py"), "--force"])
    check("index builds on the sample", rc == 0)
    stats = json.loads(out) if rc == 0 and out.strip().startswith("{") else {}
    check("sample has source indexed", stats.get("files", 0) >= 5, f"files={stats.get('files')}")
    check("sample has symbols", stats.get("symbols", 0) >= 20, f"symbols={stats.get('symbols')}")
    check("sample has a KB", stats.get("concepts", 0) >= 3, f"concepts={stats.get('concepts')}")
    check("sample has claims with provenance", stats.get("claims", 0) >= 4, f"claims={stats.get('claims')}")
    check("api dialect is indexed when configured", stats.get("api_markers", 0) >= 1,
          f"api_markers={stats.get('api_markers')}")

    rc2, out2, _ = run([str(TOOLS / "index_code.py"), "--force"])
    check("a rebuild is deterministic", out2 == out)

    # ---- the KB's own health ------------------------------------------------
    rc, out, _ = query("selftest")
    check("selftest passes on the sample", rc == 0, out.strip()[-200:])
    rc, out, _ = query("broken-links")
    check("no broken links in the sample", "No matches" in out, out.strip()[:120])

    # ---- claim dates: the silent-loss trap ---------------------------------
    rc, out, _ = query("claim")
    check("sample claims carry their dates", out.count("dated:") >= 4, out[:200])
    check("a claim date is not silently dropped", "2026-03-02" in out,
          "the column is `dated` and the documented key is `date`; both must be accepted")
    rc, recent, _ = query("claim", "--since", "2026-08-01")
    rc, older, _ = query("claim", "--since", "2026-01-01")
    check("--since filters by date", 0 < recent.count("status:") < older.count("status:"),
          f"since-Aug={recent.count('status:')} since-Jan={older.count('status:')}")

    # ---- resolver: the two fixes of 2026-08-18 ------------------------------
    db = TOOLS / f"code_index.{_branch()}.sqlite"
    if not db.exists():
        cands = sorted(TOOLS.glob("code_index.*.sqlite"))
        db = cands[0] if cands else db
    mod, con = resolver(ROOT, db)

    c = mod.resolve_link_candidates(con, "symbol", "compact")
    check("a BARE name resolves to the QUALIFIED symbol",
          len(c) == 1 and c[0]["name"] == "Store::compact", str([x["name"] for x in c]))

    c = mod.resolve_link_candidates(con, "symbol", "reset")
    check("an AMBIGUOUS bare name refuses to guess",
          len(c) > 1, f"got {[x['name'] for x in c]} -- must not silently pick one")

    c = mod.resolve_link_candidates(con, "annotation", "compaction")
    check("a concept and its annotation collapse to ONE target",
          len(c) == 1 and c[0]["kind"] == "annotation:feature", str([(x["kind"], x["name"]) for x in c]))

    c = mod.resolve_link_candidates(con, "symbol", "no_such_symbol_anywhere")
    check("a missing target resolves to nothing", c == [])
    con.close()

    # ---- a CONFIGURED KB that is missing must be reported, not swallowed ----
    # Reported from a Windows host: a mis-resolved path made the notes vanish and nothing said so.
    # The build finished normally, printed its usual counts, and produced an index with ZERO notes,
    # after which every query answered "no matches" -- which reads as an empty topic rather than a
    # broken build. That is the worst failure a knowledge base can have, because it looks like an
    # answer.
    with tempfile.TemporaryDirectory() as tdm:
        miss = Path(tdm)
        (miss / ".tools").mkdir()
        for f in ("index_code.py", "query_code_index.py"):
            (miss / ".tools" / f).write_bytes((TOOLS / f).read_bytes())
        (miss / "src").mkdir()
        (miss / "src" / "thing.c").write_text("int thing(void) { return 1; }\n", encoding="utf-8")
        (miss / "kb.config.json").write_text(
            json.dumps({"annotations": "Docs/does_not_exist.json", "roots": ["src"]}), encoding="utf-8")

        rc, out, err = run([str(miss / ".tools" / "index_code.py")], cwd=miss)
        check("missing KB: the build still succeeds", rc == 0, err.strip()[-200:])
        check("missing KB: it is REPORTED, not swallowed", "NOT FOUND" in err, err.strip()[-200:])
        check("missing KB: the message names the path",
              "does_not_exist.json" in err, err.strip()[-200:])
        mstats = json.loads(out) if out.strip().startswith("{") else {}
        check("missing KB: the index really is noteless (so the warning is warranted)",
              mstats.get("concepts", 0) == 0, f"concepts={mstats.get('concepts')}")

    # ---- ...but an ordinary repo with no KB at all stays QUIET ---------------
    # The counterpart to the check above, and the reason the warning lives at the call site rather
    # than inside load_annotations(): only the caller knows whether the KB was ASKED for. An engine
    # that shouts at every repository without a KB is an engine nobody runs twice.
    with tempfile.TemporaryDirectory() as tdq:
        quiet = Path(tdq)
        (quiet / ".tools").mkdir()
        for f in ("index_code.py", "query_code_index.py"):
            (quiet / ".tools" / f).write_bytes((TOOLS / f).read_bytes())
        (quiet / "src").mkdir()
        (quiet / "src" / "q.c").write_text("int q(void) { return 0; }\n", encoding="utf-8")

        rc, _, err = run([str(quiet / ".tools" / "index_code.py")], cwd=quiet)
        check("no KB configured: the engine stays quiet about it", rc == 0 and "NOT FOUND" not in err,
              err.strip()[-200:])

    # ---- a path typed with the native separator still resolves ---------------
    # Paths are STORED posix (Path.as_posix()); a caller on Windows types Docs\file.h, and before
    # native_path_term() the LIKE comparison matched nothing at all -- indistinguishable from
    # "that file is not indexed".
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location("wm_query_pathterm", str(TOOLS / "query_code_index.py"))
    _q = _ilu.module_from_spec(_spec)
    sys.modules["wm_query_pathterm"] = _q
    _spec.loader.exec_module(_q)
    check("native path separators are translated for lookup",
          _q.native_path_term(r"Docs\a\b.h") == "Docs/a/b.h", _q.native_path_term(r"Docs\a\b.h"))
    check("posix paths are left alone",
          _q.native_path_term("Docs/a/b.h") == "Docs/a/b.h", _q.native_path_term("Docs/a/b.h"))

    # ---- a foreign repo: generic defaults, no config, no KB -----------------
    with tempfile.TemporaryDirectory() as td:
        far = Path(td)
        (far / ".tools").mkdir()
        for f in ("index_code.py", "query_code_index.py"):
            (far / ".tools" / f).write_bytes((TOOLS / f).read_bytes())
        (far / "src").mkdir()
        (far / "src" / "thing.c").write_text(
            "#define THING_MAX 7\n"
            "/* Make a thing. */\n"
            "int thing_make(int n) { return n; }\n", encoding="utf-8")

        rc, out, err = run([str(far / ".tools" / "index_code.py")], cwd=far)
        check("engine runs on a foreign repo with no config", rc == 0, err.strip()[-200:])
        fstats = json.loads(out) if out.strip().startswith("{") else {}
        check("foreign repo: source is found", fstats.get("symbols", 0) >= 1, str(fstats.get("symbols")))
        check("foreign repo: NO api dialect is invented", fstats.get("api_markers", 0) == 0,
              f"api_markers={fstats.get('api_markers')}")
        check("foreign repo: an absent plugin says nothing", "parameter map" not in err, err.strip()[:120])

        rc, out, _ = query("selftest", cwd=far)
        check("foreign repo: selftest PASSES for a project with no KB yet", rc == 0, out.strip()[-200:])
        check("foreign repo: a codebase with constants reports them",
              re.search(r"constants non-empty[\s\S]{0,40}ok", out) is not None, out.strip()[-200:])

    # a codebase that legitimately defines NO constants must not be judged for it
    with tempfile.TemporaryDirectory() as td2:
        bare = Path(td2)
        (bare / ".tools").mkdir()
        for f in ("index_code.py", "query_code_index.py"):
            (bare / ".tools" / f).write_bytes((TOOLS / f).read_bytes())
        (bare / "src").mkdir()
        (bare / "src" / "plain.c").write_text("int f(int n) { return n; }\n", encoding="utf-8")
        run([str(bare / ".tools" / "index_code.py")], cwd=bare)
        rc, out, _ = query("selftest", cwd=bare)
        check("a codebase with NO constants still passes selftest", rc == 0, out.strip()[-200:])
        check("no constants is reported n/a, not a problem",
              re.search(r"constants non-empty[\s\S]{0,40}n/a", out) is not None, out.strip()[-200:])
        check("foreign repo: the empty KB is reported n/a, not a problem",
              re.search(r"annotations non-empty[\s\S]{0,40}n/a", out) is not None, out.strip()[-200:])


    # A HEADLINE THAT OUTLIVED ITS ENTRY. Two incidents on 2026-08-21 had this exact shape: the
    # one-line summary said one thing, the body under it said the opposite, and the summary is what
    # got acted on -- once at the cost of a bench device's entire configuration. The engine now says
    # so IN THE READER, because nobody runs a lint at the moment they are reading an answer.
    with tempfile.TemporaryDirectory() as td3:
        rep = Path(td3)
        (rep / ".tools").mkdir()
        for f in ("index_code.py", "query_code_index.py"):
            (rep / ".tools" / f).write_bytes((TOOLS / f).read_bytes())
        (rep / "src").mkdir()
        (rep / "src" / "a.c").write_text("int f(void) { return 1; }\n", encoding="utf-8")
        (rep / "Docs").mkdir()
        (rep / "kb.config.json").write_text(json.dumps(
            {"roots": ["src"], "annotations": "Docs/source_index_annotations.json"}), encoding="utf-8")
        (rep / "Docs" / "source_index_annotations.json").write_text(json.dumps({
            "schema": 2, "scope": "shared", "features": [
                {"name": "stale-headline", "kind": "feature", "status": "open",
                 "keywords": ["fixture"],
                 "brief": "RESOLVED in build 12 by deferring the reinit.",
                 "notes": "The fix landed, but the bench re-test has never been run."},
                {"name": "honest-headline", "kind": "feature", "status": "open",
                 "keywords": ["fixture"],
                 "brief": "OPEN: the reinit still runs on the wrong task.",
                 "notes": "Not started."},
                # The anchoring guarantee: a brief may NARRATE a history it has moved past. Matching
                # anywhere in the text flagged entries whose brief opened with the word OPEN.
                {"name": "narrated-history", "kind": "feature", "status": "open",
                 "keywords": ["fixture"],
                 "brief": "A latch that silences a port. This was believed FIXED in build 9 and is not.",
                 "notes": "Still under investigation."},
            ]}), encoding="utf-8")
        run([str(rep / ".tools" / "index_code.py")], cwd=rep)

        rc, out, _ = query("annotation", "stale-headline", cwd=rep)
        check("a headline that contradicts its own status is flagged IN THE READER",
              "read the notes, not the headline" in out, out.strip()[:200])
        check("the flag names both sides of the disagreement",
              "status=open" in out and "RESOLVED" in out, out.strip()[:200])

        rc, out, _ = query("annotation", "honest-headline", cwd=rep)
        check("an entry whose headline agrees with its status is left alone",
              "read the notes, not the headline" not in out, out.strip()[:200])

        rc, out, _ = query("annotation", "narrated-history", cwd=rep)
        check("a brief that merely NARRATES a past verdict is not flagged",
              "read the notes, not the headline" not in out, out.strip()[:200])

        rc, out, _ = query("--json", "annotation", "stale-headline", cwd=rep)
        payload = json.loads(out)
        got = payload["rows"] if isinstance(payload, dict) else payload
        check("--json carries the same warning as a field",
              any("verdict_conflict" in r for r in got), out.strip()[:200])

        rc, out, _ = query("selftest", cwd=rep)
        check("selftest REPORTS the disagreement", "headline agrees with status" in out, out.strip()[-300:])
        check("selftest does NOT fail for it -- a mixed state can be legitimate",
              rc == 0, f"rc={rc}\n" + out.strip()[-300:])
    # ---- the write half: a note reaches the file the engine actually reads ---
    # Recording a fact used to mean hand-editing the annotation JSON, and the two ways that went
    # wrong were both SILENT: a `note` subcommand that has never existed (argparse rejects it, and
    # the error reads as "the KB refused this"), and a note written to a path nothing indexes -
    # neither the write nor the reindex complains, and the fact is simply gone.
    with tempfile.TemporaryDirectory() as tdn:
        note = Path(tdn)
        (note / ".tools").mkdir()
        for f in ("index_code.py", "query_code_index.py", "add_note.py"):
            (note / ".tools" / f).write_bytes((TOOLS / f).read_bytes())
        (note / "src").mkdir()
        (note / "src" / "widget.c").write_text(
            "int widget_open(void) { return 0; }\n", encoding="utf-8")
        (note / "kb").mkdir()
        (note / "kb" / "ann.json").write_text(
            json.dumps({"schema": 2, "symbols": []}), encoding="utf-8")
        (note / "kb.config.json").write_text(
            json.dumps({"annotations": "kb/ann.json", "roots": ["src"]}), encoding="utf-8")

        add = str(note / ".tools" / "add_note.py")
        rc, out, err = run([add, "widget_open", "Opens the widget.",
                            "--keywords", "widget, open", "--author", "T"], cwd=note)
        check("add_note writes without error", rc == 0, err.strip()[-200:])

        ann = json.loads((note / "kb" / "ann.json").read_text(encoding="utf-8"))
        syms = ann.get("symbols", [])
        check("the note lands in the CONFIGURED file, not the default path", len(syms) == 1,
              f"symbols={len(syms)}")
        check("keywords are stored as a list, not a prefix in the prose",
              syms and syms[0].get("keywords") == ["widget", "open"],
              str(syms[0].get("keywords") if syms else None))

        # The annotation file quotes symbol names, so an unfiltered `git grep` points the note at
        # the KB rather than at the code that it constrains.
        check("the guessed file is source, never the annotation file",
              not (syms and (syms[0].get("file") or "").endswith(".json")),
              str(syms[0].get("file") if syms else None))

        # Appending is the default: one symbol can carry several findings from several sessions.
        run([add, "widget_open", "A second, later finding.", "--author", "T"], cwd=note)
        ann = json.loads((note / "kb" / "ann.json").read_text(encoding="utf-8"))
        check("a second note APPENDS rather than overwriting",
              len(ann.get("symbols", [])) == 2, f"symbols={len(ann.get('symbols', []))}")

        run([add, "widget_open", "The corrected finding.", "--author", "T", "--replace"], cwd=note)
        ann = json.loads((note / "kb" / "ann.json").read_text(encoding="utf-8"))
        kept = [x.get("notes") for x in ann.get("symbols", [])]
        check("--replace supersedes that author's earlier notes",
              kept == ["The corrected finding."], str(kept))

        # ...and the whole point: the note comes BACK out of a freshly built index.
        # Note that NONE of the prose above repeats the string "widget_open" - that is deliberate.
        # The linker used to reach a symbol only through symbols[] or a MENTION in the text, never
        # through the entry's own name, so an entry keyed on widget_open linked to nothing and this
        # query answered "No matches" - indistinguishable from "nothing was ever recorded".
        run([str(note / ".tools" / "index_code.py")], cwd=note)
        rc, out, _ = query("notes", "widget_open", cwd=note)
        check("the recorded note is readable again after a reindex",
              rc == 0 and "corrected finding" in out, out.strip()[-200:])
        check("being NAMED after a symbol is a declared link, not a guess",
              "declared" in out, out.strip()[-200:])

        rc, _, err = run([add, "widget_open", "x", "--annotations",
                          str(note / "kb" / "nope.json")], cwd=note)
        check("a missing annotation file is refused, not created blindly",
              rc != 0 and "nope.json" in err, err.strip()[-200:])

    # ---- `notes` must work where SQLite was built without JSON1 --------------
    # json_extract is used by exactly one query, and where the extension is absent that query died
    # with "no such function". Notes were still written and still indexed, so nothing was lost -
    # they just could never be read back, which removes the recall half of "record what you learn"
    # without removing anything visible. Measured on Python 3.7.8 with SQLite 3.31.1.
    import sqlite3 as _sqlite3
    _has_json1 = True
    try:
        _sqlite3.connect(":memory:").execute("SELECT json_extract('{\"a\":1}', '$.a')")
    except _sqlite3.OperationalError:
        _has_json1 = False
    rc, out, _ = query("notes", "wal_append")
    check("notes answers regardless of JSON1 in the host SQLite", rc == 0,
          f"json1={_has_json1}: " + out.strip()[-200:])
    check("notes returns content, not an empty result standing in for an error",
          "wal_append" in out, out.strip()[-200:])

    print()
    if FAILED:
        print(f"{len(FAILED)} FAILED: " + ", ".join(FAILED))
        return 1
    print("all passed")
    return 0


def _branch():
    p = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=str(ROOT),
                       capture_output=True, text=True)
    return p.stdout.strip().replace("/", "_") or "nogit"


if __name__ == "__main__":
    raise SystemExit(main())
