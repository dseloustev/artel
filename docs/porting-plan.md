# Porting plan

*Status: draft v0.1 · 2026-07-27*

Source material lives in the originating project's `.claude/` directory (assumed available as a
sibling checkout, e.g. `../adguard-wallet`). Each phase leaves the repo in a coherent,
committable state. Genericization rules are defined in [design.md](design.md#genericization-strategy).

## Phase 0 — skeleton ✅

Repo scaffold, plugin manifest, marketplace file, docs. (This commit.)

## Phase 1 — contracts and config layer

The rules everything else obeys, plus the config mechanism they will reference.

- [x] Define `.artel/config.json` schema (ticket grammar, tracker/VCS adapters, verify
      commands, language, specs dir) + a documented default → [config.md](config.md).
- [x] Port and genericize `ticket-parsing.md` (ticket-ID grammar → config-driven) →
      [ticket-parsing.md](ticket-parsing.md).
- [x] Port and genericize `autonomous-run.md` (run state, modes, caps) — host-writable state
      moves to `.artel/run/` → [autonomous-run.md](autonomous-run.md).
- [x] Port `orchestrator-common.md` (the skill-orchestrator contract) →
      [orchestrator-common.md](orchestrator-common.md).

## Phase 2 — agents

- [x] Port the crew (12): `analyst`, `figma-analyst`, `researcher`, `planner`, `task-planner`,
      `tasklist-writer`, `vision-writer`, `implementer`, `reviewer`, `qa`, `validator`,
      `tech-writer`.
- [x] Strip source-project specifics (Flutter/Dart tool references, Jira project key,
      Bitbucket MCP tool names) → config lookups or adapter instructions.
- [x] Port and genericize the agent-crew contracts consumed by every agent: `deviation-protocol.md`,
      `path-conventions.md` → `docs/`.

## Phase 3 — stage skills

- [ ] Port à-la-carte stage skills: `analysis`, `researcher`, `planner`, `tasklist`,
      `generate-idea`, `generate-vision`, `generate-tasklist`, `implementer`, `run-reviewer`,
      `qa`, `docs-update`, `validate`, `pr-description`, `pr-create`, `sync-phases`,
      `figma-analysis`, `change-digest`, `address-pr-comment`.
- [ ] Port with rename: `flutter-inner-loop` → `inner-loop` (verify commands from config),
      `wallet-review` → `deep-review` (dual reviewer; tracker/VCS via adapters),
      `jira-issue-ru` → `issue-draft` (output language from config).
- [ ] Port runtime-gate skills as config-driven adapters: `run-app`, `drive-app` (launch/drive
      commands from config; RUNTIME_OK degrades to "skipped" when unconfigured), plus
      `add-automation` / `remove-automation` (scaffold commands from config).
- [ ] Port ops & utility skills: `init-branch`, `merge-conflicts`, `save-context`,
      `restore-context` (store path → artel), `agents-md-generator`.
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
- [ ] Push to GitHub, verify `/plugin marketplace add dseloustev/artel` install path.
- [ ] Tag `v0.1.0`.

## Source → plugin map

| Source (`.claude/…`) | Plugin | Action |
|---|---|---|
| `skills/feature-development`, `skills/dev` | `skills/` | port + genericize (Phase 4) |
| `skills/{analysis,researcher,planner,tasklist,implementer,run-reviewer,qa,docs-update,validate,pr-description,pr-create,sync-phases,generate-idea,generate-vision,generate-tasklist,figma-analysis,change-digest,address-pr-comment}` | `skills/` | port + genericize (Phase 3) |
| `skills/flutter-inner-loop` → `inner-loop`, `skills/wallet-review` → `deep-review`, `skills/jira-issue-ru` → `issue-draft` | `skills/` | port + rename + genericize (Phase 3) |
| `skills/{run-app,drive-app,add-automation,remove-automation}` | `skills/` | port as config-driven adapters (Phase 3) |
| `skills/{init-branch,merge-conflicts,save-context,restore-context,agents-md-generator}` | `skills/` | port + genericize (Phase 3) |
| `skills/{flutter-*,dart-*}` (informational how-tos) | `likbez` plugin | separate repo, seeded from source |
| `skills/{move-to-windsurf,restore-from-windsurf}` | — | skip: superseded by `save-context`/`restore-context` |
| `agents/*.md` (crew of 12, incl. figma-analyst) | `agents/` | port + genericize (Phase 2) |
| `agents/docs/ticket-parsing.md` | `docs/` | port + genericize (Phase 1) |
| `agents/docs/{deviation-protocol,path-conventions}.md` | `docs/` | port + genericize (Phase 2) |
| `docs/{autonomous-run,orchestrator-common}.md` | `docs/` | port + genericize (Phase 1) |
| `docs/{workflow-guide,skills-reference}.md` | `docs/` | adapt (Phase 6) |
| `hooks/*.py` | `hooks/` | port + config-driven verify (Phase 5) |
| `rules/sensitive-paths.json` | `hooks/` or `docs/` | port, categories configurable (Phase 5) |
| `tools/agent/` (Dart CLI) | `scripts/` or `hooks/` | Python rewrite: `verify`, `plan-check`, `codegen`; `dcm` folds into `verify` stages (Phase 5) |
| `tools/launch_app.dart` + `tools/templates/` | — | replaced by config-driven launch/scaffold commands behind `run-app`/`drive-app`/`add-automation` (Phase 3/5) |
| `rules/ast-index.md`, Dart MCP rules | `likbez` plugin | index-refresh in orchestrators becomes an optional config hook |
