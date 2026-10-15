# OpenCode parallel workers — frozen contract

*Status: v0.1 · 2026-10-15 · proven on OpenCode 2.0.29*

Sub-project 2c runs one worker session per task on the OpenCode host. This file is the
contract the native capability gate froze: it fixes the session interfaces the adapter
uses, the worker's role, permissions and location, the assignment and result `schema: 1`, the
task states and their transitions, integration ownership, the concurrency metadata, and what
happens when a capability is missing. It records a contract, not shipped behavior: no
parallel code exists yet, and `parallel.seats` absent or `1` keeps the serial procedure
exactly as it is (a boolean is not an integer and fails configuration validation).

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

The adapter is a thin layer over the running service's HTTP API. The coordinator addresses a
location-scoped endpoint with the `location[directory]` query parameter; the CLI's `--param`
cannot express that object, so the adapter calls the HTTP API directly (the CLI remains the
operator path for plain requests).

| Operation | Request | Response |
|---|---|---|
| Create a worker session | `POST /api/session` — `{id?, parentID?, title?, agent?, model?{providerID, id, variant?}, location?{directory}, metadata?, permissions?}` | `{data: Session.Info}` |
| Send the pinned prompt | `POST /api/session/{id}/prompt` — `{id?, text, files?, agents?, skills?, metadata?, delivery?("steer"\|"queue"), resume?}` | `{data: Session.Inbox.User}` |
| Read status | `GET /api/session/{id}` | `{data: Session.Info}` |
| Interrupt | `POST /api/session/{id}/interrupt?resume=true\|false` | `{interrupted: boolean}` |
| Wait for idle (bounded) | `POST /api/experimental/session/{id}/wait` | `204` when the agent loop is idle |
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

## 3. Worker role, permissions and location

- **One worker, one worktree.** A worker session's `location.directory` equals its
  `worker_root`, the task's own worktree ([worktrees.md](worktrees.md)); no two workers share
  a tree. The existing hooks run in that root — the session baseline, the stop gate and fast
  verify all observed the worker's worktree as their cwd/root.
- **Role.** The session is created with an explicit worker `agent`; the role is reported back
  on `Session.Info.agent` and on every assistant message, and can be switched mid-session.
  A worker must not mutate the queue, run state, tasklist or notes, must not commit, push or
  open a pull request, and must not ask the user — it executes the pinned assignment and
  returns the structured result. Native permission rules can deny individual actions; the
  remaining worker-mode protections are not native and are listed in §12.
- **Permissions.** A session carries a ruleset of `{action, resource, effect}` rules; rules
  combine in order, the last match wins, `deny` blocks the operation, and `ask` surfaces a
  `Permission.Request` that a client answers with `once`, `always` or `reject` (a `reject`
  also rejects the session's other pending requests). Session rules are evaluated after the
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

## 12. Capability failure and unproven items

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
- There is no native seat admission, queue or lease: the coordinator enforces
  `parallel.seats`, and a crashed coordinator leaves its workers running.
- `prompt.resume` and `interrupt?resume=true` were not exercised; the recovery path is the
  coordinator's decision.
- A child session inherits location and permissions but not an explicit agent; the
  child-session path through the native subagent dispatch was not exercised end to end.
- The barrier that held the two proof workers relied on the model following the pinned
  prompt; it was scratch instrumentation, not protection.

## 13. Proof record

The capability proof ran 2026-10-15 against OpenCode 2.0.29: two workers in two detached
worktrees of an auto-cleaned scratch host, with the barrier, interrupt, missed-event,
permission, role/location and guard evidence described above. The raw evidence — session
ids, event captures, hook state and probe scripts — is host-owned scratch under
`.artel/run/repro/opencode-parallel-proof/` and is not committed; the version and interface
claims in this file come from that proof and from the installed schema
(`opencode api get /openapi.json`).
