---
concept_id: workflow.action-boundary-procedures
kind: feature
name: "Cold-session context must retrieve the canonical procedure again when an action starts"
symbols: ["project_context", "_procedure_entries"]
status: resolved
evidence: measured
---

## meaning

Initial context retrieval can correctly identify repositories and tools yet still be forgotten by
the time an agent moves from diagnosis to build, test, package, deploy, or release. A diagnostic
helper may look close enough to the real workflow and exit successfully while producing none of the
artifact the user expects. A prose warning alone does not recover the exact argv, working-directory
ownership, or success evidence at that decision point.

Project manifests can now declare general `procedures`: task triggers, applicable repository ids,
canonical argv/cwd, explicitly non-substituting diagnostic commands, and expected artifacts or
markers. The read-only context receipt returns every declaration plus `relevant_procedures` matched
to the exact task and selected repository. Agent instructions require a fresh receipt at meaningful
action boundaries and verification of declared evidence.

*** THE OBLIGATION IS NOT KEYED TO THE AGENT'S OWN WORDING. *** `triggers` are matched by substring
against task text the caller writes about itself, so a paraphrase returns an empty
`relevant_procedures` -- and an empty list at a build boundary reads as "nothing owns this, use your
judgement", which is the failure the feature exists to prevent. `applicable_procedures` answers the
question the boundary actually asks -- what owns an action in THIS checkout -- and carries the
obligation; matching only ranks. `--task` is required for the same reason: its former default of
`''` made every match false, so the cheapest invocation disarmed the rule silently and exited 0.

The engine contains no product path or private topology. Those facts remain in the target project's
manifest. `tests/test_workflow.py` proves a release-build request retrieves the canonical procedure,
preserves its diagnostic-only distinction, and does not return it for unrelated source inspection.
