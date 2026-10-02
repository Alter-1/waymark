---
claims: [{"status": "live", "evidence": "measured", "date": "2026-10-03", "text": "A PARTIAL SCAN -- index_code.py with a subset of the configured roots -- empties the primary tables of every root it was not given, while branch_symbols and symbol_lifecycle keep all of them. The index then DISAGREES WITH ITSELF: `symbol <name>` still returns a hit with file and line, because that query reads the lifecycle tables, while the files table no longer contains that file and refs/constants for it are gone.", "measured_on": "a project configured with its own roots plus an absolute ESP-IDF root. Full scan: files 11561, symbols 748483, branch_symbols 1364931. Then `index_code.py main components example tools` (the project roots only): files 61, symbols 902, refs 12227 -- and branch_symbols still 1364931, with the IDF symbol query still answering."}, {"status": "live", "evidence": "measured", "date": "2026-10-03", "text": "The notice a partial scan prints -- `partial scan (<roots>): lifecycle deletions skipped` -- describes the one thing that did NOT happen to the user's data and is silent about the one that did. A reader takes it as a reassurance.", "note": "index_code.py has a standing comment `ONLY A FULL SCAN MAY DELETE`, and full_scan is correctly computed as `sorted(args.roots) == sorted(DEFAULT_ROOTS)`. So the intent is right and the primary-table narrowing happens anyway -- the cause is not in that gate."}]
concept_id: engine.index.partial_scan_narrowing
evidence: measured
files:
  - .tools/index_code.py
keywords:
  - partial scan
  - index disagrees with itself
  - symbol found but file missing
  - lifecycle deletions skipped
  - multi-root
  - absolute root
  - rows disappeared
  - rebuild lost data
kind: feature
name: partial-scan-narrows-the-primary-tables
status: open
---

## brief

**A partial scan is advertised as narrower WORK. It is also narrower DATA, and nothing says so.**
On a single-root project the two are the same thing and the bug is invisible. On a multi-root
project -- which is the configuration the README recommends for exactly this case, an application
plus its SDK -- scanning one root throws away the others' rows from `files`, `symbols`, `refs` and
`constants`, and leaves `branch_symbols` and `symbol_lifecycle` complete.

The result is worse than losing the data outright, because the most-used query still answers. A
`symbol` lookup reads the lifecycle tables and happily reports a file and line for a file the
`files` table no longer knows, so the index looks fine until a query that joins the two returns
nothing and the user starts debugging the wrong thing.

## why it matters more than it looks

Recovering is NOT cheap. The files are unchanged on disk, so nothing is stale -- but their rows are
gone, so `plan_incremental()` treats them as NEW and re-parses every one. On the measured project
that is a 76-minute rebuild to undo a 110-second command.

## what to do until it is fixed

**Do not pass roots to `index_code.py` on a multi-root project.** Rebuild with no arguments, which is
a full scan, and accept the time. A no-op full rebuild on the measured project is 2m42s against a
76-minute cold one, so the usable workflow exists; it just is not the one the positional argument
invites.

## open

Not yet diagnosed to a line. `full_scan` is computed correctly and the deletion gate reads right, so
the narrowing comes from somewhere else in the write path -- most likely a table being rewritten
from the scanned set rather than updated per file. Worth finding before the next engine release,
because the failure is silent and the recovery is expensive.
