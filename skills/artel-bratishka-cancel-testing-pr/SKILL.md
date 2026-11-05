---
name: artel-bratishka-cancel-testing-pr
description: "Use when the Bratishka duty reviewer wants yesterday's testing pull requests closed before building today's — finds the open testing PRs dated before today, shows them, asks once, then closes them with a close reason. Never touches today's testing PR, a non-testing PR or a merged/closed one."
argument-hint: "[--delete-branch] [--yes]"
---

# Cancel the stale testing PR

This is a write. Check `.artel/config.json`: `vcs.adapter` must be `github-cli`,
otherwise stop and name the value. The contract is
`${ARTEL_ROOT}/docs/bratishka-duty.md` (`## Testing PR`, `## Safety and degradation`).

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
- **Exit 0** — the run closed the candidates; report them.

An invocation carrying `--yes` is the person's explicit consent: skip the question and
report the result. Never add `--yes` on your own.

## Confirmation

Show `error.candidates` as a short list or table: number, title, branch, date and age in
days. Say whether the branches will be deleted (`error.delete_branch`; `--delete-branch`
or the configured `testingPr.deleteBranchOnCancel`).

Ask with the `question` tool: **Close these testing PRs?** — options **Close them** /
**Cancel**. On **Cancel**, or with no answer, stop: nothing was closed.

On **Close them**, re-run with `--yes` and the same `--delete-branch` choice, if any:

```bash
python3 ${ARTEL_ROOT}/scripts/bratishka.py testing-pr-cancel --yes --json [--delete-branch]
```

Pass `--delete-branch` only when the person asked for the branches to go; never on your
own. Report `data.closed` (number, title, branch, date) and `data.skip_today`. An exit `1`
with `error.kind: partial_failure` carries the closed and failed PRs in `error.closed` /
`error.failed`: report both and stop.

Never close a PR the engine did not list, and never close one by hand. Today's testing PR,
a merged or closed PR and a non-testing PR are never candidates — the engine enforces all
three, and this skill adds no selection of its own.
