# The tail

Everything a run does once it is armed: the implement loop, the review, the docs stage, the
checkpoints, the completion gate and the PR close-out. Read by
`feature-development` when the run is armed (`${ARTEL_ROOT}/skills/artel-feature-development/SKILL.md`
steps 5–6), and on every resume of an armed run. `SKILL.md` below is that file; its steps 0–1.5
have run, so the config is read, the ticket is active and the spec store is resolved. Contract:
`${ARTEL_ROOT}/docs/autonomous-run.md`. Pass `$0` (on multi-phase traversal the current
`<TICKET_ID>-<N>`) to every sub-skill, and re-read artifacts after every sub-skill/agent return.

Nothing here depends on how the work list was produced. A rule that varies is keyed on what
exists for the ticket — a plan, a PRD, a tasklist's status — never on which path wrote it. Every
`question` tool call below is bracketed by a `pause_reason` set/clear (autonomous-run.md §2).

**Parallel mode is opt-in:** when the host's `parallel.seats` is greater than `1`, this
serial procedure is replaced for the whole armed run by the inline coordinator loop at
`${ARTEL_ROOT}/skills/artel-feature-development/parallel.md` — read it now and follow it.
Its preflight runs before any claim or worktree; a refused configuration or capability
stops parallel mode before effects. `parallel.seats` absent or `1` keeps this serial
procedure exactly as it is.

## Phase traversal

Explicit `<TICKET_ID>-<N>` in `$0` → run exactly that phase (one pass of
the `## Gates` table). Ticket-wide `$0` with a multi-phase `tasklist.md` (Progress Report table / `##
Iteration N` headers) → loop the remaining incomplete phases in order. Each iteration: write
`<TICKET_ID>-<N>` to `<specs.dir>/.active_ticket`; update `run-state.json`'s `ticket` field and
refresh `started_at` (a phase boundary re-arms the wall-clock budget); reset the review round — delete a stale
ticket-wide `review.md` on the files path (it survives in that phase's checkpoint commit), or store
the round-0 version on the kartoteka path (`${ARTEL_ROOT}/docs/spec-storage.md` §4.4; it
survives as an earlier version) — which resets the counter per autonomous-run.md §5; then renew the decision as `SKILL.md` step 1.5 says; run the `artel-sync-phases` skill with
`<TICKET_ID>-<N>` (extract `phase-<N>/tasks.md` when missing); then gates
5–10.7 passing `<TICKET_ID>-<N>` to every sub-skill. Single-phase tasklist → one ticket-wide pass
ending at gate 10.7.

**Re-mirror first.** Per `${ARTEL_ROOT}/docs/task-queue.md` §1 and §2, on the
queue path — and only there, so a `--local` run skips it — run this before the first
gate-5 dispatch of each phase. Files path first, kartoteka path (`docs/spec-storage.md` §4.2) second:

    python3 ${ARTEL_ROOT}/scripts/tasklist_tasks.py --tasklist <specs.dir>/<TICKET_ID>/tasklist.md --ticket-key <TICKET_ID>
    set -o pipefail; python3 ${ARTEL_ROOT}/scripts/spec_store.py get <specs.dir>/<TICKET_ID>/tasklist.md | python3 ${ARTEL_ROOT}/scripts/tasklist_tasks.py --tasklist - --ticket-key <TICKET_ID>

and `task_create` the rows it emits — `data.iterations`, then `data.sections` (§2 steps
2–4, surfacing every `data.warnings` line). A tasklist is mirrored by the skill that writes it,
and only when it writes it, so without this step a resumed run — or a
ticket whose tasklist was written before the adapter was reachable — never mirrors at
all, and every implementer dispatch falls back to the file. For a task-format tasklist this
step is also the first mirror when the `artel-tasklist` skill wrote it: that skill mirrors nothing
before `PLAN_APPROVED` (`${ARTEL_ROOT}/docs/task-queue.md` §2). The step is create-only and
idempotent: it never resets a `done` row and never undoes a promotion. Exit `2` → report
`error.kind` and `error.message` and continue on the fallback path. On phase runs it
lands after this section's `artel-sync-phases` skill, which is what keeps `tasklist.md`
current when it is read.

## Gates

| # | Gate | Action |
|---|------|--------|
| 5 | `IMPLEMENT_STEP_OK` — every task `- [x]` | Loop the `artel-implementer` skill with `$0`, plus `--local` when this run was invoked with it — it becomes the dispatch's **Task queue:** field, which is what carries the opt-out to the agent (`${ARTEL_ROOT}/docs/task-queue.md` §1). Returns: **completion** → aggregate its `Deviations:`/`Verify iterations:` lines, add every path its `Deviations:` line names to `run-state.json` `deviation_files` (autonomous-run.md §2), and journal its `Report:` path (never open the report — autonomous-run.md §1, "Bulk stays in files"), continue. **Commit the cycle** when it is done — after its per-task review round, when the run takes one: stage and commit it per `## Task commits` before the next dispatch. **Boundary check** first: `git status --porcelain` over that cycle's paths not empty → run the procedure again and journal `task-commit: recovered <n> file(s) at the boundary`; whatever the never-staged set forbids stays dirty, journaled `task-commit: left <n> unstaged file(s)`. **Routes** (autonomous-run.md §16) → on a task-format tasklist run `scripts/review_package.py snapshot` before every iteration-task dispatch; when the completion names its task (`Task <N.M>: …`), take that task's route from the parser's row (the re-mirror's run; on a `--local` run, the same command run for the routes alone): `route_effective` — a `Route:` ending `— set at approval` is final over floors 1–3 — raised to `full` when one of its `Files:` is in `deviation_files` or `review.perTask` is `true`; journal `task <N.M>: route <effective> (declared <route>[; floor: <reason>[, <reason>…]]); model <value>`. A `full` task gets the §16.2 wrapper: `diff` + invoke the `artel-run-reviewer` skill with `--task …` and `--model <value>` — resolve `reviewer.task` (`python3 ${ARTEL_ROOT}/scripts/models.py resolve --site reviewer.task`); no flag when null — (plus `--local` when this run was invoked with it) after its completion, at most one `## Code Review Fixes` implementer round (`MAX_TASK_REVIEW_ROUNDS = 1`, counted toward `counters.correction_rounds`), one `task review` journal entry (`; model <value>` — `none` when no flag was passed); a `light` task gets none. Fix-list dispatches are never wrapped. **`HITL: <reason>`** → set `pause_reason: "hitl-task"`, ask the pre-declared question via the `question` tool, clear `pause_reason`, dispatch a fresh `subagent` to that agent with the answer, continue. **`DEVIATION` escalation** → the skill handles the handshake; wrap it: set `pause_reason: "deviation-escalation"` before its `question` tool call, clear after. **Aborted task** → set `pause_reason: "cap-escalation"`, stop and report that the plan needs revision; the question opens with **Debug it here first** (below the table). |
| 6 | `INDEX_UPDATED` | Optional host index-refresh hook (`${ARTEL_ROOT}/docs/orchestrator-common.md` §1): run it when the host has wired one up; silently absent otherwise. |
| 7 | `REVIEW_OK` | invoke the `artel-run-reviewer` skill with `$0`, plus `--local` when this run was invoked with it — `run-reviewer` records the fix tasks it appends in the task queue, and the flag keeps a local-only run from writing rows (`${ARTEL_ROOT}/docs/task-queue.md` §1, §6). Blocking/Important findings → invoke the `artel-implementer` skill (fix tasks from `## Code Review Fixes`, plus `--local` when this run was invoked with it, plus `--model <value>` when the findings come from a `review.md` whose `**Review round:**` is 2 or more — resolve `implementer.stepUp` (`python3 ${ARTEL_ROOT}/scripts/models.py resolve --site implementer.stepUp`); no flag when null, and the implementer's `implementer.fix` applies — autonomous-run.md §5) → re-review (the re-review passes `--model <value>` — resolve `reviewer.reReview` (`python3 ${ARTEL_ROOT}/scripts/models.py resolve --site reviewer.reReview`); no flag when null, and ticket mode's `reviewer.phase` applies — and journals the round with `; model <value>`, `none` when no flag was passed). Every fix dispatch commits its fix task per `## Task commits`. Cap: `review.md` `**Review round:**` reaching `MAX_REVIEW_ROUNDS = 3` → `pause_reason: "cap-escalation"`, consolidated findings via the `question` tool, stop. On user guidance: reset the review round (delete `review.md`, or on the kartoteka path store the round-0 version — spec-storage.md §4.4) and resume. |
| 10 | `DOCS_UPDATED` | Runs only when a PRD exists for the ticket, at either scope — `spec_store.py exists` over the ticket-wide PRD and over the phase-aware one (`${ARTEL_ROOT}/docs/ticket-parsing.md` §4; exit `3` is "none"). Without one, journal `DOCS_UPDATED: skipped (no PRD)` and go on: there is no spec to write docs from. With one: invoke the `artel-docs-update` skill with `<TICKET_ID>` (ticket-wide) — **once per ticket, on the last phase only, before its 10.7 checkpoint** — so that checkpoint commit carries the docs and the CHANGELOG — never per phase. On a multi-phase traversal it is skipped for every phase but the last; an explicit `<TICKET_ID>-<N>` run of a phase that is not the last journals `DOCS_UPDATED: deferred to the final phase`. |
| 10.5 | phase write-back (phase runs only) | invoke the `artel-sync-phases` skill with `$0` — sync completion into `tasklist.md`. |
| 10.7 | `PHASE_CHECKPOINT` | Run the phase-end checkpoint (see `## Checkpoint commits & pushes`): the image sweep (kartoteka path) → the `verify.commands` gate → capped `## Verify Fixes` implementer rounds → explicit staging (no trail image on the kartoteka path) → commit (`feat\|fix\|refactor: <TICKET_ID> phase <N> - <phase title>`; no phase → `<ticket summary>`) → push → journal (on the last phase this commit also carries gate 10's docs). Then advance `.active_ticket` to the next incomplete phase; on a multi-phase run loop back to the traversal (next phase), else proceed to the completion gate. |

**Journal (§11):** append an entry to `run-journal.md` at every gate completion, pause/resume,
and external action.
**Budget:** before dispatching any fix-list implementer round (review gate 7),
increment `counters.correction_rounds`; at `MAX_TOTAL_CORRECTION_ROUNDS = 8` → journal
the breach, `pause_reason: "cap-escalation"`, consolidated report via the `question` tool, stop.

**App automation is not a gate.** Artel ships no launcher and no UI driver. When a run needs the
app built, launched or driven — to observe a change, not to gate the run — use the automation
skill and commands the host project provides (its own skill directories, `AGENTS.md` and project
commands) and follow that skill's evidence contract. Nothing in this tail invokes, records or
gates on it.

## Debug it here first

Two halts of this file are a red gate in app code, and on each the escalation's
`question` tool call opens with one more option, **Debug it here first**:

- gate 5 — a task aborted when `MAX_VERIFY_ITERATIONS` ran out;
- the checkpoint procedure's step 3 — still red after `MAX_CHECKPOINT_VERIFY_ROUNDS`.

Nowhere else: not the review cap (findings, answered by guidance), not an environment error,
not `MAX_TOTAL_CORRECTION_ROUNDS`, not the wall clock. A headless run journals and stops as
before — it has no question to add the option to.

Chosen → invoke the `artel-debugging` skill with the red evidence as its argument: the failing stage, its first
error lines, the path of the task's report. `pause_reason` stays `"cap-escalation"` throughout.
The skill never commits and never writes run state or the journal; you do, from what it
reports:

| The skill reports | Then |
|---|---|
| fixed | Journal `debugged here: <root cause> — <files>`, add those files to `run-state.json` `deviation_files`, and re-run the gate that was red. Green → reset that loop's counter as a resume with guidance does, clear `pause_reason` and continue — for an aborted task, by dispatching the implementer on it again — the next cycle commits the fix. |
| not fixed, or the gate is red again after its fix | Ask the escalation question again, without this option: `MAX_DEBUG_HERE_ATTEMPTS = 1`, one attempt per halt. |
| structural (`${ARTEL_ROOT}/docs/debugging.md` §4) | The skill's own ticket question runs; the run stays paused for guidance or an abort. |

## Completion gate

Runs once, after the last phase on multi-phase runs. Confirm the six facts below yourself — they
are artifacts you already read — and journal one line per fact (autonomous-run.md §7). No agent is
dispatched here: `/artel-validate` stays à la carte.

| Fact | How it is read |
|---|---|
| `PLAN_APPROVED` | the plan's status (the same `spec_store.py status` read the gates use); no plan exists for the ticket → `skipped (no plan)` — `gates_confirmed` holding `TASKLIST_READY` is the approval; a plan this run did not approve — its status is not `PLAN_APPROVED` while `gates_confirmed` holds `TASKLIST_READY`, which is a draft left by an earlier invocation or written à la carte on a run whose work list was what the person confirmed → `skipped (plan not approved by this run)` |
| `TASKLIST_READY` | the tasklist's status (`spec_store.py status`); a tasklist that declares none (written by `generate-tasklist` before 0.25.0 — `status` exits `1`) is read from `gates_confirmed` in `run-state.json` |
| `IMPLEMENT_STEP_OK` | `tasklist_tasks.py` over the tasklist (and each `phase-<N>/tasks.md` on phased tickets) reports no unchecked box in any iteration or fix section — a `## Final Verification` section an older tasklist carries counts too; and, on the queue path, every fix-section parent row is closed (`${ARTEL_ROOT}/docs/task-queue.md` §6) |
| `REVIEW_OK` | `review.md` exists with a `**Review round:**` line and the tasklist has no unchecked `## Code Review Fixes` box; a `**Verdict:**` line, when the reviewer wrote one, must not read *Needs fixes* |
| `CHECKPOINT_OK` — the final gate | the last checkpoint journal entry is green, and `git diff --name-only <its commit>..HEAD` plus the working tree, filtered by `verify.surface` (absent → every changed file counts), is empty; otherwise run one more checkpoint gate and commit it as a checkpoint (`## Checkpoint commits & pushes`) |
| `DOCS_UPDATED` | `summary.md` exists (`spec_store.py list <TICKET_ID>` on the kartoteka path); no PRD exists for the ticket → `skipped (no PRD)` |

A red fact routes back to its owning step once — the implementer for boxes, `run-reviewer` for
the review, the checkpoint procedure for the gate, `docs-update` for
docs (followed by one more checkpoint commit, so they are not left uncommitted) — then
cap-escalates. A fact that reads `skipped` is green. Never set `completed` while a fact is red. All green → the PR description and the PR gate.

## PR description and the PR gate

invoke the `artel-pr-description` skill with `$0` — writes `<specs.dir>/<TICKET_ID>/pr-description.md` covering
the whole branch (see `${ARTEL_ROOT}/skills/artel-pr-description/SKILL.md`). **Always
regenerate:** an existing file is overwritten, never skipped — the branch diff is the input, so
skip-if-exists (autonomous-run.md §9) does not apply. The skill is prompt-free; tracker/VCS fetch
failures degrade to its fallback template and are never a gate failure — do not loop or escalate
on them.

**PR gate:** in `plan-gate`, pause first — set `pause_reason: "hitl-task"`, ask with the `question` tool
("Open the PR now? commit+push + PR via `vcs.adapter` + tracker comment via `tracker.adapter`" /
"Skip — I'll do it manually"), clear `pause_reason`. In `yolo`, proceed without pausing (roadmap:
the opened PR is yolo's human checkpoint). On approve/yolo: invoke the `artel-pr-create` skill with `$0`. Its
degradation stop (pr-pending.md) or a decline is **not** a gate failure — record the choice,
continue to close-out; never loop.

Then update `run-state.json`: `completed: true`, `run_active: false`. Append the completion entry
to the journal.

**Serial path only — release the ticket lock** (worktrees.md §9). When gate 5 ran this tail's
serial procedure — not `parallel.md` — release now, once the run state above says `completed:
true` and no pause is recorded: `python3 ${ARTEL_ROOT}/scripts/parallel.py owner release
--request <a request file>` with `{repo, ticket, owner: {schema, ticket, run_id, phase,
coordinator_id, token, completed: true, quiescent: true}}`, taking `run_id`, `phase`,
`coordinator_id` and `token` from `run-state.json`'s `run_id` and `ownership`. A refusal keeps
the lock and is journaled, never retried with invented bytes: `premature_release` means a gate
or this completion update is not done, `foreign_owner` means the run no longer owns the ticket.
The parallel procedure releases at its own finish (`parallel.md` §8.3) — never twice, never
from the other path.

## Description-file sync

Per `${ARTEL_ROOT}/docs/orchestrator-common.md` §4 (only when `$1` is a description file
with checkbox tasks).

## Final report

On the kartoteka path, sweep once more before writing it:
`python3 ${ARTEL_ROOT}/scripts/spec_store.py image sync <TICKET_ID> --author artel:feature-development`
(`${ARTEL_ROOT}/docs/spec-storage.md` §5.6). A failure never pauses: the report lists what
it left local instead.

Ticket; phases traversed; gates passed; aggregated `Deviations:` line (`none` when clean); loop
counters (verify iterations total, review rounds, escalation count); baseline (`recorded` / `skipped` / `absent` — armed before 0.18.0); final gate (the last checkpoint's commit stands, or `re-run`);
checkpoint commits (hash + subject each, incl. push results); task commits (count and range — `task commits: <n> (<first>..<last>)`, the per-commit hashes stay in the journal); path to `pr-description.md`; PR
status (`PR_OPENED`/`PR_EXISTS` URL, `skipped-manual`, or `pending`); description-sync status;
reminder that opening the PR remains manual (only when the PR gate was skipped — the work itself
is already committed and pushed by the checkpoints); effective mode + why (`mode_reasons`);
external actions taken unattended; spec store (`kartoteka`, or `files (<reason>)` with the documents left on disk and `/artel-migrate-specs <TICKET_ID>` — spec-storage.md §5.5); images left local on the kartoteka path — each `failed` or `skipped` entry of that sweep with its reason, or on exit `5` or `2` every image still under the trail; an `unrecoverable` sweep's whole message — with the `image sync` command above to move them in (headless runs journal the same lines); path to `run-journal.md`.

## Checkpoint commits & pushes

This is the phase-level commit and push procedure of a run. Checkpoints keep the branch on `origin` carrying the
latest approved docs and every completed phase. They are orchestrator-owned `shell` actions
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
   `python3 ${ARTEL_ROOT}/scripts/spec_store.py image sync <TICKET_ID> --author artel:feature-development`
   (`${ARTEL_ROOT}/docs/spec-storage.md` §4.6). It moves every untracked image under the
   trail into kartoteka. Exit `5`, or `failed` entries, never pause: journal
   `image-sync: <n> left local — <first error line>` (spec-storage.md §5.6) and go on; those
   images stay untracked, and the next sweep point retries them. Exit `2` with kind
   `unrecoverable` never pauses either, but journal its whole message, not a first line, and
   repeat it in the final report (spec-storage.md §5.6). Any other non-zero exit: as exit `5` —
   never a `STORE_UNAVAILABLE` pause. Then: `git status --porcelain`
   clean → skip the commit (resume-safe); still push when the local branch is ahead of `origin`.
3. **Quality gate (phase-end only).** Run the checkpoint gate (`${ARTEL_ROOT}/docs/gates.md`
   §1): `python3 ${ARTEL_ROOT}/scripts/verify.py checkpoint --ticket <TICKET_ID>`. Exit `0`
   → green: journal any `baseline_red` stage by name; an empty `verify.commands` ⇒ the envelope
   says `skipped` — record the verify step as `skipped` in the journal entry and continue to
   staging. Exit `1` → the findings are the `new_keys` of the red stage
   (its `keys` when `data.baseline` is `absent` or `disabled` — a run armed before 0.18.0, or
   `verify.baseline: false`): append them as `- [ ]` tasks under `## Verify Fixes` in the
   phase-aware tasklist (kartoteka path: one `artifact_patch(project=<project>, …)` — spec-storage.md §4.3), beneath a new `### checkpoint-r<k>` source heading (k the verify
   round; `### checkpoint-p<N>-r<k>` on a phase-scoped run —
   `${ARTEL_ROOT}/docs/task-queue.md` §6); on the queue path (§1 — a `--local` run
   skips it) record them —
   `python3 ${ARTEL_ROOT}/scripts/tasklist_tasks.py --tasklist <the phase-aware tasklist> --ticket-key <TICKET_ID>` (kartoteka path: `set -o pipefail; python3 ${ARTEL_ROOT}/scripts/spec_store.py get <the phase-aware tasklist> | python3 ${ARTEL_ROOT}/scripts/tasklist_tasks.py --tasklist - --ticket-key <TICKET_ID>`)
   (`<TICKET_ID>` the canonical key, without the phase suffix), then `task_create` its
   `data.sections` (§2's fix-writer rule) — and loop the `artel-implementer` skill, plus `--local`
   when the run holds it, naming `## Verify Fixes`, plus `--model <value>` on the second round — resolve `implementer.stepUp` (`python3 ${ARTEL_ROOT}/scripts/models.py resolve --site implementer.stepUp`); no flag when null
   (`MAX_CHECKPOINT_VERIFY_ROUNDS = 2`; each
   round increments `counters.correction_rounds` per autonomous-run.md §5; each round's fix task
   commits per `## Task commits`); still red after the
   cap → cap escalation, whose question opens with **Debug it here first** (`## Debug it here
   first`). Exit `2` is an **environment error** — stop-and-ask, never a fix round.
4. **Stage explicitly.** The ticket's changed files, `<specs.dir>/<TICKET_ID>/**`, and
   `<specs.dir>/.active_ticket`. Never `git add -A`; never generated files; never `.artel/**`.
   On the kartoteka path no image under the trail is ever staged: add one exclude per image
   extension (`${ARTEL_ROOT}/docs/spec-storage.md` §4.6), and leave
   `'<specs.dir>/<TICKET_ID>'` out when that folder neither exists nor has tracked files —
   `git add` refuses a pathspec that matches nothing:

       git add -- <the ticket's changed files> '<specs.dir>/<TICKET_ID>' '<specs.dir>/.active_ticket' ':(exclude,icase,glob)<specs.dir>/<TICKET_ID>/**/*.png' ':(exclude,icase,glob)<specs.dir>/<TICKET_ID>/**/*.jpg' ':(exclude,icase,glob)<specs.dir>/<TICKET_ID>/**/*.jpeg' ':(exclude,icase,glob)<specs.dir>/<TICKET_ID>/**/*.gif' ':(exclude,icase,glob)<specs.dir>/<TICKET_ID>/**/*.webp'

   **Isolation.** `git commit` commits the whole index, so the index must carry no path outside
   this set when you commit: `git diff --cached --name-only` listing one → stop-and-ask
   (environment error). A person's pre-staged work is never swept into a run commit, never
   unstaged, and never committed by the run.
5. **Commit.** Nothing staged (`git diff --cached --quiet` exits 0 — on the kartoteka path, the
   only changes were images a failed sweep left untracked) → skip the commit and go on to the
   push. Otherwise: conventional message, always English, subject line only, no trailers.
6. **Push.** Only when step 3's verdict was green or `skipped`: a red gate never reaches a push,
   and a host with no `verify.commands` still pushes — the verdict is `skipped`, and the branch
   would otherwise never reach `origin`, which is what checkpoints are for. The arm-time planning
   checkpoint above is the one exception: docs only, no code, nothing to build.
   `git push -u origin <branch>`. Rejected non-fast-forward → stop-and-ask (the user
   reconciles; force-push is never an option). A completed task's commits may have waited here
   since its cycle — that is the accepted cost of pushing only what a build gate has passed.
7. **Journal.** Checkpoint entry: commit hash, subject, the verify verdict beside the push result, verify fix rounds.

`pr-create` remains the PR-opening close-out; after phase checkpoints its own commit step usually
finds a clean tree and skips — it is idempotent by design.

## Task commits

The rule: one commit per completed implementer cycle. The cycle — not the tasklist box — is the
completion unit, because the tick and the queue update are written by the same return: the work is
committed exactly when the run learns it is done. The section above is the phase-level procedure;
this is the second of the run's two commit kinds.

| Cycle | Commit |
|---|---|
| An iteration task (gate 5) — after its tick, and after a `full` task's §16.2 per-task review round when that ran | one |
| A fix task — gate 7's `## Code Review Fixes`, checkpoint step 3's `## Verify Fixes` — one per dispatch: the implementer closes one fix row per return | one |
| A cycle that changed nothing — verification only, or a resumed re-run | none |
| A `Routes` return, or a HITL pause before the person's answer | none — the task is not complete |

A fix round is a loop of dispatches, one fix row each (`autonomous-run.md` §16.2): every return is a
completed cycle of its own, so a round of five fix tasks is five commits, one per dispatch.

**Contents.** The cycle's changed files, plus the tasklist tick (on a phase run, the phase-aware
tasklist). The file set comes from the working tree, never from a report — you never open one
(`autonomous-run.md` §1, "Bulk stays in files"): the task's declared `Files:` from the parser's row,
plus every other path the cycle left dirty. A fix row declares no `Files:`, so the dirty set alone
is its set — exactly as the checkpoint procedure's step 4 reads a phase's changed files. Staging is
`the checkpoint procedure's rule 4` by reference: never `git add -A`, never `.artel/**`, never
generated files, the kartoteka path's per-image-extension excludes, and the guard for a pathspec
that matches nothing. A task commit runs no image sweep and no verify gate — it is not a gate.

**Subject.** `<type>: <TICKET_ID> - <task title>` — conventional, English, subject line only, no
trailers, and neither a requirement ID nor a task number. The type is what the cycle did: `feat` for
an iteration task that adds behaviour, `fix` for a fix round, `refactor`, `test`, `docs` or `chore`
when that is what it did. A fix round names its section: `fix: <TICKET_ID> - review fixes`,
`- checkpoint verify fixes`.

**It never pushes.** A task commit reaches `origin` at `the next phase-end checkpoint`, or at
`pr-create`'s close-out — never on its own.

**Idempotence.** Stage, then `git diff --cached --quiet` exits 0 → skip the commit and write no journal entry,
so a resumed run cannot commit the same cycle twice.
Rule 1's branch guard applies: a task commit is never made on the default branch — that is a stop-and-ask.

**On resume**, before the first dispatch of a phase, run this procedure once over what the tree
holds: a cycle that completed but was never committed — a session that died between the tick and the commit —
lands as its own commit, its subject the last ticked task's, or the `wip:` subject
when the ticked task cannot be told apart. Nothing staged → skip.

**Halt.** Every path that hands the session back with an unresolved in-flight cycle commits what
that cycle left, as `wip: <TICKET_ID> - <task> (run stopped: <reason>)`, journaled
`task-commit: wip <hash> (<reason>)`, and does not push it: gate 5's `MAX_VERIFY_ITERATIONS`
exhausted, gate 7's review cap, `MAX_TOTAL_CORRECTION_ROUNDS = 8`, checkpoint
step 3's verify cap, a deviation escalation, a stop-and-ask, or the person stopping the run. When the
tree is clean — nothing dirty — there is nothing to commit, and the escalation proceeds unchanged.
Two cases commit nothing, and neither recurses: the branch guard (a task commit is never made on
the default branch), and a commit the host refuses — a pre-commit hook, a missing identity, an
index lock: journal `task-commit: failed (<first error line>)`, leave the tree and the index
exactly as they are, stop-and-ask, and never attempt another commit there — never `--no-verify`,
never `--force`.

**Journal.** One entry per commit — hash, subject, cycle — as a checkpoint's is.
