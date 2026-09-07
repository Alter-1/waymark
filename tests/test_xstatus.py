#!/usr/bin/env python3
"""Tests for the cross-branch register.

Every case here is a behaviour that was once wrong, or a guard whose absence would silently produce
a WRONG ANSWER rather than an error - which is the failure mode this tool exists to prevent.

    python3 tests/test_xstatus.py

Standard library only, no test framework, exit code 1 on failure.
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / ".tools" / "xstatus.py"

FAILS = []


def run(db, *args):
    env = dict(os.environ)
    env["XSTATUS_DB"] = str(db)
    p = subprocess.Popen([sys.executable, str(TOOL), "--db", str(db)] + list(args),
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
    out, _ = p.communicate()
    return p.returncode, out.decode("utf-8", "replace")


def check(name, cond, detail=""):
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s   %s" % (name, detail))
        FAILS.append(name)


def setup(db):
    run(db, "init")
    run(db, "branch-add", "master", "--repo", "app", "--path", "/a", "--order", "10")
    run(db, "branch-add", "next", "--repo", "app", "--path", "/b", "--order", "20")
    run(db, "branch-add", "legacy", "--repo", "app", "--path", "/c", "--order", "30", "--frozen")
    run(db, "flow-add", "--from", "master", "--to", "next")


def main():
    tmp = tempfile.mkdtemp()
    db = Path(tmp) / "t.sqlite"
    setup(db)

    # A deliberate absence with no reason is indistinguishable from an oversight a year later.
    run(db, "item-add", "A", "--kind", "bug", "--title", "a")
    rc, _ = run(db, "set", "A", "next", "not-applicable")
    check("not-applicable requires --why", rc != 0)
    rc, _ = run(db, "set", "A", "next", "not-applicable", "--why", "different subsystem")
    check("not-applicable accepted with --why", rc == 0)

    # 'ported' without a source loses the direction, which is the thing worth recording.
    rc, _ = run(db, "set", "A", "master", "ported")
    check("ported requires --from", rc != 0)

    # NOT ASSESSED must never render as a gap. This is the mistake the tool exists to stop.
    run(db, "item-add", "B", "--kind", "bug", "--title", "b")
    rc, out = run(db, "matrix")
    check("unassessed shows as '-' not a gap", " - " in out or out.count("-") > 0)
    rc, out = run(db, "gaps")
    check("unassessed absent from gaps", "B" not in out.replace("BLOCKED", ""))

    # Freshness is derived: whoever holds the newest change is primary, and the branch it came
    # from flips to behind. Recording only a status would lose this.
    run(db, "item-add", "C", "--kind", "feature", "--title", "c")
    run(db, "change-add", "C", "master", "--date", "2026-01-01", "--scope", "all")
    run(db, "change-add", "C", "next", "--date", "2026-02-01", "--kind", "port",
        "--from", "master", "--scope", "all")
    run(db, "flow-add", "--from", "next", "--to", "master")
    rc, out = run(db, "review")
    check("later change makes the origin behind", "master" in out and "C" in out)

    # ...but only in directions we actually port. Divergence in a direction nobody works is noise.
    run(db, "flow-add", "--from", "next", "--to", "master", "--inactive")
    rc, out = run(db, "review")
    check("inactive direction is not reviewed", "nothing to review" in out)

    # A newer change may be branch-specific. Resolving it must retire the question permanently.
    run(db, "flow-add", "--from", "next", "--to", "master")
    rc, out = run(db, "sql", "SELECT id FROM item_change WHERE change_date='2026-02-01'")
    cid = out.strip().splitlines()[-1].strip()
    run(db, "scope", cid, "branch-only", "--why", "2.x-side adaptation")
    rc, out = run(db, "review")
    check("branch-only retires the question", "nothing to review" in out)

    # Nothing ports INTO a frozen line, and saying so must be an error rather than a silent no-op.
    rc, out = run(db, "flow-add", "--from", "master", "--to", "legacy")
    check("refuses a flow into a frozen branch", rc != 0 and "FROZEN" in out)

    # A gap on a frozen line is real but blocked - listing it as actionable invites wasted work.
    run(db, "item-add", "D", "--kind", "bug", "--title", "d")
    run(db, "set", "D", "legacy", "not-implemented", "--why", "owed when the freeze lifts")
    rc, out = run(db, "gaps")
    check("frozen gaps marked BLOCKED", "BLOCKED" in out)

    # The commit-message form is what makes the register rebuildable from git.
    run(db, "item-add", "E", "--kind", "bug", "--title", "e title")
    run(db, "change-add", "E", "master", "--date", "2026-03-01", "--commit", "deadbee")
    rc, out = run(db, "msg", "E", "--branch", "next", "--from", "master")
    check("msg emits port(src->dst): [REF]", "port(master->next): [E]" in out)
    check("msg records the source commit", "Ported-From: master deadbee" in out)

    print("")
    if FAILS:
        print("FAILED: %s" % ", ".join(FAILS))
        return 1
    print("all ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
