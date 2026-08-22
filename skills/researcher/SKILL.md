---
name: researcher
description: "Gather technical context and create a research document for the ticket"
argument-hint: "[ticket-id] or [ticket-id]-[phase] [--local]"
model: sonnet
---

## Ticket Resolution

Parse `$0` into `TICKET_ID`, `TICKET_NUM`, `PHASE_NUM` per `${CLAUDE_PLUGIN_ROOT}/docs/orchestrator-common.md` §2 and `${CLAUDE_PLUGIN_ROOT}/docs/ticket-parsing.md` §§1–2. If `$0` is empty, read the first non-empty line of `<specs.dir>/.active_ticket`; if no identifier is available, error with "Error: No ticket specified. Provide a ticket ID as a parameter or set it in <specs.dir>/.active_ticket" and terminate.

**Path resolution and the refuse-and-ask rule live in `${CLAUDE_PLUGIN_ROOT}/docs/ticket-parsing.md` §4–§5.** The `researcher` subagent already understands them — this skill does not duplicate path tables.

`--local` flag: skip the institutional-knowledge consultation for this run and research
from the codebase alone. Pass it through to the `researcher` agent in both phases.
Default is to consult; see `${CLAUDE_PLUGIN_ROOT}/docs/knowledge-consultation.md` §1
for how it resolves against `knowledge.adapter` and tool availability.

## Execute

This skill uses a **two-phase execution model**:

1. **Phase 1:** Subagent reads context, extracts questions, and returns them.
2. **Phase 2:** Collect open questions — no user interaction; unresolved ones go to `open-questions.md` with proposed defaults.
3. **Phase 3:** Resume subagent with answers to conduct research and write output.

### Phase 1: Extract Questions

Use the Agent tool with:
- `subagent_type`: `"researcher"`
- `description`: `"Extract questions for <TICKET_ID>"`
- `prompt`: pass the parsed values plus the following instructions:

```
You are preparing to research ticket <TICKET_ID>{phase ? ", Phase <PHASE_NUM>" : ""}.

## Context

- **Ticket ID:** <TICKET_ID>
- **Ticket Number:** <TICKET_NUM>
- **Phase:** <PHASE_NUM> (or "all phases" when ticket-wide)

## Instructions — Question Extraction Only

DO NOT conduct research yet. Your job in this phase is to read context and return questions.

1. Determine the research output path per `${CLAUDE_PLUGIN_ROOT}/docs/ticket-parsing.md` §4 (phase-scoped → `phase-<PHASE_NUM>/research.md`; ticket-wide → `research.md`).
2. Apply the **refuse-and-ask rule** in §5: if `PHASE_NUM` is null but `phase-*/` subfolders already exist for this ticket, do not proceed — return a refusal message naming the discovered phases.
3. Read the PRD at the corresponding scope (phase-scoped → `phase-<PHASE_NUM>/prd.md` with ticket-wide `prd.md` as read-only fallback; ticket-wide → `prd.md`).
4. Read context: `<specs.dir>/<TICKET_ID>/idea.md` and `vision.md` (focus on Phase/Iteration <PHASE_NUM> when phase is set). For phase runs, also `<specs.dir>/<TICKET_ID>/phase-<PHASE_NUM>/tasks.md` if it exists.
5. Find the "Open Questions" section in the PRD.
6. Formulate additional clarifying questions about implementation details, constraints, or preferences.

## Return Format

Return a numbered list of ALL questions for the user. Include:
- Open questions from the PRD (if any)
- Your own clarifying questions

If there are truly no questions, return: "NO_QUESTIONS".

Return control after listing the questions.
```

**Important:** Save the agent ID from the Agent result.

### Phase 2: Collect questions (no user interaction)

This skill never asks the user (`${CLAUDE_PLUGIN_ROOT}/docs/autonomous-run.md` §3).

- **NO_QUESTIONS** → proceed to Phase 3 with `answers = "No additional questions — proceed with research"`.
- **Refusal** → report it to the caller and stop.
- **Questions** → for each, have the agent (in the Phase 3 resume message) append an entry to
  `.artel/run/<TICKET_ID>/open-questions.md` in the §3 format (`from: researcher`) with a proposed
  default, then proceed with research **using the proposed defaults**. The questions are bundled at the
  approval pause by the orchestrator.

### Phase 3: Resume for Research

Use the SendMessage tool:
- `to`: `"[agent_id from Phase 1]"`
- `message`:

```
Answers/defaults to proceed on:
[user's answers when the caller supplied them; otherwise: "No user answers available — for every open
question, append an entry to .artel/run/<TICKET_ID>/open-questions.md (format:
${CLAUDE_PLUGIN_ROOT}/docs/autonomous-run.md §3, from: researcher) with your proposed default, then proceed on the
defaults."]

## Research Steps — Proceed Now

1. Incorporate the user's answers into your understanding.
2. Scan the codebase for components, endpoints, contracts, patterns, limitations, and risks. Scope to the active phase when one is set.
3. Document at the path you determined in Phase 1:
   - existing endpoints and contracts
   - layers and dependencies
   - patterns used
   - limitations and risks
   - resolved questions (with user answers)
   - new technical questions discovered during research
   - (phase runs only) a Phase Scope section at the top
4. Do not change code; only gather information.

## Important Rules

- **Phase scope:** when working on a specific phase, focus research on that phase's requirements.
- **Context files:** always read idea and vision files for background.
- **No code changes:** research only — do not modify code.
- **Never overwrite** the ticket-wide `research.md` from a phase-scoped run.
```

### Completion

When the subagent returns after researching, display a summary of the research document to the user.
