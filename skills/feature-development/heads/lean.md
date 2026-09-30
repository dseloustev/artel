# The lean head

The head for a `bounded` size: establish the work list and confirm it once. Read by
`feature-development` step 4 (`${CLAUDE_PLUGIN_ROOT}/skills/feature-development/SKILL.md`), and
after the bug head when the person chooses to treat a fault it could not reproduce as a bounded
change. `SKILL.md` below is that file; its steps 0–3 have run: the config is read, the ticket is
active, the spec store is resolved, `idea.md` exists (gate 0) and the size is recorded. Its
flags apply here. Pass `$0` to every sub-skill.

No PRD, vision or plan is written here and none is required; one that exists is used. This is
the chatty part of the run: it is not armed yet, so every question here is a plain
`AskUserQuestion` with no `pause_reason`.

On phase-scoped runs (`PHASE_NUM` set), first invoke `Skill: sync-phases` with `$0` to extract
`phase-<N>/tasks.md` from `tasklist.md` when it is missing.

**A finished work list stops the head.** When a tasklist exists (phase-scoped
`phase-<N>/tasks.md` or ticket-wide `tasklist.md`) and none of its boxes is open — the grep of
`SKILL.md` step 3 finds no `- [ ]` in it — there is nothing to plan and nothing to confirm. Do
not invoke `generate-tasklist`, which skips a tasklist that exists, and do not go on to
`SKILL.md` step 5. Print

    This ticket's work list is complete: nothing was planned and the run is not armed. For follow-up work, add tasks to the work list (/artel:tasks add <TICKET_ID> "<title>" --iteration <N>, or by hand) and run this again, or open a new ticket.

and stop, in every mode.

## The ladder

1. **Tasklist with incomplete `- [ ]` tasks exists** (phase-scoped `phase-<N>/tasks.md` or
   ticket-wide `tasklist.md`) → it is the work list. First run the plan check on it
   (`${CLAUDE_PLUGIN_ROOT}/docs/task-grammar.md` §6): `<tasklist-path>` is that tasklist,
   `<prd-path>` the phase-aware PRD with its read fallback
   (`${CLAUDE_PLUGIN_ROOT}/docs/ticket-parsing.md` §4–§5). No PRD there (kartoteka path:
   `spec_store.py exists <prd-path>` exits 3) → `<requirements>` is `absent`; otherwise read its
   active IDs and turn `data` into `<requirements>` — `present: false` → `absent`,
   `present: true` with an empty `ids` → `none`, otherwise the `ids` joined by `,`. Then check
   the tasklist. Files path first, kartoteka path (`docs/spec-storage.md` §4.2) second, for each
   command:

       python3 ${CLAUDE_PLUGIN_ROOT}/scripts/tasklist_tasks.py requirements --prd <prd-path>
       set -o pipefail; python3 ${CLAUDE_PLUGIN_ROOT}/scripts/spec_store.py get <prd-path> | python3 ${CLAUDE_PLUGIN_ROOT}/scripts/tasklist_tasks.py requirements --prd -
       python3 ${CLAUDE_PLUGIN_ROOT}/scripts/tasklist_tasks.py --tasklist <tasklist-path> --ticket-key <TICKET_ID> --check --requirements <requirements>
       set -o pipefail; python3 ${CLAUDE_PLUGIN_ROOT}/scripts/spec_store.py get <tasklist-path> | python3 ${CLAUDE_PLUGIN_ROOT}/scripts/tasklist_tasks.py --tasklist - --ticket-key <TICKET_ID> --check --requirements <requirements>

   Then present a one-screen summary (tasks + any `[HITL: …]` tags + the check's findings,
   Critical and Important first, each as `<task> <severity> <rule>: <message>`) via
   `AskUserQuestion` — **Confirm** / **Adjust** (feedback via "Other"). This is the run's one
   pause. There is no automatic fix round here: the person reads the findings and decides. An
   old-format tasklist (`data.format` `legacy`) is still the work list: it has no findings to
   show and no routes. An exit `2` from either command shows
   `plan check: not run (<error.kind>)` instead.
2. **Otherwise** → `Skill: generate-tasklist` with `$0`, plus `--local` when this run was
   invoked with it. Its questions+approval round IS the mini-interview and the one pause — do
   not add another. The writer grounds the work list in `idea.md`, in `vision.md`, `spike.md`
   and `diagnosis.md` when the ticket has them, and in the code, and it writes the task grammar,
   so the list carries routes.

On the kartoteka path these existence checks are one `spec_store.py list <TICKET_ID>`.

The confirmed work list is the deviation anchor. The confirmation presentation also notes that
confirming authorizes the run's checkpoint commits & pushes to `origin` (work-list docs now, one
commit+push per completed phase — procedure: `tail.md` `## Checkpoint commits & pushes`), and
that the run ends with a pull request: in `plan-gate` a question before it is opened, in `yolo`
opened without asking. On branch 2, say the same in one line before invoking the skill — its
approval round is the confirmation.

**`yolo` only:** present nothing — the derived work list stands. In ladder branch 1, a Critical
or Important plan-check finding still presents the confirmation, findings first: an unresolved
finding is a guardrail, not a pause preference.

The check's outcome goes into the run-start journal entry (`SKILL.md` step 5). Branch 1:
`plan check: <c> Critical, <i> Important, <m> Minor`, `plan check: skipped (old-format tasklist)`
or `plan check: not run (<error.kind>)`. Branch 2: `plan check: run by generate-tasklist` — the
skill checks its own draft and its written tasklist.

## Routes at the confirmation

On a task-format tasklist (`${CLAUDE_PLUGIN_ROOT}/docs/autonomous-run.md` §16.1) branch 1's
summary lists every task's effective route with its reasons, one line each — `2.3 full —
declared: money-movement path; floor: sensitive path (payments): src/payments/refund.py` —
from a parser run over the work list (the tail's re-mirror command, without `task_create`),
every task `full` when `review.perTask` is `true`. **Adjust** may change any route, down as well
as up: write each change into that task's `Route:` line as `<light|full> — set at approval`
(kartoteka path: one `artifact_patch`) and list it in the run-start journal entry (`SKILL.md`
step 5), naming any floor it lowered. On branch 2 the pause is `generate-tasklist`'s approval round, and
a route change asked there reaches `tasklist-writer` like any other change; `SKILL.md` step 5
announces the routes either way. An old-format tasklist shows no routes.

## Raised by the writer

On branch 2 `generate-tasklist` may end without a work list. Its whole output is then the line
`RAISE: <reason>; <reason>` followed by `Next: /artel:feature-development <TICKET_ID> --head=full`,
and it stops before its Phase 1b. The `tasklist-writer` raises in two cases only: an open
product question it cannot ground in the ticket or the code; or, with no vision, work that needs
more than one iteration.

Inside this run that is a raise, not a stop. Follow `SKILL.md` step 3, "The ratchet": rewrite
`sizing.json`, print `Size raised: bounded → architectural — full head. Reason: <reason>.` with
the writer's reasons, then read `${CLAUDE_PLUGIN_ROOT}/skills/feature-development/heads/full.md`
and run it. Do not act on the `Next:` line — it is for a person who ran the skill à la carte —
and do not invoke `generate-tasklist` again. The full head starts from what the ticket already
has: `idea.md`, and a `spike.md` or a `diagnosis.md` when present.

The skill ends the same way, with no `RAISE:` line, when the ticket carries a `diagnosis.md`
whose status is `DIAGNOSED_STRUCTURAL` — one written à la carte, on a ticket then run with
`--head=lean`. Its output is
`This diagnosis is structural. Next: /artel:feature-development <TICKET_ID> --head=full`.
Treat that line as a raise too, with the diagnosis' structural condition as the reason.
