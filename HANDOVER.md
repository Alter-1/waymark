# Handover Workflow

Waymark reduces repeated investigation, but it does not replace handover. A KB entry is structured
memory; a handover is the launch point for the next agent or developer.

Use a handover when context is low, work is paused mid-investigation, hardware state matters, or the
next action depends on what was already ruled out.

## Order

1. Record durable facts in the KB first.
2. **Close what is finished.** `query_code_index.py claim --status open --until <a-few-weeks-ago>`
   — a claim is written where the work is and resolved where the evidence lands, which is usually a
   different place, and nothing walks back to close it. An item can sit open for days after being
   done and send the next session to redo it. Claims of *absence* ("not yet measured", "still
   unfixed") rot fastest, because any work at all makes them false.
3. Rebuild the index.
4. Run `selftest`.
5. Write the handover note.

**And hold the handover note to the same standard.** Its open list rots exactly like a claim does:
one item here read "still never done" for three days after the run it named had passed.

Do not write only a free-form handover when the information belongs in the KB. A handover is easy to
lose; a queryable claim with keywords is the durable record.

## KB First

Record facts as annotations and claims:

```json
{
  "name": "example-freeze-investigation",
  "status": "open",
  "evidence": "mixed",
  "keywords": ["freeze", "save", "network", "symptom-user-will-search"],
  "brief": "Short statement of the current state.",
  "notes": "What is known, what was measured, and the cheapest next test.",
  "claims": [
    {
      "text": "Hypothesis X does not explain the failure.",
      "evidence": "measured",
      "status": "dead",
      "date": "YYYY-MM-DD",
      "killed_by": "Reproduced with X disabled."
    }
  ],
  "see_also": ["symbol:some_function", "file:src/module.cpp"]
}
```

Then run:

```bash
python3 .tools/index_code.py
python3 .tools/query_code_index.py selftest
```

If `selftest` fails, either fix it or state the failure explicitly in the handover.

## Handover Template

```md
# Handover: <short topic>

Reload:
python3 .tools/query_code_index.py --full annotation <entry-name>

Repo state:
- Repo: <path>
- Branch: <branch>
- Committed: <what commit/subject>
- Uncommitted: <files and why>
- Pushed: <yes/no/unknown>

Bench/runtime state:
- Devices: <hosts, ports, firmware versions>
- Physical wiring: <what is connected>
- Non-default config: <important state left behind>
- Running processes: <capture scripts, servers, locks>

Known facts:
- <measured fact>
- <measured fact>

Dead hypotheses:
- <claim already recorded in KB, or inline if not yet recorded>

Open questions:
- <question>
- <question>

Next action:
- <single concrete next command/test/edit>
```

## What Belongs Where

- KB annotation: durable facts, claims, links, procedures, known traps.
- Handover: current working state, hardware state, branch state, exact next action.
- Git commit message: what changed and why the change belongs in history.
- Scratch file: temporary data only; promote anything reusable to the KB before stopping.

## Useful Queries For Resuming

```bash
python3 .tools/query_code_index.py claim <topic> --dead-first
python3 .tools/query_code_index.py --full annotation <topic>
python3 .tools/query_code_index.py broken-links
python3 .tools/query_code_index.py selftest
```
