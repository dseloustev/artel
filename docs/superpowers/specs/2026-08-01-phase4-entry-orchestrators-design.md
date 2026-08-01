# Phase 4 — entry-point orchestrators: design

*Status: approved · 2026-08-01*

Design for [porting-plan.md](../../porting-plan.md) Phase 4: port the `feature-development` and
`dev` entry-point orchestrators from the source project (`../adguard-wallet/.claude/skills/`) and
add the first-run init interview. Approach: **faithful port with mapped substitutions** — keep the
source skills' structure (step numbering, gate tables, the shared checkpoint section living in
`feature-development` with `dev` referencing it) and substitute genericized references, matching
the Phase 2–3 porting precedent and the cross-references [autonomous-run.md](../../autonomous-run.md)
§14 already makes.

## Deliverables

- `skills/feature-development/SKILL.md` — full-pipeline orchestrator.
- `skills/dev/SKILL.md` — lean-loop orchestrator.
- `skills/init/SKILL.md` — first-run init interview (procedural worker).
- Two new config keys: `runtime.surface`, `setup.commands` ([config.md](../../config.md)).
- Doc updates: config.md, autonomous-run.md, design.md decision log, porting-plan.md checkboxes,
  skills/README.md, plus a sweep of now-resolvable "(Phase 4)" forward-reference labels.

## Decisions (with rationale)

1. **Gate 3.5 (PLAN_GROUNDED) ports as spec, skips until Phase 5.** The full bounce contract
   (bounce-line bookkeeping, `MAX_PLAN_CHECK_BOUNCES = 2`, exit-code semantics) is ported now;
   the tool is defined as `${CLAUDE_PLUGIN_ROOT}/scripts/plan_check.py`, shipping in Phase 5.
   Until it exists the gate records `PLAN_GROUNDED: skipped (plan-check ships in Phase 5)` and
   journals the skip — consistent with how every unconfigured gate degrades, and no artifact
   means the gate is re-run for real once the tool lands.
2. **Runtime-surface detection becomes `runtime.surface`** (array of globs, optional). The
   source's `*.dart`-under-`lib/` test is expressible as
   `["lib/**/*.dart", "packages/*/lib/**/*.dart"]`. Surface set → gate 8 runs only when the
   phase's diff (vs default branch + worktree) matches, else recorded
   `skipped (no runtime surface)`. Surface absent with `runtime.run` set → the gate always runs.
3. **The init interview is its own skill: `/artel:init`.** A self-declared procedural worker (no
   agent). Both entry points invoke it when `.artel/config.json` is missing, then continue into
   their run; users can also invoke it manually to create or revise the config. No duplication,
   à-la-carte access — consistent with `sync-phases`/`generate-idea` shaping.
4. **`setup.commands` config key added now** (array of shell commands, default `[]`), closing the
   Phase-3 follow-up: `init-branch` regains its dropped post-branch dependency-install/codegen
   step, run when the key is non-empty and silently skipped otherwise. The init interview offers
   it as an optional extra.

## `feature-development` port

Structure ports verbatim: steps 1–9, the chatty-head gate table (0–4.5), the one pause, arm, the
autonomous-tail gate table (5–10.7), completion, PR close-out, description-file sync, final
report, `## Important`, and the shared `## Checkpoint commits & pushes` section at the bottom.

Substitutions:

| Source | Port |
|---|---|
| Frontmatter `allowed-tools` | dropped (design.md decision log); `model: sonnet` and `argument-hint` (incl. `--dry-run`, `--step`, `--mode`) kept |
| Contract paths `.claude/docs/…` | `${CLAUDE_PLUGIN_ROOT}/docs/…` |
| `specs/.current/` literals | `<specs.dir>` per [config.md](../../config.md); `.active_ticket` unchanged per [ticket-parsing.md](../../ticket-parsing.md) §6 |
| `run-state.json`, `run-journal.md`, `open-questions.md` in the spec trail | `.artel/run/<TICKET_ID>/…` per [autonomous-run.md](../../autonomous-run.md) §2/§3/§11 |
| Runtime retry counter `runtime/observation.md` bookkeeping | counter in `.artel/run/<TICKET_ID>/runtime-observation.md` (§5); spec-trail evidence stays at the phase-aware `runtime/observation.md` (ticket-parsing §4) |
| Gate 0 "Jira-keyed ticket" | `tracker.adapter != "none"` → `Skill: generate-idea`; `"none"` → `$1` description file / existing `idea.md`; neither → stop-and-ask |
| Gate 0.5 Figma grep | additionally gated on `design.figma` (skip silently when `false`) per autonomous-run §13 |
| Gate 3.5 `fvm dart .claude/tools/agent/agent.dart plan-check` | decision 1 above |
| Gate 6 `ast-index update` | the optional host index-refresh hook ([orchestrator-common.md](../../orchestrator-common.md) §1); silently absent |
| Gate 8 changed-`*.dart` detection | decision 2 above; cross-phase stub rule kept with a generic `TODO` marker referencing the owning phase; the source's "audited dead branch" annotation drops (source-history specific); env-failure → immediate cap escalation stays |
| `AW-XXXX-N` phase-traversal wording | `<TICKET_ID>-<N>` per ticket grammar (config-driven) |
| Checkpoint `make verify` | `verify.commands`, run sequentially, stop at first failure; empty ⇒ verify step records `skipped`, commit+push still happen |
| Checkpoint `dcm_global.yaml` pin restore | dropped (Dart-specific) |
| Branch-guard fallback `master` | fallback `main` |
| Checkpoint staging | ticket's changed files + `<specs.dir>/<TICKET_ID>/**` + `.active_ticket`; never `git add -A`, never `.artel/run/**`, never plugin directories |

New step 0 (small): the config gate — no `.artel/config.json` → `Skill: init`, then continue; plus
the run-start `vcs.mcpToolPrefix` validity check config.md promises entry points perform
("When the adapter is unusable").

## `dev` port

Same substitution set (config-gate step 0, `.artel/run/` paths, index-refresh hook,
`runtime.surface` gate 8 equivalent, `verify.commands` checkpoint). Distinctive parts port
verbatim:

- The input ladder: existing tasklist with incomplete tasks → one-screen confirm (dev's one
  pause); else `idea.md` + `vision.md` → `Skill: generate-tasklist` whose approval round IS the
  pause; else mini-interview (zero questions when unambiguous; ≤4 per round, max two rounds) →
  confirm → write the work list as the phase-aware tasklist.
- `yolo` skips only the confirmation; an ambiguous description still asks.
- No PRD / plan / QA / docs gates; `pr-create` is never invoked — opening the PR stays manual.
- Checkpoint procedure by reference:
  `${CLAUDE_PLUGIN_ROOT}/skills/feature-development/SKILL.md` `## Checkpoint commits & pushes` —
  the same sharing shape as the source.

## `init` skill

Self-declared procedural worker, `model: sonnet`. Flow:

1. **Existing config** → show current values, ask Revise / Abort; Revise re-runs the interview
   seeded with current values. Missing config → straight into the interview.
2. **Interview** (AskUserQuestion, batched ≤4 per round, roughly three rounds):
   (a) `ticket.projectKey` (the one value with no meaningful default) + `ticket.phaseSuffix`;
   (b) `tracker.adapter` (+ `tracker.mcpToolPrefix` when `jira-mcp`), `vcs.adapter`
   (+ `vcs.mcpToolPrefix` when `bitbucket-mcp`);
   (c) `verify.commands`, `verify.fast`, `language.docs` / `language.pr`.
   Then one optional-extras round offering `setup.commands`, `design.figma`, and the `runtime.*`
   commands — all skippable, defaults stay inert.
3. **Write** the complete `.artel/config.json` as a full explicit file in the shape of config.md's
   "filled example" (strict JSON, `version: 1`). The write is the last step — an aborted
   interview leaves no partial config.
4. **`.gitignore`**: idempotently ensure `.artel/run/` and `.artel/context/` entries exist.
5. **Report**: path written, reminder the file is committed team configuration; when invoked by an
   entry point, control returns and the run continues.

The interview happens pre-arm (chatty head): plain questions, no `pause_reason` machinery.

## Config additions

| Key | Type | Default | Semantics | Consumed by |
|---|---|---|---|---|
| `runtime.surface` | array of globs | absent | Decision 2: gates when RUNTIME_OK is worth running | both orchestrators' gate 8 |
| `setup.commands` | array of strings | `[]` | Decision 4: post-branch dependency install / codegen. New top-level `setup` section. | `init-branch` |

Both are documented in config.md's key tables, the default-config JSON, and the filled example.

## Doc updates

- config.md — "When the config is missing" names `/artel:init`; new key rows.
- autonomous-run.md — §5 runtime-row note mentions `runtime.surface`; §14's
  "(Phase 4 — see porting-plan.md)" parenthetical points at the shipped skill.
- design.md — decision-log entries for the four decisions above.
- porting-plan.md — Phase 4 checkboxes; Phase-3 `init-branch` follow-up marked resolved.
- skills/README.md — rows for the three new skills.
- Repo-wide sweep for "(Phase 4)" forward-reference labels that are now resolvable.

## Edge cases

- `--dry-run` ports as-is: chatty head + the step-3 presentation, print resolved mode + intended
  external actions, never write `run-state.json`, never arm.
- Headless behavior is autonomous-run §12 — the skills obey it, nothing new to define.
- The plan-check skip is journaled; a run resumed after Phase 5 lands re-runs the gate for real.
- Init aborted mid-interview writes nothing (write is atomic, last step).

## Testing

1. Doc-consistency pass: every `${CLAUDE_PLUGIN_ROOT}` and relative link resolves; no `AW-`,
   `make `, Dart/Flutter literals (same greps as prior phases).
2. CLAUDE.md local-marketplace smoke test in a scratch host repo: `/artel:feature-development`,
   `/artel:dev`, `/artel:init` surface under the `artel:` namespace; `/artel:init` end-to-end
   writes parseable config + `.gitignore` entries.
3. The full pipeline dry-run stays Phase 6.
