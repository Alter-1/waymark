# Cold-session project context

`workflow.py context` is a read-only bootstrap for a family of related repositories. It answers
which checkout a task started in, which branches and upstreams are expected, where authoritative
knowledge and generated indexes live, which tool documentation applies, and which dirty paths have
an intentional purpose. It never rebuilds an index.

Run it from anywhere below a repository containing `waymark.project.json`:

```bash
python3 .tools/workflow.py context --cwd . --task "the user's request"
```

Add `--out context.json` for a durable receipt. A required missing path, branch mismatch, or upstream
mismatch is reported in `errors` and makes the CLI exit nonzero. Dirty repositories are information,
not an error; the receipt gives a count and at most twenty preview lines.

## Canonical manifest

Paths are relative to the canonical manifest unless absolute. Machine-specific manifests may be
ignored local files; portable projects may commit them. Keep private topology in the target project,
never in Waymark's public rules or examples.

```json
{
  "schema_version": 1,
  "project": "example-suite",
  "repositories": [
    {
      "id": "service",
      "path": ".",
      "role": "server",
      "branch": "main",
      "upstream": "origin/main"
    },
    {
      "id": "client",
      "path": "../client",
      "role": "desktop client",
      "branch": "release"
    }
  ],
  "instructions": ["AGENTS.md"],
  "knowledge_roots": [
    {"path": "Docs/kb", "authority": "source"}
  ],
  "indexes": [
    {
      "path": ".tools/code_index.sqlite",
      "repository_id": "service",
      "authority": "cache",
      "required": false
    }
  ],
  "tools": [
    {
      "path": ".tools/query_code_index.py",
      "documentation": "README.md",
      "cwd": ".",
      "mutation": "read-only"
    }
  ],
  "procedures": [
    {
      "id": "release-build",
      "triggers": ["build release", "release build"],
      "repository_ids": ["client"],
      "canonical": {
        "argv": ["tools/build.cmd", "Release"],
        "cwd": "repository"
      },
      "diagnostic_only": [
        {
          "argv": ["python", "tools/compile_probe.py"],
          "reason": "isolated evidence only; does not produce the release artifact"
        }
      ],
      "success": {
        "artifacts": ["bin/Release/client.exe"],
        "markers": ["build.ok"]
      }
    }
  ],
  "protected_paths": [
    {"path": ".tools", "reason": "project knowledge and guard tooling"}
  ]
}
```

Every repository needs a unique `id`. `branch` and `upstream` are optional assertions. Resources are
required by default; set `"required": false` only when absence is genuinely supported. Indexes are
opened with SQLite immutable read-only mode, which also prevents WAL/SHM sidecar creation. Matching
metadata yields `present-unverified`, never `fresh`: silence is not proof that source and knowledge
have not changed. A recorded branch or root mismatch yields `stale`.

Procedures make action ownership explicit without executing anything. `triggers` are
case-insensitive phrases matched against the retained task, and `repository_ids` constrain a
procedure to applicable checkouts. The canonical command is an argv array and uses
`"cwd": "repository"`; shell strings are not accepted. Diagnostic-only alternatives must state why
they are not substitutes. At build/test/package/deploy/release boundaries, request a new context
receipt with the exact action, use any returned `relevant_procedures`, and verify its declared
artifacts or markers.

## Satellite manifest

Keep one topology rather than copying it into every checkout. A related repository can carry only a
pointer and its own identity:

```json
{
  "schema_version": 1,
  "extends": "../service/waymark.project.json",
  "repository_id": "client"
}
```

The pointer may set only `repository_id`; other overrides are rejected so local copies cannot drift
silently. Include cycles are rejected.

## What bootstrap does not do

It does not execute declared tools, query prose semantically, rebuild caches, fetch remotes, switch
branches, or certify that an index is current. It produces the context receipt needed to perform
those actions deliberately and in the documented order.
