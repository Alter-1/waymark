#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
css_factor - which declarations in a stylesheet are repeated, and is factoring them SAFE?

WHY NOT JUST GROUP THE SELECTORS
--------------------------------
"These three rules all say `display: inline-block`, so give them one selector list" is right often
enough to be dangerous. Moving a declaration moves its position in the cascade, and CSS resolves a
conflict by ORDER and SPECIFICITY, not by intent. If anything else in the sheet sets the same
property for an element that also matches one of the grouped selectors, factoring can change what
that element renders as -- silently, on one browser width, in one tab nobody opened during review.

So this tool answers two questions and keeps them apart:

    HOW MUCH would factoring save, in the bytes that actually ship?
    CAN it be done without changing the cascade?

It refuses far more than it accepts, and it says why for every refusal. The refusals are the part
worth reading: they are where the sheet is telling you something about itself.

WHAT IT MEASURES
----------------
Minified bytes, because that is what a stylesheet costs. Authored indentation is free -- any
compactor strips it -- so a declaration written five times costs five copies of `prop:value;` and
nothing more. Reported savings are what remains after the whitespace a minifier would remove anyway,
which is usually far less than the authored diff suggests.

The figure is a LOWER BOUND by one byte per rule: the model charges a ';' for the last declaration
of every rule, which a minifier drops. Deliberately conservative -- a tool that oversells its own
savings gets used for changes that were not worth making.

WHAT IT WILL NOT DO
-------------------
  * cross an @media / @supports boundary -- two contexts are two sheets;
  * factor a property that ANY other rule in the same context also sets (order could matter);
  * factor a property whose SHORTHAND or LONGHAND relatives appear elsewhere -- moving `margin`
    past a `margin-top` is the same bug wearing a different name;
  * mix `!important` with plain declarations;
  * touch custom properties (`--x`), whose value can be read by rules it cannot see.

    python3 .tools/css_factor.py sheet.css                  # report
    python3 .tools/css_factor.py page.html --min-save 20    # <style> blocks, worthwhile ones only
    python3 .tools/css_factor.py sheet.css --fix            # rewrite in place
    python3 .tools/css_factor.py sheet.css --show-refused   # and why each was refused

Standard library only. Exit 0 when it ran, 1 on a usage error.
"""

import argparse
import os
import re
import sys
from collections import defaultdict, OrderedDict

# ---------------------------------------------------------------------------------------------
# Shorthand families. Factoring `margin` when something else sets `margin-top` reorders the pair,
# so the two must be treated as the same property for the safety test. Only families that actually
# collide are listed; a missing family costs a refusal, never a wrong accept, because the fallback
# is the exact-property test.
# ---------------------------------------------------------------------------------------------
FAMILIES = {
    'margin':      ('margin-top', 'margin-right', 'margin-bottom', 'margin-left'),
    'padding':     ('padding-top', 'padding-right', 'padding-bottom', 'padding-left'),
    'border':      ('border-top', 'border-right', 'border-bottom', 'border-left',
                    'border-width', 'border-style', 'border-color', 'border-radius'),
    'background':  ('background-color', 'background-image', 'background-position',
                    'background-repeat', 'background-size', 'background-attachment'),
    'font':        ('font-family', 'font-size', 'font-style', 'font-weight', 'line-height',
                    'font-variant'),
    'flex':        ('flex-grow', 'flex-shrink', 'flex-basis'),
    'grid':        ('grid-template', 'grid-template-columns', 'grid-template-rows', 'grid-area'),
    'overflow':    ('overflow-x', 'overflow-y'),
    'transition':  ('transition-property', 'transition-duration', 'transition-timing-function'),
    'animation':   ('animation-name', 'animation-duration', 'animation-timing-function'),
    'inset':       ('top', 'right', 'bottom', 'left'),
}


def related(prop):
    """Every property whose presence elsewhere makes moving `prop` unsafe -- itself included."""
    out = {prop}
    for short, longs in FAMILIES.items():
        if prop == short:
            out.update(longs)
        elif prop in longs:
            out.add(short)
            out.update(longs)
    return out


# ---------------------------------------------------------------------------------------------
# Parsing. Hand-written and segment-aware: ':' ';' '{' '}' are all legal inside a string and inside
# an unquoted url(), and a regex that does not know that will happily corrupt a data: URI.
# ---------------------------------------------------------------------------------------------
class Rule(object):
    # `idx` is DOCUMENT ORDER, and it is what every grouping key is built from. The obvious id() is
    # a memory address: it groups correctly and then orders the output differently on every run,
    # which turns a build step into a source of spurious diffs.
    def __init__(self, ctx, sel, decls, start, end, idx=0):
        self.ctx = ctx          # tuple of enclosing at-rule preludes
        self.sel = sel          # selector text as authored
        self.decls = decls      # list of (prop, value, important, raw_text)
        self.start = start      # offset of the selector's first character
        self.end = end          # offset just past the closing brace
        self.idx = idx          # position in the sheet, 0-based

    @property
    def selectors(self):
        return tuple(s.strip() for s in split_top(self.sel, ',') if s.strip())


def split_top(text, sep):
    """Split on `sep`, ignoring separators inside strings, url() and parentheses."""
    out, buf, quote, depth = [], [], '', 0
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if quote:
            buf.append(c)
            if c == '\\' and i + 1 < n:
                buf.append(text[i + 1]); i += 2; continue
            if c == quote:
                quote = ''
            i += 1; continue
        if c in '"\'':
            quote = c; buf.append(c); i += 1; continue
        if c == '(':
            depth += 1
        elif c == ')':
            depth = max(0, depth - 1)
        if c == sep and depth == 0:
            out.append(''.join(buf)); buf = []; i += 1; continue
        buf.append(c); i += 1
    out.append(''.join(buf))
    return out


def strip_comments(css):
    """Remove /* ... */ but never inside a string. CSS has no // comment; treating one as a comment
    eats the rest of any url(http://...)."""
    out, i, n, quote = [], 0, len(css), ''
    while i < n:
        c = css[i]
        if quote:
            out.append(c)
            if c == '\\' and i + 1 < n:
                out.append(css[i + 1]); i += 2; continue
            if c == quote:
                quote = ''
            i += 1; continue
        if c in '"\'':
            quote = c; out.append(c); i += 1; continue
        if css.startswith('/*', i):
            j = css.find('*/', i + 2)
            j = n if j < 0 else j + 2
            out.append(' ' * (j - i))          # keep offsets stable for --fix
            i = j; continue
        out.append(c); i += 1
    return ''.join(out)


def parse(css):
    """-> [Rule]. Offsets refer to the ORIGINAL text, so --fix can splice it."""
    clean = strip_comments(css)
    rules, ctx = [], []
    i, n = 0, len(clean)
    buf_start, quote, depth = None, '', 0
    while i < n:
        c = clean[i]
        if quote:
            if c == '\\' and i + 1 < n:
                i += 2; continue
            if c == quote:
                quote = ''
            i += 1; continue
        if c in '"\'':
            quote = c; i += 1; continue
        if not clean[i].isspace() and buf_start is None:
            buf_start = i
        if c == '{':
            prelude = clean[buf_start:i].strip() if buf_start is not None else ''
            if prelude.startswith('@'):
                ctx.append(re.sub(r'\s+', ' ', prelude))
                buf_start = None; i += 1; continue
            body_start = i + 1
            j, d, q2 = body_start, 1, ''
            while j < n and d:
                ch = clean[j]
                if q2:
                    if ch == '\\':
                        j += 2; continue
                    if ch == q2:
                        q2 = ''
                elif ch in '"\'':
                    q2 = ch
                elif ch == '{':
                    d += 1
                elif ch == '}':
                    d -= 1
                j += 1
            body = clean[body_start:j - 1]
            decls = []
            for piece in split_top(body, ';'):
                raw = piece.strip()
                if not raw or ':' not in raw:
                    continue
                prop, _, val = raw.partition(':')
                prop = prop.strip().lower()
                val = val.strip()
                imp = val.lower().endswith('!important')
                if imp:
                    val = val[:-len('!important')].rstrip()
                if prop and not prop.startswith('--'):
                    decls.append((prop, val, imp, raw))
            rules.append(Rule(tuple(ctx), re.sub(r'\s+', ' ', prelude).strip(), decls, buf_start, j,
                              len(rules)))
            buf_start = None; i = j; continue
        if c == '}':
            if ctx:
                ctx.pop()
            buf_start = None; i += 1; continue
        i += 1
    return rules


def decl_bytes(prop, val, imp):
    """Minified cost of one declaration, the units this tool reports in."""
    return len('%s:%s%s;' % (prop, val, '!important' if imp else ''))


# ---------------------------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------------------------
class Candidate(object):
    def __init__(self, ctx, key, rules, saving, refusal=None):
        self.ctx = ctx              # at-rule context
        self.key = key              # tuple of (prop, val, imp) being factored
        self.rules = rules          # the Rule objects that carry all of them
        self.saving = saving        # minified bytes
        self.refusal = refusal      # None when it is safe

    @property
    def selectors(self):
        out = []
        for r in self.rules:
            for s in r.selectors:
                if s not in out:
                    out.append(s)
        return out


def find(rules, min_save=1):
    """-> [Candidate], safe ones first, each with its saving and any refusal reason."""
    # every (context, property) that appears anywhere, for the safety test
    by_prop = defaultdict(list)
    for r in rules:
        for prop, val, imp, _raw in r.decls:
            by_prop[(r.ctx, prop)].append(r)

    # group rules by the SET of declarations they share
    owners = defaultdict(list)
    for r in rules:
        for prop, val, imp, _raw in r.decls:
            owners[(r.ctx, prop, val, imp)].append(r)

    # a selector-set -> the declarations all of them carry, so multi-declaration groups surface
    sets = defaultdict(list)
    for key, rs in owners.items():
        if len(rs) > 1:
            sets[(key[0], tuple(sorted(r.idx for r in rs)))].append(key)

    byidx = dict((r.idx, r) for r in rules)
    out = []
    seen = set()
    for (ctx, rule_ids), keys in sets.items():
        group = [byidx[i] for i in rule_ids]      # already in document order
        keys = sorted(keys, key=lambda k: (k[1], k[2]))
        sig = (ctx, rule_ids, tuple(keys))
        if sig in seen:
            continue
        seen.add(sig)

        refusal = None
        for (_c, prop, val, imp) in keys:
            fam = related(prop)
            others = set()
            for p in fam:
                for r in by_prop.get((ctx, p), ()):
                    if r not in group:
                        others.add(r.sel)
            if others:
                # One rule's selector text can itself be a long list, so trim the JOINED string --
                # trimming the list of rules still printed 27 selectors on the first real sheet.
                who = ', '.join(sorted(others))
                if len(who) > 58:
                    who = who[:58].rsplit(',', 1)[0] + ', ... (%d rules)' % len(others)
                refusal = '%s is also set by %s' % (prop, who)
                break
            mixed = set(i for (_c2, p2, v2, i2) in keys for i in (i2,) if p2 == prop)
            if len(mixed) > 1:
                refusal = '%s appears both with and without !important' % prop
                break

        per = sum(decl_bytes(p, v, i) for (_c, p, v, i) in keys)
        sel_txt = ','.join(s for r in group for s in r.selectors)
        cur = per * len(group)
        new = len(sel_txt) + per + 2                      # "sels{...}"
        saving = cur - new
        if saving >= min_save or refusal:
            out.append(Candidate(ctx, tuple((p, v, i) for (_c, p, v, i) in keys), group, saving, refusal))
    out.sort(key=lambda c: (c.refusal is not None, -c.saving))
    return out


# ---------------------------------------------------------------------------------------------
# Rewriting
# ---------------------------------------------------------------------------------------------
def apply(css, cands):
    """Splice the accepted candidates into the text. Returns (new_css, applied_count)."""
    edits = []          # (start, end, replacement)
    used = set()
    applied = 0
    for c in cands:
        if c.refusal or any(r.idx in used for r in c.rules):
            continue
        for r in c.rules:
            used.add(r.idx)
        keep = dict((r.idx, []) for r in c.rules)
        drop = set((p, v, i) for (p, v, i) in c.key)
        for r in c.rules:
            for prop, val, imp, raw in r.decls:
                if (prop, val, imp) in drop:
                    continue
                keep[r.idx].append('%s:%s%s' % (prop, val, '!important' if imp else ''))
        first = min(c.rules, key=lambda r: r.start)
        body = ';'.join('%s:%s%s' % (p, v, '!important' if i else '') for (p, v, i) in c.key)
        combined = '%s{%s}' % (','.join(c.selectors), body)
        for r in c.rules:
            rest = keep[r.idx]
            if r is first:
                text = combined + ('\n%s{%s}' % (r.sel, ';'.join(rest)) if rest else '')
            else:
                text = '%s{%s}' % (r.sel, ';'.join(rest)) if rest else ''
            edits.append((r.start, r.end, text))
        applied += 1
    out, prev = [], 0
    for start, end, text in sorted(edits):
        out.append(css[prev:start]); out.append(text); prev = end
    out.append(css[prev:])
    return ''.join(out), applied


STYLE_RE = re.compile(r'(<style[^>]*>)(.*?)(</style>)', re.S | re.I)


def sheets(text, path):
    """-> [(prefix, css, suffix)] so an HTML page's <style> blocks are handled like a .css file."""
    if path.lower().endswith(('.html', '.htm')):
        parts, prev = [], 0
        for m in STYLE_RE.finditer(text):
            parts.append((text[prev:m.start()] + m.group(1), m.group(2), ''))
            prev = m.end() - len(m.group(3))
        if not parts:
            return []
        parts.append((text[prev:], '', ''))
        return parts
    return [('', text, '')]


def main(argv):
    ap = argparse.ArgumentParser(description='Report or apply safe selector-list factoring.')
    ap.add_argument('paths', nargs='+')
    ap.add_argument('--fix', action='store_true', help='rewrite the file in place')
    ap.add_argument('--min-save', type=int, default=1, help='ignore candidates below this (bytes)')
    ap.add_argument('--show-refused', action='store_true', help='and why each was refused')
    a = ap.parse_args(argv[1:])

    total = 0
    for path in a.paths:
        with open(path) as f:
            text = f.read()
        blocks = sheets(text, path)
        if not blocks:
            print('%s: no stylesheet' % path)
            continue
        print('%s' % path)
        new_blocks, save_here, n_ok, n_no = [], 0, 0, 0
        for pre, css, _suf in blocks:
            if not css.strip():
                new_blocks.append((pre, css)); continue
            cands = find(parse(css), a.min_save)
            for c in cands:
                if c.refusal:
                    n_no += 1
                    if a.show_refused:
                        print('   refused  %-42s %s' % (
                            ';'.join('%s:%s' % (p, v) for (p, v, _i) in c.key)[:42], c.refusal))
                    continue
                n_ok += 1
                save_here += c.saving
                print('   %5d B  %-40s  <- %s' % (
                    c.saving,
                    ';'.join('%s:%s' % (p, v) for (p, v, _i) in c.key)[:40],
                    ', '.join(c.selectors)[:60]))
            if a.fix:
                css, done = apply(css, cands)
            new_blocks.append((pre, css))
        print('   %d factorable, %d refused, %d bytes minified' % (n_ok, n_no, save_here))
        total += save_here
        if a.fix:
            out = ''.join(pre + css for pre, css in new_blocks)
            with open(path, 'w') as f:
                f.write(out)
            print('   rewritten')
    if len(a.paths) > 1:
        print('total: %d bytes' % total)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
