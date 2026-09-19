---
name: run-reviewer
description: "Review changes for a ticket — the phase/ticket review, or one task's diff right after its implementer returned"
argument-hint: "[ticket-id] or [ticket-id]-[phase] [--task \"<task title>\" --report <path> --package <path>] [--local]"
model: sonnet
---

## Ticket Resolution

Parse `$0` into `TICKET_ID`, `TICKET_NUM`, `PHASE_NUM` per `${CLAUDE_PLUGIN_ROOT}/docs/orchestrator-common.md` §2 and `${CLAUDE_PLUGIN_ROOT}/docs/ticket-parsing.md` §§1–2. If `$0` is empty, read the first non-empty line of `<specs.dir>/.active_ticket`; if no identifier is available, error with "Error: No ticket specified. Provide a ticket ID as a parameter or set it in <specs.dir>/.active_ticket" and terminate.

`--local` flag: record nothing in the kartoteka task queue — the fix tasks the agent writes
stay in the tasklist file alone, as `${CLAUDE_PLUGIN_ROOT}/docs/task-queue.md` §1 row 1
prescribes. It may appear in any position; strip it before reading `$0` and the task-mode
flags, and remember that it was passed. An orchestrator invoked with `--local` passes it on.

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

## Record the fix tasks

When the agent returns, it has appended its Blocking / Important findings under
`## Code Review Fixes` in the phase-aware tasklist — `<specs.dir>/<TICKET_ID>/phase-<PHASE_NUM>/tasks.md`
when a phase is set, `<specs.dir>/<TICKET_ID>/tasklist.md` otherwise — beneath a `### <source>`
heading of its own. Record that batch in the task queue now, before the caller dispatches any
implementer for it (`${CLAUDE_PLUGIN_ROOT}/docs/task-queue.md` §2, fix-writer rule):

1. The agent reports appending no task → skip this section. When its summary gives no count,
   run the section anyway; it is idempotent.
2. Decide the path per task-queue.md §1. `--local` was passed, `knowledge.adapter` is `none` or
   absent, `knowledge.project` is unset, or the kartoteka task tools are absent → skip this
   section: the file carries the tasks, exactly as before fix rows existed.
3. On the queue path run

       python3 ${CLAUDE_PLUGIN_ROOT}/scripts/tasklist_tasks.py --tasklist <the phase-aware tasklist> --ticket-key <TICKET_ID>

   Exit `0` → for each entry of `data.sections`, in order:
   `task_create(project=<project>, ticket_key=<TICKET_ID>, title=…, description=…, status=…)`
   the section row, then each of its `children` the same way with `parent_id` set to the
   section row's `task_id`. Skip `data.iterations`: iteration rows are the orchestrators'
   re-mirror. A child emitted `backlog` that comes back `done` → report
   `fix task #<id> is done in the queue but open in the file: <title>`. Surface every
   `data.warnings` line. Exit `2`, or a `Rejected:` line naming `kartoteka project add` →
   report it and stop recording; the tasks are in the file, which is enough.

`<project>` is `knowledge.project` from `.artel/config.json`; `<TICKET_ID>` is the canonical
key, without the phase suffix.

## Report

Wait for the agent to finish and relay its summary. In task mode the summary is the verdict
line (`Approved` / `Needs fixes`), the count of `## Code Review Fixes` tasks it appended, and
the review file's path — not the findings themselves; the caller reads the tasklist, and the
detail is in the file. In both modes add one line: `Task queue: recorded <n> fix rows` or
`Task queue: not used (<the §1 reason>)`.
