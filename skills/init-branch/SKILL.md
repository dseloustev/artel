---
name: init-branch
description: "Bootstrap a feature branch for a ticket in one shot: derive the branch name from the canonical TICKET_ID, create and check it out from the repo's detected base branch, run the project's configured post-branch setup commands (setup.commands), restore that ticket's context via /artel:restore-context, and refresh CLAUDE.md via /init. Use when starting work on a ticket, or whenever someone says 'set up a branch for this ticket', 'init the branch', 'prepare a branch for work', or 'start work on <ticket>'."
argument-hint: "[ticket-id]"
disable-model-invocation: true
model: sonnet
---

## Overview

`init-branch` takes a ticket from "nothing local yet" to "a checked-out branch with its prior
context restored and `CLAUDE.md` refreshed" in one shot: derive the branch name from the resolved
`TICKET_ID`, create/check it out from the repo's detected base branch, run the configured
post-branch setup commands, restore the ticket's spec trail from the context store, and reconcile
`CLAUDE.md` against the current tree.

It is a **worker that runs inline** (like `sync-phases` / `generate-idea`) — it runs `git` and
chains a couple of sub-skills directly rather than delegating to an agent. Because it mutates the
working tree (creates/switches branches, restores files, rewrites `CLAUDE.md`), it is user-invoked
only (`disable-model-invocation: true`) — never auto-triggered.

## Ticket Resolution

Parse `$0` into `TICKET_ID`, `TICKET_NUM`, `PHASE_NUM` per `${CLAUDE_PLUGIN_ROOT}/docs/orchestrator-common.md` §2 and `${CLAUDE_PLUGIN_ROOT}/docs/ticket-parsing.md` §§1–2. If `$0` is empty, read the first non-empty line of `<specs.dir>/.active_ticket`; if no identifier is available, error with "Error: No ticket specified. Provide a ticket ID as a parameter or set it in <specs.dir>/.active_ticket" and terminate.

A branch name derives from `TICKET_ID`, so this skill always needs one — unlike `save-context` /
`restore-context`, it has no ticket-less mode.

## Steps

Run the steps in order. Each later step assumes the previous one succeeded — see the per-step
failure handling for when to stop.

### Step 1: Determine the base branch and derive the branch name

1. Detect the repo's default branch the same way `${CLAUDE_PLUGIN_ROOT}/agents/reviewer.md`'s
   standalone mode and `${CLAUDE_PLUGIN_ROOT}/skills/add-automation/SKILL.md` do:
   `git symbolic-ref refs/remotes/origin/HEAD`, asking the user if ambiguous — never a hardcoded
   `main`/`develop`. Call the result `BASE_BRANCH`.
2. Derive `BRANCH_NAME`: `feature/<TICKET_ID>` when `PHASE_NUM` is null, `feature/<TICKET_ID>-<PHASE_NUM>`
   when set. Never a hardcoded project key — `TICKET_ID` already carries `ticket.projectKey`
   (`${CLAUDE_PLUGIN_ROOT}/docs/config.md`).

### Step 2: Create or check out the branch

1. `git fetch origin` — best-effort; a failure here is non-fatal, fall back to the local
   `BASE_BRANCH` ref and note it in the report.
2. If `BRANCH_NAME` already exists locally (`git show-ref --verify --quiet refs/heads/<BRANCH_NAME>`):
   `git checkout <BRANCH_NAME>` — resuming work on an already-started branch is the common case,
   not an error.
3. Else if it exists on `origin` (`git ls-remote --exit-code --heads origin <BRANCH_NAME>`):
   `git checkout -b <BRANCH_NAME> origin/<BRANCH_NAME>` (tracking).
4. Else: `git checkout -b <BRANCH_NAME> origin/<BASE_BRANCH>` (fallback: the local `<BASE_BRANCH>`
   ref if the remote one is unavailable).
5. **This step is fatal on failure.** Any `git` error here — including one caused by uncommitted
   changes that conflict with the target branch — halts the skill; report the command and its
   output. A branch this skill is meant to bootstrap can't be worked on without a valid checkout.

### Step 3: Run the post-branch setup commands

Read `setup.commands` (`${CLAUDE_PLUGIN_ROOT}/docs/config.md`, "setup" section). Empty or absent
→ skip silently. Otherwise run the commands in order from the repo root, stopping at the first
non-zero exit. **A failure here is fatal**, like Step 2: a branch whose dependencies did not
install can't be worked on — report the failing command and its output and stop. Note the
outcome (ran / skipped) for the report.

### Step 4: Restore the ticket's context

Invoke `Skill: restore-context` with `TICKET_ID` as its argument. This copies `CLAUDE.md`,
`CHANGELOG.md`, and that ticket's `<specs.dir>/<TICKET_ID>/` artifacts back out of the
`.artel/context/` store (`${CLAUDE_PLUGIN_ROOT}/skills/restore-context/SKILL.md`).

- `restore-context` **warns but does not abort** when the ticket has no matching artifacts in the
  store (e.g. a brand-new ticket that was never saved) — that is not a failure for us either;
  common root files are still restored. Carry on.
- **This step's one hard failure is a missing `.artel/context/` store entirely** (`restore-context`
  exits 1) — stop and report: there is no store to restore from, so surface the message and let the
  user decide whether to continue without it (e.g. a brand-new repo where nothing has ever been
  saved).

> If the harness declines to invoke `restore-context` from inside this skill, say so plainly and
> ask the user to run `/artel:restore-context <TICKET_ID>` themselves, then continue from Step 5.
> Do **not** reimplement the restore by hand — the context store is the source of truth and the
> copy logic lives in that one skill on purpose.

### Step 5: Refresh `CLAUDE.md`

Invoke `Skill: init`. Run it unconditionally — even though Step 4 may have just restored
`CLAUDE.md` from the store, `/init` reconciles it against the *current* state of the codebase
(commands, structure, conventions that may have drifted on this branch). Running it after the
restore means it refines the canonical file rather than starting from nothing.

If `/init` makes no changes, that's a fine outcome — note it and move on.

### Step 6: Refresh the code-symbol index (optional host hook)

If the host project maintains a code-symbol index, refresh it now — the same optional host hook
`${CLAUDE_PLUGIN_ROOT}/docs/orchestrator-common.md` §1 describes ("Index refresh"), not an
`.artel/config.json` key. Silently skip when the host has not wired one up; nothing is reported as
missing. Non-fatal either way — a stale or absent index doesn't block the branch from being usable.

### Step 7: Report

Print a concise summary:

- Ticket used, branch created/checked-out (`BRANCH_NAME`), and the base branch it came from.
- Whether the setup commands ran or were skipped (none configured).
- What Step 4 restored (ticket artifacts vs. common root files only, or "nothing found — new
  ticket").
- Whether `/init` changed `CLAUDE.md`.
- Whether the index was refreshed, skipped (no hook wired up), or not applicable.
- Any follow-up the user should know about — e.g. if `/init` modified `CLAUDE.md`, mention that
  `/artel:save-context` will save that change back into the context store (this skill intentionally
  does not, to keep its scope to setup only).

## Rules

- **Scope is setup, nothing else.** This skill creates/checks out the branch, restores context,
  refreshes `CLAUDE.md`, and refreshes the index. It does not commit, push, run the quality gate, or
  save changes back to the context store — those are separate, deliberate operations
  (`/artel:save-context` for the last one).
- **Order matters and is fixed:** branch → setup commands → restore → `/init` → reindex. `/init`
  runs after the restore so it refines the restored `CLAUDE.md`; the reindex runs last so it picks
  up whatever the restore or `/init` changed.
- **Stop on a fatal step.** A failed checkout (Step 2), a failed setup command (Step 3), or a
  missing context store (Step 4) halts the skill — report and stop. A ticket with no saved
  artifacts (Step 4 warning) and a reindex error (Step 6) are non-fatal; log and continue.
- **Never reimplement the restore.** Delegate to `restore-context`; that skill owns the store copy
  logic and the store is authoritative.
- **Idempotent — safe to re-run.** Re-running on an already-checked-out branch just re-runs
  restore/`init`/reindex in place; `restore-context` overwrites with identical store content,
  `/init` reconciles in place, and an index refresh is incremental.

## Examples

- `init-branch PROJ-3085` — create/check out `feature/PROJ-3085` from the detected base branch,
  restore its context, refresh `CLAUDE.md`, reindex.
- `init-branch 3085` — same (numeric form is normalized via `ticket.pattern`).
- `init-branch PROJ-3085-2` — phase-scoped: branch `feature/PROJ-3085-2`; `restore-context` is
  still invoked with the ticket-wide `TICKET_ID` (`PROJ-3085`) since the store is ticket-scoped, not
  phase-scoped.
