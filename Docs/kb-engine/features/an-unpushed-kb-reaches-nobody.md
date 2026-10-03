---
claims: [{"status": "live", "evidence": "measured", "date": "2026-10-03", "text": "A KB kept in git still reaches nobody until it is pushed, and nothing surfaced that. Measured on a real project: the `kb` branch stood 77 commits and three days ahead of its remote across 514 entry files -- every fact recorded in that window existed on exactly one disk.", "why_invisible": "the KB lives on an ORPHAN branch in a SEPARATE worktree, so its commits never appear in the code tree's `git status` and never ride along when a code branch is pushed. There was no moment at which anyone was shown that the knowledge had not left the building."}, {"status": "done", "evidence": "measured", "date": "2026-10-03", "text": "FIXED: index_code.py now reports it, in the three stages the failure actually has -- entries edited and not committed, commits not pushed, and a branch with no upstream at all. Hooked into the indexer rather than given its own command, for the reason already written beside the skills projection: a KB edit must come back through a rebuild anyway, and a check needing its own command is the command nobody runs.", "verified": "six cases: clean+pushed silent, .local skipped, non-git silent, dirty reports, no-upstream reports, unpushed reports with the oldest date."}]
evidence: measured
files:
  - .tools/index_code.py
keywords:
  - unpushed kb
  - kb not pushed
  - knowledge never reached anyone
  - kb only on one disk
  - orphan branch invisible
  - separate worktree git status
  - why did nobody see my notes
  - kb has no upstream
  - recorded but not committed
kind: feature
min_fw: all
name: an-unpushed-kb-reaches-nobody
see_also: [{"type": "file", "target": ".tools/index_code.py", "note": "report_unpublished_kb(), called at the end of main()"}]
status: resolved
---

## the failure

Putting the knowledge base in git buys exactly four things: it reaches another clone, another
machine, the mirror, and a human with no agent. **An unpushed KB has none of them.** It is in a
version-control system, which feels like safety, and it is on one disk, which is what it actually
is.

*** AND IT DRIFTS SILENTLY, BY CONSTRUCTION. *** The KB is an ORPHAN branch checked out in a
SEPARATE worktree -- that is the right design, because it stops the KB forking per code branch --
but it has a cost nobody had priced: KB commits never appear in the code tree's `git status`, and
they never ride along when a code branch is pushed. Pushing "the project" does not push the
knowledge, and nothing says so.

Measured: 77 commits, three days, 514 entry files, zero behind. Not a conflict, not a mistake -- just
nobody being told.

## the three stages, which are worth separating

A single "unpushed" check would miss the earlier and later halves of the same problem:

| stage | what it looks like | why it is its own case |
|---|---|---|
| **not committed** | entries edited, sitting in the worktree | the most recoverable and the most easily forgotten -- an engine fix sat uncommitted for a day in this very repository while its project copies were being refreshed |
| **not pushed** | committed, ahead of upstream | the 77-commit case. Feels finished, because committing feels like finishing |
| **no upstream** | a branch that cannot be pushed by habit | `git push` with no argument fails, so it never becomes routine, so it never happens at all |

## where it is checked, and why not in its own tool

In `index_code.py`, at the end of `main()`, beside the skills projection -- whose comment already
states the rule this follows:

> *Hooked here because rebuilding the index after a KB edit is already mandatory; anything needing
> its own command would be the command nobody runs.*

**It is silent unless there is something to say.** An instrument that fires on every run cannot be
read, and this one would otherwise fire on every rebuild during ordinary editing. It is also never
fatal: a git question must not cost anyone their index.

`.local` KBs are skipped by name. The suffix is the entire point of a scratch KB beside the shared
one, so reporting it as unpublished would be noise -- and noise next to a real warning is how the
real warning gets ignored.
