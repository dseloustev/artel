# The agent crew (`agents/`)

The crew — one `.md` per agent (frontmatter + system prompt): analyst, figma-analyst, researcher,
planner, task-planner, tasklist-writer, vision-writer, implementer, reviewer, qa, validator,
tech-writer.

Agent bodies must stay project-agnostic — host specifics come from `.artel/config.json`
(see [design.md](design.md#genericization-strategy)). Code navigation is the one
capability an agent uses without a config key: an index is project-agnostic, so agents say
"the host's optional code-symbol index" and cite
[code-navigation.md](code-navigation.md), which is the only file in the crew's reach
that names a concrete tool.

## Why this file is in `docs/`, not `agents/`

Claude Code registers **every** `.md` directly under `agents/` as an agent. A `README.md`
there is loaded as an agent named `README` — no frontmatter, so it lands in the picker with
an empty description and "All tools", offered to the model as a real dispatch target. The
directory is a registry, not a place for prose: `agents/` holds agent definitions only.
`tests/test_plugin_surface.py` enforces that.

(`hooks/README.md` and `skills/README.md` are fine where they are — Claude Code does not
scan `hooks/`, and skills are discovered as `skills/<name>/SKILL.md`, so a loose `.md`
in either is inert.)
