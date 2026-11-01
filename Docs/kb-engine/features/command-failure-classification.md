---
concept_id: workflow.command-failure-classification
kind: feature
name: "Command failures must distinguish negative controls, detected defects, and invocation mistakes"
status: resolved
evidence: measured
---

## meaning

A session can display many nonzero exits that mean very different things. A deliberately broken
baseline is proof that a regression test is sensitive. A verifier rejecting malformed input is a
useful product or workflow finding. A command run from the wrong directory, with options in the
wrong position, or through fragile shell quoting is an avoidable invocation mistake. Reporting all
three merely as "failed commands" hides whether state changed and whether the result is evidence.

The recurring dangerous form combines a mutation and its verifier with shell chaining. The mutation
can succeed, the verifier can be invoked from the wrong directory, and the combined command then
looks wholly failed even though files were already rewritten. Recovery starts from a false premise.

Agent rules now require exact argv plus the documented cwd, separate mutation and verification
invocations, and advance labeling of expected negative controls. Project-specific paths remain in
the target manifest/tool documentation. Repeated path or quoting friction should be corrected in
that metadata or a reusable argv-based workflow, not memorized as another shell trick.
