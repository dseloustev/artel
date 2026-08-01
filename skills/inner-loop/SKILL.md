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

Bounded discipline over the quality gate (`verify.fast` / `verify.commands`,
`${CLAUDE_PLUGIN_ROOT}/docs/config.md`). Judgement (what to fix, how) stays here; a command's exit
code is the verdict, never yours.

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

Every `verify.fast` / `verify.commands` command is expected to signal, by exit code:

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
this loop's own fix step thereafter) → **fast check** (`verify.fast`) → **full gate**
(`verify.commands`). Only the commands are configuration; the loop shape below is fixed.

Note: `verify.commands` runs from the host repo root (config.md) — every full-gate invocation
already covers the whole tree, so unlike a project-specific CLI with a `--paths` flag, there is no
separate "scoped full gate" vs. "final unscoped pass" to model here: the full gate you run each
iteration already is the unscoped, CI-parity check.

## Algorithm (`MAX_VERIFY_ITERATIONS = 4`)

```
i = 1; last_findings = null
loop:
  run verify.fast (config.md) > <EVIDENCE_DIR>/iteration-<i>.json; capture exit code
    (empty verify.fast → skipped, not green; proceed straight to the full gate)
  if the fast check hit an environment error: STOP-AND-ASK (quote it; never edit code to "fix" it)
  if the fast check is clean or skipped:
    run the full gate — each command in verify.commands (config.md), in order, stop at the first
    failure > <EVIDENCE_DIR>/iteration-<i>-full.json (keep the fast-pass envelope alongside it)
      (empty verify.commands → the gate is skipped, never reported green — see Rules)
    if the full gate hit an environment error: STOP-AND-ASK
    if the full gate is clean, or skipped because unconfigured: break — green
  findings = the failing command's reported issues (or its raw output, for tools with no
    structured findings)
  if findings == last_findings: STOP-AND-ASK — no progress (do not burn budget)
  fix minimally, targeting each finding's file:line (and rule, when the tool reports one)
  last_findings = findings; i += 1
  if i > MAX_VERIFY_ITERATIONS: write <EVIDENCE_DIR>/residual.json (the last envelope) and
    STOP-AND-ASK with the residual findings, separated into in-PATHS vs. outside-PATHS
```

Evidence envelopes are simple JSON — one command:

```json
{"command": "<string>", "exit_code": <int>, "output": "<captured stdout+stderr, truncated if huge>"}
```

— or, for the ordered `verify.commands` list:

```json
{"commands": [{"command": "...", "exit_code": 0, "output": "..."}, ...], "overall_exit_code": <int>}
```

## Rules

- **Never edit files outside `PATHS`.** Because this loop only ever touches files inside `PATHS`,
  any finding on a file outside it predates (or is independent of) this loop's own edits — treat it
  as pre-existing baseline, not something to fix here. Report it (labeled `pre-existing-baseline` in
  `residual.json`) rather than silently dropping it.
- **Never mark work complete while the last full-gate run was red or unresolved.** A `skipped` gate
  (unconfigured `verify.commands`) is not green, but it is not blocking either — the caller's own
  completion contract (e.g. `${CLAUDE_PLUGIN_ROOT}/agents/implementer.md`'s "Gate before done")
  decides what a `skipped` gate means for its own sign-off.
- **STOP-AND-ASK** means: report the residual findings (or the environment error) to the
  caller/user and halt — no retries beyond the budget, no silent continuation.
- **Gate degradation** (`${CLAUDE_PLUGIN_ROOT}/docs/config.md`): an empty `verify.fast` makes the
  per-edit check a no-op; an empty `verify.commands` degrades the full gate to `skipped`, never
  `green`.
