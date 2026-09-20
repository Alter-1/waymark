#!/usr/bin/env python3
"""Tests for shared_paths - is a directory that MUST be the same on every branch actually the same?

The tool answers by comparing git TREE HASHES per branch and grouping the branches that agree, so
these build real repositories with real branches rather than faking git output: the whole value of
the instrument is that it cannot be fooled by a mode change, a rename or a file present on one side
only, and a mocked `git` would not prove that.

Each case here is a way the answer can be wrong in the direction that matters -- a green report on a
repository that has drifted.

    python3 tests/test_shared_paths.py

Standard library only, no test framework, exit code 1 on failure.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / ".tools" / "shared_paths.py"
FAILED = []


def check(name, cond, detail=""):
    print("  %s  %s%s" % ("ok  " if cond else "FAIL", name,
                          ("   -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def git(repo, *args):
    return subprocess.run(["git"] + list(args), cwd=str(repo), capture_output=True, text=True)


def write(repo, rel, text, mode=None):
    p = Path(repo) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    if mode is not None:
        os.chmod(str(p), mode)


def make_repo(td):
    """A repo with .tools/ on 'main', plus a copy of the tool where the script expects it."""
    repo = Path(td) / "proj"
    (repo / ".tools").mkdir(parents=True)
    # `git init -b <name>` is 2.28+; this has to run on older git too, so rename after the commit.
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "t")
    # The tool computes the repo root as its own parent directory, like every other waymark tool.
    (repo / ".tools" / "shared_paths.py").write_text(TOOL.read_text(encoding="utf-8"), encoding="utf-8")
    write(repo, "shared/a.py", "print(1)\n")
    write(repo, "shared/b.py", "print(2)\n")
    write(repo, "own/main.c", "int main(){}\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    git(repo, "branch", "-m", "main")
    return repo


def run(repo, *args):
    r = subprocess.run([sys.executable, str(repo / ".tools" / "shared_paths.py")] + list(args),
                       cwd=str(repo), capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def main():
    print("shared_paths")
    with tempfile.TemporaryDirectory() as td:
        repo = make_repo(td)
        git(repo, "branch", "rel1")
        git(repo, "branch", "rel2")

        rc, out = run(repo, "--path", "shared", "--branches", "main,rel1,rel2")
        check("identical trees pass", rc == 0 and "identical on 3" in out, out)

        # Asked with nothing to compare, it must REFUSE rather than report agreement. (Before the
        # config is written below, so nothing can supply the branches behind the question's back.)
        rc, out = run(repo, "--path", "shared")
        check("no branches at all is refused, not passed", rc == 2, out)

        # A file changed on one branch only.
        git(repo, "checkout", "-q", "rel2")
        write(repo, "shared/a.py", "print(1)  # fixed\n")
        git(repo, "commit", "-qam", "fix on rel2")
        git(repo, "checkout", "-q", "main")
        rc, out = run(repo, "--path", "shared", "--branches", "main,rel1,rel2")
        check("a one-branch edit is reported", rc == 1 and "DIFFERS" in out, out)
        check("the branches that agree are grouped", "main, rel1" in out or "rel1, main" in out, out)
        check("the differing file is counted", "1 differ" in out, out)
        check("--files names it", "a.py" in run(repo, "--path", "shared",
                                                "--branches", "main,rel1,rel2", "--files")[1], out)

        # A file that exists on one branch only -- the case that matters most, because it is
        # invisible to anyone who only ever reads the other branch.
        git(repo, "checkout", "-q", "rel1")
        write(repo, "shared/c.py", "print(3)\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", "new tool on rel1 only")
        git(repo, "checkout", "-q", "main")
        rc, out = run(repo, "--path", "shared", "--branches", "main,rel1")
        check("a file on one branch only is a divergence", rc == 1, out)
        check("it is counted as 'only on'", "only on rel1" in out, out)

        # A mode change with identical content: a tree hash sees it, a text diff does not.
        git(repo, "checkout", "-q", "rel2")
        os.chmod(str(repo / "shared" / "b.py"), 0o755)
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", "chmod")
        git(repo, "checkout", "-q", "main")
        rc, out = run(repo, "--path", "shared", "--branches", "main,rel2")
        check("a mode change is not identical", rc == 1, out)

        # A path absent on a branch: a divergence, unless declared optional.
        rc, out = run(repo, "--path", "nothing-here", "--branches", "main,rel1")
        check("a path on no branch is not a failure", rc == 0 and "on no branch" in out, out)
        git(repo, "checkout", "-q", "rel1")
        write(repo, "extra/x.py", "print(9)\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", "extra on rel1")
        git(repo, "checkout", "-q", "main")
        rc, out = run(repo, "--path", "extra", "--branches", "main,rel1")
        check("absent on one branch is a divergence", rc == 1 and "absent on main" in out, out)
        check("and it says the absence was not declared", "NOT declared optional" in out, out)

        cfg = {"shared_paths": {"paths": ["extra"], "branches": ["main", "rel1"],
                                "optional": ["extra"]}}
        (repo / "kb.config.json").write_text(json.dumps(cfg), encoding="utf-8")
        rc, out = run(repo)
        check("config supplies paths and branches", "extra" in out, out)
        check("a declared-optional absence passes", rc == 0, out)

        # Refusals: a bad branch name must not be read as agreement.
        rc, out = run(repo, "--path", "shared", "--branches", "main,does-not-exist")
        check("an unknown branch is not silently ignored", rc == 2 and "no such branch" in out, out)
        rc, out = run(repo, "--path", "shared")
        check("with a config, --path alone takes the branches from it", rc in (0, 1), out)

    print("\n%s" % ("all ok" if not FAILED else "FAILED: " + ", ".join(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
