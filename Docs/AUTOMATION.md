# Reusable workflow helpers

`.tools/workflow.py` is a standard-library Python 3.7+ CLI. It has no project names,
drive letters, registry keys, or deployment targets. Machine paths belong in local
JSON files, excluded from version control. Plans use argument arrays, never shell
command strings. Paths in a plan are relative to that plan's directory unless absolute.

## Cold-session context

`python .tools/workflow.py context --cwd . --task "request"` discovers
`waymark.project.json` from the current directory upward and emits a read-only context receipt.
It does not run configured tools or rebuild an index. The manifest format, canonical/satellite
layout, validation rules, and limitations are documented in [`CONTEXT.md`](CONTEXT.md).

## Command plans and before/after probes

Run `python .tools/workflow.py run workflow.local.json --out EVIDENCE_DIRECTORY`.
The output directory must be new. A minimal local plan is:

```json
{
  "inputs": ["probe.py"],
  "repositories": [{"path": ".", "branch": "working-branch"}],
  "steps": [
    {"id": "compile", "argv": ["python", "compile_probe.py"], "expect": 0},
    {"id": "before", "argv": ["python", "probe.py", "--before"], "expect": 1},
    {"id": "after", "argv": ["python", "probe.py"], "expect": 0}
  ]
}
```

List every relevant source/fixture under `inputs` when using resume. An expected
baseline failure must be an exact exit code from the test, after a separate successful
compilation step. Timeout and process-start failure never count as expected failure.
The first failed step stops dependent work. Logs and `receipt.json` remain. Commands
can use the literal `{evidence}` placeholder for their output paths.

`--resume` skips only previously successful steps and rejects changed plans, declared
inputs or recorded repository state. It does not discover undeclared dependencies.
Steps must be safe to rerun after partial failure; use a new evidence directory after
source changes. This runner is a journal, not a transaction across external tools.

Use the same runner for register update → mirror → index → integrity check → export.
Project wrappers supply the actual commands and database paths. A failed integrity
check must remain visible; it must not be converted into a successful completion.

## Evidence snapshots

`python .tools/workflow.py snapshot snapshot.local.json --out EVIDENCE_DIRECTORY`
records Git branch/HEAD/dirty status and selected files. Each file entry has `path`
and a mode: `metadata`, `hash`, `tail` (optional byte limit), or `copy`. Missing files
produce recorded errors and a nonzero exit while preserving available evidence.
This is a live best-effort snapshot, not a consistent database backup. Use a database's
backup API for live databases. No environment variables or process arguments are collected.

On Windows, `.tools/windows_snapshot.ps1 -ProcessIds 123,456 -OutputFile result.json`
captures selected process paths, window titles and modules. `-Native32` uses the
32-bit collector for COM hosts. `-IncludeUiText` explicitly adds selected processes'
UI Automation text, including owned dialogs. UI text can contain private user data;
evidence is local and is not suitable for automatic publication. The tool neither
clicks dialogs nor changes application state. UI Automation calls may wait on an
unresponsive provider; wrap collection in a command-plan timeout when necessary.

## Source-only worktrees

```text
python .tools/workflow.py worktree --repo REPOSITORY --dest NEW_DIRECTORY
  --ref SOURCE_REF --branch NEW_BRANCH --include src --include tools --exclude src/vendor
```

Supply literal repository-relative **directories**. The destination must not exist.
The helper creates the worktree with `--no-checkout`, installs sparse patterns, then
populates only the selected directories. It supports older Git through worktree-local
`core.sparseCheckout` and enables `extensions.worktreeConfig` in the shared repository.
It does not switch the source checkout. Failure retains a partial worktree for inspection;
it never recursively removes an existing destination. Do not use a full checkout first
and then exclude bundled environments: the expensive extraction already happened.

## Cross-branch comparison

`python .tools/workflow.py compare compare.local.json --out EVIDENCE_DIRECTORY`
accepts `repositories` (name → local path) and `items`. Each item has `id`, `source`,
`files` (repository-relative paths), and `targets` (name → policy). A target policy
can contain `decision: "will-not-port"` and a required `reason`, or an optional
`evidence` link. The output records exact HEADs/dirty status and byte hashes.

All non-excluded items remain `review-required`, even if files match. Different file
hashes do not mean a fix is missing, and matching files do not prove caller behavior.
Use content/symbol audits and actual regression tests to decide port status. No
automatic cherry-pick, register certification, or publication happens here.

## Workflow review

Follow the periodic workflow-review rule in [AGENTS.md](../AGENTS.md). Prefer improving
an existing helper over duplicating it. Record the observed friction, chosen change,
failure-case validation, and whether the next real use removed time, steps or errors.

Validation: `python -m unittest discover -s tests -p test_workflow.py -v`.
