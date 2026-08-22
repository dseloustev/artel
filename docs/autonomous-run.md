# Autonomous run contract

*Status: draft v0.1 · 2026-08-01*

Shared contract for the autonomous `feature-development` and `dev` orchestrators and the
skills/agents they drive. Ticket-ID parsing, the spec-trail directory layout, and
`.active_ticket` are defined in [ticket-parsing.md](ticket-parsing.md) — this document does not
restate that grammar, only consumes its `<TICKET_ID>` / `<N>` tokens. Config keys referenced
below are the ones [config.md](config.md) defines. Operator-facing narrative docs:
[workflow-guide.md](workflow-guide.md) and [skills-reference.md](skills-reference.md).

## Host-writable state: `.artel/run/`

Everything this contract writes is orchestration bookkeeping, not the human-readable spec trail
— that stays at `<specs.dir>/<TICKET_ID>/` per config.md and ticket-parsing.md. Bookkeeping
lives under `.artel/run/` in the host repo instead, matching config.md's description of
`.artel/`'s two directories:

```
.artel/run/
├── .hooks/                  # session baselines and verify-stop counters (dot-prefixed so it
│                            # never collides with a ticket dir; hooks/hook_common.py STATE_DIR)
└── <TICKET_ID>/
    ├── run-state.json          # orchestrator-owned run state (§2)
    ├── run-journal.md          # append-only run journal (§11)
    ├── open-questions.md       # question collection before the approval pause (§3)
    ├── runtime-observation.md  # runtime-gate retry counter (§5)
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
- **One approval pause.** `feature-development`: plan+tasklist approval. `dev`: work-list confirmation.
  The approved artifact is the anchor for the deviation protocol — the escalation rule for
  implementation-time divergences from the approved plan/tasklist
  ([deviation-protocol.md](deviation-protocol.md)).
- **Mid-run interruptions are exceptional**, limited to: a deviation escalation (per the deviation
  protocol above), a `[HITL: …]` task, a loop-cap escalation, the PR-gate pause, or an environment
  error. Nothing else may call `AskUserQuestion` after the pause.
- **Escalate, never spin.** Every loop is capped; counters persist in the artifacts the loop writes.
- **Run state lives in artifacts**, never in conversation memory. Re-read the relevant artifacts after
  every sub-agent return — never decide on stale state.

## 2. `run-state.json`

Path: `.artel/run/<TICKET_ID>/run-state.json` (always ticket-top-level, even for phase runs — the
`ticket` field carries the phase suffix). Written **only by the orchestrator**.

```jsonc
{
  "ticket": "PROJ-2052-1",       // full identifier incl. phase suffix
  "run_active": true,
  "completed": false,            // true only after the completion gate passes (§7)
  "pause_reason": null,          // "deviation-escalation" | "hitl-task" | "cap-escalation" | "user-abort"
  "started_at": "2026-08-01T12:00:00Z",   // ISO-8601 UTC, written at run start
  "counters": { "verify": 0, "review_rounds": 0, "escalations": 0, "correction_rounds": 0 },  // reporting aggregate only (exception: correction_rounds — authoritative here, preserved across resume; §5)
  "effective_mode": "plan-gate", // "yolo" | "plan-gate" — resolved per §10 (full-gates never arms a run)
  "requested_mode": null,        // the --mode value, or null when not passed
  "requested_local": false,      // true when --local was passed (knowledge-consultation.md §1)
  "suggested_mode": "plan-gate", // classifier suggestion (§10)
  "forced_floor": null,          // highest matched floor from the sensitive-paths policy, or null (§10)
  "mode_reasons": [],            // human-readable classifier reasons
  "gates_confirmed": []          // e.g. ["TASKLIST_READY"] after the approval pause (§10)
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

`requested_local` is **carried, not re-derived.** The mode fields are recomputed on every resume
(§10) because the classifier can see everything it needs in the artifacts; `--local` it cannot —
it is a user's opt-out for this run, and a resumed run has no argument list left to read it from.
So the orchestrator writes it at arm time and hands it to every sub-skill that consults
(`analysis`, `researcher`) on a resumed gate exactly as it did on the first pass. Losing it is
silent: the run simply starts consulting again, and only a citation nobody asked for shows it.

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
- `- [ ] [HITL: <reason>] <task text>` ⇒ the orchestrator pauses **before starting** this task, sets
  `pause_reason: "hitl-task"`, asks the pre-declared question via `AskUserQuestion`, then resumes.

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
artifact to live in (it spans review, runtime, and QA rounds). On resume/re-arm it is **preserved,
never re-zeroed**; it resets only at the initial arm of a fresh run or on an explicit user reset after
a cap escalation.

| Loop | Cap (default) | Counter location |
|---|---|---|
| implement → verify fixes (per task) | `MAX_VERIFY_ITERATIONS = 4` | `Verify iterations: N` in the implementer completion note |
| review → fix → re-review | `MAX_REVIEW_ROUNDS = 3` | `**Review round:** N` in `review.md` |
| runtime gate red → fix | `MAX_RUNTIME_RETRIES = 1` | `.artel/run/<TICKET_ID>/runtime-observation.md` |
| QA negative verdict → fix | `MAX_QA_ROUNDS = 1` | `qa.md` |
| checkpoint verify → fix (per checkpoint, §14) | `MAX_CHECKPOINT_VERIFY_ROUNDS = 2` | checkpoint entry in `run-journal.md` (rounds also count toward `correction_rounds`) |
| global correction rounds (review + runtime + QA fix rounds) | `MAX_TOTAL_CORRECTION_ROUNDS = 8` | `counters.correction_rounds` in `run-state.json` |
| global wall-clock | `WALL_CLOCK_HOURS = 3` | `run-state.json` `started_at` |

These are the workflow's built-in defaults, not `.artel/config.json` keys. The runtime-gate row
only runs at all when `runtime.run` (and `runtime.drive`, for `drive-app`) are configured; absent
those keys the gate is recorded as `skipped` (config.md) and this loop never arms. When
`runtime.surface` is set (config.md), the gate additionally runs only when the run's diff
matches it — a non-match is recorded as `skipped (no runtime surface)`.

Cap hit ⇒ set `pause_reason: "cap-escalation"`, present consolidated findings via `AskUserQuestion`, stop.

Environment errors (toolchain/dependency mismatches, subprocess failures, missing tools) are **never**
loop findings — immediate stop-and-ask pointing at setup.

## 6. `--step` compatibility flag

`/artel:feature-development --step` / `/artel:dev --step` enables per-gate confirmations instead of
the autonomous default — confirm between major phases, per-task implementer approval. No
`run-state.json` is written in step mode and the Stop hook stays disarmed.

## 7. Completion gate

A run may set `completed: true` only when its pipeline's gates all pass — for `feature-development`,
`validate` reports every gate green; for `dev`, all work items are `- [x]`, review has no unresolved
Blocking/Important findings, and the runtime gate is green or skipped. The final report always includes
the aggregated `Deviations:` line (per the deviation protocol) and the loop counters.

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
orchestrators on phase-scoped runs: at run start (extract `phase-N/tasks.md` when missing) and after
the phase's gates pass (sync status back to `tasklist.md`).

- **Task-queue mirror** — `generate-tasklist` and `tasklist` mirror `tasklist.md`
  into the kartoteka task queue as they write it, and both entry-point
  orchestrators re-mirror on entry to implementation, after `sync-phases` on
  phase-scoped runs (`docs/task-queue.md` §2):
  `dev` and `feature-development` alike run the parser and `task_create` its rows.
  The re-mirror is what covers a resumed run and a tasklist written before the
  adapter was reachable, neither of which re-runs the skill that wrote it.
  Create-only and idempotent; a failure reports and falls back rather than
  blocking the run.

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

**`gates_confirmed`:** immediately after the approval pause (feature-development §3 approve /
dev §2 confirm) — or, in `yolo`, at the moment the pause would have occurred — the orchestrator
appends `"TASKLIST_READY"`. The sensitive-path guard (the plugin's `hooks/sensitive_guard.py`,
a `PreToolUse` hook) denies writes to floored paths until it is present.

## 11. Run journal

Path: `.artel/run/<TICKET_ID>/run-journal.md` (ticket-top-level, like `run-state.json`).
Append-only; written **only by the orchestrator**; exists only for armed runs. One entry per event —
run start (with the §10 mode resolution), each gate completion, every pause and resume, every
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

## 13. Design analysis stage (`figma-analysis`)

Conditional chatty-head stage — gate 0.5 of `feature-development`, between `generate-idea` and the
analysis interview — run only when `design.figma` is enabled (config.md) and either `idea.md`
contains a design-tool link (e.g. a Figma URL) or a URL is passed explicitly. Produces the ticket-level
`design-analysis.md` (+ `design/` evidence — see [ticket-parsing.md](ticket-parsing.md)) consumed
by `analysis`, `researcher`, and `planner`.

- Runs pre-arm (chatty head): its **Major-findings handshake** may call `AskUserQuestion` — plain,
  no `pause_reason` bracketing.
- Minor findings never pause — they land in the artifact's minor-findings section and are bundled
  into the analysis interview.
- A "Park for designer" resolution ⇒ `DESIGN_BLOCKED`: the artifact finalizes with
  `Status: DESIGN_BLOCKED`, which gate 0.5 treats as still-blocking on re-entry; re-run after the
  design is fixed (manual re-run uses Overwrite).
- Figma MCP unavailable/unauthenticated ⇒ the stage skips silently and the pipeline continues,
  per `design.figma`'s runtime-optional semantics (config.md) — including under headless
  invocation (§12), where the Figma MCP is interactively authenticated and may simply be absent.
- Skip-if-exists (§9) applies; manual invocation gets the Overwrite/Abort prompt. Ticket-level
  only — a phase suffix is ignored for pathing (ticket-parsing.md).

## 14. Checkpoint commits & pushes

Both orchestrators commit and push at fixed checkpoints so the branch on `origin` always carries
the latest approved docs and every completed phase: a **planning/work-list checkpoint** right after
arming (docs only, no verify gate) and a **phase-end checkpoint** after each phase's gates pass
(the verify gate — `verify.commands` — plus capped fixes first). Checkpoints are orchestrator-owned
Bash actions, pre-approved at the approval pause (§4 exception), never pause, and are journaled as
external actions (§11). The full procedure (branch guard, idempotence, the verify gate, explicit
staging, push, journal) and the commit-subject table are defined in the `feature-development`
skill (`../skills/feature-development/SKILL.md`, `## Checkpoint commits & pushes`; shared with
`dev`).

## 15. Phase traversal & `.active_ticket`

- Explicit phase invocation (`<TICKET_ID>-<N>`) runs exactly that phase, as before.
- A ticket-wide invocation on a multi-phase tasklist loops the remaining incomplete phases in
  order. Each iteration: write `<TICKET_ID>-<N>` to `<specs.dir>/.active_ticket`
  (ticket-parsing.md §6), update the `ticket` field in `run-state.json` and refresh `started_at`
  (a phase boundary re-arms the wall-clock budget), delete a stale ticket-wide `review.md`
  (preserved in the prior phase's checkpoint commit; resets the §5 review-round counter), run
  the phase's gates passing `<TICKET_ID>-<N>` to every sub-skill, and close with the phase-end
  checkpoint (§14).
- After each checkpoint, `.active_ticket` advances to the next incomplete phase; after the final
  phase the last identifier stays in place.
- `.active_ticket` is the phase pointer for argument-less invocations and for `run-app`'s
  evidence pathing; orchestrators still pass the full identifier explicitly to sub-skills.
