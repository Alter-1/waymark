#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
shared_paths - is a directory that MUST be the same on every branch actually the same?

WHY THIS EXISTS
---------------
BEST-PRACTICE section 8 says it plainly: "two copies of anything that must agree will disagree",
and "what would notice when they diverge? If the answer is 'someone reading carefully', it will
diverge." That rule had no instrument. This is the instrument.

The case it was written for: a repository with several long-lived release branches and a set of
directories that are deliberately SHARED across all of them - a toolset, an SDK, a test suite. The
project's own rules said those must be byte-identical on every branch. On 2026-09-20 they were not,
and nobody had noticed:

    .tools/          six scripts existed on ONE branch of four, six more were older copies
    Eth2Serial/API/  fifteen files on one branch only, six more modified
    Autotests/       seventeen test files on one branch only

Every one of those is a tool or a test that somebody wrote, and that nobody working on the other
three branches knew existed. Divergence here is not a merge conflict waiting to happen - it is
work that silently does not exist for most of the people who have the repository.

WHY TREE HASHES, NOT A DIFF
---------------------------
`git rev-parse <branch>:<path>` is the tree object for that directory on that branch: one hash
over the whole subtree, content and names. Equal hashes mean byte-identical, with no walking and
no diff. Branches are then GROUPED by hash, so the report is "three agree, one differs" -- which is
almost always the shape -- instead of N-squared comparisons a reader has to fold together.

A tree hash also cannot be fooled by the things people try: an identical file with a different
mode, a file present on one side only, a rename. All of them change the tree.

WHAT IT DOES NOT DO
-------------------
It does not say which side is right, and it does not sync anything. Which branch is ahead is a
judgement -- an older line may be deliberately behind (a feature that was never back-ported), and
"identical" is the requirement only for paths somebody has DECLARED shared. So the paths are named
in config or on the command line, never guessed.

    python3 .tools/shared_paths.py                          # pairs from kb.config.json
    python3 .tools/shared_paths.py --path .tools --path Autotests --branch a --branch b
    python3 .tools/shared_paths.py --files                  # name the differing files too
    python3 .tools/shared_paths.py --quiet                  # exit code only (1 = something differs)

Config, in kb.config.json:

    "shared_paths": {
      "paths": [".tools", "Autotests", "Eth2Serial/API"],
      "branches": ["Smart", "uart-detect", "eth-alias"],
      "optional": ["esp32-c3"]          # absent on some branches BY DESIGN, not a divergence
    }
"""
from __future__ import print_function

import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ABSENT = "(absent)"


def git(*args):
    r = subprocess.run(["git"] + list(args), capture_output=True, text=True, cwd=ROOT)
    return r.stdout.strip() if r.returncode == 0 else None


def tree_hash(branch, path):
    """The git tree object for <path> on <branch>, or ABSENT when the path is not there."""
    return git("rev-parse", "%s:%s" % (branch, path)) or ABSENT


def file_delta(a_branch, b_branch, path):
    """(only_on_a, only_on_b, differing) file names between the two branches, under path."""
    out = git("diff", "--name-status", "%s..%s" % (a_branch, b_branch), "--", path) or ""
    only_a, only_b, both = [], [], []
    for line in out.split("\n"):
        if not line.strip():
            continue
        parts = line.split("\t")
        st, name = parts[0][:1], parts[-1]
        (only_a if st == "D" else only_b if st == "A" else both).append(name)
    return only_a, only_b, both


def load_config():
    p = os.path.join(ROOT, "kb.config.json")
    if not os.path.exists(p):
        return {}
    with open(p) as f:
        try:
            return json.load(f).get("shared_paths", {}) or {}
        except ValueError:
            return {}


def check(path, branches, optional, show_files, quiet):
    """Report one path. Returns 1 if the branches do not agree on it."""
    hashes = {b: tree_hash(b, path) for b in branches}
    present = {b: h for b, h in hashes.items() if h != ABSENT}
    missing = [b for b, h in hashes.items() if h == ABSENT]

    if not present:
        if not quiet:
            print("%-26s (on no branch)" % path)
        return 0

    groups = {}
    for b, h in present.items():
        groups.setdefault(h, []).append(b)
    agreed = len(groups) == 1
    # A path absent on some branches is a divergence unless it was DECLARED optional -- 1.18 has no
    # C3 target and never will, which is a fact about the product, not a drift.
    absent_ok = path in optional
    ok = agreed and (not missing or absent_ok)

    if not quiet:
        note = "" if not missing else "  [absent on %s%s]" % (
            ",".join(sorted(missing)), "" if absent_ok else " -- NOT declared optional")
        print("%-26s %s%s" % (path, "identical on %d branch(es)" % len(present) if agreed else "DIFFERS", note))
        if not agreed:
            # Biggest group first: "three agree, one differs" is the shape worth seeing.
            order = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[1][0]))
            base = order[0][1][0]
            for h, bs in order:
                print("    %-12s %s" % (h[:12], ", ".join(sorted(bs))))
            for h, bs in order[1:]:
                other = bs[0]
                only_base, only_other, both = file_delta(base, other, path)
                print("    %s vs %s: %d only on %s, %d only on %s, %d differ"
                      % (base, other, len(only_base), base, len(only_other), other, len(both)))
                if show_files:
                    for label, names in (("only on " + base, only_base),
                                         ("only on " + other, only_other),
                                         ("differ", both)):
                        for n in names:
                            print("        %-18s %s" % (label, n))
    return 0 if ok else 1


def main():
    cfg = load_config()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--path", action="append", default=[], help="path that must be shared (repeatable)")
    ap.add_argument("--branch", action="append", default=[], help="branch to compare (repeatable)")
    ap.add_argument("--branches", default="", help="comma-separated branches, as an alternative")
    ap.add_argument("--files", action="store_true", help="name every differing file, not just the count")
    ap.add_argument("--quiet", action="store_true", help="exit code only")
    a = ap.parse_args()

    paths = a.path or cfg.get("paths") or []
    branches = a.branch + [b for b in a.branches.split(",") if b] or cfg.get("branches") or []
    optional = set(cfg.get("optional") or [])
    if not paths or not branches:
        print("nothing to check: name --path and --branch, or put shared_paths in kb.config.json",
              file=sys.stderr)
        return 2
    if git("rev-parse", "--git-dir") is None:
        print("not a git repository", file=sys.stderr)
        return 2

    known = []
    for b in branches:
        if git("rev-parse", "--verify", "--quiet", b) is None:
            if not a.quiet:
                print("%-26s (no such branch)" % b)
            continue
        known.append(b)
    if len(known) < 2:
        print("need at least two existing branches to compare", file=sys.stderr)
        return 2

    bad = 0
    for p in paths:
        bad += check(p, known, optional, a.files, a.quiet)
    if not a.quiet:
        print()
        print("a shared path that differs is work the other branches do not have"
              if bad else "every declared shared path is the same on every branch")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
