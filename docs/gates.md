# Gates

Which quality check runs when, who runs it, and what its result means. Every skill and
agent that runs a check cites this file and never restates a command list; the commands
themselves come from `.artel/config.json` (`docs/config.md`, `verify.*`).

Written for the gate diet of 0.18.0 (design: the 2026-09-24 gate-diet spec).
Since 0.18.0 the pipeline calls these gates by name: the inner loop runs the task gate, and the
orchestrators record the baseline at arm time and run the checkpoint gate at every phase
checkpoint.

## 1. The schedule

| Gate | Who runs it | When | What runs | Green | Red |
|---|---|---|---|---|---|
| **task** | the `implementer` agent, inside its inner loop | after each edit round of one task | `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify.py task --files <changed paths>` — `verify.fast` on the paths, then `verify.test` on the subset matching `verify.testSurface` | every stage `ok` or `skipped` | fix and loop, `MAX_VERIFY_ITERATIONS = 4`, then a `DEVIATION` halt |
| **checkpoint** | the orchestrator, in the shared checkpoint procedure | before every phase-end commit and push; once on a single-phase ticket | `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify.py checkpoint --ticket <TICKET_ID>` — `verify.commands` in order, compared against the run's baseline | no stage with `new_keys` (no baseline: no red stage) | `## Verify Fixes` implementer rounds, `MAX_CHECKPOINT_VERIFY_ROUNDS = 2`, then cap escalation |
| **final** | the orchestrator, in the completion gate | end of ticket, before `pr-description` | no run when no file matching `verify.surface` changed since the last checkpoint commit (`git diff --name-only <commit>..HEAD` plus the working tree, filtered by `verify.surface`; with the key absent every changed file counts, so a docs-only change re-runs once); otherwise one more **checkpoint** gate | as checkpoint | as checkpoint |
| **baseline** | the orchestrator, at a fresh arm | once per run, right after the planning or work-list checkpoint and before the first implementer dispatch; never on resume, never at a phase boundary | `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify.py checkpoint --record-baseline --ticket <TICKET_ID>` | always exit 0 (exit 2 is still an environment error, and writes nothing) | — |

The full gate — `verify.commands`, with the test suite — runs at the checkpoint and final
gates and **runs nowhere else**: not per task, not as a tasklist item, not re-derived by an
agent from evidence.

## 2. Invoking the runner

    verify.py [--fast] [--files a,b] [--timeout N]             # the form the hooks use; unchanged
    verify.py task --files a,b [--timeout N]
    verify.py checkpoint [--record-baseline] [--ticket <TICKET_ID>] [--timeout N]

- `task` without `--files` is an `invalid_argument`: a task gate always has a scope.
- `checkpoint` resolves the ticket from `--ticket`, else `<specs.dir>/.active_ticket`; the
  phase suffix is stripped, because the baseline lives at
  `.artel/run/<TICKET_ID>/verify-baseline.json`, ticket-top-level like everything under
  `.artel/run/`. No ticket anywhere is an `invalid_argument`.
- Exit codes on every form: `0` clean or skipped, `1` findings, `2` environment error.
- After editing `verify.commands`, re-arm: the baseline is keyed by stage index.

## 3. The envelope

One JSON line, the shape `docs/workflow-guide.md` describes, with these additions:

- every stage has a `name`: `fast` and `test` on the task gate, `s<index>` elsewhere;
- a skipped half of the task gate is `{"name": …, "skipped": true, "reason": "no fast command"
  | "no test command" | "no test path in scope"}`; `data.skipped` is true only when both halves
  were skipped;
- the `test` stage carries `scoped` (whether the command has the `{files}` token) and `files`
  (the test paths it ran on);
- the task gate's `data.missing` lists the `--files` paths that do not exist (a task that
  deleted them); they are dropped from both scopes, and a scope emptied that way is
  `skipped` with `no existing path in scope`;
- on the checkpoint gate `data.baseline` is `absent`, `loaded`, `recorded`, `disabled` or
  `skipped`, `data.baseline_path` names the file, and with a loaded baseline each stage
  carries `new_keys` (its keys not in the baseline) and `baseline_red` (red, no new key,
  **and red at arm time too** — a stage that was green then and fails now without output is
  red, not baseline red);
- the checkpoint gate's `keys` are **uncapped** (the legacy and task forms keep the 200-key
  cap): the compare has to see every finding.

The baseline file: `{"recorded_at": <ISO-8601 UTC>, "stages": [{"name", "command", "ok", "keys"}, …]}`,
where `command` is the command as executed (`{files}` substituted), `ok` whether the stage
passed at arm time, and `keys` its uncapped finding keys.

## 4. Rules

1. **`skipped` is never green.** An empty `verify.fast`, an empty `verify.test`, a task with no
   test path in scope, or an empty `verify.commands` records that half or that gate as
   `skipped`. A skipped gate does not block; the journal says `skipped`, never `green`.
2. **Exit 2 is an environment error** on every gate: stop-and-ask, never a fix round, never an
   edit to application code. Toolchain problems are not code problems.
3. **The chain rule.** The task gate and a checkpoint gate without a baseline stop at the first
   red stage. With a baseline loaded, a stage that is red only on baseline keys is
   `baseline_red` and does not stop the chain — a dirty analyzer stage no longer hides the
   test stage — and a stage with `new_keys` does.
4. **The full gate runs nowhere else** (§1).
5. **A scoped test command has the `{files}` token.** Without it `verify.test` runs the whole
   suite on every task, which is what the key exists to avoid; the envelope says
   `scoped: false` so a router or a reviewer can tell.
6. **Evidence.** The task gate's envelopes go to the ticket's `verify/` directory
   (`iteration-<i>.json`, both stages in one file); a checkpoint gate's result goes into the
   checkpoint's journal entry (stage names, `baseline_red` stages, `new_keys` when red).

## 5. What the baseline cannot see

Finding identity is a stage's output line with ANSI codes and digits stripped — the same key
the fast-verify hooks have baselined per session since 0.5.0, so the two agree. Two blind
spots follow, stated so nobody rediscovers them:

- a new finding whose text equals a baseline finding's — the same rule in the same file on
  another line — collapses into the baseline key and is not reported;
- a line that carries something other than digits that changes from run to run (a temp
  path, a UUID, a timestamp spelled in words) is a new key every time, so a baseline-red
  stage that prints one stays red until the host's command stops printing it.

The alternative the evaluated runs applied by hand — a file byte-identical to the default
branch is baseline — was coarser than both.
