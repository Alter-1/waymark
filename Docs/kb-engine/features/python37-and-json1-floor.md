---
concept_id: engine.compat.py37_json1
evidence: measured
keywords:
  - python 3.7
  - 3.7.8
  - walrus
  - SyntaxError
  - windows
  - JSON1
  - json_extract
  - no such function
  - sqlite build
  - create_function
  - compatibility floor
kind: feature
name: python37-and-json1-floor
status: resolved
---

## brief

TWO FLOORS THIS ENGINE MUST HOLD, both discovered on a Windows host and neither visible on a modern
Linux box: **Python 3.7** (so no walrus, no 3.8+ syntax) and **SQLite possibly built without JSON1**
(so `json_extract` may not exist). Both are enforced by tests, because both fail at a distance from
the change that caused them.

## notes

PYTHON 3.7. The build Python on the Windows host is 3.7.8. A walrus operator parses fine everywhere
it was written and dies at IMPORT time there, so the failure lands on whoever runs it next rather
than whoever wrote it. Checking the interpreter in front of you proves nothing about the floor --
nothing in the repo declared it until it broke.
*** ast.parse(feature_version=(3,7)) DOES NOT REJECT THE WALRUS *** -- measured -- so the grammar
check cannot be done that way; the suite checks it differently. Do not "simplify" that check into
feature_version.

JSON1. `notes` is the only query that calls json_extract, and it died with "no such function:
json_extract" on that toolchain. The fix is a Python fallback registered with
`con.create_function("json_extract", 2, ...)`, which returns the whole value.
*** REMEMBER THE FALLBACK WHEN CHANGING THAT QUERY. *** A cap written as
`substr(json_extract(...), 1, 400)` applied OUTSIDE the function, so it truncated on both the
native and the fallback path -- removing it fixed both at once, but a change made inside only one
path would have fixed only one.

THE GENERAL SHAPE: this engine is developed on Linux and used on Windows, so every compatibility
assumption is invisible where it is written and load-bearing where it is not. That is what the
tests are for; run them before believing a change is portable.
