# The agent crew (`agents/`)

The crew — one `.md` per agent (frontmatter + system prompt): analyst, figma-analyst, researcher,
planner, task-planner, tasklist-writer, vision-writer, implementer, reviewer, qa, validator,
tech-writer, issue-scout (context for `issue-draft`, outside the pipeline), collaborative-reviewer
(the read-only analysis worker behind `/artel-collaborative-review`, outside the pipeline).

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
| `implementer` — a fix list, a round after a failed one (review round 2 or more, checkpoint verify round 2), and a task's one retry after its gate stayed red | `implementer.stepUp` |
| `reviewer` — a task review (route `full` tasks) | `reviewer.task` |
| `reviewer` — a ticket/phase review, round 1 | `reviewer.phase` |
| `reviewer` — a re-review after a fix round | `reviewer.reReview` |
| `reviewer` — plan review (gate 4.2) | `reviewer.plan` |
| `reviewer` — `deep-review`'s whole-branch review | `reviewer.deepReview` |
| `tech-writer` — the `pr-description` dispatch | `pr-description` |
| `planner` — the architecture / implementation plan | `planner.plan` |
| `task-planner`, `tasklist-writer` — the task breakdown and the tasklist | `planner.tasklist` |
| `researcher`, `issue-scout`, `figma-analyst` — research and context gathering | `planner.research` |
| `analyst`, `vision-writer` — the PRD and the vision | `planner.analysis` |
| `collaborative-reviewer` | `agents.collaborative-reviewer` |
| every other agent | `agents.<name>` |

The four kind sites share a fallback inside the block: an unset `planner.<kind>` resolves
`planner.default`, so `"planner": {"default": "…"}` sets the model for all of the planning work
at once and naming a kind carves out the exceptions. The resolver applies that fallback, so a
planning dispatch still makes one resolve call; `planner.default` on its own resolves like any
other site. An `agents.<name>` entry for an agent this table already names is ignored — every
dispatch has exactly one site.

### The dispatch procedure

Every dispatch of an artel agent — a specialised site in the table above or an ordinary
`agents.<name>` dispatch — applies this procedure at the dispatch site, before the `subagent`
call:

1. **Resolve the site.** `python3 ${ARTEL_ROOT}/scripts/models.py resolve --site <site>`. The
   site is the one the table above names for this dispatch; with no specialised site it is
   `agents.<name>`, where `<name>` is the agent's name without the `artel-` prefix.
2. **Read the answer.** A concrete `provider/model[#variant]` is passed as the call's `model`.
   `null` passes none: the child inherits the caller's model.
3. **An error stops the run.** A resolver exit `2` (for example `invalid_config`) is not an
   inherit: report it and stop — never dispatch on a guess.
4. **A refused model retries once.** A dispatch refused for its model (not available, or not
   allowed on this host) is re-dispatched once without one; a second refusal is reported and
   stops the run.

A dispatch site names its site and cites this section; it never restates the mechanics.

An iteration task's site follows its route, and a review's its scope — the distribution
superpowers 6.4.1's Model Selection and its strict-cost evaluations support
([design.md](design.md), decision log 2026-10-02). A fix list's first round resolves
`implementer.fix` whatever the task's route, and the round after a failed one steps up to
`implementer.stepUp`; the step-up rule is [autonomous-run.md](autonomous-run.md) §5. Keys,
allowed values and defaults: [config.md](config.md), `models`.

**A skill declares no `model:`.** The orchestrator and every stage skill run on the session's
model however they are reached: across the host runs of 2026-08-31 to 2026-09-30, typed
invocations switched the session's model in 156 of 166 activations, tool invocations in 0 of
230 — the measurement behind declaring none. Only the agent dispatches above carry a model. A
manual-only skill declares `metadata.opencode/autoinvoke: false` ([opencode.md](opencode.md)).

## Why this file is in `docs/`, not `agents/`

OpenCode registers **every** `.md` directly under `agents/` as an agent, named by its file
stem. A `README.md` there is loaded as an agent named `README` — no frontmatter, so it
reaches the picker with an empty description and "All tools", offered to the model as a real
dispatch target. The directory is a registry, not a place for prose: `agents/` holds agent
definitions only. `tests/test_plugin_surface.py` enforces that.

(`hooks/README.md` and `skills/README.md` are fine where they are — OpenCode does not scan
`hooks/`, and skills are discovered as `skills/<name>/SKILL.md`, so a loose `.md` in either
is inert.)
