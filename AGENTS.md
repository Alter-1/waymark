# Agent Instructions

## Use Waymark First

Before any investigation, bugfix, porting, release-note work, or test planning:

1. Identify the active repository and branch.
2. Refresh the index when source or annotations may have changed:

```bash
python3 .tools/index_code.py
```

3. Run the integrity check and do not trust the KB if it fails:

```bash
python3 .tools/query_code_index.py selftest
```

4. Query claims before reading source deeply, especially disproved hypotheses:

```bash
python3 .tools/query_code_index.py claim <keywords> --dead-first
```

5. Query annotations, comments, symbols, and references before re-deriving behavior:

```bash
python3 .tools/query_code_index.py annotation <keywords>
python3 .tools/query_code_index.py comment <keywords>
python3 .tools/query_code_index.py symbol <name>
python3 .tools/query_code_index.py refs <name>
```

Use `--full` before relying on details from a compact result.

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
