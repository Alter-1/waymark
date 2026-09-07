#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
xstatus - a CROSS-BRANCH, CROSS-REPOSITORY register of bugs and features.

WHY THIS EXISTS
---------------
"Is that fix in 2.0?" could only be answered by running git cherry across branches and reading 40
commit subjects, and the answer went stale the moment anyone ported anything. Worse, the interesting
answer is usually "no, and DELIBERATELY so" - which git cannot record at all. A commit's ABSENCE
looks identical whether it was never ported, was rejected on purpose, or does not apply to that line.

So the register stores the thing git cannot: per branch, per item, a STATE and a REASON.

WHERE THE DATA LIVES, and why not in the repo
---------------------------------------------
The database is OUTSIDE every checkout: $XSTATUS_DB, else ~/.waymark/xstatus.sqlite.

Two reasons, both learned the hard way:
  1. It can span SEVERAL REPOSITORIES - the case it was written for tracks an engine and its host
     application, kept in different repos. Nothing tracked inside one of them can be authoritative
     for the other.
  2. A file that must describe ALL branches cannot live ON a branch: checking out another branch
     would show you that branch's idea of the truth, which is exactly the confusion this is meant to
     end. (The knowledge base is gitignored in those trees for the related reason that a checkout or
     reset wiped it twice.)

The TOOL is tracked so it is versioned and reviewable; the DATA is shared. Back the database up
alongside the knowledge base after a session that adds to it.

STATES
------
  fixed            implemented/fixed on this branch by its own commit
  ported           the same change, brought across from another branch (records --from)
  not-implemented  absent, and it SHOULD be here - this is the work queue
  not-applicable   absent ON PURPOSE. --why is REQUIRED: a bare "not applicable" a year later is
                   indistinguishable from "nobody got round to it", which is the whole problem
  open             known, reproduced, not yet fixed anywhere
  superseded       was here, replaced by another item (records --why, ideally naming the successor)

Python 3.7 compatible. No JSON1 - this SQLite build does not have it.
"""

import argparse
import os
import sqlite3
import sys
from datetime import datetime

def _default_db():
    """Where the register lives, in order of preference.

    Deliberately NOT inside a checkout. The register describes EVERY branch and, in the case it was
    written for, two separate repositories - so it cannot live on a branch of either without showing
    you that branch's idea of a cross-branch truth. It also survives the thing that motivated
    waymark's own storage rules: a checkout or reset wiping the knowledge directory.

    XSTATUS_DB wins. Otherwise a sibling of the user's home, which is stable across checkouts on
    every platform.
    """
    env = os.environ.get("XSTATUS_DB")
    if env:
        return env
    return os.path.join(os.path.expanduser("~"), ".waymark", "xstatus.sqlite")


DEFAULT_DB = _default_db()

STATES = ("fixed", "ported", "not-implemented", "not-applicable", "open", "superseded")
KINDS = ("bug", "feature")

# A state that means "deliberately absent" must carry its reason, or the record is worthless.
STATES_REQUIRING_WHY = ("not-applicable", "superseded")

SCHEMA = """
CREATE TABLE IF NOT EXISTS branch (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,
    repo          TEXT NOT NULL,
    checkout_path TEXT,
    role          TEXT,
    frozen        INTEGER NOT NULL DEFAULT 0,
    sort_order    INTEGER NOT NULL DEFAULT 100
);

CREATE TABLE IF NOT EXISTS item (
    id         INTEGER PRIMARY KEY,
    ref        TEXT NOT NULL UNIQUE,
    kind       TEXT NOT NULL,
    title      TEXT NOT NULL,
    symptom    TEXT,
    detail     TEXT,
    kb_concept TEXT,
    created    TEXT NOT NULL,
    CHECK (kind IN ('bug','feature'))
);

CREATE TABLE IF NOT EXISTS item_status (
    item_id     INTEGER NOT NULL REFERENCES item(id) ON DELETE CASCADE,
    branch_id   INTEGER NOT NULL REFERENCES branch(id) ON DELETE CASCADE,
    state       TEXT NOT NULL,
    commit_sha  TEXT,
    commit_date TEXT,
    ported_from INTEGER REFERENCES branch(id),
    why         TEXT,
    updated     TEXT NOT NULL,
    PRIMARY KEY (item_id, branch_id),
    CHECK (state IN ('fixed','ported','not-implemented','not-applicable','open','superseded'))
);

CREATE INDEX IF NOT EXISTS ix_status_branch ON item_status(branch_id, state);
CREATE INDEX IF NOT EXISTS ix_item_kind     ON item(kind);

-- A feature is a STREAM of commits, not one. Recording only "fixed, commit X" loses the date the
-- line last moved, and that date is the only thing that can tell a COMPLETE port from a STALE one.
-- So every change to an item on a branch gets a row here, and the per-branch freshness is derived
-- (see v_freshness) rather than maintained by hand - a hand-maintained "ported" goes stale silently
-- the moment the origin gets one more commit.
CREATE TABLE IF NOT EXISTS item_change (
    id          INTEGER PRIMARY KEY,
    item_id     INTEGER NOT NULL REFERENCES item(id) ON DELETE CASCADE,
    branch_id   INTEGER NOT NULL REFERENCES branch(id) ON DELETE CASCADE,
    change_date TEXT NOT NULL,               -- YYYY-MM-DD, the COMMIT date, not the record date
    commit_sha  TEXT,
    kind        TEXT NOT NULL DEFAULT 'fix', -- fix | port | rework
    ported_from INTEGER REFERENCES branch(id),
    note        TEXT,
    -- Does this change belong on the OTHER branches?
    --   'all'         propagate - a branch that predates it is genuinely behind
    --   'branch-only' specific to this line, must NEVER be ported (e.g. a fork that drops a device family)
    --   NULL          NOT YET ASSESSED - flags for review and is the DEFAULT ON PURPOSE
    -- A newer date on one branch is NOT by itself evidence that another is behind: the fix may be
    -- branch-specific. The register therefore RAISES A QUESTION and refuses to answer it; a human
    -- resolves it with `xstatus.py scope`. Defaulting to 'all' would manufacture false gaps, and
    -- defaulting to 'branch-only' would hide real ones - so unassessed is its own visible state.
    scope       TEXT,
    CHECK (kind IN ('fix','port','rework')),
    CHECK (scope IS NULL OR scope IN ('all','branch-only'))
);

CREATE INDEX IF NOT EXISTS ix_change_item ON item_change(item_id, branch_id, change_date);

-- WHICH DIRECTIONS WE ACTUALLY PORT.
--
-- Divergence between two branches is only a QUESTION when we would ever move work between them, and
-- usually only in ONE direction. A typical shape: a customer fork feeds the mainline, the mainline
-- feeds the next major version, and nothing flows back.
--
-- Record the reverse direction as INACTIVE rather than omitting it, when it is real but deferred:
-- the intent then stays visible instead of merely absent.
--
-- Without this the review queue is symmetric and reports work nobody intends to do. Observed on the
-- first run of the tool it was written for: the mainline was flagged as behind the next-version
-- branch, because that branch had reworked a port after receiving it. True, and irrelevant.
CREATE TABLE IF NOT EXISTS port_flow (
    from_branch INTEGER NOT NULL REFERENCES branch(id) ON DELETE CASCADE,
    to_branch   INTEGER NOT NULL REFERENCES branch(id) ON DELETE CASCADE,
    active      INTEGER NOT NULL DEFAULT 1,
    note        TEXT,
    PRIMARY KEY (from_branch, to_branch)
);
"""

# Convenience views. Kept in the schema rather than in the tool so anyone poking the database with
# a plain sqlite3 client gets them too - the point is that this is queryable WITHOUT this script.
VIEWS = """
DROP VIEW IF EXISTS v_matrix;
CREATE VIEW v_matrix AS
SELECT i.ref, i.kind, i.title, b.name AS branch, s.state, s.commit_sha, s.why,
       pf.name AS ported_from
FROM item i
JOIN item_status s ON s.item_id = i.id
JOIN branch b      ON b.id = s.branch_id
LEFT JOIN branch pf ON pf.id = s.ported_from
ORDER BY i.ref, b.sort_order;

DROP VIEW IF EXISTS v_gaps;
CREATE VIEW v_gaps AS
SELECT b.name AS branch, i.ref, i.kind, i.title, i.kb_concept
FROM item i
JOIN item_status s ON s.item_id = i.id
JOIN branch b      ON b.id = s.branch_id
WHERE s.state = 'not-implemented'
ORDER BY b.sort_order, i.ref;

-- Per (item, branch): when did this line last move on this item, and how.
DROP VIEW IF EXISTS v_freshness;
CREATE VIEW v_freshness AS
SELECT c.item_id, i.ref, c.branch_id, b.name AS branch,
       MAX(c.change_date) AS last_change,
       COUNT(*)           AS changes
FROM item_change c
JOIN item i   ON i.id = c.item_id
JOIN branch b ON b.id = c.branch_id
GROUP BY c.item_id, c.branch_id;

-- THE REVIEW QUEUE, and the reason this tool exists.
--
-- A branch is flagged when another branch has a change on the same item that is NEWER than this
-- branch's last change AND is not marked branch-only. That is a QUESTION - "origin moved after you
-- ported, does it apply to you?" - not a verdict, because the newer change may be specific to the
-- line it landed on. Resolve with `xstatus.py scope <change-id> all|branch-only`.
--
-- Consequence of the model, and the point the author made: once a branch RE-WORKS something it
-- received, it holds the newest date and every other branch - including the one it was ported from -
-- shows here. Primary follows the work, not the history.
DROP VIEW IF EXISTS v_review;
CREATE VIEW v_review AS
SELECT behind.ref,
       behind.branch          AS behind_branch,
       behind.last_change     AS behind_since,
       ahead.branch           AS ahead_branch,
       ahead.last_change      AS ahead_at,
       c.id                   AS change_id,
       c.commit_sha,
       COALESCE(c.scope,'UNASSESSED') AS scope,
       c.note
FROM v_freshness behind
JOIN v_freshness ahead ON ahead.item_id = behind.item_id
                      AND ahead.branch_id <> behind.branch_id
                      AND ahead.last_change > behind.last_change
JOIN item_change c ON c.item_id = ahead.item_id
                  AND c.branch_id = ahead.branch_id
                  AND c.change_date > behind.last_change
                  AND (c.scope IS NULL OR c.scope = 'all')
-- only directions we actually port, and never into a frozen line
JOIN port_flow f ON f.from_branch = ahead.branch_id
                AND f.to_branch   = behind.branch_id
                AND f.active = 1
JOIN branch bb ON bb.id = behind.branch_id AND bb.frozen = 0
ORDER BY behind.ref, behind.branch;

-- An item with NO row for a branch is not the same as 'not-implemented': it means nobody has
-- assessed it there. That is worth seeing separately, because it is the silent case.
DROP VIEW IF EXISTS v_unassessed;
CREATE VIEW v_unassessed AS
SELECT b.name AS branch, i.ref, i.kind, i.title
FROM item i
CROSS JOIN branch b
WHERE NOT EXISTS (SELECT 1 FROM item_status s WHERE s.item_id = i.id AND s.branch_id = b.id)
ORDER BY b.sort_order, i.ref;
"""


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def connect(path, create=False):
    if not create and not os.path.exists(path):
        sys.stderr.write("database not found: %s\n  run:  xstatus.py init\n" % path)
        sys.exit(2)
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def branch_id(con, name):
    r = con.execute("SELECT id FROM branch WHERE name = ?", (name,)).fetchone()
    if r is None:
        sys.stderr.write("unknown branch: %s\n  known: %s\n"
                         % (name, ", ".join(x["name"] for x in
                                            con.execute("SELECT name FROM branch ORDER BY sort_order"))))
        sys.exit(2)
    return r["id"]


def item_id(con, ref):
    r = con.execute("SELECT id FROM item WHERE ref = ?", (ref,)).fetchone()
    if r is None:
        sys.stderr.write("unknown item: %s\n" % ref)
        sys.exit(2)
    return r["id"]


def cmd_init(con, a):
    con.executescript(SCHEMA)
    con.executescript(VIEWS)
    con.commit()
    print("initialised %s" % a.db)


def cmd_branch_add(con, a):
    con.execute("""INSERT OR REPLACE INTO branch (id, name, repo, checkout_path, role, frozen, sort_order)
                   VALUES ((SELECT id FROM branch WHERE name = ?), ?, ?, ?, ?, ?, ?)""",
                (a.name, a.name, a.repo, a.path, a.role, 1 if a.frozen else 0, a.order))
    con.commit()
    print("branch %s" % a.name)


def cmd_item_add(con, a):
    con.execute("""INSERT OR REPLACE INTO item (id, ref, kind, title, symptom, detail, kb_concept, created)
                   VALUES ((SELECT id FROM item WHERE ref = ?), ?, ?, ?, ?, ?, ?,
                           COALESCE((SELECT created FROM item WHERE ref = ?), ?))""",
                (a.ref, a.ref, a.kind, a.title, a.symptom, a.detail, a.kb, a.ref, now()))
    con.commit()
    print("item %s" % a.ref)


def cmd_set(con, a):
    if a.state in STATES_REQUIRING_WHY and not a.why:
        sys.stderr.write("state '%s' requires --why: a deliberate absence with no reason recorded is\n"
                         "indistinguishable from an oversight, which defeats the purpose.\n" % a.state)
        sys.exit(2)
    pf = branch_id(con, a.frm) if a.frm else None
    if a.state == "ported" and pf is None:
        sys.stderr.write("state 'ported' requires --from <branch>\n")
        sys.exit(2)
    con.execute("""INSERT OR REPLACE INTO item_status
                   (item_id, branch_id, state, commit_sha, commit_date, ported_from, why, updated)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (item_id(con, a.ref), branch_id(con, a.branch), a.state,
                 a.commit, a.date, pf, a.why, now()))
    con.commit()
    print("%s @ %s -> %s" % (a.ref, a.branch, a.state))


def cmd_show(con, a):
    it = con.execute("SELECT * FROM item WHERE ref = ?", (a.ref,)).fetchone()
    if it is None:
        sys.stderr.write("unknown item: %s\n" % a.ref)
        sys.exit(2)
    print("%s  [%s]  %s" % (it["ref"], it["kind"], it["title"]))
    for f in ("symptom", "detail", "kb_concept"):
        if it[f]:
            print("  %-10s %s" % (f + ":", it[f]))
    print("")
    rows = con.execute("""SELECT b.name, s.state, s.commit_sha, s.why, pf.name AS pfrom
                          FROM item_status s JOIN branch b ON b.id = s.branch_id
                          LEFT JOIN branch pf ON pf.id = s.ported_from
                          WHERE s.item_id = ? ORDER BY b.sort_order""", (it["id"],)).fetchall()
    for r in rows:
        extra = ""
        if r["pfrom"]:
            extra += "  (from %s)" % r["pfrom"]
        if r["commit_sha"]:
            extra += "  %s" % r["commit_sha"]
        print("  %-14s %-16s%s" % (r["name"], r["state"], extra))
        if r["why"]:
            print("  %-14s   why: %s" % ("", r["why"]))
    unassessed = con.execute("""SELECT b.name FROM branch b WHERE NOT EXISTS
                                (SELECT 1 FROM item_status s WHERE s.item_id=? AND s.branch_id=b.id)
                                ORDER BY b.sort_order""", (it["id"],)).fetchall()
    if unassessed:
        print("  %-14s %s" % ("(unassessed)", ", ".join(u["name"] for u in unassessed)))


def cmd_matrix(con, a):
    branches = con.execute("SELECT * FROM branch ORDER BY sort_order").fetchall()
    q = "SELECT * FROM item"
    args = []
    if a.kind:
        q += " WHERE kind = ?"
        args.append(a.kind)
    q += " ORDER BY ref"
    items = con.execute(q, args).fetchall()
    abbr = {"fixed": "fix", "ported": "port", "not-implemented": "MISSING",
            "not-applicable": "n/a", "open": "open", "superseded": "sup"}
    w = max([len(i["ref"]) for i in items] + [4])
    hdr = "  %-*s  " % (w, "item") + "  ".join("%-9s" % b["name"][:9] for b in branches)
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for it in items:
        cells = []
        for b in branches:
            r = con.execute("SELECT state FROM item_status WHERE item_id=? AND branch_id=?",
                            (it["id"], b["id"])).fetchone()
            cells.append("%-9s" % (abbr.get(r["state"], r["state"])[:9] if r else "-"))
        print("  %-*s  %s" % (w, it["ref"], "  ".join(cells)))
    print("")
    print("  fix=fixed here  port=ported in  MISSING=should be here, is not  n/a=deliberately absent")
    print("  -=not assessed on that branch (which is NOT the same as missing)")


def cmd_gaps(con, a):
    q = "SELECT * FROM v_gaps"
    args = []
    if a.branch:
        q += " WHERE branch = ?"
        args.append(a.branch)
    rows = con.execute(q, args).fetchall()
    if not rows:
        print("  no recorded gaps")
        return
    # A gap on a FROZEN line is real but BLOCKED - it is not work anyone can pick up today, and
    # listing it beside actionable work invites someone to start it. Say which is which.
    frozen = set(x["name"] for x in con.execute("SELECT name FROM branch WHERE frozen = 1"))
    cur = None
    for r in rows:
        if r["branch"] != cur:
            cur = r["branch"]
            if cur in frozen:
                print("\n  %s   -- FROZEN: BLOCKED, not queued (nothing ports in until the freeze lifts)"
                      % cur)
            else:
                print("\n  %s" % cur)
        print("    %-22s %s%s" % (r["ref"], r["title"],
                                  ("   [KB %s]" % r["kb_concept"]) if r["kb_concept"] else ""))


def cmd_export(con, a):
    out = []
    out.append("# Cross-branch bug and feature register\n")
    out.append("Generated %s from `%s`. Do not edit by hand - edit the database.\n" % (now(), a.db))
    branches = con.execute("SELECT * FROM branch ORDER BY sort_order").fetchall()
    out.append("\n## Branches\n")
    out.append("| branch | repo | role | checkout | |")
    out.append("|---|---|---|---|---|")
    for b in branches:
        out.append("| `%s` | %s | %s | `%s` | %s |"
                   % (b["name"], b["repo"], b["role"] or "", b["checkout_path"] or "",
                      "**FROZEN**" if b["frozen"] else ""))
    for kind in KINDS:
        items = con.execute("SELECT * FROM item WHERE kind = ? ORDER BY ref", (kind,)).fetchall()
        if not items:
            continue
        out.append("\n## %ss\n" % kind.capitalize())
        for it in items:
            out.append("\n### `%s` -- %s\n" % (it["ref"], it["title"]))
            if it["symptom"]:
                out.append("**Symptom.** %s\n" % it["symptom"])
            if it["detail"]:
                out.append("%s\n" % it["detail"])
            if it["kb_concept"]:
                out.append("KB: `%s`\n" % it["kb_concept"])
            out.append("| branch | state | commit | note |")
            out.append("|---|---|---|---|")
            for b in branches:
                r = con.execute("""SELECT s.*, pf.name AS pfrom FROM item_status s
                                   LEFT JOIN branch pf ON pf.id = s.ported_from
                                   WHERE s.item_id=? AND s.branch_id=?""",
                                (it["id"], b["id"])).fetchone()
                if r is None:
                    out.append("| `%s` | _not assessed_ | | |" % b["name"])
                else:
                    note = r["why"] or ""
                    if r["pfrom"]:
                        note = ("ported from %s. " % r["pfrom"]) + note
                    out.append("| `%s` | **%s** | %s | %s |"
                               % (b["name"], r["state"], r["commit_sha"] or "", note))
    text = "\n".join(out) + "\n"
    if a.out:
        f = open(a.out, "w")
        try:
            f.write(text)
        finally:
            f.close()
        print("written %s" % a.out)
    else:
        sys.stdout.write(text)


def cmd_flow_add(con, a):
    f = branch_id(con, a.frm)
    t = branch_id(con, a.to)
    tb = con.execute("SELECT frozen FROM branch WHERE id = ?", (t,)).fetchone()
    if tb["frozen"] and a.active:
        sys.stderr.write("target branch '%s' is FROZEN - nothing ports into it.\n"
                         "Clear the freeze first if that has changed.\n" % a.to)
        sys.exit(2)
    con.execute("""INSERT OR REPLACE INTO port_flow (from_branch, to_branch, active, note)
                   VALUES (?,?,?,?)""", (f, t, 1 if a.active else 0, a.note))
    con.commit()
    print("flow %s -> %s  %s" % (a.frm, a.to, "active" if a.active else "INACTIVE"))


def cmd_flows(con, a):
    rows = con.execute("""SELECT fb.name AS f, tb.name AS t, pf.active, pf.note
                          FROM port_flow pf
                          JOIN branch fb ON fb.id = pf.from_branch
                          JOIN branch tb ON tb.id = pf.to_branch
                          ORDER BY fb.sort_order, tb.sort_order""").fetchall()
    if not rows:
        print("  no port flows recorded - the review queue will be empty")
        return
    for r in rows:
        print("  %-12s -> %-12s %-9s %s" % (r["f"], r["t"],
                                            "active" if r["active"] else "inactive", r["note"] or ""))
    frozen = con.execute("SELECT name FROM branch WHERE frozen = 1 ORDER BY sort_order").fetchall()
    if frozen:
        print("")
        print("  frozen (nothing ports in): %s" % ", ".join(x["name"] for x in frozen))


def cmd_change_add(con, a):
    pf = branch_id(con, a.frm) if a.frm else None
    if a.kind == "port" and pf is None:
        sys.stderr.write("kind 'port' requires --from <branch>\n")
        sys.exit(2)
    con.execute("""INSERT INTO item_change
                   (item_id, branch_id, change_date, commit_sha, kind, ported_from, note, scope)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (item_id(con, a.ref), branch_id(con, a.branch), a.date, a.commit,
                 a.kind, pf, a.note, a.scope))
    con.commit()
    print("%s @ %s  %s  %s%s" % (a.ref, a.branch, a.date, a.kind,
                                 "" if a.scope else "   [scope UNASSESSED - resolve it]"))


def cmd_scope(con, a):
    r = con.execute("SELECT * FROM item_change WHERE id = ?", (a.change_id,)).fetchone()
    if r is None:
        sys.stderr.write("no such change id: %s\n" % a.change_id)
        sys.exit(2)
    con.execute("UPDATE item_change SET scope = ?, note = COALESCE(?, note) WHERE id = ?",
                (a.scope, a.why, a.change_id))
    con.commit()
    print("change %s -> scope %s" % (a.change_id, a.scope))


def cmd_review(con, a):
    rows = con.execute("SELECT * FROM v_review").fetchall()
    if not rows:
        print("  nothing to review")
        return
    cur = None
    for r in rows:
        key = (r["ref"], r["behind_branch"])
        if key != cur:
            cur = key
            print("\n  %s  ->  %s is at %s" % (r["ref"], r["behind_branch"], r["behind_since"]))
        print("      %s advanced %s  change #%-4s %-10s %s  %s"
              % (r["ahead_branch"], r["ahead_at"], r["change_id"], r["scope"],
                 (r["commit_sha"] or "")[:10], r["note"] or ""))
    print("")
    print("  These are QUESTIONS, not gaps. A newer change elsewhere may be branch-specific.")
    print("  Resolve each:  xstatus.py scope <change-id> all|branch-only --why \"...\"")
    print("  'all' keeps it flagged until the behind branch records its own change;")
    print("  'branch-only' retires it permanently.")


def cmd_msg(con, a):
    """Emit the canonical commit subject/trailer for work on a register item.

    The point is not tidiness. A commit that carries [REF] can be found again by `scan`, so the
    register is REBUILDABLE FROM GIT instead of being a parallel record that silently drifts. It also
    makes a port searchable by what it IS rather than by whichever wording the porter chose that day
    - the reason "is that fix in 2.0?" was unanswerable in the first place.
    """
    it = con.execute("SELECT * FROM item WHERE ref = ?", (a.ref,)).fetchone()
    if it is None:
        sys.stderr.write("unknown item: %s\n" % a.ref)
        sys.exit(2)
    if a.frm:
        branch_id(con, a.frm)                      # validate
        subject = "port(%s->%s): [%s] %s" % (a.frm, a.branch, it["ref"], it["title"])
    else:
        subject = "[%s] %s" % (it["ref"], it["title"])
    print(subject[:100])
    print("")
    if it["symptom"]:
        print("Symptom: %s" % it["symptom"])
        print("")
    print("xstatus: %s" % it["ref"])
    if a.frm:
        src = con.execute("""SELECT commit_sha, change_date FROM item_change c
                             JOIN branch b ON b.id = c.branch_id
                             WHERE c.item_id = ? AND b.name = ?
                             ORDER BY change_date DESC LIMIT 1""",
                          (it["id"], a.frm)).fetchone()
        if src:
            print("Ported-From: %s %s (%s)" % (a.frm, src["commit_sha"] or "", src["change_date"]))
        else:
            print("Ported-From: %s" % a.frm)
    if it["kb_concept"]:
        print("KB: %s" % it["kb_concept"])


def cmd_scan(con, a):
    """Populate the register from git history by looking for [REF] in commit subjects.

    Read-only against git. Records a change per matching commit, using the COMMIT date - not today -
    so freshness comparisons mean what they say. Existing rows for the same (item, branch, sha) are
    left alone, so this is safe to re-run.
    """
    import subprocess
    bid = branch_id(con, a.branch)
    refs = dict((r["ref"], r["id"]) for r in con.execute("SELECT id, ref FROM item"))
    if not refs:
        print("  no items to look for")
        return
    cmd = ["git", "-C", a.repo, "log", "--no-merges", "--format=%H%x01%ad%x01%s", "--date=short"]
    if a.since:
        cmd.append("--since=%s" % a.since)
    cmd.append(a.rev)
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
    except Exception as e:
        sys.stderr.write("git failed: %s\n" % e)
        sys.exit(2)
    try:
        out = out.decode("utf-8", "replace")
    except AttributeError:
        pass
    added = 0
    seen = 0
    for line in out.splitlines():
        parts = line.split("\x01")
        if len(parts) != 3:
            continue
        sha, date, subj = parts
        for ref, iid in refs.items():
            if ("[%s]" % ref) not in subj:
                continue
            seen += 1
            dup = con.execute("""SELECT 1 FROM item_change
                                 WHERE item_id=? AND branch_id=? AND commit_sha=?""",
                              (iid, bid, sha[:12])).fetchone()
            if dup:
                continue
            kind = "port" if subj.startswith("port(") else "fix"
            con.execute("""INSERT INTO item_change
                           (item_id, branch_id, change_date, commit_sha, kind, note)
                           VALUES (?,?,?,?,?,?)""",
                        (iid, bid, date, sha[:12], kind, subj[:200]))
            added += 1
    con.commit()
    print("  %d tagged commit(s) seen, %d new change(s) recorded on %s" % (seen, added, a.branch))
    if added:
        print("  scopes are UNASSESSED - run:  xstatus.py review")


def cmd_kb_mirror(con, a):
    """Write one KB concept entry per item into a checkout's Docs/kb/concepts/.

    The register is AUTHORITATIVE and lives outside every repo; this is a READ-ONLY MIRROR so that
    the normal `query_code_index.py concept` path finds cross-branch status without anyone having to
    know the register exists. Same pattern as updater/mirror_memories_to_kb.py.

    The mirror is per-checkout and therefore shows that checkout's view of a cross-branch truth -
    which is fine for LOOKUP but must never be edited: edit the database and re-mirror. Every file
    written says so in its provenance block.
    """
    d = os.path.join(a.kb, "concepts")
    if not os.path.isdir(d):
        sys.stderr.write("no such KB concepts dir: %s\n" % d)
        sys.exit(2)
    branches = con.execute("SELECT * FROM branch ORDER BY sort_order").fetchall()
    n = 0
    for it in con.execute("SELECT * FROM item ORDER BY ref"):
        lines = []
        lines.append("---")
        lines.append("concept_id: xstatus.%s" % it["ref"].lower())
        lines.append("kind: %s" % ("defect" if it["kind"] == "bug" else "feature"))
        lines.append("name: %s" % _yaml_scalar(it["title"]))
        lines.append("status: open")
        lines.append("evidence: mixed")
        # keywords must be a LIST, not a string: the indexer iterates the value, so a plain scalar
        # is stored one CHARACTER per keyword. Visible in older entries as "D, E, ' ', i, m, a, g, e".
        kws = [k.strip() for k in (it["symptom"] or "").replace(";", ",").split(",") if k.strip()]
        kws = [k for k in kws if len(k) > 2][:8]
        if kws:
            lines.append("keywords:")
            for k in kws:
                lines.append("  - %s" % _yaml_scalar(k))
        lines.append("---")
        lines.append("")
        lines.append("## meaning")
        lines.append("")
        if it["symptom"]:
            lines.append("**Symptom.** %s" % it["symptom"])
            lines.append("")
        if it["detail"]:
            lines.append(it["detail"])
            lines.append("")
        lines.append("## per-branch status")
        lines.append("")
        lines.append("| branch | state | commit | note |")
        lines.append("|---|---|---|---|")
        for b in branches:
            r = con.execute("""SELECT s.*, pf.name AS pfrom FROM item_status s
                               LEFT JOIN branch pf ON pf.id = s.ported_from
                               WHERE s.item_id=? AND s.branch_id=?""",
                            (it["id"], b["id"])).fetchone()
            if r is None:
                lines.append("| `%s` | _not assessed_ | | |" % b["name"])
            else:
                note = r["why"] or ""
                if r["pfrom"]:
                    note = ("ported from %s. " % r["pfrom"]) + note
                lines.append("| `%s` | **%s** | %s | %s |"
                             % (b["name"], r["state"], r["commit_sha"] or "", note))
        lines.append("")
        lines.append("> _not assessed_ is NOT the same as absent - it means nobody has checked that")
        lines.append("> branch. Absence is only a finding when it is recorded as one.")
        lines.append("")
        if it["kb_concept"]:
            lines.append("Related: `%s`" % it["kb_concept"])
            lines.append("")
        lines.append("## provenance")
        lines.append("")
        lines.append("MIRRORED from the cross-branch register (`xstatus`, item `%s`)." % it["ref"])
        lines.append("The register spans two repositories and every branch, so it cannot live on a")
        lines.append("branch; this copy is for LOOKUP ONLY. **Do not edit it** - edit the register and")
        lines.append("re-run `xstatus.py kb-mirror`, or your change is lost on the next mirror.")
        p = os.path.join(d, "xstatus__%s.md" % it["ref"].lower().replace("/", "_"))
        f = open(p, "w")
        try:
            f.write("\n".join(lines) + "\n")
        finally:
            f.close()
        n += 1
    print("mirrored %d item(s) into %s" % (n, d))
    print("now run:  python .tools/index_code.py && python .tools/query_code_index.py selftest")


def _yaml_scalar(s):
    """Quote a frontmatter scalar only when it needs it, and never emit a raw colon-space."""
    s = (s or "").replace("\n", " ").strip()
    if not s:
        return '""'
    if any(c in s for c in ':#{}[]&*!|>%@`"') or s[0] in "-? ":
        return '"%s"' % s.replace('"', "'")
    return s


def cmd_sql(con, a):
    for row in con.execute(a.query):
        print(" | ".join("" if v is None else str(v) for v in row))


def main():
    p = argparse.ArgumentParser(description="cross-branch bug/feature register")
    p.add_argument("--db", default=DEFAULT_DB)
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("init").set_defaults(fn=cmd_init)

    b = sub.add_parser("branch-add"); b.set_defaults(fn=cmd_branch_add)
    b.add_argument("name"); b.add_argument("--repo", required=True)
    b.add_argument("--path"); b.add_argument("--role")
    b.add_argument("--frozen", action="store_true")
    b.add_argument("--order", type=int, default=100)

    i = sub.add_parser("item-add"); i.set_defaults(fn=cmd_item_add)
    i.add_argument("ref"); i.add_argument("--kind", required=True, choices=KINDS)
    i.add_argument("--title", required=True)
    i.add_argument("--symptom"); i.add_argument("--detail"); i.add_argument("--kb")

    s = sub.add_parser("set"); s.set_defaults(fn=cmd_set)
    s.add_argument("ref"); s.add_argument("branch"); s.add_argument("state", choices=STATES)
    s.add_argument("--commit"); s.add_argument("--date")
    s.add_argument("--from", dest="frm"); s.add_argument("--why")

    sh = sub.add_parser("show"); sh.set_defaults(fn=cmd_show); sh.add_argument("ref")

    m = sub.add_parser("matrix"); m.set_defaults(fn=cmd_matrix)
    m.add_argument("--kind", choices=KINDS)

    g = sub.add_parser("gaps"); g.set_defaults(fn=cmd_gaps); g.add_argument("--branch")

    e = sub.add_parser("export"); e.set_defaults(fn=cmd_export); e.add_argument("--out")

    fl = sub.add_parser("flow-add"); fl.set_defaults(fn=cmd_flow_add)
    fl.add_argument("--from", dest="frm", required=True); fl.add_argument("--to", required=True)
    fl.add_argument("--note")
    fl.add_argument("--inactive", dest="active", action="store_false", default=True)

    fs = sub.add_parser("flows"); fs.set_defaults(fn=cmd_flows)

    c = sub.add_parser("change-add"); c.set_defaults(fn=cmd_change_add)
    c.add_argument("ref"); c.add_argument("branch")
    c.add_argument("--date", required=True, help="COMMIT date YYYY-MM-DD, not today")
    c.add_argument("--commit"); c.add_argument("--note")
    c.add_argument("--kind", default="fix", choices=("fix", "port", "rework"))
    c.add_argument("--from", dest="frm")
    c.add_argument("--scope", choices=("all", "branch-only"),
                   help="omit to leave UNASSESSED, which is what puts it in the review queue")

    sc = sub.add_parser("scope"); sc.set_defaults(fn=cmd_scope)
    sc.add_argument("change_id", type=int); sc.add_argument("scope", choices=("all", "branch-only"))
    sc.add_argument("--why")

    rv = sub.add_parser("review"); rv.set_defaults(fn=cmd_review)

    ms = sub.add_parser("msg"); ms.set_defaults(fn=cmd_msg)
    ms.add_argument("ref"); ms.add_argument("--branch", default="")
    ms.add_argument("--from", dest="frm", help="source branch - emits a port(...) subject")

    sn = sub.add_parser("scan"); sn.set_defaults(fn=cmd_scan)
    sn.add_argument("branch"); sn.add_argument("--repo", required=True)
    sn.add_argument("--rev", default="HEAD"); sn.add_argument("--since")

    k = sub.add_parser("kb-mirror"); k.set_defaults(fn=cmd_kb_mirror)
    k.add_argument("--kb", default="Docs/kb", help="path to a checkout's Docs/kb")

    q = sub.add_parser("sql"); q.set_defaults(fn=cmd_sql); q.add_argument("query")

    a = p.parse_args()
    if not getattr(a, "fn", None):
        p.print_help()
        return 1
    con = connect(a.db, create=(a.cmd == "init"))
    try:
        a.fn(con, a)
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
