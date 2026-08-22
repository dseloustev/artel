---
name: dev
description: "Lean autonomous dev workflow: mini-interview -> one work-list confirmation -> autonomous implement + review + runtime gate"
argument-hint: "[ticket-id] or [ticket-id]-[phase] [description-file] [--mode=yolo|plan-gate|full-gates]"
model: sonnet
---

Autonomous orchestrator for the lean dev loop. Contract:
`${CLAUDE_PLUGIN_ROOT}/docs/autonomous-run.md`; shared procedures:
`${CLAUDE_PLUGIN_ROOT}/docs/orchestrator-common.md`; commit/push procedure: `## Checkpoint
commits & pushes` in `${CLAUDE_PLUGIN_ROOT}/skills/feature-development/SKILL.md` (shared). Pass
`$0` (on multi-phase traversal the current `<TICKET_ID>-<N>`) to every sub-skill. No PRD / plan /
QA / docs gates. `--step` = legacy step-by-step mode (per-task confirmations, no run-state
writes). `--mode=full-gates` aliases `--step`; `--mode=yolo` skips the step-2 confirmation
(proceed on the presented work list as-is); default `plan-gate` keeps it. Mode contract:
autonomous-run.md §10.

## Workflow

### 0. Config gate

Read `.artel/config.json` per `${CLAUDE_PLUGIN_ROOT}/docs/config.md`. Missing → `Skill: setup`
(the one-time init interview), then continue with the written config. Present → run the
start-time check config.md's "When the adapter is unusable" prescribes for entry points:
`vcs.adapter: "bitbucket-mcp"` with an empty `vcs.mcpToolPrefix` is a configuration error —
report it and stop before the pipeline starts rather than failing hours later at the PR stage.

### 1. Set active ticket

Write `$0` to `<specs.dir>/.active_ticket`; ensure `<specs.dir>/<TICKET_ID>/` exists. The
pointer stays phase-accurate for the whole run: on multi-phase traversal (step 4) it is rewritten
to `<TICKET_ID>-<N>` at the start of each phase and advanced to the next incomplete phase after
each phase checkpoint (step 7.5).

### 2. Input ladder — establish the work list

On phase-scoped runs (`PHASE_NUM` set), first invoke `Skill: sync-phases` with `$0` to extract
`phase-<N>/tasks.md` from `tasklist.md` when it is missing.

1. **Tasklist with incomplete `- [ ]` tasks exists** (phase-scoped `phase-<N>/tasks.md` or
   ticket-wide `tasklist.md`) → it is the work list. Present a one-screen summary (tasks + any
   `[HITL: …]` tags) via `AskUserQuestion` — **Confirm** / **Adjust** (feedback via "Other"). This
   is dev's one pause.
2. **Else `idea.md` + `vision.md` exist** → `Skill: generate-tasklist` with `$0`. Its
   questions+approval round IS the mini-interview and the one pause — do not add another.
3. **Else** → mini-interview: if `$1` (description file) or the user's inline description is
   unambiguous, zero questions; otherwise ask only what is genuinely ambiguous (≤4 per
   `AskUserQuestion`, max two rounds, grounded in the codebase). Then present your understanding +
   proposed work list — **Confirm** / **Adjust**. On confirm, write the work items as checkbox
   tasks into the phase-aware tasklist path so progress is trackable.

The confirmed work list is the deviation anchor. The confirmation presentation also notes that
confirming authorizes the run's checkpoint commits & pushes to `origin` (work-list docs now, one
commit+push per completed phase — procedure: `feature-development` `## Checkpoint commits &
pushes`).

**`yolo` only:** present nothing — the derived work list stands. In ladder branch 3, an ambiguous
description still asks (unresolved ambiguity is a guardrail, not a pause preference).

### 3. Arm the run

Run the risk classifier (autonomous-run.md §10) over the confirmed work list, plus `idea.md`/
`vision.md` when present. `forced_floor: "full-gates"` ⇒ stop here: report the matched sensitive
categories and instruct the user to re-run with `--step`. Otherwise write
`.artel/run/<TICKET_ID>/run-state.json` per autonomous-run.md §2: `run_active: true`,
`completed: false`, `pause_reason: null`, fresh `started_at`, zeroed counters, plus the six mode
fields (§10), with `gates_confirmed: ["TASKLIST_READY"]`. Announce the effective mode and reasons.
On resume, re-derive the mode fields before re-arming — never trust stale ones. From here the run
is silent except deviations, HITL tasks, and cap escalations. Create
`.artel/run/<TICKET_ID>/run-journal.md` with the run-start entry (autonomous-run.md §11): mode
resolution, reasons, HITL tags count.

Then run the **work-list checkpoint** (procedure: `feature-development` `## Checkpoint commits &
pushes`): commit `<specs.dir>/<TICKET_ID>/**` + `<specs.dir>/.active_ticket` and push — subject
`docs: <TICKET_ID> work list` (phase runs: `docs: <TICKET_ID> phase <N> work list`). Journal it as
an external action. No verify gate here (`verify.commands`) — docs only, no code yet.

### 4. Implement (autonomous)

**Re-mirror first.** Per `${CLAUDE_PLUGIN_ROOT}/docs/task-queue.md` §1 rows 2-4
(this skill carries no local-only flag, so row 1 cannot apply — do not add one here)
and §2, on the queue path run:

    python3 ${CLAUDE_PLUGIN_ROOT}/scripts/tasklist_tasks.py --tasklist <specs.dir>/<TICKET_ID>/tasklist.md --ticket-key <TICKET_ID>

and `task_create` the rows it emits. The step is create-only and idempotent, so
this repairs a tasklist that was hand-edited or generated before the queue was
reachable, and never resets a `done` row or undoes a promotion. Exit `2` → report
and continue on the fallback path. On phase-scoped runs this lands after
`sync-phases`, which is what keeps `tasklist.md` current when it is read.

**Phase traversal:** explicit `<TICKET_ID>-<N>` in `$0` → run exactly that phase (one pass of
steps 4–7.5). Ticket-wide `$0` with a multi-phase `tasklist.md` (Progress Report table / `##
Iteration N` headers) → loop the remaining incomplete phases in order; each iteration: write
`<TICKET_ID>-<N>` to `<specs.dir>/.active_ticket`; update `run-state.json`'s `ticket` field and
refresh `started_at` (a phase boundary re-arms the wall-clock budget); delete a stale ticket-wide
`review.md` if present (the previous phase's review survives in that phase's checkpoint commit;
deletion resets the review-round counter); run `Skill: sync-phases` with `<TICKET_ID>-<N>`
(extract `phase-<N>/tasks.md` when missing); then run steps 4–7.5 passing `<TICKET_ID>-<N>` to
every sub-skill. Single-phase work → one pass ending at step 7.5.

Loop `Skill: implementer` with `$0` (or `$0 $1` when driving from a description file) until every
task is `- [x]`. Handle returns exactly as `feature-development` step 5 does: completions →
aggregate `Deviations:` / `Verify iterations:`; `HITL:` → `pause_reason: "hitl-task"`, ask, clear,
resume; `DEVIATION` escalation → bracket with `pause_reason: "deviation-escalation"`; aborted task
→ `pause_reason: "cap-escalation"`, stop and report that the plan needs revision.

### 5. Refresh the code index (optional host hook)

Optional host index-refresh hook (`${CLAUDE_PLUGIN_ROOT}/docs/orchestrator-common.md` §1): run it
when the host has wired one up; silently absent otherwise.

### 6. Review (capped loop)

`Skill: run-reviewer` with `$0`. Blocking/Important findings → `Skill: implementer` on the
`## Code Review Fixes` tasks → re-review. `review.md` `**Review round:**` at
`MAX_REVIEW_ROUNDS = 3` → cap escalation (consolidated findings via `AskUserQuestion`; on guidance
delete `review.md` and resume).

### 7. Runtime gate (RUNTIME_OK)

Surface check: with `runtime.surface` set (config.md), collect the run's changed files (diff vs
the default branch plus the working tree) and match them against the globs; no match → write
`RUNTIME_OK: skipped (no runtime surface)` to the phase-aware `runtime/observation.md` and move
on. No `runtime.run` configured → the gate records `skipped (not configured)` (run-app reports
this itself). Otherwise `Skill: run-app` with `--gate`. RED caused by a **runtime error in app
code** (runtime errors / ERROR logs / a broken UI tree) → append the quoted error as a `- [ ]`
task under `## Runtime Fixes` in the phase-aware tasklist (mirroring `## Code Review Fixes`);
when the RED stems from incomplete cross-phase wiring (this phase's code invokes pieces a later
phase will build), word the fix task to create the **minimal stubs** that restore launch —
no-op implementations / placeholder surfaces with a `TODO: phase <M>` marker — rather than real
implementations; stubbing is the expected resolution at a phase boundary and is recorded in the
completion's `Deviations:` line. Run `Skill: implementer` once (`MAX_RUNTIME_RETRIES = 1`;
counter in `.artel/run/<TICKET_ID>/runtime-observation.md`, autonomous-run.md §5) — it picks the
fix task up as the first incomplete task — then re-run the gate; second RED → cap escalation.
RED from an **environment failure** (a launch/setup failure of `runtime.run` itself, not app
code — run-app stops-and-asks for these) → cap escalation immediately, no implementer round. Do
not trust a stale green — re-run unless the observation postdates the last change to files
matching `runtime.surface` (or the last code change, when it is unset).

**Journal (§11):** append an entry to `run-journal.md` at every gate completion, pause/resume, and
external action. **Budget:** before dispatching any fix-list implementer round (review §6, runtime
§7), increment `counters.correction_rounds`; at `MAX_TOTAL_CORRECTION_ROUNDS = 8` → journal the
breach, `pause_reason: "cap-escalation"`, consolidated report via `AskUserQuestion`, stop.

### 7.5 Phase checkpoint

On phase-scoped work (explicit phase or a traversal iteration) invoke `Skill: sync-phases` with
`<TICKET_ID>-<N>` first (sync completion back to `tasklist.md`), then run the phase-end checkpoint
per `feature-development` `## Checkpoint commits & pushes`: the `verify.commands` gate → capped
`## Verify Fixes` implementer rounds (`MAX_CHECKPOINT_VERIFY_ROUNDS = 2`, counted toward
`counters.correction_rounds`) → explicit staging → commit (`feat|fix|refactor: <TICKET_ID> phase
<N> - <phase title>`; no phase → `<work summary>`) → `git push -u origin <branch>` → journal. Then
advance `.active_ticket` to the next incomplete phase and loop back to step 4 while incomplete
phases remain.

### 8. Complete

All tasks `- [x]` across all traversed phases, review clean (no unresolved Blocking/Important),
runtime green or skipped, final phase checkpoint pushed → update `run-state.json`:
`completed: true`, `run_active: false`. Append the completion entry to the journal. Then
description-file sync per `orchestrator-common.md` (when `$1` has checkbox tasks).

### 9. Report

Ticket; phases traversed; tasks completed; aggregated `Deviations:` line (`none` when clean);
verify-iteration total and review rounds; runtime gate status (green / red / skipped); checkpoint
commits (hash + subject each, incl. push results); description-sync status; reminder that opening
the PR remains manual (dev has no PR gate — the work itself is already committed and pushed by the
checkpoints); effective mode + why (`mode_reasons`); external actions taken unattended; path
to `run-journal.md`.

## Important

- No PRD / plan / QA / docs artifacts are created or required. Commits happen only at the
  checkpoints (work-list at arm time, phase-end after the runtime gate) per `feature-development`
  `## Checkpoint commits & pushes`; opening the PR remains the user's manual step — dev never
  invokes `pr-create`. Checkpoint commits & pushes are the only direct git mutations this
  orchestrator performs; the branch guard and no-force rules are absolute.
- Every mid-run `AskUserQuestion` is bracketed by a `pause_reason` set/clear — the Stop hook
  depends on it. Stops leave `run_active: true` + `pause_reason` set (resumable); explicit abort =
  `pause_reason: "user-abort"`, `run_active: false`.
- Files this orchestrator writes directly: `<specs.dir>/.active_ticket`,
  `.artel/run/<TICKET_ID>/run-state.json`, `.artel/run/<TICKET_ID>/run-journal.md`,
  `.artel/run/<TICKET_ID>/runtime-observation.md`, the phase-aware `runtime/observation.md`
  surface-skip entry, the step-2.3 work-list tasklist, and the description file during sync.
  Everything else is delegated.
