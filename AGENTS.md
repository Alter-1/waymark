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
   paths. Report conflicts or missing required context instead of silently choosing one.

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

5. A KB miss is not proof that no precedent exists. Search in this order: instruction files,
   project manifest, authoritative KB sources, task-specific tool documentation, cross-branch and
   related-repository history, then product source. Before creating infrastructure or workflow,
   check whether another maintained branch already contains it.

6. If a user supplies a durable fact that should have been found during bootstrap, treat that as a
   retrieval defect. Record the fact in the project KB or manifest and add a cold-session regression
   that proves the next context can recover it without the same prompt.

7. Re-run the read-only context receipt at a meaningful action boundary such as build, test,
   package, deploy, or release, retaining the exact requested action in `--task`. If the receipt
   returns a matching `relevant_procedures` entry, its canonical argv/cwd is mandatory. Commands
   listed as `diagnostic_only` may collect evidence but do not substitute for the canonical action.
   Verify the procedure's declared success artifacts or markers before reporting completion.

8. Execute a documented command from its declared working directory and preserve its argv rather
   than reconstructing a shell string. Run a state-changing command and its verifier as separate
   invocations: if verification is misconfigured or fails, the receipt must still make clear that
   the mutation already happened. Label an intentional negative-control failure as expected before
   running it; do not present it like an operational error.

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

## Review the Workflow Periodically

At meaningful milestones, after repeated friction or an avoidable failure, and before completing a
substantial task, review how the work is being done. Do not interrupt a live debugging step merely
to run this review; record an observation and revisit it at the next natural checkpoint.

Look for repeated commands, manual bookkeeping, slow or excessive output, duplicated test setup,
environment assumptions, and mistakes that a check could prevent. Decide whether to simplify the
procedure, improve an existing tool, add reusable automation, document a rule, or leave it manual.
Prefer small improvements justified by observed repetition or risk; do not build a framework for
a one-off operation. Review outcomes and remaining opportunities belong in the KB.

Separate public, project-neutral helpers from project-specific workflows. Keep machine paths and
environment-specific values in ignored local settings, with portable example templates. Reuse
existing tools before adding another one. Validate failure and recovery behavior as well as success;
automation must retain evidence and must not equate a successful command with verified behavior.
Implement improvements already within the user's authorized scope. Record larger changes for
review rather than silently expanding the task. Measure whether an improvement actually removes
steps, time, or errors when it is next used.
