# Artel — design

*Status: draft v0.1 · 2026-07-27*

## Context

A production Flutter project accumulated a complete autonomous feature-development setup inside
its `.claude/` directory: orchestrator skills, a crew of agents, quality-gate hooks, operator
documentation, and a deterministic CLI. The system was designed and hardened iteratively (specs
and plans preserved in that repo under `docs/superpowers/`). It works: a Jira ticket goes in, a
Bitbucket PR comes out, one approval pause in between.

Artel extracts the **project-agnostic core** of that system into a Claude Code plugin so any
project can install it from a GitHub marketplace repo.

## Goals

1. **One-command install** — `/plugin marketplace add <owner>/artel` → `/plugin install artel@artel`,
   works in any repo regardless of language or toolchain.
2. **Keep the workflow's shape** — the pipeline stages, the spec trail
   (`specs/.current/<TICKET>/`), the single approval pause, the quality gates, resumability.
3. **Genericize, don't fork** — project-specific facts (ticket grammar, tracker, VCS host,
   verify commands, language conventions) become **configuration**, not edits to skill bodies.

## Non-goals

- Porting the informational Flutter/Dart reference skills (the `flutter-*` / `dart-*` how-to
  and conventions guides) — those seed the **likbez** companion plugin (separate repo).
  Workflow skills that merely carry a Flutter/wallet name (`flutter-inner-loop`,
  `wallet-review`, `run-app`, `drive-app`) **are** in artel's scope, in genericized form.
- Windsurf mirroring (`move-to-windsurf` / `restore-from-windsurf`) — tooling quirk of the
  source repo.
- Building a hosted service or GUI. Artel is files in a plugin: skills, agents, hooks, docs.

## Architecture

The plugin follows the standard Claude Code plugin layout. Skills become namespaced commands
(`/artel:feature-development`), agents become invocable subagent types, hooks register via
`hooks/hooks.json` using `${CLAUDE_PLUGIN_ROOT}` paths.

### Components

| Layer | Contents | Source of truth ported from |
|---|---|---|
| Entry points | `feature-development` (full pipeline), `dev` (lean loop) | orchestrator skills |
| Stage skills | `analysis`, `researcher`, `planner`, `tasklist`, `implementer`, `run-reviewer`, `qa`, `docs-update`, `validate`, `pr-description`, `pr-create`, `sync-phases`, `generate-idea`, `generate-vision`, `generate-tasklist`, `figma-analysis`, `inner-loop` (← `flutter-inner-loop`), `deep-review` (← `wallet-review`), `run-app`, `drive-app`, `change-digest`, `address-pr-comment` | orchestrator skills |
| Ops & utility skills | `init-branch`, `merge-conflicts`, `add-automation`, `remove-automation`, `save-context`, `restore-context`, `issue-draft` (← `jira-issue-ru`), `agents-md-generator` | utility skills |
| Agents | analyst, figma-analyst, researcher, planner, task-planner, tasklist-writer, vision-writer, implementer, reviewer, qa, validator, tech-writer | `.claude/agents/*.md` |
| Hooks | session baseline, fast per-edit verify, stop gate (+ verify), sensitive guard | `.claude/hooks/*.py` |
| Contracts | autonomous-run contract, orchestrator-common, ticket-parsing rules, deviation protocol, path conventions | `.claude/docs/`, `.claude/agents/docs/` |
| Operator docs | workflow guide, skills reference | `.claude/docs/` |

### The skill–orchestrator contract (kept as-is)

Workflow skills are **orchestrators, not workers**: they resolve ticket context, invoke the
matching agent via the `Agent` tool, and report results. They never inline the agent's work.
This contract is the backbone of the system and ports unchanged.

### The spec trail (kept as-is)

`specs/.current/<TICKET>/` with ticket-wide artifacts at the top and `phase-N/` subfolders for
phase-scoped ones; `specs/.current/.active_ticket` points at the in-flight ticket. This
convention is already project-agnostic and ports unchanged.

## Genericization strategy

Everything project-specific becomes per-project configuration that the installed plugin reads
from the **host repo** (not from the plugin): a single `.artel/config.json` at the host repo
root. Full key reference and defaults: [config.md](config.md).

| Hardcoded in source | Becomes config |
|---|---|
| Ticket grammar `AW-NNNN` / `AW-NNNN-P` | `ticket.pattern` (project key + phase-suffix rule) |
| Jira via `mcp__aiguard__jira_*` | `tracker.adapter`: `jira-mcp` \| `github-issues` \| `none` (+ tool prefix) |
| Bitbucket via `mcp__aiguard__bitbucket_*` | `vcs.adapter`: `bitbucket-mcp` \| `github-cli` |
| `make verify`, Dart MCP analyze/format | `verify.commands` (list of shell commands), `verify.fast` (per-edit) |
| Russian PR descriptions / Jira comments | `language.docs`, `language.pr` |
| Figma design analysis | optional module, enabled only when `design.figma: true` |
| `specs/.current/` location | `specs.dir` (default keeps `specs/.current/`) |

Skills and agents reference config values instead of literals. When no config exists, the
entry-point skills run a short one-time init interview and write the file.

## Open questions

1. **The deterministic CLI.** The source system's `agent` CLI (plan-check, gate verbs) is
   written in Dart — unacceptable as a hard dependency for "any project". Options: rewrite in
   Python (hooks already require `python3`), rewrite as POSIX shell, or drop the CLI and fold
   its checks into hooks. *Leaning: Python rewrite, shipped under `hooks/` or `scripts/`.*
2. ~~**Config file name and shape.**~~ Decided 2026-08-01: `.artel/config.json` in the host repo,
   run state alongside it under `.artel/run/` — see decision log and [config.md](config.md).
3. ~~**Tracker adapters at v1.**~~ Decided 2026-08-01: v1 ships `none` + `github-issues` +
   `jira-mcp` — see decision log.
4. ~~**Run-state and journal paths.**~~ Decided 2026-08-01: `.artel/run/` in the host repo — see
   decision log and [autonomous-run.md](autonomous-run.md).
5. ~~**Figma analysis.**~~ Decided 2026-08-01: ships in v1, runtime-optional (skips silently
   when no Figma MCP is connected) — see decision log.

## Decision log

- **2026-07-27 — Name: `artel`.** Chosen over `slipway`, `greenlight`, `baton`, `ticket-to-pr`.
  Short (matters for the `/artel:<skill>` prefix), the metaphor maps exactly onto the agent
  crew, distinctive and searchable. Repo and marketplace share the name — a single-plugin
  marketplace repo installable directly from GitHub.
- **2026-07-27 — Skeleton-first.** Repo scaffolded with manifest + docs before any porting, so
  further work happens inside the plugin repo itself.
- **2026-07-27 — License holder.** MIT under "Dmitry Seloustev" **deliberately** for now;
  switches to the AdGuard legal entity only after the company reviews and approves the plugin.
  Don't flag or change it before then.
- **2026-08-01 — Full workflow scope (user-approved).** Artel ports **every** workflow skill
  and agent from the source inventory ([source-inventory-workflow.md](source-inventory-workflow.md)):
  32 skills, 12 agents (incl. `figma-analyst`), all 5 hooks, the contracts and operator docs.
  Previously skipped/deferred items are now in scope: `init-branch`, `save-context`/
  `restore-context`, `add-automation`/`remove-automation`, `merge-conflicts`,
  `address-pr-comment`, `change-digest`, `agents-md-generator`, `figma-analysis`, `run-app`,
  `drive-app`. Renames: `flutter-inner-loop` → `inner-loop`, `wallet-review` → `deep-review`,
  `jira-issue-ru` → `issue-draft` (output language from config). Toolchain-bound skills
  (`run-app`, `drive-app`, `add/remove-automation`) port as adapter-shaped skills whose
  concrete commands come from `.artel/config.json` and degrade gracefully (e.g. RUNTIME_OK
  recorded as skipped) when unconfigured.
- **2026-08-01 — Companion plugin: `likbez` (user-approved).** The informational/reference
  skills ([source-inventory-informational.md](source-inventory-informational.md), 31 skills)
  become a second plugin named **likbez** (era-matched pair to "artel": the crew works, likbez
  educates it), seeded from the source project and extended later from the internet.
  `rules/ast-index.md` goes there too; orchestrator index-refresh steps in artel become an
  optional config hook. Separate repo; out of scope for this porting plan beyond this note.
- **2026-08-01 — Config lives at `.artel/config.json`** (open question 2). A dot-directory in the
  host repo rather than `artel.config.json` at the root or a section of the host `CLAUDE.md`: it
  keeps the host root clean, keeps machine-written configuration out of a human-authored doc, and
  gives the plugin one natural home for host-writable **run state** (`.artel/run/`, open question
  4) next to the config it belongs to. `.artel/config.json` is committed; `.artel/run/` is not.
  Nothing host-writable is ever placed in the plugin install/cache directory. Schema and defaults:
  [config.md](config.md).
- **2026-08-01 — v1 tracker adapters: `none` + `github-issues` + `jira-mcp`** (open question 3).
  `none` (ticket text from a local idea file) and `github-issues` (via the `gh` CLI) need no
  private MCP server, so artel is usable in any repo on day one; `jira-mcp` ports over from the
  source system almost unchanged and keeps parity for Jira shops, with the server addressed
  through a configurable `tracker.mcpToolPrefix` instead of a hardcoded tool name.
- **2026-08-01 — Run state and journals live under `.artel/run/`** (open question 4). The source
  system's `run-state.json`, `run-journal.md` and `open-questions.md` move from the spec trail
  into their own host-writable, gitignored tree next to `.artel/config.json`, keeping the spec
  trail under `specs.dir` purely human-readable documentation. `<specs.dir>/.active_ticket`
  stays where [ticket-parsing.md](ticket-parsing.md) already settled it — it is a phase pointer,
  not run bookkeeping. Schema and rules: [autonomous-run.md](autonomous-run.md).
- **2026-08-01 — Agent contract references via `${CLAUDE_PLUGIN_ROOT}`.** Agent bodies address
  plugin-shipped contracts as `${CLAUDE_PLUGIN_ROOT}/docs/<file>.md`: agents execute with the
  host repo as working directory, and the plugin-root variable is Claude Code's documented way
  to address bundled plugin files. Docs among themselves keep relative links.
- **2026-08-01 — Ported agents ship without a `tools:` frontmatter restriction.** With
  config-driven tracker/VCS/design adapters the required MCP tool names are unknowable at
  plugin-authoring time; the source project already hit this (its figma-analyst dropped its
  curated tool list because the list silently blocked MCP tools and ToolSearch). Agents inherit
  the full toolset; their prompts constrain behavior.
- **2026-08-01 — Ported agents keep their source `model:` frontmatter.** Per-agent model choices
  (opus for design-heavy agents, sonnet for mechanical ones) are portable cost/capability
  tuning, not project specifics; each ported agent keeps its source value. Companion to the
  `tools:`-drop decision already logged.
- **2026-08-01 — Ported skills drop `allowed-tools:` frontmatter.** Same rationale as the ported
  agents' dropped `tools:`: with config-driven tracker/VCS/design adapters, the required MCP tool
  names are unknowable at plugin-authoring time, and a curated `allowed-tools:` list silently
  blocks MCP tools and ToolSearch. Skills keep `model:` and `argument-hint:` — those are portable
  and don't name adapter-specific tools.
- **2026-08-01 — Introduced `specs.releases` config key (default `"specs/releases"`).**
  `agents/qa.md` and `agents/validator.md` kept a `specs/releases/<RELEASE_ID>` literal, each with
  a caveat noting config.md had no dedicated key for it yet. Per the genericization strategy
  ("everything project-specific becomes configuration"), added `specs.releases` to config.md's
  `specs` section — repo-relative, sibling to `specs.dir` rather than nested inside it, since a
  release spans multiple tickets. `agents/qa.md`, `agents/validator.md`, and the ported
  `skills/qa` / `skills/validate` bodies now reference `<specs.releases>` instead of the literal.
  Where a project keeps its release-scope QA/validation artifacts is project-specific state, not
  workflow-structural, so it belongs in configuration alongside `specs.dir`, not hardcoded.
- **2026-08-01 — Bitbucket `projectKey`/`repositorySlug` derived at runtime, not a config key.**
  `pr-description` and `pr-create` (Phase 3, PR/digest skills) need a Bitbucket Server
  `projectKey`/`repositorySlug` pair to address `bitbucket-mcp` PR-listing and PR-creation tools;
  the source skills hardcoded `flutter`/`adguard-wallet`, verified once by hand from `.git/config`.
  Unlike `specs.releases`, this did **not** become a new `docs/config.md` key: both values are
  mechanically derivable from `git remote get-url origin` (Bitbucket Server clone-URL shape
  `.../<projectKey>/<repositorySlug>.git`), mirroring the source's own verification method. The
  git remote is the single source of truth for where the repo actually lives — a config-file copy
  could drift from it after a repo move/rename/re-clone with nothing to catch the mismatch, whereas
  deriving it at call time never can. `skills/pr-description`, `skills/pr-create` derive
  `projectKey`/`repositorySlug` this way instead of hardcoding them or adding a new config key.
- **2026-08-01 — Restored the implementer's on-demand runtime-check hint (Task-3 follow-up,
  resolved with the run-app port).** The source `implementer` skill carried a brief, optional
  debugging hint distinct from the `RUNTIME_OK` completion gate: mid-task, when a change's effect
  isn't obvious from tests alone, the agent could launch the app to look. The Phase-3 implementer
  port (Task 3) dropped it — Dart/Flutter-specific launcher mechanics, no generic launcher to
  point at yet. Now that `run-app` exists with a config-driven `runtime.run`, restored as one
  sentence in `skills/implementer/SKILL.md`'s Phase-1 dispatch prompt: "you may launch via the
  `/artel:run-app` skill flow to observe it; this is not the RUNTIME_OK gate." Not duplicated into
  `agents/implementer.md` — source only ever carried the hint in the skill's dispatch prompt, and
  the agent already receives it whenever the skill invokes it; adding a second copy in the agent
  body would only risk drift between the two without adding capability.
- **2026-08-01 — Context store lives at `.artel/context/` (Task 7: `save-context`/
  `restore-context`).** The source system mirrored session context — root docs plus the spec
  trail — into a **user-level, cross-project** store keyed by checkout basename
  (`~/.claude/plugins/data/<tool>/<project>/`), so it survived branch switches, working-tree wipes,
  and even a full reclone. Artel drops the user-level store: like `.artel/run/`, the context store
  is host-repo-local, host-writable, and gitignored — `.artel/context/`, sibling to `.artel/run/`
  under the `.artel/` footprint (see [config.md](config.md#purpose-and-location)). What gets
  mirrored narrows to match: only the parts of the source store that correspond to concepts
  artel's ported docs already define — root `CLAUDE.md`/`CHANGELOG.md` and the ticket-scoped spec
  trail (`<specs.dir>/<TICKET_ID>/`, `<specs.dir>/.active_ticket` —
  [ticket-parsing.md](ticket-parsing.md)'s own vocabulary). Dropped: the source's curated
  mutable-`docs/`-subset mirror and its top-level loose `specs/`-file mirror, both keyed to fixed
  source-project filenames (`code_style_guide.md`, `conventions.md`, `mfa-locker.md`, …) with no
  generic equivalent in artel's config-driven model. Trade-off accepted knowingly: unlike the
  global store, `.artel/context/` does not survive a full reclone or a wipe of the working tree —
  the same cost already accepted for `.artel/run/`, and consistent with "host-writable state only
  under `.artel/`" ([porting-plan.md](porting-plan.md) global constraints). `docs/config.md`'s
  "Purpose and location" section now lists the new subtree alongside `.artel/run/`.
