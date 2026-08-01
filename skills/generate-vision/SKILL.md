---
name: generate-vision
description: "Generate the host project's technical vision document (<specs.dir>/<TICKET_ID>/vision.md) from the idea and PRD"
argument-hint: "[ticket-id] [idea-file]"
model: opus
---

## Overview

Drives the `vision-writer` agent to produce `<specs.dir>/<TICKET_ID>/vision.md` from `idea.md`
and `prd.md`. Runs **after** the analysis interview; consumes the PRD's Resolved Questions so
nothing is re-asked. The agent drafts all seven sections internally, returns only genuine
questions (batched); the finished document gets **one wholesale checkpoint**. No per-section
approvals. Contract: `${CLAUDE_PLUGIN_ROOT}/docs/autonomous-run.md` §1.

## Ticket Resolution

Parse `$0` per `${CLAUDE_PLUGIN_ROOT}/docs/ticket-parsing.md` §§1–2. Vision is **ticket-level**:
if `PHASE_NUM` is non-null, ignore it and print `Note: phase argument ignored — vision is
ticket-level.` If `$0` is empty, read `<specs.dir>/.active_ticket`; if no identifier is
available, error with "Error: No ticket specified. Provide a ticket ID as a parameter or set it
in <specs.dir>/.active_ticket" and terminate.

### Inputs

- Idea (read): `$1` override or default `<specs.dir>/<TICKET_ID>/idea.md` — must exist, else
  `Error: idea file not found at {path}`.
- PRD (read): `<specs.dir>/<TICKET_ID>/prd.md` (or the phase PRD when only that exists). If no
  PRD exists, warn `Warning: no PRD found — vision will be drafted from the idea file alone` and
  continue.
- Vision (write): `<specs.dir>/<TICKET_ID>/vision.md`.

### Existing-file handling

**Pipeline invocation** (from an orchestrator): if `vision.md` exists, skip — report `Vision
exists — skipped` and terminate (`${CLAUDE_PLUGIN_ROOT}/docs/autonomous-run.md` §9).
**Manual invocation:** if `vision.md` exists, `AskUserQuestion`: **Overwrite from scratch** /
**Abort**. (Per-section refine no longer exists — the document is regenerated as a whole.)

## Execute

Three-phase model (draft → ask → finalize), plus one checkpoint.

### Phase 1: Draft the full document

Use the Agent tool with `subagent_type: "vision-writer"`, description `"Draft vision for
<TICKET_ID>"`, prompt: the ticket values, resolved input paths, plus:

```
Read the idea file, the PRD (especially Resolved Questions / Assumptions / Out of Scope), and the
codebase as needed. Draft the COMPLETE vision document — all seven sections from your section
contract — in memory. Any section fully answered by repo conventions closes as
"Standard — no deviations" plus a one-line justification.

Return THREE things and stop (do not write the file):
1. The full draft (raw markdown).
2. A numbered list of clarifying questions (only where idea+PRD are silent AND the answer changes
   the design), or `NO_QUESTIONS`. Never re-ask anything answered in Resolved Questions.
3. A "KISS trade-offs" note if anything was deliberately omitted; skip otherwise.
```

**Save the agent ID.**

### Phase 2: Ask (only if there are questions)

If the agent returned questions, present them via `AskUserQuestion` (≤4 per call, defaults first)
and collect answers. If `NO_QUESTIONS`, proceed with `answers = "NO_CHANGES"`.

### Phase 3: Finalize + checkpoint

`SendMessage`: `User's answers: [answers]. Finalize the document, set Status: VISION_READY in the
header, write it to <specs.dir>/<TICKET_ID>/vision.md, and return a per-section one-line
summary.`

Then the **wholesale checkpoint** — `AskUserQuestion`: `Vision written — approve?` with options
**Approve** / **Request changes** (changes via "Other"). On Request changes: `SendMessage` the
feedback, the agent revises and rewrites the file, repeat the checkpoint. On Approve: done.

### Completion

Print the vision path, the seven section titles each marked `written` or `standard (no
deviations)`, and `Status: VISION_READY`.

## Important Rules

- **One agent, one draft, one checkpoint.** No per-section user interaction.
- **Never write the vision file from this skill** — the agent owns Write.
- **Never re-ask resolved questions.** The PRD interview record is authoritative.
