---
claims: [{"status": "dead", "evidence": "measured", "date": "2026-08-24", "text": "Keyword-profile similarity will identify which KB labels are the same category -- two labels whose entries keep the same company are the same category, so an IDF-weighted cosine over their keywords should separate plan/todo from unrelated pairs.", "killed_by": "Run on a 280-entry KB with 40 prefixes and 34 kinds: EVERY pair scored 0.10-0.13 whether related or not. bug/perf 0.12 and bug/test 0.10 -- that is the noise floor, not a signal.", "revive_if": "the KB's keywords are GENERAL rather than entry-specific. This failed because real keywords are symbol names, dates and board identifiers, so after IDF there is almost no shared vocabulary left between labels. A KB with categorical keywords, or a comparison over entry PROSE instead of keywords, would give the method something to measure. Retest before concluding it cannot work anywhere -- it was not the idea that failed, it was the input."}, {"status": "live", "evidence": "measured", "date": "2026-08-24", "text": "SPELLING similarity works, and the long tail is the real finding: 22 of 40 prefixes and 19 of 34 kinds hold two entries or fewer. The drift is not mainly synonyms, it is one-off labels invented in passing -- which needs no algorithm at all."}]
concept_id: engine.kb.taxonomy_clustering
evidence: measured
files:
  - .tools/kb_taxonomy.py
keywords:
  - taxonomy
  - synonym
  - namespace drift
  - clustering
  - keyword profile
  - distributional
  - cosine
  - IDF
  - dead end
  - did not work
  - kb_taxonomy
kind: feature
name: taxonomy-clustering-does-not-work
see_also: [{"type": "annotation", "target": "kb-file-vs-directory-assumptions", "note": "the other engine finding of the same week"}]
status: resolved
---

## brief

A RECORDED DEAD END, with the condition it depends on. Keyword-profile similarity was proposed to
find which KB labels are the same category without an LLM. It does not work on a real KB -- but for
a reason that is a property of the INPUT, not of the idea, so the entry says what would revive it.

## notes

The tool is kept (`.tools/kb_taxonomy.py`) with the negative result documented inside it, because
the next person will have the same idea and a recorded dead end is cheaper than re-deriving one.

WHAT WORKS IN IT: spelling similarity (found resolved/resolved_bug, procedure/test_procedure,
invariant/test_invariant immediately, with one false positive), and the long-tail listing, which
needed no algorithm and turned out to be the actual finding.

THIS ENTRY IS ALSO THE WORKED EXAMPLE for `revive_if`: a dead end dies in a context, and the field
records the context rather than only the death. `query_code_index.py claim --revivable` lists the
ones that told you what they depended on.
