---
name: save-context
description: "Save the working context — root CLAUDE.md/CHANGELOG.md plus every ticket's <specs.dir>/<TICKET_ID>/ spec trail — into the host-writable .artel/context/ store (newer wins), then clear the corresponding working-tree copies. Use to declutter or archive the working tree before a branch switch or cleanup; bring everything back with /artel:restore-context."
disable-model-invocation: true
model: sonnet
---

Save the working context into the host-writable `.artel/context/` store, then clear the
working-tree copies. Working tree → store flow is "newer wins": `rsync --update` for directory
trees, an explicit `-nt` test for single files. The store — not the working tree — is the durable
home of these workflow files once saved.

## Store layout

`.artel/context/` lives in the host repo, alongside `.artel/run/`
(`${CLAUDE_PLUGIN_ROOT}/docs/config.md`, "Purpose and location"). Host-writable, gitignored — never
committed. Skills run with the host repo as their working directory, so every path below is a
plain repo-relative path — no toplevel lookup needed.

**With kartoteka as the spec store** (`${CLAUDE_PLUGIN_ROOT}/docs/spec-storage.md`), `<specs.dir>/<TICKET_ID>/` holds only gate evidence and `.active_ticket` is the only pointer: those are saved exactly as below, and the spec documents — which live in kartoteka — are neither saved nor needed here. Old spec copies already in the store are moved into kartoteka by `/artel:migrate-specs`.

```
.artel/context/
├── root/                       # latest CLAUDE.md, CHANGELOG.md (newer wins)
├── .active_ticket               # mirror of <specs.dir>/.active_ticket
└── tickets/<TICKET_ID>/
    ├── root/                    # per-ticket CLAUDE.md/CHANGELOG.md snapshot
    └── spec-trail/              # that ticket's <specs.dir>/<TICKET_ID>/ artifacts
```

Every bash block below re-derives `STORE_ROOT=".artel/context"` — shell state does not persist
between blocks.

## Step 0: Resolve the active ticket

Read `<specs.dir>/.active_ticket` (Read tool). Take the first non-empty line, strip any phase
suffix per `${CLAUDE_PLUGIN_ROOT}/docs/ticket-parsing.md` §§1–2 (e.g. `PROJ-2525-5` →
`PROJ-2525`). Call the result `ACTIVE`. A missing pointer or file leaves `ACTIVE` empty — that is
fine; Step 2 then writes only the shared copy.

## Step 1: Create target directories

```bash
STORE_ROOT=".artel/context"
mkdir -p "$STORE_ROOT/root" "$STORE_ROOT/tickets"
```

## Step 2: Root files → ticket snapshot + shared latest

Substitute the resolved `ACTIVE` value literally. When `ACTIVE` is **non-empty**:

```bash
STORE_ROOT=".artel/context"
ACTIVE="PROJ-XXXX"  # substitute the resolved ticket id
mkdir -p "$STORE_ROOT/tickets/${ACTIVE}/root"
for f in CLAUDE.md CHANGELOG.md; do
  if [ -f "$f" ]; then
    ok=1
    if [ ! -f "$STORE_ROOT/tickets/${ACTIVE}/root/$f" ] || [ "$f" -nt "$STORE_ROOT/tickets/${ACTIVE}/root/$f" ]; then
      cp "$f" "$STORE_ROOT/tickets/${ACTIVE}/root/$f" || ok=0
    fi
    if [ ! -f "$STORE_ROOT/root/$f" ] || [ "$f" -nt "$STORE_ROOT/root/$f" ]; then
      cp "$f" "$STORE_ROOT/root/$f" || ok=0
    fi
    if [ "$ok" = "1" ]; then rm -f "$f"; else echo "WARN: kept $f — store copy failed" >&2; fi
  fi
done
```

When `ACTIVE` is **empty**, run the same loop without the ticket-snapshot branch:

```bash
STORE_ROOT=".artel/context"
for f in CLAUDE.md CHANGELOG.md; do
  if [ -f "$f" ]; then
    ok=1
    if [ ! -f "$STORE_ROOT/root/$f" ] || [ "$f" -nt "$STORE_ROOT/root/$f" ]; then
      cp "$f" "$STORE_ROOT/root/$f" || ok=0
    fi
    if [ "$ok" = "1" ]; then rm -f "$f"; else echo "WARN: kept $f — store copy failed" >&2; fi
  fi
done
```

The working-tree copy is removed only after every attempted store copy succeeded; an older working
copy (no copy attempted) is simply dropped — the store already holds a newer one.

## Step 3: Active-ticket pointer

Substitute the literal `<specs.dir>` value (`${CLAUDE_PLUGIN_ROOT}/docs/config.md`; default
`specs/.current`):

```bash
STORE_ROOT=".artel/context"
if [ -f "<specs.dir>/.active_ticket" ]; then
  if [ ! -f "$STORE_ROOT/.active_ticket" ] || [ "<specs.dir>/.active_ticket" -nt "$STORE_ROOT/.active_ticket" ]; then
    cp "<specs.dir>/.active_ticket" "$STORE_ROOT/.active_ticket"
  fi
fi
```

## Step 4: Every ticket folder → `tickets/<ID>/spec-trail/`

All ticket dirs present under `<specs.dir>/` are saved, not just the active one. `find -print0`
keeps names with spaces safe.

```bash
STORE_ROOT=".artel/context"
while IFS= read -r -d '' d; do
  ID=$(basename "$d")
  mkdir -p "$STORE_ROOT/tickets/${ID}/spec-trail"
  rsync -av --update --exclude='.DS_Store' "$d/" "$STORE_ROOT/tickets/${ID}/spec-trail/"
done < <(find "<specs.dir>" -mindepth 1 -maxdepth 1 -type d -print0)
```

## Step 5: Clear the working-tree spec trail

Run this step ONLY if Steps 2–4 all completed without errors. On any earlier failure (copy error,
full disk, permission denied), stop and report instead — the cleanup must never delete artifacts
that did not reach the store.

```bash
[ -d "<specs.dir>" ] && rm -rf "<specs.dir>"
```

## Step 6: Verify

```bash
STORE_ROOT=".artel/context"
ls "$STORE_ROOT/root/" && echo "---" \
  && ls "$STORE_ROOT/tickets/" && echo "---" \
  && { [ ! -d "<specs.dir>" ] && echo "OK: <specs.dir> cleared" || echo "WARNING: <specs.dir> still present"; }
```

IMPORTANT:

- **The store is authoritative.** This skill writes INTO it but never restructures existing store
  content outside the paths above.
- **Idempotent** — `rsync --update` and the `-nt` guards only overwrite when the source is newer;
  re-running after the cleanup is a no-op.
- **Known limitation:** newer-wins uses file mtimes, and `git checkout` re-stamps files with the
  checkout time — a freshly checked-out older branch can beat a genuinely newer store copy
  (per-ticket snapshots mitigate this).
- **Narrower than the source system this was ported from.** That system also mirrored a curated
  mutable subset of its `docs/` folder and a handful of fixed top-level `specs/` files
  (project-specific filenames with no generic equivalent here). Only what maps onto artel's own
  ported vocabulary — root docs and the `<specs.dir>` spec trail — is saved. See
  `${CLAUDE_PLUGIN_ROOT}/docs/design.md`'s decision log.

Report: files per store target, confirmation that `<specs.dir>` was cleared, and which ticket
folder received the root-file snapshot (or "shared only — no active ticket").
