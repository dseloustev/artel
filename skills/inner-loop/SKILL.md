---
name: inner-loop
description: >-
  Run the bounded verify→fix→re-verify loop until the configured quality gate is green.
  MANUAL/COMPOSED ONLY — invoked explicitly or by the implementer agent; not auto-fired.
argument-hint: "[paths to scope the gate, comma-separated]"
disable-model-invocation: true
---

Composed by Read, not the Skill tool: `disable-model-invocation: true` hides this skill from
model-side Skill-tool invocation, and subagents do not inherit skills — the `implementer` agent
(`${CLAUDE_PLUGIN_ROOT}/agents/implementer.md`) consumes it by Read-ing this file by path in its
prompt. User slash-command invocation (`/artel:inner-loop`) still works.

Bounded discipline over the **task gate** (`${CLAUDE_PLUGIN_ROOT}/docs/gates.md` §1: `verify.fast`
on the changed paths, then `verify.test` on the test files among them — `docs/config.md`).
Judgement (what to fix, how) stays here; a command's exit code is the verdict, never yours. The
whole-tree gate (`verify.commands`) is the orchestrator's checkpoint gate and runs nowhere in this
loop (gates.md §1, rule 3).

## Inputs

- `PATHS` — comma-separated changed paths (from `$0`, or derive via `git status --porcelain` +
  `git diff --name-only HEAD`), excluding paths the host marks as generated (analyzer/linter
  exclusion lists, generated-file headers — the same convention
  `${CLAUDE_PLUGIN_ROOT}/agents/implementer.md`'s "Generated code is read-only" rule uses).
- `EVIDENCE_DIR` — resolve per `${CLAUDE_PLUGIN_ROOT}/docs/ticket-parsing.md`:
  `<specs.dir>/<TICKET_ID>/[phase-<PHASE_NUM>/]verify/` (read `<specs.dir>/.active_ticket` when the
  caller didn't pass a ticket id explicitly). No active ticket → skip evidence writes, report
  findings inline instead.

## Exit-code contract

Every `verify.fast` / `verify.test` command is expected to signal, by exit code:

- `0` — clean.
- Non-zero, but the command ran to completion and reported issues — **findings**: fixable, proceed
  to the fix step.
- The command failed to run at all — not found, crashed before producing output, network/dependency
  failure, toolchain version mismatch — an **environment error** (conventionally exit `2`):
  STOP-AND-ASK, quoting the failure; NEVER edit application code to "fix" it. Toolchain problems
  are not code problems.

Commands that don't distinguish "findings" from "failed to run" by exit code alone: judge from the
command's output which case applies, and treat it accordingly. When in doubt, treat an ambiguous
failure as an environment error rather than guessing at a code fix.

## Overview

Each iteration is exactly: **edit** (already done by the caller before the first iteration, or by
this loop's own fix step thereafter) → **task gate**
(`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify.py task --files <PATHS>`). Only the commands are
configuration; the loop shape below is fixed. The runner writes one envelope with two named
stages, `fast` and `test`; a half with no command, or no test path in scope, is `skipped`, never
green (gates.md §3, §4).

## Algorithm (`MAX_VERIFY_ITERATIONS = 4`)

```
i = 1; last_findings = null
loop:
  run verify.py task --files <PATHS> > <EVIDENCE_DIR>/iteration-<i>.json; capture exit code
  exit 2 → STOP-AND-ASK (quote the error; never edit code to "fix" it)
  exit 0 → break — green, or skipped (the caller's completion contract decides what skipped means)
  findings = the red stage's keys (or its raw tail, for tools with no structured findings)
  if findings == last_findings: STOP-AND-ASK — no progress (do not burn budget)
  fix minimally, targeting each finding's file:line (and rule, when the tool reports one)
  last_findings = findings; i += 1
  if i > MAX_VERIFY_ITERATIONS: write <EVIDENCE_DIR>/residual.json (the last envelope) and
    STOP-AND-ASK with the residual findings, separated into in-PATHS vs. outside-PATHS
```

The evidence envelope is the runner's own (gates.md §3): `data.stages[]` with `name`, `command`,
`exit_code`, `ok`, `keys` and `tail`, or `skipped` with its `reason`; the `test` stage also
carries `scoped` and `files`; `data.missing` lists paths that no longer exist.

## Rules

- **Never edit files outside `PATHS`.** The task gate is scoped to `PATHS` (its `test` stage to
  the test files among them), so a finding on a file outside it can only come from a
  `verify.test` without the `{files}` token — pre-existing baseline, not something to fix here.
  Report it (labeled `pre-existing-baseline` in `residual.json`) rather than silently dropping it.
- **Never mark work complete while the last task gate was red or unresolved.** A `skipped` half
  (no command, or no test path in scope) is not green, but it is not blocking either — the
  caller's own completion contract (e.g. `${CLAUDE_PLUGIN_ROOT}/agents/implementer.md`'s "Gate
  before done") decides what a `skipped` gate means for its own sign-off.
- **STOP-AND-ASK** means: report the residual findings (or the environment error) to the
  caller/user and halt — no retries beyond the budget, no silent continuation.
- **Gate degradation** (`${CLAUDE_PLUGIN_ROOT}/docs/config.md`): an empty `verify.fast` makes the
  fast half a no-op; an empty `verify.test` makes the test half `skipped`, never `green`. The
  whole-tree gate (`verify.commands`) is not this loop's: it runs at the orchestrator's
  checkpoint (gates.md §1).
