# skills/

Workflow skills — one folder per skill, `SKILL.md` inside. Installed, they surface as
`/artel:<name>`.

All [porting-plan Phase 3](../docs/porting-plan.md#phase-3--stage-skills) stage, runtime-adapter
and utility skills are in place; entry points follow in
[Phase 4](../docs/porting-plan.md#phase-4--entry-point-orchestrators). Skills here are
orchestrators — they resolve ticket context, invoke agents, and report; they never inline the
agent's work.
