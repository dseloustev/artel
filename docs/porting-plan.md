# Porting plan

*Status: draft v0.1 · 2026-07-27*

Source material lives in the originating project's `.claude/` directory (assumed available as a
sibling checkout, e.g. `../adguard-wallet`). Each phase leaves the repo in a coherent,
committable state. Genericization rules are defined in [design.md](design.md#genericization-strategy).

## Phase 0 — skeleton ✅

Repo scaffold, plugin manifest, marketplace file, docs. (This commit.)

## Phase 1 — contracts and config layer

The rules everything else obeys, plus the config mechanism they will reference.

- [ ] Define `.artel/config.json` schema (ticket grammar, tracker/VCS adapters, verify
      commands, language, specs dir) + a documented default.
- [ ] Port and genericize `ticket-parsing.md` (ticket-ID grammar → config-driven).
- [ ] Port and genericize `autonomous-run.md` (run state, modes, caps) — host-writable state
      moves to `.artel/run/`.
- [ ] Port `orchestrator-common.md` (the skill-orchestrator contract).

## Phase 2 — agents

- [ ] Port the crew: `analyst`, `researcher`, `planner`, `task-planner`, `tasklist-writer`,
      `vision-writer`, `implementer`, `reviewer`, `qa`, `validator`, `tech-writer`.
- [ ] Strip source-project specifics (Flutter/Dart tool references, Jira project key,
      Bitbucket MCP tool names) → config lookups or adapter instructions.

## Phase 3 — stage skills

- [ ] Port à-la-carte stage skills: `analysis`, `researcher`, `planner`, `tasklist`,
      `generate-idea`, `generate-vision`, `generate-tasklist`, `implementer`, `run-reviewer`,
      `qa`, `docs-update`, `validate`, `pr-description`, `pr-create`, `sync-phases`.
- [ ] Each skill resolves ticket context → invokes its agent → reports (no inlined work).

## Phase 4 — entry-point orchestrators

- [ ] Port `feature-development` (full pipeline, one approval pause, resumability).
- [ ] Port `dev` (lean loop: work-list confirmation → implement + review + runtime gate).
- [ ] First-run init interview: no config found → interview → write `.artel/config.json`.

## Phase 5 — hooks and gates

- [ ] Port Python hooks: `session_baseline`, `fast_verify_post_edit`, `stop_gate`,
      `verify_stop_gate`, `sensitive_guard`, `hook_common`.
- [ ] Wire via `hooks/hooks.json` with `${CLAUDE_PLUGIN_ROOT}` paths.
- [ ] Verify commands come from config, not `make`/Dart assumptions.
- [ ] Decide the deterministic-CLI question (design.md open question 1) and implement.

## Phase 6 — publish

- [ ] End-to-end dry run in a scratch repo (a trivial non-Dart project).
- [ ] Operator docs: adapt `workflow-guide.md` + `skills-reference.md` to plugin reality.
- [ ] Push to GitHub, verify `/plugin marketplace add <owner>/artel` install path.
- [ ] Tag `v0.1.0`.

## Source → plugin map

| Source (`.claude/…`) | Plugin | Action |
|---|---|---|
| `skills/feature-development`, `skills/dev` | `skills/` | port + genericize (Phase 4) |
| `skills/{analysis,researcher,planner,tasklist,implementer,run-reviewer,qa,docs-update,validate,pr-description,pr-create,sync-phases,generate-idea,generate-vision,generate-tasklist}` | `skills/` | port + genericize (Phase 3) |
| `skills/figma-analysis` + `agents/figma-analyst.md` | optional module | later, post-v0.1 |
| `skills/{address-pr-comment,change-digest,merge-conflicts,jira-issue-ru,agents-md-generator}` | — | evaluate later; not core pipeline |
| `skills/{flutter-*,dart-*,run-app,wallet-review,move-to-windsurf,restore-from-windsurf,init-branch}` | — | skip: project/toolchain-specific |
| `agents/*.md` (crew of 11) | `agents/` | port + genericize (Phase 2) |
| `agents/docs/ticket-parsing.md` | `docs/` | port + genericize (Phase 1) |
| `docs/{autonomous-run,orchestrator-common}.md` | `docs/` | port + genericize (Phase 1) |
| `docs/{workflow-guide,skills-reference}.md` | `docs/` | adapt (Phase 6) |
| `hooks/*.py` | `hooks/` | port + config-driven verify (Phase 5) |
| `rules/sensitive-paths.json` | `hooks/` or `docs/` | port (Phase 5) |
| `tools/agent/` (Dart CLI) | TBD | rewrite or fold into hooks (Phase 5, open question) |
| `rules/ast-index.md`, Dart MCP rules | — | skip: toolchain-specific |
