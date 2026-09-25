# What kartoteka-primary spec storage needs from kartoteka

*Status: draft · 2026-08-31 · reviewed against kartoteka 0.35.0 on 2026-09-15 · companion to
the kartoteka-primary spec-storage design*

**Every §1 and §2 item has since shipped, and so has all of §4** — kartoteka 0.28.0, 0.30.0,
0.31.0 and 0.32.0 between them closed the whole blocking and gating set. Only §3.1
(`TICKET_KEY`) remains open, and it is conditional. Each subsection carries its own
**Shipped —** paragraph below; the Summary table records the state at a glance. artel's store mode
shipped in 0.16.0 on kartoteka 0.43.0 (§6 below); spec images shipped in 0.17.0 on kartoteka
0.44.0's K3 attachment store (§6 below); §1.1/§1.2's parameters are still unused by
`docs/task-queue.md`.

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
`## Final Verification` — can no longer be worked by file scan (since 0.18.0 no writer emits
`## Final Verification`; older tasklists still carry it), which is how
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

**Shipped — kartoteka 0.28.0 (E1, 2026-09-01).** `task_ready` / `claim_ready_task` take
`parent_id`, folded into the existing fused claim statement so atomicity is unchanged. It
narrows and does not gate: omitting it still means "any parent", and a `parent_id` naming no
task is **rejected** rather than answered with "No ready tasks.", so a typo cannot look like a
finished section. **artel has not adopted it.** `docs/task-queue.md` §3 still claims unscoped
and releases a wrong-phase row back to `ready` — see the open follow-up in
[design.md](design.md#open-follow-ups).

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

**Shipped — kartoteka 0.28.0 (E1, 2026-09-01),** as proposed: `order = "activity" | "created"`
on `task_list` and `GET /api/tasks`, `"activity"` still the default. **artel has not adopted
it** — it still recovers plan order by sorting on `task_id` client-side. Same follow-up as §1.1.

---

## §2 Gating — required before artel may delete local specs

These are the "kartoteka is shared/hosted" prerequisites. artel's store mode can be built and
tested against a local daemon without them; the `specs.allowLocalDeletion` key once planned
as that gate was dropped — `/artel:migrate-specs` verifies and confirms every deletion instead
(design.md decision log, 2026-09-22).

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

**Shipped — kartoteka 0.32.0 (E3 phase 1, 2026-09-05).** `[server] bind` with TLS or an
upstream terminator, and `[auth] enabled = true` putting a bearer token on the whole port,
reads included; every write records the token's verified `principal` *beside* `author_agent`
and `actor` rather than replacing them, so the self-reported fields keep their meaning and the
verified one is new. Nothing changes with the defaults. artel's half — `knowledge.tokenEnv`,
the `Authorization` header on the mirror hook, `--header` on the MCP registration — is artel
0.11.0 (`config.md`). Phase 2, GitHub sign-in, is open in `../kartoteka/docs/epics.md`.

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

**Shipped — kartoteka 0.30.0 (E2, 2026-09-03),** as proposed, with `0` meaning "this must not
exist yet". One caveat for the store-mode design to absorb: it guards the version **number**,
not the content behind it — a redaction landing between a read and a put leaves the number
unchanged, so the absence of a conflict is not proof the content read is still stored. The
content-hash idempotence check still runs first and is unaffected.

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

**Shipped — kartoteka 0.31.0 (2026-09-04),** taking the first option: `project` is on
`artifacts`, `tasks` and `notes` and in all three unique keys, one `kartoteka.toml` per project
may share a `db_path`, and `project` is now **required** in every config file (the silent
`"default"` fallback is gone). Breaking, and it needs `kartoteka migrate` run with the config of
the project the database has been serving — nothing later can tell a wrong namespace from a
right one. artel adopted it in **0.9.0** as `knowledge.project`, and every queue and artifact
call names it ([config.md](config.md), [task-queue.md](task-queue.md) §1).

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

**Still open as of kartoteka 0.35.0.** `TICKET_KEY` is unchanged at
`^[A-Z][A-Z0-9]+-\d+\Z` (`../kartoteka/src/kartoteka/models.py:17`). This is the only item in
this file that has not shipped, and it stays conditional: it binds nothing until artel decides
to pull release scope off the disk too.

---

## §4 Nice to have

**All three shipped.** Recorded as written, each with the release that closed it.

- **No `artifact_delete`.** A wrong `artifact_put` is a permanent version row, fixable only by
  putting a correction on top. Acceptable while the store mirrors files that can be re-pushed;
  more awkward when it is the system of record and someone pastes a secret into `prd.md`.

  **Shipped — kartoteka 0.30.0,** as redaction rather than deletion: `artifact_redact` (MCP)
  and `DELETE /api/artifacts/{ticket_key}/{stage}/{name}/{version}`. One **named** version's
  content is replaced in place with `[redacted]`; the row and its version number survive, so no
  later put reuses a retired number. `version` is required and has no default, it is idempotent,
  and it is irreversible — kartoteka keeps no copy. Redacting the newest version of an indexed
  artifact re-ingests automatically, so the secret leaves `chunks`/`chunk_vectors` too.
- **No `artifact_versions` MCP tool.** It exists over HTTP
  (`GET /api/artifacts/{ticket_key}/{stage}/{name}/versions`,
  `../kartoteka/src/kartoteka/web.py:585`) and as a service method
  (`../kartoteka/src/kartoteka/workspace.py:104`), but is not registered as a tool. Agents
  reconstructing review rounds would use it; today they cannot, because a hook subprocess is
  the only artel component that speaks HTTP.

  **Shipped — kartoteka 0.30.0.** Registered as an MCP tool; every stored version of one
  artifact, newest first, without bodies. A redacted entry is marked `REDACTED`.
- **`format_task_list` omits `parent_id`** (`../kartoteka/src/kartoteka/mcp_server.py:95`),
  though `/api/tasks` returns it. Hierarchy is recoverable only from artel's own
  `I<N> · <section> · <text>` title convention. Adding it to the rendered line would make the
  MCP surface self-describing instead of convention-dependent.

  **Shipped — kartoteka 0.28.0.** `task_list` renders `· parent: #N`, and `related`'s
  `## tasks` section gains it too. Note it is a **format** change, not purely an addition: the
  segment is inserted between `actor` and the description, moving where the description sits for
  anything parsing that line positionally.

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

## §6 Store mode as built (2026-09-22, 2026-09-23)

- **K1 `artifact_patch`** — **Shipped 0.43.0.** Ordered `{old_string, new_string}` /
  `{append}` edits applied atomically to the newest version under the store's write lock; MCP
  tool and `PATCH /api/artifacts/{ticket_key}/{stage}/{name}`. Store mode's every in-place edit
  — a ticked box, an appended fix batch — is one call, not a whole-document round trip.
- **K2 write receipts** — **Shipped 0.43.0.** `artifact_put` and `artifact_patch` answer with
  the stored version's header, not the body the caller just sent.
- **K3 attachment store** — **Shipped 0.44.0.** Images of a ticket's trail, keyed
  `(project, ticket_key, path)` with the path verbatim from the ticket folder (`design/x.png`,
  `phase-2/runtime/y.png`):
  - versioned and content-addressed;
  - PNG, JPEG, GIF and WebP only, sniffed from magic bytes, with the extension required to agree;
  - capped by `[workspace] max_attachment_bytes` (5 MiB by default);
  - never indexed, and with no MCP tool.

  The routes are `PUT` and `GET /api/attachments/{ticket_key}/{path}` (raw bytes, idempotent
  put, `ETag`/`304`), `GET /api/attachments?project=&ticket_key=[&path=]` and a redacting
  `DELETE`. The dashboard renders an artifact's relative image links that name a stored
  attachment. artel 0.17.0 sweeps images in with `spec_store.py image sync` and views them with
  `image fetch` (`docs/spec-storage.md` §4.6).

### 6.4 Document header (K4)

**Shipped 0.45.0.** A leading YAML block is read on every artifact put and patch, and never
rewritten. A `version`, `type` or `ticket` that disagrees with the row the write creates is
refused (HTTP 400, MCP `Rejected:`). `title`, `status`, `summary`, `schema`, `produced_by` and
`type` are lifted into columns shown in listings, receipts and the dashboard; `status=` filters on
them. artel writes the header from 0.21.0 (design 2026-09-24).

### 6.5 References (K5)

**Shipped 0.46.0.** `workspace:<TICKET_KEY>/<stage>/<name>[@v<N>]`, the workspace index id plus an
optional version, renders on `/artifact` as a link to that artifact. Every answer about one
version carries it on a `- ref:` line (a `ref` field in JSON). artel's writers copy it from there
(`docs/spec-storage.md` §3.1). Not probed: kartoteka exposes no version, so artel's 0.46.0 floor is
documented, not enforced.

## Summary

| # | Change | Tier | Size | State |
|---|---|---|---|---|
| 1.1 | Scope `task_ready` / `claim_ready_task` by `parent_id` | Blocking | Small | **Shipped 0.28.0** — unused by artel |
| 1.2 | `order` parameter on `list_tasks` / `task_list` | Blocking | Trivial | **Shipped 0.28.0** — unused by artel |
| 2.1 | Authentication + non-loopback bind | Gating | Large — own design | **Shipped 0.32.0** — artel side in 0.11.0 |
| 2.2 | `expected_version` on `put_artifact` | Gating | Small | **Shipped 0.30.0** |
| 2.3 | Per-project namespace, or a documented one-project constraint | Gating | Medium or doc-only | **Shipped 0.31.0** — artel side in 0.9.0 |
| 3.1 | Relax `TICKET_KEY` for release identifiers | Conditional | Small, wide blast radius | **Open** — conditional, binds nothing today |
| 4.x | `artifact_delete`, `artifact_versions` tool, `parent_id` in list output | Nice to have | Small | **Shipped** — 0.30.0 (as `artifact_redact`), 0.30.0, 0.28.0 |
| 6 | `artifact_patch`, write receipts | Store mode | Small | **Shipped 0.43.0** |
| 6.3 | Attachment store (K3) | Spec images | Medium | **Shipped 0.44.0** |
| 6.4 | Document header: validation, lifted columns | Document header | Medium | **Shipped 0.45.0** |
| 6.5 | `workspace:` references, `ref:` line | References | Small | **Shipped 0.46.0** |

Only 1.1 and 1.2 stand between artel and a working store mode against a local daemon. Everything
in §2 stands between that and deleting anyone's files.

**Where that leaves things (2026-09-06).** Both sentences above are now satisfied: §1 and §2
have shipped in full, so nothing on kartoteka's side blocks store mode, and nothing blocks local
deletion either. Store mode shipped in artel 0.16.0 against kartoteka 0.43.0, which added §6's
first two items; spec images (0.17.0) need kartoteka 0.44.0's K3.
