#!/usr/bin/env python3
"""Turn the KB's opted-in procedures into agent skills -- which are GENERATED, never edited.

WHY THIS EXISTS
---------------
A procedure that lives only as prose gets RE-DERIVED. In one session the RC-cut mechanics were
read out of the KB three times and the cross-branch port four, and each re-derivation made a
different mistake. The fix is not to write it down better; it is to make the written form
executable and to put it where an agent will actually meet it.

But a skill is the NARROWEST store there is. Measured against this repository's own plumbing:

    store                      another clone   another machine   the GitLab mirror   a human
    KB (orphan `kb` branch)         yes             yes                yes             yes
    .tools/ scripts                 yes             yes                yes             yes
    .claude/skills/                 yes             yes            *** NO ***      *** NO ***

`.claude/` is not in the mirror's path allow-list (sync-presets.sh), and a SKILL.md means nothing
to someone not driving this repository with an agent. So a skill must never be the only copy of
anything.

Hence: THE KB IS THE SOURCE AND THE SKILL IS A PROJECTION. Same rule this project already applies
to main/web_*.h and to manual.html -- edit the source, regenerate; never patch the artefact.
`.claude/skills/` is gitignored for the same reason the index is: it is rebuilt, it must not
conflict across branches, and it cannot go stale because nothing reads a copy that was not just
written.

WHAT MAKES AN ENTRY A SKILL -- opt-in, because most entries must NOT become one:

    skill: cross-branch-port          the skill's name; absent means "stay prose"
    skill_when: "port this to ..."    the TRIGGER, in the author's words. This is the only text an
                                      agent matches on when deciding to load the skill, so it is a
                                      separate field and never a re-used summary.
    ## procedure                      THE ONLY SECTION COPIED.

That last constraint is the load-bearing one. Most of a good KB entry is evidence, measurements and
dead hypotheses -- which is its value, and which is exactly what must NOT be loaded into a skill. An
entry with no `## procedure` section is refused rather than flattened: if the steps have not been
written as steps, there is nothing here worth generating.
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

FM = re.compile(r"^---\s*$")


def skills_dir():
    """Where the projections go. Configurable, because the directory an agent reads is the agent's
    convention, not the knowledge base's -- and a KB should not hard-code one vendor's layout."""
    try:
        with open(os.path.join(ROOT, "kb.config.json"), encoding="utf-8") as fh:
            d = json.load(fh).get("skills_dir")
    except Exception:
        d = None
    d = d or os.path.join(".claude", "skills")
    return d if os.path.isabs(d) else os.path.join(ROOT, d)



def kb_dirs():
    cfg = os.path.join(ROOT, "kb.config.json")
    try:
        with open(cfg, encoding="utf-8") as fh:
            conf = json.load(fh).get("annotations") or []
    except Exception:
        return []
    if isinstance(conf, str):
        conf = [conf]
    out = []
    for c in conf:
        c = os.path.expanduser(c)
        out.append(c if os.path.isabs(c) else os.path.join(ROOT, c))
    return out


def entries():
    for base in kb_dirs():
        for dirpath, _d, files in os.walk(base):
            for fn in sorted(files):
                if fn.endswith(".md"):
                    yield os.path.join(dirpath, fn)


def parse(path):
    """(frontmatter dict, body). Block lists are kept; a scalar stays a string."""
    lines = open(path, encoding="utf-8", errors="replace").read().split("\n")
    fm, body_at = {}, 0
    if lines and FM.match(lines[0]):
        for i in range(1, len(lines)):
            if FM.match(lines[i]):
                body_at = i + 1
                break
            m = re.match(r"^(\w+):\s*(.*)$", lines[i])
            if not m:
                continue
            if m.group(2).strip():
                fm[m.group(1)] = m.group(2).strip().strip('"\'')
                continue
            items = []
            for j in range(i + 1, len(lines)):
                li = re.match(r"^\s+-\s+(.*)$", lines[j])
                if not li:
                    break
                v = li.group(1).strip().strip('"\'')
                if v:
                    items.append(v)
            if items:
                fm[m.group(1)] = items
    return fm, "\n".join(lines[body_at:])


def procedure_of(body):
    """The `## procedure` section, and nothing else."""
    m = re.search(r"^##\s+procedure\s*$(.*?)(?=^##\s|\Z)", body, re.S | re.M | re.I)
    return m.group(1).strip() if m else ""


def render(fm, proc, name):
    scripts = fm.get("files") or []
    if isinstance(scripts, str):
        scripts = [scripts]
    runnable = [f for f in scripts if f.endswith((".py", ".sh"))]
    out = ["---",
           "name: %s" % name,
           "description: %s" % fm.get("skill_when", "").replace("\n", " "),
           "---",
           "",
           "<!-- GENERATED from the knowledge base by .tools/gen_skills.py -- DO NOT EDIT.",
           "     Source: %s. Edit the entry and re-run `python3 .tools/index_code.py`. -->" % fm.get("name", name),
           "",
           "The reasoning, the evidence and what has already been ruled out are NOT repeated here.",
           "Load them before deviating from the steps:",
           "",
           "```bash",
           "python3 .tools/query_code_index.py --full annotation %s" % fm.get("name", name),
           "```",
           ""]
    if runnable:
        out += ["Scripts this procedure uses:", ""]
        out += ["  * `%s`" % f for f in runnable]
        out += [""]
    out += ["## Procedure", "", proc, ""]
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true",
                    help="report what would be written, change nothing; non-zero if an opted-in "
                         "entry cannot be generated")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    made, refused = [], []
    for path in entries():
        fm, body = parse(path)
        name = fm.get("skill")
        if not name:
            continue
        rel = os.path.relpath(path, ROOT)
        if not re.match(r"^[a-z0-9][a-z0-9-]*$", str(name)):
            refused.append((rel, "skill name %r is not a lowercase slug" % name))
            continue
        if not fm.get("skill_when"):
            refused.append((rel, "no skill_when: -- nothing for an agent to match on"))
            continue
        proc = procedure_of(body)
        if not proc:
            # NOT flattened into the whole entry. If the steps were never written as steps there is
            # nothing worth generating, and guessing would produce a skill made of narrative.
            refused.append((rel, "no `## procedure` section"))
            continue
        made.append((name, rel, render(fm, proc, name)))

    if not a.check:
        for name, _rel, text in made:
            d = os.path.join(skills_dir(), name)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "SKILL.md"), "w", encoding="utf-8") as fh:
                fh.write(text)
        # A skill whose entry lost its `skill:` field must GO, or it lingers as the one copy nobody
        # can trace back to a source -- exactly what this design exists to prevent.
        keep = {n for n, _r, _t in made}
        OUT = skills_dir()
        if os.path.isdir(OUT):
            for d in sorted(os.listdir(OUT)):
                p = os.path.join(OUT, d, "SKILL.md")
                if d not in keep and os.path.exists(p):
                    os.remove(p)
                    try:
                        os.rmdir(os.path.join(OUT, d))
                    except OSError:
                        pass
                    if not a.quiet:
                        print("  removed stale skill: %s" % d)

    if not a.quiet:
        for name, rel, text in made:
            print("  skill %-24s <- %s  (%d B)" % (name, rel, len(text)))
    for rel, why in refused:
        print("  REFUSED %s: %s" % (rel, why), file=sys.stderr)
    if not a.quiet:
        print("  %d skill(s), %d refused" % (len(made), len(refused)))
    return 1 if (refused and a.check) else 0


def main_quiet():
    """The entry point index_code.py uses: writes the skills, says only what changed."""
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        main_argv([])
    out = [l for l in buf.getvalue().splitlines()
           if l.strip() and not l.strip().endswith("0 refused") or "REFUSED" in l]
    for line in out:
        print(line)


def main_argv(argv):
    saved, sys.argv = sys.argv, ["gen_skills.py"] + list(argv)
    try:
        return main()
    finally:
        sys.argv = saved


if __name__ == "__main__":
    raise SystemExit(main())
