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
    # ONE grep for every candidate instead of one per candidate. The ORDER and the cap are kept,
    # so the tokens chosen are the same ones -- see tokens_found_in_ref() for why speed matters here.
    inherited = _in_parent_bulk(repo, sha, uniq)
    keep = []
    for t in uniq:
        if t not in inherited:
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


# Words too common to anchor a phrase on. A phrase is only distinctive if its WORDS are.
PROSE_STOP = set("""the and that this with from have been were will would could should than then
into over under about after before while which when where what does done just only also even
such same both each other another more most less least very much many some none than
port ports code line lines file files case cases value values return returns
""".split())


def phrases_from_comments(lines):
    """The phrase-picking rule, with no git in it so the tests can hold it still."""
    # *** SHOUTED PHRASES FIRST, AND THAT ORDER IS THE MEASUREMENT, NOT A STYLE PREFERENCE. ***
    # 2026-09-29: the first version of this took 4-word windows of words >= 4 chars. On the commit it
    # was built for it chose "request ripened almost immediately" -- and the three PORTS had reworded
    # that to "ripens about a second later", so every phrase missed and the signal read as absent.
    # What HAD crossed verbatim to all four branches was "DROP ANY QUEUED POWER-OFF", which that rule
    # could not even extract: ANY and OFF are three letters, so no window could form across them.
    # A CAPITALISED PHRASE IS THE ONE PART OF A COMMENT PEOPLE MOVE WITHOUT REWRITING -- it is the
    # point being made, not the explanation around it. Take those first; the prose window is a
    # fallback, and it is the weaker signal.
    caps, cand = [], []
    for body in lines:
        for run in re.findall(r"\b[A-Z][A-Z0-9'-]*(?:[ -][A-Z][A-Z0-9'-]*){2,}\b", body):
            run = run.strip(" -")
            words = run.split()
            if len(run) >= 15 and sum(1 for w in words if len(w) >= 4) >= 2:
                caps.append(run)
        words = re.findall(r"[A-Za-z][A-Za-z0-9_'-]*", body)
        for i in range(len(words) - 3):
            win = words[i:i + 4]
            # Relaxed from "every word >= 4" to "at least two ANCHOR words": a short word inside a
            # phrase does not make it common, and demanding four long ones threw away the good ones.
            anchors = [w for w in win if len(w) >= 5 and w.lower() not in PROSE_STOP]
            if len(anchors) < 2 or all(w.lower() in PROSE_STOP for w in win):
                continue
            # Rebuild from the ORIGINAL line so punctuation inside the window survives a -F grep.
            phrase = " ".join(win)
            if len(phrase) >= 20 and phrase in body:
                cand.append(phrase)
    return caps + cand


def prose_for(repo, sha, max_phrases=4):
    """Distinctive PHRASES from the comment lines this commit added.

    *** PROSE PORTS WHERE IDENTIFIERS DO NOT. *** tokens_for() deliberately skips comments, and
    _grep_found() deliberately ignores matches inside them -- both correct, because a comment
    mentioning a symbol is not an implementation of it. But that leaves this tool blind to the port
    that was ADAPTED rather than copied, which is the normal kind here.

    MEASURED 2026-09-29 on a real four-branch tree. Commit 7b81bf05 ("an explicit On/Off cancels a
    queued power-off") scored `absent` on ALL THREE target branches. It is PRESENT on all three: two
    of them call ClearQueuePowerOff(), and the third -- a line with a different port model -- does the
    same job as `nQueuedTransiverPowerOff[n] = 0`. The adaptation renamed the very thing the fix turns
    on, which is exactly what this tool's docstring assumes does not happen. Three false ABSENTs, the
    direction that costs a day, in the one commit it was pointed at.

    What DID cross unchanged was the reasoning: the phrase "DROP ANY QUEUED POWER-OFF" is on all four
    branches, and "ripens" is on the three PORTS and not on the original -- whoever adapted it
    explained it in place. So the comment is the more faithful witness, and one git grep finds it.

    WHY PHRASES AND NOT WORDS: on the same measurement the single word "commanded" matched 30+ times
    on every branch. Only multi-word windows discriminate, and only words long enough to carry
    meaning anchor them -- hence PROSE_STOP and the length floor.
    """
    diff = sh(["git", "-C", repo, "show", sha, "--format=", "--unified=0"])
    keep_file = True
    lines, exts = [], set()
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:]
            keep_file = not SKIP_PATH.search(path)
            if keep_file:
                exts.add(os.path.splitext(path)[1])
            continue
        if line.startswith("diff ") or line.startswith("--- ") or not line.startswith("+"):
            continue
        if not keep_file or line.startswith("+++"):
            continue
        body = line[1:].strip()
        # ONLY comments here -- the mirror image of tokens_for(), which takes only code.
        if not re.match(r"(//|/\*|\*|#|<!--|;;)", body):
            continue
        body = re.sub(r"^(//+|/\*+|\*+|#+|<!--|;;)\s*", "", body)
        body = re.sub(r"(\*/|-->)\s*$", "", body)
        body = re.sub(r"\s+", " ", body).strip(" *")
        if len(body) >= 30:
            lines.append(body)

    cand = phrases_from_comments(lines)
    # Keep the caps-first ORDER; only de-duplicate. sorted(set(...)) would have discarded it.
    seen, ordered = set(), []
    for p in cand:
        if p not in seen:
            seen.add(p); ordered.append(p)
    uniq = []
    for p in ordered:
        if not any(p in u or u in p for u in uniq):
            uniq.append(p)
        if len(uniq) >= max_phrases * 3:
            break
    # A phrase already in the parent tree says nothing -- same rule as the code tokens.
    inherited = _in_parent_bulk(repo, sha, uniq)
    out = [p for p in uniq if p not in inherited]
    return out[:max_phrases], exts


def prose_in_ref(repo, ref, phrases, exts, skip):
    """How many of these phrases appear in `ref` -- COMMENTS INCLUDED, which is the whole point.

    _grep_found() strips comments before matching; this must not, so it greps directly. A hit means
    the commit's own reasoning is on that branch, which is evidence the port was CONSIDERED there --
    never evidence the code is present. The cell says `+PROSE`, and a human reads it."""
    if not phrases:
        return 0
    hits = 0
    for p in phrases:
        cmd = ["git", "-C", repo, "grep", "-qF", "--", p, ref]
        try:
            if subprocess.call(cmd, stdout=open(os.devnull, "w"), stderr=subprocess.STDOUT) == 0:
                hits += 1
        except Exception:
            pass
    return hits


def paths_touched(repo, sha):
    """The files this commit changed, minus the ones whose presence says nothing about a fix."""
    out = sh(["git", "-C", repo, "show", "--name-only", "--format=", sha])
    return [p for p in (l.strip() for l in out.splitlines()) if p and not SKIP_PATH.search(p)]


def can_hold(repo, ref, paths, is_ref=True):
    """Could this target hold this commit at all -- does ANY file it touched even exist there?

    *** A TARGET THAT DOES NOT HAVE THE FILE WAS BEING SCORED, AND SCORED WRONG IN THE DANGEROUS
    DIRECTION. *** Measured 2026-09-29 auditing twelve commits: d81fbc5f touches exactly one file,
    esp32-c3/main/local_hw.cpp. Two of the three target branches have no esp32-c3/ directory at all --
    that target is not built on those lines. The tool reported `present` on both, because the tokens it
    picked exist elsewhere in those trees for unrelated reasons. This file's own docstring calls a
    false `present` the one that hides a real gap, and here it was inventing a port into a target that
    cannot receive one.

    The rule is deliberately conservative: only ANY-none, never some-missing. A commit touching a
    shared file and a target-only file is still scored, because the shared half can genuinely land.
    And it is EXISTENCE, not equivalence -- a file renamed on the target reads as absent, so `n/a`
    means "look at whether this target has this code at all", not "this cannot possibly apply"."""
    if not paths:
        return True
    for p in paths:
        if is_ref:
            if subprocess.call(["git", "-C", repo, "cat-file", "-e", "%s:%s" % (ref, p)],
                               stdout=open(os.devnull, "w"), stderr=subprocess.STDOUT) == 0:
                return True
        elif os.path.exists(os.path.join(ref, p)):
            return True
    return False


def _looks_like_rev(repo, value):
    """Does this string resolve as a git revision? Used to catch it being passed to --since."""
    try:
        return subprocess.call(["git", "-C", repo, "rev-parse", "--verify", "--quiet", "%s^{commit}" % value],
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


def strip_comments(src, ext):
    """The file with its comments blanked -- /* */ and // (string-aware), or # for hash languages.

    git grep cannot see a BLOCK comment: measured on a real tree, a whole status block sat inside
    /* ... */ on three branches and every line in it read as live code, which is a gap that is not
    there. Lines are preserved so nothing else shifts."""
    if ext in HASH_COMMENT_EXTS:
        return "\n".join(l.split("#", 1)[0] if l.lstrip().startswith("#") else l
                          for l in src.splitlines())
    out, i, n, quote = [], 0, len(src), ""
    while i < n:
        c = src[i]
        if quote:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(src[i + 1])
                i += 2
                continue
            if c == quote:
                quote = ""
            i += 1
            continue
        if c in "\"'":
            quote = c
            out.append(c)
            i += 1
            continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            end = n if j < 0 else j + 2
            out.append("\n" * src.count("\n", i, end))
            i = end
            continue
        if src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _file_live(repo, rev, path, cache):
    key = (rev, path)
    if key not in cache:
        try:
            src = subprocess.check_output(["git", "-C", repo, "show", "%s:%s" % (rev, path)],
                                          stderr=subprocess.STDOUT).decode("utf-8", "replace")
        except subprocess.CalledProcessError:
            cache[key] = None
            return None
        cache[key] = strip_comments(src, os.path.splitext(path)[1])
    return cache[key]


def _is_comment(body, ext):
    if body.startswith(("//", "/*", "*", "<!--")):
        return True
    return body.startswith("#") and (ext in HASH_COMMENT_EXTS or ext == "")


def _grep_found(repo, rev, patterns, exts, skip, paths=None, skip_comments=True):
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
        # skip_comments=False is the TOKEN pass, which has always counted a token wherever it appears.
        # Kept deliberately: changing that would move scores, and this commit only changes SPEED.
        if skip_comments and _is_comment(stripped, os.path.splitext(fpath)[1]):
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


def tokens_found_in_ref(repo, ref, tokens, exts):
    """present_in_ref() for EVERY token in ONE git grep instead of one grep per token.

    *** WHY THIS EXISTS: THE TOOL WAS TOO SLOW TO BE USED ON THE QUESTION IT IS FOR. *** Measured
    2026-09-29 auditing twelve power commits across three branches: one grep per token per ref, plus
    one per token against the parent, is ~40 subprocess git greps per commit. On a large repository
    that is minutes per commit -- the twelve-commit run blew a 590-second timeout and had to be split
    into four parallel batches to finish at all. A tool nobody can afford to run does not get run,
    and then the question gets answered by hand and answered wrong, which is the whole reason this
    file exists.

    Same semantics as the loop it replaces: a token counts wherever it appears, comments included
    (skip_comments=False). Only the number of processes changes."""
    if not tokens:
        return set()
    return _grep_found(repo, ref, list(tokens), exts, None, skip_comments=False)


def _in_parent_bulk(repo, sha, tokens):
    """Which of these tokens already existed in the tree BEFORE the commit? One grep, not N.

    exts is deliberately NOT passed: _in_parent(), which this replaces, greps the WHOLE parent tree
    with no extension restriction. Narrowing it here would let a token that exists in a file type the
    commit did not touch slip through the filter and be scored as distinctive."""
    if not tokens:
        return set()
    return _grep_found(repo, "%s^" % sha, list(tokens), None, None, skip_comments=False)


def old_hits_in_ref(repo, ref, by_path, exts, skip, cache=None):
    """How many old lines the ref still carries AS LIVE CODE -- in the file the commit changed.

    The file is read and its comments stripped, because neither a block comment nor a commented-out
    call is code. A whole-tree grep is the fallback only where the ref lacks that file (renamed, or
    never had it); it matched old text quoted in unrelated files, so it is not the default."""
    cache = {} if cache is None else cache
    found = set()
    for pth, lines in by_path.items():
        live = _file_live(repo, ref, pth, cache)
        if live is None:
            found |= _grep_found(repo, ref, lines, exts, skip)
            continue
        found |= set(l for l in lines if l in live)
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
    p.add_argument("--rev", help="branch to read commits from, e.g. origin/customer-fork "
                                "(with --since; not needed when --commit names them)")
    p.add_argument("--since", help="DATE for git log, e.g. 2026-09-01 or '3 weeks ago'. "
                                   "NOT a revision -- see --commit. Beware that a date bound is "
                                   "read in LOCAL time, so --since=<the day a commit is dated> can "
                                   "exclude that commit if its own timezone puts it before local "
                                   "midnight; give the day before.")
    p.add_argument("--commit", action="append", default=[], metavar="SHA",
                   help="verify exactly this commit, repeatable. No date arithmetic, no timezone "
                        "edge, and it is what you want when chasing one fix.")
    p.add_argument("--target", action="append", default=[],
                   help="name=path of a working tree to search, repeatable")
    p.add_argument("--ref", action="append", default=[],
                   help="name=ref of --repo to search THROUGH GIT, repeatable (preferred: see docstring)")
    p.add_argument("--skip", action="append", default=[],
                   help="regex of paths to ignore (generated files), repeatable")
    p.add_argument("--min-line", type=int, default=20, help="shortest removed line tested for +OLD")
    p.add_argument("--only-old", action="store_true", help="print only commits with a +OLD cell")
    p.add_argument("--no-prose", action="store_true",
                   help="skip the +PROSE adapted-port signal (one extra git grep per absent cell)")
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

    # *** A REVISION PASSED TO --since IS ACCEPTED BY GIT AND MEANS "EVERYTHING". ***
    # `--since` is an approxidate, and approxidate does not fail: measured 2026-09-29,
    # `git log --since=<sha>^` returned 5218 commits instead of one, so the tool set about verifying
    # the entire history and had to be killed. Nothing in git's output says the bound was ignored.
    # Refuse it here and name the option that was actually wanted.
    if a.since and _looks_like_rev(a.repo, a.since):
        sys.stderr.write(
            "--since=%r resolves as a REVISION, and git would silently treat it as no bound at all\n"
            "(measured: 5218 commits instead of 1). --since takes a DATE. To verify one commit:\n"
            "    --commit %s\n" % (a.since, a.since))
        return 2
    if a.commit:
        if a.since or a.rev:
            sys.stderr.write("--commit names the commits outright; --rev/--since are for a range\n")
            return 2
        fmt = ["git", "-C", a.repo, "show", "-s", "--format=%H%x01%ad%x01%s", "--date=short"]
        log = "\n".join(sh(fmt + [c]).strip() for c in a.commit)
    else:
        if not (a.rev and a.since):
            sys.stderr.write("give either --commit SHA... or both --rev and --since\n")
            return 2
        log = sh(["git", "-C", a.repo, "log", "--no-merges", "--format=%H%x01%ad%x01%s",
                  "--date=short", "--since=%s" % a.since, a.rev])
    rows = []
    for line in log.splitlines():
        parts = line.split("\x01")
        if len(parts) != 3:
            continue
        sha, date, subj = parts
        toks, exts = ([], set()) if a.no_tokens else tokens_for(a.repo, sha)
        old, old_exts, old_by_path = old_lines_for(a.repo, sha, skip, a.min_line, tip=a.rev or sha)
        # Extracted lazily: only a cell that came back absent/no-tokens asks for it.
        prose = None
        touched = paths_touched(a.repo, sha)
        res = {}
        for name, kind, where in targets:
            if kind == "ref" and in_history(a.repo, sha, where):
                res[name] = ("in-history", 0, 0, 0, 0)
                continue
            # Asked BEFORE any token work: a target that cannot hold the commit must not be scored,
            # and skipping it is also the cheapest cell in the table. See can_hold().
            if not can_hold(a.repo, where, touched, is_ref=(kind == "ref")):
                res[name] = ("n/a", 0, 0, 0, 0)
                continue
            if kind == "ref":
                hits = len(tokens_found_in_ref(a.repo, where, toks, exts))
                old_hits = old_hits_in_ref(a.repo, where, old_by_path, old_exts, skip)
            else:
                hits = sum(1 for t in toks if present_in(where, t, exts))
                old_hits = sum(1 for o in old if present_in(where, o, old_exts))
            score = "skipped" if a.no_tokens else classify(hits, len(toks))
            # *** THE ADAPTED-PORT SIGNAL. *** Only asked when the code pass found nothing, so the
            # common case pays no extra greps. See prose_for() for the measurement behind it.
            prose_hits = 0
            if score in ("absent", "no-tokens") and kind == "ref" and not a.no_prose:
                if prose is None:
                    prose = prose_for(a.repo, sha)[0]
                prose_hits = prose_in_ref(a.repo, where, prose, exts, skip)
            res[name] = (score, hits, len(toks), old_hits, prose_hits)
        if a.hide_shared and all(r[0] == "in-history" for r in res.values()):
            continue
        rows.append((sha[:8], date, subj, toks, res, old))

    def cell(r):
        # +PROSE is NOT a weaker +OLD: it says the code was not found but the commit's own reasoning
        # IS on that branch, which is how an adapted port looks. Read it before believing 'absent'.
        return r[0] + ("+OLD" if r[3] else "") + ("+PROSE" if len(r) > 4 and r[4] else "")

    names = [n for n, _, _ in targets]
    w = max(len(s) for s in names + ["no-tokens+OLD+PROSE"])
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
    print("  'n/a' = not one file this commit touches exists on that branch -- it cannot hold it,")
    print("          so it is not scored. Usually a target that line does not build.")
    print("  '+PROSE' = the code was not found but this commit's own COMMENT text is on that branch:")
    print("            the normal shape of a port that was ADAPTED and renamed. Never trust 'absent' with it.")

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
