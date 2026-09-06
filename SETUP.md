# Setting up, and moving an existing knowledge base

Two things this covers: starting a KB on a project that has none, and migrating one that already
exists — including the layout that makes it shareable. **Read the safety section before migrating.**
Every caution in it is something that actually happened, not a hypothetical.

---

## 1. A new project

```bash
cp -r .tools/ /path/to/your/project/.tools/
cd /path/to/your/project
```

Create `kb.config.json` at the repository root — this is the only file that knows anything about
your project:

```json
{
  "roots": ["src", "firmware"],
  "annotations": "kb",
  "api_regex": "(AT\\+[A-Za-z0-9_?=:+,.-]*)"
}
```

* `roots` — subdirectories to index. Defaults to the whole repository.
* `annotations` — where the KB lives. A single `.json` file **or** a directory of one file per
  entry. May be a **list**, and entries may be absolute or `~` paths.
* `source_exts`, `c_like_exts`, `js_like_exts`, `skip_dirs`, `max_file_bytes` — what to scan and
  which grammar parses it. Only needed for a language outside the built-in C/C++/Python/JS/shell
  list; **without them such a project indexes as one file and no symbols, and still exits 0.** See
  the README.
* `api_regex`, `version_file`, `plugins` — optional; see the README.

Then:

```bash
mkdir -p kb/features
python3 .tools/index_code.py                       # builds .tools/code_index.<branch>.sqlite
python3 .tools/query_code_index.py selftest        # should be clean
python3 .tools/add_note.py my_function "The thing you just learned" --keywords "symptom, area"
python3 .tools/index_code.py
```

The generated `.tools/code_index.*` are **disposable** — rebuild them any time, never commit them.
The KB itself is the artefact.

---

## 2. Migrating a single-file KB to one file per entry

A single JSON document cannot be merged: two people editing **different** entries still collide on
the same file. It also destroys per-fact history — `git log` over a 1 MB blob cannot tell you when
one note became true — and prose inside a JSON string arrives full of `\n` escapes and diffs as a
single line.

```bash
python3 .tools/kb_split.py Docs/source_index_annotations.json kb
```

`kb_split.py` round-trips the whole document **in memory first** and refuses to write anything if
the result is not identical, so a failed migration leaves nothing behind.

### Then verify before you delete anything

This is the step people skip. Do not skip it.

```bash
cp .tools/code_index.<branch>.sqlite /tmp/before.sqlite   # the index as it was
#   ... point kb.config.json at the new directory ...
python3 .tools/index_code.py --force
```

Compare the two databases table by table — row counts **and** content, ignoring only surrogate
`id` columns and timestamps. On a real 270-entry KB this reported **17 tables, 0 differ**, and on
the first attempt it did **not**: it found 273 annotations against 270, because the per-version
overlay is located *relative to the KB* and moving the KB had orphaned it. There was no error
message. A few annotations had simply stopped loading.

That is the entire reason to compare indexes rather than eyeball the files.

---

## 3. Making it shareable

The KB has to be **one set shared by every branch** — a note written while on one branch must be
visible from all of them. That rules out an ordinary tracked file, which is per-branch and would
conflict on every merge.

The arrangement that works, in a single repository:

```bash
git checkout --orphan kb                 # a branch with NO shared history
git rm -rf .                             # nothing but the KB lives on it
mkdir features concepts
echo '{"schema": 2, "scope": "shared", "collections": ["concepts", "features"]}' > kb.json
git add kb.json && git commit -m "kb: initial import"
git checkout main                        # back to your code

git worktree add ~/kb/myproject kb       # its own checkout, outside the code tree
```

`kb.json` is written because **git does not track empty directories** — with only `features/` and
`concepts/` to stage, the first commit has nothing in it and fails, leaving no branch. The engine
does not require the file (a directory of entries indexes without it); it is the manifest, and it
gives the branch a first commit.

**`git checkout --orphan`, not `git branch`.** This document told you to write `git branch kb`
until 2026-09-03, and that is not an orphan: it makes an ordinary branch at your current HEAD, so
the KB branch carries your entire codebase and *does* share history with it. The worktree then
checks out the whole tree into your KB folder and the merge problem this arrangement exists to
avoid comes straight back. **Check rather than assume — both commands below, on a correct setup,
answer the same way every time:**

```bash
git merge-base kb main                     # prints NOTHING on an orphan
git ls-tree -r --name-only kb | wc -l      # your notes only -- not the size of your codebase
```

```json
{ "annotations": "~/kb/myproject" }
```

```
myproject/        ← code, switches between branches freely
~/kb/myproject/   ← the KB, always checked out, never switched
```

Why this shape:

* **Orphan branch** — shares no history with any code branch, so it never participates in a merge
  or a rebase. The conflict problem that forces people to gitignore their KB does not arise.
* **Its own worktree** — the KB directory never collides with a `git checkout` in the code tree,
  and `git checkout` in the code tree does not touch it. Verify this on your own setup rather than
  trusting it: switch branches and confirm the KB's `HEAD` and file count are unchanged.
* **Outside the code checkout** — see the `git clean` caution below.
* **Sharing is `git push`.** Everyone who has the repository already has the KB branch; they need
  `git worktree add` once. No submodule, no second remote, no recorded SHA to keep in step.

Committing a note is done **in the KB worktree**, not in the code tree:

```bash
git -C ~/kb/myproject add features/<name>.md
git -C ~/kb/myproject commit -m "..."
```

### Keeping it in step

```bash
git -C ~/kb/myproject push -u origin kb    # the FIRST publish; -u only this once
git -C ~/kb/myproject push                 # every time after
git -C ~/kb/myproject pull                 # before writing, if anyone else publishes
```

**On a fresh clone the KB is already there** — it came with the repository as `origin/kb`, and one
command gives it a folder:

```bash
git worktree add ~/kb/myproject kb
```

Git creates the local `kb` tracking `origin/kb` for you. Older git refuses to guess; then be
explicit with `git fetch origin kb:kb` first.

### Undoing a half-finished attempt

A migration interrupted part-way leaves a worktree registered whether or not the folder is usable,
and git will then refuse to create it again at the same path. Nothing here touches your notes:

```bash
git worktree list                          # what git believes exists
git worktree remove ~/kb/myproject         # unregister and delete the folder
git worktree remove --force ~/kb/myproject # ...if it has uncommitted changes you accept losing
git worktree prune                         # forget worktrees whose folder is already gone
git branch -D kb                           # only if the BRANCH was created wrong, e.g. not orphan
```

`git branch -D kb` deletes notes if any were committed to it — check with
`git log kb --oneline | head` first, and if there are commits worth keeping, push the branch
somewhere before deleting.

### Three things not to do

* **Never `git checkout kb` in the code tree.** It replaces your code checkout with the KB. While
  the worktree exists git refuses this for you — "already checked out" — so the real exposure is
  before you create it, or after `git worktree remove`. Do not rely on the refusal; the worktree is
  there so you never need the command at all, and the code tree switches branches without touching
  the KB.
* **Never merge `kb` into a code branch, or a code branch into `kb`.** They share no history; git
  will let you, and the result is your codebase committed onto the KB branch or the reverse.
* **Do not put the worktree inside the code checkout** — see the `git clean` caution below.

### Several KBs

`annotations` takes a list, so a project's own notes can sit beside a shared one:

```json
{ "annotations": ["~/kb/myproject", "~/kb/esp-idf"] }
```

Both are indexed together and queried as one. Keep facts about a **shared** dependency in the shared
KB, and facts about **this** codebase in the project's — a fact that is true of a toolchain in
general must never be readable as a fact about your build.

---

## 4. Data safety — read this before migrating

**Back up first, and keep the backup after.** `kb_split.py` never deletes the source, and the
engine keeps a compressed copy on every rebuild, but neither is a substitute for a copy you took
deliberately.

**Verify by rebuilding, not by looking.** A migration that loses a field loses it silently. The
index comparison above is the only check that catches it. It is also the check that caught the
orphaned version overlay — which no amount of reading the files would have shown.

**A missing KB does not fail the build.** Point `annotations` at a path that does not exist and you
get an index with **zero notes** and a normal-looking summary; every query then answers "no
matches", which reads as an empty topic rather than a broken setup. The engine now prints a loud
warning when a *configured* KB is absent — do not ignore it, and check the entry counts after every
rebuild.

**`git clean -fdx` will delete a KB that lives inside the code checkout**, history and all, because
`-x` includes ignored files. This is the strongest argument for keeping the KB outside the code
tree. If you must keep it inside, know that one command removes it.

**Do not `git add -A` in the KB repository.** Name the paths. A sweep pulls in editor backups,
generated indexes and whatever else is lying around, and a KB is exactly the place where you will
not notice.

**One set, not one per branch.** If you ever find yourself with a different KB on different
branches, something is wrong: a note written on one branch is then invisible from the others, which
is how the same fact gets discovered three times.

**Renaming an entry breaks every reference to it.** Inline `[[name]]` references are indexed and
validated, so `selftest` will tell you — but only after a rebuild. Rename deliberately, then
rebuild, then fix what it reports.

---

## 4b. Reaching it from an editor (MCP)

The CLI is the tool. This is reach: an assistant inside VS Code, Cursor, Zed, JetBrains or Claude
Desktop has no shell, so `query_code_index.py` is unreachable there -- and even where a shell
exists, nothing tells an assistant that a project-specific knowledge base is worth asking. An
advertised tool gets consulted unprompted, which is the habit the whole thing depends on.

`.tools/mcp_server.py` speaks MCP on stdin/stdout. Standard library only, no SDK. Register it as a
stdio server; the shape is the same everywhere, only the config file differs:

```json
{
  "mcpServers": {
    "waymark": {
      "command": "python3",
      "args": ["/absolute/path/to/your/project/.tools/mcp_server.py"]
    }
  }
}
```

Client support and config location vary by product and version -- check the one your team actually
uses. In VS Code it is the assistant's agent mode that consumes MCP, not the editor itself.

It exposes ONE tool, `waymark_query`, with the command as a parameter. That is deliberate: a tool
definition is context, re-sent on every turn of every conversation whether or not the KB is
touched. Two dozen tools would spend that budget forever; one costs a fraction and stays correct
as commands are added -- the enum is built by asking the CLI what it supports, and
`tests/test_engine.py` fails if the two ever disagree.

It owns no knowledge of the KB, for the same reason `serve_code_index.py` does not: one
implementation of each search, not two that drift.

Humans do not need it. `python3 .tools/serve_code_index.py` gives you the browser, and the CLI
gives you everything else.

## 5. Checking your setup

```bash
python3 .tools/index_code.py --force
python3 .tools/query_code_index.py selftest
python3 .tools/query_code_index.py broken-links
python3 .tools/serve_code_index.py          # browse it, press "graph"
```

`selftest` reports the ways a KB goes wrong quietly: an index built from a missing file, a
`see_also` pointing at nothing, a claim with no provenance, an entry whose one-line summary
contradicts its own status. It exits non-zero, so it belongs in CI or a pre-commit hook.

---

## Then: the habits, not just the tool

Installing waymark gives you somewhere to put knowledge. It does not make knowledge appear, and it
does not stop a workflow from destroying the evidence before anyone writes it down.

**[`Docs/BEST-PRACTICE.md`](Docs/BEST-PRACTICE.md)** is the companion to this file: verify before you
fix, explain before you edit, make absence loud, keep the evidence of failure, record the dead ends,
and never let a secret be guarded by a fact about which file it happens to sit in. Each practice is
written with the concrete failure that produced it.

If you are adopting this for a team, that document is the part worth reading together.
