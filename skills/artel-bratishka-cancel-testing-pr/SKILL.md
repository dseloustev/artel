---
name: artel-bratishka-cancel-testing-pr
description: "Use when the Bratishka duty reviewer wants yesterday's testing pull requests closed before building today's — finds the open testing PRs dated before today, shows them, asks once, then closes them with a close reason. Never touches today's testing PR, a non-testing PR or a merged/closed one."
argument-hint: "[--delete-branch] [--yes]"
---

# Cancel the stale testing PR

This is a write. Check `.artel/config.json`: `vcs.adapter` must be `github-cli`,
otherwise stop and name the value. The contract is
`${ARTEL_ROOT}/docs/bratishka-duty.md` (`## Testing PR`, `## Safety and degradation`).

## Prerequisites

One call each; nothing is closed on a guess.

1. **`vcs.adapter` is `github-cli`** — the close goes through `gh`; the engine answers
   `adapter_not_github` otherwise (`${ARTEL_ROOT}/docs/config.md`, adapter identity checks). Stop
   and name the value.
2. **`gh auth status` passes** — otherwise nothing is read and nothing is closed: name the failing
   account and stop.
3. **The plan runs before any question** — `nothing_to_do`, `confirmation_required` and
   `stale_plan` are answers, not failures, and each has its own response below.

Plan first:

```bash
python3 ${ARTEL_ROOT}/scripts/bratishka.py testing-pr-cancel --json [--delete-branch]
```

The engine reads `bratishka` from `.artel/config.json`, lists every open PR, keeps the
testing PRs and treats a date before today as stale. Exit `2` is a
configuration/environment error: print `error.message` and stop.

- **Exit 1 with `error.kind: nothing_to_do`** — no testing PR is older than today. Say so
  (`error.skip_today` names how many testing PRs were left alone, today's or later-dated)
  and stop.
- **Exit 1 with `error.kind: confirmation_required`** — the candidates are in
  `error.candidates`; ask before closing anything.
- **Exit 1 with `error.kind: stale_plan`** — a number passed with `--pr` is no longer a stale
  testing PR; nothing was closed. Re-run the plan and ask again with the new list.
- **Exit 0** — the run closed the confirmed candidates; report them.

An invocation carrying `--yes` is the person's explicit consent: skip the question and report the
result. Never add `--yes` on your own, and always pass the confirmed numbers explicitly.

## Confirmation

Show `error.candidates` as a short list or table: number, title, branch, date and age in
days. Say whether the branches will be deleted (`error.delete_branch`; `--delete-branch`
or the configured `testingPr.deleteBranchOnCancel`).

Ask with the `question` tool: **Close these testing PRs?** — options **Close them** /
**Cancel**. On **Cancel**, or with no answer, stop: nothing was closed.

On **Close them**, re-run with `--yes` and the exact numbers you showed, plus the same
`--delete-branch` choice, if any:

```bash
python3 ${ARTEL_ROOT}/scripts/bratishka.py testing-pr-cancel --yes --json \
  --pr <shown-n>,<shown-n> [--delete-branch]
```

`--pr` freezes those numbers: the engine closes only them, so a testing PR that became stale while
the person considered the question is not closed unseen. If the answer is exit `1` with
`error.kind: stale_plan`, one of the shown PRs is no longer a candidate; nothing was closed —
re-run the plan and ask again with the new list.

Pass `--delete-branch` only when the person asked for the branches to go; never on your
own. Report `data.closed` (number, title, branch, date) and `data.skip_today`. An exit `1`
with `error.kind: partial_failure` carries the closed and failed PRs in `error.closed` /
`error.failed`: report both and stop.

Never close a PR the engine did not list, and never close one by hand. Today's testing PR,
a merged or closed PR and a non-testing PR are never candidates — the engine enforces all
three, and this skill adds no selection of its own.

## Hard rules

- NEVER close a PR the plan did not list, and never close one by hand.
- NEVER touch today's testing PR, a merged or closed PR, or a non-testing PR.
- NEVER add `--yes` on your own: it is the person's explicit consent, and only after they saw the
  list.
- NEVER pass `--delete-branch` unless the person asked for the branches to go.
- NEVER close a set different from the one shown: `--pr` freezes it.
- NEVER resolve `stale_plan` by closing the numbers that vanished — re-plan and ask again.
- NEVER report a `partial_failure` by its closed half alone.

## Common mistakes

| Mistake | Fix |
| --- | --- |
| Closing right after the plan, without the question | Show the candidates, ask, then re-run with `--yes` and those numbers |
| Re-using the old number list after `stale_plan` | One of them is no longer a candidate; re-plan and ask again |
| Closing today's PR because it is open | Today is never stale; `skip_today` says how many were spared |
| Treating `nothing_to_do` as a failure | Say no testing PR is older than today and stop |
| Deleting the branch by default | `--delete-branch`, or the configured `testingPr.deleteBranchOnCancel` |
| Reporting only the closed PRs after `partial_failure` | Report `error.closed` and `error.failed` together |
| Closing the source PRs too | Only testing PRs are candidates; source PRs are never edited or closed |
