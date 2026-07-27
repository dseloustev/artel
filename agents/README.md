# agents/

The crew — one `.md` per agent (frontmatter + system prompt): analyst, researcher, planner,
task-planner, tasklist-writer, vision-writer, implementer, reviewer, qa, validator,
tech-writer.

Empty until [porting-plan Phase 2](../docs/porting-plan.md#phase-2--agents). Agent bodies must
stay project-agnostic — host specifics come from `.artel/config.json`
(see [design.md](../docs/design.md#genericization-strategy)).
