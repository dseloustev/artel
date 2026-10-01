# The full head

The head for an `architectural` size: design analysis, the PRD interview, the vision, research
and the plan, the plan check, the tasklist and its review, then THE ONE PAUSE. Read by
`feature-development` step 4 (`${CLAUDE_PLUGIN_ROOT}/skills/feature-development/SKILL.md`), and
by a head that raises the size. `SKILL.md` below is that file; its steps 0–3 have run: the
config is read, the ticket is active, the spec store is resolved, `idea.md` exists (gate 0) and
the size is recorded. Its flags, and its step 2 rule for reading "artifact exists" and a gate's
status on the kartoteka path, apply to every gate here. Pass `$0` (including phase) to every
sub-skill.

This is the chatty part of the run: it is not armed yet, so every question here is a plain
`AskUserQuestion` with no `pause_reason`. Skip any gate whose artifact already exists (resume);
re-read artifacts after every sub-skill/agent return. Gate 3.5 has no artifact — it re-runs
after any write to the plan file, including planner regeneration and the pause's fold-back, and
unconditionally on resume; a green run is recorded by resetting the bounce line to
`**Plan-check bounces:** 0`. Gate 4.2 has no artifact to skip on either: it runs while the plan
is not yet `PLAN_APPROVED` (its own section follows the table).

## Gates

| # | Gate | Action (skip if artifact exists) |
|---|------|----------------------------------|
| 0.5 | `DESIGN_ANALYZED` — `design-analysis.md` has status `DESIGN_ANALYZED`, or `idea.md` has no `figma.com/design` link | `design.figma` disabled (config.md) → skip silently. Enabled → Grep `idea.md` for `figma.com/design` (kartoteka path: `doc=$(spec_store.py get <specs.dir>/<TICKET_ID>/idea.md) && printf '%s\n' "$doc" | grep -q figma.com/design`). Link present → `Skill: figma-analysis` with `$0` (chatty head — its Major-findings handshake may ask; on `DESIGN_BLOCKED` — returned by the skill or already recorded in an existing artifact's status — stop the pipeline and report the parked findings). No link → skip silently. |
| 1 | `PRD_READY` — PRD status `PRD_READY` | `Skill: analysis` with `$0 $1`, plus `--local` when this run was invoked with it — runs the upfront interview (chatty by design). When the ticket has a `diagnosis.md` or a `spike.md`, the skill hands each to the analyst as an input beside `idea.md`. |
| 2 | `VISION_READY` — `vision.md` status `VISION_READY` | `Skill: generate-vision` with `$0` — consumes the PRD; ends with its one wholesale checkpoint. |
| 3 | plan drafted — `plan.md` exists | `Skill: researcher` then `Skill: planner` (both `$0`; `researcher` also takes `--local` when this run was invoked with it) — **silent**: their questions land in `.artel/run/<TICKET_ID>/open-questions.md` (autonomous-run.md §3). |
| 3.5 | `PLAN_GROUNDED` — plan-check green | Run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/plan_check.py --plan <plan-path> --strict` (kartoteka path: `set -o pipefail; python3 ${CLAUDE_PLUGIN_ROOT}/scripts/spec_store.py get <plan-path> | python3 ${CLAUDE_PLUGIN_ROOT}/scripts/plan_check.py --plan - --strict`), where `<plan-path>` is the phase-aware plan path per ticket-parsing.md §4. Exit 0 → proceed. Exit 1 → append/update `**Plan-check bounces:** N` at the bottom of `<plan-path>` (kartoteka path: `artifact_patch(project=<project>, …)` replacing the existing bounce line, or `append` it), and while `N <= MAX_PLAN_CHECK_BOUNCES = 2`: `SendMessage` the `data.unresolved` list to the `planner` agent ("resolve or declare `new:`"), regenerate, re-run the check. Planner regeneration rewrites `<plan-path>` and drops the bounce line with it; after each regeneration re-append `**Plan-check bounces:** N` (N = bounces performed so far) before re-running the check. Third failure → stop and ask (chatty head — plain `AskUserQuestion`, no `pause_reason`) without writing N=3 — the file shows `**Plan-check bounces:** 2` at the stop. Exit 2 → environment error: stop-and-ask pointing at setup, never a bounce. |
| 4 | `TASKLIST_READY` — tasklist status `TASKLIST_READY` | `Skill: tasklist` with `$0`, plus `--local` when this run was invoked with it — silent, HITL-tagged; the flag keeps its task-queue mirror from writing rows. |
| 4.2 | `PLAN_REVIEWED` — the plan review ran (the plan is not yet `PLAN_APPROVED`) | The plan review before the pause — Gate 4.2, below the table: the mechanical check (`tasklist_tasks.py --check`), then `Skill: run-reviewer --plan`, with at most `MAX_PLAN_REVIEW_ROUNDS = 2` fix rounds to `task-planner`. |
| 4.5 | phase extraction (phase runs only) | `Skill: sync-phases` with `$0` — creates `phase-<N>/tasks.md` when missing. |

### Gate 4.2 — the plan review

Runs while the plan's status is not `PLAN_APPROVED` (`${CLAUDE_PLUGIN_ROOT}/docs/task-grammar.md`
§4). A resume after approval never re-runs it. It belongs to the chatty head: the run is not armed
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

## THE ONE PAUSE — plan+tasklist approval

Present via `AskUserQuestion` in one interaction: plan summary, the task list with its `[HITL: …]`
tags called out, every `Status: open` entry from `.artel/run/<TICKET_ID>/open-questions.md`
(proposed defaults as the first, "(Recommended)" option each), the plan review's open findings
as their own section — gate 4.2's last check and last `plan-review.md`, Critical and Important
first, each with where, what and the smallest fix; `none` when nothing is open — plus, when gate
4.2's requirements read gave
`absent`, the line `no requirement coverage — the PRD predates requirement IDs`, and a note that
approval also authorizes the run's checkpoint commits & pushes to `origin` (planning docs now,
one commit+push per completed phase — see `tail.md`, `## Checkpoint commits & pushes`).

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
  **Abort**. Proceed to `SKILL.md` step 5.
- **Request changes** → route feedback to `planner`/`tasklist`, regenerate, delete
  `.artel/run/<TICKET_ID>/plan-review.md`, run gates 3.5 and 4.2 again, and repeat this pause.
- **`yolo` only:** skip the `AskUserQuestion` — treat every
  `.artel/run/<TICKET_ID>/open-questions.md` default as the accepted answer, run the same
  fold-back (planner/tasklist agents, the `analyst` for an already-met answer,
  `resolved: "<answer>"` flips, status `PLAN_APPROVED`) and the check after it, and proceed. The
  HITL tags remain armed — yolo removes this pause only. One exception: a Critical or Important
  plan-review finding still open after that — from the check after the fold-back, or left by
  gate 4.2's agent check — stops the run and presents this pause after all, findings first. That
  is a guardrail, not a pause preference — the lean head's confirmation makes the same
  exception. Minor findings never stop a `yolo` run; the run-start journal entry lists them.

**Routes at the pause** (`${CLAUDE_PLUGIN_ROOT}/docs/autonomous-run.md`
§16.1). The same interaction lists every task's effective route with its reasons, one line each
— `2.3 full — declared: money-movement path; floor: sensitive path (payments): src/payments/refund.py`
— from the parser's rows (`route`, `route_reason`, `route_reasons`, `route_effective`; the
command the tail's re-mirror runs, without `task_create`), every task `full` when `review.perTask`
is `true`. The person may change any route, down as well as up. On approve each change joins the
fold-back: the `tasklist` agent rewrites that task's `Route:` line as `<light|full> — set at
approval`, and the run-start journal entry (`SKILL.md` step 5) lists every change, naming any
floor it lowered. In `yolo` the routes stand as declared and floored.
