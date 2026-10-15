# OpenCode parallel workers — frozen contract

*Status: v0.1 · 2026-10-15 · proven on OpenCode 2.0.29*

Sub-project 2c runs one worker session per task on the OpenCode host. This file is the
contract the native capability gate froze: it fixes the session interfaces the adapter
uses, the worker's role, permissions and location, the assignment and result `schema: 1`, the
task states and their transitions, integration ownership, the concurrency metadata, and what
happens when a capability is missing. It records a contract, not shipped behavior: the
schema, run-state and claim helpers exist (`scripts/parallel_contract.py`,
`scripts/parallel_state.py`, `scripts/parallel_queue.py`), no coordinator, scheduler or
worker adapter drives them yet, and `parallel.seats` absent or `1` keeps the serial
procedure exactly as it is (a boolean is not an integer and fails configuration
validation).

Proven against **OpenCode 2.0.29** only. The names below are the installed v2 HTTP API and
event stream, retrieved through `opencode api get /openapi.json`; capability coverage is
exactly the proven version, not every V2 release, so a version change re-runs the gate before
parallel mode is enabled.

## 1. What the proof established

The gate ran two real worker sessions concurrently in two detached per-task worktrees of one
scratch host, each with an explicit location, agent and model, and both blocked on a scratch
barrier until the observer had recorded `started == {A, B}` and `completed == {}` from the
event stream; only then were they released. Both made a distinct file edit and ran an
ordinary task check, and the existing bridge hooks ran in each worker's own root.

Demonstrated natively on 2.0.29: explicit `location`/`agent`/`model` on session create;
concurrent sessions with per-session event streams; `session.execution.started` and
`session.execution.succeeded`; interrupt with a durable `interrupted` outcome; durable status
and message recovery after a deliberately missed event window (the stream has no replay);
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

`opencode/plugin/parallel.ts` registers four tools in the namespace `artel.parallel` (exposed
ids `artel_parallel_workerCapabilities`, `artel_parallel_startWorker`,
`artel_parallel_workerStatus`, `artel_parallel_interruptWorker`) through the existing bridge
(`opencode/plugin/artel.ts`). The tools are the native transport only: every decision —
request validation, the capability gate, the launch journal, session verification, result
recovery, interruption semantics — lives in the policy half `scripts/parallel_launch.py`,
which reuses `parallel_contract.py`, `hook_common.py` and `task_worktree.py`'s contracts
instead of restating them.

**Capabilities.** `workerCapabilities` is read-only and reports the installed version and the
plugin-client surface the adapter needs. `startWorker` re-checks that report first: anything
but exactly 2.0.29 with the full surface is refused (`unsupported_version` /
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
a second session or double-prompting. A wrong session (id, metadata or parent), root, agent,
model or permission ruleset is refused, never adopted; a malformed journal is refused, never
overwritten; a null or unresolvable model is refused (`unresolved_model`) and never inherited
(§9).

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

## 12. Capability failure, unproven items and non-native protections

Parallel mode fails preflight: before any production claim or worktree is created, the
coordinator checks the running service against this contract (the version and the interface
set in §2). A missing or changed capability stops the parallel path and keeps the serial
procedure — parallel mode is never silently pretended to be enabled, and a changed OpenCode
version re-runs the gate.

Unproven on 2.0.29, and therefore not relied on without re-verification:

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

### Not native — worker-mode protections (Task 6)

The proof demonstrated the native session mechanics only; none of these protections ship
today:

- **No-orchestrator role guard.** Nothing native stops a worker session from dispatching
  subagents, creating sessions or otherwise acting as a second orchestrator; the base policy
  allows the `subagent` action unless a permission rule denies it. The implementation must
  enforce the worker role — workers execute the pinned assignment and never orchestrate — and
  deny orchestration actions in worker sessions.
- **Armed stop-gate interaction.** The existing bridge re-prompts a finished root session
  while the host run is armed (`run_active: true`, `completed: false`, `pause_reason: null`),
  so a worker that stops under an armed run is re-prompted by the gate. Parallel runs must
  either keep workers outside the armed stop-gate path or account for the gate in the worker
  lifecycle; worker stop and router flows need the same role handling.
- **Seat admission, queue and lease.** There is no native seat limit, queue or lease: the
  coordinator enforces `parallel.seats`, owns retries, and a crashed coordinator leaves its
  workers running.
- **Worktree scoping.** Nothing native scopes a session to `worker_root` (only the base
  policy's `external_directory` ask limits paths); the coordinator's path validation is the
  boundary.
- **Missed-event recovery policy.** The stream has no replay, so the coordinator must
  reconcile from durable session state and define the reconnect semantics.

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
exclusively and refuses one that already exists, whatever its age, its phase or its run;
the record is the run's ownership proof:

```json
{
  "schema": 1, "ticket": "PROJ-123", "run_id": "0f6f0f2e-…", "phase": 2,
  "token": "…", "acquired_at": "2026-10-15T12:00:00Z"
}
```

Only the owning run (same `run_id` and `phase`) re-acquires its own record; another run is
refused, and a lock that cannot be read is ambiguous and is never overwritten, released or
reclaimed. `release_ticket(repo, ticket, owner)` removes the lock only for that matching
run — the `token` from acquisition is the proof — and only with `completed` and
`quiescent` asserted: a pause, an unfinished run and an expired timeout all keep the lock.
Nothing is ever inferred from age.

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
| `drain` | `true` while an exclusive task awaits or executes integration |
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
   nothing starts and nothing resumes: wait for the workers to return, execute the integration
   transaction, publish the task, and recompute.
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
strictly inside the main checkout's `.claude/worktrees/` area and no other worktree, and adds
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
orchestrator. One native check is **not** met on the installed host: the worker
`subagent`/`question` guard lives in the bridge, and the installed bridge
(`sha256 28857a7f…fada`) predates it — a live `subagent` attempt slipped through and was
contained and interrupted, and a live `question` was not attempted because it would surface
a user-visible prompt. The guard is covered by the bridge source tests and takes effect when
the new bridge is installed; the native check stays open (see the Task 7 report). Raw
evidence: `.artel/run/repro/opencode-parallel-proof/task7/native-evidence/`.
