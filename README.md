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

⚠️ **Early scaffolding.** The plugin is being ported from a private production setup and is not
yet functional. See [docs/porting-plan.md](docs/porting-plan.md) for what lands when.

## What ships in the plugin

| Component | What it does |
|---|---|
| **Workflow skills** | Orchestrator slash commands: `/artel:feature-development` (full pipeline), `/artel:dev` (lean loop), plus each stage à la carte (`analysis`, `researcher`, `planner`, `tasklist`, `implementer`, `run-reviewer`, `qa`, `docs-update`, `validate`, `pr-description`) |
| **Agents** | The crew: analyst, researcher, planner, task-planner, implementer, reviewer, QA, validator, vision/tasklist/tech writers |
| **Hooks** | Quality gates: session baseline, fast per-edit verification, latching stop gate, sensitive-path guard |
| **Docs** | Operator guide, skills reference, autonomous-run contract — the rules the pipeline obeys |

Core design properties:

- **Spec trail** — every ticket owns a `specs/.current/<TICKET>/` directory with its artifacts
  (`idea.md`, `prd.md`, `vision.md`, `plan.md`, `tasklist.md`, `review.md`, `qa.md`, …); phased
  tickets get `phase-N/` subfolders.
- **One approval pause** — the pipeline interviews you, drafts the plan, and stops exactly once
  for a green light; then runs implement → review → QA → PR unattended.
- **Gates, not vibes** — hooks enforce verification before "done": analyzer/tests must pass,
  the stop gate latches until evidence exists, sensitive paths are guarded in autonomous mode.
- **Resumable** — re-invoking the same entry-point command resumes an interrupted run.

## Install (once published)

```
/plugin marketplace add <owner>/artel
/plugin install artel@artel
```

> Replace `<owner>` with the GitHub account once the repo is published.

## Repository layout

```
artel/
├── .claude-plugin/
│   ├── plugin.json        # plugin manifest
│   └── marketplace.json   # single-plugin marketplace (install straight from this repo)
├── skills/                # workflow skills (one folder per skill, SKILL.md inside)
├── agents/                # agent definitions (one .md per agent)
├── hooks/                 # hooks.json + Python gate scripts
├── docs/                  # design doc, porting plan, operator guide
├── CHANGELOG.md
└── README.md
```

## Documentation

- [docs/design.md](docs/design.md) — what Artel is, architecture, genericization strategy,
  open questions
- [docs/porting-plan.md](docs/porting-plan.md) — phased plan for porting the source material
  into the plugin
- [CHANGELOG.md](CHANGELOG.md)

## License

[MIT](LICENSE)
