---
name: validate
description: "Check which quality gates have been passed for a ticket or release"
argument-hint: "[ticket-id] or [ticket-id]-[phase] or R-[release-id]"
model: sonnet
---

## Ticket Resolution

Parse `$0` into `TICKET_ID`, `TICKET_NUM`, `PHASE_NUM` per `${CLAUDE_PLUGIN_ROOT}/docs/orchestrator-common.md` §2 and `${CLAUDE_PLUGIN_ROOT}/docs/ticket-parsing.md` §§1–2. Release identifiers start with `R-` and are passed through as-is. If `$0` is empty, read the first non-empty line of `<specs.dir>/.active_ticket`; if no identifier is available, error with "Error: No ticket specified. Provide a ticket ID as a parameter or set it in <specs.dir>/.active_ticket" and terminate.

## Execute

Use the Agent tool with `subagent_type: "validator"`, description `"Validate gates for <TICKET_ID>"`, and a prompt that passes the parsed values (or the release id). The `validator` agent already knows the gates (`PRD_READY`, `PLAN_APPROVED`, `TASKLIST_READY`, `IMPLEMENT_STEP_OK`, `REVIEW_OK`, `RUNTIME_OK`, `RELEASE_READY`, `DOCS_UPDATED`, `AUTOMATION_REMOVED`) and their artifact paths for release / ticket / phase scope (release scope reads `<specs.releases>/<RELEASE_ID>.md` and its tickets, config.md) — no need to restate them here.

Wait for the agent to finish and report the gate status back to the user.
