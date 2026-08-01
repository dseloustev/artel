---
name: implementer
description: "Implement the following task from the tasklist according to the agreed plan"
argument-hint: "[ticket-id] or [ticket-id]-[phase]"
model: sonnet
---

## Ticket Resolution

Parse `$0` into `TICKET_ID`, `TICKET_NUM`, `PHASE_NUM` per `${CLAUDE_PLUGIN_ROOT}/docs/orchestrator-common.md` §2 and `${CLAUDE_PLUGIN_ROOT}/docs/ticket-parsing.md` §§1–2. If `$0` is empty, read the first non-empty line of `<specs.dir>/.active_ticket`; if no identifier is available, error with "Error: No ticket specified. Provide a ticket ID as a parameter or set it in <specs.dir>/.active_ticket" and terminate.

**Path resolution and the refuse-and-ask rule live in `${CLAUDE_PLUGIN_ROOT}/docs/ticket-parsing.md` §4–§5.** The `implementer` subagent already understands them — this skill does not duplicate path tables.

## Execute

Single-phase autonomous model (`${CLAUDE_PLUGIN_ROOT}/docs/autonomous-run.md` §1): the agent implements the next task
directly — no proposal/approval round-trip. The approved plan+tasklist is the deviation anchor; the
deviation protocol is the only escalation path.

### Phase 1: Implement the next task

Use the Agent tool with `subagent_type: "implementer"`, description `"Implement next task for
<TICKET_ID>"`, and a prompt passing TICKET_ID / TICKET_NUM / PHASE_NUM plus:

```
Implement the next incomplete task now, per your agent definition's workflow:
1. Read context; find the first `- [ ]` task in scope. If it carries a `[HITL: …]` tag, STOP and
   return `HITL: <reason>` instead of implementing — the orchestrator owns that pause.
2. Implement directly (no proposal step). Apply the verify loop (max MAX_VERIFY_ITERATIONS = 4) via
   the `/artel:inner-loop` skill: run the gate sequence from your agent configuration (inner loop on
   changed paths → codegen if needed → unscoped verify green).
3. Do not mark the task complete on a red gate. Flip the checkbox, update the Progress Report, and
   report per your completion contract.

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

Relay the agent's completion message, always including its `Verify iterations: N` and `Deviations:`
lines. When the caller is an orchestrator running in autonomous mode, it — not this skill — updates
`.artel/run/<TICKET_ID>/run-state.json` around the escalation (set/clear `pause_reason:
"deviation-escalation"`).
