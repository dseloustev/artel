---
name: feature-development
description: "Ticket in, pull request out: sizes the work and runs the head that fits — the full pipeline (interview -> PRD -> vision -> plan -> tasks), a lean work list, a bug diagnosis or a spike answer -> ONE approval pause -> autonomous implementation, review, runtime check, PR"
argument-hint: "[ticket-id] or [ticket-id]-[phase] [description-file] [--head=full|lean|bug] [--mode=yolo|plan-gate|full-gates] [--dry-run] [--local]"
---

The one orchestrator for ticket work: it imports the ticket, sizes the work, runs the head that
fits it to one approval, then runs the tail to a pull request. Contract:
`${CLAUDE_PLUGIN_ROOT}/docs/autonomous-run.md` (read it first). Shared procedures:
`${CLAUDE_PLUGIN_ROOT}/docs/orchestrator-common.md`. Pass `$0` (including phase; on
multi-phase traversal the current `<TICKET_ID>-<N>`) to every sub-skill. Skip any gate whose
artifact already exists (resume); re-read artifacts after every sub-skill/agent return.

This file is the shared start. The rest of the skill is four files beside it, each read when the
run reaches it — never all at once:

| File | Holds | Read |
|---|---|---|
| `${CLAUDE_PLUGIN_ROOT}/skills/feature-development/heads/full.md` | gates 0.5–4.5, gate 4.2 and THE ONE PAUSE | step 4, for an `architectural` size — and when a head raises the size |
| `${CLAUDE_PLUGIN_ROOT}/skills/feature-development/heads/lean.md` | the ladder and the work-list confirmation | step 4, for a `bounded` size |
| `${CLAUDE_PLUGIN_ROOT}/skills/feature-development/heads/bug.md` | the diagnosis, then the work list | step 4, for a `bug` size |
| `${CLAUDE_PLUGIN_ROOT}/skills/feature-development/tail.md` | phase traversal, gates 5–10.7, the completion gate, the PR close-out, the final report, the checkpoint procedure | step 5, once the run is armed — inline runs only; a seated run never reads it here (it is the seat's procedure) |

Read exactly one head file per run, after sizing, and another only when a head hands the ticket
on (a raise, or the bug head's **Treat it as a bounded change**). On resume of an armed run, an
inline run reads `tail.md` and no head file; a seated run dispatches a fresh seat (step 6).

`--step` flag: run in legacy step-by-step mode — confirm between major phases via
`AskUserQuestion`, skip all `run-state.json` writes (autonomous-run.md §6). The remainder of this
file describes the default autonomous mode. `--mode=full-gates` is an alias for `--step`.
`--mode=yolo|plan-gate` selects the autonomous mode per `autonomous-run.md` §10 (default:
`plan-gate`); the classifier may raise it, never lower it. `--head=full|lean|bug` sets the size
(step 3). `--dry-run`: run steps 0–4 — the head to the end of its approval — then print the
resolved mode + reasons and the intended external actions, and stop — write no `run-state.json`,
never arm. Every flag applies to every head.
`--local`: skip the institutional-knowledge
consultation and the task queue for this whole run and pass the flag down to every sub-skill
that accepts it (`analysis`, `researcher`, `tasklist`, `generate-tasklist`, `debugging`,
`run-reviewer` and `implementer`).
Every implementer dispatch carries it, fix rounds included (gates 7, 8 and 10.7, and the
per-task review's round): its **Task queue:** field is the only way the opt-out reaches the
agent (`${CLAUDE_PLUGIN_ROOT}/docs/task-queue.md` §1). Default is to consult;
`${CLAUDE_PLUGIN_ROOT}/docs/knowledge-consultation.md` §1 resolves it against
`knowledge.adapter` and tool availability.

## Workflow

Steps 0–1.5 run on every invocation. Then look at `.artel/run/<TICKET_ID>/run-state.json`.

**An armed run resumes past the head.** `run_active: true` with `gates_confirmed` holding
`TASKLIST_READY` means the head is done. Skip steps 2–4: re-derive the mode fields and re-arm as
step 5 says for a resume, then run the tail (step 6), whose gates skip what is already done. Do
not size, do not read a head file, and do not present an approval again. A `--head` flag on such
a run changes nothing; answer it with the one line `--head ignored: the run is past its head.`
— but only when `sizing.json` records a different head. A flag that agrees with the record, or
a run with no `sizing.json`, gets no line.
A run armed before 0.25.0 has no `sizing.json` and needs none.

Every other run — new, or interrupted inside a head — goes on to step 2.

### 0. Config gate

Read `.artel/config.json` per `${CLAUDE_PLUGIN_ROOT}/docs/config.md`. Missing → `Skill: setup`
(the one-time init interview), then continue with the written config. Present → run the
start-time check config.md's "When the adapter is unusable" prescribes for entry points:
`vcs.adapter: "bitbucket-mcp"` with an empty `vcs.mcpToolPrefix` is a configuration error —
report it and stop before the pipeline starts rather than failing hours later at the PR stage.

### 1. Set active ticket

Write `$0` to `<specs.dir>/.active_ticket`; ensure `<specs.dir>/<TICKET_ID>/` exists. The pointer
is kept phase-accurate for the whole run: on multi-phase traversal (`tail.md`, `## Phase
traversal`) it is rewritten to
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

### 2. Import the ticket

On the kartoteka path "artifact exists" in every gate of this skill — the one below and every gate of a head file — is one `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/spec_store.py list <TICKET_ID>`, re-run after each sub-skill returns, and a gate's status is read without the document entering this context: `doc=$(spec_store.py get <path>) && printf '%s\n' "$doc" | spec_store.py status` (files path: `spec_store.py status < <path>`). `status` prints the header's `status:` (or, for a document written before 0.21.0, its `Status:` line's value) and exits `1` when none is declared (spec-storage.md §3.2, §8).

| # | Gate | Action (skip if artifact exists) |
|---|------|----------------------------------|
| 0 | `IDEA_READY` — `idea.md` exists | `Skill: generate-idea` with `$0 $1`, under **every** adapter — it branches on `tracker.adapter` itself (config.md): a tracker imports the ticket; `"none"` (local-only) seeds `idea.md` from the `$1` description file, or runs the same input gate `analysis` does when `$1` is absent. Every run imports: step 3 sizes the work from `idea.md` and every head starts from it, so it is seeded here rather than letting a `"none"` run die at the first gate that needs it. |

### 3. Size the work

Decide which head the ticket gets, record it, say it, and go on — never a pause. Sizing is your
own procedure, like the mode classifier: no agent is dispatched. Its contract is
`${CLAUDE_PLUGIN_ROOT}/docs/autonomous-run.md` §17 (`## 17. Sizing and heads`); the rules are
stated here in full because this is the file you act from.

**The four sizes.**

| Size | Head | It fits when |
|---|---|---|
| `spike` | none — "The spike outcome" below | the ticket asks a question — can we, is it feasible, which of these — and names no change to ship |
| `bug` | `bug` — `heads/bug.md` | the tracker's issue type is Bug (the metadata block of `idea.md`), or the text describes behaviour that differs from what it should be: expected against actual, a regression, an error |
| `bounded` | `lean` — `heads/lean.md` | it changes a flow that already exists in the repo, in one area, with acceptance that can be stated in a few lines and no open product question |
| `architectural` | `full` — `heads/full.md` | a new flow, screen or subsystem; an interface other code depends on; a data-model change or migration; a Figma design to analyse (`design.figma` on and a `figma.com/design` link in `idea.md`); work that needs several phases; or acceptance that is unclear |

**Order of decision — the first rule that matches wins.**

1. **A flag.** `--head=full` → `architectural`, `--head=lean` → `bounded`, `--head=bug` → `bug`.
   The person's flag is the only thing that lowers a size.
2. **A recorded sizing** — `.artel/run/<TICKET_ID>/sizing.json` exists: its `size` stands, so a
   run interrupted inside a head resumes on the same head. One exception: a spike already
   answered (`size` `spike`, `answered: true`) is not a recorded sizing — go on to rule 3.
3. **What exists** for the ticket. A PRD or a plan, at either scope (ticket-wide or a phase's) →
   `architectural`. Else `diagnosis.md` → `bug`. Else a tasklist with open tasks, or a
   `vision.md` → `bounded`. An open task is an unticked `- [ ]` box: `grep -q -- '- \[ \]'` over
   the tasklist (kartoteka path: `doc=$(spec_store.py get <tasklist path>) && printf '%s\n' "$doc" | grep -q -- '- \[ \]'`).
4. **Judgement** over `idea.md`, by the table above. Read that one document; you may check that
   the paths it names exist; explore nothing else. In doubt between two sizes take the heavier:
   `spike` < `bounded` < `architectural`. `bug` wins any doubt it is part of — its head
   diagnoses first, and can raise itself or hand the ticket to the lean head.
   Read it to its end: a later section — a revision, a narrowed scope — overrides an earlier
   one. Every reason you record must be true of the ticket as it stands: the size is said
   aloud so the person can catch a wrong reason, and one the text contradicts defeats that.

**Record it, then say it.** Before any head, write `.artel/run/<TICKET_ID>/sizing.json` — in
`--step` runs too: it is not run state. When rule 2 found the file, leave it as it is. The keys,
exactly:

    {
      "size": "bounded",
      "head": "lean",
      "reasons": ["changes one existing screen", "acceptance is two lines"],
      "decided_by": "judgement",
      "raised_from": null,
      "answered": false,
      "decided_at": "2026-09-30T12:00:00Z"
    }

`size` is one of the four; `head` is `none`, `bug`, `lean` or `full`; `reasons` is a list of
short strings; `decided_by` is `flag` (rule 1), `existing` (rule 3), `judgement` (rule 4) or
`raised` (the ratchet); `raised_from` holds the earlier size when a head raised it, else `null`;
`answered` is used by a spike only; `decided_at` is UTC ISO-8601.

Then print one line, before the head starts:

    Size: <size> — <head> head. Reasons: <reason>; <reason>. To change: say so now, or re-run with --head=<full|lean|bug>.

For a spike the line opens `Size: spike — no head; the researcher answers the question.` and
goes on with the same `Reasons:` and `To change:` parts. It is an announcement, not a question:
go straight on. If the person does answer it — there, or at the head's first question or its
approval — by asking for another head, honour that as the flag: rewrite `sizing.json`
(`decided_by` `flag`) and read that head's file. Headless, the line goes to the output; the
run-start journal entry repeats the size either way (step 5).

**The ratchet.** A head may raise the size, never lower it: the lean head when
`generate-tasklist` returns the writer's `RAISE`, the bug head when the diagnosis is
`DIAGNOSED_STRUCTURAL`. To raise: rewrite `sizing.json` — `size` `architectural`, `head` `full`,
`decided_by` `raised`, `raised_from` the earlier size, `reasons` the raise's — print

    Size raised: <from> → architectural — full head. Reason: <reason>.

and read `heads/full.md`. The ratchet stops at arming: once the run is armed, hidden complexity
is a `DEVIATION` or an aborted task, and the tail handles it.

**The spike outcome.** A `spike` reads no head file. Run `Skill: researcher` with
`$0 --question`, plus `--local` when this run was invoked with it. Its question mode needs no
PRD — the question it answers is the ticket's — and it writes
`<specs.dir>/<TICKET_ID>/spike.md`, answer first. Its last line is
`Spike answered: <one-line answer> — <path>`: print it, set `answered: true` in `sizing.json`,
and stop. Nothing is armed, journaled or committed, and steps 4–6 do not run.

A later run on the same ticket is sized afresh from rule 3. If it comes out `spike` again, do
not dispatch the researcher a second time: print

    Already answered: <path to spike.md>. To build on it, re-run with --head=lean or --head=full.

and stop.

### 4. The head

Read the one file the size names and run it to the end of its approval:

| Size | Read |
|---|---|
| `architectural` | `${CLAUDE_PLUGIN_ROOT}/skills/feature-development/heads/full.md` |
| `bounded` | `${CLAUDE_PLUGIN_ROOT}/skills/feature-development/heads/lean.md` |
| `bug` | `${CLAUDE_PLUGIN_ROOT}/skills/feature-development/heads/bug.md` |

A `spike` never reaches this step. The head is the chatty part of the run: nothing is armed yet,
so its questions are plain `AskUserQuestion` calls with no `pause_reason`. It ends in one of
three ways:

- **its approval** — the full head's pause, the lean head's confirmation, or the approval round
  of the work list the bug head had written → step 5;
- **a raise** (step 3, "The ratchet") → read `heads/full.md` and run it. The lean head is read
  after the bug head only when the person chooses **Treat it as a bounded change** there;
- **a stop the head names itself** — an abort, a parked design, a diagnosis that did not
  reproduce on a headless run, a work list that is already finished. The run ends unarmed, and re-invoking resumes on the recorded
  size.

### 5. Arm the run

Run the risk classifier (autonomous-run.md §10) over what the head produced: plan + tasklist,
or the confirmed work list with `idea.md` and `vision.md` when present. `forced_floor:
"full-gates"` ⇒ stop here: report the matched sensitive categories and instruct the user to
re-run with `--step`. `--dry-run` ⇒ stop here too: print the resolved mode, its reasons and the
intended external actions — the checkpoint commits & pushes, and the pull request the run ends
with (opened without asking in `yolo`) — and write nothing. Otherwise write
`.artel/run/<TICKET_ID>/run-state.json` per
autonomous-run.md §2: `run_active: true`, `completed: false`, `pause_reason: null`, fresh
`started_at`, zeroed counters, `requested_local` (§2 — the `--local` opt-out, recorded because
a resumed run has no argument list left to read it from), `deviation_files: []` (§2), plus the
six mode fields (§10), with
`gates_confirmed: ["TASKLIST_READY"]`. Announce the effective mode and reasons, the same
external actions, and every task's effective route (§16.1). On resume,
re-derive the mode fields before re-arming — never trust stale ones, and carry `requested_local`
and `deviation_files` forward unchanged: the first is the user's opt-out, the second the run's
record of deviations, and neither is a classifier output. From here the run is
silent except deviations, HITL tasks, and cap escalations. Create
`.artel/run/<TICKET_ID>/run-journal.md` with the run-start entry (autonomous-run.md §11): mode
resolution, reasons, HITL tags count, every route changed at the approval, the size —
`- size: <size> (<head> head; decided by <decided_by>)`, from `sizing.json`; a run armed before
0.25.0 has none and resumes without the line — and the plan check's outcome. When gate 4.2 ran,
that is the plan review's outcome —
`PLAN_REVIEWED: <k> round(s), <n> finding(s) left open` with the Minor ones listed. Otherwise
it is the parser check's:
`plan check: <c> Critical, <i> Important, <m> Minor` or `plan check: not run (<error.kind>)`
from the lean head's own check, and
`plan check: run by generate-tasklist` when that skill wrote and checked the work list.

Then run the **planning checkpoint** — read `${CLAUDE_PLUGIN_ROOT}/skills/feature-development/tail.md`
now, the run is armed, and follow its `## Checkpoint commits & pushes`: commit
`<specs.dir>/<TICKET_ID>/**` + `<specs.dir>/.active_ticket` and push — subject `docs: <TICKET_ID>
planning artifacts` when a plan exists, `docs: <TICKET_ID> work list` otherwise (phase runs:
`docs: <TICKET_ID> phase <N> planning artifacts` / `docs: <TICKET_ID> phase <N> work list`).
Journal it as an external action. No verify gate here (`verify.commands`) — docs only, no code
yet. On the kartoteka path the procedure sweeps images first and never stages one (its steps 2
and 4); when, images aside, only `.active_ticket` changed, skip the commit and journal `planning checkpoint: skipped — the spec trail is in kartoteka`.

**Record the baseline** (`${CLAUDE_PLUGIN_ROOT}/docs/gates.md` §1):
`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify.py checkpoint --record-baseline --ticket <TICKET_ID>`.
Exit 2 → environment error, stop-and-ask. Journal `baseline: recorded (<n> keys across <m>
stages)` or `baseline: skipped (verify.commands empty)`. **Fresh arm only** — the moment
`run-state.json` is first written: on resume an existing `.artel/run/<TICKET_ID>/verify-baseline.json`
is kept and a missing one stays missing (the tree already carries the branch's changes, so a
snapshot now would hide them; the checkpoint gate then reports `baseline: absent` and treats any
red as red). A phase boundary never re-records. `--step` runs record it too — it is evidence, not
run state.

### 6. The tail

The tail — `${CLAUDE_PLUGIN_ROOT}/skills/feature-development/tail.md`, from `## Phase
traversal` to its final report — holds phase traversal and the re-mirror, gates 5–10.7, the
completion gate, the PR description and the PR gate, the description-file sync and the final
report. It is the whole run after arming and holds every `pause_reason` bracket after the head.
A resumed armed run enters here; its gates skip what is already done.

**Who runs it** (autonomous-run.md §18). In a `--step` run, or with `seat.enabled` absent or
`false` (config.md): you do — read `tail.md` now and run it inline. Otherwise dispatch the seat
once (Claude Code only; the OpenCode build runs the tail inline):

    Agent(artel:seat, prompt: "Run the tail of <$0>. Mode fields: <the six from
    run-state.json>. Headless: <yes|no>. Local: <yes — pass --local to every sub-skill | no>.
    Spec store: <the §2.3 field>.")

On a resume, dispatch a fresh seat — a crashed session's seat is never `SendMessage`-resumed.
Returns:

- `COMPLETED` — print its final report and rulings list; the run is done.
- `PAUSED: <reason>` — the relay (autonomous-run.md §18.2): read
  `.artel/run/<TICKET_ID>/pause-request.json`, ask its question verbatim via `AskUserQuestion`
  (the seat has already set `pause_reason`; clear it on the answer), then `SendMessage` the
  seat the answer.
- `STOPPED: <reason>` — report the line and the journal pointer; the run stays paused.

A dispatch that fails to start, or returns none of the three shapes: journal `seat: unavailable
— running the tail inline` and run the tail inline, once.

## Important

- Execute gates sequentially — each depends on the previous.
- Every `AskUserQuestion` after step 5 has armed the run MUST be bracketed by a `pause_reason`
  set/clear (autonomous-run.md §2) — the Stop hook depends on it. Before that — steps 2–4, every
  head — a question is plain.
- On any stop (cap escalation, abort): leave `run_active: true` with the `pause_reason` set —
  resuming the skill continues the run; an explicit user abort sets `pause_reason: "user-abort"`,
  `run_active: false`.
- A size is raised by a head and lowered only by the person's flag. Read the one head file the
  size names: a head that did not run is not context the run needs.
- The only files this orchestrator writes directly: `<specs.dir>/.active_ticket`,
  `.artel/run/<TICKET_ID>/sizing.json`,
  `.artel/run/<TICKET_ID>/run-state.json`, `.artel/run/<TICKET_ID>/run-journal.md`,
  `.artel/run/<TICKET_ID>/runtime-observation.md`, the phase-aware `runtime/observation.md`
  surface-skip entry, the `open-questions.md` status flips (same directory), the deletion of a
  stale or reset `plan-review.md` (same directory — gate 4.2 and **Request changes**), a task's
  `Route:` line changed at the lean head's confirmation, the
  description file during sync, `.artel/run/<TICKET_ID>/spec-store.json` (through
  `spec_store.py`); on the kartoteka path its spec-document writes — the plan-check bounce line,
  runtime and verify fix batches, the review reset — are store writes. Everything else is delegated.
  Under a seated run the gate-time writes move with the seat: this list then covers steps 0–5
  plus the `pause-request.json` relay handling, and the seat body owns the rest (autonomous-run.md
  §18).
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
