# The tail

Everything a run does once it is armed: the implement loop, the review, the runtime gate, the
docs stage, the checkpoints, the completion gate and the PR close-out. Read by
`feature-development` when the run is armed (`${CLAUDE_PLUGIN_ROOT}/skills/feature-development/SKILL.md`
steps 5–6), and on every resume of an armed run. `SKILL.md` below is that file; its steps 0–1.5
have run, so the config is read, the ticket is active and the spec store is resolved. Contract:
`${CLAUDE_PLUGIN_ROOT}/docs/autonomous-run.md`. Pass `$0` (on multi-phase traversal the current
`<TICKET_ID>-<N>`) to every sub-skill, and re-read artifacts after every sub-skill/agent return.

Nothing here depends on how the work list was produced. A rule that varies is keyed on what
exists for the ticket — a plan, a PRD, a tasklist's status — never on which path wrote it. Every
`AskUserQuestion` below is bracketed by a `pause_reason` set/clear (autonomous-run.md §2).

## Phase traversal

Explicit `<TICKET_ID>-<N>` in `$0` → run exactly that phase (one pass of
the `## Gates` table). Ticket-wide `$0` with a multi-phase `tasklist.md` (Progress Report table / `##
Iteration N` headers) → loop the remaining incomplete phases in order. Each iteration: write
`<TICKET_ID>-<N>` to `<specs.dir>/.active_ticket`; update `run-state.json`'s `ticket` field and
refresh `started_at` (a phase boundary re-arms the wall-clock budget); reset the review round — delete a stale
ticket-wide `review.md` on the files path (it survives in that phase's checkpoint commit), or store
the round-0 version on the kartoteka path (`${CLAUDE_PLUGIN_ROOT}/docs/spec-storage.md` §4.4; it
survives as an earlier version) — which resets the counter per autonomous-run.md §5; then renew the decision as `SKILL.md` step 1.5 says; run `Skill: sync-phases` with
`<TICKET_ID>-<N>` (extract `phase-<N>/tasks.md` when missing); then gates
5–10.7 passing `<TICKET_ID>-<N>` to every sub-skill. Single-phase tasklist → one ticket-wide pass
ending at gate 10.7.

**Re-mirror first.** Per `${CLAUDE_PLUGIN_ROOT}/docs/task-queue.md` §1 and §2, on the
queue path — and only there, so a `--local` run skips it — run this before the first
gate-5 dispatch of each phase. Files path first, kartoteka path (`docs/spec-storage.md` §4.2) second:

    python3 ${CLAUDE_PLUGIN_ROOT}/scripts/tasklist_tasks.py --tasklist <specs.dir>/<TICKET_ID>/tasklist.md --ticket-key <TICKET_ID>
    set -o pipefail; python3 ${CLAUDE_PLUGIN_ROOT}/scripts/spec_store.py get <specs.dir>/<TICKET_ID>/tasklist.md | python3 ${CLAUDE_PLUGIN_ROOT}/scripts/tasklist_tasks.py --tasklist - --ticket-key <TICKET_ID>

and `task_create` the rows it emits — `data.iterations`, then `data.sections` (§2 steps
2–4, surfacing every `data.warnings` line). A tasklist is mirrored by the skill that writes it,
and only when it writes it, so without this step a resumed run — or a
ticket whose tasklist was written before the adapter was reachable — never mirrors at
all, and every implementer dispatch falls back to the file. For a task-format tasklist this
step is also the first mirror when `Skill: tasklist` wrote it: that skill mirrors nothing
before `PLAN_APPROVED` (`${CLAUDE_PLUGIN_ROOT}/docs/task-queue.md` §2). The step is create-only and
idempotent: it never resets a `done` row and never undoes a promotion. Exit `2` → report
`error.kind` and `error.message` and continue on the fallback path. On phase runs it
lands after this section's `Skill: sync-phases`, which is what keeps `tasklist.md`
current when it is read.

## Gates

| # | Gate | Action |
|---|------|--------|
| 5 | `IMPLEMENT_STEP_OK` — every task `- [x]` | Loop `Skill: implementer` with `$0`, plus `--local` when this run was invoked with it — it becomes the dispatch's **Task queue:** field, which is what carries the opt-out to the agent (`${CLAUDE_PLUGIN_ROOT}/docs/task-queue.md` §1). Returns: **completion** → aggregate its `Deviations:`/`Verify iterations:` lines, add every path its `Deviations:` line names to `run-state.json` `deviation_files` (autonomous-run.md §2), and journal its `Report:` path (never open the report — autonomous-run.md §1, "Bulk stays in files"), continue. **Routes** (autonomous-run.md §16) → on a task-format tasklist run `scripts/review_package.py snapshot` before every iteration-task dispatch; when the completion names its task (`Task <N.M>: …`), take that task's route from the parser's row (the re-mirror's run; on a `--local` run, the same command run for the routes alone): `route_effective` — a `Route:` ending `— set at approval` is final over floors 1–3 — raised to `full` when one of its `Files:` is in `deviation_files` or `review.perTask` is `true`; journal `task <N.M>: route <effective> (declared <route>[; floor: <reason>[, <reason>…]]); model <value>`. A `full` task gets the §16.2 wrapper: `diff` + `Skill: run-reviewer --task …` (plus `--local` when this run was invoked with it) after its completion, at most one `## Code Review Fixes` implementer round (`MAX_TASK_REVIEW_ROUNDS = 1`, counted toward `counters.correction_rounds`), one `task review` journal entry; a `light` task gets none. Fix-list dispatches are never wrapped. **`HITL: <reason>`** → set `pause_reason: "hitl-task"`, ask the pre-declared question via `AskUserQuestion`, clear `pause_reason`, `SendMessage` the answer, continue. **`DEVIATION` escalation** → the skill handles the handshake; wrap it: set `pause_reason: "deviation-escalation"` before its `AskUserQuestion`, clear after. **Aborted task** → set `pause_reason: "cap-escalation"`, stop and report that the plan needs revision; the question opens with **Debug it here first** (below the table). |
| 6 | `INDEX_UPDATED` | Optional host index-refresh hook (`${CLAUDE_PLUGIN_ROOT}/docs/orchestrator-common.md` §1): run it when the host has wired one up; silently absent otherwise. |
| 7 | `REVIEW_OK` | `Skill: run-reviewer` with `$0`, plus `--local` when this run was invoked with it — `run-reviewer` records the fix tasks it appends in the task queue, and the flag keeps a local-only run from writing rows (`${CLAUDE_PLUGIN_ROOT}/docs/task-queue.md` §1, §6). Blocking/Important findings → `Skill: implementer` (fix tasks from `## Code Review Fixes`, plus `--local` when this run was invoked with it, plus `--model fable` when the findings come from a `review.md` whose `**Review round:**` is 2 or more — autonomous-run.md §5) → re-review. Cap: `review.md` `**Review round:**` reaching `MAX_REVIEW_ROUNDS = 3` → `pause_reason: "cap-escalation"`, consolidated findings via `AskUserQuestion`, stop. On user guidance: reset the review round (delete `review.md`, or on the kartoteka path store the round-0 version — spec-storage.md §4.4) and resume. |
| 8 | `RUNTIME_OK` | Surface check: with `runtime.surface` set (config.md), collect the run's changed files (diff vs the default branch plus the working tree) and match them against the globs; no match → write `RUNTIME_OK: skipped (no runtime surface)` to the phase-aware `runtime/observation.md` and move on. No `runtime.run` configured → the gate records `skipped (not configured)` (run-app reports this itself). Otherwise `Skill: run-app` with `--gate`. RED caused by a **runtime error in app code** (runtime errors / ERROR logs / a broken UI tree) → append a `- [ ]` task under `## Runtime Fixes` in the phase-aware tasklist (mirroring `## Code Review Fixes`) (kartoteka path: one `artifact_patch(project=<project>, …)` — spec-storage.md §4.3), beneath a new `### runtime-r<n>` source heading (`### runtime-p<N>-r<n>` on a phase-scoped run, n the retry this round is — `${CLAUDE_PLUGIN_ROOT}/docs/task-queue.md` §6), its line a one-line summary of the error with the quoted error nested under it as an indented block (nested lines go to the row's description; the checkbox line is the row's title, capped at 500 characters), and on the queue path — never on a `--local` run — record it before the implementer round: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/tasklist_tasks.py --tasklist <the phase-aware tasklist> --ticket-key <TICKET_ID>` (kartoteka path: `set -o pipefail; python3 ${CLAUDE_PLUGIN_ROOT}/scripts/spec_store.py get <the phase-aware tasklist> | python3 ${CLAUDE_PLUGIN_ROOT}/scripts/tasklist_tasks.py --tasklist - --ticket-key <TICKET_ID>`) (`<TICKET_ID>` the canonical key, without the phase suffix), then `task_create` its `data.sections` (§2's fix-writer rule); when the RED stems from incomplete cross-phase wiring (this phase's code invokes pieces a later phase will build), word the fix task to create the **minimal stubs** that restore launch — no-op implementations / placeholder surfaces with a `TODO: phase <M>` marker — rather than real implementations; stubbing is the expected resolution at a phase boundary and is recorded in the completion's `Deviations:` line. Run `Skill: implementer` once, plus `--local` when this run was invoked with it (`MAX_RUNTIME_RETRIES = 1`; counter in `.artel/run/<TICKET_ID>/runtime-observation.md`, autonomous-run.md §5) — the invocation names `## Runtime Fixes` — then re-run the gate; second RED → cap escalation, whose question opens with **Debug it here first** (below the table). RED from an **environment failure** (a launch/setup failure of `runtime.run` itself, not app code — run-app stops-and-asks for these) → cap escalation immediately, no implementer round. Do not trust a stale green — re-run unless the observation postdates the last change to files matching `runtime.surface` (or the last code change, when it is unset). |
| 10 | `DOCS_UPDATED` | Runs only when a PRD exists for the ticket, at either scope — `spec_store.py exists` over the ticket-wide PRD and over the phase-aware one (`${CLAUDE_PLUGIN_ROOT}/docs/ticket-parsing.md` §4; exit `3` is "none"). Without one, journal `DOCS_UPDATED: skipped (no PRD)` and go on: there is no spec to write docs from. With one: `Skill: docs-update` with `<TICKET_ID>` (ticket-wide) — **once per ticket, on the last phase only, before its 10.7 checkpoint** — so that checkpoint commit carries the docs and the CHANGELOG — never per phase. On a multi-phase traversal it is skipped for every phase but the last; an explicit `<TICKET_ID>-<N>` run of a phase that is not the last journals `DOCS_UPDATED: deferred to the final phase`. |
| 10.5 | phase write-back (phase runs only) | `Skill: sync-phases` with `$0` — sync completion into `tasklist.md`. |
| 10.7 | `PHASE_CHECKPOINT` | Run the phase-end checkpoint (see `## Checkpoint commits & pushes`): the image sweep (kartoteka path) → the `verify.commands` gate → capped `## Verify Fixes` implementer rounds → explicit staging (no trail image on the kartoteka path) → commit (`feat\|fix\|refactor: <TICKET_ID> phase <N> - <phase title>`; no phase → `<ticket summary>`) → push → journal (on the last phase this commit also carries gate 10's docs). Then advance `.active_ticket` to the next incomplete phase; on a multi-phase run loop back to the traversal (next phase), else proceed to the completion gate. |

**Journal (§11):** append an entry to `run-journal.md` at every gate completion, pause/resume,
and external action.
**Budget:** before dispatching any fix-list implementer round (review gate 7, runtime gate 8),
increment `counters.correction_rounds`; at `MAX_TOTAL_CORRECTION_ROUNDS = 8` → journal
the breach, `pause_reason: "cap-escalation"`, consolidated report via `AskUserQuestion`, stop.

## Debug it here first

Three halts of this file are a red gate in app code, and on each the escalation's
`AskUserQuestion` opens with one more option, **Debug it here first**:

- gate 5 — a task aborted when `MAX_VERIFY_ITERATIONS` ran out;
- gate 8 — the runtime gate's second RED from a runtime error in app code;
- the checkpoint procedure's step 3 — still red after `MAX_CHECKPOINT_VERIFY_ROUNDS`.

Nowhere else: not the review cap (findings, answered by guidance), not an environment error,
not `MAX_TOTAL_CORRECTION_ROUNDS`, not the wall clock. A headless run journals and stops as
before — it has no question to add the option to.

Chosen → `Skill: debugging` with the red evidence as its argument: the failing stage, its first
error lines, the path of the task's report. `pause_reason` stays `"cap-escalation"` throughout.
The skill never commits and never writes run state or the journal; you do, from what it
reports:

| The skill reports | Then |
|---|---|
| fixed | Journal `debugged here: <root cause> — <files>`, add those files to `run-state.json` `deviation_files`, and re-run the gate that was red. Green → reset that loop's counter as a resume with guidance does, clear `pause_reason` and continue — for an aborted task, by dispatching the implementer on it again. The next checkpoint commits the fix. |
| not fixed, or the gate is red again after its fix | Ask the escalation question again, without this option: `MAX_DEBUG_HERE_ATTEMPTS = 1`, one attempt per halt. |
| structural (`${CLAUDE_PLUGIN_ROOT}/docs/debugging.md` §4) | The skill's own ticket question runs; the run stays paused for guidance or an abort. |

## Completion gate

Runs once, after the last phase on multi-phase runs. Confirm the eight facts below yourself — they
are artifacts you already read — and journal one line per fact (autonomous-run.md §7). No agent is
dispatched here: `/artel:validate` stays à la carte.

| Fact | How it is read |
|---|---|
| `PLAN_APPROVED` | the plan's status (the same `spec_store.py status` read the gates use); no plan exists for the ticket → `skipped (no plan)` — `gates_confirmed` holding `TASKLIST_READY` is the approval; a plan this run did not approve — its status is not `PLAN_APPROVED` while `gates_confirmed` holds `TASKLIST_READY`, which is a draft left by an earlier invocation or written à la carte on a run whose work list was what the person confirmed → `skipped (plan not approved by this run)` |
| `TASKLIST_READY` | the tasklist's status (`spec_store.py status`); a tasklist that declares none (written by `generate-tasklist` before 0.25.0 — `status` exits `1`) is read from `gates_confirmed` in `run-state.json` |
| `IMPLEMENT_STEP_OK` | `tasklist_tasks.py` over the tasklist (and each `phase-<N>/tasks.md` on phased tickets) reports no unchecked box in any iteration or fix section — a `## Final Verification` section an older tasklist carries counts too; and, on the queue path, every fix-section parent row is closed (`${CLAUDE_PLUGIN_ROOT}/docs/task-queue.md` §6) |
| `REVIEW_OK` | `review.md` exists with a `**Review round:**` line and the tasklist has no unchecked `## Code Review Fixes` box; a `**Verdict:**` line, when the reviewer wrote one, must not read *Needs fixes* |
| `RUNTIME_OK` | the phase-aware `runtime/observation.md` is green or `skipped`, and no file matching `runtime.surface` changed after it |
| `CHECKPOINT_OK` — the final gate | the last checkpoint journal entry is green, and `git diff --name-only <its commit>..HEAD` plus the working tree, filtered by `verify.surface` (absent → every changed file counts), is empty; otherwise run one more checkpoint gate and commit it as a checkpoint (`## Checkpoint commits & pushes`) |
| `DOCS_UPDATED` | `summary.md` exists (`spec_store.py list <TICKET_ID>` on the kartoteka path); no PRD exists for the ticket → `skipped (no PRD)` |
| `AUTOMATION_REMOVED` | `runtime.scaffold` unconfigured → `skipped`; else the scaffold paths `runtime.scaffold.add` introduces are gone (`/artel:remove-automation` was run) |

A red fact routes back to its owning step once — the implementer for boxes, `run-reviewer` for
the review, `run-app --gate` for runtime, the checkpoint procedure for the gate, `docs-update` for
docs (followed by one more checkpoint commit, so they are not left uncommitted) — then
cap-escalates. A fact that reads `skipped` is green. Never set `completed` while a fact is red. All green → the PR description and the PR gate.

## PR description and the PR gate

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

## Description-file sync

Per `${CLAUDE_PLUGIN_ROOT}/docs/orchestrator-common.md` §4 (only when `$1` is a description file
with checkbox tasks).

## Final report

On the kartoteka path, sweep once more before writing it:
`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/spec_store.py image sync <TICKET_ID> --author artel:feature-development`
(`${CLAUDE_PLUGIN_ROOT}/docs/spec-storage.md` §5.6). A failure never pauses: the report lists what
it left local instead.

Ticket; phases traversed; gates passed; aggregated `Deviations:` line (`none` when clean); loop
counters (verify iterations total, review rounds, escalation count); baseline (`recorded` / `skipped` / `absent` — armed before 0.18.0); final gate (the last checkpoint's commit stands, or `re-run`); runtime status;
checkpoint commits (hash + subject each, incl. push results); path to `pr-description.md`; PR
status (`PR_OPENED`/`PR_EXISTS` URL, `skipped-manual`, or `pending`); description-sync status;
reminder that opening the PR remains manual (only when the PR gate was skipped — the work itself
is already committed and pushed by the checkpoints); effective mode + why (`mode_reasons`);
external actions taken unattended; spec store (`kartoteka`, or `files (<reason>)` with the documents left on disk and `/artel:migrate-specs <TICKET_ID>` — spec-storage.md §5.5); images left local on the kartoteka path — each `failed` or `skipped` entry of that sweep with its reason, or on exit `5` or `2` every image still under the trail; an `unrecoverable` sweep's whole message — with the `image sync` command above to move them in (headless runs journal the same lines); path to `run-journal.md`.

## Checkpoint commits & pushes

The one commit and push procedure of a run. Checkpoints keep the branch on `origin` carrying the
latest approved docs and every completed phase. They are orchestrator-owned Bash actions
(`git add` / `git commit` / `git push -u origin`), authorized in advance at the approval pause —
they never pause, and each one is journaled as an external action (autonomous-run.md §11).

| Checkpoint | When | Contents | Subject |
|---|---|---|---|
| Planning (`SKILL.md` step 5) | immediately after arming | `<specs.dir>/<TICKET_ID>/**` + `.active_ticket` (kartoteka path: skipped when, images aside, only `.active_ticket` changed) | `docs: <TICKET_ID> planning artifacts` when a plan exists, `docs: <TICKET_ID> work list` otherwise (phase runs: `… phase <N> planning artifacts` / `… phase <N> work list`) |
| Phase-end (gate 10.7) | after the phase's gates pass | the phase's code changes + updated ticket artifacts | `feat\|fix\|refactor: <TICKET_ID> phase <N> - <phase title>` (no phase → `<ticket summary>`) |

Procedure:

1. **Branch guard.** `git branch --show-current` — on the default branch (`git symbolic-ref
   refs/remotes/origin/HEAD`, fallback `main`) → stop-and-ask (environment error): checkpoints
   never commit to the default branch. Never any `--force` variant anywhere in this procedure.
2. **Image sweep (kartoteka path), then idempotence.** On the kartoteka path, first run
   `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/spec_store.py image sync <TICKET_ID> --author artel:feature-development`
   (`${CLAUDE_PLUGIN_ROOT}/docs/spec-storage.md` §4.6). It moves every untracked image under the
   trail into kartoteka. Exit `5`, or `failed` entries, never pause: journal
   `image-sync: <n> left local — <first error line>` (spec-storage.md §5.6) and go on; those
   images stay untracked, and the next sweep point retries them. Exit `2` with kind
   `unrecoverable` never pauses either, but journal its whole message, not a first line, and
   repeat it in the final report (spec-storage.md §5.6). Any other non-zero exit: as exit `5` —
   never a `STORE_UNAVAILABLE` pause. Then: `git status --porcelain`
   clean → skip the commit (resume-safe); still push when the local branch is ahead of `origin`.
3. **Quality gate (phase-end only).** Run the checkpoint gate (`${CLAUDE_PLUGIN_ROOT}/docs/gates.md`
   §1): `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify.py checkpoint --ticket <TICKET_ID>`. Exit `0`
   → green: journal any `baseline_red` stage by name; an empty `verify.commands` ⇒ the envelope
   says `skipped` — record the verify step as `skipped` in the journal entry and continue to
   staging. Exit `1` → the findings are the `new_keys` of the red stage
   (its `keys` when `data.baseline` is `absent` or `disabled` — a run armed before 0.18.0, or
   `verify.baseline: false`): append them as `- [ ]` tasks under `## Verify Fixes` in the
   phase-aware tasklist (kartoteka path: one `artifact_patch(project=<project>, …)` — spec-storage.md §4.3), beneath a new `### checkpoint-r<k>` source heading (k the verify
   round; `### checkpoint-p<N>-r<k>` on a phase-scoped run —
   `${CLAUDE_PLUGIN_ROOT}/docs/task-queue.md` §6); on the queue path (§1 — a `--local` run
   skips it) record them —
   `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/tasklist_tasks.py --tasklist <the phase-aware tasklist> --ticket-key <TICKET_ID>` (kartoteka path: `set -o pipefail; python3 ${CLAUDE_PLUGIN_ROOT}/scripts/spec_store.py get <the phase-aware tasklist> | python3 ${CLAUDE_PLUGIN_ROOT}/scripts/tasklist_tasks.py --tasklist - --ticket-key <TICKET_ID>`)
   (`<TICKET_ID>` the canonical key, without the phase suffix), then `task_create` its
   `data.sections` (§2's fix-writer rule) — and loop `Skill: implementer`, plus `--local`
   when the run holds it, naming `## Verify Fixes`, plus `--model fable` on the second round
   (`MAX_CHECKPOINT_VERIFY_ROUNDS = 2`; each
   round increments `counters.correction_rounds` per autonomous-run.md §5); still red after the
   cap → cap escalation, whose question opens with **Debug it here first** (`## Debug it here
   first`). Exit `2` is an **environment error** — stop-and-ask, never a fix round.
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
