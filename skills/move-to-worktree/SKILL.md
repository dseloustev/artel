---
name: move-to-worktree
description: "Move the current ticket's work — its branch, uncommitted changes, artel config and run state — out of the main checkout into its own git worktree under .claude/worktrees/, and continue this session there, so other sessions can work on other tickets in parallel. Use when the user asks to move work to a worktree, isolate a ticket, or run several tickets side by side. For a ticket with no branch yet, /artel:init-branch sets it up and offers the same move."
argument-hint: "[ticket-id]"
disable-model-invocation: true
model: sonnet
---

## Overview

`move-to-worktree` moves a ticket that is already on its branch in the main checkout into
`.claude/worktrees/<name>` and switches this session into it. The contract — what moves, where,
the statuses — is `${CLAUDE_PLUGIN_ROOT}/docs/worktrees.md`; the mechanics are
`${CLAUDE_PLUGIN_ROOT}/scripts/worktree.py`.

It is a **worker that runs inline**: it runs `git` and the script directly, no agent. It moves the
session and changes two checkouts, so it is user-invoked only (`disable-model-invocation: true`).
The way back is `/artel:return-from-worktree`.

## Ticket Resolution

Parse `$0` into `TICKET_ID`, `TICKET_NUM`, `PHASE_NUM` per `${CLAUDE_PLUGIN_ROOT}/docs/orchestrator-common.md` §2 and `${CLAUDE_PLUGIN_ROOT}/docs/ticket-parsing.md` §§1–2. If `$0` is empty, read the first non-empty line of `<specs.dir>/.active_ticket`; if no identifier is available, error with "Error: No ticket specified. Provide a ticket ID as a parameter or set it in <specs.dir>/.active_ticket" and terminate.

`<name>` is `TICKET_ID`, or `<TICKET_ID>-<PHASE_NUM>` when `PHASE_NUM` is set.

## Steps

### Step 1: Preconditions

1. **Not already in a worktree.** If `git rev-parse --path-format=absolute --git-dir` differs from
   `git rev-parse --path-format=absolute --git-common-dir`, stop: "This session is already in a
   worktree — run `/artel:move-to-worktree` from the main checkout."
2. **An existing worktree is entered, not recreated.** If `git worktree list --porcelain` lists
   `<repo root>/.claude/worktrees/<name>`, skip to Step 3 with that path.
3. **The branch carries the ticket.** `git branch --show-current` → `CURRENT_BRANCH`. Scan it for
   `TICKET_ID` exactly as `${CLAUDE_PLUGIN_ROOT}/skills/init-branch/SKILL.md` Step 1 does. No
   match (another ticket, no ticket, detached HEAD) → stop: "`<CURRENT_BRANCH>` is not a branch
   for `<TICKET_ID>`. Run `/artel:init-branch <TICKET_ID>` — it sets up the branch and offers the
   move to a worktree."
4. **Base branch.** Detect it as `init-branch` Step 2.1 does (`git symbolic-ref
   refs/remotes/origin/HEAD`, asking if ambiguous) → `BASE_BRANCH`.

### Step 2: Confirm and move

1. Count the uncommitted paths: `git status --porcelain --untracked-files=all`.
2. Ask once via `AskUserQuestion` (header `Worktree`):
   - **Move to `.claude/worktrees/<name>`** (recommended) — description: "`<N>` uncommitted
     files move with it; the main checkout switches to `<BASE_BRANCH>`."
   - **Stay here** — stop without changes.
3. Run from the repo root:

   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/worktree.py move-in --ticket <TICKET_ID> --name <name> --base <BASE_BRANCH> --branch <CURRENT_BRANCH>
   ```

4. Act on `status` per `${CLAUDE_PLUGIN_ROOT}/docs/worktrees.md` §6. Anything but `ok` → report
   and stop; on `conflict`, name the stash commit ID, the conflicted files and the worktree path.

### Step 3: Enter the worktree

Call `EnterWorktree` with `path` set to the worktree path. If the call fails, report the path and
stop — the move itself is complete.

**OpenCode:** there is no `EnterWorktree`. Print `cd <path> && opencode`, skip Step 4, and report.

### Step 4: Run the post-branch setup commands

Now inside the worktree, run `setup.commands` exactly as `init-branch` Step 3 does
(`${CLAUDE_PLUGIN_ROOT}/docs/config.md`, "setup"): in order, from the worktree root, stop at the
first non-zero exit and report the command and its output. Dependencies and generated code are
per checkout and were not copied.

### Step 5: Report

- The worktree path and branch; whether it already existed.
- Which branch the main checkout is on now (`mainNowOn`; `null` means a detached HEAD).
- Whether uncommitted work moved (`stashApplied`), whether run state moved (`runMoved`), whether
  the context store is linked (`contextLinked`), and how many environment files were copied.
- Whether the setup commands ran, were skipped (none configured), or failed.
- The way back: `/artel:return-from-worktree <TICKET_ID>`.

## Rules

- **Never move without asking.** The only path that changes anything is the user's answer in
  Step 2 (an existing worktree is entered without a question — nothing moves).
- **Never lose work.** The script drops a stash only after it applied; on `conflict` the stash
  stays listed. Never any `--force` variant. Never delete a branch.
- **Never reimplement the script.** Every git step of the move lives in `worktree.py`; this skill
  decides, runs it, enters, and reports.
- **Idempotent.** A re-run for a ticket whose worktree exists just enters it.
