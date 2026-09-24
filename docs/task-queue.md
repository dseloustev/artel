# Task queue

How artel's execution agents take work from the host project's kartoteka task
queue, and what they do when it is not there.

Referenced by `agents/implementer.md`, `skills/implementer/SKILL.md`,
`skills/generate-tasklist/SKILL.md`, `skills/tasklist/SKILL.md`,
`skills/dev/SKILL.md`, `skills/feature-development/SKILL.md`,
`skills/run-reviewer/SKILL.md`, `skills/deep-review/SKILL.md` and
`skills/tasks/SKILL.md`. Spelled here
once because they need identical rules and two copies drifting apart hands the
same task to two agents.

**This file is the write direction toward kartoteka.** The read direction —
consulting prior tickets and decisions — is `docs/knowledge-consultation.md`,
and it stays read-only.

**`<project>` throughout is `knowledge.project` from `.artel/config.json`** — the
kartoteka project this repository belongs to (`docs/config.md`). Since kartoteka
0.31.0 one daemon may serve several projects out of one database and refuses a
write that does not name one, so `task_create` and `task_ready` carry it, and
`task_list` takes it as a scope. `task_update` needs none: a `task_id` is unique
across every project.

**The queue is authoritative for which iteration task to work next** — and only
for that. §6 lists the four sections it records but never offers, every one of
which is worked from the file on both paths. The tasklist in scope stays current as a rendered
view besides, because §4's fallback reads it and a fallback pointed at a file
claiming nothing is done would redo the whole ticket.

**"The file" and "the tasklist" in this contract mean the tasklist document** — a file on
the files path, a kartoteka artifact on the kartoteka path (`docs/spec-storage.md` §4.3).
Every scan, flip and append below applies to either through that contract's operation
mapping: a scan is `artifact_get` plus the same scan, a flip is one `artifact_patch`, and
§2 step 1's parser reads the stored document by pipe.

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
it — `feature-development`, `analysis`, `researcher`, `deep-review` — and on the
skills that write or keep rows on an orchestrator's behalf: `tasklist` and
`run-reviewer`, and `skills/implementer/SKILL.md`, which receives it from its caller
rather than from the user. `skills/dev/SKILL.md` deliberately has none, pinned by
`tests/test_knowledge_consultation_docs.py::test_dev_does_not_carry_the_flag`. An
orchestrator holding the flag passes it to each of those it invokes — to the
implementer skill on every dispatch, fix rounds included — and the implementer skill sets
the **Task queue:** field of the agent's dispatch to
`local-only (--local was passed)`; `agents/implementer.md` reads that field rather
than parsing a flag of its own. Where no orchestrator carries the flag — a `dev`
run — rows 2-4 alone decide.

**One precondition is resolved before the table, not in it.** With the adapter
`kartoteka` and `knowledge.project` empty or outside its grammar, the config is
in error under config.md's reading rule 3, exactly as in
`docs/knowledge-consultation.md` §1. Take the fallback path (§4) whatever the
tools say, and record:
`kartoteka is configured for this project but knowledge.project is not set`.

A fifth case the read path does not have: the adapter is on, the tools are
present, and a call fails at runtime because the daemon stopped mid-ticket. Fall
back for the remainder of the run and record `kartoteka became unreachable mid-run; continued from tasklist.md`
— distinctly, because it is the case that leaves §4's divergence behind.

And a sixth, which is a configuration error the config alone cannot show: the
daemon answers the first `task_create` or `task_ready` with a `Rejected:` line
naming `kartoteka project add <name>`. `<project>` is not registered in the
database that daemon serves — a typo of a registered name, or a project nobody
has registered yet — and the daemon deliberately cannot register one on demand.
Fall back for the remainder of the run and record
`kartoteka refused knowledge.project as unregistered; continued from tasklist.md`;
the fix is that command, run once on the machine serving the daemon.

`knowledge.adapter` is read from `.artel/config.json` per `docs/config.md`. The
tools are `task_create`, `task_update`, `task_list` and `task_ready`; they are
present when the host has wired the kartoteka MCP server into this session.

A daemon with `[auth]` on — kartoteka 0.32.0, every hosted one — that the session
registered without its bearer token is row 4 as well: Claude Code cannot connect,
so the tools are absent. The fix is host-side wiring, `--header` on the MCP
registration (`docs/config.md`, `knowledge.tokenEnv`), not anything this contract
reads. A token revoked mid-run answers every later call with an error: that is the
fifth case, fall back and record it as spelled there.

## 2. Mirroring the tasklist

Run by `generate-tasklist` and `tasklist` after `tasklist.md` is written, by
`dev` and `feature-development` alike on entry to implementation, and by every
writer of a fix section right after its append (§6).

1. `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/tasklist_tasks.py --tasklist <path> --ticket-key <TICKET_KEY>`
   (kartoteka path: `set -o pipefail; python3 ${CLAUDE_PLUGIN_ROOT}/scripts/spec_store.py get <path> | python3 ${CLAUDE_PLUGIN_ROOT}/scripts/tasklist_tasks.py --tasklist - --ticket-key <TICKET_KEY>`)
   Exit `0` → continue. Exit `2` → report `error.kind` and `error.message`, mirror
   nothing, continue the run on the fallback path.
2. For each entry of `data.iterations`, in order:
   `task_create(project=<project>, ticket_key=<TICKET_KEY>, title=…, description=…, status=…)`
   the iteration row, then `task_create` each of its `children` the same way,
   with `parent_id` set to the iteration row's `task_id`. Every call names the
   project; a `Rejected:` line naming `kartoteka project add` on the first one is
   §1's sixth case — mirror nothing more and continue on the fallback path.
3. Then each entry of `data.sections`, in order — present only when the file has
   a fix section with a task in it — exactly as in step 2: the section row
   (`CRF: Code Review Fixes`, …), then its `children` with `parent_id` set to
   that row's `task_id`. A child the script emitted `backlog` that comes back
   `done` is an earlier round's row under the same title (§6, "Titles are
   identity"): the open checkbox has no row of its own. Say so loudly —
   `fix task #<id> is done in the queue but open in the file: <title>` — and
   carry on.
4. Surface every `data.warnings` line to the user; none of them stop the mirror.

**A fix-section writer creates `data.sections` only.** It runs step 1 on the file
it wrote — `tasklist.md`, or `phase-<N>/tasks.md` on a phase-scoped run — and
skips step 2: a fix row's parent is its section row, never an iteration, and
re-creating every iteration row to record three fixes buys nothing. It resolves
§1 like every other caller; on the fallback path it runs nothing, and the file
alone carries the tasks, exactly as before fix rows existed.

**Order matters.** `task_ready` claims `ORDER BY task_id LIMIT 1` and the table
has no priority column, so insertion order is queue order. Mirror in the order
the script emits. Fix-section rows are never `ready`, so where they land in that
order changes nothing.

**The step is create-only and safe to re-run.** `task_create` is idempotent on
`(ticket_key, title)` *and discards* a changed status or description, returning
the stored row. A re-mirror never resets a `done` row and never undoes a
promotion, which is what makes the orchestrators' re-mirror a repair rather than
a hazard.

**Use the canonical ticket key, never the phase suffix** — a run of `AW-1234-2`
mirrors under `AW-1234`, the same rule `docs/knowledge-consultation.md` §2 states
for `related()`.

**`phase-<N>/tasks.md`'s iteration tasks are never mirrored.** It is an extract
of one iteration that `sync-phases` syncs back to the ticket-wide tasklist;
mirroring both would create two rows per checkbox. On phase-scoped runs
`sync-phases` therefore runs before this step, which both orchestrators'
existing step order already does. Its fix sections are the exception, because
they live nowhere else: on a phase-scoped run the review, runtime and checkpoint
gates append to the phase file, and `sync-phases` never copies a fix section
back. Their writer mirrors the phase file's `data.sections` (the extract has no
`## Iteration N:` heading, so its `data.iterations` is empty anyway), and the
source heading names the phase (§6), so two phases' fixes never share a title.

## 3. Claiming, reporting and promoting

    claim     task_ready(project=<project>, actor="artel@<hostname>", ticket_key=<TICKET_KEY>)
                → nothing returned: no ready work; fall through to §5
                → the row is now in_progress and held by this actor
                → first child of an iteration: task_update(parent, in_progress)
    guard     title contains "[HITL:" → task_update(task_id, blocked),
                return `HITL: <reason>`, do not implement
    phase     claimed row belongs to another phase -> task_update(task_id, ready)
                the one release that is not blocked; nothing was worked
    work      implement; flip the checkbox in the tasklist in scope
              (tasklist.md, or phase-<N>/tasks.md on a phase-scoped run)
              and update the Progress Report table, exactly as before
    report    task_update(task_id, status="done")
    promote   task_list(project=<project>, ticket_key=<TICKET_KEY>)
                → any "I<N> · " sibling not done?
                yes → stop here
                no  → task_update(parent "I<N>: …", done)
                      every "I<N+1> · " child: backlog → ready
                no I<N+1> exists → checkbox work is complete; Final Verification
    abort     red gate, any DEVIATION halt, or Abort task ->
                task_update(task_id, blocked)
                never left in_progress: task_ready offers `ready` rows only,
                so a held row wedges the iteration permanently

`actor` is `artel@<hostname>`, where `<hostname>` is the output of `hostname -s`,
run in the dispatch rather than recalled. Handed the placeholder alone, an agent
invents the name — one ticket's queue carried three fictitious hosts on
2026-09-02 — and §5's held-row diagnosis then names a machine that does not
exist. Two agents on one host are indistinguishable in this field; kartoteka
renders it as "self-reported, unverified" and nothing depends on it beyond the
record.

Siblings are found by the `I<N> · ` title prefix rather than by `parent_id`.
Since kartoteka 0.28.0 `task_list` *does* render the parent, as `· parent: #N`
(and `task_ready` and `task_update` return it), so the title prefix is now a
convention artel keeps rather than a limitation it works around. Moving the
sibling scan — and the claim itself, via `task_ready`'s `parent_id` narrowing —
onto the parent is an open follow-up ([design.md](design.md#open-follow-ups)).

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

**Fix-section rows are recorded, not claimed.** The rows §6 describes move by a
protocol of their own. The implementer still takes its task by file scan — the
first `- [ ]` under the section its dispatch names — and never calls
`task_ready` for it; the row is a record of that work, not the source of it:

    find      task_list(project=<project>, ticket_key=<TICKET_KEY>)
                → the row titled "<CODE> · <source> · <checkbox text>" (§6), as
                  the script builds it: cut to its first 500 characters, and
                  compared with whitespace runs collapsed to one space
                → none (an older ticket, a mirror that failed): not an error;
                  work from the file and report `row not found; file only`
    guard     title contains "[HITL:" → task_update(task_id, status="blocked"),
                return `HITL: <reason>`, do not implement
                resumed with the answer → task_update(task_id, status="in_progress")
    start     task_update(task_id, status="in_progress")
    work      implement; flip the checkbox in the tasklist in scope, exactly as before
    report    task_update(task_id, status="done") — no promotion: nothing waits on a fix row
    close     task_list(project=<project>, ticket_key=<TICKET_KEY>)
                → no other child of the same parent left backlog / ready / in_progress / blocked?
                  yes → task_update(parent_id, status="done") — a section closes with its last child
    abort     red gate, any DEVIATION halt, or Abort task ->
                task_update(task_id, status="blocked")

**Never `ready`.** No step above moves a fix row to `ready`, and the mirror never
creates one that way; §6, "Why never offer them", says why.

**No holder.** Only a `task_ready` claim sets a row's holder, so an
`in_progress` fix row names none: `actor` stays empty and `updated_at` says when
the work started. `/artel:tasks list` shows it that way rather than inventing one.

**A HITL fix task is never claimable,** so the reason above for keeping a HITL
iteration task claimable — a child never completed deadlocks promotion — does not
reach it: nothing is promoted on a fix row. It goes `blocked` when the
implementer returns `HITL:` and back to `in_progress` when the orchestrator
resumes that implementer with the user's answer. The implementer makes both
updates because it holds the row id; its `HITL:` line carries none for the
orchestrator to use.

## 4. The fallback path

Scan the tasklist in scope (`tasklist.md`, or `phase-<N>/tasks.md` on a
phase-scoped run) for the first incomplete `- [ ]` and proceed exactly as artel
did before the queue existed, flipping the checkbox on completion.

**Record which path the run took**, in the ticket's `implementation-notes.md`
alongside the deviation record. A run that switches paths mid-ticket leaves the
checkbox marks ahead of the queue statuses and nothing reconciles them. The
record is the only trail that divergence leaves.

## 5. When the queue is empty

`task_ready` returning nothing on the queue path means no work is `ready`. That is
not by itself a completion and not by itself a stall — check
`task_list(project=<project>, ticket_key=<TICKET_KEY>)` and report which of these
it is. The first four read **iteration children only** — rows titled `I<N> · …`.
Fix-section rows (§6) are never `ready`, so they can neither stall the queue nor
keep it from draining; they are the fifth line, not a variant of the first four:

- every **iteration child** row `done` — the iteration work is complete. An `I<N>: …` parent
  still `backlog` because its iteration was already complete when it was mirrored
  is not a stall: mark it `done` and treat the queue as drained. Report
  `queue drained: iteration work complete`, then
  continue from the file — the first incomplete `- [ ]` in scope (§6).
  This is the normal end of a successful ticket, not a stall.
  A ticket with no iteration rows at all — a tasklist holding only fixes — is
  drained by this line too: it has no iteration child left undone.
- iteration children in `backlog` with none `ready` — a promotion did not happen, or an
  iteration was already complete when it was promoted into. Repair it rather than
  reporting a stall: promote every `I<N> · ` child of the lowest-numbered
  iteration that still has an unfinished child, then claim again. If every child
  of that iteration is already `done`, promote the next one and repeat. If no
  iteration has an unfinished child, there is nothing left to promote — take the
  first bullet.
- iteration children in `blocked` — a HITL task or an aborted task is waiting on the user.
- iteration children in `in_progress` — a holder is still working, or stalled and left the row
  held. `actor` names the holder and `updated_at` says how long ago. Report it;
  do not clear another agent's claim on your own judgement. Nothing available
  here separates a slow verify loop from a dead holder, and clearing a live
  claim puts two agents on one task — the outcome the store's atomic claim
  exists to prevent. Releasing it is the user's call.
- fix-section rows open, `in_progress` or `blocked` — gate work the file still
  holds (§6), not a stall. Report them beside whichever line above applies —
  `fix rows: <n> open, <m> in progress, <k> blocked` — and
  never repair, promote or release one: the implementer that works them keeps them current.

## 6. What the queue records but never offers

Only iteration work is **offered** — the `## Iteration N:` sections, or `## Phase N:`,
which the parser accepts as the same heading. Four more sections are **recorded**:
the parser emits them in `data.sections`, one parent row per section and a child
per checkbox, and `task_ready` never hands one out.

| Section | Parent row | Child title | Worked by |
|---|---|---|---|
| `## Code Review Fixes` | `CRF: Code Review Fixes` | `CRF · <source> · <checkbox text>` | file scan, on either path |
| `## Runtime Fixes` | `RTF: Runtime Fixes` | `RTF · <source> · <checkbox text>` | file scan, on either path |
| `## Verify Fixes` | `VF: Verify Fixes` | `VF · <source> · <checkbox text>` | file scan, on either path |
| `## Final Verification` | `FV: Final Verification` | `FV · <source> · <checkbox text>` | file scan, on either path — a section only tasklists written before 0.18.0 carry; no writer emits it now (`docs/gates.md` §1) |

**Why record them.** They are where the longest-running part of a review cycle
happens. On a host run on 2026-09-18 a deep review appended 21 tasks under
`## Code Review Fixes`, and the implementer completed nine of them over several
hours while `/artel:tasks list` reported the queue drained. The queue is where
people look to see what an agent is doing, and that work never became searchable
history either.

**Why never offer them.** They are gate remediation and the end-of-feature gate,
not planned iteration work, and they are appended after the iterations were
mirrored. `task_ready` claims the oldest `ready` row for the ticket with no notion
of section (§2, "Order matters"), so a `ready` fix row would go to whichever
queue-path implementer asked next — a phase-scoped run included, across the
boundary §3's wrong-phase check exists to hold. So the parser emits them
`backlog`, or `done` for a checked box, and never `ready` — even in a file with no
iterations — and §3's fix-section protocol moves them by `task_update` alone. The
file stays their source of truth on every path.

**Section rows close with their last child.** A fix-section parent (`CRF:`, `RTF:`,
`VF:`, `FV:`) is `backlog` while any child is open, `done` when its last child is
`done`, and `backlog` again the moment a writer appends to its section. The
implementer that marks the last child `done` marks the parent `done` in the same
exit (§3, `close`); `/artel:tasks done` does the same; every writer in the table
below (`run-reviewer`, `deep-review`, the runtime gate, the checkpoint,
`/artel:tasks add --fix`) sets an existing parent back to `backlog` when its
re-mirror creates a new child under it — `task_create` is idempotent and returns
the stored parent row with its status, so `task_update(parent, status="backlog")`
follows whenever that status is `done`. Before 0.18.0 parents were permanent
labels, which is why a finished ticket never read fully done in kartoteka's rollup.

**The source heading.** Each writer opens its batch with a `### <source>` heading
inside the section and puts its checkboxes under it. That heading is the title's
middle segment, the way an iteration's `### ` section is:

    ## Code Review Fixes

    ### review-r2
    - [ ] **Task 3: Guard the null wallet**
      - Acceptance criteria:
        - A null wallet renders the empty state.

| Writer | Source heading |
|---|---|
| `reviewer` ticket mode, via `run-reviewer` | `### review-r<R>`, R the `**Review round:**` it writes — `### review-p<PHASE_NUM>-r<R>` on a phase-scoped run |
| `reviewer` task mode, via `run-reviewer` (`autonomous-run.md` §16) | `### task-gate-<NNN>`, the `NNN` of the report it answers |
| `deep-review` Step 6 | `### deep-review-<YYYY-MM-DD>` |
| runtime gate — `dev` step 7, `feature-development` gate 8 | `### runtime-r<n>`, n the retry this round is — `### runtime-p<N>-r<n>` on a phase-scoped run |
| phase checkpoint — `feature-development` `## Checkpoint commits & pushes`, shared by `dev` | `### checkpoint-r<k>`, k the verify round — `### checkpoint-p<N>-r<k>` on a phase-scoped run |
| `/artel:tasks add --fix` | `### manual-<YYYY-MM-DD>` — `### manual-p<N>-<YYYY-MM-DD>` in a phase file |

Dates come from `date +%F`, never from memory. A writer starting a batch never
reuses a heading already in the section: it appends `-2`, then `-3`, …
(`review-r1-2`). The one exception is `/artel:tasks add --fix`, whose additions
on one day are one batch. A box's source is the nearest `###` heading above it
in its section, whatever that heading says — a fix task written before 0.15.0
under a `### Blocking` or `### Tasks` takes that heading as its source. A box with
no `###` above it in its section — Final Verification as `tasklist-writer` writes
it, and a pre-0.15.0 fix task written straight under the section heading — has no
heading, so the source is `tasklist`.

No other `###` heading goes inside a batch. The nearest `###` above a box is its
source, so a writer's own grouping — a reviewer's `### Blocking` or `### Important`
— would stand in for the round and give every round's Blocking tasks one title
prefix again. Priority goes in the task text (`**Task N (Blocking): …**`) or under a
`####` heading, which the parser ignores as a source.

The checkbox text is the title's third segment **verbatim**, bold markers
included, because `/artel:tasks done` flips the box whose text is everything after
the title's second ` · `. Lines nested under a checkbox — its body, its
acceptance criteria, an indented sub-step — go to the row's description, never
its title. A title runs to 500 characters at most: the script cuts a longer one
there (kartoteka's cap), so its third segment is only the start of the box and
`done` matches on that start. A writer quoting something long — the runtime gate
quoting an error — therefore puts a one-line summary on the checkbox and nests the
quote under it.

**Titles are identity, and fix sections repeat.** `task_create` is idempotent on
the title and ignores a status passed for one that exists, so a re-appended task
whose title matches an old `done` row would come back `done` — new work,
invisible again. The source heading keeps rounds apart, and two warnings catch a
writer that repeats one anyway: the script warns when a fix title repeats inside
the file (the later box gets no row of its own), and §2 step 3 warns when an open
box resolves to a `done` row.

**Recognise such a dispatch by the section the orchestrator names**: the review
gate's fixes, the runtime gate's fixes, the checkpoint's verify fixes, or the
Final Verification gate. On either path the implementer scans the tasklist in
scope for the first incomplete `- [ ]` under that heading; `task_ready` is not
called at all, because it never offers these rows. On the queue path the
implementer keeps the row current per §3's fix-section protocol.

**So `task_ready` returning nothing does not mean there is nothing to do.** It
means no *iteration* work is ready. When every iteration child row is `done`, the
iteration work is finished and the run
continues from the file — the first incomplete `- [ ]` in scope —
report `queue drained: iteration work complete` so the orchestrator can tell that
from a stall. §5 covers the cases where rows remain, including a parent left
`backlog` by an iteration that was already complete when it was mirrored.

**And when the file has no incomplete `- [ ]` in scope either** — every section
complete, or the tasklist carrying none — there is nothing left to work at all.
Report the ticket complete and return; do not loop and
do not re-claim. This is the only state in which reporting completion is right,
and it is a state read off the file, never inferred from an empty queue.

**Claiming them is a follow-up, and the rows are shaped for it.** kartoteka
0.28.0's `task_ready` narrows by `parent_id`, and one parent per section is what
that needs: a fix dispatch could claim from its own section and gain the holder
these rows lack today. It changes the claim protocol, so it is the open follow-up
in [design.md](design.md#open-follow-ups), not this section.
