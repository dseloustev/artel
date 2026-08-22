# Task queue

How artel's execution agents take work from the host project's kartoteka task
queue, and what they do when it is not there.

Referenced by `agents/implementer.md`, `skills/implementer/SKILL.md`,
`skills/generate-tasklist/SKILL.md`, `skills/tasklist/SKILL.md` and
`skills/dev/SKILL.md`. Spelled here once because they need identical rules and
two copies drifting apart hands the same task to two agents.

**This file is the write direction toward kartoteka.** The read direction —
consulting prior tickets and decisions — is `docs/knowledge-consultation.md`,
and it stays read-only.

**The queue is authoritative for what to work on next.** `tasklist.md` remains
current as a rendered view, because §4's fallback reads it and a fallback
pointed at a file claiming nothing is done would redo the whole ticket.

## 1. Whether to use the queue at all

Three inputs, resolved in this order. `--local` short-circuits before capability
is considered, exactly as in `docs/knowledge-consultation.md` §1.

| `--local` | `knowledge.adapter` | kartoteka MCP tools | Behavior |
|---|---|---|---|
| **yes** | either | either | Fallback path (§4). Record: `local-only run requested` |
| no | `none` / absent | either | Fallback path (§4). Record nothing |
| no | `kartoteka` | present | **Queue path** (§2, §3) |
| no | `kartoteka` | **absent** | Fallback path (§4). Record: `kartoteka is configured for this project but its MCP tools are not available in this session` |

**Where `--local` is available.** The flag exists on the orchestrators that carry
it — `feature-development`, `analysis`, `researcher` — and on
`skills/implementer/SKILL.md`, which receives it from its caller rather than from
the user. `skills/dev/SKILL.md` deliberately has none, pinned by
`tests/test_knowledge_consultation_docs.py::test_dev_does_not_carry_the_flag`. An
orchestrator holding the flag passes it down to the implementer skill, which sets
the **Task queue:** field of the agent's dispatch to
`local-only (--local was passed)`; `agents/implementer.md` reads that field rather
than parsing a flag of its own. Where no orchestrator carries the flag — a `dev`
run — rows 2-4 alone decide.

A fifth case the read path does not have: the adapter is on, the tools are
present, and a call fails at runtime because the daemon stopped mid-ticket. Fall
back for the remainder of the run and record `kartoteka became unreachable mid-run; continued from tasklist.md`
— distinctly, because it is the case that leaves §4's divergence behind.

`knowledge.adapter` is read from `.artel/config.json` per `docs/config.md`. The
tools are `task_create`, `task_update`, `task_list` and `task_ready`; they are
present when the host has wired the kartoteka MCP server into this session.

## 2. Mirroring the tasklist

Run by `generate-tasklist` and `tasklist` after `tasklist.md` is written, and by
`dev` and `feature-development` alike on entry to implementation.

1. `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/tasklist_tasks.py --tasklist <path> --ticket-key <TICKET_KEY>`
   Exit `0` → continue. Exit `2` → report `error.kind` and `error.message`, mirror
   nothing, continue the run on the fallback path.
2. For each entry of `data.iterations`, in order: `task_create` the iteration row,
   then `task_create` each of its `children` with `parent_id` set to the
   iteration row's `task_id`.
3. Surface every `data.warnings` line to the user; none of them stop the mirror.

**Order matters.** `task_ready` claims `ORDER BY task_id LIMIT 1` and the table
has no priority column, so insertion order is queue order. Mirror in the order
the script emits.

**The step is create-only and safe to re-run.** `task_create` is idempotent on
`(ticket_key, title)` *and discards* a changed status or description, returning
the stored row. A re-mirror never resets a `done` row and never undoes a
promotion, which is what makes the orchestrators' re-mirror a repair rather than
a hazard.

**Use the canonical ticket key, never the phase suffix** — a run of `AW-1234-2`
mirrors under `AW-1234`, the same rule `docs/knowledge-consultation.md` §2 states
for `related()`.

**`phase-<N>/tasks.md` is never mirrored.** It is an extract of one iteration
that `sync-phases` syncs back to the ticket-wide tasklist; mirroring both would
create two rows per checkbox. On phase-scoped runs `sync-phases` therefore runs
before this step, which both orchestrators' existing step order already does.

## 3. Claiming, reporting and promoting

    claim     task_ready(actor="artel@<hostname>", ticket_key=<TICKET_KEY>)
                → nothing returned: no ready work; fall through to §5
                → the row is now in_progress and held by this actor
                → first child of an iteration: task_update(parent, in_progress)
    guard     title contains "[HITL:" → task_update(task_id, blocked),
                return `HITL: <reason>`, do not implement
    work      implement; flip the checkbox in tasklist.md and update the
              Progress Report table, exactly as before
    report    task_update(task_id, status="done")
    promote   task_list(ticket_key) → any "I<N> · " sibling not done?
                yes → stop here
                no  → task_update(parent "I<N>: …", done)
                      every "I<N+1> · " child: backlog → ready
                no I<N+1> exists → checkbox work is complete; Final Verification
    abort     red gate, any DEVIATION halt, or Abort task ->
                task_update(task_id, blocked)
                never left in_progress: task_ready offers `ready` rows only,
                so a held row wedges the iteration permanently

`actor` is `artel@<hostname>`. Two agents on one host are indistinguishable in
this field; kartoteka renders it as "self-reported, unverified" and nothing
depends on it beyond the record.

Siblings are found by the `I<N> · ` title prefix rather than by `parent_id`,
because `task_list` does not render the parent. `task_ready` and `task_update`
do return it.

**Iteration N is phase N.** `generate-tasklist` writes the tasklist ticket-wide
and calls its iterations phases; `sync-phases` maps `phase-<N>/tasks.md` onto
iteration N. So a phase-scoped run claims only `I<N> · ` tasks for its own phase.
If `task_ready` hands it one from another phase, return that task with
`task_update(task_id, status="ready")` — which clears the holder — and report
that the queue offered work from another phase, most often an earlier one this
run is not authorised to finish, rather than crossing the boundary
`agents/implementer.md`'s `## Phase support` rule forbids.

**A wrong-phase claim is the one exception to releasing a held task `blocked`.**
Every other exit that is not a completion goes to `blocked`, because the task
needs a human before anyone works it again. This one does not: you never touched
it, nothing about it is wrong, and the run that owns its phase has to be able to
claim it. `blocked` would strand that phase until someone cleared it by hand.

**A HITL task is claimable on purpose.** Mirroring it `blocked` would deadlock
promotion: a child that is never completed means the iteration never finishes
and nothing is ever promoted. Claiming one is what triggers the pause. The
orchestrator asks the user; clearing it sets the task back to `ready`, which
also clears the holder.

## 4. The fallback path

Scan `tasklist.md` for the first incomplete `- [ ]` within scope and proceed
exactly as artel did before the queue existed, flipping the checkbox on
completion.

**Record which path the run took**, in the ticket's `implementation-notes.md`
alongside the deviation record. A run that switches paths mid-ticket leaves the
checkbox marks ahead of the queue statuses and nothing reconciles them. The
record is the only trail that divergence leaves.

## 5. When the queue is empty

`task_ready` returning nothing on the queue path means no work is `ready`. That is
not by itself a completion and not by itself a stall — check `task_list(ticket_key)`
and report which of these four it is:

- every row `done` — the iteration work is complete. Report
  `queue drained: iteration work complete` and continue to `## Final Verification`
  from the file (§6). This is the normal end of a successful ticket, not a stall.
- rows in `backlog` with none `ready` — a promotion did not happen, or an
  iteration was already complete when it was promoted into. Repair it rather than
  reporting a stall: promote every `I<N> · ` child of the lowest-numbered
  iteration that still has an unfinished child, then claim again. If every child
  of that iteration is already `done`, promote the next one and repeat.
- rows in `blocked` — a HITL task or an aborted task is waiting on the user.
- rows in `in_progress` — a holder is still working, or stalled and left the row
  held. `actor` names the holder and `updated_at` says how long ago. Report it;
  do not clear another agent's claim on your own judgement. Nothing available
  here separates a slow verify loop from a dead holder, and clearing a live
  claim puts two agents on one task — the outcome the store's atomic claim
  exists to prevent. Releasing it is the user's call.

## 6. What the queue does not hold

Only iteration work is mirrored — the `## Iteration N:` sections, or `## Phase N:`,
which the parser accepts as the same heading. It closes the current iteration at
any other `##` heading, so four sections of a tasklist never become rows and never
will:

| Section | Worked by |
|---|---|
| `## Code Review Fixes` | file scan, on either path |
| `## Runtime Fixes` | file scan, on either path |
| `## Verify Fixes` | file scan, on either path |
| `## Final Verification` | file scan, on either path |

This is deliberate: they are gate remediation and the end-of-feature gate, not
planned iteration work, and they are appended after the mirror has run.

**Recognise such a dispatch by the section the orchestrator names**: the review
gate's fixes, the runtime gate's fixes, the checkpoint's verify fixes, or the
Final Verification gate. On either path the implementer scans the tasklist in
scope for the first incomplete `- [ ]` under that heading; `task_ready` is not
called at all, because it can only ever answer for iteration work.

**So `task_ready` returning nothing does not mean there is nothing to do.** It
means no *iteration* work is ready. When every row is `done`, the iteration work
is finished and the run continues to `## Final Verification` from the file —
report `queue drained: iteration work complete` so the orchestrator can tell that
from a stall. §5 covers the cases where rows remain.
