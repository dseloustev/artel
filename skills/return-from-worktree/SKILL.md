---
name: return-from-worktree
description: "Hand a ticket's worktree back to the main checkout once the task is done: its uncommitted changes and run state move back, the main checkout switches to the ticket's branch, and the worktree under .claude/worktrees/ is removed — the branch is kept. Use when the user says the work in a worktree is finished, wants to go back to the main checkout, or asks to remove or close a ticket's worktree."
argument-hint: "[ticket-id]"
disable-model-invocation: true
model: sonnet
---

## Overview

`return-from-worktree` is the way back from `/artel:move-to-worktree` (or `init-branch`'s
worktree option). The contract is `${CLAUDE_PLUGIN_ROOT}/docs/worktrees.md` §5–§6; the mechanics
are `${CLAUDE_PLUGIN_ROOT}/scripts/worktree.py hand-back`, which always works on the **main
checkout** and refuses to start inside the worktree — that directory is about to be deleted —
except with `--check`, which changes nothing.

A **worker that runs inline**, user-invoked only (`disable-model-invocation: true`): it moves the
session and changes the main checkout's branch.

## Steps

### Step 1: Locate the worktree

1. **Main checkout:** the path on the first `worktree` line of
   `git worktree list --porcelain` → `MAIN`.
2. **Inside a linked worktree** (`git rev-parse --path-format=absolute --git-dir` differs from
   `--git-common-dir`): `<name>` is the worktree directory's name. `TICKET_ID` comes from `$0`
   (parsed per `${CLAUDE_PLUGIN_ROOT}/docs/ticket-parsing.md` §§1–2), else from the `ticket` field of
   `.artel/worktree.json`. A worktree outside `MAIN/.claude/worktrees/`, or one with neither
   source → stop: "This worktree was not created by artel; hand it back with git."
3. **In the main checkout:** `TICKET_ID` comes from `$0`, else from `<specs.dir>/.active_ticket`
   (as in `move-to-worktree`). Among the `git worktree list` paths, take those named
   `MAIN/.claude/worktrees/<TICKET_ID>` or `MAIN/.claude/worktrees/<TICKET_ID>-<digits>`. One →
   its directory name is `<name>`. Several → ask which (`AskUserQuestion`, header `Worktree`).
   None → `<name>` = `TICKET_ID`; the script then finishes an interrupted hand-back or refuses.

### Step 2: Check

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/worktree.py hand-back --ticket <TICKET_ID> --name <name> --check
```

`refused` → report `reason` and stop. The usual one: the main checkout has uncommitted changes —
say they may belong to another session, and that they must be committed or stashed there first.

### Step 3: Confirm

Ask once via `AskUserQuestion` (header `Hand back`):

- **Hand back** (recommended) — description: "The main checkout switches from `<mainWasOn>` to
  `<branch>`; `<uncommitted>` uncommitted files move with it; `.claude/worktrees/<name>` is
  removed. The branch is kept." For a recovery (`recovery: true`): "Finish the interrupted hand-back
  of `<branch>`."
- **Keep working here** — stop without changes.

### Step 4: Leave the worktree

Only when the session is inside the worktree: call `ExitWorktree` with `action: "keep"`. If it
reports that no worktree session is active — the session was started inside the worktree — stop
without changes: "Run `/artel:return-from-worktree <TICKET_ID>` from a session in the main
checkout; this session's directory is about to be removed."

**OpenCode:** there is no `ExitWorktree`. Inside a worktree, stop with the same message.

### Step 5: Hand back

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/worktree.py hand-back --ticket <TICKET_ID> --name <name>
```

Act on `status` per `${CLAUDE_PLUGIN_ROOT}/docs/worktrees.md` §6. On `conflict`, the stash was
kept: name its commit ID and the conflicted files in `MAIN`. On `rolled-back`, the worktree is
intact — report `reason`.

### Step 6: Run the post-branch setup commands

On `ok`, the main checkout's branch changed: run `setup.commands` in `MAIN` as `init-branch`
Step 3 describes. A failure is reported, not rolled back — the hand-back is complete.

### Step 7: Report

- The branch now checked out in the main checkout, and the branch it was on (`mainWasOn`).
- Whether uncommitted work came back (`stashApplied`) and whether run state moved (`runMoved`).
- Every `envChanged` path, with one line: it changed in the worktree and was **not** copied back
  (e.g. permissions saved to `.claude/settings.local.json`) — copy what should be kept by hand.
- Whether the setup commands ran, were skipped, or failed.

## Rules

- **Never hand back without asking** (Step 3), and never from a dirty main checkout — the
  script refuses, and this skill does not work around it.
- **Never lose work.** A stash is dropped only after it applied. Never any `--force` variant.
  Never delete the branch — `ExitWorktree` is only ever called with `action: "keep"`.
- **Leave before handing back.** Step 5 runs only once the session is out of the worktree.
- **Environment files are reported, not synced.** The main checkout's copies are the real ones.
