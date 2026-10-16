---
name: artel-generate-vision
description: "Generate the host project's technical vision document (<specs.dir>/<TICKET_ID>/vision.md) from the idea and PRD"
argument-hint: "[ticket-id] [idea-file]"
---

## Overview

Drives the `artel-vision-writer` agent to produce `<specs.dir>/<TICKET_ID>/vision.md` from `idea.md`
and `prd.md`. Runs **after** the analysis interview; consumes the PRD's Resolved Questions so
nothing is re-asked. The agent drafts all seven sections internally, returns only genuine
questions (batched); the finished document gets **one wholesale checkpoint**. No per-section
approvals. Contract: `${ARTEL_ROOT}/docs/autonomous-run.md` §1.

## Ticket Resolution

Parse `$0` per `${ARTEL_ROOT}/docs/orchestrator-common.md` §2 and `${ARTEL_ROOT}/docs/ticket-parsing.md` §§1–2. Vision is **ticket-level**:
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

**Pipeline invocation** (from an orchestrator): if `vision.md` exists (kartoteka path:
`spec_store.py exists <specs.dir>/<TICKET_ID>/vision.md` exits 3), skip — report `Vision
exists — skipped` and terminate (`${ARTEL_ROOT}/docs/autonomous-run.md` §9).
**Manual invocation:** if `vision.md` exists (kartoteka path:
`spec_store.py exists <specs.dir>/<TICKET_ID>/vision.md` exits 3), ask with the `question` tool: **Overwrite
from scratch** / **Abort**. (Per-section refine no longer exists — the document is regenerated as
a whole.)

## Execute

Three-phase model (draft → ask → finalize), plus one checkpoint.

### Phase 1: Draft the full document

**Spec store.** Before dispatching, read the ticket's storage decision:
`python3 ${ARTEL_ROOT}/scripts/spec_store.py decision <TICKET_ID>`. `fresh: true` → use
its `store` and `reason`. Anything else → resolve per `${ARTEL_ROOT}/docs/spec-storage.md`
§2.1, which may ask the user — except while `.artel/run/<TICKET_ID>/run-state.json` has
`run_active: true`: then return `STORE_UNAVAILABLE: <record>` to your caller and stop. Every
dispatch prompt in this skill carries the result verbatim, as `**Spec store:** kartoteka` or
`**Spec store:** files (<reason>)`. An agent's `STORE_UNAVAILABLE` return goes back to your caller
unchanged. This skill's own reads, existence checks and writes of spec documents follow §4.1 and
§4.2 — an existence check is `spec_store.py exists <path>` (exit 0 present, 3 absent).

Resolve the dispatch's model per the dispatch procedure in `${ARTEL_ROOT}/docs/agents.md` `## Models` (site `planner.analysis`).

Use the `subagent` tool with the `artel-vision-writer` agent, description `"Draft vision for
<TICKET_ID>"`, prompt: the ticket values, the **Spec store:** field, the resolved input paths and
the run-state paths, plus:

```
Read the idea file, the PRD (especially Resolved Questions / Assumptions / Out of Scope), and the
codebase as needed. Draft the COMPLETE vision document — all seven sections from your section
contract — and write it to `.artel/run/<TICKET_ID>/vision-draft.md` (run state — a file on both
spec-store paths; never the spec trail). Any section fully answered by repo conventions closes as
"Standard — no deviations" plus a one-line justification.

Return THREE things and stop (do not write <specs.dir>/<TICKET_ID>/vision.md yet):
1. The draft's path: `.artel/run/<TICKET_ID>/vision-draft.md`.
2. A numbered list of clarifying questions (only where idea+PRD are silent AND the answer changes
   the design), or `NO_QUESTIONS`. Never re-ask anything answered in Resolved Questions. An
   architecture choice may carry a sketch on each option (your agent definition, Step 1).
3. A "KISS trade-offs" note if anything was deliberately omitted; skip otherwise.
```

**Fresh dispatch, not a resume** — every re-dispatch is a fresh child that remembers nothing: the
draft lives at `.artel/run/<TICKET_ID>/vision-draft.md` and every continuation prompt carries it
plus the accumulated answers, the ticket and phase, the resolved paths and the **Spec store:**
decision.

### Phase 2: Ask (only if there are questions)

If the agent returned questions, present them via the `question` tool (≤4 per call, defaults first)
and collect answers. A sketch the agent wrote for an option goes into the question text unchanged,
beneath that option's label — the `question` tool has no `preview` field: a `mermaid` fence stays
a `mermaid` fence (the client renders the diagram), a monospace wireframe stays a plain block.
Sketches go on single-choice questions only — a batch
that mixes sketched questions with multi-choice ones is asked as two calls, the sketched
questions first. If `NO_QUESTIONS`, proceed with `answers = "NO_CHANGES"`.

### Phase 3: Finalize + checkpoint

Dispatch a fresh `subagent` to the `artel-vision-writer` agent (model per the dispatch procedure, `${ARTEL_ROOT}/docs/agents.md` `## Models`, site `planner.analysis`), prompt:

```
Ticket <TICKET_ID> — **Spec store:** <the decision>.
Read the draft at `.artel/run/<TICKET_ID>/vision-draft.md`, the idea file at <idea-path> and the
PRD at <prd-path> or `none`.

User's answers: [answers].

Finalize the document from the draft, set status: VISION_READY in its header, write it to
<vision-path>, and return a per-section one-line summary.
```
The header is defined in `${ARTEL_ROOT}/docs/spec-storage.md` §3.2.

Then the **wholesale checkpoint** — the `question` tool: `Vision written — approve?` with options
**Approve** / **Request changes** (changes via "Other"). On Request changes: dispatch a fresh `subagent` to the `artel-vision-writer` agent (model per the dispatch procedure, `${ARTEL_ROOT}/docs/agents.md` `## Models`, site `planner.analysis`), prompt:

```
Ticket <TICKET_ID> — **Spec store:** <the decision>.
Read the current document at <vision-path> (kartoteka path: `spec_store.py get <vision-path>`).

Feedback: [the user's feedback].

Revise the document, rewrite <vision-path> whole, and return the updated per-section summary.
```

Repeat the checkpoint until **Approve** — on Approve, done.

### Completion

Print the vision path, the seven section titles each marked `written` or `standard (no
deviations)`, and status `VISION_READY`.

## Important Rules

- **One agent, one draft, one checkpoint.** No per-section user interaction.
- **Never write the vision file from this skill** — the agent owns `write`.
- **Never re-ask resolved questions.** The PRD interview record is authoritative.
