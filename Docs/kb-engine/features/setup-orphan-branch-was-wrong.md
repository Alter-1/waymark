---
concept_id: doc.setup.orphan_branch_command
name: setup-orphan-branch-was-wrong
status: resolved
kind: feature
evidence: measured -- reproduced on a real repo 2026-09-03, and the fix run end to end on git 2.25.1
keywords:
  - could not migrate KB
  - migration failed
  - orphan branch
  - git branch kb
  - kb branch carries the whole codebase
  - worktree checked out the code tree
  - empty directory not committed
  - nothing to commit
  - windows
  - kb branch maintenance
see_also:
  - file:SETUP.md
  - file:README.md
---

## brief

*** SETUP.md TOLD PEOPLE TO CREATE THE KB BRANCH WITH `git branch kb`, CALLING IT AN ORPHAN. IT IS
NOT ONE. *** `git branch` makes an ordinary branch at the current HEAD, so the KB branch carried the
whole codebase and shared history with it -- which is the exact problem the arrangement exists to
avoid. Fixed 2026-09-03 (911af78).

## notes

REPORTED as "could not migrate the KB correctly" from a Windows host. The cause was not Windows.

MEASURED on a real project: the branch `git branch kb` produces shares history with master and holds
**5342 files**; the genuine orphan holds **332**. `git worktree add` then checks the entire code tree
into the KB folder, and merges become possible again.

README.md had the correct sequence -- `git checkout --orphan` / `git rm -rf .` / commit -- all along.
**The two documents disagreed, and the wrong one is the one people follow when migrating.** When the
same procedure appears twice, they will drift; the one being followed is the one to trust least.

## The second fault, found only by RUNNING it

Both files then said `mkdir features concepts` followed by `git add . && git commit`. **Git does not
track empty directories**, so that stages nothing, the commit fails, and no branch is created. Both
now write `kb.json` first.

`kb.json` is NOT required by the engine -- a directory of entries indexes without it, checked both
ways 2026-09-03. It is the manifest, and here it exists to give the branch a first commit.

## Guesses that were REFUTED -- do not re-derive them

Two plausible Windows-specific explanations were tested first and both are wrong:

* **CRLF line endings breaking the frontmatter parser.** Built an entry with `\r\n` throughout: it
  parses identically to the LF one, status and evidence and qualifier all correct.
* **`git worktree add <path> kb` failing on a fresh clone** where `kb` exists only as `origin/kb`.
  Git DWIMs the local tracking branch; verified on 2.25.1.

## What was missing entirely: maintenance

The document covered creating the branch and committing a note, and stopped. Added: first publish
with `push -u origin kb` and plain `push` after; `pull` before writing; how the KB arrives on a
fresh clone; and `worktree list` / `remove` / `prune`, which is precisely what a half-finished
migration leaves behind and what makes the second attempt fail at the same path.

Also two verification commands, because "it looked like it worked" is how this shipped:

    git merge-base kb main                  # must print NOTHING
    git ls-tree -r --name-only kb | wc -l   # your notes, not your codebase

NOTE `git checkout kb` in the code tree is refused by git while the worktree exists ("already
checked out"), so the exposure is before the worktree is created or after `worktree remove`. The
document says so rather than implying the command is always dangerous.
