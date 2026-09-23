# Autonomous feature workflow — operator guide

This is the narrative guide to artel's autonomous feature workflow: a ticket goes in at one end,
a pull request comes out the other, and the pipeline in between runs itself with a single human
approval pause. It tells you which command to reach for, what each one leaves behind, how a full
run unfolds, and what to do when something stops.

Two companion documents back this one. Each skill's inputs, outputs, and pause behaviour live in
the [skills reference](skills-reference.md) — this guide links to it
(`skills-reference.md#<skill-name>`) instead of re-describing skills. The rules the pipeline
obeys — run state, modes, caps, hooks — live in the contract at
[autonomous-run.md](autonomous-run.md), cited below as `autonomous-run.md §N`. Ticket-path rules
are [ticket-parsing.md](ticket-parsing.md); the config schema is [config.md](config.md); hooks
are [hooks/README.md](../hooks/README.md); the deterministic gate scripts are described in
[§ the deterministic gate scripts](#the-deterministic-gate-scripts) below; risk categories are
`hooks/sensitive-paths.json` (host override: `.artel/sensitive-paths.json`). Schemas, caps,
enums, and glob lists are cited, never restated — follow the link when you need the exact shape.

Every command below is written as a slash command (`/artel:feature-development PROJ-XXXX`).
Agents driving these programmatically use the equivalent `Skill` tool call
(`skill: "artel:feature-development"`, `args: "PROJ-XXXX"`) — identical effect. `PROJ-XXXX` is a
placeholder ticket id under the **default** ticket grammar (`ticket.projectKey`/`ticket.pattern`,
config.md — your project's own key and accepted forms come from `.artel/config.json`); `-<N>` is
a phase suffix.

## First run: the config

Everything project-specific — ticket grammar, tracker, VCS host, verify commands, languages,
runtime commands — comes from `.artel/config.json` in the host repo (config.md). No config yet?
Run [`/artel:setup`](skills-reference.md#setup), or just start
`/artel:feature-development` / `/artel:dev` — they invoke the interview themselves when the file
is missing. Unconfigured gates degrade to `skipped`, never to fake green: an empty
`verify.commands` skips the quality gate, an absent `runtime.run` skips the runtime gate,
`design.figma: false` skips design analysis.

## Where the spec trail lives

With `knowledge.adapter: "kartoteka"`, kartoteka is the spec store: every spec document —
`idea.md` through `pr-description.md` — is written to and read from kartoteka's artifact store,
and nothing lands under `<specs.dir>` except `.active_ticket` and gate evidence. Nothing about
the documents changes: same names, same templates, same statuses. Read them on kartoteka's
dashboard (`/ticket`, `/artifact`) or with `/artel:knowledge <ticket> --artifacts`. The contract
is [spec-storage.md](spec-storage.md).

- **kartoteka unreachable at the start of a run** → you are asked: Retry, Work locally for this
  run, or Abort. Working locally keeps the documents as files; `/artel:migrate-specs` moves them
  in later.
- **kartoteka unreachable mid-run** → the run pauses (`store-unavailable`). If a document was
  just produced, you are asked whether to keep it locally until kartoteka is back; resuming
  uploads it and removes the local copy.
- **Headless** → `specs.onUnavailable` answers: `"abort"` (default) or `"local"`.
- **Existing local trails** → `/artel:migrate-specs --all` (or per ticket). Missing and newer
  documents are uploaded, stale copies never overwrite newer stored versions, conflicts show a
  diff and ask, and local copies are deleted only after kartoteka verifiably holds them — with
  `git rm`, in one optional commit.
- A **write refused with "kartoteka is this project's spec store"** is `spec_store_guard.py`
  catching a file write where a store call belongs; see [spec-storage.md](spec-storage.md) §6.

## Turn one: the router

Once the config exists, every session starts with the
[`using-artel`](skills-reference.md#using-artel) router in context: the `using_artel`
`SessionStart` hook ([hooks/README.md](../hooks/README.md)) injects the Quickstart below as a
routing rule — check whether the request is ticket, feature, queue or knowledge work, and if
so invoke the matching `/artel:` skill before answering — plus the status lines (config
present, `knowledge.adapter`, `knowledge.tokenEnv` when the adapter is `kartoteka`, the active ticket, whether the `ast-index` CLI is on PATH). When
it is, code navigation — find a class, its usages, the project's structure — routes to the
`ast-index` plugin's `/ast-index:ast-index` before any grep. It re-injects after `/clear` and after
compaction, so a long run keeps it. Two things it deliberately does not do: fire inside
dispatched agents (`<SUBAGENT-STOP>`), and wrap an entry point in generic brainstorming or
plan-writing skills — `feature-development` and `dev` carry their own interview. Without a
config nothing is injected.

## Quickstart

| I want to… | Run |
|---|---|
| Import a ticket from the tracker (idea file only) | `/artel:generate-idea PROJ-XXXX` (`tracker.adapter` ≠ `"none"`) |
| Analyze the ticket's Figma mockups (flow, screen mapping) | `/artel:figma-analysis PROJ-XXXX [figma-url]` (needs `design.figma: true`) |
| Get from ticket to an approved work plan, nothing more | `/artel:feature-development PROJ-XXXX --dry-run` |
| Run the whole pipeline (idea → PR), one approval pause | `/artel:feature-development PROJ-XXXX` |
| Same, fully unattended (low-risk only) | `claude -p "/artel:feature-development PROJ-XXXX --mode=yolo" --output-format stream-json --verbose` |
| Same, supervising every gate | `/artel:feature-development PROJ-XXXX --step` |
| Lean loop (no PRD/QA/docs): implement + review + runtime | `/artel:dev PROJ-XXXX` |
| Run the next phase of a phased ticket | `/artel:feature-development PROJ-XXXX-<N>` (or `/artel:dev PROJ-XXXX-<N>`) |
| Just the PRD interview | `/artel:analysis PROJ-XXXX` |
| Just research + plan | `/artel:researcher PROJ-XXXX` then `/artel:planner PROJ-XXXX` |
| Check a plan for hallucinated references | `python3 <plugin-root>/scripts/plan_check.py --plan specs/.current/PROJ-XXXX/plan.md --strict` (kartoteka path: `set -o pipefail; python3 <plugin-root>/scripts/spec_store.py get specs/.current/PROJ-XXXX/plan.md | python3 <plugin-root>/scripts/plan_check.py --plan - --strict`) |
| Just the tasklist | `/artel:tasklist PROJ-XXXX` (from plan) or `/artel:generate-tasklist PROJ-XXXX` (from idea+vision) |
| Parse a tasklist into task-queue rows (JSON; mirrors nothing) | `python3 <plugin-root>/scripts/tasklist_tasks.py --tasklist specs/.current/PROJ-XXXX/tasklist.md --ticket-key PROJ-XXXX` (kartoteka path: `set -o pipefail; python3 <plugin-root>/scripts/spec_store.py get specs/.current/PROJ-XXXX/tasklist.md | python3 <plugin-root>/scripts/tasklist_tasks.py --tasklist - --ticket-key PROJ-XXXX`) |
| Implement the next open task | `/artel:implementer PROJ-XXXX` |
| Review / runtime-check / QA / gate-status | `/artel:run-reviewer PROJ-XXXX` · `/artel:run-app --gate` · `/artel:qa PROJ-XXXX` · `/artel:validate PROJ-XXXX` |
| PR description / open the PR | `/artel:pr-description PROJ-XXXX` · `/artel:pr-create PROJ-XXXX` |
| Resume an interrupted run | re-invoke the same entry-point command — resume is automatic |
| Ask the archive: what was decided about X, what is filed under a ticket, is the index fresh | `/artel:knowledge <query>` · `/artel:knowledge PROJ-XXXX` · `/artel:knowledge status` (needs `knowledge.adapter: "kartoteka"`) |
| See or operate the ticket's task queue: list, add, done, block, release | `/artel:tasks list PROJ-XXXX` · `/artel:tasks add PROJ-XXXX "<title>" --iteration N` or `--fix CRF` for a review fix (same requirement) |

(`<plugin-root>` is the plugin's install directory — inside a skill it is
`${CLAUDE_PLUGIN_ROOT}`; from your own shell, the path `/plugin` shows for the installed artel
plugin.)

Two entry points drive everything:
[`feature-development`](skills-reference.md#feature-development) is the full pipeline (idea →
design analysis → PRD → vision → plan → tasklist → implement → review → runtime → QA → docs →
PR); [`dev`](skills-reference.md#dev) is the lean loop (confirm a work list → implement → review
→ runtime, no PRD/plan/QA/docs and no PR). Everything else in the table is one stage of those
pipelines you can also run à la carte.

## Concepts

You only need five ideas to operate the workflow confidently.

### The ticket directory

Every ticket owns one directory, `<specs.dir>/<TICKET_ID>/` (default `specs.dir`:
`specs/.current` — config.md), and the directory name carries the ticket — filenames never
repeat it. Ticket-wide artifacts (`idea.md`, `vision.md`, `plan.md`, `tasklist.md`, `review.md`,
`qa.md`, `pr-description.md`, …) sit at the top level; **phase-scoped** artifacts live in a
`phase-<N>/` subfolder (`phase-2/tasks.md`, `phase-2/plan.md`, …), created lazily on first
write. A one-line pointer, `<specs.dir>/.active_ticket`, names the in-flight ticket (with its
phase suffix, e.g. `PROJ-2052-1`) so commands can run with no argument. The identifier grammar
(config-driven), the full path table, and the refuse-and-ask rule for an ambiguous phase scope
are in ticket-parsing.md §1–§6.

### Artifacts are the run state

The pipeline keeps its state on disk, never in conversation memory — so a crash, a new session,
or a re-invocation all resume from the same files. The spec trail stays human-readable under
`<specs.dir>/`; run bookkeeping lives in its own gitignored tree, `.artel/run/<TICKET_ID>/`
(autonomous-run.md, "Host-writable state"). Four artifacts matter:

- **`run-state.json`** (`autonomous-run.md §2`) — written only by the orchestrator; carries
  `run_active`, `completed`, `pause_reason`, `started_at`, the resolved mode fields, and
  `gates_confirmed`. Its existence with `run_active: true` is what "armed" means.
- **`open-questions.md`** (`autonomous-run.md §3`) — between the interview and the approval
  pause the silent sub-skills (`researcher`, `planner`, `tasklist`) never stop to ask; a
  question they can't resolve is appended here with a proposed default, and the pipeline
  proceeds on that default. Every still-open entry is surfaced at the one pause.
- **HITL tags** (`autonomous-run.md §4`) — a tasklist task written `[HITL: <reason>]`
  (human-in-the-loop) makes the orchestrator pause *before* starting it. Untagged tasks run AFK. Sensitive surfaces (whatever
  the sensitive-paths policy matches: secrets, gate config, CI/CD by default) are mandatory
  HITL. Tags are shown at the approval pause, so every interruption is agreed before the run
  goes silent.
- **Capped loops** (`autonomous-run.md §5`) — every fix loop (implement-verify, review, runtime,
  QA) has a hard cap, and each writes its counter into the artifact it produces so re-invocation
  can't reset it. Hit a cap and the run escalates (consolidated findings, then stop) rather than
  spinning. The global correction-round budget lives in `run-state.json` itself.

The run also keeps an append-only **`run-journal.md`** (`autonomous-run.md §11`) — one entry per
gate, pause, resume, and external action. It is the machine-readable record of what an armed run
did; headless stdout stays human-oriented.

### What writes to your repo and remotes

Five skills — and only these — touch git history or remotes: **`feature-development`** and
**`dev`** (the checkpoint commits + pushes to `origin`, authorized at the approval pause:
planning/work-list docs after arming, then one commit+push per completed phase —
`autonomous-run.md §14`); **`pr-create`** (commit + push when the tree is dirty, then the PR
itself); **`add-automation`** / **`remove-automation`** (each commits exactly the scaffold
paths; `remove-automation` also pushes when an upstream exists). Everything else writes only
files: `init-branch` may create a branch (only when asked) but never commits; `move-to-worktree` /
`return-from-worktree` (and `init-branch`'s worktree option) move uncommitted work between the
main checkout and `.claude/worktrees/` through `git stash` and switch the main checkout's branch,
but never commit, push or delete a branch; `merge-conflicts` stages and stops;
`implementer` changes source but leaves committing to the orchestrators' checkpoints.

One thing writes **outside** the repo: with `knowledge.adapter: "kartoteka"` configured
([config.md](config.md)), the `knowledge_mirror` hook posts each deliberation artifact to that
daemon's artifact store as it is written. It is additive — the files under `<specs.dir>` stay
primary — and it never blocks a write or fails a run. Every attempt is logged to
`.artel/run/.hooks/knowledge-mirror.log`; with the default `adapter: "none"` nothing is sent.

The same key admits two skills a person runs from the conversation:
[`knowledge`](skills-reference.md#knowledge) reads the index and writes nothing;
[`tasks`](skills-reference.md#tasks) writes queue rows — for `add`, always by way of
`tasklist.md` and the mirror, so the file the implementer falls back to never lags the queue.

### Modes and the risk floor

A run resolves to one of three modes, ranked `yolo` < `plan-gate` < `full-gates`
(`autonomous-run.md §10`):

- **`plan-gate`** (default) — one approval pause, HITL tags pause, the PR gate pauses.
- **`yolo`** — the approval pause and the PR-gate *pause* are skipped (`pr-create` still runs;
  the opened PR is yolo's checkpoint); open questions proceed on their recorded defaults. HITL
  tags, deviation escalations, and cap escalations still pause — those are guardrails, not
  preferences.
- **`full-gates`** — alias for `--step`: legacy per-gate confirmations, no `run-state.json`;
  the run-layer hooks (stop gate, sensitive guard) stay disarmed, while the fast-verify layer
  is config-driven and fires regardless of runs.

You request a mode with `--mode=`; the orchestrator then runs a **risk classifier** over the
candidate file paths in the plan/tasklist and matches them against the sensitive-paths policy
(the plugin's `hooks/sensitive-paths.json`, replaced wholesale by a host
`.artel/sensitive-paths.json` when present). The matched categories set a `forced_floor`, and
the effective mode is the *higher* of what you asked for and that floor — the classifier can
raise the required mode (add supervision), never lower it. A `full-gates` floor (secrets, the
gate config itself) means the run refuses to arm and tells you to use `--step`. The category
names and their globs are in the policy file; the classifier procedure is
`autonomous-run.md §10`.

### The hooks that enforce the contract

Seven hooks — registered across `SessionStart`, `PreToolUse`, `PostToolUse`, and `Stop` — turn
the contract from a promise into enforcement ([hooks/README.md](../hooks/README.md)); the eighth
is the session router ([Turn one](#turn-one-the-router)). In a ticket worktree every hook works
on that worktree, not the main checkout ([worktrees.md](worktrees.md) §7):

- **Run stop gate** (`stop_gate.py`, `autonomous-run.md §8`) — blocks a session from ending
  while a run is `run_active`, not `completed`, and not paused. Waiting on a human (any
  `pause_reason`) is a legitimate stop; it fails open on infra errors and disarms past the
  wall-clock budget.
- **Sensitive-path guard** (`sensitive_guard.py`) — during an armed run, denies edits to a
  policy path whose floor outranks the effective mode, or before `TASKLIST_READY` is in
  `gates_confirmed`. Inert in ordinary (non-run) sessions.
- **VCS guard** (`vcs_guard.py`) — always armed, run or not: denies any call that *writes* to a
  pull-request or issue platform other than the one `vcs.adapter` / `tracker.adapter` declares,
  so a project moved to GitHub cannot post to Bitbucket. Reads stay allowed; an unrecognized
  verb is denied unless `guard.extraReadTools` matches the tool name. It covers `gh` and
  platform-named tools, not `git push` — hooks/README.md spells out the perimeter.
- **Fast-verify** (`session_baseline.py` + `fast_verify_post_edit.py` + `verify_stop_gate.py`)
  — runs `verify.fast` on each edited file (filtered by `verify.surface`) for same-turn
  feedback, and blocks completion while findings *introduced this session* stay red (baselined
  so pre-existing findings don't block). Details, caps, and the escape hatch are in
  hooks/README.md. With no `.artel/config.json` or an empty `verify.fast`, this layer is a
  no-op.
- **Knowledge mirror** (`knowledge_mirror.py`) — optional, driven by `knowledge.adapter` /
  `knowledge.baseUrl` / `knowledge.project` / `knowledge.tokenEnv`: posts each deliberation artifact written under
  `<specs.dir>/<TICKET>/` to a kartoteka artifact store as it is written, under that project. Additive and best-effort — files on disk stay
  primary, nothing blocks, nothing retries, every attempt is logged to
  `.artel/run/.hooks/knowledge-mirror.log`. Inert unless configured.

### The deterministic gate scripts

Three Python scripts under the plugin's `scripts/` expose the quality gates as JSON-envelope
commands with a strict exit-code contract: `0` clean, `1` findings (code issues to fix), `2`
environment error (bad toolchain/invocation — **never** edit app code in response;
`verify.py`'s closed `error.kind` list is `invalid_argument` / `timeout` / `spawn_failed` /
`command_not_found` / `internal_error`; `plan_check.py` adds `plan_not_found`).

- `verify.py [--fast] [--files <p1>,<p2>,…]` — runs the configured `verify.commands` (or
  `verify.fast` with `--fast`) and reports one envelope over all stages; `--files` takes one
  comma-separated list, and commands may carry a `{files}` token that scoped calls replace with
  the changed paths. This is the engine behind the fast-verify hooks; skills run the config
  commands directly.
- `plan_check.py --plan <path|-> [--strict]` (`-` reads stdin — the kartoteka path pipes `spec_store.py get <path>` into it, docs/spec-storage.md §4.2) — resolves every `ref:`/backticked-path anchor in a
  plan against the repo (via `ast-index` when available, else `git grep`) and lists unresolved
  references in `data.unresolved`. The `PLAN_GROUNDED` gate (feature-development gate 3.5) calls
  it with `--strict`.
- `tasklist_tasks.py --tasklist <path|-> --ticket-key <KEY>` (`-` reads stdin — the kartoteka path pipes `spec_store.py get <path>` into it, docs/spec-storage.md §4.2) — parses a tasklist into
  the task rows that mirror it, as JSON: `data.iterations`, and `data.sections` for the four
  fix sections the queue records but never offers. Contacts nothing; the caller makes the
  `task_create` MCP calls. Adds `tasklist_not_found`, `tasklist_malformed` and
  `title_collision` to the shared error kinds. See `docs/task-queue.md`.

## End-to-end walkthrough

Here is one real-shaped run of `/artel:feature-development PROJ-XXXX` in the default `plan-gate`
mode, gate by gate. The pipeline splits into a chatty head (which asks freely), one approval
pause, and a silent autonomous tail.

**Config gate.** Missing `.artel/config.json` → the `setup` interview runs first, then the
pipeline continues. A `bitbucket-mcp` VCS adapter with an empty tool prefix stops here —
config.md's start-time check — rather than failing hours later at the PR stage.

**Set the active ticket.** The orchestrator writes `PROJ-XXXX` to `<specs.dir>/.active_ticket`
and ensures the ticket directory exists.

**Chatty head (gates 0–4.5).** Each gate is skipped when its artifact already exists
(`autonomous-run.md §9`), so a resumed run fast-forwards.

- *Gate 0 — `IDEA_READY`.* [`generate-idea`](skills-reference.md#generate-idea) runs under every
  adapter. With a tracker configured it imports the ticket into `idea.md`; with
  `tracker.adapter: "none"` it seeds `idea.md` from the description file you passed, or asks you
  for a description when you passed none. Gate 2 reads `idea.md`, so the pipeline always seeds it
  here.
- *Gate 0.5 — `DESIGN_ANALYZED`* (only when `design.figma: true` and the idea links a Figma
  design). [`figma-analysis`](skills-reference.md#figma-analysis) maps flows and screens and
  raises mockup discrepancies before requirements are written; no Figma MCP connected → silent
  skip (`autonomous-run.md §13`).
- *Gate 1 — `PRD_READY`.* [`analysis`](skills-reference.md#analysis) runs the upfront interview
  — this is the pipeline's designated chatty stage (`autonomous-run.md §1`), asking in batches
  of up to four questions until the requirements are complete. You see `prd.md` marked
  `Status: PRD_READY`.
- *Gate 2 — `VISION_READY`.* [`generate-vision`](skills-reference.md#generate-vision) drafts
  `vision.md` and ends with its one wholesale Approve / Request-changes checkpoint.
- *Gate 3 — plan drafted.* [`researcher`](skills-reference.md#researcher) then
  [`planner`](skills-reference.md#planner) run **silently** — anything they can't resolve lands
  in `.artel/run/PROJ-XXXX/open-questions.md` with a default (`autonomous-run.md §3`), not a
  question to you. You get `research.md` and `plan.md`.
- *Gate 3.5 — `PLAN_GROUNDED`.* The orchestrator runs
  `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/plan_check.py --plan <plan-path> --strict` (kartoteka path:
  `set -o pipefail; python3 ${CLAUDE_PLUGIN_ROOT}/scripts/spec_store.py get <plan-path> | python3 ${CLAUDE_PLUGIN_ROOT}/scripts/plan_check.py --plan - --strict`) to catch
  hallucinated file/symbol references. Exit 0 proceeds; exit 1 bounces the findings back to
  `planner` to fix and regenerate (bounded — a third failure stops and asks); exit 2 is an
  environment error, a stop-and-ask pointing at setup, never a bounce.
- *Gate 4 — `TASKLIST_READY`.* [`tasklist`](skills-reference.md#tasklist) breaks the plan into
  small checkbox tasks and tags any that need a human `[HITL: …]`. You get `tasklist.md` marked
  `Status: TASKLIST_READY`. (For a phase run, `sync-phases` extracts `phase-<N>/tasks.md` here.)

**THE ONE PAUSE.** In a single `AskUserQuestion`, the orchestrator presents: the plan summary,
the task list with its `[HITL: …]` tags called out, every still-open `open-questions.md` entry
with its proposed default pre-selected as the "(Recommended)" option, and a note that approval
also authorizes the run's checkpoint commits & pushes to `origin`. You **Approve** or
**Request changes**. On approve, any answer that overrides a default is folded back into the
plan/tasklist, the questions flip to resolved, and the plan becomes `Status: PLAN_APPROVED`.
This is the only mid-pipeline approval — after it, the run goes silent.

**Arm the run.** The classifier runs over plan + tasklist (`autonomous-run.md §10`). A
`full-gates` forced floor stops here and tells you to re-run with `--step`. Otherwise the
orchestrator writes `.artel/run/PROJ-XXXX/run-state.json` (`run_active: true`,
`completed: false`, `gates_confirmed: ["TASKLIST_READY"]`, the resolved mode fields), announces
the effective mode and its reasons, and creates `run-journal.md` with the run-start entry.
**"Armed" means exactly this**: `run-state.json` with `run_active: true` — from now the Stop
gate blocks a premature exit (`autonomous-run.md §8`) and the sensitive-path guard enforces
floors on every edit. Immediately after arming, the orchestrator runs the planning checkpoint
(`autonomous-run.md §14`): the ticket's `<specs.dir>/` artifacts are committed and pushed to
`origin` — approval at the ONE PAUSE covers these pushes.

**Autonomous tail (gates 5–10.7).** Now silent. On a multi-phase tasklist the tail loops per
phase (`autonomous-run.md §15`): `.active_ticket` is rewritten to `PROJ-XXXX-N` at each phase
start, and each phase closes with the `verify.commands` gate + a checkpoint commit+push
(`autonomous-run.md §14`) before the next phase begins:

- *Gate 5 — implement.* Loops [`implementer`](skills-reference.md#implementer) over the open
  tasks. Each completion is a short contract pointing at a report under
  `.artel/run/<TICKET_ID>/reports/`; with `review.perTask: true` (config.md) every task's diff
  is also reviewed before the next task starts (`autonomous-run.md §16`) — findings land under
  `## Code Review Fixes`, recorded in the task queue on the queue path, for one fix round, then
  the phase review owns whatever is left.
- *Gate 6 — index.* Optional host index-refresh hook
  ([orchestrator-common.md](orchestrator-common.md) §1); silently absent when the host has not
  wired one up. Refreshing is the only part that is a host hook — how the stages above and
  below *read* an index is [code-navigation.md](code-navigation.md), which needs no wiring.
- *Gate 7 — review.* [`run-reviewer`](skills-reference.md#run-reviewer) writes `review.md`;
  Blocking / Important findings route to an implementer fix round, then a re-review, with capped
  review rounds (`autonomous-run.md §5`).
- *Gate 8 — runtime.* [`run-app`](skills-reference.md#run-app) `--gate` launches the configured
  `runtime.run` command and observes for a `RUNTIME_OK` verdict. No `runtime.run` configured →
  `skipped (not configured)`; `runtime.surface` set and no changed file matches →
  `skipped (no runtime surface)`. A runtime error in app code gets a capped fix round
  (`autonomous-run.md §5`); an environment failure escalates immediately.
- *Gate 9 — QA.* [`qa`](skills-reference.md#qa) generates a verdict; a negative one gets a
  capped fix round (`autonomous-run.md §5`).
- *Gate 10 — docs.* [`docs-update`](skills-reference.md#docs-update) updates docs and the
  CHANGELOG. (Gates 10.5/10.7 — phase write-back and the phase-end checkpoint — close each
  phase.)

**What interrupts the silent tail** is limited to four things mid-tail (`autonomous-run.md §1` —
the fifth, the PR-gate pause, belongs to the close-out below): a **deviation escalation** (the
implementer hit something the plan didn't anticipate and needs a decision —
[deviation-protocol.md](deviation-protocol.md)), a **HITL task** (a pre-declared pause the tags
warned you about), a **cap escalation** (a loop hit its bound — consolidated findings, then
stop), or an **environment error**. Nothing else asks you anything after the pause. Each is
bracketed by a `pause_reason` in `run-state.json` so the Stop gate treats the wait as
legitimate.

**Completion gate + PR gate.** [`validate`](skills-reference.md#validate) confirms every gate is
green (a red one routes back once). Then
[`pr-description`](skills-reference.md#pr-description) *always* regenerates
`pr-description.md` (the branch diff is its input, so skip-if-exists doesn't apply —
`autonomous-run.md §9`). In `plan-gate` the orchestrator pauses once more — "Open the PR now?" —
then [`pr-create`](skills-reference.md#pr-create) commits anything left, pushes, opens the PR
via the configured `vcs.adapter`, and comments the URL on the tracker via `tracker.adapter`.
Finally `run-state.json` flips to `completed: true`, `run_active: false`, and the journal gets
its completion entry.

**The final report** names: the ticket; phases traversed; gates passed; the aggregated
`Deviations:` line (`none` when clean); the loop counters; the QA verdict; runtime status; the
checkpoint commits; the path to `pr-description.md`; PR status (`PR_OPENED`/`PR_EXISTS` URL,
`skipped-manual`, or `pending`); description-sync status; the effective mode and why; external
actions taken unattended; and the path to `run-journal.md` — the append-only record of the whole
run (`autonomous-run.md §11`).

### Variant: `--dry-run` (ticket → work plan)

`/artel:feature-development PROJ-XXXX --dry-run` runs the entire chatty head and the
approval-pause *presentation*, then **stops** — it prints the resolved mode + reasons and the
intended external actions, writes **no** `run-state.json`, and never arms. Use it to get from a
ticket all the way to a fully drafted plan and tasklist, presented for approval, with zero risk
of the autonomous tail starting. (This flag exists only on `feature-development`, not on `dev`.)

### Variant: `--mode=yolo` + headless

`claude -p "/artel:feature-development PROJ-XXXX --mode=yolo" --output-format stream-json
--verbose` runs fully unattended (`autonomous-run.md §12`). In `yolo` the approval pause and the
PR-gate pause are skipped — open questions proceed on their defaults, folded in silently, and
the opened PR is the human checkpoint. The guardrail pauses still fire, but because
`AskUserQuestion` does not exist under `-p`: a **deviation escalation** auto-takes the
implementer's recommended option (journaled as `→ auto-resolved`), while **HITL tags** and
**cap escalations** are *never* auto-resolved — the run journals the entry, sets the
`pause_reason`, and stops for you to resume interactively. Headless requires a curated
`permissions.allow` in the host's Claude Code settings (never `--dangerously-skip-permissions`);
a stall on a missing permission is the guardrail working — the fix is a deliberate allowlist
extension, not a bypass. Artifacts and the journal survive a crash, so re-invoking the same
command resumes.

### Variant: `--step` (legacy supervision)

`/artel:feature-development PROJ-XXXX --step` (alias `--mode=full-gates`) restores per-gate
supervision: confirmations between major phases and per-task implementer approval
(`autonomous-run.md §6`). No `run-state.json` is written and the Stop gate and sensitive guard
stay **disarmed** — you are the gate. This is the required mode when the risk floor forces
`full-gates` (secrets, the gate config itself).

## Recipes

Each à-la-carte flow below lists its preconditions, the exact command, what it produces, and
what to run next. Run all of these from the host repo root.

1. **Idea from the tracker.** *Pre:* `tracker.adapter` is `"github-issues"` (authenticated `gh`)
   or `"jira-mcp"` (connected MCP, `tracker.mcpToolPrefix` set). *Run:*
   `/artel:generate-idea PROJ-XXXX`. *Produces:* `<specs.dir>/PROJ-XXXX/idea.md`; sets
   `.active_ticket`. With `tracker.adapter: "none"` it gathers the description from an argument
   or from you instead. *Next:* `/artel:analysis PROJ-XXXX`, or hand the whole thing to
   `/artel:feature-development PROJ-XXXX`. See
   [generate-idea](skills-reference.md#generate-idea).

2. **PRD only.** *Pre:* `idea.md` exists (or a description file). *Run:*
   `/artel:analysis PROJ-XXXX`. *Produces:* `prd.md` (`Status: PRD_READY`) after the interview
   batches. *Next:* `/artel:generate-vision PROJ-XXXX`. See
   [analysis](skills-reference.md#analysis).

3. **Vision only.** *Pre:* `idea.md` exists; `prd.md` recommended (it warns and drafts from the
   idea alone if absent). *Run:* `/artel:generate-vision PROJ-XXXX`. *Produces:* `vision.md`
   (`Status: VISION_READY`) after one Approve/Request-changes checkpoint. *Next:*
   `/artel:researcher PROJ-XXXX` or `/artel:generate-tasklist PROJ-XXXX`. See
   [generate-vision](skills-reference.md#generate-vision).

4. **Research + plan.** *Pre:* `prd.md` (and ideally `vision.md`) exist. *Run:*
   `/artel:researcher PROJ-XXXX` then `/artel:planner PROJ-XXXX`. *Produces:* `research.md`,
   then `plan.md` (`Status: PLAN_DRAFTED`/`PLAN_APPROVED`); unresolved questions go to
   `.artel/run/PROJ-XXXX/open-questions.md`, neither skill pauses. *Next:* ground the plan
   (recipe 5), then `/artel:tasklist PROJ-XXXX`. See
   [researcher](skills-reference.md#researcher) and [planner](skills-reference.md#planner).

5. **Plan grounding check.** *Pre:* `plan.md` exists. *Run:*
   `python3 <plugin-root>/scripts/plan_check.py --plan specs/.current/PROJ-XXXX/plan.md
   --strict` (kartoteka path: `set -o pipefail; python3 <plugin-root>/scripts/spec_store.py get specs/.current/PROJ-XXXX/plan.md |
   python3 <plugin-root>/scripts/plan_check.py --plan - --strict`). *Produces:* a JSON envelope — exit 0 (all anchors resolve), exit 1
   (`data.unresolved` lists hallucinated refs), exit 2 (environment error). *Next:* on exit 1,
   feed the unresolved list back to `/artel:planner PROJ-XXXX` (declare intended new files as
   `new:`), regenerate, re-check.

6. **Tasklist.** *Pre:* full PRD/plan chain (`/artel:tasklist`) **or** just `idea.md` +
   `vision.md` (`/artel:generate-tasklist`). *Run:* `/artel:tasklist PROJ-XXXX` from a plan, or
   `/artel:generate-tasklist PROJ-XXXX` from idea+vision. *Produces:* `tasklist.md`
   (`Status: TASKLIST_READY`) with HITL tags. *Next:* `/artel:implementer PROJ-XXXX`, or arm a
   run with `/artel:dev PROJ-XXXX`. See [tasklist](skills-reference.md#tasklist) and
   [generate-tasklist](skills-reference.md#generate-tasklist).

7. **Phased tickets.** *Pre:* a multi-phase `tasklist.md`. *Extract:*
   `/artel:sync-phases PROJ-XXXX` creates the next incomplete phase's `phase-<N>/tasks.md` and
   updates `**Current Phase:** N`. *Run phase N:* `/artel:feature-development PROJ-XXXX-<N>`
   (or `/artel:dev PROJ-XXXX-<N>`) — the orchestrator auto-extracts at start and, once the
   phase's gates pass, **writes back** completion into `tasklist.md`. *Manual write-back:*
   `/artel:sync-phases PROJ-XXXX`. A ticket-wide `/artel:feature-development PROJ-XXXX` (or
   `/artel:dev PROJ-XXXX`) traverses all remaining phases in one run, checkpoint-committing and
   pushing at each phase boundary (`autonomous-run.md §14–15`); the explicit `-<N>` form still
   runs a single phase. See [sync-phases](skills-reference.md#sync-phases).

8. **Implement one task.** *Pre:* a `tasklist.md`/`phase-<N>/tasks.md` with an open `- [ ]`
   task. *Run:* `/artel:implementer PROJ-XXXX`. *Produces:* the source change, the checkbox
   flipped, and a deviation record if it diverged; returns `HITL: <reason>` instead of
   implementing a HITL-tagged task. *Next:* re-run to take the next task, or
   `/artel:run-reviewer PROJ-XXXX`. See [implementer](skills-reference.md#implementer).

9. **Review loop.** *Pre:* implemented changes on the branch. *Run:*
   `/artel:run-reviewer PROJ-XXXX`. *Produces:* `review.md` with `**Review round:** N` and, in
   ticket mode, a `## Code Review Fixes` write-back to the tasklist, recorded as fix rows in the
   task queue when kartoteka is on (`/artel:tasks list` shows them). *Next:* implement the fix
   tasks, then re-run (capped review rounds — `autonomous-run.md §5`). See
   [run-reviewer](skills-reference.md#run-reviewer).

10. **Runtime gate alone.** *Pre:* `runtime.run` configured; a buildable app. *Run:*
    `/artel:run-app --gate`. *Produces:* a GREEN/RED verdict in `runtime/observation.md`
    (relaunches fresh for a clean baseline, stops the app after). *Next:* on RED from app code,
    fix and re-run; a stale green is not trusted — re-run if the observation predates your last
    relevant change. See [run-app](skills-reference.md#run-app).

11. **QA.** *Pre:* implemented, reviewed changes. *Run:* `/artel:qa PROJ-XXXX`. *Produces:*
    `qa.md` with a verdict (generated, never paused on). *Next:* on a negative verdict, one
    implementer fix round then re-run. See [qa](skills-reference.md#qa).

12. **Validate (gate status).** *Pre:* a ticket with artifacts. *Run:*
    `/artel:validate PROJ-XXXX`. *Produces:* a read-only report of which gates are green/red
    (`PRD_READY`, `PLAN_APPROVED`, `TASKLIST_READY`, `REVIEW_OK`, `RUNTIME_OK`, …); writes
    nothing. *Next:* address whatever is red. See [validate](skills-reference.md#validate).

13. **PR description.** *Pre:* work on a feature branch, diffable against the base. *Run:*
    `/artel:pr-description PROJ-XXXX`. *Produces:* `pr-description.md`, styled from recent
    merged PRs (fetched via `vcs.adapter`); prompt-free, and it *always* regenerates (the branch
    diff is the input). *Next:* review it, then `/artel:pr-create PROJ-XXXX`. See
    [pr-description](skills-reference.md#pr-description).

14. **PR create (standalone — it commits).** *Pre:* the branch is ready and you intend to push;
    the configured `vcs.adapter` (`gh` CLI or Bitbucket MCP) is authenticated. *Run:*
    `/artel:pr-create PROJ-XXXX`. *Produces:* a commit + push (only when the tree is dirty), the
    PR, and a tracker comment with the URL — or `pr-pending.md` on an identity/auth failure. It
    is **idempotent** (a clean tree skips the commit; an existing open PR is only re-commented)
    but it *is* the step that writes to remotes — **read its report carefully** to see exactly
    what it committed/pushed/opened. *Next:* nothing; the loop is closed. See
    [pr-create](skills-reference.md#pr-create).

15. **Regenerating after upstream changes.** *Pre:* an artifact went stale (plan changed under a
    tasklist, a review round is exhausted, etc.). *Run:* re-invoke the producing skill — most
    gates honor skip-if-exists (`autonomous-run.md §9`), so **delete the stale artifact first**
    to force a rebuild. In particular, deleting `review.md` resets the review round counter
    (`autonomous-run.md §5`) so the loop starts fresh; deleting `pr-description.md` is
    unnecessary (it always regenerates). *Next:* re-run the affected gate.

16. **Parallel tickets in worktrees.** *Pre:* a ticket to start, or one already on its branch in
    the main checkout. *Run:* `/artel:init-branch PROJ-XXXX` and answer **Move to
    `.claude/worktrees/PROJ-XXXX`** — or `/artel:move-to-worktree PROJ-XXXX` for a ticket already
    on its branch. *Produces:* the ticket's branch in `.claude/worktrees/PROJ-XXXX` with its
    uncommitted work, artel config and run state; this session continues there, and the main
    checkout is free for a new session on another ticket. *Next:* work the ticket as usual; when
    it is done, `/artel:return-from-worktree PROJ-XXXX` hands the branch back to the main
    checkout (which must have no uncommitted changes) and removes the worktree, keeping the
    branch. What moves and what doesn't: [worktrees.md](worktrees.md). See
    [move-to-worktree](skills-reference.md#move-to-worktree) and
    [return-from-worktree](skills-reference.md#return-from-worktree).

## Troubleshooting & recovery

| Symptom | What it means | What to do |
|---|---|---|
| A worktree move or hand-back reports **`conflict`** | The uncommitted work was stashed but did not apply where it was going; the stash is kept ([worktrees.md](worktrees.md) §6) | Resolve the conflicts in the path the report names, then `git stash drop` the stash entry it names. Nothing was lost. An **`error`** that names a `stash` means the same work is kept there: `git stash apply <stash>` restores it |
| Edit **denied** naming a sensitive-paths category and floor | The sensitive-path guard is armed and the path's floor outranks the effective mode, or `TASKLIST_READY` isn't confirmed yet (hooks/README.md, `autonomous-run.md §10`) | Re-run at or above the required mode, or use `--step` for a `full-gates` floor. Never lower the floor. |
| Session won't end: **"run incomplete"** block | Run stop gate: `run-state.json` is `run_active: true`, `completed: false`, `pause_reason: null` (`autonomous-run.md §8`) | Let the run finish, or if it legitimately paused, have the orchestrator set the matching `pause_reason` (hand-edit `.artel/run/<TICKET_ID>/run-state.json` only as a fallback, when no session is live — same as the abort row). A truly finished run should already have `completed: true`. |
| Run stops with a **consolidated findings report** | A capped loop hit its bound (verify/review/runtime/QA, or the global correction budget — `autonomous-run.md §5`) | Read the consolidated findings, give guidance, and resume. Counters reset only on that user-guided resume; for the review loop specifically, deleting `review.md` resets its round counter. |
| Stop pointing at **setup** (missing tool, exit 2) | An environment error — the verify envelope returned exit 2, which is never a code finding | Fix the toolchain/invocation, not app code. Do **not** enter a fix loop — the pipeline won't either. |
| Guard/Stop hooks seem **inert** on an old run | `run-state.json`'s `started_at` is older than the wall-clock budget (`WALL_CLOCK_HOURS`, `autonomous-run.md §2`) and is treated as stale | Resume by re-invoking the same entry-point command; the orchestrator rewrites `started_at` and re-arms (re-deriving the mode fields — never trusting stale ones). |
| Need to **abort** a run cleanly | You want to stop for good, not pause | Tell the running orchestrator to abort — it writes `run-state.json` with `pause_reason: "user-abort"`, `run_active: false` (`autonomous-run.md §2`), and the Stop gate then allows the session to end. Hand-edit `.artel/run/<TICKET_ID>/run-state.json` yourself only as a fallback, when no session is live to ask. |
| Fast-verify Stop gate **keeps blocking** on findings you can't clear right now | Findings introduced this session stay red (hooks/README.md) | Fix them (preferred), or use the escape hatch: delete `.artel/run/.hooks/baseline-<session_id>.json` so the next Stop re-baselines and passes. |
| Headless run **stalled on a permission** | A tool call isn't in `permissions.allow`; the guardrail refused to self-widen (`autonomous-run.md §12`) | Extend the allowlist in the host's Claude Code settings **deliberately** (a reviewed edit, approved interactively), then resume. Never `--dangerously-skip-permissions`. |
| Checkpoint stops: **push rejected / on default branch** | The `autonomous-run.md §14` branch guard or a non-fast-forward push — checkpoints never force-push and never commit to the default branch | Reconcile the branch manually (pull/rebase, or switch to a feature branch), then resume the run. |
| Every gate reports **skipped** | The config's defaults are inert — empty `verify.commands`, no `runtime.run`, `design.figma: false` (config.md) | Configure the gates you want armed via `/artel:setup` or by editing `.artel/config.json`. Skipped is honest, not green. |
