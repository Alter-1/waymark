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

**Where does a knowledge base actually live?** It is a folder of markdown files, one per entry, and
where that folder sits — beside your code, on a branch of its own, or in a separate repository — is
your choice. If that is the question you came with, skip ahead to
[The KB on disk](#the-kb-on-disk) and [Managing it with git](#managing-it-with-git); the sample here
uses the older single-JSON form, which the engine still reads, so it is not a good picture of the
layout you want for a real project.

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

## The KB on disk

**A knowledge base is a folder of markdown files.** That is the entire storage format — no
database, no server, no lock file. You can read it with `cat`, edit it in any editor, and diff it
in a code review. Everything else on this page is built from that one fact.

If you are starting from nothing:

```bash
mkdir -p kb/features kb/concepts            # 1. make the folder
$EDITOR kb.config.json                      # 2. "annotations": "kb"
python3 .tools/index_code.py                # 3. build the index
```

### 1. One entry is one file

The filename is the entry's name. A small frontmatter block, then ordinary prose:

```markdown
--- kb/routes/uart0.md ---
---
name: FBI UART0
concept_id: fbi.channel.uart0
destination: UART0
protocol: raw/crsf/sbus/mavlink depending on per-port config
file: Eth2Serial/Eth2Serial.h
---

## notes

Often regular UART; may be single-wire in special configurations.
```

Long explanations go in the **body**, below the frontmatter — there they diff like prose instead of
arriving as one line full of `\n` escapes.

The frontmatter dialect is deliberately tiny and is **not YAML**. Three forms, nothing else:

```
key: plain text to the end of the line
key: [{"json": "when you need structure"}]
key:
  - one list item
  - another
```

Anything the reader cannot parse is an **error** naming the file and line — never a silently
dropped field. That is what makes the format safe to hand-edit.

### 2. The files sit in named folders

Each folder is a **collection**, and the names are a fixed vocabulary — not cosmetic. Each one
lands in a different table and is reached by a different query:

```
kb/                                      ← whatever `annotations` in kb.config.json points at
├── kb.json                              ← lists the collections below
├── features/                            ← how something behaves; ported/verified status
│   ├── io32-slot3-guarded.md
│   └── wifi-apply-freeze.md
├── concepts/                            ← incidents, open bugs, root causes, invariants
│   └── aes-parallel-uart-crash.md
├── routes/                              ← endpoints and channels
├── symbols/                             ← notes bound to one symbol (add_note.py writes here)
└── symbol_comments/                     ← hard-won rules left at an exact code site
```

| folder | you read it back with |
|---|---|
| `features/` | `query_code_index.py annotation <keyword>` |
| `concepts/` | `query_code_index.py concept <id>` |
| `routes/` | `query_code_index.py route <name>` |
| `symbols/` | `query_code_index.py notes <Symbol>` |
| `symbol_comments/` | `query_code_index.py comment <term>` |

Two more things may appear in that folder later: a per-version overlay JSON, which **must stay
inside the KB directory** because it is found relative to it, and nothing else. If `kb.json` is
missing, every non-dotted subdirectory is treated as a collection — which is why `.git` sitting
beside the entry folders does not break anything.

### 3. What is NOT part of the KB

`.tools/code_index.*.sqlite` is **generated** — one per branch, rebuilt by `index_code.py` from the
KB plus a scan of your source. **Gitignore it.** Never edit it, and never treat a query result as
the record. Delete it and nothing is lost.

A rebuild against a missing KB produces an *empty* index **without erroring**, which is the failure
that looks most like success. Check the entry count after a rebuild.

## Managing it with git

The engine never looks at where the folder lives — that is your call. Three arrangements work:

| arrangement | good for | cost |
|---|---|---|
| a plain folder, gitignored | notes that must never leave the machine | no history, no sharing |
| **an orphan branch, as a worktree** | shipping the KB with the repo everyone already has | none worth naming — start here |
| its own repository | a KB spanning several projects, or a different audience | one more thing to clone |

### Why not just commit it on your main branch

Because the KB is edited from every branch. A file that lives on `main` and is edited while you are
on a feature branch either follows you (and shows up in your code diffs) or does not (and your notes
vanish when you switch). An **orphan branch** sidesteps both: it is a branch in the same repository
that shares *no history* with any other, so it never merges into your code and your code never
merges into it.

A **worktree** is git checking out a second branch into a second folder at the same time. Together
they give you a KB folder that stays put while you `git checkout` in the code tree.

```bash
git checkout --orphan kb          # a branch with no shared history
git rm -rf .                      # nothing but the KB lives here
mkdir features concepts
git add . && git commit -m "kb: initial import"
git checkout main                 # back to your code

git worktree add ~/kb/myproject kb    # the KB now lives here, permanently
```

```
$ git worktree list
/home/…/myproject     4f2a1c9 [main]      ← your code
/home/…/kb/myproject  a908d5c [kb]        ← your KB

$ git -C ~/kb/myproject rev-parse --git-common-dir
/home/…/myproject/.git                    ← the SAME repository, not a clone
```

Point `kb.config.json` at it and you are done:

```json
"annotations": "~/kb/myproject"
```

### Day to day

Record, rebuild, commit — KB edits in the KB worktree, code in the code tree. Two branches, never
staged together:

```bash
python3 .tools/add_note.py <Symbol> "<finding>" --keywords "a, b"
python3 .tools/index_code.py
git -C ~/kb/myproject add features/<name>.md
git -C ~/kb/myproject commit -m "kb: ..."
```

What that buys you: `git -C ~/kb/myproject log features/<name>.md` answers *when did this become
true*, and two people editing two different entries never touch the same file.

Four rules, each of which exists because breaking it cost someone a day:

* **Never commit the generated index.**
* **Never `git add -A` in the KB** — it sweeps in the index and whatever else is lying around. Name
  the paths, or use `git add -u`.
* **Never merge the KB branch with a code branch**, in either direction.
* **Keep the version overlay inside the KB folder** — it is found relative to the KB, so moving the
  KB without it orphans it silently.

### Public and local halves

`annotations` also takes a **list**, so a shared KB can sit beside one that is in no repository at
all:

```json
"annotations": ["~/kb/myproject", "~/kb/myproject.local"]
```

Both are merged into one index and one search — only `git` tells them apart. Site-specific material
(bench addresses, personal network details, customer particulars) goes in the local folder. Prefer
that over marking an entry private: a file merely *marked* local still sits in the tracked worktree,
and one `git add` publishes it. A folder in no repository cannot be pushed by accident.

When an entry has both, **split it** rather than hiding it whole — the general finding goes public
where other people can use it, the values stay local, and the two cross-link. Most bench notes are
90 % general; moving them wholesale would gut the shared KB of exactly the root-cause work it
exists to carry.

`Docs/STORAGE.md` goes deeper, with the reasoning behind each rule.

## Browsing it

```bash
python3 .tools/serve_code_index.py        # http://127.0.0.1:8765/
```

Every search the CLI has, the browser has — because it does not implement any of them. It asks
`/api/commands` what exists and forwards each query to `query_code_index.py --json`. Add a
subcommand and it appears in the picker; there is one implementation of each search rather than two
that drift.

Both kinds of link are indexed: the declared `see_also` field, and `[[name]]` written inline in
prose — which is how most of them are actually written. On the tree this engine came from there were
**232 inline references against 29 `see_also` entries**, none of them indexed, so nothing validated
them and the graph showed an unconnected KB that is in fact densely cross-referenced. A declared
link that does not resolve fails `selftest`; an inline one is reported but does not, because the
convention allows a forward reference to something not written yet.

Press **graph** to draw the KB itself: entries as nodes, `see_also` and relations as edges, with
broken links and violated relations in red. Drag to pin a node, click to open the entry. The layout
is a few dozen lines in the page — no library, so it works offline like everything else here.

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

Start here: **[SETUP.md](SETUP.md)** — first-time setup, migrating an existing
knowledge base to one file per entry, the layout that makes it shareable, and the data-safety
cautions that go with moving a KB.

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

The engine is separate from what you write with it, and where your KB lives is your call —
gitignored beside the code, an orphan branch in the same repository, or its own repo. All three
work; `Docs/STORAGE.md` compares them. The KB in *this* repository is committed because it
documents the sample.

For anything you intend to share, split the KB rather than redacting it. `annotations` takes a
list, so a pushed root can sit beside a local one that is in no repository at all:

```json
"annotations": ["~/kb/myproject", "~/kb/myproject.local"]
```

Site-specific material — bench addresses, home network details, customer particulars — goes in the
local root. Prefer that over marking an entry private: a file merely *marked* local still sits in
the tracked worktree, and one `git add` publishes it. A directory that belongs to no repository
cannot be pushed by accident.

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
