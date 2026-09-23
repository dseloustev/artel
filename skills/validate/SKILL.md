---
name: validate
description: "Check which quality gates have been passed for a ticket or release"
argument-hint: "[ticket-id] or [ticket-id]-[phase] or R-[release-id]"
model: sonnet
---

## Ticket Resolution

Parse `$0` into `TICKET_ID`, `TICKET_NUM`, `PHASE_NUM` per `${CLAUDE_PLUGIN_ROOT}/docs/orchestrator-common.md` §2 and `${CLAUDE_PLUGIN_ROOT}/docs/ticket-parsing.md` §§1–2. Release identifiers start with `R-` and are passed through as-is. If `$0` is empty, read the first non-empty line of `<specs.dir>/.active_ticket`; if no identifier is available, error with "Error: No ticket specified. Provide a ticket ID as a parameter or set it in <specs.dir>/.active_ticket" and terminate.

## Execute

**Spec store.** Before dispatching, read the ticket's storage decision:
`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/spec_store.py decision <TICKET_ID>`. `fresh: true` → use
its `store` and `reason`. Anything else → resolve per `${CLAUDE_PLUGIN_ROOT}/docs/spec-storage.md`
§2.1, which may ask the user — except while `.artel/run/<TICKET_ID>/run-state.json` has
`run_active: true`: then return `STORE_UNAVAILABLE: <record>` to your caller and stop. Every
dispatch prompt in this skill carries the result verbatim, as `**Spec store:** kartoteka` or
`**Spec store:** files (<reason>)`. An agent's `STORE_UNAVAILABLE` return goes back to your caller
unchanged. This skill's own reads, existence checks and writes of spec documents follow §4.1 and
§4.2 — an existence check is `spec_store.py exists <path>` (exit 0 present, 3 absent).
A release id (`R-…`) is always `files` (spec-storage.md §1): skip the decision read and pass
`**Spec store:** files (release scope is kept on disk)`.

Use the Agent tool with `subagent_type: "validator"`, description `"Validate gates for <TICKET_ID>"`, and a prompt that passes the parsed values (or the release id). The `validator` agent already knows the gates (`PRD_READY`, `PLAN_APPROVED`, `TASKLIST_READY`, `IMPLEMENT_STEP_OK`, `REVIEW_OK`, `RUNTIME_OK`, `RELEASE_READY`, `DOCS_UPDATED`, `AUTOMATION_REMOVED`) and their artifact paths for release / ticket / phase scope (release scope reads `<specs.releases>/<RELEASE_ID>.md` and its tickets, config.md) — no need to restate them here.

Wait for the agent to finish and report the gate status back to the user.
