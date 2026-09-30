# Debugging

*Adapted from superpowers' `systematic-debugging` 6.4.1 (MIT, © 2025 Jesse Vincent); the full
notice is in [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).*

The discipline artel applies whenever something behaves wrongly: find the cause, then fix it
once. Three readers follow it — the `/artel:debugging` skill in a person's session, the
`implementer` agent inside a run, and the `reviewer` when it checks a fix round (§7).

## 1. The rule

**No fix before the cause is understood.** Making a symptom disappear is not the same as fixing
it: if you cannot explain the failure, the defect behind it is still in the code and will surface
again somewhere harder to trace. Understanding means you can say what is wrong and why it
happens — not just the line where it showed up.

Apply it to anything that does not behave as it should: a bug, a red test, a broken build, a
slowdown. It is not for an expected red — a test you wrote first, failing because the code it
exercises is not written yet, or a stub still waiting for its task. Those are work in progress,
not defects.

## 2. The four phases

Take them in order. Reaching for a fix before the first phase is finished is exactly the failure
this document guards against.

### 2.1 Investigate

1. **Read the failure to the end.** Every message, warning and stack frame, with its file, line
   and code. Answers hide in the part that was skimmed.
2. **Make it happen on demand.** Pin down the steps, the actual outcome and the expected one, and
   confirm the failure repeats. One you cannot trigger is not ready to be fixed yet (§6).
3. **Look at what moved.** Recent commits, the working diff, new or upgraded dependencies,
   changed settings or environment.
4. **Locate the boundary.** When the path crosses several components, log what goes into and
   comes out of each one, run once, and find the first place a good value turns bad. Investigate
   only that component.
5. **Follow the bad value upstream.** Starting where it surfaced, ask which caller handed it
   over, and repeat until you reach the code that first produced it. That is where the fix goes.

The investigation is complete when the failure repeats on demand and you can name its cause.

### 2.2 Compare

1. Find the nearest code in this repository that does the same kind of thing and works.
2. Read the pattern, library or standard you rely on in full — skimming a reference is how
   half-understood code gets written.
3. Write down every way the working case and the broken one differ, including the ones that look
   irrelevant.
4. Note what the broken code relies on — other components, settings, the environment — and what
   it takes for granted about each.

### 2.3 Hypothesise

1. **One hypothesis, written as one sentence:** "X is the cause because Y." A single cause, not a
   shortlist.
2. **Probe it with the least change that could prove it wrong** — vary one thing at a time.
3. **A probe is throwaway.** Undo it before making the real change; it is never part of the fix.
4. **Replace, never stack.** When a hypothesis fails, form the next one from what the failure
   taught you; do not add a second change on top of the first.
5. **Admit the gap.** "I don't understand X yet" is the right statement when it is true. Read
   further or ask; do not guess.

### 2.4 Fix

1. **A failing test comes first.** Write the smallest test that shows the reported symptom — an
   automated test where the project has a framework, a reproduction script where it has none
   (§3) — and watch it fail. It has to be written and seen failing **before** any fix is
   applied: a test added together with the fix and first run afterwards proves nothing about
   the fix.
2. **Make one minimal change at the origin** §2.1 found. Leave unrelated improvements and
   refactoring for another task.
3. **Never weaken the failing test.** If the test has to change for the fix to pass, the fix is
   what is wrong (§6 names the one exception).
4. **Check all three:** the new test passes, the rest of the suite still passes, and the symptom
   from the report is gone.
5. **A fix that fails sends you back to §2.1** with what it revealed, never on to another attempt
   from the same spot. Three failed fixes are structural (§4).

## 3. Evidence

Inside a run the evidence is on disk, so a reviewer can check it without trusting a report's
wording.

- **Hypotheses.** A task whose `test` stage went red lists each iteration's hypothesis in its
  report file (`agents/implementer.md`, Step 6):

  ```markdown
  ## Verify iterations
  | # | Stage | Hypothesis | Result |
  |---|---|---|---|
  | 2 | test | `total()` sums before the discount because `apply()` runs after it | confirmed — fixed at `cart.py:41` |
  ```

- **The reproduction.** A behavioural fix-section row (`agents/implementer.md`, Step 3) keeps its
  red evidence in the ticket's `verify/` directory as `repro-<code>-<source>-<N>`: `<code>` the
  section's code in lower case (`crf`, `rtf`, `vf`), `<source>` the nearest `###` heading above
  the row inside its section (`tasklist` when there is none), `<N>` the row's 1-based position
  under that heading. The third row under `### review-r2` in `## Code Review Fixes` is
  `repro-crf-review-r2-3`.
  - `.json` — the envelope `verify.py task --files <the new test>` wrote, its `test` stage red.
  - `.txt` — when `verify.test` is empty, the gate's `test` stage is `skipped`. A `skipped` stage
    is never red evidence: run the host's own single-file test command instead and save line 1
    `$ <command>`, line 2 `exit <code>` (non-zero), then the output's tail.
  - A fault no test can reach (a launch failure, a platform-only crash) gets a reproduction
    script under `.artel/run/repro/`, never committed, and the report says why no test reaches
    it.
- **Probes and instrumentation** are removed before the task closes, or replaced by deliberate
  logging the task owns.

## 4. When the fix is structural

Stop and escalate instead of fixing when doing it properly needs any of:

- **an interface change** — code that calls or depends on this contract would have to change
  with it;
- **a design reversal** — the decision this code carries out is itself the mistake;
- **scope growth** — the proper fix is clearly bigger than the fault that was reported.

**Three failed fixes are structural too**: each miss is evidence that the problem does not sit
where the fixes were aimed.

These are `deviation-protocol.md` §2 **Major**. Where you are decides the handoff:

- **Inside a run** — halt before the change and return a `DEVIATION` report
  ([deviation-protocol.md](deviation-protocol.md) §4) that names the cause and says why fixing it
  properly is structural. The orchestrator asks the person.
- **In `/artel:debugging`** — report the cause, explain what makes the proper fix structural,
  leave the code as it is, and offer `/artel:issue-draft` to turn the report into a ticket.
- **In diagnose mode** (`/artel:debugging <ticket> --diagnose`) — record the cause in the
  ticket's `diagnosis.md` with status `DIAGNOSED_STRUCTURAL` and say which condition holds. No
  ticket is drafted: the ticket exists, and its orchestrator moves it to the full pipeline.

Never widen the scope quietly.

## 5. No root cause found

Draw this conclusion only once every phase in §2 has run and the evidence points there. Far more
often it means the investigation stopped too soon, so go back to §2.1 first. When it genuinely
holds:

1. Write down what you examined and what it showed.
2. Handle the condition gracefully: retry it, bound it with a timeout, or fail with a message
   that says what happened.
3. Leave logging in place so that the next occurrence produces something to read.

## 6. Special cases

- **Cannot reproduce.** Collect more before touching code: logging at the suspect boundaries,
  the environment, the precise steps, the versions involved. Guessing at a fix for something you
  cannot trigger only adds a change nobody can verify.
- **Fails only in CI.** Whatever differs between CI and your machine — versions, settings,
  environment variables, test order, parallelism — is the evidence. Add logging to the CI run
  until the difference shows.
- **No test framework.** A reproduction script will do, as long as it goes red without the fix
  and green with it.
- **The test is wrong.** A test can be the culprit, but only once you have shown that it is. The
  one legitimate reason to change a failing test is a task whose own acceptance criteria change
  the behaviour that test pins. Anything else — weakening, skipping, deleting — is a Major
  deviation.
- **The cause is in a dependency.** Hold it at a known-good version, adjust how it is set up, or
  work around it and document the workaround. When the dependency's interface itself is wrong,
  that is structural (§4).
- **Timing and flaky tests.** Poll for the condition you need, with an upper bound, instead of
  sleeping for a delay that happened to be long enough once.
- **Secrets.** Logs, settings and debug output can contain them. Never copy a secret value into
  chat, a report, an evidence file, a test or a kartoteka document, and never log one.

## 7. Where this applies in artel

| Reader | When | Follows |
|---|---|---|
| `skills/inner-loop` (the implementer's task gate) | a red `test` stage | §2.3 — one hypothesis per iteration; §3 — the hypothesis table; §4 — a structural cause is a Major deviation |
| `agents/implementer.md` | a behavioural fix-section row | §2, §3 (the reproduction), §4, §6 |
| `agents/reviewer.md` | re-reviewing a fix round | §3 — a `behavior` row closed without red-first evidence is Important |
| `/artel:debugging` | a person's bug, outside a run | §1–§6 |
| `/artel:debugging --diagnose` | the bug head (`heads/bug.md` of `feature-development`), before any work list | §2.1–§2.3, §3, §4, §5, §6 — and stops before §2.4 |
| `/artel:debugging` | a red-gate halt (`tail.md`, "Debug it here first"), at the person's choice | §1–§6; the orchestrator journals the fix and re-runs the gate |

Diagnose mode ends in a document instead of a fix: `diagnosis.md` in the ticket's spec trail,
its status `DIAGNOSED` (a confirmed cause with a local fix), `DIAGNOSED_STRUCTURAL` (a confirmed
cause whose proper fix is structural, §4) or `NOT_REPRODUCED` (§6's "cannot reproduce", after
the person was asked for more). The failing test of §2.4 is then the first task of the work
list written from it.
