---
name: feature-development
description: "End-to-end autonomous feature workflow: interview -> PRD -> vision -> plan -> tasks -> ONE approval pause -> autonomous implementation, review, QA, docs"
argument-hint: "[ticket-id] or [ticket-id]-[phase] [description-file] [--mode=yolo|plan-gate|full-gates] [--dry-run]"
model: sonnet
---

Autonomous orchestrator for the full feature pipeline. Contract:
`${CLAUDE_PLUGIN_ROOT}/docs/autonomous-run.md` (read it first). Shared procedures:
`${CLAUDE_PLUGIN_ROOT}/docs/orchestrator-common.md`. Commit/push procedure: `## Checkpoint
commits & pushes` at the bottom of this file (shared with `dev`). Pass `$0` (including phase; on
multi-phase traversal the current `<TICKET_ID>-<N>`) to every sub-skill. Skip any gate whose
artifact already exists (resume); re-read artifacts after every sub-skill/agent return. (gate 3.5
has no artifact — it re-runs after any write to the plan file, including planner regeneration and
the §3 fold-back, and unconditionally on resume; a green run is recorded by resetting the bounce
line to `**Plan-check bounces:** 0`).

`--step` flag: run in legacy step-by-step mode — confirm between major phases via
`AskUserQuestion`, skip all `run-state.json` writes (autonomous-run.md §6). The remainder of this
file describes the default autonomous mode. `--mode=full-gates` is an alias for `--step`.
`--mode=yolo|plan-gate` selects the autonomous mode per `autonomous-run.md` §10 (default:
`plan-gate`); the classifier may raise it, never lower it. `--dry-run`: execute the chatty head
(steps 1–2) and the step-3 presentation, print the resolved mode + reasons and the intended
external actions, then stop — write no `run-state.json`, never arm.

## Workflow

### 0. Config gate

Read `.artel/config.json` per `${CLAUDE_PLUGIN_ROOT}/docs/config.md`. Missing → `Skill: setup`
(the one-time init interview), then continue with the written config. Present → run the
start-time check config.md's "When the adapter is unusable" prescribes for entry points:
`vcs.adapter: "bitbucket-mcp"` with an empty `vcs.mcpToolPrefix` is a configuration error —
report it and stop before the pipeline starts rather than failing hours later at the PR stage.

### 1. Set active ticket

Write `$0` to `<specs.dir>/.active_ticket`; ensure `<specs.dir>/<TICKET_ID>/` exists. The pointer
is kept phase-accurate for the whole run: on multi-phase traversal (step 5) it is rewritten to
`<TICKET_ID>-<N>` at the start of each phase and advanced to the next incomplete phase after each
phase checkpoint.

### 2. Chatty head — collect everything upfront

| # | Gate | Action (skip if artifact exists) |
|---|------|----------------------------------|
| 0 | `IDEA_READY` — `idea.md` exists | `tracker.adapter` ≠ `"none"` → `Skill: generate-idea` with `$0`. `"none"` (local-only) → rely on the `$1` description file or an existing `idea.md`; if neither exists, the analysis input gate stops and asks. |
| 0.5 | `DESIGN_ANALYZED` — `design-analysis.md` has `Status: DESIGN_ANALYZED`, or `idea.md` has no `figma.com/design` link | `design.figma` disabled (config.md) → skip silently. Enabled → Grep `idea.md` for `figma.com/design`. Link present → `Skill: figma-analysis` with `$0` (chatty head — its Major-findings handshake may ask; on `DESIGN_BLOCKED` — returned by the skill or already recorded in an existing artifact's `Status:` — stop the pipeline and report the parked findings). No link → skip silently. |
| 1 | `PRD_READY` — PRD `Status: PRD_READY` | `Skill: analysis` with `$0 $1` — runs the upfront interview (chatty by design). |
| 2 | `VISION_READY` — `vision.md` `Status: VISION_READY` | `Skill: generate-vision` with `$0` — consumes the PRD; ends with its one wholesale checkpoint. |
| 3 | plan drafted — `plan.md` exists | `Skill: researcher` then `Skill: planner` (both `$0`) — **silent**: their questions land in `.artel/run/<TICKET_ID>/open-questions.md` (autonomous-run.md §3). |
| 3.5 | `PLAN_GROUNDED` — plan-check green | Run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/plan_check.py --plan <plan-path> --strict`, where `<plan-path>` is the phase-aware plan path per ticket-parsing.md §4. The script ships in Phase 5 (see `${CLAUDE_PLUGIN_ROOT}/docs/porting-plan.md`); **when it does not exist yet** (check before running), journal `PLAN_GROUNDED: skipped (plan-check ships in Phase 5)` and proceed — an unshipped tool degrades like an unconfigured gate. When it exists: exit 0 → proceed. Exit 1 → append/update `**Plan-check bounces:** N` at the bottom of `<plan-path>`, and while `N <= MAX_PLAN_CHECK_BOUNCES = 2`: `SendMessage` the `data.unresolved` list to the `planner` agent ("resolve or declare `new:`"), regenerate, re-run the check. Planner regeneration rewrites `<plan-path>` and drops the bounce line with it; after each regeneration re-append `**Plan-check bounces:** N` (N = bounces performed so far) before re-running the check. Third failure → stop and ask (chatty head — plain `AskUserQuestion`, no `pause_reason`) without writing N=3 — the file shows `**Plan-check bounces:** 2` at the stop. Exit 2 → environment error: stop-and-ask pointing at setup, never a bounce. |
| 4 | `TASKLIST_READY` — tasklist `Status: TASKLIST_READY` | `Skill: tasklist` with `$0` — silent, HITL-tagged. |
| 4.5 | phase extraction (phase runs only) | `Skill: sync-phases` with `$0` — creates `phase-<N>/tasks.md` when missing. |

### 3. THE ONE PAUSE — plan+tasklist approval

Present via `AskUserQuestion` in one interaction: plan summary, the task list with its `[HITL: …]`
tags called out, every `Status: open` entry from `.artel/run/<TICKET_ID>/open-questions.md`
(proposed defaults as the first, "(Recommended)" option each), and a note that approval also
authorizes the run's checkpoint commits & pushes to `origin` (planning docs now, one commit+push
per completed phase — see `## Checkpoint commits & pushes`).

- **Approve** → `SendMessage`/re-invoke `planner` and `tasklist` agents to fold any answer that
  overrides a default back into `plan.md` / the tasklist, flip the
  `.artel/run/<TICKET_ID>/open-questions.md` entries to `resolved: "<answer>"`, and remove
  `(provisional — Q<n>)` markers (plan becomes `Status: PLAN_APPROVED`). This write re-arms gate
  3.5 — if the fold-back touched `plan.md`, re-run the plan-check before proceeding. Proceed to
  step 4.
- **Request changes** → route feedback to `planner`/`tasklist`, regenerate, repeat this pause.
- **`yolo` only:** skip the `AskUserQuestion` — treat every
  `.artel/run/<TICKET_ID>/open-questions.md` default as the accepted answer, run the same
  fold-back (planner/tasklist agents, `resolved: "<answer>"` flips, `Status: PLAN_APPROVED`), and
  proceed. The HITL tags remain armed — yolo removes this pause only.

### 4. Arm the run

Run the risk classifier (autonomous-run.md §10) over plan + tasklist. `forced_floor:
"full-gates"` ⇒ stop here: report the matched sensitive categories and instruct the user to
re-run with `--step`. Otherwise write `.artel/run/<TICKET_ID>/run-state.json` per
autonomous-run.md §2: `run_active: true`, `completed: false`, `pause_reason: null`, fresh
`started_at`, zeroed counters, plus the six mode fields (§10), with `gates_confirmed:
["TASKLIST_READY"]`. Announce the effective mode and reasons. On resume, re-derive the mode
fields before re-arming — never trust stale ones. From here the run is silent except deviations,
HITL tasks, and cap escalations. Create `.artel/run/<TICKET_ID>/run-journal.md` with the
run-start entry (autonomous-run.md §11): mode resolution, reasons, HITL tags count.

Then run the **planning checkpoint** (see `## Checkpoint commits & pushes`): commit
`<specs.dir>/<TICKET_ID>/**` + `<specs.dir>/.active_ticket` and push — subject `docs: <TICKET_ID>
planning artifacts` (phase runs: `docs: <TICKET_ID> phase <N> planning artifacts`). Journal it as
an external action. No verify gate here (`verify.commands`) — docs only, no code yet.

### 5. Autonomous tail

**Phase traversal:** explicit `<TICKET_ID>-<N>` in `$0` → run exactly that phase (one pass of
this table). Ticket-wide `$0` with a multi-phase `tasklist.md` (Progress Report table / `##
Iteration N` headers) → loop the remaining incomplete phases in order. Each iteration: write
`<TICKET_ID>-<N>` to `<specs.dir>/.active_ticket`; update `run-state.json`'s `ticket` field and
refresh `started_at` (a phase boundary re-arms the wall-clock budget); delete a stale ticket-wide
`review.md` if present (the previous phase's review survives in that phase's checkpoint commit;
deletion resets the review-round counter per autonomous-run.md §5); run `Skill: sync-phases` with
`<TICKET_ID>-<N>` (gate 4.5 equivalent — extract `phase-<N>/tasks.md` when missing); then gates
5–10.7 passing `<TICKET_ID>-<N>` to every sub-skill. Single-phase tasklist → one ticket-wide pass
ending at gate 10.7.

| # | Gate | Action |
|---|------|--------|
| 5 | `IMPLEMENT_STEP_OK` — every task `- [x]` | Loop `Skill: implementer` with `$0`. Returns: **completion** → aggregate its `Deviations:`/`Verify iterations:` lines, continue. **`HITL: <reason>`** → set `pause_reason: "hitl-task"`, ask the pre-declared question via `AskUserQuestion`, clear `pause_reason`, `SendMessage` the answer, continue. **`DEVIATION` escalation** → the skill handles the handshake; wrap it: set `pause_reason: "deviation-escalation"` before its `AskUserQuestion`, clear after. **Aborted task** → set `pause_reason: "cap-escalation"`, stop and report that the plan needs revision. |
| 6 | `INDEX_UPDATED` | Optional host index-refresh hook (`${CLAUDE_PLUGIN_ROOT}/docs/orchestrator-common.md` §1): run it when the host has wired one up; silently absent otherwise. |
| 7 | `REVIEW_OK` | `Skill: run-reviewer` with `$0`. Blocking/Important findings → `Skill: implementer` (fix tasks from `## Code Review Fixes`) → re-review. Cap: `review.md` `**Review round:**` reaching `MAX_REVIEW_ROUNDS = 3` → `pause_reason: "cap-escalation"`, consolidated findings via `AskUserQuestion`, stop. On user guidance: delete `review.md` (counter reset) and resume. |
| 8 | `RUNTIME_OK` | Surface check: with `runtime.surface` set (config.md), collect the run's changed files (diff vs the default branch plus the working tree) and match them against the globs; no match → write `RUNTIME_OK: skipped (no runtime surface)` to the phase-aware `runtime/observation.md` and move on. No `runtime.run` configured → the gate records `skipped (not configured)` (run-app reports this itself). Otherwise `Skill: run-app` with `--gate`. RED caused by a **runtime error in app code** (runtime errors / ERROR logs / a broken UI tree) → append the quoted error as a `- [ ]` task under `## Runtime Fixes` in the phase-aware tasklist (mirroring `## Code Review Fixes`); when the RED stems from incomplete cross-phase wiring (this phase's code invokes pieces a later phase will build), word the fix task to create the **minimal stubs** that restore launch — no-op implementations / placeholder surfaces with a `TODO: phase <M>` marker — rather than real implementations; stubbing is the expected resolution at a phase boundary and is recorded in the completion's `Deviations:` line. Run `Skill: implementer` once (`MAX_RUNTIME_RETRIES = 1`; counter in `.artel/run/<TICKET_ID>/runtime-observation.md`, autonomous-run.md §5) — it picks the fix task up as the first incomplete task — then re-run the gate; second RED → cap escalation. RED from an **environment failure** (a launch/setup failure of `runtime.run` itself, not app code — run-app stops-and-asks for these) → cap escalation immediately, no implementer round. Do not trust a stale green — re-run unless the observation postdates the last change to files matching `runtime.surface` (or the last code change, when it is unset). |
| 9 | `RELEASE_READY` | `Skill: qa` with `$0` — generate, don't pause. Negative verdict → one implementer fix round (`MAX_QA_ROUNDS = 1`) → re-run qa; second negative → cap escalation. |
| 10 | `DOCS_UPDATED` | `Skill: docs-update` with `$0`. |
| 10.5 | phase write-back (phase runs only) | `Skill: sync-phases` with `$0` — sync completion into `tasklist.md`. |
| 10.7 | `PHASE_CHECKPOINT` | Run the phase-end checkpoint (see `## Checkpoint commits & pushes`): the `verify.commands` gate → capped `## Verify Fixes` implementer rounds → explicit staging → commit (`feat\|fix\|refactor: <TICKET_ID> phase <N> - <phase title>`; no phase → `<ticket summary>`) → push → journal. Then advance `.active_ticket` to the next incomplete phase; on a multi-phase run loop back to the traversal (next phase), else proceed to step 6. |

**Journal (§11):** append an entry to `run-journal.md` at every gate completion, pause/resume,
and external action.
**Budget:** before dispatching any fix-list implementer round (review gate 7, runtime gate 8, QA
gate 9), increment `counters.correction_rounds`; at `MAX_TOTAL_CORRECTION_ROUNDS = 8` → journal
the breach, `pause_reason: "cap-escalation"`, consolidated report via `AskUserQuestion`, stop.

### 6. Completion gate

Runs once, after the last phase on multi-phase runs. `Skill: validate` with `$0`. Any gate red →
treat as a defect: route back to the owning step (max one loop per gate, then cap escalation).
Never set `completed` while a gate is red. All gates green → proceed to step 7.

### 7. PR description + close the run

`Skill: pr-description` with `$0` — writes `<specs.dir>/<TICKET_ID>/pr-description.md` covering
the whole branch (see `${CLAUDE_PLUGIN_ROOT}/skills/pr-description/SKILL.md`). **Always
regenerate:** an existing file is overwritten, never skipped — the branch diff is the input, so
skip-if-exists (autonomous-run.md §9) does not apply. The skill is prompt-free; tracker/VCS fetch
failures degrade to its fallback template and are never a gate failure — do not loop or escalate
on them.

**PR gate:** in `plan-gate`, pause first — set `pause_reason: "hitl-task"`, `AskUserQuestion`
("Open the PR now? commit+push + PR via `vcs.adapter` + tracker comment via `tracker.adapter`" /
"Skip — I'll do it manually"), clear `pause_reason`. In `yolo`, proceed without pausing (roadmap:
the opened PR is yolo's human checkpoint). On approve/yolo: `Skill: pr-create` with `$0`. Its
degradation stop (pr-pending.md) or a decline is **not** a gate failure — record the choice,
continue to close-out; never loop.

Then update `run-state.json`: `completed: true`, `run_active: false`. Append the completion entry
to the journal.

### 8. Description-file sync

Per `${CLAUDE_PLUGIN_ROOT}/docs/orchestrator-common.md` §4 (only when `$1` is a description file
with checkbox tasks).

### 9. Final report

Ticket; phases traversed; gates passed; aggregated `Deviations:` line (`none` when clean); loop
counters (verify iterations total, review rounds, escalation count); QA verdict; runtime status;
checkpoint commits (hash + subject each, incl. push results); path to `pr-description.md`; PR
status (`PR_OPENED`/`PR_EXISTS` URL, `skipped-manual`, or `pending`); description-sync status;
reminder that opening the PR remains manual (only when the PR gate was skipped — the work itself
is already committed and pushed by the checkpoints); effective mode + why (`mode_reasons`);
external actions taken unattended; path to `run-journal.md`.

## Important

- Execute gates sequentially — each depends on the previous.
- Every `AskUserQuestion` after step 3 MUST be bracketed by a `pause_reason` set/clear
  (autonomous-run.md §2) — the Stop hook depends on it.
- On any stop (cap escalation, abort): leave `run_active: true` with the `pause_reason` set —
  resuming the skill continues the run; an explicit user abort sets `pause_reason: "user-abort"`,
  `run_active: false`.
- The only files this orchestrator writes directly: `<specs.dir>/.active_ticket`,
  `.artel/run/<TICKET_ID>/run-state.json`, `.artel/run/<TICKET_ID>/run-journal.md`,
  `.artel/run/<TICKET_ID>/runtime-observation.md`, the `open-questions.md` status flips (same
  directory), and the description file during sync. Everything else is delegated.
- Checkpoint commits & pushes are the only direct git mutations this orchestrator performs; every
  other external action goes through `pr-create`. The checkpoint branch guard and no-force rules
  are absolute.

## Checkpoint commits & pushes

Shared procedure (referenced by `dev` too). Checkpoints keep the branch on `origin` carrying the
latest approved docs and every completed phase. They are orchestrator-owned Bash actions
(`git add` / `git commit` / `git push -u origin`), authorized in advance at the approval pause —
they never pause, and each one is journaled as an external action (autonomous-run.md §11).

| Checkpoint | When | Contents | Subject |
|---|---|---|---|
| Planning (step 4; `dev` step 3) | immediately after arming | `<specs.dir>/<TICKET_ID>/**` + `.active_ticket` | `docs: <TICKET_ID> planning artifacts` (phase runs: `… phase <N> planning artifacts`; `dev`: `… work list`) |
| Phase-end (gate 10.7; `dev` step 7.5) | after the phase's gates pass | the phase's code changes + updated ticket artifacts | `feat\|fix\|refactor: <TICKET_ID> phase <N> - <phase title>` (no phase → `<ticket summary>`) |

Procedure:

1. **Branch guard.** `git branch --show-current` — on the default branch (`git symbolic-ref
   refs/remotes/origin/HEAD`, fallback `main`) → stop-and-ask (environment error): checkpoints
   never commit to the default branch. Never any `--force` variant anywhere in this procedure.
2. **Idempotence.** `git status --porcelain` clean → skip the commit (resume-safe); still push
   when the local branch is ahead of `origin`.
3. **Quality gate (phase-end only).** Run `verify.commands` in order (config.md), stopping at the
   first failure; an empty list ⇒ record the verify step as `skipped` in the journal entry and
   continue to staging. Findings → append them as `- [ ]` tasks under `## Verify Fixes` in the
   phase-aware tasklist and loop `Skill: implementer` (`MAX_CHECKPOINT_VERIFY_ROUNDS = 2`; each
   round increments `counters.correction_rounds` per autonomous-run.md §5); still red after the
   cap → cap escalation. A non-zero exit that reports no actionable findings (a toolchain/version
   quirk) is an **environment error** — stop-and-ask, never a fix round.
4. **Stage explicitly.** The ticket's changed files, `<specs.dir>/<TICKET_ID>/**`, and
   `<specs.dir>/.active_ticket`. Never `git add -A`; never generated files; never `.artel/**`.
5. **Commit.** Conventional message, always English, subject line only, no trailers.
6. **Push.** `git push -u origin <branch>`. Rejected non-fast-forward → stop-and-ask (the user
   reconciles; force-push is never an option).
7. **Journal.** Checkpoint entry: commit hash, subject, push result, verify fix rounds.

`pr-create` remains the PR-opening close-out; after phase checkpoints its own commit step usually
finds a clean tree and skips — it is idempotent by design.
