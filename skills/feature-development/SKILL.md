---
name: feature-development
description: "End-to-end autonomous feature workflow: interview -> PRD -> vision -> plan -> tasks -> ONE approval pause -> autonomous implementation, review, docs"
argument-hint: "[ticket-id] or [ticket-id]-[phase] [description-file] [--mode=yolo|plan-gate|full-gates] [--dry-run] [--local]"
---

Autonomous orchestrator for the full feature pipeline. Contract:
`${CLAUDE_PLUGIN_ROOT}/docs/autonomous-run.md` (read it first). Shared procedures:
`${CLAUDE_PLUGIN_ROOT}/docs/orchestrator-common.md`. Commit/push procedure: `## Checkpoint
commits & pushes` in `${CLAUDE_PLUGIN_ROOT}/skills/feature-development/tail.md` (shared with
`dev`). Pass `$0` (including phase; on
multi-phase traversal the current `<TICKET_ID>-<N>`) to every sub-skill. Skip any gate whose
artifact already exists (resume); re-read artifacts after every sub-skill/agent return. (gate 3.5
has no artifact — it re-runs after any write to the plan file, including planner regeneration and
the §3 fold-back, and unconditionally on resume; a green run is recorded by resetting the bounce
line to `**Plan-check bounces:** 0`). Gate 4.2 has no artifact to skip on either: it runs while
the plan is not yet `PLAN_APPROVED` (its own section follows the table in step 2).

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

On the kartoteka path "artifact exists" in every gate below is one `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/spec_store.py list <TICKET_ID>`, re-run after each sub-skill returns, and a gate's status is read without the document entering this context: `doc=$(spec_store.py get <path>) && printf '%s\n' "$doc" | spec_store.py status` (files path: `spec_store.py status < <path>`). `status` prints the header's `status:` (or, for a document written before 0.21.0, its `Status:` line's value) and exits `1` when none is declared (spec-storage.md §3.2, §8).

| # | Gate | Action (skip if artifact exists) |
|---|------|----------------------------------|
| 0 | `IDEA_READY` — `idea.md` exists | `Skill: generate-idea` with `$0 $1`, under **every** adapter — it branches on `tracker.adapter` itself (config.md): a tracker imports the ticket; `"none"` (local-only) seeds `idea.md` from the `$1` description file, or runs the same input gate `analysis` does when `$1` is absent. Gate 2 hard-requires `idea.md`, so the pipeline seeds it here rather than letting a `"none"` run die at the vision gate. |
| 0.5 | `DESIGN_ANALYZED` — `design-analysis.md` has status `DESIGN_ANALYZED`, or `idea.md` has no `figma.com/design` link | `design.figma` disabled (config.md) → skip silently. Enabled → Grep `idea.md` for `figma.com/design` (kartoteka path: `doc=$(spec_store.py get <specs.dir>/<TICKET_ID>/idea.md) && printf '%s\n' "$doc" | grep -q figma.com/design`). Link present → `Skill: figma-analysis` with `$0` (chatty head — its Major-findings handshake may ask; on `DESIGN_BLOCKED` — returned by the skill or already recorded in an existing artifact's status — stop the pipeline and report the parked findings). No link → skip silently. |
| 1 | `PRD_READY` — PRD status `PRD_READY` | `Skill: analysis` with `$0 $1`, plus `--local` when this run was invoked with it — runs the upfront interview (chatty by design). |
| 2 | `VISION_READY` — `vision.md` status `VISION_READY` | `Skill: generate-vision` with `$0` — consumes the PRD; ends with its one wholesale checkpoint. |
| 3 | plan drafted — `plan.md` exists | `Skill: researcher` then `Skill: planner` (both `$0`; `researcher` also takes `--local` when this run was invoked with it) — **silent**: their questions land in `.artel/run/<TICKET_ID>/open-questions.md` (autonomous-run.md §3). |
| 3.5 | `PLAN_GROUNDED` — plan-check green | Run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/plan_check.py --plan <plan-path> --strict` (kartoteka path: `set -o pipefail; python3 ${CLAUDE_PLUGIN_ROOT}/scripts/spec_store.py get <plan-path> | python3 ${CLAUDE_PLUGIN_ROOT}/scripts/plan_check.py --plan - --strict`), where `<plan-path>` is the phase-aware plan path per ticket-parsing.md §4. Exit 0 → proceed. Exit 1 → append/update `**Plan-check bounces:** N` at the bottom of `<plan-path>` (kartoteka path: `artifact_patch(project=<project>, …)` replacing the existing bounce line, or `append` it), and while `N <= MAX_PLAN_CHECK_BOUNCES = 2`: `SendMessage` the `data.unresolved` list to the `planner` agent ("resolve or declare `new:`"), regenerate, re-run the check. Planner regeneration rewrites `<plan-path>` and drops the bounce line with it; after each regeneration re-append `**Plan-check bounces:** N` (N = bounces performed so far) before re-running the check. Third failure → stop and ask (chatty head — plain `AskUserQuestion`, no `pause_reason`) without writing N=3 — the file shows `**Plan-check bounces:** 2` at the stop. Exit 2 → environment error: stop-and-ask pointing at setup, never a bounce. |
| 4 | `TASKLIST_READY` — tasklist status `TASKLIST_READY` | `Skill: tasklist` with `$0`, plus `--local` when this run was invoked with it — silent, HITL-tagged; the flag keeps its task-queue mirror from writing rows. |
| 4.2 | `PLAN_REVIEWED` — the plan review ran (a tasklist in the task grammar, plan not yet `PLAN_APPROVED`) | The plan review before the pause — Gate 4.2, below the table: the mechanical check (`tasklist_tasks.py --check`), then `Skill: run-reviewer --plan`, with at most `MAX_PLAN_REVIEW_ROUNDS = 2` fix rounds to `task-planner`. An old-format tasklist records `PLAN_REVIEWED: skipped (old-format tasklist)`. |
| 4.5 | phase extraction (phase runs only) | `Skill: sync-phases` with `$0` — creates `phase-<N>/tasks.md` when missing. |

#### Gate 4.2 — the plan review

Runs only for a tasklist in the task grammar (`${CLAUDE_PLUGIN_ROOT}/docs/task-grammar.md` §4),
and only while the plan's status is not `PLAN_APPROVED`. A resume after approval never re-runs
it, and an old-format tasklist never sees it. It belongs to the chatty head: the run is not armed
yet, its questions carry no `pause_reason`, and its rounds never count toward
`counters.correction_rounds`. `<tasklist-path>` is the phase-aware tasklist and `<prd-path>` the
phase-aware PRD with its read fallback (`${CLAUDE_PLUGIN_ROOT}/docs/ticket-parsing.md` §4–§5);
`--ticket-key` takes the canonical `<TICKET_ID>`, without the phase suffix.

1. **Requirements.** Read the PRD's active requirement IDs. Files path first, kartoteka path
   (`docs/spec-storage.md` §4.2) second:

       python3 ${CLAUDE_PLUGIN_ROOT}/scripts/tasklist_tasks.py requirements --prd <prd-path>
       set -o pipefail; python3 ${CLAUDE_PLUGIN_ROOT}/scripts/spec_store.py get <prd-path> | python3 ${CLAUDE_PLUGIN_ROOT}/scripts/tasklist_tasks.py requirements --prd -

   Turn `data` into `<requirements>`: `present: false` → `absent`; `present: true` with an empty
   `ids` → `none`; otherwise the `ids` joined by `,`. Exit `2` → environment error: stop-and-ask
   (plain `AskUserQuestion`), never a round.
2. **Mechanical check** (`${CLAUDE_PLUGIN_ROOT}/docs/task-grammar.md` §6). Files path first,
   kartoteka path second:

       python3 ${CLAUDE_PLUGIN_ROOT}/scripts/tasklist_tasks.py --tasklist <tasklist-path> --ticket-key <TICKET_ID> --check --requirements <requirements>
       set -o pipefail; python3 ${CLAUDE_PLUGIN_ROOT}/scripts/spec_store.py get <tasklist-path> | python3 ${CLAUDE_PLUGIN_ROOT}/scripts/tasklist_tasks.py --tasklist - --ticket-key <TICKET_ID> --check --requirements <requirements>

   `data.format` `legacy` → the tasklist predates the grammar: record
   `PLAN_REVIEWED: skipped (old-format tasklist)` for the pause and step 4, and end the gate.
   Exit `2` → environment error, as in step 1. Otherwise keep `data.findings` and
   `data.coverage`: exit `1` means a Critical or Important finding, exit `0` none.
3. **Agent check.** A `.artel/run/<TICKET_ID>/plan-review.md` whose `**Tasklist:**` line names
   another tasklist is stale: delete it first. Then `Skill: run-reviewer --plan` with `$0`. It
   returns `Plan review round <k>: <c> Critical, <i> Important, <m> Minor — .artel/run/<TICKET_ID>/plan-review.md`.
4. **Fix round.** A Critical or Important finding from either half, and `k` at most
   `MAX_PLAN_REVIEW_ROUNDS = 2` → `SendMessage`/re-invoke the `task-planner` agent (a fresh
   dispatch carries `TICKET_ID`, `TICKET_NUM`, `PHASE_NUM` and the **Spec store:** field) with
   the check's Critical and Important findings verbatim — `severity`, `task`, `line`, `rule`,
   `message` — and the path of `plan-review.md`, per its "Fix rounds and fold-backs" section.
   Then run the gate again from step 1. Minor findings never start a round. An
   `uncovered-requirement` finding for an ID (in `data.coverage.uncovered`) that an open question
   already asks about — `Is R<n> already met by the current code?`, `from: tasklist` — is not
   sent: the pause answers it.
5. **End.** No Critical or Important finding left, or round `k` still has some after
   `MAX_PLAN_REVIEW_ROUNDS` fix rounds → the gate ends. Whatever is still open — the last
   check's findings and the last `plan-review.md`'s, Minor included — goes to THE ONE PAUSE as
   its own section. On a resume before approval, a `plan-review.md` for this tasklist whose round
   is above `MAX_PLAN_REVIEW_ROUNDS` has used its rounds: run steps 1–2 and go to the pause;
   otherwise run the gate from step 1, and the reviewer continues the round count.

### 3. THE ONE PAUSE — plan+tasklist approval

Present via `AskUserQuestion` in one interaction: plan summary, the task list with its `[HITL: …]`
tags called out, every `Status: open` entry from `.artel/run/<TICKET_ID>/open-questions.md`
(proposed defaults as the first, "(Recommended)" option each), the plan review's open findings
as their own section — gate 4.2's last check and last `plan-review.md`, Critical and Important
first, each with where, what and the smallest fix; `none` when nothing is open, or
`PLAN_REVIEWED: skipped (old-format tasklist)` — plus, when gate 4.2's requirements read gave
`absent`, the line `no requirement coverage — the PRD predates requirement IDs`, and a note that
approval also authorizes the run's checkpoint commits & pushes to `origin` (planning docs now,
one commit+push per completed phase — see `## Checkpoint commits & pushes`).

- **Approve** → `SendMessage`/re-invoke `planner` and `tasklist` agents to fold any answer that
  overrides a default back into `plan.md` / the tasklist — the tasklist's fields as well as its
  prose: an answer that changes a route, a dependency or a file list changes that field — flip
  the `.artel/run/<TICKET_ID>/open-questions.md` entries to `resolved: "<answer>"`, and remove
  `(provisional — Q<n>)` markers (plan becomes status `PLAN_APPROVED`). An answer confirming
  that the current code already meets a requirement goes to the `analyst` agent instead, which
  adds `(already met — <evidence>)` to that requirement in the phase-aware PRD under its "After
  `PRD_READY`" rule; no task is written for it. This write re-arms gate
  3.5 — if the fold-back touched `plan.md`, re-run the plan-check before proceeding. After any
  fold-back into the tasklist or the PRD, re-run gate 4.2's requirements read and mechanical
  check (its steps 1–2 — no agent check, no fix round). A Critical or Important finding it
  reports stops and asks (plain `AskUserQuestion` — the run is not armed yet): the findings,
  then **Fix** (one more `task-planner` round, then the check again) / **Proceed as is** /
  **Abort**. Proceed to step 4.
- **Request changes** → route feedback to `planner`/`tasklist`, regenerate, delete
  `.artel/run/<TICKET_ID>/plan-review.md`, run gates 3.5 and 4.2 again, and repeat this pause.
- **`yolo` only:** skip the `AskUserQuestion` — treat every
  `.artel/run/<TICKET_ID>/open-questions.md` default as the accepted answer, run the same
  fold-back (planner/tasklist agents, the `analyst` for an already-met answer,
  `resolved: "<answer>"` flips, status `PLAN_APPROVED`) and the check after it, and proceed. The
  HITL tags remain armed — yolo removes this pause only. One exception: a Critical or Important
  plan-review finding still open after that — from the check after the fold-back, or left by
  gate 4.2's agent check — stops the run and presents this pause after all, findings first. That
  is a guardrail, not a pause preference, as when `dev` asks in `yolo` about an ambiguous
  description. Minor findings never stop a `yolo` run; the run-start journal entry lists them.

**Routes at the pause** (task-format tasklists — `${CLAUDE_PLUGIN_ROOT}/docs/autonomous-run.md`
§16.1). The same interaction lists every task's effective route with its reasons, one line each
— `2.3 full — declared: money-movement path; floor: sensitive path (payments): lib/ramps/ramps_bloc.dart`
— from the parser's rows (`route`, `route_reason`, `route_reasons`, `route_effective`; the
command step 5's re-mirror runs, without `task_create`), every task `full` when `review.perTask`
is `true`. The person may change any route, down as well as up. On approve each change joins the
fold-back: the `tasklist` agent rewrites that task's `Route:` line as `<light|full> — set at
approval`, and the run-start journal entry (step 4) lists every change, naming any floor it
lowered. In `yolo` the routes stand as declared and floored. An old-format tasklist shows no
routes.

### 4. Arm the run

Run the risk classifier (autonomous-run.md §10) over plan + tasklist. `forced_floor:
"full-gates"` ⇒ stop here: report the matched sensitive categories and instruct the user to
re-run with `--step`. Otherwise write `.artel/run/<TICKET_ID>/run-state.json` per
autonomous-run.md §2: `run_active: true`, `completed: false`, `pause_reason: null`, fresh
`started_at`, zeroed counters, `requested_local` (§2 — the `--local` opt-out, recorded because
a resumed run has no argument list left to read it from), `deviation_files: []` (§2), plus the
six mode fields (§10), with
`gates_confirmed: ["TASKLIST_READY"]`. Announce the effective mode and reasons. On resume,
re-derive the mode fields before re-arming — never trust stale ones, and carry `requested_local`
and `deviation_files` forward unchanged: the first is the user's opt-out, the second the run's
record of deviations, and neither is a classifier output. From here the run is
silent except deviations, HITL tasks, and cap escalations. Create
`.artel/run/<TICKET_ID>/run-journal.md` with the run-start entry (autonomous-run.md §11): mode
resolution, reasons, HITL tags count, and the plan review's outcome —
`PLAN_REVIEWED: <k> round(s), <n> finding(s) left open` with the Minor ones listed, or
`PLAN_REVIEWED: skipped (old-format tasklist)`.

Then run the **planning checkpoint** (see `## Checkpoint commits & pushes`): commit
`<specs.dir>/<TICKET_ID>/**` + `<specs.dir>/.active_ticket` and push — subject `docs: <TICKET_ID>
planning artifacts` (phase runs: `docs: <TICKET_ID> phase <N> planning artifacts`). Journal it as
an external action. No verify gate here (`verify.commands`) — docs only, no code yet. On the
kartoteka path the procedure sweeps images first and never stages one (its steps 2 and 4); when,
images aside, only `.active_ticket` changed, skip the commit and journal `planning checkpoint: skipped — the spec trail is in kartoteka`.

**Record the baseline** (`${CLAUDE_PLUGIN_ROOT}/docs/gates.md` §1):
`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify.py checkpoint --record-baseline --ticket <TICKET_ID>`.
Exit 2 → environment error, stop-and-ask. Journal `baseline: recorded (<n> keys across <m>
stages)` or `baseline: skipped (verify.commands empty)`. **Fresh arm only** — the moment
`run-state.json` is first written: on resume an existing `.artel/run/<TICKET_ID>/verify-baseline.json`
is kept and a missing one stays missing (the tree already carries the branch's changes, so a
snapshot now would hide them; the checkpoint gate then reports `baseline: absent` and treats any
red as red). A phase boundary never re-records. `--step` runs record it too — it is evidence, not
run state.

### 5. Autonomous tail

Read `${CLAUDE_PLUGIN_ROOT}/skills/feature-development/tail.md` and run it from `## Phase
traversal` to its final report: phase traversal and the re-mirror, gates 5–10.7, the completion
gate, the PR description and the PR gate, the description-file sync and the final report. Its
`## Checkpoint commits & pushes` is the procedure step 4's planning checkpoint runs.

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
  surface-skip entry, the `open-questions.md` status flips (same directory), the deletion of a
  stale or reset `plan-review.md` (same directory — gate 4.2 and **Request changes**), the
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
  then `SendMessage` the agent to write it to its logical path with its header's `version:`
  set to `<N>`) / Pause without saving. Clear
  `pause_reason` only after a Retry succeeds. There is no "continue locally" mid-run. Headless:
  `specs.onUnavailable` (§5.4).
