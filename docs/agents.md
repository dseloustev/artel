# The agent crew (`agents/`)

The crew — one `.md` per agent (frontmatter + system prompt): analyst, figma-analyst, researcher,
planner, task-planner, tasklist-writer, vision-writer, implementer, reviewer, review-forecaster, qa, validator,
tech-writer, issue-scout (context for `issue-draft`, outside the pipeline).

Agent bodies must stay project-agnostic — host specifics come from `.artel/config.json`
(see [design.md](design.md#genericization-strategy)). Code navigation is the one
capability an agent uses without a config key: an index is project-agnostic, so agents say
"the host's optional code-symbol index" and cite
[code-navigation.md](code-navigation.md), which is the only file in the crew's reach
that names a concrete tool.

## Models

Each agent's frontmatter `model:` is its default. Claude Code resolves a subagent's model as the
Agent call's `model`, then the agent's frontmatter, then `CLAUDE_CODE_SUBAGENT_MODEL`, then the
session's model — so a frontmatter model already beats the session's, and artel passes a model
on a call only where it varies:

| Dispatch | Model |
|---|---|
| `implementer` — every task, and a first fix round | `opus` (frontmatter) |
| `implementer` — a fix round after a failed one (review round 2 or more, checkpoint verify round 2) | `fable` |
| `reviewer` — task, phase and plan mode | `opus` (frontmatter) |
| `reviewer` — `deep-review`'s whole-branch review | `fable` |
| `review-forecaster` | `sonnet` (frontmatter) |
| every other agent | its frontmatter |

The step-up rule is [autonomous-run.md](autonomous-run.md) §5. A `sonnet` implementer for
`light` tasks was measured and rejected on 2026-09-30: about a quarter of the cost, but judged
no worse than `opus` on only 2 of 9 replayed tasks ([design.md](design.md), decision log).
Models are named by alias (`sonnet`, `opus`, `fable`): Claude Code maps each to a model of that
family — which one depends on the Claude Code build — and under an `availableModels`
restriction substitutes the newest permitted one. A call refused for its model is re-dispatched
once without `model`, on the frontmatter default.

**A skill's `model:` applies only when the person types the skill.** Invoked through the Skill
tool — by the router, or by one skill composing another — the line is ignored and the session's
model runs it: across the host runs of 2026-08-31 to 2026-09-30, typed invocations switched model
in 156 of 166 activations, Skill-tool invocations in 0 of 230. So the orchestrator
(`feature-development`, and its `/artel:dev` alias) declares none and runs on the session's
model however it is reached; a stage skill's line matters only when that skill is typed on its own.

OpenCode drops the tiers and ignores per-dispatch models ([opencode.md](opencode.md)).

## Why this file is in `docs/`, not `agents/`

Claude Code registers **every** `.md` directly under `agents/` as an agent. A `README.md`
there is loaded as an agent named `README` — no frontmatter, so it lands in the picker with
an empty description and "All tools", offered to the model as a real dispatch target. The
directory is a registry, not a place for prose: `agents/` holds agent definitions only.
`tests/test_plugin_surface.py` enforces that.

(`hooks/README.md` and `skills/README.md` are fine where they are — Claude Code does not
scan `hooks/`, and skills are discovered as `skills/<name>/SKILL.md`, so a loose `.md`
in either is inert.)
