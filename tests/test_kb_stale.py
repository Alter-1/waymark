#!/usr/bin/env python3
"""Tests for kb_stale - the check that a knowledge base is still TRUE OF THE CODE.

`selftest` checks a KB's INTERNAL consistency, and every one of its checks passes on a KB that is
perfectly consistent and completely out of date. These check the other direction, and every case
here is a false positive the tool produced on its first run against a real 3000-file tree - because
a checker that cries wolf is one people learn to skip, and it takes the true findings with it.

    python3 tests/test_kb_stale.py

Standard library only, no test framework, exit code 1 on failure.
"""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / ".tools"
FAILED = []


def check(name, cond, detail=""):
    print("  %s  %s%s" % ("ok  " if cond else "FAIL", name,
                          ("   -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def scratch(td, source, entries, roots=("src",), ann="Docs/kb"):
    """A minimal project: some source, a KB of markdown entries, a built index."""
    proj = Path(td) / "proj"
    (proj / "src").mkdir(parents=True)
    (proj / ann).mkdir(parents=True)
    (proj / ".tools").mkdir()
    for f in ("index_code.py", "query_code_index.py", "kb_stale.py"):
        shutil.copy(TOOLS / f, proj / ".tools" / f)
    (proj / "kb.config.json").write_text(json.dumps(
        {"project": {"name": "t", "roots": list(roots)}, "annotations": ann}), encoding="utf-8")
    for name, text in source.items():
        (proj / "src" / name).write_text(text, encoding="utf-8")
    for name, text in entries.items():
        dest = proj / ann / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
    subprocess.run([sys.executable, str(proj / ".tools" / "index_code.py")],
                   cwd=str(proj), capture_output=True, text=True)
    return proj


def stale(proj, *args):
    """Run kb_stale, and REFUSE TO RETURN AN ERROR AS THOUGH IT WERE A RESULT.

    The first version of this file returned whatever came out, and three "the tool is quiet about
    X" checks passed against the text 'no index for branch ...' - quiet, because the tool had not
    run at all. A negative assertion is only worth anything once the thing under test is known to
    have produced an answer, so a run that did not reach its report is a hard stop here.
    """
    p = subprocess.run([sys.executable, str(proj / ".tools" / "kb_stale.py"), "--no-git"]
                       + list(args), cwd=str(proj), capture_output=True, text=True)
    out = (p.stdout or "") + (p.stderr or "")
    if "check: file-exists" not in out:
        raise AssertionError("kb_stale did not produce a report (rc=%s):\n%s" % (p.returncode, out))
    return p.returncode, out


CPP = (
    "class Thing {\n"
    "public:\n"
    "    void alive();\n"
    "};\n"
    "\n"
    "void Thing::alive() {\n"
    "    int x = 1;\n"
    "}\n"
)


def entry(name, file_, body=""):
    return "---\nname: %s\nfile: %s\nts: \"010126 10:00\"\n---\n\n%s\n" % (name, file_, body)


def main():
    # ---------------------------------------------------------------- it finds the real things
    with tempfile.TemporaryDirectory() as td:
        proj = scratch(td, {"a.cpp": CPP}, {
            "gone.md": entry("alive", "src/does_not_exist.cpp"),
            "range.md": entry("alive", "src/a.cpp", "see src/a.cpp:9000 for the detail"),
            # UNDER symbols/ ON PURPOSE. Only there is the frontmatter `name:` a claim that a
            # symbol exists; a concept or feature entry names a topic and is not required to
            # resolve, so reporting one would be the tool inventing a fault.
            "symbols/absent.md": entry("NoSuchSymbolAnywhere", "src/a.cpp"),
        })
        rc, out = stale(proj)
        check("a KB entry naming a file that is gone is reported", "does_not_exist" in out, out[:400])
        check("a citation past the end of the file is reported", "9000" in out, out[:400])
        check("an entry whose symbol the index does not know is reported",
              "NoSuchSymbolAnywhere" in out, out[:400])
        check("and it exits non-zero so it can gate a build", rc != 0, "rc=%s" % rc)

    # ---------------------------------------------------------------- and stays quiet otherwise
    with tempfile.TemporaryDirectory() as td:
        proj = scratch(td, {"a.cpp": CPP}, {
            "ok.md": entry("alive", "src/a.cpp", "the definition is at src/a.cpp:6"),
        })
        rc, out = stale(proj)
        check("a correct entry produces no problems", rc == 0, out[:400])

    # ---------------------------------------------------------------- the false positives
    # A BARE NAME AGAINST A QUALIFIED SYMBOL. The index stores what the parser saw, which for C++
    # is usually Class::method; a KB entry is keyed on the bare name, because that is what a person
    # searches for. Matching the exact string reported 115 entries as "symbol missing" on the first
    # real run, and every one of them was present under its class.
    with tempfile.TemporaryDirectory() as td:
        proj = scratch(td, {"a.cpp": CPP}, {"bare.md": entry("alive", "src/a.cpp")})
        rc, out = stale(proj)
        check("a bare name resolves to its qualified symbol", "alive" not in out.split("freshness")[0],
              out[:400])

    # A LINE IN THE FRONTMATTER PATH. `file:` is sometimes written "src/a.cpp:12" - the line is
    # provenance, not part of the path - and treating the whole string as a path reported six
    # perfectly good files as missing.
    with tempfile.TemporaryDirectory() as td:
        proj = scratch(td, {"a.cpp": CPP}, {"prov.md": entry("alive", "src/a.cpp:6")})
        rc, out = stale(proj)
        check("a line number in the frontmatter path is not part of the path",
              "src/a.cpp" not in out.split("freshness")[0], out[:400])

    # A BASENAME IS NOT A FILE. Two files of the same name in one tree, and a citation valid in the
    # longer one: resolving by basename and taking the first match reported twelve false
    # out-of-range hits the moment a real project's roots were widened.
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "x").mkdir()
        proj = scratch(td, {"a.cpp": CPP}, {"dup.md": entry("alive", "src/sub/a.cpp",
                                                            "see a.cpp:40 for the detail")})
        (proj / "src" / "sub").mkdir()
        (proj / "src" / "sub" / "a.cpp").write_text(CPP + "\n" * 60, encoding="utf-8")
        subprocess.run([sys.executable, str(proj / ".tools" / "index_code.py")],
                       cwd=str(proj), capture_output=True, text=True)
        rc, out = stale(proj)
        check("a citation valid in ANOTHER copy of the same filename is not out of range",
              "citation-range" not in out.split("result: PROBLEM")[0] or "40" not in out,
              out[:500])

    print()
    if FAILED:
        print("%d FAILED: %s" % (len(FAILED), ", ".join(FAILED)))
        return 1
    # ------------------------------------------------- one KB, several unmerged branches
    # The shape this was written for: a file renamed on ONE branch, so entries naming the old path
    # look stale from the new one -- and the obvious repair breaks them where the old name is the
    # real and only name. Three answers, not two, and the third has to be said.
    with tempfile.TemporaryDirectory() as td:
        proj = scratch(td,
                       {"new_name.cpp": CPP},
                       {"e.md": entry("alive", "src/old_name.cpp",
                                      "see src/old_name.cpp:3 for the guard\n")})
        subprocess.run(["git", "init", "-q", "."], cwd=str(proj), capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=str(proj), capture_output=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=str(proj), capture_output=True)
        # a sibling branch where the OLD name is the real one
        (proj / "src" / "old_name.cpp").write_text(CPP, encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=str(proj), capture_output=True)
        subprocess.run(["git", "commit", "-qm", "old"], cwd=str(proj), capture_output=True)
        subprocess.run(["git", "branch", "legacy"], cwd=str(proj), capture_output=True)
        (proj / "src" / "old_name.cpp").unlink()
        subprocess.run(["git", "add", "-A"], cwd=str(proj), capture_output=True)
        subprocess.run(["git", "commit", "-qm", "renamed"], cwd=str(proj), capture_output=True)
        # scratch() indexed before git existed, so the index is named for the 'nogit' branch and
        # the tool would look for the current one. Rebuild now the branch has a name.
        subprocess.run([sys.executable, str(proj / ".tools" / "index_code.py")],
                       cwd=str(proj), capture_output=True, text=True)

        rc, out = stale(proj)
        head = out.split("resolves-only-elsewhere")[0]
        check("branch-blind by default: the renamed-away file IS reported",
              "old_name.cpp" in head and "PROBLEM" in head, head[:400])

        rc, out = stale(proj, "--branches", "all")
        head = out.split("resolves-only-elsewhere")[0]
        check("--branches all: it is no longer a file-exists problem",
              "PROBLEM" not in head.split("check: file-exists")[1].split("check:")[0], out[:600])
        check("--branches all: it is reported as resolving elsewhere, WITH the branch named",
              "resolves-only-elsewhere" in out and "legacy" in out, out[:600])
        check("--branches all: and that is REVIEW, not a failure, so a shared KB is not 'broken'",
              rc == 0, "rc=%s\n%s" % (rc, out[:400]))

        rc, out = stale(proj, "--branches", "all", "--strict")
        check("--strict: the same state IS a failure, for a gate that wants one self-contained branch",
              rc != 0, "rc=%s" % rc)

    # ------------------------------------------------- a KB it could not open is not a pass
    with tempfile.TemporaryDirectory() as td:
        proj = scratch(td, {"a.cpp": CPP}, {"e.md": entry("alive", "src/a.cpp")})
        (proj / "kb.config.json").write_text(json.dumps(
            {"project": {"name": "t", "roots": ["src"]}, "annotations": "Docs/nowhere"}),
            encoding="utf-8")
        p = subprocess.run([sys.executable, str(proj / ".tools" / "kb_stale.py"), "--no-git"],
                           cwd=str(proj), capture_output=True, text=True)
        out = (p.stdout or "") + (p.stderr or "")
        check("zero entries is reported as such, not as five checks passing",
              "NO ENTRIES" in out and p.returncode != 0,
              "rc=%s\n%s" % (p.returncode, out[:400]))

    print("all passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
