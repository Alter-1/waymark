# Working so that knowledge accumulates

waymark exists because the expensive thing in a long-lived codebase is not writing code, it is
re-deriving what somebody already learned. A knowledge base only helps if the way you work produces
knowledge worth keeping — and if it does not quietly destroy the evidence first.

These practices are **distilled from a real project**, most of them from corrections the author made
to an assistant working on their firmware over several months. Each one is written with the failure
that produced it, because a rule without its failure is just an opinion and gets argued with.

They are general. Nothing here is specific to that project, that language, or that tool.

---

## 1. Before you change anything

**Treat every bug report as a hypothesis, not a fact — including your own.** A report is a *symptom*
plus the reporter's *interpretation*, and the interpretation is the part most likely to be wrong.
Fixing the interpretation produces a change that fixes nothing, or breaks something else. Establish
what actually happens before deciding what to do about it, and say plainly what you could and could
not reproduce.

**Report the symptom before diving.** When something unexpected appears, present symptom, evidence,
suspects and a proposed next step — then let the person who owns the code choose the direction. They
are often already fixing it, or already know the answer. Chaining ten exploratory steps solo is how
an afternoon disappears into a question that had a one-line answer.

**Check feasibility before proposing.** Ask "is this actually supported?" and distinguish *supported
and reliable* from *can sometimes be coaxed*. Advising a fragile path costs someone else's time to
discover it does not work, and the discovery lands on them, not on you.

**Explain the diagnosis and the intended change before editing.** Say what the defect is and the
evidence, where the root is, what the change does and deliberately does *not* do, what else it
touches, and which parts are the author's call. Then stop. A proposal is cheap to redirect; a
completed edit is expensive, and it hides the questions worth asking while they can still change the
shape of the fix. *Investigating, measuring and reading are fine unprompted — it is editing that
waits.*

---

## 2. Making the change

**Ask whether the fix removes the root or hides it.** A green test can mean the defect is gone — or
that the trigger is no longer reachable on the path you tested while the hazard sits untouched
behind another one.

**Then ask what else the change affects.** Size, performance, anything that can now interleave
differently, memory and stack. A behaviour that used to happen as a side effect of work you removed
must be re-established deliberately.

**And ask why it worked before.** If the code was correct once, find when and why it changed. That
usually names the real defect.

**Keep the change minimal and visible.** Do not "improve" code you were not asked to touch — the odd
thing you are about to normalise may be load-bearing. Work where the author can watch, on the branch
they have open, rather than in a hidden worktree that makes them see stale code.

**Never remove or reword existing comments.** They are the previous author's evidence. Add, or
correct a comment that is factually wrong; do not tidy.

**Propagate fixes across every branch that has the functionality, before starting new work
elsewhere.** Fixes land on whichever branch the bug was hit on; the same bug then gets rediscovered
and re-fixed from scratch somewhere else. Audit by reading the **final code** on each branch, not by
`git cherry` — a fix can be present in a different shape. *Fixes propagate; features do not.*

---

## 3. Proving it

**A test that passes for the wrong reason is worse than one that fails.** Before believing a new
test, break the fix and watch the test fail. Twice in one day here, a check "passed" while reading
the wrong file — once accusing the engine of a defect it did not have, once confirming a claim that
was false.

**Name the thing you mean.** `sorted(glob("*.db"))[0]` picks whatever sorts first, which is not what
you meant and will differ on someone else's machine. Globs are for sets, not for "the one I want".

**Never cap a completeness search.** `| head` on "who calls this?" emits no warning and looks exactly
like a finished list. A self-imposed cap once turned into a confidently wrong root cause: fourteen
results, all in one file, and the answer was in the fifteenth.

**Never suppress stderr on a command whose success you then rely on.** A failed build that printed
nothing is indistinguishable from a successful one, and everything you conclude afterwards is
measured against stale output.

**Measure the thing, do not infer it.** Where a claim can be checked cheaply — a count before and
after, a digest, a diff against the remote — check it. Reasoning that "this should have worked" is
how a partial sync gets believed to be complete.

---

## 4. Silent success is the worst failure mode

The failures that cost the most are the ones that look like answers.

* An index that builds cleanly from a missing knowledge base and reports zero notes — after which
  every query answers "no matches", which reads as *nobody wrote that down* rather than *this is
  broken*.
* A note truncated mid-sentence, which reads as *recorded incomplete* and sends the next person off
  to re-derive a chain that was already written down.
* A mirror that silently kept only three files of a knowledge base, and still looked like one.

**So: make absence loud.** If a tool can produce an empty or partial result from a broken input, it
must say so. Check counts after an operation that should have changed them. Prefer an error that
names the next step over a clean run that means nothing.

**But over-blocking is not the safe direction either.** A guard that cries wolf gets overridden by
reflex, and then it protects nothing. Treat a false positive in a safety check as a real defect.

---

## 5. Git, especially in a tree someone else is using

**Publishing is the author's call.** Do not push to a shared remote on someone else's behalf unless
they asked for that push. The same goes for merging release lineage.

**Never rewrite published history.** Before it is pushed, rewriting is free and sometimes right —
that is the moment to remove a secret or squash a mess. After, it breaks every clone.

**Assert the state in the same step as the action.** Checking the branch after a checkout is not
enough: in a shared tree the branch can change between one command and the next, because the other
person is entitled to switch it. From inside a script, a silently-failed checkout and a colleague
switching branches are indistinguishable.

```bash
test "$(git rev-parse --abbrev-ref HEAD)" = "$expected" || exit 1
```

**`git add <paths>` does not make the commit explicit.** `git commit` commits the whole index, so
anything a colleague already staged rides along under your message. Use `git commit -- <paths>`, or
read `git diff --cached --name-only` first.

**One task at a time, and read the result.** Finish or abort one operation before starting another;
a dirty tree makes `checkout` fail *silently*, and every later command then runs on the wrong branch.

**A refused command means nothing ran** — not "most of it ran". Re-check any state you believed you
changed.

**Commit each substantial change on its own.** Bundles cannot be reverted, reviewed, or cited
individually.

**Keep branches, including the failed ones.** A branch is the only artifact that preserves the exact
tree that failed, it is unreconstructable, and it costs almost nothing. But know its limit: a branch
is excellent *evidence* and poor *knowledge* — it records what the tree was, not why it existed or
what it proved. Which is why a dead end deserves a KB entry that **names the branch**.

---

## 6. State and evidence

**Set the baseline at the start, do not clean up at the end.** A test that assumes a sane state
inherits whatever the last run left behind. Restore on entry.

**On failure, stop and leave the state alone.** Auto-restoring on the way out destroys exactly the
evidence someone needs. Report what you found and what state things are in. *On success, do clean
up — but nothing should depend on that having happened.*

**Every artifact you build gets a commit.** A binary built from an uncommitted tree cannot be found
or reconstructed later, and "which source made this image?" becomes unanswerable.

---

## 7. Secrets and anything that gets published

**A secret guarded by "the file it lives in is not tracked" is guarded by a fact about the FILE.**
Files get moved, tracked, split and published; storage decisions do not re-open every note that
depended on them. Here, an entry recorded a passphrase and said in writing that it was safe because
the file was gitignored. That stopped being true, and nothing re-read the line.

**Make the wrong thing impossible, not discouraged.** Prefer a location that *cannot* be published
over a marker saying it should not be. A file merely marked private still sits in a tracked tree and
one `git add` publishes it.

**Sweep before the first publish, not after.** That is the one moment when removal is free.

**Split, do not hide.** Most notes are ninety per cent general. Moving one wholesale into a private
store because it mentions a password removes exactly the root-cause work the shared store exists to
carry. Put the finding in the shared entry, the values in the private one, and cross-link them.

**Published defaults are not secrets.** A password printed in the shipped manual is documentation;
hiding it only makes the notes harder to follow.

---

## 8. Two copies of anything that must agree will disagree

**One definition, referenced twice.** Two hardcoded lists that must match is how an asymmetry is
born — here, a path filter used in both directions of a sync, where adding an entry to one and not
the other would have merged an entire tool directory backwards.

**Generated files are not source.** Edit the input and regenerate. A patched artifact is silently
undone by the next build, and a stale committed artifact is a *wrong build*, not stale
documentation.

**Vendored code is not source either.** Fix it upstream and re-copy, or the fix does not exist for
anyone else.

---

## 9. Recording what you learned

**Record the solution, not just the problem** — and key the words on the *symptom*, so a search for
what went wrong finds what fixed it. Nobody searches for the cause; they do not know it yet.

**Record the dead ends.** "Tested and refuted" is often worth more than the answer, because without
it the next person spends the same days. Say what was measured versus what was reasoned.

**Say OPEN when it is open.** One honest "here is what I know and what would settle it" beats a tidy
summary that hides the uncertainty — a summary that is trusted and wrong costs more than no summary.

**Write it down before the context ends, not after.** An unfinished change described precisely is
recoverable; a finished one nobody can find or trust is not.

**Correct the record when it turns out wrong.** Two entries here asserted the opposite of the truth
for weeks. A knowledge base nobody trusts is worse than none.

**Use your own tool.** The knowledge about the thing you are building belongs in the knowledge base
you are building it with. Asked whether waymark was used on waymark, the honest answer was no — its
own hard-won facts were in commit messages and in a different project's private notes, where no user
of it could ever reach them.

---

## The shortest version

Verify before you fix. Explain before you edit. Make absence loud. Assert state in the same breath
as the action that depends on it. Keep the evidence, especially of failure. Record the symptom, the
dead ends and the uncertainty. And put the knowledge where the next person will actually look —
which is not your memory, and not the commit message.
