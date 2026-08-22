---
name: generate-tasklist
description: "Generate an iterative, testable work plan (<specs.dir>/<TICKET_ID>/tasklist.md) directly from the idea and vision files"
argument-hint: "[ticket-id] [idea-file] [vision-file]"
model: sonnet
---

## Overview

Produces `<specs.dir>/<TICKET_ID>/tasklist.md` with the Progress Report table at the top,
`Iteration N` sections underneath, checkbox tasks grouped by file, `**Test:**` footer per
iteration. Reads the ticket's idea and vision files as input, applies KISS, and asks the user a
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

Ensure `<specs.dir>/<TICKET_ID>/` exists before writing.

### Existing-file handling

**Pipeline invocation** (from the `dev` orchestrator): if `tasklist.md` exists, skip — report
`Tasklist exists — skipped` and terminate (`${CLAUDE_PLUGIN_ROOT}/docs/autonomous-run.md` §9).
The Phase 2 questions+approval round below serves as `dev`'s mini-interview and single work-list
confirmation.

**Manual invocation:** if `tasklist.md` exists, `AskUserQuestion` with two options —
**Overwrite** (regenerate from scratch; progress marks are lost) / **Abort**. No "refine in
place" — a tasklist partway through implementation is better merged by hand.

## Execute

Three-phase model, matching `analysis` and `planner`:

1. **Phase 1:** `tasklist-writer` agent drafts the tasklist and returns clarifying questions.
2. **Phase 2:** Orchestrator asks the user via `AskUserQuestion` (if any).
3. **Phase 3:** Orchestrator resumes the agent via `SendMessage` to finalize and write the file.

### Phase 1: Draft Tasklist and Extract Questions

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
- **Tasklist file (output):** <specs.dir>/<TICKET_ID>/tasklist.md

## Instructions

Read the idea file and the vision file. Draft an iterative, testable work plan that honors every
KISS rule in your agent definition. Use the Progress Report table + numbered Iterations +
file-grouped checkbox tasks + `**Test:**` footer format from your agent definition.

Return THREE things and stop:
1. A short summary of the draft (iteration count and one-line titles).
2. A numbered list of clarifying questions, or the literal string `NO_QUESTIONS`.
3. A short "KISS trade-offs" note if you deliberately omitted anything; skip the line otherwise.

Do not write the file yet — wait for resume.
```

**Save the agent ID** — Phase 3 resumes the same agent via `SendMessage`.

### Phase 2: Ask User Questions

After the agent returns the draft summary and questions:

- **If the agent returned `NO_QUESTIONS`:** skip to a single-question `AskUserQuestion` with
  header `Tasklist`: **Approve** / **Request changes** / **Abort**.
- **If the agent returned questions:** use `AskUserQuestion`. For 1–4 questions, map each to one
  question entry. For more, combine into one free-text question listing all items. Add a final
  `Tasklist` question with **Approve** / **Request changes** / **Abort**.

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

### Phase 4: Mirror the tasklist into the task queue

Per `${CLAUDE_PLUGIN_ROOT}/docs/task-queue.md` §1, decide whether the queue path
applies. On the fallback path, skip this phase silently and continue.

On the queue path, run:

    python3 ${CLAUDE_PLUGIN_ROOT}/scripts/tasklist_tasks.py --tasklist <specs.dir>/<TICKET_ID>/tasklist.md --ticket-key <TICKET_ID>

Exit `0` → follow `docs/task-queue.md` §2 steps 2–3: `task_create` each iteration
row, then each of its children with `parent_id` set to the iteration's
`task_id`, in the order emitted. Surface every `data.warnings` line.

Exit `2` → print `error.kind` and `error.message`, mirror nothing, and continue.
A failed mirror never blocks the run: the file on disk is the fallback.

### Completion

When the agent returns its confirmation line, print it verbatim plus:

- `Next: review the iterations and adjust order/scope before starting implementation. Use
  /artel:sync-phases <TICKET_ID> to extract phase files when ready.`

## Important Rules

- **Ticket-level only.** Ignore any phase passed in `$0`.
- **Never write the tasklist directly from this skill.** All file writes happen inside the agent
  (it owns `Write`). The orchestrator only reads, asks, and messages.
- **One agent, one draft.** One `Agent` call in Phase 1, one `SendMessage` in Phase 3. Do not
  spawn a second agent.
- **Never silently overwrite.** If the tasklist already exists, always confirm via
  `AskUserQuestion` before proceeding.
- **KISS is the agent's job.** The orchestrator does not re-check KISS; if the user's answers
  would push the agent past its limits, the agent surfaces the conflict in the next round.
