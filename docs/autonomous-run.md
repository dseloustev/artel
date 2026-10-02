# Autonomous run contract

*Status: draft v0.1 · 2026-08-01*

Shared contract for the autonomous `feature-development` orchestrator and the skills/agents it
drives. Ticket-ID parsing, the spec-trail directory layout, and
`.active_ticket` are defined in [ticket-parsing.md](ticket-parsing.md) — this document does not
restate that grammar, only consumes its `<TICKET_ID>` / `<N>` tokens. Config keys referenced
below are the ones [config.md](config.md) defines. Operator-facing narrative docs:
[workflow-guide.md](workflow-guide.md) and [skills-reference.md](skills-reference.md).

## Host-writable state: `.artel/run/`

Everything this contract writes is orchestration bookkeeping, not the human-readable spec trail
— that stays at `<specs.dir>/<TICKET_ID>/` per config.md and ticket-parsing.md (or, with kartoteka as the spec store, in kartoteka — [spec-storage.md](spec-storage.md)). Bookkeeping
lives under `.artel/run/` in the host repo instead, matching config.md's description of
`.artel/`'s two directories:

```
.artel/run/
├── .hooks/                  # session baselines and verify-stop counters (dot-prefixed so it
│                            # never collides with a ticket dir; hooks/hook_common.py STATE_DIR)
└── <TICKET_ID>/
    ├── sizing.json             # the size and the head it picked, written before any head (§17.3)
    ├── run-state.json          # orchestrator-owned run state (§2)
    ├── spec-store.json         # the ticket's storage decision (spec-storage.md §2.2), written by scripts/spec_store.py
    ├── run-journal.md          # append-only run journal (§11)
    ├── open-questions.md       # question collection before the approval pause (§3)
    ├── runtime-observation.md  # runtime-gate retry counter (§5)
    ├── verify-baseline.json    # the checkpoint gate's baseline, recorded once at a fresh arm (gates.md §1)
    ├── reports/                # per-task worker output (§1 "Bulk stays in files", §16):
    │   ├── NNN-<slug>.md       #   the implementer's report for one task
    │   ├── NNN-<slug>.diff     #   that task's diff package (a `full` route only, §16)
    │   └── NNN-<slug>-review.md #  the per-task review verdict (a `full` route only, §16)
    └── .stop-gate-blocks       # stop-gate's consecutive-block counter
```

Always ticket-top-level, even for phase-scoped runs — `run-state.json`'s `ticket` field carries
the phase suffix instead of a subfolder. Not committed; add `.artel/run/` to the host repo's
`.gitignore`, same as config.md prescribes.

One file is deliberately **not** here: `<specs.dir>/.active_ticket` stays exactly where
ticket-parsing.md §6 already put it. It is a phase pointer for argument-less invocations, not
run bookkeeping, and its location was settled before this document existed — §15 reads and
updates it in place, it does not relocate it.

---

## 1. Principles

- **All information is collected upfront.** The interview (analysis) and the vision checkpoint are the
  chatty head of the pipeline. After the one approval pause, the run is silent.
- **One approval pause.** The full head: plan+tasklist approval. The lean head and the bug head:
  the work-list approval (§17).
  The approved artifact is the anchor for the deviation protocol — the escalation rule for
  implementation-time divergences from the approved plan/tasklist
  ([deviation-protocol.md](deviation-protocol.md)).
- **Mid-run interruptions are exceptional**, limited to: a deviation escalation (per the deviation
  protocol above), a `[HITL: …]` task, a loop-cap escalation, the PR-gate pause, or an environment
  error (kartoteka becoming unreachable while it is the spec store is one — spec-storage.md §5.2). Nothing else may call `AskUserQuestion` after the pause.
- **Escalate, never spin.** Every loop is capped; counters persist in the artifacts the loop writes.
- **Run state lives in artifacts**, never in conversation memory. Re-read the relevant artifacts after
  every sub-agent return — never decide on stale state.
- **Bulk stays in files.** A worker returns a short status contract; its diff, evidence and
  reasoning go to a report under `.artel/run/<TICKET_ID>/reports/`, and a reviewer reads that
  file rather than a pasted diff. Everything a worker returns sits in the orchestrator's context
  for the rest of the run and is re-read on every later turn — the orchestrator's context is the
  one that has to survive the whole run, so it carries paths, not payloads.

## 2. `run-state.json`

Path: `.artel/run/<TICKET_ID>/run-state.json` (always ticket-top-level, even for phase runs — the
`ticket` field carries the phase suffix). Written **only by the orchestrator**.

```jsonc
{
  "ticket": "PROJ-2052-1",       // full identifier incl. phase suffix
  "run_active": true,
  "completed": false,            // true only after the completion gate passes (§7)
  "pause_reason": null,          // "deviation-escalation" | "hitl-task" | "cap-escalation" | "store-unavailable" | "user-abort"
  "started_at": "2026-08-01T12:00:00Z",   // ISO-8601 UTC, written at run start
  "counters": { "verify": 0, "review_rounds": 0, "escalations": 0, "correction_rounds": 0 },  // reporting aggregate only (exception: correction_rounds — authoritative here, preserved across resume; §5)
  "effective_mode": "plan-gate", // "yolo" | "plan-gate" — resolved per §10 (full-gates never arms a run)
  "requested_mode": null,        // the --mode value, or null when not passed
  "requested_local": false,      // true when --local was passed (knowledge-consultation.md §1)
  "suggested_mode": "plan-gate", // classifier suggestion (§10)
  "forced_floor": null,          // highest matched floor from the sensitive-paths policy, or null (§10)
  "mode_reasons": [],            // human-readable classifier reasons
  "gates_confirmed": [],         // e.g. ["TASKLIST_READY"] after the approval pause (§10)
  "deviation_files": []          // files named by completions' `Deviations:` lines (§16, floor 4)
}
```

Transitions:

- **Run start** (immediately after the approval pause): write the file with `run_active: true`,
  `completed: false`, `pause_reason: null`, fresh `started_at`.
- **Legitimate pause** (before asking the user mid-run): set `pause_reason` to the matching enum value.
- **Resume after a pause** (user answered): set `pause_reason` back to `null`.
- **Completion**: set `completed: true`, `run_active: false`.
- **Abort**: set `pause_reason: "user-abort"`, `run_active: false`.
- **Staleness**: a file whose `started_at` is older than `WALL_CLOCK_HOURS = 3` is treated as inactive
  by the Stop hook (§8). On resume, the orchestrator rewrites `started_at`.

The `counters` object is a reporting aggregate; authoritative counters live in the loop artifacts (§5).

An **armed run** is one whose file has `run_active: true` and `gates_confirmed` holding
`TASKLIST_READY`. Re-invoking `feature-development` on it skips the import, sizing and every
head (§17.2): the orchestrator re-derives the mode fields, re-arms and enters the tail.

`requested_local` is **carried, not re-derived.** The mode fields are recomputed on every resume
(§10) because the classifier can see everything it needs in the artifacts; `--local` it cannot —
it is a user's opt-out for this run, and a resumed run has no argument list left to read it from.
So the orchestrator writes it at arm time and hands it to every sub-skill that consults
(`analysis`, `researcher`) on a resumed gate exactly as it did on the first pass. Losing it is
silent: the run simply starts consulting again, and only a citation nobody asked for shows it.

`deviation_files` is **carried, not re-derived**, for the same reason. It is `[]` at a fresh arm
and only grows: after every implementer completion — iteration task or fix list — the
orchestrator adds each path the completion's `Deviations:` line names
([deviation-protocol.md](deviation-protocol.md) §5), once. A resume and a phase boundary keep
it, so a deviation in phase 1 still raises a phase-3 task on the same file to `full` (§16,
floor 4). A run armed before 0.23.0 has no such key; read it as `[]`.

## 3. Question collection — `open-questions.md`

Path: `.artel/run/<TICKET_ID>/open-questions.md`. Between the interview and the approval pause,
sub-skills (`researcher`, `planner`, `tasklist`) never ask the user. A question they cannot resolve is
appended here and the pipeline continues on the proposed default. Entry format (append-only, numbered):

```markdown
## Q1: <question> (from: planner)
- **Context:** <why this came up>
- **Proposed default:** <the default the pipeline proceeds on>
- **Impact if default is wrong:** <one line>
- **Status:** open
```

At the approval pause the orchestrator presents every `Status: open` entry (defaults pre-selected).
After the user answers, the orchestrator has the relevant agent fold answers into the plan/tasklist and
flip each entry to `- **Status:** resolved: "<answer>"`. A missing file means no questions — not an error.

## 4. AFK / HITL task tags

Tasklist writers (`task-planner`, `tasklist-writer` agents) tag tasks at generation time:

- Untagged task ⇒ **AFK**: runs without any human interaction.
- A `[HITL: <reason>]` tag ⇒ the orchestrator pauses **before starting** the task, sets
  `pause_reason: "hitl-task"`, asks the pre-declared question via `AskUserQuestion`, then resumes.
  The tag sits on the task's heading —
  `### Task 2.3: <title> [HITL: <reason>]` ([task-grammar.md](task-grammar.md) §1) — and covers
  the whole task: one question before its first step, none between steps; a tag on a step is a
  grammar problem (`hitl-on-step`), because the route floor and the pause read only the heading.

Mandatory HITL triggers:

- Sensitive surfaces — any task whose files match the sensitive-paths policy (§10): a
  project-defined set of glob categories, e.g. credential/secret storage, cryptographic key
  material, database schema/migrations, or payment/money-movement code. Ships with generic
  illustrative defaults; a project extends or replaces them for its own high-risk areas.
- Irreversible external actions (pushes, API writes to third parties via `tracker.adapter` /
  `vcs.adapter`). Exception: the orchestrator-owned checkpoint commits & pushes (§14) are
  pre-authorized at the approval pause and are never HITL-tagged.
- An explicit "user must decide/provide X" recorded during the interview.

Prefer AFK wherever possible. Tags are shown at the approval pause, so every potential interruption is
agreed upon before the run starts.

## 5. Capped loops

Every loop writes its counter into the artifact it produces, so re-invocation cannot reset it. Counters
reset only when the user resumes with guidance after a cap escalation. Exception:
`counters.correction_rounds` is authoritative in `run-state.json` itself — it has no single loop
artifact to live in (it spans review and runtime rounds). On resume/re-arm it is **preserved,
never re-zeroed**; it resets only at the initial arm of a fresh run or on an explicit user reset after
a cap escalation.

| Loop | Cap (default) | Counter location |
|---|---|---|
| task gate → fix (per task, gates.md §1) | `MAX_VERIFY_ITERATIONS = 4` | `Verify iterations: N` in the implementer completion note |
| per-task review → fix (per task on the `full` route, §16) | `MAX_TASK_REVIEW_ROUNDS = 1` | the task's `task review` entry in `run-journal.md` (the round also counts toward `correction_rounds`) |
| route floor 3 (per task, §16) — a threshold, not a loop | `ROUTE_FULL_FILES = 5` | none: a task whose `Files:` lists more paths is routed `full` |
| plan review → fix (before the pause, task-format tasklists) | `MAX_PLAN_REVIEW_ROUNDS = 2` | `**Plan-review round:** k` in `.artel/run/<TICKET_ID>/plan-review.md` (the chatty head's; never counts toward `correction_rounds`) |
| review → fix → re-review | `MAX_REVIEW_ROUNDS = 3` | `**Review round:** N` in `review.md` (reset by deleting it on the files path, by a round-0 version on the kartoteka path — spec-storage.md §4.4) |
| runtime gate red → fix | `MAX_RUNTIME_RETRIES = 1` | `.artel/run/<TICKET_ID>/runtime-observation.md` |
| checkpoint verify → fix (per checkpoint, §14) | `MAX_CHECKPOINT_VERIFY_ROUNDS = 2` | checkpoint entry in `run-journal.md` (rounds also count toward `correction_rounds`) |
| debug it here first (per halt, below) | `MAX_DEBUG_HERE_ATTEMPTS = 1` | the halt's `debugged here: …` entry in `run-journal.md` |
| baseline capture (§14, gates.md §1) | once per run, at a fresh arm — never re-recorded on resume or at a phase boundary | `.artel/run/<TICKET_ID>/verify-baseline.json` |
| global correction rounds (review + runtime fix rounds) | `MAX_TOTAL_CORRECTION_ROUNDS = 8` | `counters.correction_rounds` in `run-state.json` |
| global wall-clock | `WALL_CLOCK_HOURS = 3` | `run-state.json` `started_at` |

These are the workflow's built-in defaults, not `.artel/config.json` keys. The runtime-gate row
only runs at all when `runtime.run` (and `runtime.drive`, for `drive-app`) are configured; absent
those keys the gate is recorded as `skipped` (config.md) and this loop never arms. When
`runtime.surface` is set (config.md), the gate additionally runs only when the run's diff
matches it — a non-match is recorded as `skipped (no runtime surface)`.

Cap hit ⇒ set `pause_reason: "cap-escalation"`, present consolidated findings via `AskUserQuestion`, stop.

**Debug it here first.** On three cap escalations — each a red gate in app code — the question
opens with one more option, **Debug it here first**: a task aborted when `MAX_VERIFY_ITERATIONS`
ran out, a checkpoint still red after `MAX_CHECKPOINT_VERIFY_ROUNDS`, and the runtime gate's
second red from an app-code error. Not the review cap, not an environment error, not
`MAX_TOTAL_CORRECTION_ROUNDS` or the wall clock; a headless run journals and stops as before.
The option runs the `debugging` skill on the red evidence while `pause_reason` stays
`cap-escalation`. Fixed → the orchestrator journals `debugged here: <root cause> — <files>`,
adds the files to `deviation_files` (§2) and re-runs the gate that was red; green resets that
loop's counter as a resume with guidance does. Red again → the question is asked again without
the option: `MAX_DEBUG_HERE_ATTEMPTS = 1`, one attempt per halt. The procedure is in the
`feature-development` skill (`../skills/feature-development/tail.md`, `## Debug it here first`).

**Step-up rounds.** A fix round that follows a failed one runs one tier up, on `fable`: the
review loop's fix round whose findings come from a `review.md` with `**Review round:**` 2 or
more, and a checkpoint's second `## Verify Fixes` round. The orchestrator passes the
`implementer` skill `--model fable`; every other implementer dispatch runs on the agent's
frontmatter `opus`. Rounds that exist once — the per-task review's fix round, the runtime retry,
review fix round 1 — keep the default, and a stepped-up round counts toward
`MAX_TOTAL_CORRECTION_ROUNDS` like any other. OpenCode ignores the per-dispatch model
([opencode.md](opencode.md)).

Environment errors (toolchain/dependency mismatches, subprocess failures, missing tools) are **never**
loop findings — immediate stop-and-ask pointing at setup.

## 6. `--step` compatibility flag

`/artel:feature-development --step` enables per-gate confirmations instead of
the autonomous default — confirm between major phases, per-task implementer approval. No
`run-state.json` is written in step mode and the Stop hook stays disarmed.

## 7. Completion gate

A run may set `completed: true` only when its gates all pass. The orchestrator confirms eight
facts itself, from the artifacts it already
reads, and journals one line each: `PLAN_APPROVED` (`skipped (no plan)` when the ticket has no
plan — `gates_confirmed` holding `TASKLIST_READY` is then the approval — and
`skipped (plan not approved by this run)` when a plan exists that this run did not approve: a
draft on a run whose confirmed work list was the approval), `TASKLIST_READY` (the
tasklist's status, or `gates_confirmed` for a tasklist that declares none), `IMPLEMENT_STEP_OK` (no
unchecked box in any iteration or fix section, and every fix-section parent closed —
[task-queue.md](task-queue.md) §6), `REVIEW_OK` (`review.md` with a round line, no
open `## Code Review Fixes` box), `RUNTIME_OK` (green or skipped, newer than the last
`runtime.surface` change), `CHECKPOINT_OK` (the final gate — the last checkpoint green and no
`verify.surface` change since its commit, else one more checkpoint gate; gates.md §1),
`DOCS_UPDATED` (`summary.md` exists — written on the last phase before its checkpoint, so that
checkpoint commit carries the docs; `skipped (no PRD)` when the ticket has no PRD, because the
docs stage runs only with one) and `AUTOMATION_REMOVED`; the table is in the
`feature-development` skill (`../skills/feature-development/tail.md`, `## Completion gate`). A
fact that reads `skipped` is green. Those readings are keyed on what exists for the ticket,
never on the head that ran. No
agent is dispatched: `/artel:validate` is à la carte. The final report always includes the
aggregated `Deviations:` line (per the deviation protocol) and the loop counters.

## 8. Stop hook interplay

The Stop hook (registered on the `Stop` event — the plugin's `hooks/stop_gate.py`) blocks a
session from ending while
`run_active` is true, `completed` is false, and `pause_reason` is null. Waiting for a human (any
`pause_reason`) is a legitimate stop. The hook fails open on infra errors and disarms itself past
the wall-clock budget (§5).

## 9. Worker-skill invocation (skip-if-exists)

When an orchestrator invokes `generate-idea` / `generate-vision` / `generate-tasklist`, an existing
output artifact satisfies the gate — the orchestrator skips the invocation entirely. The workers'
interactive Overwrite/Abort prompts fire only on manual invocation. `sync-phases` is invoked by the
orchestrator on phase-scoped runs: at run start (extract `phase-N/tasks.md` when missing) and after
the phase's gates pass (sync status back to `tasklist.md`). On the kartoteka path an artifact "exists" when the store holds it (`spec_store.py list <TICKET_ID>`, spec-storage.md §4.1).

- **Task-queue mirror** — `generate-tasklist` and `tasklist` mirror `tasklist.md`
  into the kartoteka task queue as they write it, and the orchestrator
  re-mirrors on entry to implementation, after `sync-phases` on
  phase-scoped runs (`docs/task-queue.md` §2):
  `feature-development`'s tail runs the parser and `task_create`s its rows, whichever head ran.
  The re-mirror is what covers a resumed run and a tasklist written before the
  adapter was reachable, neither of which re-runs the skill that wrote it. For a
  task-format tasklist it is also the first mirror: `tasklist` mirrors nothing before the plan
  is `PLAN_APPROVED`, because the plan review and the fold-back can still renumber tasks
  (`docs/task-queue.md` §2).
  Every writer of a fix section — `run-reviewer`, `deep-review`, the runtime gate
  and the phase checkpoint — records its batch the same way right after the
  append, so the queue shows fix work before the first fix is dispatched (§6:
  recorded, never offered). Create-only and idempotent; a failure reports and
  falls back rather than blocking the run.

`pr-description` is the exception to skip-if-exists: invoked by `feature-development` at run completion
(after all gates are green, before `completed: true`), it always regenerates `pr-description.md` — the
branch diff is its input, so an existing file is stale by definition. It is prompt-free; external-fetch
failures degrade gracefully and never block completion.

## 10. Modes & risk classification

Three ranked modes control how much the run pauses for approval: `yolo=0 < plan-gate=1 < full-gates=2`.

| Mode | Meaning on this base |
|---|---|
| `plan-gate` | **Default.** Today's autonomous run: one approval pause, HITL tags pause, PR gate pauses. |
| `yolo` | The approval pause and the PR-gate *pause* are skipped — `pr-create` still runs; the opened PR is yolo's human checkpoint. Open questions proceed on their recorded defaults, folded in silently. HITL tags, deviation escalations, and cap escalations still pause (guardrails). |
| `full-gates` | Alias for `--step` (§6): per-gate confirmations, no `run-state.json`, hooks disarmed. |

**Classifier (orchestrator procedure, run at arm time and re-run on every resume):**

1. Collect candidate paths: every backticked file path and every `Files:` entry in the available
   artifacts (plan, tasklist, work list; idea/PRD as fallback).
2. Match them against the sensitive-paths policy (glob-based categories, `fnmatch` semantics;
   shipped defaults in the plugin's `hooks/sensitive-paths.json`, replaced wholesale by a host
   `.artel/sensitive-paths.json` when present — config.md, "Purpose and location").
   `forced_floor` = the highest floor among matched categories, else `null`.
3. `suggested_mode`: `yolo` only when ALL hold — ≤ 3 files touched, no `open-questions.md`
   entries, no sensitive-category match, and the work mirrors an established pattern; otherwise
   `plan-gate`.
4. `effective_mode = max(requested_mode or suggested_mode, forced_floor)`.
5. `forced_floor == "full-gates"` ⇒ do **not** arm: report the matched category/globs and instruct
   the user to run with `--step`. (Key material and database migrations are typical categories
   that demand per-gate supervision.)
6. Write all six mode fields (`effective_mode`, `requested_mode`, `suggested_mode`, `forced_floor`, `mode_reasons`, `gates_confirmed`) into `run-state.json` at arm time; announce
   `mode + reasons` in the run output. Never trust stale fields — re-derive before re-arming.

**`gates_confirmed`:** immediately after the approval pause (the full head's pause on approve,
the lean head's confirmation, the bug head's work-list approval) — or, in `yolo`, at the moment
the pause would have occurred — the orchestrator
appends `"TASKLIST_READY"`. The sensitive-path guard (the plugin's `hooks/sensitive_guard.py`,
a `PreToolUse` hook) denies writes to floored paths until it is present.

## 11. Run journal

Path: `.artel/run/<TICKET_ID>/run-journal.md` (ticket-top-level, like `run-state.json`).
Append-only; written **only by the orchestrator**; exists only for armed runs. One entry per event —
run start (with the §10 mode resolution and the §17 size), each gate completion, every pause and resume, every
external action, completion/abort. Entry format:

```markdown
## <UTC ISO-8601> — <event: gate/pause/resume/external/complete>
- decision: auto | paused (<pause_reason>) | skipped (<why>)
- artifacts: <paths written or updated, or none>
- external: <PR URL (`vcs.adapter`) / tracker comment (`tracker.adapter`) / none>
- counters: verify=N review_rounds=N correction_rounds=N escalations=N
```

The journal is the only machine-readable record of a run (headless stdout stays human-oriented).
A budget breach or guardrail trip is journaled before the run stops.

## 12. Headless invocation

```bash
claude -p "/artel:feature-development <TICKET_ID> --mode=yolo" --output-format stream-json --verbose
```

- Requires a curated tool/command permissions allowlist (Claude Code settings) — never a
  permissions-bypass flag. A stall on a missing permission is the guardrail working; the fix is
  a deliberate allowlist extension, never a bypass.
- First `yolo` runs: throwaway branch, low-risk ticket, transcript reviewed against the journal.
- Crash/sleep mid-run: artifacts + journal survive; re-invoking the same command resumes
  (skip-if-exists); §2 staleness prevents a dead run from arming hooks forever.
- Headless escalation rules (`AskUserQuestion` does not exist under `-p`): a **deviation escalation**
  takes the implementer's recommended option and is journaled as
  `paused (deviation-escalation) → auto-resolved`; **HITL tags** and **cap escalations** are never
  auto-resolved — journal the entry, set the `pause_reason`, and stop. A stalled headless run on a
  HITL/cap pause is the guardrail working; resume it interactively.
- Spec store unavailability follows `specs.onUnavailable` (spec-storage.md §5.4): `"abort"` stops (journaled), `"local"` works locally; a local trail found at start always stops and names `/artel:migrate-specs`.
- Sizing (§17) never pauses, so it needs no answer: the size line goes to the output and into
  `sizing.json`, and the run-start journal entry repeats it. Two outcomes end a headless run
  unarmed: a spike prints its answer and stops, and a `NOT_REPRODUCED` diagnosis prints the
  diagnosis' path and stops. A `yolo` run opens its pull request unattended whichever head ran.

On OpenCode, the same unattended pattern runs through `opencode run` with the host's
`permission` config as the curated allowlist; the Stop gate fires on the idle event as a
re-prompt rather than a hard block. Host specifics: `docs/opencode.md`.

## 13. Design analysis stage (`figma-analysis`)

Conditional chatty-head stage — gate 0.5 of the full head (`heads/full.md`), between `generate-idea` and the
analysis interview — run only when `design.figma` is enabled (config.md) and either `idea.md`
contains a design-tool link (e.g. a Figma URL) or a URL is passed explicitly. Produces the ticket-level
`design-analysis.md` (+ `design/` screenshots — kartoteka path: kartoteka, viewed with
`image fetch`; files path: files, as before — see [ticket-parsing.md](ticket-parsing.md)) consumed
by `analysis`, `researcher`, and `planner`.

- Runs pre-arm (chatty head): its **Major-findings handshake** may call `AskUserQuestion` — plain,
  no `pause_reason` bracketing.
- Minor findings never pause — they land in the artifact's minor-findings section and are bundled
  into the analysis interview.
- A "Park for designer" resolution ⇒ `DESIGN_BLOCKED`: the artifact finalizes with
  status `DESIGN_BLOCKED`, which gate 0.5 treats as still-blocking on re-entry; re-run after the
  design is fixed (manual re-run uses Overwrite).
- Figma MCP unavailable/unauthenticated ⇒ the stage skips silently and the pipeline continues,
  per `design.figma`'s runtime-optional semantics (config.md) — including under headless
  invocation (§12), where the Figma MCP is interactively authenticated and may simply be absent.
- Skip-if-exists (§9) applies; manual invocation gets the Overwrite/Abort prompt. Ticket-level
  only — a phase suffix is ignored for pathing (ticket-parsing.md).

## 14. Checkpoint commits & pushes

The orchestrator commits and pushes at fixed checkpoints so the branch on `origin` always carries
the latest approved docs and every completed phase: a **planning checkpoint** right after
arming (docs only, no verify gate; its subject says `planning artifacts` when a plan exists and
`work list` otherwise) and a **phase-end checkpoint** after each phase's gates pass
(the checkpoint gate — `verify.commands` compared against the run's baseline, gates.md §1 — plus capped fixes first). Checkpoints are orchestrator-owned
Bash actions, pre-approved at the approval pause (§4 exception), never pause, and are journaled as
external actions (§11). The full procedure (branch guard, the image sweep, idempotence, the verify
gate, explicit staging, push, journal) and the commit-subject table are defined in the `feature-development`
skill (`../skills/feature-development/tail.md`, `## Checkpoint commits & pushes`). On the
kartoteka path the planning checkpoint stages the same paths, and when, images
aside, only `.active_ticket` changed it skips the commit and journals
`planning checkpoint: skipped — the spec trail is in kartoteka`. On the kartoteka path every
checkpoint sweeps images into kartoteka first and never stages one
([spec-storage.md](spec-storage.md) §4.6). The baseline is recorded once per run at a fresh arm
(`verify.py checkpoint --record-baseline`) and never on resume; gates.md §1 says what its absence
means.

## 15. Phase traversal & `.active_ticket`

- Explicit phase invocation (`<TICKET_ID>-<N>`) runs exactly that phase, as before.
- A ticket-wide invocation on a multi-phase tasklist loops the remaining incomplete phases in
  order. Each iteration: write `<TICKET_ID>-<N>` to `<specs.dir>/.active_ticket`
  (ticket-parsing.md §6), update the `ticket` field in `run-state.json` and refresh `started_at`
  (a phase boundary re-arms the wall-clock budget), reset the review round — delete a stale ticket-wide `review.md`
  on the files path (preserved in the prior phase's checkpoint commit), store the round-0 version on
  the kartoteka path (spec-storage.md §4.4; preserved as an earlier version) — resetting the §5
  counter, run
  the phase's gates passing `<TICKET_ID>-<N>` to every sub-skill, and close with the phase-end
  checkpoint (§14).
- After each checkpoint, `.active_ticket` advances to the next incomplete phase; after the final
  phase the last identifier stays in place.
- `.active_ticket` is the phase pointer for argument-less invocations and for `run-app`'s
  evidence pathing; the orchestrator still passes the full identifier explicitly to sub-skills.

## 16. Routes

Every iteration task runs on a **route**, `light` or `full`, in the orchestrator's
implementation loop (gate 5, `tail.md`). The route decides one thing:
whether the task's own diff is reviewed right after its implementer returns and before the next
task is dispatched, so a misread requirement is caught before the next task builds on it. It
never replaces the phase review (gate 7, `tail.md`) — that still runs over the whole phase
with the lenses and `review.md`, whatever the routes were; a `full` task's review feeds it.

### 16.1 Which route a task takes

On a task-format tasklist ([task-grammar.md](task-grammar.md) §4) each task has its own:

- **Declared** by the planner on the task's `Route:` line — `light`, or `full — <reason>`.
- **Floored** — four floors, which only ever raise a route to `full`:
  1. a `Files:` path matches a sensitive-paths category (the plugin's
     `hooks/sensitive-paths.json`, replaced wholesale by a host `.artel/sensitive-paths.json`, §10);
  2. the task carries a `[HITL: …]` tag and at least one of its `Files:` lies outside the
     ticket's spec trail (`<specs.dir>/<TICKET_ID>/`);
  3. its `Files:` lists more than `ROUTE_FULL_FILES = 5` paths;
  4. an earlier deviation in this run changed one of its files.

  Floors 1–3 are the parser's: every task row carries `route_floor`, `route_reasons` and
  `route_effective`, the higher of the declared route and the floor — or the declared route
  alone when it was set at approval (task-grammar.md §7).
  Floor 4 is known only at runtime: the orchestrator checks the task's `Files:` against
  `run-state.json` `deviation_files` (§2) as it stood when the task was dispatched, so a task's
  own deviations raise the tasks after it, not itself.
- **Overridable at the pause.** The approval pause (the full head's pause, the lean head's
  confirmation) lists every task's effective route with its reasons —
  `2.3 full — declared: money-movement path; floor: sensitive path (payments): lib/ramps/ramps_bloc.dart`
  — and the person may change any of them, down as well as up. A change is folded back into
  the task's `Route:` line with `— set at approval` (`Route: light — set at approval`), so the
  document stays the record, and journaled in the run-start entry, naming any floor it lowered.
  A route set at approval is final over floors 1–3; floor 4 still applies, because no one could
  see it at the pause.
- **`review.perTask: true`** ([config.md](config.md)) raises every task to `full`.

**When the route is decided.** The implementer takes its own task — `task_ready` on the queue
path, the first of `ready_now` on the fallback ([task-queue.md](task-queue.md) §3, §4) — so the
orchestrator learns which task a dispatch worked from its completion, whose first line names
it (`Task 2.3: <title>`). On a task-format tasklist it therefore snapshots before **every**
iteration-task dispatch (§16.2 step 1) and decides the route when the completion arrives: the
parser's row for that task (the re-mirror's parser run, or on a `--local` run a parser run of
its own), floor 4 and `review.perTask`. A `light` task's snapshot is never used.

One journal line per dispatched task (§11), in this form:

    task 2.3: route full (declared full; floor: sensitive path (payments): lib/ramps/ramps_bloc.dart)

— `task <N.M>: route <effective> (declared <route>[; floor: <reason>[, <reason>…]])`, the reasons
the row's `route_reasons`, plus `earlier deviation: <path>` for floor 4 and `review.perTask` when
the key raised it.

### 16.2 What a route runs

- **`light`** — nothing more: the implementer's own task gate (gates.md §1) is the task's check.
- **`full`** — the wrapper below: one reviewer seat on the task's diff and at most one fix round.

The `full` wrapper, per iteration-task dispatch:

1. **Snapshot** — before dispatching the implementer, run
   `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/review_package.py snapshot` and keep the printed tree
   id as `BASE`. It captures the working tree (tracked + untracked, ignore rules honoured)
   through a temporary index — the real index, HEAD and the checkpoint's explicit staging are
   untouched. Exit 2 → journal it and run this task without the gate; never skip the task.
2. **Package** — on a completion (not a `HITL:` return or a `DEVIATION` halt), run
   `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/review_package.py diff <BASE> --out
   .artel/run/<TICKET_ID>/reports/NNN-<slug>.diff`, `NNN-<slug>` taken from the completion's
   `Report:` path so the three files pair up. The one-line output carries the file count:
   `0 file(s)` → journal `task review: skipped (empty diff)` and move on.
3. **Review** — `Skill: run-reviewer` with `$0 --task "<task title>" --report <report path>
   --package <diff path>` (plus `--local` on a run that holds it); on a task-format tasklist
   `<task title>` is the task heading's text after `### ` (`Task 2.3: Show the purchase success
   dialog`). The `reviewer` agent's task
   mode writes `NNN-<slug>-review.md` and appends every Blocking / Important finding and every
   spec gap as a task under `## Code Review Fixes` in the phase-aware tasklist — the same
   section and format the phase review uses, so nothing downstream learns a new shape —
   beneath a `### task-gate-<NNN>` source heading, and `run-reviewer` records the batch in the
   task queue before step 4's round starts (`docs/task-queue.md` §6).
4. **One fix round** — when it appended fix tasks: increment `counters.correction_rounds` (the
   `MAX_TOTAL_CORRECTION_ROUNDS` check applies), then loop `Skill: implementer` naming
   `## Code Review Fixes` (plus `--local` on a run that holds it) until no fix task from this
   review is left unchecked (one round = the whole list, counted once).
   `MAX_TASK_REVIEW_ROUNDS = 1`: there is no per-task re-review — a fix task the round could
   not close stays unchecked and the phase review owns it from there; it is what `REVIEW_OK`
   sees. The implementer's usual returns apply inside the round (`HITL:`,
   `DEVIATION`, aborted task), handled exactly as in the main loop.
5. **Journal** — one `task review` entry (§11) per task: the verdict, the fix-task count, the
   round taken or `skipped (<why>)`, and the three report paths under `artifacts`. On resume
   this entry is the counter: a task whose entry records the round is done with the gate even
   if fix tasks are still open.

Fix-list tasks (`## Code Review Fixes`, `## Runtime Fixes`, `## Verify Fixes`) are never
wrapped — the loop that dispatched them re-checks its own result. `--step` runs the wrapper too,
minus the run-state and journal writes (§6).

Costs: one reviewer seat per `full` task on top of the phase review. Every floor is journaled
with its reason, so a run that routes too much to `full` shows why; `ROUTE_FULL_FILES` is one
constant, and the person can lower any route at the pause. `review.perTask: true` — every task
`full` — earns its seats when tasks carry judgement throughout, or when phases are long enough
that drift across tasks has room to compound.

### 16.3 The implementer's model

An iteration task's model follows its effective route: `light` → `sonnet`, `full` → `opus`
(`MODEL_BY_ROUTE` in `scripts/task_grammar.py`). Before the Agent call the implementer skill runs
the route helper over the tasklist in scope — the phase tasks file or `tasklist.md`, on the
kartoteka path through `spec_store.py get … |` ([task-grammar.md](task-grammar.md) §8) — and
passes a non-null `data.model` as the call's `model`; `null` or exit `2` leaves it off, on the
frontmatter `opus`. The choice is relayed as a `Model:` line, after `Verify iterations:` and
before `Deviations:`, and §16.1's journal line takes the line's value verbatim as the
`; model <value>` suffix.

The prediction can miss — the helper answers for the task that was ready when it ran, while the
completion's first line names the task actually worked. The relayed line then reads
`Model: sonnet (predicted task 2.3 at route light; worked task 2.4)`. The worked task's route
still decides whether its task review runs (§16.2): a task that ran on `sonnet` by a missed
prediction is wrapped when its effective route came out `full`.

Fix lists are the exception: a fix list is correction of a failed gate, not a task dispatch, so
its base is `opus` whatever the task's route, with `fable` on the round after a failed one (§5);
the skill runs no helper for one.

## 17. Sizing and heads

`feature-development` is the one entry point for ticket work. After the ticket is imported
(gate 0) it sizes the work, records the size, says it aloud with its reasons and runs the head
that fits — never a pause, and no agent is dispatched: sizing is the orchestrator's own
procedure, like the mode classifier (§10). The orchestrator acts from its skill, so the rules
are stated in full there (`../skills/feature-development/SKILL.md`, step 3); this section is
their contract.

### 17.1 The four sizes

| Size | Head | It fits when |
|---|---|---|
| `spike` | `none` (§17.5) | the ticket asks a question — can we, is it feasible, which of these — and names no change to ship |
| `bug` | `bug` — `heads/bug.md` | the tracker's issue type is Bug, or the text describes behaviour that differs from what it should be: expected against actual, a regression, an error |
| `bounded` | `lean` — `heads/lean.md` | it changes a flow that already exists in the repo, in one area, with acceptance that can be stated in a few lines and no open product question |
| `architectural` | `full` — `heads/full.md` | a new flow, screen or subsystem; an interface other code depends on; a data-model change or migration; a Figma design to analyse; work that needs several phases; or acceptance that is unclear |

The head files sit beside the skill, in `../skills/feature-development/`. The full head runs
gates 0.5–4.5 with the plan review and ends in the plan+tasklist pause. The lean head takes an
existing tasklist with open tasks as the work list, or has `generate-tasklist` write one, and
confirms it once. The bug head has the `debugging` skill diagnose first (`diagnosis.md`) and
writes the work list from the diagnosis. Each ends in one approval; then the run is armed (§2)
and the tail is the same for every one of them.

### 17.2 Order of decision

The first rule that matches wins:

1. **A flag.** `--head=full` → `architectural`, `--head=lean` → `bounded`, `--head=bug` → `bug`.
   The person's flag is the only thing that lowers a size.
2. **A recorded sizing** — `sizing.json` (§17.3), unless it is a spike already answered (§17.5).
3. **What exists.** A PRD or a plan, at either scope → `architectural`. Else `diagnosis.md` →
   `bug`. Else a tasklist with open tasks, or a `vision.md` → `bounded`.
4. **Judgement** over `idea.md`, by §17.1. In doubt between two sizes the heavier wins:
   `spike` < `bounded` < `architectural`. `bug` wins any doubt it is part of: its head diagnoses
   first, and can raise itself or hand the ticket to the lean head. Every recorded reason is
   true of the ticket as it stands: `idea.md` is read to its end, and a later section overrides
   an earlier one.

An armed run (§2) is not sized. It skips the import, sizing and the head, and a `--head` flag on
it is answered with `--head ignored: the run is past its head.` when `sizing.json` records a
different head, and with nothing otherwise. A run armed before 0.25.0 has no
`sizing.json` and resumes the same way, through either command.

### 17.3 sizing.json

`.artel/run/<TICKET_ID>/sizing.json`, written by the orchestrator at sizing, before any head, so
a run interrupted inside a head resumes on the same head. It is written in `--step` runs too: it
is not run state.

```json
{
  "size": "bounded",
  "head": "lean",
  "reasons": ["changes one existing screen", "acceptance is two lines"],
  "decided_by": "judgement",
  "raised_from": null,
  "answered": false,
  "decided_at": "2026-09-30T12:00:00Z"
}
```

| Key | Value |
|---|---|
| `size` | `spike`, `bug`, `bounded` or `architectural` |
| `head` | `none`, `bug`, `lean` or `full` |
| `reasons` | list of strings — the reasons said aloud |
| `decided_by` | `flag`, `existing` (rule 3), `judgement` or `raised` |
| `raised_from` | the earlier size when a head raised it, else `null` |
| `answered` | boolean; used by a spike only |
| `decided_at` | UTC ISO-8601 |

The size is said in one line before the head starts:

    Size: <size> — <head> head. Reasons: <reason>; <reason>. To change: say so now, or re-run with --head=<full|lean|bug>.

For a spike the line opens `Size: spike — no head; the researcher answers the question.` An
answer that asks for another head is honoured as the flag would be. The run-start journal entry
(§11) carries `- size: <size> (<head> head; decided by <decided_by>)`.

### 17.4 The ratchet

A head may raise the size, never lower it: the lean head when the `tasklist-writer` returns
`RAISE` — an open product question it cannot ground, or, with no vision, work that needs more
than one iteration — and the bug head when the diagnosis is `DIAGNOSED_STRUCTURAL`. Raising
rewrites `sizing.json` (`decided_by` `raised`, `raised_from` the earlier size), prints

    Size raised: <from> → architectural — full head. Reason: <reason>.

and runs the full head. The ratchet stops at arming: after that, hidden complexity is a
deviation ([deviation-protocol.md](deviation-protocol.md)) or an aborted task, handled as
before.

One move is not a raise: on a `NOT_REPRODUCED` diagnosis the person may choose to treat the
ticket as a bounded change, which records `bounded` with `decided_by` `flag` and runs the lean
head.

### 17.5 The spike outcome

A `spike` runs no head. The orchestrator invokes `researcher` in question mode (`--question`),
which needs no PRD and writes `<specs.dir>/<TICKET_ID>/spike.md`, answer first. The
orchestrator prints the skill's last line, `Spike answered: <one-line answer> — <path>`, sets
`answered: true` in `sizing.json` and stops. Nothing is armed, journaled or committed.

A later run on the same ticket is sized afresh from rule 3 — an answered spike is not a recorded
sizing. Sized `spike` again, it prints

    Already answered: <path to spike.md>. To build on it, re-run with --head=lean or --head=full.

and stops, without a second dispatch.
