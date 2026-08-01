---
name: tasklist
description: "Break down the plan for the ticket into a list of small tasks (tasklist)"
argument-hint: "[ticket-id] or [ticket-id]-[phase]"
model: sonnet
---

## Ticket Resolution

Parse `$0` into `TICKET_ID`, `TICKET_NUM`, `PHASE_NUM` per `${CLAUDE_PLUGIN_ROOT}/docs/orchestrator-common.md` §2 and `${CLAUDE_PLUGIN_ROOT}/docs/ticket-parsing.md` §§1–2. If `$0` is empty, read the first non-empty line of `<specs.dir>/.active_ticket`; if no identifier is available, error with "Error: No ticket specified. Provide a ticket ID as a parameter or set it in <specs.dir>/.active_ticket" and terminate.

## Execute

Use the Agent tool with `subagent_type: "task-planner"`, description `"Create tasklist for <TICKET_ID>"`, and a prompt that passes `TICKET_ID`, `TICKET_NUM`, and `PHASE_NUM` (or "all phases"). The `task-planner` agent already knows its input files, output paths, format, and rules — no need to restate them here.

Wait for the agent to finish and then report the tasklist status back to the user.

The prompt must also instruct: apply the HITL tagging rule from your agent definition
(`${CLAUDE_PLUGIN_ROOT}/docs/autonomous-run.md` §4) and set `Status: TASKLIST_READY` in the output file.
This skill never asks the user; any open question the breakdown surfaces goes to
`.artel/run/<TICKET_ID>/open-questions.md` (§3 format, `from: tasklist`).
