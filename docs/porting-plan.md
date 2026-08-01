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

- [x] Port à-la-carte stage skills: `analysis`, `researcher`, `planner`, `tasklist`,
      `generate-idea`, `generate-vision`, `generate-tasklist`, `implementer`, `run-reviewer`,
      `qa`, `docs-update`, `validate`, `pr-description`, `pr-create`, `sync-phases`,
      `figma-analysis`, `change-digest`, `address-pr-comment`.
- [x] Port with rename: `flutter-inner-loop` → `inner-loop` (verify commands from config),
      `wallet-review` → `deep-review` (dual reviewer; tracker/VCS via adapters),
      `jira-issue-ru` → `issue-draft` (output language from config).
- [x] Port runtime-gate skills as config-driven adapters: `run-app`, `drive-app` (launch/drive
      commands from config; RUNTIME_OK degrades to "skipped" when unconfigured), plus
      `add-automation` / `remove-automation` (scaffold commands from config). Follow-up from
      Task 3: the source `implementer` skill had an on-demand runtime-check hint for
      mid-implementation debugging, distinct from the `RUNTIME_OK` completion gate; the port
      dropped it (Dart/Flutter-specific, no `run-app` to point at yet) — when `run-app` lands,
      decide whether `implementer` (agent + skill) should regain a generic `runtime.run` /
      `/artel:run-app` on-demand reference. Resolved in the run-app port: restored as a one-line
      optional hint in `skills/implementer/SKILL.md`'s dispatch prompt (`/artel:run-app` via
      `runtime.run`, explicitly not the `RUNTIME_OK` gate).
- [x] Port ops & utility skills: `init-branch`, `merge-conflicts`, `save-context`,
      `restore-context` (store path → artel), `agents-md-generator`. Follow-up from Task 7: the
      source `init-branch` ran a post-branch dependency-install/codegen step; no generic config
      key covers it — decide in Phase 4/5 (init interview / hooks config) whether a setup-command
      key is warranted. Resolved in Phase 4: `setup.commands` added to config.md and the step
      restored in `init-branch`.
- [x] Each skill resolves ticket context → invokes its agent → reports (no inlined work), except
      the self-declared procedural workers (`sync-phases`, `generate-idea`, `merge-conflicts`,
      etc.), which have no matching agent and run their documented procedure inline instead.
- [x] Phase-2 review follow-ups (fold into the matching skill ports): add `review/findings.json`
      and the ticket `verify/` evidence dir to ticket-parsing.md §3/§4 (with `run-reviewer` /
      `inner-loop`); uniform "(Phase 3)"/"(Phase 5)" forward-reference labels across agent
      bodies (figma-analyst's template path lacks one); planner.md's design-doc citation →
      `${CLAUDE_PLUGIN_ROOT}/docs/design.md` form; reconcile reviewer.md's "PR review approval"
      checkbox with the tasklist template (with `tasklist`); consider a `specs.releases` config
      key for the `specs/releases/` literal kept (with caveat) in qa.md/validator.md.

## Phase 4 — entry-point orchestrators

- [x] Port `feature-development` (full pipeline, one approval pause, resumability).
- [x] Port `dev` (lean loop: work-list confirmation → implement + review + runtime gate).
- [x] First-run init interview: no config found → interview → write `.artel/config.json`.
      Shipped as the `setup` skill (`/artel:setup`) — see design.md's decision log for the
      naming.

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
