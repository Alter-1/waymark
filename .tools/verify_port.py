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

THE SECOND SIGNAL: THE OLD CODE IS STILL THERE  (+OLD)
------------------------------------------------------
Tokens cannot see a fix that only CHANGES AN ARGUMENT. MEASURED 2026-09-19: a one-line fix turned

    memcpy(&state->linkq, buf, sizeof(crsf_telemetry_state_t));     // 148 bytes into a 10-byte field
into
    memcpy(&state->linkq, buf, sizeof(state->linkq));

on ONE of four lines. Every identifier in it already existed, so this tool said `no-tokens` -- nothing
testable -- and the memory corruption shipped on the other three for three weeks, until a day was
spent hunting a writer that had already been found and fixed.

What such a fix leaves behind is the line it REMOVED. So each commit's removed lines are collected,
kept only if the commit ELIMINATED them from the whole tree (a line that still exists somewhere after
the commit was moved or duplicated, not fixed away -- a refactor that moves a function between files
must not report), and the target is searched for them. A hit is marked `+OLD` on the cell: the target
still carries code this commit got rid of. It is the strongest gap signal here, and it is still
triage -- the line may have been removed for a reason that does not apply to that branch.

READ THE REF, NOT A CHECKOUT
----------------------------
`--ref name=<ref>` reads the branch itself through git, so a checkout sitting on another branch cannot
answer under the wrong label (see Docs/CROSS-BRANCH-REGISTER.md). A commit already in the ref's
history is reported `in-history` without a content check. `--target name=path` still greps a working
tree, for trees that are not refs of --repo.
"""

import argparse
import os
import re
import subprocess
import sys
import tempfile

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
    """Distinctive identifiers and string literals added by this commit.

    Returns (tokens, extensions). The extensions are the file types the commit actually touched --
    see present_in() for why the search has to be restricted to those and not to a fixed list."""
    diff = sh(["git", "-C", repo, "show", sha, "--format=", "--unified=0"])
    keep_file = True
    found = []
    exts = set()
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:]
            keep_file = not SKIP_PATH.search(path)
            if keep_file:
                exts.add(os.path.splitext(path)[1])
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
    return keep, exts


def _in_parent(repo, sha, token):
    """Did this token already exist in the tree BEFORE the commit?"""

    # git grep exits 0 on a hit; sh() swallows the code, so re-run for the status
    try:
        return subprocess.call(["git", "-C", repo, "grep", "-qF", "--", token, "%s^" % sha],
                               stdout=open(os.devnull, "w"), stderr=subprocess.STDOUT) == 0
    except Exception:
        return False


def present_in(tree, token, exts=None):
    """grep -rqF across the tree, restricted to the file types the COMMIT touched.

    It used to grep a FIXED list -- .cs .xaml .cpp .h .xml -- which is the file set of the one
    codebase this was written against. Every other kind of file was invisible to it, so a port that
    landed in a .py, .js, .html, .sh or .md was reported ABSENT with the file sitting in the target
    tree byte-identical.

    MEASURED 2026-09-07, which is why this is not a tidy-up: Autotests/FBI/at_separator_matrix.py
    is identical on two branches (md5 6d333438) and was reported absent from one of them, as was a
    second port into the same branch. A false ABSENT is the direction that wastes a day -- somebody
    redoes a port that is already there.

    Deriving the set from the commit cannot make that mistake: the fix is looked for in the kind of
    file it was made in. A commit touching an extensionless file (Makefile, a script) searches
    everything rather than guessing."""
    cmd = ["grep", "-rqF"]
    if exts and all(exts):
        cmd += ["--include=*%s" % e for e in sorted(exts)]
    cmd += ["--exclude-dir=obj", "--exclude-dir=bin", "--exclude-dir=.git",
            "--exclude-dir=node_modules", "--", token, tree]
    try:
        return subprocess.call(cmd, stdout=open(os.devnull, "w"),
                               stderr=subprocess.STDOUT) == 0
    except Exception:
        return False


# Lines that are comments in these file types. '#' is NOT a comment in C: a changed #define is code.
HASH_COMMENT_EXTS = set(".py .sh .cmake .txt .yml .yaml .cfg .ini .conf .mk .toml".split())


# A trailing comment is not code: `x = 1;   // why` and `x = 1;` are the same line. Measured: a fix
# re-applied on one branch without its trailing comment read as "+OLD" on the branches that kept it.
# ` //` and ` /* ... */` need the whitespace before them, so a URL in a string ("http://") survives.
TRAILING_COMMENT = re.compile(r"\s+(//.*|/\*.*\*/\s*)$")


def _code_of(body):
    return TRAILING_COMMENT.sub("", body).rstrip()


def _is_comment(body, ext):
    if body.startswith(("//", "/*", "*", "<!--")):
        return True
    return body.startswith("#") and (ext in HASH_COMMENT_EXTS or ext == "")


def _grep_found(repo, rev, patterns, exts, skip, paths=None):
    """Which of these fixed strings occur anywhere in `rev` (a commit or ref), in files of the given
    types, outside paths matching `skip`? One `git grep -f` for the lot.

    MATCHES INSIDE A COMMENT DO NOT COUNT. Measured on a real tree: two "gaps" were the old call
    sitting commented out on the other branch, and one was the line quoted in a comment above its
    replacement. Commented-out code is not code -- on either side: a line the commit moved into a
    comment counts as eliminated, which is what it is."""
    if not patterns:
        return set()
    fd, path = tempfile.mkstemp(prefix="verify_port.")
    try:
        with os.fdopen(fd, "w") as f:
            f.write("\n".join(patterns) + "\n")
        cmd = ["git", "-C", repo, "grep", "-F", "-n", "-I", "-f", path, rev, "--"]
        if paths:
            cmd += list(paths)
        elif exts and all(exts):
            cmd += ["*%s" % e for e in sorted(exts)]
        out = sh(cmd)
    finally:
        os.unlink(path)
    found = set()
    prefix = rev + ":"
    for line in out.splitlines():
        if line.startswith(prefix):
            line = line[len(prefix):]
        fpath, sep, rest = line.partition(":")
        if not sep or (skip and skip.search(fpath)):
            continue
        _lineno, sep, body = rest.partition(":")
        if not sep:
            continue
        stripped = body.strip()
        if _is_comment(stripped, os.path.splitext(fpath)[1]):
            continue
        for pat in patterns:
            if pat in body:
                found.add(pat)
    return found


def old_lines_for(repo, sha, skip=None, min_len=20, tip=None):
    """Code lines this commit REMOVED and that exist nowhere in the tree after it -- nor, given
    `tip`, at the tip of the line the commit is on.

    A removed line that survives elsewhere was moved, not fixed away, so it says nothing about a
    target that still has it. One that is back at the tip was undone on the source line itself (a
    later commit restored it): measured, a fix first done with a class and then replaced by one CSS
    rule reported the restored markup as a gap on every other line. Comments are ignored: rewording
    one is not a fix.

    Returns (lines, exts, by_path): by_path maps each file the commit changed to its old lines."""
    diff = sh(["git", "-C", repo, "show", sha, "--format=", "--unified=0", "-M"])
    keep_file = True
    ext = ""
    cand = []
    exts = set()
    for line in diff.splitlines():
        if line.startswith("--- "):
            path = line[6:] if line.startswith("--- a/") else ""
            keep_file = bool(path) and not SKIP_PATH.search(path) and not (skip and skip.search(path))
            ext = os.path.splitext(path)[1]
            if keep_file:
                exts.add(ext)
            continue
        if line.startswith("diff ") or line.startswith("+++ "):
            continue
        if not keep_file or not line.startswith("-"):
            continue
        body = line[1:].strip()
        if _is_comment(body, ext):
            continue
        body = _code_of(body) if ext not in HASH_COMMENT_EXTS else body
        if len(body) < min_len:
            continue
        cand.append((path, body))
    lines = sorted(set(b for _, b in cand))
    if not lines:
        return [], exts, {}
    gone = set(lines) - _grep_found(repo, sha, lines, exts, skip)
    if tip and gone:
        gone -= _grep_found(repo, tip, sorted(gone), exts, skip)
    by_path = {}
    for pth, b in cand:
        if b in gone:
            by_path.setdefault(pth, set()).add(b)
    return sorted(gone), exts, dict((k, sorted(v)) for k, v in by_path.items())


def present_in_ref(repo, ref, token, exts=None):
    """present_in() for a git ref: the branch itself, whatever is checked out."""
    cmd = ["git", "-C", repo, "grep", "-qF", "-e", token, ref, "--"]
    if exts and all(exts):
        cmd += ["*%s" % e for e in sorted(exts)]
    try:
        return subprocess.call(cmd, stdout=open(os.devnull, "w"), stderr=subprocess.STDOUT) == 0
    except Exception:
        return False


def old_hits_in_ref(repo, ref, by_path, exts, skip):
    """How many old lines the ref still carries -- looked for IN THE FILE THE COMMIT CHANGED, where
    the ref has that file. A whole-tree search matched old text quoted in unrelated files (docs,
    vendored copies). Where the ref lacks the file (renamed, or never had it), the whole tree of
    the same file types is searched instead."""
    found = set()
    for pth, lines in by_path.items():
        has = subprocess.call(["git", "-C", repo, "cat-file", "-e", "%s:%s" % (ref, pth)],
                              stdout=open(os.devnull, "w"), stderr=subprocess.STDOUT) == 0
        found |= _grep_found(repo, ref, lines, exts, skip, paths=[pth] if has else None)
    return len(found)


def in_history(repo, sha, ref):
    try:
        return subprocess.call(["git", "-C", repo, "merge-base", "--is-ancestor", sha, ref],
                               stdout=open(os.devnull, "w"), stderr=subprocess.STDOUT) == 0
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
    p.add_argument("--target", action="append", default=[],
                   help="name=path of a working tree to search, repeatable")
    p.add_argument("--ref", action="append", default=[],
                   help="name=ref of --repo to search THROUGH GIT, repeatable (preferred: see docstring)")
    p.add_argument("--skip", action="append", default=[],
                   help="regex of paths to ignore (generated files), repeatable")
    p.add_argument("--min-line", type=int, default=20, help="shortest removed line tested for +OLD")
    p.add_argument("--only-old", action="store_true", help="print only commits with a +OLD cell")
    p.add_argument("--no-tokens", action="store_true",
                   help="skip the token score, test +OLD only -- one git grep per commit and target "
                        "instead of ~40; the score shows as 'skipped'")
    p.add_argument("--hide-shared", action="store_true",
                   help="drop commits already in the history of every --ref")
    p.add_argument("--csv")
    a = p.parse_args()
    skip = re.compile("|".join("(?:%s)" % x for x in a.skip)) if a.skip else None

    targets = []
    for t in a.target:
        name, _, path = t.partition("=")
        if not path or not os.path.isdir(path):
            sys.stderr.write("bad --target %s\n" % t)
            sys.exit(2)
        targets.append((name, "tree", path))
    for t in a.ref:
        name, _, ref = t.partition("=")
        ref = ref or name
        if subprocess.call(["git", "-C", a.repo, "rev-parse", "-q", "--verify", ref + "^{commit}"],
                           stdout=open(os.devnull, "w"), stderr=subprocess.STDOUT) != 0:
            sys.stderr.write("bad --ref %s: not a commit in %s\n" % (t, a.repo))
            sys.exit(2)
        targets.append((name, "ref", ref))
    if not targets:
        sys.stderr.write("give at least one --ref or --target\n")
        sys.exit(2)

    log = sh(["git", "-C", a.repo, "log", "--no-merges", "--format=%H%x01%ad%x01%s",
              "--date=short", "--since=%s" % a.since, a.rev])
    rows = []
    for line in log.splitlines():
        parts = line.split("\x01")
        if len(parts) != 3:
            continue
        sha, date, subj = parts
        toks, exts = ([], set()) if a.no_tokens else tokens_for(a.repo, sha)
        old, old_exts, old_by_path = old_lines_for(a.repo, sha, skip, a.min_line, tip=a.rev)
        res = {}
        for name, kind, where in targets:
            if kind == "ref" and in_history(a.repo, sha, where):
                res[name] = ("in-history", 0, 0, 0)
                continue
            if kind == "ref":
                hits = sum(1 for t in toks if present_in_ref(a.repo, where, t, exts))
                old_hits = old_hits_in_ref(a.repo, where, old_by_path, old_exts, skip)
            else:
                hits = sum(1 for t in toks if present_in(where, t, exts))
                old_hits = sum(1 for o in old if present_in(where, o, old_exts))
            res[name] = ("skipped" if a.no_tokens else classify(hits, len(toks)), hits, len(toks), old_hits)
        if a.hide_shared and all(r[0] == "in-history" for r in res.values()):
            continue
        rows.append((sha[:8], date, subj, toks, res, old))

    def cell(r):
        return r[0] + ("+OLD" if r[3] else "")

    names = [n for n, _, _ in targets]
    w = max(len(s) for s in names + ["no-tokens+OLD"])
    print("")
    print("  %-8s %-10s %-58s %s" % ("commit", "date", "subject", "  ".join("%-*s" % (w, n) for n in names)))
    print("  " + "-" * (80 + (w + 2) * len(names)))
    tally = dict((n, {}) for n in names)
    for sha, date, subj, toks, res, old in rows:
        cells = []
        for n in names:
            st = cell(res[n])
            tally[n][st] = tally[n].get(st, 0) + 1
            cells.append("%-*s" % (w, st))
        if a.only_old and not any(res[n][3] for n in names):
            continue
        print("  %-8s %-10s %-58s %s" % (sha, date, subj[:58], "  ".join(cells)))
        if any(res[n][3] for n in names):
            for o in old[:3]:
                print("  %8s   old: %s" % ("", o[:110]))
    print("")
    for n in names:
        print("  %-14s %s" % (n, "  ".join("%s=%d" % (k, v) for k, v in sorted(tally[n].items()))))
    print("")
    print("  TRIAGE ONLY. 'partial' is the interesting column and always needs reading by hand.")
    print("  'no-tokens' means nothing testable (version bump, translations) - not evidence either way.")
    print("  '+OLD' = the target still carries a line this commit ELIMINATED from its tree -- read it first.")

    if a.csv:
        f = open(a.csv, "w")
        try:
            f.write("commit,date,subject," + ",".join(names) + ",tokens\n")
            for sha, date, subj, toks, res, old in rows:
                f.write('%s,%s,"%s",%s,"%s"\n'
                        % (sha, date, subj.replace('"', "'"),
                           ",".join(cell(res[n]) for n in names), "|".join(toks)))
        finally:
            f.close()
        print("  csv: %s" % a.csv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
