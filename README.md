# Artel

> **артель** *(Russian)* — a self-organized crew of craftsmen who take on one job together and
> answer for the result as a collective.

Artel is a [Claude Code](https://claude.com/claude-code) plugin that packages a **spec-driven,
autonomous feature-development workflow**: a ticket goes in at one end, a reviewed pull request
comes out the other, and the pipeline in between runs itself with a **single human approval pause**.

A crew of specialized agents — analyst, researcher, planner, implementer, reviewer, QA, tech
writer — hands work down the line, leaving a durable spec trail behind:

```
idea → PRD → vision → plan → tasklist → ⏸ approval → implement → review → QA → docs → PR
```

## Status

**Released and installable.** This repo is published as its own single-plugin marketplace — see
[CHANGELOG.md](CHANGELOG.md) for the release history and [Install](#install) below.

The port from the private production setup ([docs/porting-plan.md](docs/porting-plan.md)) has
landed in full, with one item outstanding: Phase 6's end-to-end dry run on a **non-Dart** scratch
repo, which is the genericization proof. The Flutter smoke test is documented separately in
[docs/testing-flutter.md](docs/testing-flutter.md).

## What ships in the plugin

| Component | What it does |
|---|---|
| **Entry points** | `/artel:feature-development` (full pipeline, one approval pause), `/artel:dev` (lean loop), `/artel:setup` (one-time config interview) |
| **Stage skills** | Each pipeline stage à la carte: `analysis`, `researcher`, `planner`, `tasklist`, `implementer`, `run-reviewer`, `qa`, `docs-update`, `validate`, `pr-description`, `pr-create`, `figma-analysis`, `generate-idea`/`-vision`/`-tasklist`, `inner-loop`, `run-app`, `drive-app`, `sync-phases` |
| **Ops & utility skills** | `init-branch`, `merge-conflicts`, `deep-review`, `issue-draft`, `change-digest`, `address-pr-comment`, `add-automation`/`remove-automation`, `save-context`/`restore-context`, `move-to-worktree`/`return-from-worktree`, `agents-md-generator`, `set-home`, `migrate-prs` |
| **Session & kartoteka** | `using-artel` (turn-one router, hook-injected), `knowledge` (ask the index), `tasks` (operate the queue) |
| **Agents** | The crew of 13: analyst, figma-analyst, researcher, planner, task-planner, implementer, reviewer, review-forecaster, QA, validator, vision/tasklist/tech writers |
| **Hooks** | Quality gates ([hooks/README.md](hooks/README.md)): session baseline, fast per-edit verification, latching verify stop gate, run stop gate, sensitive-path guard, VCS/tracker platform guard, knowledge mirror, session router |
| **Scripts** | Deterministic gate engines: `scripts/verify.py` (JSON-envelope wrapper over the configured verify commands), `scripts/plan_check.py` (plan-anchor grounding check) |
| **Docs** | Operator guide, skills reference, and the contracts the pipeline obeys (autonomous run, ticket parsing, config, deviation protocol, path conventions) |

Core design properties:

- **Spec trail** — every ticket owns a `specs/.current/<TICKET>/` directory with its artifacts
  (`idea.md`, `prd.md`, `vision.md`, `plan.md`, `tasklist.md`, `review.md`, `qa.md`, …); phased
  tickets get `phase-N/` subfolders.
- **One approval pause** — the pipeline interviews you, drafts the plan, and stops exactly once
  for a green light; then runs implement → review → QA → PR unattended.
- **Gates, not vibes** — hooks enforce verification before "done": analyzer/tests must pass,
  the stop gate latches until evidence exists, sensitive paths are guarded in autonomous mode.
- **Resumable** — re-invoking the same entry-point command resumes an interrupted run.

## Install

```
/plugin marketplace add dseloustev/artel
/plugin install artel@artel
```

Then, in the host repo, run `/artel:setup` (or let the first `/artel:feature-development` /
`/artel:dev` invocation trigger it) to write `.artel/config.json` — the per-project
configuration every skill reads ([docs/config.md](docs/config.md)).

### OpenCode

From an artel checkout:

```bash
scripts/install-opencode.sh
```

Restart OpenCode; every skill is available as `artel-<name>` (TUI: `/artel-<name>`),
the agent crew as `artel-<name>` subagents, and the quality gates run through a bridge
plugin. Uninstall with `scripts/install-opencode.sh --remove`. Details and host
differences: [docs/opencode.md](docs/opencode.md).

## Repository layout

```
artel/
├── .claude-plugin/
│   ├── plugin.json        # plugin manifest
│   └── marketplace.json   # single-plugin marketplace (install straight from this repo)
├── skills/                # workflow skills (one folder per skill, SKILL.md inside)
├── agents/                # agent definitions only — one .md per agent, no prose (docs/agents.md)
├── hooks/                 # hooks.json + Python gate scripts + default sensitive-paths policy
├── scripts/               # deterministic gate engines (verify.py, plan_check.py)
├── tests/                 # stdlib unittest suite for hooks and scripts
├── docs/                  # operator guide, skills reference, contracts, design doc, porting plan
├── CHANGELOG.md
└── README.md
```

## Documentation

Operator-facing:

- [docs/workflow-guide.md](docs/workflow-guide.md) — the narrative guide: which command to
  reach for, how a full run unfolds, what to do when something stops
- [docs/skills-reference.md](docs/skills-reference.md) — per-skill lookup table: purpose,
  invocation, reads/writes, pause behavior
- [docs/config.md](docs/config.md) — the `.artel/config.json` schema and defaults
- [docs/testing-flutter.md](docs/testing-flutter.md) — how to smoke-test the plugin on a
  separate Flutter project

Contracts and internals:

- [docs/design.md](docs/design.md) — what Artel is, architecture, genericization strategy,
  decision log
- [docs/agents.md](docs/agents.md) — the crew: what belongs in `agents/`, and what must not
- [docs/porting-plan.md](docs/porting-plan.md) — phased plan for porting the source material
  into the plugin
- [docs/autonomous-run.md](docs/autonomous-run.md) · [docs/ticket-parsing.md](docs/ticket-parsing.md)
  · [docs/orchestrator-common.md](docs/orchestrator-common.md)
  · [docs/deviation-protocol.md](docs/deviation-protocol.md)
  · [docs/code-navigation.md](docs/code-navigation.md)
  · [docs/path-conventions.md](docs/path-conventions.md) — the rules the pipeline obeys
- [CHANGELOG.md](CHANGELOG.md)

## License

[MIT](LICENSE)
