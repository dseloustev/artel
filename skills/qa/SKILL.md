---
name: qa
description: "Prepare a QA plan and report for a ticket or release"
argument-hint: "[ticket-id] or [ticket-id]-[phase] or R-[release-id]"
model: sonnet
---

## Ticket Resolution

Parse `$0` into `TICKET_ID`, `TICKET_NUM`, `PHASE_NUM` per `${CLAUDE_PLUGIN_ROOT}/docs/orchestrator-common.md` §2 and `${CLAUDE_PLUGIN_ROOT}/docs/ticket-parsing.md` §§1–2. Release identifiers start with `R-` and are passed through as-is. If `$0` is empty, read the first non-empty line of `<specs.dir>/.active_ticket`; if no identifier is available, error with "Error: No ticket specified. Provide a ticket ID as a parameter or set it in <specs.dir>/.active_ticket" and terminate.

## Execute

Use the Agent tool with `subagent_type: "qa"`, description `"QA plan for <TICKET_ID>"`, and a prompt that passes the parsed values (or the release id). The `qa` agent already knows how to handle release / ticket / phase scopes and its output paths (including the release-scope `<specs.releases>/<RELEASE_ID>/qa.md`, config.md) — no need to restate them here.

Wait for the agent to finish and report the verdict back to the user.
