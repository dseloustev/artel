# AGENTS.md

## What this repo is

**Artel** — an OpenCode plugin packaging a spec-driven, autonomous feature-development
workflow: a ticket goes in, a reviewed pull request comes out, with a single human approval
pause in between. Install it from a checkout with `scripts/install-opencode.sh`
([docs/opencode.md](docs/opencode.md)).

## Where things live

`.opencode/skills/` (repo-local dev skills — not shipped) · `skills/` (workflow skills, one
folder per skill) · `agents/` (the crew; definitions only — see
[docs/agents.md](docs/agents.md)) · `hooks/` (quality gates) · `scripts/` (gate engines and the
installer) · `tests/` (stdlib unittest suite) · `docs/` (contracts and operator docs) ·
`opencode/plugin/artel.ts` (the bridge plugin) · `VERSION` (the release version).

## Tooling and commands

- Python 3.9+ stdlib-only — no package manager, no dependencies, no build step. The install
  copy is baked and wrapped at install time by `scripts/build_opencode.py`. CI runs the suite
  on 3.9 / 3.11 / 3.13 ([.github/workflows/tests.yml](.github/workflows/tests.yml)).
- Test: `python3 -m unittest discover -s tests -p 'test_*.py'`
- Release: [.opencode/skills/bump-version/SKILL.md](.opencode/skills/bump-version/SKILL.md) —
  bumps `VERSION` + `CHANGELOG.md`, then tags.
- Smoke-test a plugin change in a scratch host repo: `scripts/install-opencode.sh` from this
  checkout, then restart OpenCode; a hands-on Flutter guide is
  [docs/testing-flutter.md](docs/testing-flutter.md).

## Read before changing anything

- [docs/design.md](docs/design.md) — architecture, genericization strategy, open follow-ups and
  the decision log. **Record notable decisions in the decision log.**
- [docs/porting-plan.md](docs/porting-plan.md) — the port's phased plan and source→plugin map,
  kept as history.
- [README.md](README.md) — repository layout and the full documentation index.

## Conventions

- Plugin layout: one folder per skill under `skills/` (`SKILL.md` inside), one file per agent
  under `agents/`, the Python hook layer under `hooks/`, the bridge plugin under
  `opencode/plugin/`. Markdown addresses the install root with the `${ARTEL_ROOT}` token,
  baked at install time.
- Workflow skills are **orchestrators, not workers**: they resolve context, invoke agents and
  report — never inline agent work into a skill body.
- Ported bodies stay project-agnostic: never copy project literals (ticket keys, MCP tool
  names, Dart/Flutter commands, wallet paths) — they become `.artel/config.json` lookups.
  Source material: the private sibling checkout (`../adguard-wallet`).
- English everywhere; repo-relative paths in docs, no absolute paths.
- Conventional commits — subject line only, no trailers. Semver; keep `VERSION` and
  `CHANGELOG.md` in sync.
- Host-writable state (config, run state, journals) lives in the **host repo** (`.artel/…`),
  never inside the plugin install/cache directory.

## Publishing

- Repo: `https://github.com/dseloustev/artel`. `gh` operations need the corporate profile:
  `GH_CONFIG_DIR=~/.config/gh-adguard gh …`. Commits use the corporate git identity
  (`d.seloustev@adguard.com`, set in local git config).
