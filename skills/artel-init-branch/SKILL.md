---
name: artel-init-branch
description: "Bootstrap work on a ticket in one shot: keep the current branch when its name already carries the ticket ID; otherwise propose candidate branch names, create the one the user picks on top of the base branch an optional second argument names (the repo's detected default branch when it is omitted), and then offer to continue in its own git worktree — followed by the project's configured post-branch setup commands (setup.commands), context restore via /artel-restore-context, and an AGENTS.md refresh via agents-md-generator. Use when starting work on a ticket, or whenever someone says 'set up a branch for this ticket', 'init the branch', 'prepare a branch for work', or 'start work on <ticket>'."
argument-hint: "[ticket-id] [base-branch]"
metadata:
  opencode/autoinvoke: false
---

## Overview

`init-branch` takes a ticket from "nothing local yet" to "work happening on a branch for it, with
its prior context restored and `AGENTS.md` refreshed" in one shot. Step 1 settles the branch: a
current branch whose name already carries the ticket's ID is used exactly as it is, and nothing is
asked. Only when the current branch carries no ticket ID does the skill propose candidate branch
names, create the name the user picks on top of the base branch the **second argument** names (the
repo's detected default branch when that argument is omitted), and then ask whether the work should
continue in its own git worktree.

It is a **worker that runs inline** (like `sync-phases` / `generate-idea`) — it runs `git` and
chains a couple of sub-skills directly rather than delegating to an agent. Because it mutates the
working tree (may switch or create a branch, may move the session into a worktree, restores
files, rewrites `AGENTS.md`), it is user-invoked only (`metadata.opencode/autoinvoke: false`) — never
auto-triggered.

## Ticket Resolution

Parse `$0` into `TICKET_ID`, `TICKET_NUM`, `PHASE_NUM` per `${ARTEL_ROOT}/docs/orchestrator-common.md` §2 and `${ARTEL_ROOT}/docs/ticket-parsing.md` §§1–2. If `$0` is empty, read the first non-empty line of `<specs.dir>/.active_ticket`; if no identifier is available, error with "Error: No ticket specified. Provide a ticket ID as a parameter or set it in <specs.dir>/.active_ticket" and terminate.

`$1` is the optional **base branch** the new branch is created from → `BASE_ARG` (empty when
absent). The branch check compares against `TICKET_ID`, so this skill always needs a ticket ID —
unlike `save-context` / `restore-context`, it has no ticket-less mode.

## Steps

Run the steps in order. Each later step assumes the previous one succeeded — see the per-step
failure handling for when to stop.

### Step 1: Check the current branch

1. `git branch --show-current` → `CURRENT_BRANCH` (empty output means a detached HEAD).
2. Scan `CURRENT_BRANCH` for a ticket token: `<ticket.projectKey>-\d+`, case-insensitive, the key
   not preceded by a letter or digit — the same token `pr-create` and `address-pr-comment` scan
   branch names for (`${ARTEL_ROOT}/docs/config.md`). The first match wins; canonicalize
   it to `<ticket.projectKey>-<digits>` → `BRANCH_TICKET`. A detached HEAD has no token.
3. Route on the result — compare ticket IDs only; a phase number in the branch name is not
   compared, so a phase-2 run on the ticket's phase-1 branch stays:

| Current branch | Route |
|---|---|
| `BRANCH_TICKET` equals `TICKET_ID` | **Stay.** Run no git command. `BRANCH_NAME` = `CURRENT_BRANCH`. Go to Step 2b, then Step 3. |
| `BRANCH_TICKET` is another ticket | **Stop.** Run no git command and no later step. Report: "Current branch `<CURRENT_BRANCH>` belongs to `<BRANCH_TICKET>`, not `<TICKET_ID>`. Switch to a branch for `<TICKET_ID>` (or to the default branch) and re-run." |
| No token (default branch, `develop`, `feature/some-topic`, detached HEAD) | Go to Step 2. |

### Step 2: Pick a branch name and create the branch

Reached only when the current branch carries no ticket ID. The user chooses the branch name; the
**second argument**, not a guess, decides what it sits on.

1. **Settle the base.** → `BASE_BRANCH` (a branch name, no remote prefix) and `BASE_REF` (the ref
   the new branch is created from).
   - `git fetch origin` first — best-effort; a failure here is non-fatal, fall back to local refs
     and note it in the report.
   - `BASE_ARG` given: strip a leading `origin/` → `BASE_BRANCH`. `BASE_REF` is
     `origin/<BASE_BRANCH>` when `git rev-parse --verify --quiet origin/<BASE_BRANCH>` resolves,
     else `<BASE_BRANCH>` when the local ref resolves. **Neither resolving is fatal** — run no git
     command: report "Base branch `<BASE_ARG>` (the second argument) does not exist locally or on
     `origin`. Check the name and re-run." and stop.
   - `BASE_ARG` empty: detect the repo's default branch the same way
     `${ARTEL_ROOT}/agents/artel-reviewer.md`'s standalone mode does:
     `git symbolic-ref refs/remotes/origin/HEAD`, asking the user if ambiguous — never a hardcoded
     `main`/`develop`/`master`. `BASE_REF` is `origin/<BASE_BRANCH>` when that ref exists, else the
     local `<BASE_BRANCH>`.
2. **Analyse the task and propose candidate names.** The slug comes from the first available
   source:
   - the tracker summary — `tracker.adapter: "jira-mcp"` → `<tracker.mcpToolPrefix>jira_get_issue`
     with `issueIdOrKey: TICKET_ID`, field `summary`; `"github-issues"` →
     `gh issue view <TICKET_NUM> --json title`. A failed or unavailable fetch falls through;
   - the first heading of `<specs.dir>/<TICKET_ID>/idea.md`;
   - otherwise ask the user for a two-to-five-word description of the work.

   Shape the slug as short English kebab-case: translate a non-English summary (never
   transliterate), lowercase, ASCII letters and digits only, words joined by single `-`, the
   ticket key and filler words dropped, at most five words. "Добавление аккаунтов" →
   `adding-accounts`. Offer **two to three** candidates, the first always
   `feature/<TICKET_ID>-<slug>` — `feature/<TICKET_ID>-<PHASE_NUM>-<slug>` when `PHASE_NUM` is set.
   Never a hardcoded project key — `TICKET_ID` already carries `ticket.projectKey`.
3. **Find existing branches for this ticket first.** List
   `git for-each-ref --sort=-committerdate --format='%(refname:short)' refs/heads refs/remotes/origin`
   (most recent commit first), apply Step 1's token scan to each name, and keep those whose token
   equals `TICKET_ID` (skip `origin/HEAD`). Record each as its bare branch name (`origin/`
   stripped), noting whether it exists locally; a branch present both locally and on `origin` is
   one entry, at the position of its first appearance. Call the result `EXISTING`, in that order.
   This is what keeps a re-run from creating a second branch for the same ticket. Note which
   entries are checked out in a linked worktree (`git worktree list --porcelain`) — git refuses
   to check those out a second time.
4. **Ask once** via the `question` tool (header `Branch`) for the name to work on, options in this
   order:
   - **Check out `<name>`** — one option per `EXISTING` entry, the first two only; the first is
     recommended. None when `EXISTING` is empty. An entry checked out in a linked worktree is
     offered as **Enter worktree `<path>`** instead.
   - **Create `<candidate>`** — one option per proposed candidate not already in `EXISTING`;
     recommended when `EXISTING` is empty.

   The automatic free-text answer is a branch name the user wants instead. Do not offer to stay on
   `CURRENT_BRANCH`: a run that reaches this step always settles on a ticket branch.
5. **Create or check out the chosen name.** → `BRANCH_NAME`.
   - **Enter worktree `<path>`** (an `EXISTING` entry git already checks out elsewhere): print
     `cd <path> && opencode` and stop — re-running this skill in that session takes Step 1's Stay
     route and finishes the setup there.
   - **Check out** an `EXISTING` local branch: `git checkout <name>`. One only on `origin`:
     `git checkout -b <name> origin/<name>` (tracking).
   - **Create** a chosen candidate: `git checkout -b <NEW_BRANCH> <BASE_REF>` — the second
     argument's branch when it was given, otherwise the detected default. Fall back to the local
     `<BASE_BRANCH>` only when `origin/<BASE_BRANCH>` is unavailable.
   - **A typed name** that already exists locally or on `origin` is checked out, never re-created;
     otherwise it is created exactly like a candidate, from `<BASE_REF>`.

   **The checkout/creation is fatal on failure.** Any `git` error here — including one caused by
   uncommitted changes that conflict with the target branch — halts the skill; report the command
   and its output. A branch this skill is meant to bootstrap can't be worked on without a valid
   checkout.

   Set `BRANCH_NAME` to the branch now checked out, then go to Step 2b.

### Step 2b: Move to a worktree (optional)

Reached from Step 1's Stay route or after Step 2 put the session on `BRANCH_NAME`; the procedure
is `${ARTEL_ROOT}/docs/worktrees.md` §4.

1. **Ask.** Inside a linked worktree, skip the rest of this step — go straight
   to Step 3. Otherwise detect `BASE_BRANCH` as Step 2.1 does when Step 2 did not already settle it
   (the Stay route), and ask the worktree question on its own via the `question` tool
   (header `Worktree`): **Work here** / **Move to `.opencode/worktrees/<name>`**, where `<name>` is
   `TICKET_ID`, or `TICKET_ID-PHASE_NUM` when `PHASE_NUM` is set.

   **Work here** → the branch is already checked out; go to Step 3.
2. **Run** from the main checkout. The branch already exists locally at this point, so no
   `--create-from` is needed:

   ```bash
   python3 ${ARTEL_ROOT}/scripts/worktree.py move-in --ticket <TICKET_ID> --name <name> --base <BASE_BRANCH> --branch <BRANCH_NAME>
   ```

3. **Fatal unless `ok`**, like a failed checkout: report per `docs/worktrees.md` §6 and stop. On
   `ok` the main checkout is back on `BASE_BRANCH` (`mainNowOn`) and the worktree holds
   `BRANCH_NAME`.
4. **Enter:** the session cannot move into the worktree — print `cd <path> && opencode`, skip
   Steps 3–6 and report. Re-running `/artel-init-branch` in that session takes Step 1's Stay
   route and finishes the setup there.

### Step 3: Run the post-branch setup commands

Read `setup.commands` (`${ARTEL_ROOT}/docs/config.md`, "setup" section). Empty or absent
→ skip silently. Otherwise run the commands in order from the checkout root (the worktree's,
after Step 2b), stopping at the first non-zero exit. **A failure here is fatal**, like Step 2: a
branch whose dependencies did not install can't be worked on — report the failing command and
its output and stop. Note the outcome (ran / skipped) for the report.

### Step 4: Restore the ticket's context

Invoke the `artel-restore-context` skill with `TICKET_ID` as its argument. This copies `AGENTS.md`,
`CHANGELOG.md`, and that ticket's `<specs.dir>/<TICKET_ID>/` artifacts back out of the
`.artel/context/` store (`${ARTEL_ROOT}/skills/artel-restore-context/SKILL.md`).

- `restore-context` **warns but does not abort** when the ticket has no matching artifacts in the
  store (e.g. a brand-new ticket that was never saved) — that is not a failure for us either;
  common root files are still restored. Carry on.
- **This step's one hard failure is a missing `.artel/context/` store entirely** (`restore-context`
  exits 1) — stop and report: there is no store to restore from, so surface the message and let the
  user decide whether to continue without it (e.g. a brand-new repo where nothing has ever been
  saved).

> If the harness declines to invoke `restore-context` from inside this skill, say so plainly and
> ask the user to run `/artel-restore-context <TICKET_ID>` themselves, then continue from Step 5.
> Do **not** reimplement the restore by hand — the context store is the source of truth and the
> copy logic lives in that one skill on purpose.

### Step 5: Refresh `AGENTS.md`

Invoke the `artel-agents-md-generator` skill. Run it unconditionally — even though Step 4 may have just
restored `AGENTS.md` from the store, the refresh reconciles it against the *current* state of the
codebase (commands, structure, conventions that may have drifted on this branch). Running it
after the restore means it refines the canonical file rather than starting from nothing.

If the refresh makes no changes, that's a fine outcome — note it and move on.

### Step 6: Refresh the code-symbol index (optional host hook)

If the host project maintains a code-symbol index, refresh it now — the same optional host hook
`${ARTEL_ROOT}/docs/orchestrator-common.md` §1 describes ("Index refresh"), not an
`.artel/config.json` key. Silently skip when the host has not wired one up; nothing is reported as
missing. Non-fatal either way — a stale or absent index doesn't block the branch from being usable.

### Step 7: Report

Print a concise summary:

- Ticket used and what happened to the branch: stayed on `BRANCH_NAME` (it already carried the
  ticket), checked out existing `BRANCH_NAME`, or created `BRANCH_NAME` from `<BASE_BRANCH>` —
  naming the base and whether it came from the second argument or the detected default.
- Where the work continues: the main checkout, or the worktree path (moved now, or entered) — with
  `/artel-return-from-worktree <TICKET_ID>` as the way back, and which branch the main checkout is
  on now.
- Whether the setup commands ran or were skipped (none configured).
- What Step 4 restored (ticket artifacts vs. common root files only, or "nothing found — new
  ticket").
- Whether the refresh changed `AGENTS.md`.
- Whether the index was refreshed, skipped (no hook wired up), or not applicable.
- Any follow-up the user should know about — e.g. if the refresh modified `AGENTS.md`, mention that
  `/artel-save-context` will save that change back into the context store (this skill intentionally
  does not, to keep its scope to setup only).

## Rules

- **Never create a branch or a worktree without asking.** The only paths that switch or create a
  branch are the user's answers in Step 2; the only path into a worktree is the user's answer in
  Step 2b. A current branch carrying this ticket's ID is used as-is, whatever its prefix, phase or
  slug; one carrying another ticket's ID stops the skill.
- **The base branch is the second argument or the detected default — never a guess.** A supplied
  `BASE_ARG` that resolves nowhere stops the skill rather than silently falling back to another
  branch, and no `main` / `develop` / `master` is hardcoded.
- **A branch that exists is adopted, never re-created.** `EXISTING` branches for the ticket are
  offered first, and a typed name that already exists is checked out — this is what makes a re-run
  safe.
- **Scope is setup, nothing else.** This skill settles the branch (and, when asked, its
  worktree), restores context, refreshes `AGENTS.md`, and refreshes the index. It does not commit,
  push, run the quality gate, or save changes back to the context store — those are separate,
  deliberate operations (`/artel-save-context` for the last one).
- **Order matters and is fixed:** branch → worktree → setup commands → restore → refresh →
  reindex. The worktree question comes only after the branch exists, and the move before setup so
  dependencies and generated code land in the checkout that will use them. The refresh runs after
  the restore so it refines the restored `AGENTS.md`; the reindex runs last so it picks up whatever
  the restore or the refresh changed.
- **Stop on a fatal step.** Another ticket's branch (Step 1), a base branch that resolves nowhere
  (Step 2.1), a failed checkout or creation (Step 2), a worktree move that did not end `ok`
  (Step 2b), a failed setup command (Step 3), or a missing context store (Step 4) halts the skill —
  report and stop. A ticket with no saved artifacts (Step 4 warning) and a reindex error (Step 6)
  are non-fatal; log and continue.
- **Never reimplement the restore.** Delegate to `restore-context`; that skill owns the store copy
  logic and the store is authoritative.
- **Idempotent — safe to re-run.** A re-run on the branch an earlier run created or checked out
  takes Step 1's stay route — no git command, and no worktree question inside a worktree — then
  re-runs restore/refresh/reindex in place; `restore-context` overwrites with identical store
  content, the refresh reconciles in place, and an index refresh is incremental.

## Examples

- On `feature/PROJ-3085-adding-accounts`: `init-branch PROJ-3085` — stays on it (no git change),
  then the worktree question, setup, restore, refresh, reindex.
- On `main`: `init-branch 3085` (numeric form normalized via `ticket.pattern`) — no ticket in the
  branch name, so it proposes `feature/PROJ-3085-adding-accounts` (slug from the tracker summary)
  among its candidates; the user picks one, it is created from the detected default branch, and the
  skill then asks whether to move into `.opencode/worktrees/PROJ-3085`.
- On `main`: `init-branch PROJ-3085 release/2.4` — the chosen branch is created from
  `origin/release/2.4` instead of the default branch; an unknown `release/2.4` stops the skill
  before any git command runs.
- On `main`: `init-branch PROJ-3085-2` — offers `feature/PROJ-3085-2-<slug>`; `restore-context` is
  still invoked with the ticket-wide `TICKET_ID` (`PROJ-3085`) since the store is ticket-scoped, not
  phase-scoped.
- On `feature/PROJ-1000-fix-fee`: `init-branch PROJ-3085` — stops: the branch belongs to
  `PROJ-1000`.
- On `main`: `init-branch 3085`, picking a candidate and answering **Move to
  `.opencode/worktrees/PROJ-3085`** — the branch is created in the main checkout, `move-in` returns
  the main checkout to `BASE_BRANCH`, this session moves into the worktree, and setup, restore,
  refresh and reindex run there.
