---
name: artel-bratishka-merge-approved
description: "Use when the Bratishka duty reviewer wants the QA-approved pull requests merged — shows the ready, needs-update and blocked sets, updates BEHIND branches, waits once for the merge state to settle, and squash-merges one PR at a time after confirmation. Never merges a PR whose QA state is unknown, a testing PR, a draft, or a PR blocked by its merge state."
argument-hint: "[--yes] [--pr <n>]"
---

# Merge the QA-approved PRs

This is a write. Check `.artel/config.json`: `vcs.adapter` must be `github-cli`,
otherwise stop and name the value. `gh` must be authenticated. The contract is
`${ARTEL_ROOT}/docs/bratishka-duty.md` (`## Merging`, `## Safety and degradation`).

Read the plan first:

```bash
python3 ${ARTEL_ROOT}/scripts/bratishka.py merge-plan --json
```

Exit `2` is a configuration/environment error: print `error.message` and stop.
Exit `0` means `data` has `repo`, `ready`, `needs_update`, `blocked` and `dashboard_state`. Each
entry carries `number`, `title`, `url`, `code_reviewed` and `qa_approved`; a blocked entry also
carries `reason`, and an entry with a live coder task carries `bot_working: true` and
`active_tasks` (task id, status, initiator).

## 1. Show the groups

Render three short tables — number, title and URL per entry, plus `reason` for a blocked
entry. An empty group is one line, "none", not a table. Say that `needs_update` means the
branch is behind its base and will be updated first. `data.repo` is `<repo>` for the `gh` calls
below, and each entry's `number` is `<n>`.

Choose the merge set:

- **A `bot_working` entry is never merged or updated.** Report its `active_tasks` with the task
  id and status and exclude it from the set. When `dashboard_state.online` is false, say the
  active-task check is unavailable and the board may show `bot-working` PRs it cannot see; do
  not merge an entry the board named.
- **No `--pr`** — every other `ready` entry, and every other `needs_update` entry to update and
  re-check first. A `blocked` entry is reported with its `reason` and never merged; it does not
  stop the ready set.
- **`--pr <n>`** — only that number may be updated or merged. A `ready` entry is the whole set;
  a `needs_update` entry is updated and re-checked first; a `blocked` entry is reported with its
  `reason` and the skill stops with nothing updated or merged; a `bot_working` entry is reported
  with its task id/status and the skill stops; an absent entry means the PR is not both
  code-reviewed and QA-approved (or it is a testing PR, a draft or excluded by title) — say so
  and stop.

A PR whose QA verdict is `unknown` is in none of the three groups. Never merge it: say that
`bratishka.teams.qa` (a readable team slug) or `bratishka.qaLogins` (the login fallback) must be
configured, and stop. The rule is in the contract's `## Approval model`.

When no entry is in `ready` or `needs_update` and no `--pr` was given, say there is nothing to
merge and stop.

## 2. Ask once

Unless `--yes` was passed, ask with the `question` tool: **Merge these PRs?** — options
**Merge them** / **Cancel**. The question names every PR number in the merge set and every
`needs_update` PR whose branch will be updated first. On **Cancel**, or with no answer, stop:
nothing was updated, merged or deleted. `--yes` is the person's explicit consent; never add it
on your own.

The confirmation freezes those numbers. A PR that joins the plan later is not merged; a PR that
leaves it is reported, not brought back.

## 3. Merge one PR at a time

Process the confirmed set in the displayed order:

1. **Re-read the plan before each PR:**

   ```bash
   python3 ${ARTEL_ROOT}/scripts/bratishka.py merge-plan --json
   ```

   The base moves after every merge, so a PR that was `ready` can be `needs_update` by the time
   its turn comes. If the re-read marks the PR `bot_working`, stop that PR: report its
   `active_tasks` (task id/status) and move on; never merge it.

2. **A `ready` PR — merge it:**

   ```bash
   gh pr merge <n> --repo <repo> --squash
   ```

   No `--delete-branch`: this path has no branch-deletion flag. Never pass `--merge`,
   `--rebase` or a branch-deletion flag.

3. **A `needs_update` PR — update, wait once, re-read once:**

   ```bash
   gh api -X PUT repos/<repo>/pulls/<n>/update-branch
   ```

   Never `gh pr update-branch`: `hooks/vcs_guard.py` classifies that subcommand as an unknown
   verb and denies it. Wait 30 seconds, then re-read the plan once. If the PR is now `ready`,
   merge it as in step 2. If it is still `needs_update`, `blocked` or absent after that single
   re-check, report the state and leave it for the next run; do not wait longer.

4. **A `blocked` PR, or one that has left the plan — never merge it.** Report `gh`'s `reason`
   (or that it left the queue) and move on to the next confirmed PR.

5. **A failed merge — report `gh`'s message and move on.** Do not retry, force-push or switch
   merge method; re-read the plan before the next PR.

After the confirmed set, re-read the plan once and report each remaining group.

## Rules

- Never merge a PR whose QA state is `unknown`. Name `bratishka.teams.qa` or
  `bratishka.qaLogins` as the key to add.
- Never merge a testing PR, a draft PR, a PR with only one of the two approvals, a PR the
  plan does not list, or a PR with an active/queued coder task (`bot_working`).
- Never delete a branch, force-push, or push to the base branch; `update-branch` through the
  REST endpoint is the only update.
- One PR at a time: re-read the plan after each merge so a newly `BEHIND` PR is updated before
  its own merge.
- `--pr` narrows the run to one PR; it never widens the merge set. `--yes` skips the question
  and is never added on your own.
- The engine merges nothing; every update and merge here is a visible `gh` call. The merge
  states, the bounded re-check and the no-deletion rule are in
  `${ARTEL_ROOT}/docs/bratishka-duty.md` (`## Merging`).
