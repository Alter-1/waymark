#!/usr/bin/env python3
"""Split a single-document KB into one file per entry, or join it back.

    python3 .tools/kb_split.py Docs/source_index_annotations.json Docs/kb      # split
    python3 .tools/kb_split.py --join Docs/kb Docs/source_index_annotations.json

WHY SPLIT. One JSON document cannot be merged -- two people editing DIFFERENT entries still collide
on the same file -- and it destroys per-fact history: `git log` over a 1 MB blob cannot say when one
note became true. It is also why notes get written through heredocs, since prose inside a JSON
string arrives full of \n escapes and diffs as a single line.

IT REFUSES TO WRITE ANYTHING IT CANNOT READ BACK. The split is verified in memory first -- write,
re-read, compare entry by entry -- and nothing is written to disk unless the two agree exactly. A
migration that loses one field is worse than no migration, because the loss is silent and the
original gets deleted afterwards.
"""
import argparse
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

TOOLS = Path(__file__).resolve().parent


def engine():
    spec = importlib.util.spec_from_file_location("index_code", TOOLS / "index_code.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["index_code"] = mod
    spec.loader.exec_module(mod)
    return mod


def same(a: dict, b: dict) -> list:
    """Entry-by-entry, order-independent. Returns the collections that differ."""
    bad = []
    for key in set(a) | set(b):
        if key == "collections":
            continue
        x, y = a.get(key), b.get(key)
        if isinstance(x, list) or isinstance(y, list):
            k = lambda e: json.dumps(e, sort_keys=True, ensure_ascii=False)
            if sorted(x or [], key=k) != sorted(y or [], key=k):
                bad.append(key)
        elif x != y:
            bad.append(key)
    return bad


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source")
    ap.add_argument("dest")
    ap.add_argument("--join", action="store_true", help="directory -> single document")
    a = ap.parse_args()
    mod = engine()
    src, dst = Path(a.source), Path(a.dest)

    if a.join:
        data = mod.read_kb_dir(src)
        data.pop("collections", None)
        dst.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print("joined %s -> %s" % (src, dst))
        return 0

    data = json.loads(src.read_text(encoding="utf-8"))
    # DRY RUN FIRST, into a temp directory nobody will look at: if the round trip is not exact the
    # real directory is never created, so a failed migration leaves no half-split KB behind.
    with tempfile.TemporaryDirectory() as td:
        probe = Path(td) / "kb"
        mod.write_kb_dir(data, probe)
        bad = same(data, mod.read_kb_dir(probe))
    if bad:
        print("REFUSING TO SPLIT: these do not survive a round trip: %s" % ", ".join(bad),
              file=sys.stderr)
        return 1
    n = mod.write_kb_dir(data, dst)
    print("split %s -> %s/  (%d entries, round trip verified)" % (src, dst, n))
    print("now rebuild and compare before deleting the original:")
    print("  python3 .tools/index_code.py --force")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
