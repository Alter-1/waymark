#!/usr/bin/env python3
"""Tests for the CSS factoring tool.

Every case here is either a wrong answer the tool once gave, or a refusal whose absence would
change what a page RENDERS -- which is the failure this tool exists to avoid. The last test is the
important one: it does not check the output text, it checks that every selector still resolves to
the same declarations it had before.

    python3 tests/test_css_factor.py

Standard library only, no test framework, exit code 1 on failure.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".tools"))

import css_factor as cf

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s   %s" % (name, detail))
        FAILED.append(name)


def cands(css, min_save=1):
    return cf.find(cf.parse(css), min_save)


def accepted(css, min_save=1):
    return [c for c in cands(css, min_save) if not c.refusal]


def refused(css):
    return [c for c in cands(css) if c.refusal]


def resolved(css):
    """selector -> {prop: (value, important)} in cascade order, the thing that must not change."""
    out = {}
    for r in cf.parse(css):
        for sel in r.selectors:
            cur = out.setdefault((r.ctx, sel), {})
            for prop, val, imp, _raw in r.decls:
                cur[prop] = (val, imp)
    return out


# --- it finds the plain case, and measures it in minified bytes -------------------------------
css = ".a { display: inline-block; min-width: 28vw; }\n.b { display: inline-block; }\n"
acc = accepted(css)
check("finds a shared declaration", len(acc) == 1 and acc[0].key == (("display", "inline-block", False),))
check("selectors in document order", acc and acc[0].selectors == [".a", ".b"], acc and acc[0].selectors)
# Two copies of "display:inline-block;" (21 B each) against ".a,.b{" + the declaration + "}".
# The model charges the trailing ';' on both sides, so the figure is a lower bound by one byte --
# asserted here so that conservatism stays deliberate rather than drifting into an off-by-one.
check("saving is minified bytes, conservatively",
      acc and acc[0].saving == 2 * 21 - (len(".a,.b") + 21 + 2), acc and acc[0].saving)

# --- refusals: each of these would change rendering -------------------------------------------
check("refuses when a third rule sets the property",
      any("also set by" in c.refusal for c in refused(".a{color:red}.b{color:red}.c{color:blue}")))
check("refuses shorthand vs longhand",
      any("also set by" in c.refusal for c in refused(".a{margin:1px}.b{margin:1px}.c{margin-top:2px}")))
check("refuses mixed !important",
      not accepted(".a{color:red}.b{color:red !important}"))
check("never factors custom properties",
      not accepted(".a{--x:1}.b{--x:1}"))
check("does not group across @media",
      not accepted(".a{color:red}@media print{.b{color:red}}"))

# --- the parser must not be fooled by CSS that contains its own punctuation --------------------
tricky = '.a{content:"x; y: z";color:red}.b{background:url(data:image/png;base64,A;B);color:red}'
acc = accepted(tricky)
check("factors past a string and a data: url", len(acc) == 1 and acc[0].key[0][0] == "color")
fixed, n = cf.apply(tricky, cands(tricky))
check("string survives --fix", 'content:"x; y: z"' in fixed, fixed)
check("data: url survives --fix", "url(data:image/png;base64,A;B)" in fixed, fixed)

# --- rewriting ---------------------------------------------------------------------------------
css = ".a{display:inline-block;min-width:28vw}.b{display:inline-block}"
fixed, n = cf.apply(css, cands(css))
check("emits one combined rule", fixed.count("{") == 2, fixed)
check("drops the rule left empty", ".b{}" not in fixed, fixed)
check("rewriting is smaller", len(fixed) < len(css), "%d vs %d" % (len(fixed), len(css)))

# --- THE ONE THAT MATTERS: nothing a selector resolves to may change ---------------------------
for name, css in (
    ("simple",   ".a{display:inline-block;min-width:28vw}.b{display:inline-block}"),
    ("three",    ".a{color:red;padding:1px}.b{color:red}.c{color:red;margin:2px}"),
    ("multi",    ".a{top:0;left:0;width:100%}.b{top:0;left:0;width:100%;height:3px}"),
    ("mixed",    ".a{color:red}.b{color:red}.c{background:blue}.d{background:blue}"),
    ("tricky",   tricky),
):
    before = resolved(css)
    after = resolved(cf.apply(css, cands(css))[0])
    check("resolution unchanged (%s)" % name, before == after,
          "\n     before %s\n     after  %s" % (before, after))

# --- a comment above a rule explains THAT rule; folding must not orphan it ---------------------
commented = ("/* why .pt exists, in four lines nobody wants to lose */\n"
             ".pt { border-collapse: collapse; }\n"
             ".wsl-t table { border-collapse: collapse; margin-top: 0.5vw; }\n")
check("refuses to orphan an explanatory comment",
      any("orphan that comment" in c.refusal for c in refused(commented)),
      [c.refusal for c in cands(commented)])
check("and therefore changes nothing", cf.apply(commented, cands(commented))[0] == commented)
# the same sheet WITHOUT the comment is fair game again
check("folds the same rules when no comment is attached",
      len(accepted(commented.split("\n", 1)[1])) == 1)

# --- --fix must not minify a sheet a person maintains -----------------------------------------
multi = (".a {\n    color: red;\n    padding: 1px;\n}\n.b {\n    color: red;\n}\n")
fixed, _n = cf.apply(multi, cands(multi))
check("keeps the authored multi-line shape", "\n    color: red;\n" in fixed, fixed)
check("keeps the authored indentation", "    padding: 1px;" in fixed, fixed)
one = ".a { color: red; padding: 1px; }\n.b { color: red; }\n"
fixed1, _n = cf.apply(one, cands(one))
check("keeps a one-line rule on one line", "\n" not in fixed1.strip().split("}")[0], fixed1)
check("declarations are reproduced as authored, not rebuilt",
      "color: red" in fixed1 and "color:red" not in fixed1, fixed1)

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("all passed")
