---
name: generate-tasklist
description: "Generate an iterative, testable work plan (<specs.dir>/<TICKET_ID>/tasklist.md) directly from the idea and vision files"
argument-hint: "[ticket-id] [idea-file] [vision-file]"
model: sonnet
---

## Overview

Produces `<specs.dir>/<TICKET_ID>/tasklist.md` with the Progress Report table at the top,
`Iteration N` sections underneath holding task blocks in the task grammar
(`${CLAUDE_PLUGIN_ROOT}/docs/task-grammar.md`), `**Test:**` footer per iteration. Reads the
ticket's idea and vision files as input (and the PRD's requirement IDs, when a PRD exists),
checks the draft mechanically before anyone approves it, applies KISS, and asks the user a
short set of clarifying questions before finalizing.

This is the **lean path**. It skips the PRD + plan stages used by the full feature-development
pipeline and the existing `tasklist` skill. Use `generate-tasklist` when the idea + vision are
enough to start; use `tasklist` when the full PRD/plan chain has been produced.

## Ticket Resolution

Parse `$0` into `TICKET_ID`, `TICKET_NUM`, `PHASE_NUM` per
`${CLAUDE_PLUGIN_ROOT}/docs/orchestrator-common.md` §2 and `${CLAUDE_PLUGIN_ROOT}/docs/ticket-parsing.md` §§1–2.

Tasklist is **ticket-level**. If `PHASE_NUM` is non-null, ignore it and print the one-line notice
`Note: phase argument ignored — tasklist is ticket-level; iterations are the phases.`

If `$0` is empty, read the first non-empty line of `<specs.dir>/.active_ticket`. If no
identifier is available, error with "Error: No ticket specified. Provide a ticket ID as a
parameter or set it in <specs.dir>/.active_ticket" and terminate.

### Idea and vision files (second and third arguments)

`$1` and `$2` are optional overrides for the idea and vision file paths.

- If `$1` is absent or empty, default to `<specs.dir>/<TICKET_ID>/idea.md`.
- If `$2` is absent or empty, default to `<specs.dir>/<TICKET_ID>/vision.md`.
- Strip a leading `@` if present.
- Resolve paths relative to the repo root.
- If the resolved idea file does not exist, error with `Error: idea file not found at {path}` and
  terminate.
- If the resolved vision file does not exist, error with `Error: vision file not found at
  {path}. Run /artel:generate-vision first.` and terminate.

### File paths this skill uses

All ticket artifacts live under `<specs.dir>/<TICKET_ID>/`. The directory carries the ticket;
filenames do not repeat `<TICKET_ID>` or `<TICKET_NUM>`.

- Idea (read): `{resolved idea path, default <specs.dir>/<TICKET_ID>/idea.md}`
- Vision (read): `{resolved vision path, default <specs.dir>/<TICKET_ID>/vision.md}`
- Tasklist (write): `<specs.dir>/<TICKET_ID>/tasklist.md`
- PRD (read, when it exists): `<specs.dir>/<TICKET_ID>/prd.md` — its `## Requirements` IDs only
- Draft (written by the agent; run state, a file on both spec-store paths):
  `.artel/run/<TICKET_ID>/tasklist-draft.md`

Ensure `<specs.dir>/<TICKET_ID>/` exists before writing.

### Existing-file handling

**Pipeline invocation** (from the `dev` orchestrator): if `tasklist.md` exists (kartoteka path:
`spec_store.py exists <specs.dir>/<TICKET_ID>/tasklist.md` exits 3), skip — report
`Tasklist exists — skipped` and terminate (`${CLAUDE_PLUGIN_ROOT}/docs/autonomous-run.md` §9).
The Phase 2 questions+approval round below serves as `dev`'s mini-interview and single work-list
confirmation.

**Manual invocation:** if `tasklist.md` exists (kartoteka path:
`spec_store.py exists <specs.dir>/<TICKET_ID>/tasklist.md` exits 3), `AskUserQuestion` with two
options — **Overwrite** (regenerate from scratch; progress marks are lost) / **Abort**. No
"refine in place" — a tasklist partway through implementation is better merged by hand.

## Execute

Four-phase model, matching `analysis` and `planner`:

1. **Phase 1:** `tasklist-writer` agent drafts the tasklist into
   `.artel/run/<TICKET_ID>/tasklist-draft.md` and returns clarifying questions; **Phase 1b**
   checks the draft and sends its findings back to the same agent, for at most
   `MAX_PLAN_REVIEW_ROUNDS` fix rounds.
2. **Phase 2:** Orchestrator asks the user via `AskUserQuestion` (if any), with the findings the
   rounds left open.
3. **Phase 3:** Orchestrator resumes the agent via `SendMessage` to finalize and write the file,
   then checks it once more.

### Phase 1: Draft Tasklist and Extract Questions

**Spec store.** Before dispatching, read the ticket's storage decision:
`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/spec_store.py decision <TICKET_ID>`. `fresh: true` → use
its `store` and `reason`. Anything else → resolve per `${CLAUDE_PLUGIN_ROOT}/docs/spec-storage.md`
§2.1, which may ask the user — except while `.artel/run/<TICKET_ID>/run-state.json` has
`run_active: true`: then return `STORE_UNAVAILABLE: <record>` to your caller and stop. Every
dispatch prompt in this skill carries the result verbatim, as `**Spec store:** kartoteka` or
`**Spec store:** files (<reason>)`. An agent's `STORE_UNAVAILABLE` return goes back to your caller
unchanged. This skill's own reads, existence checks and writes of spec documents follow §4.1 and
§4.2 — an existence check is `spec_store.py exists <path>` (exit 0 present, 3 absent).

**PRD.** `<specs.dir>/<TICKET_ID>/prd.md` exists (kartoteka path:
`spec_store.py exists <specs.dir>/<TICKET_ID>/prd.md` exits 0) → pass its path on the prompt's
**PRD** line; otherwise pass `none`. The agent reads it for its requirement IDs only, and Phase
1b reads the same IDs.

Use the `Agent` tool with:

- `subagent_type`: `"tasklist-writer"`
- `description`: `"Draft tasklist for <TICKET_ID>"`
- `prompt`:

```
You are drafting the iterative work plan for ticket <TICKET_ID>.

## Context

- **Ticket ID:** <TICKET_ID>
- **Ticket Number:** <TICKET_NUM>
- **Idea file (input):** <resolved idea path>
- **Vision file (input):** <resolved vision path>
- **PRD (input, requirement IDs only):** <specs.dir>/<TICKET_ID>/prd.md, or none
- **Tasklist file (output):** <specs.dir>/<TICKET_ID>/tasklist.md

## Instructions

Read the idea file and the vision file, and the PRD's `## Requirements` section when a PRD is
given. Draft an iterative, testable work plan that honors every KISS rule in your agent
definition. Use the Progress Report table + numbered Iterations + task blocks in the task
grammar + `**Test:**` footer format from your agent definition.

Return THREE things and stop:
1. A short summary of the draft (iteration count and one-line titles).
2. A numbered list of clarifying questions, or the literal string `NO_QUESTIONS`.
3. A short "KISS trade-offs" note if you deliberately omitted anything; skip the line otherwise.

Write the draft to .artel/run/<TICKET_ID>/tasklist-draft.md — never to the tasklist path yet —
and wait for resume.
```

**Save the agent ID** — Phase 1b and Phase 3 resume the same agent via `SendMessage`.

### Phase 1b: Check the draft

The mechanical plan review (`${CLAUDE_PLUGIN_ROOT}/docs/task-grammar.md` §6), run on the draft
before the approval round sees it.

1. **Requirements.** No PRD (the **PRD.** check in Phase 1) → `<requirements>` is `absent`.
   Otherwise read its active IDs. Files path first, kartoteka path (`docs/spec-storage.md`
   §4.2) second:

       python3 ${CLAUDE_PLUGIN_ROOT}/scripts/tasklist_tasks.py requirements --prd <specs.dir>/<TICKET_ID>/prd.md
       set -o pipefail; python3 ${CLAUDE_PLUGIN_ROOT}/scripts/spec_store.py get <specs.dir>/<TICKET_ID>/prd.md | python3 ${CLAUDE_PLUGIN_ROOT}/scripts/tasklist_tasks.py requirements --prd -

   Turn `data` into `<requirements>`: `present: false` → `absent`; `present: true` with an empty
   `ids` → `none`; otherwise the `ids` joined by `,`.
2. **Check.** The draft is a file on both paths — run state, never the spec trail:

       python3 ${CLAUDE_PLUGIN_ROOT}/scripts/tasklist_tasks.py --tasklist .artel/run/<TICKET_ID>/tasklist-draft.md --ticket-key <TICKET_ID> --check --requirements <requirements>

   `data.format` `legacy`, or no Critical or Important finding (exit `0`) → go to Phase 2.
   Exit `2` from either command → print `error.kind` and `error.message` and go to Phase 2: the
   approval round still runs, and the Completion says the check did not run.
3. **Fix round.** A Critical or Important finding (exit `1`), and fewer than
   `MAX_PLAN_REVIEW_ROUNDS = 2` fix rounds so far → `SendMessage` the saved agent those findings
   verbatim — `severity`, `task`, `line`, `rule`, `message` — with "Fix these in the draft,
   leave the rest as it is, and return the same three things." Then run step 2 again. Minor
   findings never start a round.
4. Whatever is still open after the rounds, Minor included, goes into Phase 2.

### Phase 2: Ask User Questions

After the agent returns the draft summary and questions:

- **If the agent returned `NO_QUESTIONS`:** skip to a single-question `AskUserQuestion` with
  header `Tasklist`: **Approve** / **Request changes** / **Abort**.
- **If the agent returned questions:** use `AskUserQuestion`. For 1–4 questions, map each to one
  question entry. For more, combine into one free-text question listing all items. Add a final
  `Tasklist` question with **Approve** / **Request changes** / **Abort**.

Findings Phase 1b left open go into the `Tasklist` question's text, Critical and Important
first, each as `<task> <severity> <rule>: <message>` — the person approves knowing them.

On a task-grammar draft the `Tasklist` question also lists every task's effective route with
its reasons, one line each — `2.3 full — declared: payment path; floor: sensitive path
(payments): src/checkout/pay.py` — read from a parser run over the draft that creates nothing:
`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/tasklist_tasks.py --tasklist .artel/run/<TICKET_ID>/tasklist-draft.md --ticket-key <TICKET_ID>`
(each child's `route_effective` and `route_reasons`; every task `full` when `review.perTask` is
`true` — `${CLAUDE_PLUGIN_ROOT}/docs/autonomous-run.md` §16). A draft the parser rejects
shows its findings and no routes. The person may change any route, down as well as up — a
**Request changes** answer such as `route 2.3: light` — and Phase 3 carries it to the writer,
which records it as `Route: <light|full> — set at approval`.

Collect the answers into a single string:

- **Approve** with no changes → `answers = "NO_CHANGES"`.
- **Abort** → terminate without writing.
- Otherwise → concatenate the answers.

### Phase 3: Resume to Finalize Tasklist

Use `SendMessage` to the saved agent ID:

```
User's answers:
[answers]

## Finalize the Tasklist

1. Incorporate the answers.
2. Write the whole tasklist in one pass to <specs.dir>/<TICKET_ID>/tasklist.md.
3. Return the single confirmation line.
```

When the agent confirms, run Phase 1b's check once more on the written tasklist — the answers
folded in can break what the draft check passed (a dropped task renumbers the rest). Files path
first, kartoteka path (`docs/spec-storage.md` §4.2) second:

    python3 ${CLAUDE_PLUGIN_ROOT}/scripts/tasklist_tasks.py --tasklist <specs.dir>/<TICKET_ID>/tasklist.md --ticket-key <TICKET_ID> --check --requirements <requirements>
    set -o pipefail; python3 ${CLAUDE_PLUGIN_ROOT}/scripts/spec_store.py get <specs.dir>/<TICKET_ID>/tasklist.md | python3 ${CLAUDE_PLUGIN_ROOT}/scripts/tasklist_tasks.py --tasklist - --ticket-key <TICKET_ID> --check --requirements <requirements>

A Critical or Important finding gets one fix `SendMessage` to the same agent — the findings,
each with its line, and "fix only these in <specs.dir>/<TICKET_ID>/tasklist.md; keep the task
grammar and the person's answers" — and the check runs once more. When it still has a Critical
or Important finding, stop and ask the person, even in `yolo` (a tasklist that fails its own
check is a guardrail, not a pause preference): **Fix it by hand** (edit the tasklist, then run
this skill again) / **Proceed anyway** (the findings go into the Completion report) / **Abort**.
Minor findings go into the Completion report.

### Phase 4: Mirror the tasklist into the task queue

Per `${CLAUDE_PLUGIN_ROOT}/docs/task-queue.md` §1, decide whether the queue path
applies. On the fallback path, skip this phase silently and continue.

On the queue path, run. Files path first, kartoteka path (`docs/spec-storage.md` §4.2)
second:

    python3 ${CLAUDE_PLUGIN_ROOT}/scripts/tasklist_tasks.py --tasklist <specs.dir>/<TICKET_ID>/tasklist.md --ticket-key <TICKET_ID>
    set -o pipefail; python3 ${CLAUDE_PLUGIN_ROOT}/scripts/spec_store.py get <specs.dir>/<TICKET_ID>/tasklist.md | python3 ${CLAUDE_PLUGIN_ROOT}/scripts/tasklist_tasks.py --tasklist - --ticket-key <TICKET_ID>

Exit `0` → follow `docs/task-queue.md` §2 steps 2–4: `task_create` each iteration
row, then each of its children with `parent_id` set to the iteration's
`task_id`, in the order emitted; then each `data.sections` entry the same way —
usually none at generation time, since no writer emits `## Final Verification` any
more; a tasklist written before 0.18.0 still mirrors its section. Surface every
`data.warnings` line.

Exit `2` → print `error.kind` and `error.message`, mirror nothing, and continue.
A failed mirror never blocks the run: the file on disk is the fallback.

### Completion

When the agent returns its confirmation line, print it verbatim plus:

- `Plan check: clean`, or each Critical and Important finding Phase 3's check reported, or
  `Plan check: not run (<error.kind>)`.
- `Next: review the iterations and adjust order/scope before starting implementation. Use
  /artel:sync-phases <TICKET_ID> to extract phase files when ready.`

## Important Rules

- **Ticket-level only.** Ignore any phase passed in `$0`.
- **Never write the tasklist directly from this skill.** All file writes happen inside the agent
  (it owns `Write`). The orchestrator only reads, asks, and messages.
- **One agent, one draft.** One `Agent` call in Phase 1, at most `MAX_PLAN_REVIEW_ROUNDS`
  fix-round `SendMessage`s in Phase 1b, one `SendMessage` in Phase 3 and at most one post-write
  fix `SendMessage` in Phase 3 — all to the same agent. Do not spawn a second agent.
- **Never silently overwrite.** If the tasklist already exists, always confirm via
  `AskUserQuestion` before proceeding.
- **KISS is the agent's job.** The orchestrator does not re-check KISS; if the user's answers
  would push the agent past its limits, the agent surfaces the conflict in the next round.
