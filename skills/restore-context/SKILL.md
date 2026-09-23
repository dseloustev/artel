---
name: restore-context
description: "Restore the working context — root CLAUDE.md/CHANGELOG.md and the spec trail — from the host-writable .artel/context/ store back into the project. With a ticket arg, restores ONLY that ticket's <specs.dir>/<TICKET_ID>/ artifacts plus common root files; omit the argument for a full restore of every saved ticket. Counterpart to /artel:save-context."
argument-hint: "[ticket-id]"
model: sonnet
---

Restore the working context from the `.artel/context/` store back to its working-tree locations.
Files are copied, not moved — the store is the authoritative mirror. Store layout, "newer wins"
semantics, and exactly what gets saved are defined in
`${CLAUDE_PLUGIN_ROOT}/skills/save-context/SKILL.md` — this skill reads exactly what that one
writes, same paths, same names.

Every bash block below re-derives `STORE_ROOT=".artel/context"` — shell state does not persist
between blocks. Skills run with the host repo as their working directory, so this is a plain
repo-relative path.

Store layout reminder:

- `root/{CLAUDE.md,CHANGELOG.md}` — latest shared copies; `tickets/<TICKET_ID>/root/` — per-ticket
  snapshots (preferred in ticket mode).
- `.active_ticket` — the active-ticket pointer.
- `tickets/<TICKET_ID>/spec-trail/` — that ticket's `<specs.dir>/<TICKET_ID>/` artifacts.

## Argument

`$0` is optional. Parse it per `${CLAUDE_PLUGIN_ROOT}/docs/ticket-parsing.md` §§1–2 when
non-empty (a phase suffix is accepted and ignored — the store is ticket-scoped, not phase-scoped):

| Input | TICKET_ID | Mode |
|-------|-----------|------|
| (empty) | — | **Full restore** — root files + every ticket under `tickets/` |
| `PROJ-2774` | `PROJ-2774` | **Ticket restore** — common files + the `PROJ-2774` artifacts only |
| `2774` | `PROJ-2774` | **Ticket restore** — same as above |
| `PROJ-2774-3` | `PROJ-2774` | **Ticket restore** — phase suffix is ignored |

## Step 0: Verify the store exists

```bash
STORE_ROOT=".artel/context"
test -d "$STORE_ROOT" || { echo "No context store found at $STORE_ROOT — nothing has been saved yet (run /artel:save-context first)." >&2; exit 1; }
```

### Branch A — Full restore (no ticket argument)

Substitute the literal `<specs.dir>` value (`${CLAUDE_PLUGIN_ROOT}/docs/config.md`; default
`specs/.current`); run as-is otherwise. When `knowledge.adapter` is `kartoteka`, kartoteka is the spec store (`${CLAUDE_PLUGIN_ROOT}/docs/spec-storage.md`) and spec documents are never restored to disk: add `--exclude={idea,vision,prd,research,plan,tasklist,tasks,implementation-notes,review,deep-review,qa,adr,summary,design-analysis,pr-description,post_feedback}.md` to both `rsync` commands in this file (Branch A and Branch B step 3), and report that old spec copies stay in the context store until `/artel:migrate-specs` moves them in.

```bash
STORE_ROOT=".artel/context"
[ -f "$STORE_ROOT/root/CLAUDE.md" ] && cp "$STORE_ROOT/root/CLAUDE.md" CLAUDE.md
[ -f "$STORE_ROOT/root/CHANGELOG.md" ] && cp "$STORE_ROOT/root/CHANGELOG.md" CHANGELOG.md
mkdir -p "<specs.dir>"
[ -f "$STORE_ROOT/.active_ticket" ] && cp "$STORE_ROOT/.active_ticket" "<specs.dir>/.active_ticket"
while IFS= read -r -d '' t; do
  ID=$(basename "$t")
  [ -d "$t/spec-trail" ] || continue
  mkdir -p "<specs.dir>/${ID}"
  # kartoteka as the spec store: add --exclude={idea,vision,prd,research,plan,tasklist,tasks,implementation-notes,review,deep-review,qa,adr,summary,design-analysis,pr-description,post_feedback}.md (see above)
  rsync -av --exclude='.DS_Store' "$t/spec-trail/" "<specs.dir>/${ID}/"
done < <(find "$STORE_ROOT/tickets" -mindepth 1 -maxdepth 1 -type d -print0)
ls -la CLAUDE.md CHANGELOG.md 2>/dev/null; ls -la "<specs.dir>"
```

Report: "Full restore — copied root docs and every saved ticket's spec trail from the context
store."

### Branch B — Ticket restore (ticket argument provided)

Restores common files + ONLY the requested ticket's artifacts. Other tickets' folders are never
touched. Substitute the resolved `TICKET_ID` and the literal `<specs.dir>` value.

1. **Common root files** — ticket snapshot preferred, shared latest as fallback (covers tickets
   saved before per-ticket snapshots existed):

   ```bash
   STORE_ROOT=".artel/context"
   TICKET_ID="PROJ-XXXX"  # substitute
   for f in CLAUDE.md CHANGELOG.md; do
     if [ -f "$STORE_ROOT/tickets/${TICKET_ID}/root/${f}" ]; then
       cp "$STORE_ROOT/tickets/${TICKET_ID}/root/${f}" "${f}"
       echo "restored ${f} (ticket snapshot)"
     elif [ -f "$STORE_ROOT/root/${f}" ]; then
       cp "$STORE_ROOT/root/${f}" "${f}"
       echo "restored ${f} (shared latest — no ticket snapshot)"
     fi
   done
   ```

2. **Active-ticket pointer:**

   ```bash
   STORE_ROOT=".artel/context"
   [ -f "$STORE_ROOT/.active_ticket" ] && mkdir -p "<specs.dir>" && cp "$STORE_ROOT/.active_ticket" "<specs.dir>/.active_ticket"
   ```

3. **Ticket artifacts:**

   ```bash
   STORE_ROOT=".artel/context"
   TICKET_ID="PROJ-XXXX"  # substitute
   FOUND=0
   if [ -d "$STORE_ROOT/tickets/${TICKET_ID}/spec-trail" ]; then
     mkdir -p "<specs.dir>/${TICKET_ID}"
     # kartoteka as the spec store: add --exclude={idea,vision,prd,research,plan,tasklist,tasks,implementation-notes,review,deep-review,qa,adr,summary,design-analysis,pr-description,post_feedback}.md (see above)
     rsync -av --exclude='.DS_Store' "$STORE_ROOT/tickets/${TICKET_ID}/spec-trail/" "<specs.dir>/${TICKET_ID}/"
     FOUND=1
   fi
   echo "FOUND=$FOUND"
   ```

4. **Warn loudly if nothing matched (non-fatal).** Common root files are still restored; silent
   skip is a bug, not a feature:

   ```bash
   STORE_ROOT=".artel/context"
   TICKET_ID="PROJ-XXXX"  # substitute — run only when step 3 printed FOUND=0
   echo "WARN: no artifacts found for ${TICKET_ID} in the context store" >&2
   echo "Searched: $STORE_ROOT/tickets/${TICKET_ID}/spec-trail/" >&2
   echo "Tickets present:" >&2
   ls "$STORE_ROOT/tickets/" >&2 2>/dev/null || echo "  (none)" >&2
   rmdir "<specs.dir>/${TICKET_ID}" 2>/dev/null || true
   ```

5. **Verification listing:**

   ```bash
   TICKET_ID="PROJ-XXXX"  # substitute
   ls -la CLAUDE.md CHANGELOG.md 2>/dev/null
   ls -la "<specs.dir>"
   [ -d "<specs.dir>/${TICKET_ID}" ] && ls -la "<specs.dir>/${TICKET_ID}/"
   ```

Report based on `FOUND`:

- **`FOUND=1`** — "Ticket restore for ${TICKET_ID} — copied common root files + ${TICKET_ID}
  artifacts into <specs.dir>/${TICKET_ID}/."
- **`FOUND=0`** — "Ticket restore for ${TICKET_ID} — no ticket artifacts in the store; common root
  files (`CLAUDE.md`, `CHANGELOG.md`, `.active_ticket`) restored."

## Examples

- `restore-context` — full restore.
- `restore-context PROJ-2774` — restore common root files + the `PROJ-2774` spec trail.
- `restore-context 2774` — same as above.

## Invariants

- **Copy, don't move** — the store stays authoritative.
- **Root-file fallback:** ticket snapshot (`tickets/<ID>/root/`) wins; `root/` shared latest is
  used only when the snapshot is absent.
- **Skip `.DS_Store`** in every copy step. **Idempotent** — re-running with the same argument
  overwrites with identical content.
- **Missing optional files are not errors** — log and continue. The only hard failure is a missing
  `.artel/context/` store entirely.
- **Known limitation:** newer-wins uses file mtimes, and `git checkout` re-stamps files with the
  checkout time — a freshly checked-out older branch can beat a genuinely newer store copy
  (per-ticket snapshots mitigate this).
- **Narrower than the source system this was ported from.** No `docs/` mirror, no top-level loose
  `specs/` files mirror — see `${CLAUDE_PLUGIN_ROOT}/skills/save-context/SKILL.md`'s matching note
  and `${CLAUDE_PLUGIN_ROOT}/docs/design.md`'s decision log.
