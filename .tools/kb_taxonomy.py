#!/usr/bin/env python3
"""Propose which KB labels are the same category, so a drifting namespace can be tidied.

WHY THIS EXISTS. A KB grown by hand drifts: `behavior` and `behaviour` both appear, so do `ref` and
`reference`, and forward-looking work ends up split across `plan`, `todo`, `open` and `lead` with no
rule between them. Every kind-based query then answers for one spelling and silently misses the
others.

WHY NOT STRING DISTANCE ALONE. It finds `behaviour`/`behavior` and `ref`/`reference` and will NEVER
find `plan`/`todo` -- lexically unrelated, semantically identical, and the ones that actually hurt.

WHAT THIS DOES INSTEAD. Entries already carry `keywords`. Two labels whose entries keep the same
company are the same category: build a keyword profile per label and compare them. That is
distributional similarity without a model -- a Counter and a dot product.

IDF IS WHAT MAKES IT WORK. Without it every profile is dominated by the words that appear
everywhere (a platform name, a year, the project's own nouns) and everything looks similar to
everything. Weighting each keyword by how FEW labels use it leaves only the discriminating terms.

MEASURED RESULT, AND THE CLEVER HALF LOST. Run against a 280-entry KB with 40 prefixes and 34
kinds, the distributional column produced NOISE: every pair scored 0.10-0.13 whether related or not,
because keywords in a real KB are entry-specific -- symbol names, dates, board identifiers -- so
after IDF there is almost no shared vocabulary between any two labels to measure. The idea is sound
where keywords are general; it does not survive keywords that are specific, which is what a useful
KB has. The column is kept, labelled honestly, and should not be trusted below ~0.35.

WHAT DID WORK, and it is the boring part:
  * SPELLING similarity found real folds immediately -- resolved/resolved_bug, procedure/
    test_procedure, invariant/test_invariant -- with one false positive (protocol_channel/
    protocol_header are genuinely different), which is a fine ratio for a human-reviewed list.
  * THE LONG TAIL was the actual finding. 22 of 40 prefixes and 19 of 34 kinds hold two entries or
    fewer. The drift is not mainly synonyms; it is one-off labels invented in passing. That list
    needs no algorithm at all, and it is the thing worth acting on.

IT PROPOSES; IT NEVER RENAMES. Half the labels here hold one or two entries, where the honest answer
is "this was a one-off, fold it somewhere general" -- a judgement about intent, not similarity. The
output is a ranked list for a human to accept or reject.

    python3 .tools/kb_taxonomy.py [--db PATH] [--field prefix|kind] [--top N]
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def load(db: Path, field: str) -> dict:
    con = sqlite3.connect(str(db))
    groups: dict = defaultdict(list)
    seen = set()
    for name, value in con.execute("SELECT name, value FROM annotations"):
        if name in seen:
            continue
        seen.add(name)
        try:
            d = json.loads(value)
        except ValueError:
            continue
        if field == "prefix":
            cid = str(d.get("concept_id") or "")
            label = cid.split(".")[0] if cid else ""
        else:
            label = str(d.get("kind") or "")
        if not label:
            continue
        kws = [str(k).strip().lower() for k in (d.get("keywords") or []) if str(k).strip()]
        groups[label].append((name, kws))
    con.close()
    return groups


def profiles(groups: dict) -> dict:
    """One IDF-weighted keyword vector per label."""
    n = len(groups)
    doc_freq: Counter = Counter()
    for label, entries in groups.items():
        for kw in {k for _, kws in entries for k in kws}:
            doc_freq[kw] += 1
    out = {}
    for label, entries in groups.items():
        tf: Counter = Counter()
        for _, kws in entries:
            tf.update(kws)
        vec = {}
        for kw, c in tf.items():
            # a keyword used by nearly every label says nothing about category
            idf = math.log(n / (1 + doc_freq[kw]))
            if idf > 0:
                vec[kw] = (1 + math.log(c)) * idf
        out[label] = vec
    return out


def cosine(a: dict, b: dict) -> float:
    if not a or not b:
        return 0.0
    common = set(a) & set(b)
    if not common:
        return 0.0
    num = sum(a[k] * b[k] for k in common)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return num / (na * nb) if na and nb else 0.0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", default="")
    ap.add_argument("--field", choices=["prefix", "kind"], default="prefix")
    ap.add_argument("--top", type=int, default=18)
    a = ap.parse_args()

    db = Path(a.db) if a.db else None
    if db is None:
        cands = sorted((REPO_ROOT / ".tools").glob("code_index*.sqlite"))
        cands = [c for c in cands if not c.name.endswith(".tmp")]
        if not cands:
            print("no index found -- run index_code.py first")
            return 2
        db = cands[0]

    groups = load(db, a.field)
    if not groups:
        print("no labels found for field=%s" % a.field)
        return 1
    vecs = profiles(groups)

    print("  %d labels over %d entries (field=%s)\n"
          % (len(groups), sum(len(v) for v in groups.values()), a.field))

    pairs = []
    labels = sorted(groups)
    for i, x in enumerate(labels):
        for y in labels[i + 1:]:
            sem = cosine(vecs[x], vecs[y])
            lex = SequenceMatcher(None, x, y).ratio()
            if sem > 0.10 or lex > 0.72:
                pairs.append((max(sem, lex * 0.9), sem, lex, x, y))
    pairs.sort(reverse=True)

    print("  CANDIDATE MERGES -- lex = spelling (RELIABLE), sem = distributional (NOISY below ~0.35;")
    print("  see the module docstring -- real KB keywords are too entry-specific for it to work)")
    print("  %-22s %-22s %5s %5s  %s" % ("label", "label", "lex", "sem", "sizes"))
    for _, sem, lex, x, y in pairs[:a.top]:
        flag = "SPELLING" if lex > 0.72 else ("meaning" if sem > 0.10 else "")
        print("  %-22s %-22s %5.2f %5.2f  %2d/%-2d  %s"
              % (x, y, lex, sem, len(groups[x]), len(groups[y]), flag))
    if not pairs:
        print("  (none above threshold)")

    tiny = sorted((l for l in labels if len(groups[l]) <= 2), key=lambda l: (len(groups[l]), l))
    print("\n  ONE- AND TWO-ENTRY LABELS -- usually 'fold into something general', a judgement call")
    print("  " + ", ".join("%s(%d)" % (l, len(groups[l])) for l in tiny))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
