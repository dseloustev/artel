# OpenCode parallel workers — frozen contract

*Status: v0.2 · 2026-10-19 · proven on OpenCode 2.0.32*

Sub-project 2c runs one worker session per task on the OpenCode host. This file is the
contract the native capability gate froze: it fixes the session interfaces the adapter
uses, the worker's role, permissions and location, the assignment and result `schema: 1`, the
task states and their transitions, integration ownership, the concurrency metadata, and what
happens when a capability is missing. The contract is shipped behavior: the contract, state,
claim, scheduler, worktree, integration and publication helpers exist
(`scripts/parallel_contract.py`, `scripts/parallel_state.py`, `scripts/parallel_queue.py`,
`scripts/parallel_scheduler.py`, `scripts/task_worktree.py`,
`scripts/parallel_integration.py`, `scripts/parallel_publication.py`), the worker-session
adapter is registered by the bridge (`opencode/plugin/parallel.ts`), the coordinator drives
them through `scripts/parallel.py` (§13.10-§13.11), and `parallel.seats` absent or `1` keeps
the serial procedure exactly as it is (a boolean is not an integer and fails configuration
validation).

Proven against **OpenCode 2.0.32** only (re-proved 2026-10-19; the original proof was 2.0.29).
The names below are the installed v2 HTTP API and event stream, retrieved through
`opencode api get /openapi.json`; capability coverage is exactly the proven version, not every
V2 release, so a version change re-runs the gate before parallel mode is enabled.

Validated end to end on a real host fixture (2026-10-16) in local file-backed mode: native
worker sessions implemented disjoint tasks at `seats: 2`, ran the scoped task gate, passed
task reviews, and the coordinator owned the transport/apply/one-cycle commit and the durable
completion publication, with a pause/resume across a coordinator restart and a `seats: 1`
sample. §14 records the evidence and, explicitly, the limits.

## 1. What the proof established

The gate ran two real worker sessions concurrently in two detached per-task worktrees of one
scratch host, each with an explicit location, agent and model, and both blocked on a scratch
barrier until the observer had recorded `started == {A, B}` and `completed == {}` from the
event stream; only then were they released. Both made a distinct file edit and ran an
ordinary task check, and the existing bridge hooks ran in each worker's own root.

Demonstrated natively on 2.0.32 (re-proved 2026-10-19; originally 2.0.29): explicit
`location`/`agent`/`model` on session create; concurrent sessions with per-session event
streams; `session.execution.started` and `session.execution.succeeded`; interrupt with a
durable `interrupted` outcome; durable status and message recovery after a deliberately
missed event window (the stream has no replay);
session permission rules (`deny` blocks, `ask` surfaces a request that a client answers);
agent switch; session move; child-session inheritance of location and permissions; the
existing hooks (session baseline, stop gate, fast verify) running per root; and the existing
guards denying normally (`vcs_guard`, `sensitive_guard`) without loosening anything.

Not established by the proof — the unproven items are listed in §12 and are not claims.

## 2. The adapter's native surface

The adapter is a thin layer over the running service's session API. The coordinator addresses a
location-scoped endpoint with the `location[directory]` query parameter; the CLI's `--param`
cannot express that object, so location-scoped calls go through the installed plugin client
(`ctx.session.*` — the same API the proof exercised over HTTP) rather than the CLI (the CLI
remains the operator path for plain requests).

| Operation | Request | Response |
|---|---|---|
| Create a worker session | `POST /api/session` — `{id?, parentID?, title?, agent?, model?{providerID, id, variant?}, location?{directory}, metadata?, permissions?}` | `{data: Session.Info}` |
| Send the pinned prompt | `POST /api/session/{id}/prompt` — `{id?, text, files?, agents?, skills?, metadata?, delivery?("steer"\|"queue"), resume?}` | `{data: Session.Inbox.User}` |
| Read status | `GET /api/session/{id}` | `{data: Session.Info}` |
| Interrupt | `POST /api/session/{id}/interrupt?resume=true\|false` | `{interrupted: boolean}` |
| Wait for idle (bounded) | `POST /api/experimental/session/{id}/wait` — experimental route | `204` when the agent loop is idle |
| Read messages | `GET /api/session/{id}/message?limit&order&cursor&type` | `{data: [Message.Info], cursor}` |
| List running sessions | `GET /api/session/active` | `{data: {"<sessionID>": {type: "running"}}}` |
| Pending permissions | `GET /api/session/{id}/permission` | `{data: [Permission.Request]}` |
| Answer a permission | `POST /api/session/{id}/permission/{requestID}/reply` — `{decision: "once"\|"always"\|"reject", message?}` | `204` |
| Switch agent | `POST /api/session/{id}/agent` — `{agent}` | `204` |
| Switch model | `POST /api/session/{id}/model` — `{model: {providerID, id, variant?}}` | `204` |
| Move location | `POST /api/session/{id}/move` — `{directory, delivery?}` | `204` |

`Session.Info` carries at least `id`, `projectID`, `cost`, `tokens`, `time{created, updated}`
and `location{directory}`; when set it also carries `parentID`, `agent`, `model`,
`outcome("succeeded"|"failed"|"interrupted")`, `time.idle`, `title`, `metadata` and
`permissions`. A child session carries `parentID`; its `session.created` event carries
`parentID` and the inherited `permissions`.

Thin adapter types:

```ts
type WorkerCreate = {
  location: { directory: string }                                  // the assigned worker_root
  model: { providerID: string; id: string; variant?: string }      // a concrete resolved model
  agent: string                                                    // the worker role
  permissions?: Array<{ action: string; resource: string; effect: "allow" | "deny" | "ask" }>
  metadata: { schema: 1; ticket: string; phase: number | null; task: string;
              run_id: string; attempt: number; claim: string | null }
  title?: string
}
type WorkerPrompt = { text: string; delivery?: "steer" | "queue"; resume?: boolean }
type WorkerStatus = {
  id: string
  outcome?: "succeeded" | "failed" | "interrupted"
  parentID?: string
  agent?: string
  model?: { providerID: string; id: string; variant?: string }
  location: { directory: string }
  time: { created: number; updated: number; idle?: number }
}
type WorkerInterrupt = { interrupted: boolean }
```

The adapter writes the assignment identity into the session's `metadata` at create time, so a
coordinator that reconnects can correlate a native session with its assignment without
relying on conversation memory.

Observable events (server-wide stream, `GET /api/event`, SSE): `session.created` (location,
and `parentID`/`permissions` for a child), `session.execution.started`,
`session.execution.succeeded`, `session.execution.interrupted` (`{reason}`),
`session.tool.called`, `session.tool.success`, `session.tool.failed` (`error.type`; `aborted`
after an interrupt), `permission.asked` and `permission.replied`, `session.agent.selected`
(`{agent, previous}`), `session.moved`, and the step/text/reasoning deltas. The stream is
volatile: events during a disconnection are missed and are not replayed, so the coordinator
recovers state from `Session.Info` and the messages, never from an event it did not see.

### 2.1 The adapter tools (Task 7)

`opencode/plugin/parallel.ts` registers five tools in the namespace `artel.parallel` (exposed
ids `artel_parallel_workerCapabilities`, `artel_parallel_coordinatorIdentity`,
`artel_parallel_startWorker`, `artel_parallel_workerStatus`, `artel_parallel_interruptWorker`)
through the existing bridge (`opencode/plugin/artel.ts`). The tools are the native transport
only: every decision — request validation, the capability gate, the launch journal, session
verification, result recovery, interruption semantics — lives in the policy half
`scripts/parallel_launch.py`, which reuses `parallel_contract.py`, `hook_common.py` and
`task_worktree.py`'s contracts instead of restating them.

**Coordinator identity and the native fence.** `coordinatorIdentity` is read-only: it answers
the calling session's own `session_id` and root, and takes no lock. The generated procedure
calls it before `owner acquire` and passes that `session_id` as `coordinator_id` on every
mutating command (§13.4). `startWorker`, `workerStatus` and `interruptWorker` are fenced to
the exact run's current coordinator: the transport spawns one bounded ephemeral Python
guard-holder child (`parallel_launch.py guard-hold`) that takes the same repository-common
kernel guard the CLI takes, validates the owner and the calling coordinator inside it, and
holds the lock across the whole awaited native sequence — session create/prompt/interrupt and
the journal writes around them — releasing it on EOF, on error or by the kernel when the
child dies. A read-only policy check alone would not exclude a concurrent `owner recover`
while an RPC is in flight; the held guard does, and after a recovery the replaced session's
native effects are refused (`coordinator_conflict`) before any journal write, session create
or interrupt. The guard-holder is lifetime plumbing only: it never mutates run state and
every decision stays in the policy half. `workerCapabilities` stays read-only and unfenced.

**Per-instance root.** `opencode/plugin/artel.ts` exports `createArtelPlugin({root})`: one
bridge instance bound to an explicit install root for every hook spawn and for the adapter
registration (`registerParallel(ctx, {root})`). The default export is the default instance
with the `ARTEL_ROOT` environment fallback unchanged; instances never share a root and never
mutate the process environment, so a trusted scratch proof can load the current bridge from
its own checkout without touching global state.

**Capabilities.** `workerCapabilities` is read-only and reports the installed version and the
plugin-client surface the adapter needs. `startWorker` re-checks that report first: anything
but exactly 2.0.32 with the full surface is refused (`unsupported_version` /
`unsupported_api`) before any journal, session or prompt exists. The coordinator's preflight
consumes this report before its first claim or worker creation (§12).

**Requests.** `startWorker` takes `{assignment, agent, prompt{text, delivery?}, permissions?}`
— the strict schema-1 assignment plus the worker role, the pinned prompt and an optional
session permission ruleset; `workerStatus` and `interruptWorker` take `{assignment}`. Role,
session and root metadata never enters the strict assignment/result payloads.

**Launch journal and ordering.** `startWorker` persists a launch journal under
`<main>/.artel/run/<ticket>/parallel/<run_id>/launches/<task>-a<attempt>.json` — the launch
intent and the assignment identity **before** any session can exist, then the session id
**before** the pinned prompt is admitted. The session id is derived from the assignment
identity, so the native create is idempotent by id: a duplicate start, a crash between
create and record, or a crash between prompt and record reconciles from the durable native
session and messages — adopting the existing session or prompting once — instead of creating
a second session or double-prompting. A wrong session (id or parent), root, agent, model or
permission ruleset is refused, never adopted; the session's `metadata` must carry every
launch-identity key — `schema`, `ticket`, `phase`, `task`, `run_id`, `attempt` and `claim`,
with nullable `phase`/`claim` present as explicit `null` — exactly typed and equal, so a
missing key, a null where an integer belongs and a boolean or float for an integer all
refuse, while additional host-added namespaces (a host may decorate its sessions with its own
metadata) are tolerated because they do not change the launch identity; a malformed journal is
refused, never overwritten; a null or unresolvable model is refused (`unresolved_model`) and
never inherited (§9).

**Durable status.** `workerStatus` reads the native session and the validated
`<artifact_root>/result.json` only — never the event stream, so a missed event window costs
nothing. `success` requires a native `succeeded` outcome, a valid result whose identity
matches the assignment and a `succeeded` result status: native idle, native success without a
valid result, and a missing, malformed, escaping or identity-mismatched result are all
non-successful; an `interrupted` outcome wins over a contradictory success result and is
reported as `interrupted_over_result`. `interruptWorker` interrupts only the journal's known
session and reports the durable post-interrupt state; it never resumes or retries.

**Coordinator-only.** The adapter namespace is coordinator-only twice over: the bridge's
worker guard denies `subagent`, `question` and every `artel_parallel_*` tool in a worker
location, and the policy half independently refuses a caller whose location is a pinned
worker root (`caller_is_worker`, via the authoritative marker reader). A worker therefore
gains no session-creation authority by calling the adapter instead of `subagent`: it is not
a second orchestrator and never reaches `startWorker`. The adapter never schedules, claims,
releases, publishes, commits, edits tasklists, answers permissions or drives another
coordinator.

## 3. Worker role, permissions and location

- **One worker, one worktree.** A worker session's `location.directory` equals its
  `worker_root`, the task's own worktree ([worktrees.md](worktrees.md)); no two workers share
  a tree. The existing hooks run in that root — the session baseline, the stop gate and fast
  verify all observed the worker's worktree as their cwd/root.
- **Role.** The session is created with an explicit worker `agent`; the role is reported back
  on `Session.Info.agent` and on every assistant message, and can be switched mid-session.
  A worker must not mutate the queue, run state, tasklist or notes, must not commit, push or
  open a pull request, and must not ask the user — it executes the pinned assignment and
  returns the structured result. Native permission rules can deny individual actions, but
  nothing native enforces the worker role by itself: a worker session can dispatch subagents,
  act as a second orchestrator, or be re-prompted by an armed run's stop gate unless the
  implementation prevents it. Those worker-mode protections are not native and are enumerated
  under "Not native" in §12; they are implementation work, not shipped behavior.
- **Permissions.** A session carries a ruleset of `{action, resource, effect}` rules; rules
  combine in order, the last match wins, `deny` blocks the operation, and `ask` surfaces a
  `Permission.Request` that a client answers with `once`, `always` or `reject` (the official
  V2 permissions documentation states that a `reject` also rejects the session's other pending
  requests; only `once` was exercised in the proof — §12). Session rules are evaluated after the
  agent's rules; a child session inherits the rules in effect when it is created. The only
  native path boundary is the base policy's `external_directory` ask: nothing native scopes a
  worker to its worktree.
- **Location.** `POST /api/session/{id}/move` changes the location and the hooks follow it;
  the coordinator pins each worker to its assigned root and treats an unexpected move as a
  deviation.

## 4. Assignment — `schema: 1`

The coordinator hands a worker one pinned assignment. Every key is required; a nullable key
is present with an explicit `null`, never missing.

```json
{
  "schema": 1,
  "ticket": "PROJ-123",
  "phase": 2,
  "task": "3.1",
  "run_id": "0f6f0f2e-6b1a-4f2e-9c5d-2b7a1c8e4d10",
  "attempt": 1,
  "claim": "artel@host",
  "base_commit": "0123456789abcdef0123456789abcdef01234567",
  "files": ["src/a.py", "tests/test_a.py"],
  "tests": ["tests/test_a.py"],
  "route": "full",
  "model": "provider/model#variant",
  "worker_root": "/absolute/path/to/the/task/worktree",
  "artifact_root": "/absolute/path/to/the/host/artel/run/PROJ-123"
}
```

| Field | Type | Rules |
|---|---|---|
| `schema` | integer | exactly `1`. |
| `ticket` | string | canonical `<PROJECT_KEY>-<number>` ([ticket-parsing.md](ticket-parsing.md)). |
| `phase` | integer or null | the phase number of a phase-scoped run; `null` for a ticket-wide run. |
| `task` | string | the task block's `<iteration>.<n>` ([task-grammar.md](task-grammar.md)). |
| `run_id` | string | the run's transaction id, a canonical UUID; the same vocabulary the deep-review manifest uses. |
| `attempt` | integer | ≥ 1; incremented by a recorded retry/recovery decision. |
| `claim` | string or null | the queue claim the coordinator holds for the task (the queue's actor holder, `artel@<hostname>`); `null` when the run is not queue-backed or the claim is not taken yet. |
| `base_commit` | string | a full commit id (40 hex); abbreviated ids are invalid. |
| `files` | array of string | the task's declared `Files:`, repo-relative paths. A declaration, not a sandbox (§11). |
| `tests` | array of string | the task's declared `Test:`, repo-relative paths; empty for `none`. |
| `route` | string | `"light"` or `"full"`, the effective route at dispatch ([task-grammar.md](task-grammar.md) §7). |
| `model` | string or null | `<provider>/<model>` with an optional `#<variant>` ([config.md](config.md), `models`); explicit `null` means inherit at Artel's resolver boundary (§9). |
| `worker_root` | string | absolute path of the assigned per-task worktree. |
| `artifact_root` | string | absolute path of the only root under which the worker writes artifacts. |

## 5. Result — `schema: 1`

The worker returns the same identity plus the outcome of its work. The identity fields must
equal the assignment's exactly; a mismatch rejects the result.

```json
{
  "schema": 1,
  "ticket": "PROJ-123",
  "phase": 2,
  "task": "3.1",
  "run_id": "0f6f0f2e-6b1a-4f2e-9c5d-2b7a1c8e4d10",
  "attempt": 1,
  "claim": "artel@host",
  "base_commit": "0123456789abcdef0123456789abcdef01234567",
  "files": ["src/a.py", "tests/test_a.py"],
  "tests": ["tests/test_a.py"],
  "route": "full",
  "model": "provider/model#variant",
  "worker_root": "/absolute/path/to/the/task/worktree",
  "artifact_root": "/absolute/path/to/the/host/artel/run/PROJ-123",
  "status": "succeeded",
  "changed_files": ["src/a.py", "tests/test_a.py"],
  "verify": [
    {"command": "python3 -m pytest tests/test_a.py", "ok": true, "output": "3 passed"}
  ],
  "review": null,
  "report": "report.md",
  "deviations": []
}
```

| Field | Type | Rules |
|---|---|---|
| `status` | string | `"succeeded"`, `"failed"`, `"blocked"` or `"cancelled"`; the worker's completion of the assignment (§10). |
| `changed_files` | array of string | the paths the worker actually changed, repo-relative; every resolved path must stay inside `worker_root`. |
| `verify` | array of object | `{command: string, ok: boolean, output: string}` — one entry per ordinary check the worker ran; `output` is the captured tail. |
| `review` | object or null | coordinator-owned: `{required: boolean, route: "light"\|"full", verdict: "passed"\|"changes-requested"\|"skipped", rounds: integer}`; explicit `null` in the worker's submission, filled before the task can leave `verifying/reviewing`. |
| `report` | string or null | the worker's report, a path relative to `artifact_root`; `null` when the task produces none. |
| `deviations` | array of object | `{severity: "minor"\|"major", summary: string, paths: array of repo-relative string}` per [deviation-protocol.md](deviation-protocol.md); `[]` when clean. |

Validation: `schema` must be exactly `1`; the identity must match the assignment; commit ids
are full; `worker_root` and `artifact_root` are absolute; and every path in `changed_files`,
`report` and `deviations` must resolve inside its root — a path escaping the assigned root is
rejected and never written. A result carries **no** artifact-store version, queue receipt or
commit id of the worker's own work: those are coordinator-owned manifest entries (§8).

## 6. Task states and transitions

The coordinator records a task's state independently of the queue row's status:

```
reserved -> preparing -> running -> verifying/reviewing -> prepared -> integrating -> published
```

Side states: `blocked`, `failed`, `cancelled`. Every side transition records the retry or
recovery decision that produced it; a retry keeps the identity and increments `attempt`.

- `reserved` — admitted, not yet claimed or prepared.
- `preparing` — the coordinator resolves the effective model and creates the worker session
  and its worktree; a failure here returns the task to `reserved` or `blocked`.
- `running` — the worker holds the assignment; the coordinator watches events and can
  interrupt.
- `verifying/reviewing` — the task gate and, on the `full` route, the task review
  ([gates.md](gates.md)); the result's `review` field is filled here.
- `prepared` — review/verification passed; the completion is **not** published yet.
- `integrating` — the coordinator integrates the cycle: it makes the cycle's own commit
  (orchestrator-owned, [autonomous-run.md](autonomous-run.md) §14) and updates the shared
  artifacts.
- `published` — the completion is durably published (manifest and queue). Only this state
  unlocks dependents.

A dependent starts only after every dependency is integrated, its completion is durably
published, and its required review/verification passed. Worker completion is not publication:
a `succeeded` result at `verifying/reviewing` or `prepared` does not unlock anything.

## 7. Task concurrency metadata

A task may carry optional metadata `Concurrency: exclusive — <reason>`; its absence means
automatic admission. HITL tasks and commands with unproven shared external effects are
exclusive, and codegen or setup that cannot be shown to be worker-local runs exclusively.
Exclusivity is an admission statement about the task, not a worker permission.

## 8. Integration ownership

The coordinator owns everything outside the worker's result: queue claims and rows, run state
and journals, the coordinator manifest (artifact-store versions and queue receipts), the
per-cycle commits, integration and publication. A worker owns only its result payload written
under `artifact_root`; it never commits, never publishes and never edits shared state.

Per-task worktrees replace shared-tree workers, and the per-cycle commits decided 2026-10-13
([design.md](design.md), decision log) replace wave-level commits: each completed worker
cycle gets its own coordinator-owned commit at integration, not one commit per wave.

## 9. Model resolution and the inheritance trap

`model` is nullable at Artel's resolver boundary, where `null` means inherit. The adapter
must resolve a concrete effective model — the assignment's configured value, or the run's
resolved default — and pass it on `session.create` before any worker starts; it must never
omit the model and let a session inherit, because a worker then inherits the caller's model,
often the coordinator's most expensive one. The resolver vocabulary is
`<provider>/<model>[#<variant>]` ([config.md](config.md), `models`); the API encoding is
`{providerID, id, variant?}`, and the adapter rejects a value that does not resolve against
the installed model list.

## 10. Native outcome, worker status and publication

Three layers, three owners, and only the last one is completion:

| Layer | Values | Owner | Meaning |
|---|---|---|---|
| Native session outcome | `succeeded`, `failed`, `interrupted` | OpenCode | the agent loop ended; never a task result by itself. |
| Worker result `status` | `succeeded`, `failed`, `blocked`, `cancelled` | the worker's result payload | the assignment's work is implemented and the declared checks ran; **not** review passed, not integrated, not published. |
| Task state | §6 | the coordinator | the run's record; `published` is the only state a dependency may consume. |

A native `succeeded` with no valid structured result is not success: the coordinator treats a
missing, malformed or identity-mismatched result as `failed` (or a recorded retry). A
structured `succeeded` alongside a native `interrupted` is not success either — the interrupt
wins. Native `idle` is a scheduling fact, not a result.

## 11. Deviations

`files` is the task's declaration, not a security sandbox: the implementer may need a file it
did not declare. When `changed_files` exceeds `files`, the result records a deviation in
`deviations` and the coordinator routes it per [deviation-protocol.md](deviation-protocol.md)
— minor continues, major halts and escalates. A path escaping `worker_root` is not a
deviation to route: it is rejected (§5).

## 12. Capability failure, unproven items and the native boundary

Parallel mode fails preflight: before any production claim or worktree is created, the
coordinator checks the running service against this contract (the version and the interface
set in §2). A missing or changed capability stops the parallel path and keeps the serial
procedure — parallel mode is never silently pretended to be enabled, and a changed OpenCode
version re-runs the gate.

Unproven on 2.0.32, and therefore not relied on without re-verification:

- The bridge's `session.compaction.ended` and `session.deleted` subscriptions were not
  exercised; neither was `session.execution.failed`.
- The event stream is volatile and has no replay; a coordinator must reconcile from durable
  session state after any disconnection.
- `prompt.resume` and `interrupt?resume=true` were not exercised; the recovery path is the
  coordinator's decision.
- The `reject` permission reply — including the documented rejection of the session's other
  pending requests — was not exercised; only `once` was.
- `POST /api/experimental/session/{id}/wait` is an experimental route: the proof used it for
  bounded idle waits (`204`), but its stability across versions is not guaranteed; keep the
  durable-state fallback.
- A child session inherits location and permissions but not an explicit agent; the
  child-session path through the native subagent dispatch was not exercised end to end.
- The barrier that held the two proof workers relied on the model following the pinned
  prompt; it was scratch instrumentation, not protection.

### Worker-mode protections (shipped) and the remaining native boundary

The native session mechanics do not enforce the worker role by themselves; the following
protections are implementation, and they ship:

- **No-orchestrator role guard.** The bridge denies `subagent`, `question` and every
  `artel_parallel_*` tool in a worker location (the authoritative marker decides), and the
  policy half independently refuses a caller whose location is a pinned worker root
  (`caller_is_worker`), so a worker gains no session-creation authority by naming the
  adapter instead of `subagent`. Natively proven (Task 7 fix round): the three calls were
  denied pre-execution, no child session, no form and no journal.
- **Armed stop-gate interaction.** A worker root is its own checkout with its own run
  state; the coordinator never arms a worker root, so the stop gate is inert there. The
  gate is not disabled for workers — a worker root that were armed would still be
  re-prompted by the existing bridge.
- **Seat admission, queue and lease.** There is no native seat limit, queue or lease: the
  coordinator enforces `parallel.seats`, owns claims and retries, and a crashed coordinator
  leaves its workers running — recovery is the coordinator's durable-state reconciliation
  (§13).
- **Worktree scoping.** Nothing native scopes a session to `worker_root` (only the base
  policy's `external_directory` ask limits paths); the coordinator's path validation and
  the assignment contract are the boundary.
- **Missed-event recovery.** The stream has no replay; the adapter never reads events, and
  the coordinator recovers from durable session state and messages (§2, §10).

## 13. Run state, ticket ownership and claim recovery

The coordinator drives a parallel run from durable host state: one run manifest, one
repository-wide ticket lock, and a claim intent that survives a crash between the queue
call and its record. `scripts/parallel_state.py` owns the manifest and the lock,
`scripts/parallel_queue.py` owns the claim intents and their reconciliation; the
coordinator is their only caller and performs every queue call itself.

### 13.1 The run manifest — `schema: 1`

One manifest per run, host-owned under `.artel/run/<TICKET_ID>/parallel/<run-id>/` (never
in the plugin install), JSON-safe and carrying exactly these keys:

```json
{
  "schema": 1,
  "ticket": "PROJ-123",
  "phase": 2,
  "run_id": "0f6f0f2e-6b1a-4f2e-9c5d-2b7a1c8e4d10",
  "seats": 2,
  "graph_digest": "…",
  "revision": 3,
  "tasks": {},
  "claims": {"seq": 0, "intents": []},
  "stop": null
}
```

| Field | Rules |
|---|---|
| `schema` | exactly `1`. |
| `ticket` | canonical `<PROJECT_KEY>-<number>`. |
| `phase` | integer ≥ 1, or `null` for a ticket-wide run. |
| `run_id` | the run's transaction id, a canonical UUID; the same vocabulary as the assignment (§4). |
| `seats` | the run's `parallel.seats`, a positive integer. |
| `graph_digest` | the digest of the pinned task graph, an opaque non-empty string. |
| `revision` | the write guard, starting at 0; see §13.3. |
| `tasks` | the task records; see §13.2. |
| `claims` | the claim intents and the sequence counter; see §13.5. |
| `stop` | `null`, or the latched stop that halts admission for reconciliation; see §13.5. |

### 13.2 Task records and transitions

`tasks` maps a task's `<iteration>.<n>` to one record:

```json
{"state": "running", "attempt": 2, "decision": {"reason": "retried after the session failed"}}
```

`state` is §6's vocabulary. `attempt` is 1 when the task is admitted and grows by one on
the retry transition only; a retry keeps the task's identity. `decision` is the last retry
or recovery decision recorded on the task, `null` until one is.

The transition table is §6's sequence plus its side states:

| From | To |
|---|---|
| `reserved` | `preparing`, `blocked`, `cancelled` |
| `preparing` | `running`, `reserved` (a preparation failure returns the task), `blocked`, `failed`, `cancelled` |
| `running` | `verifying/reviewing`, `blocked`, `failed`, `cancelled` |
| `verifying/reviewing` | `prepared`, `blocked`, `failed`, `cancelled` |
| `prepared` | `integrating`, `blocked`, `cancelled` |
| `integrating` | `published`, `blocked`, `failed`, `cancelled` |
| `published` | — (terminal) |
| `blocked` / `failed` / `cancelled` | `reserved` (the retry) |

A task enters the manifest at `reserved` and only there. Every side entry, the
preparation-failure return to `reserved` and every retry carries the decision that produced
it; any other transition may carry one. A mismatched expected state, a pair outside the
table, an unknown state or a malformed decision is refused, so the recorded state never
silently drifts.

### 13.3 Revision-checked writes

`revision` starts at 0 and is the write guard. `write_manifest(path, value,
expected_revision)` refuses a value or a file that is not at exactly `expected_revision`
(0 when nothing is persisted yet), writes the next revision, and returns the manifest it
wrote; the reducers never change `revision`, so the coordinator persists each reducer
result before doing anything else with it. The bytes go to a temporary sibling and then
through `os.replace`: a reader sees the previous manifest or the next one, never a
half-written file, and a corrupt file is refused rather than overwritten. `read_manifest`
refuses a missing, unreadable or malformed manifest with its own error kind, so a damaged
run stops instead of re-initialising itself.

### 13.4 Repository-wide ticket ownership

Before it arms, the coordinator owns the ticket across every checkout of the repository.
The lock is one JSON record per ticket in the **repository-common Git directory**:

    <git-common-dir>/artel/parallel/<TICKET_ID>.json

Every checkout — the main one and every linked worktree — resolves that same path through
`git rev-parse --git-common-dir`, so a competing coordinator cannot bypass ownership by
working from another tree. `acquire_ticket(repo, ticket, owner)` creates the record
exclusively and refuses one that already exists, whatever its age, its phase or its run; the
record is the run's ownership proof:

```json
{
  "schema": 1, "ticket": "PROJ-123", "run_id": "0f6f0f2e-…", "phase": 2,
  "coordinator_id": "ses_…", "token": "…", "acquired_at": "2026-10-15T12:00:00Z",
  "recovered_at": null, "prior_coordinator_id": null, "recovery_reason": null
}
```

`coordinator_id` is the **native session id** of the coordinator that holds the ticket —
never the run UUID, the token or a CLI process id — obtained from the read-only
`artel_parallel_coordinatorIdentity` tool. Only the owning run (same `run_id`, `phase` and
`coordinator_id`) re-acquires its own record; another run is `ticket_owned`, another
coordinator of the same run is `coordinator_conflict` even when the run UUID is known, and a
lock that cannot be read is ambiguous and is never overwritten, released or reclaimed.

**The coordinator guard.** Every mutating command — `owner acquire/recover/release` included
— runs inside `ticket_guard(repo, ticket)`: one kernel-locked file per ticket next to the
record (`<git-common-dir>/artel/parallel/<TICKET_ID>.guard`), resolved through the common
Git directory so the CLI and the native adapter serialize on the same inode. Acquisition is
bounded (`owner_busy`, retry), the lock file is never unlinked while POSIX holds it (the
kernel releases it on process exit), and the owner — run, phase and coordinator — is
validated *inside* the guard and held through the whole effect: the manifest write, the Git
effect, the session launch, the owner release. A read-only check can never race the effect it
authorized. Manifest writes additionally serialize their compare-and-replace under a
per-manifest lock, so two concurrent `write_manifest` callers — the CLI and a direct trusted
helper caller alike — cannot both pass the revision check and lose one update. Lock order is
always common guard outer, manifest/budget inner; no path nests them the other way.

**Handoff.** A deliberate handoff to a new coordinator session is only `owner recover`:
`{schema, ticket, run_id, phase, coordinator_id, reason}` for the same run and phase. It
rotates the token, names the new coordinator, and records `recovered_at`,
`prior_coordinator_id` and `recovery_reason`; the previous coordinator's commands and native
effects then fail fenced (`coordinator_conflict`) until the new one holds them. Recovery never
crosses runs, is never inferred from age, has no TTL, and does not prove the previous session
is dead. A lock written before coordinator identity existed (the six-key legacy record) is
never silently adopted: every mutation refuses `legacy_owner` and preserves its bytes until an
explicit `owner recover` adopts it, recording the prior identity as null and the reason.

`release_ticket(repo, ticket, owner)` removes the lock only for that matching run — the
`token` from acquisition is the proof and the current `coordinator_id` must match — and only
with `completed` and `quiescent` asserted: a pause, an unfinished run, a replaced coordinator
and an expired timeout all keep the lock. Nothing is ever inferred from age.

**Threat boundary.** Deliberate same-user shell or filesystem impersonation is outside the
sandbox the feature promises: a token or a session id readable by the same user is not a
secret. What is enforced is the supported flow — two coordinators of the same run cannot both
mutate it, and a replaced coordinator's effects are fenced.

### 13.5 Claim intents and recovery

The coordinator is the only claimer and claims through the queue's atomic operation —
never by task ID, never with an unguarded status write. A claim is made recoverable by its
order and its actor. `begin_claim(manifest)` appends one intent:

```json
{
  "intent_id": "<run_id>#<seq>:<attempt>",
  "seq": 1, "attempt": 1,
  "actor": "artel@<run_id>#<seq>:<attempt>",
  "state": "pending", "row": null
}
```

The actor is unique per run, claim sequence and attempt; every claim of one run shares the
`artel@<run_id>#` prefix, which is how a restarted coordinator recognises the rows its own
crashed attempts still hold. The order is fixed:

1. `begin_claim`, then persist the manifest — **before** the claim call, so the intent is
   durable while the queue call is in flight.
2. call the queue's claim with the intent's actor.
3. `record_claim(manifest, intent_id, row)` with the returned row, then persist the manifest
   — **before any worker is launched**. Only a durable `mapped` row mapping authorizes the
   launch.
4. an unknown outcome — a timeout, or a crash between the call and the record — leaves the
   intent `pending`, and it is never retried blindly. `reconcile_claims(manifest, rows)`
   over the queue's rows resolves it: a row held by this run's prefix is this run's orphan
   and is released; no such row means the claim never landed (`lost`), after which the next
   `begin_claim` retries that sequence with the next attempt; a row held by anyone else is
   never adopted, cleared or reclaimed.

An orphan release is a **decision and an effect**: reconciliation persists `orphaned` and
returns the release action, and the decision stays unresolved until a fresh **complete**
observation confirms it. `reconcile_claims` moves an `orphaned` intent to `released` only
when the exact recorded row is no longer observed `in_progress` under the exact
run/sequence/attempt actor; while the row is still observed held, the one release is
re-issued (it did not execute) and never re-decided. `released` is the durable confirmation
that this run no longer holds the row — an incomplete or filtered row list can never prove
absence, so the coordinator passes its complete observation. An unconfirmed `orphaned` intent
blocks new claims (`begin_claim` → `unresolved_claim`) and blocks `owner release`
(`premature_release`), so ownership is never abandoned over a decision whose external effect
never ran; a foreign or reacquired holder is never released, adopted or cleared, and there is
no release/reclaim scan.

`record_claim` classifies the returned row against the intent:

| Observation | Intent state | Action |
|---|---|---|
| held by the intent's actor, in the run's phase | `mapped` | run: launch the worker once the mapping is durable |
| held by another actor, or by nobody | `foreign` | stop admission for diagnosis; the row is never touched |
| a phase-scoped run's row from another phase | `wrong-phase` | release it and stop admission; recovery re-issues it only on fresh same-row, same-actor evidence |

A stop is a latched record in the manifest — `{"reason", "intent_id", "row"}` — and
`begin_claim` refuses while it is set. A durable decision is never re-decided: whether its
release executed is read fresh from the queue, never assumed from the decision. If a crash
lands between persisting the wrong-phase decision and the external release, reconciliation
re-issues that one release — and only while the fresh observation still shows the exact
same row `in_progress` under the exact run/sequence/attempt actor, which is the proof the
release did not execute. Once the row is observed released — or held by anyone else — it is
never released again.

### 13.6 Admission — the scheduler and its consumer protocol

`scripts/parallel_scheduler.py` answers the coordinator's admission question from the parsed
tasklist and the stored manifest. It is pure — no filesystem, Git, queue or session call — and
the coordinator recomputes it after **every** durable transition or publication:

    select_admissions(iterations, manifest, seats, case_sensitive) -> admission

```json
{"schema": 1, "start": ["2.4"], "resume": ["2.2"], "claim_capacity": 2, "drain": false,
 "reasons": ["…"]}
```

| Field | Meaning |
|---|---|
| `start` | new tasks to admit now, in document order; a task with any manifest record is never in it |
| `resume` | already-owned `reserved` tasks that are safe to launch now, in document order |
| `claim_capacity` | the cap on new claims — a figure, not a row count |
| `drain` | `true` while any task is `integrating` (the durable admission latch) or an exclusive integration awaits/is in progress |
| `reasons` | deterministic diagnostics for an empty or shorter `start` |

The consumer protocol:

1. **`resume` — never claim again; the only launch gate.** Each listed task is a reservation
   this run already holds. Persist its `reserved → preparing` transition (revision-checked,
   §13.3) **before** any effect — no worktree, no session, no prompt — and then launch it. A
   `resume` entry is never passed to the queue's claim operation and is never re-claimed.
2. **`start` — claim through the queue's atomic operation; the returned row decides the
   task.** For each listed task follow §13.5's order: `begin_claim`, persist, call the queue's
   claim (the operation returns the row; the queue cannot be told which task to claim by id),
   `record_claim`, persist. The **returned mapping is authoritative**: the returned row is the
   reservation this run now holds, and it may name a different task than the `start` candidate
   — the tracker's next claimable row can be a task the selector did not list. Reserve that
   task; never claim by id, never release or re-claim a row to obtain the listed candidate. A
   row that is not this run's stops admission.
3. **Every launch goes through `resume` — the returned row, a local reservation and a retry
   alike.** `start` and `claim_capacity` authorize bounded **admission attempts** (claims),
   not launches. After any transition that lands a task in `reserved` — a fresh claim, a local
   reservation, a retry (`blocked`/`failed`/`cancelled → reserved`) or a preparation-failure
   return — the coordinator recomputes `select_admissions` and launches only the tasks
   `resume` lists, persisting `preparing` first. A claimed task that is not resumable (a
   conflict, an exclusive task waiting for quiet, no free seat) stays `reserved`, holds its
   row and waits; it is never prepared blind.
4. **Act on `start`, not on `claim_capacity`.** The capacity figure bounds new claims; it can
   exceed the number of tasks in `start`, and it never authorizes claiming a task the selector
   did not list.
5. **`drain` — stop admission, wait, integrate, publish, recompute.** While `drain` is true
   nothing starts and nothing resumes. A task's own `prepared → integrating` transition is the
   **durable admission latch**: it is persisted before the transport/commit effects and holds
   the run (any task, any kind, a restarted coordinator's unknown record included) until the
   integration transaction completes and the task is published — so the coordinator must
   persist it, then let the workers still `preparing`/`running`/`verifying/reviewing` reach
   safe return, execute the transaction, publish, and recompute. A sibling already at
   `prepared` is a safe return and parks; it is never relaunched, blocked or cancelled to make
   room. A pending *exclusive* integration (an exclusive task at `prepared`) additionally
   holds the run from admission to publication, exactly as before.
6. **Recompute after every transition.** The selector never mutates the manifest; every
   durable transition or publication — a recorded claim, a retry and a preparation-failure
   return included — is followed by a fresh call.

Capacity and ordering. `parallel.seats` bounds the workers (`preparing` or `running`), the
waiting reservations (`reserved`) and — doubled — every unresolved assignment (`reserved`,
`preparing`, `running`, `verifying/reviewing`, `prepared`, `integrating`). Launch seats are
reserved for `resume` before `start` is selected; a promotion frees a waiting reservation slot
without changing the unresolved count; combined resume and start never exceed the caps.
Conflicting reservations are serialized deterministically in document order: one is resumed,
the others wait and are re-checked after it is published. A pending exclusive reservation
stops new claims and starts but not the other reservations: the run drains to it and it
launches alone once no other unresolved work outside the waiting reservations is left —
`verifying/reviewing`, `prepared` and `integrating` included, so it never launches beside a
pending integration, while a passive waiting reservation does not hold it back and mutually
exclusive reservations still take turns.

Launch invariant. A resumed task is compatible with every active or un-integrated task and
with the other resumes selected with it; a newly admitted task is compatible with every
unresolved task, waiting reservations included; and two tasks are never launched together when
their declared paths overlap — equal, case-folded where the platform is not case-sensitive, or
related as a path-segment prefix (a directory/prefix declaration makes its task exclusive
instead of being fed to a glob engine). An exclusive task is launched alone: an unclaimed one
is admitted only when nothing is unresolved, an exclusive reservation only when no other
non-reserved unresolved work is left. Conflicting reservations are the one deliberate
exception to "every unresolved task is pairwise compatible": the withheld reservation is
re-checked on every call and cannot launch while a conflicting task is unresolved.

### 13.7 Task worktrees and the cleanup publication proof

`scripts/task_worktree.py` owns one detached worktree per task and the proof cleanup needs.
`create_worker(repo, assignment)` requires the repository-common lock of §13.4 to be held by
the assignment's run (read-only; it never acquires or reclaims), places the worker root
strictly inside the main checkout's `.opencode/worktrees/` area and no other worktree, and adds
a linked worktree detached at `assignment.base_commit`. The coordinator's branch, index, stash
and `.artel/run/` are never moved, stashed or switched.

The worker root carries `<worker_root>/.artel/parallel-worker.json` (role `worker`, the
validated assignment verbatim, the repository identity, the config digest and the setup state)
and a verbatim copy of the coordinator's validated `.artel/config.json`; the only evidence root
is `assignment.artifact_root`, which must be inside the worker root and git-ignored. The run's
namespace holds `refs/artel/parallel/<run_id>/<task>/<attempt>/base` (the exact base commit,
create-only) and `refs/artel/parallel/<run_id>/<task>/<attempt>/result` (the coordinator's
transport result). `setup.commands` run in the worker root, and a failure leaves the root and
the failing command, exit code and output tail in the marker.

`cleanup_worker(repo, assignment, publication)` removes a root only when the publication proves
its work durable and the root itself is clean. The proof is exactly:

```json
{"schema": 1, "ticket": "PROJ-123", "phase": 2, "run_id": "…", "task": "3.1", "attempt": 1,
 "cycle_commit": "<40hex>", "cycle_ref": "refs/heads/<branch>",
 "result_commit": "<40hex>", "result_ref": "refs/artel/parallel/<run_id>/<task>/<attempt>/result",
 "collected_root": "/abs/<repo>/.artel/run/PROJ-123/parallel/<run_id>/<task>-a<attempt>",
 "evidence": [{"path": "result.json", "sha256": "<64hex>"}], "published": true}
```

Every key is required and no unknown key is accepted. `result_ref` must be the run's canonical
ref and resolve to `result_commit`, which must descend from the base commit.
`cycle_ref` must be an existing `refs/heads/` branch — not a revision expression, not the
worker result namespace — under which `cycle_commit` is reachable; `cycle_commit` must exist,
descend from the base and never equal it. The `collected_root` must resolve strictly under
`<repo>/.artel/run/<ticket>/parallel/<run_id>/` (the attempt-scoped subdirectory is the
coordinator's convention), traverse no symlink, and overlap neither the worker root nor the
artifact root. Each evidence `{path, sha256}` is verified independently at the worker artifact
root and at `collected_root`: the artifact root must hold exactly the listed set, and the
collected copy must be a real separate copy — missing, changed, symlinked or hardlinked to the
worker file all refuse. Cleanup removes nothing until every one of these holds, the worker
`HEAD` equals `result_commit`, and every file under the root is accounted for: tracked at that
commit, one of the two lifecycle files, the verified evidence set, or the worktree's gitfile.
An ignored, untracked or hidden file refuses as `dirty_worktree` and is preserved byte for
byte; the module never resets, cleans or force-removes, and `git worktree remove` runs without
`--force`. The namespaced refs stay as the recoverable record.

The caller still owns the queue and artifact-store receipts, the semantic claim that the cycle
commit is the task's integration, and the collection step itself; the lifecycle machine-checks
the Git and evidence proofs available to it and refuses anything it cannot prove. In the
inspection report, state `published` only means "a result ref exists" — it is not §6's
`published` task state, which remains the coordinator's record.

A real host's worker root is not only the worker's own files: the bridge's hooks keep their
per-session state in the root's own `.artel/run/.hooks/` (`baseline-<session_id>.json` from
the verify layer, `stopblocks-<session_id>.json` from the stop gate; the run layer's
`.artel/run/<TICKET_ID>/.stop-gate-blocks` counter only exists in an armed root). Those files
are not part of the publication proof's accounted set, so cleanup refuses `dirty_worktree` and
preserves the root — by design (nothing is force-removed), but it means a root on a host with
the bridge active stays for the operator to inspect and remove. The refusal never undoes a
valid publication and never requires a relaunch. It is not a scan of the root either: the
accounting walk treats any traversal or read error as fatal (`scan_failed`), so a failed
directory scan can never authorize deleting content it did not see.

### 13.8 Serialized integration and recovery

`scripts/parallel_integration.py` owns the one integration transaction that mutates the ticket
branch, extending the `## Task commits` contract of the `artel-feature-development` skill to the
parallel path: one commit per completed worker cycle, the same staging discipline, the same
conventional subject-only message, no push. Its public interface is

    prepare_transport(repo, assignment, result) -> transaction
    apply_transport(repo, assignment, prepared) -> transaction
    commit_cycle(repo, transaction, subject) -> transaction
    recover_cycle(repo, transaction) -> transaction

`repo` is the **coordinator checkout**: the one whose branch carries the final commit and whose
`.artel/run/<TICKET_ID>/parallel/<run-id>/` holds the run manifest and the transaction record.
The module never schedules, launches, claims or publishes; the coordinator performs every
scheduler and queue call itself, and `resume` stays the only launch signal for reservations. It
never resets, stashes, switches or cleans either checkout.

**The transaction record.** One JSON-safe `schema: 1` record per task/attempt, persisted
atomically at
`<repo>/.artel/run/<TICKET_ID>/parallel/<run-id>/integration/<task>-a<attempt>.json` **before**
every Git side effect, with exactly these keys:

```json
{"schema": 1, "transaction_id": "artel-integrate-<run_id>-<task>-a<attempt>",
 "ticket": "PROJ-123", "phase": 2, "task": "3.1", "run_id": "…", "attempt": 1,
 "base_commit": "<40hex>", "worker_root": "/abs/worker", "repo_root": "/abs/coordinator",
 "branch": "feature/PROJ-123", "pre_head": "<40hex>", "transport_commit": "<40hex>|null",
 "result_ref": "refs/artel/parallel/<run_id>/<task>/<attempt>/result",
 "route": "light|full", "review": {"required": true, "route": "full", "verdict": "passed",
 "rounds": 1} | null, "changed_files": ["…"], "excluded": ["…"],
 "diff": {"base_commit": "<40hex>", "commit": "<40hex>", "sha256": "<64hex>", "files": ["…"]},
 "documents": ["…"], "drain": {"in_flight": [], "waiting": ["2.4"], "checked_at": "…"},
 "publication": null, "stage": "…", "subject": null, "cycle_commit": null,
 "verify": null, "recovery": null,
 "trees": {"transport": "<40hex>|null", "landing": "<40hex>|null", "integrity": null},
 "created_at": "…", "updated_at": "…", "record": "/abs/…"}
```

`stage` moves `preparing → prepared → applying → applied → committing → committed`; the side
stages `conflicted`, `apply_failed`, `failed`, `commit_refused` and `recovered` keep a refusal
inspectable. `documents` is the files path's pending tick/ordered notes (empty on the
artifact-store path); `drain.waiting` lists the parked safe-return work — waiting `reserved`
reservations and `prepared` siblings, which are never relaunched to make room; `publication` is
reserved for Task 9 and is never written here; `verify` is the integrated-tree gate envelope;
`recovery` is `{"state": "landed"|"none", "checked_at": "…"}`.

**Content identity — `trees`.** `trees.transport` pins the tree of the accepted transport
commit and `trees.landing` the exact tree the final cycle commit must carry; both are
persisted before the commit they bind, and `trees.integrity` is durable evidence
(`{stage, expected, actual, detail, checked_at}`) when a hook or gate changed either tree after
acceptance. A transport or final hook that rewrites or restages any path — the same allowed
name or a new one — produces a different tree and refuses (`hook_mutated`); a gate that writes
the working tree or the index refuses (`gate_mutated`) instead of silently restaging the
mutation so the green receipt describes an earlier tree. The hook-created commit, the index and
the working tree stay exactly as they are: nothing is reset, amended or rewritten, and the
transaction keeps the pinned tree plus the failure evidence for the operator or a new review.
Recovery validates the pin too: a transaction-marked commit whose tree differs from
`trees.landing` is refused (`integrity_failed`), so a crash after a bad hook commit but before
the post-commit check cannot let reflog parent recognition publish it; the branch, the
integration latch and the evidence are retained.
Every function returns exactly the record it persisted, so the coordinator persists the returned
value (or reads the file back) and passes it to the next step.

**Ownership and drain.** Every operation matches the repository-common ticket lock (§13.4)
read-only — `not_owned`, `ambiguous_owner`, `foreign_owner` refuse, and nothing here acquires,
releases or reclaims it. The worker root must be the assignment's own marker-validated root in
the same repository, and the run manifest at its conventional path must name this run and hold
the task at `integrating`. The coordinator persists that `prepared → integrating` transition
**before** polling for safe returns or creating any transport/commit: it is the scheduler's
durable admission latch (§13.6), so nothing new starts and no reservation resumes for the whole
transaction. Because the transport and the final commit run the host's normal commit hooks —
shared external effects the coordinator cannot prove worker-local — no sibling may still be
`preparing`, `running` or `verifying/reviewing`, and no other task may be `integrating`; a
non-drained run refuses (`drain_required`) and names them. `active` workers reaching zero alone
is not quiescence. A sibling already at `prepared` **is** a safe return: it parks, is recorded in
`drain.waiting` together with the waiting `reserved` reservations, and is never relaunched,
blocked, cancelled or published to make room. Once no `integrating` record remains, the normal
reviewed admission rules resume.

**Transport — `prepare_transport`.** The result must be `succeeded`, every worker check green and
the accepted review must cover the effective route of this exact diff/root/attempt; a submission
`review: null` is not an accepted review. The payload's `succeeded` is the result's own claim, not
native evidence: the worker session's disposition (Task 7's `workerStatus`, outside the result)
is the coordinator's precondition, and an interrupted session is never rescued by a contradictory
succeeded file. The coordinator creates a **normal hooked, code-only transport commit in the
worker tree**: the staged set is exactly the result's actual code diff computed from a
working-tree snapshot of the worker root against the recorded base, with both sides of a rename
included. Paths under `.artel/` and the ticket's spec trail are excluded (a
worker's shared-state edit stays dirty and inspectable, and the root is retained); every other
actual path must equal `changed_files`. A path outside the assignment's declared `files` must be
recorded in a deviation — a major deviation refuses (`major_deviation`), an unrecorded one
refuses (`undeclared_change`) — and it floors the effective route to `full`, so a light review
refuses (`route_floor`) instead of silently broadening the commit. A refusing commit hook leaves
the index, HEAD and the record as they were (`commit_refused`). On success the namespaced
`result` ref retains the transport commit, the worker is left clean at it, and `diff` records the
worker-scoped review package digest. A crash between the commit and the receipt is reconciled
from the transaction-marked worker reflog; an unexplained moved worker HEAD refuses
(`worker_not_at_base`) rather than adopting someone's commit.

**Apply — `apply_transport`.** The coordinator must be on the transaction's branch at its
recorded `pre_head` with a usable branch reflog and a **completely clean nonignored tree**: any
tracked, staged or untracked change — however unrelated to the task — refuses (`coordinator_dirty`)
before the mutation, preserving every byte, the index, the stash and HEAD; nothing is reset,
cleaned, stashed or staged to pass the check, and a person's file is never swept in as a document.
The host's `.artel/run/` ignore rule ([config.md](config.md), "Where it lives") is what keeps the
run's own manifest and integration records out of that check — a checkout that tracks them reads
as dirty and stops integration until the ignore rule is in place.
The transport is applied with **`cherry-pick --no-commit`**: it stages the change and adds no
commit to branch history. A conflict leaves git's own conflict state and a `conflicted` record —
nothing is auto-resolved, reset or force-cleaned — and stops admission until the coordinator's
bounded fix/review procedure resolves it. The pre-HEAD and the transaction id are recorded before
the mutation, and an interrupted apply is adopted only while the index still matches the
transported paths exactly (`ambiguous_apply` otherwise). The coordinator writes the files-path
tick/ordered notes **after** a successful apply, never before, and lists them in `documents`.

**Final commit — `commit_cycle`.** Before anything is committed, the tree is checked twice. The
index may carry only the transported paths and the transaction's `documents` (`foreign_staged`);
and the whole nonignored working tree may carry nothing outside those two sets
(`coordinator_dirty`) — including a change introduced after a successful apply. The staged tree
is then pinned (`trees.landing`) and the integrated tree meets the existing task gate
(`scripts/verify.py task`, run in the coordinator root) and the normal commit hooks, so the gate
and the hooks always see **exactly the tree that can land**: two individually green task roots do
not establish combined correctness, an unrelated WIP file can neither fake that gate green nor
block a good integration, and a red gate (`task_gate_failed`) or an environment error
(`task_gate_error`) commits nothing and leaves the staged tree inspectable for the coordinator's
bounded fix. The gate's scope is the **deduplicated union of the transported changed paths and
the assignment's declared `Test:` paths** — read from the validated worker marker, so an
unchanged declared test still runs on the combined tree and cannot be skipped just because the
integration did not touch it; the gate's own generated/surface/missing-path rules are unchanged.
A gate or hook that changes the index or the working tree after the pin refuses
(`gate_mutated`/`hook_mutated`, §13.8 `trees`) and is never restaged to make an earlier review
apply. A fix stays inside the transported paths; a new path is a new attempt. A person's staged
or dirty work is never swept in, never unstaged and never reset. On the **files path** the tick
and ordered notes are the pending `documents` and land in this one commit with the code; on the
**artifact-store path** `documents` is empty, the code commit lands first, and Task 9 publishes
the versioned artifacts afterwards — never the reverse. The subject must be a conventional, subject-only message (`feat`, `fix`,
`refactor`, `test`, `docs` or `chore`) with no task id and no trailer, and the commit carries the
transaction id as its `GIT_REFLOG_ACTION`, so it is attributable from the reflog, the retained
refs and the run
state rather than from its message. There is exactly one completed-cycle commit on the ticket
branch per task/attempt; the transport commit stays a retained ref, not branch history. A
refusing final hook leaves the record and the staged tree as they were (`commit_refused`).

**Recovery — `recover_cycle`.** A final commit may have landed before its receipt was persisted.
Recovery matches the recorded `pre_head` plus the branch reflog entry whose message is the
transaction id exactly (also when the coordinator itself is a linked checkout) and returns that
commit exactly once; `commit_cycle` and `prepare_transport` run the same recognition first, so a
repeated call never creates a second transport or cycle commit. Missing or unusable reflogs
(`reflogs_unusable`), an expired window (`expired_evidence`), evidence that carries the marker but
does not match the pre-HEAD (`ambiguous_evidence`) and a matching commit no longer reachable from
the branch (`unreachable_evidence`) each **refuses rather than re-committing** — the diagnosis is
the coordinator's, and nothing is reset or guessed.

**Caller obligations for Tasks 9-10.** Integration produces the Git facts Task 9 consumes — the
final `cycle_commit`, its branch (`cycle_ref`), the retained `result_ref`/`result_commit` and the
record — and never asserts an external publication or cleanup receipt. The exact ordering is:
persist `prepared → integrating` (revision-checked) first — that record is the admission latch;
then make sure no sibling is still `preparing`/`running`/`verifying/reviewing` or `integrating`
(a `prepared` or `reserved` sibling parks and is left alone); then run the transaction on a
completely clean coordinator — if the person has work in progress, the coordinator stops and asks
before anything is integrated, and the transport commit already created stays retained for the
retry; write the files-path tick/ordered notes only after a successful apply and list them in
`documents`; run the bounded fix/review procedure after a red gate or a conflict; call
`recover_cycle` after a restart before retrying anything; and only after the record reaches
`committed` (or `recovered`) does Task 9 publish and the queue mark the task done. Normal
admission resumes once no `integrating` record remains. §13.9 is the coordinator's publication
loop: plan with `next_publication_actions`, persist each intent, execute the effect through the
configured store/queue adapter, and record the receipt with `record_publication` until it returns
the promoted manifest.

### 13.9 Restartable completion publication

`scripts/parallel_publication.py` owns the last step of a worker cycle: everything between a
`committed` integration record and the durable `published` task state a dependent may consume.
Its public interface is

    next_publication_actions(manifest, task, observed) -> [action, ...]
    record_publication(manifest, task, action_id, receipt) -> {manifest, publication}

Both are pure reducers: they never call the artifact store, the queue, a session or Git, and
they never schedule. The configured coordinator performs every external call and persists what
they return — the publication record into the integration record's reserved `publication` field
(§13.8), the returned manifest through the revision-checked `parallel_state.write_manifest`.

**The observed bundle.** `next_publication_actions` answers from the stored manifest and one
fresh observation bundle the coordinator assembles: the `committed`/`recovered` transaction
record, the native worker `lifecycle` disposition (`succeeded` is required — a worker
completion, a result ref or the code commit alone is never publication), the configured
`store` mode with the pending tasklist tick and the accepted deviation proposals, the fresh
observation of the run's own recorded `queue` row, and the worker `evidence` files with the
coordinator's `collected_root` copy. A record that is not `committed`/`recovered`
(`not_committed`), a foreign or incomplete identity, a review that does not cover the effective
route, a missing green integrated-tree gate and a non-`succeeded` disposition all refuse before
any action is planned.

**Ordering.** The plan is fixed. On the **files path** the tick and ordered notes are already in
the final cycle commit (Task 8's `documents`): the reducer recognizes that committed effect
(`committed-documents`) and never emits a post-commit file write: it never produces a file
tick after the fact and never a second completion commit. On the **artifact-store path** the
accepted code
commit lands first (`documents` empty) and the reducer plans the expected-version tasklist tick
and one numbered implementation note per accepted proposal, in proposal order. Both paths then
collect the worker evidence into the coordinator's task/attempt-scoped `collected_root`, and
queue done is last of the external effects: the owned row is completed only after the committed
code, the store patches and the collected evidence are durable. Only after every required receipt is
durable does the manifest move `integrating → published` — the single transition the scheduler
treats as complete, so dependents move only then, and the `integrating` latch holds through
every partial publication.

**Actions, intents and receipts.** Each action carries a stable
`artel-publish-<run_id>-<task>-a<attempt>-<suffix>` id (suffix `documents`, `tick`,
`note-<proposal index>`, `collect`, `queue`, `promote`), the transaction identity, the full
ordered plan and its kind-specific payload (`committed-documents` paths, `store-tick`
expected_version, `note-append` number and key, `collect-evidence` roots and entries,
`queue-done` the recorded owned row, `promote` nothing). An action whose effect the fresh
observation already proves — a tick already present, a note key already numbered, collected
copies already matching, the owned row already `done` — is returned as `observed`: the
coordinator records its receipt and never repeats the effect. The coordinator persists each
action's intent (`state: planned`) **before** the effect and its confirmed receipt
(`applied`/`observed`) after; an identical repeat is idempotent, a conflicting one is
`conflicting_receipt`, and a receipt without its intent, out of plan order, or naming another
task/attempt/transaction is refused. A store action whose durable intent's base moved is
re-based on the fresh version and re-applied once — the write still carries the fresh
`expected_version`, so an unrelated concurrent edit is never overwritten or dropped, and a
re-base may never move the expected version backwards (`intent_conflict`); a note renumbering
over a concurrent append takes the next free number and consumes it once. A queue row observed
under a foreign actor, a changed row id or task, a missing row, or a status that is neither
`in_progress` nor `done` is never completed (`foreign_row`, `row_changed`, `row_missing`,
`row_not_in_progress`); a queue-free (local) run keeps the same lifecycle with no queue action.
A **receipted** queue completion is rechecked like the store kinds: before any promotion the
fresh observation must still show the exact receipted row, actor and `done` status. A row that
was reopened (`in_progress` again), reacquired by another actor, replaced or deleted refuses
(`queue_conflict`; a foreign or reacquired holder is refused earlier as `foreign_row`) and the
integration latch is retained — the old receipt never silently re-completes a row that is no
longer done.

**Completion and cleanup.** When the last required receipt lands, the reducer marks the record
`complete`, builds the §13.7 cleanup publication proof — `cycle_commit` under
`refs/heads/<branch>`, the retained `result_ref`/`result_commit` (the transport commit), the
task/attempt-scoped `collected_root` and the collected evidence digests — and returns the
promoted manifest. A receipt proves an effect landed, not that it is still there: before the
publication may finish, the fresh observation must still carry every receipted effect. A
forward store version that drops an already-receipted tick, or a receipted note key that is
gone or renumbered, refuses (`conflicting_receipt`, or `note_conflict` for the exact renumber
conflict) and the reducer never silently re-applies the deletion, overwrites the concurrent
edit, removes the store's history or allocates a new note number; a benign forward edit that
still carries the tick and every note key at its recorded number continues. That re-check runs
for a complete record too: a complete record whose manifest write was lost returns its single
`promote` action only after the same fresh receipt/content/evidence checks, so a reverted
effect is never mistaken for completion — "after completion" is the manifest's `published`
state, not the record's `complete` flag. The cleanup itself stays a later, conservative
lifecycle operation: it re-verifies the real copies and refuses a dirty or unknown root, and
such a refusal never undoes a valid publication or requires a relaunch. Evidence collection is
a publication prerequisite, not a cleanup one. A lost reply is reconciled from fresh
observation, a refusal preserves the durable record, the effects already done and the
`integrating` latch exactly as they were, and the `promote` receipt re-checks the durable
record instead of asserting anything new.

**Caller trust.** `record_publication` receives no observation bundle, so it cannot re-verify
the planning prerequisites: a receipt must name an action `next_publication_actions` returned
for the current fresh observation, and the trusted coordinator plans fresh before every
effect. A hand-made action with plausible identity fields is not checked against the queue row
or the store content there.

### 13.10 The coordinator CLI

`scripts/parallel.py` is the coordinator's one entry point into the helpers above. It is an
explicit argparse dispatcher over a JSON request — one command per operation, each routing to
exactly one helper function. It never evaluates a function name or arbitrary code, never
duplicates a reducer's policy and never talks to the store, the queue, a session or a tracker;
those stay the configured coordinator's calls, through its own tools.

    python3 scripts/parallel.py <command...> --request <JSON-file|->

Request keys are the selected helper's argument names plus the explicit run context a
mutation needs: the coordinator checkout (`repo`), the run identity (`ticket`, `run_id`),
`expected_revision` for manifest writes, and — on **every mutating command** — the caller's
`coordinator_id` (the native session id from `artel_parallel_coordinatorIdentity`). The
commands:

| Command | Request | Effect |
|---|---|---|
| `config` | `{repo}` | Reads `<repo>/.artel/config.json` and resolves `parallel.seats`; absent or `1` is the serial default. |
| `preflight` | `{repo, capabilities}` | Consumes the adapter's read-only capability report and verifies the coordinator's branch, cleanliness and reflog before any claim or worktree. Read-only. |
| `owner acquire` / `owner recover` / `owner release` | `{repo, ticket, owner}` / `{repo, ticket, run_id, phase, coordinator_id, reason}` / `{repo, ticket, owner}` | The repository-common ticket lock. `acquire` is idempotent for the same run and coordinator; `recover` is the explicit audited handoff that rotates the token and the coordinator (and adopts a legacy record); `release` requires the exact run's manifest quiescent (every task published, no latched stop, no unfinished owned claim, no unconfirmed orphan release) and its run state `completed: true` and unpaused, then the matching token and coordinator. |
| `run create` / `run inspect` / `run transition` | `{repo, ticket, phase, run_id, seats, graph_digest, coordinator_id}` / `{repo, ticket, run_id}` / `{repo, ticket, run_id, expected_revision, task, expected, target, fields?, coordinator_id}` | The run manifest; `inspect` is a pure read. |
| `claim prepare` / `claim record` / `claim reconcile` | `{repo, ticket, run_id, expected_revision, coordinator_id, intent_id?, row?, rows?}` | The claim intent before the queue call, the recorded returned row, and reconciliation against fresh rows (an orphan becomes `released` only when a fresh complete observation confirms it). |
| `admit` | `{repo, ticket, run_id, tasklist, case_sensitive?}` | The pure admission selector over the parsed tasklist; only the current unfinished iteration is admitted. Without the optional override, case sensitivity is read from the actual volume that holds the checkout, never from `normcase`'s POSIX string behavior; an unknown volume is treated conservatively as case-insensitive and NFC/NFD aliases conflict. Read-only. |
| `worktree create` / `worktree inspect` / `worktree cleanup` | `{repo, assignment, coordinator_id}` / `{repo, assignment}` / `{repo, assignment, publication, coordinator_id}` | The per-task worker worktree lifecycle. |
| `integration prepare` / `integration apply` / `integration commit` / `integration recover` | `{repo, assignment, result, coordinator_id}` / `{repo, assignment, prepared, coordinator_id}` / `{repo, transaction, subject, coordinator_id}` / `{repo, transaction, coordinator_id}` | The serialized integration transaction. |
| `publication next` / `publication record` | `{repo, ticket, run_id, task, observed}` / `{repo, transaction, task, action_id, receipt, coordinator_id}` | The publication plan and one durable intent/receipt. |
| `budget inspect` / `budget reserve` | `{repo, ticket, phase}` / `{repo, ticket, run_id, phase, kind, scope, attempt, ordinal, expected_revision, coordinator_id}` | The run-wide correction budget (§13.12). |

Every command answers with the deterministic-script envelope — `{ok, verb, elapsed_ms,
data}` or `{ok, verb, elapsed_ms, error: {kind, message}}` — and exits `0` on success, exit `1`
for a blocked outcome (state, ownership, admission, capability, ordering or evidence), and
exit `2` for invalid input or an environment failure. The exit code is a classification of
the refusal kind, never a new decision: a `start`, `resume`, `drain` or stop action is data the
coordinator acts on, not an error.

**Authoritative mutations.** Reads are side-effect-free. Every mutating command runs inside
`parallel_state.ticket_guard(repo, ticket)` (§13.4), the repository-common kernel guard shared
with the native adapter: inside it the lock is matched to the exact run/phase **and**
coordinator (`not_owned`, `legacy_owner`, `foreign_owner`, `coordinator_conflict`) and the
guard stays held through the whole effect. Every manifest write resolves the run's canonical
path from the explicit coordinator checkout and reads the manifest fresh: a caller-supplied
copy is never trusted for an effect, and `expected_revision` must equal the revision on disk
(`stale_revision`) before any reducer runs — a stale caller or a foreign revision fails before
a Git or setup effect, and a refusal leaves the bytes exactly as they were. The manifest write
itself serializes its compare-and-replace under a per-manifest lock, so two concurrent callers
cannot both pass the revision check and lose one update. `publication record` reads the durable integration record and
requires the caller's copy to be exactly it: a stale caller is `stale_publication`, never a
silent clobber of a newer record. It then persists the returned publication record into the
transaction's reserved `publication` field **first** — through the reviewed, ownership- and
drain-checked writer — and only then writes the promoted manifest, revision-checked, when the
reducer changed it. A lost manifest write is recovered by the next fresh `promote`, not by
repeating an external effect. `publication record` also runs the same lock match
unconditionally before its reducer, on every path — including the pure promote whose
publication is unchanged. `owner release` is gated by the CLI itself: the exact run's manifest
must prove quiescence (every task published, no latched stop, no unfinished owned claim) and
the matching run state must say `completed: true` without a pause before the token proof
removes the lock — the caller's own `completed`/`quiescent` bytes are an assertion, never the
proof.

**Caller trust.** A request's pure planning inputs (the tasklist, the observed bundle, a row)
are caller-supplied; its effects are not. `record_publication` still trusts the coordinator's
fresh observation, and a hand-made action is not re-verified against the queue or the store.

### 13.11 The inline coordinator loop

The loop is one opt-in procedure, inline in the current OpenCode session — never an outer
orchestrator, never a subagent-driven branch, and never a second scheduler in TypeScript or in prose.
`parallel.seats` is worker concurrency; `artel-feature-development` runs the tail inline. `parallel.seats`
absent or `1` keeps the serial procedure exactly as it is; the parallel path is never silently
pretended to be enabled. The canonical procedure lives at
`skills/artel-feature-development/parallel.md`: the shared start lists it, and the canonical
tail carries the opt-in gate that reads it.

    preflight/lock/reconcile -> claim/admit -> launch/observe -> review/integrate/publish -> repeat

1. **Preflight, lock, reconcile.** The capability report, the coordinator branch/cleanliness/
   reflog preflight, `owner acquire`, then `run create` or `run inspect` — and a full
   reconciliation of claims, integration records, native sessions, refs and publications
   before any new launch.
2. **Claim/admit.** `admit` answers `start`, `resume`, `claim_capacity` and `drain` fresh
   after every durable transition. `start` authorizes bounded claims through the queue's
   atomic operation; the returned row decides the task. A task that lands `reserved` is
   launched only when a fresh `resume` lists it, and `run transition` must persist `preparing`
   before any worktree, session or prompt. `drain` stops new admission until the integration
   and publication complete.
3. **Launch/observe.** One pinned assignment per task: a lifecycle worktree, a concrete
   resolved model (never an inherit, never a model tier alias), then the
   `artel_parallel_startWorker` adapter. Status comes from `artel_parallel_workerStatus` —
   durable state, never the volatile event stream — and an `interrupted` outcome overrides a
   contradictory succeeded result. The review is coordinator-owned at that worker's exact
   diff, root and attempt; fix lists stay serial and every fix round is reserved from the
   run-wide budget before its dispatch (§13.12), never counted from zero after a resume.
4. **Integrate/publish.** Persisting `prepared -> integrating` is the durable admission latch:
   siblings still returning reach safe return, a `prepared` or `reserved` sibling parks, and
   nothing is relaunched, blocked or cancelled to make room. Then the transport commit in the
   worker, `cherry-pick --no-commit` on a completely clean coordinator, the files-path
   tick/notes after a successful apply, one normal final commit, and the §13.9 publication
   loop until the task is durably `published` and dependents may move.
5. **Pauses, drain, cancel.** A HITL pause, a deviation escalation, a failure or an exhausted
   budget stops new admission, lets unaffected workers reach safe return, retains every
   reservation, worktree, ref, record and the ticket ownership, and lets only the coordinator
   ask the person. Stop/cancel follows the real transitions (`cancelled` with a recorded
   decision, `artel_parallel_interruptWorker` for a running session) — never an invented key.
6. **Finish.** The final whole-tree review, runtime gate and checkpoint run only when the run
   is quiescent: no task active or `integrating`, every required publication durable. `owner release`
   happens only for the matching, completed, quiescent run — the CLI re-checks the exact run's
   manifest quiescence and its run state's `completed` flag before the token proof removes the
   lock; a pause retains the lock and the recovery state.

### 13.12 The run-wide correction budget

Every correction round the parallel loop dispatches is reserved from the **existing**
authoritative budget: `counters.correction_rounds` in the coordinator's
`<repo>/.artel/run/<TICKET_ID>/run-state.json` ([autonomous-run.md](autonomous-run.md) §2, §5).
There is no second ledger, no manifest key and no worker payload field;
`scripts/parallel_budget.py` is the only writer, reached through the CLI:

- `budget inspect` — `{repo, ticket, phase}`: the current `correction_rounds`, the run-wide cap
  (`MAX_TOTAL_CORRECTION_ROUNDS = 8`), the remaining rounds, the reservation `revision`, every
  recorded reservation and the canonical per-scope caps the coordinator reconciles against.
  Read-only; a missing, unreadable, non-matching or not-armed run state refuses
  `missing_run_state`, `corrupt_run_state`, `run_state_mismatch` or `run_not_active` and is
  never created, reset or overwritten.
- `budget reserve` — `{repo, ticket, run_id, phase, kind, scope, attempt, ordinal,
  expected_revision}`: one durable reservation before a fix dispatch. Like every mutation, it
  is a ticket-owned operation: the repository-common lock must match the exact run
  (`not_owned`, `ambiguous_owner`, `foreign_owner`) before the helper runs — the transient
  `<run-state>.lock` below serializes budget writers only and is not ticket ownership — and a
  malformed request still fails as invalid input first. The **stable key** is
  `<kind>:<scope>:a<attempt>:r<ordinal>` — the fix-list source (`review`, `runtime`,
  `checkpoint`), the gate scope (a task number, `ticket` or `phase-<N>`), the worker attempt
  and the true round ordinal of that scope's canonical artifact; a task and attempt alone never
  identify a round. The exact same identity and key replays for free (`replayed: true`), a new
  identity or round charges the counter once, `expected_revision` is the write guard
  (`stale_revision` before any write), the per-scope cap refuses a fabricated extra round
  (`scope_cap`) and the run-wide cap refuses any new round once the counter reaches 8
  (`budget_exhausted`) — both before the write, with every byte preserved. There is no reset,
  decrement or recredit verb.

The counter and the reservation block (`counters.correction_reservations`,
`{schema, revision, rounds}`) are written atomically — a temp sibling and `os.replace` — under
an exclusive transient lock: a separate `<run-state>.lock` file locked with `fcntl.flock`,
never taken over and released by the kernel on process exit, so a crashed coordinator leaves no
stale lock. Concurrent reservations serialize without losing an increment, and every unrelated
run-state field and nonzero counter is preserved. A crash before the write costs nothing; a
lost reply replays the same reservation for free; a reserved-but-undispatched round
conservatively stays charged until the same identity resumes.

Only an actual fix dispatch is charged: never the initial plan review or a worker's own
execution. The scope caps are the canonical workflow defaults ([autonomous-run.md](autonomous-run.md)
§5) — `MAX_TASK_REVIEW_ROUNDS = 1` per worker task review, `MAX_REVIEW_ROUNDS = 3` per
ticket/phase `review.md` artifact, `MAX_RUNTIME_RETRIES = 1` and
`MAX_CHECKPOINT_VERIFY_ROUNDS = 2` — while the run-wide `MAX_TOTAL_CORRECTION_ROUNDS = 8` is
shared by every scope. On resume the coordinator inspects fresh and reconciles the canonical
artifact round (the task review's journal entry, `review.md`'s `**Review round:**`,
`runtime-observation.md`, the checkpoint entry) against the recorded reservations before every
dispatch; it never starts from zero.

## 14. Proof record

The capability proof ran 2026-10-15 against OpenCode 2.0.29: two workers in two detached
worktrees of an auto-cleaned scratch host, with the barrier, interrupt, missed-event,
permission, role/location and guard evidence described above. The raw evidence — session
ids, event captures, hook state and probe scripts — is host-owned scratch under
`.artel/run/repro/opencode-parallel-proof/` and is not committed; the version and interface
claims in this file come from that proof and from the installed schema
(`opencode api get /openapi.json`).

**Task 7 adapter run (2026-10-15).** The same existing service, through the newly registered
adapter and no global change: a fresh scratch host carried `.opencode/plugins/artel-parallel.ts`
(the repo's `parallel.ts` plus a standalone definition — the bridge registers the same module
in a real install), the service discovered it per location without a restart, and a
coordinator session called the tools for real. Recorded: `workerCapabilities` returned
`ok: true` for 2.0.29 with the full client surface; `startWorker` created and prompted two
workers in their lifecycle worktrees (both running at the same time, `session/active` naming
both) with the launch journal written before each session and `prompted` after each prompt;
`interruptWorker` interrupted one mid-turn (`{"interrupted": true}`) and `workerStatus`
recovered both durably after a deliberately missed event window — the reconnected capture
carries no worker terminal event while the status envelopes recovered the succeeded worker's
validated result and the interrupted worker's contradictory `succeeded` result as
`interrupted_over_result`; a worker session calling `startWorker` was refused
(`caller_is_worker`) and wrote no journal, so the adapter namespace is not a worker
orchestrator. Raw evidence: `.artel/run/repro/opencode-parallel-proof/task7/native-evidence/`.

**Fix round 1 (review findings, 2026-10-15).** The worker `subagent`/`question` denial and the
bridge-registration path are now proven natively on the same service, still with no global
install and no restart: a scratch-local `.opencode/opencode.json` (`plugins: ["-artel"]`)
disabled the obsolete globally installed bridge for the disposable scratch locations only,
and one distinct-id test wrapper (`artel-fix1`) imported the EXACT repo
`opencode/plugin/artel.ts` — and, through its own `./parallel` import, the repo adapter — so
`registerParallel` and the worker guard executed as shipped. Recorded: per-location plugin
lists with exactly one active current bridge and no old `artel` or failed entry (host and both
lifecycle worker roots); `workerCapabilities` `ok` for 2.0.29; `startWorker` created and
prompted two workers in their lifecycle worktrees (fresh creation, `adopted: false`, both
running); `interruptWorker` interrupted one mid-turn (`already_interrupted: false`);
`workerStatus` reported the completed worker's validated result and the interrupted worker's
contradictory result as `interrupted_over_result`; and in a worker session the `subagent`,
`question` and `artel_parallel_startWorker` calls were each denied pre-execution by the same
current `tool.execute.before` hook with the exact `artel worker role` error — no child
session, no pending form, no journal. Deviations disclosed: the test plugin id differs from
the shipped `artel`, and the scratch wrapper pointed `ARTEL_ROOT` at the checkout for its
module evaluation only (the globally installed copy predates this task; the process value is
restored immediately). Raw evidence: `.artel/run/repro/opencode-parallel-proof/task7/fix1-native-preflight/`
and `.../fix1-native-run/`.

**Task 12 real-host fixture (2026-10-16).** A disposable scratch host carried a real
file-backed ticket (`PROOF-12`, phase 1) with a three-task tasklist — 1.1 and 1.2 disjoint,
1.3 depending on both — a configured `setup.commands` codegen step (a real generator plus a
baseline test run, ~0.3–0.5 s per worker root), configured `verify.test`/`verify.testSurface`
gate commands, and the scratch-local per-instance bridge wrapper (a distinct plugin id
building one `createArtelPlugin({root})` instance from the exact checkout, with the obsolete
global `artel` disabled for the location only). The run drove the documented coordinator CLI
(`scripts/parallel.py`); a native coordinator session performed every adapter call
(`workerCapabilities`, `startWorker`, `workerStatus`), native worker sessions
(`tokenguard/deepseek-flash`, agent `artel-implementer`) implemented the tasks, and native
reviewer sessions (`tokenguard/glm-5.3`) produced the task-review verdicts. This is
**script-assisted CLI coordination** with native sessions for the adapter and the reviews —
not the generated inline coordinator procedure (§13.11), which no model drove here, and not
the `artel-run-reviewer` skill.

Recorded at `seats: 2`: two workers ran concurrently (both in `session/active` at once, and
the event stream shows both `session.execution.started` before either `succeeded`), each
wrote real code and tests and ran the scoped task gate green in its own worktree; the
coordinator accepted the reviews (1.1 full; 1.2 route-floored to full by its recorded minor
deviation on an undeclared path); 1.1's integration parked 1.2 at `prepared` (`drain.waiting`
names it) and committed code plus the files-path tick; then the run **paused** — run-state
`pause_reason` set, lock retained, 1.3 absent from the manifest with no worktree and no
journal — and a **new process** with a **new coordinator session** rediscovered the run from
disk, reconciled both worker dispositions with `workerStatus`, integrated and published 1.2,
and only then admitted 1.3, whose base commit is 1.2's cycle commit. Exactly one cycle commit
per task landed on the ticket branch (three, plus the fixture seed), each carrying the code
plus the tasklist tick (and 1.2's ordered note); `integration recover` recognized each landed
commit without a second commit; the publication plan ran committed-documents →
collect-evidence → promote per task; the lock was released only after `completed` and
quiescent. A cleanup attempt was refused `dirty_worktree` — the bridge's own hook-state files
in the worker root are not part of the accounted set (§13.7) — and the root was preserved.
The bridge's stop gate ran in every worker root (each left its `stopblocks` counter) and
passed: a worker root carries no armed run state, which is the shipped answer to the
armed-stop-gate question of §12.

Raw times (one sample each, wall-clock ms; one synthetic fixture, not a benchmark). The
**column total** row sums the per-task cells: it is not run active wall time (that is the
phase clocks below) and not a benchmark figure:

| Task | setup | worker | review | integration | publication |
|---|---|---|---|---|---|
| 1.1 (full) | 460 | 48428 | 12550 | 1570 | 598 |
| 1.2 (full, route floor) | 401 | 96905 | 5954 | 1367 | 600 |
| 1.3 (light) | 300 | 55462 | — (skipped) | 1891 | 671 |
| **column total** | 1161 | 200795 | 18504 | 4828 | 1869 |

Phase wall clocks: phase A (claim → pause) 205.3 s, phase B (resume → release) 86.5 s; the
pause gap was 86.6 s of driver time outside the run. Operator interventions inside the run:
0.

The `seats: 1` sample ran the same three tasks, prompts, models and CLI path one at a time
(the parallel path capped at one seat, not the shipped serial procedure): active total
297.5 s; worker 161003 + 38856 + 33141 ms; setup 1044 ms; review 19708 ms; integration
4822 ms; publication 2268 ms; 0 interventions; three cycle commits. The measured difference
(291.8 s vs 297.5 s) is **not** a speedup claim: on one sample the model's own turn latency
dominates (the same task 1.1 took 48.4 s at seats 2 and 161.0 s at seats 1), the overlap is
real but small against that variance, and nothing here is a production benchmark. What the
sample establishes is that both seat counts close the same boundary with the same artifacts.

Limits, not claims: local file-backed mode only — no remote artifact store or queue adapter
was exercised live (its deterministic coverage is not a live proof); the 3.11/3.13 matrix
runs were retired by the 2026-10-19 testing-policy decision (latest Python only; 3.9 and
3.14 were green at 2,981 tests); the default globally installed plugin path was not exercised
(the fixture used the per-instance wrapper); the generated inline coordinator procedure was
not driven by a model; and the **expensive-setup/codegen host case is NOT PROVEN** — the
fixture's configured setup is real generation work (a generator plus a baseline test run)
measured at ~0.3–0.5 s per worker root, which is not an expensive-setup host and cannot show
per-task worktree setup multiplication when setup cost is significant. The real generator
measurement stands as measured; the expensive case is not claimed, and the expensive-setup
criterion is neither waived nor replaced by the cheaper case that was measured. Raw evidence:
`.artel/run/repro/opencode-parallel-proof/task12/` (host-owned, not committed).

**Capability re-proof on 2.0.32 (2026-10-19).** The installed service upgraded to 2.0.32,
which the gate refuses by design (`unsupported_version`). The native gate was re-run against
the installed version with the current source and a scratch install root whose only difference
was `PROVEN_VERSION = '2.0.32'` — no global install, no service restart and no wrapper in the
user's environment. Recorded: `workerCapabilities` returned `ok: true` for 2.0.32 with the
full client surface; two pinned `artel-implementer` workers started in their detached
worktrees (`ses_67f65af14f5b0cbd38c9128258`, `ses_92d874dc8199dc6db255acb305`), both
`state: running` at the same time, and both completed with validated `succeeded` results whose
identity matched their assignments; `interruptWorker` interrupted a third worker
(`{"interrupted": true}`) and `workerStatus` reported `outcome: interrupted`, `success: false`;
a fresh private server recovered the durable status of sessions created by an earlier process
(no event replay); and in a worker session the `artel_parallel_startWorker` and `subagent`
calls were each denied pre-execution by the worker-role guard with the exact `artel worker
role` error. Raw evidence: host-owned scratch under the scratch host's `.artel/run/` (not
committed). Limits: the re-proof exercised the adapter transport and role guard, not a second
full model-driven coordinator run; the expensive-setup host, live remote adapters and the
default global-install path remain unproven.
