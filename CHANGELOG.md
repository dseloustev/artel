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
