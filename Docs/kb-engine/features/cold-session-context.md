---
concept_id: workflow.cold-session-context
kind: feature
name: "Cold sessions need a project topology and retrieval receipt before they can use the KB reliably"
symbols: ["project_context", "discover_context_manifest"]
status: resolved
evidence: measured
---

## meaning

"Use the KB first" was not enough for a repository family. A new context knew only its current
directory; it did not know which sibling worktrees existed, which branch each owned, which paths
were authoritative knowledge versus generated caches, or why an already-dirty tool directory was
intentional. When the KB missed a fact, no rule required checking maintained branches before
inventing a replacement. The instruction to refresh whenever source "may have changed" also made an
expensive index rebuild the default even though the index is only a cache.

`workflow.py context` now discovers a generic `waymark.project.json`, validates every declared Git
checkout and required resource, labels indexes as caches, and emits a read-only receipt. A satellite
checkout can extend one canonical manifest and select its repository id; arbitrary overrides and
include cycles are rejected. Existing SQLite indexes are opened immutable and read-only so bootstrap
cannot create WAL/SHM sidecars. Matching root/branch metadata is reported as `present-unverified`,
not as proof of freshness; mismatches are `stale`.

Agent rules now require context/KB/tool/history/source retrieval order, query-before-rebuild, and a
cold-session regression when a user has to supply a durable fact that bootstrap should have found.
Private project topology belongs in that project's manifest and KB, never in this public entry.

## notes

`tests/test_workflow.py` creates two real Git repositories, discovers a satellite manifest from a
nested directory, validates the selected repository and cache authority, and proves the SQLite
index mtime is unchanged. Separate tests cover branch/resource errors and include cycles.
