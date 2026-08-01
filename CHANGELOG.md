# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

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
  project, attribution dropped for frontmatter consistency with the rest of the crew) ports
  essentially unchanged — its workflow and both `references/` templates were already
  language/toolchain-agnostic — with its two template reads pointed at
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
