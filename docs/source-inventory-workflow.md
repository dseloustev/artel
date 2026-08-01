# Source inventory — workflow skills, agents & gates (task execution)

*Compiled 2026-08-01 from the sibling checkout `../adguard-wallet/.claude/`.*

Everything here **participates in working on a task**: orchestrating the ticket→PR pipeline,
writing code and documents, QA, git/branch/PR operations, context lifecycle, and the quality
gates around them. The companion list of reference/how-to skills lives in
[source-inventory-informational.md](source-inventory-informational.md).

Decision 2026-08-01 (see [design.md](design.md#decision-log)): **everything in this list is in
artel's scope** — 32 skills (three renamed: `flutter-inner-loop` → `inner-loop`,
`wallet-review` → `deep-review`, `jira-issue-ru` → `issue-draft`), 12 agents, 5 hooks, the
CLI (Python rewrite), and the contracts/operator docs. Phasing lives in
[porting-plan.md](porting-plan.md).

## Entry-point orchestrators

- **`feature-development`** — The full autonomous pipeline: chatty head gates (`generate-idea`
  → `figma-analysis` → `analysis` → `generate-vision` → `researcher` + `planner` → plan-check
  → `tasklist` → `sync-phases`), then THE ONE plan+tasklist approval pause, then autonomous
  implementation, review, QA and docs with per-phase checkpoint commits and pushes. Owns
  `run-state.json` and the shared checkpoint-commit procedure.
- **`dev`** — Lean autonomous loop: derives a work list via an input ladder (existing tasklist
  → `generate-tasklist` → mini-interview), takes one confirmation, arms `run-state.json`, then
  drives implement + review + runtime gates. Supports `--mode=yolo|plan-gate|full-gates`.

## Pipeline stage skills (rough pipeline order)

Most of these are **thin orchestrator wrappers**: resolve ticket/phase context, invoke the
matching agent via the Agent tool, report. They never inline the agent's work.

- **`generate-idea`** — Inline worker: fetches the Jira issue + comments via MCP, translates to
  English, renders `specs/.current/{TICKET}/idea.md` from a template. The IDEA_READY seed of
  the pipeline.
- **`figma-analysis`** — Resolves the ticket's Figma URL and delegates to the `figma-analyst`
  agent: flow graph, screen-to-code mapping, discrepancy handshake → `design-analysis.md`.
  Skips silently when there is no design link.
- **`analysis`** — Stage 1: gates on `idea.md`, orchestrates the `analyst` agent through a
  batched requirements interview to produce the PRD (PRD_READY gate).
- **`generate-vision`** — Drives the `vision-writer` agent (draft → one question round →
  finalize) to produce `vision.md` from idea + PRD (VISION_READY gate).
- **`researcher`** — Three-phase orchestrator around the `researcher` agent: extract questions,
  park unresolved ones in `open-questions.md` with proposed defaults, resume to write
  `research.md`. Silent (no user interaction).
- **`planner`** — Same three-phase shape around the `planner` agent: architecture plan
  (`plan.md`, optional `adr.md`) from PRD + research; plan anchors must survive
  `plan-check --strict` (PLAN_GROUNDED).
- **`tasklist`** — Delegates to `task-planner` to break the approved plan into small checkbox
  tasks with HITL tagging (TASKLIST_READY gate). Full-pipeline counterpart of
  `generate-tasklist`.
- **`generate-tasklist`** — Lean-path counterpart: delegates to `tasklist-writer` to go
  straight from `idea.md` + `vision.md` to an iteration-based `tasklist.md`; its clarifying
  round doubles as `dev`'s mini-interview.
- **`sync-phases`** — Inline bookkeeping: extracts `phase-{N}/tasks.md` from the master
  tasklist and syncs completion status back after phase gates pass.
- **`implementer`** — Delegates the next unchecked task to the `implementer` agent (no
  proposal round; deviation protocol is the escalation path). Honors `[HITL: …]` tags.
- **`flutter-inner-loop`** — The bounded verify→fix→re-verify loop the implementer runs until
  the deterministic gate (`tools/agent verify`) is green: max 4 iterations, exit-2 =
  stop-and-ask, evidence JSON written under `specs/.current/{TICKET}/…/verify/`. The only
  `flutter-*` skill that is a pipeline stage rather than reference material.
- **`run-reviewer`** — Delegates to the `reviewer` agent: Blocking/Important/Nice-to-have
  findings, `review.md` + tasklist write-back (REVIEW_OK gate).
- **`wallet-review`** — Deep-review variant: enforces `make verify`, pulls Jira/Bitbucket PR
  context, spawns **two independent reviewer agents**, merges findings, enters plan mode for
  improvements.
- **`qa`** — Delegates to the `qa` agent for a QA plan + report and verdict, at ticket, phase,
  or `R-` release scope.
- **`run-app`** — The RUNTIME_OK gate: launches the app via `tools/launch_app.dart`, connects
  the Dart Tooling Daemon MCP, checks runtime errors/logs/widget tree, records evidence,
  yields GREEN/RED. `--gate` mode for pipelines; interactive mode leaves the app running.
- **`drive-app`** — Interactive counterpart of `run-app`: drives the UI via Flutter Driver
  (unlock with the test-wallet password, navigate, tap, type, screenshot). Requires
  `add-automation` on the branch.
- **`docs-update`** — Delegates to `tech-writer` for the ticket summary + CHANGELOG entry
  (DOCS_UPDATED gate).
- **`validate`** — Delegates to the `validator` agent to report which quality gates pass
  (PRD_READY … AUTOMATION_REMOVED) and what blocks the rest.
- **`change-digest`** — Renders a self-contained HTML change-comprehension report (context,
  decisions, deviations, module breakdown, reader quiz) from the ticket artifacts + real
  branch diff.
- **`pr-description`** — Delegates to `tech-writer` to write `pr-description.md` from ticket
  artifacts, the branch diff, the Jira issue, and the style of recent merged PRs.
- **`pr-create`** — Idempotent loop-close: commit, push, open the Bitbucket PR with the
  generated description, comment the PR URL on the Jira issue. Refuses the default branch;
  never force-pushes.
- **`address-pr-comment`** — Takes one Bitbucket PR comment URL, grounds it in the ticket's
  docs, and enters plan mode with a proposed fix. Read-only towards Bitbucket.

## Branch, repo & context operations

- **`init-branch`** — One-shot branch bootstrap: `restore-context` (ticket-scoped), `/init`
  refresh of CLAUDE.md, `make fin` (deps + codegen + l10n), `ast-index` rebuild. User-invoked
  only.
- **`merge-conflicts`** — Fetch + merge target branch (`--no-commit --no-ff`), resolve each
  conflict by analyzing both sides' intent, verify via analyzer/format/tests. Resumes an
  in-progress merge.
- **`add-automation`** — Applies the transient UI-automation artifacts to the feature branch
  (flutter_driver dep, `test_driver/agent_main.dart`, shimmer frame-sync patch) in one chore
  commit, so the agent can drive the app.
- **`remove-automation`** — Reverses `add-automation` by re-deriving its exact edits (never
  `git revert`) before merge; feeds the AUTOMATION_REMOVED gate.
- **`save-context`** — Mirrors CLAUDE.md/CHANGELOG.md/docs/specs into the user-level context
  store (`~/.claude/plugins/data/wallet-workflow/<project>`, "newer wins"), then clears the
  working-tree copies.
- **`restore-context`** — The restore half: full or ticket-scoped copy-back from the store
  into the working tree.

## Ticket-authoring & meta utilities

- **`jira-issue-ru`** — Turns free text / chat excerpts / a local file into a Jira-ready issue
  file (Russian summary + Jira-wiki description). Writes a local file only, never posts.
- **`agents-md-generator`** — Creates/repairs minimal root and nested `AGENTS.md` files from
  bundled templates using progressive disclosure. Portable, repo-agnostic.

## The agent crew (`.claude/agents/*.md`)

| Agent | Model | Role |
|---|---|---|
| `analyst` | opus | Requirements interview (batched, branch-by-branch, grounded in the codebase first) → writes the PRD. Never invents business requirements. |
| `figma-analyst` | opus | Figma MCP analysis: flow graph from connector nodes, desktop/mobile pairing, screen-to-code mapping via ast-index → `design-analysis.md` + evidence screenshots. |
| `researcher` | opus | Read-only codebase investigator with a two-step handshake (questions first, then research) → `research.md`. Never guesses instead of asking. |
| `planner` | opus | Architecture: components, interfaces, data flows, NFRs, risks, optional ADR → `plan.md`. Every `ref:` anchor must resolve or plan-check bounces it. |
| `task-planner` | sonnet | Breaks PRD + plan into small verifiable checkbox tasks → `tasklist.md` / `phase-{N}/tasks.md`. Owns mandatory HITL tagging for sensitive paths. |
| `tasklist-writer` | sonnet | Lean-loop counterpart: idea + vision → iteration-based `tasklist.md` in one shot, KISS and testable. No PRD/plan needed. |
| `vision-writer` | opus | Drafts the seven-section technical vision from idea + PRD (PRD decisions are binding) → `vision.md`, DRAFT → VISION_READY. |
| `implementer` | opus | Autonomous developer, one task at a time; the approved plan is the contract, deviations go through the deviation protocol. Must pass the bounded verify loop before closing a task. |
| `reviewer` | opus | Dual-mode reviewer (ticket-grounded or standalone git-diff) with three lenses: convention-fit, architecture-fit, security. Writes `review.md` + `review/findings.json`, appends fix tasks to the tasklist. |
| `qa` | sonnet | QA plan + report (positive/negative scenarios, risk zones, release verdict) at release, ticket, or phase scope. |
| `validator` | sonnet | Read-only gatekeeper: reports which of the nine quality gates pass. Conservative — uncertainty means "requires attention". |
| `tech-writer` | opus | Ticket summary + CHANGELOG entry (and PR descriptions), written for a new developer and an incident commander who won't read the code. |

## Quality-gate hooks (`.claude/hooks/`, Python 3, stdlib-only)

- **`session_baseline.py`** (SessionStart) — Records pre-existing verify findings per session
  so the stop gate only reacts to findings introduced this session.
- **`fast_verify_post_edit.py`** (PostToolUse: Edit|Write|MultiEdit) — Runs `verify --fast` on
  the just-edited file and feeds findings back as context; never blocks.
- **`stop_gate.py`** (Stop) — Blocks session end while an autonomous run is active and
  incomplete, with wall-clock and consecutive-block escape hatches.
- **`verify_stop_gate.py`** (Stop) — Re-verifies all changed files at session end and blocks
  on findings beyond the baseline (max 2 consecutive blocks, then loud pass-through).
- **`sensitive_guard.py`** (PreToolUse) — During live autonomous runs, denies edits to paths
  matching `rules/sensitive-paths.json` when the run mode ranks below the category's floor
  (mfa-locker, key-material, db-encryption-migrations, money-movement).
- **`hook_common.py`** — Shared plumbing (verify command, Dart-file filters, state dir); not a
  hook itself.

## Deterministic CLI (`.claude/tools/`)

- **`agent/`** — Dart CLI (`fvm dart .claude/tools/agent/agent.dart <verb>`), one JSON
  envelope per invocation, exit 0 = ok / 1 = findings / 2 = environment error (never "fix" an
  exit 2 by editing app code). Verbs: `verify` (analyzer+dcm+format[+tests] composite gate),
  `dcm`, `codegen` (detects and runs needed `make` targets + ast-index refresh), `plan-check`
  (resolves `ref:`/`new:` anchors in a plan against the codebase — the PLAN_GROUNDED gate).
- **`launch_app.dart`** — Detached app launcher with `launch`/`stop`/`status` verbs; captures
  the DTD URI that `run-app` feeds into the Dart MCP connection.
- **`templates/agent_main.dart`** — The agent-only Flutter Driver entrypoint that
  `add-automation` copies into the branch and `remove-automation` deletes.
