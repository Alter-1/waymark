#!/usr/bin/env python3
"""Tests for verify_port - "is this commit's substance on that branch?" answered by content.

The case that made the +OLD signal necessary is the first one here: a fix that changes only an
argument introduces no new token, so the token score says `no-tokens` and a real gap stays invisible.
The others are the ways a "the old line is still there" check would cry wolf -- code moved between
files, a reworded comment -- and the ways a check can answer for the wrong branch.

    python3 tests/test_verify_port.py

Standard library only, no test framework, exit code 1 on failure.
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".tools"))
import verify_port as v  # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    print("  %s  %s%s" % ("ok  " if cond else "FAIL", name,
                          ("   -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def git(repo, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@t")
    return subprocess.check_output(["git", "-C", str(repo)] + list(args), env=env,
                                   stderr=subprocess.STDOUT).decode().strip()


def write(repo, path, text):
    p = Path(repo) / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def commit(repo, msg):
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", msg)
    return git(repo, "rev-parse", "HEAD")


BASE_A = """#include "a.h"
static void update_link(state_t *state, uint8_t *buf)
{
    memcpy(&state->linkq, buf, sizeof(crsf_telemetry_state_t));
    state->link_ts = millis();
}
// the link statistics frame is copied field by field on the wire order
static int helper_that_will_move(int value_to_move)
{
    return value_to_move * 2 + compute_offset_of_thing(value_to_move);
}
"""


def build(td):
    repo = Path(td) / "r"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "symbolic-ref", "HEAD", "refs/heads/main")   # `init -b` needs git 2.28
    write(repo, "src/a.c", BASE_A)
    commit(repo, "base")
    for b in ("stale", "ported", "fixed"):
        git(repo, "branch", b)

    git(repo, "checkout", "-q", "fixed")
    # 1. the argument-only fix: every token already existed
    write(repo, "src/a.c", BASE_A.replace("sizeof(crsf_telemetry_state_t)", "sizeof(state->linkq)"))
    fix = commit(repo, "fix: link update overran its field")
    # 2. a move: the helper goes to b.c unchanged -- its lines still exist after the commit
    a = (Path(repo) / "src/a.c").read_text()
    body = a[a.index("static int helper_that_will_move"):]
    write(repo, "src/a.c", a.replace(body, ""))
    write(repo, "src/b.c", body)
    move = commit(repo, "refactor: move the helper")
    # 3. a comment reworded
    a = (Path(repo) / "src/a.c").read_text()
    write(repo, "src/a.c", a.replace("// the link statistics frame is copied field by field on the wire order",
                                     "// the link statistics frame is copied in wire order, packed"))
    comment = commit(repo, "comment only")
    # 4. a change UNDONE on the same line later: a line removed, then restored as it was
    a = (Path(repo) / "src/a.c").read_text()
    write(repo, "src/a.c", a.replace("    state->link_ts = millis();\n", "    state->link_ts = millis_fast_monotonic();\n"))
    undone = commit(repo, "use the fast clock")
    a = (Path(repo) / "src/a.c").read_text()
    write(repo, "src/a.c", a.replace("millis_fast_monotonic()", "millis()"))
    commit(repo, "back to millis")

    git(repo, "checkout", "-q", "ported")
    # a HAND-ADAPTED port: same fix, different surroundings -> different patch id
    write(repo, "src/a.c", BASE_A.replace("memcpy(&state->linkq, buf, sizeof(crsf_telemetry_state_t));",
                                          "/* ported */\n    memcpy(&state->linkq, buf, sizeof(state->linkq));"))
    commit(repo, "port the link fix")

    git(repo, "checkout", "-q", "-b", "merged", "fixed")
    # 5. a fix whose line differs on another branch ONLY by a trailing comment
    git(repo, "checkout", "-q", "-b", "cmt_src", "main")
    write(repo, "src/c.c", "int fn(void)\n{\n    counter_value = compute_the_thing() | 1;   // | 1: zero means none\n}\n")
    commit(repo, "c: with comment")
    git(repo, "checkout", "-q", "-b", "cmt_dst", "cmt_src")
    write(repo, "src/c.c", "int fn(void)\n{\n    counter_value = compute_the_thing() | 1;\n}\n")
    cmt = commit(repo, "c: the same line, comment dropped")
    git(repo, "checkout", "-q", "-b", "quoted", "main")
    # a branch WITHOUT the fix whose old line appears only QUOTED in an unrelated file
    write(repo, "src/a.c", BASE_A.replace("sizeof(crsf_telemetry_state_t)", "sizeof(state->linkq)"))
    write(repo, "src/notes.c", "/* was: */ const char *was = \"memcpy(&state->linkq, buf, sizeof(crsf_telemetry_state_t));\";\n")
    commit(repo, "fixed differently, old line quoted in a note")
    git(repo, "checkout", "-q", "stale")   # the working tree sits on the STALE branch throughout
    return repo, fix, move, comment, undone, cmt


def main():
    with tempfile.TemporaryDirectory() as td:
        repo, fix, move, comment, undone, cmt = build(td)
        r = str(repo)

        old, exts, by_path = v.old_lines_for(r, fix, tip="fixed")
        check("argument-only fix: the removed line is a candidate",
              old == ["memcpy(&state->linkq, buf, sizeof(crsf_telemetry_state_t));"], old)
        toks, _ = v.tokens_for(r, fix)
        check("argument-only fix: tokens see nothing (why +OLD exists)", toks == [], toks)
        check("+OLD on the branch that still has the old line",
              len(v._grep_found(r, "stale", old, exts, None)) == 1)
        check("no +OLD on a hand-adapted port",
              len(v._grep_found(r, "ported", old, exts, None)) == 0)
        check("a merged branch is in-history", v.in_history(r, fix, "merged"))
        check("a stale branch is not in-history", not v.in_history(r, fix, "stale"))

        mold, _, _ = v.old_lines_for(r, move)
        check("moved code is not 'old code' (it still exists after the commit)", mold == [], mold)
        cold, _, _ = v.old_lines_for(r, comment)
        check("a reworded comment is not 'old code'", cold == [], cold)

        uold, _, _ = v.old_lines_for(r, undone)
        check("undone later: without the tip the removed line is a candidate", uold != [], uold)
        uold, _, _ = v.old_lines_for(r, undone, tip="fixed")
        check("undone later: back at the source tip, so not 'old code'", uold == [], uold)

        check("old line QUOTED in another file of the target is not a hit (same-file search)",
              v.old_hits_in_ref(r, "quoted", by_path, exts, None) == 0)
        check("...while the stale branch's own file is", v.old_hits_in_ref(r, "stale", by_path, exts, None) == 1)

        cmold, _, _ = v.old_lines_for(r, cmt, tip="cmt_dst")
        check("a line that differs only by a trailing comment is not 'old code'", cmold == [], cmold)
        check("trailing comment stripped, URL kept", v._code_of('s = "http://x";   // c') == 's = "http://x";')

        # READ THE REF: the checkout is on `stale`; asking about `ported` must answer for ported
        check("--ref answers for the ref, not the checkout",
              len(v._grep_found(r, "ported", old, exts, None)) == 0
              and "sizeof(crsf_telemetry_state_t)" in (Path(repo) / "src/a.c").read_text())

        skip = v.re.compile(r"^src/a\.c$")
        check("a skipped path is not searched",
              len(v._grep_found(r, "stale", old, exts, skip)) == 0)

        # end to end, through the command line
        out = subprocess.run([sys.executable, str(ROOT / ".tools" / "verify_port.py"), "--repo", r,
                              "--rev", "fixed", "--since", "2000-01-01",
                              "--ref", "stale", "--ref", "ported", "--ref", "merged", "--only-old"],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT).stdout.decode()
        line = [l for l in out.splitlines() if fix[:8] in l]
        check("CLI: the fix row is printed with --only-old", len(line) == 1, out)
        if line:
            cells = line[0].split()[-3:]
            check("CLI: stale +OLD, ported clean, merged in-history",
                  cells == ["no-tokens+OLD", "no-tokens", "in-history"], cells)
        check("CLI: --only-old hides the move and the comment",
              move[:8] not in out and comment[:8] not in out, out)

    print("")
    if FAILED:
        print("FAILED: %d" % len(FAILED))
        return 1
    print("all passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
