#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
kb_stale - is the knowledge base still TRUE OF THE CODE?

WHY THIS EXISTS
---------------
`query_code_index.py selftest` has ten checks and they are all about the KB's INTERNAL
consistency: links resolve, the vocabulary is known, a headline does not contradict its own
status, claims carry provenance. Every one of them passes on a KB that is beautifully consistent
and completely out of date.

Nothing checked the other direction - whether what an entry SAYS about the code is still so. That
is the direction knowledge actually rots in, and 090926 paid for it several times over:

  * A generated map, Docs/toolbar_map.md, was committed at 16:44 and the code it describes changed
    at 17:19 - 35 minutes later, same day. It then sat there for a day reporting the wrong site
    count, and nothing noticed, because nothing compares an entry against the files it cites.

  * Porting comments between branches, nearly every ":NNN" citation was wrong on arrival -
    ImagerCaptureController :546, Settings.xaml.cs:1415, setConfigModes :959, bTouchActivated :483,
    keypad_LostFocus :647. Each had to be re-resolved by hand.

  * One citation was wrong about a FACT, not a line. A comment said an X-ray gate covered
    "PixRadEZ / RayenceWGB / Bolt6K / VIVIX / DEMO". On that branch the LIVE gate was at :740 and
    read Guardian and IRay, not VIVIX - the VIVIX form was the SUPERSEDED, COMMENTED-OUT line
    directly above it at :739. In a tree whose house style is to comment old code out and leave it
    in place, reading the commented line as if it were the code is not an unlikely mistake. It is
    the DEFAULT mistake.

That last one is why this tool exists in waymark rather than as a grep: the index ALREADY records
symbols.commented_out. The knowledge to catch it was there and unused.

WHAT IT CHECKS

  file-exists      an entry's frontmatter "file:" still resolves on disk
  symbol-exists    the index knows an entry's "name:". TWO CAUSES, and the tool cannot tell them
                   apart - read the hit before believing either: the symbol really was renamed or
                   removed, OR THE INDEXER NEVER SAW IT. On 090926 all eight hits on this branch
                   were the second: the index was NEWER than the sources and still had 0 rows for
                   CDualEnergyManagementBase::IsDescentNotFollowing (:566) and
                   CCardCommand::ToggleDontUseCAT (:12222). What both have in common is a
                   MULTI-LINE SIGNATURE - the parameter list does not close on the definition
                   line - which is a gap in index_code.py, not staleness in the KB. Filed as
                   WAYMARK-MULTILINE-SIGNATURE.
  symbol-live      ... and is not ONLY present as a commented-out definition
  citation-range   "foo.cpp:1234" in a body points into a file that HAS 1234 lines. A BASENAME IS
                   NOT A FILE here - three CorrectionSet.cpp, two CardDefs.h, two ImageData.cpp -
                   so a citation is out of range only when NO copy of that name is long enough.
                   Taking the first match instead produced twelve false hits in one run.
  freshness        a cited file has commits AFTER the entry's own ts:, so the entry describes
                   code that has since moved. REVIEW, never a failure: a changed file does not
                   make an entry wrong, it makes it unverified. Grouped by file, because one busy
                   file drags every entry citing it into the list at once.

ADVISORY, NOT A CHECK

  citation-dead    a cited line sits in a comment that names a function. The premise is sound -
                   the 090926 VIVIX case was a comment citing the superseded line one above the
                   live one - but symbols.commented_out means "this text is inside a comment", and
                   that includes PROSE THAT MERELY NAMES A FUNCTION. All three hits on this KB are
                   exactly that: "see also CreateInfoTags()", "Note: ... in ProcessUserCommand()",
                   an _AT_ trace note. Zero were dead code. So it is printed to glance at and
                   never counted as a failure: a check that is wrong every time is one people
                   learn to skip, and they would take the other four with it.

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
It does not judge prose. "This function is slow" cannot be checked and is not attempted. It
reports only things with a definite answer, because a checker that cries wolf gets muted, and a
muted checker is worse than none - which is the same reasoning as vdproj_check refusing to guess
a repair rather than inventing one.

It also does not rewrite anything. A stale entry is a person's call: the fix might be to correct
the citation, or to delete a finding that is no longer true, and a script cannot tell which.

Python 3.7, standard library only.

    python .tools/kb_stale.py                    check the current branch's index
    python .tools/kb_stale.py --db <path>        a specific index
    python .tools/kb_stale.py --no-git           skip freshness (no git calls, much faster)
    python .tools/kb_stale.py --quiet            only problems, no REVIEW lines
"""

import argparse
import collections
import io
import json
import os
import re
import sqlite3
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# "foo.cpp:1234"  /  "Settings.xaml.cs:1403"  - a filename with an extension, then a line
CITATION = re.compile(r'\b([A-Za-z_][\w.\-]*\.(?:cpp|h|hpp|c|cs|xaml|py|cmd|vdproj))\s*:\s*(\d{1,6})\b')
FM = re.compile(r'^---\s*$')


def pick_db(explicit):
    """The index for THIS branch. Never a glob.

    sorted(glob("code_index*.sqlite"))[0] once made a test accuse the engine of corrupting a
    database it had never touched, and this directory currently also holds
    code_index.<branch>.sqlite.<pid>.tmp leftovers from interrupted runs, which such a glob would
    happily select. So the branch is asked for by name and a miss is an error, not a guess.
    """
    if explicit:
        return explicit
    try:
        br = subprocess.check_output(["git", "-C", ROOT, "rev-parse", "--abbrev-ref", "HEAD"],
                                     stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        br = ""
    # THE SAME FALLBACKS THE ENGINE USES, because the file has to be the one index_code.py wrote.
    # A directory that is not a git checkout gets "nogit" there; a detached HEAD reports "HEAD".
    # Reimplementing the name and getting it slightly wrong means this tool reports a clean bill
    # of health on a project it never opened - so it errors out loudly instead, and only after
    # trying what the engine would have written.
    if not br or br == "HEAD":
        br = "nogit"
    br = br.replace("/", "_")
    cand = os.path.join(HERE, "code_index.%s.sqlite" % br)
    if not os.path.exists(cand):
        sys.stderr.write("no index for branch %r at %s\n"
                         "run: python .tools/index_code.py     (or pass --db)\n" % (br, cand))
        sys.exit(2)
    return cand


INDEX_ROOTS = []


def _config():
    """kb.config.json, with `project` flattened - both shapes are in the wild.

    Some configs put roots/annotations at the top level, others nest them under "project". The
    engine reads them through PROJECT, so this has to accept the same two shapes or it silently
    finds no KB and reports a clean bill of health, which is the worst thing this tool could do.
    """
    cfg = os.path.join(ROOT, "kb.config.json")
    if not os.path.exists(cfg):
        return {}
    try:
        d = json.load(io.open(cfg, encoding="utf-8"))
    except Exception:
        return {}
    out = dict(d)
    out.update(d.get("project") or {})
    return out


def kb_dirs():
    """Every KB root, absolute. `annotations` may be a string OR a list - the engine accepts
    several (a project's own notes beside a shared toolchain KB), so this must too. A single
    string was all the first version handled, and against this repository's own config - which
    names two - it crashed rather than checking the first."""
    conf = _config().get("annotations") or "Docs/source_index_annotations.json"
    if isinstance(conf, str):
        conf = [conf]
    # A KB ROOT MAY BE OUTSIDE THE REPOSITORY, AND IS OFTEN WRITTEN WITH `~`. Joining "~/kb/x" to
    # ROOT produces "<repo>/~/kb/x", which exists nowhere -- so the walk found no entries, every
    # check reported ok, and the run exited 0 on a knowledge base it had never opened. Measured on
    # a 299-entry KB kept in a sibling worktree: 0 entries loaded, 5 checks "ok".
    # expanduser first, and only then treat a still-relative path as repo-relative.
    out = []
    for c in conf:
        c = os.path.expanduser(c)
        out.append(c if os.path.isabs(c) else os.path.join(ROOT, c))
    return out


def load_roots():
    return list(_config().get("roots") or [])


def entries():
    """(path, frontmatter dict, body) for every KB markdown entry, across every KB root.

    A single-document KB (Docs/source_index_annotations.json) has no .md files and simply
    contributes nothing here - the checks below are about entries that cite a file and a line, and
    that is the directory form. Walking a path that does not exist is silent, which is fine for a
    configured-but-absent root and is why the count is printed.
    """
    out = []
    for base in kb_dirs():
        out.extend(_entries_under(base))
    return out


def _entries_under(base):
    out = []
    for dirpath, _dirs, files in os.walk(base):
        for fn in sorted(files):
            if not fn.endswith(".md"):
                continue
            p = os.path.join(dirpath, fn)
            text = io.open(p, encoding="utf-8", errors="replace").read()
            lines = text.split("\n")
            fm, body_at = {}, 0
            if lines and FM.match(lines[0]):
                for i in range(1, len(lines)):
                    if FM.match(lines[i]):
                        body_at = i + 1
                        break
                    m = re.match(r'^(\w+):\s*(.*)$', lines[i])
                    if not m:
                        continue
                    if m.group(2).strip():
                        v = m.group(2).strip()
                        # An INLINE list is a list: "files: []" is empty, not a path named "[]",
                        # and "files: [a, b]" is two. Without this the empty form became a referent
                        # and was faithfully reported missing, once per entry that had one.
                        if v.startswith("[") and v.endswith("]"):
                            inner = v[1:-1].strip()
                            fm[m.group(1)] = [x.strip().strip('"\'')
                                              for x in inner.split(",") if x.strip()] if inner else []
                        else:
                            fm[m.group(1)] = v
                        continue
                    # A BLOCK LIST IS STILL A VALUE. Reading only "key: value" made every
                    # `files:` list an EMPTY key, so on a KB that writes its referents as a list
                    # the file check inspected nothing and reported ok -- a check that passes
                    # because it is not looking, which is the exact failure this tool exists to
                    # catch. Measured on a 299-entry KB: 24 entries named a file, 0 were checked.
                    items = []
                    for j in range(i + 1, len(lines)):
                        li = re.match(r'^\s+-\s+(.*)$', lines[j])
                        if not li:
                            break
                        v = li.group(1).strip().strip('"\'')
                        if v:
                            items.append(v)
                    if items:
                        fm[m.group(1)] = items
            out.append((p, fm, "\n".join(lines[body_at:])))
    return out


def load_index(db):
    """Lookups that understand QUALIFIED names.

    The index stores what the parser saw, which for C++ is usually qualified -
    "CDualEnergyManagementBase::AdjustDEExposure", "CXfoxCntrl::InitializeConnection". KB entries
    are keyed on the BARE name, because that is what a person searches for. A naive exact match
    therefore reports almost every entry as missing: the first run of this tool cried "115 symbols
    gone" and every one of them was present under its class.

    Worse, the exact match can find the WRONG row. "AbortAllActions" matched exactly once - a
    COMMENTED-OUT occurrence in cardgrab.cpp - while the live definition sits under
    CCardCommand::AbortAllActions. Reporting "every definition is commented out" from that would
    have been precisely the kind of confident, wrong answer this tool exists to catch.

    So a name resolves through three routes, and all matching rows are returned together.
    """
    con = sqlite3.connect(db)
    rows = list(con.execute("SELECT name, file, line, commented_out FROM symbols"))
    files = {}
    for (p,) in con.execute("SELECT path FROM files"):
        files.setdefault(os.path.basename(p).lower(), []).append(p)
    con.close()

    exact = collections.defaultdict(list)         # "A::b"      -> rows
    by_last = collections.defaultdict(list)       # "b"         -> rows   (method of any class)
    by_owner = collections.defaultdict(list)      # "A"         -> rows   (A is a class we see)
    by_file = collections.defaultdict(list)       # basename    -> rows
    for name, f, ln, co in rows:
        rec = (f, ln, co, name)
        exact[name].append(rec)
        by_file[os.path.basename(f).lower()].append(rec)
        if "::" in name:
            head, _, last = name.rpartition("::")
            by_last[last].append(rec)
            by_owner[head.split("::")[-1]].append(rec)

    def resolve(name):
        """Every row that could be this KB entry's symbol, by any of the three routes."""
        return exact.get(name, []) + by_last.get(name, []) + by_owner.get(name, [])

    return resolve, by_file, files


# ---------------------------------------------------------------------------------------------
# ONE KNOWLEDGE BASE, SEVERAL LONG-LIVED BRANCHES.
#
# A KB is often shared by branches that are not merged into one another, and then a referent is not
# simply present or absent -- it can be CORRECT SOMEWHERE ELSE. The case this was written for: an
# implementation file renamed on one branch only, so ~21 entries naming the old path look stale
# from the new branch, and the obvious repair (rewrite them to the new name) breaks every one of
# them on the branches where the old name is the real and only name.
#
# So there are three answers, not two, and the third has to be SAID rather than folded into either
# neighbour: "missing here" and "wrong" are different findings, and a checker that conflates them
# reports false alarms on a correct KB -- which is how a check gets switched off.
#
# THE TIERS DEGRADE DIFFERENTLY PER CHECK, because they are answered by different things:
#
#   file-exists, citation-range   git. Any branch, no index, works on a fresh clone.
#   symbol-exists, symbol-live    the branch's INDEX -- and code_index.*.sqlite is generated and
#                                 normally gitignored, so a fresh clone has exactly ONE. Widening
#                                 a symbol check is therefore best-effort: where a branch has no
#                                 index we say UNRESOLVED, never "missing", and fall back to
#                                 asking git whether the name appears in that branch's file at all.
BRANCH_CACHE = {}


def git_out(args):
    try:
        return subprocess.check_output(["git", "-C", ROOT] + args,
                                       stderr=subprocess.DEVNULL).decode("utf-8", "replace")
    except Exception:
        return ""


def current_branch():
    return git_out(["rev-parse", "--abbrev-ref", "HEAD"]).strip()


def sibling_branches(limit_to=None):
    """Local branches other than the current one, newest first.

    Newest first because a rename is usually recent, so the branch that explains a referent tends
    to be near the top and the search stops early.
    """
    key = ("siblings", tuple(limit_to) if limit_to else None)
    if key in BRANCH_CACHE:
        return BRANCH_CACHE[key]
    cur = current_branch()
    out = git_out(["for-each-ref", "--sort=-committerdate", "--format=%(refname:short)",
                   "refs/heads"])
    names = [b.strip() for b in out.splitlines() if b.strip() and b.strip() != cur]
    if limit_to:
        wanted = set(limit_to)
        names = [b for b in names if b in wanted]
    BRANCH_CACHE[key] = names
    return names


def path_on_branch(branch, path):
    key = ("exists", branch, path)
    if key not in BRANCH_CACHE:
        BRANCH_CACHE[key] = subprocess.call(
            ["git", "-C", ROOT, "cat-file", "-e", "%s:%s" % (branch, path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0
    return BRANCH_CACHE[key]


def branches_holding(path, branches):
    return [b for b in branches if path_on_branch(b, path)]


def blob_line_count(branch, path):
    key = ("lines", branch, path)
    if key not in BRANCH_CACHE:
        txt = git_out(["show", "%s:%s" % (branch, path)])
        BRANCH_CACHE[key] = txt.count("\n") + (1 if txt and not txt.endswith("\n") else 0) if txt else None
    return BRANCH_CACHE[key]


def indexed_branches(branches):
    """Which of these branches have an index on disk -- the only ones a symbol check can use."""
    have = []
    for b in branches:
        slug = re.sub(r"[^A-Za-z0-9._-]+", "_", b).strip("._-")
        if os.path.exists(os.path.join(HERE, "code_index.%s.sqlite" % slug)):
            have.append((b, os.path.join(HERE, "code_index.%s.sqlite" % slug)))
    return have


def name_in_branch_file(branch, path, name):
    """Cheap fallback when a branch has no index: does that branch's copy even mention the name?

    Weaker than an index -- it cannot tell a definition from a mention -- so it is only ever used
    to downgrade a claim, never to make one.
    """
    if not path:
        return False
    txt = git_out(["show", "%s:%s" % (branch, path)])
    return bool(txt) and re.search(r"\b%s\b" % re.escape(name), txt) is not None


def _widen_symbol(name, src, siblings, sib_indexed):
    """Where else could this symbol be defined? -> (found_on, maybe_on, blind_count)

    found_on   branches whose INDEX defines it -- as good an answer as the current branch gives
    maybe_on   branches with no index whose copy of the file at least CONTAINS the name
    blind      branches that could be neither confirmed nor denied

    The split exists so a missing index never reads as a missing symbol. Widening a symbol check is
    best-effort by construction: code_index.*.sqlite is generated and normally gitignored, so a
    fresh clone has one index and the honest answer for every other branch is "unresolved".
    """
    found_on, maybe_on, blind = [], [], 0
    for b in siblings:
        db = sib_indexed.get(b)
        if db:
            try:
                con = sqlite3.connect(db)
                row = con.execute(
                    "SELECT 1 FROM symbols WHERE name = ? OR name LIKE '%::' || ? LIMIT 1",
                    (name, name)).fetchone()
                con.close()
            except Exception:
                row = None
                blind += 1
                continue
            if row:
                found_on.append(b)
        elif src and name_in_branch_file(b, src, name):
            maybe_on.append(b)
        else:
            blind += 1
    return found_on, maybe_on, blind


def branch_paths_by_basename(branch):
    """basename -> [paths] for one branch, from git. Cached: one ls-tree per branch.

    A citation names a BASENAME ("single_wire.cpp:272"), and the entry does not always list the
    file among its referents -- often it is only mentioned in prose. So the range check needs its
    own way to ask "is there a file of this name, long enough, on another branch?" without an index
    there and without the entry's help.
    """
    key = ("bybase", branch)
    if key not in BRANCH_CACHE:
        idx = {}
        for line in git_out(["ls-tree", "-r", "--name-only", branch]).splitlines():
            line = line.strip()
            if line:
                idx.setdefault(os.path.basename(line).lower(), []).append(line)
        BRANCH_CACHE[key] = idx
    return BRANCH_CACHE[key]


def citation_fits_elsewhere(base, ln, siblings):
    """Is there a copy of this basename on a sibling branch with at least `ln` lines?"""
    for b in siblings:
        for path in branch_paths_by_basename(b).get(base.lower(), []):
            n = blob_line_count(b, path)
            if n is not None and n >= ln:
                return b, path, n
    return None


def line_count(path):
    try:
        with io.open(path, "rb") as fh:
            return sum(1 for _ in fh)
    except Exception:
        return None


def last_commit_epoch(path):
    try:
        out = subprocess.check_output(
            ["git", "-C", ROOT, "log", "-1", "--format=%ct", "--", path],
            stderr=subprocess.DEVNULL).decode().strip()
        return int(out) if out else None
    except Exception:
        return None


def ts_epoch(ts):
    """KB ts is 'ddmmyy HH:MM'. Returns epoch, or None if unparseable."""
    import calendar
    import time
    m = re.match(r'^(\d{2})(\d{2})(\d{2})\s+(\d{2}):(\d{2})', ts or "")
    if not m:
        return None
    d, mo, y, hh, mm = (int(x) for x in m.groups())
    try:
        return calendar.timegm((2000 + y, mo, d, hh, mm, 0, 0, 0, 0))
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser(description="is the KB still true of the code?")
    ap.add_argument("--db")
    ap.add_argument("--no-git", action="store_true", help="skip the freshness check")
    ap.add_argument("--quiet", action="store_true", help="problems only, no REVIEW lines")
    ap.add_argument("--branches", default="current", metavar="current|all|a,b,c",
                    help="where a referent may live. 'current' (default) judges only the checked-out "
                         "branch; 'all' widens to every local branch when the current one misses, and "
                         "NAMES the branch that satisfies it; a comma-separated list restricts that "
                         "widening. Use it when one knowledge base is shared by branches that are not "
                         "merged into one another")
    ap.add_argument("--strict", action="store_true",
                    help="treat a referent that resolves only on ANOTHER branch as a problem. Off by "
                         "default because on a shared KB that is the CORRECT state, not a defect")
    a = ap.parse_args()

    if a.branches == "current":
        siblings = []
    elif a.branches == "all":
        siblings = sibling_branches()
    else:
        siblings = sibling_branches([b.strip() for b in a.branches.split(",") if b.strip()])
    sib_indexed = dict(indexed_branches(siblings))
    elsewhere = []
    unresolved = []

    global INDEX_ROOTS
    INDEX_ROOTS = load_roots()
    db = pick_db(a.db)
    resolve, by_file, index_files = load_index(db)
    ents = entries()

    # citation-dead is ADVISORY, not a problem, and that is a measured decision rather than
    # timidity. The premise is sound - the 090926 VIVIX case was a comment citing the superseded
    # line one above the live one - but symbols.commented_out means "this text sits inside a
    # comment", and that includes PROSE THAT MERELY NAMES A FUNCTION. All three hits on this KB
    # are exactly that: "see also CreateInfoTags()", "Note: ... in ProcessUserCommand()", an _AT_
    # trace note. Zero were dead code. Reported so a reader can glance at them, never counted as a
    # failure, because a check that is wrong every time is one people learn to skip - and they
    # would take the other four checks with it.
    problems = collections.OrderedDict(
        (k, []) for k in ("file-exists", "symbol-exists", "symbol-live", "citation-range"))
    advisory = []
    review = []
    checked_citations = 0
    uncheckable = 0
    fresh_cache = {}

    for path, fm, body in ents:
        rel = os.path.relpath(path, ROOT).replace("\\", "/")

        # frontmatter "file:" is sometimes "path/to/thing.cpp:416" - the line is part of the
        # provenance, not the path. The first run reported 6 files "missing" for exactly this.
        # `file:` (one) and `files:` (a list) are both in use. Every referent is checked; the
        # first is kept as `src` because the later checks want one file to anchor to -- the
        # directory a citation most likely means, and the blob to range-check against.
        raw = fm.get("files") or fm.get("file") or []
        if isinstance(raw, str):
            raw = [raw]
        refs = []
        for r in raw:
            r = (r or "").strip()
            if not r:
                continue
            m = re.match(r'^(.*?):(\d+)$', r)
            refs.append(m.group(1) if m else r)
        src = refs[0] if refs else ""
        src_here = bool(src) and os.path.exists(os.path.join(ROOT, src))
        src_on = []
        ref_on = {}            # referent -> branches that have it, for referents absent HERE
        for r in refs:
            if os.path.exists(os.path.join(ROOT, r)):
                continue
            on = branches_holding(r, siblings)
            ref_on[r] = on
            if r == src:
                src_on = on
            if on:
                # THE THIRD ANSWER. Not missing, not wrong -- correct, on a branch that is not this
                # one. Reported with the branch named so nobody "repairs" it into being wrong here.
                elsewhere.append("%s -> %s (on %s)" % (rel, r, ", ".join(on[:3])))
            else:
                problems["file-exists"].append("%s -> %s" % (rel, r))

        # ONLY A SYMBOL THE INDEX COULD SEE CAN BE REPORTED MISSING. kb.config.json names the
        # indexed roots (here xfox and xfoxcmd); the KB also holds entries about VEO's C#, about
        # build scripts (Common_build, VEOSetup) and about settings (AllowScanWithoutXray), none of
        # which this index parses. Absent from the index is then "not checkable", not "gone", and
        # saying otherwise is how a checker teaches people to ignore it - 30 of the first run's
        # hits were this.
        in_roots = bool(src) and any(
            src.replace("\\", "/").startswith(r.rstrip("/") + "/") for r in INDEX_ROOTS)

        name = fm.get("name")
        if name:
            hits = resolve(name)
            if not hits:
                if os.sep + "symbols" + os.sep in path and in_roots:
                    found_on, maybe_on, blind = _widen_symbol(name, src, siblings, sib_indexed)
                    if found_on:
                        elsewhere.append("%s -> %s (defined on %s)" % (rel, name, ", ".join(found_on[:3])))
                    elif maybe_on:
                        # git can see the NAME in that branch's file but cannot tell a definition
                        # from a mention, so this downgrades the claim rather than making one.
                        unresolved.append("%s -> %s (name present on %s, not indexed there)"
                                          % (rel, name, ", ".join(maybe_on[:3])))
                    elif blind:
                        unresolved.append("%s -> %s (%d branch(es) not indexed)" % (rel, name, blind))
                    else:
                        problems["symbol-exists"].append("%s -> %s (in %s)" % (rel, name, src))
                else:
                    uncheckable += 1
            elif all(co for (_f, _l, co, _n) in hits):
                problems["symbol-live"].append(
                    "%s -> %s (all %d definition(s) commented out)" % (rel, name, len(hits)))

        for m in CITATION.finditer(body):
            base, ln = m.group(1), int(m.group(2))
            checked_citations += 1
            # THE AUTHORITATIVE FILE MAY NOT BE ON THIS BRANCH, and then a same-named file that
            # IS here is the wrong yardstick. Measured: `single_wire.cpp:272` range-checked against
            # a 24-line Eth2Serial/single_wire.cpp shim while the real 500-line file lives at
            # wt32-eth01/main/ on the other branches -- 15 citations reported out of range, every
            # one of them correct. When the entry's own referent names this basename and resolves
            # only elsewhere, measure against THAT blob and nothing else.
            # ANY of the entry's referents may be the file this citation means -- not just the
            # first. Keying on refs[0] left 14 citations still measured against the wrong same-named
            # file, because the entry listed that file second.
            elsewhere_ref = next((r for r, on in ref_on.items()
                                  if on and os.path.basename(r) == base), None)
            if elsewhere_ref:
                n = blob_line_count(ref_on[elsewhere_ref][0], elsewhere_ref)
                if n is not None:
                    if ln > n:
                        problems["citation-range"].append(
                            "%s -> %s:%d (%s has %d lines on %s)"
                            % (rel, base, ln, elsewhere_ref, n, ref_on[elsewhere_ref][0]))
                    continue

            cands = index_files.get(base.lower())
            if not cands:
                # Not in THIS branch's index. It may still be a real file on a sibling, where the
                # citation can be range-checked from the blob without any index at all.
                if siblings and src_on:
                    n = blob_line_count(src_on[0], src) if src and os.path.basename(src) == base else None
                    if n is not None and ln > n:
                        problems["citation-range"].append(
                            "%s -> %s:%d (%s has %d lines on %s)" % (rel, base, ln, src, n, src_on[0]))
                continue                                   # file not in the indexed roots

            # A BASENAME IS NOT A FILE. This tree carries copy-paste forks - five CorrectionSet.cpp,
            # several CardDefs.h, archutil.cpp, InstallationItem.cpp - and widening the roots on
            # 090926 turned that from theory into eleven false "out of range" hits in one run,
            # because the first version took cands[0], whatever happened to list first. Globs are
            # for sets, not for "the one I want".
            #
            # The honest rule: a citation is out of range only when NO file of that name is long
            # enough. Preferring the copy in the entry's own frontmatter directory first keeps the
            # answer specific when the entry says which one it means.
            preferred = [c for c in cands if src and c.replace("\\", "/").startswith(
                os.path.dirname(src.replace("\\", "/")))]
            ordered = preferred + [c for c in cands if c not in preferred]
            lengths = [(c, line_count(os.path.join(ROOT, c))) for c in ordered]
            usable = [(c, n) for c, n in lengths if n is not None]
            if usable and all(ln > n for _c, n in usable):
                # BEFORE CALLING IT OUT OF RANGE, ASK THE OTHER BRANCHES. The same basename can be
                # a 24-line shim here and the real 900-line implementation there -- measured:
                # Eth2Serial/single_wire.cpp against wt32-eth01/main/single_wire.cpp. Six citations
                # were reported out of range against the shim, and every one of them was correct.
                fit = citation_fits_elsewhere(base, ln, siblings) if siblings else None
                if fit:
                    b, path, n = fit
                    elsewhere.append("%s -> %s:%d (fits %s, %d lines, on %s)"
                                     % (rel, base, ln, path, n, b))
                    continue
                c, n = usable[0]
                # IS THE FILE WE MEASURED PLAUSIBLY THE ONE THE CITATION MEANS? `preferred` holds
                # the copies sitting in the entry's own referent directory. With none, and with the
                # basename absent from the entry's referents, the match is a GUESS -- and a wrong
                # guess reads as a stale citation forever.
                # Measured: an entry about ESP-IDF WiFi cites esp_wifi_types.h:294, whose only
                # in-tree namesake is a 7-line stub under a vendored component's host tests. The
                # real header is in the SDK, outside the repository and outside any index. Calling
                # that "out of range" is answering a question nobody asked, and one permanent false
                # positive means a non-zero exit forever, which stops the tool being a gate.
                related = bool(preferred) or any(os.path.basename(r) == base for r in refs)
                if not related:
                    advisory.append(
                        "%s -> %s:%d not range-checked: no copy of that name belongs to this entry"
                        " (nearest in tree: %s, %d lines)" % (rel, base, ln, c, n))
                    continue
                problems["citation-range"].append(
                    "%s -> %s:%d (%s has %d lines%s)"
                    % (rel, base, ln, c, n,
                       "" if len(usable) == 1 else "; %d copies, none long enough" % len(usable)))
                continue
            # SAME BASENAME AMBIGUITY as citation-range above: by_file is keyed on the basename, so
            # without this the commented-out ProcessUserCommand in one copy of StartTaskInfo.h would
            # be reported against a citation that means another copy. Only symbols from a file long
            # enough to HAVE that line count.
            long_enough = set(c for c, n in usable if n >= ln)
            near = [(sline, co, sname) for (f, sline, co, sname) in by_file.get(base.lower(), ())
                    if abs(sline - ln) <= 2 and (not long_enough or f in long_enough)]
            if near and all(co for (_s, co, _n) in near):
                dead = near[0]
                advisory.append(
                    "%s -> %s:%d sits in a comment naming %s" % (rel, base, ln, dead[2]))

        if not a.no_git:
            t = ts_epoch(fm.get("ts"))
            if t and src:
                if src not in fresh_cache:
                    fresh_cache[src] = last_commit_epoch(src)
                c = fresh_cache[src]
                if c and c > t:
                    review.append((src, rel))

    print("\n  index   : %s" % os.path.basename(db))
    print("  entries : %d" % len(ents))
    if not ents:
        # NOT "ok". Five checks over an empty set pass trivially, and a green run on a KB nobody
        # opened is worse than a red one -- it is the same silent-success failure this tool was
        # written to find, turned on itself.
        print("  result: NO ENTRIES FOUND -- nothing was checked")
        for d in kb_dirs():
            print("      %s %s" % ("ok     " if os.path.isdir(d) else "MISSING", d))
        print("  Check `annotations` in kb.config.json. Exiting non-zero because five checks over"
              " an empty set are not a pass.")
        return 2
    print("  citations checked: %d" % checked_citations)
    print("  indexed roots: %s" % ", ".join(INDEX_ROOTS))

    total = 0
    for check, items in problems.items():
        total += len(items)
        print("\n  check: %s" % check)
        print("  result: %s" % ("ok" if not items else "PROBLEM"))
        if items:
            print("  detail: %d" % len(items))
            for s in items[:12]:
                print("      %s" % s)
            if len(items) > 12:
                print("      ... and %d more" % (len(items) - 12))

    if elsewhere:
        # STRICT MAKES IT A FAILURE, DEFAULT DOES NOT. On a knowledge base shared by unmerged
        # branches this state is CORRECT -- the entry describes code that lives on another line --
        # so counting it by default would report a healthy KB as broken. Under --strict it counts,
        # for a gate that wants one branch to be self-contained.
        print("\n  check: resolves-only-elsewhere")
        print("  result: %s" % ("PROBLEM" if a.strict else "REVIEW"))
        print("  detail: %d referent(s) do not exist here but DO on another branch" % len(elsewhere))
        for x in elsewhere[:12]:
            print("      %s" % x)
        if len(elsewhere) > 12:
            print("      ... and %d more" % (len(elsewhere) - 12))
        if a.strict:
            total += len(elsewhere)

    if unresolved and not a.quiet:
        print("\n  check: unresolved-elsewhere")
        print("  result: REVIEW")
        print("  detail: %d symbol(s) could not be confirmed on other branches "
              "(no index there -- build one to decide)" % len(unresolved))
        for x in unresolved[:8]:
            print("      %s" % x)
        if len(unresolved) > 8:
            print("      ... and %d more" % (len(unresolved) - 8))

    if not a.quiet:
        print("\n  check: freshness")
        if a.no_git:
            print("  result: skipped (--no-git)")
        else:
            print("  result: %s" % ("ok" if not review else "REVIEW"))
            # GROUPED BY FILE, not one line per entry. A busy file drags every entry that cites it
            # into the list at once - cardcommand.cpp alone accounted for a fifth of the first
            # run's 84 - and 84 undifferentiated lines is a wall, not a signal. Grouped, it reads
            # as "this file moved; these N entries have not been re-checked since", which is the
            # actual question.
            byfile = collections.Counter(src for src, _rel in review)
            print("  detail: %d entry(ies) across %d file(s) describe code that has since moved"
                  % (len(review), len(byfile)))
            for src, n in byfile.most_common(10):
                ex = [r for s, r in review if s == src][:2]
                print("      %-52s %2d entry(ies)  e.g. %s"
                      % (src, n, os.path.basename(ex[0]) if ex else ""))
            if len(byfile) > 10:
                print("      ... and %d more file(s)" % (len(byfile) - 10))

    print("\n  branches: %s" % (a.branches if siblings or a.branches != "current"
                                   else "current only (%s)" % (current_branch() or "?")))
    if siblings:
        print("  widened to: %s%s" % (", ".join(siblings[:6]),
                                      "" if len(siblings) <= 6 else " (+%d)" % (len(siblings) - 6)))
        print("  of those, indexed: %s" % (", ".join(sorted(sib_indexed)) or "none"))
    print("  %d problem(s)%s%s" % (total, "" if a.no_git else ", %d to review" % len(review),
                                   "" if not elsewhere else ", %d elsewhere" % len(elsewhere)))
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
