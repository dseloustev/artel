---
name: init-branch
description: "Bootstrap work on a ticket in one shot: make sure the ticket has a working branch (the current one when its name already carries a ticket ID; otherwise only what the user picks — never a branch created without asking), run the project's configured post-branch setup commands (setup.commands), restore that ticket's context via /artel:restore-context, and refresh CLAUDE.md via /init. Use when starting work on a ticket, or whenever someone says 'set up a branch for this ticket', 'init the branch', 'prepare a branch for work', or 'start work on <ticket>'."
argument-hint: "[ticket-id]"
disable-model-invocation: true
model: sonnet
---

## Overview

`init-branch` takes a ticket from "nothing local yet" to "work happening on a branch for it, with
its prior context restored and `CLAUDE.md` refreshed" in one shot: check the current branch (and
only when it carries no ticket ID, ask which branch to work on), run the configured post-branch
setup commands, restore the ticket's spec trail from the context store, and reconcile `CLAUDE.md`
against the current tree.

It is a **worker that runs inline** (like `sync-phases` / `generate-idea`) — it runs `git` and
chains a couple of sub-skills directly rather than delegating to an agent. Because it mutates the
working tree (may switch or create a branch, restores files, rewrites `CLAUDE.md`), it is
user-invoked only (`disable-model-invocation: true`) — never auto-triggered.

## Ticket Resolution

Parse `$0` into `TICKET_ID`, `TICKET_NUM`, `PHASE_NUM` per `${CLAUDE_PLUGIN_ROOT}/docs/orchestrator-common.md` §2 and `${CLAUDE_PLUGIN_ROOT}/docs/ticket-parsing.md` §§1–2. If `$0` is empty, read the first non-empty line of `<specs.dir>/.active_ticket`; if no identifier is available, error with "Error: No ticket specified. Provide a ticket ID as a parameter or set it in <specs.dir>/.active_ticket" and terminate.

The branch check compares against `TICKET_ID`, so this skill always needs one — unlike
`save-context` / `restore-context`, it has no ticket-less mode.

## Steps

Run the steps in order. Each later step assumes the previous one succeeded — see the per-step
failure handling for when to stop.

### Step 1: Check the current branch

1. `git branch --show-current` → `CURRENT_BRANCH` (empty output means a detached HEAD).
2. Scan `CURRENT_BRANCH` for a ticket token: `<ticket.projectKey>-\d+`, case-insensitive, the key
   not preceded by a letter or digit — the same token `pr-create` and `address-pr-comment` scan
   branch names for (`${CLAUDE_PLUGIN_ROOT}/docs/config.md`). The first match wins; canonicalize
   it to `<ticket.projectKey>-<digits>` → `BRANCH_TICKET`. A detached HEAD has no token.
3. Route on the result — compare ticket IDs only; a phase number in the branch name is not
   compared, so a phase-2 run on the ticket's phase-1 branch stays:

| Current branch | Route |
|---|---|
| `BRANCH_TICKET` equals `TICKET_ID` | **Stay.** Run no git command. `BRANCH_NAME` = `CURRENT_BRANCH`. Go to Step 3. |
| `BRANCH_TICKET` is another ticket | **Stop.** Run no git command and no later step. Report: "Current branch `<CURRENT_BRANCH>` belongs to `<BRANCH_TICKET>`, not `<TICKET_ID>`. Switch to a branch for `<TICKET_ID>` (or to the default branch) and re-run." |
| No token (default branch, `develop`, `feature/some-topic`, detached HEAD) | Go to Step 2. |

### Step 2: Choose a branch (current branch carries no ticket ID)

1. Detect the repo's default branch the same way `${CLAUDE_PLUGIN_ROOT}/agents/reviewer.md`'s
   standalone mode and `${CLAUDE_PLUGIN_ROOT}/skills/add-automation/SKILL.md` do:
   `git symbolic-ref refs/remotes/origin/HEAD`, asking the user if ambiguous — never a hardcoded
   `main`/`develop`. Call the result `BASE_BRANCH`.
2. `git fetch origin` — best-effort; a failure here is non-fatal, fall back to local refs and
   note it in the report.
3. **Find existing branches for this ticket.** List
   `git for-each-ref --sort=-committerdate --format='%(refname:short)' refs/heads refs/remotes/origin`
   (most recent commit first), apply Step 1's token scan to each name, and keep those whose token
   equals `TICKET_ID` (skip `origin/HEAD`). Record each as its bare branch name (`origin/`
   stripped), noting whether it exists locally; a branch present both locally and on `origin` is
   one entry, at the position of its first appearance. This is how a re-run finds the branch an
   earlier run created, whatever its slug. Call the result `EXISTING`, in that order.
4. **Propose a new name** `NEW_BRANCH`: `feature/<TICKET_ID>-<slug>` when `PHASE_NUM` is null,
   `feature/<TICKET_ID>-<PHASE_NUM>-<slug>` when set. Never a hardcoded project key —
   `TICKET_ID` already carries `ticket.projectKey`. The slug comes from the first available source:
   - the tracker summary — `tracker.adapter: "jira-mcp"` → `<tracker.mcpToolPrefix>jira_get_issue`
     with `issueIdOrKey: TICKET_ID`, field `summary`; `"github-issues"` →
     `gh issue view <TICKET_NUM> --json title`. A failed or unavailable fetch falls through;
   - the first heading of `<specs.dir>/<TICKET_ID>/idea.md`;
   - otherwise ask the user for a two-to-five-word description of the work.

   Shape the slug as short English kebab-case: translate a non-English summary (never
   transliterate), lowercase, ASCII letters and digits only, words joined by single `-`, the
   ticket key and filler words dropped, at most five words. "Добавление аккаунтов" →
   `adding-accounts`.
5. **Ask once** via `AskUserQuestion` (header `Branch`), options in this order:
   - **Check out `<name>`** — one option per `EXISTING` entry, the first two only (if `NEW_BRANCH`
     equals a later entry, that entry replaces the second); the first is marked recommended. None
     when `EXISTING` is empty;
   - **Create `<NEW_BRANCH>`** — from `origin/<BASE_BRANCH>`; recommended when `EXISTING` is empty.
     Omitted when `NEW_BRANCH` equals an `EXISTING` name — that branch is offered for checkout
     instead;
   - **Stay on `<CURRENT_BRANCH>`** (or "Stay on the detached HEAD") — no branch change.

   The automatic free-text answer is a branch name the user wants instead.
6. Act on the answer:
   - **Check out** a local branch: `git checkout <name>`. A branch only on `origin`:
     `git checkout -b <name> origin/<name>` (tracking).
   - **Create:** `git checkout -b <NEW_BRANCH> origin/<BASE_BRANCH>` (fallback: the local
     `<BASE_BRANCH>` ref if the remote one is unavailable).
   - **A typed name:** check it out if it already exists locally or on `origin` (as above);
     otherwise create it from `origin/<BASE_BRANCH>`.
   - **Stay:** run no git command. `BRANCH_NAME` = `CURRENT_BRANCH`. Continue with Step 3 — the
     setup, restore and `/init` are still useful on the current branch.

   Set `BRANCH_NAME` to the branch now checked out.
7. **The checkout is fatal on failure.** Any `git` error in step 6 — including one caused by
   uncommitted changes that conflict with the target branch — halts the skill; report the command
   and its output. A branch this skill is meant to bootstrap can't be worked on without a valid
   checkout.

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

- Ticket used and what happened to the branch: stayed on `BRANCH_NAME` (it already carried the
  ticket), checked out existing `BRANCH_NAME`, created `BRANCH_NAME` from `BASE_BRANCH`, or stayed
  on a non-ticket branch at the user's choice.
- Whether the setup commands ran or were skipped (none configured).
- What Step 4 restored (ticket artifacts vs. common root files only, or "nothing found — new
  ticket").
- Whether `/init` changed `CLAUDE.md`.
- Whether the index was refreshed, skipped (no hook wired up), or not applicable.
- Any follow-up the user should know about — e.g. if `/init` modified `CLAUDE.md`, mention that
  `/artel:save-context` will save that change back into the context store (this skill intentionally
  does not, to keep its scope to setup only).

## Rules

- **Never create a branch without asking.** The only path that switches or creates a branch is
  the user's answer in Step 2. A current branch carrying this ticket's ID is used as-is, whatever
  its prefix, phase or slug; one carrying another ticket's ID stops the skill.
- **Scope is setup, nothing else.** This skill settles the branch, restores context, refreshes
  `CLAUDE.md`, and refreshes the index. It does not commit, push, run the quality gate, or save
  changes back to the context store — those are separate, deliberate operations
  (`/artel:save-context` for the last one).
- **Order matters and is fixed:** branch → setup commands → restore → `/init` → reindex. `/init`
  runs after the restore so it refines the restored `CLAUDE.md`; the reindex runs last so it picks
  up whatever the restore or `/init` changed.
- **Stop on a fatal step.** Another ticket's branch (Step 1), a failed checkout (Step 2), a failed
  setup command (Step 3), or a missing context store (Step 4) halts the skill — report and stop. A
  ticket with no saved artifacts (Step 4 warning) and a reindex error (Step 6) are non-fatal; log
  and continue.
- **Never reimplement the restore.** Delegate to `restore-context`; that skill owns the store copy
  logic and the store is authoritative.
- **Idempotent — safe to re-run.** A re-run on the branch an earlier run created or checked out
  takes Step 1's stay route — no git command — then re-runs restore/`init`/reindex in place;
  `restore-context` overwrites with identical store content, `/init` reconciles in place, and an
  index refresh is incremental.

## Examples

- On `feature/PROJ-3085-adding-accounts`: `init-branch PROJ-3085` — stays on it (no git change),
  then setup, restore, `/init`, reindex.
- On `main`: `init-branch 3085` (numeric form normalized via `ticket.pattern`) — no ticket in the
  branch name, so it asks: check out an existing `feature/PROJ-3085-…` branch if one exists,
  create `feature/PROJ-3085-adding-accounts` (slug from the tracker summary), or stay on `main`.
- On `main`: `init-branch PROJ-3085-2` — offers `feature/PROJ-3085-2-<slug>`; `restore-context` is
  still invoked with the ticket-wide `TICKET_ID` (`PROJ-3085`) since the store is ticket-scoped, not
  phase-scoped.
- On `feature/PROJ-1000-fix-fee`: `init-branch PROJ-3085` — stops: the branch belongs to
  `PROJ-1000`.
