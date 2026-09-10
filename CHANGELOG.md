# Changelog

Notable changes to waymark, newest first.

There are no version tags yet, so entries are dated. Each names what a **user** gets or stops
getting; the commit messages carry the reasoning and the measurements behind them.

## 2026-09-10 (later)

### Added
* **`gen_skills.py` — the knowledge base projects its procedures into agent skills**, and
  `index_code.py` regenerates them on every build. A skill is GENERATED, never authored: the KB is
  the source, the skill directory is disposable, and a fresh clone gets its skills from the first
  index build. There is no restore step and no second copy to drift.

  This is a deliberate answer to *reach*. A skill is the narrowest store there is — it means
  nothing to a human, nothing to another tool, and in the project this was built for it does not
  even reach the off-site mirror, whose path allow-list carries `.tools/` but not `.claude/`. So a
  skill must never be the only copy of anything: the finding stays in the KB, the doing stays in a
  script, and the skill is a thin adapter over both.

  Opting in takes three things, and the third is the one that matters: `skill:` (a name),
  `skill_when:` (the trigger, in your words — the only text an agent matches on), and a
  `## procedure` section, **which is the only section copied**. An entry without one is refused
  rather than flattened. Most of a good KB entry is evidence, measurements and dead hypotheses —
  its whole value, and exactly what must not be loaded into a skill. `--check` reports without
  writing and exits non-zero, so it can gate a build.

  `skills_dir` in `kb.config.json` moves the output; the default is `.claude/skills`. The directory
  an agent reads is that agent's convention, not the knowledge base's.

## 2026-09-10

### Added
* **`kb_stale --branches`** — one knowledge base, several long-lived branches. A referent is then
  not simply present or absent: it can be **correct somewhere else**. `--branches current` (the
  default) is unchanged; `--branches all` (or a comma-separated list) widens the search when the
  current branch misses and **names the branch that satisfies it**, under a new
  `resolves-only-elsewhere` heading. `--strict` turns that into a failure, for a gate that wants
  one branch to be self-contained.

  Measured on a 299-entry KB shared by four branches, from the branch where an implementation file
  had been renamed: **23 file referents and 15 citations reported as problems became 0 and 2** — and
  one of the two survivors was a genuinely stale citation (`local_hw.cpp:1085`, where no branch's
  copy is longer than 842 lines), which is the tool doing its job. The repair those false alarms
  invite — rewriting the paths to the new name — would have broken every one of them on the
  branches where the old name is the real and only name.

  The tiers degrade differently per check, and that is deliberate: `file-exists` and
  `citation-range` are answered by **git**, so they widen to any branch for free and work on a
  fresh clone. `symbol-exists` needs that branch's **index**, and `code_index.*.sqlite` is
  generated and normally gitignored — so where a branch has no index the answer is **unresolved**,
  never "missing", with a git-grep fallback that can only ever downgrade a claim.

### Fixed
* **A KB root written with `~` was silently never opened.** `~/kb/x` was joined to the repo root,
  producing `<repo>/~/kb/x`, which exists nowhere — so the walk found no entries, all five checks
  reported `ok`, and the run exited 0 on a knowledge base it had never read. Roots are expanded
  before use, and **zero entries is now reported as such and exits non-zero**: five checks over an
  empty set are not a pass, and a green run on a KB nobody opened is the same silent-success
  failure this tool exists to find, turned on itself.
* **Frontmatter lists are read.** Only `key: value` scalars were parsed, so a KB writing its
  referents as a `files:` block list (or inline `[a, b]`) had **every one of them ignored** —
  the file check inspected nothing and said `ok`. Both spellings and both list forms now work, and
  every referent is checked rather than only the first.
* **A citation is measured against the right file.** The same basename can be a 24-line shim on one
  branch and the real implementation on another; six citations were reported out of range against
  the shim, and all six were correct.

## 2026-09-10

### Added
* **`kb_stale.py`** — is the knowledge base still **true of the code**? `selftest` has ten checks
  and all ten are about a KB's internal consistency; every one passes on a KB that is perfectly
  consistent and completely out of date. This checks the other direction: the entry's `file:` still
  resolves, the index still knows its `name:`, that name is not present only as a commented-out
  definition, a `file.cpp:NNN` citation points inside the file, and — as REVIEW, never a failure —
  the cited file has not been committed to since the entry was written. Non-zero exit on a problem,
  so it can gate a build.

  It does **not** judge prose, and it does not rewrite anything: a stale entry might need its
  citation corrected or its finding deleted, and only a person can tell which.

  Its first run on a real 3000-file tree reported **149 problems, and almost all were the tool's own
  fault** — bare names matched against qualified symbols, a line number inside a frontmatter `file:`
  path, entries about code outside the indexed roots. 149 → 1. Those are the test cases now; a
  checker that cries wolf is one people learn to skip, and it takes the true findings with it.

### Fixed
* **A definition whose parameter list wraps is a definition.** The matcher required the closing `)`
  on the definition line, and the split form beside it is for the opposite shape — name first,
  return type on the line before. Neither covered

  ```cpp
  bool Store::copy_range(const std::string& first_key, const std::string& last_key,
                         Store* into, bool overwrite_existing) {
  ```

  which is how a great deal of C++ is written once a signature outgrows a line. Such a function got
  **no symbol row at all**: no notes, no refs, no graph, and any KB entry naming it read as stale.
  On one 3000-file tree that was **575 invisible functions** — `files` and `constants` unchanged, so
  nothing else moved. It is recorded at the line the definition **opens** on, so citations already
  written against it keep meaning what they meant. Found, fittingly, by `kb_stale` reporting entries
  whose symbol "did not exist" against an index newer than the sources.

* **`dangling-refs` no longer calls a qualified name deleted.** Moving an inline out of a header
  renames the symbol from `ratio` to `Thing::ratio` in a single indexing run: the bare row goes
  deleted, the qualified row arrives, and every call site still writes the bare name.

  A third filter class, and neither of the two added the same day catches it: the name is long, so
  the length floor passes it, and both sides are the same language, so the family check passes it
  too. On one 3000-file C++ tree it was the **only** finding and it was false — the rate that
  teaches a reader to ignore a command, which is the reasoning every one of these filters exists
  under.

* **The test suite's exit code now sees every check.** `if FAILED: return 1` sits partway up
  `main()`, with roughly 350 lines of cases after it — the html lexer, the js vars, `dangling-refs`.
  A failure in any of those printed `FAIL` and then fell through to `all passed` and exit 0. Caught
  while adding cases below it: two printed FAIL, the run said `all passed`, and the exit code was 0.

## 2026-09-09 (later)

### Added
* **`dangling-refs`** — names this codebase used to define, no longer defines, and **still
  references**. That is a refactor which removed a definition and left the call sites behind: in a
  compiled language the build catches it, and in a scripting language nothing does. It is reported
  with the enclosing function of every site, because "who still calls this" is the question you
  actually have.

  Two filters keep it honest, and both were added because the first run on a real repository
  produced two findings and both were wrong. A name is only the same name **within one language**:
  a `dbg` deleted as a JS function was "still referenced" by an HTML element id and by a local in a
  Python test. And names shorter than four characters are ignored by default (`--min-len`), because
  a short identifier is reused as a local in unrelated code far more often than it is left
  dangling — the survivor of the first filter was `char dmp` in a C file, a local variable that
  happened to share its name with a symbol mis-extracted from a generated blob.

  Written after a real one. Four page-level JavaScript flags were replaced by a unified structure
  and three references were left behind; one of them sat in an error handler, so it threw only when
  a request failed — and the message that handler existed to print was the one thing that would
  have pointed at it. It shipped in two release images. Reproduced here from the two real revisions:
  the command names all four, at all six sites, with `in jq` and `in uploadFW` beside them.

### Fixed
* **A page is two languages, and only one of them was being lexed.** `.html` got `<!-- -->`
  handling and nothing else, so every `//` and `/* */` inside `<script>` was indexed **as code**.
  On one real page that was **371 comment lines presented as source** — commented-out code
  contributing symbols, and names mentioned only in prose coming back as live references.
  Now the JavaScript rules switch on inside `<script>` and the markup rules switch off.

* **JavaScript regex literals are no longer read as quotes or comments.** `/[&<>"']/g` holds a `"`
  and a `'` that are ordinary characters; lexed as quotes they pair with the next real quote far
  below and everything between stops being code. This was not optional: fixing the comments without
  it took the code the indexer could see on one page from 96.9% of the file to **48.7%**. A slash
  opens a regex unless the previous significant character ended a value.

* **Module-scope JavaScript variables are indexed** (kind `js_var`). Only `js_function` was, so
  `var enabled = 0;` was invisible — and invisible means its deletion was invisible too, which is
  why `dangling-refs` could not have worked without this. Locals stay out: one page had 52
  module-scope declarations against 230 locals, and nobody searches for a local.

* **The reference scan keeps looking for a name after its definition is gone.** A reference was
  recorded only when it matched a known name, so removing a definition erased every call site of
  it from the index — the evidence vanished at exactly the moment it became interesting, and
  "0 references" read as "nothing uses it".

## 2026-09-09

### Added
* **`css_factor.py`** — reports which repeated CSS declarations could be folded into a selector
  list, **and whether that is safe**. It refuses to move a declaration whenever any other rule in
  the same context sets the same property, or a shorthand/longhand relative of it, or mixes
  `!important` — because CSS resolves conflicts by order and specificity, so grouping selectors can
  change what a page renders without changing what it says. `--fix` applies only the accepted ones;
  `--show-refused` prints the reasons, which are usually the more interesting output. Savings are
  reported in **minified bytes** and are a deliberate lower bound.

  Measured on the sheet it was written for: **0 candidates, 17 refused.** Worth knowing before
  anyone spends an afternoon grouping selectors by hand — on that page, plain whitespace removal
  at compaction time was worth 580 bytes at no risk.

  Two of those refusals exist because `--fix` was tried on a real sheet and its output was worse
  than the 12 bytes it saved: it emitted minified rules into a hand-maintained stylesheet, and it
  deleted a rule whose last declaration it had folded away — leaving four lines of comment
  explaining a rule that was no longer there. So `--fix` now reproduces the shape it found
  (indentation, one-line or multi-line, declarations as authored), and a rule that a comment
  explains is refused rather than emptied.

## 2026-09-07

### Added
* **`xstatus` — a cross-branch register** (`.tools/xstatus.py`, `Docs/CROSS-BRANCH-REGISTER.md`).
  For codebases kept as several long-lived parallel lines, where `git cherry` cannot answer "is
  that fix on the other branch?" because ports are hand-adapted and patch identity no longer
  matches. Records **why** a fix is absent — never ported, deliberately rejected, or not applicable
  — which git has nowhere to put. SQLite with plain SQL views, usable from any `sqlite3` client.
* **`verify_port.py`** — answers "is this commit's substance on that branch?" by content rather
  than patch id, scoring only tokens the commit *introduced*. Triage, not a verdict.
* Two entries in `Docs/BEST-PRACTICE.md`: never cap a completeness search, and always run the
  negative control.

### Fixed
* `verify_port.py` searched a fixed `.cs/.xaml/.cpp/.h/.xml` include list, so a port that landed in
  a `.py`, `.sh`, `.js` or `.html` was reported **absent** with the file byte-identical in the
  target tree. It now derives the file types from the commit itself. Measured on a real repository:
  four of thirty-five reported absences were wrong.

## 2026-09-06

### Added
* **MCP server** — reach the knowledge base from an editor, not only from a shell.
* C indexing records the **preprocessor condition** a definition sits inside, and reports
  **duplicated include guards** (an include guard is not a condition; a repeated one is a defect).

### Fixed
* A C definition whose return type sits on the previous line is indexed.

## 2026-09-03

### Fixed
* `git branch kb` never made an orphan branch, though SETUP said it did.
* The determinism check failed on every clean checkout.
* The README described an engine three commits out of date.

## 2026-09-02

### Added
* `evidence` — one field answering both "can I trust this?" and "is it still true?".
* `selftest` reports the vocabulary problems a rebuild only whispers about.

### Fixed
* A single arrow character in one entry killed the whole query: stdout is now forced to UTF-8.
* Source files are decoded with the encoding they are actually in, not assumed UTF-8.
* Two permanent false alarms in the broken-link report.

## 2026-08-30

### Added
* **What to scan and which grammar parses it come from the config**, not from hard-coded lists.
* An XML-like grammar, and the lexer's markup set stops being hard-coded.

## 2026-08-24

### Added
* `--until`, so you can ask what has been open a while.
* A dead end is recorded with the **context** it died in and what would revive it.
* Taxonomy merge proposals — with the half that does not work reported as not working.

### Fixed
* A full rebuild silently destroyed the history it is documented to keep.

## 2026-08-22 — 2026-08-23

### Added
* **A knowledge base can be a directory, one file per entry** — which is what makes a KB shareable
  through git without every edit colliding in one document.
* Browse the KB over the CLI, and draw how its entries link.
* `[[references]]` written in prose are indexed, not just declared `see_also`.
* Relations the build can test, starting with `must_not_call_from`.
* `--local`, and the tool says which knowledge base a finding went into.
* `Docs/STORAGE.md` and `Docs/BEST-PRACTICE.md`; waymark now indexes itself.

### Fixed
* Directory KBs were never backed up — only the split form was.
* A symlink in a KB was archived as a symlink rather than as an entry.
* A link whose target lives on another branch is external, not broken; and the `branch_scoped`
  claim is checked rather than believed.
* A KB-only edit did not trigger a rebuild.
* `add_note.py` could not write to a directory KB or read a list config, and cut notes longer than
  400 characters **silently**.

## 2026-08-19 — 2026-08-21

### Added
* **`add_note.py` — the write half.** Until then waymark could only be read.
* Incremental rebuilds: re-scan only what changed, and lex each file once.
* The index is published by rename, and one note-writer at a time.
* A session can ask what changed.

### Fixed
* Claim dates were being lost.
* Works where SQLite has no JSON1, and stays parseable on Python 3.7 (now enforced in CI).
* A missing KB file is reported — and warned about only where one was asked for.
* An entry whose headline disagrees with its own status is called out.
* Annotation files are written aside and renamed, never in place.

## 2026-08-18 — first public commit

* **waymark: a knowledge base that lives next to the source.** The indexer, the query CLI, the
  sample project, CI, and the handover workflow. Nothing in the engine knows the host project's
  name; a codebase with no constants is not a broken index.
