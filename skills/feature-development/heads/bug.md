# The bug head

The head for a `bug` size: find the cause before anything is planned, then write the work list
from the diagnosis. Read by `feature-development` step 4
(`${CLAUDE_PLUGIN_ROOT}/skills/feature-development/SKILL.md`). `SKILL.md` below is that file;
its steps 0–3 have run: the config is read, the ticket is active, the spec store is resolved,
`idea.md` exists (gate 0) and the size is recorded. Its flags, and its step 2 rule for reading
"artifact exists" and a status on the kartoteka path, apply here. Pass `$0` to every sub-skill.

This is the chatty part of the run: it is not armed yet, so every question here is a plain
`AskUserQuestion` with no `pause_reason`. No PRD, vision or plan is written here.

## Gates

| # | Gate | Action (skip if the artifact exists) |
|---|------|--------------------------------------|
| B1 | `DIAGNOSED` — `diagnosis.md` has status `DIAGNOSED` or `DIAGNOSED_STRUCTURAL` (a `NOT_REPRODUCED` one goes to its question, below) | `Skill: debugging` with `$0 --diagnose`, plus `--local` when this run was invoked with it |
| B2 | the work list — `tasklist.md` exists | by the diagnosis' status, below |

**Gate B1.** The debugging skill's diagnose mode runs `${CLAUDE_PLUGIN_ROOT}/docs/debugging.md`
§2.1–§2.3 on the main thread — investigate and reproduce, compare, hypothesise and confirm — and
stops before the fix. It leaves no fix, no failing test and no probe in the tree, and writes
`<specs.dir>/<TICKET_ID>/diagnosis.md`: the symptom, the reproduction, the root cause, the
evidence, the fix origin, and whether the fault is structural. Its last line is
`Diagnosis: <status> — <path>`. Take the outcome from the status — that line, or on resume
`spec_store.py status` over the document — without the document entering this context.

## By the diagnosis' status

| Status | Then |
|---|---|
| `DIAGNOSED` | Gate B2: `Skill: generate-tasklist` with `$0`, plus `--local` when this run was invoked with it. The writer reads the diagnosis: its first task writes the failing test for the reproduction and names it in `Test:`, and the fix goes at the fix origin and nowhere else. The skill's approval round is the run's one pause — add none. |
| `DIAGNOSED_STRUCTURAL` | A raise (`SKILL.md` step 3, "The ratchet"): rewrite `sizing.json`, print `Size raised: bug → architectural — full head. Reason: <reason>.` with the diagnosis' structural condition as the reason, then read `${CLAUDE_PLUGIN_ROOT}/skills/feature-development/heads/full.md` and run it. Its PRD interview starts from the diagnosis. |
| `NOT_REPRODUCED` | Ask (plain `AskUserQuestion`): **Give more detail** — run gate B1's skill again with what the person adds / **Treat it as a bounded change** — rewrite `sizing.json` (`size` `bounded`, `head` `lean`, `decided_by` `flag`) and read `${CLAUDE_PLUGIN_ROOT}/skills/feature-development/heads/lean.md` / **Stop**. Headless: print the diagnosis' path and stop. |

**Before gate B2's skill runs**, say in one line what its approval will authorise: the run's
checkpoint commits & pushes to `origin` (`tail.md` `## Checkpoint commits & pushes`), and a pull
request at the end — asked first in `plan-gate`, opened without asking in `yolo`. The approved
work list is the deviation anchor.

**When gate B2's skill returns `RAISE: <reason>; <reason>`** instead of a work list, that is a
raise too, from `bug`: do what the `DIAGNOSED_STRUCTURAL` row does, with the writer's reasons.
Do not act on its `Next:` line, and do not invoke the skill again.

**Resume.** `diagnosis.md` exists → gate B1 is done: read its status and take its row.
`tasklist.md` exists → gate B2 is done, and its approval was given before the skill wrote the
file: go to `SKILL.md` step 5, whose run-start entry records
`plan check: run by generate-tasklist`. One exception: a `tasklist.md` with no open box is a
finished work list, and it stops this head as it stops the lean one. There is nothing to
diagnose again and nothing to confirm: do not go on to `SKILL.md` step 5. Print

    This ticket's work list is complete: nothing was planned and the run is not armed. For follow-up work, add tasks to the work list (/artel:tasks add <TICKET_ID> "<title>" --iteration <N>, or by hand) and run this again, or open a new ticket.

and stop, in every mode.
