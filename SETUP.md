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
git branch kb                            # an ORPHAN branch: no shared history with any code branch
git worktree add ~/kb/myproject kb       # its own checkout, outside the code tree
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
