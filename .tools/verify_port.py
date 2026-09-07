#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
verify_port - decide whether a commit's SUBSTANCE is present on other branches.

WHY NOT git cherry
------------------
`git cherry` and `git log --cherry-pick` match on PATCH ID. In these trees essentially every port is
hand-adapted - the KB's own survey found 61 of 66 conflict - so patch identity cannot see a port that
landed. Measured 070926: git cherry called all six candidate German fixes ABSENT from master; content
verification found ALL SIX PRESENT. It also missed nine real ports into Veo-2.0. The tool is not
merely imprecise here, it is wrong in both directions, so it must not be used to decide presence.

WHAT THIS DOES INSTEAD
----------------------
For each commit it picks DISTINCTIVE TOKENS out of the added lines - identifiers and string literals
that the ported code would have to contain whatever the surrounding context looks like - and greps
the target working trees for them. Adaptation changes context, indentation and neighbouring code; it
does not usually rename the identifier the fix turns on.

WHAT IT IS AND IS NOT
---------------------
This is a TRIAGE tool. It reports a score, not a verdict:

    present    every token found            -> almost certainly there, spot-check one
    partial    some tokens found            -> THE INTERESTING CASE. Usually a genuine partial port:
                                               e.g. the CAT option's checkbox present, its per-line
                                               default absent. Always read these by hand.
    absent     no token found               -> likely genuinely missing, still confirm by reading
    no-tokens  nothing distinctive to test  -> version bumps, translation-only, whitespace. Tells you
                                               nothing; excluded from the counts on purpose.

A score is never a reason to port or not port. Record the decision in xstatus with the reason.
"""

import argparse
import os
import re
import subprocess
import sys

# Files whose presence says nothing about whether a FIX was ported.
SKIP_PATH = re.compile(
    r"(MultilingualResources/.*\.xlf$|\.resx$|/obj/|\.Designer\.cs$|version\.def$|"
    r"AssemblyInfo\.cs$|\.vdproj$|\.sln$|packages\.config$)", re.I)

# Tokens that would match everywhere and prove nothing.
NOISE = set("""if else return true false null new var void public private protected internal static
string int bool double float this base using namespace class get set value try catch finally throw
for while switch case break continue default override virtual async await task list dictionary""".split())


def sh(cmd, cwd=None):
    try:
        out = subprocess.check_output(cmd, cwd=cwd, stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as e:
        out = e.output
    if isinstance(out, bytes):
        out = out.decode("utf-8", "replace")
    return out


def tokens_for(repo, sha, max_tokens=6):
    """Distinctive identifiers and string literals added by this commit."""
    diff = sh(["git", "-C", repo, "show", sha, "--format=", "--unified=0"])
    keep_file = True
    found = []
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            keep_file = not SKIP_PATH.search(line[6:])
            continue
        if line.startswith("diff ") or line.startswith("--- "):
            continue
        if not keep_file or not line.startswith("+") or line.startswith("+++"):
            continue
        body = line[1:].strip()
        if not body or body.startswith("//") or body.startswith("*") or body.startswith("<!--"):
            continue
        # string literals first - they survive adaptation best
        for lit in re.findall(r'"([^"\\]{12,60})"', body):
            if not lit.startswith("{") and " " not in lit[:3]:
                found.append(lit)
        # then long/compound identifiers
        for ident in re.findall(r"\b([A-Za-z_][A-Za-z0-9_]{11,})\b", body):
            if ident.lower() in NOISE:
                continue
            found.append(ident)
    # de-duplicate, prefer the rarest-looking (longest) tokens
    uniq = []
    for t in sorted(set(found), key=lambda s: -len(s)):
        if not any(t in u or u in t for u in uniq):
            uniq.append(t)
        if len(uniq) >= max_tokens * 3:
            break

    # A token only DISTINGUISHES the fix if the commit INTRODUCED it. Anything that already existed
    # in the parent tree is present on any branch that has the surrounding code, fix or no fix.
    #
    # This was not a theoretical concern. MEASURED 070926, before this filter existed: e4cdad56
    # (add a null guard around saveToDatabaseControl.ImageData) yielded the single token
    # "saveToDatabaseControl" - which of course exists in Veo-2.0, so the fix was reported PRESENT
    # there when hand verification showed the guard is absent. Every fix that GUARDS or WRAPS
    # existing code failed the same way, and the failure direction is the dangerous one: a false
    # "present" hides a real gap.
    keep = []
    for t in uniq:
        if not _in_parent(repo, sha, t):
            keep.append(t)
        if len(keep) >= max_tokens:
            break
    return keep


def _in_parent(repo, sha, token):
    """Did this token already exist in the tree BEFORE the commit?"""

    # git grep exits 0 on a hit; sh() swallows the code, so re-run for the status
    try:
        return subprocess.call(["git", "-C", repo, "grep", "-qF", "--", token, "%s^" % sha],
                               stdout=open(os.devnull, "w"), stderr=subprocess.STDOUT) == 0
    except Exception:
        return False


def present_in(tree, token):
    """grep -rqF across the tree's source, excluding generated output."""
    cmd = ["grep", "-rqF", "--include=*.cs", "--include=*.xaml", "--include=*.cpp",
           "--include=*.h", "--include=*.xml", "--exclude-dir=obj", "--exclude-dir=bin",
           "--exclude-dir=.git", "--", token, tree]
    try:
        return subprocess.call(cmd, stdout=open(os.devnull, "w"),
                               stderr=subprocess.STDOUT) == 0
    except Exception:
        return False


def classify(hits, total):
    if total == 0:
        return "no-tokens"
    if hits == total:
        return "present"
    if hits == 0:
        return "absent"
    return "partial"


def main():
    p = argparse.ArgumentParser(description="content-based port verification (NOT patch identity)")
    p.add_argument("--repo", required=True, help="repo to read commits FROM")
    p.add_argument("--rev", required=True, help="e.g. origin/customer-fork")
    p.add_argument("--since", required=True)
    p.add_argument("--target", action="append", required=True,
                   help="name=path of a working tree to search, repeatable")
    p.add_argument("--csv")
    a = p.parse_args()

    targets = []
    for t in a.target:
        name, _, path = t.partition("=")
        if not path or not os.path.isdir(path):
            sys.stderr.write("bad --target %s\n" % t)
            sys.exit(2)
        targets.append((name, path))

    log = sh(["git", "-C", a.repo, "log", "--no-merges", "--format=%H%x01%ad%x01%s",
              "--date=short", "--since=%s" % a.since, a.rev])
    rows = []
    for line in log.splitlines():
        parts = line.split("\x01")
        if len(parts) != 3:
            continue
        sha, date, subj = parts
        toks = tokens_for(a.repo, sha)
        res = {}
        for name, path in targets:
            hits = sum(1 for t in toks if present_in(path, t))
            res[name] = (classify(hits, len(toks)), hits, len(toks))
        rows.append((sha[:8], date, subj, toks, res))

    names = [n for n, _ in targets]
    w = max(len(s) for s in names + ["branch"])
    print("")
    print("  %-8s %-10s %-58s %s" % ("commit", "date", "subject", "  ".join("%-*s" % (w, n) for n in names)))
    print("  " + "-" * (80 + (w + 2) * len(names)))
    tally = dict((n, {}) for n in names)
    for sha, date, subj, toks, res in rows:
        cells = []
        for n in names:
            st = res[n][0]
            tally[n][st] = tally[n].get(st, 0) + 1
            cells.append("%-*s" % (w, st))
        print("  %-8s %-10s %-58s %s" % (sha, date, subj[:58], "  ".join(cells)))
    print("")
    for n in names:
        print("  %-14s %s" % (n, "  ".join("%s=%d" % (k, v) for k, v in sorted(tally[n].items()))))
    print("")
    print("  TRIAGE ONLY. 'partial' is the interesting column and always needs reading by hand.")
    print("  'no-tokens' means nothing testable (version bump, translations) - not evidence either way.")

    if a.csv:
        f = open(a.csv, "w")
        try:
            f.write("commit,date,subject," + ",".join(names) + ",tokens\n")
            for sha, date, subj, toks, res in rows:
                f.write('%s,%s,"%s",%s,"%s"\n'
                        % (sha, date, subj.replace('"', "'"),
                           ",".join(res[n][0] for n in names), "|".join(toks)))
        finally:
            f.close()
        print("  csv: %s" % a.csv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
