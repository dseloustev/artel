---
name: run-reviewer
description: "Review changes for a ticket"
argument-hint: "[ticket-id] or [ticket-id]-[phase]"
model: sonnet
---

## Ticket Resolution

Parse `$0` into `TICKET_ID`, `TICKET_NUM`, `PHASE_NUM` per `${CLAUDE_PLUGIN_ROOT}/docs/orchestrator-common.md` §2 and `${CLAUDE_PLUGIN_ROOT}/docs/ticket-parsing.md` §§1–2. If `$0` is empty, read the first non-empty line of `<specs.dir>/.active_ticket`; if no identifier is available, error with "Error: No ticket specified. Provide a ticket ID as a parameter or set it in <specs.dir>/.active_ticket" and terminate.

## Execute

Use the Agent tool with `subagent_type: "reviewer"`, description `"Review changes for <TICKET_ID>"`, and a prompt that passes `TICKET_ID`, `TICKET_NUM`, and `PHASE_NUM` (or "all phases"). The `reviewer` agent already knows the input artifacts, priority taxonomy (Blocking / Important / Nice-to-have), the machine-readable `review/findings.json` lens output, and the `## Code Review Fixes` tasklist write-back format for ticket mode.

Wait for the agent to finish and report the summary back to the user.
