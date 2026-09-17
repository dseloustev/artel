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
`generate-idea`, `merge-conflicts`, `set-home`, `migrate-prs`) — no agent matches their job, so
they run their documented procedure inline and say so in their own body. `set-home` moves a
project to a different VCS platform, rewriting `vcs.*` in `.artel/config.json` and re-pointing
the git remotes together; `migrate-prs` follows it to recreate a Bitbucket project's still-open
pull requests on GitHub. `move-to-worktree` and `return-from-worktree` are workers too: they
move a ticket's work into its own git worktree and back, through `scripts/worktree.py`
([docs/worktrees.md](../docs/worktrees.md)).

Three of them are not pipeline stages: `using-artel` is the session router the `using_artel`
hook injects at turn one (a routing table over every other skill —
`tests/test_using_artel_docs.py` keeps it complete both ways), and `knowledge` / `tasks` are the
conversational front doors to kartoteka's read and write sides, under the same
`knowledge.adapter` gate the pipeline uses.
