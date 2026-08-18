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
        check("foreign repo: the empty KB is reported n/a, not a problem",
              re.search(r"annotations non-empty[\s\S]{0,40}n/a", out) is not None, out.strip()[-200:])

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
