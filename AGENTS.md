# AGENTS.md

## What this repo is

**Artel** — an OpenCode plugin packaging a spec-driven, autonomous feature-development workflow: a
ticket in, a reviewed pull request out, one human approval pause in between.

## Where things live

`skills/` (workflow skills) · `agents/` (the crew of 14 — [docs/agents.md](docs/agents.md)) ·
`hooks/` (quality gates — [hooks/README.md](hooks/README.md)) · `scripts/` (gate engines, spec
store, task grammar and queue, worktrees, parallel) · `opencode/plugin/` (`artel.ts` bridge,
`parallel.ts` worker) · `tests/` · `docs/` · `.opencode/skills/` (dev-only) · `VERSION`.

## Tooling and commands

- Python 3.9+ stdlib-only, no dependencies, no build step; `scripts/build_opencode.py` bakes the
  install copy. CI runs the suite on 3.14 only, the latest stable interpreter
  ([tests.yml](.github/workflows/tests.yml)); 3.9 is the compatibility floor, not per-change CI.
- Test: `python3 -m unittest discover -s tests -p 'test_*.py'`. 27 of the files are **doc-contract
  tests** — change a contract doc or a skill's documented behavior and its test changes with it.
- Release: [.opencode/skills/bump-version/SKILL.md](.opencode/skills/bump-version/SKILL.md) —
  bumps `VERSION` + `CHANGELOG.md`, then tags.
- Smoke-test in a scratch host repo: `scripts/install-opencode.sh`, then restart OpenCode
  ([docs/opencode.md](docs/opencode.md), [docs/testing-flutter.md](docs/testing-flutter.md)).

## Read before changing anything

- [docs/design.md](docs/design.md) — architecture, genericization strategy, open follow-ups,
  decision log. **Record decisions there** — parked work too: `docs/superpowers/` is gitignored.
- [README.md](README.md) — repository layout and the full documentation index.
- Contracts: [gates](docs/gates.md) · [spec storage](docs/spec-storage.md) ·
  [worktrees](docs/worktrees.md) · [task queue](docs/task-queue.md) ·
  [parallel workers](docs/opencode-parallel.md) — gates cited, never restated.

## Changing things

- **New hook**: a `hooks/*.py` module, a call site in `opencode/plugin/artel.ts`, an entry in
  [hooks/README.md](hooks/README.md) (its ABI list is pinned to the bridge), and a test.
- **New skill**: `skills/artel-*/SKILL.md` + an entry in
  [docs/skills-reference.md](docs/skills-reference.md); the build discovers it, no registration.

## Conventions

- Layout: one folder per skill under `skills/`, one file per agent under `agents/`, the hook layer
  under `hooks/`, the bridge under `opencode/plugin/`; Markdown uses the `${ARTEL_ROOT}` token.
- Workflow skills are **orchestrators, not workers**: they resolve context, invoke agents and
  report — never inline agent work into a skill body.
- Bodies stay project-agnostic: never copy project literals (ticket keys, MCP tool names,
  Dart/Flutter commands, wallet paths) — they become `.artel/config.json` lookups; source material
  is the private sibling checkout (`../adguard-wallet`).
- Conventional commits — subject line only, no trailers. Semver; keep `VERSION` and
  `CHANGELOG.md` in sync. English only; repo-relative paths in docs, no absolute paths.
- Host-writable state (config, run, journals) lives in the host repo's `.artel/`, not the plugin.

## Publishing

- Repo: `https://github.com/dseloustev/artel`. `gh` needs `GH_CONFIG_DIR=~/.config/gh-adguard`;
  commits use the corporate identity `d.seloustev@adguard.com` (set in local git config).
