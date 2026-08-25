# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- **`docs/code-navigation.md` — the query-side contract for a code-symbol index.** Six agents
  already reached for "the host's optional code-symbol index" and all of them pointed at
  `orchestrator-common.md` §1, which documents only the post-implementation *refresh* hook —
  nothing about reading an index. The new file is that contract, shaped like
  `knowledge-consultation.md`: §1 availability (`command -v`, silently absent, never a
  config key — an index is project-agnostic), §2 the reference implementation and its command
  table, §3 index-before-grep and the literal/regex/comment exceptions, §4 staleness with one
  update-and-retry, §5 the grounding rule the `PLAN_GROUNDED` gate and the deviation protocol
  rest on. It is the second sanctioned exception to the genericization rule and narrower than
  the router's: only §2 names `ast-index`, every agent keeps the generic phrasing and cites the
  file.

### Changed

- **Five more agents and skills now navigate code through the index.** `reviewer` resolves a
  diff's symbols before judging it (`changed --base`, `usages`, `implementations`, `callers` —
  a diff shows changed lines, not what depends on them); `tech-writer` derives key code changes
  instead of grepping for them; `vision-writer` grounds every cited path and uses the index to
  find what to reuse; `agents-md-generator` discovers repo shape, conventions and module
  dependencies from `map` / `conventions` / `deps` rather than a directory crawl;
  `merge-conflicts` Phase 3 locates symbols that moved between the two sides. `analyst`,
  `researcher`, `planner`, `implementer` and `figma-analyst` keep their wording and re-point
  from `orchestrator-common.md` §1 to the new contract.
- `orchestrator-common.md` §1 now says which half it owns: refreshing is the host hook,
  querying is `code-navigation.md`.

### Unchanged (deliberately)

- `qa`, `validator` and `task-planner` read artifacts, not code. `merge-conflicts` Phase 5 keeps
  its Grep — a conflict marker is a string literal, which §3 makes the worked example of when
  *not* to reach for the index.

## [0.4.0] - 2026-08-25

### Added

- **`using-artel`, the session router.** A `SessionStart` hook (`startup|clear|compact`)
  injects a routing table over every skill — plus `knowledge.adapter`, the active ticket and
  whether the `ast-index` CLI is on PATH —
  whenever `.artel/config.json` exists, so "start work on PROJ-123" reaches
  `/artel:init-branch` on turn one and survives compaction. Carries `<SUBAGENT-STOP>`; lists no
  agents; tells the model an entry point is already the process. Inert without a config. With
  the `ast-index` CLI present, code navigation routes to the `ast-index` plugin's skill before
  any grep.
- **`/artel:knowledge` and `/artel:tasks`, the conversational front doors to kartoteka.**
  `knowledge` searches prior decisions, lists what is filed under a ticket, or reports index
  freshness — read-only, under the consultation contract's budget and citation rules. `tasks`
  lists and diagnoses a ticket's queue, adds a task by appending to `tasklist.md` and running
  the existing mirror (so the row carries its iteration prefix, parent and queue order), marks
  done or blocked, and releases a held task after confirmation. Both refuse — with a pointer to
  `/artel:setup` — when `knowledge.adapter` is not `kartoteka`. Neither claims.

## [0.3.1] - 2026-08-24

### Fixed

- **The automation skills' file-list and verify steps.** `add-automation` /
  `remove-automation` derive the scaffold's paths from `git status --porcelain`,
  which collapses a new directory into one `?? dir/` entry — breaking the
  rollback (`rm -f` refuses a directory), the commit's exact-paths check, and any
  `{files}` scope; both now read `--porcelain -uall -z`. Both also ran
  `verify.fast` as a raw string, so a config carrying `{files}` sent the literal
  token to the shell and failed a correct scaffold; they now substitute their own
  changed paths. `add-automation` no longer claims a failed apply leaves nothing
  to roll back, and `remove-automation` gained the default-branch guard its
  counterpart already had, since it commits and pushes.

## [0.3.0] - 2026-08-23

### Added

- **Task queue integration with kartoteka.** `tasklist.md` is mirrored into
  kartoteka's `tasks` table through `task_create`, and `implementer` takes its
  next task from `task_ready` and reports through `task_update` rather than
  scanning for the first `- [ ]`. Iterations become parent rows and checkboxes
  their children; the queue is authoritative for what to work on, while
  `tasklist.md` stays current as the offline fallback. Gated by
  `knowledge.adapter` plus tool presence, with `--local` forcing the fallback —
  no new config key. New `scripts/tasklist_tasks.py` does the parsing and
  contacts nothing; everything reaching kartoteka goes through MCP tools.
  See `docs/task-queue.md` and
  `docs/superpowers/specs/2026-08-22-artel-task-queue-design.md`.

## [0.2.0] - 2026-08-22

### Added

- **Knowledge consultation — the read half of the kartoteka adapter.** With
  `knowledge.adapter: "kartoteka"` and the kartoteka MCP tools in the session, the `analyst`
  consults the institutional-knowledge index before its interview and the `researcher` consults
  it during its scan, so a decision the team already took is neither re-asked nor
  re-litigated. `research.md` gains a **Prior Decisions** section; the PRD gains no new section
  and instead cites into its existing Resolved Questions and Assumptions.

  The contract is `docs/knowledge-consultation.md`, spelled once and referenced by both agents.
  Config declares intent, the session supplies capability, and every disagreement between them
  is reported in the agent's own output rather than failing silently. `--local` on `analysis`,
  `researcher` and `feature-development` forces a knowledge-free run; `dev` does not take it,
  because it invokes neither consulting skill.

  **Nothing here writes.** The `PostToolUse` mirror hook remains the only path from artel into
  kartoteka, and agents read this ticket's own spec trail from disk, never from kartoteka's
  best-effort mirror of it.
- Knowledge mirror: `hooks/knowledge_mirror.py` (`PostToolUse` on `Edit|Write|MultiEdit`) posts
  each deliberation artifact written under `<specs.dir>/<TICKET>/` — `prd.md`, `plan.md`,
  `adr.md`, `review.md` and twelve others — to a kartoteka artifact store, which versions it by
  content hash. New config: `knowledge.adapter` (`"none"` default, `"kartoteka"`) and
  `knowledge.baseUrl`. Additive and best-effort by design: the spec-trail files on disk stay
  primary, the hook never blocks a write and never retries, and every attempt is logged to
  `.artel/run/.hooks/knowledge-mirror.log`. Gate evidence, machine-readable findings, derived
  reports and everything under `.artel/` are deliberately not mirrored.
- Operator docs (Phase 6): `docs/workflow-guide.md` (the narrative operator guide — quickstart,
  concepts, end-to-end walkthrough, recipes, troubleshooting) and `docs/skills-reference.md`
  (per-skill lookup: purpose, invocation, reads/writes, pauses, notes for all 33 skills),
  adapted from the source project's operator docs to plugin reality: `/artel:` command forms,
  config-driven adapters and gates, `.artel/run/` state paths, and the `scripts/verify.py` /
  `scripts/plan_check.py` gate engines in place of the source's Dart CLI.
- Flutter smoke-test guide: `docs/testing-flutter.md` — installing the plugin from a local
  checkout into a separate Flutter project, a known-good Flutter `.artel/config.json`, six
  ordered smoke-test scenarios (spec stage, dry run, hooks, lean loop, full pipeline, gate
  scripts), pass criteria, cleanup, and troubleshooting.

- Project skeleton: plugin manifest, single-plugin marketplace file, repo scaffold
  (`skills/`, `agents/`, `hooks/`, `docs/`).
- Documentation: README, design doc (architecture, genericization strategy, open questions,
  decision log), phased porting plan with source→plugin map, CLAUDE.md working guidance.
- Config contract: `docs/config.md` — the `.artel/config.json` schema (ticket grammar,
  tracker/VCS adapters, verify commands, languages, design toggle, specs dir, runtime commands)
  with annotated defaults, a filled example, and missing-file/precedence rules.
- Ticket-parsing contract: `docs/ticket-parsing.md` — config-driven identifier parsing
  (`ticket.pattern`/`projectKey`/`phaseSuffix`), the spec-trail directory layout, artifact path
  resolution, and the refuse-and-ask write rules, ported and genericized from the source project.
- Autonomous-run contract: `docs/autonomous-run.md` — `run-state.json` schema, question
  collection, AFK/HITL tags, capped loops, modes and risk classification, the run journal,
  headless invocation, checkpoint commits, and phase traversal, ported and genericized with all
  host-writable run state relocated to `.artel/run/`.
- Skill-orchestrator contract: `docs/orchestrator-common.md` — ticket resolution, phase-aware
  artifact paths, the description-file sync procedure, and the checkpoint-commit/autonomous-run
  tie-in, ported and genericized from the source project.
- Deviation-protocol contract: `docs/deviation-protocol.md` — severity classification
  (minor/major), the `implementation-notes.md` format, the escalation handshake, and reviewer
  verification duties, ported and genericized from the source project.
- Path-conventions contract: `docs/path-conventions.md` — the repo-relative-paths-only rule for
  spec-trail artifact content, its rationale, scope, and citation-form conventions, ported and
  genericized from the source project.
- Analysis agents: `agents/analyst.md`, `agents/researcher.md`, `agents/figma-analyst.md` — the
  PRD-interview, codebase-research, and optional Figma design-analysis agents, ported and
  genericized from the source project, with paths resolved via `<specs.dir>`/`<TICKET_ID>` and
  contracts cited via `${CLAUDE_PLUGIN_ROOT}/docs/`.
- Planning agents: `agents/planner.md`, `agents/task-planner.md`, `agents/vision-writer.md`,
  `agents/tasklist-writer.md` — architecture/plan, tasklist breakdown, technical vision, and
  iterative work-plan drafting, ported and genericized from the source project's agent crew.
- Implementation agents: `agents/implementer.md`, `agents/reviewer.md`, `agents/qa.md` — the
  task-by-task implementer (inner-loop and codegen steps forward-referenced to the Phase-3
  `inner-loop` skill, generated-code rule genericized), the dual-mode (ticket/standalone)
  reviewer (project-specific lenses renamed to project-agnostic Review lenses, static analysis
  routed through `verify.fast`, transient-automation and sensitive-surface notes forward-referenced
  to Phase 5), and the QA plan/report agent, ported and genericized from the source project's agent
  crew.
- Validation agents: `agents/validator.md`, `agents/tech-writer.md` — the release/ticket/phase
  gate-checklist validator (gates genericized to config-driven degradation, e.g. `IMPLEMENT_STEP_OK`'s
  `verify.commands` step and `RUNTIME_OK`'s `runtime.run`, forward-referencing the Phase-3 `run-app`
  skill; `AUTOMATION_REMOVED` consistent with the reviewer's transient-automation treatment) and the
  ticket summary/CHANGELOG tech-writer, ported and genericized from the source project's agent crew.
  Completes the 12-agent crew (incl. `figma-analyst`).
- Spec-stage skills: `skills/analysis`, `skills/researcher`, `skills/planner`, `skills/tasklist`
  — the PRD-interview, research, planning, and tasklist-breakdown orchestrators, ported and
  genericized from the source project's à-la-carte skills, invoking the `analyst`, `researcher`,
  `planner`, and `task-planner` agents respectively via the `Agent` tool. Ticket resolution and
  artifact paths follow `docs/orchestrator-common.md`/`docs/ticket-parsing.md`; open-questions
  bookkeeping moves to `.artel/run/<TICKET_ID>/open-questions.md` per `docs/autonomous-run.md`;
  contract references use `${CLAUDE_PLUGIN_ROOT}/docs/`. Folded-in follow-ups from the phase-2
  review: `agents/planner.md`'s design-doc citation now resolves via `${CLAUDE_PLUGIN_ROOT}`;
  `agents/reviewer.md`'s dangling "PR review approval" checkbox reference reworded to match the
  tasklist's actual `## Code Review Fixes` / `REVIEW_OK` mechanism (the source template never had
  such a checkbox); logged the ported-skills' dropped `allowed-tools:` frontmatter decision.
- Generator skills: `skills/generate-idea`, `skills/generate-vision`, `skills/generate-tasklist`
  — the tracker-import, technical-vision, and lean idea+vision-to-tasklist workers, ported and
  genericized from the source project. `generate-idea` stays a procedural worker (no matching
  agent, like `sync-phases`) and now branches on `tracker.adapter` (`"none"` gathers the
  description from an argument or the user, matching `analysis`'s input gate; `"jira-mcp"` and
  `"github-issues"` fetch via `<tracker.mcpToolPrefix>jira_get_issue(_comments)` and the `gh` CLI
  respectively) with content translated to `language.docs` instead of hardcoded English/Jira;
  its idea template moves to `skills/generate-idea/assets/templates/idea.template.md`.
  `generate-vision` and `generate-tasklist` invoke the `vision-writer` and `tasklist-writer`
  agents via the three-phase draft/ask/finalize model, writing `<specs.dir>/<TICKET_ID>/vision.md`
  and `tasklist.md` respectively; cross-references to sibling skills use the installed
  `/artel:<name>` form.
- Implement/verify stage skills: `skills/implementer`, `skills/run-reviewer`, `skills/qa`,
  `skills/validate` — the task-by-task implementer (single-phase autonomous model, deviation
  escalation handshake per `docs/deviation-protocol.md`, verify loop forward-referenced to the
  Phase-3 `/artel:inner-loop` skill), and the one-shot review/QA/gate-check orchestrators
  invoking the `reviewer`, `qa`, and `validator` agents respectively, ported and genericized from
  the source project's à-la-carte skills. Agent-mapping verified against each agent file with no
  disagreements. Folded-in follow-ups: `docs/ticket-parsing.md` §3/§4 now document the reviewer's
  machine-readable `review/findings.json` output (ticket-wide and phase-scoped); introduced a
  `specs.releases` config key (default `"specs/releases"`, `docs/config.md`) and switched
  `agents/qa.md`, `agents/validator.md`, and the `qa`/`validate` skill bodies off the hardcoded
  `specs/releases/` literal, with the decision and rationale logged in `docs/design.md`.
- PR and digest skills: `skills/docs-update`, `skills/pr-description`, `skills/pr-create`,
  `skills/sync-phases`, `skills/change-digest`, `skills/address-pr-comment`, ported and
  genericized from the source project's à-la-carte skills. `docs-update` (dispatches
  `tech-writer`) and `pr-description` (dispatches `tech-writer`, gathering a tracker summary via
  `tracker.adapter`, a git diff, and a style sample of merged PRs via `vcs.adapter` before
  delegating the write) stay orchestrators; `pr-create`, `sync-phases`, and `change-digest` stay
  procedural, matching their source shape (no agent dispatched in source). `pr-create` and
  `pr-description` now branch on `vcs.adapter`/`tracker.adapter` instead of hardcoding a host —
  Bitbucket `projectKey`/`repositorySlug` are derived from `git remote get-url origin` at runtime
  rather than a new config key, since `docs/config.md` has no dedicated key for them.
  `sync-phases` and `change-digest` had no VCS/tracker dependency to genericize in source (pure
  file sync / local `git diff` respectively) and stay adapter-agnostic. `address-pr-comment`
  stays its documented carve-out (no `Agent` delegation — the artifact is a plan-mode plan only
  the main session can author) and now parses both a GitHub and a Bitbucket comment-URL shape
  depending on `vcs.adapter`. Default-branch resolution in `pr-description`, `pr-create`, and
  `change-digest` matches `agents/reviewer.md`'s existing standalone-mode pattern (`git
  symbolic-ref`, no hardcoded `master`/`main` fallback) instead of reintroducing one.
  `change-digest`'s self-contained HTML/quiz template
  (`skills/change-digest/assets/report_template.html`) ports unchanged — it was already
  project-agnostic.
- Renamed skills: `skills/inner-loop` (← `flutter-inner-loop`), `skills/deep-review` (←
  `wallet-review`), `skills/issue-draft` (← `jira-issue-ru`), ported and genericized from the
  source project's à-la-carte skills, consistent with the forward references already committed in
  `agents/implementer.md` and `skills/implementer`. `inner-loop` keeps its bounded
  edit→fast-check→full-gate loop shape and `MAX_VERIFY_ITERATIONS = 4` cap, now driven by
  `verify.fast`/`verify.commands` (config.md) instead of a project-specific CLI — since
  `verify.commands` always runs unscoped from the repo root, the source's separate scoped/unscoped
  final pass collapses into one full-gate step per iteration; environment-error handling keeps the
  exit-`2` convention `agents/implementer.md` already forward-references. `deep-review` keeps its
  dual-independent-reviewer structure and quality-gate/resolve/dispatch/merge/plan-mode steps,
  branches its quality gate and PR-link parsing on `verify.commands`/`vcs.adapter`, and — since
  `agents/reviewer.md`'s standalone mode already runs the full lens set plus the regression guard on
  every dispatch — drops the source's per-reviewer lens duplication, keeping only independence
  (second reviewer never reads the first) as what distinguishes the two dispatches; the legacy
  `review-windsurf.md` compatibility path is dropped (Windsurf mirroring is an explicit non-goal,
  design.md). `issue-draft` keeps the free-text/file/Slack-export input resolution, single vs.
  multiple-suggestions modes, and self-verify checklist; output language moves from hardcoded
  Russian to `language.pr`, and the description's markup dialect now follows `tracker.adapter`
  (Jira wiki under `"jira-mcp"`, Markdown otherwise) since the source's Jira-wiki-only format only
  made sense when the destination was always Jira; issue creation/submission stays out of scope, as
  in the source. Folded-in follow-ups: `docs/ticket-parsing.md` §3/§4 now document the ticket
  `verify/` evidence dir (inner-loop), `change-report.html` and its phase variant (change-digest),
  and `pr-pending.md` (pr-create's identity-check-failure fallback).
- Runtime-gate skills: `skills/run-app`, `skills/drive-app`, `skills/add-automation`,
  `skills/remove-automation` — config-driven adapters ported from the source project's
  Dart/Flutter-specific launcher and driver tooling. `run-app` and `drive-app` treat their
  `runtime.run` / `runtime.drive` commands (config.md) as opaque, self-reporting black boxes — the
  same evidence envelope (`{command, exit_code, output}`) `inner-loop` uses for `verify.commands` —
  recording `runtime/observation.md` / `runtime/drive-observation.md` evidence; `run-app --gate` is
  the only mode `agents/validator.md`'s `RUNTIME_OK` gate treats as authoritative. `add-automation`
  / `remove-automation` run `runtime.scaffold.add`/`remove`, derive the changed paths from
  `git status --porcelain` (the source's fixed Dart file list has no generic replacement), gate on
  `verify.fast`, and commit exactly those paths, consistent with the `AUTOMATION_REMOVED` gate and
  the reviewer's transient-automation carve-out already ported. Dropped as unreplaceable outside a
  Dart/Flutter toolchain: the DTD/MCP connect and observe steps, widget-tree-based finder
  targeting, the wallet unlock procedure and its hard rules, screenshot-per-verification-point
  capture, the seed-phrase secrecy guardrail, and the file-content idempotency/trace checks.
  Task-3 follow-up resolved: restored a one-line optional on-demand-runtime-check hint in
  `skills/implementer/SKILL.md`'s dispatch prompt (`/artel:run-app` via `runtime.run`, explicitly
  not the `RUNTIME_OK` gate) — logged in `docs/design.md`'s decision log. Folded-in follow-up:
  `docs/ticket-parsing.md` §3/§4 now document the ticket `runtime/` evidence dir
  (`observation.md`, `drive-observation.md`) and its phase-scoped variant.
- Ops and utility skills: `skills/init-branch`, `skills/merge-conflicts`, `skills/save-context`,
  `skills/restore-context`, `skills/agents-md-generator`, ported and genericized from the source
  project's utility skills — none dispatch an agent; each keeps its procedural shape per the
  ported-skills' `allowed-tools:`-drop convention. `init-branch` gains real branch-creation
  (`feature/<TICKET_ID>[-<PHASE_NUM>]`, base-branch detection matching `agents/reviewer.md`'s
  standalone-mode convention) that the source skill never had, chains `/artel:restore-context` and
  `/init`, and replaces the source's Dart-toolchain dependency/codegen install step and hardcoded
  `ast-index` calls with the optional host-hook pattern `docs/orchestrator-common.md` §1 already
  defines (no generic equivalent exists for the former, so it is dropped rather than faked).
  `merge-conflicts` keeps its five-phase setup/identify/plan-mode/resolve/verify structure,
  routing its post-resolution check through `verify.commands` instead of a Dart analyzer/formatter
  pair and generalizing the generated-file guardrail beyond `*.dart`/`make g`. `save-context` and
  `restore-context` port as a matched pair (new decision below) with a `docs/`-subset mirror and a
  top-level loose-`specs/`-files mirror dropped — both keyed to fixed source-project filenames with
  no generic equivalent. `agents-md-generator` (a third-party community skill in the source
  project; metadata frontmatter dropped for consistency with the rest of the crew, attribution
  kept as a footer line) ports essentially unchanged — its workflow and both `references/`
  templates were already language/toolchain-agnostic — with its two template reads pointed at
  `${CLAUDE_PLUGIN_ROOT}/skills/agents-md-generator/references/`. New decision: the context store
  `save-context`/`restore-context` read and write moves from the source's user-level,
  cross-project store to `.artel/context/` in the host repo (sibling to `.artel/run/`,
  `docs/config.md` "Purpose and location"), gitignored like `.artel/run/`; logged with its
  narrowed-scope trade-off in `docs/design.md`'s decision log.
- Design-analysis skill: `skills/figma-analysis` — the last Phase-3 stage skill, ported and
  genericized from the source project, dispatching the already-ported `figma-analyst` agent.
  Config-gated by `design.figma` (`docs/config.md`): disabled reports `skipped`; enabled but no
  Figma MCP connected degrades silently and the pipeline continues, per
  `docs/autonomous-run.md` §13 — resolving the source skill's stop-and-ask `ENV_ERROR` handling
  in favor of the runtime-optional contract. Its `design-analysis.md` template moves to
  `skills/figma-analysis/assets/templates/design-analysis.template.md` (genericizing the
  Flutter-specific "go_router routes, Scope widgets, and BLoCs" §3 note into "routing and
  state-management surfaces"), matching `agents/figma-analyst.md`'s existing citation and
  completing the 30-skill Phase-3 roster. Forward-reference sweep: dropped stale "(Phase 3)"
  labels off the now-landed `inner-loop` and `run-app` references (`agents/implementer.md`,
  `agents/validator.md`) and normalized every remaining Phase-4/5 forward reference
  (`agents/reviewer.md`, `agents/tasklist-writer.md`, `agents/task-planner.md`,
  `agents/vision-writer.md`, `agents/planner.md`, `docs/config.md`, `docs/autonomous-run.md`) to
  a uniform first-reference/bare-repeat "(Phase N — see .../porting-plan.md)" form. Folded-in
  deferred minors: `generate-idea`'s no-tracker metadata prose now matches its own
  `$METADATA`/`$COMMENTS_RENDERED` omit behavior instead of a stale "not applicable" promise;
  `skills/README.md`'s per-skill enumeration replaced with a phase-complete summary;
  `pr-description`/`pr-create`'s Bitbucket `projectKey`/`repositorySlug` derivation gains a
  stop-and-ask clause for a missing/malformed git remote; `docs/config.md`'s
  `tracker.adapter`/`verify.commands` Consumed-by columns now list `issue-draft`/`deep-review`.
  Closes out Phase 3.
- Entry-point orchestrators: `skills/feature-development` (full pipeline: chatty head, one
  approval pause, autonomous tail, completion gate, PR close-out) and `skills/dev` (lean loop:
  input ladder, one work-list confirmation, implement + review + runtime gate), ported and
  genericized from the source project. Run state, journal and open questions live at
  `.artel/run/<TICKET_ID>/` per `docs/autonomous-run.md`; the shared
  `## Checkpoint commits & pushes` procedure lives in `feature-development` (referenced by
  `dev`, branch-guard fallback now `main`, verify gate via `verify.commands`, Dart-specific pin
  restore dropped); gate 3.5's deterministic plan-check ports as contract but skips until the
  Phase-5 `scripts/plan_check.py` ships; `ast-index` steps become the optional host
  index-refresh hook; the runtime gate's Dart-specific surface test becomes the new
  `runtime.surface` config key. New `skills/setup` — the one-time config interview both entry
  points invoke when `.artel/config.json` is missing (also run manually to create or revise the
  config; named `setup` to avoid colliding with the built-in `/init`). New config keys:
  `runtime.surface` (globs gating when the runtime gate runs) and `setup.commands` (post-branch
  install/codegen, restoring `init-branch`'s dropped source step — the parked Phase-3
  follow-up). Decisions logged in `docs/design.md`.
- Hooks and gates (Phase 5): `scripts/verify.py` (deterministic envelope wrapper over
  `verify.fast`/`verify.commands` — exit 0 clean / 1 findings / 2 environment error, digit-
  stripped finding keys, `{files}` scoping) and `scripts/plan_check.py` (the plan-anchor checker
  Gate 3.5 invokes; `ref:`/`new:` grammar ported, backticked-path rule genericized, symbols via
  `ast-index` when present else `git grep`), resolving design.md open question 1 (`codegen`
  dropped — `setup.commands` covers it). Five hooks ported from the source project and wired via
  `hooks/hooks.json`: `session_baseline` (SessionStart findings baseline),
  `fast_verify_post_edit` (PostToolUse feedback, never blocks), `verify_stop_gate` (Stop; blocks
  only findings NEW vs the session baseline, 2-block cap with latch), `stop_gate` (Stop; blocks
  while an autonomous run is active and incomplete, 5-block cap, 3h wall clock),
  `sensitive_guard` (PreToolUse; mode-floor denials during armed runs). Shipped default
  sensitive-paths policy (`hooks/sensitive-paths.json`: secrets/gate-config at full-gates,
  ci-cd at plan-gate) with wholesale host override at `.artel/sensitive-paths.json`; new
  `verify.surface` config key; hook state under `.artel/run/.hooks/`; verify-layer hooks inert
  until `.artel/config.json` exists; stdlib `unittest` suite under `tests/`.

### Changed

- Documentation refresh to post-porting reality: README status flipped from "early scaffolding"
  to ported-pre-publish with a complete component table and repo layout (`scripts/`, `tests/`);
  `hooks/README.md` rewritten to describe the five shipped hooks, their state paths, and the
  escape hatch; stale "ship later"/"ported in a later phase"/"(Phase 5)" forward references
  resolved across `docs/autonomous-run.md`, `docs/orchestrator-common.md`, `agents/planner.md`,
  `agents/task-planner.md`, `agents/tasklist-writer.md`, `agents/vision-writer.md`, and
  `agents/reviewer.md` — each now cites the shipped artifact (deviation protocol, sensitive-paths
  policy, `plan_check.py`) instead of the porting plan.
