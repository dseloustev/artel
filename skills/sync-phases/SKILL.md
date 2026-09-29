---
name: sync-phases
description: "Sync phase completion status between tasklist.md and phase-N/tasks.md files"
argument-hint: "[ticket-id] or [ticket-id]-[phase]"
model: sonnet
---

## Overview

This skill synchronizes phase task completion between the ticket-wide tasklist and the per-phase `tasks.md` files inside `phase-<N>/` subfolders.

**Invocation points:** the `dev` and `feature-development` orchestrators invoke this skill automatically on phase-scoped runs — at run start (extract `phase-<N>/tasks.md` when missing) and after the phase's gates pass (sync completion status back to `tasklist.md`) (`${CLAUDE_PLUGIN_ROOT}/docs/autonomous-run.md` §9). Manual invocation remains for hand-repair.

This skill is a **worker, not an orchestrator** — like `generate-idea`, it runs inline rather than delegating to a subagent: no agent matches this job, and pure-procedure utility skills keep their procedural shape (they stay config-driven instead).

## Ticket Resolution

Parse `$0` into `TICKET_ID`, `TICKET_NUM`, `PHASE_NUM` per `${CLAUDE_PLUGIN_ROOT}/docs/orchestrator-common.md` §2 and `${CLAUDE_PLUGIN_ROOT}/docs/ticket-parsing.md` §§1–2. If `$0` is empty, read the first non-empty line of `<specs.dir>/.active_ticket`; if no identifier is available, error with "Error: No ticket specified. Provide a ticket ID as a parameter or set it in <specs.dir>/.active_ticket" and terminate.

**Path resolution lives in `${CLAUDE_PLUGIN_ROOT}/docs/ticket-parsing.md` §3–§4.** In summary:

- Tasklist (ticket-wide): `<specs.dir>/<TICKET_ID>/tasklist.md`
- Phase tasks file: `<specs.dir>/<TICKET_ID>/phase-<N>/tasks.md`
- Idea: `<specs.dir>/<TICKET_ID>/idea.md`
- Vision: `<specs.dir>/<TICKET_ID>/vision.md`

## Steps

### Step 1: Determine file paths based on arguments

Use the ticket resolution above to determine paths.

**Spec store.** This skill reads and writes spec-trail documents itself. Resolve the store
first: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/spec_store.py decision <TICKET_ID>` — `fresh: true`
→ its `store`; otherwise resolve per `${CLAUDE_PLUGIN_ROOT}/docs/spec-storage.md` §2.1, asking the
user only while no run is active for the ticket. On `kartoteka`, every spec-trail path below is
an address: operate on it as §4.1 (MCP tools) and §4.2 (scripts, by pipe) map each file
operation — never with Read/Write/Edit or a shell file command.

### Step 2: Read the tasklist and context files

1. Read `<specs.dir>/<TICKET_ID>/tasklist.md` to understand the current state of all phases.
2. Note its format (`${CLAUDE_PLUGIN_ROOT}/docs/task-grammar.md` §4): any `### Task <N>.<m>:` heading → the task grammar; none → the old format. Steps 4 and 6 branch on it; a phase file is in the format of the tasklist it was extracted from.
3. Old format only, also read for context (a task-grammar phase file is copied, not re-derived):
   - `<specs.dir>/<TICKET_ID>/idea.md` — for feature motivation and scope.
   - `<specs.dir>/<TICKET_ID>/vision.md` — for technical design and architecture.

### Step 3: Find existing phase tasks files

Use Glob to find all phase tasks files: `<specs.dir>/<TICKET_ID>/phase-*/tasks.md`. The phase number is read from the path segment (`phase-1/tasks.md` → phase `1`). On the kartoteka path the phase files are the names `phase-<N>.tasks.md` in `artifact_list(project=<project>, ticket_key=<TICKET_ID>)`.

### Step 4: Sync completed phases FROM phase tasks files TO tasklist

**This step ALWAYS runs for ALL existing phase tasks files, regardless of PHASE_NUM.**

For each existing `phase-N/tasks.md`:

1. Read the file.
2. Check if ALL tasks in that phase are marked complete (`- [x]`).
3. If the phase is complete:
   - Update the corresponding task checkboxes in the tasklist iteration body section to `[x]`.
   - Update the Progress Report table row: detect the status convention already used in the table (e.g., `✅ Done`, `🔄 In Progress`, `⬜ Pending`) and set status to the "done" variant; set progress to `X/X`.
4. If the phase is NOT complete but has some progress:
   - Sync individual task completion status (checkboxes) to the tasklist iteration body section.
   - Update progress count in the Progress Report table row (e.g., `2/4`) and set status to the "in progress" variant.

**Task grammar:** the phase file holds the iteration's task blocks (Step 6), so a "task" above is
a task block and its checkboxes are its steps. Sync step ticks: each step of a
`### Task <N>.<m>:` block in the tasklist takes the tick state of its twin in the phase file —
matched by task number and step text, never by position; a step with no twin is reported, never
guessed. A task is complete when every step is ticked; the phase is complete when every task
is. The Progress Report counts tasks: `X/Y` is completed tasks over the phase's tasks. Only task
steps sync — fix-section boxes stay in the file their writer put them in.

On the kartoteka path every checkbox, Progress Report and (Step 7) `**Current Phase:**` change to the tasklist is one `artifact_patch(project=<project>, …)` with the version bump (spec-storage.md §4.1) first and an edit per changed line (spec-storage.md §4.3).

### Step 5: Find the target phase for extraction (Step 6)

**If PHASE_NUM is specified:**
- Use PHASE_NUM as the target for Step 6 (skip scanning).

**If PHASE_NUM is NOT specified:**
- After Step 4 sync is complete, scan the tasklist for the first phase where:
  - Status is NOT the "done" variant in the Progress Report table, OR
  - Any task is marked `[ ]` (incomplete).
- Use that phase as the target for Step 6.

### Step 6: Extract incomplete phase to its `phase-N/tasks.md`

If `<specs.dir>/<TICKET_ID>/phase-<N>/tasks.md` does NOT exist for the target phase, extract it
in the tasklist's format (Step 2).

**Task grammar.** The phase file is the iteration itself — copied, never re-derived: the task
blocks are already the work, and a paraphrase would drift from the tasklist the plan review
checked.

1. Create the directory `<specs.dir>/<TICKET_ID>/phase-<N>/` if missing.
2. Create `<specs.dir>/<TICKET_ID>/phase-<N>/tasks.md`:
   ```markdown
   ---
   type: tasklist
   ticket: <TICKET_ID>
   version: <1 on the kartoteka path, 0 on the files path>
   title: "Phase N: Title"
   status: <the ticket-wide tasklist's status>
   schema: 1
   produced_by: artel:sync-phases
   ---
   # Phase N: Title

   <the tasklist's `## Iteration N: Title` section, verbatim>
   ```
   The iteration section is copied whole and unchanged: its `## Iteration N:` heading (the
   parser finds the phase's tasks under it), its `**Goal:**` line, every `### Task N.<m>:` block
   with its fields, steps and ticks, and its `**Test:**` line. No `## Context`, `## Tasks` or
   `## Technical Details` sections: the task blocks carry what the implementer needs. The phase's
   fix sections are this file's own — on a phase-scoped run their writers append them here
   (`${CLAUDE_PLUGIN_ROOT}/docs/task-queue.md` §6) — so the extraction copies none from the
   tasklist. The header follows `${CLAUDE_PLUGIN_ROOT}/docs/spec-storage.md` §3.2.

   On the kartoteka path it is `artifact_put(project=<project>, …, name="phase-<N>.tasks.md", expected_version=0)`.

**Old format.** The phase file is derived from the tasklist and the idea and vision files:

1. Create the directory `<specs.dir>/<TICKET_ID>/phase-<N>/` if missing.
2. Extract from the tasklist:
   - Phase title (from `## Phase N: Title` or `## Iteration N: Title`).
   - Goal (from `**Goal:**` line).
   - All tasks for that phase (`- [ ] N.1 ...`, `- [ ] N.2 ...`, etc.).
   - Test/acceptance criteria (from `**Test:**` line).
3. Extract additional context from idea and vision files:
   - **From idea file**: feature motivation, technical design overview, acceptance criteria, phase-specific requirements.
   - **From vision file**: class/entity structure, data model details, usage scenarios, logging approach, code examples and patterns.
4. Create `<specs.dir>/<TICKET_ID>/phase-<N>/tasks.md` with this structure:
   ```markdown
   ---
   type: tasklist
   ticket: <TICKET_ID>
   version: <1 on the kartoteka path, 0 on the files path>
   title: "Phase N: Title"
   status: <the ticket-wide tasklist's status>
   schema: 1
   produced_by: artel:sync-phases
   ---
   # Phase N: Title

   **Goal:** [extracted goal]

   ## Context

   [Relevant context extracted from idea and vision files:
   - Feature motivation (from idea)
   - Technical approach (from vision)
   - Related classes/entities
   - Data flow relevant to this phase]

   ## Tasks

   - [ ] N.1 [task description]
   - [ ] N.2 [task description]
   ...

   ## Acceptance Criteria

   **Test:** [extracted test criteria]

   ## Dependencies

   - Phase N-1 complete
   - [any other dependencies mentioned]

   ## Technical Details

   [Relevant technical details from vision file:
   - Code examples
   - Class signatures
   - Data models
   - Logging patterns]

   ## Implementation Notes

   [Extract any implementation notes from tasklist for this phase, or leave placeholder]
   ```
   The header follows `${CLAUDE_PLUGIN_ROOT}/docs/spec-storage.md` §3.2;
   `tasklist_tasks.py` skips it.

   On the kartoteka path it is `artifact_put(project=<project>, …, name="phase-<N>.tasks.md", expected_version=0)`.

### Step 7: Update Current Phase (MANDATORY)

**ALWAYS runs regardless of PHASE_NUM.**

Scan the tasklist (after Step 4 sync has been applied) to find the first phase that is still incomplete. Update the `**Current Phase:** N` line in the tasklist to reflect that phase number.

### Step 8: Report

Output a summary:
- Ticket ID used.
- Which phases were synced.
- Which `phase-N/tasks.md` was created (if any).
- Context sources used (idea/vision).
- Current phase number.
- Next actions needed.

## Rules

- Phase tasks files (`phase-N/tasks.md`) are the source of truth for task completion within that phase.
- The tasklist Progress Report table must stay in sync with actual task completion.
- Never delete or overwrite existing implementation notes in phase tasks files.
- Preserve all formatting and extra sections in existing files.
- **Old format:** always check for and use idea/vision files if they exist, extract relevant
  context for each phase based on phase number/iteration number, and include code examples from
  the vision file when applicable. A task-grammar phase file is the iteration copied verbatim —
  never add derived context to it.
- Create the ticket directory (`<specs.dir>/<TICKET_ID>/`) and the phase subdirectory (`phase-<N>/`) if they don't exist.
- **If PHASE_NUM is specified:** only create/extract a phase tasks file for that specific phase (Steps 5–6). Step 4 (sync all existing phase tasks files to tasklist) and Step 7 (Current Phase update) ALWAYS run for all phases.
- **Paths in output: repo-relative only** — see `${CLAUDE_PLUGIN_ROOT}/docs/path-conventions.md`.
