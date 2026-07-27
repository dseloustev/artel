# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project

**Artel** — a Claude Code plugin packaging a spec-driven autonomous feature-development
workflow (ticket in → PR out, one approval pause). This repo is both the plugin and its own
single-plugin marketplace (`.claude-plugin/marketplace.json`), installable straight from GitHub.

Read these before making changes:

- [docs/design.md](docs/design.md) — architecture, genericization strategy, open questions,
  decision log. **Record notable decisions in its decision log.**
- [docs/porting-plan.md](docs/porting-plan.md) — phased plan and source→plugin map. Keep
  checkboxes current as work lands.

## Source material

The system being ported lives in a **sibling checkout** of the originating project (private
Flutter repo), normally at `../adguard-wallet`:

- `.claude/skills/` — orchestrator skills · `.claude/agents/` — the agent crew
- `.claude/hooks/` — Python quality gates · `.claude/docs/` — contracts + operator guide
- `docs/superpowers/{specs,plans,specs-claude-code}/` — the design history behind the system

When porting, **genericize** — never copy project literals: `AW-` ticket keys,
`mcp__aiguard__*` tool names, `make`/Dart/Flutter commands, wallet paths. Those become
`.artel/config.json` lookups (see design.md). If a source file mixes generic and specific
content, port the structure and replace specifics with config references.

## Conventions

- Plugin layout: `.claude-plugin/plugin.json` (manifest), `skills/<name>/SKILL.md`,
  `agents/<name>.md`, `hooks/hooks.json` + scripts. Hook paths use `${CLAUDE_PLUGIN_ROOT}`.
- Skill/agent names: kebab-case. Installed skills surface as `/artel:<name>`.
- Workflow skills are **orchestrators, not workers** — they resolve context, invoke agents,
  report. Never inline agent work into a skill body.
- All docs and commit messages in English. No absolute paths in docs — repo-relative paths or
  the `../adguard-wallet` sibling convention only.
- Commits: conventional commits (`feat:`, `fix:`, `docs:`, `chore:`). Subject line only, no
  trailers.
- Versioning: semver; keep `CHANGELOG.md` and `.claude-plugin/plugin.json` `version` in sync.
- Host-writable state (config, run state, journals) belongs in the **host repo**
  (`.artel/…`), never inside the plugin install/cache directory.

## Testing a change

Local smoke test: add this checkout as a local marketplace and install from it —
`/plugin marketplace add <path-to-this-repo>` then `/plugin install artel@artel` — and confirm
skills appear under the `artel:` namespace. (A scratch host repo works best; see
porting-plan Phase 6.)
