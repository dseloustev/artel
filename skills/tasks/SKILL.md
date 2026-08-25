---
name: tasks
description: "Operate the ticket's kartoteka task queue from the conversation: list the queue and diagnose it (drained, promotion pending, blocked, held), add a task through tasklist.md so file and queue stay in step, mark a task done or blocked, or release a task a dead agent left in_progress. Use when the user asks what is in the queue, who holds a task, wants a task added to a ticket, or wants a stuck task released."
argument-hint: 'list|add|done|block|release [ticket-id] [<task-id> | "<title>" --iteration N [--section <name>] [--hitl <reason>] [--raw]] [--status <status>] [--note <text>]'
model: sonnet
---

Worker, not an orchestrator — no agent matches this job; it runs inline (like `sync-phases` and
`setup`). It is the conversational front door to the write side of
`${CLAUDE_PLUGIN_ROOT}/docs/task-queue.md`; read that contract first. Two things it never does:
**it never calls `task_ready`** — claiming is `implementer`'s job, and a claim made here would
hold a row no loop is going to work — and it never promotes an iteration, which is the
implementer's repair (`docs/task-queue.md` §3, §5).

## 1. Resolve

- **Verb**: the first token — `list`, `add`, `done`, `block`, `release`. Missing or unknown →
  print the argument hint and stop.
- **Ticket**: the next token when it matches `ticket.pattern`
  (`${CLAUDE_PLUGIN_ROOT}/docs/ticket-parsing.md` §§1–2), else `<specs.dir>/.active_ticket`,
  else — for `list` only — no ticket (all tickets). `add` with no ticket stops with "Error: No
  ticket specified. Provide a ticket ID as a parameter or set it in <specs.dir>/.active_ticket".
  `done`, `block` and `release` take a task id, and every kartoteka row carries its
  `ticket_key` (`#<id> · <KEY> · <status>`): when no ticket resolved, `release` finds the row
  with an unfiltered `task_list()`, and `done` / `block` read `ticket_key` off the
  `task_update` result — that key names the tasklist `done` edits.
  The **queue key is always the canonical `TICKET_ID`**, phase suffix stripped
  (`docs/task-queue.md` §2); `PHASE_NUM`, when given, only says which phase file is also edited.
- **Gate**: read `knowledge.adapter` from `.artel/config.json`
  (`${CLAUDE_PLUGIN_ROOT}/docs/config.md`).

| `knowledge.adapter` | kartoteka MCP tools in session | Do |
|---|---|---|
| `none` / absent | either | Stop: "`knowledge.adapter` is not `kartoteka` for this project — declare it with `/artel:setup`." |
| `kartoteka` | absent | Stop: "kartoteka is configured for this project but its MCP tools are not available in this session" |
| `kartoteka` | present | Continue |
| anything else | either | Stop: configuration error (config.md reading rule 3); name the value. |

The tools are `task_create`, `task_update`, `task_list`. There is no override flag: kartoteka is
single-project, and a queue wired up for another checkout must not be written to from this one.

## 2. Verbs

### `list [ticket] [--status <status>]`

1. `task_list(ticket_key=<TICKET_ID> or omitted, status=<status> or omitted)`.
2. Render a table — `task_id`, title, status, actor, last activity — with iteration parents
   (titles `I<N>: …`) first, each followed by its children (`I<N> · …`). Flag `[HITL:` titles.
   A parent `in_progress` means only that its iteration is active (`docs/task-queue.md` §3 sets
   it on the first child's claim and leaves it there); it is not a held claim.
3. Diagnose per `docs/task-queue.md` §5 over the **child rows only** (`I<N> · …`; `--raw` rows
   count as children) — but where §5 answers for one empty claim, a listing can be in several
   of its states at once, so report **every** line that applies, in this order, and say
   "nothing to report" only when none does:
   - any child `blocked` → **blocked** — list them; a HITL title is waiting on the user;
   - any child `in_progress` → **held** — actor and how long since `updated_at`, per row;
   - child rows in `backlog`, none `ready`, and some iteration still has an unfinished child →
     **promotion pending** — name the lowest-numbered such iteration; the implementer's next
     claim repairs it;
   - every child `done` → **drained** — iteration work complete (a parent still `backlog` is
     §5's known leftover, not a stall).
4. **Report only.** `list` never promotes, never releases, never edits a file. If the user wants
   a held row cleared, that is `release`.

### `add <ticket> "<title>" --iteration N [--section <name>] [--hitl <reason>] [--raw]`

`--raw` → skip to **Raw** below.

1. **Tasklist in scope**: `<specs.dir>/<TICKET_ID>/tasklist.md`. Missing → stop: "no tasklist
   for <TICKET_ID>; create one with `/artel:tasklist` or `/artel:generate-tasklist`, or pass
   `--raw` for a bare backlog row". `--iteration` missing, or iteration `N` absent → stop and
   list the `## Iteration N:` / `## Phase N:` headings the file has.
2. **Append the checkbox** — the line `- [ ] <title>`, with ` [HITL: <reason>]` appended when
   `--hitl` was given — under iteration `N`:
   - under `### <section>` when `--section` names an existing section of that iteration;
   - else under the iteration's **last** `### ` section;
   - else create `### Follow-ups` at the end of the iteration and put it there.
   A checkbox outside a `### ` section never becomes a row — `scripts/tasklist_tasks.py`
   collects only sectioned checkboxes — so never append one bare.
   If `<specs.dir>/<TICKET_ID>/phase-<N>/tasks.md` exists, append the identical line under the
   same section there, so `sync-phases` matches the same text on both sides.
   If the tasklist has a Progress Report table with a row for iteration `N`, bump that row's
   total (`X/Y` → `X/Y+1`).
3. **Mirror** — exactly `docs/task-queue.md` §2:

       python3 ${CLAUDE_PLUGIN_ROOT}/scripts/tasklist_tasks.py --tasklist <specs.dir>/<TICKET_ID>/tasklist.md --ticket-key <TICKET_ID>

   Exit `0` → for each entry of `data.iterations` in order: `task_create` the iteration row,
   then each of its `children` with `parent_id` set to the iteration row's `task_id`.
   Create-only and idempotent on `(ticket_key, title)`: every pre-existing row comes back
   unchanged, and the new checkbox comes back as a new row. Surface every `data.warnings` line.
   Yes, that is one `task_create` per row of the whole tasklist to add one task — do not skip
   the parent or the siblings to save calls: the new child's `parent_id` comes from the
   parent's returned `task_id`, and a partial mirror is how two rows end up in two orders.
   Exit `2` → report `error.kind` and `error.message`. The checkbox is written; the row is not;
   the next orchestrator re-mirror picks it up. Stop.
4. **Status**: the parser emits `ready` for a child of the first iteration and `backlog`
   otherwise. If the new row came back `backlog` **and** its parent `I<N>: …` row is
   `in_progress` (the implementer is working that iteration now), promote it:
   `task_update(<task_id>, status="ready")`. Otherwise leave it — normal promotion reaches it.
5. **Report**: `task_id`, title (the composed `I<N> · <section> · <title>`), status — or
   "already mirrored as #<id>" when that title existed — plus the parser warnings.

**Raw** (`--raw`): `task_create(ticket_key=<TICKET_ID>, title=<title>, status="backlog")`.
Report the row and this line verbatim: "backlog only; not part of any iteration; artel's
implementer will not claim it". No file is edited.

### `done <task-id>`

1. `task_update(<task-id>, status="done")`.
2. Take the row's title from the `task_update` result — the `## ` header up to ` (#`, since
   kartoteka renders `## <title> (#<id> · <KEY> · <status>)` and ` · ` is also its field
   separator. The checkbox text is everything after the title's second ` · ` (the
   `I<N> · <section> · ` prefix); a `--raw` title has no prefix and no checkbox. Flip the
   matching `- [ ]` to `- [x]` in `<specs.dir>/<TICKET_ID>/tasklist.md` (and in
   `phase-<N>/tasks.md` when it exists) — the file is the fallback the implementer reads when
   the daemon is gone, so it must not fall behind the queue. Not found → warn: "queue updated;
   no matching checkbox in tasklist.md — the file is now behind the queue".
3. Do **not** promote the iteration; report whether its siblings are all done and leave the
   promotion to the implementer's loop.

### `block <task-id> [--note <text>]`

`task_update(<task-id>, status="blocked"[, description=<text>])`. Report the row.

### `release <task-id>`

1. `task_list(ticket_key=<TICKET_ID>)` (unfiltered when no ticket resolved) and find the row.
   A title `I<N>: …` is an iteration parent, not a claim — stop: "task #<id> is the iteration
   parent; parents are never released". Parents are never `ready` by contract, and the
   implementer's `task_ready` (never called here) claims the oldest `ready` row with no
   parent/child distinction, so releasing one hands it a row with no checkbox. Not
   `in_progress` → stop: "task #<id> is <status>, not held; nothing to release".
2. `AskUserQuestion`: "Clear <actor>'s claim on #<id> (held since <updated_at>, <age>)? If that
   agent is still alive, two agents will be on one task." Options: **Release** / **Keep**.
3. On Release: `task_update(<task-id>, status="ready")` — this clears the holder and appends the
   reset to the task's history. On Keep: stop, nothing changed.

Never automatic: kartoteka has no claim leases, nothing distinguishes a slow verify loop from a
dead holder, and clearing a live claim is the outcome the atomic claim exists to prevent.

## 3. Failure

A tool call that errors mid-verb: report the error text and stop. There is no fallback path
here — the user asked for the queue, not for work. For `add`, the invariant is that the file is
written **before** any `task_create`, so a failed mirror leaves the queue behind the file, never
ahead of it; say which rows were created before the failure.
