# Task grammar

How a tasklist states its work: one block per task, each with the fields the queue, the
orchestrator and the plan review read. Every writer of a tasklist (`task-planner`,
`tasklist-writer`, `/artel:tasks add`, the approval fold-back) writes this grammar, and every
reader cites this file rather than restating it. `scripts/tasklist_tasks.py` is the only
program that parses it (`scripts/task_grammar.py` holds the rules).

Written for sub-project 2a of the workflow redesign (0.23.0; design: the 2026-09-29
task-grammar spec). Before 0.23.0 a task was one checkbox; a tasklist without a task block
still is (§4).

## 1. The task block

A task is a `### Task <iteration>.<n>: <title>` heading inside an `## Iteration N:` section
(`## Phase N:` is the same heading), followed by its fields and then its steps:

```markdown
## Iteration 2: Wire the success dialog

**Goal:** the dialog shows after a purchase

### Task 2.3: Show the purchase success dialog [HITL: copy needs product sign-off]
- **Files:** `lib/ramps/success_dialog.dart` (new), `lib/ramps/ramps_bloc.dart`
- **Depends on:** 2.1, 2.2
- **Route:** full — money-movement path
- **Test:** `test/ramps/success_dialog_test.dart`
- **Produces:** `SuccessDialog.show(context, amount)` — static method
- **Implements:** R1, R3
- [ ] Add the dialog widget
- [ ] Show it on `PurchaseSucceeded`

**Test:** buy with the test card; the dialog shows the amount
```

- `<n>` starts at 1 and has no gaps inside the iteration, in document order.
- The optional `[HITL: <reason>]` tag sits on the heading and applies to the whole task
  (`docs/autonomous-run.md` §4). It is not part of the title.
- Fields come first, in any order, one bullet each; a value may wrap onto indented
  continuation lines. The steps follow: one or more `- [ ]` checkboxes. A task is **done**
  when every step is ticked.
- One task is one queue row and one implementer dispatch (`docs/task-queue.md`).

## 2. Field rules

The value after a dash (`—`, `–`, `--` or a spaced `-`) is a reason; the dash needs a space on
each side, so a hyphenated word is never split.

### 2.1 `Files:` (required)

The files the task changes, as backticked repo-relative paths, comma-separated. A file the
task creates carries ` (new)` after its path. The implementer lists in its report every file it
touched outside this list. `Files:` also drives the route floors (§7).

### 2.2 `Depends on:` (required)

`none`, or the numbers of the tasks this one waits for, comma-separated (`2.1, 2.2`; `Task 2.1`
reads the same). Only tasks of the same iteration: an earlier iteration is already done before
this one starts, and a later one can never be waited for. No task depends on itself, and the
dependencies have no cycle.

### 2.3 `Route:` (required)

`light`, or `full — <reason>` (`light` may carry a reason too). `light` is implement, the task
gate, done. `full` adds a task-mode review with one fix round. The planner declares it; the
floors of §7 can only raise it; the person can change it at the approval pause, and the change
is written back as `<light|full> — set at approval` (`docs/autonomous-run.md` §16).

### 2.4 `Test:` (required)

The test files the task must leave green, as backticked paths, or `none — <reason>` for work
with nothing to test (assets, configuration). A listed file exists already, or is `(new)` in
the `Files:` of a task of this or an earlier iteration. The task gate runs these files whether
or not the task touched them (`docs/gates.md` §1).

### 2.5 `Produces:` (optional)

What later tasks consume from this one: each symbol with its signature and kind. A dependent
task's brief carries the `Produces:` lines of the tasks it depends on.

### 2.6 `Implements:` (required when the PRD has requirements)

The requirement IDs the task delivers, comma-separated (`R1, R3`). Required on every task when
the PRD at the tasklist's phase-aware path has a `## Requirements` section; absent otherwise.
IDs belong to the PRD that defines them (ticket PRD or phase PRD), so `R2` of one phase never
means `R2` of another.

## 3. What stays and what goes

**Stays:** the document header (`docs/spec-storage.md` §3.2); the Progress Report table; each
iteration's `**Goal:**` and `**Test:**` lines; and the fix sections — `## Code Review Fixes`,
`## Runtime Fixes`, `## Verify Fixes` — with their `### <source>` headings, one checkbox per
row, exactly as `docs/task-queue.md` §6 describes. The fix sections are outside this grammar.

**Goes:** the `### \`path\`` file headings (their paths move into `Files:`), and the
`### After changes` checklist — codegen, localization and formatting are the implementer's
standing duties, and a box that runs a gate is a gate task, which no tasklist carries
(`docs/gates.md`).

## 4. Format detection and the old format

Per tasklist file: a `### Task N.M:` heading anywhere makes the file a task-format tasklist.
A file with none is the **old format** — one row per checkbox under `### \`path\`` sections,
promotion by iteration, `review.perTask` as before, no plan review — and keeps that behaviour
everywhere, so a ticket already in flight finishes the way it started. A phase file
(`phase-<N>/tasks.md`) is in the format of the tasklist it was extracted from.

The old format stays until sub-project 2b ships: `dev`'s mini-interview work list still writes
it.

## 5. IDs stay out of the product

Requirement IDs (`R3`) and task numbers (`2.3`) are planning coordinates. They never appear in
code, tests, identifiers, comments or commit subjects. The reviewer reports one as a Minor
finding.

## 6. Validation and the plan check

### 6.1 Grammar problems

Every mirror run validates the grammar and reports every problem at once, each with its line
in the whole document, as `tasklist_malformed` (exit 2, `error.data.problems`). Nothing is
mirrored until the file is clean.

| Rule | Meaning |
|---|---|
| `missing-field` | a required field (`Files`, `Depends on`, `Route`, `Test`) is absent |
| `empty-field` | a field has no value |
| `duplicate-field` | a field appears twice in one task |
| `bad-files` | `Files:` names no backticked path |
| `bad-dependency` | a `Depends on:` token is not a task number |
| `unknown-dependency` | it names a task the iteration does not have |
| `cross-iteration-dependency` | it names a task of another iteration |
| `self-dependency` | the task depends on itself |
| `cycle` | the iteration's dependencies form a cycle |
| `bad-route` | `Route:` is neither `light` nor `full` |
| `route-reason` | `Route: full` without a reason |
| `test-reason` | `Test: none` without a reason |
| `bad-test` | `Test:` names no backticked path and is not `none — <reason>` |
| `bad-implements` | an `Implements:` token is not a requirement ID like `R1` |
| `numbering` | the task number is not the next one of its iteration |
| `no-steps` | the task has no `- [ ]` step |
| `bare-checkbox` | a checkbox of a task-format iteration sits outside any task |
| `no-tasks` | an iteration has no task |

An unknown field, a field after the steps, or another `###` heading inside an iteration is a
warning (`data.warnings`), so the grammar can grow.

### 6.2 The plan check (`--check`)

The mechanical half of the plan review before the approval pause. It runs the grammar rules
above as Critical findings, plus the rules that need the repository and the PRD:

| Rule | Severity | Meaning |
|---|---|---|
| `uncovered-requirement` | Critical | an active PRD requirement has no task |
| `unknown-requirement` | Important | a task cites an ID the PRD does not define |
| `missing-implements` | Important | the PRD has requirements and some tasks cite them, but this one cites none |
| `missing-file` | Important | a `Files:` path does not exist and is not `(new)` |
| `missing-test` | Important | a `Test:` path does not exist and no task of this or an earlier iteration creates it |
| `placeholder` | Important | a title, field or step holds `TBD`, `TODO`, `???`, a `<…>` template slot, "same as Task", or "etc." (backticked code is not scanned) |

Exit 1 on any Critical or Important finding; Minor findings and a clean file exit 0.
Requirements marked `(withdrawn — <reason>)` or `(already met — <evidence>)` are not active and
need no task. The PRD's IDs arrive through `--requirements`, from the `requirements` read of
the PRD:

    python3 ${CLAUDE_PLUGIN_ROOT}/scripts/tasklist_tasks.py requirements --prd <prd-path>
    python3 ${CLAUDE_PLUGIN_ROOT}/scripts/tasklist_tasks.py --tasklist <tasklist-path> --ticket-key <TICKET_ID> --check --requirements <ids>

`<ids>` is `data.ids` joined by commas; `none` when `data.present` is true and `data.ids` is
empty; `absent` when there is no PRD or `data.present` is false. On the kartoteka path both
documents are piped from the store, never copied locally:

    set -o pipefail; python3 ${CLAUDE_PLUGIN_ROOT}/scripts/spec_store.py get <prd-path> | python3 ${CLAUDE_PLUGIN_ROOT}/scripts/tasklist_tasks.py requirements --prd -
    set -o pipefail; python3 ${CLAUDE_PLUGIN_ROOT}/scripts/spec_store.py get <tasklist-path> | python3 ${CLAUDE_PLUGIN_ROOT}/scripts/tasklist_tasks.py --tasklist - --ticket-key <TICKET_ID> --check --requirements <ids>

An old-format tasklist has nothing to check: `format: "legacy"`, no findings, exit 0.

## 7. Rows, waves, readiness and route floors

What a mirror run of a task-format tasklist emits beyond the old format's shape
(`data.format` is `"tasks"`):

- **Rows.** An iteration parent as before (`I<N>: name`). One child per task, titled
  `I<N> · <N.M> · <task title>` — the idempotency key, capped at 500 characters. Its
  description lists the fields as written, `HITL:`, then the steps without ticks (the mirror
  is create-only, so a tick there would go stale). Each child also carries `task`, `deps`,
  `files`, `route`, `route_reason`, `route_floor`, `route_reasons`, `route_effective`, `test`,
  `test_none_reason`, `produces`, `implements` and `steps`.
- **Status at creation.** `done` when every step is ticked; `ready` when the task is in
  `ready_now`; otherwise `backlog`. Parents stay `backlog`.
- **`data.waves`.** Per iteration, the tasks in dependency levels:
  `{"2": [["2.1", "2.2"], ["2.3"]]}`. Ties inside a level keep task-number order.
- **`data.ready_now`.** The tasks claimable now, as `{task, title}` pairs: in the first
  iteration that has a task not done, every task not done whose dependencies are all done.
  Computed from the checkboxes, so the tasklist stays the source of truth for promotion.
- **Route floors.** `route_floor` is `full` when any of these holds, with one `route_reasons`
  entry each:
  1. a `Files:` path matches a category of the sensitive-paths policy (a host
     `.artel/sensitive-paths.json` replaces the plugin's `hooks/sensitive-paths.json`
     wholesale) — `sensitive path (<category>): <path>`;
  2. the task carries a `[HITL: …]` tag — `HITL tag`;
  3. it lists more than `ROUTE_FULL_FILES = 5` files — `more than 5 files (<n>)`.

  `route_effective` is `full` when the declared route or the floor is — except that a route
  whose reason is `set at approval` is final over these three floors: the person chose it at
  the pause with the floors in front of them (`route_floor` and `route_reasons` still report
  the floor). The fourth floor — an earlier deviation in the run touched one of the task's
  files — is known only at runtime and is the orchestrator's (`docs/autonomous-run.md` §16);
  it applies to a route set at approval too.
