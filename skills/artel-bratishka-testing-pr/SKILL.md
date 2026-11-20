---
name: artel-bratishka-testing-pr
description: "Use when the Bratishka duty reviewer wants today's testing pull request built — selects the source PRs that have code review but not QA approval, shows the plan, then has the engine build the branch, run the host's verification, push it and open the PR with the exact planned title and body; finally reads the PR back and reports it. The PR is never created by a bot task, never renamed and never merged."
argument-hint: "[--dry-run] [--yes]"
---

# Build today's testing PR

The daily QA vehicle: one PR bundling the source PRs that have code review but not QA
approval. It lives one day, and the next duty reviewer closes it
(`${ARTEL_ROOT}/docs/bratishka-duty.md`, `## Testing PR`).

**The engine opens it.** `${ARTEL_ROOT}/scripts/bratishka.py testing-pr-local` assembles the branch
in a throwaway worktree under `.artel/run/bratishka/`, merges the source heads, runs the host's
verification commands, pushes, and opens the PR with the planned title and body. No bot task is
involved, and that is deliberate: a coder agent carries naming rules of its own, and one that
requires a ticket-key prefix — with its own value when it is given none — rewrites the title to
satisfy it, because its completion gate refuses to end on a red check. The title is a duty
contract, not an agent's guess, so the agent never gets to write it.

Every engine call prints one envelope. Exit `2` is a configuration or environment error: print
`error.message` and stop.

## Prerequisites

Prove each with one call; nothing starts on a guess.

1. **`vcs.adapter` is `github-cli`** and `gh auth status` passes — every read and the PR itself go
   through `gh` (`${ARTEL_ROOT}/docs/config.md`, adapter identity checks). A failure stops the run
   naming the account or the adapter value.
2. **A git checkout of `bratishka.repo` with push access to `origin`** — the build runs
   `git fetch`, `git worktree`, `git merge` and `git push` in this checkout. A failure stops the
   run naming the branch or the remote.
3. **The configuration parses** — the engine's exit `2` is that check; print `error.message` and
   stop.

## 1. Plan

```bash
python3 ${ARTEL_ROOT}/scripts/bratishka.py testing-pr-plan --json
```

`data.tasks` are the selected source PRs; `data.title`, `data.branch`, `data.base_ref`,
`data.base_sha` and `data.repo` are the plan.

- `data.tasks` empty → say that no source PR is ready (code-reviewed and not QA-approved) and
  stop.
- Show a short table: ticket, PR number and head branch per source PR, then the planned title,
  branch, base ref and base sha.
- Refuse when today's testing PR or branch already exists: read
  `python3 ${ARTEL_ROOT}/scripts/bratishka.py status --json --offline` for a `testing_prs` entry
  dated today, and `git ls-remote --heads origin <branch>` for the planned branch. Say which one
  exists and stop; one testing PR per day.
- `--dry-run` → print the plan and the calls this run would take, then stop: nothing is built.
- Unless `--yes`, ask with the `question` tool — **Build today's testing PR?** — options
  **Build it** / **Cancel**. On **Cancel**, or with no answer, stop: nothing was written.
  `--yes` is the person's explicit consent; never add it on your own.

Keep the confirmed set — every `data.tasks[].number` — frozen for step 2; the approval means that
set and no other.

## 2. Build, push and open the PR

Pass the confirmed set as `--include`, so a PR the person did not approve cannot join:

```bash
python3 ${ARTEL_ROOT}/scripts/bratishka.py testing-pr-local --yes --json \
  --include <confirmed-n>,<confirmed-n>
```

The engine merges each head with a merge commit, runs `verify.commands` in the worktree, pushes the
branch and opens the PR with `data.title` and `data.branch` exactly as planned. Read the answer:

- `error.kind: changed_plan` — nothing was built. Say which confirmed PRs left or joined
  (`error.left` / `error.joined`) and ask again with the new `error.selected` set; continue only
  with an explicitly re-confirmed set.
- `error.kind: conflict` — nothing was pushed and the worktree is kept for inspection
  (`error.worktree`). Name the conflicting PRs and files and stop; never resolve a conflict by
  hand.
- `error.kind: verification_failed` — nothing was pushed. Name the failing command and its output
  and stop; a red local run means a red PR.
- Success → `data.pr_url`, `data.title`, `data.branch`, `data.merge_notes`, `data.verification`.

This is the only place the PR is created. Never open a second one, never rebuild the branch, and
never assemble it by hand in the working checkout.

## 3. Verify the PR before reporting it

```bash
python3 ${ARTEL_ROOT}/scripts/bratishka.py testing-pr-verify --json
```

`data.pr` is today's testing PR, matched by the planned title or branch; `data.checks` are its own
check runs. Compare it with the plan, field by field:

- `data.pr.title` is exactly `data.title` (`Testing dd-mm-yy`), and `data.pr.head` exactly
  `data.branch` (`feature/testing-dd-mm-yy`).
- `data.pr.base_ref` is the plan's `data.base_ref`, and `data.pr.head_sha` is the commit this run
  pushed.
- A PR that disagrees on any field is a **naming defect**: report the field that disagreed and the
  value it carries. Never rename it, never rebuild it, never start another path.
- `data.checks` is reported as observed. `[]` means no check run is published yet — say "no check
  runs published yet", never "green" and never "no CI". A `fail` or `cancel` bucket is a failure,
  named with its check; `pending` is still running. `null` means the read failed or there is no
  PR: say the checks are unknown, and never fill the gap from the source PRs' checks (those belong
  in the body's `## CI status of source PRs`).
- `data.pr` null → the create call returned a URL the read cannot find. Report that the PR is not
  readable now, with the URL the build returned, and name the failed read; do not invent a number
  and do not build again.

Report its `number`, `title`, `url`, head branch and head SHA from this read.

## 4. Keep the metadata fixed

- The title and the branch are exactly the engine's rendered values from `testingPr.titleTemplate`
  and `testingPr.branchTemplate`. Nothing is added in front of them: no ticket key, no repository
  name, no date-format change.
- A repository whose metadata check requires a ticket-key prefix rejects a bare `Testing …` title.
  On this PR that check is **cosmetic**: the testing PR is never merged, so it blocks nothing.
  Report the rejected check and keep the title. Never rename the PR to make a check pass, and never
  rebuild it under another name.
- When a rejected check actually blocks the duty, say so and let the person and the repository's
  owners decide. It is never a licence to change the title.

## 5. Leave everything else untouched

Do not merge the testing PR. Do not push to the base branch or to a source branch. Do not edit,
close, rebase or force-update a source PR. Do not create a branch or a PR by hand.

## 6. Report

Return the URL from this run's read-back, the title, the head branch and head SHA, the bundled
source PRs, the verification results and every check observed. Say what you saw: how many checks
are published, which passed, which failed, which are still running. Zero published check runs means
"not started yet" — not "green".

Before sending the report, re-read every claim in it: each SHA, PR number and link must correspond
to a call in this run. Anything that no call backs is reported as unknown.

## Hard rules

- NEVER let a bot task create the testing PR: the engine opens it, with the planned title.
- NEVER add a ticket key, a repository name or any other prefix to the title or branch, and never
  change the date format.
- NEVER rename, rebuild or re-open the testing PR to satisfy a check: report the rejected check and
  keep the metadata.
- NEVER create a second testing PR: one per day, and today's title, branch or PR ends the run.
- NEVER merge the testing PR, push to the base branch or a source branch, or edit, close, rebase or
  force-update a source PR.
- NEVER add `--yes` on your own; it is the person's explicit consent, and `--dry-run` starts
  nothing.
- NEVER report a PR link, number or SHA that no call in this run returned.
- NEVER call a PR green while a check failed or is pending, and never call an empty check list
  green.
- NEVER bundle a PR outside the confirmed set.

## Common mistakes

| Mistake | Fix |
| --- | --- |
| Adding a ticket key so the metadata check goes green | That is the bug, not the fix: the title is fixed, the check is reported |
| Renaming the PR to pass a check | Report the rejection; the one-day PR is never merged |
| Treating the metadata check as a blocker for the duty | It is cosmetic here; say so, and let the owners decide |
| Creating today's PR again after a failed build | `status --offline` and `git ls-remote` first; one PR per day |
| Reading the title from the plan instead of the envelope | Compare `data.pr` with `data.title` / `data.branch`; a mismatch is a defect |
| Declaring "CI is running" with zero check runs | "No check runs published yet" — pending, not green |
| Filling in the PR number from the neighbouring one | Only `data.pr.number` from this read |
| Building the branch by hand in the working checkout | The engine builds in a throwaway worktree; never assemble it by hand |
| Using the stale plan after the set changed | Stop, re-ask, and continue only with the re-confirmed set |
