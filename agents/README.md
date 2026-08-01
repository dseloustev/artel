# agents/

The crew — one `.md` per agent (frontmatter + system prompt): analyst, figma-analyst, researcher,
planner, task-planner, tasklist-writer, vision-writer, implementer, reviewer, qa, validator,
tech-writer.

Agent bodies must stay project-agnostic — host specifics come from `.artel/config.json`
(see [design.md](../docs/design.md#genericization-strategy)).
