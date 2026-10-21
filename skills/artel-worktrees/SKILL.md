---
name: artel-worktrees
description: "List this repository's git worktrees — ticket, branch, path, dirty state, and which one this session is in — so parked work and parallel sessions are visible at a glance. Read-only: it never creates, moves, prunes or removes a worktree. Use when someone asks 'which worktrees are there', 'what is parked', 'where does ticket X's work live', or before moving a session into or out of one."
argument-hint: ""
metadata:
  opencode/autoinvoke: false
---

## Overview

The repository's worktrees, one row each: the ticket whose work it holds, its branch, its path,
how many files are dirty, and which one this session is in — plus the repository's **ticket
locks**, so a ticket that is actively owned by another session is visible before anyone moves
in. It is the read side of `move-to-worktree` / `return-from-worktree`: those two change where a
ticket's work lives, this one only says where it currently lives and who may work it.

It is a **worker that runs inline** — one script call and a table. It mutates nothing, so it is
safe to run at any point in a session, including inside another skill's procedure. It is
user-invoked only (`metadata.opencode/autoinvoke: false`).

## Steps

Run from anywhere inside the repository.

1. Run

       python3 ${ARTEL_ROOT}/scripts/worktree.py list

   The report is `{"status": "ok", "schema": 1, "main": <path>, "current": <path>,
   "worktrees": [{name, path, branch, ticket, main, current, dirty, missing}, …], "locks":
   [{ticket, run_id, phase, coordinator_id, acquired_at, recovered_at, unreadable}, …]}` — the
   main checkout first, every linked worktree after it. The lock records carry no token.

2. Render a table, one row per worktree, in the order reported:

   | Ticket | Branch | Path | Dirty | State |

   - **Ticket** — the `ticket` field, or `—` when it is null: that worktree was not made by
     `move-to-worktree`, so no ticket owns it. Say so rather than leaving the cell blank.
   - **Branch** — `branch`, or `detached` when it is null.
   - **Dirty** — `dirty`, or `missing` when the field is null because the directory is gone.
   - **State** — `current` marks the worktree this session runs in; `main` marks the checkout
     the repository was cloned into. Both can be true for the same row.

3. Then a second table, one row per lock, in the order reported — the ticket lock is what says
   a ticket is being worked right now (`${ARTEL_ROOT}/docs/worktrees.md` §9):

   | Ticket | Run | Coordinator | Acquired | Worktree |

   - **Run** — `run_id`, or `—` when the record is `unreadable`.
   - **Coordinator** — `coordinator_id`: the session that holds it.
   - **Acquired** — `acquired_at`, and `recovered <recovered_at>` beside it when a handoff
     happened.
   - **Worktree** — the path of the row in the first table whose ticket matches, or `none`: the
     ticket is owned but no worktree holds it (the main checkout, or a session that has not
     moved in yet).
   - An `unreadable` row is reported as `unreadable — an ambiguous owner; do not assume it is
     free`. Never guess its fields.

4. Close with what the tables imply, one line each, only when it applies:
   - a `missing` row → `stale registration: <path> is gone; git worktree prune clears it`;
   - a row with no ticket → `not created by move-to-worktree: <path>`;
   - a dirty row → `<n> uncommitted path(s) in <ticket-or-path> — hand-back refuses a dirty
     worktree of another session, so this is the one to look at before moving anything`.

## Important Rules

- **Read-only.** Never create, move, prune or remove a worktree from here, and never run
  `git worktree prune` yourself: report the stale row and let the person clear it.
- **Never invent a ticket.** A worktree without `.artel/worktree.json` has no ticket; render `—`.
- **Never print a token.** The lock's `token` is the release proof and the report never carries
  it; if a record shows one, that is a bug in `worktree.py`, not something to render.
- **`dirty` excludes ignored files** — it is `git status --porcelain --untracked-files=all`, so
  build output and `.artel/run/` do not make a worktree look dirty.
- **One call, no polling.** The listing is a snapshot; re-run it only when the person asks again.
