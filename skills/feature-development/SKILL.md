---
name: feature-development
description: "End-to-end autonomous feature workflow: interview -> PRD -> vision -> plan -> tasks -> ONE approval pause -> autonomous implementation, review, QA, docs"
argument-hint: "[ticket-id] or [ticket-id]-[phase] [description-file] [--mode=yolo|plan-gate|full-gates] [--dry-run] [--local]"
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
`--local`: skip the institutional-knowledge
consultation and the task queue for this whole run and pass the flag down to every sub-skill
that accepts it (`analysis`, `researcher`, `tasklist`, `run-reviewer` and `implementer`).
Every implementer dispatch carries it, fix rounds included (gates 7, 8, 9 and 10.7, and the
per-task review's round): its **Task queue:** field is the only way the opt-out reaches the
agent (`${CLAUDE_PLUGIN_ROOT}/docs/task-queue.md` §1). Default is to consult;
`${CLAUDE_PLUGIN_ROOT}/docs/knowledge-consultation.md` §1 resolves it against
`knowledge.adapter` and tool availability.

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

### 1.5 Spec store

Resolve where this ticket's spec trail lives (`${CLAUDE_PLUGIN_ROOT}/docs/spec-storage.md` §2)
before any spec document is read:

1. **Your tool list** (§2.1 rows 5–6): with `knowledge.adapter` `kartoteka`, kartoteka's
   artifact tools — `artifact_get`, `artifact_put`, `artifact_patch`, `artifact_list`,
   `artifact_versions` — must be in this session. Missing → unavailable with that row's record:
   go to 3.
2. Run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/spec_store.py decide <TICKET_ID> --decided-by feature-development` (plus `--local` when this run was invoked with it).
3. **Exit 5** (unavailable) → §5.1: ask Retry / Work locally for this run / Abort — headless,
   `specs.onUnavailable` answers (§5.4). Work locally →
   `spec_store.py decide <TICKET_ID> --decided-by feature-development --files "kartoteka unavailable; working locally at the user's request — <record>"`.
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
with `decide … --files "<its reason>"` (or `--local`), never a plain `decide`, which would re-probe
and switch a run working locally to kartoteka mid-run (spec-storage.md §2.2). On resume of a run that worked locally (§5.1) while the
store is back, ask once (§5.3): Move this run's documents into kartoteka and continue there
(recommended; `Skill: migrate-specs` with `<TICKET_ID>`) / Keep working locally. Headless keeps
working locally.

### 2. Chatty head — collect everything upfront

On the kartoteka path "artifact exists" in every gate below is one `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/spec_store.py list <TICKET_ID>`, re-run after each sub-skill returns, and a gate's `Status:` line is read without the document entering this context: `doc=$(spec_store.py get <path>) && printf '%s\n' "$doc" | grep -m1 'Status:'`.

| # | Gate | Action (skip if artifact exists) |
|---|------|----------------------------------|
| 0 | `IDEA_READY` — `idea.md` exists | `Skill: generate-idea` with `$0 $1`, under **every** adapter — it branches on `tracker.adapter` itself (config.md): a tracker imports the ticket; `"none"` (local-only) seeds `idea.md` from the `$1` description file, or runs the same input gate `analysis` does when `$1` is absent. Gate 2 hard-requires `idea.md`, so the pipeline seeds it here rather than letting a `"none"` run die at the vision gate. |
| 0.5 | `DESIGN_ANALYZED` — `design-analysis.md` has `Status: DESIGN_ANALYZED`, or `idea.md` has no `figma.com/design` link | `design.figma` disabled (config.md) → skip silently. Enabled → Grep `idea.md` for `figma.com/design` (kartoteka path: `doc=$(spec_store.py get <specs.dir>/<TICKET_ID>/idea.md) && printf '%s\n' "$doc" | grep -q figma.com/design`). Link present → `Skill: figma-analysis` with `$0` (chatty head — its Major-findings handshake may ask; on `DESIGN_BLOCKED` — returned by the skill or already recorded in an existing artifact's `Status:` — stop the pipeline and report the parked findings). No link → skip silently. |
| 1 | `PRD_READY` — PRD `Status: PRD_READY` | `Skill: analysis` with `$0 $1`, plus `--local` when this run was invoked with it — runs the upfront interview (chatty by design). |
| 2 | `VISION_READY` — `vision.md` `Status: VISION_READY` | `Skill: generate-vision` with `$0` — consumes the PRD; ends with its one wholesale checkpoint. |
| 3 | plan drafted — `plan.md` exists | `Skill: researcher` then `Skill: planner` (both `$0`; `researcher` also takes `--local` when this run was invoked with it) — **silent**: their questions land in `.artel/run/<TICKET_ID>/open-questions.md` (autonomous-run.md §3). |
| 3.5 | `PLAN_GROUNDED` — plan-check green | Run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/plan_check.py --plan <plan-path> --strict` (kartoteka path: `set -o pipefail; python3 ${CLAUDE_PLUGIN_ROOT}/scripts/spec_store.py get <plan-path> | python3 ${CLAUDE_PLUGIN_ROOT}/scripts/plan_check.py --plan - --strict`), where `<plan-path>` is the phase-aware plan path per ticket-parsing.md §4. Exit 0 → proceed. Exit 1 → append/update `**Plan-check bounces:** N` at the bottom of `<plan-path>` (kartoteka path: `artifact_patch(project=<project>, …)` replacing the existing bounce line, or `append` it), and while `N <= MAX_PLAN_CHECK_BOUNCES = 2`: `SendMessage` the `data.unresolved` list to the `planner` agent ("resolve or declare `new:`"), regenerate, re-run the check. Planner regeneration rewrites `<plan-path>` and drops the bounce line with it; after each regeneration re-append `**Plan-check bounces:** N` (N = bounces performed so far) before re-running the check. Third failure → stop and ask (chatty head — plain `AskUserQuestion`, no `pause_reason`) without writing N=3 — the file shows `**Plan-check bounces:** 2` at the stop. Exit 2 → environment error: stop-and-ask pointing at setup, never a bounce. |
| 4 | `TASKLIST_READY` — tasklist `Status: TASKLIST_READY` | `Skill: tasklist` with `$0`, plus `--local` when this run was invoked with it — silent, HITL-tagged; the flag keeps its task-queue mirror from writing rows. |
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
`started_at`, zeroed counters, `requested_local` (§2 — the `--local` opt-out, recorded because
a resumed run has no argument list left to read it from), plus the six mode fields (§10), with
`gates_confirmed: ["TASKLIST_READY"]`. Announce the effective mode and reasons. On resume,
re-derive the mode fields before re-arming — never trust stale ones, and carry `requested_local`
forward unchanged: it is the user's opt-out, not a classifier output. From here the run is
silent except deviations, HITL tasks, and cap escalations. Create
`.artel/run/<TICKET_ID>/run-journal.md` with the run-start entry (autonomous-run.md §11): mode
resolution, reasons, HITL tags count.

Then run the **planning checkpoint** (see `## Checkpoint commits & pushes`): commit
`<specs.dir>/<TICKET_ID>/**` + `<specs.dir>/.active_ticket` and push — subject `docs: <TICKET_ID>
planning artifacts` (phase runs: `docs: <TICKET_ID> phase <N> planning artifacts`). Journal it as
an external action. No verify gate here (`verify.commands`) — docs only, no code yet. On the
kartoteka path the procedure sweeps images first and never stages one (its steps 2 and 4); when,
images aside, only `.active_ticket` changed, skip the commit and journal `planning checkpoint: skipped — the spec trail is in kartoteka`.

### 5. Autonomous tail

**Phase traversal:** explicit `<TICKET_ID>-<N>` in `$0` → run exactly that phase (one pass of
this table). Ticket-wide `$0` with a multi-phase `tasklist.md` (Progress Report table / `##
Iteration N` headers) → loop the remaining incomplete phases in order. Each iteration: write
`<TICKET_ID>-<N>` to `<specs.dir>/.active_ticket`; update `run-state.json`'s `ticket` field and
refresh `started_at` (a phase boundary re-arms the wall-clock budget); reset the review round — delete a stale
ticket-wide `review.md` on the files path (it survives in that phase's checkpoint commit), or store
the round-0 version on the kartoteka path (`${CLAUDE_PLUGIN_ROOT}/docs/spec-storage.md` §4.4; it
survives as an earlier version) — which resets the counter per autonomous-run.md §5; then renew the decision as step 1.5 says; run `Skill: sync-phases` with
`<TICKET_ID>-<N>` (gate 4.5 equivalent — extract `phase-<N>/tasks.md` when missing); then gates
5–10.7 passing `<TICKET_ID>-<N>` to every sub-skill. Single-phase tasklist → one ticket-wide pass
ending at gate 10.7.

**Re-mirror first.** Per `${CLAUDE_PLUGIN_ROOT}/docs/task-queue.md` §1 and §2, on the
queue path — and only there, so a `--local` run skips it — run this before the first
gate-5 dispatch of each phase. Files path first, kartoteka path (`docs/spec-storage.md` §4.2) second:

    python3 ${CLAUDE_PLUGIN_ROOT}/scripts/tasklist_tasks.py --tasklist <specs.dir>/<TICKET_ID>/tasklist.md --ticket-key <TICKET_ID>
    set -o pipefail; python3 ${CLAUDE_PLUGIN_ROOT}/scripts/spec_store.py get <specs.dir>/<TICKET_ID>/tasklist.md | python3 ${CLAUDE_PLUGIN_ROOT}/scripts/tasklist_tasks.py --tasklist - --ticket-key <TICKET_ID>

and `task_create` the rows it emits — `data.iterations`, then `data.sections` (§2 steps
2–4, surfacing every `data.warnings` line). Gate 4 invokes `Skill: tasklist` only when the
tasklist is not already `TASKLIST_READY`, so without this step a resumed run — or a
ticket whose tasklist was written before the adapter was reachable — never mirrors at
all, and every implementer dispatch falls back to the file. The step is create-only and
idempotent: it never resets a `done` row and never undoes a promotion. Exit `2` → report
`error.kind` and `error.message` and continue on the fallback path. On phase runs it
lands after this section's `Skill: sync-phases`, which is what keeps `tasklist.md`
current when it is read.

| # | Gate | Action |
|---|------|--------|
| 5 | `IMPLEMENT_STEP_OK` — every task `- [x]` | Loop `Skill: implementer` with `$0`, plus `--local` when this run was invoked with it — it becomes the dispatch's **Task queue:** field, which is what carries the opt-out to the agent (`${CLAUDE_PLUGIN_ROOT}/docs/task-queue.md` §1). Returns: **completion** → aggregate its `Deviations:`/`Verify iterations:` lines and journal its `Report:` path (never open the report — autonomous-run.md §1, "Bulk stays in files"), continue. **Per-task review** (`review.perTask: true`, config.md; off by default) → wrap every iteration-task dispatch in autonomous-run.md §16: `scripts/review_package.py snapshot` before the implementer, `diff` + `Skill: run-reviewer --task …` (plus `--local` when this run was invoked with it) after its completion, at most one `## Code Review Fixes` implementer round (`MAX_TASK_REVIEW_ROUNDS = 1`, counted toward `counters.correction_rounds`), one `task review` journal entry; fix-list dispatches are never gated this way. **`HITL: <reason>`** → set `pause_reason: "hitl-task"`, ask the pre-declared question via `AskUserQuestion`, clear `pause_reason`, `SendMessage` the answer, continue. **`DEVIATION` escalation** → the skill handles the handshake; wrap it: set `pause_reason: "deviation-escalation"` before its `AskUserQuestion`, clear after. **Aborted task** → set `pause_reason: "cap-escalation"`, stop and report that the plan needs revision. |
| 6 | `INDEX_UPDATED` | Optional host index-refresh hook (`${CLAUDE_PLUGIN_ROOT}/docs/orchestrator-common.md` §1): run it when the host has wired one up; silently absent otherwise. |
| 7 | `REVIEW_OK` | `Skill: run-reviewer` with `$0`, plus `--local` when this run was invoked with it — `run-reviewer` records the fix tasks it appends in the task queue, and the flag keeps a local-only run from writing rows (`${CLAUDE_PLUGIN_ROOT}/docs/task-queue.md` §1, §6). Blocking/Important findings → `Skill: implementer` (fix tasks from `## Code Review Fixes`, plus `--local` when this run was invoked with it) → re-review. Cap: `review.md` `**Review round:**` reaching `MAX_REVIEW_ROUNDS = 3` → `pause_reason: "cap-escalation"`, consolidated findings via `AskUserQuestion`, stop. On user guidance: reset the review round (delete `review.md`, or on the kartoteka path store the round-0 version — spec-storage.md §4.4) and resume. |
| 8 | `RUNTIME_OK` | Surface check: with `runtime.surface` set (config.md), collect the run's changed files (diff vs the default branch plus the working tree) and match them against the globs; no match → write `RUNTIME_OK: skipped (no runtime surface)` to the phase-aware `runtime/observation.md` and move on. No `runtime.run` configured → the gate records `skipped (not configured)` (run-app reports this itself). Otherwise `Skill: run-app` with `--gate`. RED caused by a **runtime error in app code** (runtime errors / ERROR logs / a broken UI tree) → append a `- [ ]` task under `## Runtime Fixes` in the phase-aware tasklist (mirroring `## Code Review Fixes`) (kartoteka path: one `artifact_patch(project=<project>, …)` — spec-storage.md §4.3), beneath a new `### runtime-r<n>` source heading (`### runtime-p<N>-r<n>` on a phase-scoped run, n the retry this round is — `${CLAUDE_PLUGIN_ROOT}/docs/task-queue.md` §6), its line a one-line summary of the error with the quoted error nested under it as an indented block (nested lines go to the row's description; the checkbox line is the row's title, capped at 500 characters), and on the queue path — never on a `--local` run — record it before the implementer round: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/tasklist_tasks.py --tasklist <the phase-aware tasklist> --ticket-key <TICKET_ID>` (kartoteka path: `set -o pipefail; python3 ${CLAUDE_PLUGIN_ROOT}/scripts/spec_store.py get <the phase-aware tasklist> | python3 ${CLAUDE_PLUGIN_ROOT}/scripts/tasklist_tasks.py --tasklist - --ticket-key <TICKET_ID>`) (`<TICKET_ID>` the canonical key, without the phase suffix), then `task_create` its `data.sections` (§2's fix-writer rule); when the RED stems from incomplete cross-phase wiring (this phase's code invokes pieces a later phase will build), word the fix task to create the **minimal stubs** that restore launch — no-op implementations / placeholder surfaces with a `TODO: phase <M>` marker — rather than real implementations; stubbing is the expected resolution at a phase boundary and is recorded in the completion's `Deviations:` line. Run `Skill: implementer` once, plus `--local` when this run was invoked with it (`MAX_RUNTIME_RETRIES = 1`; counter in `.artel/run/<TICKET_ID>/runtime-observation.md`, autonomous-run.md §5) — it picks the fix task up as the first incomplete task — then re-run the gate; second RED → cap escalation. RED from an **environment failure** (a launch/setup failure of `runtime.run` itself, not app code — run-app stops-and-asks for these) → cap escalation immediately, no implementer round. Do not trust a stale green — re-run unless the observation postdates the last change to files matching `runtime.surface` (or the last code change, when it is unset). |
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

On the kartoteka path, sweep once more before writing it:
`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/spec_store.py image sync <TICKET_ID> --author artel:feature-development`
(`${CLAUDE_PLUGIN_ROOT}/docs/spec-storage.md` §5.6). A failure never pauses: the report lists what
it left local instead.

Ticket; phases traversed; gates passed; aggregated `Deviations:` line (`none` when clean); loop
counters (verify iterations total, review rounds, escalation count); QA verdict; runtime status;
checkpoint commits (hash + subject each, incl. push results); path to `pr-description.md`; PR
status (`PR_OPENED`/`PR_EXISTS` URL, `skipped-manual`, or `pending`); description-sync status;
reminder that opening the PR remains manual (only when the PR gate was skipped — the work itself
is already committed and pushed by the checkpoints); effective mode + why (`mode_reasons`);
external actions taken unattended; spec store (`kartoteka`, or `files (<reason>)` with the documents left on disk and `/artel:migrate-specs <TICKET_ID>` — spec-storage.md §5.5); images left local on the kartoteka path — each `failed` or `skipped` entry of that sweep with its reason, or on exit `5` every image still under the trail; an `unrecoverable` sweep's whole message — with the `image sync` command above to move them in (headless runs journal the same lines); path to `run-journal.md`.

## Important

- Execute gates sequentially — each depends on the previous.
- Every `AskUserQuestion` after step 3 MUST be bracketed by a `pause_reason` set/clear
  (autonomous-run.md §2) — the Stop hook depends on it.
- On any stop (cap escalation, abort): leave `run_active: true` with the `pause_reason` set —
  resuming the skill continues the run; an explicit user abort sets `pause_reason: "user-abort"`,
  `run_active: false`.
- The only files this orchestrator writes directly: `<specs.dir>/.active_ticket`,
  `.artel/run/<TICKET_ID>/run-state.json`, `.artel/run/<TICKET_ID>/run-journal.md`,
  `.artel/run/<TICKET_ID>/runtime-observation.md`, the phase-aware `runtime/observation.md`
  surface-skip entry, the `open-questions.md` status flips (same directory), the
  description file during sync, `.artel/run/<TICKET_ID>/spec-store.json` (through
  `spec_store.py`); on the kartoteka path its spec-document writes — the plan-check bounce line,
  runtime and verify fix batches, the review reset — are store writes. Everything else is delegated.
- Checkpoint commits & pushes are the only direct git mutations this orchestrator performs; every
  other external action goes through `pr-create`. The checkpoint branch guard and no-force rules
  are absolute.
- **`STORE_UNAVAILABLE`** from a sub-skill or agent, or a failing store call of your own, is the
  environment error of `${CLAUDE_PLUGIN_ROOT}/docs/spec-storage.md` §5.2: set
  `pause_reason: "store-unavailable"` and ask Retry (resume the agent) / Save it locally and pause
  (only when a produced document is unsaved: first
  `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/spec_store.py pending add <path> --base-version <N>`,
  then `SendMessage` the agent to write it to its logical path) / Pause without saving. Clear
  `pause_reason` only after a Retry succeeds. There is no "continue locally" mid-run. Headless:
  `specs.onUnavailable` (§5.4).

## Checkpoint commits & pushes

Shared procedure (referenced by `dev` too). Checkpoints keep the branch on `origin` carrying the
latest approved docs and every completed phase. They are orchestrator-owned Bash actions
(`git add` / `git commit` / `git push -u origin`), authorized in advance at the approval pause —
they never pause, and each one is journaled as an external action (autonomous-run.md §11).

| Checkpoint | When | Contents | Subject |
|---|---|---|---|
| Planning (step 4; `dev` step 3) | immediately after arming | `<specs.dir>/<TICKET_ID>/**` + `.active_ticket` (kartoteka path: skipped when only `.active_ticket` changed) | `docs: <TICKET_ID> planning artifacts` (phase runs: `… phase <N> planning artifacts`; `dev`: `… work list`) |
| Phase-end (gate 10.7; `dev` step 7.5) | after the phase's gates pass | the phase's code changes + updated ticket artifacts | `feat\|fix\|refactor: <TICKET_ID> phase <N> - <phase title>` (no phase → `<ticket summary>`) |

Procedure:

1. **Branch guard.** `git branch --show-current` — on the default branch (`git symbolic-ref
   refs/remotes/origin/HEAD`, fallback `main`) → stop-and-ask (environment error): checkpoints
   never commit to the default branch. Never any `--force` variant anywhere in this procedure.
2. **Image sweep (kartoteka path), then idempotence.** On the kartoteka path, first run
   `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/spec_store.py image sync <TICKET_ID> --author artel:<skill>`
   — `<skill>` is `feature-development`, or `dev` when `dev` runs this procedure
   (`${CLAUDE_PLUGIN_ROOT}/docs/spec-storage.md` §4.6). It moves every untracked image under the
   trail into kartoteka. Exit `5`, or `failed` entries, never pause: journal
   `image-sync: <n> left local — <first error line>` (spec-storage.md §5.6) and go on; those
   images stay untracked, and the next sweep point retries them. Exit `2` with kind
   `unrecoverable` never pauses either, but journal its whole message, not a first line, and
   repeat it in the final report (spec-storage.md §5.6). Then: `git status --porcelain`
   clean → skip the commit (resume-safe); still push when the local branch is ahead of `origin`.
3. **Quality gate (phase-end only).** Run `verify.commands` in order (config.md), stopping at the
   first failure; an empty list ⇒ record the verify step as `skipped` in the journal entry and
   continue to staging. Findings → append them as `- [ ]` tasks under `## Verify Fixes` in the
   phase-aware tasklist (kartoteka path: one `artifact_patch(project=<project>, …)` — spec-storage.md §4.3), beneath a new `### checkpoint-r<k>` source heading (k the verify
   round; `### checkpoint-p<N>-r<k>` on a phase-scoped run —
   `${CLAUDE_PLUGIN_ROOT}/docs/task-queue.md` §6); on the queue path (§1 — a `--local` run
   skips it) record them —
   `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/tasklist_tasks.py --tasklist <the phase-aware tasklist> --ticket-key <TICKET_ID>` (kartoteka path: `set -o pipefail; python3 ${CLAUDE_PLUGIN_ROOT}/scripts/spec_store.py get <the phase-aware tasklist> | python3 ${CLAUDE_PLUGIN_ROOT}/scripts/tasklist_tasks.py --tasklist - --ticket-key <TICKET_ID>`)
   (`<TICKET_ID>` the canonical key, without the phase suffix), then `task_create` its
   `data.sections` (§2's fix-writer rule) — and loop `Skill: implementer`, plus `--local`
   when the run holds it (a `dev` run never does) (`MAX_CHECKPOINT_VERIFY_ROUNDS = 2`; each
   round increments `counters.correction_rounds` per autonomous-run.md §5); still red after the
   cap → cap escalation. A non-zero exit that reports no actionable findings (a toolchain/version
   quirk) is an **environment error** — stop-and-ask, never a fix round.
4. **Stage explicitly.** The ticket's changed files, `<specs.dir>/<TICKET_ID>/**`, and
   `<specs.dir>/.active_ticket`. Never `git add -A`; never generated files; never `.artel/**`.
   On the kartoteka path no image under the trail is ever staged: add one exclude per image
   extension (`${CLAUDE_PLUGIN_ROOT}/docs/spec-storage.md` §4.6), and leave
   `'<specs.dir>/<TICKET_ID>'` out when that folder neither exists nor has tracked files —
   `git add` refuses a pathspec that matches nothing:

       git add -- <the ticket's changed files> '<specs.dir>/<TICKET_ID>' '<specs.dir>/.active_ticket' ':(exclude,icase,glob)<specs.dir>/<TICKET_ID>/**/*.png' ':(exclude,icase,glob)<specs.dir>/<TICKET_ID>/**/*.jpg' ':(exclude,icase,glob)<specs.dir>/<TICKET_ID>/**/*.jpeg' ':(exclude,icase,glob)<specs.dir>/<TICKET_ID>/**/*.gif' ':(exclude,icase,glob)<specs.dir>/<TICKET_ID>/**/*.webp'
5. **Commit.** Nothing staged (`git diff --cached --quiet` exits 0 — on the kartoteka path, the
   only changes were images a failed sweep left untracked) → skip the commit and go on to the
   push. Otherwise: conventional message, always English, subject line only, no trailers.
6. **Push.** `git push -u origin <branch>`. Rejected non-fast-forward → stop-and-ask (the user
   reconciles; force-push is never an option).
7. **Journal.** Checkpoint entry: commit hash, subject, push result, verify fix rounds.

`pr-create` remains the PR-opening close-out; after phase checkpoints its own commit step usually
finds a clean tree and skips — it is idempotent by design.
