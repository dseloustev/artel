# skills/

Workflow skills — one folder per skill, `SKILL.md` inside. Installed, they surface as
`/artel:<name>`.

All [porting-plan Phase 3](../docs/porting-plan.md#phase-3--stage-skills) stage, runtime-adapter
and utility skills are in place, plus the
[Phase 4](../docs/porting-plan.md#phase-4--entry-point-orchestrators) entry points:
`feature-development` (full pipeline), `dev` (lean loop), and `setup` (the one-time config
interview both entry points trigger when `.artel/config.json` is missing). Most skills here are
orchestrators — they resolve ticket context, invoke agents, and report; they never inline the
agent's work. A handful are self-declared procedural workers instead (e.g. `sync-phases`,
`generate-idea`, `merge-conflicts`) — no agent matches their job, so they run their documented
procedure inline and say so in their own body.
