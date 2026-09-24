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

Most workflow skills are **orchestrators, not workers**: they resolve ticket context, invoke the
matching agent via the `Agent` tool, and report results, never inlining the agent's work. A
self-declared subset of pure-procedure utility skills (e.g. `sync-phases`, `generate-idea`,
`merge-conflicts`) has no matching agent to invoke and runs its documented procedure inline
instead, keeping its procedural shape — each says so in its own body ("worker, not an
orchestrator"). For every skill that does dispatch an agent, this contract is the backbone of the
system and ports unchanged.

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
| Bitbucket via `mcp__aiguard__bitbucket_*` | `vcs.adapter`: `bitbucket-mcp` \| `github-cli`, enforced by the `vcs_guard` hook |
| `make verify`, Dart MCP analyze/format | `verify.commands` (list of shell commands), `verify.fast` (per-edit) |
| Russian PR descriptions / Jira comments | `language.docs`, `language.pr` |
| Figma design analysis | optional module, enabled only when `design.figma: true` |
| `specs/.current/` location | `specs.dir` (default keeps `specs/.current/`) |
| Spec trail readable only on the machine that produced it | `knowledge.adapter`: `kartoteka` \| `none` (+ base URL) |

Skills and agents reference config values instead of literals. When no config exists, the
entry-point skills run a short one-time init interview and write the file.

## Open questions

1. ~~**The deterministic CLI.**~~ Decided 2026-08-07: Python rewrite of `verify` + `plan-check`
   under `scripts/`; `codegen` dropped (`setup.commands` covers install/codegen) — see decision
   log.
2. ~~**Config file name and shape.**~~ Decided 2026-08-01: `.artel/config.json` in the host repo,
   run state alongside it under `.artel/run/` — see decision log and [config.md](config.md).
3. ~~**Tracker adapters at v1.**~~ Decided 2026-08-01: v1 ships `none` + `github-issues` +
   `jira-mcp` — see decision log.
4. ~~**Run-state and journal paths.**~~ Decided 2026-08-01: `.artel/run/` in the host repo — see
   decision log and [autonomous-run.md](autonomous-run.md).
5. ~~**Figma analysis.**~~ Decided 2026-08-01: ships in v1, runtime-optional (skips silently
   when no Figma MCP is connected) — see decision log.

## Open follow-ups

Work that is known, deliberate and not yet done. This section exists because `docs/superpowers/`
is **gitignored** — a follow-up recorded only in a spec's "Open questions" section leaves no
trace in the repository, and several had already gone invisible by the time they were collected
here (2026-09-06). Anything parked for later belongs here as well as in its spec. Resolved items
move to the decision log.

- **Phase 6: the non-Dart end-to-end dry run.** The last unchecked box in
  [porting-plan.md](porting-plan.md) and the genericization proof — every run so far has been on
  a Flutter host, which is the stack artel was ported *from*. The `tracker.adapter: "none"` gate-0
  defect fixed on 2026-09-05 is exactly the class of bug this catches, and it survived from
  2026-08-08 because the run never happened. 0.13.0 added a guard and two migration skills on top
  of adapter branches that this run has still never exercised against a real GitHub host.
- **What OpenCode names MCP tools is unverified, and the VCS guard's reach there depends on
  it.** `hooks/vcs_guard.py` and `opencode/plugin/artel.ts` originally required Claude Code's
  literal `mcp__` prefix before classifying a tool; both now match the platform token in the
  name instead (`bitbucket`, `github`, `jira`), which is what the design rule always said. That
  widening is correct on either host, but whether an OpenCode MCP tool actually surfaces as
  `<server>_<tool>`, as something namespaced, or as a name carrying no platform token at all
  cannot be settled from this repository — and if it is the last of those, Bitbucket MCP writes
  stay unguarded on that host. Needs one real OpenCode session with an MCP server attached to
  confirm the naming, then a line in [opencode.md](opencode.md) recording it.
- **The VCS guard does not cover `git push` or run an entry-point preflight** (from the
  2026-09-16 platform-migration design). A stale `origin` pointing at the old platform still
  pushes there; `set-home` moves it, but nothing enforces that it was run. Both were considered
  and deliberately declined for 0.13.0, so they are decisions rather than omissions — revisit if
  a stale remote ever causes a real push to the wrong host.
- **kartoteka 0.28.0's queue parameters are unused.** `task_ready` takes `parent_id` and
  `task_list` takes `order="created"`, both added for artel's stated needs.
  [task-queue.md](task-queue.md) §3 still claims unscoped and releases a wrong-phase row back to
  `ready`, and still recovers plan order by sorting client-side. Adopting them changes the claim
  protocol (a phase-scoped run must first learn its iteration's `task_id`), so it wants its own
  design pass rather than an in-place edit. Fix-section rows (0.15.0,
  [task-queue.md](task-queue.md) §6) are already shaped for it: one parent row per section, so
  a fix dispatch can claim from its own section by `parent_id` once the protocol adopts it, and
  gain the holder those rows lack today. Store mode (0.16.0) did not need them: the tasklist
  stayed a document (2026-09-22 decision log). Moving task state wholly into the queue — option
  A of the 2026-09-22 design — is where they would land.
- **Store mode and spec images: the live smoke test is still owed.** 0.16.0 and 0.17.0 shipped
  on the unit and doc-contract suites alone. The end-to-end run is to be done on a live project,
  with the result recorded here. It covers:
  - migration of documents and images (AW-3270);
  - a store-mode `dev` run whose checkpoint sweeps a new screenshot;
  - an outage;
  - the guard and its `Read` hint;
  - a live `artifact_patch`;
  - the dashboard rendering `design-analysis.md` with its images;
  - `image fetch` from a fresh worktree.
- **Spec images: parked follow-ups.** From the 2026-09-23 design, §15:
  - blobs out of SQLite if database size hurts backups;
  - an MCP `attachment_get` returning image content, once OpenCode support is known;
  - user-supplied images and Jira attachments as sources;
  - phase-relative image links.
  - **Per-Read hook cost.** Every Read in an artel project now starts `python3`, about 30 ms
    each: on OpenCode, through the bridge; on Claude Code, through the new `Read` matcher in
    `hooks.json`. A cheap image-extension check before the hook runs would avoid the cost for
    non-image Reads. On OpenCode that check goes in TypeScript, before `runHook`.
  - **Guard input parsing.** `spec_store_guard.py` parses its input inline instead of calling
    `hook_common.read_hook_input`, which the `ENTERS_ROOT_DIRECTLY` allowlist in
    `tests/test_hook_common.py` permits. A shared parse primitive in `hook_common` would remove
    the allowlist.

  Evidence files (`findings.json`, `observation.md`) moving into the store, and `.active_ticket`
  moving to `.artel/run/`, stay parked (2026-09-22 design, §16).
- **Spec-trail frontmatter is unblocked on kartoteka's side, not adopted** (from the 2026-09-15
  OKF review, decision log below). kartoteka 0.35.0 indexes a workspace artifact without its
  leading YAML frontmatter block, while the store and `artifact_get` keep it verbatim. That opens
  three ideas borrowed from OKF: frontmatter in place of the `## Metadata` / `- **Status:**`
  header bullets, a `verified` entry recording the plan-approval pause, and keyed `sources`
  citing kartoteka doc ids, which would make
  [knowledge-consultation.md](knowledge-consultation.md)'s ⚠ NON-CURRENT citation rule
  mechanically checkable. Any adoption inherits two constraints: the host's daemon must run
  0.35.0 or later before the mirror hook posts the first block (an older one indexes it as
  prose), and the block needs LF line endings and a closing `---` on its own line (anything else
  is indexed as prose, and nothing warns). The larger payoff is kartoteka's and waits on artel:
  once artifacts cite `sources`, kartoteka could flag one whose cited decision has since become
  `rejected` or `superseded_by` (§8.3 of
  `../kartoteka/docs/superpowers/specs/2026-09-15-artifact-frontmatter-design.md`).
- **`issue-draft` operator smoke test** (from the 0.10.0 redesign, 2026-09-04). The templates
  were calibrated on 2026-09-24 against ten real adguard-wallet tickets pulled from kartoteka
  (decision log, same date), but those runs were `--local` and non-interactive. The 0.20.0 evaluation ran kartoteka, Figma and code
  retrieval live; tracker reads have never run live (no tracker MCP in the evaluation session),
  and the interactive question round has still never run with a person. Also unverified: whether
  Jira renders the epic template's bracketed label hints (`As [user role]`) as text or as
  broken links.
- **Worktrees: the live smoke test has not been run** (from the 2026-09-17 worktree design,
  released in 0.14.0). The suite drives `scripts/worktree.py` against throwaway repositories, but
  three things need a real Claude Code session: that `$CLAUDE_PROJECT_DIR` really stays on the
  main checkout after `EnterWorktree` while the hook payload's `cwd` follows the worktree (the
  premise of the hook fix, taken from Claude Code's docs — if the variable already follows,
  that fix is a harmless guard and this entry should say so); an `init-branch` → work →
  `return-from-worktree` round trip; and whether the Flutter host's analyzer, run from the main
  checkout, descends into `.claude/worktrees/` (if it does, `docs/testing-flutter.md` needs an
  `analysis_options.yaml` exclude). Two extensions are parked behind it: a ticket-level lock so
  two sessions cannot run the same ticket, and a `/artel:worktrees` listing (ticket, branch,
  path, dirty state) once parallel use is common.
- **`deep-review`'s forecast constants are placeholders** (from the 0.8.0 design, 2026-09-02).
  The `0.5` weight for unlisted reviewers is a guess, not a measurement, and whether
  `review-forecaster` should run on `opus` or `sonnet` was deliberately started at `opus` to be
  lowered once the evidence rows read well. Both need real runs behind them.
- **No GitHub releases have been published.** Eighteen tags through `v0.13.0`, zero releases;
  `bump-version` prints the reminder at the end of every release and it has never been acted on.
  Fine if deliberate — but then it should be a decision here rather than a standing omission.
- **The license holder is provisional.** MIT under a personal name pending AdGuard's review of
  the plugin (decision of 2026-07-27, below). No record that the review has been requested.

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
- **2026-08-01 — Figma analysis ships in v1, runtime-optional** (open question 5). Design
  analysis is not deferred to a later release: it ships now, config-gated via `design.figma`
  (`docs/config.md`, default `false`), and even when enabled it degrades gracefully rather than
  blocking — a silent skip when no Figma MCP is connected, per
  [autonomous-run.md](autonomous-run.md) §13. Implemented by the `figma-analysis` skill
  dispatching the `figma-analyst` agent.
- **2026-08-01 — Gate 3.5 (plan-check) ports as spec, skips until Phase 5.** The
  `feature-development` port keeps the full bounce contract (bounce-line bookkeeping,
  `MAX_PLAN_CHECK_BOUNCES = 2`, exit-code semantics) but the deterministic checker itself —
  `scripts/plan_check.py`, the open-question-1 Python rewrite — ships in Phase 5. Until it
  exists the gate journals `PLAN_GROUNDED: skipped (plan-check ships in Phase 5)` and proceeds:
  an unshipped tool degrades exactly like an unconfigured gate, and the absence of a recorded
  green means the gate re-runs for real once the tool lands.
- **2026-08-01 — Runtime-surface detection becomes `runtime.surface`.** The source
  orchestrators skipped the `RUNTIME_OK` launch when no `*.dart` under `lib/` /
  `packages/*/lib/` changed — a Dart-specific test with no generic equivalent. Added an
  optional `runtime.surface` config key (array of globs, config.md): set → the gate runs only
  when the run's diff matches, else `skipped (no runtime surface)`; absent with `runtime.run`
  configured → the gate always runs. The source behavior is expressible as
  `["lib/**/*.dart", "packages/*/lib/**/*.dart"]`.
- **2026-08-01 — `setup.commands` config key; init-branch regains its post-branch step**
  (Phase-3 Task-7 follow-up). The source `init-branch` ran dependency install/codegen after
  branch creation; the Phase-3 port dropped it for lack of a generic key. Added
  `setup.commands` (array, `verify.commands` execution rules, default `[]`) and restored the
  step in `init-branch` — fatal on failure, silently skipped when empty. The `setup` skill's
  interview offers it as an optional extra.
- **2026-08-01 — The init interview is `/artel:setup`, its own skill.** A self-declared
  procedural worker both entry points invoke when `.artel/config.json` is missing and users
  invoke manually to create or revise the config (revise pre-selects current values). Named
  `setup`, not `init`: Claude Code's built-in `/init` (CLAUDE.md generator) already exists and
  artel's own `init-branch` chains it, so an `artel:init` would collide cognitively; kinship
  with `setup.commands` is a bonus. Writes one complete explicit config file (the config.md
  "filled example" shape) as its last step — an aborted interview writes nothing — and
  maintains the `.gitignore` entries for `.artel/run/` and `.artel/context/`.
- **2026-08-07 — Open question 1: Python rewrite of `verify` + `plan-check`; `codegen` dropped.**
  `scripts/plan_check.py` was effectively pre-decided — feature-development's Gate 3.5 already
  calls it. `scripts/verify.py` wraps the configured `verify.fast`/`verify.commands` in the
  source CLI's one-line JSON envelope (exit 0 clean / 1 findings / 2 environment error, kinds
  `invalid_argument`/`timeout`/`spawn_failed`/`command_not_found`/`internal_error`), giving hooks
  one deterministic contract over arbitrary commands: exit 126/127, a spawn failure, or a timeout
  classify as environment errors, any other non-zero as findings. The source `codegen` verb is
  not ported — its auto-detection rules (freezed/arb/API annotations) are inherently
  Dart-specific and `setup.commands` covers install/codegen generically. `plan_check.py` keeps
  the source anchor grammar, genericizes the backticked-path rule (any repo-relative token with
  a `/` and a file extension, Dart root whitelist dropped), and resolves symbols via `ast-index`
  when it is on PATH **and usable** (an unindexed or non-JSON response falls back to
  `git grep -l -w` per symbol), else `git grep -l -w` — no hard tool dependency. Phase-3 skill
  bodies keep running config commands directly; `verify.py` is the hooks' engine, not a forced
  migration.
- **2026-08-07 — Finding keys are digit-stripped output lines.** The source stop gate diffed
  structured `file:line:rule` keys; generic commands emit arbitrary text. A key = a non-empty
  output line of a red stage, ANSI-stripped, digit-stripped, whitespace-collapsed, deduped,
  prefixed `s<stage-index>:`, capped at 200 per stage — stable against shifting line numbers and
  timing noise ("Done in 3.2s") at the accepted cost of deduping same-rule-same-file findings.
  Keys are computed once in `verify.py`; hooks read them from the envelope.
- **2026-08-07 — `verify.surface` config key + `{files}` placeholder.** Replaces the source
  hooks' hardcoded `is_code_dart` filter: optional fnmatch globs (`!`-prefix excludes; only-
  excludes implies `*`; absent → every changed file counts), deliberately separate from
  `runtime.surface` (runtime and lintable surfaces are different sets). `verify.fast`/
  `verify.commands` entries may carry `{files}`, replaced with the space-joined shell-quoted
  changed paths; a blank `--files` value is an `invalid_argument`, never a silent widening to
  unscoped.
- **2026-08-07 — Sensitive-paths policy: shipped defaults + wholesale host override.** The
  plugin ships `hooks/sensitive-paths.json` with three generic categories: `secrets`
  (full-gates), `gate-config` (full-gates — an armed run must not rewrite its own gates or the
  host's hook wiring), `ci-cd` (plan-gate). A host `.artel/sensitive-paths.json` replaces the
  default wholesale — no merge semantics, the effective policy is always exactly one readable
  file; the `setup` skill offers to scaffold it from the defaults. Broader nets (migrations,
  lockfiles, infra) were rejected: too many innocent matches across ecosystems.
- **2026-08-07 — Hook state at `.artel/run/.hooks/`; hooks inert until configured.** Session
  baselines and verify-stop counters live in a dot-prefixed dir inside the already-gitignored
  run tree (can never collide with a ticket dir); the per-ticket stop-gate counter stays at
  `.artel/run/<TICKET>/.stop-gate-blocks` for source parity. The verify-layer hooks return 0
  immediately when `.artel/config.json` does not exist, so an installed-but-unconfigured plugin
  leaves zero footprint in the host repo.
- **2026-08-24 — The automation skills derive their file list with `-uall -z`, and substitute
  `{files}` themselves.** Both skills learn what the host's scaffold command touched by diffing
  `git status --porcelain`, which turned out to be the wrong reading of that command for the job:
  it collapses a newly created directory into a single `?? dir/` entry, and `test_driver/` is
  exactly what the source project's scaffold creates. That one entry then breaks three downstream
  steps — `rm -f` refuses a directory, so the verify-failure rollback leaves the tree dirty; the
  per-file paths `git show --stat` prints never match it, so the commit's "exactly those paths"
  check cannot pass; and a `{files}` linter is handed a directory. `-uall` expands it, `-z` drops
  the quoting `--porcelain` applies to names with spaces. Separately, both skills ran `verify.fast`
  as a raw config string while holding the very file scope the `{files}` token wants: with the
  source project's own config (which carries the token twice) the literal reached the shell and
  failed a correct scaffold. Skill bodies run config commands directly (the 2026-08-07 decision
  above), so they own the substitution `scripts/verify.py` would otherwise do for them. Also
  closed here: `add-automation` claimed a failed apply left "nothing to roll back" because
  preflight had proved the tree clean, which says nothing about a command that fails part-way
  through writing; and `remove-automation` had no default-branch guard despite committing *and*
  pushing — the one realistic way to meet the scaffold on the default branch is a branch merged
  with `AUTOMATION_REMOVED` red, and the fix for that is a branch and a PR, not a push to main.
- **2026-08-22 — The kartoteka mirror is a hook, not a step in each producing skill.**
  artel's spec trail is unreachable to any later agent asking why a decision was made; the
  fix is to dual-write it into a kartoteka artifact store (that project's spec §11). The
  mirror could have been an `artifact_put` call in each of the nine producing skills and
  agents, which is how the consuming project's spec described it and which would have made
  `stage` intentional and populated `author_agent`. It is a `PostToolUse` hook instead,
  because an agent-executed side effect happens only when the agent remembers, and this one
  is *invisible* when skipped — nothing breaks, the trail is quietly incomplete. artel
  already puts must-not-be-skipped work in hooks. Two consequences accepted: a hook is a
  subprocess and cannot call MCP tools, so it posts to the HTTP facade of the same service;
  and `PostToolUse` input does not carry the subagent name, so `author_agent` is always null
  (the field is nullable and self-reported on that side). Design:
  the consuming project's `2026-08-22-artel-dual-write-design.md`.
- **2026-08-25 — The session router is a hook-injected skill, inert without a config.**
  `skills/using-artel` copies `superpowers:using-superpowers`' mechanism: a `SessionStart`
  hook (`startup|clear|compact`) injects a routing table over every skill, because a skill
  description in a list is not a rule the model reliably follows, and an autonomous run
  compacts several times. It is injected only when `.artel/config.json` exists — the
  2026-08-07 zero-footprint rule — since a repo artel does not run against has nothing to
  route, and the entry points already invoke `/artel:setup` themselves. Capped at 10 KiB by
  test, carries `<SUBAGENT-STOP>`, lists no agents (every agent is behind a skill that resolves
  ticket context first), and states that an entry point is a complete process not to be
  wrapped in generic brainstorming or plan-writing skills.
- **2026-08-25 — Conversational kartoteka access is two worker skills, read and write.**
  `knowledge` (search, `related`, `index_status`, a non-active ticket's artifact history) and
  `tasks` (`list`/`add`/`done`/`block`/`release`) rather than one `kartoteka <verb>` skill or
  procedure inlined in the router: the contracts are already organised as a read side and a
  write side, a fat router would charge every session for the queue rules, and a router
  executing `task_create` from prose is a meta-skill doing a worker's job. Both refuse — no
  override — when `knowledge.adapter` is not `kartoteka`, for the same reason the pipeline's
  row 3 exists: an undeclared adapter names no project.
- **2026-08-25 — `tasks add` goes through `tasklist.md` and the existing mirror; no
  conversational claim; `release` is user-confirmed.** Titles are the idempotency key,
  promotion finds siblings by the `I<N> · ` prefix, and `task_ready` claims in `task_id`
  order, so a hand-built row sits outside all three; `add` appends a sectioned checkbox and
  runs `scripts/tasklist_tasks.py` + create-only `task_create` exactly as the pipeline does.
  `--raw` creates `backlog` only and says artel will not claim it (a `ready` bare row would be
  claimed by an implementer with no checkbox to flip). There is no `claim` verb — claiming is
  the implementer's — and `release` shows holder and age and asks first, because
  `docs/task-queue.md` §5 makes clearing a claim the user's call.
- **2026-08-25 — The router names one other plugin: `ast-index`.** Cross-plugin routing stays
  out of scope in general (likbez is not routed), but the AST index is different in kind: artel
  already depends on its CLI — `scripts/plan_check.py` resolves plan anchors through it — the
  index is project-agnostic, and the requests it answers ("find usages of X", "what is the
  project structure") are the ones a session in an artel repo makes before every stage. So
  `using-artel` carries a code-navigation group pointing at `/ast-index:ast-index` and
  `/ast-index:initialize`, gated on a fourth host-status line the hook computes with
  `shutil.which('ast-index')` — the CLI on PATH is the practical prerequisite the ast-index
  skill itself states, and whether the plugin's skill is installed is not knowable from a hook.
  With the CLI absent the group is inert, exactly like the kartoteka group with
  `knowledge.adapter: none`. The pipeline's own agents are unchanged: the source project's
  mandatory-ast-index rule went to likbez (2026-08-01) and stays there.
- **2026-08-25 — Code navigation gets its own contract; the pipeline's agents do use the index
  after all.** The entry above closed with "the pipeline's own agents are unchanged." That was
  right about the *rule* — the source project's mandatory-ast-index policy is likbez's and stays
  there — and wrong about the mechanics. Six agents already reached for "the host's optional
  code-symbol index," and every one of them pointed at `orchestrator-common.md` §1, which
  documents only the post-implementation *refresh* hook. There was no contract anywhere for
  *querying* an index: no availability probe, no index-before-grep rule, no staleness handling,
  no command mapping. The pointer was dangling for the case it was cited for most.
  [code-navigation.md](code-navigation.md) is that contract, shaped like
  [knowledge-consultation.md](knowledge-consultation.md) — numbered sections agents cite as `§N`.
  It is also the second sanctioned exception to the genericization rule, and a narrower one than
  the router: §2 names `ast-index` and its commands because an index-first rule without commands
  is unactionable, every *other* section is tool-neutral, and a host with a different index
  answers §1 with its own probe and §2 with its own table. Agents keep saying "the host's
  optional code-symbol index" and cite the file; the concrete names live in exactly one place,
  which is what the test guards. Newly wired: `reviewer` (resolve a diff's symbols before
  judging it — `changed`, `usages`, `implementations`), `tech-writer`, `vision-writer`
  (grounding, and finding what to reuse), `agents-md-generator` (`map` / `conventions` / `deps`
  are built for repo-shape discovery) and `merge-conflicts` Phase 3 (locating moved symbols).
  Deliberately not wired: `qa`, `validator`, `task-planner` read artifacts, not code — and
  `merge-conflicts` Phase 5 keeps its Grep, because a conflict marker is a string literal, which
  §3 makes the worked example of when *not* to use the index.
- **2026-08-25 — Superpowers' subagent-driven development is not adopted as a mode; three of
  its mechanics are.** Assessed `superpowers:subagent-driven-development` (v6.3.0) against the
  pipeline. Its premise — a controller session that dispatches a fresh worker per task and
  reviews each one — is artel's founding shape already (`dev` / `feature-development` loop
  `Skill: implementer`, which spawns the agent and keeps its id for resumes), and its
  compaction ledger is weaker than `run-state.json` + the journal + the Stop hook. Its
  "rulings, not stalls" rule is the opposite of the deviation protocol's major → ask, and stays
  out. What it had and artel lacked: (1) the implementer returned "files changed (with the
  actual diff)" — every task's diff sat in the orchestrator's context for the rest of the run,
  the single biggest lever on how often a long run compacts; now the diff, evidence and
  reasoning go to `.artel/run/<TICKET_ID>/reports/NNN-<slug>.md` and the completion is a
  short contract (autonomous-run.md §1, "Bulk stays in files"); (2) nothing forbade a worker
  from spawning its own reviewer, which superpowers observed duplicating the controller's
  review at full cost every time — the implementer and reviewer agents now carry a no-subagent
  rule; (3) no review between tasks inside a phase, so a misread acceptance criterion could be
  built on by every later task before the phase review saw it — that became the opt-in gate
  below. Not borrowed: batching same-shape tasks (conflicts with one claim per task on the
  queue), per-dispatch model tiering (a cost knob, not a workflow change; the agents keep their
  frontmatter `model:`), and the brief-file extraction (the agent reads its task from the
  tasklist it already owns).
- **2026-08-25 — Per-task review is `review.perTask`, off by default, one fix round, no
  re-review, and it feeds the phase review rather than replacing it.** The gate reuses every
  existing shape: findings land under `## Code Review Fixes` in the phase-aware tasklist (the
  section the phase review, the implementer's fix-list rule and the `REVIEW_OK` validator
  already understand), the fix round is a normal fix-list implementer dispatch counted toward
  `counters.correction_rounds`, and whatever the round leaves unchecked is the phase review's
  from there — so there is no second loop, no second cap artifact and no per-task re-review
  (`MAX_TASK_REVIEW_ROUNDS = 1`). Off by default because it is one reviewer seat per task and
  `task-planner` is told to make tasks small. Two mechanics it needed that did not exist: the
  implementer does not commit, so a task has no `BASE..HEAD` — `scripts/review_package.py`
  snapshots the working tree through a throwaway index (`read-tree --empty`, `add -A`,
  `write-tree` under `GIT_INDEX_FILE`), so the real index and the checkpoint's explicit
  staging are never touched, and writes the diff package to a file the reviewer reads; and the
  `reviewer` agent gained a **task** mode that grades one task's diff against that task's own
  acceptance criteria, writes `NNN-<slug>-review.md` beside the implementer's report, and
  deliberately does not write `review.md`, bump `**Review round:**`, run the lenses or write
  `findings.json` — those are the phase review's, and a per-task gate that touched them would
  corrupt its counters.
- **2026-08-26 — OpenCode becomes the second host, via a generator — canonical files stay
  Claude-flavored.** `scripts/build_opencode.py` derives an OpenCode install
  (`artel-`-prefixed skills/agents/commands, a host glossary prepended to every generated
  body, `${CLAUDE_PLUGIN_ROOT}` baked to the install root) from the canonical `skills/`
  and `agents/`; a TypeScript bridge (`opencode/plugin/artel.ts`) adapts OpenCode's
  plugin events onto the existing Python hook stdin/stdout contracts; a bridge-agnostic
  `scripts/install-opencode.sh` copies the plugin to `~/.config/opencode/artel/` and the
  generated artifacts into OpenCode's flat discovery directories. Claude Code is preserved
  by construction: the generator never writes outside `--out`, and the hook layer is
  untouched. Known softenings, documented in docs/opencode.md: the Stop gate becomes an
  idle re-prompt (OpenCode has no blocking Stop hook), `SendMessage` resume-by-id becomes
  a fresh Task dispatch, and per-agent model tiers are dropped (subagents inherit the
  caller's model).
- **2026-08-28 — `remove-automation` verifies the removal from git alone: an add-commit
  cross-check and a directory sweep.** The skill learned what the host's
  `runtime.scaffold.remove` changed from `git status` and took the command's exit `0` as proof
  the scaffold was gone — but git lists files, never directories, and a host command that
  deletes the entrypoint without its directory (or whose `rmdir` fails on an ignored file an
  editor or Finder dropped there) leaves the directory on disk under a clean `git status`, with
  a removal commit that still contains "exactly those paths". Two generic checks close this
  without the plugin learning any host artifact: `add-automation` always commits under one
  fixed subject, so the paths it added are recoverable from git and each must be gone (a
  survivor byte-identical to the scaffold's version is deleted by the skill itself — a plain
  `rm`, never `git rm`, so step 5's `git add` of the path still resolves — anything modified
  since is stop-and-report); and a directory whose every tracked file is among this run's
  deletions is the scaffold's own, removed when empty or holding only git-ignored entries and
  left with a stop on untracked work. Rejected: making the host command responsible (the
  wallet's script already does `rmdir`, and the failure is invisible from the host's exit
  code), and a second run of `runtime.scaffold.remove` as the completeness check (a host
  `pub get` per run, and an idempotent no-op proves nothing). `drive-app`'s
  `drive-observation.md` stays: it is spec-trail evidence, like `run-app`'s `observation.md`.
- **2026-09-02 — `deep-review` is one reviewer pass plus a kartoteka-grounded forecast, in one
  file.** The dual-review flow — two standalone `reviewer` dispatches, a merged
  `review-summary.md` with a QA plan, then plan mode — bought independence, not a second
  checklist, and produced three files nothing else consumed. Replaced by one `reviewer`
  dispatch and a new `review-forecaster` agent that groups the diff into change units, finds
  precedents for each in kartoteka's `pr` / `review_thread` documents, classifies how the
  reviewer reacted, computes a pass percentage and drafts fixes under a threshold — all into
  `<specs.dir>/<TICKET_ID>/deep-review.md`, which replaces `review-summary.md` in the mirror
  set. Rejected: a precedent pass inside `reviewer`'s standalone mode (the agent serves the
  per-task and phase gates too, and a fourth mode to protect them is more surface than a new
  agent) and doing the forecast in the skill body (orchestrators, not workers). The reviewer's
  own report becomes run-state evidence under `.artel/run/<TICKET_ID>/reports/`, which also
  closes the store-mode draft's open question about `review-claude.md` / `review-second.md`.
  Design: `docs/superpowers/specs/2026-09-02-deep-review-forecast-design.md` (local —
  `docs/superpowers/` is untracked).
- **2026-09-02 — The forecast has its own lookup budget and always writes its mode.**
  [review-forecast.md](review-forecast.md) §3 allows `search_knowledge` ×16 and `related` ×4
  per run — the first deliberate deviation from [knowledge-consultation.md](knowledge-consultation.md)
  §3's four searches, because a forecast needs about one search per change unit and the unit
  count is bounded by the diff, not by curiosity. And §1's mode line is written even when the
  adapter is `none`, deviating from consultation §4's no-record rule: the forecast table is a
  fixed part of a document the person asked for, and a table of dashes with no reason is the
  ambiguity §4 exists to prevent. The number is a Laplace-smoothed rate over judged precedents
  (§5), shown with its evidence, a dash at zero precedents — a grounded prior, not a model;
  kartoteka holds no "author changed the code" signal, so a fix request is judged from the
  thread text. Threshold 70 over the proposed 60 (`review.forecast.threshold`): with two to
  five precedents the estimate moves ten or fifteen points on one reclassified thread. The
  reviewer roster is `review.forecast.reviewers` because it changes; unlisted participants
  weigh 0.5, a placeholder to revisit against real outcomes.
- **2026-09-02 — Applying a forecast's fixes goes through `## Code Review Fixes`, not plan
  mode.** After writing the file the skill asks which set to apply and appends the chosen task
  blocks to the ticket-wide `tasklist.md`, then `Skill: implementer` once per task and
  `verify.commands` once — the pipeline's own review-fix path ([task-queue.md](task-queue.md)
  §6: file-scan work, never mirrored until 2026-09-19, below). No re-review loop; re-running
  the skill refreshes the forecast.
- **2026-09-04 — `knowledge.project` names the kartoteka namespace, with no default.**
  kartoteka 0.31.0 namespaces its store and index by project so one daemon can serve several
  repositories out of one database, and it refuses any write — `POST /api/artifacts`,
  `task_create`, `task_ready` — and `related()` that name none. artel carries the name as
  `knowledge.project` beside `adapter` and `baseUrl`, the block kartoteka's own integration
  note suggested, and puts it on **every** call: required where kartoteka requires it and as a
  scope on the reads (`search_knowledge`, `index_status`, `task_list`, `artifact_list`), which
  is what retires the six places where the docs justified the gate by kartoteka serving one
  project only. No
  default, deliberately: kartoteka removed its own because a guessed project appends to
  another project's deliberation trail, and a slug derived from the directory name would be
  exactly such a guess. A missing or malformed value is a configuration error resolved
  *before* the gating tables rather than as a fifth row — it is not a capability question,
  the tables are pinned at four rows, and `deep-review` already handles a bad adapter value
  the same way — with one record line spelled identically in all three contracts. The
  unscoped `index_status()` that every consultation already opens with doubles as the
  registration check, because a scoped read for an unregistered project answers with silent
  zeros. Rejected: a fallback to `ticket.projectKey` lower-cased (a Jira key is not a
  namespace, and two repos sharing one is the case the namespace exists for), and carrying
  the project in `scripts/tasklist_tasks.py`'s output (the script contacts nothing and the
  orchestrator reads the config for the gate anyway). Released as 0.9.0 — a breaking config
  change, minor pre-1.0 — in step with kartoteka's coordinated release.
- **2026-09-04 — `issue-draft` drafts against a template, consults kartoteka, and asks once
  before it writes.** A reading of the corporate Slack↔Jira bridge (`slackjira-service`) found
  its issue text comes from one structured-output call whose prompt is, item for item, the
  generation rules the skill has carried since the `jira-issue-ru` port; there was no prompt
  to adopt, and its Slack-side context handling and AI routing step were not adopted — input
  is text or a local file, and no issue type is inferred. What carries over is the principle
  that a draft is always produced and its gaps are listed rather than guessed. The skill now
  renders one universal, self-describing template (each section's
  `<!-- required|optional … -->` comment is its rule; a host overrides the whole file at
  `.artel/templates/issue-draft.md`, presence being the switch, as with
  `.artel/sensitive-paths.json`), consults kartoteka under the read-side contract to close
  gaps before anyone is asked — quoted and attributed, `⚠ NON-CURRENT` closing nothing, with
  two declared deviations (a tool error stops the consultation, not the draft; nothing is
  recorded under `<specs.dir>`) — and then asks the four highest-ranked remaining gaps in one
  `AskUserQuestion` round, listing what it did not ask or was not told under Missing Details.
  Slack-export input and the multiple-suggestions mode go; `language.pr` stays the output
  language and the dialect stays adapter-driven. Rejected: a drafting agent (the question
  round would cross the agent boundary twice, and utility skills stay procedural workers),
  per-type templates (they need exactly the type inference the rules forbid), and an
  iterative interview (one interruption per invocation). Released as 0.10.0 — a capability
  added and two behaviours removed, minor pre-1.0.
- **2026-09-05 — `knowledge.tokenEnv` names the kartoteka credential; the config never holds
  it.** kartoteka 0.32.0 (E3 phase 1) puts a bearer token on the whole port once
  `[auth] enabled = true` — every hosted daemon — and asked artel for three things: a config key
  naming the variable, the `Authorization` header on the mirror hook, and `--header` on the MCP
  registration. The key carries a variable's *name* because `.artel/config.json` is committed
  team configuration and a token is one machine's credential. A named variable that is unset
  sends the request unauthenticated rather than logging `misconfigured`: one committed config
  then serves a laptop against a loopback daemon with auth off and a host against a hosted one,
  and the daemon — not the hook — decides whether a token was needed. So a `401` is classified
  by what the hook had: a token sent and refused is a `reject` (permanent until the operator
  acts, like every other contract refusal), none sent is `misconfigured` naming the empty key or
  the unset variable. The default is empty, inert like every other optional key, over
  `"KARTOTEKA_TOKEN"`: an explicit log line beats a silent convention, and the setup interview
  suggests the conventional name anyway. The MCP session's token stays host wiring, not artel
  config — there is still no MCP URL in the config — because both clients expand an environment
  variable inside headers (`${VAR}` in `.mcp.json`, `{env:VAR}` in `opencode.json`), so one
  export feeds the hook and the session. Rejected: a file path to the token (a path in committed
  config points at a secret on every machine), and folding the token status into the adapter
  line of the host status (a separate line, presence only, never the value). Two guards came
  out of review: a value with control characters is `misconfigured` by name before any header
  exists — `http.client` would otherwise refuse the header with an error that echoes it, the
  one path on which the token could have reached the log — and a token is never sent over
  plaintext `http://` off loopback, since kartoteka refuses such a bind without TLS and the
  only thing at that address is a proxy's upstream port. The hook's proxy
  bypass stays, its promise restated as "the document goes to `baseUrl` and nowhere else", and
  the 2-second timeout stays until a hosted daemon shows it short. Released as 0.11.0 — an
  additive key with an inert default, minor pre-1.0; no coordinated release this time, since
  kartoteka's defaults are byte-identical to 0.31.0.
- **2026-09-05 — Pipeline gate 0 invokes `generate-idea` under every tracker adapter.** Resolves
  the reader-test finding of 2026-08-08 (porting-plan.md, Phase 6): gate 0 previously ran
  `generate-idea` only when `tracker.adapter` was not `"none"`, and under `"none"` merely relied
  on a `$1` description file or a pre-existing `idea.md`. Nothing in the pipeline ever wrote
  `idea.md` on that path, and gate 2 (`generate-vision`) hard-requires it — so an untracked run
  died at the vision gate with `Error: idea file not found`, whether or not a description file
  was passed. Gate 0 now delegates to `generate-idea` unconditionally, passing `$0 $1`. No branch
  is needed in the orchestrator because the skill already branches on `tracker.adapter` itself:
  its `"none"` path reads the description file, or runs the same input gate `analysis` does when
  there is none, and its pipeline-invocation preflight skips silently when `idea.md` exists, so
  resume semantics are unchanged. Rejected: teaching gate 2 to tolerate a missing idea (the idea
  is a real input to the vision, not ceremony), and leaving the seeding to the operator (the
  workaround [testing-flutter.md](testing-flutter.md) carried — a pipeline that cannot start
  itself from its own documented arguments is a defect, not a runbook step). This restores the
  `"none"` adapter as a first-class path, which is what makes artel usable in a repo with no
  tracker on day one.
- **2026-09-12 — `init-branch` reads the current branch first and creates one only when asked.**
  The port derived `feature/<TICKET_ID>` and checked it out or created it on every run, matching
  existing branches by exact name, so a host whose branches carry a slug
  (`feature/<KEY>-<num>[-<phase>]-<slug>`, the originating project's convention) got a duplicate
  branch each time. The skill now scans the current branch name for a
  `<ticket.projectKey>-\d+` token (the same scan `pr-create` and `address-pr-comment` use).
  Same ticket → stay, no git change. Different ticket → stop: restoring one ticket's spec trail
  onto another ticket's branch mixes the two, and switching away from someone's in-flight branch
  is not a setup step's call. No token → one question: check out an existing branch for the
  ticket (found by the same token scan, so a re-run whose generated slug differs still finds it),
  create `feature/<TICKET_ID>[-<N>]-<slug>`, or stay. Staying still runs the rest of the setup,
  since restore and `/init` are useful on any branch. The phase suffix is not compared: a phase-2
  run on the ticket's phase-1 branch stays. Rejected: a `branch.template` config key (no second
  host needs a different shape yet, and the prompt accepts a typed name for a one-off `bugfix/`),
  and falling back to the branch's ticket ID when no argument or `.active_ticket` is given (not
  asked for; ticket resolution stays uniform with `orchestrator-common.md` §2).
- **2026-09-15 — The spec trail does not adopt the Open Knowledge Format; kartoteka gets the part
  that fits.** Evaluated OKF (`GoogleCloudPlatform/open-knowledge-format`, v0.2) as the format for
  artel's generated specs. Declined: a ticket's spec trail is a workflow record with gate states,
  not a set of curated concepts someone keeps current and vouches for, which is what OKF's trust
  and lifecycle fields describe — and no artel consumer reads OKF. The spec is also young; v0.2
  already broke two v0.1 fields. The review sent two requests to kartoteka instead: an
  `export --okf` of a project's corpus, which kartoteka declined on the same grounds (its
  `PROJECT.md` §9), and stripping YAML frontmatter from artifacts on its index path, which
  shipped in kartoteka 0.35.0. The OKF ideas still worth borrowing if artel ever writes
  frontmatter are parked under "Open follow-ups" above. The prompt that carried the requests is
  `docs/superpowers/prompts/2026-09-15-kartoteka-okf.md` (local — `docs/superpowers/` is
  untracked).
- **2026-09-16 — the configured platform is enforced, not merely documented.** Every PR-touching
  skill already branched on `vcs.adapter`, but that branching is prose a model can drift past. A
  `PreToolUse` hook (`hooks/vcs_guard.py`, always armed — unlike `sensitive_guard.py`, it is not
  gated on an autonomous run) now denies any call that *writes* to a platform other than the one
  `vcs.adapter` (pull requests) or `tracker.adapter` (issues) declares. Domain is derived from
  the **platform**, not the tool name: Bitbucket answers to `vcs.adapter`, Jira to
  `tracker.adapter`, and GitHub — which hosts both — is judged against both. A review during
  implementation found that an earlier name-based heuristic (routing on whether a tool's name
  contained `issue`) could send a Bitbucket write into the tracker domain, where the default
  `tracker.adapter: "none"` left it unenforced; deriving the domain from the platform instead
  closed that gap. Foreign **reads** stay allowed, which is what lets `migrate-prs` read the
  Bitbucket PRs it recreates on GitHub. Verb extraction is positional, never substring:
  `bitbucket_get_pr_comments` is a read whose name contains the write token `comment`. An
  unrecognized verb is denied (fail closed); `guard.extraReadTools` rescues an unrecognized verb
  only, never a recognized write. Rejected along the way: a `vcs.repo` config key (it would
  duplicate `origin` and go stale — `origin` stays the single source of truth for coordinates),
  and moving `.active_ticket` or the run state into `.artel/config.json` (three different
  lifetimes — committed team config, a per-worktree pointer, and gitignored per-run state —
  and `.active_ticket` is referenced by 29 skill bodies, seven agent definitions and four hook
  modules: `grep -l active_ticket skills/*/SKILL.md agents/*.md hooks/*.py`). Bitbucket stays
  supported: artel is a public plugin. Released as 0.13.0, alongside `/artel:set-home` (moves a
  project between platforms) and `/artel:migrate-prs` (recreates a Bitbucket project's still-open
  PRs on GitHub) — see [CHANGELOG.md](../CHANGELOG.md).
- **2026-09-16 — `v0.13.0` is tagged on the merge, not on its `chore(release)` commit.** Ten
  commits landed after `ebfaeb3 chore(release): v0.13.0` — the VCS guard's write-detection
  fixes, OpenCode host guarding, `set-home`'s remote restore, and `tests/test_vcs_guard.py` —
  and every one of them amended the `[0.13.0]` CHANGELOG section in place instead of opening a
  new `[Unreleased]` one. The notes therefore describe the merge tip (`d473140`), so that is
  where the tag sits. `bump-version`'s Step 6 tags the release commit, which is correct whenever
  nothing follows it; when a release is finished on a branch afterwards, the tag follows the
  notes rather than the commit that first carried the version. The general rule: the tag points
  at the tree the `[<version>]` section actually describes.
- **2026-09-17 — one ticket, one worktree, one session.** To run several sessions on one host
  repo, a ticket's work can move into `.claude/worktrees/<name>` and the current session moves
  with it (`EnterWorktree`); `/artel:return-from-worktree` hands the branch back to the main
  checkout. The git work lives in a tested script (`scripts/worktree.py`), not in skill prose:
  stashes, the one-branch-one-worktree rule and removal are where work gets lost. Rejected:
  letting Claude Code create the worktree (`EnterWorktree(name)` names the branch
  `worktree-<name>`, carries no uncommitted work, and `ExitWorktree(remove)` deletes the branch);
  sibling directories outside the repo (they need an approval to enter, and a session inside a
  worktree can only switch to paths under `.claude/worktrees/`); merging the task branch locally
  on hand-back (it would bypass the PR flow). Uncommitted work travels as a stash, dropped only
  after it applied, so a failure always leaves it recoverable. The context store is shared by
  symlink, the run state moves, and host files are copied per `.worktreeinclude` — Claude Code's
  file, so one list serves `claude -w` too. The two skills are user-invoked only, so
  `init-branch` follows the shared procedure in [worktrees.md](worktrees.md) rather than chaining
  one. The move exposed a latent bug: hooks run from `$CLAUDE_PROJECT_DIR`, which per Claude
  Code's docs stays on the main checkout, so every gate checked the wrong tree in any worktree
  session; `hook_common.read_hook_input()` now moves into the session's linked worktree. Released
  as 0.14.0. Spec: `docs/superpowers/specs/2026-09-17-worktree-isolation-design.md` (local).
- **2026-09-19 — Fix-section tasks are recorded in the task queue, never offered.**
  `## Code Review Fixes`, `## Runtime Fixes`, `## Verify Fixes` and `## Final Verification`
  become rows: one parent per section (`CRF: Code Review Fixes`, …) and a child per checkbox
  titled `<CODE> · <source> · <checkbox text>`, created by whoever appends the checkbox, right
  after the append. On a host run on 2026-09-18 a deep review's 21 fix tasks ran for hours
  while `/artel:tasks list` read "queue drained". They are never `ready`: `task_ready` claims
  the oldest ready row for the ticket with no notion of section, so a ready fix row would go
  to any queue-path implementer and break phase-scoped claiming. The implementer still finds
  its task by file scan and moves the row by `task_update` (`in_progress`, `done`, `blocked`).
  The row is a record, the file stays the source of truth, and no holder is recorded, because
  only a claim sets one. Rejected: claimable fix rows now (that needs `task_ready`'s
  `parent_id`, the open follow-up above) and invisible ones (the old behaviour). `<source>` is
  a `### <source>` heading each writer opens its batch with, because titles are identity and
  `task_create` ignores the status of an existing title: a re-appended task matching an old
  `done` row would come back `done`, invisible again. The script warns on a repeat inside the
  file, and the mirror warns on an open box that resolves to a `done` row. The heading was
  chosen over a hidden HTML marker (people and LLM copies drop what they cannot see) and a
  per-task `Source:` bullet (every writer, every task). Phase files are mirrored for their fix
  sections, which live nowhere else, and the source names the phase. Fix writers create
  `data.sections` only; `data.sections` is absent when empty, so a tasklist without one prints
  exactly the 0.14.0 output. The implementer, not the orchestrator as first proposed, moves a
  `HITL:` fix row `blocked` and back: it holds the row id, and its `HITL:` line carries none.
  The reviewer agent writes the batch and `run-reviewer` records it, which gained `--local` so
  a local-only `feature-development` run still writes no rows. `/artel:tasks` gained
  `add --fix` and refuses to `release` a fix row, since releasing sets `ready`. kartoteka
  needed nothing new. Prompt: `docs/superpowers/prompts/2026-09-19-review-fix-queue.md` (local).
- **2026-09-22 — kartoteka is the spec store, activated by `knowledge.adapter` alone.** With
  the adapter on, spec documents live in kartoteka's artifact store and nowhere else; no
  `specs.store` key, because a project that consults kartoteka and keeps a second copy of its
  trail on disk is the duplication being removed. Supersedes the unbuilt 2026-08-31 store-mode
  design. Design: `docs/superpowers/specs/2026-09-22-kartoteka-spec-store-design.md`.
- **2026-09-22 — Agents speak MCP; edits are server-side patches; the tasklist stays a
  document.** kartoteka 0.43.0 added `artifact_patch`, so Read/Write/Edit map one-to-one onto
  `artifact_get`/`artifact_put`/`artifact_patch` and every agent keeps its logic. The
  queue-owned tasklist was deferred, not rejected; a write-through cache under `.artel/run/` was
  rejected as a local copy.
- **2026-09-22 — Scripts reach kartoteka over HTTP (`scripts/spec_store.py`).** Documents go
  script to script by pipe, never through an orchestrator's context; reverses 2026-08-31's
  "scripts stay offline", at the cost of a CLI-minted token where MCP signs in with GitHub.
- **2026-09-22 — The storage decision is a per-ticket file with a freshness window.**
  `.artel/run/<TICKET_ID>/spec-store.json`: sub-skills inherit it without a flag, the guard reads
  it, migration flips it.
- **2026-09-22 — Nothing is saved locally without asking; mid-run never continues locally.**
  Later gates read documents that exist only in kartoteka, so an outage pauses the run; an
  unsaved document is kept locally only with permission and moved in on resume.
- **2026-09-22 — Migration compares hashes against every stored version.** "The mirror can only
  lag" stopped being true once store-mode writes exist. `specs.allowLocalDeletion` was dropped:
  deletion is verified and confirmed instead.
- **2026-09-22 — A PreToolUse guard enforces the store** (`hooks/spec_store_guard.py`), because
  a stale sentence in any of sixty prompt files would otherwise fail silently into a local copy.
- **2026-09-22 — Migration switches a ticket to kartoteka only when nothing is left behind.** `migrate
  apply` flips the storage decision only when the ticket's whole local trail is current, stale, or
  resolved to the stored copy. A run the user kept local is never redirected while content they chose
  to keep is still on disk.
- **2026-09-22 — A headless migration uploads, but never deletes except pending saves.** Uploads are
  append-only, `expected_version`-guarded and verified, so they need no confirmation. Deleting a user's
  files does, so `--no-prompt` deletes only under `--pending-only`.
- **2026-09-22 — Migration classifies each local copy on its own, and never auto-uploads around a
  redaction.** From plan 3's final review:
  - a working-tree copy and a stale context snapshot of one document are judged separately;
  - a copy that may hold redacted text is always a conflict, shown with no diff;
  - `keep-stored` needs a stored version;
  - a `keep-local` answer is pinned to the version the user saw.
- **2026-09-23 — Images are inputs, not evidence.** `figma-analyst`'s `design/` screenshots are
  what the implementer builds from and what the validator judges runtime screenshots against. So
  they must travel with the documents that embed them. This amends the 2026-09-22 design's §1.2,
  which had kept `design/*.png` on disk as gate evidence. Runtime screenshots move with them.
  Evidence *text* (`observation.md`, `verify/*`, `findings.json`) stays on disk.
- **2026-09-23 — A separate attachment store, not binary artifacts.** kartoteka 0.44.0 keeps
  images in their own path-keyed, content-addressed store. Every text-only invariant of the
  artifact store (`artifact_get`, `artifact_patch`, the indexer, the renderer), and every
  document listing artel reads, stays untouched. It is HTTP only: producers need a script
  anyway, and viewing images through MCP depends on host support that is unverified on
  OpenCode.
- **2026-09-23 — A sweep at fixed points instead of per-producer uploads.** Images arrive through
  Bash, `curl`, host scripts and simulator tools, which no hook sees. The enforcement is
  `spec_store.py image sync`, run at the end of design analysis, before each checkpoint commit,
  in `pr-create` and at completion, together with a staging exclude. A failed sweep never
  pauses: nothing is lost while the images stay local and untracked.
- **2026-09-23 — Hard-require kartoteka 0.44.0.** It follows 0.16.0's hard requirement of 0.43.0,
  and avoids a mixed mode where documents are stored but images stay files. Design:
  `2026-09-23-kartoteka-spec-images-design.md`.
- **2026-09-23 — A tracked image's age is its last commit's author time.** Git resets mtime on
  checkout, so judging a committed image by mtime would let an old image become the newest stored
  version during migration. For a copy unchanged since HEAD, migration's `successor` rule uses
  `git log -1 --format=%at`. A locally modified copy keeps its mtime. No usable time means
  `conflict`.
- **2026-09-23 — An unrecoverable sweep is surfaced whole.** `image sync` exits `2` with kind
  `unrecoverable` when a file can be neither verified nor put back. The bytes are renamed to
  `<cache file>.unverified`, the message names that path and where the file belongs, and
  orchestrators copy the whole message into the journal and the report without pausing.
- **2026-09-24 — The gate diet.** Three real runs (AW-3270, AW-3187, AW-3342) showed the per-task
  whole-tree gate never found a task-owned defect and forced a hand-made baseline policy every
  time, the QA gate returned twelve positive verdicts in twelve, and the final check existed
  four times. Design: `docs/superpowers/specs/2026-09-24-gate-diet-design.md`; evaluation:
  `2026-09-24-workflow-evaluation.md`. Decisions:
  - **One contract, one runner.** `docs/gates.md` is the schedule; `scripts/verify.py task |
    checkpoint` are the gates; skills call them by name and never restate a command list.
  - **The task gate is `verify.fast` plus the tests a task touched** (`verify.test`, scoped by
    `verify.testSurface` — file globs only, never `test/**`, which selects helpers and mocks).
  - **The full gate runs at every phase checkpoint and nowhere else**; the final gate is the
    last checkpoint unless a `verify.surface` file changed since.
  - **Baseline by finding key, recorded once at a fresh arm**, never on resume; keys uncapped on
    the checkpoint gate; a stage green at arm time that fails silently later is red.
  - **QA and the validator leave the pipeline**, no switch: the reviewer carries the criteria
    table and the manual-checks list; the orchestrator's completion gate is an eight-fact
    checklist. Both skills stay à la carte.
  - **No `## Final Verification` section**, no gate-running tasks; older tasklists still parse.
  - **Fix-section parents close with their last child** and reopen on append — the follow-up
    "a generated ticket never reads fully done" is resolved on artel's side.
  - **Docs once per ticket**, on the last phase before its checkpoint commit.
- **2026-09-24 — `issue-draft` renders the team's per-type templates.** The corporate
  `jira-task-formatting` / `jira-bug-formatting` / `jira-epic-formatting` skills (and their
  shared Confluence-markup reference) were compared with `issue-draft` on ten real
  adguard-wallet tickets drawn from kartoteka, each drafted by the old skill and by the
  candidates in isolated runs and scored mechanically and by a blind judge given the team's
  conventions. The candidate won ten of ten in both rounds, on format alone — both sides
  invented and dropped next to nothing. Decisions:
  - **Three templates, one skill.** `task`, `bug` and `epic` templates replace the universal
    one; the type comes from `--type`, else the words of the request, else a defect as the
    source's main subject (bug), else task. An epic is never inferred. This reverses 0.10.0's
    rejection of per-type templates: that rejection assumed the type had to be inferred as a
    tracker field; here it only picks a template, and the user names it whenever it is not
    obvious.
  - **A `keep` rule beside `required` and `optional`.** The team's templates show fixed
    headings and table rows even when empty, for the reader to fill; `keep` renders them
    empty rather than padding or dropping them.
  - **The description holds only the issue.** Open questions move from a Missing Details
    section to the report — the team's templates have no such section and a judge scored it
    as noise.
  - **The team's markup rules live in a reference file** (`references/jira-wiki-markup.md`):
    bare links, `-` bullets, no blank lines inside lists, `| |` cells, escaped braces — with
    one deliberate correction, `----` for the horizontal rule, because the team template's
    `---` renders as an em dash (real tickets show it).
  - **Pasted tracker exports are filtered**: bot comments dropped, mentions kept only on
    contact or reviewer lines, unfilled template text treated as no data.
  - **Two long-standing input bugs fixed**: text with whitespace is never a path candidate
    (any pasted URL used to trigger the path question), and in free text only
    `<projectKey>-<digits>` is a ticket key (bare numbers used to match).
  - **The skill stays English; the drafts stay in `language.pr`.** Russian trigger phrases
    and headings from the source skills were not carried over; headings are rendered in the
    output language.
  - **Rejected:** three separate skills (they would triple the kartoteka and question-round
    machinery for a difference that is only the template), and Russian-literal templates in
    the plugin (a host that wants exact wording overrides the template).
- **2026-09-24 — `issue-draft` gathers context through an `issue-scout` agent.** The operator
  asked for drafts that carry the code they touch, links and parent looked up rather than asked,
  and the history and decisions of the area, with fewer questions — at a few minutes per draft.
  A read-only agent reads kartoteka (1 · ≤ 5 · ≤ 10), the tracker (≤ 6 issues), Figma frame
  names (≤ 6 links) and the host code (≤ 20 lookups) and returns a fact sheet; the skill closes
  gaps with `stated` facts, asks what stays open, and places at most six facts (three of them code) in the team's citation style, surveyed from ~95 adguard-wallet tickets (bare keys in relation sentences,
  inline code, decisions attributed to a person and date, no verbatim quotes). Decisions:
  - **One agent, before the question round.** 0.10.0 rejected a drafting agent because the
    question round would cross the agent boundary twice; retrieval crosses it once. Rejected:
    reusing researcher / figma-analyst (they write spec-trail artifacts) and inline retrieval
    (it floods the user's session).
  - **Paraphrase with attribution, a declared deviation from the consultation contract's §5.**
    The rule exists because later stages read artel's documents as instructions; a draft is read
    and filed by a person first, and the team cites by attribution. Retrieved imperatives still
    never become directives, and ⚠ NON-CURRENT travels verbatim.
  - **AC take nothing from retrieval**, and `inferred` is allowed only for where in the code the
    work lands — history must not turn into requirements.
  - **The Related section is gone**: related tickets are cited inline the way the team writes
    them; leftovers go to the report's Also found.
    - **`--local` leaves only the code source on** — it is the one source that is not the network.
  - **Six facts, three of them code, and noise at 3.5 accepted.** The first evaluation round, at
    eight facts, scored noise 3.5 against the 4.5 target and invented two Figma labels. The fix
    pass — source labels win, facts that change how the issue reads rank before code, a report
    heads-up for done or superseded work — removed the invented facts; the operator accepted
    noise at 3.5 on 2026-09-24 rather than trade away context.
