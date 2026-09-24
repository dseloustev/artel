---
name: docs-update
description: "Update documentation based on ticket work"
argument-hint: "[ticket-id] or [ticket-id]-[phase]"
model: sonnet
---

## Ticket Resolution

Parse `$0` into `TICKET_ID`, `TICKET_NUM`, `PHASE_NUM` per `${CLAUDE_PLUGIN_ROOT}/docs/orchestrator-common.md` §2 and `${CLAUDE_PLUGIN_ROOT}/docs/ticket-parsing.md` §§1–2. If `$0` is empty, read the first non-empty line of `<specs.dir>/.active_ticket`; if no identifier is available, error with "Error: No ticket specified. Provide a ticket ID as a parameter or set it in <specs.dir>/.active_ticket" and terminate.

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

Use the Agent tool with `subagent_type: "tech-writer"`, description `"Update docs for <TICKET_ID>"`, and a prompt that passes `TICKET_ID`, `TICKET_NUM`, and `PHASE_NUM` (or "all phases"). The `tech-writer` agent already knows the artifact inputs and output paths (`<specs.dir>/<TICKET_ID>/summary.md` or its phase-scoped variant, plus `CHANGELOG.md`) — no need to restate them here.

Wait for the agent to finish and show the documentation diff.

In the pipeline this skill runs **once per ticket**, ticket-wide, on the last phase before its checkpoint
commit (`feature-development` gate 10), so that commit carries the docs; the phase-scoped output
exists for manual runs.
