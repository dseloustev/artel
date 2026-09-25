---
name: analysis
description: "Initialize feature: create a ticket and draft the PRD"
argument-hint: "[ticket-id] or [ticket-id]-[phase] [description-file] [--local]"
model: sonnet
---

## Ticket Resolution

Parse `$0` into `TICKET_ID`, `TICKET_NUM`, `PHASE_NUM` per `${CLAUDE_PLUGIN_ROOT}/docs/orchestrator-common.md` §2 and `${CLAUDE_PLUGIN_ROOT}/docs/ticket-parsing.md` §§1–2. If `$0` is empty, read the first non-empty line of `<specs.dir>/.active_ticket`; if no identifier is available, error with "Error: No ticket specified. Provide a ticket ID as a parameter or set it in <specs.dir>/.active_ticket" and terminate.

**Path resolution and the refuse-and-ask rule live in `${CLAUDE_PLUGIN_ROOT}/docs/ticket-parsing.md` §4–§5.** The `analyst` subagent already understands them — this skill does not duplicate path tables.

`--local` flag: skip the institutional-knowledge consultation for this run and work
from the repository and the user alone. The agent cannot see your arguments, so it
learns this from the **Knowledge consultation** field of the Context block below —
set it from whether `--local` appeared in the invocation, and set it on every spawn.
Default is to consult; see `${CLAUDE_PLUGIN_ROOT}/docs/knowledge-consultation.md` §1
for how it resolves against `knowledge.adapter` and tool availability.

## Execute

This skill runs the **upfront interview**: the analyst explores the codebase first, then interviews the
user branch-by-branch until every requirement branch is unambiguous, then writes the PRD. Contract:
`${CLAUDE_PLUGIN_ROOT}/docs/autonomous-run.md` §1. This stage is intentionally chatty — it is the
interaction budget for the silent pipeline that follows.

### Phase 0: Input gate

Before spawning any agent: if `<specs.dir>/<TICKET_ID>/idea.md` does not exist (kartoteka path:
`spec_store.py exists <specs.dir>/<TICKET_ID>/idea.md` exits 3) AND no description
file was passed as `$1`, stop and ask the user for a feature description via `AskUserQuestion`, showing
one concrete example of an actionable description (e.g. "Add a user-facing export of transaction
history to CSV from the History screen, per-network, dev flavor first"). If the available description is
too vague to act on ("make it better", "fix stuff"), reject it the same way. Never guess, never write a
placeholder PRD.

`idea.md`'s origin depends on `tracker.adapter` (`${CLAUDE_PLUGIN_ROOT}/docs/config.md`): with the
default `none`, it is a local file the operator or the `generate-idea` skill produces at this path;
other adapters may have already populated it from the tracker.

### Phase 1: Explore, then open the interview

**Spec store.** Before dispatching, read the ticket's storage decision:
`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/spec_store.py decision <TICKET_ID>`. `fresh: true` → use
its `store` and `reason`. Anything else → resolve per `${CLAUDE_PLUGIN_ROOT}/docs/spec-storage.md`
§2.1, which may ask the user — except while `.artel/run/<TICKET_ID>/run-state.json` has
`run_active: true`: then return `STORE_UNAVAILABLE: <record>` to your caller and stop. Every
dispatch prompt in this skill carries the result verbatim, as `**Spec store:** kartoteka` or
`**Spec store:** files (<reason>)`. An agent's `STORE_UNAVAILABLE` return goes back to your caller
unchanged. This skill's own reads, existence checks and writes of spec documents follow §4.1 and
§4.2 — an existence check is `spec_store.py exists <path>` (exit 0 present, 3 absent).

Use the Agent tool with:
- `subagent_type`: `"analyst"`
- `description`: `"Interview for <TICKET_ID>"`
- `prompt`: the parsed ticket values, the description-file argument, plus:

```
You are running the requirements interview for <TICKET_ID>{phase ? ", Phase <PHASE_NUM>" : ""}.

## Context

- **Ticket ID:** <TICKET_ID> / **Ticket Number:** <TICKET_NUM> / **Phase:** <PHASE_NUM> (or "all phases" when ticket-wide)
- **Description file:** [path or "none"]
- **Knowledge consultation:** <"local-only (--local was passed)" | "enabled">

## Instructions — Explore, then interview (do NOT draft the PRD yet)

1. Update `<specs.dir>/.active_ticket`; ensure the ticket directory exists; resolve the PRD path per
   `${CLAUDE_PLUGIN_ROOT}/docs/ticket-parsing.md` §4 and apply the refuse-and-ask rule (§5).
2. Read `<specs.dir>/<TICKET_ID>/idea.md`, `<specs.dir>/<TICKET_ID>/design-analysis.md`
   (if present), and the description file if provided.
3. EXPLORE FIRST: search the codebase (per your agent definition) so every question is grounded —
   never ask what the repo already answers.
4. CONSULT THE RECORD, on the same principle: per your agent definition's Interview duties and
   `${CLAUDE_PLUGIN_ROOT}/docs/knowledge-consultation.md`, resolve the gate (§1) — the
   **Knowledge consultation** field above is its `--local` input — then `index_status()`,
   `related(<project>, <canonical ticket key>)` and up to four `search_knowledge` queries
   scoped to `<project>` (`knowledge.project`), drawn from `idea.md` (§§2–3). What the record already answers is not a question either.
5. Build the design-tree question list per your agent definition's Interview duties, then return the
   FIRST batch of at most 4 questions (most load-bearing first), or `NO_QUESTIONS` if the idea is
   already unambiguous.

## Return format

Either `NO_QUESTIONS`, or a numbered batch of 1–4 questions, each with: the question, why it matters,
and a proposed default when an industry-standard one exists. End with `MORE_QUESTIONS_PENDING` if
further branches remain after this batch, else `LAST_BATCH`.
```

**Save the agent ID** — the interview loop resumes this agent repeatedly.

### Phase 2: Interview loop

Repeat until the agent returns `INTERVIEW_COMPLETE`:

1. Present the batch via `AskUserQuestion` (one entry per question; proposed defaults as the first,
   "(Recommended)" option). Vague user answers ("it depends") are sent back to the agent, which must
   split them into resolvable sub-questions in the next batch.
2. `SendMessage` the answers to the agent: `User's answers: [answers]. Resolve these branches. Return
   the next batch (same format), or INTERVIEW_COMPLETE when every branch is unambiguous or explicitly
   parked as an Assumption with user consent.`
3. On `NO_QUESTIONS` from Phase 1, skip straight to Phase 3.

### Phase 3: Finalize the PRD

`SendMessage`:

```
Interview complete. Write the PRD now:
1. Follow the PRD structure from your agent definition's Output section (goal/context, user
   stories, metrics, risks, out of scope, assumptions, resolved questions).
2. Record every Q&A pair in `## Resolved Questions`, parked items in `## Assumptions`, exclusions in
   `## Out of Scope`. `## Open Questions` must be empty. A question the institutional record
   answered is recorded there too, in the cited form your agent definition's Output section gives —
   status and ⚠ NON-CURRENT marker included.
3. Open the PRD with the document header (`${CLAUDE_PLUGIN_ROOT}/docs/spec-storage.md` §3.2) — `type: prd`, `status: PRD_READY` — and save to the path from Phase 1.
4. Return a summary: goals, key decisions, scope, count of resolved questions and assumptions.
```

### Completion

Display the summary. Report status `PRD_READY` and the resolved-question count to the caller.

## Important Rules

- **Phase scope / inheritance / never-overwrite:** unchanged — phase-scoped runs cover only that
  phase; never overwrite ticket-wide `prd.md` from a phase run.
- **Priority order** for the interview: scope > security/privacy > UX > technical details.
- **This skill does not read `vision.md`** — the vision is generated after the PRD (see the
  `generate-vision` skill).
