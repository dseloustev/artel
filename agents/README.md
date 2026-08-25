# agents/

The crew — one `.md` per agent (frontmatter + system prompt): analyst, figma-analyst, researcher,
planner, task-planner, tasklist-writer, vision-writer, implementer, reviewer, qa, validator,
tech-writer.

Agent bodies must stay project-agnostic — host specifics come from `.artel/config.json`
(see [design.md](../docs/design.md#genericization-strategy)). Code navigation is the one
capability an agent uses without a config key: an index is project-agnostic, so agents say
"the host's optional code-symbol index" and cite
[code-navigation.md](../docs/code-navigation.md), which is the only file in the crew's reach
that names a concrete tool.
