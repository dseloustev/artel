# The parallel coordinator procedure

The OpenCode build's opt-in coordinator loop, read from `tail.md` when the host's
`parallel.seats` is greater than `1`. It replaces the serial gates for the whole armed run:
one inline coordinator (this session), one pinned worker session per task, one integration
transaction at a time. `parallel.seats` is **worker concurrency** — how many worker sessions
run at once — and it is **not `seat.enabled`**: the seat is a Claude Code tail layer that does
not exist here, and nothing in this procedure depends on it. There is no outer seat, no
second orchestrator and no subagent-driven parallel branch: the loop below runs in this
session.

Every run-state mutation goes through the coordinator CLI; never edit a manifest, a
transaction record, a launch journal or a claim row directly, and never drive the helpers
through ad-hoc Python — the CLI is the only entry point. Never re-implement the scheduler,
the state reducers or the publication plan in this session — the CLI dispatches to the
reviewed helpers, and its answer is the truth. Store, queue, session and tracker calls stay
with this coordinator, through its own configured tools.

Every command answers with the deterministic JSON envelope on stdout: exit `0` is success,
exit `1` is a blocked outcome (reconcile, wait or report it per the branch below), and exit
`2` is invalid input or an environment failure — stop and report, never retry it blind.

## 0. The gate

Resolve the host configuration through `config`:

    python3 ${CLAUDE_PLUGIN_ROOT}/scripts/parallel.py config --request <a request file>

with `{"repo": "<the coordinator checkout>"}`. Exit `2` (for example an invalid
`parallel.seats` — a boolean, zero, a string) is invalid configuration: report it and stop;
never arm parallel work on it. Exit `0` with `data.seats == 1` (or absent) means the serial
procedure applies: this file does not. Only `data.seats > 1` continues.

`parallel.seats` is not a suggestion: the run manifest is created with exactly that seat
count, the CLI refuses a manifest whose seats disagree with the host, and the admission
selector enforces it. Do not start more workers than the seats, and do not fall back to the
serial gates after parallel work has begun.

## 1. Preflight, lock, reconcile

Nothing below runs before this section passes, in this order:

1. **Capabilities.** Call the `artel_parallel_workerCapabilities` tool (read-only). A report
   that is not `ok` — a different OpenCode version, a missing client surface — must **refuse
   parallel mode before any claim or worktree**: report it and stop. Parallel mode is never
   silently pretended to be enabled, and a changed OpenCode version re-runs this gate.
2. **Coordinator readiness.** Run `preflight` with the coordinator checkout and that exact
   capability report:

       python3 ${CLAUDE_PLUGIN_ROOT}/scripts/parallel.py preflight --request <a request file>

   with `{"repo": "<the coordinator checkout>", "capabilities": <the report>}`. A refusal —
   the default branch, a dirty tree, an unusable branch reflog — is a blocked outcome: report
   it and stop before any claim, worktree or session.
3. **Ownership.** Call `artel_parallel_coordinatorIdentity` (read-only) once: it answers this
   session's `session_id`. Run `owner acquire` for the ticket with this run's identity and
   that coordinator (`{schema: 1, ticket, run_id, phase, coordinator_id: <session_id>}`), and
   pass the same `coordinator_id` on **every** mutating command below — the CLI validates it
   inside the repository-common guard, so a second coordinator of the same run is refused even
   when the run UUID is known. A foreign holder is never reclaimed; stop. A deliberate handoff
   to a new coordinator session is only `owner recover` with the run identity, the new
   `coordinator_id` and a reason — never a TTL, a reclaim or a silent takeover; after it, the
   previous coordinator's commands and native effects fail fenced.
4. **The run manifest.** `run inspect`; when the run does not exist yet, `run create` with the
   ticket, phase, run id, the configured seats and the digest of the pinned task graph. The
   create is revision-guarded: an existing manifest refuses.
5. **Reconcile before any new launch.** A resumed coordinator must first reconcile what the
   previous one left, from durable state, never from memory or events:
   - `claim reconcile` with fresh `task_list` rows for the run's own claims;
   - `integration recover` for every task the manifest holds at `integrating` — the landed
     commit is recognized from the transaction-marked branch reflog, never re-committed;
   - `artel_parallel_workerStatus` for every launch this run's journals record;
   - `publication next` for every `integrating` task, then the publication loop (§6) until it
     promotes or refuses;
   - `worktree inspect` for every recorded assignment;
   - `budget inspect` once, so the run-wide correction budget is known before any fix
     dispatch (§4) — never start from zero.
   Launch nothing new while any of these is unresolved. The launch journals resolve under the
   worker marker's repository main checkout (`<main>/.artel/run/…`), while the run manifest and
   the integration records resolve under this explicit coordinator checkout — when the
   coordinator is a linked worktree the two roots differ. Both are this run's state: reconcile
   each where it actually lives, and never move, copy or merge one directory into the other to
   make the paths coincide.

## 2. Admission and claiming

Compute admission fresh from the stored manifest and the current tasklist:

    python3 ${CLAUDE_PLUGIN_ROOT}/scripts/parallel.py admit --request <a request file>

with `{"repo": ..., "ticket": ..., "run_id": ..., "tasklist": "<the tasklist path>"}`. The
selector answers `{start, resume, claim_capacity, drain, reasons}`:

- `drain: true` — a task is `integrating` (the durable admission latch) or an exclusive
  integration is pending. **No new starts, no resumes, no claims**; let the returning workers
  reach safe return and finish the integration/publication first.
- Only the **current unfinished iteration** is admitted: a later iteration's tasks wait, and a
  task whose dependencies are not yet durably published is not ready. Never work ahead into
  another iteration.
- `resume` is the **only** launch signal for a task this run already holds (a fresh claim, a
  local reservation, a retry, a preparation failure). Never launch a task from `start`, from
  `claim_capacity` or from a queue-returned row directly.
- An exclusive task (`Concurrency: exclusive`, a HITL task, or an uncertain write-ownership
  declaration) is admitted alone and holds the run until it is published.

For each task in `start`, claim through the queue's atomic operation in this order — the
queue cannot claim by task id, and its returned row is authoritative:

1. `claim prepare` — persists the durable claim intent **before** the call.
2. Call `task_ready` through the configured queue adapter (the host's queue tools).
3. `claim record` with the returned row, exactly as returned. A row held by this run's own
   actor in the run's phase authorizes the run; anything else is a stop for diagnosis — never
   adopt, clear, release or re-claim a foreign row, and never claim by id.
4. Recompute `admit`. The returned row names a task — reserve that task with `run transition`
   (`expected: null`, `target: reserved`) if the manifest does not hold it yet — then launch
   **only** when the fresh `resume` lists it.

For each task in `resume`: persist `preparing` with `run transition` **before any effect**,
then run §3. Never claim a resumed task again.

**Queue-free (`--local`) runs.** When the invocation inherits `--local`, or the host has no
queue adapter, the run is queue-free: there is no queue to claim from and no row to complete.
Every `start` task is admitted by a **local reservation** instead, and the queue half of the
loop above does not run at all:

1. `run transition` with `expected: null`, `target: reserved` — the assignment's `claim`
   stays `null` (a local reservation, never a fabricated claim, actor or row).
2. Recompute `admit` and launch **only** the tasks the fresh `resume` lists, persisting
   `preparing` before any effect, exactly as above.
3. Zero queue effects in this branch: never call `claim prepare`, the queue's `task_ready`,
   `claim record`, `claim reconcile`, a queue list or a queue release, and never publish a
   queue-done action — the publication plan has no queue action for a null claim, and
   completion publishes the store/evidence receipts only.

A queue-enabled run keeps the authoritative-row flow above unchanged: the returned row is
the reservation, and it is never bypassed with a local reservation.

## 3. Launch and observe one worker

1. **Worktree.** Build the pinned assignment (`schema: 1`, every key present, nullable keys
   explicit) from the task block: ticket, phase, task, run id, attempt, the claim the run now
   holds, the recorded base commit, the declared `Files:`/`Test:`, the effective route, the
   resolved model, and the worker/artifact roots. Then `worktree create`. A lifecycle refusal
   (`not_owned`, `foreign_root`, `incomplete_setup`, `setup_failed`, a dirty or uncertain
   root) is a blocked launch: return the task to `reserved` with a recorded decision or
   `blocked`, preserve the root and report — never reset, clean or delete it.
2. **Model.** Resolve a concrete model before the session exists — `models.py resolve` over
   the host's configured worker site
   (`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/models.py resolve --site implementer.<route_effective>`).
   A `null` model, or a resolver exit `2`, stops the launch: the assignment never carries
   `null`, because a worker must not inherit this coordinator's model. Never pass a Claude
   Code model alias — it does not resolve here.
3. **Session.** Persist `running` with `run transition`, then call
   `artel_parallel_startWorker` with the strict assignment, the worker agent and the pinned
   prompt. The adapter writes its launch journal before any session exists and reconciles a
   duplicate start from durable state; never create a session any other way, never pass a
   working directory to a subagent as a substitute, and never edit the journal.
4. **Observe.** Poll `artel_parallel_workerStatus` — durable session state plus the validated
   `<artifact_root>/result.json`; never the event stream, which has no replay. A native
   `succeeded` without a valid, identity-matching result is **not** completion; native idle is
   not completion; and an `interrupted` outcome **overrides** a contradictory `succeeded`
   result. `interruptWorker` is the only way to stop a worker — it interrupts the journaled
   session and never resumes or retries.
5. On a valid `succeeded` result: persist `verifying/reviewing` and go to §4. On a failed,
   blocked or interrupted worker: persist the matching side state with the recorded decision,
   keep the evidence, and let the run's bounded retry decide (a retry keeps the identity and
   takes the next attempt).

## 4. Review, fix and prepare

The review is coordinator-owned and scoped to **that worker's exact diff, root and attempt** —
a worker never orchestrates its own review, and a worker submission with `review: null` is not
an accepted review.

- Run the task gate on the worker's tree and record its envelope.
- `full`-route tasks (including any task whose actual diff exceeds its declared `Files:`, or
  whose deviation floors the route) get a `run-reviewer` task review of the worker diff.
  Resolve the reviewer model from the host config before the dispatch; never a Claude alias.
- Fix lists stay **serial** — one fix dispatch at a time, in the worker's own tree, inside the
  transported paths; a new path is a new attempt, not an unreviewed commit.
- **Central budgets are durable.** Every fix dispatch in this loop — a task review's
  `## Code Review Fixes` round, a ticket/phase review's fix round, a runtime fix, a checkpoint
  `## Verify Fixes` round — reserves its round from the run-wide budget **before** the
  dispatch: `budget inspect` fresh, reconcile the canonical artifact round (the task review's
  journal entry, `review.md`'s `**Review round:**`, `runtime-observation.md`, the checkpoint
  entry) against the recorded reservations, then `budget reserve` with the true
  `kind`/`scope`/`attempt`/`ordinal` and the revision you just read. The budget is the existing
  `counters.correction_rounds` in the coordinator's
  `.artel/run/<TICKET_ID>/run-state.json` — never a second ledger, never a manifest key, never
  a worker payload field. On resume, never start from zero: inspect and reconcile first. A
  `stale_revision` means re-inspect and retry; `budget_exhausted` or `scope_cap` means stop
  admission and escalate — no fix dispatch happens without a durable reservation. A
  reserved-but-undispatched round stays charged until the same identity resumes, and there is
  no reset, decrement or recredit.
- The caps are the canonical ones, and concurrency never multiplies them:
  `MAX_TOTAL_CORRECTION_ROUNDS = 8` is shared by every task, review, runtime and checkpoint fix
  round; `MAX_TASK_REVIEW_ROUNDS = 1` is per worker task review (its journal `task review`
  entry); `MAX_REVIEW_ROUNDS = 3` is per ticket/phase `review.md` artifact — not the sum of
  every worker's initial review; `MAX_RUNTIME_RETRIES = 1`; `MAX_CHECKPOINT_VERIFY_ROUNDS = 2`.
  Only an actual fix dispatch is charged — never the initial plan review or a worker's own
  execution.
- Workers never commit, push, edit the tasklist, touch the queue or run state, write shared
  completion artifacts or ask the person. Only the coordinator asks questions (the `question`
  tool), and only the coordinator updates shared artifacts.

When the accepted review and the gate are green, persist `prepared`. The task is a safe
return: it parks until the coordinator integrates it and is never relaunched, blocked or
cancelled to make room.

## 5. Integration — one transaction at a time

Persist `prepared → integrating` with `run transition` **first**: that record is the durable
admission latch. From that moment `admit` returns `drain`, so nothing new starts and no
reservation resumes for the whole transaction.

1. **Wait for safe returns.** No sibling may still be `preparing`, `running` or
   `verifying/reviewing`, and no other task may be `integrating`. A `prepared` or `reserved`
   sibling parks and is left alone — never cancelled, blocked or published to make room.
2. **Transport.** `integration prepare` with the assignment and the validated result. It
   creates the normal hooked, code-only transport commit in the worker tree, retains it under
   the run's result ref and records the transaction. A refusal (`drain_required`,
   `major_deviation`, `route_floor`, `commit_refused`, …) is a blocked integration: keep the
   record and the root inspectable, resolve the cause, and never reset, clean or force
   anything.
3. **Apply.** `integration apply` with the prepared transaction. The coordinator must be on the
   transaction branch at its pre-HEAD with a **completely clean nonignored tree**; a dirty
   coordinator is a refusal before the mutation — a person's file is never swept in, reset,
   stashed or staged to pass the check. A conflict preserves git's own state (`conflicted`):
   resolve it through a bounded fix/review procedure, never `--abort`/`--force` someone's work.
4. **Documents.** On the files path, write the cycle's tasklist tick and ordered notes only
   **after** a successful apply and list them in the transaction's `documents`. On the
   artifact-store path `documents` stays empty: the code commit lands first and §6 publishes
   the versioned artifacts afterwards.
5. **Commit.** `integration commit` with an ordinary conventional subject (no task id, no
   trailer). The integrated tree runs the existing task gate and the host's normal hooks; a
   red gate or a refused hook commits nothing and leaves the staged tree inspectable. The
   commit carries the transaction id as its reflog action, so recovery never guesses. After a
   restart, `integration recover` recognizes a commit that landed before its receipt — exactly
   once; never re-commit by guess.
6. **Publish** (§6), then recompute `admit`. Normal admission resumes only once no
   `integrating` record remains.

**Resume by stage.** Continue an interrupted transaction from its recorded stage — `prepared`
continues at apply, `applied` at the commit, `committed`/`recovered` at publication — and
`recover_cycle` before retrying anything. Never call `integration prepare` again to "restart"
an applied transaction: a repeated prepare can rewind a landed stage, and recovery never
creates a second transport commit. A stage refusal (`conflicted`, `apply_failed`,
`commit_refused`, a red gate) is diagnosed from the record and the tree as they are.

## 6. Publication

Publication is a loop over fresh observations, never a stale batch:

1. Assemble the observed bundle fresh: the exact integration record (with its current
   `publication` field), the native `artel_parallel_workerStatus` disposition, the configured
   store mode with the pending tick and accepted deviation proposals, the run's own recorded
   queue row, and the worker evidence with the coordinator's collected copies.
2. `publication next` with the manifest, the task and that bundle. It returns the ordered
   actions still owed, an action whose effect is already durable as `observed`, or the single
   `promote` when a complete record lost its manifest write.
3. For each returned action, in plan order: persist its `planned` receipt with
   `publication record` **before** the effect; execute the effect through the configured store
   or queue adapter (the CLI never calls them); persist the confirmed `applied`/`observed`
   receipt after it. Never hand-make an action or a receipt — plan fresh before every effect,
   and reconcile a lost reply from fresh observation instead of repeating the effect.
4. Queue done is **last** of the external effects, under the run's exact recorded row/actor.
   Store effects use the action's expected version — never overwrite a concurrent edit. A
   receipt is rechecked against the fresh observation before the publication finishes: a
   reopened, reacquired, replaced or deleted queue row refuses and keeps the `integrating`
   latch for reconciliation — never a silent re-done.
5. The record persists into the integration transaction's reserved `publication` field before
   the manifest moves; a valid publication never depends on cleanup, and a dirty or unknown
   worker root is retained rather than force-cleaned. Cleanup is a later, conservative
   operation with the publication proof; a refusal never undoes the publication.

## 7. Drain, pause, cancel and stop

- **Drain** (`admit` → `drain: true`): stop admission, let the returning workers reach safe
  return, finish the one integration and publication, recompute. Never start or resume while a
  task is `integrating`.
- **Pause** (a HITL task, a `question`, a deviation escalation, budget exhaustion, a major
  deviation, an environment error): stop new admission; let unaffected workers reach safe
  return; keep every reservation, worktree, ref, record and the ticket ownership; keep the
  evidence; and ask the person — only the coordinator asks. A pause retains ownership and
  recovery state.
- **Cancel/stop** a task only through its actual transitions: `run transition` to `cancelled`
  with the recorded decision, `artel_parallel_interruptWorker` for a running session. Never
  invent a state, a key or a release.
- A run that cannot proceed (an ambiguous owner, unusable recovery evidence, a foreign queue
  row) stops admission and is diagnosed from durable state — same-run identity is not proof
  that another coordinator is absent, so re-check ownership and the reflogs before resuming.

## 8. Finish

The run is finished only when it is **quiescent**: no task is active or `integrating`, every
task's completion is durably published (required review, gate, evidence, queue and store
receipts all recorded), and the publication latch is clear. Only then:

1. Run the serial final gates from `tail.md` — the whole-tree review, the runtime gate, the
   docs stage and the checkpoint — over the integrated branch. Their fix lists are serial.
2. Write the final report and update the run state exactly as the tail says.
3. Release ownership with `owner release` **only** for this matching, completed, quiescent run
   (`completed: true`, `quiescent: true`). The CLI re-checks it for you: it reads the exact
   run's manifest (every task `published`, no latched stop, no unfinished owned claim, and no
   orphan claim whose external release is still unconfirmed — reconcile it with a fresh
   complete observation first) and the matching `run-state.json` (`completed: true`) and
   refuses `premature_release` otherwise — a forged flag cannot release an unfinished run. A
   pause, an unfinished run or any unresolved `integrating`/publication state keeps the lock.

Do not release, move or reconfigure anything else: no global installs, no service restarts, no
changes to another run's state or another coordinator's claims.
