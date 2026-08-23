# How a KB is stored

Three independent things get conflated whenever this comes up:

1. **what one entry looks like** — a markdown file with a small frontmatter block;
2. **how the directory is arranged** — `kb.json` plus one folder per collection;
3. **where that directory physically lives** — a plain directory, a worktree of an orphan branch,
   or its own repository.

They are separate choices. You can change any one of them without touching the others. The engine
reads the first two and does not care about the third; one line of `kb.config.json` names the path,
and that is the entire coupling.

A fourth thing is not storage at all: `.tools/code_index.<branch>.sqlite` is **generated,
per-branch and disposable**. Never commit it, never edit it, never treat a query result as the
record. Delete it and nothing is lost — `index_code.py` rebuilds it from the KB plus a scan of your
source.

---

## 1. The entry

One entry is one markdown file. The filename is the entry name. A real one, complete:

```markdown
--- routes/FBI_UART0.md ---
---
concept_id: fbi.channel.uart0
destination: UART0
file: Eth2Serial/Eth2Serial.h
name: FBI UART0
protocol: raw/crsf/sbus/mavlink depending on per-port config
source: FBI UDP packet prefix d
---

## notes

Often regular UART; may be single-wire in special configurations.
```

Long prose belongs in the **body**, not in a frontmatter value: there it diffs like prose instead of
arriving as one line full of `\n` escapes.

### The frontmatter dialect is deliberately not YAML

Exactly three forms:

```
key: plain text to end of line
key: [{"json": "when you need structure"}]
key:
  - one list item
  - another
```

Anything the reader cannot parse is a hard **error** naming the file and line — never a silently
dropped field. That rule is what makes the format safe to hand-edit.

### Keys that carry weight

| key | what it does |
|---|---|
| `name` | the entry's identity, and what `[[links]]` point at |
| `keywords` | what recall depends on — name the **symptom** someone types while the thing is going wrong, not the cause they do not know yet |
| `status` | closed vocabulary: `open` / `resolved` / `wontfix` / `n/a`, so "what is still open?" is answerable without reading everything |
| `evidence` | `measured` / `inferred` / `mixed` / `unknown` — a fact reasoned to is not the same kind of thing as one observed, and the reader deserves to know which |
| `claims` | dated assertions with their own status, including the ones that **died** — a dead hypothesis recorded is a week nobody spends re-deriving it |
| `see_also` | explicit typed links: `symbol:`, `annotation:`, `concept:`, `file:` |

Inline `[[entry-name]]` anywhere in the body is indexed and validated too. A link to an entry that
does not exist yet is not an error — it marks something worth writing.

---

## 2. The directory

The folder names are a **fixed vocabulary**, not cosmetic. Each collection lands in its own table
and answers a different question.

```
~/kb/myproject/
├── kb.json              # lists the collections; the only required file
├── features/            # how something behaves, and its ported/verified status
│   ├── io32-slot3-guarded.md
│   └── wifi-apply-freeze.md
├── concepts/            # incidents, open bugs, root causes, invariants
├── routes/              # endpoints and channels
├── symbols/             # notes bound to one symbol (what add_note.py writes)
└── source_index_annotations.2_1.json    # optional per-version overlay
```

| folder | table | queried with |
|---|---|---|
| `features/` | `annotations` | `annotation <kw>` |
| `concepts/` | `concepts` | `concept <id>` |
| `routes/` | `routes` | `route <name>` |
| `symbols/` | `symbol_annotations` | `notes <Symbol>` |
| `symbol_comments/` | `symbol_comments` | `comment <term>` |
| `branch_overrides/` | `branch_overrides` | per-branch value overrides |

`symbol_annotations` and `symbol_comments` are mostly **derived** from the source scan, which is why
they dwarf the hand-written collections (11 198 against 270 in the project this came from).

If `kb.json` is absent the engine treats every non-dotted subdirectory as a collection — which is
why a dotted directory is not one, and why `.git` sitting beside the entry folders does not break it.

> **Gotcha, paid for once.** A per-version overlay is found *relative to the KB*. Move the KB and
> leave the overlay behind and there is **no error** — entries just quietly stop existing. It was
> caught only by comparing index row counts before and after a move: 273 against 270.

---

## 3. Where the directory lives

| arrangement | good for | cost |
|---|---|---|
| a plain directory | notes that must never be pushed; simplest thing that works | no history, no sharing |
| **an orphan branch, checked out as a worktree** | shipping the KB with the repo everyone already has | none worth naming — this is the default recommendation |
| its own repository | a KB spanning several projects, or with a different audience from the code | one more thing to clone and keep in sync |

### What the orphan-branch arrangement actually is

**One repository with two working trees.** Not a submodule, not a second clone:

```bash
git worktree add ~/kb/myproject kb      # one-time
```

```
$ git worktree list
/home/…/myproject     faf463eb [main]
/home/…/kb/myproject  a908d5c5 [kb]     ← the KB

$ git -C ~/kb/myproject rev-parse --git-common-dir
/home/…/myproject/.git                  ← the same repository
```

Two properties follow, and both are the point:

* because it is a **worktree**, it stays put while you `git checkout` in the code tree — one
  knowledge base shared by every branch;
* because the branch is an **orphan**, it has no common ancestor with any code branch, so it can
  never be merged into one by accident.

### Why not just a file in the repo

The KB in the project this was extracted from started as one JSON document, gitignored, precisely
because a single shared file edited from several branches meant a guaranteed conflict every time.
**One file per entry removes that** — two people recording two different facts never touch the same
file — and an orphan branch removes the merge risk. Both halves of the original objection are gone,
which is what made it safe to put in git at all. What it buys is per-fact history:
`git log features/<name>.md` answers *when did this become true*.

---

## Several KBs at once

`annotations` takes a list. Every root is merged into one index and one search.

```json
"annotations": [
  "~/kb/myproject",
  "~/kb/myproject.local",
  "~/kb/esp-idf"
]
```

Two uses fall straight out of this: a **shared toolchain KB** that several projects read, and a
**public / local split** — everything shareable in the pushed root, everything site-specific (bench
addresses, home network details, customer particulars) in a root that is in no repository at all.

> **Make the wrong thing impossible, not discouraged.** Prefer a separate root over a
> `visibility: local` field on the entry. A file merely *marked* local still sits in the tracked
> worktree, and one `git add` publishes it. A directory that belongs to no repository cannot be
> pushed by accident.
>
> When a local entry contains something worth sharing, split it: the general fact becomes a public
> entry, the site-specific values stay local, and the two cross-link.

Product defaults that are already published — a documented password printed in the user manual, say
— are not secrets and do not need this treatment. Personal and third-party material does.

---

## Rules worth adopting with it

* **Never commit the index.** Check the entry count after a rebuild — a missing KB rebuilds an
  *empty* index.
* **Never `git add -A` in a KB.** Name the paths, or use `git add -u`.
* **Never merge a KB branch with a code branch**, in either direction.
* **Record honestly.** An entry that says *OPEN, here is what was measured and here is what would
  settle it* beats a tidy summary that hides the uncertainty.
* **Record solutions, not only problems** — and let the keywords name the problem the solution
  solves, so a search for the *symptom* finds the *cure*.

See `SETUP.md` for first-time setup, migrating an existing KB, and the data-safety cautions.
