# Debugging

*Adapted from superpowers' `systematic-debugging` 6.4.1 (MIT, © 2025 Jesse Vincent); the full
notice is in [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).*

The discipline artel applies whenever something behaves wrongly: find the cause, then fix it
once. Three readers follow it — the `/artel:debugging` skill in a person's session, the
`implementer` agent inside a run, and the `reviewer` when it checks a fix round (§7).

## 1. The rule

**No fix before the cause is understood.** A change that makes the symptom go away without
explaining it is not a fix: the cause is still there, and the next symptom it produces will be
harder to trace. Understood means you can say what is wrong and why — not only where it
surfaced.

The rule is for broken behaviour: a bug, a failing test, a failing build, a performance problem,
anything that does not do what it should. It is not for an expected red — a test written first
that fails because the code does not exist yet, or a stub not yet implemented. That is work in
progress, not a defect.

## 2. The four phases

Run them in order. Jumping to a fix is the failure this document exists to prevent.

### 2.1 Investigate

1. **Read the whole failure.** Every error, warning and stack trace, to the end: file paths,
   line numbers, codes. The answer is often in the line that was skimmed.
2. **Reproduce it.** The exact steps, what happened, what should have happened — and it happens
   every time. A failure you cannot trigger is not yet a failure you can fix: gather more (§6).
3. **Check what changed.** The diff, recent commits, new dependencies, configuration and
   environment.
4. **Find the boundary.** Where several components are involved, record what enters and what
   leaves each one, run once, and see where the good value turns bad. Then investigate that
   component only.
5. **Trace the bad value backwards.** From where it surfaced, ask what called this with that
   value, and keep going up until you reach the place it was first produced. The fix belongs
   there, not where the value surfaced.

Investigation is done when the failure reproduces reliably and you can state its cause.

### 2.2 Compare

1. Find the closest working analogue in the same codebase.
2. Read the reference you are following — a pattern, a library, a standard — end to end, not
   skimmed.
3. List every difference between the working case and the broken one, however small.
4. Note what the broken code depends on: other components, configuration, environment, and the
   assumptions it makes about them.

### 2.3 Hypothesise

1. **One hypothesis, one sentence:** "X is the cause because Y." One cause, not a list.
2. **Test it with the smallest change** that would tell you whether it is right — one variable
   at a time.
3. **A probe is not the fix.** Revert it before the real change; it never ships.
4. **Replace, never stack.** A wrong hypothesis is replaced by a new one built on what you
   learned; a second change is never piled on a failed one.
5. **Say what you do not understand.** "I don't understand X yet" is the right answer when it is
   true. Read more, or ask; do not guess.

### 2.4 Fix

1. **A failing test first.** The simplest test that reproduces the reported symptom — an
   automated test where the project has a framework, a reproduction script where it has none
   (§3). It must exist and fail **before** any fix is applied. A test written in the same change
   as the fix and run only afterwards proves nothing.
2. **One minimal fix, at the origin** §2.1 found. No while-I'm-here improvements, no bundled
   refactoring.
3. **Never weaken the failing test.** A test that has to change to pass says the fix is wrong,
   not the test (§6 names the one exception).
4. **Verify three things:** the new test passes, nothing else broke, and the reported symptom is
   gone.
5. **A failed fix goes back to §2.1** with what it taught you — never to another fix from where
   you stand. Three failed fixes are structural (§4).

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

Stop and escalate instead of fixing when the correct fix needs any of:

- **an interface change** — callers, consumers or contracts would have to change;
- **a design reversal** — the decision the code implements is itself wrong;
- **scope growth** — the right fix is materially larger than the reported fault.

**Three failed fixes are structural too**: each failure is evidence that the problem is not
where the fixes were aimed.

These are `deviation-protocol.md` §2 **Major**. Where you are decides the handoff:

- **Inside a run** — halt before the change and return a `DEVIATION` report
  ([deviation-protocol.md](deviation-protocol.md) §4) naming the cause and why its fix is
  structural. The orchestrator asks the person.
- **In `/artel:debugging`** — report the cause and why the fix is structural, make no fix, and
  offer `/artel:issue-draft` to turn the report into a ticket.

Never widen the scope silently.

## 5. No root cause found

Conclude this only after every phase in §2 ran and the evidence supports it. Most such
conclusions are an investigation that stopped early: go back to §2.1. When it does hold:

1. Record what was examined and what the evidence showed.
2. Add handling for the condition: a retry, a timeout, a clear error message.
3. Add instrumentation so the next occurrence leaves evidence.

## 6. Special cases

- **Cannot reproduce.** Gather more: instrumentation, environment details, exact steps,
  versions. Never guess at a fix for a failure you cannot trigger.
- **Fails only in CI.** The difference between CI and local — versions, configuration,
  environment variables, ordering, parallelism — is the evidence. Instrument the CI run to see
  it.
- **No test framework.** A reproduction script is acceptable; it must fail before the fix and
  pass after.
- **The test is wrong.** It can be the cause, but only once you have shown it is wrong. The one
  legitimate reason to change a failing test is a task whose own acceptance criteria change the
  behaviour that test pins. Anything else — weakening, skipping, deleting — is a Major deviation.
- **The cause is in a dependency.** Pin the version, change the configuration, or apply a
  documented workaround. If the dependency's interface is itself wrong, that is structural (§4).
- **Timing and flaky tests.** Wait for the condition you need — poll until it holds, with a
  timeout — never for a fixed delay that happened to be long enough once.
- **Secrets.** Logs, configuration and instrumentation can carry them. Never copy a secret value
  into chat, a report, an evidence file, a test or a kartoteka document, and never log one.

## 7. Where this applies in artel

| Reader | When | Follows |
|---|---|---|
| `skills/inner-loop` (the implementer's task gate) | a red `test` stage | §2.3 — one hypothesis per iteration; §3 — the hypothesis table; §4 — a structural cause is a Major deviation |
| `agents/implementer.md` | a behavioural fix-section row | §2, §3 (the reproduction), §4, §6 |
| `agents/reviewer.md` | re-reviewing a fix round | §3 — a `behavior` row closed without red-first evidence is Important |
| `/artel:debugging` | a person's bug, outside a run | §1–§6 |
