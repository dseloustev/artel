---
name: artel-bratishka-status
description: "Use when the person asks what the Bratishka duty queue looks like — the open bot pull requests, their review and QA state, the live testing PR, or what the bot is working on. Read-only; never merges, closes or comments."
argument-hint: "[--offline] [--json]"
---

# Bratishka duty board

## Prerequisites

One call each; nothing here runs on a guess.

1. **The configuration** — `.artel/config.json` parses and the `bratishka` block reads back. The
   engine's exit `2` is that check: print `error.message` and stop.
2. **GitHub access** — `gh auth status` passes and `vcs.adapter` is `github-cli`
   (`${ARTEL_ROOT}/docs/config.md`, adapter identity checks). The board is read through `gh`; any
   other adapter stops it with `adapter_not_github`, so name the value and stop.
3. **The dashboard** — optional. `bratishka.dashboardUrl` empty or unreachable costs the live-task
   section only, and the board still renders.

Run:

```bash
python3 ${ARTEL_ROOT}/scripts/bratishka.py status --json [--offline]
```

The engine reads `.artel/config.json` (`bratishka`) and the repo, then prints
`{ok, verb, elapsed_ms, data}`. Exit `2` means a configuration/environment error: print the
`error.message` and stop. Exit `0` means `data` has `testing_prs`, `queue`, `live_tasks` and
`next_actions`.

Render, in this order, from `data`:

1. **Testing PR** — each open testing PR, its date, age in days, QA-approval count
   (`qa_approvals`) and how many source PRs are already merged or closed (`source_merged`).
2. **Queue** — the five buckets (`qa-approved`, `needs-qa`, `needs-review`, `bot-working`,
   `blocked`) as short tables: number, ticket, title, review state, QA state, CI/merge state
   (`mergeable`, `merge_state`) and flags. An empty bucket is one line "none", not a table.
3. **Live bot tasks** — initiator and a one-line description; say "dashboard offline" when
   `--offline` or the engine reported no dashboard.
4. **Next actions** — the engine's `next_actions`, each naming a skill.

This skill **writes nothing** and is safe at any time. It never posts, merges or closes.
The approval rules, buckets and flags are in `${ARTEL_ROOT}/docs/bratishka-duty.md`.

## Hard rules

- NEVER write, post, merge, close or label anything: the board is read-only.
- NEVER render a bucket, a count, a flag or a task the engine did not return: `data` is the whole
  answer, and a field it omits is unknown.
- NEVER call a PR approved from `review_decision` alone, or count a bot review as a human one —
  the engine's buckets are the verdict.
- NEVER turn `unknown` QA into approval, or an empty bucket into "nothing to do".
- NEVER present a live task as current when `--offline` was passed or the dashboard is
  unreachable: say the section is unavailable.
- NEVER report a red check as resolved, or a pending one as green.

## Common mistakes

| Mistake | Fix |
| --- | --- |
| Filling a gap with `gh pr list` | Report what the engine returned; the missing field stays unknown |
| Drawing a table for an empty bucket | One line, "none" |
| Treating `--offline` as a failure | It is a read without the dashboard; the rest of the board renders |
| Reading the live-task section from memory | Say "dashboard offline" when the engine reported none |
| Rendering `next_actions` as prose | Each action names the skill to run |
| Calling a source PR merged because the testing PR looks finished | `source_merged` counts them; the testing PR is never merged |
