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

*** THE OBLIGATION IS NOT KEYED TO THE AGENT'S OWN WORDING. *** Trigger phrases are normalized
across ordinary punctuation and command separators, then matched in order against the task text so
qualifiers such as a version may occur between trigger words. A generic
trigger may also match a more specific one (for example, `build VEO` inside `build VEO setup`), so
`relevant_procedures` is ranked by trigger specificity and `selected_procedure` is set only when the
highest-ranked match is unique. If no trigger matches, or the top rank ties, the caller must stop
and clarify; an empty list is not permission and a repository-wide declaration is not a license to
guess. `applicable_procedures` is the checkout's inventory, not the action selection. `--task` is
required for the same reason: its former default of `''` made every match false, so the cheapest
invocation disarmed the rule silently and exited 0.

The engine contains no product path or private topology. Those facts remain in the target project's
manifest. `tests/test_workflow.py` proves a release-build request retrieves the canonical procedure,
preserves its diagnostic-only distinction, selects the most-specific setup procedure over a generic
build, and does not return it for unrelated source inspection.
