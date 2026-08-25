---
name: run-reviewer
description: "Review changes for a ticket — the phase/ticket review, or one task's diff right after its implementer returned"
argument-hint: "[ticket-id] or [ticket-id]-[phase] [--task \"<task title>\" --report <path> --package <path>]"
model: sonnet
---

## Ticket Resolution

Parse `$0` into `TICKET_ID`, `TICKET_NUM`, `PHASE_NUM` per `${CLAUDE_PLUGIN_ROOT}/docs/orchestrator-common.md` §2 and `${CLAUDE_PLUGIN_ROOT}/docs/ticket-parsing.md` §§1–2. If `$0` is empty, read the first non-empty line of `<specs.dir>/.active_ticket`; if no identifier is available, error with "Error: No ticket specified. Provide a ticket ID as a parameter or set it in <specs.dir>/.active_ticket" and terminate.

## Execute

### Ticket mode (default)

Use the Agent tool with `subagent_type: "reviewer"`, description `"Review changes for <TICKET_ID>"`, and a prompt that passes `TICKET_ID`, `TICKET_NUM`, and `PHASE_NUM` (or "all phases"). The `reviewer` agent already knows the input artifacts, priority taxonomy (Blocking / Important / Nice-to-have), the machine-readable `review/findings.json` lens output, and the `## Code Review Fixes` tasklist write-back format for ticket mode.

### Task mode (`--task`)

The per-task gate of `${CLAUDE_PLUGIN_ROOT}/docs/autonomous-run.md` §16 — an orchestrator
invokes it after one implementer completion when `review.perTask` is on. All three flags are
required; a missing one is an invocation error — report it and stop, never fall back to the
ticket review:

- `--task "<task title>"` — the task exactly as titled in the phase-aware tasklist
- `--report <path>` — the implementer's report, `.artel/run/<TICKET_ID>/reports/NNN-<slug>.md`
- `--package <path>` — the diff package `scripts/review_package.py diff` wrote

Use the Agent tool with `subagent_type: "reviewer"`, description `"Review task for
<TICKET_ID>: <task title>"`, and a prompt that states **Mode: task** and passes `TICKET_ID`,
`TICKET_NUM`, `PHASE_NUM` plus the three values verbatim. The agent's task mode knows the
rest: read the task text from the tasklist, judge the package against it and the report, write
`NNN-<slug>-review.md` beside the report, and append Blocking / Important findings under
`## Code Review Fixes`.

## Report

Wait for the agent to finish and relay its summary. In task mode the summary is the verdict
line (`Approved` / `Needs fixes`), the count of `## Code Review Fixes` tasks it appended, and
the review file's path — not the findings themselves; the caller reads the tasklist, and the
detail is in the file.
