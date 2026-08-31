# What kartoteka-primary spec storage needs from kartoteka

*Status: draft · 2026-08-31 · companion to the kartoteka-primary spec-storage design*

artel's spec trail is moving from "files on disk, best-effort mirror into kartoteka" to
"kartoteka is the store; nothing lands in the repo." This file states what that mode needs
from the **other side of the contract** — `../kartoteka` — and, equally, what it does *not*
need, so nobody spends effort on a change that was already unnecessary.

Everything here was checked against `../kartoteka` source, not against
`../kartoteka/docs/artel-integration.md`. Where the two disagree, the source is cited.

**Nothing in this file is a change to artel.** artel's own side is the design document this
accompanies. The split matters: items in §2 gate whether artel may *delete a user's local
specs*, and artel must not ship that step before they land.

## How to read this

| Tier | Meaning |
|---|---|
| **§1 Blocking** | artel's store mode cannot be built as designed without this, or is built with a named workaround at a stated cost. |
| **§2 Gating** | Not needed to build store mode; needed before local deletion is safe. These are the "shared/hosted kartoteka" prerequisites. |
| **§3 Conditional** | Needed only if a specific carve-out is closed. |
| **§4 Nice to have** | Real gaps, no current blocker. |
| **§5 Verified fine** | Checked, no change needed. Recorded so it is not re-litigated. |

---

## §1 Blocking

### 1.1 `task_ready` cannot be scoped to a section

**What artel needs.** Under store mode there is no `tasklist.md` on disk, so the four
gate-remediation sections — `## Code Review Fixes`, `## Runtime Fixes`, `## Verify Fixes`,
`## Final Verification` — can no longer be worked by file scan, which is how
`docs/task-queue.md` §6 handles them on both paths today. The design turns them into queue
rows created at gate time. A gate dispatch must then claim **only** from its own section.

**What exists.** `claim_ready_task(conn, *, actor=None, ticket_key=None)`
(`../kartoteka/src/kartoteka/tasks.py:376`) takes the oldest `ready` row for the ticket —
`ORDER BY task_id LIMIT 1` — with no way to narrow it further. `parent_id` is stored but,
per its own comment in `../kartoteka/src/kartoteka/models.py`, is "structurally inert:
readiness is status alone, so nothing reads this for scheduling."

**Why that does not suffice.** A run that reaches the review gate with iteration rows still
`ready` would hand the review-fix dispatch an iteration task instead. Filtering client-side
means replacing the claim with `task_list` + `task_update`, which gives up the atomicity the
fused claim statement was specifically built for — `claim_ready_task`'s own docstring records
that a peek-only queue "hands the same task to two agents the first time more than one polls."

**Proposed change.** Add an optional narrowing parameter to `claim_ready_task` and the
`task_ready` MCP tool. `parent_id: int | None` is the smallest version and reuses a column
that already exists:

```sql
SELECT task_id FROM tasks
 WHERE status='ready' AND ticket_key=? AND parent_id=?
 ORDER BY task_id LIMIT 1
```

folded into the same single UPDATE statement, so atomicity is unchanged. A `group`/`label`
column would be more general; `parent_id` is enough for artel and adds no schema.

**artel-side workaround, if this is declined.** Keep the four sections out of the queue and
carry them in the tasklist *artifact* instead, re-putting the whole document on each flip.
Costs one full-document `artifact_put` and one contextualize call per gate-fix checkbox
(typically 5–15 per run rather than the 40+ that made whole-document round-trips unacceptable
for iteration work). Workable, and strictly worse.

### 1.2 `list_tasks` returns activity order, not plan order

**What artel needs.** Store mode renders `tasklist.md` on demand from the queue for humans and
for the checkpoint record. That rendering must be in **plan order** — iteration 1 first, tasks
in the order the planner wrote them.

**What exists.** `list_tasks` ends `ORDER BY updated_at DESC, task_id DESC`
(`../kartoteka/src/kartoteka/tasks.py:358`) — "most recent activity first." There is no
ordering parameter and no position column.

**Why it half-suffices.** `format_task_list` (`../kartoteka/src/kartoteka/mcp_server.py:95`)
does emit `#{task_id}`, and artel's own `build_rows`
(`scripts/tasklist_tasks.py:123`) mirrors in document order precisely because "insertion order
IS queue order." So artel *can* sort by `task_id` client-side and recover plan order. That
makes this non-fatal — but it puts a re-sort in the model's hands on every render, and a
rendered work list whose order silently churns with activity is the kind of defect nobody
notices until a phase runs out of sequence.

**Proposed change.** An `order: str = "activity"` parameter on `list_tasks` and the `task_list`
tool, accepting `"activity"` (today's behaviour, default, nothing breaks) and `"created"`
(`ORDER BY task_id`). Two lines of SQL.

---

## §2 Gating — required before artel may delete local specs

These are the "kartoteka is shared/hosted" prerequisites. artel's store mode can be built and
tested against a local daemon without them; artel's `specs.allowLocalDeletion` stays `false`
until they land.

### 2.1 No authentication, loopback-only bind

**What exists.** `../kartoteka/docs/operations.md:189`, on enabling `[workspace]`: it "turns
the daemon into a **writer** for the first time — any local process that can reach the daemon
can call `artifact_put` and append a row to `artifacts`. The loopback bind is what stands in
front of that … this switch adds no authentication of its own."

**Why it matters now and did not before.** Today the artifact store holds a *best-effort
mirror* of files that exist on disk and in git; losing or corrupting it costs nothing. Under
store mode it holds the **only** copy of the deliberation trail. The threat model changes
completely, and "the bind is the access control" does not survive the move off loopback.

**What is needed.** A design decision on kartoteka's side, not a patch — at minimum a
non-loopback bind option, transport security, and a bearer token on the write routes and the
MCP transport, with `author_agent` and `actor` moving from self-reported provenance to
something derived from the authenticated principal (both are currently commented as
"self-reported by an unauthenticated caller on a loopback port … never consulted for a
decision", and the trail's audit value changes once it is the system of record). This deserves
its own brainstorm in `../kartoteka`; it is out of scope for artel's plan.

### 2.2 `put_artifact` has no lost-update protection

**What exists.** `put_artifact` (`../kartoteka/src/kartoteka/workspace.py:138`) skips the write
when the content hash matches the newest version, and otherwise appends
`COALESCE(MAX(version),0)+1` unconditionally. There is no expected-version check.

**The failure.** Two agents read `plan.md` at v1, both edit, both put. The store now holds v2
and v3; v2's changes are absent from the newest version and nothing reports a conflict. Under
loopback with one run at a time this is close to impossible. Under a shared store with several
developers on one ticket it is routine, and it silently destroys work that has no other copy.

**Proposed change.** An optional `expected_version: int | None` on `put_artifact`, the
`artifact_put` tool and `POST /api/artifacts`; on mismatch, reject with `409` and the current
version number rather than appending. Callers that omit it keep today's behaviour, so nothing
existing breaks.

### 2.3 No per-project namespace

**What exists.** Artifacts and tasks key on `ticket_key` alone
(`UNIQUE(ticket_key, stage, name, version)`, `UNIQUE(ticket_key, title)`).

**The failure.** One shared daemon serving several host repositories has no way to keep them
apart. Two repos that share a `ticket.projectKey` — or two checkouts of the same repo, one of
them a scratch host used for artel smoke tests per `CLAUDE.md` — write into each other's trail.
On a best-effort mirror that is noise; on the system of record it is data loss.

**Proposed change.** Either a `workspace`/`project` column participating in both unique keys,
or an explicit documented constraint that one daemon serves exactly one project-key space, with
the deployment guidance to match. The second is cheaper and may well be the right answer; what
is not acceptable is leaving it undecided once the store is authoritative.

---

## §3 Conditional — only if release scope must also leave the disk

### 3.1 `TICKET_KEY` rejects artel's release identifiers and short project keys

**What exists.** `TICKET_KEY = re.compile(r"^[A-Z][A-Z0-9]+-\d+\Z")`
(`../kartoteka/src/kartoteka/models.py:17`), validated in both `put_artifact` and
`create_task`.

**What it rejects.**

| artel identifier | Where it comes from | Why it fails |
|---|---|---|
| `R-2026.10` | Release scope — `<specs.releases>/<RELEASE_ID>.md` and `<specs.releases>/<RELEASE_ID>/qa.md`, `docs/config.md` §`specs` | Single-character prefix (`[A-Z][A-Z0-9]+` needs two or more) **and** a non-digit tail |
| `X-123` | A one-character `ticket.projectKey`, which `docs/config.md` explicitly allows | Prefix too short |

The second case is already known on artel's side — `hooks/knowledge_mirror.py` names it as one
of the two ways a mirror post earns a permanent `400`.

**The decision this forces.** artel's design currently carves release scope out to files. That
carve-out is a hole in "no local copies": release QA would still write to disk under a mode
that promises it does not. Closing it needs the grammar relaxed — something like
`^[A-Z][A-Z0-9]*-[A-Za-z0-9.]+\Z` — which widens what a "ticket key" means in kartoteka and
touches `related()`'s join with Jira documents. That is kartoteka's call, and it is a real
design question rather than a validation tweak.

Leaving it as-is is defensible; it just has to be a decision on record, not an oversight.

---

## §4 Nice to have

- **No `artifact_delete`.** A wrong `artifact_put` is a permanent version row, fixable only by
  putting a correction on top. Acceptable while the store mirrors files that can be re-pushed;
  more awkward when it is the system of record and someone pastes a secret into `prd.md`.
- **No `artifact_versions` MCP tool.** It exists over HTTP
  (`GET /api/artifacts/{ticket_key}/{stage}/{name}/versions`,
  `../kartoteka/src/kartoteka/web.py:585`) and as a service method
  (`../kartoteka/src/kartoteka/workspace.py:104`), but is not registered as a tool. Agents
  reconstructing review rounds would use it; today they cannot, because a hook subprocess is
  the only artel component that speaks HTTP.
- **`format_task_list` omits `parent_id`** (`../kartoteka/src/kartoteka/mcp_server.py:95`),
  though `/api/tasks` returns it. Hierarchy is recoverable only from artel's own
  `I<N> · <section> · <text>` title convention. Adding it to the rendered line would make the
  MCP surface self-describing instead of convention-dependent.

---

## §5 Verified fine — no change needed

Checked while writing this, and recorded so it is not investigated twice.

- **Artifact content survives the MCP round-trip verbatim.** `_fenced`
  (`../kartoteka/src/kartoteka/mcp_server.py:24`) picks a fence longer than the longest backtick
  run in the body, so artel spec documents — every one of which contains ```` ``` ```` blocks —
  come back intact rather than breaking the framing. This was the single biggest correctness
  risk in strict mode and it is already handled.
- **Capability probing needs no new tool.** With `[workspace]` off the seven tools are *not
  registered at all*, so their presence in the session is itself the probe. artel needs no
  ping endpoint, and should scope its confirming call to the run's own ticket
  (`artifact_list(ticket_key=…)`) rather than calling it bare, which returns every artifact in
  the store.
- **`artifact_put` idempotence on content hash is exactly what migration needs.** Pushing an
  unchanged file adds no version; pushing a changed one becomes the new version. artel's
  migration therefore needs no timestamp comparison and no conflict API — which is why the
  "compare local against kartoteka and update if newer" step in the original proposal
  collapses to a plain push.
- **The artifact HTTP routes are registered independently of `[web] enabled`**
  (`../kartoteka/src/kartoteka/web.py:565`, and the comment above the versions route), so an
  artifact store with no dashboard is a valid deployment.
- **`max_artifact_bytes` at 1 MiB** (`../kartoteka/src/kartoteka/workspace.py:24`) is far above
  any artel spec document.
- **Indexing cost is already controllable.** Each new artifact version costs one
  `[contextualize]` call (`../kartoteka/docs/operations.md`, "Indexing the artifact store").
  `workspace.index_stages` can exclude the rendered tasklist view that store mode re-puts at
  every checkpoint. Configuration, not code.
- **Task statuses cover what artel needs.**
  `{backlog, ready, in_progress, blocked, done}` (`../kartoteka/src/kartoteka/tasks.py:49`) —
  gate-remediation rows need no new status.

---

## Summary

| # | Change | Tier | Size |
|---|---|---|---|
| 1.1 | Scope `task_ready` / `claim_ready_task` by `parent_id` | Blocking | Small |
| 1.2 | `order` parameter on `list_tasks` / `task_list` | Blocking | Trivial |
| 2.1 | Authentication + non-loopback bind | Gating | Large — own design |
| 2.2 | `expected_version` on `put_artifact` | Gating | Small |
| 2.3 | Per-project namespace, or a documented one-project constraint | Gating | Medium or doc-only |
| 3.1 | Relax `TICKET_KEY` for release identifiers | Conditional | Small, wide blast radius |
| 4.x | `artifact_delete`, `artifact_versions` tool, `parent_id` in list output | Nice to have | Small |

Only 1.1 and 1.2 stand between artel and a working store mode against a local daemon. Everything
in §2 stands between that and deleting anyone's files.
