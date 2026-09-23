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

### 1.5 Spec store

Resolve where this ticket's spec trail lives (`${CLAUDE_PLUGIN_ROOT}/docs/spec-storage.md` §2)
before any spec document is read:

1. **Your tool list** (§2.1 rows 5–6): with `knowledge.adapter` `kartoteka`, kartoteka's
   artifact tools — `artifact_get`, `artifact_put`, `artifact_patch`, `artifact_list`,
   `artifact_versions` — must be in this session. Missing → unavailable with that row's record:
   go to 3.
2. Run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/spec_store.py decide <TICKET_ID> --decided-by dev`.
3. **Exit 5** (unavailable) → §5.1: ask Retry / Work locally for this run / Abort — headless,
   `specs.onUnavailable` answers (§5.4). Work locally →
   `spec_store.py decide <TICKET_ID> --decided-by dev --files "kartoteka unavailable; working locally at the user's request — <record>"`.
   While working locally, a gate that would *create* a document the decision's `versions` names
   stops instead of regenerating it: `<name> exists in kartoteka (v<N>) but kartoteka is
   unreachable; retry when it is back` (§5.1).
4. **`pending` non-empty** — a resume after an outage → `Skill: migrate-specs` with
   `<TICKET_ID> --pending-only` (plus `--no-prompt` headless) before anything else (§5.3).
   Continue only when its `pending_left` for the ticket is 0; otherwise report what is left (a
   conflict needs the user) and pause with `pause_reason: "store-unavailable"` — headless: journal and stop.
   A migrate exit 5 is the same outage as §5.2.
5. **`local_trail` non-empty** → §7: ask Move them into kartoteka (recommended —
   `Skill: migrate-specs` with `<TICKET_ID>`, then run step 2 again) / Work locally for this run
   (`decide … --files "the user kept the local trail for this run"`) / Abort. Headless: stop and
   name `/artel:migrate-specs <TICKET_ID>`.
6. Journal the decision in the run-start entry. Pass nothing on: every sub-skill reads the
   decision file itself (§2.2), and every agent dispatch carries **Spec store:** (§2.3).

Renew the decision wherever `started_at` is refreshed — on resume and at every phase boundary —
so it stays fresh for sub-skills: a kartoteka decision by running step 2 again; a files decision
with `decide … --files "<its reason>"`, never a plain `decide`, which would re-probe
and switch a run working locally to kartoteka mid-run (spec-storage.md §2.2). On resume of a run that worked locally (§5.1) while the
store is back, ask once (§5.3): Move this run's documents into kartoteka and continue there
(recommended; `Skill: migrate-specs` with `<TICKET_ID>`) / Keep working locally. Headless keeps
working locally.

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

On the kartoteka path these existence checks are one `spec_store.py list <TICKET_ID>`, and branch 3's work list is written with `artifact_put(project=<project>, …, expected_version=0)` (spec-storage.md §4.1).

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
an external action. No verify gate here (`verify.commands`) — docs only, no code yet. On the
kartoteka path, when, images aside, only `.active_ticket` changed, skip the commit and journal `work-list checkpoint: skipped — the spec trail is in kartoteka`.

On the kartoteka path this checkpoint sweeps images first and never stages one — the procedure's
steps 2 and 4, spelled out here because this checkpoint names its own paths
(`${CLAUDE_PLUGIN_ROOT}/docs/spec-storage.md` §4.6). Sweep:
`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/spec_store.py image sync <TICKET_ID> --author artel:dev`.
Exit `5`, or `failed` entries, never pause: journal
`image-sync: <n> left local — <first error line>` (spec-storage.md §5.6) and go on.
Exit `2` with kind `unrecoverable` never pauses either, but journal its whole message, not a
first line, and repeat it in the final report (spec-storage.md §5.6). Any other non-zero exit:
as exit `5` — never a `STORE_UNAVAILABLE` pause. Then stage
with one exclude per image extension, leaving `'<specs.dir>/<TICKET_ID>'` out when that folder
neither exists nor has tracked files:

    git add -- '<specs.dir>/<TICKET_ID>' '<specs.dir>/.active_ticket' ':(exclude,icase,glob)<specs.dir>/<TICKET_ID>/**/*.png' ':(exclude,icase,glob)<specs.dir>/<TICKET_ID>/**/*.jpg' ':(exclude,icase,glob)<specs.dir>/<TICKET_ID>/**/*.jpeg' ':(exclude,icase,glob)<specs.dir>/<TICKET_ID>/**/*.gif' ':(exclude,icase,glob)<specs.dir>/<TICKET_ID>/**/*.webp'

### 4. Implement (autonomous)

**Re-mirror first.** Per `${CLAUDE_PLUGIN_ROOT}/docs/task-queue.md` §1 rows 2-4
(this skill carries no local-only flag, so row 1 cannot apply — do not add one here)
and §2, on the queue path run. Files path first, kartoteka path (`docs/spec-storage.md` §4.2) second:

    python3 ${CLAUDE_PLUGIN_ROOT}/scripts/tasklist_tasks.py --tasklist <specs.dir>/<TICKET_ID>/tasklist.md --ticket-key <TICKET_ID>
    set -o pipefail; python3 ${CLAUDE_PLUGIN_ROOT}/scripts/spec_store.py get <specs.dir>/<TICKET_ID>/tasklist.md | python3 ${CLAUDE_PLUGIN_ROOT}/scripts/tasklist_tasks.py --tasklist - --ticket-key <TICKET_ID>

and `task_create` the rows it emits — `data.iterations`, then `data.sections` (§2 steps
2–4, surfacing every `data.warnings` line). The step is create-only and idempotent, so
this repairs a tasklist that was hand-edited or generated before the queue was
reachable, and never resets a `done` row or undoes a promotion. Exit `2` → report
and continue on the fallback path. On phase-scoped runs this lands after
`sync-phases`, which is what keeps `tasklist.md` current when it is read.

**Phase traversal:** explicit `<TICKET_ID>-<N>` in `$0` → run exactly that phase (one pass of
steps 4–7.5). Ticket-wide `$0` with a multi-phase `tasklist.md` (Progress Report table / `##
Iteration N` headers) → loop the remaining incomplete phases in order; each iteration: write
`<TICKET_ID>-<N>` to `<specs.dir>/.active_ticket`; update `run-state.json`'s `ticket` field and
refresh `started_at` (a phase boundary re-arms the wall-clock budget) and renew the decision as step 1.5 says;
reset the review round (delete `review.md`, or on the kartoteka path store the round-0 version —
`${CLAUDE_PLUGIN_ROOT}/docs/spec-storage.md` §4.4) when a stale ticket-wide one is present (the
previous phase's review survives in that phase's checkpoint commit, or as an earlier version); run `Skill: sync-phases` with `<TICKET_ID>-<N>`
(extract `phase-<N>/tasks.md` when missing); then run steps 4–7.5 passing `<TICKET_ID>-<N>` to
every sub-skill. Single-phase work → one pass ending at step 7.5.

Loop `Skill: implementer` with `$0` (or `$0 $1` when driving from a description file) until every
task is `- [x]`. Handle returns exactly as `feature-development` step 5 does: completions →
aggregate `Deviations:` / `Verify iterations:` and journal the `Report:` path (never open the
report — autonomous-run.md §1, "Bulk stays in files"); `HITL:` → `pause_reason: "hitl-task"`,
ask, clear, resume; `DEVIATION` escalation → bracket with `pause_reason:
"deviation-escalation"`; aborted task → `pause_reason: "cap-escalation"`, stop and report that
the plan needs revision.

**Per-task review** (`review.perTask: true` in config.md; off by default): wrap every
iteration-task dispatch in autonomous-run.md §16 — `scripts/review_package.py snapshot` before
the implementer, `diff` + `Skill: run-reviewer --task …` after its completion, at most one
`## Code Review Fixes` implementer round (`MAX_TASK_REVIEW_ROUNDS = 1`, counted toward
`counters.correction_rounds`), one `task review` journal entry. Fix-list dispatches are never
gated this way.

### 5. Refresh the code index (optional host hook)

Optional host index-refresh hook (`${CLAUDE_PLUGIN_ROOT}/docs/orchestrator-common.md` §1): run it
when the host has wired one up; silently absent otherwise.

### 6. Review (capped loop)

`Skill: run-reviewer` with `$0`. Blocking/Important findings → `Skill: implementer` on the
`## Code Review Fixes` tasks → re-review. `review.md` `**Review round:**` at
`MAX_REVIEW_ROUNDS = 3` → cap escalation (consolidated findings via `AskUserQuestion`; on guidance
reset the review round (delete `review.md`, or on the kartoteka path store the round-0 version —
`${CLAUDE_PLUGIN_ROOT}/docs/spec-storage.md` §4.4) and resume).

### 7. Runtime gate (RUNTIME_OK)

Surface check: with `runtime.surface` set (config.md), collect the run's changed files (diff vs
the default branch plus the working tree) and match them against the globs; no match → write
`RUNTIME_OK: skipped (no runtime surface)` to the phase-aware `runtime/observation.md` and move
on. No `runtime.run` configured → the gate records `skipped (not configured)` (run-app reports
this itself). Otherwise `Skill: run-app` with `--gate`. RED caused by a **runtime error in app
code** (runtime errors / ERROR logs / a broken UI tree) → append a `- [ ]` task under
`## Runtime Fixes` in the phase-aware tasklist (mirroring `## Code Review Fixes`) (kartoteka path: one `artifact_patch(project=<project>, …)` — spec-storage.md §4.3), beneath a
new `### runtime-r<n>` source heading (`### runtime-p<N>-r<n>` on a phase-scoped run, n the
retry this round is — `${CLAUDE_PLUGIN_ROOT}/docs/task-queue.md` §6), its line a
one-line summary of the error with the quoted error nested under it as an indented block
(nested lines go to the row's description; the checkbox line is the row's title, capped
at 500 characters), and on the queue path (§1 rows 2-4) record it before the implementer
round: run
`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/tasklist_tasks.py --tasklist <the phase-aware tasklist> --ticket-key <TICKET_ID>` (kartoteka path: `set -o pipefail; python3 ${CLAUDE_PLUGIN_ROOT}/scripts/spec_store.py get <the phase-aware tasklist> | python3 ${CLAUDE_PLUGIN_ROOT}/scripts/tasklist_tasks.py --tasklist - --ticket-key <TICKET_ID>`)
— `<TICKET_ID>` the canonical key, without the phase suffix — and `task_create` its
`data.sections` (§2's fix-writer rule);
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
per `feature-development` `## Checkpoint commits & pushes`: the image sweep (kartoteka path) → the
`verify.commands` gate → capped `## Verify Fixes` implementer rounds
(`MAX_CHECKPOINT_VERIFY_ROUNDS = 2`, counted toward `counters.correction_rounds`) → explicit
staging (no trail image on the kartoteka path) → commit (`feat|fix|refactor: <TICKET_ID> phase
<N> - <phase title>`; no phase → `<work summary>`) → `git push -u origin <branch>` → journal. Then
advance `.active_ticket` to the next incomplete phase and loop back to step 4 while incomplete
phases remain.

### 8. Complete

All tasks `- [x]` across all traversed phases, review clean (no unresolved Blocking/Important),
runtime green or skipped, final phase checkpoint pushed → update `run-state.json`:
`completed: true`, `run_active: false`. Append the completion entry to the journal. Then
description-file sync per `orchestrator-common.md` (when `$1` has checkbox tasks).

### 9. Report

On the kartoteka path, sweep once more before writing it:
`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/spec_store.py image sync <TICKET_ID> --author artel:dev`
(`${CLAUDE_PLUGIN_ROOT}/docs/spec-storage.md` §5.6). A failure never pauses: the report lists what
it left local instead.

Ticket; phases traversed; tasks completed; aggregated `Deviations:` line (`none` when clean);
verify-iteration total and review rounds; runtime gate status (green / red / skipped); checkpoint
commits (hash + subject each, incl. push results); description-sync status; reminder that opening
the PR remains manual (dev has no PR gate — the work itself is already committed and pushed by the
checkpoints); effective mode + why (`mode_reasons`); external actions taken unattended; spec store (`kartoteka`, or `files (<reason>)` with the documents left on disk and `/artel:migrate-specs <TICKET_ID>` — spec-storage.md §5.5); images left local on the kartoteka path — each `failed` or `skipped` entry of that sweep with its reason, or on exit `5` or `2` every image still under the trail; an `unrecoverable` sweep's whole message — with the `image sync` command above to move them in (headless runs journal the same lines); path
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
- **`STORE_UNAVAILABLE`** from a sub-skill or agent, or a failing store call of your own, is the
  environment error of `${CLAUDE_PLUGIN_ROOT}/docs/spec-storage.md` §5.2: set
  `pause_reason: "store-unavailable"` and ask Retry (resume the agent) / Save it locally and pause
  (only when a produced document is unsaved: first
  `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/spec_store.py pending add <path> --base-version <N>`,
  then `SendMessage` the agent to write it to its logical path) / Pause without saving. Clear
  `pause_reason` only after a Retry succeeds. There is no "continue locally" mid-run. Headless:
  `specs.onUnavailable` (§5.4).
