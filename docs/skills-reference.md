# Skills reference

Structured per-skill reference for the artel workflow (`skills/<name>/SKILL.md`). For the
end-to-end walkthrough — how the entry points, pipeline sub-skills, and gates fit together — see
the [workflow guide](workflow-guide.md). This document is the lookup table: one entry per skill,
six fixed fields.

- **Invocation** is copied verbatim from each skill's SKILL.md frontmatter hint, in slash form
  (`/artel:<skill-name> ...`). The equivalent `Skill` tool call (`skill: "artel:<skill-name>"`,
  `args: "..."`) works identically for agents driving these skills programmatically — this is
  noted once, here, not per entry. On OpenCode the same skills are named `artel-<name>`
  (commands `/artel-<name>`); see [opencode.md](opencode.md).
- Section citations below (`autonomous-run.md §N`) refer to [autonomous-run.md](autonomous-run.md);
  ticket-parsing citations refer to [ticket-parsing.md](ticket-parsing.md); config keys
  (`verify.commands`, `tracker.adapter`, …) are defined in [config.md](config.md); hook
  citations refer to [hooks/README.md](../hooks/README.md). Schemas, caps, enums, and glob
  lists live in those documents — they are cited, not repeated here.
- `<specs.dir>` is the configured spec-trail root (default `specs/.current`); `<TICKET_ID>` and
  `<N>`/`<PHASE_NUM>` follow ticket-parsing.md.

Entry template:

```markdown
### <skill-name>

- **Purpose:** <one sentence — what it produces, not how>
- **Invocation:** `/artel:<skill-name> <args per its frontmatter hint, verbatim>`
- **Reads:** <input artifacts / preconditions, with paths>
- **Writes:** <output artifacts + status markers, with paths>
- **Pauses:** <when it asks the user; "never" for silent pipeline skills>
- **Notes:** <standalone-vs-pipeline behaviour, skip-if-exists interaction (autonomous-run.md §9), config keys, phase awareness>
```

Paths in the **Reads** / **Writes** lines are logical: with kartoteka as the spec store they are kartoteka documents, addressed per [spec-storage.md](spec-storage.md) §3.

---

## Session router

### using-artel

- **Purpose:** Route ticket, feature, queue and knowledge requests to the matching `/artel:`
  skill before any other response — the plugin's analogue of `superpowers:using-superpowers`.
- **Invocation:** `/artel:using-artel` — normally never typed: the `using_artel` `SessionStart`
  hook ([hooks/README.md](../hooks/README.md)) injects its body (frontmatter stripped) plus a
  host status on `startup|clear|compact` whenever `.artel/config.json` exists.
- **Reads:** nothing itself; the hook reads `.artel/config.json` (`knowledge.adapter`,
  `knowledge.baseUrl`, `knowledge.project`, `knowledge.tokenEnv`, `specs.dir`) and `<specs.dir>/.active_ticket`, and checks whether the
  `ast-index` CLI is on PATH.
- **Writes:** nothing.
- **Pauses:** never.
- **Notes:** a routing table over every other skill, kept complete both ways by
  `tests/test_using_artel_docs.py` and capped at 10 KiB because every session pays for it.
  Carries `<SUBAGENT-STOP>` so dispatched agents ignore it. States precedence: user
  instructions > artel skills for artel's domain > generic process skills; an entry point is a
  complete process and is never wrapped in brainstorming or plan-writing skills. Lists no
  agents — every agent is reached through its skill. Not injected without a config. Routes
  code navigation — find a class, its usages or callers, the project's structure — to the
  `ast-index` plugin's `/ast-index:ast-index` (and index creation to `/ast-index:initialize`)
  only when the status line says the CLI is on PATH; the index is then the first search tool,
  before grep.

---

## Entry points

### feature-development

- **Purpose:** End-to-end autonomous orchestrator that carries a ticket from idea through PRD,
  vision, plan, tasklist, implementation, review, runtime check, docs (once per ticket), and PR — with exactly
  one approval pause.
- **Invocation:** `/artel:feature-development [ticket-id] or [ticket-id]-[phase] [description-file] [--mode=yolo|plan-gate|full-gates] [--dry-run] [--local]`
- **Reads:** `.artel/config.json` (missing → invokes `setup` first); whichever ticket artifacts
  already exist (`idea.md`, `prd.md`, `vision.md`, `plan.md`, `tasklist.md`) — skip-if-exists
  governs each gate; `.artel/run/<TICKET_ID>/open-questions.md`; `run-state.json` /
  `run-journal.md` on resume.
- **Writes:** `<specs.dir>/.active_ticket`, `.artel/run/<TICKET_ID>/run-state.json`,
  `run-journal.md`, `runtime-observation.md` (the runtime-retry counter — run bookkeeping,
  distinct from `run-app`'s `runtime/observation.md` evidence in the spec trail), the
  `open-questions.md` status flips, and the description-file sync — everything else (`prd.md`,
  `vision.md`, `plan.md`, `tasklist.md`, `review.md`, `pr-description.md`, …) is
  delegated to sub-skills/agents. Also performs the checkpoint commits & pushes to `origin`
  (autonomous-run.md §14: planning + one per completed phase).
- **Pauses:** one plan+tasklist approval pause (skipped entirely in `yolo`, per
  autonomous-run.md §10); mid-run HITL tasks, deviation escalations, and loop-cap escalations;
  the PR-gate confirmation in `plan-gate` mode (proceeds without pausing in `yolo`).
- **Notes:** `--mode=yolo|plan-gate|full-gates` resolves per autonomous-run.md §10 (the risk
  classifier may raise the effective mode, never lower it); `--dry-run` runs only the chatty
  head + the approval-pause presentation, prints the resolved mode + reasons and the intended
  external actions, writes no `run-state.json`, and never arms — **this flag exists only on
  `feature-development`, not on `dev`**. `--step` is legacy per-gate confirmation mode (alias
  `--mode=full-gates`, autonomous-run.md §6): no `run-state.json`, hooks stay disarmed. Every
  worker-skill gate follows skip-if-exists (autonomous-run.md §9); `pr-description` is the
  deliberate exception, always regenerated at close-out. Gate 3.5 (`PLAN_GROUNDED`) runs
  `scripts/plan_check.py --strict` with a bounded bounce loop
  (`MAX_PLAN_CHECK_BOUNCES = 2`). Multi-phase tasklists are traversed phase-by-phase in one run
  (autonomous-run.md §15): `.active_ticket` is rewritten at each phase start and a checkpoint
  commit+push closes each phase; `pr-create` then finds a clean tree and only opens the PR. The
  run records the checkpoint gate's baseline once at arm time and confirms the completion
  checklist itself (autonomous-run.md §7); QA and validate are not pipeline stages since 0.18.0.

### dev

- **Purpose:** Lean autonomous implementation loop for straightforward work — implement, review,
  and runtime-gate a confirmed work list, with no PRD/plan/docs artifacts.
- **Invocation:** `/artel:dev [ticket-id] or [ticket-id]-[phase] [description-file] [--mode=yolo|plan-gate|full-gates]`
- **Reads:** `.artel/config.json` (missing → invokes `setup` first); an existing tasklist or
  `phase-<N>/tasks.md` with incomplete tasks; else `idea.md` + `vision.md`; else `$1`/an inline
  description.
- **Writes:** `<specs.dir>/.active_ticket`, `.artel/run/<TICKET_ID>/run-state.json`,
  `run-journal.md`, `runtime-observation.md`, the work-list tasklist written during the input
  ladder, and the description-file sync. Also performs the checkpoint commits & pushes to
  `origin` (autonomous-run.md §14: work-list + one per completed phase), sweeping spec-trail
  images into kartoteka first on that path (spec-storage.md §4.6).
- **Pauses:** one work-list confirmation — it happens even when an existing tasklist is found
  (skipped in `yolo`; genuine ambiguity on the description-only input path still asks even in
  `yolo`); mid-run HITL tasks, deviation escalations, and loop-cap escalations. No PR gate — commits/pushes happen at the §14 checkpoints; only opening the PR
  remains manual (`dev` never invokes `pr-create`).
- **Notes:** `--mode` resolves the same way as `feature-development` (autonomous-run.md §10);
  `--step` is the legacy per-gate mode (alias `--mode=full-gates`, §6). **No `--dry-run` flag**
  — that is a `feature-development`-only capability. No PRD/plan/docs gates exist on this path.
  Multi-phase tasklists are traversed phase-by-phase in one run (autonomous-run.md §15)
  with a checkpoint commit+push per phase.

### setup

- **Purpose:** One-time configuration interview that creates or revises `.artel/config.json` —
  ticket grammar, tracker/VCS adapters, verify commands, languages, optional extras.
- **Invocation:** `/artel:setup`
- **Reads:** `.artel/config.json` (existing values pre-selected on Revise); the config contract
  ([config.md](config.md)); `hooks/sensitive-paths.json` when the policy scaffold is selected.
- **Writes:** `.artel/config.json` — one complete, explicit file in config.md's filled-example
  shape (an aborted interview writes nothing); optionally `.artel/sensitive-paths.json`
  scaffolded from the shipped defaults (never overwrites an existing one); `.gitignore` entries
  for `.artel/run/` and `.artel/context/`.
- **Pauses:** the whole skill is an interview (`AskUserQuestion`, ≤4 questions per round:
  ticket grammar → adapters → quality gate + languages → optional extras); a
  present-and-parseable config asks Revise/Abort first, a broken one asks Recreate/Abort.
- **Notes:** worker, not orchestrator — runs inline. Invoked automatically by
  `feature-development`/`dev` when no config exists; run manually to create or revise. Reports
  which gates are armed vs will record `skipped`, and reminds that `.artel/config.json` is
  committed team configuration (checkpoint commits never stage `.artel/`).

---

## Pipeline sub-skills

### generate-idea

- **Purpose:** Turn a tracker ticket (or a directly gathered description) into the seed
  artifact of the workflow — a structured idea file in `language.docs`.
- **Invocation:** `/artel:generate-idea [ticket-id] [description-file]`
- **Reads:** `tracker.adapter` / `tracker.mcpToolPrefix` / `language.docs`; the ticket via
  `<tracker.mcpToolPrefix>jira_get_issue(_comments)` (`jira-mcp`) or `gh issue view`
  (`github-issues`); `$1` or the user's description under `"none"`; a pre-flight check for an
  existing idea file.
- **Writes:** `<specs.dir>/<TICKET_ID>/idea.md` (from the skill's bundled template); sets
  `<specs.dir>/.active_ticket`.
- **Pauses:** when `idea.md` already exists (Overwrite / Abort) — manual invocation only;
  pipeline runs skip-if-exists (autonomous-run.md §9). Under `tracker.adapter: "none"` with no
  description file it asks for the description (the same input gate as `analysis`).
- **Notes:** worker, not orchestrator — runs inline (like `sync-phases`). Fails fast and writes
  nothing on a tracker fetch error; an unusable `jira-mcp` adapter falls back to an existing
  `idea.md` when present, else stops and asks. Natural-language content is translated to
  `language.docs`; technical artifacts (code, URLs, identifiers) are preserved verbatim. Ideas
  are ticket-level only — a phase suffix is ignored and noted in the report.

### figma-analysis

- **Purpose:** Analyze the ticket's Figma design workflow — flow graph, screen-to-code mapping,
  form-factor differences — and surface mockup errors before requirements are written.
- **Invocation:** `/artel:figma-analysis [ticket-id] [figma-url]`
- **Reads:** `design.figma` (must be `true`); `idea.md` (design URLs auto-extracted; an explicit
  URL argument wins); the Figma file via the connected Figma MCP; the codebase.
- **Writes:** (via the agent) `<specs.dir>/<TICKET_ID>/design-analysis.md`
  (`Status: DESIGN_ANALYZED`, or `Status: DESIGN_BLOCKED` when findings are parked) and
  `<specs.dir>/<TICKET_ID>/design/` screenshots (kartoteka path: swept into kartoteka, viewed
  with `image fetch`; files path: files, as before). Never `.active_ticket`.
- **Pauses:** on Major findings (the discrepancy handshake: apply correction / proceed as
  designed / park for designer) and on the manual Overwrite/Abort prompt. Minor findings flow
  into the analysis interview instead.
- **Notes:** orchestrator (dispatches the `figma-analyst` agent). Conditional gate 0.5 of
  `feature-development` — auto-skips when the ticket has no `figma.com/design` link or
  `design.figma` is off. Figma MCP absence is a **silent skip**, not an error — the stage is
  runtime-optional (autonomous-run.md §13). A "Park for designer" answer stops the pipeline
  with `DESIGN_BLOCKED`. Ticket-level only (phase suffix ignored).

### analysis

- **Purpose:** Run the upfront requirements interview and draft the ticket's PRD.
- **Invocation:** `/artel:analysis [ticket-id] or [ticket-id]-[phase] [description-file] [--local]`
- **Reads:** `idea.md` (or the `$1` description file) as the interview seed;
  `design-analysis.md` when present; the `analyst` agent explores the codebase before asking
  anything, and consults the institutional-knowledge index when `knowledge.adapter` is
  `kartoteka` and its MCP tools are in the session (config.md; `--local` forces this off for
  one run). Answers found there close Resolved Questions with a citation instead of being asked.
- **Writes:** (via the agent) the PRD at the phase-aware path (ticket-parsing.md §4) with
  `Status: PRD_READY`; `<specs.dir>/.active_ticket`.
- **Pauses:** repeatedly, via `AskUserQuestion`, in batches of up to 4 questions until the agent
  returns `INTERVIEW_COMPLETE` — this is the pipeline's designated chatty stage
  (autonomous-run.md §1); the input gate stops and asks (never guesses) when neither `idea.md`
  nor a description file exists, or the description is too vague.
- **Notes:** orchestrator per the skill-orchestrator contract — delegates all PRD writing to the
  `analyst` agent. Never reads `vision.md`; phase scope and never-overwrite rules live in
  ticket-parsing.md §4–§5 and §7.

### generate-vision

- **Purpose:** Draft the host project's technical vision document from the idea and PRD.
- **Invocation:** `/artel:generate-vision [ticket-id] [idea-file]`
- **Reads:** `idea.md` (`$1` override; must exist), `prd.md` (warns and continues, drafting from
  the idea alone, if missing) — never re-asking anything the PRD's Resolved Questions already
  answer.
- **Writes:** (via the agent) `<specs.dir>/<TICKET_ID>/vision.md` with `Status: VISION_READY`.
- **Pauses:** at most one clarifying-question round (≤4 questions, only where idea+PRD are
  silent) plus one wholesale Approve/Request-changes checkpoint on the finished document; on
  manual invocation, an Overwrite/Abort prompt when `vision.md` already exists (pipeline runs
  skip-if-exists, autonomous-run.md §9).
- **Notes:** ticket-level only — a phase suffix is ignored with a printed note. One agent
  (`vision-writer`), one draft, one checkpoint — no per-section approvals.

### researcher

- **Purpose:** Gather codebase/technical context and produce the ticket's research document.
- **Invocation:** `/artel:researcher [ticket-id] or [ticket-id]-[phase] [--local]`
- **Reads:** PRD (phase-scoped with ticket-wide fallback), `idea.md`, `vision.md`, and the phase
  tasks file when one exists; the codebase (scan only); and the institutional-knowledge index
  when `knowledge.adapter` is `kartoteka` and its MCP tools are in the session (config.md;
  `--local` forces this off for one run).
- **Writes:** (via the agent) `research.md` (or `phase-<N>/research.md`), whose **Prior
  Decisions** section records what the knowledge index held — or states that nothing was found,
  or that consultation did not happen and why; unresolved questions to
  `.artel/run/<TICKET_ID>/open-questions.md` (`from: researcher`) with proposed defaults.
- **Pauses:** never — this skill never asks the user (autonomous-run.md §3); questions are
  recorded with defaults and research proceeds on them.
- **Notes:** orchestrator (dispatches the `researcher` agent in a phased
  extract-questions-then-research model); refuses per the phase-ambiguity rule
  (ticket-parsing.md §5) when `PHASE_NUM` is unset but `phase-*/` subfolders already exist;
  never overwrites the ticket-wide `research.md` from a phase-scoped run; read-only — no code
  changes.

### planner

- **Purpose:** Draft the architecture and implementation plan for the ticket.
- **Invocation:** `/artel:planner [ticket-id] or [ticket-id]-[phase]`
- **Reads:** PRD, `research.md`, the host project's conventions docs (its CLAUDE.md and whatever
  it points to), `idea.md`, `vision.md`, and the phase tasks file when one exists.
- **Writes:** (via the agent) `plan.md` (or `phase-<N>/plan.md`) with
  `Status: PLAN_APPROVED`/`PLAN_DRAFTED`; optionally `adr.md`; unresolved questions to
  `.artel/run/<TICKET_ID>/open-questions.md` (`from: planner`), with affected plan decisions
  marked `(provisional — Q<n>)`.
- **Pauses:** never — proceeds on proposed defaults, marking affected decisions provisional
  (autonomous-run.md §3).
- **Notes:** orchestrator (dispatches the `planner` agent); refuses per the phase-ambiguity rule
  (ticket-parsing.md §5); re-invoked by `feature-development`'s `PLAN_GROUNDED` gate (3.5) to
  resolve `plan_check.py` findings, and at the approval-pause fold-back when an answer overrides
  a recorded default.

### tasklist

- **Purpose:** Break the approved plan down into small, trackable checkbox tasks.
- **Invocation:** `/artel:tasklist [ticket-id] or [ticket-id]-[phase] [--local]`
- **Reads:** the plan and its upstream inputs, resolved internally by the `task-planner` agent.
- **Writes:** (via the agent) `tasklist.md` (or the phase tasks file) with
  `Status: TASKLIST_READY` and HITL tags (autonomous-run.md §4); unresolved questions to
  `.artel/run/<TICKET_ID>/open-questions.md` (`from: tasklist`).
- **Pauses:** never.
- **Notes:** thin orchestrator — the `task-planner` agent owns input/output paths, format, and
  rules, so this skill does not restate them. Requires the full PRD/plan chain; the lean
  idea+vision-only path is `generate-tasklist`, not this skill.

### generate-tasklist

- **Purpose:** Produce the ticket's iterative tasklist directly from idea + vision, skipping the
  PRD/plan chain used by the full pipeline.
- **Invocation:** `/artel:generate-tasklist [ticket-id] [idea-file] [vision-file]`
- **Reads:** `idea.md`, `vision.md` (both overridable via `$1`/`$2`); errors if the resolved
  vision file is missing, pointing at `/artel:generate-vision`.
- **Writes:** (via the agent) `<specs.dir>/<TICKET_ID>/tasklist.md` (Progress Report table,
  numbered Iterations, file-grouped checkbox tasks, `**Test:**` footer per iteration).
- **Pauses:** one questions+approval round (`AskUserQuestion`: Approve / Request changes /
  Abort) — this doubles as `dev`'s mini-interview and single work-list confirmation; an
  Overwrite/Abort prompt on manual invocation when `tasklist.md` already exists (pipeline runs
  skip-if-exists, autonomous-run.md §9).
- **Notes:** ticket-level only — a phase argument is ignored with a printed note ("iterations
  are the phases"). One agent (`tasklist-writer`), one draft — never a second agent spawn;
  never writes the file itself, only the agent does.

### implementer

- **Purpose:** Implement the next incomplete tasklist task, verify it, and flip its checkbox.
- **Invocation:** `/artel:implementer [ticket-id] or [ticket-id]-[phase] [--local]`
- **Reads:** on the queue path a `task_ready` claim from kartoteka, else the phase tasks
  file or `tasklist.md` (first `- [ ]` task in scope) — `--local` forces the file
  ([task-queue.md](task-queue.md) §1); `idea.md`, `vision.md`.
- **Writes:** (via the agent) the source changes for the task; the tasklist checkbox and
  Progress Report; on the queue path, a fix-section task's row moved by `task_update` alone —
  never claimed ([task-queue.md](task-queue.md) §3); deviation records per
  [deviation-protocol.md](deviation-protocol.md); the
  task's report — diff, verify evidence, decisions — at
  `.artel/run/<TICKET_ID>/reports/NNN-<slug>.md`, so the completion message itself stays a
  short contract (task, changed paths, `Report:` path, `Verify iterations:`, `Deviations:`).
- **Pauses:** never directly on completion — returns `HITL: <reason>` for the caller to pause
  on; a `DEVIATION` report is presented by the skill itself via `AskUserQuestion` (recommended
  option first, Abort-task always offered) — pipeline callers only bracket that call with
  `pause_reason` set/clear.
- **Notes:** single-phase autonomous model — implements directly, no proposal/approval
  round-trip. The verify loop is the composed `inner-loop` procedure over the task gate —
  `verify.fast` on the changed paths, `verify.test` on the changed tests ([gates.md](gates.md)
  §1) — capped at `MAX_VERIFY_ITERATIONS` (autonomous-run.md §5); the whole-tree gate is the
  orchestrator's checkpoint. A task tagged `[HITL: …]` is never
  implemented directly — the skill stops and returns control instead. When a change's effect
  isn't obvious from tests alone the agent may launch the app via the `run-app` flow
  (`runtime.run` configured) — an on-demand check, not the `RUNTIME_OK` gate.

### inner-loop

- **Purpose:** Run the bounded verify→fix→re-verify loop until the configured quality gate is
  green, leaving JSON evidence per iteration.
- **Invocation:** `/artel:inner-loop [paths to scope the gate, comma-separated]`
- **Reads:** `verify.fast` / `verify.test` (the task gate, [gates.md](gates.md) §1); the
  changed-path set (argument, or derived from `git status`/`git diff`);
  `<specs.dir>/.active_ticket` for evidence pathing.
- **Writes:** minimal code fixes inside the scoped paths;
  `<specs.dir>/<TICKET_ID>/[phase-<N>/]verify/iteration-<i>.json` (the task gate's envelope,
  both stages), `residual.json` on budget exhaustion.
- **Pauses:** stop-and-ask on an environment error (exit-2 class — never "fixed" by editing app
  code), on no-progress between iterations, and on exceeding `MAX_VERIFY_ITERATIONS`
  (autonomous-run.md §5).
- **Notes:** worker, not orchestrator — a fixed procedure, manual/composed only
  (`disable-model-invocation`); the `implementer` agent consumes it by reading this file, since
  subagents do not inherit skills. Empty `verify.fast` or `verify.test` makes that half
  `skipped`, never `green`; the full gate (`verify.commands`) is the checkpoint's, never this
  loop's. Findings outside the
  scoped paths are pre-existing baseline — reported, never fixed.

---

## Quality gates & close-out

### run-reviewer

- **Purpose:** Review the ticket's changes and classify findings as Blocking / Important /
  Nice-to-have; with `--task`, review one task's diff right after its implementer returned.
- **Invocation:** `/artel:run-reviewer [ticket-id] or [ticket-id]-[phase]
  [--task "<task title>" --report <path> --package <path>] [--local]`
- **Reads:** input artifacts and the priority taxonomy, resolved internally by the `reviewer`
  agent (PRD/plan/conventions in ticket mode). In task mode: the task's text from the
  tasklist, the implementer's report and the diff package `scripts/review_package.py` wrote
  (all three flags required — never a fallback to the ticket review).
- **Writes:** (via the agent) `review.md` with `**Review round:** N` (always ticket-level, even
  for phase runs); the machine-readable `review/findings.json` (phase-aware —
  ticket-parsing.md §4); in ticket mode, a tasklist write-back under `## Code Review Fixes`.
  Task mode writes `.artel/run/<TICKET_ID>/reports/NNN-<slug>-review.md` and the same
  `## Code Review Fixes` write-back, and nothing else — no `review.md`, no round bump, no
  lenses. Either mode opens its write-back with a `### <source>` heading, and the skill records
  the batch as fix rows in the task queue unless `--local` or the adapter rules it out
  ([task-queue.md](task-queue.md) §6).
- **Pauses:** never.
- **Notes:** orchestrator (dispatches the `reviewer` agent). Capped at `MAX_REVIEW_ROUNDS`
  (autonomous-run.md §5) — the cap is enforced by the calling orchestrators, which loop it
  against implementer fix rounds until clean or capped. Task mode is the `review.perTask`
  gate of autonomous-run.md §16 (off by default; one fix round, `MAX_TASK_REVIEW_ROUNDS = 1`,
  no per-task re-review — open fix tasks are handed to the phase review). `deep-review` drives
  the same `reviewer` agent in standalone mode once, then the `review-forecaster` agent, for a
  separate, non-pipeline review-and-forecast workflow.

### run-app

- **Purpose:** Build and launch the app via the configured `runtime.run` command and record the
  `RUNTIME_OK` verdict.
- **Invocation:** `/artel:run-app [--gate]`
- **Reads:** `runtime.run`; `<specs.dir>/.active_ticket` for evidence pathing.
- **Writes:** `<specs.dir>/<TICKET_ID>/[phase-<N>/]runtime/observation.md` — date, mode,
  command, exit code, output summary, `Verdict: GREEN | RED (<reason>)`, and the
  `RUNTIME_OK: green | red | skipped (not configured)` marker; reports inline when no ticket is
  active.
- **Pauses:** never — a failed launch retries once, then RED is the verdict (quoting the
  captured output).
- **Notes:** treats `runtime.run` as an opaque, self-reporting black box (the same
  `{command, exit_code, output}` evidence envelope `inner-loop` uses). **`--gate`** (pipeline
  use) is the only mode the `validator` agent's `RUNTIME_OK` gate treats as authoritative;
  interactive mode launches for inspection and leaves the app running. Absent/empty
  `runtime.run` → `skipped (not configured)`, never a blocker. Callers must not trust a stale
  green — re-run unless the observation postdates the last relevant change. UI driving is out
  of scope — that is `drive-app`.

### drive-app

- **Purpose:** Drive the running app's UI via the configured `runtime.drive` command and record
  a verified / not-verified drive observation.
- **Invocation:** `/artel:drive-app`
- **Reads:** `runtime.drive`; `<specs.dir>/.active_ticket` for evidence pathing.
- **Writes:** `<specs.dir>/<TICKET_ID>/[phase-<N>/]runtime/drive-observation.md` — date,
  command, goal, exit code, `Verdict: verified | not verified (<reason>)`.
- **Pauses:** never.
- **Notes:** requires the automation scaffold applied via `add-automation` first — this skill
  never applies it itself, it relays the failure and points the caller there. Interactive
  counterpart to `run-app`; the `RUNTIME_OK` pipeline gate never uses this skill. Absent/empty
  `runtime.drive` → reports `not configured` and stops.

### qa

- **Purpose:** Generate a QA plan and verdict for a ticket, phase, or release.
- **Invocation:** `/artel:qa [ticket-id] or [ticket-id]-[phase] or R-[release-id]`
- **Reads:** scope and paths resolved internally by the `qa` agent (release / ticket / phase).
- **Writes:** (via the agent) `qa.md` (or its phase-scoped variant; release scope:
  `<specs.releases>/<RELEASE_ID>/qa.md`) with a verdict.
- **Pauses:** never — generates a verdict rather than pausing on it.
- **Notes:** orchestrator (dispatches the `qa` agent). Not a pipeline stage since 0.18.0: no
  gate calls it, no fix round follows it; the reviewer's `## PRD acceptance criteria` table is
  the pipeline's record of criteria against evidence. Release identifiers start with `R-` and
  are passed through as-is.

### validate

- **Purpose:** Report which quality gates have passed for a ticket, phase, or release.
- **Invocation:** `/artel:validate [ticket-id] or [ticket-id]-[phase] or R-[release-id]`
- **Reads:** the gate artifacts the `validator` agent already knows about (`PRD_READY`,
  `PLAN_APPROVED`, `TASKLIST_READY`, `IMPLEMENT_STEP_OK`, `REVIEW_OK`, `RUNTIME_OK`,
  `CHECKPOINT_OK`, `DOCS_UPDATED`, `AUTOMATION_REMOVED`).
- **Writes:** nothing — a read-only status report.
- **Pauses:** never.
- **Notes:** orchestrator (dispatches the `validator` agent). Not a pipeline stage since 0.18.0:
  `feature-development`'s completion gate confirms the same facts itself (autonomous-run.md §7)
  and routes a red one back once; this is the à-la-carte report.
  Unconfigured gates (`verify.commands` empty, no `runtime.run`) report `skipped`, never
  `green`.

### docs-update

- **Purpose:** Update project documentation and the CHANGELOG based on the ticket's shipped
  work.
- **Invocation:** `/artel:docs-update [ticket-id] or [ticket-id]-[phase]`
- **Reads:** artifact inputs resolved internally by the `tech-writer` agent.
- **Writes:** (via the agent) `<specs.dir>/<TICKET_ID>/summary.md` (or its phase-scoped
  variant) plus a `CHANGELOG.md` entry.
- **Pauses:** never.
- **Notes:** thin orchestrator — the agent owns its own input/output paths. Runs as
  `feature-development` gate 10 (`DOCS_UPDATED`) once per ticket, on the last phase before its checkpoint
  commit; `dev` has no docs gate.

### pr-description

- **Purpose:** Draft a comprehensive PR description for the ticket, styled to match recently
  merged PRs.
- **Invocation:** `/artel:pr-description [ticket-id]`
- **Reads:** the tracker summary via `tracker.adapter` (Jira MCP or `gh issue view`; `"none"` →
  `TICKET_SUMMARY_UNAVAILABLE`, continue); `idea.md` / `vision.md` / `tasklist.md`; the git log
  and merge-base diff against the base branch; 3–5 recent merged PRs via `vcs.adapter` for
  structural style.
- **Writes:** (via the agent) `<specs.dir>/<TICKET_ID>/pr-description.md`, in `language.pr`.
- **Pauses:** never — prompt-free; every external dependency (tracker, style sample, local
  docs) is optional and degrades to a fallback template rather than blocking.
- **Notes:** the deliberate exception to skip-if-exists (autonomous-run.md §9) — an existing
  file is always regenerated, never skipped, because the branch diff is the input and a file
  from earlier in the run is stale by definition. `PHASE_NUM` is parsed for input compatibility
  only; the description always covers the whole branch. The skill gathers all inputs; only the
  `tech-writer` agent writes the file.

### pr-create

- **Purpose:** Commit, push, open the pull request via the configured `vcs.adapter`, and
  comment the URL on the tracker — the run's loop-closing step.
- **Invocation:** `/artel:pr-create [ticket-id]`
- **Reads:** `pr-description.md` (invokes `pr-description` first if missing); adapter identity
  pre-flight (`gh auth status` / Bitbucket+Jira whoami); `git status` / `branch` / `log`; the
  existing-PR check via `gh pr list` or `<vcs.mcpToolPrefix>bitbucket_list_my_prs`.
- **Writes:** a commit + push (only when the tree is dirty; kartoteka path: sweeps spec-trail
  images into kartoteka first and never commits one — spec-storage.md §4.6), the PR (title
  `<TICKET_ID>: <tracker summary>`, body from `pr-description.md`), a tracker comment with the
  PR URL — or `<specs.dir>/<TICKET_ID>/pr-pending.md` when an adapter identity check fails
  (report and stop, never fabricate).
- **Pauses:** never directly — `feature-development`'s PR gate wraps the invocation with a
  confirmation in `plan-gate` mode only; proceeds without pausing in `yolo`. Stops-and-asks
  only on a missing/malformed Bitbucket remote or an ambiguous default branch.
- **Notes:** **idempotent by design** — a clean `git status --porcelain` skips the commit; an
  existing open PR (matched on branch **and** repo slug for Bitbucket) is never re-created,
  only re-commented when the description changed. **Never-list:** never commits to the default
  branch; never force-pushes; never `git add -A` on `.artel/**`/`<specs.dir>/**` outside the
  ticket's own artifacts; never transitions the ticket's status. Ticket-key scraping is
  restricted to `origin/<default>..HEAD`, never the full base range. Bitbucket
  `projectKey`/`repositorySlug` are derived from `git remote get-url origin` at call time —
  no config key. With `runtime.scaffold` configured, the report reminds to run
  `/artel:remove-automation` before merge when scaffold artifacts appear present.

---

## Utilities

### sync-phases

- **Purpose:** Keep `tasklist.md` and per-phase `phase-<N>/tasks.md` files in sync in both
  directions.
- **Invocation:** `/artel:sync-phases [ticket-id] or [ticket-id]-[phase]`
- **Reads:** `tasklist.md`, every existing `phase-*/tasks.md`, `idea.md`, `vision.md`.
- **Writes:** completion status synced back into `tasklist.md`'s Progress Report and iteration
  checkboxes; a missing `phase-<N>/tasks.md` extracted for the target phase;
  `**Current Phase:** N`.
- **Pauses:** never.
- **Notes:** worker, not orchestrator — runs inline (like `generate-idea`). Invoked
  automatically by `dev`/`feature-development` on phase-scoped runs: at run start (extraction)
  and after a phase's gates pass (write-back); manual invocation remains available for
  hand-repair. Phase tasks files are the source of truth for completion within their phase;
  implementation notes in them are never deleted or overwritten.

### merge-conflicts

- **Purpose:** Resolve git merge conflicts by analyzing both sides and applying an intelligent,
  approved merge.
- **Invocation:** `/artel:merge-conflicts [branch-name]`
- **Reads:** the conflicted files, both branch versions (`git show <branch>:<file>` /
  `git show HEAD:<file>`), `git diff --stat`; the host's optional code-symbol index to locate
  symbols that moved between the two sides ([code-navigation.md](code-navigation.md)),
  silently absent otherwise.
- **Writes:** resolved files, staged but not committed; never hand-edits generated files
  (regenerates via the host's own regeneration command instead).
- **Pauses:** enters plan mode with a per-file resolution strategy for approval before applying
  it; never commits or runs `git merge --abort` without explicit user confirmation.
- **Notes:** standalone utility — no agent, no ticket context. Post-resolution check runs
  `verify.commands` (empty → `skipped`, never green). Base branch defaults to the detected
  `origin` default branch, never a hardcoded name; prefers HEAD's additions when a conflict is
  semantically ambiguous; verifies no conflict markers remain.

### deep-review

- **Purpose:** Review a branch once with the `reviewer` agent, then forecast from kartoteka's
  review history which of its changes will draw reviewer comments, and offer to work the fixes.
- **Invocation:** `/artel:deep-review [ticket-id] [branch] [pr-link] [--local]`
- **Reads:** the `verify.commands` gate output (step 0 — a hard gate when configured); the
  ticket directory; optionally the PR title/body via `vcs.adapter` when a PR link is given;
  `knowledge.adapter` plus the kartoteka MCP tools for the forecast mode
  (`docs/review-forecast.md` §1); `review.forecast.threshold` and `review.forecast.reviewers`.
- **Writes:** `<specs.dir>/<TICKET_ID>/deep-review.md` — the reviewer's comments verbatim, a
  table of definite issues, a table of the remaining changes with a pass percentage and cited
  precedents, proposed fixes for changes under the threshold, and the consultation record. The
  reviewer's own report lands at `.artel/run/<TICKET_ID>/reports/deep-review-findings.md`. On
  apply: `## Code Review Fixes` tasks appended to the ticket-wide `tasklist.md` under a
  `### deep-review-<date>` heading, and recorded as fix rows in the task queue on the queue path.
- **Pauses:** on a `verify.commands` failure (stop and report — review does not proceed; an
  empty list degrades the gate to `skipped` and continues); when `deep-review.md` already
  exists (overwrite?); after the file is written, to ask which fixes to apply (definite issues
  only / definite plus at-risk / none).
- **Notes:** standalone utility, not part of the autonomous pipeline. Orchestrator: dispatches
  the `reviewer` (standalone mode) and then the `review-forecaster`, which always runs — with
  the forecast off (`--local`, adapter not `kartoteka`, or tools absent) the file still carries
  the comments and the definite-issues table, and its `Forecast:` line says why. Applying
  fixes means `Skill: implementer` once per appended task, then `verify.commands` once; no
  re-review loop — re-run the skill to refresh the forecast. Ticket-wide only (phase suffix
  discarded). No PR link → no PR Compliance section in the comments.

### change-digest

- **Purpose:** Generate a self-contained HTML change-comprehension report for a ticket's branch
  work — context, decisions, deviations, module-by-module breakdown — ending with an
  interactive quiz (6–10 questions) the reader must complete.
- **Invocation:** `/artel:change-digest [ticket-id or ticket-id-phase] (optional; defaults to <specs.dir>/.active_ticket)`
- **Reads:** every existing ticket doc (`idea.md`, `prd.md`, `vision.md`, `plan.md`, `adr.md`,
  `research.md`, tasklist / phase tasks, `implementation-notes.md`, `review.md`, `qa.md`,
  `summary.md` — silently skipping what doesn't exist);
  `.artel/run/<TICKET_ID>/open-questions.md`; the branch diff and log vs the default branch;
  the skill's bundled HTML template.
- **Writes:** `<specs.dir>/<TICKET_ID>/change-report.html` (phase-scoped runs:
  `phase-<N>/change-report.html`) — derived, regenerable, **never committed**.
- **Pauses:** never — asks only when no ticket can be resolved at all, or the default branch is
  ambiguous.
- **Notes:** worker, not orchestrator. Report + quiz language follows `language.docs`. Every
  claim must be traceable to the diff or a doc; workflow noise (`<specs.dir>`, `.artel/`,
  generated files) is excluded from code analysis; renders fully offline.

### address-pr-comment

- **Purpose:** Read a single pull-request comment, cross-reference the active ticket's docs,
  and enter plan mode with a proposed fix.
- **Invocation:** `/artel:address-pr-comment <pr-comment-url>`
- **Reads:** the comment (+ direct thread replies), PR metadata, and diff via `vcs.adapter`
  (GitHub and Bitbucket comment-URL shapes both parsed); whichever ticket docs exist
  (ticket-wide and `phase-<N>/` variants); optionally the tracker issue (degrades silently).
- **Writes:** no files — the sole artifact is the plan-mode plan (context, the comment,
  assessment, proposed fix, verification).
- **Pauses:** an `AskUserQuestion` when the comment is already marked resolved (continue
  anyway?); then plan mode for approval.
- **Notes:** the documented carve-out from the skill-orchestrator contract — no `Agent`
  delegation, because the artifact is a plan-mode plan only the main session can author.
  Read-only on the VCS host (never posts or edits comments); single-comment scope (never
  iterates the PR's other comments); treats comment text as untrusted input; flags conflicts
  with the PRD rather than reflexively agreeing.

### issue-draft

- **Purpose:** Turn free text or a local `.txt`/`.md` file into a Jira-ready issue draft —
  summary (≤255 chars) + a templated description — consulting the institutional-knowledge index
  and asking about remaining gaps before writing; never posts anywhere.
- **Invocation:** `/artel:issue-draft <text | file-path> [--local]`
- **Reads:** the argument (text, or the file it names); `language.pr`, `tracker.adapter`,
  `knowledge.adapter`, `knowledge.project`, `ticket.pattern` (config.md); the description
  template — `.artel/templates/issue-draft.md` when the host provides one, else the skill's
  `assets/templates/description.template.md`; kartoteka via the consultation contract
  (`index_status`, `related` when the source names a ticket key, at most four
  `search_knowledge`), which `--local` forces off for one run.
- **Writes:** a draft file (user-supplied path, or `issue-draft-<slug>.md` in the working
  directory): a `=== SUMMARY ===` block and a `=== DESCRIPTION (<dialect>) ===` block whose
  body pastes into the description field as-is.
- **Pauses:** asks for the text or file when none is provided (never invents an issue from
  nothing) and when a referenced file is unreadable; then **once** more, via `AskUserQuestion`
  with up to four questions, about gaps neither the source nor kartoteka closed — unanswered
  gaps are listed under Missing Details rather than guessed. Non-interactive runs skip the
  round and list every gap.
- **Notes:** worker, not orchestrator. Output language is `language.pr`; the markup dialect
  follows `tracker.adapter` (Jira wiki under `"jira-mcp"`, Markdown otherwise). Consumer of
  knowledge-consultation.md with two declared deviations: a kartoteka tool error stops the
  consultation, not the draft, and nothing is recorded under `<specs.dir>`. Retrieved text is
  quoted and attributed, `⚠ NON-CURRENT` hits never close a gap, Related holds at most five
  cited hits. Draft only — never calls an issue-creation API; never invents facts, priorities,
  assignees, labels or issue types.

### init-branch

- **Purpose:** Bootstrap work on a ticket in one shot: branch check, optional move into a
  worktree, post-branch setup, context restore, CLAUDE.md refresh.
- **Invocation:** `/artel:init-branch [ticket-id]`
- **Reads:** the current branch name; `setup.commands`; the ticket grammar (`ticket.projectKey`
  for the branch-name token scan); the tracker summary or `idea.md` title (branch slug); the
  detected `origin` default branch; the `.artel/context/` store (via `restore-context`).
- **Writes:** nothing to git when the current branch already carries the ticket's ID. Otherwise,
  only at the user's choice: an existing branch for the ticket checked out, or a new
  `feature/<TICKET_ID>-<slug>` (phase runs: `feature/<TICKET_ID>-<N>-<slug>`) created from the
  detected base branch. Then restored `CLAUDE.md` / `CHANGELOG.md` / ticket spec trail (via
  `restore-context`) and a refreshed `CLAUDE.md` (via the built-in `/init`). At the user's
  choice, the branch is checked out or created in `.claude/worktrees/<name>` instead and the
  session moves there (`scripts/worktree.py move-in`, [worktrees.md](worktrees.md)).
- **Pauses:** when the current branch carries no ticket ID (check out an existing ticket branch /
  create one / stay, plus the worktree question); on the Stay route in the main checkout (the
  worktree question alone); when no slug source exists (a short description); when the default branch
  is ambiguous or the context store is missing (the user decides whether to continue).
- **Stops:** when the current branch carries a *different* ticket's ID — it never switches away
  from another ticket's branch on its own.
- **Notes:** worker, not orchestrator; user-invoked only (`disable-model-invocation`). Never
  creates a branch without asking. Fixed order: branch → `setup.commands` (fatal on failure,
  silently skipped when empty) → restore → `/init` → optional host index refresh, with the
  optional worktree move right after the branch step. Scope is setup, nothing else — it never
  commits, pushes, or runs the quality gate. Idempotent — safe to re-run.

### move-to-worktree

- **Purpose:** Move a ticket already on its branch out of the main checkout into its own git
  worktree and continue the session there, so other sessions can work on other tickets.
- **Invocation:** `/artel:move-to-worktree [ticket-id]`
- **Reads:** the current branch; `git worktree list`; the detected `origin` default branch;
  `setup.commands`; the main checkout's `.worktreeinclude`, `.artel/` and ignored files.
- **Writes:** `.claude/worktrees/<name>` on the ticket's branch, with the main checkout's
  uncommitted work, copied `.artel/` config and ignored host files, a symlink to the context
  store, the ticket's moved `.artel/run/<TICKET_ID>/`, copied hook baselines and
  `.artel/worktree.json`; the main checkout switches to the base branch. Possibly
  `/.claude/worktrees/` in `.git/info/exclude`. Then `setup.commands` in the worktree.
- **Pauses:** once, to confirm the move.
- **Stops:** inside a worktree; when the current branch is not the ticket's (points at
  `init-branch`); on any script status but `ok` ([worktrees.md](worktrees.md) §6).
- **Notes:** worker; user-invoked only (`disable-model-invocation`). An existing worktree for the
  ticket is entered, not recreated. A stash is dropped only after it applied; never `--force`,
  never a branch deletion. OpenCode: prints `cd <path> && opencode` instead of entering.
  Counterpart: `return-from-worktree`.

### return-from-worktree

- **Purpose:** Hand a finished worktree's branch back to the main checkout and remove the
  worktree.
- **Invocation:** `/artel:return-from-worktree [ticket-id]`
- **Reads:** `git worktree list`; the worktree's `.artel/worktree.json`; both checkouts' status;
  `setup.commands`.
- **Writes:** the main checkout switched to the ticket's branch with the worktree's uncommitted
  work applied; `.artel/run/<TICKET_ID>/` and hook baselines merged back (newer wins), plus the
  `.artel/run/<TICKET_ID>/worktree.json` marker; a context store the worktree grew merged back;
  the worktree removed. Then `setup.commands` in the main checkout.
- **Pauses:** once, to confirm; to pick a worktree when a ticket has several.
- **Stops:** when the main checkout has uncommitted changes; on a detached worktree; when the
  session was started inside the worktree (run it from the main checkout); on any script status
  but `ok`.
- **Notes:** worker; user-invoked only (`disable-model-invocation`). Leaves with
  `ExitWorktree` `keep` only. The branch is never deleted, never `--force`. Changed environment
  files (`.claude/`, `.mcp.json`, config) are reported as `envChanged`, never copied back. An
  interrupted hand-back is finished by a re-run.

### set-home

- **Purpose:** Move the project to a different VCS platform — rewrite `vcs.*` in
  `.artel/config.json` and re-point the git remotes so config and `origin` agree.
- **Invocation:** `/artel:set-home <repo-url>`
- **Reads:** `.artel/config.json` (`vcs.adapter`, `vcs.mcpToolPrefix`, `tracker.adapter`);
  `git remote -v`.
- **Writes:** `.artel/config.json` (`vcs.*` only, read-modify-write preserving key order);
  git remotes (`rename`, `add`, `fetch`, `set-head`, upstream tracking). Never pushes, never
  `--force`s, never deletes a remote or branch.
- **Pauses:** confirms every change via `AskUserQuestion` (**Apply** / **Abort**) before
  touching remotes or config; asks for `vcs.mcpToolPrefix` when the target is `bitbucket-mcp`
  and it is empty (re-asks until non-empty); asks about `tracker.adapter` only when it names the
  platform being left (e.g. `github-issues` while moving off GitHub).
- **Notes:** worker, not orchestrator — runs inline (like `setup`). No argument → reports the
  current `vcs.adapter` vs. `origin` and stops, read-only. Idempotent: skips the git surgery
  entirely when `origin` already matches the target. Preserves `vcs.mcpToolPrefix` on a switch
  to `github-cli` — that adapter ignores it, but `/artel:migrate-prs` needs it to address the
  old platform's MCP tools for reading. Renames rather than replaces `origin` (`bitbucket` /
  `github` / `old-origin`, suffixed on a name collision) so the old remote survives for
  `/artel:migrate-prs`; the operator removes it by hand once done. Runs
  `git remote set-head origin -a` after the swap — required, not cosmetic: `pr-create` and
  `agents/reviewer.md`'s standalone mode resolve the default branch through
  `refs/remotes/origin/HEAD`. Offers `/artel:migrate-prs` in its report when the old platform
  was Bitbucket and open PRs exist.

### migrate-prs

- **Purpose:** Recreate a Bitbucket project's still-open pull requests on GitHub after
  `/artel:set-home`, carrying title, description and branches.
- **Invocation:** `/artel:migrate-prs [pr-id ...]`
- **Reads:** `.artel/config.json` (`vcs.adapter`, `vcs.mcpToolPrefix`); the old (renamed)
  remote's URL via `git remote -v`; `<vcs.mcpToolPrefix>bitbucket_list_repo_prs` /
  `bitbucket_get_pr`; `gh pr list --head` for the idempotency check; `gh auth status`.
- **Writes:** branches fetched from the old remote and pushed to `origin` (never `--force`);
  pull requests via `gh pr create`, with the Bitbucket description copied verbatim plus a
  `Migrated from <bitbucket-pr-url>` provenance line. Never writes to Bitbucket — no comment, no
  decline, no approval; the `vcs_guard` hook denies it regardless.
- **Pauses:** `AskUserQuestion` to choose which open PRs to migrate, skipped when `$0` names PR
  ids explicitly. Otherwise never.
- **Notes:** worker, not orchestrator — runs inline, directional by design (Bitbucket → GitHub
  only). Re-runnable: each PR's own `gh pr list --head` check runs before anything is pushed or
  created, so a partial run resumes cleanly on re-invocation. A divergent-history branch on
  `origin` is skipped and reported, never reconciled. Review comments, reviewer assignments and
  PR state are not migrated — thread anchors and reviewer identities do not carry across
  platforms. The Bitbucket PRs stay open until the operator declines them by hand.

### add-automation

- **Purpose:** Apply the transient agent UI-automation scaffold to the current branch and
  commit exactly what it changed.
- **Invocation:** `/artel:add-automation`
- **Reads:** `runtime.scaffold.add`, `verify.fast`; a clean-tree and not-on-default-branch
  pre-flight.
- **Writes:** whatever paths `runtime.scaffold.add` changes (discovered via
  `git status --porcelain`), committed as `chore: enable agent UI automation` after a
  `verify.fast` check.
- **Pauses:** stops and asks only on a dirty tree (commit or stash first).
- **Notes:** worker; user-invoked only (`disable-model-invocation`). Opt-in per task; undone by
  `remove-automation` before merge; the reviewer's transient-automation carve-out and the
  validator's `AUTOMATION_REMOVED` gate account for it. Absent/empty command → `not
  configured`, gate records `skipped`. Rolls the scaffold back if `verify.fast` goes red; never
  `git add -A`; never on the default branch.

### remove-automation

- **Purpose:** Remove the transient UI-automation scaffold again (reverse of `add-automation`)
  and push the removal when an upstream exists.
- **Invocation:** `/artel:remove-automation`
- **Reads:** `runtime.scaffold.remove`, `verify.fast`; a clean-tree pre-flight.
- **Writes:** the removal changes, committed as `chore: remove agent UI automation`; `git push`
  when an upstream exists (never any `--force` variant).
- **Pauses:** stops and asks only on a dirty tree.
- **Notes:** worker; user-invoked only (`disable-model-invocation`). Run after the PR is
  created, before merge — this is what turns the `AUTOMATION_REMOVED` gate green. Re-derives
  the removal by running the configured command, never `git revert`, then verifies it from
  git alone: every path the `chore: enable agent UI automation` commit added must be gone
  (a byte-identical survivor is deleted; a modified one is stop-and-report), and the
  directories the scaffold created for itself are swept once their files are gone (removed
  when empty or holding only ignored entries; untracked work is stop-and-report). Absent/empty
  command → `not configured`, gate records `skipped`. `drive-app`'s `drive-observation.md` is
  evidence and is not touched.

### save-context

- **Purpose:** Mirror the working context — root `CLAUDE.md`/`CHANGELOG.md` plus every ticket's
  spec trail — into the host-local `.artel/context/` store, then clear the working-tree copies.
- **Invocation:** `/artel:save-context`
- **Reads:** `CLAUDE.md`, `CHANGELOG.md`, `<specs.dir>/.active_ticket`, and every ticket
  directory under `<specs.dir>/`.
- **Writes:** `.artel/context/root/` (shared latest), `.artel/context/.active_ticket`,
  `.artel/context/tickets/<TICKET_ID>/{root,spec-trail}/` — newer-wins; then removes the
  working-tree copies (only after every store copy succeeded).
- **Pauses:** never.
- **Notes:** worker; user-invoked only (`disable-model-invocation`). The store is
  host-repo-local and gitignored (config.md, "Purpose and location") — unlike the source
  system's user-level store, it does not survive a reclone; the trade-off is logged in
  [design.md](design.md)'s decision log. Idempotent; cleanup never deletes anything that failed
  to reach the store.
  Counterpart: `restore-context`.

### restore-context

- **Purpose:** Restore workflow docs and/or one ticket's spec artifacts from `.artel/context/`
  back into the project.
- **Invocation:** `/artel:restore-context [ticket-id]`
- **Reads:** the `.artel/context/` store (`root/`, `.active_ticket`,
  `tickets/<TICKET_ID>/{root,spec-trail}/`).
- **Writes:** `CLAUDE.md`, `CHANGELOG.md` (ticket snapshot preferred, shared latest as
  fallback), `<specs.dir>/.active_ticket`, and either `<specs.dir>/<TICKET_ID>/` (ticket mode)
  or every saved ticket's folder (full restore, no argument).
- **Pauses:** never.
- **Notes:** worker. Copies, never moves — the store stays authoritative; warns loudly
  (non-fatal) when a requested ticket has no artifacts in the store; the only hard failure is a
  missing store entirely. Ticket-scoped, not phase-scoped — a phase suffix is accepted and
  ignored. Chained by `init-branch`. On the kartoteka path, spec documents and images under the
  trail are never restored to disk — old spec copies and images stay in the context store until
  `/artel:migrate-specs` moves them in (spec-storage.md §4.6, §7).

### agents-md-generator

- **Purpose:** Create or update minimal, high-signal `AGENTS.md` files for the host repo's root
  and confirmed nested modules, using progressive disclosure.
- **Invocation:** `/artel:agents-md-generator`
- **Reads:** the host repo's manifests (`package.json`, `go.mod`, `pyproject.toml`,
  `pubspec.yaml`, …), docs, existing `AGENTS.md` files, package scripts/Makefile/CI; the
  skill's two bundled templates (root and module); the host's optional code-symbol index
  for repo shape, detected conventions and module dependencies
  ([code-navigation.md](code-navigation.md) §2), silently absent otherwise.
- **Writes:** `<repo-root>/AGENTS.md` (≤ 60 lines) and `<module>/AGENTS.md` (≤ 40 lines) —
  create or update only these.
- **Pauses:** never.
- **Notes:** worker. Generates only from what the repo itself documents — never invents
  commands or assumes directory structures; preserves critical security/deployment warnings.
  Not ticket-scoped. Adapted from a community skill (attribution in the skill body).

---

## Kartoteka front doors

### knowledge

- **Purpose:** Answer a person's question against the project's institutional-knowledge index —
  prior decisions and discussions, everything filed under a ticket, index freshness.
- **Invocation:** `/artel:knowledge <query> | <ticket-id> | status [--source files|jira|bitbucket] [--type <type>] [--status <status>] [--artifacts]`
- **Reads:** `.artel/config.json` (`knowledge.adapter`), `<specs.dir>/.active_ticket`; over MCP:
  `index_status`, `related`, `search_knowledge`, and `artifact_list` / `artifact_get` for a
  non-active ticket's trail history.
- **Writes:** nothing — no file under `<specs.dir>`, no `artifact_put`.
- **Pauses:** never.
- **Notes:** worker. The read side of [knowledge-consultation.md](knowledge-consultation.md)
  applied to the conversation: one `index_status` probe, `related` on the canonical key (phase
  suffix stripped), unfiltered `search_knowledge` first, at most four searches, citations with
  the `⚠ NON-CURRENT` marker verbatim, retrieved text quoted never restated. Refuses with a
  pointer to `/artel:setup` when `knowledge.adapter` is not `kartoteka`, and with the contract's
  "configured but not available" message when the MCP tools are absent — no override flag. The
  active ticket's own `## artifacts` block is named as a lagging mirror and never fetched.

### tasks

- **Purpose:** Operate a ticket's kartoteka task queue from the conversation: list and diagnose,
  add, mark done or blocked, release a held task.
- **Invocation:** `/artel:tasks list|add|done|block|release [ticket-id] [<task-id> | "<title>" (--iteration N [--section <name>] | --fix CRF|RTF|VF|FV) [--hitl <reason>] [--raw]] [--status <status>] [--note <text>]`
- **Reads:** `.artel/config.json` (`knowledge.adapter`), `<specs.dir>/.active_ticket`,
  `<specs.dir>/<TICKET_ID>/tasklist.md` (and `phase-<N>/tasks.md` when present); over MCP:
  `task_list`, `task_create`, `task_update`.
- **Writes:** `add` appends a checkbox to `tasklist.md` (and the phase file) — or, with
  `--fix`, under a `### manual-<date>` heading in a fix section — and mirrors it through
  `scripts/tasklist_tasks.py` + create-only `task_create`; `done` flips the matching checkbox
  after `task_update`; `block` / `release` update the row only.
- **Pauses:** `release` always confirms via `AskUserQuestion` (shows holder and age); nothing
  else pauses.
- **Notes:** worker. The write side of [task-queue.md](task-queue.md) applied to the
  conversation, under the same gate as `knowledge`. Never calls `task_ready` (claiming is the
  implementer's) and never promotes an iteration (the implementer's repair). `add` composes no
  title by hand — the mirror script does, so the row carries the `I<N> · ` prefix, its
  `parent_id`, and queue order; `--raw` creates a bare `backlog` row that artel will not claim
  and says so. `list` reports drained / promotion pending / blocked / held / fix work open and
  repairs nothing; fix rows never count toward promotion pending or drained, and `release`
  refuses them.

### migrate-specs

- **Purpose:** Move local spec trails into kartoteka, the spec store, and delete the local copies
  it verifiably holds.
- **Invocation:** `/artel:migrate-specs [<ticket-id>… | --all] [--pending-only] [--no-prompt]`
- **Reads:** `.artel/config.json`, `<specs.dir>/<TICKET_ID>/` (and `phase-<N>/`),
  `.artel/context/tickets/<TICKET_ID>/spec-trail/`, `.artel/run/<TICKET_ID>/spec-store.json`;
  over HTTP (`scripts/spec_store.py`): every stored version's hash.
- **Writes:** artifact versions (`author_agent: artel:migrate-specs`); the decision file; with
  confirmation, deletions — `git rm` for tracked files, one optional commit.
- **Pauses:** once per conflict (keep local / keep stored / skip) and once before deleting;
  `--pending-only` deletes without asking; `--no-prompt` never pauses and never deletes except
  under `--pending-only`.
- **Notes:** worker. Classification is `absent` / `current` / `stale` / `successor` / `conflict`
  / `skipped`, one item per distinct local copy ([spec-storage.md](spec-storage.md) §7): a stale
  local copy never overwrites a newer stored version. Orchestrators invoke it when a run finds a local trail and on resume
  after an outage. `.active_ticket`, evidence and release-scope files are never deleted.
