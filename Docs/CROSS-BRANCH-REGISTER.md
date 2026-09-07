# The cross-branch register (`xstatus`)

A record of which bugs and features exist on which branches, and **why** — for codebases maintained
as several long-lived parallel lines.

It answers the one question git cannot: *"is that fix in the other branch?"*

---

## The problem it exists for

If your branches are short-lived, `git cherry` answers this and you do not need any of what follows.

It stops working when branches are **long-lived and diverged**, because ports then have to be
**hand-adapted**. `git cherry` and `git log --cherry-pick` match on **patch id**. An adapted port has
a different patch id, so they cannot see it.

Measured on the codebase this was written for, where a survey found 61 of 66 candidate ports
conflict:

* `git cherry` reported **all six** candidate fixes as absent from a branch. Reading the code found
  **all six present**.
* It also missed **nine** real ports into another branch, each of which had landed and been adapted.

Wrong in both directions. The false-*absent* direction wastes a day; the false-*present* direction
ships a gap.

And there is a second problem that no diff tool addresses at all:

> **A commit's absence looks identical whether it was never ported, was rejected deliberately, or
> does not apply to that line.**

That distinction is the most valuable thing anyone learns while doing a port, and git has nowhere to
put it. A year later "not there" is indistinguishable from "we decided against it", so somebody
re-derives the decision — or worse, re-does it.

## What it stores

SQLite. Plain SQL with views, so it is queryable from any `sqlite3` client without this script.

* **`item`** — a bug or feature, keyed on the **symptom**, with an optional link to a KB concept.
* **`branch`** — the lines, each with its repository, checkout path, and a `frozen` flag.
* **`item_status`** — per item per branch: `fixed`, `ported` (records `--from`), `not-implemented`,
  `not-applicable`, `open`, `superseded`.
* **`item_change`** — every commit touching an item on a branch, with its **commit date**.
* **`port_flow`** — which directions you actually port, and which way round.

### Three decisions worth understanding

**"Not assessed" is not "absent".** An item with no row for a branch shows as `-`, never as a gap.
Absence is only a finding when somebody recorded it as one. `v_unassessed` lists what nobody has
looked at, because that is the silent case.

**`not-applicable` and `superseded` refuse to save without `--why`.** A deliberate absence with no
reason is indistinguishable from an oversight, which is the whole problem restated.

**Freshness is derived, not maintained.** A feature is a *stream* of commits, so recording "fixed,
commit X" loses the date the line last moved — and that date is the only thing separating a complete
port from a stale one. Whichever branch holds the newest change is primary; a branch whose last
change predates it is behind. A branch that is ported to and then reworks the code becomes primary,
and the branch it came from flips to behind.

## What it will not do

**It will not decide for you.** A newer change on one branch may be branch-specific and never
intended to travel. So divergence raises a *question* and the tool refuses to answer it:
`item_change.scope` is `all`, `branch-only`, or `NULL`, and **`NULL` is the default on purpose** —
defaulting to `all` manufactures false gaps, defaulting to `branch-only` hides real ones. Unassessed
is its own visible state and sits in `review` until a human rules on it.

## Using it

    xstatus.py init
    xstatus.py branch-add master  --repo app --path /src/app --role "1.x line"
    xstatus.py branch-add next    --repo app --path /src/next --role "2.x line"
    xstatus.py flow-add --from master --to next --note "1.x -> 2.x convergence"

    xstatus.py item-add QUICKSAVE-CRASH --kind bug \
        --title "QuickSave crashed and reported success on an existing record" \
        --symptom "save reports success but writes nothing" --kb app.save.paths
    xstatus.py set QUICKSAVE-CRASH master fixed --commit e4cdad56 --date 2026-08-21
    xstatus.py set QUICKSAVE-CRASH next not-implemented --why "verified by content: guard absent"

    xstatus.py matrix      # the grid
    xstatus.py gaps        # what is owed, with frozen lines marked BLOCKED
    xstatus.py review      # divergences awaiting a human ruling
    xstatus.py flows       # which directions are live

### Commit messages, and why they matter here

    xstatus.py msg QUICKSAVE-CRASH --branch next --from master

emits

    port(master->next): [QUICKSAVE-CRASH] QuickSave crashed and reported success ...
    xstatus: QUICKSAVE-CRASH
    Ported-From: master e4cdad56 (2026-08-21)

This is not tidiness. `xstatus.py scan <branch> --repo <path>` reads `[REF]` back out of `git log`,
so **the register is rebuildable from git** rather than a parallel record that drifts. It also makes
a port findable by *what it is* instead of by whichever wording the porter chose that day — which is
how the question became unanswerable in the first place.

### With the knowledge base

    xstatus.py kb-mirror --kb Docs/kb

writes one read-only concept per item, so `query_code_index.py concept "<symptom>"` finds
cross-branch status without the reader knowing the register exists. Mirrored files say do-not-edit
and name the register; edit the database and re-mirror.

## Where the database goes

Outside every checkout. `XSTATUS_DB` if set, otherwise `~/.waymark/xstatus.sqlite`.

A file describing all branches must not live on a branch — checking out another would show you that
branch's version of a cross-branch truth. Back it up with the knowledge base.

## Companion: `verify_port.py`

Answers "is this commit's substance on that branch?" by content rather than patch id: it greps the
target tree for tokens the commit **introduced**.

That last word is the design. An earlier version scored tokens without checking whether they predated
the commit, and reported a fix that merely **adds a null guard** around an existing identifier as
*present* on a branch that lacks the guard entirely — because the identifier is obviously there
either way. Every guard-or-wrap fix failed the same way, and the failure direction hides real gaps.
Tokens are now rejected if they exist in `sha^`.

The honest cost is coverage: it returns *nothing testable* for many commits rather than guessing.
That is the correct failure mode. It is **triage, not a verdict** — `partial` is the interesting
column and always needs reading.
