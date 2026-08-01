---
name: planner
description: "Create the architecture and implementation plan for the ticket"
argument-hint: "[ticket-id] or [ticket-id]-[phase]"
model: sonnet
---

## Ticket Resolution

Parse `$0` into `TICKET_ID`, `TICKET_NUM`, `PHASE_NUM` per `${CLAUDE_PLUGIN_ROOT}/docs/orchestrator-common.md` §2 and `${CLAUDE_PLUGIN_ROOT}/docs/ticket-parsing.md` §§1–2. If `$0` is empty, read the first non-empty line of `<specs.dir>/.active_ticket`; if no identifier is available, error with "Error: No ticket specified. Provide a ticket ID as a parameter or set it in <specs.dir>/.active_ticket" and terminate.

**Path resolution and the refuse-and-ask rule live in `${CLAUDE_PLUGIN_ROOT}/docs/ticket-parsing.md` §4–§5.** The `planner` subagent already understands them — this skill does not duplicate path tables.

## Execute

This skill uses a **two-phase execution model**:

1. **Phase 1:** Subagent drafts the plan and identifies open questions.
2. **Phase 2:** Collect open questions — no user interaction; unresolved ones go to `open-questions.md` with proposed defaults (if any).
3. **Phase 3:** Resume subagent to finalize the plan with user's answers.

### Phase 1: Draft Plan and Extract Questions

Use the Agent tool with:
- `subagent_type`: `"planner"`
- `description`: `"Draft plan for <TICKET_ID>"`
- `prompt`: pass the parsed values plus the following instructions:

```
You are creating the architecture and implementation plan for ticket <TICKET_ID>{phase ? ", Phase <PHASE_NUM>" : ""}.

## Context

- **Ticket ID:** <TICKET_ID>
- **Ticket Number:** <TICKET_NUM>
- **Phase:** <PHASE_NUM> (or "all phases" when ticket-wide)

## Instructions — Draft Plan and Identify Questions

1. Determine the plan output path per `${CLAUDE_PLUGIN_ROOT}/docs/ticket-parsing.md` §4 (phase-scoped → `phase-<PHASE_NUM>/plan.md`; ticket-wide → `plan.md`).
2. Apply the **refuse-and-ask rule** in §5: if `PHASE_NUM` is null but `phase-*/` folders exist, do not write — return a refusal message naming the discovered phases and asking the user to disambiguate (`<TICKET_ID>-N` for phase-scoped, or explicit `--ticket-level` intent).
3. Read input:
   - PRD at the corresponding scope (phase-scoped → `phase-<PHASE_NUM>/prd.md` with ticket-wide `prd.md` as read-only fallback; ticket-wide → `prd.md`).
   - Research at the corresponding scope (`phase-<PHASE_NUM>/research.md` or `research.md`).
   - The host project's conventions docs (its CLAUDE.md and anything it points to) for architectural guidelines.
   - `<specs.dir>/<TICKET_ID>/idea.md` and `vision.md` — focus on Phase/Iteration <PHASE_NUM> when phase is set.
   - For phase runs, `<specs.dir>/<TICKET_ID>/phase-<PHASE_NUM>/tasks.md` if it exists.
4. Draft the plan. Phase-scoped plans use the structure: Phase Scope, Components, API contract, Data flows, NFR, Risks, Dependencies, Open questions. Ticket-wide plans omit Phase Scope/Dependencies.
5. If there are architectural alternatives, note them for potential ADR creation alongside the plan (`phase-<PHASE_NUM>/adr.md` or `adr.md`).
6. DO NOT finalize yet.

## Return Format

Return:
1. A summary of the drafted plan (key architectural decisions, components, data flows).
2. A numbered list of open questions that need user input.

If there are no open questions, return: "NO_QUESTIONS".

Return control after listing the questions.
```

**Important:** Save the agent ID from the Agent result.

### Phase 2: Collect questions (no user interaction)

This skill never asks the user (`${CLAUDE_PLUGIN_ROOT}/docs/autonomous-run.md` §3).

- **NO_QUESTIONS** → proceed to Phase 3.
- **Refusal** → report it to the caller and stop.
- **Questions** → they ride into Phase 3: the agent appends each to
  `.artel/run/<TICKET_ID>/open-questions.md` (§3 format, `from: planner`) with a proposed default
  and finalizes the plan **on the defaults**, marking each affected decision `(provisional — Q<n>)` in
  the plan text. The orchestrator bundles them at the approval pause and has this agent amend the plan
  if an answer overrides a default.

### Phase 3: Resume to Finalize Plan

Use the SendMessage tool:
- `to`: `"[agent_id from Phase 1]"`
- `message`:

**If questions were asked:**
```
Answers/defaults to proceed on:
[the caller's resolved answers when supplied; otherwise proceed on the proposed defaults you will record in .artel/run/<TICKET_ID>/open-questions.md]

## Finalize the Plan

1. Incorporate the user's answers.
2. For every unresolved question, append an open-questions.md entry (format: ${CLAUDE_PLUGIN_ROOT}/docs/autonomous-run.md §3, from: planner) with your proposed default, and mark the affected plan decisions "(provisional — Q<n>)".
3. Save the finalized plan to the path you determined in Phase 1.
4. If there are architectural alternatives, create the ADR document alongside the plan.
5. Set "Status: PLAN_APPROVED" when no provisional markers remain, else "Status: PLAN_DRAFTED".
```

**If no questions (NO_QUESTIONS):**
```
No open questions — the plan is approved.

## Finalize the Plan

1. Save the finalized plan to the path you determined in Phase 1.
2. If there are architectural alternatives, create the ADR document alongside the plan.
3. Set `Status: PLAN_APPROVED` in the plan document.
```

### Completion

When the subagent returns after finalizing, display a summary of the approved plan to the user.
