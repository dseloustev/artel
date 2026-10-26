---
name: artel-implementer
description: "Implement the following task from the tasklist according to the agreed plan"
argument-hint: "[ticket-id] or [ticket-id]-[phase] [--local] [--model <provider/model[#variant]>]"
---

## Ticket Resolution

Parse `$0` into `TICKET_ID`, `TICKET_NUM`, `PHASE_NUM` per `${ARTEL_ROOT}/docs/orchestrator-common.md` §2 and `${ARTEL_ROOT}/docs/ticket-parsing.md` §§1–2. If `$0` is empty, read the first non-empty line of `<specs.dir>/.active_ticket`; if no identifier is available, error with "Error: No ticket specified. Provide a ticket ID as a parameter or set it in <specs.dir>/.active_ticket" and terminate.

**Path resolution and the refuse-and-ask rule live in `${ARTEL_ROOT}/docs/ticket-parsing.md` §4–§5.** The `implementer` subagent already understands them — this skill does not duplicate path tables.

`--local` flag: work this task from the tasklist file alone, never from the kartoteka
task queue. The agent cannot see your arguments, so it learns this from the
**Task queue** field of the prompt below — set it from whether `--local` appeared in
the invocation (an orchestrator that was invoked with it passes it down), and set it
on every spawn. Default is the queue; see
`${ARTEL_ROOT}/docs/task-queue.md` §1 for how it resolves against
`knowledge.adapter` and tool availability.

`--model <provider/model[#variant]>`: dispatch the agent on this concrete model. It may appear
in any position; strip it before reading `$0`. Any other value is an invocation error — report
it and stop. A dispatch refused for that model (not available, or not allowed on this host) is
re-dispatched once without it. The orchestrator passes the flag to step up a fix round that
follows a failed one, and the one retry of a task whose gate stayed red
(`${ARTEL_ROOT}/docs/autonomous-run.md` §5); otherwise the skill
resolves the model itself (below).

**Choosing the model** — after the spec-store read and before the `subagent` call, first match
wins:

1. `--model` given → that model.
2. **Fix-list dispatch** — the invocation names `## Code Review Fixes`, `## Runtime Fixes`,
   `## Verify Fixes` or Final Verification → resolve `implementer.fix`
   (`python3 ${ARTEL_ROOT}/scripts/models.py resolve --site implementer.fix`); a `null`
   model → no model, no helper run.
3. **Iteration dispatch** → run the route helper (`route --next`) over the tasklist in scope
   (`<specs.dir>/<TICKET_ID>/phase-<PHASE_NUM>/tasks.md` on a phase-scoped run,
   `<specs.dir>/<TICKET_ID>/tasklist.md` otherwise):

       python3 ${ARTEL_ROOT}/scripts/tasklist_tasks.py route --tasklist <the tasklist> --ticket-key <TICKET_ID> --next

   (kartoteka path: `set -o pipefail; python3 ${ARTEL_ROOT}/scripts/spec_store.py get <the tasklist> | python3 ${ARTEL_ROOT}/scripts/tasklist_tasks.py route --tasklist - --ticket-key <TICKET_ID> --next`)
   take its `route_effective` and resolve
   `implementer.<route_effective>`
   (`python3 ${ARTEL_ROOT}/scripts/models.py resolve --site implementer.<route_effective>`);
   a `null` route, a route-helper exit `2` or a `null` model → no model; a resolver exit `2` is
   reported and stops the run.

The chosen model is passed as the `subagent` call's `model`. A call refused for its model is
re-dispatched once without one.

## Worker mode

Parallel runs do not go through this skill's serial flow: the coordinator hands each worker
session one pinned assignment, and the worker follows `agents/artel-implementer.md` § Worker mode —
the pinned `task` in the pinned `worker_root`, evidence under the assignment's
`artifact_root`, and a structured result (`review: null`) back to the coordinator.

Nothing below runs in a worker session: the route helper and the model choice, the spec-store
decision, `--next`/`task_ready` task selection, the queue updates, the `<specs.dir>` report
and `verify/` paths. A worker session never loads this skill, and this skill never dispatches
a pinned assignment — the coordinator owns session creation. The serial procedure below is
unchanged.

## Execute

Single-phase autonomous model (`${ARTEL_ROOT}/docs/autonomous-run.md` §1): the agent implements the next task
directly — no proposal/approval round-trip. The approved plan+tasklist is the deviation anchor; the
deviation protocol is the only escalation path.

### Phase 1: Implement the next task

**Spec store.** Before dispatching, read the ticket's storage decision:
`python3 ${ARTEL_ROOT}/scripts/spec_store.py decision <TICKET_ID>`. `fresh: true` → use
its `store` and `reason`. Anything else → resolve per `${ARTEL_ROOT}/docs/spec-storage.md`
§2.1, which may ask the user — except while `.artel/run/<TICKET_ID>/run-state.json` has
`run_active: true`: then return `STORE_UNAVAILABLE: <record>` to your caller and stop. Every
dispatch prompt in this skill carries the result verbatim, as `**Spec store:** kartoteka` or
`**Spec store:** files (<reason>)`. An agent's `STORE_UNAVAILABLE` return goes back to your caller
unchanged. This skill's own reads, existence checks and writes of spec documents follow §4.1 and
§4.2 — an existence check is `spec_store.py exists <path>` (exit 0 present, 3 absent).

Use the `subagent` tool with the `artel-implementer` agent, `model` set to the chosen model (left out
when the choice is `null`), description `"Implement next task for <TICKET_ID>"`, and a prompt
passing TICKET_ID / TICKET_NUM / PHASE_NUM plus:

```
## Context

- **Task queue:** <"local-only (--local was passed)" | "enabled">
- **Spec store:** <"kartoteka" | "files (<reason>)"> — from the decision read above (`${ARTEL_ROOT}/docs/spec-storage.md` §2.3)

Implement the next incomplete task now, per your agent definition's workflow:
1. Take the next task per `${ARTEL_ROOT}/docs/task-queue.md` §1 and §3 — a
   `task_ready` claim on the queue path, the first `- [ ]` in scope on the fallback
   path (the first task of the parser's `data.ready_now` —
   `${ARTEL_ROOT}/docs/task-queue.md` §4). The **Task queue** field above is §1's `--local` input.
   One task is one `### Task <N.M>:` block: work every step of it
   in this dispatch, reading the `Produces:` lines of the tasks its `Depends on:` names. A dispatch naming
   `## Code Review Fixes`, `## Runtime Fixes`, `## Verify Fixes` or Final Verification
   is file-scan work on either path — an ordinary `task_ready` never offers their rows (§6);
   the dispatch promotes its own row and claims it narrowed to its section (§3). On the
   queue path keep that task's row current per §3's fix-section protocol: `in_progress` when
   you start, `done` when you flip the box, `blocked` on any other exit; a missing row is
   `row not found; file only`, not an error. If the task carries a `[HITL: …]` tag, STOP and
   return `HITL: <reason>` instead of implementing — the orchestrator owns that pause.
2. Implement directly (no proposal step). Apply the task gate (`${ARTEL_ROOT}/docs/gates.md`
   §1) through the verify loop (max MAX_VERIFY_ITERATIONS = 4) of the `/artel-inner-loop` skill:
   `verify.fast` on the changed paths, then `verify.test` on the test files you touched and on
   the task's `Test:` files, touched or not; codegen
   when needed, then one more pass. The whole-tree gate is the orchestrator's checkpoint, not yours.
3. Only when the last task gate is green or skipped: flip the checkbox (every step of a task
   block, in one write), update the Progress
   Report, and report per your completion contract. A red gate is never "done" — leave
   the checkbox as it is and return a `DEVIATION` report instead of a completion.
4. On the queue path a completion is `task_update(task_id, status="done")` followed by
   the promotion step (`${ARTEL_ROOT}/docs/task-queue.md` §3), before returning —
   for a fix-section row, `done` alone.
   Every other exit — red gate, any `DEVIATION` halt, an aborted task — releases the
   claim first with `task_update(task_id, status="blocked")`. `task_ready` offers
   `ready` rows only, so a task left `in_progress` is never re-offered and the ticket's
   queue wedges silently.

For on-demand runtime checks: when a change's effect is unclear from tests alone and the host
project provides an app-automation skill or documented commands (check its `AGENTS.md` and its
own skill directories), use that to launch or drive the app and observe it. This is a debugging
aid mid-task, not a gate — artel defines no runtime gate or evidence contract, and the host
skill's own contract governs the evidence it leaves.

Deviations follow `${ARTEL_ROOT}/docs/deviation-protocol.md`: minor → conservative option, record,
continue; major or unsure → halt and return a DEVIATION report instead of a completion.
Needing something another task produces that its `Depends on:` does not list is Major. The
completion's `Deviations:` line names the files each deviation changed (protocol §5).
```

**Fresh dispatch, not a resume** — deviation escalations re-dispatch the same
`artel-implementer` agent; it re-reads its context files.

### Phase 2: Handle the return

- **Completion** (ends with `Deviations:` line) → report it to the caller; done.
- **`HITL: <reason>`** → return that verbatim to the orchestrator (it owns the pause); done.
- **`DEVIATION` report** (protocol §4) → escalate:
  1. Malformed report (missing Planned / Blocked by / Options) → dispatch a fresh `subagent` asking the agent to
     re-emit before involving the user.
  2. Present via the `question` tool: one option per report option, recommendation first labeled
     "(Recommended)", plus **Abort task**. (Max 4 options — keep recommendation + strongest, "Other"
     carries the rest; never drop Abort task.)
  3. Dispatch a fresh `subagent` with the decision: record the deviation per protocol §3, then continue and finish the
     task, or on abort leave the checkbox unchecked, record `Decision: task aborted for re-planning`,
     and return control including the recorded deviation.
  4. A task may escalate more than once — repeat. On abort, report that the plan needs revision.

### Completion

Relay the agent's completion message as it is — the short contract: task, changed paths, the
`Report: <path>` line, and always its `Verify iterations: N` and `Deviations:` lines.

Add one `Model:` line to the relayed contract, after `Verify iterations:` and before
`Deviations:`, written so an orchestrator can append its value to a journal line verbatim:
`Model: <value>` — the model the dispatch ran on — with the `(predicted task 2.3 at route
light; worked task 2.4)` suffix when the prediction missed (compare the completion's first
line); or `Model: none` with the reason — `fix list: implementer.fix unset`, `no ready task`,
`route lookup failed: <kind>`, or `model refused` after a refused re-dispatch. `HITL:` and
`DEVIATION` returns carry no `Model:` line.

Do not open
the report file to expand it into your own output; the diff and evidence live there so that
they never enter the caller's context (`${ARTEL_ROOT}/docs/autonomous-run.md` §1). When
the caller is an orchestrator running in autonomous mode, it — not this skill — updates
`.artel/run/<TICKET_ID>/run-state.json` around the escalation (set/clear `pause_reason:
"deviation-escalation"`).
