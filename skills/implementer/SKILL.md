---
name: implementer
description: "Implement the following task from the tasklist according to the agreed plan"
argument-hint: "[ticket-id] or [ticket-id]-[phase] [--local]"
model: sonnet
---

## Ticket Resolution

Parse `$0` into `TICKET_ID`, `TICKET_NUM`, `PHASE_NUM` per `${CLAUDE_PLUGIN_ROOT}/docs/orchestrator-common.md` §2 and `${CLAUDE_PLUGIN_ROOT}/docs/ticket-parsing.md` §§1–2. If `$0` is empty, read the first non-empty line of `<specs.dir>/.active_ticket`; if no identifier is available, error with "Error: No ticket specified. Provide a ticket ID as a parameter or set it in <specs.dir>/.active_ticket" and terminate.

**Path resolution and the refuse-and-ask rule live in `${CLAUDE_PLUGIN_ROOT}/docs/ticket-parsing.md` §4–§5.** The `implementer` subagent already understands them — this skill does not duplicate path tables.

`--local` flag: work this task from the tasklist file alone, never from the kartoteka
task queue. The agent cannot see your arguments, so it learns this from the
**Task queue** field of the prompt below — set it from whether `--local` appeared in
the invocation (an orchestrator that was invoked with it passes it down), and set it
on every spawn. Default is the queue; see
`${CLAUDE_PLUGIN_ROOT}/docs/task-queue.md` §1 for how it resolves against
`knowledge.adapter` and tool availability.

## Execute

Single-phase autonomous model (`${CLAUDE_PLUGIN_ROOT}/docs/autonomous-run.md` §1): the agent implements the next task
directly — no proposal/approval round-trip. The approved plan+tasklist is the deviation anchor; the
deviation protocol is the only escalation path.

### Phase 1: Implement the next task

**Spec store.** Before dispatching, read the ticket's storage decision:
`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/spec_store.py decision <TICKET_ID>`. `fresh: true` → use
its `store` and `reason`. Anything else → resolve per `${CLAUDE_PLUGIN_ROOT}/docs/spec-storage.md`
§2.1, which may ask the user — except while `.artel/run/<TICKET_ID>/run-state.json` has
`run_active: true`: then return `STORE_UNAVAILABLE: <record>` to your caller and stop. Every
dispatch prompt in this skill carries the result verbatim, as `**Spec store:** kartoteka` or
`**Spec store:** files (<reason>)`. An agent's `STORE_UNAVAILABLE` return goes back to your caller
unchanged. This skill's own reads, existence checks and writes of spec documents follow §4.1 and
§4.2 — an existence check is `spec_store.py exists <path>` (exit 0 present, 3 absent).

Use the Agent tool with `subagent_type: "implementer"`, description `"Implement next task for
<TICKET_ID>"`, and a prompt passing TICKET_ID / TICKET_NUM / PHASE_NUM plus:

```
## Context

- **Task queue:** <"local-only (--local was passed)" | "enabled">
- **Spec store:** <"kartoteka" | "files (<reason>)"> — from the decision read above (`${CLAUDE_PLUGIN_ROOT}/docs/spec-storage.md` §2.3)

Implement the next incomplete task now, per your agent definition's workflow:
1. Take the next task per `${CLAUDE_PLUGIN_ROOT}/docs/task-queue.md` §1 and §3 — a
   `task_ready` claim on the queue path, the first `- [ ]` in scope on the fallback
   path. The **Task queue** field above is §1's `--local` input. A dispatch naming
   `## Code Review Fixes`, `## Runtime Fixes`, `## Verify Fixes` or Final Verification
   is file-scan work on either path — `task_ready` never offers their rows (§6), so do
   not call it for one. On the queue path keep that task's row current per §3's
   fix-section protocol: `in_progress` when you start, `done` when you flip the box,
   `blocked` on any other exit, never `ready`; a missing row is `row not found; file only`,
   not an error. If the task carries a `[HITL: …]` tag, STOP and
   return `HITL: <reason>` instead of implementing — the orchestrator owns that pause.
2. Implement directly (no proposal step). Apply the verify loop (max MAX_VERIFY_ITERATIONS = 4) via
   the `/artel:inner-loop` skill: run the gate sequence from your agent configuration (inner loop on
   changed paths → codegen if needed → unscoped verify green).
3. Only when the last unscoped verify is green: flip the checkbox, update the Progress
   Report, and report per your completion contract. A red gate is never "done" — leave
   the checkbox as it is and return a `DEVIATION` report instead of a completion.
4. On the queue path a completion is `task_update(task_id, status="done")` followed by
   the promotion step (`${CLAUDE_PLUGIN_ROOT}/docs/task-queue.md` §3), before returning —
   for a fix-section row, `done` alone.
   Every other exit — red gate, any `DEVIATION` halt, an aborted task — releases the
   claim first with `task_update(task_id, status="blocked")`. `task_ready` offers
   `ready` rows only, so a task left `in_progress` is never re-offered and the ticket's
   queue wedges silently.

For on-demand runtime checks: when a change's effect is unclear from tests alone and `runtime.run`
(`${CLAUDE_PLUGIN_ROOT}/docs/config.md`) is configured, you may launch via the `/artel:run-app`
skill flow to observe it. This is not the `RUNTIME_OK` completion gate — it's a debugging aid
mid-task; `/artel:run-app --gate` remains the only mode the gate treats as authoritative.

Deviations follow `${CLAUDE_PLUGIN_ROOT}/docs/deviation-protocol.md`: minor → conservative option, record,
continue; major or unsure → halt and return a DEVIATION report instead of a completion.
```

**Save the agent ID** — deviation escalations resume the same agent.

### Phase 2: Handle the return

- **Completion** (ends with `Deviations:` line) → report it to the caller; done.
- **`HITL: <reason>`** → return that verbatim to the orchestrator (it owns the pause); done.
- **`DEVIATION` report** (protocol §4) → escalate:
  1. Malformed report (missing Planned / Blocked by / Options) → `SendMessage` asking the agent to
     re-emit before involving the user.
  2. Present via `AskUserQuestion`: one option per report option, recommendation first labeled
     "(Recommended)", plus **Abort task**. (Max 4 options — keep recommendation + strongest, "Other"
     carries the rest; never drop Abort task.)
  3. `SendMessage` the decision: record the deviation per protocol §3, then continue and finish the
     task, or on abort leave the checkbox unchecked, record `Decision: task aborted for re-planning`,
     and return control including the recorded deviation.
  4. A task may escalate more than once — repeat. On abort, report that the plan needs revision.

### Completion

Relay the agent's completion message as it is — the short contract: task, changed paths, the
`Report: <path>` line, and always its `Verify iterations: N` and `Deviations:` lines. Do not open
the report file to expand it into your own output; the diff and evidence live there so that
they never enter the caller's context (`${CLAUDE_PLUGIN_ROOT}/docs/autonomous-run.md` §1). When
the caller is an orchestrator running in autonomous mode, it — not this skill — updates
`.artel/run/<TICKET_ID>/run-state.json` around the escalation (set/clear `pause_reason:
"deviation-escalation"`).
