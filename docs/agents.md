# The agent crew (`agents/`)

The crew — one `.md` per agent (frontmatter + system prompt): analyst, figma-analyst, researcher,
planner, task-planner, tasklist-writer, vision-writer, implementer, reviewer, qa, validator,
tech-writer, issue-scout (context for `issue-draft`, outside the pipeline), collaborative-reviewer
(the read-only analysis worker behind `/artel-collaborative-review`, outside the pipeline), seat
(the orchestrator's tail one layer down, dispatched by `feature-development` when `seat.enabled` —
autonomous-run.md §18; never routed to directly).

Agent bodies must stay project-agnostic — host specifics come from `.artel/config.json`
(see [design.md](design.md#genericization-strategy)). Code navigation is the one
capability an agent uses without a config key: an index is project-agnostic, so agents say
"the host's optional code-symbol index" and cite
[code-navigation.md](code-navigation.md), which is the only file in the crew's reach
that names a concrete tool.

## Models

No agent carries a frontmatter `model:`. Every dispatch artel covers resolves its model from
`models.opencode` in the host's `.artel/config.json` through `scripts/models.py`, at call time,
and passes the concrete `provider/model[#variant]` on the `subagent` call; a site left unset
passes no model, so the child inherits the caller's:

| Dispatch | Config site |
|---|---|
| `implementer` — an iteration task on route `light` | `implementer.light` |
| `implementer` — an iteration task on route `full` | `implementer.full` |
| `implementer` — a fix list, first round | `implementer.fix` |
| `implementer` — a fix list, a round after a failed one (review round 2 or more, checkpoint verify round 2) | `implementer.stepUp` |
| `reviewer` — a task review (route `full` tasks) | `reviewer.task` |
| `reviewer` — a ticket/phase review, round 1 | `reviewer.phase` |
| `reviewer` — a re-review after a fix round | `reviewer.reReview` |
| `reviewer` — plan review (gate 4.2) | `reviewer.plan` |
| `reviewer` — `deep-review`'s whole-branch review | `reviewer.deepReview` |
| `tech-writer` — the `pr-description` dispatch | `pr-description` |
| `collaborative-reviewer` | `agents.collaborative-reviewer` |
| every other agent | `agents.<name>` |

An iteration task's site follows its route, and a review's its scope — the distribution
superpowers 6.4.1's Model Selection and its strict-cost evaluations support
([design.md](design.md), decision log 2026-10-02). A fix list's first round resolves
`implementer.fix` whatever the task's route, and the round after a failed one steps up to
`implementer.stepUp`; the step-up rule is [autonomous-run.md](autonomous-run.md) §5. A dispatch
refused for its model is re-dispatched once without one; a resolver error (exit `2`) is reported
and stops the run. Keys, allowed values and defaults: [config.md](config.md), `models`.

**A skill declares no `model:`.** The orchestrator and every stage skill run on the session's
model however they are reached: across the host runs of 2026-08-31 to 2026-09-30, typed
invocations switched the session's model in 156 of 166 activations, tool invocations in 0 of
230 — the measurement behind declaring none. Only the agent dispatches above carry a model. A
manual-only skill declares `metadata.opencode/autoinvoke: false` ([opencode.md](opencode.md)).

## Why this file is in `docs/`, not `agents/`

Claude Code registers **every** `.md` directly under `agents/` as an agent. A `README.md`
there is loaded as an agent named `README` — no frontmatter, so it lands in the picker with
an empty description and "All tools", offered to the model as a real dispatch target. The
directory is a registry, not a place for prose: `agents/` holds agent definitions only.
`tests/test_plugin_surface.py` enforces that.

(`hooks/README.md` and `skills/README.md` are fine where they are — Claude Code does not
scan `hooks/`, and skills are discovered as `skills/<name>/SKILL.md`, so a loose `.md`
in either is inert.)
