---
concept_id: workflow.automation
kind: feature
status: resolved
evidence: measured
name: "Reusable command plans, evidence snapshots, sparse worktrees and workflow review"
---

Repeated command orchestration and manual evidence bookkeeping benefit from a small
project-neutral CLI, while branch names, runtime topology and machine locations belong
to project configuration. `.tools/workflow.py` implements argv-based ordered plans,
exact expected exits, retained receipts, guarded resume, selected-file snapshots,
source-only worktrees and conservative comparison manifests. Windows process/UI/module
collection is an explicit companion script. No project paths belong in either tool.

Validation covers argument literals, expected failures, compilation failure stopping
later tests, timeout rejection, resume freshness, missing snapshot files, sparse
exclusions, preserving the source checkout, and retaining will-not-port decisions.
Initial testing found older Git lacks the sparse-checkout porcelain; the implementation
now installs sparse patterns through worktree-local configuration before read-tree.
Windows Git objects are read-only, so test cleanup must handle those attributes within
the allocated temporary test root only.

Periodic workflow review is an explicit AGENTS.md rule: revisit at natural milestones,
after repeated friction/failure and before completing substantial work. Improve an
existing procedure/tool first; distinguish a repeatable benefit from speculative
framework-building. Record larger out-of-scope opportunities rather than silently
expanding a task. See Docs/AUTOMATION.md for contracts and limits.
