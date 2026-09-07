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

### If you generate source, do not send it through a shell

Writing file content inside an *unquoted* shell heredoc silently consumes one level of escaping, and
the behaviour differs by how many levels you wrote. The generating script reads back correctly, so
the damage is invisible until something downstream chokes on it:

    written        arrives as              result
    \\n            a real newline          a string literal split across two lines
    `Foo`          command substitution    the command dies part-way, leaving a half-written file
    x->y*          a redirect              a junk file named y* appears in the source tree

**Write the script to a file with an editor/file-writing tool, then run that file.** Not "escape more
carefully" — that phrasing is why it recurs. If a generator must emit an escape, build it from a
character code rather than typing it.

*Quoting the delimiter is not the answer, though it is worth knowing why.* `<<'EOF'` suppresses
expansion entirely — measured: with `<<EOF` a backtick runs and `\\n` collapses to `\n`, with
`<<'EOF'` both stay literal. That removes the three rows above, and it does **not** remove the case
that actually recurs: content that is itself handed to another interpreter, where the escaping you
must survive is the *next* layer's, not the shell's. A quoted heredoc also still cannot contain its
own delimiter. Use it where a heredoc is unavoidable; do not treat it as permission to generate
source through one.

**And do not answer this with a detector.** A checker that finds broken output after the fact, or a
build-script gate that fails the compile, is a workaround for a defect you control — it reports late,
and a gate in a shared build makes everyone pay for one author's tooling. Keep such a check as a
manual sweep if it is cheap, but fix the transport. *No workaround for code under your own control.*

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

### A negative result needs a positive control

"I searched and found nothing" and "I searched the wrong thing" produce the identical output. An
empty result is not evidence of absence until you have shown the same search can return something.

Four ways this happened in a single day, each costing twenty minutes or more, each looking like a
finished answer:

* **A capped range.** Reading lines 48-120 of a 330-line function and reporting "this function does
  not handle X". It handled X at line 365. The grep was empty because the window was, not the file.
* **The wrong encoding.** `strings` defaults to ASCII, so a UTF-16 literal in a .NET binary returned
  zero matches — read as "the build does not contain my change", when it did.
* **The wrong form of the argument.** A directory-only ignore rule reported "not ignored" because the
  query omitted the trailing slash. The rule was working.
* **A probe past the exit.** A trace placed after two early returns logged nothing when either
  return fired — which reads as *the function was never called*, not *it returned before here*.
* **A request that was reinterpreted, not refused.** A command sent to the wrong endpoint came back
  with an empty body — read as "not supported here". It had in fact been parsed as something else
  entirely and had *acted*, setting an unrelated numeric option, and the only trace was that option's
  new value. An empty answer can mean the system did nothing, or that it did something you did not
  ask for and had no way to report.

**So: before believing a negative, make the check succeed once.** Grep for something you know is
present, in the same file, with the same tool and the same flags. If a search is capped — a line
range, a head, a result limit — a result of exactly the limit means truncated, and a result of zero
means nothing at all until the cap is ruled out. When a guarded function is silent, the silence
locates the next probe; it does not answer the question.

This is the same discipline as proving a test fails without the fix, applied to searching. A check
that cannot fail proves nothing; a search that cannot succeed proves less.

### And a plausible positive is not a verified one

The mirror of the rule above, and the more dangerous half, because nothing prompts you to look
again. A wrong answer that has the *shape* of a right one ends the investigation:

* a port number recorded byte-swapped — a connection to 9999 logged as 3879. Both are plausible port
  numbers; only one is the one you used.
* a captured payload arriving eight bytes short, because a header offset was applied to a buffer the
  layer below had already stripped. The dump was well-formed, printable, and wrong at the front.

Neither was visible to any host test: both lived in what the *caller* handed over, which a test that
drives the callee directly supplies itself. What caught both, immediately, was **one real packet
carrying a value chosen to be recognisable** — a port and a payload whose exact bytes were known in
advance, so "close enough" could not pass.

So when a result finally arrives: check it against a value you picked, not against your expectation
of it. Round numbers, byte-swaps, and off-by-a-header-length are the shapes to distrust, and all
three survive a reader who is looking for *whether* there is output rather than *which* output.

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

**But `git commit -- <paths>` only commits TRACKED files.** A new file matching the pathspec is
silently skipped — no warning, and the commit succeeds. `git add` it first. Discovered by shipping a
commit that was supposed to contain a new document and did not; the safer form has its own way of
doing less than you asked.

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

**And never trust a dead end completely, because a dead end dies in a CONTEXT.** A toolchain, a
board revision, a library version or the shape of the data can move, and a refutation quietly stops
holding while the entry still reads *refuted*. So record what the death DEPENDED on, not only what
killed it:

```json
{"status": "dead", "text": "the fast path cannot work",
 "killed_by": "measured 3x slower",
 "revive_if": "the allocator stops serialising -- retest on any toolchain bump"}
```

`killed_by` is the past; `revive_if` is the dependency. When the environment moves, that field is
what makes the question answerable at all:

```bash
python3 .tools/query_code_index.py claim --revivable
```

Measured on a real KB before this existed: **21 dead claims, 19 recording what killed them, zero
recording what the death depended on.** Nothing could answer "which of these should I recheck?"

**When a dead end does revive, add a claim — never edit the old one.** Keep both halves:

* *it was impossible because …* — the original claim, its `killed_by`, and its date
* *it became possible because …* — a new claim naming the change that opened the way

The pair is worth more than either half, because it names **the constraint that moved** — and that
constraint is usually load-bearing somewhere else too. Overwriting the corpse destroys exactly the
part a future reader needs to judge whether the same thing has moved again. The same pair belongs in
a comment at the code site when the change lands: the person who next reads that line will not be
querying a knowledge base.

**Say OPEN when it is open.** One honest "here is what I know and what would settle it" beats a tidy
summary that hides the uncertainty — a summary that is trusted and wrong costs more than no summary.

**Write it down before the context ends, not after.** An unfinished change described precisely is
recoverable; a finished one nobody can find or trust is not.

**Correct the record when it turns out wrong.** Two entries here asserted the opposite of the truth
for weeks. A knowledge base nobody trusts is worse than none.

**Audit your open claims, because nothing closes them for you.** A claim is written where the *work*
is and resolved where the *evidence* lands, and those are usually different places — often a
different entry, sometimes a different branch. Nothing walks back.

```bash
python3 .tools/query_code_index.py claim --status open --until <a-few-weeks-ago>
```

**Claims of absence rot fastest.** "Has NOT been given the guard", "NEITHER measured", "a SECOND,
UNFIXED copy" — any work at all makes those false, and nothing points back to say so. Three such
claims were found by hand here in one sweep, one of them wrong for two days while its own entry
documented the opposite, and one for four days because the measurement that settled it was recorded
elsewhere. **An open item that is actually finished is worse than none: it is a standing invitation
to redo work already done.**

Two things that are *not* signals, measured rather than assumed: an open claim on a `resolved` entry
means nothing (every one of the fourteen legitimate open claims here sits on a resolved entry —
entry status and claim status are orthogonal), and age alone is not a defect either. Most open
claims are honestly open: predictions awaiting a rig, competing explanations, and warnings that
exist to stop themselves being cited as proof. The audit is a *review*, not a check that can fail.

**Use your own tool.** The knowledge about the thing you are building belongs in the knowledge base
you are building it with. Asked whether waymark was used on waymark, the honest answer was no — its
own hard-won facts were in commit messages and in a different project's private notes, where no user
of it could ever reach them.

---

## The shortest version

Verify before you fix. Explain before you edit. Make absence loud. Prove a search can succeed
before believing it failed, and check a result you did get against a value you chose. Assert state
in the same breath as the action that depends on it. Keep the evidence, especially of failure.
Record the symptom, the dead ends and the uncertainty. And put the knowledge where the next person
will actually look — which is not your memory, and not the commit message.
