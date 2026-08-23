---
concept_id: engine.test.glob_index_zero
evidence: measured
keywords:
  - sorted glob
  - glob[0]
  - wrong database
  - wrong index
  - test passes for the wrong reason
  - false failure
  - inode unchanged
  - code_index
  - per-branch index
  - flaky
kind: feature
name: sorted-glob-index-zero
status: resolved
---

## brief

`sorted(TOOLS.glob("code_index*.sqlite"))[0]` PICKS A DATABASE AT RANDOM as far as intent goes --
alphabetically first, which is whichever branch happens to sort first. The index is PER BRANCH, so
`.tools/` accumulates one database per branch ever built there. Hit TWICE in one day, in opposite
directions, by two different people.

## notes

DIRECTION ONE -- A FALSE FAILURE. The "a rebuild REPLACES the index file rather than mutating it in
place" check watched the alphabetically first index, which for any branch not sorting first is a
database the rebuild never touches. Its inode does not change for the most ordinary reason
available, and the check reported a data-safety defect that was not there. Seen with four indexes
present while on the second of them.

DIRECTION TWO -- A TEST THAT PASSED FOR THE WRONG REASON. A branch_scoped test planted a sibling
index deliberately, then read `[0]` of the glob -- which was the PLANTED sibling, a snapshot taken
before the rebuild, so the assertion read the PREVIOUS answer and agreed with the wrong outcome.
Noticed only because the same helper, called directly, returned the opposite verdict.

THE RULE: name the index you mean. Use the current branch's, via the `_branch()` helper the test
file already has, and build it first on a fresh checkout so the stat has something to read. A glob
is fine where exactly one index can exist -- a scratch repo built once -- and nowhere else.

WHY IT IS WORTH AN ENTRY: both failures are silent in the direction that matters. One accuses the
engine of a data-safety defect it does not have; the other confirms a claim that is false. A test
that agrees with you for the wrong reason is worse than one that fails.
