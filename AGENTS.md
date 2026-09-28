# Agent Instructions

Read **[`Docs/BEST-PRACTICE.md`](Docs/BEST-PRACTICE.md)** before working on a codebase with waymark
in it. It is short, and it is the difference between an agent that leaves knowledge behind and one
that leaves a mess: verify the report before fixing it, explain the diagnosis before editing, prove
a test fails without the fix, assert the branch in the same step as the commit, keep the evidence on
failure, and record the dead ends as well as the answer.


## Use Waymark First

Before any investigation, bugfix, porting, release-note work, or test planning:

1. Discover the project context. If the project has `waymark.project.json`, run the read-only
   bootstrap from the directory where the task starts:

```bash
python3 .tools/workflow.py context --cwd . --task "<the request>"
```

   Read the reported instruction files and identify the active repository, branch, related
   repositories, authoritative knowledge roots, generated caches, tool documentation, and protected
   paths. Report conflicts or missing required context instead of silently choosing one. **Say which
   manifest you obeyed** -- discovery walks upward and a satellite `extends` anywhere, so the file
   that won may not be the one you assumed. It is as trusted as the repository holding it.

2. Query the existing index before rebuilding it. If one exists, run its integrity check and do not
   trust it if the check fails:

```bash
python3 .tools/query_code_index.py selftest
```

   An index is a cache, not the knowledge source. Do **not** rebuild merely because files may have
   changed. Rebuild only when it is missing, a branch/root mismatch or another check proves it stale,
   a query misses knowledge that the source KB demonstrably contains, or after you author knowledge
   or source changes that must be indexed.

3. Query claims before reading source deeply, especially disproved hypotheses:

```bash
python3 .tools/query_code_index.py claim <keywords> --dead-first
```

4. Query annotations, comments, symbols, and references before re-deriving behavior:

```bash
python3 .tools/query_code_index.py annotation <keywords>
python3 .tools/query_code_index.py comment <keywords>
python3 .tools/query_code_index.py symbol <name>
python3 .tools/query_code_index.py refs <name>
```

Use `--full` before relying on details from a compact result.

5. A KB miss is not proof that no precedent exists: follow the receipt's `retrieval_order` before
   concluding there is none, and check whether a maintained branch already has the infrastructure
   you are about to invent.

6. If a user supplies a durable fact that should have been found during bootstrap, treat that as a
   retrieval defect. Record the fact in the project KB or manifest and add a cold-session regression
   that proves the next context can recover it without the same prompt.

7. Re-run the read-only context receipt at a meaningful action boundary such as build, test,
   package, deploy, or release, retaining the exact requested action in `--task`. A canonical
   procedure in `applicable_procedures` OWNS that action: its argv/cwd is mandatory, and
   `relevant_procedures` only ranks them against your wording. *** AN EMPTY `relevant_procedures` IS
   NOT PERMISSION *** -- it usually means you phrased the task differently from the trigger. Commands
   listed as `diagnostic_only` may collect evidence but never substitute for the canonical action.
   Verify the declared success artifacts or markers before reporting completion.

## Record What You Learn

When you learn a durable fact, update the project KB annotations and rebuild the index. Record:

- The symptom or question that future agents will search for.
- What was measured, inferred, reported, or still open.
- Dead hypotheses with `status: "dead"` and `killed_by`.
- Cross-references with `see_also`, then verify with `broken-links`.

Do not turn uncertainty into certainty. If the next step is unknown, write the cheapest test that would settle it.

## Keep Boundaries Clean

Waymark engine work belongs in this repository:

- `.tools/index_code.py`
- `.tools/query_code_index.py`
- `.tools/serve_code_index.py`
- Waymark documentation and sample data

Product/project work belongs in the target project repository. Private project knowledge belongs in that project's untracked annotation files unless the project explicitly chooses to commit them.

Do not copy private KB content into Waymark. Do not make product-source changes in this repository unless the product source is the sample project.

## Handover Rule

When context is nearly exhausted, stop taking new work and hand over deliberately:

1. Put durable facts into the KB first, including open questions and failed hypotheses.
2. Rebuild the index and run `selftest`.
3. Write a short handover note that stands alone for the next agent.

The handover note should include:

- The one query that reloads the relevant KB entry.
- Repository and branch state.
- Committed, uncommitted, and unpushed work.
- Test/bench/device state, if any.
- What is proven, what is hypothesis, and the next concrete action.

Prefer an honest incomplete handover over finishing code that the next session cannot understand or trust.

## Review Standard

For code reviews, report findings first, ordered by severity, with file and line references. Treat failed `selftest`, stale indexes, broken links, missing provenance, and generated-file drift as real defects.
