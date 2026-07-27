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

- Porting Flutter/Dart-specific skills (`flutter-*`, `dart-*`, `run-app`, `wallet-review`) —
  those stay in the source project or become a separate companion plugin later.
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
| Stage skills | `analysis`, `researcher`, `planner`, `tasklist`, `implementer`, `run-reviewer`, `qa`, `docs-update`, `validate`, `pr-description`, `pr-create`, `sync-phases`, `generate-idea`, `generate-vision`, `generate-tasklist` | orchestrator skills |
| Agents | analyst, researcher, planner, task-planner, tasklist-writer, vision-writer, implementer, reviewer, qa, validator, tech-writer | `.claude/agents/*.md` |
| Hooks | session baseline, fast per-edit verify, stop gate (+ verify), sensitive guard | `.claude/hooks/*.py` |
| Contracts | autonomous-run contract, orchestrator-common, ticket-parsing rules | `.claude/docs/`, `.claude/agents/docs/` |
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
from the **host repo** (not from the plugin). Proposed: a single `artel.config.json` at the host
repo root (exact name/location to be settled in Phase 1).

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
2. **Config file name and shape.** `artel.config.json` vs `.artel/config.json` vs a section in
   the host `CLAUDE.md`. *Leaning: `.artel/config.json` — keeps host root clean, gives the
   plugin a natural place for run state too.*
3. **Tracker adapters at v1.** Source supports Jira-via-MCP only. Ship v1 with `jira-mcp` +
   `github-issues` + `none` (manual idea file), or Jira only? *Leaning: `none` + `github-issues`
   first — they need no private MCP server; `jira-mcp` ports easily for parity.*
4. **Run-state and journal paths.** Source keeps run state under the host `.claude/`; plugin
   should keep host-writable state out of the plugin cache dir. Candidate: `.artel/run/`.
5. **Figma analysis.** Depends on the Figma MCP server being connected; port as optional module
   in a later phase.

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
