# waymark

A knowledge base that lives next to your source and is queried from the command line.

A waymark is a marker left on a route so the next traveller does not have to work it out again.
That is the whole idea: the expensive thing in a long-lived codebase is not writing code, it is
re-deriving what somebody already learned — which hypothesis was tested and died, why a function
looks wrong but isn't, what the wire actually carries as opposed to what the config claims.

waymark indexes your code, and lets you attach durable notes, **claims with evidence**, and
cross-references to it. Then it checks itself, because a knowledge base nobody trusts is worse
than none.

```
$ waymark/.tools/query_code_index.py claim --status dead
  status: dead
  evidence: measured
  entry: compaction
  text: The lost writes are caused by WAL_SYNC_BATCH not flushing before the segment swap,
        so setting WAL_SYNC_ALWAYS should fix it.
  killed_by: reproduced with WAL_SYNC_ALWAYS at 8x the write cost: identical growth and
             identical lost writes. The policy was never involved.
```

That entry is the point of the tool. Somebody spent days on that theory. Without it written down,
the next person spends them again — and the search that finds it is a search for the *symptom*.

## Quick start

```bash
git clone <this repo> && cd waymark
python3 .tools/index_code.py            # build the index
python3 .tools/query_code_index.py summary
python3 .tools/query_code_index.py selftest
```

Everything below runs against `sample/`, a small append-only key-value store included in this
repository, and its knowledge base in `Docs/source_index_annotations.json`. The examples are real:
copy and paste them.

## Finding things

```bash
python3 .tools/query_code_index.py symbol compact              # where is it, what is its signature
python3 .tools/query_code_index.py symbol wal_append --branches all
python3 .tools/query_code_index.py constant WAL_SEGMENT_SIZE
python3 .tools/query_code_index.py refs WAL_SEGMENT_SIZE       # who uses it
python3 .tools/query_code_index.py comment tombstone           # rules left at the code site
python3 .tools/query_code_index.py arch WAL                    # KB_ARCH: comments
python3 .tools/query_code_index.py api 'KV+PUT'                # your project's command dialect
python3 .tools/query_code_index.py file store.cpp
```

Output is compact by default and shows the first useful lines of long fields. Add `--full` to
expand an entry before relying on its detail, or `--json` for machine-readable output.

## The knowledge base

```bash
python3 .tools/query_code_index.py annotation compaction       # by name or keyword
python3 .tools/query_code_index.py annotation writes-disappear # by SYMPTOM -- this is the point
python3 .tools/query_code_index.py --full annotation durability-policy
python3 .tools/query_code_index.py concept store.durability
python3 .tools/query_code_index.py open                        # everything still unresolved
```

Notes live in `Docs/source_index_annotations.json`, which is the source of truth; the index is a
build product and is regenerated with `index_code.py`. **Keyword entries on the SYMPTOM, not the
cause** — a year later you will search for "writes disappear", not for "compaction snapshot".

Each entry carries a short `brief` for triage and a long `notes` for the evidence. Compact output
prefers the `brief`; `--full` gives you everything.

## Claims: what was tested, and what died

A note that says "compaction is fine now" ages badly. A claim carries its provenance:

```bash
python3 .tools/query_code_index.py claim compaction
python3 .tools/query_code_index.py claim --status dead --dead-first
python3 .tools/query_code_index.py claim --evidence measured
```

* `--status` — `live` (believed), `dead` (disproved, with `killed_by`), `open` (unsettled).
* `--evidence` — `measured`, `inferred`, `reported`, `unstated`.
* `--dead-first` — start with what has already been ruled out.

`--dead-first` is the query to run when you pick up an investigation. A hypothesis that was tested
and killed, with the reason recorded, is the most reusable thing in the file and the easiest to
spend a week re-deriving.

## Cross-references

Entries link to code and to each other with `see_also`, and the links are validated on every build:

```bash
python3 .tools/query_code_index.py links compaction
python3 .tools/query_code_index.py broken-links
```

A link may target `symbol:`, `constant:`, `annotation:`, `concept:`, `route:`, `comment:` or
`file:`. Two conveniences worth knowing:

* **Bare names resolve to qualified ones.** `symbol:compact` finds `Store::compact`, because that
  is how you looked it up. A name that matches more than one symbol stays `ambiguous` and asks you
  to qualify it, rather than silently picking the first.
* **A concept and the annotation elaborating it are one subject.** They share a `concept_id`, so
  `annotation:compaction` resolves to the entry holding the content instead of complaining that the
  name is ambiguous with its own concept.

## Rebuilding

```bash
python3 .tools/index_code.py            # re-scans only what changed
python3 .tools/index_code.py --force    # everything, from scratch
```

A rebuild re-scans the files whose size or mtime moved, and reuses everything else. On a
678-file tree (11.6k symbols, 73k refs) that is **13.3 s down to 2.4 s** for an ordinary edit. The
build says which it did:

```json
{ "scan": "incremental", "rescanned": 1, "dropped": 0, "refs_rescan": "changed" }
```

The rebuild is **atomic**: it builds into a private file and publishes it with one rename, so a
concurrent reader sees the old index or the new one and never a half-built one, and an interrupted
build leaves the previous index in place. Two builds racing each other each produce a complete
index and the last rename wins — wasted work, never corruption, and no lock required.

One case costs more, and it is not a bug. References are found by matching tokens against the
**complete** symbol table, so adding or renaming a symbol means files that did not change may now
contain references to it. When the symbol name set moves, references are rebuilt for every file
(`"refs_rescan": "all"`); when it does not, only the changed files are touched. Skipping that would
leave references missing, and a missing reference is indistinguishable from one that was never
written — the failure this whole tool exists to avoid.

Size and mtime is the same pair git's index uses, and it inherits the same caveat: a checkout can
restore an old timestamp. `--force` is the answer to that, rather than making every build slow.

## Relations: claims the build can test

`see_also` says two entries are related. A **relation** says something about the code, and every
build goes and checks it:

```json
{
  "name": "wal_open",
  "notes": "Opening the log is Store::open's job. A write path that reaches it opens a SECOND handle...",
  "relations": { "must_not_call_from": ["Store::put"] }
}
```

```bash
python3 .tools/query_code_index.py relations
```

`must_not_call_from` is tested against the call graph already in `refs`, transitively — a violation
is reported with the chain that causes it (`Store::put -> wal_append`), and `selftest` fails on it,
because an assertion the code has stopped honouring is a defect and not a note.

**Know what this can and cannot tell you.** A violation is a finding; **silence is not evidence**.
`refs.in_symbol` is the nearest preceding definition, headers get no attribution, and a source-level
graph cannot see calls the compiler invents — the bug that motivated this feature reached flash from
an ISR through a GCC `$constprop` clone and had to be caught by disassembly. So the check catches
paths that should not exist; it never certifies that none does.

For the same reason the engine is explicit about what it did **not** check. A target that resolves
to no known symbol is `unchecked`, and a relation kind this engine does not implement is
`unknown-relation` — both listed by `selftest` without failing it. The one thing worse than an
unchecked invariant is an unchecked invariant that looks enforced.

Everything else — "writes EEPROM", "restarts WiFi" — belongs in `claims`, with the evidence that
backs it. Prose about side effects cannot be tested, so it should carry provenance rather than
sit next to a verified relation looking equally solid.

## Checking the KB itself

```bash
python3 .tools/query_code_index.py selftest     # exits 1 on any problem
```

Each check corresponds to a way a knowledge base goes wrong quietly: an index that built from a
missing annotation file and is silently EMPTY, a `see_also` pointing at something that no longer
exists, a claim with no provenance so an inference reads like a measurement. It exits non-zero, so
it belongs in a pre-commit hook or CI.

### When the headline outlives the entry

The most-read part of an entry is its one-line `brief`, and that is the part most likely to go
stale: the body gets corrected when the facts change, the summary above it does not. Waymark
compares the verdict an entry OPENS with against its own `status` and says so **in the reader**:

```
$ query_code_index.py annotation wifi-apply-freeze
wifi-apply-freeze feature
  !! status=open, but the brief opens by calling it RESOLVED -- read the notes, not the headline
```

It is deliberately not a verdict about which side is right — a mixed state is legitimate, and a fix
that has landed while its bench re-test is still outstanding is honestly both. It is a warning
placed where somebody is about to act on the headline alone. `selftest` lists the same entries as
`REVIEW` and does **not** fail for them, because a check that is permanently red is a check nobody
reads. Only the opening of the brief is examined; a brief that narrates a history it has moved past
("this was believed FIXED in build 9 and is not") is left alone.

## Handing work to the next context

Waymark keeps durable facts queryable; a handover records current working state. Use both when a
session stops mid-investigation: put facts and dead hypotheses into the KB first, rebuild and run
`selftest`, then write a short handover with repo state, hardware/runtime state, what is proven, and
the next concrete action. See `HANDOVER.md`.

## What it needs

Python **3.7** or newer, standard library only. No third-party packages, and nothing to install.

3.7 is not a guess: the engine is run on a work host with Python 3.7.8 and a SQLite built without
the JSON1 extension, and both of those have already broken it once. Where SQLite lacks JSON1 the
`notes` query registers a small pure-Python `json_extract` and carries on. Where the interpreter is
3.7, syntax is the thing that bites — a `:=` does not degrade one query, it stops the module
parsing and takes every query with it, `selftest` included. CI parses the tree under a real 3.7 for
that reason, and the test suite refuses 3.8+ syntax on whatever machine you are working on, which
is where a syntax error is cheap to fix.

## Using it on your own project

Copy `.tools/` into your repository and write a `kb.config.json` at the root:

```json
{
  "roots": ["src", "tests"],
  "annotations": "Docs/source_index_annotations.json",
  "version_file": "src/version.h",
  "api_regex": "(KV\\+[A-Za-z0-9_?=:+,.-]*|\\?[A-Za-z0-9_*][A-Za-z0-9_=&*.,+-]*)"
}
```

Every field is optional. With no config at all the engine indexes the repository it sits in, which
is enough to try it. `api_regex` teaches it your project's command dialect — omit it and no api
markers are indexed rather than a dialect being invented for you. `version_file` is read for a
`BUILD_VER` define, which lets you keep version-specific overlays.

Languages recognised: C, C++, Python, JavaScript, HTML, shell. Parsing is regex over a lexical
pass that classifies code, comments, strings and char literals — good enough to navigate by, and
deliberately not a compiler.

## Keeping your notes private

The engine is separate from what you write with it. In the project this was extracted from, the
annotation file is gitignored and shared across branches as local knowledge, while the tools are
tracked. Both arrangements work; the KB in *this* repository is committed because it documents the
sample.

Backups are automatic: every run keeps a gzipped copy of each annotation file it read under
`.tools/kb-backups/`, one per distinct content, sixty retained. The file is hand-written over
months and is usually not in git, so it is the one thing here worth protecting from a bad edit.

## Sample project

`sample/` is a small append-only key-value store — a C write-ahead log, a C++ index on top, a
Python client and a status page. It is not a toy for its own sake: its knowledge base carries a
real-shaped investigation (writes appearing to vanish, two dead hypotheses, the actual root cause)
so the queries above have something honest to return.

## Licence

MIT. See `LICENSE`.
