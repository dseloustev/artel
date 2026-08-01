---
name: docs-update
description: "Update documentation based on ticket work"
argument-hint: "[ticket-id] or [ticket-id]-[phase]"
model: sonnet
---

## Ticket Resolution

Parse `$0` into `TICKET_ID`, `TICKET_NUM`, `PHASE_NUM` per `${CLAUDE_PLUGIN_ROOT}/docs/orchestrator-common.md` §2 and `${CLAUDE_PLUGIN_ROOT}/docs/ticket-parsing.md` §§1–2. If `$0` is empty, read the first non-empty line of `<specs.dir>/.active_ticket`; if no identifier is available, error with "Error: No ticket specified. Provide a ticket ID as a parameter or set it in <specs.dir>/.active_ticket" and terminate.

## Execute

Use the Agent tool with `subagent_type: "tech-writer"`, description `"Update docs for <TICKET_ID>"`, and a prompt that passes `TICKET_ID`, `TICKET_NUM`, and `PHASE_NUM` (or "all phases"). The `tech-writer` agent already knows the artifact inputs and output paths (`<specs.dir>/<TICKET_ID>/summary.md` or its phase-scoped variant, plus `CHANGELOG.md`) — no need to restate them here.

Wait for the agent to finish and show the documentation diff.
