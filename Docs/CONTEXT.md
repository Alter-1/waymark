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

## Trust

*** A MANIFEST IS EXACTLY AS TRUSTED AS THE REPOSITORY IT SITS IN, AND NO MORE. *** Discovery walks
UPWARD from `--cwd` and takes the first `waymark.project.json` it finds, so the file that wins may
belong to a parent directory rather than to the checkout you have in mind; a satellite's `extends`
resolves to any path on disk. The receipt names the file it used in `entry_manifest` and `manifest`,
and an agent following these rules should say which one it obeyed.

This matters because a manifest is not inert. `procedures` declare argv that agent rules treat as
owning an action, and `run_plan` executes argv arrays directly. Clone an unfamiliar repository, start
an agent inside it, and its manifest can name the commands that agent is told to use. Nothing here is
sandboxed and nothing tries to be.

`context` itself does not execute `canonical.argv`; it reads. But reading is not nothing: it runs
`git` inside each declared repository path, and git reads that repository's own configuration; it
opens declared SQLite indexes, which means parsing files the manifest chose. Both are ordinary,
bounded operations on paths somebody else may have written down.

So: **read a manifest before acting on it in a repository you do not control**, exactly as you would
read a `Makefile` before running `make`. In a repository family you maintain, this is a non-issue --
which is the case the feature was built for.

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
