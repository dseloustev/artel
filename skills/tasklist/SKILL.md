---
name: tasklist
description: "Break down the plan for the ticket into a list of small tasks (tasklist)"
argument-hint: "[ticket-id] or [ticket-id]-[phase] [--local]"
model: sonnet
---

## Ticket Resolution

Parse `$0` into `TICKET_ID`, `TICKET_NUM`, `PHASE_NUM` per `${CLAUDE_PLUGIN_ROOT}/docs/orchestrator-common.md` §2 and `${CLAUDE_PLUGIN_ROOT}/docs/ticket-parsing.md` §§1–2. If `$0` is empty, read the first non-empty line of `<specs.dir>/.active_ticket`; if no identifier is available, error with "Error: No ticket specified. Provide a ticket ID as a parameter or set it in <specs.dir>/.active_ticket" and terminate.

`--local` flag: mirror nothing into the kartoteka task queue — the tasklist file alone
carries the work, as `${CLAUDE_PLUGIN_ROOT}/docs/task-queue.md` §1 row 1 prescribes. It may
appear in any position; strip it before reading `$0`, and remember that it was passed.
`feature-development` passes it on when it was invoked with it.

## Execute

Use the Agent tool with `subagent_type: "task-planner"`, description `"Create tasklist for <TICKET_ID>"`, and a prompt that passes `TICKET_ID`, `TICKET_NUM`, and `PHASE_NUM` (or "all phases"). The `task-planner` agent already knows its input files, output paths, format, and rules — no need to restate them here.

Wait for the agent to finish and then report the tasklist status back to the user.

The prompt must also instruct: apply the HITL tagging rule from your agent definition
(`${CLAUDE_PLUGIN_ROOT}/docs/autonomous-run.md` §4) and set `Status: TASKLIST_READY` in the output file.
This skill never asks the user; any open question the breakdown surfaces goes to
`.artel/run/<TICKET_ID>/open-questions.md` (§3 format, `from: tasklist`).

### Mirror the tasklist into the task queue

Skipped entirely when `--local` was passed (§1 row 1). Otherwise, per
`${CLAUDE_PLUGIN_ROOT}/docs/task-queue.md` §1, decide whether the queue path applies. On the
fallback path, skip this step silently and continue.

On the queue path, run:

    python3 ${CLAUDE_PLUGIN_ROOT}/scripts/tasklist_tasks.py --tasklist <specs.dir>/<TICKET_ID>/tasklist.md --ticket-key <TICKET_ID>

Exit `0` → follow `docs/task-queue.md` §2 steps 2–4: `task_create` each iteration
row, then each of its children with `parent_id` set to the iteration's
`task_id`, in the order emitted; then each `data.sections` entry the same way —
at generation time that is the `## Final Verification` section. Surface every
`data.warnings` line.

Exit `2` → print `error.kind` and `error.message`, mirror nothing, and continue.
A failed mirror never blocks the run: the file on disk is the fallback.
